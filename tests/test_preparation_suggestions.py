"""#717: AI-judged, grounded and bounded dream initiative.

Every suggestion cites a read turn; each scheduled row is told once in waking hours with its own
existing cancel notice before its first automatic start. Upkeep pause retains scheduled dream
rows. Dream budgets reserve owner capacity and bound standing recurrence. Legacy proposals keep
their acceptance and expiry paths. Evidence: model-free fake-clock/store/service tests, no live AI
or messenger calls.
"""
import json
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone

from personal_agent import owner_model as om
from personal_agent.current_context import TIMEZONE_CONFIG_KEY
from personal_agent import preparations as prep

from test_owner_model_dream import Dream, dream, GENERATION as DREAM_GENERATION, CHAT as DREAM_CHAT
import test_preparations as prep_t
from test_preparations import CHAT, _Case, engine


def suggestion(goal='평일 아침 일정 요약', due=None, sources=('t1', 't2'), kind='prepare', recurrence='weekdays',
               delivery='send', title='아침 일정 요약', offer='평일 아침 7시에 그날 일정을 정리해 둘게요.'):
    due = due or (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(timespec='seconds')
    return {'kind': kind, 'goal': goal, 'due': due, 'recurrence': recurrence, 'delivery': delivery, 'title': title,
            'offer': offer, 'sources': list(sources)}


class Gates(unittest.TestCase):
    """``om.validate_suggestions``: grounding and budget, with interest judged by the AI."""
    refs = {'t1': 'a', 't2': 'b', 't3': 'c'}
    days = {'t1': '2026-10-30', 't2': '2026-10-31', 't3': '2026-10-31'}

    def reasons(self, *items):
        kept, dropped = om.validate_suggestions(list(items), self.refs, self.days)
        return [item['index'] for item in kept], [item['reason'] for item in dropped]

    def test_two_turns_of_different_days_are_kept(self):
        self.assertEqual(self.reasons(suggestion(sources=('t1', 't3'))), ([0], []))

    def test_one_turn_is_enough_but_an_unread_or_empty_source_is_dropped(self):
        self.assertEqual(self.reasons(suggestion(sources=('t1',)), suggestion(sources=('t1', 't9')), suggestion(sources=())),
                         ([0], ['sources', 'sources']))

    def test_two_turns_of_the_same_day_or_undated_are_kept(self):
        """#717: an interest shown on one busy day counts; two different dates are no longer required."""
        self.assertEqual(self.reasons(suggestion(sources=('t2', 't3'))), ([0], []))
        kept, dropped = om.validate_suggestions([suggestion()], self.refs, {'t1': '2026-10-30'})
        self.assertEqual(([item['index'] for item in kept], dropped), ([0], []))

    def test_at_most_the_limit_is_kept(self):
        self.assertEqual(om.DREAM_SUGGESTIONS, 3)
        self.assertEqual(self.reasons(*[suggestion(sources=('t1', 't2'))] * 4), ([0, 1, 2], ['limit']))

    def test_a_one_off_rests_on_one_cited_turn(self):
        once = dict(recurrence=om.SUGGESTION_ONCE)
        self.assertEqual(self.reasons(suggestion(sources=('t1',), **once), suggestion(sources=('t9',), **once),
                                      suggestion(sources=(), **once)),
                         ([0], ['sources', 'sources']))

    def test_the_limit_counts_both_kinds(self):
        self.assertEqual(self.reasons(suggestion(sources=('t1', 't2')), suggestion(sources=('t3',), recurrence='once'),
                                      suggestion(sources=('t1', 't3')), suggestion(sources=('t3',), recurrence='once')),
                         ([0, 1, 2], ['limit']))

    def test_the_schema_requires_every_field_and_the_shape_accepts_none(self):
        item = om.DREAM_SCHEMA['properties']['suggestions']['items']
        self.assertEqual(set(item['required']), set(item['properties']))
        self.assertIn('suggestions', om.DREAM_SCHEMA['required'])
        self.assertTrue(om.dream_shape(dream()), 'an answer without suggestions is none')
        self.assertFalse(om.dream_shape({**dream(), 'suggestions': 'x'}))


class LookBack(Dream):
    """The dream run stores what passes as a ``scheduled`` row (#717) and records it in its Evidence, no text."""

    def setUp(self):
        super().setUp()
        self.store.put('telegram', {'enabled': True, 'user_id': DREAM_CHAT, 'generation': DREAM_GENERATION})

    def day(self, *texts):
        return [self.settled(text, telegram=True) for text in texts]

    def two_days(self):
        first, second, newest = self.day('오늘 일정 정리해줘', '오늘 일정 다시 정리해줘', '다른 질문')
        self.a_day_earlier(first)
        return first, second, newest

    def rows(self):
        return self.service.preparations.rows()

    def detail(self):
        [(_status, detail)] = self.evidence(self.dream_rows()[0]['job_id'])
        return detail

    def test_a_request_asked_on_two_days_is_scheduled_on_the_anchor(self):
        _first, _second, newest = self.two_days()
        self.dreams = [{**dream(), 'suggestions': [suggestion()]}]
        self.assertTrue(self.tick())
        [row] = self.rows()
        self.assertEqual((row['state'], row['origin'], row['created_from'], row['accepted_by'], row['offered_at']),
                         (prep.STATE_SCHEDULED, prep.ORIGIN_DREAM, newest, prep.ACCEPTED_DREAM, None))
        self.assertEqual((row['kind'], row['recurrence'], row['title_text']), ('prepare', 'RRULE:FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR;COUNT=30', '아침 일정 요약'))
        [kept] = self.detail()['suggestions']['kept']
        self.assertEqual((kept['preparation_id'], kept['sources']), (row['id'], ['t1', 't2']))
        self.assertNotIn('일정', json.dumps(self.detail(), ensure_ascii=False), 'Evidence keeps no text')
        [(context, _question, schema)] = self.asked
        self.assertEqual(context.facts['preparations'], 'none')
        self.assertIn('suggestions', schema['properties'])
        self.assertEqual(self.service.work_waits_for_owner(self.store.job(newest)), (),
                         'the anchor Work itself does not wait for the suggestion')

    def test_one_turn_can_ground_a_standing_interest(self):
        self.day('이 주제는 계속 관심 있어', '다른 질문', '또 다른 질문')
        self.dreams = [{**dream(), 'suggestions': [suggestion(sources=('t1',))]}]
        self.tick()
        [row] = self.rows()
        self.assertEqual((row['state'], row['accepted_by']), (prep.STATE_SCHEDULED, prep.ACCEPTED_DREAM))
        self.assertEqual(self.detail()['suggestions']['dropped'], [])

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

    def test_a_dated_need_mentioned_once_becomes_a_scheduled_one_off(self):
        _mention, _other, newest = self.day('다음 주 화요일까지 서류 내야 해', '다른 질문', '또 다른 질문')
        self.dreams = [{**dream(), 'suggestions': [suggestion(goal='화요일 서류 제출', kind='reminder', recurrence='once',
                                                               sources=('t1',)),
                                                    suggestion(goal='매일 일정', sources=('t1',))]}]
        self.tick()
        row = next(row for row in self.rows() if row['recurrence'] is None)
        self.assertEqual((row['state'], row['accepted_by'], row['origin'], row['created_from'], row['kind'], row['recurrence']),
                         (prep.STATE_SCHEDULED, prep.ACCEPTED_DREAM, prep.ORIGIN_DREAM, newest, 'reminder', None))
        self.assertEqual(len(self.rows()), 2, 'the AI may also judge a standing interest from one turn')
        self.assertEqual(self.detail()['suggestions']['dropped'], [])

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
    """#717: the owner is told once, through each row's scheduled message and cancel button.

    A row still ``proposed`` from before #717 (``legacy``) keeps the old proposal message and buttons.
    """

    def setUp(self):
        super().setUp()
        self.store.put(TIMEZONE_CONFIG_KEY, 'Asia/Seoul')

    def suggest(self, anchor, **fields):
        outcomes, calls = self.service.suggest_preparations(anchor, [suggestion(due=self.due_iso(86400), **fields)])
        self.assertEqual(calls, 0)
        return self.service.preparations.get(outcomes[0]['preparation_id'])

    def legacy(self, anchor, seconds=86400, recurrence='weekdays', goal='평일 아침 일정 요약', kind='prepare'):
        """A look-back suggestion stored ``proposed`` before #717."""
        return self.service.preparations.create(
            kind=kind, goal=goal, due_at=self.now + seconds, timezone='Asia/Seoul', recurrence=recurrence,
            channel=self.service.preparation_channel(self.store.job(anchor), kind, 'send'), created_from=anchor,
            state=prep.STATE_PROPOSED, title='아침 일정 요약', offer='평일 아침 7시에 그날 일정을 정리해 둘게요.',
            origin=prep.ORIGIN_DREAM)

    def deliver(self):
        self.service.deliver_one()
        with mock.patch('time.time', return_value=self.now):
            self.service.deliver_notification()

    def notification(self, work, kind='preparation_scheduled'):
        with self.store.db() as db:
            row = db.execute('SELECT * FROM telegram_notifications WHERE (job_id=? OR job_id LIKE ?) AND kind=?',
                             (work, work + '/dream:%', kind)).fetchone()
        return dict(row) if row else None

    def row_notification(self, row):
        return self.store.notification_of(f"{row['created_from']}/dream:{row['id']}", 'preparation_scheduled')

    def proposal_sends(self):
        """Every message that tells or offers a preparation (cancel or accept/decline buttons)."""
        return [body for body in self.sends() if 'p7p:' in json.dumps(body.get('reply_markup') or {})]

    def cancel_button(self, message):
        [[button]] = message['reply_markup']['inline_keyboard']
        self.assertTrue(button['callback_data'].endswith(':cancel'), button)
        return button['callback_data']

    def test_told_once_after_the_next_own_work_with_a_cancel_button_and_it_runs(self):
        anchor = self.receive('첫 질문')
        self.deliver()
        row = self.suggest(anchor)
        self.assertEqual((row['state'], row['accepted_by']), (prep.STATE_SCHEDULED, prep.ACCEPTED_DREAM))
        self.now += 1800
        self.assertFalse(self.tick(), 'not due yet, still within waking hours')
        self.receive('다음 질문')
        self.deliver()
        [message] = self.proposal_sends()
        self.assertIn('아침 일정 요약', message['text'])
        self.cancel_button(message)
        notification = self.notification(anchor)
        self.assertEqual((notification['chat_id'], notification['state']), (CHAT, 'sent'))
        self.assertIsNone(self.notification(anchor, 'preparation_proposed'), 'told, not asked')
        self.tick()  # observe the completed send
        self.assertIsNotNone(self.service.preparations.get(row['id'])['offered_at'])
        self.receive('또 다른 질문')
        self.deliver()
        self.assertEqual(len(self.proposal_sends()), 1, 'told once')
        self.now = row['due_at'] + 1
        self.assertTrue(self.tick(), 'it runs without an acceptance')
        self.assertEqual(len(self.runs(row['id'])), 1)

    def test_cancelling_it_is_a_decline(self):
        anchor = self.receive('첫 질문')
        row = self.suggest(anchor)
        self.receive('다음 질문')
        self.deliver()
        [message] = self.proposal_sends()
        self.tap(self.cancel_button(message), self.notification(anchor)['message_id'])
        self.assertEqual(self.service.preparations.get(row['id'])['state'], 'cancelled')
        self.assertEqual(self.service.preparations.declined_goals(self.now), ['평일 아침 일정 요약'])
        self.now = row['due_at'] + 1
        self.assertFalse(self.tick())
        self.now += 86400
        self.assertEqual(self.service.suggest_preparations(self.receive('셋째 질문'), [suggestion(due=self.due_iso(86400))]),
                         ([{'reason': 'declined_before'}], 0))

    def test_all_rows_are_queued_together_once_each_with_an_independent_cancel(self):
        anchor = self.receive('첫 질문')
        self.deliver()
        items = [suggestion(goal=f'준비 {n}', title=f'준비 {n}', due=self.due_iso(86400))
                 for n in range(om.DREAM_SUGGESTIONS)]
        outcomes, calls = self.service.suggest_preparations(anchor, items)
        rows = [self.service.preparations.get(outcome['preparation_id']) for outcome in outcomes]
        self.assertEqual(calls, 0)
        self.tick()
        self.assertTrue(all(self.row_notification(row)['state'] == 'queued' for row in rows))
        for _ in rows:
            self.deliver()
        messages = self.proposal_sends()
        self.assertEqual(len(messages), len(rows))
        for row in rows:
            notice = self.row_notification(row)
            self.assertEqual(notice['fingerprint'], 'ids:' + row['id'])
            self.assertEqual(notice['created'], self.now)
            message = next(m for m in messages if f"p7p:{notice['id']}:cancel" == self.cancel_button(m))
            self.assertIn(row['title_text'], message['text'])
        first = rows[0]
        notice = self.row_notification(first)
        self.tap(f"p7p:{notice['id']}:cancel", notice['message_id'])
        self.assertEqual(self.service.preparations.get(first['id'])['state'], prep.STATE_CANCELLED)
        self.assertTrue(all(self.service.preparations.get(row['id'])['state'] == prep.STATE_SCHEDULED for row in rows[1:]))
        self.tick()
        self.receive('다음 질문')
        self.deliver()
        self.assertEqual(len(self.proposal_sends()), len(rows), 'no second notice')

    def test_all_anchors_are_told_and_later_rows_need_not_wait_a_day(self):
        first = self.receive('첫 질문')
        second = self.receive('둘째 질문')
        self.suggest(first)
        self.suggest(second, goal='주간 보고 정리', recurrence='weekly')
        self.tick()
        self.assertIsNotNone(self.notification(first))
        self.assertIsNotNone(self.notification(second))
        third = self.receive('셋째 질문')
        row = self.suggest(third, goal='나중 준비')
        self.tick()
        self.assertIsNotNone(self.row_notification(row), 'another batch on the same day is told too')

    def test_first_due_waits_for_its_notice_in_waking_hours_including_after_restart(self):
        anchor = self.receive('첫 질문')
        self.deliver()
        self.now = prep_t.SEOUL_MIDNIGHT + 3 * 3600
        outcomes, _ = self.service.suggest_preparations(anchor, [suggestion(
            goal='밤에 마련한 준비', kind='reminder', recurrence='once', due=self.due_iso(60))])
        row = self.service.preparations.get(outcomes[0]['preparation_id'])
        self.now += 61
        self.assertFalse(self.tick())
        current = self.service.preparations.get(row['id'])
        self.assertEqual((current['state'], current['run_count'], current['due_at']),
                         (prep.STATE_SCHEDULED, 0, row['due_at']))
        self.assertIsNone(self.row_notification(row))
        self.service = self.make_service()
        self.assertFalse(self.tick(), 'restart does not bypass the notice')
        self.now = prep_t.SEOUL_MIDNIGHT + 8 * 3600
        original = self.service.preparations.start
        observed = []
        def start(*args, **kwargs):
            observed.append(self.row_notification(row))
            return original(*args, **kwargs)
        self.service.preparations.start = start
        self.assertFalse(self.tick())
        self.deliver()
        self.assertFalse(self.tick())
        self.assertEqual(observed, [])
        self.assertEqual(self.runs(row['id']), [])
        self.assertEqual(self.service.preparations.get(row['id'])['state'], prep.STATE_EXPIRED)

    def test_queue_failure_keeps_first_run_pending_and_retry_is_idempotent(self):
        anchor = self.receive('첫 질문')
        self.deliver()
        row = self.suggest(anchor, kind='reminder', recurrence='once')
        self.now = row['due_at'] + 1
        original = self.store.telegram.queue_notification
        self.store.telegram.queue_notification = lambda *args, **kwargs: None
        self.assertFalse(self.tick())
        self.assertIsNone(self.service.preparations.get(row['id'])['offered_at'])
        self.assertEqual(self.runs(row['id']), [])
        self.store.telegram.queue_notification = original
        self.assertFalse(self.tick())
        first = self.row_notification(row)
        self.service = self.make_service()  # queued, but no delivery receipt yet
        self.assertFalse(self.tick())
        self.assertEqual(self.row_notification(row)['id'], first['id'])
        self.deliver()
        self.tick()
        self.assertEqual(self.service.preparations.get(row['id'])['state'], prep.STATE_EXPIRED)
        self.assertEqual(self.runs(row['id']), [])
        self.assertEqual(self.row_notification(row)['state'], 'sent')
        self.assertEqual(len(self.proposal_sends()), 1)

    def test_paused_dream_rows_do_not_hide_owner_or_day_plan_rows_and_resume(self):
        anchor = self.receive('첫 질문')
        rows = [self.suggest(anchor, goal=f'꿈 준비 {n}') for n in range(prep.MAX_ACTIVE_DREAM)]
        self.tick()
        for _ in rows:
            self.deliver()
        self.tick()  # observe delivered notices before pausing
        self.service.owner_model.set_controls({'dream': False})
        self.now = max(row['due_at'] for row in rows) + 1
        owner = self.scheduled(seconds=-1, kind='reminder')
        plan = self.service.preparations.create(kind='reminder', goal='계획이 마련한 준비', due_at=self.now - 1,
            timezone='Asia/Seoul', recurrence=None, channel='web', created_from=anchor,
            state=prep.STATE_SCHEDULED, accepted_by=prep.ACCEPTED_DAY_PLAN, origin=prep.ORIGIN_DAY_PLAN)
        self.tick()
        for row in rows:
            current = self.service.preparations.get(row['id'])
            self.assertEqual((current['state'], current['run_count'], current['due_at']),
                             (prep.STATE_SCHEDULED, 0, row['due_at']))
        self.assertEqual(len(self.runs(owner['id'])), 1)
        self.assertEqual(len(self.runs(plan['id'])), 1)
        self.service.owner_model.set_controls({'dream': True})
        self.tick()
        self.tick()  # keep the ordinary per-tick budget
        self.assertTrue(all(not self.runs(row['id']) for row in rows))
        self.assertTrue(all(self.service.preparations.get(row['id'])['due_at'] > self.now for row in rows))
        self.now = max(self.service.preparations.get(row['id'])['due_at'] for row in rows)
        self.tick()
        self.tick()
        self.assertTrue(all(len(self.runs(row['id'])) == 1 for row in rows))

    def test_dream_budget_leaves_room_for_an_owner_schedule(self):
        anchor = self.receive('첫 질문')
        for n in range(prep.MAX_ACTIVE_DREAM):
            self.suggest(anchor, goal=f'꿈 준비 {n}')
        outcomes, _ = self.service.suggest_preparations(anchor, [suggestion(goal='초과 준비', due=self.due_iso(86400))])
        self.assertEqual(outcomes, [{'reason': 'limit'}])
        self.script = [{'content': None, 'tool_calls': [prep_t.call('schedule', 'schedule_preparation',
            kind='reminder', goal='소유자가 요청한 준비', due=self.due_iso(600))]}, {'content': '예약했어요.'}]
        owner_work = self.receive('10분 뒤 알려 줘')
        [owner] = [row for row in self.service.preparations.rows() if row['created_from'] == owner_work]
        self.assertEqual((owner['state'], owner['accepted_by']), (prep.STATE_SCHEDULED, prep.ACCEPTED_OWNER_REQUEST))
        self.assertLess(prep.MAX_ACTIVE_DREAM, prep.MAX_ACTIVE)

    def test_standing_dream_is_finite_and_owner_can_extend_it(self):
        anchor = self.receive('첫 질문')
        row = self.suggest(anchor, kind='reminder', recurrence='daily')
        self.assertEqual(row['max_runs'], prep.DREAM_MAX_RUNS)
        self.assertEqual(prep.rule_parts(row['recurrence'])['COUNT'], str(prep.DREAM_MAX_RUNS))
        self.tick()  # queue its notice once
        for _ in range(prep.DREAM_MAX_RUNS):
            row = self.service.preparations.get(row['id'])
            self.now = row['due_at'] + 1
            work = self.service.preparations.start(row, channel='web', chat_id=None, now=self.now)
            with self.store.db() as db:
                db.execute("UPDATE jobs SET status='succeeded',delivery='none',response='done' WHERE id=?", (work,))
            self.service.preparations.settle(self.service.preparations.get(row['id']), self.now)
        current = self.service.preparations.get(row['id'])
        self.assertEqual((current['state'], current['run_count']), (prep.STATE_DELIVERED, prep.DREAM_MAX_RUNS))
        self.now += 86400
        self.tick()
        self.assertEqual(len(self.runs(row['id'])), prep.DREAM_MAX_RUNS)
        other = self.suggest(self.receive('새 질문'), goal='연장할 준비', recurrence='weekly')
        extended = self.service.preparations.edit(other['id'], rrule='FREQ=WEEKLY;COUNT=60')
        self.assertEqual((extended['recurrence'], extended['max_runs']), ('RRULE:FREQ=WEEKLY;COUNT=60', 60))

    def test_a_legacy_proposal_is_offered_once_and_accepted_with_its_button(self):
        anchor = self.receive('첫 질문')
        self.deliver()
        row = self.legacy(anchor)
        self.now += 3600
        self.assertFalse(self.tick(), 'a proposal never runs before acceptance')
        self.receive('다음 질문')
        self.deliver()
        [message] = self.proposal_sends()
        self.assertIn('아침 일정 요약', message['text'])
        notification = self.notification(anchor, 'preparation_proposed')
        self.assertEqual((notification['chat_id'], notification['state']), (CHAT, 'sent'))
        self.tick()  # observe the completed send
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

    def test_declining_a_legacy_proposal_is_a_decline(self):
        anchor = self.receive('첫 질문')
        row = self.legacy(anchor)
        self.receive('다음 질문')
        self.deliver()
        notification = self.notification(anchor, 'preparation_proposed')
        self.tap(f"p7p:{notification['id']}:deny", notification['message_id'])
        self.assertEqual(self.service.preparations.get(row['id'])['state'], 'cancelled')
        self.assertEqual(self.service.preparations.declined_goals(self.now), ['평일 아침 일정 요약'])

    def test_a_first_slot_that_passed_before_the_offer_moves_to_the_next_one(self):
        anchor = self.receive('첫 질문')
        row = self.legacy(anchor, seconds=3600, recurrence='daily')
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

    def test_an_unanswered_legacy_suggestion_expires_after_a_week_and_is_not_a_decline(self):
        anchor = self.receive('첫 질문')
        row = self.legacy(anchor)
        scheduled = self.suggest(self.receive('둘째 질문'), goal='주간 보고 정리', recurrence='weekly')
        self.receive('다음 질문')
        self.deliver()
        notification = self.notification(anchor, 'preparation_proposed')
        self.now += prep.SUGGESTION_EXPIRY_SECONDS - 60
        self.tick()
        self.assertEqual(self.service.preparations.get(row['id'])['state'], 'proposed')
        self.now += 120
        self.tick()
        self.assertEqual(self.service.preparations.get(row['id'])['state'], prep.STATE_EXPIRED)
        self.assertEqual(self.runs(row['id']), [])
        self.assertNotEqual(self.service.preparations.get(scheduled['id'])['state'], prep.STATE_EXPIRED,
                            'what the look back scheduled does not lapse')
        self.assertEqual(self.service.preparations.declined_goals(self.now), [], 'a lapse is not a decline')
        self.tap(f"p7p:{notification['id']}:accept", notification['message_id'])
        self.assertEqual(self.service.preparations.get(row['id'])['state'], prep.STATE_EXPIRED, 'too late to accept')
        again = self.suggest(self.receive('한 주 뒤 질문'))
        self.assertEqual(again['state'], prep.STATE_SCHEDULED, 'it may be set up again')


class OneOff(Offer):
    """#638: a look back holding a one-off is told on the tick, in the owner's daytime, without a new Work."""

    def setUp(self):
        super().setUp()
        # Offer hours read the owner's zone, not the host's.
        self.store.put(TIMEZONE_CONFIG_KEY, 'Asia/Seoul')

    def one_off(self, anchor, seconds=2 * 86400):
        outcomes, _calls = self.service.suggest_preparations(anchor, [suggestion(
            goal='화요일 서류 제출', kind='reminder', recurrence=om.SUGGESTION_ONCE, sources=('t1',),
            due=self.due_iso(seconds))])
        return self.service.preparations.get(outcomes[0]['preparation_id'])

    def test_told_on_the_tick_without_a_new_work_and_runs_once(self):
        anchor = self.receive('다음 주 화요일까지 서류 내야 해')
        self.deliver()
        row = self.one_off(anchor)
        self.assertEqual((row['state'], row['accepted_by'], row['recurrence']),
                         (prep.STATE_SCHEDULED, prep.ACCEPTED_DREAM, None))
        self.assertFalse(self.tick(), 'telling is not a run')
        self.deliver()
        [message] = self.proposal_sends()
        self.assertIn('화요일 서류 제출', message['text'])
        self.cancel_button(message)
        notification = self.notification(anchor)
        self.assertEqual((notification['chat_id'], notification['state']), (CHAT, 'sent'))
        self.tick()
        self.deliver()
        self.assertEqual(len(self.proposal_sends()), 1, 'told once')
        self.now = row['due_at'] + 1
        self.assertTrue(self.tick())
        self.assertEqual(len(self.runs(row['id'])), 1)
        self.now += 86400
        self.tick()
        self.assertEqual(len(self.runs(row['id'])), 1, 'a one-off runs once')

    def test_a_standing_suggestion_is_told_on_the_tick_without_a_new_work(self):
        anchor = self.receive('첫 질문')
        self.deliver()
        self.suggest(anchor)
        self.tick()
        self.deliver()
        self.assertEqual(len(self.proposal_sends()), 1)
        self.assertIsNotNone(self.notification(anchor))

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

    def test_the_pause_defers_notices_until_resume(self):
        anchor = self.receive('첫 질문')
        self.deliver()
        self.suggest(anchor)
        self.one_off(anchor)
        self.service.owner_model.set_controls({'enabled': False})
        self.tick()
        self.assertIsNone(self.notification(anchor))
        self.service.owner_model.set_controls({'enabled': True})
        self.tick()
        with self.store.db() as db:
            notices = db.execute("SELECT * FROM telegram_notifications WHERE kind='preparation_scheduled'").fetchall()
        self.assertEqual(len(notices), 2)

    def test_a_legacy_one_off_whose_time_passed_lapses_and_is_not_a_decline(self):
        anchor = self.receive('다음 주 화요일까지 서류 내야 해')
        self.deliver()
        self.service.owner_model.set_controls({'enabled': False})
        row = self.legacy(anchor, seconds=600, recurrence=None, goal='화요일 서류 제출', kind='reminder')
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
