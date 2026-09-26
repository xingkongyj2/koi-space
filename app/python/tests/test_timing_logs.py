"""Readable timing rows cover model planning, Jev and browser commands."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from koi_agent import protocol
from koi_agent.config import Provider
from koi_agent.executor import Action, Executor
from koi_agent.models import JevDecision
from koi_agent.observer import Observation
from koi_agent.planner import Planner


class TimingLogTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.log_path = Path(temporary.name) / "agent.jsonl"
        patcher = patch.object(protocol, "LOG_PATH", self.log_path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def timings(self):
        return [json.loads(line) for line in self.log_path.read_text().splitlines()
                if json.loads(line).get("kind") == "timing"]

    def test_planner_requests_have_separate_prominent_durations(self):
        ai = Mock()
        ai.chat.side_effect = [
            '{"status":"ready","url":"https://example.com/","question":""}',
            json.dumps({
                "status": "ask", "needs_browser": True, "question": "请提供账号",
                "direct_answer": "", "steps": [],
            }),
        ]
        planner = Planner(ai)
        planner.locate_entry("打开 example.com")
        planner.plan("打开 example.com")

        rows = self.timings()
        self.assertEqual([row["stage"] for row in rows], [
            "planning.entry.model_request", "planning.full.model_request",
            "planning.full.total",
        ])
        self.assertTrue(all(row["ms"] >= 0 and row["since_start_ms"] >= 0 for row in rows))
        self.assertEqual([row["attempt"] for row in rows[:2]], [1, 1])
        readable = self.log_path.with_suffix(".log").read_text()
        self.assertEqual(readable.count("[TIMING]"), 3)
        self.assertIn("planning.full.model_request", readable)
        timing_only = self.log_path.with_suffix(".timing.log").read_text()
        self.assertEqual(timing_only.count("[TIMING]"), 3)
        self.assertNotIn("planner.plan.start", timing_only)

    def test_jev_and_click_have_separate_durations(self):
        provider = Provider("decision", "https://api.example.com", "jev", "key")
        observation = Observation("https://example.com/", "", "- button History [ref=e1]",
                                  "", False, ({"ref": "@e1", "text": "button History"},))
        response = {"answers": {
            "operation": {"choice": "CLICK", "confidence": 0.9},
            "click_target": {"choice": "1"},
        }}
        with patch("koi_agent.models._request_json", return_value=response):
            choice = JevDecision(provider).choose("打开历史记录", observation)
        self.assertEqual(choice["target"], "@e1")

        session = Mock()
        session.run.return_value = Mock(ok=True, preview="clicked")
        with patch.object(protocol, "tool_call"), patch.object(protocol, "tool_result"):
            Executor(session).execute(Action("click", ref="@e1"))

        rows = {row["stage"]: row for row in self.timings()}
        self.assertIn("decision.jev.request", rows)
        self.assertIn("decision.jev.total", rows)
        self.assertEqual(rows["browser.action.command"]["action"], "click")
        self.assertEqual(rows["browser.action.command"]["target"], "@e1")


if __name__ == "__main__":
    unittest.main()
