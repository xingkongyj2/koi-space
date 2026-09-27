"""Verify default app routing and complete-task waiting without live AI calls."""

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import debug_task
from koi_agent import protocol
from koi_agent.config import Provider, Settings


class DebugTaskTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.profile = Path(temporary.name)
        patcher = patch.object(protocol, "LOG_PATH", self.profile / "debug.jsonl")
        patcher.start()
        self.addCleanup(patcher.stop)
        token = protocol._CONTEXT.set({})
        self.addCleanup(protocol._CONTEXT.reset, token)

    def test_default_routes_to_app_without_local_browser_or_model(self):
        with (
            patch.object(debug_task, "find_app", return_value=(self.profile, {})),
            patch.object(debug_task, "run_app_task", return_value=0) as run,
            patch.object(debug_task, "test_browser") as browser,
            patch.object(debug_task, "load_settings") as settings,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(debug_task.main(["全部流程"]), 0)
        self.assertEqual(run.call_args.args[0], "全部流程")
        browser.assert_not_called()
        settings.assert_not_called()

    def test_missing_app_does_not_silently_launch_browser(self):
        with (
            patch.object(debug_task, "find_app", side_effect=RuntimeError("应用未启动")),
            patch.object(debug_task, "test_browser") as browser,
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(debug_task.main([]), 1)
        browser.assert_not_called()

    def test_explicit_existing_browser_runs_python_directly(self):
        settings = Settings(
            Provider("planner", "https://example.com", "key", "model"),
            Provider("decision", "", "", ""),
        )
        with (
            patch.object(debug_task, "find_app") as app,
            patch.object(debug_task, "load_settings", return_value=settings),
            patch.object(debug_task.browser, "resolve_cli", return_value="agent-browser"),
            patch.object(debug_task, "run_task", return_value=0) as run,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(
                debug_task.main(["--cdp-port", "9222", "--target-id", "target", "全部流程"]), 0
            )
        app.assert_not_called()
        self.assertEqual(run.call_args.args[2:], (9222, "target"))

    def test_discovery_checks_health_and_skips_stale_profile(self):
        stale, live = self.profile / "stale", self.profile / "live"
        for directory in (stale, live):
            directory.mkdir()
            (directory / "local-task-server.json").write_text(
                json.dumps({"url": "http://127.0.0.1:1234", "token": "test"})
            )
        with (
            patch.object(debug_task, "profile_candidates", return_value=[stale, live]),
            patch.object(debug_task, "app_request", side_effect=[OSError("offline"), {"ok": True}]),
        ):
            self.assertEqual(debug_task.find_app()[0], live)

    def test_app_submission_waits_and_prints_complete_result(self):
        path = self.profile / "sessions.db"
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript(
                "CREATE TABLE sessions (id TEXT, status TEXT, error TEXT); CREATE TABLE session_events (session_id TEXT, seq INTEGER, payload TEXT);"
            )
            db.execute("INSERT INTO sessions VALUES ('task-test', 'running', NULL)")
            db.execute(
                "INSERT INTO session_events VALUES ('task-test', 0, ?)",
                (json.dumps({"type": "thinking", "text": "规划中"}),),
            )

        def finish(_):
            with closing(sqlite3.connect(path)) as db, db:
                db.execute(
                    "INSERT INTO session_events VALUES ('task-test', 1, ?)",
                    (json.dumps({"type": "done", "summary": "完整流程结束", "iterations": 1}),),
                )
                db.execute("UPDATE sessions SET status = 'idle'")

        with (
            patch.object(
                debug_task,
                "app_request",
                return_value={"ok": True, "started": True, "id": "task-test"},
            ) as submit,
            patch.object(debug_task.time, "sleep", side_effect=finish),
            patch.object(debug_task, "print_task_log") as logs,
            redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(debug_task.run_app_task("全部流程", self.profile, {}, 10), 0)
        self.assertEqual(submit.call_args.args[2], {"userInput": "全部流程", "engine": "python"})
        self.assertEqual(output.getvalue().count("规划中"), 1)
        self.assertIn("完整流程结束", output.getvalue())
        logs.assert_called_once()

    def test_api_token_not_sent_to_remote_address(self):
        with self.assertRaises(ValueError), patch("urllib.request.urlopen") as request:
            debug_task.app_request(
                {"url": "https://remote.example.com", "token": "secret"}, "/health"
            )
        request.assert_not_called()
