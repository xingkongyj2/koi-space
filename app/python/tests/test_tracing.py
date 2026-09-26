"""Trace fidelity and correlation across the model and execution pipeline."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from koi_agent import protocol
from koi_agent.config import Provider
from koi_agent.decision import Decision
from koi_agent.models import JevDecision, ModelError, OpenAICompatible
from koi_agent.observer import Observation
from koi_agent.orchestrator import Orchestrator
from koi_agent.planner import Planner
from koi_agent.reflection import Reflection
from test_navigation_flow import FakeSession, navigation_plan


class Response(io.BytesIO):
    status = 200


class TracingTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "trace.jsonl"
        patcher = patch.object(protocol, "LOG_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        token = protocol._CONTEXT.set({"session_id": "test-session"})
        self.addCleanup(protocol._CONTEXT.reset, token)
        self.provider = Provider("test", "https://example.com/v1", "secret-not-for-logs", "test-model")

    def records(self, stage):
        return [entry for entry in map(json.loads, self.path.read_text().splitlines()) if entry.get("stage") == stage]

    def test_model_raw_extracted_and_normalized_plan_are_distinct(self):
        plan = navigation_plan().to_dict()
        text = json.dumps(plan)
        raw = json.dumps({"output": [{"type": "message", "content": [{"text": text}]}], "padding": "x" * 16000})
        with patch("urllib.request.urlopen", return_value=Response(raw.encode())):
            result = Planner(ai=OpenAICompatible(self.provider)).plan("open site")
        self.assertEqual(self.records("model.response.raw")[0]["body"], raw)
        self.assertEqual(self.records("model.responses.end")[0]["result"], text)
        self.assertEqual(self.records("planner.json.decoded")[0]["data"], plan)
        self.assertEqual(self.records("planner.parse.end")[0]["result"]["steps"][0]["success_criteria"], list(result.steps[0].success_criteria))
        self.assertNotIn(self.provider.api_key, self.path.read_text())

    def test_system_instructions_are_separate_from_complete_model_input(self):
        with patch("urllib.request.urlopen", return_value=Response(b'{"output_text":"ok"}')) as request:
            self.assertEqual(OpenAICompatible(self.provider).chat("system rules", "complete history"), "ok")
        body = json.loads(request.call_args.args[0].data)
        self.assertEqual(body["instructions"], "system rules")
        self.assertEqual(body["input"], "complete history")
        self.assertFalse(body["enable_thinking"])

    def test_malformed_response_keeps_raw_body_and_traceback(self):
        with patch("urllib.request.urlopen", return_value=Response(b"not json")):
            with self.assertRaises(ModelError):
                OpenAICompatible(self.provider).chat("system", "task")
        self.assertEqual(self.records("model.response.raw")[0]["body"], "not json")
        self.assertIn("JSONDecodeError", self.records("model.http.error")[0]["traceback"])
        self.assertNotIn("call_id", protocol._CONTEXT.get())

    def test_http_failure_body_is_preserved(self):
        error = HTTPError("https://example.com", 429, "rate limited", {}, io.BytesIO(b'{"error":"retry later"}'))
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(ModelError):
                OpenAICompatible(self.provider).chat("system", "task")
        self.assertEqual(self.records("model.response.http_error")[0]["status"], 429)
        self.assertIn("retry later", self.records("model.response.http_error")[0]["body"])

    def test_quota_rejection_reports_actionable_cause(self):
        body = b'{"error":{"code":"PERMISSION_DENIED","message":"Free quota exhausted. Disable use free tier only."}}'
        error = HTTPError("https://example.com", 403, "Forbidden", {}, io.BytesIO(body))
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaisesRegex(ModelError, "免费额度已耗尽") as raised:
                OpenAICompatible(self.provider).chat("system", "task")
        self.assertIn("HTTP 403", str(raised.exception))
        self.assertIn("PERMISSION_DENIED", str(raised.exception))

    def test_jev_mapping_and_decision_filtering_are_visible(self):
        observation = Observation("https://example.com", "", "page", "", False, ({"ref": "@e1", "text": "button"},))
        raw = {"answers": {"operation": {"choice": "CLICK", "confidence": 0.9}, "click_target": {"choice": "1"}}}
        with patch("urllib.request.urlopen", return_value=Response(json.dumps(raw).encode())):
            decision = Decision(jev=JevDecision(self.provider)).choose("click", observation)
        self.assertEqual(self.records("model.jev.end")[0]["result"]["target"], "@e1")
        self.assertEqual(decision.actions[0].ref, "@e1")
        value = {"actions": [{"kind": "unsupported"}, {"kind": "click", "ref": "@e1"}]}
        ai = Mock()
        ai.chat.return_value = json.dumps(value)
        Decision(ai=ai).choose("click", observation)
        self.assertEqual(json.loads(ai.chat.call_args.args[1])["goal"], "click")
        self.assertEqual(len(self.records("decision.model.parsed")[0]["data"]["actions"]), 2)
        self.assertEqual(len(self.records("decision.choose.end")[-1]["result"]["actions"]), 1)

    def test_execution_layers_share_step_and_iteration(self):
        Orchestrator(FakeSession(), navigation_plan()).run()
        execute = self.records("executor.execute.start")[0]
        for stage in ("observer.capture.end", "decision.choose.end", "executor.command", "validator.action.end", "validator.step.end"):
            entry = self.records(stage)[0]
            self.assertEqual(entry["session_id"], "test-session")
            self.assertEqual(entry["step_id"], "s1")
            self.assertEqual(entry["iteration"], 1)
        self.assertEqual(self.records("executor.execute.end")[0]["call_id"], execute["call_id"])
        self.assertNotIn("step_id", protocol._CONTEXT.get())

    def test_caught_decision_failure_logs_fallback(self):
        observation = Observation("", "", "", "", False)
        ai = Mock()
        ai.chat.return_value = "invalid json"
        result = Decision(ai=ai).choose("test", observation)
        self.assertEqual(result.route, "none")
        self.assertEqual(self.records("decision.model.raw")[0]["data"], "invalid json")
        self.assertIn("JSONDecodeError", self.records("decision.model.fallback")[0]["traceback"])

    def test_reflection_calls_the_same_ai_method(self):
        ai = Mock()
        ai.chat.return_value = "重新观察按钮"
        observation = Observation("", "", "当前页面", "", False)
        self.assertEqual(Reflection(ai=ai).advise("点击按钮", observation, "no-op"), "重新观察按钮")
        self.assertEqual(json.loads(ai.chat.call_args.args[1]), {"goal": "点击按钮", "snapshot": "当前页面", "error": "no-op"})
