"""Process entry point for one Koi task."""
from __future__ import annotations

import json
import sys

from . import browser, protocol
from .budget import Budget
from .config import load_settings
from .decision import Decision
from .models import JevDecision, OpenAICompatible
from .memory import Memory
from .orchestrator import Orchestrator
from .planner import Plan, PlanError, Planner
from .skills import SkillLibrary


def read_task() -> dict:
    raw = sys.stdin.read()
    if not raw.strip():
        raise ValueError("no task on stdin")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("task envelope must be a JSON object")
    return value


def browser_target(task: dict) -> tuple[int, str]:
    spec = task.get("browser")
    if not isinstance(spec, dict):
        raise ValueError("envelope is missing the `browser` object")
    port, target = spec.get("cdpPort"), spec.get("targetId")
    if not isinstance(port, int) or not isinstance(target, str) or not target:
        raise ValueError("browser must carry cdpPort and targetId")
    return port, target


def emit_plan(plan: Plan) -> None:
    protocol.log(
        f"planner status={plan.status} needs_browser={plan.needs_browser} "
        f"steps={len(plan.steps)}"
    )
    protocol.thinking(f"Planner: {plan.status}; {len(plan.steps)} step(s)")


@protocol.traced("task.run")
def main() -> int:
    try:
        task = read_task()
    except (ValueError, json.JSONDecodeError) as exc:
        protocol.error(f"could not read the task envelope: {exc}")
        return 0

    prompt = str(task.get("prompt") or "")
    session_id = str(task.get("sessionId") or "?")
    protocol.set_context(session_id=session_id)
    protocol.trace("task.received", task=task)

    settings = load_settings()
    protocol.trace("task.configuration", planner={"model": settings.planner.model, "base_url": settings.planner.base_url, "configured": bool(settings.planner.api_key)}, decision={"model": settings.decision.model, "base_url": settings.decision.base_url, "configured": bool(settings.decision.api_key)}, max_steps=settings.max_steps, max_failures=settings.max_failures, max_seconds=settings.max_seconds)
    planner_client = (
        OpenAICompatible(settings.planner) if settings.planner.api_key else None
    )
    planner_model = (
        (lambda system, user: planner_client.chat(system, user))
        if planner_client
        else None
    )

    try:
        plan = Planner(planner_model).plan(prompt)
    except PlanError as exc:
        protocol.error(f"flow=planner invalid: {exc}")
        return 0
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
    protocol.log(f"flow=bind tab={tab}")

    if not plan.steps[0].start_url:
        protocol.notify("规划已生成，当前步骤需要继续观察页面后执行。", "info")
        protocol.done(plan.steps[0].goal, 0)
        return 0

    try:
        decision = None
        if settings.decision.api_key:
            decision = Decision(
                jev=JevDecision(settings.decision, text_model=planner_client),
                model=(
                    lambda payload: planner_client.chat(
                        '根据浏览器快照选择最多一个安全动作，只返回 JSON：{"actions":[{"kind":"click|fill|press|wait|scroll|open","ref":"@e1","value":""}],"confidence":0.8}。只使用快照中存在的 ref；不要猜测密码。',
                        payload,
                    )
                ) if planner_client else None,
            )
        orchestrator = Orchestrator(
            session,
            plan,
            decision=decision,
            budget=Budget(settings.max_steps, settings.max_failures, settings.max_seconds),
            memory=Memory(settings.memory_path),
            skills=SkillLibrary(settings.skills_path),
        )
        summary = orchestrator.run()
    except browser.BindingLost as exc:
        protocol.error(f"flow=runtime binding lost: {exc}")
        return 0

    protocol.done(summary, len(plan.steps))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # Last resort: never leave the task hanging.
        protocol.log(f"unhandled exception: {exc!r}")
        protocol.error(f"agent crashed: {exc}")
        sys.exit(0)
