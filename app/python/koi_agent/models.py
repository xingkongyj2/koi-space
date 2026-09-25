"""OpenAI Responses client and Jev's typed browser decision adapter."""
from __future__ import annotations

import json
import urllib.request

from .config import Provider


class ModelError(RuntimeError):
    """Raised when a configured model cannot return a usable response."""


class OpenAICompatible:
    """Minimal dependency-free client for the Responses protocol."""

    def __init__(self, provider: Provider) -> None:
        self.provider = provider

    def chat(self, system: str, user: str) -> str:
        return self.responses(system, user)

    def responses(self, system: str, user: str) -> str:
        body = {
            "model": self.provider.model,
            "input": f"{system}\n\n用户任务：\n{user}",
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
            with urllib.request.urlopen(request, timeout=self.provider.timeout) as response:
                data = json.load(response)
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

    def __init__(self, provider: Provider) -> None:
        self.client = OpenAICompatible(provider)

    def choose(self, goal: str, snapshot: str) -> dict:
        prompt = json.dumps(
            {
                "task": goal,
                "elements": snapshot,
                "operations": [
                    "CLICK", "TYPE_TEXT", "SELECT", "SCROLL",
                    "WAIT", "DONE", "BLOCKED",
                ],
            },
            ensure_ascii=False,
        )
        raw = self.client.chat(
            "Choose exactly one browser operation and target. Return JSON.",
            prompt,
        )
        return json.loads(raw)
