"""#583: the daily look-back suggests a standing preparation for a request repeated on separate days.

The dream's one judgment may return up to two ``suggestions``.  Still keeps one only when it cites
two turns of different owner dates, validates it with ``schedule_preparation``'s normalisers, skips
what an active preparation has or the owner declined, and stores it ``proposed`` on the dream's
anchor Work.  It is offered once, in the existing proposal message, after the owner's next own
Work; an unanswered one lapses after a week and is not a decline.  Nothing runs before the owner's yes.

#638: the look back also looks ahead.  A one-off (``recurrence`` ``once``) for something dated the owner
mentioned rests on one cited turn; a look back holding one is offered on the preparations tick in the
owner's daytime, without waiting for the next Work, under the same daily cap and pause; one whose time
passes unanswered lapses and is not a decline.

Evidence class: model-free unit and service tests.  A scripted ``FixtureDecisionEngine`` plays the
Judgment AI, a recording Telegram transport and a fake preparations clock replace only the wires;
the real ``AgentService`` dream run, Work loop, notification delivery, callback handler and
preparation tick run unchanged.  No live model or Telegram is contacted, and none is claimed.
"""
import json
import unittest
from datetime import datetime, timedelta, timezone

from personal_agent import owner_model as om
from personal_agent.current_context import TIMEZONE_CONFIG_KEY
from personal_agent import preparations as prep

from test_owner_model_dream import Dream, dream
import test_preparations as prep_t
from test_preparations import CHAT, _Case, engine


