"""#636: sign back in through the identity provider the owner already used, with no interaction on the provider.

Evidence classes, named separately:

* unit (model-free): ``browser_worker.authorization_host`` recognises an OAuth 2.0
  authorization request (RFC 6749 §4.1.1: ``response_type``, ``client_id`` and
  ``redirect_uri``; OIDC Core §3.1.2.1 adds ``scope=openid`` to the same request)
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
AUTHORIZE = (f'https://{PROVIDER}/authorize?response_type=code&client_id={CLIENT}'
             f'&redirect_uri={quote("https://site.test/callback", safe="")}&scope=openid&state={STATE}&nonce={NONCE}')
QUERY_VALUES = (CLIENT, STATE, NONCE, 'callback')
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
    # A public page of the site (no sign-in form, no session needed).
    f'{SITE}/': '<html><head><title>홈</title></head><body><h1>공개 페이지</h1></body></html>',
    # A provider page that asks for something without a password: an account choice / consent.
    f'{PROVIDER}/choose': '''<html><head><title>계정 선택</title></head><body><h1>계속할 계정</h1>
      <form action="/consent" method="post"><button type="submit">계속</button></form>
      <label>메모 <input type="text" name="note"></label></body></html>''',
    # A provider page that asks for a password.
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
                    'silent_no_session': 'https://site.test/home'}


class TwoHostDriver(FakeDriver):
    """The fake page driver over two hosts; the provider's authorization endpoint redirects as ``answer`` says.

    ``authorizations`` answers as the worker's ``op_state`` does: the count and hosts that
    ``browser_worker.Worker.note_authorization`` recorded for each allowed navigation.
    """

    def __init__(self, answer='silent', log=None):
        super().__init__(pages=PAGES, origin='https://' + SITE, log=log)
        self.answer = answer
        self.worker = types.SimpleNamespace(authorizations=[], authorization_count=0)
        #: The browser's cookies by site; the signed-out site already holds an anonymous one.
        self.cookies = {SITE: [{'name': 'anon', 'value': 'a1', 'domain': SITE, 'path': '/', 'expires': None}]}

    def _path(self, url):
        parts = urlsplit(url)
        return (parts.hostname or '') + (parts.path or '/')

    def goto(self, url, timeout):
        self.log.append(('goto', url, timeout))
        browser_worker.Worker.note_authorization(self.worker, url)
        if self._path(url) == f'{PROVIDER}/authorize':
            url = PROVIDER_ANSWERS[self.answer]   # the provider's redirect: navigation only
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

    def login_required(self, url, explicit=False, *, window_url=None):
        self.offers.append((url, explicit))
        return 'OFFER-EXPLICIT' if explicit else 'OFFER'

    def provider_sign_in(self, url):
        site = bs.registrable_domain(urlsplit(url).hostname)
        provider = self.providers.get(site)
        return {'site': site, 'provider': provider} if provider else None

    def login_resolved(self, url):
        self.resolved = getattr(self, 'resolved', []) + [url]
        return True


def resign_session(answer='silent', providers=None):
    driver = TwoHostDriver(answer)
    approvals = ProviderApprovals({SITE: PROVIDER} if providers is None else providers)
    return bs.BrowserSession(lambda: driver, work_id='work-636', approvals=approvals), driver, approvals


def open_login(sess):
    return sess.run('browser_open', {'url': f'https://{SITE}/login', 'effect': 'navigate'})


class AuthorizationRequestTests(unittest.TestCase):
    def test_an_authorization_request_is_known_by_its_parameter_names_and_only_its_host_is_kept(self):
        self.assertEqual(browser_worker.authorization_host(AUTHORIZE), PROVIDER)
        for missing in ('response_type', 'client_id', 'redirect_uri'):
            url = '&'.join(part for part in AUTHORIZE.split('&') if not part.split('?')[-1].startswith(missing + '='))
            self.assertIsNone(browser_worker.authorization_host(url), missing)
        self.assertIsNone(browser_worker.authorization_host(f'https://{SITE}/login?next=/home'))
        self.assertIsNone(browser_worker.authorization_host('intent://x?response_type=code&client_id=a&redirect_uri=b'))
        self.assertIsNone(browser_worker.authorization_host('not a url'))

    def test_the_worker_keeps_hosts_only_and_a_bounded_list(self):
        state = types.SimpleNamespace(authorizations=[], authorization_count=0)
        browser_worker.Worker.note_authorization(state, f'https://{SITE}/home')
        self.assertEqual((state.authorization_count, state.authorizations), (0, []))
        for _ in range(browser_worker.AUTHORIZATIONS_KEPT + 3):
            browser_worker.Worker.note_authorization(state, AUTHORIZE)
        self.assertEqual(state.authorization_count, browser_worker.AUTHORIZATIONS_KEPT + 3)
        self.assertEqual(state.authorizations, [PROVIDER] * browser_worker.AUTHORIZATIONS_KEPT)
        self.assertFalse(any(value in flat(state.__dict__) for value in QUERY_VALUES))


class ResignInSessionTests(unittest.TestCase):
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

    def test_a_provider_that_returns_without_asking_completes_and_is_recorded(self):
        sess, driver, _approvals = resign_session('silent')
        open_login(sess)
        page = sess.run('browser_click', {'target': '다른 계정으로 로그인', 'effect': 'navigate'})
        self.assertEqual((page['state'], page['url']), ('page', f'https://{SITE}/home'))
        # The Work's own pending login request for the site is settled (the service's ``resolve_browser_login``).
        self.assertEqual(_approvals.resolved, [f'https://{SITE}/login'])
        self.assertEqual(page['provider_sign_in'], {'site': SITE, 'provider': PROVIDER, 'state': 'signed_in',
                                                    'login_request': 'resolved'})
        summary = evidence_summary('browser_click', page)
        self.assertEqual(summary['provider_sign_in'], {'site': SITE, 'provider': PROVIDER, 'state': 'signed_in',
                                                       'login_request': 'resolved'})
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

    def assert_unconfirmed(self, page, approvals):
        self.assertFalse(getattr(approvals, 'resolved', []), 'the login request stays for the owner')
        self.assertEqual(page['provider_sign_in'], {'site': SITE, 'provider': PROVIDER, 'state': 'unconfirmed'})
        self.assertTrue(page['next_step'].startswith(msg('browser.provider_unconfirmed', provider=PROVIDER)))
        self.assertTrue(page['next_step'].endswith('OFFER-EXPLICIT'), 'the owner is asked to sign in')
        self.assertEqual(approvals.offers[-1], (f'https://{SITE}/login', True))
        self.assertEqual(evidence_summary('browser_click', page)['provider_sign_in']['state'], 'unconfirmed')

    def test_an_oauth_error_at_the_redirect_is_no_sign_in(self):
        sess, driver, approvals = resign_session('refused')
        open_login(sess)
        # The site even sets a cookie on the error page: the OAuth error still decides.
        driver.cookies[SITE].append({'name': 'flash', 'value': 'denied', 'domain': SITE, 'path': '/', 'expires': None})
        page = sess.run('browser_click', {'target': '다른 계정으로 로그인', 'effect': 'navigate'})
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

    def test_the_unsupported_navigation_text_points_to_the_desktop_layout(self):
        text = bs.NAVIGATION_REFUSALS['unsupported_navigation']
        self.assertIn('layout: desktop', text)
        self.assertIn('browser_open', text)
        self.assertIn('browser_sign_in', text)
        self.assertIn('layout: desktop', msg('browser.unsupported_navigation', 'en'))


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

    def test_the_re_sign_in_is_in_the_receipt_and_the_information_use_audit(self):
        self.run_work('silent',
                      {'tool_calls': [call('1', 'browser_open', url=f'https://{SITE}/login', effect='navigate')]},
                      {'tool_calls': [call('2', 'browser_click', target='다른 계정으로 로그인', effect='navigate')]},
                      {'content': '주문 내역을 확인했습니다.'})
        [(status, detail)] = self.details('browser_click')
        self.assertEqual(status, 'succeeded')
        self.assertEqual(detail['evidence']['provider_sign_in'], {'site': SITE, 'provider': PROVIDER, 'state': 'signed_in',
                                                                  'login_request': 'resolved'})
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
                                                         'same_site': None}]})

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

    def test_the_owners_settings_login_window_records_the_provider_host_and_no_query_value(self):
        self.settings_window_sign_in()
        record = self.providers()['fixture.test']
        self.assertEqual((record['provider'], record['observed_in']), (PROVIDER, 'owner_login_window'))
        self.assertLessEqual(abs(record['observed_at'] - time.time()), 60)
        stored = json.dumps(self.providers(), ensure_ascii=False)
        self.assertFalse(any(value in stored for value in QUERY_VALUES), 'hosts only, never a query value')
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
        self.assertEqual(self.service.browser_signin_provider(url), {'site': 'fixture.test', 'provider': PROVIDER})
        self.assertEqual(self.service.browser_approvals_for({'id': 'w', 'channel': 'web-636'}).provider_sign_in(url),
                         {'site': 'fixture.test', 'provider': PROVIDER})
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


class ResolvedLoginTests(LoginHarness):
    """Codex P2 on #637: a confirmed re-sign-in settles the Work's own login request; nothing else moves."""

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

    def test_a_confirmed_re_sign_in_leaves_no_pending_login_and_opens_no_window(self):
        job_id = self.resign_work()
        row = self.service._browser_login(job_id)
        self.assertEqual((row['state'], row['cause']), ('resolved', 'provider_sign_in'))
        self.assertEqual(self.receipt(job_id), [{'site': SITE, 'provider': PROVIDER, 'state': 'signed_in',
                                                 'login_request': 'resolved'}])
        self.assertFalse(self.service.work_has_pending_resume(job_id))
        self.assertFalse(self.service.sign_in_requested(job_id))
        # No login window was opened after the run: only the Work's own browser ever started.
        self.assertEqual(len(self.drivers), 1)
        self.assertNotEqual(self.profile.occupancy(), 'login')
        self.assertEqual(self.service.browser_status()['pending_logins'], [])
        self.assertFalse(self.service.offer_browser_login(self.store.job(job_id)), 'nothing left to offer')
        # The Work's status is its own outcome; no pending login made it partial or holds it.
        self.assertIn(self.store.job(job_id)['status'], ('succeeded', 'partial', 'failed'))
        self.assertNotIn(self.store.job(job_id)['status'], ('queued', 'running', 'awaiting_connection'))
        # A later login page in the same Work may ask again.
        from personal_agent.quickstart_service import BROWSER_LOGIN_REASK_STATES
        self.assertIn('resolved', BROWSER_LOGIN_REASK_STATES)

    def test_an_unconfirmed_re_sign_in_keeps_the_request_and_the_owner_is_asked(self):
        self.answer = 'silent_no_session'
        job_id = self.resign_work()
        self.assertEqual(self.receipt(job_id)[0]['state'], 'unconfirmed')
        self.assertTrue(wait_until(lambda: self.state(job_id) in ('opening', 'offered')), self.state(job_id))
        self.assertTrue(self.service._browser_login(job_id)['explicit'], 'asked however the Work ends')
        self.assertTrue(wait_until(lambda: len(self.drivers) == 2), 'the owner login window was opened')

    def test_another_sites_or_another_works_request_is_untouched(self):
        job = {'id': 'work-a', 'channel': 'web'}
        other = {'work_id': 'work-a', 'url': 'https://other.test/login', 'host': 'other.test', 'state': 'requested',
                 'requested_at': time.time(), 'nonce': 'n1', 'explicit': False}
        self.service._put_browser_login('work-a', other)
        self.assertFalse(self.service.resolve_browser_login(job, f'https://{SITE}/login'))
        self.assertEqual(self.service._browser_login('work-a'), other)
        theirs = {**other, 'work_id': 'work-b', 'url': f'https://{SITE}/login', 'host': SITE, 'nonce': 'n2'}
        self.service._put_browser_login('work-b', theirs)
        self.assertFalse(self.service.resolve_browser_login(job, f'https://{SITE}/login'))
        self.assertEqual(self.service._browser_login('work-b'), theirs)
        # A window already offered for this Work is the owner's; only a request still to be shown moves.
        offered = {**theirs, 'work_id': 'work-c', 'state': 'offered', 'nonce': 'n3'}
        self.service._put_browser_login('work-c', offered)
        self.assertFalse(self.service.resolve_browser_login({'id': 'work-c'}, f'https://{SITE}/login'))
        self.assertEqual(self.service._browser_login('work-c'), offered)
        mine = {**theirs, 'work_id': 'work-d', 'nonce': 'n4'}
        self.service._put_browser_login('work-d', mine)
        self.assertTrue(self.service.resolve_browser_login({'id': 'work-d'}, f'https://www.{SITE}/home'))
        self.assertEqual(self.service._browser_login('work-d')['state'], 'resolved')
        self.assertEqual(self.service._browser_login('work-b'), theirs)


if __name__ == '__main__':
    unittest.main()
