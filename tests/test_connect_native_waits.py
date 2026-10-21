"""CONNECT-01: independent original Works across native prerequisites.

Real store, OAuth callback, queue and worker; fixture provider/model transports.
No live account or model calls.
"""
import hashlib
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from test_conversation_connector_handoff import (HandoffTestCase as ConnectorCase,
    GMAIL_CONNECTOR_ID, GMAIL_READONLY_SCOPE, GMAIL_SCOPES, OWNER, OTHER_OWNER,
    OTHER_CHAT, GENERATION, MAIL_REQUEST, CALENDAR_REQUEST)
from test_contextual_capability_handoff import HandoffTestCase as FolderCase
from personal_agent.connector_contract import ConnectorContractError
from personal_agent.conversation_handoff import (ConnectorHandoff, ConversationHandoffError,
    CONVERSATION_RESUME_KEY)
from personal_agent.gmail import GmailError
from personal_agent.quickstart_store import QuickStore
from personal_agent.quickstart_service import AgentService
from personal_agent.decision import FixtureDecisionEngine


class NativeIndependentWaits(ConnectorCase):
    def restart(self):
        old=self.service
        self.store=QuickStore(self.temp.name)
        self.service=AgentService(self.store,old.adapter,old.telegram_transport,
                                  connector_registry=self.registry,gmail=self.gmail)
        self.service.use_decision_engine(FixtureDecisionEngine(judge=self.reached, choose=self.none_unsupported))
        self.service.connector_handoff=ConnectorHandoff(self.store,self.registry,now=lambda:self.clock[0])
        self.handoff=self.service.connector_handoff

    def callback(self):
        self.gmail.begin_oauth(OWNER)
        pending=self.encrypted.secret('gmail_oauth_pending:'+hashlib.sha256(OWNER.encode()).hexdigest())
        self.service.gmail_token_exchange=lambda request: {
            'access_token':'fixture-access','refresh_token':'fixture-refresh',
            'expires_in':7200,'scope':GMAIL_READONLY_SCOPE}
        self.last_callback={'state':pending['state'],'code':'fixture'}
        return self.service.complete_gmail_connection(self.last_callback)

    def test_callback_after_restart_runs_both_original_requests_once(self):
        first=self.park()
        second=self.park('메일에서 계약서 관련 내용 찾아줘')
        self.restart()
        result=self.callback()
        self.assertEqual({r['work_id'] for r in result['resumed']},{first,second})
        self.assertTrue(all(r['scheduled'] for r in result['resumed']))
        self.assertEqual(self.drain(),2)
        for work in (first,second):
            self.assertEqual(self.store.job(work)['status'],'succeeded')
            self.assertIn('예산 승인 안내',self.store.job(work)['response'])
        self.assertEqual(self.store.job(first)['message'],MAIL_REQUEST)
        self.assertIn('계약서',self.store.job(second)['message'])
        self.assertEqual(self.searches(),2)
        # A duplicate browser callback cannot replay either original request.
        with self.assertRaises(GmailError):
            self.service.complete_gmail_connection(self.last_callback)
        self.assertEqual(self.drain(),0)
        self.assertEqual(self.searches(),2)

    def test_concurrent_completion_attempts_do_not_duplicate_either_work(self):
        first=self.park()
        second=self.park('메일에서 계약서 관련 내용 찾아줘')
        self.connect_gmail()
        def resume():
            try:
                return self.service.resume_connector_work(GMAIL_CONNECTOR_ID,OWNER,GMAIL_SCOPES)
            except (ConnectorContractError,ConversationHandoffError) as exc:
                self.assertIn(exc.reason,('no_pending_work','replayed_resume'))
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _:resume(),range(2)))
        self.assertEqual(self.drain(),2)
        self.assertEqual(self.searches(),2)
        self.assertEqual(self.index(),{})
        for work in (first,second):
            self.assertEqual(self.store.job(work)['status'],'succeeded')

    def test_one_expired_wait_does_not_starve_a_later_valid_request(self):
        expired=self.park()
        self.clock[0]+=800
        current=self.park('메일에서 계약서 관련 내용 찾아줘')
        self.clock[0]+=101
        result=self.callback()
        self.assertEqual(result['resume_refusals'],[{'work_id':expired,'reason':'expired_resume'}])
        self.assertEqual(self.store.job(expired)['status'],'failed')
        self.assertEqual(self.drain(),1)
        self.assertEqual(self.store.job(current)['status'],'succeeded')

    def test_cancel_one_work_preserves_same_connector_sibling_and_other_connector(self):
        first=self.park()
        second=self.park('메일에서 계약서 관련 내용 찾아줘')
        other=self.park(CALENDAR_REQUEST)
        changed,_=self.service.cancel_focused_work(self.store.job(first),OWNER)
        self.assertTrue(changed)
        self.callback()
        self.assertEqual(self.drain(),1)
        self.assertEqual(self.store.job(first)['status'],'cancelled')
        self.assertEqual(self.store.job(second)['status'],'succeeded')
        self.assertEqual(self.store.job(other)['status'],'awaiting_connection')

    def test_refused_connection_fails_all_its_owner_waits_only(self):
        first=self.park()
        second=self.park('메일에서 계약서 관련 내용 찾아줘')
        other=self.park(chat_id=OTHER_CHAT)
        self.service.deny_connector_work(GMAIL_CONNECTOR_ID,OWNER)
        for work in (first,second):
            self.assertEqual(self.store.job(work)['status'],'failed')
        self.assertEqual(self.store.job(other)['status'],'awaiting_connection')
        self.assertEqual(len(self.handoff.records(GMAIL_CONNECTOR_ID,owner_id=OTHER_OWNER)),1)
        self.assertEqual(self.searches(),0)

    def test_current_owner_callback_cannot_resume_other_owner_or_generation(self):
        stale=self.park()
        row=self.handoff.record(GMAIL_CONNECTOR_ID,work_id=stale)
        rows=self.index();rows[row['handoff_id']]={**row,'generation':'obsolete'}
        self.store.secret(CONVERSATION_RESUME_KEY,rows)
        other=self.park(chat_id=OTHER_CHAT)
        current=self.park('메일에서 계약서 관련 내용 찾아줘')
        result=self.callback()
        self.assertEqual(result['resume_refusals'],[])  # The worker already settled dead identities.
        self.assertEqual(self.drain(),1)
        self.assertEqual(self.store.job(current)['status'],'succeeded')
        for work in (other,stale):
            self.assertEqual(self.store.job(work)['status'],'failed')

    def test_legacy_index_row_is_preserved_alongside_new_wait(self):
        first=self.park()
        row=self.handoff.record(GMAIL_CONNECTOR_ID)
        self.store.secret(CONVERSATION_RESUME_KEY,{GMAIL_CONNECTOR_ID:row})
        second=self.park('메일에서 계약서 관련 내용 찾아줘')
        self.callback()
        self.assertEqual(self.drain(),2)
        self.assertEqual(self.store.job(first)['status'],'succeeded')
        self.assertEqual(self.store.job(second)['status'],'succeeded')
        self.assertEqual(self.index(),{})

    def test_restart_between_first_schedule_and_complete_does_not_duplicate(self):
        first=self.park()
        second=self.park('메일에서 계약서 관련 내용 찾아줘')
        self.connect_gmail()
        with patch.object(self.handoff.pending,'complete',side_effect=RuntimeError('crash')):
            with self.assertRaises(RuntimeError):
                self.service.resume_connector_work(GMAIL_CONNECTOR_ID,OWNER,GMAIL_SCOPES)
        # The worker recovers the second ready wait without another callback.
        self.assertEqual(self.drain(),2)
        self.restart()
        with self.assertRaises(ConversationHandoffError):
            self.service.resume_connector_work(GMAIL_CONNECTOR_ID,OWNER,GMAIL_SCOPES)
        self.assertIsNone(self.handoff.record(GMAIL_CONNECTOR_ID,work_id=first))
        self.assertEqual(self.drain(),0)
        self.assertEqual(self.searches(),2)

    def test_except_work_preserves_its_handle_and_index_limit_preserves_waits(self):
        first=self.park()
        second=self.park('메일에서 계약서 관련 내용 찾아줘')
        with patch.object(self.handoff.pending,'max_records',2):
            with self.assertRaises(ConnectorContractError) as caught:
                self.handoff.park(OWNER,'another-work',GMAIL_CONNECTOR_ID,generation=GENERATION)
        self.assertEqual(caught.exception.reason,'pending_limit')
        self.service.supersede_pending_handoffs(except_work_id=first,owner_id=OWNER)
        self.assertIsNotNone(self.handoff.record(GMAIL_CONNECTOR_ID,work_id=first))
        self.assertEqual(self.store.job(second)['status'],'cancelled')
        self.callback()
        self.assertEqual(self.drain(),1)


