"""Visible planning/evaluation calls share the request's existing allowance."""
import unittest
from concurrent.futures import Future

from personal_agent.agent_runtime import WorkBudget, goal_judgment
from personal_agent.conversation_handoff import ConversationJudgments
from personal_agent.decision import BinaryDecision, FixtureDecisionEngine, OUTCOME_DECIDED, fixture_confidence
from personal_agent.orchestrator import Orchestration
import test_orchestrator as fixtures


class DecisionBudget(unittest.TestCase):
    def make(self, budget, calls, request='request'):
        def plan(context, *_):
            calls.append('plan')
            return fixtures.decided(fixtures.plan('a', ''))
        def judge(context, _):
            calls.append('judge')
            return BinaryDecision(OUTCOME_DECIDED, True, fixture_confidence())
        judgments = ConversationJudgments(FixtureDecisionEngine(structured=plan, judge=judge))
        return Orchestration(judgments, fixtures.OrchestrationUnit.catalogue(None), request=request, budget=budget)

    def test_planning_spends_before_dispatch_and_exhaustion_prevents_evaluation(self):
        calls = []
        budget = WorkBudget(turns=1)
        orchestration = self.make(budget, calls)
        orchestration.first()
        self.assertEqual((calls, budget.turns_used), (['plan'], 1))
        self.assertEqual(orchestration.evaluate_answer('answer', 'observed', final=True), 'not_judged')
        self.assertEqual(calls, ['plan'])

    def test_early_result_is_charged_once_but_changed_input_replans(self):
        for changed, expected in ((False, 1), (True, 2)):
            with self.subTest(changed=changed):
                calls = []
                budget = WorkBudget(turns=4)
                early = Future()
                early.set_result(self.make(budget, calls).early_plan())
                current = self.make(budget, calls, 'changed' if changed else 'request')
                current.first(early=early)
                self.assertEqual((len(calls), budget.turns_used), (expected, expected))
                current.evaluate_answer('answer', 'observed')
                self.assertEqual(budget.turns_used, expected + 1)

    def test_direct_completion_judgment_obeys_the_same_allowance(self):
        calls = []
        budget = WorkBudget(turns=1)
        judgments = self.make(budget, calls).judgments
        args = (judgments, 'request', {'evidence_refs': [], 'summary': 'answer'}, {}, [])
        self.assertEqual(goal_judgment(*args, budget=budget), 'yes')
        self.assertEqual(goal_judgment(*args, budget=budget), 'unavailable')
        self.assertEqual((calls, budget.turns_used), (['judge'], 1))


class JudgmentReserve(unittest.TestCase):
    """#1418: the last turn of a request is the outcome judgment's."""

    def test_ordinary_calls_leave_the_last_turn_and_the_judgment_takes_it(self):
        from personal_agent.agent_runtime import ToolError
        budget = WorkBudget(turns=3)
        budget.spend_turn();budget.spend_turn()
        with self.assertRaises(ToolError):budget.spend_turn()
        budget.spend_turn(reserve=True)
        with self.assertRaises(ToolError):budget.spend_turn(reserve=True)
        single = WorkBudget(turns=1)
        single.spend_turn()
        self.assertEqual(single.turns_used, 1, 'one turn goes to whichever call comes first')

    def test_a_worker_on_the_last_ordinary_turn_is_still_judged_and_no_unrunnable_replan_is_asked(self):
        calls = []
        budget = WorkBudget(turns=4)
        orchestration = DecisionBudget().make(budget, calls)
        orchestration.judgments.engine._judge = lambda *_: calls.append('judge') or BinaryDecision(
            OUTCOME_DECIDED, False, fixture_confidence())
        attempt = orchestration.first()
        budget.spend_turn();budget.spend_turn()  # the worker's turns up to the ordinary limit
        self.assertEqual(orchestration.evaluate_answer('answer', 'observed'), 'not_reached')
        self.assertIsNone(orchestration.next(attempt, 'not_reached'))
        self.assertEqual((calls, budget.turns_used), (['plan', 'judge'], 4))

    def test_a_judged_shortfall_with_room_is_re_delegated(self):
        calls = []
        budget = WorkBudget(turns=6)
        orchestration = DecisionBudget().make(budget, calls)
        orchestration.judgments.engine._judge = lambda *_: calls.append('judge') or BinaryDecision(
            OUTCOME_DECIDED, False, fixture_confidence())
        attempt = orchestration.first()
        budget.spend_turn()  # worker
        self.assertEqual(orchestration.evaluate_answer('answer', 'observed'), 'not_reached')
        orchestration.next(attempt, 'not_reached')
        self.assertEqual(calls, ['plan', 'judge', 'plan'], 're-plan, worker and judgment still fit, so the re-plan is asked')


