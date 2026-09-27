"""单次任务的进程入口：读取请求、并发规划、绑定浏览器并输出结构化事件。"""

from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from urllib.parse import urlsplit

import protocol.protocol as protocol
from config.config import load_settings
from llm.models import JevDecision, ModelError, OpenAICompatible
from logger import logger
from memory.history import execution_context, task_history
from memory.memory import Memory
from memory.skills import SkillLibrary
from planner.planner import EntryPoint, Plan, PlanError, Planner
from react import browser
from react.budget import Budget, BudgetExceeded
from react.completion import CompletionVerifier
from react.decision import Decision
from react.executor import Action, Executor
from react.observer import Observer
from react.orchestrator import Orchestrator


def read_task() -> dict:
    """从 stdin 读取单个任务 JSON 对象。"""
    raw = sys.stdin.read()
    if not raw.strip():
        raise ValueError("no task on stdin")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("task envelope must be a JSON object")
    return value


def browser_target(task: dict) -> tuple[int, str]:
    """读取宿主显式提供的 CDP 端口和 targetId，缺失时拒绝运行。"""
    spec = task.get("browser")
    if not isinstance(spec, dict):
        raise ValueError("envelope is missing the `browser` object")
    port, target = spec.get("cdpPort"), spec.get("targetId")
    if not isinstance(port, int) or not isinstance(target, str) or not target:
        raise ValueError("browser must carry cdpPort and targetId")
    return port, target


def emit_plan(plan: Plan) -> None:
    """持久化完整规划契约，方便后续回复和进程重启后恢复。"""
    logger.debug(
        f"planner status={plan.status} needs_browser={plan.needs_browser} steps={len(plan.steps)}"
    )
    # 保存完整契约，包括目标、网址、依赖和追问，供下一轮规划恢复。
    protocol.thinking(
        json.dumps({"kind": "planner_plan", "plan": plan.to_dict()}, ensure_ascii=False)
    )


def open_entry(session: browser.BrowserSession, entry: EntryPoint) -> bool:
    """提前打开入口页面；导航超时后再根据实际 URL 判断是否已到达。"""
    result = Executor(session).execute(
        Action("open", entry.url, expected="entry point")
    )
    if result.ok:
        return True
    # 命令超时并不代表导航失败；读取实际地址后再判断。
    try:
        current = session.current_url()
    except Exception:
        current = ""
    wanted = urlsplit(entry.url)
    actual = urlsplit(current)
    reached = bool(
        current
        and actual.scheme == wanted.scheme
        and actual.netloc == wanted.netloc
        and (not wanted.path or actual.path.startswith(wanted.path))
    )
    logger.trace(
        "planner.entry_open.result", ok=result.ok, current_url=current, reached=reached
    )
    return reached


@logger.timed("task.total")
@logger.traced("task.run")
def main() -> int:
    """解析请求并设置事件上下文，stdout 始终只输出协议事件。"""
    try:
        task = read_task()
    except (ValueError, json.JSONDecodeError) as exc:
        protocol.error(f"could not read the task envelope: {exc}")
        return 0

    user_input = str(task.get("userInput", task.get("prompt")) or "")
    session_id = str(task.get("sessionId") or "?")
    logger.set_context(session_id=session_id)
    logger.trace("task.received", task=task)

    try:
        history = task_history(task, user_input)
    except ValueError as exc:
        protocol.error(f"could not read the task history: {exc}")
        return 0

    with protocol.capture_events(history):
        return run_task(task, user_input, session_id, history)


def run_task(task: dict, user_input: str, session_id: str, history: list[dict]) -> int:
    """使用完整历史运行本轮任务，让规划与执行共享同一累计预算。"""

    settings = load_settings()
    budget = Budget(
        settings.max_steps,
        settings.max_failures,
        settings.max_seconds,
        settings.max_model_calls,
        settings.max_tokens,
        settings.max_step_actions,
        settings.max_no_ops,
        settings.max_slow_calls,
        settings.max_replans,
    )
    try:
        with budget.track_models():
            return _run_task(task, user_input, session_id, history, settings, budget)
    except BudgetExceeded as exc:
        protocol.error(str(exc))
        return 0


