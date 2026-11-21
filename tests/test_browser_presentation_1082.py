"""#1082: the owner's AI opens a Work's pages in a chosen language and time zone, fixed per Work at launch.

Evidence class: focused unit tests with fake page drivers, a fake worker process
(``tests/browser_fake_worker.py``) speaking the shared worker contract, and a
temporary profile directory.  No WebKit, Chromium, model or network is used, so
nothing here observes a real engine applying a locale or time zone.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

from personal_agent import browser_session as bs
from personal_agent.agent_runtime import DEFINITIONS, ToolError, evidence_summary
from personal_agent.messages import msg

from test_browser_session import ORIGIN, PAGES, Approvals, FakeDriver

FAKE_WORKER = Path(__file__).resolve().with_name('browser_fake_worker.py')
URL = ORIGIN + '/checkout'


class PresentationDriver(FakeDriver):
    """A fake driver that acknowledges its launch presentation and reports what the page observed."""

    def __init__(self, presentation=None, unsupported=(), log=None):
        super().__init__(PAGES, log=log)
        presentation = presentation or {}
        self.launched_with = dict(presentation)
        if presentation:
            applied = {key: (None if key in unsupported else presentation.get(key)) for key in ('locale', 'timezone')}
            self.presentation = {**applied, 'unsupported': [key for key in unsupported if presentation.get(key)]}
            self.presentation_observed = {'navigator_language': presentation.get('locale'),
                                          'intl_locale': presentation.get('locale'),
                                          'intl_timezone': None if 'timezone' in unsupported else presentation.get('timezone')}
        else:
            self.presentation = None
            self.presentation_observed = None


class Launcher:
    """A profile launcher recording each launch's presentation (``None`` = the launcher got no keyword)."""

    def __init__(self, unsupported=()):
        self.launches, self.drivers, self.unsupported = [], [], unsupported

    def __call__(self, profile_dir, headless, **kwargs):
        presentation = kwargs.get('presentation')
        self.launches.append(presentation)
        driver = PresentationDriver(presentation, self.unsupported)
        self.drivers.append(driver)
        return driver


class Validation(unittest.TestCase):
    def test_bcp47_tags_with_script_subtags_are_accepted_and_canonicalised(self):
        for given, expected in (('en-US', 'en-US'), ('ja', 'ja'), ('zh-Hans-CN', 'zh-Hans-CN'),
                                ('zh-hans-cn', 'zh-Hans-CN'), ('EN-us', 'en-US'), ('es-419', 'es-419'),
                                ('sr-Latn', 'sr-Latn'), ('de-DE-1996', 'de-DE-1996')):
            with self.subTest(given=given):
                self.assertEqual(bs.presentation_locale(given), expected)

    def test_omitted_locale_is_none(self):
        for given in (None, ''):
            self.assertIsNone(bs.presentation_locale(given))

    def test_underscores_and_garbage_are_refused(self):
        for given in ('en_US', 'zh_Hans_CN', 'english!', 'e', 'en-', '-US', 'en US', 'en-US;q=0.9', 'a' * 40,
                      'en-USA-x', 123, ['en']):
            with self.subTest(given=given):
                with self.assertRaises(ValueError):
                    bs.presentation_locale(given)

    def test_iana_time_zones_are_accepted_and_others_refused(self):
        self.assertEqual(bs.presentation_timezone('Asia/Tokyo'), 'Asia/Tokyo')
        self.assertEqual(bs.presentation_timezone('America/New_York'), 'America/New_York')
        self.assertIsNone(bs.presentation_timezone(None))
        self.assertIsNone(bs.presentation_timezone(''))
        for given in ('Mars/Olympus', '../etc/passwd', '/etc/localtime', 'Asia/Tokyo; rm', 5):
            with self.subTest(given=given):
                with self.assertRaises(ValueError):
                    bs.presentation_timezone(given)

    def test_a_bad_value_is_refused_before_anything_is_opened(self):
        launcher = Launcher()
        profile = bs.BrowserProfile(Path(tempfile.mkdtemp()) / 'profile', launcher=launcher)
        work = bs.BrowserSession(profile.driver_factory('A'), work_id='A')
        for args in ({'locale': 'en_US'}, {'timezone': 'Mars/Olympus'}):
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    work.open({'url': URL, 'effect': 'read', **args})
        self.assertEqual(launcher.launches, [])
        self.assertIsNone(profile.presentation_for('A'))