class EarlyRequestBudget(fixtures.Harness):
    def test_service_opens_one_durable_budget_before_early_plan_and_pauses_it(self):
        opened = []
        original = self.service.work_budget
        def opening(job_id):
            budget = original(job_id)
            opened.append(budget)
            return budget
        self.service.work_budget = opening
        self.script([fixtures.plan('codex', '')], goals=[True])
        job, row = self.run_work('Complete this synthetic request')
        self.assertEqual(row['status'], 'succeeded')
        self.assertEqual(len(opened), 1)
        saved = self.store.config('work_budget:' + job)
        self.assertEqual(saved['turns'], 3, 'one plan, one host CLI invocation, one evaluation')
        self.assertFalse(saved['active'])
        self.assertTrue(self.events(job, 'planned')[0][1]['early'])

    def test_early_planning_time_is_not_replenished_before_worker_dispatch(self):
        from personal_agent.agent_runtime import WorkLedger
        now = [1000.0]
        self.service.work_budget = lambda job_id: WorkBudget(clock=lambda: now[0], seconds=60,
            ledger=WorkLedger(self.store, job_id, wall=lambda: now[0], fresh=True, seconds=60))
        self.script([fixtures.plan('codex', '')], goals=[True])
        original = self.service.decision_engine._structured
        def slow(context, question, schema):
            result = original(context, question, schema)
            if context.purpose == 'work-orchestration':now[0] += 70
            return result
        self.service.decision_engine._structured = slow
        job, row = self.run_work('Complete another synthetic request')
        self.assertEqual(self.engine.turns, [], 'expired initial planning cannot buy a fresh worker allowance')
        self.assertNotEqual(row['status'], 'succeeded')
        self.assertEqual(self.store.config('work_budget:' + job)['remaining_seconds'], 0)
        self.assertFalse([event for event in self.store.task_events(job)
                          if event['tool'] == 'subscription_engine' and event['status'] == 'running'])

    def test_natural_language_retry_resolves_root_before_opening_budget(self):
        from unittest import mock
        # A read-only API attempt may be retried; an unmediated CLI correctly
        # cannot, regardless of this budget fix.  #1431: the API route is the Main AI here.
        self.use_main('openai')
        self.script([fixtures.plan('openai', '')], goals=[False])
        parent, row = self.run_work('Compare the two synthetic records', key='first')
        self.assertEqual(row['status'], 'failed')
        before = self.store.config('work_budget:' + parent)
        self.script([fixtures.plan('openai', '')], goals=[True])
        relation = {'relation': 'retry', 'previous': self.store.job(parent)}
        with mock.patch.object(self.service, 'continuity_relation', return_value=relation):
            child, row = self.run_work('Try again', key='second')
        self.assertEqual(row['status'], 'succeeded', row)
        self.assertEqual(row['related_job_id'], parent)
        self.assertEqual(row['relation_kind'], 'retry')
        self.assertIsNone(self.store.config('work_budget:' + child))
        after = self.store.config('work_budget:' + parent)
        # Plan, the API turns and the judgment all land on the parent's request row.
        self.assertGreaterEqual(after['turns'], before['turns'] + 3)
        self.assertFalse(after['active'])

    def test_an_explicit_command_starts_no_early_plan(self):
        """#1389: /help is answered with no model, so no speculative plan spends its budget."""
        started = []
        original = self.service.start_early_judgment

        def start(work_id, call, *args, **kwargs):
            if call == self.service.early_work_plan:started.append(work_id)
            return original(work_id, call, *args, **kwargs)
        self.service.start_early_judgment = start
        job, row = self.run_work('/help')
        self.assertEqual(row['status'], 'succeeded')
        self.assertEqual((started, self.engine.turns), ([], []))
        self.assertEqual(self.store.config('work_budget:' + job)['turns'], 0)

    def test_unused_early_future_settles_before_active_time_pauses(self):
        from personal_agent.agent_runtime import WorkLedger
        now = [1000.0]
        self.service.work_budget = lambda job_id: WorkBudget(clock=lambda: now[0], seconds=60,
            ledger=WorkLedger(self.store, job_id, wall=lambda: now[0], fresh=True, seconds=60))
        original = self.service.start_early_judgment
        settled = []

        class DeferredPlan:
            # An injected Future seam delays the plan until the host settles it;
            # no sleep or model call is necessary to exercise finalization order.
            def __init__(self, budget):self.budget = budget
            def result(self):
                if not settled:
                    self.budget.spend_turn()
                    now[0] += 15
                    settled.append(True)
                return None

        def start(work_id, call, *args, **kwargs):
            if call == self.service.early_work_plan:return DeferredPlan(args[-1])
            return original(work_id, call, *args, **kwargs)
        self.service.start_early_judgment = start
        # #1389: an explicit command no longer starts a plan (test above), so this Work
        # ends before reading its plan by a step after routing that fails.
        def stop(job):raise RuntimeError('stopped before the worker')
        self.service.present_turn = stop
        job = self.store.enqueue('Complete this synthetic request', 'unread-plan')
        with self.assertRaisesRegex(RuntimeError, 'stopped before the worker'):
            self.service.run_one()
        self.assertEqual(self.engine.turns, [])
        self.assertEqual(settled, [True])
        saved = self.store.config('work_budget:' + job)
        self.assertFalse(saved['active'])
        self.assertEqual((saved['turns'], saved['remaining_seconds']), (1, 45))


