"""SEC-ATTN-01 (#659): owner-accepted preparations - reminders and prepared answers.

Evidence class: model-free, fixture transports.  A scripted compatible-API
model, a counting public network, a recording Telegram transport and a
FixtureDecisionEngine replace only the provider and the wires; the real
QuickStore, ``AgentService`` (Telegram ingress, ``run_one``, ``deliver_one``,
``deliver_notification``, the callback handler, restart recovery),
``run_agent`` and ``Capabilities`` run unchanged.  The preparations clock is
a fake clock.  No live model, Telegram, calendar or search provider is
contacted, and none is claimed.
"""
import json
import logging
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from personal_agent import preparations as prep
from personal_agent.messages import msg
from personal_agent.agent_runtime import (PROFILE_HEADING, Capabilities, ToolError, action_definitions, recorded_arguments,
                                          turn_context, render_turn_prompt)
from personal_agent.conversation_handoff import ConversationJudgments, PREPARATION_REQUEST_PROPOSITION, TelegramNotSent
from personal_agent.decision import OUTCOME_DECIDED, BinaryDecision, FixtureDecisionEngine, fixture_confidence
from personal_agent.memory_service import MemoryService
from personal_agent.providers import ModelAdapter, ProviderError
from personal_agent.quickstart_service import AgentService, PREPARATION_CONVERSATION_CHARS, PREPARATION_CONVERSATION_TURNS
from personal_agent.quickstart_store import QuickStore

CHAT, GENERATION = 77, 'gen-1'
SECRET = 'bot-token-value-1234-abcdef'
SEOUL = ZoneInfo('Asia/Seoul')
#: #9: one clock for preparations and the turn's current context, 2026-10-14 21:00:30 Asia/Seoul.
START = 1_791_979_230.0
#: 2026-10-15 00:00:00 Asia/Seoul.
SEOUL_MIDNIGHT = 1_791_990_000.0


def call(ident, name, **args):
    return {'id': ident, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args, ensure_ascii=False)}}


def finish(ident, *refs, summary='끝났습니다.', status='done'):
    return {'content': None, 'tool_calls': [call(ident, 'finish', status=status, evidence_refs=list(refs), summary=summary)]}


def engine(preparation=True, goal=True):
    """Answers the two judgments this feature relies on; everything else is unavailable."""
    def judge(context, proposition):
        if context.purpose == 'goal-reached':
            return BinaryDecision(OUTCOME_DECIDED, goal, fixture_confidence())
        if context.purpose == 'explicit-preparation-request' and preparation is not None:
            return BinaryDecision(OUTCOME_DECIDED, preparation, fixture_confidence())
        return None
    return FixtureDecisionEngine(judge=judge)


class Network:
    """The public wire; counts every outbound plan."""

    def __init__(self):
        self.plans = []

    def execute(self, plan):
        self.plans.append(dict(plan))
        return {'tool': plan['tool'], 'query': plan.get('query'), 'retrieved_at': 1,
                'sources': ['https://lunch.example/list'],
                'results': [{'title': '합정 식당 목록', 'url': 'https://lunch.example/list', 'snippet': '국수, 덮밥, 샐러드'}]}


class _Case(unittest.TestCase):
    START = START

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / 'data'
        self.store = QuickStore(self.root)
        self.now = self.START
        self.telegram, self.model_calls, self.script = [], [], []
        self.network = Network()
        self.service = self.make_service()
        self.service.save_model({'provider': 'compatible', 'endpoint': 'https://example.test/v1', 'model': 'test-model', 'api_key': 'k'})
        self.assertTrue(self.service.test_model()['ok'])
        self.store.secret('telegram_token', SECRET)
        self.store.put('telegram', {'enabled': True, 'user_id': CHAT, 'generation': GENERATION, 'cursor': 0})
        self.service.context_observations.set_controls({'timezone': 'Asia/Seoul'})
        self.judge = engine()
        self.service.use_decision_engine(self.judge)
        self.model_calls.clear()
        self.telegram.clear()
        self.update_id, self.message_id = 100, 500

    # --- fakes --------------------------------------------------------------

    def make_service(self):
        """A service over the same store: a second one is a restart."""
        service = AgentService(self.store, ModelAdapter(self._model), self._telegram)
        service.local_tools = self.network
        service.preparations.clock = lambda: self.now
        service.context_observations.clock = lambda: self.now
        if hasattr(self, 'judge'):
            service.use_decision_engine(self.judge)
        return service

    def _model(self, url, body, headers=None, timeout=60):
        names = [tool.get('function', {}).get('name') for tool in body.get('tools', [])]
        if 'agentos_connection_probe' in names:
            return {'choices': [{'message': {'tool_calls': [{'id': 'p', 'function': {'name': 'agentos_connection_probe', 'arguments': '{}'}}]}}]}
        self.model_calls.append(json.loads(json.dumps(body)))
        step = self.script.pop(0) if self.script else {'content': '끝났습니다.'}
        message = step(body) if callable(step) else step
        return {'choices': [{'message': message}]}

    def _telegram(self, url, body=None, headers=None, timeout=60):
        method = url.rsplit('/', 1)[-1]
        self.telegram.append((method, body))
        if method == 'sendMessage':
            return {'ok': True, 'result': {'message_id': 9000 + len(self.telegram)}}
        return {'ok': True, 'result': True}

    # --- helpers ------------------------------------------------------------

    def due_iso(self, seconds):
        return datetime.fromtimestamp(self.now + seconds, SEOUL).isoformat(timespec='seconds')

    def receive(self, text):
        """The owner's Telegram message, run through the ordinary worker."""
        self.update_id += 1
        self.message_id += 1
        self.service.ingest_update({'update_id': self.update_id, 'message': {
            'message_id': self.message_id, 'from': {'id': CHAT}, 'chat': {'id': CHAT, 'type': 'private'},
            'text': text, 'date': int(self.now)}}, GENERATION)
        job_id = self.store.jobs()[0]['id']
        self.assertTrue(self.service.run_one())
        return job_id

    def web_turn(self, text):
        self.message_id += 1
        job_id = self.store.enqueue(text, f'web-{self.message_id}')
        self.assertTrue(self.service.run_one())
        return job_id

    def tap(self, data, message_id):
        self.service.ingest_callback({'id': 'cb', 'from': {'id': CHAT}, 'data': data,
                                      'message': {'message_id': message_id, 'chat': {'id': CHAT, 'type': 'private'}}},
                                     GENERATION)

    def sends(self):
        return [body for method, body in self.telegram if method == 'sendMessage']

    def texts(self):
        return [str(body.get('text')) for body in self.sends()]

    def rows(self):
        return self.service.preparations.rows()

    def runs(self, preparation_id):
        with self.store.db() as db:
            return [dict(row) for row in db.execute('SELECT * FROM jobs WHERE request_key LIKE ? ORDER BY created',
                                                    (prep.REQUEST_KEY_PREFIX + preparation_id + ':%',))]

    def scheduled(self, kind='prepare', goal='점심 후보 3곳 준비해 줘', seconds=60, recurrence=None, channel='web'):
        """A row the owner already accepted in Settings."""
        return self.service.preparations.create(kind=kind, goal=goal, due_at=self.now + seconds, timezone='Asia/Seoul',
                                                recurrence=recurrence, channel=channel, created_from='settings',
                                                state=prep.STATE_SCHEDULED, accepted_by=prep.ACCEPTED_OWNER_SETTINGS)

    def tick(self, service=None):
        return (service or self.service).run_due_preparation()


class PreparationRunFactTests(_Case):
    def test_window_runs_use_the_recorded_count_next_local_slot_end_and_delivery(self):
        from personal_agent.agent_runtime import WorkBudget
        start = datetime(2026, 11, 5, 12, tzinfo=SEOUL).timestamp()
        end = datetime(2026, 11, 5, 18, tzinfo=SEOUL).timestamp()
        row = self.service.preparations.create(kind='prepare', goal='정기 확인', due_at=start,
            timezone='Asia/Seoul', recurrence=None, channel='telegram', created_from='settings',
            state=prep.STATE_SCHEDULED, window=(3600, end, 6))
        # Start and settle the real rows: start must not be mistaken for advancing due_at.
        for number in range(1, 7):
            self.now = start + (number - 1) * 3600
            current = self.service.preparations.get(row['id'])
            work = self.service.preparations.start(current, channel='telegram', chat_id=CHAT, now=self.now)
            job = self.store.job(work)
            self.assertEqual(self.service.preparations.get(row['id'])['due_at'], self.now)
            fact = self.service.preparation_run_fact(job)
            self.assertIn(f'총 6회 중 {number}번째', fact)
            self.assertIn('일정 종료: 2026-11-05 18:00 (Thu, Asia/Seoul)', fact)
            self.assertIn('Work가 끝난 뒤 Still이 답을 telegram 채널로', fact)
            self.assertIn('이 Work가 직접 보내지 않습니다', fact)
            if number == 3:
                self.assertIn('다음 실행 예정: 2026-11-05 15:00 (Thu, Asia/Seoul)', fact)
                # #692: followed by the line naming the previous run's report.
                self.assertTrue(self.service.preparation_history(job)[-1]['content'].startswith(job['message'] + '\n\n' + fact))
                orchestration = self.service.work_orchestration(job, job['message'], [], {}, WorkBudget())
                self.assertEqual(orchestration.request, job['message'] + '\n\n' + fact)
                with self.store.db() as db:
                    db.execute("UPDATE preparations SET delivery_mode='when_needed' WHERE id=?", (row['id'],))
                needed = self.service.preparation_run_fact(job)
                self.assertIn('Still이 telegram 채널로 소유자에게 알릴지 판단', needed)
                self.assertIn('이 Work가 직접 보내지 않습니다', needed)
                with self.store.db() as db:
                    db.execute("UPDATE preparations SET delivery_mode='always' WHERE id=?", (row['id'],))
            if number == 6:
                self.assertIn('이번이 마지막 실행', fact)
                self.assertNotIn('다음 실행 예정', fact)
            with self.store.db() as db:
                db.execute("UPDATE jobs SET status='succeeded',response='확인',delivery='sent' WHERE id=?", (work,))
            self.service.preparations.settle(self.service.preparations.get(row['id']), self.now)
        ordinary = {'id': 'ordinary', 'message': '확인', 'request_key': 'web-ordinary'}
        self.assertEqual(self.service.preparation_run_fact(ordinary), '')
        self.assertEqual(self.service.work_orchestration(ordinary, '확인', [], {}, WorkBudget()).request, '확인')
        self.assertEqual(self.service.preparation_run_fact({**job, 'model': 'preparation'}), '')

    def test_other_schedules_name_the_next_run_from_the_scheduler_or_the_last(self):
        start = datetime(2026, 11, 5, 12, tzinfo=SEOUL).timestamp()
        self.now = start
        cases = [
            (None, '이번이 마지막 실행'),
            ('daily', '다음 실행 예정: 2026-11-06 12:00 (Fri, Asia/Seoul)'),
            ('RRULE:FREQ=DAILY;COUNT=3', '다음 실행 예정: 2026-11-06 12:00 (Fri, Asia/Seoul)'),
        ]
        for recurrence, next_text in cases:
            with self.subTest(recurrence=recurrence):
                row = self.service.preparations.create(kind='prepare', goal=f'정기 확인 {recurrence}', due_at=start,
                    timezone='Asia/Seoul', recurrence=recurrence, channel='web', created_from='settings',
                    state=prep.STATE_SCHEDULED)
                work = self.service.preparations.start(row, channel='web', chat_id=None, now=start)
                fact = self.service.preparation_run_fact(self.store.job(work))
                self.assertIn(next_text, fact)
                # Only a watch records an end of its own; nothing else is computed here.
                self.assertNotIn('일정 종료', fact)

    def test_missing_or_unreadable_row_keeps_the_fixed_fallback(self):
        from unittest.mock import patch
        import sqlite3
        job = {'request_key': prep.request_key('missing', self.now)}
        self.assertEqual(self.service.preparation_run_fact(job), msg('preparations.run_fact'))
        with patch.object(self.service.preparations, 'get', side_effect=sqlite3.OperationalError('unavailable')):
            self.assertEqual(self.service.preparation_run_fact(job), msg('preparations.run_fact'))
        history = self.service.preparation_history({**job, 'id': 'missing', 'message': '확인'})
        self.assertEqual(history[-1]['content'], '확인\n\n' + msg('preparations.run_fact'))


class PreparationEarlierReportTests(_Case):
    """#692: one attributable delivered report, shared by both workers and judgment."""

    def run_job(self, row, number, *, response=None, status='succeeded', delivery='none',
                chat_id=None, suffix=''):
        ident = self.store.enqueue(row['goal_text'], prep.request_key(row['id'], self.now + number) + suffix,
                                   channel='telegram' if chat_id else 'web', chat_id=chat_id)
        with self.store.db() as db:
            db.execute('UPDATE jobs SET created=?,status=?,response=?,delivery=? WHERE id=?',
                       (self.now + number, status, response, delivery, ident))
            db.execute('INSERT INTO messages(role,content,channel,created,job_id) VALUES (?,?,?,?,?)',
                       ('user', row['goal_text'], 'telegram' if chat_id else 'web', self.now + number, ident))
        return self.store.job(ident)

    def test_latest_earlier_delivered_result_is_selected_structurally(self):
        row = self.scheduled()
        first = self.run_job(row, 1, response='First report')
        self.assertEqual([r['role'] for r in self.service.preparation_history(first)], ['user'])
        second = self.run_job(row, 2, status='queued')
        self.assertEqual(self.service.preparation_history(second)[0]['content'], 'First report')
        latest = self.run_job(row, 3, response='Latest report', status='partial', delivery='sent', chat_id=CHAT,
                              suffix=prep.CONTINUATION_SEPARATOR + '1')
        for number, status, delivery, chat_id, response in (
                (4, 'failed', 'sent', CHAT, 'Failure notice'),
                (5, 'cancelled', 'sent', CHAT, 'Cancelled notice'),
                (6, 'succeeded', 'unknown', CHAT, 'Unknown delivery'),
                (7, 'succeeded', 'none', CHAT, 'Not sent'),
                (8, 'succeeded', 'none', None, '\t\n ')):
            self.run_job(row, number, status=status, delivery=delivery, chat_id=chat_id, response=response)
        current = self.run_job(row, 10, status='queued', response='Current response')
        self.run_job(row, 11, response='Future response')
        other = self.scheduled(goal='Unrelated preparation')
        self.run_job(other, 9, response='Unrelated report')
        # More than jobs()'s 40-row window must not hide the last result.
        for number in range(45):
            self.store.enqueue('Unrelated conversation', f'unrelated-{number}')
        history = self.service.preparation_history(current)
        self.assertEqual([r['role'] for r in history], ['assistant', 'user'])
        self.assertIn(msg('preparations.previous_report', at=prep.local_text(self.now + 3, 'Asia/Seoul')), history[-1]['content'])
        self.assertEqual(history[0], {'role': 'assistant', 'content': 'Latest report', 'job_id': latest['id'],
                                     'channel': 'telegram', 'qualifier': None,
                                     'request_key': self.store.job(latest['id'])['request_key'], 'created': self.now + 3})

    def test_forgotten_report_is_skipped_for_an_earlier_delivered_report(self):
        from personal_agent.owner_forget import OwnerForget
        row = self.scheduled()
        first = self.run_job(row, 1, response='Kept report')
        forgotten = self.run_job(row, 2, response='Forgotten report')
        current = self.run_job(row, 3, status='queued')
        with self.store.db() as db:
            message_id = db.execute("SELECT id FROM messages WHERE job_id=? AND role='user'",
                                    (forgotten['id'],)).fetchone()['id']
        OwnerForget(self.store).forget('local-owner', f'message:{message_id}')
        history = self.service.preparation_history(current)
        self.assertEqual(history[0]['job_id'], first['id'])
        self.assertNotIn('Forgotten report', json.dumps(history))
        with self.store.db() as db:
            message_id = db.execute("SELECT id FROM messages WHERE job_id=? AND role='user'",
                                    (first['id'],)).fetchone()['id']
        OwnerForget(self.store).forget('local-owner', f'message:{message_id}')
        self.assertEqual([r['role'] for r in self.service.preparation_history(current)], ['user'])

    def test_report_is_scrubbed_and_bounded_in_both_worker_envelopes_and_judgment(self):
        from personal_agent.agent_runtime import MESSAGE_CAP_CHARS
        from personal_agent.conversation_projection import context_message, earlier_turn_line
        from personal_agent.agent_runtime import WorkBudget
        from unittest.mock import patch
        row = self.scheduled()
        private = 'PRIVATE-SAVED-VALUE-12345678'
        previous = self.run_job(row, 1, response=f'Report {private} {SECRET} ' + 'x' * 5000)
        with self.store.db() as db:
            db.execute('INSERT INTO memory_candidates(id,job_id,memory_key,content,created,state,work_key) '
                       'VALUES (?,?,?,?,?,?,?)', ('private', None, 'private.fact', private, self.now, 'pending',
                                                self.store._work_binding(previous['id'])))
        current = self.run_job(row, 2, status='queued')
        rows = self.service.history_for_models(self.service.preparation_history(current))
        self.assertEqual(rows[0]['content'], self.service.scrub_work_text(previous['id'], previous['response']))
        for route in ('cli', 'api'):
            context = turn_context([context_message(r, current=i == len(rows)-1) for i, r in enumerate(rows)], route)
            [report] = context['conversation']
            self.assertEqual(report['role'], 'assistant')
            self.assertLessEqual(len(report['content']), MESSAGE_CAP_CHARS + len(' [...]'))
            rendered = render_turn_prompt(context)
            self.assertIn('[assistant] Report', rendered)
            self.assertNotIn(private, rendered)
            self.assertNotIn(SECRET, rendered)
            self.assertIn('[assistant, earlier Work 2026-10-14 21:00]', earlier_turn_line(rows[0], report, SEOUL))
        orchestration = self.service.work_orchestration(current, current['message'], rows, {}, WorkBudget())
        # The plan's own label carries the host zone's time (none on a zone-less host).
        self.assertRegex(orchestration.conversation, r'\[assistant, earlier Work[^]]*\] Report')
        self.assertNotIn(private, orchestration.conversation)
        with patch('personal_agent.orchestrator.Orchestration.early_plan', autospec=True, return_value=None) as early:
            self.service.early_work_plan(current, current['message'])
        self.assertEqual(early.call_args.args[0].conversation, orchestration.conversation)
        orchestration.evaluate_answer('Current report', '')
        judged = [item[1] for item in self.judge.asked if item[0] == 'judge' and item[1].purpose == 'goal-reached']
        self.assertTrue(judged)
        self.assertRegex(json.dumps(judged[-1].facts, ensure_ascii=False), r'\[assistant, earlier Work[^]]*\] Report')


