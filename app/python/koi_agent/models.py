"""OpenAI Responses client and Jev's typed browser decision adapter."""
from __future__ import annotations

from . import protocol

import json
import urllib.request
import urllib.error

from .config import Provider


class ModelError(RuntimeError):
    """Raised when a configured model cannot return a usable response."""


@protocol.traced("model.http")
def _request_json(request: urllib.request.Request, body: dict, *, provider_name: str, timeout: float) -> dict:
    # Request is logged only as its type; never serialize authentication headers.
    protocol.trace("model.request", url=request.full_url, body=body, provider=provider_name, timeout=timeout)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            protocol.trace("model.response.raw", status=response.status, body=raw)
    except urllib.error.HTTPError as exc:
        protocol.trace("model.response.http_error", status=exc.code, body=exc.read().decode("utf-8", errors="replace"))
        raise
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
            "input": f"{system}\n\n用户任务：\n{user_input}",
            "enable_thinking": True,
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

    @protocol.traced("model.jev")
    def choose(self, goal: str, observation) -> dict:
        # TypeSafe System One selects from a finite set of observed actions.
        # It does not implement OpenAI's /responses endpoint or generate text.
        elements = list(observation.elements)
        if not elements:
            raise ModelError("Jev needs interactive elements in the current snapshot")

        click_targets = {str(i): element for i, element in enumerate(elements, 1)}
        text_targets = {
            index: element
            for index, element in click_targets.items()
            if any(role in element["text"].lower() for role in ("textbox", "searchbox", "input"))
        }
        operations = {
            "CLICK": "Click one of the observed interactive elements.",
            "WAIT": "Wait for the page to finish loading.",
            "DONE": "The goal is already visibly satisfied.",
            "BLOCKED": "No safe supported action can make progress.",
        }
        if text_targets and self.text_model:
            operations["TYPE_TEXT"] = "Enter text into an observed editable field."

        questions = {
            "operation": {
                "type": "choice",
                "criteria": operations,
                "instructions": {"goal": goal, "rules": "Choose one safe next action; do not invent a target."},
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
                "page": {"url": observation.url, "title": observation.title, "text": observation.snapshot[:8000]},
                "elements": [
                    {"index": index, "label": element["text"], "operations": ["CLICK"]}
                    for index, element in click_targets.items()
                ],
                "recent_actions": [],
            },
            "questions": questions,
        }
        request = urllib.request.Request(
            f"{self.provider.base_url.rstrip('/')}/systemone",
            json.dumps(body, ensure_ascii=False).encode("utf8"),
            {"Content-Type": "application/json", "Authorization": f"Bearer {self.provider.api_key}"},
        )
        try:
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
        if operation in {"CLICK", "TYPE_TEXT"}:
            targets = click_targets if operation == "CLICK" else text_targets
            target_answer = answers.get(operation.lower() + "_target") or {}
            target = targets.get(str(target_answer.get("choice")))
            if target is None:
                raise ModelError("Jev selected an unknown element")
            result["target"] = target["ref"]
        if operation == "TYPE_TEXT":
            prompt = json.dumps({"goal": goal, "field": target["text"]}, ensure_ascii=False)
            raw = self.text_model.chat(
                'Return only JSON with one key: {"text":"value to enter"}. Never include credentials.',
                prompt,
            )
            value = json.loads(raw).get("text")
            if not isinstance(value, str) or not value.strip():
                raise ModelError("text model returned no field value")
            result["text"] = value
        return result
