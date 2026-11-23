"""#1183: a long Work's wait signal carries the decision model's own one-line acknowledgement.

Evidence class: model-free, fixture transports.  A ``FixtureDecisionEngine``
answers the plan call, the scripted Ollama-shaped transport of
``test_imessage_surface`` is the worker and ``FakeSidecar`` replaces the
iMessage wire; the real QuickStore, ``AgentService`` (``run_one``,
``present_waiting_work``, ``deliver_one``) and ``Orchestration`` run unchanged.
No live model or Messages is contacted, and none is claimed.

C16/#477: the words are the model's.  Still carries the line it was given,
bounded and redacted; with no line it sends nothing on a surface that cannot
edit a sent message, and it never writes a phrase of its own.
"""
import json
import time
import unittest
import uuid
from unittest import mock

from personal_agent.conversation_handoff import ConversationJudgments
from personal_agent.decision import OUTCOME_DECIDED, FixtureDecisionEngine, StructuredDecision, fixture_confidence
from personal_agent.orchestrator import Orchestration, plan_shape
from personal_agent.telegram_presence import DOTS_FRAMES, NO_STEP_LINE, WaitState, draft_frame

from test_orchestrator import OrchestrationUnit, plan
import test_imessage_surface as im
from test_imessage_surface import ANSWER, OWNER, IMessageCase


def decided(data):
    return StructuredDecision(OUTCOME_DECIDED, data, fixture_confidence(0.9))


def sentinel():
    """A line no Still code could have written: any other text on the wire is Still's own."""
    return f'모델이 쓴 한 줄 {uuid.uuid4().hex[:8]}'


class AcknowledgementInThePlan(unittest.TestCase):
    """The plan call that already reads the request writes the line; no extra model call."""

    def orchestration(self, answers, request='이번 주 서울 날씨 알려줘'):
        asked = []
        answers = list(answers)

        def structured(context, question, schema):
            asked.append((question, schema))
            return decided(answers.pop(0)) if answers else None
        orchestration = Orchestration(ConversationJudgments(FixtureDecisionEngine(structured=structured)),
                                      OrchestrationUnit.catalogue(self), request=request,
                                      record=lambda status, detail: None)
        return orchestration, asked

    def test_the_first_plan_is_asked_for_the_line_and_carries_it(self):
        line = '이번 주 서울 날씨를 찾아볼게요'
        orchestration, asked = self.orchestration([{**plan('a', ''), 'acknowledgement': line}])
        attempt = orchestration.first()
        self.assertEqual(len(asked), 1, 'the same plan call: no extra model request')
        question, schema = asked[0]
        self.assertIn('acknowledgement', schema['properties'])
        self.assertEqual(schema['properties']['acknowledgement']['type'], 'string')
        self.assertIn('acknowledgement', question, 'the planner prompt describes the field')
        self.assertTrue(attempt.planned)
        self.assertEqual(attempt.acknowledgement, line)

    def test_a_plan_without_the_field_still_parses_and_runs(self):
        data = plan('a', '')
        self.assertNotIn('acknowledgement', data)
        self.assertTrue(plan_shape(data))
        orchestration, _asked = self.orchestration([data])
        attempt = orchestration.first()
        self.assertTrue(attempt.planned, 'the plan is used, not a fallback')
        self.assertFalse(getattr(attempt, 'fallback', ''))
        self.assertEqual(attempt.acknowledgement, '')

    def test_a_malformed_line_costs_the_line_never_the_plan(self):
        for bad in (None, 7, ['찾아볼게요'], {'text': '찾아볼게요'}):
            with self.subTest(bad=bad):
                orchestration, _asked = self.orchestration([{**plan('a', ''), 'acknowledgement': bad}])
                attempt = orchestration.first()
                self.assertTrue(attempt.planned)
                self.assertEqual(attempt.acknowledgement, '')

    def test_the_line_is_one_bounded_line_and_redacted(self):
        orchestration, _asked = self.orchestration([{**plan('a', ''), 'acknowledgement': '  찾아\n볼게요  ' + 'ㅁ' * 900}])
        line = orchestration.first().acknowledgement
        self.assertNotIn('\n', line)
        self.assertTrue(line.startswith('찾아 볼게요'))
        self.assertLessEqual(len(line), 300)
        orchestration, _asked = self.orchestration([{**plan('a', ''), 'acknowledgement': '찾아볼게요'}])
        orchestration._redact = lambda text: str(text).replace('찾아', '[redacted]')
        self.assertNotIn('찾아', orchestration.first().acknowledgement)

    def test_a_direct_reply_has_no_line_and_a_replan_writes_none(self):
        orchestration, _asked = self.orchestration(
            [{**plan('a', ''), 'reply': '네, 내일은 맑아요.', 'acknowledgement': '찾아볼게요'}])
        attempt = orchestration.first()
        self.assertEqual(attempt.acknowledgement, '', 'the reply is the answer; there is nothing to wait for')
        orchestration, _asked = self.orchestration([plan('a', '')])
        orchestration.attempts.append(orchestration.first())
        again, _why = orchestration.validate({**plan('a', '다른 방법'), 'acknowledgement': '다시 해볼게요'},
                                             orchestration.catalogue.available(), 2)
        self.assertEqual(again.acknowledgement, '', 'at most one line per Work, from its first plan')


