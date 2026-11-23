"""#1167: a record Still holds itself is reconciled from Still's own store, not stopped on as an outside effect.

Real store, the real worker loop with a scripted model, the real ``schedule_preparation`` and
``entrusted_line`` handlers and the one continuation rule.  No model, browser or account calls.
The negative tests hold the #618 invariant: an outside change is never re-delegated past, and a
re-delegated attempt never leaves a second preparation for the same Work.
"""
import email.utils
import json
from types import SimpleNamespace
from unittest.mock import Mock

from personal_agent import preparations as prep
from personal_agent.agent_runtime import ToolError
from personal_agent.api_requests import ApiRequests, save_slot

from personal_agent.providers import ModelAdapter

from test_openai_responses_994 import responses_reply
from test_orchestrator import Harness, plan
from test_preparations import _Case, call, finish

POST = {'slot': 'alpha', 'url': 'https://alpha.example/change', 'method': 'POST', 'effect': 'mutate', 'body': '{"v":1}'}


class StillRecords(_Case):
    def scheduled_work(self, title='물 마시기'):
        self.script = [{'content': None, 'tool_calls': [call('1', 'schedule_preparation', kind='reminder', goal='물 마시라고 알리기',
                                                             due=self.due_iso(3600), title=title)]},
                       finish('f', '1')]
        work = self.receive('한 시간 뒤에 물 마시라고 알려줘')
        [row] = self.active(work)
        return work, row

    def active(self, work):
        return [row for row in self.service.preparations.rows() if row['created_from'] == work
                and row['state'] in (*prep.ACTIVE_STATES, prep.STATE_PROPOSED)]

    def event(self, work, tool, status, detail):
        with self.store.db() as db:
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,0)',
                       (work, tool, status, json.dumps(detail)))

    def orchestration(self, following):
        return SimpleNamespace(orchestrated=True, _redact=lambda text, **_: text, next=Mock(return_value=following),
                               evaluate_answer=Mock(return_value='not_reached'), may_judge=lambda: True,
                               budget_allows=lambda **_: True, budget=None, owner_told=Mock(), continues='')

    def step(self, work, following=None):
        orchestration = self.orchestration(following)
        self.service.orchestration_step(orchestration, object(), work, 0, answer='아직 목표에 못 미쳤어요', outcome='partial')
        return orchestration

    def schedule_args(self, row, **extra):
        return {'kind': 'reminder', 'goal': '물 마시라고 다시 알리기', 'due': self.due_iso(7200), 'title': '물 마시기', **extra}


class Reconciled(StillRecords):
    def test_a_scheduled_preparation_alone_does_not_stop_re_delegation(self):
        work, _row = self.scheduled_work()
        self.assertNotIn('effect', self.service.continuation_reasons(work, 'redelegate'))
        self.assertEqual(self.service.may_continue(work, 'redelegate'), (True, None))

    def test_the_replacement_brief_names_the_existing_preparation(self):
        work, row = self.scheduled_work(title='오후 물 알림')
        orchestration = self.step(work, following=object())
        kwargs = orchestration.next.call_args.kwargs
        self.assertIs(kwargs['effect'], False)
        self.assertIn(row['id'], orchestration.continues)
        self.assertIn(row.get('title_text') or row['goal_text'], orchestration.continues)
        self.assertIn(prep.iso(row['due_at'], row['timezone']), orchestration.continues)

    def test_the_re_delegated_attempt_revises_and_never_duplicates(self):
        work, row = self.scheduled_work()
        self.step(work, following=object())
        schedule = self.service.preparation_scheduler(self.store.job(work), '')
        with self.assertRaises(ToolError) as refused:
            schedule(self.schedule_args(row))
        self.assertEqual(refused.exception.code, 'preparation_exists')
        self.assertIn(row['id'], str(refused.exception))
        self.assertEqual([other['id'] for other in self.active(work)], [row['id']])
        replaced = schedule(self.schedule_args(row, replaces=row['id']))
        self.assertEqual(replaced['replaced']['preparation_id'], row['id'])
        [kept] = self.active(work)
        self.assertEqual(kept['id'], replaced['preparation_id'])
        self.assertNotEqual(kept['id'], row['id'])

    def test_without_a_re_delegation_two_preparations_in_one_work_stay_allowed(self):
        work, row = self.scheduled_work()
        schedule = self.service.preparation_scheduler(self.store.job(work), '')
        schedule({**self.schedule_args(row), 'goal': '스트레칭하라고 알리기', 'title': '스트레칭'})
        self.assertEqual(len(self.active(work)), 2)

    def test_no_marker_when_nothing_follows(self):
        work, _row = self.scheduled_work()
        self.step(work, following=None)
        with self.store.db() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM tool_events WHERE job_id=? AND tool='still_record_continuation'",
                                         (work,)).fetchone())
        schedule = self.service.preparation_scheduler(self.store.job(work), '')
        schedule({**self.schedule_args(_row), 'goal': '스트레칭하라고 알리기', 'title': '스트레칭'})

    def test_an_opened_line_is_reconciled_and_not_opened_twice(self):
        self.script = [{'content': None, 'tool_calls': [call('1', 'entrusted_line', action='open', task='가격 변동 확인',
                                                             report_promise='바뀌면 알려 줘')]}, finish('f', '1')]
        work = self.receive('가격 변동 확인을 맡아 줘')
        self.assertNotIn('effect', self.service.continuation_reasons(work, 'redelegate'))
        [line] = self.service.lines.lines()
        orchestration = self.step(work, following=object())
        self.assertIn(line['line_id'], orchestration.continues)
        handle = self.service.line_handler(self.store.job(work))
        with self.assertRaises(ToolError) as refused:
            handle({'action': 'open', 'task': '가격 변동 확인', 'report_promise': '바뀌면 알려 줘'})
        self.assertEqual(refused.exception.code, 'line_exists')


