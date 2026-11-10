"""#636: sign back in through the identity provider the owner already used, with no interaction on the provider.

Evidence classes, named separately:

* unit (model-free): ``browser_worker.authorization_host`` recognises an OAuth 2.0
  authorization request (RFC 6749 §4.1.1: ``response_type`` and ``client_id``;
  ``redirect_uri`` names the client endpoint, RFC 6749 §3.1.2; OIDC Core §3.1.2.1 adds ``scope=openid`` to the same request)
  by its parameter names and keeps the host only; the worker's
  ``note_authorization`` keeps hosts only, bounded;
* unit (model-free, a fake two-host page driver and a scripted approvals
  surface): the re-sign-in hint, the provider that returns without asking, the
  refusal of every press and typing step on the provider's pages (``ran: false``,
  nothing reaches the page) and the owner hand-off, the provider that asks for a
  password, no hint and no special path without a recorded provider;
* model-free service integration (fake drivers, temporary stores, the memory
  jar): only the owner's own login window records a provider, hosts only;
  no hint without the provider's session; the owner's off switch (every site,
  one site) through the conversational Settings draft; the information-use
  audit and the receipt; the effect rule (#618) for a refused step.

No live browser, site, account, model or network is used.  The hosts below are
the test's own (``.test``); ``src`` names no provider or site.
"""
import html
import json
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from urllib.parse import quote, urlsplit

from personal_agent import browser_session as bs
from personal_agent import browser_worker, information_use
from personal_agent.agent_runtime import Capabilities, ToolError, evidence_summary, record_effect, run_agent
from personal_agent.messages import msg
from personal_agent.providers import ModelAdapter
from personal_agent.quickstart_service import (BROWSER_PROVIDER_SIGNIN_KEY, BROWSER_PROVIDERS_KEY, AgentService)
from personal_agent.quickstart_store import QuickStore

from test_browser_session import Approvals, FakeDriver, Script, call, flat
from test_flow_login import ORIGIN, JarDriver, LoginHarness, memory_jar, wait_until

SITE = 'site.test'
PROVIDER = 'id.provider.test'
CLIENT = 'client-id-value-6361'
STATE = 'state-value-6362'
NONCE = 'nonce-value-6363'
REDIRECT = 'https://site.test/callback?return=redirect-value-6366'
AUTHORIZE = (f'https://{PROVIDER}/authorize?response_type=code&client_id={CLIENT}'
             f'&redirect_uri={quote(REDIRECT, safe="")}&scope=openid&state={STATE}&nonce={NONCE}')
QUERY_VALUES = (CLIENT, STATE, NONCE, 'callback', 'redirect-value-6366', REDIRECT, quote(REDIRECT, safe=''))
#: A federated sign-in: the site's provider (a broker) asks an upstream provider in turn.
UPSTREAM = 'login.upstream.test'
UPSTREAM_AUTHORIZE = (f'https://{UPSTREAM}/authorize?response_type=code&client_id=broker-client-6364'
                      f'&redirect_uri={quote(f"https://{PROVIDER}/upstream-callback", safe="")}&state=up-state-6365')
CFG = {'provider': 'compatible', 'endpoint': 'https://example.test/v1', 'model': 'test-model'}

PAGES = {
    f'{SITE}/login': f'''<html><head><title>로그인</title></head><body><form action="/session" method="post">
      <label>이메일 <input type="email" name="email"></label>
      <label>비밀번호 <input type="password" name="password"></label>
      <button type="submit">로그인</button></form>
      <a href="{html.escape(AUTHORIZE)}">다른 계정으로 로그인</a></body></html>''',
    f'{SITE}/home': '''<html><head><title>내 페이지</title></head><body><h1>주문 내역</h1>
      <a href="https://site.test/settings">설정</a></body></html>''',
    f'{SITE}/settings': '<html><head><title>설정</title></head><body><h1>설정</h1></body></html>',
    # A sign-in page that offers only the provider (no password field anywhere).
    f'{SITE}/start': f'''<html><head><title>시작</title></head><body><h1>시작하기</h1>
      <a href="{html.escape(AUTHORIZE)}">다른 계정으로 계속</a></body></html>''',
    # Another host of the provider's registrable domain that is no sign-in host (a search page).
    'search.provider.test/': '''<html><head><title>검색</title></head><body><h1>검색</h1>
      <label>검색어 <input type="text" name="q"></label><button type="button">검색하기</button></body></html>''',
    # An unrelated public site.
    'public.test/': '''<html><head><title>뉴스</title></head><body><h1>뉴스</h1>
      <button type="button">더 보기</button></body></html>''',
    # A public page of the site (no sign-in form, no session needed).
    f'{SITE}/': '<html><head><title>홈</title></head><body><h1>공개 페이지</h1></body></html>',
    # A provider page that asks for something without a password: an account choice / consent.
    f'{PROVIDER}/choose': '''<html><head><title>계정 선택</title></head><body><h1>계속할 계정</h1>
      <form action="/consent" method="post"><button type="submit">계속</button></form>
      <label>메모 <input type="text" name="note"></label></body></html>''',
    # A provider page that asks for a password.
    f'{UPSTREAM}/choose': '''<html><head><title>계정 선택</title></head><body><h1>업스트림 계정</h1>
      <button type="button">이 계정으로 계속</button></body></html>''',
    f'{PROVIDER}/signin': '''<html><head><title>로그인</title></head><body><form action="/pw" method="post">
      <label>아이디 <input type="email" name="id"></label>
      <label>비밀번호 <input type="password" name="pw"></label>
      <button type="submit">다음</button></form></body></html>''',
}
#: Where the provider's authorization endpoint sends the browser, by how the provider answers.
#: ``silent`` returns an authorization code to the site's redirect URI (the site then sets its session);
#: ``refused`` returns an OAuth error there (RFC 6749 §4.1.2.1); ``silent_no_session`` returns but the
#: site sets no session.
PROVIDER_ANSWERS = {'silent': 'https://site.test/callback?code=abc', 'chooser': f'https://{PROVIDER}/choose',
                    'password': f'https://{PROVIDER}/signin', 'refused': 'https://site.test/?error=access_denied&state=s',
                    'silent_no_session': 'https://site.test/home', 'federated': UPSTREAM_AUTHORIZE}


class TwoHostDriver(FakeDriver):
    """The fake page driver over two hosts; the provider's authorization endpoint redirects as ``answer`` says.

    ``authorizations`` answers as the worker's ``op_state`` does: the count and hosts that
    ``browser_worker.Worker.note_authorization`` recorded for each allowed navigation.
    """

    def __init__(self, answer='silent', log=None):
        super().__init__(pages=PAGES, origin='https://' + SITE, log=log)
        self.answer = answer
        self.worker = types.SimpleNamespace(authorizations=[], authorization_count=0, resolved={},
                                            provider_sign_in_policy={})
        self.provider_sign_in_policy = dict
        #: The browser's cookies by site; the signed-out site already holds an anonymous one.
        self.cookies = {SITE: [{'name': 'anon', 'value': 'a1', 'domain': SITE, 'path': '/', 'expires': None}]}

    def _path(self, url):
        parts = urlsplit(url)
        return (parts.hostname or '') + (parts.path or '/')

    def set_provider_sign_in_policy(self, policy):
        self.provider_sign_in_policy = policy

    def navigate(self, url):
        # Exercise the production navigation decision before a fake provider can redirect or set a cookie.
        self.worker.provider_sign_in_policy = self.provider_sign_in_policy()
        self.worker.resolved[browser_worker.origin_key(url)] = None
        decisions = []
        browser_worker.Worker.decide(self.worker, url, True, lambda ok, reason=None: decisions.append((ok, reason)))
        allowed, reason = decisions[0]
        if not allowed:
            exc = ToolError(bs.NAVIGATION_REFUSALS[reason], reason)
            exc.posted = False
            raise exc
        self.log.append(('navigation', url))
        browser_worker.Worker.note_authorization(self.worker, url)

    def goto(self, url, timeout):
        self.log.append(('goto', url, timeout))
        self.navigate(url)
        if self._path(url) == f'{SITE}/signin-redirect':
            url = AUTHORIZE   # the site's own sign-in address redirects to the provider at once
            self.navigate(url)
        if self._path(url) == f'{PROVIDER}/authorize':
            url = PROVIDER_ANSWERS[self.answer]   # the provider's redirect: navigation only
            if self.answer == 'refused':
                # The error page sets its flash cookie during the redirect, after the baseline.
                self.cookies[SITE].append({'name': 'flash', 'value': 'denied', 'domain': SITE,
                                           'path': '/', 'expires': None})
        if self._path(url) == f'{UPSTREAM}/authorize':
            browser_worker.Worker.note_authorization(self.worker, url)   # the broker's own authorization request
            url = f'https://{UPSTREAM}/choose'
        if self._path(url) == f'{SITE}/callback':
            # The site exchanges the code and sets its session cookie, then shows the signed-in page.
            self.cookies[SITE] = [{'name': 'sid', 'value': 'session-' + str(time.time_ns()), 'domain': SITE,
                                   'path': '/', 'expires': None}]
            url = f'https://{SITE}/home'
        if self._path(url) not in self.pages:
            raise RuntimeError('not found')
        self.url = url

    def cookies_export(self):
        return {site: list(rows) for site, rows in self.cookies.items()}, []

    def authorizations(self):
        return self.worker.authorization_count, tuple(self.worker.authorizations)


