"""#673 owner decision: scoped CLI relay; synthetic state, never a provider."""
import json
import os
from pathlib import Path
import subprocess
import threading
from datetime import datetime, timezone
from unittest import mock

import pytest

from test_a2a_peers import state  # noqa: F401 - shared synthetic service fixture
from personal_agent import mcp_bridge
from personal_agent.a2a_work import answer_peer
from personal_agent.a2a_peers import PeerError
from personal_agent.bounded_execution import (BoundedExecutionAdapter, ExecutionError, ExecutionResult,
    RelayedWorkMcpTools, STRICT_PROFILE, BOUNDED_PROFILE)
from personal_agent.cli_browser_relay import BrowserRelay, RelayClient, relay_exception
from personal_agent.orchestrator import Catalogue

PLAN = ['shell_tool', 'view_image', 'plugins', 'hooks']


class InProcessRelay:
    """Exercise the actual authenticated dispatcher without a socket bind.

    Separate tests below use real RelayClient sockets and injected stdio only.
    """
    def __init__(self, tools, *, full_surface=False):
        self.relay = object.__new__(BrowserRelay)
        self.relay.tools, self.relay.full_surface = tools, full_surface
        self.relay._key, self.relay._closed = 'synthetic-key', False
        self.relay._lock = threading.Lock()
        self.address = 'synthetic-relay'

    def call(self, _client, name, arguments):
        reply = self.relay._reply(json.dumps({'key': self.relay._key, 'name': name, 'arguments': arguments}).encode())
        if 'ok' in reply:
            return reply['ok']
        raise relay_exception(reply['error'])

    def close(self):
        self.relay._closed = True


def catalogue(engine):
    return Catalogue([{'id': engine, 'available': True, 'tools': [], 'reason': '', 'models': [],
                       'default_model': '', 'model_tiers': {}, 'name': engine, 'kind': 'subscription',
                       'default': True, 'cost': 'subscription', 'latency': 'unknown'}],
                     {engine: {'kind': 'subscription', 'subscription': {'id': engine}}}, engine)


def peer_turn(state, engine='codex', callback=None, real_socket=False, *, decision_engine=None, answer='Scoped answer'):
    service, peers, model, registration = state
    from personal_agent.decision import FixtureDecisionEngine, BinaryDecision, OUTCOME_DECIDED
    from personal_agent.decision import fixture_confidence
    service.use_decision_engine(decision_engine or FixtureDecisionEngine(
        judge=lambda context, proposition: BinaryDecision(OUTCOME_DECIDED, True, fixture_confidence())
        if context.purpose == 'goal-reached' else None))
    service.store.put('subscription_isolation', {'profile': STRICT_PROFILE})
    observed = {}
    relays = []
    def create(tools, **kwargs):
        if real_socket:
            try:
                relay = BrowserRelay(tools, **kwargs)
            except PermissionError:
                pytest.skip('sandbox forbids Unix socket bind; rerun outside sandbox')
        else:
            relay = InProcessRelay(tools, **kwargs)
        relays.append(relay)
        return relay
    def execute(engine_id, prompt, tools, **kwargs):
        observed.update(engine=engine_id, prompt=prompt, tools=tools)
        def inspect(handle, concurrent):
            observed['listed'] = handle('tools/list', {})['tools']
            observed['results'] = callback(handle, tools) if callback else []
        # The actual bridge must never open a store or reconstruct capabilities,
        # even if an attacker supplies the owner's real store path.
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(mcp_bridge, '_serve_stdio', side_effect=inspect))
            if not real_socket:
                stack.enter_context(mock.patch.object(mcp_bridge, 'QuickStore', side_effect=AssertionError('bridge opened store')))
                stack.enter_context(mock.patch.object(mcp_bridge, 'Capabilities', side_effect=AssertionError('local capabilities')))
            mcp_bridge.serve(service.store.root, tools.capabilities.job_id, profile=STRICT_PROFILE,
                             peer_relay=tools.browser_relay)
        return ExecutionResult(answer, engine_id, 0)
    patches = [mock.patch('personal_agent.a2a_work.worker_catalogue', return_value=catalogue(engine)),
               mock.patch('personal_agent.a2a_work.BrowserRelay', side_effect=create),
               mock.patch.object(service.execution_adapter, 'execute', side_effect=execute)]
    if not real_socket:
        patches.append(mock.patch.object(RelayClient, 'call', lambda client, name, args: relays[-1].call(client, name, args)))
    from contextlib import ExitStack
    with ExitStack() as stack:
        for patch in patches:
            stack.enter_context(patch)
        observed['turn'] = peers.receive(registration['peer']['id'], 'relay-message', None, 'Share only what is permitted')
    return observed


