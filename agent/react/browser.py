"""agent-browser 的唯一调用边界，负责标签页绑定和守护进程重连。

Electron 只传入 CDP targetId；agent-browser 使用自己的 t1..tN 编号。
本模块先读取目标 URL/标题，再通过 CDP 注入唯一标题标记，完成精确映射。
守护进程重启后必须重新绑定，不能默认选择 t1，否则会操作其他会话。
目标不存在或匹配有歧义时直接报错，不猜测目标标签页。"""

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

from logger import logger

from react import cdp

#: macOS `sun_path` is 104 bytes; agent-browser validates 103 and exits 1 with
#: the complaint on **stdout**, not stderr.
SOCKET_PATH_LIMIT = 103

#: Long leash: the default one-hour idle timeout would drop the binding between
#: turns of a long conversation.
IDLE_TIMEOUT_MS = "86400000"

DEFAULT_TIMEOUT = 60.0


class BindingLost(RuntimeError):
    """会话与指定页面的绑定已失效，禁止盲目重试到其他页面。"""


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
        """生成简短的 tool_result 预览；成功无输出时显示 ok。"""
        body = (self.stdout or self.stderr).strip()
        if len(body) > 800:
            body = body[:800] + "…"
        return body or ("ok" if self.ok else f"exit {self.exit_code}")


def resolve_cli(explicit: str | None = None) -> str:
    """确定 agent-browser 路径：显式参数和环境覆盖优先，否则使用宿主扩充的 PATH。"""
    candidate = explicit or os.environ.get("KOI_AGENT_BROWSER")
    if candidate:
        if not os.path.isfile(candidate):
            raise BindingLost(
                f"KOI_AGENT_BROWSER points at a missing file: {candidate}"
            )
        return candidate
    found = shutil.which("agent-browser")
    if not found:
        raise BindingLost(
            "agent-browser is not installed or not on PATH. "
            "Install it with `npm install -g agent-browser` (or `brew install agent-browser`)."
        )
    return found


def session_name(session_id: str, cdp_port: int) -> str:
    """由会话 ID 和 CDP 端口生成短名称，避免重启后复用旧端口的守护进程。"""
    digest = hashlib.sha256(f"{session_id}:{cdp_port}".encode("utf-8")).hexdigest()
    return f"bu{digest[:10]}"


def socket_dir() -> Path:
    """使用较短的临时目录规避 Unix socket 长度限制；会话名称提供隔离。"""
    return Path(tempfile.gettempdir()) / "buab"


def socket_path(session: str) -> Path:
    """返回当前 agent-browser 会话的 Unix socket 文件路径。"""
    return socket_dir() / f"{session}.sock"


