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
        """校验动作、执行命令并返回统一浏览器结果。"""
        # Executor 是浏览器命令唯一出口，先校验动作，再生成固定命令参数。
        self._validate(action)
        commands = self._commands(action)
        name = f"browser.{action.kind}"
        self._announce(name, action)
        started = time.monotonic()
        try:
            # type/press 可能需要先清空输入框或聚焦控件。
            prepared = self._prepare(action)
            if prepared is not None:
                return self._report(name, prepared, started, action)
            # 前置动作成功后才执行用户请求的主命令。
            logger.trace("executor.command", command=commands[action.kind])
            result = self.session.run(commands[action.kind])
        except Exception as exc:
            # 协议记录异常，但原异常继续抛给 Orchestrator 的恢复策略。
            self._report_exception(name, action, started, exc)
            raise
        return self._report(name, result, started, action)

    def _validate(self, action):
        """检查动作类型和用户确认标记。"""
        # 封闭动作集防止模型拼接未授权的 agent-browser 子命令。
        if action.kind not in self.ALLOWED:
            raise ValueError(f"unsupported action: {action.kind}")
        if action.sensitive:
            # 敏感动作在浏览器执行前停住，必须由上层确认后重新进入流程。
            protocol.notify("此操作需要用户确认后继续", "blocking")
            raise PermissionError("user confirmation required")

    @staticmethod
    def _commands(action):
        """把封闭动作映射为 agent-browser 命令参数。"""
        # 所有参数都来自已校验的 Action；这里不再做业务决策。
        return {
            "open": ["open", action.value],
            "click": ["click", action.ref],
            "fill": ["fill", action.ref, action.value],
            "type": ["type", action.ref, action.value],
            "press": ["press", action.value],
            "wait": ["wait", action.value or "500"],
            "scroll": ["scroll", action.value or "down"],
        }

    def _announce(self, name, action):
        """记录动作调用事件，并对敏感参数做脱敏。"""
        logger.debug(f"flow=execute action={action.kind} ref={action.ref or '-'}")
        action_data = asdict(action)
        if action.sensitive:
            action_data["value"] = "<redacted>"
        protocol.tool_call(name, action_data, logger.current_iteration())

    def _prepare(self, action):
        """执行 type 的清空和 press 的聚焦前置动作。"""
        if action.kind == "type":
            # type 语义是替换输入值，因此先用 fill 清空旧值。
            logger.trace("executor.command", command=["fill", action.ref, ""])
            cleared = self.session.run(["fill", action.ref, ""])
            if not cleared.ok:
                return cleared
        if action.kind == "press" and action.ref:
            # press 操作当前焦点；指定 ref 时先聚焦对应控件。
            logger.trace("executor.command", command=["focus", action.ref])
            focused = self.session.run(["focus", action.ref])
            if not focused.ok:
                return focused
        return None

    def _report(self, name, result, started, action):
        """记录命令耗时和协议结果。"""
        # 统一写耗时和 tool_result，调用方只关心 BrowserResult。
        logger.timing(
            "browser.action.command",
            started,
            action=action.kind,
            target=action.ref or (action.value if action.kind == "open" else ""),
        )
        preview = "<redacted>" if action.sensitive else result.preview
        protocol.tool_result(name, result.ok, preview, (time.monotonic() - started) * 1000)
        return result

    def _report_exception(self, name, action, started, exc):
        """记录命令异常，并保留原异常供上层恢复策略处理。"""
        # 异常也要落一条失败结果，保证日志和协议事件成对出现。
        logger.timing(
            "browser.action.command",
            started,
            action=action.kind,
            target=action.ref or (action.value if action.kind == "open" else ""),
        )
        protocol.tool_result(name, False, str(exc), (time.monotonic() - started) * 1000)
