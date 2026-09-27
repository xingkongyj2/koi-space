"""Recovery must interrupt successful commands that make no visible progress."""

import json
import unittest

from koi_agent.budget import Budget
from koi_agent.decision import Decision, DecisionResult
from koi_agent.executor import Action
from koi_agent.observer import Observation
from koi_agent.orchestrator import Orchestrator
from koi_agent.planner import parse_plan


class StallRecoveryTests(unittest.TestCase):
    def test_field_oscillation_with_banner_changes_and_ref_renumbering_recovers(self):
        class Session:
            value = "北京北"

            def run(self, args, timeout=30):
                self.value = "潜江" if args[0] == "fill" else "北京北"
                return type("Result", (), {"ok": True, "preview": "ok"})()

        session = Session()

        class Pages:
            count = 0

            def capture(self):
                self.count += 1
                label = f'textbox "目的地" : {session.value}'
                ref = f"@e{self.count}"
                snapshot = f'- {label} [ref=e{self.count}]\n- heading "广告{self.count}"'
                return Observation(
                    "https://example.com/",
                    "",
                    snapshot,
                    "",
                    True,
                    ({"ref": ref, "text": label},),
                    f"广告{self.count}",
                )

        class Choices:
            recovery = []

            def choose(self, goal, observation, *args, **kwargs):
                self.recovery.append(kwargs["force_reasoning"])
                action = (
                    Action("fill", "潜江", observation.elements[0]["ref"])
                    if session.value == "北京北"
                    else Action("press", "Enter")
                )
                return DecisionResult((action,), 1.0, "test")

        plan = parse_plan(
            json.dumps(
                {
                    "status": "ready",
                    "needs_browser": True,
                    "question": "",
                    "direct_answer": "",
                    "steps": [
                        {
                            "id": "s1",
                            "goal": "查询结果",
                            "success_criteria": ["text_contains:结果"],
                            "depends_on": [],
                            "start_url": "https://example.com/",
                            "needs_user_confirmation": False,
                            "risk": "low",
                            "parallel_group": "",
                        }
                    ],
                }
            )
        )
        choices = Choices()
        result = Orchestrator(
            session, plan, decision=choices, observer=Pages(), budget=Budget(max_steps=20)
        ).run()
        self.assertIn("控件状态往返循环", result.summary)
        self.assertEqual(choices.recovery, [False, False, False, True, True])

    def test_recovery_bypasses_jev_and_supplies_action_history(self):
        class Jev:
            def choose(self, *args, **kwargs):
                raise AssertionError("stalled execution must bypass JEV")

        class AI:
            def chat(self, system, payload):
                self.payload = json.loads(payload)
                return '{"actions":[{"kind":"press","value":"Escape"}]}'

        ai = AI()
        observation = Observation("https://example.com/", "", "form", "", False)
        history = [{"action": {"kind": "wait"}, "page_changed": False}]
        decision = Decision(ai=ai, jev=Jev()).choose(
            "查询",
            observation,
            force_reasoning=True,
            action_history=history,
            advice="不要重复等待",
            recent_action=Action("wait", "500"),
            slow=True,
        )
        self.assertEqual(decision.actions[0].kind, "press")
        self.assertEqual(ai.payload["recent_actions"], history)
        self.assertEqual(ai.payload["advice"], "不要重复等待")

    def test_unchanged_waits_trigger_recovery_and_stop_before_budget_exhaustion(self):
        class Session:
            def current_url(self):
                return "https://example.com/"

            def run(self, args, timeout=30):
                return type("Result", (), {"ok": True, "stdout": "form", "preview": "ok"})()

        class Choices:
            def __init__(self):
                self.recovery = []

            def choose(self, *args, **kwargs):
                self.recovery.append(kwargs["force_reasoning"])
                return DecisionResult((Action("wait", "1"),), 1.0, "test")

        plan = parse_plan(
            json.dumps(
                {
                    "status": "ready",
                    "needs_browser": True,
                    "question": "",
                    "direct_answer": "",
                    "steps": [
                        {
                            "id": "s1",
                            "goal": "查询结果",
                            "success_criteria": ["text_contains:结果"],
                            "depends_on": [],
                            "start_url": "https://example.com/",
                            "needs_user_confirmation": False,
                            "risk": "low",
                            "parallel_group": "",
                        }
                    ],
                }
            )
        )
        choices = Choices()
        result = Orchestrator(Session(), plan, decision=choices, budget=Budget(max_steps=20)).run()
        self.assertIn("无可观察进展", result.summary)
        self.assertEqual(choices.recovery, [False, False, True, True, True])


if __name__ == "__main__":
    unittest.main()
