"""Native-search intake: service launch precedes the independent bridge channel."""
import json
from unittest import mock

import pytest

from personal_agent import bounded_execution as be, owner_mcp, trust_record
from personal_agent.api_requests import save_slot
from personal_agent.quickstart_store import QuickStore
from personal_agent.self_set_writes import set_allowance
from test_744_effects import host, WRITE
from test_744_resume import bridge, cli


def native_turn(case, monkeypatch, enabled):
    case.complete_turn = True
    monkeypatch.setattr(case.service, 'cli_native_search', lambda *args: (enabled, '' if enabled else 'work_mode'))


@pytest.mark.parametrize('enabled', [True, False])
def test_launch_mark_is_committed_before_first_bridge_call(bridge, monkeypatch, enabled):
    case = bridge
    native_turn(case, monkeypatch, enabled)
    original = case.run_bridge
    observed = []
    def first_call(*args):
        # Another store instance, before the CLI emits even one stdout event.
        sources = QuickStore(case.store.root).outside_content_sources(case.job)
        observed.append(sources)
        assert bool(sources) == enabled
        if enabled:
            assert sources == [{'category': 'native-search-enabled', 'operation': 'cli-launch', 'engine': 'codex'}]
        return original(*args)
    monkeypatch.setattr(case, 'run_bridge', first_call)
    case.wire(cli._call(3, 'list_memory'))
    assert len(observed) == 1
    assert bool(case.store.outside_content_sources(case.job)) == enabled


@pytest.mark.parametrize('enabled', [True, False])
def test_native_launch_memory_save_uses_candidate_boundary(bridge, monkeypatch, enabled):
    case = bridge
    native_turn(case, monkeypatch, enabled)
    replies = case.wire(cli._call(3, 'save_memory', memory_key='preference.drink', content='cobalt tea'))
    result = cli._value(replies[3])
    # The real bridge uses worker_result's minimal receipt; state/source stay
    # in the host store, rather than being exposed as promotion authority.
    assert result['remembered'] is not enabled
    if enabled:
        assert case.store.memories() == []
        [candidate] = case.store.memory_candidates()
        assert (candidate['state'], candidate['content']) == ('pending', 'cobalt tea')
        assert candidate['source'][0]['category'] == 'native-search-enabled'
    else:
        assert [row['content'] for row in case.store.memories()] == ['cobalt tea']
        assert case.store.outside_content_sources(case.job) == []


@pytest.mark.parametrize('action', ['api', 'connector'])
@pytest.mark.parametrize('enabled', [True, False])
def test_native_launch_write_held_without_waiting_for_stdout(bridge, monkeypatch, enabled, action):
    case = bridge
    native_turn(case, monkeypatch, enabled)
    if action == 'api':
        save_slot(case.store, 'notes', ['notes.test'], 'fixture-secret-notes-123456789', money=False)
        request = cli._call(3, 'api_request', **WRITE)
        effects = case.api_sent
    else:
        case.service.set_ai_connections({'enabled': True})
        owner_mcp.set_confirmed(case.store, 'codex', ['synthetic'])
        target = trust_record.connection('codex', 'owner-mcp', 'synthetic')
        trust_record.set_operation(case.store, target, 'apply_change', 'mutate')
        request = {'id': 3, 'method': 'fixture/connector', 'params': {
            'tool_name': 'mcp__synthetic__apply_change', 'input': {'item': 'a'}}}
        effects = case.connector_sent
    case.wire(request)
    assert len(effects) == (0 if enabled else 1)
    offered = case.service._browser_request(case.job)
    assert bool(offered) == enabled
    if enabled:
        assert 'native-search-enabled' in offered['label'] and 'codex' in offered['label']


def test_native_launch_respects_current_connection_write_allowance(bridge, monkeypatch):
    case = bridge
    native_turn(case, monkeypatch, True)
    save_slot(case.store, 'notes', ['notes.test'], 'fixture-secret-notes-123456789', money=False)
    set_allowance(case.service, 'slot:notes', True, 0, 'owner')
    case.wire(cli._call(3, 'api_request', **WRITE))
    assert len(case.api_sent) == 1
    assert case.service._browser_request(case.job) is None


def test_launch_mark_failure_prevents_adapter_dispatch(bridge, monkeypatch):
    case = bridge
    native_turn(case, monkeypatch, True)
    launch = mock.Mock(wraps=case.service.execution_adapter.execute)
    monkeypatch.setattr(case.service.execution_adapter, 'execute', launch)
    monkeypatch.setattr(case.store, 'mark_outside_content', mock.Mock(side_effect=RuntimeError('mark unavailable')))
    case.wire(cli._call(3, 'save_memory', memory_key='preference.drink', content='cobalt tea'))
    launch.assert_not_called()
    assert case.store.memories() == []


EVENTS = [
    ('codex', {'type': 'item.completed', 'item': {'id': 's', 'type': 'web_search', 'query': 'archive query'}}, {'query': 'archive query'}),
    ('codex', {'type': 'item.completed', 'item': {'id': 's', 'type': 'web_search', 'action': {'type': 'open_page', 'url': 'https://outside.test/doc'}}}, {'url': 'https://outside.test/doc'}),
    ('claude-code', {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'id': 's', 'name': 'WebSearch', 'input': {'query': 'archive query'}}]}}, {'query': 'archive query'}),
    ('claude-code', {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'id': 's', 'name': 'WebFetch', 'input': {'url': 'https://outside.test/doc'}}]}}, {'url': 'https://outside.test/doc'}),
]


