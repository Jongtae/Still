"""#428: a mid-Work correction reaches planning, the next brief and the outcome judgment.

Evidence class: model-free, fixture transports.  A FixtureDecisionEngine
answers the plan and goal judgments and a fake subscription CLI is the worker;
the real QuickStore, ``AgentService`` (``run_one``) and ``Orchestration`` run
unchanged.  The steer is recorded through ``QuickStore.add_steer``, the path
``judge_steer`` takes after a yes.  No live model is contacted.
"""
import unittest

from personal_agent.conversation_handoff import ConversationJudgments
from personal_agent.decision import FixtureDecisionEngine
from personal_agent.orchestrator import MAX_CORRECTION_CHARS, MAX_CORRECTIONS, Orchestration

from test_orchestrator import Harness, OrchestrationUnit, decided, plan

CORRECTION = '아니 해운대 말고 광안리로 찾아줘'


class CorrectionsUnit(unittest.TestCase):
    def orchestration(self, answers):
        asked = []
        answers = list(answers)

        def structured(context, question, schema):
            asked.append(context)
            return decided(answers.pop(0)) if answers else None
        orchestration = Orchestration(ConversationJudgments(FixtureDecisionEngine(structured=structured)),
                                      OrchestrationUnit.catalogue(self), request='부산 숙소 찾아줘')
        return orchestration, asked

    def test_an_uncorrected_plan_asks_exactly_as_before(self):
        orchestration, asked = self.orchestration([plan('a', '')])
        before = orchestration.plan_digest(orchestration.catalogue.available())
        orchestration.owner_corrected([])
        self.assertEqual(orchestration.plan_digest(orchestration.catalogue.available()), before)
        orchestration.first()
        self.assertNotIn('owner_corrections', asked[0].facts)

    def test_corrections_are_bounded_and_carried_by_the_plan_and_later_briefs(self):
        orchestration, asked = self.orchestration([plan('a', ''), plan('a', '다시')])
        first = orchestration.first()
        texts = [f'수정 {n}' for n in range(MAX_CORRECTIONS + 2)] + ['x' * (MAX_CORRECTION_CHARS + 50), '  ']
        orchestration.owner_corrected(texts)
        self.assertEqual(len(orchestration.corrections), MAX_CORRECTIONS)
        self.assertEqual(len(orchestration.corrections[-1]), MAX_CORRECTION_CHARS)
        self.assertNotIn('수정 0', orchestration.corrections, 'the most recent are kept')
        self.assertIsNone(first.brief(), 'the attempt already running is not rewritten')
        orchestration.owner_corrected([CORRECTION])
        orchestration.attempts.append(first)
        second, _invalid = orchestration.validate(plan('a', '다시'), orchestration.catalogue.available(), 2)
        self.assertIn(CORRECTION, second.brief(adjusted=True))
        self.assertIn('this wins', second.brief(adjusted=True))
        second.shown_preview = '1. 해운대 숙소 찾기'
        self.assertIn('corrects the preview', second.brief(adjusted=True))
        continuation = orchestration.continue_for_steers(second, CORRECTION, 1)
        self.assertNotIn(CORRECTION, continuation.brief() or '', 'a steer continuation already carries the words')


class CorrectionReachesPlanning(Harness):
    def steer_while_running(self, text):
        """The owner's message, judged to steer the running Work, recorded while the worker runs."""
        def before(tools):
            if self.engine.before is None:
                return
            self.engine.before = None
            running = self._running
            source = self.store.enqueue(text, 'steer-source')
            self.assertTrue(self.store.add_steer(running, source, text))
        self.engine.before = before

    def run_with_steer(self, text):
        original_enqueue = self.store.enqueue

        def remember(message, key, *args, **kwargs):
            work = original_enqueue(message, key, *args, **kwargs)
            if not hasattr(self, '_running'):
                self._running = work
            return work
        self.store.enqueue = remember
        self.steer_while_running(text)
        return self.run_work('부산 숙소 찾아줘')

    def test_the_re_plan_the_next_brief_and_every_later_judgment_read_the_correction(self):
        self.engine.answers = ['해운대 숙소 세 곳', '광안리 숙소 세 곳']
        self.script([plan('codex', '', model='gpt-5.6-luna'), plan('codex', '', model='gpt-5.6-terra')],
                    goals=[False, True])
        job, row = self.run_with_steer(CORRECTION)
        self.assertEqual(row['response'], '광안리 숙소 세 곳')
        self.assertEqual(len(self.engine.turns), 2, 'the brief delivered it: no extra steer continuation runs')
        self.assertNotIn('owner_corrections', self.asked_plans[0][0].facts, 'the first plan had none')
        self.assertIn(CORRECTION, self.asked_plans[1][0].facts['owner_corrections'])
        self.assertIn(CORRECTION, self.engine.turns[1]['prompt'], 'the re-delegated worker is told')
        self.assertNotIn(CORRECTION, self.engine.turns[0]['prompt'])
        self.assertIn(CORRECTION, self.asked_goals[0].facts['owner_request'],
                      'the attempt that received it is judged against it')
        self.assertIn(CORRECTION, self.asked_goals[1].facts['owner_request'])

    def test_a_stored_secret_in_a_correction_never_reaches_the_plan(self):
        from test_orchestrator import OPENAI_KEY
        self.engine.answers = ['a', 'b']
        self.script([plan('codex', '', model='gpt-5.6-luna'), plan('codex', '', model='gpt-5.6-terra')],
                    goals=[False, True])
        self.run_with_steer(f'키는 {OPENAI_KEY} 로 해 줘')
        self.assertNotIn(OPENAI_KEY, self.asked_plans[1][0].facts['owner_corrections'])


if __name__ == '__main__':
    unittest.main()
