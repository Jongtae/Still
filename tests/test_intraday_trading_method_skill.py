"""#1285: the intraday trading method Still publishes as a reference skill.

Evidence class: content checks of the skill folder and its listings, and a
model-free install/load/disable/remove run of ``SkillLibrary`` over a fake GitHub
transport serving this repository's own folder.  No live model, broker, account
or network is used, so nothing here shows how a live model applies the method,
that a backtest ran in ``code_run`` or that any order was placed or withheld.

Reused, not repeated here: install pinning, licence and containment
(``test_skill_supply``); the reference-skill settings flow (``test_skill_shop``);
the same lifecycle shape as ``test_trading_costs_skill``.
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
from test_no_scenario_code import CATEGORY_TOKENS, SITE_TOKENS
from test_skill_shop import COMMIT, REPO, archive

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src' / 'personal_agent'
NAME = 'intraday-trading-method'
FOLDER = ROOT / 'skills' / NAME
SKILL = f'{NAME}/{NAME}'
GATE = 'references/backtest-gate.md'


class GitHub:
    def __init__(self):
        self.data = archive({f'skills/{NAME}': FOLDER})

    def __call__(self, url, limit=None, timeout=None):
        return COMMIT.encode() if url.startswith('https://api.github.com/') else self.data


class IntradayMethodContent(unittest.TestCase):
    def setUp(self):
        self.text = (FOLDER / 'SKILL.md').read_text()
        self.gate = (FOLDER / GATE).read_text()

    def test_it_is_supported_versioned_content_listed_for_the_owner_to_add(self):
        record = inspect_skill(FOLDER)
        self.assertEqual((record['status'], record['licence']), (['supported_as_is'], 'AGPL-3.0-only'))
        self.assertEqual(sorted(record['files']), ['SKILL.md', GATE], 'text only: no scripts Still would refuse')
        frontmatter, _body = parse_frontmatter(self.text)
        self.assertEqual(frontmatter['metadata']['version'], '1')
        listed = {row['name']: row for row in reference_skills()}
        self.assertEqual(listed[NAME]['address'], f'github.com/{REPO}/tree/main/skills/{NAME}')
        self.assertIn(f'`{NAME}`', (ROOT / 'skills' / 'README.md').read_text())

    def test_its_usage_header_leaves_limits_to_the_owner_and_grants_nothing(self):
        _frontmatter, body = parse_frontmatter(self.text)
        header, cut = usage_header(body)
        self.assertFalse(cut, 'the whole usage header fits the header read')
        for phrase in ('Use this', 'Do not use it', "come from the owner's line or request, never from this skill",
                       'never an owner limit', 'It also grants nothing',
                       "owner's approval, or the owner's standing autonomy setting", 'never widens a budget'):
            self.assertIn(phrase, header)

    def test_it_teaches_the_rules_the_live_runs_lacked(self):
        for phrase in ('**Act only on closed bars.**', '**Stale data stops the run.**', '**Hysteresis:**',
                       '**Persistence:**', '**Carry it forward:**', 'limit order', 'Never use a market order to enter',
                       'VWAP + 1 × ATR(14)', '**No upward re-pricing.**', 'never use a wider stop than the owner\'s',
                       '**Time exit:**', 'quantity = floor(risk per trade ÷ (entry price − stop price))',
                       "within the owner's budget", '**Daily loss stop:**', 'observe-only',
                       'The condition check only does what the plan states', '**Finish inside the run\'s time.**',
                       'Skipped signals matter', '**checkable expectation:**', '**settle by:**',
                       '**Full gate**', '**Data-limited mode**', 'a quarter of the normal risk per trade',
                       'with every owner limit unchanged', 'the rule is data-limited', '**Let history accumulate.**',
                       'the full gate applies', '**Recorded outcomes also decide.**', 'returns to observe-only',
                       'Data-limited mode lowers risk; it never widens'):
            self.assertIn(phrase, self.text)

    def test_the_gate_pins_maintained_packages_that_fit_code_run(self):
        for pin in ('backtesting==0.6.6', 'pandas-ta-classic==0.9.0'):
            self.assertIn(pin, self.text)
            self.assertIn(pin, self.gate)
        self.assertIn('import pandas_ta_classic as ta', self.gate)
        self.assertIn('from backtesting import Backtest, Strategy', self.gate)
        for rejected in ('vectorbt', 'backtrader', 'import pandas_ta\n'):
            self.assertNotIn(rejected, self.text + self.gate)

    def test_it_names_no_site_provider_or_owner_figure_and_no_core_module_names_it(self):
        both = (self.text + self.gate).lower()
        tokens = re.compile(r'(?<![a-z0-9])(' + '|'.join(map(re.escape, SITE_TOKENS)) + r')')
        self.assertIsNone(tokens.search(both))
        for token in (t for t in CATEGORY_TOKENS if t.isascii()):
            self.assertIsNone(re.search(rf'(?<![a-z0-9]){token}', both), token)
        self.assertIsNone(re.search(r'\d+(\.\d+)?\s?%', self.text), 'no stop, loss or fee is stated as a fixed percentage')
        self.assertIsNone(re.search(r'\d[\d,]*\s?(krw|won|usd|원)', both), 'no budget amount is hard-coded')
        for module in SRC.rglob('*.py'):
            self.assertNotIn(NAME, module.read_text(), f'{module.name} names the {NAME} skill')


class IntradayMethodLifecycle(unittest.TestCase):
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

    def test_it_is_catalogued_and_loads_only_through_skill_load(self):
        binding = self.library.binding()
        self.assertIn(SKILL, binding.entries)
        self.assertIn(SKILL, binding.catalogue_text())
        self.assertEqual(binding.loaded, {}, 'nothing is loaded until the model asks')
        caps = self.caps()
        loaded = caps.execute('skill_load', {'skill': SKILL})
        self.assertIn('## 5. Entries: limit orders only, never chase', loaded['instructions'])
        resource = caps.execute('skill_resource', {'skill': SKILL, 'path': GATE})
        self.assertIn('class Pullback(Strategy)', str(resource))

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
