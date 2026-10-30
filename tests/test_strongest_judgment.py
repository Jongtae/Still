"""#420: a judgment may ask for the route engine's strongest model, never a pinned name.

Evidence class: model-free unit tests.  The fake CLI runner of ``tests/test_decision_routes.py``
plays both official CLIs; ``DecisionRoutes.strongest_choice`` reads the recorded capability and
the installed CLI's own catalogue.
"""
import json
import tempfile
import types
import unittest

from personal_agent.decision import OUTCOME_DECIDED, DecisionContext
from personal_agent.decision_adapters import SubscriptionCliDecisionEngine
from personal_agent.decision_routes import CLAUDE_CODE_STRONGEST, STRONGEST_EFFORT, DecisionRoutes

from test_decision_routes import PLAN, CliRunner, cli_adapter, oracle


def flag(argv, name):
    return argv[argv.index(name) + 1] if name in argv else None


class Adapter(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = temp.name

    def engine(self, runner, strongest, engine_id='codex', **kwargs):
        self.runner, self.audit = runner, []
        if engine_id == 'codex':
            kwargs.setdefault('codex_disabled_features', PLAN)
        return SubscriptionCliDecisionEngine(cli_adapter(runner, self.root), engine_id, audit=self.audit.append,
                                             strongest=strongest, model='route-model', **kwargs)

    def calls(self):
        return [call['argv'] for call in self.runner.calls]

    def test_only_a_context_that_asks_gets_the_strongest_model_and_its_longer_bound(self):
        runner = CliRunner(judges={'top-model': oracle, 'route-model': oracle})
        engine = self.engine(runner, lambda: ('top-model', 'high'))
        decision = engine.choose(DecisionContext('owner-model-dream', {'a': 'b'}, strongest=True, timeout=240), ('x',), 'q')
        self.assertEqual(decision.outcome, OUTCOME_DECIDED)
        [argv] = self.calls()
        self.assertEqual(flag(argv, '--model'), 'top-model')
        self.assertIn('model_reasoning_effort="high"', argv)
        self.assertEqual(self.runner.calls[0]['timeout'], 240)
        self.assertEqual((self.audit[-1]['model'], self.audit[-1]['model_policy']), ('top-model', 'strongest'))
        engine.choose(DecisionContext('conversation-followup', {'a': 'b'}), ('x',), 'q')
        self.assertEqual(flag(self.calls()[-1], '--model'), 'route-model', 'every other judgment keeps the route model')
        self.assertEqual(self.runner.calls[-1]['timeout'], engine.timeout)

    def test_claude_code_gets_the_choice_through_its_own_flags(self):
        runner = CliRunner(judges={'top-model': oracle})
        engine = self.engine(runner, lambda: ('top-model', 'high'), engine_id='claude-code')
        engine.choose(DecisionContext('owner-model-dream', {'a': 'b'}, strongest=True), ('x',), 'q')
        [argv] = self.calls()
        self.assertEqual((flag(argv, '--model'), flag(argv, '--effort')), ('top-model', 'high'))

    def test_no_choice_or_an_unreadable_catalogue_leaves_the_route_model(self):
        def broken():
            raise RuntimeError('catalogue')
        for strongest in (None, lambda: None, broken, lambda: ('--bad model', None)):
            with self.subTest(strongest=strongest):
                engine = self.engine(CliRunner(judges={'route-model': oracle}), strongest)
                engine.choose(DecisionContext('owner-model-dream', {'a': 'b'}, strongest=True), ('x',), 'q')
                self.assertEqual(flag(self.calls()[-1], '--model'), 'route-model')

    def test_a_listed_model_the_account_refuses_falls_back_once_to_the_route_model(self):
        def fail(engine, argv):
            if flag(argv, '--model') == 'top-model':
                return types.SimpleNamespace(returncode=1, stdout=json.dumps(
                    {'type': 'error', 'message': json.dumps({'status': 400, 'error': {'message': 'model not supported'}})}),
                    stderr='')
            return None

        class Runner(CliRunner):
            def __call__(self, argv, **kwargs):
                refused = fail('codex', argv) if argv[1:2] == ['exec'] else None
                if refused is not None:
                    self.calls.append({'argv': list(argv), 'timeout': kwargs.get('timeout')})
                    return refused
                return super().__call__(argv, **kwargs)
        engine = self.engine(Runner(judges={'route-model': oracle}), lambda: ('top-model', 'high'))
        with self.assertLogs('personal_agent.decision', 'WARNING'):
            decision = engine.choose(DecisionContext('owner-model-dream', {'a': 'b'}, strongest=True), ('x',), 'q')
        self.assertEqual(decision.outcome, OUTCOME_DECIDED)
        self.assertEqual([flag(argv, '--model') for argv in self.calls()], ['top-model', 'route-model'])
        self.assertEqual(self.audit[-1]['model'], 'route-model', 'the audit names the model that answered')


class Choice(unittest.TestCase):
    """``strongest_choice``: the installed CLI's own top model, only where a model flag is accepted."""

    def routes(self, capabilities, models=None, efforts=None):
        routes = DecisionRoutes.__new__(DecisionRoutes)
        routes._capabilities = lambda: capabilities
        routes.bundled_models = lambda engine_id: models
        routes.bundled_efforts = lambda engine_id: efforts or {}
        return routes

    def test_codex_takes_the_first_model_its_catalogue_lists(self):
        routes = self.routes({'codex': {'model_override': True}}, ['newest-top', 'older'],
                             {'newest-top': ['low', 'medium', 'high', 'xhigh']})
        self.assertEqual(routes.strongest_choice('codex'), ('newest-top', STRONGEST_EFFORT))
        routes = self.routes({'codex': {'model_override': True}}, ['newest-top'], {'newest-top': ['low']})
        self.assertEqual(routes.strongest_choice('codex'), ('newest-top', None), "the model's own default effort")

    def test_claude_code_takes_its_top_alias(self):
        routes = self.routes({'claude-code': {'model_override': True}})
        self.assertEqual(routes.strongest_choice('claude-code'), (CLAUDE_CODE_STRONGEST, STRONGEST_EFFORT))

    def test_no_model_flag_no_catalogue_or_another_engine_gives_none(self):
        self.assertIsNone(self.routes({'codex': {'model_override': False}}, ['m']).strongest_choice('codex'))
        self.assertIsNone(self.routes({}, ['m']).strongest_choice('codex'))
        self.assertIsNone(self.routes({'codex': {'model_override': True}}, None).strongest_choice('codex'))
        self.assertIsNone(self.routes({'codex': {'model_override': True}}, []).strongest_choice('codex'))
        self.assertIsNone(self.routes({'jev': {'model_override': True}}).strongest_choice('jev'))


if __name__ == '__main__':
    unittest.main()
