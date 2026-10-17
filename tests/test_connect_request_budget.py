"""CONNECT-01 request bounds across host-recorded continuations; no live wires."""
import unittest
from unittest import mock

from personal_agent.agent_runtime import WorkBudget, WorkLedger
from personal_agent.quickstart_service import AgentService
import test_ask_location as location_cases
from test_agency_recovery import _store


class RequestBudget(unittest.TestCase):
    def setUp(self):
        self.store, _ = _store(self)
        self.now = 1000.0
        self.parent = self.store.enqueue('Complete the original request', 'original')
        self.set_status(self.parent, 'running')

    def set_status(self, work, status):
        with self.store.db() as db:
            db.execute('UPDATE jobs SET status=? WHERE id=?', (status, work))

    def ledger(self, work, fresh=True):
        return WorkLedger(self.store, work, wall=lambda: self.now, fresh=fresh)

    def retry(self, parent=None):
        parent = parent or self.parent
        child = self.store.enqueue('Try that again', 'retry-' + parent)
        AgentService(self.store).record_continuity(child, parent, 'retry', executed=True)
        return child

    def test_retry_inherits_counters_time_and_stale_parent_cannot_spend_or_pause(self):
        parent = self.ledger(self.parent)
        parent.spend(40); parent.spend(9, 'turns'); parent.spend(2, 'nudges')
        self.now += 50
        parent.pause()
        self.set_status(self.parent, 'failed')
        child = self.retry()
        self.now += 3600
        resumed = self.ledger(child)
        self.assertEqual([resumed.used(k) for k in ('attempts', 'turns', 'nudges')], [1, 1, 1])
        self.assertEqual(resumed.remaining(), 550)
        bridge = self.ledger(child, fresh=False)
        self.assertTrue(bridge.spend(40))
        self.assertEqual(resumed.used(), 2)
        self.assertFalse(parent.spend(40))
        parent.pause()
        self.assertEqual(resumed.remaining(), 550)
        self.assertFalse(self.ledger(self.parent, fresh=False).spend(40), 'a new old-parent bridge cannot join the child')
        self.assertTrue(self.ledger(self.parent).expired(), 'a late parent host cannot take back the request')

    def test_separate_reference_and_correction_keep_their_own_budget(self):
        parent = self.ledger(self.parent)
        parent.spend(40); parent.pause()
        self.set_status(self.parent, 'succeeded')
        for relation in ('reference', 'correction'):
            child = self.store.enqueue('A separate request', relation)
            AgentService(self.store).record_continuity(child, self.parent, relation)
            self.assertEqual(self.ledger(child).used(), 0)

    def test_legacy_branch_counters_are_combined_once_and_time_cannot_refill(self):
        self.set_status(self.parent, 'failed')
        child = self.retry()
        sibling = self.store.enqueue('Earlier retry', 'sibling')
        AgentService(self.store).record_continuity(sibling, self.parent, 'retry', executed=True)
        self.set_status(sibling, 'failed')
        for work, count, remaining in ((self.parent, 2, 300), (child, 3, 200), (sibling, 4, 150)):
            self.store.put('work_budget:' + work, {'attempts': count, 'turns': count, 'nudges': 0,
                'deadline': 900, 'active': False, 'remaining_seconds': remaining, 'epoch': 0})
        resumed = self.ledger(child)
        self.assertEqual(resumed.used(), 9)
        self.assertEqual(resumed.remaining(), 150)
        resumed.pause()
        self.now += 3600
        again = self.ledger(child)
        self.assertEqual((again.used(), again.remaining()), (9, 150))
        self.assertFalse(again.spend(9))

    def test_missing_or_cyclic_parent_fails_closed(self):
        child = self.retry()
        with self.store.db() as db:
            db.execute("UPDATE jobs SET related_job_id=?,relation_kind='retry' WHERE id=?", (child, self.parent))
        self.assertTrue(self.ledger(child).expired())
        self.assertFalse(self.ledger(child).spend(40))
        with self.store.db() as db:
            db.execute('DELETE FROM jobs WHERE id=?', (self.parent,))
        self.assertTrue(self.ledger(child).expired())

    def test_invalid_queued_request_is_refused_before_any_model_judgment(self):
        child = self.retry()
        with self.store.db() as db:
            db.execute('DELETE FROM jobs WHERE id=?', (self.parent,))
        service = AgentService(self.store)
        with mock.patch.object(service, 'start_early_judgment', side_effect=AssertionError('invalid request binding')):
            self.assertTrue(service.run_one())
        self.assertEqual(self.store.job(child)['status'], 'failed')
        self.assertFalse(self.ledger(child).spend(40))

    def test_owner_channel_mismatch_cannot_share_budget(self):
        self.ledger(self.parent).pause()
        child = self.retry()
        with self.store.db() as db:
            db.execute("UPDATE jobs SET channel='telegram:other',chat_id=88 WHERE id=?", (child,))
        self.assertTrue(self.ledger(child).expired())

    def test_queued_child_waits_for_parent_before_model_or_budget_handoff(self):
        self.ledger(self.parent)
        child = self.retry()
        service = AgentService(self.store)
        with mock.patch.object(service, 'start_early_judgment', side_effect=AssertionError('parent is still running')):
            self.assertFalse(service.run_one())
        self.assertEqual(self.store.job(child)['status'], 'queued')
        self.assertTrue(self.ledger(child).expired())

    def test_corrupt_legacy_child_cannot_partially_migrate_or_refill(self):
        self.set_status(self.parent, 'failed')
        child = self.retry()
        old = {'attempts': 4, 'deadline': 1200}
        bad = {'attempts': -1, 'deadline': 1200}
        self.store.put('work_budget:' + self.parent, old)
        self.store.put('work_budget:' + child, bad)
        self.assertTrue(self.ledger(child).expired())
        self.assertEqual(self.store.config('work_budget:' + self.parent), old)
        self.assertEqual(self.store.config('work_budget:' + child), bad)

    def test_legacy_migration_cannot_take_a_budget_from_another_running_work(self):
        self.set_status(self.parent, 'failed')
        child = self.retry()
        self.set_status(child, 'running')
        old = {'attempts': 2, 'deadline': 1200}
        for work in (self.parent, child):self.store.put('work_budget:' + work, old)
        self.assertTrue(self.ledger(self.parent).expired())
        self.assertEqual(self.store.config('work_budget:' + self.parent), old)
        self.assertEqual(self.store.config('work_budget:' + child), old)

    def test_real_bridge_process_spends_original_requests_remaining_attempts(self):
        from test_agency_loop import BridgeCli
        from personal_agent.agent_runtime import WORK_TOOL_ATTEMPTS
        from personal_agent.subscription_engines import SubscriptionEngines
        original = WorkLedger(self.store, self.parent, fresh=True)
        for _ in range(WORK_TOOL_ATTEMPTS - 2):self.assertTrue(original.spend(WORK_TOOL_ATTEMPTS))
        original.pause()
        self.set_status(self.parent, 'failed')
        child = self.retry()
        cli = BridgeCli([('list_notes', {}), ('list_notes', {}), ('list_notes', {})], '메모를 확인했습니다.')
        service = AgentService(self.store,
            subscription_engines=SubscriptionEngines(finder=lambda _: '/runtime/codex', clock=lambda: 1),
            execution_adapter=cli)
        service.connect_subscription_engine({'engine': 'codex', 'officially_authenticated': True})
        self.assertTrue(service.run_one())
        self.assertNotIn('isError', cli.replies[0]['result'])
        self.assertNotIn('isError', cli.replies[1]['result'])
        self.assertEqual(cli.replies[2]['result']['structuredContent']['code'], 'attempt_budget')
        self.assertEqual(self.store.config('work_budget:' + self.parent)['attempts'], WORK_TOOL_ATTEMPTS)
        self.assertEqual(self.store.job(child)['status'], 'partial')