class ReminderTests(_Case):
    def reminder_request(self):
        """The owner asks; the scripted model reads nothing else and schedules one reminder."""
        self.script = [{'content': None, 'tool_calls': [call('1', 'schedule_preparation', kind='reminder', goal='치과 예약 10시',
                                                             due=self.due_iso(3600))]},
                       finish('f', '1', summary='내일 9시에 알려 드릴게요.')]
        work = self.receive('한 시간 뒤에 치과 예약 알려줘')
        [row] = self.rows()
        return work, row

    def test_a_reminder_from_the_request_is_delivered_once_and_survives_restart(self):
        work, row = self.reminder_request()
        self.assertEqual((row['state'], row['accepted_by'], row['kind']), ('scheduled', 'owner-request', 'reminder'))
        self.assertEqual(row['created_from'], work)
        asked = [item[1] for item in self.judge.asked if item[0] == 'judge' and item[1].purpose == 'explicit-preparation-request']
        self.assertEqual(len(asked), 1, 'one judgment, not a text rule')
        self.assertEqual(asked[0].facts['owner_message'], '한 시간 뒤에 치과 예약 알려줘')
        self.assertIn('치과 예약 10시', asked[0].facts['proposed_preparation'])
        self.assertEqual(self.store.job(work)['status'], 'succeeded')
        self.service.deliver_one()
        self.service.deliver_notification()
        # #1455: the reply, then the reservation in the conversation with a cancel button (no proposal to accept).
        sends = self.sends()
        self.assertEqual(len(sends), 2)
        self.assertIn('치과 예약 10시', sends[1]['text'])
        self.assertNotIn('설정 >', sends[1]['text'])
        [[button]] = sends[1]['reply_markup']['inline_keyboard']
        self.assertTrue(button['callback_data'].endswith(':cancel'))

        # Not due yet: nothing happens.
        self.now += 3500
        self.assertFalse(self.tick())
        self.now += 101
        model_calls = len(self.model_calls)
        self.assertTrue(self.tick())
        [run] = self.runs(row['id'])
        self.assertEqual((run['status'], run['delivery'], run['channel']), ('succeeded', 'pending', f'telegram:{GENERATION}'))
        self.service.deliver_one()
        self.assertEqual(sum('알림: 치과 예약 10시' in text for text in self.texts()), 1)
        self.assertEqual(len(self.model_calls), model_calls, 'a reminder runs no model')
        self.tick()
        self.assertEqual(self.service.preparations.get(row['id'])['state'], 'delivered')

        # Restart, more ticks and deliveries: never a second reminder.
        restarted = self.make_service()
        restarted.recover_interrupted_work()
        for _ in range(3):
            self.now += 3600
            self.tick(restarted)
            restarted.deliver_one()
            restarted.deliver_notification()
        self.assertEqual(sum('알림: 치과 예약' in text for text in self.texts()), 1)
        self.assertEqual(len(self.runs(row['id'])), 1)
        # Evidence: preparation id -> Work id -> delivery receipt.
        events = [e['trace'] for e in self.store.task_events(run['id']) if e['tool'] == 'preparation']
        self.assertEqual([e['preparation_id'] for e in events], [row['id'], row['id']])
        self.assertEqual(events[-1]['delivery'], 'sent')
        self.assertEqual(self.service.preparations.get(row['id'])['last_run_job_id'], run['id'])

    def test_a_restart_between_claim_and_send_sends_once(self):
        _work, row = self.reminder_request()
        self.service.deliver_one()
        self.now += 3700
        self.assertTrue(self.tick())
        restarted = self.make_service()
        restarted.recover_interrupted_work()
        self.tick(restarted)
        restarted.deliver_one()
        restarted.deliver_one()
        self.assertEqual(sum('알림: 치과 예약 10시' in text for text in self.texts()), 1)
        self.assertEqual(len(self.runs(row['id'])), 1)

    def test_a_send_interrupted_mid_flight_is_unknown_and_never_resent(self):
        _work, row = self.reminder_request()
        self.service.deliver_one()
        self.now += 3700
        self.tick()
        [run] = self.runs(row['id'])
        with self.store.db() as db:
            db.execute("UPDATE jobs SET delivery='sending' WHERE id=?", (run['id'],))
        restarted = self.make_service()
        restarted.recover_interrupted_work()
        restarted.deliver_one()
        self.tick(restarted)
        self.assertFalse(any('알림:' in text for text in self.texts()))
        self.assertEqual(self.service.preparations.get(row['id'])['state'], 'unknown')

    def test_a_reminder_due_while_telegram_is_unreachable_waits_and_arrives_once(self):
        """#123: a send that never reached Telegram stays queued, backs off and is sent once it is reachable."""
        _work, row = self.reminder_request()
        self.service.deliver_one()
        self.now += 3700
        self.tick()
        [run] = self.runs(row['id'])
        outage, attempts, original = [True], [], self.service.telegram_transport
        def unreachable(url, body=None, headers=None, timeout=60):
            if url.endswith('/sendMessage'):
                attempts.append(url)
                if outage[0]:
                    raise TelegramNotSent('connect failed after the client retries')
            return original(url, body, headers, timeout=timeout)
        self.service.telegram_transport = unreachable
        self.service.deliver_one()
        self.assertEqual(self.store.job(run['id'])['delivery'], 'pending')
        self.assertEqual(self.service._unsent_backoff, 15)
        self.tick()
        self.assertEqual(self.service.preparations.get(row['id'])['state'], 'running', 'not settled while waiting')
        self.service.deliver_one()
        self.assertEqual(len(attempts), 1, 'nothing is tried during the backoff')
        self.service._unsent_retry_at = 0
        self.service.deliver_one()
        self.assertEqual((len(attempts), self.service._unsent_backoff), (2, 30))
        outage[0] = False
        self.service._unsent_retry_at = 0
        self.service.deliver_one()
        self.tick()
        self.assertEqual(sum('알림: 치과 예약 10시' in text for text in self.texts()), 1)
        self.assertEqual(self.store.job(run['id'])['delivery'], 'sent')
        self.assertEqual(self.service._unsent_backoff, 0)
        self.assertEqual(self.service.preparations.get(row['id'])['state'], 'delivered')

    def test_a_send_lost_after_it_was_written_is_unknown_and_never_resent(self):
        _work, row = self.reminder_request()
        self.service.deliver_one()
        self.now += 3700
        self.tick()
        [run] = self.runs(row['id'])
        attempts, original = [], self.service.telegram_transport
        def lost(url, body=None, headers=None, timeout=60):
            if url.endswith('/sendMessage'):
                attempts.append(url)
                raise ProviderError('reset after the request was written')
            return original(url, body, headers, timeout=timeout)
        self.service.telegram_transport = lost
        for _ in range(3):
            self.service.deliver_one()
            self.tick()
        self.assertEqual(len(attempts), 1)
        self.assertEqual(self.store.job(run['id'])['delivery'], 'unknown')
        self.assertEqual(self.service.preparations.get(row['id'])['state'], 'unknown')

    def test_a_late_reminder_says_it_is_late(self):
        row = self.scheduled(kind='reminder', goal='약 먹기', seconds=60, channel='telegram')
        self.now += 3 * 3600
        self.tick()
        self.service.deliver_one()
        [text] = [t for t in self.texts() if '약 먹기' in t]
        self.assertIn('늦게 전달했습니다', text)
        self.assertEqual(len(self.runs(row['id'])), 1)


class RecurrenceTests(_Case):
    def test_a_recurring_preparation_advances_and_a_missed_window_runs_once(self):
        row = self.scheduled(recurrence='daily')
        first = row['due_at']
        self.now = first + 3 * 86400 + 60  # three daily slots missed while down
        self.assertTrue(self.tick())
        self.assertFalse(self.tick(), 'the claimed slot is running, not due again')
        self.assertTrue(self.service.run_one())
        self.assertTrue(self.tick())  # settle
        current = self.service.preparations.get(row['id'])
        self.assertEqual(current['state'], 'scheduled')
        self.assertEqual(current['due_at'], first + 4 * 86400, 'next wall-clock slot after now')
        self.assertEqual(len(self.runs(row['id'])), 1, 'three missed slots ran once')
        self.assertFalse(self.tick())
        self.now = current['due_at'] + 1
        self.assertTrue(self.tick())
        self.assertEqual(len(self.runs(row['id'])), 2)

    def test_next_due_keeps_wall_clock_time_and_weekdays(self):
        friday = datetime(2026, 10, 2, 11, 30, tzinfo=SEOUL).timestamp()
        monday = prep.next_due(friday, 'Asia/Seoul', 'weekdays', friday + 60)
        self.assertEqual(datetime.fromtimestamp(monday, SEOUL).strftime('%a %H:%M'), 'Mon 11:30')
        weekly = prep.next_due(friday, 'Asia/Seoul', 'weekly', friday + 60)
        self.assertEqual(weekly - friday, 7 * 86400)
        self.assertIsNone(prep.next_due(friday, 'Asia/Seoul', None, friday + 60))
        # DST (New York leaves daylight time on 2026-11-01): 09:00 stays 09:00.
        new_york = ZoneInfo('America/New_York')
        before = datetime(2026, 10, 31, 9, 0, tzinfo=new_york).timestamp()
        after = prep.next_due(before, 'America/New_York', 'daily', before + 60)
        self.assertEqual(datetime.fromtimestamp(after, new_york).strftime('%m-%d %H:%M'), '11-01 09:00')
        self.assertEqual(after - before, 86400 + 3600)
        later = prep.next_due(after, 'America/New_York', 'daily', after + 60)
        self.assertEqual(datetime.fromtimestamp(later, new_york).strftime('%m-%d %H:%M'), '11-02 09:00')

    def test_due_parsing_refuses_the_past_and_a_missing_zone(self):
        with self.assertRaises(prep.PreparationRefusal) as refused:
            prep.parse_due(self.due_iso(-3600), '', 'Asia/Seoul', self.now)
        self.assertEqual(refused.exception.code, 'due_in_past')
        self.assertIn('지금은', str(refused.exception), 'the loop learns the current time')
        with self.assertRaises(prep.PreparationRefusal) as refused:
            prep.parse_due('2026-12-01T09:00', '', '', self.now)
        self.assertEqual(refused.exception.code, 'timezone_unknown')
        day = datetime.fromtimestamp(self.now + 30 * 86400, SEOUL).date().isoformat()
        due_at, zone = prep.parse_due(day + 'T09:00', '', 'Asia/Seoul', self.now)
        self.assertEqual((zone, datetime.fromtimestamp(due_at, SEOUL).hour), ('Asia/Seoul', 9))
        with self.assertRaises(prep.PreparationRefusal):
            prep.normalize_recurrence('every 5 minutes')


class TickCostTests(_Case):
    def test_a_tick_with_nothing_due_calls_no_model_network_or_telegram(self):
        self.scheduled(seconds=3600)
        proposed = self.service.preparations.create(kind='reminder', goal='제안만 된 알림', due_at=self.now + 1,
                                                    timezone='Asia/Seoul', recurrence=None, channel='telegram',
                                                    created_from='w', state=prep.STATE_PROPOSED)
        cancelled = self.scheduled(kind='reminder', goal='취소된 알림', seconds=1, channel='telegram')
        self.service.preparations.cancel(cancelled['id'])
        self.now += 60
        for _ in range(50):
            self.assertFalse(self.tick())
            self.now += 1
        self.assertEqual((self.model_calls, self.network.plans, self.telegram, self.store.jobs()), ([], [], [], []))
        self.assertEqual(self.service.preparations.get(proposed['id'])['state'], 'proposed')

    def test_the_tick_query_uses_the_state_due_index(self):
        with self.store.db() as db:
            plan = ' '.join(str(tuple(row)) for row in db.execute(
                "EXPLAIN QUERY PLAN SELECT p.* FROM preparations p LEFT JOIN jobs j ON j.id=p.last_run_job_id "
                "WHERE p.state IN ('scheduled','running') AND p.due_at<=? ORDER BY p.due_at LIMIT 8", (self.now,)))
        self.assertIn('preparations_state_due', plan)


class PreparedAnswerTests(_Case):
    def lunch_worker(self, body):
        """The prepared Work's model reads the profile it was given, then searches."""
        system = body['messages'][0]['content']
        self.assertIn(PROFILE_HEADING, system)
        self.assertIn('땅콩', system)
        return {'content': None, 'tool_calls': [call('1', 'web_search', query='합정 점심 땅콩 없는 식당')]}

    def test_a_prepared_lunch_runs_the_loop_and_is_consumed_fresh_but_not_stale(self):
        memory = MemoryService(self.store, private_read_sink=MemoryService.NO_EGRESS_GUARD)
        memory.remember_profile('local-owner', 'settings', 'profile.allergy', '땅콩 알레르기')
        memory.remember_profile('local-owner', 'settings', 'profile.place.home', '합정')
        row = self.scheduled(recurrence='daily')
        self.now += 120
        self.tick()
        self.script = [self.lunch_worker, finish('f', '1', summary='합정 점심 후보: 국수집, 덮밥집, 샐러드집 (땅콩 없음)')]
        self.assertTrue(self.service.run_one())
        [run] = self.runs(row['id'])
        run = self.store.job(run['id'])
        self.assertEqual(run['status'], 'succeeded', run)
        self.assertEqual([plan['query'] for plan in self.network.plans], ['합정 점심 땅콩 없는 식당'])
        self.tick()
        current = self.service.preparations.get(row['id'])
        self.assertEqual((current['prepared_result_ref'], current['last_outcome']), (run['id'], 'delivered'))

        seen = []
        self.script = [lambda body: seen.append(body['messages'][0]['content']) or {'content': '준비해 둔 후보입니다.'}]
        self.now += 30 * 60
        asked = self.web_turn('점심 뭐 먹지?')
        section = seen[0].split(prep.PREPARED_HEADING + '\n', 1)[1]
        payload = json.loads(section.split('\n')[1].split('\n\n')[0])
        [item] = payload['items']
        self.assertEqual((item['ref'], item['goal']), ('prep:' + row['id'], '점심 후보 3곳 준비해 줘'))
        self.assertIn('국수집', item['answer'])
        self.assertEqual(item['age_min'], 30)
        record = self.store.turn_provenance(asked)
        # #701 (pilot posture): the prepared section no longer withholds the local
        # record; the envelope is kept after deterministic redaction.
        self.assertNotIn('prompt_withheld', record)
        self.assertIn('국수집', record['prompt_envelope'])

        self.script = [lambda body: seen.append(body['messages'][0]['content']) or {'content': '다시 찾아볼게요.'}]
        self.now += prep.FRESH_SECONDS
        self.web_turn('점심 뭐 먹지?')
        self.assertNotIn(prep.PREPARED_HEADING, seen[1], 'a stale prepared answer is not offered')

    def test_the_section_is_bounded_and_counted_in_the_turn_budget(self):
        rows = [{'id': f'p{i}', 'goal_text': '목표' * 20, 'timezone': 'Asia/Seoul', 'recurrence': None,
                 'prepared_at': self.now - 60, 'status': 'succeeded', 'response': '가' * 5000} for i in range(3)]
        text = prep.render_prepared(rows, self.now)
        self.assertLessEqual(len(text.encode()), prep.SECTION_MAX_BYTES)
        context = turn_context([{'role': 'user', 'content': '점심'}], 'cli', prepared=text)
        self.assertIn(prep.PREPARED_HEADING, render_turn_prompt(context))
        self.assertEqual(turn_context([{'role': 'user', 'content': '점심'}], 'cli').get('prepared'), None)


class AcceptanceContextTests(_Case):
    """#573: the acceptance judgment reads the exchange the request refers to."""

    def asked(self):
        return [item[1] for item in self.judge.asked if item[0] == 'judge' and item[1].purpose == 'explicit-preparation-request']

    def remind_at_that_time(self, text='그 시간에 먼저 알려줘'):
        self.script = [{'content': None, 'tool_calls': [call('1', 'schedule_preparation', kind='reminder', goal='출발할 시간이에요.',
                                                             due=self.due_iso(3600))]},
                       finish('f', '1', summary='그 시간에 알려 드릴게요.')]
        return self.receive(text)

    def test_the_earlier_turns_reach_the_judgment_labelled_and_apart_from_the_message(self):
        # The worker's answer, then the same text as its final reply.
        self.script = [{'content': '좋아요, 한 시간 뒤에 출발하시는군요.'}] * 2
        self.receive('나 한 시간 뒤에 출발해')
        self.now += 60
        work = self.remind_at_that_time()
        [context] = self.asked()
        self.assertEqual(context.facts['owner_message'], '그 시간에 먼저 알려줘')
        conversation = context.facts['recent_conversation']
        self.assertIn('[owner, earlier Work', conversation)
        self.assertIn('나 한 시간 뒤에 출발해', conversation)
        self.assertIn('[assistant, earlier Work', conversation)
        self.assertIn('한 시간 뒤에 출발하시는군요', conversation)
        self.assertNotIn('그 시간에 먼저 알려줘', conversation, 'the message itself is its own fact')
        [row] = self.rows()
        self.assertEqual((row['state'], row['accepted_by'], row['created_from']), ('scheduled', 'owner-request', work))

    def test_a_first_message_has_no_conversation(self):
        self.remind_at_that_time('한 시간 뒤에 출발한다고 알려줘')
        [context] = self.asked()
        self.assertEqual(context.facts['recent_conversation'], 'none')

    def test_the_window_is_bounded_and_stored_secrets_never_reach_it(self):
        for index in range(8):
            self.script = [{'content': f'답변 {index}'}]
            self.receive(f'이전 질문 {index} {SECRET}' if index == 7 else f'이전 질문 {index}')
            self.now += 60
        self.remind_at_that_time()
        [context] = self.asked()
        conversation = context.facts['recent_conversation']
        self.assertNotIn(SECRET, conversation)
        self.assertEqual(conversation.count('earlier Work'), PREPARATION_CONVERSATION_TURNS)
        self.assertNotIn('이전 질문 4', conversation)
        self.assertIn('이전 질문 5', conversation)
        self.assertLessEqual(len(conversation), PREPARATION_CONVERSATION_CHARS)

    def test_a_no_keeps_the_proposal_waiting_for_the_owner(self):
        self.judge = engine(preparation=False)
        self.service.use_decision_engine(self.judge)
        self.script = [{'content': '좋아요.'}]
        self.receive('나 한 시간 뒤에 출발해')
        self.remind_at_that_time()
        [row] = self.rows()
        self.assertEqual((row['state'], row['accepted_by']), ('proposed', None))


