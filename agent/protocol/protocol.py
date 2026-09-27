"""Python 与桌面端的 NDJSON 事件协议；stdout 只输出 HlEvent。"""

from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from typing import Any

from logger import logger

_HISTORY: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "task_history", default=None
)


@contextmanager
def capture_events(history: list[dict[str, Any]]):
    """本轮事件追加到任务历史；退出时恢复上下文，避免跨任务污染。"""
    token = _HISTORY.set(history)
    try:
        yield
    finally:
        _HISTORY.reset(token)


def _emit(event: dict[str, Any]) -> None:
    """先追加历史，再原子式输出一行 NDJSON 并刷新缓冲。"""
    history = _HISTORY.get()
    if history is not None:
        history.append(deepcopy(event))
    logger.record_event(event)
    sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def thinking(text: str) -> None:
    """发送可持久化的规划或执行状态。"""
    _emit({"type": "thinking", "text": text})


def tool_call(name: str, args: Any, iteration: int) -> None:
    """记录即将执行的浏览器动作及参数。"""
    _emit({"type": "tool_call", "name": name, "args": args, "iteration": iteration})


def tool_result(name: str, ok: bool, preview: str, ms: float) -> None:
    """记录动作执行是否成功及其结果预览。"""
    _emit(
        {
            "type": "tool_result",
            "name": name,
            "ok": ok,
            "preview": preview,
            "ms": round(ms, 1),
        }
    )


def notify(message: str, level: str = "info") -> None:
    """发送用户可见提示，blocking 表示需要用户介入。"""
    _emit({"type": "notify", "message": message, "level": level})


def error(message: str) -> None:
    """发送任务失败事件，不伪装成完成结果。"""
    _emit({"type": "error", "message": message})


def done(summary: str, iterations: int) -> None:
    """发送本轮结束事件及已执行动作数量。"""
    _emit({"type": "done", "summary": summary, "iterations": iterations})
