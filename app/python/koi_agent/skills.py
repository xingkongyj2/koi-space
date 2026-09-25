"""Persistent successful action chains for zero-model repeated tasks."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .executor import Action


class SkillLibrary:
    def __init__(self, path=None) -> None:
        self.path = Path(path).expanduser() if path else None
        self._items = []
        self._load()

    def _load(self) -> None:
        if not self.path or not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf8").splitlines():
            try:
                self._items.append(json.loads(line))
            except ValueError:
                continue

    def match(self, goal: str, url: str = ""):
        terms = set(re.findall(r"\w+", goal.lower()))
        best = None
        for item in self._items:
            score = len(terms & set(item.get("terms", ())))
            if url and item.get("host") and item["host"] not in url:
                score -= 2
            if score and (best is None or score > best[0]):
                best = (score, item)
        return best[1] if best else None

    def save(self, goal: str, actions, url: str = "") -> None:
        if not self.path:
            return
        item = {
            "terms": list(set(re.findall(r"\w+", goal.lower()))),
            "host": url.split("/")[2] if "//" in url else "",
            "actions": [action.__dict__ for action in actions],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf8") as handle:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")

    @staticmethod
    def actions(item) -> tuple[Action, ...]:
        return tuple(Action(**action) for action in item.get("actions", ()))
