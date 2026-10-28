"""CONNECT-EFFECT-01 (#1298): exact AI-side effects and original-Work continuation.

Injected transports and real owner store/approval queue; no account or model calls.
"""
import json
import tempfile
import unittest
from pathlib import Path

from personal_agent import owner_mcp, trust_record
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore

ACTION = 'mcp__synthetic__apply_change'
INPUT = {'account': 'owner-account-a', 'item': 'unit-a', 'quantity': 2, 'amount': '4.00'}


class EffectBoundary(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = QuickStore(Path(temp.name) / 'state')
        self.service = AgentService(self.store)
        self.service.set_ai_connections({'enabled': True})
        owner_mcp.set_confirmed(self.store, 'codex', ['synthetic'])
        self.target = trust_record.connection('codex', 'owner-mcp', 'synthetic')
        self.job = self.store.job(self.store.enqueue('Apply the changes and summarize the observed result', 'original'))

    def call(self, values=None, effect=None, tool=ACTION):
        return self.service.connector_permission(self.job, {'engine': 'codex', 'tool_name': tool,
                    'input': INPUT if values is None else values, 'effect': effect})

    def partial(self):
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='partial' WHERE id=?", (self.job['id'],))

    def approve(self):
        self.partial()
        return self.service.browser_step_decision({'work_id': self.job['id'], 'decision': 'approve', 'digest': self.service._browser_request(self.job['id'])['offer_digest']})

    def test_reviewed_non_payment_mutation_runs_and_records_the_observed_effect(self):
        trust_record.set_operation(self.store, self.target, 'apply_change', 'mutate')
        self.assertEqual(self.call()['behavior'], 'allow')
        self.assertIsNone(self.service._browser_request(self.job['id']))
        digest = self.service.connector_input_digest(ACTION, INPUT)
        self.service.record_connector_calls(self.job['id'], [{'tool': ACTION, 'status': 'succeeded',
                                                               'input_digest': digest}], engine='codex')
        event = [e for e in self.store.task_events(self.job['id']) if e['tool'] == 'connector_read'][-1]
        self.assertEqual(event['trace']['effect'], 'mutate')
        self.assertEqual(event['trace']['evidence']['operation'], 'apply_change')
        self.assertEqual(owner_mcp.confirmed(self.store, 'codex'), ['synthetic'])
        self.assertEqual(self.call()['behavior'], 'deny', 'a completed effect is not repeated by this Work')

    def test_money_floor_waits_then_exact_approval_continues_original_work(self):
        trust_record.set_money(self.store, self.target, True)
        self.assertEqual(self.call(effect='read')['behavior'], 'deny')
        pending = self.service._browser_request(self.job['id'])
        self.assertIsNotNone(pending)
        self.assertEqual(pending['action'], 'connector_call')
        for value in INPUT.values():
            self.assertIn(str(value), pending['label'])
        self.assertTrue(self.approve()['resumed'])
        self.assertEqual(self.store.job(self.job['id'])['status'], 'queued')
        self.assertEqual(self.call(effect='read')['behavior'], 'allow')
        self.assertEqual(self.call(effect='read')['behavior'], 'deny', 'spent approval never permits a second dispatch')

    def test_changed_input_or_authority_cannot_inherit_approval(self):
        for change in ('input', 'authority'):
            with self.subTest(change=change):
                self.setUp()
                trust_record.set_money(self.store, self.target, True)
                self.call()
                self.approve()
                if change == 'authority':
                    trust_record.set_rung(self.store, self.target, 'approve')
                    values = INPUT
                else:
                    values = {**INPUT, 'account': 'owner-account-b'}
                self.assertEqual(self.call(values)['behavior'], 'deny')

    def test_owner_refusal_is_never_overridden_by_a_model_label(self):
        trust_record.set_operation(self.store, self.target, 'apply_change', 'refused')
        self.assertEqual(self.call(effect='mutate')['behavior'], 'deny')
        self.assertIsNone(self.service._browser_request(self.job['id']))

    def test_parallel_distinct_effects_recheck_unknown_state_inside_the_claim(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier,local
        from unittest import mock
        trust_record.set_operation(self.store,self.target,'apply_change','mutate')
        other=AgentService(self.store)
        barrier=Barrier(2);seen=local()
        original=self.service._work_has_unknown_effect
        def checked(work_id):
            result=original(work_id)
            if not getattr(seen,'checked',False):
                seen.checked=True
                barrier.wait(timeout=5)
            return result
        def call(pair):
            service,quantity=pair
            return service.connector_permission(self.job,{'engine':'codex','tool_name':ACTION,'input':{**INPUT,'quantity':quantity}})
        with mock.patch.object(self.service,'_work_has_unknown_effect',side_effect=checked),mock.patch.object(other,'_work_has_unknown_effect',side_effect=checked):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(call,((self.service,2),(other,3))))
        self.assertEqual(sorted(row['behavior'] for row in results),['allow','deny'])
        self.assertEqual(len(self.service.connector_effect_progress(self.job['id'])[1]),1)

    def test_unmatched_unknown_effect_also_blocks_vendor_connector_mutations(self):
        tool='mcp__claude_ai_Synthetic__apply_change'
        target=trust_record.connection('claude-code','ai-connection','Synthetic')
        trust_record.set_operation(self.store,target,'apply_change','mutate')
        self.service.record_connector_calls(self.job['id'],[{'tool':tool,'status':'succeeded'}],engine='claude-code')
        self.assertEqual(self.service.connector_effect_progress(self.job['id'])[1],[],'there is no dispatch claim to hide the unmatched observation')
        self.assertTrue(self.service._work_has_unknown_effect(self.job['id']))
        decision=self.service.connector_permission(self.job,{'engine':'claude-code','tool_name':tool,'input':INPUT})
        self.assertEqual(decision['behavior'],'deny')

    def test_resumed_worker_failure_cannot_redelegate_after_a_prior_completed_effect(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        trust_record.set_operation(self.store,self.target,'apply_change','mutate')
        self.assertEqual(self.call()['behavior'],'allow')
        self.service.record_connector_calls(self.job['id'],[{'tool':ACTION,'status':'succeeded',
            'input_digest':self.service.connector_input_digest(ACTION,INPUT)}],engine='codex')
        with self.store.db() as db:since=db.execute('SELECT MAX(id) FROM tool_events WHERE job_id=?',(self.job['id'],)).fetchone()[0]
        orchestration=SimpleNamespace(orchestrated=True,_redact=lambda text:text,next=Mock(return_value=None))
        self.service.orchestration_step(orchestration,object(),self.job['id'],since,failed='resumed worker failed before any tools')
        # #387: an observed change (here a completed connector effect) is named as such.
        self.assertEqual(orchestration.next.call_args.kwargs['effect'],'changed')

    def test_rejected_or_expired_offer_cannot_approve_an_identical_new_offer(self):
        from unittest import mock
        for settlement in ('deny','expired'):
            with self.subTest(settlement=settlement):
                self.setUp()
                trust_record.set_money(self.store,self.target,True)
                self.call();self.partial()
                first=self.service._browser_request(self.job['id'])
                if settlement=='deny':
                    self.service.browser_step_decision({'work_id':self.job['id'],'decision':'deny','digest':first['offer_digest']})
                else:
                    with mock.patch('personal_agent.quickstart_service.time.time',return_value=first['requested_at']+601):
                        self.service.resume_action_work()
                self.call()
                second=self.service._browser_request(self.job['id'])
                self.assertEqual(first['digest'],second['digest'],'same action, different offered confirmation')
                self.assertNotEqual(first['offer_digest'],second['offer_digest'])
                with self.assertRaises(ValueError):
                    self.service.browser_step_decision({'work_id':self.job['id'],'decision':'approve','digest':first['offer_digest']})
                self.assertEqual(self.store.job(self.job['id'])['status'],'partial')
                self.assertTrue(self.approve()['resumed'])

    def test_model_payment_label_can_raise_a_reviewed_mutation(self):
        trust_record.set_operation(self.store, self.target, 'apply_change', 'mutate')
        self.assertEqual(self.call(effect='payment')['behavior'], 'deny')
        self.assertIsNotNone(self.service._browser_request(self.job['id']))

    def test_cancelled_work_and_duplicate_approval_do_not_resume(self):
        trust_record.set_money(self.store, self.target, True)
        self.call()
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='cancelled' WHERE id=?", (self.job['id'],))
        result = self.service.browser_step_decision({'work_id': self.job['id'], 'decision': 'approve', 'digest': self.service._browser_request(self.job['id'])['offer_digest']})
        self.assertFalse(result['resumed'])
        self.assertEqual(self.call()['behavior'], 'deny')

    def test_restart_after_dispatch_cannot_replay_an_unobserved_effect(self):
        trust_record.set_money(self.store, self.target, True)
        self.call()
        self.approve()
        self.assertEqual(self.call()['behavior'], 'allow')
        self.service = AgentService(self.store)
        self.assertEqual(self.call()['behavior'], 'deny')
        self.assertIsNone(self.service._browser_request(self.job['id']), 'unknown dispatch must not ask to repeat')

    def test_stale_surface_cannot_approve_replaced_parameters(self):
        trust_record.set_money(self.store,self.target,True)
        self.call()
        original=self.service._browser_request(self.job['id'])['offer_digest']
        self.call({**INPUT,'quantity':3})
        self.partial()
        with self.assertRaises(ValueError):
            self.service.browser_step_decision({'work_id':self.job['id'],'decision':'approve','digest':original})
        self.assertEqual(self.store.job(self.job['id'])['status'],'partial')

    def test_expired_offer_does_not_issue_or_resume(self):
        from unittest import mock
        trust_record.set_money(self.store,self.target,True)
        self.call()
        requested=self.service._browser_request(self.job['id'])['requested_at']
        with mock.patch('personal_agent.quickstart_service.time.time',return_value=requested+601):
            self.assertFalse(self.approve()['approved'])
        self.assertEqual(self.store.job(self.job['id'])['status'],'partial')

    def test_cumulative_action_and_settings_continuation_limit_is_explained(self):
        trust_record.set_money(self.store,self.target,True)
        self.call()
        with self.store.db() as db:
            for index in range(8):
                db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,0)',
                    (self.job['id'],'settings_continuation' if index%2 else 'action_continuation','queued',json.dumps({'resumed':True,'action_digest':str(index)})))
        result=self.approve()
        self.assertFalse(result['resumed'])
        self.service.resume_action_work()
        self.assertIn('자동 재개 한도',self.store.job(self.job['id'])['owner_cause'])
        withheld=[e for e in self.store.task_events(self.job['id']) if e['tool']=='action_continuation' and e['status']=='withheld']
        self.assertEqual(len(withheld),1)

    def test_approval_cannot_restart_a_worker_with_unaccounted_host_actions(self):
        trust_record.set_money(self.store,self.target,True)
        self.call()
        with self.store.db() as db:
            db.execute("INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,'subscription_engine','failed',?,0)",
                (self.job['id'],json.dumps({'continuation_read_only':False,'continuation_accounted':False})))
        self.assertFalse(self.approve()['resumed'])
        self.assertEqual(self.store.job(self.job['id'])['status'],'partial')
        self.assertIn('앞선 동작',self.store.job(self.job['id'])['owner_cause'])

    def test_expired_issued_approval_and_owner_revocation_cannot_dispatch(self):
        from unittest import mock
        for changed in ('expired','revoked'):
            with self.subTest(changed=changed):
                self.setUp()
                trust_record.set_money(self.store,self.target,True)
                self.call();self.approve()
                if changed=='revoked':
                    self.service.set_ai_connections({'enabled':False})
                    self.assertEqual(self.call()['behavior'],'deny')
                else:
                    issued=self.service._browser_request(self.job['id'])['issued_at']
                    with mock.patch('personal_agent.quickstart_store.time.time',return_value=issued+601):
                        self.assertEqual(self.call()['behavior'],'deny')
                self.assertEqual(self.service.connector_effect_progress(self.job['id']),( [],[] ))

    def test_approval_during_worker_return_recovers_once_after_restart(self):
        trust_record.set_money(self.store,self.target,True)
        self.call()
        with self.store.db() as db:db.execute("UPDATE jobs SET status='running' WHERE id=?",(self.job['id'],))
        pending=self.service._browser_request(self.job['id'])
        result=self.service.browser_step_decision({'work_id':self.job['id'],'decision':'approve','digest':pending['offer_digest']})
        self.assertTrue(result['approved'])
        self.assertFalse(result['resumed'])
        self.partial()
        self.service=AgentService(self.store)
        self.service.resume_action_work()
        self.service.resume_action_work()
        self.assertEqual(self.store.job(self.job['id'])['status'],'queued')
        self.assertTrue(self.service.settings_work_continued(self.job['id']))
        self.assertEqual(len([e for e in self.store.task_events(self.job['id']) if e['tool']=='action_continuation']),1)
        self.assertEqual(self.call()['behavior'],'allow')

    def test_expiry_settles_issued_wait_without_requeue_or_late_confirmation(self):
        from unittest import mock
        trust_record.set_money(self.store,self.target,True)
        self.call()
        with self.store.db() as db:db.execute("UPDATE jobs SET status='running' WHERE id=?",(self.job['id'],))
        pending=self.service._browser_request(self.job['id'])
        self.service.browser_step_decision({'work_id':self.job['id'],'decision':'approve','digest':pending['offer_digest']})
        issued=self.service._browser_request(self.job['id'])['issued_at']
        self.partial()
        with mock.patch('personal_agent.quickstart_service.time.time',return_value=issued+601):
            self.service.resume_action_work()
            self.service.resume_action_work()
        self.assertIsNone(self.service._browser_request(self.job['id']))
        self.assertEqual(self.store.job(self.job['id'])['status'],'partial')
        self.assertIn('유효 시간',self.store.job(self.job['id'])['owner_cause'])
        events=[e for e in self.store.task_events(self.job['id']) if e['tool']=='action_continuation']
        self.assertEqual(len(events),1)
        self.assertEqual(events[0]['trace']['reason'],'expired')
        self.assertFalse(self.service._decide_browser_step(self.job['id'],True,pending['offer_digest'])['resumed'])

    def test_two_service_approvals_do_not_revoke_the_winning_token(self):
        from concurrent.futures import ThreadPoolExecutor
        trust_record.set_money(self.store,self.target,True)
        self.call();self.partial()
        body={'work_id':self.job['id'],'decision':'approve','digest':self.service._browser_request(self.job['id'])['offer_digest']}
        other=AgentService(self.store)
        def approve(service):
            try:return service.browser_step_decision(body)
            except ValueError:return {'resumed':False}
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(approve,(self.service,other)))
        self.assertEqual(sum(row['resumed'] for row in results),1)
        self.assertEqual(self.call()['behavior'],'allow')

    def test_disconnect_reconnect_same_names_invalidates_approval(self):
        trust_record.set_money(self.store,self.target,True)
        self.call();self.approve()
        owner_mcp.set_confirmed(self.store,'codex',[])
        owner_mcp.set_confirmed(self.store,'codex',['synthetic'])
        self.assertEqual(self.call()['behavior'],'deny')

    def test_actual_launch_configuration_change_invalidates_approval_without_storing_secrets(self):
        trust_record.set_money(self.store,self.target,True)
        self.service.record_connector_configuration(self.job,'codex',{'synthetic':{'url':'https://old.invalid','token':'private-test-value'}})
        self.call();self.approve()
        self.service.record_connector_configuration(self.job,'codex',{'synthetic':{'url':'https://new.invalid','token':'new-private-value'}})
        self.assertEqual(self.call()['behavior'],'deny')
        provenance=json.dumps(self.store.turn_provenance(self.job['id']))
        for value in ('private-test-value','new-private-value','https://old.invalid','https://new.invalid'):
            self.assertNotIn(value,provenance)

    def test_stale_rejection_cannot_remove_concurrently_issued_approval(self):
        from unittest import mock
        trust_record.set_money(self.store,self.target,True)
        self.call();self.partial()
        other=AgentService(self.store)
        put=self.service._put_browser_request
        def raced(work_id,row,**kwargs):
            self.assertTrue(other._decide_browser_step(work_id,True)['approved'])
            return put(work_id,row,**kwargs)
        with mock.patch.object(self.service,'_put_browser_request',side_effect=raced):
            self.assertFalse(self.service._decide_browser_step(self.job['id'],False)['approved'])
        self.assertEqual(self.service._browser_request(self.job['id'])['state'],'issued')
        self.assertEqual(self.call()['behavior'],'allow')

    def test_delayed_initial_offer_cannot_overwrite_a_concurrent_issued_offer(self):
        from unittest import mock
        trust_record.set_money(self.store,self.target,True)
        self.partial()
        other=AgentService(self.store)
        put=self.service._put_browser_request
        newer={**INPUT,'quantity':3}
        def raced(work_id,row,**kwargs):
            decision=other.connector_permission(self.job,{'engine':'codex','tool_name':ACTION,'input':newer})
            self.assertEqual(decision['behavior'],'deny')
            self.assertTrue(other._decide_browser_step(work_id,True)['resumed'])
            return put(work_id,row,**kwargs)
        with mock.patch.object(self.service,'_put_browser_request',side_effect=raced):
            self.assertEqual(self.call()['behavior'],'deny')
        self.assertEqual(self.service._browser_request(self.job['id'])['state'],'issued')
        self.assertEqual(self.call(newer)['behavior'],'allow')

    def test_sensitive_structures_never_reach_cards_evidence_or_classification(self):
        from unittest import mock
        specimens=[{'cvv':'secret-cvv-value'},{'nested':[{'clientSecret':'secret-client-value'}]},
                   {'private_key':'secret-private-value'},{'parameters':[{'name':'clientSecret','value':'secret-named-value'}]},
                   {'body':json.dumps({'nested':[{'clientSecret':'secret-json-value'}]})},
                   {'body':'cvv=secret-form-value'},
                   {'parameters':[{'Name':'clientSecret','Value':'secret-case-value'}]},
                   {'parameters':[{'Name':'clientSecret','name':'description','Value':'secret-collision-value'}]},
                   {'parameters':[{'FIELD':'private_key','VALUE':'secret-field-value'}]},
                   {'body':'private%5Fkey=secret-encoded-value'},
                   {'body':'clientSecret%3Dsecret-encoded-separator'},
                   {'body':'private%255Fkey=secret-nested-encoding'},
                   {'payload':'-----BEGIN RSA PRIVATE KEY----- secret-pem-value'}]
        for values in specimens:
            with self.subTest(values=tuple(values)):
                self.setUp()
                trust_record.set_money(self.store,self.target,True)
                with mock.patch.object(self.service.decision_judge,'connector_effect',side_effect=AssertionError('secret input must not reach classification')):
                    self.assertEqual(self.call(values)['behavior'],'deny')
                self.assertIsNone(self.service._browser_request(self.job['id']))
                self.assertEqual(self.service.connector_effect_progress(self.job['id']),( [],[] ))
                with self.store.db() as db:
                    tables=[row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
                    for table in tables:
                        content=json.dumps([[str(value) for value in tuple(row)] for row in db.execute(f'SELECT * FROM "{table}"')])
                        self.assertNotIn('secret-',content)

    def test_non_secret_pagination_metadata_remains_usable(self):
        trust_record.set_operation(self.store,self.target,'apply_change','read')
        self.assertEqual(self.call({'pageToken':'cursor-next-page','limit':20})['behavior'],'allow')

    def test_connection_control_discloses_mutations_and_exact_payment_approval(self):
        note=self.service.ai_connections_status()['note']
        self.assertIn('일반 변경',note)
        self.assertIn('매번 정확한 실행 내용',note)
        self.assertNotIn('읽기 전용',note)

    def test_live_and_final_observations_do_not_duplicate_an_effect(self):
        from personal_agent.bounded_execution import cli_metadata,live_progress_reader
        trust_record.set_operation(self.store,self.target,'apply_change','mutate')
        self.assertEqual(self.call()['behavior'],'allow')
        record={'type':'item.completed','item':{'id':'observed-one','type':'mcp_tool_call','server':'synthetic',
                'tool':'apply_change','arguments':INPUT,'status':'completed'}}
        line=json.dumps(record)
        observe=live_progress_reader('codex',None,lambda calls:self.service.record_connector_calls(self.job['id'],calls,engine='codex'),
                                     self.service.connector_input_digest)
        observe(line)
        meta=cli_metadata('codex',line,self.service.connector_input_digest)
        self.service.record_connector_calls(self.job['id'],meta['connector_reads'],engine='codex')
        self.assertEqual(len([e for e in self.store.task_events(self.job['id']) if e['tool']=='connector_read']),1)
        self.assertEqual(len(self.service.connector_effect_progress(self.job['id'])[0]),1)
        self.assertEqual(self.service.connector_effect_progress(self.job['id'])[1],[])
        self.assertEqual(owner_mcp.confirmed(self.store,'codex'),['synthetic'])
        trust_record.set_operation(self.store,self.target,'finish_change','payment')
        self.call(tool='mcp__synthetic__finish_change')
        self.assertTrue(self.approve()['resumed'])
        note=self.service.action_continuation_note(self.job)
        self.assertIn('Observed completed action',note)
        self.assertIn('apply_change',note)
        self.assertIn('finish_change',note)
        self.assertIn('remaining obligations',note)

    def test_full_parameters_are_offered_without_silent_truncation(self):
        trust_record.set_money(self.store, self.target, True)
        values = {**INPUT, 'description': 'x' * 150 + '  two  spaces', 'limit_amount': '987.65'}
        self.call(values)
        pending = self.service._browser_request(self.job['id'])
        self.assertIsNotNone(pending)
        self.assertIn('987.65', pending['label'])
        self.assertIn('  two  spaces', pending['label'])

    def test_model_effect_classification_uses_metadata_and_never_grants_a_read(self):
        from personal_agent.decision import FixtureDecisionEngine,SelectionDecision,OUTCOME_DECIDED,fixture_confidence
        for choice,behavior in (('mutate','allow'),('payment','deny'),('read','deny')):
            with self.subTest(choice=choice):
                self.setUp()
                engine=FixtureDecisionEngine(choose=lambda context,candidates,question:
                    SelectionDecision(OUTCOME_DECIDED,choice,candidates,fixture_confidence()))
                self.service.decision_judge.engine=engine
                self.assertEqual(self.call()['behavior'],behavior)
                context=engine.asked[-1][1]
                self.assertEqual(context.purpose,'connector-effect')
                self.assertIn('quantity',context.render())
                self.assertNotIn('owner-account-a',context.render())
                self.assertNotIn('unit-a',context.render())
                self.assertEqual(self.service._browser_request(self.job['id']) is not None,choice=='payment')

    def test_continuation_accounts_for_observed_effect_but_never_unknown_dispatch(self):
        from personal_agent.bounded_execution import cli_metadata
        trust_record.set_operation(self.store,self.target,'apply_change','mutate')
        with self.store.db() as db:
            db.execute("INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,'subscription_engine','running','{}',0)",(self.job['id'],))
        self.assertEqual(self.call()['behavior'],'allow')
        self.assertTrue(self.service._work_has_unknown_effect(self.job['id']))
        self.assertFalse(self.service.settings_resume_safe(self.job['id']))
        stream='\n'.join(json.dumps(row) for row in (
            {'type':'item.completed','item':{'id':'one','type':'mcp_tool_call','server':'synthetic',
                'tool':'apply_change','arguments':INPUT,'status':'completed'}}, {'type':'turn.completed'}))
        meta=cli_metadata('codex',stream,self.service.connector_input_digest)
        self.assertFalse(self.service._connector_turn_accounted(self.job['id'],'codex',meta,allow_effects=True))
        self.service.record_connector_calls(self.job['id'],meta['connector_reads'],engine='codex')
        self.assertFalse(self.service._work_has_unknown_effect(self.job['id']))
        self.assertFalse(self.service.connector_turn_read_only(self.job['id'],'codex',meta))
        self.assertTrue(self.service._connector_turn_accounted(self.job['id'],'codex',meta,allow_effects=True))
        with self.store.db() as db:
            db.execute("INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,'subscription_engine','succeeded',?,0)",
                       (self.job['id'],json.dumps({'continuation_read_only':False,'continuation_accounted':True})))
            db.execute("INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,'settings_continuation','queued','{}',0)",(self.job['id'],))
        self.assertTrue(self.service.settings_resume_safe(self.job['id']))
        self.assertIn('apply_change',self.service.action_continuation_note(self.job))
        # An extra built-in action or repeated invocation is not covered by this proof.
        extra={**meta,'tool_calls':meta['tool_calls']+[{'type':'command_execution','name':'shell'}]}
        self.assertFalse(self.service._connector_turn_accounted(self.job['id'],'codex',extra,allow_effects=True))
        duplicate={**meta,'tool_calls':meta['tool_calls']*2}
        self.assertFalse(self.service._connector_turn_accounted(self.job['id'],'codex',duplicate,allow_effects=True))


class StreamIdentity(unittest.TestCase):
    def test_both_cli_streams_keep_only_keyed_input_identity(self):
        from personal_agent.bounded_execution import cli_metadata
        calls=[]
        def digest(tool, values):
            calls.append((tool,values))
            return 'a'*64
        streams={
            'codex': [{'type':'item.completed','item':{'id':'one','type':'mcp_tool_call','server':'synthetic',
                'tool':'apply_change','arguments':INPUT,'status':'completed'}}],
            'claude-code': [{'type':'assistant','message':{'content':[{'type':'tool_use','id':'one','name':ACTION,'input':INPUT}]}},
                {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'one','content':'result'}]}}],
        }
        for engine, records in streams.items():
            with self.subTest(engine=engine):
                meta=cli_metadata(engine,'\n'.join(json.dumps(row) for row in records),digest)
                self.assertEqual(meta['connector_reads'][0]['input_digest'],'a'*64)
                self.assertIn((ACTION,INPUT),calls)
                self.assertNotIn('owner-account-a',json.dumps(meta))
                self.assertNotIn('unit-a',json.dumps(meta))
