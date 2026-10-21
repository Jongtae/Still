"""Recover ready original native Works without replaying an OAuth callback."""
import json
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from test_conversation_connector_handoff import (HandoffTestCase as NativeCase,
    OWNER, GENERATION, GMAIL_CONNECTOR_ID, GMAIL_SCOPES)
from personal_agent.agent_runtime import WorkLedger, WORK_STOP_KEY
from personal_agent.connector_contract import ConnectorState, ConnectorContractError, PENDING_WORK_KEY
from personal_agent.conversation_handoff import (ConnectorHandoff, ConversationHandoffError, LOCAL_FOLDER_READ)
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore
from personal_agent.decision import FixtureDecisionEngine


class ReadyNativeRecovery(NativeCase):
    def restart(self):
        old=self.service
        self.store=QuickStore(self.temp.name)
        self.service=AgentService(self.store,old.adapter,old.telegram_transport,
                                  connector_registry=self.registry,gmail=self.gmail)
        self.service.use_decision_engine(FixtureDecisionEngine(judge=self.reached, choose=self.none_unsupported))
        self.handoff=ConnectorHandoff(self.store,self.registry,now=lambda:self.clock[0])
        self.service.connector_handoff=self.handoff

    def event(self, work, tool, status, detail):
        with self.store.db() as db:
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (work,tool,status,json.dumps(detail),time.time()))

    def test_connection_commit_then_restart_reaches_original_result_without_callback(self):
        work=self.park()
        original=self.store.job(work)['message']
        self.connect_gmail()  # Provider/registry commit succeeded; service callback resume was lost.
        self.restart()
        self.assertEqual(self.drain(),1)
        job=self.store.job(work)
        self.assertEqual(job['status'],'succeeded')
        self.assertEqual(job['message'],original)
        self.assertIn('예산 승인 안내',job['response'])
        self.assertEqual(self.searches(),1)
        self.assertEqual(self.index(),{})
        with self.assertRaises(ConversationHandoffError):
            self.service.resume_connector_work(GMAIL_CONNECTOR_ID,OWNER,GMAIL_SCOPES)
        self.assertFalse(self.service.run_one())
        self.assertEqual(self.searches(),1)

    def test_claim_before_lost_queue_recovers_after_restart_and_offer_expiry(self):
        work=self.park()
        self.connect_gmail()
        row=self.handoff.record(GMAIL_CONNECTOR_ID)
        self.handoff.pending.claim(row['resume_token'],OWNER,GMAIL_CONNECTOR_ID,GMAIL_SCOPES,row['handoff_id'])
        self.clock[0]+=901
        self.restart()
        self.assertEqual(self.drain(),1)
        self.assertEqual(self.store.job(work)['status'],'succeeded')
        self.assertEqual(self.searches(),1)
        self.assertEqual({r['state'] for r in self.store.config(PENDING_WORK_KEY).values()},{'completed'})

    def test_current_authority_is_required_even_for_claimed_recovery(self):
        work=self.park()
        self.connect_gmail()
        row=self.handoff.record(GMAIL_CONNECTOR_ID)
        self.handoff.pending.claim(row['resume_token'],OWNER,GMAIL_CONNECTOR_ID,GMAIL_SCOPES,row['handoff_id'])
        self.registry.transition(OWNER,GMAIL_CONNECTOR_ID,ConnectorState.DISCONNECTED)
        self.restart()
        self.assertFalse(self.service.run_one())
        self.assertEqual(self.store.job(work)['status'],'awaiting_connection')
        self.assertEqual(self.searches(),0)

    def test_a_different_owners_connection_cannot_resume_the_wait(self):
        work=self.park()
        self.connect_gmail('local-owner')
        self.assertFalse(self.service.run_one())
        self.assertEqual(self.store.job(work)['status'],'awaiting_connection')
        self.assertEqual(self.searches(),0)

    def test_recovery_and_callback_race_schedule_only_one_execution(self):
        work=self.park()
        self.connect_gmail()
        def callback():
            try:return self.service.resume_connector_work(GMAIL_CONNECTOR_ID,OWNER,GMAIL_SCOPES)
            except (ConnectorContractError,ConversationHandoffError) as exc:
                self.assertIn(exc.reason,('no_pending_work','replayed_resume'))
        with ThreadPoolExecutor(max_workers=2) as pool:
            recover=pool.submit(self.service.resume_ready_native_work)
            completion=pool.submit(callback)
            recover.result();completion.result()
        self.assertEqual(self.drain(),1)
        self.assertEqual(self.store.job(work)['status'],'succeeded')
        self.assertEqual(self.searches(),1)

    def test_unknown_effect_refuses_replay_and_retains_unknown_outcome(self):
        work=self.park()
        self.event(work,'api_dispatch','claiming',{'evidence':{'work_id':work,'effect':'mutate'}})
        self.connect_gmail()
        self.assertFalse(self.service.run_one())
        self.assertEqual(self.store.job(work)['status'],'unknown')
        self.assertIn('불확실',self.store.job(work)['response'])
        self.assertEqual(self.searches(),0)
        self.assertEqual(self.index(),{})

    def test_unreadable_effect_evidence_does_not_stall_an_independent_request(self):
        bad=self.park()
        other=self.park('메일에서 계약서 관련 내용 찾아줘')
        with self.store.db() as db:
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (bad,'api_dispatch','claiming','not-json',time.time()))
        self.connect_gmail()
        self.assertEqual(self.drain(),1)
        self.assertEqual(self.store.job(bad)['status'],'unknown')
        self.assertEqual(self.store.job(other)['status'],'succeeded')
        self.assertEqual(self.searches(),1)

    def test_prior_non_read_attempt_is_not_replayed_after_a_connection(self):
        work=self.park()
        self.event(work,'write_note','succeeded',{'effect':'mutate'})
        self.connect_gmail()
        self.assertFalse(self.service.run_one())
        self.assertEqual(self.store.job(work)['status'],'failed')
        self.assertIn('상태를 바꾸는',self.store.job(work)['response'])
        self.assertEqual(self.searches(),0)

    def test_stop_survives_restart_and_prevents_recovery(self):
        work=self.park()
        self.connect_gmail()
        self.store.put(WORK_STOP_KEY,[work])
        self.restart()
        self.assertFalse(self.service.run_one())
        self.assertEqual(self.store.job(work)['status'],'failed')
        self.assertIn('멈춤',self.store.job(work)['response'])
        self.assertEqual(self.searches(),0)

    def test_original_counters_are_not_reset_by_recovery(self):
        work=self.park()
        budget=WorkLedger(self.store,work,fresh=True)
        for _ in range(3):self.assertTrue(budget.spend(40))
        before=budget.used()
        budget.pause()
        self.connect_gmail()
        self.restart()
        self.assertEqual(self.drain(),1)
        after=WorkLedger(self.store,work,existing_only=True)
        # #173: the resumed request is the worker's; its one mail_search attempt
        # counts on top of the original attempts, never from zero.
        self.assertEqual(after.used(),before+1)
        self.assertEqual(self.searches(),1)
        self.assertEqual(self.store.job(work)['status'],'succeeded')

    def test_exhausted_active_time_cannot_reach_a_legacy_direct_read(self):
        work=self.park()
        now=[time.time()]
        budget=WorkLedger(self.store,work,fresh=True,wall=lambda:now[0])
        now[0]+=601
        budget.pause()
        self.connect_gmail()
        self.restart()
        with patch.object(self.service,'start_early_judgment',side_effect=AssertionError('expired request')):
            self.assertTrue(self.service.run_one())
        self.assertEqual(self.store.job(work)['status'],'failed')
        self.assertIn('시간 한도',self.store.job(work)['error'])
        self.assertEqual(self.searches(),0)

    def test_other_native_connections_never_approve_a_local_folder(self):
        work=self.store.enqueue('Read the selected material','local','web',None)
        self.service.park_for_local_authority(self.store.job(work),LOCAL_FOLDER_READ)
        self.connect_gmail('local-owner')
        self.assertFalse(self.service.run_one())
        self.assertEqual(self.store.job(work)['status'],'awaiting_connection')
        self.assertEqual(self.store.config('file_roots',[]),[])

    def test_all_terminal_outcomes_clean_claims_without_rewriting_results(self):
        self.connect_gmail()
        for status in ('partial','interrupted','unknown','blocked'):
            with self.subTest(status=status):
                work=self.store.enqueue('Finished original request',status,f'telegram:{GENERATION}',77)
                self.handoff.park(OWNER,work,GMAIL_CONNECTOR_ID,generation=GENERATION)
                row=self.handoff.record(GMAIL_CONNECTOR_ID,work_id=work)
                self.handoff.pending.claim(row['resume_token'],OWNER,GMAIL_CONNECTOR_ID,GMAIL_SCOPES,row['handoff_id'])
                with self.store.db() as db:
                    db.execute('UPDATE jobs SET status=?,response=?,error=? WHERE id=?',(status,'original result','original reason',work))
                self.service.run_one()
                job=self.store.job(work)
                self.assertEqual((job['status'],job['response'],job['error']),(status,'original result','original reason'))
                self.assertIsNone(self.handoff.record(GMAIL_CONNECTOR_ID,work_id=work))
        self.assertEqual({r['state'] for r in self.store.config(PENDING_WORK_KEY).values()},{'completed'})
        self.assertEqual(self.searches(),0)