class LocalIndependentWaits(FolderCase):
    def test_approval_resumes_only_its_selected_request_to_result(self):
        first,_=self.park_read()
        first_request=self.pending()[0]
        second,_=self.park_read()
        self.assertEqual(len(self.pending()),2)
        self.assertEqual(self.job(first)['status'],'awaiting_connection')
        path=self.folder('contracts',{'계약서.txt':'갱신일은 10월 1일'})
        self.service.select_local_folder({'handoff_id':first_request['handoff_id'],'path':str(path)})
        self.service.approve_local_folder({'handoff_id':first_request['handoff_id']})
        self.assertEqual(self.job(first)['status'],'queued')
        self.assertEqual(self.job(second)['status'],'awaiting_connection')
        self.plan=[('find_files',{'query':'계약서'})]
        self.claim_completion()
        self.assertTrue(self.service.run_one())
        self.assertEqual(self.job(first)['status'],'succeeded')
        self.assertIn('10월 1일',self.job(first)['response'])
        self.assertEqual(len(self.pending()),1)
        self.assertEqual(self.job(second)['status'],'awaiting_connection')
        with self.assertRaises(ConversationHandoffError):
            self.service.approve_local_folder({'handoff_id':first_request['handoff_id']})

    def test_denial_and_stale_completed_selection_leave_siblings_untouched(self):
        first,_=self.park_read()
        req=self.pending()[0]
        second,_=self.park_read()
        self.service.deny_local_folder({'handoff_id':req['handoff_id']})
        self.assertEqual(self.job(first)['status'],'failed')
        self.assertEqual(self.job(second)['status'],'awaiting_connection')
        request=self.pending()[0]
        path=self.folder('contracts')
        self.service.select_local_folder({'handoff_id':request['handoff_id'],'path':str(path)})
        third,_=self.park_read()
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='cancelled' WHERE id=?",(second,))
        with self.assertRaises(ConversationHandoffError):
            self.service.approve_local_folder({'handoff_id':request['handoff_id']})
        self.assertEqual(self.roots(),[])
        self.assertEqual(self.job(third)['status'],'awaiting_connection')
        self.assertEqual(len(self.pending()),1)
