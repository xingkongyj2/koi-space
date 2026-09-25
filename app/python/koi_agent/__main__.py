"""Koi agent entrypoint.

Spawned per task by the Electron main process (see
`app/src/main/hl/engines/python/adapter.ts`). Reads one JSON envelope on stdin,
writes NDJSON HlEvents on stdout, logs to stderr.

Division of labour: the app owns the *display* — it creates the browser view,
renders it, and tells us which CDP target that view is. Everything about driving
the browser lives here. The envelope carries `browser.cdpPort` and
`browser.targetId` and nothing else browser-related; no env vars, no PATH shim,
no pre-established binding to inherit.

This is deliberately a skeleton: receive -> analyze -> one browser action ->
report. `analyze()` is where the Planner/Decision modules from
`koi/Koi方案设计.md` land; `browser.BrowserSession` is already the Executor.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass

from . import browser, protocol

FIXED_URL = "https://v.qq.com/"


@dataclass(frozen=True)
class Plan:
    """What this run intends to do. Structured so a real Planner can replace it."""

    url: str
    rationale: str


def analyze(prompt: str) -> Plan:
    """Turn a user request into an action.

    Deliberately hardcoded: every task opens the same site, so the whole chain
    (UI -> app -> this agent -> agent-browser -> browser view) can be verified
    without any model in the loop. `prompt` is unused for now and is the seam the
    Planner and Decision modules take over.
    """
    return Plan(url=FIXED_URL, rationale="fixed target — no analysis yet")


def read_task() -> dict:
    raw = sys.stdin.read()
    if not raw.strip():
        raise ValueError("no task on stdin")
    envelope = json.loads(raw)
    if not isinstance(envelope, dict):
        raise ValueError("task envelope must be a JSON object")
    return envelope


def browser_target(task: dict) -> tuple[int, str]:
    """Pull (cdpPort, targetId) out of the envelope with actionable errors."""
    spec = task.get("browser")
    if not isinstance(spec, dict):
        raise ValueError("envelope is missing the `browser` object")
    port = spec.get("cdpPort")
    target_id = spec.get("targetId")
    if not isinstance(port, int) or not isinstance(target_id, str) or not target_id:
        raise ValueError(f"`browser` must carry an int cdpPort and a non-empty targetId, got {spec!r}")
    return port, target_id


def main() -> int:
    try:
        task = read_task()
        cdp_port, target_id = browser_target(task)
    except (ValueError, json.JSONDecodeError) as exc:
        protocol.error(f"could not read the task envelope: {exc}")
        return 0

    prompt = str(task.get("prompt") or "")
    session_id = str(task.get("sessionId") or "?")
    browser.log_environment()
    protocol.log(f"task received session={session_id} target={target_id[:8]} port={cdp_port} prompt={prompt[:120]!r}")
    protocol.thinking(f"Received task for session {session_id}: {prompt[:200]}")

    session = browser.BrowserSession(session_id, cdp_port, target_id)
    try:
        tab_id = session.bind()
    except browser.BindingLost as exc:
        protocol.error(str(exc))
        return 0
    protocol.log(f"bound to agent-browser tab {tab_id}")

    plan = analyze(prompt)
    protocol.thinking(f"Plan: open {plan.url} ({plan.rationale})")

    iteration = 1
    protocol.tool_call("agent-browser", {"command": ["open", plan.url]}, iteration)
    try:
        result = session.open_url(plan.url)
    except browser.BindingLost as exc:
        protocol.error(f"Lost the browser binding while navigating: {exc}")
        return 0
    protocol.tool_result("agent-browser", result.ok, result.preview, result.ms)

    if not result.ok:
        protocol.error(f"`agent-browser open {plan.url}` failed (exit {result.exit_code}): {result.preview}")
        return 0

    # Read the URL back through the same binding. Proves the command landed on
    # THIS session's view rather than some other page the daemon defaulted to.
    try:
        observed = session.current_url()
    except browser.BindingLost as exc:
        protocol.error(f"Lost the browser binding while verifying: {exc}")
        return 0

    if observed and not observed.startswith(plan.url.split("#")[0].rstrip("/")):
        protocol.thinking(f"View reports {observed} after navigating to {plan.url}")

    protocol.log(f"navigated; view now at {observed or '<unknown>'}")
    protocol.done(f"Opened {observed or plan.url} in the session browser view.", iteration)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - last resort so the run never hangs
        protocol.log(f"unhandled exception: {exc!r}")
        protocol.error(f"agent crashed: {exc}")
        sys.exit(0)
