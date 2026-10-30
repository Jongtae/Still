"""#407: the owner reads what was understood and the plan before a long Work runs.

Evidence class: model-free, fixture transports.  A FixtureDecisionEngine
answers the plan call, a scripted compatible-API model is the worker and a
recording Telegram transport replaces the wire; the real QuickStore,
``AgentService`` (Telegram ingress, ``run_one``) and ``Orchestration`` run
unchanged.  No live model or Telegram is contacted, and none is claimed.
"""
import json
import unittest

from personal_agent.conversation_handoff import ConversationJudgments
from personal_agent.decision import OUTCOME_DECIDED, FixtureDecisionEngine, StructuredDecision, fixture_confidence
from personal_agent.orchestrator import (MAX_PREVIEW_STEPS, PREVIEW_SHOWN, Orchestration, plan_preview, preview_text)
from personal_agent.quickstart_store import PREVIEW_PROJECTION

from test_orchestrator import OrchestrationUnit, decided, plan
from test_preparations import SECRET, _Case

PREVIEW = {'understanding': '이번 주말 부산 1박 숙소를 찾으시는 걸로 이해했어요.',
           'approach': '해운대 근처에서 가격과 후기를 비교해 볼게요.',
           'steps': ['후보 숙소 찾기', '가격과 후기 비교', '세 곳 추천']}
EMPTY = {'understanding': '', 'approach': '', 'steps': []}


class PlanPreviewUnit(unittest.TestCase):
    """The decision model writes the preview with the first plan; Still bounds and carries it."""

    def orchestration(self, answers, can_preview):
        asked, events = [], []
        answers = list(answers)

        def structured(context, question, schema):
            asked.append((question, schema))
            return decided(answers.pop(0)) if answers else None
        orchestration = Orchestration(ConversationJudgments(FixtureDecisionEngine(structured=structured)),
                                      OrchestrationUnit.catalogue(self), request='부산 숙소 찾아줘',
                                      record=lambda status, detail: events.append((status, detail)),
                                      can_preview=can_preview)
        return orchestration, asked, events

    def test_offered_only_on_the_first_plan_of_a_work_that_can_show_it(self):
        orchestration, asked, events = self.orchestration([{**plan('a', ''), 'preview': PREVIEW}], True)
        attempt = orchestration.first()
        question, schema = asked[0]
        self.assertIn('preview', question)
        self.assertEqual(schema['required'][-1], 'preview')
        self.assertEqual(attempt.preview, PREVIEW)
        self.assertEqual(events[0][1]['preview_steps'], 3)
        self.assertNotIn(PREVIEW['understanding'], json.dumps(events, ensure_ascii=False),
                         'Evidence keeps the step count; the words are the transcript\'s')
        self.assertFalse(orchestration.can_preview, 'a re-plan never writes one')
        self.assertNotIn('preview', orchestration._schema(orchestration.catalogue.available())['properties'])

        orchestration, asked, events = self.orchestration([{**plan('a', ''), 'preview': PREVIEW}], False)
        attempt = orchestration.first()
        question, schema = asked[0]
        self.assertNotIn('preview', schema['properties'])
        self.assertNotIn('planner_preview', question)
        self.assertIsNone(attempt.preview)
        self.assertNotIn('preview_steps', events[0][1])

    def test_a_short_reply_has_no_preview_and_a_malformed_one_is_refused(self):
        orchestration, _asked, _events = self.orchestration([{**plan('a', ''), 'preview': EMPTY}], True)
        self.assertIsNone(orchestration.first().preview)
        orchestration, _asked, _events = self.orchestration(
            [{**plan('a', ''), 'preview': {'understanding': '이해했어요.', 'approach': '', 'steps': []}}], True)
        self.assertIsNone(orchestration.first().preview, 'an understanding without steps shows nothing')
        orchestration, _asked, _events = self.orchestration(
            [{**plan('a', ''), 'preview': {'understanding': 'x', 'approach': '', 'steps': '한 단계'}}], True)
        self.assertEqual(orchestration.first().fallback, 'plan_invalid')

    def test_the_preview_is_bounded_and_reads_as_understanding_approach_then_numbered_steps(self):
        bounded = plan_preview({'understanding': ' 길게\n' + 'u' * 400, 'approach': '',
                                'steps': ['  하나  ', '', *(f'단계 {n}' for n in range(9))]})
        self.assertEqual(len(bounded['understanding']), 300)
        self.assertEqual(len(bounded['steps']), MAX_PREVIEW_STEPS)
        self.assertEqual(bounded['steps'][0], '하나', 'blank steps are dropped and whitespace folded')
        self.assertEqual(preview_text(PREVIEW),
                         '이번 주말 부산 1박 숙소를 찾으시는 걸로 이해했어요.\n해운대 근처에서 가격과 후기를 비교해 볼게요.\n\n'
                         '1. 후보 숙소 찾기\n2. 가격과 후기 비교\n3. 세 곳 추천')

    def test_only_a_shown_preview_reaches_the_brief_and_every_later_attempt_keeps_it(self):
        orchestration, _asked, events = self.orchestration(
            [{**plan('a', ''), 'preview': PREVIEW}], True)
        attempt = orchestration.first()
        self.assertIsNone(attempt.brief(), 'not shown yet: the worker is not told about it')
        orchestration.preview_shown(attempt, preview_text(PREVIEW))
        self.assertIn('1. 후보 숙소 찾기', attempt.brief())
        self.assertIn('not an instruction', attempt.brief())
        self.assertEqual([status for status, _detail in events][-1], PREVIEW_SHOWN)
        orchestration.attempts.append(attempt)
        redelegated, _invalid = orchestration.validate(plan('a', '다른 방법'), orchestration.catalogue.available(), 2)
        self.assertIsNone(redelegated.preview)
        self.assertIn('1. 후보 숙소 찾기', redelegated.brief(adjusted=True))


