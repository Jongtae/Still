"""#1124: a decided outcome judgment is not discarded for its self-reported confidence.

The outcome judgment offers an explicit undecided answer (``answer: null``).  Its verbalized
confidence is the model's own number, not calibrated across judgment routes, so one fixed
threshold shared by every route turned decided answers from one route into "could not
judge".  A judgment that offers the undecided answer is taken at its answer; a judgment
whose answer space has no undecided option keeps the confidence threshold.
"""
import unittest

from personal_agent.conversation_handoff import ConversationJudgments
from personal_agent.decision import (BinaryDecision, DecisionContext, DecisionPolicy, OUTCOME_DECIDED,
                                     OUTCOME_MALFORMED, fixture_confidence)
from test_judgment_gap import ScriptedSchemaEngine

GAP = 'The reply leaves the second city unanswered.'


def explained(answer, probability, gap='', note=''):
    return ScriptedSchemaEngine({'answer': answer, 'gap': gap, 'note': note, 'confidence': probability},
                                probability=probability)


def goal(engine):
    return ConversationJudgments(engine).goal_reached('Weather for two cities, please.', 'two weather reads',
                                                      answer='Both forecasts are below.')


class DecidedOutcomeJudgment(unittest.TestCase):
    def test_a_decided_yes_below_the_fixed_threshold_is_kept(self):
        judged = goal(explained(True, 0.70))
        self.assertEqual(judged.outcome, 'yes')

    def test_a_decided_no_below_the_fixed_threshold_keeps_its_gap(self):
        judged = goal(explained(False, 0.65, gap=GAP))
        self.assertEqual((judged.outcome, judged.value), ('no', GAP))

    def test_the_explained_judgment_offers_the_undecided_answer(self):
        engine = explained(True, 0.9)
        decision = engine.judge_explained(DecisionContext('goal-reached', {'reply': 'r'}), 'The reply serves it.')
        self.assertTrue(decision.abstains)
        _kind, _question, schema = engine.asked[0]
        self.assertIn('null', schema['properties']['answer']['type'])


class UndecidedStaysUnavailable(unittest.TestCase):
    def test_an_explicit_undecided_answer_is_unavailable(self):
        self.assertEqual(goal(explained(None, 0.95)).outcome, 'unavailable')

    def test_an_answer_its_own_number_does_not_favour_is_unavailable(self):
        """Confidence at or below one half says the other answer is as likely: not a decided verdict."""
        for probability in (0.5, 0.4, 0.0):
            with self.subTest(probability=probability):
                self.assertEqual(goal(explained(True, probability)).outcome, 'unavailable')

    def test_a_malformed_answer_is_unavailable(self):
        engine = ScriptedSchemaEngine({'answer': 'maybe', 'gap': '', 'confidence': 0.9})
        decision = engine.judge_explained(DecisionContext('goal-reached', {}), 'q')
        self.assertEqual(decision.outcome, OUTCOME_MALFORMED)
        self.assertEqual(goal(engine).outcome, 'unavailable')


class JudgmentsWithoutAnUndecidedAnswer(unittest.TestCase):
    def test_a_bare_yes_or_no_keeps_the_confidence_threshold(self):
        engine = ScriptedSchemaEngine({'answer': True, 'confidence': 0.70}, probability=0.70)
        decision = engine.judge(DecisionContext('p', {}), 'q')
        self.assertFalse(decision.abstains)
        self.assertEqual(DecisionPolicy().binary(decision), 'unknown')
        self.assertFalse(DecisionPolicy().confident_binary(decision))
        sure = ScriptedSchemaEngine({'answer': True, 'confidence': 0.80}, probability=0.80)
        self.assertEqual(DecisionPolicy().binary(sure.judge(DecisionContext('p', {}), 'q')), 'yes')

    def test_an_engine_without_the_explained_judgment_keeps_the_threshold(self):
        """A route whose judgment has no undecided answer (a fixture, the Jev API) is gated as before."""
        decision = BinaryDecision(OUTCOME_DECIDED, True, fixture_confidence(0.70))
        self.assertFalse(decision.abstains)
        self.assertEqual(DecisionPolicy().binary(decision), 'unknown')


if __name__ == '__main__':
    unittest.main()
