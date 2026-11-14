"""#821 C1: owner-accepted scheduled runs may ask for sign-in, never payment approval.

Model-free service evidence: real temporary stores, preparation ticks and login
settlement, with the existing fake browser profile and Telegram/model wires.
Reuse review: N/A for production implementation (tests only, no new dependency
or runtime mechanism); reuse test_preparations._Case and test_flow_login's
JarDriver, memory_jar and window-thread helpers. No live account is contacted.
"""
import json
import math
from unittest import mock

from personal_agent import browser_session as bs
from personal_agent import owner_mcp, preparations as prep, trust_record
from personal_agent.agent_runtime import ToolError
from personal_agent.messages import msg
from personal_agent.quickstart_service import (
    BROWSER_LOGIN_PAGE_OFFERED_TEXT, BROWSER_LOGIN_RESULT_TEXT,
    BROWSER_LOGIN_UNATTENDED_TEXT,
    UNATTENDED_APPROVAL_TEXT,
)

from test_connect_effects import ACTION, INPUT
from test_flow_login import JarDriver, LoginHarness, close_login_windows, memory_jar, wait_until
from test_preparations import CHAT, GENERATION, _Case, call

SITE = 'https://shop.example.test'
OWNER_ACCEPTED = (prep.ACCEPTED_OWNER_REQUEST, prep.ACCEPTED_OWNER_BUTTON,
                  prep.ACCEPTED_OWNER_SETTINGS, prep.ACCEPTED_OWNER_ANSWER)


