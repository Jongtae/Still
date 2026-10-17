"""Native prerequisite settlement without a callback; model-free real stores."""
from unittest.mock import patch
from test_conversation_connector_handoff import (HandoffTestCase as NativeCase,
    GMAIL_CONNECTOR_ID, GMAIL_SCOPES, OWNER, GENERATION, CHAT)
from test_contextual_capability_handoff import HandoffTestCase as FolderCase
from personal_agent.conversation_handoff import (ConnectorHandoff, ConversationHandoffError,
    LOCAL_FOLDER_READ, local_authority_handoff)
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore
from personal_agent.decision import FixtureDecisionEngine
from personal_agent.telegram_presence import WaitState, RECEIVED_REACTION


class NativeSettlement(NativeCase):
    def restart(self):
        previous=self.service
        self.store=QuickStore(self.temp.name)
        self.service=AgentService(self.store,previous.adapter,previous.telegram_transport,
                                  connector_registry=self.registry,gmail=self.gmail)
        self.service.use_decision_engine(FixtureDecisionEngine(choose=self.none_unsupported))
        self.handoff=ConnectorHandoff(self.store,self.registry,now=lambda:self.clock[0])
        self.service.connector_handoff=self.handoff

    def test_expiry_after_restart_closes_without_callback_and_reports_once(self):
        work=self.park()
        self.clock[0]+=901
        self.restart()
        before=len(self.sent)
        self.assertFalse(self.service.run_one())
        job=self.store.job(work)
        self.assertEqual(job['status'],'failed')
        self.assertIn('만료',job['error'])
        self.assertEqual(job['response'],job['error'])
        self.assertEqual(self.assistant_messages(work)[-1],job['error'])
        self.assertEqual(len(self.sent)-before,1)
        self.assertEqual(self.index(),{})
        self.assertEqual(self.service.settle_native_handoffs(),0)
        self.assertEqual(len(self.sent)-before,1)
        self.connect_gmail()
        with self.assertRaises(ConversationHandoffError):
            self.service.resume_connector_work(GMAIL_CONNECTOR_ID,OWNER,GMAIL_SCOPES)
        self.assertEqual(self.drain(),0)
        self.assertEqual(self.searches(),0)

    def test_expired_sibling_frees_index_capacity_without_touching_valid_wait(self):
        first=self.park()
        self.clock[0]+=800
        second=self.park('메일에서 계약서 관련 내용 찾아줘')
        self.clock[0]+=101
        with patch.object(self.handoff.pending,'max_records',2):
            third=self.park('메일에서 영수증 관련 내용 찾아줘')
        self.assertEqual(self.store.job(first)['status'],'failed')
        for work in (second,third):
            self.assertEqual(self.store.job(work)['status'],'awaiting_connection')
        self.connect_gmail()
        self.service.resume_connector_work(GMAIL_CONNECTOR_ID,OWNER,GMAIL_SCOPES)
        self.assertEqual(self.drain(),2)
        self.assertEqual(self.searches(),2)

    def test_changed_generation_closes_with_no_new_bot_message(self):
        work=self.park()
        cfg=self.store.config('telegram');cfg['generation']='replacement'
        self.store.put('telegram',cfg)
        before=len(self.sent)
        self.service.run_one()
        self.assertEqual(self.store.job(work)['status'],'failed')
        self.assertIn('교체',self.store.job(work)['error'])
        self.assertEqual(self.index(),{})
        # The error remains in original Work history; it must not cross the
        # old Work's generation to the replacement bot conversation.
        self.assertEqual(len(self.sent),before)

    def test_replaced_owner_cannot_receive_old_wait_content_or_revive_it(self):
        work=self.park()
        cfg=self.store.config('telegram');cfg['user_id']=999
        self.store.put('telegram',cfg)
        before=len(self.sent)
        self.service.run_one()
        self.assertEqual(self.store.job(work)['status'],'failed')
        self.assertEqual(self.index(),{})
        self.assertEqual(len(self.sent),before)

    def test_claimed_offer_keeps_recovery_after_its_expiry(self):
        work=self.park()
        self.connect_gmail()
        row=self.handoff.record(GMAIL_CONNECTOR_ID)
        self.handoff.pending.claim(row['resume_token'],OWNER,GMAIL_CONNECTOR_ID,GMAIL_SCOPES,row['handoff_id'])
        self.clock[0]+=901
        self.restart()
        self.assertEqual(self.service.settle_native_handoffs(),0)
        self.assertEqual(self.store.job(work)['status'],'awaiting_connection')
        self.service.resume_connector_work(GMAIL_CONNECTOR_ID,OWNER,GMAIL_SCOPES)
        self.assertEqual(self.drain(),1)
        self.assertEqual(self.store.job(work)['status'],'succeeded')
        self.assertEqual(self.searches(),1)

    def test_callback_scheduled_before_settlement_wins_work_compare_and_set(self):
        work=self.park()
        row=self.handoff.record(GMAIL_CONNECTOR_ID)
        self.assertTrue(self.service._schedule_resumed_work(work))
        result=self.handoff.settle(row,'expired_resume',self.service._abandon_parked_work)
        self.assertFalse(result)
        self.assertEqual(self.store.job(work)['status'],'queued')
        self.assertIsNone(self.store.job(work)['error'])

    def test_stale_sweep_cannot_settle_a_renewed_handoff(self):
        work=self.park()
        old=self.handoff.record(GMAIL_CONNECTOR_ID)
        self.handoff.park(OWNER,work,GMAIL_CONNECTOR_ID,generation=GENERATION)
        with patch.object(self.service,'_abandon_parked_work',side_effect=AssertionError('stale settlement')) as fail:
            self.assertFalse(self.handoff.settle(old,'expired_resume',fail))
        self.assertEqual(self.store.job(work)['status'],'awaiting_connection')
        self.connect_gmail()
        self.service.resume_connector_work(GMAIL_CONNECTOR_ID,OWNER,GMAIL_SCOPES)
        self.assertEqual(self.drain(),1)

    def test_cancelled_work_cleanup_does_not_overwrite_its_reason(self):
        work=self.park()
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='cancelled',error='owner stop' WHERE id=?",(work,))
        self.clock[0]+=901
        before=len(self.sent)
        self.service.run_one()
        self.assertEqual(self.store.job(work)['status'],'cancelled')
        self.assertEqual(self.store.job(work)['error'],'owner stop')
        self.assertEqual(self.index(),{})
        self.assertEqual(len(self.sent),before)

    def parked_reaction(self, work):
        reactions=[]
        original=self.service.telegram_transport
        def transport(url, body, headers=None, timeout=60):
            if url.endswith('/setMessageReaction'):
                reactions.append(body)
                return {'ok':True,'result':True}
            return original(url,body,headers=headers,timeout=timeout)
        self.service.telegram_transport=transport
        self.service.telegram_turns.record_source(work,CHAT,123)
        self.service.presence[work]=WaitState(reacted=True,reaction=RECEIVED_REACTION)
        return reactions

    def test_expiry_clears_the_parked_reaction_once_without_a_completion_claim(self):
        work=self.park()
        reactions=self.parked_reaction(work)
        self.clock[0]+=901
        self.service.run_one()
        self.assertEqual(self.store.job(work)['status'],'failed')
        self.assertEqual(len(reactions),1)
        self.assertEqual(reactions[0]['message_id'],123)
        self.assertEqual(reactions[0]['reaction'],[])
        self.service.run_one()
        self.assertEqual(len(reactions),1)

    def test_expiry_never_sends_a_reaction_through_a_replaced_bot(self):
        work=self.park()
        reactions=self.parked_reaction(work)
        cfg=self.store.config('telegram');cfg['generation']='replacement'
        self.store.put('telegram',cfg)
        self.service.run_one()
        self.assertEqual(self.store.job(work)['status'],'failed')
        self.assertEqual(reactions,[])

    def test_bad_clock_is_not_evidence_that_the_wait_expired(self):
        work=self.park()
        self.clock[0]=float('nan')
        self.assertEqual(self.service.settle_native_handoffs(),0)
        self.assertEqual(self.store.job(work)['status'],'awaiting_connection')


