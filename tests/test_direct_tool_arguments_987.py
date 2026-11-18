"""Direct-route regressions from /tmp/still-watch/987-shapes.txt (#987).

The recording contains argument types, not tool results. Dictionary sources
below are an explicit fault injection, not a claim about the recorded payloads.
"""
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from personal_agent.agent_runtime import Capabilities, ToolError, check_arguments, run_agent
from personal_agent.providers import ModelAdapter
from personal_agent.quickstart_store import QuickStore
from test_agency_loop import CFG, Script, call, finish
from test_worker_supervision_893 import case, service_case, work_id  # scheduled Work fixtures


def recorded_turns(ref_count=4):
    turns = [{'tool_calls': [call('settings', 'settings_read', status='Checking settings')]}]
    refs = []
    for turn in range(12):
        calls = []
        for item in range(3):
            ref = f'call_{turn:02d}_{item:021d}'  # recorded ids are 29 characters
            refs.append(ref)
            calls.append(call(ref, 'api_request', slot='service',
                              url=f'https://api.example/items/{turn}/{item}',
                              method='GET', effect='read',
                              **({'approval_note': 'Read the item'} if turn == 0 else
                                 {'status': 'Reading the item'})))
        turns.append({'tool_calls': calls})
    turns.append(finish('finish', *refs[-ref_count:], summary='Please confirm the next step.', status='needs_owner'))
    return turns


def tool_results(script):
    return {message['tool_call_id']: json.loads(message['content'])
            for message in script.bodies[-1]['messages'] if message['role'] == 'tool'}


@pytest.fixture
def direct(tmp_path):
    store = QuickStore(tmp_path / 'state')
    events = []
    record = lambda *event: events.append(event)
    caps = Capabilities(store, None, CFG, '', 'job', record,
                        settings=lambda *_: {'settings': []})
    return store, caps, record, events


@pytest.mark.parametrize('ref_count', [2, 4])
@pytest.mark.parametrize('structured_sources', [False, True])
def test_recorded_finish_shape_with_tool_result_sources(direct, ref_count, structured_sources):
    store, caps, record, events = direct
    script = Script(*recorded_turns(ref_count))
    source = 'https://api.example/source'
    result = {'data': {'items': []}, 'sources':
              [source, {'url': source}, [source], source] if structured_sources else [source]}
    # Exercise Capabilities.execute and the real direct loop; only the API wire is injected.
    with patch.object(caps, 'api', return_value=SimpleNamespace(call=lambda _: result, slots=lambda: ['service'])):
        answer = run_agent(ModelAdapter(script), CFG, '', [{'role': 'user', 'content': 'Check the saved request.'}],
                           '', caps, record)
    assert len(script.bodies) == 14
    # #1014: structured sources do not break the run, and Still appends no source of its own.
    assert source not in answer.content
    assert answer.report['question'] == 'Please confirm the next step.'
    assert not any(status == 'claim_rejected' for _, status, _ in events)
    # status is an offered progress argument, not an unexpected field.
    assert tool_results(script)['settings']['settings'] == []


@pytest.mark.parametrize('arguments', [
    {'status': 'needs_owner', 'evidence_refs': [{}], 'summary': 'Please confirm.'},
    {'status': {}, 'evidence_refs': [], 'summary': 'Please confirm.'},
    {'status': 'needs_owner', 'evidence_refs': [], 'summary': {}},
    {'status': 'needs_owner', 'evidence_refs': [], 'summary': 'Please confirm.', 'unknown': [{}]},
    {'status': 'needs_owner', 'evidence_refs': [], 'summary': 'Please confirm.', 'extra': {}},
])
def test_malformed_finish_returns_correctable_arguments_error(direct, arguments):
    _, caps, record, _ = direct
    script = Script({'tool_calls': [call('bad', 'finish', **arguments)]},
                    finish('corrected', summary='Please confirm.', status='needs_owner'))
    answer = run_agent(ModelAdapter(script), CFG, '', [{'role': 'user', 'content': 'Check.'}], '', caps, record)
    error = tool_results(script)['bad']
    assert error['code'] == 'invalid_arguments'
    assert error['effect'] == 'none'
    assert error['ran'] is False
    assert 'evidence_refs' in error['error'] or any(key in error['error'] for key in arguments)
    assert answer.content == 'Please confirm.'
    assert len(script.bodies) == 2


