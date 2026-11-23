"""#1172: a request whose outcome is a target to reach is briefed for depth.

Model-free: the planner, worker, outcome-judgment and line-account prompts carry the
general rule (no method names), and an effort the plan chooses for a direct-API worker
reaches that worker's model call instead of being dropped.
"""
import unittest

from personal_agent.prompt_files import prompt

from test_orchestrator import Harness, plan


class TargetRuleInPrompts(unittest.TestCase):
    def test_planner_reads_a_target_as_reached_not_planned_and_raises_effort(self):
        text = prompt('planner')
        self.assertIn('target to reach', text)
        self.assertIn('A plan or a scheduled continuation alone does not serve it', text)
        self.assertIn('highest effort level listed', text)

    def test_outcome_judgment_does_not_accept_a_schedule_alone_for_a_target(self):
        text = prompt('goal_reached')
        self.assertIn('target to reach', text)
        self.assertIn('a plan or a scheduled continuation alone does not complete it', text)

    def test_worker_and_line_account_report_what_was_tried_and_why(self):
        for name in ('worker_core', 'line_account'):
            with self.subTest(prompt=name):
                text = prompt(name)
                self.assertIn('target', text)
                self.assertIn('tried', text)
                self.assertIn('why', text)

    def test_the_rule_names_no_method_or_domain(self):
        """C16: the AI chooses its methods; Still prompts carry no named technique or strategy."""
        for name in ('planner', 'goal_reached', 'worker_core', 'line_account'):
            text = prompt(name).lower()
            for word in ('reflexion', 'alphaevolve', 'backtest', 'trading', 'strategy', 'chain-of-thought'):
                with self.subTest(prompt=name, word=word):
                    self.assertNotIn(word, text)


class ApiWorkerEffort(Harness):
    """The live Main AI on a direct-API route was offered no effort levels, so the plan's
    effort was dropped and the Work ran with ``effort: null``."""

    def test_the_catalogue_offers_the_api_models_documented_efforts(self):
        from personal_agent.orchestrator import render_catalogue, worker_catalogue
        catalogue = worker_catalogue(self.service)
        worker = catalogue.worker('openai')
        self.assertEqual(worker['model_efforts'].get(worker['default_model']),
                         list(self.service.decision_routes.api_model_efforts('openai', worker['default_model'])))
        self.assertIn('effort: low/medium/high/xhigh/max', render_catalogue([worker]))

    def test_a_target_plan_keeps_its_chosen_effort_to_the_api_model_call(self):
        self.use_main('openai')
        self.script([plan('openai', '', effort='max')], goals=[True])
        job, row = self.run_work('Fixture target to reach')
        self.assertEqual(row['status'], 'succeeded')
        self.assertEqual(self.transport.bodies[-1]['reasoning'], {'effort': 'max'})
        self.assertEqual(self.store.turn_provenance(job)['workers'][-1]['effort'], 'max')
        [(_s, planned)] = self.events(job, 'planned')
        self.assertEqual(planned['effort'], 'max')

    def test_an_undeclared_effort_still_runs_the_model_default(self):
        self.use_main('openai')
        self.script([plan('openai', '', effort='extreme')], goals=[True])
        job, row = self.run_work('Fixture undeclared effort')
        self.assertEqual(row['status'], 'succeeded')
        self.assertNotIn('reasoning', self.transport.bodies[-1])
        [(_s, planned)] = self.events(job, 'planned')
        self.assertIsNone(planned['effort'])


if __name__ == '__main__':
    unittest.main()