class ProviderApprovals(Approvals):
    """A scripted approvals surface with the service's in-flow login and its recorded providers."""

    def __init__(self, providers=None):
        super().__init__()
        self.providers = dict(providers or {})
        self.offers = []
        self.enabled = True
        self.off_sites = set()

    def provider_signin_settings(self):
        return {'enabled': self.enabled, 'off_sites': sorted(self.off_sites), 'sites': dict(self.providers)}

    def provider_sign_in_enabled(self, url):
        return self.enabled and bs.registrable_domain(urlsplit(url or '').hostname) not in self.off_sites

    def login_required(self, url, explicit=False, *, window_url=None):
        self.offers.append((url, explicit))
        return 'OFFER-EXPLICIT' if explicit else 'OFFER'

    def provider_sign_in(self, url):
        site = bs.registrable_domain(urlsplit(url).hostname)
        if not self.provider_sign_in_enabled(url):
            return None
        provider = self.providers.get(site)
        if isinstance(provider, list):
            return {'site': site, 'provider': provider[0], 'chain': provider}
        return {'site': site, 'provider': provider} if provider else None

    def provider_hosts(self):
        """As the service: every host of every recorded chain (``browser_provider_hosts``)."""
        return frozenset(host for chain in self.providers.values()
                         for host in (chain if isinstance(chain, list) else [chain]) if host)


def resign_session(answer='silent', providers=None):
    driver = TwoHostDriver(answer)
    approvals = ProviderApprovals({SITE: PROVIDER} if providers is None else providers)
    return bs.BrowserSession(lambda: driver, work_id='work-636', approvals=approvals), driver, approvals


def open_login(sess):
    return sess.run('browser_open', {'url': f'https://{SITE}/login', 'effect': 'navigate'})


class AuthorizationRequestTests(unittest.TestCase):
    def test_an_authorization_request_is_known_by_its_parameter_names_and_only_its_host_is_kept(self):
        self.assertEqual(browser_worker.authorization_host(AUTHORIZE), PROVIDER)
        for missing in ('response_type', 'client_id'):
            url = '&'.join(part for part in AUTHORIZE.split('&') if not part.split('?')[-1].startswith(missing + '='))
            self.assertIsNone(browser_worker.authorization_host(url), missing)
        self.assertIsNone(browser_worker.authorization_host(f'https://{SITE}/login?next=/home'))
        self.assertIsNone(browser_worker.authorization_host('intent://x?response_type=code&client_id=a&redirect_uri=b'))
        self.assertIsNone(browser_worker.authorization_host('not a url'))

    def test_worker_decides_each_main_frame_url_from_the_client_endpoint(self):
        base = f'https://{PROVIDER}/authorize?response_type=code&client_id={CLIENT}'
        policy = {'protected_hosts': [PROVIDER], 'enabled': True, 'off_sites': [SITE]}
        state = types.SimpleNamespace(provider_sign_in_policy=policy,
                                      resolved={browser_worker.origin_key(AUTHORIZE): None})
        for enabled, off_sites in ((False, []), (True, [SITE]), (True, [])):
            policy.update(enabled=enabled, off_sites=off_sites)
            for url, site_off in ((AUTHORIZE, SITE in off_sites),
                                  (base + '&redirect_uri=https%3A%2F%2Fsub.other.test%2Fcallback', False),
                                  (base, bool(off_sites)),
                                  (base + '&redirect_uri=invalid', bool(off_sites)),
                                  (base + '&redirect_uri=https%3A%2F%2Fsub.site.test%2Fcallback', SITE in off_sites)):
                for main_frame in (True, False):
                    with self.subTest(url=url.split('?')[0], enabled=enabled, off_sites=off_sites, main_frame=main_frame):
                        decisions = []
                        browser_worker.Worker.decide(state, url, main_frame,
                                                     lambda ok, reason=None: decisions.append((ok, reason)))
                        blocked = main_frame and (not enabled or site_off)
                        self.assertEqual(decisions, [(False, 'provider_sign_in_off') if blocked else (True, None)])
        self.assertFalse(any(value in flat(state.__dict__) for value in QUERY_VALUES))

    def test_the_worker_keeps_hosts_only_and_a_bounded_list(self):
        state = types.SimpleNamespace(authorizations=[], authorization_count=0)
        browser_worker.Worker.note_authorization(state, f'https://{SITE}/home')
        self.assertEqual((state.authorization_count, state.authorizations), (0, []))
        for _ in range(browser_worker.AUTHORIZATIONS_KEPT + 3):
            browser_worker.Worker.note_authorization(state, AUTHORIZE)
        self.assertEqual(state.authorization_count, browser_worker.AUTHORIZATIONS_KEPT + 3)
        self.assertEqual(state.authorizations, [PROVIDER] * browser_worker.AUTHORIZATIONS_KEPT)
        self.assertFalse(any(value in flat(state.__dict__) for value in QUERY_VALUES))


class ProviderPolicyProtocolTests(unittest.TestCase):
    def test_policy_and_typed_refusal_cross_the_worker_process_boundary(self):
        # Substitute only Cocoa and the page; keep the production command handling, policy decision,
        # navigation-refusal record and parent protocol decoder.
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / 'worker.py'
            script.write_text("""
import json
import sys
from personal_agent.browser_worker import Worker, origin_key
class PageWorker(Worker):
    def __init__(self):
        self.emit = lambda row: print(json.dumps(row), flush=True)
        self.pending = {}
        self.resolved = {}
        self.intranet = frozenset()
        self.provider_sign_in_policy = {}
        self.blocked = 0
        self.navigation = None
    def op_navigate(self, ident, command, timeout):
        self.resolved[origin_key(command['url'])] = None
        def decided(allowed, reason=None):
            if allowed:
                self.reply(ident)
            else:
                self.navigation_refused(True, reason)
                self.fail(ident, self.blocked_reason, posted=command.get('posted', False))
        self.decide(command['url'], True, decided)
    def op_quit(self, ident, command, timeout):
        self.reply(ident)
worker = PageWorker()
worker.emit({'event': 'ready'})
for line in sys.stdin:
    command = json.loads(line)
    worker.handle(command)
    if command['op'] == 'quit':
        break
""")
            driver = bs.WebKitWorkerDriver(command=[sys.executable, str(script)], cwd=folder)
            try:
                driver.goto(AUTHORIZE, 3)  # An owner window has no Work policy.
                policy = {'protected_hosts': [PROVIDER], 'enabled': True, 'off_sites': [SITE]}
                driver.set_provider_sign_in_policy(lambda: policy)
                with self.assertRaises(ToolError) as caught:
                    driver._request('navigate', 3, url=AUTHORIZE, posted=True)
                self.assertEqual(caught.exception.code, 'provider_sign_in_off')
                self.assertEqual(str(caught.exception), msg('browser.provider_sign_in_off'))
                self.assertIs(caught.exception.posted, True)
                driver.goto(f'https://{PROVIDER}/ordinary', 3)  # Not an authorization request.
                driver.goto(AUTHORIZE.replace(quote(REDIRECT, safe=''),
                                              quote('https://other.test/callback', safe='')), 3)
                missing = f'https://{PROVIDER}/authorize?response_type=code&client_id={CLIENT}'
                with self.assertRaises(ToolError) as caught:
                    driver.goto(missing, 3)
                self.assertEqual(caught.exception.code, 'provider_sign_in_off')
                policy['off_sites'].clear()
                driver.goto(missing, 3)
                driver.goto(AUTHORIZE, 3)  # Policy changes take effect on the next command.
            finally:
                driver.close()


