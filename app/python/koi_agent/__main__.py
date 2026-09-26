"""Process entry point for one Koi task."""
from __future__ import annotations

import json
import sys
import time
from contextvars import copy_context
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

from . import browser, protocol
from .budget import Budget
from .config import load_settings
from .completion import CompletionVerifier
from .decision import Decision
from .executor import Action, Executor
from .history import execution_context, task_history
from .models import JevDecision, ModelError, OpenAICompatible
from .memory import Memory
from .observer import Observer
from .orchestrator import Orchestrator
from .planner import EntryPoint, Plan, PlanError, Planner
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
    # Persist the validated contract, not just a step count: a later planning
    # turn needs the prior goals, URLs, dependencies and clarification question.
    protocol.thinking(json.dumps({"kind": "planner_plan", "plan": plan.to_dict()}, ensure_ascii=False))


def open_entry(session: browser.BrowserSession, entry: EntryPoint) -> bool:
    """Emit the fast browser.open event and start loading the located page."""
    result = Executor(session).execute(Action("open", entry.url, expected="entry point"))
    if result.ok:
        return True
    # Navigation commands may time out after the browser has already changed
    # the page.  Read the URL once before deciding that the fast phase failed.
    try:
        current = session.current_url()
    except Exception:
        current = ""
    wanted = urlsplit(entry.url)
    actual = urlsplit(current)
    reached = bool(current and actual.scheme == wanted.scheme and actual.netloc == wanted.netloc
                   and (not wanted.path or actual.path.startswith(wanted.path)))
    protocol.trace("planner.entry_open.result", ok=result.ok, current_url=current, reached=reached)
    return reached


@protocol.timed("task.total")
@protocol.traced("task.run")
def main() -> int:
    try:
        task = read_task()
    except (ValueError, json.JSONDecodeError) as exc:
        protocol.error(f"could not read the task envelope: {exc}")
        return 0

    user_input = str(task.get("userInput", task.get("prompt")) or "")
    session_id = str(task.get("sessionId") or "?")
    protocol.set_context(session_id=session_id)
    protocol.trace("task.received", task=task)

    try:
        history = task_history(task, user_input)
    except ValueError as exc:
        protocol.error(f"could not read the task history: {exc}")
        return 0

    with protocol.capture_events(history):
        return run_task(task, user_input, session_id, history)


def run_task(task: dict, user_input: str, session_id: str, history: list[dict]) -> int:
    """Run one turn using the complete history shared by all task layers."""

    settings = load_settings()
    protocol.trace("task.configuration", planner={"model": settings.planner.model, "base_url": settings.planner.base_url, "configured": bool(settings.planner.api_key)}, decision={"model": settings.decision.model, "base_url": settings.decision.base_url, "configured": bool(settings.decision.api_key)}, max_steps=settings.max_steps, max_failures=settings.max_failures, max_seconds=settings.max_seconds)
    planner_client = (
        OpenAICompatible(settings.planner) if settings.planner.api_key else None
    )

    planner = Planner(ai=planner_client)
    restored = execution_context(history)
    previous = restored.get("active_plan", {})

    if planner_client and previous.get("status") == "ready" and previous.get("needs_browser"):
        # Rebind the existing task tab after the Python process restarts.
        # Never reopen the homepage merely because the user replied.
        try:
            cdp_port, target_id = browser_target(task)
            browser.log_environment()
            session = browser.BrowserSession(session_id, cdp_port, target_id)
            with protocol.measure("planning.resume.browser_bind"):
                session.bind()
            current_url = session.current_url()
            restored.update({"current_url": current_url, "resume": True})
            if current_url.startswith(("https://", "http://")):
                restored.update({"entry_url": current_url, "entry_locator": "completed"})
            plan = planner.plan(user_input, history=history, context=restored)
        except (ValueError, PlanError, browser.BindingLost) as exc:
            protocol.error(f"flow=resume failed: {exc}")
            return 0
    elif planner_client:
        # The short locator and full task planner make independent model
        # requests.  Open the page as soon as the locator returns; execution
        # waits for both the page and the full plan.
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="koi-plan") as workers:
            def submit_with_task_context(function, *args, **kwargs):
                context = copy_context()
                return workers.submit(context.run, function, *args, **kwargs)

            def locate_entry():
                with protocol.measure("planning.entry.total"):
                    return Planner(ai=planner_client).locate_entry(user_input, history=history)

            parallel_started = time.monotonic()
            protocol.trace("planner.parallel.started", phases=["entry_locator", "full_plan"])
            entry_future = submit_with_task_context(locate_entry)
            plan_future = submit_with_task_context(planner.plan, user_input, history=history,
                                                   context={"entry_locator": "pending"})

            try:
                entry = entry_future.result()
            except PlanError as exc:
                protocol.error(f"flow=entry_locator invalid: {exc}")
                return 0
            protocol.trace("planner.entry_locator.completed", result={"status": entry.status, "url": entry.url})
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
                with protocol.measure("planning.entry.browser_bind"):
                    tab = session.bind()
                protocol.log(f"flow=bind tab={tab}")
                with protocol.measure("planning.entry.browser_open"):
                    if not open_entry(session, entry):
                        protocol.error(f"flow=entry_locator could not open {entry.url}")
                        return 0
                # The URL is already loading while this future finishes.  Do
                # not execute a subtask before both gates are ready.
                with protocol.measure("planning.full.wait_after_browser_open"):
                    plan = plan_future.result()
                protocol.timing("planning.full.parallel_wall", parallel_started)
            except PlanError as exc:
                protocol.error(f"flow=planner invalid: {exc}")
                return 0
            except browser.BindingLost as exc:
                protocol.error(f"flow=bind failed: {exc}")
                return 0
            # Keep the two parallel results independent.  The locator's page
            # open is an early browser warm-up; the full plan remains
            # authoritative for step goals, start URLs and acceptance criteria.
    else:
        # Keep the deterministic no-model/PyCharm path unchanged: it can still
        # open an explicitly supplied URL through the regular plan contract.
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
        protocol.log(f"flow=bind tab={tab}")

    try:
        decision = None
        if settings.decision.api_key:
            decision = Decision(
                jev=JevDecision(settings.decision, text_model=planner_client),
                ai=planner_client,
            )
        orchestrator = Orchestrator(
            session,
            plan,
            observer=Observer(session, include_full=bool(planner_client)),
            decision=decision,
            completion=CompletionVerifier(ai=planner_client),
            budget=Budget(settings.max_steps, settings.max_failures, settings.max_seconds),
            memory=Memory(settings.memory_path),
            skills=SkillLibrary(settings.skills_path),
        )
        with protocol.measure("runtime.orchestrator.total"):
            summary = orchestrator.run()
    except browser.BindingLost as exc:
        protocol.error(f"flow=runtime binding lost: {exc}")
        return 0

    protocol.done(summary, len(plan.steps))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ModelError as exc:
        protocol.error(f"模型服务请求失败：{exc}")
        sys.exit(0)
    except Exception as exc:  # Last resort: never leave the task hanging.
        protocol.log(f"unhandled exception: {exc!r}")
        protocol.error(f"agent crashed: {exc}")
        sys.exit(0)
