"""#744: native-search intake and a bounded barrier across stdout/bridge channels."""
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest import mock

import pytest

from personal_agent import bounded_execution as be, owner_mcp, trust_record
from personal_agent.agent_runtime import Capabilities, ToolError
from personal_agent.api_requests import save_slot
from personal_agent.quickstart_store import QuickStore
from personal_agent.self_set_writes import set_allowance
from test_744_effects import host, WRITE, injected_api
from test_744_resume import bridge, cli


def start(engine, tool, values, ref='call-1'):
    if engine == 'codex':
        server, name = tool[5:].split('__', 1)
        return {'type': 'item.started', 'item': {'id': ref, 'type': 'mcp_tool_call',
                'server': server, 'tool': name, 'arguments': values, 'status': 'in_progress'}}
    return {'type': 'assistant', 'message': {'content': [
        {'type': 'tool_use', 'id': ref, 'name': tool, 'input': values}]}}


def native_host(host, engine='codex', enabled=True):
    store, service, job = host
    sent = []
    save_slot(store, 'notes', ['notes.test'], 'fixture-secret-notes-123456789', money=False)
    caps = Capabilities(store, None, {}, '', job['id'], lambda *args: None,
        browser_approvals=service.browser_approvals_for(job),
        connections=lambda: service.connection_slots(job), judgments=service.decision_judge)
    caps.api().transport = lambda *args: injected_api(*args, sent=sent)
    barrier = be.NativeIntakeBarrier(service.connector_input_digest,
        lambda tool: store.mark_outside_content(job['id'], {'category': 'native-search-barrier',
            'engine': engine, 'operation': tool, 'reason': 'start-event-timeout'}),
        marked=lambda: bool(store.outside_content_sources(job['id'])))
    barrier.enabled = enabled
    tools = be.AgentOSMcpTools(caps, native_search=enabled)
    tools.native_intake_barrier = barrier
    service.__dict__.setdefault('_native_intake_barriers', {})[job['id']] = barrier
    reader = be.live_progress_reader(engine, None, connector_digest=service.connector_input_digest,
        native_observed=lambda step: service.record_cli_native_intake(job['id'], engine, step),
        native_barrier=barrier)
    return tools, barrier, reader, sent


def delayed_stream(barrier, reader, events):
    # Fake the reader catching up while Condition.wait releases the decision.
    def consume(seconds):
        assert 0 < seconds <= be.NATIVE_INTAKE_WAIT_SECONDS
        for event in events:
            reader(json.dumps(event))
    return mock.patch.object(barrier.condition, 'wait', side_effect=consume)


@pytest.mark.parametrize('engine', ['codex', 'claude-code'])
@pytest.mark.parametrize('action', ['save_memory', 'api_request'])
def test_search_then_own_start_precedes_authority_decision(host, engine, action):
    store, service, job = host
    tools, barrier, reader, sent = native_host(host, engine)
    values = {'memory_key': 'preference.drink', 'content': 'cobalt tea'} if action == 'save_memory' else WRITE
    search = next(event for e, event, _ in EVENTS if e == engine)
    with delayed_stream(barrier, reader, [search, start(engine, 'mcp__agentos__'+action, values)]) as waited:
        if action == 'save_memory':
            result = tools.call(action, values)
            assert result['state'] == 'pending' and store.memories() == []
            assert store.memory_candidates()[0]['source'][0]['category'] == 'native-search'
        else:
            with pytest.raises(ToolError) as held:
                tools.call(action, values)
            assert held.value.code == 'approval_required' and sent == []
            assert 'native-search' in service._browser_request(job['id'])['label']
    waited.assert_called_once()
    assert store.outside_content_sources(job['id'])[0]['category'] == 'native-search'


@pytest.mark.parametrize('engine', ['codex', 'claude-code'])
def test_bridge_thread_waits_for_its_start_after_search(host, engine):
    store, _, job = host
    tools, barrier, reader, sent = native_host(host, engine)
    entered = Event()
    real_wait = barrier.condition.wait
    def wait(seconds):
        entered.set()
        return real_wait(seconds)
    with mock.patch.object(barrier.condition, 'wait', side_effect=wait), ThreadPoolExecutor(1) as pool:
        future = pool.submit(tools.call, 'api_request', WRITE)
        assert entered.wait(1) and not future.done() and sent == []
        reader(json.dumps(next(event for e, event, _ in EVENTS if e == engine)))
        reader(json.dumps(start(engine, 'mcp__agentos__api_request', WRITE)))
        with pytest.raises(ToolError) as held:
            future.result(timeout=1)
    assert held.value.code == 'approval_required' and sent == []
    assert store.outside_content_sources(job['id'])