class OutsideEffectsUnchanged(StillRecords):
    """Negative: a record of Still's own never carries an outside change past the rule."""

    def test_an_api_dispatch_next_to_a_preparation_still_stops(self):
        work, _row = self.scheduled_work()
        save_slot(self.store, 'alpha', ['alpha.example'], 'alpha-credential-1167')
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (work,))
        ApiRequests(self.store, work, transport=lambda *args: (200, {'last-modified': email.utils.formatdate(usegmt=True)}, b'{}'),
                    approvals=self.service.browser_approvals_for(self.store.job(work))).call(POST)
        with self.store.db() as db:
            [dispatch] = db.execute("SELECT id FROM tool_events WHERE job_id=? AND tool='api_dispatch'", (work,)).fetchall()
        self.event(work, 'api_request', 'running', {'scope': 's', 'call_id': 'a1', 'host_action': 'api_request', 'declared_effect': 'mutate'})
        self.event(work, 'api_request', 'succeeded', {'scope': 's', 'call_id': 'a1', 'host_action': 'api_request',
                                                      'declared_effect': 'mutate', 'evidence': {'dispatch_event': dispatch['id']}})
        self.assertIn('effect', self.service.continuation_reasons(work, 'redelegate'))
        orchestration = self.step(work, following=object())
        self.assertEqual(orchestration.next.call_args.kwargs['effect'], 'changed')

    def test_a_browser_mutation_next_to_a_preparation_still_stops(self):
        work, _row = self.scheduled_work()
        self.event(work, 'browser_click', 'running', {'scope': 's', 'call_id': 'b1', 'host_action': 'browser_click', 'declared_effect': 'mutate'})
        self.event(work, 'browser_click', 'succeeded', {'scope': 's', 'call_id': 'b1', 'host_action': 'browser_click',
                                                        'declared_effect': 'mutate', 'evidence': {'state': 'page'}})
        self.assertIn('effect', self.service.continuation_reasons(work, 'redelegate'))
        self.assertEqual(self.step(work, following=object()).next.call_args.kwargs['effect'], 'changed')

    def test_a_record_still_cannot_read_back_stops(self):
        work, row = self.scheduled_work()
        with self.store.db() as db:
            db.execute('UPDATE preparations SET created_from=? WHERE id=?', ('another-work', row['id']))
        self.assertIn('effect', self.service.continuation_reasons(work, 'redelegate'))

    def test_other_continuations_keep_todays_rule(self):
        work, _row = self.scheduled_work()
        for purpose in ('retry', 'steer'):
            with self.subTest(purpose=purpose):
                self.assertIn('effect', self.service.continuation_reasons(work, purpose))

    def test_a_cut_off_preparation_call_is_unknown(self):
        work = self.store.enqueue('알림 하나 맞춰 줘', 'k1167', owner_typed=True)
        self.event(work, 'schedule_preparation', 'running', {'scope': 's', 'call_id': 'p1', 'host_action': 'schedule_preparation'})
        self.assertIn('unknown', self.service.continuation_reasons(work, 'redelegate'))


