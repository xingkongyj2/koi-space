"""The only module that translates safe actions to agent-browser commands."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import time

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

    @protocol.traced("executor.execute")
    def execute(self, action: Action):
        if action.kind not in self.ALLOWED:
            raise ValueError(f"unsupported action: {action.kind}")
        if action.sensitive:
            protocol.notify("此操作需要用户确认后继续", "blocking")
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
        protocol.trace("executor.command", command=commands[action.kind])
        name = f"browser.{action.kind}"
        action_data = asdict(action)
        if action.sensitive:
            action_data["value"] = "<redacted>"
        protocol.tool_call(name, action_data, protocol.current_iteration())
        started = time.monotonic()
        try:
            result = self.session.run(commands[action.kind])
        except Exception as exc:
            protocol.tool_result(name, False, str(exc), (time.monotonic() - started) * 1000)
            raise
        preview = "<redacted>" if action.sensitive else result.preview
        protocol.tool_result(name, result.ok, preview, (time.monotonic() - started) * 1000)
        return result