def _run_task(task, user_input, session_id, history, settings, budget):
    """串接规划、浏览器准备和执行；模型预算由外层 run_task 统一管理。"""
    planner_client = (
        OpenAICompatible(settings.planner) if settings.planner.api_key else None
    )
    planner = Planner(ai=planner_client)
    plan, session = _prepare_plan(
        task, user_input, session_id, history, planner, planner_client
    )
    if plan is None:  # 子流程已输出明确错误或追问。
        return 0

    emit_plan(plan)
    if _finish_non_browser_plan(plan):
        return 0
    if session is None:
        session = _bind_session(task, session_id)
        if session is None:
            return 0

    return _execute_plan(
        session, plan, user_input, settings, budget, planner, planner_client
    )


def _prepare_plan(task, user_input, session_id, history, planner, planner_client):
    """选择历史恢复、入口预热或无模型规划，并返回计划与会话。"""
    restored = execution_context(history)
    previous = restored.get("active_plan", {})
    if planner_client and previous.get("status") == "ready" and previous.get("needs_browser"):
        return _resume_plan(task, user_input, session_id, history, planner, restored)
    if planner_client:
        return _plan_with_entry(task, user_input, session_id, history, planner, planner_client)
    # 无模型时只支持显式网址导航，方便本地和 PyCharm 调试。
    try:
        return planner.plan(user_input, history=history), None
    except PlanError as exc:
        protocol.error(f"flow=planner invalid: {exc}")
        return None, None


def _resume_plan(task, user_input, session_id, history, planner, restored):
    """续接历史任务，绑定原标签页并把当前 URL 交给 Planner。"""
    try:
        session = _new_session(task, session_id)
        with logger.measure("planning.resume.browser_bind"):
            session.bind()
        current_url = session.current_url()
        restored.update({"current_url": current_url, "resume": True})
        if current_url.startswith(("https://", "http://")):
            restored.update({"entry_url": current_url, "entry_locator": "completed"})
        return planner.plan(user_input, history=history, context=restored), session
    except (ValueError, PlanError, browser.BindingLost) as exc:
        protocol.error(f"flow=resume failed: {exc}")
        return None, None


def _submit_with_context(workers, function, *args, **kwargs):
    """把当前任务的日志与模型预算上下文复制到规划线程。"""
    context = copy_context()
    return workers.submit(context.run, function, *args, **kwargs)


def _locate_entry(planner_client, user_input, history):
    """独立 Planner 查找入口；它与完整规划同时运行。"""
    with logger.measure("planning.entry.total"):
        return Planner(ai=planner_client).locate_entry(user_input, history=history)


def _plan_with_entry(task, user_input, session_id, history, planner, planner_client):
    """并发定位入口和完整规划；先打开入口，再等待规划结果。"""
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="koi-plan") as workers:
        started = time.monotonic()
        entry_future = _submit_with_context(
            workers, _locate_entry, planner_client, user_input, history
        )
        plan_future = _submit_with_context(
            workers, planner.plan, user_input,
            history=history, context={"entry_locator": "pending"},
        )
        entry = _await_entry(entry_future)
        if entry is None:
            return None, None
        session = _open_planned_entry(task, session_id, entry)
        if session is None:
            return None, None
        return _await_plan(plan_future, started, session)


def _await_entry(entry_future):
    """等待入口定位结果，统一处理追问和入口契约错误。"""
    try:
        entry = entry_future.result()
    except PlanError as exc:
        protocol.error(f"flow=entry_locator invalid: {exc}")
        return None
    if entry.status == "ask":
        protocol.notify(entry.question, "info")
        protocol.done(entry.question, 0)
        return None
    return entry