class PerWork(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.launcher = Launcher()
        self.profile = bs.BrowserProfile(Path(self.tmp.name) / 'profile', launcher=self.launcher)

    def work(self, ident, **kwargs):
        return bs.BrowserSession(self.profile.driver_factory(ident), work_id=ident, **kwargs)

    def test_omitted_is_todays_behavior(self):
        work = self.work('A')
        page = work.open({'url': URL, 'effect': 'read'})
        self.assertEqual(self.launcher.launches, [None], 'the launcher gets no presentation keyword')
        self.assertNotIn('presentation', page)
        self.assertNotIn('presentation', evidence_summary('browser_open', page))

    def test_the_first_open_sets_the_work_and_its_continuation_keeps_it(self):
        work = self.work('A')
        page = work.open({'url': URL, 'effect': 'read', 'locale': 'ja-JP', 'timezone': 'Asia/Tokyo'})
        work.open({'url': ORIGIN + '/coupon', 'effect': 'read'})   # omitted later = keep
        work.open({'url': URL, 'effect': 'read', 'locale': 'ja-jp'})   # the same value, other case
        work.close()
        resumed = self.work('A')   # the continuation after a sign-in: same Work id, a new worker
        resumed.open({'url': URL, 'effect': 'read'})
        resumed.close()
        wanted = {'locale': 'ja-JP', 'timezone': 'Asia/Tokyo'}
        self.assertEqual(self.launcher.launches, [wanted, wanted])
        self.assertEqual(page['presentation']['requested'], wanted)
        self.assertEqual(page['presentation']['applied'], wanted)
        self.assertEqual(page['presentation']['unsupported'], [])
        self.assertEqual(page['presentation']['observed']['intl_timezone'], 'Asia/Tokyo')

    def test_other_works_and_standalone_login_use_defaults(self):
        work = self.work('A')
        work.open({'url': URL, 'effect': 'read', 'locale': 'zh-Hans-CN'})
        work.close()
        other = self.work('B')
        page = other.open({'url': URL, 'effect': 'read'})
        other.close()
        self.assertEqual(self.launcher.launches, [{'locale': 'zh-Hans-CN', 'timezone': None}, None])
        self.assertNotIn('presentation', page)
        self.assertIsNone(self.profile.presentation_for(None))
        self.assertEqual(self.profile.presentation_for('B'), {'locale': None, 'timezone': None})

    def test_login_window_uses_its_work_presentation_and_standalone_gets_none(self):
        class Window:
            def __init__(self):
                self.shown = []

            def show(self, url, timeout, layout=None):
                self.shown.append(url)

            def is_open(self):
                return False

            def close(self):
                pass

        launches = []

        def launcher(profile_dir, headless, **kwargs):
            launches.append(kwargs.get('presentation'))
            return Window()

        from personal_agent.browser_jar import JAR_NAME, CookieJar, MemoryKey
        jar = CookieJar(Path(self.tmp.name) / 'p2' / JAR_NAME, MemoryKey(), lambda: 1000.0)
        profile = bs.BrowserProfile(Path(self.tmp.name) / 'p2', launcher=launcher, jar=jar)
        profile.fix_presentation('A', {'locale': 'en-US', 'timezone': 'America/New_York'})
        profile.open_for_login('https://auth.test/login', wait=True, work_id='A')
        profile.open_for_login('https://auth.test/login', wait=True)
        profile.open_for_login('https://auth.test/login', wait=True, work_id='B')
        self.assertEqual(launches, [{'locale': 'en-US', 'timezone': 'America/New_York'}, None, None])

    def test_a_mid_work_change_is_refused_without_a_restart(self):
        work = self.work('A')
        work.open({'url': URL, 'effect': 'read', 'locale': 'ja-JP'})
        steps = work.budget.attempts_used
        for args in ({'locale': 'en-US'}, {'timezone': 'Asia/Tokyo'}):
            with self.subTest(args=args):
                with self.assertRaises(ToolError) as refused:
                    work.open({'url': ORIGIN + '/coupon', 'effect': 'read', **args})
                self.assertEqual(refused.exception.code, 'presentation_fixed')
                self.assertIn('ja-JP', str(refused.exception))
        self.assertEqual(len(self.launcher.launches), 1, 'no restart')
        driver = self.launcher.drivers[0]
        self.assertFalse(driver.closed)
        self.assertEqual([entry[1] for entry in driver.log if entry[0] == 'goto'], [URL], 'nothing else was opened')
        self.assertEqual(work.budget.attempts_used, steps, 'a refused open spends no step')

    def test_a_default_work_refuses_a_later_presentation(self):
        work = self.work('A')
        work.open({'url': URL, 'effect': 'read'})
        with self.assertRaises(ToolError) as refused:
            work.open({'url': URL, 'effect': 'read', 'locale': 'ja-JP'})
        self.assertEqual(refused.exception.code, 'presentation_fixed')
        self.assertEqual(len(self.launcher.launches), 1)

    def test_the_refusal_is_in_every_owner_language(self):
        for lang in ('ko', 'en', 'ja', 'zh'):
            with self.subTest(lang=lang):
                text = msg('browser.presentation_fixed', lang, locale='ja-JP', timezone='Asia/Tokyo')
                self.assertIn('ja-JP', text)
                self.assertNotIn('{', text)

    def test_forget_work_clears_the_settings(self):
        work = self.work('A')
        work.open({'url': URL, 'effect': 'read', 'locale': 'ja-JP', 'layout': 'desktop'})
        work.close()
        self.profile.forget_work('A')
        self.assertIsNone(self.profile.presentation_for('A'))
        self.assertEqual(self.profile.layout_for(work_id='A'), 'mobile')
        again = self.work('A')
        again.open({'url': URL, 'effect': 'read', 'locale': 'en-US'})
        self.assertEqual(self.launcher.launches[-1], {'locale': 'en-US', 'timezone': None})

    def test_an_unsupported_time_zone_is_reported_not_claimed(self):
        launcher = Launcher(unsupported=('timezone',))
        profile = bs.BrowserProfile(Path(self.tmp.name) / 'p3', launcher=launcher)
        work = bs.BrowserSession(profile.driver_factory('A'), work_id='A')
        page = work.open({'url': URL, 'effect': 'read', 'locale': 'ja-JP', 'timezone': 'Asia/Tokyo'})
        report = page['presentation']
        self.assertEqual(report['requested'], {'locale': 'ja-JP', 'timezone': 'Asia/Tokyo'})
        self.assertIsNone(report['applied']['timezone'])
        self.assertEqual(report['applied']['locale'], 'ja-JP')
        self.assertEqual(report['unsupported'], ['timezone'])
        self.assertIsNone(report['observed']['intl_timezone'])
        summary = evidence_summary('browser_open', page)
        self.assertEqual(summary['presentation']['unsupported'], ['timezone'])
        self.assertIsNone(summary['presentation']['applied']['timezone'])

    def test_an_engine_that_says_nothing_is_reported_as_unknown(self):
        class Silent(FakeDriver):
            pass

        profile = bs.BrowserProfile(Path(self.tmp.name) / 'p4', launcher=lambda d, h, **k: Silent(PAGES))
        work = bs.BrowserSession(profile.driver_factory('A'), work_id='A')
        page = work.open({'url': URL, 'effect': 'read', 'locale': 'ja-JP'})
        self.assertEqual(page['presentation']['requested'], {'locale': 'ja-JP', 'timezone': None})
        self.assertIsNone(page['presentation']['applied'], 'never copied from the request')
        self.assertIsNone(page['presentation']['observed'])


class ApprovalBinding(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def refused_open(self, work_id, **args):
        probe = Approvals()
        profile = bs.BrowserProfile(Path(self.tmp.name) / work_id, launcher=Launcher())
        work = bs.BrowserSession(profile.driver_factory(work_id), work_id=work_id, approvals=probe)
        with self.assertRaises(ToolError):
            work.open({'url': ORIGIN + '/pay', 'effect': 'payment', **args})
        return probe.requests[-1]

    def test_the_default_binding_is_todays_url_only_binding(self):
        binding, _ = self.refused_open('w')
        self.assertEqual(binding, bs.step_binding('w', 'browser_open', ORIGIN + '/pay', ORIGIN + '/pay', ORIGIN + '/pay'))

    def test_an_approval_does_not_cover_a_page_opened_under_other_settings(self):
        default, _ = self.refused_open('w')
        japanese, description = self.refused_open('w', locale='ja-JP', timezone='Asia/Tokyo')
        self.assertNotEqual(bs.binding_digest(default), bs.binding_digest(japanese))
        self.assertIn('ja-JP', description, 'the owner sees what the approval covers')
        self.assertIn('Asia/Tokyo', description)
        english, _ = self.refused_open('w', locale='en-US', timezone='Asia/Tokyo')
        self.assertNotEqual(bs.binding_digest(english), bs.binding_digest(japanese))

    def test_an_approval_for_default_settings_is_not_consumed_under_other_settings(self):
        default, _ = self.refused_open('w')
        approvals = Approvals(default)
        profile = bs.BrowserProfile(Path(self.tmp.name) / 'x', launcher=Launcher())
        work = bs.BrowserSession(profile.driver_factory('w'), work_id='w', approvals=approvals)
        with self.assertRaises(ToolError) as refused:
            work.open({'url': ORIGIN + '/pay', 'effect': 'payment', 'locale': 'ja-JP'})
        self.assertEqual(refused.exception.code, 'approval_required')
        self.assertEqual(len(approvals.issued), 1, 'the default approval was not spent')

    def test_a_matching_approval_is_consumed(self):
        japanese, _ = self.refused_open('w', locale='ja-JP')
        approvals = Approvals(japanese)
        profile = bs.BrowserProfile(Path(self.tmp.name) / 'y', launcher=Launcher())
        work = bs.BrowserSession(profile.driver_factory('w'), work_id='w', approvals=approvals)
        work.open({'url': ORIGIN + '/pay', 'effect': 'payment', 'locale': 'ja-JP'})
        self.assertEqual(approvals.issued, [])


class WorkerCommand(unittest.TestCase):
    def test_flags_are_passed_only_when_set(self):
        base = bs.chromium_worker_command('p', '/opt/chrome')
        self.assertNotIn('--locale', base)
        self.assertNotIn('--timezone', base)
        self.assertEqual(bs.chromium_worker_command('p', '/opt/chrome', locale=None, timezone=None), base)
        full = bs.chromium_worker_command('p', '/opt/chrome', locale='ja-JP', timezone='Asia/Tokyo')
        self.assertEqual(full[:len(base)], base)
        self.assertEqual(full[len(base):], ['--locale', 'ja-JP', '--timezone', 'Asia/Tokyo'])
        webkit = bs.webkit_worker_command('p')
        self.assertEqual(webkit, [sys.executable, '-P', '-m', 'personal_agent.browser_worker', '--profile', 'p'])
        self.assertEqual(bs.webkit_worker_command('p', locale='zh-Hans-CN')[len(webkit):], ['--locale', 'zh-Hans-CN'])
        self.assertEqual(bs.webkit_worker_command('p', timezone='Asia/Tokyo')[len(webkit):],
                         ['--timezone', 'Asia/Tokyo'])


class FakeWorkerProcess(unittest.TestCase):
    """The driver against a fake worker process speaking the shared contract (ready / navigate fields)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = Path(self.tmp.name) / 'commands.jsonl'

    def driver(self, *flags):
        driver = bs.WebKitWorkerDriver('p', command=[sys.executable, str(FAKE_WORKER), str(self.log), *flags])
        self.addCleanup(driver.close)
        return driver

    def starts(self):
        lines = [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []
        return [line['argv'] for line in lines if line.get('op') == 'start']

    def test_ready_and_navigate_are_read_and_a_crash_restart_keeps_the_flags(self):
        driver = self.driver('--locale', 'ja-JP', '--timezone', 'Asia/Tokyo')
        self.assertEqual(driver.presentation, {'locale': 'ja-JP', 'timezone': 'Asia/Tokyo', 'unsupported': []})
        driver.goto('https://shop.test/a', 5)
        self.assertEqual(driver.presentation_observed,
                         {'navigator_language': 'ja-JP', 'intl_locale': 'ja-JP', 'intl_timezone': 'Asia/Tokyo'})
        with self.assertRaises(bs.WorkerError):
            driver.goto('https://shop.test/crash', 5)
        driver.goto('https://shop.test/b', 5)   # restarted on the next call
        self.assertEqual(driver.restarts, 1)
        self.assertEqual(self.starts(), [['--locale', 'ja-JP', '--timezone', 'Asia/Tokyo']] * 2)
        self.assertEqual(driver.presentation['locale'], 'ja-JP')

    def test_an_unsupported_time_zone_is_carried_from_ready(self):
        driver = self.driver('--locale', 'ja-JP', '--timezone', 'Asia/Tokyo', '--fake-tz', 'unsupported')
        self.assertEqual(driver.presentation, {'locale': 'ja-JP', 'timezone': None, 'unsupported': ['timezone']})

    def test_a_worker_that_says_nothing_leaves_both_unknown(self):
        driver = self.driver()
        driver.goto('https://shop.test/a', 5)
        self.assertIsNone(driver.presentation)
        self.assertIsNone(driver.presentation_observed)


class ToolArguments(unittest.TestCase):
    def test_browser_open_offers_locale_and_timezone_as_optional_choices(self):
        tool = next(d['function'] for d in DEFINITIONS if d['function']['name'] == 'browser_open')
        properties, required = tool['parameters']['properties'], tool['parameters']['required']
        for name in ('locale', 'timezone'):
            self.assertIn(name, properties)
            self.assertNotIn(name, required)
            self.assertTrue(properties[name].get('description'))
            self.assertEqual(properties[name]['type'], 'string')


if __name__ == '__main__':
    unittest.main()
