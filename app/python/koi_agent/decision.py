"""Three-path action selection: rules, Jev fast choice, and a text model."""
from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import urlsplit

from . import protocol
from .executor import Action
from .models import JevDecision, OpenAICompatible

DECISION_SYSTEM_PROMPT = '根据浏览器快照选择最多一个安全动作，只返回 JSON：{"actions":[{"kind":"click|fill|press|wait|scroll|open","ref":"@e1","value":""}],"confidence":0.8}。只使用快照中存在的 ref；不要猜测密码。对于带联想下拉框的输入，先选择并确认下拉候选，再填写下一个字段或点击查询；不要在站点仍显示未确认的占位值时点击查询。'


@dataclass(frozen=True)
class DecisionResult:
    actions: tuple[Action, ...]
    confidence: float
    route: str
    rationale: str = ""
    terminal: str = ""


class Decision:
    def __init__(self, ai: OpenAICompatible | None = None, jev: JevDecision | None = None) -> None:
        self.ai = ai
        self.jev = jev

    @protocol.traced("decision.choose")
    def choose(
        self, goal, observation, start_url="", slow=False, advice="", force_entry=False,
        success_criteria=(), recent_action=None,
    ) -> DecisionResult:
        # A step's start_url is a way into the site, not the only valid page.
        # Detail/result paths on the same site are often the desired progress.
        current_host = urlsplit(observation.url).hostname
        entry_host = urlsplit(start_url).hostname if start_url else None
        if start_url and (not current_host or current_host != entry_host or
                          (force_entry and not observation.url.startswith(start_url.rstrip("/")))):
            return DecisionResult(
                (Action("open", start_url, expected="URL changed"),),
                1.0,
                "rule",
                "open start URL",
            )

        if self.jev:
            try:
                value = self.jev.choose(
                    goal,
                    observation,
                    success_criteria=success_criteria,
                    recent_action=recent_action,
                )
                if value["operation"] in {"DONE", "BLOCKED"}:
                    return DecisionResult(
                        (),
                        float(value.get("confidence", 0)),
                        "jev",
                        value["operation"].lower(),
                        value["operation"],
                    )
                return DecisionResult(
                    (self._jev_action(value),),
                    float(value.get("confidence", 0.8)),
                    "jev",
                    "typed choice",
                )
            except Exception as exc:
                protocol.trace_exception("decision.jev.fallback", exc)
                protocol.log(f"flow=decision jev_error={exc}")

        if self.ai:
            try:
                payload = json.dumps(
                    {
                        "goal": goal,
                        "url": observation.url,
                        "diff": observation.diff,
                        "elements": observation.elements,
                        "snapshot": observation.snapshot[:12000],
                        "advice": advice,
                        "slow": slow,
                    },
                    ensure_ascii=False,
                )
                raw = self.ai.chat(DECISION_SYSTEM_PROMPT, payload)
                protocol.trace("decision.model.raw", data=raw)
                data = json.loads(raw)
                protocol.trace("decision.model.parsed", data=data)
                # A page can change after every click/fill.  Keep exactly one
                # action per decision so the orchestrator observes the fresh
                # page and asks again instead of replaying a stale sequence.
                actions = tuple(
                    Action(
                        str(item["kind"]),
                        str(item.get("value", "")),
                        str(item.get("ref", "")),
                        str(item.get("expected", "")),
                        bool(item.get("sensitive")),
                    )
                    for item in data.get("actions", [])
                    if item.get("kind") in {"open", "click", "fill", "press", "wait", "scroll"}
                )[:1]
                return DecisionResult(
                    actions,
                    float(data.get("confidence", 0.5)),
                    "slow" if slow else "fast",
                    str(data.get("rationale", "")),
                )
            except Exception as exc:
                protocol.trace_exception("decision.model.fallback", exc)
                protocol.log(f"flow=decision model_error={exc}")

        return DecisionResult((), 0.0, "none", "no safe action available")

    @staticmethod
    def _jev_action(value: dict) -> Action:
        operation = str(value.get("operation", value.get("action", "WAIT"))).upper()
        reference = str(value.get("target", value.get("target_index", "")))
        kinds = {
            "CLICK": "click",
            "TYPE_TEXT": "fill",
            "SELECT": "click",
            "SCROLL": "scroll",
            "WAIT": "wait",
        }
        return Action(
            kinds.get(operation, "wait"),
            str(value.get("text", value.get("value", "500"))),
            reference,
        )
