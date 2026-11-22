"""#1097: a preparation's self-change notice names it by title, and the undo resolves by that name.

Evidence class: model-free fake-clock/temporary-store tests through the existing settings path and the
Telegram callback; no live AI.
"""
import json

from personal_agent import preparations as prep
from personal_agent.conversation_surface import notice_binding, notice_text
from personal_agent.settings_orchestrator import SettingsError

import test_self_narrowing_717 as sn
from test_owner_settings_conversation import CHAT, GENERATION, _Case


class _Titled(_Case):
    def setUp(self):
        super().setUp()
        self.clock[0] = 1800000000.0
        self.service.preparations.clock = lambda: self.clock[0]
        self.preps = self.service.preparations
        self.serial = 0

    change = sn.SelfNarrowing.change
    notices = sn.SelfNarrowing.notices

    def running(self, title=None, goal=None):
        self.serial += 1
        row = self.preps.create(kind='prepare', goal=goal or f'Check material {self.serial}', due_at=self.clock[0] + self.serial,
                                timezone='UTC', recurrence=None, channel='web', created_from=None,
                                state=prep.STATE_SCHEDULED, accepted_by=prep.ACCEPTED_OWNER_SETTINGS, title=title,
                                window=(600, self.clock[0] + 7200, 8), delivery_mode='when_needed')
        work = self.preps.start(row, channel='web', chat_id=None, now=self.clock[0])
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (work,))
        return self.preps.get(row['id']), self.store.job(work)

    def text(self):
        notice = self.notices()[-1]
        return notice_text(self.service, notice, notice_binding(self.service, notice))

    def undo(self, name):
        draft = self.settings.propose('owner', 'http', 'preparations', 'restore', name)
        return self.settings.confirm('owner', 'http', draft['draft_id'], draft['digest'])

    def tap(self, data, message_id):
        self.service.ingest_callback({'id': 'cb', 'from': {'id': CHAT}, 'data': data,
                                      'message': {'message_id': message_id, 'chat': {'id': CHAT, 'type': 'private'}}},
                                     GENERATION)


class NamedByTitle(_Titled):
    def test_the_notice_names_the_title_and_undo_by_that_title_restores_the_row(self):
        row, run = self.running(title='Morning market brief')
        self.assertTrue(self.change(row, run, 'edit', {'max_runs': 2})['applied'])
        text = self.text()
        self.assertIn('Morning market brief', text)
        self.assertNotIn(row['id'], text)
        # The owner says the name back, in other spacing and case; it resolves to the one row.
        self.assertEqual(self.undo('  morning  MARKET brief ')['state'], 'applied')
        restored = self.preps.get(row['id'])
        self.assertEqual((restored['max_runs'], restored['self_change_undone']), (row['max_runs'], 1))

    def test_a_repeated_title_is_told_with_its_schedule_and_a_bare_title_undoes_nothing(self):
        first, run = self.running(title='Weekly report')
        second, other = self.running(title='Weekly report')
        self.assertTrue(self.change(second, other, 'edit', {'max_runs': 3})['applied'])
        self.assertTrue(self.change(first, run, 'edit', {'max_runs': 2})['applied'])
        text = self.text()
        self.assertIn(' · '.join(('Weekly report', prep.schedule_text(first, self.service.home_zone()))), text)
        self.assertNotIn(first['id'], text)
        before = [self.preps.get(item['id']) for item in (first, second)]
        with self.assertRaises(SettingsError) as refused:
            self.settings.propose('owner', 'http', 'preparations', 'restore', 'Weekly report')
        # Both candidates are offered back, so the owner (or the AI asking) can say which.
        self.assertIn(first['id'], str(refused.exception))
        self.assertIn(second['id'], str(refused.exception))
        self.assertEqual([self.preps.get(item['id']) for item in (first, second)], before)
        # The id the refusal named settles it.
        self.assertEqual(self.undo(first['id'])['state'], 'applied')
        self.assertEqual(self.preps.get(first['id'])['max_runs'], first['max_runs'])

    def test_a_row_without_a_title_is_named_by_its_goal_and_undone_by_it(self):
        row, run = self.running(goal='Compare the three moving quotes')
        self.assertIsNone(row.get('title_text'))
        self.assertTrue(self.change(row, run, 'cancel')['applied'])
        text = self.text()
        self.assertIn('Compare the three moving quotes', text)
        self.assertNotIn(row['id'], text)
        self.assertEqual(self.undo('Compare the three moving quotes')['state'], 'applied')
        self.assertEqual(self.preps.get(row['id'])['state'], prep.STATE_RUNNING)

    def test_an_unknown_name_is_refused_and_changes_nothing(self):
        row, run = self.running(title='Rent reminder')
        self.assertTrue(self.change(row, run, 'pause')['applied'])
        before = self.preps.get(row['id'])
        with self.assertRaises(SettingsError):
            self.settings.propose('owner', 'http', 'preparations', 'restore', 'Something else')
        self.assertEqual(self.preps.get(row['id']), before)


class UndoButton(_Titled):
    def sent_notice(self, row, run, operation='edit', changes=None):
        self.assertTrue(self.change(row, run, operation, changes or {'max_runs': 2})['applied'])
        self.assertTrue(self.service.deliver_notification())
        notice = self.notices()[-1]
        sent = [body for method, body in self.telegram if method == 'sendMessage'][-1]
        markup = json.dumps(sent.get('reply_markup') or {})
        self.assertIn(f"p7k:{notice['id']}:undo", markup)
        return notice

    def test_the_button_undoes_this_run_once(self):
        row, run = self.running(title='Weekly report')
        self.running(title='Weekly report')  # a namesake: the button needs no name at all
        notice = self.sent_notice(row, run)
        self.tap(f"p7k:{notice['id']}:undo", notice['message_id'])
        restored = self.preps.get(row['id'])
        self.assertEqual((restored['max_runs'], restored['self_change_undone']), (row['max_runs'], 1))
        edits = [body for method, body in self.telegram if method == 'editMessageText']
        self.assertEqual(edits[-1]['reply_markup'], {'inline_keyboard': []})
        # A second tap acts on nothing.
        after = self.preps.get(row['id'])
        self.tap(f"p7k:{notice['id']}:undo", notice['message_id'])
        self.assertEqual(self.preps.get(row['id']), after)

    def test_a_stale_button_does_not_revert_a_later_owner_change(self):
        row, run = self.running(title='Weekly report')
        notice = self.sent_notice(row, run, 'pause', {})
        # The owner resumed it since: the receipt no longer describes the row.
        draft = self.settings.propose('owner', 'http', 'preparations', 'resume', row['id'])
        self.settings.confirm('owner', 'http', draft['draft_id'], draft['digest'])
        before = self.preps.get(row['id'])
        self.tap(f"p7k:{notice['id']}:undo", notice['message_id'])
        self.assertEqual(self.preps.get(row['id']), before)
        answers = [body.get('text') for method, body in self.telegram if method == 'answerCallbackQuery']
        self.assertTrue(answers and answers[-1], 'the owner is told why nothing changed')
