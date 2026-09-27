"""Regression coverage for already-open and newly-opened navigation goals."""

import json
import unittest
from unittest.mock import patch

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
        return type(
            "Result", (), {"ok": True, "stdout": '- button "Play" [ref=e1]', "preview": "ok"}
        )()


class SettlingSession(FakeSession):
    """The first post-click snapshot is unchanged; the next one updates."""

    def __init__(self):
        super().__init__("https://example.com/")
        self.snapshots = 0

    def run(self, args, timeout=30):
        self.commands.append(args)
        if args[0] == "click":
            return type("Result", (), {"ok": True, "stdout": "✓ Done", "preview": "ok"})()
        if args[0] == "snapshot":
            self.snapshots += 1
            label = "before" if self.snapshots <= 2 else "after click"
            body = f'- button "{label}" [ref=e1]'
            return type("Result", (), {"ok": True, "stdout": body, "preview": body})()
        return type("Result", (), {"ok": True, "stdout": "", "preview": "ok"})()


def navigation_plan():
    return parse_plan(
        json.dumps(
            {
                "status": "ready",
                "needs_browser": True,
                "question": "",
                "direct_answer": "",
                "steps": [
                    {
                        "id": "s1",
                        "goal": "导航到腾讯视频首页",
                        "start_url": "https://v.qq.com/",
                        "success_criteria": ["url_prefix:https://v.qq.com/"],
                        "depends_on": [],
                        "needs_user_confirmation": False,
                        "risk": "low",
                        "parallel_group": "",
                    }
                ],
            }
        )
    )


class NavigationFlowTests(unittest.TestCase):
    def test_navigation_criteria_become_machine_checkable(self):
        self.assertEqual(
            navigation_plan().steps[0].success_criteria, ("url_prefix:https://v.qq.com/",)
        )

    def test_already_open_page_finishes_without_model_or_action(self):
        session = FakeSession("https://v.qq.com/")
        result = Orchestrator(session, navigation_plan(), budget=Budget()).run()
        self.assertIn("任务完成", result.summary)
        self.assertEqual(session.commands, [["snapshot", "-i"]])

    def test_navigation_opens_once_and_verifies(self):
        session = FakeSession()
        result = Orchestrator(session, navigation_plan(), budget=Budget()).run()
        self.assertIn("任务完成", result.summary)
        self.assertEqual(
            [command for command in session.commands if command[0] == "open"],
            [["open", "https://v.qq.com/"]],
        )

    def test_explicit_navigation_opens_from_another_path_on_same_site(self):
        target = "https://v.qq.com/biu/u/history/"
        session = FakeSession("https://v.qq.com/other")
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
                            "goal": "打开历史记录",
                            "success_criteria": [f"url_prefix:{target}"],
                            "depends_on": [],
                            "start_url": target,
                            "needs_user_confirmation": False,
                            "risk": "low",
                            "parallel_group": "",
                        }
                    ],
                }
            )
        )
        result = Orchestrator(session, plan, budget=Budget()).run()
        self.assertIn("任务完成", result.summary)
        self.assertEqual(
            [command for command in session.commands if command[0] == "open"], [["open", target]]
        )

    def test_snapshot_ref_is_extracted(self):
        from koi_agent.observer import Observer

        elements = list(Observer._elements('- button "Play" [ref=e1]'))
        self.assertEqual(elements, [{"ref": "@e1", "text": 'button "Play"'}])

    def test_successful_click_rechecks_without_fixed_settle_delay(self):
        from koi_agent.decision import DecisionResult
        from koi_agent.executor import Action

        session = SettlingSession()
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
                            "goal": "点击按钮",
                            "success_criteria": [{"type": "element_text", "value": "after click"}],
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
        with patch("time.sleep") as sleep:
            result = Orchestrator(
                session,
                plan,
                budget=Budget(max_steps=5, max_failures=1),
                decision=type(
                    "Decision",
                    (),
                    {
                        "choose": lambda *_args, **_kwargs: DecisionResult(
                            (Action("click", ref="@e1"),), 1.0, "test"
                        )
                    },
                )(),
            ).run()
        sleep.assert_not_called()
        self.assertEqual(result.steps[0].status, "completed")
        self.assertEqual(result.status, "blocked")  # 非导航任务缺少最终模型时不谎报成功。
        self.assertEqual(session.snapshots, 3)

    def test_entry_url_does_not_restrict_the_result_page(self):
        result = Observation("https://example.com/items/42", "", "订单详情", "", True)
        self.assertTrue(
            Validator().step(result, ("text_contains:订单详情",), "https://example.com/orders/")
        )

    def test_full_page_text_is_available_for_completion_without_polluting_action_refs(self):
        from koi_agent.observer import Observer

        class PageSession:
            def __init__(self):
                self.commands = []

            def current_url(self):
                return "https://example.com/orders/42"

            def run(self, args, timeout=30):
                self.commands.append(args)
                body = (
                    '- button "返回" [ref=e1]'
                    if "-i" in args
                    else '- heading "订单详情"\n- text "已发货"'
                )
                return type("Result", (), {"ok": True, "stdout": body})()

        session = PageSession()
        observation = Observer(session, include_full=True).capture()
        self.assertEqual([element["ref"] for element in observation.elements], ["@e1"])
        self.assertTrue(Validator().step(observation, ("text_contains:已发货",)))
        self.assertEqual(session.commands, [["snapshot"], ["snapshot", "-i"]])


if __name__ == "__main__":
    unittest.main()