class ActualLocationBudget(unittest.TestCase):
    def test_actual_location_callback_and_worker_preserve_original_counts(self):
        case = location_cases._LocationCase(methodName='runTest')
        case.setUp()
        self.addCleanup(case.doCleanups)
        parent = case.ask()
        before = case.store.config('work_budget:' + parent)
        case.now += 30
        case.location()
        [child] = case.continued()
        resumed = case.service.work_budget(child['id'])
        self.assertEqual(resumed.turns_used, before['turns'])
        self.assertEqual(resumed.attempts_used, before['attempts'])
        resumed.ledger.pause()
        answer = '현재 위치를 확인해 요청한 결과를 준비했습니다.'
        case.script = [{'content': answer}] * 2
        self.assertTrue(case.service.run_one())
        self.assertEqual(case.store.job(child['id'])['status'], 'succeeded')
        self.assertIn(answer, case.store.job(child['id'])['response'])
        after = case.store.config('work_budget:' + parent)
        self.assertGreater(after['turns'], before['turns'])
        self.assertLessEqual(after['remaining_seconds'], before['remaining_seconds'])

    def test_unanswered_input_fails_only_its_run_and_preserves_accepted_watch(self):
        from personal_agent import preparations as prep
        from personal_agent.context_observations import LOCATION_REQUEST_TTL_SECONDS
        from test_preparations import call, finish
        for end in ('expired', 'cancelled'):
            with self.subTest(end=end):
                case = location_cases._LocationCase(methodName='runTest')
                case.setUp()
                self.addCleanup(case.doCleanups)
                row = case.service.preparations.create(kind='prepare', goal='Prepare the requested result',
                    due_at=case.now, timezone='UTC', recurrence=None, channel='telegram', created_from='settings',
                    state=prep.STATE_SCHEDULED, accepted_by=prep.ACCEPTED_OWNER_SETTINGS,
                    window=(600, case.now + LOCATION_REQUEST_TTL_SECONDS + 3600, 2))
                case.tick()
                [work] = case.runs(row['id'])
                case.script = [{'tool_calls': [call('loc', 'ask_location', reason='현재 위치가 필요합니다.')]},
                               finish('f', 'loc', summary='현재 위치를 요청했어요.')]
                case.service.run_one(); case.service.deliver_one(); case.tick()
                if end == 'expired':
                    case.now += LOCATION_REQUEST_TTL_SECONDS + 1
                    with case.store.db() as db:case.service.context_observations.prune(db, case.now)
                else:
                    case.service.context_observations.cancel_work_requests(work['id'])
                case.tick()
                settled = case.service.preparations.get(row['id'])
                self.assertEqual((settled['state'], settled['last_outcome'], settled['run_count']), ('scheduled', 'failed', 1))
                self.assertIsNone(settled['prepared_result_ref'])
                self.assertEqual(settled['window_end'], row['window_end'])
                case.now = settled['due_at']
                case.tick()
                self.assertEqual(case.service.preparations.get(row['id'])['run_count'], 2)