@pytest.mark.parametrize('engine', ['codex', 'claude-code'])
def test_existing_strict_bridge_is_not_a_peer_disclosure_boundary(state, engine):
    service, peers, model, registration = state
    service.store.save_memory('private', 'MEMORY-OUTSIDE-SCOPE-673')
    service.memory_profile_request({'operation': 'remember', 'memory_key': 'profile.private', 'content': 'PROFILE-OUTSIDE-SCOPE-673'})
    with service.store.db() as db:
        db.execute("INSERT INTO notes VALUES ('private','NOTE-OUTSIDE-SCOPE-673',1)")
    forbidden = ('list_notes', 'save_note', 'propose_current_state', 'web_search', 'public_page_read', 'list_memory')
    def calls(handle, tools):
        return [handle('tools/call', {'name': name, 'arguments': {}}) for name in forbidden]
    seen = peer_turn(state, engine, calls)
    assert seen['turn']['state'] == 'completed'
    assert {tool['name'] for tool in seen['listed']} == seen['tools'].capabilities.offered_tools() == {'save_memory', 'tell_owner'}
    assert all(row['isError'] and row['structuredContent']['code'] == 'peer_tool_denied' for row in seen['results'])
    assert 'OUTSIDE-SCOPE-673' not in json.dumps([seen['prompt'], seen['results'], seen['listed']])
    denied = [row for row in service.store.task_events(seen['turn']['work_id']) if row['status'] == 'failed']
    assert len(denied) == len(forbidden)
    assert all(row['trace']['ran'] is False and row['tool'] == 'unlisted' for row in denied)
    assert not model.calls  # no direct-API fallback


def test_peer_relay_candidate_notice_budget_redaction_and_audit(state):
    service, peers, model, registration = state
    peers.update({'peer_id': registration['peer']['id'], 'scope': {'sections': ['memory'], 'tools': ['list_memory']}})
    service.store.save_memory('scoped', 'SCOPED-MEMORY-673')
    service.store.secret('test-secret', 'fixture-secret-value-673')
    def calls(handle, tools):
        values = []
        for name, args in [('list_memory', {}), ('save_memory', {'memory_key': 'peer.fact', 'content': 'Third party fact'}),
                           ('tell_owner', {'text': 'Please review fixture-secret-value-673'})]:
            values.append(handle('tools/call', {'name': name, 'arguments': args}))
        assert tools.capabilities.budget.attempts_used == 3
        return values
    seen = peer_turn(state, callback=calls)
    assert seen['turn']['state'] == 'failed'  # pending owner item is not completion
    assert service.store.job(seen['turn']['work_id'])['status'] == 'failed'
    assert 'SCOPED-MEMORY-673' in json.dumps(seen['results'])
    assert 'fixture-secret-value-673' not in json.dumps(seen['results'])
    assert len(service.store.memory_candidates(work_id=seen['turn']['work_id'])) == 1
    assert len(peers.conversations()['owner_items']) == 1
    assert not any(row.get('memory_key') == 'peer.fact' for row in service.store.memories())
    from personal_agent.information_use import work_information_use
    audit = work_information_use(service.store, seen['turn']['work_id'])
    assert audit['sent_to']['peers'][0]['tools'] == ['list_memory']
    assert 'fixture-secret-value-673' not in json.dumps(service.store.task_events(seen['turn']['work_id']))


def test_peer_relay_blocks_duplicate_and_budget_exhaustion(state):
    def calls(handle, tools):
        args = {'memory_key': 'peer.fact', 'content': 'Third party fact'}
        first = handle('tools/call', {'name': 'save_memory', 'arguments': args})
        duplicate = handle('tools/call', {'name': 'save_memory', 'arguments': args})
        assert not first.get('isError')
        assert duplicate['structuredContent']['code'] == 'duplicate_call'
        tools.capabilities.budget.attempts = tools.capabilities.budget.attempts_used
        exhausted = handle('tools/call', {'name': 'tell_owner', 'arguments': {'text': 'review'}})
        assert exhausted['structuredContent']['code'] == 'attempt_budget'
        return [first, duplicate, exhausted]
    seen = peer_turn(state, callback=calls)
    assert seen['turn']['state'] == 'completed'


def test_peer_block_cancels_cli_and_withholds_late_answer(state):
    service, peers, model, registration = state
    def calls(handle, tools):
        peers.block(registration['peer']['id'])
        assert tools.capabilities.budget.interrupted() == 'stopped'
        with pytest.raises(ValueError):
            handle('tools/list', {})
    seen = peer_turn(state, callback=calls)
    assert seen['turn']['state'] == 'cancelled'
    assert seen['turn']['answer'] != 'Scoped answer'
    assert service.store.job(seen['turn']['work_id'])['status'] == 'cancelled'


def test_peer_real_socket_bridge_only_injects_stdio(state):
    seen = peer_turn(state, callback=lambda handle, tools: [handle('tools/call', {'name': 'list_notes', 'arguments': {}})], real_socket=True)
    assert seen['turn']['state'] == 'completed'
    assert seen['results'][0]['structuredContent']['code'] == 'peer_tool_denied'


