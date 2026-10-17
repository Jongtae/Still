"""#1300: oauthlib serialization/parsing and encrypted token renewal, no live accounts."""
import base64
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, quote_plus

from personal_agent.api_requests import ApiRequests, ApiError, save_slot, slots
from personal_agent.browser_jar import MemoryKey
from personal_agent.quickstart_store import QuickStore
from personal_agent.current_context import redact_known_secrets

ISSUER = 'https://issuer.example/token'
URL = 'https://records.example/records'
SPEC = {'token_url': ISSUER, 'client_auth': 'basic', 'scope': ['records.read']}
PAIR = {'client_id': 'fixture:client', 'client_secret': 'fixture+private:credential'}
PROBE = {'url': URL, 'effect': 'read', 'required_fields': '["$.result"]'}


class OAuthSlots(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = QuickStore(Path(temporary.name))
        self.store.api_slot_key = MemoryKey()
        self.clock = [1000.0]
        self.calls, self.tokens = [], []
        self.mode, self.fault = 'basic', None
        self.during_exchange = lambda: None
        self.api = ApiRequests(self.store, 'original', transport=self.transport, clock=lambda: self.clock[0], sleep=lambda _: None)

    def transport(self, method, url, headers, body, timeout):
        self.calls.append((method, url, dict(headers), body))
        if url == ISSUER:
            self.assertEqual(method, 'POST')
            fields = parse_qs(body.decode())
            self.assertEqual(fields['grant_type'], ['client_credentials'])
            self.assertEqual(fields['scope'], ['records.read'])
            if self.mode == 'basic':
                encoded = quote_plus(PAIR['client_id']) + ':' + quote_plus(PAIR['client_secret'])
                self.assertEqual(headers['Authorization'], 'Basic ' + base64.b64encode(encoded.encode()).decode())
                self.assertNotIn('client_secret', fields)
            else:
                self.assertNotIn('Authorization', headers)
                self.assertEqual(fields['client_id'], [PAIR['client_id']])
                self.assertEqual(fields['client_secret'], [PAIR['client_secret']])
            self.during_exchange()
            if self.fault == 'timeout':
                raise TimeoutError(PAIR['client_secret'])
            if self.fault == 'redirect':
                return 302, {'Location': 'https://other.example/token'}, b'{}'
            token = 'issued-private-token-' + str(len(self.tokens))
            self.tokens.append(token)
            response = {'access_token': token, 'token_type': 'Bearer', 'expires_in': 60, 'scope': 'records.read'}
            if self.fault == 'scope':
                response['scope'] = 'records.write'
            if self.fault == 'expiry':
                response.pop('expires_in')
            if self.fault == 'type':
                response['token_type'] = 'MAC'
            return 200, {}, json.dumps(response).encode()
        self.assertEqual(url, URL)
        self.assertEqual(headers['Authorization'], 'Bearer ' + self.tokens[-1])
        if self.fault == 'unauthorized':
            return 401, {}, b'{}'
        return 200, {}, json.dumps({'account': 'account-b' if self.fault == 'account' else 'account-a',
                                   'result': 42, 'echo': [*PAIR.values(), self.tokens[-1]]}).encode()

    def save(self, **kwargs):
        return save_slot(self.store, 'records', ['records.example'], PAIR, encrypted=True,
                         oauth={**SPEC, 'client_auth': self.mode}, subject_field='$.account', subject_value='account-a',
                         _verify=lambda candidate: self.api.check_connection(candidate, PROBE), **kwargs)

    def read(self):
        return self.api.call({'slot': 'records', **PROBE})

    def test_active_cache_renewal_during_input_does_not_invalidate_publication(self):
        self.save()
        old = slots(self.store)['records']
        self.clock[0] += 61

        def verify(candidate):
            self.read()  # another Work renews the active connection's token
            self.api.check_connection(candidate, PROBE)

        save_slot(self.store, 'records', ['records.example'], PAIR, encrypted=True, oauth=SPEC,
                  subject_field='$.account', subject_value='account-a', _verify=verify)
        current = slots(self.store)['records']
        self.assertNotEqual(current['credential_ref'], old['credential_ref'])
        self.assertEqual(current['revision'], old['revision'] + 1)
        self.assertEqual(self.read()['data']['result'], 42)
        self.assertEqual(len(self.tokens), 3)

    def test_sdk_basic_and_body_forms_cache_and_renew_without_exposing_values(self):
        for mode in ('basic', 'body'):
            with self.subTest(mode=mode):
                self.mode = mode
                before = len(self.tokens)
                self.save()
                original = slots(self.store)['records']
                self.assertEqual(len(self.tokens), before + 1)
                result = self.read()
                self.assertEqual(result['data']['result'], 42)
                self.assertTrue(result['provenance']['subject_verified'])
                self.assertEqual(len(self.tokens), before + 1, 'readiness cache is reused by the original request')
                self.clock[0] += 61
                self.read()
                self.assertEqual(len(self.tokens), before + 2)
                current = slots(self.store)['records']
                self.assertEqual(current['credential_ref'], original['credential_ref'])
                self.assertEqual(current['revision'], original['revision'])
                self.assertNotEqual(current['token_cache'], original['token_cache'])
                all_values = [*PAIR.values(), *self.tokens]
                for value in all_values:
                    self.assertNotIn(value, self.store.secret_path.read_text())
                    self.assertNotIn(value, json.dumps(self.store.config('api_slots')))
                    self.assertNotIn(value, json.dumps(result))
                self.assertEqual(redact_known_secrets(self.store, ' '.join(all_values)),
                                 ' '.join('[redacted]' for _ in all_values))

    def test_failed_exchange_never_publishes_and_is_not_automatically_retried(self):
        for fault in ('timeout', 'redirect', 'scope', 'expiry', 'type'):
            with self.subTest(fault=fault):
                self.fault = fault
                before = len(self.calls)
                with self.assertRaises(ApiError) as refused:
                    self.save()
                self.assertEqual(len(self.calls), before + 1)
                self.assertEqual(refused.exception.code, 'api_token_unavailable')
                self.assertNotIn(PAIR['client_secret'], str(refused.exception))
                self.assertNotIn('records', slots(self.store))

    def test_readiness_subject_failure_preserves_old_slot_and_discards_new_cache(self):
        self.save()
        previous = slots(self.store)
        encrypted = self.store.secret_path.read_text()
        self.fault = 'account'
        with self.assertRaises(ApiError):
            self.save()
        self.assertEqual(slots(self.store), previous)
        self.assertEqual(self.store.secret_path.read_text(), encrypted)

    def test_revocation_during_renewal_prevents_api_read_and_cache_publication(self):
        self.save()
        self.clock[0] += 61
        before = len(self.calls)

        def revoke():
            rows = self.store.config('api_slots')
            rows['records']['revision'] += 1
            self.store.put('api_slots', rows)

        self.during_exchange = revoke
        with self.assertRaises(ApiError):
            self.read()
        self.assertEqual(len(self.calls), before + 1)

    def test_cached_expiry_is_cryptographically_bound(self):
        self.save()
        rows = self.store.config('api_slots')
        rows['records']['token_cache']['expires_at'] += 3600
        self.store.put('api_slots', rows)
        before = len(self.calls)
        with self.assertRaises(ApiError):
            self.read()
        self.assertEqual(len(self.calls), before)

    def test_active_oauth_revision_change_during_backoff_stops_the_retry(self):
        self.save()
        before = len(self.calls)

        def timeout(*args):
            self.transport(*args)
            raise TimeoutError('synthetic read failure')

        def revoke(_):
            rows = self.store.config('api_slots')
            rows['records']['revision'] += 1
            self.store.put('api_slots', rows)

        self.api.transport = timeout
        self.api.sleep = revoke
        with self.assertRaises(ApiError) as refused:
            self.read()
        self.assertEqual(refused.exception.code, 'needs_setup')
        self.assertEqual(len(self.calls), before + 1)

    def test_unauthorized_token_invalidates_cache_without_replaying_the_call(self):
        self.save()
        self.fault = 'unauthorized'
        before = len(self.calls)
        with self.assertRaises(ApiError):
            self.read()
        self.assertEqual(len(self.calls), before + 1)
        self.assertNotIn('token_cache', slots(self.store)['records'])
        self.fault = None
        self.read()
        self.assertEqual(len(self.tokens), 2)

    def test_changed_issuer_or_scope_cannot_reuse_credentials(self):
        import copy
        self.save()
        original = self.store.config('api_slots')
        for field, value in (('token_url', 'https://other.example/token'), ('scope', ['records.write']), ('client_auth', 'body')):
            with self.subTest(field=field):
                changed = copy.deepcopy(original)
                changed['records']['oauth'][field] = value
                self.store.put('api_slots', changed)
                before = len(self.calls)
                with self.assertRaises(ApiError):
                    self.read()
                self.assertEqual(len(self.calls), before)

    def test_money_approval_precedes_token_renewal_and_covers_only_one_change(self):
        from test_api_requests import Approvals
        self.api.work_id = self.store.enqueue('Make the authorized change', 'payment')
        self.save(money=True)
        self.clock[0] += 61
        self.api.approvals = Approvals()
        action = {'slot': 'records', 'url': URL, 'method': 'POST', 'effect': 'read', 'body': '{"change":1}'}
        before = len(self.calls)
        with self.assertRaises(ApiError):
            self.api.call(action)
        self.assertIn('payment', self.api.approvals.requested[-1][1])
        self.assertEqual(len(self.calls), before)
        self.api.approvals.approve_last()
        result = self.api.call(action)
        self.assertEqual(result['provenance']['effect'], 'payment')
        self.assertEqual(len(self.calls), before + 2, 'one token exchange and one approved change')
        with self.assertRaises(ApiError):
            self.api.call(action)
        self.assertEqual(len(self.calls), before + 2)

    def test_actual_input_page_hands_key_pair_to_sdk_and_resumes_original(self):
        from test_connect_api_input import ApiInput, PUBLIC
        from personal_agent import phone_input
        case = ApiInput(methodName='runTest')
        case.setUp()
        self.addCleanup(case.doCleanups)
        metadata = {**PUBLIC, 'hosts': ['records.example'], 'subject_field': '$.account',
                    'subject_value': 'account-a', 'probe': {'url': URL, 'required_fields': ['$.result']}, 'oauth': SPEC}
        case.service.api_input_transport = self.transport
        _, session, cookie = case.open_input(metadata)
        status, page, _ = case.request(f'{phone_input.PAGE_PATH}?code={session.code}', cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIn('client_id', page.decode())
        status, body, _ = case.request(f'{phone_input.API_PATH}?code={session.code}', 'POST', PAIR,
                                       cookie=cookie, tunneled=False)
        self.assertEqual(status, 200, body)
        self.assertEqual(case.store.job(case.job['id'])['status'], 'queued')
        active = ApiRequests(case.store, case.job['id'], transport=self.transport)
        result = active.call({'slot': PUBLIC['name'], **PROBE})
        self.assertEqual(result['data']['result'], 42)
        self.assertEqual(len(self.tokens), 1, 'input readiness token carries into the original request')
        for value in (*PAIR.values(), *self.tokens):
            self.assertNotIn(value, json.dumps(case.settings._drafts()))
            self.assertNotIn(value, json.dumps(case.store.task_events(case.job['id'])))
            self.assertNotIn(value, json.dumps(result))

    def test_existing_http_transport_carries_sdk_request_and_refuses_redirect(self):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from personal_agent.api_requests import http_request
        case = self
        unexpected = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def respond(self):
                if self.path not in ('/token', '/records'):
                    unexpected.append(self.path)
                raw_body = self.rfile.read(int(self.headers.get('Content-Length', '0'))) or None
                status, headers, payload = case.transport(self.command, ISSUER if self.path == '/token' else URL,
                                                          dict(self.headers.items()), raw_body, 15)
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, '/forbidden' if name == 'Location' else value)
                self.end_headers()
                self.wfile.write(payload)

            do_POST = respond
            do_GET = respond

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        def wire(method, url, headers, body, timeout):
            target = f'http://127.0.0.1:{server.server_port}' + ('/token' if url == ISSUER else '/records')
            return http_request(method, target, headers, body, timeout)

        self.api.transport = wire
        self.save()
        self.assertEqual(self.read()['data']['result'], 42)
        self.fault = 'redirect'
        self.clock[0] += 61
        with self.assertRaises(ApiError):
            self.read()
        self.assertFalse(unexpected)

    def test_expired_input_token_renews_in_actual_worker_and_returns_original_result(self):
        from test_connect_api_input import ApiInput, PUBLIC
        case = ApiInput(methodName='runTest')
        case.setUp()
        self.addCleanup(case.doCleanups)
        records = case.api

        def provider(method, url, headers, body, timeout):
            if url == ISSUER:
                return self.transport(method, url, headers, body, timeout)
            records.key = self.tokens[-1]
            return records(method, url, headers, body, timeout)

        case.api = provider
        case.service.api_input_transport = provider
        # The input observed readiness earlier; the actual worker's clock sees
        # that token as expired and must reacquire through the same SDK path.
        case.service.api_input_clock = lambda: self.clock[0]
        case.run_original({**PUBLIC, 'oauth': SPEC}, PAIR)
        self.assertEqual(len(self.tokens), 2)
        self.assertEqual(len(records.calls), 2, 'readiness and the original substantive read')
