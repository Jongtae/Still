"""#988: qualified follow fallback, with injected checks and fixture transports only."""
import json
import types
from pathlib import Path
from unittest import mock

from personal_agent.decision import OUTCOME_UNAVAILABLE, DecisionContext, ModelDecisionEngine, UnavailableDecisionEngine
from personal_agent.decision_adapters import SubscriptionCliDecisionEngine
from personal_agent.decision_qualification import SUITE_VERSION
from personal_agent.decision_routes import MODE_FOLLOW
from personal_agent.information_use import work_information_use
from test_decision_routes import CliRunner, ServiceFixture
from test_orchestrator import Transport, plan


class PlanningRunner(CliRunner):
    def __call__(self, argv, **kwargs):
        # Exercise the real CLI adapter and its audit, replacing only subprocess I/O.
        if '--json-schema' in argv:
            schema = json.loads(argv[argv.index('--json-schema') + 1])
            if 'worker' in schema['properties']:
                self.calls.append({'argv': list(argv)})
                answer = {**plan('openai', 'Answer the request.'), 'confidence': 0.99,
                          'effort': '', 'account_change': False, 'owner_situation': False, 'technical_work': False,
                          'ask_location': '', 'location_button': '',
                          'preview': {'understanding': '', 'approach': '', 'steps': []}}
                answer = {key: value for key, value in answer.items() if key in schema['properties']}
                return types.SimpleNamespace(returncode=0, stderr='', stdout=json.dumps(
                    {'type': 'result', 'structured_output': answer,
                     'modelUsage': {'fallback-observed-model': {'inputTokens': 5}}}))
        return super().__call__(argv, **kwargs)


