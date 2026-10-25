"""STEER-01 (#999): a message sent while a Work runs can steer that Work.

#308 S1 (the finish guard): an answer is never delivered with a steer its worker
did not see; one continuation on the same worker answers it, or it runs as its
own Work.  #308 S4: each such message's disposition is told, from the recorded
steer state.

Evidence class: model-free, fixture transports.  A scripted compatible-API
model, a recording Telegram transport and a FixtureDecisionEngine replace only
the provider and the wires; the real QuickStore, ``AgentService`` (Telegram
ingress, ``run_one``), ``run_agent`` and the MCP bridge helper run unchanged.
No live model or Telegram is contacted, and none is claimed.
"""
import json
import threading
import time
import unittest
from unittest import mock

from personal_agent import mcp_bridge, quickstart_service
from personal_agent.agent_runtime import STEER_DRAFT_LEAD, STEER_EVENT, STEER_LEAD, claim_owner_steers
from personal_agent.orchestrator import (EVENT_TOOL as ORCHESTRATION_EVENT, EVALUATED, NOT_REACHED, STEER_CONTINUED,
                                         STOP_STEER, Attempt, Catalogue, Orchestration)
from personal_agent.decision import OUTCOME_DECIDED, BinaryDecision, FixtureDecisionEngine, fixture_confidence
from personal_agent.messages import msg
from personal_agent.quickstart_service import STEER_FOLDED_TEXT, STEER_NOT_APPLIED, STEER_SECOND_PASS_TEXT
from personal_agent.telegram_locale import translate

from test_preparations import CHAT, GENERATION, _Case, call, finish

FIRST = '마음에 드는 벨트 네 개를 장바구니에 한 번에 담아줘'
REMARK = '보니까 내가 로고가 크게 박힌 걸 싫어하네'
LATER = '그리고 갈색으로'
DRAFT = '담았어요.'
REPLY = {'content': DRAFT}
STEERED = '로고가 크게 박히지 않은 벨트 네 개로 담았어요.'


def steer_engine(steers):
    """Goal judgments pass; the steer judgment answers ``steers`` (None: unavailable)."""
    def judge(context, proposition):
        if context.purpose == 'goal-reached':
            return BinaryDecision(OUTCOME_DECIDED, True, fixture_confidence())
        if context.purpose == 'running-work-steer' and steers is not None:
            return BinaryDecision(OUTCOME_DECIDED, steers, fixture_confidence())
        return None
    return FixtureDecisionEngine(judge=judge)


class _SteerCase(_Case):
    STEERS = True

    def setUp(self):
        super().setUp()
        self.judge = steer_engine(self.STEERS)
        self.service.use_decision_engine(self.judge)
        # The judgment runs inline here instead of on its own thread.
        self.service.steer_spawn = lambda target: target()
        self.seen = []

    def typed(self, text):
        self.update_id += 1
        self.message_id += 1
        self.service.ingest_update({'update_id': self.update_id, 'message': {
            'message_id': self.message_id, 'from': {'id': CHAT}, 'chat': {'id': CHAT, 'type': 'private'},
            'text': text, 'date': int(self.now)}}, GENERATION)
        return self.message_id

    def jobs(self):
        with self.store.db() as db:
            return {row['message']: dict(row) for row in db.execute('SELECT * FROM jobs ORDER BY created')}

    def events(self, job_id, tool):
        with self.store.db() as db:
            return [dict(row) for row in db.execute('SELECT * FROM tool_events WHERE job_id=? AND tool=? ORDER BY id',
                                                    (job_id, tool))]

    def typed_from_poll_thread(self, text):
        """Ingest on another thread, as the Telegram poll thread does while the work thread runs."""
        sent = []
        thread = threading.Thread(target=lambda: sent.append(self.typed(text)))
        thread.start()
        thread.join()
        return sent[0]

    def remark_mid_run(self, body):
        """The first model call: the owner sends a remark while this Work runs, then a tool is called."""
        self.remark_message = self.typed_from_poll_thread(REMARK)
        return {'content': None, 'tool_calls': [call('1', 'list_notes')]}

    def after_tool(self, body):
        self.seen.append(json.loads(json.dumps(body['messages'])))
        return finish('f', '1', summary='담았어요.')

    def remark_then_finish(self, body):
        """The owner sends a remark while the worker writes a tool-free answer.

        The direct route asks once more after a tool-free reply (its execution
        check), so each tool-free run here is that call and ``REPLY`` after it.
        """
        self.remark_message = self.typed_from_poll_thread(REMARK)
        return {'content': DRAFT}

    def continued(self, body):
        self.seen.append(json.loads(json.dumps(body['messages'])))
        return {'content': STEERED}


