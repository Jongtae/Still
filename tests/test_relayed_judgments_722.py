"""#722: model-free thread-boundary, owner-state judgment and relay wiring tests.

The audited fixture uses the production audit serializer and service sink;
it never supplies a Work id. Only the service's executing thread can link it.
Socket tests intentionally require a host that permits Unix socket binding.
"""
import threading
import tempfile
import unittest
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace
from unittest.mock import patch

from personal_agent import preparations as prep
from personal_agent.agent_runtime import WorkBudget, concurrent_read, HOST_RELAYED_ACTIONS
from personal_agent.cli_browser_relay import BrowserRelay, RelayClient
from personal_agent.decision import (BinaryDecision, FixtureDecisionEngine, OUTCOME_DECIDED,
                                     RoutedDecisionEngine, audit_record, fixture_confidence)
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore
from test_orchestrator import Harness, plan


def on_thread(call):
    """Propagate failures to unittest instead of losing assertions on a worker."""
    results, errors = [], []

    def run():
        try:
            results.append(call())
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(10)
    if thread.is_alive():
        raise AssertionError('#722 worker did not finish')
    if errors:
        raise errors[0]
    return results[0]


class LocalScope:
    def __init__(self):
        self.local = threading.local()
        self.budget = object()
        self.exits = []

    @contextmanager
    def scope(self):
        previous = self.values()
        self.local.work, self.local.budget = 'work-722', self.budget
        try:
            yield
        finally:
            self.local.work, self.local.budget = previous
            self.exits.append((threading.get_ident(), self.values()))

    def values(self):
        return getattr(self.local, 'work', None), getattr(self.local, 'budget', None)


class RelayScopeTests(unittest.TestCase):
    def exercise(self, action, *, full_surface=False, scoped=True, fail=False):
        state, seen = LocalScope(), []

        class Tools:
            capabilities = SimpleNamespace(tools={action: {'host_action': action}})

            def call(self, name, arguments):
                seen.append((threading.get_ident(), state.values()))
                if fail:
                    raise ValueError('fixture refusal')
                return {'called': name}

            handle_mcp = call
            connector_permission = lambda self, arguments: self.call('connector_permission', arguments)

        relay = BrowserRelay(Tools(), full_surface=full_surface, scope=state.scope if scoped else None)
        self.addCleanup(relay.close)
        callers = []

        def request():
            callers.append(threading.get_ident())
            return RelayClient(relay.address).call(action, {})

        if fail:
            with self.assertRaisesRegex(ValueError, 'fixture refusal'):
                on_thread(request)
        else:
            self.assertEqual(on_thread(request), {'called': action})
        self.assertEqual(len(seen), 1)
        handler, values = seen[0]
        self.assertNotEqual(handler, callers[0])
        self.assertEqual(values, ('work-722', state.budget) if scoped else (None, None))
        self.assertEqual(state.values(), (None, None))
        self.assertEqual(state.exits, [(handler, (None, None))] if scoped else [])

    def test_locked_owner_state_call(self):
        """#722: locked owner-state calls inherit and exit the handler scope."""
        self.assertIn('settings_change', HOST_RELAYED_ACTIONS)
        self.assertFalse(concurrent_read('settings_change', {}))
        self.exercise('settings_change')

    def test_concurrent_effect_free_read(self):
        """#722: the unlocked concurrent-read path inherits the handler scope."""
        self.assertIn('settings_read', HOST_RELAYED_ACTIONS)
        self.assertTrue(concurrent_read('settings_read', {}))
        self.exercise('settings_read')

    def test_full_surface(self):
        """#722: full-surface handle_mcp calls inherit the handler scope."""
        self.exercise('tools/list', full_surface=True)

    def test_connector_permission(self):
        """#722: connector permission judgments inherit the handler scope."""
        self.exercise('connector_permission')

    def test_scope_exits_after_tool_failure(self):
        """#722: a tool exception still exits the handler scope."""
        self.exercise('settings_change', fail=True)

    def test_scope_that_cannot_open_runs_nothing(self):
        """#722: a scope that fails to open answers a typed not-run error; no call runs outside the Work."""
        from personal_agent.agent_runtime import ToolError
        ran = []

        class Tools:
            capabilities = SimpleNamespace(tools={'settings_change': {'host_action': 'settings_change'}})

            def call(self, name, arguments):
                ran.append(name)

        def broken():
            raise RuntimeError('scope unavailable')

        relay = BrowserRelay(Tools(), scope=broken)
        self.addCleanup(relay.close)
        with self.assertRaises(ToolError) as caught:
            on_thread(lambda: RelayClient(relay.address).call('settings_change', {}))
        self.assertEqual(caught.exception.code, 'browser_relay_unavailable')
        self.assertIs(getattr(caught.exception, 'ran', None), False)
        self.assertEqual(ran, [])

    def test_none_scope_preserves_legacy_calls(self):
        """#722: scope=None still serves a call without binding owner state."""
        self.exercise('settings_change', scoped=False)