def _await_plan(plan_future, started, session):
    """入口打开后等待完整规划，记录并发耗时并返回会话。"""
    try:
        # 页面加载与规划并行；完整计划返回前不执行任何业务步骤。
        with logger.measure("planning.full.wait_after_browser_open"):
            plan = plan_future.result()
        logger.timing("planning.full.parallel_wall", started)
        return plan, session
    except PlanError as exc:
        protocol.error(f"flow=planner invalid: {exc}")
        return None, None


def _new_session(task, session_id):
    """按宿主指定的 CDP target 创建会话，不猜测其他标签页。"""
    cdp_port, target_id = browser_target(task)
    browser.log_environment()
    return browser.BrowserSession(session_id, cdp_port, target_id)


def _open_planned_entry(task, session_id, entry):
    """绑定指定标签页并预热入口；失败时输出对应协议错误。"""
    try:
        session = _new_session(task, session_id)
    except ValueError as exc:
        protocol.error(f"flow=browser_gate {exc}")
        return None
    try:
        with logger.measure("planning.entry.browser_bind"):
            session.bind()
        with logger.measure("planning.entry.browser_open"):
            if not open_entry(session, entry):
                protocol.error(f"flow=entry_locator could not open {entry.url}")
                return None
        return session
    except browser.BindingLost as exc:
        protocol.error(f"flow=bind failed: {exc}")
        return None


def _finish_non_browser_plan(plan):
    """直接答复或追问无需启动执行器；返回是否已经结束本轮。"""

    if plan.status == "ask":
        protocol.notify(plan.question, "info")
        protocol.done(plan.question, 0)
        return True
    if plan.status == "direct" or not plan.needs_browser:
        answer = plan.direct_answer or "已完成规划，无需打开浏览器。"
        protocol.notify(answer, "info")
        protocol.done(answer, 0)
        return True
    return False


def _bind_session(task, session_id):
    """无预热会话时，按完整计划要求绑定宿主标签页。"""
    try:
        session = _new_session(task, session_id)
    except ValueError as exc:
        protocol.error(f"flow=browser_gate {exc}")
        return None
    try:
        session.bind()
        return session
    except browser.BindingLost as exc:
        protocol.error(f"flow=bind failed: {exc}")
        return None


def _execute_plan(session, plan, user_input, settings, budget, planner, planner_client):
    """构造执行组件、运行计划，并统一输出任务结果事件。"""
    try:
        decision = _build_decision(settings, planner_client)
        orchestrator = Orchestrator(
            session,
            plan,
            observer=Observer(session),
            decision=decision,
            completion=CompletionVerifier(ai=planner_client),
            budget=budget,
            planner=planner if planner_client else None,
            user_goal=user_input,
            memory=Memory(settings.memory_path),
            skills=SkillLibrary(settings.skills_path),
        )
        with logger.measure("runtime.orchestrator.total"):
            outcome = orchestrator.run()
    except browser.BindingLost as exc:
        protocol.error(f"flow=runtime binding lost: {exc}")
        return 0

    _emit_outcome(outcome, budget.steps)
    return 0


def _build_decision(settings, planner_client):
    """有 JEV 配置时启用快路径，文本 Planner 作为慢路径。"""
    if settings.decision.api_key:
        return Decision(
            jev=JevDecision(settings.decision, text_model=planner_client),
            ai=planner_client,
        )
    return Decision(ai=planner_client)


def _emit_outcome(outcome, step_count):
    """持久化完整结果，并向宿主发送唯一完成或错误事件。"""
    protocol.thinking(
        json.dumps({"kind": "task_outcome", **outcome.to_dict()}, ensure_ascii=False)
    )
    if outcome.status in {"completed", "waiting_user"}:
        protocol.done(outcome.summary, step_count)
    else:
        protocol.error(outcome.summary)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ModelError as exc:
        protocol.error(f"模型服务请求失败：{exc}")
        sys.exit(0)
    except Exception as exc:  # Last resort: never leave the task hanging.
        logger.debug(f"unhandled exception: {exc!r}")
        protocol.error(f"agent crashed: {exc}")
        sys.exit(0)
