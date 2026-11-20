"""#994: raw Responses wire contract through injected, model-free transports."""
import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evals'))
from agentos_eval.fixture_responses import responses_reply
from personal_agent.messages import msg
from personal_agent.providers import ModelAdapter, ProviderError

OPENAI = {'provider': 'openai', 'endpoint': 'https://api.openai.com/v1', 'model': 'gpt-6.2-sol'}
TOOL = {'type': 'function', 'function': {'name': 'lookup', 'description': 'Look up a record.',
                                      'parameters': {'type': 'object', 'properties': {'query': {'type': 'string'}}}}}
REASONING = {'type': 'reasoning', 'id': 'rs_1', 'encrypted_content': 'opaque-fixture',
             'summary': [{'type': 'summary_text', 'text': 'private-fixture-summary'}]}


class Transport:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, url, body, headers=None, timeout=60):
        self.calls.append((url, copy.deepcopy(body), headers, timeout))
        return self.replies.pop(0)


def test_tool_request_history_and_next_turn_reasoning():
    calls = [{'id': ident, 'type': 'function', 'function': {'name': 'lookup', 'arguments': arguments}}
             for ident, arguments in [('call_1', '{"query":"a"}'), ('call_2', '{"query":"b"}')]]
    reply = responses_reply({'content': 'First part.', 'tool_calls': calls}, 'gpt-6.2-sol-observed')
    reply['output'].insert(0, REASONING)
    reply['output'][1]['content'].append({'type': 'output_text', 'text': ' Second part.'})
    transport = Transport(reply, responses_reply({'content': 'Done.'}))
    adapter = ModelAdapter(transport)
    history = [{'role': 'system', 'content': 'System'}, {'role': 'user', 'content': 'User'},
               {'role': 'user', 'content': [{'type': 'text', 'text': 'Photo'},
                                           {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,AA=='}}]},
               {'role': 'assistant', 'content': 'Checking.', 'tool_calls': calls[:1],
                'openai_reasoning': [REASONING]},
               {'role': 'tool', 'tool_call_id': 'call_1', 'content': {'found': True}},
               {'role': 'system', 'content': 'Mid-loop instruction'},
               {'role': 'assistant', 'content': ''}]
    original = copy.deepcopy(history)
    message, actual = adapter.tool_turn(OPENAI, 'fixture-key', history, [TOOL],
                                       tool_choice={'type': 'function', 'function': {'name': 'lookup'}}, timeout=7)
    assert actual == 'gpt-6.2-sol-observed'
    assert message == {'role': 'assistant', 'content': 'First part. Second part.',
                       'tool_calls': calls, 'openai_reasoning': [REASONING]}
    expected_input = [*history[:2], {'role': 'user', 'content': [{'type': 'input_text', 'text': 'Photo'},
                       {'type': 'input_image', 'image_url': 'data:image/png;base64,AA=='}]}, REASONING,
                      {'role': 'assistant', 'content': 'Checking.'},
                      {'type': 'function_call', 'call_id': 'call_1', **calls[0]['function']},
                      {'type': 'function_call_output', 'call_id': 'call_1', 'output': json.dumps({'found': True})},
                      history[-2]]
    assert transport.calls[0] == ('https://api.openai.com/v1/responses',
        {'model': OPENAI['model'], 'input': expected_input,
         'tools': [{'type': 'function', **TOOL['function'], 'strict': False}],
         'tool_choice': {'type': 'function', 'name': 'lookup'}, 'store': False,
         'include': ['reasoning.encrypted_content'], 'stream': False}, {'Authorization': 'Bearer fixture-key'}, 7)
    assert history == original
    adapter.tool_turn(OPENAI, '', [message, {'role': 'tool', 'tool_call_id': 'call_1', 'content': 'a'},
                                 {'role': 'tool', 'tool_call_id': 'call_2', 'content': 'b'}], [TOOL])
    assert transport.calls[1][1]['input'] == [REASONING,
        {'role': 'assistant', 'content': 'First part. Second part.'},
        *[{'type': 'function_call', 'call_id': c['id'], **c['function']} for c in calls],
        {'type': 'function_call_output', 'call_id': 'call_1', 'output': 'a'},
        {'type': 'function_call_output', 'call_id': 'call_2', 'output': 'b'}]


@pytest.mark.parametrize('choice', ['auto', 'required', 'none'])
def test_choice_passthrough_and_images(choice):
    transport = Transport(responses_reply({'content': 'Seen.'}))
    ModelAdapter(transport).tool_turn(OPENAI, '', [{'role': 'user', 'content': 'Look'}], [],
                                     tool_choice=choice, images=[{'mime_type': 'image/png', 'data': b'photo'}])
    body = transport.calls[0][1]
    assert body['tool_choice'] == choice
    assert body['input'][0]['content'][0]['type'] == 'input_text'
    assert body['input'][0]['content'][1] == {'type': 'input_image', 'image_url': 'data:image/png;base64,cGhvdG8='}


@pytest.mark.parametrize('reported', [None, 'observed-model'])
@pytest.mark.parametrize('report_observed', [False, True])
def test_report_observed(reported, report_observed):
    transport = Transport(responses_reply({'content': 'OK'}, reported))
    _, actual = ModelAdapter(transport).tool_turn(OPENAI, '', [], [], report_observed=report_observed)
    assert actual == (reported or ('' if report_observed else OPENAI['model']))


@pytest.mark.parametrize('reply', [None, {}, {'output': None}, {'output': [None]},
    {'status': 'incomplete', 'incomplete_details': {'reason': 'max_output_tokens'}, 'output': [REASONING]},
    {'status': 'failed', 'error': {'message': 'private'}, 'output': []}, {'output': []},
    {'output': [{'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': None}]}]},
    {'output': [{'type': 'function_call', 'call_id': 'c', 'name': 'lookup', 'arguments': {}}]},
    {'output': [{'type': 'function_call', 'name': 'lookup', 'arguments': '{}'}]}])
def test_bad_tool_response_is_provider_error(reply):
    with pytest.raises(ProviderError, match=msg('providers.invalid_tool_response')):
        ModelAdapter(Transport(reply)).tool_turn(OPENAI, '', [], [])


def test_refusal_is_text_and_partial_text_is_usable():
    reply = {'status': 'incomplete', 'output': [{'type': 'message', 'role': 'assistant',
              'content': [{'type': 'refusal', 'refusal': 'Cannot comply.'}]}]}
    message, _ = ModelAdapter(Transport(reply)).tool_turn(OPENAI, '', [], [])
    assert message == {'role': 'assistant', 'content': 'Cannot comply.'}


@pytest.mark.parametrize('endpoint', ['https://compatible.example/v1', 'https://openrouter.ai/api/v1'])
def test_compatible_body_unchanged_and_reasoning_stripped(endpoint):
    config = {**OPENAI, 'provider': 'compatible', 'endpoint': endpoint}
    history = [{'role': 'assistant', 'content': 'Draft', 'reasoning_details': [{'type': 'text'}],
                'openai_reasoning': [REASONING]}]
    clean = [{k: v for k, v in history[0].items() if k != 'openai_reasoning'}]
    transport = Transport({'choices': [{'message': {'content': 'OK'}}]}, {'choices': [{'message': {'content': 'OK'}}]})
    adapter = ModelAdapter(transport)
    adapter.tool_turn(config, 'key', history, [TOOL], 'required')
    expected = {'model': config['model'], 'messages': clean, 'tools': [TOOL], 'tool_choice': 'required', 'stream': False}
    if endpoint == 'https://openrouter.ai/api/v1':
        expected['provider'] = {'require_parameters': True}
    assert transport.calls[0] == (endpoint + '/chat/completions', expected, {'Authorization': 'Bearer key'}, 60)
    adapter.invoke(config, 'key', history)
    assert transport.calls[1][1] == {'model': config['model'], 'messages': clean, 'stream': False}
    assert history[0]['openai_reasoning'] == [REASONING]


@pytest.mark.parametrize('provider,endpoint,reply', [
    ('ollama', 'http://localhost:11434', {'message': {'content': 'OK'}}),
    ('anthropic', 'https://api.anthropic.com', {'content': [{'type': 'text', 'text': 'OK'}]})])
def test_reasoning_never_reaches_other_providers(provider, endpoint, reply):
    transport = Transport(reply, reply)
    adapter = ModelAdapter(transport)
    history = [{'role': 'assistant', 'content': 'Draft', 'openai_reasoning': [REASONING]}]
    config = {**OPENAI, 'provider': provider, 'endpoint': endpoint}
    adapter.tool_turn(config, 'key', history, [])
    adapter.invoke(config, 'key', history)
    for _, body, _, _ in transport.calls:
        assert 'openai_reasoning' not in json.dumps(body)
        assert 'opaque-fixture' not in json.dumps(body)


def test_invoke_joins_raw_output_text_and_reports_model():
    reply = responses_reply({'content': 'Hello'}, 'observed')
    reply['output'].insert(0, REASONING)
    reply['output'].append({'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': ' world'}]})
    transport = Transport(reply)
    history = [{'role': 'system', 'content': 'Instructions'}, {'role': 'user', 'content': 'Hi'}]
    result = ModelAdapter(transport).invoke(OPENAI, 'key', history)
    assert (result.content, result.provider, result.model) == ('Hello world', 'openai', 'observed')
    assert transport.calls == [('https://api.openai.com/v1/responses',
                               {'model': OPENAI['model'], 'input': history, 'store': False},
                               {'Authorization': 'Bearer key'}, 60)]


