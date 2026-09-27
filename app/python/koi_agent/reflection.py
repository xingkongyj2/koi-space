"""根据当前证据和失败原因生成恢复建议，不直接执行浏览器操作。"""

from __future__ import annotations

import json

from . import protocol
from .models import OpenAICompatible

REFLECTION_SYSTEM_PROMPT = "根据任务目标、当前页面快照和执行错误，给出下一次决策可直接使用的简短建议。只使用页面中存在的元素，不猜测密码或目标。"


class Reflection:
    def __init__(self, ai: OpenAICompatible | None = None) -> None:
        self.ai = ai

    @protocol.traced("reflection.advise")
    def advise(self, goal: str, observation, error: str = "") -> str:
        """根据错误给出恢复建议；反思模型不可用时返回确定性提示。"""
        protocol.log(f"flow=reflection goal={goal[:80]!r} error={error[:120]!r}")
        if self.ai:
            try:
                payload = json.dumps(
                    {"goal": goal, "snapshot": observation.snapshot, "error": error},
                    ensure_ascii=False,
                )
                return self.ai.chat(REFLECTION_SYSTEM_PROMPT, payload)
            except Exception as exc:  # reflection must never stop recovery
                protocol.trace_exception("reflection.model.fallback", exc)
                protocol.log(f"flow=reflection failed={exc}")
        return "重新观察页面，只使用当前 snapshot 中仍存在的元素引用。"
