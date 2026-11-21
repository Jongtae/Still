"""#1077: the plan, which may write the owner's direct reply, asks for the route's most capable qualified model.

Evidence class: deterministic unit tests (orchestrator harness, fixture plans; no model or CLI).
The engine side, which sends the stronger model and falls back once on a refusal, is covered by
``test_goal_judgment_model_1071``.  Whether that model keeps a reply in the owner's language is a
live-model question; the replay notes are on #1077, and nothing here claims it.
"""
from test_orchestrator import Harness
from test_orchestrator import plan


class PlanModel(Harness):
    def test_every_plan_call_asks_for_the_strongest_qualified_model(self):
        self.script([dict(plan('codex', ''), reply='네, 정리했어요.', recall_query='',
                          brief={'notes': '', 'lens': '', 'needs': ''})])
        self.run_work('帮我整理一份购物清单。')
        self.assertTrue(self.asked_plans)
        self.assertTrue(all(context.qualified_strongest for context, _q, _s in self.asked_plans))
