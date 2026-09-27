"""PyCharm 直接运行：只向 Jev /systemone 发一次请求，不启动浏览器。"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# 支持在 PyCharm 或任意工作目录直接运行本文件。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.config import load_settings  # noqa: E402


def build_simulated_body(goal: str, observation: dict, model: str) -> dict:
    """与 llm.models.JevDecision.choose 使用相同的请求体结构。"""
    elements = observation["elements"]
    if not elements:
        raise ValueError("SIMULATED_OBSERVATION.elements 不能为空")
    click_targets = {str(index): element for index, element in enumerate(elements, 1)}
    text_targets = {
        index: element
        for index, element in click_targets.items()
        if any(
            role in element["text"].lower()
            for role in ("textbox", "searchbox", "input")
        )
    }
    operations = {
        "CLICK": "Click one of the observed interactive elements.",
        "WAIT": "Wait for the page to finish loading.",
        "DONE": "The goal is already visibly satisfied.",
        "BLOCKED": "No safe supported action can make progress.",
    }
    if text_targets:
        operations["TYPE_TEXT"] = "Enter text into an observed editable field."
    questions = {
        "operation": {
            "type": "choice",
            "criteria": operations,
            "instructions": {
                "goal": goal,
                "rules": "Choose one safe next action; do not invent a target.",
            },
        },
        "click_target": {
            "type": "choice",
            "criteria": {
                index: {"element": element["text"]}
                for index, element in click_targets.items()
            },
            "instructions": {"goal": goal, "operation": "CLICK"},
        },
    }
    if text_targets:
        questions["type_text_target"] = {
            "type": "choice",
            "criteria": {
                index: {"element": element["text"]}
                for index, element in text_targets.items()
            },
            "instructions": {"goal": goal, "operation": "TYPE_TEXT"},
        }
    return {
        "model": model,
        "state": {
            "page": {
                "url": observation["url"],
                "title": observation["title"],
                "text": observation["snapshot"][:8000],
            },
            "elements": [
                {"index": index, "label": element["text"], "operations": ["CLICK"]}
                for index, element in click_targets.items()
            ],
            "recent_actions": [],
        },
        "questions": questions,
    }


def body_from_log(log_path: Path, call_number: int) -> dict:
    """重放一次真实 Jev 请求；只读取请求体，不运行历史任务。"""
    bodies = []
    with log_path.open(encoding="utf-8") as handle:
        for line in handle:
            entry = json.loads(line)
            if (
                entry.get("stage") == "model.request"
                and entry.get("provider") == "decision"
            ):
                bodies.append(entry["body"])
    if not 1 <= call_number <= len(bodies):
        raise ValueError(
            f"日志中只有 {len(bodies)} 次 Jev 请求，不能选第 {call_number} 次"
        )
    return bodies[call_number - 1]


def request_once(base_url: str, api_key: str, body: dict, timeout: float) -> None:
    """请求一次，分别打印响应头等待、响应体读取和总耗时。"""
    if not api_key:
        raise ValueError(
            "API_KEY 为空，请在 __main__ 中填写，或配置现有的 KOI_DECISION_API_KEY"
        )
    encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
    url = f"{base_url.rstrip('/')}/systemone"
    request = urllib.request.Request(
        url,
        encoded,
        {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    print(f"Jev URL: {url}")
    print(
        f"model={body.get('model')} elements={len(body.get('state', {}).get('elements', []))} "
        f"request_bytes={len(encoded)} timeout={timeout}s"
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            headers_at = time.perf_counter()
            status = response.status
            raw = response.read().decode("utf-8", errors="replace")
            body_at = time.perf_counter()
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code}，耗时 {(time.perf_counter() - started) * 1000:.2f} ms")
        print(exc.read().decode("utf-8", errors="replace"))
        return
    except Exception as exc:
        print(f"请求失败，耗时 {(time.perf_counter() - started) * 1000:.2f} ms: {exc}")
        return

    print(
        f"HTTP {status} | 等待响应头 {(headers_at - started) * 1000:.2f} ms | "
        f"读取响应体 {(body_at - headers_at) * 1000:.2f} ms | "
        f"请求总计 {(body_at - started) * 1000:.2f} ms | response_bytes={len(raw.encode('utf-8'))}"
    )
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        print("返回不是 JSON：\n" + raw)
        return
    print("Jev 原始返回：")
    print(json.dumps(value, ensure_ascii=False, indent=2))
    print(
        "Jev 选项结果：", json.dumps(value.get("answers"), ensure_ascii=False, indent=2)
    )


if __name__ == "__main__":
    # 只改这里，然后在 PyCharm 直接 Run。本文件只发一次 /systemone 请求。
    current = load_settings().decision
    BASE_URL = current.base_url  # 或手动改成 "https://api.typesafe.ai/v1"
    API_KEY = current.api_key  # 或手动填写自己的 key；不会打印
    MODEL = current.model  # 或手动改成 "jev-latest"
    TIMEOUT = current.timeout

    # 默认使用下面可直接修改的模拟数据，不依赖任何旧日志文件。
    # 若要重放真实请求，改成 True，并填写实际存在的 .jsonl 日志路径。
    USE_RECORDED_REQUEST = False
    RECORDED_LOG = (
        Path(__file__).resolve().parents[1]
        / "logger"
        / "agent-20260926T113115813705Z-41619.log"
    )
    RECORDED_CALL_NUMBER = 2

    GOAL = "在历史记录列表中查找标题包含“灵境行者”的条目，并点击进入播放页以继续观看"
    SIMULATED_OBSERVATION = {
        "url": "https://v.qq.com/biu/u/history/",
        "title": "腾讯视频 - 历史记录",
        "snapshot": '- link "历史" [ref=e1]\n- generic "灵境行者第01集 · 看至40%" [ref=e2] clickable\n- link "首页" [ref=e3]',
        "elements": [
            {"ref": "@e1", "text": 'link "历史"'},
            {"ref": "@e2", "text": 'generic "灵境行者第01集 · 看至40%" clickable'},
            {"ref": "@e3", "text": 'link "首页"'},
        ],
    }

    if USE_RECORDED_REQUEST:
        if not RECORDED_LOG.is_file():
            raise SystemExit(
                f"找不到日志：{RECORDED_LOG}\n请修改 RECORDED_LOG，或将 USE_RECORDED_REQUEST 改为 False 使用模拟数据。"
            )
        BODY = body_from_log(RECORDED_LOG, RECORDED_CALL_NUMBER)
        BODY["model"] = MODEL
        print(f"重放日志：{RECORDED_LOG.name}，第 {RECORDED_CALL_NUMBER} 次 Jev 请求")
    else:
        BODY = build_simulated_body(GOAL, SIMULATED_OBSERVATION, MODEL)
        print("使用 SIMULATED_OBSERVATION 模拟页面")
    print("\n")
    print(BODY)
    print("\n")
    request_once(BASE_URL, API_KEY, BODY, TIMEOUT)
