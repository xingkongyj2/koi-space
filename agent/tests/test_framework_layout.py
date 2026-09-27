"""验证框架迁移后，入口和配置定位不依赖桌面端工作目录。"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config.config as config


class FrameworkLayoutTests(unittest.TestCase):
    def test_root_entry_runs_from_an_unrelated_directory(self):
        entry = Path(__file__).resolve().parents[1] / "main.py"
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, "-u", str(entry)],
                input="",
                text=True,
                capture_output=True,
                cwd=directory,
                timeout=10,
                env={
                    key: value
                    for key, value in os.environ.items()
                    if key != "PYTHONPATH"
                },
            )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        event = json.loads(result.stdout)
        self.assertEqual(event["type"], "error")
        self.assertIn("no task on stdin", event["message"])

    def check_dotenv(self, location, *, configured=False):
        """模拟重构后的文件层级，避开开发者真实配置和凭据。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            agent = root / "agent"
            agent.mkdir()
            env_file = root / location
            env_file.parent.mkdir(parents=True, exist_ok=True)
            env_file.write_text(
                "KOI_TEST_VALUE=from-file\nKOI_TEST_EXISTING=from-file\n"
            )
            cwd = root / "unrelated"
            cwd.mkdir()
            environment = {"KOI_TEST_EXISTING": "from-process"}
            if configured:
                environment["KOI_ENV_FILE"] = str(env_file)

            previous_cwd = Path.cwd()
            try:
                os.chdir(cwd)
                with (
                    patch.object(
                        config, "__file__", str(agent / "config" / "config.py")
                    ),
                    patch.dict(os.environ, environment, clear=True),
                ):
                    config._load_dotenv()
                    self.assertEqual(os.environ.get("KOI_TEST_VALUE"), "from-file")
                    self.assertEqual(
                        os.environ.get("KOI_TEST_EXISTING"), "from-process"
                    )
            finally:
                os.chdir(previous_cwd)

    def test_loads_env_beside_root_entry(self):
        self.check_dotenv("agent/.env")

    def test_loads_desktop_env_from_unrelated_directory(self):
        self.check_dotenv("desktop/.env")

    def test_explicit_env_file_preserves_process_environment(self):
        self.check_dotenv("custom/settings.env", configured=True)