class DeclinedOfferTests(_Case):
    """#582: the worker may offer a preparation; one the owner declined is not offered again."""

    def offer(self, goal='아침 요약 준비', text='오늘 아침 요약해줘'):
        self.script = [{'content': None, 'tool_calls': [call('1', 'schedule_preparation', kind='prepare', goal=goal,
                                                             due=self.due_iso(86400), recurrence='weekdays', delivery='send')]},
                       {'content': '요약했어요. 평일 아침마다 준비해 둘까요?'}]
        work = self.receive(text)
        results = [json.loads(m['content']) for m in self.model_calls[-1]['messages'] if m['role'] == 'tool']
        return work, results[-1]

    def declined_asked(self):
        return [item[1] for item in self.judge.asked if item[0] == 'judge' and item[1].purpose == 'declined-preparation-goal']

    def test_an_offer_waits_for_the_owner_and_never_runs(self):
        self.judge = engine(preparation=False)
        self.service.use_decision_engine(self.judge)
        _work, result = self.offer()
        [row] = self.rows()
        self.assertEqual((row['state'], row['accepted_by']), ('proposed', None))
        self.assertTrue(result['requires_owner_acceptance'])
        self.now += 2 * 86400
        self.assertFalse(self.tick())
        self.assertEqual(self.runs(row['id']), [])
        self.assertEqual(self.declined_asked(), [], 'no declined proposal, no extra judgment')

    def test_a_declined_offer_is_refused_the_second_time(self):
        self.judge = engine(preparation=False)
        self.service.use_decision_engine(self.judge)
        self.offer()
        [row] = self.rows()
        self.service.cancel_preparation(row['id'])
        self.now += 86400
        _work, result = self.offer(text='오늘도 아침 요약해줘')
        self.assertEqual(result.get('code'), 'declined_before', result)
        self.assertEqual([r['id'] for r in self.rows()], [row['id']], 'no second proposal')
        self.assertEqual(self.declined_asked(), [], 'the same goal text needs no judgment')

    def test_a_reworded_offer_is_judged_and_a_different_one_is_allowed(self):
        def judge(context, proposition):
            if context.purpose == 'declined-preparation-goal':
                return BinaryDecision(OUTCOME_DECIDED, '날씨' in context.facts['proposed_goal'], fixture_confidence())
            if context.purpose == 'goal-reached':
                return BinaryDecision(OUTCOME_DECIDED, True, fixture_confidence())
            if context.purpose == 'explicit-preparation-request':
                return BinaryDecision(OUTCOME_DECIDED, False, fixture_confidence())
            return None
        self.judge = FixtureDecisionEngine(judge=judge)
        self.service.use_decision_engine(self.judge)
        self.offer(goal='아침 날씨 요약')
        [first] = self.rows()
        self.service.cancel_preparation(first['id'])
        _work, result = self.offer(goal='출근 전 날씨 정리')
        self.assertEqual(result.get('code'), 'declined_before')
        [asked] = self.declined_asked()
        self.assertIn('아침 날씨 요약', asked.facts['declined_goals'])
        _work, result = self.offer(goal='주간 일정 정리')
        self.assertEqual(result.get('state'), 'proposed', result)

    def test_the_owners_own_request_is_never_blocked_by_an_earlier_decline(self):
        self.judge = engine(preparation=False)
        self.service.use_decision_engine(self.judge)
        self.offer()
        [row] = self.rows()
        self.service.cancel_preparation(row['id'])
        self.judge = engine(preparation=True)
        self.service.use_decision_engine(self.judge)
        _work, result = self.offer(text='평일 아침마다 요약 준비해줘')
        self.assertEqual((result.get('state'), result.get('accepted_by')), ('scheduled', 'owner-request'), result)

    def test_a_decline_is_forgotten_after_thirty_days_and_an_accepted_row_is_not_a_decline(self):
        self.judge = engine(preparation=False)
        self.service.use_decision_engine(self.judge)
        self.offer()
        [row] = self.rows()
        self.service.cancel_preparation(row['id'])
        accepted = self.scheduled(goal='다른 준비')
        self.service.cancel_preparation(accepted['id'])
        self.assertEqual(self.service.preparations.declined_goals(self.now), ['아침 요약 준비'])
        self.now += prep.DECLINED_SECONDS + 60
        self.assertEqual(self.service.preparations.declined_goals(self.now), [])
        _work, result = self.offer()
        self.assertEqual(result.get('state'), 'proposed', result)


class AcceptanceTests(_Case):
    def propose(self, text='점심 메뉴 추천해줘'):
        self.script = [{'content': None, 'tool_calls': [call('1', 'schedule_preparation', kind='prepare', goal='점심 후보 준비',
                                                             due=self.due_iso(1800), recurrence='daily', delivery='send')]},
                       {'content': '매일 점심 후보를 준비해 둘까요? 수락하면 예약합니다.'}]
        work = self.receive(text)
        [row] = self.rows()
        return work, row

    def test_no_message_is_sent_except_from_an_accepted_preparation(self):
        self.service.use_decision_engine(engine(preparation=False))
        work, row = self.propose()
        self.assertEqual((row['state'], row['accepted_by']), ('proposed', None))
        [result] = [json.loads(m['content']) for m in self.model_calls[-1]['messages'] if m['role'] == 'tool']
        self.assertTrue(result['requires_owner_acceptance'])
        self.service.deliver_one()
        self.service.deliver_notification()
        self.assertEqual(len(self.sends()), 2, 'the reply and the one proposal message')
        proposal = self.sends()[-1]
        self.assertIn('점심 후보 준비', proposal['text'])
        buttons = json.dumps(proposal['reply_markup'])
        self.assertIn('p7p:', buttons)

        self.now += 3600
        for _ in range(3):
            self.assertFalse(self.tick(), 'an unaccepted preparation never runs')
            self.service.deliver_one()
            self.service.deliver_notification()
        self.assertEqual(len(self.sends()), 2)
        self.assertEqual(self.runs(row['id']), [])

        with self.store.db() as db:
            notification = dict(db.execute("SELECT * FROM telegram_notifications WHERE job_id=? AND kind='preparation_proposed'",
                                           (work,)).fetchone())
        self.tap(f"p7p:{notification['id']}:accept", notification['message_id'] + 1)
        self.assertEqual(self.service.preparations.get(row['id'])['state'], 'proposed', 'another message is not the proposal')
        self.tap(f"p7p:{notification['id']}:accept", notification['message_id'])
        accepted = self.service.preparations.get(row['id'])
        self.assertEqual((accepted['state'], accepted['accepted_by']), ('scheduled', 'owner-button'))
        self.assertTrue(self.tick())
        self.script = [{'content': '국수집과 덮밥집이 좋아요.'}]
        self.assertTrue(self.service.run_one())
        self.service.deliver_one()
        self.assertEqual(len(self.sends()), 3)
        self.assertTrue(self.sends()[-1]['text'].startswith('<b>점심 후보 준비</b>\n\n'))

    def test_declining_the_proposal_cancels_it(self):
        self.service.use_decision_engine(engine(preparation=False))
        work, row = self.propose()
        self.service.deliver_one()
        self.service.deliver_notification()
        with self.store.db() as db:
            notification = dict(db.execute("SELECT * FROM telegram_notifications WHERE job_id=? AND kind='preparation_proposed'",
                                           (work,)).fetchone())
        self.tap(f"p7p:{notification['id']}:deny", notification['message_id'])
        self.assertEqual(self.service.preparations.get(row['id'])['state'], 'cancelled')
        self.assertIn('editMessageText', [method for method, _body in self.telegram])
        self.tap(f"p7p:{notification['id']}:accept", notification['message_id'])
        self.assertEqual(self.service.preparations.get(row['id'])['state'], 'cancelled', 'a used message cannot accept later')
        self.now += 3600
        self.assertFalse(self.tick())

    def test_without_a_decision_engine_the_owner_is_asked(self):
        self.service.use_decision_engine(engine(preparation=None))
        _work, row = self.propose('매일 11시 반에 점심 후보 준비해 둬')
        self.assertEqual(row['state'], 'proposed')

    def test_settings_accept_cancel_and_delete(self):
        self.service.use_decision_engine(engine(preparation=False))
        _work, row = self.propose()
        status = self.service.preparation_request({'operation': 'accept', 'id': row['id']})
        [listed] = status['preparations']
        self.assertEqual((listed['state'], listed['accepted_by'], listed['recurrence']), ('scheduled', 'owner-settings', 'daily'))
        self.assertIn('Asia/Seoul', listed['due_local'])
        with self.assertRaises(ValueError):
            self.service.preparation_request({'operation': 'accept', 'id': row['id']})
        self.service.preparation_request({'operation': 'cancel', 'id': row['id']})
        self.service.preparation_request({'operation': 'delete', 'id': row['id']})
        self.assertEqual(self.service.preparation_request({'operation': 'list'})['preparations'], [])

    def test_a_preparation_run_cannot_accept_a_new_preparation_itself(self):
        row = self.scheduled(goal='내일도 점심 후보 준비해 둬', channel='web')
        self.now += 120
        self.tick()
        before = len(self.judge.asked)
        self.script = [{'content': None, 'tool_calls': [call('1', 'schedule_preparation', kind='prepare', goal='점심 후보',
                                                             due=self.due_iso(86400))]}, {'content': '제안했습니다.'}]
        self.assertTrue(self.service.run_one())
        # #619 (owner decision 2026-11-03): a scheduled run leaves nothing waiting for the owner, so it
        # makes no proposal either; the tool tells the worker to say what it would schedule.
        created = [r for r in self.rows() if r['id'] != row['id']]
        self.assertEqual(created, [])
        [work] = [job for job in self.store.jobs() if prep.preparation_of(job.get('request_key')) == row['id']]
        refused = [e['trace'] for e in self.store.task_events(work['id']) if e['tool'] == 'schedule_preparation']
        self.assertTrue(refused and refused[-1].get('code') == 'unattended_run', refused)
        self.assertFalse(any(item[1].purpose == 'explicit-preparation-request' for item in self.judge.asked[before:]))

    def test_the_judgment_is_a_bounded_proposition_not_a_rule(self):
        engine_ = engine(preparation=True)
        judged = ConversationJudgments(engine_).explicit_preparation_request('내일 9시에 알려줘', '[알림] 2026-09-28 09:00')
        self.assertEqual(judged.outcome, 'yes')
        [(_kind, context, proposition)] = engine_.asked
        self.assertEqual(proposition, PREPARATION_REQUEST_PROPOSITION)
        self.assertEqual(set(context.facts), {'owner_message', 'recent_conversation', 'proposed_preparation'})
        self.assertEqual(context.facts['recent_conversation'], 'none')


class CancelTests(_Case):
    def test_cancel_stops_future_runs(self):
        row = self.scheduled(recurrence='daily')
        self.service.preparation_request({'operation': 'cancel', 'id': row['id']})
        self.now += 2 * 86400
        self.assertFalse(self.tick())
        self.assertEqual(self.runs(row['id']), [])

    def test_cancel_while_running_lets_that_work_finish_and_stops_the_next(self):
        row = self.scheduled(recurrence='daily')
        self.now += 120
        self.tick()
        self.service.preparation_request({'operation': 'cancel', 'id': row['id']})
        self.assertTrue(self.service.run_one())
        self.tick()
        self.now += 3 * 86400
        self.tick()
        self.assertEqual(self.service.preparations.get(row['id'])['state'], 'cancelled')
        self.assertEqual(len(self.runs(row['id'])), 1)
        with self.assertRaises(ValueError):
            self.service.preparations.delete('missing')


class SecretTests(_Case):
    PASSPORT = 'M12345678'

    def capture_logs(self):
        """Every record of the service and runtime loggers, whatever other tests configured."""
        records = []
        handler = logging.Handler(logging.DEBUG)
        handler.emit = records.append
        previous_disable = logging.root.manager.disable
        logging.disable(logging.NOTSET)
        self.addCleanup(logging.disable, previous_disable)
        for name in ('', 'personal_agent', 'personal_agent.service'):
            logger = logging.getLogger(name)
            level = logger.level
            logger.addHandler(handler)
            logger.setLevel(logging.DEBUG)
            self.addCleanup(logger.removeHandler, handler)
            self.addCleanup(logger.setLevel, level)
        return records

    def test_a_secret_in_goal_text_or_result_never_reaches_logs_or_evidence(self):
        records = self.capture_logs()
        self.script = [{'content': None, 'tool_calls': [call('1', 'schedule_preparation', kind='reminder',
                                                             goal=f'토큰 {SECRET} 갱신하기', due=self.due_iso(600))]},
                       finish('f', '1', summary='예약했습니다.')]
        self.web_turn(f'10분 뒤에 토큰 {SECRET} 갱신하라고 알려줘')
        prepared = self.scheduled(goal='점심 후보 준비', seconds=30)
        self.now += 700
        self.tick()
        self.script = [{'content': f'결과에 섞인 {SECRET} 값'}]
        self.service.run_one()
        self.service.deliver_one()
        self.tick()

        [reminder] = [row for row in self.rows() if row['kind'] == 'reminder']
        self.assertEqual(reminder['goal_text'], '토큰 [redacted] 갱신하기')
        self.assertEqual(reminder['state'], 'delivered')
        logged = '\n'.join(record.getMessage() for record in records)
        self.assertIn('preparation', logged)
        self.assertNotIn(SECRET, logged)
        with self.store.db() as db:
            events = ' '.join(row['detail'] or '' for row in db.execute('SELECT detail FROM tool_events'))
            stored = json.dumps([dict(row) for row in db.execute('SELECT * FROM preparations')], ensure_ascii=False)
        self.assertNotIn(SECRET, events)
        self.assertIn('토큰 [redacted] 갱신하기', events, 'the call record keeps the goal with the secret removed')
        self.assertNotIn(SECRET, stored)
        section = self.service.prepared_text({'id': 'next'})
        self.assertIn(prepared['id'], section)
        self.assertNotIn(SECRET, section)

    def test_a_goal_without_a_secret_is_recorded_as_written(self):
        self.script = [{'content': None, 'tool_calls': [call('1', 'schedule_preparation', kind='reminder',
                                                             goal='치과 예약 10시', due=self.due_iso(600))]},
                       finish('f', '1', summary='예약했습니다.')]
        job = self.web_turn('10분 뒤에 치과 예약 알려줘')
        running = [e['trace'] for e in self.store.task_events(job) if e['status'] == 'running']
        self.assertEqual(running[0]['arguments']['goal'], '치과 예약 10시')

    def test_the_goal_keeps_the_owners_values_and_saved_values_leave_the_prepared_answer(self):
        """#617: the goal is the owner's own reservation, so the owner's saved values stay in it (#826
        removed that guard); only secrets are removed.  The prepared answer keeps its own scrub."""
        self.script = [{'content': None, 'tool_calls': [
                           call('1', 'save_note', content='여권번호 ' + self.PASSPORT),
                           call('2', 'schedule_preparation', kind='reminder', goal=f'여권 {self.PASSPORT} 챙기기',
                                due=self.due_iso(600))]},
                       finish('f', '1', '2', summary='저장하고 예약했습니다.')]
        self.web_turn(f'여권번호는 {self.PASSPORT}야. 10분 뒤에 여권 챙기라고 알려줘')
        [reminder] = self.rows()
        self.assertEqual(reminder['goal_text'], f'여권 {self.PASSPORT} 챙기기')

        row = self.scheduled(goal='여행 준비물 정리', seconds=900)
        self.now += 1000
        self.tick()
        self.script = [{'content': None, 'tool_calls': [call('1', 'save_note', content='비자번호 V7654321')]},
                       finish('f', '1', summary='비자번호 V7654321 을 메모했고 준비물은 여권, 충전기입니다.')]
        self.service.run_one()
        self.tick()
        current = self.service.preparations.get(row['id'])
        self.assertIn('충전기', current['prepared_text'])
        self.assertNotIn('V7654321', current['prepared_text'])
        self.assertNotIn('V7654321', self.service.prepared_text({'id': 'next'}))


class PreparationScopeTests(_Case):
    """A preparation's Work sees its origin, latest delivered report and goal, and
    persists nothing unscrubbed; a proposal message accepts only what it shows."""

    ORIGIN = '매일 점심 후보 준비해 줘 ORIGIN-MARK'
    UNRELATED = '어제 병원 검사 결과 이야기 UNRELATED-MARK'

    def origin_then_unrelated(self):
        self.script = [{'content': None, 'tool_calls': [call('1', 'schedule_preparation', kind='prepare', goal='점심 후보 3곳 준비',
                                                             due=self.due_iso(600), recurrence='daily')]},
                       finish('f', '1', summary='매일 준비할게요.')]
        origin = self.web_turn(self.ORIGIN)
        [row] = self.rows()
        self.assertEqual((row['state'], row['created_from']), ('scheduled', origin))
        self.script = [{'content': '그랬군요.'}]
        self.web_turn(self.UNRELATED)
        self.now += 700
        self.assertTrue(self.tick())
        return row

    def test_direct_route_preparation_work_sees_only_its_origin_and_goal(self):
        self.origin_then_unrelated()
        seen = []
        self.script = [lambda body: seen.append(json.loads(json.dumps(body['messages']))) or {'content': '후보: 국수집'}] * 2
        self.assertTrue(self.service.run_one())
        sent = json.dumps(seen[0], ensure_ascii=False)
        self.assertIn('ORIGIN-MARK', sent)
        self.assertIn('점심 후보 3곳 준비', sent)
        self.assertNotIn('UNRELATED-MARK', sent)
        self.assertEqual([m['role'] for m in seen[0][1:]], ['user', 'user'])
        # #671: the run is told Still keeps the schedule and delivers, so its reply is this run's result.
        [run] = [job for job in self.store.jobs() if prep.preparation_of(job.get('request_key'))]
        self.assertIn(self.service.preparation_run_fact(run), seen[0][-1]['content'])
        self.assertIn('다음 실행 예정: 2026-10-15 21:10 (Thu, Asia/Seoul)', seen[0][-1]['content'])

        self.assertTrue(self.tick())  # settle the first web-channel result
        self.now += 86400
        self.assertTrue(self.tick())
        seen.clear()
        self.script = [lambda body: seen.append(json.loads(json.dumps(body['messages']))) or {'content': '두 번째 후보'}]
        self.assertTrue(self.service.run_one())
        self.assertEqual([m['role'] for m in seen[0][1:]], ['user', 'assistant', 'user'])
        self.assertEqual(seen[0][2], {'role': 'assistant', 'content': '후보: 국수집'})
        self.assertNotIn('UNRELATED-MARK', json.dumps(seen[0]))
        # An ordinary Work still reads the ordinary transcript, including unrelated owner turns.
        seen.clear()
        self.script = [lambda body: seen.append(body['messages']) or {'content': '일반 답변'}]
        self.web_turn('일반 요청')
        self.assertIn('UNRELATED-MARK', json.dumps(seen[0]))

    def test_cli_route_preparation_work_sees_only_its_origin_and_goal(self):
        from personal_agent.bounded_execution import ExecutionResult
        from personal_agent.subscription_engines import SubscriptionEngines
        prompts = []

        class Engine:
            def execute(self_, engine, prompt, tools, **kwargs):
                prompts.append(prompt)
                return ExecutionResult('준비했습니다.', engine, 0)

        self.service = AgentService(self.store, ModelAdapter(self._model), self._telegram, execution_adapter=Engine(),
                                    subscription_engines=SubscriptionEngines(finder=lambda _: '/runtime/cli', clock=lambda: 1))
        self.service.preparations.clock = lambda: self.now
        self.service.use_decision_engine(self.judge)
        self.service.connect_subscription_engine({'engine': 'codex', 'officially_authenticated': True})
        origin = self.web_turn(self.ORIGIN)
        self.web_turn(self.UNRELATED)
        row = self.service.preparations.create(kind='prepare', goal='점심 후보 3곳 준비', due_at=self.now + 60,
                                               timezone='Asia/Seoul', recurrence='daily', channel='web', created_from=origin,
                                               state=prep.STATE_SCHEDULED, accepted_by=prep.ACCEPTED_OWNER_SETTINGS)
        self.now += 120
        self.assertTrue(self.tick())
        prompts.clear()
        self.assertTrue(self.service.run_one())
        [prompt] = prompts
        self.assertIn('ORIGIN-MARK', prompt)
        self.assertIn('점심 후보 3곳 준비', prompt)
        self.assertNotIn('UNRELATED-MARK', prompt)
        self.assertIn(self.service.preparation_run_fact(self.runs(row['id'])[0]), prompt)
        self.assertIn('다음 실행 예정:', prompt)
        self.assertEqual(len(self.runs(row['id'])), 1)
        self.assertNotIn('[assistant] 준비했습니다.', prompt)
        self.assertTrue(self.tick())
        self.now += 86400
        self.assertTrue(self.tick())
        prompts.clear()
        self.assertTrue(self.service.run_one())
        [prompt] = prompts
        self.assertEqual(prompt.count('[assistant] 준비했습니다.'), 1)
        self.assertNotIn('UNRELATED-MARK', prompt)

    def test_a_secret_in_a_preparation_answer_is_never_persisted_or_replayed(self):
        self.origin_then_unrelated()
        # The same reply to the loop's one execution check.
        self.script = [{'content': f'후보: 국수집 (키 {SECRET})'}] * 2
        self.assertTrue(self.service.run_one())
        [run] = [job for job in self.store.jobs() if (job['request_key'] or '').startswith(prep.REQUEST_KEY_PREFIX)]
        self.assertIn('국수집', run['response'])
        self.assertNotIn(SECRET, run['response'])
        with self.store.db() as db:
            stored = ' '.join(row['content'] for row in db.execute('SELECT content FROM messages'))
        self.assertIn('국수집', stored)
        self.assertNotIn(SECRET, stored)
        self.tick()
        seen = []
        self.script = [lambda body: seen.append(json.loads(json.dumps(body['messages']))) or {'content': '좋아요.'}]
        self.web_turn('점심 뭐 먹지?')
        replay = json.dumps(seen[0], ensure_ascii=False)
        self.assertIn('국수집', replay, 'the earlier answer is in the next turn')
        self.assertNotIn(SECRET, replay)

    def test_accept_schedules_only_the_proposals_the_message_showed(self):
        self.service.use_decision_engine(engine(preparation=False))
        self.script = [{'content': '알겠습니다.'}]
        work = self.receive('여러 가지 준비해 줘')
        for index in range(12):
            self.service.preparations.create(kind='prepare', goal=f'P{index:02d}Q ' + '긴 준비 목표 ' * 60, due_at=self.now + 3600 + index,
                                             timezone='Asia/Seoul', recurrence=None, channel='web', created_from=work,
                                             state=prep.STATE_PROPOSED)
        self.service.queue_preparation_proposal(self.store.job(work))
        self.service.deliver_one()
        self.service.deliver_notification()
        proposal = self.sends()[-1]
        self.assertLessEqual(len(proposal['text']), prep.PROPOSAL_TEXT_LIMIT)
        self.assertIn('대화로 확인하고 수락', proposal['text'])
        self.assertNotIn('설정 >', proposal['text'])
        shown = [row for row in self.rows() if row['goal_text'][:4] in proposal['text']]
        self.assertTrue(0 < len(shown) < 12)
        with self.store.db() as db:
            notification = dict(db.execute("SELECT * FROM telegram_notifications WHERE job_id=? AND kind='preparation_proposed'",
                                           (work,)).fetchone())
        self.tap(f"p7p:{notification['id']}:accept", notification['message_id'])
        states = {row['goal_text'][:4]: row['state'] for row in self.rows()}
        self.assertEqual({key for key, state in states.items() if state == 'scheduled'}, {row['goal_text'][:4] for row in shown})
        self.assertEqual(sum(state == 'proposed' for state in states.values()), 12 - len(shown))
        # #1455: the accepted message keeps one cancel button bound to exactly what it accepted.
        edits = [body for method, body in self.telegram if method == 'editMessageText']
        self.assertNotIn('설정 >', edits[-1]['text'])
        [[button]] = edits[-1]['reply_markup']['inline_keyboard']
        self.assertEqual(button['callback_data'], f"p7p:{notification['id']}:cancel")
        self.tap(button['callback_data'], notification['message_id'])
        states = {row['goal_text'][:4]: row['state'] for row in self.rows()}
        self.assertEqual({key for key, state in states.items() if state == 'cancelled'}, {row['goal_text'][:4] for row in shown})
        self.assertEqual(sum(state == 'proposed' for state in states.values()), 12 - len(shown), 'the others are untouched')
        self.assertIn('취소했어요', [body for method, body in self.telegram if method == 'editMessageText'][-1]['text'])
        self.tap(button['callback_data'], notification['message_id'])
        self.assertEqual(sum(state == 'cancelled' for state in states.values()), len(shown), 'a second tap changes nothing')