class ResignInSessionTests(unittest.TestCase):
    def test_popup_sites_policy_includes_only_enabled_recorded_sites(self):
        approvals = ProviderApprovals({SITE: PROVIDER, 'other.test': UPSTREAM})
        sess = bs.BrowserSession(lambda: TwoHostDriver(), work_id='popup-policy', approvals=approvals)
        self.assertEqual(sess._provider_sign_in_policy()['popup_sites'], ['other.test', SITE])
        approvals.off_sites.add(SITE)
        policy = sess._provider_sign_in_policy()
        self.assertEqual(policy['popup_sites'], ['other.test'])
        self.assertEqual(policy['off_sites'], [SITE])
        approvals.enabled = False
        self.assertEqual(sess._provider_sign_in_policy()['popup_sites'], [])
        approvals.off_sites.clear()
        self.assertEqual(sess._provider_sign_in_policy()['popup_sites'], [])

    def test_popup_sites_policy_without_recorded_sites_is_empty(self):
        sess = bs.BrowserSession(lambda: TwoHostDriver(), work_id='popup-no-sites', approvals=Approvals())
        self.assertEqual(sess._provider_sign_in_policy()['popup_sites'], [])

    def assert_switch_refusal(self, sess, driver, approvals, url):
        sess.run('browser_open', {'url': url, 'effect': 'navigate'})
        before = (driver.url, driver.cookies_export(), driver.authorizations())
        navigation_count = len([row for row in driver.log if row[0] == 'navigation'])
        with self.assertRaises(ToolError) as caught:
            sess.run('browser_click', {'target': '다른 계정으로 계속', 'effect': 'navigate'})
        self.assertEqual(caught.exception.code, 'provider_sign_in_off')
        self.assertEqual(caught.exception.observed_step, {'posted': False, 'approved': False})
        self.assertIn('OFFER-EXPLICIT', str(caught.exception))
        self.assertEqual((driver.url, driver.cookies_export(), driver.authorizations()), before)
        self.assertEqual(len([row for row in driver.log if row[0] == 'navigation']), navigation_count)
        self.assertEqual(approvals.offers[-1], (url, True))

    def test_switch_off_globally_blocks_before_provider_navigation(self):
        sess, driver, approvals = resign_session()
        approvals.enabled = False
        self.assert_switch_refusal(sess, driver, approvals, f'https://{SITE}/start')

    def test_switch_off_for_one_site_leaves_another_site_enabled(self):
        sess, driver, approvals = resign_session()
        approvals.off_sites.add(SITE)
        self.assert_switch_refusal(sess, driver, approvals, f'https://{SITE}/start')
        # The same provider remains usable for another client endpoint.
        other_authorize = AUTHORIZE.replace(quote(REDIRECT, safe=""), quote("https://other.test/callback", safe=""))
        driver.pages['other.test/start'] = PAGES[f'{SITE}/start'].replace(html.escape(AUTHORIZE), html.escape(other_authorize))
        sess.run('browser_open', {'url': 'https://other.test/start', 'effect': 'navigate'})
        page = sess.run('browser_click', {'target': '다른 계정으로 계속', 'effect': 'navigate'})
        self.assertEqual(page['url'], f'https://{SITE}/home')
        self.assertEqual(driver.authorizations(), (1, (PROVIDER,)))

    def test_open_authorization_with_switch_off_is_not_run(self):
        sess, driver, approvals = resign_session()
        approvals.off_sites.add(SITE)
        sess.run('browser_open', {'url': f'https://{SITE}/start', 'effect': 'navigate'})
        before = (list(driver.log), driver.cookies_export())
        with self.assertRaises(ToolError) as caught:
            sess.run('browser_open', {'url': AUTHORIZE, 'effect': 'navigate'})
        self.assertEqual(caught.exception.code, 'provider_sign_in_off')
        self.assertIs(caught.exception.ran, False)
        self.assertEqual((driver.log, driver.cookies_export()), before)
        self.assertEqual(approvals.offers[-1], (f'https://{SITE}/start', True))

    def test_direct_authorization_applies_the_client_switch_without_an_origin_or_from_an_unrelated_site(self):
        other = AUTHORIZE.replace(quote(REDIRECT, safe=''), quote('https://other.test/callback', safe=''))
        for prior in (None, 'https://public.test/'):
            for url, blocked in ((AUTHORIZE, True), (other, False)):
                with self.subTest(prior=prior, blocked=blocked):
                    sess, driver, approvals = resign_session()
                    approvals.off_sites.add(SITE)
                    if prior:
                        sess.run('browser_open', {'url': prior, 'effect': 'navigate'})
                    before = list(driver.log)
                    if blocked:
                        with self.assertRaises(ToolError) as caught:
                            sess.run('browser_open', {'url': url, 'effect': 'navigate'})
                        self.assertEqual(caught.exception.code, 'provider_sign_in_off')
                        self.assertIs(caught.exception.ran, False)
                        self.assertEqual(driver.log, before)
                    else:
                        sess.run('browser_open', {'url': url, 'effect': 'navigate'})
                        self.assertEqual(driver.authorizations(), (1, (PROVIDER,)))

    def test_missing_or_unparseable_client_endpoint_fails_closed_if_any_switch_is_off(self):
        base = f'https://{PROVIDER}/authorize?response_type=code&client_id={CLIENT}'
        for suffix in ('', '&redirect_uri=', '&redirect_uri=not-a-url', '&redirect_uri=https%3A%2F%2F%5B',
                       '&redirect_uri=https%3A%2F%2Fsite.test%3Abad%2Fcallback',
                       '&redirect_uri=https%3A%2F%2Fother.test&redirect_uri=https%3A%2F%2Fsite.test'):
            for enabled, off_sites in ((False, []), (True, [SITE]), (True, [])):
                with self.subTest(suffix=suffix, enabled=enabled, off_sites=off_sites):
                    sess, driver, approvals = resign_session()
                    approvals.enabled, approvals.off_sites = enabled, set(off_sites)
                    url = base + suffix
                    if not enabled or off_sites:
                        with self.assertRaises(ToolError) as caught:
                            sess.run('browser_open', {'url': url, 'effect': 'navigate'})
                        self.assertEqual(caught.exception.code, 'provider_sign_in_off')
                        self.assertIs(caught.exception.ran, False)
                    else:
                        sess.run('browser_open', {'url': url, 'effect': 'navigate'})
                        self.assertEqual(driver.authorizations(), (1, (PROVIDER,)))

    def test_marks_set_before_the_provider_window_do_not_confirm_its_return(self):
        sess, driver, _ = resign_session('silent_no_session')
        open_login(sess)
        driver.cookies[SITE][0]['value'] = 'renewed-before-provider-navigation'
        page = sess.run('browser_click', {'target': '다른 계정으로 로그인', 'effect': 'navigate'})
        self.assertEqual(page['provider_sign_in']['state'], 'unconfirmed')

    def test_a_login_page_of_a_site_with_a_recorded_provider_tells_the_ai_how_to_sign_back_in(self):
        sess, _driver, approvals = resign_session()
        page = open_login(sess)
        self.assertEqual(page['state'], 'login_required')
        self.assertEqual(page['next_step'], msg('browser.provider_sign_in_hint', provider=PROVIDER))
        for words in ('layout: desktop', 'browser_sign_in', PROVIDER):
            self.assertIn(words, page['next_step'])
        self.assertEqual(page['provider_sign_in'], {'site': SITE, 'provider': PROVIDER, 'state': 'available'})
        # The in-flow login is still recorded, so a re-sign-in that fails leaves the owner's window (#709).
        self.assertEqual(approvals.offers, [(f'https://{SITE}/login', False)])

    def test_a_provider_that_returns_without_asking_is_recorded_as_returned_not_signed_in(self):
        sess, driver, approvals = resign_session('silent')
        open_login(sess)
        page = sess.run('browser_click', {'target': '다른 계정으로 로그인', 'effect': 'navigate'})
        self.assertEqual((page['state'], page['url']), ('page', f'https://{SITE}/home'))
        # #757: a new site cookie is no proof of a sign-in; the receipt says only what Still observed.
        self.assertEqual(page['provider_sign_in'], {'site': SITE, 'provider': PROVIDER, 'state': 'returned'})
        summary = evidence_summary('browser_click', page)
        self.assertEqual(summary['provider_sign_in'], {'site': SITE, 'provider': PROVIDER, 'state': 'returned'})
        # The AI reads the page to tell; it is told not to press the provider again when it is signed out.
        self.assertEqual(page['next_step'], msg('browser.provider_returned', provider=PROVIDER))
        self.assertIn('browser_sign_in', page['next_step'])
        # The Work's login request is left for the owner, never settled on the cookie alone.
        self.assertEqual(approvals.offers, [(f'https://{SITE}/login', False)])
        self.assertFalse(any(value in flat(summary) for value in QUERY_VALUES), 'no query value in the receipt')
        # The re-sign-in is over: a later page of the site carries no second receipt.
        later = sess.run('browser_click', {'target': '설정', 'effect': 'navigate'})
        self.assertNotIn('provider_sign_in', later)
        self.assertEqual([entry[0] for entry in driver.log if entry[0] == 'click'], ['click', 'click'])

    def test_an_ai_click_or_typing_on_a_provider_page_is_refused_and_nothing_is_sent(self):
        sess, driver, approvals = resign_session('chooser')
        open_login(sess)
        page = sess.run('browser_click', {'target': '다른 계정으로 로그인', 'effect': 'navigate'})
        self.assertEqual(urlsplit(page['url']).hostname, PROVIDER, 'the redirect through the provider is allowed')
        before = (list(driver.log), dict(driver.values), list(driver.posts))
        for action, args in (('browser_click', {'target': '계속', 'effect': 'navigate'}),
                             ('browser_type', {'target': '메모', 'text': 'x', 'effect': 'mutate'})):
            with self.assertRaises(ToolError) as caught:
                sess.run(action, args)
            self.assertEqual(caught.exception.code, bs.PROVIDER_INTERACTION_CODE)
            self.assertIs(getattr(caught.exception, 'ran', None), False)
            self.assertIn(PROVIDER, str(caught.exception))
            self.assertIn('OFFER-EXPLICIT', str(caught.exception), 'the owner is asked to sign in (C14)')
        self.assertEqual([entry for entry in driver.log if entry[0] in ('click', 'type')],
                         [entry for entry in before[0] if entry[0] in ('click', 'type')], 'nothing reached the page')
        self.assertEqual((driver.values, driver.posts), (before[1], before[2]))
        # The hand-off is the in-flow login of the site, asked however the Work ends.
        self.assertEqual(approvals.offers[-1], (f'https://{SITE}/login', True))
        # The effect rule (#618): a refusal before hand-off changed nothing.
        self.assertEqual(record_effect('browser_click', {'ran': False}, 'failed'), 'none')

    def test_a_provider_that_asks_for_a_password_ends_in_the_owner_hand_off(self):
        sess, driver, approvals = resign_session('password')
        open_login(sess)
        page = sess.run('browser_click', {'target': '다른 계정으로 로그인', 'effect': 'navigate'})
        self.assertEqual((page['state'], urlsplit(page['url']).hostname), ('login_required', PROVIDER))
        self.assertTrue(page['next_step'].startswith(msg('browser.provider_asks', provider=PROVIDER)))
        self.assertTrue(page['next_step'].endswith('OFFER-EXPLICIT'))
        self.assertNotIn('provider_sign_in', page, 'no second hint on the provider')
        self.assertEqual(approvals.offers[-1], (f'https://{SITE}/login', True))
        with self.assertRaises(ToolError) as caught:
            sess.run('browser_type', {'target': '아이디', 'text': 'someone', 'effect': 'mutate'})
        self.assertEqual(caught.exception.code, bs.PROVIDER_INTERACTION_CODE)
        self.assertFalse([entry for entry in driver.log if entry[0] == 'type'])

    def test_the_site_still_asking_after_the_provider_ends_in_the_owner_hand_off(self):
        sess, driver, approvals = resign_session('silent')
        open_login(sess)
        driver.pages = {**PAGES, f'{SITE}/home': PAGES[f'{SITE}/login']}   # the site did not take the sign-in
        page = sess.run('browser_click', {'target': '다른 계정으로 로그인', 'effect': 'navigate'})
        self.assertEqual(page['state'], 'login_required')
        self.assertTrue(page['next_step'].startswith(msg('browser.provider_not_returned', provider=PROVIDER)))
        self.assertEqual(approvals.offers[-1], (f'https://{SITE}/login', True))
        # The browser did come back through the provider (the owner's account was used): never a sign-in claim.
        self.assertEqual(page['provider_sign_in']['state'], 'returned')

    def assert_unconfirmed(self, page, approvals):
        self.assertEqual(page['provider_sign_in'], {'site': SITE, 'provider': PROVIDER, 'state': 'unconfirmed'})
        self.assertTrue(page['next_step'].startswith(msg('browser.provider_unconfirmed', provider=PROVIDER)))
        self.assertTrue(page['next_step'].endswith('OFFER-EXPLICIT'), 'the owner is asked to sign in')
        self.assertEqual(approvals.offers[-1], (f'https://{SITE}/login', True))
        self.assertEqual(evidence_summary('browser_click', page)['provider_sign_in']['state'], 'unconfirmed')

    def test_an_oauth_error_at_the_redirect_is_no_sign_in(self):
        sess, driver, approvals = resign_session('refused')
        open_login(sess)
        # The site even sets a cookie on the error page: the OAuth error still decides.
        before = driver.cookies_export()
        page = sess.run('browser_click', {'target': '다른 계정으로 로그인', 'effect': 'navigate'})
        self.assertNotEqual(driver.cookies_export(), before, 'the redirect set a new cookie')
        self.assertEqual(page['state'], 'page', 'no sign-in form on the error page')
        self.assert_unconfirmed(page, approvals)

    def test_returning_to_a_public_page_with_no_new_session_is_no_sign_in(self):
        sess, _driver, approvals = resign_session('silent_no_session')
        open_login(sess)
        page = sess.run('browser_click', {'target': '다른 계정으로 로그인', 'effect': 'navigate'})
        self.assertEqual(page['url'], f'https://{SITE}/home')
        self.assert_unconfirmed(page, approvals)
        # Decided once: a later page of the site carries no second receipt.
        self.assertNotIn('provider_sign_in', sess.run('browser_click', {'target': '설정', 'effect': 'navigate'}))

    def test_leaving_the_chooser_for_a_public_page_of_the_site_is_no_sign_in(self):
        sess, _driver, approvals = resign_session('chooser')
        open_login(sess)
        sess.run('browser_click', {'target': '다른 계정으로 로그인', 'effect': 'navigate'})
        page = sess.run('browser_open', {'url': f'https://{SITE}/', 'effect': 'navigate'})
        self.assert_unconfirmed(page, approvals)

    def test_a_browser_that_cannot_report_its_cookies_is_no_sign_in(self):
        sess, driver, approvals = resign_session('silent')
        driver.cookies_export = None   # unknown is not proof
        open_login(sess)
        page = sess.run('browser_click', {'target': '다른 계정으로 로그인', 'effect': 'navigate'})
        self.assert_unconfirmed(page, approvals)

    def test_a_federated_chain_refuses_interaction_on_the_broker_and_the_upstream(self):
        sess, driver, approvals = resign_session('federated', providers={SITE: [PROVIDER, UPSTREAM]})
        page = open_login(sess)
        self.assertEqual(page['next_step'], msg('browser.provider_sign_in_hint', provider=PROVIDER), 'names the first hop')
        self.assertEqual(page['provider_sign_in']['chain'], [PROVIDER, UPSTREAM])
        landed = sess.run('browser_click', {'target': '다른 계정으로 로그인', 'effect': 'navigate'})
        self.assertEqual(urlsplit(landed['url']).hostname, UPSTREAM, 'redirects through both are allowed')
        for url, target, host in ((None, '이 계정으로 계속', UPSTREAM), (f'https://{PROVIDER}/choose', '계속', PROVIDER)):
            if url:
                sess.run('browser_open', {'url': url, 'effect': 'navigate'})
            with self.assertRaises(ToolError) as caught:
                sess.run('browser_click', {'target': target, 'effect': 'navigate'})
            self.assertEqual(caught.exception.code, bs.PROVIDER_INTERACTION_CODE)
            self.assertIn(host, str(caught.exception))
        self.assertEqual(len([entry for entry in driver.log if entry[0] == 'click']), 1, 'only the site control was pressed')
        self.assertEqual(approvals.offers[-1], (f'https://{SITE}/login', True))

    def test_a_chain_with_a_hop_that_is_no_host_gets_no_hint(self):
        sess, _driver, _approvals = resign_session('chooser', providers={SITE: [PROVIDER, '']})
        page = open_login(sess)
        self.assertEqual(page['next_step'], 'OFFER')
        self.assertNotIn('provider_sign_in', page)

    # -- Codex P1 on #637: protection comes from navigation, never from the hint or a password form --
    def test_a_provider_only_sign_in_page_still_protects_the_providers_chooser(self):
        sess, driver, approvals = resign_session('chooser')
        start = sess.run('browser_open', {'url': f'https://{SITE}/start', 'effect': 'navigate'})
        self.assertEqual(start['state'], 'page', 'no password field: no sign-in page and no hint')
        self.assertNotIn('provider_sign_in', start)
        chooser = sess.run('browser_click', {'target': '다른 계정으로 계속', 'effect': 'navigate'})
        self.assertEqual(urlsplit(chooser['url']).hostname, PROVIDER)
        with self.assertRaises(ToolError) as caught:
            sess.run('browser_click', {'target': '계속', 'effect': 'navigate'})
        self.assertEqual(caught.exception.code, bs.PROVIDER_INTERACTION_CODE)
        self.assertIs(caught.exception.ran, False)
        self.assertEqual(len([entry for entry in driver.log if entry[0] == 'click']), 1, 'only the site link was pressed')
        self.assertEqual(approvals.offers[-1], (f'https://{SITE}/start', True), 'the owner signs in from where it began')

    def test_the_ai_opening_a_recorded_provider_host_directly_is_refused(self):
        for url in (AUTHORIZE, f'https://{PROVIDER}/choose'):
            sess, driver, _approvals = resign_session('chooser')
            page = sess.run('browser_open', {'url': url, 'effect': 'navigate'})
            self.assertEqual(urlsplit(page['url']).hostname, PROVIDER)
            for action, args in (('browser_click', {'target': '계속', 'effect': 'navigate'}),
                                 ('browser_type', {'target': '메모', 'text': 'x', 'effect': 'mutate'})):
                with self.assertRaises(ToolError) as caught:
                    sess.run(action, args)
                self.assertEqual(caught.exception.code, bs.PROVIDER_INTERACTION_CODE, url)
            self.assertFalse([entry for entry in driver.log if entry[0] in ('click', 'type')])

    def test_another_host_of_the_providers_domain_opened_directly_is_an_ordinary_page(self):
        sess, driver, _approvals = resign_session('chooser')
        sess.run('browser_open', {'url': 'https://search.provider.test/', 'effect': 'navigate'})
        sess.run('browser_type', {'target': '검색어', 'text': '날씨', 'effect': 'read'})
        sess.run('browser_click', {'target': '검색하기', 'effect': 'read'})
        self.assertEqual([entry[0] for entry in driver.log if entry[0] in ('click', 'type')], ['type', 'click'])

    def test_a_protected_open_redirecting_to_an_unrecorded_host_keeps_the_window(self):
        for prior in (None, f'https://{SITE}/start'):
            with self.subTest(prior=prior):
                sess, driver, _approvals = resign_session('chooser', providers={SITE: PROVIDER})
                driver.pages = {**driver.pages, f'{UPSTREAM}/choose': PAGES[f'{PROVIDER}/choose']}
                goto = driver.goto

                def redirect(url, timeout):
                    goto(url, timeout)
                    if url == f'https://{PROVIDER}/choose':
                        goto(f'https://{UPSTREAM}/choose', timeout)

                driver.goto = redirect
                if prior:
                    sess.run('browser_open', {'url': prior, 'effect': 'navigate'})
                page = sess.run('browser_open', {'url': f'https://{PROVIDER}/choose', 'effect': 'navigate'})
                self.assertEqual(urlsplit(page['url']).hostname, UPSTREAM)
                self.assertEqual(driver.authorizations(), (0, ()), 'no authorization parameters on either hop')
                self.assertNotIn(UPSTREAM, sess._protected_hosts())
                for action, args in (('browser_click', {'target': '계속', 'effect': 'navigate'}),
                                     ('browser_type', {'target': '메모', 'text': 'x', 'effect': 'mutate'})):
                    with self.assertRaises(ToolError) as caught:
                        sess.run(action, args)
                    self.assertEqual(caught.exception.code, bs.PROVIDER_INTERACTION_CODE)
                    self.assertIs(caught.exception.ran, False)
                self.assertFalse([entry for entry in driver.log if entry[0] in ('click', 'type')])
                self.assertEqual((sess._window['origin'] or {}).get('site'), SITE if prior else None)

    def test_a_protected_open_preserves_the_prior_site_without_authorization_parameters(self):
        sess, driver, _approvals = resign_session('chooser')
        sess.run('browser_open', {'url': f'https://{SITE}/start', 'effect': 'navigate'})
        sess.run('browser_open', {'url': f'https://{PROVIDER}/choose', 'effect': 'navigate'})
        self.assertEqual(sess._window['origin']['site'], SITE)
        driver.goto(f'https://{SITE}/callback?code=abc', 1)
        page = sess.run('browser_read', {})
        self.assertIsNone(sess._window)
        self.assertEqual(page['provider_sign_in']['state'], 'returned')
        sess.run('browser_click', {'target': '설정', 'effect': 'navigate'})

    def test_an_unknown_origin_does_not_clear_on_a_silent_return(self):
        base = f'https://{PROVIDER}/authorize?response_type=code&client_id={CLIENT}'
        for suffix in ('', '&redirect_uri=invalid', '&redirect_uri=https%3A%2F%2Fsite.test%2F&redirect_uri=other',
                       '&redirect_uri=https%3A%2F%2Fsite.test%3Abad%2F'):
            with self.subTest(suffix=suffix):
                sess, _driver, _approvals = resign_session('silent')
                page = sess.run('browser_open', {'url': base + suffix, 'effect': 'navigate'})
                self.assertIsNone(sess._window['origin'])
                self.assertNotIn('provider_sign_in', page)
                with self.assertRaises(ToolError) as caught:
                    sess.run('browser_click', {'target': '설정', 'effect': 'navigate'})
                self.assertEqual(caught.exception.code, bs.PROVIDER_INTERACTION_CODE)
                sess.run('browser_open', {'url': 'https://public.test/', 'effect': 'navigate'})
                self.assertIsNone(sess._window, 'the existing unrelated-open rule still clears the window')

    def test_the_window_covers_pages_reached_from_the_provider_and_clears_back_on_the_site(self):
        # Only the broker is recorded: its upstream's chooser is reached from it by redirect, so it is inside.
        sess, driver, _approvals = resign_session('federated', providers={SITE: PROVIDER})
        sess.run('browser_open', {'url': f'https://{SITE}/start', 'effect': 'navigate'})
        upstream = sess.run('browser_click', {'target': '다른 계정으로 계속', 'effect': 'navigate'})
        self.assertEqual(urlsplit(upstream['url']).hostname, UPSTREAM)
        with self.assertRaises(ToolError) as caught:
            sess.run('browser_click', {'target': '이 계정으로 계속', 'effect': 'navigate'})
        self.assertEqual(caught.exception.code, bs.PROVIDER_INTERACTION_CODE)
        self.assertIn(UPSTREAM, str(caught.exception))
        # A provider that returns to the site at once: back on the site, the window is over.
        sess, driver, _approvals = resign_session('silent', providers={SITE: PROVIDER})
        sess.run('browser_open', {'url': f'https://{SITE}/start', 'effect': 'navigate'})
        home = sess.run('browser_click', {'target': '다른 계정으로 계속', 'effect': 'navigate'})
        self.assertEqual(home['url'], f'https://{SITE}/home')
        sess.run('browser_click', {'target': '설정', 'effect': 'navigate'})
        self.assertEqual(len([entry for entry in driver.log if entry[0] == 'click']), 2, 'the site is free again')

    # -- Codex P1/P2 on #637: one window state machine (origin, hosts visited inside) --
    def test_reopening_the_chooser_keeps_the_protection(self):
        sess, driver, _approvals = resign_session('federated', providers={SITE: PROVIDER})
        sess.run('browser_open', {'url': f'https://{SITE}/start', 'effect': 'navigate'})
        sess.run('browser_click', {'target': '다른 계정으로 계속', 'effect': 'navigate'})
        reopened = sess.run('browser_open', {'url': f'https://{UPSTREAM}/choose', 'effect': 'navigate'})
        self.assertEqual(urlsplit(reopened['url']).hostname, UPSTREAM)
        for action, args in (('browser_click', {'target': '이 계정으로 계속', 'effect': 'navigate'}),):
            with self.assertRaises(ToolError) as caught:
                sess.run(action, args)
            self.assertEqual(caught.exception.code, bs.PROVIDER_INTERACTION_CODE)
        self.assertEqual(len([entry for entry in driver.log if entry[0] == 'click']), 1, 'only the site link was pressed')

    def test_an_explicit_open_that_redirects_to_the_provider_keeps_the_site_as_its_origin(self):
        sess, driver, approvals = resign_session('chooser')
        chooser = sess.run('browser_open', {'url': f'https://{SITE}/signin-redirect', 'effect': 'navigate'})
        self.assertEqual(urlsplit(chooser['url']).hostname, PROVIDER)
        with self.assertRaises(ToolError):
            sess.run('browser_click', {'target': '계속', 'effect': 'navigate'})
        # The hand-off names the site the open asked for, not the provider's page.
        self.assertEqual(approvals.offers[-1], (f'https://{SITE}/signin-redirect', True))
        # The provider later returns the main frame to the site (its own redirect): the window is over.
        driver.url = f'https://{SITE}/home'
        sess.run('browser_read', {})
        sess.run('browser_click', {'target': '설정', 'effect': 'navigate'})
        self.assertEqual([entry[0] for entry in driver.log if entry[0] == 'click'], ['click'], "the site's controls work")

    def test_an_explicit_open_of_an_unrelated_site_ends_the_window(self):
        sess, driver, _approvals = resign_session('chooser')
        open_login(sess)
        sess.run('browser_click', {'target': '다른 계정으로 로그인', 'effect': 'navigate'})
        with self.assertRaises(ToolError):
            sess.run('browser_click', {'target': '계속', 'effect': 'navigate'})
        sess.run('browser_open', {'url': 'https://public.test/', 'effect': 'navigate'})
        sess.run('browser_click', {'target': '더 보기', 'effect': 'read'})
        self.assertEqual(len([entry for entry in driver.log if entry[0] == 'click']), 2)

    def test_an_unrecorded_provider_gets_no_hint_and_no_special_path(self):
        for approvals_providers in ({}, None):
            sess, driver, approvals = resign_session('chooser', providers={})
            if approvals_providers is None:
                sess.approvals = Approvals()   # an approvals surface without the provider lookup
                sess.approvals.login_required = lambda url, explicit=False, *, window_url=None: 'OFFER'
            page = open_login(sess)
            self.assertEqual(page['next_step'], 'OFFER')
            self.assertNotIn('provider_sign_in', page)
            sess.run('browser_click', {'target': '다른 계정으로 로그인', 'effect': 'navigate'})
            # No re-sign-in began, so the provider's pages are ordinary pages (the existing guards apply).
            sess.run('browser_type', {'target': '메모', 'text': 'x', 'effect': 'mutate'})
            self.assertEqual([entry[2] for entry in driver.log if entry[0] == 'type'], ['x'])


