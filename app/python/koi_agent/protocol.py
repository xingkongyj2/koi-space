"""NDJSON event protocol between the Python agent and the Electron main process.

The main process reads this process's stdout line by line and turns each line
into an `HlEvent` (see `app/src/shared/session-schemas.ts`), which is persisted
to SQLite and streamed to the renderer. One JSON object per line, no other
stdout output — anything else on stdout is dropped by the parser.

Diagnostics and emitted events are saved under the Python root's log directory.
stdout remains exclusively the event protocol.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_STARTED_AT = time.monotonic()
LOG_DIR = Path(__file__).resolve().parent.parent / "log"
LOG_PATH = LOG_DIR / f"agent-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}-{os.getpid()}.jsonl"


def _record(kind: str, **data: Any) -> None:
    """One file per agent process, independent of cwd and Electron userData."""
    entry = {"time": datetime.now(timezone.utc).isoformat(), "kind": kind, **data}
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        # Keep the task/event protocol alive if the installation is read-only.
        print(f"[koi-agent] cannot write {LOG_PATH}: {exc}; {entry!r}", file=sys.stderr, flush=True)


def _emit(event: dict[str, Any]) -> None:
    _record("event", event=event)
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
    """Persist full diagnostics locally without sending them to Electron."""
    _record("diagnostic", elapsed=round(time.monotonic() - _STARTED_AT, 3), message=message)