@pytest.mark.parametrize('engine', ['codex', 'claude-code'])
@pytest.mark.parametrize('action', ['save_memory', 'api_request'])
def test_barrier_timeout_uses_marked_rules_with_fake_clock(host, engine, action):
    store, service, job = host
    tools, barrier, _, sent = native_host(host, engine)
    now = [0.0]
    barrier.clock = lambda: now[0]
    def advance(seconds):
        now[0] += seconds
    values = {'memory_key': 'preference.drink', 'content': 'cobalt tea'} if action == 'save_memory' else WRITE
    with mock.patch.object(barrier.condition, 'wait', side_effect=advance):
        if action == 'save_memory':
            assert tools.call(action, values)['state'] == 'pending'
            assert store.memories() == []
        else:
            with pytest.raises(ToolError) as held:
                tools.call(action, values)
            assert held.value.code == 'approval_required' and sent == []
    assert now[0] == be.NATIVE_INTAKE_WAIT_SECONDS
    assert store.outside_content_sources(job['id']) == [{'category': 'native-search-barrier',
        'engine': engine, 'operation': 'mcp__agentos__'+action, 'reason': 'start-event-timeout'}]


@pytest.mark.parametrize('engine', ['codex', 'claude-code'])
def test_search_enabled_without_search_waits_then_writes_unmarked(host, engine):
    store, _, job = host
    tools, barrier, reader, sent = native_host(host, engine)
    values = {'memory_key': 'preference.drink', 'content': 'cobalt tea'}
    with delayed_stream(barrier, reader, [start(engine, 'mcp__agentos__save_memory', values)]) as waited:
        assert tools.call('save_memory', values)['auto_saved']
    waited.assert_called_once()
    assert store.outside_content_sources(job['id']) == []
    assert [row['content'] for row in store.memories()] == ['cobalt tea']


@pytest.mark.parametrize('enabled', [True, False])
def test_reads_never_wait_and_disabled_native_write_never_waits(host, enabled):
    store, _, job = host
    tools, barrier, _, _ = native_host(host, enabled=enabled)
    with mock.patch.object(barrier.condition, 'wait', side_effect=AssertionError('read waited')):
        tools.call('list_memory', {})
        if not enabled:
            tools.call('save_memory', {'memory_key': 'preference.drink', 'content': 'cobalt tea'})
    assert store.outside_content_sources(job['id']) == []


@pytest.mark.parametrize('engine', ['codex', 'claude-code'])
def test_connector_mutate_waits_but_reviewed_read_does_not(host, engine):
    store, service, job = host
    _, barrier, reader, _ = native_host(host, engine)
    service.set_ai_connections({'enabled': True})
    owner_mcp.set_confirmed(store, engine, ['synthetic'])
    target = trust_record.connection(engine, 'owner-mcp', 'synthetic')
    trust_record.set_operation(store, target, 'apply_change', 'mutate')
    trust_record.set_operation(store, target, 'list_items', 'read')
    tool = 'mcp__synthetic__apply_change'
    service.connector_approval_note(job, {'tool_name': tool, 'input': {'item': 'a'},
                                          'approval_note': 'Apply the requested change.'})
    search = next(event for e, event, _ in EVENTS if e == engine)
    with barrier.call(tool, {'item': 'a'}), delayed_stream(barrier, reader,
            [search, start(engine, tool, {'item': 'a'})]) as waited:
        result = service.connector_permission(job, {'engine': engine, 'tool_name': tool,
            'input': {'item': 'a'}}, native_intake_wait=barrier.wait)
    waited.assert_called_once()
    assert result['behavior'] == 'deny' and service._browser_request(job['id'])
    no_wait = mock.Mock(side_effect=AssertionError('read waited'))
    assert service.connector_permission(job, {'engine': engine,
        'tool_name': 'mcp__synthetic__list_items', 'input': {}}, native_intake_wait=no_wait)['behavior'] == 'allow'
    no_wait.assert_not_called()


