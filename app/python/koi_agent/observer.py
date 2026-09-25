"""Page observation, interactive element extraction and incremental diffs."""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import protocol


@dataclass(frozen=True)
class Observation:
    url: str
    title: str
    snapshot: str
    diff: str
    changed: bool
    elements: tuple[dict, ...] = ()


class Observer:
    def __init__(self, session) -> None:
        self.session = session
        self._last_snapshot = ""

    def capture(self) -> Observation:
        url = self.session.current_url()
        result = self.session.run(["snapshot", "-i"], timeout=30)
        snapshot = result.stdout if result.ok else result.preview
        diff = self.diff(self._last_snapshot, snapshot)
        self._last_snapshot = snapshot
        elements = tuple(self._elements(snapshot))
        protocol.log(
            f"flow=observe url={url or '<unknown>'} "
            f"bytes={len(snapshot)} elements={len(elements)} changed={bool(diff)}"
        )
        return Observation(url, "", snapshot, diff, bool(diff), elements)

    @staticmethod
    def _elements(snapshot: str):
        """Convert agent-browser refs into a compact decision table."""
        for line in snapshot.splitlines():
            match = re.search(r"\[ref=([\w-]+)\]|(@[\w-]+)", line)
            if match:
                ref = match.group(1) or match.group(2).lstrip("@")
                label = line.replace(match.group(0), "").strip(" -")
                yield {"ref": f"@{ref}", "text": label[:240]}

    @staticmethod
    def diff(old: str, new: str) -> str:
        if not old:
            return new[:3000]
        if old == new:
            return ""
        old_lines, new_lines = set(old.splitlines()), set(new.splitlines())
        added = [f"+{line}" for line in new_lines - old_lines][:80]
        removed = [f"-{line}" for line in old_lines - new_lines][:80]
        return "\n".join(added + removed)
