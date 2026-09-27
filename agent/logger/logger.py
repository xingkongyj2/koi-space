"""统一诊断日志：中文可读输出、调用关联和耗时追踪，不向 stdout 写协议数据。"""

from __future__ import annotations

import inspect
import json
import os
import sys
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from functools import wraps
from itertools import count
from pathlib import Path
from typing import Any

LOG_DIR = Path(__file__).resolve().parent
LOG_PATH = (
    LOG_DIR / f"agent-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}-{os.getpid()}.log"
)
_LAYERS = {
    "task.run": "任务层",
    "planner.locate_entry": "规划层 · ①入口定位",
    "planner.plan": "规划层 · ②任务拆分",
    "orchestrator.run": "执行层 · 最终结果",
    "orchestrator.step": "执行层 · 子任务",
    "observer.capture": "观察层 · 页面摘要",
    "decision.choose": "决策层",
    "executor.execute": "执行层 · 本轮命令",
    "validator.action": "验证层 · 动作结果",
    "validator.step": "验证层 · 步骤结果",
    "reflection.advise": "反思层",
    "completion.verify": "验收层",
    "skills.match": "技能层 · 匹配",
    "skills.save": "技能层 · 保存",
    "memory.write": "记忆层",
    "model.responses": "模型请求",
    "model.jev": "决策层 · JEV 请求",
}
_FRAME: ContextVar[dict | None] = ContextVar("log_frame", default=None)
_REQUEST_STAGES = {
    "planning.entry.model_request",
    "planning.full.model_request",
    "decision.jev.request",
    "decision.jev.type_text_model_request",
    "verification.model_request",
}
_STAGE_LABELS = {
    **_LAYERS,
    "model.http": "模型 IO · HTTP 请求",
    "model.request": "模型 IO · 请求参数",
    "model.response.raw": "模型 IO · 原始响应",
    "model.response.parsed": "模型 IO · 解析响应",
    "model.response.http_error": "模型 IO · HTTP 错误",
    "browser.action.command": "浏览器 IO · 命令结果",
    "planning.entry.model_request": "规划层 · 入口定位模型请求",
    "planning.full.model_request": "规划层 · 任务拆分模型请求",
    "decision.jev.request": "决策层 · JEV 请求",
    "decision.jev.type_text_model_request": "决策层 · 填写内容模型请求",
    "verification.model_request": "验收层 · 模型请求",
    "decision.jev.fallback": "决策层 · JEV 失败后降级",
    "decision.model.fallback": "决策层 · 模型失败后降级",
    "reflection.model.fallback": "反思层 · 模型失败后降级",
    "completion.model.unavailable": "验收层 · 模型不可用",
    "planner.repair": "规划层 · 修正计划",
    "planner.entry_locator.repair": "规划层 · 修正入口",
}
_FIELD_LABELS = {
    "inputs": "输入",
    "result": "输出",
    "ms": "耗时（毫秒）",
    "requests": "模型请求",
    "request": "请求",
    "request_timings": "请求耗时",
    "commands": "命令",
    "errors": "错误",
    "error": "错误原因",
    "error_type": "错误类型",
    "response": "响应",
    "messages": "最终消息",
    "task": "任务输入",
    "stage": "阶段",
    "layer": "所属层",
    "session_id": "任务编号",
    "step_id": "步骤编号",
    "iteration": "轮次",
    "url": "网址",
    "title": "页面标题",
    "snapshot": "网页交互内容",
    "page_text": "网页全文",
    "diff": "页面变化",
    "changed": "是否变化",
    "elements": "交互元素",
    "element_count": "交互元素数",
    "has_page_text": "包含网页正文",
    "has_diff": "包含页面变化",
    "ref": "元素引用",
    "text": "文本",
    "goal": "目标",
    "step": "步骤",
    "steps": "步骤列表",
    "id": "编号",
    "status": "状态",
    "needs_browser": "是否需要浏览器",
    "question": "追问",
    "direct_answer": "直接答复",
    "success_criteria": "验收条件",
    "depends_on": "依赖步骤",
    "start_url": "入口网址",
    "parallel_group": "并行组",
    "needs_user_confirmation": "是否需要用户确认",
    "risk": "风险",
    "user_input": "用户输入",
    "history": "历史",
    "context": "上下文",
    "completed": "已完成步骤",
    "criteria": "校验条件",
    "observation": "网页观察",
    "before": "操作前网页",
    "after": "操作后网页",
    "action": "动作",
    "actions": "动作列表",
    "kind": "类型",
    "value": "参数",
    "expected": "预期结果",
    "sensitive": "是否敏感",
    "route": "决策来源",
    "confidence": "置信度",
    "operation": "操作",
    "target": "目标元素",
    "ok": "是否成功",
    "args": "命令参数",
    "stdout": "标准输出",
    "stderr": "错误输出",
    "stdout_bytes": "标准输出字节数",
    "stderr_bytes": "错误输出字节数",
    "exit_code": "退出码",
    "code": "退出码",
    "preview": "结果预览",
    "attempt": "请求次数",
    "reason": "原因",
    "model": "模型",
    "provider": "模型服务",
    "provider_name": "模型服务",
    "body": "请求体",
    "timeout": "超时（秒）",
    "instructions": "系统提示词",
    "input": "输入",
    "system": "系统提示词",
    "enable_thinking": "是否启用思考",
    "summary": "总结",
    "iterations": "执行轮数",
    "message": "消息",
    "type": "类型",
    "event": "最终事件",
    "level": "级别",
    "advice": "反思建议",
    "recent_action": "上一次动作",
    "action_history": "动作历史",
    "force_reasoning": "是否强制推理",
    "skills": "技能",
    "memory": "记忆",
    "version": "观察版本",
    "loading": "是否加载中",
    "stable": "页面是否稳定",
    "extracted": "提取结果",
    "rationale": "决策理由",
    "terminal": "终止状态",
}
_CONTEXT: ContextVar[dict] = ContextVar("log_context", default={})
_CALLS = count(1)

