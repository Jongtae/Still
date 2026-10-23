"""CONNECT-CODE-01 (#1380) slice 2: code-run secret sets, model-free.

The owner enters named values on the existing one-time input page; code_run
receives them masked (or, when declared in_code, as real values limited to the
set's hosts) and may send only reads.  No service is named: the fixtures are a
synthetic API at example hosts.
"""
import json
import types
import unittest
from unittest import mock

from personal_agent import api_input, api_requests, code_run, phone_input
from personal_agent.api_credentials import ApiCredentials, credential_values
from personal_agent.api_requests import ApiError, slots
from personal_agent.browser_jar import MemoryKey
from personal_agent.code_run import CodeRunError, CodeRunner
from test_code_run import FakeProcess
from test_phone_input import PhoneInputServiceCase

HOST = 'api.example.com:9443'
CODE_SET = {'name': 'example-broker', 'hosts': [HOST], 'money': True, 'note': 'Read-only account data',
            'fields': [{'name': 'APP_KEY', 'label': 'App key', 'use': 'header_or_body'},
                       {'name': 'APP_SECRET', 'label': 'App secret', 'use': 'header_or_body'},
                       {'name': 'ACCOUNT', 'label': 'Account number', 'use': 'in_code'}],
            'reads': [f'POST {HOST}/oauth2/token']}
VALUES = {'APP_KEY': 'key-value-0001', 'APP_SECRET': 'secret-value-0002', 'ACCOUNT': '12345678-01'}


def code_run_arguments(hosts):
    from personal_agent.code_run import _arguments
    return _arguments({'code': 'print(1)', 'hosts': hosts})[2]


class Validation(unittest.TestCase):
    def test_named_fields_are_a_credential_shape(self):
        self.assertEqual(credential_values(VALUES), tuple(VALUES.values()))
        # #1380 live: an account product code such as "01" is a valid value of a code-run set.
        self.assertEqual(credential_values({'PRODUCT_CODE': '01'}), ('01',))
        for bad in ({'app_key': 'x' * 8}, {'APP_KEY': ''}, {'APP_KEY': 'a\nb'}, {f'K{i}': 'x' * 8 for i in range(9)}):
            with self.subTest(bad=list(bad)[:2]), self.assertRaises(ValueError):
                credential_values(bad)
        with self.assertRaises(ValueError):
            credential_values('short')  # a single-secret slot keeps its 8-character floor

    def test_code_set_rejects_reads_off_its_hosts_and_bad_fields(self):
        api_requests.code_set(CODE_SET, [HOST])
        cases = [{**CODE_SET, 'reads': ['POST other.example.net/token']},
                 {**CODE_SET, 'reads': ['DELETE api.example.com:9443/orders']},
                 {**CODE_SET, 'fields': [{'name': 'APP_KEY', 'label': 'x', 'use': 'anywhere'}]},
                 {**CODE_SET, 'fields': CODE_SET['fields'] + [CODE_SET['fields'][0]]}]
        for case in cases:
            with self.subTest(case=str(case)[-60:]), self.assertRaises(ApiError):
                api_requests.code_set(case, [HOST])

    def test_local_and_private_hosts_are_refused(self):
        # #1445: a set's values reach code that calls its hosts; a local address would reach the owner's Mac.
        for host in ('127.0.0.1:8787', 'localhost:8787', '[::1]:9000', 'printer.local', 'nas.internal', '10.0.0.5'):
            with self.subTest(host=host), self.assertRaises(ApiError) as caught:
                api_requests.code_set({'fields': ['APP_KEY']}, [host])
            self.assertIn('내부 주소', str(caught.exception))
        for host in ('printer.local', 'localhost'):
            with self.subTest(host=host), self.assertRaises(CodeRunError):
                code_run_arguments(host)
        self.assertEqual(code_run_arguments('api.example.com'), ['api.example.com'])

    def test_mechanical_differences_are_normalized(self):
        loose = {'fields': ['app-key', {'name': 'app secret', 'description': 'ignored', 'label': '앱 시크릿 ' * 6},
                            {'name': 'ACCOUNT', 'use': 'in_code'}],
                 'reads': [f'post https://{HOST}/oauth2/token']}
        result = api_requests.code_set(loose, [HOST])
        self.assertEqual([item['name'] for item in result['fields']], ['APP_KEY', 'APP_SECRET', 'ACCOUNT'])
        self.assertEqual([item['use'] for item in result['fields']], ['header_or_body', 'header_or_body', 'in_code'])
        self.assertEqual(result['fields'][0]['label'], 'APP_KEY')
        self.assertEqual(result['reads'], [f'POST {HOST}/oauth2/token'])

    def test_refusals_name_the_item_and_reason(self):
        cases = [({'fields': [{'name': 'APP_KEY'}, {'name': '1bad'}]}, 'fields[1].name'),
                 ({'fields': [{'name': 'APP_KEY', 'use': 'query'}]}, 'fields[0].use'),
                 ({'fields': [{'name': 'APP_KEY', 'label': 'x' * 81}]}, 'fields[0].label'),
                 ({'fields': ['A_KEY'], 'reads': ['GET api.example.com:9443/v1']}, 'reads[0]'),
                 ({'fields': ['A_KEY'], 'reads': ['POST other.example.net/token']}, 'other.example.net')]
        for value, expected in cases:
            with self.subTest(expected=expected), self.assertRaises(ApiError) as caught:
                api_requests.code_set(value, [HOST])
            self.assertIn(expected, str(caught.exception))

    def test_draft_defaults_optional_keys_and_names_unknown_ones(self):
        store = types.SimpleNamespace(config=lambda key, default=None: {}, secret_items=lambda prefix: {})
        with mock.patch('personal_agent.api_input.redact_known_secrets', lambda store, text: text):
            canonical = json.loads(api_input.value(store, json.dumps(
                {'name': 'Example_Broker', 'hosts': f'https://{HOST}/v1', 'fields': ['APP_KEY']})))
            self.assertEqual(canonical['hosts'], [HOST])
            self.assertEqual((canonical['name'], canonical['money'], canonical['reads'], canonical['note']),
                             ('example-broker', True, [], ''))
            with self.assertRaises(ValueError) as caught:
                api_input.value(store, json.dumps({**CODE_SET, 'endpoint': 'x'}))
            self.assertIn('endpoint', str(caught.exception))

    def test_input_draft_is_canonical_and_public_only(self):
        store = types.SimpleNamespace(config=lambda key, default=None: {}, secret_items=lambda prefix: {})
        with mock.patch('personal_agent.api_input.redact_known_secrets', lambda store, text: text):
            canonical = json.loads(api_input.value(store, json.dumps(CODE_SET)))
            self.assertEqual(canonical['reads'], CODE_SET['reads'])
            self.assertTrue(api_input.code_draft(canonical))
            with self.assertRaises(ValueError):
                api_input.value(store, json.dumps({**CODE_SET, 'probe': {}}))


