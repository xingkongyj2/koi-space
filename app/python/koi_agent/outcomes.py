"""执行结果契约：保留每次尝试及证据，供恢复、重规划和最终验收使用。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

Status = Literal["completed", "retry", "waiting_user", "blocked", "replan", "failed"]


@dataclass(frozen=True)
class StepResult:
    """一个动作的追加式记录；快照版本用于追溯 ref 的来源。"""

    action: dict
    before_version: int
    after_version: int
    status: str
    evidence: tuple[str, ...] = ()
    current_url: str = ""


@dataclass(frozen=True)
class StepOutcome:
    step_id: str
    goal: str
    status: Status
    reason: str = ""
    current_url: str = ""
    source: str = ""
    evidence: tuple[str, ...] = ()
    results: tuple[StepResult, ...] = ()


@dataclass(frozen=True)
class TaskOutcome:
    status: Status
    summary: str
    steps: tuple[StepOutcome, ...] = ()
    current_url: str = ""
    evidence: tuple[str, ...] = ()

    @property
    def completed_steps(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(step.step_id for step in self.steps if step.status == "completed")
        )

    def to_dict(self) -> dict:
        return asdict(self)