class _Runs(_Case):
    def setUp(self):
        super().setUp()
        self.drivers = []
        self.profile_path = self.root.parent / 'profile'

        def launcher(profile_dir, headless):
            driver = JarDriver()
            self.drivers.append(driver)
            return driver

        self.launcher = launcher
        self.profile = self.new_profile()
        self.service.browser_profile = self.profile
        self.addCleanup(lambda: close_login_windows(self, self.drivers, self.profile))

    def new_profile(self):
        return bs.BrowserProfile(self.profile_path, launcher=self.launcher, jar=memory_jar(self.profile_path))

    def preparation(self, accepted_by=prep.ACCEPTED_OWNER_REQUEST, quiet=False):
        due = self.now + 60
        terms = {'window': (600, due + 3600, 3), 'delivery_mode': prep.DELIVERY_WHEN_NEEDED} if quiet else {}
        return self.service.preparations.create(
            kind=prep.KIND_PREPARE, goal='계정의 요청한 자료를 확인해 줘', title='자료 확인', due_at=due,
            timezone='Asia/Seoul', recurrence=None, channel='telegram', created_from='settings',
            state=prep.STATE_SCHEDULED, accepted_by=accepted_by,
            origin=prep.ORIGIN_DREAM if accepted_by == prep.ACCEPTED_DREAM else None, **terms)

    def started(self, **kwargs):
        row = self.preparation(**kwargs)
        self.now = row['due_at'] + 1
        self.assertTrue(self.tick())
        [run] = self.runs(row['id'])
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (run['id'],))
        return self.service.preparations.get(row['id']), self.store.job(run['id'])

    def tick(self):
        return self.service.run_due_preparation(self.now)

    def ended_waiting(self, job):
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='partial',response='로그인이 필요합니다.',delivery='none' WHERE id=?",
                       (job['id'],))

    def ask(self, job):
        return self.service._request_browser_login(job, SITE + '/login')

    def offered(self, job, deliver=True):
        self.assertEqual(self.ask(job), BROWSER_LOGIN_PAGE_OFFERED_TEXT)
        self.ended_waiting(job)
        self.assertTrue(self.service.offer_browser_login(self.store.job(job['id'])))
        self.shown(job['id'])
        if deliver:
            self.assertTrue(wait_until(self.service.deliver_notification))
        return self.service._browser_login(job['id'])

    def open_run(self, **kwargs):
        row, job = self.started(**kwargs)
        login = self.offered(job)
        return row, job, login, self.store.notification_of(job['id'], 'browser_login_needed')

    state = LoginHarness.state
    shown = LoginHarness.shown
    settle = LoginHarness.settle
    window_thread_done = LoginHarness.window_thread_done
    window = LoginHarness.window

    def owner_closes(self, work_id):
        self.window().login('shop.example.test')
        LoginHarness.owner_closes(self, work_id, logged_in=False)

    def edits(self):
        return [body for method, body in self.telegram if method == 'editMessageText']

    def notices(self, work_id):
        with self.store.db() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM telegram_notifications WHERE job_id=? AND kind='browser_login_needed'", (work_id,))]

    def receipts(self, work_id):
        with self.store.db() as db:
            return [(row['status'], json.loads(row['detail'])) for row in db.execute(
                "SELECT status,detail FROM tool_events WHERE job_id=? AND tool='action_continuation' ORDER BY id",
                (work_id,))]

    @staticmethod
    def continuation(login):
        digest = 'browser-login:' + login['nonce']
        return {'action': 'browser_sign_in', 'digest': digest, 'offer_digest': digest,
                'preparation': login.get('preparation')}

    def queue(self, job, login):
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            return self.service._queue_action_continuation(db, self.store.job(job['id']), self.continuation(login))

    def assert_no_continuation(self, job):
        self.assertNotEqual(self.store.job(job['id'])['status'], 'queued')
        self.assertFalse(any(detail.get('resumed') for _, detail in self.receipts(job['id'])))

    @staticmethod
    def binding(job):
        return bs.step_binding(job['id'], 'api_request', SITE + '/charge', 'POST /charge',
                               argument='{"amount":"10"}', state='api-payment-v2|slot@1|payment')

    def assert_payment_refused(self, job):
        with self.assertRaises(ToolError) as caught:
            self.service.browser_approvals_for(job).request(self.binding(job), 'POST /charge 10')
        self.assertEqual((caught.exception.code, str(caught.exception)), ('unattended_run', UNATTENDED_APPROVAL_TEXT))
        self.assertIs(caught.exception.ran, False)
        result = self.service.connector_permission(job, {'engine': 'codex', 'tool_name': ACTION, 'input': INPUT})
        self.assertEqual(result, {'behavior': 'deny', 'message': UNATTENDED_APPROVAL_TEXT})
        self.assertIsNone(self.service._browser_request(job['id']))
        self.assertIsNone(self.store.notification_of(job['id'], 'browser_approval_needed'))

    def money_connection(self):
        self.service.set_ai_connections({'enabled': True})
        owner_mcp.set_confirmed(self.store, 'codex', ['synthetic'])
        trust_record.set_money(self.store, trust_record.connection('codex', 'owner-mcp', 'synthetic'), True)


