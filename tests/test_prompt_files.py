"""#1508: model-facing prompt text lives in shipped ``prompts/*.md`` files."""
import ast
import re
import tomllib
import unittest
from pathlib import Path

from personal_agent import prompt_files

SRC = Path(prompt_files.__file__).parent
ROOT = SRC.parent.parent
LOADED = re.compile(r"\bprompt\('([a-z_]+)'\)")


class PromptFileTests(unittest.TestCase):
    def test_judgment_constants_are_not_plain_string_literals(self):
        # No literal exceptions: capability lists are formatted into prompt files,
        # and reaction/goal questions compose loaded prompts with shared fragments.
        for module in ('conversation_handoff.py', 'decision.py'):
            tree = ast.parse((SRC / module).read_text(encoding='utf-8'))
            for node in tree.body:
                if isinstance(node, ast.Assign):
                    targets = node.targets
                elif isinstance(node, ast.AnnAssign):
                    targets = [node.target]
                else:
                    continue
                for target in targets:
                    for name in ast.walk(target):
                        if isinstance(name, ast.Name) and name.id.endswith(
                                ('_PROPOSITION', '_QUESTION', '_INSTRUCTION')):
                            with self.subTest(module=module, constant=name.id):
                                self.assertFalse(isinstance(node.value, ast.Constant)
                                                 and isinstance(node.value.value, str),
                                                 f'{name.id} must load its text from prompts/')

    def test_prompts_do_not_assign_a_secretary_role(self):
        for path in prompt_files.PROMPTS.glob('*.md'):
            with self.subTest(prompt=path.stem):
                self.assertNotRegex(path.read_text(encoding='utf-8'), r'(?i)secretary|비서')

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

    def test_ask_or_assume_is_shared_once_verbatim_in_all_three_contexts(self):
        from personal_agent.agent_runtime import CORE_INSTRUCTIONS
        from personal_agent.conversation_handoff import GOAL_REACHED_PROPOSITION
        from personal_agent.orchestrator import LOCATION_QUESTION
        fragment = prompt_files.prompt('ask_or_assume')
        for text in (CORE_INSTRUCTIONS, LOCATION_QUESTION, GOAL_REACHED_PROPOSITION):
            with self.subTest(context=text[:40]):
                self.assertEqual(text.count(fragment), 1)
                self.assertEqual(text.count('## Ask or assume'), 1)
        self.assertNotIn("Ask only for a decision that is the owner's to make or a value no tool can find",
                         CORE_INSTRUCTIONS)
        self.assertNotIn('if being wrong matters, ask once and say why', CORE_INSTRUCTIONS)
        self.assertIn('Ask or assume', prompt_files.prompt('planner_location'))

    def test_ask_statement_and_evidence_rules_name_no_scenario(self):
        from test_no_scenario_code import scenario_tokens
        fragments = [prompt_files.prompt('ask_or_assume'), prompt_files.prompt('planner_location')]
        selectors = {
            'worker_core': ('- Use the means you have',
                            '- When the owner tells you', '- Do not claim execution'),
            'planner': ('- A message that tells', '- owner_situation', '- brief.notes', '4. Fit,'),
            'goal_reached': ('- fulfillment:', '- retry_value:', 'A reply falls short when it claims',
                             'A reply falls short if it addresses', 'When the owner shares'),
            'owner_model_review': ('The assistant fell short',),
        }
        for name, prefixes in selectors.items():
            for prefix in prefixes:
                lines = [line for line in prompt_files.prompt(name).splitlines() if line.startswith(prefix)]
                self.assertEqual(len(lines), 1, (name, prefix))
                fragments.extend(lines)
        for text in fragments:
            with self.subTest(text=text[:80]):
                self.assertEqual(scenario_tokens(text), [])

    def test_failed_steps_need_another_method_not_disclosure_alone(self):
        from personal_agent.agent_runtime import CORE_INSTRUCTIONS
        from personal_agent.conversation_handoff import GOAL_REACHED_PROPOSITION
        for sentence in (
            'Disclosure makes a reply honest, not fulfilled by itself.',
            'When a lookup or tool step the answer needs failed or returned nothing usable, the reply serves only if the attempt also tried the next method a capable assistant would use with the means and budgets it had (another available source, tool or route), or observations show no other method was available and the reply says so.',
            'Otherwise return false with retry_warranted true and retry_value naming that next method (#202).',
            'Unavailable here means evidence the judgment cannot see; a recorded failed or empty result is seen, not unseen, and supports false when the result was not obtained under the fulfillment rule.',
            'An owner-requested rough estimate also serves without a lookup.',
            'Disclosure still has to meet the fulfillment rule above.',
            'This observed unmet result supports false, not null for unseen evidence, but does not itself prove a retry would help.',
        ):
            with self.subTest(sentence=sentence):
                self.assertEqual(GOAL_REACHED_PROPOSITION.count(sentence), 1)
        self.assertEqual(CORE_INSTRUCTIONS.count(
            'Use the means you have before answering: get facts with tools; if a needed step fails, try the next available method before estimating, then say what could not be checked.'), 1)

    def test_timing_uses_a_stated_assumption_and_a_question_in_one_reply(self):
        from personal_agent.agent_runtime import CORE_INSTRUCTIONS
        from personal_agent.conversation_handoff import GOAL_REACHED_PROPOSITION
        from personal_agent.orchestrator import LOCATION_QUESTION
        for text in (CORE_INSTRUCTIONS, LOCATION_QUESTION, GOAL_REACHED_PROPOSITION):
            for sentence in (
                "Ask the owner for information before answering only when the choice is the owner's, no assumption fits, or acting on a wrong guess would itself cause loss (money, an account change, something sent).",
                'When a wrong guess could cost time, answer on the stated assumption and ask the deciding question in the same reply.',
                'No assumption replaces an approval Still asks for.',
            ):
                with self.subTest(sentence=sentence):
                    self.assertEqual(text.count(sentence), 1)
        self.assertEqual(LOCATION_QUESTION.count(
            'the worker answers on that stated assumption and offers location sharing in the same reply, even when timing matters.'), 1)
        self.assertIn('Only when no such assumption fits or the request needs the exact current position', LOCATION_QUESTION)
        self.assertNotIn('a wrong guess would cost the owner', LOCATION_QUESTION)

    def test_wrong_versioned_method_is_a_developer_blocker(self):
        review = prompt_files.prompt('owner_model_review')
        self.assertEqual(review.count("When a method in a skill or the assistant's own instruction text was wrong or out of date, give the blocker and no notes: say generally which method and what it led to, so a developer can revise the versioned text; never rewrite it yourself (#202)."), 1)
        self.assertIn('not a general rule any assistant should follow', review)
        self.assertIn('The blocker is at most 200 characters.', review)

    def test_route_skill_discloses_failed_lookup_and_distance_estimate(self):
        skill = (ROOT / 'skills/map-directions/SKILL.md').read_text()
        self.assertIn('try another available route method first', skill)
        self.assertIn('say the route lookup failed and that the time is a distance-based estimate', skill)
        self.assertNotIn('Say "약",', skill)
        self.assertIn('never present them as live traffic', skill)
        self.assertIn('The map link in step 3 shows the live time.', skill)
        self.assertIn('ask about the starting place in the same reply', skill)
        self.assertIn('Do not guess silently, and do not stop to ask when a stated assumption works.', skill)
        self.assertIn('Never present an estimate as checked, or a stale position as current.', skill)

    def test_goal_reached_keeps_authority_guards_and_lookup_exception(self):
        import hashlib
        from personal_agent.conversation_handoff import GOAL_REACHED_PROPOSITION
        for guard in ('use secure input', 'recorded_effects lists the calls of this Work',
                      'awaiting the owner', 'an item is not approved or applied until a later event settles it',
                      'Never reward replay', 'unless the owner asked not to look it up'):
            with self.subTest(guard=guard):
                self.assertIn(guard, GOAL_REACHED_PROPOSITION)
        # The complete authority section stays unchanged, including qualifiers
        # that a substring-only assertion could miss.
        guards = prompt_files.prompt('goal_reached').split('Authority, failures and prerequisites:\n')[1]
        self.assertEqual(hashlib.sha256((guards + '\n').encode()).hexdigest(),
                         '3b3b18c013f6d2d00958951be30eef1ee296890759857acf3fd0209f6ec1c118')

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
        # #918/#202: use context and tools first; collect owner-only questions in one answer.
        self.assertNotIn('Ask a concise question if required context is missing', POLICY)
        self.assertIn('then what tools find', POLICY)
        self.assertIn("Ask the owner for information before answering only when the choice is the owner's", POLICY)
        self.assertIn('ask for everything in one answer', POLICY)

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

    def test_voice_preference_key_and_axes_are_explicit(self):
        from personal_agent.memory_service import PROFILE_KEY_GUIDANCE, VOICE_PREFERENCE_KEY
        voice = prompt_files.prompt('voice')
        self.assertIn(VOICE_PREFERENCE_KEY, voice)
        for axis in ('register', 'warmth', 'enthusiasm', 'length', 'emoji', 'humour', 'lists and headers', 'initiative'):
            self.assertIn(axis, voice)
        self.assertIn(VOICE_PREFERENCE_KEY, PROFILE_KEY_GUIDANCE)
        self.assertIn('never infer', PROFILE_KEY_GUIDANCE)
        self.assertIn('other facts', PROFILE_KEY_GUIDANCE)

    def test_calibration_leaves_the_cli_conversation_budget_intact(self):
        from personal_agent.agent_runtime import (CORE_INSTRUCTIONS, CLI_TOOL_GUIDANCE,
                                                  CLI_NATIVE_SEARCH_GUIDANCE, CONTEXT_BUDGET_BYTES)
        from personal_agent.memory_service import PROFILE_SNAPSHOT_BYTES, OWNER_WORDS_BYTES
        from personal_agent.owner_model import NOTES_CHARS
        # Same worst-case fixed sections as the owner-profile regression, without
        # requiring its HTTP fixture just to measure the instruction byte budget.
        fixed = (len((CORE_INSTRUCTIONS + CLI_TOOL_GUIDANCE + CLI_NATIVE_SEARCH_GUIDANCE).encode())
                 + PROFILE_SNAPSHOT_BYTES + OWNER_WORDS_BYTES + len(('가' * NOTES_CHARS + 'x' * 600).encode()))
        self.assertLess(fixed, CONTEXT_BUDGET_BYTES - 4000)

    def test_calibration_guidance_keeps_explicit_owner_control(self):
        from personal_agent.agent_runtime import CORE_INSTRUCTIONS
        from personal_agent.memory_service import VOICE_PREFERENCE_KEY
        guidance = prompt_files.prompt('voice_calibration')
        self.assertEqual(CORE_INSTRUCTIONS.count(guidance), 1)
        for boundary in (VOICE_PREFERENCE_KEY, 'save_memory', 'undo', 'redo', 'A/B',
                         'do not save partial', 're-offer unprompted', 'Ask no tastes',
                         'Voice', "Memory's preference rules"):
            with self.subTest(boundary=boundary):
                self.assertIn(boundary, guidance)

    def test_every_worker_route_and_its_status_generation_share_one_voice(self):
        from personal_agent.agent_runtime import (DEFINITIONS, action_definitions, context_sections,
                                                  render_turn_prompt, turn_context)
        voice = prompt_files.prompt('voice')
        for route, native_search in (('api', False), ('cli', False), ('cli', True)):
            context = turn_context([{'role': 'user', 'content': '안녕'}], route, native_search=native_search)
            assembled = (render_turn_prompt(context) if route == 'cli'
                         else context['instructions'] + '\n\n' + context_sections(context))
            with self.subTest(route=route, native_search=native_search):
                self.assertEqual(assembled.count(voice), 1)
                self.assertEqual(assembled.count('## Voice'), 1)
                self.assertEqual(assembled.count(prompt_files.prompt('voice_calibration')), 1)
                self.assertNotIn("in the owner's language and register", assembled)
        # Status is written by this same worker, with this same context; the schema
        # refers to the shared instructions rather than repeating a separate register.
        tools = {tool['function']['name']: {'host_action': tool['function']['name']} for tool in DEFINITIONS}
        statuses = [tool['function']['parameters']['properties']['status']['description']
                    for tool in action_definitions(tools, set(tools))]
        self.assertTrue(statuses)
        for description in statuses:
            self.assertIn('shared Voice instructions', description)
            self.assertNotIn('해요체', description)
            self.assertNotIn('friendly', description)

    def test_planner_lens_frames_content_without_assigning_a_voice(self):
        from personal_agent.orchestrator import QUESTION
        [lens] = [line for line in QUESTION.splitlines() if line.startswith('- brief.lens')]
        self.assertNotRegex(lens, r'(?i)\b(register|voice|persona)\b|how brief')
        for content in ('what matters', 'numbers', 'risks', 'next steps', 'when to report'):
            self.assertIn(content, lens)
        self.assertIn(prompt_files.prompt('voice'), QUESTION)


if __name__ == '__main__':
    unittest.main()
