"""#1261: a judged stop that withdraws nothing never swallows the owner's message.

Live pattern (2026-11-27): while an earlier request was running, the owner asked to
cancel a line's scheduled run and start it again on new terms.  The follow-up
judgment read the message as cancelling the earlier Work; Still could not withdraw a
running Work and answered only that it had not cancelled it, so the rest of the
message never reached the owner's AI and the scheduled row stayed active.

Evidence class: model-free, fixture transports (``test_preparations._Case``).  A
scripted compatible-API model and a scripted DecisionEngine replace only the
providers; ``AgentService`` (Telegram ingress, ``run_one``, ``continuity_relation``)
runs unchanged.
"""
import json
from unittest.mock import patch

from personal_agent import preparations as prep
from personal_agent.conversation_handoff import FOLLOWUP_CANCEL
from personal_agent.decision import (OUTCOME_DECIDED, BinaryDecision, FixtureDecisionEngine, SelectionDecision,
                                     fixture_confidence)
from personal_agent.messages import msg
from personal_agent.quickstart_service import CANCEL_NOT_DONE_WHY
from test_preparations import CHAT, GENERATION, _Case

EARLIER = '규칙 바꿔줘: 손절 기준을 2%로'
RESTART = '지금 돌고 있는 5분 예약 실행 취소하고, 지금부터 15:20까지 5분마다 다시 시작해서 조건 맞으면 주문해줘'
NOT_CANCELLED = msg('service.the_previous_request_is_already_running')


class CancelKeepsRestTests(_Case):
    def setUp(self):
        super().setUp()
        # The steer judgment of a message arriving while a Work runs is judged inline.
        self.service.steer_spawn = lambda target: target()

    def owner_work(self, text):
        with self.store.db() as db:
            return db.execute('SELECT id FROM jobs WHERE message=?', (text,)).fetchone()['id']

    def judged(self, cancel_of):
        """A scripted engine: the follow-up judgment says ``cancel`` of the candidate whose request is ``cancel_of``."""
        def choose(context, candidates, question):
            if context.purpose == 'conversation-followup':
                about = next((n for n in range(1, 6) if context.facts.get(f'w{n}_owner_message') == cancel_of), None)
                return SelectionDecision(OUTCOME_DECIDED, f'{FOLLOWUP_CANCEL} w{about}' if about else 'none-of-these',
                                         candidates, fixture_confidence())
            return None

        def judge(context, proposition):
            if context.purpose == 'goal-reached':
                return BinaryDecision(OUTCOME_DECIDED, True, fixture_confidence())
            return None
        self.judge = FixtureDecisionEngine(judge=judge, choose=choose)
        self.service.use_decision_engine(self.judge)

    def running_work(self, text, request_key):
        work = self.store.enqueue(text, request_key, f'telegram:{GENERATION}', CHAT)
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (work,))
        return work

    def worker_saw(self, text):
        return [call for call in self.model_calls if text in json.dumps(call['messages'], ensure_ascii=False)]

    def followup_facts(self):
        return [item[1].facts for item in self.judge.asked
                if item[0] == 'choose' and item[1].purpose == 'conversation-followup']

    def assert_ran_as_request(self, work, earlier, why):
        job = self.store.job(work)
        self.assertEqual(job['status'], 'succeeded', job.get('error'))
        self.assertNotEqual(job['response'], NOT_CANCELLED, 'the refusal is not the whole answer')
        self.assertEqual((job['relation_kind'], job['related_job_id']), (FOLLOWUP_CANCEL, earlier))
        [call, *_] = self.worker_saw(RESTART)
        latest = [m['content'] for m in call['messages'] if m['role'] == 'user'][-1]
        self.assertIn(CANCEL_NOT_DONE_WHY[why], latest, 'the worker is told what was not stopped and why')
        self.assertTrue(latest.endswith(RESTART), "the owner's whole message follows the note")
        with self.store.db() as db:
            stored = db.execute("SELECT content FROM messages WHERE role='user' AND job_id=?", (work,)).fetchone()
        self.assertEqual(stored['content'], RESTART, 'the note is never stored as owner text')

    def test_a_cancel_and_restart_while_the_earlier_work_runs_reaches_the_worker(self):
        earlier = self.running_work(EARLIER, 'tg-earlier')
        self.judged(EARLIER)
        self.script = [{'content': '예약 실행을 취소하고 15:20까지 다시 걸어 둘게요.'}]
        work = self.receive(RESTART)
        self.assert_ran_as_request(work, earlier, 'running')
        self.assertEqual(self.store.job(earlier)['status'], 'running', 'the running Work is not touched')

    def test_a_scheduled_run_is_never_withdrawn_by_the_shortcut(self):
        row = self.scheduled(goal='5분마다 조건 확인', channel=f'telegram:{GENERATION}')
        run = self.store.enqueue('5분마다 조건 확인', f"{prep.REQUEST_KEY_PREFIX}{row['id']}:1",
                                 f'telegram:{GENERATION}', CHAT)
        # Queued behind the owner's message, so the shortcut could withdraw it; it must not.
        with self.store.db() as db:
            db.execute('UPDATE jobs SET created=created+60 WHERE id=?', (run,))
        self.judged('5분마다 조건 확인')
        self.script = [{'content': '예약을 바꿔 둘게요.'}]
        with patch.object(self.service, 'followup_candidates', return_value=[self.store.job(run)]):
            self.receive(RESTART)
        self.assert_ran_as_request(self.owner_work(RESTART), run, 'scheduled')
        self.assertEqual(self.store.job(run)['status'], 'queued')
        [facts] = self.followup_facts()
        self.assertEqual(facts['w1_started_by'], 'a scheduled preparation')

    def test_a_plain_stop_of_a_queued_work_still_withdraws_it(self):
        self.judged(EARLIER)
        stop = self.store.enqueue('그거 취소해', 'tg-stop', f'telegram:{GENERATION}', CHAT, owner_typed=True)
        earlier = self.store.enqueue(EARLIER, 'tg-earlier', f'telegram:{GENERATION}', CHAT)
        judged = {'relation': FOLLOWUP_CANCEL, 'previous': self.store.job(earlier)}
        with patch.object(self.service, 'continuity_relation', return_value=judged):
            self.assertTrue(self.service.run_one())
        self.assertEqual(self.store.job(earlier)['status'], 'cancelled')
        job = self.store.job(stop)
        self.assertEqual((job['status'], job['response']), ('succeeded', msg('quickstart_service.cancelled_request')))
        self.assertEqual(self.model_calls, [], 'a withdrawal Still did itself needs no worker')

    def test_the_judgment_reads_who_started_each_candidate(self):
        self.running_work(EARLIER, 'tg-earlier')
        self.judged('nothing')
        self.receive(RESTART)
        [facts] = self.followup_facts()
        self.assertEqual(facts['w1_started_by'], 'owner')
