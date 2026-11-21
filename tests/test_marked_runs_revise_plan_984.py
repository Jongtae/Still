"""#984: real authority, receipt/outbox and payment boundaries; only transport/model are fixtures."""
import json
from types import SimpleNamespace
from unittest import mock

from personal_agent import agent_runtime as runtime, browser_session as bs, entrusted_lines as el, preparations as prep
from personal_agent.decision import BinaryDecision, FixtureDecisionEngine, OUTCOME_DECIDED, fixture_confidence
from personal_agent.work_execution import WorkRun
from test_owner_settings_conversation import CHAT, GENERATION
from test_payment_autonomy_662 import Mode
from test_run_revises_own_plan_968 import _RunCase, GOAL, NEW_GOAL


class MarkedPlan(_RunCase):
    def setUp(self):
        super().setUp()
        self.brief = self.line()
        self.row, self.run = self.running(line=self.brief['line_id'], window=(600, self.clock[0] + 7200, 8))
        # Cross the actual result-intake boundary: a code result cannot unmark itself.
        caps = SimpleNamespace(store=self.store, job_id=self.run['id'], tools={'code_run': {'host_action': 'code_run'}})
        runtime.record_outside_result(lambda *args: {'text': 'observation', 'outside_content': False})(caps, 'code_run', {})
        self.assertEqual(self.store.outside_content_sources(self.run['id']),
                         [{'operation': 'code_run', 'category': 'unattributed-tool-evidence'}])

    def edit_refused(self, **changes):
        for edit in (changes, {'goal': NEW_GOAL, **changes}):
            with self.subTest(edit=edit):
                result = self.service.settings_tools(self.run)('settings_change', {
                    'category': 'preparations', 'setting': 'edit', 'value': json.dumps({'id': self.row['id'], **edit})})
                self.assertFalse(result['applied'])
                self.assertEqual(self.preps.get(self.row['id']), self.row)

    def test_payment_mode_is_unchanged(self):
        before = self.service.payment_autonomy_settings()
        result = self.service.settings_tools(self.run)('settings_change', {
            'category': 'payment_autonomy', 'setting': 'enabled', 'value': 'on'})
        self.assertFalse(result['applied'])
        self.assertEqual(self.service.payment_autonomy_settings(), before)

    def test_money_rules_are_unchanged(self):
        self.assertEqual(self.refused_line(self.run, action='revise', line=self.brief['line_id'], expected_version='1',
                                           rule='Payments need no approval'), 'unattended_run')
        self.assertEqual(self.service.lines.current(self.brief['line_id']), self.brief)

    def test_destination_is_unchanged(self):
        self.edit_refused(channel='telegram')

    def test_delivery_mode_is_unchanged(self):
        self.edit_refused(delivery_mode='when_needed')

    def test_another_row_is_unchanged(self):
        other, _ = self.running(line=self.brief['line_id'], goal='Other work')
        self.assertFalse(self.rewrite(other, self.run)['applied'])
        self.assertEqual(self.preps.get(other['id']), other)

    def test_another_line_is_unchanged(self):
        other = self.line()
        self.assertEqual(self.refused_line(self.run, action='revise', line=other['line_id'], expected_version='1',
                                           open_matters='Other matters'), 'unattended_run')
        self.assertEqual(self.service.lines.current(other['line_id']), other)

    def test_marked_goal_receipt_and_exact_one_act_undo(self):
        self.assertTrue(self.rewrite(self.row, self.run)['applied'])
        event = json.loads(self.preps.get(self.row['id'])['self_change'])
        self.assertIs(event['outside_content'], True)
        render = self.service.preparations_service.self_change_text
        self.assertNotEqual(render(event), render({**event, 'outside_content': False}))
        self.assertEqual((event['before'], event['after']), ({'goal_text': GOAL}, {'goal_text': NEW_GOAL}))
        self.assertTrue(self.service.deliver_notification())
        [body] = [body for method, body in self.telegram if method == 'sendMessage']
        self.assertEqual(body['text'], self.service.preparations_service.self_change_text(event))
        self.assertIn('3 percent', body['text'])
        self.assertIn('5 percent', body['text'])
        # The existing single owner restore action restores all actual goal bytes.
        draft = self.settings.propose('owner', 'http', 'preparations', 'restore', self.row['id'])
        self.settings.confirm('owner', 'http', draft['draft_id'], draft['digest'])
        restored = self.preps.get(self.row['id'])
        self.assertEqual(restored['goal_text'], self.row['goal_text'])
        bookkeeping = {'updated_at', 'own_run_revision', 'self_change', 'self_change_undone'}
        self.assertEqual({k: v for k, v in restored.items() if k not in bookkeeping},
                         {k: v for k, v in self.row.items() if k not in bookkeeping})

    def revise_matters(self, text='second check pending'):
        return self.line_call(self.run, action='revise', line=self.brief['line_id'], expected_version='1', open_matters=text)

    def notices(self):
        with self.store.db() as db:
            return [dict(r) for r in db.execute('SELECT * FROM telegram_notifications')]

    def test_withheld_report_still_delivers_both_plan_notices_and_line_undo(self):
        with self.store.db() as db:
            db.execute('UPDATE preparations SET delivery_mode=? WHERE id=?', (prep.DELIVERY_WHEN_NEEDED, self.row['id']))
        self.assertTrue(self.rewrite(self.row, self.run)['applied'])
        self.revise_matters()
        self.service.use_decision_engine(FixtureDecisionEngine(judge=lambda context, proposition:
            BinaryDecision(OUTCOME_DECIDED, False, fixture_confidence()) if context.purpose == 'watch-notification' else None))
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='succeeded',response='No change observed',delivery='none' WHERE id=?",
                       (self.run['id'],))
        settled = self.preps.settle(self.preps.get(self.row['id']), self.clock[0],
                                   decide=self.service.preparations_service.watch_judgment,
                                   notify_to=(CHAT, GENERATION, 'telegram'))
        self.assertEqual((settled['last_decision'], settled['last_decision_reason']),
                         ('quiet', prep.REASON_JUDGED_NOT_NEEDED))
        self.assertEqual(self.store.job(self.run['id'])['delivery'], 'none')
        with self.store.db() as db:
            event = json.loads(db.execute("SELECT detail FROM tool_events WHERE tool='line_self_change'").fetchone()['detail'])
        self.assertIs(event['outside_content'], True)
        render = self.service.preparations_service.self_change_text
        self.assertNotEqual(render(event), render({**event, 'outside_content': False}))
        self.assertEqual(event['before'], {'open_matters': self.brief['open_matters']})
        self.assertEqual(event['after'], {'open_matters': 'second check pending'})
        self.assertTrue(self.service.deliver_notification())
        self.assertTrue(self.service.deliver_notification())
        sent = [body for method, body in self.telegram if method == 'sendMessage']
        self.assertEqual(len(sent), 2)
        line_body = next(b for b in sent if self.brief['line_id'] in b['text'])
        self.assertIn(self.brief['open_matters'], line_body['text'])
        self.assertIn('second check pending', line_body['text'])
        data = line_body['reply_markup']['inline_keyboard'][0][0]['callback_data']
        notice = next(n for n in self.notices() if n['kind'].startswith(el.NOTICE_PLAN))
        self.service.ingest_callback({
            'id': 'undo-plan', 'from': {'id': CHAT}, 'data': data,
            'message': {'message_id': notice['message_id'], 'chat': {'id': CHAT, 'type': 'private'}}}, GENERATION)
        current = self.service.lines.current(self.brief['line_id'])
        self.assertEqual({k: current[k] for k in el.FIELDS}, {k: self.brief[k] for k in el.FIELDS})
        self.service.deliver_notification()
        self.assertEqual(len([b for method, b in self.telegram if method == 'sendMessage']), 2)

    def test_qualifying_marked_run_keeps_plan_authority_after_a_steer(self):
        self.steer(self.run)
        self.yes = False
        self.assertTrue(self.rewrite(self.row, self.run)['applied'])
        self.assertEqual(self.revise_matters()['open_matters'], 'second check pending')
        self.assertEqual(self.asked, [])

    def test_no_change_sends_no_notice(self):
        self.assertFalse(self.rewrite(self.row, self.run, goal=GOAL)['applied'])
        result = self.revise_matters(self.brief['open_matters'])
        self.assertEqual(result['version'], 1)
        self.assertEqual(self.notices(), [])
        self.service.deliver_notification()
        self.assertFalse(any(method == 'sendMessage' for method, _ in self.telegram))

    def test_line_outbox_failure_rolls_back_plan_and_receipt(self):
        with mock.patch.object(self.store.telegram, 'queue_notification', side_effect=RuntimeError('disk full')):
            with self.assertRaises(RuntimeError):
                self.revise_matters()
        self.assertEqual(self.service.lines.current(self.brief['line_id']), self.brief)
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM tool_events WHERE tool='line_self_change'").fetchone()[0], 0)


