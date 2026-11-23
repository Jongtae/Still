"""#1142: the Python launcher for the pinned iMessage sidecar.

Model-free, network-free and Messages-free: an injected ``npm ci``, an injected
prebuild download and a fake child process that speaks protocol v1.  Nothing
here reads ``~/Library/Messages/chat.db``, runs ``osascript`` or downloads.
"""
import hashlib
import io
import json
import queue
import shutil
import tarfile
import tempfile
import threading
import types
import unittest
from pathlib import Path

from personal_agent import imessage_sidecar as sidecar
from personal_agent.imessage_sidecar import IMessageSidecar, IMessageSidecarError

NODE_BINARY = b'\xcf\xfa\xed\xfe fake better_sqlite3.node'


def prebuild_archive(data=NODE_BINARY, extra=()):
    """A ``.tar.gz`` shaped like a better-sqlite3 GitHub release prebuild."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
        for name, payload in (('build/Release/better_sqlite3.node', data), *extra):
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(payload), 0o755
            archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


ARCHIVE = prebuild_archive()
PIN = ('https://example.invalid/better-sqlite3-prebuild.tar.gz',
       hashlib.sha256(ARCHIVE).hexdigest(), hashlib.sha256(NODE_BINARY).hexdigest())


def store():
    return types.SimpleNamespace(root=tempfile.mkdtemp(prefix='imessage-sidecar-test-'))


def fake_node(root):
    """A pinned-Node-shaped ``bin/node`` with ``bin/npm`` beside it."""
    bin_dir = Path(root) / 'node' / 'bin'
    bin_dir.mkdir(parents=True)
    for name in ('node', 'npm'):
        (bin_dir / name).write_text('#!/bin/sh\n')
    return str(bin_dir / 'node')


class FakeNpm:
    """``npm ci``: lays out node_modules the way the lockfile would, or fails."""

    def __init__(self, returncode=0, kit_version=sidecar.KIT_VERSION, sqlite_version=sidecar.SQLITE_VERSION):
        self.returncode, self.kit_version, self.sqlite_version = returncode, kit_version, sqlite_version
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append((list(args), kwargs))
        cwd = Path(kwargs['cwd'])
        if self.returncode == 0:
            for name, version in (('@photon-ai/imessage-kit', self.kit_version), ('better-sqlite3', self.sqlite_version)):
                package = cwd / 'node_modules' / name
                package.mkdir(parents=True, exist_ok=True)
                (package / 'package.json').write_text(json.dumps({'name': name, 'version': version}))
                (package / 'index.js').write_text(f'// {name}\n')
        return types.SimpleNamespace(returncode=self.returncode, stdout='', stderr='npm ERR! code EINTEGRITY' if self.returncode else '')


class FakeFetch:
    def __init__(self, data=ARCHIVE):
        self.data, self.urls = data, []

    def __call__(self, url):
        self.urls.append(url)
        return self.data


class FakeStdout:
    def __init__(self):
        self.lines = queue.Queue()

    def push(self, event):
        self.lines.put(event if event is None else json.dumps(event) + '\n')

    def readline(self):
        line = self.lines.get(timeout=5)
        return '' if line is None else line

    def __iter__(self):
        return iter(self.readline, '')

    def close(self):
        pass


class FakeStdin:
    def __init__(self, process):
        self.process, self.closed = process, False

    def write(self, text):
        if self.closed:
            raise BrokenPipeError()
        for line in text.splitlines():
            if line.strip():
                self.process.received(json.loads(line))
        return len(text)

    def flush(self):
        pass

    def close(self):
        self.closed = True


class FakeProcess:
    """A child that answers hello with ``ready`` (or ``answer``) and exits on ``stop``."""

    def __init__(self, args, answer=None, exits_on_stop=True):
        self.args = args
        self.answer = answer if answer is not None else [{'ev': 'ready', 'v': 1, 'kit': '3.0.0'}]
        self.exits_on_stop = exits_on_stop
        self.ops, self.returncode = [], None
        self.stdout, self.stdin = FakeStdout(), FakeStdin(self)
        self.killed = self.terminated = False

    def received(self, op):
        self.ops.append(op)
        if op.get('op') == 'hello':
            for event in self.answer:
                self.stdout.push(event)
        elif op.get('op') == 'stop' and self.exits_on_stop:
            self.exit(0)

    def exit(self, code=1):
        if self.returncode is None:
            self.returncode = code
            self.stdout.push(None)

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            raise sidecar.subprocess.TimeoutExpired(self.args, timeout)
        return self.returncode

    def kill(self):
        self.killed = True
        self.exit(-9)

    def terminate(self):
        self.terminated = True
        self.exit(-15)


class FakePopen:
    def __init__(self, **process_kwargs):
        self.process_kwargs, self.processes, self.calls = process_kwargs, [], []

    def __call__(self, args, **kwargs):
        self.calls.append((list(args), kwargs))
        process = FakeProcess(args, **self.process_kwargs)
        self.processes.append(process)
        return process


def installed_target(owner_store, node):
    return sidecar.ensure_installed(owner_store, node, run=FakeNpm(), fetch=FakeFetch(), prebuild=PIN)


def launcher(owner_store=None, popen=None, **kwargs):
    owner_store = owner_store or store()
    node = fake_node(owner_store.root)
    kwargs.setdefault('provide_node', lambda directory: node)
    kwargs.setdefault('install', lambda store_, node_: installed_target(store_, node_))
    kwargs.setdefault('prebuild', PIN)
    kwargs.setdefault('sleep', lambda seconds: None)
    kwargs.setdefault('ready_seconds', 2)
    return IMessageSidecar(owner_store, popen=popen or FakePopen(), **kwargs)


class SourceIntegrityTest(unittest.TestCase):
    def copy_source(self):
        target = Path(tempfile.mkdtemp()) / 'imessage_sidecar'
        shutil.copytree(sidecar.SOURCE, target)
        return target

    def test_shipped_files_pass(self):
        sidecar.check_source()

    def test_lockfile_pins_the_reviewed_versions_with_integrity(self):
        lock = json.loads((sidecar.SOURCE / 'package-lock.json').read_text())
        packages = lock['packages']
        self.assertEqual(packages['node_modules/@photon-ai/imessage-kit']['version'], sidecar.KIT_VERSION)
        self.assertEqual(packages['node_modules/better-sqlite3']['version'], sidecar.SQLITE_VERSION)
        manifest = json.loads((sidecar.SOURCE / 'package.json').read_text())
        self.assertEqual(manifest['dependencies'], {'@photon-ai/imessage-kit': sidecar.KIT_VERSION,
                                                    'better-sqlite3': sidecar.SQLITE_VERSION})
        for name, entry in packages.items():
            if name:
                self.assertTrue(entry['integrity'].startswith('sha512-'), name)
                self.assertTrue(entry['resolved'].startswith('https://registry.npmjs.org/'), name)

    def test_tampered_lockfile_is_refused(self):
        source = self.copy_source()
        lock = source / 'package-lock.json'
        lock.write_text(lock.read_text().replace('"version": "3.0.0"', '"version": "3.0.1"', 1))
        with self.assertRaises(IMessageSidecarError) as caught:
            sidecar.check_source(source)
        self.assertEqual(caught.exception.code, 'integrity_mismatch')

    def test_lockfile_pointing_elsewhere_is_refused(self):
        source = self.copy_source()
        lock = source / 'package-lock.json'
        lock.write_text(lock.read_text().replace('https://registry.npmjs.org/', 'https://registry.example.invalid/', 1))
        with self.assertRaises(IMessageSidecarError) as caught:
            sidecar.check_source(source)
        self.assertEqual(caught.exception.code, 'integrity_mismatch')

    def test_tampered_lockfile_installs_and_runs_nothing(self):
        source = self.copy_source()
        lock = source / 'package-lock.json'
        lock.write_text(lock.read_text().replace('sha512-', 'sha512-AAAA', 1))
        owner_store, npm, fetch = store(), FakeNpm(), FakeFetch()
        node = fake_node(owner_store.root)
        with self.assertRaises(IMessageSidecarError) as caught:
            sidecar.ensure_installed(owner_store, node, run=npm, fetch=fetch, prebuild=PIN, source=source)
        self.assertEqual(caught.exception.code, 'integrity_mismatch')
        self.assertEqual(npm.calls, [])
        self.assertEqual(fetch.urls, [])
        self.assertFalse(any((Path(owner_store.root) / 'tools').glob('imessage-sidecar-*')))


class InstallTest(unittest.TestCase):
    def test_installs_with_npm_ci_from_the_lockfile_and_the_pinned_prebuild(self):
        owner_store, npm, fetch = store(), FakeNpm(), FakeFetch()
        node = fake_node(owner_store.root)
        target = sidecar.ensure_installed(owner_store, node, run=npm, fetch=fetch, prebuild=PIN)
        self.assertEqual(target.parent, Path(owner_store.root) / 'tools')
        self.assertEqual(target.name, f'imessage-sidecar-{sidecar.runtime_digest()[:16]}')
        for name in sidecar.RUNTIME_FILES:
            self.assertEqual((target / name).read_bytes(), (sidecar.SOURCE / name).read_bytes())
        self.assertEqual((target / 'node_modules/better-sqlite3/build/Release/better_sqlite3.node').read_bytes(),
                         NODE_BINARY)
        [(args, kwargs)] = npm.calls
        self.assertEqual(args[0], str(Path(node).parent / 'npm'))
        self.assertEqual(args[1], 'ci')
        self.assertIn('--ignore-scripts', args)
        self.assertNotIn('install', args)
        self.assertTrue(kwargs['env']['PATH'].startswith(str(Path(node).parent)))
        self.assertNotIn('GITHUB_TOKEN', kwargs['env'])
        self.assertEqual(fetch.urls, [PIN[0]])
        self.assertTrue(sidecar.verify_installed(target, PIN))

    def test_a_second_call_reuses_the_install(self):
        owner_store = store()
        node = fake_node(owner_store.root)
        first = sidecar.ensure_installed(owner_store, node, run=FakeNpm(), fetch=FakeFetch(), prebuild=PIN)
        npm, fetch = FakeNpm(), FakeFetch()
        self.assertEqual(sidecar.ensure_installed(owner_store, node, run=npm, fetch=fetch, prebuild=PIN), first)
        self.assertEqual((npm.calls, fetch.urls), ([], []))

    def test_a_damaged_install_is_replaced(self):
        owner_store = store()
        node = fake_node(owner_store.root)
        target = sidecar.ensure_installed(owner_store, node, run=FakeNpm(), fetch=FakeFetch(), prebuild=PIN)
        (target / 'sidecar.mjs').write_text('process.exit(0)\n')
        self.assertFalse(sidecar.verify_installed(target, PIN))
        npm = FakeNpm()
        again = sidecar.ensure_installed(owner_store, node, run=npm, fetch=FakeFetch(), prebuild=PIN)
        self.assertEqual(len(npm.calls), 1)
        self.assertTrue(sidecar.verify_installed(again, PIN))

    def test_npm_ci_failure_keeps_nothing(self):
        owner_store = store()
        node = fake_node(owner_store.root)
        with self.assertRaises(IMessageSidecarError) as caught:
            sidecar.ensure_installed(owner_store, node, run=FakeNpm(returncode=1), fetch=FakeFetch(), prebuild=PIN)
        self.assertEqual(caught.exception.code, 'install_failed')
        self.assertTrue(str(caught.exception))
        tools = Path(owner_store.root) / 'tools'
        self.assertEqual([path.name for path in tools.iterdir()] if tools.exists() else [], [])

    def test_unexpected_installed_versions_are_refused(self):
        for npm in (FakeNpm(kit_version='3.0.1'), FakeNpm(sqlite_version='12.12.0')):
            owner_store = store()
            node = fake_node(owner_store.root)
            with self.assertRaises(IMessageSidecarError) as caught:
                sidecar.ensure_installed(owner_store, node, run=npm, fetch=FakeFetch(), prebuild=PIN)
            self.assertIn(caught.exception.code, ('install_failed', 'integrity_mismatch'))
            self.assertFalse(any((Path(owner_store.root) / 'tools').glob('imessage-sidecar-*')))

    def test_a_prebuild_archive_that_does_not_match_its_pin_is_refused(self):
        owner_store = store()
        node = fake_node(owner_store.root)
        with self.assertRaises(IMessageSidecarError) as caught:
            sidecar.ensure_installed(owner_store, node, run=FakeNpm(), prebuild=PIN,
                                     fetch=FakeFetch(prebuild_archive(b'something else')))
        self.assertEqual(caught.exception.code, 'integrity_mismatch')
        self.assertFalse(any((Path(owner_store.root) / 'tools').glob('imessage-sidecar-*')))

    def test_a_prebuild_binary_that_does_not_match_its_pin_is_refused(self):
        other = prebuild_archive(b'another binary')
        pin = (PIN[0], hashlib.sha256(other).hexdigest(), PIN[2])
        owner_store = store()
        node = fake_node(owner_store.root)
        with self.assertRaises(IMessageSidecarError) as caught:
            sidecar.ensure_installed(owner_store, node, run=FakeNpm(), fetch=FakeFetch(other), prebuild=pin)
        self.assertEqual(caught.exception.code, 'integrity_mismatch')
        self.assertFalse(any((Path(owner_store.root) / 'tools').glob('imessage-sidecar-*')))

    def test_a_prebuild_archive_cannot_write_outside_its_package(self):  # only the one named member is extracted
        hostile = prebuild_archive(extra=(('../../../escaped.txt', b'x'),))
        pin = (PIN[0], hashlib.sha256(hostile).hexdigest(), PIN[2])
        owner_store = store()
        node = fake_node(owner_store.root)
        try:
            target = sidecar.ensure_installed(owner_store, node, run=FakeNpm(), fetch=FakeFetch(hostile), prebuild=pin)
        except IMessageSidecarError:
            target = None
        root = Path(owner_store.root)
        self.assertEqual(list(root.rglob('escaped.txt')), [])
        self.assertFalse((root.parent / 'escaped.txt').exists())
        if target is not None:
            self.assertTrue(sidecar.verify_installed(target, pin))

    def test_any_change_in_the_installed_tree_fails_verification(self):
        owner_store = store()
        node = fake_node(owner_store.root)
        target = sidecar.ensure_installed(owner_store, node, run=FakeNpm(), fetch=FakeFetch(), prebuild=PIN)
        kit_code = target / 'node_modules/@photon-ai/imessage-kit/index.js'
        original = kit_code.read_text()
        kit_code.write_text('// changed\n')
        self.assertFalse(sidecar.verify_installed(target, PIN))
        kit_code.write_text(original)
        self.assertTrue(sidecar.verify_installed(target, PIN))
        # bindings looks in build/ before build/Release/: a planted addon there must not pass.
        planted = target / 'node_modules/better-sqlite3/build/better_sqlite3.node'
        planted.write_bytes(b'planted')
        self.assertFalse(sidecar.verify_installed(target, PIN))
        planted.unlink()
        self.assertTrue(sidecar.verify_installed(target, PIN))
        (target / 'node_modules/@photon-ai/imessage-kit/index.js').unlink()
        self.assertFalse(sidecar.verify_installed(target, PIN))

    def test_the_prebuild_is_extracted_without_special_mode_bits(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
            info = tarfile.TarInfo('build/Release/better_sqlite3.node')
            info.size, info.mode = len(NODE_BINARY), 0o6777
            archive.addfile(info, io.BytesIO(NODE_BINARY))
        data = buffer.getvalue()
        pin = (PIN[0], hashlib.sha256(data).hexdigest(), PIN[2])
        owner_store = store()
        node = fake_node(owner_store.root)
        target = sidecar.ensure_installed(owner_store, node, run=FakeNpm(), fetch=FakeFetch(data), prebuild=pin)
        mode = (target / 'node_modules/better-sqlite3/build/Release/better_sqlite3.node').stat().st_mode
        self.assertEqual(mode & 0o6000, 0)
        self.assertEqual(mode & 0o022, 0)

    def test_the_pinned_prebuilds_are_the_reviewed_release(self):
        for (system, machine), (url, archive_sha, node_sha) in sidecar.PREBUILDS.items():
            self.assertEqual(system, 'Darwin')
            self.assertTrue(url.startswith(
                f'https://github.com/WiseLibs/better-sqlite3/releases/download/v{sidecar.SQLITE_VERSION}/'), url)
            self.assertIn('-node-v147-darwin-', url)
            self.assertEqual(len(archive_sha), 64)
            self.assertEqual(len(node_sha), 64)
        self.assertEqual(sidecar.prebuild_pin('Linux', 'x86_64'), None)
        self.assertEqual(sidecar.prebuild_pin('Darwin', 'arm64'), sidecar.PREBUILDS[('Darwin', 'arm64')])


class LauncherTest(unittest.TestCase):
    def test_stop_latches_the_launcher_closed(self):
        # #1143: once stop() has begun, no start or automatic restart runs a child again.
        popen = FakePopen()
        process_launcher = launcher(popen=popen)
        process_launcher.start()
        process_launcher.stop()
        with self.assertRaises(IMessageSidecarError) as caught:
            process_launcher.start()
        self.assertEqual(caught.exception.code, 'not_ready')
        process_launcher._restart()
        self.assertEqual(len(popen.calls), 1, 'neither start() nor a restart launched another child')

    def test_stop_waits_for_a_child_another_thread_is_still_terminating(self):
        # #1143 review: a restart whose handshake failed clears the process, then reaps it; stop() must not return first.
        process_launcher = launcher()
        process_launcher.start()
        child = process_launcher._process
        real_end, ended = process_launcher._end_process, threading.Event()

        def slow_end(process):
            threading.Event().wait(0.5)
            real_end(process)
            ended.set()

        process_launcher._end_process = slow_end
        reaper = threading.Thread(target=process_launcher._stop_process, args=(child,), daemon=True)
        reaper.start()
        for _ in range(100):
            if process_launcher._process is None:
                break
            threading.Event().wait(0.01)
        process_launcher.stop()
        self.assertTrue(ended.is_set(), 'stop() returned only after the child was reaped')
        self.assertIsNotNone(child.returncode)

    def test_start_runs_the_pinned_node_on_the_installed_sidecar_and_waits_for_ready(self):
        popen = FakePopen()
        owner_store = store()
        nodes = []

        def provide(directory):
            nodes.append(Path(directory))
            return fake_node(Path(owner_store.root) / 'n')

        process_launcher = launcher(owner_store, popen, provide_node=provide)
        ready = process_launcher.start()
        self.assertEqual(ready, {'ev': 'ready', 'v': 1, 'kit': '3.0.0'})
        self.assertEqual(nodes, [Path(owner_store.root) / 'tools'])
        [(args, kwargs)] = popen.calls
        self.assertEqual(args[0], str(Path(owner_store.root) / 'n' / 'node' / 'bin' / 'node'))
        self.assertEqual(Path(args[1]).name, 'sidecar.mjs')
        self.assertTrue(Path(args[1]).parent.name.startswith('imessage-sidecar-'))
        self.assertEqual(kwargs['stdin'], sidecar.subprocess.PIPE)
        self.assertEqual(kwargs['stdout'], sidecar.subprocess.PIPE)
        self.assertFalse(kwargs.get('shell', False))
        self.assertNotIn('GITHUB_TOKEN', kwargs['env'])
        self.assertEqual(popen.processes[0].ops[0], {'op': 'hello', 'v': 1})
        process_launcher.stop()

    def test_missing_node_is_a_visible_error_and_nothing_runs(self):
        for failure in (FileNotFoundError(2, 'No such file or directory', 'node'),
                        RuntimeError('the Node.js download did not match its pinned SHA-256; nothing was run.')):
            popen = FakePopen()

            def provide(directory, failure=failure):
                raise failure

            with self.assertRaises(IMessageSidecarError) as caught:
                launcher(popen=popen, provide_node=provide).start()
            self.assertEqual(caught.exception.code, 'node_unavailable')
            self.assertTrue(str(caught.exception).strip())
            self.assertEqual(popen.calls, [])

    def test_an_unsupported_platform_is_a_visible_error_and_nothing_runs(self):
        popen = FakePopen()
        with self.assertRaises(IMessageSidecarError) as caught:
            launcher(popen=popen, prebuild=None).start()
        self.assertEqual(caught.exception.code, 'unsupported_platform')
        self.assertEqual(popen.calls, [])

    def test_an_install_whose_files_changed_since_install_never_runs(self):
        popen = FakePopen()

        def install(store_, node_):
            target = installed_target(store_, node_)
            (target / 'package-lock.json').write_text('{}')
            return target

        with self.assertRaises(IMessageSidecarError) as caught:
            launcher(popen=popen, install=install).start()
        self.assertEqual(caught.exception.code, 'integrity_mismatch')
        self.assertEqual(popen.calls, [])

    def test_a_sidecar_error_before_ready_is_raised_with_its_code(self):
        popen = FakePopen(answer=[{'ev': 'error', 'code': 'no_disk_access', 'detail': 'chat.db could not be opened'}])
        with self.assertRaises(IMessageSidecarError) as caught:
            launcher(popen=popen).start()
        self.assertEqual(caught.exception.code, 'no_disk_access')
        process = popen.processes[0]
        self.assertIsNotNone(process.returncode)

    def test_no_ready_in_time_is_not_ready_and_the_child_is_stopped(self):
        popen = FakePopen(answer=[{'ev': 'noise'}], exits_on_stop=False)
        with self.assertRaises(IMessageSidecarError) as caught:
            launcher(popen=popen, ready_seconds=0.2).start()
        self.assertEqual(caught.exception.code, 'not_ready')
        self.assertIsNotNone(popen.processes[0].returncode)

    def test_a_ready_for_another_protocol_version_is_not_ready(self):
        popen = FakePopen(answer=[{'ev': 'ready', 'v': 2, 'kit': '9.9.9'}])
        with self.assertRaises(IMessageSidecarError) as caught:
            launcher(popen=popen).start()
        self.assertEqual(caught.exception.code, 'not_ready')

    def test_send_op_writes_one_json_line_and_events_yield_in_order(self):
        popen = FakePopen()
        process_launcher = launcher(popen=popen)
        process_launcher.start()
        process_launcher.send_op({'op': 'scope', 'mode': 'paired', 'handle': '+15551230000', 'since_ms': 5})
        process = popen.processes[0]
        self.assertEqual(process.ops[-1], {'op': 'scope', 'mode': 'paired', 'handle': '+15551230000', 'since_ms': 5})
        process.stdout.push({'ev': 'message', 'guid': 'g1', 'handle': '+15551230000', 'text': 'hi', 'ts_ms': 6, 'reaction': None})
        process.stdout.push({'ev': 'sent', 'id': 'r1'})
        events = process_launcher.events()
        self.assertEqual(next(events)['guid'], 'g1')
        self.assertEqual(next(events), {'ev': 'sent', 'id': 'r1'})
        process_launcher.stop()

    def test_send_op_says_whether_a_ready_child_took_the_op(self):
        process_launcher = launcher()
        scope = {'op': 'scope', 'mode': 'off'}
        self.assertFalse(process_launcher.send_op(scope))
        process_launcher.start()
        self.assertTrue(process_launcher.send_op({'op': 'send', 'id': 'r1', 'to': '+1', 'text': 'x'}))
        process_launcher.stop()
        self.assertFalse(process_launcher.send_op(scope))

    def test_a_scope_sent_before_start_is_applied_after_ready(self):
        popen = FakePopen()
        process_launcher = launcher(popen=popen)
        scope = {'op': 'scope', 'mode': 'paired', 'handle': '+15551230000', 'since_ms': 1}
        process_launcher.send_op(scope)
        process_launcher.start()
        self.assertEqual(popen.processes[0].ops[:2], [{'op': 'hello', 'v': 1}, scope])
        process_launcher.stop()

    def test_send_op_takes_only_a_dict(self):
        process_launcher = launcher()
        process_launcher.start()
        with self.assertRaises(TypeError):
            process_launcher.send_op('{"op":"stop"}')
        process_launcher.stop()

    def test_an_unexpected_exit_restarts_with_backoff_and_reapplies_hello_and_scope(self):
        popen, sleeps = FakePopen(), []
        process_launcher = launcher(popen=popen, sleep=sleeps.append)
        process_launcher.start()
        scope = {'op': 'scope', 'mode': 'paired', 'handle': '+15551230000', 'since_ms': 5}
        process_launcher.send_op(scope)
        events = process_launcher.events()
        popen.processes[0].exit(1)
        result = {}

        def pull():
            result['event'] = next(events)

        reader = threading.Thread(target=pull, daemon=True)
        reader.start()
        for _ in range(200):
            if len(popen.processes) > 1:
                break
            threading.Event().wait(0.01)
        self.assertEqual(len(popen.processes), 2)
        second = popen.processes[1]
        for _ in range(200):
            if len(second.ops) >= 2:
                break
            threading.Event().wait(0.01)
        self.assertEqual(second.ops[:2], [{'op': 'hello', 'v': 1}, scope])
        second.stdout.push({'ev': 'from_me', 'guid': 'g2', 'handle': '+15551230000', 'text': 'x', 'ts_ms': 7})
        reader.join(5)
        self.assertEqual(result['event']['guid'], 'g2')
        self.assertEqual(len(sleeps), 1)
        self.assertGreater(sleeps[0], 0)
        process_launcher.stop()

    def test_repeated_failures_back_off_further_and_then_give_up_visibly(self):
        sleeps = []

        class Dying(FakePopen):
            def __call__(self, args, **kwargs):
                process = super().__call__(args, **kwargs)
                if len(self.processes) > 1:
                    process.answer = []
                    process.exit(1)
                return process

        popen = Dying()
        process_launcher = launcher(popen=popen, sleep=sleeps.append, max_restarts=3)
        process_launcher.start()
        events = process_launcher.events()
        popen.processes[0].exit(1)
        with self.assertRaises(IMessageSidecarError) as caught:
            next(events)
        self.assertEqual(caught.exception.code, 'exited')
        self.assertEqual(len(sleeps), 3)
        self.assertEqual(sleeps, sorted(sleeps))
        self.assertLess(sleeps[0], sleeps[-1])

    def test_a_child_that_keeps_dying_after_ready_is_given_up_visibly(self):
        sleeps = []

        class DiesAfterReady(FakePopen):
            def __call__(self, args, **kwargs):
                process = super().__call__(args, **kwargs)
                if len(self.processes) > 1:
                    original = process.received

                    def received(op, original=original, process=process):
                        original(op)
                        if op.get('op') == 'hello':
                            threading.Timer(0.05, process.exit, (1,)).start()
                    process.received = received
                return process

        popen = DiesAfterReady()
        process_launcher = launcher(popen=popen, sleep=sleeps.append, max_restarts=3, clock=lambda: 100.0)
        process_launcher.start()
        events = process_launcher.events()
        popen.processes[0].exit(1)
        with self.assertRaises(IMessageSidecarError) as caught:
            next(events)
        self.assertEqual(caught.exception.code, 'exited')
        self.assertLessEqual(len(popen.processes), 1 + 3)

    def test_iterating_events_before_start_does_not_take_the_ready(self):
        popen = FakePopen()
        process_launcher = launcher(popen=popen)
        got = []
        reader = threading.Thread(target=lambda: got.append(next(process_launcher.events())), daemon=True)
        reader.start()
        threading.Event().wait(0.05)
        self.assertEqual(process_launcher.start()['ev'], 'ready')
        popen.processes[0].stdout.push({'ev': 'sent', 'id': 'r1'})
        reader.join(5)
        self.assertEqual(got, [{'ev': 'sent', 'id': 'r1'}])
        process_launcher.stop()

    def test_stop_returns_even_when_the_child_stops_reading_stdin(self):
        popen = FakePopen(exits_on_stop=False)
        process_launcher = launcher(popen=popen, stop_seconds=0.2)
        process_launcher.start()
        process = popen.processes[0]
        blocked = threading.Event()

        def write(text):
            blocked.wait(30)
            return len(text)

        process.stdin.write = write
        stopper = threading.Thread(target=process_launcher.stop, daemon=True)
        stopper.start()
        stopper.join(5)
        blocked.set()
        self.assertFalse(stopper.is_alive())
        self.assertTrue(process.killed or process.terminated)

    def test_stop_sends_stop_ends_events_and_does_not_restart(self):
        popen = FakePopen()
        process_launcher = launcher(popen=popen)
        process_launcher.start()
        events = process_launcher.events()
        process_launcher.stop()
        self.assertEqual(popen.processes[0].ops[-1], {'op': 'stop'})
        self.assertEqual(list(events), [])
        self.assertEqual(len(popen.processes), 1)

    def test_stop_kills_a_child_that_does_not_exit(self):
        popen = FakePopen(exits_on_stop=False)
        process_launcher = launcher(popen=popen, stop_seconds=0.2)
        process_launcher.start()
        process_launcher.stop()
        process = popen.processes[0]
        self.assertTrue(process.killed or process.terminated)
        self.assertIsNotNone(process.returncode)


class PackagingTest(unittest.TestCase):
    def test_the_sidecar_files_ship_in_the_package(self):
        import tomllib
        data = tomllib.loads((Path(__file__).resolve().parent.parent / 'pyproject.toml').read_text())
        shipped = data['tool']['setuptools']['package-data']['personal_agent']
        for name in sidecar.RUNTIME_FILES:
            self.assertIn(f'imessage_sidecar/{name}', shipped)


class NoListenerTest(unittest.TestCase):
    def test_the_launcher_opens_no_socket(self):
        source = Path(sidecar.__file__).read_text()
        for module in ('socket', 'socketserver', 'http.server', 'asyncio', 'selectors', 'ssl'):
            self.assertNotRegex(source, rf'^\s*(import|from)\s+{module}\b', module)
        self.assertNotIn('start_server', source)
        self.assertNotIn('pass_fds', source)


if __name__ == '__main__':
    unittest.main()
