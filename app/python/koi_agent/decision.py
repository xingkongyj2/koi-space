"""Three-path action selection: rules, Jev fast choice, and a text model."""
from __future__ import annotations

import json
from dataclasses import dataclass

from . import protocol
from .executor import Action


@dataclass(frozen=True)
class DecisionResult:
    actions: tuple[Action, ...]
    confidence: float
    route: str
    rationale: str = ""


class Decision:
    def __init__(self, model=None, jev=None) -> None:
        self.model = model
        self.jev = jev

    def choose(
        self, goal, observation, start_url="", slow=False, advice=""
    ) -> DecisionResult:
        if start_url and not observation.url.startswith(start_url.rstrip("/")):
            return DecisionResult(
                (Action("open", start_url, expected="URL changed"),),
                1.0,
                "rule",
                "open start URL",
            )

        if self.jev:
            try:
                value = self.jev.choose(goal, observation.snapshot)
                return DecisionResult(
                    (self._jev_action(value),),
                    float(value.get("confidence", 0.8)),
                    "jev",
                    "typed choice",
                )
            except Exception as exc:
                protocol.log(f"flow=decision jev_error={exc}")

        if self.model:
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
                data = json.loads(self.model(payload))
                actions = tuple(
                    Action(
                        str(item["kind"]),
                        str(item.get("value", "")),
                        str(item.get("ref", "")),
                        str(item.get("expected", "")),
                        bool(item.get("sensitive")),
                    )
                    for item in data.get("actions", [])[:10]
                    if item.get("kind") in {"open", "click", "fill", "press", "wait", "scroll"}
                )
                return DecisionResult(
                    actions,
                    float(data.get("confidence", 0.5)),
                    "slow" if slow else "fast",
                    str(data.get("rationale", "")),
                )
            except Exception as exc:
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
            "DONE": "wait",
            "BLOCKED": "wait",
        }
        return Action(
            kinds.get(operation, "wait"),
            str(value.get("text", value.get("value", "500"))),
            reference,
        )
