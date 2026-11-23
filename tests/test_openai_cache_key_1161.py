"""#1161: the OpenAI route sends a stable ``prompt_cache_key`` for Still's static prefix.

OpenAI routes cache lookups by the prompt's start plus ``prompt_cache_key``.
Requests that share the model, tools and static prefix get the same key, so a
later Work reaches the prefix an earlier Work cached; any change to that text
changes the key, and a request without a static prefix sends none.

Evidence class: unit tests with an injected transport.  No model, provider,
network or owner data is used.
"""
import copy
import json
import unittest

from personal_agent.providers import STATIC_PREFIX_KEY, ModelAdapter

OPENAI = {'provider': 'openai', 'endpoint': 'https://api.openai.com/v1', 'model': 'gpt-6.2-sol'}
ANTHROPIC = {'provider': 'anthropic', 'endpoint': 'https://api.anthropic.com', 'model': 'claude-opus-5-5'}
TOOLS = [{'type': 'function', 'function': {'name': 'lookup', 'description': 'Look up.',
          'parameters': {'type': 'object', 'properties': {'q': {'type': 'string'}}}}}]
POLICY = 'Fixed instructions that are the same on every Work.'
OPENAI_REPLY = {'model': 'gpt-6.2-sol', 'status': 'completed',
                'output': [{'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'ok'}]}]}
ANTHROPIC_REPLY = {'model': 'claude-opus-5-5', 'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': 'ok'}]}


class Transport:
    def __init__(self, reply):
        self.reply, self.bodies = reply, []

    def __call__(self, url, body, headers, timeout=60):
        self.bodies.append(copy.deepcopy(body))
        return copy.deepcopy(self.reply)


def work(context, prefix=POLICY, question='Look it up.'):
    return [{'role': 'system', 'content': prefix + '\n' + context, STATIC_PREFIX_KEY: len(prefix)},
            {'role': 'user', 'content': question}]


def key_for(messages, tools=TOOLS, config=OPENAI):
    transport = Transport(OPENAI_REPLY)
    ModelAdapter(transport).tool_turn(config, 'sk', messages, tools)
    return transport.bodies[0].get('prompt_cache_key')


class OpenAICacheKeyTests(unittest.TestCase):
    def test_two_works_with_different_owner_context_share_one_key(self):
        first = key_for(work('Owner context of the first Work.'))
        second = key_for(work('Different owner context, later Work.', question='Something else.'))
        self.assertTrue(first and first.startswith('still-'))
        self.assertEqual(first, second)

    def test_the_key_changes_when_the_prefix_tools_or_model_change(self):
        base = key_for(work('ctx'))
        self.assertNotEqual(base, key_for(work('ctx', prefix=POLICY + ' Revised.')))
        other_tools = [dict(TOOLS[0], function=dict(TOOLS[0]['function'], description='Look up again.'))]
        self.assertNotEqual(base, key_for(work('ctx'), tools=other_tools))
        self.assertNotEqual(base, key_for(work('ctx'), config=dict(OPENAI, model='gpt-6.2-terra')))

    def test_the_key_carries_no_request_text(self):
        transport = Transport(OPENAI_REPLY)
        ModelAdapter(transport).tool_turn(OPENAI, 'sk', work('private owner detail'), TOOLS)
        key = transport.bodies[0]['prompt_cache_key']
        self.assertRegex(key, r'^still-[0-9a-f]{32}$')
        self.assertNotIn(STATIC_PREFIX_KEY, json.dumps(transport.bodies[0]))

    def test_no_static_prefix_means_no_key(self):
        self.assertIsNone(key_for([{'role': 'system', 'content': 'plain'}, {'role': 'user', 'content': 'hi'}]))
        self.assertIsNone(key_for([{'role': 'user', 'content': 'hi'}]))

    def test_other_routes_never_receive_the_key(self):
        transport = Transport(ANTHROPIC_REPLY)
        ModelAdapter(transport).tool_turn(ANTHROPIC, 'ak', work('ctx'), TOOLS)
        self.assertNotIn('prompt_cache_key', transport.bodies[0])


if __name__ == '__main__':
    unittest.main()
