"""#1301: goal-first routing contract and existing model/worker handoffs.

Injected decisions prove context, brief and outcome propagation, not that a live
model discovers these routes. Case-specific interpretation exists only here.
"""
import unittest

from personal_agent import agent_runtime
from personal_agent.conversation_handoff import FOLLOWUP_QUESTION, ConversationJudgments, GOAL_REACHED_PROPOSITION
from personal_agent.decision import BinaryDecision, FixtureDecisionEngine, OUTCOME_DECIDED, fixture_confidence
from personal_agent.orchestrator import Orchestration, QUESTION
import test_orchestrator as fixtures
from test_orchestrator import Harness, decided, plan


# The four contract observation inputs and a synthetic held-out capability.
CASES = (
    ('스크린골프장 약속 가는 길, 큰길 우선으로 알려줘',
     'Arrive on time using the saved service and major roads.',
     'Saved service exposes duration but no major-road option.',
     'Use the observed duration for a leave-by time; disclose the unsupported preference.'),
    ('내 쇼핑몰에서 이 상품 두 개 장바구니에 담아줘. 결제는 하지 마.',
     'Have exactly two of the requested variant ready without payment.',
     'Connected hand can read variants; its mutation is unavailable.',
     'Use an authorized signed-in hand after reading back the same variant and quantity.'),
    ('현재 자산 요약해줘. 내 계좌만 읽어줘.',
     'Understand current holdings in the same account without orders.',
     'No AI-side hand found; existing API read returned partial dated holdings.',
     'Report the supported partial holdings with account, as-of time and missing fields.'),
    ('이 자료를 다른 AI에게 독립적으로 검토받아줘.',
     'Obtain a second opinion without leading it with the first conclusion.',
     'A different configured worker is available; specialist delegation is unavailable.',
     'Propose the available independent worker with evidence context but no conclusion.'),
    ('LumenForge의 위상 기록을 비교해줘. Z-17만 써.',
     'Compare phase records for the required Z-17 identity.',
     'An unnamed read-only export supplies Z-17 records; the named hand is unavailable.',
     'Compare the export within scope; do not substitute another identity.'),
)


