"""OpenAI Responses client and Jev's typed browser decision adapter."""
from __future__ import annotations

from . import protocol

import json
import re
import time
import urllib.request
import urllib.error
from dataclasses import asdict
from urllib.parse import urlsplit

from .config import Provider


class ModelError(RuntimeError):
    """Raised when a configured model cannot return a usable response."""


@protocol.traced("model.http")
def _request_json(request: urllib.request.Request, body: dict, *, provider_name: str, timeout: float) -> dict:
    # Request is logged only as its type; never serialize authentication headers.
    protocol.trace("model.request", url=request.full_url, body=body, provider=provider_name, timeout=timeout)
    network_started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            status = response.status
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        protocol.trace("model.response.http_error", status=exc.code, body=error_body)
        try:
            error = json.loads(error_body).get("error", {})
            message = str(error.get("message") or "") if isinstance(error, dict) else ""
            code = str(error.get("code") or "") if isinstance(error, dict) else ""
        except (ValueError, AttributeError):
            message, code = "", ""
        if "free quota exhausted" in message.lower():
            message = "免费额度已耗尽：请在模型服务控制台检查额度和‘仅使用免费额度’设置，或更换可用模型。"
        detail = f": {message[:1000]}" if message else ""
        raise ModelError(f"{provider_name} HTTP {exc.code} {code}{detail}") from exc
    finally:
        protocol.timing("model.http.network", network_started, provider=provider_name,
                        endpoint=urlsplit(request.full_url).path)
    protocol.trace("model.response.raw", status=status, body=raw)
    data = json.loads(raw)
    protocol.trace("model.response.parsed", data=data)
    return data


class OpenAICompatible:
    """Minimal dependency-free client for the Responses protocol."""

    def __init__(self, provider: Provider) -> None:
        self.provider = provider

    def chat(self, system: str, user_input: str) -> str:
        return self.responses(system, user_input)

    @protocol.traced("model.responses")
    def responses(self, system: str, user_input: str) -> str:
        body = {
            "model": self.provider.model,
            "instructions": system,
            "input": user_input,
            "enable_thinking": False,
        }
        request = urllib.request.Request(
            f"{self.provider.base_url.rstrip('/')}/responses",
            json.dumps(body, ensure_ascii=False).encode("utf8"),
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.provider.api_key}",
            },
        )
        try:
            data = _request_json(request, body, provider_name=self.provider.name,
                                 timeout=self.provider.timeout)
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
        except ModelError:
            raise
        except Exception as exc:
            raise ModelError(f"{self.provider.name} responses request failed: {exc}") from exc


