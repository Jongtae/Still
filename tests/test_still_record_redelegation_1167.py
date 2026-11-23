"""#1167: a record Still holds itself is reconciled from Still's own store, not stopped on as an outside effect.

Real store, the real worker loop with a scripted model, the real ``schedule_preparation`` and
``entrusted_line`` handlers and the one continuation rule.  No model, browser or account calls.
The negative tests hold the #618 invariant: an outside change is never re-delegated past, and a
re-delegated attempt never leaves a second preparation for the same Work.
"""
import email.utils
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from personal_agent import preparations as prep
from personal_agent.agent_runtime import ToolError, revisable_record_note
from personal_agent.decision import OUTCOME_DECIDED, BinaryDecision, FixtureDecisionEngine, fixture_confidence
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
        # #1170's brief note (the worker's host facts) names it; #1167 adds the fence below.
        work, row = self.scheduled_work(title='오후 물 알림')
        orchestration = self.step(work, following=object())
        self.assertIs(orchestration.next.call_args.kwargs['effect'], False)
        self.assertIn(row['id'], revisable_record_note(self.store, work))

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

    def test_a_replacement_moves_the_fence_to_its_successor(self):
        work, row = self.scheduled_work()
        self.step(work, following=object())
        schedule = self.service.preparation_scheduler(self.store.job(work), '')
        successor = schedule(self.schedule_args(row, replaces=row['id']))['preparation_id']
        with self.assertRaises(ToolError) as refused:
            schedule(self.schedule_args(row))
        self.assertEqual(refused.exception.code, 'preparation_exists')
        self.assertIn(successor, str(refused.exception))

    def test_a_carried_preparation_that_settles_after_re_delegation_stays_fenced(self):
        # Codex P2: it fired, or the owner cancelled or deleted it, after the re-delegation was decided.
        for settle in ('ran', 'cancelled', 'deleted'):
            with self.subTest(settle=settle):
                work, row = self.scheduled_work(title=settle)
                self.step(work, following=object())
                if settle == 'ran':
                    self.now = row['due_at'] + 1
                    self.tick()
                elif settle == 'cancelled':
                    self.service.preparations.cancel(row['id'])
                else:
                    self.service.preparations.delete(row['id'])
                schedule = self.service.preparation_scheduler(self.store.job(work), '')
                for extra in ({}, {'replaces': row['id']}):
                    with self.assertRaises(ToolError) as refused:
                        schedule(self.schedule_args(row, **extra))
                    self.assertEqual(refused.exception.code, 'preparation_settled')
                self.assertEqual([other['id'] for other in self.service.preparations.rows() if other['created_from'] == work],
                                 [] if settle == 'deleted' else [row['id']], 'nothing new was scheduled')

    def test_a_preparation_that_settles_before_the_marker_is_still_fenced(self):
        # Codex P2: it is delivered between the continuation rule's read-back and the fence's read.
        import personal_agent.agent_runtime as runtime
        work, row = self.scheduled_work()
        read = runtime.still_record_fence

        def settles_first(store, work_id):
            with store.db() as db:
                db.execute('UPDATE preparations SET state=? WHERE id=?', (prep.STATE_DELIVERED, row['id']))
            return read(store, work_id)
        with patch.object(runtime, 'still_record_fence', settles_first):
            orchestration = self.step(work, following=object())
        self.assertIs(orchestration.next.call_args.kwargs['effect'], False)
        schedule = self.service.preparation_scheduler(self.store.job(work), '')
        with self.assertRaises(ToolError) as refused:
            schedule(self.schedule_args(row))
        self.assertEqual(refused.exception.code, 'preparation_settled')

    def test_a_carried_line_the_owner_stopped_is_neither_reopened_nor_restarted(self):
        self.script = [{'content': None, 'tool_calls': [call('1', 'entrusted_line', action='open', task='가격 변동 확인',
                                                             report_promise='바뀌면 알려 줘')]}, finish('f', '1')]
        work = self.receive('가격 변동 확인을 맡아 줘')
        [line] = self.service.lines.lines()
        self.step(work, following=object())
        self.service.preparations_service.stop_line(line['line_id'], work, end_line=True)
        handle = self.service.line_handler(self.store.job(work))
        for args in ({'action': 'open', 'task': '가격 변동 확인', 'report_promise': '바뀌면 알려 줘'},
                     {'action': 'restart', 'line': line['line_id']}):
            with self.subTest(action=args['action']):
                with self.assertRaises(ToolError) as refused:
                    handle(args)
                self.assertEqual(refused.exception.code, 'line_settled')
        self.assertEqual([entry['state'] for entry in self.service.lines.lines()], ['stopped'])

    def test_new_work_none_of_them_covers_is_still_set_up(self):
        # #1170's brief invites new work only where no carried record covers it: the same-goal judgment says so.
        work, row = self.scheduled_work()
        self.step(work, following=object())

        def judge(context, proposition):
            if context.purpose == 'same-preparation-goal':
                return BinaryDecision(OUTCOME_DECIDED, False, fixture_confidence())
            return None
        self.service.use_decision_engine(FixtureDecisionEngine(judge=judge))
        schedule = self.service.preparation_scheduler(self.store.job(work), '')
        schedule({**self.schedule_args(row), 'goal': '스트레칭하라고 알리기', 'title': '스트레칭'})
        self.assertEqual(len(self.active(work)), 2)

    def test_revising_one_carried_row_is_not_blocked_by_an_unrelated_one_that_ran(self):
        # Codex P2: two carried rows; one fires; the other is still revised in place unless that would redo the first.
        for same, expected in ((False, None), (True, 'preparation_settled')):
            with self.subTest(same=same):
                work, first = self.scheduled_work(title=f'first-{same}')
                schedule = self.service.preparation_scheduler(self.store.job(work), '')
                other = schedule({**self.schedule_args(first), 'goal': '스트레칭하라고 알리기', 'title': '스트레칭',
                                  'due': self.due_iso(7200)})['preparation_id']
                self.step(work, following=object())
                self.now = first['due_at'] + 1
                self.tick()

                def judge(context, proposition, same=same):
                    if context.purpose == 'same-preparation-goal':
                        return BinaryDecision(OUTCOME_DECIDED, same, fixture_confidence())
                    return None
                self.service.use_decision_engine(FixtureDecisionEngine(judge=judge))
                revise = self.service.preparation_scheduler(self.store.job(work), '')
                args = {**self.schedule_args(first), 'goal': '스트레칭 두 번 하라고 알리기', 'title': '스트레칭',
                        'due': self.due_iso(9000), 'replaces': other}
                if expected is None:
                    self.assertEqual(revise(args)['replaced']['preparation_id'], other)
                else:
                    with self.assertRaises(ToolError) as refused:
                        revise(args)
                    self.assertEqual(refused.exception.code, expected)
                    self.assertEqual(self.service.preparations.get(other)['state'], prep.STATE_SCHEDULED)
                self.service.use_decision_engine(self.judge)
                self.now = self.START

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
        self.step(work, following=object())
        self.assertIn(line['line_id'], revisable_record_note(self.store, work))
        handle = self.service.line_handler(self.store.job(work))
        with self.assertRaises(ToolError) as refused:
            handle({'action': 'open', 'task': '가격 변동 확인', 'report_promise': '바뀌면 알려 줘'})
        self.assertEqual(refused.exception.code, 'line_exists')


    def test_a_line_account_is_neither_carried_nor_fenced(self):
        # Codex P2: opening a line sets up its automatic account; the next attempt may still schedule the line's work.
        self.script = [{'content': None, 'tool_calls': [call('1', 'entrusted_line', action='open', task='가격 변동 확인',
                                                             report_promise='바뀌면 알려 줘')]}, finish('f', '1')]
        work = self.receive('가격 변동 확인을 맡아 줘')
        [account] = [row for row in self.service.preparations.rows()
                     if row['created_from'] == work and row['origin'] == prep.ORIGIN_LINE_ACCOUNT]
        self.step(work, following=object())
        self.assertNotIn(account['id'], revisable_record_note(self.store, work))
        from personal_agent.agent_runtime import still_record_claims
        with self.store.db() as db:
            self.assertNotIn(account['id'], still_record_claims(db, work))
        schedule = self.service.preparation_scheduler(self.store.job(work), '')
        scheduled = schedule({'kind': 'reminder', 'goal': '가격 확인하라고 알리기', 'due': self.due_iso(3600), 'title': '가격 확인'})
        self.assertTrue(scheduled['scheduled'])
        kept = self.service.preparations.get(account['id'])
        self.assertEqual((kept['state'], kept['updated_at']), (account['state'], account['updated_at']))