@pytest.mark.parametrize('engine', ['codex', 'claude-code'])
def test_peer_launch_argv_and_synthetic_global_homes(tmp_path, monkeypatch, engine):
    home = tmp_path / 'owner-home'
    home.mkdir()
    (home / 'AGENTS.md').write_text('GLOBAL-AGENTS-CANARY-673')
    (home / 'AGENTS.override.md').write_text('GLOBAL-OVERRIDE-CANARY-673')
    (home / 'CLAUDE.md').write_text('GLOBAL-CLAUDE-CANARY-673')
    (home / 'auth.json').write_text(json.dumps({'tokens': {'access_token': 'synthetic-auth-secret-673'}, 'last_refresh': datetime.now(timezone.utc).isoformat()}))
    (home / 'auth.json').chmod(0o600)
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('CODEX_HOME', str(home))
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(home))
    adapter = BoundedExecutionAdapter(codex_home=home, credentials=lambda engine: 'synthetic-oauth', runtime_root=tmp_path / 'runtime')
    run = tmp_path / 'turn'
    run.mkdir(mode=0o700)
    env = adapter.peer_environment(engine, '/bin/cli', run)
    config = run / 'mcp.json'
    config.write_text(json.dumps({'mcpServers': {'agentos': {'args': ['--peer-relay=private-relay']}}}))
    argv = adapter.command(engine, '/bin/cli', 'Peer prompt', config, 'Scoped instructions',
                           profile=STRICT_PROFILE, disabled_features=PLAN, peer_tools=['save_memory', 'tell_owner'])
    assert 'CANARY-673' not in json.dumps([env, argv])
    assert env['HOME'] == str(run)
    assert 'CLAUDE_CONFIG_DIR' not in env
    assert '--strict-mcp-config' in argv if engine == 'claude-code' else '--ignore-user-config' in argv
    if engine == 'codex':
        auth_home = Path(env['CODEX_HOME'])
        assert auth_home != home and list(auth_home.iterdir()) == [auth_home / 'auth.json']
        assert auth_home.stat().st_mode & 0o777 == 0o700
        assert (auth_home / 'auth.json').stat().st_mode & 0o777 == 0o600
        assert not (auth_home / 'auth.json').is_symlink()
        assert (auth_home / 'auth.json').read_bytes() == (home / 'auth.json').read_bytes()
        assert 'synthetic-auth-secret-673' not in json.dumps(argv)
    else:
        assert '--safe-mode' not in argv and '--system-prompt' in argv
        assert argv[argv.index('--setting-sources') + 1] == ''
        assert argv[argv.index('--tools') + 1] == ''
        assert argv[argv.index('--permission-mode') + 1] == 'dontAsk'
        assert argv[argv.index('--allowedTools') + 1:] == ['mcp__agentos__save_memory', 'mcp__agentos__tell_owner']


@pytest.mark.parametrize('kind', ['missing', 'symlink', 'public'])
def test_codex_peer_auth_without_safe_file_is_refused(tmp_path, kind):
    home, run = tmp_path / 'owner', tmp_path / 'run'
    home.mkdir(); run.mkdir()
    if kind == 'symlink':
        (home / 'auth.json').symlink_to(tmp_path / 'missing')
    if kind == 'public':
        (home / 'auth.json').write_text('credential')
        (home / 'auth.json').chmod(0o644)
    adapter = BoundedExecutionAdapter(codex_home=home)
    with pytest.raises(ExecutionError, match='auth-only'):
        adapter.peer_environment('codex', '/bin/cli', run)


@pytest.mark.parametrize('engine', ['codex', 'claude-code'])
def test_opt_in_peer_cli_qualification(tmp_path, engine):
    """Real CLI, fake loopback model, synthetic homes; never a provider."""
    if os.environ.get('AGENTOS_CLI_QUALIFICATION') != '1':
        pytest.skip('opt-in real CLI qualification not enabled')
    # Use the normal runtime path: the strict sandbox rejects a /tmp root.
    import tempfile
    with tempfile.TemporaryDirectory(prefix='peer-673-', dir=Path.home()) as root:
        adapter = BoundedExecutionAdapter(runtime_root=Path(root) / 'runtime')
        result = adapter.qualify_strict(engine, store_root=Path(root) / 'store', purpose='peer')
        assert result['qualified'], result