class GoalRouteContract(unittest.TestCase):
    def test_the_owner_surface_is_a_fact_in_both_routes(self):
        # #1380 live: from Telegram the worker sent the owner to check a key on a computer.
        self.assertIn('might already hold', agent_runtime.CORE_INSTRUCTIONS)
        history = [{'role': 'user', 'content': 'request'}]
        phone = agent_runtime.turn_context(history, 'cli', surface='telegram')
        self.assertIn('# Where the owner is writing', agent_runtime.render_turn_prompt(phone))
        self.assertIn('one-time page', agent_runtime.context_sections(phone))
        for surface in (None, 'local', 'anything'):
            plain = agent_runtime.turn_context(history, 'cli', surface=surface)
            self.assertNotIn('surface', plain)
            self.assertNotIn('Where the owner is writing', agent_runtime.render_turn_prompt(plain))

    def test_first_plan_worker_and_existing_judge_share_the_goal_first_contract(self):
        self.assertIn('Before choosing a worker', QUESTION)
        self.assertIn('observed discovery outcomes', QUESTION)
        self.assertIn('never restate, replace, narrow or extend', QUESTION)
        guidance = agent_runtime.GOAL_FIRST_GUIDANCE
        self.assertIn('from the first attempt', guidance)
        # #1508: asking (one answer, only what no tool can find) is stated once, in the core prompt.
        self.assertIn('then ask for everything in one answer', agent_runtime.CORE_INSTRUCTIONS)
        self.assertNotIn('Ask a concise question if required context is missing', agent_runtime.CORE_INSTRUCTIONS)
        self.assertIn("the vendor's own developer documentation and API", guidance)
        self.assertIn('experienced developer would after reading the manuals', guidance)
        self.assertIn('List position only breaks ties', guidance)
        self.assertIn('why you set the others aside', guidance)
        # #1380: no fixed walk through the candidate list.
        self.assertNotIn('Next consider public skills, then', guidance)
        self.assertNotIn('then public skills, Still reference skills, then', QUESTION)
        self.assertIn('not by list position', QUESTION)
        self.assertIn('which candidate routes were found', GOAL_REACHED_PROPOSITION)
        self.assertIn('A name-search miss is not evidence', guidance)
        self.assertIn('implementation gap separately from', guidance)
        self.assertIn('not a replacement goal', guidance)
        self.assertIn(guidance, agent_runtime.POLICY)
        self.assertIn(guidance, agent_runtime.render_turn_prompt(
            agent_runtime.turn_context([{'role': 'user', 'content': 'request'}], 'cli')))
        self.assertIn('a route the owner did not name', GOAL_REACHED_PROPOSITION)
        self.assertIn('Novelty is not required', GOAL_REACHED_PROPOSITION)
        # #1504: a connected route listed in owner_context, untried, makes "not supported here" short.
        self.assertIn('owner_context lists a connected route whose stated authority covers it', GOAL_REACHED_PROPOSITION)
        # #1506: cancelling something in an account is work, not withdrawing the previous Work.
        self.assertIn('is work for the assistant to do, not cancel', FOLLOWUP_QUESTION)
        self.assertIn('setup receipt alone', GOAL_REACHED_PROPOSITION)
        self.assertIn('return false when the reply only proposes a route', GOAL_REACHED_PROPOSITION)
        self.assertIn('This overrides the general rule above', GOAL_REACHED_PROPOSITION)
        self.assertIn('waiting for continuation, not successful completion', GOAL_REACHED_PROPOSITION)
        self.assertIn('judge that actual request without inventing further work', GOAL_REACHED_PROPOSITION)
        from test_no_scenario_code import scenario_tokens
        self.assertEqual(scenario_tokens(guidance), [])

    def test_five_case_facts_reach_brief_and_one_existing_outcome_judgment(self):
        for request, purpose, discovery, route in CASES:
            with self.subTest(request=request):
                plans, outcomes = [], []
                notes = purpose + ' Observed: ' + discovery + ' Candidate: ' + route

                def structured(context, question, schema):
                    plans.append(context)
                    return decided(plan('a', notes))

                def judge(context, proposition):
                    outcomes.append((context, proposition))
                    return BinaryDecision(OUTCOME_DECIDED, True, fixture_confidence())

                orchestration = Orchestration(
                    ConversationJudgments(FixtureDecisionEngine(structured=structured, judge=judge)),
                    fixtures.OrchestrationUnit.catalogue(None), request=request,
                    sections={'current_context': discovery}, conversation='Keep the stated constraints.')
                attempt = orchestration.first()
                self.assertEqual(attempt.brief(), notes)
                self.assertEqual(plans[0].facts['owner_request'], request)
                self.assertIn(discovery, plans[0].facts['current_context'])
                self.assertEqual(orchestration.evaluate_answer(route, discovery), 'reached')
                self.assertEqual(len(outcomes), 1, 'no second route judge')
                context, proposition = outcomes[0]
                self.assertEqual(context.facts['owner_request'], request)
                self.assertEqual(context.facts['reply'], route)
                self.assertEqual(context.facts['observations'], discovery)
                self.assertNotIn('completion_criteria', context.facts)
                self.assertEqual(proposition, GOAL_REACHED_PROPOSITION)

    def test_an_unconfirmed_or_mismatched_answer_is_not_reached_and_is_re_planned(self):
        """#1440 (live 2026-10-12): tried-but-unconfirmed, or a usual fact for a specific case, is not served."""
        self.assertIn('could not find, open or confirm it does not serve the message either, even after an attempt',
                      GOAL_REACHED_PROPOSITION)
        self.assertIn('supports only itself, not a claim about the specific day, time or case', GOAL_REACHED_PROPOSITION)
        from test_no_scenario_code import scenario_tokens
        self.assertEqual(scenario_tokens(GOAL_REACHED_PROPOSITION.split('#1440')[0][-600:]), [])
        plans = []

        def structured(context, question, schema):
            plans.append(context)
            return decided(plan('a', ''))
        decisions = FixtureDecisionEngine(
            structured=structured, judge=lambda *_: BinaryDecision(OUTCOME_DECIDED, False, fixture_confidence()))
        orchestration = Orchestration(ConversationJudgments(decisions), fixtures.OrchestrationUnit.catalogue(None),
                                      request='Is the shop open today?')
        attempt = orchestration.first()
        verdict = orchestration.evaluate_answer('It usually opens at 10:30; I could not confirm today.',
                                                'web_search: no source URL reported')
        self.assertEqual(verdict, 'not_reached')
        orchestration.next(attempt, verdict, answer='It usually opens at 10:30; I could not confirm today.')
        self.assertEqual(len(plans), 2, 'a re-plan is asked instead of stopping as reached')

    def test_setup_and_proposal_verdicts_stop_short_of_execution_completion(self):
        for reply, observed in (
                ('Use the available export; shall I connect it?', 'Candidate found; no connection or input binding.'),
                ('The connection is ready.', 'Readiness succeeded; no requested records were read.'),
                ('Please approve the connection.', 'A durable prerequisite binding is pending.')):
            with self.subTest(reply=reply):
                seen = []

                def judge(context, proposition):
                    seen.append(context)
                    # The injected negative is the amended proposition's contract;
                    # this test verifies its real consequence, not model semantics.
                    self.assertIn('return false when the reply only proposes a route', proposition)
                    return BinaryDecision(OUTCOME_DECIDED, False, fixture_confidence())

                decisions = FixtureDecisionEngine(
                    structured=lambda *_: decided(plan('a', '')), judge=judge)
                orchestration = Orchestration(ConversationJudgments(decisions),
                    fixtures.OrchestrationUnit.catalogue(None), request='Compare the Z-17 records.')
                attempt = orchestration.first()
                verdict = orchestration.evaluate_answer(reply, observed)
                self.assertEqual(verdict, 'not_reached')
                # A repeated worker/model is refused; none of these reports may
                # terminate as REACHED merely because the wording is useful.
                self.assertIsNone(orchestration.next(attempt, verdict, answer=reply))
                self.assertEqual(orchestration.terminal, 'not_reached')
                self.assertEqual(len(seen), 1)