# 页面快照只供决策和验收使用，不写入诊断日志。日志保留 URL、状态、
# 元素数量等足以定位问题的摘要；模型 IO 仍保留请求和响应的结构化摘要。
_WEB_CONTENT_KEYS = frozenset({"snapshot", "page_text", "diff", "elements"})
_MAX_LOG_TEXT = 1200


def set_context(**data: Any) -> None:
    """合并当前任务的日志关联字段。"""
    _CONTEXT.set({**_CONTEXT.get(), **data})


def current_iteration() -> int:
    """读取当前动作轮次，尚未执行动作时为零。"""
    return _CONTEXT.get().get("iteration", 0)


def _json_value(value: Any) -> Any:
    """将数据类和路径转换为可记录对象，不序列化任意实例的内部状态。"""
    class_name = type(value).__name__
    if class_name == "Observation":
        return {
            "url": value.url,
            "title": value.title,
            "changed": value.changed,
            "version": value.version,
            "loading": value.loading,
            "stable": value.stable,
            "element_count": len(value.elements),
            "has_page_text": bool(value.page_text),
            "has_diff": bool(value.diff),
            "extracted": value.extracted,
        }
    if class_name == "BrowserResult":
        args = tuple(value.args)
        is_snapshot = "snapshot" in args
        result = {
            "ok": value.ok,
            "args": list(args),
            "exit_code": value.exit_code,
            "ms": round(value.ms, 2),
            "stdout_bytes": len(value.stdout.encode("utf-8")),
            "stderr_bytes": len(value.stderr.encode("utf-8")),
        }
        # URL 查询和失败原因是核心结果；snapshot 命令的 stdout 永不落盘。
        if args == ("get", "url") and value.stdout:
            result["url"] = value.stdout.strip()[:500]
        elif not is_snapshot and value.stderr:
            result["error"] = value.stderr.strip()[:500]
        return result
    # 测试会话和兼容适配器可能返回轻量 Result 对象；同样只留命令结果摘要。
    if class_name == "Result" and hasattr(value, "ok"):
        return {
            "ok": bool(value.ok),
            "preview": str(getattr(value, "preview", ""))[:300],
        }
    if class_name == "Action":
        return {
            "kind": value.kind,
            "ref": value.ref,
            "expected": value.expected,
            "sensitive": value.sensitive,
            "observation_version": value.observation_version,
            "value": "<redacted>" if value.sensitive else value.value,
        }
    if is_dataclass(value) and not isinstance(value, type):
        to_dict = getattr(value, "to_dict", None)
        return to_dict() if callable(to_dict) else asdict(value)
    if isinstance(value, Path):
        return str(value)
    return f"<{type(value).__name__}>"


