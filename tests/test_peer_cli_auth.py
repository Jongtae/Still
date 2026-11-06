"""#673 pass 2 owner decision: synthetic auth refresh preservation; no CLI/model."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import threading

import pytest

from personal_agent.bounded_execution import (BoundedExecutionAdapter, EngineInterrupted, ExecutionError,
    RelayedWorkMcpTools, PEER_AUTH_MAX_AGE_SECONDS)
from personal_agent.agent_runtime import Capabilities, WorkBudget
from personal_agent.quickstart_store import QuickStore


def auth_value(token='SYNTHETIC-ACCESS-TOKEN-673', age=0):
    return json.dumps({'auth_mode': 'chatgpt', 'tokens': {'access_token': token,
        'refresh_token': token + '-refresh'},
        'last_refresh': (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat()}).encode()


@pytest.fixture
def auth_state(tmp_path):
    home = tmp_path / 'synthetic-owner'
    home.mkdir(mode=0o700)
    original = home / 'auth.json'
    original.write_bytes(auth_value())
    original.chmod(0o600)
    run = tmp_path / 'turn'
    run.mkdir(mode=0o700)
    store = QuickStore(tmp_path / 'store')
    work = store.enqueue('Synthetic peer auth cleanup', 'peer-auth-673')
    adapter = BoundedExecutionAdapter(codex_home=home, runtime_root=tmp_path / 'runtime',
        finder=lambda name: '/synthetic/codex', credentials=lambda name: '')
    return adapter, original, run, store, work


@pytest.mark.parametrize('exit_path', ['success', 'failure', 'exception', 'cancellation', 'block'])
def test_auth_refresh_written_back_in_every_scope_exit(auth_state, exit_path, caplog):
    adapter, original, run, store, work = auth_state
    refreshed = auth_value('ROTATED-SYNTHETIC-TOKEN-673')
    try:
        with adapter.peer_environment_scope('codex', '/synthetic/codex', run, store_root=store.root, work_id=work) as env:
            (Path(env['CODEX_HOME']) / 'auth.json').write_bytes(refreshed)
            assert 'TOKEN-673' not in json.dumps(env)
            if exit_path == 'block':
                with store.db() as db:
                    db.execute("UPDATE jobs SET status='cancelled' WHERE id=?", (work,))
            if exit_path != 'success':
                raise RuntimeError('synthetic exit')
    except RuntimeError:
        pass
    assert original.read_bytes() == refreshed
    assert original.stat().st_mode & 0o777 == 0o600
    assert original.stat().st_uid == os.getuid()
    assert not original.is_symlink()
    assert not list(original.parent.glob('.auth-peer-*'))
    assert 'TOKEN-673' not in caplog.text
    assert 'TOKEN-673' not in json.dumps(store.task_events(work))


def test_original_changed_kept_and_redacted_conflict_recorded_after_block(auth_state, caplog):
    adapter, original, run, store, work = auth_state
    concurrent = auth_value('CONCURRENT-OWNER-TOKEN-673')
    with adapter.peer_environment_scope('codex', '/synthetic/codex', run, store_root=store.root, work_id=work) as env:
        (Path(env['CODEX_HOME']) / 'auth.json').write_bytes(auth_value('PEER-ROTATED-TOKEN-673'))
        original.write_bytes(concurrent)
        with store.db() as db:
            db.execute("UPDATE jobs SET status='cancelled' WHERE id=?", (work,))
    assert original.read_bytes() == concurrent
    events = store.task_events(work)
    assert events[-1]['trace']['code'] == 'peer_auth_writeback_conflict'
    assert 'TOKEN-673' not in json.dumps(events) + caplog.text
    assert not list(original.parent.glob('.auth-peer-*'))


def test_original_changed_while_staging_is_rechecked(auth_state, monkeypatch):
    adapter, original, run, store, work = auth_state
    concurrent = auth_value('OWNER-WRITES-DURING-FSYNC')
    real_fsync = os.fsync
    def fsync(fd):
        original.write_bytes(concurrent)
        return real_fsync(fd)
    with adapter.peer_environment_scope('codex', '/synthetic/codex', run, store_root=store.root, work_id=work) as env:
        (Path(env['CODEX_HOME']) / 'auth.json').write_bytes(auth_value('PEER-ROTATED'))
        monkeypatch.setattr(os, 'fsync', fsync)
    assert original.read_bytes() == concurrent
    assert store.task_events(work)[-1]['trace']['code'] == 'peer_auth_writeback_conflict'


@pytest.mark.parametrize('change', ['symlink', 'permissions', 'removed'])
def test_unsafe_original_at_writeback_is_never_overwritten(auth_state, change):
    adapter, original, run, store, work = auth_state
    untouched = original.parent / 'unrelated'
    untouched.write_bytes(b'unrelated owner original')
    with adapter.peer_environment_scope('codex', '/synthetic/codex', run, store_root=store.root, work_id=work) as env:
        (Path(env['CODEX_HOME']) / 'auth.json').write_bytes(auth_value('ROTATED'))
        if change == 'permissions':
            original.chmod(0o644)
        else:
            original.unlink()
            if change == 'symlink':
                original.symlink_to(untouched)
    assert untouched.read_bytes() == b'unrelated owner original'
    assert store.task_events(work)[-1]['trace']['code'] == 'peer_auth_writeback_conflict'
    if change == 'symlink':
        assert original.is_symlink()
    elif change == 'permissions':
        assert original.stat().st_mode & 0o777 == 0o644
    else:
        assert not original.exists()


@pytest.mark.parametrize('age', [PEER_AUTH_MAX_AGE_SECONDS, 8 * 86400, -3600, None, 'invalid'])
def test_due_soon_or_unknown_refresh_age_refused_before_copy(auth_state, age):
    adapter, original, run, store, work = auth_state
    if isinstance(age, (int, float)):
        original.write_bytes(auth_value(age=age))
    else:
        data = json.loads(auth_value())
        data['last_refresh'] = age
        original.write_text(json.dumps(data))
    with pytest.raises(ExecutionError, match='normal owner Codex use refreshes') as caught:
        with adapter.peer_environment_scope('codex', '/synthetic/codex', run):
            pytest.fail('stale copy admitted')
    assert caught.value.reason.startswith('peer-auth-refresh-due:')
    assert not (run / 'codex-auth').exists()


def test_peer_turns_serialize_the_entire_copy_run_writeback_lifetime(auth_state):
    adapter, original, run, store, work = auth_state
    other = run.parent / 'second-turn'
    other.mkdir(mode=0o700)
    waiting, entered = threading.Event(), threading.Event()
    failures = []
    refreshed = auth_value('FIRST-ROTATION')
    def second():
        waiting.set()
        try:
            with adapter.peer_environment_scope('codex', '/synthetic/codex', other) as env:
                entered.set()
                assert (Path(env['CODEX_HOME']) / 'auth.json').read_bytes() == refreshed
        except BaseException as exc:
            failures.append(exc)
    with adapter.peer_environment_scope('codex', '/synthetic/codex', run) as env:
        thread = threading.Thread(target=second)
        thread.start()
        assert waiting.wait(2)
        assert not entered.wait(.1)
        (Path(env['CODEX_HOME']) / 'auth.json').write_bytes(refreshed)
    thread.join(3)
    assert not thread.is_alive() and entered.is_set() and not failures


@pytest.mark.parametrize('exit_path', ['success', 'failure', 'exception', 'cancellation', 'block', 'due-soon'])
def test_worker_refresh_cleanup_uses_production_execute(auth_state, monkeypatch, exit_path, caplog):
    adapter, original, run, store, work = auth_state
    refreshed = auth_value('WORKER-ROTATED-TOKEN-673')
    if exit_path == 'due-soon':
        original.write_bytes(auth_value(age=7 * 86400))
    caps = Capabilities(store, None, {}, '', work, lambda *args: None,
        allowed_tools={'save_memory'}, delegated=True, budget=WorkBudget(), secret_redactor=lambda text: text)
    caps.peer_guard = lambda: None
    tools = RelayedWorkMcpTools(caps)
    tools.browser_relay = 'synthetic-relay'
    calls = []
    def runner(argv, **kwargs):
        calls.append(argv)
        assert 'TOKEN-673' not in json.dumps([argv, kwargs['env']])
        (Path(kwargs['env']['CODEX_HOME']) / 'auth.json').write_bytes(refreshed)
        if exit_path == 'block':
            with store.db() as db:
                db.execute("UPDATE jobs SET status='cancelled' WHERE id=?", (work,))
            raise EngineInterrupted('stopped')
        if exit_path == 'cancellation':
            raise EngineInterrupted('stopped')
        if exit_path == 'exception':
            raise OSError('synthetic start failure')
        return subprocess.CompletedProcess(argv, 1 if exit_path == 'failure' else 0,
            json.dumps({'item': {'type': 'agent_message', 'text': 'answer'}}), '')
    adapter.runner = runner
    monkeypatch.setattr(adapter, 'bridge_check', lambda: '')
    monkeypatch.setattr(adapter, '_strict_binary_identity', lambda *args: 'current')
    monkeypatch.setattr(adapter, 'qualified_identity', lambda *args, **kwargs: {
        'qualified': True, 'identity': 'current', 'disabled_features': ['shell_tool', 'view_image']})
    if exit_path == 'success':
        assert adapter.execute('codex', 'Synthetic peer prompt', tools).content == 'answer'
    else:
        with pytest.raises(ExecutionError):
            adapter.execute('codex', 'Synthetic peer prompt', tools)
    assert len(calls) == (0 if exit_path == 'due-soon' else 1)
    if exit_path != 'due-soon':
        assert original.read_bytes() == refreshed
    assert not list(adapter.runtime_root.glob('turn-*'))
    assert 'TOKEN-673' not in json.dumps(store.task_events(work)) + caplog.text


@pytest.mark.parametrize('exit_path', ['success', 'failure', 'exception', 'cancellation', 'due-soon'])
def test_judgment_refresh_cleanup_before_temporary_removal(auth_state, monkeypatch, exit_path, caplog):
    from personal_agent.decision import DecisionContext
    from personal_agent.decision_adapters import SubscriptionCliDecisionEngine
    adapter, original, run, store, work = auth_state
    refreshed = auth_value('JUDGMENT-ROTATED-TOKEN-673')
    if exit_path == 'due-soon':
        original.write_bytes(auth_value(age=7 * 86400))
    calls = []
    def runner(argv, **kwargs):
        calls.append(argv)
        assert 'TOKEN-673' not in json.dumps([argv, kwargs['env']])
        (Path(kwargs['env']['CODEX_HOME']) / 'auth.json').write_bytes(refreshed)
        if exit_path == 'exception':
            raise OSError('synthetic failure')
        if exit_path == 'cancellation':
            raise EngineInterrupted('stopped')
        return subprocess.CompletedProcess(argv, 1 if exit_path == 'failure' else 0,
            json.dumps({'item': {'type': 'agent_message', 'text': '{"choice":"yes"}'}}), '')
    adapter.runner = runner
    monkeypatch.setattr(adapter, '_strict_binary_identity', lambda *args: 'current')
    monkeypatch.setattr(adapter, 'qualified_identity', lambda *args, **kwargs: {
        'qualified': True, 'identity': 'current', 'disabled_features': ['shell_tool', 'view_image']})
    judge = SubscriptionCliDecisionEngine(adapter, 'codex', codex_disabled_features=['shell_tool'])
    context = DecisionContext('peer-planning', {'request': 'hello'}, work_id=work)
    context.peer_store_root = store.root
    schema = {'type': 'object', 'properties': {'choice': {'type': 'string'}}, 'required': ['choice'], 'additionalProperties': False}
    if exit_path == 'cancellation':
        with pytest.raises(EngineInterrupted):
            judge.structured(context, 'Choose yes', schema)
    else:
        result = judge.structured(context, 'Choose yes', schema)
        if exit_path == 'due-soon':
            assert judge.last_failure.startswith('peer-auth-refresh-due:')
    assert len(calls) == (0 if exit_path == 'due-soon' else 1)
    if exit_path != 'due-soon':
        assert original.read_bytes() == refreshed
    assert not list(adapter.runtime_root.glob('turn-*'))
    assert 'TOKEN-673' not in json.dumps(store.task_events(work)) + caplog.text


def test_writeback_is_same_directory_atomic_fsynced_and_owner_only(auth_state, monkeypatch):
    adapter, original, run, store, work = auth_state
    replacement = auth_value('ATOMIC-ROTATION')
    real_replace, real_fsync = os.replace, os.fsync
    synced, replaced = [], []
    def fsync(fd):
        synced.append(fd)
        return real_fsync(fd)
    def replace(src, dst, **kwargs):
        assert dst == 'auth.json' and src.startswith('.auth-peer-')
        assert kwargs['src_dir_fd'] == kwargs['dst_dir_fd']
        info = os.stat(src, dir_fd=kwargs['src_dir_fd'], follow_symlinks=False)
        assert info.st_mode & 0o777 == 0o600 and info.st_uid == os.getuid()
        assert synced  # file flush/fsync precedes replace
        replaced.append(True)
        return real_replace(src, dst, **kwargs)
    monkeypatch.setattr(os, 'fsync', fsync)
    monkeypatch.setattr(os, 'replace', replace)
    with adapter.peer_environment_scope('codex', '/synthetic/codex', run) as env:
        (Path(env['CODEX_HOME']) / 'auth.json').write_bytes(replacement)
    assert replaced and len(synced) == 2  # staged file and containing directory
    assert original.read_bytes() == replacement


def test_writeback_io_failure_records_only_fixed_code_and_removes_staging(auth_state, monkeypatch, caplog):
    adapter, original, run, store, work = auth_state
    unchanged = original.read_bytes()
    def refused(*args, **kwargs):
        raise OSError('SYNTHETIC-SECRET-IN-EXCEPTION-673')
    with adapter.peer_environment_scope('codex', '/synthetic/codex', run, store_root=store.root, work_id=work) as env:
        (Path(env['CODEX_HOME']) / 'auth.json').write_bytes(auth_value('UPDATED-SECRET-673'))
        monkeypatch.setattr(os, 'replace', refused)
    assert original.read_bytes() == unchanged
    assert not list(original.parent.glob('.auth-peer-*'))
    events = store.task_events(work)
    assert events[-1]['trace']['code'] == 'peer_auth_writeback_unavailable'
    assert 'SECRET-673' not in caplog.text + json.dumps(events)
    assert 'SECRET-IN-EXCEPTION-673' not in caplog.text


def test_wrong_uid_original_refused_before_copy(auth_state, monkeypatch):
    adapter, original, run, store, work = auth_state
    monkeypatch.setattr(os, 'getuid', lambda: original.stat().st_uid + 1)
    with pytest.raises(ExecutionError, match='safe auth-only'):
        with adapter.peer_environment_scope('codex', '/synthetic/codex', run):
            pytest.fail('wrong uid admitted')
    assert not (run / 'codex-auth').exists()


def test_real_process_cancellation_persists_rotated_copy(auth_state, monkeypatch):
    """Real process-group cleanup with a synthetic Python CLI, no model/socket."""
    import sys
    adapter, original, run, store, work = auth_state
    program = run.parent / 'synthetic-cli.py'
    program.write_text('''import json, os, pathlib, time
path = pathlib.Path(os.environ['CODEX_HOME']) / 'auth.json'
data = json.loads(path.read_bytes())
data['tokens']['access_token'] += '-rotated'
path.write_text(json.dumps(data))
pathlib.Path('ready').touch()
time.sleep(30)
''')
    caps = Capabilities(store, None, {}, '', work, lambda *args: None,
        allowed_tools={'save_memory'}, delegated=True,
        budget=WorkBudget(stop=lambda: any(adapter.runtime_root.glob('turn-*/ready'))),
        secret_redactor=lambda text: text)
    caps.peer_guard = lambda: None
    tools = RelayedWorkMcpTools(caps)
    tools.browser_relay = 'synthetic-relay'
    adapter.runner = subprocess.run
    monkeypatch.setattr(adapter, 'bridge_check', lambda: '')
    monkeypatch.setattr(adapter, '_strict_binary_identity', lambda *args: 'current')
    monkeypatch.setattr(adapter, 'qualified_identity', lambda *args, **kwargs: {
        'qualified': True, 'identity': 'current', 'disabled_features': ['shell_tool', 'view_image']})
    monkeypatch.setattr(adapter, 'command', lambda *args, **kwargs: [sys.executable, str(program)])
    with pytest.raises(ExecutionError) as caught:
        adapter.execute('codex', 'Synthetic cancellation test', tools)
    assert caught.value.failure_class == 'stopped'
    assert json.loads(original.read_bytes())['tokens']['access_token'].endswith('-rotated')
    assert not list(adapter.runtime_root.glob('turn-*'))


@pytest.mark.parametrize('reason', ['stopped', 'deadline_exceeded'])
def test_waiting_auth_copy_respects_stop_and_deadline(auth_state, reason):
    import time
    adapter, original, run, store, work = auth_state
    waiting = run.parent / 'waiting-turn'
    waiting.mkdir()
    seen = []
    def second():
        try:
            with adapter.peer_environment_scope('codex', '/synthetic/codex', waiting,
                interrupted=(lambda: 'stopped') if reason == 'stopped' else None,
                deadline=time.monotonic() - 1 if reason == 'deadline_exceeded' else None):
                pytest.fail('cancelled/deadline copy admitted')
        except ExecutionError as exc:
            seen.append(exc.failure_class)
    with adapter.peer_environment_scope('codex', '/synthetic/codex', run):
        thread = threading.Thread(target=second)
        thread.start()
        thread.join(3)
        assert not thread.is_alive()
        assert seen == [reason]
    assert not (waiting / 'codex-auth').exists()