class ResignInLoopTests(unittest.TestCase):
    """The loop end to end with a scripted model: the receipt, the audit and the refused step's record."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = QuickStore(Path(self.tmp.name) / 'data')
        self.job = self.store.enqueue('주문 내역 확인해줘', 'web-636')
        self.events = []

    def record(self, tool, status, detail):
        self.events.append((tool, status, detail))
        with self.store.db() as db:
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (self.job, tool, status, detail if isinstance(detail, str) else json.dumps(detail), time.time()))

    def run_work(self, answer, *steps):
        driver = TwoHostDriver(answer)
        caps = Capabilities(self.store, ModelAdapter(Script(*steps)), CFG, '', self.job, self.record,
                            browser=lambda: driver, browser_approvals=ProviderApprovals({SITE: PROVIDER}))
        try:
            run_agent(caps.adapter, CFG, '', [{'role': 'user', 'content': '주문 내역 확인해줘'}], '', caps, self.record)
        finally:
            caps.close_browser()
        return driver

    def details(self, tool):
        return [(status, json.loads(detail)) for name, status, detail in self.events if name == tool
                and status in ('succeeded', 'failed')]

    def record_result(self, action, page):
        self.record(action, 'succeeded', {'evidence': evidence_summary(action, page)})

    def test_popup_receipt_reaches_information_use_without_claiming_sign_in(self):
        sess, driver, _ = resign_session('silent')
        sess.run('browser_open', {'url': f'https://{SITE}/start', 'effect': 'navigate'})
        driver.navigate(AUTHORIZE)
        driver.goto(f'https://{SITE}/callback', 1)
        sess._popup_results = [{'origin': f'https://{SITE}/start', 'hosts': [PROVIDER],
                                'needs_owner': False, 'oauth_error': False}]
        page = sess.run('browser_read', {})
        self.record_result('browser_read', page)
        audit = information_use.work_information_use(self.store, self.job)
        self.assertEqual(audit['sent_to']['browser_provider_sign_ins'][0]['site'], SITE)
        self.assertIn(PROVIDER, information_use.render_korean(audit))
        self.assertEqual(page['provider_sign_in']['state'], 'returned')

    def test_provider_hops_accumulate_across_observations_and_reach_the_audit(self):
        sess, driver, _ = resign_session('chooser', providers={SITE: PROVIDER, 'other.test': UPSTREAM})
        # No hint: the chain must come from the observed authorization requests.
        sess.run('browser_open', {'url': f'https://{SITE}/start', 'effect': 'navigate'})
        sess.run('browser_click', {'target': '다른 계정으로 계속', 'effect': 'navigate'})
        driver.goto(UPSTREAM_AUTHORIZE, 1)
        sess.run('browser_read', {})
        # Repeated observations of the upstream must not duplicate it.
        driver.goto(UPSTREAM_AUTHORIZE, 1)
        sess.run('browser_read', {})
        driver.goto(f'https://{SITE}/callback?code=abc', 1)
        page = sess.run('browser_read', {})
        self.assertEqual(page['provider_sign_in']['state'], 'returned')
        self.assertEqual(page['provider_sign_in']['chain'], [PROVIDER, UPSTREAM])
        self.assertIsNone(sess._resign)
        self.record_result('browser_read', page)
        audit = information_use.work_information_use(self.store, self.job)
        self.assertEqual(audit['sent_to']['browser_provider_sign_ins'],
                         [{'site': SITE, 'provider': PROVIDER, 'chain': [PROVIDER, UPSTREAM]}])
        self.assertIn(PROVIDER + ' → ' + UPSTREAM, information_use.render_korean(audit))
        self.assertNotIn('provider_sign_in', sess.run('browser_read', {}))

    def test_pre_click_return_is_finalized_before_clicking_away_and_reaches_the_audit(self):
        sess, driver, approvals = resign_session('chooser')
        open_login(sess)
        sess.run('browser_click', {'target': '다른 계정으로 로그인', 'effect': 'navigate'})
        driver.pages = {**PAGES, f'{SITE}/home': '<a href="https://public.test/">뉴스</a>'}
        # The provider returns between tools; only the pre-click observation sees the site.
        driver.goto(f'https://{SITE}/callback?code=abc', 1)
        page = sess.run('browser_click', {'target': '뉴스', 'effect': 'navigate'})
        self.assertEqual(page['url'], 'https://public.test/')
        self.assertEqual(page['provider_sign_in'], {'site': SITE, 'provider': PROVIDER, 'state': 'returned'})
        self.assertIsNone(sess._resign)
        self.record_result('browser_click', page)
        audit = information_use.work_information_use(self.store, self.job)
        self.assertEqual(audit['sent_to']['browser_provider_sign_ins'], [{'site': SITE, 'provider': PROVIDER}])
        self.assertIn(PROVIDER, information_use.render_korean(audit))
        self.assertNotIn('provider_sign_in', sess.run('browser_read', {}))

    def test_pre_click_completion_survives_a_failed_action_until_the_next_result(self):
        sess, driver, approvals = resign_session('chooser')
        open_login(sess)
        sess.run('browser_click', {'target': '다른 계정으로 로그인', 'effect': 'navigate'})
        driver.goto(f'https://{SITE}/callback?code=abc', 1)
        with self.assertRaises(ToolError):
            sess.run('browser_click', {'target': '존재하지 않는 링크', 'effect': 'navigate'})
        self.assertIsNone(sess._resign)
        # Delivery must use the evidence at closure, even if the session has since disappeared.
        driver.cookies[SITE] = []
        page = sess.run('browser_read', {})
        self.assertEqual(page['provider_sign_in']['state'], 'returned')
        self.assertNotIn('provider_sign_in', sess.run('browser_read', {}))

    def test_explicit_open_elsewhere_finalizes_with_origin_session_evidence_only(self):
        for renewed in (False, True):
            with self.subTest(renewed=renewed):
                sess, driver, _ = resign_session('chooser')
                open_login(sess)
                sess.run('browser_click', {'target': '다른 계정으로 로그인', 'effect': 'navigate'})
                if renewed:
                    driver.cookies[SITE][0]['value'] = 'renewed-during-provider-window'
                page = sess.run('browser_open', {'url': 'https://public.test/', 'effect': 'navigate'})
                self.assertEqual(page['provider_sign_in']['state'], 'returned' if renewed else 'unconfirmed')
                self.assertIsNone(sess._resign)
                self.assertNotIn('provider_sign_in', sess.run('browser_read', {}))

    def test_provider_only_page_records_the_return_and_information_use(self):
        self.run_work('silent',
                      {'tool_calls': [call('1', 'browser_open', url=f'https://{SITE}/start', effect='navigate')]},
                      {'tool_calls': [call('2', 'browser_click', target='다른 계정으로 계속', effect='navigate')]},
                      {'content': '확인했습니다.'})
        [(_status, detail)] = self.details('browser_click')
        self.assertEqual(detail['evidence']['provider_sign_in']['state'], 'returned')
        audit = information_use.work_information_use(self.store, self.job)
        self.assertEqual(audit['sent_to']['browser_provider_sign_ins'], [{'site': SITE, 'provider': PROVIDER}])
        self.assertIn(msg('information_use.browser_provider_sign_in', site=SITE, provider=PROVIDER),
                      information_use.render_korean(audit))

    def assert_explicit_authorization_return(self, prior_page):
        steps = []
        if prior_page:
            steps.append({'tool_calls': [call('start', 'browser_open', url=prior_page, effect='navigate')]})
        driver = self.run_work('silent', *steps,
                               {'tool_calls': [call('authorize', 'browser_open', url=AUTHORIZE, effect='navigate')]},
                               {'tool_calls': [call('settings', 'browser_click', target='설정', effect='navigate')]},
                               {'content': '확인했습니다.'})
        status, detail = self.details('browser_open')[-1]
        self.assertEqual(status, 'succeeded')
        self.assertEqual(detail['evidence']['provider_sign_in']['state'], 'returned')
        [(status, _detail)] = self.details('browser_click')
        self.assertEqual(status, 'succeeded', 'the returned site is outside the provider window')
        self.assertEqual(driver.url, f'https://{SITE}/settings')
        audit = information_use.work_information_use(self.store, self.job)
        self.assertEqual(audit['sent_to']['browser_provider_sign_ins'], [{'site': SITE, 'provider': PROVIDER}])
        self.assertIn(msg('information_use.browser_provider_sign_in', site=SITE, provider=PROVIDER),
                      information_use.render_korean(audit))
        self.assertFalse(any(value in flat(audit) for value in QUERY_VALUES))

    def test_explicit_authorization_open_preserves_the_prior_site(self):
        self.assert_explicit_authorization_return(f'https://{SITE}/start')

    def test_explicit_authorization_open_without_a_prior_page_uses_the_redirect_site(self):
        self.assert_explicit_authorization_return(None)

    def test_provider_only_page_without_new_marks_is_unconfirmed(self):
        self.run_work('silent_no_session',
                      {'tool_calls': [call('1', 'browser_open', url=f'https://{SITE}/start', effect='navigate')]},
                      {'tool_calls': [call('2', 'browser_click', target='다른 계정으로 계속', effect='navigate')]},
                      {'content': '로그인이 필요합니다.'})
        [(_status, detail)] = self.details('browser_click')
        self.assertEqual(detail['evidence']['provider_sign_in']['state'], 'unconfirmed')
        self.assertNotIn('browser_provider_sign_ins', information_use.work_information_use(self.store, self.job)['sent_to'])

    def test_the_re_sign_in_is_in_the_receipt_and_the_information_use_audit(self):
        self.run_work('silent',
                      {'tool_calls': [call('1', 'browser_open', url=f'https://{SITE}/login', effect='navigate')]},
                      {'tool_calls': [call('2', 'browser_click', target='다른 계정으로 로그인', effect='navigate')]},
                      {'content': '주문 내역을 확인했습니다.'})
        [(status, detail)] = self.details('browser_click')
        self.assertEqual(status, 'succeeded')
        self.assertEqual(detail['evidence']['provider_sign_in'], {'site': SITE, 'provider': PROVIDER, 'state': 'returned'})
        audit = information_use.work_information_use(self.store, self.job)
        self.assertEqual(audit['sent_to']['browser_provider_sign_ins'], [{'site': SITE, 'provider': PROVIDER}])
        self.assertIn(msg('information_use.browser_provider_sign_in', site=SITE, provider=PROVIDER),
                      information_use.render_korean(audit))
        self.assertFalse(any(value in json.dumps(audit, ensure_ascii=False) for value in (CLIENT, STATE, NONCE)))

    def test_an_unconfirmed_re_sign_in_is_no_success_in_the_receipt_or_the_audit(self):
        self.run_work('refused',
                      {'tool_calls': [call('1', 'browser_open', url=f'https://{SITE}/login', effect='navigate')]},
                      {'tool_calls': [call('2', 'browser_click', target='다른 계정으로 로그인', effect='navigate')]},
                      {'content': '로그인이 필요합니다.'})
        [(_status, detail)] = self.details('browser_click')
        self.assertEqual(detail['evidence']['provider_sign_in']['state'], 'unconfirmed')
        audit = information_use.work_information_use(self.store, self.job)
        self.assertNotIn('browser_provider_sign_ins', audit['sent_to'])
        self.assertNotIn(msg('information_use.browser_provider_sign_in', site=SITE, provider=PROVIDER),
                         information_use.render_korean(audit))

    def test_a_refused_provider_step_is_recorded_as_not_run(self):
        driver = self.run_work('chooser',
                               {'tool_calls': [call('1', 'browser_open', url=f'https://{SITE}/login', effect='navigate')]},
                               {'tool_calls': [call('2', 'browser_click', target='다른 계정으로 로그인', effect='navigate')]},
                               {'tool_calls': [call('3', 'browser_click', target='계속', effect='navigate')]},
                               {'content': '로그인이 필요합니다.'})
        [first, refused] = self.details('browser_click')
        self.assertEqual(first[0], 'succeeded')
        self.assertEqual(refused[0], 'failed')
        self.assertIs(refused[1].get('ran'), False)
        self.assertEqual(record_effect('browser_click', refused[1], 'failed'), 'none')
        self.assertEqual(driver.posts, [], 'the provider received no press')
        audit = information_use.work_information_use(self.store, self.job)
        self.assertNotIn('browser_provider_sign_ins', audit['sent_to'], 'no re-sign-in is claimed')


class AuthJarDriver(JarDriver):
    """The login window's fake driver: its navigations go through the worker's authorization record."""

    def __init__(self, log=None):
        super().__init__(log=log)
        self.worker = types.SimpleNamespace(authorizations=[], authorization_count=0)

    def navigate(self, url):
        """A main-frame navigation the page made (the owner's press, the provider's redirect)."""
        self.navigated += 1
        browser_worker.Worker.note_authorization(self.worker, url)

    def authorizations(self):
        return self.worker.authorization_count, tuple(self.worker.authorizations)


class ProviderRecordTests(LoginHarness):
    """Only the owner's own login window records a provider; the hint needs its session and the switch."""

    def setUp(self):
        super().setUp()
        self.ai_navigates = None

        def launcher(profile_dir, headless):
            driver = AuthJarDriver(log=self.driver_log)
            if not self.drivers and self.ai_navigates:
                driver.navigate(self.ai_navigates)   # the Work's own browser, not an owner window
            if self.drivers:
                for name, value in self.window_holds.items():
                    setattr(driver, name, value)
            self.drivers.append(driver)
            return driver
        self.profile.launcher = launcher

    def providers(self):
        return self.store.config(BROWSER_PROVIDERS_KEY, {})

    def provider_session(self, expires=None):
        self.profile.jar.save_export({'provider.test': [{'name': 'idp', 'value': 'v-' + str(time.time_ns()), 'domain': '.provider.test',
                                                         'path': '/', 'expires': expires, 'secure': True, 'http_only': True,
                                                         'same_site': None}]}, imported=())

    def settings_window_sign_in(self, through=AUTHORIZE):
        shown = len(self.drivers)
        receipt = self.service.open_browser_for_login({'url': ORIGIN + '/login'})
        self.assertEqual(receipt['state'], 'opening')
        self.assertTrue(wait_until(lambda: len(self.drivers) > shown and self.drivers[-1].url is not None))
        self.assertTrue(wait_until(lambda: self.service.browser_status()['settings_login']['state'] == 'opened'))
        window = self.drivers[-1]
        if through:
            window.navigate(through)                      # the owner pressed the site's provider control
        window.navigate('https://fixture.test/callback')  # the provider returned to the site
        window.login()
        window.closed = True
        self.assertTrue(wait_until(lambda: self.service.browser_status()['settings_login']['state'] == 'closed'))

    def test_owner_login_window_is_unaffected_by_switch_off(self):
        self.service.set_provider_signin('off')
        approvals = self.service.browser_approvals_for({'id': 'w', 'channel': 'web-636'})
        sess, driver, _ = resign_session()
        self.store.put(BROWSER_PROVIDERS_KEY, {SITE: {'provider': PROVIDER}})
        sess.approvals = approvals
        sess.run('browser_open', {'url': f'https://{SITE}/start', 'effect': 'navigate'})
        before = driver.cookies_export()
        with self.assertRaises(ToolError) as caught:
            sess.run('browser_click', {'target': '다른 계정으로 계속', 'effect': 'navigate'})
        self.assertEqual(caught.exception.code, 'provider_sign_in_off')
        self.assertEqual(driver.cookies_export(), before)
        self.settings_window_sign_in()
        self.assertEqual(self.providers()['fixture.test']['provider'], PROVIDER)
        self.assertEqual(self.drivers[-1].authorizations(), (1, (PROVIDER,)))

    def test_the_owners_settings_login_window_records_the_provider_host_and_no_query_value(self):
        self.settings_window_sign_in()
        record = self.providers()['fixture.test']
        self.assertEqual((record['provider'], record['observed_in']), (PROVIDER, 'owner_login_window'))
        self.assertLessEqual(abs(record['observed_at'] - time.time()), 60)
        with self.store.db() as db:
            stored = '\n'.join(db.iterdump())
        self.assertFalse(any(value in stored for value in QUERY_VALUES),
                         'no query value, decoded redirect endpoint or nested query value in any stored record')
        # The owner's own window is the owner, not the AI: nothing was refused or asked there.
        self.assertEqual(self.drivers[-1].posts, [])

    def test_a_later_owner_sign_in_without_a_provider_forgets_it(self):
        self.settings_window_sign_in()
        self.assertIn('fixture.test', self.providers())
        self.settings_window_sign_in(through=None)
        self.assertNotIn('fixture.test', self.providers())

    def test_the_in_flow_login_window_records_it_and_the_ais_own_navigations_never_do(self):
        # The AI's own browser sent an authorization request during its run: never a record.
        self.ai_navigates = AUTHORIZE
        job_id, _prompt, _buttons, _notification = self.login_work()
        self.assertEqual(self.drivers[0].authorizations(), (1, (PROVIDER,)))
        self.assertEqual(self.providers(), {}, "the AI's navigation recorded nothing")
        self.owner_closes(job_id)   # the owner signed in by hand, with no provider
        self.assertEqual(self.state(job_id), 'resumed')
        self.assertEqual(self.providers(), {}, 'only the owner window, and it used no provider')

    def test_the_in_flow_owner_window_through_a_provider_records_it(self):
        job_id, _prompt, _buttons, _notification = self.login_work()
        self.window().navigate(AUTHORIZE)
        self.owner_closes(job_id)
        self.assertEqual(self.state(job_id), 'resumed')
        self.assertEqual(self.providers()['fixture.test']['provider'], PROVIDER)

    def test_the_hint_needs_a_recorded_provider_its_session_and_the_switch_on(self):
        url = ORIGIN + '/login'
        self.assertIsNone(self.service.browser_signin_provider(url), 'no recorded provider: no hint')
        self.settings_window_sign_in()
        self.assertIsNone(self.service.browser_signin_provider(url), 'a recorded provider with no session: no hint')
        self.provider_session(expires=time.time() - 10)
        self.assertIsNone(self.service.browser_signin_provider(url), 'an expired provider session is no session')
        self.provider_session()
        found = {'site': 'fixture.test', 'provider': PROVIDER, 'chain': [PROVIDER]}
        self.assertEqual(self.service.browser_signin_provider(url), found)
        self.assertEqual(self.service.browser_approvals_for({'id': 'w', 'channel': 'web-636'}).provider_sign_in(url), found)
        self.service.set_provider_signin('off')
        self.assertIsNone(self.service.browser_signin_provider(url), 'off for every site')
        self.service.set_provider_signin('on')
        self.service.set_provider_signin('fixture.test|off')
        self.assertIsNone(self.service.browser_signin_provider(url), 'off for this site')
        self.service.set_provider_signin('fixture.test|on')
        self.assertIsNotNone(self.service.browser_signin_provider(url))
        # Forgetting the site's session forgets its provider record too.
        self.service.delete_browser_sessions({'site': 'fixture.test'})
        self.assertNotIn('fixture.test', self.providers())

    def session_for(self, site):
        self.profile.jar.save_export({site: [{'name': 'idp', 'value': 'v-' + str(time.time_ns()), 'domain': '.' + site,
                                              'path': '/', 'expires': None, 'secure': True, 'http_only': True,
                                              'same_site': None}]}, imported=())   # added beside the stored sites

    def test_a_federated_sign_in_records_every_hop_and_the_hint_needs_each_session(self):
        self.settings_window_sign_in(through=AUTHORIZE)
        window = self.drivers[-1]
        self.assertEqual(window.authorizations()[0], 1)
        # Again, through the broker and its upstream (each hop's authorization request).
        shown = len(self.drivers)
        self.assertEqual(self.service.open_browser_for_login({'url': ORIGIN + '/login'})['state'], 'opening')
        self.assertTrue(wait_until(lambda: len(self.drivers) > shown and self.drivers[-1].url is not None))
        self.assertTrue(wait_until(lambda: self.service.browser_status()['settings_login']['state'] == 'opened'))
        window = self.drivers[-1]
        for url in (AUTHORIZE, UPSTREAM_AUTHORIZE, 'https://fixture.test/callback'):
            window.navigate(url)
        window.login()
        window.closed = True
        self.assertTrue(wait_until(lambda: self.providers().get('fixture.test', {}).get('chain') == [PROVIDER, UPSTREAM]))
        record = self.providers()['fixture.test']
        self.assertEqual((record['provider'], record['complete']), (PROVIDER, True))
        self.assertFalse(any(value in json.dumps(record) for value in QUERY_VALUES + ('broker-client-6364', 'up-state-6365')))
        url = ORIGIN + '/login'
        self.provider_session()
        self.assertIsNone(self.service.browser_signin_provider(url), "the upstream's session is not held: no hint")
        self.session_for('upstream.test')
        self.assertEqual(self.service.browser_signin_provider(url),
                         {'site': 'fixture.test', 'provider': PROVIDER, 'chain': [PROVIDER, UPSTREAM]})
        self.assertIn(f'{PROVIDER} → {UPSTREAM}', self.service.provider_signin_settings()['sites']['fixture.test'])

    def test_the_protected_hosts_are_every_recorded_hop_whatever_the_switch(self):
        self.store.put(BROWSER_PROVIDERS_KEY, {
            'fixture.test': {'provider': PROVIDER, 'chain': [PROVIDER, UPSTREAM], 'complete': False},
            'other.test': {'provider': 'old.idp.test'}})
        self.service.set_provider_signin('off')
        hosts = self.service.browser_approvals_for({'id': 'w', 'channel': 'web-636'}).provider_hosts()
        self.assertEqual(hosts, frozenset({PROVIDER, UPSTREAM, 'old.idp.test'}))

    def test_an_old_single_provider_record_is_a_one_host_chain(self):
        self.store.put(BROWSER_PROVIDERS_KEY, {'fixture.test': {'provider': PROVIDER, 'observed_at': time.time(),
                                                                'observed_in': 'owner_login_window'}})
        self.provider_session()
        self.assertEqual(self.service.browser_signin_provider(ORIGIN + '/login'),
                         {'site': 'fixture.test', 'provider': PROVIDER, 'chain': [PROVIDER]})

    def test_a_chain_the_window_could_not_keep_whole_gets_no_hint(self):
        # More authorization requests than the worker keeps: an early hop may be missing.
        shown = len(self.drivers)
        self.assertEqual(self.service.open_browser_for_login({'url': ORIGIN + '/login'})['state'], 'opening')
        self.assertTrue(wait_until(lambda: len(self.drivers) > shown and self.drivers[-1].url is not None))
        self.assertTrue(wait_until(lambda: self.service.browser_status()['settings_login']['state'] == 'opened'))
        window = self.drivers[-1]
        for _ in range(browser_worker.AUTHORIZATIONS_KEPT + 1):
            window.navigate(AUTHORIZE)
        window.login()
        window.closed = True
        self.assertTrue(wait_until(lambda: 'fixture.test' in self.providers()))
        self.assertIs(self.providers()['fixture.test']['complete'], False)
        self.provider_session()
        self.assertIsNone(self.service.browser_signin_provider(ORIGIN + '/login'), 'unsure which hosts to protect: no hint')
        # A stored chain with a hop that is no host is no more reliable.
        self.store.put(BROWSER_PROVIDERS_KEY, {'fixture.test': {'provider': PROVIDER, 'chain': [PROVIDER, None],
                                                                'complete': True}})
        self.assertIsNone(self.service.browser_signin_provider(ORIGIN + '/login'))

    def test_the_switch_is_on_by_default_and_the_owner_turns_it_off_in_the_conversation(self):
        self.assertTrue(self.service.provider_signin_settings()['enabled'])
        self.settings_window_sign_in()
        self.provider_session()
        job = self.store.job(self.receive('다른 계정으로 다시 로그인하는 거 꺼줘'))
        settings = self.service.settings_orchestrator
        read = settings.read(self.service.settings_owner(job), 'browser')
        row = read['settings']['browser']['provider_sign_in']
        self.assertEqual(row['value'], 'on')
        self.assertEqual([option['value'] for option in row['options']],
                         ['on', 'off', 'fixture.test|on', 'fixture.test|off'])
        self.assertIn(PROVIDER, row['value_label'])
        for value, check in (('fixture.test|off', lambda current: current['off_sites'] == ['fixture.test']),
                             ('off', lambda current: current['enabled'] is False)):
            draft = settings.propose(self.service.settings_owner(job), job['channel'], 'browser', 'provider_sign_in', value)
            pending = settings._drafts()[draft['draft_id']]
            self.assertTrue(self.service.provider_signin_settings()['enabled'] or value != 'off', 'nothing applies before the owner confirms')
            result = settings.confirm(self.service.settings_owner(job), job['channel'], draft['draft_id'], pending['digest'])
            self.assertEqual(result['state'], 'applied')
            self.assertTrue(check(self.service.provider_signin_settings()))
        self.assertEqual(self.store.config(BROWSER_PROVIDER_SIGNIN_KEY, {}), {'enabled': False, 'off_sites': ['fixture.test']})
        self.assertIsNone(self.service.browser_signin_provider(ORIGIN + '/login'))


