"""One chronological event history, restored from the session's durable log."""
from __future__ import annotations

from copy import deepcopy
import json
from typing import Any


def execution_context(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Recover structured state from prior agent events, preserving full history separately."""
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
            if value.get("current_url"):
                context["entry_url"] = value["current_url"]
    return context


def task_history(task: dict[str, Any], user_input: str) -> list[dict[str, Any]]:
    """Preserve every event and include the current request exactly once.

    Electron supplies the complete session log, including the current user
    message. Direct Python callers may omit history or provide only earlier
    turns. Never trim old messages or replace them with summaries here.
    """
    source = task.get("history", [])
    if not isinstance(source, list) or any(
        not isinstance(event, dict) or not isinstance(event.get("type"), str)
        for event in source
    ):
        raise ValueError("history must be a chronological array of session events")
    events = deepcopy(source)
    if user_input and not (
        events
        and events[-1].get("type") == "user_input"
        and events[-1].get("text") == user_input
    ):
        events.append({"type": "user_input", "text": user_input})
    return events
