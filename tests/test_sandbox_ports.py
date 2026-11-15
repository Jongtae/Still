"""#868: real POSIX locks/processes and model-free sandbox lifecycle regressions."""
import select
import signal
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from agentos_eval import sandbox


class AvailableSocket:
    """Inject only socket availability; real file locks remain the test boundary."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def bind(self, address):
        pass


@pytest.fixture(autouse=True)
def port_lock_dir(tmp_path, monkeypatch):
    folder = tmp_path / 'shared-port-locks'
    monkeypatch.setenv('AGENTOS_EVAL_PORT_LOCK_DIR', str(folder))
    monkeypatch.setattr(sandbox, 'socket', SimpleNamespace(
        AF_INET=sandbox.socket.AF_INET, SOCK_STREAM=sandbox.socket.SOCK_STREAM,
        socket=lambda *args: AvailableSocket()))
    return folder


def reserve_exact(port):
    return sandbox.reserve_port(start=port, end=port + 1)


def read_child_line(child):
    assert select.select([child.stdout], [], [], 10)[0], 'child did not report readiness'
    line = child.stdout.readline().strip()
    assert line, f'child exited: {child.poll()}; {child.stderr.read()}'
    return line


@pytest.mark.parametrize('release', ['close', 'exit', 'kill'])
def test_reservations_exclude_other_processes_and_recover_after_release(release):
    # No server binds a socket: probe-and-close alone would choose this same
    # first available port in both processes. Pipes synchronize without sleeps.
    code = '''
import sys
from unittest import mock
from agentos_eval.sandbox import reserve_port
mock.patch('agentos_eval.sandbox.socket.socket').start()
reservation = reserve_port()
print(reservation.port, flush=True)
if sys.stdin.readline().strip() == 'close':
    reservation.close()
    print('released', flush=True)
    sys.stdin.readline()
'''
    evals = str(Path(sandbox.__file__).resolve().parents[1])  # CI puts evals on sys.path, not PYTHONPATH
    child = subprocess.Popen([sys.executable, '-c', f'import sys; sys.path.insert(0, {evals!r})\n' + code], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        port = int(read_child_line(child))
        with sandbox.reserve_port(start=port, end=port + 20) as other:
            assert {port, port + 1}.isdisjoint({other.port, other.port + 1})
        for overlapping in (port - 1, port, port + 1):
            with pytest.raises(sandbox.SandboxError):
                reserve_exact(overlapping)
        if release == 'close':
            child.stdin.write('close\n')
            child.stdin.flush()
            assert read_child_line(child) == 'released'
            assert child.poll() is None  # explicit close works before process exit
        elif release == 'exit':
            child.stdin.write('exit\n')
            child.stdin.flush()
            assert child.wait(timeout=10) == 0
        else:
            child.kill()
            child.wait(timeout=10)
        with reserve_exact(port) as reused:
            assert reused.port == port
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=10)


@pytest.fixture
def fake_server(monkeypatch):
    process = mock.Mock(poll=mock.Mock(return_value=0), returncode=0)
    popen = mock.Mock(return_value=process)
    monkeypatch.setattr(sandbox.subprocess, 'Popen', popen)
    return popen


@pytest.mark.parametrize('kind', ['sandboxes', 'two-pools', 'one-pool'])
def test_instances_hold_disjoint_pairs_until_stop_or_pool_exit(tmp_path, fake_server, kind):
    with ExitStack() as stack:
        if kind == 'sandboxes':
            boxes = [sandbox.Sandbox(tmp_path / str(n)) for n in range(2)]
            for box in boxes:
                stack.callback(box.stop)
                box.start()
        else:
            first = sandbox.SandboxPool(size=2, root=tmp_path / 'first')
            second = (first if kind == 'one-pool' else
                      sandbox.SandboxPool(size=2, root=tmp_path / 'second'))
            boxes = [stack.enter_context(pool.sandbox()) for pool in (first, second)]
        a, b = (box.port for box in boxes)
        assert {a, a + 1}.isdisjoint({b, b + 1})
        for port in (a - 1, a, a + 1, b - 1, b, b + 1):
            with pytest.raises(sandbox.SandboxError):
                reserve_exact(port)
    # No socket was bound; only stop/pool cleanup made these available again.
    for port in (a, b):
        with reserve_exact(port) as reused:
            assert reused.port == port
    if kind != 'sandboxes':
        for pool in (first, second):
            assert not pool.ports
            assert not pool.held


def test_explicit_sandbox_port_is_reserved_too(tmp_path, fake_server):
    port = sandbox.free_port()
    first = sandbox.Sandbox(tmp_path / 'first', port=port, keep=True)
    second = sandbox.Sandbox(tmp_path / 'second', port=port)
    try:
        first.start()
        with pytest.raises(sandbox.SandboxError):
            second.start()
    finally:
        second.stop()
        first.stop()
    assert first.data.exists()  # keeping a folder does not keep a reservation
    with reserve_exact(port):
        pass
    first.stop()  # idempotent release


@pytest.mark.parametrize('force_kill', [False, True])
def test_stop_holds_reservation_until_server_has_been_reaped(tmp_path, monkeypatch, fake_server, force_kill):
    box = sandbox.Sandbox(tmp_path)
    box.start()
    box.process.poll.return_value = None
    signals, waits = [], []

    def killpg(pid, sig):
        with pytest.raises(sandbox.SandboxError):
            reserve_exact(box.port)
        signals.append(sig)

    def wait(timeout):
        with pytest.raises(sandbox.SandboxError):
            reserve_exact(box.port)
        waits.append(timeout)
        if force_kill and len(waits) == 1:
            raise subprocess.TimeoutExpired('server', timeout)
        return 0

    monkeypatch.setattr(sandbox.os, 'killpg', killpg)
    box.process.wait.side_effect = wait
    box.stop()
    assert signals == ([signal.SIGTERM, signal.SIGKILL] if force_kill else [signal.SIGTERM])
    assert len(waits) == len(signals)
    with reserve_exact(box.port):
        pass


@pytest.mark.parametrize('pooled', [False, True])
@pytest.mark.parametrize('stage', ['prepare', 'spawn'])
def test_start_failure_releases_pair_and_pool_slot(tmp_path, monkeypatch, pooled, stage):
    seen = []

    def fail_prepare(box):
        seen.append(box.port)
        with pytest.raises(sandbox.SandboxError):
            reserve_exact(box.port)
        raise RuntimeError('prepare failed')

    def fail_spawn(argv, **kwargs):
        port = int(argv[argv.index('--port') + 1])
        seen.append(port)
        with pytest.raises(sandbox.SandboxError):
            reserve_exact(port)
        raise RuntimeError('spawn failed')

    if stage == 'prepare':
        monkeypatch.setattr(sandbox.Sandbox, 'prepare', fail_prepare)
    else:
        monkeypatch.setattr(sandbox.subprocess, 'Popen', fail_spawn)
    if pooled:
        pool = sandbox.SandboxPool(size=1, root=tmp_path / 'boxes')
        with pytest.raises(RuntimeError, match=f'{stage} failed'):
            with pool.sandbox():
                pytest.fail('start should fail')
        assert not pool.ports
        assert not pool.held
    else:
        box = sandbox.Sandbox(tmp_path / 'box')
        with pytest.raises(RuntimeError, match=f'{stage} failed'):
            box.start()
        assert box.log is None or box.log.closed
    assert len(seen) == 1
    with reserve_exact(seen[0]):
        pass


@pytest.mark.parametrize('failure', ['reserve', 'factory', 'stop', 'body'])
def test_pool_cleans_up_on_all_exit_paths(tmp_path, monkeypatch, fake_server, failure):
    pool = sandbox.SandboxPool(size=1, root=tmp_path / 'boxes')
    seen = []

    def fail(*args, **kwargs):
        seen.append(kwargs.get('port'))
        raise RuntimeError('failed')

    with monkeypatch.context() as patch:
        if failure == 'reserve':
            patch.setattr(sandbox, 'reserve_port', fail)
        elif failure == 'factory':
            pool.factory = fail
        elif failure == 'stop':
            patch.setattr(sandbox.Sandbox, 'stop', fail)
        with pytest.raises(RuntimeError, match='failed'):
            with pool.sandbox() as box:
                seen.append(box.port)
                if failure == 'body':
                    raise RuntimeError('failed')
    assert not pool.ports
    assert not pool.held
    pool.factory = sandbox.Sandbox
    # Re-entering a one-slot pool also checks semaphore/slot recovery.
    with pool.sandbox() as box:
        assert box.slot == 0
        if failure != 'reserve':
            assert box.port == next(port for port in seen if port is not None)


@pytest.mark.parametrize('offset', [0, 1])
def test_probe_skips_occupied_port_and_releases_partial_locks(monkeypatch, offset):
    port = sandbox.free_port()
    probed = []

    def bind(probe, address):
        probed.append(address)
        if address[1] == port + offset:
            raise OSError('address already in use')

    with monkeypatch.context() as patch:
        patch.setattr(AvailableSocket, 'bind', bind)
        with pytest.raises(sandbox.SandboxError):
            reserve_exact(port)
        with sandbox.reserve_port(start=port, end=port + 20) as other:
            assert other.port >= port + offset + 1
    assert ('127.0.0.1', port + offset) in probed
    with reserve_exact(port):
        pass


@pytest.mark.parametrize('offset', [-1, 0, 1])
def test_taken_and_live_exclusions_cover_both_ports(monkeypatch, offset):
    port = sandbox.free_port()
    with pytest.raises(sandbox.SandboxError):
        sandbox.reserve_port(start=port + offset, end=port + offset + 1, taken={port, port + 1})
    monkeypatch.setattr(sandbox, 'LIVE_PORTS', {port, port + 1})
    with pytest.raises(sandbox.SandboxError):
        reserve_exact(port + offset)


def test_default_locks_are_independent_of_eval_home_and_tmpdir(tmp_path, monkeypatch):
    monkeypatch.delenv('AGENTOS_EVAL_PORT_LOCK_DIR')
    with sandbox.reserve_port() as held:
        monkeypatch.setenv('AGENTOS_EVAL_HOME', str(tmp_path / 'another-eval-home'))
        monkeypatch.setenv('TMPDIR', str(tmp_path))
        with pytest.raises(sandbox.SandboxError):
            reserve_exact(held.port)


@pytest.mark.parametrize('exit_during_request', [False, True])
def test_wait_healthy_rejects_an_exited_process_even_when_health_is_true(tmp_path, exit_during_request):
    box = sandbox.Sandbox(tmp_path)
    box.process = mock.Mock(returncode=1)
    box.process.poll.side_effect = [None, 1] if exit_during_request else [1]
    client = mock.Mock(healthy=mock.Mock(return_value=True))
    with pytest.raises(sandbox.SandboxError, match='exited with 1'):
        box.wait_healthy(client)
    if exit_during_request:
        client.healthy.assert_called_once()


def test_wait_healthy_accepts_a_healthy_live_process(tmp_path):
    box = sandbox.Sandbox(tmp_path)
    box.process = mock.Mock(poll=mock.Mock(return_value=None))
    assert box.wait_healthy(mock.Mock(healthy=mock.Mock(return_value=True))) is True
