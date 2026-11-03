"""#627 (E4 of #614): one continuation count, owner waits paused from the deadline, a reason for every budget stop.

Evidence class: model-free unit and service tests (scripted Judgment AI and workers, temporary stores,
fake clocks).  No live model, CLI, account or credential is used.
"""
import json
import time
import unittest

import test_budget_continue
import test_orchestrator
from test_orchestrator import Harness, plan, decided
from personal_agent.agent_runtime import (BUDGET_TEXTS, MAX_CONTINUATIONS, WORK_DEADLINE, WORK_DEADLINE_SECONDS,
                                          WORK_LEDGER_KEY, WORK_TURNS_EXHAUSTED, GOAL_UNJUDGED, ToolError, WorkBudget,
                                          WorkLedger, continuation_count)
from personal_agent.conversation_handoff import ConversationJudgments
from personal_agent.decision import (OUTCOME_CANCELLED, DecisionContext, FixtureDecisionEngine, RoutedDecisionEngine)
from personal_agent.decision_adapters import JevDecisionEngine
from personal_agent.messages import msg
from personal_agent.orchestrator import NOT_JUDGED, NOTICE_ONCE, Orchestration


def receipt(store, job, tool='action_continuation', status='queued', index=0):
    with store.db() as db:
        db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                   (job, tool, status, json.dumps({'resumed': status == 'queued', 'offer_digest': f'{tool}-{index}'}),
                    time.time()))


class OneContinuationCount(unittest.TestCase):
    """Resumes, renewals and budget-continue share one bounded count, read through the one continuation rule."""

    # The real Work lifecycle of the #471 tests (their helpers, not their tests).
    service = test_budget_continue.BudgetContinueTests.service
    stopped = test_budget_continue.BudgetContinueTests.stopped

    def retry_of(self, store, job):
        retry = store.enqueue('Read the material and produce a comparison', 'budget-test-retry')
        with store.db() as db:
            db.execute("UPDATE jobs SET relation_kind='retry',related_job_id=?,status='partial' WHERE id=?", (job, retry))
        return retry

    def queue(self, service, store, job, digest):
        with service.lock, store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            return service._queue_action_continuation(db, store.job(job), {'action': 'browser_sign_in', 'digest': digest,
                                                                           'offer_digest': digest})

    def test_the_rule_names_the_limit_once_the_request_reached_its_one_count(self):
        service, store, job, _run, _ = self.stopped()
        for index in range(MAX_CONTINUATIONS - 1):
            receipt(store, job, 'settings_continuation' if index % 2 else 'action_continuation', index=index)
        receipt(store, job, status='withheld', index=99)  # a withheld resume is not a continuation
        retry = self.retry_of(store, job)
        self.assertEqual(service.may_continue(retry, 'resume'), (True, None))
        receipt(store, retry, index=100)
        with store.db() as db:
            self.assertEqual(continuation_count(db, retry), MAX_CONTINUATIONS)
        self.assertEqual(service.may_continue(retry, 'resume'), (False, 'limit'),
                         'one count for the whole request, its retries included')
        self.assertNotIn('limit', service.continuation_reasons(retry, 'redelegate'),
                         'attempts within one run are not continuations')

    def test_at_the_count_a_continuation_is_withheld_with_its_one_reason(self):
        service, store, job, _run, _ = self.stopped()
        for index in range(MAX_CONTINUATIONS):
            receipt(store, job, index=index)
        retry = self.retry_of(store, job)
        before = store.config(WORK_LEDGER_KEY + ':' + job)
        self.assertFalse(self.queue(service, store, retry, 'sign-in-after-limit'))
        current = store.job(retry)
        self.assertEqual((current['status'], current['owner_cause']), ('partial', msg('quickstart_service.resume_limit')))
        with store.db() as db:
            withheld = [json.loads(row[0]) for row in db.execute(
                "SELECT detail FROM tool_events WHERE job_id=? AND tool='action_continuation' AND status='withheld'", (retry,))]
        self.assertEqual([row['reason'] for row in withheld], ['continuation-budget'])
        self.assertEqual(store.config(WORK_LEDGER_KEY + ':' + job), before, 'no renewal past the count')

    def test_every_counted_continuation_renews_without_a_separate_renewal_cap(self):
        # Three owner prerequisites already renewed this request (the pre-#627 ``renewals`` cap of three).
        service, store, job, _run, _ = self.stopped()
        for index in range(3):
            receipt(store, job, index=index)
        key = WORK_LEDGER_KEY + ':' + job
        store.put(key, {**store.config(key), 'renewals': 3, 'remaining_seconds': 0})
        self.assertTrue(self.queue(service, store, job, 'fourth-sign-in'))
        renewed = store.config(key)
        self.assertEqual((renewed['turns'], renewed['attempts'], renewed['remaining_seconds']), (0, 0, WORK_DEADLINE_SECONDS),
                         'the fourth owner continuation runs with an allowance, not the time spent before the wait')
        with store.db() as db:
            self.assertEqual(continuation_count(db, job), 4)