def test_invoke_empty_text_error_and_model_fallback():
    with pytest.raises(ProviderError, match=msg('providers.no_text_response')):
        ModelAdapter(Transport({'output': []})).invoke(OPENAI, '', [])
    assert ModelAdapter(Transport(responses_reply({'content': 'OK'}))).invoke(OPENAI, '', []).model == OPENAI['model']


def test_agent_loop_retains_reasoning_without_recording_it(tmp_path):
    from personal_agent.agent_runtime import Capabilities, run_agent
    from personal_agent.quickstart_store import QuickStore
    from test_agency_loop import judgments

    reply = responses_reply({'tool_calls': [{'id': 'notes', 'function': {'name': 'list_notes', 'arguments': '{}'}}]})
    reply['output'].insert(0, REASONING)
    transport = Transport(reply, *[responses_reply({'content': 'No notes.'})] * 3)
    adapter = ModelAdapter(transport)
    events = []
    record = lambda *args: events.append(args)
    caps = Capabilities(QuickStore(tmp_path / 'state'), adapter, OPENAI, '', 'job', record, judgments=judgments())
    result = run_agent(adapter, OPENAI, '', [{'role': 'user', 'content': 'List my notes.'}], '', caps, record)
    assert REASONING in transport.calls[1][1]['input']
    assert transport.calls[1][1]['input'].index(REASONING) < next(
        i for i, item in enumerate(transport.calls[1][1]['input']) if item.get('type') == 'function_call')
    for recorded in (events, vars(result)):
        assert 'opaque-fixture' not in json.dumps(recorded)
        assert 'private-fixture-summary' not in json.dumps(recorded)