class AuditedFixture(FixtureDecisionEngine):
    """Fixture provider plus the same serializer/sink used by real adapters."""
    def __init__(self, service):
        super().__init__(judge=lambda context, proposition:
                         BinaryDecision(OUTCOME_DECIDED, True, fixture_confidence()))
        self.service, self.contexts, self.threads = service, [], []

    def judge(self, context, proposition):
        self.contexts.append(context)
        self.threads.append(threading.get_ident())
        decision = super().judge(context, proposition)
        self.service.record_decision(audit_record(context, 'judge', decision.outcome,
                                                {'answer': decision.answer}, decision.confidence, 1000))
        return decision


class ServiceScopeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = QuickStore(tmp.name)
        self.service = AgentService(self.store)
        self.now = 1_800_000_000.0
        self.service.preparations.clock = lambda: self.now
        self.service.context_observations.clock = lambda: self.now
        self.fixture = AuditedFixture(self.service)
        # use_decision_engine alone replaces the router too: retain the real
        # budget admission/cancellation path while replacing only its provider.
        self.service.use_decision_engine(RoutedDecisionEngine(
            lambda: self.fixture, budget=self.service.current_decision_budget))

    def work(self, key):
        ident = self.store.enqueue('Prepare a reading summary tomorrow', key, owner_typed=True)
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (ident,))
        return self.store.job(ident), self.service.work_budget(ident)

    def schedule(self, job):
        return self.service.preparation_scheduler(job, job['message'])({
            'offer': '내일 읽을거리 요약을 준비해요.', 'kind': 'prepare', 'goal': 'Read material and prepare a summary',
            'due': '2027-01-16T12:00:00+00:00', 'timezone': 'UTC', 'delivery': 'when_needed'})

    def scoped(self, job, budget, call, *, threaded=True, scope=True):
        def run():
            with self.service.work_decision_scope(job['id'], budget) if scope else nullcontext():
                result = call()
            self.assertIsNone(self.service.current_work_id)
            self.assertIsNone(self.service.current_decision_budget())
            return result
        return on_thread(run) if threaded else run()

    def assert_judgment(self, job, budget, before=0):
        rows = self.store.work_decisions(job['id'])
        self.assertEqual([row['purpose'] for row in rows], ['explicit-preparation-request'])
        self.assertEqual([row['work_id'] for row in rows], [job['id']])
        self.assertEqual(budget.turns_used - before, 1)
        self.assertEqual(budget.ledger.used('turns'), budget.turns_used)
        return [row['purpose'] for row in rows]

    def test_separate_thread_records_and_spends(self):
        """#722: a service owner-state judgment records and spends on its executing thread."""
        job, budget = self.work('threaded')
        self.scoped(job, budget, lambda: self.schedule(job))
        self.assert_judgment(job, budget)
        self.assertEqual(len(self.fixture.asked), 1)
        self.assertNotEqual(self.fixture.threads[0], threading.get_ident())

    def socket_schedule(self, job, budget, *, scope=True):
        service = self.service

        class Tools:
            capabilities = SimpleNamespace(tools={'schedule_preparation': {'host_action': 'schedule_preparation'}})

            def call(self, name, arguments):
                return service.preparation_scheduler(job, job['message'])(arguments)

        relay = BrowserRelay(Tools(), scope=(lambda: service.work_decision_scope(job['id'], budget))
                             if scope else None)
        self.addCleanup(relay.close)
        on_thread(lambda: RelayClient(relay.address).call('schedule_preparation', {
            'offer': '내일 읽을거리 요약을 준비해요.', 'kind': 'prepare', 'goal': 'Read material and prepare a summary',
            'due': '2027-01-16T12:00:00+00:00', 'timezone': 'UTC', 'delivery': 'when_needed'}))

    def test_real_relay_records_and_spends(self):
        """#722: a real socket relay binds the Work before a service judgment."""
        job, budget = self.work('socket')
        self.socket_schedule(job, budget)
        self.assert_judgment(job, budget)
        self.assertNotEqual(self.fixture.threads[0], threading.get_ident())

    def test_real_relay_none_scope_documents_pre_fix_behavior(self):
        """#722: scope=None documents the real relay's unchanged pre-fix behavior."""
        job, budget = self.work('socket-unscoped')
        # Negative control: binding the caller alone never binds the handler.
        with self.service.work_decision_scope(job['id'], budget):
            self.socket_schedule(job, budget, scope=False)
        self.assertEqual(len(self.fixture.asked), 1)
        self.assertEqual(self.store.work_decisions(job['id']), [])
        self.assertEqual(budget.turns_used, 0)
        [record] = self.store.config('decision_audit', [])
        self.assertEqual(record['purpose'], 'explicit-preparation-request')
        self.assertFalse(record.get('work_id'))

    def test_direct_route_parity(self):
        """#722: direct and separate-thread owner-state calls record and spend equally."""
        direct, direct_budget = self.work('direct')
        self.scoped(direct, direct_budget, lambda: self.schedule(direct), threaded=False)
        relayed, relayed_budget = self.work('relayed')
        self.scoped(relayed, relayed_budget, lambda: self.schedule(relayed))
        self.assertEqual(self.assert_judgment(direct, direct_budget),
                         self.assert_judgment(relayed, relayed_budget))
        self.assertEqual(direct_budget.turns_used, relayed_budget.turns_used)
        self.assertEqual(self.fixture.threads[0], threading.get_ident())
        self.assertNotEqual(self.fixture.threads[1], threading.get_ident())

    def test_unscoped_thread_documents_pre_fix_behavior(self):
        """#722: negative control documents the unscoped thread's missing Work link."""
        job, budget = self.work('unscoped')
        # Pre-fix behavior, not a desired invariant of scoped relays. None
        # deliberately leaves the handler unbound, even when the caller is bound.
        with self.service.work_decision_scope(job['id'], budget):
            self.scoped(job, budget, lambda: self.schedule(job), scope=False)
        self.assertEqual(len(self.fixture.asked), 1)
        self.assertEqual(self.store.work_decisions(job['id']), [])
        self.assertEqual(budget.turns_used, 0)
        [record] = self.store.config('decision_audit', [])
        self.assertEqual(record['purpose'], 'explicit-preparation-request')
        self.assertFalse(record.get('work_id'))

    def settings_cancellation(self, reason=None, *, relayed=False):
        job, budget = self.work((reason or 'settings-positive') + ('-relayed' if relayed else ''))
        preparation = self.service.preparations.create(
            kind='prepare', goal='Read material', due_at=self.now + 3600,
            timezone='UTC', recurrence='daily', channel='web', created_from=None,
            state=prep.STATE_SCHEDULED)
        if reason == 'stopped':
            with self.store.db() as db:
                db.execute("UPDATE jobs SET status='cancelled' WHERE id=?", (job['id'],))
            self.assertTrue(self.service.work_stopped(job['id']))
        elif reason == 'deadline_exceeded':
            # Keep the real Work's shared ledger and Stop callback; make time
            # deterministic and expire exactly at the budget's deadline.
            clock = [100.0]
            budget.clock = lambda: clock[0]
            budget.deadline = 110.0
            clock[0] = 110.0
        before = budget.turns_used
        arguments = {'category': 'preparations', 'setting': 'cancel', 'value': preparation['id']}
        if relayed:
            # The real socket relay, its handler thread and the scope the service hands it.
            service = self.service

            class Tools:
                capabilities = SimpleNamespace(tools={'settings_change': {'host_action': 'settings_change'}})

                def call(self, name, arguments):
                    return service.settings_tools(job)(name, arguments)

            relay = BrowserRelay(Tools(), scope=lambda: service.work_decision_scope(job['id'], budget))
            self.addCleanup(relay.close)
            result = on_thread(lambda: RelayClient(relay.address).call('settings_change', arguments))
        else:
            result = self.scoped(job, budget, lambda: self.service.settings_tools(job)('settings_change', arguments))
        self.assertEqual(len(self.fixture.contexts), 1, 'reach the judgment, not an unrelated refusal')
        self.assertEqual(self.fixture.contexts[0].purpose, 'explicit-settings-request')
        if reason is None:
            self.assertEqual(len(self.fixture.asked), 1)
            self.assertEqual(budget.turns_used - before, 1)
            self.assertTrue(result.get('applied'))
            self.assertEqual(self.service.preparations.get(preparation['id'])['state'], prep.STATE_CANCELLED)
            return
        self.assertEqual(self.fixture.contexts[0].refused, reason)
        self.assertEqual(self.fixture.asked, [], 'no fixture provider dispatch after interruption')
        self.assertEqual(budget.turns_used, before)
        self.assertFalse(result.get('applied'))
        self.assertEqual(self.service.preparations.get(preparation['id'])['state'], prep.STATE_SCHEDULED)

    def test_uninterrupted_settings_change_applies(self):
        """#722: the settings change used for refusal tests applies when admitted."""
        self.settings_cancellation()

    def test_stop_refuses_before_provider_and_does_not_apply(self):
        """#722: a stopped Work refuses a threaded settings judgment before dispatch."""
        self.settings_cancellation('stopped')

    def test_deadline_refuses_before_provider_and_does_not_apply(self):
        """#722: a passed deadline refuses a threaded judgment with deadline_exceeded."""
        self.settings_cancellation('deadline_exceeded')


    def test_relayed_settings_change_applies_and_spends(self):
        """#722: through the real relay, the admitted settings judgment spends the Work budget."""
        self.settings_cancellation(relayed=True)

    def test_relayed_stop_refuses_before_provider(self):
        """#722: through the real relay, the Work's Stop refuses the judgment before dispatch."""
        self.settings_cancellation('stopped', relayed=True)

    def test_relayed_deadline_refuses_before_provider(self):
        """#722: through the real relay, a passed deadline refuses the judgment (deadline_exceeded)."""
        self.settings_cancellation('deadline_exceeded', relayed=True)


