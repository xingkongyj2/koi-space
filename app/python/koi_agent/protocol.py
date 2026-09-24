"""NDJSON event protocol between the Python agent and the Electron main process.

The main process reads this process's stdout line by line and turns each line
into an `HlEvent` (see `app/src/shared/session-schemas.ts`), which is persisted
to SQLite and streamed to the renderer. One JSON object per line, no other
stdout output — anything else on stdout is dropped by the parser.

Diagnostic logging goes to stderr, which the main process captures separately
and only surfaces on failure.
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any

_STARTED_AT = time.monotonic()


def _emit(event: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def thinking(text: str) -> None:
    _emit({"type": "thinking", "text": text})


def tool_call(name: str, args: Any, iteration: int) -> None:
    _emit({"type": "tool_call", "name": name, "args": args, "iteration": iteration})


def tool_result(name: str, ok: bool, preview: str, ms: float) -> None:
    _emit({"type": "tool_result", "name": name, "ok": ok, "preview": preview, "ms": round(ms, 1)})


def notify(message: str, level: str = "info") -> None:
    _emit({"type": "notify", "message": message, "level": level})


def error(message: str) -> None:
    _emit({"type": "error", "message": message})


def done(summary: str, iterations: int) -> None:
    _emit({"type": "done", "summary": summary, "iterations": iterations})


def log(message: str) -> None:
    """Diagnostics. Never stdout — that channel is the event protocol."""
    print(f"[koi-agent +{time.monotonic() - _STARTED_AT:6.2f}s] {message}", file=sys.stderr, flush=True)