def _compact_log_value(value: Any, *, parent_key: str = "") -> Any:
    """压缩日志数据并移除网页正文，避免把快照重复写入每个模块日志。"""
    if isinstance(value, dict):
        compact = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text in _WEB_CONTENT_KEYS:
                continue
            # JEV 的 state.page.text 也是网页正文，但普通 text 可能是用户输入
            # 或模型输出，因此只在 page/observation 上下文中移除。
            if key_text == "text" and parent_key in {"page", "observation", "before", "after"}:
                continue
            compact[key_text] = _compact_log_value(item, parent_key=key_text)
        return compact
    if isinstance(value, list):
        return [_compact_log_value(item, parent_key=parent_key) for item in value]
    if isinstance(value, tuple):
        return [_compact_log_value(item, parent_key=parent_key) for item in value]
    if isinstance(value, str):
        # 请求体中的 input 经常是 JSON 字符串；解析后才能去掉嵌套的 snapshot。
        stripped = value.strip()
        if parent_key == "input" and stripped[:1] in {"{", "["}:
            try:
                parsed = json.loads(stripped)
            except (TypeError, ValueError):
                parsed = None
            if isinstance(parsed, (dict, list)):
                return _compact_log_value(parsed, parent_key=parent_key)
        if len(value) > _MAX_LOG_TEXT:
            return f"<文本已省略，长度={len(value)} 字符>"
        return value
    return value


def trace(stage: str, **data: Any) -> None:
    """将请求、响应和错误信息合并到当前模块的诊断记录。"""
    frame = _FRAME.get()
    if frame is None:
        return
    if stage == "task.received":
        frame["task"] = data["task"]
    elif stage == "model.request":
        frame["request"] = data
    elif stage == "model.response.raw":
        frame["response"] = data.get("body")
    elif stage == "model.response.parsed":
        # Keep only one representation of the response.
        frame["response"] = data.get("data")
    elif stage == "model.response.http_error":
        frame["response"] = data
    elif stage == "executor.command":
        frame.setdefault("commands", []).append(data["command"])
    elif "error" in data:
        frame.setdefault("errors", []).append({"stage": stage, "error": data["error"]})


def timing(stage: str, started_at: float, **data: Any) -> float:
    """计算耗时，并将模型请求时间追加到当前模块的日志。"""
    ms = round((time.monotonic() - started_at) * 1000, 2)
    frame = _FRAME.get()
    if frame is not None and stage in _REQUEST_STAGES:
        frame.setdefault("request_timings", []).append(
            {"stage": stage, "ms": ms, **data}
        )
    return ms


@contextmanager
def measure(stage: str, **data: Any):
    """即使模块抛出异常也记录耗时，不额外发送界面事件。"""
    started_at = time.monotonic()
    try:
        yield
    finally:
        timing(stage, started_at, **data)


def timed(stage: str):
    """为整个函数记录耗时，包含失败和异常路径。"""

    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with measure(stage):
                return function(*args, **kwargs)

        return wrapped

    return decorate


def trace_exception(stage: str, exc: Exception) -> None:
    """记录异常类型与信息。"""
    trace(stage, error=str(exc), error_type=type(exc).__name__)


def traced(stage: str):
    """每层只输出一组完整的输入、结果与耗时，并恢复父级日志上下文。"""

    def decorate(function):
        signature = inspect.signature(function)

        @wraps(function)
        def wrapped(*args, **kwargs):
            if stage not in _LAYERS:
                return function(*args, **kwargs)
            inputs = dict(signature.bind(*args, **kwargs).arguments)
            inputs.pop("self", None)
            context = _CONTEXT.get()
            extra = {"call_id": next(_CALLS), "parent_call_id": context.get("call_id")}
            if "step" in inputs and hasattr(inputs["step"], "id"):
                extra["step_id"] = inputs["step"].id
            token = _CONTEXT.set({**context, **extra})
            parent = _FRAME.get()
            frame = {}
            frame_token = _FRAME.set(frame)
            started = time.monotonic()
            output = {}
            try:
                output["result"] = function(*args, **kwargs)
                return output["result"]
            except Exception as exc:
                output.update(error=str(exc), error_type=type(exc).__name__)
                raise
            finally:
                if stage.startswith("model.") and "error" not in output:
                    frame.pop("response", None)
                if "request" in frame:
                    # The actual request contains the complete model input.
                    inputs = {}
                entry = dict(
                    stage=stage,
                    layer=_LAYERS[stage],
                    inputs=inputs,
                    **output,
                    **frame,
                    ms=round((time.monotonic() - started) * 1000, 2),
                )
                if stage.startswith("model.") and parent is not None:
                    parent.setdefault("requests", []).append(entry)
                else:
                    _record("layer", **entry)
                _FRAME.reset(frame_token)
                _CONTEXT.reset(token)

        return wrapped

    return decorate


