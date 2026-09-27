"""PyCharm 直接运行：只向完整规划模型 /responses 发一次请求。"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from koi_agent.config import load_settings

# 与生产代码共用提示词，避免手动测试落后于当前规划契约。
from koi_agent.planner import PLANNER_SYSTEM_PROMPT


def request_once(
    base_url: str,
    api_key: str,
    model: str,
    timeout: float,
    user_input: str,
    history: list[dict],
    context: dict,
) -> None:
    """复现 Planner.plan 的单次模型请求，不解析计划、不启动浏览器。"""
    if not api_key:
        raise ValueError("API_KEY 为空，请在 __main__ 中填写，或配置现有的 KOI_PLANNER_API_KEY")
    model_input = json.dumps(
        {
            "history": history,
            "user_input": user_input,
            "context": context,
        },
        ensure_ascii=False,
    )
    body = {
        "model": model,
        "instructions": PLANNER_SYSTEM_PROMPT,
        "input": model_input,
        "enable_thinking": False,
    }
    encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
    url = f"{base_url.rstrip('/')}/responses"
    request = urllib.request.Request(
        url,
        encoded,
        {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    print(f"规划 URL: {url}")
    print(
        f"model={model} system_chars={len(PLANNER_SYSTEM_PROMPT)} "
        f"input_chars={len(model_input)} request_bytes={len(encoded)} timeout={timeout}s"
    )
    print("模型 input JSON：", model_input)
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
    print("模型原始返回：")
    print(json.dumps(value, ensure_ascii=False, indent=2))
    texts = []
    for item in value.get("output", []):
        if item.get("type") != "message":
            continue
        for part in item.get("content", []):
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                texts.append(part["text"])
    if not texts and isinstance(value.get("output_text"), str):
        texts.append(value["output_text"])
    print("提取出的规划文本：")
    print("\n".join(texts) if texts else "<未找到文本>")


if __name__ == "__main__":
    # 只改这里，然后在 PyCharm 直接 Run。本文件只发一次 /responses 请求。
    current = load_settings().planner
    BASE_URL = current.base_url  # 或手动填写你的规划模型 base_url
    API_KEY = current.api_key  # 或手动填写自己的 key；不会打印
    MODEL = current.model  # 或手动填写模型名
    TIMEOUT = current.timeout

    # 默认复制这次任务实际发给完整规划层的数据；按需直接修改变量。
    USER_INPUT = "12306帮我买武汉到潜江10点左右的票"
    HISTORY = [{"type": "user_input", "text": USER_INPUT}]
    CONTEXT = {"entry_locator": "completed", "entry_url": "https://v.qq.com/"}

    request_once(BASE_URL, API_KEY, MODEL, TIMEOUT, USER_INPUT, HISTORY, CONTEXT)
