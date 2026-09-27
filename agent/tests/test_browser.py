"""Tests for the pure logic in react.browser — no browser required.

Run with:  PYTHONPATH=agent python3 -m unittest discover -s agent/tests -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from react import browser  # noqa: E402


def make_session(session_id="62257e30-c753-4153-a8d9-b242cbad2987", port=56353):
    """A session that never shells out — `cli` points at the interpreter."""
    return browser.BrowserSession(session_id, port, "TARGET-1", cli=sys.executable)


def tab(tab_id, url, title, kind="page"):
    return {"tabId": tab_id, "url": url, "title": title, "type": kind}


class SessionNaming(unittest.TestCase):
    def test_is_stable_and_short(self):
        first = browser.session_name("sess-a", 51234)
        self.assertEqual(first, browser.session_name("sess-a", 51234))
        self.assertRegex(first, r"^bu[0-9a-f]{10}$")

    def test_differs_per_session_and_per_port(self):
        self.assertNotEqual(browser.session_name("a", 1), browser.session_name("b", 1))
        # The app picks a random CDP port each launch; a daemon that survived a
        # restart would otherwise keep aiming at the dead one.
        self.assertNotEqual(browser.session_name("a", 1), browser.session_name("a", 2))

    def test_survives_session_ids_that_are_not_path_safe(self):
        name = browser.session_name("../../etc/passwd", 1)
        self.assertRegex(name, r"^bu[0-9a-f]{10}$")
        self.assertNotIn("/", name)
        self.assertNotIn("..", name)


class SocketBudget(unittest.TestCase):
    """macOS `sun_path` is 104 bytes; agent-browser validates 103 and exits 1
    with the complaint on stdout. A UUID session name under `<userData>/harness/`
    once produced 174 bytes and broke every run."""

    def test_real_uuid_stays_under_the_limit(self):
        session = make_session()
        length = len(str(browser.socket_path(session.session)).encode("utf-8"))
        self.assertLessEqual(length, browser.SOCKET_PATH_LIMIT)

    def test_socket_dir_is_under_tmpdir_not_userdata(self):
        self.assertEqual(browser.socket_dir(), Path(tempfile.gettempdir()) / "buab")

    def test_constructor_rejects_an_over_long_path(self):
        with unittest.mock.patch.object(
            browser, "socket_dir", lambda: Path("/tmp/" + "d" * 120)
        ):
            with self.assertRaises(browser.BindingLost):
                make_session()


class TabSelection(unittest.TestCase):
    select = staticmethod(browser.BrowserSession._select)

    def test_marker_wins(self):
        tabs = [
            tab("t1", "https://example.com", "Example Domain"),
            tab("t2", "about:blank", "bu-target-mine"),
            tab("t3", "about:blank", ""),
        ]
        self.assertEqual(self.select(tabs, "bu-target-mine", "about:blank", ""), "t2")

    def test_falls_back_to_url_and_title_once_navigated(self):
        tabs = [tab("t1", "https://a.test", "A"), tab("t2", "https://b.test", "B")]
        self.assertEqual(
            self.select(tabs, "bu-target-gone", "https://b.test", "B"), "t2"
        )

    def test_url_alone_is_enough_when_the_title_drifted(self):
        tabs = [tab("t1", "https://a.test", "A"), tab("t2", "https://b.test", "Stale")]
        self.assertEqual(self.select(tabs, None, "https://b.test", "B"), "t2")

    def test_refuses_to_guess_between_identical_blank_views(self):
        """The common case at session start: every fresh view is about:blank.
        Picking one would drive another session's page."""
        tabs = [tab("t1", "about:blank", ""), tab("t2", "about:blank", "")]
        self.assertIsNone(self.select(tabs, None, "about:blank", ""))

    def test_refuses_to_guess_between_identical_pages(self):
        tabs = [
            tab("t1", "https://a.test", "Same"),
            tab("t2", "https://a.test", "Same"),
        ]
        self.assertIsNone(self.select(tabs, None, "https://a.test", "Same"))

    def test_returns_none_when_the_target_is_absent(self):
        self.assertIsNone(
            self.select(
                [tab("t1", "https://a.test", "A")], None, "https://missing.test", ""
            )
        )

    def test_ignores_non_page_targets(self):
        tabs = [
            tab("t1", "chrome-extension://x", "Ext", "background_page"),
            tab("t2", "https://a.test", "A"),
        ]
        self.assertEqual(self.select(tabs, None, "https://a.test", "A"), "t2")


class FailureText(unittest.TestCase):
    """agent-browser reports failures as JSON on **stdout** with an empty stderr,
    so reading stderr alone loses the only useful detail."""

    def test_extracts_the_json_error_from_stdout(self):
        text = browser.BrowserSession._failure_text(
            '{"error":"Session name is too long. Socket path would be 174 bytes (max 103).","success":false}',
            "",
        )
        self.assertIn("Socket path would be 174 bytes", text)

    def test_prefers_stderr_when_present(self):
        self.assertEqual(
            browser.BrowserSession._failure_text('{"error":"x"}', "boom"), "boom"
        )

    def test_falls_back_to_raw_text(self):
        self.assertEqual(
            browser.BrowserSession._failure_text("plain failure", ""), "plain failure"
        )

    def test_reports_silence_explicitly(self):
        self.assertEqual(
            browser.BrowserSession._failure_text("", ""),
            "agent-browser produced no output",
        )


class Environment(unittest.TestCase):
    def test_sets_only_the_vars_agent_browser_needs(self):
        env = make_session().env
        self.assertEqual(env["AGENT_BROWSER_CDP"], "56353")
        self.assertEqual(
            env["AGENT_BROWSER_SESSION"],
            browser.session_name("62257e30-c753-4153-a8d9-b242cbad2987", 56353),
        )
        self.assertEqual(env["AGENT_BROWSER_SOCKET_DIR"], str(browser.socket_dir()))
        self.assertGreater(int(env["AGENT_BROWSER_IDLE_TIMEOUT_MS"]), 3_600_000)

    def test_does_not_force_a_screenshot_dir(self):
        # That would route the agent's private visual check-ins into the watched
        # outputs dir and surface every one of them in the chat.
        self.assertNotIn("AGENT_BROWSER_SCREENSHOT_DIR", make_session().env)

    def test_strips_vars_that_would_steer_the_binding(self):
        import os

        os.environ["AGENT_BROWSER_AUTO_CONNECT"] = "1"
        try:
            self.assertNotIn("AGENT_BROWSER_AUTO_CONNECT", make_session().env)
        finally:
            del os.environ["AGENT_BROWSER_AUTO_CONNECT"]


if __name__ == "__main__":
    unittest.main(verbosity=2)
