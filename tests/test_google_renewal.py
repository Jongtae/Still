"""STEADY-GOOGLE-01 (#8): Google connections keep a usable refresh credential through transient renewal failures.

Every token exchange, HTTP call, clock and key store is injected; nothing here
reaches Google or the macOS Keychain, and every credential is a made-up string.
"""

import json
import pathlib
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

from cryptography.fernet import Fernet

from personal_agent import calendar_oauth as calendar_oauth_module
from personal_agent import connector_contract
from personal_agent import gmail as gmail_module
from personal_agent.calendar import CALENDAR_SPEC, CALENDAR_WRITE_SPEC, CalendarConnector
from personal_agent.calendar_oauth import (
    DRIVE_GRANT,
    DRIVE_SECRET_NAMESPACE,
    RENEWAL_MARGIN_SECONDS,
    CalendarOAuth,
    CalendarOAuthError,
    CalendarReauthenticationRequired,
    CalendarRenewalUnavailable,
    EncryptedCalendarSecretStore,
    calendar_transport,
    drive_transport,
)
from personal_agent.connector_contract import ConnectorRegistry, ConnectorState
from personal_agent.connector_http import token_endpoint_refused
from personal_agent.gmail import (
    GMAIL_CONNECTOR,
    GMAIL_CONNECTOR_ID,
    GMAIL_READONLY_SCOPE,
    EncryptedGmailSecretStore,
    GmailConnector,
    GmailError,
    GmailReauthenticationRequired,
    GmailRenewalUnavailable,
)
from personal_agent.google_calendar import CALENDAR_API, GoogleCalendar, GoogleCalendarHTTPError
from personal_agent.google_drive_read import DRIVE_API, DRIVE_CONNECTOR_ID, DRIVE_READONLY_SCOPE, DriveHTTPError
from personal_agent.quickstart import configured_service
from personal_agent.quickstart_service import DRIVE_CALLBACK_PATH
from personal_agent.quickstart_store import QuickStore

OWNER = 'local-owner'
TOKEN_URL = 'https://oauth2.googleapis.com/token'


def http_error(status):
    return HTTPError(TOKEN_URL, status, 'error', {}, None)


def state_of(offer):
    return parse_qs(urlparse(offer['authorization_url']).query)['state'][0]


#: Failures that say nothing about the grant: kept, retried by a later request.
RETRYABLE = (lambda: URLError('offline'), lambda: TimeoutError('slow'), lambda: OSError('reset'),
             lambda: http_error(503), lambda: http_error(500), lambda: http_error(429),
             lambda: ValueError('captive portal page, not JSON'))
#: Google refused the grant (RFC 6749 5.2): reauthentication.
DEFINITIVE = (lambda: http_error(400), lambda: http_error(401))


class TokenEndpointClassificationTest(unittest.TestCase):
    def test_only_a_refusal_from_the_token_endpoint_is_definitive(self):
        for make in DEFINITIVE:
            self.assertTrue(token_endpoint_refused(make()))
        for make in RETRYABLE:
            self.assertFalse(token_endpoint_refused(make()))


class Exchange:
    """A token endpoint: authorization codes always work; renewals follow ``self.renewal``."""

    def __init__(self, scope):
        self.scope = scope
        self.renewal = None
        self.calls = []

    def __call__(self, payload):
        self.calls.append(payload['grant_type'])
        if payload['grant_type'] == 'refresh_token':
            if self.renewal is not None:
                raise self.renewal()
            return {'access_token': f'renewed-{len(self.calls)}', 'expires_in': 3600, 'scope': self.scope}
        return {'access_token': 'first', 'expires_in': 3600, 'refresh_token': 'refresh-1', 'scope': self.scope}

    def renewals(self):
        return self.calls.count('refresh_token')


class DriveCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.raw = QuickStore(temp.name)
        self.key = Fernet.generate_key()
        self.clock = [1_000.0]
        self.exchange = Exchange(DRIVE_READONLY_SCOPE)
        self.store, self.registry, self.oauth = self.build()
        self.oauth.complete_oauth(OWNER, {'state': state_of(self.oauth.begin_oauth(OWNER)), 'code': 'c'},
                                  self.exchange)
        self.requests = []
        self.replies = []

    def build(self, key=None):
        store = EncryptedCalendarSecretStore(self.raw, key or (lambda: self.key), namespace=DRIVE_SECRET_NAMESPACE)
        registry = ConnectorRegistry(store, (), clock=lambda: self.clock[0])
        oauth = CalendarOAuth(store, 'n.apps.googleusercontent.com', 'http://127.0.0.1:8787' + DRIVE_CALLBACK_PATH,
                              registry=registry, now=lambda: self.clock[0], allow_localhost=True, grants=(DRIVE_GRANT,))
        return store, registry, oauth

    def opener(self, url, headers):
        self.requests.append(headers['Authorization'])
        reply = self.replies.pop(0) if self.replies else b'{"files": []}'
        if isinstance(reply, Exception):
            raise reply
        return reply

    def transport(self, oauth=None):
        return drive_transport(oauth or self.oauth, OWNER, self.exchange, opener=self.opener)

    def state(self):
        return self.registry.status(OWNER, DRIVE_CONNECTOR_ID).state

    def stored_ciphertext(self):
        return self.raw.secret(DRIVE_SECRET_NAMESPACE + calendar_oauth_module._secret_slot(
            calendar_oauth_module.TOKEN_SECRET_KEY, DRIVE_GRANT, OWNER))


class DriveRenewalTest(DriveCase):
    def test_a_transient_renewal_failure_keeps_the_credential_and_a_later_request_recovers(self):
        for make in RETRYABLE:
            with self.subTest(failure=type(make()).__name__):
                self.clock[0] += 7200
                self.exchange.renewal = make
                before = self.exchange.renewals()
                with self.assertRaises(CalendarRenewalUnavailable):
                    self.transport()(DRIVE_API + '/files?q=x')
                self.assertEqual(self.exchange.renewals(), before + 1)  # one attempt, no retry loop
                self.assertEqual(self.requests, [])
                self.assertIs(self.state(), ConnectorState.CONNECTED)
                self.assertTrue(self.oauth.credential_renewable(OWNER))
                # Google answers again: the next request renews without the owner.
                self.exchange.renewal = None
                self.transport()(DRIVE_API + '/files?q=x')
                self.assertEqual(self.requests[-1], f'Bearer renewed-{len(self.exchange.calls)}')
                self.assertIs(self.state(), ConnectorState.CONNECTED)
                self.requests.clear()

    def test_a_definitive_refusal_requires_reauthentication(self):
        for make in DEFINITIVE:
            with self.subTest(status=make().code):
                self.setUp()
                self.clock[0] += 7200
                self.exchange.renewal = make
                with self.assertRaises(CalendarReauthenticationRequired):
                    self.transport()(DRIVE_API + '/files?q=x')
                self.assertIs(self.state(), ConnectorState.REAUTH_REQUIRED)
                self.assertFalse(self.oauth.credential_renewable(OWNER))
                self.assertEqual(self.requests, [])

    def test_a_token_about_to_expire_is_renewed_before_the_request(self):
        self.clock[0] += 3600 - RENEWAL_MARGIN_SECONDS / 2
        self.transport()(DRIVE_API + '/files?q=x')
        self.assertEqual(self.exchange.renewals(), 1)
        self.assertEqual(self.requests, ['Bearer renewed-2'])

    def test_a_token_well_before_expiry_is_not_renewed(self):
        self.clock[0] += 3600 - RENEWAL_MARGIN_SECONDS * 4
        self.transport()(DRIVE_API + '/files?q=x')
        self.assertEqual((self.exchange.renewals(), self.requests), (0, ['Bearer first']))

    def test_a_transient_failure_before_expiry_still_uses_the_valid_token(self):
        self.clock[0] += 3600 - RENEWAL_MARGIN_SECONDS / 2
        self.exchange.renewal = RETRYABLE[0]
        self.transport()(DRIVE_API + '/files?q=x')
        self.assertEqual(self.requests, ['Bearer first'])
        self.assertIs(self.state(), ConnectorState.CONNECTED)

    def test_one_renewal_recovers_from_a_401_and_the_read_is_sent_once_more(self):
        self.replies = [DriveHTTPError(401), b'{"files": []}']
        self.assertEqual(self.transport()(DRIVE_API + '/files?q=x'), b'{"files": []}')
        self.assertEqual(self.requests, ['Bearer first', 'Bearer renewed-2'])
        self.assertEqual(self.exchange.renewals(), 1)
        self.assertIs(self.state(), ConnectorState.CONNECTED)

    def test_a_second_401_requires_reauthentication_without_a_loop(self):
        self.replies = [DriveHTTPError(401), DriveHTTPError(401), DriveHTTPError(401)]
        with self.assertRaises(DriveHTTPError):
            self.transport()(DRIVE_API + '/files?q=x')
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.exchange.renewals(), 1)
        self.assertIs(self.state(), ConnectorState.REAUTH_REQUIRED)

    def test_a_401_whose_renewal_gets_no_answer_keeps_the_credential(self):
        self.replies = [DriveHTTPError(401)]
        self.exchange.renewal = RETRYABLE[0]
        with self.assertRaises(CalendarRenewalUnavailable):
            self.transport()(DRIVE_API + '/files?q=x')
        self.assertEqual(len(self.requests), 1)
        self.assertIs(self.state(), ConnectorState.CONNECTED)
        self.assertTrue(self.oauth.credential_renewable(OWNER))

    def test_a_401_whose_renewal_is_refused_requires_reauthentication(self):
        self.replies = [DriveHTTPError(401)]
        self.exchange.renewal = DEFINITIVE[0]
        with self.assertRaises(DriveHTTPError):
            self.transport()(DRIVE_API + '/files?q=x')
        self.assertEqual(len(self.requests), 1)
        self.assertIs(self.state(), ConnectorState.REAUTH_REQUIRED)

    def test_renewal_after_a_401_is_pinned_to_the_revision_that_saw_it(self):
        self.assertFalse(self.oauth.renew_after_rejection(OWNER, self.exchange, expected_revision='an-older-revision'))
        self.assertEqual(self.exchange.renewals(), 0)
        self.assertTrue(self.oauth.credential_current(OWNER))

    def test_a_renewal_that_widens_the_scope_is_still_refused(self):
        self.clock[0] += 7200
        self.exchange.scope = DRIVE_READONLY_SCOPE + ' https://www.googleapis.com/auth/drive'
        with self.assertRaises(CalendarReauthenticationRequired):
            self.transport()(DRIVE_API + '/files?q=x')
        self.assertIs(self.state(), ConnectorState.REAUTH_REQUIRED)