class RoutedAdmission(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        from personal_agent.quickstart_store import QuickStore
        from personal_agent.quickstart_service import AgentService
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = QuickStore(Path(temp.name))
        self.service = AgentService(self.store)
        self.engine = FixtureDecisionEngine(structured=lambda *_: None)
        # Keep the production routed admission; inject only the selected worker.
        self.service.decision_engine.resolve = lambda: self.engine
        self.work = self.store.enqueue('A synthetic request', 'first')
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (self.work,))
        self.service.current_work_id = self.work

    def budget(self, turns=1, seconds=60, now=None):
        from personal_agent.agent_runtime import WorkLedger
        now = now or [1000.0]
        budget = WorkBudget(turns=turns, seconds=seconds, clock=lambda: now[0],
                            ledger=WorkLedger(self.store, self.work, fresh=True, seconds=seconds, wall=lambda: now[0]),
                            stop=lambda: self.service.work_stopped(self.work))
        self.service._work_local.budget = budget
        return budget

    def test_all_generic_operations_refuse_before_route_resolution_when_exhausted(self):
        from personal_agent.decision import DecisionContext
        budget = self.budget()
        route = self.service.decision_engine
        route.judge(DecisionContext('first'), 'question')
        calls = [('judge', ('q',)), ('choose', (['a'], 'q')), ('score', ('q',)),
                 ('choose_many', (['a'], 'q')), ('structured', ('q', {'type': 'object'}))]
        for method, args in calls:
            with self.subTest(method=method):
                decision = getattr(route, method)(DecisionContext('later'), *args)
                self.assertNotEqual(decision.outcome, OUTCOME_DECIDED)
        self.assertEqual(len(self.engine.asked), 1)
        self.assertEqual(budget.ledger.used('turns'), 1)

    def test_parallel_early_judgments_share_the_last_durable_allowance(self):
        from personal_agent.decision import DecisionContext
        budget = self.budget()
        futures = [self.service.start_early_judgment(self.work, self.service.decision_engine.judge,
                    DecisionContext('early'), 'q') for _ in range(8)]
        for future in futures:future.result(timeout=5)
        self.assertEqual(len(self.engine.asked), 1)
        self.assertEqual(budget.ledger.used('turns'), 1)
        self.assertIs(self.service.current_decision_budget(), budget)
        self.assertEqual(self.service.current_work_id, self.work)

    def test_cancel_and_deadline_refuse_without_dispatch_or_spend(self):
        from personal_agent.decision import DecisionContext
        now = [1000.0]
        budget = self.budget(now=now)
        self.service.decision_engine.judge(DecisionContext('cancelled', cancelled=lambda: True), 'q')
        now[0] += 61
        self.service.decision_engine.judge(DecisionContext('expired'), 'q')
        self.assertEqual(self.engine.asked, [])
        self.assertEqual(budget.ledger.used('turns'), 0)

    def test_source_attribution_alone_does_not_open_a_foreground_budget(self):
        from personal_agent.decision import DecisionContext
        self.service.decision_engine.judge(DecisionContext('source-attributed-upkeep'), 'q')
        self.assertEqual(len(self.engine.asked), 1)
        self.assertIsNone(self.store.config('work_budget:' + self.work))

    def test_paused_and_restarted_work_inherits_spent_decisions(self):
        from personal_agent.decision import DecisionContext
        budget = self.budget()
        self.service.decision_engine.judge(DecisionContext('first'), 'q')
        budget.ledger.pause()
        resumed = self.budget()
        self.service.decision_engine.judge(DecisionContext('resumed'), 'q')
        self.assertEqual((len(self.engine.asked), resumed.ledger.used('turns')), (1, 1))

    def test_plan_and_both_completion_paths_do_not_charge_twice(self):
        from personal_agent.decision import RoutedDecisionEngine
        calls = []
        budget = self.budget(turns=3)
        orchestration = DecisionBudget().make(budget, calls)
        engine = orchestration.judgments.engine
        orchestration.judgments.engine = RoutedDecisionEngine(lambda: engine, budget=lambda: budget)
        orchestration.first()
        self.assertEqual(orchestration.evaluate_answer('answer', 'observed', final=True), 'reached')
        args = (orchestration.judgments, 'request', {'evidence_refs': [], 'summary': 'answer'}, {}, [])
        self.assertEqual(goal_judgment(*args, budget=budget), 'yes')
        self.assertEqual((calls, budget.ledger.used('turns')), (['plan', 'judge', 'judge'], 3))
        self.assertEqual(goal_judgment(*args, budget=budget), 'unavailable')
        self.assertEqual(len(calls), 3)


