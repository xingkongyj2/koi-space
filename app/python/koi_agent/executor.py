"""The only module that translates safe actions to agent-browser commands."""
from __future__ import annotations

from dataclasses import dataclass

from . import protocol


@dataclass(frozen=True)
class Action:
    kind: str
    value: str = ""
    ref: str = ""
    expected: str = ""
    sensitive: bool = False


class Executor:
    ALLOWED = {"open", "click", "fill", "press", "wait", "scroll"}

    def __init__(self, session) -> None:
        self.session = session

    def execute(self, action: Action):
        if action.kind not in self.ALLOWED:
            raise ValueError(f"unsupported action: {action.kind}")
        if action.sensitive:
            protocol.notify("此操作需要用户确认后继续", "warning")
            raise PermissionError("user confirmation required")

        commands = {
            "open": ["open", action.value],
            "click": ["click", action.ref],
            "fill": ["fill", action.ref, action.value],
            "press": ["press", action.ref, action.value],
            "wait": ["wait", action.value or "500"],
            "scroll": ["scroll", action.value or "down"],
        }
        protocol.log(
            f"flow=execute action={action.kind} ref={action.ref or '-'}"
        )
        return self.session.run(commands[action.kind])
