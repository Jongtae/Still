"""#868: qualification diagnostics and seedless cache application, without models or sockets."""
import json
import hashlib
import sys
from types import SimpleNamespace
from unittest import mock

import pytest

from agentos_eval import report, runner, sandbox, scenarios, scoring
from agentos_eval.client import AgentOSClient, ClientError
from personal_agent.decision_adapters import cli_fingerprint
from personal_agent.decision_qualification import SUITE_VERSION
from personal_agent.providers import ModelAdapter
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore
from personal_agent.subscription_engines import SubscriptionEngines
from test_decision_routes import CliRunner, FAKE_NATIVE, OpenAITransport, PLAN, cli_adapter


def status(ready=False, qualification=None):
    return {'available': ready, 'mode': 'follow_main', 'main': 'codex', 'engine': 'codex',
            'model': 'fixture', 'qualification': qualification or {}}


def client_for(initial, final=None):
    client = mock.Mock()
    client.judgment.return_value = initial
    client.wait_judgment.return_value = final or initial
    client.judgment_ready.side_effect = AgentOSClient.judgment_ready
    return client


def test_failed_wait_retains_only_bounded_qualification_reasons():
    client = client_for(status(), status(qualification={
        'state': 'failed', 'failure': 'timeout-' + 'f' * 250, 'message': 'reason-' + 'm' * 250,
        'unrelated': 'must not be recorded'}))
    result = runner.prepare_judgment(client, mock.Mock(), SimpleNamespace(cached=None), 'codex')
    assert result['ready'] is False
    assert result['initially_ready'] is False
    assert result['cache_applied'] is False
    assert result['requalified'] is True
    assert result['qualification'] == {
        'state': 'failed', 'failure': ('timeout-' + 'f' * 250)[:200], 'message': ('reason-' + 'm' * 250)[:200]}
    assert 'failure' not in result


@pytest.mark.parametrize('ready', [False, True])
def test_cache_application_and_initial_readiness_are_independent_of_requalification(ready):
    client = client_for(status(ready), status(True))
    box = SimpleNamespace(cached={'decision_route': 'fixture'}, cache_applied=True, slot=0,
                          read_config=lambda keys: {'decision_route': 'fresh'})
    pool = mock.Mock()
    result = runner.prepare_judgment(client, pool, box, 'codex')
    assert result['cache_applied'] is True
    assert result['initially_ready'] is ready
    assert result['requalified'] is (not ready)
    assert result['cached'] is ready  # legacy meaning is unchanged
    assert result['ready'] is True
    assert result['qualification'] == dict.fromkeys(('state', 'failure', 'message'))
    if ready:
        client.requalify_judgment.assert_not_called()
    else:
        client.requalify_judgment.assert_called_once()
        pool.forget_judgment.assert_called_once_with(0, 'codex')


def test_present_but_unapplied_cache_is_not_reported_as_applied():
    box = SimpleNamespace(cached={'decision_route': 'fixture'}, cache_applied=False, slot=0)
    result = runner.prepare_judgment(client_for(status(True)), mock.Mock(), box, 'codex')
    assert result['cache_applied'] is False
    assert result['cached'] is True  # existing field semantics remain unchanged


def test_requalification_client_error_keeps_failure_and_qualification():
    client = client_for(status(qualification={'state': 'interrupted', 'failure': 'cli-exited', 'message': 'Stopped'}))
    client.requalify_judgment.side_effect = ClientError('activation failed')
    result = runner.prepare_judgment(client, mock.Mock(), SimpleNamespace(cached=None), 'codex')
    assert result['failure'] == 'activation failed'
    assert result['qualification'] == {'state': 'interrupted', 'failure': 'cli-exited', 'message': 'Stopped'}
    assert result['cache_applied'] is False
    assert result['initially_ready'] is False
    assert result['requalified'] is True
    assert result['cached'] is False
    assert result['ready'] is False
    client.wait_judgment.assert_not_called()


