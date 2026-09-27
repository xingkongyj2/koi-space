"""Selection controls need candidate confirmation, not just text entry."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from config.config import Provider
from llm.models import JevDecision, ModelError
from react.decision import Decision
from react.executor import Action, Executor
from react.observer import Observation


class SelectionControlTests(unittest.TestCase):
    def setUp(self):
        self.model = JevDecision(
            Provider("decision", "https://example.com/v1", "key", "jev")
        )
        self.page = Observation(
            "https://example.com",
            "",
            'textbox "到达地" : 潜江',
            "",
            True,
            (
                {"ref": "@e1", "text": 'combobox "到达地" : 潜江'},
                {"ref": "@e2", "text": 'option "潜江" [selected]'},
            ),
            "请选择到达地；候选：潜江",
        )

    def test_keyboard_confirmation_uses_visible_page_errors_and_candidates(self):
        response = {
            "answers": {
                "operation": {"choice": "PRESS", "confidence": 0.9},
                "press_key": {"choice": "Enter"},
            }
        }
        with patch("llm.models._request_json", return_value=response) as request:
            choice = Decision(jev=self.model).choose(
                "选择潜江", self.page, recent_action=Action("fill", "潜江", "@e1")
            )
        self.assertEqual(choice.actions, (Action("press", "Enter"),))
        body = request.call_args.args[1]
        self.assertIn("请选择到达地", body["state"]["page"]["text"])
        self.assertIn(
            "does not confirm", body["questions"]["operation"]["instructions"]["rules"]
        )

    def test_model_cannot_invent_keyboard_commands(self):
        response = {
            "answers": {
                "operation": {"choice": "PRESS"},
                "press_key": {"choice": "Control+Delete"},
            }
        }
        with patch("llm.models._request_json", return_value=response):
            with self.assertRaises(ModelError):
                self.model.choose("选择", self.page)

    def test_enter_without_matching_candidate_escalates_instead_of_selecting_default(
        self,
    ):
        page = Observation(
            "https://example.com",
            "",
            "",
            "",
            True,
            (
                {"ref": "@e1", "text": 'combobox "目的地" : 潜江'},
                {"ref": "@e2", "text": 'option "北京北" [selected]'},
            ),
        )
        response = {
            "answers": {
                "operation": {"choice": "PRESS"},
                "press_key": {"choice": "Enter"},
            }
        }
        with patch("llm.models._request_json", return_value=response):
            with self.assertRaisesRegex(ModelError, "observed matching option"):
                self.model.choose(
                    "选择潜江", page, recent_action=Action("fill", "潜江", "@e1")
                )

    def test_missing_accessibility_candidate_does_not_block_enter(self):
        page = Observation(
            "https://example.com",
            "",
            "",
            "",
            True,
            ({"ref": "@e1", "text": 'combobox "目的地" : 潜江'},),
        )
        response = {
            "answers": {
                "operation": {"choice": "PRESS"},
                "press_key": {"choice": "Enter"},
            }
        }
        with patch("llm.models._request_json", return_value=response):
            result = self.model.choose(
                "选择潜江", page, recent_action=Action("type", "潜江", "@e1")
            )
        self.assertEqual(result["value"], "Enter")

    def test_selection_input_uses_character_typing_in_both_decision_routes(self):
        response = {
            "operation": "TYPE_TEXT",
            "confidence": 0.9,
            "target": "@e1",
            "text": "潜江",
        }
        jev = Mock()
        jev.choose.return_value = response
        action = Decision(jev=jev).choose("选择潜江", self.page).actions[0]
        self.assertEqual(action, Action("type", "潜江", "@e1"))
        ai = Mock()
        ai.chat.return_value = (
            '{"actions":[{"kind":"fill","ref":"@e1","value":"潜江"}]}'
        )
        self.assertEqual(
            Decision(ai=ai).choose("选择潜江", self.page).actions[0], action
        )
        session = Mock()
        session.run.return_value = SimpleNamespace(ok=True, preview="ok")
        Executor(session).execute(action)
        self.assertEqual(
            [call.args[0] for call in session.run.call_args_list],
            [["fill", "@e1", ""], ["type", "@e1", "潜江"]],
        )

    def test_ref_keypress_focuses_target_then_uses_cli_key_syntax(self):
        session = Mock()
        session.run.return_value = SimpleNamespace(ok=True, preview="ok")
        Executor(session).execute(Action("press", "ArrowDown", "@e1"))
        self.assertEqual(
            [call.args[0] for call in session.run.call_args_list],
            [["focus", "@e1"], ["press", "ArrowDown"]],
        )

    def test_focus_failure_does_not_press_on_another_control(self):
        session = Mock()
        session.run.return_value = SimpleNamespace(ok=False, preview="missing target")
        result = Executor(session).execute(Action("press", "Enter", "@e1"))
        self.assertFalse(result.ok)
        session.run.assert_called_once_with(["focus", "@e1"])


if __name__ == "__main__":
    unittest.main()
