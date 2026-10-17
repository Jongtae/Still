"""HARDEN-GENERIC-01 (#1445): an owner-completed prerequisite renews a paused Work's allowance, bounded."""
import pathlib
import tempfile
import unittest

from personal_agent.agent_runtime import (MAX_OWNER_RENEWALS, WORK_LEDGER_KEY, WorkBudget, WorkLedger,
                                          renew_for_owner_prerequisite)
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

    def test_a_paused_spent_work_is_renewed_at_most_the_limit(self):
        self.spend_and_pause()
        self.assertEqual(self.row()['turns'], WorkBudget().turns)
        for count in range(1, MAX_OWNER_RENEWALS + 1):
            self.assertTrue(self.renew())
            self.assertEqual((self.row()['turns'], self.row()['renewals']), (0, count))
        self.assertFalse(self.renew(), 'the renewal itself is bounded per Work')

    def test_an_active_run_or_a_missing_budget_is_never_renewed(self):
        self.assertFalse(self.renew(), 'no budget row yet')
        WorkLedger(self.store, self.job, fresh=True)  # active run
        self.assertFalse(self.renew())


if __name__ == '__main__':
    unittest.main()
