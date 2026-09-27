"""恢复会话的完整时间序事件，提取供后续规划使用的执行状态。"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any


def execution_context(events: list[dict[str, Any]]) -> dict[str, Any]:
    """从历史事件提取有效计划及最近进度，原始历史另行完整保留。"""
    context: dict[str, Any] = {}
    for event in events:
        if event.get("type") != "thinking":
            continue
        try:
            value = json.loads(event.get("text", ""))
        except (ValueError, TypeError):
            continue
        if not isinstance(value, dict):
            continue
        if value.get("kind") == "planner_plan" and isinstance(value.get("plan"), dict):
            context["previous_plan"] = value["plan"]
            if value["plan"].get("status") == "ready":
                context["active_plan"] = value["plan"]
                context.pop("execution_progress", None)
        elif value.get("kind") == "task_progress":
            context["execution_progress"] = value
            if isinstance(value.get("result"), dict):
                context.setdefault("step_results", []).append(value["result"])
            if value.get("current_url"):
                context["entry_url"] = value["current_url"]
        elif value.get("kind") == "task_outcome":
            context["task_outcome"] = value
    return context


def task_history(task: dict[str, Any], user_input: str) -> list[dict[str, Any]]:
    """保留全部事件，并确保当前用户请求只追加一次。

    宿主通常已提供当前消息；直接 Python 调用可只传入之前的历史。
    这里不裁剪旧消息，也不以摘要替代原始上下文。"""
    source = task.get("history", [])
    if not isinstance(source, list) or any(
        not isinstance(event, dict) or not isinstance(event.get("type"), str) for event in source
    ):
        raise ValueError("history must be a chronological array of session events")
    events = deepcopy(source)
    if user_input and not (
        events and events[-1].get("type") == "user_input" and events[-1].get("text") == user_input
    ):
        events.append({"type": "user_input", "text": user_input})
    return events