def suggestion(goal='평일 아침 일정 요약', due=None, sources=('t1', 't2'), kind='prepare', recurrence='weekdays',
               delivery='send', title='아침 일정 요약', offer='평일 아침 7시에 그날 일정을 정리해 둘게요.'):
    due = due or (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(timespec='seconds')
    return {'kind': kind, 'goal': goal, 'due': due, 'recurrence': recurrence, 'delivery': delivery, 'title': title,
            'offer': offer, 'sources': list(sources)}


class Gates(unittest.TestCase):
    """``om.validate_suggestions``: a pattern needs two cited turns of different days."""
    refs = {'t1': 'a', 't2': 'b', 't3': 'c'}
    days = {'t1': '2026-10-30', 't2': '2026-10-31', 't3': '2026-10-31'}

    def reasons(self, *items):
        kept, dropped = om.validate_suggestions(list(items), self.refs, self.days)
        return [item['index'] for item in kept], [item['reason'] for item in dropped]

    def test_two_turns_of_different_days_are_kept(self):
        self.assertEqual(self.reasons(suggestion(sources=('t1', 't3'))), ([0], []))

    def test_one_occasion_one_day_or_an_unread_turn_is_dropped(self):
        self.assertEqual(self.reasons(suggestion(sources=('t1',)), suggestion(sources=('t2', 't3')),
                                      suggestion(sources=('t1', 't9')), suggestion(sources=())),
                         ([], ['one-occasion', 'one-day', 'sources', 'sources']))

    def test_an_undated_turn_dates_nothing(self):
        kept, dropped = om.validate_suggestions([suggestion()], self.refs, {'t1': '2026-10-30'})
        self.assertEqual((kept, [item['reason'] for item in dropped]), ([], ['one-day']))

    def test_at_most_two_are_kept(self):
        self.assertEqual(self.reasons(*[suggestion(sources=('t1', 't2'))] * 3), ([0, 1], ['limit']))

    def test_a_one_off_rests_on_one_cited_turn(self):
        once = dict(recurrence=om.SUGGESTION_ONCE)
        self.assertEqual(self.reasons(suggestion(sources=('t1',), **once), suggestion(sources=('t9',), **once),
                                      suggestion(sources=(), **once)),
                         ([0], ['sources', 'sources']))

    def test_the_limit_counts_both_kinds(self):
        self.assertEqual(self.reasons(suggestion(sources=('t1', 't2')), suggestion(sources=('t3',), recurrence='once'),
                                      suggestion(sources=('t3',), recurrence='once')),
                         ([0, 1], ['limit']))

    def test_the_schema_requires_every_field_and_the_shape_accepts_none(self):
        item = om.DREAM_SCHEMA['properties']['suggestions']['items']
        self.assertEqual(set(item['required']), set(item['properties']))
        self.assertIn('suggestions', om.DREAM_SCHEMA['required'])
        self.assertTrue(om.dream_shape(dream()), 'an answer without suggestions is none')
        self.assertFalse(om.dream_shape({**dream(), 'suggestions': 'x'}))


class LookBack(Dream):
    """The dream run stores what passes as a ``proposed`` row and records it in its Evidence, no text."""

    def two_days(self):
        first, second, newest = self.day('오늘 일정 정리해줘', '오늘 일정 다시 정리해줘', '다른 질문')
        self.a_day_earlier(first)
        return first, second, newest

    def rows(self):
        return self.service.preparations.rows()

    def detail(self):
        [(_status, detail)] = self.evidence(self.dream_rows()[0]['job_id'])
        return detail

    def test_a_request_asked_on_two_days_becomes_a_proposal_on_the_anchor(self):
        _first, _second, newest = self.two_days()
        self.dreams = [{**dream(), 'suggestions': [suggestion()]}]
        self.assertTrue(self.tick())
        [row] = self.rows()
        self.assertEqual((row['state'], row['origin'], row['created_from'], row['accepted_by']),
                         (prep.STATE_PROPOSED, prep.ORIGIN_DREAM, newest, None))
        self.assertEqual((row['kind'], row['recurrence'], row['title_text']), ('prepare', 'weekdays', '아침 일정 요약'))
        [kept] = self.detail()['suggestions']['kept']
        self.assertEqual((kept['preparation_id'], kept['sources']), (row['id'], ['t1', 't2']))
        self.assertNotIn('일정', json.dumps(self.detail(), ensure_ascii=False), 'Evidence keeps no text')
        [(context, _question, schema)] = self.asked
        self.assertEqual(context.facts['preparations'], 'none')
        self.assertIn('suggestions', schema['properties'])
        self.assertEqual(self.service.work_waits_for_owner(self.store.job(newest)), (),
                         'the anchor Work itself does not wait for the suggestion')

    def test_one_occasion_or_one_day_is_never_suggested(self):
        self.day('오늘 일정 정리해줘', '오늘 일정 다시 정리해줘', '다른 질문')
        self.dreams = [{**dream(), 'suggestions': [suggestion(sources=('t1',)), suggestion(sources=('t1', 't2'))]}]
        self.tick()
        self.assertEqual(self.rows(), [])
        self.assertEqual([item['reason'] for item in self.detail()['suggestions']['dropped']], ['one-occasion', 'one-day'])

    def test_a_declined_or_active_goal_is_not_suggested_again(self):
        self.two_days()
        future = datetime.now(timezone.utc).timestamp() + 86400
        declined = self.service.preparations.create(kind='prepare', goal='평일 아침 일정 요약', due_at=future, timezone='UTC',
                                                    recurrence='weekdays', channel='web', created_from='earlier',
                                                    state=prep.STATE_PROPOSED)
        self.service.cancel_preparation(declined['id'])
        self.service.preparations.create(kind='reminder', goal='물 마시기', due_at=future, timezone='UTC',
                                         recurrence='daily', channel='web', created_from='settings',
                                         state=prep.STATE_SCHEDULED, accepted_by=prep.ACCEPTED_OWNER_SETTINGS)
        self.dreams = [{**dream(), 'suggestions': [suggestion(), suggestion(goal='물 마시기', kind='reminder')]}]
        self.tick()
        self.assertEqual([row['state'] for row in self.rows()], ['scheduled', 'cancelled'], 'no new row')
        self.assertEqual([item['reason'] for item in self.detail()['suggestions']['dropped']], ['declined_before', 'active'])
        [(context, _question, _schema)] = self.asked
        self.assertIn('물 마시기', context.facts['preparations'], 'the look back is shown what is already active')

    def test_an_invalid_suggestion_is_refused_by_the_tools_own_normalisers(self):
        self.two_days()
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec='seconds')
        self.dreams = [{**dream(), 'suggestions': [suggestion(due=past), suggestion(kind='reminder', delivery='when_needed')]}]
        self.tick()
        self.assertEqual(self.rows(), [])
        self.assertEqual([item['reason'] for item in self.detail()['suggestions']['dropped']],
                         ['due_in_past', 'invalid_delivery'])

    def test_a_dated_need_mentioned_once_becomes_a_one_off_proposal(self):
        _mention, _other, newest = self.day('다음 주 화요일까지 서류 내야 해', '다른 질문', '또 다른 질문')
        self.dreams = [{**dream(), 'suggestions': [suggestion(goal='화요일 서류 제출', kind='reminder', recurrence='once',
                                                               sources=('t1',)),
                                                    suggestion(goal='매일 일정', sources=('t1',))]}]
        self.tick()
        [row] = self.rows()
        self.assertEqual((row['state'], row['origin'], row['created_from'], row['kind'], row['recurrence']),
                         (prep.STATE_PROPOSED, prep.ORIGIN_DREAM, newest, 'reminder', None))
        self.assertEqual([item['reason'] for item in self.detail()['suggestions']['dropped']], ['one-occasion'],
                         'a standing suggestion still needs two days')

    def test_a_paused_owner_model_suggests_nothing(self):
        self.two_days()
        self.dreams = [{**dream(), 'suggestions': [suggestion()]}]
        original = self.upkeep.run_dream

        def paused(*args, **kwargs):
            self.upkeep.set_controls({'enabled': False})
            return original(*args, **kwargs)
        self.upkeep.run_dream = paused
        self.tick()
        self.assertEqual(self.rows(), [])