class ScheduledSignIn(_Runs):
    def test_owner_accepted_run_asks_once_and_owner_close_continues(self):
        for accepted_by in OWNER_ACCEPTED:
            with self.subTest(accepted_by=accepted_by):
                row, job = self.started(accepted_by=accepted_by)
                self.assertTrue(self.service.owner_accepted_run(job))
                login = self.offered(job)
                self.assertEqual(self.ask(job), BROWSER_LOGIN_PAGE_OFFERED_TEXT)
                self.assertEqual(self.service._browser_login(job['id'])['nonce'], login['nonce'])
                self.assertEqual(login['preparation'], {'id': row['id'], 'accepted_by': accepted_by,
                                                       'revision': row['updated_at'], 'run': job['id']})
                self.assertEqual(len(self.notices(job['id'])), 1)
                self.tick()
                self.assertEqual(self.service.preparations.get(row['id'])['state'], prep.STATE_RUNNING)
                self.owner_closes(job['id'])
                self.assertEqual(self.state(job['id']), 'resumed')
                self.assertEqual(self.store.job(job['id'])['status'], 'queued')
                [(status, detail)] = self.receipts(job['id'])
                self.assertEqual((status, detail['resumed']), ('queued', True))
                self.assertEqual(self.service.preparations.get(row['id'])['state'], prep.STATE_RUNNING)

    def test_decline_ends_step_and_tells_owner_once(self):
        row, job, _, note = self.open_run()
        before = len(self.edits())
        self.tap(f"p7l:{note['id']}:skip", note['message_id'])
        self.assertEqual(self.settle(job['id']), 'skipped')
        self.tap(f"p7l:{note['id']}:done", note['message_id'])
        self.service.process_browser_logins()
        self.assert_no_continuation(job)
        self.assertEqual(self.receipts(job['id']), [])
        self.assertEqual([edit['text'] for edit in self.edits()[before:]], [BROWSER_LOGIN_RESULT_TEXT['skipped']])
        self.tick()
        self.assertNotEqual(self.service.preparations.get(row['id'])['state'], prep.STATE_RUNNING)

    def test_unanswered_login_expires_and_tells_owner_once(self):
        row, job, login, note = self.open_run()
        before = len(self.edits())
        self.assertEqual(self.settle(job['id'], now=login['deadline'] + 1), 'expired')
        self.service.process_browser_logins(now=login['deadline'] + 2)
        self.tap(f"p7l:{note['id']}:done", note['message_id'])
        self.assert_no_continuation(job)
        self.assertEqual(len(self.edits()[before:]), 1)
        self.assertEqual(self.store.notification(note['id'])['state'], 'browser_login_expired')
        self.tick()
        self.assertNotEqual(self.service.preparations.get(row['id'])['state'], prep.STATE_RUNNING)

    def test_cancel_waiting_run_blocks_late_sign_in_without_second_prompt(self):
        row, job, _, _ = self.open_run()
        self.service.preparations.cancel(row['id'])
        self.tick()
        self.owner_closes(job['id'])
        self.assertEqual(self.state(job['id']), 'not_resumed')
        self.assert_no_continuation(job)
        self.assertEqual(self.service.preparations.get(row['id'])['state'], prep.STATE_CANCELLED)
        self.assertEqual(len(self.notices(job['id'])), 1)

    def test_pause_and_edit_are_refused_while_stamp_remains_current(self):
        row, job, login, _ = self.open_run()
        for action in (lambda: self.service.preparations.pause(row['id']),
                       lambda: self.service.preparations.edit(row['id'], title='다른 제목')):
            with self.subTest(action=action):
                with self.assertRaises(prep.PreparationRefusal) as refused:
                    action()
                self.assertEqual(refused.exception.code, 'already_running')
        self.assertEqual(self.service.preparations.get(row['id'])['updated_at'], login['preparation']['revision'])
        with self.store.db() as db:
            self.assertIsNone(prep.Preparations.wait_refusal(db, job['id'], login['preparation']))
        self.owner_closes(job['id'])
        self.assertEqual(self.store.job(job['id'])['status'], 'queued')

    def test_stale_version_withholds_once_and_marks_work_partial(self):
        row, job, login, _ = self.open_run()
        with self.store.db() as db:
            db.execute('UPDATE preparations SET updated_at=? WHERE id=?',
                       (math.nextafter(row['updated_at'], math.inf), row['id']))
        self.owner_closes(job['id'])
        self.assertEqual(self.state(job['id']), 'not_resumed')
        self.assertFalse(self.queue(job, login))
        [(status, detail)] = self.receipts(job['id'])
        self.assertEqual((status, detail['reason'], detail['resumed']), ('withheld', 'preparation-changed', False))
        self.assertEqual(detail['offer_digest'], self.continuation(login)['offer_digest'])
        current = self.store.job(job['id'])
        self.assertEqual((current['status'], current['owner_cause']), ('partial', msg('preparations.wait_changed')))
        self.assertEqual(len(self.edits()), 1)

    def test_restart_expires_offered_login_without_continuation_or_duplicate_prompt(self):
        row, job, _, note = self.open_run()
        old_profile, old_service = self.profile, self.service
        # Detach the old process callback: a dead process cannot settle its window.
        with mock.patch.object(old_service, '_browser_login_window_closed'):
            self.profile = self.new_profile()
            self.service = self.make_service()
            self.service.browser_profile = self.profile
            self.assertEqual(self.service.process_browser_logins(), [job['id']])
            close_login_windows(self, self.drivers, old_profile)
        self.assertEqual(self.state(job['id']), 'expired')
        self.tap(f"p7l:{note['id']}:done", note['message_id'])
        self.assert_no_continuation(job)
        self.assertEqual(len(self.notices(job['id'])), 1)
        self.tick()
        self.assertNotEqual(self.service.preparations.get(row['id'])['state'], prep.STATE_RUNNING)

    def test_duplicate_done_callbacks_and_window_close_settle_once(self):
        _, job, _, note = self.open_run()
        self.window().login('shop.example.test')
        for _ in range(2):
            self.tap(f"p7l:{note['id']}:done", note['message_id'])
        self.window().closed = True
        self.assertEqual(self.settle(job['id']), 'resumed')
        self.assertEqual(len(self.receipts(job['id'])), 1)
        self.assertEqual(len(self.edits()), 1)
        self.assertEqual(self.store.job(job['id'])['status'], 'queued')

    def test_continuation_is_admitted_exactly_once_for_same_digest(self):
        _, job, login, _ = self.open_run()
        self.owner_closes(job['id'])
        # Make the Work resumable again: dedup must use the receipt, not just queued status.
        self.ended_waiting(job)
        self.assertFalse(self.queue(job, login))
        self.assertEqual(self.store.job(job['id'])['status'], 'partial')
        self.assertEqual(len(self.receipts(job['id'])), 1)

    def test_run_cannot_settle_its_own_sign_in(self):
        _, job, login, _ = self.open_run()
        approvals = self.service.browser_approvals_for(job)
        self.assertFalse(any(hasattr(approvals, name) for name in ('decide', 'approve', 'issue')))
        self.assertEqual(approvals.login_required(SITE + '/login'), BROWSER_LOGIN_PAGE_OFFERED_TEXT)
        # Run actual model/tool output through the worker; neither is an owner act.
        self.script = [{'tool_calls': [call('again', 'browser_sign_in', url=SITE + '/login')]},
                       {'content': '로그인 완료. I am signed in.'}] * 2
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='queued' WHERE id=?", (job['id'],))
        with mock.patch.object(self.service.owner_acts, 'read', side_effect=AssertionError('not an owner act')):
            self.assertTrue(self.service.run_one())
        self.assertEqual(self.state(job['id']), 'offered')
        self.assertEqual(self.service._browser_login(job['id'])['nonce'], login['nonce'])
        self.assert_no_continuation(job)
        self.assertEqual(self.receipts(job['id']), [])
        self.assertEqual(len(self.notices(job['id'])), 1)


