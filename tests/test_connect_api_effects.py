"""#1301: generic API effect dispatch, real stores/callbacks, injected transport."""
import json
import email.utils
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest import mock

from personal_agent.api_requests import ApiError, ApiRequests, save_slot
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore

CALL = {'slot': 'synthetic', 'url': 'https://synthetic.example/change?item=private-item',
        'method': 'POST', 'effect': 'mutate', 'body': '{"value":"private-body"}'}


class ApiEffects(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = QuickStore(Path(temp.name))
        self.service = AgentService(self.store)
        self.work = self.store.enqueue('Apply the change and report the observed result', 'original')
        self.job = self.store.job(self.work)
        self.key = 'synthetic-private-credential'
        self.save()
        self.sent = []
        self.api = self.client()

    def save(self, **kwargs):
        save_slot(self.store, 'synthetic', ['synthetic.example'], self.key, **kwargs)

    def client(self, transport=None, work=None):
        work = work or self.work
        return ApiRequests(self.store, work, transport=transport or self.transport,
                           approvals=self.service.browser_approvals_for(self.store.job(work)))

    def transport(self, *args):
        self.sent.append(args)
        return 200, {'last-modified': email.utils.formatdate(usegmt=True)}, b'{"result":"changed"}'

    def assert_error(self, code, args=None, api=None):
        with self.assertRaises(ApiError) as caught:
            (api or self.api).call(args or CALL)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_nonpayment_runs_without_approval_and_restart_or_reconnect_cannot_replay(self):
        self.assertEqual(self.api.call(CALL)['data'], {'result': 'changed'})
        self.assertIsNone(self.service._browser_request(self.work))
        self.assertEqual(len(self.sent), 1)
        self.save()
        self.assert_error('api_effect_replayed', api=self.client())
        self.assertEqual(len(self.sent), 1)
        completed, unknown = self.service.connector_effect_progress(self.work)
        self.assertEqual(len(completed), 1)
        self.assertEqual(unknown, [])
        with self.store.db() as db:
            claim = db.execute("SELECT detail FROM tool_events WHERE tool='api_dispatch'").fetchone()[0]
        for private in ('private-item', 'private-body', self.key):
            self.assertNotIn(private, claim)

    def test_parallel_different_effect_waits_and_then_can_run_after_observed_result(self):
        entered, release = Event(), Event()
        def blocked(*args):
            self.sent.append(args)
            entered.set()
            self.assertTrue(release.wait(5))
            return 200, {}, b'{}'
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.client(blocked).call, CALL)
            try:
                self.assertTrue(entered.wait(5))
                self.assert_error('effect_unknown', {**CALL, 'body': '{"value":2}'})
            finally:
                release.set()
            future.result(timeout=5)
        self.api.call({**CALL, 'body': '{"value":2}'})
        self.assertEqual(len(self.sent), 2)

    def test_all_transport_failures_and_unreadable_results_remain_unknown_after_restart(self):
        for failure in (TimeoutError, RuntimeError, 'invalid-json', 'server-error', 'redirect'):
            with self.subTest(failure=failure):
                self.setUp()
                def failed(*args):
                    self.sent.append(args)
                    if isinstance(failure, type):
                        raise failure('must never be exposed')
                    return {'server-error': 500, 'redirect': 302}.get(failure, 200), {}, b'not-json'
                with self.assertRaises(ApiError) as caught:
                    self.client(failed).call(CALL)
                self.assertEqual(caught.exception.effect, 'unknown')
                self.assertNotIn('must never be exposed', str(caught.exception))
                self.assert_error('effect_unknown', api=self.client())
                self.assert_error('effect_unknown', {**CALL, 'body': '{"value":3}'})
                self.assertEqual(len(self.sent), 1)
                self.assertTrue(self.service._work_has_unknown_effect(self.work))
                result = self.api.call({**CALL, 'method': 'GET', 'effect': 'read', 'body': None})
                self.assertEqual(result['data'], {'result': 'changed'})

    def test_authentication_denial_allows_corrected_connection_without_replaying_automatically(self):
        for status in (401, 403):
            with self.subTest(status=status):
                self.setUp()
                self.assert_error('api_unauthorized', api=self.client(lambda *args: (status, {}, b'{}')))
                self.assertFalse(self.service._work_has_unknown_effect(self.work))
                self.save()
                self.api.call(CALL)
                self.assertEqual(len(self.sent), 1)

    def test_cancelled_work_and_revoked_connection_never_dispatch(self):
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='cancelled' WHERE id=?", (self.work,))
        self.assert_error('api_work_inactive')
        self.assertEqual(self.sent, [])

    def test_money_floor_actual_approval_callback_resumes_same_work_and_spends_once(self):
        self.save(money=True)
        self.assert_error('approval_required')
        self.assertEqual(self.sent, [])
        pending = self.service._browser_request(self.work)
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='partial' WHERE id=?", (self.work,))
        receipt = self.service.browser_step_decision({'work_id': self.work, 'decision': 'approve',
                                                    'digest': pending['offer_digest']})
        self.assertTrue(receipt['resumed'])
        self.assertEqual(self.store.job(self.work)['status'], 'queued')
        result = self.client().call(CALL)
        self.assertEqual(result['provenance']['effect'], 'payment')
        self.assert_error('api_effect_replayed')
        self.assertEqual(len(self.sent), 1)
        self.assertIn('Idempotency-Key', self.sent[0][2])

    def test_host_retry_cannot_repeat_original_effect_but_separate_request_can(self):
        self.api.call(CALL)
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='failed' WHERE id=?", (self.work,))
        retry = self.store.enqueue('Continue the original request', 'retry')
        self.store.link_work_relation(retry, self.work, 'retry')
        self.assert_error('api_effect_replayed', api=self.client(work=retry))
        separate = self.store.enqueue('Apply another change', 'separate')
        self.client(work=separate).call(CALL)
        self.assertEqual(len(self.sent), 2)
        self.assertNotEqual(self.sent[0][2]['Idempotency-Key'], self.sent[1][2]['Idempotency-Key'])

    def test_current_slot_is_rechecked_after_payment_consumption(self):
        self.save(money=True)
        approvals = self.service.browser_approvals_for(self.job)
        def consume(_):
            self.save(money=True)
            return True
        with mock.patch.object(approvals, 'consume', side_effect=consume):
            api = ApiRequests(self.store, self.work, approvals=approvals, transport=self.transport)
            self.assert_error('needs_setup', api=api)
        self.assertEqual(self.sent, [])

    def test_actual_payment_callback_restarts_worker_and_finishes_original_result(self):
        from personal_agent.providers import ModelAdapter
        from test_agency_loop import call, finish, judgments
        self.save(money=True)
        messages, prompts = [], []
        def model(url, body, headers=None, timeout=60):
            prompts.append(body)
            names = [tool.get('function', {}).get('name') for tool in body.get('tools', [])]
            if 'agentos_connection_probe' in names:
                return {'choices': [{'message': {'tool_calls': [call('probe', 'agentos_connection_probe')]}}]}
            return {'choices': [{'message': messages.pop(0) if messages else {'content': 'Waiting for approval.'}}]}
        self.service.adapter = ModelAdapter(model)
        self.service.save_model({'provider': 'compatible', 'endpoint': 'https://example.test/v1',
                                 'model': 'test-model', 'api_key': 'k'})
        self.assertTrue(self.service.test_model()['ok'])
        messages.extend([{'tool_calls': [call('change', 'api_request', **CALL)]},
                         {'content': 'Please approve this change; I will verify and report its result.'}])
        with mock.patch('personal_agent.api_requests.http_request', self.transport):
            self.assertTrue(self.service.run_one())
        self.assertEqual(self.sent, [])
        pending = self.service._browser_request(self.work)
        self.assertIsNotNone(pending)
        receipt = self.service.browser_step_decision({'work_id': self.work, 'decision': 'approve',
                                                    'digest': pending['offer_digest']})
        self.assertTrue(receipt['resumed'])
        self.service.decision_judge = judgments(True)
        messages.extend([{'tool_calls': [call('change', 'api_request', **CALL)]},
                         {'tool_calls': [call('verify', 'api_request', **{**CALL, 'method': 'GET',
                                          'effect': 'read', 'body': ''})]},
                         finish('done', 'change', 'verify', summary='The requested record now has result changed.')])
        with mock.patch('personal_agent.api_requests.http_request', self.transport):
            self.assertTrue(self.service.run_one())
        result = self.store.job(self.work)
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertIn('result changed', result['response'])
        self.assertEqual(len(self.store.jobs()), 1)
        self.assertEqual([row[0] for row in self.sent], ['POST', 'GET'])
        self.assertNotIn(self.key, json.dumps(prompts))
        self.assertNotIn(self.key, json.dumps(self.store.task_events(self.work)))
        self.assertIn('Observed API response', self.service.action_continuation_note(self.job))
        self.assertTrue(self.service.settings_resume_safe(self.work), 'an observed effect must not block a later prerequisite')

    def test_approval_description_preserves_long_exact_body_and_refuses_hidden_secrets(self):
        self.save(money=True)
        body = json.dumps({'note': 'preserve  double  spaces ' + 'x' * 200, 'amount': 42})
        self.assert_error('approval_required', {**CALL, 'body': body})
        self.assertIn(body, self.service._browser_request(self.work)['label'])
        self.assert_error('api_approval_unrepresentable', {**CALL, 'body': 'x' * 8001})
        self.assert_error('api_approval_unrepresentable', {**CALL, 'body': json.dumps({'value': self.key})})
        self.assertEqual(self.sent, [])

    def test_slot_revocation_during_credential_resolution_prevents_nonpayment_dispatch(self):
        from personal_agent.api_requests import SECRET_PREFIX, remove_slot
        secret = self.store.secret
        def resolve(name, *args, **kwargs):
            value = secret(name, *args, **kwargs)
            if name == SECRET_PREFIX + 'synthetic':
                remove_slot(self.store, 'synthetic')
            return value
        with mock.patch.object(self.store, 'secret', side_effect=resolve):
            self.assert_error('needs_setup')
        self.assertEqual(self.sent, [])

    def test_fragment_changes_do_not_repeat_the_same_http_dispatch(self):
        self.api.call(CALL)
        self.assert_error('api_effect_replayed', {**CALL, 'url': CALL['url'] + '#different'})
        self.assertEqual(len(self.sent), 1)

    def test_api_unknown_effect_also_blocks_ai_connector_and_is_audited_after_restart(self):
        from personal_agent import owner_mcp, trust_record, information_use
        self.service.set_ai_connections({'enabled': True})
        owner_mcp.set_confirmed(self.store, 'codex', ['synthetic'])
        target = trust_record.connection('codex', 'owner-mcp', 'synthetic')
        trust_record.set_operation(self.store, target, 'apply', 'mutate')
        self.assert_error('effect_unknown', api=self.client(lambda *a: (_ for _ in ()).throw(TimeoutError())))
        restarted = AgentService(self.store)
        self.assertEqual(restarted.connector_permission(self.job, {'engine': 'codex', 'tool_name': 'mcp__synthetic__apply',
                                'input': {'value': 3}})['behavior'], 'deny')
        audit = information_use.work_information_use(self.store, self.work)
        [connection] = audit['sent_to']['connections']
        self.assertEqual(connection['state'], 'outcome-unknown')
        self.assertEqual(connection['destination'], 'synthetic.example')

    def test_connector_unknown_effect_also_blocks_api_mutation(self):
        from personal_agent import owner_mcp, trust_record
        self.service.set_ai_connections({'enabled': True})
        owner_mcp.set_confirmed(self.store, 'codex', ['synthetic'])
        target = trust_record.connection('codex', 'owner-mcp', 'synthetic')
        trust_record.set_operation(self.store, target, 'apply', 'mutate')
        self.assertEqual(self.service.connector_permission(self.job, {'engine': 'codex', 'tool_name': 'mcp__synthetic__apply',
                              'input': {'value': 3}})['behavior'], 'allow')
        self.assert_error('effect_unknown')
        self.assertEqual(self.sent, [])

    def test_inflight_slot_change_withholds_response_and_preserves_unknown_effect(self):
        def changed(*args):
            self.save()
            return self.transport(*args)
        error = self.assert_error('needs_setup', api=self.client(changed))
        self.assertEqual(error.effect, 'unknown')
        self.assert_error('effect_unknown')
        self.assertEqual(len(self.sent), 1)

    def test_accepted_async_or_wrong_subject_is_not_a_completed_effect(self):
        for status, payload in ((202, b'{"result":"pending","subject":"owner-a"}'),
                                (200, b'{"result":"changed","subject":"other"}')):
            with self.subTest(status=status):
                self.setUp()
                self.save(subject_field='$.subject', subject_value='owner-a')
                result = self.client(lambda *args: (status, {}, payload)).call(CALL)
                self.assertTrue(result['partial'])
                self.assertTrue(self.service._work_has_unknown_effect(self.work))
                self.assert_error('effect_unknown', {**CALL, 'body': '{"value":4}'})

    def test_known_effect_prevents_worker_redelegation_even_before_runtime_receipt(self):
        from types import SimpleNamespace
        self.api.call(CALL)
        with self.store.db() as db:
            since = db.execute('SELECT MAX(id) FROM tool_events WHERE job_id=?', (self.work,)).fetchone()[0]
        orchestration = SimpleNamespace(orchestrated=True, _redact=lambda text: text, next=mock.Mock(return_value=None))
        self.service.orchestration_step(orchestration, object(), self.work, since, failed='worker stopped')
        self.assertTrue(orchestration.next.call_args.kwargs['effect'])

    def test_declared_read_never_lowers_nonpayment_post_to_retryable_read(self):
        result = self.api.call({**CALL, 'effect': 'read'})
        self.assertEqual(result['provenance']['effect'], 'mutate')
        self.assertIsNone(self.service._browser_request(self.work))
        self.assert_error('api_effect_replayed', {**CALL, 'effect': 'payment'})

    def test_old_truncated_payment_approval_and_encoded_secrets_are_not_accepted(self):
        from personal_agent.browser_session import step_binding
        from test_api_requests import Approvals
        from urllib.parse import quote
        self.save(money=True)
        approvals = Approvals()
        old = step_binding(self.work, 'api_request', CALL['url'], f"synthetic|POST|{CALL['url']}",
                           argument=CALL['body'], state='synthetic@2|payment')
        approvals.request(old, 'old truncated description')
        approvals.approve_last()
        api = ApiRequests(self.store, self.work, approvals=approvals, transport=self.transport)
        self.assert_error('approval_required', api=api)
        self.assertEqual(self.sent, [])
        encoded = ''.join('\\u%04x' % ord(c) for c in self.key)
        self.assert_error('api_approval_unrepresentable', {**CALL, 'body': '{"value":"' + encoded + '"}'})
        self.assert_error('api_approval_unrepresentable', {**CALL, 'url': CALL['url'] + '&x=' + quote(self.key, safe='')})

    def test_failure_to_persist_response_leaves_a_durable_unknown_claim(self):
        import sqlite3
        from contextlib import contextmanager
        @contextmanager
        def unavailable():
            raise sqlite3.OperationalError('storage unavailable')
            yield
        settle = self.api._settle_effect
        def cannot_settle(*args):
            with mock.patch.object(self.store, 'db', unavailable):
                return settle(*args)
        with mock.patch.object(self.api, '_settle_effect', side_effect=cannot_settle):
            self.assert_error('effect_unknown')
        self.assertTrue(self.service._work_has_unknown_effect(self.work))
        self.assertEqual(len(self.sent), 1)

    def test_money_marking_floors_a_declared_get_mutation_too(self):
        self.save(money=True)
        self.assert_error('approval_required', {**CALL, 'method': 'GET', 'body': None})
        self.assertIn('payment', self.service._browser_request(self.work)['label'])
        self.assertEqual(self.sent, [])

    def test_sensitive_structures_never_reach_api_payment_cards_or_notifications(self):
        specimens = [{'card_number': '4242424242424242', 'cvv': '937'},
                     {'nested': [{'client_secret': 'unregistered-private-value'}]},
                     {'parameters': [{'Name': 'private_key', 'Value': 'unregistered-private-value'}]},
                     {'body': 'client%255Fsecret%253Dunregistered-private-value'},
                     {'body': '-----BEGIN PRIVATE KEY----- unregistered-private-value'}]
        for value in specimens:
            with self.subTest(value=value):
                self.setUp()
                self.save(money=True)
                args = {**CALL, 'body': json.dumps({'note': 'x' * 150, 'payload': value})}
                self.assert_error('api_approval_unrepresentable', args)
                self.assertIsNone(self.service._browser_request(self.work))
                self.assertEqual(self.sent, [])
                with self.store.db() as db:
                    for table in ('config', 'tool_events', 'telegram_notifications'):
                        stored = json.dumps([tuple(row) for row in db.execute('SELECT * FROM ' + table)])
                        self.assertNotIn('unregistered-private-value', stored)
                        self.assertNotIn('4242424242424242', stored)
        self.assert_error('api_approval_unrepresentable', {**CALL, 'url': CALL['url'] + '&client%5Fsecret=unregistered-private-value'})

    def test_retry_work_after_observed_api_change_can_approve_and_finish_next_action(self):
        from personal_agent.providers import ModelAdapter
        from test_agency_loop import call, finish, judgments
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='failed' WHERE id=?", (self.work,))
        retry = self.store.enqueue('Continue the original goal', 'retry')
        self.store.link_work_relation(retry, self.work, 'retry')
        second = {**CALL, 'effect': 'payment', 'body': '{"value":"next"}'}
        messages = []
        def model(url, body, headers=None, timeout=60):
            names = [tool.get('function', {}).get('name') for tool in body.get('tools', [])]
            if 'agentos_connection_probe' in names:
                return {'choices': [{'message': {'tool_calls': [call('probe', 'agentos_connection_probe')]}}]}
            return {'choices': [{'message': messages.pop(0) if messages else {'content': 'Waiting.'}}]}
        self.service.adapter = ModelAdapter(model)
        self.service.save_model({'provider': 'compatible', 'endpoint': 'https://example.test/v1',
                                 'model': 'test-model', 'api_key': 'test-model-key'})
        self.assertTrue(self.service.test_model()['ok'])
        messages.extend([{'tool_calls': [call('first', 'api_request', **CALL),
                                       call('second', 'api_request', **second)]},
                         {'content': 'First change observed. Please approve the remaining payment.'}])
        with mock.patch('personal_agent.api_requests.http_request', self.transport):
            self.assertTrue(self.service.run_one())
        pending = self.service._browser_request(retry)
        self.assertIsNotNone(pending)
        self.assertEqual(len(self.sent), 1)
        receipt = self.service.browser_step_decision({'work_id': retry, 'decision': 'approve', 'digest': pending['offer_digest']})
        self.assertTrue(receipt['resumed'], receipt)
        self.service.decision_judge = judgments(True)
        messages.extend([{'tool_calls': [call('second', 'api_request', **second),
                                       call('read', 'api_request', **{**CALL, 'method': 'GET', 'effect': 'read', 'body': ''})]},
                         finish('done', 'second', 'read', summary='Both requested changes are observed.')])
        with mock.patch('personal_agent.api_requests.http_request', self.transport):
            self.assertTrue(self.service.run_one())
        self.assertEqual(self.store.job(retry)['status'], 'succeeded', self.store.job(retry))
        self.assertEqual([row[0] for row in self.sent], ['POST', 'POST', 'GET'])
        self.assertEqual(self.store.job(retry)['related_job_id'], self.work)
