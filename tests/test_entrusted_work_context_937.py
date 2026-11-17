"""#937: the index of the owner's active entrusted lines reaches every route of a turn the owner started.

Evidence class: model-free unit and fixture-transport tests.
"""
import json
import sys
import unittest
from pathlib import Path

from personal_agent import entrusted_lines as el
from personal_agent import orchestrator as o
from personal_agent.agent_runtime import CURRENT_CONTEXT_FACT_CHARS, owner_context_fact, split_entrusted

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_entrusted_lines_798 import TASK, _LineCase  # noqa: E402
from test_preparations import SECRET  # noqa: E402

CATALOGUE = json.loads((Path(__file__).resolve().parents[1] / 'evals/judgments/cases-v1.json').read_text())['catalogue']


def brief(task, state=el.STATE_ACTIVE, open_matters=''):
    return {'task': task, 'state': state, 'open_matters': open_matters}


class HeldTextTests(unittest.TestCase):
    def test_names_active_lines_by_task_only_and_leaves_stopped_out(self):
        # #798 "Answers": only each active line's id and task sentence; the brief is never injected.
        text = el.held_text([brief('Run the small stock budget', open_matters='1 share held, no exit watch'),
                             brief('Old watch', state=el.STATE_STOPPED), brief('Track deliveries')])
        self.assertTrue(text.startswith(el.HELD_PREFIX))
        self.assertIn('1) Run the small stock budget', text)
        self.assertIn('2) Track deliveries', text)
        self.assertNotIn('1 share held', text)
        self.assertIn('entrusted_line action=read', text)
        self.assertNotIn('Old watch', text)
        self.assertNotIn('\n', text)

    def test_empty_without_active_lines_and_bounded_with_many(self):
        self.assertEqual(el.held_text([brief('Old', state=el.STATE_STOPPED)]), '')
        many = el.held_text([brief('x' * 400, open_matters='y' * 400) for _ in range(9)])
        self.assertLessEqual(len(many), el.HELD_CHARS)
        self.assertNotIn(f'{el.INDEX_ITEMS + 1})', many)


    def test_redaction_runs_on_the_whole_field_before_the_cut(self):
        secret = 'Zq8vR2mXw4Lp9tNc7bYk'
        task = 'f' * (el.INDEX_TASK_CHARS - 15) + ' ' + secret
        cut_after = el.held_text([brief(task)])
        self.assertIn(secret[:8], cut_after)  # the unredacted cut keeps a fragment: the hazard
        text = el.held_text([brief(task)], lambda value: value.replace(secret, '[secret]'))
        for size in range(6, len(secret) + 1):
            self.assertNotIn(secret[:size], text)


class FactTests(unittest.TestCase):
    HELD = el.held_text([brief('Run the small stock budget')])

    def test_owner_context_keeps_the_line_whole_after_a_long_snapshot(self):
        current = '{"snapshot": "' + 'z' * (CURRENT_CONTEXT_FACT_CHARS * 2) + '"}\n' + self.HELD
        fact = owner_context_fact({'profile': 'p', 'current_context': current}, lambda text: text)
        self.assertIn('entrusted_work: ' + self.HELD, fact)
        self.assertNotIn(el.HELD_PREFIX, fact.split('entrusted_work: ')[0])
        self.assertIn('entrusted_work: none', owner_context_fact({'current_context': 'now'}, lambda text: text))

    def test_split_returns_the_rest_unchanged(self):
        self.assertEqual(split_entrusted('a\n' + self.HELD + '\nb'), ('a\nb', self.HELD))
        self.assertEqual(split_entrusted('a\nb'), ('a\nb', ''))

    def test_plan_reads_it_as_its_own_fact(self):
        catalogue = o.Catalogue(**CATALOGUE)
        orch = o.Orchestration(None, catalogue, request='that thing went way up',
                               sections={'current_context': 'Now: Tue 09:24\n' + self.HELD})
        facts = orch._plan_context(catalogue.available()).facts
        self.assertEqual(facts['entrusted_work'], self.HELD)
        self.assertNotIn(el.HELD_PREFIX, facts['current_context'])
        self.assertEqual(o.Orchestration(None, catalogue, request='hi')._plan_context(catalogue.available())
                         .facts['entrusted_work'], 'none')


class AuditTests(unittest.TestCase):
    def test_the_lines_a_turn_carried_are_recorded_for_the_information_use_audit(self):
        from personal_agent.information_use import context_claims, section_references
        held = el.held_text([{'line_id': 'abc123', 'task': 'Run the small stock budget', 'state': el.STATE_ACTIVE,
                              'open_matters': '1 share held'},
                             {'line_id': 'def456', 'task': 'Track deliveries', 'state': el.STATE_ACTIVE}])
        self.assertIn('[line:abc123] Run the small stock budget;', held)
        self.assertNotIn('1 share held', held)
        claims = context_claims('Now: Tue 09:24\n' + held)
        self.assertEqual([c['ref'] for c in claims], ['line:abc123', 'line:def456'])
        self.assertEqual(claims[0]['label'], '맡은 일: Run the small stock budget')
        self.assertEqual(section_references(current_context=held)['current_context'], claims)
        self.assertEqual(context_claims('Now: Tue 09:24'), [])


class TurnContextTests(_LineCase):
    def test_a_stored_secret_in_a_brief_is_redacted_before_any_cut(self):
        # The fixture's stored Telegram token, placed so the task cut would split it.
        secret = SECRET
        brief_ = {'line_id': 'l1', 'state': el.STATE_ACTIVE, 'open_matters': '',
                  'task': 'f' * (el.INDEX_TASK_CHARS - 15) + ' ' + secret}
        self.service.lines.lines = lambda: [brief_]
        self.service.preparations_service.waiting_line = lambda brief: False
        text = self.service.preparations_service.lines_index({'id': 'probe'})
        self.assertTrue(text.startswith(el.HELD_PREFIX))
        for size in range(6, len(secret) + 1):
            self.assertNotIn(secret[:size], text)

    def test_a_later_message_carries_the_open_line_and_a_stopped_line_is_gone(self):
        line, _ = self.open_line(schedule=False)
        text = self.service.current_context_text({'id': 'probe'})
        self.assertIn(el.HELD_PREFIX, text)
        self.assertIn(el.excerpt(' '.join(TASK.split()), el.INDEX_TASK_CHARS), text)
        # One source: the index is no longer repeated in the prepared section.
        self.assertNotIn(line['line_id'], self.service.prepared_text({'id': 'probe'}) or '')
        self.assertIn(f"[line:{line['line_id']}]", text)
        from personal_agent.information_use import context_claims
        self.assertIn(f"line:{line['line_id']}", [claim['ref'] for claim in context_claims(text)])
        self.service.lines.write(line['line_id'], None, line['version'], 'stopped',
                                 lambda current: {**current, 'state': el.STATE_STOPPED})
        self.assertNotIn(el.HELD_PREFIX, self.service.current_context_text({'id': 'probe'}) or '')

    def test_a_run_no_owner_is_present_for_carries_no_index(self):
        self.open_line(schedule=False)
        self.service.unattended_work = lambda job: True
        self.assertNotIn(el.HELD_PREFIX, self.service.current_context_text({'id': 'probe'}) or '')


if __name__ == '__main__':
    unittest.main()
