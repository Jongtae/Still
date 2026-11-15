"""Model-free #717 authority and dispatch boundary tests."""
import json
from unittest import mock

from personal_agent import preparations as prep
from personal_agent.api_requests import ApiRequests, ApiError, save_slot
from personal_agent.browser_session import step_binding
from personal_agent.agent_runtime import ToolError
from personal_agent.settings_orchestrator import SettingsError, direct_request_setting
from personal_agent.self_set_writes import allowance, set_allowance
from personal_agent.context_observations import CONTINUATION_KEY_PREFIX
from test_scheduled_runs_ask_821 import _Runs
from test_owner_settings_conversation import _Case


class Settings(_Case):
    def setUp(self):
        super().setUp()
        save_slot(self.store,'notes',['notes.test'],'secret-notes-123456789',money=False)

    def draft(self, op='allow'):
        return self.settings.propose('owner','http','write_allowances',op,'slot:notes')

    def confirm(self, draft):
        return self.settings.confirm('owner','http',draft['draft_id'],draft['digest'])

    def test_exact_owner_confirmation_revision_and_revoke(self):
        self.assertFalse(allowance(self.service,'slot:notes')['enabled'])
        first, stale = self.draft(), self.draft()
        with self.assertRaises(SettingsError):
            self.settings.confirm('owner','http',first['draft_id'],'wrong')
        self.confirm(first)
        with self.assertRaises(SettingsError):self.confirm(stale)
        with self.assertRaises(SettingsError):self.confirm(first)
        self.confirm(self.draft('revoke'))
        self.assertFalse(allowance(self.service,'slot:notes')['enabled'])
        history=self.store.config('write_allowances_history')
        self.assertEqual([r['revision'] for r in history],[1,2])
        self.assertEqual(history[0]['authorized_by'],'owner')
        self.assertEqual(history[0]['connection'],'slot:notes')

    def test_ai_cannot_apply_enable_and_continuation_replay_cannot_draft(self):
        self.assertFalse(direct_request_setting('write_allowances','allow','slot:notes'))
        args={'category':'write_allowances','setting':'allow','value':'slot:notes'}
        job=self.store.job(self.store.enqueue('allow changes','typed',owner_typed=True))
        result=self.service.settings_tools(job)('settings_change',args)
        self.assertTrue(result['requires_owner_confirmation'])
        self.assertFalse(allowance(self.service,'slot:notes')['enabled'])
        for key,typed in [('replay',False),(CONTINUATION_KEY_PREFIX+'continued',True)]:
            job=self.store.job(self.store.enqueue('allow changes',key,owner_typed=typed))
            with self.assertRaises(SettingsError):self.service.settings_tools(job)('settings_change',args)
        job=self.store.job(self.store.enqueue('allow changes',prep.request_key('x',1000)))
        self.assertFalse(self.service.settings_tools(job)('settings_change',args)['applied'])