class SemanticWorkerBudget(fixtures.Harness):
    def routed_script(self, plans, goals=()):
        from personal_agent.decision import RoutedDecisionEngine
        engine = self.script(plans, goals=goals)
        self.service.use_decision_engine(RoutedDecisionEngine(lambda: engine, budget=self.service.current_decision_budget))
        return engine

    def test_real_worker_admits_early_semantics_and_reports_original_result(self):
        engine = self.routed_script([fixtures.plan('codex', '')], goals=[True])
        job, row = self.run_work('Complete this synthetic request')
        self.assertEqual(row['status'], 'succeeded', row)
        saved = self.store.config('work_budget:' + job)
        self.assertEqual(saved['turns'], len(engine.asked) + len(self.engine.turns))
        self.assertGreater(saved['turns'], 3, 'initial semantic decisions now count too')
        self.assertFalse(saved['active'])
        self.assertIsNone(self.service.current_decision_budget())

    def test_newly_judged_retry_folds_preassociation_calls_into_original_ledger(self):
        from unittest import mock
        from personal_agent.agent_runtime import WorkLedger
        from personal_agent.decision import DecisionContext
        parent = self.store.enqueue('Original synthetic goal', 'parent')
        ledger = WorkLedger(self.store, parent, fresh=True)
        ledger.spend(9, 'turns'); ledger.spend(9, 'turns'); ledger.pause()
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='failed' WHERE id=?", (parent,))
        engine = self.routed_script([fixtures.plan('codex', '')], goals=[True])
        def relation(*args, **kwargs):
            self.service.decision_engine.judge(DecisionContext('followup'), 'Is this a retry?')
            return {'relation': 'retry', 'previous': self.store.job(parent)}
        with mock.patch.object(self.service, 'continuity_relation', side_effect=relation):
            child, row = self.run_work('Try the original goal again', key='child')
        self.assertEqual(row['status'], 'succeeded', row)
        self.assertEqual(row['related_job_id'], parent)
        self.assertIsNone(self.store.config('work_budget:' + child))
        saved = self.store.config('work_budget:' + parent)
        self.assertEqual(saved['turns'], 2 + len(engine.asked) + len(self.engine.turns))
        self.assertFalse(saved['active'])

    def test_routing_elapsed_time_is_not_replenished_at_planning(self):
        from personal_agent.agent_runtime import WorkLedger
        now = [1000.0]
        self.service.work_budget = lambda job_id: WorkBudget(clock=lambda: now[0], seconds=60,
            ledger=WorkLedger(self.store, job_id, wall=lambda: now[0], fresh=True, seconds=60))
        engine = self.routed_script([fixtures.plan('codex', '')], goals=[True])
        def slow(*_):
            now[0] += 70
            return None
        engine._choose = slow
        job, row = self.run_work('A request that needs a routing judgment')
        self.assertEqual(self.engine.turns, [])
        self.assertNotEqual(row['status'], 'succeeded')
        self.assertFalse(self.store.config('work_budget:' + job)['active'])
        self.assertEqual(self.store.config('work_budget:' + job)['remaining_seconds'], 0)


