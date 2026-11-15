"""#855 Task C: model-free synthetic seed construction and failure cleanup."""
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evals'))
from agentos_eval import judgments, synthetic_seed as seed


@pytest.fixture(autouse=True)
def fixture_location(tmp_path, monkeypatch):
    # All stores here are model-free. Production still refuses the real system
    # temp roots; make this one filesystem seam explicit for fixture stores.
    monkeypatch.setenv('AGENTOS_EVAL_HOME', str(tmp_path / 'eval-home'))
    monkeypatch.setattr(seed, '_system_temp_roots', lambda: {Path('/fixture-system-temp')})


@pytest.fixture
def factory():
    production_factory = judgments.service_factory

    def fresh(path):
        # Cross the real production constructor/schema boundary; replace only
        # route activation/status so no CLI or provider is called.
        service = production_factory(path)
        service.decision_routes = SimpleNamespace(
            activate=Mock(), status=lambda: {
                'active': {'transport': 'subscription_cli', 'engine': 'codex',
                           'available': True, 'secret': 'must-not-appear'},
                'effective': {'state': 'ready'}, 'suite_version': 'fixture'})
        return service
    return Mock(side_effect=fresh)


def test_empty_production_layout_and_requested_activation(tmp_path, factory):
    out = tmp_path / 'seed'
    seen = []

    def activate(service, body):
        assert service.store.root == out
        seen.append(body)

    result = seed.build(out, model='fixture-model', effort='high', factory=factory, activate=activate)
    assert seen == [{'transport': 'subscription_cli', 'engine': 'codex',
                     'model_policy': 'explicit', 'model': 'fixture-model', 'effort': 'high'}]
    with sqlite3.connect(out / 'private' / 'quickstart.db') as db:
        for table in ('memories', 'memory_candidates', 'messages', 'jobs', 'notes', 'owner_model_upkeep'):
            assert db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 0
        keys = {row[0] for row in db.execute('SELECT key FROM config')}
        assert not keys.intersection({'model', 'subscription_engine', 'owner_model',
                                      'ai_connections', 'ai_mcp_servers'})
    assert not (out / 'private' / 'connections.json').exists()
    assert result['owner_data'] == 'none'
    assert result['engine'] == 'codex'
    assert result['requested_model'] == 'fixture-model'
    assert result['requested_effort'] == 'high'
    assert result['created_at'] and 'git_head' in result
    assert result['runtime']['effective']['state'] == 'ready'
    assert 'must-not-appear' not in (out / 'synthetic-seed.json').read_text()
    assert json.loads((out / 'synthetic-seed.json').read_text()) == result


def test_default_activation_uses_shipped_owner_method(tmp_path, factory):
    services = []

    def make(path):
        services.append(factory(path))
        return services[-1]

    out = tmp_path / 'empty-existing'
    out.mkdir()
    seed.build(out, engine='claude-code', factory=make)
    services[0].decision_routes.activate.assert_called_once_with({
        'transport': 'subscription_cli', 'engine': 'claude-code', 'model_policy': 'fastest_qualified'})


def test_nonempty_output_refused(tmp_path, factory):
    original = tmp_path / 'original'
    original.write_text('preserve')
    activate = Mock()
    with pytest.raises(ValueError, match='must be empty'):
        seed.build(tmp_path, factory=factory, activate=activate)
    assert original.read_text() == 'preserve'
    factory.assert_not_called()
    activate.assert_not_called()


def test_live_output_and_alias_refused(tmp_path, monkeypatch, factory):
    live = tmp_path / 'live'
    live.mkdir()
    alias = tmp_path / 'alias'
    alias.symlink_to(live, target_is_directory=True)
    monkeypatch.setattr(seed, 'LIVE_DATA', live)
    for out in (live, alias):
        with pytest.raises(ValueError, match='live data folder'):
            seed.build(out, factory=factory)
    factory.assert_not_called()
    assert live.is_dir()


def test_activation_failure_cleans_output(tmp_path, factory):
    out = tmp_path / 'seed'
    activate = Mock(side_effect=ValueError('shipped qualification failure'))
    with pytest.raises(ValueError, match='shipped qualification failure'):
        seed.build(out, factory=factory, activate=activate)
    assert not out.exists()


def test_construction_failure_cleans_output(tmp_path):
    out = tmp_path / 'seed'
    with pytest.raises(RuntimeError, match='construction failed'):
        seed.build(out, factory=Mock(side_effect=RuntimeError('construction failed')))
    assert not out.exists()


def test_main_activation_error_is_shipped_message(tmp_path, monkeypatch, capsys, factory):
    def make(path):
        service = factory(path)
        service.decision_routes.activate.side_effect = ValueError('shipped qualification failure')
        return service

    monkeypatch.setattr(judgments, 'service_factory', make)
    out = tmp_path / 'seed'
    assert seed.main(['--out', str(out)]) == 1
    captured = capsys.readouterr()
    assert captured.err == 'shipped qualification failure\n'
    assert not captured.out
    assert not out.exists()


@pytest.mark.parametrize('options,policy,trials', [([], 'fastest_qualified', 3),
    (['--model', 'fixture-model', '--effort', 'high'], 'explicit', 1)])
def test_dry_run_calls_nothing(tmp_path, monkeypatch, capsys, options, policy, trials):
    factory, build = Mock(side_effect=AssertionError('must not construct')), Mock(side_effect=AssertionError('must not build'))
    monkeypatch.setattr(judgments, 'service_factory', factory)
    monkeypatch.setattr(seed, 'build', build)
    out = tmp_path / 'seed'
    assert seed.main(['--out', str(out), '--dry-run', *options]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan['activate']['model_policy'] == policy
    assert plan['activate']['transport'] == 'subscription_cli'
    assert plan['suite_calls'] == len(judgments.still().decision_qualification.CASES) == 11
    assert plan['max_calls_per_candidate'] == 11 * trials
    factory.assert_not_called()
    build.assert_not_called()
    assert not out.exists()


def test_bare_output_name_uses_eval_home_seeds(tmp_path):
    assert seed._output('synthetic-terra') == tmp_path / 'eval-home/seeds/synthetic-terra'


def test_system_temp_output_refused_before_construction(tmp_path, monkeypatch, factory):
    monkeypatch.setattr(seed, '_system_temp_roots', lambda: {tmp_path})
    with pytest.raises(ValueError, match='protected-directory-denied'):
        seed.build(tmp_path / 'rejected', model='fixture-model', factory=factory)
    factory.assert_not_called()
    assert not (tmp_path / 'rejected').exists()
