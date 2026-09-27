import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import protocol.protocol as protocol
from logger import logger


class ProtocolLogTests(unittest.TestCase):
    def test_full_diagnostics_and_events_are_saved_without_polluting_stdout(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "logger" / "agent.log"
            stdout, stderr = io.StringIO(), io.StringIO()
            with (
                patch.object(logger, "LOG_PATH", path),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                message = "完整日志\n" + "x" * 10000
                logger.debug(message)
                protocol.done("完成", 2)
            self.assertEqual(json.loads(stdout.getvalue())["summary"], "完成")
            self.assertEqual(stderr.getvalue(), "")
            readable = path.read_text()
            self.assertNotIn(message, readable)
            self.assertIn('"总结": "完成"', readable)
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_readable_model_json_and_nested_multiline_fields(self):
        entry = {
            "time": "2026-09-26",
            "kind": "trace",
            "stage": "model.response.raw",
            "session_id": "s1",
            "body": '{"actions":[{"kind":"click"}]}',
            "inputs": {"snapshot": "line one\nline two"},
        }
        formatted = logger._format_readable(entry)
        self.assertIn("任务编号=s1", formatted)
        self.assertIn('--- 请求体 ---\n{\n  "actions": [', formatted)
        self.assertIn("--- 输入.网页交互内容 ---\nline one\nline two", formatted)

    def test_log_failure_does_not_break_event_protocol(self):
        with (
            patch.object(Path, "mkdir", side_effect=PermissionError("read only")),
            contextlib.redirect_stdout(io.StringIO()) as stdout,
            contextlib.redirect_stderr(io.StringIO()) as stderr,
        ):
            protocol.done("完成", 0)
        self.assertEqual(json.loads(stdout.getvalue())["type"], "done")
        self.assertIn("cannot write", stderr.getvalue())

    def test_log_directory_is_logger_package(self):
        self.assertEqual(logger.LOG_DIR, Path(logger.__file__).resolve().parent)

    def test_layer_logs_keep_page_summary_without_snapshot(self):
        formatted = logger._format_readable(
            {
                "time": "2026-09-26",
                "kind": "layer",
                "stage": "observer.capture",
                "layer": "观察层 · 页面摘要",
                "result": {
                    "url": "https://example.com",
                    "snapshot": '- button "Play" [ref=e1]',
                    "page_text": "完整网页正文",
                    "elements": [{"ref": "@e1", "text": "Play"}],
                    "element_count": 1,
                },
            }
        )
        self.assertIn('"交互元素数": 1', formatted)
        self.assertNotIn("Play", formatted)
        self.assertNotIn("完整网页正文", formatted)