class AcknowledgedWait(IMessageCase):
    """A paired iMessage Work end to end: the plan's line is the one wait message, or nothing is sent."""

    def setUp(self):
        self.line = sentinel()
        self.plans = []
        self.ticks = []          # seconds after the Work was created, presented while the worker runs
        original = im.model

        def worker(url, body, headers=None, timeout=60):
            tools = [t.get('function', {}).get('name') or t.get('name') for t in body.get('tools', [])]
            if 'agentos_connection_probe' not in tools and self.ticks:
                created = self.work()['created']
                for after in self.ticks:
                    self.service.present_waiting_work(now=created + after)
                self.ticks = []
            return original(url, body, headers, timeout)
        # IMessageCase builds its ModelAdapter from the module's ``model``: the worker transport.
        patcher = mock.patch.object(im, 'model', worker)
        patcher.start()
        self.addCleanup(patcher.stop)
        super().setUp()

        def structured(context, question, schema):
            self.plans.append(schema)
            data = {'reply': '', 'worker': schema['properties']['worker']['enum'][0], 'model': '',
                    'brief': {'notes': ''}, 'reason': 'fits'}
            if self.line is not None:
                data['acknowledgement'] = self.line
            return decided(data)
        self.service.use_decision_engine(FixtureDecisionEngine(structured=structured))
        self.pair()
        self.timing = self.service.presence_timing

    def work(self):
        return self.im_jobs()[0]

    def waits(self):
        return [op['text'] for op in self.sidecar.sends()
                if op['text'] != ANSWER and op['text'] != im.msg('imessage.connected_hello')]

    def run_work(self, ticks):
        self.owner_says('이번 주 서울 날씨 알려줘')
        self.ticks = list(ticks)
        self.assertTrue(self.service.run_one())
        self.assertEqual(self.ticks, [], 'the worker ran while the Work was active')
        self.service.deliver_one()

    def long(self):
        return (self.timing.draft_after + 1, self.timing.draft_after + 20, self.timing.draft_after + 300)

    def test_a_long_work_sends_exactly_one_wait_message_carrying_the_models_line(self):
        self.run_work(self.long())
        self.assertEqual(len(self.plans), 1, 'the line came with the plan, no extra call')
        self.assertEqual(self.waits(), [draft_frame(self.line, 0)])
        texts = [op['text'] for op in self.sidecar.sends()]
        self.assertLess(texts.index(self.waits()[0]), texts.index(ANSWER), 'before the answer')
        self.service.present_waiting_work(now=self.work()['created'] + self.timing.draft_after + 600)
        self.assertEqual(len(self.waits()), 1, 'never after the answer')

    def test_the_wait_text_is_the_models_line_and_no_word_of_stills_own(self):
        self.run_work(self.long())
        [wait] = self.waits()
        dots = {frame.strip() for frame in DOTS_FRAMES}
        rest = wait.replace(self.line, '', 1).strip()
        self.assertTrue(rest == '' or rest in dots, f'Still added its own words: {rest!r}')

    def test_without_a_line_no_wait_message_is_sent_and_never_lone_dots(self):
        self.line = None
        self.run_work(self.long())
        self.assertEqual(self.waits(), [], 'no line: nothing, rather than a stuck "…" (or a phrase of Still\'s own)')
        self.assertNotIn(draft_frame(NO_STEP_LINE, 0), [op['text'] for op in self.sidecar.sends()])
        self.assertEqual(len(self.answer_sends()), 1, 'the Work runs and answers normally')

    def test_an_empty_line_sends_nothing(self):
        self.line = '   '
        self.run_work(self.long())
        self.assertEqual(self.waits(), [])

    def test_a_short_work_sends_nothing(self):
        self.run_work((self.timing.draft_after - 0.5,))
        self.assertEqual(self.waits(), [])

    def test_an_approval_wait_sends_nothing(self):
        self.owner_says('이번 주 서울 날씨 알려줘')
        work = self.work()
        cfg = self.surface.config()
        self.store.queue_notification(work['id'], cfg['owner_user_id'], cfg['generation'],
                                      'context_approval_needed', 'approval-1', surface='imessage')
        self.ticks = list(self.long())
        self.assertTrue(self.service.run_one())
        self.assertEqual(self.waits(), [], 'the approval prompt is the only surface')

    def test_the_line_is_never_the_answer_and_its_echo_never_settles_delivery(self):
        self.owner_says('이번 주 서울 날씨 알려줘')
        self.ticks = [self.timing.draft_after + 1]
        self.assertTrue(self.service.run_one())
        work = self.store.job(self.work()['id'])
        self.assertEqual(work['response'], ANSWER)
        self.assertNotIn(self.line, json.dumps({k: work[k] for k in work.keys()}, ensure_ascii=False, default=str),
                         'the line is not stored as the Work\'s reply or outcome')
        [wait] = self.waits()
        self.sidecar.from_me(OWNER, wait, sent=True, delivered=True)
        self.surface.receive()
        self.service.deliver_one()
        self.assertEqual(self.store.job(work['id'])['delivery'], 'sending', 'the wait echo settles nothing')
        self.assertNotEqual(self.store.last_reply(work['id'])['content'], wait)


