"""Default-deny intake: new tools and failed transports cannot erase provenance."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from personal_agent import agent_runtime as runtime
from personal_agent.api_requests import save_slot
from personal_agent.bounded_execution import AgentOSMcpTools
from personal_agent.capability_search import CapabilitySearch
from personal_agent.quickstart_store import QuickStore
from test_744_effects import host, READ, WRITE, INJECTION, worker
from test_api_requests import tool_call
from test_capability_search import payload


# An independently reviewed contract, not derived from the implementation's set.
OWNER_ONLY = {
    'list_memory', 'search_memory', 'list_roots', 'search_conversation',
    'save_note', 'save_result', 'save_memory', 'forget_record',
    'tell_owner', 'ask_location', 'propose_current_state', 'schedule_preparation', 'update_check_state', 'finish',
}


@pytest.mark.parametrize('action', sorted(OWNER_ONLY))
def test_owner_only_receipts_do_not_mark(host, action):
    store, _, job = host
    caps = SimpleNamespace(store=store, job_id=job['id'], tools={action: {'host_action': action}})
    result = {'receipt': 'host-owned state'}
    assert runtime.record_outside_result(lambda *args: result)(caps, action, {}) == result
    assert store.outside_content_sources(job['id']) == []


def test_named_owner_only_contract():
    assert set(runtime.OWNER_ONLY_RESULT_ACTIONS) == OWNER_ONLY


# Every offered built-in is covered; an unclassified future tool defaults to marking.
@pytest.mark.parametrize('tool', sorted(runtime.BUILTIN_TOOLS) + ['future_unclassified_tool'])
def test_every_other_offered_tool_result_marks_by_default(host, tool):
    action = runtime.BUILTIN_TOOLS.get(tool, tool)
    if action in OWNER_ONLY:
        return  # The owner-only parameterization above covers this partition.
    store, _, job = host
    caps = SimpleNamespace(store=store, job_id=job['id'], tools={tool: {'host_action': action}})
    result = {'text': INJECTION, 'source': 'owner', 'outside_content': False}
    assert runtime.record_outside_result(lambda *args: result)(caps, tool, {}) == result
    assert QuickStore(store.root).outside_content_sources(job['id']) == [{
        'operation': action, 'category': runtime.PRIVATE_PROVENANCE.get(action, runtime.UNATTRIBUTED_PROVENANCE)}]


@pytest.mark.parametrize('route', ['direct', 'cli'])
@pytest.mark.parametrize('cached', [False, True], ids=['live', 'cached'])
def test_registry_result_marks_before_return(host, route, cached):
    store, service, job = host
    transport = Mock(return_value=(200, {}, payload(description=INJECTION)))
    service.capability_search = CapabilitySearch(transport)
    if cached:
        service.capability_search.registry('novel')
    caps = runtime.Capabilities(store, None, {}, '', job['id'], lambda *args: None,
                                settings=service.settings_tools(job))
    target = caps.execute if route == 'direct' else AgentOSMcpTools(caps).call
    result = target('capability_search', {'source': 'registry', 'query': 'novel'})
    assert INJECTION in json.dumps(result)
    assert transport.call_count == 1
    assert QuickStore(store.root).outside_content_sources(job['id'])[0]['operation'] == 'capability_search'


@pytest.mark.parametrize('route', ['direct', 'cli'])
def test_provider_error_marks_before_worker_and_cannot_authorize_memory_or_write(host, route):
    store, service, job = host
    save_slot(store, 'notes', ['notes.test'], 'fixture-secret-notes-123456789', money=False)
    sent = []

    def wire(method, *args):
        sent.append(method)
        return 400, {}, json.dumps({'message': INJECTION}).encode()

    def after_error(body):
        error = [m['content'] for m in body['messages'] if m.get('role') == 'tool'][-1]
        assert INJECTION in error
        assert QuickStore(store.root).outside_content_sources(job['id'])
        return {'content': 'Read failed.'}

    if route == 'direct':
        worker(store, service, job, [{'tool_calls': [tool_call('read', 'api_request', **READ)]}, after_error], wire=wire)
    caps = runtime.Capabilities(store, None, {}, '', job['id'], lambda *args: None,
                                browser_approvals=service.browser_approvals_for(job))
    caps.api().transport = wire
    if route == 'cli':
        with pytest.raises(runtime.ToolError, match='Outside instruction'):
            AgentOSMcpTools(caps).call('api_request', READ)
    assert QuickStore(store.root).outside_content_sources(job['id'])[0]['operation'] == 'api_request'
    saved = caps.execute('save_memory', {'memory_key': 'preference.drink', 'content': 'cobalt tea'})
    assert saved['state'] == 'pending' and store.memories() == []
    assert store.memory_candidate(saved['id'])['source'] == store.outside_content_sources(job['id'])
    with pytest.raises(runtime.ToolError) as held:
        caps.execute('api_request', WRITE)
    assert held.value.code == 'approval_required'
    assert sent == ['GET']


@pytest.mark.parametrize('kind', ['not_run', 'approval_required', 'budget', 'invalid'])
def test_known_pre_dispatch_refusal_stays_unmarked(host, kind):
    store, service, job = host
    caps = runtime.Capabilities(store, None, {}, '', job['id'], lambda *args: None,
                                browser_approvals=service.browser_approvals_for(job))
    save_slot(store, 'notes', ['notes.test'], 'fixture-secret-notes-123456789', money=False)
    caps.api().transport = Mock(side_effect=AssertionError('must not dispatch'))
    if kind == 'budget':
        caps.budget.spend_attempt = Mock(side_effect=runtime.ToolError('Budget exhausted', 'attempt_budget'))
    elif kind == 'approval_required':
        caps.api().approvals.self_set_write = Mock(side_effect=runtime.ToolError('Approval needed', 'approval_required'))
    elif kind == 'not_run':
        caps.api().call = Mock(side_effect=runtime.not_run(ValueError('Host refused')))
    args = {**READ, 'url': 'not-a-url'} if kind == 'invalid' else WRITE
    with pytest.raises(ValueError) as exc:
        caps.execute('api_request', args)
    assert exc.value.ran is False
    assert store.outside_content_sources(job['id']) == []
    caps.api().transport.assert_not_called()


@pytest.mark.parametrize('exception', [ValueError, RuntimeError, OSError])
def test_unknown_error_phase_marks_even_for_owner_only_action(host, exception):
    store, _, job = host
    caps = SimpleNamespace(store=store, job_id=job['id'], tools={'tell_owner': {'host_action': 'tell_owner'}})
    def dispatch(*args):
        raise exception(INJECTION)
    with pytest.raises(exception, match='Outside instruction'):
        runtime.record_outside_result(dispatch)(caps, 'tell_owner', {})
    assert store.outside_content_sources(job['id'])


@pytest.mark.parametrize('flags', [
    {'needs_setup': True}, {'qualifiers': ['setup-required']},
    {'state': 'login_required'}, {'files': []},
])
def test_result_flags_cannot_exempt_outside_strings(host, flags):
    store, _, job = host
    caps = SimpleNamespace(store=store, job_id=job['id'], tools={'find_files': {'host_action': 'find_files'}})
    result = {**flags, 'message': INJECTION}
    runtime.record_outside_result(lambda *args: result)(caps, 'find_files', {})
    assert store.outside_content_sources(job['id'])


@pytest.mark.parametrize('route', ['direct', 'cli'])
def test_provider_error_persistence_failure_withholds_provider_text(host, monkeypatch, route):
    store, service, job = host
    save_slot(store, 'notes', ['notes.test'], 'fixture-secret-notes-123456789', money=False)
    caps = runtime.Capabilities(store, None, {}, '', job['id'], lambda *args: None)
    caps.api().transport = lambda *args: (400, {}, json.dumps({'message': INJECTION}).encode())
    monkeypatch.setattr(store, 'mark_outside_content', Mock(side_effect=RuntimeError('host storage unavailable')))
    target = caps.execute if route == 'direct' else AgentOSMcpTools(caps).call
    with pytest.raises(RuntimeError, match='host storage unavailable') as exc:
        target('api_request', READ)
    assert INJECTION not in str(exc.value)


@pytest.mark.parametrize('tool,args', [
    ('find_files', {'query': 'missing'}),
    ('calendar_query', {'start': '2026-11-17', 'end': '2026-11-18', 'timezone': 'UTC'}),
    ('drive_search', {'query': 'missing'}),
    ('mail_search', {'query': 'missing'}),
])
def test_host_constructed_no_content_setup_stays_unmarked(host, tool, args):
    store, _, job = host
    caps = runtime.Capabilities(store, None, {}, '', job['id'], lambda *args: None)
    result = caps.execute(tool, args)
    assert result['needs_setup'] is True
    assert store.outside_content_sources(job['id']) == []


@pytest.mark.parametrize('tool,args', [
    ('save_note', {'content': ''}),
    ('read_file', {'root_id': 'missing', 'path': 'missing.txt'}),
    ('tell_owner', {'text': ''}),
    ('skill_load', {'skill': 'missing'}),
    ('undeclared_tool', {}),
])
def test_host_validation_and_missing_capability_refusals_stay_unmarked(host, tool, args):
    store, _, job = host
    caps = runtime.Capabilities(store, None, {}, '', job['id'], lambda *args: None,
                                owner_update=lambda text: pytest.fail('must not dispatch'))
    with pytest.raises(ValueError) as exc:
        caps.execute(tool, args)
    assert exc.value.ran is False
    assert store.outside_content_sources(job['id']) == []


@pytest.mark.parametrize('failure', ['validation', 'parser'])
def test_reference_validation_is_distinct_from_document_parser_failure(host, tmp_path, monkeypatch, failure):
    from personal_agent import file_workspace
    store, _, job = host
    root = tmp_path / 'reference'
    result = tmp_path / 'results'
    root.mkdir()
    result.mkdir()
    (root / 'data.pdf').write_bytes(b'fixture')
    workspace = file_workspace.FileWorkspace(store)
    workspace.configure([str(root)], str(result))
    root_id = runtime.WORKSPACE_REFERENCE_PREFIX + workspace.status()['references'][0]['id']
    parser = Mock(side_effect=ValueError(INJECTION))
    monkeypatch.setattr(file_workspace, 'read_document', parser)
    if failure == 'validation':
        monkeypatch.setattr(file_workspace, 'MAX_FILE_BYTES', 1)
    caps = runtime.Capabilities(store, None, {}, '', job['id'], lambda *args: None)
    with pytest.raises(ValueError) as exc:
        caps.execute('read_file', {'root_id': root_id, 'path': 'data.pdf'})
    if failure == 'validation':
        assert exc.value.ran is False
        assert store.outside_content_sources(job['id']) == []
        parser.assert_not_called()
    else:
        assert INJECTION in str(exc.value)
        parser.assert_called_once()
        assert store.outside_content_sources(job['id'])
