"""追加式任务记忆：保存执行结果，并按关键词检索既有经验。"""

from __future__ import annotations

import json
import time
from pathlib import Path

from logger import logger


class Memory:
    def __init__(self, path=None) -> None:
        self.path = Path(path).expanduser() if path else None

    @logger.traced("memory.write")
    def write(self, kind: str, data: dict) -> None:
        """只追加一条时间戳记录，不改写既有执行历史。"""
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"kind": kind, "time": time.time(), "data": data}
        with self.path.open("a", encoding="utf8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def search(self, query: str, limit: int = 5) -> list[dict]:
        """按关键词命中数量检索记忆，跳过损坏的 JSON 行。"""
        if not self.path or not self.path.exists():
            return []
        terms = set(query.lower().split())
        hits = []
        for line in self.path.read_text(encoding="utf8").splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            text = json.dumps(item, ensure_ascii=False).lower()
            score = sum(term in text for term in terms)
            if score:
                hits.append((score, item))
        hits.sort(key=lambda item: item[0], reverse=True)
        return [item for _, item in hits[:limit]]

    def record_success(self, plan, steps) -> None:
        """保存已验证的计划目标与完成步骤。"""
        self.write(
            "success", {"goals": [step.goal for step in plan.steps], "steps": steps}
        )

    def record_failure(self, reason: str) -> None:
        """保存失败原因，供后续恢复或重规划参考。"""
        self.write("failure", {"reason": reason})
