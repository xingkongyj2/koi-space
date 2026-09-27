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
    """规划预热和正式执行共享任务预算，跨线程的模型用量不会丢失。"""
    logger.trace(
        "task.configuration",
        planner={
            "model": settings.planner.model,
            "base_url": settings.planner.base_url,
            "configured": bool(settings.planner.api_key),
        },
        decision={
            "model": settings.decision.model,
            "base_url": settings.decision.base_url,
            "configured": bool(settings.decision.api_key),
        },
        max_steps=settings.max_steps,
        max_failures=settings.max_failures,
        max_seconds=settings.max_seconds,
    )
    planner_client = (
        OpenAICompatible(settings.planner) if settings.planner.api_key else None
    )

    planner = Planner(ai=planner_client)
    restored = execution_context(history)
    previous = restored.get("active_plan", {})

    if (
        planner_client
        and previous.get("status") == "ready"
        and previous.get("needs_browser")
    ):
        # 进程重启后重新绑定原标签页；用户回复参数不应触发返回首页。
        try:
            cdp_port, target_id = browser_target(task)
            browser.log_environment()
            session = browser.BrowserSession(session_id, cdp_port, target_id)
            with logger.measure("planning.resume.browser_bind"):
                session.bind()
            current_url = session.current_url()
            restored.update({"current_url": current_url, "resume": True})
            if current_url.startswith(("https://", "http://")):
                restored.update(
                    {"entry_url": current_url, "entry_locator": "completed"}
                )
            plan = planner.plan(user_input, history=history, context=restored)
        except (ValueError, PlanError, browser.BindingLost) as exc:
            protocol.error(f"flow=resume failed: {exc}")
            return 0
    elif planner_client:
        # 入口定位与完整规划独立并发；定位结果先打开页面，正式执行等待完整计划。
        with ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="koi-plan"
        ) as workers:

            def submit_with_task_context(function, *args, **kwargs):
                context = copy_context()
                return workers.submit(context.run, function, *args, **kwargs)

            def locate_entry():
                """运行轻量入口定位；后续回复必须结合原任务历史理解。"""
                with logger.measure("planning.entry.total"):
                    return Planner(ai=planner_client).locate_entry(
                        user_input, history=history
                    )

            parallel_started = time.monotonic()
            logger.trace("planner.parallel.started", phases=["entry_locator", "full_plan"])
            entry_future = submit_with_task_context(locate_entry)
            plan_future = submit_with_task_context(
                planner.plan,
                user_input,
                history=history,
                context={"entry_locator": "pending"},
            )

            try:
                entry = entry_future.result()
            except PlanError as exc:
                protocol.error(f"flow=entry_locator invalid: {exc}")
                return 0
            logger.trace(
                "planner.entry_locator.completed",
                result={"status": entry.status, "url": entry.url},
            )
            if entry.status == "ask":
                protocol.notify(entry.question, "info")
                protocol.done(entry.question, 0)
                return 0

            try:
                cdp_port, target_id = browser_target(task)
            except ValueError as exc:
                protocol.error(f"flow=browser_gate {exc}")
                return 0
            browser.log_environment()
            session = browser.BrowserSession(session_id, cdp_port, target_id)
            try:
                with logger.measure("planning.entry.browser_bind"):
                    tab = session.bind()
                logger.debug(f"flow=bind tab={tab}")
                with logger.measure("planning.entry.browser_open"):
                    if not open_entry(session, entry):
                        protocol.error(f"flow=entry_locator could not open {entry.url}")
                        return 0
                # 页面加载与规划并行；完整计划返回前不执行任何业务步骤。
                with logger.measure("planning.full.wait_after_browser_open"):
                    plan = plan_future.result()
                logger.timing("planning.full.parallel_wall", parallel_started)
            except PlanError as exc:
                protocol.error(f"flow=planner invalid: {exc}")
                return 0
            except browser.BindingLost as exc:
                protocol.error(f"flow=bind failed: {exc}")
                return 0
            # 定位结果只用于预热；步骤目标、入口及验收条件始终以完整规划为准。
    else:
        # 无模型时仍支持显式网址导航，方便本地和 PyCharm 调试。
        try:
            plan = planner.plan(user_input, history=history)
        except PlanError as exc:
            protocol.error(f"flow=planner invalid: {exc}")
            return 0
        session = None

    emit_plan(plan)

    if plan.status == "ask":
        protocol.notify(plan.question, "info")
        protocol.done(plan.question, 0)
        return 0
    if plan.status == "direct" or not plan.needs_browser:
        answer = plan.direct_answer or "已完成规划，无需打开浏览器。"
        protocol.notify(answer, "info")
        protocol.done(answer, 0)
        return 0

    if session is None:
        try:
            cdp_port, target_id = browser_target(task)
        except ValueError as exc:
            protocol.error(f"flow=browser_gate {exc}")
            return 0

        browser.log_environment()
        session = browser.BrowserSession(session_id, cdp_port, target_id)
        try:
            tab = session.bind()
        except browser.BindingLost as exc:
            protocol.error(f"flow=bind failed: {exc}")
            return 0
        logger.debug(f"flow=bind tab={tab}")

    try:
        decision = Decision(ai=planner_client)
        if settings.decision.api_key:
            decision = Decision(
                jev=JevDecision(settings.decision, text_model=planner_client),
                ai=planner_client,
            )
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

    protocol.thinking(
        json.dumps({"kind": "task_outcome", **outcome.to_dict()}, ensure_ascii=False)
    )
    if outcome.status == "completed":
        protocol.done(outcome.summary, budget.steps)
    elif outcome.status == "waiting_user":
        protocol.done(outcome.summary, budget.steps)
    else:
        protocol.error(outcome.summary)
    return 0


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
