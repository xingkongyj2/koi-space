"""把封闭动作集转换成 agent-browser 命令的唯一执行入口。"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass

import protocol.protocol as protocol
from logger import logger


@dataclass(frozen=True)
class Action:
    kind: str
    value: str = ""
    ref: str = ""
    expected: str = ""
    sensitive: bool = False
    observation_version: int | None = None


class Executor:
    ALLOWED = {"open", "click", "fill", "type", "press", "wait", "scroll"}

    def __init__(self, session) -> None:
        self.session = session

    @logger.traced("executor.execute")
    def execute(self, action: Action):
        """统一校验动作和确认标记，记录事件后执行对应浏览器命令。"""
        if action.kind not in self.ALLOWED:
            raise ValueError(f"unsupported action: {action.kind}")
        if action.sensitive:
            protocol.notify("此操作需要用户确认后继续", "blocking")
            raise PermissionError("user confirmation required")

        commands = {
            "open": ["open", action.value],
            "click": ["click", action.ref],
            "fill": ["fill", action.ref, action.value],
            "type": ["type", action.ref, action.value],
            "press": ["press", action.value],
            "wait": ["wait", action.value or "500"],
            "scroll": ["scroll", action.value or "down"],
        }
        logger.debug(f"flow=execute action={action.kind} ref={action.ref or '-'}")
        name = f"browser.{action.kind}"
        action_data = asdict(action)
        if action.sensitive:
            action_data["value"] = "<redacted>"
        protocol.tool_call(name, action_data, logger.current_iteration())
        started = time.monotonic()
        try:
            if action.kind == "type":
                logger.trace("executor.command", command=["fill", action.ref, ""])
                cleared = self.session.run(["fill", action.ref, ""])
                if not cleared.ok:
                    protocol.tool_result(
                        name,
                        False,
                        cleared.preview,
                        (time.monotonic() - started) * 1000,
                    )
                    return cleared
            if action.kind == "press" and action.ref:
                # press 操作当前焦点；指定 ref 时先聚焦对应控件。
                logger.trace("executor.command", command=["focus", action.ref])
                focused = self.session.run(["focus", action.ref])
                if not focused.ok:
                    protocol.tool_result(
                        name,
                        False,
                        focused.preview,
                        (time.monotonic() - started) * 1000,
                    )
                    return focused
            logger.trace("executor.command", command=commands[action.kind])
            result = self.session.run(commands[action.kind])
        except Exception as exc:
            logger.timing(
                "browser.action.command",
                started,
                action=action.kind,
                target=action.ref or (action.value if action.kind == "open" else ""),
            )
            protocol.tool_result(
                name, False, str(exc), (time.monotonic() - started) * 1000
            )
            raise
        logger.timing(
            "browser.action.command",
            started,
            action=action.kind,
            target=action.ref or (action.value if action.kind == "open" else ""),
        )
        preview = "<redacted>" if action.sensitive else result.preview
        protocol.tool_result(
            name, result.ok, preview, (time.monotonic() - started) * 1000
        )
        return result
