"""任务与步骤状态机：调度、恢复、记录结果，不直接调用浏览器命令。"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, replace
from urllib.parse import urlsplit

import protocol.protocol as protocol
from logger import log
from memory.memory import Memory
from reflection.reflection import Reflection

from react.browser import BindingLost
from react.budget import Budget, BudgetExceeded, StepBudget
from react.completion import CompletionGate, CompletionVerifier, TaskValidator
from react.decision import Decision, DecisionResult
from react.executor import Action, Executor
from react.observer import Observation, Observer
from react.outcomes import StepOutcome, StepResult, TaskOutcome
from react.validator import Validator, url_matches


@dataclass
class StepRuntimeState:
    """只属于当前步骤的一次尝试；重规划不会覆盖既有结果。"""

    budget: StepBudget = field(default_factory=StepBudget)
    results: list[StepResult] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)
    transitions: dict = field(default_factory=dict)
    navigations: dict = field(default_factory=dict)
    decisions: set = field(default_factory=set)
    advice: str = ""
    slow: bool = False
    cycle: bool = False
    last_action: Action | None = None
    last_before: Observation | None = None
    skill_cursor: int = 0
    skill_disabled: bool = False


def _controls(page):
    """轮播广告和 ref 重编号不应掩盖控件之间的往返循环。"""
    lines = [
        re.sub(r"\[ref=[\w-]+\]", "", line).strip()
        for line in page.snapshot.splitlines()
    ]
    controls = tuple(
        line
        for line in lines
        if re.search(
            r"\b(textbox|searchbox|combobox|listbox|option|spinbutton|checkbox|radio|button)\b",
            line,
        )
    )
    return page.url, controls or tuple(lines)


def _page_path(url):
    parsed = urlsplit(url)
    return parsed.scheme, parsed.netloc, parsed.path


class Orchestrator:
    def __init__(
        self,
        session,
        plan,
        *,
        budget=None,
        decision=None,
        memory=None,
        reflection=None,
        observer=None,
        executor=None,
        validator=None,
        completion=None,
        skills=None,
        planner=None,
        task_validator=None,
        user_goal="",
    ):
        self.session = session
        self.plan = plan
        self.original_plan = plan
        self.user_goal = user_goal or "；".join(step.goal for step in plan.steps)
        self.budget = budget or Budget()
        self.observer = observer or Observer(session)
        self.decision = decision or Decision()
        self.executor = executor or Executor(session)
        self.validator = validator or Validator()
        self.completion = completion or CompletionVerifier()
        self.gate = CompletionGate(self.validator, self.completion)
        self.task_validator = task_validator or TaskValidator(self.completion.ai)
        self.memory = memory or Memory()
        self.reflection = reflection or Reflection()
        self.skills = skills
        self.planner = planner
        self.outcomes = []
        self.observation = None

    @staticmethod
    def _progress(step, status, completed, **details):
        """沿用 thinking 事件存储结构化状态，兼容已有的历史恢复入口。"""
        protocol.thinking(
            json.dumps(
                {
                    "kind": "task_progress",
                    "step_id": step.id,
                    "goal": step.goal,
                    "status": status,
                    "completed_steps": list(completed),
                    **details,
                },
                ensure_ascii=False,
            )
        )

    def _record(self, outcome, step, completed):
        """追加步骤尝试及验收证据，让历史恢复保留成功和失败的完整过程。"""
        self.outcomes.append(outcome)
        self.memory.write("step_result", asdict(outcome))
        self._progress(
            step,
            "waiting_confirmation"
            if outcome.status == "waiting_user"
            else outcome.status,
            completed,
            current_url=outcome.current_url,
            reason=outcome.reason,
            verification_source=outcome.source,
            evidence=list(outcome.evidence),
            result=asdict(outcome),
            budget=asdict(self.budget.snapshot()),
        )

    def _finish(self, status, reason, evidence=()):
        page_url = self.observation.url if self.observation else ""
        summary = (
            ("任务失败：" if status == "failed" else "")
            + reason
            + (f" 当前页面：{page_url}" if page_url else "")
        )
        outcome = TaskOutcome(status, summary, tuple(self.outcomes), page_url, evidence)
        if status == "completed":
            self.memory.record_success(self.plan, list(outcome.completed_steps))
        else:
            self.memory.record_failure(summary)
        return outcome

    @log.traced("orchestrator.run")
    def run(self) -> TaskOutcome:
        """按依赖执行步骤，必要时增量重规划，最后统一验证原始用户目标。"""
        completed = set()
        replans = 0

        try:
            with self.budget.track_models():
                while len(completed) < len(self.plan.steps):
                    ready = [
                        step
                        for step in self.plan.steps
                        if step.id not in completed
                        and set(step.depends_on) <= completed
                    ]
                    if not ready:
                        return self._finish("blocked", "计划依赖无法满足")

                    # 当前宿主只提供一个 targetId。即使标记同一 parallel_group，
                    # 也必须串行，直到宿主提供独立标签页和独立上下文。
                    step = ready[0]
                    self._progress(
                        step, "started", sorted(completed), start_url=step.start_url
                    )
                    log.set_context(step_id=step.id, iteration=self.budget.steps + 1)
                    with log.measure("runtime.step.total", step_id=step.id):
                        try:
                            outcome = self._run_step(step, sorted(completed))
                        except BudgetExceeded as exc:
                            outcome = self._outcome(
                                step, self._active_state, "failed", str(exc)
                            )
                    if outcome.status == "completed":
                        completed.add(step.id)
                    self._record(outcome, step, sorted(completed))

                    if outcome.status == "completed":
                        continue
                    if (
                        outcome.status == "replan"
                        and self.planner
                        and replans < self.budget.max_replans
                    ):
                        replans += 1
                        try:
                            self.plan = self.planner.incremental_replan(
                                self.user_goal,
                                self.plan,
                                tuple(self.outcomes),
                                step.id,
                                self.observation,
                                reason=outcome.reason,
                            )
                        except BudgetExceeded:
                            raise
                        except Exception as exc:
                            return self._finish("blocked", f"增量重规划失败：{exc}")
                        protocol.thinking(
                            json.dumps(
                                {"kind": "planner_plan", "plan": self.plan.to_dict()},
                                ensure_ascii=False,
                            )
                        )
                        continue
                    status = "blocked" if outcome.status == "replan" else outcome.status
                    return self._finish(status, outcome.reason)

                final = self.task_validator.verify(
                    self.user_goal,
                    self.plan,
                    tuple(self.outcomes),
                    self.observation,
                    original_plan=self.original_plan,
                )
                if not final.accepted:
                    return self._finish(
                        "blocked",
                        "步骤已结束，但用户目标尚未通过最终验收",
                        final.evidence,
                    )
                return self._finish(
                    "completed", f"任务完成：{self.user_goal}。", final.evidence
                )
        except BudgetExceeded as exc:
            return self._finish("failed", str(exc))

    def _capture(self, step):
        # URL/元素类验收只抓交互树，正文和语义条件才需要完整页面。
        if isinstance(self.observer, Observer):
            encoded = json.dumps(step.success_criteria, ensure_ascii=False)
            self.observer.include_full = (
                any(kind in encoded for kind in ("text_contains", "goal_state"))
                or step.risk != "low"
            )
        page = self.observer.capture()
        self.observation = page
        return page

    def _outcome(self, step, state, status, reason="", completion=None):
        return StepOutcome(
            step.id,
            step.goal,
            status,
            reason,
            self.observation.url if self.observation else "",
            completion.source if completion else "",
            completion.evidence if completion else (),
            tuple(state.results),
        )

    def _recover(self, step, state, page, reason, completed, *, no_op=False):
        """L1 记录失败；连续失败或 no-op 的下一轮升级 L2。"""
        self.budget.failure()
        if no_op:
            state.budget.no_ops += 1
        else:
            state.budget.failures += 1
        state.skill_disabled = True
        state.slow = state.budget.failures >= 1 or state.budget.no_ops >= 2
        state.advice = (
            f"{reason}；依据当前页面修正动作，不重复无效操作，不猜测业务参数。"
        )
        self._progress(
            step,
            "retry",
            completed,
            current_url=page.url,
            error=reason,
            advice=state.advice,
        )

    def _choose(self, step, state, page):
        """技能每轮只绑定一个动作；缓存过的快路径失败状态直接升级。"""
        if self.skills and not state.skill_disabled and not state.slow:
            skill = self.skills.match(step.goal, page.url)
            if skill:
                action = self.skills.next_action(skill, state.skill_cursor, page)
                if action is not None:
                    return DecisionResult((action,), 1.0, "skill")
                state.skill_disabled = True

        cache_key = page.fingerprint
        if cache_key in state.decisions:
            state.slow = True
        state.decisions.add(cache_key)

        if state.slow or state.cycle:
            state.budget.slow_calls += 1
        decision = self.decision.choose(
            step.goal,
            page,
            step.start_url if not state.results else "",
            slow=state.slow,
            advice=state.advice,
            force_entry=step.success_criteria
            in (
                (f"url_prefix:{step.start_url}",),
                ({"type": "url_prefix", "value": step.start_url},),
            ),
            success_criteria=step.success_criteria,
            recent_action=state.last_action,
            force_reasoning=state.budget.no_ops >= 2 or state.cycle,
            action_history=state.history[-6:],
        )
        if decision.route == "slow" and not (state.slow or state.cycle):
            state.budget.slow_calls += 1
        return decision

    def _track_transition(self, state, before, after, action):
        target = next(
            (
                item.get("text", "")
                for item in before.elements
                if item.get("ref") == action.ref
            ),
            "",
        )
        transition = (
            _controls(before),
            action.kind,
            target,
            action.value,
            _controls(after),
        )
        state.transitions[transition] = state.transitions.get(transition, 0) + 1
        count = state.transitions[transition]
        state.history.append(
            {
                "action": asdict(action),
                "page_changed": before.fingerprint != after.fingerprint,
                "before_controls": transition[0][1],
                "after_controls": transition[-1][1],
            }
        )
        if count >= 2 and transition[0] != transition[-1]:
            state.cycle = True
        if count >= 3 and state.cycle:
            return "控件状态往返循环，慢路径恢复仍无进展"

        if _page_path(before.url) != _page_path(after.url):
            navigation = (
                _page_path(before.url),
                action.kind,
                target,
                action.value,
                _page_path(after.url),
            )
            state.navigations[navigation] = state.navigations.get(navigation, 0) + 1
            if state.navigations[navigation] >= 2:
                state.cycle = True
                state.slow = True
            if state.navigations[navigation] >= 3:
                return "页面往返循环，慢路径恢复仍无进展"
        return ""

    @log.traced("orchestrator.step")
    def _run_step(self, step, completed) -> StepOutcome:
        """用当前观察驱动一个动作，动作后验收并复用新观察进入下一轮。"""
        state = StepRuntimeState()
        self._active_state = state
        if not self.budget.allow():
            return self._outcome(step, state, "failed", "预算或重试次数耗尽")

        page = self._capture(step)
        initial = self.gate.check(step, page, initial=True)
        if initial.accepted:
            return self._outcome(step, state, "completed", completion=initial)

        while state.budget.allow(self.budget):
            state.budget.attempts += 1
            log.set_context(iteration=self.budget.steps + 1)
            if not page.stable or page.loading:
                # 不稳定观察不可驱动动作；轮询次数也受步骤尝试预算约束。
                page = self._capture(step)
                check = self.gate.check(
                    step, page, state.last_before, state.last_action
                )
                if check.accepted:
                    return self._outcome(step, state, "completed", completion=check)
                continue

            decision = self._choose(step, state, page)
            if decision.terminal == "DONE":
                done = self.gate.check(step, page, state.last_before, state.last_action)
                if done.accepted:
                    return self._outcome(step, state, "completed", completion=done)
                self._recover(step, state, page, "DONE 未通过完成验收", completed)
                continue

            if decision.terminal == "BLOCKED" or not decision.actions:
                self._recover(step, state, page, "没有安全可执行的动作", completed)
                continue
            if decision.confidence < 0.65:
                self._recover(step, state, page, "决策置信度不足", completed)
                continue

            # 每轮只接受一个动作，并绑定本次观察；显式的旧版本不可覆盖。
            action = decision.actions[0]
            if action.observation_version is None:
                action = replace(action, observation_version=page.version)
            precondition = self.validator.precondition(page, action)
            if not precondition:
                self._recover(
                    step, state, page, "；".join(precondition.evidence), completed
                )
                page = self._capture(step)
                continue

            requires_confirmation = action.sensitive or (
                step.needs_user_confirmation
                and action.kind not in {"open", "wait", "scroll"}
            )
            if requires_confirmation:
                protocol.notify(f"步骤需要用户确认：{step.goal}", "blocking")
                return self._outcome(
                    step, state, "waiting_user", f"等待用户确认：{step.goal}"
                )

            self.budget.consume_step()
            state.budget.actions += 1
            error = ""
            try:
                result = self.executor.execute(action)
                if not result.ok:
                    error = result.preview
            except BindingLost:
                raise
            except Exception as exc:
                error = str(exc)

            # 即使命令报错，也可能已造成部分变化；始终刷新并废弃旧 ref。
            after = self._capture(step)
            if error and action.kind == "open" and url_matches(after.url, action.value):
                error = ""
            validation = self.validator.action(page, after, action)
            state.last_before, state.last_action = page, action
            state.results.append(
                StepResult(
                    asdict(action),
                    page.version,
                    after.version,
                    "execution_failed" if error else validation.status,
                    (error,) if error else validation.evidence,
                    after.url,
                )
            )
            self.memory.write("action_result", asdict(state.results[-1]))
            cycle_reason = self._track_transition(state, page, after, action)
            before, page = page, after

            if error:
                self._recover(step, state, page, error, completed)
                continue
            if validation.status == "loading":
                continue
            if validation.status == "unchanged":
                self._recover(
                    step, state, page, "连续动作无可观察进展", completed, no_op=True
                )
                # L1 允许一次即时刷新，处理异步控件和失效 ref；不增加固定 sleep。
                refreshed = self._capture(step)
                if (
                    refreshed.fingerprint != page.fingerprint
                    and refreshed.stable
                    and not refreshed.loading
                ):
                    state.budget.progressed()
                    state.slow = False
                    state.skill_cursor += 1
                page = refreshed
            elif not validation:
                self._recover(
                    step, state, page, "；".join(validation.evidence), completed
                )
                continue
            else:
                state.budget.progressed()
                state.slow = False
                state.skill_cursor += 1

            completion = self.gate.check(step, page, before, action)
            if completion.accepted:
                return self._outcome(step, state, "completed", completion=completion)
            if cycle_reason:
                return self._outcome(step, state, "replan", cycle_reason)

        reason = (
            "连续动作无可观察进展，恢复预算耗尽"
            if state.budget.no_ops
            else "预算或重试次数耗尽，步骤仍未完成"
        )
        status = "replan" if self.budget.allow() else "failed"
        return self._outcome(step, state, status, reason)
