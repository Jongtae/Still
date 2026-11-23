"""#1139: the Anthropic API route caches its static prefix and every API route records cache usage.

Anthropic prompt caching (GA): the static prefix (tools + Still's fixed
instructions) gets a 1-hour breakpoint, a multi-turn loop's growing
conversation the default 5-minute automatic entry, and a one-shot required
judgment no tail entry.  OpenAI Responses caches implicitly (its only request
change is #1161's ``prompt_cache_key``); both routes record the provider's
reported token counts.

Evidence class: unit tests with injected transports and a temporary store.
No model, provider, network or owner data is used.
"""
import copy
import json
import shutil
import tempfile
import unittest

from personal_agent import information_use
from personal_agent.decision import DecisionContext, ModelDecisionEngine
from personal_agent.providers import STATIC_PREFIX_KEY, ModelAdapter, api_usage
from personal_agent.quickstart_store import QuickStore

ANTHROPIC = {'provider': 'anthropic', 'endpoint': 'https://api.anthropic.com', 'model': 'claude-opus-5-5'}
OPENAI = {'provider': 'openai', 'endpoint': 'https://api.openai.com/v1', 'model': 'gpt-6.2-sol'}
COMPATIBLE = {'provider': 'compatible', 'endpoint': 'http://127.0.0.1:9999', 'model': 'fixture'}
OLLAMA = {'provider': 'ollama', 'endpoint': 'http://127.0.0.1:11434', 'model': 'fixture'}
TOOLS = [{'type': 'function', 'function': {'name': 'lookup', 'description': 'Look up.',
                                           'parameters': {'type': 'object', 'properties': {}}}}]
POLICY = 'Fixed instructions that are the same on every Work.'
CONTEXT = 'Owner context that changes between Works.'
ANTHROPIC_USAGE = {'input_tokens': 120, 'output_tokens': 30, 'cache_creation_input_tokens': 900,
                   'cache_read_input_tokens': 4000, 'service_tier': 'standard',
                   'cache_creation': {'ephemeral_1h_input_tokens': 700, 'ephemeral_5m_input_tokens': 200}}
ANTHROPIC_REPLY = {'model': 'claude-opus-5-5', 'stop_reason': 'end_turn',
                   'content': [{'type': 'text', 'text': 'Done.'}], 'usage': ANTHROPIC_USAGE}