class SubscriptionWiringTests(Harness):
    def test_subscription_relay_receives_work_scope(self):
        """#722: work_executor gives the subscription relay its exact run budget and Work."""
        self.script([plan('codex', 'Read material and summarize it')], goals=[True])
        recorded = []

        class Recorder:
            address = '/fixture/relay-722'

            def __init__(relay, tools, **kwargs):
                recorded.append((tools, kwargs))

            def close(relay):
                pass

        with patch('personal_agent.work_executor.BrowserRelay', Recorder):
            job_id, _ = self.run_work('Read material and summarize it')
        self.assertEqual(len(recorded), 1, 'drive the subscription cli_browser path')
        tools, kwargs = recorded[0]
        self.assertIn('scope', kwargs)
        self.assertTrue(callable(kwargs['scope']))

        def inspect():
            self.assertIsNone(self.service.current_work_id)
            self.assertIsNone(self.service.current_decision_budget())
            with kwargs['scope']():
                self.assertEqual(self.service.current_work_id, job_id)
                self.assertIs(self.service.current_decision_budget(), tools.capabilities.budget)
            self.assertIsNone(self.service.current_work_id)
            self.assertIsNone(self.service.current_decision_budget())
        on_thread(inspect)


if __name__ == '__main__':
    unittest.main()
