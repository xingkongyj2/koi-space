"""基于可见证据验收动作和结构化条件，不把命令成功当成业务成功。"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from . import protocol


@dataclass(frozen=True)
class ValidationResult:
    status: str
    evidence: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    def __bool__(self) -> bool:
        return self.passed


def url_matches(actual: str, expected: str) -> bool:
    """比较来源和路径边界，避免 example.com.evil 或 /orders-other 冒充目标。"""
    try:
        current, target = urlsplit(actual), urlsplit(expected)
        path = target.path.rstrip("/")
        return (
            current.scheme == target.scheme
            and current.hostname == target.hostname
            and current.port == target.port
            and (not path or current.path == path or current.path.startswith(path + "/"))
            and (not target.query or current.query == target.query)
        )
    except ValueError:
        return False


class Validator:
    def precondition(self, observation, action) -> ValidationResult:
        """执行前必须检查版本及 ref；未知引用不能交给浏览器猜测。"""
        from .executor import Executor

        if not observation.stable:
            return ValidationResult("unexpected_change", ("页面观察不稳定，需要刷新引用",))
        if action.kind not in Executor.ALLOWED:
            return ValidationResult("unexpected_change", ("动作不在封闭动作集中",))
        if action.observation_version != observation.version:
            return ValidationResult("unexpected_change", ("动作来自过期观察",))
        if (action.kind in {"click", "fill", "type"} and not action.ref) or (
            action.ref and action.ref not in {item.get("ref") for item in observation.elements}
        ):
            return ValidationResult("unexpected_change", ("元素引用不属于当前观察",))
        if action.kind == "open":
            from .planner import PlanError, _url

            try:
                _url(action.value, "action.url")
            except PlanError as exc:
                return ValidationResult("unexpected_change", (str(exc),))
        return ValidationResult("passed")

    @protocol.traced("validator.action")
    def action(self, before, after, action) -> ValidationResult:
        # 加载中不能当作 no-op，更不能据此确认步骤完成。
        if after.loading or not after.stable:
            return ValidationResult("loading", ("页面仍在加载或捕获期间发生导航",))

        changed = before.fingerprint != after.fingerprint
        if not changed:
            return ValidationResult("unchanged", ("页面语义指纹没有变化",))

        # expected 使用与规划器相同的条件协议；普通描述不能冒充机器证据。
        if action.expected and ":" in action.expected:
            kind = action.expected.split(":", 1)[0]
            if kind in {"url_prefix", "url_contains", "text_contains", "element_text"}:
                check = self.step(after, (action.expected,))
                if not check:
                    return ValidationResult("unexpected_change", check.evidence)
        if action.kind == "open" and not url_matches(after.url, action.value):
            return ValidationResult("unexpected_change", (f"导航落在其他地址：{after.url}",))

        return ValidationResult("passed", (after.diff or f"页面已更新：{after.url}",))

    @protocol.traced("validator.step")
    def step(
        self, observation, criteria, start_url="", *, semantic_verified=False
    ) -> ValidationResult:
        """顶层条件为 AND；空条件、未知条件及不稳定页面一律不通过。"""
        if not criteria or observation.loading or not observation.stable:
            return ValidationResult("unproven", ("条件为空或页面尚未稳定",))

        checks = [self._criterion(observation, item, semantic_verified) for item in criteria]
        evidence = tuple(item for check in checks for item in check.evidence)
        return ValidationResult("passed" if all(checks) else "unproven", evidence)

    def _criterion(self, page, criterion, semantic_verified=False) -> ValidationResult:
        if isinstance(criterion, str):
            kind, separator, value = criterion.partition(":")
            if not separator:
                return ValidationResult("unproven", ("不支持的旧格式条件",))
        elif isinstance(criterion, dict):
            kind, value = criterion.get("type"), criterion.get("value")
        else:
            return ValidationResult("unproven", ("条件格式无效",))

        if kind in {"all", "any"}:
            if not isinstance(value, (list, tuple)) or not value:
                return ValidationResult("unproven", ("组合条件为空",))
            checks = [self._criterion(page, item, semantic_verified) for item in value]
            passed = all(checks) if kind == "all" else any(checks)
            return ValidationResult(
                "passed" if passed else "unproven",
                tuple(text for check in checks for text in check.evidence),
            )

        text = (page.snapshot + "\n" + page.page_text).casefold()
        wanted = str(value).strip().casefold() if value is not None else ""
        passed = False
        if kind == "goal_state":
            passed = semantic_verified and bool(wanted)
        elif kind == "url_prefix" and wanted:
            passed = url_matches(page.url, str(value).strip())
        elif kind == "url_contains" and wanted:
            passed = wanted in page.url.casefold()
        elif kind == "text_contains" and wanted:
            passed = wanted in text
        elif kind == "element_text" and wanted:
            passed = any(wanted in item.get("text", "").casefold() for item in page.elements)
        elif kind == "element_count_at_least" and type(value) is int and value > 0:
            passed = len(page.elements) >= value
        elif kind == "page_state":
            passed = value == "ready" and page.stable and not page.loading
        elif kind == "extracted_value" and isinstance(value, dict):
            passed = value.get("key") in page.extracted and page.extracted[
                value["key"]
            ] == value.get("equals")

        return ValidationResult(
            "passed" if passed else "unproven",
            (f"{kind}={value!r}：{'满足' if passed else '证据不足'}",),
        )