class LocalKeyProblemTest(DriveCase):
    def test_an_unavailable_or_invalid_key_is_reported_and_nothing_is_cleared(self):
        def locked():
            raise RuntimeError('keychain locked')

        for key, reason in ((locked, 'local_key_unavailable'), (lambda: b'not-a-fernet-key', 'local_key_invalid')):
            with self.subTest(reason=reason):
                ciphertext = self.stored_ciphertext()
                store, registry, oauth = self.build(key)
                with self.assertRaises(CalendarOAuthError) as caught:
                    drive_transport(oauth, OWNER, self.exchange, opener=self.opener)(DRIVE_API + '/files?q=x')
                self.assertEqual(caught.exception.reason, reason)
                self.assertEqual(store.key_problem, reason)
                self.assertEqual(self.stored_ciphertext(), ciphertext)
                self.assertIs(registry.status(OWNER, DRIVE_CONNECTOR_ID).state, ConnectorState.CONNECTED)
                self.assertEqual(self.requests, [])
        # The right key again: the same stored credential works.
        store, registry, oauth = self.build()
        drive_transport(oauth, OWNER, self.exchange, opener=self.opener)(DRIVE_API + '/files?q=x')
        self.assertEqual(self.requests, ['Bearer first'])
        self.assertIsNone(store.key_problem)


class CalendarTransportRenewalTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = EncryptedCalendarSecretStore(QuickStore(temp.name), Fernet.generate_key())
        self.clock = [1_000.0]
        self.registry = ConnectorRegistry(self.store, (), clock=lambda: self.clock[0])
        self.oauth = CalendarOAuth(self.store, 'n.apps.googleusercontent.com',
                                   'http://127.0.0.1:8787/oauth/calendar/callback', registry=self.registry,
                                   now=lambda: self.clock[0], allow_localhost=True)
        self.exchange = Exchange(' '.join(CALENDAR_SPEC.required_scopes))
        self.oauth.complete_oauth(OWNER, {'state': state_of(self.oauth.begin_oauth(OWNER)), 'code': 'c'}, self.exchange)
        write_exchange = Exchange(' '.join(CALENDAR_WRITE_SPEC.required_scopes))
        self.oauth.complete_oauth(OWNER, {'state': state_of(self.oauth.begin_oauth(OWNER, write=True)), 'code': 'c'},
                                  write_exchange)
        self.sent = []
        self.replies = []

    def opener(self, method, url, body, headers):
        self.sent.append((method, headers['Authorization']))
        reply = self.replies.pop(0) if self.replies else {}
        if isinstance(reply, Exception):
            raise reply
        return reply

    def transport(self):
        return calendar_transport(self.store, self.registry, OWNER, allow_writes=True, now=lambda: self.clock[0],
                                  oauth=self.oauth, exchange=self.exchange, opener=self.opener)

    def test_a_read_refused_with_401_is_renewed_and_sent_once_more(self):
        self.replies = [GoogleCalendarHTTPError(401), {'items': []}]
        self.assertEqual(self.transport()('GET', CALENDAR_API + '/calendars/primary/events', None, {}), {'items': []})
        self.assertEqual([method for method, _ in self.sent], ['GET', 'GET'])
        self.assertEqual(self.exchange.renewals(), 1)
        self.assertTrue(self.oauth.credential_current(OWNER))

    def test_a_write_refused_with_401_is_never_replayed(self):
        self.replies = [GoogleCalendarHTTPError(401)]
        with self.assertRaises(GoogleCalendarHTTPError):
            self.transport()('POST', CALENDAR_API + '/calendars/primary/events', {'summary': 'x'}, {})
        self.assertEqual(self.sent, [('POST', 'Bearer first')])
        self.assertEqual(self.oauth.status(OWNER, write=True)['state'], 'reauth_required')
        # The separately authorized read grant is untouched.
        self.assertEqual(self.oauth.status(OWNER)['state'], 'connected')

    def test_a_transient_renewal_failure_keeps_the_calendar_connected(self):
        self.clock[0] += 7200
        self.exchange.renewal = RETRYABLE[1]
        with self.assertRaises(CalendarRenewalUnavailable):
            self.transport()('GET', CALENDAR_API + '/calendars/primary/events', None, {})
        self.assertEqual(self.sent, [])
        self.assertEqual(self.oauth.status(OWNER)['state'], 'connected')
        self.assertTrue(self.oauth.credential_renewable(OWNER))


class CalendarConnectorKeyTest(unittest.TestCase):
    """The connector asks its transport for the key before dispatch, never for a completed change."""

    def test_a_completed_change_is_returned_even_when_the_key_is_unavailable(self):
        from personal_agent.calendar import CALENDAR_CONNECTOR_ID, CALENDAR_WRITE_CONNECTOR_ID, CalendarError
        from personal_agent.google_calendar import CALENDAR_WRITE_SCOPE
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = QuickStore(temp.name)
        key = {'problem': None}

        def prepare_key(*, write=False):
            if key['problem']:
                raise CalendarOAuthError(key['problem'])

        class Provider:
            def __init__(self):
                self.transport = lambda *args: None
                self.transport.prepare_key = prepare_key
                self.calls = []

            def create(self, payload, idempotency):
                self.calls.append('create')
                return {'id': 'new-event', 'etag': '"v1"'}

        provider = Provider()
        registry = ConnectorRegistry(store, (CALENDAR_SPEC, CALENDAR_WRITE_SPEC))
        registry.transition('owner', CALENDAR_CONNECTOR_ID, ConnectorState.CONNECTED,
                            granted_scopes=CALENDAR_SPEC.required_scopes)
        registry.transition('owner', CALENDAR_WRITE_CONNECTOR_ID, ConnectorState.CONNECTED,
                            granted_scopes=(CALENDAR_WRITE_SCOPE,))
        calendar = CalendarConnector(store, provider, registry=registry)
        event = {'summary': 'review', 'start': '2026-09-22T10:00:00+09:00', 'end': '2026-09-22T11:00:00+09:00',
                 'timezone': 'Asia/Seoul'}
        draft = calendar.draft_create(event, 'owner')
        approval = calendar.approve(draft['id'], 'owner')['approval_id']
        created = calendar.create(draft['id'], approval, 'owner')
        key['problem'] = 'local_key_unavailable'
        self.assertEqual(calendar.create(draft['id'], approval, 'owner'), created)
        self.assertEqual(provider.calls, ['create'])
        # A change not yet sent fails before dispatch with the key reason and sends nothing.
        other = calendar.draft_create({**event, 'summary': 'second'}, 'owner')
        other_approval = calendar.approve(other['id'], 'owner')['approval_id']
        with self.assertRaises(CalendarError) as caught:
            calendar.create(other['id'], other_approval, 'owner')
        self.assertEqual((caught.exception.reason, caught.exception.effect), ('local-key-unavailable', 'none'))
        self.assertEqual(provider.calls, ['create'])


class GmailRenewalTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.raw = QuickStore(temp.name)
        self.key = Fernet.generate_key()
        self.clock = [1_000.0]
        self.exchange = Exchange(GMAIL_READONLY_SCOPE)
        self.calls = []
        self.replies = []
        self.gmail = self.build()
        offer = self.gmail.begin_oauth(OWNER)
        self.gmail.complete_oauth(OWNER, {'state': state_of(offer), 'code': 'c'}, self.exchange)

    def build(self, key=None):
        store = EncryptedGmailSecretStore(self.raw, key or (lambda: self.key))
        registry = ConnectorRegistry(store, (GMAIL_CONNECTOR,), clock=lambda: self.clock[0])
        return GmailConnector(store, 'n.apps.googleusercontent.com', 'http://127.0.0.1:8787/oauth/gmail/callback',
                              registry=registry, transport=self.transport, now=lambda: self.clock[0],
                              allow_localhost=True, token_exchange=self.exchange)

    def transport(self, method, endpoint, params, headers):
        self.calls.append(headers['Authorization'])
        return self.replies.pop(0) if self.replies else {'messages': []}

    def state(self):
        return self.gmail.status(OWNER)['state']

    def test_a_transient_renewal_failure_keeps_the_credential_and_recovers(self):
        self.clock[0] += 7200
        self.exchange.renewal = RETRYABLE[0]
        with self.assertRaises(GmailRenewalUnavailable):
            self.gmail.search(OWNER, 'from:me')
        self.assertEqual((self.calls, self.state()), ([], 'connected'))
        self.assertTrue(self.gmail.credential_renewable(OWNER))
        self.exchange.renewal = None
        self.gmail.search(OWNER, 'from:me')
        self.assertEqual(self.calls, ['Bearer renewed-3'])

    def test_a_definitive_refusal_requires_reauthentication(self):
        self.clock[0] += 7200
        self.exchange.renewal = DEFINITIVE[0]
        with self.assertRaises(GmailReauthenticationRequired):
            self.gmail.search(OWNER, 'from:me')
        self.assertEqual(self.state(), 'reauth_required')
        self.assertFalse(self.gmail.credential_renewable(OWNER))

    def test_a_token_about_to_expire_is_renewed_first(self):
        self.clock[0] += 3600 - RENEWAL_MARGIN_SECONDS / 2
        self.gmail.search(OWNER, 'from:me')
        self.assertEqual((self.exchange.renewals(), self.calls), (1, ['Bearer renewed-2']))

    def test_a_401_is_renewed_once_and_the_search_starts_over(self):
        self.replies = [{'status_code': 401}, {'messages': []}]
        self.assertEqual(self.gmail.search(OWNER, 'from:me'), ())
        self.assertEqual(self.calls, ['Bearer first', 'Bearer renewed-2'])
        self.assertEqual((self.exchange.renewals(), self.state()), (1, 'connected'))

    def test_a_second_401_requires_reauthentication_without_a_loop(self):
        self.replies = [{'status_code': 401}, {'status_code': 401}, {'status_code': 401}]
        with self.assertRaises(GmailReauthenticationRequired):
            self.gmail.search(OWNER, 'from:me')
        self.assertEqual((len(self.calls), self.exchange.renewals(), self.state()), (2, 1, 'reauth_required'))

    def test_a_local_key_problem_is_reported_and_nothing_is_cleared(self):
        before = self.raw.secret('encrypted:gmail:' + gmail_module._owner_secret_key(gmail_module.TOKEN_SECRET_KEY, OWNER))

        def locked():
            raise RuntimeError('keychain locked')

        self.gmail = self.build(locked)
        with self.assertRaises(GmailError) as caught:
            self.gmail.search(OWNER, 'from:me')
        self.assertEqual(caught.exception.reason, 'local_key_unavailable')
        self.assertEqual(self.gmail.store.key_problem, 'local_key_unavailable')
        self.assertEqual(self.state(), 'connected')
        self.assertEqual(self.raw.secret('encrypted:gmail:' + gmail_module._owner_secret_key(
            gmail_module.TOKEN_SECRET_KEY, OWNER)), before)


def held_locks():
    """Which OAuth, authority or connector-dispatch locks the calling thread holds."""
    held = []
    if calendar_oauth_module._OAUTH_LOCK._is_owned():
        held.append('calendar-oauth')
    if gmail_module._OAUTH_LOCK._is_owned():
        held.append('gmail-oauth')
    if connector_contract._CONNECTOR_STATE_LOCK._is_owned():
        held.append('authority')
    if any(lock._is_owned() for lock in list(connector_contract._CONNECTOR_DISPATCH_LOCKS.values())):
        held.append('dispatch')
    return held