OPENAI_REPLY = {'model': 'gpt-6.2-sol', 'status': 'completed',
                'output': [{'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'Done.'}]}],
                'usage': {'input_tokens': 5000, 'output_tokens': 40, 'total_tokens': 5040,
                          'input_tokens_details': {'cached_tokens': 4096, 'cache_write_tokens': 512},
                          'output_tokens_details': {'reasoning_tokens': 10}}}


class Transport:
    def __init__(self, reply):
        self.reply, self.bodies = reply, []

    def __call__(self, url, body, headers, timeout=60):
        self.bodies.append(copy.deepcopy(body))
        return copy.deepcopy(self.reply)


def loop_messages():
    return [{'role': 'system', 'content': POLICY + '\n' + CONTEXT, STATIC_PREFIX_KEY: len(POLICY)},
            {'role': 'user', 'content': 'Look it up.'}]


class AnthropicCacheTests(unittest.TestCase):
    def send(self, messages, choice='auto', reply=ANTHROPIC_REPLY):
        transport = Transport(reply)
        adapter = ModelAdapter(transport)
        adapter.tool_turn(ANTHROPIC, 'ak', messages, TOOLS, tool_choice=choice)
        return transport.bodies[0], adapter

    def test_the_static_prefix_gets_the_one_hour_breakpoint_and_the_tail_the_automatic_entry(self):
        body, _ = self.send(loop_messages())
        first, rest = body['system'][0], body['system'][1:]
        self.assertEqual(first, {'type': 'text', 'text': POLICY, 'cache_control': {'type': 'ephemeral', 'ttl': '1h'}})
        self.assertTrue(all('cache_control' not in block for block in rest))
        self.assertEqual(body['cache_control'], {'type': 'ephemeral'})

    def test_the_model_reads_the_same_system_text_as_before(self):
        body, _ = self.send(loop_messages())
        self.assertEqual(''.join(block['text'] for block in body['system']), POLICY + '\n' + CONTEXT)

    def test_two_leading_system_messages_keep_their_joined_text(self):
        messages = [*loop_messages()[:1], {'role': 'system', 'content': 'Second.'}, {'role': 'user', 'content': 'q'}]
        body, _ = self.send(messages)
        self.assertEqual(''.join(block['text'] for block in body['system']), POLICY + '\n' + CONTEXT + '\nSecond.')
        self.assertEqual(sum('cache_control' in block for block in body['system']), 1)

    def test_a_required_judgment_caches_its_instructions_but_writes_no_tail_entry(self):
        messages = [{'role': 'system', 'content': POLICY, STATIC_PREFIX_KEY: len(POLICY)},
                    {'role': 'user', 'content': 'Judge this.'}]
        body, _ = self.send(messages, choice='required')
        self.assertNotIn('cache_control', body)
        self.assertEqual(body['system'], [{'type': 'text', 'text': POLICY,
                                           'cache_control': {'type': 'ephemeral', 'ttl': '1h'}}])

    def test_a_system_message_without_the_hint_is_sent_as_before(self):
        body, _ = self.send([{'role': 'system', 'content': 'You are Still.'}, {'role': 'user', 'content': 'q'}])
        self.assertEqual(body['system'], 'You are Still.')
        self.assertEqual(body['cache_control'], {'type': 'ephemeral'})
        body, _ = self.send([{'role': 'user', 'content': 'q'}], choice='required')
        self.assertEqual(body['system'], '')
        self.assertNotIn('cache_control', body)

    def test_the_hint_never_reaches_the_provider(self):
        body, _ = self.send(loop_messages())
        self.assertNotIn(STATIC_PREFIX_KEY, json.dumps(body))

    def test_the_prefix_is_byte_stable_across_turns_of_one_work(self):
        transport = Transport({'model': 'claude-opus-5-5', 'stop_reason': 'tool_use',
                               'content': [{'type': 'tool_use', 'id': 'toolu_1', 'name': 'lookup', 'input': {}}]})
        adapter = ModelAdapter(transport)
        messages = loop_messages()
        message, _ = adapter.tool_turn(ANTHROPIC, 'ak', messages, TOOLS)
        messages += [message, {'role': 'tool', 'tool_call_id': 'toolu_1', 'content': 'found'},
                     {'role': 'system', 'content': 'Execution check.'}]
        adapter.tool_turn(ANTHROPIC, 'ak', messages, TOOLS)
        first, second = transport.bodies
        self.assertEqual(json.dumps(first['tools']), json.dumps(second['tools']))
        self.assertEqual(json.dumps(first['system']), json.dumps(second['system']))

    def test_reported_usage_is_kept_with_the_writes_per_ttl(self):
        _, adapter = self.send(loop_messages())
        self.assertEqual(adapter.last_usage, {'input_tokens': 120, 'output_tokens': 30,
                                              'cache_creation_input_tokens': 900, 'cache_read_input_tokens': 4000,
                                              'ephemeral_1h_input_tokens': 700, 'ephemeral_5m_input_tokens': 200})

    def test_a_reply_without_usage_records_none(self):
        reply = {key: value for key, value in ANTHROPIC_REPLY.items() if key != 'usage'}
        _, adapter = self.send(loop_messages(), reply=reply)
        self.assertIsNone(adapter.last_usage)

    def test_a_later_call_does_not_keep_the_previous_usage(self):
        transport = Transport(ANTHROPIC_REPLY)
        adapter = ModelAdapter(transport)
        adapter.tool_turn(ANTHROPIC, 'ak', loop_messages(), TOOLS)
        transport.reply = {key: value for key, value in ANTHROPIC_REPLY.items() if key != 'usage'}
        adapter.tool_turn(ANTHROPIC, 'ak', loop_messages(), TOOLS)
        self.assertIsNone(adapter.last_usage)


class OtherProviderTests(unittest.TestCase):
    def test_openai_request_keeps_its_shape_and_its_cache_usage_is_recorded(self):
        transport = Transport(OPENAI_REPLY)
        adapter = ModelAdapter(transport)
        adapter.tool_turn(OPENAI, 'sk', loop_messages(), TOOLS)
        body = transport.bodies[0]
        self.assertNotIn('cache_control', body)
        self.assertNotIn('prompt_cache_options', body)
        self.assertNotIn(STATIC_PREFIX_KEY, json.dumps(body))
        self.assertEqual(body['input'][0], {'role': 'system', 'content': POLICY + '\n' + CONTEXT})
        self.assertEqual(adapter.last_usage, {'input_tokens': 5000, 'output_tokens': 40,
                                              'cached_tokens': 4096, 'cache_write_tokens': 512})

    def test_compatible_and_ollama_never_receive_the_hint(self):
        for config, reply in ((COMPATIBLE, {'choices': [{'message': {'role': 'assistant', 'content': 'ok'}}]}),
                              (OLLAMA, {'message': {'role': 'assistant', 'content': 'ok'}})):
            transport = Transport(reply)
            ModelAdapter(transport).tool_turn(config, '', loop_messages(), TOOLS)
            self.assertNotIn(STATIC_PREFIX_KEY, json.dumps(transport.bodies[0]))

    def test_usage_keeps_numbers_only(self):
        self.assertIsNone(api_usage('anthropic', {'usage': {'input_tokens': True, 'service_tier': 'x'}}))
        self.assertIsNone(api_usage('compatible', {'usage': {'prompt_tokens': 3}}))
        self.assertIsNone(api_usage('openai', {}))


class RecordTests(unittest.TestCase):
    def test_a_judgment_audit_carries_the_reported_usage(self):
        reply = {'model': 'claude-haiku-5-5', 'stop_reason': 'tool_use', 'usage': ANTHROPIC_USAGE,
                 'content': [{'type': 'tool_use', 'id': 'toolu_1', 'name': 'decide',
                              'input': {'answer': True, 'confidence': 0.9}}]}
        transport, records = Transport(reply), []
        engine = ModelDecisionEngine(ModelAdapter(transport), lambda: (dict(ANTHROPIC, model='claude-haiku-5-5'), 'ak'),
                                     audit=records.append)
        engine.judge(DecisionContext('test'), 'Is it done?')
        self.assertEqual(records[-1]['api_usage']['cache_read_input_tokens'], 4000)
        self.assertEqual(transport.bodies[0]['system'][0]['cache_control'], {'type': 'ephemeral', 'ttl': '1h'})
        self.assertNotIn('cache_control', transport.bodies[0])

    def test_direct_api_turns_are_summed_into_one_usage_attempt(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        store = QuickStore(root)
        job_id = store.enqueue('Look it up.', 'usage-1139', owner_typed=True, owner_words=True)
        with store.db() as db:
            for index, usage in enumerate(({'input_tokens': 100, 'cache_read_input_tokens': 900},
                                           {'input_tokens': 50, 'cache_read_input_tokens': 1000}, None)):
                detail = {'scope': 'main', 'model': 'claude-opus-5-5', **({'usage': usage} if usage else {})}
                db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                           (job_id, 'model', 'responded', json.dumps(detail), 1000.0 + index))
        audit = information_use.work_information_use(store, job_id, compare_held_values=False)
        attempts = audit['usage']['attempts']
        self.assertEqual([row['engine'] for row in attempts], ['direct-api'])
        self.assertEqual(attempts[0]['usage'], {'input_tokens': 150, 'cache_read_input_tokens': 1900})
        self.assertEqual(audit['usage']['totals']['usage'], {'input_tokens': 150, 'cache_read_input_tokens': 1900})

    def test_a_work_without_reported_usage_has_no_direct_api_attempt(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        store = QuickStore(root)
        job_id = store.enqueue('Look it up.', 'usage-1139-none', owner_typed=True, owner_words=True)
        with store.db() as db:
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (job_id, 'model', 'responded', json.dumps({'scope': 'main'}), 1000.0))
        audit = information_use.work_information_use(store, job_id, compare_held_values=False)
        self.assertEqual(audit['usage']['attempts'], [])


if __name__ == '__main__':
    unittest.main()