class SteeringReachesTheRunningWork(_SteerCase):
    def test_a_remark_mid_run_reaches_the_worker_and_is_not_answered_twice(self):
        self.script = [self.remark_mid_run, self.after_tool]
        self.typed(FIRST)
        self.assertTrue(self.service.run_one())

        [messages] = self.seen
        steer = [m for m in messages if m.get('role') == 'user' and str(m.get('content', '')).startswith(STEER_LEAD)]
        self.assertEqual(len(steer), 1)
        self.assertIn(REMARK, steer[0]['content'])
        # The steer follows the tool result it rode on.
        self.assertEqual(messages[-1], steer[0])

        jobs = self.jobs()
        first, remark = jobs[FIRST], jobs[REMARK]
        self.assertEqual((remark['status'], remark['relation_kind'], remark['related_job_id']),
                         ('queued', 'steer', first['id']))
        self.assertEqual([e['status'] for e in self.events(first['id'], STEER_EVENT)], ['received', 'delivered'])
        # Content-free Evidence: the remark text is not in the events.
        self.assertTrue(all(REMARK not in (e['detail'] or '') for e in self.events(first['id'], STEER_EVENT)))

        self.telegram.clear()
        self.assertTrue(self.service.run_one())
        remark = self.jobs()[REMARK]
        self.assertEqual((remark['status'], remark['response'], remark['delivery']),
                         ('succeeded', STEER_FOLDED_TEXT, 'none'))
        self.assertEqual(self.sends(), [], 'a folded remark gets no second answer')
        reactions = [body for method, body in self.telegram if method == 'setMessageReaction']
        self.assertEqual([(r['message_id'], r['reaction']) for r in reactions],
                         [(self.remark_message, [{'type': 'emoji', 'emoji': '👌'}])])
        # The owner's words stay in the conversation.
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM messages WHERE role='user' AND content=?",
                                        (REMARK,)).fetchone()[0], 1)
        self.assertFalse(self.service.run_one())

    def test_a_tool_free_answer_with_a_steer_gives_one_reply_that_reflects_it(self):
        # #308 S1: nothing carried the remark to the worker, so the finish guard continues once.
        self.script = [self.remark_then_finish, REPLY, self.continued, {'content': STEERED}]
        self.typed(FIRST)
        self.assertTrue(self.service.run_one())

        [messages] = self.seen
        request = messages[-1]['content']
        self.assertTrue(request.startswith(FIRST), 'the original request leads the continuation')
        self.assertIn(STEER_LEAD + '\n- ' + REMARK, request)
        self.assertIn(STEER_DRAFT_LEAD + '\n' + DRAFT, request)
        first = self.jobs()[FIRST]
        self.assertEqual((first['status'], first['response']), ('succeeded', STEERED))
        self.assertEqual([e['status'] for e in self.events(first['id'], STEER_EVENT)], ['received', 'continued'])
        self.assertTrue(all(REMARK not in (e['detail'] or '') for e in self.events(first['id'], STEER_EVENT)))
        self.assertEqual([e['status'] for e in self.events(first['id'], ORCHESTRATION_EVENT)
                          if e['status'] == STEER_CONTINUED], [STEER_CONTINUED])
        # The continuation is a worker turn of this Work, in its record (#826).
        workers = self.store.turn_provenance(first['id'])['workers']
        self.assertEqual([bool(w.get('steer_continuation')) for w in workers], [False, True])
        # One reply: the continuation's, never the unsent draft.
        with self.store.db() as db:
            replies = [row[0] for row in db.execute("SELECT content FROM messages WHERE role='assistant' AND job_id=?",
                                                    (first['id'],))]
        self.assertEqual(replies, [STEERED])

        self.telegram.clear()
        calls = len(self.model_calls)
        self.assertTrue(self.service.run_one())
        remark = self.jobs()[REMARK]
        # #308 S4: applied by the one second pass, and told so.
        self.assertEqual((remark['status'], remark['response']), ('succeeded', STEER_SECOND_PASS_TEXT))
        self.assertEqual(len(self.model_calls), calls, 'the folded remark is not answered again')
        self.assertEqual(self.sends(), [])
        self.assertEqual([(r['message_id'], r['reaction']) for m, r in self.telegram if m == 'setMessageReaction'],
                         [(self.remark_message, [{'type': 'emoji', 'emoji': '👌'}])])

    def test_a_continuation_carries_the_steers_the_last_attempt_already_had(self):
        # Remark A reaches the worker with a tool result; remark B arrives after its last call.
        later = {}
        def second_remark(body):
            later['message'] = self.typed_from_poll_thread(LATER)
            return {'content': DRAFT}
        self.script = [self.remark_mid_run, second_remark, REPLY, self.continued, {'content': STEERED}]
        self.typed(FIRST)
        self.assertTrue(self.service.run_one())
        request = self.seen[-1][-1]['content']
        self.assertIn(STEER_LEAD + '\n- ' + REMARK + '\n- ' + LATER, request, 'both, in the order sent')
        self.assertEqual(self.jobs()[FIRST]['response'], STEERED)
        calls = len(self.model_calls)
        self.telegram.clear()
        self.assertTrue(self.service.run_one())
        self.assertTrue(self.service.run_one())
        jobs = self.jobs()
        self.assertEqual((jobs[REMARK]['response'], jobs[LATER]['response']), (STEER_SECOND_PASS_TEXT, STEER_SECOND_PASS_TEXT))
        self.assertEqual(len(self.model_calls), calls, 'neither is answered twice')
        self.assertEqual(sorted(r['message_id'] for m, r in self.telegram if m == 'setMessageReaction'),
                         sorted([self.remark_message, later['message']]))

    def assert_runs_as_its_own_work(self, reason):
        first = self.jobs()[FIRST]
        self.assertEqual(first['response'], DRAFT, 'the reply is delivered as written')
        self.assertFalse(any(STEER_LEAD in json.dumps(body, ensure_ascii=False) for body in self.model_calls),
                         'no continuation')
        model_calls = len(self.model_calls)
        self.assertNotIn('continued', [e['status'] for e in self.events(first['id'], STEER_EVENT)])
        # #308 S4: the guard records why, content-free.
        self.assertIn({'reason': reason}, [json.loads(e['detail']) for e in self.events(first['id'], STEER_EVENT)
                                           if e['status'] == STEER_NOT_APPLIED])
        self.assertFalse(self.store.steer_answered(self.jobs()[REMARK]['id']))
        self.telegram.clear()
        self.assertTrue(self.service.run_one())
        remark = self.jobs()[REMARK]
        self.assertNotEqual(remark['response'], STEER_FOLDED_TEXT)
        self.assertGreater(len(self.model_calls), model_calls, 'the remark ran as its own Work')
        self.assertNotIn([{'type': 'emoji', 'emoji': '👌'}],
                         [r['reaction'] for m, r in self.telegram if m == 'setMessageReaction'])
        self.assert_told(msg('steer.not_applied.' + reason))

    def assert_told(self, line):
        """#308 S4: the remark's own reply starts with its disposition, stored and sent."""
        remark = self.jobs()[REMARK]
        self.assertTrue(remark['response'].startswith(line + '\n\n'), remark['response'])
        for _ in range(3):  # one pending reply per call: the earlier Work's, then this one's
            self.service.deliver_one()
        self.assertTrue(any(text.startswith(line) for text in self.texts()), self.texts())

    def test_no_continuation_after_an_effect(self):
        # The worker saved a note (an effect), then the remark arrived while it wrote the answer.
        self.script = [{'content': None, 'tool_calls': [call('1', 'save_note', content='벨트 메모')]},
                       self.remark_then_finish, REPLY]
        self.typed(FIRST)
        self.assertTrue(self.service.run_one())
        self.assert_runs_as_its_own_work('effect')

    def test_no_continuation_while_an_approval_is_pending(self):
        work = {}
        real = self.service._browser_request
        self.service._browser_request = lambda work_id: ({'state': 'requested', 'requested_at': self.now}
                                                         if work_id == work.get('id') else real(work_id))
        def remark_then_finish(body):
            work['id'] = self.jobs()[FIRST]['id']
            return self.remark_then_finish(body)
        self.script = [remark_then_finish, REPLY]
        self.typed(FIRST)
        self.assertTrue(self.service.run_one())
        self.service._browser_request = real
        self.assert_runs_as_its_own_work('owner')

    def test_no_continuation_once_the_budget_is_spent(self):
        self.script = [self.remark_then_finish, REPLY]
        self.typed(FIRST)
        with mock.patch.object(quickstart_service, 'MIN_ATTEMPT_SECONDS', 10 ** 9):
            self.assertTrue(self.service.run_one())
        self.assert_runs_as_its_own_work('budget')

    def test_a_failed_continuation_delivers_the_earlier_reply_and_the_remark_runs_as_its_own_work(self):
        # The provider's reply to the continuation is unreadable; nothing had an effect.
        self.script = [self.remark_then_finish, REPLY, lambda body: 'not a message']
        self.typed(FIRST)
        self.assertTrue(self.service.run_one())
        first = self.jobs()[FIRST]
        self.assertEqual((first['status'], first['response']), ('succeeded', DRAFT))
        self.assertEqual([e['status'] for e in self.events(first['id'], STEER_EVENT)],
                         ['received', 'continued', 'continuation_failed'])
        self.assertFalse(self.store.steer_delivered(self.jobs()[REMARK]['id']), 'released, not answered')
        self.assertFalse(self.store.steer_answered(self.jobs()[REMARK]['id']))
        calls = len(self.model_calls)
        self.telegram.clear()
        self.assertTrue(self.service.run_one())
        self.assertNotEqual(self.jobs()[REMARK]['response'], STEER_FOLDED_TEXT)
        self.assertGreater(len(self.model_calls), calls)
        self.assert_told(msg('steer.not_applied.failed'))

    def test_the_disposition_line_reaches_the_owner_in_their_language(self):
        line = msg('steer.not_applied.effect')
        shown = translate(line + '\n\n' + '여기 답이에요.', 'en')
        self.assertTrue(shown.startswith(msg('steer.not_applied.effect', 'en') + '\n\n'), shown)
        self.assertEqual(translate(STEER_SECOND_PASS_TEXT, 'ja'), msg('steer.applied_second_pass', 'ja'))


