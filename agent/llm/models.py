"""Responses 文本模型客户端与 JEV 类型化决策适配器。"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import asdict
from urllib.parse import urlsplit

from config.config import Provider
from logger import logger
from react.budget import BudgetExceeded, before_model_request, model_scope, record_usage


class ModelError(RuntimeError):
    """模型请求失败或返回内容无法使用。"""


@logger.traced("model.http")
def _request_json(
    request: urllib.request.Request, body: dict, *, provider_name: str, timeout: float
) -> dict:
    # 只记录请求类型和业务载荷，认证头不能写入日志。
    """发送一次模型请求，记录真实调用预算、网络耗时及服务端用量。"""
    logger.trace(
        "model.request",
        url=request.full_url,
        body=body,
        provider=provider_name,
        timeout=timeout,
    )
    timeout = before_model_request(timeout)
    network_started = time.monotonic()
    try:
        raw, status = _http_response(request, timeout)
    except urllib.error.HTTPError as exc:
        raise _model_http_error(exc, provider_name) from exc
    finally:
        logger.timing(
            "model.http.network",
            network_started,
            provider=provider_name,
            endpoint=urlsplit(request.full_url).path,
        )
    logger.trace("model.response.raw", status=status, body=raw)
    data = json.loads(raw)
    record_usage(data)
    logger.trace("model.response.parsed", data=data)
    return data


def _http_response(request, timeout):
    """读取 HTTP 响应正文和状态码，保留原始 JSON 供上层解析。"""
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace"), response.status


def _model_http_error(exc: urllib.error.HTTPError, provider_name: str) -> ModelError:
    """解析服务端错误并转换成不泄露认证信息的统一异常。"""
    error_body = exc.read().decode("utf-8", errors="replace")
    logger.trace("model.response.http_error", status=exc.code, body=error_body)
    try:
        error = json.loads(error_body).get("error", {})
        message = str(error.get("message") or "") if isinstance(error, dict) else ""
        code = str(error.get("code") or "") if isinstance(error, dict) else ""
    except (ValueError, AttributeError):
        message, code = "", ""
    if "free quota exhausted" in message.lower():
        message = "免费额度已耗尽：请在模型服务控制台检查额度和‘仅使用免费额度’设置，或更换可用模型。"
    detail = f": {message[:1000]}" if message else ""
    return ModelError(f"{provider_name} HTTP {exc.code} {code}{detail}")


class OpenAICompatible:
    """仅使用标准库实现 Responses 协议客户端。"""

    def __init__(self, provider: Provider) -> None:
        self.provider = provider

    def chat(self, system: str, user_input: str) -> str:
        """保留通用文本模型调用接口，底层统一使用 Responses。"""
        return self.responses(system, user_input)

    @logger.traced("model.responses")
    def responses(self, system: str, user_input: str) -> str:
        """调用 Responses 接口并拼接文本输出，不返回模型的内部推理信息。"""
        body = self._response_body(system, user_input)
        request = self._response_request(body)
        try:
            data = _request_json(
                request,
                body,
                provider_name=self.provider.name,
                timeout=self.provider.timeout,
            )
            return self._response_text(data)
        except (ModelError, BudgetExceeded):
            raise
        except Exception as exc:
            raise ModelError(
                f"{self.provider.name} responses request failed: {exc}"
            ) from exc

    def _response_body(self, system: str, user_input: str) -> dict:
        """构造 Responses 请求体，关闭内部推理输出。"""
        return {
            "model": self.provider.model,
            "instructions": system,
            "input": user_input,
            "enable_thinking": False,
        }

    def _response_request(self, body: dict) -> urllib.request.Request:
        """构造带认证头的 Responses 请求。"""
        return urllib.request.Request(
            f"{self.provider.base_url.rstrip('/')}/responses",
            json.dumps(body, ensure_ascii=False).encode("utf8"),
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.provider.api_key}",
            },
        )

    def _response_text(self, data: dict) -> str:
        """从 Responses 信封提取消息文本，拒绝空输出。"""
        texts = []
        for item in data.get("output", []):
            if item.get("type") != "message":
                continue
            for part in item.get("content", []):
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    texts.append(part["text"])
        if not texts and isinstance(data.get("output_text"), str):
            texts.append(data["output_text"])
        if not texts:
            raise ModelError(f"{self.provider.name} returned no message output")
        return "\n".join(texts)


class JevDecision:
    """让 JEV 从当前可见元素和有限操作中选择浏览器动作。"""

    def __init__(
        self, provider: Provider, text_model: OpenAICompatible | None = None
    ) -> None:
        self.provider = provider
        self.text_model = text_model

    @logger.timed("decision.jev.total")
    @logger.traced("model.jev")
    def choose(
        self,
        goal: str,
        observation,
        *,
        success_criteria=(),
        recent_action=None,
        action_history=(),
        advice="",
    ) -> dict:
        """构建 JEV 请求、校验选择，并返回一个结构化动作。"""
        elements = list(observation.elements)
        if not elements:
            raise ModelError("Jev needs interactive elements in the current snapshot")
        click_targets, text_targets = self._targets(elements)
        operations = self._operations(text_targets)
        questions = self._questions(
            goal,
            success_criteria,
            recent_action,
            action_history,
            advice,
            click_targets,
            text_targets,
            operations,
        )
        body = self._request_body(
            observation,
            success_criteria,
            action_history,
            recent_action,
            click_targets,
            questions,
        )
        data = self._request(body)
        return self._parse_answer(
            data,
            operations,
            questions,
            elements,
            click_targets,
            text_targets,
            goal,
            recent_action,
        )

    @staticmethod
    def _targets(elements):
        """按可见顺序建立点击目标和可编辑目标索引。"""
        click_targets = {str(i): element for i, element in enumerate(elements, 1)}
        text_targets = {
            index: element
            for index, element in click_targets.items()
            if any(
                role in element["text"].lower()
                for role in ("textbox", "searchbox", "input", "combobox")
            )
        }
        return click_targets, text_targets

    def _operations(self, text_targets):
        """返回 JEV 可选操作，并按配置决定是否开放文本输入。"""
        operations = {
            "CLICK": "Click one of the observed interactive elements.",
            "PRESS": "Press a keyboard key in the currently focused control to navigate or confirm a visible selection, or dismiss a popup.",
            "WAIT": "Wait for the page to finish loading.",
            "DONE": "All supplied acceptance criteria are visibly satisfied and the user goal is complete.",
            "BLOCKED": "No safe supported action can make progress.",
        }
        if text_targets and self.text_model:
            operations["TYPE_TEXT"] = "Enter text into an observed editable field."
        return operations

    @staticmethod
    def _questions(
        goal,
        success_criteria,
        recent_action,
        action_history,
        advice,
        click_targets,
        text_targets,
        operations,
    ):
        """构建 JEV 的操作、按键和目标选择题。"""
        questions = {
            "operation": JevDecision._operation_question(
                goal, success_criteria, recent_action, advice, operations
            ),
            "click_target": JevDecision._target_question(goal, "CLICK", click_targets),
            "press_key": JevDecision._press_question(goal),
        }
        if "TYPE_TEXT" in operations:
            questions["type_text_target"] = JevDecision._target_question(
                goal, "TYPE_TEXT", text_targets
            )
        return questions

    @staticmethod
    def _operation_question(goal, criteria, recent_action, advice, operations):
        """构造动作选择题和验收规则。"""
        return {
            "type": "choice",
            "criteria": operations,
            "instructions": {
                "goal": goal,
                "acceptance_criteria": list(criteria),
                "recent_action": asdict(recent_action) if recent_action is not None else None,
                "advice": advice,
                "rules": (
                    "Choose DONE only when the acceptance criteria and visible goal evidence are satisfied. "
                    "Typing into an autocomplete or combobox does not confirm its underlying selection. "
                    "Observe and select a matching visible option before moving to another field or submitting. "
                    "Use keyboard navigation only in the currently focused control; Enter must confirm an "
                    "observed matching selection, not blindly submit a form. Read validation errors and repair "
                    "the affected field. Wait only with evidence of loading. Do not invent targets, parameters "
                    "or hidden side effects."
                ),
            },
        }

    @staticmethod
    def _target_question(goal, operation, targets):
        """构造点击或输入控件选择题。"""
        return {
            "type": "choice",
            "criteria": {index: {"element": element["text"]} for index, element in targets.items()},
            "instructions": {"goal": goal, "operation": operation},
        }

    @staticmethod
    def _press_question(goal):
        """构造受限键盘操作选择题。"""
        return {
            "type": "choice",
            "criteria": {
                "ArrowDown": "Move to the next visible option in the focused control.",
                "ArrowUp": "Move to the previous visible option in the focused control.",
                "Enter": "Confirm an observed matching option in the focused control.",
                "Escape": "Dismiss the current popup.",
                "Tab": "Move focus to the next control.",
            },
            "instructions": {
                "goal": goal,
                "operation": "PRESS",
                "rules": "Choose a key for the current focused control based on visible evidence. Do not submit an unconfirmed form.",
            },
        }

    def _request_body(
        self, observation, success_criteria, action_history, recent_action,
        click_targets, questions
    ):
        """组装发送给 JEV 的当前页面和有限操作状态。"""
        return {
            "model": self.provider.model,
            "state": {
                "page": {
                    "url": observation.url,
                    "title": observation.title,
                    "text": (observation.page_text or observation.snapshot)[:8000],
                },
                "elements": [
                    {"index": index, "label": element["text"], "operations": ["CLICK"]}
                    for index, element in click_targets.items()
                ],
                "recent_actions": (
                    list(action_history)[-6:]
                    or ([asdict(recent_action)] if recent_action is not None else [])
                ),
                "acceptance_criteria": list(success_criteria),
            },
            "questions": questions,
        }

    def _request(self, body):
        """发送一次 JEV 请求，并把网络异常转换为统一模型错误。"""
        request = urllib.request.Request(
            f"{self.provider.base_url.rstrip('/')}/systemone",
            json.dumps(body, ensure_ascii=False).encode("utf8"),
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.provider.api_key}",
            },
        )
        try:
            with logger.measure("decision.jev.request", model=self.provider.model):
                return _request_json(
                    request,
                    body,
                    provider_name=self.provider.name,
                    timeout=self.provider.timeout,
                )
        except BudgetExceeded:
            raise
        except Exception as exc:
            raise ModelError(f"Jev systemone request failed: {exc}") from exc

    def _parse_answer(
        self, data, operations, questions, elements, click_targets,
        text_targets, goal, recent_action
    ):
        """校验操作、目标和输入内容，生成执行器使用的有限结果。"""
        answers = data.get("answers") or {}
        operation_answer = answers.get("operation") or {}
        operation = operation_answer.get("choice")
        if operation not in operations:
            raise ModelError("Jev selected an unsupported operation")
        result = {
            "operation": operation,
            "confidence": operation_answer.get("confidence", 0),
        }
        if operation == "PRESS":
            result["value"] = self._press_value(
                answers, questions, elements, recent_action
            )
        if operation in {"CLICK", "TYPE_TEXT"}:
            result["target"] = self._target_value(
                answers, operation, click_targets, text_targets
            )
        if operation == "TYPE_TEXT":
            result["text"] = self._text_value(goal, text_targets, result["target"])
        return result

    def _press_value(self, answers, questions, elements, recent_action):
        """校验按键选择，并阻止未确认联想候选直接提交。"""
        key = (answers.get("press_key") or {}).get("choice")
        if key not in questions["press_key"]["criteria"]:
            raise ModelError("Jev selected an unsupported key")
        if key == "Enter" and recent_action is not None and recent_action.kind in {"fill", "type"}:
            self._validate_enter_selection(elements, recent_action)
        return key

    @staticmethod
    def _validate_enter_selection(elements, recent_action):
        """只有存在匹配且已选中或聚焦的候选时，才允许回车确认。"""
        field = next(
            (item["text"] for item in elements if item.get("ref") == recent_action.ref),
            "",
        )
        selection_control = any(
            token in field.lower()
            for token in ("combobox", "autocomplete", "回车键选中", "上下键进行选择")
        )
        selected_options = [
            item for item in elements if re.search(r"\b(option|listitem|button)\b", item["text"], re.I)
            and re.search(r"\[(?:selected|focused)(?:=true)?\]", item["text"], re.I)
        ]
        matching_option = any(
            recent_action.value
            and recent_action.value in item["text"]
            and re.search(r"\b(option|listitem|button)\b", item["text"], re.I)
            and re.search(r"\[(?:selected|focused)(?:=true)?\]", item["text"], re.I)
            for item in elements
        )
        if selection_control and selected_options and not matching_option:
            raise ModelError(
                "Cannot confirm a selection control with Enter without an observed matching option that is selected or focused; inspect the candidates and use another interaction."
            )

    @staticmethod
    def _target_value(answers, operation, click_targets, text_targets):
        """把 JEV 选择题中的序号映射回当前观察的元素引用。"""
        targets = click_targets if operation == "CLICK" else text_targets
        target_answer = answers.get(operation.lower() + "_target") or {}
        target = targets.get(str(target_answer.get("choice")))
        if target is None:
            raise ModelError("Jev selected an unknown element")
        return target["ref"]

    def _text_value(self, goal, text_targets, target_ref):
        """调用文本模型生成输入值，并校验结果非空。"""
        target = next(
            (item for item in text_targets.values() if item.get("ref") == target_ref),
            None,
        )
        if target is None:
            raise ModelError("Jev selected an unknown text field")
        prompt = json.dumps({"goal": goal, "field": target["text"]}, ensure_ascii=False)
        with model_scope("jev_text"), logger.measure("decision.jev.type_text_model_request"):
            raw = self.text_model.chat(
                'Return only JSON with one key: {"text":"value to enter"}. Never include credentials.',
                prompt,
            )
        value = json.loads(raw).get("text")
        if not isinstance(value, str) or not value.strip():
            raise ModelError("text model returned no field value")
        return value