# --- SEC-ATTN-02 (#719): watching until a deadline, telling only when needed ---

def watch_engine(verdicts, preparation=False, same_goals=None):
    """Scripted ``watch-notification`` verdicts (True/False/None=unavailable), in order.

    ``same_goals`` maps a proposed preparation goal to the ``same-preparation-goal``
    verdict (#173 slice B); a goal it does not name is unavailable.
    """
    verdicts = list(verdicts)
    same_goals = dict(same_goals or {})

    def judge(context, proposition):
        if context.purpose == 'same-preparation-goal':
            verdict = same_goals.get(context.facts.get('proposed_goal'))
            return None if verdict is None else BinaryDecision(OUTCOME_DECIDED, verdict, fixture_confidence())
        if context.purpose == 'goal-reached':
            return BinaryDecision(OUTCOME_DECIDED, True, fixture_confidence())
        if context.purpose == 'explicit-preparation-request':
            return BinaryDecision(OUTCOME_DECIDED, preparation, fixture_confidence())
        if context.purpose == 'watch-notification':
            verdict = verdicts.pop(0) if verdicts else None
            return None if verdict is None else BinaryDecision(OUTCOME_DECIDED, verdict, fixture_confidence())
        return None
    return FixtureDecisionEngine(judge=judge)


class _WatchCase(_Case):
    EVERY = 600

    def watch(self, *, runs=None, span=3600, delivery=prep.DELIVERY_WHEN_NEEDED, goal='출발 시각을 앞당겨야 하는지 확인'):
        """An accepted watch: every 10 minutes from one minute from now for ``span`` seconds."""
        due = self.now + 60
        every, end, max_runs = prep.normalize_window(self.EVERY // 60, datetime.fromtimestamp(due + span, SEOUL).isoformat(),
                                                     runs, due, 'Asia/Seoul', self.now)
        return self.service.preparations.create(kind='prepare', goal=goal, due_at=due, timezone='Asia/Seoul', recurrence=None,
                                                channel='telegram', created_from='settings', state=prep.STATE_SCHEDULED,
                                                accepted_by=prep.ACCEPTED_OWNER_SETTINGS, window=(every, end, max_runs),
                                                delivery_mode=delivery)

    def run_slot(self, answer):
        """Advance to the row's slot, start it, let the worker answer, settle, deliver."""
        row = self.service.preparations.get(self.watched['id'])
        self.now = max(self.now, row['due_at']) + 1
        self.assertTrue(self.tick())
        self.script = [{'content': answer}] * 2  # the loop asks once more after a bare answer
        self.assertTrue(self.service.run_one())
        self.service.deliver_one()
        self.assertTrue(self.tick())  # settle: the decision
        self.service.deliver_notification()
        return self.service.preparations.get(self.watched['id'])

    def watch_asks(self):
        return [item[1] for item in self.judge.asked if item[0] == 'judge' and item[1].purpose == 'watch-notification']


class WindowTests(_WatchCase):
    def setUp(self):
        super().setUp()
        self.judge = watch_engine([False] * 50)
        self.service.use_decision_engine(self.judge)

    def test_the_window_is_bounded_by_its_slots_max_runs_and_limits(self):
        due = self.now + 60
        until = lambda seconds: datetime.fromtimestamp(due + seconds, SEOUL).isoformat()
        self.assertEqual(prep.normalize_window(10, until(3600), None, due, 'Asia/Seoul', self.now), (600, due + 3600, 6), 'slots before the deadline')
        self.assertEqual(prep.normalize_window('10', until(3600), 3, due, 'Asia/Seoul', self.now)[2], 3)
        self.assertEqual(prep.normalize_window(5, until(86400), None, due, 'Asia/Seoul', self.now)[2], prep.MAX_WINDOW_RUNS)
        # #846: about three checks a day up to a deadline weeks away fits the window.
        self.assertEqual(prep.normalize_window(480, until(30 * 86400), None, due, 'Asia/Seoul', self.now)[2], 90)
        for every, span, runs in ((4, 3600, None), (721, 3600, None), (10, 0, None), (10, -600, None),
                                  (10, 31 * 86400 + 60, None), (True, 3600, None), (10, 3600, 0), (10, 3600, 97)):
            with self.assertRaises(prep.PreparationRefusal, msg=(every, span, runs)):
                prep.normalize_window(every, until(span), runs, due, 'Asia/Seoul', self.now)
        with self.assertRaises(prep.PreparationRefusal):
            self.service.preparations.create(kind='reminder', goal='x', due_at=due, timezone='Asia/Seoul', recurrence=None,
                                             channel='telegram', created_from='w', state=prep.STATE_SCHEDULED,
                                             delivery_mode=prep.DELIVERY_WHEN_NEEDED)

    def test_a_watch_runs_at_most_max_runs_then_stops(self):
        self.watched = self.watch(runs=3)
        for index in range(3):
            row = self.run_slot(f'확인 {index}: 아직 괜찮습니다.')
        self.assertEqual((row['state'], row['run_count']), ('delivered', 3))
        for _ in range(12):
            self.now += self.EVERY
            self.assertFalse(self.tick(), 'no fourth run inside the window')
        self.assertEqual(len(self.runs(row['id'])), 3)
        events = [e['trace'] for run in self.runs(row['id']) for e in self.store.task_events(run['id'])
                  if e['tool'] == 'preparation' and 'run' in e['trace']]
        self.assertEqual([(e['run'], e['max_runs']) for e in events], [(1, 3), (2, 3), (3, 3)])

    def test_a_watch_stops_at_its_deadline(self):
        self.watched = self.watch(span=1800)  # slots at +0, +10, +20 minutes; none at the deadline
        slots = []
        while self.service.preparations.get(self.watched['id'])['state'] == 'scheduled':
            slots.append(self.service.preparations.get(self.watched['id'])['due_at'])
            row = self.run_slot('아직 괜찮습니다.')
        end = self.watched['window_end']
        self.assertEqual(len(slots), 3)
        self.assertTrue(all(slot < end for slot in slots))
        self.assertEqual(row['state'], 'delivered')
        self.now = end + 7200
        self.assertFalse(self.tick())
        self.assertEqual(len(self.runs(row['id'])), 3)

    def test_a_window_missed_while_down_expires_without_running(self):
        self.watched = self.watch(span=1800)
        self.now = self.watched['window_end'] + prep.PAST_GRACE_SECONDS + 1
        self.assertTrue(self.tick())
        self.assertEqual(self.service.preparations.get(self.watched['id'])['state'], prep.STATE_EXPIRED)
        self.assertEqual((self.runs(self.watched['id']), self.model_calls), ([], []))
        self.assertFalse(self.tick())

    def test_a_tick_just_after_the_deadline_does_not_run(self):
        self.watched = self.watch(span=1800)
        self.now = self.watched['window_end']
        self.assertTrue(self.tick())
        self.assertEqual(self.service.preparations.get(self.watched['id'])['state'], prep.STATE_EXPIRED)
        self.assertEqual((self.runs(self.watched['id']), self.model_calls), ([], []))

    def test_missed_slots_run_once_then_the_next_slot_after_now(self):
        self.watched = self.watch(span=3600)
        first = self.watched['due_at']
        self.now = first + 3 * self.EVERY + 5  # three slots missed while down
        self.assertTrue(self.tick())
        self.script = [{'content': '아직 괜찮습니다.'}] * 2
        self.assertTrue(self.service.run_one())
        self.assertTrue(self.tick())
        row = self.service.preparations.get(self.watched['id'])
        self.assertEqual((row['state'], row['due_at'], row['run_count']), ('scheduled', first + 4 * self.EVERY, 1))

    def test_a_tick_with_nothing_due_makes_no_model_or_judgment_call(self):
        self.watched = self.watch()
        for _ in range(50):
            self.assertFalse(self.tick())
            self.now += 1
        self.assertEqual((self.model_calls, self.telegram, self.store.jobs(), self.watch_asks()), ([], [], [], []))


class SilentDeliveryTests(_WatchCase):
    def use(self, verdicts):
        self.judge = watch_engine(verdicts)
        self.service.use_decision_engine(self.judge)

    def notifications(self):
        return [text for text in self.texts() if text.startswith('지켜보던 일에서 알려 드립니다')]

    def test_quiet_runs_send_nothing_and_notify_sends_once_per_new_decision(self):
        self.use([False, True, True])
        self.watched = self.watch()
        row = self.run_slot('지금은 평소와 같습니다. 예정대로 출발하면 됩니다.')
        self.assertEqual((row['last_decision'], row['last_decision_reason']), ('quiet', prep.REASON_JUDGED_NOT_NEEDED))
        self.assertEqual(self.telegram, [], 'a quiet run sends nothing, not even a typing indicator')
        [run] = self.runs(row['id'])
        self.assertEqual((run['channel'], run['chat_id'], run['delivery'], run['status']), ('web', None, 'none', 'succeeded'))
        self.assertIn(run['id'], [job['id'] for job in self.store.jobs()], 'a quiet run stays in 작업 현황')
        settled = [e['trace'] for e in self.store.task_events(run['id']) if e['tool'] == 'preparation' and 'decision' in e['trace']]
        self.assertEqual([(e['decision'], e['reason'], e['notification_queued']) for e in settled],
                         [('quiet', prep.REASON_JUDGED_NOT_NEEDED, False)])
        self.assertNotIn('평소와', json.dumps(settled, ensure_ascii=False), 'Evidence records the decision, not the answer')

        row = self.run_slot('정체가 시작됐습니다. 20분 일찍 출발하세요.')
        self.assertEqual((row['last_decision'], row['last_decision_reason']), ('notify', prep.REASON_JUDGED_NEEDED))
        [sent] = self.notifications()
        self.assertIn('20분 일찍 출발하세요', sent)
        self.assertIn('p7q:', json.dumps(self.sends()[-1]['reply_markup']))
        self.service.deliver_notification()
        self.service.deliver_one()
        self.assertEqual(len(self.sends()), 1, 'exactly one message for this decision')

        # The same result again: quiet without asking the judgment.
        asked = len(self.watch_asks())
        row = self.run_slot('정체가 시작됐습니다.  20분 일찍 출발하세요.')
        self.assertEqual((row['last_decision'], row['last_decision_reason']), ('quiet', prep.REASON_UNCHANGED))
        self.assertEqual(len(self.watch_asks()), asked)
        self.assertEqual(len(self.notifications()), 1)

        # A new situation the judgment says is new: one more message.
        row = self.run_slot('정체가 더 심해졌습니다. 40분 일찍 출발하세요.')
        self.assertEqual(row['last_decision'], 'notify')
        self.assertEqual(len(self.notifications()), 2)
        last = self.watch_asks()[-1]
        self.assertEqual(set(last.facts), {'watch_goal', 'run_result', 'last_notification'})
        self.assertIn('20분 일찍', last.facts['last_notification'], 'the judgment sees what the owner was last told')
        self.assertEqual(last.facts['watch_goal'], '출발 시각을 앞당겨야 하는지 확인')

    def test_a_notification_survives_a_restart_and_is_sent_once(self):
        self.use([True])
        self.watched = self.watch()
        row = self.service.preparations.get(self.watched['id'])
        self.now = row['due_at'] + 1
        self.tick()
        self.script = [{'content': '지금 출발하세요.'}] * 2
        self.service.run_one()
        self.tick()  # settled and queued in one transaction, not yet sent
        restarted = self.make_service()
        restarted.recover_interrupted_work()
        for _ in range(3):
            restarted.deliver_notification()
            self.tick(restarted)
        self.assertEqual(len(self.notifications()), 1)

    def test_only_a_confirmed_send_is_what_the_owner_was_last_told(self):
        self.use([True, True])
        self.watched = self.watch()
        row = self.service.preparations.get(self.watched['id'])
        self.now = row['due_at'] + 1
        self.tick()
        self.script = [{'content': '지금 출발하세요.'}] * 2
        self.service.run_one()
        self.tick()  # notify decided and queued
        self.assertIsNone(self.service.preparations.get(row['id'])['last_notified_digest'], 'queued is not sent')
        # The pairing changes before the send: the queued message is cancelled, never sent.
        self.store.put('telegram', {'enabled': True, 'user_id': CHAT, 'generation': 'gen-2', 'cursor': 0})
        self.service.deliver_notification()
        self.assertEqual(self.notifications(), [])
        row = self.service.preparations.get(row['id'])
        self.assertEqual((row['last_notified_digest'], row['last_notified_text']), (None, None))
        # The same result on the next slot is judged again and reaches the re-paired owner.
        self.store.put('telegram', {'enabled': True, 'user_id': CHAT, 'generation': GENERATION, 'cursor': 0})
        asked = len(self.watch_asks())
        row = self.run_slot('지금 출발하세요.')
        self.assertEqual((row['last_decision'], len(self.watch_asks())), ('notify', asked + 1))
        self.assertEqual(len(self.notifications()), 1)
        self.assertEqual(row['last_notified_digest'], prep.text_digest('지금 출발하세요.'))
        self.assertIn('지금 출발하세요', row['last_notified_text'])

    def test_without_a_judgment_the_result_is_sent_once_then_kept_quiet(self):
        self.use([None, None, None])
        self.watched = self.watch()
        self.assertEqual(self.run_slot('상황 A')['last_decision_reason'], prep.REASON_UNJUDGED)
        self.assertEqual(self.run_slot('상황 B')['last_decision_reason'], prep.REASON_UNJUDGED_REPEAT)
        self.assertEqual(self.run_slot('상황 C')['last_decision'], 'quiet')
        self.assertEqual(len(self.notifications()), 1)

    def end_slot(self, status='failed'):
        """Advance to the row's slot, start it, end its Work without a result, settle, deliver."""
        row = self.service.preparations.get(self.watched['id'])
        self.now = max(self.now, row['due_at']) + 1
        self.assertTrue(self.tick())
        run = self.runs(row['id'])[-1]
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status=?,response=NULL,error='조회 서버가 응답하지 않았습니다.',delivery='none' WHERE id=?",
                       (status, run['id']))
        self.assertTrue(self.tick())
        self.service.deliver_notification()
        return self.service.preparations.get(self.watched['id'])

    def test_a_failing_watch_is_told_once_and_asks_nothing(self):
        """#394: a watch whose runs fail must not look like one with nothing to report."""
        self.use([False])
        self.watched = self.watch()
        row = self.end_slot()
        self.assertEqual((row['last_decision'], row['last_decision_reason'], row['state']),
                         ('notify', prep.REASON_RUN_FAILED_TOLD, 'scheduled'))
        [told] = self.notifications()
        self.assertIn('출발 시각을 앞당겨야 하는지 확인', told)
        self.assertIn('조회 서버가 응답하지 않았습니다', told, 'the owner is told why')
        self.assertEqual(self.watch_asks(), [], 'no judgment is asked about a run without a result')
        row = self.end_slot()
        self.assertEqual((row['last_decision'], row['last_decision_reason']), ('quiet', prep.REASON_RUN_FAILED))
        self.assertEqual(len(self.notifications()), 1, 'a failure already told stays quiet')
        row = self.run_slot('지금은 평소와 같습니다.')
        self.assertEqual(row['last_decision_reason'], prep.REASON_JUDGED_NOT_NEEDED)
        row = self.end_slot()
        self.assertEqual(row['last_decision_reason'], prep.REASON_RUN_FAILED_TOLD)
        self.assertEqual(len(self.notifications()), 2, 'a failure after a result is told again')

    def test_a_run_the_owner_stopped_is_quiet(self):
        self.use([])
        self.watched = self.watch()
        row = self.end_slot(status='cancelled')
        self.assertEqual((row['last_decision'], row['last_decision_reason']), ('quiet', prep.REASON_RUN_STOPPED))
        self.assertEqual((self.watch_asks(), self.notifications()), ([], []))
        row = self.end_slot()
        self.assertEqual(row['last_decision_reason'], prep.REASON_RUN_FAILED_TOLD, 'a stop does not hide a later failure')

    def test_the_owner_stops_a_watch_from_its_notification(self):
        self.use([True])
        self.watched = self.watch()
        self.run_slot('지금 출발하세요.')
        with self.store.db() as db:
            notification = dict(db.execute('SELECT * FROM telegram_notifications WHERE kind=?', (prep.NOTIFY_KIND,)).fetchone())
        self.tap(f"p7q:{notification['id']}:stop", notification['message_id'] + 1)
        self.assertEqual(self.service.preparations.get(self.watched['id'])['state'], 'scheduled', 'another message stops nothing')
        self.tap(f"p7q:{notification['id']}:stop", notification['message_id'])
        self.assertEqual(self.service.preparations.get(self.watched['id'])['state'], 'cancelled')
        self.assertIn('editMessageReplyMarkup', [method for method, _body in self.telegram])
        self.now += 3 * 3600
        self.assertFalse(self.tick())
        self.assertEqual(len(self.runs(self.watched['id'])), 1)

    def test_a_notification_queued_before_a_cancel_is_not_sent(self):
        self.use([True])
        self.watched = self.watch()
        row = self.service.preparations.get(self.watched['id'])
        self.now = row['due_at'] + 1
        self.tick()
        self.script = [{'content': '지금 출발하세요.'}] * 2
        self.service.run_one()
        self.tick()
        self.service.preparation_request({'operation': 'cancel', 'id': row['id']})
        self.service.deliver_notification()
        self.assertEqual(self.notifications(), [])

    def test_the_judgment_is_a_bounded_proposition_not_a_rule(self):
        from personal_agent.conversation_handoff import WATCH_NOTIFY_PROPOSITION
        engine_ = watch_engine([True])
        judged = ConversationJudgments(engine_).watch_notification_needed('목표', '결과', None, work_id='w')
        self.assertEqual(judged.outcome, 'yes')
        [(_kind, context, proposition)] = engine_.asked
        self.assertEqual(proposition, WATCH_NOTIFY_PROPOSITION)
        self.assertEqual(context.facts['last_notification'], 'none')


