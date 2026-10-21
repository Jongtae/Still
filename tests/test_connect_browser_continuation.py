"""Login readiness and original Work continuation are one durable transaction."""
import json
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from personal_agent.quickstart_store import QuickStore
from personal_agent.quickstart_service import AgentService, BROWSER_OWNER_SIGNINS_KEY
from personal_agent.agent_runtime import WORK_STOP_KEY, WorkLedger
from personal_agent import preparations as prep
from test_flow_login import LoginHarness, ORIGIN, GENERATION, CHAT
from test_browser_session import Script, call
from test_agency_loop import finish, goal_engine


class BrowserSettlement(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=QuickStore(self.temp.name)
        self.service=AgentService(self.store)
        self.work=self.store.enqueue('Read the signed-in record','original','web',None)
        with self.store.db() as db:db.execute("UPDATE jobs SET status='partial' WHERE id=?",(self.work,))
        self.row={'state':'offered','nonce':'fixture-nonce','host':'fixture.test','explicit':True,
                  'deadline':time.time()+300,'work_id':self.work}
        self.service._put_browser_login(self.work,self.row)
        # No real profile or cookies. This is the already-observed local readiness seam.
        self.marks={'at':time.time(),'marks':[['keyed-fixture-mark',time.time()+300]],'ids':[]}
        self.patch=patch.object(self.service,'_login_cookie_marks',return_value=self.marks)
        self.patch.start();self.addCleanup(self.patch.stop)

    def settle(self):
        return self.service._settle_browser_login(self.work,self.row['nonce'],'resume',True,
                                                  signed=(('fixture.test',None),))

    def receipts(self):
        with self.store.db() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT detail FROM tool_events WHERE job_id=? AND tool='action_continuation'",(self.work,))]

    def event(self,tool,status,detail):
        with self.store.db() as db:
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (self.work,tool,status,json.dumps(detail),time.time()))

    def test_failure_at_final_login_write_rolls_back_queue_receipt_and_signin(self):
        original=self.service._put_browser_login
        def fail_after_write(*args,**kwargs):
            original(*args,**kwargs)
            raise RuntimeError('crash before commit')
        with patch.object(self.service,'_put_browser_login',side_effect=fail_after_write):
            with self.assertRaises(RuntimeError):self.settle()
        self.assertEqual(self.store.job(self.work)['status'],'partial')
        self.assertEqual(self.service._browser_login(self.work)['state'],'offered')
        self.assertEqual(self.receipts(),[])
        self.assertIsNone(self.store.config(BROWSER_OWNER_SIGNINS_KEY))
        self.assertEqual(self.settle(),'resumed')

    def test_postcommit_notification_loss_survives_restart_without_requeue(self):
        with patch.object(self.service,'_finish_login_notification',side_effect=RuntimeError('after commit')):
            with self.assertRaises(RuntimeError):self.settle()
        self.service=AgentService(self.store)
        self.assertEqual(self.store.job(self.work)['status'],'queued')
        self.assertEqual(self.service._browser_login(self.work)['state'],'resumed')
        self.assertEqual(len(self.receipts()),1)
        self.assertTrue(self.service.settings_work_continued(self.work))
        self.assertIn('same Work',self.service.action_continuation_note(self.store.job(self.work)))
        self.assertIsNone(self.settle())
        self.assertEqual(self.service.process_browser_logins(),[])
        self.assertEqual(len(self.receipts()),1)

    def test_duplicate_concurrent_completion_has_one_receipt(self):
        other=AgentService(self.store)
        with patch.object(other,'_login_cookie_marks',return_value=self.marks):
            with ThreadPoolExecutor(max_workers=2) as pool:
                first=pool.submit(self.settle)
                second=pool.submit(other._settle_browser_login,self.work,self.row['nonce'],'resume',True,
                                   signed=(('fixture.test',None),))
                outcomes=[first.result(),second.result()]
        self.assertEqual(outcomes.count('resumed'),1)
        self.assertEqual(len(self.receipts()),1)

    def test_stale_nonce_and_missing_readiness_do_not_queue(self):
        self.assertIsNone(self.service._settle_browser_login(self.work,'other','resume',True,signed=(('fixture.test',None),)))
        self.assertEqual(self.service._settle_browser_login(self.work,self.row['nonce'],'resume',True,signed=()),'not_logged_in')
        self.assertEqual(self.store.job(self.work)['status'],'partial')
        self.assertEqual(self.receipts(),[])

    def test_deadline_and_stop_prevent_late_resume(self):
        self.service._put_browser_login(self.work,{**self.row,'deadline':time.time()-1})
        self.assertEqual(self.settle(),'expired')
        self.service._put_browser_login(self.work,self.row)
        self.store.put(WORK_STOP_KEY,[self.work])
        self.assertEqual(self.settle(),'not_resumed')
        self.assertEqual(self.receipts(),[])

    def test_cancelled_or_replaced_owner_is_not_resumed(self):
        with self.store.db() as db:db.execute("UPDATE jobs SET status='cancelled' WHERE id=?",(self.work,))
        self.assertEqual(self.settle(),'not_resumed')
        self.service._put_browser_login(self.work,self.row)
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='partial',channel=?,chat_id=? WHERE id=?",(f'telegram:{GENERATION}',CHAT,self.work))
        self.store.put('telegram',{'enabled':True,'user_id':CHAT,'generation':'other'})
        self.assertEqual(self.settle(),'not_resumed')
        self.assertEqual(self.receipts(),[])

    def test_unknown_effect_refuses_resume_and_preserves_uncertainty(self):
        self.event('api_dispatch','claiming',{'evidence':{'work_id':self.work,'effect':'mutate'}})
        self.assertEqual(self.settle(),'not_resumed')
        self.assertEqual(self.receipts()[0]['reason'],'unknown-effect')
        self.assertIn('확인되지',self.store.job(self.work)['owner_cause'])
        self.assertTrue(self.service._work_has_unknown_effect(self.work))
        self.assertNotEqual(self.store.job(self.work)['status'],'queued')

    def test_unaccounted_effect_is_not_replayed(self):
        self.event('write_note','succeeded',{})
        self.assertEqual(self.settle(),'not_resumed')
        self.assertEqual(self.receipts()[0]['reason'],'unaccounted-action')

    def test_a_guard_refusal_does_not_withhold_the_resume(self):
        # #1484 (live, Work 51cd331b): Still refused a repeated click and an over-budget API call before they ran.
        click={'scope':'s','call_id':'c1','host_action':'browser_click','declared_effect':'mutate'}
        self.event('browser_click','running',click)
        self.event('browser_click','failed',{**click,'code':'repeat_path','retry':'permanent','effect':'none','ran':False})
        api={'scope':'s','call_id':'c2','attempt':1,'host_action':'api_request','declared_effect':'mutate'}
        self.event('api_request','running',api)
        self.event('api_request','failed',{**api,'code':'attempt_budget','retry':'budget','effect':'none','ran':False})
        self.assertEqual(self.settle(),'resumed')

    def test_a_timed_out_navigation_click_that_posted_nothing_does_not_withhold_the_resume(self):
        # #1496 (live, Work 3cc7b7e4): a menu click on a developer portal timed out before the owner approved.
        click={'scope':'s','call_id':'c1','host_action':'browser_click','declared_effect':'navigate'}
        self.event('browser_click','running',click)
        self.event('browser_click','failed',{**click,'code':'browser_timeout','retry':'permanent','effect':'none',
                                             'observed_step':{'posted':False,'approved':False}})
        self.assertEqual(self.settle(),'resumed')

    def test_a_timed_out_click_that_started_a_form_post_is_still_withheld(self):
        click={'scope':'s','call_id':'c1','host_action':'browser_click','declared_effect':'navigate'}
        self.event('browser_click','running',click)
        self.event('browser_click','failed',{**click,'code':'browser_timeout','retry':'permanent','effect':'none',
                                             'observed_step':{'posted':True,'approved':False}})
        self.assertEqual(self.settle(),'not_resumed')
        self.assertEqual(self.receipts()[0]['reason'],'unaccounted-action')

    def test_a_click_failure_without_the_not_run_mark_is_still_withheld(self):
        click={'scope':'s','call_id':'c1','host_action':'browser_click','declared_effect':'mutate'}
        self.event('browser_click','running',click)
        self.event('browser_click','failed',{**click,'code':'repeat_path','retry':'permanent','effect':'none'})
        self.assertEqual(self.settle(),'not_resumed')
        self.assertEqual(self.receipts()[0]['reason'],'unaccounted-action')

    def test_incomplete_cli_execution_is_not_inferred_read_only(self):
        self.event('subscription_engine','running',{})
        self.event('subscription_engine','succeeded',{'continuation_read_only':False,'continuation_accounted':False})
        self.assertEqual(self.settle(),'not_resumed')
        self.assertEqual(self.receipts()[0]['reason'],'unaccounted-action')

    def test_existing_shared_continuation_bound_is_not_reset(self):
        for i in range(8):self.event('settings_continuation','queued',{'resumed':True,'offer_digest':str(i)})
        self.assertEqual(self.settle(),'not_resumed')
        self.assertEqual(self.receipts()[0]['reason'],'continuation-budget')

    def test_legacy_resuming_settles_explicitly_and_never_invents_authentication(self):
        self.service._put_browser_login(self.work,{**self.row,'state':'resuming'})
        self.service=AgentService(self.store)
        self.assertEqual(self.service.process_browser_logins(),[self.work])
        self.assertEqual(self.service._browser_login(self.work)['cause'],'close_failed')
        self.assertEqual(self.store.job(self.work)['status'],'partial')
        self.assertEqual(self.receipts(),[])
        self.assertEqual(self.service.process_browser_logins(),[])

    def test_legacy_already_queued_work_is_not_lost_or_queued_again(self):
        self.service._put_browser_login(self.work,{**self.row,'state':'resuming'})
        with self.store.db() as db:db.execute("UPDATE jobs SET status='queued' WHERE id=?",(self.work,))
        self.service.process_browser_logins()
        self.assertEqual(self.store.job(self.work)['status'],'queued')
        self.assertEqual(self.receipts(),[])


    def test_unreadable_effect_evidence_is_unknown_without_replay(self):
        with self.store.db() as db:
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (self.work,'api_dispatch','claiming','not-json',time.time()))
        self.assertEqual(self.settle(),'not_resumed')
        self.assertEqual(self.store.job(self.work)['status'],'unknown')
        self.assertIn('불확실',self.store.job(self.work)['owner_cause'])

    def test_original_ledger_survives_login_and_exhaustion_blocks_worker(self):
        now=[time.time()]
        ledger=WorkLedger(self.store,self.work,fresh=True,wall=lambda:now[0])
        self.assertTrue(ledger.spend(40));before=ledger.used()
        now[0]+=601;ledger.pause()
        self.assertEqual(self.settle(),'resumed')
        self.service=AgentService(self.store)
        with patch.object(self.service,'start_early_judgment',side_effect=AssertionError('expired active time')):
            self.assertTrue(self.service.run_one())
        self.assertEqual(self.store.job(self.work)['status'],'failed')
        self.assertEqual(WorkLedger(self.store,self.work,existing_only=True).used(),before)

    def test_login_keeps_existing_scheduled_slot_and_refuses_expired_window(self):
        now=[time.time()];self.service.preparations.clock=lambda:now[0]
        row=self.service.preparations.create(kind='prepare',goal='Read the record',due_at=now[0],timezone='UTC',
            recurrence=None,channel='web',created_from='settings',state=prep.STATE_SCHEDULED,
            accepted_by=prep.ACCEPTED_OWNER_SETTINGS,window=(600,now[0]+3600,1))
        self.work=self.service.preparations.start(row,channel='web',chat_id=None,now=now[0])
        with self.store.db() as db:db.execute("UPDATE jobs SET status='partial' WHERE id=?",(self.work,))
        self.row={**self.row,'work_id':self.work}
        self.service._put_browser_login(self.work,self.row)
        self.assertEqual(self.settle(),'resumed')
        current=self.service.preparations.get(row['id'])
        self.assertEqual((current['last_run_job_id'],current['run_count'],current['window_end']),
                         (self.work,1,row['window_end']))
        with self.store.db() as db:db.execute("UPDATE jobs SET status='partial' WHERE id=?",(self.work,))
        self.service._put_browser_login(self.work,{**self.row,'nonce':'second'})
        self.row={**self.row,'nonce':'second'};now[0]=row['window_end']
        self.assertEqual(self.settle(),'not_resumed')
        self.assertNotEqual(self.store.job(self.work)['status'],'queued')
        self.assertEqual(self.service.preparations.get(row['id'])['run_count'],1)


