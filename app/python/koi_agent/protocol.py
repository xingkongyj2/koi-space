"""NDJSON event protocol between the Python agent and the Electron main process.

The main process reads this process's stdout line by line and turns each line
into an `HlEvent` (see `app/src/shared/session-schemas.ts`), which is persisted
to SQLite and streamed to the renderer. One JSON object per line, no other
stdout output — anything else on stdout is dropped by the parser.

Diagnostics and emitted events are saved under the Python root's log directory.
Timing rows also go to a per-task ``*.timing.log`` for quick inspection.
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
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from functools import wraps
from itertools import count
from pathlib import Path
from typing import Any

_STARTED_AT = time.monotonic()
LOG_DIR = Path(__file__).resolve().parent.parent / "log"
LOG_PATH = LOG_DIR / f"agent-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}-{os.getpid()}.jsonl"
_TIMING_LABELS = {
    "task.total": "任务总耗时",
    "planning.entry.model_request": "规划①定位网址·模型请求",
    "planning.entry.total": "规划①定位网址·总耗时",
    "planning.entry.browser_bind": "绑定浏览器标签页",
    "planning.entry.browser_open": "打开入口页面",
    "planning.full.model_request": "规划②拆分任务·模型请求",
    "planning.full.total": "规划②拆分任务·总耗时",
    "planning.full.wait_after_browser_open": "页面打开后等待规划",
    "planning.full.parallel_wall": "页面打开与规划并行总耗时",
    "runtime.orchestrator.total": "执行层总耗时",
    "runtime.step.total": "子任务总耗时",
    "runtime.observe": "观察页面",
    "runtime.goal_check": "判断目标是否完成",
    "runtime.decision": "选择下一步动作",
    "runtime.execute": "执行动作总耗时",
    "browser.action.page_observed_to_dispatch": "观察页面到发出动作",
    "browser.action.command": "浏览器命令耗时",
    "decision.jev.request": "Jev 接口请求",
    "decision.jev.total": "Jev 总耗时",
    "decision.jev.type_text_model_request": "Jev 填写内容模型请求",
    "verification.model_request": "完成判断模型请求",
    "model.http.network": "模型 HTTP 网络往返",
}
_CONTEXT: ContextVar[dict] = ContextVar("log_context", default={})
_HISTORY: ContextVar[list[dict[str, Any]] | None] = ContextVar("task_history", default=None)
_CALLS = count(1)


def set_context(**data: Any) -> None:
    _CONTEXT.set({**_CONTEXT.get(), **data})


def current_iteration() -> int:
    return _CONTEXT.get().get("iteration", 0)


@contextmanager
def capture_events(history: list[dict[str, Any]]):
    """Keep emitted messages in the same history used by later planner calls.

    Electron persists these exact events for the next process/resume. Context
    scoping keeps independent tasks and tests from sharing a mutable history.
    """
    token = _HISTORY.set(history)
    try:
        yield
    finally:
        _HISTORY.reset(token)


def _json_value(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    return f"<{type(value).__name__}>"


def trace(stage: str, **data: Any) -> None:
    _record("trace", stage=stage, **data)


def timing(stage: str, started_at: float, **data: Any) -> float:
    """Record a prominent local-only wall-clock duration in milliseconds."""
    now = time.monotonic()
    ms = round((now - started_at) * 1000, 2)
    _record("timing", stage=stage, ms=ms,
            since_start_ms=round((now - _STARTED_AT) * 1000, 2), **data)
    return ms


@contextmanager
def measure(stage: str, **data: Any):
    """Time a layer even when it fails, without adding a frontend event."""
    started_at = time.monotonic()
    try:
        yield
    finally:
        timing(stage, started_at, **data)


def timed(stage: str):
    """Decorator for whole-layer timing, including failure paths."""
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with measure(stage):
                return function(*args, **kwargs)
        return wrapped
    return decorate


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


def _format_readable(entry: dict) -> str:
    stage = entry.get("stage", entry["kind"])
    if entry["kind"] == "timing":
        label = _TIMING_LABELS.get(stage)
        display_stage = f"{stage}（{label}）" if label else stage
        context = " ".join(f"{key}={entry[key]}" for key in
                           ("session_id", "step_id", "iteration") if key in entry)
        details = " ".join(f"{key}={value}" for key, value in entry.items()
                           if key not in {"time", "pid", "kind", "stage", "ms", "since_start_ms", "session_id",
                                          "step_id", "iteration", "call_id", "parent_call_id"})
        return ("\n" + ">" * 24 + " [TIMING] " + ">" * 24 + "\n"
                + f"{entry['time']} | t+{entry['since_start_ms']:,.2f} ms | "
                + f"{display_stage} | {entry['ms']:,.2f} ms\n"
                + " | ".join(part for part in (context, details) if part) + "\n"
                + "<" * 58 + "\n\n")
    header = f"[{entry['time']}] {stage}"
    for key in ("session_id", "step_id", "iteration", "call_id"):
        if key in entry:
            header += f" | {key}={entry[key]}"
    # Normalize dataclasses once so nested fields can also be rendered as text.
    normalized = json.loads(json.dumps(entry, ensure_ascii=False, default=_json_value))
    blocks = []

    def expand(value, path=""):
        if isinstance(value, dict):
            return {key: expand(item, f"{path}.{key}" if path else key) for key, item in value.items()}
        if isinstance(value, list):
            return [expand(item, f"{path}[{index}]") for index, item in enumerate(value)]
        if isinstance(value, str):
            # Raw model bodies often contain JSON encoded inside a string.
            display = value
            try:
                parsed = json.loads(value)
                if isinstance(parsed, (dict, list)):
                    display = json.dumps(parsed, ensure_ascii=False, indent=2)
            except (ValueError, TypeError):
                pass
            if "\n" in display:
                blocks.append(f"--- {path} ---\n{display}")
                return f"[见下方 {path}]"
        return value

    body = json.dumps(expand(normalized), ensure_ascii=False, indent=2)
    return "\n".join(["=" * 88, header, body, *blocks]) + "\n\n"


def _record(kind: str, **data: Any) -> None:
    """One file per agent process, independent of cwd and Electron userData."""
    entry = {"time": datetime.now(timezone.utc).isoformat(), "pid": os.getpid(), **_CONTEXT.get(), "kind": kind, **data}
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, default=_json_value) + "\n")
        readable = _format_readable(entry)
        with LOG_PATH.with_suffix(".log").open("a", encoding="utf-8") as handle:
            handle.write(readable)
        if kind == "timing":
            with LOG_PATH.with_suffix(".timing.log").open("a", encoding="utf-8") as handle:
                handle.write(readable)
    except (OSError, TypeError, ValueError) as exc:
        # Keep the task/event protocol alive if the installation is read-only.
        print(f"[koi-agent] cannot write {LOG_PATH}: {exc}; {entry!r}", file=sys.stderr, flush=True)


def _emit(event: dict[str, Any]) -> None:
    history = _HISTORY.get()
    if history is not None:
        history.append(deepcopy(event))
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
