"""#407: the owner reads what was understood and the plan before a long Work runs.

Evidence class: model-free, fixture transports.  A FixtureDecisionEngine
answers the plan call, a scripted compatible-API model is the worker and a
recording Telegram transport replaces the wire; the real QuickStore,
``AgentService`` (Telegram ingress, ``run_one``) and ``Orchestration`` run
unchanged.  No live model or Telegram is contacted, and none is claimed.
"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from personal_agent import mcp_bridge
from personal_agent.agent_runtime import Capabilities, ToolError, evidence_summary, recorded_arguments
from personal_agent.bounded_execution import BOUNDED_PROFILE
from personal_agent.conversation_handoff import ConversationJudgments
from personal_agent.decision import OUTCOME_DECIDED, FixtureDecisionEngine, StructuredDecision, fixture_confidence
from personal_agent.orchestrator import (MAX_PREVIEW_STEPS, PREVIEW_SHOWN, Orchestration, plan_preview, preview_text)
from personal_agent.quickstart_store import PREVIEW_PROJECTION, UPDATE_PROJECTION, QuickStore

from test_orchestrator import OrchestrationUnit, decided, plan
from test_preparations import CHAT, GENERATION, SECRET, _Case, call, finish

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
            [{**plan('a', ''), 'preview': {'understanding': '', 'approach': '찾아볼게요.', 'steps': ['찾기']}}], True)
        self.assertIsNone(orchestration.first().preview, 'without an understanding nothing is shown')
        orchestration, _asked, _events = self.orchestration(
            [{**plan('a', ''), 'preview': {'understanding': '네, 22일 라운딩을 11시 12분으로 바꿀게요.', 'approach': '',
                                          'steps': []}}], True)
        self.assertEqual(preview_text(orchestration.first().preview), '네, 22일 라운딩을 11시 12분으로 바꿀게요.',
                         'one natural message, no list (owner feedback 2026-10-31)')
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
                         '이번 주말 부산 1박 숙소를 찾으시는 걸로 이해했어요. 해운대 근처에서 가격과 후기를 비교해 볼게요.\n\n'
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


class _PreviewCase(_Case):
    """A paired Telegram Work whose first plan writes ``self.preview``."""
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



class PlanPreviewService(_PreviewCase):
    """A paired Telegram Work: the preview goes out once before the worker, then the reply."""

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


class TellOwnerTool(unittest.TestCase):
    """#407: the worker speaks up in the conversation; the tool exists only where the service wired it."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = QuickStore(Path(tmp.name) / 'data')

    def caps(self, **kwargs):
        return Capabilities(self.store, None, {}, '', 'job', lambda *a: None, **kwargs)

    def names(self, caps):
        return [d['function']['name'] for d in caps.definitions()]

    def test_offered_only_when_wired_and_it_sends_the_words_it_was_given(self):
        self.assertNotIn('tell_owner', self.names(self.caps()))
        told = []
        caps = self.caps(owner_update=told.append)
        self.assertIn('tell_owner', self.names(caps))
        result = caps.execute('tell_owner', {'text': '  후보 다섯 곳 찾았어요. 이제 가격을 비교해 볼게요.  '})
        self.assertEqual(told, ['후보 다섯 곳 찾았어요. 이제 가격을 비교해 볼게요.'])
        self.assertEqual(evidence_summary('tell_owner', result), {'sent': True, 'channel': 'telegram'})
        for bad in ('', '   ', 'x' * 501, 3):
            with self.subTest(text=bad), self.assertRaises(ToolError):
                caps.execute('tell_owner', {'text': bad})
        self.assertEqual(len(told), 1)

    def test_a_delegated_specialist_cannot_message_the_owner(self):
        with self.assertRaises(ToolError) as raised:
            self.caps(owner_update=lambda text: None, delegated=True).execute('tell_owner', {'text': '안녕하세요'})
        self.assertEqual(raised.exception.code, 'owner_update_unavailable')

    def test_its_text_is_recorded_by_length_only(self):
        recorded = recorded_arguments('tell_owner', {'text': '후보 다섯 곳 찾았어요.'})
        self.assertNotIn('후보', json.dumps(recorded, ensure_ascii=False))

    def listed(self, job_id):
        requests = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}},
                    {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}]
        out = io.StringIO()
        with mock.patch.object(sys, 'stdin', io.StringIO(''.join(json.dumps(r) + '\n' for r in requests))), \
                contextlib.redirect_stdout(out):
            mcp_bridge.serve(str(self.store.root), job_id, profile=BOUNDED_PROFILE, browser_relay=str(self.store.root / 'relay'))
        replies = {reply['id']: reply for reply in map(json.loads, out.getvalue().splitlines())}
        return [tool['name'] for tool in replies[2]['result']['tools']]

    def test_the_bridge_lists_it_only_after_a_preview_was_shown(self):
        work = self.store.enqueue('부산 숙소 찾아줘', 'tg-1', f'telegram:{GENERATION}', CHAT)
        self.assertNotIn('tell_owner', self.listed(work))
        with self.store.db() as db:
            db.execute('INSERT INTO messages(role,content,channel,created,job_id,delivery_projection) VALUES (?,?,?,?,?,?)',
                       ('assistant', '미리보기', f'telegram:{GENERATION}', 1.0, work, PREVIEW_PROJECTION))
        self.assertIn('tell_owner', self.listed(work))