class ScriptedWorker:
    """The direct OpenAI route, scripted per orchestration attempt, so the real agent loop records every call.

    ``attempt()`` names the current attempt; ``steps[attempt]`` are its tool calls (``(id, name, args)`` or a
    callable returning one) or replies, and once they are spent the worker answers with its last reply.
    """

    def __init__(self, attempt, steps):
        self.attempt, self.steps, self.bodies = attempt, {key: list(value) for key, value in steps.items()}, {}

    def __call__(self, url, body, headers=None, timeout=60):
        names = [tool['name'] for tool in body.get('tools') or []]
        if 'agentos_connection_probe' in names:
            return responses_reply({'role': 'assistant', 'tool_calls': [
                {'id': 'p1', 'type': 'function', 'function': {'name': 'agentos_connection_probe', 'arguments': '{}'}}]}, body['model'])
        if not names:
            return responses_reply({'role': 'assistant', 'content': 'ok'}, body['model'])
        attempt = self.attempt()
        self.bodies.setdefault(attempt, []).append(json.dumps(body, ensure_ascii=False))
        steps = self.steps[attempt]
        step = steps.pop(0) if len(steps) > 1 else steps[0]
        step = step() if callable(step) else step
        if isinstance(step, str):
            return responses_reply({'role': 'assistant', 'content': step}, body['model'])
        ident, name, args = step
        return responses_reply({'role': 'assistant', 'tool_calls': [
            {'id': ident, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args, ensure_ascii=False)}}]},
            body['model'])


class RealOrchestration(Harness):
    """The real ``Orchestration.next`` over a scripted Judgment AI, with a worker whose calls the real loop records."""

    def test_a_not_reached_attempt_re_delegates_and_the_next_revises_the_same_preparation(self):
        from datetime import datetime, timedelta, timezone

        def due(days):
            return (datetime.now(timezone.utc) + timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%S+00:00')
        args = {'kind': 'reminder', 'goal': '출발 시간입니다.', 'timezone': 'Asia/Seoul'}
        first = {}
        job = {}

        def attempt():
            return max(1, len(self.events(job['id'], 'planned'))) if job else 1

        def replacing():
            with self.store.db() as db:
                first['id'] = db.execute('SELECT id FROM preparations ORDER BY created_at LIMIT 1').fetchone()['id']
            return 'c3', 'schedule_preparation', {**args, 'due': due(2), 'replaces': first['id']}
        worker = ScriptedWorker(attempt, {1: [('c1', 'schedule_preparation', {**args, 'due': due(1)}), '알림을 맞췄어요.'],
                                          2: [('c2', 'schedule_preparation', {**args, 'due': due(2)}), replacing, '알림을 바꿨어요.']})
        self.service.adapter = ModelAdapter(worker)
        self.use_main('openai')
        self.script([plan('openai', 'Set the reminder.'), plan('openai', 'Finish what is missing.')],
                    goals=[False] * 4 + [True] * 4)
        job['id'] = self.store.enqueue('내일 출발하라고 알려줘', 'k1167-loop', owner_typed=True, owner_words=True)
        self.assertTrue(self.service.run_one())
        job = job['id']
        evaluated = [detail for _status, detail in self.events(job, 'evaluated')]
        # Attempt 1 was judged not reached; the real next() re-delegated instead of stopping on effect.
        self.assertEqual([(detail['outcome'], detail['next']) for detail in evaluated][:1], [('not_reached', 'redelegate')], evaluated)
        self.assertEqual(len(evaluated), 2, evaluated)
        # Attempt 2's brief names the preparation Still already holds.
        self.assertIn(first['id'], worker.bodies[2][0])
        # A new row without replaces is refused; replaces revises it, leaving one active preparation.
        with self.store.db() as db:
            calls = [(row['status'], json.loads(row['detail'])) for row in db.execute(
                "SELECT status,detail FROM tool_events WHERE job_id=? AND tool='schedule_preparation' AND status!='running' ORDER BY id",
                (job,))]
            active = db.execute('SELECT id FROM preparations WHERE created_from=? AND state IN (?,?,?,?)',
                                (job, *prep.ACTIVE_STATES)).fetchall()
            replaced = db.execute('SELECT state FROM preparations WHERE id=?', (first['id'],)).fetchone()['state']
        self.assertEqual([(status, detail.get('code')) for status, detail in calls],
                         [('succeeded', None), ('failed', 'preparation_exists'), ('succeeded', None)])
        self.assertEqual(replaced, prep.STATE_CANCELLED)
        self.assertEqual([row['id'] for row in active], [calls[2][1]['evidence']['preparation_id']])
        self.assertNotEqual(active[0]['id'], first['id'])