class SteerJudgedWhileTheWorkFinishes(_SteerCase):
    def test_a_judgment_still_running_when_the_worker_finishes_is_awaited(self):
        # The judgment runs on its own thread and answers only after the worker's last call returned.
        gate = threading.Event()
        threads = []
        def spawn(target):
            thread = threading.Thread(target=lambda: (gate.wait(5), target()))
            threads.append(thread)
            thread.start()
        self.service.steer_spawn = spawn
        def finish_then_release(body):
            reply = self.remark_then_finish(body)
            threading.Timer(0.2, gate.set).start()
            return reply
        self.script = [finish_then_release, REPLY, self.continued, {'content': STEERED}]
        self.typed(FIRST)
        self.assertTrue(self.service.run_one())
        for thread in threads:
            thread.join(5)
        self.assertEqual(self.jobs()[FIRST]['response'], STEERED)
        self.assertTrue(self.store.steer_answered(self.jobs()[REMARK]['id']))

    def test_a_steer_after_the_guard_closed_runs_as_its_own_work_once(self):
        # The guard decided to finish; a steer judged now is refused, not lost and not answered twice.
        refused = []
        real = self.service._finish_work
        def finish(run):
            remark = self.store.enqueue(REMARK, 'late-remark', 'telegram', CHAT)
            refused.append(self.service.judge_steer(run.job['id'], remark, REMARK))
            return real(run)
        self.service._finish_work = finish
        self.script = [REPLY, REPLY]
        self.typed(FIRST)
        self.assertTrue(self.service.run_one())
        self.assertEqual(refused, [False])
        self.assertEqual(self.events(self.jobs()[FIRST]['id'], STEER_EVENT), [])
        self.assertEqual(self.jobs()[FIRST]['response'], DRAFT)
        self.service._finish_work = real
        calls = len(self.model_calls)
        self.assertTrue(self.service.run_one())
        self.assertEqual(self.jobs()[REMARK]['relation_kind'], None)
        self.assertGreater(len(self.model_calls), calls, 'the late message ran as its own Work')
        # #308 S4: told that it came after the reply was settled; the record is content-free.
        late = self.jobs()[REMARK]
        self.assertEqual([(e['status'], json.loads(e['detail'])) for e in self.events(late['id'], STEER_EVENT)],
                         [(STEER_NOT_APPLIED, {'reason': 'finished'})])
        self.assertTrue(late['response'].startswith(msg('steer.not_applied.finished') + '\n\n'))
        self.assertFalse(self.service.run_one())


