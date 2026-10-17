"""#1451: the outcome judgment names what is missing, not only yes or no.

Live 2026-10-12: a correct balance answer drawn from a long code_run output was judged
not reached; the next attempt was briefed only "not reached" and the Work closed with
"일부 단계는 끝내지 못했어요" although no step failed.  The judgment now returns a gap that
briefs the next attempt and closes a Work that stayed short, and it reads the evidence
the answer rests on instead of about 2200 characters of it.
"""
import json
import unittest

from personal_agent import agent_runtime
from personal_agent.conversation_handoff import ConversationJudgments
from personal_agent.conversation_projection import report_statement, TERMINAL_UNCONFIRMED_NOTE, answer_note, terminal_text
from personal_agent.decision import (BinaryDecision, DecisionConfidence, DecisionContext, FixtureDecisionEngine,
                                     OUTCOME_DECIDED, SchemaDecisionEngine, fixture_confidence)
from personal_agent.orchestrator import OBSERVATION_CHARS, Orchestration, observations_with_pages
import test_orchestrator as fixtures
from test_orchestrator import decided, plan

GAP = '잔고 숫자를 뒷받침하는 조회 결과가 보이지 않습니다.'


class ScriptedSchemaEngine(SchemaDecisionEngine):
    """A schema engine whose transport returns one scripted object (no provider)."""

    def __init__(self, data, probability=0.9):
        records = []
        super().__init__(audit=records.append)
        self.records, self.data, self.probability, self.asked = records, data, probability, []

    def _ask(self, context, kind, question, schema, valid):
        self.asked.append((kind, question, schema))
        confidence = DecisionConfidence(self.probability, provider='scripted', model='m')
        outcome = self._checked(self.data, confidence, valid)
        return self._done(context, kind, outcome, self.data, confidence, self.now(), sent=True)


def no_with_gap(*_):
    return BinaryDecision(OUTCOME_DECIDED, False, fixture_confidence(), gap=GAP)


class EngineContract(unittest.TestCase):
    def test_the_explained_judgment_is_one_judge_call_whose_record_keeps_no_text(self):
        engine = ScriptedSchemaEngine({'answer': False, 'gap': f'  {GAP} ', 'confidence': 0.9})
        decision = engine.judge_explained(DecisionContext('goal-reached', {'reply': 'r'}), 'The reply serves it.')
        self.assertEqual((decision.answer, decision.gap), (False, GAP))
        kind, question, schema = engine.asked[0]
        self.assertEqual(kind, 'judge')
        self.assertIn('gap', schema['required'])
        self.assertIn('when the proposition is false', question)
        self.assertEqual(len(engine.records), 1)
        self.assertEqual(engine.records[0]['answer'], False)
        self.assertNotIn(GAP, json.dumps(engine.records[0], ensure_ascii=False), 'the audit keeps no owner text')

    def test_the_owner_reads_a_note_and_the_gap_stays_internal(self):
        """#1459: the same one call returns the owner's line; it may be empty when the reply already said it."""
        note = '예약이 실제로 걸려 있는지는 한투 앱에서 한 번 확인해 주세요.'
        engine = ScriptedSchemaEngine({'answer': False, 'gap': GAP, 'note': f' {note} ', 'confidence': 0.9})
        decision = engine.judge_explained(DecisionContext('goal-reached', {'reply': 'r'}), 'The reply serves it.')
        self.assertEqual((decision.gap, decision.note), (GAP, note))
        kind, question, schema = engine.asked[0]
        self.assertIn('note', schema['required'])
        self.assertIn('no internal terms', question)
        self.assertNotIn(note, json.dumps(engine.records[0], ensure_ascii=False))
        judged = ConversationJudgments(engine).goal_reached('request', 'observed', answer='reply')
        self.assertEqual((judged.value, judged.note), (GAP, note))
        empty = ScriptedSchemaEngine({'answer': False, 'gap': GAP, 'note': '', 'confidence': 0.9})
        self.assertEqual(empty.judge_explained(DecisionContext('p', {}), 'q').note, '')
        omitted = ScriptedSchemaEngine({'answer': False, 'gap': GAP, 'confidence': 0.9})
        self.assertIsNone(omitted.judge_explained(DecisionContext('p', {}), 'q').note, 'an engine without the note keeps the gap path')
        yes = ScriptedSchemaEngine({'answer': True, 'gap': '', 'note': 'stray', 'confidence': 0.9})
        self.assertIsNone(yes.judge_explained(DecisionContext('p', {}), 'q').note)

    def test_a_true_verdict_carries_no_gap_and_a_missing_gap_is_malformed(self):
        yes = ScriptedSchemaEngine({'answer': True, 'gap': 'stray', 'confidence': 0.9})
        self.assertEqual(yes.judge_explained(DecisionContext('p', {}), 'q').gap, '')
        bare = ScriptedSchemaEngine({'answer': False, 'confidence': 0.9})
        self.assertNotEqual(bare.judge_explained(DecisionContext('p', {}), 'q').outcome, OUTCOME_DECIDED)

    def test_an_engine_without_the_explained_judgment_answers_the_bare_verdict(self):
        engine = FixtureDecisionEngine(judge=lambda *_: BinaryDecision(OUTCOME_DECIDED, False, fixture_confidence()))
        judged = ConversationJudgments(engine).goal_reached('request', 'observed', answer='reply')
        self.assertEqual((judged.outcome, judged.value), ('no', None))
        self.assertEqual([call[0] for call in engine.asked], ['judge'], 'still one judgment')


