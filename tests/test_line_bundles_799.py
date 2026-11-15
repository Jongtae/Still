"""L2 fixture evidence: real store, run/settle and builtin delivery; only the wires are injected."""
import json
import sqlite3
from datetime import datetime
from unittest.mock import patch

from personal_agent import preparations as prep, owner_model as om, entrusted_lines as el
from personal_agent.agent_runtime import MEMORY_OWNER
from personal_agent.owner_forget import OwnerForget
from personal_agent.messages import LANGUAGES, msg
from personal_agent.preparations_service import BUNDLE_CHARS, BUNDLE_SETTLE_SECONDS
from test_lines_from_conversation_799 import _LinesCase
from test_preparations import SEOUL


class Bundles(_LinesCase):
    def setUp(self):
        super().setUp()
        from personal_agent.current_context import TIMEZONE_CONFIG_KEY
        self.store.put(TIMEZONE_CONFIG_KEY, 'Asia/Seoul')

    def at(self, hour, minute=0):
        self.now=datetime.fromtimestamp(self.now,SEOUL).replace(hour=hour,minute=minute,second=0,microsecond=0).timestamp()

    def line(self, task='Responsibility A'):
        self.a=self.owner(task+' one',self.now-86400)
        self.b=self.owner(task+' two',self.now-60)
        brief=self.stand(task=task)
        self.deliver(self.announce(brief))
        return brief,self.work_rows(brief)[0]

    def report(self, row, text='Result lead', *, deliver=True):
        # Schedule fixtures set the next due time, then exercise real start, worker, settle.
        with self.store.db() as db:
            db.execute('UPDATE preparations SET due_at=? WHERE id!=? AND state=?',(self.now+86400,row['id'],prep.STATE_SCHEDULED))
            db.execute('UPDATE preparations SET due_at=?,state=? WHERE id=?',(self.now,prep.STATE_SCHEDULED,row['id']))
        self.script=[{'content':text}]*4
        self.service.run_due_preparation()
        work=self.service.preparations.get(row['id'])['last_run_job_id']
        self.assertIsNotNone(work)
        self.assertTrue(self.service.run_one())
        self.assertEqual(self.store.job(work)['response'],text)
        if deliver:self.service.deliver_one()
        self.service.run_due_preparation()
        return work

    def bundles(self):
        with self.store.db() as db:
            return [dict(r) for r in db.execute("SELECT * FROM jobs WHERE model='line-bundle' ORDER BY created")]

    def receipts(self):
        with self.store.db() as db:
            return [(r['job_id'],json.loads(r['detail'])['bundle_id']) for r in db.execute("SELECT * FROM tool_events WHERE tool='line_bundled'")]

    def flush(self):
        calls=len(self.model_calls)
        bundle=self.helper.flush_line_bundles(self.now)
        self.assertEqual(len(self.model_calls),calls,'bundling calls no AI')
        self.service.deliver_one()
        return bundle

    def assert_kept_locally(self, brief, work, text):
        job=self.store.job(work)
        self.assertEqual(job['delivery'],'cancelled')
        self.assertEqual(self.store.last_reply(work)['content'],text)
        outcome=next(item for item in self.helper.line_outcomes(brief['line_id']) if item['work_id']==work)
        self.assertEqual(outcome['outcome'],el.OUTCOME_KEPT)
        self.assertNotIn(work,[part for part,_ in self.receipts()])

    def start_held_notice_run(self, destination='telegram'):
        self.at(10);_,row=self.line('Notice responsibility '+str(self.counter));self.now+=86400;self.at(8)
        with self.store.db() as db:
            db.execute('UPDATE preparations SET due_at=? WHERE id!=?',(self.now+86400,row['id']))
            db.execute('UPDATE preparations SET due_at=? WHERE id=?',(self.now,row['id']))
            db.execute("UPDATE tool_events SET detail=json_set(detail,'$.channel',?) "
                       "WHERE tool='line_held' AND json_extract(detail,'$.preparation_id')=?",
                       (destination,row['id']))
        self.service.run_due_preparation()
        row=self.service.preparations.get(row['id'])
        job=self.store.job(row['last_run_job_id'])
        self.assertEqual((row['channel'],job['channel'],job['chat_id']),('web','web',None))
        self.telegram.clear()
        return row,job

    def finish_held_notice_run(self, row, job):
        text='Completed after owner action'
        receipts=self.receipts()
        self.script=[{'content':text}]*4
        self.assertTrue(self.service.run_one())
        self.assertEqual(self.store.job(job['id'])['response'],text)
        self.service.deliver_one()
        self.service.run_due_preparation()
        self.assertEqual(self.service.preparations.get(row['id'])['state'],prep.STATE_SCHEDULED)
        self.assertEqual(self.sends(),[],'completed report stays held')
        self.at(9);bundle=self.flush()
        self.assertEqual(self.receipts(),[*receipts,(job['id'],bundle)])
        self.assertIn(text,self.store.job(bundle)['response'])

    def test_held_connection_wait_sent_once_then_restored_run_is_bundled(self):
        row,job=self.start_held_notice_run()
        with patch.object(self.service,'_park_for_connector',return_value='private connection guidance'):
            self.assertTrue(self.service.park_for_connector_read(job,'fixture-connector'))
            self.assertTrue(self.service.park_for_connector_read(job,'fixture-connector'))
        self.service=self.make_service();self.helper=self.service.preparations_service
        self.assertTrue(self.service.deliver_notification())
        self.assertFalse(self.service.deliver_notification())
        self.assertEqual([s['text'] for s in self.sends()],
                         [msg('preparations.notice_waiting',title=prep.owner_title(row))])
        self.assertEqual(self.store.job(job['id'])['status'],'awaiting_connection')
        self.assertEqual(self.store.job(job['id'])['delivery'],'none')
        self.telegram.clear()
        self.assertTrue(self.service._schedule_resumed_work(job['id']))
        self.finish_held_notice_run(row,job)

    def test_held_connection_wait_uses_recorded_surface_not_origin(self):
        self.store.put('mattermost',{'enabled':True,'owner_user_id':'owner','channel_id':'dm','generation':'mm-1'})
        row,job=self.start_held_notice_run('mattermost')
        with patch.object(self.service,'_park_for_connector',return_value='private connection guidance'):
            self.assertTrue(self.service.park_for_connector_read(job,'fixture-connector'))
        self.assertIsNone(self.store.next_notification('telegram'))
        surface=next(s for s in self.service.surfaces if s.name=='mattermost')
        with patch.object(surface,'_post',return_value='posted-799') as wire:
            self.assertTrue(self.service.deliver_notification())
            self.assertFalse(self.service.deliver_notification())
        self.assertEqual(wire.call_count,1)
        self.assertIn(msg('preparations.notice_waiting',title=prep.owner_title(row)),str(wire.call_args))

    def test_held_irreversible_write_approval_then_report_is_bundled(self):
        from personal_agent.api_requests import ApiRequests, ApiError, save_slot
        save_slot(self.store,'notes',['notes.test'],'fixture-secret-123456789',money=False)
        row,job=self.start_held_notice_run()
        with self.store.db() as db:db.execute("UPDATE jobs SET status='running' WHERE id=?",(job['id'],))
        sent=[]
        api=ApiRequests(self.store,job['id'],approvals=self.service.browser_approvals_for(job),
                        transport=lambda *a,**k:sent.append(a) or (200,{},b'{}'))
        args={'slot':'notes','url':'https://notes.test/items','method':'POST','body':'{}','effect':'mutate'}
        with patch.object(self.service.decision_judge,'connector_effect',return_value='irreversible'):
            with self.assertRaises(ApiError) as raised:api.call(args)
            self.assertEqual(raised.exception.code,'approval_required')
            self.assertEqual(sent,[])
            request=self.service._browser_request(job['id'])
            with self.store.db() as db:
                db.execute("UPDATE jobs SET status='partial',delivery='none' WHERE id=?",(job['id'],))
            self.assertTrue(self.service.deliver_notification())
            self.assertFalse(self.service.deliver_notification())
            self.assertIn('notes.test',self.sends()[0]['text'])
            decision=self.service._decide_browser_step(job['id'],True,request['offer_digest'])
            self.assertTrue(decision['resumed'],(decision,self.store.job(job['id']),self.service._browser_request(job['id'])))
            api.call(args)
        self.assertEqual(len(sent),1)
        self.telegram.clear()
        self.finish_held_notice_run(row,job)

    def test_held_browser_write_approval_then_report_is_bundled(self):
        from personal_agent import browser_session as bs
        from personal_agent.agent_runtime import ToolError
        from test_browser_session import FakeDriver, ORIGIN
        row,job=self.start_held_notice_run()
        with self.store.db() as db:db.execute("UPDATE jobs SET status='running' WHERE id=?",(job['id'],))
        driver=FakeDriver(pages={'/edit':'<form method="post" action="/save"><input aria-label="Note" name="note"></form>'})
        browser=bs.BrowserSession(lambda:driver,work_id=job['id'],approvals=self.service.browser_approvals_for(job))
        self.addCleanup(browser.close)
        browser.open({'url':ORIGIN+'/edit','effect':'navigate'})
        args={'target':'Note','text':'Reviewed value','effect':'mutate'}
        with patch.object(self.service.decision_judge,'connector_effect',return_value='irreversible'):
            with self.assertRaises(ToolError) as raised:browser.type(args)
            self.assertEqual(raised.exception.code,'approval_required')
            request=self.service._browser_request(job['id'])
            with self.store.db() as db:
                db.execute("UPDATE jobs SET status='partial',delivery='none' WHERE id=?",(job['id'],))
            self.assertTrue(self.service.deliver_notification())
            self.assertFalse(self.service.deliver_notification())
            self.assertIn('Note',self.sends()[0]['text'])
            decision=self.service._decide_browser_step(job['id'],True,request['offer_digest'])
            self.assertTrue(decision['resumed'],(decision,self.store.job(job['id']),self.service._browser_request(job['id'])))
            browser.type(args)
        self.telegram.clear()
        self.finish_held_notice_run(row,job)

    def test_held_expiry_status_is_sent_once(self):
        row,job=self.start_held_notice_run()
        with patch.object(self.service,'_park_for_connector',return_value='private connection guidance'):
            self.assertTrue(self.service.park_for_connector_read(job,'fixture-connector'))
        with self.store.db() as db:
            db.execute("UPDATE preparations SET recurrence='window',window_end=?,max_runs=3 WHERE id=?",(self.now,row['id']))
        self.service.run_due_preparation()
        while self.service.deliver_notification():pass
        self.assertEqual([s['text'] for s in self.sends()],
                         [msg('preparations.notice_elapsed',title=prep.owner_title(row))])

    def test_held_expiry_before_start_is_sent_once(self):
        self.at(10);_,row=self.line();self.now+=86400;self.at(8)
        with self.store.db() as db:
            db.execute('UPDATE preparations SET due_at=? WHERE id!=?',(self.now+86400,row['id']))
            db.execute("UPDATE preparations SET due_at=?,recurrence='window',every_seconds=600,"
                       "window_end=?,until_at=?,max_runs=3 WHERE id=?",(self.now-1200,self.now,self.now,row['id']))
        self.telegram.clear()
        self.service.run_due_preparation()
        self.assertIsNone(self.service.preparations.get(row['id'])['last_run_job_id'])
        self.assertTrue(self.service.deliver_notification())
        self.assertFalse(self.service.deliver_notification())
        self.assertEqual([s['text'] for s in self.sends()],
                         [msg('preparations.notice_elapsed',title=prep.owner_title(row))])

    def test_held_signin_notice_uses_intent_without_widening_acceptance(self):
        from personal_agent.quickstart_service import BROWSER_LOGIN_UNATTENDED_TEXT, BROWSER_LOGIN_PAGE_OFFERED_TEXT
        row,job=self.start_held_notice_run()
        with patch.object(self.service.browser_profile,'available',return_value=True):
            # Holding must not let a self-set run acquire #821's owner-accepted authority.
            self.assertEqual(self.service._request_browser_login(job,'https://shop.example.test/login'),
                             BROWSER_LOGIN_UNATTENDED_TEXT)
            self.assertIsNone(self.service._browser_login(job['id']))
            # Owner acceptance can land before the next hold tick restores the row's result channel.
            with self.store.db() as db:
                db.execute('UPDATE preparations SET accepted_by=? WHERE id=?',(prep.ACCEPTED_OWNER_REQUEST,row['id']))
            self.assertEqual(self.service._request_browser_login(job,'https://shop.example.test/login'),
                             BROWSER_LOGIN_PAGE_OFFERED_TEXT)
        login=self.service._browser_login(job['id'])
        self.service._put_browser_login(job['id'],{**login,'state':'opening'})
        with patch.object(self.service.browser_signin,'_login_window_baselines',return_value={'landed_site':None}), \
                patch.object(self.service,'_owner_signed_in',return_value=False):
            self.service._browser_login_opened(job['id'],login['nonce'],'fixture-window')
        with patch.object(self.service,'AUTO_PHONE_LOGIN',False):
            self.assertTrue(self.service.deliver_notification())
            self.assertFalse(self.service.deliver_notification())
        self.assertIn('shop.example.test',self.sends()[0]['text'])
        self.assertEqual(self.store.job(job['id'])['channel'],'web')

    def test_held_self_change_receipt_uses_recorded_send_intent(self):
        self.store.put('mattermost',{'enabled':True,'owner_user_id':'owner','channel_id':'dm','generation':'mm-1'})
        row,job=self.start_held_notice_run('mattermost')
        result=self.service.settings_tools(job)('settings_change',{'category':'preparations','setting':'pause',
                                                                 'value':row['id'],'reason':'Owner action is needed.'})
        self.assertTrue(result['applied'],result)
        self.assertIsNone(self.store.next_notification('telegram'))
        surface=next(s for s in self.service.surfaces if s.name=='mattermost')
        with patch.object(surface,'_post',return_value='posted-799') as wire:
            self.assertTrue(self.service.deliver_notification())
            self.assertFalse(self.service.deliver_notification())
        self.assertEqual(wire.call_count,1)

    def test_held_allowed_write_status_uses_recorded_send_intent(self):
        from personal_agent.api_requests import save_slot
        from personal_agent.browser_session import step_binding
        from personal_agent.self_set_writes import set_allowance
        save_slot(self.store,'notes',['notes.test'],'fixture-secret-123456789',money=False)
        set_allowance(self.service,'slot:notes',True,0,'owner')
        row,job=self.start_held_notice_run()
        binding=step_binding(job['id'],'api_request','https://notes.test/items','target')
        ticket=self.service.self_set_write(job,binding,'slot:notes','POST','/items',effect='irreversible')
        self.service.self_set_write_finished(job,ticket)
        self.assertEqual(len(self.sends()),1)
        self.assertIn('notes',self.sends()[0]['text'])
        self.assertEqual(self.store.job(job['id'])['delivery'],'none')

    def test_held_location_request_is_actionable_now_even_with_answer(self):
        for with_answer in (False,True):
            with self.subTest(with_answer=with_answer):
                row,job=self.start_held_notice_run()
                ask=self.service.location_requester(job)
                self.assertIsNotNone(ask)
                request=ask('Share the current position for this run.',with_answer=with_answer)
                self.assertEqual(self.service.context_observations.request_work(request),job['id'])
                self.assertEqual(self.sends()[0]['text'],'Share the current position for this run.')
                self.assertTrue(self.sends()[0]['reply_markup']['keyboard'][0][0]['request_location'])
                self.assertIsNone(self.store.config(self.service.LOCATION_OFFER_PREFIX+job['id']))
                self.assertEqual((self.store.job(job['id'])['channel'],self.store.job(job['id'])['delivery']),('web','none'))
                with self.store.db() as db:
                    db.execute("UPDATE jobs SET status='partial',response='Waiting for position' WHERE id=?",(job['id'],))
                from test_ask_location import _LocationCase
                _LocationCase.location(self)
                continued=self.store.job(self.service.preparations.get(row['id'])['last_run_job_id'])
                self.assertNotEqual(continued['id'],job['id'])
                self.assertEqual((continued['channel'],continued['chat_id']),('web',None))
                self.telegram.clear()
                self.finish_held_notice_run(row,continued)
                self.now+=86400

    def test_held_memory_candidate_request_is_sent_without_releasing_report(self):
        row,job=self.start_held_notice_run()
        self.store.save_memory_candidate(job['id'],'profile.fixture799','Owner candidate for review')
        self.script=[{'content':'Held answer with a candidate'}]*4
        self.assertTrue(self.service.run_one())
        self.assertTrue(self.service.deliver_notification())
        self.assertFalse(self.service.deliver_notification())
        self.assertIn('Owner candidate for review',self.sends()[0]['text'])
        self.assertNotIn('Held answer with a candidate',self.sends()[0]['text'])
        self.assertEqual(self.store.job(job['id'])['delivery'],'none')

    def test_held_memory_saved_status_is_sent_without_releasing_report(self):
        row,job=self.start_held_notice_run()
        self.store.save_memory('profile.fixture799','Owner fact saved',work_id=job['id'],notice=True)
        self.script=[{'content':'Held answer with a saved fact'}]*4
        self.assertTrue(self.service.run_one())
        self.assertTrue(self.service.deliver_notification())
        self.assertFalse(self.service.deliver_notification())
        self.assertIn('Owner fact saved',self.sends()[0]['text'])
        self.assertNotIn('Held answer with a saved fact',self.sends()[0]['text'])
        self.assertEqual(self.store.job(job['id'])['delivery'],'none')

    def test_held_budget_continue_request_then_report_is_bundled(self):
        import time
        from personal_agent.work_execution import WorkRun
        row,job=self.start_held_notice_run()
        run=WorkRun(job,time.time())
        budget=self.service.work_budget(job['id'])
        budget.spend_attempt();budget.spend_turn();budget.ledger.pause()
        with self.store.db() as db:
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (job['id'],'list_notes','succeeded',json.dumps({'evidence':{'count':1}}),time.time()))
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (job['id'],'list_notes','failed',json.dumps({'code':'attempt_budget','ran':False}),time.time()))
            db.execute("UPDATE jobs SET status='partial',response='Partial answer' WHERE id=?",(job['id'],))
        run.outcome='partial'
        self.assertTrue(self.service.offer_budget_continue(run))
        self.assertTrue(self.service.deliver_notification())
        self.assertFalse(self.service.deliver_notification())
        offer=self.service.budget_continue_offer(job['id'])
        self.assertIn('Partial answer',offer['progress'])
        self.assertTrue(self.service.budget_continue_decision({'work_id':job['id'],'nonce':offer['nonce']})['resumed'])
        self.telegram.clear()
        self.finish_held_notice_run(row,job)

    def test_work_mode_run_stays_local_after_switch_off_before_report_time(self):
        from personal_agent import work_mode
        self.at(10);brief,row=self.line();self.now+=86400;self.at(8)
        work_mode.set_enabled(self.store,True,surface='settings',clock=lambda:self.now-1)
        with patch('personal_agent.quickstart_store.time.time',return_value=self.now):
            work=self.report(row,'Work-mode result')
        work_mode.set_enabled(self.store,False,surface='settings',clock=lambda:self.now+60)
        self.telegram.clear();self.at(9)
        with patch.object(work_mode,'was_on_since',wraps=work_mode.was_on_since) as history:
            self.assertIsNone(self.flush())
        history.assert_any_call(self.store,self.store.job(work)['created'])
        self.assertEqual(self.bundles(),[]);self.assertEqual(self.sends(),[])
        self.assert_kept_locally(brief,work,'Work-mode result')
        self.now+=86400;self.at(18);self.assertIsNone(self.flush())

    def test_personal_mode_run_after_old_work_mode_interval_is_bundled(self):
        from personal_agent import work_mode
        self.at(10);_,row=self.line();self.now+=86400;self.at(8)
        work_mode.set_enabled(self.store,True,surface='settings',clock=lambda:self.now-120)
        work_mode.set_enabled(self.store,False,surface='settings',clock=lambda:self.now-60)
        with patch('personal_agent.quickstart_store.time.time',return_value=self.now):
            work=self.report(row,'Personal-mode result')
        self.telegram.clear();self.at(9);bundle=self.flush()
        self.assertEqual(self.store.job(bundle)['delivery'],'sent')
        self.assertEqual(self.receipts(),[(work,bundle)])
        self.assertIn('Personal-mode result',self.sends()[0]['text'])

    def test_pairing_change_does_not_readdress_a_held_run(self):
        for changed in ({'generation':'gen-2'},{'user_id':88}):
            with self.subTest(changed=changed):
                self.store.put('telegram',{'enabled':True,'generation':'gen-1','user_id':77})
                self.at(10);brief,row=self.line('Old pairing '+str(changed));self.now+=86400;self.at(8)
                work=self.report(row,'Result for original pairing')
                self.store.put('telegram',{**self.store.config('telegram'),**changed})
                self.telegram.clear();self.at(9);self.assertIsNone(self.flush())
                self.assertEqual(self.sends(),[])
                self.assert_kept_locally(brief,work,'Result for original pairing')

    def test_when_needed_cancelled_or_deleted_before_bundle_stays_local(self):
        for operation in ('cancel','delete'):
            with self.subTest(operation=operation):
                self.at(10);brief,row=self.line('Watch '+operation);self.now+=86400;self.at(8)
                with self.store.db() as db:
                    db.execute('UPDATE preparations SET delivery_mode=? WHERE id=?',(prep.DELIVERY_WHEN_NEEDED,row['id']))
                with patch.object(self.service,'watch_judgment',return_value='yes'):
                    work=self.report(row,'Watch result '+operation)
                if operation=='cancel':self.service.cancel_preparation(row['id'])
                else:self.service.preparations.delete(row['id'])
                self.telegram.clear();self.at(9);self.assertIsNone(self.flush())
                self.assertEqual(self.sends(),[])
                if operation=='cancel':self.assert_kept_locally(brief,work,'Watch result '+operation)
                else:
                    self.assertEqual(self.store.last_reply(work)['content'],'Watch result '+operation)
                    self.assertNotIn(work,[part for part,_ in self.receipts()])

    def test_bundle_uses_each_runs_delivery_projection(self):
        for projection in ('blocked-turn','system'):
            with self.subTest(projection=projection):
                self.at(10);_,row=self.line('Projection '+projection);self.now+=86400;self.at(8)
                work=self.report(row,'Raw worker text')
                with self.store.db() as db:
                    db.execute("INSERT INTO messages(job_id,role,content,channel,created,delivery_projection) "
                               "VALUES (?,'assistant',?,'web',?,?)",(work,'Projected '+projection,self.now,projection))
                self.telegram.clear();self.at(9);bundle=self.flush()
                self.assertIsNotNone(bundle)
                self.assertIn('Projected '+projection,self.sends()[0]['text'])
                self.assertNotIn('Raw worker text',self.sends()[0]['text'])
                self.now+=86400

    def test_bundle_preserves_partial_results_owner_note(self):
        self.at(10);_,row=self.line();self.now+=86400;self.at(8)
        work=self.report(row,'Partial worker answer')
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='partial',owner_note=? WHERE id=?",('Some material remains unavailable.',work))
        self.telegram.clear();self.at(9);self.flush()
        self.assertIn('Partial worker answer',self.sends()[0]['text'])
        self.assertIn('Some material remains unavailable.',self.sends()[0]['text'])

    def test_first_failure_receipt_uses_the_unbundled_empty_answer(self):
        self.at(10);_,row=self.held_watch();self.now+=86400;self.at(8)
        self.fail_watch(row)
        self.at(9);self.flush();self.helper.reconcile_line_bundles(self.now)
        self.assertEqual(self.watch_state(row),(prep.text_digest(''),'',self.now))

    def test_partial_watch_receipt_keeps_the_unbundled_digest_input(self):
        self.at(10);_,row=self.held_watch();self.now+=86400;self.at(8)
        with patch.object(self.service,'watch_judgment',return_value='yes'):
            work=self.report(row,'Reusable partial answer')
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='partial',owner_note=? WHERE id=?",('One source remains unavailable.',work))
        self.telegram.clear();self.at(9);self.flush()
        self.assertIn('One source remains unavailable.',self.sends()[0]['text'])
        self.helper.reconcile_line_bundles(self.now)
        self.assertEqual(self.watch_state(row),(prep.text_digest('Reusable partial answer'),'Reusable partial answer',self.now))
        self.at(10)
        with patch.object(self.service,'watch_judgment',return_value='yes') as judge:
            self.report(row,'Reusable partial answer')
        judge.assert_not_called()
        self.assertEqual(self.service.preparations.get(row['id'])['last_decision_reason'],prep.REASON_UNCHANGED)

    def test_mixed_work_mode_history_filters_only_the_restricted_part(self):
        from personal_agent import work_mode
        self.at(10);brief,row=self.line();self.now+=86400;self.at(7)
        work_mode.set_enabled(self.store,True,surface='settings',clock=lambda:self.now-1)
        with patch('personal_agent.quickstart_store.time.time',return_value=self.now):
            private=self.report(row,'Local work-mode result')
        work_mode.set_enabled(self.store,False,surface='settings',clock=lambda:self.now+60)
        self.at(8)
        with patch('personal_agent.quickstart_store.time.time',return_value=self.now):
            public=self.report(row,'Sendable personal result')
        self.telegram.clear();self.at(9);bundle=self.flush()
        self.assertEqual(self.receipts(),[(public,bundle)])
        self.assertNotIn('Local work-mode result',self.sends()[0]['text'])
        self.assertIn('Sendable personal result',self.sends()[0]['text'])
        self.assert_kept_locally(brief,private,'Local work-mode result')

    def test_destination_uses_its_existing_company_approved_work_mode_policy(self):
        from personal_agent import work_mode
        for approved in (False,True):
            with self.subTest(company_approved=approved):
                self.at(10);brief,row=self.line('Company policy '+str(approved));self.now+=86400;self.at(8)
                self.store.put('mattermost',{'enabled':True,'owner_user_id':'owner','channel_id':'dm',
                    'generation':'mm-1','company_approved':approved})
                with self.store.db() as db:
                    db.execute("UPDATE tool_events SET detail=json_set(detail,'$.channel','mattermost') "
                               "WHERE tool='line_held' AND json_extract(detail,'$.preparation_id')=?",(row['id'],))
                work_mode.set_enabled(self.store,True,surface='settings',clock=lambda:self.now-1)
                with patch('personal_agent.quickstart_store.time.time',return_value=self.now):
                    work=self.report(row,'Company result '+str(approved))
                work_mode.set_enabled(self.store,False,surface='settings',clock=lambda:self.now+60)
                self.at(9)
                surface=next(s for s in self.service.surfaces if s.name=='mattermost')
                with patch.object(surface,'_post_reply',return_value='posted-799') as wire:
                    bundle=self.flush()
                if approved:
                    self.assertEqual(self.store.job(bundle)['delivery'],'sent')
                    self.assertEqual(wire.call_count,1)
                    self.assertIn((work,bundle),self.receipts())
                else:
                    self.assertIsNone(bundle);wire.assert_not_called()
                    self.assert_kept_locally(brief,work,'Company result False')
                self.now+=86400

    def test_missing_pairing_receipt_after_interrupted_handoff_stays_local(self):
        self.at(10);brief,row=self.line();self.now+=86400;self.at(8)
        with patch.object(self.helper,'record_held_pairing',side_effect=RuntimeError('receipt interrupted')):
            with self.assertLogs('personal_agent.service',level='WARNING'):
                work=self.report(row,'Result without pairing receipt')
        self.service=self.make_service();self.helper=self.service.preparations_service
        self.telegram.clear();self.at(9);self.assertIsNone(self.flush())
        self.assertEqual(self.sends(),[])
        self.assert_kept_locally(brief,work,'Result without pairing receipt')

    def test_unresolvable_host_zone_does_not_break_reads_flush_or_unrelated_reminder(self):
        self.at(10);_,row=self.line();self.now+=86400;self.at(8)
        from personal_agent.current_context import TIMEZONE_CONFIG_KEY
        with self.store.db() as db:
            db.execute('DELETE FROM config WHERE key IN (?,?)',(TIMEZONE_CONFIG_KEY,'current_context'))
        self.assertIsNone(self.helper.home_zone())
        other=self.service.preparations.create(kind='reminder',goal='Owner reminder',due_at=self.now-1,
            timezone='Asia/Seoul',recurrence=None,channel='telegram',created_from=self.b,
            state=prep.STATE_SCHEDULED,accepted_by=prep.ACCEPTED_OWNER_REQUEST)
        with patch('personal_agent.current_context.host_zone',side_effect=LookupError('unset UTC host')):
            for name,call in (('get',lambda:self.service.preparations.get(row['id'])),
                              ('rows',self.service.preparations.rows),
                              ('status',self.helper.preparations_status),
                              ('flush',lambda:self.helper.flush_line_bundles(self.now)),
                              ('tick',self.service.run_due_preparation)):
                with self.subTest(step=name):call()
            self.assertIsNotNone(self.service.preparations.get(other['id'])['last_run_job_id'])

    def test_bundle_uses_recorded_telegram_intent_after_a_web_turn(self):
        self.at(10);brief,row=self.line();self.now+=86400;self.at(8)
        work=self.report(row,'Push-intended result')
        web=self.store.enqueue('web turn','web-turn-1',channel='web',owner_typed=True)
        with self.store.db() as db:
            db.execute("UPDATE jobs SET created=?,status='succeeded',response='ok' WHERE id=?",(self.now,web))
        self.telegram.clear();self.at(9);bundle=self.helper.flush_line_bundles(self.now)
        self.assertEqual(self.helper.line_outcomes(brief['line_id'])[0]['outcome'],el.OUTCOME_KEPT)
        self.service.deliver_one()
        self.assertEqual(self.store.job(bundle)['channel'],'telegram:gen-1')
        self.assertEqual(self.store.job(bundle)['delivery'],'sent')
        self.assertEqual(len(self.sends()),1)
        self.assertIn('Push-intended result',self.sends()[0]['text'])
        self.assertEqual(self.receipts(),[(work,bundle)])
        self.assertEqual(self.helper.line_outcomes(brief['line_id'])[0]['outcome'],el.OUTCOME_DELIVERED)

    def test_finished_report_survives_row_cancel_or_delete(self):
        self.at(10)
        for operation in ('cancel','delete'):
            with self.subTest(operation=operation):
                brief,row=self.line('Finished before '+operation);self.now+=86400;self.at(8)
                work=self.report(row,'Finished before '+operation)
                if operation=='cancel':self.service.cancel_preparation(row['id'])
                else:
                    # A worker can also read another line. That later read must not
                    # replace the preparation's own line when recovering a deleted row.
                    other=self.service.lines.open(self.b,task='Unrelated line',report_promise='Later')
                    self.helper.line_handler(self.store.job(work))({'action':'read','line':other['line_id']})
                    self.service.preparations.delete(row['id'])
                self.assertEqual(self.service.lines.readable(brief['line_id'])['state'],el.STATE_ACTIVE)
                self.telegram.clear();self.at(9);bundle=self.flush()
                self.assertIsNotNone(bundle)
                self.assertEqual(len(self.sends()),1)
                self.assertIn(brief['task'],self.sends()[0]['text'])
                self.assertNotIn('Unrelated line',self.sends()[0]['text'])
                self.assertIn((work,bundle),self.receipts())
                self.now+=86400

    def test_held_local_intent_run_cannot_request_a_location_continuation(self):
        from personal_agent.context_observations import answerable_work
        self.at(10);_,row=self.line();self.now+=86400;self.at(8)
        with self.store.db() as db:
            db.execute('UPDATE preparations SET due_at=? WHERE id=?',(self.now,row['id']))
            db.execute("UPDATE tool_events SET detail=json_set(detail,'$.channel','web') "
                       "WHERE tool='line_held' AND json_extract(detail,'$.preparation_id')=?",(row['id'],))
        self.service.run_due_preparation()
        work=self.service.preparations.get(row['id'])['last_run_job_id']
        job=self.store.job(work)
        self.assertEqual(job['channel'],'web');self.assertIsNone(job['chat_id'])
        self.assertFalse(answerable_work(job))
        self.assertIsNone(self.service.location_requester(job))
        with self.assertRaises(ValueError):self.service.request_current_location(work,'Where are you?')
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM context_location_requests WHERE job_id=?',(work,)).fetchone()[0],0)
            # Quiet watches intentionally started web even before holding. The
            # notice adapter must not turn them into paired interactive Works.
            db.execute('UPDATE preparations SET delivery_mode=? WHERE id=?',(prep.DELIVERY_WHEN_NEEDED,row['id']))
            db.execute("UPDATE tool_events SET detail=json_set(detail,'$.channel','telegram') "
                       "WHERE tool='line_held' AND json_extract(detail,'$.preparation_id')=?",(row['id'],))
        self.assertEqual(self.helper.notice_job(job),job)
        self.assertIsNone(self.service.location_requester(job))
        with self.assertRaises(ValueError):self.service.request_current_location(work,'Where are you?')

    def test_bundles_group_by_destination_and_cap_each_destination_at_each_slot(self):
        self.at(10);brief,row=self.line();self.now+=86400;self.at(8)
        local=self.service.preparations.create(kind='prepare',goal='Local report',due_at=self.now,
            timezone='Asia/Seoul',recurrence='daily',channel='web',bundle_channel='web',created_from=self.b,
            state=prep.STATE_SCHEDULED,accepted_by=prep.ACCEPTED_DAY_PLAN,line_id=brief['line_id'])
        tele_work=self.report(row,'Telegram result')
        local_work=self.report(local,'Local result')
        self.telegram.clear();self.at(9);self.flush()
        bundles=self.bundles()
        self.assertEqual(len(bundles),2)
        by_surface={b['channel'].split(':')[0]:b for b in bundles}
        self.assertEqual(set(by_surface),{'telegram','web'})
        self.assertIn('Telegram result',by_surface['telegram']['response'])
        self.assertNotIn('Local result',by_surface['telegram']['response'])
        self.assertIn('Local result',by_surface['web']['response'])
        self.assertNotIn('Telegram result',by_surface['web']['response'])
        self.assertEqual(set(self.receipts()),{(tele_work,by_surface['telegram']['id']),(local_work,by_surface['web']['id'])})
        self.assertEqual(len(self.sends()),1)
        # Pre-fix unsuffixed keys still consume the destination's slot.
        with self.store.db() as db:
            db.execute("UPDATE jobs SET request_key=substr(request_key,1,length(request_key)-length(':telegram')) WHERE id=?",
                       (by_surface['telegram']['id'],))
        self.at(9,1)
        later_tele=self.report(row,'Later Telegram result')
        later_local=self.report(local,'Later local result')
        self.assertIsNone(self.flush());self.assertEqual(len(self.bundles()),2)
        # An unpaired send shares the local fallback destination, so one bundle
        # drains both sets of parts instead of piling them up or making two web bundles.
        self.store.put('telegram',{'enabled':False})
        self.at(18);fallback=self.flush()
        self.assertEqual(len(self.bundles()),3)
        self.assertEqual(self.store.job(fallback)['channel'],'web')
        self.assertEqual(self.store.job(fallback)['delivery'],'none')
        self.assertIn((later_tele,fallback),self.receipts());self.assertIn((later_local,fallback),self.receipts())
        self.assertEqual(len(self.receipts()),4)
        self.assertIsNone(self.flush())

    def test_two_reports_one_interval_one_message_two_parts(self):
        self.at(10);a,ra=self.line('Responsibility A')
        self.now+=86400;b,rb=self.line('Responsibility B')
        self.now+=86400;self.at(8);self.telegram.clear()
        wa=self.report(ra,'First unique result');wb=self.report(rb,'Second unique result')
        self.assertEqual(self.sends(),[])
        self.assertEqual(self.helper.preparation_delivery(self.service.preparations.get(ra['id'])),'bundle')
        self.assertEqual(self.helper.line_outcomes(a['line_id'])[0]['outcome'],el.OUTCOME_KEPT)
        self.at(9);bundle=self.flush()
        self.assertIsNotNone(bundle)
        self.assertEqual(len(self.sends()),1)
        text=self.sends()[0]['text']
        for part in ('Responsibility A','Responsibility B','First unique result','Second unique result'):self.assertIn(part,text)
        self.assertEqual({w for w,_ in self.receipts()},{wa,wb})
        self.assertEqual(self.helper.line_outcomes(a['line_id'])[0]['outcome'],el.OUTCOME_DELIVERED)
        block,_=self.helper.line_judgment_input(self.now,refs=False)
        self.assertEqual(next(l for l in json.loads(block)['lines'] if l['line_id']==a['line_id'])['use']['reached_owner'],1)
        self.assertIn('묶음',self.service.preparation_run_fact(self.store.job(wa)))

    def test_burst_never_exceeds_report_times_and_every_part_bundled_exactly_once(self):
        self.at(10);brief,row=self.line();self.now+=86400;self.at(7);self.telegram.clear()
        works=[]
        for h,m in ((7,0),(8,0),(9,1),(10,0),(17,0),(18,1),(22,0)):
            self.at(h,m);works.append(self.report(row,f'Result {h}:{m}'))
            self.flush()
            self.assertLessEqual(len(self.bundles()),2)
        self.assertEqual(len(self.sends()),2)
        self.assertEqual(len(self.receipts()),5)
        self.now+=86400;self.at(9);self.flush()
        self.assertEqual(len(self.receipts()),len(works))
        self.assertEqual(sorted(w for w,_ in self.receipts()),sorted(works))
        for b in self.bundles():self.assertLessEqual(len(b['response']),BUNDLE_CHARS)

    def test_held_runs_spend_scheduled_budget_but_builtin_bundle_does_not(self):
        self.at(10);_,row=self.line();self.now+=86400;self.at(8)
        self.service.preparations.set_budget(1)
        work=self.report(row,'Budgeted held result')
        day=self.service.preparations.budget_day(self.now)
        usage_key=prep.SCHEDULED_USAGE_PREFIX+day
        self.assertEqual(self.store.config(usage_key),1)
        self.assertIsNotNone(self.service.preparations.get(row['id'])['offered_at'])
        # A second scheduled slot is refused before creating another model Work.
        self.now+=60
        with self.store.db() as db:
            db.execute('UPDATE preparations SET due_at=? WHERE id=?',(self.now,row['id']))
        calls=len(self.model_calls)
        self.service.run_due_preparation()
        self.assertFalse(self.service.run_one())
        self.assertEqual(len(self.model_calls),calls)
        with self.store.db() as db:
            events=[dict(r) for r in db.execute('SELECT job_id,status FROM tool_events WHERE tool=?',
                                                (prep.SCHEDULED_BUDGET_KEY,))]
            self.assertEqual(db.execute("SELECT count(*) FROM telegram_notifications WHERE kind='preparation_scheduled'").fetchone()[0],0)
        self.assertEqual([e['status'] for e in events],['admitted','skipped'])
        self.assertEqual(events[0]['job_id'],work)
        # The held result still reaches existing delivery even with the daily allowance exhausted.
        self.at(9);bundle=self.flush()
        self.assertIsNotNone(bundle)
        self.assertEqual(self.store.job(bundle)['provider'],'builtin')
        self.assertEqual(self.store.job(bundle)['delivery'],'sent')
        self.assertEqual(self.receipts(),[(work,bundle)])
        self.assertEqual(self.store.config(usage_key),1)
        self.assertEqual(len(self.model_calls),calls)
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM tool_events WHERE job_id=? AND tool=?',
                                        (bundle,prep.SCHEDULED_BUDGET_KEY)).fetchone()[0],0)

    def test_restart_mid_flush_no_duplicate_bundle(self):
        self.at(10);_,row=self.line();self.now+=86400;self.at(8);work=self.report(row)
        self.at(9)
        # A failure after jobs/messages/part inserts must roll back the entire handoff.
        with self.store.db() as db:
            db.execute("CREATE TRIGGER break_flush BEFORE INSERT ON tool_events WHEN NEW.tool='line_bundle' BEGIN SELECT RAISE(ABORT,'fixture crash'); END")
        with self.assertRaises(Exception):self.helper.flush_line_bundles(self.now)
        self.assertEqual(self.bundles(),[]);self.assertEqual(self.receipts(),[])
        with self.store.db() as db:db.execute('DROP TRIGGER break_flush')
        bundle=self.helper.flush_line_bundles(self.now) # committed but not yet sent
        self.service=self.make_service();self.helper=self.service.preparations_service
        self.assertIsNone(self.helper.flush_line_bundles(self.now))
        self.telegram.clear();self.service.deliver_one();self.service.deliver_one()
        self.assertEqual(len(self.sends()),1)
        self.assertEqual(self.receipts(),[(work,bundle)])

    def test_stopped_line_part_not_bundled_even_after_restart(self):
        self.at(10);brief,row=self.line();self.now+=86400;self.at(8);self.report(row)
        self.helper.stop_line(brief['line_id'],self.b)
        self.at(9);self.assertIsNone(self.flush());self.assertEqual(self.receipts(),[])
        self.helper.line_handler(self.store.job(self.b))({'action':'restart','line':brief['line_id']})
        self.assertIsNone(self.flush())

    def pending_bundle(self, *, mixed=False):
        self.at(10);brief,row=self.line('Responsibility A')
        other=other_row=None
        if mixed:
            self.now+=86400;other,other_row=self.line('Responsibility B')
        self.now+=86400;self.at(8)
        self.report(row,'Stopped older result')
        self.now+=60;self.report(row,'Stopped newest result')
        if mixed:
            self.now+=60;self.report(other_row,'Remaining older result')
            self.now+=60;self.report(other_row,'Remaining newest result')
        self.at(9);bundle=self.helper.flush_line_bundles(self.now)
        self.assertIsNotNone(bundle)
        self.assertEqual(self.store.job(bundle)['delivery'],'pending')
        self.telegram.clear()
        return brief,other,bundle

    def bundle_record(self, bundle):
        with self.store.db() as db:
            return (self.store.job(bundle),
                    [dict(r) for r in db.execute('SELECT * FROM messages WHERE job_id=?',(bundle,))],
                    [dict(r) for r in db.execute("SELECT * FROM tool_events WHERE job_id=? OR "
                        "(tool='line_bundled' AND json_extract(detail,'$.bundle_id')=?) ORDER BY id",(bundle,bundle))])

    def suppressions(self, bundle):
        with self.store.db() as db:
            return [{'work_id':r['job_id'],**json.loads(r['detail'])} for r in db.execute(
                "SELECT job_id,detail FROM tool_events WHERE tool='line_bundle_suppressed' "
                "AND json_extract(detail,'$.bundle_id')=? ORDER BY id",(bundle,))]

    def assert_bundle_outcomes(self, brief, bundle, outcome):
        works={work for work,receipt in self.receipts() if receipt==bundle
               and self.store.job(work)['response'].startswith(
                   'Stopped' if outcome==el.OUTCOME_KEPT else 'Remaining')}
        outcomes=self.helper.line_outcomes(brief['line_id'])
        self.assertEqual({o['work_id'] for o in outcomes},works)
        self.assertEqual(len(outcomes),2)
        self.assertEqual([o['outcome'] for o in outcomes],[outcome]*2)
        delivered=outcome==el.OUTCOME_DELIVERED
        entry=self.helper.line_entry(self.service.lines.current(brief['line_id']))
        self.assertEqual(entry['last_report'],prep.local_text(outcomes[0]['at'],'Asia/Seoul') if delivered else None)
        block,_=self.helper.line_judgment_input(self.now,refs=False)
        use=next(line['use'] for line in json.loads(block)['lines'] if line['line_id']==brief['line_id'])
        self.assertEqual(use['runs'],2)
        self.assertEqual(use['reached_owner'],2 if delivered else 0)
        self.assertEqual(use['kept'],0 if delivered else 2)
        self.assertEqual(use['last_report'] is not None,delivered)

    def test_suppressed_mixed_parts_stay_kept_in_every_delivery_reader_after_restart(self):
        brief,other,bundle=self.pending_bundle(mixed=True)
        self.helper.stop_line(brief['line_id'],self.b)
        self.service.deliver_one()
        self.assertEqual(self.store.job(bundle)['delivery'],'sent')
        for restarted in (False,True):
            with self.subTest(restarted=restarted):
                if restarted:
                    self.helper.line_handler(self.store.job(self.b))({'action':'restart','line':brief['line_id']})
                    self.service=self.make_service();self.helper=self.service.preparations_service
                self.assert_bundle_outcomes(brief,bundle,el.OUTCOME_KEPT)
                self.assert_bundle_outcomes(other,bundle,el.OUTCOME_DELIVERED)

    def test_cancelled_single_line_bundle_records_every_part_as_suppressed_and_kept(self):
        brief,_,bundle=self.pending_bundle()
        self.helper.stop_line(brief['line_id'],self.b)
        self.assertEqual(self.store.job(bundle)['delivery'],'cancelled')
        self.service.deliver_one()
        self.assertEqual(self.sends(),[])
        self.assert_bundle_outcomes(brief,bundle,el.OUTCOME_KEPT)
        self.assertEqual(self.suppressions(bundle),[
            {'work_id':work,'bundle_id':bundle,'line_id':brief['line_id']} for work,_ in self.receipts()])

    def test_suppressed_parts_stay_consumed_and_are_never_bundled_again(self):
        brief,_,bundle=self.pending_bundle(mixed=True)
        self.helper.stop_line(brief['line_id'],self.b)
        consumed=self.receipts()
        suppressed=self.suppressions(bundle)
        self.assertEqual(len(suppressed),2)
        self.helper.line_handler(self.store.job(self.b))({'action':'restart','line':brief['line_id']})
        self.helper.stop_line(brief['line_id'],self.b) # Repeated suppression is idempotent.
        self.assertEqual(self.suppressions(bundle),suppressed)
        self.helper.line_handler(self.store.job(self.b))({'action':'restart','line':brief['line_id']})
        self.service=self.make_service();self.helper=self.service.preparations_service
        self.service.deliver_one()
        self.now+=86400;self.at(9)
        self.assertIsNone(self.helper.flush_line_bundles(self.now))
        self.assertEqual(self.receipts(),consumed)
        self.assertEqual(self.suppressions(bundle),suppressed)

    def assert_pending_line_removed(self, *, forget=False, mixed=False):
        brief,other,bundle=self.pending_bundle(mixed=mixed)
        before,messages,events=self.bundle_record(bundle)
        # A committed pending bundle must still honour a stop after recovery.
        self.service=self.make_service();self.helper=self.service.preparations_service
        if forget:OwnerForget(self.store).forget(MEMORY_OWNER,'line:'+brief['line_id'])
        else:self.helper.stop_line(brief['line_id'],self.b)
        after,updated,receipts=self.bundle_record(bundle)
        self.assertEqual(after['id'],before['id'])
        self.assertEqual(after['request_key'],before['request_key'])
        self.assertEqual(receipts,events)
        self.assertEqual(self.suppressions(bundle),[
            {'work_id':work,'bundle_id':bundle,'line_id':brief['line_id']} for work,_ in self.receipts()
            if self.store.job(work)['response'].startswith('Stopped')])
        if mixed:
            self.assertEqual(after['delivery'],'pending')
            older=next(self.store.job(work) for work,_ in self.receipts()
                       if self.store.job(work)['response']=='Remaining older result')
            expected='\n\n'.join((msg('lines.bundle_heading',count=1),
                '\n'.join((other['task'],'Remaining newest result',
                    prep.local_text(older['created'],'Asia/Seoul')+' Remaining older result')),
                msg('lines.bundle_footer')))
            self.assertEqual(after['response'],expected)
            self.assertEqual(updated,[{**messages[0],'content':expected}])
            for text in (after['response'],updated[0]['content']):
                self.assertNotIn(brief['task'],text)
                self.assertNotIn('Stopped',text)
                for part in (other['task'],'Remaining older result','Remaining newest result'):
                    self.assertEqual(text.count(part),1)
        else:
            self.assertEqual(after['delivery'],'cancelled')
        self.service.deliver_one();self.service.deliver_one()
        self.assertEqual(len(self.sends()),1 if mixed else 0)
        if mixed:self.assertEqual(self.sends()[0]['text'],after['response'])
        # Consumption receipts stay even when the owner restarts the stopped line.
        if not forget:self.helper.line_handler(self.store.job(self.b))({'action':'restart','line':brief['line_id']})
        self.now+=86400
        self.assertIsNone(self.helper.flush_line_bundles(self.now))
        self.assertEqual(self.bundle_record(bundle)[2],events)

    def test_stop_cancels_committed_single_line_bundle(self):
        self.assert_pending_line_removed()

    def test_stop_rewrites_committed_mixed_line_bundle(self):
        self.assert_pending_line_removed(mixed=True)

    def test_forget_cancels_committed_single_line_bundle(self):
        self.assert_pending_line_removed(forget=True)

    def test_forget_rewrites_committed_mixed_line_bundle(self):
        self.assert_pending_line_removed(forget=True,mixed=True)

    def test_stop_leaves_sent_bundle_untouched(self):
        brief,_,bundle=self.pending_bundle(mixed=True)
        self.service.deliver_one()
        self.assertEqual(self.store.job(bundle)['delivery'],'sent')
        before=self.bundle_record(bundle)
        self.helper.stop_line(brief['line_id'],self.b)
        self.assertEqual(self.bundle_record(bundle),before)
        self.service.deliver_one()
        self.assertEqual(len(self.sends()),1)

    def test_stop_leaves_sending_bundle_untouched(self):
        brief,_,bundle=self.pending_bundle(mixed=True)
        with self.store.db() as db:db.execute("UPDATE jobs SET delivery='sending' WHERE id=?",(bundle,))
        before=self.bundle_record(bundle)
        self.helper.stop_line(brief['line_id'],self.b)
        self.assertEqual(self.bundle_record(bundle),before)

    def assert_bundle_rewrite_rolls_back(self, *, forget=False):
        brief,_,bundle=self.pending_bundle(mixed=True)
        before=self.bundle_record(bundle)
        with self.store.db() as db:
            db.execute("CREATE TRIGGER break_bundle_rewrite BEFORE UPDATE OF content ON messages "
                       "WHEN OLD.job_id IN (SELECT id FROM jobs WHERE model='line-bundle') "
                       "BEGIN SELECT RAISE(ABORT,'fixture rewrite failure'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError,'fixture rewrite failure'):
            if forget:OwnerForget(self.store).forget(MEMORY_OWNER,'line:'+brief['line_id'])
            else:self.helper.stop_line(brief['line_id'],self.b)
        self.assertEqual(self.bundle_record(bundle),before)
        self.assertEqual(self.service.lines.current(brief['line_id']),brief)
        self.assertEqual(self.suppressions(bundle),[])

    def test_stop_and_bundle_rewrite_roll_back_together(self):
        self.assert_bundle_rewrite_rolls_back()

    def test_forget_and_bundle_rewrite_roll_back_together(self):
        self.assert_bundle_rewrite_rolls_back(forget=True)

    def test_stop_cancels_every_pending_bundle_of_line(self):
        brief,_,first=self.pending_bundle()
        self.at(17);self.report(self.work_rows(brief)[0],'Later stopped result',deliver=False)
        self.at(18);second=self.helper.flush_line_bundles(self.now)
        self.assertIsNotNone(second)
        self.assertNotEqual(first,second)
        receipts=self.receipts()
        self.helper.stop_line(brief['line_id'],self.b)
        for bundle in (first,second):self.assertEqual(self.store.job(bundle)['delivery'],'cancelled')
        self.service.deliver_one();self.service.deliver_one()
        self.assertEqual(self.sends(),[])
        self.assertEqual(self.receipts(),receipts)

    def test_later_stop_does_not_restore_parts_of_restarted_line(self):
        brief,other,bundle=self.pending_bundle(mixed=True)
        self.helper.stop_line(brief['line_id'],self.b)
        self.helper.line_handler(self.store.job(self.b))({'action':'restart','line':brief['line_id']})
        self.helper.stop_line(other['line_id'],self.b)
        self.assertEqual(self.store.job(bundle)['delivery'],'cancelled')
        self.service.deliver_one()
        self.assertEqual(self.sends(),[])

    def test_later_stop_does_not_restore_parts_after_forget_undo(self):
        brief,other,bundle=self.pending_bundle(mixed=True)
        forget=OwnerForget(self.store)
        receipt=forget.forget(MEMORY_OWNER,'line:'+brief['line_id'])
        forget.undo(MEMORY_OWNER,receipt['receipt'])
        self.helper.stop_line(other['line_id'],self.b)
        self.assertEqual(self.store.job(bundle)['delivery'],'cancelled')
        self.service.deliver_one()
        self.assertEqual(self.sends(),[])

    def test_forgotten_line_or_run_not_bundled(self):
        self.at(10);brief,row=self.line();self.now+=86400;self.at(8);work=self.report(row)
        OwnerForget(self.store).forget(MEMORY_OWNER,'line:'+brief['line_id'])
        self.at(9);self.assertIsNone(self.flush());self.assertEqual(self.receipts(),[])

    def test_forgotten_run_is_not_bundled(self):
        self.at(10);_,row=self.line();self.now+=86400;self.at(8);work=self.report(row)
        with self.store.db() as db:
            message=db.execute("SELECT id FROM messages WHERE job_id=? AND role='assistant'",(work,)).fetchone()[0]
        OwnerForget(self.store).forget(MEMORY_OWNER,'message:'+str(message))
        self.at(9);self.assertIsNone(self.flush());self.assertEqual(self.receipts(),[])

    def test_pending_and_cancelled_bundle_do_not_count_as_delivered(self):
        self.at(10);brief,row=self.line();self.now+=86400;self.at(8);self.report(row)
        self.at(9);bundle=self.helper.flush_line_bundles(self.now)
        self.assertIsNotNone(bundle)
        self.assertEqual(self.helper.line_outcomes(brief['line_id'])[0]['outcome'],el.OUTCOME_KEPT)
        with self.store.db() as db:db.execute("UPDATE jobs SET delivery='cancelled' WHERE id=?",(bundle,))
        self.assertEqual(self.helper.line_outcomes(brief['line_id'])[0]['outcome'],el.OUTCOME_KEPT)
        self.assertIsNone(self.helper.flush_line_bundles(self.now))

    def test_bundle_has_generic_heading_each_part_once_and_footer_only(self):
        self.at(10);brief,row=self.line('Responsibility A')
        self.now+=86400;other,other_row=self.line('Responsibility B')
        self.now+=86400;self.at(8)
        self.report(row,'First unique result');self.report(other_row,'Second unique result')
        self.at(9);self.flush();text=self.bundles()[0]['response']
        self.assertEqual(text,'\n\n'.join((msg('lines.bundle_heading',count=2),
            brief['task']+'\nFirst unique result',other['task']+'\nSecond unique result',msg('lines.bundle_footer'))))
        parts=[]
        for line_number, (line, line_row) in enumerate(((brief,row),(other,other_row))):
            for run in range(2):
                parts.append({'brief':line,'job':{'created':self.now+run},'row':line_row,
                              'text':f'Unique result {line_number}/{run}'})
        for lang in LANGUAGES:
            with self.subTest(lang=lang),patch('personal_agent.entrusted_lines.msg',
                    side_effect=lambda key,**values:msg(key,lang,**values)):
                text=self.helper.compose_line_bundle(parts)
                heading=msg('lines.bundle_heading',lang,count=2)
                self.assertEqual(text.splitlines()[0],heading)
                blocks=[]
                for line_number, (line, line_row) in enumerate(((brief,row),(other,other_row))):
                    self.assertNotIn(line['task'],heading)
                    blocks.append('\n'.join((line['task'],f'Unique result {line_number}/1',
                        prep.local_text(self.now,line_row['timezone'])+f' Unique result {line_number}/0')))
                    self.assertEqual(text.count(line['task']),1)
                    for run in range(2):
                        self.assertEqual(text.count(f'Unique result {line_number}/{run}'),1)
                self.assertEqual(text,'\n\n'.join((heading,*blocks,msg('lines.bundle_footer',lang))))

    def test_bundle_is_bounded_names_overflow_and_redacts_saved_secrets(self):
        self.at(10);brief,row=self.line()
        # Composer boundary: many active lines, long texts, and multiple old runs.
        parts=[]
        for n in range(10):
            for run in range(3):parts.append({'brief':{**brief,'line_id':brief['line_id']+str(n),'task':'Task '+str(n)},
                                            'job':{'created':self.now+run},'row':row,'text':'Long '+str(run)+' '*2+'x'*2000})
        with patch.object(self.service.lines,'first_saved',return_value=self.now):text=self.helper.compose_line_bundle(parts)
        self.assertLessEqual(len(text),BUNDLE_CHARS)
        self.assertIn('Task 8',text);self.assertIn('Task 9',text)
        leads=[line for line in text.splitlines() if line.startswith('Long ')]
        self.assertEqual(len(leads),8)
        self.assertTrue(all(len(lead)>=200 for lead in leads))
        self.assertEqual(sum('Task 8' in l or 'Task 9' in l for l in text.splitlines()),1)
        # A secret saved after the brief was written is still removed from its label.
        self.store.secret('telegram_token','new-stored-secret-799')
        parts[0]['brief']['task']='new-stored-secret-799'
        with patch.object(self.service.lines,'first_saved',return_value=self.now):text=self.helper.compose_line_bundle(parts)
        self.assertNotIn('new-stored-secret-799',text)

    def test_owner_requested_or_accepted_rows_send_directly_without_bundling(self):
        self.at(10);brief,held=self.line();self.now+=86400;self.at(8)
        self.service.owner_model.set_controls({'report_times':['23:00']})
        for accepted in (prep.ACCEPTED_OWNER_REQUEST,prep.ACCEPTED_OWNER_WORK,prep.ACCEPTED_OWNER_BUTTON):
            row=self.service.preparations.create(kind='prepare',goal='Owner work '+accepted,due_at=self.now+1,timezone='Asia/Seoul',recurrence=None,
                channel='telegram',created_from=self.b,state=prep.STATE_SCHEDULED,accepted_by=accepted,line_id=brief['line_id'])
            self.telegram.clear();self.now+=2;work=self.report(row,'Owner result')
            self.assertEqual(self.store.job(work)['delivery'],'sent');self.assertEqual(len(self.sends()),1)
            self.assertFalse(self.helper.held_row(self.service.preparations.get(row['id'])))
        self.assertEqual(self.bundles(),[])

    def test_keep_rows_never_bundled(self):
        self.at(10);brief,_=self.line();self.now+=86400;self.at(8)
        row=self.service.preparations.create(kind='prepare',goal='Kept work',due_at=self.now+1,timezone='Asia/Seoul',recurrence=None,
                channel='web',created_from=self.b,state=prep.STATE_SCHEDULED,accepted_by=prep.ACCEPTED_DAY_PLAN,line_id=brief['line_id'])
        self.report(row);self.at(9);self.assertIsNone(self.flush())
        self.assertEqual(self.helper.preparation_delivery(self.service.preparations.get(row['id'])),'keep')

    def test_owner_acceptance_of_previously_held_row_restores_its_delivery(self):
        self.at(10);_,row=self.line();self.now+=86400;self.at(8)
        with self.store.db() as db:db.execute('UPDATE preparations SET accepted_by=? WHERE id=?',(prep.ACCEPTED_OWNER_BUTTON,row['id']))
        self.telegram.clear();work=self.report(row,'Accepted result')
        self.assertEqual(self.store.job(work)['delivery'],'sent')
        self.assertEqual(len(self.sends()),1)
        self.assertEqual(self.service.preparations.get(row['id'])['channel'],'telegram')
        self.at(9);self.assertIsNone(self.flush())

    def test_held_report_then_owner_accepts_is_bundled_once(self):
        # The verifier's test_held_report_then_owner_accepts_is_dropped probe, with assertions.
        self.at(10);_,row=self.line();self.now+=86400;self.at(8)
        work=self.report(row,'Held before acceptance')
        self.at(8,30)
        with self.store.db() as db:db.execute('UPDATE preparations SET accepted_by=? WHERE id=?',(prep.ACCEPTED_OWNER_BUTTON,row['id']))
        self.service.run_due_preparation()
        self.assertEqual(self.service.preparations.get(row['id'])['channel'],'telegram')
        self.at(9);self.telegram.clear();bundle=self.flush()
        self.assertIsNotNone(bundle)
        self.assertIn('Held before acceptance',self.store.job(bundle)['response'])
        self.now+=86400;self.at(9);self.assertIsNone(self.flush())
        self.assertEqual(self.store.job(work)['delivery'],'none')
        self.assertEqual(self.receipts(),[(work,bundle)])
        self.assertEqual(len(self.sends()),1)

    def test_upkeep_failure_never_skips_holding_or_bundle_flush(self):
        self.at(10);_,row=self.line()
        for step in ('withdraw_unannounced','announce_stood_line','start_announced_lines'):
            with self.subTest(step=step):
                # Existing L1 row: the next tick must hold it even if earlier upkeep breaks.
                with self.store.db() as db:
                    db.execute("DELETE FROM tool_events WHERE tool='line_held' AND json_extract(detail,'$.preparation_id')=?",(row['id'],))
                    db.execute("UPDATE preparations SET channel='telegram' WHERE id=?",(row['id'],))
                self.now+=86400;self.at(8);self.telegram.clear()
                with patch.object(self.helper,step,side_effect=KeyError('fixture failure')),self.assertLogs(
                        'personal_agent.service',level='WARNING') as logs:
                    work=self.report(row,'Held despite upkeep failure')
                    self.assertEqual(self.store.job(work)['channel'],'web')
                    self.assertEqual(self.store.job(work)['delivery'],'none')
                    self.assertEqual(self.sends(),[])
                    self.at(9);self.service.run_due_preparation();self.service.deliver_one()
                    self.assertEqual(len(self.sends()),1)
                    self.assertIn(work,[w for w,_ in self.receipts()])
                self.assertTrue(any('step='+step in line and 'kind=KeyError' in line for line in logs.output))

    def test_new_stood_row_is_created_held_even_if_account_creation_fails(self):
        self.at(10)
        self.a=self.owner('Earlier owner request',self.now-86400)
        self.b=self.owner('Later owner request',self.now-60)
        self.at(10);brief=self.stand();notice=self.announce(brief)
        with patch.object(self.helper,'ensure_line_account',side_effect=RuntimeError('fixture failure')):
            try:self.deliver(notice)
            except RuntimeError:pass  # Inspect the row committed before the account failure.
        row=self.work_rows(brief)[0]
        self.assertEqual(row['channel'],'web')
        self.assertTrue(self.helper.held_row(row))
        with self.store.db() as db:
            held=db.execute("SELECT detail FROM tool_events WHERE tool='line_held' AND json_extract(detail,'$.preparation_id')=?",(row['id'],)).fetchone()
        self.assertIsNotNone(held)
        self.assertEqual(json.loads(held['detail'])['channel'],'telegram')

    def test_failed_hold_leaves_legacy_row_due_but_still_flushes_held_parts(self):
        self.at(10);_,row=self.line()
        self.now+=86400;_,legacy=self.line('Legacy responsibility')
        self.now+=86400;self.at(8)
        work=self.report(row,'Already held result')
        # Recreate an L1 initial row that was already announced, so #874's offer
        # gate cannot hide a missing L2 hold guard by independently skipping it.
        with self.store.db() as db:
            db.execute("DELETE FROM tool_events WHERE tool='line_held' AND json_extract(detail,'$.preparation_id')=?",(legacy['id'],))
            db.execute("UPDATE preparations SET due_at=?,channel='telegram' WHERE id=?",(self.now,legacy['id']))
        self.telegram.clear()
        with patch.object(self.helper,'hold_line_rows',side_effect=RuntimeError('fixture failure')):
            self.service.run_due_preparation()
            self.at(9);self.service.run_due_preparation();self.service.deliver_one()
        self.assertIsNone(self.service.preparations.get(legacy['id'])['last_run_job_id'])
        self.assertEqual(self.service.preparations.get(legacy['id'])['state'],prep.STATE_SCHEDULED)
        self.assertIsNotNone(self.service.preparations.get(legacy['id'])['offered_at'])
        self.assertEqual(len(self.sends()),1)
        self.assertEqual([w for w,_ in self.receipts()],[work])

    def test_held_schedule_names_bundles_and_report_times_in_every_locale(self):
        self.at(10);_,row=self.line()
        self.service.owner_model.set_controls({'report_times':['07:15','20:45']})
        row=self.service.preparations.get(row['id'])
        for lang in LANGUAGES:
            with self.subTest(lang=lang):
                text=prep.schedule_text(row,lang=lang)
                self.assertIn('07:15',text);self.assertIn('20:45',text)
                self.assertIn(msg('preparations.schedule_delivery_bundle',lang,
                                  owner_preparation_schedule_with_delivery='',report_times='07:15, 20:45').strip(),text)
        listed=next(r for r in self.service.preparations.rows() if r['id']==row['id'])
        self.assertIn('07:15, 20:45',prep.proposal_summary(listed))
        self.assertIn('07:15, 20:45',prep.proposal_html([listed],0))

    def test_mid_run_acceptance_of_held_when_needed_row_is_told_once(self):
        self.at(10);_,row=self.line();self.now+=86400;self.at(8)
        with self.store.db() as db:
            db.execute('UPDATE preparations SET delivery_mode=?,due_at=? WHERE id=?',(prep.DELIVERY_WHEN_NEEDED,self.now,row['id']))
        self.service.run_due_preparation()
        work=self.service.preparations.get(row['id'])['last_run_job_id']
        self.assertEqual(self.store.job(work)['channel'],'web')
        with self.store.db() as db:db.execute('UPDATE preparations SET accepted_by=? WHERE id=?',(prep.ACCEPTED_OWNER_BUTTON,row['id']))
        self.now+=1;self.helper.hold_line_rows(self.now)
        self.assertEqual(self.service.preparations.get(row['id'])['channel'],'telegram')
        self.telegram.clear();self.script=[{'content':'Mid-run accepted result'}]*4
        with patch.object(self.service,'watch_judgment',return_value='yes'):
            self.assertTrue(self.service.run_one());self.service.run_due_preparation()
        with patch('personal_agent.telegram_store.time.time',return_value=self.now):self.service.deliver_notification()
        self.assertEqual(self.store.notification_of(work,prep.NOTIFY_KIND)['state'],'sent')
        self.at(9);self.assertIsNone(self.flush());self.assertEqual(self.receipts(),[])
        self.assertEqual(len(self.sends()),1)
        self.assertIn('Mid-run accepted result',self.sends()[0]['text'])

    def test_l1_inflight_when_needed_report_is_not_lost_when_holding_starts(self):
        self.at(10);_,row=self.line();self.now+=86400;self.at(8)
        with self.store.db() as db:
            db.execute("DELETE FROM tool_events WHERE tool='line_held'")
            db.execute('UPDATE preparations SET due_at=?,channel=?,delivery_mode=? WHERE id=?',
                       (self.now,'telegram',prep.DELIVERY_WHEN_NEEDED,row['id']))
        with patch.object(self.helper,'hold_line_rows',return_value=None),patch.object(self.helper,'held_row',return_value=False):self.service.run_due_preparation()
        work=self.service.preparations.get(row['id'])['last_run_job_id']
        self.assertEqual(self.store.job(work)['channel'],'web')
        self.now+=1;self.helper.hold_line_rows(self.now)
        self.script=[{'content':'In-flight result'}]*4
        with patch.object(self.service,'watch_judgment',return_value='yes'):
            self.assertTrue(self.service.run_one());self.service.run_due_preparation()
        self.assertIsNone(self.store.notification_of(work,prep.NOTIFY_KIND))
        self.at(9);bundle=self.flush()
        self.assertIsNotNone(bundle)
        self.assertEqual(self.receipts(),[(work,bundle)])
        self.assertIn('In-flight result',self.store.job(bundle)['response'])

    def test_report_times_default_validation_and_normalization(self):
        self.assertEqual(self.service.owner_model.settings()['report_times'],['09:00','18:00'])
        for value in ([],['08:00']*2,['12:00','09:00'],['24:00'],['09:60'],['9:00'],['09:00 '],['０９:00'],[True],
                      '09:00',None,['00:00','01:00','02:00','03:00','04:00']):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):self.service.owner_model.set_controls({'report_times':value})
                self.assertEqual(om.Upkeep._controls({'report_times':value})['report_times'],['09:00','18:00'])
        for value in (['00:00'],['00:00','06:00','12:00','23:59']):
            self.assertEqual(self.service.owner_model.set_controls({'report_times':value})['report_times'],value)

    def test_changing_setting_does_not_send_extra_bundle_same_day(self):
        self.at(10);_,row=self.line();self.now+=86400;self.at(8);self.report(row)
        self.at(9);self.flush()
        self.service.owner_model.set_controls({'report_times':['10:00','11:00','12:00','13:00']})
        self.at(9,1);self.report(row,'Late part')
        for hour in (10,11,12,13):self.at(hour);self.assertIsNone(self.flush())
        self.assertEqual(len(self.bundles()),1)
        self.at(18);self.flush();self.assertEqual(len(self.bundles()),2)
        self.now+=86400;self.at(8);self.report(row,'Tomorrow')
        self.at(9);self.assertIsNone(self.flush())
        self.at(10);self.assertIsNotNone(self.flush())

    def test_empty_last_slot_leaves_late_parts_for_tomorrow(self):
        self.at(10);_,row=self.line();self.now+=86400;self.at(19)
        work=self.report(row,'Late only result')
        self.assertIsNone(self.flush());self.assertEqual(self.receipts(),[])
        self.now+=86400;self.at(9);bundle=self.flush()
        self.assertEqual(self.receipts(),[(work,bundle)])
        self.assertEqual(len(self.bundles()),1)

    def test_finish_beyond_settle_allowance_waits_for_next_slot_even_if_empty(self):
        self.at(10);_,row=self.line();self.now+=86400;self.at(8,59)
        with self.store.db() as db:db.execute('UPDATE preparations SET due_at=? WHERE id=?',(self.now,row['id']))
        self.service.run_due_preparation()
        work=self.service.preparations.get(row['id'])['last_run_job_id']
        self.at(9);self.assertIsNone(self.flush())
        self.at(9,31);self.script=[{'content':'Finished late'}]*4
        self.assertTrue(self.service.run_one());self.service.run_due_preparation()
        self.assertIsNone(self.flush());self.assertEqual(self.receipts(),[])
        self.at(18);bundle=self.flush()
        self.assertEqual(self.receipts(),[(work,bundle)])

    def test_running_part_waits_only_until_settle_deadline(self):
        self.at(10);_,row=self.line();self.now+=86400;self.at(8);self.report(row)
        with self.store.db() as db:db.execute('UPDATE preparations SET due_at=?,state=? WHERE id=?',(self.now+60,prep.STATE_SCHEDULED,row['id']))
        self.now+=60;self.service.run_due_preparation()
        self.at(9);self.assertIsNone(self.flush())
        self.now+=BUNDLE_SETTLE_SECONDS;self.assertIsNotNone(self.flush())
        self.script=[{'content':'Late running result'}];self.assertTrue(self.service.run_one());self.service.run_due_preparation()
        self.assertEqual(len(self.receipts()),1)
        self.at(18);self.flush();self.assertEqual(len(self.receipts()),2)

    def held_watch(self, task='Watched responsibility'):
        brief,row=self.line(task)
        with self.store.db() as db:
            db.execute('UPDATE preparations SET delivery_mode=? WHERE id=?',(prep.DELIVERY_WHEN_NEEDED,row['id']))
        return brief,self.service.preparations.get(row['id'])

    def fail_watch(self, row, cause='The source did not respond.'):
        with self.store.db() as db:
            db.execute('UPDATE preparations SET due_at=? WHERE id!=? AND state=?',(self.now+86400,row['id'],prep.STATE_SCHEDULED))
            db.execute('UPDATE preparations SET due_at=?,state=? WHERE id=?',(self.now,prep.STATE_SCHEDULED,row['id']))
        self.service.run_due_preparation()
        work=self.service.preparations.get(row['id'])['last_run_job_id']
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='failed',response=NULL,error='transport error',owner_cause=?,delivery='none' WHERE id=?",(cause,work))
        self.service.run_due_preparation()
        return work

    def watch_state(self, row):
        current=self.service.preparations.get(row['id'])
        return tuple(current[key] for key in ('last_notified_digest','last_notified_text','last_notified_at'))

    def propagation_events(self):
        with self.store.db() as db:
            return [dict(r) for r in db.execute("SELECT * FROM tool_events WHERE tool='line_bundle_notified' ORDER BY id")]

    def test_held_watch_failure_is_bundled_once_with_its_recorded_cause(self):
        self.at(10);brief,row=self.held_watch();self.now+=86400;self.at(8)
        work=self.fail_watch(row)
        self.assertEqual(self.service.preparations.get(row['id'])['last_decision_reason'],prep.REASON_RUN_FAILED_TOLD)
        # A second failure before delivery must not consume or duplicate the first notice.
        self.now+=60;second=self.fail_watch(row,'The source still did not respond.')
        self.assertEqual(self.service.preparations.get(row['id'])['last_decision_reason'],prep.REASON_RUN_FAILED)
        self.telegram.clear();self.at(9);bundle=self.flush()
        self.assertIsNotNone(bundle)
        expected=self.service.telegram_result_text(None,'The source did not respond.','failed')
        text=self.store.job(bundle)['response']
        self.assertIn(msg('projection.i_couldn_t_complete_this_request'),text)
        self.assertIn(' '.join(expected.split()),' '.join(text.split()))
        self.assertNotIn('transport error',text)
        self.assertNotIn('still did not respond',text)
        self.assertEqual(self.receipts(),[(work,bundle)])
        self.assertEqual(len(self.sends()),1)
        self.assertIsNone(self.store.notification_of(work,prep.NOTIFY_KIND))
        self.assertIsNone(self.store.notification_of(second,prep.NOTIFY_KIND))
        self.at(18);self.helper.line_tick(self.now);self.service.deliver_one()
        self.assertEqual(len(self.sends()),1)
        self.assertEqual(len(self.bundles()),1)

    def test_pending_bundle_rewrite_preserves_another_lines_failure_notice(self):
        self.at(10);brief,row=self.line('Stopped report')
        self.now+=86400;other,other_row=self.held_watch('Failing report')
        self.now+=86400;self.at(8)
        self.report(row,'Remove this result');work=self.fail_watch(other_row)
        self.at(9);bundle=self.helper.flush_line_bundles(self.now)
        self.helper.stop_line(brief['line_id'])
        self.assertIn('The source did not respond.',self.store.job(bundle)['response'])
        self.assertNotIn('Remove this result',self.store.job(bundle)['response'])
        self.service.deliver_one();self.helper.line_tick(self.now)
        # The displayed failure survives the rewrite; notification comparison
        # state matches the unbundled empty raw answer, not its failure bubble.
        self.assertEqual(self.watch_state(other_row),(prep.text_digest(''),'',self.now))

    def test_settings_failure_told_claim_waits_for_bundle_receipt(self):
        for delivery in ('sent','web','cancelled'):
            with self.subTest(delivery=delivery):
                self.at(10);_,row=self.held_watch('Failure '+delivery);self.now+=86400;self.at(8)
                self.fail_watch(row)
                def reason():
                    return next(r for r in self.helper.preparations_status()['preparations'] if r['id']==row['id'])['last_decision_reason']
                self.assertEqual(reason(),prep.REASON_RUN_FAILED)
                if delivery=='web':self.store.put('telegram',{'enabled':False})
                self.at(9);bundle=self.helper.flush_line_bundles(self.now)
                self.assertIsNotNone(bundle)
                if delivery!='web':self.assertEqual(reason(),prep.REASON_RUN_FAILED)
                if delivery=='sent':self.service.deliver_one()
                elif delivery=='cancelled':self.helper.stop_line(row['line_id'])
                self.helper.line_tick(self.now)
                self.assertEqual(reason(),prep.REASON_RUN_FAILED if delivery=='cancelled' else prep.REASON_RUN_FAILED_TOLD)
                self.store.put('telegram',{'enabled':True,'user_id':77,'generation':'gen-1','cursor':0})
                self.now+=86400

    def test_sent_bundle_updates_watch_and_identical_next_result_is_unchanged(self):
        self.at(10);_,row=self.held_watch();self.now+=86400;self.at(8)
        with patch.object(self.service,'watch_judgment',return_value='yes'):
            self.report(row,'A result worth telling')
        self.at(9);bundle=self.helper.flush_line_bundles(self.now)
        self.helper.line_tick(self.now)
        self.assertEqual(self.watch_state(row),(None,None,None),'pending is not notified')
        self.service.deliver_one();self.now+=1;self.helper.line_tick(self.now)
        self.assertEqual(self.watch_state(row),(prep.text_digest('A result worth telling'),'A result worth telling',self.now))
        self.at(10)
        with patch.object(self.service,'watch_judgment',return_value='yes') as judge:
            self.report(row,'A result worth telling')
        judge.assert_not_called()
        self.assertEqual(self.service.preparations.get(row['id'])['last_decision_reason'],prep.REASON_UNCHANGED)
        self.at(18);self.assertIsNone(self.flush())
        self.assertEqual(len(self.bundles()),1)

    def test_web_bundle_propagates_watch_notification(self):
        self.at(10);_,row=self.held_watch();self.now+=86400;self.at(8)
        with patch.object(self.service,'watch_judgment',return_value='yes'):self.report(row,'Web result')
        self.store.put('telegram',{'enabled':False})
        self.at(9);bundle=self.helper.flush_line_bundles(self.now)
        self.assertEqual(self.store.job(bundle)['channel'],'web')
        self.assertEqual(self.store.job(bundle)['delivery'],'none')
        self.helper.line_tick(self.now)
        self.assertEqual(self.watch_state(row),(prep.text_digest('Web result'),'Web result',self.now))

    def test_bundle_notification_propagates_once_across_restarts(self):
        self.at(10);_,row=self.held_watch();self.now+=86400;self.at(8)
        with patch.object(self.service,'watch_judgment',return_value='yes'):self.report(row,'Restart result')
        self.at(9);bundle=self.flush()
        for restart in range(3):
            self.service=self.make_service();self.helper=self.service.preparations_service
            with patch.object(self.service.preparations,'record_notified',wraps=self.service.preparations.record_notified) as record:
                self.helper.line_tick(self.now)
                self.assertEqual(record.call_count,1 if restart==0 else 0)
            self.now+=1
        self.assertEqual([e['job_id'] for e in self.propagation_events()],[bundle])
        self.assertEqual(self.watch_state(row)[2],self.now-3)

    def test_bundle_notification_and_marker_roll_back_together(self):
        self.at(10);_,row=self.held_watch();self.now+=86400;self.at(8)
        with patch.object(self.service,'watch_judgment',return_value='yes'):self.report(row,'Atomic result')
        self.at(9);bundle=self.flush()
        with self.store.db() as db:
            db.execute("CREATE TRIGGER break_propagation BEFORE INSERT ON tool_events WHEN NEW.tool='line_bundle_notified' BEGIN SELECT RAISE(ABORT,'fixture crash'); END")
        with self.assertLogs('personal_agent.service',level='WARNING'):self.helper.line_tick(self.now)
        self.assertEqual(self.watch_state(row),(None,None,None))
        self.assertEqual(self.propagation_events(),[])
        with self.store.db() as db:db.execute('DROP TRIGGER break_propagation')
        self.service=self.make_service();self.helper=self.service.preparations_service
        self.helper.line_tick(self.now)
        self.assertEqual(self.watch_state(row),(prep.text_digest('Atomic result'),'Atomic result',self.now))
        self.assertEqual([e['job_id'] for e in self.propagation_events()],[bundle])

    def test_suppressed_bundle_part_does_not_propagate(self):
        self.at(10);brief,row=self.held_watch('Suppressed watch')
        self.now+=86400;other,other_row=self.held_watch('Remaining watch')
        self.now+=86400;self.at(8)
        with patch.object(self.service,'watch_judgment',return_value='yes'):
            self.report(row,'Suppressed result');self.report(other_row,'Remaining result')
        self.at(9);bundle=self.helper.flush_line_bundles(self.now)
        self.helper.stop_line(brief['line_id'])
        self.service.deliver_one();self.helper.line_tick(self.now)
        self.assertEqual(self.watch_state(other_row),(prep.text_digest('Remaining result'),'Remaining result',self.now))
        self.assertEqual(self.watch_state(row),(None,None,None))
        self.assertEqual([e['job_id'] for e in self.propagation_events()],[bundle])

    def test_cancelled_bundle_does_not_propagate(self):
        self.at(10);brief,row=self.held_watch();self.now+=86400;self.at(8)
        with patch.object(self.service,'watch_judgment',return_value='yes'):self.report(row,'Cancelled result')
        self.at(9);bundle=self.helper.flush_line_bundles(self.now)
        self.helper.stop_line(brief['line_id']);self.helper.line_tick(self.now)
        self.assertEqual(self.store.job(bundle)['delivery'],'cancelled')
        self.assertEqual(self.watch_state(row),(None,None,None))
        self.assertEqual(self.propagation_events(),[])

    def test_when_needed_only_notify_is_bundled_and_unpaired_send_falls_back_to_web(self):
        self.at(10);brief,row=self.line();self.now+=86400;self.at(8)
        with self.store.db() as db:db.execute('UPDATE preparations SET delivery_mode=? WHERE id=?',(prep.DELIVERY_WHEN_NEEDED,row['id']))
        with patch.object(self.service,'watch_judgment',return_value='no'):self.report(row,'Silent result')
        self.at(9);self.assertIsNone(self.flush())
        self.at(10)
        with patch.object(self.service,'watch_judgment',return_value='yes'):work=self.report(row,'Notify result')
        self.store.put('telegram',{'enabled':False})
        self.at(18);bundle=self.flush();self.assertIsNotNone(bundle)
        self.assertEqual(self.store.job(bundle)['delivery'],'none')
        self.assertEqual(self.receipts(),[(work,bundle)])
        self.assertEqual(self.helper.line_outcomes(brief['line_id'])[0]['outcome'],el.OUTCOME_DELIVERED)