class SteeredWorkFailed(_SteerCase):
    def test_a_steer_claimed_by_a_work_that_then_failed_runs_as_its_own_work(self):
        self.script = [self.remark_mid_run, self.after_tool]
        self.typed(FIRST)
        self.assertTrue(self.service.run_one())
        first = self.jobs()[FIRST]
        self.assertTrue(self.store.steer_delivered(self.jobs()[REMARK]['id']))
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='failed' WHERE id=?", (first['id'],))
        calls = len(self.model_calls)
        self.assertTrue(self.service.run_one())
        self.assertNotEqual(self.jobs()[REMARK]['response'], STEER_FOLDED_TEXT)
        self.assertGreater(len(self.model_calls), calls)
        self.assertTrue(self.jobs()[REMARK]['response'].startswith(msg('steer.not_applied.failed') + '\n\n'))


class DispositionFromRecordedState(_SteerCase):
    """#308 S4: the line comes from what was recorded about the steered Work, never from the message."""

    def steered(self, status, *events):
        self.case = getattr(self, 'case', 0) + 1
        first = self.store.enqueue(FIRST, 'k-%d' % self.case)
        remark = self.store.enqueue(REMARK, 'r-%d' % self.case)
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (first,))
        self.assertTrue(self.store.add_steer(first, remark, REMARK))
        with self.store.db() as db:
            db.execute('UPDATE jobs SET status=? WHERE id=?', (status, first))
            for event, detail in events:
                db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                           (first, STEER_EVENT, event, json.dumps(detail), time.time()))
        return self.store.job(remark)

    def test_each_recorded_state_names_one_disposition(self):
        cases = [(('succeeded', (STEER_NOT_APPLIED, {'reason': 'route'})), 'route'),
                 (('succeeded', (STEER_NOT_APPLIED, {'reason': 'continued'})), 'continued'),
                 (('succeeded', (STEER_NOT_APPLIED, {'reason': 'stopped'})), 'stopped'),
                 (('succeeded', ('continued', {}), ('continuation_failed', {})), 'failed'),
                 (('awaiting_connection',), 'owner'),
                 (('unknown',), 'effect'),
                 (('interrupted',), 'failed'),
                 (('succeeded',), 'new_request')]
        for (status, *events), reason in cases:
            with self.subTest(status=status, events=events):
                self.assertEqual(self.service.steer_disposition_notice(self.steered(status, *events)),
                                 msg('steer.not_applied.' + reason))

    def test_a_message_with_no_steer_record_gets_no_line(self):
        plain = self.store.job(self.store.enqueue('다른 요청', 'plain'))
        self.assertEqual(self.service.steer_disposition_notice(plain), '')
        with self.store.db() as db:
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (plain['id'], STEER_EVENT, STEER_NOT_APPLIED, json.dumps({'reason': 'not-a-reason'}), time.time()))
        self.assertEqual(self.service.steer_disposition_notice(plain), '', 'an unknown recorded reason says nothing')


