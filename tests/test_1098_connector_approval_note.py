"""#1098: AI-side connector approval cards reuse the exact-request note."""
import json

import pytest

import test_connect_effects
from personal_agent import mcp_bridge, trust_record
from personal_agent.agent_runtime import CONNECTOR_APPROVAL_NOTE, ToolError
from personal_agent.browser_signin import UNATTENDED_APPROVAL_TEXT
from personal_agent.bounded_execution import cli_metadata
from personal_agent.code_run import APPROVAL_EXACT_LABEL
from personal_agent.quickstart_service import AgentService, request_prompt_parts
from personal_agent.self_set_writes import scoped_binding
from test_744_resume import bridge  # noqa: F401 (fixture)
from test_connect_effects import ACTION, INPUT

NOTE = 'Apply the change for 2 units at 4.00.'


@pytest.fixture
def connector():
    case = test_connect_effects.EffectBoundary()
    case.setUp()
    try:
        yield case
    finally:
        case.doCleanups()


def set_effect(case, effect):
    trust_record.set_operation(case.store, case.target, 'apply_change', effect)
    if effect == 'mutate':
        case.store.mark_outside_content(case.job['id'], {'category': 'ai-connection'})


def record(case, values=INPUT, note=NOTE):
    return case.service.connector_approval_note(case.job, {
        'tool_name': ACTION, 'input': values, 'approval_note': note})


@pytest.mark.parametrize('effect', ['mutate', 'payment'])
@pytest.mark.parametrize('wrong_input', [False, True])
def test_missing_exact_note_denies_without_an_approval(connector, effect, wrong_input):
    set_effect(connector, effect)
    if wrong_input:
        record(connector, {**INPUT, 'quantity': 3})
    decision = connector.call()
    assert decision['behavior'] == 'deny'
    assert 'approval_note_required' in decision['message']
    assert 'mcp__agentos__connector_approval_note' in decision['message']
    assert 'exact input' in decision['message']
    assert connector.service._browser_request(connector.job['id']) is None


@pytest.mark.parametrize('effect', ['mutate', 'payment'])
def test_latest_note_survives_restart_and_preserves_exact_input_and_binding(connector, effect):
    set_effect(connector, effect)
    service, job = connector.service, connector.job
    binding = service._connector_action_binding(job, ACTION, INPUT, connector.target, effect)
    if effect == 'mutate':
        binding = scoped_binding(binding)
    digest = service._browser_step_keys(binding)['digest']
    record(connector, note='Earlier description.')
    record(connector, note='  ' + NOTE.replace('2 units', '2   units') + '  ')
    record(connector, {**INPUT, 'quantity': 3}, 'A different request.')
    connector.service = service = AgentService(connector.store)

    assert connector.call()['behavior'] == 'deny'
    card = service._browser_request(job['id'])
    assert card['digest'] == digest
    assert card['label'].startswith(f'{NOTE}\n\n{APPROVAL_EXACT_LABEL} ')
    _title, summary, _rest, _check, exact = request_prompt_parts(card['label'])
    assert summary == NOTE
    assert all(str(value) in exact for value in INPUT.values())

    record(connector, note='A new description for the same 2 units.')
    assert connector.approve()['resumed']
    assert connector.call()['behavior'] == 'allow'


@pytest.mark.parametrize('effect', ['mutate', 'payment'])
def test_unattended_refusal_takes_precedence(connector, effect, monkeypatch):
    set_effect(connector, effect)
    monkeypatch.setattr(connector.service, 'unattended_work', lambda job: True)
    assert connector.call() == {'behavior': 'deny', 'message': UNATTENDED_APPROVAL_TEXT}
    assert connector.service._browser_request(connector.job['id']) is None


def test_read_needs_no_note(connector):
    set_effect(connector, 'read')
    assert connector.call()['behavior'] == 'allow'
    assert connector.service._browser_request(connector.job['id']) is None