@pytest.mark.parametrize('engine', ['codex', 'claude-code'])
@pytest.mark.parametrize('outcome', ['success', 'failure', 'cancelled', 'unqualified', 'changed'])
def test_peer_adapter_qualifies_exact_launch_and_cleans_auth(tmp_path, monkeypatch, engine, outcome):
    from personal_agent.agent_runtime import Capabilities, WorkBudget
    from personal_agent.quickstart_store import QuickStore
    from personal_agent.bounded_execution import EngineInterrupted
    owner = tmp_path / 'owner-cli-home'
    owner.mkdir()
    auth = owner / 'auth.json'
    auth.write_text(json.dumps({'tokens': {'access_token': 'synthetic-secret-673'}, 'last_refresh': datetime.now(timezone.utc).isoformat()}))
    auth_original = auth.read_text()
    auth.chmod(0o600)
    (owner / 'AGENTS.md').write_text('AMBIENT-OWNER-CANARY-673')
    (owner / 'CLAUDE.md').write_text('AMBIENT-CLAUDE-CANARY-673')
    store = QuickStore(tmp_path / 'store')
    caps = Capabilities(store, None, {}, '', 'peer-work', lambda *args: None,
                        allowed_tools={'save_memory'}, delegated=True, budget=WorkBudget(), secret_redactor=lambda text: text)
    caps.peer_guard = lambda: None
    tools = RelayedWorkMcpTools(caps)
    tools.browser_relay = 'synthetic-relay'
    seen = []
    def runner(argv, **kwargs):
        seen.append((argv, kwargs))
        config = json.loads((kwargs['cwd'] / 'agentos-mcp.json').read_text())
        args = config['mcpServers']['agentos']['args']
        assert '--peer-relay=synthetic-relay' in args
        assert str(store.root) not in args
        assert not any('browser-relay=' in arg for arg in args)
        assert 'CANARY-673' not in json.dumps([argv, kwargs['env']])
        assert 'synthetic-secret-673' not in json.dumps(argv)
        assert kwargs['env']['HOME'] == str(kwargs['cwd'])
        if engine == 'codex':
            assert Path(kwargs['env']['CODEX_HOME']).parent == kwargs['cwd']
            assert set(path.name for path in Path(kwargs['env']['CODEX_HOME']).iterdir()) == {'auth.json'}
        if outcome == 'cancelled':
            raise EngineInterrupted('stopped')
        if outcome == 'failure':
            return subprocess.CompletedProcess(argv, 1, '', 'CLI refused turn')
        stdout = (json.dumps({'item': {'type': 'agent_message', 'text': 'answer'}}) if engine == 'codex'
                  else json.dumps({'type': 'result', 'result': 'answer'}))
        return subprocess.CompletedProcess(argv, 0, stdout, '')
    adapter = BoundedExecutionAdapter(finder=lambda name: '/synthetic/cli', runner=runner,
        codex_home=owner, credentials=lambda engine: 'synthetic-oauth', runtime_root=tmp_path / 'runtime')
    monkeypatch.setattr(adapter, 'bridge_check', lambda: '')
    qualification_calls = []
    def qualify(*args, **kwargs):
        qualification_calls.append(kwargs)
        return {'qualified': outcome != 'unqualified', 'identity': 'old' if outcome == 'changed' else 'current',
                'disabled_features': PLAN, 'reason': 'peer-instructions-and-relay' if outcome == 'unqualified' else ''}
    monkeypatch.setattr(adapter, 'qualified_identity', qualify)
    monkeypatch.setattr(adapter, '_strict_binary_identity', lambda *args: 'current')
    if outcome == 'success':
        assert adapter.execute(engine, 'Scoped peer prompt', tools).content == 'answer'
    else:
        with pytest.raises(ExecutionError):
            adapter.execute(engine, 'Scoped peer prompt', tools)
    assert qualification_calls[0]['purpose'] == 'peer'
    assert len(seen) == (0 if outcome in ('unqualified', 'changed') else 1)
    assert not list((tmp_path / 'runtime').glob('turn-*'))
    assert auth.read_text() == auth_original


@pytest.mark.parametrize('engine', ['codex', 'claude-code'])
def test_unverified_launch_reason_is_visible_in_peer_settings(state, engine):
    service, peers, model, registration = state
    service.store.put('subscription_isolation', {'profile': STRICT_PROFILE})
    with mock.patch('personal_agent.a2a_work.worker_catalogue', return_value=catalogue(engine)), \
         mock.patch('personal_agent.a2a_work.BrowserRelay', side_effect=lambda tools, **kwargs: InProcessRelay(tools, **kwargs)), \
         mock.patch.object(service.execution_adapter, 'execute', side_effect=ExecutionError('No safe mechanism',
                          failure_class='isolation-unqualified', reason='peer-instructions-and-relay')):
        result = peers.receive(registration['peer']['id'], 'unsupported-cli', None, 'hello')
    assert result['state'] == 'failed'
    assert peers.conversations()['turns'][0]['answer'] == 'peer_cli_not_contained: peer-instructions-and-relay'
    assert not model.calls


