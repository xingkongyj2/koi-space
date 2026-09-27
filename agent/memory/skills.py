"""持久化可复用的技能动作，执行时将语义目标重新绑定到当前引用。"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

from logger import logger
from react.executor import Action


class SkillLibrary:
    def __init__(self, path=None) -> None:
        self.path = Path(path).expanduser() if path else None
        self._items = []
        self._load()

    def _load(self) -> None:
        """读取技能记录，单行损坏不影响其他技能加载。"""
        if not self.path or not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf8").splitlines():
            try:
                self._items.append(json.loads(line))
            except ValueError:
                continue

    @logger.traced("skills.match")
    def match(self, goal: str, url: str = ""):
        """按目标词和精确主机名选择匹配的技能。"""
        terms = set(re.findall(r"\w+", goal.lower()))
        best = None
        for item in self._items:
            score = len(terms & set(item.get("terms", ())))
            if url and item.get("host") and item["host"] != urlsplit(url).hostname:
                continue
            if score > 0 and (best is None or score > best[0]):
                best = (score, item)
        return best[1] if best else None

    @logger.traced("skills.save")
    def save(self, goal: str, actions, url: str = "") -> None:
        """追加保存技能动作；跨观察的元素操作还需要可重绑的语义目标。"""
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
        """读取旧格式技能动作，调度器实际使用 next_action 逐次安全绑定。"""
        return tuple(Action(**action) for action in item.get("actions", ()))

    @staticmethod
    def next_action(item, index, observation):
        """技能只能保存语义目标，历史 ref 不允许跨观察直接复用。"""
        actions = item.get("actions", [])
        if index >= len(actions):
            return None

        spec = dict(actions[index])
        target_text = spec.pop("target_text", "")
        if spec.get("ref") or target_text:
            if not target_text:
                return None
            matches = [
                element
                for element in observation.elements
                if element.get("text") == target_text
            ]
            if len(matches) != 1:
                return None
            spec["ref"] = matches[0]["ref"]

        try:
            action = Action(**spec)
        except (TypeError, ValueError):
            return None
        return replace(action, observation_version=observation.version)
