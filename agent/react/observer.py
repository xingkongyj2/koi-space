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
        page_text, full_ok = self._capture_full_text()
        result, snapshot = self._capture_interactive()
        current_url = self.session.current_url()
        url, page_text, stable = self._capture_stability(
            url, current_url, page_text, result.ok and full_ok
        )
        diff, page_changed = self._record_change(snapshot, page_text)
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
        return self._build_observation(
            url,
            snapshot,
            diff,
            elements,
            page_text,
            loading,
            stable,
            page_changed,
        )

    def _capture_full_text(self) -> tuple[str, bool]:
        """按配置捕获正文快照，返回正文和捕获是否成功。"""
        if not self.include_full:
            return "", True
        full = self.session.run(["snapshot"], timeout=30)
        return (full.stdout if full.ok else ""), full.ok

    def _capture_interactive(self):
        """最后捕获交互快照，确保动作引用对应最新编号。"""
        result = self.session.run(["snapshot", "-i"], timeout=30)
        return result, result.stdout if result.ok else result.preview

    @staticmethod
    def _capture_stability(before_url, current_url, page_text, stable):
        """导航发生在双快照之间时丢弃旧正文，避免混页验收。"""
        if current_url == before_url:
            return before_url, page_text, stable
        logger.trace(
            "observer.page_navigated_during_capture",
            before=before_url,
            after=current_url,
        )
        return current_url, "", False

    def _record_change(self, snapshot, page_text):
        """计算有限差异并更新下次观察使用的基线。"""
        diff = self.diff(self._last_snapshot, snapshot)
        page_changed = page_text != self._last_page_text
        self._last_snapshot = snapshot
        self._last_page_text = page_text
        return diff, page_changed

    def _build_observation(
        self, url, snapshot, diff, elements, page_text, loading, stable, page_changed
    ) -> Observation:
        """把捕获结果装配成不可变观察对象。"""
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
