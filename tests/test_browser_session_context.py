"""#942: the Work's context names the browser sign-ins, and a shared one says so."""
import tempfile
import time
import types
import unittest
from pathlib import Path

from personal_agent import family_share
from personal_agent.information_use import context_claims
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore


class BrowserSessionsInContext(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = QuickStore(Path(self.temp.name))
        self.service = types.SimpleNamespace(store=self.store, browser_profile=None)
        self.text = lambda: AgentService.browser_sessions_text(self.service)

    def view(self, *sites):
        self.service.browser_profile = types.SimpleNamespace(_jar_view=lambda: ('stored', [{'site': site} for site in sites]))

    def test_no_sign_in_no_line(self):
        self.assertIsNone(self.text())
        self.view()
        self.assertIsNone(self.text())

    def test_sites_are_named_and_a_shared_one_says_so(self):
        self.view('shop.test', 'news.test')
        self.store.put(family_share.SHARED_KEY, {'shop.test': {'from': 'owner', 'since': 1}})
        line = self.text()
        self.assertIn("shop.test (the owner's sign-in shared with you", line)
        self.assertIn('payment is the owner', line)
        self.assertNotIn('news.test', line, '#563: a site with cookies only is not a sign-in')
        labels = [claim['label'] for claim in context_claims('legend\n{}\n' + line)]
        self.assertEqual(labels, ['로그인한 사이트: shop.test (공유받은 로그인)'])

    def test_a_site_the_owner_signed_in_to_names_when_and_that_a_site_can_end_it(self):
        """#1006 (live 2026-10-04): stored cookies outlived the site's session; the guest cart read as the owner's."""
        from personal_agent.quickstart_service import BROWSER_OWNER_SIGNINS_KEY
        self.view('shop.test', 'news.test')
        at = time.mktime((2026, 10, 3, 15, 24, 0, 0, 0, -1))
        self.store.put(BROWSER_OWNER_SIGNINS_KEY, {'shop.test': {'at': at, 'marks': ['m'], 'host': 'shop.test'}})
        line = self.text()
        self.assertIn('shop.test (owner signed in 2026-10-03 15:24)', line)
        self.assertIn('as a guest, the sign-in has ended', line)
        self.assertIn('call browser_sign_in', line)
        self.assertNotIn('news.test', line)
        labels = [claim['label'] for claim in context_claims('legend\n{}\n' + line)]
        self.assertEqual(labels, ['로그인한 사이트: shop.test'])

    def test_cookies_a_work_only_left_are_no_sign_in(self):
        """#563 (live 2026-11-01): a broker's cookies from browsing read as "the available signed-in session",
        so a new Mac's planner went to the browser and never weighed the official API."""
        from personal_agent.quickstart_service import BROWSER_OWNER_SIGNINS_KEY
        self.view('broker.test', 'news.test')
        self.assertIsNone(self.text(), 'no recorded sign-in, no share: nothing is claimed')
        self.store.put(BROWSER_OWNER_SIGNINS_KEY, {'news.test': {'at': 0, 'marks': ['m']}, 'broker.test': 'bad'})
        self.assertIsNone(self.text(), 'a record without a sign-in time is not one')

    def test_cookie_only_sites_never_crowd_out_a_sign_in(self):
        from personal_agent.quickstart_service import BROWSER_OWNER_SIGNINS_KEY, BROWSER_SESSION_SITES
        visited = [f'visited{index}.test' for index in range(BROWSER_SESSION_SITES + 5)]
        self.view(*visited, 'shop.test')
        self.store.put(BROWSER_OWNER_SIGNINS_KEY, {'shop.test': {'at': time.time(), 'marks': ['m']}})
        line = self.text()
        self.assertIn('shop.test (owner signed in', line)
        self.assertNotIn('visited', line)

    def test_a_broken_view_is_no_line(self):
        def broken():
            raise OSError('keychain')
        self.service.browser_profile = types.SimpleNamespace(_jar_view=broken)
        self.assertIsNone(self.text())

    def test_the_information_use_record_still_reads_the_context_json(self):
        rendered = 'Current context (legend)\n{"local_time": "2026-10-01T14:00", "timezone": "Asia/Seoul"}\nBrowser sign-ins: shop.test'
        claims = context_claims(rendered)
        self.assertIn('clock', [claim['ref'] for claim in claims])
        self.assertIn({'ref': 'browser:shop.test', 'label': '로그인한 사이트: shop.test'}, claims, 'the audit names the sites sent')

    def test_an_unreadable_share_row_never_blocks_the_turn(self):
        from personal_agent.quickstart_service import BROWSER_OWNER_SIGNINS_KEY
        self.view('shop.test')
        self.store.put(BROWSER_OWNER_SIGNINS_KEY, {'shop.test': {'at': time.time(), 'marks': ['m']}})
        config = self.store.config
        self.store.config = lambda key, default=None, **kw: (_ for _ in ()).throw(ValueError('corrupt')) if key == family_share.SHARED_KEY else config(key, default, **kw)
        self.assertIn('shop.test', self.text())


if __name__ == '__main__':
    unittest.main()