def test_disabled_route_without_requalification_still_records_diagnostics():
    client = client_for(status())
    client.requalify_judgment.return_value = None
    result = runner.prepare_judgment(client, mock.Mock(), SimpleNamespace(cached=None), 'codex')
    assert result['requalified'] is False
    assert result['qualification'] == dict.fromkeys(('state', 'failure', 'message'))
    assert result['cache_applied'] is False
    assert result['initially_ready'] is False


def qualified_codex_rows(execution, data, binary):
    """Synthetic successful cache, shaped like DecisionRoutes._activate_cli/_commit.

    Generating the strict profile would launch CLI sandbox probes, so use the
    recorded-row shape here. The score/probe results are fixture evidence only.
    Fingerprints and the strict binding use the actual injected CLI and slot;
    production status validation is left intact.
    """
    fingerprint = cli_fingerprint(binary)
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    route = {
        'mode': 'follow_main', 'main': 'codex', 'transport': 'subscription_cli',
        'engine': 'codex', 'model_policy': 'explicit', 'requested_model': 'gpt-5.6-luna',
        'effort': 'low', 'activated_at': 1, 'cloud_profile': 'subscription',
        'fingerprint': fingerprint, 'cli_version': '0.153.4',
        'binary_identity': execution._strict_binary_identity('codex', str(binary)),
        'codex_disabled_features': list(PLAN), 'instruction_files_present': [],
        'strict_profile': {
            'platform': sys.platform, 'version': '0.153.4', 'checked_at': 1,
            'checks': ['strict-work-bridge-and-tool-surface',
                       *[f'instruction-files-{n}' for n in range(3)]],
            'binding': execution.strict_binding('codex', str(binary), data),
            'binary_sha256': digest, 'native_sha256': digest,
        },
        'qualification': {'suite_version': SUITE_VERSION, 'model': 'gpt-5.6-luna',
                          'score': 1, 'at': 1, 'cli_version': '0.153.4', 'fingerprint': fingerprint},
    }
    capability = {'version': '0.153.4', 'fingerprint': fingerprint, 'model_override': True,
                  'isolation_flags': True, 'tool_surface': 'allowlisted-features-only',
                  'codex_disabled_features': list(PLAN)}
    return {key: json.dumps(value) for key, value in {
        'decision_route': route, 'decision_cli_capabilities': {'codex': capability},
        'decision_route_checks': {'follow_main:codex': {'state': 'active', 'suite_version': SUITE_VERSION}},
        'decision_model': {},
    }.items()}