class Storage(unittest.TestCase):
    def setUp(self):
        import pathlib
        import tempfile
        from personal_agent.quickstart_store import QuickStore
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = QuickStore(str(pathlib.Path(temp.name) / 'data'))
        self.store.api_slot_key = MemoryKey()

    def save(self, **changes):
        data = {**CODE_SET, **changes}
        return api_requests.save_slot(self.store, data['name'], data['hosts'], dict(VALUES), note=data['note'],
                                      money=data['money'], encrypted=True,
                                      code={'fields': data['fields'], 'reads': data['reads']})

    def test_a_saved_function_library_is_listed_with_its_set(self):
        # #1486: the next request on this set finds what worked last time, by name and signature.
        from personal_agent.procedures import ProcedureLibrary
        self.save()
        self.assertNotIn('saved functions', api_requests.context_line(self.store))
        library = ProcedureLibrary(self.store)
        library.propose('w1', 'example-broker', 'def holdings(account):\n    """Holdings."""\n',
                        [{'name': 'holdings', 'signature': '(account)', 'doc': 'Holdings of one account.'}])
        library.settle('w1', True)
        line = api_requests.context_line(self.store)
        self.assertIn('saved functions v1 (import lib_example_broker; ref procedure:', line)
        self.assertIn('holdings(account) - Holdings of one account.]', line)

    def test_saved_set_is_code_only_and_listed_by_environment_name(self):
        described = self.save()
        self.assertEqual(described['env'], {'EXAMPLE_BROKER_APP_KEY': 'header_or_body',
                                            'EXAMPLE_BROKER_APP_SECRET': 'header_or_body',
                                            'EXAMPLE_BROKER_ACCOUNT': 'in_code'})
        slot = slots(self.store)['example-broker']
        self.assertEqual(slot['kind'], 'code')
        line = api_requests.context_line(self.store)
        self.assertIn('code_run secrets only', line)
        self.assertNotIn('key-value-0001', line)
        # #1504: a worker reads what Still enforces, never the setup-time note that can go stale.
        self.assertIn('sent once after the owner approves', line)
        self.assertNotIn('Read-only account data', line)
        # #1566: an add-only redraft must repeat the stored money flag, so the worker sees it.
        self.assertIn('money: true', line)
        with self.assertRaises(ApiError) as caught:
            api_requests.ApiRequests(self.store, 'work').call(
                {'slot': 'example-broker', 'url': f'https://{HOST}/v1/balance', 'method': 'GET', 'effect': 'read'})
        self.assertEqual(caught.exception.code, 'api_slot_code_only')

    def test_values_must_match_the_declared_fields_and_stay_encrypted(self):
        with self.assertRaises(ApiError):
            api_requests.save_slot(self.store, 'example-broker', [HOST], {'APP_KEY': 'key-value-0001'}, encrypted=True,
                                   note='', money=True, code={'fields': CODE_SET['fields'], 'reads': []})
        with self.assertRaises(ApiError):
            api_requests.save_slot(self.store, 'example-broker', [HOST], dict(VALUES), encrypted=False,
                                   note='', money=True, code={'fields': CODE_SET['fields'], 'reads': []})
        self.save()
        slot = slots(self.store)['example-broker']
        self.assertEqual(ApiCredentials(self.store).read('example-broker', slot), VALUES)
        self.assertTrue(set(VALUES.values()) <= set(api_requests.encrypted_secret_values(self.store)))

    def test_short_values_are_stored_but_not_redacted(self):
        from personal_agent.current_context import redact_known_secrets
        api_requests.save_slot(self.store, 'short-set', [HOST], {'APP_KEY': VALUES['APP_KEY'], 'PRODUCT': '01'},
                               note='', money=True, encrypted=True,
                               code={'fields': ['APP_KEY', {'name': 'PRODUCT', 'use': 'in_code'}], 'reads': []})
        text = redact_known_secrets(self.store, f"key={VALUES['APP_KEY']} year=2001 product=01")
        self.assertEqual(text, 'key=[redacted] year=2001 product=01')

    def test_changing_reviewed_reads_rebinds_the_stored_values(self):
        self.save()
        slot = dict(slots(self.store)['example-broker'])
        slot['reads'] = [f'POST {HOST}/v1/orders']
        with self.assertRaises(ValueError):
            ApiCredentials(self.store).read('example-broker', slot)

    def runner(self, process):
        return CodeRunner(self.store, 'work-1', popen=lambda *a, **k: process, node='/usr/bin/node',
                          install=lambda store, node: store.root)

    def test_masked_values_reviewed_reads_and_in_code_values(self):
        self.save()
        process = FakeProcess(requests=[
            {'method': 'POST', 'host': HOST, 'path': '/oauth2/token'},
            {'method': 'GET', 'host': HOST, 'path': '/v1/balance/12345678-01'},
            {'method': 'POST', 'host': HOST, 'path': '/v1/orders'},
            {'method': 'GET', 'host': 'public.example.org', 'path': '/'}],
            result={'stdout': 'balance for 12345678-01', 'stderr': ''})
        result = self.runner(process).run({'code': 'print(1)', 'hosts': '', 'secrets': 'example-broker'})
        self.assertEqual([item['allow'] for item in process.decisions], [True, True, False, False])
        masked = {item['name']: item for item in process.config['masked']}
        self.assertEqual(set(masked), {'EXAMPLE_BROKER_APP_KEY', 'EXAMPLE_BROKER_APP_SECRET'})
        self.assertEqual(masked['EXAMPLE_BROKER_APP_KEY']['injectHosts'], ['api.example.com'])
        self.assertEqual(process.config['env']['EXAMPLE_BROKER_ACCOUNT'], '12345678-01')
        self.assertNotIn('EXAMPLE_BROKER_APP_KEY', process.config['env'])
        self.assertEqual(process.config['allowedDomains'], [HOST])
        # Stored values never come back: not in output, not in the request log, not in Evidence.
        self.assertNotIn('12345678-01', result['stdout'])
        self.assertNotIn('12345678-01', json.dumps(result['requests']))
        self.assertEqual(result['secrets'], ['example-broker'])

    def test_in_code_values_keep_the_run_on_the_set_hosts(self):
        self.save()
        with self.assertRaises(CodeRunError) as caught:
            self.runner(FakeProcess()).run({'code': 'print(1)', 'hosts': 'public.example.org', 'secrets': 'example-broker'})
        self.assertEqual(caught.exception.code, 'code_run_argument_invalid')

    def test_masked_only_sets_may_add_public_hosts(self):
        values = {key: VALUES[key] for key in ('APP_KEY', 'APP_SECRET')}
        api_requests.save_slot(self.store, 'example-broker', [HOST], values, note='', money=True, encrypted=True,
                               code={'fields': CODE_SET['fields'][:2], 'reads': []})
        process = FakeProcess(result={'stdout': '', 'stderr': ''})
        self.runner(process).run({'code': 'print(1)', 'hosts': 'public.example.org', 'secrets': 'example-broker'})
        self.assertEqual(process.config['allowedDomains'], [HOST, 'public.example.org'])

    def test_a_bare_set_host_covers_its_other_ports(self):
        api_requests.save_slot(self.store, 'bare-set', ['api.example.com'], {'APP_KEY': VALUES['APP_KEY']}, note='',
                               money=True, encrypted=True,
                               code={'fields': [{'name': 'APP_KEY', 'label': 'k', 'use': 'header_or_body'}],
                                     'reads': ['POST api.example.com/oauth2/token']})
        process = FakeProcess(requests=[
            {'method': 'POST', 'host': 'api.example.com:9443', 'path': '/oauth2/token'},
            {'method': 'GET', 'host': 'api.example.com:9443', 'path': '/v1/balance'},
            {'method': 'POST', 'host': 'api.example.com:9443', 'path': '/v1/orders'},
            {'method': 'GET', 'host': 'other.example.com:9443', 'path': '/'}],
            result={'stdout': '', 'stderr': ''})
        self.runner(process).run({'code': 'print(1)', 'secrets': 'bare-set'})
        self.assertEqual([item['allow'] for item in process.decisions], [True, True, False, False])
        # A refusal names what is allowed, so the worker can correct a path instead of giving up.
        self.assertIn('POST api.example.com/oauth2/token', process.decisions[2]['reason'])
        self.assertIn('api.example.com', process.decisions[3]['reason'])

    def test_a_replaced_or_removed_run_state_token_stays_redacted(self):
        """Codex review on #259: a rotated access token in saved run state is retired, not forgotten."""
        from personal_agent.api_credentials import ApiCredentials
        from personal_agent.current_context import redact_known_secrets
        self.save()
        slot = api_requests.slots(self.store)['example-broker']
        old, new = 'old-access-token-' + 'a' * 20, 'new-access-token-' + 'b' * 20
        credentials = ApiCredentials(self.store)
        self.assertTrue(credentials.write_state('example-broker', slot, json.dumps({'access_token': old})))
        self.assertTrue(credentials.write_state('example-broker', slot, json.dumps({'access_token': new})))
        self.assertEqual(redact_known_secrets(self.store, f'token {old} then {new}'), 'token [redacted] then [redacted]')
        api_requests.remove_slot(self.store, 'example-broker')
        self.assertEqual(redact_known_secrets(self.store, f'later {new}'), 'later [redacted]')

    def test_an_unreadable_credential_keeps_the_slot_for_a_later_removal(self):
        """Codex review on #259: retirement runs before the slot's metadata goes, so a retry still finds it."""
        from personal_agent.api_credentials import ApiCredentials, CredentialError
        self.save()
        with mock.patch.object(ApiCredentials, 'values', side_effect=CredentialError()):
            with self.assertRaises(CredentialError):
                api_requests.remove_slot(self.store, 'example-broker')
        self.assertIn('example-broker', api_requests.slots(self.store))
        self.assertTrue(api_requests.remove_slot(self.store, 'example-broker'))
        self.assertNotIn('example-broker', api_requests.slots(self.store))

    def test_run_state_persists_encrypted_and_its_token_is_redacted(self):
        # #1380 live: each run requested a new token and the vendor refused the second within a minute.
        self.save()
        token = 'access-token-' + 'x' * 40
        seen = []

        class Writer(FakeProcess):
            def _lines(inner):
                path = inner.config['env']['EXAMPLE_BROKER_STATE_FILE']
                with open(path, encoding='utf-8') as handle:
                    seen.append(handle.read())
                with open(path, 'w', encoding='utf-8') as handle:
                    json.dump({'access_token': token, 'expires_at': '2026-10-13 09:00:00'}, handle)
                yield from FakeProcess._lines(inner)

        first = self.runner(Writer(result={'stdout': f'issued {token} ok', 'stderr': ''})).run(
            {'code': 'print(1)', 'secrets': 'example-broker'})
        self.assertEqual(first['state_saved'], {'example-broker': True})
        self.assertEqual(first['stdout'], 'issued [redacted] ok', 'a saved token is redacted like a stored value')
        self.runner(Writer(result={'stdout': '2026-10-13 09:00:00', 'stderr': ''})).run(
            {'code': 'print(1)', 'secrets': 'example-broker'})
        self.assertEqual(seen[0], '{}')
        self.assertEqual(json.loads(seen[1])['access_token'], token, 'the next run reads the saved state')
        self.assertNotIn(token, json.dumps(self.store.secret_items('api_credential_state:')), 'stored encrypted')
        # Changed set metadata does not hand over an older state; removing the set removes it.
        self.save(reads=[])
        third = Writer(result={'stdout': '', 'stderr': ''})
        self.runner(third).run({'code': 'print(1)', 'secrets': 'example-broker'})
        self.assertEqual(seen[2], '{}')
        api_requests.remove_slot(self.store, 'example-broker')
        self.assertEqual(self.store.secret_items('api_credential_state:'), {})

    def test_an_unknown_set_asks_for_setup(self):
        with self.assertRaises(CodeRunError) as caught:
            self.runner(FakeProcess()).run({'code': 'print(1)', 'secrets': 'missing-set'})
        self.assertEqual((caught.exception.code, caught.exception.requires), ('needs_setup', 'api-slot:missing-set'))