class OwnerWaitPaused(unittest.TestCase):
    """Time a Work spends waiting for the owner is not spent from its deadline (LangGraph interrupt/resume)."""

    def test_a_wait_between_runs_leaves_the_remaining_time_untouched(self):
        import tempfile
        from personal_agent.quickstart_store import QuickStore
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = QuickStore(temp.name)
        job = store.enqueue('a request that waits for the owner', 'owner-wait')
        wall = [1000.0]
        ledger = WorkLedger(store, job, fresh=True, wall=lambda: wall[0])
        wall[0] += 100  # the run works for 100 s, then asks for an approval
        ledger.pause()
        wall[0] += 7200  # the owner answers two hours later
        resumed = WorkLedger(store, job, fresh=True, wall=lambda: wall[0])
        self.assertEqual(resumed.remaining(), WORK_DEADLINE_SECONDS - 100)
        self.assertIsNone(resumed.lapse())


class BudgetStub:
    """A Work budget whose turn admission the test controls (the shared ledger refused a turn)."""

    def __init__(self):
        self.turns, self.turns_used, self.refuse = 15, 0, None

    def interrupted(self):
        return None

    def remaining(self):
        return 600

    def turn_limit(self, reserve=False):
        return self.turns if reserve else self.turns - 1

    def spend_turn(self, reserve=False):
        if self.refuse:
            raise ToolError(BUDGET_TEXTS[self.refuse], self.refuse)
        self.turns_used += 1


