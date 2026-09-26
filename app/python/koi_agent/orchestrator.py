"""Task state machine for the Koi browser agent.

This module owns control flow only. All browser/model/storage concerns are
injected through the dedicated components, which keeps the loop testable.
"""
from __future__ import annotations
from dataclasses import asdict
import json

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
                    failed = True
                    self.budget.failure()
                    advice = self.reflection.advise(step.goal, observation, result.preview)
                    self._progress(step, "retry", completed, current_url=observation.url,
                                   error=result.preview, advice=advice)
                    break
                after = self.observer.capture()
                if not self.validator.action(observation, after, action):
                    failed = True
                    self.budget.failure()
                    advice = self.reflection.advise(step.goal, after, "no-op")
                    self._progress(step, "retry", completed, current_url=after.url,
                                   error="no-op", advice=advice)
                    break
                observation = after
            if not failed and self.validator.step(observation, step.success_criteria, step.start_url):
                self._complete(step, completed, observation)
                protocol.log(f"flow=verify step={step.id} status=passed")
                return None
            failures += 1
            slow = failures >= 2
            if failures >= 3:
                break
        protocol.trace("orchestrator.step.exhausted", budget=self.budget.snapshot(), failures=failures)
        self._progress(step, "failed", completed, reason="预算或重试次数耗尽",
                       budget=asdict(self.budget.snapshot()), failures=failures)
        return f"步骤 {step.id} 失败：预算或重试次数耗尽"