def test_other_tool_arguments_are_typed_and_correctable(direct):
    _, caps, record, _ = direct
    script = Script({'tool_calls': [call('bad', 'settings_read', category={}),
                                    call('extra', 'settings_read', unexpected='value')]},
                    {'tool_calls': [call('good', 'settings_read')]},
                    finish('finish', 'good', summary='Please confirm.', status='needs_owner'))
    with patch.object(caps, 'execute', wraps=caps.execute) as execute:
        run_agent(ModelAdapter(script), CFG, '', [{'role': 'user', 'content': 'Check.'}], '', caps, record)
    assert execute.call_count == 1
    for ref in ('bad', 'extra'):
        assert tool_results(script)[ref]['code'] == 'invalid_arguments'
        assert tool_results(script)[ref]['ran'] is False


@pytest.mark.parametrize('function', [
    {'name': {}, 'arguments': '{}'},
    {'name': 'settings_read', 'arguments': '{'},
    {'name': 'settings_read', 'arguments': '[]'},
    {'name': 'finish', 'arguments': '{'},
])
def test_malformed_call_never_reaches_hashing_or_dispatch(direct, function):
    _, caps, record, _ = direct
    script = Script({'tool_calls': [{'id': 'bad', 'function': function}]},
                    finish('corrected', summary='Please confirm.', status='needs_owner'))
    with patch.object(caps, 'execute', wraps=caps.execute) as execute:
        run_agent(ModelAdapter(script), CFG, '', [{'role': 'user', 'content': 'Check.'}], '', caps, record)
    assert not execute.called
    assert tool_results(script)['bad']['code'] == 'invalid_arguments'
    assert tool_results(script)['bad']['ran'] is False


def test_shared_schema_checks_nested_values_enums_and_numeric_bounds():
    schema = {'type': 'object', 'properties': {
        'refs': {'type': 'array', 'items': {'type': 'string'}},
        'choice': {'type': 'string', 'enum': ['yes', 'no']},
        'enabled': {'type': 'boolean'},
        'count': {'type': 'integer', 'minimum': 1, 'maximum': 3}},
        'required': ['refs'], 'additionalProperties': False}
    assert check_arguments(schema, {'refs': ['a'], 'choice': 'yes', 'count': 2, 'enabled': True})
    for args in ({'refs': [{}]}, {'refs': [], 'choice': 'maybe'}, {'refs': [], 'count': True},
                 {'refs': [], 'count': 4}, {'refs': [], 'count': float('nan')}, []):
        with pytest.raises(ToolError) as caught:
            check_arguments(schema, args)
        assert caught.value.code == 'invalid_arguments'


def test_scheduled_work_executor_finishes_after_correctable_arguments(case, caplog):
    """Same recorded turns through the queue, WorkExecutor and durable Work row."""
    turns = recorded_turns()
    turns.insert(-1, {'tool_calls': [call('malformed', 'finish', status='needs_owner',
                                        evidence_refs=[{}], summary='Please confirm.')]})
    script = Script(*turns)
    case.service.adapter = ModelAdapter(script)
    api = SimpleNamespace(slots=lambda: ['service'], call=lambda _: {
        'data': {'items': []}, 'sources': [{'url': 'https://api.example/source'}]})
    with patch.object(Capabilities, 'api', return_value=api):
        assert case.service.run_one()
    job = case.store.job(work_id(case))
    assert job['status'] == 'partial'
    assert 'Please confirm the next step.' in job['response']
    assert 'work failed' not in caplog.text
    assert len(script.bodies) == 15
    assert tool_results(script)['malformed']['code'] == 'invalid_arguments'
    assert tool_results(script)['malformed']['ran'] is False
