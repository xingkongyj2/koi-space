"""Task state machine for the Koi browser agent.

This module owns control flow only. All browser/model/storage concerns are
injected through the dedicated components, which keeps the loop testable.
"""
from __future__ import annotations
from dataclasses import asdict
import json
import time
from urllib.parse import urlsplit

from .budget import Budget
from .observer import Observer
from .decision import Decision, DecisionResult
from .executor import Executor
from .validator import Validator
from .completion import CompletionVerifier
from .reflection import Reflection
from .memory import Memory
from . import protocol

class Orchestrator:
    def __init__(self, session, plan, *, budget=None, decision=None,
                 memory=None, reflection=None, observer=None, executor=None,
                 validator=None, completion=None, skills=None):
        self.session = session
        self.plan = plan
        self.budget = budget or Budget()
        self.observer = observer or Observer(session)
        self.decision = decision or Decision()
        self.executor = executor or Executor(session)
        self.validator = validator or Validator()
        self.completion = completion or CompletionVerifier()
        self.memory = memory or Memory()
        self.reflection = reflection or Reflection()
        self.skills = skills
        self._last_completed_url = ""

    @staticmethod
    def _progress(step, status: str, completed: list[str], **details) -> None:
        """Persist planning-relevant execution state in the session history."""
        protocol.thinking(json.dumps({
            "kind": "task_progress",
            "step_id": step.id,
            "goal": step.goal,
            "status": status,
            "completed_steps": list(completed),
            **details,
        }, ensure_ascii=False))

    def _complete(self, step, completed: list[str], observation, source="criteria") -> None:
        completed.append(step.id)
        self._last_completed_url = observation.url
        self._progress(step, "completed", completed,
                       current_url=observation.url,
                       success_criteria=list(step.success_criteria),
                       verification_source=source)

    @protocol.traced("orchestrator.run")
    def run(self) -> str:
        completed: list[str] = []
        for step in self.plan.steps:
            missing = [dep for dep in step.depends_on if dep not in completed]
            if missing:
                protocol.trace("orchestrator.step.blocked", step_id=step.id, missing_dependencies=missing)
                protocol.log(f"flow=plan step={step.id} status=blocked dependencies={missing}")
                self._progress(step, "blocked", completed, missing_dependencies=missing)
                continue
            self._progress(step, "started", completed, start_url=step.start_url)
            with protocol.measure("runtime.step.total", step_id=step.id):
                result = self._run_step(step, completed)
            if result is not None:
                self.memory.record_failure(result)
                return result
        if len(completed) != len(self.plan.steps):
            reason = f"计划依赖无法满足：{len(completed)}/{len(self.plan.steps)} 个步骤完成"
            self.memory.record_failure(reason)
            return reason
        self.memory.record_success(self.plan, completed)
        final_goal = self.plan.steps[-1].goal if self.plan.steps else "已完成请求"
        result = f"任务完成：{final_goal}。"
        if self._last_completed_url:
            result += f" 当前页面：{self._last_completed_url}"
        return result

    @protocol.traced("orchestrator.step")
    def _run_step(self, step, completed: list[str]) -> str | None:
        failures = 0
        slow = False
        advice = ""
        interacted = False
        last_before = None
        last_action = None
        last_check = None
        last_verdict = None
        verification_source = "criteria"
        transitions: dict[tuple[str, str, str, str, str], int] = {}

        def needs_model_verification() -> bool:
            """Return whether page evidence needs semantic completion review.

            URL-only acceptance is deterministic.  Text-only and goal-state
            criteria are deliberately treated as ambiguous because a label can
            remain visible before the requested action has finished.
            """
            if step.risk in {"medium", "high"} or step.needs_user_confirmation:
                return True
            if len(step.success_criteria) > 1:
                return True
            for criterion in step.success_criteria:
                if isinstance(criterion, dict):
                    if criterion.get("type") in {"text_contains", "goal_state"}:
                        return True
                elif str(criterion).lower().startswith("text_contains:"):
                    return True
            return False

        def deterministic_completion_allowed() -> bool:
            """Avoid finishing on a weak legacy text criterion at step entry."""
            return not needs_model_verification()

        def goal_complete(page, before=None, action=None, phase="before_decision",
                          allow_model=False) -> bool:
            nonlocal last_check, last_verdict, verification_source
            with protocol.measure("runtime.goal_check", phase=phase):
                criteria_met = self.validator.step(page, step.success_criteria, step.start_url)
                if not allow_model:
                    verification_source = "criteria"
                    return criteria_met
                key = (page.url, page.snapshot, page.page_text,
                       before.url if before else "", before.snapshot if before else "",
                       before.page_text if before else "",
                       action.kind if action else "", action.ref if action else "",
                       action.value if action else "")
                if key != last_check:
                    last_verdict = self.completion.verify(step, before, page, action)
                    last_check = key
                if last_verdict is None:
                    verification_source = "criteria"
                    return criteria_met
                if last_verdict != criteria_met:
                    protocol.trace("completion.criteria.disagree", step_id=step.id,
                                   criteria_met=criteria_met, goal_complete=last_verdict,
                                   current_url=page.url)
                if last_verdict:
                    verification_source = "goal_verifier"
                return last_verdict

        def page_key(url: str) -> str:
            parsed = urlsplit(url)
            return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

        while self.budget.allow():
            self.budget.consume_step()
            protocol.set_context(iteration=self.budget.steps)
            protocol.trace("orchestrator.iteration", budget=self.budget.snapshot(), failures=failures, slow=slow, advice=advice)
            with protocol.measure("runtime.observe", phase="before_decision"):
                observation = self.observer.capture()
            page_observed_at = time.monotonic()
            # The first observation only uses cheap deterministic acceptance.
            # Semantic completion is checked after an action or when JEV
            # explicitly proposes DONE.
            if (goal_complete(
                    observation,
                    last_before,
                    last_action,
                    allow_model=interacted and needs_model_verification(),
                ) and (interacted or deterministic_completion_allowed())):
                self._complete(step, completed, observation, verification_source)
                protocol.log(f"flow=verify step={step.id} status=passed source=pre_decision")
                return None
            decision_started = time.monotonic()
            skill = self.skills.match(step.goal, observation.url) if self.skills else None
            if skill:
                protocol.log(f"flow=decision step={step.id} route=skill")
                actions = self.skills.actions(skill)
                decision = type("SkillDecision", (), {"actions": actions, "route": "skill", "confidence": 1.0})()
            else:
                decision = self.decision.choose(
                    step.goal, observation, step.start_url if not interacted else "",
                    slow=slow, advice=advice,
                    force_entry=(step.success_criteria == (f"url_prefix:{step.start_url}",)),
                    success_criteria=step.success_criteria,
                    recent_action=last_action,
                )
            protocol.timing("runtime.decision", decision_started, route=decision.route)
            # Treat every browser interaction as a new planning boundary.  A
            # previously chosen sequence can become invalid as soon as the
            # page changes, so the next loop must capture and analyze again.
            if len(decision.actions) > 1:
                protocol.trace("orchestrator.reanalyze_after_action",
                               discarded_actions=len(decision.actions) - 1)
                decision = DecisionResult(decision.actions[:1], decision.confidence,
                                          decision.route, getattr(decision, "rationale", ""),
                                          getattr(decision, "terminal", ""))
            protocol.log(
                f"flow=decision step={step.id} route={decision.route} "
                f"confidence={decision.confidence:.2f} actions={len(decision.actions)}"
            )
            protocol.trace("orchestrator.actions", route=decision.route, confidence=decision.confidence, actions=decision.actions)
            terminal = getattr(decision, "terminal", "")
            if terminal == "DONE":
                accepted = goal_complete(
                    observation,
                    last_before,
                    last_action,
                    phase="jev_done",
                    allow_model=needs_model_verification(),
                )
                if accepted:
                    self._complete(step, completed, observation, verification_source)
                    protocol.log(f"flow=verify step={step.id} status=passed source=jev_done")
                    return None
                protocol.trace("orchestrator.done_rejected",
                               step_id=step.id,
                               reason="JEV DONE did not pass the completion gate",
                               current_url=observation.url)
                failures += 1
                self.budget.failure()
                advice = self.reflection.advise(step.goal, observation, "JEV claimed DONE but acceptance was not proven")
                self._progress(step, "retry", completed, current_url=observation.url,
                               error="JEV DONE 未通过完成验收", advice=advice)
                slow = True
                continue
            if terminal == "BLOCKED":
                self._progress(step, "failed", completed, current_url=observation.url,
                               reason="JEV 报告没有安全可执行的动作")
                return f"步骤 {step.id} 失败：JEV 报告没有安全可执行的动作"
            if not decision.actions:
                failures += 1
                self.budget.failure()
                advice = self.reflection.advise(step.goal, observation, "no action")
                self._progress(step, "retry", completed, current_url=observation.url,
                               error="no action", advice=advice)
                slow = True
                continue
            failed = False
            for action in decision.actions:
                if step.needs_user_confirmation:
                    protocol.trace("orchestrator.confirmation_required", action=action)
                    self._progress(step, "waiting_confirmation", completed, current_url=observation.url)
                    protocol.notify(f"步骤需要用户确认：{step.goal}", "blocking")
                    return f"等待用户确认：{step.goal}"
                try:
                    target_label = next((element.get("text", "") for element in observation.elements
                                         if element.get("ref") == action.ref), "")
                    protocol.timing("browser.action.page_observed_to_dispatch", page_observed_at,
                                    action=action.kind, route=decision.route,
                                    target=action.ref or (action.value if action.kind == "open" else ""),
                                    target_label=target_label)
                    with protocol.measure("runtime.execute", action=action.kind):
                        result = self.executor.execute(action)
                except Exception as exc:  # executor turns operational errors into reflection input
                    interacted = interacted or action.kind != "open"
                    failed = True
                    self.budget.failure()
                    advice = self.reflection.advise(step.goal, observation, str(exc))
                    self._progress(step, "retry", completed, current_url=observation.url,
                                   error=str(exc), advice=advice)
                    break
                if not result.ok:
                    # Browser navigation may report a timeout while the page
                    # finishes loading in the background. Verify the URL once
                    # before counting it as a failed action.
                    if action.kind == "open" and action.value:
                        try:
                            settled_url = self.session.current_url()
                        except Exception:
                            settled_url = ""
                        if settled_url.startswith(action.value.rstrip("/")):
                            protocol.trace("orchestrator.action.soft_success", action=action,
                                           reason="navigation reached target after command timeout",
                                           current_url=settled_url)
                            result = type("SettledResult", (), {"ok": True, "preview": result.preview})()
                    if result.ok:
                        pass
                    else:
                        interacted = interacted or action.kind != "open"
                        failed = True
                        self.budget.failure()
                        advice = self.reflection.advise(step.goal, observation, result.preview)
                        self._progress(step, "retry", completed, current_url=observation.url,
                                       error=result.preview, advice=advice)
                        break
                interacted = True
                last_before, last_action = observation, action
                with protocol.measure("runtime.observe", phase="after_action", action=action.kind):
                    after = self.observer.capture()
                if not self.validator.action(observation, after, action):
                    # A successful browser command is still progress even when
                    # the page has not exposed a visible change yet. This is
                    # common for input events, autocomplete menus and async
                    # navigation. Start the next observation immediately;
                    # only command errors consume the failure budget.
                    protocol.trace("orchestrator.action.uncertain", action=action,
                                   reason="command succeeded but page snapshot did not change",
                                   current_url=after.url)
                    self._progress(step, "progress_uncertain", completed,
                                   current_url=after.url, action=asdict(action),
                                   advice="动作已执行，页面暂未显示可验证变化；继续基于最新页面观察推进。")
                    observation = after
                    continue
                observation = after
            if (not failed and goal_complete(
                    observation,
                    last_before,
                    last_action,
                    phase="after_action",
                    allow_model=needs_model_verification())):
                self._complete(step, completed, observation, verification_source)
                protocol.log(f"flow=verify step={step.id} status=passed")
                return None
            if not failed:
                # Repeating the same navigation between two pages is a cycle,
                # not progress.  Stop before it consumes the entire budget.
                if last_before and last_action and page_key(last_before.url) != page_key(observation.url):
                    acted_on = next((element.get("text", "") for element in last_before.elements
                                     if element.get("ref") == last_action.ref), "")
                    transition = (page_key(last_before.url), last_action.kind,
                                  acted_on, last_action.value, page_key(observation.url))
                    transitions[transition] = transitions.get(transition, 0) + 1
                    if transitions[transition] >= 2:
                        protocol.trace("orchestrator.cycle_detected", transition=transition,
                                       count=transitions[transition])
                        self._progress(step, "failed", completed, current_url=observation.url,
                                       reason="页面往返循环，未能确认目标完成")
                        return f"步骤 {step.id} 停止：页面往返循环，未能确认目标完成"
                # The actions completed successfully but the step's final
                # criterion is not visible yet. This is normal multi-action
                # progress, so do not convert it into a failure.
                slow = True
                advice = "动作已执行但步骤尚未完成；继续观察当前页面并选择下一步。"
                self._progress(step, "progressing", completed,
                               current_url=observation.url, advice=advice)
                continue
            slow = failures >= 2
        protocol.trace("orchestrator.step.exhausted", budget=self.budget.snapshot(), failures=failures)
        self._progress(step, "failed", completed, reason="预算或重试次数耗尽",
                       budget=asdict(self.budget.snapshot()), failures=failures)
        return f"步骤 {step.id} 失败：预算或重试次数耗尽"
