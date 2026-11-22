"""#1099: the 2026-11-17 communication sweep's two findings, held by general rules.

The findings were a manager report that mixed two registers, and a field term defined more broadly
than the field's own definition, with its source named but not linked. These tests check the
properties the rules must carry, not one wording: the rule for text written to another reader keeps
that reader's register through the whole text; the field-term rule bounds a plain gloss by the
field's definition and links a named source; the rules name nothing from the sampled cases (C16);
and the judge rubric that grades the sweep covers both properties.

Evidence class: static prompt and rubric checks. Real-model re-sampling on a fresh seed needs an
owner budget and is not part of this test.
"""
import json
import re
import sys
import unittest
from pathlib import Path

from personal_agent import prompt_files

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evals'))
SAMPLED = ('communication-weekly-status-report', 'communication-field-term-plain-words')


def bullets(text):
    return [line[2:] for line in text.splitlines() if line.startswith('- ')]


def sentences(bullet):
    return [part for part in re.split(r'(?<=[.])\s+', bullet) if part]


class RulesCarryTheProperties(unittest.TestCase):
    def setUp(self):
        self.voice = prompt_files.prompt('voice')

    def test_text_for_another_reader_keeps_one_register_to_its_end(self):
        """The rule for another reader's register must also bound its extent (the mixed report)."""
        rule = [s for b in bullets(self.voice) for s in sentences(b)
                if re.search(r'(?i)someone other than the owner', s)]
        self.assertTrue(rule, 'a rule names text written for another reader')
        joined = ' '.join(rule).casefold()
        self.assertIn('register', joined)
        self.assertRegex(joined, r'whole text|throughout|from start to end|every part')
        # The owner's voice still stays separate from the other reader's register.
        self.assertRegex(joined, r'voice .*owner')

    def test_a_field_term_gloss_is_bounded_by_the_fields_definition_and_a_named_source_is_linked(self):
        rule = ' '.join(s for b in bullets(self.voice) for s in sentences(b)
                        if re.search(r'(?i)field term|definition', s)).casefold()
        self.assertIn('plain words', rule)
        self.assertIn('definition', rule)
        self.assertRegex(rule, r'widen|broader|wider')
        self.assertIn('link', rule)

    def test_the_rules_name_nothing_from_the_sampled_cases(self):
        """C16 / #940: the fix is a general rule, never the sampled request's words."""
        from test_no_scenario_code import scenario_tokens
        self.assertEqual(scenario_tokens(self.voice), [])
        cases = {row['id']: row for row in json.loads((ROOT / 'evals/scenarios/communication.json').read_text())
                 if isinstance(row, dict) and row.get('id') in SAMPLED}
        self.assertEqual(set(cases), set(SAMPLED))
        words = {word for row in cases.values() for turn in row['turns']
                 for word in re.findall(r'[A-Za-z]{3,}|[가-힣]{2,}', turn['say'])}
        self.assertTrue(words)
        leaked = sorted(word for word in words if word in self.voice)
        self.assertEqual(leaked, [])

    def test_the_rules_reach_every_worker_once(self):
        from personal_agent.agent_runtime import CORE_INSTRUCTIONS
        self.assertEqual(CORE_INSTRUCTIONS.count(self.voice), 1)


class RubricGradesBoth(unittest.TestCase):
    def test_reader_form_grades_one_register_and_the_terms_own_meaning(self):
        from agentos_eval.scenarios import RUBRIC
        reader_form = RUBRIC['reader_form'].casefold()
        self.assertRegex(reader_form, r'one register|single register|same register')
        self.assertRegex(reader_form, r"field.s own meaning|field.s own definition")
        self.assertIn('link', reader_form)


if __name__ == '__main__':
    unittest.main()