class QualifiedFallback(ServiceFixture):
    def setUp(self):
        super().setUp()
        self.service_under_test = self.service(runner=PlanningRunner(), openai=Transport())
        self.routes = self.service_under_test.decision_routes
        self.now = 10
        self.routes.clock = lambda: self.now

    def qualify_cli(self, engine):
        model = 'haiku' if engine == 'claude-code' else 'gpt-5.6-luna'
        result = {'qualified': True, 'suite_version': SUITE_VERSION, 'score': 1.0}
        tried = [{'model': model, 'qualified': True, 'score': 1.0}]
        with mock.patch.object(self.routes, '_first_qualified', return_value=(model, result, tried)):
            self.service_under_test.activate_decision_route(
                {'transport': 'subscription_cli', 'engine': engine, 'model_policy': 'lowest_qualified'})
        self.now += 10
        return self.routes.active()

    def failed_follow(self, previous=None):
        service = self.service_under_test
        if previous:
            self.store.put('decision_route', {**previous, 'mode': MODE_FOLLOW, 'main': previous['engine']})
        else:
            self.store.put('decision_route', None)
        service.save_main_ai_key({'provider': 'openai', 'key': 'sk-fixture-fallback-key'})
        service.activate_main_ai({'route': 'openai'})
        with mock.patch.object(self.routes, '_first_qualified', return_value=(None, None, [])):
            self.assertTrue(service.run_due_qualification())
        self.assertEqual(self.routes._checks()['follow_main:openai']['failure'], 'no-qualified-candidate')

    def test_work_is_orchestrated_and_names_actual_judgment_recipient(self):
        previous = self.qualify_cli('claude-code')
        self.failed_follow(previous)
        service = self.service_under_test
        before = self.routes.active()
        status = self.routes.status()
        self.assertEqual((status['mode'], status['active']['source'], status['active']['engine']),
                         (MODE_FOLLOW, 'fallback', 'claude-code'))
        self.assertEqual(status['effective']['state'], 'fallback')
        self.assertIn('Claude Code', status['effective']['text'])
        self.assertIn('haiku', status['effective']['text'])
        self.assertIn('Claude Code', service.main_ai.command(''))
        self.assertIn('haiku', service.settings_orchestrator._judgment_ai()['effective'])
        job = self.store.enqueue('Explain this.', 'fallback-work')
        self.assertTrue(service.run_one())
        with self.store.db() as db:
            rows = db.execute("SELECT status FROM tool_events WHERE job_id=? AND tool='orchestrator'", (job,)).fetchall()
        self.assertIn('planned', [row['status'] for row in rows], self.store.work_decisions(job))
        self.assertNotIn('fallback', [row['status'] for row in rows])
        self.assertIn('Claude Code', self.store.job(job)['response'])
        self.assertIn('haiku', self.store.job(job)['response'])
        calls = [row for row in self.store.work_decisions(job) if row['purpose'] == 'work-orchestration']
        self.assertTrue(calls)
        self.assertEqual({row['engine'] for row in calls}, {'claude-code'})
        audit = work_information_use(self.store, job, compare_held_values=False)
        self.assertIn('fallback-observed-model', audit['sent_to']['judgment_ai']['models'])
        self.assertEqual(self.routes.active(), before, 'fallback does not replace the follow choice')

    def test_notice_and_information_use_survive_switchback_before_delivery(self):
        previous = self.qualify_cli('claude-code')
        self.failed_follow(previous)
        service = self.service_under_test
        transport = service.adapter.transport
        switched = []

        def reply_and_requalify(*args, **kwargs):
            reply = transport(*args, **kwargs)
            if not switched:
                switched.append(True)
                self.routes.queue_qualification('openai')
                result = {'qualified': True, 'suite_version': SUITE_VERSION, 'score': 1.0}
                with mock.patch.object(self.routes, '_first_qualified',
                                       return_value=('gpt-6-luna', result, [])):
                    self.assertTrue(service.run_due_qualification())
            return reply

        service.adapter.transport = reply_and_requalify
        job = self.store.enqueue('Explain this.', 'switch-before-delivery')
        self.assertTrue(service.run_one())
        self.assertTrue(switched)
        self.assertEqual(self.routes.status()['effective']['state'], 'active')
        self.assertIn('Claude Code', self.store.job(job)['response'])
        self.assertIn('haiku', self.store.job(job)['response'])
        audit = work_information_use(self.store, job, compare_held_values=False)
        self.assertIn('fallback-observed-model', audit['sent_to']['judgment_ai']['models'])

    def test_no_qualified_route_keeps_judgment_unavailable(self):
        self.failed_follow()
        self.assertIsInstance(self.routes.engine(), UnavailableDecisionEngine)
        self.assertEqual(self.routes.status()['effective']['state'], 'attention')
        job = self.store.enqueue('Explain this.', 'no-judgment-work')
        self.assertTrue(self.service_under_test.run_one())
        with self.store.db() as db:
            row = db.execute("SELECT detail FROM tool_events WHERE job_id=? AND tool='orchestrator' AND status='fallback'",
                             (job,)).fetchone()
        self.assertEqual(json.loads(row['detail'])['code'], 'judgment_unavailable')

    def test_newest_qualification_wins_and_unavailable_routes_are_skipped(self):
        older = self.qualify_cli('claude-code')
        newer = self.qualify_cli('codex')
        self.failed_follow(newer)
        checks = self.routes._checks()
        checks['subscription_cli:claude-code']['checked_at'] = self.now + 100
        self.store.put('decision_route_checks', checks)
        self.assertEqual(self.routes.engine().engine_id, 'codex')
        # The most recent route lost its binary: keep the older usable qualification.
        Path(self.finder('codex')).unlink()
        self.assertEqual(self.routes.engine().engine_id, 'claude-code')
        self.assertEqual(self.routes.status()['active']['requested_model'], older['requested_model'])

    def test_selection_uses_recency_across_transports(self):
        previous = self.qualify_cli('claude-code')
        self.store.secret('decision_model_key', 'sk-fixture-judgment-key')
        model = 'qualified-api-model'
        result = {'qualified': True, 'suite_version': SUITE_VERSION, 'score': 1.0}
        with mock.patch.object(self.routes, '_first_qualified', return_value=(model, result, [])):
            self.service_under_test.activate_decision_route({'transport': 'direct_api', 'model': model})
        self.failed_follow(previous)
        engine = self.routes.engine()
        self.assertIsInstance(engine, ModelDecisionEngine)
        self.assertEqual(engine.route()[0]['model'], model)
        self.assertEqual(self.routes.status()['effective']['model'], model)
        self.assertIn(model, self.service_under_test.main_ai.command(''))

    def test_background_requalification_keeps_fallback_and_switches_back_on_success(self):
        previous = self.qualify_cli('claude-code')
        self.failed_follow(previous)
        service = self.service_under_test
        self.routes.queue_qualification('openai')
        revision = self.routes._route_rev()
        self.assertIsInstance(self.routes.engine(), SubscriptionCliDecisionEngine)
        self.assertEqual(self.routes.status()['effective']['state'], 'checking')
        self.assertIn('Claude Code', service.main_ai.command(''))
        result = {'qualified': True, 'suite_version': SUITE_VERSION, 'score': 1.0}
        model = 'gpt-6-luna'
        with mock.patch.object(self.routes, '_first_qualified', return_value=(model, result, [])):
            self.assertTrue(service.run_due_qualification())
        self.assertEqual(self.routes.status()['effective']['state'], 'active')
        self.assertEqual(self.routes.active()['requested_model'], model)
        self.assertNotEqual(self.routes._route_rev(), revision)
        self.assertEqual(self.routes.engine().route()[0]['model'], model)

    def test_legacy_active_check_reuses_the_persisted_isolation_evidence(self):
        previous = self.qualify_cli('codex')
        self.failed_follow(previous)
        checks = self.routes._checks()
        checks['subscription_cli:codex'].pop('route')
        self.store.put('decision_route_checks', checks)
        self.assertEqual(self.routes.engine().engine_id, 'codex')
        self.assertTrue(self.routes._fallback_route()['strict_profile']['binding'])

    def test_legacy_secondary_check_can_supply_the_fallback(self):
        self.qualify_cli('claude-code')
        self.failed_follow()
        checks = self.routes._checks()
        checks['subscription_cli:claude-code'].pop('route')
        self.store.put('decision_route_checks', checks)
        self.assertEqual(self.routes.engine().engine_id, 'claude-code')
        # A later capability check cannot replace the identity that qualified.
        capabilities = self.routes._capabilities()
        capabilities['claude-code']['checked_at'] = self.now + 1
        self.store.put('decision_cli_capabilities', capabilities)
        self.assertIsInstance(self.routes.engine(), UnavailableDecisionEngine)

    def test_probe_only_failed_and_old_suite_checks_are_not_qualified(self):
        previous = self.qualify_cli('claude-code')
        self.failed_follow(previous)
        original = self.routes._checks()
        for change in ({'state': 'failed'}, {'route': {**previous, 'qualification': {}}},
                       {'route': {**previous, 'qualification': {'suite_version': 'old'}}}):
            checks = {**original, 'subscription_cli:claude-code': {**original['subscription_cli:claude-code'], **change}}
            self.store.put('decision_route_checks', checks)
            self.assertIsInstance(self.routes.engine(), UnavailableDecisionEngine)

    def test_explicit_and_off_choices_never_use_the_follow_fallback(self):
        previous = self.qualify_cli('claude-code')
        self.failed_follow(previous)
        self.store.put('decision_route', previous)
        self.assertIsNone(self.routes._fallback_route())
        self.service_under_test.activate_decision_route({'transport': 'off'})
        self.assertIsInstance(self.routes.engine(), UnavailableDecisionEngine)
        self.assertIsNone(self.routes._fallback_route())

    def test_a_dispatch_guard_refusal_does_not_claim_the_fallback_answered(self):
        previous = self.qualify_cli('claude-code')
        self.failed_follow(previous)
        engine = self.routes.engine()
        engine.guard = lambda: 'requalification-needed'
        result = engine.choose(DecisionContext('fixture', {}), ('yes', 'no'), 'Fixture question')
        self.assertEqual(result.outcome, OUTCOME_UNAVAILABLE)
        self.assertNotIn('route_fallback', self.store.config('decision_audit')[-1])