class AdapterAdmission(unittest.TestCase):
    def test_supported_adapter_transports_are_not_called_after_request_exhaustion(self):
        from personal_agent.decision import DecisionContext, RoutedDecisionEngine
        from test_decision_routes import AdapterParityTests
        fixture = AdapterParityTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        for name, engine in fixture.engines().items():
            with self.subTest(route=name):
                budget = WorkBudget(turns=1)
                route = RoutedDecisionEngine(lambda: engine, budget=lambda: budget)
                context = lambda: DecisionContext('conversation-followup', {'owner_message': '다시 해봐'})
                self.assertEqual(route.choose(context(), ['retry', 'reference'], 'relation?').outcome, OUTCOME_DECIDED)
                self.assertEqual(budget.turns_used, 1)
                transport = (engine.adapter.transport if name == 'direct_api' else
                             engine.transport if name == 'jev' else engine.execution.runner)
                before = len(transport.calls)
                second = route.choose(context(), ['retry', 'reference'], 'relation?')
                self.assertEqual(len(transport.calls), before)
                self.assertNotEqual(second.outcome, OUTCOME_DECIDED)
                self.assertFalse(second.confidence.sent)
                self.assertEqual(budget.turns_used, 1)

    def test_unconfigured_or_rejected_input_does_not_consume_provider_call_allowance(self):
        from personal_agent.decision import DecisionContext, ModelDecisionEngine, RoutedDecisionEngine
        from personal_agent.providers import ModelAdapter
        from test_decision_routes import OpenAITransport, OPENAI_KEY
        transport = OpenAITransport()
        config = [None]
        engine = ModelDecisionEngine(ModelAdapter(transport), lambda: config[0])
        budget = WorkBudget(turns=1)
        route = RoutedDecisionEngine(lambda: engine, budget=lambda: budget)
        route.judge(DecisionContext('unconfigured'), 'q')
        self.assertEqual(budget.turns_used, 0)
        config[0] = ({'provider': 'openai', 'endpoint': 'https://api.openai.com/v1', 'model': 'gpt-4o-mini'}, OPENAI_KEY)
        route.judge(DecisionContext('oversized', {'fact': 'x' * 7000}), 'q')
        self.assertEqual((budget.turns_used, transport.calls), (0, []))