class JevDecision:
    """Use Jev as a small typed choice model for each browser step."""

    def __init__(self, provider: Provider, text_model: OpenAICompatible | None = None) -> None:
        self.provider = provider
        self.text_model = text_model

    @protocol.timed("decision.jev.total")
    @protocol.traced("model.jev")
    def choose(self, goal: str, observation, *, success_criteria=(), recent_action=None,
               action_history=(), advice="") -> dict:
        # TypeSafe System One selects from a finite set of observed actions.
        # It does not implement OpenAI's /responses endpoint or generate text.
        elements = list(observation.elements)
        if not elements:
            raise ModelError("Jev needs interactive elements in the current snapshot")

        click_targets = {str(i): element for i, element in enumerate(elements, 1)}
        text_targets = {
            index: element
            for index, element in click_targets.items()
            if any(role in element["text"].lower() for role in ("textbox", "searchbox", "input", "combobox"))
        }
        operations = {
            "CLICK": "Click one of the observed interactive elements.",
            "PRESS": "Press a keyboard key in the currently focused control to navigate or confirm a visible selection, or dismiss a popup.",
            "WAIT": "Wait for the page to finish loading.",
            "DONE": "All supplied acceptance criteria are visibly satisfied and the user goal is complete.",
            "BLOCKED": "No safe supported action can make progress.",
        }
        if text_targets and self.text_model:
            operations["TYPE_TEXT"] = "Enter text into an observed editable field."

        questions = {
            "operation": {
                "type": "choice",
                "criteria": operations,
                "instructions": {
                    "goal": goal,
                    "acceptance_criteria": list(success_criteria),
                    "recent_action": asdict(recent_action) if recent_action is not None else None,
                    "advice": advice,
                    "rules": ("Choose DONE only when the acceptance criteria and visible goal evidence are satisfied. "
                              "Typing into an autocomplete or combobox does not confirm its underlying selection. "
                              "Observe and select a matching visible option before moving to another field or submitting. "
                              "Use keyboard navigation only in the currently focused control; Enter must confirm an "
                              "observed matching selection, not blindly submit a form. Read validation errors and repair "
                              "the affected field. Wait only with evidence of loading. Do not invent targets, parameters "
                              "or hidden side effects."),
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
        questions["press_key"] = {
            "type": "choice",
            "criteria": {
                "ArrowDown": "Move to the next visible option in the focused control.",
                "ArrowUp": "Move to the previous visible option in the focused control.",
                "Enter": "Confirm an observed matching option in the focused control.",
                "Escape": "Dismiss the current popup.",
                "Tab": "Move focus to the next control.",
            },
            "instructions": {"goal": goal, "operation": "PRESS",
                             "rules": "Choose a key for the current focused control based on visible evidence. Do not submit an unconfirmed form."},
        }
        if "TYPE_TEXT" in operations:
            questions["type_text_target"] = {
                "type": "choice",
                "criteria": {
                    index: {"element": element["text"]}
                    for index, element in text_targets.items()
                },
                "instructions": {"goal": goal, "operation": "TYPE_TEXT"},
            }

        body = {
            "model": self.provider.model,
            "state": {
                "page": {"url": observation.url, "title": observation.title,
                         "text": (observation.page_text or observation.snapshot)[:8000]},
                "elements": [
                    {"index": index, "label": element["text"], "operations": ["CLICK"]}
                    for index, element in click_targets.items()
                ],
                "recent_actions": (list(action_history)[-6:] or
                                   ([asdict(recent_action)] if recent_action is not None else [])),
                "acceptance_criteria": list(success_criteria),
            },
            "questions": questions,
        }
        request = urllib.request.Request(
            f"{self.provider.base_url.rstrip('/')}/systemone",
            json.dumps(body, ensure_ascii=False).encode("utf8"),
            {"Content-Type": "application/json", "Authorization": f"Bearer {self.provider.api_key}"},
        )
        try:
            with protocol.measure("decision.jev.request", model=self.provider.model):
                data = _request_json(request, body, provider_name=self.provider.name,
                                     timeout=self.provider.timeout)
        except Exception as exc:
            raise ModelError(f"Jev systemone request failed: {exc}") from exc

        answers = data.get("answers") or {}
        operation_answer = answers.get("operation") or {}
        operation = operation_answer.get("choice")
        if operation not in operations:
            raise ModelError("Jev selected an unsupported operation")
        result = {"operation": operation, "confidence": operation_answer.get("confidence", 0)}
        if operation == "PRESS":
            key = (answers.get("press_key") or {}).get("choice")
            if key not in questions["press_key"]["criteria"]:
                raise ModelError("Jev selected an unsupported key")
            if key == "Enter" and recent_action is not None and recent_action.kind in {"fill", "type"}:
                field = next((item["text"] for item in elements
                              if item.get("ref") == recent_action.ref), "")
                selection_control = any(token in field.lower() for token in
                                        ("combobox", "autocomplete", "回车键选中", "上下键进行选择"))
                selected_options = [item for item in elements
                                    if re.search(r"\b(option|listitem|button)\b", item["text"], re.I)
                                    and re.search(r"\[(?:selected|focused)(?:=true)?\]", item["text"], re.I)]
                matching_option = any(
                    recent_action.value and recent_action.value in item["text"]
                    and re.search(r"\b(option|listitem|button)\b", item["text"], re.I)
                    and re.search(r"\[(?:selected|focused)(?:=true)?\]", item["text"], re.I)
                    for item in elements
                )
                if selection_control and selected_options and not matching_option:
                    raise ModelError("Cannot confirm a selection control with Enter without an observed matching option that is selected or focused; inspect the candidates and use another interaction.")
            result["value"] = key
        if operation in {"CLICK", "TYPE_TEXT"}:
            targets = click_targets if operation == "CLICK" else text_targets
            target_answer = answers.get(operation.lower() + "_target") or {}
            target = targets.get(str(target_answer.get("choice")))
            if target is None:
                raise ModelError("Jev selected an unknown element")
            result["target"] = target["ref"]
        if operation == "TYPE_TEXT":
            prompt = json.dumps({"goal": goal, "field": target["text"]}, ensure_ascii=False)
            with protocol.measure("decision.jev.type_text_model_request"):
                raw = self.text_model.chat(
                    'Return only JSON with one key: {"text":"value to enter"}. Never include credentials.',
                    prompt,
                )
            value = json.loads(raw).get("text")
            if not isinstance(value, str) or not value.strip():
                raise ModelError("text model returned no field value")
            result["text"] = value
        return result