class GoalReached(unittest.TestCase):
    def test_no_carries_the_gap_and_yes_carries_none(self):
        judged = ConversationJudgments(FixtureDecisionEngine(judge=no_with_gap)).goal_reached('r', 'o', answer='a')
        self.assertEqual((judged.outcome, judged.value), ('no', GAP))
        yes = FixtureDecisionEngine(judge=lambda *_: BinaryDecision(OUTCOME_DECIDED, True, fixture_confidence()))
        self.assertEqual(ConversationJudgments(yes).goal_reached('r', 'o', answer='a').value, None)

    def test_the_observations_are_not_cut_by_the_general_context_bound(self):
        seen = []

        def judge(context, _):
            seen.append(context)
            return BinaryDecision(OUTCOME_DECIDED, True, fixture_confidence())
        observations = 'x' * (OBSERVATION_CHARS - 10)
        ConversationJudgments(FixtureDecisionEngine(judge=judge)).goal_reached('r', observations, answer='a')
        self.assertFalse(seen[0].too_large())
        self.assertEqual(seen[0].facts['observations'], observations)

    def test_a_long_run_output_reaches_the_judgment_whole_after_many_steps(self):
        """The live shape: ~2900 characters of step lines and a long API response."""
        lines = [f'- code_run: step {n} ' + 'x' * 300 for n in range(9)]
        output = '{"output1": [' + ', '.join('{"evlu_amt": "0"}' for _ in range(300)) + '], "tot_asst_amt": "100001"}'
        text = observations_with_pages(lines, [('code_run:abc', 'code_run output', output)])
        self.assertIn('"tot_asst_amt": "100001"', text, 'the figure at the end of the output is seen')


class Orchestrated(unittest.TestCase):
    def make(self, judge, plans, events):
        def structured(context, question, schema):
            plans.append(context)
            return decided(plan('a', ''))
        engine = FixtureDecisionEngine(structured=structured, judge=judge)
        return Orchestration(ConversationJudgments(engine), fixtures.OrchestrationUnit.catalogue(None),
                             request='한투 잔고 조회해줘', record=lambda status, detail: events.append((status, detail)))

    def test_the_gap_briefs_the_next_plan_and_is_recorded(self):
        plans, events = [], []
        orchestration = self.make(no_with_gap, plans, events)
        attempt = orchestration.first()
        verdict = orchestration.evaluate_answer('총자산 100,001원이에요.', 'code_run: exit 0')
        self.assertEqual(verdict, 'not_reached')
        orchestration.next(attempt, verdict, answer='총자산 100,001원이에요.')
        self.assertEqual(len(plans), 2, 'a re-plan was asked')
        self.assertIn(GAP, plans[-1].facts['previous_attempts'])
        evaluated = [detail for status, detail in events if status == 'evaluated'][-1]
        self.assertEqual(evaluated['gap'], GAP)
        self.assertIn(GAP, evaluated['text'])

    def test_the_last_short_attempt_keeps_its_gap_for_the_closing_line(self):
        plans, events = [], []
        orchestration = self.make(no_with_gap, plans, events)
        attempt = orchestration.first()
        verdict = orchestration.evaluate_answer('answer', 'observed', final=True)
        orchestration.next(attempt, verdict, answer='answer', effect=True)
        self.assertEqual((orchestration.terminal, orchestration.terminal_gap), ('not_reached', GAP))

    def test_a_reached_or_unjudged_attempt_has_no_gap(self):
        for judge in (lambda *_: BinaryDecision(OUTCOME_DECIDED, True, fixture_confidence()), lambda *_: None):
            with self.subTest(judge=judge):
                plans, events = [], []
                orchestration = self.make(judge, plans, events)
                attempt = orchestration.first()
                orchestration.next(attempt, orchestration.evaluate_answer('a', 'o'), answer='a')
                evaluated = [detail for status, detail in events if status == 'evaluated'][-1]
                self.assertIsNone(evaluated['gap'])


