"""Task state machine for the Koi browser agent.

This module owns control flow only. All browser/model/storage concerns are
injected through the dedicated components, which keeps the loop testable.
"""
from __future__ import annotations
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

    def run(self) -> str:
        completed: list[str] = []
        for step in self.plan.steps:
            missing = [dep for dep in step.depends_on if dep not in completed]
            if missing:
                protocol.log(f"flow=plan step={step.id} status=blocked dependencies={missing}")
                continue
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

    def _run_step(self, step, completed: list[str]) -> str | None:
        failures = 0
        slow = False
        advice = ""
        while self.budget.allow():
            self.budget.consume_step()
            observation = self.observer.capture()
            # The first observation may already satisfy a navigation step.
            # Verify before asking Jev or a text model for another action.
            if self.validator.step(observation, step.success_criteria, step.start_url):
                completed.append(step.id)
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
            if not decision.actions:
                if self.validator.step(observation, step.success_criteria, step.start_url):
                    completed.append(step.id)
                    protocol.log(f"flow=verify step={step.id} status=passed")
                    return None
                failures += 1
                self.budget.failure()
                advice = self.reflection.advise(step.goal, observation, "no action")
                slow = True
                continue
            failed = False
            for action in decision.actions:
                if step.needs_user_confirmation:
                    protocol.notify(f"步骤需要用户确认：{step.goal}", "warning")
                    return f"等待用户确认：{step.goal}"
                try:
                    result = self.executor.execute(action)
                except Exception as exc:  # executor turns operational errors into reflection input
                    failed = True
                    self.budget.failure()
                    advice = self.reflection.advise(step.goal, observation, str(exc))
                    break
                if not result.ok:
                    failed = True
                    self.budget.failure()
                    advice = self.reflection.advise(step.goal, observation, result.preview)
                    break
                after = self.observer.capture()
                if not self.validator.action(observation, after, action):
                    failed = True
                    self.budget.failure()
                    advice = self.reflection.advise(step.goal, after, "no-op")
                    break
                observation = after
            if not failed and self.validator.step(observation, step.success_criteria, step.start_url):
                completed.append(step.id)
                protocol.log(f"flow=verify step={step.id} status=passed")
                return None
            failures += 1
            slow = failures >= 2
            if failures >= 3:
                break
        return f"步骤 {step.id} 失败：预算或重试次数耗尽"