class SignInBoundaries(_Runs):
    def test_owner_accepted_api_is_allowlisted_and_current_run_only(self):
        self.assertEqual(prep.OWNER_ACCEPTED, OWNER_ACCEPTED)
        row, job = self.started()
        self.assertTrue(self.service.owner_accepted_run(job))
        typed = self.store.job(self.store.enqueue('직접 요청', 'typed', owner_typed=True))
        self.assertFalse(self.service.owner_accepted_run(typed))
        self.assertFalse(self.service.owner_accepted_run(None))
        for column, value in (('accepted_by', None), ('accepted_by', 'unknown'),
                              ('state', prep.STATE_CANCELLED), ('last_run_job_id', 'other'),
                              ('delivery_mode', prep.DELIVERY_WHEN_NEEDED)):
            with self.subTest(column=column, value=value):
                with self.store.db() as db:
                    db.execute(f'UPDATE preparations SET {column}=? WHERE id=?', (value, row['id']))
                self.assertFalse(self.service.owner_accepted_run(job))
                with self.store.db() as db:
                    db.execute(f'UPDATE preparations SET {column}=? WHERE id=?', (row[column], row['id']))

    def test_wait_refusal_fails_closed_for_missing_or_changed_preparation(self):
        row, job = self.started()
        stamp = {'id': row['id'], 'accepted_by': row['accepted_by'], 'revision': row['updated_at'], 'run': job['id']}
        with self.store.db() as db:
            check = prep.Preparations.wait_refusal
            self.assertIsNone(check(db, job['id'], stamp))
            for bad in (None, {}, {**stamp, 'id': 'missing'}, {**stamp, 'revision': row['updated_at'] + 1}):
                with self.subTest(stamp=bad):
                    self.assertEqual(check(db, job['id'], bad), 'preparation-changed')
            for column, value in (('accepted_by', None), ('accepted_by', 'unknown'),
                                  ('state', prep.STATE_CANCELLED), ('last_run_job_id', 'another-run')):
                with self.subTest(column=column):
                    db.execute(f'UPDATE preparations SET {column}=? WHERE id=?', (value, row['id']))
                    self.assertEqual(check(db, job['id'], stamp), 'preparation-changed')
                    db.execute(f'UPDATE preparations SET {column}=? WHERE id=?', (row[column], row['id']))

    def test_non_owner_and_when_needed_runs_ask_no_login_with_autonomy_on(self):
        self.service.set_payment_autonomy({'enabled': True, 'revision': 0})
        for provenance in (*prep.SELF_SET, None, 'unknown', 'missing', 'when_needed'):
            with self.subTest(provenance=provenance):
                row, job = self.started(accepted_by=prep.ACCEPTED_OWNER_REQUEST if provenance in ('missing', 'when_needed')
                                        else provenance, quiet=provenance == 'when_needed')
                if provenance == 'missing':
                    self.service.preparations.cancel(row['id'])
                    self.service.preparations.delete(row['id'])
                for explicit in (False, True):
                    self.assertEqual(self.service._request_browser_login(job, SITE + '/login', explicit=explicit),
                                     BROWSER_LOGIN_UNATTENDED_TEXT)
                self.assertIsNone(self.service._browser_login(job['id']))
                self.assertFalse(self.service.offer_browser_login(job))
                self.assertEqual(self.notices(job['id']), [])

    def test_self_set_runs_have_no_payment_request_or_autonomy_authorization(self):
        # Payment keeps the #717 autonomy contract unchanged (``self_set_run``): a NULL acceptance on a
        # present row and an owner-accepted ``when_needed`` run are not narrowed here; #821 adds no
        # scheduled payment path and removes none.
        self.money_connection()
        self.service.set_payment_autonomy({'enabled': True, 'revision': 0})
        for provenance in (*prep.SELF_SET, 'missing'):
            with self.subTest(provenance=provenance):
                row, job = self.started(accepted_by=prep.ACCEPTED_OWNER_REQUEST if provenance in ('missing', 'when_needed')
                                        else provenance, quiet=provenance == 'when_needed')
                if provenance == 'missing':
                    self.service.preparations.cancel(row['id'])
                    self.service.preparations.delete(row['id'])
                self.assertIsNone(self.service._authorize_payment_autonomy(job, None, 'pay'))
                self.assertIsNone(self.service._authorize_payment_autonomy(job, self.binding(job), 'pay 10'))
                self.assertIsNone(self.service._authorize_payment_autonomy(job, self.binding(job), 'pay 10', dispatch=True))
                self.assert_payment_refused(job)

    def test_owner_accepted_payment_without_autonomy_is_refused(self):
        self.money_connection()
        for accepted_by in OWNER_ACCEPTED:
            with self.subTest(accepted_by=accepted_by):
                _, job = self.started(accepted_by=accepted_by)
                self.assert_payment_refused(job)

    def test_typed_owner_work_still_asks_without_preparation_stamp(self):
        work = self.store.enqueue('계정 자료를 확인해 줘', 'typed-owner', 'telegram:' + GENERATION,
                                  chat_id=CHAT, owner_typed=True)
        job = self.store.job(work)
        login = self.offered(job)
        self.assertNotIn('preparation', login)
        self.assertEqual(len(self.notices(work)), 1)
        self.owner_closes(work)
        self.assertEqual(self.state(work), 'resumed')
        self.assertEqual(self.store.job(work)['status'], 'queued')
        self.assertEqual(len(self.receipts(work)), 1)