class OriginalBrowserResult(LoginHarness):
    def test_login_callback_reaches_original_report_without_new_owner_utterance(self):
        work,_prompt,_buttons,notification=self.login_work()
        self.window().login()
        self.tap(f"p7l:{notification['id']}:done",notification['message_id'])
        self.settle(work)
        self.assertEqual(self.store.job(work)['status'],'queued')
        self.service=AgentService(self.store,self.service.adapter,self.transport,browser_profile=self.profile)
        summary='세탁세제 3L의 표시 가격은 12,900원입니다. 장바구니나 결제는 실행하지 않았습니다.'
        self.scripts=[Script({'tool_calls':[call('read','browser_open',url=ORIGIN+'/product',effect='read')]},
                             finish('done','read',summary=summary))]
        self.service.use_decision_engine(goal_engine(True))
        with patch.object(self.service.owner_acts,'read',side_effect=AssertionError('replay is not owner approval')):
            self.assertTrue(self.service.run_one())
        job=self.store.job(work)
        self.assertEqual(job['status'],'succeeded',job)
        self.assertIn('12,900원',job['response'])
        self.assertEqual(len(self.store.jobs()),1)
        self.service.deliver_one()
        self.assertTrue(any(method=='sendMessage' and '12,900원' in body.get('text','') and body['chat_id']==CHAT
                            for method,body in self.calls))
        self.assertFalse(self.service.run_one())
        self.assertTrue(self.store.turn_provenance(work))
        self.assertTrue(any(event['tool']=='browser_open' and event['status']=='succeeded'
                            for event in self.store.task_events(work)))

    def test_replaced_generation_receives_no_old_login_notice(self):
        work,_prompt,_buttons,_notification=self.login_work()
        self.store.put('telegram',{'enabled':True,'user_id':CHAT,'generation':'replacement'})
        before=len(self.calls)
        self.owner_closes(work)
        self.assertEqual(self.state(work),'not_resumed')
        self.assertNotEqual(self.store.job(work)['status'],'queued')
        self.assertFalse(any(method in ('sendMessage','editMessageText') for method,_ in self.calls[before:]))
