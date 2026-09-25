import json, unittest
from koi_agent.planner import Planner, PlanError, parse_plan
class PlannerTests(unittest.TestCase):
    def test_parse_ready(self):
        p=parse_plan(json.dumps({'status':'ready','needs_browser':True,'steps':[{'id':'s1','goal':'open','success_criteria':['url']}]}))
        self.assertEqual(p.steps[0].id,'s1')
    def test_fenced_json(self):
        self.assertEqual(parse_plan('```json\n{"status":"ask","question":"which site?"}\n```').question,'which site?')
    def test_invalid(self):
        with self.assertRaises(PlanError): parse_plan('{"status":"ready","steps":[]}')
    def test_empty_asks(self): self.assertEqual(Planner().plan('').status,'ask')
    def test_url_ready(self): self.assertTrue(Planner().plan('open https://example.com').needs_browser)
if __name__ == '__main__': unittest.main()