class FakeApprovals:
    """The Work's per-action approval surface: ``consume`` a granted binding once, ``request`` records the ask."""

    def __init__(self):
        self.granted, self.requested = [], []

    def consume(self, binding):
        if binding in self.granted:
            self.granted.remove(binding)
            return True
        return False

    def request(self, binding, label):
        self.requested.append((binding, label))

    def api_effect_current(self):
        return True


class OwnerApprovedStateChange(Storage):
    """#1456: a state-changing request to a set's host is sent once, only with the owner's exact approval."""

    ORDER = '{"CANO":"12345678-01","PDNO":"360750","ORD_QTY":"2","ORD_UNPR":"25900","KEY":"{{EXAMPLE_BROKER_APP_KEY}}"}'

    def setUp(self):
        super().setUp()
        self.save()
        self.work = self.store.enqueue('360750 2주 25,900원에 예약매수해줘', 'order-1')
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (self.work,))
        self.approvals = FakeApprovals()

    def run_order(self, body=ORDER, host=HOST, path='/uapi/domestic-stock/v1/trading/order-resv'):
        process = FakeProcess(requests=[{'method': 'POST', 'host': host, 'path': path, 'query': '', 'bodyText': body}],
                              result={'stdout': 'ok', 'stderr': ''})
        runner = CodeRunner(self.store, self.work, popen=lambda *a, **k: process, node='/usr/bin/node',
                            install=lambda store, node: store.root, approvals=self.approvals)
        return process, runner.run({'code': 'print(1)', 'hosts': '', 'secrets': 'example-broker'})

    def dispatches(self):
        with self.store.db() as db:
            return [row['status'] for row in db.execute("SELECT status FROM tool_events WHERE tool='api_dispatch'")]

    def test_the_first_run_asks_the_owner_for_exactly_that_request(self):
        process, result = self.run_order()
        self.assertEqual([item['allow'] for item in process.decisions], [False])
        self.assertIn('asked the owner to approve exactly this request', process.decisions[0]['reason'])
        [(binding, label)] = self.approvals.requested
        self.assertEqual(binding['action'], 'code_run')
        self.assertIn('/uapi/domestic-stock/v1/trading/order-resv', label)
        self.assertIn('보낼 요청: POST ', label)
        self.assertIn('"ORD_QTY":"2"', label)
        self.assertIn('{{EXAMPLE_BROKER_APP_KEY}}', label, 'a masked value is shown by its name')
        self.assertNotIn('12345678-01', label, 'a stored value never reaches the approval text')
        self.assertIn('돈이 오가는 요청이에요.', label, 'money is said plainly')
        self.assertNotIn('payment', label, 'no internal effect name')
        self.assertEqual(result['approval_requested'][0]['method'], 'POST')
        kept = code_run.evidence(result)
        self.assertEqual(kept['refused_because'], 'owner-approval-requested')
        self.assertEqual(self.dispatches(), [], 'nothing was sent')

    def test_an_approved_request_is_sent_once_and_never_again(self):
        self.run_order()
        [(binding, _label)] = self.approvals.requested
        self.approvals.granted.append(binding)
        process, result = self.run_order()
        self.assertEqual([item['allow'] for item in process.decisions], [True])
        self.assertEqual(result['effects'], [{'method': 'POST', 'host': HOST, 'path': '/uapi/domestic-stock/v1/trading/order-resv'}])
        self.assertEqual(self.dispatches(), ['observed'])
        self.assertIn('effects', code_run.evidence(result))
        from personal_agent.agent_runtime import EFFECT_CHANGED, record_effect
        self.assertEqual(record_effect('code_run', {'evidence': code_run.evidence(result)}, 'succeeded'), EFFECT_CHANGED)
        # The same request again, even with a fresh approval, is a replay.
        self.approvals.granted.append(binding)
        process, _result = self.run_order()
        self.assertEqual([item['allow'] for item in process.decisions], [False])
        self.assertEqual(self.dispatches(), ['observed'])

    def test_an_approval_covers_only_the_exact_request(self):
        self.run_order()
        self.approvals.granted.append(self.approvals.requested[0][0])
        process, _result = self.run_order(body=self.ORDER.replace('"2"', '"20"'))
        self.assertEqual([item['allow'] for item in process.decisions], [False])
        self.assertEqual(len(self.approvals.requested), 2, 'another quantity is another approval')
        self.assertEqual(self.dispatches(), [])

    def test_a_body_that_cannot_be_shown_or_another_host_is_refused_without_asking(self):
        process, _result = self.run_order(body=None)
        self.assertEqual([item['allow'] for item in process.decisions], [False])
        self.assertIn('cannot be shown', process.decisions[0]['reason'])
        process, _result = self.run_order(host='public.example.org')
        self.assertEqual([item['allow'] for item in process.decisions], [False])
        self.assertEqual(self.approvals.requested, [])

    def test_the_ais_description_comes_first_and_a_declared_effect_is_no_obstacle(self):
        """#1470: the owner reads the AI's words first; the exact request below is what is approved."""
        process = FakeProcess(requests=[{'method': 'POST', 'host': HOST, 'path': '/v1/order', 'query': '', 'bodyText': self.ORDER}])
        CodeRunner(self.store, self.work, popen=lambda *a, **k: process, node='/usr/bin/node',
                   install=lambda store, node: store.root, approvals=self.approvals).run(
            {'code': 'print(1)', 'hosts': '', 'secrets': 'example-broker', 'effect': 'payment',
             'approval_note': '  360750  2주를 25,900원에 내일 아침 예약매수해요. '})
        label = self.approvals.requested[0][1]
        self.assertTrue(label.startswith('360750 2주를 25,900원에 내일 아침 예약매수해요.\n\n'))
        self.assertIn('보낼 요청: POST ', label)
        self.assertIn('/v1/order', label)
        self.assertIn('"ORD_QTY":"2"', label, 'the exact request is still shown')

    def test_without_an_approval_surface_every_state_change_stays_refused(self):
        process = FakeProcess(requests=[{'method': 'POST', 'host': HOST, 'path': '/v1/orders', 'bodyText': '{}'}])
        CodeRunner(self.store, self.work, popen=lambda *a, **k: process, node='/usr/bin/node',
                   install=lambda store, node: store.root).run({'code': 'print(1)', 'hosts': '', 'secrets': 'example-broker'})
        self.assertEqual([item['allow'] for item in process.decisions], [False])
        self.assertIn('only GET/HEAD', process.decisions[0]['reason'])