class WatchAcceptanceTests(_WatchCase):
    def propose_watch(self, preparation):
        self.judge = watch_engine([], preparation=preparation)
        self.service.use_decision_engine(self.judge)
        self.script = [{'content': None, 'tool_calls': [call('1', 'schedule_preparation', kind='prepare',
                                                             goal='출발 시각을 앞당겨야 하는지 확인', due=self.due_iso(600),
                                                             every_minutes='10', until=self.due_iso(600 + 7200), max_runs='6',
                                                             delivery='when_needed')]},
                       {'content': '7시부터 10분마다 확인하고 필요할 때만 알려 드릴게요.'}]
        work = self.receive('9시 전에 출발 시간 당겨야 하면 알려줘')
        [row] = self.rows()
        [result] = [json.loads(m['content']) for m in self.model_calls[-1]['messages'] if m['role'] == 'tool']
        return work, row, result

    def test_a_watch_is_proposed_and_accepted_with_the_existing_flow(self):
        work, row, result = self.propose_watch(preparation=False)
        self.assertEqual((row['state'], row['recurrence'], row['every_seconds'], row['max_runs'], row['delivery_mode']),
                         ('proposed', prep.RECURRENCE_WINDOW, 600, 6, 'when_needed'))
        self.assertEqual((result['every_minutes'], result['max_runs'], result['delivery']), (10, 6, 'when_needed'))
        self.service.deliver_one()
        self.service.deliver_notification()
        proposal = self.sends()[-1]['text']
        # #393: the window as enforced - before its deadline, at most max_runs - with no repeated date or zone.
        end = datetime.fromtimestamp(row['window_end'], SEOUL).strftime('%H:%M')
        self.assertIn(f'{end} 전까지, 10분마다 최대 6번', proposal)
        self.assertIn('알릴 일이 있을 때만', proposal)
        self.assertNotIn('Asia/Seoul', proposal, 'the owner\'s own zone is not repeated')
        with self.store.db() as db:
            notification = dict(db.execute("SELECT * FROM telegram_notifications WHERE job_id=? AND kind='preparation_proposed'",
                                           (work,)).fetchone())
        self.tap(f"p7p:{notification['id']}:accept", notification['message_id'])
        self.assertEqual(self.service.preparations.get(row['id'])['state'], 'scheduled')
        listed = self.service.preparations_status()['preparations'][0]
        self.assertEqual((listed['every_minutes'], listed['max_runs'], listed['run_count'], listed['delivery']),
                         (10, 6, 0, 'when_needed'))
        self.assertIn('Asia/Seoul', listed['until_local'])

    def test_the_model_is_told_its_last_check_and_until_is_exclusive(self):
        """#404: a check at the until time never runs; the result names the real last scheduled check."""
        _work, row, result = self.propose_watch(preparation=False)
        self.assertEqual(result['last_check'], prep.iso(row['due_at'] + 5 * 600, 'Asia/Seoul'), 'six runs: due + 50 min')
        self.judge = watch_engine([], preparation=False)
        self.service.use_decision_engine(self.judge)
        due = self.now + 600
        for until, expected in ((due + 3600, due + 3000), (due + 3660, due + 3600)):
            self.script = [{'content': None, 'tool_calls': [call('1', 'schedule_preparation', kind='prepare', goal=f'확인 {until}',
                                                                 due=self.due_iso(600), every_minutes='10',
                                                                 until=datetime.fromtimestamp(until, SEOUL).isoformat(),
                                                                 delivery='when_needed')]},
                           {'content': '지켜볼까요?'}]
            self.receive('확인해 줘')
            [result] = [json.loads(m['content']) for m in self.model_calls[-1]['messages'] if m['role'] == 'tool']
            self.assertEqual(result['last_check'], prep.iso(expected, 'Asia/Seoul'))

    def test_a_three_a_day_watch_until_a_deadline_is_accepted_as_a_proposal(self):
        """#846: the guidance's default cadence (every_minutes ~420 with until) is within the validated window."""
        self.judge = watch_engine([], preparation=False)
        self.service.use_decision_engine(self.judge)
        self.script = [{'content': None, 'tool_calls': [call('1', 'schedule_preparation', kind='prepare',
                                                             goal='바뀌었는지 확인', due=self.due_iso(600),
                                                             every_minutes='420', until=self.due_iso(600 + 20 * 3600),
                                                             delivery='when_needed')]},
                       {'content': '하루 세 번 확인하고 필요할 때만 알려 드릴까요?'}]
        self.receive('바뀌면 알려줘')
        [row] = self.rows()
        [result] = [json.loads(m['content']) for m in self.model_calls[-1]['messages'] if m['role'] == 'tool']
        self.assertEqual((row['state'], row['every_seconds'], row['delivery_mode']), ('proposed', 420 * 60, 'when_needed'))
        self.assertEqual((result['every_minutes'], result['delivery']), (420, 'when_needed'))
        self.assertNotIn('refused_because', json.dumps(result))

    def proposal_notification(self, work):
        with self.store.db() as db:
            return dict(db.execute("SELECT * FROM telegram_notifications WHERE job_id=? AND kind='preparation_proposed'",
                                   (work,)).fetchone())

    def test_a_watch_accepted_after_its_window_ended_is_not_started(self):
        """#395: a late yes never reads as a running watch."""
        work, row, _result = self.propose_watch(preparation=False)
        self.service.deliver_one()
        self.service.deliver_notification()
        notification = self.proposal_notification(work)
        self.now = row['window_end'] + 60
        self.tap(f"p7p:{notification['id']}:accept", notification['message_id'])
        self.assertEqual(self.service.preparations.get(row['id'])['state'], prep.STATE_CANCELLED)
        edit = [body for method, body in self.telegram if method == 'editMessageText'][-1]
        self.assertIn('시작하지 않았어요', edit['text'])
        self.assertNotIn('예약했어요', edit['text'])
        self.assertEqual(edit['reply_markup'], {'inline_keyboard': []})
        self.assertFalse(self.tick())
        self.assertEqual(self.runs(row['id']), [])

    def test_a_late_yes_starts_only_the_watches_still_open(self):
        """#395: of one message's proposals, the ended watch is not started and the open one is."""
        self.service.use_decision_engine(watch_engine([], preparation=False))
        self.script = [{'content': '알겠습니다.'}]
        work = self.receive('두 가지 지켜봐 줘')
        made = {}
        for name, span in (('ENDS', 900), ('OPEN', 7200)):
            due = self.now + 600
            window = prep.normalize_window(10, datetime.fromtimestamp(due + span, SEOUL).isoformat(), None, due,
                                           'Asia/Seoul', self.now)
            made[name] = self.service.preparations.create(
                kind='prepare', goal=name + ' 확인', due_at=due, timezone='Asia/Seoul', recurrence=None, channel='telegram',
                created_from=work, state=prep.STATE_PROPOSED, window=window, delivery_mode=prep.DELIVERY_WHEN_NEEDED)
        self.service.queue_preparation_proposal(self.store.job(work))
        self.service.deliver_one()
        self.service.deliver_notification()
        notification = self.proposal_notification(work)
        self.now = made['ENDS']['window_end'] + 1
        self.tap(f"p7p:{notification['id']}:accept", notification['message_id'])
        get = self.service.preparations.get
        self.assertEqual((get(made['ENDS']['id'])['state'], get(made['OPEN']['id'])['state']),
                         (prep.STATE_CANCELLED, prep.STATE_SCHEDULED))
        edit = [body for method, body in self.telegram if method == 'editMessageText'][-1]
        self.assertIn('예약했어요', edit['text'])
        self.assertIn('시작하지 않았어요', edit['text'])
        [[button]] = edit['reply_markup']['inline_keyboard']
        self.tap(button['callback_data'], notification['message_id'])
        self.assertEqual(get(made['OPEN']['id'])['state'], prep.STATE_CANCELLED, 'the cancel button covers what was accepted')

    def test_accept_refuses_only_an_ended_window(self):
        """#395: the refusal is in the UPDATE; a one-shot proposal past its time is unaffected."""
        once = self.service.preparations.create(kind='prepare', goal='한 번', due_at=self.now + 60, timezone='Asia/Seoul',
                                                recurrence=None, channel='web', created_from='w', state=prep.STATE_PROPOSED)
        due = self.now + 60
        window = prep.normalize_window(10, datetime.fromtimestamp(due + 1200, SEOUL).isoformat(), None, due,
                                       'Asia/Seoul', self.now)
        watch = self.service.preparations.create(kind='prepare', goal='지켜보기', due_at=due, timezone='Asia/Seoul',
                                                 recurrence=None, channel='web', created_from='w', state=prep.STATE_PROPOSED,
                                                 window=window)
        self.now = watch['window_end']
        with self.assertRaises(prep.PreparationRefusal) as refused:
            self.service.preparations.accept(watch['id'], prep.ACCEPTED_OWNER_SETTINGS)
        self.assertEqual(refused.exception.code, 'expired')
        self.assertEqual(self.service.preparations.get(watch['id'])['state'], prep.STATE_PROPOSED)
        self.assertEqual(self.service.preparations.accept(once['id'], prep.ACCEPTED_OWNER_SETTINGS)['state'],
                         prep.STATE_SCHEDULED)
        with self.assertRaises(prep.PreparationRefusal) as again:
            self.service.preparations.accept(once['id'], prep.ACCEPTED_OWNER_SETTINGS)
        self.assertEqual(again.exception.code, 'not_proposed')

    def test_the_owners_own_request_schedules_it(self):
        _work, row, _result = self.propose_watch(preparation=True)
        self.assertEqual((row['state'], row['accepted_by']), ('scheduled', 'owner-request'))

    def test_bad_windows_are_refused_to_the_model(self):
        self.judge = watch_engine([], preparation=True)
        self.service.use_decision_engine(self.judge)
        self.script = [{'content': None, 'tool_calls': [
            call('1', 'schedule_preparation', kind='prepare', goal='확인', due=self.due_iso(600), every_minutes='1',
                 until=self.due_iso(1200)),
            call('2', 'schedule_preparation', kind='reminder', goal='확인', due=self.due_iso(600), delivery='when_needed'),
            call('3', 'schedule_preparation', kind='prepare', goal='확인', due=self.due_iso(600), recurrence='daily',
                 every_minutes='10', until=self.due_iso(1200))]}, {'content': '예약하지 못했습니다.'}]
        self.receive('확인해줘')
        results = [m['content'] for m in self.model_calls[-1]['messages'] if m['role'] == 'tool']
        self.assertEqual(len(results), 3)
        self.assertTrue(all('every_minutes' in text or 'when_needed' in text for text in results), results)
        self.assertEqual(self.rows(), [])

    def test_rejected_watch_cannot_fan_out_into_same_goal_daily_preparations(self):
        """#860: correct one rejected watch; never replace it with dated one-shot rows."""
        goal = '같은 목표의 변동을 확인하고 달라질 때 알리기'
        # #173 slice B: the judgment, not a word table, reads the dated rewordings as the refused goal.
        self.judge = watch_engine([], preparation=False, same_goals={f'10월 4일까지 {goal} 알려 주세요': True})
        self.service.use_decision_engine(self.judge)
        self.script = [{'content': None, 'tool_calls': [
            call('window-bad', 'schedule_preparation', kind='prepare', goal=goal, due=self.due_iso(600),
                 every_minutes='1', until=self.due_iso(600 + 7200), delivery='when_needed')]}]
        # Keep all five fan-out counterexamples, in one worker turn, so the
        # cumulative request budget leaves room to observe the finish refusal.
        self.script.append({'content': None, 'tool_calls': [
                call(f'daily-{index}', 'schedule_preparation', kind='prepare',
                     goal=f'10월 4일까지 {goal} 알려 주세요',
                     due=self.due_iso(600 + index * 86400), recurrence='daily', delivery='when_needed')
                for index in range(5)]})
        self.script.extend([
            {'content': None, 'tool_calls': [
                call('window-corrected', 'schedule_preparation', kind='prepare', goal=goal,
                     due=self.due_iso(600), every_minutes='420', until=self.due_iso(600 + 4 * 86400),
                     delivery='when_needed')]},
            finish('finish-watch', 'window-corrected', summary='반복 확인을 제안했습니다.')
        ])

        self.receive('마감 때까지 변동을 매일 확인해줘')

        results = [json.loads(message['content']) for message in self.model_calls[-1]['messages']
                   if message['role'] == 'tool']
        rows = self.rows()
        self.assertEqual(len(rows), 1, ([(row['goal_text'], row['state'], row['recurrence'], row['every_seconds']) for row in rows], results))
        [row] = rows
        self.assertEqual((row['goal_text'], row['state'], row['recurrence'], row['every_seconds'], row['delivery_mode']),
                         (goal, 'proposed', prep.RECURRENCE_WINDOW, 420 * 60, prep.DELIVERY_WHEN_NEEDED))
        self.assertEqual(len(results), 8)  # includes the final finish-tool receipt
        self.assertIn('invalid_window', json.dumps(results[0]))
        self.assertTrue(all('window_retry_required' in json.dumps(result) for result in results[1:6]))
        self.assertEqual(results[6]['preparation_id'], row['id'])
        self.assertTrue(results[6]['requires_owner_acceptance'])
        self.assertEqual(results[7]['code'], 'failed_ref', 'a proposal is not an accepted schedule')

    def test_failed_watch_guard_is_rebuilt_from_tool_events_after_work_resume(self):
        """#860 review P2: the guard survives a resumable Work and service restart."""
        job_id='resumable-watch-work'
        details=[
            ('running',{'call_id':'bad-window','host_action':'schedule_preparation',
                        'arguments':{'goal':'가격 변동 확인'}}),
            ('failed',{'call_id':'bad-window','code':'invalid_window'}),
        ]
        with self.store.db() as db:
            for status,detail in details:
                db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                           (job_id,'schedule_preparation',status,json.dumps(detail,ensure_ascii=False),self.now))

        restarted=self.make_service()
        job={'id':job_id,'message':'가격 변동을 확인해줘'}
        scheduler=restarted.preparation_scheduler(job,job['message'])
        with self.assertRaises(ToolError) as caught:
            scheduler({'kind':'prepare','goal':'10월 4일까지 가격 변동을 확인해 주세요',
                       'due':self.due_iso(600),'recurrence':'daily','delivery':'when_needed'})
        self.assertEqual(caught.exception.code,'window_retry_required')
        self.assertEqual(self.rows(),[])

    def refused_watch(self, job_id, goal):
        with self.store.db() as db:
            for status, detail in (('running', {'call_id': 'bad-window', 'host_action': 'schedule_preparation',
                                                'arguments': {'goal': goal}}),
                                   ('failed', {'call_id': 'bad-window', 'code': 'invalid_window'})):
                db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                           (job_id, 'schedule_preparation', status, json.dumps(detail, ensure_ascii=False), self.now))

    def test_whether_a_goal_repeats_a_refused_watch_is_judged_not_word_matched(self):
        """#173 slice B: no particle/filler table decides it; the judgment does, in context."""
        self.refused_watch('work-1', '가격 변동 확인')
        reworded, shared_words = '10월 4일까지 가격이 바뀌는지 알려 주세요', '주가 변동 확인'
        self.judge = watch_engine([], same_goals={reworded: True, shared_words: False})
        self.service.use_decision_engine(self.judge)
        # The same goal in different wording (no shared word with the refused goal) is the same.
        self.assertTrue(self.service.repeats_refused_watch('work-1', reworded))
        # A different goal sharing the words "변동 확인" is not.
        self.assertFalse(self.service.repeats_refused_watch('work-1', shared_words))
        asked = [item for item in self.judge.asked if item[1].purpose == 'same-preparation-goal']
        self.assertEqual([item[1].facts for item in asked],
                         [{'proposed_goal': reworded, 'refused_goals': '가격 변동 확인'},
                          {'proposed_goal': shared_words, 'refused_goals': '가격 변동 확인'}])

    def test_the_refused_goal_itself_needs_no_judgment_and_nothing_refused_asks_nothing(self):
        self.refused_watch('work-1', '가격 변동 확인')
        self.judge = watch_engine([])
        self.service.use_decision_engine(self.judge)
        self.assertTrue(self.service.repeats_refused_watch('work-1', '가격 변동 확인'))
        self.assertFalse(self.service.repeats_refused_watch('work-2', '가격 변동 확인'))
        self.assertEqual([item for item in self.judge.asked if item[1].purpose == 'same-preparation-goal'], [])

    def test_an_unavailable_judgment_keeps_the_retry_guard_closed(self):
        self.refused_watch('work-1', '가격 변동 확인')
        self.judge = watch_engine([])
        self.service.use_decision_engine(self.judge)
        self.assertTrue(self.service.repeats_refused_watch('work-1', '다른 약속 알림'))

    def test_failed_watch_retry_guard_does_not_block_a_different_explicit_goal(self):
        """#860: the guard is scoped to the rejected watch goal, not the whole Work."""
        self.judge = watch_engine([], preparation=False, same_goals={'다른 약속 알림': False})
        self.service.use_decision_engine(self.judge)
        self.script = [
            {'content': None, 'tool_calls': [
                call('window-bad', 'schedule_preparation', kind='prepare', goal='가격 변동 확인',
                     due=self.due_iso(600), every_minutes='1', until=self.due_iso(600 + 7200),
                     delivery='when_needed')]},
            {'content': None, 'tool_calls': [
                call('other-goal', 'schedule_preparation', kind='reminder', goal='다른 약속 알림',
                     due=self.due_iso(3600))]},
            finish('finish-other', 'other-goal', summary='두 번째 준비는 제안했습니다.')
        ]

        self.receive('가격 변동을 마감까지 살펴보고 별도로 약속도 알려줘')

        [row] = self.rows()
        self.assertEqual((row['goal_text'], row['kind'], row['state']), ('다른 약속 알림', 'reminder', 'proposed'))
        results = [json.loads(message['content']) for message in self.model_calls[-1]['messages']
                   if message['role'] == 'tool']
        self.assertEqual([result['preparation_id'] for result in results if 'preparation_id' in result], [row['id']])