@pytest.mark.parametrize('refusal, code', [
    ('unsafe', 'unsafe_connector_input'), ('stopped', 'stopped'),
    ('disabled', 'ai_connections_disabled'), ('invalid_tool', 'invalid_connector_tool'),
])
def test_refused_note_records_nothing(connector, refusal, code):
    arguments = {'tool_name': ACTION, 'input': INPUT, 'approval_note': NOTE}
    if refusal == 'unsafe':
        arguments['input'] = {**INPUT, 'credential': 'sensitive value'}
    elif refusal == 'stopped':
        connector.service.stop.set()
    elif refusal == 'disabled':
        connector.service.set_ai_connections({'enabled': False})
    else:
        arguments['tool_name'] = 'mcp__agentos__list_notes'
    before = connector.store.task_events(connector.job['id'])
    with pytest.raises(ToolError) as caught:
        connector.service.connector_approval_note(connector.job, arguments)
    assert caught.value.code == code and caught.value.ran is False
    assert connector.store.task_events(connector.job['id']) == before


def test_note_storage_redacts_secrets_and_keeps_only_the_digest(connector):
    secret = 'fixture-connector-secret-123456789'
    connector.store.secret('model_key', secret)
    record(connector, note=f'Apply the change for {secret}.')
    events = connector.store.task_events(connector.job['id'])
    assert [event['trace'] for event in events if event['tool'] == CONNECTOR_APPROVAL_NOTE] == [{
        'input_digest': connector.service.connector_input_digest(ACTION, INPUT),
        'approval_note': 'Apply the change for [redacted].',
    }]


def test_payment_length_limit_checks_the_full_label(connector):
    set_effect(connector, 'payment')
    values = {'item': 'x' * 7900}
    record(connector, values, 'Apply the change. ' * 16)
    decision = connector.call(values)
    assert decision['behavior'] == 'deny'
    assert 'approval_note_required' not in decision['message']
    assert connector.service._browser_request(connector.job['id']) is None


def test_bridge_lists_and_relays_the_tool_only_with_ai_connections_and_relay(bridge, monkeypatch):
    listing = {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}
    replies = bridge.wire(listing)
    assert CONNECTOR_APPROVAL_NOTE not in [tool['name'] for tool in replies[2]['result']['tools']]

    bridge.service.set_ai_connections({'enabled': True})
    replies = bridge.wire(listing, {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {
        'name': CONNECTOR_APPROVAL_NOTE, 'arguments': {'tool_name': ACTION, 'input': INPUT, 'approval_note': NOTE},
    }})
    names = [tool['name'] for tool in replies[2]['result']['tools']]
    assert CONNECTOR_APPROVAL_NOTE in names
    assert 'connector_permission' not in names
    assert 'same exact input' in replies[3]['result']['content'][0]['text']
    assert bridge.service._connector_approval_note(bridge.job, bridge.service.connector_input_digest(ACTION, INPUT)) == NOTE

    def list_without_relay(handle, _concurrent):
        tools = handle('tools/list', {})['tools']
        assert CONNECTOR_APPROVAL_NOTE not in [tool['name'] for tool in tools]

    monkeypatch.setattr(mcp_bridge, '_serve_stdio', list_without_relay)
    mcp_bridge.serve(str(bridge.store.root), bridge.job, ai_connections=True)


def test_codex_stream_accounts_for_the_note_as_a_bridge_call(connector):
    stream = '\n'.join(json.dumps(record) for record in [
        {'type': 'item.completed', 'item': {'id': 'note', 'type': 'mcp_tool_call',
            'server': 'agentos', 'tool': CONNECTOR_APPROVAL_NOTE, 'status': 'completed'}},
        {'type': 'turn.completed'},
    ])
    meta = cli_metadata('codex', stream)
    assert meta['bridge_calls'] == 1
    assert connector.service._connector_turn_accounted(connector.job['id'], 'codex', meta)
