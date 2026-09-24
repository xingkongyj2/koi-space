"""Koi agent entrypoint.

Spawned per task by the Electron main process (see
`app/src/main/hl/engines/python/adapter.ts`). Reads one JSON envelope on stdin,
writes NDJSON HlEvents on stdout, logs to stderr.

This is deliberately a skeleton: receive -> analyze -> one browser action ->
report. The design in `koi/Koi方案设计.md` grows into this shape —
`analyze()` is where the Planner/Decision modules land, `browser.py` is already
the Executor seam. Replace the body of `analyze()`; leave the protocol alone.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass

from . import browser, protocol

FALLBACK_URL = "https://example.com"

_URL_RE = re.compile(r"(?:https?://[^\s'\"<>]+|(?:[\w-]+\.)+[a-z]{2,}(?:/[^\s'\"<>]*)?)", re.IGNORECASE)


@dataclass(frozen=True)
class Plan:
    """What this run intends to do. Structured so a real Planner can replace it."""

    url: str
    rationale: str


def analyze(prompt: str) -> Plan:
    """Turn a user request into an action.

    Placeholder intelligence: pull the first URL out of the prompt. When there is
    none, fall back to a fixed target so the end-to-end chain is still exercised
    and visible in the browser view.
    """
    match = _URL_RE.search(prompt or "")
    if match:
        url = match.group(0)
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"
        return Plan(url=url, rationale="found a URL in the request")
    return Plan(url=FALLBACK_URL, rationale=f"no URL in the request; using the fixed target {FALLBACK_URL}")


def read_task() -> dict:
    raw = sys.stdin.read()
    if not raw.strip():
        raise ValueError("no task on stdin")
    envelope = json.loads(raw)
    if not isinstance(envelope, dict):
        raise ValueError("task envelope must be a JSON object")
    return envelope


def main() -> int:
    try:
        task = read_task()
    except (ValueError, json.JSONDecodeError) as exc:
        protocol.error(f"could not read the task envelope: {exc}")
        return 0

    prompt = str(task.get("prompt") or "")
    session_id = str(task.get("sessionId") or "?")
    protocol.log(f"task received session={session_id} prompt={prompt[:120]!r}")
    protocol.thinking(f"Received task for session {session_id}: {prompt[:200]}")

    try:
        browser.resolve_cli()
    except browser.BindingLost as exc:
        protocol.error(str(exc))
        return 0

    plan = analyze(prompt)
    protocol.thinking(f"Plan: open {plan.url} ({plan.rationale})")

    iteration = 1
    protocol.tool_call("agent-browser", {"command": ["open", plan.url]}, iteration)
    try:
        result = browser.open_url(plan.url)
    except browser.BindingLost as exc:
        protocol.error(f"Lost the browser binding before navigating: {exc}")
        return 0
    protocol.tool_result("agent-browser", result.ok, result.preview, result.ms)

    if not result.ok:
        protocol.error(f"`agent-browser open {plan.url}` failed (exit {result.exit_code}): {result.preview}")
        return 0

    # Read the URL back through the same binding. Proves the command landed on
    # THIS session's view rather than some other page the daemon defaulted to.
    try:
        observed = browser.current_url()
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
    except Exception as exc:  # noqa: BLE001 - last-resort so the run never hangs
        protocol.log(f"unhandled exception: {exc!r}")
        protocol.error(f"agent crashed: {exc}")
        sys.exit(0)
