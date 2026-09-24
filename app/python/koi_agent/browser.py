"""The only module that talks to `agent-browser`.

Everything upstream of this stays ignorant of CLI details — that is the seam the
design doc calls the Executor. Swap the driver and nothing else changes.

Two rules this module exists to enforce:

1. Never pass `--session`, `--cdp`, or `--auto-connect`. The Electron main
   process binds this session to its own browser view before spawning us, and
   re-binding from here would point at another session's page. The environment
   already carries AGENT_BROWSER_SESSION / _CDP / _SOCKET_DIR, and `agent-browser`
   on PATH is the app's shim, which re-acquires the binding if its daemon died.
2. A lost binding is fatal, not retryable. Guessing drives the wrong page.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass

BINDING_LOST_MARKER = "lost its browser binding"
DEFAULT_TIMEOUT = 60.0


class BindingLost(RuntimeError):
    """The session is no longer attached to its browser view. Do not retry."""


@dataclass(frozen=True)
class BrowserResult:
    ok: bool
    args: tuple[str, ...]
    stdout: str
    stderr: str
    exit_code: int
    ms: float

    @property
    def preview(self) -> str:
        """Short single-string rendering for the chat UI's tool_result event."""
        body = (self.stdout or self.stderr).strip()
        if len(body) > 800:
            body = body[:800] + "…"
        return body or ("ok" if self.ok else f"exit {self.exit_code}")


def resolve_cli() -> str:
    """Absolute path to the agent-browser the app put on our PATH.

    Resolved once and cached by the caller; raising here means the harness env
    was not applied, which is a wiring bug rather than a runtime condition.
    """
    found = shutil.which("agent-browser")
    if not found:
        raise BindingLost(
            "agent-browser is not on PATH. The app injects a shim directory into the "
            "agent environment; if it is missing, this process was not spawned by the app."
        )
    return found


def run(args: list[str], timeout: float = DEFAULT_TIMEOUT) -> BrowserResult:
    """Run one agent-browser command and return its result.

    `args` is the argv tail, e.g. ["open", "https://example.com"].
    """
    if not args:
        raise ValueError("agent-browser requires a subcommand")
    cli = resolve_cli()
    started = time.monotonic()
    try:
        proc = subprocess.run(
            [cli, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            # Inherit env deliberately: AGENT_BROWSER_* and the shim PATH prefix
            # are what keep this session on its own browser view.
            env=None,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed = (time.monotonic() - started) * 1000
        return BrowserResult(False, tuple(args), "", f"timed out after {timeout}s: {exc}", 124, elapsed)

    elapsed = (time.monotonic() - started) * 1000
    result = BrowserResult(
        ok=proc.returncode == 0,
        args=tuple(args),
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
        exit_code=proc.returncode,
        ms=elapsed,
    )
    if not result.ok and BINDING_LOST_MARKER in (result.stderr + result.stdout):
        raise BindingLost(result.stderr.strip() or result.stdout.strip())
    return result


def open_url(url: str, timeout: float = DEFAULT_TIMEOUT) -> BrowserResult:
    return run(["open", url], timeout=timeout)


def current_url(timeout: float = 15.0) -> str:
    """Read back the bound view's URL — the cheapest proof the chain is live."""
    result = run(["get", "url"], timeout=timeout)
    return result.stdout.strip() if result.ok else ""