@pytest.mark.parametrize('engine,event,fields', EVENTS, ids=['codex-search', 'codex-fetch', 'claude-search', 'claude-fetch'])
def test_live_reader_marks_before_other_consumers_without_launch_mark(host, engine, event, fields):
    store, service, job = host
    observed = []
    def progress(step):
        sources = QuickStore(store.root).outside_content_sources(job['id'])
        assert sources == [{'category': 'native-search', 'operation': 'web_search', 'engine': engine, **fields}]
        observed.append(step)
    reader = be.live_progress_reader(engine, progress, native_observed=lambda step:
                                    service.record_cli_native_intake(job['id'], engine, step))
    assert store.outside_content_sources(job['id']) == []
    reader(json.dumps(event))
    assert observed
    assert store.outside_content_sources(job['id'])


def test_non_native_text_and_unrelated_claude_results_do_not_mark(host):
    store, service, job = host
    reader = be.live_progress_reader('claude-code', None, native_observed=lambda step:
                                    service.record_cli_native_intake(job['id'], 'claude-code', step))
    for event in [{'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'WebSearch was enabled'}]}},
                  {'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': 'other', 'content': 'WebSearch'}]}}]:
        reader(json.dumps(event))
    assert store.outside_content_sources(job['id']) == []


def test_live_mark_failure_is_not_a_presentation_failure(host, monkeypatch):
    store, service, job = host
    monkeypatch.setattr(store, 'mark_outside_content', mock.Mock(side_effect=RuntimeError('mark unavailable')))
    progress = mock.Mock()
    reader = be.live_progress_reader('codex', progress, native_observed=lambda step:
                                    service.record_cli_native_intake(job['id'], 'codex', step))
    stream = be._StreamedOutput.__new__(be._StreamedOutput)
    stream.on_line, stream.answered, stream.encoding = reader, None, 'utf-8'
    with pytest.raises(be.ExecutionError):
        stream._line(json.dumps(EVENTS[0][1]).encode())
    progress.assert_not_called()


def test_live_intake_failure_stops_and_reaps_the_cli_process(host, tmp_path, monkeypatch):
    import os
    import subprocess
    import sys
    store, service, job = host
    monkeypatch.setattr(store, 'mark_outside_content', mock.Mock(side_effect=RuntimeError('mark unavailable')))
    reader = be.live_progress_reader('codex', None, native_observed=lambda step:
                                    service.record_cli_native_intake(job['id'], 'codex', step))
    exited = mock.Mock()
    script = 'import time; print(' + repr(json.dumps(EVENTS[0][1])) + ', flush=True); time.sleep(10)'
    with pytest.raises(be.ExecutionError) as exc:
        be.bounded_run(subprocess.run, [sys.executable, '-c', script], cwd=tmp_path,
                       env=os.environ.copy(), timeout=15, on_line=reader, on_exit=exited)
    assert exc.value.failure_class == 'context_unverified'
    exited.assert_called_once()


def test_live_intake_precedes_claude_session_consumer(host, tmp_path):
    import os
    import subprocess
    import sys
    from types import SimpleNamespace
    store, service, job = host
    reader = be.live_progress_reader('codex', None, native_observed=lambda step:
                                    service.record_cli_native_intake(job['id'], 'codex', step))
    consumed = []
    def consume(line):
        assert store.outside_content_sources(job['id'])
        consumed.append(line)
    session = SimpleNamespace(pump=lambda stream: None, on_line=consume, pending=False, closed=False)
    be.bounded_run(subprocess.run, [sys.executable, '-c', 'print(' + repr(json.dumps(EVENTS[0][1])) + ', flush=True)'],
                   cwd=tmp_path, env=os.environ.copy(), timeout=10, on_line=reader, session=session)
    assert len(consumed) == 1


def test_adapter_wires_intake_without_presentation_sink(host, tmp_path):
    import sys
    from personal_agent.agent_runtime import Capabilities
    store, service, job = host
    binary = tmp_path / 'fixture-codex'
    records = [EVENTS[0][1], {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'Done'}},
               {'type': 'turn.completed'}]
    binary.write_text('#!' + sys.executable + '\n' + '\n'.join('print(' + repr(json.dumps(row)) + ', flush=True)' for row in records))
    binary.chmod(0o700)
    home = tmp_path / 'codex-home'
    home.mkdir()
    caps = Capabilities(store, None, {}, '', job['id'], lambda *args: None)
    tools = be.AgentOSMcpTools(caps)
    tools.native_search_observed = lambda step: service.record_cli_native_intake(job['id'], 'codex', step)
    adapter = be.BoundedExecutionAdapter(finder=lambda name: str(binary), runtime_root=tmp_path / 'runs', codex_home=home)
    assert store.outside_content_sources(job['id']) == []
    result = adapter.execute('codex', 'Read.', tools)
    assert result.content == 'Done'
    sources = store.outside_content_sources(job['id'])
    assert sources and sources[0]['category'] == 'native-search'
