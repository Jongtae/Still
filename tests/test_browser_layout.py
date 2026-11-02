"""#572: the AI can ask for a site's desktop version; Still keeps it per site.

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

    def test_desktop_is_kept_for_the_site_across_hosts_and_works_until_mobile(self):
        work = self.work('w1')
        self.assertNotIn('layout', work.open({'url': 'https://www.shop.co.kr/account', 'effect': 'read'}))
        page = work.open({'url': 'https://www.shop.co.kr/account', 'effect': 'read', 'layout': 'desktop'})
        self.assertEqual(page['layout'], 'desktop')
        # Another host of the same site, without asking again, and a different site.
        self.assertEqual(work.open({'url': 'https://m.shop.co.kr/account', 'effect': 'read'})['layout'], 'desktop')
        work.open({'url': 'https://www.other.co.kr/account', 'effect': 'read'})
        self.assertEqual(self.layouts(), ['mobile', 'desktop', 'desktop', 'mobile'])
        work.close()
        later = self.work('w2')
        self.assertEqual(later.open({'url': 'https://shop.co.kr/account', 'effect': 'read'})['layout'], 'desktop')
        later.open({'url': 'https://shop.co.kr/account', 'effect': 'read', 'layout': 'mobile'})
        later.open({'url': 'https://www.shop.co.kr/account', 'effect': 'read'})
        self.assertEqual(self.layouts()[-3:], ['desktop', 'mobile', 'mobile'])
        later.close()
        self.assertEqual(json.loads((Path(self.tmp.name) / 'profile' / bs.LAYOUTS_NAME).read_text()), {})

    def test_desktop_sites_reads_the_saved_choices_and_mobile_removes_one(self):
        self.assertEqual(self.profile.desktop_sites(), [])
        self.profile.set_layout('www.shop.co.kr', 'desktop')
        self.profile.set_layout('www.other.co.kr', 'desktop')
        saved = bs.BrowserProfile(self.profile.profile_dir)
        self.assertEqual(saved.desktop_sites(), ['other.co.kr', 'shop.co.kr'])
        saved.set_layout('m.shop.co.kr', 'mobile')
        self.assertEqual(self.profile.desktop_sites(), ['other.co.kr'])

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

    def test_the_login_window_loads_the_site_in_its_kept_layout(self):
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

        self.profile.set_layout('accounts.shop.co.kr', 'desktop')
        for url, expected in (('https://www.shop.co.kr/login', 'desktop'), ('https://www.other.co.kr/login', 'mobile')):
            window = Window()
            jar = CookieJar(Path(self.tmp.name) / 'profile' / JAR_NAME, MemoryKey(), Clock())
            profile = bs.BrowserProfile(Path(self.tmp.name) / 'profile', launcher=lambda d, h: window, jar=jar)
            profile.open_for_login(url, wait=True)
            self.assertEqual(window.shown, [(url, expected)])


class LayoutArgument(unittest.TestCase):
    def test_browser_open_offers_layout_as_an_optional_choice(self):
        tool = next(d['function'] for d in DEFINITIONS if d['function']['name'] == 'browser_open')
        self.assertEqual(tool['parameters']['properties']['layout']['enum'], ['mobile', 'desktop'])
        self.assertNotIn('layout', tool['parameters']['required'])
        self.assertIn('layout=desktop', tool['description'])


if __name__ == '__main__':
    unittest.main()
