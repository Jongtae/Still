"""#1517: the owner's workshop log on 2026-10-14 failed three ways.

* A correct reading of the owner's lunch-menu photo was judged unsupported twice: the
  outcome judgment never learned the message carried a photo the worker was given.
* Codex gave up on ``last status: 429 Too Many Requests`` with no structured status, so the
  usage-limit hint never applied and the owner read "out of time or turns" instead.
"""
import json
import unittest

from personal_agent.bounded_execution import failure_details
from personal_agent.conversation_handoff import CAPABILITY_NEED_QUESTION, GOAL_REACHED_PROPOSITION, ConversationJudgments
from personal_agent.decision import BinaryDecision, FixtureDecisionEngine, OUTCOME_DECIDED, fixture_confidence
from personal_agent.orchestrator import Orchestration
import test_orchestrator as fixtures
from test_orchestrator import decided, plan


class RetryStatus(unittest.TestCase):
    def test_codex_retry_message_carries_its_status(self):
        stream = json.dumps({'type': 'turn.failed', 'error': {
            'message': 'exceeded retry limit, last status: 429 Too Many Requests'}})
        status, reason = failure_details('codex', stream, '')
        self.assertEqual(status, 429)
        self.assertIn('429', reason)

    def test_a_message_without_a_status_stays_without_one(self):
        stream = json.dumps({'type': 'turn.failed', 'error': {'message': 'stream disconnected'}})
        self.assertIsNone(failure_details('codex', stream, '')[0])


class AttachmentsReachTheJudgment(unittest.TestCase):
    def setUp(self):
        self.judged = []

        def judge(context, proposition):
            self.judged.append(context.facts)
            return BinaryDecision(OUTCOME_DECIDED, True, fixture_confidence())

        def structured(context, question, schema):
            return decided(plan('a', ''))

        self.engine = FixtureDecisionEngine(structured=structured, judge=judge)

    def orchestration(self, **kwargs):
        return Orchestration(ConversationJudgments(self.engine), fixtures.OrchestrationUnit.catalogue(None),
                             request='오늘 리더 워크샵 점심', **kwargs)

    def test_the_photo_the_worker_was_given_is_a_fact_of_the_judgment(self):
        orchestration = self.orchestration(attachments='1 photo')
        orchestration.first()
        self.assertEqual(orchestration.evaluate_answer('Western Set 메뉴예요.', 'none'), 'reached')
        self.assertEqual(self.judged[-1]['attachments'], '1 photo')
        self.assertIn('attachments', GOAL_REACHED_PROPOSITION)

    def test_a_message_without_attachments_says_none(self):
        orchestration = self.orchestration()
        orchestration.first()
        orchestration.evaluate_answer('답', 'none')
        self.assertEqual(self.judged[-1]['attachments'], 'none')


class TellingIsNotAsking(unittest.TestCase):
    def test_the_capability_question_names_a_statement_of_the_owners_day(self):
        self.assertIn('telling or logging their day', CAPABILITY_NEED_QUESTION)


if __name__ == '__main__':
    unittest.main()