class WatchAcceptanceAcrossMidnightTests(WatchAcceptanceTests):
    """#9: the same watch requests 30 seconds before midnight (Asia/Seoul), so each first slot is tomorrow."""
    START = SEOUL_MIDNIGHT - 30

    def test_the_first_slot_falls_after_midnight(self):
        self.assertEqual(self.due_iso(600)[:10], '2026-10-15')
        self.assertEqual(datetime.fromtimestamp(self.now, SEOUL).date().isoformat(), '2026-10-14')


class OwnerOfferTests(_WatchCase):
    """#393: the owner reads and accepts an offer; each run gets its brief."""

    TITLE = '5종목 반등 매수 타이밍 지켜보기'
    OFFER = '예산은 최대 10만 원, 처음엔 1주\n한 번에 잃을 수 있는 돈은 1,000원 이하\n주문은 하지 않고, 살 때마다 직접 승인받아요'
    BRIEF = '매회 잔고·오늘주문·미체결·완성분봉·최신호가를 조회하고 비용차감 손익비 2 이상만 제안한다. ' * 12

    def propose(self, **extra):
        self.judge = watch_engine([], preparation=False)
        self.service.use_decision_engine(self.judge)
        args = dict(kind='prepare', goal=self.BRIEF.strip(), due=self.due_iso(600), every_minutes='5',
                    until=self.due_iso(600 + 80 * 60), max_runs='16', delivery='when_needed',
                    title=self.TITLE, offer=self.OFFER)
        args.update(extra)
        self.script = [{'content': None, 'tool_calls': [call('1', 'schedule_preparation', **args)]},
                       {'content': '이렇게 지켜볼까요?'}]
        work = self.receive('오늘 저녁 반등 매수 타이밍 봐줘')
        [result] = [json.loads(m['content']) for m in self.model_calls[-1]['messages'] if m['role'] == 'tool']
        return work, result

    def proposal_message(self, work):
        self.service.deliver_one()
        self.service.deliver_notification()
        with self.store.db() as db:
            notification = dict(db.execute("SELECT * FROM telegram_notifications WHERE job_id=? AND kind='preparation_proposed'",
                                           (work,)).fetchone())
        [sent] = [body for body in self.sends() if body.get('message_id') is None and 'reply_markup' in body
                  and 'p7p:' in json.dumps(body['reply_markup'])]
        return notification, sent

    def test_the_owner_reads_the_offer_and_the_brief_is_folded(self):
        work, result = self.propose()
        [row] = self.rows()
        self.assertEqual((row['title_text'], row['offer_text'], row['goal_text']), (self.TITLE, self.OFFER, self.BRIEF.strip()))
        self.assertGreater(len(row['goal_text']), prep.GOAL_MAX_CHARS, 'a prepare brief is not cut to a reminder length')
        _notification, sent = self.proposal_message(work)
        text = sent['text']
        self.assertEqual(sent.get('parse_mode'), 'HTML')
        self.assertTrue(text.startswith(f'<b>{self.TITLE}</b>\n'), text)
        end = datetime.fromtimestamp(row['window_end'], SEOUL).strftime('%H:%M')
        self.assertIn(f'{end} 전까지, 5분마다 최대 16번 · 알릴 일이 있을 때만 알려요', text)
        self.assertIn('한 번에 잃을 수 있는 돈은 1,000원 이하', text)
        brief_at = text.index('<blockquote expandable>')
        self.assertLess(text.index('주문은 하지 않고'), brief_at, 'the offer comes before the folded brief')
        self.assertIn('완성분봉', text[brief_at:])
        self.assertNotIn('완성분봉', text[:brief_at], 'the method is not in what the owner reads first')
        self.assertNotIn(prep.PROPOSAL_HEADER, text)
        self.assertNotIn(prep.PROPOSAL_QUESTION, text)
        # The judged-answer path and other surfaces get the same offer first, the brief under its label.
        plain = prep.proposal_text([row], 0, 'Asia/Seoul')
        self.assertLess(plain.index(self.OFFER.splitlines()[-1]), plain.index(prep.BRIEF_LABEL))

    def test_ai_offer_supplies_the_only_question_in_text_and_html(self):
        work, _ = self.propose(offer='조건에 맞는 결과가 있으면 알려 드릴까요?')
        [row] = self.rows()
        for render in (prep.proposal_text, prep.proposal_html):
            text = render([row], 0)
            self.assertEqual(text.count('?'), 1, text)
            self.assertIn(row['offer_text'], text)
            self.assertNotIn(prep.PROPOSAL_QUESTION, text)
        _, sent = self.proposal_message(work)
        self.assertEqual([b['text'] for b in sent['reply_markup']['inline_keyboard'][0]],
                         [msg('button.preparation_accept'), msg('button.preparation_decline')])
        self.assertEqual(msg('button.accept'), '수락')

    def test_proposal_button_labels_on_slack_and_terminal(self):
        from types import SimpleNamespace
        notice = {'id': 'n1', 'kind': 'preparation_proposed', 'job_id': None}
        posted = []
        def post(**kwargs):
            posted.append(kwargs)
            return {'ok': True, 'ts': '123.456'}
        labels = self.service.preparations_status()['management']
        for key, message_id in {'accept': 'button.preparation_accept', 'decline': 'button.preparation_decline',
                                **{'delivery_' + mode: 'preparations.delivery_' + mode
                                   for mode in ('send', 'keep', 'when_needed')}}.items():
            for lang in ('ko', 'en'):
                self.assertEqual(labels[key][lang], msg(message_id, lang))
        surface = self.service.slack_surface
        surface.client = lambda cfg: SimpleNamespace(chat_postMessage=post)
        surface._post_notice({'channel_id': 'C1'}, '제안', notice, {})
        self.assertEqual([button['text']['text'] for button in posted[0]['blocks'][-1]['elements']],
                         [msg('button.preparation_accept'), msg('button.preparation_decline')])
        surface = self.service.terminal_surface
        generation = surface.attach(lambda text: None)
        surface._post_notice({'generation': generation}, '제안', notice, {})
        self.assertEqual([msg(key) for key in surface.take_request()['labels']],
                         [msg('button.preparation_accept'), msg('button.preparation_decline')])

    def test_accepting_binds_the_offer_that_was_shown(self):
        work, _result = self.propose()
        notification, _sent = self.proposal_message(work)
        [row] = self.rows()
        with self.store.db() as db:
            db.execute('UPDATE preparations SET offer_text=? WHERE id=?', ('주문까지 해 드려요', row['id']))
        self.tap(f"p7p:{notification['id']}:accept", notification['message_id'])
        self.assertEqual(self.service.preparations.get(row['id'])['state'], 'proposed', 'a changed offer is not accepted')
        with self.store.db() as db:
            db.execute('UPDATE preparations SET offer_text=? WHERE id=?', (self.OFFER, row['id']))
        self.tap(f"p7p:{notification['id']}:accept", notification['message_id'])
        self.assertEqual(self.service.preparations.get(row['id'])['state'], 'scheduled')

    def test_a_row_without_an_offer_keeps_its_digest(self):
        """An earlier message's buttons keep working: its rows' digest material is unchanged."""
        row = {'id': 'a', 'kind': 'prepare', 'goal_text': 'g', 'due_at': 1.0, 'recurrence': None, 'every_seconds': None,
               'window_end': None, 'max_runs': None, 'delivery_mode': None, 'title_text': None, 'offer_text': None}
        import hashlib
        before = json.dumps(sorted([('a', 'prepare', 'g', 1.0, None, None, None, None, None)]), ensure_ascii=False)
        self.assertEqual(prep.digest([row]), hashlib.sha256(before.encode()).hexdigest()[:32])
        self.assertNotEqual(prep.digest([{**row, 'offer_text': 'o'}]), prep.digest([row]))

    def test_each_run_works_within_the_accepted_offer_and_is_named_by_it(self):
        self.judge = watch_engine([True])
        self.service.use_decision_engine(self.judge)
        due = self.now + 60
        window = prep.normalize_window(10, datetime.fromtimestamp(due + 3600, SEOUL).isoformat(), None, due, 'Asia/Seoul', self.now)
        self.watched = self.service.preparations.create(
            kind='prepare', goal='매회 시세를 조회한다', due_at=due, timezone='Asia/Seoul', recurrence=None, channel='telegram',
            created_from='settings', state=prep.STATE_SCHEDULED, accepted_by=prep.ACCEPTED_OWNER_SETTINGS, window=window,
            delivery_mode=prep.DELIVERY_WHEN_NEEDED, title=self.TITLE, offer=self.OFFER)
        self.run_slot('한화오션 1주, 지금 살 만해요.')
        run_prompt = json.dumps(self.model_calls[0]['messages'], ensure_ascii=False)
        self.assertIn('한 번에 잃을 수 있는 돈은 1,000원 이하', run_prompt, 'the run sees what the owner accepted')
        self.assertIn('매회 시세를 조회한다', run_prompt)
        self.assertNotIn('마다 최대', run_prompt, 'no schedule line re-rendered from the advanced due time')
        [asked] = [item[1] for item in self.judge.asked if item[0] == 'judge' and item[1].purpose == 'watch-notification']
        self.assertIn('1,000원 이하', asked.facts['accepted_offer'])
        [told] = [text for text in self.texts() if text.startswith('지켜보던 일에서 알려 드립니다')]
        self.assertTrue(told.startswith(f'지켜보던 일에서 알려 드립니다 ({self.TITLE}).'), told)
        listed = self.service.preparations_status()['preparations'][0]
        self.assertEqual((listed['title'], listed['offer']), (self.TITLE, self.OFFER))

    def test_a_late_start_never_runs_past_what_the_owner_read(self):
        """#399 review: max_runs below the slot count, accepted late: runs stay within the stated bound."""
        self.judge = watch_engine([False] * 10)
        self.service.use_decision_engine(self.judge)
        due = self.now + 60
        until = due + 80 * 60
        window = prep.normalize_window(5, datetime.fromtimestamp(until, SEOUL).isoformat(), 2, due, 'Asia/Seoul', self.now)
        self.watched = self.service.preparations.create(
            kind='prepare', goal='시세 확인', due_at=due, timezone='Asia/Seoul', recurrence=None, channel='telegram',
            created_from='settings', state=prep.STATE_SCHEDULED, accepted_by=prep.ACCEPTED_OWNER_SETTINGS, window=window,
            delivery_mode=prep.DELIVERY_WHEN_NEEDED, title=self.TITLE, offer=self.OFFER)
        shown = prep.schedule_text(self.watched, 'Asia/Seoul')
        end = datetime.fromtimestamp(until, SEOUL).strftime('%H:%M')
        self.assertIn(f'{end} 전까지, 5분마다 최대 2번', shown)
        self.now = due + 15 * 60  # three slots late
        starts = []
        for _ in range(6):
            row = self.service.preparations.get(self.watched['id'])
            if row['state'] != prep.STATE_SCHEDULED:
                break
            self.now = max(self.now, row['due_at']) + 1
            if self.tick():
                starts.append(self.now)
                self.script = [{'content': '평소와 같아요.'}] * 2
                self.service.run_one()
                self.tick()
        self.assertEqual(len(starts), 2, 'at most the stated number of runs')
        self.assertTrue(all(at < until for at in starts), 'never at or after the stated deadline')

    def test_an_offer_out_of_shape_is_refused_to_the_model_and_a_secret_never_stays(self):
        _work, result = self.propose(title='가' * (prep.TITLE_MAX_CHARS + 1))
        self.assertEqual(result['code'], 'invalid_offer')
        self.assertEqual(self.rows(), [])
        _work, result = self.propose(offer='\n'.join(['한 줄'] * (prep.OFFER_MAX_LINES + 1)))
        self.assertEqual((result['code'], self.rows()), ('invalid_offer', []))
        self.propose(offer=f'토큰 {SECRET} 으로 조회해요\n주문은 하지 않아요')
        [row] = self.rows()
        self.assertNotIn(SECRET, row['offer_text'])
        with self.store.db() as db:
            events = ' '.join(r['detail'] or '' for r in db.execute('SELECT detail FROM tool_events'))
        self.assertNotIn(SECRET, events)

    def test_a_reminder_ignores_an_offer_and_keeps_its_length(self):
        self.script = [{'content': None, 'tool_calls': [call('1', 'schedule_preparation', kind='reminder', goal='약 먹기',
                                                             due=self.due_iso(600), title='제목', offer='설명')]},
                       {'content': '알려 드릴게요.'}]
        self.receive('10분 뒤에 약 먹으라고 알려줘')
        [row] = self.rows()
        self.assertEqual((row['title_text'], row['offer_text']), (None, None))
        with self.assertRaises(prep.PreparationRefusal):
            prep.normalize_goal('가' * (prep.GOAL_MAX_CHARS + 1))


class CrossWorkProposalTests(_Case):
    """#574: one waiting proposal per preparation, whichever Work proposed it first."""

    def propose(self, work, *, due=600, recurrence=None, window=None, state=prep.STATE_PROPOSED, goal='매일 아침 일정 정리'):
        return self.service.preparations.create(
            kind='prepare', goal=goal, due_at=self.now + due, timezone='Asia/Seoul', recurrence=recurrence,
            channel='web', created_from=work, state=state,
            accepted_by=prep.ACCEPTED_OWNER_SETTINGS if state == prep.STATE_SCHEDULED else None, window=window)

    def test_an_identical_proposal_from_another_work_is_the_same_row(self):
        first = self.propose('work-1', recurrence='daily')
        again = self.propose('work-2', recurrence='daily')
        self.assertEqual(again['id'], first['id'])
        self.assertTrue(again['already_proposed'])
        self.assertNotIn('already_proposed', first)
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.service.preparations.proposed_from('work-2'), [], 'the second Work offers nothing new')

    def test_a_scheduled_row_from_another_work_is_not_reused(self):
        first = self.propose('work-1', recurrence='daily', state=prep.STATE_SCHEDULED)
        again = self.propose('work-2', recurrence='daily')
        self.assertNotEqual(again['id'], first['id'])
        self.assertNotIn('already_proposed', again)
        self.assertEqual(len(self.rows()), 2)

    def test_a_different_due_recurrence_or_window_is_a_new_proposal(self):
        base = self.propose('work-1', recurrence='daily')
        window = prep.normalize_window(360, self.due_iso(3 * 86400), None, self.now + 600, 'Asia/Seoul', self.now)
        others = [self.propose('work-2', due=1200, recurrence='daily'),
                  self.propose('work-3', recurrence='weekly'),
                  self.propose('work-4'),
                  self.propose('work-5', window=window)]
        self.assertEqual(len({row['id'] for row in [base, *others]}), 5)
        self.assertFalse(any(row.get('already_proposed') for row in others))
        shorter = prep.normalize_window(360, self.due_iso(2 * 86400), None, self.now + 600, 'Asia/Seoul', self.now)
        self.assertNotEqual(self.propose('work-6', window=shorter)['id'], others[-1]['id'], 'a different window is new')
        self.assertEqual(self.propose('work-7', window=window)['id'], others[-1]['id'], 'the same window is the same watch')

    def schedule(self, work, message):
        job = {'id': work, 'message': message}
        return self.service.preparation_scheduler(job, message)(
            {'kind': 'prepare', 'goal': '매일 아침 일정 정리', 'due': self.due_iso(600), 'recurrence': 'daily'})

    def test_the_tool_result_says_the_proposal_already_existed(self):
        self.judge = engine(preparation=False)
        self.service.use_decision_engine(self.judge)
        first = self.schedule('work-1', '아침마다 일정 정리해 줄래?')
        again = self.schedule('work-2', '아침 일정 정리 얘기 기억해?')
        self.assertFalse(first['already_proposed'])
        self.assertTrue(again['already_proposed'])
        self.assertEqual(again['preparation_id'], first['preparation_id'])
        self.assertTrue(again['requires_owner_acceptance'])
        self.assertIn('이미 제안', again['next_step'])
        [row] = self.rows()
        self.assertEqual(row['state'], prep.STATE_PROPOSED)

    def test_the_owners_own_request_accepts_the_existing_proposal(self):
        self.judge = engine(preparation=False)
        self.service.use_decision_engine(self.judge)
        first = self.schedule('work-1', '아침마다 일정 정리해 줄래?')
        self.judge = engine(preparation=True)
        self.service.use_decision_engine(self.judge)
        again = self.schedule('work-2', '매일 아침 일정 정리 예약해 줘')
        self.assertEqual(again['preparation_id'], first['preparation_id'])
        self.assertTrue(again['already_proposed'])
        self.assertTrue(again['scheduled'])
        self.assertEqual(again['accepted_by'], prep.ACCEPTED_OWNER_REQUEST)
        [row] = self.rows()
        self.assertEqual((row['state'], row['accepted_by'], row['created_from']),
                         (prep.STATE_SCHEDULED, prep.ACCEPTED_OWNER_REQUEST, 'work-1'))

    def test_a_too_long_goal_refusal_states_the_kinds_limit_and_one_retry(self):
        job = {'id': 'work-1', 'message': '알려줘'}
        scheduler = self.service.preparation_scheduler(job, job['message'])
        for kind, limit in (('reminder', prep.GOAL_MAX_CHARS), ('prepare', prep.BRIEF_MAX_CHARS)):
            with self.assertRaises(ToolError) as caught:
                scheduler({'kind': kind, 'goal': '가' * (limit + 1), 'due': self.due_iso(600)})
            self.assertEqual(caught.exception.code, 'invalid_goal')
            self.assertIn(f'kind {kind}의 goal은 {limit}자 이내', str(caught.exception))
            self.assertIn('줄여서 한 번 다시 호출', str(caught.exception))
        self.assertEqual(self.rows(), [])


