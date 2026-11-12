"""A decisive preparation detail survives a service restart on the ordinary Work path."""
import json

from personal_agent import preparations as prep
from personal_agent.conversation_handoff import FOLLOWUP_REFERENCE
from personal_agent.decision import (
    OUTCOME_DECIDED, BinaryDecision, FixtureDecisionEngine, SelectionDecision, fixture_confidence,
)
from personal_agent.quickstart_store import QuickStore

from test_preparations import _Case, call


class PreparationContinuationTests(_Case):
    def test_owner_can_answer_the_stored_question_after_restart(self):
        request = '정기 현황 보고를 예약해 줘. 시작 시각은 꼭 나한테 물어봐.'
        question = '몇 시에 시작할까요?'
        answer = '매일 아홉 시요.'
        self.script = [{'content': question}] * 2
        original = self.receive(request)
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.store.last_reply(original)['content'], question)

        def choose(context, candidates, _question):
            if context.purpose == 'conversation-followup':
                self.assertEqual(context.facts['previous_owner_message'], request)
                self.assertEqual(context.facts['previous_reply'], '몇 시에 시작할까요?')
                return SelectionDecision(OUTCOME_DECIDED, FOLLOWUP_REFERENCE, candidates, fixture_confidence())
            return None

        def judge(context, proposition):
            if context.purpose in ('goal-reached', 'explicit-preparation-request'):
                return BinaryDecision(OUTCOME_DECIDED, True, fixture_confidence())
            return None

        self.judge = FixtureDecisionEngine(choose=choose, judge=judge)
        self.store = QuickStore(self.root)
        self.service = self.make_service()

        expected_due = self.now + 43200 - 30

        def schedule_from_answer(body):
            messages = json.dumps(body['messages'], ensure_ascii=False)
            self.assertIn('정기 현황 보고를 예약해 줘', messages)
            self.assertIn(answer, messages)
            return {'content': None, 'tool_calls': [
                call('schedule', 'schedule_preparation', kind='prepare', goal='정기 현황 보고',
                     due=self.due_iso(43200 - 30), recurrence='daily'),
            ]}

        self.script = [schedule_from_answer, {'content': '매일 보고하도록 예약했어요.'}]
        followup = self.receive(answer)
        self.assertEqual(self.store.job(followup)['related_job_id'], original)
        [row] = self.rows()
        self.assertEqual((row['created_from'], row['state'], row['recurrence']),
                         (followup, prep.STATE_SCHEDULED, 'daily'))
        self.assertEqual(row['due_at'], expected_due)


class DayPlanPreparationStateTests(_Case):
    def test_day_plan_reads_connection_wait_and_expiry_from_current_state(self):
        scheduled = self.service.preparations.create(
            kind=prep.KIND_PREPARE, goal='공개 기록 준비', due_at=self.now+60, timezone='Asia/Seoul',
            recurrence=None, channel='web', created_from='day-plan',
            state=prep.STATE_SCHEDULED, accepted_by=prep.ACCEPTED_DAY_PLAN,
        )
        self.now += 60
        self.assertTrue(self.tick())
        [work] = self.runs(scheduled['id'])

        def observe():
            record = self.service.preparations_service.day_plan_record({})
            [item] = json.loads(record.removeprefix('earlier_plans: '))
            return item

        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='awaiting_connection' WHERE id=?", (work['id'],))
        self.assertEqual((observe()['state'], observe()['reason']), ('blocked', 'awaiting_connection'))

        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='queued' WHERE id=?", (work['id'],))
        self.assertEqual(observe()['state'], 'running')
        self.assertNotIn('reason', observe())

        with self.store.db() as db:
            db.execute("UPDATE preparations SET state='expired' WHERE id=?", (scheduled['id'],))
        self.assertEqual((observe()['state'], observe()['reason']), ('expired', 'expired'))


class EarlyClosingTests(_Case):
    def test_terminal_observation_closes_this_window_and_preserves_the_daily_rhythm(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        zone = ZoneInfo('Asia/Seoul')
        first = datetime.fromtimestamp(self.now + 60, zone)
        end = datetime.fromtimestamp(first.timestamp() + 1800, zone)
        row = self.service.preparations.create(
            kind=prep.KIND_PREPARE, goal='상태 확정까지 확인', due_at=first.timestamp(),
            timezone=zone.key, recurrence='daily', channel='telegram', created_from='settings',
            state=prep.STATE_SCHEDULED, accepted_by=prep.ACCEPTED_OWNER_SETTINGS,
            window=(600, end.timestamp(), 4), delivery_mode=prep.DELIVERY_WHEN_NEEDED,
        )

        observed = []
        result = '공개 기록에서 상태가 최종 확정됐습니다.'

        def judge(context, proposition):
            if context.purpose == 'watch-window-closed':
                observed.append((context.facts['watch_goal'], context.facts['run_result']))
                self.assertIn('explicitly observed final state', proposition)
                return BinaryDecision(OUTCOME_DECIDED, True, fixture_confidence())
            if context.purpose == 'goal-reached':
                return BinaryDecision(OUTCOME_DECIDED, True, fixture_confidence())
            return None

        self.service.use_decision_engine(FixtureDecisionEngine(judge=judge))
        self.now = first.timestamp() + 1
        self.assertTrue(self.tick())
        self.assertEqual(len(self.runs(row['id'])), 1)
        self.script = [{'content': result}] * 2
        self.assertTrue(self.service.run_one())
        self.assertTrue(self.tick())
        updated = self.service.preparations.get(row['id'])
        self.assertEqual(observed, [(row['goal_text'], result)])
        self.assertEqual(updated['last_decision_reason'], prep.REASON_WINDOW_CLOSED)
        self.assertEqual(updated['due_at'], first.timestamp() + 86400)
        self.assertEqual(updated['run_count'], 0)
        self.service.deliver_notification()
        self.service.deliver_notification()
        self.assertEqual(sum(result in text for text in self.texts()), 1)
