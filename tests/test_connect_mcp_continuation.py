"""#1299: exact Settings → remote setup → same Work, all transports synthetic."""
import json
from pathlib import Path
from unittest import mock
from concurrent.futures import ThreadPoolExecutor

from personal_agent import owner_mcp, trust_record
from personal_agent.quickstart_service import AgentService
from personal_agent.settings_orchestrator import SettingsError
from test_owner_settings_conversation import _Case, call
from test_agency_loop import finish, judgments

PUBLIC = {'engine': 'claude-code', 'name': 'unseen-service', 'url': 'https://service.example/mcp',
          'source': 'https://publisher.example/source', 'publisher': 'Example publisher', 'licence': 'unknown',
          'required_tools': ['get_record'], 'read_tools': ['get_record'], 'money': False}


class McpContinuation(_Case):
    def setUp(self):
        super().setUp()
        self.home = self.store.root / 'synthetic-owner-home'
        self.home.mkdir()
        self.service.mcp_setup_home = self.home
        self.calls = []
        self.observations = [{'state': 'connected', 'tools': ['get_record']}]
        self.login_result = {'state': 'login-returned'}
        self.on_observe = lambda: None
        self.service.mcp_setup_factory = lambda draft, interrupted: self.client(interrupted)
        self.job = self.store.job(self.store.enqueue('Summarize my requested record', 'original'))

    def client(self, interrupted):
        case = self
        class Client:
            def register(self, *args):
                case.calls.append('register')
                assert not interrupted()
                (case.home / '.claude.json').write_text(json.dumps({'mcpServers': {
                    args[1]: {'type': 'http', 'url': args[2]}}}))
                return {'state': 'registration-returned'}
            def observe(self, *args):
                case.calls.append('observe')
                assert not interrupted()
                case.on_observe()
                return case.observations.pop(0)
            def login(self, *args):
                case.calls.append('login')
                assert not interrupted()
                return case.login_result
        return Client()

    def status(self, state):
        with self.store.db() as db:
            db.execute('UPDATE jobs SET status=? WHERE id=?', (state, self.job['id']))

    def propose(self, **kwargs):
        args = {'category': 'ai_connections', 'setting': 'connect', 'value': json.dumps(PUBLIC), 'continue_work': 'yes', **kwargs}
        result = self.service.settings_tools(self.job)('settings_change', args)
        self.status('partial')
        return result

    def confirm(self, draft):
        row = self.settings._drafts()[draft['draft_id']]
        return self.settings.confirm(self.service.settings_owner(self.job), self.job['channel'], row['id'], row['digest'])

    def receipts(self):
        return [e['trace'] for e in self.store.task_events(self.job['id']) if e['tool'] == 'settings_continuation']

    def target(self):
        return trust_record.connection('claude-code', 'owner-mcp', 'unseen-service')

    def test_exact_confirmation_registers_observes_and_resumes_once(self):
        draft = self.propose()
        self.assertEqual(self.calls, [])
        self.assertFalse(owner_mcp.confirmed(self.store, 'claude-code'))
        self.assertIn(PUBLIC['url'], draft['summary'])
        self.confirm(draft)
        self.assertEqual(self.calls, ['register', 'observe'])
        self.assertEqual(self.store.job(self.job['id'])['status'], 'queued')
        self.assertEqual(owner_mcp.confirmed(self.store, 'claude-code'), ['unseen-service'])
        self.assertEqual(trust_record.hand(self.store, self.target())['operations'], {'get_record': 'read'})
        self.service.resume_settings_work()
        self.assertEqual(len(self.receipts()), 1)
        self.assertEqual(self.store.job(self.job['id'])['message'], self.job['message'])
        with self.assertRaises(SettingsError):
            self.confirm(draft)
        self.assertEqual(len(self.calls), 2)

    def test_authentication_needs_observed_tools_and_keeps_unknown_fields(self):
        self.observations = [{'state': 'needs-auth'}, {'state': 'connected', 'tools': ['get_record']}]
        draft = self.propose()
        self.confirm(draft)
        self.assertEqual(self.calls, ['register', 'observe', 'login', 'observe'])
        receipt = self.settings._drafts()[draft['draft_id']]['setup_receipt']
        for key in ('authentication', 'account', 'granted_scopes', 'input_schemas'):
            self.assertEqual(receipt[key], 'unknown')

    def test_setup_only_changes_connection_without_reexecuting_work(self):
        draft = self.propose(continue_work='no')
        self.status('succeeded')
        self.confirm(draft)
        self.assertFalse(self.receipts())
        self.assertEqual(self.store.job(self.job['id'])['status'], 'succeeded')

    def test_registration_or_login_success_is_not_readiness(self):
        for observed in ({'state': 'failed'}, {'state': 'connected', 'tools': ['different_tool']}):
            with self.subTest(observed=observed):
                self.observations = [observed]
                draft = self.propose()
                with self.assertRaises(SettingsError):
                    self.confirm(draft)
                self.assertFalse(owner_mcp.confirmed(self.store, 'claude-code'))
                self.assertFalse(trust_record.hand(self.store, self.target())['revision'])
                self.assertNotEqual(self.store.job(self.job['id'])['status'], 'queued')
        self.observations = [{'state': 'needs-auth'}]
        self.login_result = {'state': 'cancelled'}
        with self.assertRaises(SettingsError):
            self.confirm(self.propose())
        self.assertFalse(owner_mcp.confirmed(self.store, 'claude-code'))
        self.assertEqual(self.calls.count('register'), 1, 'matching partial registration is preserved')

    def fail_setup(self, state='failed'):
        self.observations = [{'state': state}]
        draft = self.propose()
        with self.assertRaises(SettingsError):
            self.confirm(draft)
        return self.settings._drafts()[draft['draft_id']]

    def recovery_rows(self):
        return self.settings.pending_or_renewable(self.service.settings_owner(self.job),self.job['channel'],self.job['id'])

    def test_observed_failure_is_reconfirmed_without_repeating_original_request(self):
        failed = self.fail_setup()
        self.assertEqual([row['id'] for row in self.recovery_rows()], [failed['id']])
        self.assertEqual(self.store.job(self.job['id'])['status'], 'partial')
        self.assertFalse(any(row['resumed'] for row in self.receipts()))
        self.observations = [{'state':'connected','tools':['get_record']}]
        result = self.settings.settle_pending(self.service.settings_owner(self.job),self.job['channel'],self.recovery_rows(),True)
        self.assertEqual(result['state'], 'applied', result)
        self.assertEqual(self.settings._drafts()[failed['id']]['state'], 'renewed')
        self.assertEqual(self.calls, ['register','observe','observe'])
        self.assertEqual(self.store.job(self.job['id'])['status'], 'queued')
        self.assertEqual(self.store.job(self.job['id'])['message'], self.job['message'])
        self.service.resume_settings_work()
        self.assertEqual(sum(row['resumed'] for row in self.receipts()), 1)
        with self.assertRaises(SettingsError):
            self.settings._renew(self.service.settings_owner(self.job),self.job['channel'],failed)
        self.assertEqual(len(self.calls), 3)
        self.run_original_results([self.job],['The requested record has two incomplete items.'])

    def typed_recovery_sequence(self, states):
        self.propose()
        self.service.conversation_focus.record(mock.Mock(intent='settings'),self.job['id'])
        # #172: the one owner-act judgment, scripted to read each reply as a plain yes to the open settings item.
        def owner_act(act,items,target='',work_id=None):
            return {'item':next(item['id'] for item in items if item['kind']=='settings-drafts'),'answer':'approve','rest':''}
        with mock.patch.object(self.service.decision_judge,'owner_act_answer',side_effect=owner_act):
            for index,state in enumerate(states):
                self.observations=[{'state':state,'tools':['get_record']}]
                self.script=[{'content':'확인했습니다.'}]*4
                reply=self.store.enqueue('응, 같은 연결 설정을 다시 시도해 줘','typed-recovery-'+str(index),owner_typed=True)
                self.assertTrue(self.service.run_one())
                if self.settings.thread:self.settings.thread.join(10)
                self.assertFalse(self.settings._worker_running)
                self.assertEqual(self.store.job(reply)['status'],'succeeded')
                self.assertEqual(self.service.conversation_focus.current()['work_id'],self.job['id'])
                if state!='connected':self.assertEqual(len(self.recovery_rows()),1)
        self.assertEqual(self.calls,['register','observe','observe','observe'])
        self.assertEqual(self.store.job(self.job['id'])['status'],'queued')
        self.run_original_results([self.job],['The requested record has two incomplete items.'])
        self.assertEqual(sum(row['resumed'] for row in self.receipts()),1)

    def test_typed_confirmations_keep_original_focus_through_two_setup_failures(self):
        self.typed_recovery_sequence(('failed','failed','connected'))

    def test_typed_unknown_confirmations_observe_until_original_result(self):
        self.typed_recovery_sequence(('unknown','unknown','connected'))

    def test_failed_login_can_reobserve_existing_registration_then_complete(self):
        self.observations = [{'state':'needs-auth'}]
        self.login_result = {'state':'failed'}
        draft = self.propose()
        with self.assertRaises(SettingsError):self.confirm(draft)
        self.assertEqual(self.settings.read(self.service.settings_owner(self.job),'ai_connections')['setup_failures'][0]['setup_failure'],
                         {'phase':'login','state':'failed','retryable':True})
        self.observations = [{'state':'needs-auth'},{'state':'connected','tools':['get_record']}]
        self.login_result = {'state':'login-returned'}
        result = self.settings.settle_pending(self.service.settings_owner(self.job),self.job['channel'],self.recovery_rows(),True)
        self.assertEqual(result['state'],'applied')
        self.assertEqual(self.calls,['register','observe','login','observe','login','observe'])
        self.assertEqual(self.store.job(self.job['id'])['status'],'queued')

    def test_cancelled_and_unsupported_outcomes_are_not_recovery_offers(self):
        for state in ('cancelled','disabled','unsupported'):
            with self.subTest(state=state):
                self.job=self.store.job(self.store.enqueue('Original '+state,'recovery-'+state))
                row=self.fail_setup(state)
                self.assertEqual(row['state'],'unknown' if state=='unknown' else 'failed')
                self.assertEqual(self.recovery_rows(),[])
                with self.assertRaises(SettingsError):
                    self.settings._renew(self.service.settings_owner(self.job),self.job['channel'],row)
                self.assertFalse(any(item['resumed'] for item in self.receipts()))

    def test_unknown_setup_is_reconciled_by_observation_without_register_or_login_replay(self):
        failed=self.fail_setup('unknown')
        self.assertEqual(failed['state'],'unknown')
        rows=self.recovery_rows()
        self.assertEqual(len(rows),1)
        read=self.settings.read(self.service.settings_owner(self.job),'ai_connections')
        self.assertEqual(read['pending_changes'][0]['recovery_mode'],'observe-only')
        self.observations=[{'state':'connected','tools':['get_record']}]
        result=self.settings.settle_pending(self.service.settings_owner(self.job),self.job['channel'],rows,True)
        self.assertEqual(result['state'],'applied',result)
        self.assertEqual(self.calls,['register','observe','observe'])
        self.assertTrue(any(row.get('observe_only') for row in self.settings._drafts().values()))
        self.run_original_results([self.job],['The requested record has two incomplete items.'])
        self.assertEqual(sum(row['resumed'] for row in self.receipts()),1)

    def test_unknown_readback_needs_auth_does_not_login_until_another_confirmation(self):
        self.fail_setup('unknown')
        self.observations=[{'state':'needs-auth'}]
        result=self.settings.settle_pending(self.service.settings_owner(self.job),self.job['channel'],self.recovery_rows(),True)
        self.assertEqual(result['state'],'failed')
        self.assertEqual(self.calls,['register','observe','observe'])
        rows=self.recovery_rows()
        self.assertEqual(rows[0]['setup_failure']['state'],'needs-auth')
        self.observations=[{'state':'needs-auth'},{'state':'connected','tools':['get_record']}]
        result=self.settings.settle_pending(self.service.settings_owner(self.job),self.job['channel'],rows,True)
        self.assertEqual(result['state'],'applied',result)
        self.assertEqual(self.calls,['register','observe','observe','observe','login','observe'])
        self.assertEqual(self.store.job(self.job['id'])['status'],'queued')

    def test_unknown_registration_without_definition_is_not_repeated(self):
        self.fail_setup('unknown')
        (self.home/'.claude.json').unlink()
        result=self.settings.settle_pending(self.service.settings_owner(self.job),self.job['channel'],self.recovery_rows(),True)
        self.assertEqual(result['state'],'failed')
        self.assertEqual(self.calls,['register','observe'])
        self.assertFalse(any(row['resumed'] for row in self.receipts()))
        self.assertTrue(any(row.get('state')=='unknown' and row.get('observe_only') for row in self.settings._drafts().values()))

    def test_restart_unknown_setup_can_observe_but_cannot_replay_registration(self):
        from personal_agent.settings_orchestrator import SettingsOrchestrator
        draft=self.propose()
        row=self.settings._drafts()[draft['draft_id']]
        self.settings._admit(row,row['digest'])
        (self.home/'.claude.json').write_text(json.dumps({'mcpServers':{PUBLIC['name']:{'type':'http','url':PUBLIC['url']}}}))
        self.settings=SettingsOrchestrator(self.store,now=lambda:self.clock[0],service=self.service)
        self.service.settings_orchestrator=self.settings
        self.settings.reconcile()
        self.assertEqual(self.settings._drafts()[row['id']]['state'],'unknown')
        result=self.settings.settle_pending(self.service.settings_owner(self.job),self.job['channel'],self.recovery_rows(),True)
        self.assertEqual(result['state'],'applied',result)
        self.assertEqual(self.calls,['observe'])
        self.assertEqual(self.store.job(self.job['id'])['status'],'queued')

    def test_unknown_readback_budget_is_not_reset_by_new_drafts(self):
        self.fail_setup('unknown')
        for _ in range(2):
            self.observations=[{'state':'unknown'}]
            result=self.settings.settle_pending(self.service.settings_owner(self.job),self.job['channel'],self.recovery_rows(),True)
            self.assertEqual(result['state'],'failed')
        self.assertEqual(self.recovery_rows(),[])
        self.assertEqual(self.service.mcp_setup_attempts(self.job['id']),3)
        self.assertEqual(self.calls,['register','observe','observe','observe'])
        self.assertFalse(any(row['resumed'] for row in self.receipts()))

    def test_unknown_observation_flag_is_digest_bound_and_stopped_work_cannot_recover(self):
        old=self.fail_setup('unknown')
        fresh=self.settings._renew(self.service.settings_owner(self.job),self.job['channel'],old)
        self.assertTrue(fresh['observe_only'])
        forged=dict(fresh,observe_only=False)
        self.settings._put_drafts({forged['id']:forged})
        with self.assertRaises(SettingsError):self.confirm({'draft_id':forged['id']})
        self.assertEqual(self.calls,['register','observe'])
        self.settings._put_drafts({fresh['id']:fresh})
        self.status('cancelled')
        with self.assertRaises(SettingsError):self.confirm({'draft_id':fresh['id']})
        self.assertEqual(self.calls,['register','observe'])
        self.assertFalse(any(row['resumed'] for row in self.receipts()))

    def test_unknown_recovery_decline_discloses_partial_setup(self):
        old=self.fail_setup('unknown')
        result=self.settings.settle_pending(self.service.settings_owner(self.job),self.job['channel'],self.recovery_rows(),False)
        self.assertEqual(result['state'],'canceled')
        self.assertIn('부분 설정은 남아',result['response'])
        self.assertEqual(self.settings._drafts()[old['id']]['state'],'canceled')
        self.assertEqual(self.calls,['register','observe'])

    def test_retry_budget_survives_new_drafts_and_restart(self):
        self.fail_setup()
        from personal_agent.settings_orchestrator import SettingsOrchestrator
        self.settings=SettingsOrchestrator(self.store,now=lambda:self.clock[0],service=self.service)
        self.service.settings_orchestrator=self.settings
        self.settings.reconcile()
        for _ in range(2):
            rows=self.recovery_rows()
            self.assertEqual(len(rows),1)
            self.observations=[{'state':'failed'}]
            result=self.settings.settle_pending(self.service.settings_owner(self.job),self.job['channel'],rows,True)
            self.assertEqual(result['state'],'failed')
        self.assertEqual(self.service.mcp_setup_attempts(self.job['id']),3)
        self.assertEqual(self.recovery_rows(),[])
        self.observations=[{'state':'connected','tools':['get_record']}]
        fourth=self.propose()
        with self.assertRaisesRegex(SettingsError,'한도'):self.confirm(fourth)
        self.assertEqual(self.calls,['register','observe','observe','observe'])
        self.assertFalse(any(row['resumed'] for row in self.receipts()))

    def test_concurrent_admission_rechecks_budget_inside_transaction(self):
        from personal_agent.settings_orchestrator import SettingsOrchestrator
        self.fail_setup()
        self.fail_setup()
        drafts=[self.propose(),self.propose()]
        other=SettingsOrchestrator(self.store,now=lambda:self.clock[0],service=self.service)
        def admit(pair):
            settings,draft=pair
            row=settings._drafts()[draft['draft_id']]
            try:return settings._admit(row,row['digest'])['state']
            except SettingsError:return 'refused'
        with mock.patch.object(self.service,'mcp_setup_attempts',return_value=2):
            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes=list(pool.map(admit,zip((self.settings,other),drafts)))
        self.assertCountEqual(outcomes,['applying','refused'])
        self.assertEqual(self.service.mcp_setup_attempts(self.job['id']),3)
        self.assertEqual(self.calls,['register','observe','observe'])

    def test_retry_requires_same_owner_authority_and_unexpired_offer(self):
        for change in ('stop','authority','expiry'):
            with self.subTest(change=change):
                self.job=self.store.job(self.store.enqueue('Original '+change,'changed-'+change))
                failed=self.fail_setup()
                if change=='stop':self.status('cancelled')
                elif change=='authority':self.service.set_ai_connections({'enabled':True})
                else:self.settings.now=lambda:5000
                self.assertEqual(self.recovery_rows(),[])
                with self.assertRaises(SettingsError):
                    self.settings._renew(self.service.settings_owner(self.job),self.job['channel'],failed)
                self.assertFalse(any(row['resumed'] for row in self.receipts()))

    def test_completed_original_is_not_offered_or_replayed_by_recovery(self):
        failed=self.fail_setup()
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='succeeded',response=? WHERE id=?",('Completed via existing alternative.',self.job['id']))
        self.assertEqual(self.recovery_rows(),[])
        with self.assertRaises(SettingsError):
            self.settings._renew(self.service.settings_owner(self.job),self.job['channel'],failed)
        self.assertEqual(self.store.job(self.job['id'])['status'],'succeeded')
        self.assertEqual(self.calls,['register','observe'])

    def test_completion_during_recovery_cannot_requeue_original(self):
        failed=self.fail_setup()
        fresh=self.settings._renew(self.service.settings_owner(self.job),self.job['channel'],failed)
        self.observations=[{'state':'connected','tools':['get_record']}]
        self.on_observe=lambda:self.status('succeeded')
        with self.assertRaises(SettingsError):self.confirm({'draft_id':fresh['id']})
        self.service.resume_settings_work()
        self.assertEqual(self.store.job(self.job['id'])['status'],'succeeded')
        self.assertFalse(any(row['resumed'] for row in self.receipts()))
        self.assertFalse(owner_mcp.confirmed(self.store,'claude-code'))

    def test_completion_after_recovery_commit_before_queue_cannot_replay(self):
        failed=self.fail_setup()
        self.observations=[{'state':'connected','tools':['get_record']}]
        original=self.service.resume_settings_work
        def complete_then_resume():
            self.status('succeeded')
            original()
        with mock.patch.object(self.service,'resume_settings_work',side_effect=complete_then_resume):
            self.settings.settle_pending(self.service.settings_owner(self.job),self.job['channel'],self.recovery_rows(),True)
        self.assertEqual(self.store.job(self.job['id'])['status'],'succeeded')
        self.assertFalse(any(row['resumed'] for row in self.receipts()))
        self.assertTrue(owner_mcp.confirmed(self.store,'claude-code'))

    def test_setup_only_failure_recovers_without_replaying_completed_conversation(self):
        draft=self.propose(continue_work='no')
        self.status('succeeded')
        self.observations=[{'state':'failed'}]
        with self.assertRaises(SettingsError):self.confirm(draft)
        rows=self.recovery_rows()
        self.assertEqual(len(rows),1)
        self.assertNotIn('continuation',rows[0])
        self.observations=[{'state':'connected','tools':['get_record']}]
        result=self.settings.settle_pending(self.service.settings_owner(self.job),self.job['channel'],rows,True)
        self.assertEqual(result['state'],'applied',result)
        self.assertEqual(self.store.job(self.job['id'])['status'],'succeeded')
        self.assertEqual(self.receipts(),[])
        self.assertEqual(self.calls,['register','observe','observe'])

    def test_declining_renewed_recovery_keeps_partial_setup_receipt(self):
        failed=self.fail_setup()
        fresh=self.settings._renew(self.service.settings_owner(self.job),self.job['channel'],failed)
        result=self.settings.cancel(self.service.settings_owner(self.job),self.job['channel'],fresh['id'])
        self.assertIn('부분 설정은 남아',result['response'])
        self.assertNotIn('아무것도 바꾸지',result['response'])
        self.assertTrue(self.service.mcp_setup_definition(PUBLIC))
        self.assertEqual(self.calls,['register','observe'])

    def test_owner_can_decline_failed_setup_without_retrying(self):
        row=self.fail_setup()
        result=self.settings.settle_pending(self.service.settings_owner(self.job),self.job['channel'],self.recovery_rows(),False)
        self.assertEqual(result['state'],'canceled')
        self.assertIn('부분 설정은 남아',result['response'])
        self.assertNotIn('아무것도 바꾸지',result['response'])
        self.assertTrue(self.service.mcp_setup_definition(PUBLIC))
        self.assertEqual(self.settings._drafts()[row['id']]['state'],'canceled')
        self.assertEqual(self.recovery_rows(),[])
        self.assertEqual(self.calls,['register','observe'])

    def test_changed_definition_or_authority_during_observation_blocks_admission(self):
        for changed in ('definition', 'authority', 'work'):
            with self.subTest(changed=changed):
                self.observations = [{'state': 'connected', 'tools': ['get_record']}]
                self.on_observe = (lambda: (self.home / '.claude.json').write_text('{}')) if changed == 'definition' else (
                    (lambda: self.service.set_ai_connections({'enabled': True})) if changed == 'authority' else lambda: self.status('cancelled'))
                draft = self.propose()
                with self.assertRaises(SettingsError):
                    self.confirm(draft)
                self.assertFalse(owner_mcp.confirmed(self.store, 'claude-code'))
                self.assertFalse(trust_record.hand(self.store, self.target())['revision'])

    def test_credential_or_command_never_enters_draft(self):
        for patch in ({'url': 'https://service.example/?token=private'}, {'command': '/bin/false'},
                      {'engine': 'codex'}, {'url': 'https://service.example/sk-123456789012345678901234567890'},
                      {'required_tools': []}, {'read_tools': ['unlisted']}):
            with self.subTest(patch=patch), self.assertRaises((SettingsError, ValueError)):
                self.propose(value=json.dumps({**PUBLIC, **patch}))
        self.assertEqual(self.settings._drafts(), {})
        self.assertFalse(self.calls)

    def test_cancel_and_expiry_do_not_start_setup(self):
        for action in ('cancel', 'expire'):
            with self.subTest(action=action):
                draft = self.propose()
                if action == 'cancel':
                    self.settings.cancel(self.service.settings_owner(self.job), self.job['channel'], draft['draft_id'])
                else:
                    self.clock[0] += self.settings.TTL_SECONDS + 1
                self.service.resume_settings_work()
                self.assertFalse(self.calls)
                self.assertNotEqual(self.store.job(self.job['id'])['status'], 'queued')

    def test_money_connection_only_introduces_named_reads(self):
        draft = self.propose(value=json.dumps({**PUBLIC, 'money': True}))
        self.confirm(draft)
        record = trust_record.hand(self.store, self.target())
        self.assertTrue(record['money'])
        self.assertEqual(record['rung'], 'report')
        self.assertEqual(record['operations'], {'get_record': 'read'})

    def test_existing_refusal_and_marking_are_not_overridden(self):
        trust_record.set_operation(self.store, self.target(), 'get_record', 'refused')
        with self.assertRaises(SettingsError):
            self.propose()
        self.assertEqual(self.calls, [])

    def test_atomic_authority_commit_rolls_back_on_confirmation_store_failure(self):
        draft = self.propose()
        with mock.patch('personal_agent.owner_mcp.set_confirmed', side_effect=ValueError('cannot persist')):
            with self.assertRaises(SettingsError):
                self.confirm(draft)
        self.assertFalse(trust_record.hand(self.store, self.target())['revision'])
        self.assertFalse(self.service.ai_connections_enabled())
        self.assertNotEqual(self.store.job(self.job['id'])['status'], 'queued')

    def test_restart_after_atomic_commit_recovers_same_work_without_registration(self):
        draft = self.propose()
        with mock.patch.object(self.service, 'resume_settings_work'):
            self.confirm(draft)
        restarted = AgentService(self.store)
        restarted.mcp_setup_home = self.home
        restarted.resume_settings_work()
        self.assertEqual(self.store.job(self.job['id'])['status'], 'queued')
        self.assertEqual(self.calls, ['register', 'observe'])

    def test_current_definition_is_rechecked_before_resume(self):
        draft = self.propose()
        with mock.patch.object(self.service, 'resume_settings_work'):
            self.confirm(draft)
        (self.home / '.claude.json').write_text('{}')
        self.service.resume_settings_work()
        self.assertNotEqual(self.store.job(self.job['id'])['status'], 'queued')

    def test_parallel_callbacks_resume_once(self):
        draft = self.propose()
        with mock.patch.object(self.service, 'resume_settings_work'):
            self.confirm(draft)
        services = [AgentService(self.store) for _ in range(3)]
        for service in services:
            service.mcp_setup_home = self.home
        with ThreadPoolExecutor(3) as pool:
            list(pool.map(lambda s: s.resume_settings_work(), services))
        self.assertEqual(sum(row['resumed'] for row in self.receipts()), 1)

    def pair(self, patch=None):
        first = self.propose()
        first_job = self.job
        self.job = self.store.job(self.store.enqueue('A separate original request', 'second-original'))
        second = self.propose(value=json.dumps({**PUBLIC, **(patch or {})}))
        return first, first_job, second

    def test_two_original_works_share_setup_but_have_independent_receipts(self):
        first, first_job, second = self.pair()
        self.confirm(first)
        self.assertEqual(self.calls, ['register', 'observe'])
        for job, draft in ((first_job, first), (self.job, second)):
            self.assertEqual(self.store.job(job['id'])['status'], 'queued')
            self.assertEqual(self.store.job(job['id'])['message'], job['message'])
            events = [e['trace'] for e in self.store.task_events(job['id']) if e['tool'] == 'settings_continuation']
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]['draft_ids'], [draft['draft_id']])
            self.assertTrue(events[0]['resumed'])
        row = self.settings._drafts()[second['draft_id']]
        self.assertEqual(row['state'], 'satisfied')
        self.assertEqual(row['setup_from'], first['draft_id'])
        self.assertNotIn('setup_receipt', row, 'do not invent a fresh observation')
        with self.assertRaises(SettingsError):
            self.confirm(second)
        self.service.resume_settings_work()
        self.assertEqual(self.calls, ['register', 'observe'])

    def test_second_confirmation_admitted_while_first_setup_runs_reuses_at_apply(self):
        first, first_job, second = self.pair()
        row = self.settings._drafts()[first['draft_id']]
        self.settings._admit(row, row['digest'])
        row = self.settings._drafts()[second['draft_id']]
        admitted = self.settings._admit(row, row['digest'])
        self.settings._checked_apply(self.settings._drafts()[first['draft_id']])
        self.assertEqual(self.settings._drafts()[second['draft_id']]['state'], 'applying')
        result = self.settings._checked_apply(admitted)
        self.assertEqual(result['state'], 'satisfied')
        self.assertEqual(self.calls, ['register', 'observe'])
        for job in (first_job, self.job):
            self.assertEqual(self.store.job(job['id'])['status'], 'queued')
        self.assertFalse(self.settings._in_flight)

    def test_stopped_admitted_work_cannot_use_completed_setup(self):
        first, _, second = self.pair()
        row = self.settings._drafts()[second['draft_id']]
        admitted = self.settings._admit(row, row['digest'])
        self.confirm(first)
        self.status('cancelled')
        with self.assertRaises(SettingsError):
            self.settings._checked_apply(admitted)
        self.assertEqual(self.calls, ['register', 'observe'])
        self.assertEqual(self.store.job(self.job['id'])['status'], 'cancelled')
        self.assertNotIn('setup_from', self.settings._drafts()[second['draft_id']])

    def test_shared_setup_covers_tool_subsets_without_changing_trust(self):
        self.observations = [{'state': 'connected', 'tools': ['get_record', 'list_records']}]
        first = self.propose(value=json.dumps({**PUBLIC, 'required_tools': ['get_record', 'list_records'],
                                              'read_tools': ['get_record', 'list_records']}))
        self.confirm(first)
        revision = trust_record.hand(self.store, self.target())['revision']
        self.job = self.store.job(self.store.enqueue('Another record', 'third-original'))
        second = self.propose()
        self.service.resume_settings_work()
        self.assertEqual(self.settings._drafts()[second['draft_id']]['state'], 'satisfied')
        self.assertEqual(trust_record.hand(self.store, self.target())['revision'], revision)
        self.assertEqual(self.calls, ['register', 'observe'])

    def run_original_results(self, jobs, answers):
        before_count=len(self.store.jobs())
        import subprocess
        from personal_agent.bounded_execution import BoundedExecutionAdapter
        from personal_agent.subscription_engines import SubscriptionEngines
        turns = []
        def runner(argv, **kwargs):
            if argv[1:3] == ['auth', 'status']:
                return subprocess.CompletedProcess(argv, 0, json.dumps({'loggedIn': True}), '')
            config = json.loads((Path(kwargs['cwd']) / 'agentos-mcp.json').read_text())
            self.assertEqual(config['mcpServers'][PUBLIC['name']], {'type': 'http', 'url': PUBLIC['url']})
            answer = answers[len(turns)]
            turns.append(argv)
            return subprocess.CompletedProcess(argv, 0, json.dumps(
                {'type': 'result', 'subtype': 'success', 'is_error': False, 'result': answer}), '')
        self.service.execution_adapter = BoundedExecutionAdapter(finder=lambda name: '/runtime/'+name, runner=runner,
                                                                 runtime_root=self.store.root/'turns')
        self.service.subscription_engines = SubscriptionEngines(finder=lambda _: '/runtime/claude', clock=lambda: 1)
        self.service.connect_subscription_engine({'engine': 'claude-code', 'officially_authenticated': True})
        self.service.cli_native_search = lambda *args: (False, 'refused')
        definitions = owner_mcp.claude_definitions
        with mock.patch('personal_agent.owner_mcp.claude_definitions', side_effect=lambda home, names: definitions(self.home, names)):
            for _ in jobs:self.assertTrue(self.service.run_one())
        for job, answer in zip(jobs, answers):
            result = self.store.job(job['id'])
            self.assertEqual(result['status'], 'succeeded', result)
            self.assertEqual(result['response'], answer)
            self.assertEqual(result['message'], job['message'])
        self.assertEqual(len(self.store.jobs()), before_count, 'no new request or Work was manufactured')
        self.assertEqual(len(turns), len(jobs))

    def test_shared_setup_runs_each_original_work_to_its_own_result(self):
        first, first_job, _ = self.pair()
        self.confirm(first)
        self.run_original_results((first_job,self.job),['The first record has two incomplete items.','The second request has a Friday deadline.'])
        self.assertEqual(self.calls,['register','observe'])

    def test_noncovering_setup_keeps_other_work_waiting(self):
        for patch in ({'url': 'https://other.example/mcp'}, {'source': 'https://other.example/source'},
                      {'publisher': 'Another publisher'}, {'licence': 'MIT'}, {'money': True},
                      {'required_tools': ['missing'], 'read_tools': []},
                      {'required_tools': ['list_records'], 'read_tools': ['list_records']}):
            with self.subTest(patch=patch):
                # Reset the independent fixture so each attempted mismatch starts unconnected.
                case = McpContinuation(methodName='test_exact_confirmation_registers_observes_and_resumes_once')
                case.setUp()
                try:
                    case.observations = [{'state': 'connected', 'tools': ['get_record', 'list_records']}]
                    first, _, second = case.pair(patch)
                    case.confirm(first)
                    self.assertEqual(case.settings._drafts()[second['draft_id']]['state'], 'awaiting-confirmation')
                    self.assertEqual(case.store.job(case.job['id'])['status'], 'awaiting_connection')
                    self.assertEqual(case.calls, ['register', 'observe'])
                finally:
                    case.doCleanups()

    def test_cancelled_or_expired_wait_is_not_satisfied_by_other_setup(self):
        for action in ('cancel', 'expire'):
            with self.subTest(action=action):
                first, _, second = self.pair()
                if action == 'cancel':
                    self.settings.cancel(self.service.settings_owner(self.job), self.job['channel'], second['draft_id'])
                else:
                    self.clock[0] += self.settings.TTL_SECONDS + 1
                if action == 'expire':
                    # A newly proposed setup may complete; the older Work's expiry still holds.
                    self.job = self.store.job(self.store.enqueue('New setup request', 'new-setup'))
                    first = self.propose()
                self.observations = [{'state': 'connected', 'tools': ['get_record']}]
                self.confirm(first)
                self.assertNotEqual(self.settings._drafts()[second['draft_id']]['state'], 'satisfied')

    def test_shared_receipt_restart_and_current_authority_checks(self):
        first, _, second = self.pair()
        with mock.patch.object(self.service, 'resume_settings_work'):
            self.confirm(first)
        restarted = AgentService(self.store)
        restarted.mcp_setup_home = self.home
        restarted.settings_orchestrator.now = lambda: self.clock[0]
        with ThreadPoolExecutor(3) as pool:
            list(pool.map(lambda _: restarted.resume_settings_work(), range(3)))
        row = self.settings._drafts()[second['draft_id']]
        self.assertTrue(restarted.mcp_setup_ready(row))
        self.assertEqual(sum(receipt['resumed'] for receipt in self.receipts()), 1)
        trust_record.set_operation(self.store, self.target(), 'get_record', 'refused')
        self.assertFalse(restarted.mcp_setup_ready(row))
        self.assertEqual(self.calls, ['register', 'observe'])

    def test_another_owner_generation_cannot_supply_shared_readiness(self):
        first, _, second = self.pair()
        with mock.patch.object(self.service, 'resume_settings_work'):
            self.confirm(first)
        rows = self.settings._drafts()
        rows[first['draft_id']]['setup_guard'] = {'generation': 'different-owner'}
        rows[first['draft_id']]['digest'] = self.settings._digest(rows[first['draft_id']])
        self.store.put('settings_change_drafts', rows)
        self.service.resume_settings_work()
        self.assertEqual(self.settings._drafts()[second['draft_id']]['state'], 'awaiting-confirmation')
        self.assertEqual(self.store.job(self.job['id'])['status'], 'awaiting_connection')

    def test_shared_settlement_cannot_win_concurrent_cancellation(self):
        first, _, second = self.pair()
        with mock.patch.object(self.service, 'resume_settings_work'):
            self.confirm(first)
        settle = self.settings._settle
        def racing_settle(draft_id, state, terminal, *args, **kwargs):
            if draft_id == second['draft_id'] and state == 'satisfied':
                settle(draft_id, 'canceled', 'canceled')
            return settle(draft_id, state, terminal, *args, **kwargs)
        with mock.patch.object(self.settings, '_settle', side_effect=racing_settle):
            self.service.resume_settings_work()
        self.service.resume_settings_work()
        self.assertEqual(self.settings._drafts()[second['draft_id']]['state'], 'canceled')
        self.assertNotEqual(self.store.job(self.job['id'])['status'], 'queued')
        self.assertFalse(any(row['resumed'] for row in self.receipts()))

    def test_forget_restore_never_revives_shared_queued_work(self):
        from personal_agent.owner_forget import OwnerForget
        from personal_agent.agent_runtime import MEMORY_OWNER
        first, first_job, second = self.pair()
        self.confirm(first)
        self.assertEqual(self.settings._drafts()[second['draft_id']]['state'], 'satisfied')
        lifecycle = OwnerForget(self.store)
        receipt = lifecycle.forget(MEMORY_OWNER, 'trust:claude-code|owner-mcp|unseen-service')
        lifecycle.undo(MEMORY_OWNER, receipt['receipt'], confirmed_authority=True)
        self.service.resume_settings_work()
        for job in (first_job, self.job):
            self.assertEqual(self.store.job(job['id'])['status'], 'cancelled')
        self.assertFalse(self.service.mcp_setup_ready(self.settings._drafts()[second['draft_id']]))

    def test_shared_source_must_keep_definition_receipt_and_current_revision(self):
        first, _, second = self.pair()
        with mock.patch.object(self.service, 'resume_settings_work'):
            self.confirm(first)
        second_row = self.settings._drafts()[second['draft_id']]
        self.assertIsNotNone(self.service.mcp_covering_setup(second_row))
        original = (self.home / '.claude.json').read_text()
        (self.home / '.claude.json').write_text('{}')
        self.assertIsNone(self.service.mcp_covering_setup(second_row))
        (self.home / '.claude.json').write_text(original)
        self.service.set_ai_connections({'enabled': False})
        self.assertIsNone(self.service.mcp_covering_setup(second_row))
        self.service.set_ai_connections({'enabled': True})
        rows = self.settings._drafts()
        rows[first['draft_id']]['after'] = json.dumps({**PUBLIC, 'publisher': 'Tampered'})
        self.store.put('settings_change_drafts', rows)
        self.assertIsNone(self.service.mcp_covering_setup(second_row))

    def test_late_insert_after_forget_restore_cannot_share_new_setup(self):
        from personal_agent.owner_forget import OwnerForget
        from personal_agent.agent_runtime import MEMORY_OWNER
        trust_record.set_operation(self.store, self.target(), 'get_record', 'read')
        lifecycle = OwnerForget(self.store)
        put = self.settings._put_drafts
        def revoke_before_insert(rows):
            receipt = lifecycle.forget(MEMORY_OWNER, 'trust:claude-code|owner-mcp|unseen-service')
            lifecycle.undo(MEMORY_OWNER, receipt['receipt'], confirmed_authority=True)
            return put(rows)
        with mock.patch.object(self.settings, '_put_drafts', side_effect=revoke_before_insert):
            old = self.propose()
        old_job = self.job
        self.job = self.store.job(self.store.enqueue('A new request after restoration', 'after-restore'))
        self.confirm(self.propose())
        row = self.settings._drafts()[old['draft_id']]
        self.assertEqual(row['state'], 'cancelled')
        self.assertTrue(row['trust_revoked'])
        self.assertIsNone(self.service.mcp_covering_setup(row))
        self.assertNotEqual(self.store.job(old_job['id'])['status'], 'queued')
        self.assertEqual(self.store.job(self.job['id'])['status'], 'queued')

    def test_revocation_between_before_and_guard_capture_rejects_proposal(self):
        from personal_agent.owner_forget import OwnerForget
        from personal_agent.agent_runtime import MEMORY_OWNER
        trust_record.set_operation(self.store, self.target(), 'get_record', 'read')
        lifecycle = OwnerForget(self.store)
        guard = self.service.mcp_setup_guard
        def revoke_before_guard(draft):
            receipt = lifecycle.forget(MEMORY_OWNER, 'trust:claude-code|owner-mcp|unseen-service')
            lifecycle.undo(MEMORY_OWNER, receipt['receipt'], confirmed_authority=True)
            return guard(draft)
        with mock.patch.object(self.service, 'mcp_setup_guard', side_effect=revoke_before_guard):
            with self.assertRaises(SettingsError):
                self.propose()
        self.assertFalse(self.settings._drafts())
        self.assertFalse(self.calls)

    def test_pre_sharing_applied_receipt_resumes_itself_but_cannot_supply_another_work(self):
        first, first_job, second = self.pair()
        with mock.patch.object(self.service, 'resume_settings_work'):
            self.confirm(first)
        rows = self.settings._drafts()
        for ref in (first['draft_id'], second['draft_id']):
            rows[ref]['setup_guard'].pop('trust_epoch')
            rows[ref]['digest'] = self.settings._digest(rows[ref])
        self.store.put('settings_change_drafts', rows)
        self.service.resume_settings_work()
        self.assertEqual(self.store.job(first_job['id'])['status'], 'queued')
        self.assertEqual(self.store.job(self.job['id'])['status'], 'awaiting_connection')
        self.assertNotIn('setup_from', self.settings._drafts()[second['draft_id']])

    def test_owner_or_stop_change_after_shared_settlement_prevents_queue(self):
        from test_owner_settings_conversation import CHAT
        for change in ('disconnect', 'generation', 'user', 'stop'):
            with self.subTest(change=change):
                case = McpContinuation(methodName='test_exact_confirmation_registers_observes_and_resumes_once')
                case.setUp()
                try:
                    case.status('cancelled')
                    case.job = case.store.job(case.store.enqueue('First phone request', 'phone-first', 'telegram:gen-1', CHAT))
                    first = case.propose()
                    case.job = case.store.job(case.store.enqueue('Second phone request', 'phone-second', 'telegram:gen-1', CHAT))
                    second = case.propose()
                    with mock.patch.object(case.service, 'resume_settings_work'):
                        case.confirm(first)
                    settle = case.settings._settle
                    def change_after_settlement(ref, state, terminal, *args, **kwargs):
                        row = settle(ref, state, terminal, *args, **kwargs)
                        if ref == second['draft_id'] and state == 'satisfied':
                            pairing = case.store.config('telegram', {})
                            if change == 'stop':
                                case.status('cancelled')
                            else:
                                pairing.update({'enabled': False} if change == 'disconnect' else
                                               {'generation': 'gen-2'} if change == 'generation' else {'user_id': CHAT + 1})
                                case.store.put('telegram', pairing)
                        return row
                    with mock.patch.object(case.settings, '_settle', side_effect=change_after_settlement):
                        case.service.resume_settings_work()
                    self.assertNotEqual(case.store.job(case.job['id'])['status'], 'queued')
                    self.assertFalse(any(row['resumed'] for row in case.receipts()))
                finally:
                    case.doCleanups()

    def test_unseen_service_discovery_setup_and_fresh_cli_complete_original_work(self):
        import subprocess
        from personal_agent.bounded_execution import BoundedExecutionAdapter
        from personal_agent.subscription_engines import SubscriptionEngines
        from personal_agent.capability_search import CapabilitySearch
        from test_capability_search import payload
        from test_ai_connections import INIT
        turns = []
        discovery = mock.Mock(return_value=(200, {}, payload(remotes=[{'type': 'streamable-http', 'url': PUBLIC['url']}],
                                        repository={'url': PUBLIC['source']})))
        self.service.capability_search = CapabilitySearch(discovery)
        original_definitions = owner_mcp.claude_definitions
        tool = 'mcp__unseen-service__get_record'

        def runner(argv, **kwargs):
            if argv[1:3] == ['auth', 'status']:
                return subprocess.CompletedProcess(argv, 0, json.dumps({'loggedIn': True}), '')
            turns.append(argv)
            config = json.loads((Path(kwargs['cwd']) / 'agentos-mcp.json').read_text())
            server = config['mcpServers']['agentos']
            if len(turns) == 1:
                self.assertNotIn(PUBLIC['name'], config['mcpServers'])
                requests = [INIT,
                    {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': 'capability_search',
                        'arguments': {'source': 'registry', 'query': 'novel'}}},
                    {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'settings_change',
                        'arguments': {'category': 'ai_connections', 'setting': 'connect', 'value': json.dumps(PUBLIC), 'continue_work': 'yes'}}}]
                answer = '연결 확인 후 요청한 자료를 요약하겠습니다.'
                records = []
            else:
                self.assertIn(PUBLIC['name'], config['mcpServers'], self.store.task_events(self.job['id']))
                self.assertEqual(config['mcpServers'][PUBLIC['name']], {'type': 'http', 'url': PUBLIC['url']})
                requests = [INIT, {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'connector_permission',
                                         'arguments': {'tool_name': tool, 'input': {}}}}]
                answer = '요청한 기록의 핵심은 금요일 일정과 미완료 항목 두 가지입니다.'
                records = [
                    {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'id': 'read', 'name': tool, 'input': {}}]}},
                    {'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': 'read', 'content': '금요일 일정; 미완료 항목 두 가지'}]}}]
            done = subprocess.run([server['command'], *server['args']],
                input=''.join(json.dumps(row)+'\n' for row in requests), capture_output=True, text=True, timeout=30,
                env={**kwargs['env'], **server.get('env', {})})
            self.assertEqual(done.returncode, 0, done.stderr)
            replies = {row['id']: row for line in done.stdout.splitlines() if (row := json.loads(line)).get('id')}
            response = json.loads(replies[3]['result']['content'][0]['text'])
            if len(turns) == 1:
                self.assertFalse(response['applied'])
                self.assertTrue(response['continues_work'])
            else:
                self.assertEqual(response['behavior'], 'allow', response)
            records.append({'type': 'result', 'subtype': 'success', 'is_error': False, 'result': answer})
            return subprocess.CompletedProcess(argv, 0, '\n'.join(json.dumps(row) for row in records), '')

        self.service.execution_adapter = BoundedExecutionAdapter(finder=lambda name: '/runtime/'+name, runner=runner,
                                                                 runtime_root=self.store.root/'turns')
        self.service.subscription_engines = SubscriptionEngines(finder=lambda _: '/runtime/claude', clock=lambda: 1)
        self.service.connect_subscription_engine({'engine': 'claude-code', 'officially_authenticated': True})
        self.service.cli_native_search = lambda *args: (False, 'refused')
        with mock.patch('personal_agent.owner_mcp.claude_definitions', side_effect=lambda home, names: original_definitions(self.home, names)):
            self.assertTrue(self.service.run_one())
            self.assertEqual(self.store.job(self.job['id'])['status'], 'awaiting_connection', self.store.task_events(self.job['id']))
            [row] = self.settings.pending_for_work(self.job['id'])
            self.confirm({'draft_id': row['id']})
            self.assertEqual(self.store.job(self.job['id'])['status'], 'queued')
            self.assertTrue(self.service.run_one())
        result = self.store.job(self.job['id'])
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertIn('미완료 항목 두 가지', result['response'])
        self.assertEqual(len(self.store.jobs()), 1)
        self.assertEqual(len(turns), 2)
        discovery.assert_called_once()
        audit = self.service.work_information_use(self.job['id'])
        self.assertTrue(audit['sent_to']['lookups'])
        self.assertEqual({row['phase'] for row in audit['sent_to']['connections']}, {'register', 'observe'})
        self.assertTrue(any(row['tool'] == 'connector_read' and row['status'] == 'succeeded' for row in audit['results']))

    def test_long_telegram_confirmation_keeps_all_metadata_before_controls(self):
        from test_owner_settings_conversation import CHAT
        self.job = self.store.job(self.store.enqueue('Connect and summarize the record', 'phone', 'telegram:gen-1', CHAT))
        data = {**PUBLIC, 'url': 'https://service.example/' + 'a' * 900,
                'source': 'https://publisher.example/' + 'b' * 900}
        draft = self.propose(value=json.dumps(data))
        self.service.queue_settings_confirmation(self.job)
        self.assertTrue(self.service.deliver_notification())
        sent = [body for method, body in self.telegram if method == 'sendMessage']
        self.assertGreater(len(sent), 1)
        combined = ''.join(body['text'].split('\n', 1)[1] for body in sent)
        self.assertIn(data['url'], combined)
        self.assertIn(data['source'], combined)
        self.assertTrue(sent[-1].get('reply_markup'))
        self.assertFalse(any(body.get('reply_markup') for body in sent[:-1]))
        self.assertFalse(self.calls)

    def test_tampered_setup_or_changed_owner_before_confirmation_runs_nothing(self):
        draft = self.propose()
        rows = self.settings._drafts()
        rows[draft['draft_id']]['setup_guard'] = {'generation': 'different'}
        self.settings._put_drafts(rows)
        with self.assertRaises(SettingsError):
            self.confirm(draft)
        self.assertFalse(self.calls)
        self.settings.cancel(self.service.settings_owner(self.job), self.job['channel'], draft['draft_id'])
        fresh = self.propose()
        self.service.set_ai_connections({'enabled': True})
        with self.assertRaises(SettingsError):
            self.confirm(fresh)
        self.assertFalse(self.calls)

    def test_mid_apply_restart_is_unknown_and_does_not_replay_login(self):
        draft = self.propose()
        row = self.settings._drafts()[draft['draft_id']]
        self.settings._admit(row, row['digest'])
        self.client(lambda: None).register(PUBLIC['engine'], PUBLIC['name'], PUBLIC['url'])
        restarted = AgentService(self.store)
        restarted.mcp_setup_home = self.home
        restarted.settings_orchestrator.reconcile()
        self.assertEqual(restarted.settings_orchestrator._drafts()[row['id']]['state'], 'unknown')
        restarted.resume_settings_work()
        self.assertNotEqual(self.store.job(self.job['id'])['status'], 'queued')
        self.assertEqual(self.calls, ['register'])
        self.assertFalse(owner_mcp.confirmed(self.store, 'claude-code'))

    def test_failed_observation_audit_never_claims_received_or_leaks_raw_output(self):
        self.observations = [{'state': 'failed', 'error': 'private-message', 'authUrl': 'https://private.example/?token=secret'}]
        with self.assertRaises(SettingsError):
            self.confirm(self.propose())
        audit = self.service.work_information_use(self.job['id'])
        connection = next(row for row in audit['sent_to']['connections'] if row['phase'] == 'observe')
        self.assertEqual(connection['state'], 'failed')
        self.assertEqual(connection['remote_receipt'], 'unknown')
        self.assertNotIn('private-message', json.dumps(self.store.task_events(self.job['id'])))
        self.assertNotIn('private.example', json.dumps(audit))

    def test_current_telegram_pairing_is_required_at_authority_commit(self):
        from test_owner_settings_conversation import CHAT
        for change in ('disconnect', 'different-owner', 'generation'):
            with self.subTest(change=change):
                self.store.put('telegram', {'enabled': True, 'user_id': CHAT, 'generation': 'gen-1'})
                self.job = self.store.job(self.store.enqueue('Read the record', 'paired-' + change, 'telegram:gen-1', CHAT))
                draft = self.propose()
                self.observations = [{'state': 'connected', 'tools': ['get_record']}]
                def change_pairing():
                    if change == 'disconnect':
                        self.service.disconnect_telegram()
                    else:
                        self.store.put('telegram', {'enabled': True, 'user_id': CHAT + (change == 'different-owner'),
                                                   'generation': 'gen-2' if change == 'generation' else 'gen-1'})
                self.on_observe = change_pairing
                with self.assertRaises(SettingsError):
                    self.confirm(draft)
                self.assertFalse(owner_mcp.confirmed(self.store, 'claude-code'))
                self.assertFalse(trust_record.hand(self.store, self.target())['revision'])
                self.assertFalse(self.service.ai_connections_enabled())

    def test_forget_revokes_json_setup_and_restore_cannot_revive_queued_work(self):
        from personal_agent.owner_forget import OwnerForget
        from personal_agent.agent_runtime import MEMORY_OWNER
        lifecycle = OwnerForget(self.store)
        ref = 'trust:claude-code|owner-mcp|unseen-service'
        for state in ('awaiting-confirmation', 'applying', 'queued'):
            with self.subTest(state=state):
                trust_record.set_operation(self.store, self.target(), 'get_record', 'read')
                self.job = self.store.job(self.store.enqueue('Read the record', 'forget-' + state))
                draft = self.propose()
                self.observations = [{'state': 'connected', 'tools': ['get_record']}]
                if state == 'queued':
                    self.confirm(draft)
                    self.assertEqual(self.store.job(self.job['id'])['status'], 'queued')
                elif state == 'applying':
                    row = self.settings._drafts()[draft['draft_id']]
                    self.settings._admit(row, row['digest'])
                    self.status('awaiting_connection')
                else:
                    self.service.resume_settings_work()
                receipt = lifecycle.forget(MEMORY_OWNER, ref)
                row = self.settings._drafts()[draft['draft_id']]
                self.assertTrue(row.get('trust_revoked'))
                self.assertEqual(row['state'], 'cancelled')
                self.assertEqual(self.store.job(self.job['id'])['status'], 'cancelled')
                lifecycle.undo(MEMORY_OWNER, receipt['receipt'], confirmed_authority=True)
                self.service.resume_settings_work()
                self.assertEqual(self.store.job(self.job['id'])['status'], 'cancelled')

    def test_private_and_ambiguous_destinations_never_create_setup_draft(self):
        for url in ('https://127.0.0.1/mcp', 'https://192.168.1.1/mcp', 'https://[::1]/mcp',
                    'https://localhost/mcp', 'https://host.local/mcp', 'https://metadata.google.internal/mcp',
                    'https://127.1/mcp', 'https://2130706433/mcp', 'https://0x7f000001/mcp',
                    'https://0177.0.0.1/mcp', 'https://%31%32%37.0.0.1/mcp'):
            for key in ('url', 'source'):
                with self.subTest(url=url, key=key), self.assertRaises(SettingsError):
                    self.propose(value=json.dumps({**PUBLIC, key: url}))
        self.assertFalse(self.calls)
        self.assertFalse(self.settings._drafts())

    def test_forget_during_external_observation_prevents_final_admission(self):
        from personal_agent.owner_forget import OwnerForget
        from personal_agent.agent_runtime import MEMORY_OWNER
        trust_record.set_operation(self.store, self.target(), 'get_record', 'read')
        draft = self.propose()
        self.on_observe = lambda: OwnerForget(self.store).forget(MEMORY_OWNER, 'trust:claude-code|owner-mcp|unseen-service')
        with self.assertRaises(SettingsError):
            self.confirm(draft)
        self.assertTrue(self.settings._drafts()[draft['draft_id']]['trust_revoked'])
        self.assertFalse(owner_mcp.confirmed(self.store, 'claude-code'))
        self.assertFalse(self.service.ai_connections_enabled())
        self.assertEqual(trust_record.hand(self.store, self.target())['state'], 'forgotten')
