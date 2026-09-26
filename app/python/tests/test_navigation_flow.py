"""Regression coverage for already-open and newly-opened navigation goals."""
import json
import unittest

from koi_agent.budget import Budget
from koi_agent.observer import Observation
from koi_agent.orchestrator import Orchestrator
from koi_agent.planner import parse_plan
from koi_agent.validator import Validator


class FakeSession:
    def __init__(self, url="about:blank"):
        self.url = url
        self.commands = []

    def current_url(self):
        return self.url

    def run(self, args, timeout=30):
        self.commands.append(args)
        if args[0] == "open":
            self.url = args[1]
        return type("Result", (), {"ok": True, "stdout": "- button \"Play\" [ref=e1]", "preview": "ok"})()


class SettlingSession(FakeSession):
    """The first snapshot is unchanged; the page updates after settling."""
    def __init__(self):
        super().__init__("https://example.com/")
        self.snapshots = 0

    def run(self, args, timeout=30):
        self.commands.append(args)
        if args[0] == "click":
            return type("Result", (), {"ok": True, "stdout": "✓ Done", "preview": "ok"})()
        if args[0] == "snapshot":
            self.snapshots += 1
            body = "before" if self.snapshots == 1 else "after click"
            return type("Result", (), {"ok": True, "stdout": body, "preview": body})()
        return type("Result", (), {"ok": True, "stdout": "", "preview": "ok"})()


def navigation_plan():
    return parse_plan(json.dumps({
        "status": "ready",
        "needs_browser": True,
        "question": "",
        "direct_answer": "",
        "steps": [{
            "id": "s1",
            "goal": "导航到腾讯视频首页",
            "start_url": "https://v.qq.com/",
            "success_criteria": ["url_prefix:https://v.qq.com/"],
            "depends_on": [],
            "needs_user_confirmation": False,
            "risk": "low",
            "parallel_group": "",
        }],
    }))


class NavigationFlowTests(unittest.TestCase):
    def test_navigation_criteria_become_machine_checkable(self):
        self.assertEqual(navigation_plan().steps[0].success_criteria, ("url_prefix:https://v.qq.com/",))

    def test_already_open_page_finishes_without_model_or_action(self):
        session = FakeSession("https://v.qq.com/")
        result = Orchestrator(session, navigation_plan(), budget=Budget()).run()
        self.assertIn("任务完成", result)
        self.assertEqual(session.commands, [["snapshot", "-i"]])

    def test_navigation_opens_once_and_verifies(self):
        session = FakeSession()
        result = Orchestrator(session, navigation_plan(), budget=Budget()).run()
        self.assertIn("任务完成", result)
        self.assertEqual([command for command in session.commands if command[0] == "open"], [["open", "https://v.qq.com/"]])

    def test_snapshot_ref_is_extracted(self):
        from koi_agent.observer import Observer
        elements = list(Observer._elements('- button "Play" [ref=e1]'))
        self.assertEqual(elements, [{"ref": "@e1", "text": 'button "Play"'}])

    def test_successful_click_is_allowed_to_settle_before_noop_failure(self):
        from koi_agent.decision import DecisionResult
        from koi_agent.executor import Action
        session = SettlingSession()
        plan = parse_plan(json.dumps({
            "status": "ready", "needs_browser": True,
            "question": "", "direct_answer": "",
            "steps": [{"id": "s1", "goal": "点击按钮", "success_criteria": ["text_contains:after click"],
                        "depends_on": [], "start_url": "https://example.com/",
                        "needs_user_confirmation": False, "risk": "low", "parallel_group": ""}],
        }))
        result = Orchestrator(
            session, plan, budget=Budget(max_steps=5, max_failures=1),
            decision=type("Decision", (), {"choose": lambda *_args, **_kwargs: DecisionResult((Action("click", ref="@e1"),), 1.0, "test")})(),
        ).run()
        self.assertIn("任务完成", result)


if __name__ == "__main__":
    unittest.main()
