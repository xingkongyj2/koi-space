"""Planner contracts, dependency graphs and full multi-turn context."""
import json
import unittest
from copy import deepcopy
from unittest.mock import Mock

from koi_agent.planner import PlanError, Planner, parse_plan


def step(step_id="s1", **changes):
    return {
        "id": step_id, "goal": "打开 https://example.com/",
        "success_criteria": ["url_prefix:https://example.com/"],
        "depends_on": [], "start_url": "https://example.com/",
        "needs_user_confirmation": False, "risk": "low", "parallel_group": "",
        **changes,
    }


def ready(*steps):
    return {"status": "ready", "needs_browser": True, "question": "",
            "direct_answer": "", "steps": list(steps) or [step()]}


def ask(question="请提供网址"):
    return {"status": "ask", "needs_browser": True, "question": question,
            "direct_answer": "", "steps": []}


class PlannerTests(unittest.TestCase):
    def parse(self, value):
        return parse_plan(json.dumps(value, ensure_ascii=False))

    def test_ready_round_trips_fixed_contract(self):
        value = ready()
        self.assertEqual(self.parse(value).to_dict(), value)

    def test_all_statuses_have_fixed_fields(self):
        for value in [ask(), {"status": "direct", "needs_browser": False,
                              "question": "", "direct_answer": "4", "steps": []}]:
            self.assertEqual(self.parse(value).to_dict(), value)
        self.assertTrue(self.parse(ask()).needs_browser)

    def test_fenced_json_tolerated(self):
        self.assertEqual(parse_plan('```json\n' + json.dumps(ask()) + '\n```').question, "请提供网址")

    def test_invalid_json_and_duplicate_keys_rejected(self):
        for raw in ['[]', 'not json', '{"status":"ask","status":"ready"}']:
            with self.subTest(raw=raw), self.assertRaises(PlanError):
                parse_plan(raw)

    def test_missing_extra_fields_and_inconsistent_status_rejected(self):
        cases = []
        missing = ready()
        del missing["question"]
        cases.append(missing)
        cases.extend([
            {**ready(), "output_schema": {}}, {**ready(), "needs_browser": "false"},
            {**ready(), "needs_browser": False}, {**ready(), "steps": []},
            {**ready(), "question": "which?"}, {**ready(), "steps": "s1"},
            {**ask(), "steps": [step()]}, {**ask(), "question": ""},
            {**ask(), "direct_answer": "answer"},
            {**ask(), "status": "direct", "question": "", "direct_answer": "4"},
            ready(*[step(f"s{i}") for i in range(1, 14)]),
        ])
        for value in cases:
            with self.subTest(value=value), self.assertRaises(PlanError):
                self.parse(value)

    def test_invalid_step_types_and_criteria_rejected(self):
        changes = [
            {"id": ""}, {"goal": " "}, {"goal": 12}, {"depends_on": "s1"},
            {"success_criteria": []}, {"success_criteria": "url_prefix:https://example.com"},
            {"success_criteria": ["页面加载成功"]}, {"success_criteria": ["text_contains:"]},
            {"success_criteria": ["url_prefix:example.com"]},
            {"success_criteria": [True]}, {"risk": "urgent"},
            {"needs_user_confirmation": "false"}, {"parallel_group": None},
        ]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(PlanError):
                self.parse(ready(step(**change)))
        value = ready()
        del value["steps"][0]["depends_on"]
        with self.assertRaises(PlanError):
            self.parse(value)

    def test_invalid_urls_rejected(self):
        for url in ["", "example.com", "javascript:alert(1)", "file:///tmp/test", "https://",
                    "https://user:password@example.com", "https://example.com/{id}",
                    "https://example.com/a b", "https://example.com:wrong"]:
            with self.subTest(url=url), self.assertRaises(PlanError):
                self.parse(ready(step(start_url=url)))

    def test_single_parallel_serial_and_fan_in(self):
        for steps in [
            [step()],
            [step(parallel_group="p1"), step("s2", parallel_group="p1")],
            [step(), step("s2", depends_on=["s1"])],
            [step(parallel_group="p1"), step("s2", parallel_group="p1"),
             step("s3", depends_on=["s1", "s2"])],
        ]:
            value = ready(*steps)
            self.assertEqual(self.parse(value).to_dict(), value)

    def test_bad_dependencies_rejected(self):
        for steps in [
            [step(depends_on=["s1"])], [step(depends_on=["s2"])],
            [step(), step()], [step(), step("s2", depends_on=["s1", "s1"])],
            [step(depends_on=["s2"]), step("s2", depends_on=["s1"])],
            [step(parallel_group="p1"), step("s2", depends_on=["s1"], parallel_group="p1")],
            [step(parallel_group="p1"), step("s2", depends_on=["s1"]),
             step("s3", depends_on=["s2"], parallel_group="p1")],
        ]:
            with self.subTest(steps=steps), self.assertRaises(PlanError):
                self.parse(ready(*steps))

    def test_confirmed_high_risk_can_continue_without_lowering_risk(self):
        result = self.parse(ready(step(risk="high", needs_user_confirmation=False)))
        self.assertEqual(result.steps[0].risk, "high")
        self.assertFalse(result.steps[0].needs_user_confirmation)

    def test_composite_navigation_never_loses_actual_acceptance_condition(self):
        value = ready(step(goal="打开网站并搜索订单123", success_criteria=["text_contains:订单123"]))
        self.assertEqual(self.parse(value).steps[0].success_criteria, ("text_contains:订单123",))

    def test_local_repeated_calls_include_original_goal_and_every_ask(self):
        ai = Mock()
        ai.chat.side_effect = [json.dumps(ask("哪个网站？")), json.dumps(ask("请提供完整网址")), json.dumps(ready())]
        planner = Planner(ai)
        planner.plan("查询采购订单")
        planner.plan("公司的网站")
        planner.plan("https://example.com/")
        payload = json.loads(ai.chat.call_args.args[1])
        self.assertEqual([event["text"] for event in payload["history"] if event["type"] == "user_input"],
                         ["查询采购订单", "公司的网站", "https://example.com/"])
        previous = [json.loads(event["text"]) for event in payload["history"] if event["type"] == "thinking"]
        self.assertEqual([event["plan"]["question"] for event in previous], ["哪个网站？", "请提供完整网址"])

    def test_explicit_history_is_authoritative_untruncated_and_unmodified(self):
        ai = Mock()
        ai.chat.return_value = json.dumps(ask())
        planner = Planner(ai)
        planner.plan("不属于当前会话")
        events = [{"type": "user_input", "text": "查订单"}]
        events += [{"type": "tool_result", "preview": "x" * 20000, "ok": True} for _ in range(20)]
        events += [{"type": "error", "message": "已失效"}, {"type": "user_input", "text": "改用第二个网址"}]
        expected = deepcopy(events)
        planner.plan("改用第二个网址", history=events, context={"failed_step": "s2"})
        payload = json.loads(ai.chat.call_args.args[1])
        self.assertEqual(payload["history"], expected)
        self.assertEqual(events, expected)
        self.assertEqual(payload["context"], {"failed_step": "s2"})

    def test_one_repair_preserves_full_history(self):
        ai = Mock()
        ai.chat.side_effect = ['{"status":"ready"}', json.dumps(ready())]
        history = [{"type": "user_input", "text": "打开 https://example.com/"}]
        plan = Planner(ai).plan(history[0]["text"], history=history)
        self.assertEqual(plan.status, "ready")
        payload = json.loads(ai.chat.call_args.args[1])
        self.assertEqual(payload["history"], history)
        self.assertIn("missing", payload["repair"]["error"])
        self.assertEqual(ai.chat.call_count, 2)

    def test_repair_is_bounded(self):
        ai = Mock()
        ai.chat.return_value = "invalid"
        with self.assertRaises(PlanError):
            Planner(ai).plan("打开网址")
        self.assertEqual(ai.chat.call_count, 2)

    def test_no_model_navigation_only_and_no_loss_of_earlier_goal(self):
        self.assertEqual(Planner().plan("").status, "ask")
        self.assertEqual(Planner().plan("open https://example.com/").status, "ready")
        self.assertEqual(Planner().plan("打开 https://example.com/ 并查询订单").status, "ask")
        planner = Planner()
        planner.plan("查询商品价格")
        self.assertEqual(planner.plan("打开 https://shop.example/").status, "ask")

    def test_planner_prompt_uses_user_site_or_default_web_entry(self):
        ai = Mock()
        ai.chat.return_value = json.dumps(ready(
            step("s1", goal="打开并确认腾讯视频网页版入口", start_url="https://v.qq.com/",
                 success_criteria=["url_prefix:https://v.qq.com/"]),
            step("s2", goal="在腾讯视频网页版中打开历史记录", start_url="https://v.qq.com/",
                 depends_on=["s1"], success_criteria=["text_contains:历史记录"]),
        ), ensure_ascii=False)
        Planner(ai).plan("打开腾讯视频历史记录")
        system_prompt = ai.chat.call_args.args[0]
        self.assertIn("腾讯视频", system_prompt)
        self.assertIn("AI Agent 浏览器助手", system_prompt)
        self.assertIn("用户写出网址时直接使用该网址", system_prompt)
        self.assertIn("只说网站名称或不规范地描述目标时", system_prompt)
        self.assertIn("https://v.qq.com/", system_prompt)
        self.assertIn("根据用户任务的自然语言描述，判断最合理的网站和页面", system_prompt)
        self.assertIn("如果只能确认网站，就使用该网站的默认官方入口", system_prompt)


if __name__ == '__main__':
    unittest.main()