class GoalRouteWorkerHandoff(Harness):
    def test_first_attempt_carries_goal_and_discovery_notes_to_both_workers(self):
        request, purpose, discovery, route = CASES[-1]
        notes = purpose + ' Observed: ' + discovery
        self.engine.answers = [route]
        self.transport.answers = [route, route]
        self.script([plan('codex', notes), plan('openai', notes)], goals=[True, True])
        for worker in ('cli', 'api'):
            if worker == 'api':
                self.use_main('openai')  # #1431: the API route runs as the Main AI
            _job, row = self.run_work(request, key='first-goal-' + worker)
            self.assertEqual(row['status'], 'succeeded')
        cli = self.engine.turns[0]['context']
        self.assertEqual(cli['request'], request)
        self.assertEqual(cli['brief'], notes)
        self.assertIn(agent_runtime.GOAL_FIRST_GUIDANCE, self.engine.turns[0]['prompt'])
        body = self.transport.bodies[0]
        self.assertIn(notes, body['messages'][0]['content'])
        self.assertIn(agent_runtime.GOAL_FIRST_GUIDANCE, body['messages'][0]['content'])
        self.assertEqual(body['messages'][-1], {'role': 'user', 'content': request})

    def test_bare_limitation_is_replanned_and_unbound_proposal_stays_incomplete(self):
        request = 'Compare LumenForge phase records, using Z-17 only.'
        limitation = 'The named connection is unavailable.'
        proposal = ('The available export can supply Z-17 records. I have not read them yet. '
                    'May I use that export for this comparison?')
        self.engine.answers = [limitation, proposal]
        self.script([plan('codex', 'The desired result is the comparison for Z-17.'),
                     plan('codex', 'Observed: the named connection is unavailable; the export is a candidate.',
                          model='gpt-5.6-luna')], goals=[False, False])
        job, row = self.run_work(request)
        self.assertEqual(len(self.engine.turns), 2)
        self.assertIn(limitation, self.asked_plans[1][0].facts['previous_attempts'])
        self.assertTrue(all(item[0].facts['owner_request'] == request for item in self.asked_plans))
        # No tool ran or prerequisite was bound: a plain offer cannot create a
        # continuation, an owner-needed receipt or successful completion.
        self.assertEqual(row['status'], 'failed', row)
        self.assertIn(proposal, row['response'])
        self.assertEqual(self.events(job, 'evaluated')[-1][1]['outcome'], 'not_reached')
        self.assertEqual(len(self.asked_goals), 2, 'one existing judgment per attempt')
