"""SEC-BROWSER-02 (#680): the embedded macOS WebKit engine and the encrypted session jar.

Evidence classes, named separately:

* unit (any platform): the encrypted jar (Fernet file at 0600, per-site
  names/counts/times only, unreadable with another key), the Keychain key
  adapter with an injected ``security`` runner (the key never on a command
  line), the profile's save-on-close / per-site delete / delete-all over a
  fake driver, and backup/export exclusion of the jar;
* model-free process integration (any platform): ``WebKitWorkerDriver`` over
  ``tests/browser_fake_worker.py`` speaking the worker protocol (timeouts,
  crash restart with the jar's cookies imported again, typed target errors);
* macOS integration (real ``/usr/bin/security``, test-only service name,
  cleaned up): the Keychain round trip;
* macOS + PyObjC integration (the real worker process, real WebKit): the
  fixture site's product -> 장바구니 -> cart flow, the account page without
  secrets, the payment guard, login_required, trusted native input, cookies
  surviving a worker restart through the jar, per-site delete, and no
  plaintext fixture cookie value under the data directory,
  ``~/Library/WebKit`` or ``~/Library/HTTPStorages``.

No site, provider or category is named in ``src``; the fixture site is the test's own.
"""
import json
import os

import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from personal_agent import browser_session as bs
from personal_agent.agent_runtime import ToolError, WorkBudget
from personal_agent.browser_jar import JAR_NAME, CookieJar, JarError, KeychainKey, MemoryKey, SERVICE, store_account
from personal_agent.portable_state import export_owner_state
from personal_agent.quickstart_store import QuickStore

from test_browser_session import OTP, PASSWORD, PASSPORT, TOKEN, Approvals, FixtureHandler, flat

ROOT = Path(__file__).resolve().parents[1]
FAKE_WORKER = Path(__file__).resolve().with_name('browser_fake_worker.py')


def cookie(domain, name='sid', value='v', expires=None):
    return {'name': name, 'value': value, 'domain': domain, 'path': '/', 'expires': expires, 'secure': False,
            'http_only': True, 'same_site': None}


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


# ---------------------------------------------------------------- the jar (unit)

class JarTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = Clock()
        self.key = MemoryKey()
        self.jar = CookieJar(Path(self.tmp.name) / 'private' / 'browser-profile' / JAR_NAME, self.key, self.clock)

    def test_saved_cookies_are_encrypted_at_0600_and_listed_without_values(self):
        value = 'fixture-' + uuid.uuid4().hex
        self.jar.save_export({'shop.test': [cookie('.shop.test', value=value), cookie('www.shop.test', 'pref')],
                              'news.test': [cookie('news.test')]}, hosts=['www.shop.test'])
        raw = self.jar.path.read_bytes()
        self.assertNotIn(value.encode(), raw)
        self.assertNotIn(b'shop.test', raw, 'even site names are inside the ciphertext')
        self.assertEqual(stat.S_IMODE(self.jar.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.jar.path.parent.stat().st_mode), 0o700)
        self.assertEqual(self.jar.state(), 'stored')
        sites = self.jar.sites()
        self.assertEqual([(row['site'], row['cookies'], row['last_used']) for row in sites],
                         [('news.test', 1, 1000.0), ('shop.test', 2, 1000.0)])
        self.assertNotIn(value, flat(sites))
        self.assertIn(value, flat(self.jar.cookies()), 'the worker import gets the values back')

    def test_last_used_moves_only_for_sites_this_worker_opened(self):
        self.jar.save_export({'shop.test': [cookie('shop.test')], 'news.test': [cookie('news.test')]}, hosts=[])
        self.clock.now = 2000.0
        self.jar.save_export({'shop.test': [cookie('shop.test')], 'news.test': [cookie('news.test')]},
                             hosts=['m.shop.test'])
        used = {row['site']: row['last_used'] for row in self.jar.sites()}
        self.assertEqual(used, {'shop.test': 2000.0, 'news.test': 1000.0})

    def test_expired_rows_are_not_imported_and_remove_clear_work(self):
        self.jar.save_export({'shop.test': [cookie('shop.test', expires=500.0), cookie('shop.test', 'live', expires=5000.0)],
                              'news.test': [cookie('news.test')]})
        self.assertEqual(sorted(row['name'] for row in self.jar.cookies()), ['live', 'sid'])
        self.assertTrue(self.jar.remove('shop.test'))
        self.assertFalse(self.jar.remove('shop.test'))
        self.assertEqual([row['site'] for row in self.jar.sites()], ['news.test'])
        self.assertEqual(self.jar.clear(), {'jar_deleted': True, 'key_deleted': True, 'key_error': None})
        self.assertFalse(self.jar.path.exists())
        self.assertIsNone(self.key.get(), 'delete-all also deletes the key this jar owns')
        self.assertEqual(self.jar.state(), 'empty')

    def test_another_key_cannot_read_it_and_nothing_falls_back_to_plaintext(self):
        self.jar.save_export({'shop.test': [cookie('shop.test', value='secret-value')]})
        other = CookieJar(self.jar.path, MemoryKey(MemoryKey().create()), self.clock)
        self.assertEqual(other.state(), 'unreadable')
        self.assertEqual(other.sites(), [])
        self.assertEqual(other.cookies(), [])
        with self.assertRaises(JarError):
            other.remove('shop.test')
        missing = CookieJar(self.jar.path, MemoryKey(), self.clock)
        self.assertEqual(missing.state(), 'key_missing')
        # A key that cannot be stored means no jar at all, never a plaintext file.
        class NoKeychain(MemoryKey):
            def create(self):
                raise JarError('keychain_unavailable')
        broken = CookieJar(Path(self.tmp.name) / 'other' / JAR_NAME, NoKeychain(), self.clock)
        with self.assertRaises(JarError):
            broken.save_export({'shop.test': [cookie('shop.test', value='secret-value')]})
        self.assertFalse(broken.path.exists())

    def test_a_key_that_cannot_be_removed_is_reported_not_hidden(self):
        class Stuck(MemoryKey):
            def delete(self):
                raise JarError('keychain_delete_failed')
        jar = CookieJar(Path(self.tmp.name) / 'stuck' / JAR_NAME, Stuck(), self.clock)
        jar.save_export({'shop.test': [cookie('shop.test')]})
        self.assertEqual(jar.clear(), {'jar_deleted': True, 'key_deleted': False, 'key_error': 'keychain_delete_failed'})
        self.assertFalse(jar.path.exists(), 'the sessions themselves are gone')

    def test_saving_merges_and_never_drops_sites_that_were_not_imported(self):
        self.jar.save_export({'shop.test': [cookie('shop.test')], 'news.test': [cookie('news.test')],
                              'mail.test': [cookie('mail.test')]})
        sites, rows = self.jar.import_rows()
        self.assertEqual(sites, {'shop.test', 'news.test', 'mail.test'})
        # This worker was given shop and news only; it signed out of news and signed in to docs.
        self.jar.save_export({'shop.test': [cookie('shop.test', value='new')], 'docs.test': [cookie('docs.test')]},
                             imported={'shop.test', 'news.test'})
        self.assertEqual(sorted(row['site'] for row in self.jar.sites()), ['docs.test', 'mail.test', 'shop.test'])
        # With a set, an unreadable jar is never overwritten.
        other = CookieJar(self.jar.path, MemoryKey(MemoryKey().create()), self.clock)
        with self.assertRaises(JarError):
            other.import_rows()
        before = self.jar.path.read_bytes()
        with self.assertRaises(JarError):
            other.save_export({'x.test': [cookie('x.test')]}, imported=set())
        self.assertEqual(self.jar.path.read_bytes(), before)

    def test_an_empty_export_does_not_create_a_file(self):
        self.jar.save_export({})
        self.assertFalse(self.jar.path.exists())
        self.assertIsNone(self.key.get(), 'no key is created before there is a session to protect')


class KeychainKeyTests(unittest.TestCase):
    def runner(self, results):
        calls = []

        def run(args, input=None, **kwargs):
            calls.append((list(args), input))
            code, out = results.pop(0)
            return subprocess.CompletedProcess(args, code, stdout=out, stderr='')
        return run, calls

    def test_the_key_goes_through_stdin_never_argv(self):
        stored = {}

        def run(args, input=None, **kwargs):
            run.calls.append((list(args), input))
            if args[1] == '-i':
                stored['hex'] = input.split(' -X ')[1].split()[0]
                return subprocess.CompletedProcess(args, 0, stdout='', stderr='')
            if args[1] == 'find-generic-password':
                if 'hex' not in stored:
                    return subprocess.CompletedProcess(args, 44, stdout='', stderr='')
                return subprocess.CompletedProcess(args, 0, stdout=bytes.fromhex(stored['hex']).decode() + '\n', stderr='')
            return subprocess.CompletedProcess(args, 0, stdout='', stderr='')
        run.calls = []
        key = KeychainKey('store-abc', runner=run)
        self.assertIsNone(key.get())
        created = key.create()
        self.assertEqual(key.get(), created)
        for args, stdin in run.calls:
            self.assertNotIn(created.decode(), ' '.join(args))
            self.assertNotIn(created.hex(), ' '.join(args))
        self.assertEqual(run.calls[1][0], ['/usr/bin/security', '-i'])
        self.assertIn(f'-s {SERVICE} ', run.calls[1][1])
        self.assertIn('-a store-abc ', run.calls[1][1])

    def test_errors_are_typed_and_tokens_are_plain(self):
        run, _ = self.runner([(1, ''), (0, ''), (44, ''), (51, '')])
        key = KeychainKey('store-abc', runner=run)
        with self.assertRaises(JarError):
            key.get()
        self.assertTrue(key.delete())
        self.assertFalse(key.delete(), 'no item: nothing to delete')
        with self.assertRaises(JarError) as caught:
            key.delete()
        self.assertEqual(str(caught.exception), 'keychain_delete_failed')
        for bad in ('store abc', 'x;rm', '', 'a\nb'):
            with self.assertRaises(ValueError):
                KeychainKey(bad)
        self.assertRegex(store_account('/tmp/x'), r'^store-[0-9a-f]{24}$')
        self.assertEqual(store_account('/tmp/x'), store_account('/tmp/x/'))


@unittest.skipUnless(sys.platform == 'darwin' and Path('/usr/bin/security').exists() and __import__('os').environ.get('AGENTOS_REAL_BROWSER_TESTS') == '1', 'real macOS Keychain; set AGENTOS_REAL_BROWSER_TESTS=1')
class KeychainIntegrationTests(unittest.TestCase):
    """Real ``/usr/bin/security`` with a test-only service name that is deleted afterwards."""

    def test_create_read_delete_round_trip(self):
        key = KeychainKey('store-test-' + uuid.uuid4().hex[:12], service='personal-agentos.browser-jar.test-' + uuid.uuid4().hex[:12])
        self.addCleanup(key.delete)
        self.assertIsNone(key.get())
        created = key.create()
        self.assertEqual(key.get(), created)
        self.assertTrue(key.delete())
        self.assertIsNone(key.get())


# ---------------------------------------------------------------- the profile over a fake driver (unit)

class CookieDriver:
    """A driver that holds cookie rows like the worker's in-memory store."""

    def __init__(self, rows=(), fail_delete=False):
        self.rows = list(rows)
        self.fail_delete = fail_delete
        self.closed = False
        self.calls = []

    def cookies_export(self):
        grouped = {}
        for row in self.rows:
            grouped.setdefault(row['domain'].lstrip('.'), []).append(row)
        return grouped, ['shop.test']

    def cookies_delete(self, site):
        self.calls.append(('delete', site))
        if self.fail_delete:
            raise RuntimeError('worker gone')
        self.rows = [row for row in self.rows if row['domain'].lstrip('.') != site]

    def cookies_clear(self):
        self.calls.append(('clear',))
        if self.fail_delete:
            raise RuntimeError('worker gone')
        self.rows = []

    def discard(self):
        self.calls.append(('discard',))

    def close(self):
        self.closed = True


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.jar = CookieJar(Path(self.tmp.name) / JAR_NAME, MemoryKey(), Clock())
        self.driver = CookieDriver([cookie('shop.test', value='shop-secret'), cookie('news.test', value='news-secret')])
        self.profile = bs.BrowserProfile(Path(self.tmp.name), launcher=lambda d, h: self.driver, jar=self.jar)

    def test_closing_a_session_saves_its_cookies_into_the_jar_only(self):
        session = self.profile.driver_factory('w1')()
        self.assertTrue(self.profile.status()['in_use'])
        session.close()
        self.assertTrue(self.driver.closed)
        self.assertEqual([row['site'] for row in self.profile.status()['sessions']], ['news.test', 'shop.test'])
        status = self.profile.status()
        self.assertFalse(status['in_use'])
        for secret in ('shop-secret', 'news-secret'):
            self.assertNotIn(secret, flat(status))

    def test_delete_one_site_reaches_the_running_worker_and_the_jar(self):
        self.jar.save_export({'shop.test': [cookie('shop.test')], 'news.test': [cookie('news.test')]})
        session = self.profile.driver_factory('w1')()
        self.assertEqual(self.profile.delete_site('SHOP.test '), {'deleted': True, 'site': 'shop.test'})
        self.assertIn(('delete', 'shop.test'), self.driver.calls)
        session.close()
        self.assertEqual([row['site'] for row in self.profile.status()['sessions']], ['news.test'],
                         'the running worker no longer had it, so closing did not save it back')
        with self.assertRaises(ValueError):
            self.profile.delete_site('  ')

    def test_a_worker_that_cannot_delete_is_stopped_and_nothing_it_holds_is_saved(self):
        self.jar.save_export({'shop.test': [cookie('shop.test')]})
        self.driver.fail_delete = True
        session = self.profile.driver_factory('w1')()
        reply = self.profile.delete_site('shop.test')
        self.assertEqual((reply['deleted'], reply['running_browser'], reply['removed_from_jar']), (False, 'failed', True),
                         'the deletion is not acknowledged')
        self.assertEqual(reply['message'], bs.WORKER_DELETE_FAILED_TEXT)
        self.assertIn(('discard',), self.driver.calls, 'the worker is stopped without an export')
        self.driver.rows.append(cookie('late.test'))   # acquired after the failed delete
        session.close()
        self.assertEqual(self.profile.status()['sessions'], [], 'no site of that worker was saved, the late one included')
        self.assertEqual(self.profile.status()['storage']['save_error'], 'worker_delete_failed')

    def test_a_failed_clear_in_the_running_worker_is_reported_and_blocks_its_export(self):
        self.jar.save_export({'shop.test': [cookie('shop.test')]})
        self.driver.fail_delete = True
        session = self.profile.driver_factory('w1')()
        reply = self.profile.delete_all()
        self.assertFalse(reply['deleted'])
        self.assertEqual((reply['running_browser'], reply['jar_deleted'], reply['key_error']), ('failed', True, None))
        self.assertEqual(reply['message'], bs.WORKER_DELETE_FAILED_TEXT)
        self.driver.rows.append(cookie('late.test', value='late-secret'))
        session.close()
        self.assertFalse(self.jar.path.exists(), 'nothing from that worker was saved after the clear')

    def test_delete_all_reports_a_key_that_could_not_be_removed(self):
        class Stuck(MemoryKey):
            def delete(self):
                raise JarError('keychain_delete_failed')
        jar = CookieJar(Path(self.tmp.name) / 'stuck' / JAR_NAME, Stuck(), Clock())
        jar.save_export({'shop.test': [cookie('shop.test')]})
        profile = bs.BrowserProfile(Path(self.tmp.name) / 'stuck', launcher=lambda d, h: self.driver, jar=jar)
        reply = profile.delete_all()
        self.assertEqual((reply['deleted'], reply['jar_deleted'], reply['key_deleted'], reply['key_error']),
                         (False, True, False, 'keychain_delete_failed'))
        self.assertEqual(reply['message'], bs.KEY_DELETE_FAILED_TEXT)

    def test_a_jar_that_cannot_be_read_at_start_is_never_saved_over(self):
        from unittest import mock
        self.jar.save_export({'shop.test': [cookie('shop.test')], 'news.test': [cookie('news.test')]})
        before = self.jar.path.read_bytes()
        locked = CookieJar(self.jar.path, MemoryKey(), Clock())   # the Keychain key cannot be read

        class Worker(CookieDriver):
            def __init__(self, profile_id, seed=None, **kwargs):
                super().__init__([cookie('only.test')])
                self.seeded = seed()
        with mock.patch.object(bs, 'WebKitWorkerDriver', Worker):
            profile = bs.BrowserProfile(Path(self.tmp.name), jar=locked, available=lambda: True)
            session = profile.driver_factory('w1')()
            self.assertEqual(session.seeded, [])
            session.close()
        self.assertEqual(self.jar.path.read_bytes(), before, 'the jar was left as it was')
        self.assertEqual(profile.save_error, 'key_missing')
        # With a readable jar the import is recorded and the save merges.
        with mock.patch.object(bs, 'WebKitWorkerDriver', Worker):
            profile = bs.BrowserProfile(Path(self.tmp.name), jar=self.jar, available=lambda: True)
            session = profile.driver_factory('w2')()
            self.assertEqual(len(session.seeded), 2)
            session.close()
        self.assertEqual([row['site'] for row in self.jar.sites()], ['only.test'],
                         'imported sites the worker no longer had were dropped, the new one kept')

    def test_the_legacy_playwright_profile_is_deleted_once_and_by_delete_all(self):
        from personal_agent.quickstart_service import AgentService
        store = QuickStore(Path(self.tmp.name) / 'data')
        legacy = store.private / 'browser-profile'
        (legacy / 'Default').mkdir(parents=True)
        (legacy / 'Default' / 'Cookies').write_bytes(b'plaintext-legacy-cookie')
        (legacy / 'Local State').write_text('{}')
        jar = CookieJar(legacy / JAR_NAME, MemoryKey(), Clock())
        jar.save_export({'shop.test': [cookie('shop.test')]})
        profile = bs.BrowserProfile(legacy, launcher=lambda d, h: self.driver, jar=jar)
        service = AgentService(store, browser_profile=profile)
        self.assertEqual(sorted(path.name for path in legacy.iterdir()), [JAR_NAME], 'only the encrypted jar is left')
        self.assertIsNotNone(service.browser_status()['legacy_profile_removed_at'])
        (legacy / 'Default').mkdir()
        service.delete_browser_sessions({'all': True})
        self.assertFalse(any(legacy.iterdir()))
        self.assertIsNone(service.browser_status()['legacy_profile_removed_at'])

    def test_settings_never_waits_on_the_keychain(self):
        import threading as _threading
        gate = _threading.Event()

        class Slow(MemoryKey):
            def get(self):
                gate.wait(5)
                return super().get()
        key = Slow(MemoryKey().create())
        writer = CookieJar(self.jar.path, MemoryKey(key.key), Clock())
        writer.save_export({'shop.test': [cookie('shop.test')]})
        profile = bs.BrowserProfile(Path(self.tmp.name), launcher=lambda d, h: self.driver,
                                    jar=CookieJar(self.jar.path, key, Clock()))
        started = time.monotonic()
        self.assertEqual(profile.status()['storage']['state'], 'checking')
        self.assertLess(time.monotonic() - started, 1.0)
        gate.set()
        for _ in range(100):
            if profile.status()['storage']['state'] != 'checking':
                break
            time.sleep(0.02)
        self.assertEqual(profile.status()['storage']['state'], 'stored')
        self.assertEqual([row['site'] for row in profile.status()['sessions']], ['shop.test'])

    def test_delete_all_clears_the_worker_the_jar_and_the_key(self):
        self.jar.save_export({'shop.test': [cookie('shop.test')]})
        session = self.profile.driver_factory('w1')()
        reply = self.profile.delete_all()
        self.assertEqual((reply['deleted'], reply['all'], reply['key_deleted'], reply['running_browser']),
                         (True, True, True, 'cleared'))
        self.assertIn(('clear',), self.driver.calls)
        self.assertIsNone(self.jar.key.get())
        session.close()
        self.assertEqual(self.profile.status()['sessions'], [])

    def test_the_login_window_saves_when_the_owner_closes_it(self):
        class Window(CookieDriver):
            def __init__(self, rows):
                super().__init__(rows)
                self.shown = []

            def show(self, url, timeout):
                self.shown.append(url)

            def is_open(self):
                return False
        window = Window([cookie('shop.test', value='login-secret')])
        profile = bs.BrowserProfile(Path(self.tmp.name), launcher=lambda d, h: window, jar=self.jar)
        receipt = profile.open_for_login('https://shop.test/login?next=/', wait=True)
        self.assertEqual((receipt['state'], receipt['url']), ('closed', 'https://shop.test/login'))
        self.assertEqual(window.shown, ['https://shop.test/login?next=/'])
        self.assertTrue(window.closed)
        self.assertEqual([row['site'] for row in profile.status()['sessions']], ['shop.test'])
        self.assertNotIn('login-secret', flat(receipt))