class KeyStoreOutsideLocksTest(unittest.TestCase):
    """The key provider (the macOS Keychain in production) is never asked while a lock is held."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.raw = QuickStore(temp.name)
        self.key = Fernet.generate_key()
        self.asked = []
        self.clock = [1_000.0]

    def provider(self):
        self.asked.append(held_locks())
        return self.key

    def assert_never_under_a_lock(self):
        self.assertTrue(self.asked)
        self.assertEqual([held for held in self.asked if held], [])

    def calendar(self, raw_store=None):
        """A fresh store, registry and OAuth over the same data: a restarted process."""
        store = EncryptedCalendarSecretStore(raw_store or self.raw, self.provider)
        registry = ConnectorRegistry(store, (), clock=lambda: self.clock[0])
        oauth = CalendarOAuth(store, 'n.apps.googleusercontent.com', 'http://127.0.0.1:8787/oauth/calendar/callback',
                              registry=registry, now=lambda: self.clock[0], allow_localhost=True)
        return store, registry, oauth

    def test_calendar_lifecycle_and_requests(self):
        exchange = Exchange(' '.join(CALENDAR_SPEC.required_scopes))
        _store, _registry, oauth = self.calendar()
        offer = oauth.begin_oauth(OWNER)
        _store, _registry, oauth = self.calendar()
        owner = oauth.callback_owner(state_of(offer), [OWNER])
        _store, _registry, oauth = self.calendar()
        oauth.complete_oauth(owner, {'state': state_of(offer), 'code': 'c'}, exchange)
        self.clock[0] += 7200
        store, registry, oauth = self.calendar()
        transport = calendar_transport(store, registry, OWNER, now=lambda: self.clock[0], oauth=oauth,
                                       exchange=exchange, opener=lambda *args: {'items': []})
        CalendarConnector(self.raw, GoogleCalendar(transport), registry=registry).query(
            OWNER, '2026-10-09T00:00:00+00:00', '2026-10-10T00:00:00+00:00', 'UTC')
        _store, _registry, oauth = self.calendar()
        oauth.refresh(OWNER, exchange)
        _store, _registry, oauth = self.calendar()
        oauth.disconnect(OWNER, lambda credential: None)
        self.assert_never_under_a_lock()
        self.assertGreaterEqual(len(self.asked), 6)

    def test_gmail_lifecycle_and_requests(self):
        exchange = Exchange(GMAIL_READONLY_SCOPE)

        def gmail():
            store = EncryptedGmailSecretStore(self.raw, self.provider)
            registry = ConnectorRegistry(store, (GMAIL_CONNECTOR,), clock=lambda: self.clock[0])
            return GmailConnector(store, 'n.apps.googleusercontent.com', 'http://127.0.0.1:8787/oauth/gmail/callback',
                                  registry=registry, transport=lambda *args: {'messages': []},
                                  now=lambda: self.clock[0], allow_localhost=True, token_exchange=exchange)

        offer = gmail().begin_oauth(OWNER)
        owner = gmail().callback_owner(state_of(offer), [OWNER])
        gmail().complete_oauth(owner, {'state': state_of(offer), 'code': 'c'}, exchange)
        self.clock[0] += 7200
        gmail().search(OWNER, 'from:me')
        gmail().refresh(OWNER, exchange)
        gmail().mark_reauthentication_required(OWNER)
        gmail().disconnect(OWNER, lambda credential: None)
        self.assert_never_under_a_lock()
        self.assertGreaterEqual(len(self.asked), 7)

    def test_wiring_and_reading_empty_slots_still_never_ask(self):
        store, registry, oauth = self.calendar()
        transport = calendar_transport(store, registry, OWNER, now=lambda: self.clock[0])
        with self.assertRaises(Exception):
            CalendarConnector(self.raw, GoogleCalendar(transport), registry=registry).query(
                OWNER, '2026-10-09T00:00:00+00:00', '2026-10-10T00:00:00+00:00', 'UTC')
        self.assertFalse(oauth.credential_current(OWNER))
        self.assertEqual(self.asked, [])


class OwnClientSettingsTest(unittest.TestCase):
    CLIENT = json.dumps({'installed': {'client_id': '123-abcdefgh.apps.googleusercontent.com',
                                       'client_secret': 'made-up-secret'}})
    OTHER = json.dumps({'installed': {'client_id': '999-zzzzzz.apps.googleusercontent.com',
                                      'client_secret': 'made-up-secret-2'}})

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = pathlib.Path(temp.name)
        self.store = QuickStore(str(self.root / 'data'))
        self.env = {'AGENTOS_GOOGLE_LOCAL_PORT': '9911', 'AGENTOS_GOOGLE_OAUTH_KEY': Fernet.generate_key().decode()}

    def connect_drive(self, service):
        oauth = service.drive_oauth
        owner = service.connector_callback_owner(DRIVE_CONNECTOR_ID)
        oauth.complete_oauth(owner, {'state': state_of(oauth.begin_oauth(owner)), 'code': 'c'},
                             Exchange(DRIVE_READONLY_SCOPE))
        return owner

    def test_concurrent_first_saves_install_one_client(self):
        service = configured_service(self.store, self.env)
        installer = service.google_client_installer
        installs = []

        def slow_installer(client):
            installs.append(client[0])
            time.sleep(0.2)
            installer(client)

        service.google_client_installer = slow_installer
        threads = [threading.Thread(target=service.save_google_client, args=({'client_json': value},))
                   for value in (self.CLIENT, self.OTHER)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        self.assertEqual(len(installs), 1)
        installed = installs[0]
        self.assertEqual(service.google_client_id, installed)
        for holder in (service.gmail, service.calendar_oauth, service.drive_oauth):
            self.assertEqual(holder.client_id, installed)
        # Whichever save landed last is what a restart uses, and Settings says so truthfully.
        saved = json.loads(json.dumps(self.store.secret('google_oauth_client')))['installed']['client_id']
        self.assertEqual(service.google_client_status()['restart_required'], saved != installed)

    def test_an_invalid_configured_key_is_named_clearly(self):
        service = configured_service(self.store, {**self.env, 'AGENTOS_GOOGLE_OAUTH_KEY': 'not-a-key'})
        with self.assertRaises(ValueError) as caught:
            service.save_google_client({'client_json': self.CLIENT})
        self.assertIn('AGENTOS_GOOGLE_OAUTH_KEY', str(caught.exception))
        self.assertIsNone(service.drive_oauth)

    def test_a_key_store_problem_is_shown_in_settings(self):
        service = configured_service(self.store, self.env)
        service.save_google_client({'client_json': self.CLIENT})
        self.assertIsNone(service.google_client_status()['key_problem'])
        service.drive_oauth.store.key_problem = 'local_key_unavailable'
        self.assertEqual(service.google_client_status()['key_problem'], 'unavailable')

    def test_a_saved_client_is_removed_after_its_services_are_disconnected(self):
        from personal_agent.quickstart_service import ConnectorHandoff  # noqa: F401  (service import side effects)
        service = configured_service(self.store, self.env)
        service.save_google_client({'client_json': self.CLIENT})
        owner = self.connect_drive(service)
        self.assertFalse(service.google_client_status()['removable'])
        with self.assertRaises(ValueError) as caught:
            service.remove_google_client()
        self.assertIn('연결을 해제', str(caught.exception))
        self.assertTrue(self.store.secret('google_oauth_client'))
        service.drive_oauth.disconnect(owner, lambda credential: None)
        self.assertTrue(service.google_client_status()['removable'])
        status = service.remove_google_client()
        self.assertEqual((status['configured'], status['saved'], status['removable']), (False, False, False))
        self.assertEqual(self.store.secret('google_oauth_client'), '')
        self.assertIsNone(service.drive_oauth)
        self.assertIsNone(service.gmail)
        self.assertIsNone(service.calendar_oauth)
        self.assertEqual(service.connector_connect_url(DRIVE_CONNECTOR_ID), '')
        # Nothing comes back after a restart, and a client can be added again.
        restarted = configured_service(self.store, self.env)
        self.assertIsNone(restarted.drive_oauth)
        self.assertTrue(restarted.save_google_client({'client_json': self.CLIENT})['configured'])

    def test_removal_waits_for_an_unconfirmed_google_revocation(self):
        service = configured_service(self.store, self.env)
        service.save_google_client({'client_json': self.CLIENT})
        self.connect_drive(service)
        answers = [OSError('no answer')]

        def google(url, body, headers):
            answer = answers.pop(0) if answers else (200, b'')
            if isinstance(answer, Exception):
                raise answer
            return answer

        service.google_revoke_transport = google
        preview = service.google_disconnect_preview({'connector_id': DRIVE_CONNECTOR_ID}, 'owner-session')
        service.google_disconnect({'connector_id': DRIVE_CONNECTOR_ID, 'confirmation': preview['confirmation']},
                                  'owner-session')
        self.assertTrue(service.google_revocations()['pending_provider_revocations'])
        self.assertFalse(service.google_client_status()['removable'])
        with self.assertRaises(ValueError) as caught:
            service.remove_google_client()
        self.assertIn('권한 취소', str(caught.exception))
        self.assertIsNotNone(service.drive_oauth)
        # Google confirms on retry; then the client can go.
        service.retry_google_revocation({'connector_id': DRIVE_CONNECTOR_ID})
        self.assertEqual(service.google_revocations()['pending_provider_revocations'], [])
        self.assertFalse(service.remove_google_client()['saved'])

    def test_removal_rechecks_under_the_oauth_lock_a_connection_committed_meanwhile(self):
        service = configured_service(self.store, self.env)
        service.save_google_client({'client_json': self.CLIENT})
        original = service._own_client_removal_refusal

        def connect_in_between():
            # The full check passed; an authorization completes before removal locks in.
            refusal = original()
            self.connect_drive(service)
            return refusal

        service._own_client_removal_refusal = connect_in_between
        with self.assertRaises(ValueError) as caught:
            service.remove_google_client()
        self.assertIn('연결을 해제', str(caught.exception))
        self.assertIsNotNone(service.drive_oauth)
        self.assertTrue(self.store.secret('google_oauth_client'))

    def test_removal_waits_for_an_authorization_completing_under_the_oauth_lock(self):
        service = configured_service(self.store, self.env)
        service.save_google_client({'client_json': self.CLIENT})
        entered = threading.Event()
        results = []
        lock = service.drive_oauth.oauth_lock

        def completing():
            with lock:
                entered.set()
                time.sleep(0.3)
                self.connect_drive(service)

        def removing():
            entered.wait(5)
            try:
                results.append(service.remove_google_client())
            except ValueError as exc:
                results.append(str(exc))

        threads = [threading.Thread(target=completing), threading.Thread(target=removing)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], str)
        self.assertIsNotNone(service.drive_oauth)

    def test_a_saved_client_that_is_not_the_running_one_says_restart(self):
        service = configured_service(self.store, self.env)
        self.assertFalse(service.save_google_client({'client_json': self.CLIENT})['restart_required'])
        status = service.save_google_client({'client_json': self.OTHER})
        self.assertTrue(status['restart_required'])
        self.assertEqual(status['client_hint'], '…cdefgh')

    def test_removal_is_refused_with_a_reason_when_not_applicable(self):
        service = configured_service(self.store, self.env)
        with self.assertRaises(ValueError) as caught:
            service.remove_google_client()
        self.assertIn('저장돼 있지 않습니다', str(caught.exception))
        path = self.root / 'client.json'
        path.write_text(self.CLIENT)
        pinned = configured_service(self.store, {**self.env, 'AGENTOS_GOOGLE_CLIENT_FILE': str(path)})
        self.assertFalse(pinned.google_client_status()['removable'])
        with self.assertRaises(ValueError) as caught:
            pinned.remove_google_client()
        self.assertIn('AGENTOS_GOOGLE_CLIENT_FILE', str(caught.exception))

    def test_removal_over_http_notifies_the_conversation(self):
        from http.cookiejar import CookieJar
        from http.server import ThreadingHTTPServer
        from urllib.request import HTTPCookieProcessor, Request, build_opener
        from personal_agent import quickstart
        from personal_agent.quickstart import make_handler
        self.assertIn('/api/google/client/remove', quickstart.SETTINGS_WEB_CHANGES)
        service = configured_service(self.store, self.env)
        self.store.claim(self.store.bootstrap.read_text(), 'long-password-test')
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(service))
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        client = build_opener(HTTPCookieProcessor(CookieJar()))
        url = 'http://127.0.0.1:' + str(server.server_port)

        def request(path, body):
            req = Request(url + path, data=json.dumps(body).encode(),
                          headers={'Content-Type': 'application/json', 'Origin': url})
            with client.open(req, timeout=5) as response:
                return json.load(response)

        try:
            request('/api/login', {'password': 'long-password-test'})
            request('/api/google/client', {'client_json': self.CLIENT})
            result = request('/api/google/client/remove', {})
            self.assertEqual((result['configured'], result['saved']), (False, False))
            with self.store.db() as db:
                notices = [row['content'] for row in db.execute("SELECT content FROM messages WHERE role='assistant'")]
            self.assertIn('설정 화면에서 자체 Google client을(를) 지웠습니다.', notices[-1])
        finally:
            server.shutdown()
            thread.join()
            server.server_close()


if __name__ == '__main__':
    unittest.main()
