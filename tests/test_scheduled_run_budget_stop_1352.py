"""#1352: a scheduled run that stops at its budget settles; it does not wait a day for Continue.

Model-free service evidence over real temporary stores and preparation ticks.
Reuse review: N/A for production implementation (tests only, no new dependency);
reuses test_preparations._Case and the #471 budget-stop setup from test_budget_continue.
"""
import json
import time

from personal_agent import preparations as prep
from personal_agent.agent_runtime import WorkLedger, WORK_LEDGER_KEY
from personal_agent.work_execution import WorkRun

from test_preparations import _Case


class ScheduledRunBudgetStopTests(_Case):
    def stopped_run(self):
        """A web preparation whose run Work ended partial at its budget after observed progress."""
        row = self.scheduled(recurrence='daily')
        self.now = row['due_at'] + 1
        self.assertTrue(self.tick())
        [work] = self.runs(row['id'])
        job = work['id']
        budget = self.service.work_budget(job)
        budget.spend_attempt(); budget.spend_turn(); budget.ledger.pause()
        with self.store.db() as db:
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (job, 'list_notes', 'succeeded', json.dumps({'evidence': {'count': 2}}), time.time()))
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (job, 'list_notes', 'failed', json.dumps({'code': 'attempt_budget', 'ran': False, 'effect': 'none'}), time.time()))
            db.execute("UPDATE jobs SET status='partial',response=?,delivery='none' WHERE id=?", ('Two of three found.', job))
        run = WorkRun(self.store.job(job), time.time() - 5)
        run.outcome = 'partial'
        return row, job, run

    def test_a_budget_stopped_scheduled_run_settles_on_the_next_tick(self):
        row, job, run = self.stopped_run()
        self.assertFalse(self.service.offer_budget_continue(run), 'no one asked to continue a scheduled run')
        self.assertFalse(self.service.work_has_pending_resume(job))
        self.tick()
        settled = self.service.preparations.get(row['id'])
        self.assertEqual(settled['state'], prep.STATE_SCHEDULED, 'the next daily slot is scheduled')
        self.assertEqual(settled['last_outcome'], prep.run_outcome(prep.KIND_PREPARE, self.store.job(job)))
        self.assertEqual(settled['prepared_text'], 'Two of three found.')

    def test_an_offer_made_before_the_fix_no_longer_holds_the_row(self):
        row, job, _ = self.stopped_run()
        # The live shape: an offer recorded under the earlier rule, current in every other respect.
        with self.store.db() as db:
            root = WorkLedger.request_chain(db, job)[-1]['id']
        key = WORK_LEDGER_KEY + ':' + root
        current = self.store.job(job)
        self.store.put('budget_continue:' + job, {
            'state': 'offered', 'nonce': 'n' * 32, 'deadline': time.time() + 3600, 'ledger_key': key,
            'epoch': WorkLedger._validate(self.store.config(key))['epoch'],
            'generation': self.service._owner_generation(self.service.issuer_owner_id(current)),
            'text': 'Continue?', 'progress': ''})
        offer = self.service.budget_continue_offer(job)
        self.assertFalse(self.service.budget_continue_current(current, offer), 'no Continue button on a scheduled run')
        self.assertFalse(self.service.work_has_pending_resume(job))
        self.tick()
        self.assertNotEqual(self.service.preparations.get(row['id'])['state'], prep.STATE_RUNNING)
