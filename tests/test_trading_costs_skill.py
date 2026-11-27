"""#783: the trading-costs method Still publishes as a reference skill.

Evidence class: content checks of the skill folder and its listings, and a
model-free install/disable/remove run of ``SkillLibrary`` over a fake GitHub
transport serving this repository's own folder.  No live model, broker,
account or network is used, so nothing here shows how a live model applies
the method or that any order was placed or withheld.

Reused, not repeated here: install pinning, licence and containment
(``test_skill_supply``); the reference-skill settings flow (``test_skill_shop``).
"""
import re
import tempfile
import unittest
from pathlib import Path

from personal_agent.agent_runtime import Capabilities, ToolError
from personal_agent.plugins import PluginRegistry
from personal_agent.quickstart_store import QuickStore
from personal_agent.skills import SkillLibrary, inspect_skill, parse_frontmatter, reference_skills, usage_header
from test_agency_loop import CFG
from test_no_scenario_code import SITE_TOKENS
from test_skill_shop import COMMIT, REPO, archive

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src' / 'personal_agent'
NAME = 'trading-costs'
FOLDER = ROOT / 'skills' / NAME
SKILL = f'{NAME}/{NAME}'


class GitHub:
    def __init__(self):
        self.data = archive({f'skills/{NAME}': FOLDER})

    def __call__(self, url, limit=None, timeout=None):
        return COMMIT.encode() if url.startswith('https://api.github.com/') else self.data


class TradingCostsContent(unittest.TestCase):
    def setUp(self):
        self.text = (FOLDER / 'SKILL.md').read_text()

    def test_it_is_supported_versioned_content_listed_for_the_owner_to_add(self):
        record = inspect_skill(FOLDER)
        self.assertEqual((record['status'], record['licence']), (['supported_as_is'], 'AGPL-3.0-only'))
        frontmatter, _body = parse_frontmatter(self.text)
        self.assertEqual(frontmatter['metadata']['version'], '1')
        listed = {row['name']: row for row in reference_skills()}
        # #1291: pinned to one exact commit and its content digest.
        self.assertRegex(listed[NAME]['address'], rf'^github\.com/{REPO}/tree/[0-9a-f]{{40}}/skills/{NAME}$')
        self.assertTrue(listed[NAME]['pinned'])
        self.assertIn(f'`{NAME}`', (ROOT / 'skills' / 'README.md').read_text())

    def test_it_opens_with_a_bounded_usage_header_that_disclaims_advice_and_authority(self):
        _frontmatter, body = parse_frontmatter(self.text)
        header, cut = usage_header(body)
        self.assertFalse(cut, 'the whole usage header fits the header read')
        for phrase in ('Use this', 'Do not use it', 'never names an asset', 'It also grants nothing',
                       "owner's approval, or the owner's standing autonomy setting"):
            self.assertIn(phrase, header)

    def test_it_teaches_the_cost_check_the_hold_back_and_net_reporting(self):
        for phrase in ('minimum fee', 'Do not assume it is zero', 'round-trip cost =', 'break-even move =',
                       'Selling at the same price you bought is always a loss', 'do not place the order',
                       'Then wait for their decision', '**net P&L**', 'never present gross P&L as the result'):
            self.assertIn(phrase, self.text)

    def test_it_names_no_site_provider_or_figure_and_no_core_module_names_it(self):
        tokens = re.compile(r'(?<![a-z0-9])(' + '|'.join(map(re.escape, SITE_TOKENS)) + r')')
        self.assertIsNone(tokens.search(self.text.lower()))
        self.assertIsNone(re.search(r'\d+(\.\d+)?\s?%', self.text), 'no fee or tax rate is stated as fact')
        for module in SRC.rglob('*.py'):
            self.assertNotIn(NAME, module.read_text(), f'{module.name} names the {NAME} skill')


class TradingCostsLifecycle(unittest.TestCase):
    """Listed, loadable only through the model's own ``skill_load``, and off or gone when the owner says so."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = QuickStore(Path(tmp.name) / 'data')
        PluginRegistry(self.store.root)
        self.library = SkillLibrary(self.store, transport=GitHub())
        self.library.install(f'https://github.com/{REPO}/tree/main/skills/{NAME}')
        self.library.set_enabled(True)

    def caps(self):
        return Capabilities(self.store, None, CFG, '', 'job', lambda *a: None, skills=self.library.binding())

    def test_it_is_catalogued_and_loads_through_skill_load(self):
        binding = self.library.binding()
        self.assertIn(SKILL, binding.entries)
        self.assertIn(SKILL, binding.catalogue_text())
        self.assertEqual(binding.loaded, {}, 'nothing is loaded until the model asks')
        loaded = self.caps().execute('skill_load', {'skill': SKILL})
        self.assertIn('## 2. Compute the round-trip cost', loaded['instructions'])

    def test_disabling_it_removes_it_from_the_catalogue_and_refuses_its_load(self):
        caps = self.caps()
        PluginRegistry(self.store.root).set_enabled(NAME, False)
        self.assertNotIn(SKILL, self.library.binding().entries)
        self.assertNotIn(SKILL, [entry['skill'] for entry in self.library.loadable()])
        with self.assertRaises(ToolError) as raised:
            caps.execute('skill_load', {'skill': SKILL})
        self.assertIn(raised.exception.code, {'skill_revoked', 'skill_unavailable'})

    def test_the_owner_can_remove_it_on_its_own(self):
        self.library.remove(NAME)
        self.assertEqual(self.library.installed(), [])
        self.assertNotIn(SKILL, self.library.binding().entries)
        self.assertNotIn(SKILL, [row['skill'] for row in self.library.summary()])


if __name__ == '__main__':
    unittest.main()
