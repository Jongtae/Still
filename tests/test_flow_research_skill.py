"""#1332: the rule-driven flow research method Still publishes as a reference skill.

Evidence class: content checks of the skill folder, and a model-free
install/load/disable/remove run of ``SkillLibrary`` over a fake GitHub
transport serving this repository's own folder.  No live model, broker,
market data or network is used, so nothing here shows how a live model
applies the method or that any strategy works.

Reused, not repeated here: install pinning, licence and containment
(``test_skill_supply``); listing and pins (``test_skill_auto_adopt_1291``,
once the skill is listed after merge).
"""
import re
import tempfile
import unittest
from pathlib import Path

from personal_agent.agent_runtime import Capabilities, ToolError
from personal_agent.plugins import PluginRegistry
from personal_agent.quickstart_store import QuickStore
from personal_agent.skills import SkillLibrary, inspect_skill, parse_frontmatter, usage_header
from test_agency_loop import CFG
from test_no_scenario_code import SITE_TOKENS
from test_skill_shop import COMMIT, REPO, archive

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src' / 'personal_agent'
NAME = 'rule-driven-flow-research'
FOLDER = ROOT / 'skills' / NAME
SKILL = f'{NAME}/{NAME}'
#: Markets, venues and index providers a market-specific script would name (C16: a method names none).
MARKET_TOKENS = ('s&p', 'kospi', 'kosdaq', 'msci', 'ftse', 'russell', 'nasdaq', 'nyse', 'krx', 'dow jones',
                 'nikkei', 'stoxx', 'cboe', 'cme', 'proshares', 'direxion', 'kodex', 'tiger')


def everything():
    return '\n'.join(path.read_text() for path in sorted(FOLDER.rglob('*.md')))


class GitHub:
    def __init__(self):
        self.data = archive({f'skills/{NAME}': FOLDER})

    def __call__(self, url, limit=None, timeout=None):
        return COMMIT.encode() if url.startswith('https://api.github.com/') else self.data


class FlowResearchContent(unittest.TestCase):
    def setUp(self):
        self.text = (FOLDER / 'SKILL.md').read_text()

    def test_it_is_supported_versioned_content_in_the_catalogue(self):
        record = inspect_skill(FOLDER)
        self.assertEqual((record['status'], record['licence']), (['supported_as_is'], 'AGPL-3.0-only'))
        frontmatter, _body = parse_frontmatter(self.text)
        self.assertEqual(frontmatter['metadata']['version'], '1')
        self.assertIn(f'`{NAME}`', (ROOT / 'skills' / 'README.md').read_text())

    def test_it_opens_with_a_bounded_header_that_disclaims_advice_and_authority(self):
        _frontmatter, body = parse_frontmatter(self.text)
        header, cut = usage_header(body)
        self.assertFalse(cut, 'the whole usage header fits the header read')
        for phrase in ('Use this', 'Do not use it', 'never names an asset', 'It also grants nothing',
                       "owner's approval, or the owner's standing autonomy setting"):
            self.assertIn(phrase, header)

    def test_it_teaches_point_in_time_residual_estimates_and_an_already_priced_check(self):
        for phrase in ('Use only what was known at the time', 'its public time is before the decision time',
                       '**Gross**', '**Net**', '**Residual**', 'Do not fill an unknown with zero',
                       '**predicted** traded value', 'never from the volume that later printed',
                       'do not trade: record the event as observed'):
            self.assertIn(phrase, self.text)

    def test_it_keeps_the_hypotheses_apart_and_tests_against_baselines_and_look_ahead(self):
        self.assertIn('do not cancel each other', self.text)
        self.assertIn('Its data-limited mode does not apply here', self.text)
        for phrase in ('do not switch between them after seeing the result', 'treat "found nothing" as unknown',
                       '**Baselines**', '**one** event, not several', '**Every trial recorded**',
                       '**Language-model look-ahead**', 'is not executable',
                       'never a change to the rule that runs', '`trading-costs`'):
            self.assertIn(phrase, self.text)

    def test_the_references_carry_the_formulas_and_their_caveats(self):
        lev = (FOLDER / 'references' / 'leveraged-fund-rebalancing.md').read_text()
        self.assertIn('Q ≈ A × L × (L − 1) × r', lev)
        self.assertIn('Inverse funds therefore add to the same direction', lev)
        self.assertIn('**Creations and redemptions.**', lev)
        self.assertIn('Do not read the gross figure as share orders at the close', lev)
        index = (FOLDER / 'references' / 'index-changes.md').read_text()
        self.assertIn('new target weight_f − current weight_f', index)
        self.assertIn('**at the same prices**', index)
        self.assertIn('has nothing to trade from drift alone', index)
        self.assertIn('Counting benchmarked money as forced', index)

    def test_it_forbids_manipulation_and_names_no_site_market_or_skill_in_core(self):
        self.assertIn('Never place an order that tries to move a price', self.text)
        self.assertIn('never trade on non-public information', self.text)
        tokens = re.compile(r'(?<![a-z0-9])(' + '|'.join(map(re.escape, SITE_TOKENS + MARKET_TOKENS)) + r')(?![a-z0-9])')
        self.assertIsNone(tokens.search(everything().lower()))
        for module in SRC.rglob('*.py'):
            self.assertNotIn(NAME, module.read_text(), f'{module.name} names the {NAME} skill')


class FlowResearchLifecycle(unittest.TestCase):
    """Loadable only through the model's own ``skill_load``, and off or gone when the owner says so."""

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

    def test_it_loads_through_skill_load_with_its_references(self):
        binding = self.library.binding()
        self.assertIn(SKILL, binding.entries)
        self.assertEqual(binding.loaded, {}, 'nothing is loaded until the model asks')
        caps = self.caps()
        loaded = caps.execute('skill_load', {'skill': SKILL})
        self.assertIn('## 4. Estimate gross, then net, then what is still ahead', loaded['instructions'])
        resource = caps.execute('skill_resource', {'skill': SKILL, 'path': 'references/index-changes.md'})
        self.assertIn('at the same prices', str(resource))

    def test_disabling_or_removing_it_refuses_its_load(self):
        caps = self.caps()
        PluginRegistry(self.store.root).set_enabled(NAME, False)
        self.assertNotIn(SKILL, self.library.binding().entries)
        with self.assertRaises(ToolError) as raised:
            caps.execute('skill_load', {'skill': SKILL})
        self.assertIn(raised.exception.code, {'skill_revoked', 'skill_unavailable'})
        self.library.remove(NAME)
        self.assertEqual(self.library.installed(), [])
        with self.assertRaises(ToolError):
            self.caps().execute('skill_load', {'skill': SKILL})


if __name__ == '__main__':
    unittest.main()
