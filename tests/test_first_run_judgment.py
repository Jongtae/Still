"""Console setup through the real route/store/qualification boundary; fake CLI only."""
from unittest.mock import patch

from personal_agent import first_run
from test_decision_routes import ServiceFixture, CliRunner
from test_first_run import ScriptedTerminal


class ConsoleJudgmentTests(ServiceFixture):
    def wizard(self, service, *, fresh=False):
        if not self.store.claimed():
            self.store.claim(self.store.bootstrap.read_text(), 'fixture-password-123')
        self.store.put('telegram', {'enabled': True})
        if not fresh:
            service.connect_subscription_engine({'engine': 'codex', 'officially_authenticated': True})
        terminal = ScriptedTerminal(asks=['1'] if fresh else [])
        self.assertTrue(first_run.run(service, self.store, terminal))
        return terminal

    def test_fresh_codex_setup_queues_then_qualifies_a_real_decision_route(self):
        service = self.service()
        terminal = self.wizard(service, fresh=True)
        status = service.decision_routes.status()
        self.assertEqual(status['qualification']['state'], 'queued')
        self.assertEqual(status['effective']['state'], 'checking')
        self.assertIn(first_run.msg('first_run.judgment_checking'), terminal.text())
        # Setup itself never executes a judgment/model call.
        self.assertFalse(any('--output-schema' in c['argv'] for c in self.runner.calls))
        self.assertTrue(service.run_due_qualification())
        self.assertEqual(service.decision_routes.status()['effective']['state'], 'active')
        self.assertEqual(self.judge(service).outcome, 'yes')

    def test_resumed_setup_with_connected_cli_also_queues_qualification(self):
        service = self.service()
        self.wizard(service)
        self.assertEqual(service.decision_routes.status()['qualification']['state'], 'queued')

    def test_explicit_off_and_separate_routes_are_preserved(self):
        for route in ({'transport': 'off', 'activated_at': 1},
                      {'transport': 'direct_api', 'activated_at': 1}):
            with self.subTest(route=route):
                service = self.service()
                self.store.put('decision_route', route)
                with patch.object(service.decision_routes, 'follow_main_switched') as follow:
                    self.wizard(service)
                follow.assert_not_called()
                self.assertEqual(service.decision_routes.active(), route)

    def test_existing_active_follow_route_is_not_requalified(self):
        service = self.service()
        service.connect_subscription_engine({'engine': 'codex', 'officially_authenticated': True})
        self.follow(service)
        with patch.object(service.decision_routes, 'follow_main_switched') as follow:
            terminal = self.wizard(service)
        follow.assert_not_called()
        self.assertIn(first_run.msg('first_run.judgment_active'), terminal.text())

    def test_pending_and_failed_qualifications_are_not_replaced_or_retried(self):
        service = self.service(runner=CliRunner(fail='timeout'))
        service.connect_subscription_engine({'engine': 'codex', 'officially_authenticated': True})
        service.decision_routes.follow_main_switched('codex')
        pending = service.decision_routes.status()['qualification']
        with patch.object(service.decision_routes, 'follow_main_switched') as follow:
            self.wizard(service)
        follow.assert_not_called()
        self.assertEqual(service.decision_routes.status()['qualification']['id'], pending['id'])
        self.assertTrue(service.run_due_qualification())
        self.assertEqual(service.decision_routes.status()['effective']['state'], 'attention')
        with patch.object(service.decision_routes, 'follow_main_switched') as follow:
            terminal = self.wizard(service)
        follow.assert_not_called()
        self.assertIn(first_run.msg('first_run.judgment_attention'), terminal.text())
        self.assertNotIn(first_run.msg('first_run.judgment_active'), terminal.text())