class OutsideEffectsUnchanged(StillRecords):
    def test_an_owner_cancellation_during_the_attempt_keeps_the_effect_stop(self):
        # Codex P2: re-delegation must not undo the owner's cancellation by scheduling the reminder again.
        work, row = self.scheduled_work()
        self.service.preparations.cancel(row['id'])
        self.assertIn('effect', self.service.continuation_reasons(work, 'redelegate'))
        self.assertEqual(self.step(work, following=object()).next.call_args.kwargs['effect'], 'changed')

    def test_a_row_this_work_replaced_still_reconciles(self):
        work, row = self.scheduled_work()
        self.script = [{'content': None, 'tool_calls': [call('2', 'schedule_preparation',
                                                             **self.schedule_args(row, replaces=row['id']))]}, finish('g', '2')]
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='queued',response=NULL WHERE id=?", (work,))
        self.service.run_one()
        self.assertEqual(self.service.preparations.get(row['id'])['state'], prep.STATE_CANCELLED)
        self.assertNotIn('effect', self.service.continuation_reasons(work, 'redelegate'))

    def test_a_preparation_that_already_ran_is_never_set_up_again(self):
        # Codex P2: a short reminder fired while the attempt ran.  Running, #1170 still revises it in place,
        # so #1167's fence holds it as settled; delivered, it is no record to revise and the Work stops.
        work, row = self.scheduled_work()
        self.now = row['due_at'] + 1
        self.tick()
        self.assertEqual(self.service.preparations.get(row['id'])['state'], prep.STATE_RUNNING)
        self.step(work, following=object())
        schedule = self.service.preparation_scheduler(self.store.job(work), '')
        with self.assertRaises(ToolError) as refused:
            schedule(self.schedule_args(row))
        self.assertEqual(refused.exception.code, 'preparation_settled')
        delivered, delivered_row = self.scheduled_work(title='delivered')
        with self.store.db() as db:  # the terminal state its delivery leaves
            db.execute('UPDATE preparations SET state=? WHERE id=?', (prep.STATE_DELIVERED, delivered_row['id']))
        self.assertIn('effect', self.service.continuation_reasons(delivered, 'redelegate'))
        self.assertEqual(self.step(delivered, following=object()).next.call_args.kwargs['effect'], 'changed')

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

    def test_a_record_still_no_longer_holds_stops(self):
        work, row = self.scheduled_work()
        self.service.preparations.delete(row['id'])
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
