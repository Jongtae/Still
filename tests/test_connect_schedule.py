"""CONNECT-01: the existing accepted slot survives an actual owner input wait.

Only model/provider wires are fixtures; HTTP, encrypted input, queue, worker
and preparation settlement are real. No live scheduler or account is used.
"""
import unittest
from unittest import mock

from personal_agent import preparations as prep
import test_connect_api_input as api_input_cases

PUBLIC = api_input_cases.PUBLIC


class ScheduledConnection(unittest.TestCase):
    def setUp(self):
        self.case = api_input_cases.ApiInput(methodName='runTest')
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.service, self.store = self.case.service, self.case.store
        self.now = self.service.preparations.clock()
        self.service.preparations.clock = lambda: self.now
        with self.store.db() as db:
            db.execute('DELETE FROM jobs WHERE id=?', (self.case.job['id'],))
        self.row = self.service.preparations.create(
            kind='prepare', goal='Read my requested records and report the cash total',
            due_at=self.now, timezone='UTC', recurrence=None, channel='web', created_from='settings',
            state=prep.STATE_SCHEDULED, accepted_by=prep.ACCEPTED_OWNER_SETTINGS,
            window=(600, self.now + 3600, 1))
        self.work = self.service.preparations.start(self.row, channel='web', chat_id=None, now=self.now)
        self.case.job = self.store.job(self.work)

    def current(self):
        return self.service.preparations.get(self.row['id'])

    def test_input_resumes_original_slot_and_settles_original_result(self):
        def waiting():
            self.now += 120
            self.service.run_due_preparation()
            row = self.current()
            self.assertEqual((row['state'], row['run_count'], row['last_run_job_id']), ('running', 1, self.work))
            self.assertIsNone(row['prepared_result_ref'])
        self.case.run_original(PUBLIC, {'secret': self.case.key}, after_wait=waiting)
        self.service.run_due_preparation()
        row = self.current()
        self.assertEqual((row['state'], row['run_count'], row['prepared_result_ref']), ('delivered', 1, self.work))
        self.assertIn('1,250,340', row['prepared_text'])
        self.assertEqual(row['window_end'], self.row['window_end'])

    def test_window_expiry_rejects_actual_input_before_provider_read(self):
        _, session, cookie = self.case.open_input()
        self.now = self.row['window_end']
        status, _, _ = self.case.submit(session, cookie)
        self.assertIn(status, (400, 404))
        self.assertEqual(self.case.api.calls, [])
        self.service.run_due_preparation()
        self.assertEqual(self.current()['state'], 'expired')
        self.assertNotIn(self.store.job(self.work)['status'], ('queued', 'running', 'awaiting_connection'))
        self.assertIsNone(self.current()['prepared_result_ref'])

    def test_expired_wait_settles_without_model_or_owner_callback(self):
        self.case.open_input()
        self.now = self.row['window_end']
        with mock.patch.object(self.service, 'watch_judgment', side_effect=AssertionError('no judgment for a wait')):
            self.service.run_due_preparation()
        self.assertEqual(self.current()['state'], 'expired')
        self.assertEqual(self.current()['run_count'], 1)
        self.assertEqual(self.case.api.calls, [])
        self.assertIsNone(self.current()['prepared_result_ref'])

    def test_cancelled_or_deleted_schedule_cannot_accept_late_input(self):
        _, session, cookie = self.case.open_input()
        self.service.preparations.cancel(self.row['id'])
        self.service.preparations.delete(self.row['id'])
        status, _, _ = self.case.submit(session, cookie)
        self.assertIn(status, (400, 404))
        self.assertEqual(self.case.api.calls, [])
        self.assertNotEqual(self.store.job(self.work)['status'], 'queued')

    def test_queued_continuation_rechecks_deadline_before_worker(self):
        _, session, cookie = self.case.open_input()
        self.assertEqual(self.case.submit(session, cookie)[0], 200)
        self.now = self.row['window_end']
        with mock.patch.object(self.service, 'start_early_judgment', side_effect=AssertionError('no model after deadline')):
            self.assertTrue(self.service.run_one())
        self.assertNotIn(self.store.job(self.work)['status'], ('queued', 'running', 'succeeded'))
        self.service.run_due_preparation()
        self.assertEqual(self.current()['state'], 'expired')

    def test_pending_action_does_not_settle_partial_answer(self):
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='partial',response='Please approve this action.' WHERE id=?", (self.work,))
        with mock.patch.object(self.service, '_browser_request', return_value={'state': 'requested'}):
            self.service.run_due_preparation()
        self.assertEqual(self.current()['state'], 'running')
        self.assertIsNone(self.current()['prepared_result_ref'])

    def test_settlement_cannot_attach_result_from_a_superseded_work(self):
        stale = self.current()
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='succeeded',response='Old answer' WHERE id=?", (self.work,))
            other = self.store.enqueue('Original goal', 'other', db=db)
            db.execute("UPDATE jobs SET status='succeeded',response='Current answer' WHERE id=?", (other,))
            prep.Preparations.continue_run(db, self.work, other, self.now)
        self.assertIsNone(self.service.preparations.settle(stale, self.now))
        self.service.preparations.settle(self.current(), self.now)
        self.assertEqual(self.current()['prepared_text'], 'Current answer')

    def test_settlement_rechecks_result_changed_while_scrubbing(self):
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='partial',response='Old partial result' WHERE id=?", (self.work,))
        def scrub(job):
            with self.store.db() as db:
                db.execute("UPDATE jobs SET status='succeeded',response='Completed result' WHERE id=?", (self.work,))
            return job['response']
        self.assertIsNone(self.service.preparations.settle(self.current(), self.now, scrub=scrub))
        self.service.run_due_preparation()
        self.assertEqual(self.current()['prepared_text'], 'Completed result')

    def test_finished_result_is_kept_when_tick_arrives_after_window(self):
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='succeeded',response='Observed result' WHERE id=?", (self.work,))
        self.now = self.row['window_end']
        self.service.run_due_preparation()
        self.assertEqual(self.current()['state'], 'delivered')
        self.assertEqual(self.current()['prepared_text'], 'Observed result')

    def test_queue_admission_refuses_cancelled_slot_without_starting_model(self):
        self.service.preparations.cancel(self.row['id'])
        with mock.patch.object(self.service, 'start_early_judgment', side_effect=AssertionError('cancelled slot')):
            self.assertTrue(self.service.run_one())
        self.assertEqual(self.current()['state'], 'cancelled')
        self.assertEqual(self.store.job(self.work)['status'], 'failed')

    def test_queue_admission_refuses_run_count_above_accepted_bound(self):
        with self.store.db() as db:
            db.execute('UPDATE preparations SET run_count=2 WHERE id=?', (self.row['id'],))
        with mock.patch.object(self.service, 'start_early_judgment', side_effect=AssertionError('run limit')):
            self.assertTrue(self.service.run_one())
        self.assertEqual(self.current()['state'], 'expired')
        self.assertEqual(self.current()['run_count'], 2, 'admission never resets the count')

    def offer_action(self):
        from personal_agent import owner_mcp, trust_record
        from test_connect_effects import ACTION, INPUT
        self.service.set_ai_connections({'enabled': True})
        owner_mcp.set_confirmed(self.store, 'codex', ['synthetic'])
        trust_record.set_money(self.store, trust_record.connection('codex', 'owner-mcp', 'synthetic'), True)
        result = self.service.connector_permission(self.case.job, {'engine': 'codex', 'tool_name': ACTION, 'input': INPUT})
        self.assertEqual(result['behavior'], 'deny')
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='partial',response='Approve the remaining action' WHERE id=?", (self.work,))
        return self.service._browser_request(self.work)

    def test_exact_action_approval_resumes_same_slot(self):
        offer = self.offer_action()
        self.service.run_due_preparation()
        self.assertEqual(self.current()['state'], 'running')
        result = self.service.browser_step_decision({'work_id': self.work, 'decision': 'approve', 'digest': offer['offer_digest']})
        self.assertTrue(result['resumed'])
        self.assertEqual(self.store.job(self.work)['status'], 'queued')
        self.assertEqual((self.current()['run_count'], self.current()['last_run_job_id']), (1, self.work))

    def test_late_action_approval_cannot_outlive_accepted_window(self):
        offer = self.offer_action()
        self.now = self.row['window_end']
        result = self.service.browser_step_decision({'work_id': self.work, 'decision': 'approve', 'digest': offer['offer_digest']})
        self.assertFalse(result['approved'])
        self.assertFalse(result['resumed'])
        self.service.run_due_preparation()
        self.assertEqual(self.current()['state'], 'expired')
        self.assertIsNone(self.current()['prepared_result_ref'])

    def test_queue_transaction_rechecks_window_even_after_initial_approval_check(self):
        offer = self.offer_action()
        self.now = self.row['window_end']
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self.assertFalse(self.service._queue_action_continuation(db, self.case.job, offer))
        self.assertEqual(self.store.job(self.work)['status'], 'failed')
        self.assertEqual(self.current()['state'], 'expired')

    def test_resume_and_bridge_share_the_smaller_remaining_watch_window(self):
        from personal_agent.agent_runtime import WorkLedger
        self.now = self.row['window_end'] - 150
        budget = self.service.work_budget(self.work)
        budget.spend_turn()
        budget.ledger.pause()
        self.now += 100
        resumed = self.service.work_budget(self.work)
        self.assertEqual(resumed.turns_used, 1)
        self.assertGreater(resumed.remaining(), 45)
        self.assertLessEqual(resumed.remaining(), 50)
        bridge = WorkLedger(self.store, self.work)
        self.assertLessEqual(bridge.remaining(), 50)
        self.assertEqual(bridge.used('turns'), 1)

    def test_cancellation_leaves_active_run_alone_but_prevents_its_next_resume(self):
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (self.work,))
        self.service.preparations.cancel(self.row['id'])
        self.assertFalse(self.service.work_stopped(self.work))
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='partial' WHERE id=?", (self.work,))
        self.assertTrue(self.service.work_stopped(self.work))


