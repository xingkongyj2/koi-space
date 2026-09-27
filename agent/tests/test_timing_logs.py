"""Readable timing rows cover model planning, Jev and browser commands."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import protocol.protocol as protocol
from config.config import Provider
from llm.models import JevDecision
from logger import logger
from planner.planner import Planner
from react.executor import Action, Executor
from react.observer import Observation


class TimingLogTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.log_path = Path(temporary.name) / "agent.log"
        self.entries = []
        original = logger._record

        def record(kind, **data):
            self.entries.append({"kind": kind, **data})
            original(kind, **data)

        recorder = patch.object(logger, "_record", side_effect=record)
        recorder.start()
        self.addCleanup(recorder.stop)
        patcher = patch.object(logger, "LOG_PATH", self.log_path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_planner_requests_have_separate_prominent_durations(self):
        ai = Mock()
        ai.chat.side_effect = [
            '{"status":"ready","url":"https://example.com/","question":""}',
            json.dumps(
                {
                    "status": "ask",
                    "needs_browser": True,
                    "question": "请提供账号",
                    "direct_answer": "",
                    "steps": [],
                }
            ),
        ]
        planner = Planner(ai)
        planner.locate_entry("打开 example.com")
        planner.plan("打开 example.com")

        self.assertEqual(
            [entry["stage"] for entry in self.entries],
            ["planner.locate_entry", "planner.plan"],
        )
        self.assertEqual(
            [entry["request_timings"][0]["stage"] for entry in self.entries],
            ["planning.entry.model_request", "planning.full.model_request"],
        )
        self.assertTrue(all(entry["ms"] >= 0 for entry in self.entries))
        readable = self.log_path.read_text()
        self.assertIn("规划层 · ①入口定位", readable)
        self.assertIn("规划层 · ②任务拆分", readable)
        self.assertEqual(list(self.log_path.parent.iterdir()), [self.log_path])

    def test_jev_and_click_have_separate_durations(self):
        provider = Provider("decision", "https://api.example.com", "jev", "key")
        observation = Observation(
            "https://example.com/",
            "",
            "- button History [ref=e1]",
            "",
            False,
            ({"ref": "@e1", "text": "button History"},),
        )
        response = {
            "answers": {
                "operation": {"choice": "CLICK", "confidence": 0.9},
                "click_target": {"choice": "1"},
            }
        }
        with patch("llm.models._request_json", return_value=response):
            choice = JevDecision(provider).choose("打开历史记录", observation)
        self.assertEqual(choice["target"], "@e1")

        session = Mock()
        session.run.return_value = Mock(ok=True, preview="clicked")
        with patch.object(protocol, "tool_call"), patch.object(protocol, "tool_result"):
            Executor(session).execute(Action("click", ref="@e1"))

        jev, execute = self.entries
        self.assertEqual(jev["request_timings"][0]["stage"], "decision.jev.request")
        self.assertEqual(jev["result"]["target"], "@e1")
        self.assertEqual(execute["commands"], [["click", "@e1"]])
        self.assertGreaterEqual(execute["ms"], 0)
        readable = self.log_path.read_text()
        for label in ("输入", "输出", "命令", "请求耗时", "耗时", "决策层 · JEV 请求"):
            self.assertIn(label, readable)
        self.assertNotIn('"request_timings"', readable)


if __name__ == "__main__":
    unittest.main()
