"""执行层改造的行为回归：快照复用、完成门、预算、安全门与增量恢复。"""

import io
import json
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from unittest.mock import Mock

from memory.skills import SkillLibrary
from planner.planner import Plan, PlanError, Planner, Step, parse_plan
from react.budget import (
    Budget,
    BudgetExceeded,
    StepBudget,
    before_model_request,
    model_scope,
    record_usage,
)
from react.completion import (
    CompletionGate,
    CompletionResult,
    CompletionVerifier,
    TaskValidator,
)
from react.decision import Decision, DecisionResult
from react.executor import Action
from react.observer import Observation
from react.orchestrator import Orchestrator
from react.outcomes import StepOutcome
from react.validator import Validator, url_matches


def page(text="开始", version=1, url="https://example.com/"):
    return Observation(
        url,
        "",
        f'- button "{text}" [ref=e{version}]',
        "",
        True,
        ({"ref": f"@e{version}", "text": f'button "{text}"'},),
        version=version,
    )


def step(step_id="s1", criteria=None, **kwargs):
    return Step(
        step_id,
        "显示结果",
        criteria or ({"type": "element_text", "value": "完成"},),
        start_url="https://example.com/",
        **kwargs,
    )


class RuntimeContractTests(unittest.TestCase):
    def run_agent(self, steps, pages, decisions, **kwargs):
        observer = Mock()
        observer.capture.side_effect = pages
        decision = Mock()
        decision.choose.side_effect = decisions
        executor = Mock()
        executor.execute.return_value = Mock(ok=True)
        final = Mock()
        final.verify.return_value = CompletionResult(True, "test", ("最终证据",))
        agent = Orchestrator(
            Mock(),
            Plan("ready", True, steps=tuple(steps)),
            observer=observer,
            decision=decision,
            executor=executor,
            memory=Mock(),
            task_validator=final,
            **kwargs,
        )
        with redirect_stdout(io.StringIO()):
            result = agent.run()
        return result, agent, observer, decision, executor, final

    def test_after_observation_is_reused_and_actions_bind_to_current_version(self):
        result, _, observer, decision, executor, final = self.run_agent(
            [step()],
            [page(), page("继续", 2), page("完成", 3)],
            [
                DecisionResult(
                    (Action("click", ref="@e1"), Action("click", ref="@e1")), 1, "jev"
                ),
                DecisionResult((Action("click", ref="@e2"),), 1, "jev"),
            ],
        )
        self.assertEqual(result.status, "completed")
        self.assertEqual(observer.capture.call_count, 3)
        self.assertEqual(decision.choose.call_args_list[1].args[1].version, 2)
        self.assertEqual(
            [
                call.args[0].observation_version
                for call in executor.execute.call_args_list
            ],
            [1, 2],
        )
        self.assertEqual(len(result.steps[0].results), 2)
        final.verify.assert_called_once()

    def test_done_cannot_bypass_deterministic_completion(self):
        result, _, _, decision, executor, _ = self.run_agent(
            [step()], [page()], [DecisionResult((), 1, "jev", terminal="DONE")] * 3
        )
        self.assertEqual(result.status, "blocked")
        executor.execute.assert_not_called()
        self.assertTrue(decision.choose.call_args_list[1].kwargs["slow"])

    def test_initial_text_presence_cannot_complete_action_step(self):
        target = Step(
            "s1",
            "找到灵境行者并继续观看",
            (
                {"type": "url_contains", "value": "v.qq.com"},
                {"type": "text_contains", "value": "灵境行者"},
            ),
            start_url="https://v.qq.com/",
        )
        page_with_listing = Observation(
            "https://v.qq.com/",
            "",
            '- generic "灵境行者" [ref=e1]',
            "",
            True,
            ({"ref": "@e1", "text": 'generic "灵境行者"'},),
            version=1,
        )
        ai = Mock()
        gate = CompletionGate(verifier=CompletionVerifier(ai))

        result = gate.check(target, page_with_listing, initial=True)

        self.assertFalse(result.accepted)
        ai.chat.assert_not_called()

    def test_blocked_escalates_to_slow_and_success_returns_to_fast(self):
        decisions = [
            DecisionResult((), 1, "jev", terminal="BLOCKED"),
            DecisionResult((Action("click", ref="@e1"),), 1, "slow"),
            DecisionResult((Action("click", ref="@e2"),), 1, "jev"),
        ]
        result, _, _, decision, _, _ = self.run_agent(
            [step()], [page(), page("继续", 2), page("完成", 3)], decisions
        )
        self.assertEqual(result.status, "completed")
        self.assertEqual(
            [call.kwargs["slow"] for call in decision.choose.call_args_list],
            [False, True, False],
        )

    def test_same_fingerprint_does_not_repeat_completion_model(self):
        ai = Mock()
        ai.chat.return_value = json.dumps(
            {"complete": False, "confidence": 0.9, "evidence": "尚未完成"}
        )
        gate = CompletionGate(verifier=CompletionVerifier(ai))
        target = step(criteria=({"type": "goal_state", "value": "订单提交成功"},))
        gate.check(target, page(), initial=True)
        ai.chat.assert_not_called()
        for version in range(1, 4):
            self.assertFalse(gate.check(target, page(version=version)).accepted)
        self.assertEqual(ai.chat.call_count, 1)

    def test_sensitive_action_waits_before_executor_but_navigation_can_proceed(self):
        target = step(needs_user_confirmation=True, risk="high")
        result, _, _, _, executor, final = self.run_agent(
            [target],
            [page(url="about:blank"), page()],
            [
                DecisionResult((Action("open", "https://example.com/"),), 1, "rule"),
                DecisionResult((Action("click", ref="@e1"),), 1, "jev"),
            ],
        )
        self.assertEqual(result.status, "waiting_user")
        self.assertEqual(executor.execute.call_count, 1)
        final.verify.assert_not_called()

    def test_action_sensitive_flag_works_even_when_step_is_low_risk(self):
        result, _, _, _, executor, _ = self.run_agent(
            [step()],
            [page()],
            [DecisionResult((Action("click", ref="@e1", sensitive=True),), 1, "jev")],
        )
        self.assertEqual(result.status, "waiting_user")
        executor.execute.assert_not_called()

    def test_stale_action_is_rejected_before_dispatch(self):
        result, _, _, _, executor, _ = self.run_agent(
            [step()],
            [page(version=2)] * 4,
            [
                DecisionResult(
                    (Action("click", ref="@e2", observation_version=1),), 1, "jev"
                )
            ]
            * 3,
        )
        self.assertEqual(result.status, "blocked")
        executor.execute.assert_not_called()

    def test_command_error_refreshes_page_before_retry(self):
        observed = [page(), page("重试", 2), page("完成", 3)]
        observer = Mock(capture=Mock(side_effect=observed))
        decision = Mock(
            choose=Mock(
                side_effect=[
                    DecisionResult((Action("click", ref="@e1"),), 1, "jev"),
                    DecisionResult((Action("click", ref="@e2"),), 1, "slow"),
                ]
            )
        )
        executor = Mock(
            execute=Mock(side_effect=[RuntimeError("旧控件已移除"), Mock(ok=True)])
        )
        final = Mock(verify=Mock(return_value=CompletionResult(True, "test")))
        with redirect_stdout(io.StringIO()):
            result = Orchestrator(
                Mock(),
                Plan("ready", True, steps=(step(),)),
                observer=observer,
                decision=decision,
                executor=executor,
                task_validator=final,
                memory=Mock(),
            ).run()
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.steps[0].results[0].status, "execution_failed")
        self.assertEqual(decision.choose.call_args_list[1].args[1].version, 2)

    def test_dependency_ready_scheduler_handles_unordered_in_memory_plan(self):
        first = step("s1")
        second = step("s2", depends_on=("s1",))
        result, _, _, _, executor, _ = self.run_agent(
            [second, first], [page("完成"), page("完成")], []
        )
        self.assertEqual(result.completed_steps, ("s1", "s2"))
        executor.execute.assert_not_called()

    def test_missing_dependency_cannot_report_success(self):
        result, _, observer, _, _, final = self.run_agent(
            [step(depends_on=("missing",))], [], []
        )
        self.assertEqual(result.status, "blocked")
        observer.capture.assert_not_called()
        final.verify.assert_not_called()

    def test_incremental_replan_preserves_failed_attempt_and_completed_results(self):
        first, second = step("s1"), step("s2", depends_on=("s1",))
        planner = Mock()
        planner.incremental_replan.return_value = Plan(
            "ready", True, steps=(first, second)
        )
        result, _, _, _, executor, _ = self.run_agent(
            [first, second],
            [page("完成"), page(), page("完成")],
            [DecisionResult((), 1, "jev", terminal="BLOCKED")] * 3,
            planner=planner,
        )
        self.assertEqual(result.status, "completed")
        self.assertEqual(
            [outcome.status for outcome in result.steps],
            ["completed", "replan", "completed"],
        )
        self.assertEqual(result.completed_steps, ("s1", "s2"))
        planner.incremental_replan.assert_called_once()
        executor.execute.assert_not_called()

    def test_validator_distinguishes_noop_loading_and_wrong_direction(self):
        validator = Validator()
        before = page()
        self.assertEqual(
            validator.action(before, page(version=2), Action("wait")).status,
            "unchanged",
        )
        self.assertEqual(
            validator.action(
                before, replace(page(), loading=True), Action("click")
            ).status,
            "loading",
        )
        action = Action("click", expected="text_contains:目标")
        self.assertEqual(
            validator.action(before, page("错误"), action).status, "unexpected_change"
        )
        self.assertEqual(
            validator.action(before, page("目标"), action).status, "passed"
        )

    def test_composite_criteria_return_evidence_and_reject_empty_conditions(self):
        criteria = (
            {
                "type": "all",
                "value": [
                    {"type": "url_contains", "value": "example.com"},
                    {
                        "type": "any",
                        "value": [
                            {"type": "element_text", "value": "完成"},
                            {"type": "text_contains", "value": "失败"},
                        ],
                    },
                ],
            },
        )
        check = Validator().step(page("完成"), criteria)
        self.assertTrue(check.passed)
        self.assertTrue(check.evidence)
        self.assertFalse(Validator().step(page(), ()))
        self.assertFalse(
            Validator().step(page(), ({"type": "url_contains", "value": ""},))
        )

    def test_url_prefix_does_not_accept_lookalike_host_or_path(self):
        self.assertFalse(
            url_matches("https://example.com.evil/", "https://example.com/")
        )
        self.assertFalse(
            url_matches(
                "https://example.com/orders-other", "https://example.com/orders"
            )
        )
        self.assertTrue(
            url_matches("https://example.com/orders/42", "https://example.com/orders")
        )

    def test_slow_flag_and_low_jev_confidence_use_text_model(self):
        ai = Mock(
            chat=Mock(
                return_value='{"actions":[{"kind":"press","value":"Escape"}],"confidence":0.9}'
            )
        )
        jev = Mock(
            choose=Mock(
                return_value={"operation": "CLICK", "target": "@e1", "confidence": 0.2}
            )
        )
        decision = Decision(ai, jev)
        self.assertEqual(decision.choose("目标", page(), slow=True).route, "slow")
        jev.choose.assert_not_called()
        self.assertEqual(decision.choose("目标", page()).route, "slow")
        self.assertEqual(ai.chat.call_count, 2)

    def test_step_success_resets_consecutive_failures_without_resetting_task_usage(
        self,
    ):
        budget, local = Budget(max_failures=1), StepBudget()
        budget.failure()
        budget.consume_step()
        local.failures = 1
        self.assertFalse(local.allow(budget))
        local.progressed()
        self.assertTrue(local.allow(budget))
        self.assertEqual((budget.failures, budget.steps), (1, 1))

    def test_real_request_accounting_is_scoped_and_enforced(self):
        budget = Budget(max_model_calls=2)
        with budget.track_models():
            with model_scope("jev"):
                before_model_request(10)
                record_usage({"usage": {"input_tokens": 3, "output_tokens": 4}})
            with model_scope("completion"):
                before_model_request(10)
            with self.assertRaises(BudgetExceeded):
                before_model_request(10)
        self.assertEqual(dict(budget.model_calls), {"jev": 1, "completion": 1})
        self.assertEqual(budget.tokens, 7)

    def test_budget_exhaustion_does_not_fallback_to_another_model(self):
        ai = Mock(chat=Mock(side_effect=BudgetExceeded("已耗尽")))
        with self.assertRaises(BudgetExceeded):
            Decision(ai).choose("目标", page())
        with self.assertRaises(BudgetExceeded):
            CompletionVerifier(ai).verify(step(), None, page())

    def test_task_validator_checks_original_goal_and_all_results(self):
        ai = Mock(
            chat=Mock(
                return_value='{"complete":false,"confidence":0.9,"evidence":"遗漏第二个结果"}'
            )
        )
        target = step()
        outcome = StepOutcome(
            "s1", target.goal, "completed", evidence=("页面显示完成",)
        )
        verdict = TaskValidator(ai).verify(
            "要求两个结果",
            Plan("ready", True, steps=(target,)),
            (outcome,),
            page("完成"),
        )
        self.assertFalse(verdict.accepted)
        payload = json.loads(ai.chat.call_args.args[1])
        self.assertEqual(payload["original_goal"], "要求两个结果")
        self.assertEqual(payload["step_results"][0]["evidence"], ["页面显示完成"])

    def test_high_risk_verifier_failure_cannot_fallback_to_deterministic_success(self):
        ai = Mock(chat=Mock(return_value="bad json"))
        gate = CompletionGate(verifier=CompletionVerifier(ai))
        self.assertFalse(gate.check(step(risk="high"), page("完成")).accepted)

    def test_replan_rejects_changes_to_completed_steps_and_reduced_permissions(self):
        first = step("s1")
        second = step("s2", risk="high", needs_user_confirmation=True)
        plan = Plan("ready", True, steps=(first, second))
        outcomes = (StepOutcome("s1", first.goal, "completed"),)
        ai = Mock()
        for invalid in (
            replace(plan, steps=(replace(first, goal="另一任务"), second)),
            replace(
                plan, steps=(first, replace(second, needs_user_confirmation=False))
            ),
        ):
            ai.chat.return_value = json.dumps(invalid.to_dict())
            with self.assertRaises(PlanError):
                Planner(ai).incremental_replan("原目标", plan, outcomes, "s2", page())

    def test_structured_plan_conditions_round_trip(self):
        target = step(
            criteria=(
                {
                    "type": "all",
                    "value": [
                        {"type": "page_state", "value": "ready"},
                        {
                            "type": "extracted_value",
                            "value": {"key": "title", "equals": "结果"},
                        },
                    ],
                },
            )
        )
        plan = Plan("ready", True, steps=(target,))
        self.assertEqual(parse_plan(json.dumps(plan.to_dict())).steps, plan.steps)
        observed = replace(page(), extracted={"title": "结果"})
        self.assertTrue(Validator().step(observed, target.success_criteria))

    def test_skills_rebind_semantic_target_instead_of_reusing_saved_ref(self):
        stale = {"actions": [{"kind": "click", "ref": "@e1"}]}
        self.assertIsNone(SkillLibrary.next_action(stale, 0, page(version=2)))
        bound = {"actions": [{"kind": "click", "target_text": 'button "开始"'}]}
        action = SkillLibrary.next_action(bound, 0, page(version=2))
        self.assertEqual((action.ref, action.observation_version), ("@e2", 2))

    def test_semantic_model_cannot_override_structured_mandatory_conditions(self):
        ai = Mock(
            chat=Mock(
                return_value='{"complete":true,"confidence":0.9,"evidence":"模型声称已完成"}'
            )
        )
        target = step(
            criteria=(
                {
                    "type": "all",
                    "value": [
                        {"type": "goal_state", "value": "已提交"},
                        {"type": "text_contains", "value": "提交成功"},
                    ],
                },
            )
        )
        self.assertFalse(
            CompletionGate(verifier=CompletionVerifier(ai))
            .check(target, page())
            .accepted
        )

    def test_fingerprint_keeps_usernames_as_business_evidence(self):
        self.assertNotEqual(page("@alice").fingerprint, page("@bob").fingerprint)
        self.assertEqual(page(version=1).fingerprint, page(version=2).fingerprint)

    def test_skill_never_matches_another_host_even_with_many_matching_terms(self):
        library = SkillLibrary()
        library._items = [
            {"terms": ["open", "read", "item"], "host": "other.example.com"}
        ]
        self.assertIsNone(library.match("open read item", "https://example.com/"))

    def test_structured_navigation_on_same_host_uses_rule_without_a_model(self):
        target_url = "https://example.com/orders"
        choice = Decision().choose(
            "打开订单",
            page(url="https://example.com/other"),
            target_url,
            force_entry=True,
        )
        self.assertEqual(choice.route, "rule")
        self.assertEqual(choice.actions[0].value, target_url)

    def test_model_budget_failure_preserves_the_current_partial_step(self):
        decisions = [
            DecisionResult((Action("click", ref="@e1"),), 1, "jev"),
            BudgetExceeded("模型调用已耗尽"),
        ]
        result, _, _, _, _, final = self.run_agent(
            [step()], [page(), page("继续", 2)], decisions
        )
        self.assertEqual(result.status, "failed")
        self.assertEqual(len(result.steps[0].results), 1)
        final.verify.assert_not_called()

    def test_main_emits_error_instead_of_done_for_failed_outcome(self):
        from unittest.mock import patch

        import main as entrypoint
        from config.config import Provider, Settings
        from react.outcomes import TaskOutcome

        settings = Settings(
            Provider("planner", "", "", ""), Provider("decision", "", "", "")
        )
        output = io.StringIO()
        with (
            patch.object(entrypoint, "load_settings", return_value=settings),
            patch.object(entrypoint.browser, "BrowserSession"),
            patch.object(entrypoint.browser, "log_environment"),
            patch.object(entrypoint, "Orchestrator") as orchestrator,
            redirect_stdout(output),
        ):
            orchestrator.return_value.run.return_value = TaskOutcome(
                "failed", "预算耗尽"
            )
            entrypoint.run_task(
                {"browser": {"cdpPort": 9222, "targetId": "own-tab"}},
                "打开 https://example.com/",
                "session",
                [],
            )
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(events[-1]["type"], "error")
        self.assertNotIn("done", [event["type"] for event in events])


if __name__ == "__main__":
    unittest.main()
