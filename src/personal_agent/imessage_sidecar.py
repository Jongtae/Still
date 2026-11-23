"""Install and supervise Still's iMessage sidecar (#1142).

Adapt @photon-ai/imessage-kit 3.0.0 (MIT, see its tarball LICENSE) on Still's
pinned Node.js from node_runtime. Use the official pinned GitHub release
prebuild for the better-sqlite3 12.11.1 (MIT) native addon in place of an
install script. The protocol uses stdio only, with no socket or port.
"""

import hashlib
import io
import json
import platform
import queue
import shutil
import stat
import subprocess
import tarfile
import tempfile
import threading
import time
import urllib.request
from contextlib import suppress
from pathlib import Path

from . import node_runtime, quick_tunnel

SOURCE = Path(__file__).with_name('imessage_sidecar')
RUNTIME_FILES = ('package.json', 'package-lock.json', 'sidecar.mjs')
KIT_VERSION = '3.0.0'
SQLITE_VERSION = '12.11.1'
PROTOCOL_VERSION = 1
LOCK_SHA256 = '3149ed0f14d8d872d156e0b80957e837e0db56dbf0c70c0259d16210c9265113'

# node-v147 is the Node 26 ABI. The archive hashes match GitHub's published
# asset digests for uploads by github-actions[bot]. The arm64 binary matches
# the one fetched by the npm ci install script on 2026-11-23.
_RELEASE = 'https://github.com/WiseLibs/better-sqlite3/releases/download/v12.11.1'
PREBUILDS = {
    ('Darwin', 'arm64'): (
        f'{_RELEASE}/better-sqlite3-v12.11.1-node-v147-darwin-arm64.tar.gz',
        '449dc1a8d6faf652fd449a19af125746f45c0f0abb1a5d17ec9fd44e8f5e7e69',
        '0000d73c6e2e94318ed2b9339139623d5a0908b195f1e761c16cfd98f9cc6229',
    ),
    ('Darwin', 'x86_64'): (
        f'{_RELEASE}/better-sqlite3-v12.11.1-node-v147-darwin-x64.tar.gz',
        'a2ecaf018ac3648dc752efa4a97de098d4d24b2425731c897de94ca1e8ae0b0e',
        '098e2ca10709bdb5e54628639cd0a2ba9e034be89f9344ec91348ed93da54425',
    ),
}
MAX_PREBUILD_BYTES = 16 * 1024 * 1024
_INSTALL_SECONDS = 180
_PACKAGES = {'@photon-ai/imessage-kit': KIT_VERSION, 'better-sqlite3': SQLITE_VERSION}
_ADDON = 'build/Release/better_sqlite3.node'
_INSTALL_MANIFEST = '.still-install.json'
_DEFAULT_PREBUILD = object()
_EOF = object()
_STOP = object()
_SIDECAR_ERRORS = ('no_disk_access', 'messages_not_signed_in', 'db_unreadable', 'bad_request')


class IMessageSidecarError(RuntimeError):
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


def prebuild_pin(system=None, machine=None):
    """The native addon's release pin for this platform, if supported."""
    machine = str(machine or platform.machine()).lower()
    machine = {'aarch64': 'arm64', 'amd64': 'x86_64'}.get(machine, machine)
    return PREBUILDS.get((system or platform.system(), machine))


def runtime_digest(source=SOURCE):
    digest = hashlib.sha256()
    for name in RUNTIME_FILES:
        digest.update(name.encode() + b'\0' + (source / name).read_bytes() + b'\0')
    return digest.hexdigest()


def check_source(source=SOURCE):
    """Check both the shipped lockfile digest and the expected npm pins."""
    try:
        data = (source / 'package-lock.json').read_bytes()
        packages = json.loads(data)['packages']
        manifest = json.loads((source / 'package.json').read_bytes())
        if (hashlib.sha256(data).hexdigest() == LOCK_SHA256
                and manifest['dependencies'] == _PACKAGES
                and all(packages[f'node_modules/{name}']['version'] == version
                        for name, version in _PACKAGES.items())
                and all(entry['integrity'].startswith('sha512-')
                        and entry['resolved'].startswith('https://registry.npmjs.org/')
                        for name, entry in packages.items() if name)):
            return
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        pass
    raise IMessageSidecarError('The iMessage sidecar source failed its integrity check. Nothing ran.',
                               'integrity_mismatch')


def _fetch(url):
    with urllib.request.urlopen(url, timeout=node_runtime.DOWNLOAD_SECONDS) as response:
        data = response.read(MAX_PREBUILD_BYTES + 1)
    if len(data) > MAX_PREBUILD_BYTES:
        raise IMessageSidecarError('The iMessage SQLite download was larger than expected. Nothing ran.',
                                   'integrity_mismatch')
    return data