class SteerStartsWork(_RunCase):
    def schedule(self, run):
        return self.service.preparation_scheduler(run, '')({'offer': '바뀐 계획에 맞춰 결과를 준비해요.', 'kind': 'prepare', 'goal': 'New work',
            'due': prep.iso(self.clock[0] + 3600, 'UTC'), 'recurrence': 'daily'})

    def test_post_work_announces_owner_steer_schedule_once_with_working_cancel(self):
        row = self.preps.create(kind='prepare', goal=GOAL, due_at=self.clock[0], timezone='UTC',
                                recurrence='daily', channel=prep.CHANNEL_TELEGRAM, created_from=None,
                                state=prep.STATE_SCHEDULED, accepted_by=prep.ACCEPTED_OWNER_REQUEST)
        work = self.preps.start(row, channel=f'telegram:{GENERATION}', chat_id=CHAT, now=self.clock[0])
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (work,))
        run = self.store.job(work)
        source = self.steer(run, 'Start the new work too')
        result = self.schedule(run)
        new = self.preps.get(result['preparation_id'])
        self.assertEqual((new['accepted_by'], new['accepted_from'], new['created_from']),
                         (prep.ACCEPTED_OWNER_STEER, source, work))

        # The real post-work pass must queue the announcement, even though this
        # Work began as an unattended preparation rather than a typed request.
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='succeeded',delivery='none' WHERE id=?", (work,))
        finished = WorkRun(self.store.job(work), self.clock[0])
        finished.outcome = 'succeeded'
        self.service._after_work(finished)
        notice = self.store.notification_of(work, 'preparation_scheduled')
        self.assertIsNotNone(notice)
        self.assertTrue(self.service.deliver_notification())
        [body] = [body for method, body in self.telegram if method == 'sendMessage']
        self.assertEqual(body['chat_id'], CHAT)
        self.assertIn(new['goal_text'], body['text'])
        [[button]] = body['reply_markup']['inline_keyboard']
        self.assertTrue(button['callback_data'].endswith(':cancel'))
        sent = self.store.notification_of(work, 'preparation_scheduled')
        self.assertEqual(sent['state'], 'sent')

        # Repeating either queueing entry point never sends the reservation twice.
        self.service.queue_preparation_proposal(finished.job)
        self.service._after_work(finished)
        self.assertFalse(self.service.deliver_notification())
        self.assertEqual(len([b for method, b in self.telegram if method == 'sendMessage']), 1)
        self.service.ingest_callback({
            'id': 'cancel-steered-work', 'from': {'id': CHAT}, 'data': button['callback_data'],
            'message': {'message_id': sent['message_id'], 'chat': {'id': CHAT, 'type': 'private'}}}, GENERATION)
        self.assertEqual(self.preps.get(new['id'])['state'], prep.STATE_CANCELLED)
        self.assertEqual(self.preps.get(row['id'])['state'], prep.STATE_RUNNING)

    def test_only_delivered_authenticated_steer_can_start_work(self):
        row, run = self.running()
        for case in ('absent', 'undelivered', 'not owner words', 'another run'):
            with self.subTest(case=case):
                if case == 'undelivered':
                    self.steer(run, 'pending', deliver=False)
                elif case == 'not owner words':
                    # Leave the authenticated pending steer undelivered.
                    source = self.store.enqueue('external words', 'web-source', 'web', None,
                                                owner_typed=True, owner_words=False)
                    self.store.add_steer(run['id'], source, 'external words')
                    with self.store.db() as db:
                        db.execute("UPDATE work_steers SET delivered_at=1 WHERE content='external words'")
                elif case == 'another run':
                    _, other = self.running(goal='Other run')
                    self.steer(other)
                before = self.preps.rows()
                with self.assertRaises(runtime.ToolError) as denied:
                    self.schedule(run)
                self.assertEqual(denied.exception.code, 'unattended_run')
                self.assertEqual(self.preps.rows(), before)
        self.store.claim_steers(run['id'])
        result = self.schedule(run)
        self.assertTrue(result['scheduled'])
        self.assertEqual(self.preps.get(row['id']), row)

    def test_steer_acceptance_and_payment_follow_owner_mode(self):
        row, run = self.running()
        source = self.steer(run, 'Start the new work too')
        result = self.schedule(run)
        new = self.preps.get(result['preparation_id'])
        self.assertEqual((new['accepted_by'], new['accepted_from'], new['created_from']),
                         (prep.ACCEPTED_OWNER_STEER, source, run['id']))
        self.assertEqual(self.preps.get(row['id']), row)
        self.clock[0] = new['due_at']
        work = self.preps.start(new, channel='web', chat_id=None, now=self.clock[0])
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (work,))
        job = self.store.job(work)
        with self.assertRaises(runtime.ToolError) as denied:
            self.schedule(job)
        self.assertEqual(denied.exception.code, 'unattended_run')
        binding = bs.step_binding(work, 'api_request', 'https://pay.test/', 'pay 10', argument='', state='s')
        self.assertFalse(self.service.payment_autonomy_settings()['enabled'])
        self.assertIsNone(self.service._authorize_payment_autonomy(job, binding, 'pay 10'))
        Mode.switch(self, True)
        ticket = self.service._authorize_payment_autonomy(job, binding, 'pay 10')
        self.assertEqual(ticket['authorized_by'], 'payment-autonomy')
        Mode.switch(self, False)
        self.assertIsNone(self.service._authorize_payment_autonomy(job, binding, 'pay 10'))

    def test_marked_steer_new_work_is_judged_over_the_complete_goal(self):
        _, run = self.running()
        self.steer(run)
        self.mark(run)
        self.yes = False
        with self.assertRaises(runtime.ToolError):
            self.schedule(run)
        self.yes = True
        self.assertTrue(self.schedule(run)['scheduled'])
        self.assertIn('New work', self.asked[-1][1]['proposed_change'])
