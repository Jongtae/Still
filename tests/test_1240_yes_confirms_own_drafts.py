"""#1240: a yes confirms only the settings drafts its conversation answers; a stale draft is withdrawn.

Live pattern (2026-11-26, counts only): two reservation-cancel drafts an earlier owner Work left
open (no expiry, #619) stayed pending after both reservations finished.  Two days later the owner
answered a later Work's question, which had no draft of its own, with a short yes; it was applied
to the two stale drafts, both failed, and the owner's request never became a Work.

Evidence class: model-free service tests over the real QuickStore / AgentService /
SettingsOrchestrator / OwnerActs, with a scripted compatible-API model and a scripted owner-act
judgment (``test_owner_acts.OwnerActCase``).  No live model or Telegram was contacted.
"""
import unittest

from personal_agent import preparations as prep
from personal_agent.settings_orchestrator import SettingsError
from test_owner_acts import OwnerActCase
from test_owner_settings_conversation import _Case


def approve_any(act, items):
    """A judgment that approves whatever is offered: what the code lets reach it is the point."""
    return (items[0]['id'], 'approve', '') if items else None


class AYesAnswersItsOwnConversation(OwnerActCase):
    def test_a_yes_to_the_work_that_made_the_draft_still_applies_it(self):
        self.offered()
        self.rule = approve_any
        self.receive('응 해줘')
        self.assertEqual(self.applies, [{'enabled': True}])
        self.assertEqual(self.offered_state(), 'applied')

    def test_a_yes_to_a_later_work_without_a_draft_runs_as_a_work_and_leaves_the_old_draft(self):
        self.offered()
        # A later owner Work asks its own question and makes no draft.
        self.now += 2 * 86400
        self.script = [{'content': '이 조건으로 맡겨 둘까요?'}]
        self.receive('새 조건으로 해 볼까?')
        calls = len(self.model_calls)
        self.rule = approve_any
        self.now += 60
        self.script = [{'content': '맡아서 진행할게요.'}]
        yes = self.receive('응 맡아줘')
        self.assertEqual(self.applies, [], "another Work's draft is not confirmed by this yes")
        self.assertEqual(self.offered_state(), 'awaiting-confirmation', 'the old draft stays open, untouched')
        self.assertNotIn('응 맡아줘', [act for act, _items, _target in self.acts],
                         'nothing this yes could answer was open, so nothing was judged')
        self.assertGreater(len(self.model_calls), calls, 'the yes ran as an ordinary Work')
        self.assertEqual(self.job(yes)['status'], 'succeeded')

    def test_a_reply_to_the_old_proposal_still_reaches_it(self):
        message_id = self.offered()
        self.now += 2 * 86400
        self.script = [{'content': '이 조건으로 맡겨 둘까요?'}]
        self.receive('새 조건으로 해 볼까?')
        self.rule = approve_any
        self.now += 60
        self.receive_reply('응 이거 해줘', message_id)
        self.assertEqual(self.applies, [{'enabled': True}], 'a reply-to binds the act to what that message showed')


class AFinishedTargetsDraftIsWithdrawn(_Case):
    def reservation(self):
        return self.service.preparations.create(
            kind='prepare', goal='시장 확인', due_at=4102444800, timezone='Asia/Seoul', recurrence=None,
            channel='web', created_from=None, state=prep.STATE_SCHEDULED, accepted_by=prep.ACCEPTED_OWNER_REQUEST)

    def finish(self, row):
        with self.store.db() as db:
            db.execute('UPDATE preparations SET state=? WHERE id=?', (prep.STATE_DELIVERED, row['id']))

    def test_a_cancel_draft_of_a_finished_reservation_is_withdrawn_and_never_applied(self):
        row = self.reservation()
        draft = self.settings.propose('owner', 'http', 'preparations', 'cancel', row['id'])
        self.finish(row)
        self.assertEqual(self.settings.pending_or_renewable('owner', 'http'), [], 'not offered to an answer')
        self.assertEqual(self.settings.read('preparations')['pending_changes'], [], 'nor shown as waiting')
        with self.assertRaises(SettingsError):
            self.settings.confirm('owner', 'http', draft['draft_id'], draft['digest'])
        self.assertEqual(self.settings._drafts()[draft['draft_id']]['state'], 'superseded', 'withdrawn')
        self.assertEqual(self.store.config('settings_audit')[-1]['error_class'], 'target-unavailable')
        self.assertEqual(self.service.preparations.get(row['id'])['state'], prep.STATE_DELIVERED)
        self.assertNotIn('applying_at', self.settings._drafts()[draft['draft_id']])

    def test_a_draft_whose_target_still_takes_the_change_stays_open(self):
        row = self.reservation()
        draft = self.settings.propose('owner', 'http', 'preparations', 'cancel', row['id'])
        self.assertEqual([item['id'] for item in self.settings.pending_or_renewable('owner', 'http')], [draft['draft_id']])
        self.assertEqual(self.settings.confirm('owner', 'http', draft['draft_id'], draft['digest'])['state'], 'applied')

    def test_an_unreadable_target_does_not_withdraw_the_draft(self):
        row = self.reservation()
        draft = self.settings.propose('owner', 'http', 'preparations', 'cancel', row['id'])
        rows = self.service.preparations.rows
        self.service.preparations.rows = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('unavailable'))
        try:
            self.settings.pending_drafts()
        finally:
            self.service.preparations.rows = rows
        self.assertEqual(self.settings._drafts()[draft['draft_id']]['state'], 'awaiting-confirmation',
                         'an unavailable read does not prove the change can no longer be made')


if __name__ == '__main__':
    unittest.main()