def _versions_match(target):
    try:
        return all(json.loads((target / 'node_modules' / name / 'package.json').read_bytes())['version'] == version
                   for name, version in _PACKAGES.items())
    except (OSError, ValueError, TypeError, KeyError):
        return False


def _sha256(path):
    with path.open('rb') as binary:
        return hashlib.file_digest(binary, 'sha256').hexdigest()


def _addon_matches(package, sha256):
    try:
        return _sha256(package / _ADDON) == sha256
    except OSError:
        return False


def _regular_files(target):
    def raise_walk_error(error):
        raise error

    if not stat.S_ISDIR(target.lstat().st_mode):
        raise IMessageSidecarError('The iMessage sidecar install is not a regular directory.', 'integrity_mismatch')
    for directory, dirs, names in target.walk(on_error=raise_walk_error):
        for name in dirs + names:
            path = directory / name
            mode = path.lstat().st_mode
            if stat.S_ISREG(mode):
                yield path
            elif not stat.S_ISDIR(mode):
                raise IMessageSidecarError('The iMessage sidecar install contains a symlink or special file.',
                                           'integrity_mismatch')


def _installed_hashes(target):
    return {path.relative_to(target).as_posix(): _sha256(path)
            for path in _regular_files(target) if path != target / _INSTALL_MANIFEST}


def verify_installed(target, prebuild, source=SOURCE):
    try:
        # Detects damage and planted files, including an addon that bindings would
        # load from build/ before build/Release/. The owner controls this store
        # and a writer who also rewrites the manifest can defeat this check.
        files = _installed_hashes(target)
        manifest = json.loads((target / _INSTALL_MANIFEST).read_bytes())
        return (manifest['files'] == files
                and all((target / name).read_bytes() == (source / name).read_bytes() for name in RUNTIME_FILES)
                and _versions_match(target)
                and _addon_matches(target / 'node_modules' / 'better-sqlite3', prebuild[2]))
    except (IMessageSidecarError, OSError, ValueError, TypeError, KeyError):
        return False


def _install_addon(package, prebuild, fetch):
    url, archive_sha, binary_sha = prebuild
    data = fetch(url)
    if len(data) > MAX_PREBUILD_BYTES or hashlib.sha256(data).hexdigest() != archive_sha:
        raise IMessageSidecarError('The iMessage SQLite archive failed its integrity check. Nothing ran.',
                                   'integrity_mismatch')
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
            member = archive.getmember(_ADDON)
            if member.isfile():
                archive.extract(member, path=package, filter='data')
                (package / _ADDON).chmod(0o755)
                if _addon_matches(package, binary_sha):
                    return
    except (tarfile.TarError, EOFError, OSError, KeyError):
        pass
    raise IMessageSidecarError('The iMessage SQLite binary failed its integrity check. Nothing ran.',
                               'integrity_mismatch')


def ensure_installed(store, node, *, run=subprocess.run, fetch=None, prebuild=None, source=SOURCE):
    """Stage the pinned packages and native addon, then publish the verified install."""
    check_source(source)
    prebuild = prebuild_pin() if prebuild is None else prebuild
    if prebuild is None:
        raise IMessageSidecarError('The iMessage sidecar requires a supported Mac. Nothing ran.', 'unsupported_platform')

    tools = quick_tunnel.tool_dir(store)
    staging = None
    try:
        target = tools / f'imessage-sidecar-{runtime_digest(source)[:16]}'
        if target.exists() and verify_installed(target, prebuild, source):
            return target
        with suppress(FileNotFoundError):
            if target.is_symlink() or target.is_file():
                target.unlink()
            elif target.exists():
                shutil.rmtree(target)

        npm = Path(node).parent / 'npm'
        if not npm.is_file():
            raise IMessageSidecarError('The pinned npm executable is unavailable. Nothing ran.', 'node_unavailable')
        tools.mkdir(parents=True, exist_ok=True, mode=0o700)
        staging = Path(tempfile.mkdtemp(dir=tools, prefix='.imessage-sidecar-'))
        for name in RUNTIME_FILES:
            shutil.copyfile(source / name, staging / name)
        check_source(staging)

        # The runtime uses module imports only. Skip scripts and executable
        # symlinks, and keep npm's transient home and cache out of the install.
        with tempfile.TemporaryDirectory(dir=staging) as npm_home:
            env = {'PATH': f'{npm.parent}:/usr/bin:/bin', 'HOME': npm_home, 'npm_config_update_notifier': 'false'}
            done = run([str(npm), 'ci', '--ignore-scripts', '--no-bin-links', '--omit=dev', '--no-audit', '--no-fund'],
                       cwd=staging, env=env, capture_output=True, text=True,
                       timeout=_INSTALL_SECONDS, stdin=subprocess.DEVNULL)
        if done.returncode != 0:
            raise IMessageSidecarError('The pinned iMessage packages could not be installed. The sidecar did not run.',
                                       'install_failed')

        # Check before extracting as well, so no addon path can traverse a symlink.
        for _ in _regular_files(staging):
            pass
        if not _versions_match(staging):
            raise IMessageSidecarError('The pinned iMessage packages could not be installed. The sidecar did not run.',
                                       'install_failed')
        _install_addon(staging / 'node_modules' / 'better-sqlite3', prebuild, fetch or _fetch)
        files = _installed_hashes(staging)
        with (staging / _INSTALL_MANIFEST).open('x', encoding='utf-8') as manifest:
            json.dump({'files': files}, manifest, sort_keys=True)
        try:
            staging.rename(target)
        except OSError:
            if not verify_installed(target, prebuild, source):
                raise
    except (OSError, subprocess.SubprocessError) as error:
        raise IMessageSidecarError('The iMessage sidecar could not be installed. The sidecar did not run.',
                                   'install_failed') from error
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
    return target


