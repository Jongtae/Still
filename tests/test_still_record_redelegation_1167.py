"""#1167: a record Still holds itself is reconciled from Still's own store, in any state, not stopped on.

Real store, the real worker loop and orchestration with a scripted model and Judgment AI, the real
``schedule_preparation`` and ``entrusted_line`` handlers and the one continuation rule.  No model,
browser or account calls.  The negative tests hold the #618 invariant: an outside change is never
re-delegated past.  No Still record is fenced (C17): the next attempt reads each record's facts.
"""
import email.utils
import json
from types import SimpleNamespace
from unittest.mock import Mock

from personal_agent import preparations as prep
from personal_agent.agent_runtime import revisable_record_note
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
        [row] = [row for row in self.service.preparations.rows() if row['created_from'] == work]
        return work, row

    def event(self, work, tool, status, detail):
        with self.store.db() as db:
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,0)',
                       (work, tool, status, json.dumps(detail)))

    def step(self, work):
        orchestration = SimpleNamespace(orchestrated=True, _redact=lambda text, **_: text, next=Mock(return_value=object()),
                                        evaluate_answer=Mock(return_value='not_reached'), may_judge=lambda: True,
                                        budget_allows=lambda **_: True, budget=None, owner_told=Mock(), continues='')
        self.service.orchestration_step(orchestration, object(), work, 0, answer='아직 목표에 못 미쳤어요', outcome='partial')
        return orchestration.next.call_args.kwargs['effect']

    def facts(self, work):
        return json.loads(revisable_record_note(self.store, work).rsplit('\n', 1)[1])


class Reconciled(StillRecords):
    def test_a_record_in_any_state_re_delegates_and_is_carried_as_a_fact(self):
        for settle in ('scheduled', 'running', 'delivered', 'cancelled', 'deleted'):
            with self.subTest(settle=settle):
                work, row = self.scheduled_work(title=f'물 알림 {settle}')
                if settle == 'running':
                    self.now = row['due_at'] + 1
                    self.tick()
                elif settle == 'delivered':
                    with self.store.db() as db:  # the terminal state its delivery leaves
                        db.execute('UPDATE preparations SET state=? WHERE id=?', (prep.STATE_DELIVERED, row['id']))
                elif settle == 'cancelled':
                    self.service.preparations.cancel(row['id'])
                elif settle == 'deleted':
                    self.service.preparations.delete(row['id'])
                self.assertNotIn('effect', self.service.continuation_reasons(work, 'redelegate'))
                self.assertIs(self.step(work), False)
                [fact] = self.facts(work)
                self.assertEqual((fact['preparation_id'], fact['state']), (row['id'], settle))
                if settle != 'deleted':
                    self.assertEqual(fact.get('title'), row['title_text'] or None)
                    self.assertEqual(fact['due'], prep.iso(row['due_at'], row['timezone']))
                self.now = self.START

    def test_an_opened_line_is_carried_with_its_state(self):
        self.script = [{'content': None, 'tool_calls': [call('1', 'entrusted_line', action='open', task='가격 변동 확인',
                                                             report_promise='바뀌면 알려 줘')]}, finish('f', '1')]
        work = self.receive('가격 변동 확인을 맡아 줘')
        [line] = self.service.lines.lines()
        self.service.preparations_service.stop_line(line['line_id'], work, end_line=True)
        self.assertIs(self.step(work), False)
        self.assertIn((line['line_id'], 'stopped'), [(fact.get('line_id'), fact['state']) for fact in self.facts(work)])

    def test_the_note_carries_no_goal(self):
        work, _row = self.scheduled_work()
        self.assertNotIn('물 마시라고 알리기', revisable_record_note(self.store, work))


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
        self.assertEqual(self.step(work), 'changed')

    def test_a_browser_mutation_next_to_a_preparation_still_stops(self):
        work, _row = self.scheduled_work()
        self.event(work, 'browser_click', 'running', {'scope': 's', 'call_id': 'b1', 'host_action': 'browser_click', 'declared_effect': 'mutate'})
        self.event(work, 'browser_click', 'succeeded', {'scope': 's', 'call_id': 'b1', 'host_action': 'browser_click',
                                                        'declared_effect': 'mutate', 'evidence': {'state': 'page'}})
        self.assertIn('effect', self.service.continuation_reasons(work, 'redelegate'))
        self.assertEqual(self.step(work), 'changed')

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

    def test_a_not_reached_attempt_re_delegates_and_the_next_brief_carries_the_record(self):
        # Attempt 1 schedules a reminder; the owner cancels it while the attempt runs; the reply is judged not reached.
        # origin/main stops on effect (a cancelled row was no record to revise); now the Work re-delegates and the
        # replacement attempt's brief carries the record with its current state.
        from datetime import datetime, timedelta, timezone

        def due(days):
            return (datetime.now(timezone.utc) + timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%S+00:00')
        job = {}

        def attempt():
            return max(1, len(self.events(job['id'], 'planned'))) if job else 1

        def owner_cancels():
            with self.store.db() as db:
                job['row'] = db.execute('SELECT id FROM preparations ORDER BY created_at LIMIT 1').fetchone()['id']
            self.service.preparations.cancel(job['row'])
            return '알림을 맞췄어요.'
        worker = ScriptedWorker(attempt, {1: [('c1', 'schedule_preparation', {'kind': 'reminder', 'goal': '출발 시간입니다.',
                                                                              'timezone': 'Asia/Seoul', 'due': due(1),
                                                                              'title': '출발 알림'}), owner_cancels],
                                          2: ['취소된 알림을 확인했어요.']})
        self.service.adapter = ModelAdapter(worker)
        self.use_main('openai')
        self.script([plan('openai', 'Set the reminder.'), plan('openai', 'Finish what is missing.')],
                    goals=[False] * 4 + [True] * 4)
        job['id'] = self.store.enqueue('내일 출발하라고 알려줘', 'k1167-loop', owner_typed=True, owner_words=True)
        self.assertTrue(self.service.run_one())
        evaluated = [detail for _status, detail in self.events(job['id'], 'evaluated')]
        self.assertEqual([(detail['outcome'], detail['next']) for detail in evaluated][:1], [('not_reached', 'redelegate')], evaluated)
        self.assertEqual(len(evaluated), 2, evaluated)
        brief = worker.bodies[2][0]
        self.assertIn(job['row'], brief)
        self.assertIn('\\"state\\": \\"cancelled\\"', brief, 'the record as a fact with its current state')
