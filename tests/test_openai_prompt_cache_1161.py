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

from personal_agent.providers import STATIC_PREFIX_KEY, ModelAdapter, ProviderError

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



class OpenAIStaticBreakpointTests(unittest.TestCase):
    """#1164: GPT-5.6+ reuses a prefix only at a cache boundary, so the static prefix gets one."""

    def body(self, messages):
        transport = Transport(OPENAI_REPLY)
        ModelAdapter(transport).tool_turn(OPENAI, 'sk', messages, TOOLS)
        return transport.bodies[0]

    def test_the_static_prefix_ends_at_a_12h_breakpoint_and_the_text_is_unchanged(self):
        first = self.body(work('Owner context of one Work.'))['input'][0]
        self.assertEqual(first['role'], 'system')
        static, rest = first['content']
        self.assertEqual(static, {'type': 'input_text', 'text': POLICY,
                                  'prompt_cache_breakpoint': {'mode': 'explicit', 'ttl': '12h'}})
        self.assertEqual(rest, {'type': 'input_text', 'text': '\nOwner context of one Work.'})

    def test_two_works_send_the_same_static_block(self):
        a = self.body(work('First context.'))['input'][0]['content'][0]
        b = self.body(work('Second context.', question='Other.'))['input'][0]['content'][0]
        self.assertEqual(a, b)

    def test_a_system_message_without_the_hint_stays_a_plain_string(self):
        body = self.body([{'role': 'system', 'content': 'plain'}, {'role': 'user', 'content': 'hi'}])
        self.assertEqual(body['input'][0], {'role': 'system', 'content': 'plain'})
        self.assertNotIn('prompt_cache_breakpoint', json.dumps(body))

    def test_a_prefix_that_is_the_whole_message_gets_one_block(self):
        body = self.body([{'role': 'system', 'content': POLICY, STATIC_PREFIX_KEY: len(POLICY)},
                          {'role': 'user', 'content': 'hi'}])
        self.assertEqual(len(body['input'][0]['content']), 1)

    def test_anthropic_never_receives_an_openai_breakpoint(self):
        transport = Transport(ANTHROPIC_REPLY)
        ModelAdapter(transport).tool_turn(ANTHROPIC, 'ak', work('ctx'), TOOLS)
        self.assertNotIn('prompt_cache_breakpoint', json.dumps(transport.bodies[0]))


class RefusingTransport(Transport):
    """A model before GPT-5.6: refuses a request that carries an explicit breakpoint."""

    def __init__(self, reply, detail=None, status=400):
        super().__init__(reply)
        self.detail = detail or {'type': 'invalid_request_error', 'code': 'unknown_parameter',
                                 'param': 'input[0].content[0].prompt_cache_breakpoint'}
        self.status = status

    def __call__(self, url, body, headers, timeout=60):
        self.bodies.append(copy.deepcopy(body))
        if 'prompt_cache_breakpoint' in json.dumps(body):
            error = ProviderError('refused', status=self.status)
            error.error_detail = self.detail
            raise error
        return copy.deepcopy(self.reply)


class OlderModelFallbackTests(unittest.TestCase):
    """#1164: the provider's own refusal of a breakpoint sends that model as before."""

    def test_a_refused_breakpoint_is_resent_plain_and_not_tried_again(self):
        transport = RefusingTransport(OPENAI_REPLY)
        adapter = ModelAdapter(transport)
        older = dict(OPENAI, model='gpt-4.1')
        message, _model = adapter.tool_turn(older, 'sk', work('ctx'), TOOLS)
        self.assertEqual(message['content'], 'ok')
        self.assertEqual(len(transport.bodies), 2)
        self.assertEqual(transport.bodies[1]['input'][0], {'role': 'system', 'content': POLICY + '\nctx'})
        adapter.tool_turn(older, 'sk', work('other'), TOOLS)
        self.assertEqual(len(transport.bodies), 3)
        self.assertNotIn('prompt_cache_breakpoint', json.dumps(transport.bodies[2]))

    def test_the_fallback_is_per_model(self):
        transport = RefusingTransport(OPENAI_REPLY)
        adapter = ModelAdapter(transport)
        adapter.tool_turn(dict(OPENAI, model='gpt-4.1'), 'sk', work('ctx'), TOOLS)
        good = Transport(OPENAI_REPLY)
        adapter.transport = good
        adapter.tool_turn(OPENAI, 'sk', work('ctx'), TOOLS)
        self.assertIn('prompt_cache_breakpoint', json.dumps(good.bodies[0]))

    def test_any_other_error_is_not_retried(self):
        for status, detail in ((400, {'code': 'context_length_exceeded'}), (401, {'code': 'invalid_api_key'})):
            transport = RefusingTransport(OPENAI_REPLY, detail=detail, status=status)
            with self.subTest(status=status), self.assertRaises(ProviderError):
                ModelAdapter(transport).tool_turn(OPENAI, 'sk', work('ctx'), TOOLS)
            self.assertEqual(len(transport.bodies), 1)


if __name__ == '__main__':
    unittest.main()