@pytest.mark.parametrize('seeded', [False, True])
def test_cached_rows_are_applied_when_store_exists_and_ready_on_first_read(tmp_path, monkeypatch, seeded):
    seed = tmp_path / 'seed' if seeded else None
    if seed:
        QuickStore(seed)
    binary = tmp_path / 'codex'
    binary.write_bytes(FAKE_NATIVE)
    finder = lambda name: str(binary) if name == 'codex' else None
    cli = CliRunner()
    execution = cli_adapter(cli, tmp_path, finder=finder)
    model = OpenAITransport()
    rows = qualified_codex_rows(execution, tmp_path / 'boxes' / 'slot-0', binary)
    services, readers = [], []
    box = sandbox.Sandbox(tmp_path / 'boxes', seed=seed, slot=0)
    box.cached = rows
    process = mock.Mock(poll=mock.Mock(return_value=None))
    monkeypatch.setattr(sandbox, 'reserve_port', lambda **kwargs: mock.Mock(port=18900))

    def spawn(*args, **kwargs):
        # Stand in for quickstart creating the seedless store at server startup.
        if not seeded:
            assert not (box.data / 'private' / 'quickstart.db').exists()
        store = QuickStore(box.data)  # quickstart's real initializer, not a config-only schema
        store.put('subscription_engine', {'id': 'codex', 'authentication': 'owner-confirmed-official-login'})
        service = AgentService(store, ModelAdapter(model), model,
                               subscription_engines=SubscriptionEngines(finder=finder),
                               execution_adapter=execution)
        services.append(service)
        readers.append(service.decision_routes)
        return process

    monkeypatch.setattr(sandbox.subprocess, 'Popen', spawn)
    try:
        with mock.patch.object(sandbox, 'write_config', wraps=sandbox.write_config) as write:
            box.start(config=rows)
            assert box.cache_applied is seeded
            assert write.call_count == int(seeded)
            service, reader = services[0], readers[0]
            # /api/state -> AgentService.settings -> DecisionRoutes.status is
            # the production path consumed by AgentOSClient.judgment. Keep the
            # same service/reader alive across the harness's deferred write.
            def transport(method, url, body, headers):
                assert method == 'GET', 'cache hit must not activate or requalify'
                if url.endswith('/healthz'):
                    payload = {'ok': True}
                else:
                    assert url.endswith('/api/state')
                    payload = {'settings': {'decision_route': reader.status()}}
                return 200, {}, json.dumps(payload).encode()

            client = AgentOSClient(box.url, transport=transport)
            before = client.judgment()
            assert AgentOSClient.judgment_ready(before, 'codex') is seeded
            if not seeded:
                assert reader.active() is None
            assert box.wait_healthy(client) is True
            assert write.call_count == 1
            assert box.cache_applied is True
            assert box.read_config(sandbox.JUDGMENT_KEYS) == rows
            assert service.decision_routes is reader
            assert client.judgment_ready(client.judgment(), 'codex') is True
            assert reader.active()['transport'] == 'subscription_cli'
            active = reader.status()['active']
            assert active['available'] is True
            assert active['requalification_needed'] is False
            assert active['strict_profile_unqualified'] is False
            assert active['instruction_files_unqualified'] is False
            result = runner.prepare_judgment(client, mock.Mock(), box, 'codex')
            assert result['ready'] is True
            assert result['cache_applied'] is True
            assert result['initially_ready'] is True
            assert result['requalified'] is False
            assert not model.calls  # status reads and cache reuse invoke no model
            assert not cli.calls
            box.wait_healthy(client)
            assert write.call_count == 1  # never reapply over a later qualification
    finally:
        process.poll.return_value = 0
        box.stop()


@pytest.mark.parametrize('scored', [False, True])
def test_inspect_scoring_and_report_preserve_new_judgment_fields(scored):
    judgment = runner.prepare_judgment(client_for(status(True)), mock.Mock(), SimpleNamespace(cached=None), 'codex')
    run = {'judgment': judgment, 'worker': 'codex', 'turns': []}
    scenario = scenarios.validate({'id': 'cache-reasons', 'turns': ['Hi']})
    value, metadata = scoring.combine(scenario, run, {}, [])
    assert metadata['judgment_ai'] == judgment
    sample = SimpleNamespace(id='cache-reasons@codex', epoch=1, metadata={'run': run},
                             scores={report.SCORER: SimpleNamespace(value=value, metadata=metadata)} if scored else {})
    record = report.records_from_samples([sample])[0]
    assert record['judgment_ai']['qualification'] == dict.fromkeys(('state', 'failure', 'message'))
    assert record['judgment_ai']['cache_applied'] is False
    assert record['judgment_ai']['initially_ready'] is True


def test_a_check_already_running_is_awaited_not_requested_again():
    running = status(qualification={'state': 'running'})
    client = client_for(running, status(True))
    result = runner.prepare_judgment(client, mock.Mock(), SimpleNamespace(cached=None), 'codex')
    client.requalify_judgment.assert_not_called()
    client.wait_judgment.assert_called_once()
    assert result['ready'] is True and 'failure' not in result


def test_a_400_while_a_check_runs_is_awaited_too():
    from agentos_eval.client import ClientError
    client = client_for(status(), status(True))
    client.requalify_judgment.side_effect = ClientError('400', 400)
    client.judgment.side_effect = [status(), status(qualification={'state': 'running'})]
    result = runner.prepare_judgment(client, mock.Mock(), SimpleNamespace(cached=None), 'codex')
    assert result['ready'] is True and 'failure' not in result