def test_free_router_retry_strips_both_reasoning_keys(tmp_path):
    from personal_agent.agent_runtime import Capabilities, run_agent
    from personal_agent.quickstart_store import QuickStore
    from test_agency_loop import judgments

    config = {**OPENAI, 'provider': 'compatible', 'endpoint': 'https://openrouter.ai/api/v1', 'model': 'openrouter/free'}
    histories = []

    class Adapter:
        def tool_turn(self, config, key, messages, tools, **kwargs):
            histories.append(copy.deepcopy(messages))
            if len(histories) == 1:
                return {'role': 'assistant', 'content': '', 'openai_reasoning': [REASONING],
                        'reasoning_details': [{'text': 'router-reasoning'}],
                        'tool_calls': [{'id': 'notes', 'function': {'name': 'list_notes', 'arguments': '{}'}}]}, 'openrouter/free'
            if len(histories) == 2:
                raise ProviderError('rate limited', status=429)
            return {'role': 'assistant', 'content': 'No notes.'}, 'openrouter/free'

    adapter = Adapter()
    caps = Capabilities(QuickStore(tmp_path / 'state'), adapter, config, '', 'job', lambda *a: None, judgments=judgments())
    run_agent(adapter, config, '', [{'role': 'user', 'content': 'List notes.'}], '', caps, lambda *a: None)
    assert any('openai_reasoning' in m and 'reasoning_details' in m for m in histories[1])
    assert not any('openai_reasoning' in m or 'reasoning_details' in m for m in histories[2])
    assert any(m.get('role') == 'tool' for m in histories[2])


@pytest.mark.parametrize('provider,endpoint,effort_key', [
    ('openai', 'https://api.openai.com/v1', 'reasoning'),
    ('anthropic', 'https://api.anthropic.com', 'output_config'),
    ('compatible', 'https://openrouter.ai/api/v1', 'reasoning'),
    ('ollama', 'http://localhost:11434', None),
])
@pytest.mark.parametrize('method', ['invoke', 'tool_turn'])
def test_optional_effort_preserves_existing_wire_body(provider, endpoint, effort_key, method):
    from personal_agent.providers import validate_model
    config = {'provider': provider, 'endpoint': endpoint, 'model': 'fixture-model'}
    reply = (responses_reply({'content': 'ok'}) if provider == 'openai' else
             {'content': [{'type': 'text', 'text': 'ok'}]} if provider == 'anthropic' else
             {'message': {'role': 'assistant', 'content': 'ok'}} if provider == 'ollama' else
             {'choices': [{'message': {'role': 'assistant', 'content': 'ok'}}]})
    transport = Transport(*[copy.deepcopy(reply) for _ in range(4)])
    adapter = ModelAdapter(transport)
    for extra in ({}, {'effort': ''}, {'effort': 'invalid'}, {'effort': 'high'}):
        args = ({'tools': []} if method == 'tool_turn' else {})
        getattr(adapter, method)({**config, **extra}, 'fixture-key', [{'role': 'user', 'content': 'hello'}], **args)
    baseline = transport.calls[0][1]
    assert transport.calls[1][1] == baseline
    assert transport.calls[2][1] == baseline
    expected = {**baseline, **({effort_key: {'effort': 'high'}} if effort_key else {})}
    assert transport.calls[3][1] == expected
    assert validate_model({**config, 'effort': 'high'})['effort'] == 'high'
