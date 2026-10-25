"""#309: model-free qualification observations, never cached authority."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from personal_agent import bounded_execution as be

VERSION = '0.153.4'
NATIVE = b'\x7fELFfake-native'


@pytest.fixture
def setup(tmp_path, monkeypatch):
    be._STRICT_OBSERVATIONS.clear()
    launcher = tmp_path / 'codex'
    native = tmp_path / 'native'
    launcher.write_bytes(b'launcher')
    native.write_bytes(NATIVE)
    runner = Mock(return_value=SimpleNamespace(returncode=0, stdout=f'codex-cli {VERSION}'))
    adapter = be.BoundedExecutionAdapter(runner=runner, runtime_root=tmp_path / 'runs')
    monkeypatch.setattr(adapter, 'native_cli_binary', lambda *_: str(native))
    monkeypatch.setattr(adapter, 'environment', lambda *_: {})
    digest = Mock(wraps=adapter._sha256)
    monkeypatch.setattr(adapter, '_sha256', digest)
    record = dict(binary_sha256=hashlib.sha256(launcher.read_bytes()).hexdigest(),
                  native_sha256=hashlib.sha256(native.read_bytes()).hexdigest(), version=VERSION)

    def observe(qualification=None, binary=None, **kwargs):
        return adapter.strict_observation('codex', str(binary or launcher),
                                          record if qualification is None else qualification,
                                          tmp_path, {VERSION}, **kwargs)
    yield SimpleNamespace(**locals())
    be._STRICT_OBSERVATIONS.clear()


def test_unchanged_and_record_comparison(setup):
    s = setup
    assert s.observe() == s.observe() == ([], VERSION)
    assert s.digest.call_count == 2  # one hash of each distinct file
    assert s.runner.call_count == 1
    assert s.observe({**s.record, 'native_sha256': 'wrong'}) == (['native_sha256'], None)
    assert s.observe() == ([], VERSION)
    assert s.digest.call_count == 4  # mismatch evicts even an earlier success
    assert s.runner.call_count == 2


@pytest.mark.parametrize('which', ['launcher', 'native'])
@pytest.mark.parametrize('change', ['size', 'mtime', 'inode', 'device'])
def test_identity_changes_recompute(setup, monkeypatch, which, change):
    s = setup
    assert s.observe() == ([], VERSION)
    path = getattr(s, which)
    stat = path.stat()
    if change == 'size':
        path.write_bytes(path.read_bytes() + b'!')
        s.record['binary_sha256' if which == 'launcher' else 'native_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    elif change == 'mtime':
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    elif change == 'inode':
        replacement = path.with_suffix('.new')
        replacement.write_bytes(path.read_bytes())
        os.utime(replacement, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        os.replace(replacement, path)
        assert path.stat().st_ino != stat.st_ino
    else:
        real_stat = be.os.stat

        def changed_stat(candidate, *args, **kwargs):
            result = real_stat(candidate, *args, **kwargs)
            if os.fspath(candidate) == str(path):
                return SimpleNamespace(st_size=result.st_size, st_mtime_ns=result.st_mtime_ns,
                                       st_ino=result.st_ino, st_dev=result.st_dev + 1)
            return result
        monkeypatch.setattr(be.os, 'stat', changed_stat)
    assert s.observe() == ([], VERSION)
    assert s.digest.call_count == 4
    assert s.runner.call_count == 2


@pytest.mark.parametrize('failure', ['nonzero', 'unparsable', 'exception', 'version', 'digest', 'missing'])
def test_failure_never_cached(setup, failure):
    s = setup
    if failure == 'nonzero':
        s.runner.return_value.returncode = 1
    elif failure == 'unparsable':
        s.runner.return_value.stdout = 'garbled'
    elif failure == 'exception':
        s.runner.side_effect = OSError('not executable')
    elif failure == 'version':
        s.record['version'] = '0.0.0'
    elif failure == 'digest':
        s.record['native_sha256'] = 'wrong'
    else:
        s.native.unlink()
    observed = s.observe()
    if failure == 'version':
        assert observed == ([], VERSION)  # caller still rejects the recorded version
        assert observed[1] != s.record['version']
    else:
        assert observed != ([], VERSION)
    assert not be._STRICT_OBSERVATIONS
    s.runner.side_effect = None
    s.runner.return_value = SimpleNamespace(returncode=0, stdout=f'codex-cli {VERSION}')
    s.native.write_bytes(NATIVE)
    s.record.update(version=VERSION, native_sha256=hashlib.sha256(NATIVE).hexdigest())
    before = s.digest.call_count
    assert s.observe() == ([], VERSION)
    assert s.digest.call_count == before + 2
    assert s.runner.call_count == (1 if failure in ('digest', 'missing') else 2)


def test_distinct_paths_never_share_observation(setup):
    s = setup
    assert s.observe() == ([], VERSION)
    other = s.launcher.with_name('other')
    os.link(s.launcher, other)
    assert s.launcher.stat() == other.stat()
    assert s.observe(binary=other) == ([], VERSION)
    assert s.digest.call_count == 4
    assert s.runner.call_count == 2


def test_changed_digest_refuses_despite_previous_identity_success(setup):
    s = setup
    assert s.observe() == ([], VERSION)
    s.native.write_bytes(NATIVE + b'changed')
    assert s.observe() == (['native_sha256'], None)
    assert s.observe() == (['native_sha256'], None)
    assert s.digest.call_count == 6
    assert s.runner.call_count == 1


def test_threads_share_one_successful_computation(setup):
    s = setup
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(lambda _: s.observe(), range(20))) == [([], VERSION)] * 20
    assert s.digest.call_count == 2
    assert s.runner.call_count == 1


def test_memo_is_bounded(setup):
    s = setup
    for index in range(12):
        path = s.launcher.with_name(f'codex-{index}')
        path.write_bytes(s.launcher.read_bytes())
        assert s.observe(binary=path) == ([], VERSION)
    assert len(be._STRICT_OBSERVATIONS) == 8


def test_version_record_rechecked_on_hit(setup):
    s = setup
    assert s.observe() == ([], VERSION)
    observed = s.observe({**s.record, 'version': '0.0.0'})
    assert observed[1] != '0.0.0'
    assert not be._STRICT_OBSERVATIONS
    assert s.observe() == ([], VERSION)
    assert s.runner.call_count == 2


def test_exception_does_not_cache_partial_computation(setup):
    s = setup
    s.runner.side_effect = RuntimeError('unexpected runner failure')
    with pytest.raises(RuntimeError):
        s.observe()
    assert not be._STRICT_OBSERVATIONS
    s.runner.side_effect = None
    assert s.observe() == ([], VERSION)
    assert s.digest.call_count == 4
    assert s.runner.call_count == 2


def test_native_path_change_recomputes(setup, monkeypatch):
    s = setup
    assert s.observe() == ([], VERSION)
    other = s.native.with_name('other-native')
    other.write_bytes(s.native.read_bytes())
    stat = s.native.stat()
    os.utime(other, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    monkeypatch.setattr(s.adapter, 'native_cli_binary', lambda *_: str(other))
    assert s.observe() == ([], VERSION)
    assert s.digest.call_count == 4
    assert s.runner.call_count == 2


@pytest.mark.parametrize('first', ['judgment', 'work'])
def test_judgments_and_work_share_memo_and_keep_refusal_reasons(setup, monkeypatch, first):
    import json
    from personal_agent.decision_routes import DecisionRoutes

    s = setup
    monkeypatch.setattr(be.sys, 'platform', 'darwin')
    monkeypatch.setattr(s.adapter, 'finder', lambda _: str(s.launcher))
    monkeypatch.setattr(s.adapter, 'environment', lambda *args, **kwargs: {})
    monkeypatch.setattr(s.adapter, 'bridge_check', lambda: None)
    root = SimpleNamespace(root=s.tmp_path)
    caps = SimpleNamespace(store=root, job_id='test', private_provenance=set())
    record = {**s.record, 'binding': s.adapter.strict_binding('codex', str(s.launcher), s.tmp_path),
              'disabled_features': ['shell_tool']}
    routes = DecisionRoutes.__new__(DecisionRoutes)
    routes.service = SimpleNamespace(execution_adapter=s.adapter)
    routes.store = root
    routes.codex_instruction_qualified = {VERSION}
    tools = be.StrictIsolatedAgentOSMcpTools(caps, qualification=record)

    def run(argv, **kwargs):
        out = (f'codex-cli {VERSION}' if argv[1:] == ['--version'] else
               json.dumps({'item': {'type': 'agent_message', 'text': 'answer'}}))
        return SimpleNamespace(returncode=0, stdout=out, stderr='')
    s.runner.side_effect = run

    def judgment():
        assert routes._codex_strict_stale({'strict_profile': record}) == []

    def work():
        assert s.adapter.execute('codex', 'hello', tools).content == 'answer'

    checks = {'judgment': judgment, 'work': work}
    checks[first]()
    checks['work' if first == 'judgment' else 'judgment']()
    assert s.digest.call_count == 2
    assert sum(call.args[0][1:] == ['--version'] for call in s.runner.call_args_list) == 1

    # A changed binding still refuses before any memo lookup or version run.
    record['binding']['home'] = '/other-home'
    assert routes._codex_strict_stale({'strict_profile': record}) == ['home']
    with pytest.raises(be.ExecutionError) as caught:
        work()
    assert caught.value.failure_class == 'isolation-unqualified'
    assert caught.value.reason == 'stale: home; observed version unknown'
    record['binding'] = s.adapter.strict_binding('codex', str(s.launcher), s.tmp_path)
    tools.qualification = record

    s.native.write_bytes(NATIVE + b'changed')
    # As in the original guard, binding is checked first. Keep it current to
    # isolate the stored digest comparison and its exact refusal reason.
    record['binding'] = s.adapter.strict_binding('codex', str(s.launcher), s.tmp_path)
    tools.qualification = record
    assert routes._codex_strict_stale({'strict_profile': record}) == ['native_sha256']
    with pytest.raises(be.ExecutionError) as caught:
        work()
    assert caught.value.failure_class == 'isolation-unqualified'
    assert caught.value.reason == 'stale: native_sha256; observed version unknown'


def test_missing_file_cannot_use_earlier_success(setup):
    s = setup
    assert s.observe() == ([], VERSION)
    s.native.unlink()
    assert s.observe() == (['native_sha256'], None)
    assert s.digest.call_count == 4
    assert s.runner.call_count == 1


def test_change_during_computation_is_not_memoized(setup):
    s = setup
    original = s.runner.return_value

    def changing_runner(*args, **kwargs):
        stat = s.native.stat()
        os.utime(s.native, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
        return original
    s.runner.side_effect = changing_runner
    assert s.observe() == ([], VERSION)
    assert not be._STRICT_OBSERVATIONS
    s.runner.side_effect = None
    assert s.observe() == ([], VERSION)
    assert s.runner.call_count == 2


def test_accepted_same_identity_replacement_risk_until_restart(setup):
    s = setup
    assert s.observe() == ([], VERSION)
    stat = s.native.stat()
    s.native.write_bytes(NATIVE.replace(b'fake', b'fak3'))
    os.utime(s.native, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert s.observe() == ([], VERSION)  # owner-accepted #309 residual risk
    assert s.digest.call_count == 2
    be._STRICT_OBSERVATIONS.clear()  # a new process has an empty memo
    assert s.observe() == (['native_sha256'], None)
