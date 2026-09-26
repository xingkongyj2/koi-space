import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from koi_agent import protocol


class ProtocolLogTests(unittest.TestCase):
    def test_full_diagnostics_and_events_are_saved_without_polluting_stdout(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "log" / "agent.jsonl"
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch.object(protocol, "LOG_PATH", path), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                message = "完整日志\n" + "x" * 10000
                protocol.log(message)
                protocol.done("完成", 2)
            entries = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(entries[0]["message"], message)
            self.assertEqual(entries[1]["event"], json.loads(stdout.getvalue()))
            self.assertEqual(stderr.getvalue(), "")

    def test_log_failure_does_not_break_event_protocol(self):
        with patch.object(Path, "mkdir", side_effect=PermissionError("read only")), contextlib.redirect_stdout(io.StringIO()) as stdout, contextlib.redirect_stderr(io.StringIO()) as stderr:
            protocol.done("完成", 0)
        self.assertEqual(json.loads(stdout.getvalue())["type"], "done")
        self.assertIn("cannot write", stderr.getvalue())

    def test_log_directory_is_python_root(self):
        self.assertEqual(protocol.LOG_DIR, Path(protocol.__file__).resolve().parent.parent / "log")
