"""#1300: real local input HTTP boundary, synthetic provider and encryption key."""
import json
import time
from unittest import mock

from personal_agent import phone_input
from personal_agent.api_requests import slots
from personal_agent.browser_jar import MemoryKey
from test_phone_input import PhoneInputServiceCase
from fake_holdings_api import FakeHoldingsApi, fake_key, ACCOUNT
from test_api_requests import HOST, URL

PUBLIC = {'name': 'requested-api', 'hosts': [HOST], 'header': 'Authorization', 'scheme': 'Bearer',
          'money': False, 'note': 'Requested records', 'subject_field': '$.account_id', 'subject_value': ACCOUNT,
          'probe': {'url': URL, 'required_fields': ['$.cash']}}


class ApiInput(PhoneInputServiceCase):
    def setUp(self):
        super().setUp()
        self.store.api_slot_key = MemoryKey()
        self.key = fake_key()
        self.api = FakeHoldingsApi(self.key, now=time.time)
        self.service.api_input_transport = self.api
        self.settings = self.service.settings_orchestrator
        self.job = self.store.job(self.store.enqueue('Read my requested records and report the cash total', 'original'))

    def open_input(self, metadata=None):
        draft = self.service.settings_tools(self.job)('settings_change', {
            'category': 'api_connections', 'setting': 'connect', 'value': json.dumps(metadata or PUBLIC), 'continue_work': 'yes'})
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='partial' WHERE id=?", (self.job['id'],))
        result = self.settings.confirm(self.service.settings_owner(self.job), self.job['channel'],
                                       draft['draft_id'], self.settings._drafts()[draft['draft_id']]['digest'])
        self.assertEqual(result['state'], 'awaiting-input')
        session = self.service.phone_input_session()
        cookie = self.open_page(session)
        return draft, session, cookie

    def submit(self, session, cookie, key=None):
        return self.request(f'{phone_input.API_PATH}?code={session.code}', 'POST',
                            {'secret': self.key if key is None else key}, tunneled=False, cookie=cookie)

    def receipts(self):
        return [e['trace'] for e in self.store.task_events(self.job['id']) if e['tool'] == 'settings_continuation']

    def test_a_click_refused_before_running_does_not_block_resumption(self):
        # #1380 live: a click whose target was not on the page pressed nothing, yet the original
        # Work's continuation was withheld as "effect-or-unobserved-action".
        def event(tool, status, detail):
            with self.store.db() as db:
                db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,0)',
                           (self.job['id'], tool, status, json.dumps(detail)))
        call = {'scope': 'subscription-mcp-bridge', 'call_id': 'c1', 'attempt': None, 'host_action': 'browser_click'}
        event('subscription_engine', 'running', {})
        event('browser_click', 'running', {**call, 'declared_effect': 'read'})
        event('browser_click', 'failed', {**call, 'declared_effect': 'read', 'effect': 'none', 'code': 'target_unavailable'})
        event('subscription_engine', 'succeeded', {'continuation_read_only': True, 'continuation_accounted': True})
        self.assertTrue(self.service.settings_resume_safe(self.job['id']))
        # A click still running, or one refused for another reason, keeps the continuation withheld.
        event('browser_click', 'running', {**call, 'call_id': 'c2', 'declared_effect': 'read'})
        self.assertFalse(self.service.settings_resume_safe(self.job['id']))
        event('browser_click', 'failed', {**call, 'call_id': 'c2', 'declared_effect': 'read', 'effect': 'none',
                                          'code': 'approval_required'})
        self.assertFalse(self.service.settings_resume_safe(self.job['id']))

    def test_a_refused_draft_before_the_corrected_one_does_not_block_resumption(self):
        # #1445 rehearsal: the worker's first draft was refused (malformed), the corrected one was
        # confirmed and filled in, and the original Work never resumed.
        def event(tool, status, detail):
            with self.store.db() as db:
                db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,0)',
                           (self.job['id'], tool, status, json.dumps(detail)))
        first = {'scope': 'subscription-mcp-bridge', 'call_id': 's1', 'attempt': None, 'host_action': 'settings_change'}
        second = {**first, 'call_id': 's2'}
        event('subscription_engine', 'running', {})
        event('settings_change', 'running', first)
        event('settings_change', 'failed', {**first, 'code': 'settings_refused', 'effect': 'none'})
        event('settings_change', 'running', second)
        event('settings_change', 'succeeded', {**second, 'evidence': {'applied': False, 'requires_owner_confirmation': True}})
        event('subscription_engine', 'succeeded', {'continuation_read_only': True, 'continuation_accounted': True})
        self.assertTrue(self.service.settings_resume_safe(self.job['id']))
        # Any other settings failure still withholds it.
        event('settings_change', 'running', {**first, 'call_id': 's3'})
        event('settings_change', 'failed', {**first, 'call_id': 's3', 'code': 'tool_failed', 'effect': 'unknown'})
        self.assertFalse(self.service.settings_resume_safe(self.job['id']))

    def test_page_submission_checks_read_and_queues_original_once(self):
        draft, session, cookie = self.open_input()
        self.assertFalse(self.tunnel.processes, 'local owner needs no tunnel or paired chat')
        self.assertEqual(self.store.job(self.job['id'])['status'], 'awaiting_connection')
        self.assertNotIn(PUBLIC['name'], slots(self.store))
        status, body, _ = self.submit(session, cookie)
        self.assertEqual(status, 200, body)
        self.assertTrue(json.loads(body)['ok'])
        self.assertEqual(self.store.job(self.job['id'])['status'], 'queued')
        self.assertEqual(len(self.api.calls), 1)
        self.assertTrue(slots(self.store)[PUBLIC['name']]['credential_ref'])
        self.assertEqual(self.settings._drafts()[draft['draft_id']]['state'], 'applied')
        self.service.resume_settings_work()
        self.assertEqual(len(self.receipts()), 1)
        status, _, _ = self.submit(session, cookie)
        self.assertIn(status, (400, 404))
        self.assertEqual(len(self.api.calls), 1)
        self.assertEqual(len(self.store.jobs()), 1)
        self.assertNotIn(self.key, self.store.secret_path.read_text())
        self.assertNotIn(self.key, json.dumps(self.settings._drafts()))
        self.assertNotIn(self.key, json.dumps(self.store.task_events(self.job['id'])))

    def test_authentication_failure_can_be_corrected_without_a_new_request(self):
        draft, session, cookie = self.open_input()
        status, body, _ = self.submit(session, cookie, 'wrong-synthetic-key')
        self.assertEqual(status, 400, body)
        self.assertEqual(self.settings._drafts()[draft['draft_id']]['state'], 'awaiting-input')
        self.assertNotIn(PUBLIC['name'], slots(self.store))
        self.assertFalse(self.receipts())
        status, body, _ = self.submit(session, cookie)
        self.assertEqual(status, 200, body)
        self.assertEqual(self.store.job(self.job['id'])['status'], 'queued')

    def test_wrong_cookie_or_cancelled_work_cannot_save(self):
        draft, session, cookie = self.open_input()
        self.assertEqual(self.submit(session, 'other-cookie')[0], 404)
        self.settings.cancel(self.service.settings_owner(self.job), self.job['channel'], draft['draft_id'])
        self.assertIn(self.submit(session, cookie)[0], (400, 404))
        self.assertFalse(self.api.calls)
        self.assertNotIn(PUBLIC['name'], slots(self.store))

    def test_cancel_releases_input_immediately_and_settles_original_wait(self):
        draft, session, cookie = self.open_input()
        self.settings.cancel(self.service.settings_owner(self.job), self.job['channel'], draft['draft_id'])
        self.assertFalse(session.alive())
        self.assertIsNone(self.service.phone_input_session())
        self.assertEqual(self.store.job(self.job['id'])['status'], 'partial')
        self.assertEqual(self.settings._drafts()[draft['draft_id']]['state'], 'canceled')
        self.assertEqual(len(self.receipts()), 1)
        self.assertEqual(self.submit(session, cookie)[0], 404)
        self.job = self.store.job(self.store.enqueue('Read the records for a new request', 'new-request'))
        _, renewed, _ = self.open_input()
        self.assertNotEqual(renewed.code, session.code)

    def test_expired_link_rejects_secret_and_preserves_original(self):
        draft, session, cookie = self.open_input()
        session.expires = time.time() - 1
        self.assertEqual(self.submit(session, cookie)[0], 404)
        self.settings.reconcile()
        self.assertEqual(self.settings._drafts()[draft['draft_id']]['state'], 'expired')
        self.assertFalse(self.api.calls)
        self.assertEqual(self.store.job(self.job['id'])['message'], self.job['message'])

    def test_expiry_renews_the_same_requirement_without_new_original_work(self):
        draft, session, cookie = self.open_input()
        session.finish('expired')
        rows = self.settings.pending_or_renewable(self.service.settings_owner(self.job), self.job['channel'])
        self.assertEqual([row['id'] for row in rows], [draft['draft_id']])
        result = self.settings.settle_pending(self.service.settings_owner(self.job), self.job['channel'], rows, True)
        self.assertEqual(result['state'], 'awaiting-input', result)
        renewed = self.service.phone_input_session()
        self.assertNotEqual(renewed.code, session.code)
        self.assertEqual(self.submit(session, cookie)[0], 404)
        status, body, _ = self.submit(renewed, self.open_page(renewed))
        self.assertEqual(status, 200, body)
        self.assertEqual(self.store.job(self.job['id'])['status'], 'queued')
        self.assertEqual(len(self.store.jobs()), 1)

    def test_restart_expires_the_old_page_without_publishing_or_replaying(self):
        from personal_agent.quickstart_service import AgentService
        draft, session, cookie = self.open_input()
        restarted = AgentService(self.store)
        restarted.settings_orchestrator.reconcile()
        self.assertEqual(restarted.settings_orchestrator._drafts()[draft['draft_id']]['state'], 'expired')
        self.assertEqual(self.submit(session, cookie)[0], 400)
        self.assertFalse(self.api.calls)
        self.assertNotIn(PUBLIC['name'], slots(self.store))
        rows = restarted.settings_orchestrator.pending_or_renewable(self.service.settings_owner(self.job), self.job['channel'])
        self.assertEqual(len(rows), 1)

    def test_input_retries_are_bounded_across_the_work(self):
        _, session, cookie = self.open_input()
        for _ in range(4):
            self.assertEqual(self.submit(session, cookie, 'wrong-synthetic-key')[0], 400)
        self.assertEqual(len(self.api.calls), 3)
        self.assertNotIn(PUBLIC['name'], slots(self.store))

    def test_committed_input_receipt_survives_restart_without_another_read(self):
        from personal_agent.quickstart_service import AgentService
        draft, session, cookie = self.open_input()
        original = self.settings._settle

        def interrupted(ident, state, *args, **kwargs):
            if state == 'applied':
                raise RuntimeError('synthetic interruption after atomic publication')
            return original(ident, state, *args, **kwargs)

        with mock.patch.object(self.settings, '_settle', interrupted):
            self.assertEqual(self.submit(session, cookie)[0], 400)
        self.assertEqual(self.settings._drafts()[draft['draft_id']]['state'], 'unknown')
        restarted = AgentService(self.store)
        restarted.resume_settings_work()
        self.assertEqual(self.store.job(self.job['id'])['status'], 'queued')
        self.assertEqual(len(self.api.calls), 1)

    def test_missing_key_prevents_recovered_receipt_from_resuming(self):
        from personal_agent.quickstart_service import AgentService
        _, session, cookie = self.open_input()
        with mock.patch.object(self.service, 'resume_settings_work'):
            self.assertEqual(self.submit(session, cookie)[0], 200)
        original_key = self.store.api_slot_key.get()
        self.store.api_slot_key.delete()
        restarted = AgentService(self.store)
        restarted.resume_settings_work()
        self.assertNotEqual(self.store.job(self.job['id'])['status'], 'queued')
        self.assertFalse(self.receipts(), 'temporary key unavailability must not consume the original wait')
        self.store.api_slot_key = MemoryKey(original_key)
        restarted.resume_settings_work()
        self.assertEqual(self.store.job(self.job['id'])['status'], 'queued')
        self.assertEqual(len(self.api.calls), 1)

    def test_restart_finishes_failed_plaintext_cleanup_without_repeating_read(self):
        from personal_agent.api_requests import SECRET_PREFIX, save_slot
        from personal_agent.quickstart_service import AgentService
        save_slot(self.store, PUBLIC['name'], PUBLIC['hosts'], 'retired-plaintext-key')
        draft, session, cookie = self.open_input()
        remove = self.store.remove_secret

        def unavailable(name):
            if name == SECRET_PREFIX + PUBLIC['name']:
                raise OSError('synthetic unavailable secret file')
            return remove(name)

        with mock.patch.object(self.store, 'remove_secret', unavailable):
            self.assertEqual(self.submit(session, cookie)[0], 400)
            self.assertEqual(self.settings._drafts()[draft['draft_id']]['state'], 'unknown')
            self.service.resume_settings_work()
            self.assertNotEqual(self.store.job(self.job['id'])['status'], 'queued')
        self.assertTrue(self.store.secret(SECRET_PREFIX + PUBLIC['name']))
        restarted = AgentService(self.store)
        restarted.resume_settings_work()
        restarted.resume_settings_work()
        self.assertEqual(self.store.job(self.job['id'])['status'], 'queued')
        self.assertEqual(self.settings._drafts()[draft['draft_id']]['state'], 'applied')
        self.assertFalse(self.store.secret(SECRET_PREFIX + PUBLIC['name']))
        self.assertEqual(len(self.api.calls), 1)
        self.assertEqual(len(self.receipts()), 1)

    def test_old_receipt_cannot_cleanup_a_replaced_connection(self):
        from personal_agent.api_requests import SECRET_PREFIX, SLOTS_KEY, save_slot
        from personal_agent.quickstart_service import AgentService
        draft, session, cookie = self.open_input()
        original = self.settings._settle

        def interrupted(ident, state, *args, **kwargs):
            if state == 'applied':
                raise RuntimeError('synthetic interruption')
            return original(ident, state, *args, **kwargs)

        with mock.patch.object(self.settings, '_settle', interrupted):
            self.assertEqual(self.submit(session, cookie)[0], 400)
        self.store.put(SLOTS_KEY, {})  # host disconnect, then a later legacy registration
        save_slot(self.store, PUBLIC['name'], PUBLIC['hosts'], 'later-plaintext-key')
        AgentService(self.store).resume_settings_work()
        self.assertEqual(self.store.secret(SECRET_PREFIX + PUBLIC['name']), 'later-plaintext-key')
        self.assertNotEqual(self.store.job(self.job['id'])['status'], 'queued')
        self.assertEqual(self.settings._drafts()[draft['draft_id']]['state'], 'unknown')
        self.assertEqual(len(self.api.calls), 1)

    def test_changed_original_request_invalidates_page(self):
        _, session, cookie = self.open_input()
        with self.store.db() as db:
            db.execute('UPDATE jobs SET message=? WHERE id=?', ('Use my other account instead', self.job['id']))
        self.assertEqual(self.submit(session, cookie)[0], 400)
        self.assertFalse(self.api.calls)

    def test_paired_phone_link_binds_the_original_owner_generation(self):
        from test_phone_input import CHAT
        self.pair()
        with self.store.db() as db:
            db.execute('UPDATE jobs SET channel=?,chat_id=? WHERE id=?', ('telegram:g1', CHAT, self.job['id']))
        self.job = self.store.job(self.job['id'])
        _, session, cookie = self.open_input()
        self.assertEqual(len(self.tunnel.processes), 1)
        delivered = [body for method, body in self.calls if method == 'sendMessage']
        self.assertEqual(delivered[-1]['chat_id'], CHAT)
        self.assertIn(session.link, delivered[-1]['text'])
        self.store.put('telegram', {'enabled': True, 'user_id': CHAT, 'generation': 'g2'})
        self.assertEqual(self.submit(session, cookie)[0], 400)
        self.assertFalse(self.api.calls)

    def test_link_start_failure_is_renewable_on_the_same_original_request(self):
        from personal_agent.settings_orchestrator import SettingsError
        port = self.service.local_server_port
        self.service.local_server_port = None
        with self.assertRaises(SettingsError):
            self.open_input()
        self.service.local_server_port = port
        rows = self.settings.pending_or_renewable(self.service.settings_owner(self.job), self.job['channel'])
        self.assertEqual(len(rows), 1)
        result = self.settings.settle_pending(self.service.settings_owner(self.job), self.job['channel'], rows, True)
        self.assertEqual(result['state'], 'awaiting-input', result)
        session = self.service.phone_input_session()
        self.assertEqual(self.submit(session, self.open_page(session))[0], 200)
        self.assertEqual(self.store.job(self.job['id'])['status'], 'queued')

    def test_resumed_worker_observes_and_reports_original_result(self):
        self.run_original(PUBLIC, {'secret': self.key})

    def run_original(self, metadata, secret_input, after_wait=None):
        from personal_agent.providers import ModelAdapter
        from test_owner_settings_conversation import call
        from test_agency_loop import finish, judgments
        messages, prompts = [], []

        def model(url, body, headers=None, timeout=60):
            prompts.append(body)
            names = [tool.get('function', {}).get('name') for tool in body.get('tools', [])]
            if 'agentos_connection_probe' in names:
                return {'choices': [{'message': {'tool_calls': [call('p', 'agentos_connection_probe')]}}]}
            return {'choices': [{'message': messages.pop(0) if messages else {'content': 'Ready.'}}]}

        self.service.adapter = ModelAdapter(model)
        self.service.save_model({'provider': 'compatible', 'endpoint': 'https://example.test/v1', 'model': 'test-model', 'api_key': 'k'})
        self.assertTrue(self.service.test_model()['ok'])
        messages.extend([{'tool_calls': [call('draft', 'settings_change', category='api_connections', setting='connect',
                                             value=json.dumps(metadata), continue_work='yes')]},
                         {'content': 'Please confirm the connection so I can read your requested records.'}])
        self.assertTrue(self.service.run_one())
        budget_before = self.store.config('work_budget:' + self.job['id'])
        self.assertFalse(budget_before['active'], 'the worker releases active time while waiting')
        [row] = self.settings.pending_for_work(self.job['id'])
        self.settings.confirm(self.service.settings_owner(self.job), self.job['channel'], row['id'], row['digest'])
        if after_wait is not None:
            after_wait()
        session = self.service.phone_input_session()
        status, body, _ = self.request(f'{phone_input.API_PATH}?code={session.code}', 'POST', secret_input,
                                      cookie=self.open_page(session), tunneled=False)
        self.assertEqual(status, 200, body)
        self.service.decision_judge = judgments(True)
        messages.extend([{'tool_calls': [call('read', 'api_request', slot=metadata['name'], url=URL, effect='read',
                                             as_of_field='$.as_of', required_fields=json.dumps(['$.cash']))]},
                         finish('done', 'read', summary='The cash total in the requested account is 1,250,340 KRW.')])
        with mock.patch('personal_agent.api_requests.http_request', self.api):
            self.assertTrue(self.service.run_one())
        result = self.store.job(self.job['id'])
        self.assertEqual(result['status'], 'succeeded', result.get('response'))
        budget_after = self.store.config('work_budget:' + self.job['id'])
        self.assertFalse(budget_after['active'])
        # #1445: the owner completed the prerequisite, so the remaining work ran on one renewed
        # allowance (bounded per Work); its own spending is recorded from zero.
        from personal_agent.agent_runtime import WORK_DEADLINE_SECONDS
        self.assertEqual(budget_after.get('renewals'), 1)
        self.assertGreater(budget_after['turns'], 0)
        self.assertGreater(budget_after['attempts'], 0)
        self.assertLessEqual(budget_after['remaining_seconds'], WORK_DEADLINE_SECONDS)
        self.assertIn('1,250,340', result['response'])
        events = self.store.task_events(self.job['id'])
        self.assertTrue(any(e['tool'] == 'api_request' and e['status'] == 'succeeded' for e in events))
        self.assertEqual(len(self.store.jobs()), 1)
        for value in secret_input.values():
            self.assertNotIn(value, json.dumps(prompts))
            self.assertNotIn(value, json.dumps(events))