def test_full_relay_authentication_and_method_boundaries(state):
    def calls(handle, tools):
        transport = InProcessRelay(tools, full_surface=True)
        wrong_key = transport.relay._reply(json.dumps({'key': 'wrong', 'name': 'tools/list', 'arguments': {}}).encode())
        assert wrong_key['error']['code'] == 'relay_refused'
        for name in ('redact_known_secrets', 'connector_permission', 'list_notes'):
            with pytest.raises(ValueError):
                transport.call(None, name, {})
        assert transport.call(None, 'tools/list', {})['tools'] == tools.definitions()
        return []
    assert peer_turn(state, callback=calls)['turn']['state'] == 'completed'


def test_peer_block_kills_running_synthetic_cli_process(state):
    """Real process-group cancellation, no model or socket; late reply withheld."""
    import sys
    from personal_agent.bounded_execution import bounded_run, EngineInterrupted
    service, peers, model, registration = state
    def calls(handle, tools):
        def ready(line):
            if 'ready' in line:
                peers.block(registration['peer']['id'])
        with pytest.raises(EngineInterrupted, match='stopped'):
            bounded_run(subprocess.run, [sys.executable, '-c',
                'import time; print("ready", flush=True); time.sleep(30)'],
                cwd=service.store.root, env={'PATH': '/usr/bin:/bin'}, timeout=5,
                interrupted=tools.capabilities.budget.interrupted, on_line=ready)
        return []
    seen = peer_turn(state, callback=calls)
    assert seen['turn']['state'] == 'cancelled'
    assert seen['turn']['answer'] != 'Scoped answer'


@pytest.mark.parametrize('engine', ['codex', 'claude-code'])
def test_peer_judgments_exclude_ambient_instructions_and_require_peer_pass(tmp_path, monkeypatch, engine):
    from personal_agent.decision import DecisionContext, RoutedDecisionEngine
    from personal_agent.decision_adapters import SubscriptionCliDecisionEngine
    from personal_agent.agent_runtime import WorkBudget
    home = tmp_path / 'cli-home'
    home.mkdir()
    (home / 'auth.json').write_text('{"OPENAI_API_KEY":"synthetic"}')
    (home / 'auth.json').chmod(0o600)
    (home / 'AGENTS.md').write_text('JUDGMENT-AMBIENT-CANARY-673')
    launched = []
    def runner(argv, **kwargs):
        launched.append((argv, kwargs['env']))
        assert kwargs['env']['HOME'] != str(home)
        assert 'JUDGMENT-AMBIENT-CANARY-673' not in json.dumps([argv, kwargs['env']])
        if engine == 'codex':
            assert Path(kwargs['env']['CODEX_HOME']) != home
            assert not (Path(kwargs['env']['CODEX_HOME']) / 'AGENTS.md').exists()
        else:
            assert '--safe-mode' not in argv
        data = json.dumps({'choice': 'yes'})
        out = (json.dumps({'item': {'type': 'agent_message', 'text': data}}) if engine == 'codex'
               else json.dumps({'type': 'result', 'result': data, 'structured_output': {'choice': 'yes'}}))
        return subprocess.CompletedProcess(argv, 0, out, '')
    adapter = BoundedExecutionAdapter(finder=lambda name: '/synthetic/cli', runner=runner,
        runtime_root=tmp_path / 'runtime', codex_home=home, credentials=lambda name: 'synthetic-oauth')
    monkeypatch.setattr(adapter, '_strict_binary_identity', lambda *args: 'current')
    judge = SubscriptionCliDecisionEngine(adapter, engine, codex_disabled_features=PLAN)
    budget = WorkBudget()
    budget.peer_store_root = tmp_path / 'store'
    routed = RoutedDecisionEngine(lambda: judge, budget=lambda: budget)
    schema = {'type': 'object', 'properties': {'choice': {'type': 'string'}}, 'required': ['choice'], 'additionalProperties': False}
    with mock.patch.object(adapter, 'qualified_identity', return_value={'qualified': True, 'identity': 'current', 'disabled_features': PLAN}) as qualification:
        routed.structured(DecisionContext('peer-planning', {'request': 'hello'}), 'Choose yes', schema)
    assert qualification.call_args.kwargs['purpose'] == 'peer'
    assert len(launched) == 1
    with mock.patch.object(adapter, 'qualified_identity', return_value={'qualified': False}):
        routed.structured(DecisionContext('peer-planning', {'request': 'hello'}), 'Choose yes', schema)
    assert len(launched) == 1  # refusal never runs an ordinary CLI judgment
    assert not list((tmp_path / 'runtime').iterdir())