class NotASteer(_SteerCase):
    STEERS = False

    def test_a_new_request_mid_run_stays_its_own_work(self):
        self.script = [self.remark_mid_run, self.after_tool, {'content': '네, 알겠어요.'}]
        self.typed(FIRST)
        self.assertTrue(self.service.run_one())
        [messages] = self.seen
        self.assertFalse(any(str(m.get('content', '')).startswith(STEER_LEAD) for m in messages))
        remark = self.jobs()[REMARK]
        self.assertEqual((remark['status'], remark['relation_kind']), ('queued', None))
        self.assertEqual(self.events(self.jobs()[FIRST]['id'], STEER_EVENT), [])
        self.assertTrue(self.service.run_one())
        self.assertNotEqual(self.jobs()[REMARK]['response'], STEER_FOLDED_TEXT)
        # #308 S4: a new request's own reply is its disposition; no line is added.
        response = self.jobs()[REMARK]['response']
        self.assertFalse(any(response.startswith(msg('steer.not_applied.' + reason))
                             for reason in quickstart_service.STEER_NOT_APPLIED_REASONS), response)


class Unavailable(NotASteer):
    """No answer from the Judgment AI keeps today's behaviour: the message waits its turn."""
    STEERS = None


class SteerContinuationIsTheLastAttempt(unittest.TestCase):
    """#308 S1: the orchestrator's side of a continuation, without a service."""

    def test_the_continuation_keeps_the_worker_reads_the_steer_and_is_never_redelegated(self):
        worker = {'id': 'a', 'kind': 'subscription', 'name': 'A', 'destination': '', 'default': True, 'available': True,
                  'reason': '', 'native_search': True, 'browser': False, 'cost': 'c', 'latency': 'l',
                  'default_model': '', 'models': ['m1'], 'model_tiers': {}, 'tools': []}
        asked, events = [], []

        class Judgments:
            def goal_reached(self, request, observed, failed, **kwargs):
                asked.append(request)
                return type('Judged', (), {'outcome': 'no', 'value': '로고', 'note': None})()

        orchestration = Orchestration(Judgments(), Catalogue([worker], {}, 'a'), request=FIRST,
                                      record=lambda status, detail: events.append((status, detail)))
        orchestration.orchestrated = True
        attempt = Attempt(1, 'a', model='m1', notes='notes', planned=True)
        orchestration.attempts.append(attempt)
        following = orchestration.continue_for_steers(attempt, REMARK, 1)
        self.assertEqual((following.number, following.worker, following.model, following.steer, attempt.steer),
                         (2, 'a', 'm1', True, False))
        self.assertEqual(following.brief(), 'notes')
        self.assertEqual([status for status, _ in events], [STEER_CONTINUED])
        self.assertNotIn(REMARK, json.dumps(events, ensure_ascii=False), 'content-free Evidence')
        self.assertEqual(orchestration.evaluate_answer(DRAFT, 'none'), NOT_REACHED)
        self.assertEqual(asked, [FIRST + '\n\n' + REMARK], 'the outcome judgment reads the steer with the request')
        self.assertIsNone(orchestration.next(following, NOT_REACHED, answer=DRAFT))
        [(status, detail)] = events[1:]
        self.assertEqual((status, detail['stop'], orchestration.terminal), (EVALUATED, STOP_STEER, NOT_REACHED))