def _format_readable(entry: dict) -> str:
    """把嵌套数据转成中文字段日志，长文本移到独立段落。"""
    stage = entry.get("stage", entry["kind"])
    header = f"[{entry['time']}] {entry.get('layer', stage)} | 耗时 {entry.get('ms', 0):,.2f} 毫秒"
    for key in ("session_id", "step_id", "iteration"):
        if key in entry:
            header += f" | {_FIELD_LABELS.get(key, key)}={entry[key]}"
    # Normalize dataclasses once so nested fields can also be rendered as text.
    # Header already carries timing and correlation; do not repeat metadata.
    details = {
        key: value
        for key, value in entry.items()
        if key
        not in {
            "time",
            "pid",
            "kind",
            "stage",
            "layer",
            "ms",
            "session_id",
            "step_id",
            "iteration",
            "call_id",
            "parent_call_id",
        }
    }
    normalized = json.loads(
        json.dumps(details, ensure_ascii=False, default=_json_value)
    )
    # 生产日志只由 layer/event 记录；trace 保留给旧的格式化接口测试和离线诊断
    # 使用，避免改变它对任意原始字段的通用语义。
    if entry.get("kind") != "trace":
        normalized = _compact_log_value(normalized)
    blocks = []

    def expand(value, path=""):
        if isinstance(value, dict):
            return {
                _FIELD_LABELS.get(key, key): expand(
                    _STAGE_LABELS.get(item, item)
                    if key == "stage" and isinstance(item, str)
                    else item,
                    f"{path}.{_FIELD_LABELS.get(key, key)}"
                    if path
                    else _FIELD_LABELS.get(key, key),
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [
                expand(item, f"{path}[{index}]") for index, item in enumerate(value)
            ]
        if isinstance(value, str):
            # 请求/响应的多行 JSON 保留为独立段落，网页正文已经在压缩阶段移除。
            display = value
            try:
                parsed = json.loads(value)
                if isinstance(parsed, (dict, list)):
                    display = json.dumps(parsed, ensure_ascii=False, indent=2)
            except (ValueError, TypeError):
                pass
            if "\n" in display:
                blocks.append(f"--- {path} ---\n{display}")
                return f"[见下方 {path}]"
        return value

    body = json.dumps(expand(normalized), ensure_ascii=False, indent=2)
    return "\n".join(["=" * 88, header, body, *blocks]) + "\n\n"


def _record(kind: str, **data: Any) -> None:
    """每个代理进程写独立日志，不依赖工作目录或 Electron 配置目录。"""
    entry = {
        "time": datetime.now(timezone.utc).isoformat(),
        "pid": os.getpid(),
        **_CONTEXT.get(),
        "kind": kind,
        **data,
    }
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(_format_readable(entry))
    except (OSError, TypeError, ValueError) as exc:
        # Keep the task/event protocol alive if the installation is read-only.
        print(
            f"[koi-agent] cannot write {LOG_PATH}: {exc}; {entry!r}",
            file=sys.stderr,
            flush=True,
        )


def record_event(event: dict[str, Any]) -> None:
    """把最终事件并入当前调用日志；没有调用上下文时单独记录。"""
    if event["type"] not in {"done", "error", "notify"}:
        return

    frame = _FRAME.get()
    if frame is not None:
        frame.setdefault("messages", []).append(event)
    else:
        _record("event", event=event)


def debug(message: str) -> None:
    """保留流程提示入口；详细输入、结果和耗时由 traced 统一记录。"""
    # 避免重复输出已有调用日志中的中间提示。
    pass
