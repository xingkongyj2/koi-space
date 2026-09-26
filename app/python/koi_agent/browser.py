"""The only module that talks to `agent-browser` — the Executor seam.

Everything upstream stays ignorant of CLI details. This module owns the whole
browser contract that used to live in the Electron main process plus a PATH
shim:

* **Binding.** agent-browser addresses pages as `t1..tN` in its own enumeration
  order and has no notion of a CDP targetId — feeding it a page-level
  `ws://…/devtools/page/<id>` URL is accepted and then ignored. The app assigns
  each session one specific browser view, so we translate targetId → tab id
  ourselves: read the target's real url/title from `/json/list`, plant a unique
  `document.title` marker through CDP, then match it in `tab list`.

* **Rebinding.** The binding lives in agent-browser's per-session daemon. When
  that daemon exits (idle timeout, crash, machine sleep) the next command
  silently reattaches to `t1` — another session's page. So every command first
  checks the daemon pid; if it changed or died, we re-resolve. Doing this
  in-process is why no PATH shim is needed any more.

* **Refusing to guess.** If the target cannot be matched uniquely we raise
  instead of picking one. Driving the wrong page is worse than failing.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from . import cdp, protocol

#: macOS `sun_path` is 104 bytes; agent-browser validates 103 and exits 1 with
#: the complaint on **stdout**, not stderr.
SOCKET_PATH_LIMIT = 103

#: Long leash: the default one-hour idle timeout would drop the binding between
#: turns of a long conversation.
IDLE_TIMEOUT_MS = "86400000"

DEFAULT_TIMEOUT = 60.0


class BindingLost(RuntimeError):
    """The session is not attached to its browser view. Never retry blindly."""


@dataclass(frozen=True)
class BrowserResult:
    ok: bool
    args: tuple[str, ...]
    stdout: str
    stderr: str
    exit_code: int
    ms: float

    @property
    def preview(self) -> str:
        """Short single-string rendering for the chat UI's tool_result event."""
        body = (self.stdout or self.stderr).strip()
        if len(body) > 800:
            body = body[:800] + "…"
        return body or ("ok" if self.ok else f"exit {self.exit_code}")


def resolve_cli(explicit: str | None = None) -> str:
    """Absolute path to agent-browser.

    Resolved from PATH, which the app hands us already enriched with the
    nvm/Homebrew/volta directories a GUI-launched Electron process would
    otherwise not see. `KOI_AGENT_BROWSER` overrides for tests and odd installs.
    """
    candidate = explicit or os.environ.get("KOI_AGENT_BROWSER")
    if candidate:
        if not os.path.isfile(candidate):
            raise BindingLost(f"KOI_AGENT_BROWSER points at a missing file: {candidate}")
        return candidate
    found = shutil.which("agent-browser")
    if not found:
        raise BindingLost(
            "agent-browser is not installed or not on PATH. "
            "Install it with `npm install -g agent-browser` (or `brew install agent-browser`)."
        )
    return found


def session_name(session_id: str, cdp_port: int) -> str:
    """Short, stable, collision-resistant agent-browser session name.

    The CDP port is folded in on purpose: the app picks a random port on every
    launch, but agent-browser's daemon caches the endpoint it first connected to
    and ignores `AGENT_BROWSER_CDP` afterwards. A surviving daemon would
    otherwise keep aiming at the previous launch's dead port.

    Short because this name becomes a Unix socket filename (see
    `socket_path`).
    """
    digest = hashlib.sha256(f"{session_id}:{cdp_port}".encode("utf-8")).hexdigest()
    return f"bu{digest[:10]}"


def socket_dir() -> Path:
    """Shared by every session — isolation comes from the session name.

    Kept under tmpdir rather than the app's userData because directory depth is
    exactly what the socket path budget cannot afford.
    """
    return Path(tempfile.gettempdir()) / "buab"


def socket_path(session: str) -> Path:
    return socket_dir() / f"{session}.sock"


