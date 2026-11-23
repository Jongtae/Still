"""#1170: a not-met attempt is re-delegated past Still's own records it can revise in place, and only there.

Expected 3: a re-delegation of the same Work continues past a scheduled preparation or an entrusted
line the attempt set up (Still's own owner records with an in-place revision), and the next brief names
each record.  Expected 5 (negative): every effect that cannot be repeated safely - an observed external
change, a payment step, an approved state-changing code run, an unknown or cut-off call - still stops
re-delegation; every other purpose (retry, steer, resume) is unchanged; and the call classes the
approval and information-use records read are unchanged.

Evidence class: model-free.  Real ``QuickStore`` and ``AgentService``; tool events are inserted as the
routes record them (``test_one_continuation_rule_618.Store``).  No model, browser or account calls.
"""
import json
import unittest

from personal_agent.agent_runtime import (EFFECT_CHANGED, EFFECT_NONE, record_effect, revisable_record_note)

from test_one_continuation_rule_618 import Store

PREPARED = {'evidence': {'preparation_id': 'prep-1170a', 'kind': 'prepare', 'state': 'scheduled',
                         'scheduled': True, 'due': '2030-01-02T09:00:00', 'accepted_by': 'owner_work'}}
LINE = {'evidence': {'line_id': 'line-1170a', 'version': 2, 'state': 'active'}}


class Records(Store):
    def prepared(self, goal, accepted_by=None, state=None):
        import time
        from personal_agent import preparations as prep
        row = self.service.preparations.create(kind='prepare', goal=goal, due_at=time.time() + 3600, timezone='UTC',
                                               recurrence=None, channel='web', created_from=self.work,
                                               state=state or prep.STATE_SCHEDULED,
                                               accepted_by=accepted_by if accepted_by is not None else prep.ACCEPTED_OWNER_WORK)
        return row['id']

    def scheduled(self, goal='kept goal', call_id='p1', **kwargs):
        ident = self.prepared(goal, **kwargs)
        self.call('schedule_preparation', {'evidence': {**PREPARED['evidence'], 'preparation_id': ident}}, 'succeeded', call_id)
        return ident


class ReviseInPlace(Records):
    def records(self):
        self.scheduled()
        self.call('entrusted_line', LINE, 'succeeded', 'l1')

    def test_a_redelegation_continues_past_records_it_revises_in_place(self):
        self.records()
        self.assertEqual(self.service.may_continue(self.work, 'redelegate'), (True, None))

    def test_every_other_purpose_still_stops_on_them(self):
        # Negative: a retry starts the request again as a new Work, which would set them up a second time.
        self.records()
        for purpose in ('retry', 'steer', 'resume'):
            with self.subTest(purpose=purpose):
                self.assertEqual(self.service.may_continue(self.work, purpose), (False, 'effect'))

    def test_the_call_classes_other_readers_use_are_unchanged(self):
        # The approval, receipt and information-use records still read them as changes.
        for action, detail in (('schedule_preparation', PREPARED), ('entrusted_line', LINE)):
            self.assertEqual(record_effect(action, detail, 'succeeded'), EFFECT_CHANGED)

    def test_the_next_brief_names_each_record_by_id_only(self):
        self.assertEqual(revisable_record_note(self.store, self.work), '')
        kept = self.prepared('SECRET-GOAL one')
        self.call('schedule_preparation', {'evidence': {**PREPARED['evidence'], 'preparation_id': kept}}, 'succeeded', 'p1')
        self.call('entrusted_line', LINE, 'succeeded', 'l1')
        # A read of a line is no record to revise (#1163).
        self.call('entrusted_line', {'declared_effect': 'read', 'evidence': {'line_id': 'line-read', 'version': 1}},
                  'succeeded', 'l2')
        note = revisable_record_note(self.store, self.work)
        facts = json.loads(note.rsplit('\n', 1)[1])
        self.assertEqual([(f['tool'], f.get('preparation_id') or f.get('line_id')) for f in facts],
                         [('schedule_preparation', kept), ('entrusted_line', 'line-1170a')])
        self.assertIn('replaces', note)
        self.assertNotIn('SECRET-GOAL', note, 'identifiers and states only, never a goal')

    def test_a_replaced_or_cancelled_preparation_is_not_offered_for_revision(self):
        # Codex review: a replacement cancels the row it replaces; only the current row is named.
        replaced, current = self.prepared('first goal'), self.prepared('second goal')
        self.call('schedule_preparation', {'evidence': {**PREPARED['evidence'], 'preparation_id': replaced}}, 'succeeded', 'p1')
        self.call('schedule_preparation', {'evidence': {**PREPARED['evidence'], 'preparation_id': current}}, 'succeeded', 'p2')
        self.service.preparations.cancel(replaced)
        facts = json.loads(revisable_record_note(self.store, self.work).rsplit('\n', 1)[1])
        self.assertEqual([f.get('preparation_id') for f in facts], [current])
        self.assertEqual(facts[0]['state'], 'scheduled', 'the current state, not the recorded one')
        self.service.preparations.cancel(current)
        self.assertEqual(revisable_record_note(self.store, self.work), '')

    def test_a_record_under_another_tool_id_is_named(self):
        # Codex review: a manifest may expose the action under another tool id; the recorded host action decides.
        kept = self.prepared('aliased goal')
        base = {'scope': 's', 'call_id': 'alias', 'host_action': 'schedule_preparation'}
        self.event('plan_next_check', 'running', base)
        self.event('plan_next_check', 'succeeded', {**base, 'evidence': {**PREPARED['evidence'], 'preparation_id': kept}})
        facts = json.loads(revisable_record_note(self.store, self.work).rsplit('\n', 1)[1])
        self.assertEqual([f.get('preparation_id') for f in facts], [kept])

    def test_a_line_piece_is_marked_to_be_scheduled_again_not_replaced(self):
        # Codex review: schedule_piece refuses replaces; a piece is revised by scheduling it again.
        parent, piece = self.prepared('parent run'), self.prepared('next piece')
        # The row as schedule_piece leaves it: one child of the run that scheduled it.
        with self.store.db() as db:
            db.execute('UPDATE preparations SET piece_of=? WHERE id=?', (parent, piece))
        plain = self.prepared('plain goal')
        for ident, call_id in ((piece, 'p1'), (plain, 'p2')):
            self.call('schedule_preparation', {'evidence': {**PREPARED['evidence'], 'preparation_id': ident}}, 'succeeded', call_id)
        note = revisable_record_note(self.store, self.work)
        facts = {f['preparation_id']: f for f in json.loads(note.rsplit('\n', 1)[1])}
        self.assertTrue(facts[piece].get('line_piece'))
        self.assertNotIn('line_piece', facts[plain])
        self.assertIn('without replaces', note)

    def test_a_record_this_work_may_not_replace_still_stops(self):
        # Codex review: the scheduler refuses ``replaces`` from a daily-plan run and for a proposal, so such a
        # record is neither a reason to re-delegate nor offered for revision.
        from personal_agent import preparations as prep
        for label, kwargs in (('day plan', {'accepted_by': prep.ACCEPTED_DAY_PLAN}),
                              ('proposal', {'accepted_by': '', 'state': prep.STATE_PROPOSED})):
            with self.subTest(label):
                self.setUp()
                if label == 'proposal':
                    ident = self.prepared('offered goal', state=prep.STATE_PROPOSED)
                    with self.store.db() as db:
                        db.execute('UPDATE preparations SET accepted_by=NULL WHERE id=?', (ident,))
                    self.call('schedule_preparation', {'evidence': {**PREPARED['evidence'], 'preparation_id': ident}},
                              'succeeded', 'p1')
                else:
                    self.scheduled(**kwargs)
                self.assertEqual(self.service.may_continue(self.work, 'redelegate'), (False, 'effect'))
                self.assertEqual(revisable_record_note(self.store, self.work), '')