class BudgetStopReasons(unittest.TestCase):
    """Every budget stop carries its #617 code and text to the owner and the model."""

    def routed(self, budget, **scripts):
        return ConversationJudgments(RoutedDecisionEngine(lambda: FixtureDecisionEngine(**scripts), budget=lambda: budget))

    def test_a_refused_turn_keeps_its_code(self):
        budget = BudgetStub()
        budget.refuse = 'turn_budget'
        context = DecisionContext('p', {'a': 'b'})
        engine = RoutedDecisionEngine(lambda: FixtureDecisionEngine(judge=lambda *a: None), budget=lambda: budget)
        engine.judge(context, 'q')
        self.assertEqual(context.refused, 'turn_budget')
        stopped = BudgetStub()
        stopped.interrupted = lambda: 'deadline_exceeded'
        context = DecisionContext('p', {'a': 'b'})
        RoutedDecisionEngine(lambda: FixtureDecisionEngine(judge=lambda *a: None), budget=lambda: stopped).judge(context, 'q')
        self.assertEqual(context.refused, 'deadline_exceeded')

    def test_an_adapter_records_the_refusal_reason(self):
        audit = []
        engine = JevDecisionEngine(lambda: 'jev-fixture-key', transport=lambda *a: self.fail('no request'), audit=audit.append)
        context = DecisionContext('p', {'a': 'b'})

        def refuse():
            raise ToolError(WORK_TURNS_EXHAUSTED, 'turn_budget')
        context.admission = refuse
        decision = engine.judge(context, 'q')
        self.assertEqual(decision.outcome, OUTCOME_CANCELLED)
        self.assertEqual(audit[-1]['failure'], 'turn_budget')

    def test_a_plan_turn_the_budget_refused_is_a_budget_fallback_not_an_unavailable_judgment_ai(self):
        budget = BudgetStub()
        budget.refuse = 'turn_budget'
        events = []
        orchestration = Orchestration(self.routed(budget, structured=lambda *a: decided(plan('a', 'g'))),
                                      test_orchestrator.OrchestrationUnit.catalogue(self), request='r', budget=budget,
                                      record=lambda status, detail: events.append((status, detail)))
        orchestration._set_state('active')
        attempt = orchestration.first()
        self.assertEqual(attempt.fallback, 'budget_short')
        self.assertNotEqual(orchestration.notice, NOTICE_ONCE, 'the Judgment AI is not said to be unavailable')
        status, detail = events[-1]
        self.assertEqual((status, detail['budget_code']), ('fallback', 'turn_budget'))
        self.assertIn(WORK_TURNS_EXHAUSTED, detail['text'])

    def test_a_judgment_the_budget_refused_stops_for_that_reason(self):
        budget = BudgetStub()
        events = []
        orchestration = Orchestration(self.routed(budget, structured=lambda *a: decided(plan('a', 'g')),
                                                  judge=lambda *a: self.fail('the refused judgment is never asked')),
                                      test_orchestrator.OrchestrationUnit.catalogue(self), request='r', budget=budget,
                                      record=lambda status, detail: events.append((status, detail)))
        attempt = orchestration.first()
        self.assertTrue(orchestration.orchestrated)
        budget.refuse = 'turn_budget'  # the CLI bridge spent the shared turns meanwhile
        self.assertEqual(orchestration.evaluate_answer('answer', 'observations'), NOT_JUDGED,
                         'not "the completion check was unavailable"')
        self.assertIsNone(orchestration.next(attempt, NOT_JUDGED, answer='answer'))
        status, detail = events[-1]
        self.assertEqual((status, detail['stop'], detail['code']), ('evaluated', 'budget', 'turn_budget'))
        self.assertIn(WORK_TURNS_EXHAUSTED, detail['text'])
        self.assertEqual(orchestration.terminal_code, 'turn_budget')
        self.assertIn('stopped by the Work budget (turn_budget)', orchestration._attempts_text(),
                      'a continued run\'s model reads why the last attempt stopped')


class CliBudgetStop(Harness):
    """The orchestrator's budget stop on the subscription-CLI route reaches the owner with its reason."""

    def test_a_cli_attempt_stopped_by_the_deadline_says_so_and_records_the_code(self):
        clock = [0.0]
        self.service.work_budget = lambda job_id: WorkBudget(clock=lambda: clock[0], seconds=600)
        self.engine.before = lambda tools: clock.__setitem__(0, clock[0] + 590)
        self.script([plan('codex', 'Look it up.')], goals=[True])
        job, row = self.run_work('시간 걸리는 일')
        last = self.events(job, 'evaluated')[-1][1]
        self.assertEqual((last['outcome'], last['stop'], last['code']), ('not_judged', 'budget', 'deadline_exceeded'))
        self.assertEqual(row['status'], 'partial')
        self.assertIn(WORK_DEADLINE, row['owner_cause'])
        self.assertNotIn(GOAL_UNJUDGED, row['owner_cause'])
        with self.store.db() as db:
            stops = [json.loads(r[0]) for r in db.execute(
                "SELECT detail FROM tool_events WHERE job_id=? AND tool='model' AND status='stopped'", (job,))]
        self.assertEqual([(stop['scope'], stop['code']) for stop in stops], [('orchestration', 'deadline_exceeded')])

    def test_service_shortfall_of_a_budget_stop_names_the_budget(self):
        job = self.store.enqueue('r', 'shortfall')
        outcome, report = self.service.cli_shortfall(job, 0, 'r', NOT_JUDGED, code='turn_budget')
        self.assertEqual(outcome, 'partial')
        self.assertEqual(report['unknown'], [WORK_TURNS_EXHAUSTED])


if __name__ == '__main__':
    unittest.main()
