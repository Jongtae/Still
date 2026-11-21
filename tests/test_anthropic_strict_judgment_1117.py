"""#1117: a judgment on the Anthropic route uses strict tool use, so its arguments match the schema."""
import copy
import unittest

from personal_agent.orchestrator import plan_schema
from personal_agent.providers import ModelAdapter

ANTHROPIC = {'provider': 'anthropic', 'endpoint': 'https://api.anthropic.com', 'model': 'claude-haiku-5-5'}
REPLY = {'model': 'claude-haiku-5-5', 'stop_reason': 'tool_use',
         'content': [{'type': 'tool_use', 'id': 'toolu_1', 'name': 'decide', 'input': {'confidence': 0.9}}]}


class Transport:
    def __init__(self):
        self.bodies = []

    def __call__(self, url, body, headers, timeout=60):
        self.bodies.append(copy.deepcopy(body))
        return REPLY


def tool(schema):
    return [{'type': 'function', 'function': {'name': 'decide', 'description': 'Record.', 'parameters': schema}}]


def walk(schema):
    if isinstance(schema, dict):
        yield schema
        for value in schema.values():
            yield from walk(value)
    elif isinstance(schema, list):
        for item in schema:
            yield from walk(item)


class StrictJudgmentTests(unittest.TestCase):
    def send(self, schema, choice):
        transport = Transport()
        ModelAdapter(transport).tool_turn(ANTHROPIC, 'ak', [{'role': 'user', 'content': 'q'}], tool(schema),
                                          tool_choice=choice)
        return transport.bodies[0]['tools'][0]

    def test_a_required_call_is_strict_with_a_schema_strict_mode_accepts(self):
        schema = dict(plan_schema([{'id': 'codex'}], ask_location=True, preview=True))
        schema['properties']['confidence'] = {'type': 'number', 'minimum': 0, 'maximum': 1}
        sent = self.send(schema, 'required')
        self.assertIs(sent['strict'], True)
        nodes = list(walk(sent['input_schema']))
        self.assertTrue(all(node.get('additionalProperties') is False for node in nodes if node.get('type') == 'object'))
        self.assertFalse({'minimum', 'maximum'} & {key for node in nodes for key in node})
        self.assertEqual(set(sent['input_schema']['properties']), set(schema['properties']))
        self.assertEqual(schema['properties']['confidence']['maximum'], 1)  # the local check keeps its bound

    def test_a_property_named_like_a_keyword_is_kept(self):
        schema = {'type': 'object', 'properties': {'pattern': {'type': 'string', 'maxLength': 5}}, 'required': ['pattern']}
        sent = self.send(schema, 'required')['input_schema']
        self.assertEqual(sent['properties'], {'pattern': {'type': 'string'}})

    def test_ordinary_worker_tools_are_sent_as_declared(self):
        schema = {'type': 'object', 'properties': {'body': {'type': 'object'}}}
        sent = self.send(schema, 'auto')
        self.assertNotIn('strict', sent)
        self.assertEqual(sent['input_schema'], schema)


if __name__ == '__main__':
    unittest.main()