class StoreAndBridge(_SteerCase):
    def test_a_steer_is_recorded_only_while_the_work_runs_and_its_message_waits(self):
        first = self.store.enqueue(FIRST, 'k-1')
        remark = self.store.enqueue(REMARK, 'k-2')
        self.assertFalse(self.store.add_steer(first, remark, REMARK), 'not running yet')
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (first,))
        self.assertTrue(self.store.add_steer(first, remark, REMARK))
        self.assertEqual(self.store.claim_steers(first), [REMARK])
        self.assertEqual(self.store.claim_steers(first), [], 'each steer is handed over once')
        self.assertTrue(self.store.steer_delivered(remark))
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='succeeded' WHERE id=?", (first,))
        other = self.store.enqueue('다른 말', 'k-3')
        self.assertFalse(self.store.add_steer(first, other, '다른 말'), 'a finished Work takes no steer')

    def test_only_a_steer_its_delivered_reply_was_made_after_is_answered(self):
        # #308 S1: judged -> claimed -> answered; a claim by an attempt whose reply was dropped is not an answer.
        first = self.store.enqueue(FIRST, 'k-1')
        remark = self.store.enqueue(REMARK, 'k-2')
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (first,))
        self.assertTrue(self.store.add_steer(first, remark, REMARK))
        self.assertEqual(self.store.unanswered_steers(first, 0), [REMARK], 'judged, not yet claimed')
        self.assertEqual(self.store.claim_steers(first), [REMARK])
        self.assertTrue(self.store.steer_delivered(remark))
        self.assertFalse(self.store.steer_answered(remark), 'claimed is not answered')
        next_attempt = time.time() + 1  # a later attempt started after the claim
        self.assertEqual(self.store.unanswered_steers(first, next_attempt), [REMARK],
                         'claimed by an earlier attempt: the delivered reply never saw it')
        self.assertEqual(self.store.claim_unanswered_steers(first, next_attempt), [REMARK])
        self.assertEqual(self.store.unanswered_steers(first, next_attempt), [], 'claimed again once')
        self.assertEqual(self.store.claim_steers(first), [], 'and not handed to the bridge a second time')
        with self.store.db() as db:
            self.store.answer_steers(first, next_attempt, db)
        self.assertTrue(self.store.steer_answered(remark))
        with self.store.db() as db:
            self.store.release_steers(first, next_attempt, db)
        self.assertTrue(self.store.steer_answered(remark), 'an answered steer is never released')

    def test_the_bridge_appends_the_steer_to_the_next_tool_result(self):
        first = self.store.enqueue(FIRST, 'k-1')
        remark = self.store.enqueue(REMARK, 'k-2')
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (first,))
        self.assertTrue(self.store.add_steer(first, remark, REMARK))
        recorded = []
        result = {'content': [{'type': 'text', 'text': json.dumps({'ok': True})}]}
        mcp_bridge._with_steer(result, self.store, first, lambda *args: recorded.append(args))
        self.assertEqual(len(result['content']), 2)
        self.assertTrue(result['content'][1]['text'].startswith(STEER_LEAD))
        self.assertIn(REMARK, result['content'][1]['text'])
        self.assertEqual([(tool, status) for tool, status, _ in recorded], [(STEER_EVENT, 'delivered')])
        again = {'content': [{'type': 'text', 'text': '{}'}]}
        mcp_bridge._with_steer(again, self.store, first, lambda *args: recorded.append(args))
        self.assertEqual(len(again['content']), 1)
        self.assertIsNone(claim_owner_steers(object(), first))


if __name__ == '__main__':
    unittest.main()