class PresenceDecisionBudget(unittest.TestCase):
    def setUp(self):
        from personal_agent.telegram_presence import WaitState
        fixture = RoutedAdmission()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.store, self.service, self.engine, self.work = fixture.store, fixture.service, fixture.engine, fixture.work
        self.job = self.store.job(self.work)
        self.state = WaitState()
        self.pending = []
        self.service.turn_reaction_spawn = self.pending.append
        self.service.progress_reaction_spawn = self.pending.append

    def dispatch(self):
        from concurrent.futures import ThreadPoolExecutor
        pending = list(self.pending)
        self.pending.clear()
        with ThreadPoolExecutor(max_workers=2) as pool:
            for future in [pool.submit(call) for call in pending]:future.result(timeout=5)

    def reactions(self):
        self.service._spawn_turn_reaction(self.job, self.state)
        self.service._react_progress_choice(self.job, self.state, ('observed-step', 'Looking at a result'))

    def test_reactions_spend_their_own_bounded_counter_never_the_work_turns(self):
        from personal_agent.agent_runtime import WORK_PRESENTATION_TURNS
        budget = self.service.work_budget(self.work)
        for _ in range(budget.turn_limit()):budget.spend_turn()
        self.reactions()
        self.dispatch()
        self.assertEqual(len(self.engine.asked), 2, '#1418: an emoji no longer waits on the work turns')
        self.assertEqual((budget.ledger.used('turns'), budget.ledger.used('presentation')), (budget.turn_limit(), 2))
        budget.spend_turn(reserve=True)
        self.assertEqual(budget.ledger.used('turns'), budget.turns, 'the held turn is still the judgment\'s')
        value = self.store.config('work_budget:' + self.work)
        value['presentation'] = WORK_PRESENTATION_TURNS - 1
        self.store.put('work_budget:' + self.work, value)
        self.reactions()
        self.dispatch()
        self.assertEqual(len(self.engine.asked), 3, 'the reaction counter keeps the request-level bound')
        self.assertEqual(budget.ledger.used('presentation'), WORK_PRESENTATION_TURNS)
        self.assertIsNone(self.service.current_decision_budget(), 'poll thread must not acquire a foreground scope')
        self.assertTrue(self.service.progress_reaction_flight.acquire(blocking=False))
        self.service.progress_reaction_flight.release()

    def test_missing_budget_never_creates_an_optional_reaction_allowance(self):
        self.reactions()
        self.dispatch()
        self.assertEqual(self.engine.asked, [])
        self.assertIsNone(self.store.config('work_budget:' + self.work))

    def test_late_reactions_cannot_spend_a_paused_or_later_run(self):
        budget = self.service.work_budget(self.work)
        self.reactions()
        budget.ledger.pause()
        resumed = self.service.work_budget(self.work)
        self.dispatch()
        self.assertEqual(self.engine.asked, [])
        self.assertEqual(resumed.ledger.used('presentation'), 0)
        self.service._spawn_turn_reaction(self.job, self.state)
        self.dispatch()
        self.assertEqual(len(self.engine.asked), 1)
        self.assertEqual((resumed.ledger.used('presentation'), resumed.ledger.used('turns')), (1, 0))

    def test_stop_and_deadline_prevent_queued_reaction_dispatch(self):
        from unittest import mock
        budget = self.service.work_budget(self.work)
        self.reactions()
        with mock.patch.object(self.service, 'work_stopped', return_value=True):
            self.dispatch()
        self.assertEqual(self.engine.asked, [])
        self.reactions()
        value = self.store.config('work_budget:' + self.work)
        value['deadline'] = 1
        self.store.put('work_budget:' + self.work, value)
        self.dispatch()
        self.assertEqual(self.engine.asked, [])
        self.assertEqual(budget.ledger.used('turns'), 0)

    def test_deleted_ledger_cannot_be_recreated_by_already_queued_reaction(self):
        self.service.work_budget(self.work)
        self.reactions()
        with self.store.db() as db:db.execute('DELETE FROM config WHERE key=?', ('work_budget:' + self.work,))
        self.dispatch()
        self.assertEqual(self.engine.asked, [])
        self.assertIsNone(self.store.config('work_budget:' + self.work))

    def test_inline_spawn_fallback_restores_both_caller_bindings(self):
        budget = self.service.work_budget(self.work)
        self.service.current_work_id = 'caller-work'
        self.service._work_local.budget = budget
        def refused(_):raise RuntimeError('cannot start a thread')
        self.service.turn_reaction_spawn = refused
        self.service._spawn_turn_reaction(self.job, self.state)
        self.assertEqual(len(self.engine.asked), 1)
        self.assertEqual(self.service.current_work_id, 'caller-work')
        self.assertIs(self.service.current_decision_budget(), budget)

    def test_delivered_outcome_keeps_truth_gated_fallback_without_reopening_budget(self):
        from unittest import mock
        from personal_agent.telegram_presence import DONE_REACTION
        budget = self.service.work_budget(self.work)
        budget.spend_turn(); budget.ledger.pause()
        with self.store.db() as db:db.execute("UPDATE jobs SET status='succeeded' WHERE id=?", (self.work,))
        saved = self.store.config('work_budget:' + self.work)
        with mock.patch.object(self.service.telegram_turns, 'source', return_value=123), \
             mock.patch.object(self.service, '_presence_call', return_value=True) as sent:
            self.service._present_outcome(self.store.job(self.work), delivered=True, blocked=False)
        self.assertEqual(self.engine.asked, [])
        self.assertEqual(self.store.config('work_budget:' + self.work), saved)
        self.assertEqual(sent.call_args.args[-1], DONE_REACTION)
        self.assertIsNone(self.service.current_decision_budget())