class ReturnedLoginTests(LoginHarness):
    """#757: a return through the provider is not a sign-in: the Work's login request stays for the owner."""

    def setUp(self):
        super().setUp()
        self.answer = 'silent'

        def launcher(profile_dir, headless):
            driver = TwoHostDriver(self.answer, log=self.driver_log)
            self.drivers.append(driver)
            return driver
        self.profile.launcher = launcher
        self.store.put(BROWSER_PROVIDERS_KEY, {SITE: {'provider': PROVIDER, 'observed_at': time.time(),
                                                      'observed_in': 'owner_login_window'}})
        self.profile.jar.save_export({'provider.test': [{'name': 'idp', 'value': 'v1', 'domain': '.provider.test', 'path': '/',
                                                         'expires': None, 'secure': True, 'http_only': True,
                                                         'same_site': None}]})

    def resign_work(self):
        self.scripts = [Script({'tool_calls': [call('1', 'browser_open', url=f'https://{SITE}/login', effect='navigate')]},
                               {'tool_calls': [call('2', 'browser_click', target='다른 계정으로 로그인', effect='navigate')]},
                               {'content': '주문 내역을 확인했습니다.'})]
        job_id = self.receive('주문 내역 확인해줘')
        self.assertTrue(self.service.run_one())
        return job_id

    def receipt(self, job_id):
        with self.store.db() as db:
            rows = db.execute("SELECT detail FROM tool_events WHERE job_id=? AND tool='browser_click' AND status='succeeded'",
                              (job_id,)).fetchall()
        return [json.loads(row['detail'])['evidence'].get('provider_sign_in') for row in rows]

    def test_a_returned_re_sign_in_leaves_the_login_request_to_the_work_outcome(self):
        job_id = self.resign_work()
        self.assertEqual(self.receipt(job_id), [{'site': SITE, 'provider': PROVIDER, 'state': 'returned'}])
        # The request the sign-in page made is neither settled nor turned into an explicit ask: a Work
        # that did not succeed shows it to the owner (#752), as for any sign-in page met in passing.
        self.assertNotEqual(self.store.job(job_id)['status'], 'succeeded')
        self.assertTrue(wait_until(lambda: self.state(job_id) in ('opening', 'offered')), self.state(job_id))
        self.assertFalse(self.service._browser_login(job_id).get('explicit'))
        self.assertFalse(self.service.sign_in_requested(job_id))
        self.assertTrue(wait_until(lambda: len(self.drivers) == 2), 'the owner login window was opened')

    def test_an_unconfirmed_re_sign_in_keeps_the_request_and_the_owner_is_asked(self):
        self.answer = 'silent_no_session'
        job_id = self.resign_work()
        self.assertEqual(self.receipt(job_id)[0]['state'], 'unconfirmed')
        self.assertTrue(wait_until(lambda: self.state(job_id) in ('opening', 'offered')), self.state(job_id))
        self.assertTrue(self.service._browser_login(job_id)['explicit'], 'asked however the Work ends')
        self.assertTrue(wait_until(lambda: len(self.drivers) == 2), 'the owner login window was opened')