class ScheduleTextTests(unittest.TestCase):
    """#393: the schedule line in the owner's words."""

    def row(self, **extra):
        due = datetime(2026, 10, 28, 18, 35, tzinfo=SEOUL).timestamp()
        row = {'kind': 'prepare', 'goal_text': 'g', 'due_at': due, 'timezone': 'Asia/Seoul', 'recurrence': None,
               'channel': 'telegram', 'delivery_mode': None}
        row.update(extra)
        return row

    def test_single_proposal_without_offer_uses_catalog_fallback(self):
        for title in (None, '준비 제목'):
            row = self.row(title_text=title)
            for render in (prep.proposal_text, prep.proposal_html):
                text = render([row], 0)
                self.assertTrue(text.endswith(msg('preparations.proposal_question')), text)
                self.assertEqual(text.count('?'), 1)
                self.assertNotIn('예약', text)
        self.assertEqual(msg('preparations.proposal_question', 'en'), 'Shall I set this up?')

    def test_delivery_lines_use_catalog_for_each_mode(self):
        from unittest.mock import patch
        for mode, extra in (('send', {}), ('keep', {'channel': 'web'}),
                            ('when_needed', {'delivery_mode': 'when_needed'})):
            row = self.row(**extra)
            self.assertTrue(prep.schedule_text(row).endswith(msg('preparations.delivery_' + mode)))
            with patch.object(prep, 'msg', side_effect=lambda key, **kwargs: msg(key, 'en', **kwargs)):
                self.assertTrue(prep.schedule_text(row).endswith(msg('preparations.delivery_' + mode, 'en')))

    def window(self, minutes, until_hm, runs):
        due = self.row()['due_at']
        until = datetime(2026, 10, 28, *until_hm, tzinfo=SEOUL) if until_hm[0] >= 18 else datetime(2026, 10, 29, *until_hm, tzinfo=SEOUL)
        every, end, max_runs = prep.normalize_window(minutes, until.isoformat(), runs, due, 'Asia/Seoul', due - 60)
        return self.row(recurrence=prep.RECURRENCE_WINDOW, every_seconds=every, window_end=end, max_runs=max_runs,
                        delivery_mode=prep.DELIVERY_WHEN_NEEDED)

    def test_the_deadline_is_stated_as_enforced(self):
        """The live case: 18:35 until 19:55 every 5 minutes: nothing runs at 19:55 itself."""
        self.assertEqual(prep.schedule_text(self.window(5, (19, 55), 16), 'Asia/Seoul'),
                         '10월 28일(수) 18:35부터 19:55 전까지, 5분마다 최대 16번 · 알릴 일이 있을 때만 알려요')

    def test_an_end_set_one_minute_after_the_last_check_reads_as_that_check(self):
        """#412: "11시 반까지" is set as until 11:31; the owner reads 11:30까지, which stays exact."""
        self.assertEqual(prep.schedule_text(self.window(5, (19, 56), 16), 'Asia/Seoul'),
                         '10월 28일(수) 18:35부터 19:55까지, 5분마다 최대 16번 · 알릴 일이 있을 때만 알려요')
        row = self.window(5, (19, 56), 16)
        self.assertEqual(prep.schedule_text({**row, 'due_at': row['due_at'] + 600}, 'Asia/Seoul'),
                         '10월 28일(수) 18:45부터 19:55까지, 5분마다 최대 16번 · 알릴 일이 있을 때만 알려요',
                         'a later slot of the same watch still names 19:55')
        self.assertIn('19:57 전까지', prep.schedule_text(self.window(5, (19, 57), 16), 'Asia/Seoul'),
                      'a deadline whose previous minute is not a slot keeps "전까지"')

    def test_a_window_past_midnight_names_its_last_day_and_a_foreign_zone_is_named(self):
        self.assertEqual(prep.schedule_text(self.window(120, (0, 40), None), 'Europe/Paris'),
                         '10월 28일(수) 18:35부터 10월 29일(목) 00:40 전까지 (Asia/Seoul), 2시간마다 최대 4번 · 알릴 일이 있을 때만 알려요')

    def test_one_run_repetitions_and_delivery(self):
        self.assertEqual(prep.schedule_text(self.window(5, (19, 0), 1), 'Asia/Seoul'),
                         '10월 28일(수) 18:35부터 19:00 전까지 한 번 · 알릴 일이 있을 때만 알려요')
        self.assertEqual(prep.schedule_text(self.row(recurrence='daily', channel='web'), 'Asia/Seoul'),
                         '10월 28일(수) 18:35부터 매일 · 결과가 나와도 따로 알리지 않아요')
        self.assertEqual(prep.schedule_text(self.row(kind='reminder'), 'Asia/Seoul'), '10월 28일(수) 18:35')


class SurfaceTests(unittest.TestCase):
    def test_the_tool_is_offered_only_where_the_service_wired_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = QuickStore(Path(tmp) / 'data')
            plain = Capabilities(store, None, {}, '', 'job', lambda *a: None)
            wired = Capabilities(store, None, {}, '', 'job', lambda *a: None, preparations=lambda args: {})
            names = lambda caps: [d['function']['name'] for d in caps.definitions()]
            self.assertNotIn('schedule_preparation', names(plain))
            self.assertIn('schedule_preparation', names(wired))
        schema = next(d for d in action_definitions({'schedule_preparation': {'host_action': 'schedule_preparation'}},
                                                    {'schedule_preparation'}))
        self.assertEqual(schema['function']['parameters']['required'], ['kind', 'goal', 'due'])
        self.assertIn('proposal the owner accepts', schema['function']['description'])
        # Scrubbed with the Work's redactor; without one, only the length.
        scrub = lambda text: text.replace('token-1', '[redacted]')
        self.assertEqual(recorded_arguments('schedule_preparation', {'goal': '갱신 token-1', 'kind': 'reminder'}, scrub)['goal'],
                         '갱신 [redacted]')
        self.assertEqual(recorded_arguments('schedule_preparation', {'goal': 'abc', 'kind': 'reminder'})['goal'], '[가림: 3자]')


UI_CHECK = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const app=fs.readFileSync(process.argv[1],'utf8'),ids=new Map();
class Element {
 constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.attrs={};this.className='';this.hidden=false;this._text='';}
 set id(value){this._id=value;ids.set(value,this);} get id(){return this._id;}
 set textContent(value){this._text=String(value);this.children=[];} get textContent(){return this._text+this.children.map(node=>typeof node==='string'?node:node.textContent).join('');}
 append(...nodes){this.children.push(...nodes);} replaceChildren(...nodes){this._text='';this.children=[];this.append(...nodes);}
 setAttribute(key,value){this.attrs[key]=value;} focus(){} get isConnected(){return true;}
 get classList(){const node=this;return {toggle(name,on){node._cls=Boolean(on);},add(){},remove(){},contains:()=>Boolean(node._cls)};}
 querySelector(){return null;} querySelectorAll(){return [];}
}
function descendants(node){return node.children.flatMap(child=>typeof child==='string'?[]:[child,...descendants(child)]);}
for(const id of ['preparations-list','preparations-feedback'])new Element('div').id=id;
const $=id=>ids.get(id),document={getElementById:$,createElement:tag=>new Element(tag)};
const part=(start,end)=>app.slice(app.indexOf(start),app.indexOf(end));
const source=part('const LANGUAGES=','function normalizeEndpoint(')+part('function element(','function setError(')+part('let preparationRows=','// end of #659 preparations');
const calls=[];
const ctx={document,$,console,calls,
 api:async(path,body)=>{calls.push({path,body});if(body&&body.operation==='accept')ctx.rows=[{...ctx.rows[0],state:'scheduled',accepted_by:'owner-settings'}];if(body&&body.operation==='delete')ctx.rows=[];return {preparations:ctx.rows,management:{...JSON.parse(process.argv[2]),pause:'일시 정지',resume:'다시 시작',edit:'일정 변경',run_now:'지금 실행',paused:'일시 정지됨',saved:'준비 일정을 변경했습니다.'}};},
 busy:async(button,fn)=>fn(),setError:(id,error)=>{$(id).textContent=error?.message||String(error||'');},setFeedback:(id,text)=>{$(id).textContent=text||'';}};
vm.createContext(ctx);vm.runInContext(source,ctx);vm.runInContext("setLanguage('ko')",ctx);
const rows=()=>$('preparations-list').children.filter(node=>node.tag==='div'&&node.className.split(' ')[0]==='settings-row');
const texts=(node,cls)=>descendants(node).filter(n=>n.className===cls).map(n=>n.textContent);
const buttons=node=>descendants(node).filter(n=>n.tag==='button').map(n=>n.textContent);
const press=async(label)=>{const button=descendants(rows()[0]).find(n=>n.tag==='button'&&n.textContent===label);await button.onclick({currentTarget:button});};
(async()=>{
 ctx.rows=[];await ctx.loadPreparations();
 assert.equal($('preparations-list').children[0].className,'settings-empty');
 ctx.rows=[{id:'p1',kind:'prepare',goal:'점심 후보 준비',state:'proposed',due_local:'2026-09-28 11:30 (Mon, Asia/Seoul)',recurrence:'daily',delivery:'keep',last_result:''}];
 await ctx.loadPreparations();
 assert.deepEqual(texts(rows()[0],'settings-row-title'),['점심 후보 준비']);
 assert.equal(texts(rows()[0],'settings-row-description')[0],'미리 준비 · 매일 · 2026-09-28 11:30 (Mon, Asia/Seoul) 예정 · 결과가 나와도 따로 알리지 않아요');
 assert.deepEqual(texts(rows()[0],'settings-state attention'),['수락 대기']);
 assert.deepEqual(buttons(rows()[0]),['좋아요','필요 없어요','일정 변경','삭제']);
 for(const [mode,key] of [['send','delivery_send'],['keep','delivery_keep'],['when_needed','delivery_when_needed']]){
  assert.equal(ctx.preparationDeliveryLabel(mode),JSON.parse(process.argv[2])[key].ko);
 }
 vm.runInContext("setLanguage('en')",ctx);ctx.renderPreparations();
 assert.deepEqual(buttons(rows()[0]),['Sounds good','No thanks','일정 변경','Delete']);
 for(const mode of ['send','keep','when_needed'])assert.equal(ctx.preparationDeliveryLabel(mode),JSON.parse(process.argv[2])['delivery_'+mode].en);
 vm.runInContext("setLanguage('ko')",ctx);ctx.renderPreparations();
 await press('좋아요');
 assert.equal(JSON.stringify(calls.at(-1)),JSON.stringify({path:'/api/preparations/request',body:{operation:'accept',id:'p1'}}));
 assert.deepEqual(texts(rows()[0],'settings-state active'),['예약됨']);
 assert.deepEqual(buttons(rows()[0]),['취소','일시 정지','지금 실행','일정 변경','삭제']);
 await press('삭제');
 assert.equal(calls.filter(c=>c.body).length,1,'the first press deletes nothing');
 assert(texts(rows()[0],'confirm-text')[0].includes('지난 실행 기록은 남습니다'));
 await press('삭제 확인');
 assert.equal(JSON.stringify(calls.at(-1).body),JSON.stringify({operation:'delete',id:'p1'}));
 assert.equal($('preparations-feedback').textContent,'지웠습니다.');
 ctx.rows=[{id:'p2',kind:'reminder',goal:'치과',state:'running',due_local:'x',recurrence:null,delivery:'send',last_result:''}];
 await ctx.loadPreparations();
 assert.deepEqual(buttons(rows()[0]),['취소'],'a running preparation can be cancelled, not deleted');
 // #719: a watch shows its window, bound, delivery and last typed decision; Settings cancels it.
 ctx.rows=[{id:'p3',kind:'prepare',goal:'출발 확인',state:'scheduled',due_local:'D',recurrence:'window',every_minutes:10,until_local:'U',max_runs:6,run_count:2,delivery:'when_needed',last_decision:'quiet',last_decision_reason:'judged-not-needed',last_result:''}];
 await ctx.loadPreparations();
 assert.equal(texts(rows()[0],'settings-row-description')[0],'미리 준비 · 10분마다 U까지 · 2/6회 실행 · D 예정 · 알릴 일이 있을 때만 알려요 · 마지막 실행: 조용히 넘어감 (알릴 일이 없다고 판단)');
 assert.deepEqual(buttons(rows()[0]),['취소','일시 정지','지금 실행','일정 변경','삭제']);
 await press('취소');
 assert.equal(JSON.stringify(calls.at(-1).body),JSON.stringify({operation:'cancel',id:'p3'}));
 vm.runInContext("setLanguage('en')",ctx);ctx.rows=[{...ctx.rows[0],state:'expired',last_decision:'notify',last_decision_reason:'judged-needed'}];
 await ctx.loadPreparations();
 assert.equal(texts(rows()[0],'settings-row-description')[0],'Prepare ahead · Every 10 min until U · 2/6 runs · D · I’ll tell you only when a result needs your attention. · Last run: notified (judged you need to know)');
 assert.deepEqual(texts(rows()[0],'settings-state neutral'),['Window closed']);
 // #393: a row with the owner's title and offer is listed by them, the brief is not its name.
 vm.runInContext("setLanguage('ko')",ctx);ctx.rows=[{...ctx.rows[0],state:'scheduled',goal:'매회 잔고와 호가를 조회한다',title:'5종목 지켜보기',offer:'예산 10만 원\n주문은 하지 않아요'}];
 await ctx.loadPreparations();
 assert.deepEqual(texts(rows()[0],'settings-row-title'),['5종목 지켜보기']);
 assert.ok(texts(rows()[0],'settings-row-description')[0].startsWith('예산 10만 원 · 주문은 하지 않아요 · 미리 준비'));
 // #575: paused rows remain cancellable and expose resume, run-now and schedule-only editing.
 ctx.rows=[{id:'p4',kind:'prepare',goal:'자료 준비',state:'paused',due_iso:'2026-10-15T09:00:00+09:00',due_local_input:'2026-10-15T09:00',timezone:'Asia/Seoul',recurrence:'daily'}];
 await ctx.loadPreparations();
 assert.deepEqual(buttons(rows()[0]),['취소','다시 시작','지금 실행','일정 변경','삭제']);
 assert.deepEqual(texts(rows()[0],'settings-state neutral'),['일시 정지됨']);
 await press('다시 시작');assert.equal(calls.at(-1).body.operation,'resume');
 await press('지금 실행');assert.equal(calls.at(-1).body.operation,'run_now');
 await press('일정 변경');
 const form=$('preparations-list').children.find(n=>n.tag==='form'),fields=descendants(form).filter(n=>n.tag==='input'||n.tag==='select');
 assert.deepEqual(fields.slice(0,3).map(n=>n.name),['due','timezone','recurrence']);
 assert.equal(fields[0].type,'datetime-local');assert.equal(fields[0].value,'2026-10-15T09:00');
 fields.find(n=>n.name==='recurrence').value='weekly';
 await form.onsubmit({preventDefault(){}});
 assert.equal(JSON.stringify(calls.at(-1).body),JSON.stringify({operation:'edit',id:'p4',due:'2026-10-15T09:00',timezone:'Asia/Seoul',recurrence:'weekly'}));
 assert(!Object.hasOwn(calls.at(-1).body,'goal'));
 ctx.rows=[{...ctx.rows[0],recurrence:'window',every_minutes:10,until:'2026-10-15T12:00:00+09:00',until_local_input:'2026-10-15T12:00',max_runs:6}];
 await ctx.loadPreparations();await press('일정 변경');
 const watchForm=$('preparations-list').children.find(n=>n.tag==='form'),watchFields=descendants(watchForm).filter(n=>n.tag==='input');
 assert.deepEqual(watchFields.map(n=>n.name),['due','timezone','every_minutes','until','max_runs']);
 watchFields.find(n=>n.name==='max_runs').value='3';await watchForm.onsubmit({preventDefault(){}});
 assert.equal(calls.at(-1).body.max_runs,3);assert(!Object.hasOwn(calls.at(-1).body,'recurrence'));
 assert.equal(watchFields.find(n=>n.name==='until').type,'datetime-local');
 assert.equal(calls.at(-1).body.until,'2026-10-15T12:00');
 // #581: rule controls submit one rule, without recurrence or JS offset conversion.
 ctx.rows=[{...ctx.rows[0],recurrence:'weekly'}];await ctx.loadPreparations();await press('일정 변경');
 const ruleForm=$('preparations-list').children.find(n=>n.tag==='form'),ruleFields=descendants(ruleForm).filter(n=>n.tag==='input'||n.tag==='select');
 const field=name=>ruleFields.find(n=>n.name===name);
 field('day_MO').checked=true;field('day_TH').checked=true;field('day_TH').onchange();
 field('interval').value='2';field('interval').onchange();field('rule_until').value='2026-12-31';field('rule_until').onchange();
 await ruleForm.onsubmit({preventDefault(){}});
 assert.equal(calls.at(-1).body.rrule,'RRULE:FREQ=WEEKLY;BYDAY=MO,TH;INTERVAL=2;UNTIL=20261231');
 assert(!Object.hasOwn(calls.at(-1).body,'recurrence'));assert.equal(calls.at(-1).body.timezone,'Asia/Seoul');
 field('monthly_day').value='31';field('monthly_day').onchange();await ruleForm.onsubmit({preventDefault(){}});
 assert.equal(calls.at(-1).body.rrule,'RRULE:FREQ=MONTHLY;BYMONTHDAY=31;UNTIL=20261231');
 // Existing count/ordinal rules round trip unchanged when editing the time only.
 ctx.rows=[{...ctx.rows[0],recurrence:'RRULE:FREQ=MONTHLY;BYDAY=-1FR;COUNT=3'}];await ctx.loadPreparations();await press('일정 변경');
 await $('preparations-list').children.find(n=>n.tag==='form').onsubmit({preventDefault(){}});
 assert.equal(calls.at(-1).body.rrule,ctx.rows[0].recurrence);
 await press('일정 변경');
 const ordinalForm=$('preparations-list').children.find(n=>n.tag==='form'),endDate=descendants(ordinalForm).find(n=>n.name==='rule_until');
 endDate.value='2026-12-31';endDate.onchange();await ordinalForm.onsubmit({preventDefault(){}});
 assert.equal(calls.at(-1).body.rrule,'RRULE:FREQ=MONTHLY;BYDAY=-1FR;UNTIL=20261231');
 console.log('ok');
})().catch(error=>{console.error(error);process.exit(1);});
"""


class PreparationSettingsUi(unittest.TestCase):
    def test_the_settings_group_lists_accepts_cancels_and_deletes(self):
        import shutil
        import subprocess
        node = shutil.which('node')
        if node is None:
            raise unittest.SkipTest('Node is needed for the DOM-stub check')
        app = Path(__file__).resolve().parents[1] / 'src' / 'personal_agent' / 'web' / 'app.js'
        result = subprocess.run([node, '-e', UI_CHECK, str(app), json.dumps({
            key: {lang: msg(message_id, lang) for lang in ('ko', 'en')}
            for key, message_id in {
                'accept': 'button.preparation_accept', 'decline': 'button.preparation_decline',
                **{'delivery_' + mode: 'preparations.delivery_' + mode for mode in ('send', 'keep', 'when_needed')}
            }.items()})], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip().splitlines()[-1], 'ok')


if __name__ == '__main__':
    unittest.main()


class PreparationManagementTests(unittest.TestCase):
    """Owner schedule management through the real store and claim boundary (#575)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = QuickStore(Path(tmp.name))
        self.now = START
        self.preparations = prep.Preparations(self.store, clock=lambda: self.now)

    def make(self, recurrence=None, state=prep.STATE_SCHEDULED, window=None):
        return self.preparations.create(kind='prepare', goal='자료 준비', due_at=self.now + 60,
                                        timezone='Asia/Seoul', recurrence=recurrence, channel='web',
                                        created_from=None, state=state, window=window)

    def refusal(self, code, action, *args, **kwargs):
        with self.assertRaises(prep.PreparationRefusal) as caught:
            action(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def test_pause_skips_tick_and_stale_claim_and_still_counts_and_cancels(self):
        from unittest.mock import patch
        row = self.make()
        self.preparations.pause(row['id'])
        self.now += 120
        self.assertEqual(self.preparations.due(self.now), [])
        self.assertIsNone(self.preparations.start(row, channel='web', chat_id=None, now=self.now))
        self.assertEqual(self.preparations.rows()[0]['state'], prep.STATE_PAUSED)
        with patch.object(prep, 'MAX_ACTIVE', 1):
            self.refusal('too_many', self.make)
        self.assertTrue(self.preparations.cancel(row['id'])['changed'])

    def test_resume_skips_missed_recurrences_and_refuses_expired_one_shot(self):
        for recurrence in ('daily', 'weekdays', 'weekly'):
            row = self.make(recurrence)
            self.preparations.pause(row['id'])
            self.now += 8 * 86400
            resumed = self.preparations.resume(row['id'])
            self.assertEqual(resumed['due_at'], prep.next_due(row['due_at'], row['timezone'], recurrence, self.now))
            self.assertGreater(resumed['due_at'], self.now)
        row = self.make()
        self.preparations.pause(row['id'])
        self.now += 61
        self.refusal('resume_expired', self.preparations.resume, row['id'])
        self.assertEqual(self.preparations.get(row['id'])['state'], prep.STATE_PAUSED)

    def test_resume_watch_skips_missed_slots_and_refuses_closed_window(self):
        row = self.make(window=(300, self.now + 1800, 6))
        self.preparations.pause(row['id'])
        self.now += 700
        self.assertEqual(self.preparations.resume(row['id'])['due_at'], row['due_at'] + 900)
        self.preparations.pause(row['id'])
        self.now = row['window_end']
        self.refusal('resume_expired', self.preparations.resume, row['id'])

    def test_edit_validates_schedule_keeps_proposed_and_preserves_goal(self):
        row = self.make(state=prep.STATE_PROPOSED)
        due = datetime.fromtimestamp(self.now + 3600, SEOUL).isoformat()
        edited = self.preparations.edit(row['id'], due=due, timezone='Asia/Seoul', recurrence='weekdays')
        self.assertEqual((edited['due_at'], edited['recurrence'], edited['state'], edited['goal_text']),
                         (self.now + 3600, 'weekdays', prep.STATE_PROPOSED, row['goal_text']))
        self.assertIsNone(edited['accepted_by'])
        self.assertEqual(self.preparations.edit(row['id'], timezone='UTC')['timezone'], 'UTC')
        for changes, code in (({'goal': '다른 목표'}, 'goal_not_editable'), ({'goal_text': row['goal_text']}, 'goal_not_editable'),
                              ({'due': 'wrong'}, 'invalid_due'), ({'timezone': 'Mars/Olympus'}, 'timezone_unknown'),
                              ({'recurrence': 'hourly'}, 'invalid_recurrence'), ({'kind': 'reminder'}, 'invalid_edit')):
            self.refusal(code, self.preparations.edit, row['id'], **changes)
        self.assertIsNone(self.preparations.edit(row['id'], recurrence='none')['recurrence'])
        scheduled = self.make()
        self.preparations.pause(scheduled['id'])
        self.assertEqual(self.preparations.edit(scheduled['id'], recurrence='daily')['state'], prep.STATE_PAUSED)
        self.preparations.run_now(scheduled['id'], channel='web', chat_id=None)
        self.refusal('already_running', self.preparations.edit, scheduled['id'], due=due)
        self.refusal('already_running', self.preparations.pause, scheduled['id'])

    def test_edit_watch_reuses_window_validation(self):
        row = self.make(window=(300, self.now + 1800, 6))
        until = datetime.fromtimestamp(self.now + 3600, SEOUL).isoformat()
        edited = self.preparations.edit(row['id'], every_minutes=10, until=until, max_runs=3)
        self.assertEqual((edited['every_seconds'], edited['window_end'], edited['max_runs']), (600, self.now + 3600, 3))
        self.refusal('invalid_window', self.preparations.edit, row['id'], every_minutes=1)
        self.refusal('invalid_max_runs', self.preparations.edit, row['id'], max_runs=0)

    def test_editing_a_running_watchs_end_keeps_the_slots_left(self):
        """#693: a watch at run 7 of 12, its end moved to two more slots, runs exactly those two."""
        start = self.now
        row = self.make(window=(3600, start + 12 * 3600, 12))
        with self.store.db() as db:
            db.execute('UPDATE preparations SET run_count=7,due_at=? WHERE id=?', (start + 7 * 3600, row['id']))
        until = datetime.fromtimestamp(start + 8 * 3600 + 600, SEOUL).isoformat()
        edited = self.preparations.edit(row['id'], until=until)
        self.assertEqual((edited['run_count'], edited['max_runs']), (7, 9))
        self.assertEqual(prep.next_window_slot({**edited, 'due_at': start + 7 * 3600, 'run_count': 8}, start + 7 * 3600),
                         start + 8 * 3600, 'the second slot left still runs')
        self.assertIsNone(prep.next_window_slot({**edited, 'run_count': 9}, start + 8 * 3600))
        # A total is a total: at or below the runs done it ends the watch.
        self.assertEqual(self.preparations.edit(row['id'], max_runs=5)['max_runs'], 5)
        self.assertEqual(self.preparations.edit(row['id'], max_runs=12)['max_runs'], 9)

    def test_run_now_uses_one_atomic_work_and_preserves_schedule_across_restart(self):
        for paused in (False, True):
            recurrence = 'daily' if paused else None
            row = self.make(recurrence=recurrence)
            if paused:
                self.preparations.pause(row['id'])
            work = self.preparations.run_now(row['id'], channel='web', chat_id=None)
            self.assertEqual(self.store.job(work)['request_key'], prep.request_key(row['id'], self.now))
            self.assertEqual(self.preparations.get(row['id'])['due_at'], row['due_at'])
            restarted = prep.Preparations(QuickStore(self.store.root), clock=lambda: self.now)
            self.refusal('already_running', restarted.run_now, row['id'], channel='web', chat_id=None)
            with self.store.db() as db:
                db.execute("UPDATE jobs SET status='succeeded',delivery='none',response='준비 완료' WHERE id=?", (work,))
            self.assertIn(row['id'], [item['id'] for item in restarted.due(self.now)])
            settled = restarted.settle(restarted.get(row['id']), self.now)
            self.assertEqual((settled['state'], settled['due_at'], settled['recurrence']),
                             (prep.STATE_PAUSED if paused else prep.STATE_SCHEDULED, row['due_at'], recurrence))
            self.assertEqual(restarted.run_now(row['id'], channel='web', chat_id=None), work)
            with self.store.db() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM jobs WHERE request_key=?',
                                            (prep.request_key(row['id'], self.now),)).fetchone()[0], 1)

    def test_run_now_refuses_unaccepted_or_expired_watch(self):
        proposed = self.make(state=prep.STATE_PROPOSED)
        self.refusal('invalid_state', self.preparations.run_now, proposed['id'], channel='web', chat_id=None)
        row = self.make(window=(300, self.now + 1800, 6))
        self.now = row['window_end']
        self.refusal('resume_expired', self.preparations.run_now, row['id'], channel='web', chat_id=None)

    def test_migration_preserves_old_rows_and_adds_nullable_return_state(self):
        row = self.make()
        with self.store.db() as db:
            db.execute('ALTER TABLE preparations DROP COLUMN run_return_state')
            prep.migrate(db)
            prep.migrate(db)
        restored = self.preparations.get(row['id'])
        self.assertEqual(restored['goal_text'], row['goal_text'])
        self.assertIsNone(restored['run_return_state'])