class Offer(_Case):
    """Offered once with the owner's next own Work, through the existing message and buttons."""

    def suggest(self, anchor, **fields):
        outcomes, calls = self.service.suggest_preparations(anchor, [suggestion(due=self.due_iso(86400), **fields)])
        self.assertEqual(calls, 0)
        return self.service.preparations.get(outcomes[0]['preparation_id'])

    def deliver(self):
        self.service.deliver_one()
        self.service.deliver_notification()

    def notification(self, work):
        with self.store.db() as db:
            row = db.execute("SELECT * FROM telegram_notifications WHERE job_id=? AND kind='preparation_proposed'",
                             (work,)).fetchone()
        return dict(row) if row else None

    def proposal_sends(self):
        return [body for body in self.sends() if 'p7p:' in json.dumps(body.get('reply_markup') or {})]

    def test_shown_once_after_the_next_own_work_and_accepted_with_its_button(self):
        anchor = self.receive('첫 질문')
        self.deliver()
        row = self.suggest(anchor)
        self.now += 3600
        self.assertFalse(self.tick(), 'a suggestion never runs before acceptance')
        self.receive('다음 질문')
        self.deliver()
        [message] = self.proposal_sends()
        self.assertIn('아침 일정 요약', message['text'])
        notification = self.notification(anchor)
        self.assertEqual((notification['chat_id'], notification['state']), (CHAT, 'sent'))
        self.assertIsNotNone(self.service.preparations.get(row['id'])['offered_at'])
        self.receive('또 다른 질문')
        self.deliver()
        self.assertEqual(len(self.proposal_sends()), 1, 'offered once')
        self.tap(f"p7p:{notification['id']}:accept", notification['message_id'])
        accepted = self.service.preparations.get(row['id'])
        self.assertEqual((accepted['state'], accepted['accepted_by']), ('scheduled', 'owner-button'))
        self.now = accepted['due_at'] + 1
        self.assertTrue(self.tick())
        self.assertEqual(len(self.runs(row['id'])), 1)

    def test_declining_it_is_a_decline(self):
        anchor = self.receive('첫 질문')
        row = self.suggest(anchor)
        self.receive('다음 질문')
        self.deliver()
        notification = self.notification(anchor)
        self.tap(f"p7p:{notification['id']}:deny", notification['message_id'])
        self.assertEqual(self.service.preparations.get(row['id'])['state'], 'cancelled')
        self.assertEqual(self.service.preparations.declined_goals(self.now), ['평일 아침 일정 요약'])
        self.now += 86400
        self.assertEqual(self.service.suggest_preparations(self.receive('셋째 질문'), [suggestion(due=self.due_iso(86400))]),
                         ([{'reason': 'declined_before'}], 0))

    def test_at_most_one_suggestion_message_a_day(self):
        first = self.receive('첫 질문')
        self.suggest(first)
        second = self.receive('둘째 질문')
        self.suggest(second, goal='주간 보고 정리', recurrence='weekly')
        self.receive('셋째 질문')
        self.deliver()
        self.receive('넷째 질문')
        self.deliver()
        self.assertEqual(len(self.proposal_sends()), 1)
        self.assertIsNotNone(self.notification(first), 'the oldest look back is offered first')
        self.assertIsNone(self.notification(second))
        self.now += 86400
        self.receive('다음 날 질문')
        self.deliver()
        self.assertEqual(len(self.proposal_sends()), 2)
        self.assertIsNotNone(self.notification(second))

    def test_a_first_slot_that_passed_before_the_offer_moves_to_the_next_one(self):
        anchor = self.receive('첫 질문')
        outcomes, _calls = self.service.suggest_preparations(anchor, [suggestion(due=self.due_iso(3600), recurrence='daily')])
        row = self.service.preparations.get(outcomes[0]['preparation_id'])
        self.now += 2 * 3600
        self.receive('다음 질문')
        offered = self.service.preparations.get(row['id'])
        self.assertEqual(offered['due_at'], row['due_at'] + 86400, 'the message shows the slot an acceptance keeps')
        self.assertFalse(self.tick())
        self.assertEqual(self.runs(row['id']), [])

    def test_paused_upkeep_offers_nothing(self):
        anchor = self.receive('첫 질문')
        self.suggest(anchor)
        self.service.owner_model.set_controls({'enabled': False})
        self.receive('다음 질문')
        self.deliver()
        self.assertEqual(self.proposal_sends(), [])
        self.assertIsNone(self.notification(anchor))

    def test_an_anchor_that_already_offered_a_proposal_gets_no_suggestion(self):
        self.script = [{'content': None, 'tool_calls': [{'id': '1', 'type': 'function', 'function': {
            'name': 'schedule_preparation', 'arguments': json.dumps({'kind': 'reminder', 'goal': '물 마시기',
                                                                     'due': self.due_iso(3600)})}}]},
                       {'content': '알려 드릴까요?'}]
        self.service.use_decision_engine(engine(preparation=False))
        anchor = self.receive('물 마실 때 알려줄래?')
        self.assertEqual(self.service.suggest_preparations(anchor, [suggestion(due=self.due_iso(86400))]),
                         ([{'reason': 'anchor-offered'}], 0))

    def test_an_unanswered_suggestion_expires_after_a_week_and_is_not_a_decline(self):
        anchor = self.receive('첫 질문')
        row = self.suggest(anchor)
        self.receive('다음 질문')
        self.deliver()
        notification = self.notification(anchor)
        self.now += prep.SUGGESTION_EXPIRY_SECONDS - 60
        self.tick()
        self.assertEqual(self.service.preparations.get(row['id'])['state'], 'proposed')
        self.now += 120
        self.tick()
        self.assertEqual(self.service.preparations.get(row['id'])['state'], prep.STATE_EXPIRED)
        self.assertEqual(self.runs(row['id']), [])
        self.assertEqual(self.service.preparations.declined_goals(self.now), [], 'a lapse is not a decline')
        self.tap(f"p7p:{notification['id']}:accept", notification['message_id'])
        self.assertEqual(self.service.preparations.get(row['id'])['state'], prep.STATE_EXPIRED, 'too late to accept')
        again = self.suggest(self.receive('한 주 뒤 질문'))
        self.assertEqual(again['state'], prep.STATE_PROPOSED, 'it may be suggested again')


