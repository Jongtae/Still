"""#410: the plan reads what each effort level has cost a worker turn here, as data (C16)."""
import json
import tempfile
import time
import unittest
from pathlib import Path

from personal_agent.quickstart_service import AgentService, ORCHESTRATION_EVENT
from personal_agent.quickstart_store import QuickStore


class EffortTiming(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = QuickStore(Path(tmp.name) / 'data')
        self.service = AgentService(self.store)

    def turn(self, effort, seconds, *, ago=3600, status='succeeded'):
        job = self.store.enqueue('x', f'k-{time.time_ns()}')
        start = time.time() - ago
        with self.store.db() as db:
            for tool, state, detail, at in ((ORCHESTRATION_EVENT, 'planned', {'effort': effort}, start - 1),
                                            ('subscription_engine', 'running', {'engine': 'codex'}, start),
                                            ('subscription_engine', status, {'engine': 'codex'}, start + seconds)):
                db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                           (job, tool, state, json.dumps(detail), at))

    def test_medians_per_level_in_effort_order_with_counts(self):
        for seconds in (100, 120, 140):
            self.turn('high', seconds)
        for seconds in (10, 12, 14):
            self.turn('low', seconds)
        for seconds in (20, 30, 40):
            self.turn('', seconds, status='failed')
        text = self.service.effort_timing_text()
        self.assertIn('default 30 s (3 turns), low 12 s (3 turns), high 120 s (3 turns)', text)

    def test_rare_old_and_absent_levels_are_left_out(self):
        self.turn('xhigh', 300)
        self.turn('xhigh', 320)
        for _ in range(3):
            self.turn('medium', 45, ago=30 * 86400)
        self.assertEqual(self.service.effort_timing_text(), '')

    def test_the_text_is_kept_for_its_window_so_both_plans_read_the_same(self):
        for seconds in (10, 12, 14):
            self.turn('low', seconds)
        first = self.service.effort_timing_text()
        for seconds in (500, 600, 700):
            self.turn('max', seconds)
        self.assertEqual(self.service.effort_timing_text(), first)

    def test_the_plan_facts_carry_it(self):
        from personal_agent.orchestrator import Orchestration
        orchestration = Orchestration(None, type('Catalogue', (), {'descriptions': {}})(), request='hi',
                                      effort_timing='median worker-turn time ...: low 12 s (3 turns)')
        facts = orchestration._plan_context([]).facts
        self.assertEqual(facts['effort_timing'], 'median worker-turn time ...: low 12 s (3 turns)')
        self.assertEqual(Orchestration(None, type('Catalogue', (), {'descriptions': {}})(), request='hi')
                         ._plan_context([]).facts['effort_timing'], 'none recorded')


if __name__ == '__main__':
    unittest.main()
