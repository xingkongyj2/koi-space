"""Completion is judged from the actual page, not guessed URL patterns."""

import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock

from koi_agent.budget import Budget
from koi_agent.completion import CompletionVerifier
from koi_agent.decision import Decision, DecisionResult
from koi_agent.executor import Action
from koi_agent.observer import Observation
from koi_agent.orchestrator import Orchestrator
from koi_agent.planner import Plan, Step


class SequenceObserver:
    def __init__(self, *observations):
        self._items = iter(observations)

    def capture(self):
        return next(self._items)


class RecordingExecutor:
    def __init__(self):
        self.actions = []

    def execute(self, action):
        self.actions.append(action)
        return type("Result", (), {"ok": True, "preview": "ok"})()


class CompletionFlowTests(unittest.TestCase):
    def test_same_site_result_path_never_forces_the_entry_url(self):
        page = Observation("https://example.com/items/42", "", "详情", "", True)
        result = Decision().choose("查看详情", page, "https://example.com/items/")
        self.assertEqual(result.route, "none")
        self.assertEqual(result.actions, ())

    def test_model_verdict_requires_a_supported_contract(self):
        step = Step(
            "s1",
            "打开订单详情",
            ("text_contains:订单详情",),
            start_url="https://example.com/orders/",
        )
        page = Observation("https://example.com/orders/42", "", "订单详情", "", True)
        ai = Mock()
        verifier = CompletionVerifier(ai)
        ai.chat.return_value = json.dumps(
            {"complete": True, "confidence": 0.9, "evidence": "详情已显示"}
        )
        self.assertTrue(verifier.verify(step, None, page))
        ai.chat.return_value = json.dumps(
            {"complete": True, "confidence": 0.5, "evidence": "不确定"}
        )
        self.assertFalse(verifier.verify(step, None, page))
        ai.chat.return_value = "not json"
        self.assertIsNone(verifier.verify(step, None, page))

    def test_video_and_order_detail_use_the_same_completion_flow(self):
        cases = (
            (
                "灵境行者继续观看",
                "https://v.qq.com/biu/u/history/",
                "灵境行者第01集",
                "https://v.qq.com/x/cover/program/episode.html",
                "播放控件，10:25 / 27:18",
                ("url_contains:/x/vid_", "text_contains:灵境行者"),
            ),
            (
                "查看订单 42 详情",
                "https://example.com/orders/",
                "订单 42",
                "https://example.com/order-detail/42",
                "订单 42，状态已发货",
                ("url_contains:/orders/detail/",),
            ),
        )
        for goal, entry_url, target, result_url, result_text, criteria in cases:
            with self.subTest(goal=goal):
                before = Observation(
                    entry_url, "", target, "", False, ({"ref": "@e1", "text": target},)
                )
                after = Observation(result_url, "", result_text, "", True)
                step = Step(
                    "s1", goal, ({"type": "goal_state", "value": goal},), start_url=entry_url
                )
                ai = Mock()
                ai.chat.return_value = json.dumps(
                    {"complete": True, "confidence": 0.95, "evidence": "目标结果已在当前页面显示"}
                )
                ai.chat.side_effect = [
                    json.dumps(
                        {
                            "complete": True,
                            "confidence": 0.95,
                            "evidence": "目标结果已在当前页面显示",
                        }
                    ),
                    json.dumps(
                        {"complete": True, "confidence": 0.95, "evidence": "原始目标和步骤证据一致"}
                    ),
                ]
                decision = Mock()
                decision.choose.return_value = DecisionResult(
                    (Action("click", ref="@e1"),), 1.0, "test"
                )
                executor = RecordingExecutor()
                with redirect_stdout(io.StringIO()):
                    summary = Orchestrator(
                        Mock(),
                        Plan("ready", True, steps=(step,)),
                        # 动作后验收一次，最终任务验收一次；观察无需重复抓取。
                        observer=SequenceObserver(before, after),
                        executor=executor,
                        decision=decision,
                        completion=CompletionVerifier(ai),
                        memory=Mock(),
                        budget=Budget(max_steps=4),
                    ).run()
                self.assertEqual(summary.status, "completed")
                self.assertEqual([action.kind for action in executor.actions], ["click"])
                self.assertEqual(ai.chat.call_count, 2)
                final_payload = json.loads(ai.chat.call_args_list[0].args[1])
                self.assertEqual(final_payload["acted_on"], target)
                self.assertEqual(final_payload["current"]["url"], result_url)

    def test_planned_text_on_entry_page_does_not_override_goal_verdict(self):
        entry_url = "https://example.com/history/"
        before = Observation(
            entry_url, "", "节目 A", "", False, ({"ref": "@e1", "text": "节目 A"},)
        )
        after = Observation("https://example.com/watch/a", "", "播放页面", "", True)
        step = Step("s1", "打开节目 A 继续观看", ("text_contains:节目 A",), start_url=entry_url)
        ai = Mock()
        ai.chat.side_effect = [
            json.dumps({"complete": True, "confidence": 0.9, "evidence": "已进入节目播放页"}),
            json.dumps(
                {"complete": True, "confidence": 0.9, "evidence": "继续观看的原始目标已满足"}
            ),
        ]
        decision = Mock()
        decision.choose.return_value = DecisionResult((Action("click", ref="@e1"),), 1.0, "test")
        executor = RecordingExecutor()
        with redirect_stdout(io.StringIO()):
            summary = Orchestrator(
                Mock(),
                Plan("ready", True, steps=(step,)),
                observer=SequenceObserver(before, after, after),
                executor=executor,
                decision=decision,
                completion=CompletionVerifier(ai),
                memory=Mock(),
                budget=Budget(max_steps=4),
            ).run()
        self.assertEqual(summary.status, "completed")
        self.assertEqual(len(executor.actions), 1)

    def test_repeated_page_cycle_stops_instead_of_exhausting_budget(self):
        list_url = "https://example.com/list"
        detail_url = "https://example.com/detail"
        listing = Observation(list_url, "", "项目 A", "", True, ({"ref": "@e1", "text": "项目 A"},))
        detail = Observation(detail_url, "", "详情仍未加载", "", True)
        detail_again = Observation(detail_url + "?attempt=2", "", "详情仍未加载", "", True)
        observer = SequenceObserver(listing, detail, listing, detail, listing, detail_again)
        ai = Mock()
        ai.chat.return_value = json.dumps(
            {"complete": False, "confidence": 0.9, "evidence": "当前目标尚未确认"}
        )
        decision = Mock()
        decision.choose.side_effect = lambda _goal, page, *_args, **_kwargs: DecisionResult(
            (
                Action("click", ref="@e1")
                if page.url == list_url
                else Action("open", value=list_url),
            ),
            1.0,
            "test",
        )
        executor = RecordingExecutor()
        step = Step("s1", "查看项目 A 的状态", ("text_contains:已完成",), start_url=list_url)
        with redirect_stdout(io.StringIO()):
            summary = Orchestrator(
                Mock(),
                Plan("ready", True, steps=(step,)),
                observer=observer,
                executor=executor,
                decision=decision,
                completion=CompletionVerifier(ai),
                memory=Mock(),
                budget=Budget(max_steps=8),
            ).run()
        self.assertIn("页面往返循环", summary.summary)
        self.assertEqual(len(executor.actions), 5)


if __name__ == "__main__":
    unittest.main()
