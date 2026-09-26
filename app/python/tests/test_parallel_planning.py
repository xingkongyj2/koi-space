"""The entry page opens while full task planning is still in flight."""
from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from koi_agent import __main__ as entrypoint, protocol
from koi_agent.config import Provider, Settings
from koi_agent.planner import EntryPoint, Plan, Step


class ParallelPlanningTests(unittest.TestCase):
    def test_model_requests_overlap_and_entry_opens_before_plan_finishes(self):
        locator_started = Event()
        planner_started = Event()
        browser_opened = Event()
        plan_finished = Event()
        opened_before_plan = []
        located_url = "https://v.qq.com/"

        def locate_entry(_planner, _user_input, *, history):
            locator_started.set()
            self.assertTrue(planner_started.wait(2), "full planning did not start alongside entry lookup")
            return EntryPoint("ready", url=located_url)

        def plan(_planner, _user_input, *, history, context):
            planner_started.set()
            self.assertTrue(locator_started.wait(2))
            self.assertEqual(context, {"entry_locator": "pending"})
            self.assertTrue(browser_opened.wait(2), "entry page did not open before planning finished")
            plan_finished.set()
            return Plan("ready", True, steps=(Step(
                "s1", "查看历史记录", ("url_prefix:https://wrong.example/",),
                start_url="https://wrong.example/",
            ),))

        class FakeSession:
            def bind(self):
                return "t1"

            def run(self, args, timeout=30):
                self_outer.assertEqual(args, ["open", located_url])
                opened_before_plan.append(not plan_finished.is_set())
                browser_opened.set()
                return SimpleNamespace(ok=True, preview="opened")

        self_outer = self
        settings = Settings(
            Provider("planner", "https://example.com", "test-key", "test-model"),
            Provider("decision", "", "", ""),
        )
        task = {"browser": {"cdpPort": 9222, "targetId": "target-1"}}
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(protocol, "LOG_PATH", Path(temporary) / "agent.jsonl"), \
             patch.object(entrypoint, "load_settings", return_value=settings), \
             patch.object(entrypoint.Planner, "locate_entry", new=locate_entry), \
             patch.object(entrypoint.Planner, "plan", new=plan), \
             patch.object(entrypoint.browser, "log_environment"), \
             patch.object(entrypoint.browser, "BrowserSession", return_value=FakeSession()), \
             patch.object(entrypoint, "Orchestrator") as orchestrator, \
             redirect_stdout(io.StringIO()):
            orchestrator.return_value.run.return_value = "任务完成"
            self.assertEqual(entrypoint.run_task(task, "打开腾讯视频历史记录", "test", []), 0)

        self.assertEqual(opened_before_plan, [True])
        executed_plan = orchestrator.call_args.args[1]
        self.assertEqual(executed_plan.steps[0].start_url, located_url)
        self.assertEqual(executed_plan.steps[0].success_criteria, (f"url_prefix:{located_url}",))

    def test_plan_that_finishes_first_waits_for_the_entry_url(self):
        plan_finished = Event()
        opened_urls = []
        located_url = "https://example.com/actual"

        def locate_entry(_planner, _user_input, *, history):
            self.assertTrue(plan_finished.wait(2))
            return EntryPoint("ready", url=located_url)

        def plan(_planner, _user_input, *, history, context):
            result = Plan("ready", True, steps=(Step(
                "s1", "打开目标页面", ("url_prefix:https://example.com/guess",),
                start_url="https://example.com/guess",
            ),))
            plan_finished.set()
            return result

        class FakeSession:
            def bind(self):
                return "t1"

            def run(self, args, timeout=30):
                opened_urls.append(args[1])
                return SimpleNamespace(ok=True, preview="opened")

        settings = Settings(
            Provider("planner", "https://example.com", "test-key", "test-model"),
            Provider("decision", "", "", ""),
        )
        task = {"browser": {"cdpPort": 9222, "targetId": "target-1"}}
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(protocol, "LOG_PATH", Path(temporary) / "agent.jsonl"), \
             patch.object(entrypoint, "load_settings", return_value=settings), \
             patch.object(entrypoint.Planner, "locate_entry", new=locate_entry), \
             patch.object(entrypoint.Planner, "plan", new=plan), \
             patch.object(entrypoint.browser, "log_environment"), \
             patch.object(entrypoint.browser, "BrowserSession", return_value=FakeSession()), \
             patch.object(entrypoint, "Orchestrator") as orchestrator, \
             redirect_stdout(io.StringIO()):
            orchestrator.return_value.run.return_value = "任务完成"
            self.assertEqual(entrypoint.run_task(task, "打开目标页面", "test", []), 0)

        self.assertEqual(opened_urls, [located_url])
        self.assertEqual(orchestrator.call_args.args[1].steps[0].start_url, located_url)


if __name__ == "__main__":
    unittest.main()