class PlanPreviewService(_Case):
    """A paired Telegram Work: the preview goes out once before the worker, then the reply."""
    REQUEST = '이번 주말 부산 숙소 찾아줘'

    def setUp(self):
        super().setUp()
        self.preview = dict(PREVIEW)
        self.schemas = []

        def structured(context, question, schema):
            self.schemas.append(schema)
            offered = 'preview' in schema['properties']
            data = {'worker': schema['properties']['worker']['enum'][0], 'model': '', 'brief': {'notes': ''},
                    'reason': 'fits', 'account_change': False, 'owner_situation': False,
                    **({'preview': self.preview} if offered else {})}
            return StructuredDecision(OUTCOME_DECIDED, data, fixture_confidence())
        base = self.judge
        self.judge = FixtureDecisionEngine(judge=base._judge, structured=structured)
        self.service.use_decision_engine(self.judge)
        self.sent_before_worker = None

        def first_step(body):
            self.sent_before_worker = list(self.texts())
            return {'content': '해운대 근처 숙소 세 곳을 골랐어요.'}
        self.script = [first_step]

    def preview_rows(self, work):
        with self.store.db() as db:
            return [dict(row) for row in db.execute(
                'SELECT content FROM messages WHERE job_id=? AND delivery_projection=?', (work, PREVIEW_PROJECTION))]

    def test_the_preview_is_sent_once_before_the_worker_and_is_not_the_reply(self):
        work = self.receive(self.REQUEST)
        text = preview_text(PREVIEW)
        self.assertEqual(self.sent_before_worker, [text], 'the owner reads it before the worker starts')
        [sent] = self.sends()
        self.assertEqual(sent['reply_parameters']['message_id'], self.message_id, 'anchored to the owner message')
        self.assertEqual(self.preview_rows(work), [{'content': text}])
        self.assertIn('1. 후보 숙소 찾기', json.dumps(self.model_calls[0], ensure_ascii=False),
                      'the worker is told what the owner was shown')
        reply = self.store.job(work)['response']
        self.assertTrue(reply and reply != text)
        self.assertEqual(self.store.last_reply(work)['content'], reply, 'the reply, never the preview')
        self.assertEqual(self.store.shown_preview(work), text)
        with self.store.db() as db:
            shown = db.execute("SELECT detail FROM tool_events WHERE job_id=? AND tool='orchestrator' AND status=?",
                               (work, PREVIEW_SHOWN)).fetchall()
        self.assertEqual(len(shown), 1)
        self.service.deliver_one()
        self.assertEqual(self.texts(), [text, reply])

    def test_a_preview_row_is_not_an_earlier_answer_of_its_work(self):
        work = self.store.enqueue(self.REQUEST, 'tg-preview', 'telegram:gen-1', 77)
        with self.store.db() as db:
            db.execute('INSERT INTO messages(role,content,channel,created,job_id,delivery_projection) VALUES (?,?,?,?,?,?)',
                       ('assistant', 'preview', 'telegram:gen-1', self.now, work, PREVIEW_PROJECTION))
        self.assertFalse(self.service._answered_before(work))
        self.assertIsNone(self.store.last_reply(work))

    def test_a_short_reply_sends_no_preview(self):
        self.preview = dict(EMPTY)
        work = self.receive('고마워')
        self.assertEqual(self.sent_before_worker, [])
        self.assertEqual(self.preview_rows(work), [])

    def test_a_web_work_is_not_offered_a_preview(self):
        work = self.web_turn(self.REQUEST)
        self.assertNotIn('preview', self.schemas[-1]['properties'])
        self.assertEqual(self.sends(), [])
        self.assertEqual(self.preview_rows(work), [])
        self.assertEqual(self.store.job(work)['status'], 'succeeded')

    def test_a_preview_that_could_not_be_sent_leaves_the_work_running_as_before(self):
        original = self._telegram

        def refusing(url, body=None, headers=None, timeout=60):
            if url.endswith('/sendMessage') and self.sent_before_worker is None:
                return {'ok': False, 'error_code': 400, 'description': 'Bad Request'}
            return original(url, body, headers, timeout)
        self.service.telegram_transport = refusing
        work = self.receive(self.REQUEST)
        self.assertEqual(self.store.job(work)['status'], 'succeeded')
        self.assertEqual(self.preview_rows(work), [])
        self.assertNotIn('1. 후보 숙소 찾기', json.dumps(self.model_calls[0], ensure_ascii=False),
                         'the worker is not told about a preview the owner never saw')

    def test_a_stored_secret_never_reaches_the_preview(self):
        self.preview = {**PREVIEW, 'understanding': f'토큰 {SECRET} 로 확인하시는 걸로 이해했어요.'}
        work = self.receive(self.REQUEST)
        self.assertNotIn(SECRET, json.dumps(self.sends(), ensure_ascii=False))
        self.assertNotIn(SECRET, self.preview_rows(work)[0]['content'])

    def test_the_steer_judgment_reads_the_shown_preview(self):
        work = self.receive(self.REQUEST)
        asked = []

        def judge(context, proposition):
            asked.append(context)
            return None
        self.service.use_decision_engine(FixtureDecisionEngine(judge=judge))
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (work,))
        side = self.store.enqueue('아니 해운대 말고 광안리로', 'tg-side', 'telegram:gen-1', 77)
        self.service.judge_steer(work, side, '아니 해운대 말고 광안리로')
        [context] = [item for item in asked if item.purpose == 'running-work-steer']
        self.assertEqual(context.facts['shown_plan'], preview_text(PREVIEW))


if __name__ == '__main__':
    unittest.main()