class StepLineOnEverySurface(IMessageCase):
    """Telegram drafts and the Slack status read the same step line (``_draft_step_text``)."""

    def setUp(self):
        super().setUp()
        self.pair()
        self.owner_says('질문')
        self.job = self.im_jobs()[0]

    def test_the_models_line_replaces_the_bare_dots_and_is_marked_as_model_text(self):
        line = sentinel()
        self.service.live_acknowledgements[self.job['id']] = line
        text = self.service._draft_step_text(self.job, WaitState())
        self.assertEqual(str(text), line)
        from personal_agent.telegram_locale import Text
        self.assertIsInstance(text, Text, 'AI prose, never matched against Still\'s catalog (#477)')
        self.assertEqual(draft_frame(text, 0), draft_frame(line, 0))

    def test_without_a_line_the_step_stays_empty(self):
        self.assertEqual(self.service._draft_step_text(self.job, WaitState()), NO_STEP_LINE)

    def test_developer_mode_keeps_naming_the_worker(self):
        self.service.live_acknowledgements[self.job['id']] = sentinel()
        self.service.developer_mode = lambda: True
        self.service.live_workers[self.job['id']] = 'Codex'
        self.assertEqual(self.service._draft_step_text(self.job, WaitState()), 'Codex')

    def test_a_redelegated_attempt_drops_the_first_plans_line(self):
        self.service.live_acknowledgements[self.job['id']] = sentinel()
        self.service.record_turn_worker(self.job['id'], {'attempt': 1, 'route': 'subscription', 'engine': 'codex'})
        self.assertNotEqual(self.service._draft_step_text(self.job, WaitState()), NO_STEP_LINE)
        self.service.record_turn_worker(self.job['id'], {'attempt': 2, 'route': 'subscription', 'engine': 'codex'})
        self.assertEqual(self.service._draft_step_text(self.job, WaitState()), NO_STEP_LINE,
                         'the line described the first plan, not the re-delegated one')

    def test_an_approval_wait_shows_no_line(self):
        self.service.live_acknowledgements[self.job['id']] = sentinel()
        cfg = self.surface.config()
        self.store.queue_notification(self.job['id'], cfg['owner_user_id'], cfg['generation'],
                                      'context_approval_needed', 'approval-1', surface='imessage')
        self.assertIsNone(self.service._draft_step_text(self.job, WaitState()))


class NoFixedPhrase(unittest.TestCase):
    """C16: the wait path takes no wording from Still's message catalog or a literal of its own."""

    def test_the_wait_path_reads_no_catalog_text(self):
        import inspect
        import re
        from personal_agent.imessage_surface import IMessageSurface
        from personal_agent.quickstart_service import AgentService
        from personal_agent.slack_surface import SlackSurface
        for function in (AgentService._draft_step_text, IMessageSurface.show_wait_status,
                         SlackSurface.show_wait_status):
            with self.subTest(function=function.__qualname__):
                source = inspect.getsource(function)
                code = re.sub(r'(?s)""".*?"""', '', source)
                code = '\n'.join(line.split('#', 1)[0] for line in code.splitlines())
                self.assertNotRegex(code, r'\bmsg\(', 'a catalog phrase would be Still\'s own words')
                self.assertNotRegex(code, r'[\uac00-\ud7a3]', 'a Korean literal would be Still\'s own words')


if __name__ == '__main__':
    unittest.main()
