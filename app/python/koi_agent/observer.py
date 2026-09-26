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
    page_text: str = ""


class Observer:
    def __init__(self, session, *, include_full: bool = False) -> None:
        self.session = session
        self.include_full = include_full
        self._last_snapshot = ""
        self._last_page_text = ""

    @protocol.traced("observer.capture")
    def capture(self) -> Observation:
        url = self.session.current_url()
        page_text = ""
        if self.include_full:
            full = self.session.run(["snapshot"], timeout=30)
            page_text = full.stdout if full.ok else ""
        # Always obtain interactive refs last: agent-browser may renumber them
        # on each snapshot, and the action must use the most recent mapping.
        result = self.session.run(["snapshot", "-i"], timeout=30)
        snapshot = result.stdout if result.ok else result.preview
        if self.include_full:
            current_url = self.session.current_url()
            if current_url != url:
                protocol.trace("observer.page_navigated_during_capture",
                               before=url, after=current_url)
                url = current_url
                page_text = ""  # The full tree may describe the previous page.
        diff = self.diff(self._last_snapshot, snapshot)
        page_changed = page_text != self._last_page_text
        self._last_snapshot = snapshot
        self._last_page_text = page_text
        elements = tuple(self._elements(snapshot))
        protocol.log(
            f"flow=observe url={url or '<unknown>'} "
            f"bytes={len(snapshot)} full_bytes={len(page_text)} "
            f"elements={len(elements)} changed={bool(diff) or page_changed}"
        )
        # Full observations are already recorded by @traced. Keep this compact
        # summary in the local log instead of adding a renderer/SQLite event
        # on every capture; browser execution must not depend on chat updates.
        protocol.trace("observer.summary", url=url, ok=result.ok, diff=diff,
                       element_count=len(elements), page_changed=page_changed)
        return Observation(url, "", snapshot, diff, bool(diff) or page_changed, elements, page_text)

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
