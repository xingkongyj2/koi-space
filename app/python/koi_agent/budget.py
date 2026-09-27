"""任务累计预算、步骤连续失败预算，以及各类真实模型请求计数。"""

from __future__ import annotations

import time
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import RLock

_ACTIVE = ContextVar("model_budget", default=None)
_CATEGORY = ContextVar("model_category", default="planner")


class BudgetExceeded(RuntimeError):
    """预算熔断必须穿透模型降级逻辑，不能当作普通网络错误吞掉。"""


@contextmanager
def model_scope(category: str):
    token = _CATEGORY.set(category)
    try:
        yield
    finally:
        _CATEGORY.reset(token)


def before_model_request(timeout: float) -> float:
    budget = _ACTIVE.get()
    if budget is None:
        return timeout

    budget.consume_model(_CATEGORY.get())
    return min(timeout, max(0.01, budget.max_seconds - budget.elapsed))


def record_usage(data: dict) -> None:
    budget = _ACTIVE.get()
    usage = data.get("usage") or {}
    if budget is not None and isinstance(usage, dict):
        total = usage.get("total_tokens")
        if total is None:
            parts = (usage.get("input_tokens", 0), usage.get("output_tokens", 0))
            total = sum(parts) if all(type(part) is int for part in parts) else None
        if type(total) is int:
            budget.add_tokens(total)


@dataclass(frozen=True)
class BudgetSnapshot:
    steps: int
    failures: int
    elapsed: float
    remaining_steps: int
    tokens: int
    model_calls: dict[str, int]


@dataclass
class Budget:
    max_steps: int = 30
    max_failures: int = 3
    max_seconds: float = 300
    max_model_calls: int = 100
    max_tokens: int = 100000
    max_step_actions: int = 15
    max_no_ops: int = 5
    max_slow_calls: int = 5
    max_replans: int = 2
    started: float = field(default_factory=time.monotonic)
    steps: int = 0
    failures: int = 0
    tokens: int = 0
    model_calls: Counter = field(default_factory=Counter)
    _lock: RLock = field(default_factory=RLock, repr=False)

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    @contextmanager
    def track_models(self):
        """ContextVar 会随并发规划的 copy_context 传播，共享计数由锁保护。"""
        token = _ACTIVE.set(self)
        try:
            yield
        finally:
            _ACTIVE.reset(token)

    def consume_step(self) -> None:
        self.steps += 1

    def failure(self) -> None:
        # 累计失败用于诊断；熔断依据独立的步骤连续失败数。
        self.failures += 1

    def consume_model(self, category: str) -> None:
        with self._lock:
            if (
                self.elapsed >= self.max_seconds
                or self.tokens >= self.max_tokens
                or sum(self.model_calls.values()) >= self.max_model_calls
            ):
                raise BudgetExceeded("模型调用、token 或时间预算耗尽")
            self.model_calls[category] += 1

    def add_tokens(self, count: int) -> None:
        with self._lock:
            self.tokens += max(0, count)

    def snapshot(self) -> BudgetSnapshot:
        return BudgetSnapshot(
            self.steps,
            self.failures,
            self.elapsed,
            max(0, self.max_steps - self.steps),
            self.tokens,
            dict(self.model_calls),
        )

    def exhausted(self) -> bool:
        return (
            self.steps >= self.max_steps
            or self.elapsed >= self.max_seconds
            or self.tokens >= self.max_tokens
            or sum(self.model_calls.values()) >= self.max_model_calls
        )

    def allow(self) -> bool:
        return not self.exhausted()


@dataclass
class StepBudget:
    """步骤推进会清零连续失败；任务累计用量永远不清零。"""

    actions: int = 0
    failures: int = 0
    no_ops: int = 0
    slow_calls: int = 0
    attempts: int = 0

    def progressed(self) -> None:
        self.failures = 0
        self.no_ops = 0

    def allow(self, task: Budget) -> bool:
        return (
            task.allow()
            and self.actions < task.max_step_actions
            and self.failures < task.max_failures
            and self.no_ops < task.max_no_ops
            and self.slow_calls < task.max_slow_calls
            and self.attempts < task.max_step_actions * 2
        )