def test_peer_qualification_is_separate_and_missing_observations_refuse():
    from test_strict_isolation import StrictQualificationLogic
    from personal_agent import cli_qualification
    # Reuse the existing synthetic native binaries and sandbox/feature runner.
    case = StrictQualificationLogic()
    case.setUp()
    try:
        adapter = BoundedExecutionAdapter(finder=case.finder, runner=case._runner('codex-cli 0.164.0'),
            runtime_root=case.folder / 'turns', codex_home=case.folder / 'codex-home')
        binary = case.finder('codex')
        ordinary = adapter.qualify_strict('codex', store_root=case.store)
        assert ordinary['qualified']
        assert adapter.strict_cached_qualification('codex', binary, case.store, purpose='peer') is None
        with mock.patch.object(cli_qualification, 'peer_surface_checks', return_value=[]):
            refused = adapter.qualify_strict('codex', store_root=case.store, purpose='peer')
        assert not refused['qualified']
        with mock.patch.object(cli_qualification, 'peer_surface_checks',
                               return_value=[{'check': 'peer-instructions-and-relay', 'passed': True}]):
            qualified = adapter.qualify_strict('codex', store_root=case.store, purpose='peer')
        assert qualified['qualified']
        assert adapter.strict_cached_qualification('codex', binary, case.store)['qualified']
        assert adapter.strict_cached_qualification('codex', binary, case.store, purpose='peer')['qualified']
        # #346: binary identity change cannot reuse a previous peer pass.
        Path(binary).write_bytes(b'changed CLI executable')
        assert adapter.strict_cached_qualification('codex', binary, case.store, purpose='peer') is None
    finally:
        case.doCleanups()


def test_peer_auth_stale_cleanup_preserves_live_turns_and_originals(tmp_path):
    import time
    home, root = tmp_path / 'owner-home', tmp_path / 'runtime'
    home.mkdir(); root.mkdir()
    (home / 'auth.json').write_text('owner original')
    expired = root / 'turn-peer-decision-old'
    live = root / 'turn-live'
    expired.mkdir(); live.mkdir()
    (expired / 'auth.json').write_text('expired synthetic credential copy')
    os.utime(expired, (time.time() - 1000, time.time() - 1000))
    link = root / 'turn-owner-link'
    link.symlink_to(home, target_is_directory=True)
    adapter = BoundedExecutionAdapter(runtime_root=root)
    adapter.cleanup_stale_turns()
    assert not expired.exists()
    assert live.exists() and link.is_symlink()
    assert (home / 'auth.json').read_text() == 'owner original'


def test_claude_probe_canaries_cover_only_controlled_instruction_locations(tmp_path):
    from personal_agent.cli_qualification import peer_instruction_canaries
    from personal_agent.quickstart_store import QuickStore
    owner_ancestor = tmp_path / 'CLAUDE.md'
    owner_ancestor.write_text('OWNER-ANCESTOR-MUST-STAY-UNCHANGED')
    root = tmp_path / 'synthetic-fixture'
    work = root / 'project' / 'work'
    work.mkdir(parents=True)
    store = QuickStore(root / 'store')
    markers = peer_instruction_canaries(root, work, store)
    assert len(markers) == 4  # cwd, project, fixture root, store
    for directory in (work, work.parent, root):
        assert 'CANARY-673' in (directory / 'CLAUDE.md').read_text()
        assert 'CANARY-673' in (directory / '.claude' / 'CLAUDE.md').read_text()
        assert 'CANARY-673' in (directory / '.claude' / 'rules' / 'canary.md').read_text()
        assert 'CANARY-673' in (directory / '.claude' / 'settings.json').read_text()
        assert 'CANARY-673' in (directory / '.claude' / 'settings.local.json').read_text()
    assert 'CANARY-673' in (store.root / 'CLAUDE.md').read_text()
    assert owner_ancestor.read_text() == 'OWNER-ANCESTOR-MUST-STAY-UNCHANGED'
    assert not (tmp_path / '.claude').exists()
    with pytest.raises(ValueError, match='controlled fixture'):
        peer_instruction_canaries(root, tmp_path, store)


def test_claude_clean_candidate_still_refused_with_uncontrolled_ancestors():
    """A working relay/clean synthetic prompt cannot certify real ancestor inputs."""
    from test_strict_isolation import StrictQualificationLogic
    from personal_agent import cli_qualification
    case = StrictQualificationLogic()
    case.setUp()
    try:
        adapter = BoundedExecutionAdapter(finder=case.finder, runner=case._runner('2.1.287 (Claude Code)'),
            runtime_root=case.folder / 'turns', codex_home=case.folder / 'codex-home')
        with mock.patch.object(cli_qualification, 'peer_surface_checks', return_value=[
            {'check': 'peer-instructions-and-relay', 'passed': True},
            {'check': 'peer-instruction-locations-covered', 'passed': False,
             'uncontrolled': ['ancestors-above-fixture', 'platform-managed-settings']} ]):
            result = adapter.qualify_strict('claude-code', store_root=case.store, purpose='peer')
        assert not result['qualified']
        assert 'peer-instruction-locations-covered' in result['reason']
        assert adapter.strict_cached_qualification('claude-code', case.finder('claude'), case.store, purpose='peer') is None
        # Missing the coverage check is itself a failure, never a pass.
        with mock.patch.object(cli_qualification, 'peer_surface_checks', return_value=[
            {'check': 'peer-instructions-and-relay', 'passed': True}]):
            result = adapter.qualify_strict('claude-code', store_root=case.store, purpose='peer')
        assert not result['qualified']
    finally:
        case.doCleanups()