# ---------------------------------------------------------------- backup / export exclusion (unit)

class BackupExclusionTests(unittest.TestCase):
    def test_export_and_backup_never_include_the_jar(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = QuickStore(Path(tmp.name) / 'data')
        value = 'fixture-' + uuid.uuid4().hex
        key = MemoryKey()
        jar = CookieJar(store.private / 'browser-profile' / JAR_NAME, key)
        jar.save_export({'shop.test': [cookie('shop.test', value=value)]})
        ciphertext = jar.path.read_bytes()
        self.assertNotIn(value.encode(), ciphertext)
        archives = [export_owner_state(store.root, Path(tmp.name) / 'export.tar.gz')]
        script = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'agentos-backup.py'), str(store.root),
                                 str(Path(tmp.name) / 'backup.tar.gz')], capture_output=True, text=True, check=True)
        archives.append(Path(script.stdout.strip()))
        for archive in archives:
            with tarfile.open(archive) as bundle:
                names = bundle.getnames()
                self.assertFalse([name for name in names if 'browser-profile' in name or JAR_NAME in name], names)
                for member in bundle.getmembers():
                    if member.isfile():
                        data = bundle.extractfile(member).read()
                        self.assertNotIn(value.encode(), data)
                        self.assertNotIn(ciphertext[:40], data)


# ---------------------------------------------------------------- the driver's process boundary (fake worker)

class DriverProtocolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = Path(self.tmp.name) / 'commands.jsonl'
        self.seeded = [cookie('shop.test', value='seed-value')]

    def driver(self, **kwargs):
        driver = bs.WebKitWorkerDriver('p', seed=lambda: self.seeded,
                                       command=[sys.executable, str(FAKE_WORKER), str(self.log)], **kwargs)
        self.addCleanup(driver.close)
        return driver

    def commands(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_the_owners_stop_ends_a_running_load_more(self):
        """#244: a Stop while the worker scrolls asks it to end that request; it answers at once, stopped."""
        driver = self.driver()
        started = time.monotonic()
        answer = driver.load_more(4, 12.0, 15, stop=lambda: time.monotonic() - started > 0.5)
        self.assertLess(time.monotonic() - started, 3, 'not at the 12 s bound')
        self.assertEqual(answer['stopped'], 'stopped')
        request, stop = self.commands()[-2:]
        self.assertEqual((request['op'], stop['op'], stop['target']), ('load_more', 'load_more_stop', request['id']))
        self.assertNotIn('stop', request, 'the callable never crosses the process boundary')
        driver.goto('https://shop.test/after', 4)   # the stop's own answer did not confuse the next request
        self.assertEqual(self.commands()[-1]['op'], 'navigate')

    def test_requests_carry_timeouts_and_targets_carry_the_classified_descriptor(self):
        driver = self.driver()
        self.assertEqual(self.commands()[0]['op'], 'cookies_import', 'the jar is imported on start')
        self.assertEqual(self.commands()[0]['cookies'], self.seeded)
        driver.goto('https://shop.test/p', 7)
        page = driver.snapshot()
        self.assertEqual(page['url'], 'https://shop.test/p')
        driver.click(3, 4)
        driver.type(3, 'hello', 4)
        ops = self.commands()
        self.assertEqual([c['op'] for c in ops], ['cookies_import', 'navigate', 'snapshot', 'click', 'type'])
        self.assertEqual(ops[1]['timeout'], 7.0)
        # The full descriptor the guard classified, and the guard's payment tokens.
        self.assertEqual(ops[3]['expect'], {'tag': 'button', 'type': 'submit', 'autocomplete': '', 'name': 'Go',
                                            'own_text': '', 'label_name': '', 'ancestor_text': '', 'in_form': False,
                                            'payment_form': False, 'nav_link': False, 'pressable': True, 'href': ''})
        self.assertEqual(ops[3]['tokens'], sorted(bs.PAYMENT_AUTOCOMPLETE))
        self.assertEqual(ops[4]['expect'], ops[3]['expect'])
        self.assertEqual((ops[3]['approved'], ops[4]['approved']), (False, False), 'unapproved unless the session says so')
        # #698: an element whose label forwards to the card form carries payment_form, and
        # the worker's cancelled payment-form submit is the typed approval refusal.
        with self.assertRaises(ToolError) as caught:
            driver.click(9, 4)
        self.assertEqual((caught.exception.code, caught.exception.requires), ('approval_required', 'browser-step-approval'))
        held = {'dom': 0, 'method': 'post', 'action': 'https://shop.test/pay', 'page': 'https://shop.test/p', 'state': 'a' * 64}
        self.assertEqual(caught.exception.cancelled_form, held,
                         'the refusal names the form whose submit was cancelled and the digest of what it would send')
        self.assertEqual(self.commands()[-1]['expect']['payment_form'], True)
        # #700: the approved held submit is released by its record, once; any other is typed.
        with self.assertRaises(ToolError) as caught:
            driver.release_submit(dict(held, state='b' * 64), 4)
        self.assertEqual(caught.exception.code, 'target_unavailable')
        self.assertEqual(driver.release_submit(dict(held, extra='never sent'), 4), {'navigated': True})
        self.assertEqual(self.commands()[-1]['form'], held, 'only the record fields cross')
        with self.assertRaises(ToolError):
            driver.release_submit(held, 4)
        driver.goto('https://shop.test/refusedpost', 4)
        driver.snapshot()
        with self.assertRaises(ToolError) as caught:
            driver.click(3, 4)
        self.assertEqual((caught.exception.code, str(caught.exception)), ('submit_refused', bs.SUBMIT_REFUSED_TEXT))
        driver.goto('https://shop.test/p', 4)
        driver.snapshot()
        driver.click(9, 4, approved=True)
        self.assertIs(self.commands()[-1]['approved'], True)
        with self.assertRaises(ToolError):
            driver.click(9, 4, approved='yes')
        self.assertIs(self.commands()[-1]['approved'], False, 'only a literal True approves')
        with self.assertRaises(ToolError) as caught:
            driver.click(7, 4)   # the fake worker answers target_obscured for it
        self.assertEqual(caught.exception.code, 'target_unavailable')
        # #1395: the worker's cover rides along for the session to name in model terms.
        self.assertEqual(caught.exception.cover, {'text': 'Open in app', 'controls': [{'index': 3, 'name': 'Go'}]})
        self.assertEqual(self.commands()[-1]['expect']['payment_form'], True, "the pay button's form holds a card field")
        with self.assertRaises(ToolError) as caught:
            driver.type(3, 'moved', 4)   # the fake worker answers focus_moved for it
        self.assertEqual(caught.exception.code, 'target_unavailable')
        # #12: the field the page moved the focus to rides along for the session to name.
        self.assertEqual(caught.exception.focused, {'name': 'Search here'})
        self.assertEqual(str(caught.exception), bs.target_text('focus_moved'))
        # #11: a bounded load_more request and the worker's answer.
        self.assertEqual(driver.load_more(3, 9.0, 12), {'rounds': 2, 'stopped': 'no_progress', 'added': 3,
                                                       'text': 'row 11\nrow 12', 'navigation_refused': 1, 'capped': None})
        self.assertEqual({key: self.commands()[-1][key] for key in ('op', 'rounds', 'seconds', 'timeout')},
                         {'op': 'load_more', 'rounds': 3, 'seconds': 9.0, 'timeout': 12.0})
        self.assertNotIn('load_more_stop', [command['op'] for command in self.commands()], 'no Stop: never asked to stop')
        sent = len(self.commands())
        self.assertEqual(driver.snapshot()['title'], 'held', 'the page captured under the navigation hold is returned')
        self.assertEqual(len(self.commands()), sent, 'without reading the page again')
        self.assertNotEqual(driver.snapshot()['title'], 'held', 'once only')
        driver.goto('https://shop.test/intercepted', 4)
        driver.snapshot()
        with self.assertRaises(ToolError) as caught:
            driver.click(3, 4)   # #102: the press landed elsewhere because the target moved
        # #103: after the press reached the page, the miss is a press of unknown effect.
        self.assertEqual((caught.exception.code, str(caught.exception), caught.exception.effect, caught.exception.posted),
                         (bs.PRESS_UNCONFIRMED_CODE, bs.press_text(), 'unknown', False))
        sent = len(self.commands())
        with self.assertRaises(ToolError) as caught:
            driver.type(5, 'x', 4)   # index 5 was never in a snapshot: nothing is sent
        self.assertEqual(caught.exception.code, 'target_unavailable')
        self.assertEqual(len(self.commands()), sent)
        with self.assertRaises(ToolError) as caught:
            driver.goto('https://shop.test/blocked', 3)
        self.assertEqual(caught.exception.code, 'blocked_destination')
        self.assertEqual(driver.cookies_export(), ({'shop.test': self.seeded}, []))

    def test_the_worker_runs_with_a_minimal_environment_a_fixed_cwd_and_no_cwd_on_its_path(self):
        from unittest import mock
        seen = {}

        class Stop(Exception):
            pass

        def popen(command, **kwargs):
            seen['command'], seen['kwargs'] = command, kwargs
            raise Stop()
        planted = {'OPENAI_API_KEY': 'sk-planted', 'ANTHROPIC_API_KEY': 'planted', 'GITHUB_API_KEY': 'planted',
                   'GH_TOKEN': 'planted', 'AGENTOS_DATA': '/planted', 'TELEGRAM_BOT_TOKEN': 'planted',
                   'AWS_SECRET_ACCESS_KEY': 'planted', 'HOME': '/Users/owner', 'PATH': '/usr/bin:/bin', 'LANG': 'ko_KR.UTF-8'}
        with mock.patch.dict(os.environ, planted), mock.patch.object(bs.subprocess, 'Popen', popen):
            with self.assertRaises(Stop):
                bs.WebKitWorkerDriver('p', cwd=self.tmp.name)
        env = seen['kwargs']['env']
        self.assertLessEqual(set(env), set(bs.WORKER_ENVIRONMENT))
        self.assertNotIn('planted', json.dumps(env))
        self.assertEqual((env['HOME'], env['LANG']), ('/Users/owner', 'ko_KR.UTF-8'))
        self.assertEqual(seen['kwargs']['cwd'], self.tmp.name)
        self.assertEqual(seen['command'][:4], [sys.executable, '-P', '-m', 'personal_agent.browser_worker'])
        self.assertNotIn('--test-allow-origin', seen['command'], 'production never allows a local origin')

    def test_worker_timeout_hang_and_crash_are_typed_and_the_next_call_restarts(self):
        driver = self.driver(grace_seconds=0.5)
        with self.assertRaises(TimeoutError):
            driver.goto('https://shop.test/slow', 2)
        self.assertTrue(driver.alive(), 'a worker-side timeout keeps the worker')
        with self.assertRaises(TimeoutError):
            driver.goto('https://shop.test/hang', 1)
        self.assertFalse(driver.alive(), 'an unresponsive worker is killed')
        driver.goto('https://shop.test/ok', 3)
        self.assertEqual(driver.restarts, 1)
        with self.assertRaises(bs.WorkerError):
            driver.goto('https://shop.test/crash', 3)
        driver.goto('https://shop.test/again', 3)
        self.assertEqual(driver.restarts, 2)
        imports = [c for c in self.commands() if c['op'] == 'cookies_import']
        self.assertEqual(len(imports), 3, 'every (re)start imports the jar again')

    def test_a_session_maps_driver_failures_to_typed_tool_errors(self):
        driver = self.driver(grace_seconds=0.5)
        session = bs.BrowserSession(lambda: driver, work_id='w', action_seconds=1)
        page = session.open({'url': 'https://shop.test/p', 'effect': 'navigate'})
        self.assertEqual(page['state'], 'page')
        with self.assertRaises(ToolError) as caught:
            session.open({'url': 'https://shop.test/hang', 'effect': 'navigate'})
        self.assertEqual(caught.exception.code, 'browser_timeout')
        with self.assertRaises(ToolError) as caught:
            session.open({'url': 'https://shop.test/crash', 'effect': 'navigate'})
        self.assertEqual(caught.exception.code, 'browser_failed')
        self.assertNotIn('crash', str(caught.exception))

    def test_an_engine_that_cannot_start_is_a_typed_failure(self):
        env = dict(os.environ, AGENTOS_FAKE_WORKER_MODE='unavailable')
        original = subprocess.Popen

        def popen(*args, **kwargs):
            kwargs['env'] = env
            return original(*args, **kwargs)
        from unittest import mock
        with mock.patch.object(bs.subprocess, 'Popen', popen):
            with self.assertRaises(bs.WorkerError) as caught:
                bs.WebKitWorkerDriver('p', command=[sys.executable, str(FAKE_WORKER), str(self.log)])
        self.assertEqual(caught.exception.code, 'worker_unavailable')

    def test_a_close_that_arrives_while_show_is_pending_is_not_lost(self):
        driver = self.driver()
        driver.show('https://shop.test/closefirst', 5)   # the worker reports 'hidden' before answering
        self.assertFalse(driver.is_open())

    def test_the_login_window_state_follows_the_owner_closing_it(self):
        driver = self.driver()
        driver.show('https://shop.test/login', 5)
        self.assertTrue(driver.is_open())
        driver.show('https://shop.test/autoclose', 5)
        for _ in range(40):
            if not driver.is_open():
                break
            time.sleep(0.05)
        self.assertFalse(driver.is_open())


# ---------------------------------------------------------------- destinations, output encoding, the UI marker (unit)

class WorkerGuardLogicTests(unittest.TestCase):
    """#700: the worker's own Python side of the submit guard, without WebKit (any platform)."""

    def worker(self, **fields):
        from personal_agent import browser_worker as bw
        worker = object.__new__(bw.Worker)   # no Cocoa objects: only the guard's bookkeeping
        worker.guard_off, worker.cancelled, worker.reported, worker.held = False, None, [], None
        worker.pending, worker.deciding, worker.failed, worker.ran = {}, 0, [], []
        worker.refused_submits = worker.step_refused = 0
        worker.posted_submits = worker.step_posted = 0
        worker.fail = lambda ident, code: worker.failed.append((ident, code))
        worker.run = lambda body, arguments, done, world=None: worker.ran.append((body, arguments, done))
        for key, value in fields.items():
            setattr(worker, key, value)
        return bw, worker

    def test_a_cancelled_submit_crosses_as_a_digest_of_what_it_would_send(self):
        bw, worker = self.worker()
        state = 'who=홍길동\namount=990000\ncard=<guarded>\n\x1e결제 금액 990,000원'
        raw = {'id': 'x1', 'dom': 1, 'method': 'post', 'action': 'https://shop.test/pay', 'page': 'https://shop.test/c?q=1',
               'state': state}
        record = worker.take_cancelled(raw)
        import hashlib
        self.assertEqual(record, {'dom': 1, 'method': 'post', 'action': 'https://shop.test/pay',
                                  'page': 'https://shop.test/c?q=1', 'state': hashlib.sha256(state.encode()).hexdigest()})
        self.assertNotIn('990000', json.dumps(record), 'field values never leave the worker')
        self.assertEqual(worker.held['id'], 'x1', 'what an approval may release')
        self.assertIsNone(worker.take_cancelled(), 'reported once')
        self.assertIsNone(worker.take_cancelled(raw), 'the same page record is not reported twice')

    def test_a_stop_ends_a_running_load_more_at_its_next_probe(self):
        """#244: ``load_more_stop`` names the running request; its next probe finishes it as ``stopped``."""
        emitted, later = [], []

        class View:
            def isLoading(self):
                return False

        class AppHelper:
            @staticmethod
            def callLater(seconds, then):
                later.append(then)
        bw, worker = self.worker(emit=emitted.append, view=View(), AppHelper=AppHelper, stopping=set(),
                                 reading=False, read_navigations=0)
        worker._snapshot_page = lambda ident, captured: captured({'url': 'https://shop.test/feed'}, None)

        def step(value):
            body, arguments, done = worker.ran.pop()
            done(value, None)
        worker.op_load_more(7, {'rounds': 5, 'seconds': 10}, 15)
        step({'nodes': 10})        # begun: the first round scrolls
        step({'scrolled': True})   # scrolled: the page is probed
        worker.op_load_more_stop(8, {'target': 7}, 1)
        worker.op_load_more_stop(9, {'target': 99}, 1)   # no such request: answered, nothing marked
        self.assertEqual(worker.stopping, {7})
        step({'added': 3, 'text': 'x', 'nodes': 13})     # the next probe sees the Stop
        step({'text': 'row 11', 'added': 3, 'kept': 3})  # what it had loaded is collected
        answer = emitted[-1]
        self.assertEqual((answer['id'], answer['ok'], answer['stopped'], answer['rounds']), (7, True, 'stopped', 1))
        self.assertEqual([message['id'] for message in emitted], [8, 9, 7])
        self.assertEqual((worker.stopping, worker.reading, worker.ran), (set(), False, []), 'no further round is scheduled')

    def test_release_only_the_held_submit_it_reported_once(self):
        bw, worker = self.worker()
        worker.take_cancelled({'id': 'h1', 'dom': 0, 'method': 'post', 'action': 'https://shop.test/pay',
                               'page': 'https://shop.test/c', 'state': 's'})
        reported = {key: worker.held[key] for key in ('dom', 'method', 'action', 'page', 'state')}
        worker.op_release_submit(1, {'form': dict(reported, state='0' * 64)}, 5)
        self.assertEqual((worker.failed, worker.ran), ([(1, 'submit_changed')], []), 'another state is not released')
        worker.deadline = lambda *args, **kwargs: None
        worker.blocked, worker.main_navigations, worker.landed = 0, 0, 0
        worker.op_release_submit(2, {'form': reported}, 5)
        self.assertEqual(worker.ran[-1][:2], (bw.RELEASE_SCRIPT, {'id': 'h1'}))
        self.assertIsNone(worker.held)
        worker.op_release_submit(3, {'form': reported}, 5)
        self.assertEqual(worker.failed[-1], (3, 'submit_changed'), 'released at most once')

    def test_a_submit_cancelled_after_a_timed_out_step_keeps_its_hold_id(self):
        # #700 review P2-5: re-queued under the page's own hold id, so its release still matches.
        bw, worker = self.worker()
        worker.run = lambda body, arguments, done, world=None: done(
            {'cancelled': {'id': 'page-hold', 'dom': 0, 'method': 'post', 'action': 'https://shop.test/pay',
                           'page': 'https://shop.test/c', 'state': 's'}}, None)
        worker.finish_input(9)   # the step already answered (timeout): not pending
        self.assertEqual(worker.cancelled['id'], 'page-hold')
        self.assertEqual(worker.take_cancelled()['action'], 'https://shop.test/pay')
        self.assertEqual(worker.held['id'], 'page-hold')

    def test_a_form_post_refused_with_nothing_held_is_a_typed_refusal(self):
        # #700 re-review P2-1: never an approval request the owner could not have released.
        bw, worker = self.worker()
        worker.refused_submits, worker.step_refused, worker.blocked = 1, 0, 0
        worker.pending[5] = True
        worker.reply = lambda ident, ok=True, **fields: worker.failed.append((ident, fields.get('error')))
        worker.run = lambda body, arguments, done, world=None: done({'cancelled': None}, None)
        worker.finish_input(5, 0)
        self.assertEqual(worker.failed, [(5, 'submit_refused')])
        self.assertIsNone(worker.cancelled)

    def test_a_step_answers_whether_the_page_started_a_non_get_submit(self):
        """#899: counted per step, from the step's own baseline."""
        for posted, expected in ((0, False), (1, True)):
            with self.subTest(posted=posted):
                bw, worker = self.worker()
                worker.posted_submits, worker.step_posted, worker.blocked = 3 + posted, 3, 0
                worker.pending[5] = True
                replies = []
                worker.reply = lambda ident, ok=True, **fields: replies.append(fields)
                worker.run = lambda body, arguments, done, world=None: done({'cancelled': None}, None)
                worker.finish_input(5, 0)
                self.assertEqual(replies, [{'posted': expected}])

    def test_a_decision_still_pending_when_a_step_answers_counts_as_posted(self):
        """#899 review P1: unknown is not "nothing posted"."""
        bw, worker = self.worker()
        worker.posted_submits, worker.step_posted, worker.blocked, worker.deciding = 3, 3, 0, 1
        worker.pending[5] = True
        replies = []
        worker.reply = lambda ident, ok=True, **fields: replies.append(fields)
        worker.run = lambda body, arguments, done, world=None: done({'cancelled': None}, None)
        worker.finish_input(5, 0)
        self.assertEqual(replies, [{'posted': True}])

    def test_a_step_that_times_out_answers_whether_it_posted(self):
        """#1496: the timeout answer of a click carries the same ``posted`` a finished step answers."""
        import types as types_module
        for posted, deciding, expected in ((0, 0, False), (1, 0, True), (0, 1, True)):
            with self.subTest(posted=posted, deciding=deciding):
                bw, worker = self.worker()
                worker.posted_submits, worker.step_posted, worker.deciding = 3 + posted, 3, deciding
                answers = []
                worker.fail = lambda ident, code, **fields: answers.append((ident, code, fields))
                worker.AppHelper = types_module.SimpleNamespace(callLater=lambda seconds, function: function())
                ended = []
                worker.deadline(4, 5, on_timeout=lambda: ended.append(True), observed=worker.step_posted_field)
                self.assertEqual(answers, [(4, 'timeout', {'posted': expected})])
                self.assertEqual(ended, [True])
        # Another op's deadline answers a bare timeout, as before.
        bw, worker = self.worker()
        answers = []
        worker.fail = lambda ident, code, **fields: answers.append((ident, code, fields))
        worker.AppHelper = types_module.SimpleNamespace(callLater=lambda seconds, function: function())
        worker.deadline(5, 5)
        self.assertEqual(answers, [(5, 'timeout', {})])

    def test_typing_settles_only_once_no_decision_is_pending(self):
        """#899 review P1: ``settle`` waits for a pending policy decision as a click does."""
        bw, worker = self.worker()
        worker.pending[7] = True
        calls = []

        class View:
            def isLoading(self):
                return False
        worker.view = View()
        worker.AppHelper = type('Helper', (), {'callLater': staticmethod(lambda delay, fn: calls.append(fn))})
        finished = []
        worker.deciding = 1
        worker.settle(7, lambda: finished.append(True))
        clock = [0.0]
        import unittest.mock as um
        with um.patch.object(bw.time, 'monotonic', lambda: clock[0]):
            for _ in range(5):
                clock[0] += 1.0
                calls.pop(0)()
            self.assertEqual(finished, [], 'still deciding: not settled')
            worker.deciding = 0
            for _ in range(3):
                clock[0] += 1.0
                if calls:
                    calls.pop(0)()
        self.assertEqual(finished, [True])

    def test_a_page_set_press_handler_property_is_signalled_to_the_client_world(self):
        """#1181: the page-world wrapper marks ``el.onclick = f`` (invisible from the client world)."""
        import re
        import shutil
        from personal_agent import browser_worker as bw
        listed = re.search(r"const PRESS_HANDLERS = \[(.*?)\];", bw.PRELUDE, re.S).group(1)
        self.assertEqual(tuple(key.strip().strip("'") for key in listed.replace('\n', '').split(',')),
                         bw.PRESS_HANDLER_KEYS, 'the wrapper covers exactly the handlers scripted() checks')
        self.assertIn(json.dumps(bw.PRESS_MARK), bw.SNAPSHOT_SCRIPT, 'scripted() reads the mark')
        node = shutil.which('node')
        if not node:
            self.skipTest('node is not installed')
        # A minimal DOM: handler properties are accessors on HTMLElement.prototype, as in WebKit.
        harness = """
class Element extends EventTarget { setAttribute(name, value) { (this.a = this.a || {})[name] = value; } }
class HTMLElement extends Element {}
class SVGElement extends Element {}
class HTMLFormElement extends HTMLElement { submit() {} }
for (const key of %s) Object.defineProperty(HTMLElement.prototype, key, {configurable: true, enumerable: true,
  get() { return (this.h || {})[key] || null; }, set(v) { (this.h = this.h || {})[key] = v; }});
Object.assign(globalThis, {Element, HTMLElement, SVGElement, HTMLFormElement, CustomEvent: Event});
%s
const el = new HTMLElement(), other = new HTMLElement();   // never connected: the mark needs no tree
el.onclick = function () {};
other.onclick = null;
other.onmousedown = 'not a function';
console.log(JSON.stringify({el: el.a || {}, other: other.a || {}, kept: typeof el.onclick}));
""" % (json.dumps(list(bw.PRESS_HANDLER_KEYS)), bw.PAGE_WRAP_USER_SCRIPT)
        result = subprocess.run([node, '-e', harness], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {'el': {bw.PRESS_MARK: ''}, 'other': {}, 'kept': 'function'},
                         'only a function assignment marks the element, and the handler is still set')

    def test_a_late_snapshot_leaves_a_held_submit_for_the_next_report(self):
        """#11 review: a snapshot that finishes after its deadline answered takes nothing it cannot report."""
        bw, worker = self.worker(reported_refused=0)
        taken, answered = [], []
        worker.take_cancelled = lambda value: taken.append(value)
        worker._snapshot_page(7, lambda page, error: answered.append((page, error)))
        late = worker.ran[-1][2]
        late({'cancelled': {'dom': 0}, 'url': 'u'}, None)   # 7 is not pending: its deadline answered
        self.assertEqual((taken, answered), ([], []))
        worker.pending[8] = True
        worker._snapshot_page(8, lambda page, error: answered.append((page, error)))
        worker.ran[-1][2]({'cancelled': {'dom': 0}, 'url': 'u'}, None)
        self.assertEqual(taken, [{'dom': 0}])
        self.assertEqual(answered, [({'url': 'u'}, None)])

    def test_a_cover_found_after_the_press_reaches_the_answer(self):
        """#102 review: the post-press checks of a click and a typing step pass the cover on."""
        cover = {'text': '잠시만요', 'controls': [{'index': 2, 'name': '닫기'}]}
        for op in ('op_click', 'op_type'):
            for pressed in (False, True):   # #103: whether the press reached the page rides along
                bw, worker = self.worker(blocked=0)
                finished = []
                worker.deadline = lambda *args, **kwargs: None
                worker.press = lambda x, y: None
                worker.AppHelper = type('AppHelper', (), {'callLater': staticmethod(lambda delay, call: call())})
                worker._locate = lambda ident, command, then: then({'x': 1, 'y': 2, 'editable': True}, 'n', {}, [])
                worker.finish_input = lambda ident, *args, **kwargs: finished.append((args, kwargs))
                getattr(worker, op)(7, {'text': 'x'}, 5)
                worker.pending[7] = True
                worker.ran[-1][2]({'error': 'target_obscured', 'cover': cover, 'pressed': pressed}, None)
                self.assertEqual(finished, [((0, 'target_obscured'), {'cover': cover, 'pressed': pressed,
                                                                      **({'focused': None} if op == 'op_type' else {})})],
                                 op)

    def test_the_press_guard_confirms_only_a_click_on_the_target(self):
        """#103: the guard's record of one press, and what each record answers, without WebKit."""
        import shutil
        from personal_agent import browser_worker as bw
        node = shutil.which('node')
        if not node:
            self.skipTest('node is not installed')
        # A minimal page: the guard's listeners are called directly with trusted-looking events.
        harness = r"""
class Node {}
class Element extends Node {}
class HTMLElement extends Element { get innerText() { return ''; } }
class HTMLFormElement extends HTMLElement { get elements() { return []; } get action() { return ''; } get method() { return 'get'; } }
class Document { get forms() { return []; } }
const listeners = {}, posted = [];
Object.assign(globalThis, {Node, Element, HTMLElement, HTMLFormElement, Document, CSS: {escape: (x) => x},
  document: {querySelectorAll: () => [], elementFromPoint: () => null, getElementById: (id) => id === 'pay' ? payButton : null},
  window: {addEventListener: (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); },
           webkit: {messageHandlers: {%(handler)s: {postMessage: (m) => posted.push(m)}}}}});
%(prelude)s
// The target sits in <label for="pay">: a press on it forwards a click to the pay button.
const payButton = {}, forLabel = {getAttribute: (name) => name === 'for' ? 'pay' : null};
const target = {isConnected: false, contains: (n) => n === target || n === child, closest: () => forLabel};
const child = {}, layer = {contains: () => false};
const label = {control: target, contains: () => false};
const run = async (early, events, opts = {}) => {
  delete window.__agentos; for (const key of Object.keys(listeners)) delete listeners[key]; posted.length = 0;
  armPressGuard(early);
  const s = state();
  s.targets.set('n', {deref: () => target});
  s.guard = {nonce: 'n', x: 1, y: 2, until: Date.now() + (opts.expired ? -1 : 60000), first: null, down: false, hit: false,
    bad: false, moved: false, settling: false, released: false, wake: null};
  const cancelled = [];
  for (const [type, on, trusted] of events) {
    const event = {type, target: on, isTrusted: trusted !== false,
      preventDefault() { cancelled.push(type); }, stopImmediatePropagation() {}};
    for (const fn of listeners[type] || []) fn(event);
  }
  await pressSettled(s.guard, 30);
  return {outcome: pressOutcome(s.guard, target, s.early), cancelled, posted: posted.slice(-1)};
};
const press = (down, up, click) => [['pointerdown', down], ['mousedown', down], ['pointerup', up], ['mouseup', up]]
  .concat(click ? [['click', click]] : []);
(async () => {
  console.log(JSON.stringify({
    ordinary: await run(true, press(target, target, target)),
    forwarded: await run(true, press(target, target, target).concat([['click', payButton]])),
    forwardedDown: await run(true, [['pointerdown', payButton]].concat(press(target, target, target))),
    child: await run(true, press(child, child, child)),
    label: await run(true, press(label, label, label)),
    layerDuringPress: await run(true, press(target, layer, null)),
    releasedNoClick: await run(true, press(target, target, null)),
    blockedEarly: await run(true, press(layer, layer, layer)),
    blockedLate: await run(false, press(layer, layer, layer)),
    blockedAfterHover: await run(true, [['mouseover', target]].concat(press(layer, layer, layer))),
    neverReleased: await run(true, [['pointerdown', target], ['mousedown', target]]),
    untrusted: await run(true, [['pointerdown', layer, false], ['click', layer, false]]),
    expired: await run(true, press(layer, layer, layer), {expired: true}),
  }));
})();
""" % {'handler': json.dumps(bw.GUARD_HANDLER), 'prelude': bw.PRELUDE}
        result = subprocess.run([node, '-e', harness], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        seen = json.loads(result.stdout)
        confirmed = {'outcome': {'ok': True}, 'cancelled': [], 'posted': [{'press': 'n', 'hit': True}]}
        for name in ('ordinary', 'child', 'label', 'forwarded'):
            self.assertEqual(seen[name], confirmed, name)
        # Only the click a label forwards reaches its control; a press event aimed there is stray.
        self.assertEqual(seen['forwardedDown']['cancelled'], ['pointerdown'])
        # The page put a layer over the target during the press: the release on it is cancelled,
        # and the press, whose down handlers ran on the target, is not confirmed (effect unknown).
        self.assertEqual(seen['layerDuringPress'], {'outcome': {'error': 'target_missing', 'pressed': True},
                                                    'cancelled': ['pointerup', 'mouseup'],
                                                    'posted': [{'press': 'n', 'hit': False}]})
        self.assertEqual(seen['releasedNoClick']['outcome'], {'error': 'press_no_click', 'pressed': True})
        # Every event of a press that first landed off the target is cancelled: with the guard armed at
        # document start no page press handler ran (not pressed); armed late, one may have (unknown).
        everything = ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']
        self.assertEqual((seen['blockedEarly']['outcome'], seen['blockedEarly']['cancelled']),
                         ({'error': 'target_missing', 'pressed': False}, everything))
        self.assertEqual((seen['blockedLate']['outcome'], seen['blockedLate']['cancelled']),
                         ({'error': 'target_missing', 'pressed': True}, everything))
        # #120 review: a hover the press caused ran page handlers (here the one that put up the layer),
        # so even a fully cancelled press is of unknown effect.
        self.assertEqual((seen['blockedAfterHover']['outcome'], seen['blockedAfterHover']['cancelled']),
                         ({'error': 'target_missing', 'pressed': True}, everything))
        for name in ('neverReleased', 'untrusted', 'expired'):
            self.assertEqual(seen[name]['outcome'], {'error': 'press_unconfirmed', 'pressed': True}, name)
            self.assertEqual(seen[name]['cancelled'], [], name)
        self.assertIn('armPressGuard(true);', bw.client_guard_user_script(), 'armed at document start')
        self.assertIn('armPressGuard(true);', bw.client_guard_user_script(off=True), 'in the owner window too')

    def test_a_press_whose_page_went_away_is_confirmed_only_by_the_guards_report(self):
        """#103: VERIFY could not answer (the page navigated); the guard's message says whether a click landed."""
        for value, error, seen, confirmed in (
                (None, 'script_failed', {'nonce': 'n', 'hit': True}, True),
                ({'lost': True}, None, {'nonce': 'n', 'hit': True}, True),
                (None, 'script_failed', {'nonce': 'n', 'hit': False}, False),
                (None, 'script_failed', {'nonce': 'older', 'hit': True}, False),
                ({'lost': True}, None, None, False),
                ({'ok': True}, None, None, True)):
            bw, worker = self.worker(blocked=0)
            finished = []
            worker.deadline = lambda *args, **kwargs: None
            worker.press = lambda x, y: None
            worker._locate = lambda ident, command, then: then({'x': 1, 'y': 2}, 'n', {}, [])
            worker.settle_click = lambda ident, finish: finish(True)
            worker.finish_input = lambda ident, *args, **kwargs: finished.append((args, kwargs))
            worker.op_click(7, {}, 5)
            worker.pending[7] = True
            worker.press_seen = seen
            worker.ran[-1][2](value, error)
            expected = ((0, None), {'navigated': True, 'pressed': False}) if confirmed else \
                ((0, 'press_unconfirmed'), {'navigated': True, 'pressed': True})
            self.assertEqual(finished, [expected], (value, error, seen))

    def test_a_pressed_failure_says_so_with_what_the_step_observed(self):
        """#103: an error after the press reached the page carries ``pressed``, ``navigated`` and ``posted``."""
        bw, worker = self.worker(blocked=0)
        failed = []
        worker.fail = lambda ident, code, **fields: failed.append((ident, code, fields))
        worker.run = lambda body, arguments, done, world=None: done({'cancelled': None}, None)
        worker.posted_submits = 1
        for ident, kwargs in ((1, {'pressed': True, 'navigated': True}), (2, {'pressed': True}), (3, {})):
            worker.pending[ident] = True
            worker.finish_input(ident, 0, 'press_no_click', **kwargs)
        self.assertEqual(failed, [(1, 'press_no_click', {'pressed': True, 'navigated': True, 'posted': True}),
                                  (2, 'press_no_click', {'pressed': True, 'posted': True}),
                                  (3, 'press_no_click', {})])
        self.assertEqual(bw._press_record({'press': 'abc', 'hit': 1}), {'nonce': 'abc', 'hit': True})
        self.assertIsNone(bw._press_record({'id': 'x1', 'dom': 0}), 'a cancelled submit is not a press report')
        worker.receive_press({'press': 'abc', 'hit': False})
        self.assertEqual(worker.press_seen, {'nonce': 'abc', 'hit': False})

    def test_the_page_wrapper_is_not_installed_while_the_owner_signs_in(self):
        bw, worker = self.worker()
        added = []

        class Script:
            @classmethod
            def alloc(cls):
                return cls()

            def initWithSource_injectionTime_forMainFrameOnly_inContentWorld_(self, source, when, main, world):
                return (source, world, main)

        class Controller:
            def removeAllUserScripts(self):
                added.clear()

            def addUserScript_(self, script):
                added.append(script)
        worker.WebKit = type('WebKit', (), {'WKUserScriptInjectionTimeAtDocumentStart': 0, 'WKUserScript': Script})
        worker.controller, worker.world, worker.page_world = Controller(), 'client', 'page'
        worker._install_guard_scripts()
        self.assertEqual([(world, main) for _, world, main in added], [('client', True), ('page', True), ('page', False)])
        self.assertEqual([source for source, _, _ in added][1:], [bw.PAGE_WRAP_USER_SCRIPT, bw.PHONE_PLATFORM_USER_SCRIPT])
        worker.guard_off = True
        worker._install_guard_scripts()
        self.assertNotIn(bw.PAGE_WRAP_USER_SCRIPT, [source for source, _, _ in added], 'no detectable wrapper in the login window')
        self.assertEqual(added[-1], (bw.PHONE_PLATFORM_USER_SCRIPT, 'page', False),
                         'the phone platform holds in the login window and every frame (#13)')
        self.assertIn('state().off = true', added[0][0])

    def test_the_page_reports_an_iphone_platform_and_no_touch(self):
        """#13: ``navigator.platform`` agrees with the phone user agent; touch is not claimed; nothing else changes."""
        import shutil
        from personal_agent import browser_worker as bw
        node = shutil.which('node')
        if not node:
            self.skipTest('node is not installed')
        harness = r"""
class Navigator {}
Object.defineProperty(Navigator.prototype, 'platform', {get() { return 'MacIntel'; }, enumerable: true, configurable: true});
Object.defineProperty(Navigator.prototype, 'maxTouchPoints', {get() { return 0; }, enumerable: true, configurable: true});
globalThis.Navigator = Navigator;
const nav = new Navigator();
%s
const desc = Object.getOwnPropertyDescriptor(Navigator.prototype, 'platform');
console.log(JSON.stringify({platform: nav.platform, touch: nav.maxTouchPoints, own: Object.keys(nav).length,
                            enumerable: desc.enumerable, configurable: desc.configurable}));
""" % bw.PHONE_PLATFORM_USER_SCRIPT
        result = subprocess.run([node, '-e', harness], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {'platform': 'iPhone', 'touch': 0, 'own': 0,
                                                     'enumerable': True, 'configurable': True})

    def test_a_form_navigation_the_page_does_not_vouch_for_is_refused_and_reported(self):
        bw, worker = self.worker()
        later = []
        worker.AppHelper = type('AppHelper', (), {'callLater': staticmethod(lambda seconds, fn: later.append(fn))})
        answers = []
        worker.check_form_navigation('https://shop.test/pay', 'POST', answers.append)
        self.assertEqual(worker.ran[-1][:2], (bw.FORM_NAVIGATION_SCRIPT, {'url': 'https://shop.test/pay', 'method': 'POST', 'frame': False}))
        self.assertEqual(worker.deciding, 1, 'a click waits for this decision')
        worker.ran[-1][2]({'allow': False, 'cancelled': {'id': 'n1', 'dom': 0, 'method': 'post',
                                                         'action': 'https://shop.test/pay', 'page': 'p', 'state': 's'}}, None)
        self.assertEqual((answers, worker.deciding), ([False], 0))
        self.assertEqual(worker.take_cancelled()['action'], 'https://shop.test/pay')
        later[-1]()
        self.assertEqual(answers, [False], 'answered once')
        worker.check_form_navigation('https://shop.test/search', 'GET', answers.append)
        worker.ran[-1][2]({'allow': True}, None)
        self.assertEqual(answers[-1], True)
        # A script error, or no answer in time, is a refusal with nothing held: not an approval request.
        worker.refused_submits = 0
        worker.check_form_navigation('https://shop.test/pay', 'POST', answers.append)
        worker.ran[-1][2](None, 'script_failed')
        worker.check_form_navigation('https://shop.test/pay', 'POST', answers.append)
        later[-1]()
        self.assertEqual((answers[-2:], worker.deciding, worker.refused_submits), ([False, False], 0, 2))
        self.assertIsNone(worker.take_cancelled())


class DestinationTests(unittest.TestCase):
    def test_local_and_private_destinations_are_refused_by_name_literal_and_dns(self):
        from personal_agent import browser_worker as bw
        for url in ('http://127.0.0.1:8765/', 'http://localhost:8765/api/state', 'http://agentos.localhost:8765/',
                    'http://[::1]:8765/', 'http://10.0.0.2/', 'http://192.168.1.1/', 'http://169.254.169.254/latest',
                    'http://printer.local/', 'http://0.0.0.0/', 'file:///etc/hosts', 'ftp://shop.test/'):
            self.assertTrue(bw.local_url(url), url)
            self.assertEqual(bw.destination_refusal(url, resolver=lambda *a, **k: []), 'blocked_destination', url)
        public = lambda *a, **k: [(2, 1, 6, '', ('93.184.215.14', 443))]
        loop = lambda *a, **k: [(2, 1, 6, '', ('93.184.215.14', 443)), (2, 1, 6, '', ('127.0.0.1', 443))]
        self.assertIsNone(bw.destination_refusal('https://shop.example/', resolver=public))
        self.assertEqual(bw.destination_refusal('https://rebind.example/', resolver=loop), 'blocked_destination',
                         'a public name that resolves to loopback is refused')
        def fails(*a, **k):
            raise OSError('nxdomain')
        self.assertIsNone(bw.destination_refusal('https://missing.example/', resolver=fails))
        # The test-only allowance is one exact origin.
        self.assertIsNone(bw.destination_refusal('http://127.0.0.1:5000/x', ('127.0.0.1:5000',)))
        self.assertEqual(bw.destination_refusal('http://127.0.0.1:5001/x', ('127.0.0.1:5000',)), 'blocked_destination')

    def test_a_session_refuses_this_computer_before_any_driver_runs(self):
        from test_browser_session import FakeDriver
        driver = FakeDriver()
        sess = bs.BrowserSession(lambda: driver, work_id='w')
        for url in ('http://127.0.0.1:8765/', 'http://localhost:8765/', 'http://[::1]/', 'http://192.168.0.10/admin'):
            with self.assertRaises(ToolError) as caught:
                sess.open({'url': url, 'effect': 'read'})
            self.assertEqual(caught.exception.code, 'blocked_destination')
        self.assertEqual(driver.log, [])
        self.assertEqual(sess.steps_used, 0)
        allowed = bs.BrowserSession(lambda: driver, work_id='w', allowed_origins_for_tests=('127.0.0.1:9',))
        with self.assertRaises(ToolError) as caught:
            allowed.open({'url': 'http://127.0.0.1:10/', 'effect': 'read'})
        self.assertEqual(caught.exception.code, 'blocked_destination')
        profile = bs.BrowserProfile(Path(tempfile.mkdtemp()), launcher=lambda d, h: driver, jar=CookieJar(Path(tempfile.mkdtemp()) / JAR_NAME, MemoryKey()))
        with self.assertRaises(ValueError):
            profile.open_for_login('http://127.0.0.1:8765/')

    def test_worker_lines_are_ascii_and_emit_never_raises(self):
        import io
        from personal_agent.browser_worker import make_emit
        out = io.StringIO()
        emit = make_emit(out)
        emit({'id': 1, 'ok': True, 'page': {'title': 'a\ud800b', 'name': 'a' * 159 + '\U0001F600'}})
        emit({'id': 2, 'ok': True, 'bad': object()})
        lines = out.getvalue().splitlines()
        self.assertTrue(all(line.isascii() for line in lines))
        self.assertEqual(json.loads(lines[0])['page']['name'], 'a' * 159 + '\U0001F600')
        self.assertEqual(json.loads(lines[1]), {'id': 2, 'ok': False, 'error': 'encode_failed'})

        class Broken:
            def write(self, text):
                raise OSError('pipe closed')
            flush = write
        make_emit(Broken())({'id': 3, 'ok': True})   # does not raise

    def test_the_owner_ui_and_api_refuse_the_embedded_browser(self):
        from urllib.error import HTTPError
        from urllib.request import Request, urlopen
        from personal_agent.browser_worker import EMBEDDED_UA_TOKEN, safari_application_name
        from personal_agent.quickstart import make_handler
        from personal_agent.quickstart_service import AgentService
        self.assertIn(EMBEDDED_UA_TOKEN, safari_application_name())
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = QuickStore(tmp.name)
        service = AgentService(store, browser_profile=bs.BrowserProfile(Path(tmp.name) / 'p', launcher=lambda d, h: None))
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(service))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f'http://127.0.0.1:{server.server_port}'
        agent = 'Mozilla/5.0 (Macintosh) AppleWebKit/605.1.15 (KHTML, like Gecko) ' + safari_application_name()
        for path, body in (('/', None), ('/api/state', None), ('/api/browser/approval', b'{"work_id":"x","decision":"approve"}'),
                           ('/api/local-login', b'{}')):
            request = Request(base + path, data=body, headers={'User-Agent': agent, 'Content-Type': 'application/json',
                                                                 'Origin': base})
            with self.assertRaises(HTTPError) as caught:
                urlopen(request, timeout=3)
            self.assertEqual(caught.exception.code, 403, path)
        try:
            with urlopen(Request(base + '/healthz', headers={'User-Agent': 'Mozilla/5.0 Safari/605.1.15'}), timeout=3) as response:
                status = response.status
        except HTTPError as error:
            status = error.code
        self.assertNotEqual(status, 403, 'an ordinary browser is unaffected')


# ---------------------------------------------------------------- the real worker (macOS + PyObjC)

# Real WebKit windows and real Keychain items open on the owner's screen, so these
# tests run only when explicitly requested: AGENTOS_REAL_BROWSER_TESTS=1.
REAL_BROWSER_TESTS = __import__('os').environ.get('AGENTOS_REAL_BROWSER_TESTS') == '1'


def _webkit_ready():
    return REAL_BROWSER_TESTS and bs.webkit_unavailable_reason() is None and Path('/usr/bin/security').exists()


class SessionFixtureHandler(FixtureHandler):
    """The shared fixture site plus a session cookie and a trusted-input page."""

    def do_GET(self):
        path = urlsplit(self.path).path
        value = self.server.cookie_value
        if path == '/session-start':
            data = '<html><head><title>세션</title></head><body><h1>세션 시작</h1></body></html>'.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Set-Cookie', f'fixture_sid={value}; Path=/; HttpOnly; Max-Age=3600')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if path == '/device':
            # #1183: what the site sees: the User-Agent header and the layout viewport width.
            agent = (self.headers.get('User-Agent') or '').replace('<', '')
            # #13: and what the page's script sees, in the page and in a frame.
            return self._send('<html><head><title>기기</title><meta name="viewport" content="width=device-width">'
                              f'</head><body><p id="ua">{agent}</p><p id="w">폭 측정 중</p><p id="nav">대기</p>'
                              '<iframe src="/device-frame" style="height:40px"></iframe>'
                              '<script>document.getElementById("w").textContent = "폭 " + window.innerWidth;'
                              'document.getElementById("nav").textContent = "플랫폼 " + navigator.platform + " 터치 " +'
                              ' navigator.maxTouchPoints + " " + ("ontouchstart" in window) + " 같은UA " +'
                              f' (navigator.userAgent === {json.dumps(agent)});</script>'
                              '</body></html>')
        if path == '/desktop-only':
            # #254: a desktop-only page: laid out 1100 px wide whatever the viewport, with its
            # controls at the right edge (where a phone-sized window cuts it off).
            return self._send('<html><head><title>넓은 페이지</title><meta name="viewport" content="width=device-width">'
                              '</head><body style="margin:0"><div style="min-width:1100px;height:600px;position:relative">'
                              '<input id="name" aria-label="이름" style="position:absolute;left:960px;top:100px;width:120px;height:40px">'
                              '<button id="go" style="position:absolute;left:960px;top:200px;width:120px;height:60px"'
                              ' onclick="document.getElementById(\'r\').textContent = \'눌림 \' + document.getElementById(\'name\').value">'
                              '로그인</button><p id="r" style="position:absolute;left:960px;top:300px">대기</p></div>'
                              '<a href="/device">다음</a></body></html>')
        if path == '/off-canvas':
            # #254: a phone page whose off-screen panel is as wide as the viewport, so it always overflows.
            return self._send('<html><head><title>메뉴</title><meta name="viewport" content="width=device-width">'
                              '</head><body style="margin:0"><p>본문</p>'
                              '<nav style="position:absolute;top:0;left:100%;width:100vw;height:100px">메뉴</nav>'
                              '</body></html>')
        if path == '/device-worker':
            # #13 review: what a Web Worker sees (WebKit's separate WorkerNavigator).
            return self._send('<html><head><title>워커</title></head><body><p id="r">대기</p><script>'
                              'var w = new Worker(URL.createObjectURL(new Blob(["postMessage(navigator.platform + \\" \\" +'
                              ' /iPhone/.test(navigator.userAgent))"], {type: "text/javascript"})));'
                              'w.onmessage = function (e) { document.getElementById("r").textContent = "워커 " + e.data; };'
                              '</script></body></html>')
        if path == '/device-frame':
            return self._send('<html><body><p id="f">대기</p><script>document.getElementById("f").textContent ='
                              ' "frame-platform " + navigator.platform; parent.document.title = "기기 " +'
                              ' navigator.platform;</script></body></html>')
        if path == '/whoami':
            present = value in (self.headers.get('Cookie') or '')
            return self._send(f'<html><head><title>계정 상태</title></head><body><p>{"세션 있음" if present else "세션 없음"}</p></body></html>')
        if path == '/windows':
            return self._send('''<html><head><title>새 창</title></head><body>
              <a href="/cart" target="_blank">새 창 장바구니</a>
              <a href="file:///etc/hosts" target="_blank">로컬 파일</a></body></html>''')
        if path == '/memo-trap':
            # Review P1-1 repro: focusing the memo inserts a card field at the top of <body>.
            return self._send('''<html><head><title>메모</title></head><body>
              <p id="leak">없음</p>
              <label>메모 <input id="memo" type="text" onfocus="if(!window.armed){window.armed=1;var c=document.createElement('input');
                c.setAttribute('autocomplete','cc-number');c.setAttribute('aria-label','카드');
                c.oninput=function(){document.getElementById('leak').textContent='카드 입력됨:'+c.value;};document.body.prepend(c);}"></label>
              </body></html>''')
        if path == '/input-overlay':
            # #12: a consent layer covers the search field until it is dismissed.
            return self._send('''<html><head><title>가림</title></head><body>
              <input aria-label="검색어" oninput="document.getElementById('r').textContent='입력:'+this.value">
              <p id="r">대기</p>
              <div id="layer" style="position:fixed;top:0;left:0;right:0;bottom:0;background:#fff">
                <p>쿠키 사용에 동의하시겠습니까?</p>
                <button type="button" onclick="document.getElementById('layer').remove()">동의</button></div>
              </body></html>''')
        if path == '/input-rerender':
            # #12: focusing the search field replaces it with another field and focuses that one.
            return self._send('''<html><head><title>교체</title></head><body>
              <div id="box"><input aria-label="검색" onfocus="var n=document.createElement('input');
                n.setAttribute('aria-label','검색어 입력');
                n.oninput=function(){document.getElementById('r').textContent='입력:'+n.value;};
                document.getElementById('box').replaceChildren(n);n.focus();"></div>
              <p id="r">대기</p></body></html>''')
        if path == '/input-focus-editable':
            # #12: a text-entry widget that becomes editable only once it has focus.
            return self._send('''<html><head><title>편집</title></head><body>
              <div role="searchbox" aria-label="메모" tabindex="0" style="min-height:2em;border:1px solid"
                onfocus="this.contentEditable='true'"
                oninput="document.getElementById('r').textContent='입력:'+this.textContent"></div>
              <input aria-label="잠긴 입력" readonly onfocus="this.removeAttribute('readonly')"
                oninput="document.getElementById('r2').textContent='입력2:'+this.value">
              <p id="r">대기</p><p id="r2">대기</p></body></html>''')
        if path == '/lazy-list':
            # #11: rows arrive (after a short fetch) only once the list's end is scrolled into view; 30 at most.
            return self._send('''<html><head><title>목록</title></head><body>
              <button type="button" onclick="document.getElementById('pressed').textContent='더 보기 눌림'">더 보기</button>
              <p id="pressed">안 눌림</p><ul id="list"></ul><div id="end" style="height:10px"></div>
              <script>var n=0;function add(){for(var i=0;i<10&&n<30;i++){n++;var li=document.createElement('li');
                li.style.height='100px';li.textContent='행 '+n;document.getElementById('list').appendChild(li);}}
              add();new IntersectionObserver(function(es){if(es[0].isIntersecting&&n<30)setTimeout(add,300);})
                .observe(document.getElementById('end'));</script></body></html>''')
        if path == '/lazy-box':
            # #11: a list in its own scroll box that adds five rows whenever its end is reached, without end.
            return self._send('''<html><head><title>상자</title></head><body><h1>상자 목록</h1>
              <div id="box" style="height:300px;overflow:auto"></div>
              <script>var box=document.getElementById('box'),n=0;function add(){for(var i=0;i<5;i++){n++;
                var row=document.createElement('div');row.style.height='80px';row.textContent='항목 '+n;box.appendChild(row);}}
              add();box.addEventListener('scroll',function(){if(box.scrollTop+box.clientHeight>=box.scrollHeight-5)setTimeout(add,200);});
              </script></body></html>''')
        if path == '/lazy-endless':
            # #11: a page that never stops adding rows, so only the time bound ends a request.
            return self._send('''<html><head><title>끝없음</title></head><body><div id="feed"></div>
              <script>var n=0;setInterval(function(){n++;var row=document.createElement('p');row.textContent='소식 '+n;
                document.getElementById('feed').appendChild(row);},150);</script></body></html>''')
        if path == '/lazy-recycle':
            # #11 review: a list that reuses its ten rows, rewriting their text for each next page (3 pages).
            return self._send('''<html><head><title>재사용</title></head><body><ul id="list"></ul>
              <div id="end" style="height:10px"></div>
              <script>var list=document.getElementById('list'),page=0;
              for(var i=1;i<=10;i++){var li=document.createElement('li');li.style.height='100px';
                li.appendChild(document.createTextNode('줄 '+i));list.appendChild(li);}
              new IntersectionObserver(function(es){if(!es[0].isIntersecting||page>=2)return;page++;
                setTimeout(function(){Array.prototype.forEach.call(list.children,function(li,i){
                  li.firstChild.data='줄 '+(page*10+i+1);});window.scrollTo(0,0);},300);})
                .observe(document.getElementById('end'));</script></body></html>''')
        if path == '/lazy-leave':
            # #11 review: the page's scroll handler navigates away; loading more must not follow.
            return self._send('''<html><head><title>머무름</title></head><body>
              <div style="height:3000px">긴 내용</div>
              <script>window.addEventListener('scroll',function(){location.assign('/product');});</script>
              </body></html>''')
        if path == '/lazy-placeholder':
            # #11 review: reaching the end shows a loading placeholder that goes away with no rows.
            return self._send('''<html><head><title>빈 로딩</title></head><body><div style="height:2000px">처음 내용</div>
              <div id="end" style="height:10px"></div>
              <script>new IntersectionObserver(function(es){if(!es[0].isIntersecting)return;var p=document.createElement('p');
                p.textContent='불러오는 중';document.body.appendChild(p);setTimeout(function(){p.remove();},300);})
                .observe(document.getElementById('end'));</script></body></html>''')
        if path == '/lazy-long':
            # #11 review: the first load adds one row longer than the whole text budget, the next a short one.
            return self._send('''<html><head><title>긴 행</title></head><body><div style="height:2000px">처음</div>
              <div id="list"></div><div id="end" style="height:10px"></div>
              <script>var n=0;new IntersectionObserver(function(es){if(!es[0].isIntersecting||n>=2)return;n++;
                setTimeout(function(){var p=document.createElement('p');p.style.minHeight='1500px';
                  p.textContent=n===1?'가'.repeat(21000):'두 번째 행';document.getElementById('list').appendChild(p);},200);})
                .observe(document.getElementById('end'));</script></body></html>''')
        if path == '/wide':
            # Review P2-1 repro: a surrogate pair at the label cut and a lone surrogate in the title.
            return self._send('<html><head><title>넓은 글자</title></head><body><button type="button" aria-label="'
                              + '가' * 159 + '\U0001F600' + '">버튼</button><script>document.title = "x\\uD800y";</script></body></html>')
        if path == '/to-agentos':
            self.send_response(302)
            self.send_header('Location', self.server.agentos_origin + '/api/state')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if path == '/open-agentos':
            return self._send('<html><head><title>열기</title></head><body><button type="button" onclick="window.open(\''
                              + self.server.agentos_origin + '/\')">Still 열기</button>'
                              + '<form action="' + self.server.agentos_origin + '/api/browser/approval" method="post">'
                              + '<button type="submit">승인 보내기</button></form></body></html>')
        if path == '/nav-links':
            # #899: a list-page link whose label ends in a commit noun is a navigation; a
            # scripted, empty, role=button or script-assigned one is judged as a button.
            return self._send('''<html><head><title>링크</title></head><body>
              <a id="plain" href="/list/often">자주구매</a>
              <a id="attr" href="/list/often" onmousedown="void 0">선물구매</a>
              <a id="prop" href="/list/often">재구매</a>
              <a id="empty" href="">정기주문</a>
              <a id="role" href="/list/often" role="button">해외결제</a>
              <a id="noun" href="/list/often">구매</a>
              <script>document.getElementById('prop').onclick = function () {};
                // #1181 review: created, given a handler, then connected.
                const late = document.createElement('a'); late.href = '/list/often'; late.textContent = '추가구매';
                late.onclick = function () {}; document.body.append(late);</script>
            </body></html>''')
        if path == '/one-click-names':
            # Review of #763 P1/P2: names from aria-labelledby, a child image's alt, a role that
            # hides the button, and a neutral child inside a pay link; no card field anywhere.
            return self._send('''<html><head><title>이름</title></head><body>
              <form action="/order" method="post"><span id="lbl">Place your order</span>
                <input type="submit" name="placeOrder1" value="" aria-labelledby="lbl" style="width:80px;height:20px"></form>
              <a href="/order" onclick="return true"><img alt="Buy now" src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" width="40" height="20"></a>
              <form action="/order" method="post"><input type="submit" value="Pay now" role="none"></form>
              <a href="/order">Card <span role="button">Details</span> ₩12,900 결제하기</a>
              <a href="/reviews">Best laptops to buy in 2026, compared</a>
              <a href="#" onclick="return false">Purchase this song for $0.99</a>
              <div onclick="void 0">Our plans and a long description you can subscribe to later on.
                Every plan includes the same features, support hours and storage, and the differences are only in
                how many people can share one account and how long the history is kept for each of them.
                <span role="button">Read more</span></div>
              </body></html>''')
        if path == '/checkout-realm':
            # #700 item 2 repro: the native submit of a fresh iframe's prototype bypasses the page-world wrapper.
            native = ("var f=document.createElement('iframe');document.body.appendChild(f);"
                      "f.contentWindow.HTMLFormElement.prototype.submit.call(document.getElementById('{0}'))")
            return self._send('''<html><head><title>다른 창</title></head><body>
              <form id="payForm" action="/pay" method="post"><p>결제 금액 12,900원</p>
                <label>카드번호 <input type="text" autocomplete="cc-number" name="card"></label>
                <input type="hidden" name="amount" value="12900"></form>
              <form id="couponForm" action="/coupon" method="post"><input type="hidden" name="coupon" value="SAVE"></form>
              <form id="sinkForm" action="/pay" method="post" target="sink">
                <label>카드 <input type="text" autocomplete="cc-number" name="card"></label></form>
              <iframe name="sink" src="about:blank"></iframe>
              <div role="button" onclick="''' + native.format('payForm') + '''">다른 창 진행</div>
              <div role="button" onclick="''' + native.format('sinkForm') + '''">숨은 창 진행</div>
              <div role="button" onclick="''' + native.format('couponForm') + '''">다른 창 쿠폰</div>
              </body></html>''')
        if path == '/checkout-bound':
            # #700 review (Codex): an approval's allowance is held to the approved payload,
            # spent by one submit, and a held submit is recorded where its submitter sends it.
            return self._send('''<html><head><title>묶인 결제</title></head><body>
              <form id="payA" action="/pay" method="post"><p>결제 금액 12,900원</p>
                <label>카드번호 <input type="text" autocomplete="cc-number" name="card"></label>
                <input type="hidden" name="amount" id="amount" value="12900">
                <input type="hidden" name="token" id="token" value="">
                <button type="button" onclick="document.getElementById('amount').value='99999';payA.submit()">금액 바꿔 진행</button>
                <button type="button" onclick="document.getElementById('token').value='tok_1';payA.submit()">토큰 넣고 진행</button>
                <button type="submit" id="elsewhere" formaction="/pay-elsewhere" style="display:none">다른 곳</button></form>
              <form id="sinkForm" action="/pay" method="post" target="sink">
                <label>카드 <input type="text" autocomplete="cc-number" name="card"></label></form>
              <iframe name="sink" src="about:blank"></iframe>
              <div role="button" onclick="document.getElementById('elsewhere').click()">다른 곳 진행</div>
              <div role="button" onclick="sinkForm.requestSubmit();setTimeout(function(){sinkForm.requestSubmit()},3000)">두 번 진행</div>
              </body></html>''')
        if path in ('/press-mousedown', '/press-hover', '/press-scroll'):
            # #103: the page puts a layer over the button during the press (on its mousedown, on its
            # mouseover) or when the locate step scrolls it into view.  Counters say whose press
            # handlers ran; the button's click handler writes the result.
            when = {'/press-mousedown': "button.addEventListener('mousedown', cover)",
                    '/press-hover': "button.addEventListener('mouseover', cover)",
                    '/press-scroll': "window.addEventListener('scroll', cover)"}[path]
            gap = '1600px' if path == '/press-scroll' else '0'
            return self._send('''<html><head><title>가림</title></head><body>
              <p id="b">버튼 누름 0</p><p id="l">층 누름 0</p><p id="r">결과 대기</p>
              <button type="button" id="go" style="margin-top:''' + gap + '''">확인</button>
              <script>
                var button = document.getElementById('go'), pressed = 0, layered = 0;
                ['pointerdown', 'mousedown'].forEach(function (type) { button.addEventListener(type, function () {
                  pressed += 1; document.getElementById('b').textContent = '버튼 누름 ' + pressed; }); });
                button.addEventListener('click', function () { document.getElementById('r').textContent = '결과 눌림'; });
                function cover() {
                  if (document.getElementById('layer')) return;
                  var layer = document.createElement('div');
                  layer.id = 'layer';
                  layer.style.cssText = 'position:fixed;left:0;top:0;right:0;bottom:0;background:#fff;z-index:9';
                  layer.innerHTML = '<p>잠시만요</p><button type="button" onclick="this.parentNode.remove()">닫기</button>';
                  ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'].forEach(function (type) {
                    layer.addEventListener(type, function () { layered += 1;
                      document.getElementById('l').textContent = '층 누름 ' + layered; }); });
                  document.body.appendChild(layer);
                }
                ''' + when + ''';
              </script></body></html>''')
        if path == '/press-capture':
            # #103: the page's own capture listener on window, registered before any locate step,
            # swallows every click: the guard (registered at document start) still sees the click land.
            return self._send('''<html><head><title>가로채기</title><script>
              window.addEventListener('click', function (event) { event.stopImmediatePropagation();
                document.getElementById('r').textContent = '페이지가 클릭을 가로챔'; }, true);
              </script></head><body><button type="button">확인</button><p id="r">결과 대기</p></body></html>''')
        if path == '/trusted':
            return self._send('''<html><head><title>입력</title></head><body>
              <button type="button" onclick="document.getElementById('r').textContent='click trusted='+event.isTrusted">누르기</button>
              <input aria-label="메모" oninput="document.getElementById('r2').textContent='input trusted='+event.isTrusted">
              <p id="r">대기</p><p id="r2">대기</p></body></html>''')
        if path == '/confirm':
            # #174: the page's own confirmation questions; the result line shows how each was answered.
            return self._send('''<html><head><title>확인 질문</title></head><body>
              <button type="button" onclick="document.getElementById('r').textContent = confirm('정말 탈퇴하시겠습니까?') ? '탈퇴 확인됨' : '탈퇴 취소됨'">탈퇴</button>
              <button type="button" onclick="document.getElementById('r').textContent = confirm('12,900원을 결제하시겠습니까?') ? '결제 확인됨' : '결제 취소됨'">진행</button>
              <p id="r">대기</p></body></html>''')
        return super().do_GET()


@unittest.skipUnless(_webkit_ready(), 'embedded WebKit needs macOS with pyobjc-framework-WebKit')
class WebKitIntegrationTests(unittest.TestCase):
    """Evidence class: model-free integration with the real worker process and system WebKit."""

    def setUp(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), SessionFixtureHandler)
        self.server.posts = []
        self.server.cookie_value = 'agentos-fixture-' + uuid.uuid4().hex
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.origin = f'http://127.0.0.1:{self.server.server_address[1]}'
        # A stand-in for the owner's Still on another loopback port: it must never be reached.
        self.agentos = ThreadingHTTPServer(('127.0.0.1', 0), SessionFixtureHandler)
        self.agentos.posts, self.agentos.cookie_value, self.agentos.hits = [], 'unused', []
        self.agentos.agentos_origin = ''
        threading.Thread(target=self.agentos.serve_forever, daemon=True).start()
        self.addCleanup(self.agentos.server_close)
        self.addCleanup(self.agentos.shutdown)
        self.agentos_origin = f'http://127.0.0.1:{self.agentos.server_address[1]}'
        self.server.agentos_origin = self.agentos_origin
        self.fixture = f'127.0.0.1:{self.server.server_address[1]}'
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = QuickStore(Path(self.tmp.name) / 'data')
        profile_dir = self.store.private / 'browser-profile'
        # The real Keychain, under a test-only service name this test deletes.
        self.key = KeychainKey(store_account(profile_dir), service='personal-agentos.browser-jar.test-' + uuid.uuid4().hex[:12])
        self.addCleanup(self.key.delete)
        self.jar = CookieJar(profile_dir / JAR_NAME, self.key)
        self.profile = bs.BrowserProfile(profile_dir, jar=self.jar)
        # Test-only: the fixture origin (and only it) may be loaded from loopback.
        self.profile.allow_origins_for_tests(self.fixture)

    def session(self, work_id):
        return bs.BrowserSession(self.profile.driver_factory(work_id), work_id=work_id, approvals=Approvals(), steps=40,
                                 excluded=lambda: [PASSPORT], allowed_origins_for_tests=(self.fixture,))

    def test_a_page_confirm_is_answered_by_the_steps_own_declared_effect(self):
        """#174: after a step declared navigate the question is declined and reported; after mutate it is accepted."""
        sess = self.session('work-174-effect')
        try:
            sess.open({'url': self.origin + '/confirm', 'effect': 'navigate'})
            page = sess.click({'target': '탈퇴', 'effect': 'navigate'})
            self.assertIn('탈퇴 취소됨', page['text'])
            self.assertEqual(page['dialogs'], [{'kind': 'confirm', 'message': '정말 탈퇴하시겠습니까?',
                                                'answer': bs.DIALOG_OUTCOMES['declined_effect']}],
                             'the worker AI reads the question and why it was declined')
            page = sess.click({'target': '탈퇴', 'effect': 'mutate'})
            self.assertIn('탈퇴 확인됨', page['text'])
            self.assertEqual(page['dialogs'], [{'kind': 'confirm', 'message': '정말 탈퇴하시겠습니까?',
                                                'answer': bs.DIALOG_OUTCOMES['accepted']}])
        finally:
            sess.close()

    def test_a_payment_confirm_is_declined_unless_the_owner_approved_that_press(self):
        """#174: a payment question is declined after a mutate step; the owner's approval of the press confirms it."""
        sess = self.session('work-174-payment')
        try:
            sess.open({'url': self.origin + '/confirm', 'effect': 'navigate'})
            page = sess.click({'target': '진행', 'effect': 'mutate'})
            self.assertIn('결제 취소됨', page['text'])
            self.assertEqual([row['answer'] for row in page['dialogs']], [bs.DIALOG_OUTCOMES['declined_payment']])
            with self.assertRaises(ToolError) as caught:
                sess.click({'target': '진행', 'effect': 'payment'})
            self.assertEqual(caught.exception.code, 'approval_required')
            self.assertIn('결제 취소됨', sess.read()['text'], 'nothing was pressed while it waited for the owner')
            sess.approvals.issue(sess.approvals.requests[-1][0])
            page = sess.click({'target': '진행', 'effect': 'payment'})
            self.assertIn('결제 확인됨', page['text'])
            self.assertEqual([row['answer'] for row in page['dialogs']], [bs.DIALOG_OUTCOMES['accepted']])
        finally:
            sess.close()

    def test_typing_never_lands_in_a_field_the_page_inserts_on_focus(self):
        sess = self.session('work-trap')
        try:
            sess.open({'url': self.origin + '/memo-trap', 'effect': 'navigate'})
            try:
                page = sess.type({'target': '메모', 'text': '장보기 목록', 'effect': 'mutate'})
            except ToolError as refused:
                self.assertEqual(refused.code, 'target_unavailable')
                page = sess.read()
            self.assertNotIn('카드 입력됨', page['text'], 'nothing was typed into the inserted card field')
            self.assertIn('없음', page['text'])
            values = {row['name']: row.get('value') for row in page['elements']}
            # Either the text is in the memo, or the step was refused because the
            # page moved the pointer's target (the click guard): never elsewhere.
            self.assertIn(values.get('메모'), ('장보기 목록', None), page['elements'])
            if values.get('메모') is None:
                self.assertNotIn('장보기', flat(page))
        finally:
            sess.close()

    def test_a_covered_field_names_its_cover_and_takes_text_once_it_is_dismissed(self):
        """#12: the refusal says what covers the field and which control closes it."""
        sess = self.session('work-12-cover')
        try:
            page = sess.open({'url': self.origin + '/input-overlay', 'effect': 'navigate'})
            agree = next(row['n'] for row in page['elements'] if row['name'] == '동의')
            with self.assertRaises(ToolError) as caught:
                sess.type({'target': '검색어', 'text': '우유', 'effect': 'mutate'})
            self.assertEqual(caught.exception.code, 'target_unavailable')
            self.assertIn(bs.OBSCURED_TEXT, str(caught.exception))
            self.assertIn(f'그 안의 요소: {agree} 동의.', str(caught.exception))
            self.assertIn('대기', sess.read()['text'], 'nothing was typed while the field was covered')
            sess.click({'target': str(agree), 'effect': 'mutate'})
            self.assertIn('입력:우유', sess.type({'target': '검색어', 'text': '우유', 'effect': 'mutate'})['text'])
        finally:
            sess.close()

    def _press_covered(self, path):
        """#103: click the button of a page that covers it during the press; what the step says, and the page after."""
        sess = self.session('work-103' + path.replace('/', '-'))
        try:
            sess.open({'url': self.origin + path, 'effect': 'navigate'})
            with self.assertRaises(ToolError) as caught:
                sess.click({'target': '확인', 'effect': 'mutate'})
            text = sess.read()['text']
        finally:
            sess.close()
        exc = caught.exception
        # Never a confirmed press: the button's click handler never ran, and the guard cancelled every
        # press event that reached the layer, so none of the layer's own handlers ran either.
        self.assertIn('결과 대기', text, path)
        self.assertIn('층 누름 0', text, path)
        if exc.code == bs.PRESS_UNCONFIRMED_CODE:
            self.assertEqual(exc.effect, 'unknown', path)
            self.assertEqual(exc.observed_step, {'posted': False, 'approved': False}, path)
            self.assertIn('잠시만요', str(exc), 'the layer over the pressed point is named')
        else:
            # Not pressed: nothing of the press reached the button either.
            self.assertEqual(exc.code, 'target_unavailable', path)
            self.assertIsNone(getattr(exc, 'effect', None), path)
            self.assertIn('버튼 누름 0', text, path)
            self.assertIn('잠시만요', str(exc), 'the layer is named as the cover')
        return exc.code, text

    def test_a_layer_put_over_the_target_on_its_mousedown_is_a_press_of_unknown_effect(self):
        """#103 fixture 1: the button's mousedown ran, then the layer took the release; no click landed."""
        code, text = self._press_covered('/press-mousedown')
        self.assertEqual(code, bs.PRESS_UNCONFIRMED_CODE)
        self.assertNotIn('버튼 누름 0', text, 'the press reached the button: that is why its effect is unknown')

    def test_a_layer_put_over_the_target_on_hover_is_never_a_confirmed_press(self):
        """#103 fixture 2: the layer appears on the button's mouseover, which the press itself causes."""
        self._press_covered('/press-hover')

    def test_a_layer_put_up_by_the_locate_scroll_is_a_cover_found_before_pressing(self):
        """#103 fixture 3: the page covers the target when the locate step scrolls it into view."""
        code, _text = self._press_covered('/press-scroll')
        self.assertEqual(code, 'target_unavailable', 'the scroll settles before the hit test: nothing is pressed')

    def test_an_ordinary_press_and_a_page_capture_listener_are_still_confirmed(self):
        """#103: a click on the target confirms the press, even when the page's own window listener swallows it."""
        sess = self.session('work-103-ordinary')
        try:
            sess.open({'url': self.origin + '/trusted', 'effect': 'navigate'})
            self.assertIn('click trusted=true', sess.click({'target': '누르기', 'effect': 'mutate'})['text'])
            sess.open({'url': self.origin + '/press-capture', 'effect': 'navigate'})
            self.assertIn('페이지가 클릭을 가로챔', sess.click({'target': '확인', 'effect': 'mutate'})['text'])
        finally:
            sess.close()

    def test_a_field_the_page_replaces_on_focus_is_named_and_never_typed_into(self):
        """#12: the press moved the focus to a new field; the step names it and types nowhere."""
        sess = self.session('work-12-replace')
        try:
            sess.open({'url': self.origin + '/input-rerender', 'effect': 'navigate'})
            with self.assertRaises(ToolError) as caught:
                sess.type({'target': '검색', 'text': '우유', 'effect': 'mutate'})
            self.assertEqual(caught.exception.code, 'target_unavailable')
            self.assertTrue(str(caught.exception).startswith(bs.FOCUS_MOVED_TEXT), str(caught.exception))
            self.assertIn('초점을 받은 요소: “검색어 입력”.', str(caught.exception))
            page = sess.read()
            self.assertIn('대기', page['text'], 'the unchecked field that took the focus received nothing')
            self.assertNotIn('우유', flat(page))
            field = next(row['n'] for row in page['elements'] if row['name'] == '검색어 입력')
            self.assertIn('입력:우유', sess.type({'target': str(field), 'text': '우유', 'effect': 'mutate'})['text'])
        finally:
            sess.close()

    def test_a_widget_that_becomes_editable_on_focus_is_listed_and_takes_text(self):
        """#12: a search widget editable only once focused, and a field read-only until focused."""
        sess = self.session('work-12-editable')
        try:
            page = sess.open({'url': self.origin + '/input-focus-editable', 'effect': 'navigate'})
            roles = {row['name']: row['role'] for row in page['elements']}
            self.assertEqual(roles.get('메모'), 'searchbox', page['elements'])
            self.assertIn('입력:우유', sess.type({'target': '메모', 'text': '우유', 'effect': 'mutate'})['text'])
            self.assertIn('입력2:두부', sess.type({'target': '잠긴 입력', 'text': '두부', 'effect': 'mutate'})['text'])
        finally:
            sess.close()

    def test_a_lazy_list_loads_its_later_rows_and_stops_when_nothing_more_appears(self):
        """#11: rows added only when scrolled reach the read; nothing is pressed."""
        sess = self.session('work-11-list')
        try:
            page = sess.open({'url': self.origin + '/lazy-list', 'effect': 'read'})
            self.assertIn('행 10', page['text'])
            self.assertNotIn('행 11', page['text'])
            page = sess.read({'load_more': '5'})
            loaded = page['loaded']
            self.assertEqual((loaded['stopped'], loaded['new_content']), ('no_progress', True), loaded)
            self.assertEqual(loaded['rounds'], 3, 'two rounds add rows, the third finds none')
            self.assertIn('행 11', loaded['text'])
            self.assertIn('행 30', loaded['text'])
            self.assertNotIn('행 10\n', loaded['text'] + '\n', 'only what appeared is reported')
            self.assertIn('행 30', page['text'], 'the page state the read returns shows the loaded rows too')
            self.assertIn('안 눌림', page['text'], 'loading more never presses a control')
        finally:
            sess.close()

    def test_a_list_in_its_own_scroll_box_loads_within_the_round_bound(self):
        sess = self.session('work-11-box')
        try:
            page = sess.open({'url': self.origin + '/lazy-box', 'effect': 'read'})
            self.assertNotIn('항목 6', page['text'])
            loaded = sess.read({'load_more': '2'})['loaded']
            self.assertEqual((loaded['rounds'], loaded['stopped'], loaded['new_content']), (2, 'bound', True), loaded)
            self.assertIn('항목 6', loaded['text'])
            self.assertIn('항목 15', loaded['text'])
            self.assertNotIn('항목 16', loaded['text'], 'no more than the requested rounds ran')
        finally:
            sess.close()

    def test_a_page_that_loads_nothing_more_ends_after_one_round(self):
        sess = self.session('work-11-static')
        try:
            sess.open({'url': self.origin + '/product', 'effect': 'read'})
            started = time.monotonic()
            loaded = sess.read({'load_more': '5'})['loaded']
            self.assertEqual(loaded, {'rounds': 1, 'stopped': 'no_progress', 'new_content': False, 'text': ''})
            self.assertLess(time.monotonic() - started, 10)
        finally:
            sess.close()

    def test_a_page_that_never_settles_ends_at_the_time_bound(self):
        sess = self.session('work-11-endless')
        try:
            sess.open({'url': self.origin + '/lazy-endless', 'effect': 'read'})
            started = time.monotonic()
            loaded = sess.read({'load_more': '5'})['loaded']
            self.assertEqual((loaded['stopped'], loaded['new_content']), ('time', True), loaded)
            self.assertLess(time.monotonic() - started, bs.LOAD_MORE_SECONDS + 10)
        finally:
            sess.close()

    def test_the_owners_stop_ends_a_load_more_within_one_round(self):
        """#244: a Stop during a load-more on a page that never settles ends the read promptly, as stopped."""
        stopped_at = []
        budget = WorkBudget(seconds=120, stop=lambda: bool(stopped_at) and time.monotonic() >= stopped_at[0])
        sess = bs.BrowserSession(self.profile.driver_factory('work-244'), work_id='work-244', approvals=Approvals(),
                                 steps=40, excluded=lambda: [PASSPORT], allowed_origins_for_tests=(self.fixture,),
                                 budget=budget)
        try:
            sess.open({'url': self.origin + '/lazy-endless', 'effect': 'read'})
            stopped_at.append(time.monotonic() + 1.5)
            with self.assertRaises(ToolError) as caught:
                sess.read({'load_more': '5'})
            self.assertEqual(caught.exception.code, 'stopped')
            self.assertLess(time.monotonic() - stopped_at[0], 4.0, 'within one round, not at the 15 s bound')
        finally:
            sess.close()

    def test_a_list_that_reuses_its_rows_reports_the_rewritten_rows(self):
        """#11 review: rows whose text is rewritten in place count as new content."""
        sess = self.session('work-11-recycle')
        try:
            page = sess.open({'url': self.origin + '/lazy-recycle', 'effect': 'read'})
            self.assertNotIn('줄 11', page['text'])
            loaded = sess.read({'load_more': '5'})['loaded']
            self.assertTrue(loaded['new_content'], loaded)
            self.assertIn('줄 11', loaded['text'])
            self.assertIn('줄 30', loaded['text'])
            self.assertEqual(loaded['stopped'], 'no_progress', 'the third page is the last')
        finally:
            sess.close()

    def test_loading_more_never_follows_a_navigation_the_page_starts(self):
        """#11 review: a scroll handler's navigation is cancelled; the read stays on its page."""
        sess = self.session('work-11-leave')
        try:
            sess.open({'url': self.origin + '/lazy-leave', 'effect': 'read'})
            page = sess.read({'load_more': '2'})
            self.assertEqual(page['title'], '머무름', page['url'])
            self.assertTrue(page['loaded']['navigation_refused'], page['loaded'])
            self.assertEqual(sess.read()['title'], '머무름', 'still on the page after the read')
        finally:
            sess.close()

    def test_a_placeholder_that_comes_and_goes_is_not_progress(self):
        """#11 review: an empty load (a placeholder that is removed) stops with no_progress and no content."""
        sess = self.session('work-11-placeholder')
        try:
            sess.open({'url': self.origin + '/lazy-placeholder', 'effect': 'read'})
            loaded = sess.read({'load_more': '5'})['loaded']
            self.assertEqual((loaded['rounds'], loaded['stopped'], loaded['new_content'], loaded['text']),
                             (1, 'no_progress', False, ''), loaded)
        finally:
            sess.close()

    def test_rows_after_the_text_budget_still_count_as_progress(self):
        """#11 review: once the returned text is full, later rows still keep the request going."""
        sess = self.session('work-11-long')
        try:
            sess.open({'url': self.origin + '/lazy-long', 'effect': 'read'})
            loaded = sess.read({'load_more': '5'})['loaded']
            self.assertEqual((loaded['rounds'], loaded['stopped'], loaded['new_content']), (3, 'no_progress', True), loaded)
            self.assertTrue(loaded['text_truncated'])
        finally:
            sess.close()

    def test_every_page_is_loaded_as_its_mobile_version(self):
        """#1183: the site sees a phone browser (header) and a phone-width viewport (layout)."""
        sess = self.session('work-1183')
        try:
            page = sess.open({'url': self.origin + '/device', 'effect': 'read'})
            self.assertIn('iPhone', page['text'])
            self.assertIn('Mobile/', page['text'])
            # #13: the OS token is one a real iPhone with this Safari sends, and the page sees the same phone.
            from personal_agent import browser_worker as bw
            agent = page['text'].split('\n', 1)[0]
            version = agent.split('Version/', 1)[1].split()[0]
            self.assertIn(f'CPU iPhone OS {bw.ios_os_token(version)} like Mac OS X', agent)
            self.assertIn('플랫폼 iPhone 터치 0 false 같은UA true', page['text'])
            self.assertEqual(page['title'], '기기 iPhone', 'a frame sees the iPhone platform too')
            # Documented limit (#13 review): a Web Worker keeps the phone user agent but WebKit's
            # WorkerNavigator still reports the Mac platform; no public API injects into workers.
            sess.open({'url': self.origin + '/device-worker', 'effect': 'read'})
            deadline = time.monotonic() + 5
            while '워커 ' not in (text := sess.read()['text']) and time.monotonic() < deadline:
                time.sleep(0.3)
            self.assertIn('워커 MacIntel true', text)
            width = int(page['text'].split('폭 ', 1)[1].split()[0])
            self.assertLessEqual(width, 430, page['text'])
        finally:
            sess.close()

    def test_emoji_at_the_cut_and_a_lone_surrogate_do_not_break_the_worker(self):
        sess = self.session('work-wide')
        try:
            page = sess.open({'url': self.origin + '/wide', 'effect': 'read'})
            self.assertEqual(page['state'], 'page')
            self.assertIn('\ufffd', page['title'], 'the lone surrogate is replaced, not passed on')
            json.dumps(page, ensure_ascii=False).encode('utf-8')
            names = [row['name'] for row in page['elements']]
            self.assertTrue(any(name.startswith('가' * bs.NAME_LIMIT) for name in names), names)
            again = sess.open({'url': self.origin + '/product', 'effect': 'read'})
            self.assertIn('세탁세제 3L', again['text'], 'the same worker keeps working')
        finally:
            sess.close()

    def test_the_production_policy_refuses_the_agentos_port_by_redirect_and_window_open(self):
        agentos_port = self.agentos.server_address[1]
        plain = bs.BrowserSession(lambda: None, work_id='w')   # no test allowance at all
        with self.assertRaises(ToolError) as caught:
            plain.open({'url': f'http://127.0.0.1:{agentos_port}/', 'effect': 'read'})
        self.assertEqual(caught.exception.code, 'blocked_destination')
        sess = self.session('work-policy')
        try:
            with self.assertRaises(ToolError) as caught:
                sess.open({'url': self.origin + '/to-agentos', 'effect': 'read'})
            self.assertEqual(caught.exception.code, 'blocked_destination', 'a redirect to the Still port is refused')
            sess.open({'url': self.origin + '/open-agentos', 'effect': 'read'})
            with self.assertRaises(ToolError) as caught:
                sess.click({'target': 'Still 열기', 'effect': 'navigate'})
            self.assertEqual(caught.exception.code, 'blocked_destination', 'window.open to the Still port is refused')
            sess.read()
            with self.assertRaises(ToolError) as caught:
                sess.click({'target': '승인 보내기', 'effect': 'navigate'})
            self.assertEqual(caught.exception.code, 'blocked_destination', 'a form posting to it is refused')
            page = sess.read()
            self.assertEqual(page['title'], '열기', 'the page stayed on the fixture site')
        finally:
            sess.close()
        self.assertEqual(self.agentos.posts, [], 'the stand-in Still received no request')
        # Only the exact test origin was allowed; the worker in production has no allowance at all.
        worker = bs.WebKitWorkerDriver('p', cwd=self.tmp.name)
        try:
            with self.assertRaises(ToolError) as caught:
                worker.goto(self.origin + '/product', 10)
            self.assertEqual(caught.exception.code, 'blocked_destination')
        finally:
            worker.close()

    def test_product_to_cart_guard_login_and_trusted_input_through_the_real_worker(self):
        sess = self.session('work-int')
        try:
            page = sess.open({'url': self.origin + '/product', 'effect': 'navigate'})
            self.assertEqual(page['state'], 'page')
            self.assertIn('세탁세제 3L', page['text'])
            self.assertIn(bs.REDACTED, page['text'], 'the saved value is redacted by the real snapshot too')
            self.assertNotIn('숨김 링크', [row['name'] for row in page['elements']])
            self.assertTrue(sess.find({'text': '12,900'})['found'])
            cart = sess.click({'target': '장바구니', 'effect': 'mutate'})
            self.assertEqual(cart['title'], '장바구니')
            self.assertIn('세탁세제 3L × 1', cart['text'])
            self.assertEqual(self.server.posts, ['/cart'])
            account = sess.open({'url': self.origin + '/account', 'effect': 'read'})
            for secret in (PASSWORD, OTP, TOKEN):
                self.assertNotIn(secret, flat(account))
            sess.open({'url': self.origin + '/checkout', 'effect': 'navigate'})
            with self.assertRaises(ToolError) as caught:
                sess.type({'target': '카드번호', 'text': '4111', 'effect': 'navigate'})
            self.assertEqual(caught.exception.code, 'approval_required')
            with self.assertRaises(ToolError) as caught:
                sess.click({'target': '결제하기', 'effect': 'mutate'})
            self.assertEqual(caught.exception.code, 'approval_required')
            self.assertEqual(self.server.posts, ['/cart'], 'no payment form was submitted')
            compound = sess.open({'url': self.origin + '/checkout-compound', 'effect': 'navigate'})
            for secret in ('4242424242424242', '987', '246810'):
                self.assertNotIn(secret, flat(compound))
            reset = sess.open({'url': self.origin + '/reset/' + PASSPORT, 'effect': 'read'})
            for secret in (PASSPORT, 'hunter2', 'abcDEF123456secret'):
                self.assertNotIn(secret, flat(reset))
            login = sess.open({'url': self.origin + '/login', 'effect': 'navigate'})
            self.assertEqual(login['state'], 'login_required')
            sess.open({'url': self.origin + '/trusted', 'effect': 'navigate'})
            sess.click({'target': '누르기', 'effect': 'navigate'})
            typed = sess.type({'target': '메모', 'text': '안녕', 'effect': 'mutate'})
            self.assertIn('click trusted=true', typed['text'])
            self.assertIn('input trusted=true', typed['text'])
            self.assertIn('안녕', [row.get('value') for row in typed['elements']])
            # A new-window link opens in the same view; a page can never make it load a local file.
            sess.open({'url': self.origin + '/windows', 'effect': 'navigate'})
            local = sess.click({'target': '로컬 파일', 'effect': 'navigate'})
            self.assertEqual(local['title'], '새 창')
            self.assertTrue(local['url'].startswith(self.origin))
            opened = sess.click({'target': '새 창 장바구니', 'effect': 'navigate'})
            self.assertEqual(opened['title'], '장바구니')
        finally:
            sess.close()
        self.assertFalse(self.profile.status()['in_use'])

    def test_label_and_script_forwarded_payment_submits_need_an_approval(self):
        """#698: repro of the #687 re-review gap on the real worker, and the approved and ordinary paths."""
        approvals = Approvals()
        sess = bs.BrowserSession(self.profile.driver_factory('work-698'), work_id='work-698', approvals=approvals, steps=40,
                                 allowed_origins_for_tests=(self.fixture,))
        page_url = self.origin + '/checkout-forwarded'

        def number(target):
            # The pay button is named by the same <label for=paybtn>: the span is addressed by number.
            rows = [row for row in sess.last['_elements'] if row['name'] == target]
            return str(next(row['n'] for row in rows if row['tag'] != 'button')) if len(rows) > 1 else target

        def refused(target):
            with self.assertRaises(ToolError) as caught:
                sess.click({'target': number(target), 'effect': 'mutate'})
            self.assertEqual(caught.exception.code, 'approval_required', target)
            return approvals.requests[-1][0]
        try:
            sess.open({'url': page_url, 'effect': 'navigate'})
            span = next(row for row in sess.last['_elements'] if row['tag'] == 'span')
            self.assertTrue(span['submit_guarded'], 'the real snapshot reports the label control form')
            # (a) the span inside <label for=paybtn>, (b) payForm.submit() and requestSubmit() from a div.
            for target in ('빠른 구매', '바로 진행', '요청 진행'):
                refused(target)
                self.assertEqual(self.server.posts, [], target)
                self.assertEqual(sess.read()['title'], '빠른 결제', 'the page stayed')
            # An ordinary form still submits, by its button and by a script.
            self.assertEqual(sess.click({'target': '쿠폰 바로 적용', 'effect': 'mutate'})['title'], '쿠폰')
            sess.open({'url': page_url, 'effect': 'navigate'})
            self.assertEqual(sess.click({'target': '쿠폰 적용', 'effect': 'mutate'})['title'], '쿠폰')
            self.assertEqual(self.server.posts, ['/coupon', '/coupon'])
            # The owner's approval of the refused step lets exactly that step submit, once.
            for target in ('바로 진행', '빠른 구매'):
                sess.open({'url': page_url, 'effect': 'navigate'})
                approvals.issued.append(bs.binding_digest(refused(target)))
                self.assertEqual(sess.click({'target': number(target), 'effect': 'mutate'})['title'], '결제 완료')
                self.assertEqual(approvals.issued, [])
            self.assertEqual(self.server.posts, ['/coupon', '/coupon', '/pay', '/pay'])
            # The approval was spent by its one step: the guard is whole again.
            sess.open({'url': page_url, 'effect': 'navigate'})
            refused('바로 진행')
        finally:
            sess.close()
        self.assertEqual(self.server.posts, ['/coupon', '/coupon', '/pay', '/pay'])
        # The worker's own guard, without the session's classification: a press on the span is cancelled.
        worker = bs.WebKitWorkerDriver('p', cwd=self.tmp.name, allowed_origins_for_tests=(self.fixture,))
        try:
            worker.goto(page_url, 10)
            page = worker.snapshot()
            span = next(row for row in page['elements'] if row['tag'] == 'span')
            self.assertIsNotNone(span['label_form'])
            with self.assertRaises(ToolError) as caught:
                worker.click(span['index'], 10)
            self.assertEqual(caught.exception.code, 'approval_required')
            self.assertEqual(self.server.posts, ['/coupon', '/coupon', '/pay', '/pay'], 'no POST')
        finally:
            worker.close()

    def test_deferred_payment_submits_stay_guarded_for_the_page_lifetime(self):
        """#698 review P1-1 (timer, fetch-then-submit) and P2-3 (a handler that changes the DOM first)."""
        approvals = Approvals()
        sess = bs.BrowserSession(self.profile.driver_factory('work-698b'), work_id='work-698b', approvals=approvals, steps=60,
                                 allowed_origins_for_tests=(self.fixture,))
        page_url = self.origin + '/checkout-forwarded'

        def refused_eventually(target):
            """Refused by the step itself or, for a submit after it answered, by the next snapshot."""
            sess.open({'url': page_url, 'effect': 'navigate'})
            asked = len(approvals.requests)
            try:
                sess.click({'target': target, 'effect': 'mutate'})
                time.sleep(2.5)
                sess.read()
            except ToolError as refusal:
                self.assertEqual(refusal.code, 'approval_required', target)
            else:
                self.fail(f'{target}: the payment submit was not refused')
            self.assertEqual(len(approvals.requests), asked + 1, 'asked once')
            binding, description = approvals.requests[-1]
            self.assertEqual(binding['action'], 'browser_submit', 'bound to the cancelled form and what it would send (#700)')
            self.assertIn(target, description, 'the owner reads the step that caused it')
            self.assertIn(bs.CANCELLED_NOTE, description)
            return binding
        try:
            for target in ('나중에 진행', '확인 후 진행'):
                refused_eventually(target)
                self.assertEqual(self.server.posts, [], target)
            # Ordinary forms still submit, by a timer after the step and by a script in it.
            sess.open({'url': page_url, 'effect': 'navigate'})
            sess.click({'target': '나중에 쿠폰', 'effect': 'mutate'})
            time.sleep(2.5)
            self.assertEqual(sess.read()['title'], '쿠폰')
            sess.open({'url': page_url, 'effect': 'navigate'})
            self.assertEqual(sess.click({'target': '쿠폰 바로 적용', 'effect': 'mutate'})['title'], '쿠폰')
            self.assertEqual(self.server.posts, ['/coupon', '/coupon'])
            # P2-3: the handler changed the form and its own text before submitting ...
            binding = refused_eventually('메모 후 진행')
            changed = sess.read()
            self.assertIn('처리 중', [row.get('value') for row in changed['elements']])
            # ... and the repeated step on the changed page (#700 item 4), not reopened, still
            # matches: the handler renamed its own button, the form it submits is the same.
            approvals.issued.append(bs.binding_digest(binding))
            self.assertEqual(sess.click({'target': '처리 중…', 'effect': 'mutate'})['title'], '결제 완료')
            self.assertEqual(approvals.issued, [])
            self.assertEqual(self.server.posts, ['/coupon', '/coupon', '/pay'], 'the approved step posted exactly once')
            # #700 P2-2: an approved async checkout (validate, then submit after the step) is
            # released when its deferred submit is cancelled again, not asked again.
            binding = refused_eventually('확인 후 진행')
            approvals.issued.append(bs.binding_digest(binding))
            asked = len(approvals.requests)
            sess.open({'url': page_url, 'effect': 'navigate'})
            sess.click({'target': '확인 후 진행', 'effect': 'mutate'})
            time.sleep(2.5)
            self.assertEqual(sess.read()['title'], '결제 완료')
            self.assertEqual((len(approvals.requests), approvals.issued), (asked, []))
            self.assertEqual(self.server.posts, ['/coupon', '/coupon', '/pay', '/pay'])
        finally:
            sess.close()
        self.assertEqual(self.server.posts, ['/coupon', '/coupon', '/pay', '/pay'])

    def test_an_allowance_is_held_to_its_payload_spent_once_and_a_held_submit_goes_where_it_was_going(self):
        """#700 review (Codex): payload-bound, one submit per approval, submitter overrides bound."""
        page_url = self.origin + '/checkout-bound'
        approvals = Approvals()
        sess = bs.BrowserSession(self.profile.driver_factory('work-700c'), work_id='work-700c', approvals=approvals, steps=40,
                                 allowed_origins_for_tests=(self.fixture,))
        try:
            # An approved press whose handler changes an amount that was already set: held and asked.
            sess.open({'url': page_url, 'effect': 'navigate'})
            with self.assertRaises(ToolError):
                sess.click({'target': '금액 바꿔 진행', 'effect': 'mutate'})
            approvals.issued.append(bs.binding_digest(approvals.requests[-1][0]))
            with self.assertRaises(ToolError) as caught:
                sess.click({'target': '금액 바꿔 진행', 'effect': 'mutate'})
            self.assertEqual(caught.exception.code, 'approval_required')
            self.assertEqual(approvals.requests[-1][0]['action'], 'browser_submit', 'the changed payload is asked for')
            self.assertEqual(self.server.posts, [])
            # An approved press whose handler only fills in an empty hidden token goes ahead.
            sess.open({'url': page_url, 'effect': 'navigate'})
            with self.assertRaises(ToolError):
                sess.click({'target': '토큰 넣고 진행', 'effect': 'mutate'})
            approvals.issued.append(bs.binding_digest(approvals.requests[-1][0]))
            self.assertEqual(sess.click({'target': '토큰 넣고 진행', 'effect': 'mutate'})['title'], '결제 완료')
            self.assertEqual(self.server.posts, ['/pay'])
        finally:
            sess.close()
        worker = bs.WebKitWorkerDriver('p', cwd=self.tmp.name, allowed_origins_for_tests=(self.fixture,))
        try:
            # A held submit is recorded, and released, where its submitter's formaction sends it.
            worker.goto(page_url, 10)
            index = next(row['index'] for row in worker.snapshot()['elements'] if row['name'] == '다른 곳 진행')
            with self.assertRaises(ToolError) as caught:
                worker.click(index, 10)
            self.assertTrue(caught.exception.cancelled_form['action'].endswith('/pay-elsewhere'))
            worker.release_submit(caught.exception.cancelled_form, 10)
            self.assertEqual(self.server.posts, ['/pay', '/pay-elsewhere'])
            # One approval, one submit: the page's second submit (into a frame, so the page
            # stays) within the window is cancelled and reported, not let through.
            worker.goto(page_url, 10)
            index = next(row['index'] for row in worker.snapshot()['elements'] if row['name'] == '두 번 진행')
            with self.assertRaises(ToolError) as caught:
                worker.click(index, 10)
            worker.release_submit(caught.exception.cancelled_form, 10)
            time.sleep(3.5)
            self.assertIsNotNone(worker.snapshot().get('cancelled_submit'), 'the second submit was cancelled')
            self.assertEqual(self.server.posts, ['/pay', '/pay-elsewhere', '/pay'])
        finally:
            worker.close()

    def test_one_click_commit_controls_need_approval_through_the_real_worker(self):
        """#758: no card field anywhere; the real snapshot names the label's control, the session refuses first."""
        approvals = Approvals()
        sess = bs.BrowserSession(self.profile.driver_factory('work-758'), work_id='work-758', approvals=approvals, steps=40,
                                 allowed_origins_for_tests=(self.fixture,))
        try:
            sess.open({'url': self.origin + '/one-click', 'effect': 'navigate'})
            rows = sess.last['_elements']
            # The <label for=buyBtn> names the button itself "빠른 진행" (accessible name):
            # its own text "Buy now" still marks it, and the label's span forwards to it.
            committing = [row for row in rows if row['commit']]
            self.assertEqual(sorted(row['tag'] for row in committing), ['a', 'button', 'div', 'span'])
            self.assertFalse(next(row for row in rows if row['name'] == '구매후기')['commit'])
            for row in committing:
                with self.assertRaises(ToolError) as caught:
                    sess.click({'target': str(row['n']), 'effect': 'mutate'})
                self.assertEqual(caught.exception.code, 'approval_required', row['name'])
            self.assertEqual(self.server.posts, [])
            self.assertEqual(sess.click({'target': '담기', 'effect': 'mutate'})['title'], '담음')
            sess.open({'url': self.origin + '/one-click', 'effect': 'navigate'})
            button = next(row for row in sess.last['_elements'] if row['tag'] == 'button' and row['commit'])
            approvals.issued.append(bs.binding_digest(approvals.requests[[row['tag'] for row in committing].index('button')][0]))
            self.assertEqual(sess.click({'target': str(button['n']), 'effect': 'mutate'})['title'], '주문 완료')
            self.assertEqual(self.server.posts, ['/add', '/order'])
        finally:
            sess.close()

    def test_a_list_page_link_navigates_and_a_scripted_one_is_judged_as_a_button(self):
        """#899 review P1/P2-3/P2-4/P2-5: the real worker's navLink, end to end."""
        sess = bs.BrowserSession(self.profile.driver_factory('work-899'), work_id='work-899', approvals=Approvals(),
                                 steps=20, allowed_origins_for_tests=(self.fixture,))
        try:
            sess.open({'url': self.origin + '/nav-links', 'effect': 'navigate'})
            flagged = {row['name']: row['commit'] for row in sess.last['_elements']}
            self.assertEqual(flagged.get('자주구매'), False, 'a plain http(s) list link opens a page')
            for name in ('선물구매', '재구매', '추가구매', '정기주문', '해외결제', '구매'):
                self.assertEqual(flagged.get(name), True, name)
        finally:
            sess.close()

    def test_accessible_names_images_hidden_roles_and_children_of_pay_controls_are_classified(self):
        """Review of #763: aria-labelledby, a descendant image's alt, role=none, a child of a pay link."""
        sess = bs.BrowserSession(self.profile.driver_factory('work-758b'), work_id='work-758b', approvals=Approvals(),
                                 steps=40, allowed_origins_for_tests=(self.fixture,))
        try:
            sess.open({'url': self.origin + '/one-click-names', 'effect': 'navigate'})
            rows = sess.last['_elements']
            self.assertIn('Place your order', [row['name'] for row in rows], 'aria-labelledby names the input')
            flagged = {row['name']: row['commit'] for row in rows}
            self.assertEqual(flagged.get('Place your order'), True)
            self.assertTrue(any(row['commit'] and row['tag'] == 'a' and 'Details' not in row['name'] for row in rows),
                            'the image-only pay link')
            self.assertEqual(flagged.get('Pay now'), True, 'role=none on a submit input')
            self.assertEqual(flagged.get('Details'), True, 'a child of a pay link')
            self.assertFalse(next(row for row in rows if row['name'].startswith('Best laptops'))['commit'])
            self.assertEqual(flagged.get('Purchase this song for $0.99'), True, 'a styled link that runs a script')
            self.assertEqual(flagged.get('Read more'), False, "a wrapper's long text is content")
            for row in [row for row in rows if row['commit']]:
                with self.assertRaises(ToolError) as caught:
                    sess.click({'target': str(row['n']), 'effect': 'mutate'})
                self.assertEqual(caught.exception.code, 'approval_required', row['name'])
            self.assertEqual(self.server.posts, [])
        finally:
            sess.close()

    def test_a_native_submit_from_another_realm_is_refused_as_a_navigation(self):
        """#700 item 2: a fresh iframe's unwrapped ``HTMLFormElement.prototype.submit`` on the payment form."""
        worker = bs.WebKitWorkerDriver('p', cwd=self.tmp.name, allowed_origins_for_tests=(self.fixture,))
        try:
            worker.goto(self.origin + '/checkout-realm', 10)
            index = next(row['index'] for row in worker.snapshot()['elements'] if row['name'] == '다른 창 진행')
            with self.assertRaises(ToolError) as caught:
                worker.click(index, 10)
            self.assertEqual(caught.exception.code, 'approval_required')
            record = caught.exception.cancelled_form
            self.assertEqual((record['dom'], record['method']), (0, 'post'))
            self.assertTrue(record['action'].endswith('/pay'))
            self.assertEqual(self.server.posts, [], 'no POST')
            # #700 review P2-2: the same, aimed at an iframe of the page.
            worker.goto(self.origin + '/checkout-realm', 10)
            index = next(row['index'] for row in worker.snapshot()['elements'] if row['name'] == '숨은 창 진행')
            with self.assertRaises(ToolError) as caught:
                worker.click(index, 10)
            self.assertEqual((caught.exception.code, caught.exception.cancelled_form['dom']), ('approval_required', 2))
            self.assertEqual(self.server.posts, [], 'no POST into the frame either')
            # An ordinary form submitted the same way still goes.
            worker.goto(self.origin + '/checkout-realm', 10)
            index = next(row['index'] for row in worker.snapshot()['elements'] if row['name'] == '다른 창 쿠폰')
            worker.click(index, 10)
            self.assertEqual(self.server.posts, ['/coupon'])
            # The owner's approval of that form in that state releases the held submit.
            worker.goto(self.origin + '/checkout-realm', 10)
            index = next(row['index'] for row in worker.snapshot()['elements'] if row['name'] == '다른 창 진행')
            with self.assertRaises(ToolError) as caught:
                worker.click(index, 10)
            self.assertEqual(worker.release_submit(caught.exception.cancelled_form, 10), {'navigated': True, 'posted': True})
            self.assertEqual(self.server.posts, ['/coupon', '/pay'])
        finally:
            worker.close()

    def test_the_owner_login_window_turns_the_guard_off_and_back_on(self):
        page_url = self.origin + '/checkout-forwarded'
        worker = bs.WebKitWorkerDriver('p', cwd=self.tmp.name, allowed_origins_for_tests=(self.fixture,))

        def scripted_pay():
            return next(row['index'] for row in worker.snapshot()['elements'] if row['name'] == '바로 진행')
        try:
            worker.show(page_url, 10)
            worker.click(scripted_pay(), 10)   # the owner acting in their own window: not guarded
            self.assertEqual(self.server.posts, ['/pay'])
            worker.hide()
            worker.goto(page_url, 10)
            with self.assertRaises(ToolError) as caught:
                worker.click(scripted_pay(), 10)
            self.assertEqual(caught.exception.code, 'approval_required', 'armed again once the window is hidden')
            self.assertEqual(self.server.posts, ['/pay'])
        finally:
            worker.close()

    def test_a_desktop_only_page_widens_the_phone_window_and_its_far_right_controls_work(self):
        """#254: in a phone session a page wider than the window widens it (no reload); taps and typing land;
        the next page is measured afresh from the phone width; the session's end restores the window."""
        worker = bs.WebKitWorkerDriver('p', cwd=self.tmp.name, allowed_origins_for_tests=(self.fixture,))

        def frame_until(check, seconds=8):
            # The phone polls frames; the worker measures the page after each one.
            deadline = time.monotonic() + seconds
            while True:
                frame = worker.frame()
                if check(frame) or time.monotonic() > deadline:
                    return frame
                time.sleep(0.3)

        def widths(count=8):
            seen = []
            for _ in range(count):
                seen.append(worker.frame()['width'])
                time.sleep(0.3)
            return seen
        try:
            worker.show(self.origin + '/desktop-only', 10)
            before = worker.frame()
            self.assertEqual(before['width'], 390.0)
            navigations = worker.navigations()
            worker.remote_begin()
            frame = frame_until(lambda frame: frame['width'] > 390.0)
            self.assertEqual((frame['width'], frame['height']), (1100.0, before['height']),
                             'as wide as the page, the same height')
            self.assertEqual(worker.navigations(), navigations, 'only a resize: the page was not reloaded')
            # The phone maps a tap by the frame's own width and height: view points.
            worker.remote_input('tap', x=1020.0, y=120.0)
            self.assertEqual(frame_until(lambda frame: frame['focus'] == 'text')['focus'], 'text', '#293 focus')
            worker.remote_input('text', text='홍길동')
            worker.remote_input('tap', x=1020.0, y=230.0)
            deadline = time.monotonic() + 5
            while '눌림' not in (text := worker.snapshot()['text']) and time.monotonic() < deadline:
                time.sleep(0.2)
            self.assertIn('눌림 홍길동', text, 'the far-right button got the tap after the typed name')
            # A later phone page is measured from the phone width again.
            worker.goto(self.origin + '/device', 10)
            self.assertEqual(set(widths()), {390.0}, 'a page that fits stays phone-sized')
            self.assertIn('폭 390', worker.snapshot()['text'], 'its own scripts read the phone width')
            # A page whose overflow grows with the window is not widened for good.
            worker.goto(self.origin + '/off-canvas', 10)
            seen = widths()
            self.assertEqual(seen[-3:], [390.0] * 3, seen)
            # Back on the wide page; the session ends and the window is as it was before it.
            worker.goto(self.origin + '/desktop-only', 10)
            self.assertEqual(frame_until(lambda frame: frame['width'] > 390.0)['width'], 1100.0)
            worker.remote_end()
            self.assertEqual((worker.frame()['width'], worker.frame()['height']), (before['width'], before['height']))
        finally:
            worker.close()

    def test_sessions_survive_a_restart_only_through_the_encrypted_jar(self):
        value = self.server.cookie_value
        first = self.session('work-1')
        try:
            first.open({'url': self.origin + '/session-start', 'effect': 'navigate'})
            self.assertEqual(first.open({'url': self.origin + '/whoami', 'effect': 'read'})['text'], '세션 있음')
        finally:
            first.close()
        self.assertEqual(stat.S_IMODE(self.jar.path.stat().st_mode), 0o600)
        self.assertIsNotNone(self.key.get(), 'the jar key is in the Keychain')
        self.assertNotIn(value.encode(), self.jar.path.read_bytes())
        sessions = self.profile.status()['sessions']
        self.assertEqual([row['site'] for row in sessions], ['127.0.0.1'])
        self.assertNotIn(value, flat(self.profile.status()))
        second = self.session('work-2')
        try:
            self.assertEqual(second.open({'url': self.origin + '/whoami', 'effect': 'read'})['text'], '세션 있음',
                             'a new worker process got the session from the jar')
        finally:
            second.close()
        self.assertEqual(self.profile.delete_site('127.0.0.1')['deleted'], True)
        third = self.session('work-3')
        try:
            self.assertEqual(third.open({'url': self.origin + '/whoami', 'effect': 'read'})['text'], '세션 없음')
        finally:
            third.close()
        self.assertEqual(self.scan_for(value), [], 'no plaintext copy of the fixture cookie anywhere')

    def scan_for(self, value):
        needle = value.encode()
        home = Path.home() / 'Library'
        hits = []
        for root in (Path(self.tmp.name), home / 'WebKit', home / 'HTTPStorages'):
            if not root.is_dir():
                continue
            for folder, _, files in os.walk(root, followlinks=False):
                for name in files:
                    path = Path(folder) / name
                    try:
                        if path.is_symlink() or path.stat().st_size > 64 * 1024 * 1024:
                            continue
                        if needle in path.read_bytes():
                            hits.append(str(path))
                    except OSError:
                        continue
        return hits


if __name__ == '__main__':
    unittest.main()
