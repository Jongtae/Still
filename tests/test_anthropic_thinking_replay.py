"""#1047: Claude's signed thinking blocks survive a tool boundary unchanged.

Current Claude models think by default.  Their thinking blocks must go back
unmodified with the tool results, and the prefix they are signed against (the
top-level ``system``, the tools and the earlier messages) must not change
between requests of one Work.
"""
import copy
import json
import unittest

from personal_agent.providers import ModelAdapter

ANTHROPIC = {'provider': 'anthropic', 'endpoint': 'https://api.anthropic.com', 'model': 'claude-opus-5-5'}
OPENAI = {'provider': 'openai', 'endpoint': 'https://api.openai.com/v1', 'model': 'gpt-x'}
TOOLS = [{'type': 'function', 'function': {'name': 'lookup', 'description': 'Look up.',
                                           'parameters': {'type': 'object', 'properties': {}}}}]
FIRST = {'model': 'claude-opus-5-5', 'stop_reason': 'tool_use', 'content': [
    {'type': 'thinking', 'thinking': '', 'signature': 'sig-1'},
    {'type': 'text', 'text': 'Checking.'},
    {'type': 'tool_use', 'id': 'toolu_1', 'name': 'lookup', 'input': {}}]}
SECOND = {'model': 'claude-opus-5-5', 'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': 'Done.'}]}


class Transport:
    def __init__(self, *replies):
        self.replies, self.bodies = list(replies), []

    def __call__(self, url, body, headers, timeout=60):
        self.bodies.append(copy.deepcopy(body))
        return self.replies.pop(0)


class ThinkingReplayTests(unittest.TestCase):
    def run_loop(self):
        transport = Transport(FIRST, SECOND)
        adapter = ModelAdapter(transport)
        messages = [{'role': 'system', 'content': 'You are Still.'}, {'role': 'user', 'content': 'Look it up.'}]
        message, _ = adapter.tool_turn(ANTHROPIC, 'ak', messages, TOOLS)
        messages += [message, {'role': 'tool', 'tool_call_id': 'toolu_1', 'content': 'found'},
                     {'role': 'system', 'content': 'Execution check.'}]
        adapter.tool_turn(ANTHROPIC, 'ak', messages, TOOLS)
        return message, transport.bodies

    def test_signed_thinking_goes_back_unchanged(self):
        message, bodies = self.run_loop()
        self.assertEqual(message['tool_calls'][0]['id'], 'toolu_1')
        replayed = bodies[1]['messages'][1]
        self.assertEqual(replayed, {'role': 'assistant', 'content': FIRST['content']})

    def test_a_later_system_message_does_not_rewrite_the_signed_prefix(self):
        _message, bodies = self.run_loop()
        self.assertEqual(bodies[0]['system'], bodies[1]['system'])
        self.assertEqual(bodies[1]['messages'][:1], bodies[0]['messages'])
        last = bodies[1]['messages'][-1]
        self.assertEqual(last['role'], 'user')
        self.assertEqual(last['content'][0]['type'], 'tool_result')
        self.assertEqual(last['content'][-1], {'type': 'text', 'text': 'Execution check.'})

    def test_claude_blocks_never_reach_another_provider(self):
        message, _ = self.run_loop()
        transport = Transport({'model': 'gpt-x', 'output': [{'type': 'message', 'role': 'assistant', 'content': [
            {'type': 'output_text', 'text': 'ok'}]}]})
        ModelAdapter(transport).tool_turn(OPENAI, 'k', [{'role': 'user', 'content': 'hi'}, message,
                                                        {'role': 'tool', 'tool_call_id': 'toolu_1', 'content': 'x'}], TOOLS)
        self.assertNotIn('sig-1', json.dumps(transport.bodies[0]))


if __name__ == '__main__':
    unittest.main()
