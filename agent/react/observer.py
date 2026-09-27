"""捕获页面证据、解析当前交互引用，并生成增量差异与稳定指纹。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

from logger import logger


@dataclass(frozen=True)
class Observation:
    url: str
    title: str
    snapshot: str
    diff: str
    changed: bool
    elements: tuple[dict, ...] = ()
    page_text: str = ""
    version: int = 0
    loading: bool = False
    stable: bool = True
    extracted: dict = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        """忽略 ref 编号，保留 URL、正文、控件值及页面状态等验收证据。"""
        text = re.sub(r"\[ref=[\w-]+\]", "", self.snapshot + "\n" + self.page_text)
        payload = (
            self.url,
            text,
            self.loading,
            self.stable,
            self.extracted,
            [item.get("text", "") for item in self.elements],
        )
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()


class Observer:
    def __init__(self, session, *, include_full: bool = False) -> None:
        self.session = session
        self.include_full = include_full
        self._version = 0
        self._last_snapshot = ""
        self._last_page_text = ""

    @logger.traced("observer.capture")
    def capture(self) -> Observation:
        """先按需捕获正文，再生成最终交互引用，避免完整快照使 ref 失效。"""
        url = self.session.current_url()
        page_text = ""
        if self.include_full:
            full = self.session.run(["snapshot"], timeout=30)
            page_text = full.stdout if full.ok else ""
        # 交互快照必须最后抓取：完整快照可能重编号，动作只能使用最新映射。
        result = self.session.run(["snapshot", "-i"], timeout=30)
        snapshot = result.stdout if result.ok else result.preview
        stable = result.ok and (full.ok if self.include_full else True)
        current_url = self.session.current_url()
        if current_url != url:
            logger.trace(
                "observer.page_navigated_during_capture", before=url, after=current_url
            )
            stable = False
            url = current_url
            page_text = ""  # 导航前抓取的正文不能当成新页面证据。
        diff = self.diff(self._last_snapshot, snapshot)
        page_changed = page_text != self._last_page_text
        self._last_snapshot = snapshot
        self._last_page_text = page_text
        elements = tuple(self._elements(snapshot))
        logger.debug(
            f"flow=observe url={url or '<unknown>'} "
            f"bytes={len(snapshot)} full_bytes={len(page_text)} "
            f"elements={len(elements)} changed={bool(diff) or page_changed}"
        )
        # 完整观察已由装饰器记录；这里只写摘要，不为每次抓取增加界面事件。
        logger.trace(
            "observer.summary",
            url=url,
            ok=result.ok,
            diff=diff,
            element_count=len(elements),
            page_changed=page_changed,
        )
        self._version += 1
        loading = bool(re.search(r'\[busy(?:=true)?\]|aria-busy="true"', snapshot))
        return Observation(
            url,
            "",
            snapshot,
            diff,
            bool(diff) or page_changed,
            elements,
            page_text,
            self._version,
            loading,
            stable,
        )

    @staticmethod
    def _elements(snapshot: str):
        """将交互快照中的 ref 转换成紧凑的当前元素表。"""
        for line in snapshot.splitlines():
            match = re.search(r"\[ref=([\w-]+)\]|(@[\w-]+)", line)
            if match:
                ref = match.group(1) or match.group(2).lstrip("@")
                label = line.replace(match.group(0), "").strip(" -")
                yield {"ref": f"@{ref}", "text": label[:240]}

    @staticmethod
    def diff(old: str, new: str) -> str:
        """比较前后快照行，按稳定顺序输出有限长度的新增和删除项。"""
        if not old:
            return new[:3000]
        if old == new:
            return ""
        old_lines, new_lines = set(old.splitlines()), set(new.splitlines())
        added = [f"+{line}" for line in sorted(new_lines - old_lines)][:80]
        removed = [f"-{line}" for line in sorted(old_lines - new_lines)][:80]
        return "\n".join(added + removed)
