"""#1445 part 4: every host action is declared once, with every attribute.

Adding ``code_run`` took about fifteen hand-kept lists, and two live defects were lists it was
missing from.  ``manifests.HOST_TOOLS`` now declares each attribute once (no defaults, so a new
tool cannot leave one out); the membership sets are derived from it, and the presentation
tables that stay hand-written must cover every host action.
"""
import dataclasses
import unittest

from personal_agent import agent_runtime, bounded_execution, information_use, manifests
from personal_agent.conversation_projection import TOOL_LABELS
from personal_agent.telegram_presence import FALLBACK_STEP_LINES


class OneDeclaration(unittest.TestCase):
    def test_no_attribute_has_a_default(self):
        for field in dataclasses.fields(manifests.HostTool):
            with self.subTest(field.name):
                self.assertIs(field.default, dataclasses.MISSING)
                self.assertIs(field.default_factory, dataclasses.MISSING)
        with self.assertRaises(TypeError):
            manifests.HostTool(write=False)

    def test_every_tool_the_model_can_call_is_declared_and_every_declared_tool_has_a_schema(self):
        names = {definition['function']['name'] for definition in agent_runtime.DEFINITIONS}
        self.assertEqual(names, set(manifests.HOST_TOOLS))
        self.assertEqual(manifests.HOST_ACTIONS, set(manifests.HOST_TOOLS))

    def test_the_sets_are_projections_of_the_declaration(self):
        tools = manifests.HOST_TOOLS
        self.assertEqual(manifests.WRITE_ACTIONS, {name for name, tool in tools.items() if tool.write})
        self.assertEqual(agent_runtime.EFFECT_FREE_READS | manifests.WRITE_ACTIONS, manifests.HOST_ACTIONS)
        self.assertFalse(agent_runtime.EFFECT_FREE_READS & manifests.WRITE_ACTIONS)
        self.assertEqual(set(agent_runtime.READONLY_EXCLUDED),
                         {name for name, tool in tools.items() if not tool.read_only_surface})
        self.assertEqual(agent_runtime.OWNER_STATE_ACTIONS, set(bounded_execution._OWNER_STATE_RELAYED))
        self.assertEqual(information_use.LOOKUP_ACTIONS, {name for name, tool in tools.items() if tool.lookup})
        self.assertEqual(agent_runtime.REDACTED_ARGUMENTS,
                         {name: tool.recorded_by_length for name, tool in tools.items() if tool.recorded_by_length})

    def test_every_declared_category_is_one_the_audit_names(self):
        for name, tool in manifests.HOST_TOOLS.items():
            if tool.reads is not None:
                with self.subTest(name):
                    self.assertIn(tool.reads, information_use.CATEGORY_NAMES)
                    self.assertEqual(information_use.READ_CATEGORIES[name], tool.reads)

    def test_every_host_action_has_a_label_and_a_step_line(self):
        for name in manifests.HOST_TOOLS:
            with self.subTest(name):
                self.assertIn(name, TOOL_LABELS, 'the conversation says what kind of step it was')
                self.assertIn(name, FALLBACK_STEP_LINES, 'the live step line names the kind of step')

    def test_a_service_held_action_is_relayed_on_the_trusted_local_route(self):
        relayed = set(bounded_execution.CLI_PROFILES['trusted-local']['actions'])
        for name, tool in manifests.HOST_TOOLS.items():
            if tool.service_held:
                with self.subTest(name):
                    self.assertIn(name, relayed)


if __name__ == '__main__':
    unittest.main()