class BrowserSession:
    """One app session's handle on its own browser view."""

    def __init__(self, session_id: str, cdp_port: int, target_id: str, cli: str | None = None):
        self.session_id = session_id
        self.cdp_port = cdp_port
        self.target_id = target_id
        self.cli = resolve_cli(cli)
        self.session = session_name(session_id, cdp_port)
        self.dir = socket_dir()
        self.pid_file = self.dir / f"{self.session}.pid"
        self.pid_cache = self.dir / f"{self.session}.daemon.pid"
        self.marker = f"bu-target-{hashlib.sha256(session_id.encode()).hexdigest()[:16]}"
        self._bound_tab: str | None = None
        self._daemon_pid: str | None = None
        self._check_socket_budget()

    # ── setup ───────────────────────────────────────────────────────────────

    def _check_socket_budget(self) -> None:
        length = len(str(socket_path(self.session)).encode("utf-8"))
        if length > SOCKET_PATH_LIMIT:
            raise BindingLost(
                f"agent-browser socket path would be {length} bytes, over the "
                f"{SOCKET_PATH_LIMIT}-byte Unix socket limit: {socket_path(self.session)}. "
                "Set a shorter TMPDIR."
            )

    @property
    def env(self) -> dict[str, str]:
        """Environment for agent-browser subprocesses.

        Deliberately does NOT set `AGENT_BROWSER_SCREENSHOT_DIR`: that would
        route the agent's own visual check-ins into the watched outputs dir and
        surface every one of them in the chat as a `file_output` event.
        Screenshots the user should see get an explicit path instead.
        """
        env = dict(os.environ)
        env.update({
            "AGENT_BROWSER_SESSION": self.session,
            "AGENT_BROWSER_CDP": str(self.cdp_port),
            "AGENT_BROWSER_SOCKET_DIR": str(self.dir),
            "AGENT_BROWSER_IDLE_TIMEOUT_MS": IDLE_TIMEOUT_MS,
        })
        # Nothing upstream may steer the binding: these would override the above
        # or make agent-browser pick its own browser.
        for key in ("AGENT_BROWSER_AUTO_CONNECT", "AGENT_BROWSER_PROFILE", "AGENT_BROWSER_STATE"):
            env.pop(key, None)
        return env

    # ── agent-browser plumbing ──────────────────────────────────────────────

    @protocol.traced("browser.cli")
    def _cli(self, args: list[str], timeout: float = 30.0) -> tuple[bool, str, str]:
        try:
            proc = subprocess.run(
                [self.cli, *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                env=self.env,
            )
        except subprocess.TimeoutExpired:
            return False, "", f"timed out after {timeout}s"
        return proc.returncode == 0, proc.stdout or "", proc.stderr or ""

    @staticmethod
    def _failure_text(stdout: str, stderr: str) -> str:
        """agent-browser reports failures as JSON on **stdout** with an empty
        stderr, so reading stderr alone loses the only useful detail."""
        for chunk in (stderr, stdout):
            text = (chunk or "").strip()
            if not text:
                continue
            start = text.find("{")
            if start >= 0:
                try:
                    parsed = json.loads(text[start:])
                except ValueError:
                    return text
                error = parsed.get("error") if isinstance(parsed, dict) else None
                if isinstance(error, str) and error.strip():
                    return error.strip()
            return text
        return "agent-browser produced no output"

    def _list_tabs(self) -> list[dict]:
        ok, stdout, stderr = self._cli(["tab", "list", "--json"])
        if not ok:
            raise BindingLost(
                f"could not reach the browser on CDP port {self.cdp_port}: "
                f"{self._failure_text(stdout, stderr)[:400]}"
            )
        # Anchor on the JSON envelope, not the first `{` anywhere: a tab's url
        # can be a multi-kilobyte `data:text/html,…` document (the app's takeover
        # overlay is one) whose percent-encoded CSS is full of braces.
        for marker in ('{"success"', "{"):
            start = stdout.find(marker)
            if start < 0:
                continue
            try:
                tabs = json.loads(stdout[start:]).get("data", {}).get("tabs")
            except ValueError:
                continue
            if isinstance(tabs, list):
                return tabs
        raise BindingLost(f"unparseable `tab list --json` output ({len(stdout)} bytes)")

    # ── binding ─────────────────────────────────────────────────────────────

    def _target_fingerprint(self) -> tuple[str, str]:
        """This target's real url/title, straight from the browser."""
        try:
            entry = cdp.find_target(self.cdp_port, self.target_id)
        except cdp.CdpError as exc:
            protocol.log(f"target lookup failed, binding by marker only: {exc}")
            return "", ""
        return entry.get("url") or "", entry.get("title") or ""

    def _plant_marker(self) -> bool:
        """Make this target uniquely identifiable in `tab list`.

        Needed because every fresh session view sits at `about:blank` with an
        empty title — several concurrent sessions are then indistinguishable by
        url/title alone. Fails harmlessly on `chrome://` or a crashed renderer.
        """
        try:
            cdp.evaluate(self.cdp_port, self.target_id, f"document.title = {json.dumps(self.marker)}")
            return True
        except cdp.CdpError as exc:
            protocol.log(f"could not plant marker: {exc}")
            return False

    @staticmethod
    def _select(tabs: list[dict], marker: str | None, url: str, title: str) -> str | None:
        pages = [t for t in tabs if (t.get("type") or "page") == "page"]
        if marker:
            hits = [t for t in pages if t.get("title") == marker]
            if len(hits) == 1:
                return hits[0]["tabId"]
        if url:
            exact = [t for t in pages if t.get("url") == url and t.get("title") == title]
            if len(exact) == 1:
                return exact[0]["tabId"]
            loose = [t for t in pages if t.get("url") == url]
            if len(loose) == 1:
                return loose[0]["tabId"]
        return None

    @protocol.traced("browser.bind")
    def bind(self) -> str:
        """Point this agent-browser session at our assigned view. Returns the tab id."""
        self.dir.mkdir(parents=True, exist_ok=True)
        url, title = self._target_fingerprint()
        planted = self._plant_marker()

        tabs = self._list_tabs()
        tab_id = self._select(tabs, self.marker if planted else None, url, title)
        if tab_id is None:
            seen = ", ".join(f"{t.get('tabId')}={str(t.get('url'))[:60]}" for t in tabs) or "none"
            raise BindingLost(
                f"could not uniquely identify target {self.target_id} "
                f"(url={url or '?'!r}) among {len(tabs)} tabs: {seen}. "
                "Refusing to guess — picking the wrong tab would drive another session's page."
            )

        ok, stdout, stderr = self._cli(["tab", tab_id], timeout=15.0)
        if not ok:
            raise BindingLost(f"`agent-browser tab {tab_id}` failed: {self._failure_text(stdout, stderr)[:300]}")

        self._bound_tab = tab_id
        self._daemon_pid = self._read_pid()
        try:
            self.pid_cache.write_text(self._daemon_pid or "", encoding="utf-8")
        except OSError:
            pass  # best effort; worst case the next command rebinds again
        protocol.log(f"bound {self.session} -> {tab_id} (target {self.target_id[:8]}, {len(tabs)} candidates)")
        return tab_id

    def _read_pid(self) -> str:
        try:
            return self.pid_file.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    @staticmethod
    def _pid_alive(pid: str) -> bool:
        if not pid:
            return False
        try:
            os.kill(int(pid), 0)
        except (ValueError, ProcessLookupError):
            return False
        except PermissionError:
            return True  # exists, owned by someone else
        return True

    def ensure_bound(self) -> None:
        """Re-resolve the binding only when the daemon has actually restarted.

        A pid-file match alone is not enough: a killed daemon leaves its pid file
        behind with the old pid in it, so the cache would compare equal while
        nothing is serving and the next command would spawn a fresh daemon bound
        to `t1` — another session's page.

        Known residual race: for a few milliseconds after a daemon dies it is a
        zombie, and `os.kill(pid, 0)` still succeeds for zombies (measured at
        3.2 ms on macOS before launchd reaps it). Closing that would mean
        spawning `ps` on every browser command, which costs more than the risk:
        the daemon only ever dies while idle, so a command would have to begin
        within ~3 ms of an idle death to be affected.
        """
        if self._bound_tab is None:
            self.bind()
            return
        current = self._read_pid()
        if current == self._daemon_pid and self._pid_alive(current):
            return
        protocol.log(f"daemon pid changed ({self._daemon_pid!r} -> {current!r}); rebinding")
        self.bind()

    # ── execution ───────────────────────────────────────────────────────────

    @protocol.traced("browser.run")
    def run(self, args: list[str], timeout: float = DEFAULT_TIMEOUT) -> BrowserResult:
        """Run one agent-browser command against this session's view."""
        if not args:
            raise ValueError("agent-browser requires a subcommand")
        self.ensure_bound()
        started = time.monotonic()
        try:
            proc = subprocess.run(
                [self.cli, *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                env=self.env,
            )
        except subprocess.TimeoutExpired:
            elapsed = (time.monotonic() - started) * 1000
            return BrowserResult(False, tuple(args), "", f"timed out after {timeout}s", 124, elapsed)

        elapsed = (time.monotonic() - started) * 1000
        result = BrowserResult(
            ok=proc.returncode == 0,
            args=tuple(args),
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            exit_code=proc.returncode,
            ms=elapsed,
        )
        if not result.ok:
            detail = self._failure_text(result.stdout, result.stderr)
            if "lost its browser binding" in detail:
                raise BindingLost(detail)
            # A command can also fail because the daemon died underneath it; give
            # one rebind-and-retry before reporting, then surface honestly.
            if self._read_pid() != self._daemon_pid:
                protocol.log("daemon changed under a failed command; rebinding and retrying once")
                self._daemon_pid = None
                self._bound_tab = None
                self.ensure_bound()
                return self.run(args, timeout=timeout)
        return result

    def open_url(self, url: str, timeout: float = DEFAULT_TIMEOUT) -> BrowserResult:
        return self.run(["open", url], timeout=timeout)

    def current_url(self, timeout: float = 15.0) -> str:
        """Read back the bound view's URL — cheapest proof the chain is live."""
        result = self.run(["get", "url"], timeout=timeout)
        return result.stdout.strip() if result.ok else ""


def log_environment() -> None:
    """One stderr line describing the resolved toolchain, for post-mortems."""
    protocol.log(f"python={sys.version.split()[0]} agent-browser={shutil.which('agent-browser') or 'NOT FOUND'}")