if __name__ == '__main__':
    unittest.main()


class PopupResultsTests(unittest.TestCase):
    def popup_result(self, *, failed=False, error=False, origin=None, blank=False):
        return {'origin': origin or f'https://{SITE}/start', 'hosts': [] if blank else [PROVIDER],
                'needs_owner': failed, 'oauth_error': error}

    def test_closed_popup_result_uses_returned_receipt_without_settling_login(self):
        sess, driver, approvals = resign_session('silent')
        sess.run('browser_open', {'url': f'https://{SITE}/start', 'effect': 'navigate'})
        driver.navigate(AUTHORIZE)
        driver.goto(f'https://{SITE}/callback', 1)
        sess._popup_results = [self.popup_result()]
        page = sess.run('browser_read', {})
        self.assertEqual(page['provider_sign_in']['state'], 'returned')
        self.assertFalse(getattr(approvals, 'resolved', []))
        self.assertNotIn('provider_sign_in', sess.run('browser_read', {}))

    def test_timeout_blank_and_fragment_error_hand_off_once_without_poisoning_next_attempt(self):
        for failed, error, blank in ((True, False, True), (True, False, False), (False, True, False)):
            sess, driver, approvals = resign_session('silent')
            sess.run('browser_open', {'url': f'https://{SITE}/start', 'effect': 'navigate'})
            sess._popup_results = [self.popup_result(failed=failed, error=error, blank=blank)]
            page = sess.run('browser_read', {})
            self.assertEqual(page['provider_sign_in']['state'], 'unconfirmed')
            self.assertTrue(approvals.offers)
            offers = list(approvals.offers)
            sess.run('browser_read', {})
            self.assertEqual(offers, approvals.offers)
            page = sess.run('browser_open', {'url': AUTHORIZE, 'effect': 'navigate'})
            self.assertEqual(page['provider_sign_in']['state'], 'returned')

    def test_hard_action_timeout_delivers_the_popup_opener_handoff(self):
        sess, driver, approvals = resign_session('silent')
        sess.run('browser_open', {'url': f'https://{SITE}/start', 'effect': 'navigate'})
        def timeout(*args, **kwargs):
            error = TimeoutError('bounded action')
            error.work_popup_origin = f'https://{SITE}/start'
            raise error
        driver.click = timeout
        with self.assertRaises(ToolError) as caught:
            sess.run('browser_click', {'target': '다른 계정으로 계속', 'effect': 'navigate'})
        self.assertEqual(caught.exception.code, 'browser_timeout')
        self.assertIn('OFFER-EXPLICIT', str(caught.exception))
        self.assertEqual(approvals.offers[-1][0], f'https://{SITE}/start')

    def test_old_popup_origin_survives_later_explicit_open(self):
        sess, driver, approvals = resign_session('silent')
        sess.run('browser_open', {'url': f'https://{SITE}/start', 'effect': 'navigate'})
        sess._popup_results = [self.popup_result(failed=True)]
        page = sess.run('browser_open', {'url': 'https://public.test/', 'effect': 'navigate'})
        self.assertEqual(page['provider_sign_in']['site'], SITE)
        self.assertEqual(approvals.offers[-1][0], f'https://{SITE}/start')

    def test_closed_blank_does_not_skip_main_provider_interaction_refusal(self):
        sess, driver, approvals = resign_session('chooser')
        sess.run('browser_open', {'url': f'https://{SITE}/start', 'effect': 'navigate'})
        driver.goto(AUTHORIZE, 1)
        sess._popup_results = [self.popup_result(blank=True)]
        with self.assertRaises(ToolError) as caught:
            sess.run('browser_click', {'target': 'anything', 'effect': 'navigate'})
        self.assertEqual(caught.exception.code, 'provider_interaction_refused')