class OneOff(Offer):
    """#638: a look back holding a one-off is offered on the tick, in the owner's daytime, without a new Work."""

    def setUp(self):
        super().setUp()
        # The offer hours and the daily cap read the owner's zone (``resolve_local_timezone``), not the host's.
        self.store.put(TIMEZONE_CONFIG_KEY, 'Asia/Seoul')

    def one_off(self, anchor, seconds=2 * 86400):
        outcomes, _calls = self.service.suggest_preparations(anchor, [suggestion(
            goal='화요일 서류 제출', kind='reminder', recurrence=om.SUGGESTION_ONCE, sources=('t1',),
            due=self.due_iso(seconds))])
        return self.service.preparations.get(outcomes[0]['preparation_id'])

    def test_offered_on_the_tick_without_a_new_work_and_accepted_with_its_button(self):
        anchor = self.receive('다음 주 화요일까지 서류 내야 해')
        self.deliver()
        row = self.one_off(anchor)
        self.assertEqual((row['state'], row['recurrence']), (prep.STATE_PROPOSED, None))
        self.assertFalse(self.tick(), 'offering is not a run')
        self.deliver()
        [message] = self.proposal_sends()
        self.assertIn('화요일 서류 제출', message['text'])
        notification = self.notification(anchor)
        self.assertEqual((notification['chat_id'], notification['state']), (CHAT, 'sent'))
        self.tick()
        self.deliver()
        self.assertEqual(len(self.proposal_sends()), 1, 'offered once')
        self.tap(f"p7p:{notification['id']}:accept", notification['message_id'])
        accepted = self.service.preparations.get(row['id'])
        self.assertEqual((accepted['state'], accepted['accepted_by']), ('scheduled', 'owner-button'))
        self.now = accepted['due_at'] + 1
        self.assertTrue(self.tick())
        self.assertEqual(len(self.runs(row['id'])), 1)
        self.now += 86400
        self.tick()
        self.assertEqual(len(self.runs(row['id'])), 1, 'a one-off runs once')

    def test_a_standing_suggestion_still_waits_for_the_next_work(self):
        anchor = self.receive('첫 질문')
        self.deliver()
        self.suggest(anchor)
        self.tick()
        self.deliver()
        self.assertEqual(self.proposal_sends(), [])
        self.assertIsNone(self.notification(anchor))

    def test_not_offered_at_night_but_in_the_morning(self):
        anchor = self.receive('다음 주 화요일까지 서류 내야 해')
        self.deliver()
        self.one_off(anchor)
        self.now = prep_t.SEOUL_MIDNIGHT + 3 * 3600
        self.tick()
        self.deliver()
        self.assertEqual(self.proposal_sends(), [], '03:00 is outside the offer hours')
        self.now = prep_t.SEOUL_MIDNIGHT + 8 * 3600
        self.tick()
        self.deliver()
        self.assertEqual(len(self.proposal_sends()), 1)

    def test_the_daily_cap_and_the_pause_hold(self):
        first = self.receive('첫 질문')
        self.suggest(first)
        self.receive('둘째 질문')
        self.deliver()
        self.assertEqual(len(self.proposal_sends()), 1)
        later = self.receive('다음 주 화요일까지 서류 내야 해')
        self.deliver()
        self.one_off(later)
        self.tick()
        self.deliver()
        self.assertEqual(len(self.proposal_sends()), 1, 'one suggestion message per owner day')
        self.service.owner_model.set_controls({'enabled': False})
        self.now = prep_t.SEOUL_MIDNIGHT + 9 * 3600
        self.tick()
        self.deliver()
        self.assertEqual(len(self.proposal_sends()), 1, 'none while upkeep is paused')
        self.service.owner_model.set_controls({'enabled': True})
        self.tick()
        self.deliver()
        self.assertEqual(len(self.proposal_sends()), 2)
        self.assertIsNotNone(self.notification(later))

    def test_a_one_off_whose_time_passed_lapses_and_is_not_a_decline(self):
        anchor = self.receive('다음 주 화요일까지 서류 내야 해')
        self.deliver()
        self.service.owner_model.set_controls({'enabled': False})
        row = self.one_off(anchor, seconds=600)
        self.now += 601
        self.tick()
        self.assertEqual(self.service.preparations.get(row['id'])['state'], prep.STATE_EXPIRED)
        self.assertEqual(self.runs(row['id']), [])
        self.assertEqual(self.service.preparations.declined_goals(self.now), [], 'a lapse is not a decline')
        self.service.owner_model.set_controls({'enabled': True})
        self.tick()
        self.receive('다음 질문')
        self.deliver()
        self.assertEqual(self.proposal_sends(), [], 'a lapsed one-off is never offered')


if __name__ == '__main__':
    unittest.main()
