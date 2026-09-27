"""任务与步骤状态机：调度、恢复、记录结果，不直接调用浏览器命令。"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, replace
from urllib.parse import urlsplit

import protocol.protocol as protocol
from logger import logger
from memory.memory import Memory
from reflection.reflection import Reflection

from react.browser import BindingLost
from react.budget import Budget, BudgetExceeded, StepBudget
from react.completion import CompletionGate, CompletionVerifier, TaskValidator
from react.decision import Decision, DecisionResult
from react.executor import Action, Executor
from react.observer import Observation, Observer
from react.outcomes import StepOutcome, StepResult, TaskOutcome
from react.validator import ValidationResult, Validator, url_matches


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


@dataclass
class ActionCycleResult:
    """一次动作及其后续观察的结果，避免在步骤主循环中传递长元组。"""

    page: Observation
    before: Observation | None = None
    validation: ValidationResult | None = None
    cycle_reason: str = ""
    error: str = ""
    outcome: StepOutcome | None = None


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

    @logger.traced("orchestrator.run")
    def run(self) -> TaskOutcome:
        """按依赖调度步骤，处理重规划，并在末尾验收原始用户目标。"""
        completed = set()
        replans = 0

        try:
            with self.budget.track_models():
                while len(completed) < len(self.plan.steps):
                    step = self._next_ready_step(completed)
                    if step is None:
                        return self._finish("blocked", "计划依赖无法满足")

                    outcome = self._run_scheduled_step(step, completed)
                    if outcome.status == "completed":
                        continue

                    replans, did_replan, replan_result = self._maybe_replan(
                        step, outcome, replans
                    )
                    if replan_result is not None:
                        return replan_result
                    if did_replan:
                        continue

                    status = "blocked" if outcome.status == "replan" else outcome.status
                    return self._finish(status, outcome.reason)

                return self._verify_task_result()
        except BudgetExceeded as exc:
            return self._finish("failed", str(exc))

    def _next_ready_step(self, completed):
        """返回当前依赖已满足的第一个步骤；单标签页环境保持串行执行。"""
        for step in self.plan.steps:
            if step.id not in completed and set(step.depends_on) <= completed:
                return step
        return None

    def _run_scheduled_step(self, step, completed):
        """记录步骤开始状态并执行一次步骤预算，统一转换预算异常。"""
        completed_ids = sorted(completed)
        self._progress(step, "started", completed_ids, start_url=step.start_url)
        logger.set_context(step_id=step.id, iteration=self.budget.steps + 1)
        with logger.measure("runtime.step.total", step_id=step.id):
            try:
                outcome = self._run_step(step, completed_ids)
            except BudgetExceeded as exc:
                outcome = self._outcome(
                    step, self._active_state, "failed", str(exc)
                )
        if outcome.status == "completed":
            completed.add(step.id)
        self._record(outcome, step, sorted(completed))
        return outcome

    def _maybe_replan(self, step, outcome, replans):
        """对可恢复失败执行一次增量重规划，返回次数、是否重规划和终止结果。"""
        can_replan = (
            outcome.status == "replan"
            and self.planner is not None
            and replans < self.budget.max_replans
        )
        if not can_replan:
            return replans, False, None

        try:
            new_plan = self.planner.incremental_replan(
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
            return replans, False, self._finish("blocked", f"增量重规划失败：{exc}")

        replans += 1
        self.plan = new_plan
        protocol.thinking(
            json.dumps(
                {"kind": "planner_plan", "plan": self.plan.to_dict()},
                ensure_ascii=False,
            )
        )
        return replans, True, None

    def _verify_task_result(self):
        """步骤全部完成后，使用原始计划和完整结果验收用户目标。"""
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
        """先尝试技能动作，再根据页面指纹选择快路径或慢路径。"""
        skill_action = self._skill_action(step, state, page)
        if skill_action is not None:
            return skill_action

        cache_key = page.fingerprint
        if cache_key in state.decisions:
            state.slow = True
        state.decisions.add(cache_key)
        return self._model_decision(step, state, page)

    def _skill_action(self, step, state, page):
        """从技能库取当前步骤的下一个动作，失败后禁用该技能。"""
        if self.skills and not state.skill_disabled and not state.slow:
            skill = self.skills.match(step.goal, page.url)
            if skill:
                action = self.skills.next_action(skill, state.skill_cursor, page)
                if action is not None:
                    return DecisionResult((action,), 1.0, "skill")
                state.skill_disabled = True
        return None

    def _model_decision(self, step, state, page):
        """将当前页面和步骤状态交给决策器，并累计慢路径调用次数。"""
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
        """记录控件变化，并检测控件或页面路径的往返循环。"""
        target = self._action_target(before, action)
        transition = self._transition_key(before, after, action, target)
        count = state.transitions.get(transition, 0) + 1
        state.transitions[transition] = count
        self._record_transition_history(state, before, after, action, transition)
        if count >= 2 and transition[0] != transition[-1]:
            state.cycle = True
        if count >= 3 and state.cycle:
            return "控件状态往返循环，慢路径恢复仍无进展"
        return self._track_navigation(state, before, after, action, target)

    @staticmethod
    def _action_target(before, action):
        """读取动作引用在操作前对应的控件文本。"""
        return next(
            (
                item.get("text", "")
                for item in before.elements
                if item.get("ref") == action.ref
            ),
            "",
        )

    @staticmethod
    def _transition_key(before, after, action, target):
        """生成不含 ref 编号噪声的控件状态转移键。"""
        return (
            _controls(before),
            action.kind,
            target,
            action.value,
            _controls(after),
        )

    @staticmethod
    def _record_transition_history(state, before, after, action, transition):
        """保存有限动作历史，供下一轮决策识别重复状态。"""
        state.history.append(
            {
                "action": asdict(action),
                "page_changed": before.fingerprint != after.fingerprint,
                "before_controls": transition[0][1],
                "after_controls": transition[-1][1],
            }
        )

    def _track_navigation(self, state, before, after, action, target):
        """统计跨页面路径往返，并在重复导航时切换慢路径。"""
        if _page_path(before.url) == _page_path(after.url):
            return ""

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

    @logger.traced("orchestrator.step")
    def _run_step(self, step, completed) -> StepOutcome:
        """用当前观察驱动动作，并把每轮交给独立的动作验收流程。"""
        state, page, initial = self._start_step(step)
        if initial is not None:
            return initial
        return self._step_loop(step, state, page, completed)

    def _step_loop(self, step, state, page, completed):
        """循环执行步骤轮次，直到完成、等待确认或需要重规划。"""
        while state.budget.allow(self.budget):
            outcome, page = self._run_step_iteration(
                step, state, page, completed
            )
            if outcome is not None:
                return outcome

        return self._exhausted_step(step, state)

    def _run_step_iteration(self, step, state, page, completed):
        """执行单轮观察、决策、动作和验收，返回结果与下一轮页面。"""
        self._begin_iteration(state)
        if not page.stable or page.loading:
            refreshed, completion = self._refresh_unstable_step(step, state, page)
            return completion, refreshed

        action, terminal = self._prepare_action(step, state, page, completed)
        if terminal is not None:
            return terminal, page
        if action is None:
            return None, page

        cycle = self._run_action_cycle(step, state, page, action, completed)
        return self._resolve_action_cycle(step, state, page, action, cycle, completed)

    def _begin_iteration(self, state):
        """递增步骤轮次并同步日志上下文。"""
        state.budget.attempts += 1
        logger.set_context(iteration=self.budget.steps + 1)

    def _prepare_action(self, step, state, page, completed):
        """完成决策与动作选择，把终态和可执行动作统一交给主循环。"""
        decision = self._choose(step, state, page)
        return self._select_action(step, state, page, decision, completed)

    def _resolve_action_cycle(self, step, state, page, action, cycle, completed):
        """处理动作周期结果、恢复分支和动作后的步骤验收。"""
        if cycle.outcome is not None:
            return cycle.outcome, cycle.page
        if cycle.error:
            self._recover(step, state, cycle.page, cycle.error, completed)
            return None, cycle.page
        if cycle.validation is None:
            # 动作前置条件失败时已刷新引用并记录恢复状态。
            return None, cycle.page
        if cycle.validation.status == "loading":
            return None, cycle.page

        next_page = self._process_validation(step, state, cycle, completed)
        if next_page is None:
            return None, cycle.page
        completion = self.gate.check(step, next_page, cycle.before, action)
        if completion.accepted:
            return self._completed_cycle(step, state, next_page, completion)
        if cycle.cycle_reason:
            return self._outcome(step, state, "replan", cycle.cycle_reason), next_page
        return None, next_page

    def _completed_cycle(self, step, state, page, completion):
        """把动作后的完成门结果转换成步骤结果。"""
        return self._outcome(step, state, "completed", completion=completion), page

    def _start_step(self, step):
        """创建步骤状态，捕获初始页面，并处理无需动作即可完成的步骤。"""
        state = StepRuntimeState()
        self._active_state = state
        if not self.budget.allow():
            return state, None, self._outcome(
                step, state, "failed", "预算或重试次数耗尽"
            )
        page = self._capture(step)
        initial = self.gate.check(step, page, initial=True)
        if initial.accepted:
            return state, page, self._outcome(step, state, "completed", completion=initial)
        return state, page, None

    def _refresh_unstable_step(self, step, state, page):
        """页面加载或导航不稳定时只刷新观察，不向决策器提交旧引用。"""
        refreshed = self._capture(step)
        check = self.gate.check(step, refreshed, state.last_before, state.last_action)
        if check.accepted:
            return refreshed, self._outcome(step, state, "completed", completion=check)
        return refreshed, None

    def _select_action(self, step, state, page, decision, completed):
        """处理 DONE、BLOCKED 和低置信度结果，只把安全动作交给执行器。"""
        if decision.terminal == "DONE":
            done = self.gate.check(step, page, state.last_before, state.last_action)
            if done.accepted:
                return None, self._outcome(step, state, "completed", completion=done)
            self._recover(step, state, page, "DONE 未通过完成验收", completed)
            return None, None
        if decision.terminal == "BLOCKED" or not decision.actions:
            self._recover(step, state, page, "没有安全可执行的动作", completed)
            return None, None
        if decision.confidence < 0.65:
            self._recover(step, state, page, "决策置信度不足", completed)
            return None, None

        action = decision.actions[0]
        if action.observation_version is None:
            action = replace(action, observation_version=page.version)
        return action, None

    def _run_action_cycle(self, step, state, page, action, completed):
        """执行一次动作，刷新页面，记录动作结果并计算循环信号。"""
        precondition = self.validator.precondition(page, action)
        if not precondition:
            self._recover(step, state, page, "；".join(precondition.evidence), completed)
            return ActionCycleResult(self._capture(step))
        if self._needs_confirmation(step, action):
            return self._confirmation_result(step, state, page)

        self.budget.consume_step()
        state.budget.actions += 1
        error = self._execute_action(action)
        after = self._capture(step)
        if error and action.kind == "open" and url_matches(after.url, action.value):
            error = ""
        validation = self.validator.action(page, after, action)
        cycle_reason = self._record_action_result(
            state, action, page, after, error, validation
        )
        return ActionCycleResult(after, page, validation, cycle_reason, error)

    @staticmethod
    def _needs_confirmation(step, action):
        """判断动作是否需要用户确认；等待和滚动不触发业务确认。"""
        return action.sensitive or (
            step.needs_user_confirmation
            and action.kind not in {"open", "wait", "scroll"}
        )

    def _confirmation_result(self, step, state, page):
        """生成等待确认结果，并暂停当前步骤的动作预算。"""
        protocol.notify(f"步骤需要用户确认：{step.goal}", "blocking")
        return ActionCycleResult(
            page,
            outcome=self._outcome(
                step, state, "waiting_user", f"等待用户确认：{step.goal}"
            ),
        )

    def _record_action_result(self, state, action, before, after, error, validation):
        """保存动作结果和页面转移历史，供恢复与任务验收使用。"""
        state.last_before, state.last_action = before, action
        state.results.append(
            StepResult(
                asdict(action),
                before.version,
                after.version,
                "execution_failed" if error else validation.status,
                (error,) if error else validation.evidence,
                after.url,
            )
        )
        self.memory.write("action_result", asdict(state.results[-1]))
        return self._track_transition(state, before, after, action)

    def _execute_action(self, action):
        """调用执行器并把可恢复的命令异常转换为本轮错误文本。"""
        try:
            result = self.executor.execute(action)
            return "" if result.ok else result.preview
        except BindingLost:
            raise
        except Exception as exc:
            return str(exc)

    def _process_validation(self, step, state, cycle, completed):
        """按动作验收状态更新恢复预算，并返回下一轮使用的页面观察。"""
        validation = cycle.validation
        if validation.status == "unchanged":
            self._recover(
                step, state, cycle.page, "连续动作无可观察进展", completed, no_op=True
            )
            refreshed = self._capture(step)
            if (
                refreshed.fingerprint != cycle.page.fingerprint
                and refreshed.stable
                and not refreshed.loading
            ):
                state.budget.progressed()
                state.slow = False
                state.skill_cursor += 1
            return refreshed
        if not validation:
            self._recover(
                step, state, cycle.page, "；".join(validation.evidence), completed
            )
            return None

        state.budget.progressed()
        state.slow = False
        state.skill_cursor += 1
        return cycle.page

    def _exhausted_step(self, step, state):
        """生成步骤预算耗尽结果，供外层决定失败或增量重规划。"""
        reason = (
            "连续动作无可观察进展，恢复预算耗尽"
            if state.budget.no_ops
            else "预算或重试次数耗尽，步骤仍未完成"
        )
        status = "replan" if self.budget.allow() else "failed"
        return self._outcome(step, state, status, reason)
