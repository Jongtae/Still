"""#572: the AI can ask for desktop for this Work only.

Evidence class: focused unit tests with a fake page driver and a temporary
profile directory; no WebKit worker, model or network is used.  The worker's
own user agent and window size are tested in tests/test_remote_login.py.
"""
import json
import tempfile
import unittest
from pathlib import Path

from personal_agent import browser_session as bs
from personal_agent.agent_runtime import DEFINITIONS

from test_browser_session import FakeDriver


class LayoutDriver(FakeDriver):
    """A fake driver that takes the site's layout, as the WebKit worker driver does."""
    takes_layout = True

    def goto(self, url, timeout, layout=None):
        self.log.append(('layout', layout))
        super().goto(url, timeout)


class SiteLayout(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = []
        self.profile = bs.BrowserProfile(Path(self.tmp.name) / 'profile', launcher=lambda d, h: LayoutDriver(log=self.log))

    def work(self, ident):
        return bs.BrowserSession(self.profile.driver_factory(ident), work_id=ident)

    def layouts(self):
        return [entry[1] for entry in self.log if entry[0] == 'layout']

    def test_desktop_is_kept_only_for_the_same_work_and_its_resume(self):
        work = self.work('A')
        work.open({'url': 'https://shop.test/account', 'effect': 'read'})
        work.open({'url': 'https://shop.test/account', 'effect': 'read', 'layout': 'desktop'})
        work.open({'url': 'https://auth.test/account', 'effect': 'read'})
        work.close()
        resumed = self.work('A')
        resumed.open({'url': 'https://shop.test/account', 'effect': 'read'})
        resumed.close()
        other = self.work('B')
        other.open({'url': 'https://shop.test/account', 'effect': 'read'})
        other.close()
        self.assertEqual(self.layouts(), ['mobile', 'desktop', 'desktop', 'desktop', 'mobile'])
        self.assertFalse((self.profile.profile_dir / bs.LAYOUTS_NAME).exists())

    def test_legacy_layouts_are_ignored_and_left_untouched(self):
        self.profile.profile_dir.mkdir()
        path = self.profile.profile_dir / bs.LAYOUTS_NAME
        legacy = json.dumps({'shop.test': 'desktop'})
        path.write_text(legacy)
        work = self.work('A')
        work.open({'url': 'https://shop.test/account', 'effect': 'read'})
        work.open({'url': 'https://shop.test/account', 'effect': 'read', 'layout': 'desktop'})
        work.close()
        self.assertEqual(self.layouts(), ['mobile', 'desktop'])
        self.profile.remove_legacy_profile()
        self.assertEqual(path.read_text(), legacy)
        self.assertEqual(bs.BrowserProfile(self.profile.profile_dir).layout_for('shop.test', work_id='B'), 'mobile')
        self.assertEqual(self.profile.desktop_sites(), [])

    def test_an_unknown_layout_is_refused_before_anything_is_opened(self):
        work = self.work('w1')
        with self.assertRaises(ValueError):
            work.open({'url': 'https://www.shop.co.kr/account', 'effect': 'read', 'layout': 'tablet'})
        self.assertEqual(self.log, [])
        self.assertEqual(self.profile.layout_for('www.shop.co.kr'), 'mobile')
        with self.assertRaises(ValueError):
            self.profile.set_layout('www.shop.co.kr', 'tablet')
        work.close()

    def test_a_driver_that_takes_no_layout_is_sent_none(self):
        log = []
        profile = bs.BrowserProfile(Path(self.tmp.name) / 'plain', launcher=lambda d, h: FakeDriver(log=log))
        work = bs.BrowserSession(profile.driver_factory('w1'), work_id='w1')
        page = work.open({'url': 'https://www.shop.co.kr/account', 'effect': 'read', 'layout': 'desktop'})
        self.assertNotIn('layout', page)   # nothing claims a desktop page the driver was never asked for
        self.assertEqual(log[0][0], 'goto')
        work.close()

    def test_the_login_window_uses_its_work_layout_across_auth_hosts(self):
        from personal_agent.browser_jar import JAR_NAME, CookieJar
        from test_browser_webkit import Clock, CookieDriver, MemoryKey

        class Window(CookieDriver):
            takes_layout = True

            def __init__(self):
                super().__init__([])
                self.shown = []

            def show(self, url, timeout, layout=None):
                self.shown.append((url, layout))

            def is_open(self):
                return False

        for work_id, expected in (('A', 'desktop'), ('B', 'mobile'), ('A', 'desktop')):
            url = 'https://auth.test/login'
            window = Window()
            jar = CookieJar(Path(self.tmp.name) / 'profile' / JAR_NAME, MemoryKey(), Clock())
            profile = bs.BrowserProfile(Path(self.tmp.name) / 'profile', launcher=lambda d, h: window, jar=jar)
            profile.set_layout('shop.test', 'desktop', work_id='A')
            profile.open_for_login(url, wait=True, work_id=work_id)
            self.assertEqual(window.shown, [(url, expected)])


class LayoutArgument(unittest.TestCase):
    def test_browser_open_offers_layout_as_an_optional_choice(self):
        tool = next(d['function'] for d in DEFINITIONS if d['function']['name'] == 'browser_open')
        self.assertEqual(tool['parameters']['properties']['layout']['enum'], ['mobile', 'desktop'])
        self.assertNotIn('layout', tool['parameters']['required'])
        self.assertIn('layout=desktop', tool['parameters']['properties']['layout']['description'])


if __name__ == '__main__':
    unittest.main()
