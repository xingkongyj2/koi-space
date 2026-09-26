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
import inspect
import traceback
from contextvars import ContextVar
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from functools import wraps
from itertools import count
from pathlib import Path
from typing import Any

_STARTED_AT = time.monotonic()
LOG_DIR = Path(__file__).resolve().parent.parent / "log"
LOG_PATH = LOG_DIR / f"agent-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}-{os.getpid()}.jsonl"
_CONTEXT: ContextVar[dict] = ContextVar("log_context", default={})
_CALLS = count(1)


def set_context(**data: Any) -> None:
    _CONTEXT.set({**_CONTEXT.get(), **data})


def _json_value(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    return f"<{type(value).__name__}>"


def trace(stage: str, **data: Any) -> None:
    _record("trace", stage=stage, **data)


def trace_exception(stage: str, exc: Exception) -> None:
    trace(stage, error=str(exc), error_type=type(exc).__name__, traceback=traceback.format_exc())


def traced(stage: str):
    """Log layer boundaries, preserving nested call/step correlation on errors."""
    def decorate(function):
        signature = inspect.signature(function)

        @wraps(function)
        def wrapped(*args, **kwargs):
            inputs = dict(signature.bind(*args, **kwargs).arguments)
            inputs.pop("self", None)
            context = _CONTEXT.get()
            extra = {"call_id": next(_CALLS), "parent_call_id": context.get("call_id")}
            if "step" in inputs and hasattr(inputs["step"], "id"):
                extra["step_id"] = inputs["step"].id
            token = _CONTEXT.set({**context, **extra})
            started = time.monotonic()
            try:
                trace(f"{stage}.start", inputs=inputs)
                result = function(*args, **kwargs)
                trace(f"{stage}.end", result=result, ms=round((time.monotonic() - started) * 1000, 2))
                return result
            except Exception as exc:
                trace(f"{stage}.error", error=str(exc), error_type=type(exc).__name__, traceback=traceback.format_exc(), ms=round((time.monotonic() - started) * 1000, 2))
                raise
            finally:
                _CONTEXT.reset(token)
        return wrapped
    return decorate


def _record(kind: str, **data: Any) -> None:
    """One file per agent process, independent of cwd and Electron userData."""
    entry = {"time": datetime.now(timezone.utc).isoformat(), "pid": os.getpid(), **_CONTEXT.get(), "kind": kind, **data}
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, default=_json_value) + "\n")
    except (OSError, TypeError, ValueError) as exc:
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