@pytest.mark.parametrize('effect', ['navigate', 'read', 'reversible', None])
def test_browser_press_classifies_before_waiting(host, effect):
    from personal_agent.browser_session import step_binding
    store, service, job = host
    _, barrier, reader, _ = native_host(host)
    service.decision_judge.connector_effect = mock.Mock(return_value=effect)
    values = {'target': 'item', 'effect': 'mutate'}
    binding = step_binding(job['id'], 'browser_click', 'https://outside.test', 'item')
    with barrier.call('mcp__agentos__browser_click', values), delayed_stream(barrier, reader,
            [EVENTS[0][1], start('codex', 'mcp__agentos__browser_click', values)]) as waited:
        if effect in ('read', 'navigate'):
            assert service.self_set_write(job, binding, None, 'browser_click', 'https://outside.test') is None
            waited.assert_not_called()
            assert store.outside_content_sources(job['id']) == []
        elif effect == 'reversible' and service.owner_typed_work(job):
            # #1065: after the wait, the owner's typed request covers a reversible press.
            ticket = service.self_set_write(job, binding, None, 'browser_click', 'https://outside.test')
            assert ticket['authorized_by'] == 'reversible'
            waited.assert_called_once()
        else:
            with pytest.raises(ToolError) as held:
                service.self_set_write(job, binding, None, 'browser_click', 'https://outside.test')
            assert held.value.code == 'approval_required'
            waited.assert_called_once()


def test_start_cannot_cover_a_second_call_or_a_different_input(host):
    _, _, _ = host
    _, barrier, reader, _ = native_host(host)
    values = {'memory_key': 'preference.drink', 'content': 'tea'}
    event = start('codex', 'mcp__agentos__save_memory', values)
    reader(json.dumps(event))
    with barrier.call('mcp__agentos__save_memory', values):
        barrier.wait()
    reader(json.dumps(event))  # duplicate delivery is not a second start
    barrier.timed_out = mock.Mock()
    barrier.clock = mock.Mock(side_effect=[0, 5, 10, 15])
    for args in (values, {**values, 'content': 'different'}):
        with barrier.call('mcp__agentos__save_memory', args):
            barrier.wait()
    assert barrier.timed_out.call_count == 2


@pytest.mark.parametrize('enabled', [True, False])
def test_real_adapter_launch_does_not_mark_before_bridge_read(bridge, monkeypatch, enabled):
    case = bridge
    case.complete_turn = True
    monkeypatch.setattr(case.service, 'cli_native_search', lambda *args: (enabled, ''))
    original = case.run_bridge
    def first_call(*args):
        assert QuickStore(case.store.root).outside_content_sources(case.job) == []
        assert case.service._native_intake_barriers[case.job].enabled == enabled
        return original(*args)
    monkeypatch.setattr(case, 'run_bridge', first_call)
    case.wire(cli._call(3, 'list_memory'))
    assert case.store.outside_content_sources(case.job) == []


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


def test_broker_thread_inherits_blocked_code_calls_barrier(host):
    from personal_agent.browser_session import step_binding
    store, service, job = host
    _, barrier, reader, _ = native_host(host)
    values = {'code': 'fixture request'}
    binding = step_binding(job['id'], 'code_run', 'https://notes.test/items', 'POST')
    with barrier.call('mcp__agentos__code_run', values), delayed_stream(barrier, reader,
            [EVENTS[0][1], start('codex', 'mcp__agentos__code_run', values)]) as waited, ThreadPoolExecutor(1) as pool:
        future = pool.submit(service.self_set_write, job, binding, 'slot:notes', 'POST',
                             'https://notes.test/items', effect='irreversible', values={'text': 'PWNED'})
        with pytest.raises(ToolError) as held:
            future.result(timeout=1)
    assert held.value.code == 'approval_required'
    waited.assert_called_once()
    assert store.outside_content_sources(job['id'])


def test_timeout_persistence_failure_prevents_memory_and_effect(host, monkeypatch):
    store, _, _ = host
    tools, barrier, _, sent = native_host(host)
    barrier.clock = mock.Mock(side_effect=[0, 5])
    monkeypatch.setattr(store, 'mark_outside_content', mock.Mock(side_effect=RuntimeError('unavailable')))
    with pytest.raises(RuntimeError):
        tools.call('save_memory', {'memory_key': 'preference.drink', 'content': 'cobalt tea'})
    assert store.memories() == [] and store.memory_candidates() == [] and sent == []