class InputPage(PhoneInputServiceCase):
    def setUp(self):
        super().setUp()
        self.store.api_slot_key = MemoryKey()
        self.settings = self.service.settings_orchestrator
        self.job = self.store.job(self.store.enqueue('Show my account balances', 'original'))

    def test_owner_enters_named_values_and_the_original_work_resumes_once(self):
        draft = self.service.settings_tools(self.job)('settings_change', {
            'category': 'api_connections', 'setting': 'connect', 'value': json.dumps(CODE_SET), 'continue_work': 'yes'})
        row = self.settings._drafts()[draft['draft_id']]
        self.assertIn('코드 실행용 비밀값 묶음', row['effect'])
        self.assertIn('Account number (코드 안에서 직접 사용)', row['effect'])
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='partial' WHERE id=?", (self.job['id'],))
        result = self.settings.confirm(self.service.settings_owner(self.job), self.job['channel'],
                                       draft['draft_id'], row['digest'])
        self.assertEqual(result['state'], 'awaiting-input')
        session = self.service.phone_input_session()
        page = phone_input.page(session, 'nonce')
        self.assertIn('id="f_APP_SECRET"', page)
        self.assertIn('App secret', page)
        cookie = self.open_page(session)
        status, body, _ = self.request(f'{phone_input.API_PATH}?code={session.code}', 'POST',
                                       {'APP_KEY': VALUES['APP_KEY']}, tunneled=False, cookie=cookie)
        self.assertEqual(status, 400, body)
        status, body, _ = self.request(f'{phone_input.API_PATH}?code={session.code}', 'POST',
                                       dict(VALUES), tunneled=False, cookie=cookie)
        self.assertEqual(status, 200, body)
        self.assertEqual(slots(self.store)['example-broker']['kind'], 'code')
        self.assertEqual(self.store.job(self.job['id'])['status'], 'queued')
        self.assertEqual(self.settings._drafts()[draft['draft_id']]['state'], 'applied')
        self.assertNotIn('key-value-0001', json.dumps(self.settings._drafts()))
        # #1380 live: the resumed Work is told the owner completed this prerequisite, so a request
        # that was only this setup is reported done instead of proposing it again.
        self.service.resume_settings_work()
        note = self.service.action_continuation_note(self.store.job(self.job['id']))
        self.assertIn('The owner completed the prerequisite proposed for this Work', note)
        self.assertIn('코드 실행용 비밀값 묶음', note)
        self.assertNotIn('key-value-0001', note)

    def _confirmed(self, value):
        draft = self.service.settings_tools(self.job)('settings_change', {
            'category': 'api_connections', 'setting': 'connect', 'value': json.dumps(value), 'continue_work': 'yes'})
        row = self.settings._drafts()[draft['draft_id']]
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='partial' WHERE id=?", (self.job['id'],))
        result = self.settings.confirm(self.service.settings_owner(self.job), self.job['channel'],
                                       draft['draft_id'], row['digest'])
        self.assertEqual(result['state'], 'awaiting-input')
        return row, self.service.phone_input_session()

    def _saved_without_account(self):
        keys_only = {**CODE_SET, 'fields': CODE_SET['fields'][:2]}
        api_requests.save_slot(self.store, keys_only['name'], keys_only['hosts'],
                               {name: VALUES[name] for name in ('APP_KEY', 'APP_SECRET')}, note=keys_only['note'],
                               money=True, encrypted=True, code={'fields': keys_only['fields'], 'reads': keys_only['reads']})

    def test_adding_an_owner_held_value_asks_only_for_it_and_keeps_the_stored_ones(self):
        # #1566 live: a set lacked an identifier only the owner holds; adding it re-asked every key.
        self._saved_without_account()
        row, session = self._confirmed(CODE_SET)
        self.assertIn('App key (코드에는 가려지고 보낼 때만 채움, 저장된 값 그대로)', row['effect'])
        self.assertIn('Account number (코드 안에서 직접 사용)', row['effect'])
        page = phone_input.page(session, 'nonce')
        self.assertIn('id="f_ACCOUNT"', page)
        self.assertNotIn('id="f_APP_KEY"', page)
        self.assertIn('이미 저장된 값(App key, App secret)', page)
        cookie = self.open_page(session)
        status, body, _ = self.request(f'{phone_input.API_PATH}?code={session.code}', 'POST',
                                       dict(VALUES), tunneled=False, cookie=cookie)
        self.assertEqual(status, 400, body)  # a kept value is never accepted from the page again
        status, body, _ = self.request(f'{phone_input.API_PATH}?code={session.code}', 'POST',
                                       {'ACCOUNT': VALUES['ACCOUNT']}, tunneled=False, cookie=cookie)
        self.assertEqual(status, 200, body)
        slot = slots(self.store)['example-broker']
        self.assertEqual([item['name'] for item in slot['fields']], ['APP_KEY', 'APP_SECRET', 'ACCOUNT'])
        self.assertEqual(ApiCredentials(self.store).read('example-broker', slot), VALUES)
        self.assertEqual(self.store.job(self.job['id'])['status'], 'queued')
        self.assertNotIn(VALUES['APP_KEY'], json.dumps(self.settings._drafts()))

    def test_any_other_change_to_a_stored_set_asks_for_every_value(self):
        self._saved_without_account()
        for changed in ({**CODE_SET, 'hosts': [HOST, 'data.example.com']},
                        {**CODE_SET, 'money': False},
                        {**CODE_SET, 'fields': [{**CODE_SET['fields'][0], 'use': 'in_code'}, *CODE_SET['fields'][1:]]},
                        {**CODE_SET, 'fields': [CODE_SET['fields'][0], CODE_SET['fields'][2]]}):
            with self.subTest(changed=changed):
                self.assertEqual(api_input.kept_fields(self.store, changed), frozenset())
        self.assertEqual(api_input.kept_fields(self.store, CODE_SET), {'APP_KEY', 'APP_SECRET'})
        self.assertEqual(api_input.kept_fields(self.store, {**CODE_SET, 'name': 'other-set'}), frozenset())

    def test_labels_are_escaped_on_the_page(self):
        hostile = {**CODE_SET, 'fields': [{'name': 'APP_KEY', 'label': '<script>x</script>', 'use': 'header_or_body'}]}
        draft = self.service.settings_tools(self.job)('settings_change', {
            'category': 'api_connections', 'setting': 'connect', 'value': json.dumps(hostile), 'continue_work': 'yes'})
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='partial' WHERE id=?", (self.job['id'],))
        self.settings.confirm(self.service.settings_owner(self.job), self.job['channel'], draft['draft_id'],
                              self.settings._drafts()[draft['draft_id']]['digest'])
        page = phone_input.page(self.service.phone_input_session(), 'nonce')
        self.assertNotIn('<script>x</script>', page)
        self.assertIn('&lt;script&gt;', page)