def test_refresh_due_reason_reaches_settings_without_cli_launch(state, tmp_path):
    from datetime import timedelta
    service, peers, model, registration = state
    home = tmp_path / 'synthetic-cli-home'
    home.mkdir()
    auth = home / 'auth.json'
    auth.write_text(json.dumps({'auth_mode': 'chatgpt', 'tokens': {'refresh_token': 'SYNTHETIC-REFRESH-673'},
        'last_refresh': (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()}))
    auth.chmod(0o600)
    adapter = BoundedExecutionAdapter(codex_home=home, runtime_root=tmp_path / 'runtime',
        finder=lambda name: '/synthetic/codex', runner=mock.Mock(side_effect=AssertionError('CLI launched')))
    service.execution_adapter = adapter
    service.store.put('subscription_isolation', {'profile': STRICT_PROFILE})
    with mock.patch('personal_agent.a2a_work.worker_catalogue', return_value=catalogue('codex')), \
         mock.patch('personal_agent.a2a_work.BrowserRelay', side_effect=lambda tools, **kwargs: InProcessRelay(tools, **kwargs)), \
         mock.patch.object(adapter, 'bridge_check', return_value=''), \
         mock.patch.object(adapter, '_strict_binary_identity', return_value='current'), \
         mock.patch.object(adapter, 'qualified_identity', return_value={
             'qualified': True, 'identity': 'current', 'disabled_features': PLAN}):
        result = peers.receive(registration['peer']['id'], 'refresh-due', None, 'hello')
    assert result['state'] == 'failed'
    reason = peers.conversations()['turns'][0]['answer']
    assert 'peer-auth-refresh-due' in reason and 'normal owner Codex use refreshes' in reason
    assert 'SYNTHETIC-REFRESH-673' not in reason
    adapter.runner.assert_not_called()
    assert not model.calls


def peer_decisions(verdicts, plans=()):
    """Script the model at the decision transport, keeping real orchestration."""
    from personal_agent.decision import FixtureDecisionEngine, BinaryDecision, StructuredDecision, OUTCOME_DECIDED
    from personal_agent.decision import fixture_confidence
    verdicts, plans = iter(verdicts), iter(plans)
    def judge(context, proposition):
        if context.purpose == 'goal-reached':
            verdict = next(verdicts, None)
            if verdict is not None:
                return BinaryDecision(OUTCOME_DECIDED, verdict, fixture_confidence())
    def structured(context, question, schema):
        if context.purpose == 'work-orchestration':
            notes = next(plans, None)
            if notes:
                return StructuredDecision(OUTCOME_DECIDED,
                    {'reply': '', 'worker': 'codex', 'model': '', 'brief': {'notes': notes}, 'reason': 'fits'},
                    fixture_confidence())
    return FixtureDecisionEngine(judge=judge, structured=structured)


def test_peer_cli_failed_read_is_judged_and_redelegated(state):
    service, peers, model, registration = state
    peers.update({'peer_id': registration['peer']['id'],
                  'scope': {'sections': ['folders'], 'tools': ['read_file']}})
    engine = peer_decisions([False, False], ['Read the shared file.', 'Try another scoped source.'])
    attempts = []
    def calls(handle, tools):
        attempts.append(tools.capabilities.job_id)
        result = handle('tools/call', {'name': 'read_file', 'arguments': {'root_id': 'missing-root', 'path': 'missing.txt'}})
        assert result['isError']
        return [result]
    seen = peer_turn(state, callback=calls, decision_engine=engine, answer='I could not read the requested file.')
    assert len(attempts) == 2
    assert seen['turn']['state'] == 'failed'
    assert seen['turn']['answer'] == 'I could not read the requested file.'
    assert service.store.job(seen['turn']['work_id'])['status'] == 'failed'
    goals = [entry[1] for entry in engine.asked if entry[0] == 'judge' and entry[1].purpose == 'goal-reached']
    assert len(goals) == 2
    assert all('read_file' in json.dumps(goal.facts) for goal in goals)
    evaluations = [row['trace'] for row in service.store.task_events(seen['turn']['work_id'])
                   if row['tool'] == 'orchestrator' and row['status'] == 'evaluated']
    assert evaluations and all(row['outcome'] == 'not_reached' for row in evaluations)


def test_peer_cli_pending_owner_stops_after_one_judgment(state):
    service, peers, model, registration = state
    engine = peer_decisions([True], ['Ask the owner.', 'Must never run.'])
    attempts = []
    def calls(handle, tools):
        attempts.append(1)
        return [handle('tools/call', {'name': 'tell_owner', 'arguments': {'text': 'Please decide.'}})]
    seen = peer_turn(state, callback=calls, decision_engine=engine)
    assert attempts == [1]
    assert seen['turn']['state'] == 'failed'
    assert service.store.job(seen['turn']['work_id'])['status'] == 'failed'
    evaluations = [row['trace'] for row in service.store.task_events(seen['turn']['work_id'])
                   if row['tool'] == 'orchestrator' and row['status'] == 'evaluated']
    assert evaluations[0]['outcome'] == 'owner_needed'
    assert len([row for row in engine.asked if row[0] == 'judge' and row[1].purpose == 'goal-reached']) == 1


def test_peer_cli_success_judgment_is_scoped_and_redacted(state):
    service, peers, model, registration = state
    service.store.save_memory('private', 'OUT-OF-SCOPE-JUDGMENT-CANARY')
    other = service.store.enqueue('OWNER-HISTORY-JUDGMENT-CANARY', 'owner-private')
    with service.store.db() as db:
        db.execute('INSERT INTO messages(role,content,channel,created,job_id) VALUES (?,?,?,?,?)',
            ('user', 'OWNER-HISTORY-JUDGMENT-CANARY', 'web', 1, other))
        db.execute("INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)",
            (other, 'read_file', 'succeeded', json.dumps({'evidence': {'text': 'OTHER-WORK-EVENT-CANARY'}}), 1))
    service.store.secret('model_key', 'JUDGMENT-SECRET-CANARY')
    engine = peer_decisions([True], ['Answer the peer.'])
    seen = peer_turn(state, decision_engine=engine, answer='Served JUDGMENT-SECRET-CANARY')
    assert seen['turn']['state'] == 'completed'
    assert service.store.job(seen['turn']['work_id'])['status'] == 'succeeded'
    goals = [entry[1] for entry in engine.asked if entry[0] == 'judge' and entry[1].purpose == 'goal-reached']
    assert len(goals) == 1
    facts = json.dumps(goals[0].facts)
    assert 'Share only what is permitted' in facts
    for canary in ('OUT-OF-SCOPE-JUDGMENT-CANARY', 'OWNER-HISTORY-JUDGMENT-CANARY',
                   'OTHER-WORK-EVENT-CANARY', 'JUDGMENT-SECRET-CANARY'):
        assert canary not in facts


@pytest.mark.parametrize('verdicts', [[], [False]])
def test_peer_cli_unavailable_or_negative_judgment_never_succeeds(state, verdicts):
    seen = peer_turn(state, decision_engine=peer_decisions(verdicts))
    assert seen['turn']['state'] == 'failed'
    assert state[0].store.job(seen['turn']['work_id'])['status'] == 'failed'


@pytest.mark.parametrize('reached', [True, False])
def test_peer_api_reuses_its_own_outcome_judgment(state, reached):
    service, peers, model, registration = state
    peers.update({'peer_id': registration['peer']['id'],
                  'scope': {'sections': ['memory'], 'tools': ['list_memory']}})
    model.batch = [('list_memory', {})]
    engine = peer_decisions([reached])
    service.use_decision_engine(engine)
    turn = peers.receive(registration['peer']['id'], 'api-judgment', None, 'Read the shared memory.')
    assert turn['state'] == ('completed' if reached else 'failed')
    assert service.store.job(turn['work_id'])['status'] == ('succeeded' if reached else 'failed')
    assert len([row for row in engine.asked if row[0] == 'judge' and row[1].purpose == 'goal-reached']) == 1


def test_peer_api_pending_owner_is_not_completion(state):
    service, peers, model, registration = state
    model.batch = [('tell_owner', {'text': 'Please decide.'})]
    service.use_decision_engine(peer_decisions([True]))
    turn = peers.receive(registration['peer']['id'], 'api-owner-needed', None, 'Ask the owner to decide.')
    assert peers.owner_outcomes(registration['peer']['id'], turn['work_id'])[0]['state'] == 'pending'
    assert turn['state'] == 'failed'
    assert service.store.job(turn['work_id'])['status'] == 'failed'


@pytest.mark.parametrize('seconds,exhausted,expected,calls', [
    (40, False, 'completed', 1), (10, False, 'failed', 0), (40, True, 'failed', 0)])
def test_peer_cli_final_judgment_obeys_service_budget(state, seconds, exhausted, expected, calls):
    engine = peer_decisions([True], ['Serve the peer.'])
    def shorten_budget(handle, tools):
        budget = tools.capabilities.budget
        budget.deadline = budget.clock() + seconds
        if exhausted:
            budget.turns_used = budget.turns
        return []
    seen = peer_turn(state, callback=shorten_budget, decision_engine=engine)
    assert seen['turn']['state'] == expected
    goals = [row for row in engine.asked if row[0] == 'judge' and row[1].purpose == 'goal-reached']
    assert len(goals) == calls
    assert state[0].store.job(seen['turn']['work_id'])['status'] == ('succeeded' if expected == 'completed' else 'failed')