class RecurrenceRuleTests(unittest.TestCase):
    """Bounded rules through the real claim/settle store, with a fake clock (#580)."""

    setUp = PreparationManagementTests.setUp
    make = PreparationManagementTests.make
    refusal = PreparationManagementTests.refusal

    def timestamp(self, text, zone='Asia/Seoul'):
        return datetime.fromisoformat(text).replace(tzinfo=ZoneInfo(zone)).timestamp()

    def test_allowed_parts_normalize(self):
        for rule in ('FREQ=DAILY', 'FREQ=WEEKLY;INTERVAL=12;BYDAY=MO,TH',
                     'FREQ=MONTHLY;BYDAY=1MO,-1FR', 'FREQ=MONTHLY;BYMONTHDAY=1,31,-1',
                     'FREQ=DAILY;COUNT=366', 'FREQ=DAILY;UNTIL=20261231',
                     'FREQ=DAILY;UNTIL=20261231T090000', 'FREQ=DAILY;UNTIL=20261231T090000Z'):
            with self.subTest(rule=rule):
                self.assertEqual(prep.normalize_rrule(rule), 'RRULE:' + rule)
        self.assertEqual(prep.normalize_rrule('rrule:freq=weekly;interval=02'), 'RRULE:FREQ=WEEKLY;INTERVAL=2')

    def test_refused_parts_before_dateutil(self):
        from unittest.mock import patch
        rules = ['FREQ=HOURLY', 'FREQ=YEARLY', 'FREQ=MINUTELY', 'FREQ=SECONDLY',
                 'FREQ=DAILY;BYHOUR=9', 'FREQ=DAILY;BYMINUTE=1', 'FREQ=DAILY;BYSECOND=0',
                 'FREQ=DAILY;BYSETPOS=1', 'FREQ=DAILY;BYMONTH=1', 'FREQ=DAILY;WKST=SU',
                 'FREQ=DAILY;BYWEEKNO=1', 'FREQ=DAILY;BYYEARDAY=1', 'FREQ=DAILY;BYEASTER=0',
                 'FREQ=WEEKLY;BYDAY=1MO', 'FREQ=DAILY;BYDAY=-1FR', 'FREQ=MONTHLY;BYDAY=0MO',
                 'FREQ=MONTHLY;BYDAY=54MO', 'FREQ=MONTHLY;BYMONTHDAY=0', 'FREQ=MONTHLY;BYMONTHDAY=32',
                 'FREQ=MONTHLY;BYMONTHDAY=-2', 'FREQ=WEEKLY;BYMONTHDAY=1', 'FREQ=DAILY;INTERVAL=0',
                 'FREQ=DAILY;INTERVAL=13', 'FREQ=DAILY;COUNT=0', 'FREQ=DAILY;COUNT=367',
                 'FREQ=DAILY;COUNT=2;UNTIL=20261231', 'FREQ=DAILY;UNTIL=20260230',
                 'FREQ=DAILY;UNTIL=20261231T250000', 'FREQ=DAILY;COUNT=1;COUNT=2',
                 'BYDAY=MO', 'FREQ=DAILY\nDTSTART:20260101', 'FREQ=DAILY;', '', None, 3]
        with patch.object(prep, 'rrulestr', side_effect=AssertionError('parser must not be called')):
            for rule in rules:
                with self.subTest(rule=rule):
                    self.refusal('invalid_rrule', prep.normalize_rrule, rule)

    def next(self, first, rule, now=None, zone='Asia/Seoul'):
        due = self.timestamp(first, zone)
        return prep.next_due(due, zone, prep.normalize_rrule(rule), self.timestamp(now, zone) if now else due)

    def test_weekdays_across_week_and_biweekly(self):
        rule = 'FREQ=WEEKLY;BYDAY=MO,TH'
        self.assertEqual(self.next('2026-10-12T09:00', rule), self.timestamp('2026-10-15T09:00'))
        self.assertEqual(self.next('2026-10-12T09:00', rule, '2026-10-15T09:00'), self.timestamp('2026-10-19T09:00'))
        self.assertEqual(self.next('2026-10-12T09:00', 'FREQ=WEEKLY;INTERVAL=2'), self.timestamp('2026-10-26T09:00'))

    def test_month_days_and_ordinals(self):
        self.assertEqual(self.next('2026-01-31T09:00', 'FREQ=MONTHLY;BYMONTHDAY=31'), self.timestamp('2026-03-31T09:00'))
        self.assertEqual(self.next('2026-01-31T09:00', 'FREQ=MONTHLY;BYMONTHDAY=-1'), self.timestamp('2026-02-28T09:00'))
        self.assertEqual(self.next('2026-01-05T09:00', 'FREQ=MONTHLY;BYDAY=1MO'), self.timestamp('2026-02-02T09:00'))
        self.assertEqual(self.next('2026-01-30T09:00', 'FREQ=MONTHLY;BYDAY=-1FR'), self.timestamp('2026-02-27T09:00'))

    def test_dst_keeps_wall_time_and_skips_nonexistent_time(self):
        self.assertEqual(self.next('2026-03-07T09:00', 'FREQ=DAILY', zone='America/New_York'),
                         self.timestamp('2026-03-08T09:00', 'America/New_York'))
        self.assertEqual(self.next('2026-10-31T09:00', 'FREQ=DAILY', zone='America/New_York'),
                         self.timestamp('2026-11-01T09:00', 'America/New_York'))
        self.assertEqual(self.next('2026-03-07T02:30', 'FREQ=DAILY', zone='America/New_York'),
                         self.timestamp('2026-03-09T02:30', 'America/New_York'))

    def finish_run(self, row):
        self.now = row['due_at']
        work = self.preparations.start(row, channel='web', chat_id=None, now=self.now)
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='succeeded',delivery='none',response='done' WHERE id=?", (work,))
        return self.preparations.settle(self.preparations.get(row['id']), self.now)

    def test_count_ends_after_three_runs_preserving_anchor_across_restart(self):
        row = self.make(prep.normalize_rrule('FREQ=WEEKLY;INTERVAL=2;COUNT=3'))
        anchor = row['due_at']
        for count in range(1, 4):
            row = self.finish_run(row)
            self.assertEqual(row['rule_start'], anchor)
            self.assertEqual(row['run_count'], count)
            self.assertEqual(row['state'], prep.STATE_DELIVERED if count == 3 else prep.STATE_SCHEDULED)
            self.preparations = prep.Preparations(QuickStore(self.store.root), clock=lambda: self.now)
        self.assertEqual(row['due_at'], anchor + 28 * 86400)

    def test_until_date_and_datetime_end_after_last_run(self):
        for ending in ('20261015', '20261015T210130', '20261015T120130Z'):
            self.now = START
            row = self.make(prep.normalize_rrule('FREQ=DAILY;UNTIL=' + ending))
            row = self.finish_run(row)
            self.assertEqual(row['state'], prep.STATE_SCHEDULED)
            row = self.finish_run(row)
            self.assertEqual(row['state'], prep.STATE_DELIVERED)

    def test_missed_slots_are_skipped_and_legacy_unchanged(self):
        due = self.timestamp('2026-10-12T09:00')
        now = self.timestamp('2026-10-21T11:00')
        self.assertEqual(prep.next_due(due, 'Asia/Seoul', prep.normalize_rrule('FREQ=WEEKLY;BYDAY=MO,TH'), now),
                         self.timestamp('2026-10-22T09:00'))
        for recurrence, expected in (('daily', '2026-10-22T09:00'), ('weekdays', '2026-10-22T09:00'), ('weekly', '2026-10-26T09:00')):
            self.assertEqual(prep.next_due(due, 'Asia/Seoul', recurrence, now), self.timestamp(expected))
        self.assertIsNone(prep.next_due(due, 'Asia/Seoul', 'RRULE:FREQ=DAILY;COUNT=3', now, due, 1))

    def test_edit_rule_and_conflicts(self):
        row = self.make()
        updated = self.preparations.edit(row['id'], rrule='FREQ=MONTHLY;BYMONTHDAY=-1')
        self.assertEqual(updated['recurrence'], 'RRULE:FREQ=MONTHLY;BYMONTHDAY=-1')
        self.assertEqual(updated['rule_start'], row['due_at'])
        for changes in ({'rrule':'FREQ=DAILY', 'recurrence':'daily'}, {'rrule':'FREQ=DAILY', 'until':'20261231'}):
            self.refusal('invalid_rrule', self.preparations.edit, row['id'], **changes)
        window = self.make(window=(300, self.now + 1800, 6))
        self.refusal('invalid_rrule', self.preparations.edit, window['id'], rrule='FREQ=DAILY')

    def test_rule_schedule_words(self):
        row = self.make('RRULE:FREQ=WEEKLY;BYDAY=MO,TH;COUNT=3')
        text = prep.schedule_text(row)
        self.assertIn('월·목', text)
        self.assertIn('3번', text)
        self.assertIn(text, prep.proposal_summary(row))
        self.assertIn('격주', prep.rule_text('RRULE:FREQ=WEEKLY;INTERVAL=2'))
        self.assertIn('매주', text)
        self.assertIn('31일', prep.rule_text('RRULE:FREQ=MONTHLY;BYMONTHDAY=31'))
        self.assertIn('2026-12-31까지', prep.rule_text('RRULE:FREQ=DAILY;UNTIL=20261231'))

    def test_rule_start_column_migration_preserves_legacy_row(self):
        row = self.make('daily')
        with self.store.db() as db:
            db.execute('ALTER TABLE preparations DROP COLUMN rule_start')
            prep.migrate(db)
            prep.migrate(db)
        self.assertIsNone(self.preparations.get(row['id'])['rule_start'])


class RuleHandlerTests(_Case):
    """The actual service tool handler accepts rules and refuses mixed languages (#580)."""

    def test_rule_handler_and_status_local_inputs(self):
        job = {'id':'rule-work', 'message':'schedule'}
        schedule = self.service.preparation_scheduler(job, job['message'])
        args = {'kind':'reminder', 'goal':'message', 'due':self.due_iso(600), 'timezone':'Asia/Seoul'}
        for extra in ({'recurrence':'daily'}, {'every_minutes':10}, {'until':self.due_iso(1200)}):
            with self.assertRaises(ToolError) as caught:
                schedule({**args, 'rrule':'FREQ=WEEKLY;BYDAY=MO,TH', **extra})
            self.assertEqual(caught.exception.code, 'invalid_rrule')
        result = schedule({**args, 'rrule':'FREQ=WEEKLY;BYDAY=MO,TH'})
        row = self.service.preparations.get(result['preparation_id'])
        self.assertEqual(row['recurrence'], 'RRULE:FREQ=WEEKLY;BYDAY=MO,TH')
        status = self.service.preparations_status()
        self.assertEqual(status['preparations'][0]['due_local_input'],
                         datetime.fromtimestamp(row['due_at'], SEOUL).strftime('%Y-%m-%dT%H:%M'))
        for key in ('rule','weekly_days','monthly_day','interval','rule_until','day_MO','last_day'):
            self.assertEqual(set(status['management'][key]), {'ko','en','ja','zh'})


class RuleContinuityTests(unittest.TestCase):
    """Exercise persisted anchor use across Settings saves and pause/resume (#580)."""

    setUp = RecurrenceRuleTests.setUp
    make = RecurrenceRuleTests.make
    finish_run = RecurrenceRuleTests.finish_run

    def test_resume_and_unchanged_edit_keep_anchor_and_count(self):
        row = self.make('RRULE:FREQ=WEEKLY;INTERVAL=2;COUNT=3')
        anchor = row['due_at']
        row = self.finish_run(row)
        row = self.preparations.edit(row['id'], rrule=row['recurrence'])
        self.assertEqual((row['rule_start'], row['run_count']), (anchor, 1))
        self.preparations.pause(row['id'])
        self.now = anchor + 15 * 86400
        row = self.preparations.resume(row['id'])
        self.assertEqual(row['due_at'], anchor + 28 * 86400)
        self.assertEqual(row['rule_start'], anchor)

    def test_immediate_run_does_not_consume_scheduled_count(self):
        row = self.make('RRULE:FREQ=DAILY;COUNT=1')
        work = self.preparations.run_now(row['id'], channel='web', chat_id=None)
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='succeeded',delivery='none',response='done' WHERE id=?", (work,))
        row = self.preparations.settle(self.preparations.get(row['id']), self.now)
        self.assertEqual((row['state'], row['run_count']), (prep.STATE_SCHEDULED, 0))
        self.assertEqual(self.finish_run(row)['state'], prep.STATE_DELIVERED)
