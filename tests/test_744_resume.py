"""#744: service approval continuations with injected CLI/relay I/O, no sockets.

Keep the bridge handler, relay dispatch, CLI metadata parser and Work executor
real. Only the process/stdio/socket transports and remote effects are fixtures.
"""
import json
from unittest import mock

import pytest

import test_cli_route_agency as cli
from personal_agent import cli_browser_relay as relay_module, mcp_bridge, owner_mcp, trust_record
from personal_agent.api_requests import save_slot
from test_744_effects import READ, WRITE, injected_api


@pytest.fixture
def bridge(monkeypatch):
    case = cli.BrowserThroughTheBridge()
    case.setUp()
    case.api_sent, case.connector_sent, relays = [], [], []

    def relay_factory(tools, **kwargs):
        # Real relay dispatch and scope; only its listening socket/thread is absent.
        with mock.patch.object(relay_module.socketserver, 'ThreadingUnixStreamServer'), \
                mock.patch.object(relay_module.threading.Thread, 'start'):
            relay = relay_module.BrowserRelay(tools, **kwargs)
        tools.capabilities.api().transport = lambda *args: injected_api(*args, sent=case.api_sent)
        relays.append(relay)
        return relay

    def relay_call(client, name, arguments):
        relay = relays[-1]
        assert str(client.directory) == relay.address
        result = relay._reply(json.dumps({'key': relay._key, 'name': name, 'arguments': arguments}).encode())
        if 'error' in result:
            raise relay_module.relay_exception(result['error'])
        return result['ok']

    def run_bridge(args, requests):
        replies = {}

        def stdio(handle, concurrent=None):
            for request in requests:
                if request['method'] == 'fixture/connector':
                    # The same permission/observation callbacks installed by the
                    # Work executor for AI-side connectors; fake the remote write.
                    arguments = request['params']
                    result = relays[-1].tools.connector_permission(arguments)
                    if result['behavior'] == 'allow':
                        case.connector_sent.append(result['updatedInput'])
                        relays[-1].tools.connector_observed([{
                            'tool': arguments['tool_name'], 'status': 'succeeded',
                            'input_digest': case.service.connector_input_digest(
                                arguments['tool_name'], arguments['input'])}])
                else:
                    result = handle(request['method'], request.get('params', {}))
                replies[request['id']] = {'result': result}

        with mock.patch.object(mcp_bridge, '_serve_stdio', stdio):
            mcp_bridge.serve(str(case.store.root), case.job, browser_relay=relays[-1].address,
                            connection_slots=('notes',))
        return replies

    monkeypatch.setattr('personal_agent.work_executor.BrowserRelay', relay_factory)
    monkeypatch.setattr(relay_module.RelayClient, 'call', relay_call)
    monkeypatch.setattr(owner_mcp, 'codex_definition', lambda *args, **kwargs: {
        'type': 'stdio', 'command': 'fixture-mcp', 'args': []})
    case.run_bridge = run_bridge
    case.service.decision_judge.connector_effect = mock.Mock(return_value='reversible')
    try:
        yield case
    finally:
        case.doCleanups()


def test_existing_cart_bridge_journey_resumes_after_complete_cli_turn(bridge):
    """Run the reported failing fixture unchanged except for its I/O transport."""
    bridge.test_the_trusted_local_cli_lists_and_calls_the_five_browser_tools()


def requests_for(case, action):
    if action == 'browser':
        return (cli._call(3, 'browser_open', url=cli.ORIGIN + '/product', effect='navigate'),
                cli._call(4, 'browser_click', target='장바구니', effect='mutate'))
    if action == 'api':
        save_slot(case.store, 'notes', ['notes.test'], 'fixture-secret-notes-123456789', money=False)
        return (cli._call(3, 'api_request', **READ), cli._call(4, 'api_request', **WRITE))
    case.service.set_ai_connections({'enabled': True})
    owner_mcp.set_confirmed(case.store, 'codex', ['synthetic'])
    target = trust_record.connection('codex', 'owner-mcp', 'synthetic')
    trust_record.set_operation(case.store, target, 'apply_change', 'mutate')
    return (cli._call(3, 'browser_open', url=cli.ORIGIN + '/product', effect='navigate'),
            {'id': 4, 'method': 'fixture/connector', 'params': {
                'tool_name': 'mcp__synthetic__apply_change', 'input': {'item': 'a'}}})


def effects(case, action):
    if action == 'browser':
        return [post for driver in case.drivers for post in driver.posts]
    if action == 'api':
        return [row for row in case.api_sent if row[0] == 'POST']
    return case.connector_sent


@pytest.mark.parametrize('action', ['browser', 'api', 'connector'])
@pytest.mark.parametrize('decision', ['approve', 'deny'])
def test_marked_write_decision_resumes_same_work_once_or_denies(bridge, action, decision):
    case = bridge
    case.complete_turn = True
    requests = requests_for(case, action)
    case.wire(*requests)
    work = case.job
    assert case.store.outside_content_sources(work)
    assert effects(case, action) == []
    offered = case.service._browser_request(work)
    assert offered['action'] == 'self_set_write'
    assert case.store.job(work)['status'] == 'partial'
    result = case.service.browser_step_decision({
        'work_id': work, 'decision': decision, 'digest': offered['offer_digest']})
    approved = decision == 'approve'
    assert result == {'approved': approved, 'resumed': approved, 'work_id': work}
    if approved:
        assert case.store.job(work)['status'] == 'queued'
        # Repeat the bound request in the resumed worker too: it must dispatch once.
        case.rerun(*requests, *requests)
        assert len(effects(case, action)) == 1
        assert case.service._browser_request(work) is None
        with case.store.db() as db:
            assert [row[0] for row in db.execute(
                "SELECT state FROM memory_approvals WHERE action='browser-step'")] == ['consumed']
    else:
        assert case.service._browser_request(work) is None
        assert effects(case, action) == []
    assert case.job == work
    assert not case.service.run_one()
    receipts = [row for row in case.store.task_events(work)
                if row['tool'] == 'action_continuation' and row['status'] == 'queued']
    assert len(receipts) == int(approved)


@pytest.mark.parametrize('action', ['browser', 'api', 'connector'])
def test_incomplete_cli_stream_keeps_approval_from_resuming_unknown_turn(bridge, action):
    case = bridge
    case.complete_turn = False
    case.wire(*requests_for(case, action))
    offered = case.service._browser_request(case.job)
    result = case.service.browser_step_decision({
        'work_id': case.job, 'decision': 'approve', 'digest': offered['offer_digest']})
    assert result == {'approved': True, 'resumed': False, 'work_id': case.job}
    assert not case.service.run_one()
    assert effects(case, action) == []
    held = [row['trace'] for row in case.store.task_events(case.job)
            if row['tool'] == 'action_continuation' and row['status'] == 'withheld']
    assert [row['reason'] for row in held] == ['unaccounted-action']
