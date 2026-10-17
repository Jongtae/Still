"""#1508: model-facing prompt text lives in shipped ``prompts/*.md`` files."""
import re
import tomllib
import unittest
from pathlib import Path

from personal_agent import prompt_files

SRC = Path(prompt_files.__file__).parent
ROOT = SRC.parent.parent
LOADED = re.compile(r"\bprompt\('([a-z_]+)'\)")


class PromptFileTests(unittest.TestCase):
    def test_every_prompt_file_is_loaded_and_every_load_has_a_file(self):
        loaded = set()
        for module in SRC.glob('*.py'):
            if module.name != 'prompt_files.py':
                loaded |= set(LOADED.findall(module.read_text(encoding='utf-8')))
        files = {path.stem for path in prompt_files.PROMPTS.glob('*.md')}
        self.assertTrue(files)
        self.assertEqual(files, loaded)

    def test_prompt_files_ship_with_the_package(self):
        data = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
        self.assertIn('prompts/*.md', data['tool']['setuptools']['package-data']['personal_agent'])

    def test_the_closing_newline_is_not_part_of_the_prompt(self):
        for path in prompt_files.PROMPTS.glob('*.md'):
            with self.subTest(prompt=path.stem):
                raw = path.read_text(encoding='utf-8')
                self.assertTrue(raw.endswith('\n') and not raw.endswith('\n\n'))
                self.assertEqual(prompt_files.prompt(path.stem), raw[:-1])


class ConsolidatedPromptTests(unittest.TestCase):
    """#1508 step 2: one priority order, no stale or contradicting rule, one home per method."""

    def test_worker_and_planner_state_one_priority_order_in_sections(self):
        from personal_agent.agent_runtime import POLICY
        from personal_agent.orchestrator import QUESTION
        for text in (POLICY, QUESTION):
            self.assertEqual(text.count('## Priorities'), 1)
            self.assertIn('the earlier one wins', text)
        # A CLI prompt nests the worker instructions under "# Still instructions".
        self.assertNotRegex(POLICY, r'(?m)^# ')

    def test_the_removed_and_contradicting_rules_are_gone(self):
        from personal_agent.agent_runtime import API_TOOL_GUIDANCE, DEFINITIONS, POLICY
        # #826 removed the separation of file contents from web search.
        self.assertNotIn('Do not transmit file contents', POLICY)
        # #918: an inference is saved as what was inferred, as the save_memory description says.
        self.assertNotIn('never save an inference as a fact', API_TOOL_GUIDANCE)
        [save_memory] = [t['function']['description'] for t in DEFINITIONS if t['function']['name'] == 'save_memory']
        self.assertIn('as what you inferred', save_memory)
        # #918 act-then-tell: look up first; ask only what no tool can find, in one answer.
        self.assertNotIn('Ask a concise question if required context is missing', POLICY)
        self.assertIn('a value no tool can find', POLICY)

    def test_the_route_collection_method_is_stated_once(self):
        from personal_agent.agent_runtime import GOAL_FIRST_GUIDANCE, POLICY
        from personal_agent.orchestrator import QUESTION
        self.assertIn('the official Registry', GOAL_FIRST_GUIDANCE)
        self.assertEqual(POLICY.count('the official Registry'), 1)
        self.assertNotIn('Registry', QUESTION)
        self.assertIn("the worker's own instructions describe how", QUESTION)

    def test_a_change_step_is_kept_in_the_library_without_carrying_approval(self):
        # #1510 (a): the library is for every step that worked, a change as well as a read.
        from personal_agent.agent_runtime import GOAL_FIRST_GUIDANCE
        self.assertIn('a change as well as a read', GOAL_FIRST_GUIDANCE)
        self.assertIn("for a change in the run that asks the owner's approval", GOAL_FIRST_GUIDANCE)
        self.assertIn('A saved function never carries an approval', GOAL_FIRST_GUIDANCE)


if __name__ == '__main__':
    unittest.main()
