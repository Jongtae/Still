"""#34: what the Linux desktop browser needs before its worker runs.

The pinned Chrome for Testing download (``chromium_runtime``), the desktop
keyring key (``browser_jar.SecretServiceKey``), the engine choice per
platform and the typed refusals a first start can end in (download,
sandbox).

Evidence class: focused unit tests with injected downloads, a fake Secret
Service and a temporary store; no browser runs and nothing is downloaded.
The real-engine conformance run is ``tests/test_browser_webkit.py``
``WebKitIntegrationTests`` against the Chromium worker on Linux.
"""
import hashlib
import io
import os
import stat
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from personal_agent import browser_session as bs
from personal_agent import chromium_runtime as cr
from personal_agent.agent_runtime import ToolError
from personal_agent.browser_jar import (JarError, KeychainKey, MemoryKey, SecretServiceKey, platform_key,
                                        store_account)

CHROME = b'\x7fELF pinned chrome'


def release(chrome=CHROME, extra=()):
    """A zip shaped like a Chrome for Testing release for ``linux-arm64``."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        def add(name, data=b'', mode=0o644):
            info = zipfile.ZipInfo('chrome-linux-arm64/' + name if not name.startswith('/') and '..' not in name else name)
            info.external_attr = (stat.S_IFREG | mode) << 16
            archive.writestr(info, data)
        add('chrome', chrome, 0o755)
        add('chrome_sandbox', b'sandbox', 0o4755)
        add('resources.pak', b'pak')
        for name, data, mode in extra:
            add(name, data, mode)
    return buffer.getvalue()


def pin_for(data, chrome=CHROME):
    return ('linux-arm64', hashlib.sha256(data).hexdigest(), hashlib.sha256(chrome).hexdigest())


class Fetch:
    def __init__(self, data):
        self.data, self.urls = data, []

    def __call__(self, url, destination):
        self.urls.append(url)
        Path(destination).write_bytes(self.data)
        return hashlib.sha256(self.data).hexdigest()


class ChromiumRuntime(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.directory = Path(tmp.name)

    def test_the_pinned_build_is_downloaded_once_checked_and_kept(self):
        data = release()
        fetch = Fetch(data)
        chrome = cr.ensure(self.directory, fetch=fetch, chosen=pin_for(data))
        self.assertEqual(fetch.urls, [f'{cr.DIST}/{cr.VERSION}/linux-arm64/chrome-linux-arm64.zip'])
        self.assertEqual(Path(chrome).read_bytes(), CHROME)
        self.assertTrue(os.access(chrome, os.X_OK))
        sandbox = Path(chrome).with_name('chrome_sandbox')
        self.assertFalse(sandbox.stat().st_mode & (stat.S_ISUID | stat.S_ISGID), 'never setuid from an archive')
        self.assertFalse(list(Path(chrome).parent.glob('*.zip')), 'the archive is not kept')
        self.assertEqual(cr.ensure(self.directory, fetch=Fetch(b'unused'), chosen=pin_for(data)), chrome)

    def test_a_download_that_does_not_match_its_pin_is_never_kept(self):
        data = release()
        with self.assertRaises(RuntimeError):
            cr.ensure(self.directory, fetch=Fetch(data + b'x'), chosen=pin_for(data))
        with self.assertRaises(RuntimeError):
            cr.ensure(self.directory, fetch=Fetch(data), chosen=pin_for(data, chrome=b'another binary'))
        self.assertIsNone(cr.installed(self.directory, chosen=pin_for(data)))
        leftovers = [path for path in self.directory.rglob('*') if path.is_file()]
        self.assertEqual(leftovers, [])

    def test_an_archive_naming_a_path_outside_its_folder_is_refused(self):
        data = release(extra=[('../../escape', b'x', 0o644)])
        with self.assertRaises(RuntimeError):
            cr.ensure(self.directory, fetch=Fetch(data), chosen=pin_for(data))
        self.assertFalse((self.directory / 'escape').exists())
        self.assertFalse((self.directory.parent / 'escape').exists())

    def test_a_changed_binary_is_not_used_again(self):
        data = release()
        chrome = cr.ensure(self.directory, fetch=Fetch(data), chosen=pin_for(data))
        self.assertFalse(os.stat(chrome).st_mode & 0o222, 'the checked build is kept read-only')
        os.chmod(Path(chrome).parent, 0o755)
        os.chmod(chrome, 0o755)   # a same-user change; the digest check before each start catches it
        Path(chrome).write_bytes(b'tampered')
        self.assertIsNone(cr.installed(self.directory, chosen=pin_for(data)))
        self.assertEqual(Path(cr.ensure(self.directory, fetch=Fetch(data), chosen=pin_for(data))).read_bytes(), CHROME)

    def test_the_pins_cover_the_linux_machines_and_nothing_else(self):
        self.assertEqual(cr.pin('Linux', 'aarch64'), cr.PINS[('Linux', 'arm64')])
        self.assertEqual(cr.pin('Linux', 'AMD64'), cr.PINS[('Linux', 'x86_64')])
        self.assertIsNone(cr.pin('Darwin', 'arm64'))
        self.assertIsNone(cr.pin('Linux', 'riscv64'))
        with mock.patch.object(cr, 'pin', lambda *a, **k: None), self.assertRaises(FileNotFoundError):
            cr.ensure(self.directory, fetch=Fetch(b'never'))

    def test_the_apparmor_profile_names_exactly_this_binary(self):
        for pattern in ('/home/o/*/chrome', '/home/o/[ab]/chrome', '/home/o/@{HOME}/chrome', '/home/o/a{b,c}/chrome',
                        '/home/o/a\\b/chrome', '/home/o/../chrome', '/home/o//chrome'):
            with self.assertRaises(ValueError, msg=pattern):
                cr.apparmor_profile(pattern)
        profile = cr.apparmor_profile('/home/owner/.local/share/agentos/tools/chromium/1/chrome-linux64/chrome')
        self.assertIn('profile still-chromium "/home/owner/.local/share/agentos/tools/chromium/1/chrome-linux64/chrome"',
                      profile)
        self.assertIn('userns,', profile)
        self.assertNotIn('*', profile)
        command = cr.apparmor_setup('/home/o w/chrome')
        self.assertIn('sudo tee /etc/apparmor.d/still-chromium', command)
        self.assertIn('sudo apparmor_parser -r /etc/apparmor.d/still-chromium', command)
        self.assertIn('"/home/o w/chrome"', command)
        for bad in ('relative/chrome', '/a"b/chrome', '/a\nb'):
            with self.assertRaises(ValueError):
                cr.apparmor_profile(bad)

    def test_the_user_namespace_restriction_is_read_not_assumed(self):
        path = self.directory / 'restrict'
        path.write_text('1\n')
        self.assertTrue(cr.userns_restricted(path))
        path.write_text('0\n')
        self.assertFalse(cr.userns_restricted(path))
        self.assertFalse(cr.userns_restricted(self.directory / 'missing'))


class Item:
    def __init__(self, collection, attributes, secret):
        self.collection, self.attributes, self.secret = collection, attributes, secret

    def get_secret(self):
        return self.secret

    def delete(self):
        self.collection.items.remove(self)


class Collection:
    def __init__(self, locked=False, unlocks=True):
        self.items, self.locked, self.unlocks, self.unlock_calls = [], locked, unlocks, 0

    def is_locked(self):
        return self.locked

    def unlock(self):
        self.unlock_calls += 1
        if self.unlocks:
            self.locked = False
        return not self.unlocks

    def search_items(self, attributes):
        return [item for item in self.items if all(item.attributes.get(k) == v for k, v in attributes.items())]

    def create_item(self, label, attributes, secret, replace=False):
        if replace:
            for item in self.search_items(attributes):
                self.items.remove(item)
        self.items.append(Item(self, dict(attributes), bytes(secret)))


class SecretService:
    """A fake of the ``secretstorage`` module functions SecretServiceKey uses."""

    def __init__(self, collection=None, running=True):
        self.collection, self.running, self.opened, self.closed = collection or Collection(), running, 0, 0

    def dbus_init(self):
        if not self.running:
            raise RuntimeError('no session bus')
        self.opened += 1
        backend = self

        class Connection:
            def close(self):
                backend.closed += 1
        return Connection()

    def get_default_collection(self, connection):
        return self.collection


class DesktopKeyring(unittest.TestCase):
    def test_the_key_is_created_once_read_back_and_deleted(self):
        service = SecretService()
        key = SecretServiceKey('store-abc', backend=service)
        self.assertIsNone(key.get())
        created = key.create()
        self.assertEqual(key.get(), created)
        self.assertEqual(service.collection.items[0].attributes,
                         {'service': 'personal-agentos.browser-jar', 'account': 'store-abc'})
        other = SecretServiceKey('store-other', backend=service)
        self.assertIsNone(other.get(), 'another store never reads this key')
        self.assertTrue(key.delete())
        self.assertFalse(key.delete())
        self.assertEqual(service.closed, service.opened, 'every connection is closed')

    def test_a_locked_keyring_is_unlocked_through_its_prompt_or_refused(self):
        service = SecretService(Collection(locked=True))
        key = SecretServiceKey('store-abc', backend=service)
        self.assertIsNone(key.get())
        self.assertEqual(service.collection.unlock_calls, 1)
        refused = SecretService(Collection(locked=True, unlocks=False))
        with self.assertRaises(JarError) as caught:
            SecretServiceKey('store-abc', backend=refused).create()
        self.assertEqual(str(caught.exception), 'keyring_locked')
        self.assertEqual(refused.collection.items, [], 'nothing is written while locked')

    def test_no_secret_service_is_a_typed_failure_never_a_plaintext_fallback(self):
        key = SecretServiceKey('store-abc', backend=SecretService(running=False))
        for operation in (key.get, key.create):
            with self.assertRaises(JarError) as caught:
                operation()
            self.assertEqual(str(caught.exception), 'keyring_unavailable')
        with self.assertRaises(JarError) as caught:
            key.delete()
        self.assertEqual(str(caught.exception), 'keyring_delete_failed')

    def test_each_platform_gets_its_own_key_store(self):
        self.assertIsInstance(platform_key('store-abc', platform='linux'), SecretServiceKey)
        self.assertIsInstance(platform_key('store-abc', platform='darwin'), KeychainKey)
        self.assertEqual(platform_key('store-abc', 'other.service', platform='linux').service, 'other.service')
        with self.assertRaises(ValueError):
            SecretServiceKey('store abc')


class EngineChoice(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_linux_needs_its_packages_and_a_desktop_session(self):
        present = lambda name: object()
        self.assertIsNone(bs.engine_unavailable_reason('linux', present, {'DISPLAY': ':0'}))
        self.assertIsNone(bs.engine_unavailable_reason('linux', present, {'WAYLAND_DISPLAY': 'wayland-0'}))
        self.assertEqual(bs.engine_unavailable_reason('linux', present, {}), 'display')
        for missing in ('playwright', 'secretstorage'):
            self.assertEqual(bs.engine_unavailable_reason('linux', lambda name, m=missing: None if name == m else object(),
                                                          {'DISPLAY': ':0'}), 'dependency')
        self.assertEqual(bs.engine_unavailable_reason('win32', present, {'DISPLAY': ':0'}), 'platform')
        self.assertEqual(bs.engine_unavailable_reason('darwin', lambda name: None), 'dependency')
        with mock.patch.object(cr, 'pin', lambda **kwargs: None):
            self.assertEqual(bs.chromium_unavailable_reason(present, {'DISPLAY': ':0'}), 'platform')

    def test_each_reason_has_its_own_text(self):
        self.assertEqual(bs.unavailable_text('dependency', 'linux'), bs.LINUX_DEPENDENCY_TEXT)
        self.assertEqual(bs.unavailable_text('dependency', 'darwin'), bs.DEPENDENCY_TEXT)
        self.assertEqual(bs.unavailable_text('display', 'linux'), bs.DISPLAY_TEXT)
        self.assertEqual(bs.unavailable_text('platform', 'win32'), bs.PLATFORM_TEXT)
        self.assertEqual(bs.engine_name('linux'), 'linux-chromium')
        self.assertEqual(bs.engine_name('darwin'), 'macos-webkit')

    def profile(self):
        return bs.BrowserProfile(self.root / 'private' / 'browser-profile', jar=bs.CookieJar(
            self.root / 'private' / 'browser-profile' / 'jar', MemoryKey()), available=lambda: True)

    def test_the_chromium_is_kept_beside_the_stores_other_tools(self):
        self.assertEqual(self.profile().tools_dir, self.root / 'tools')

    def test_a_failed_download_is_a_typed_refusal_and_runs_nothing(self):
        profile = self.profile()
        with mock.patch.object(sys, 'platform', 'linux'), \
             mock.patch.object(cr, 'ensure', side_effect=RuntimeError('digest')), \
             mock.patch.object(bs, 'WebKitWorkerDriver', side_effect=AssertionError('never started')):
            with self.assertRaises(ToolError) as caught:
                profile._launch_engine(profile.profile_dir, True)
        self.assertEqual(caught.exception.code, bs.UNAVAILABLE_CODE)
        self.assertEqual(str(caught.exception), bs.DOWNLOAD_TEXT)
        self.assertEqual(profile.unavailable_reason(), 'download')

    def test_a_refused_sandbox_names_the_one_command_and_is_retried_next_time(self):
        profile = self.profile()
        chrome = str(self.root / 'tools' / 'chrome')
        started = []

        def worker(*args, **kwargs):
            started.append(kwargs['command'])
            raise bs.WorkerError('worker_unavailable', 'sandbox_unavailable')
        with mock.patch.object(sys, 'platform', 'linux'), mock.patch.object(cr, 'ensure', return_value=chrome), \
             mock.patch.object(bs, 'WebKitWorkerDriver', side_effect=worker):
            with self.assertRaises(ToolError) as caught:
                profile._launch_engine(profile.profile_dir, True)
        self.assertEqual(caught.exception.code, bs.UNAVAILABLE_CODE)
        self.assertIn(bs.SANDBOX_TEXT, str(caught.exception))
        self.assertIn(f'profile still-chromium "{chrome}"', str(caught.exception))
        self.assertNotIn('--no-sandbox', ' '.join(started[0]))
        self.assertEqual(started[0][2:4], ['-m', 'personal_agent.browser_worker_chromium'])
        self.assertEqual(started[0][-2:], ['--chromium', chrome])
        self.assertEqual(profile.unavailable_reason(), 'sandbox')
        with mock.patch.object(sys, 'platform', 'linux'):
            status = profile.status()
        self.assertEqual(status['unavailable_reason'], 'sandbox')
        self.assertIn('apparmor_parser', status['sandbox_command'])
        self.assertIsNotNone(profile.driver_factory('w'), 'the owner may have run the command: the next use tries again')
        with mock.patch.object(sys, 'platform', 'linux'), mock.patch.object(cr, 'ensure', return_value=chrome), \
             mock.patch.object(bs, 'WebKitWorkerDriver', return_value=object()):
            profile._launch_engine(profile.profile_dir, True)
        self.assertIsNone(profile.unavailable_reason())

    def test_a_start_failure_clears_so_settings_can_retry(self):
        """Codex review on #640: the remembered failure lifts once the profile names this Chromium, or in time."""
        profile = self.profile()
        chrome = '/home/o/tools/chromium/1/chrome-linux64/chrome'
        profile._start_blocked, profile._blocked_chrome, profile._blocked_at = 'sandbox', chrome, time.monotonic()
        self.assertEqual(profile.unavailable_reason(), 'sandbox')
        with mock.patch.object(cr, 'apparmor_installed', lambda path: path == chrome):
            self.assertIsNone(profile.unavailable_reason())
        profile._start_blocked, profile._blocked_at = 'download', time.monotonic() - bs.START_RETRY_SECONDS - 1
        self.assertIsNone(profile.unavailable_reason())
        profile._start_blocked, profile._blocked_at = 'download', time.monotonic()
        self.assertEqual(profile.unavailable_reason(), 'download')

    def test_the_apparmor_profile_is_recognised_only_for_this_binary(self):
        path = self.root / 'still-chromium'
        path.write_text(cr.apparmor_profile('/opt/a/chrome'))
        self.assertTrue(cr.apparmor_installed('/opt/a/chrome', path))
        self.assertFalse(cr.apparmor_installed('/opt/b/chrome', path))
        self.assertFalse(cr.apparmor_installed('/opt/a/chrome', self.root / 'missing'))

    def test_the_worker_receives_the_desktop_session_and_nothing_else(self):
        env = bs.worker_environment({'DISPLAY': ':0', 'XAUTHORITY': '/run/x', 'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/b',
                                     'XDG_RUNTIME_DIR': '/run/user/1000', 'OPENAI_API_KEY': 'sk-x', 'HOME': '/h'})
        self.assertEqual(env['DISPLAY'], ':0')
        self.assertEqual(env['DBUS_SESSION_BUS_ADDRESS'], 'unix:path=/b')
        self.assertNotIn('OPENAI_API_KEY', env)

    def test_a_changed_chromium_never_starts_not_even_on_a_restart(self):
        """Codex review P2 on #34: the digest is checked before every start, a crash restart included."""
        allowed = [True]
        script = self.root / 'ready.py'
        script.write_text('import json,sys\nprint(json.dumps({"event": "ready"}), flush=True)\nsys.stdin.read()\n')
        driver = bs.WebKitWorkerDriver('p', command=[sys.executable, str(script)], start_seconds=10, cwd=self.root,
                                       before_start=lambda: allowed[0])
        try:
            allowed[0] = False
            with self.assertRaises(bs.WorkerError) as caught:
                driver._start()
            self.assertEqual(caught.exception.detail, 'chromium_changed')
        finally:
            driver.close()

    def test_a_worker_that_cannot_start_says_why(self):
        """The driver keeps the worker's own ``unavailable`` reason (an Still code) for the profile."""
        script = self.root / 'worker.py'
        script.write_text('import json,sys\nprint(json.dumps({"event": "unavailable", "error": "sandbox_unavailable"}), flush=True)\n')
        with self.assertRaises(bs.WorkerError) as caught:
            bs.WebKitWorkerDriver('p', command=[sys.executable, str(script)], start_seconds=10, cwd=self.root)
        self.assertEqual((caught.exception.code, caught.exception.detail), ('worker_unavailable', 'sandbox_unavailable'))


if __name__ == '__main__':
    unittest.main()