class IMessageSidecar:
    """A stdio child with a verified startup and bounded restart attempts."""

    def __init__(self, store, *, popen=subprocess.Popen, provide_node=node_runtime.ensure,
                 install=None, prebuild=_DEFAULT_PREBUILD, sleep=time.sleep, clock=time.monotonic,
                 ready_seconds=30, stop_seconds=5, max_restarts=5, backoff=(1, 2, 4, 8, 16, 30),
                 stable_seconds=60):
        self.prebuild = prebuild_pin() if prebuild is _DEFAULT_PREBUILD else prebuild
        self._store = store
        self._popen = popen
        self._provide_node = provide_node
        self._install = install
        self._sleep = sleep
        self._clock = clock
        self._ready_seconds = ready_seconds
        self._stop_seconds = stop_seconds
        self._max_restarts = max_restarts
        self._backoff = backoff
        self._stable_seconds = stable_seconds
        self._restart_attempts = 0
        self._queue = queue.Queue()
        self._lock = threading.Lock()
        self._start_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._process = None
        self._handshake = None
        self._ready = None
        self._ready_at = None
        self._scope = None
        self._stopping = False

    def start(self):
        with self._start_lock:
            return self._start()

    @property
    def ready(self):
        """The ``ready`` frame of the child carrying messages now, or None while stopped or restarting.

        ``events()`` consumes ``ready`` frames for the handshake, so this is how a caller sees readiness.
        """
        with self._lock:
            return None if self._ready is None else self._ready.copy()

    def _start(self):
        with self._lock:
            if self._stopping:
                raise IMessageSidecarError('The iMessage sidecar has been stopped.', 'not_ready')
            if self._ready is not None:
                return self._ready.copy()
        if self.prebuild is None:
            raise IMessageSidecarError('The iMessage sidecar requires a supported Mac. Nothing ran.',
                                       'unsupported_platform')
        try:
            node = self._provide_node(quick_tunnel.tool_dir(self._store))
        except (OSError, RuntimeError, tarfile.TarError) as error:
            raise IMessageSidecarError('The pinned Node.js runtime is unavailable. The sidecar did not run.',
                                       'node_unavailable') from error
        if self._install is None:
            target = ensure_installed(self._store, node, prebuild=self.prebuild)
        else:
            target = Path(self._install(self._store, node))
        if not verify_installed(target, self.prebuild):
            raise IMessageSidecarError('The installed iMessage sidecar failed its integrity check. Nothing ran.',
                                       'integrity_mismatch')

        env = {'PATH': f'{Path(node).parent}:/usr/bin:/bin', 'HOME': str(Path.home()), 'LANG': 'en_US.UTF-8'}
        handshake = queue.Queue()
        with self._lock:
            if self._stopping:
                raise IMessageSidecarError('The iMessage sidecar has been stopped.', 'not_ready')
            try:
                process = self._popen([node, str(target / 'sidecar.mjs')], cwd=str(target),
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                      text=True, encoding='utf-8', bufsize=1, env=env)
            except OSError as error:
                raise IMessageSidecarError('The iMessage sidecar could not start. Nothing ran.', 'not_ready') from error
            self._process = process
            self._handshake = handshake
        try:
            threading.Thread(target=self._read_events, args=(process, handshake), daemon=True).start()
            with self._write_lock:
                if self._stopping or not self._write(process, {'op': 'hello', 'v': PROTOCOL_VERSION}):
                    raise IMessageSidecarError('The iMessage sidecar could not accept its handshake.', 'not_ready')
            ready, ready_at = self._wait_ready(handshake)
            with self._write_lock:
                with self._lock:
                    if self._stopping:
                        raise IMessageSidecarError('The iMessage sidecar has been stopped.', 'not_ready')
                    scope = self._scope
                if scope is not None:
                    self._write(process, scope)
                with self._lock:
                    if self._stopping:
                        raise IMessageSidecarError('The iMessage sidecar has been stopped.', 'not_ready')
                    self._ready, self._ready_at = ready, ready_at
            return ready.copy()
        except BaseException:
            self._stop_process(process)
            raise

    def _read_events(self, process, handshake):
        ready = False
        try:
            for line in iter(process.stdout.readline, ''):
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict):
                    if ready:
                        self._queue.put((process, event, None))
                    else:
                        ready = event.get('ev') == 'ready'
                        handshake.put((event, self._clock() if ready else None))
        except (OSError, ValueError):
            pass
        finally:
            exited_at = self._clock()
            with suppress(OSError, ValueError):
                process.stdout.close()
            if not ready:
                handshake.put((_EOF, None))
            self._queue.put((process, _EOF, exited_at))

    def _wait_ready(self, handshake):
        deadline = self._clock() + self._ready_seconds
        while (remaining := deadline - self._clock()) > 0:
            try:
                event, ready_at = handshake.get(timeout=remaining)
            except queue.Empty:
                break
            if event is _STOP or event is _EOF:
                break
            if event.get('ev') == 'error':
                code = event.get('code')
                detail = event.get('detail')
                raise IMessageSidecarError(
                    detail if isinstance(detail, str) and detail else 'The iMessage sidecar reported a startup error.',
                    code if code in _SIDECAR_ERRORS else 'db_unreadable',
                )
            if event.get('ev') == 'ready':
                if type(event.get('v')) is int and event['v'] == PROTOCOL_VERSION:
                    return event, ready_at
                raise IMessageSidecarError('The iMessage sidecar uses an incompatible protocol version.', 'not_ready')
        raise IMessageSidecarError('The iMessage sidecar did not become ready.', 'not_ready')

    @staticmethod
    def _write(process, op):
        try:
            process.stdin.write(json.dumps(op, ensure_ascii=False) + '\n')
            process.stdin.flush()
        except (OSError, ValueError):
            return False
        return True

    def send_op(self, op):
        """Write one op; False when no ready child took it (stopped or restarting).

        A ``scope`` op is remembered either way and re-applied after each restart.
        """
        if not isinstance(op, dict):
            raise TypeError('The iMessage sidecar operation must be a dict.')
        with self._write_lock:
            with self._lock:
                if self._stopping:
                    return False
                if op.get('op') == 'scope':
                    self._scope = op.copy()
                if self._ready is None:
                    return False
                process = self._process
            return self._write(process, op)

    def events(self):
        while not self._stopping:
            process, event, exited_at = self._queue.get()
            if self._stopping or event is _STOP:
                return
            # A child can report ready and exit before start() commits the
            # handshake. Only supervise events from a child that started.
            with self._start_lock:
                with self._lock:
                    if process is not self._process or self._ready_at is None:
                        continue
                    ready_at = self._ready_at
                if event is _EOF:
                    if exited_at - ready_at >= self._stable_seconds:
                        self._restart_attempts = 0
                    self._stop_process(process)
            if event is _EOF:
                self._restart()
            elif event.get('ev') != 'ready' and not self._stopping:
                yield event

    def _restart(self):
        last_error = None
        while not self._stopping and self._restart_attempts < self._max_restarts:
            attempt = self._restart_attempts
            self._sleep(self._backoff[min(attempt, len(self._backoff) - 1)])
            if self._stopping:
                return
            self._restart_attempts += 1
            try:
                self.start()
                return
            except IMessageSidecarError as error:
                last_error = error
        if not self._stopping:
            raise IMessageSidecarError('The iMessage sidecar exited and could not restart.', 'exited') from last_error

    def stop(self):
        with self._lock:
            if self._stopping:
                return
            self._stopping = True
            process = self._process
            self._queue.put((None, _STOP, None))
        if process is not None:
            self._stop_process(process)

    def _stop_process(self, process):
        with self._lock:
            if self._process is not process:
                return
            self._handshake.put((_STOP, None))
            self._process = self._handshake = self._ready = self._ready_at = None

        def close_stdin():
            with self._write_lock:
                if process.poll() is None:
                    self._write(process, {'op': 'stop'})
                with suppress(OSError, ValueError):
                    process.stdin.close()

        # Both a write and closing a buffered pipe can block. Reap the child
        # even if the stdin worker is stuck.
        writer = threading.Thread(target=close_stdin, daemon=True)
        writer.start()
        writer.join(self._stop_seconds)
        try:
            process.wait(timeout=self._stop_seconds)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=self._stop_seconds)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
