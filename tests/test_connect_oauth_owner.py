"""Native OAuth callbacks keep their initiating owner across independent waits."""
from urllib.parse import parse_qs, urlsplit
from cryptography.fernet import Fernet

from test_conversation_connector_handoff import (HandoffTestCase as NativeCase,
    OWNER, CHAT, GENERATION, MAIL_REQUEST, GMAIL_CONNECTOR_ID, GMAIL_READONLY_SCOPE)
from personal_agent.gmail import GmailError
from personal_agent.calendar_oauth import CalendarOAuth, CalendarOAuthError, EncryptedCalendarSecretStore
from personal_agent.calendar import CALENDAR_SPEC, CALENDAR_WRITE_SPEC
from personal_agent.google_drive_read import DRIVE_SPEC
from personal_agent.connector_contract import ConnectorState
from personal_agent.conversation_handoff import ConnectorHandoff
from personal_agent.quickstart_store import QuickStore
from personal_agent.quickstart_service import AgentService
from personal_agent.decision import FixtureDecisionEngine


def state_of(result):
    return parse_qs(urlsplit(result['authorization_url']).query)['state'][0]


class GmailCallbackOwner(NativeCase):
    def setUp(self):
        super().setUp()
        self.exchanges=[]
        def exchange(payload):
            self.exchanges.append(payload)
            return {'access_token':'fixture-access','refresh_token':'fixture-refresh',
                    'expires_in':7200,'scope':GMAIL_READONLY_SCOPE}
        self.service.gmail_token_exchange=exchange

    def park_owner(self, owner):
        if owner==OWNER:
            return self.park()
        work=self.store.enqueue(MAIL_REQUEST,f'web-{next(self.keys)}','web',None)
        self.assertTrue(self.service.run_one())
        self.assertEqual(self.store.job(work)['status'],'awaiting_connection')
        return work

    def complete(self, state, **extra):
        return self.service.complete_gmail_connection({'state':state,'code':'fixture',**extra})

    def assert_direction(self, first_owner, later_owner):
        original=self.park_owner(first_owner)
        state=state_of(self.service.begin_gmail_connection())
        later=self.park_owner(later_owner)
        result=self.complete(state)
        self.assertEqual(result['work_id'],original)
        self.assertEqual(result['resumed'],[{'work_id':original,'scheduled':True}])
        self.assertEqual(self.registry.status(first_owner,GMAIL_CONNECTOR_ID).state,ConnectorState.CONNECTED)
        self.assertEqual(self.registry.status(later_owner,GMAIL_CONNECTOR_ID).state,ConnectorState.DISCONNECTED)
        self.assertEqual(self.drain(),1)
        self.assertEqual(self.store.job(original)['status'],'succeeded')
        self.assertIn('예산 승인 안내',self.store.job(original)['response'])
        self.assertEqual(self.store.job(later)['status'],'awaiting_connection')
        self.assertEqual(self.searches(),1)
        self.assertEqual(len(self.exchanges),1)
        with self.assertRaises(GmailError):self.complete(state)
        self.assertEqual(self.drain(),0)
        self.assertEqual(len(self.exchanges),1)

    def test_telegram_oauth_survives_a_later_web_wait(self):
        self.assert_direction(OWNER,'local-owner')

    def test_web_oauth_survives_a_later_telegram_wait(self):
        self.assert_direction('local-owner',OWNER)

    def test_two_concurrent_flows_settle_only_their_own_waits(self):
        first=self.park_owner(OWNER)
        first_state=state_of(self.service.begin_gmail_connection())
        second=self.park_owner('local-owner')
        second_state=state_of(self.service.begin_gmail_connection())
        # Completion order differs from creation order and latest-wait order.
        self.complete(first_state)
        self.assertEqual(self.store.job(second)['status'],'awaiting_connection')
        self.complete(second_state)
        self.assertEqual(self.drain(),2)
        for work in (first,second):
            self.assertEqual(self.store.job(work)['status'],'succeeded')
        self.assertEqual(self.searches(),2)
        self.assertEqual(len(self.exchanges),2)

    def test_denial_is_bound_to_original_flow_not_latest_wait(self):
        first=self.park_owner(OWNER)
        state=state_of(self.service.begin_gmail_connection())
        second=self.park_owner('local-owner')
        with self.assertRaises(GmailError) as caught:
            self.complete(state,error='access_denied')
        self.assertEqual(caught.exception.reason,'authorization_denied')
        self.assertEqual(self.store.job(first)['status'],'failed')
        self.assertEqual(self.store.job(second)['status'],'awaiting_connection')
        self.assertEqual(self.exchanges,[])

    def test_expired_auth_state_keeps_failure_bound_to_original_owner(self):
        first=self.park_owner(OWNER)
        state=state_of(self.service.begin_gmail_connection())
        second=self.park_owner('local-owner')
        self.clock[0]+=601
        with self.assertRaises(GmailError) as caught:self.complete(state)
        self.assertEqual(caught.exception.reason,'state_expired')
        self.assertEqual(self.store.job(first)['status'],'failed')
        self.assertEqual(self.store.job(second)['status'],'awaiting_connection')
        self.assertEqual(self.exchanges,[])

    def test_forged_state_cannot_cancel_any_wait_or_exchange_tokens(self):
        first=self.park_owner(OWNER)
        state=state_of(self.service.begin_gmail_connection())
        second=self.park_owner('local-owner')
        with self.assertRaises(GmailError):self.complete(state+'x',error='access_denied')
        for work in (first,second):self.assertEqual(self.store.job(work)['status'],'awaiting_connection')
        self.assertEqual(self.exchanges,[])
        self.assertTrue(self.complete(state)['scheduled'])

    def test_original_binding_survives_service_restart(self):
        first=self.park_owner(OWNER)
        state=state_of(self.service.begin_gmail_connection())
        self.park_owner('local-owner')
        old=self.service
        self.store=QuickStore(self.temp.name)
        self.service=AgentService(self.store,old.adapter,old.telegram_transport,
                                  connector_registry=self.registry,gmail=self.gmail)
        self.service.use_decision_engine(FixtureDecisionEngine(judge=self.reached, choose=self.none_unsupported))
        self.service.gmail_token_exchange=old.gmail_token_exchange
        self.service.connector_handoff=ConnectorHandoff(self.store,self.registry,now=lambda:self.clock[0])
        self.assertEqual(self.complete(state)['work_id'],first)
        self.assertEqual(self.drain(),1)
        self.assertEqual(self.store.job(first)['status'],'succeeded')

    def test_removed_paired_owner_is_not_an_authentication_candidate(self):
        first=self.park_owner(OWNER)
        state=state_of(self.service.begin_gmail_connection())
        self.store.put('telegram',{'enabled':True,'user_id':999,'generation':'replacement'})
        with self.assertRaises(GmailError):self.complete(state)
        self.assertEqual(self.exchanges,[])
        self.assertEqual(self.registry.status(OWNER,GMAIL_CONNECTOR_ID).state,ConnectorState.DISCONNECTED)
        self.service.run_one()
        self.assertEqual(self.store.job(first)['status'],'failed')


