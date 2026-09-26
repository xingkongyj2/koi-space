"""Task state machine for the Koi browser agent.

This module owns control flow only. All browser/model/storage concerns are
injected through the dedicated components, which keeps the loop testable.
"""
from __future__ import annotations
from dataclasses import asdict
import json
import time

from .budget import Budget
from .observer import Observer
from .decision import Decision
from .executor import Executor
from .validator import Validator
from .reflection import Reflection
from .memory import Memory
from . import protocol

class Orchestrator:
    def __init__(self, session, plan, *, budget=None, decision=None,
                 memory=None, reflection=None, observer=None, executor=None,
                 validator=None, skills=None):
        self.session = session
        self.plan = plan
        self.budget = budget or Budget()
        self.observer = observer or Observer(session)
        self.decision = decision or Decision()
        self.executor = executor or Executor(session)
        self.validator = validator or Validator()
        self.memory = memory or Memory()
        self.reflection = reflection or Reflection()
        self.skills = skills

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

    def _complete(self, step, completed: list[str], observation) -> None:
        completed.append(step.id)
        self._progress(step, "completed", completed,
                       current_url=observation.url,
                       success_criteria=list(step.success_criteria))

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
            result = self._run_step(step, completed)
            if result is not None:
                self.memory.record_failure(result)
                return result
        if len(completed) != len(self.plan.steps):
            reason = f"计划依赖无法满足：{len(completed)}/{len(self.plan.steps)} 个步骤完成"
            self.memory.record_failure(reason)
            return reason
        self.memory.record_success(self.plan, completed)
        return f"任务完成：{len(completed)}/{len(self.plan.steps)} 个步骤已验证通过。"

    @protocol.traced("orchestrator.step")
    def _run_step(self, step, completed: list[str]) -> str | None:
        failures = 0
        slow = False
        advice = ""
        while self.budget.allow():
            self.budget.consume_step()
            protocol.set_context(iteration=self.budget.steps)
            protocol.trace("orchestrator.iteration", budget=self.budget.snapshot(), failures=failures, slow=slow, advice=advice)
            observation = self.observer.capture()
            # The first observation may already satisfy a navigation step.
            # Verify before asking Jev or a text model for another action.
            if self.validator.step(observation, step.success_criteria, step.start_url):
                self._complete(step, completed, observation)
                protocol.log(f"flow=verify step={step.id} status=passed source=pre_decision")
                return None
            skill = self.skills.match(step.goal, observation.url) if self.skills else None
            if skill:
                protocol.log(f"flow=decision step={step.id} route=skill")
                actions = self.skills.actions(skill)
                decision = type("SkillDecision", (), {"actions": actions, "route": "skill", "confidence": 1.0})()
            else:
                decision = self.decision.choose(
                    step.goal, observation, step.start_url, slow=slow, advice=advice
                )
            protocol.log(
                f"flow=decision step={step.id} route={decision.route} "
                f"confidence={decision.confidence:.2f} actions={len(decision.actions)}"
            )
            protocol.trace("orchestrator.actions", route=decision.route, confidence=decision.confidence, actions=decision.actions)
            if not decision.actions:
                if self.validator.step(observation, step.success_criteria, step.start_url):
                    self._complete(step, completed, observation)
                    protocol.log(f"flow=verify step={step.id} status=passed")
                    return None
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
                    result = self.executor.execute(action)
                except Exception as exc:  # executor turns operational errors into reflection input
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
                        failed = True
                        self.budget.failure()
                        advice = self.reflection.advise(step.goal, observation, result.preview)
                        self._progress(step, "retry", completed, current_url=observation.url,
                                       error=result.preview, advice=advice)
                        break
                after = self.observer.capture()
                if not self.validator.action(observation, after, action):
                    # Clicks, fills and navigation can dispatch asynchronous
                    # page updates after the command has returned. Give the
                    # page a short settle window before classifying a no-op.
                    if action.kind in {"open", "click", "fill", "press", "scroll"}:
                        time.sleep(0.8)
                        settled = self.observer.capture()
                        if self.validator.action(observation, settled, action):
                            after = settled
                        else:
                            after = settled
                    if self.validator.action(observation, after, action):
                        observation = after
                        continue
                    # A successful browser command is still progress even when
                    # the page has not exposed a visible change yet. This is
                    # common for input events, autocomplete menus and async
                    # navigation. Keep the fresh observation and let the next
                    # decision advance the task; only command errors consume
                    # the failure budget.
                    protocol.trace("orchestrator.action.uncertain", action=action,
                                   reason="command succeeded but page snapshot did not change",
                                   current_url=after.url)
                    self._progress(step, "progress_uncertain", completed,
                                   current_url=after.url, action=asdict(action),
                                   advice="动作已执行，页面暂未显示可验证变化；继续基于最新页面观察推进。")
                    observation = after
                    continue
                observation = after
            if not failed and self.validator.step(observation, step.success_criteria, step.start_url):
                self._complete(step, completed, observation)
                protocol.log(f"flow=verify step={step.id} status=passed")
                return None
            if not failed:
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