class BrowserSession:
    """一个应用会话对其唯一浏览器视图的访问句柄。"""

    def __init__(
        self, session_id: str, cdp_port: int, target_id: str, cli: str | None = None
    ):
        self.session_id = session_id
        self.cdp_port = cdp_port
        self.target_id = target_id
        self.cli = resolve_cli(cli)
        self.session = session_name(session_id, cdp_port)
        self.dir = socket_dir()
        self.pid_file = self.dir / f"{self.session}.pid"
        self.pid_cache = self.dir / f"{self.session}.daemon.pid"
        self.marker = (
            f"bu-target-{hashlib.sha256(session_id.encode()).hexdigest()[:16]}"
        )
        self._bound_tab: str | None = None
        self._daemon_pid: str | None = None
        self._check_socket_budget()

    # ── setup ───────────────────────────────────────────────────────────────

    def _check_socket_budget(self) -> None:
        """在启动 CLI 前检查 socket 路径字节长度。"""
        length = len(str(socket_path(self.session)).encode("utf-8"))
        if length > SOCKET_PATH_LIMIT:
            raise BindingLost(
                f"agent-browser socket path would be {length} bytes, over the "
                f"{SOCKET_PATH_LIMIT}-byte Unix socket limit: {socket_path(self.session)}. "
                "Set a shorter TMPDIR."
            )

    @property
    def env(self) -> dict[str, str]:
        """构造 CLI 子进程环境，固定会话与 CDP 端口。

        不设置 AGENT_BROWSER_SCREENSHOT_DIR，避免内部检查截图
        被宿主误识别为用户交付文件；交付截图应显式指定输出位置。"""
        env = dict(os.environ)
        env.update(
            {
                "AGENT_BROWSER_SESSION": self.session,
                "AGENT_BROWSER_CDP": str(self.cdp_port),
                "AGENT_BROWSER_SOCKET_DIR": str(self.dir),
                "AGENT_BROWSER_IDLE_TIMEOUT_MS": IDLE_TIMEOUT_MS,
            }
        )
        # Nothing upstream may steer the binding: these would override the above
        # or make agent-browser pick its own browser.
        for key in (
            "AGENT_BROWSER_AUTO_CONNECT",
            "AGENT_BROWSER_PROFILE",
            "AGENT_BROWSER_STATE",
        ):
            env.pop(key, None)
        return env

    # ── agent-browser plumbing ──────────────────────────────────────────────

    @logger.traced("browser.cli")
    def _cli(self, args: list[str], timeout: float = 30.0) -> tuple[bool, str, str]:
        """执行内部绑定命令，将超时统一转换为失败返回值。"""
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
        """兼顾 stdout 中的 JSON 错误与 stderr，避免遗漏 agent-browser 的实际报错。"""
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
        """读取 CLI 标签列表，仅解析 JSON 信封，不被页面文本中的大括号干扰。"""
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
        """直接从 CDP 获取该 target 的真实 URL 和标题。"""
        try:
            entry = cdp.find_target(self.cdp_port, self.target_id)
        except cdp.CdpError as exc:
            logger.debug(f"target lookup failed, binding by marker only: {exc}")
            return "", ""
        return entry.get("url") or "", entry.get("title") or ""

    def _plant_marker(self) -> bool:
        """给目标页面写入唯一标题标记，区分多个 about:blank 会话；失败时返回 False。"""
        try:
            cdp.evaluate(
                self.cdp_port,
                self.target_id,
                f"document.title = {json.dumps(self.marker)}",
            )
            return True
        except cdp.CdpError as exc:
            logger.debug(f"could not plant marker: {exc}")
            return False

    @staticmethod
    def _select(
        tabs: list[dict], marker: str | None, url: str, title: str
    ) -> str | None:
        """优先精确匹配唯一标记，再匹配 URL/标题；匹配有歧义时拒绝选择。"""
        pages = [t for t in tabs if (t.get("type") or "page") == "page"]
        if marker:
            hits = [t for t in pages if t.get("title") == marker]
            if len(hits) == 1:
                return hits[0]["tabId"]
        if url:
            exact = [
                t for t in pages if t.get("url") == url and t.get("title") == title
            ]
            if len(exact) == 1:
                return exact[0]["tabId"]
            loose = [t for t in pages if t.get("url") == url]
            if len(loose) == 1:
                return loose[0]["tabId"]
        return None

    @logger.traced("browser.bind")
    def bind(self) -> str:
        """将 agent-browser 会话绑定到宿主指定的视图，返回唯一匹配的 tabId。"""
        self.dir.mkdir(parents=True, exist_ok=True)
        url, title = self._target_fingerprint()
        planted = self._plant_marker()

        tabs = self._list_tabs()
        tab_id = self._select(tabs, self.marker if planted else None, url, title)
        if tab_id is None:
            seen = (
                ", ".join(f"{t.get('tabId')}={str(t.get('url'))[:60]}" for t in tabs)
                or "none"
            )
            raise BindingLost(
                f"could not uniquely identify target {self.target_id} "
                f"(url={url or '?'!r}) among {len(tabs)} tabs: {seen}. "
                "Refusing to guess — picking the wrong tab would drive another session's page."
            )

        ok, stdout, stderr = self._cli(["tab", tab_id], timeout=15.0)
        if not ok:
            raise BindingLost(
                f"`agent-browser tab {tab_id}` failed: {self._failure_text(stdout, stderr)[:300]}"
            )

        self._bound_tab = tab_id
        self._daemon_pid = self._read_pid()
        try:
            self.pid_cache.write_text(self._daemon_pid or "", encoding="utf-8")
        except OSError:
            pass  # best effort; worst case the next command rebinds again
        logger.debug(
            f"bound {self.session} -> {tab_id} (target {self.target_id[:8]}, {len(tabs)} candidates)"
        )
        return tab_id

    def _read_pid(self) -> str:
        """读取守护进程 PID，文件缺失或不可读时返回空值。"""
        try:
            return self.pid_file.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    @staticmethod
    def _pid_alive(pid: str) -> bool:
        """用信号 0 检查 PID 是否仍存活，不向进程发送实际信号。"""
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
        """守护进程重启或消失后重新绑定，避免新守护进程默认选中 t1。

        同时验证 PID 文件和进程存活状态。僵尸进程短暂存活期间仍存在
        极小的竞态窗口；不为此在每条命令前启动昂贵的 ps 查询。"""
        if self._bound_tab is None:
            self.bind()
            return
        current = self._read_pid()
        if current == self._daemon_pid and self._pid_alive(current):
            return
        logger.debug(
            f"daemon pid changed ({self._daemon_pid!r} -> {current!r}); rebinding"
        )
        self.bind()

    # ── execution ───────────────────────────────────────────────────────────

    @logger.traced("browser.run")
    def run(self, args: list[str], timeout: float = DEFAULT_TIMEOUT) -> BrowserResult:
        """校验绑定后执行一个命令，返回统一的成功、错误与耗时信息。"""
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
            return BrowserResult(
                False, tuple(args), "", f"timed out after {timeout}s", 124, elapsed
            )

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
                logger.debug(
                    "daemon changed under a failed command; rebinding and retrying once"
                )
                self._daemon_pid = None
                self._bound_tab = None
                self.ensure_bound()
                return self.run(args, timeout=timeout)
        return result

    def open_url(self, url: str, timeout: float = DEFAULT_TIMEOUT) -> BrowserResult:
        """把显式导航委托给统一的浏览器命令入口。"""
        return self.run(["open", url], timeout=timeout)

    def current_url(self, timeout: float = 15.0) -> str:
        """读取当前绑定页面的 URL，失败时返回空字符串。"""
        result = self.run(["get", "url"], timeout=timeout)
        return result.stdout.strip() if result.ok else ""


def log_environment() -> None:
    """通过诊断接口记录 Python 和 agent-browser 的解析路径。"""
    logger.debug(
        f"python={sys.version.split()[0]} agent-browser={shutil.which('agent-browser') or 'NOT FOUND'}"
    )