class TellOwnerService(_PreviewCase):
    """After a preview the worker's own messages reach the chat mid-Work, at most three, then the reply."""

    def updates(self, work):
        with self.store.db() as db:
            return [row['content'] for row in db.execute(
                'SELECT content FROM messages WHERE job_id=? AND delivery_projection=? ORDER BY id', (work, UPDATE_PROJECTION))]

    def offered(self, body):
        return [tool['function']['name'] for tool in body.get('tools') or ()]

    def test_the_worker_speaks_up_mid_work_and_its_answer_still_comes_last(self):
        sent_at_answer = []

        def answer(body):
            sent_at_answer.append(list(self.texts()))
            return finish('f', '1', summary='세 곳을 골랐어요.')
        self.script = [{'content': None, 'tool_calls': [call('1', 'tell_owner', text='후보 다섯 곳 찾았어요. 이제 가격을 비교해 볼게요.')]},
                       answer]
        work = self.receive(self.REQUEST)
        self.assertIn('tell_owner', self.offered(self.model_calls[0]))
        self.assertIn('tell_owner if it is offered', json.dumps(self.model_calls[0], ensure_ascii=False))
        self.assertEqual(sent_at_answer[0], [preview_text(PREVIEW), '후보 다섯 곳 찾았어요. 이제 가격을 비교해 볼게요.'],
                         'the owner read it while the worker was still working')
        self.assertEqual(self.updates(work), ['후보 다섯 곳 찾았어요. 이제 가격을 비교해 볼게요.'])
        reply = self.store.job(work)['response']
        self.assertEqual(self.store.last_reply(work)['content'], reply, 'an interim message is never the reply')
        self.service.deliver_one()
        self.assertEqual(len(self.sends()), 3)

    def test_at_most_three_messages_and_a_secret_never_reaches_the_chat(self):
        self.script = [{'content': None, 'tool_calls': [call(str(n), 'tell_owner', text=f'{n}번째 소식 {SECRET}')]}
                       for n in range(1, 5)] + [finish('f', '1', summary='끝났어요.')]
        work = self.receive(self.REQUEST)
        self.assertEqual(len(self.updates(work)), 3)
        self.assertNotIn(SECRET, json.dumps(self.sends(), ensure_ascii=False))
        with self.store.db() as db:
            refused = db.execute("SELECT detail FROM tool_events WHERE job_id=? AND tool='tell_owner' AND status='failed'",
                                 (work,)).fetchall()
        self.assertEqual(len(refused), 1)
        self.assertIn('owner_update_limit', refused[0]['detail'])

    def test_without_a_preview_the_tool_is_not_offered(self):
        self.preview = dict(EMPTY)
        self.receive('고마워')
        self.assertNotIn('tell_owner', self.offered(self.model_calls[0]))


if __name__ == '__main__':
    unittest.main()
