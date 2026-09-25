"""Append-only task memory with simple local retrieval."""
from __future__ import annotations

import json
import time
from pathlib import Path


class Memory:
    def __init__(self, path=None) -> None:
        self.path = Path(path).expanduser() if path else None

    def write(self, kind: str, data: dict) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"kind": kind, "time": time.time(), "data": data}
        with self.path.open("a", encoding="utf8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def search(self, query: str, limit: int = 5) -> list[dict]:
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
        self.write("success", {"goals": [step.goal for step in plan.steps], "steps": steps})

    def record_failure(self, reason: str) -> None:
        self.write("failure", {"reason": reason})