class Lens(unittest.TestCase):
    """#1459: the plan names the professional lens; the worker reads it with its brief, and it grants nothing."""

    def orchestration(self, data):
        engine = FixtureDecisionEngine(structured=lambda *_: decided(data),
                                       judge=lambda *_: BinaryDecision(OUTCOME_DECIDED, True, fixture_confidence()))
        return Orchestration(ConversationJudgments(engine), fixtures.OrchestrationUnit.catalogue(None),
                             request='오늘 아침 주식 매수 매도 계획은?', record=lambda *_: None)

    def test_the_lens_reaches_the_brief_and_an_empty_one_adds_nothing(self):
        lens = '투자자문 담당자: 계획, 가격, 위험, 실행 가능 여부와 다음 할 일을 짧게 숫자와 함께'
        attempt = self.orchestration({**plan('a', ''), 'brief': {'notes': '', 'lens': f'  {lens} '}}).first()
        self.assertEqual(attempt.lens, lens)
        self.assertIn(lens, attempt.brief())
        self.assertIn('never a permission', attempt.brief())
        bare = self.orchestration(plan('a', '')).first()
        self.assertEqual(bare.lens, '')
        self.assertIsNone(bare.brief(), 'no lens and no notes: no brief section')
        self.assertNotEqual(attempt.digest, bare.digest, 'the lens is part of what the attempt was briefed')


class DirectRoute(unittest.TestCase):
    def test_goal_judgment_returns_the_gap_with_the_verdict(self):
        judgments = ConversationJudgments(FixtureDecisionEngine(judge=no_with_gap))
        verdict = agent_runtime.goal_judgment(judgments, 'request', {'evidence_refs': [], 'summary': 'a'}, {}, [])
        self.assertEqual((verdict, verdict.gap), ('no', GAP))
        yes = ConversationJudgments(FixtureDecisionEngine(
            judge=lambda *_: BinaryDecision(OUTCOME_DECIDED, True, fixture_confidence())))
        verdict = agent_runtime.goal_judgment(yes, 'request', {'evidence_refs': [], 'summary': 'a'}, {}, [])
        self.assertEqual((verdict, verdict.gap), ('yes', ''))


class ClosingLine(unittest.TestCase):
    def test_a_short_work_closes_with_its_gap_not_a_failed_step_count(self):
        report = agent_runtime.agency_report('r', [], [], [GAP], None)
        report['gap'] = GAP
        note = answer_note([], 'partial', report=report)
        self.assertEqual(note, GAP)
        self.assertNotIn('단계', note)
        self.assertEqual(terminal_text('총자산 100,001원이에요.', None, 'partial', note=note),
                         '총자산 100,001원이에요.\n\n' + GAP)

    def test_the_owner_reads_the_note_unlabelled_and_never_the_internal_gap(self):
        """#1459: the closing line and the report detail carry the note; an empty note adds nothing."""
        note = '예약이 실제로 걸려 있는지는 한투 앱에서 한 번 확인해 주세요.'
        report = agent_runtime.agency_report('r', [], [], [], None)
        report['note'] = note
        self.assertEqual(answer_note([], 'partial', report=report), note)
        self.assertEqual(report_statement(report), note)
        empty = agent_runtime.agency_report('r', [], [], [], None)
        empty['note'] = ''
        self.assertIsNone(answer_note([], 'partial', report=empty))
        self.assertIsNone(report_statement(empty))
        both = agent_runtime.agency_report('r', [], [], [], None)
        both.update(note=note, gap=GAP)
        self.assertNotIn(GAP, answer_note([], 'partial', report=both))

    def test_a_work_waiting_for_approval_says_nothing_is_unconfirmed(self):
        """#1470 (live): an order waiting for the owner's tap ended with "확인하지 못했어요"."""
        self.assertEqual(terminal_text('승인을 요청했어요.', None, 'partial', waiting=True), '승인을 요청했어요.')
        self.assertEqual(terminal_text('승인을 요청했어요.', None, 'partial'), '승인을 요청했어요.\n\n' + TERMINAL_UNCONFIRMED_NOTE)

    def test_with_no_gap_and_no_failed_step_nothing_claims_a_step_failed(self):
        self.assertIsNone(answer_note([], 'partial', report=agent_runtime.agency_report('r', [], [], ['x'], None)))
        text = terminal_text('answer', None, 'partial')
        self.assertEqual(text, 'answer\n\n' + TERMINAL_UNCONFIRMED_NOTE)
        self.assertNotIn('단계는 끝내지 못했어요', text)


if __name__ == '__main__':
    unittest.main()