class StillStops(Records):
    """Expected 5: what cannot be repeated safely stops re-delegation, beside a revisable record or not."""

    def setUp(self):
        super().setUp()
        self.scheduled()

    def test_an_external_change_still_stops(self):
        self.call('browser_click', {'declared_effect': 'mutate', 'evidence': {'state': 'page'}}, 'succeeded', 'b1')
        self.assertEqual(self.service.may_continue(self.work, 'redelegate'), (False, 'effect'))

    def test_a_payment_step_still_stops(self):
        self.call('browser_click', {'declared_effect': 'payment', 'evidence': {'state': 'page'}}, 'succeeded', 'b1')
        self.assertEqual(self.service.may_continue(self.work, 'redelegate'), (False, 'effect'))

    def test_a_payment_still_waits_for_its_approval_and_spends_none(self):
        # A step refused for its approval ran nothing: the approval path is the same as before.
        self.call('browser_click', {'declared_effect': 'payment', 'code': 'approval_required', 'effect': 'none',
                                    'ran': False}, 'failed', 'b1')
        self.assertEqual(record_effect('browser_click', {'declared_effect': 'payment', 'code': 'approval_required',
                                                         'effect': 'none', 'ran': False}, 'failed'), EFFECT_NONE)
        self.assertEqual(self.service.may_continue(self.work, 'redelegate'), (True, None))

    def test_an_approved_state_changing_code_run_still_stops(self):
        self.call('code_run', {'evidence': {'effects': [{'method': 'POST'}]}}, 'succeeded', 'c1')
        self.assertEqual(self.service.may_continue(self.work, 'redelegate'), (False, 'effect'))

    def test_a_failed_preparation_call_still_stops(self):
        # Only a definite success is a record to revise; a failure after execution stays a change.
        self.call('schedule_preparation', {'code': 'tool_failed'}, 'failed', 'p2')
        self.assertEqual(self.service.may_continue(self.work, 'redelegate'), (False, 'effect'))

    def test_an_unknown_or_cut_off_call_still_stops(self):
        self.event('api_request', 'running', {'scope': 's', 'call_id': 'cut', 'host_action': 'api_request',
                                              'declared_effect': 'mutate'})
        self.assertEqual(self.service.may_continue(self.work, 'redelegate'), (False, 'unknown'))

    def test_a_cut_off_preparation_call_still_stops(self):
        self.event('schedule_preparation', 'running', {'scope': 's', 'call_id': 'cut', 'host_action': 'schedule_preparation'})
        reasons = self.service.continuation_reasons(self.work, 'redelegate')
        self.assertTrue(reasons, 'a call with no terminal record is never revised in place')


if __name__ == '__main__':
    unittest.main()
