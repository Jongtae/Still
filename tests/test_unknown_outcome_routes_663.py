"""#663: a Work with an unknown-effect call ends ``unknown`` on the direct API route too.

Evidence class: model-free.  The direct route runs through ``AgentService.run_one``
with ``run_agent`` replaced by a fake that records one call and reports an outcome;
the CLI route runs with a fake execution adapter.  The Work-wide scan
(``_work_has_unknown_effect``) and the owner-facing bubble run unchanged.
"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import test_agency_loop as loop
from personal_agent.bounded_execution import ExecutionResult
from personal_agent.conversation_projection import TERMINAL_UNKNOWN_EFFECT
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore
from personal_agent.subscription_engines import SubscriptionEngines

UNKNOWN_CALL = {'host_action': 'api_request', 'code': 'effect_unknown', 'retry': 'never', 'effect': 'unknown'}
SETTLED_CALL = {'host_action': 'api_request', 'code': 'tool_failed', 'retry': 'never', 'effect': 'none'}
STATEMENT = 'The example update was sent, but no reply came back. Check the example board before trying again.'


def fake_run_agent(outcome, detail):
    """A ``run_agent`` stand-in: one failed call with ``detail``, then ``outcome``."""
    def run(adapter, config, key, messages, sections, capabilities, record, **_kwargs):
        record('api_request', 'failed', json.dumps(detail))
        return SimpleNamespace(outcome=outcome, content=STATEMENT, provider='ollama', model='test-model',
                               incomplete=[('api_request', 'no reply')] if outcome != 'succeeded' else [],
                               verified=[], report=None, alternatives=[], judgment=None)
    return run


class DirectRouteUnknownOutcomeTests(unittest.TestCase):
    service = loop.OwnerEntryPointTests.service

    def run_work(self, outcome, detail):
        service, store = self.service(loop.Script(), loop.Network())
        job = store.enqueue('update the example board', f'direct-{outcome}', channel='telegram:g', chat_id=77)
        with mock.patch('personal_agent.quickstart_service.run_agent', fake_run_agent(outcome, detail)):
            self.assertTrue(service.run_one())
        return service, store, store.job(job)

    @staticmethod
    def bubble(service, row):
        # The terminal bubble exactly as the Telegram surface renders it.
        return service.telegram_result_text(row['response'], row.get('owner_cause') or row['error'], row.get('status'),
                                            verified=row.get('owner_verified'), note=row.get('owner_note'))

    def test_an_unknown_effect_call_ends_unknown_whatever_run_agent_reported(self):
        for outcome in ('succeeded', 'partial', 'failed'):
            with self.subTest(outcome=outcome):
                service, store, row = self.run_work(outcome, UNKNOWN_CALL)
                self.assertEqual(row['status'], 'unknown')
                self.assertEqual((row['error'], row['owner_cause']), (STATEMENT, STATEMENT))
                # The bubble is the unknown statement alone: no closing line, no default text.
                self.assertEqual(self.bubble(service, row), STATEMENT)
                self.assertNotEqual(self.bubble(service, row), TERMINAL_UNKNOWN_EFFECT)
                # No retry, and no budget Continue, is offered for it.
                allowed, _reason = service.safe_retry(row)
                self.assertFalse(allowed)
                self.assertNotEqual(service.budget_continue_offer(row['id']).get('state'), 'pending')
                self.assertEqual(store.turn_provenance(row['id'])['goal']['effect'], 'unknown')
                # #691: the turn record agrees with the Work row.
                self.assertEqual(store.turn_provenance(row['id'])['status'], 'unknown')

    def test_a_work_without_one_keeps_the_outcome_run_agent_reported(self):
        for outcome in ('partial', 'failed'):
            with self.subTest(outcome=outcome):
                service, store, row = self.run_work(outcome, SETTLED_CALL)
                self.assertEqual(row['status'], outcome)
                self.assertEqual(store.turn_provenance(row['id'])['status'], outcome)
                self.assertNotEqual(self.bubble(service, row), STATEMENT)
                self.assertTrue(self.bubble(service, row).startswith(STATEMENT))


class CliRouteUnknownOutcomeTests(unittest.TestCase):
    """The CLI route keeps its behaviour: an unknown-effect call ends ``unknown``; without one it does not."""

    def run_work(self, detail):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = QuickStore(Path(tmp.name) / 'data')

        class Engine:
            def execute(self, engine, prompt, tools, **_kwargs):
                tools.capabilities.record('api_request', 'failed', json.dumps(detail))
                return ExecutionResult(STATEMENT, engine, 0)

        service = AgentService(store, subscription_engines=SubscriptionEngines(finder=lambda _: '/runtime/codex', clock=lambda: 1),
                               execution_adapter=Engine())
        service.connect_subscription_engine({'engine': 'codex', 'officially_authenticated': True})
        job = store.enqueue('update the example board', 'cli-unknown', channel='http')
        self.assertTrue(service.run_one())
        return store, store.job(job)

    def test_an_unknown_effect_call_ends_unknown(self):
        store, row = self.run_work(UNKNOWN_CALL)
        self.assertEqual((row['status'], row['error'], row['owner_cause']), ('unknown', STATEMENT, STATEMENT))
        # #691: the turn record agrees with the Work row.
        self.assertEqual(store.turn_provenance(row['id'])['status'], 'unknown')

    def test_a_work_without_one_is_not_unknown(self):
        store, row = self.run_work(SETTLED_CALL)
        self.assertNotEqual(row['status'], 'unknown')
        self.assertEqual(store.turn_provenance(row['id'])['status'], 'answered')


if __name__ == '__main__':
    unittest.main()
