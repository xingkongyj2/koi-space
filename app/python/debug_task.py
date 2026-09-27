#!/usr/bin/env python3
"""默认通过本项目应用执行完整任务，也可手动指定独立浏览器。"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from contextlib import closing, contextmanager, redirect_stdout
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from koi_agent import browser, protocol
from koi_agent.config import load_settings

# 在 PyCharm 中直接运行时，修改这里即可更换任务。
DEFAULT_USER_INPUT = "打开 https://example.com"
REPO_ROOT = Path(__file__).resolve().parents[2]


def profile_candidates(explicit: str | None = None) -> list[Path]:
    """按显式参数、环境变量和本地默认位置查找应用配置目录。"""
    configured = explicit or os.getenv("AGB_USER_DATA_DIR")
    if configured:
        return [Path(configured).expanduser().resolve()]
    candidates = []
    try:
        branch = subprocess.check_output(
            ["git", "branch", "--show-current"], cwd=REPO_ROOT, text=True
        ).strip()
        if not branch:
            branch = (
                "detached-"
                + subprocess.check_output(
                    ["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, text=True
                ).strip()
            )
        name = re.sub(r"-+", "-", re.sub(r"[^a-zA-Z0-9._-]+", "-", branch)).strip("-.") or "default"
        candidates.append(REPO_ROOT / ".task" / "user-data" / name)
    except (OSError, subprocess.CalledProcessError):
        pass
    package = json.loads((REPO_ROOT / "app" / "package.json").read_text())
    product = package.get("productName", package["name"])
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        base = Path(os.getenv("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    else:
        base = Path(os.getenv("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    candidates.append(base / product)
    return candidates


def app_request(control: dict, endpoint: str, payload: dict | None = None) -> dict:
    """向本地任务服务发送请求并解析 JSON 结果。"""
    url = control["url"]
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("本地任务接口必须使用本机 HTTP 地址")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url.rstrip("/") + endpoint,
        data=body,
        headers={"Authorization": f"Bearer {control['token']}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30 if payload is not None else 2) as response:
        return json.load(response)


def find_app(explicit: str | None = None) -> tuple[Path, dict]:
    """找到当前正在运行且可访问的本地任务服务。"""
    candidates = profile_candidates(explicit)
    for profile in candidates:
        try:
            control = json.loads((profile / "local-task-server.json").read_text())
            if app_request(control, "/health").get("ok"):
                return profile, control
        except (OSError, ValueError, KeyError):
            continue
    paths = "\n".join(str(path) for path in candidates)
    raise RuntimeError(
        "没有找到正在运行的项目应用。请先启动应用；自定义 profile 可在文件底部设置 user_data_dir。\n"
        "也可在文件底部设置 browser_path，或 cdp_port 和 target_id 指定自己的浏览器。\n"
        f"检查的 profile：\n{paths}"
    )


def print_task_log(session_id: str, started: float) -> None:
    """读取本次任务生成的日志，帮助定位运行错误。"""
    for path in sorted(protocol.LOG_DIR.glob("agent-*.log"), reverse=True):
        if path.name.endswith(".timing.log") or path.stat().st_mtime < started - 2:
            continue
        with path.open(encoding="utf-8") as handle:
            for _ in range(3):
                line = handle.readline()
                if session_id in line:
                    print(f"Python 可读日志：{path}", flush=True)
                    return
    print(f"Python 日志目录：{protocol.LOG_DIR}（尚未找到该任务的日志）", flush=True)


def run_app_task(user_input: str, profile: Path, control: dict, timeout: float) -> int:
    """通过应用服务创建任务，等待结束并输出结果。"""
    db_path = profile / "sessions.db"
    if not db_path.is_file():
        raise RuntimeError(f"找不到任务数据库：{db_path}")
    started = time.time()
    result = app_request(control, "/tasks", {"userInput": user_input, "engine": "python"})
    if not result.get("ok") or not result.get("started"):
        raise RuntimeError(result.get("error", "应用未能启动任务"))
    session_id = result["id"]
    print(f"应用 profile：{profile}\n任务 ID：{session_id}\n等待完整流程结束……", flush=True)
    deadline = time.monotonic() + timeout
    seq = -1
    with closing(sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)) as db:
        while time.monotonic() < deadline:
            row = db.execute(
                "SELECT status, error FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is None:
                raise RuntimeError("任务已从应用中删除")
            events = db.execute(
                "SELECT seq, payload FROM session_events WHERE session_id = ? AND seq > ? ORDER BY seq",
                (session_id, seq),
            ).fetchall()
            for event_seq, payload in events:
                print(json.dumps(json.loads(payload), ensure_ascii=False, indent=2), flush=True)
                seq = event_seq
            status, error = row
            if status in {"idle", "stopped", "paused"}:
                print(f"任务状态：{status}", flush=True)
                if error:
                    print(f"错误：{error}", file=sys.stderr)
                print_task_log(session_id, started)
                return 0 if status == "idle" and not error else 1
            time.sleep(0.25)
    print_task_log(session_id, started)
    raise TimeoutError(
        f"等待任务超时（{timeout:g} 秒）；应用中的任务未被停止，可继续查看。任务 ID：{session_id}"
    )


def find_browser(explicit: str | None = None) -> str:
    """查找可用浏览器程序，显式指定的路径优先。"""
    if explicit:
        candidate = shutil.which(explicit) or str(Path(explicit).expanduser())
        if Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
        raise ValueError(f"浏览器可执行文件不存在或无法执行：{explicit}")
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    ]
    for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        if os.getenv(variable):
            candidates.append(
                str(Path(os.environ[variable]) / "Google/Chrome/Application/chrome.exe")
            )
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "msedge"):
        if path := shutil.which(name):
            candidates.append(path)
    for candidate in candidates:
        if Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise ValueError("找不到 Chrome/Chromium，请用 --browser 指定浏览器可执行文件。")


@contextmanager
def test_browser(executable: str, headless: bool = False):
    """仅管理本次创建的临时浏览器，不接管用户日常使用的配置目录。"""
    with tempfile.TemporaryDirectory(prefix="koi-debug-browser-") as directory:
        command = [
            executable,
            f"--user-data-dir={directory}",
            "--remote-debugging-port=0",
            "--remote-debugging-address=127.0.0.1",
            "--no-first-run",
            "--no-default-browser-check",
            "about:blank",
        ]
        if headless:
            command.append("--headless=new")
        protocol.trace("debug.browser.launch", command=command)
        with tempfile.TemporaryFile() as diagnostics:
            process = subprocess.Popen(command, stdout=diagnostics, stderr=diagnostics)
            try:
                active_port = Path(directory) / "DevToolsActivePort"
                deadline = time.monotonic() + 30
                port = None
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        diagnostics.seek(0)
                        raise RuntimeError(
                            f"测试浏览器提前退出：{diagnostics.read().decode('utf-8', errors='replace')}"
                        )
                    if active_port.exists():
                        try:
                            port = int(active_port.read_text().splitlines()[0])
                            break
                        except (ValueError, IndexError, OSError):
                            pass
                    time.sleep(0.1)
                if port is None:
                    raise TimeoutError("测试浏览器未在 30 秒内提供 CDP 端口")
                # Create a named target explicitly instead of guessing from the tab list.
                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}/json/new?about:blank", method="PUT"
                )
                with urllib.request.urlopen(request, timeout=10) as response:
                    target = json.load(response)
                target_id = target["id"]
                protocol.trace("debug.browser.ready", cdp_port=port, target_id=target_id)
                yield port, target_id
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                diagnostics.seek(0)
                protocol.trace(
                    "debug.browser.closed",
                    exit_code=process.returncode,
                    diagnostics=diagnostics.read().decode("utf-8", errors="replace"),
                )


class EventPrinter:
    """缩进显示代理事件，并记录协议中的错误状态。"""

    def __init__(self, output):
        self.output = output
        self.failed = False

    def write(self, text):
        event = json.loads(text)
        if event.get("type") == "error":
            self.failed = True
        self.output.write(json.dumps(event, ensure_ascii=False, indent=2) + "\n")
        return len(text)

    def flush(self):
        self.output.flush()


def run_task(user_input: str, session_id: str, port: int, target_id: str) -> int:
    """构造任务信封并在本进程调用代理，保留调试事件输出。"""
    from koi_agent.__main__ import main as run_agent

    task = {
        "sessionId": session_id,
        "userInput": user_input,
        "browser": {"cdpPort": port, "targetId": target_id},
    }
    original_stdin = sys.stdin
    output = EventPrinter(sys.stdout)
    try:
        sys.stdin = io.StringIO(json.dumps(task, ensure_ascii=False))
        with redirect_stdout(output):
            code = run_agent()
        return 1 if output.failed else code
    finally:
        sys.stdin = original_stdin


def main(argv: list[str] | None = None) -> int:
    """解析命令行参数，选择宿主应用或临时浏览器执行方式。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("user_input", nargs="?", default=DEFAULT_USER_INPUT, help="任务文字")
    parser.add_argument("--cdp-port", type=int, help="使用已经运行的浏览器 CDP 端口")
    parser.add_argument("--target-id", help="要控制的页面 CDP targetId")
    parser.add_argument("--browser", help="自动启动时使用的 Chrome/Chromium 可执行文件")
    parser.add_argument("--headless", action="store_true", help="自动启动浏览器时不显示窗口")
    parser.add_argument("--config", help="指定 koi.toml 配置文件")
    parser.add_argument("--user-data-dir", help="项目应用使用的 userData/profile 目录")
    parser.add_argument(
        "--timeout", type=float, default=600, help="等待应用任务结束的秒数，默认 600"
    )
    args = parser.parse_args(argv)
    if not args.user_input.strip():
        parser.error("任务文字不能为空")
    if (args.cdp_port is None) != (args.target_id is None):
        parser.error("--cdp-port 和 --target-id 必须一起提供")
    if args.cdp_port is not None and not 1 <= args.cdp_port <= 65535:
        parser.error("CDP 端口必须在 1 到 65535 之间")
    if args.cdp_port is not None and (args.browser or args.headless):
        parser.error("已有浏览器参数不能与自动启动选项一起使用")
    direct = args.browser is not None or args.cdp_port is not None
    if args.user_data_dir and direct:
        parser.error("--user-data-dir 不能与独立浏览器参数一起使用")
    if args.headless and not args.browser:
        parser.error("--headless 需要配合 --browser")
    if args.config and not direct:
        parser.error("--config 只用于手动指定浏览器的模式；项目应用使用启动时的模型配置")
    if args.timeout <= 0:
        parser.error("--timeout 必须大于 0")
    if args.config:
        os.environ["KOI_CONFIG"] = args.config

    session_id = f"debug-{uuid4().hex}"
    protocol.set_context(session_id=session_id)
    print(f"任务：{args.user_input}", flush=True)
    try:
        if not direct:
            profile, control = find_app(args.user_data_dir)
            return run_app_task(args.user_input, profile, control, args.timeout)
        print(f"任务 ID：{session_id}")
        print(f"可读日志：{protocol.LOG_PATH.with_suffix('.log')}")
        print(f"原始日志：{protocol.LOG_PATH}", flush=True)
        settings = load_settings()
        if not settings.planner.api_key:
            raise ValueError(
                "未配置规划模型 API key。请设置 KOI_PLANNER_API_KEY，或配置 ~/.koi/config.toml / app/python/.env。"
            )
        browser.resolve_cli()  # fail before opening Chrome if agent-browser is missing
        if args.cdp_port is not None:
            return run_task(args.user_input, session_id, args.cdp_port, args.target_id)
        with test_browser(find_browser(args.browser), args.headless) as (port, target_id):
            return run_task(args.user_input, session_id, port, target_id)
    except Exception as exc:
        protocol.trace_exception("debug_task.error", exc)
        print(f"执行失败：{exc}\n详情请查看上方日志。", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n测试已中断。", file=sys.stderr)
        return 130


def run_debug(
    user_input: str,
    *,
    browser_path: str | None = None,
    user_data_dir: str | None = None,
    cdp_port: int | None = None,
    target_id: str | None = None,
    config_path: str | None = None,
    headless: bool = False,
    timeout: float = 600,
) -> int:
    """PyCharm 入口：传入任务及可选配置，不读取 IDE 的命令行参数。"""
    arguments = ["--timeout", str(timeout)]
    for option, value in (
        ("--browser", browser_path),
        ("--user-data-dir", user_data_dir),
        ("--cdp-port", cdp_port),
        ("--target-id", target_id),
        ("--config", config_path),
    ):
        if value is not None:
            arguments.extend([option, str(value)])
    if headless:
        arguments.append("--headless")
    arguments.extend(["--", user_input])
    return main(arguments)


if __name__ == "__main__":
    # 在 PyCharm 中修改这里的问题，然后点击本文件的 Run 即可执行完整流程。
    user_input = "打开 https://example.com"

    # 默认 None：连接本项目正在运行的应用，让应用准备浏览器。
    # 如果应用没启动，填入你自己的浏览器可执行文件路径，例如：
    # browser_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    browser_path = None

    # 应用使用自定义 profile 时可填写其目录；一般保持 None 自动查找。
    user_data_dir = None

    # 如果要连接已开启 CDP 的浏览器，在这里同时填入端口和页面 ID。
    # 这种方式需要保持 browser_path 和 user_data_dir 为 None。
    cdp_port = None
    target_id = None

    raise SystemExit(
        run_debug(
            user_input,
            browser_path=browser_path,
            user_data_dir=user_data_dir,
            cdp_port=cdp_port,
            target_id=target_id,
        )
    )
