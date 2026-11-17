"""#937: the owner's active entrusted lines reach every route of a conversational turn.

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

CATALOGUE = json.loads((Path(__file__).resolve().parents[1] / 'evals/judgments/cases-v1.json').read_text())['catalogue']


def brief(task, state=el.STATE_ACTIVE, open_matters=''):
    return {'task': task, 'state': state, 'open_matters': open_matters}


class HeldTextTests(unittest.TestCase):
    def test_names_active_lines_with_open_matters_and_leaves_stopped_out(self):
        text = el.held_text([brief('Run the small stock budget', open_matters='1 share held, no exit watch'),
                             brief('Old watch', state=el.STATE_STOPPED), brief('Track deliveries')])
        self.assertTrue(text.startswith(el.HELD_PREFIX))
        self.assertIn('1) Run the small stock budget (open: 1 share held, no exit watch)', text)
        self.assertIn('2) Track deliveries', text)
        self.assertNotIn('Old watch', text)
        self.assertNotIn('\n', text)

    def test_empty_without_active_lines_and_bounded_with_many(self):
        self.assertEqual(el.held_text([brief('Old', state=el.STATE_STOPPED)]), '')
        many = el.held_text([brief('x' * 400, open_matters='y' * 400) for _ in range(9)])
        self.assertLessEqual(len(many), el.HELD_CHARS)
        self.assertNotIn(f'{el.HELD_LIMIT + 1})', many)


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


class TurnContextTests(_LineCase):
    def test_a_later_message_carries_the_open_line_and_a_stopped_line_is_gone(self):
        line, _ = self.open_line(schedule=False)
        text = self.service.current_context_text({'id': 'probe'})
        self.assertIn(el.HELD_PREFIX, text)
        self.assertIn(' '.join(TASK.split())[:el.HELD_TASK_CHARS], text)
        self.service.lines.write(line['line_id'], None, line['version'], 'stopped',
                                 lambda current: {**current, 'state': el.STATE_STOPPED})
        self.assertNotIn(el.HELD_PREFIX, self.service.current_context_text({'id': 'probe'}) or '')


if __name__ == '__main__':
    unittest.main()
