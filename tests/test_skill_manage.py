"""Bundled management skill content and settings tool availability."""
import tempfile
import unittest
from pathlib import Path

from personal_agent.agent_runtime import SETTINGS_ACTIONS, SKILL_ACTIONS, Capabilities
from personal_agent.quickstart_store import QuickStore
from personal_agent.settings_orchestrator import SettingsOrchestrator
from personal_agent.skills import BUNDLED_ROOT, SkillLibrary, inspect_skill
from test_agency_loop import CFG

SKILL = 'agentos/agentos-management'
FOLDER = BUNDLED_ROOT / 'agentos-management'


class _Service(unittest.TestCase):
    def setUp(self):
        from personal_agent.quickstart_service import AgentService
        self.folder = tempfile.TemporaryDirectory()
        self.store = QuickStore(Path(self.folder.name) / 'data')
        self.service = AgentService(self.store)
        self.settings = SettingsOrchestrator(self.store, service=self.service)

    def tearDown(self):
        self.folder.cleanup()


class TheSkillIsBundledContent(_Service):
    def test_it_is_a_supported_bundled_skill_with_its_references(self):
        record = inspect_skill(FOLDER)
        self.assertEqual(record['status'], ['supported_as_is'])
        self.assertEqual(sorted(record['files']), ['SKILL.md', 'references/main-ai.md'])
        library = SkillLibrary(self.store)
        library.set_enabled(True)
        binding = library.binding()
        self.assertIn(SKILL, binding.entries)
        loaded = binding.load(SKILL)
        self.assertEqual(loaded['resources'], ['references/main-ai.md'])
        for reference in loaded['resources']:
            self.assertTrue(binding.resource(SKILL, reference)['content'])

    def test_a_new_name_is_the_owners_words_not_a_listed_value(self):
        """#973 review: creating something new must not be blocked by the "only listed values" rule."""
        text = (FOLDER / 'SKILL.md').read_text()
        self.assertIn('When the change creates something new, the value is not listed', text)

    def test_it_points_only_at_the_existing_settings_tools(self):
        text = '\n'.join(path.read_text() for path in sorted(FOLDER.rglob('*.md')))
        for tool in ('settings_read', 'settings_change'):
            self.assertIn(tool, text)
        for setting in ('main_ai', 'skills', 'route', 'model'):
            self.assertIn(setting, text)
        # Know-how, not authority: no credential handling, shell or file editing instructions.
        for forbidden in ('launchctl', 'sqlite', '.json', 'password:', 'token='):
            self.assertNotIn(forbidden, text)


class ManagementWorksWithoutTheSkill(_Service):
    def test_skills_off_keep_the_settings_tools(self):
        caps = Capabilities(self.store, None, CFG, '', 'job', lambda *a: None, settings=lambda *a: {'state': 'read'})
        offered = {d['function']['name'] for d in caps.definitions()}
        self.assertTrue(SETTINGS_ACTIONS <= offered)
        self.assertFalse(SKILL_ACTIONS & offered)
        draft = self.settings.propose('owner', 'web', 'current_context', 'enabled', 'on')
        self.assertEqual(draft['state'], 'awaiting-confirmation')


if __name__ == '__main__':
    unittest.main()
