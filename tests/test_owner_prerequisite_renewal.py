"""HARDEN-GENERIC-01 (#1445): an owner-completed prerequisite renews a paused Work's allowance, bounded.

#627: the bound is the request's one continuation count (``MAX_CONTINUATIONS``), not a separate renewal cap.
"""
import json
import pathlib
import tempfile
import time
import unittest

from personal_agent.agent_runtime import (MAX_CONTINUATIONS, WORK_LEDGER_KEY, WorkBudget, WorkLedger,
                                          continuation_count, renew_for_owner_prerequisite)
from personal_agent.quickstart_store import QuickStore


class OwnerPrerequisiteRenewal(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = QuickStore(str(pathlib.Path(temp.name) / 'data'))
        self.job = self.store.enqueue('a request that needed a setup', 'renewal')
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (self.job,))

    def spend_and_pause(self):
        ledger = WorkLedger(self.store, self.job, fresh=True)
        budget = WorkBudget(ledger=ledger)
        for _ in range(budget.turns - 1):
            budget.spend_turn()
        budget.spend_turn(reserve=True)  # #1418: the last turn is the outcome judgment's
        ledger.pause()
        return ledger

    def row(self):
        return dict(self.store.config(f'{WORK_LEDGER_KEY}:{self.job}'))

    def renew(self):
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            return renew_for_owner_prerequisite(db, self.job)

    def test_a_paused_spent_work_is_renewed_and_no_separate_cap_is_kept(self):
        self.spend_and_pause()
        # A pre-#627 row at the old three-renewal cap: the one continuation count bounds it now.
        self.store.put(f'{WORK_LEDGER_KEY}:{self.job}', {**self.row(), 'renewals': 3})
        self.assertEqual(self.row()['turns'], WorkBudget().turns)
        self.assertTrue(self.renew())
        self.assertEqual(self.row()['turns'], 0)
        self.assertNotIn('renewals', self.row(), 'no second counter beside the continuation count')

    def test_an_active_run_or_a_missing_budget_is_never_renewed(self):
        self.assertFalse(self.renew(), 'no budget row yet')
        WorkLedger(self.store, self.job, fresh=True)  # active run
        self.assertFalse(self.renew())

    def test_the_continuation_count_reads_the_queued_receipts_of_the_whole_request(self):
        def receipt(job, tool, status):
            with self.store.db() as db:
                db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                           (job, tool, status, json.dumps({'resumed': status == 'queued'}), time.time()))
        receipt(self.job, 'settings_continuation', 'queued')
        receipt(self.job, 'action_continuation', 'queued')
        receipt(self.job, 'action_continuation', 'withheld')  # withheld: not a continuation
        retry = self.store.enqueue('again', 'renewal-retry')
        with self.store.db() as db:
            db.execute("UPDATE jobs SET relation_kind='retry',related_job_id=? WHERE id=?", (self.job, retry))
        receipt(retry, 'action_continuation', 'queued')
        with self.store.db() as db:
            self.assertEqual(continuation_count(db, self.job), 2)
            self.assertEqual(continuation_count(db, retry), 3, 'a retry shares its request\'s one count')
        self.assertGreater(MAX_CONTINUATIONS, 0)


if __name__ == '__main__':
    unittest.main()