class LocalSettlement(FolderCase):
    def setUp(self):
        super().setUp()
        self.clock=[1000.0]
        self.service.local_handoff=local_authority_handoff(self.store,now=lambda:self.clock[0])

    def test_expired_selection_never_grants_a_folder_and_is_reported_once(self):
        work,_=self.park_read()
        request=self.pending()[0]
        path=self.folder('contracts')
        self.service.select_local_folder({'handoff_id':request['handoff_id'],'path':str(path)})
        self.clock[0]+=3601
        before=len(self.sent)
        self.assertFalse(self.service.run_one())
        self.assertEqual(self.job(work)['status'],'failed')
        self.assertIn('만료',self.job(work)['response'])
        self.assertEqual(self.pending(),[])
        self.assertEqual(self.roots(),[])
        self.assertNotIn(request['handoff_id'],self.service._local_selections)
        self.assertEqual(len(self.sent)-before,1)
        with self.assertRaises(ConversationHandoffError):
            self.service.approve_local_folder({'handoff_id':request['handoff_id']})
        self.service.run_one()
        self.assertEqual(len(self.sent)-before,1)
        self.assertEqual(self.roots(),[])

    def test_valid_sibling_and_claimed_local_wait_survive_expiry_sweep(self):
        first,_=self.park_read()
        row=self.service.local_handoff.record(LOCAL_FOLDER_READ)
        self.service.local_handoff.pending.claim(row['resume_token'],'telegram:505',LOCAL_FOLDER_READ,
                                                ('folder:read',),row['handoff_id'])
        self.clock[0]+=3500
        second,_=self.park_read()
        self.clock[0]+=101
        self.assertEqual(self.service.settle_native_handoffs(),0)
        for work in (first,second):
            self.assertEqual(self.job(work)['status'],'awaiting_connection')
        self.assertEqual(len(self.pending()),2)
        self.assertEqual(self.roots(),[])
