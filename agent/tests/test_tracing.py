"""Trace fidelity and correlation across the model and execution pipeline."""

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from config.config import Provider
from llm.models import JevDecision, ModelError, OpenAICompatible
from logger import log
from planner.planner import Planner
from react.decision import Decision
from react.observer import Observation
from react.orchestrator import Orchestrator
from reflection.reflection import Reflection
from test_navigation_flow import FakeSession, navigation_plan


class Response(io.BytesIO):
    status = 200


class TracingTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "trace.log"
        self.entries = []
        original = log._record

        def record(kind, **data):
            self.entries.append({**log._CONTEXT.get(), "kind": kind, **data})
            original(kind, **data)

        recorder = patch.object(log, "_record", side_effect=record)
        recorder.start()
        self.addCleanup(recorder.stop)
        patcher = patch.object(log, "LOG_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        token = log._CONTEXT.set({"session_id": "test-session"})
        self.addCleanup(log._CONTEXT.reset, token)
        self.provider = Provider(
            "test", "https://example.com/v1", "secret-not-for-logs", "test-model"
        )

    def records(self, stage):
        return [entry for entry in self.entries if entry.get("stage") == stage]

    def test_model_request_and_final_plan_share_one_block(self):
        plan = navigation_plan().to_dict()
        text = json.dumps(plan)
        raw = json.dumps(
            {
                "output": [{"type": "message", "content": [{"text": text}]}],
                "padding": "x" * 16000,
            }
        )
        with patch("urllib.request.urlopen", return_value=Response(raw.encode())):
            result = Planner(ai=OpenAICompatible(self.provider)).plan("open site")
        entry = self.records("planner.plan")[0]
        self.assertEqual(entry["requests"][0]["result"], text)
        self.assertEqual(entry["result"].steps, result.steps)
        self.assertEqual(len(self.entries), 1)
        self.assertNotIn("padding", self.path.read_text())
        self.assertNotIn(self.provider.api_key, self.path.read_text())

    def test_both_planning_requests_share_input_output_and_timing_blocks(self):
        entry_text = '{"status":"ready","url":"https://example.com/","question":""}'
        plan_text = json.dumps(navigation_plan().to_dict())
        responses = [
            Response(json.dumps({"output_text": text}).encode())
            for text in (entry_text, plan_text)
        ]
        planner = Planner(OpenAICompatible(self.provider))
        with patch("urllib.request.urlopen", side_effect=responses):
            planner.locate_entry("open site")
            planner.plan("open site")
        self.assertEqual(len(self.entries), 2)
        for entry, expected in zip(self.entries, (entry_text, plan_text)):
            self.assertEqual(len(entry["requests"]), 1)
            request = entry["requests"][0]
            self.assertEqual(request["result"], expected)
            self.assertIn("open site", request["request"]["body"]["input"])
            self.assertGreaterEqual(entry["request_timings"][0]["ms"], 0)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_system_instructions_are_separate_from_complete_model_input(self):
        with patch(
            "urllib.request.urlopen", return_value=Response(b'{"output_text":"ok"}')
        ) as request:
            self.assertEqual(
                OpenAICompatible(self.provider).chat(
                    "system rules", "complete history"
                ),
                "ok",
            )
        body = json.loads(request.call_args.args[0].data)
        self.assertEqual(body["instructions"], "system rules")
        self.assertEqual(body["input"], "complete history")
        self.assertFalse(body["enable_thinking"])

    def test_malformed_response_keeps_body_and_error(self):
        with patch("urllib.request.urlopen", return_value=Response(b"not json")):
            with self.assertRaises(ModelError):
                OpenAICompatible(self.provider).chat("system", "task")
        self.assertEqual(self.records("model.responses")[0]["response"], "not json")
        self.assertIn("not json", self.path.read_text())
        self.assertIn("error", self.records("model.responses")[0])
        self.assertNotIn("call_id", log._CONTEXT.get())

    def test_http_failure_body_is_preserved(self):
        error = HTTPError(
            "https://example.com",
            429,
            "rate limited",
            {},
            io.BytesIO(b'{"error":"retry later"}'),
        )
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(ModelError):
                OpenAICompatible(self.provider).chat("system", "task")
        self.assertEqual(self.records("model.responses")[0]["response"]["status"], 429)
        self.assertIn(
            "retry later", self.records("model.responses")[0]["response"]["body"]
        )

    def test_quota_rejection_reports_actionable_cause(self):
        body = b'{"error":{"code":"PERMISSION_DENIED","message":"Free quota exhausted. Disable use free tier only."}}'
        error = HTTPError("https://example.com", 403, "Forbidden", {}, io.BytesIO(body))
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaisesRegex(ModelError, "免费额度已耗尽") as raised:
                OpenAICompatible(self.provider).chat("system", "task")
        self.assertIn("HTTP 403", str(raised.exception))
        self.assertIn("PERMISSION_DENIED", str(raised.exception))

    def test_jev_mapping_and_decision_filtering_are_visible(self):
        observation = Observation(
            "https://example.com",
            "",
            "page",
            "",
            False,
            ({"ref": "@e1", "text": "button"},),
        )
        raw = {
            "answers": {
                "operation": {"choice": "CLICK", "confidence": 0.9},
                "click_target": {"choice": "1"},
            }
        }
        with patch(
            "urllib.request.urlopen", return_value=Response(json.dumps(raw).encode())
        ):
            decision = Decision(jev=JevDecision(self.provider)).choose(
                "click", observation
            )
        self.assertEqual(
            self.records("decision.choose")[0]["requests"][0]["result"]["target"], "@e1"
        )
        self.assertEqual(decision.actions[0].ref, "@e1")
        value = {"actions": [{"kind": "unsupported"}, {"kind": "click", "ref": "@e1"}]}
        ai = Mock()
        ai.chat.return_value = json.dumps(value)
        Decision(ai=ai).choose("click", observation)
        self.assertEqual(json.loads(ai.chat.call_args.args[1])["goal"], "click")
        self.assertEqual(len(self.records("decision.choose")[-1]["result"].actions), 1)

    def test_execution_layers_share_step_and_iteration(self):
        Orchestrator(FakeSession(), navigation_plan()).run()
        execute = self.records("executor.execute")[0]
        for stage in (
            "observer.capture",
            "decision.choose",
            "executor.execute",
            "validator.action",
            "validator.step",
        ):
            entry = self.records(stage)[0]
            self.assertEqual(entry["session_id"], "test-session")
            self.assertEqual(entry["step_id"], "s1")
            self.assertEqual(entry["iteration"], 1)
        self.assertEqual(
            self.records("executor.execute")[0]["call_id"], execute["call_id"]
        )
        self.assertNotIn("step_id", log._CONTEXT.get())

    def test_caught_decision_failure_logs_fallback(self):
        observation = Observation("", "", "", "", False)
        ai = Mock()
        ai.chat.return_value = "invalid json"
        result = Decision(ai=ai).choose("test", observation)
        self.assertEqual(result.route, "none")
        self.assertEqual(
            self.records("decision.choose")[0]["errors"][0]["stage"],
            "decision.model.fallback",
        )

    def test_reflection_calls_the_same_ai_method(self):
        ai = Mock()
        ai.chat.return_value = "重新观察按钮"
        observation = Observation("", "", "当前页面", "", False)
        self.assertEqual(
            Reflection(ai=ai).advise("点击按钮", observation, "no-op"), "重新观察按钮"
        )
        self.assertEqual(
            json.loads(ai.chat.call_args.args[1]),
            {"goal": "点击按钮", "snapshot": "当前页面", "error": "no-op"},
        )
