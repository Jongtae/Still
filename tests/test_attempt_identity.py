import hashlib
import json
from types import SimpleNamespace
import unittest

from personal_agent.attempt_identity import (
    TOOLS_KEEP, canonical_json, instructions_digest, normalize_tool,
    offered_tools_identity, short_digest, tool_digest, unavailable,
)


class AttemptIdentityTests(unittest.TestCase):
    def tool(self, name='search'):
        return {'name': name, 'description': 'Search documents',
                'parameters': {'type': 'object', 'properties': {'query': {'type': 'string'}}}}

    def identity(self, tools):
        return offered_tools_identity(tools, representation='still-host')

    def test_key_order(self):
        original = self.tool()
        reordered = {'parameters': {'properties': {'query': {'type': 'string'}}, 'type': 'object'},
                     'description': original['description'], 'name': original['name']}
        self.assertEqual(tool_digest(original), tool_digest(reordered))
        self.assertEqual(self.identity([original]), self.identity([reordered]))

    def test_schema_and_description_changes(self):
        original = self.tool()
        schema_changed = self.tool()
        schema_changed['parameters']['properties']['query']['type'] = 'integer'
        description_changed = dict(original, description='Search everything')
        for changed in (schema_changed, description_changed):
            with self.subTest(changed=changed):
                self.assertNotEqual(tool_digest(original), tool_digest(changed))
                self.assertNotEqual(self.identity([original])['digest'], self.identity([changed])['digest'])

    def test_tool_order_and_duplicates(self):
        tools = [self.tool('z'), self.tool('a'), dict(self.tool('a'), description='Another')]
        result = self.identity(tools)
        self.assertEqual(result, self.identity(reversed(tools)))
        self.assertEqual([tool['name'] for tool in result['tools']], ['a', 'a', 'z'])
        self.assertEqual(result['count'], 3)
        self.assertNotEqual(result['digest'], self.identity(tools[:2])['digest'])
        self.assertEqual(result['tools'][0]['digest'], tool_digest(tools[1])
                         if tool_digest(tools[1]) < tool_digest(tools[2]) else tool_digest(tools[2]))

    def test_supported_shapes(self):
        tool = self.tool()
        shapes = [tool, {'type': 'function', 'function': tool}, dict(tool, type='function'),
                  {'name': tool['name'], 'description': tool['description'], 'input_schema': tool['parameters']},
                  {'name': tool['name'], 'description': tool['description'], 'inputSchema': tool['parameters']},
                  SimpleNamespace(name=tool['name'], description=tool['description'], inputSchema=tool['parameters'])]
        for shape in shapes:
            with self.subTest(shape=shape):
                self.assertEqual(tool_digest(shape), tool_digest(tool))

    def test_defaults_and_non_identity_keys(self):
        self.assertEqual(normalize_tool({'name': 'empty'}),
                         {'name': 'empty', 'description': '', 'parameters': {}})
        original = dict(self.tool(), type='function')
        decorated = dict(original, annotations={'readOnlyHint': True}, cache_control={'type': 'ephemeral'}, strict=False)
        self.assertEqual(tool_digest(original), tool_digest(decorated))
        for shape in (dict(original, strict=True),
                      {'type': 'function', 'function': dict(self.tool(), strict=True)}):
            self.assertNotEqual(tool_digest(original), tool_digest(shape))
            self.assertTrue(normalize_tool(shape)['strict'])

    def test_instructions(self):
        text = 'Follow these instructions: 한글'
        self.assertEqual(instructions_digest(text), hashlib.sha256(text.encode()).hexdigest()[:16])
        self.assertEqual(short_digest(text), instructions_digest(text))
        self.assertIsNone(instructions_digest(''))
        self.assertIsNone(instructions_digest(None))
        self.assertNotEqual(instructions_digest(text), instructions_digest(text + '!'))

    def test_truncation_covers_every_tool(self):
        tools = [self.tool(f'tool_{index:02}') for index in range(45)]
        result = self.identity(iter(tools))
        self.assertEqual(result['count'], 45)
        self.assertEqual(len(result['tools']), TOOLS_KEEP)
        self.assertEqual(result['tools_truncated'], 5)
        tools[-1]['parameters']['properties']['extra'] = {'type': 'boolean'}
        changed = self.identity(tools)
        self.assertNotEqual(result['digest'], changed['digest'])
        self.assertEqual(result['tools'], changed['tools'])

    def test_empty_and_unavailable(self):
        result = self.identity([])
        self.assertEqual(result, {'representation': 'still-host', 'count': 0, 'tools': [],
                                  'digest': short_digest('[]')})
        self.assertEqual(unavailable('cli-bridge', 'Vendor native tools'),
                         {'representation': 'cli-bridge', 'digest': None, 'unavailable': 'Vendor native tools'})

    def test_record_has_no_definitions(self):
        tool = {'name': 'lookup', 'description': 'PRIVATE_DESCRIPTION_MARKER',
                'parameters': {'properties': {'PRIVATE_PROPERTY_MARKER': {'type': 'string'}}}}
        serialized = json.dumps(self.identity([tool]))
        self.assertNotIn('PRIVATE_DESCRIPTION_MARKER', serialized)
        self.assertNotIn('PRIVATE_PROPERTY_MARKER', serialized)
        self.assertEqual(set(self.identity([tool])['tools'][0]), {'name', 'digest'})

    def test_label_limits(self):
        result = offered_tools_identity([self.tool('n' * 100)], representation='r' * 50)
        self.assertEqual(result['representation'], 'r' * 40)
        self.assertEqual(result['tools'][0]['name'], 'n' * 80)
        self.assertEqual(unavailable('r' * 50, 'x' * 150),
                         {'representation': 'r' * 40, 'digest': None, 'unavailable': 'x' * 120})

    def test_missing_name(self):
        for tool in ({}, {'type': 'function', 'function': {}}, SimpleNamespace(description='missing')):
            with self.subTest(tool=tool), self.assertRaises(ValueError):
                normalize_tool(tool)

    def test_canonical_json(self):
        self.assertEqual(canonical_json({'z': ('한글',), 'a': 1}), '{"a":1,"z":["한글"]}')
        for value in (float('nan'), float('inf'), float('-inf')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                canonical_json(value)
        with self.assertRaises(TypeError):
            canonical_json(object())


if __name__ == '__main__':
    unittest.main()
