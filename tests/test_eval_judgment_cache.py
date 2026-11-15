"""#868: qualification diagnostics and seedless cache application, without models or sockets."""
import json
import sqlite3
from types import SimpleNamespace
from unittest import mock

import pytest

from agentos_eval import report, runner, sandbox, scenarios, scoring
from agentos_eval.client import AgentOSClient, ClientError


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


def create_store(data):
    private = data / 'private'
    private.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(private / 'quickstart.db') as db:
        db.execute('CREATE TABLE IF NOT EXISTS config(key TEXT PRIMARY KEY, value TEXT)')


@pytest.mark.parametrize('seeded', [False, True])
def test_cached_rows_are_applied_when_store_exists_and_ready_on_first_read(tmp_path, monkeypatch, seeded):
    seed = tmp_path / 'seed' if seeded else None
    if seed:
        create_store(seed)
    rows = {'decision_route': json.dumps({'available': True}),
            'decision_route_checks': '{}', 'decision_cli_capabilities': '{}', 'decision_model': '{}'}
    box = sandbox.Sandbox(tmp_path / 'boxes', seed=seed, slot=0)
    box.cached = rows
    process = mock.Mock(poll=mock.Mock(return_value=None))
    monkeypatch.setattr(sandbox, 'reserve_port', lambda **kwargs: mock.Mock(port=18900))

    def spawn(*args, **kwargs):
        # Stand in for quickstart creating the seedless store at server startup.
        if not seeded:
            assert not (box.data / 'private' / 'quickstart.db').exists()
            create_store(box.data)
        return process

    monkeypatch.setattr(sandbox.subprocess, 'Popen', spawn)
    try:
        with mock.patch.object(sandbox, 'write_config', wraps=sandbox.write_config) as write:
            box.start(config=rows)
            assert box.cache_applied is seeded
            assert write.call_count == int(seeded)
            client = client_for(status())
            client.healthy.return_value = True
            assert box.wait_healthy(client) is True
            assert write.call_count == 1
            assert box.cache_applied is True
            assert box.read_config(sandbox.JUDGMENT_KEYS) == rows
            # Model-free server read: readiness depends on the actual SQLite row,
            # not on the cache argument passed into start.
            client.judgment.side_effect = lambda: status(json.loads(box.read_config(['decision_route'])['decision_route'])['available'])
            result = runner.prepare_judgment(client, mock.Mock(), box, 'codex')
            assert result['ready'] is True
            assert result['cache_applied'] is True
            assert result['initially_ready'] is True
            assert result['requalified'] is False
            client.requalify_judgment.assert_not_called()
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