class ScheduledLocation(unittest.TestCase):
    def test_late_location_expires_run_before_consumed_wait_can_settle(self):
        import test_ask_location as location_cases
        from test_preparations import call, finish
        case = location_cases._LocationCase(methodName='runTest')
        case.setUp()
        self.addCleanup(case.doCleanups)
        row = case.service.preparations.create(
            kind='prepare', goal='Prepare the requested result using my current position',
            due_at=case.now, timezone='UTC', recurrence=None, channel='telegram', created_from='settings',
            state=prep.STATE_SCHEDULED, accepted_by=prep.ACCEPTED_OWNER_SETTINGS,
            window=(600, case.now + 300, 1))
        case.tick()
        [work] = case.runs(row['id'])
        case.script = [{'tool_calls': [call('loc', 'ask_location', reason='Please share your current position.')]},
                       finish('f', 'loc', summary='Please share your current position.')]
        self.assertTrue(case.service.run_one())
        case.service.deliver_one()
        [request] = case.pending()
        case.now = row['window_end'] + 1
        self.assertLess(case.now, request['expires'], 'the location itself is still answerable')
        case.location()
        self.assertEqual(case.continued(), [])
        case.tick()
        settled = case.service.preparations.get(row['id'])
        self.assertEqual(settled['state'], 'expired')
        self.assertIsNone(settled['prepared_result_ref'])
        self.assertEqual(case.store.job(work['id'])['status'], 'failed')
        self.assertEqual(settled['run_count'], 1)