def test_timeout_still_respects_current_connection_write_allowance(host):
    store, service, job = host
    tools, barrier, _, sent = native_host(host)
    barrier.clock = mock.Mock(side_effect=[0, 5])
    set_allowance(service, 'slot:notes', True, 0, 'owner')
    tools.call('api_request', WRITE)
    assert len(sent) == 1 and service._browser_request(job['id']) is None
    assert any(source['reason'] == 'start-event-timeout' for source in store.outside_content_sources(job['id'])
               if 'reason' in source)


def test_adapter_delivers_start_after_intake_without_progress_or_connector_sink(host, tmp_path):
    import sys
    store, service, job = host
    tools, barrier, _, _ = native_host(host)
    tools.connector_input_digest = service.connector_input_digest
    tools.native_search_observed = lambda step: service.record_cli_native_intake(job['id'], 'codex', step)
    binary = tmp_path / 'fixture-codex'
    records = [EVENTS[0][1], start('codex', 'mcp__agentos__save_memory', {'memory_key': 'x', 'content': 'y'}),
               {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'Done'}}, {'type': 'turn.completed'}]
    binary.write_text('#!' + sys.executable + '\n' + '\n'.join('print(' + repr(json.dumps(row)) + ', flush=True)' for row in records))
    binary.chmod(0o700)
    home = tmp_path / 'codex-home'
    home.mkdir()
    barrier.enabled = False
    observed = []
    original = barrier.observe
    def observe(calls):
        if calls:
            assert store.outside_content_sources(job['id'])[0]['category'] == 'native-search'
            observed.extend(calls)
        original(calls)
    barrier.observe = observe
    adapter = be.BoundedExecutionAdapter(finder=lambda name: str(binary), runtime_root=tmp_path / 'runs', codex_home=home)
    assert adapter.execute('codex', 'Read.', tools).content == 'Done'
    assert barrier.enabled and observed[0]['status'] == 'started'


def test_no_search_api_dispatch_reads_unmarked_state_after_own_start(host):
    store, service, job = host
    tools, barrier, reader, sent = native_host(host)
    at_dispatch = []
    original = tools.capabilities.api().transport
    def transport(*args):
        at_dispatch.append(store.outside_content_sources(job['id']))
        return original(*args)
    tools.capabilities.api().transport = transport
    with delayed_stream(barrier, reader, [start('codex', 'mcp__agentos__api_request', WRITE)]) as waited:
        tools.call('api_request', WRITE)
    waited.assert_called_once()
    assert at_dispatch == [[]] and len(sent) == 1
    assert service._browser_request(job['id']) is None
    # Its provider response is subsequent outside intake, independently of search.
    assert store.outside_content_sources(job['id'])[0]['category'] == 'owner-api'


@pytest.mark.parametrize('searched', [True, False])
def test_real_adapter_stream_and_bridge_share_memory_barrier(bridge, monkeypatch, searched):
    case = bridge
    case.complete_turn = True
    monkeypatch.setattr(case.service, 'cli_native_search', lambda *args: (True, ''))
    runner = case.service.execution_adapter.runner
    values = {'memory_key': 'preference.drink', 'content': 'cobalt tea'}
    observed_waits = []
    def streaming_runner(argv, *, on_line=None, **kwargs):
        reader = on_line
        assert reader is not None
        assert case.store.outside_content_sources(case.job) == []
        events = ([EVENTS[0][1]] if searched else []) + [start('codex', 'mcp__agentos__save_memory', values)]
        barrier = case.service._native_intake_barriers[case.job]
        with delayed_stream(barrier, reader, events) as waited:
            result = runner(argv, **kwargs)
        observed_waits.append(waited.call_count)
        return result
    monkeypatch.setattr(case.service.execution_adapter, 'runner', streaming_runner)
    result = cli._value(case.wire(cli._call(3, 'save_memory', **values))[3])
    # Assert outside the runner: the service intentionally catches worker errors.
    assert observed_waits == [1]
    assert result['remembered'] is not searched
    assert bool(case.store.memory_candidates()) == searched
    assert bool(case.store.outside_content_sources(case.job)) == searched