class CalendarAndDriveCallbackOwner(NativeCase):
    def fixture(self, grant):
        spec={'read':CALENDAR_SPEC,'write':CALENDAR_WRITE_SPEC,'drive':DRIVE_SPEC}[grant]
        secrets=EncryptedCalendarSecretStore(self.store,Fernet.generate_key())
        oauth=CalendarOAuth(secrets,'fixture-client','https://connect.example.test/callback',
                            registry=self.registry,now=lambda:self.clock[0],grants=(grant,))
        exchanges=[]
        def exchange(payload):
            exchanges.append(payload)
            return {'access_token':'fixture-access','refresh_token':'fixture-refresh',
                    'expires_in':7200,'scope':' '.join(spec.required_scopes)}
        if grant=='drive':
            self.service.drive_oauth=oauth
            self.service.drive_read_token_exchange=exchange
            begin=self.service.begin_drive_connection
            complete=self.service.complete_drive_connection
        else:
            self.service.calendar_oauth=oauth
            self.service.calendar_token_exchange=exchange
            begin=lambda:self.service.begin_calendar_connection(grant)
            complete=self.service.complete_calendar_connection
        return spec,oauth,begin,complete,exchanges

    def wait(self, spec, owner):
        local=owner=='local-owner'
        work=self.store.enqueue('Original capability request',f'wait-{next(self.keys)}',
                                'web' if local else f'telegram:{GENERATION}',None if local else CHAT)
        with self.store.db() as db:db.execute("UPDATE jobs SET status='awaiting_connection' WHERE id=?",(work,))
        self.handoff.park(owner,work,spec.connector_id,generation='' if local else GENERATION)
        return work

    def check_grant(self, grant):
        spec,oauth,begin,complete,exchanges=self.fixture(grant)
        first=self.wait(spec,OWNER)
        state=state_of(begin())
        later=self.wait(spec,'local-owner')
        other_state=state_of(begin())
        result=complete({'state':state,'code':'fixture'})
        self.assertEqual(result['resumed'],[{'work_id':first,'scheduled':True}])
        self.assertEqual(self.store.job(later)['status'],'awaiting_connection')
        self.assertEqual(self.registry.status(OWNER,spec.connector_id).granted_scopes,spec.required_scopes)
        with self.assertRaises(CalendarOAuthError):complete({'state':state,'code':'replay'})
        self.assertEqual(len(exchanges),1)
        result=complete({'state':other_state,'code':'fixture'})
        self.assertEqual(result['work_id'],later)
        self.assertEqual(len(exchanges),2)

    def test_calendar_read_flow_keeps_owner_and_exact_grant(self):self.check_grant('read')
    def test_calendar_write_flow_keeps_owner_and_exact_grant(self):self.check_grant('write')
    def test_drive_flow_keeps_owner_and_exact_grant(self):self.check_grant('drive')

    def test_expired_calendar_state_resolves_owner_but_cannot_complete(self):
        spec,oauth,begin,complete,exchanges=self.fixture('write')
        self.wait(spec,OWNER)
        state=state_of(begin())
        self.wait(spec,'local-owner')
        self.clock[0]+=601
        self.assertEqual(oauth.callback_owner(state,['local-owner',OWNER]),OWNER)
        with self.assertRaises(CalendarOAuthError) as caught:complete({'state':state,'code':'fixture'})
        self.assertEqual(caught.exception.reason,'state_expired')
        self.assertEqual(exchanges,[])
        self.assertEqual(self.registry.status(OWNER,spec.connector_id).state,ConnectorState.DISCONNECTED)

    def test_relabelled_calendar_grant_cannot_find_an_owner_or_exchange(self):
        spec,oauth,begin,complete,exchanges=self.fixture('write')
        self.wait(spec,OWNER)
        state=state_of(begin())
        self.wait(spec,'local-owner')
        forged='read.'+state.split('.',1)[1]
        with self.assertRaises(CalendarOAuthError):complete({'state':forged,'code':'fixture'})
        self.assertEqual(exchanges,[])
        self.assertTrue(complete({'state':state,'code':'fixture'})['scheduled'])