class Writes(_Runs):
    def setUp(self):
        super().setUp()
        save_slot(self.store,'notes',['notes.test'],'secret-notes-123456789',money=False)
        self.sent=[]

    def api(self,job):
        return ApiRequests(self.store,job['id'],approvals=self.service.browser_approvals_for(job),
            transport=lambda *a,**k:self.sent.append(a) or (200,{},b'{}'))

    def args(self,body='{}'):
        return {'slot':'notes','url':'https://notes.test/items','method':'POST','body':body,'effect':'mutate'}

    def started_self(self):
        return self.started(accepted_by=prep.ACCEPTED_DREAM)

    def test_unknown_waits_exact_approval_continues_once(self):
        row,job=self.started_self()
        args=self.args('{"note":"owner can review this value"}')
        with mock.patch.object(self.service.decision_judge,'connector_effect',return_value=None) as judge:
            with self.assertRaises(ApiError):self.api(job).call(args)
            self.assertEqual(self.sent,[])
            request=self.service._browser_request(job['id'])
            self.assertEqual(request['state'],'requested')
            self.assertEqual(judge.call_args.args[1],['body'])
            self.assertNotIn('owner can review this value',str(judge.call_args))
            self.assertIn(args['body'],request['label'])
            self.ended_waiting(job)
            self.assertFalse(self.service._decide_browser_step(job['id'],True,'other')['approved'])
            approved=self.service._decide_browser_step(job['id'],True,request['offer_digest'])
            self.assertTrue(approved['approved'])
            self.assertTrue(approved['resumed'])
            self.assertFalse(self.service._decide_browser_step(job['id'],True,request['offer_digest'])['approved'])
            with self.store.db() as db:db.execute("UPDATE jobs SET status='running' WHERE id=?",(job['id'],))
            self.api(job).call(args)
            self.assertEqual(len(self.sent),1)
            with self.assertRaises(ApiError):self.api(job).call(args)
            self.assertEqual(len(self.sent),1)

    def test_reversible_proceeds_unknown_and_irreversible_wait(self):
        _,job=self.started_self()
        for effect in ('reversible','irreversible',None):
            with mock.patch.object(self.service.decision_judge,'connector_effect',return_value=effect):
                if effect=='reversible':self.api(job).call(self.args('{"n":1}'))
                else:
                    with self.assertRaises(ApiError):self.api(job).call(self.args('{"n":2}'))
        self.assertEqual(len(self.sent),1)

    def test_api_connection_changed_during_judgment_dispatches_nothing(self):
        _,job=self.started_self()
        def changed(*args):
            save_slot(self.store,'notes',['notes.test'],'replacement-secret-123456789',money=False)
            return 'reversible'
        with mock.patch.object(self.service.decision_judge,'connector_effect',side_effect=changed):
            with self.assertRaises(ApiError):self.api(job).call(self.args())
        self.assertEqual(self.sent,[])

    def test_only_owner_button_on_the_offered_message_settles(self):
        from test_preparations import CHAT, GENERATION
        _,job=self.started_self()
        with mock.patch.object(self.service.decision_judge,'connector_effect',return_value='irreversible'):
            with self.assertRaises(ApiError):self.api(job).call(self.args())
        self.ended_waiting(job)
        note=self.store.notification_of(job['id'],'browser_approval_needed')
        self.store.update_notification(note['id'],'sent',message_id=77,delivered=True)
        data=f"p7w:{note['id']}:approve"
        self.service.ingest_callback({'id':'foreign','from':{'id':999},'data':data,
            'message':{'message_id':77,'chat':{'id':CHAT,'type':'private'}}},GENERATION)
        self.tap(data,78)
        self.assertEqual(self.service._browser_request(job['id'])['state'],'requested')
        self.assertEqual(self.sent,[])
        self.tap(data,77)
        self.assertEqual(self.service._browser_request(job['id'])['state'],'issued')
        self.assertEqual(self.store.job(job['id'])['status'],'queued')
        self.tap(data,77)
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tool_events WHERE job_id=? AND tool='action_continuation'",(job['id'],)).fetchone()[0],1)

    def test_allowed_once_notice_then_revocation_and_payment_floor(self):
        _,job=self.started_self()
        set_allowance(self.service,'slot:notes',True,0,'owner')
        with mock.patch.object(self.service.decision_judge,'connector_effect',return_value='irreversible'):
            self.api(job).call(self.args())
            with self.assertRaises(ApiError):self.api(job).call(self.args())
            set_allowance(self.service,'slot:notes',False,1,'owner')
            with self.assertRaises(ApiError):self.api(job).call(self.args('{"n":2}'))
        self.assertEqual(len(self.sent),1)
        with self.store.db() as db:
            rows=[json.loads(row['detail']) for row in db.execute("SELECT detail FROM tool_events WHERE tool='self_set_write' AND status='observed'")]
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['revision'],1)
        set_allowance(self.service,'slot:notes',True,2,'owner')
        args={**self.args('{"n":3}'),'effect':'payment'}
        with self.assertRaises((ApiError,ToolError)):self.api(job).call(args)
        self.assertEqual(len(self.sent),1)

    def test_owner_accepted_and_typed_skip_new_judgment(self):
        _,scheduled=self.started()
        typed=self.store.job(self.store.enqueue('write','typed',owner_typed=True))
        for job in (scheduled,typed):
            with self.store.db() as db:db.execute("UPDATE jobs SET status='running' WHERE id=?",(job['id'],))
            with mock.patch.object(self.service.decision_judge,'connector_effect',side_effect=AssertionError('must not judge')):
                self.api(job).call(self.args())
        self.assertEqual(len(self.sent),2)

    def test_expired_offer_or_changed_preparation_does_not_resume(self):
        _,job=self.started_self()
        with mock.patch.object(self.service.decision_judge,'connector_effect',return_value='irreversible'):
            with self.assertRaises(ApiError):self.api(job).call(self.args())
        self.ended_waiting(job)
        row=self.service._browser_request(job['id'])
        with mock.patch('personal_agent.browser_signin.time.time',return_value=row['requested_at']+100000):
            self.assertFalse(self.service._decide_browser_step(job['id'],True,row['offer_digest'])['approved'])
        self.assertEqual(self.sent,[])

    def test_connector_owner_mutate_still_needs_reversibility_and_exact_input(self):
        from personal_agent import owner_mcp, trust_record
        from test_connect_effects import ACTION, INPUT
        _,job=self.started_self()
        self.service.set_ai_connections({'enabled':True})
        owner_mcp.set_confirmed(self.store,'codex',['synthetic'])
        target=trust_record.connection('codex','owner-mcp','synthetic')
        trust_record.set_operation(self.store,target,'apply_change','mutate')
        args={'engine':'codex','tool_name':ACTION,'input':INPUT}
        with mock.patch.object(self.service.decision_judge,'connector_effect',return_value='irreversible') as judge:
            self.assertEqual(self.service.connector_permission(job,args)['behavior'],'deny')
            self.assertEqual(judge.call_args.args[1],sorted(INPUT))
            request=self.service._browser_request(job['id'])
            self.ended_waiting(job)
            self.assertTrue(self.service._decide_browser_step(job['id'],True,request['offer_digest'])['resumed'])
            with self.store.db() as db:db.execute("UPDATE jobs SET status='running' WHERE id=?",(job['id'],))
            self.assertEqual(self.service.connector_permission(job,args)['behavior'],'allow')
            self.assertEqual(self.service.connector_permission(job,args)['behavior'],'deny')

    def test_browser_press_and_nonpayment_submit_unknown_wait_before_driver(self):
        from personal_agent import browser_session as bs
        from test_browser_session import FakeDriver, ORIGIN
        _,job=self.started_self()
        driver=FakeDriver()
        browser=bs.BrowserSession(lambda:driver,work_id=job['id'],approvals=self.service.browser_approvals_for(job))
        browser.open({'url':ORIGIN+'/one-press','effect':'navigate'})
        with mock.patch.object(self.service.decision_judge,'connector_effect',return_value=None):
            with self.assertRaises(ToolError):browser.click({'target':'메모 저장','effect':'mutate'})
        self.assertEqual(driver.approved,[])
        record={'page':ORIGIN+'/form','action':ORIGIN+'/submit','method':'POST','dom':0,
                'state':'digest-of-exact-values','payment':False,'fieldNames':['note']}
        driver.release_submit=mock.Mock(return_value={})
        with mock.patch.object(self.service.decision_judge,'connector_effect',return_value='irreversible') as judge:
            with self.assertRaises(ToolError):browser._cancelled_submit(record,'submit')
            self.assertEqual(judge.call_args.args[1],['note'])
            self.assertNotIn(record['state'],str(judge.call_args))
        driver.release_submit.assert_not_called()
        self.assertEqual(self.service._browser_request(job['id'])['action'],'self_set_write')

    def test_approved_browser_submit_releases_once_without_payment_authority(self):
        from personal_agent import browser_session as bs
        from test_browser_session import FakeDriver, ORIGIN
        _,job=self.started_self()
        driver=FakeDriver()
        driver.release_submit=mock.Mock(return_value={})
        browser=bs.BrowserSession(lambda:driver,work_id=job['id'],approvals=self.service.browser_approvals_for(job))
        record={'page':ORIGIN+'/form','action':ORIGIN+'/submit','method':'POST','dom':0,
                'state':'exact-state','payment':False,'fieldNames':['note']}
        with mock.patch.object(self.service.decision_judge,'connector_effect',return_value='irreversible'):
            with self.assertRaises(ToolError):browser._cancelled_submit(record,'submit')
            driver.release_submit.assert_not_called()
            self.ended_waiting(job)
            offered=self.service._browser_request(job['id'])
            self.assertTrue(self.service._decide_browser_step(job['id'],True,offered['offer_digest'])['resumed'])
            with self.store.db() as db:db.execute("UPDATE jobs SET status='running' WHERE id=?",(job['id'],))
            with mock.patch.object(browser.approvals,'payment_autonomy',side_effect=AssertionError('not payment')):
                browser._cancelled_submit(record,'submit')
            with self.assertRaises(ToolError):browser._cancelled_submit(record,'submit')
        driver.release_submit.assert_called_once()
        with self.store.db() as db:
            self.assertEqual([row['status'] for row in db.execute("SELECT status FROM tool_events WHERE tool='self_set_write'")],['observed'])

    def test_browser_input_changed_during_judgment_does_not_use_old_approval(self):
        import copy
        from personal_agent import browser_session as bs
        from test_browser_session import FakeDriver, ORIGIN
        _,job=self.started_self()
        driver=FakeDriver()
        browser=bs.BrowserSession(lambda:driver,work_id=job['id'],approvals=self.service.browser_approvals_for(job))
        browser.open({'url':ORIGIN+'/one-press','effect':'navigate'})
        args={'target':'메모 저장','effect':'mutate'}
        with mock.patch.object(self.service.decision_judge,'connector_effect',return_value='irreversible'):
            with self.assertRaises(ToolError):browser.click(args)
            self.ended_waiting(job)
            offered=self.service._browser_request(job['id'])
            self.assertTrue(self.service._decide_browser_step(job['id'],True,offered['offer_digest'])['resumed'])
            with self.store.db() as db:db.execute("UPDATE jobs SET status='running' WHERE id=?",(job['id'],))
            original=browser._snapshot()
            changed=copy.deepcopy(original)
            changed['_states']={key:'changed during judgment' for key in original.get('_states',{})}
            changed['_states'][None]='changed during judgment'
            with mock.patch.object(browser,'_snapshot',side_effect=[original,changed]):
                with self.assertRaises(ToolError):browser.click(args)
        self.assertEqual(driver.approved,[])

    def test_code_run_non_money_boundary_and_allowance(self):
        from personal_agent.code_run import CodeRunner
        from personal_agent.api_requests import slots
        _,job=self.started_self()
        runner=CodeRunner(self.store,job['id'],approvals=self.service.browser_approvals_for(job))
        runner._owners={'notes.test':[('notes',slots(self.store)['notes'])]}
        runner._fields=[]
        request={'method':'POST','host':'notes.test','path':'/items','query':'','bodyText':'{"title":"private value"}'}
        with mock.patch.object(self.service.decision_judge,'connector_effect',return_value='irreversible'):
            self.assertFalse(runner._decide(request,{'notes.test':[]})[0])
            self.assertEqual(runner._asked,[{'method':'POST','host':'notes.test','path':'/items'}])
            set_allowance(self.service,'slot:notes',True,0,'owner')
            self.assertTrue(runner._decide(request,{'notes.test':[]})[0])
            self.assertFalse(runner._decide(request,{'notes.test':[]})[0])
        self.assertEqual(len(runner._claims),1)

    def test_allowance_notice_is_once_and_not_an_undo_claim(self):
        _,job=self.started_self()
        set_allowance(self.service,'slot:notes',True,0,'owner')
        binding=step_binding(job['id'],'api_request','https://notes.test/items','target',argument='body')
        ticket=self.service.self_set_write(job,binding,'slot:notes','POST','/items',effect='irreversible')
        with self.store.db() as db:
            before=db.execute("SELECT COUNT(*) FROM messages WHERE role='assistant'").fetchone()[0]
        self.service.self_set_write_finished(job,ticket)
        self.service.self_set_write_finished(job,ticket)
        with self.store.db() as db:
            after=db.execute("SELECT COUNT(*) FROM messages WHERE role='assistant'").fetchone()[0]
        self.assertEqual(after-before,1)

    def test_peer_send_waits_and_allowance_dispatches_only_once(self):
        import importlib.util
        if importlib.util.find_spec('a2a') is None:self.skipTest('optional A2A extra unavailable')
        import asyncio
        import httpx
        from cryptography.fernet import Fernet
        from google.protobuf.json_format import MessageToDict
        from a2a.server.routes import create_agent_card_routes
        from personal_agent.a2a_peers import A2APeers
        from test_a2a_compatibility import card, application
        _,job=self.started_self()
        self.service.record_turn_provenance(job['id'],owner_information={},context_messages=0)
        peers=A2APeers(self.service,secret_key=Fernet.generate_key())
        self.service.__dict__['a2a_peers']=peers
        remote_card=card('http://127.0.0.1/')
        peer=peers.register({'card':MessageToDict(remote_card)})['peer']['id']
        app=application(remote_card)
        app.router.routes.extend(create_agent_card_routes(remote_card))
        async def send():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as http:
                return await peers.send(peer,job['id'],'hello','exact-message',http=http)
        with self.assertRaises(ToolError):asyncio.run(send())
        self.assertEqual(peers.conversations()['turns'],[])
        self.assertEqual(self.service._browser_request(job['id'])['state'],'requested')
        set_allowance(self.service,'peer:'+peer,True,0,'owner')
        self.assertEqual(asyncio.run(send())['answer'],'answer: hello')
        with self.assertRaises(ToolError):asyncio.run(send())
        self.assertEqual(len(peers.conversations()['turns']),1)

    def test_worker_preserves_only_nonpayment_form_names_across_wait(self):
        from test_browser_webkit import WorkerGuardLogicTests
        harness=WorkerGuardLogicTests()
        _bw,worker=harness.worker()
        worker.self_set_writes=True
        raw={'id':'one','dom':0,'method':'post','action':'https://site.test/send',
             'page':'https://site.test','state':'note=private-value', 'payment':False,'fieldNames':['note']}
        first=worker.take_cancelled(raw)
        self.assertNotIn('private-value',json.dumps(first))
        self.assertEqual(first['fieldNames'],['note'])
        self.assertIs(first['payment'],False)
        self.assertEqual(worker.take_cancelled(),first)
        worker.held=None
        self.assertIsNone(worker.take_cancelled())

    def test_unavailable_judge_changed_input_and_other_work_do_not_spend_approval(self):
        _,job=self.started_self()
        first=step_binding(job['id'],'api_request','https://notes.test/items','target',argument='first')
        second=step_binding(job['id'],'api_request','https://notes.test/items','target',argument='second')
        with mock.patch.object(self.service.decision_judge,'connector_effect',side_effect=RuntimeError('unavailable')):
            with self.assertRaises(ToolError):self.service.self_set_write(job,first,'slot:notes','POST','/items')
        self.ended_waiting(job)
        offered=self.service._browser_request(job['id'])
        other=self.store.enqueue('another work','other',owner_typed=True)
        self.assertFalse(self.service._decide_browser_step(other,True,offered['offer_digest'])['approved'])
        self.assertTrue(self.service._decide_browser_step(job['id'],True,offered['offer_digest'])['approved'])
        with self.store.db() as db:db.execute("UPDATE jobs SET status='running' WHERE id=?",(job['id'],))
        with self.assertRaises(ToolError):
            self.service.self_set_write(job,second,'slot:notes','POST','/items',effect='irreversible')
        self.assertFalse(self.service._decide_browser_step(job['id'],True,offered['offer_digest'])['approved'])
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tool_events WHERE tool='self_set_write'").fetchone()[0],0)

    def test_native_submit_guard_intercepts_only_self_set_nonpayment_posts(self):
        import shutil
        import subprocess
        from test_browser_webkit import WorkerGuardLogicTests
        node=shutil.which('node')
        if not node:self.skipTest('Node unavailable for worker JavaScript boundary')
        bw,_worker=WorkerGuardLogicTests().worker()
        fragment=bw.PRELUDE[bw.PRELUDE.index('const armSubmitGuard ='):bw.PRELUDE.index('const takeCancelled =')]
        harness=r'''
let current, listener, held;
class HTMLFormElement {}
const state=()=>current, holdsPayment=form=>form.payment;
const effective=form=>({method:form.method}), vet=()=>{}, allows=()=>null;
const hold=()=>{held++};
const window={addEventListener:(type,fn)=>{if(type==='submit')listener=fn}};
''' + fragment + r'''
const run=(selfSetWrites,method,payment)=>{
 current={selfSetWrites};held=0;armSubmitGuard();
 const form=Object.assign(new HTMLFormElement(),{method,payment});let prevented=false;
 listener({type:'submit',target:form,preventDefault(){prevented=true},stopImmediatePropagation(){}});
 return {prevented,held};
};
console.log(JSON.stringify([run(false,'post',false),run(true,'get',false),run(true,'post',false),
                           run(false,'post',true),run(true,'post',true)]));
'''
        result=subprocess.run([node,'-e',harness],capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),[
            {'prevented':False,'held':0},{'prevented':False,'held':0},
            {'prevented':True,'held':1},{'prevented':True,'held':1},{'prevented':True,'held':1}])

    def test_reconnected_mcp_server_does_not_inherit_allowance(self):
        from personal_agent import owner_mcp
        from test_connect_effects import ACTION, INPUT
        _,job=self.started_self()
        self.service.set_ai_connections({'enabled':True})
        owner_mcp.set_confirmed(self.store,'codex',['synthetic'])
        connection='connector:codex|owner-mcp|synthetic'
        set_allowance(self.service,connection,True,0,'owner')
        owner_mcp.set_confirmed(self.store,'codex',[])
        owner_mcp.set_confirmed(self.store,'codex',['synthetic'])
        with mock.patch.object(self.service.decision_judge,'connector_effect',return_value='irreversible'):
            result=self.service.connector_permission(job,{'engine':'codex','tool_name':ACTION,'input':INPUT})
        self.assertEqual(result['behavior'],'deny')
        self.assertEqual(self.service._browser_request(job['id'])['state'],'requested')
