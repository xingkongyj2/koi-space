"""Full history survives process turns and includes validated runtime progress."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from koi_agent import __main__ as entrypoint, protocol
from koi_agent.budget import Budget
from koi_agent.config import Provider, Settings
from koi_agent.history import task_history
from koi_agent.observer import Observation
from koi_agent.orchestrator import Orchestrator
from koi_agent.planner import Plan, Step
from koi_agent.executor import Action, Executor
from test_navigation_flow import FakeSession, navigation_plan


class TaskHistoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        patcher = patch.object(protocol, "LOG_PATH", Path(temporary.name) / "agent.jsonl")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.settings = Settings(Provider("planner", "", "", ""), Provider("decision", "", "", ""))

    def run_turn(self, user_input, events, plan):
        received = []

        def plan_with_history(prompt, *, history):
            received.append((prompt, deepcopy(history)))
            return plan

        task = {"sessionId": "test", "userInput": user_input, "history": events}
        with patch.object(entrypoint, "read_task", return_value=task), \
             patch.object(entrypoint, "load_settings", return_value=self.settings), \
             patch.object(entrypoint.Planner, "plan", side_effect=plan_with_history), \
             patch.object(entrypoint.browser, "BrowserSession") as session, \
             redirect_stdout(io.StringIO()) as output:
            self.assertEqual(entrypoint.main(), 0)
        session.assert_not_called()
        emitted = [json.loads(line) for line in output.getvalue().splitlines()]
        return received[0], emitted

    def test_multiple_clarifications_keep_every_prior_turn_without_clipping(self):
        original = "查看最近两个采购订单，并返回状态"
        history = [{"type": "user_input", "text": original}]
        for prompt, question in (
            (original, "这些订单在哪个网站？"),
            ("公司的采购网站", "请提供采购网站的完整网址。"),
            ("https://orders.example.com/", "需要查看哪个账号下的订单？"),
        ):
            if history[-1].get("text") != prompt:
                history.append({"type": "user_input", "text": prompt})
            expected = deepcopy(history)
            received, emitted = self.run_turn(prompt, history, Plan("ask", False, question=question))
            self.assertEqual(received, (prompt, expected))
            self.assertEqual(history, expected)
            persisted_plan = json.loads(emitted[0]["text"])
            self.assertEqual(persisted_plan["kind"], "planner_plan")
            self.assertEqual(persisted_plan["plan"]["question"], question)
            self.assertEqual(emitted[-1]["summary"], question)
            history.extend(emitted)
        long_result = "已读取完整执行结果：" + "数据" * 20000
        history.append({"type": "tool_result", "name": "inspect", "ok": True, "preview": long_result, "ms": 1})
        history.append({"type": "error", "message": "登录已失效"})
        history.append({"type": "user_input", "text": "继续"})
        received, _ = self.run_turn("继续", history, Plan("ask", False, question="请完成登录后继续。"))
        self.assertEqual(received[1], history)
        self.assertEqual(received[1][0]["text"], original)
        self.assertEqual(received[1][-3]["preview"], long_result)

    def test_standalone_input_and_repeated_identical_turns_are_preserved(self):
        self.assertEqual(task_history({}, "继续"), [{"type": "user_input", "text": "继续"}])
        history = [{"type": "user_input", "text": "继续"}]
        self.assertEqual(task_history({"history": history}, "继续"), history)
        history.append({"type": "done", "summary": "请提供网址", "iterations": 0})
        restored = task_history({"history": history}, "继续")
        self.assertEqual(restored, history + [{"type": "user_input", "text": "继续"}])

    def test_invalid_history_fails_instead_of_forgetting_earlier_turns(self):
        with patch.object(entrypoint, "read_task", return_value={"userInput": "继续", "history": "invalid"}), \
             patch.object(entrypoint.Planner, "plan") as planner, \
             redirect_stdout(io.StringIO()) as output:
            self.assertEqual(entrypoint.main(), 0)
        planner.assert_not_called()
        self.assertIn("history", json.loads(output.getvalue())["message"])

    def test_in_run_history_captures_all_events_and_is_isolated(self):
        history = []
        args = {"values": ["original"]}
        with redirect_stdout(io.StringIO()), protocol.capture_events(history):
            protocol.tool_call("inspect", args, 1)
            protocol.tool_result("inspect", True, "result", 1)
            protocol.notify("需要更多信息")
            protocol.error("failure")
            protocol.done("complete", 1)
        args["values"].append("mutated")
        with redirect_stdout(io.StringIO()):
            protocol.thinking("another task")
        self.assertEqual([event["type"] for event in history], ["tool_call", "tool_result", "notify", "error", "done"])
        self.assertEqual(history[0]["args"], {"values": ["original"]})

    def test_step_completion_is_available_to_the_next_planning_turn(self):
        step = Step("s1", "打开订单网站", ("url_prefix:https://orders.example.com/",), start_url="https://orders.example.com/")
        plan = Plan("ready", True, steps=(step,))
        observer = Mock()
        observer.capture.return_value = Observation(step.start_url, "订单", "", "", False)
        history = []
        with redirect_stdout(io.StringIO()), protocol.capture_events(history):
            result = Orchestrator(Mock(), plan, observer=observer, decision=Mock(), memory=Mock()).run()
        progress = [json.loads(event["text"]) for event in history]
        self.assertIn("任务完成", result)
        self.assertEqual([event["status"] for event in progress], ["started", "completed"])
        self.assertEqual(progress[-1]["completed_steps"], ["s1"])
        self.assertEqual(progress[-1]["current_url"], step.start_url)
        received, _ = self.run_turn("接下来查看订单", history, Plan("ask", False, question="查看哪个订单？"))
        self.assertEqual(received[1][:-1], history)

    def test_failed_step_persists_budget_and_reason(self):
        step = Step("s1", "打开订单网站", ("url_prefix:https://orders.example.com/",), start_url="https://orders.example.com/")
        history = []
        with redirect_stdout(io.StringIO()), protocol.capture_events(history):
            result = Orchestrator(Mock(), Plan("ready", True, steps=(step,)), budget=Budget(max_steps=0), memory=Mock()).run()
        failure = json.loads(history[-1]["text"])
        self.assertIn("失败", result)
        self.assertEqual(failure["status"], "failed")
        self.assertEqual(failure["budget"]["remaining_steps"], 0)
        self.assertEqual(failure["completed_steps"], [])

    def test_real_execution_and_observations_reach_next_planning_turn(self):
        history = []
        with redirect_stdout(io.StringIO()), protocol.capture_events(history):
            result = Orchestrator(FakeSession(), navigation_plan(), memory=Mock()).run()
        self.assertIn("任务完成", result)
        calls = [event for event in history if event["type"] == "tool_call"]
        results = [event for event in history if event["type"] == "tool_result"]
        observations = [json.loads(event["text"]) for event in history
                        if event["type"] == "thinking" and json.loads(event["text"])["kind"] == "observation"]
        self.assertEqual(calls[0]["name"], "browser.open")
        self.assertEqual(calls[0]["args"]["value"], "https://v.qq.com/")
        self.assertTrue(results[0]["ok"])
        self.assertIn("Play", observations[0]["diff"])
        self.assertEqual(observations[-1]["url"], "https://v.qq.com/")
        received, _ = self.run_turn("继续", history, Plan("ask", True, question="接下来做什么？"))
        self.assertEqual(received[1][:-1], history)

    def test_real_action_failure_is_recorded(self):
        session = Mock()
        session.run.side_effect = RuntimeError("page closed")
        history = []
        with redirect_stdout(io.StringIO()), protocol.capture_events(history):
            with self.assertRaises(RuntimeError):
                Executor(session).execute(Action("click", ref="@e1"))
        self.assertEqual(history[-1]["type"], "tool_result")
        self.assertFalse(history[-1]["ok"])
        self.assertEqual(history[-1]["preview"], "page closed")


if __name__ == "__main__":
    unittest.main()
