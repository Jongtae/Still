"""#1228: a line run's own open matters are recorded, not pushed; owner-set changes are still told.

Evidence class: model-free unit tests with the real QuickStore, ``AgentService``, preparations,
entrusted lines, settings tools, notification outbox and Telegram surface (transport recorded by the
existing fixture), a FixtureDecisionEngine and a fake clock. No live model or messenger is contacted.
"""
import json
from datetime import datetime
from types import SimpleNamespace

from personal_agent import agent_runtime as runtime, entrusted_lines as el
from personal_agent.messages import msg
from test_owner_settings_conversation import CHAT, GENERATION
from test_run_revises_own_plan_968 import _RunCase, GOAL, NEW_GOAL


class OpenMattersQuiet(_RunCase):
    def setUp(self):
        super().setUp()
        self.brief = self.line()
        self.row, self.run = self.running(line=self.brief['line_id'], window=(600, self.clock[0] + 7200, 8))
        # The run read outside content, across the real result-intake boundary.
        caps = SimpleNamespace(store=self.store, job_id=self.run['id'], tools={'code_run': {'host_action': 'code_run'}})
        runtime.record_outside_result(lambda *args: {'text': 'page text', 'outside_content': False})(caps, 'code_run', {})
        self.assertTrue(self.store.outside_content_sources(self.run['id']))
        # The row's owner conversation is paired, so a told change has somewhere to go.
        self.assertIsNotNone(self.service.preparations_service.self_change_destination(self.row))

    def notices(self):
        with self.store.db() as db:
            return [dict(r) for r in db.execute('SELECT * FROM telegram_notifications ORDER BY id')]

    def sent(self):
        return [body for method, body in self.telegram if method == 'sendMessage']

    def receipt(self):
        with self.store.db() as db:
            return json.loads(db.execute("SELECT detail FROM tool_events WHERE tool='line_self_change'").fetchone()['detail'])

    def revise_matters(self, text='second check pending'):
        return self.line_call(self.run, action='revise', line=self.brief['line_id'], expected_version='1', open_matters=text)

    def assert_no_machinery(self, text):
        for raw in (self.brief['line_id'], self.row['id'], self.run['id']):
            self.assertNotIn(raw, text)

    def test_open_matters_rewrite_is_recorded_with_outside_marker_and_not_sent(self):
        result = self.revise_matters()
        self.assertEqual((result['version'], result['open_matters']), (2, 'second check pending'))
        self.assertIs(result['outside_content'], True)
        event = self.receipt()
        self.assertEqual((event['target'], event['version'], event['outside_content']), ('lines.open_matters', 2, True))
        self.assertEqual((event['before'], event['after']),
                         ({'open_matters': 'first check pending'}, {'open_matters': 'second check pending'}))
        self.assertEqual(self.notices(), [])
        self.assertFalse(self.service.deliver_notification())
        self.assertEqual(self.sent(), [])

    def test_repeated_rewrites_on_a_watch_never_reach_the_messenger(self):
        for version, text in ((1, 'a'), (2, 'b'), (3, 'c')):
            self.line_call(self.run, action='revise', line=self.brief['line_id'], expected_version=str(version), open_matters=text)
        self.assertEqual(self.service.lines.current(self.brief['line_id'])['version'], 4)
        while self.service.deliver_notification():
            pass
        self.assertEqual(self.sent(), [])

    def test_a_run_written_hold_becomes_a_dated_note_at_the_owners_midnight(self):
        self.service.context_observations.set_controls({'timezone': 'Asia/Seoul'})
        # The run's schedule uses UTC, and both checks fall on the same UTC date.
        self.clock[0] = datetime.fromisoformat('2027-01-15T14:55:00+00:00').timestamp()
        row, run = self.running(line=self.brief['line_id'])
        hold = 'Wait for another instruction before the next check.'
        self.line_call(run, action='revise', line=self.brief['line_id'], expected_version='1', open_matters=hold)
        self.assertEqual(self.service.lines.current(self.brief['line_id'])['open_matters_run_day'], '2027-01-15')
        self.assertIn(hold, self.service.preparations_service.line_run_input(run, row))
        self.settle(row, run)

        self.clock[0] += 600
        next_row, next_run = self.running(line=self.brief['line_id'])
        lines = self.service.preparations_service
        seen = lines.line_run_input(next_run, next_row)
        note = msg('lines.previous_run_notes', date='2027-01-15', text=hold)
        self.assertIn(note, seen)
        self.assertNotIn(hold, seen.replace(note, ''))
        brief = lines.line_read(self.brief['line_id'])
        self.assertEqual(brief['open_matters'], '')
        self.assertEqual(brief['previous_run_notes'], {'date': '2027-01-15', 'text': hold})

    def test_a_run_writing_after_midnight_keeps_its_start_day(self):
        self.service.context_observations.set_controls({'timezone': 'Asia/Seoul'})
        self.clock[0] = datetime.fromisoformat('2027-01-15T14:55:00+00:00').timestamp()
        _row, run = self.running(line=self.brief['line_id'])
        self.clock[0] += 600
        hold = 'Wait for another instruction before the next check.'
        result = self.line_call(run, action='revise', line=self.brief['line_id'], expected_version='1', open_matters=hold)
        self.assertEqual(result['open_matters'], '')
        self.assertEqual(result['previous_run_notes'], {'date': '2027-01-15', 'text': hold})

    def test_an_owner_revise_keeps_open_matters_current_across_days(self):
        self.revise_matters()
        owner = self.store.job(self.store.enqueue('Keep this pending', 'owner-keep-1249', 'web', None, owner_typed=True))
        # Explicitly keeping the same text also adopts it as the owner's open matter.
        hold = 'second check pending'
        result = self.line_call(owner, action='revise', line=self.brief['line_id'], expected_version='2', open_matters=hold)
        self.assertEqual(result['version'], 3)
        self.clock[0] += 86400
        row, run = self.running(line=self.brief['line_id'])
        lines = self.service.preparations_service
        seen = lines.line_run_input(run, row)
        self.assertIn(hold, seen)
        self.assertEqual(lines.line_read(self.brief['line_id'])['open_matters'], hold)

    def test_the_owner_undoes_it_by_asking(self):
        self.revise_matters()
        owner = self.store.job(self.store.enqueue('Undo the last change to that line', 'owner-undo-1228', 'web', None,
                                                  owner_typed=True))
        self.line_call(owner, action='undo', line=self.brief['line_id'], version='2')
        current = self.service.lines.current(self.brief['line_id'])
        self.assertEqual(current['open_matters'], 'first check pending')
        self.assertEqual({k: current[k] for k in el.FIELDS}, {k: self.brief[k] for k in el.FIELDS})

    def test_a_notice_queued_before_the_change_is_not_sent_and_a_sent_one_keeps_its_undo(self):
        self.revise_matters()
        chat, generation, surface = self.service.preparations_service.self_change_destination(self.row)
        fingerprint = f"{self.brief['line_id']}:2"
        self.store.telegram.queue_notification(self.run['id'], chat, generation, el.NOTICE_PLAN + fingerprint,
                                               fingerprint, surface=surface)
        self.service.deliver_notification()
        self.assertEqual(self.sent(), [])
        # One sent by an older version still undoes from its button.
        [stale] = self.notices()
        with self.store.db() as db:
            db.execute("UPDATE telegram_notifications SET state='sent',message_id=77 WHERE id=?", (stale['id'],))
        self.service.ingest_callback({
            'id': 'undo-old-plan', 'from': {'id': CHAT}, 'data': f"p7k:{stale['id']}:undo",
            'message': {'message_id': 77, 'chat': {'id': CHAT, 'type': 'private'}}}, GENERATION)
        self.assertEqual(self.service.lines.current(self.brief['line_id'])['open_matters'], 'first check pending')

    def told(self, result):
        self.assertTrue(result['applied'], result)
        event = json.loads(self.preps.get(self.row['id'])['self_change'])
        self.assertIs(event['outside_content'], True)
        self.assertTrue(self.service.deliver_notification())
        [body] = self.sent()
        self.assertIn(msg('preparations.self_change_outside', 'ko'), body['text'])
        self.assertEqual([[button['text'] for button in row] for row in body['reply_markup']['inline_keyboard']],
                         [[msg('button.undo')]])
        self.assert_no_machinery(body['text'])
        return body['text']

    def test_outside_content_run_changing_the_rule_is_still_told_with_the_outside_marker(self):
        text = self.told(self.rewrite(self.row, self.run))
        self.assertIn('3 percent', text)
        self.assertIn('5 percent', text)
        self.assertEqual(self.preps.get(self.row['id'])['goal_text'], NEW_GOAL)

    def test_outside_content_run_changing_the_schedule_is_still_told_with_the_outside_marker(self):
        result = self.service.settings_tools(self.run)('settings_change', {
            'category': 'preparations', 'setting': 'edit', 'reason': 'Fewer runs are enough.',
            'value': json.dumps({'id': self.row['id'], 'max_runs': 4})})
        self.told(result)
        self.assertEqual(self.preps.get(self.row['id'])['max_runs'], 4)
        self.assertEqual(self.preps.get(self.row['id'])['goal_text'], GOAL)

    def test_the_quiet_path_cannot_carry_an_owner_set_change(self):
        # Rules, task, promise, title and account schedule are refused on the run's own path, and leave nothing.
        for change in ({'rule': 'Report every hour'}, {'rule': 'Report every hour', 'rule_scope': 'today'},
                       {'drop_rule': '1'}, {'task': 'Watch another list'},
                       {'report_promise': 'no notes'}, {'title': 'Renamed'}, {'account_every_days': '30'},
                       {'open_matters': 'x', 'rule': 'Report every hour'}):
            with self.subTest(change=change):
                self.assertEqual(self.refused_line(self.run, action='revise', line=self.brief['line_id'],
                                                   expected_version='1', **change), 'unattended_run')
                self.assertEqual(self.service.lines.current(self.brief['line_id']), self.brief)
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM tool_events WHERE tool='line_self_change'").fetchone()[0], 0)
        self.assertEqual(self.notices(), [])