class ClearSigning(unittest.TestCase):
    """Owner choice 2026-10-14: the owner reads labelled values Still checked against the exact request."""

    BODY = json.dumps({'ITEM': 'A1', 'QTY': '2', 'NOTE': '', 'ACCOUNT': '12345678', 'EXTRA': 'x'})

    def lines(self, fields, redact=str):
        return code_run.approval_field_lines(code_run.parse_approval_fields(fields), self.BODY, 'side=buy', redact)

    def test_labelled_values_come_from_the_request_and_unnamed_ones_are_counted_without_alarm(self):
        shown, check = self.lines('item=ITEM; count=QTY=two units; side=side')
        self.assertEqual(shown, ['item A1', 'count 2 (two units)', 'side buy'])
        self.assertIn(code_run.APPROVAL_FIELDS_OK, check)
        self.assertIn(code_run.APPROVAL_FIELDS_UNLABELED.format(count=2), check)
        self.assertNotIn('⚠️', check)

    def test_a_label_may_state_the_value_and_a_fact_the_request_lacks_is_context(self):
        # Live 2026-10-14: an amendment's labels gave values ("수량=1주"), and the old price is
        # not in the request; neither is an alarm.
        shown, check = self.lines('item=A1; count=2 units; old price=25,900')
        self.assertEqual(shown, ['item A1', 'count 2 units'])
        self.assertIn(code_run.APPROVAL_FIELDS_CONTEXT.format(fields='old price'), check)
        self.assertNotIn('⚠️', check)

    def test_a_description_the_request_does_not_carry_at_all_is_the_one_warning(self):
        shown, check = self.lines('item=B9; price=PRICE')
        self.assertEqual(shown, [])
        self.assertIn(code_run.APPROVAL_FIELDS_MISMATCH, check)

    def test_empty_and_secret_fields_are_neither_shown_nor_counted(self):
        shown, check = self.lines('item=ITEM; count=QTY; note=NOTE; extra=EXTRA; side=side',
                                  redact=lambda value: value.replace('12345678', '[redacted]'))
        self.assertEqual(shown, ['item A1', 'count 2', 'extra x', 'side buy'])
        self.assertEqual(check, code_run.APPROVAL_FIELDS_OK)

    def test_fields_are_parsed_bounded(self):
        parsed = code_run.parse_approval_fields('; '.join(f'l{i}=F{i}' for i in range(40)) + '; broken; =X')
        self.assertEqual(len(parsed), code_run.MAX_APPROVAL_FIELDS)
        self.assertEqual(parsed[0], ('l0', 'F0', ''))


if __name__ == '__main__':
    unittest.main()
