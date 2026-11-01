"""#530: the first-conversations method Still publishes as a reference skill.

Evidence class: content checks of the skill folder and its listings.  No live
model is used, so nothing here shows how a live model applies the method;
the offline role-play evaluation behind its wording is recorded on #530.

Reused, not repeated here: install pinning, licence and containment
(``test_skill_supply``); the reference-skill settings flow (``test_skill_shop``).
"""
import unittest
from pathlib import Path

from personal_agent.skills import inspect_skill, parse_frontmatter, reference_skills, usage_header

ROOT = Path(__file__).resolve().parents[1]
NAME = 'first-conversations'
FOLDER = ROOT / 'skills' / NAME


class FirstConversationsContent(unittest.TestCase):
    def setUp(self):
        self.text = (FOLDER / 'SKILL.md').read_text()

    def test_it_is_supported_content_listed_for_the_owner_to_add(self):
        record = inspect_skill(FOLDER)
        self.assertEqual((record['status'], record['licence']), (['supported_as_is'], 'AGPL-3.0-only'))
        listed = {row['name']: row for row in reference_skills()}
        self.assertEqual(listed[NAME]['address'], f'github.com/Jongtae/Still/tree/main/skills/{NAME}')
        guide = (ROOT / 'src' / 'personal_agent' / 'bundled_skills' / 'agentos-skills' / 'SKILL.md').read_text()
        self.assertIn(f'https://github.com/Jongtae/Still/tree/main/skills/{NAME}', guide)
        self.assertIn(f'`{NAME}`', (ROOT / 'skills' / 'README.md').read_text())

    def test_it_opens_with_a_bounded_usage_header(self):
        _frontmatter, body = parse_frontmatter(self.text)
        header, cut = usage_header(body)
        self.assertFalse(cut, 'the whole usage header fits the header read')
        self.assertIn('Use this', header)
        self.assertIn('Do not use it', header)

    def test_it_keeps_the_memory_and_place_rules(self):
        """Stated lasting facts are saved under #918; places are never guessed or saved from inference."""
        for phrase in ('save_memory', 'do not ask permission first', "Do not save a place you only inferred",
                       "Never infer a place from the owner's language, timezone or name",
                       'A stale position is not a current one', 'is a trip, not a new home', 'Say only what the lookup returned'):
            self.assertIn(phrase, self.text)

    def test_it_bounds_questions_and_accepts_a_decline(self):
        for phrase in ('One question per reply, one per task', 'do not ask again or get it another way',
                       'ask nothing optional', 'Use mode `ask` when you only ask'):
            self.assertIn(phrase, self.text)


if __name__ == '__main__':
    unittest.main()
