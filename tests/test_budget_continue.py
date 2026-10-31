"""#471: real Work lifecycle, ledger and effect guards; scripted workers, no live models."""
import json
import time
import unittest
from unittest import mock

import test_agency_loop as loop
from personal_agent import owner_mcp, trust_record
from personal_agent.agent_runtime import WorkLedger, WORK_LEDGER_KEY, WORK_STOP_KEY
from personal_agent.messages import msg
from personal_agent.work_execution import WorkRun
from personal_agent.conversation_surface import notice_binding, notice_text


class BudgetContinueTests(unittest.TestCase):
    def service(self, script=None):
        service,store=loop.OwnerEntryPointTests.service(self,script or loop.Script(),loop.Network())
        original=service.work_budget
        def budget(work_id):
            value=original(work_id)
            value.attempts=1
            return value
        service.work_budget=budget
        return service,store

    def stopped(self, code='attempt_budget', progress=True, **options):
        service,store=self.service()
        job=store.enqueue('Read the material and produce a comparison','budget-test',**options)
        run=WorkRun(store.job(job),time.time())
        budget=service.work_budget(job)
        budget.spend_attempt();budget.spend_turn();budget.ledger.pause()
        if code in ('deadline','deadline_exceeded'):
            store.put(budget.ledger.key,{**store.config(budget.ledger.key),'remaining_seconds':0})
        with store.db() as db:
            if progress:
                db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                           (job,'list_notes','succeeded',json.dumps({'evidence':{'count':3,'titles':['Read A, B and C']}}),time.time()))
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (job,'model' if code=='turn_budget' else 'list_notes','stopped' if code=='turn_budget' else 'failed',json.dumps({'code':code,'ran':False,'effect':'none'}),time.time()))
            db.execute("UPDATE jobs SET status='partial',response=? WHERE id=?",('Compared A and B; C still needs comparison.',job))
        run.outcome='partial'
        return service,store,job,run,budget

    def press(self,service,job):
        return service.budget_continue_decision({'work_id':job,'nonce':service.budget_continue_offer(job)['nonce']})

    def test_budget_codes_offer_observed_done_left_and_missing_usage(self):
        for code in ('attempt_budget','turn_budget','deadline','deadline_exceeded'):
            with self.subTest(code=code):
                service,store,job,run,_=self.stopped(code)
                self.assertTrue(service.offer_budget_continue(run))
                offer=service.budget_continue_offer(job)
                self.assertIn(msg('information_use.usage_not_reported'),offer['text'])
                self.assertIn(msg('budget_continue.done',count=1),offer['text'])
                self.assertIn(msg('budget_continue.remaining'),offer['text'])
                self.assertIn('1',offer['text'])
                self.assertIn('Read A, B and C',offer['progress'])
                self.assertTrue(service.task_progress(job)['selected']['budget_continue'])
                self.assertTrue(self.press(service,job)['resumed'])
                renewed=store.config(WORK_LEDGER_KEY+':'+job)
                self.assertEqual((renewed['attempts'],renewed['turns']),(0,0))
                self.assertGreater(renewed['remaining_seconds'],0)
                service.work_budget(job).check()

    def test_real_worker_resumes_same_work_with_fresh_budget_and_carried_answer(self):
        script=loop.Script({'tool_calls':[loop.call('a','list_notes')]},
                           {'tool_calls':[loop.call('b','find_files',query='remaining')]},
                           {'content':'Compared A and B; C remains.'},
                           {'content':'The comparison is complete.'})
        service,store=self.service(script)
        job=store.enqueue('Read the material and produce a comparison','real-budget')
        self.assertTrue(service.run_one())
        self.assertEqual(store.job(job)['status'],'partial')
        offer=service.budget_continue_offer(job)
        self.assertEqual(offer['state'],'offered')
        first_response=store.job(job)['response']
        old=store.config(WORK_LEDGER_KEY+':'+job)
        self.assertEqual(old['attempts'],1)
        self.assertTrue(self.press(service,job)['resumed'])
        self.assertEqual(store.job(job)['status'],'queued')
        renewed=store.config(WORK_LEDGER_KEY+':'+job)
        self.assertEqual((renewed['attempts'],renewed['turns'],renewed['nudges']),(0,0,0))
        self.assertGreater(renewed['remaining_seconds'],0)
        self.assertFalse(self.press(service,job)['resumed'])
        self.assertTrue(service.run_one())
        prompt=json.dumps(script.bodies[-1],ensure_ascii=False)
        self.assertIn(first_response,prompt)
        self.assertIn('Do not repeat completed actions',prompt)
        self.assertEqual(store.job(job)['status'],'succeeded')
        receipts=[e for e in store.task_events(job) if e['tool']=='action_continuation' and e['status']=='queued']
        self.assertEqual(len(receipts),1)

    def test_observed_change_cannot_be_replayed_and_unknown_withholds(self):
        service,store,job,run,_=self.stopped()
        service.set_ai_connections({'enabled':True})
        owner_mcp.set_confirmed(store,'codex',['synthetic'])
        target=trust_record.connection('codex','owner-mcp','synthetic')
        trust_record.set_operation(store,target,'apply_change','mutate')
        args={'engine':'codex','tool_name':'mcp__synthetic__apply_change','input':{'item':'a'}}
        with store.db() as db:db.execute("UPDATE jobs SET status='running' WHERE id=?",(job,))
        effect_ledger=WorkLedger(store,job,fresh=True)
        self.assertEqual(service.connector_permission(store.job(job),args)['behavior'],'allow')
        digest=service.connector_input_digest(args['tool_name'],args['input'])
        service.record_connector_calls(job,[{'tool':args['tool_name'],'status':'succeeded','input_digest':digest}],engine='codex')
        effect_ledger.pause()
        with store.db() as db:db.execute("UPDATE jobs SET status='partial' WHERE id=?",(job,))
        self.assertTrue(service.offer_budget_continue(run))
        self.assertTrue(self.press(service,job)['resumed'])
        self.assertIn('Observed completed action',service.action_continuation_note(store.job(job)))
        resumed_ledger=WorkLedger(store,job,fresh=True)
        denied=service.connector_permission(store.job(job),args)
        self.assertEqual(denied['behavior'],'deny')
        self.assertIn('기존 실행 기록',denied['message'])
        resumed_ledger.pause()
        # An unknown outcome discovered after offering is checked again at the press.
        service,store,job,run,_=self.stopped()
        self.assertTrue(service.offer_budget_continue(run))
        with store.db() as db:
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (job,'browser_open','failed',json.dumps({'effect':'unknown','code':'timeout'}),time.time()))
        self.assertFalse(self.press(service,job)['resumed'])
        self.assertEqual(store.job(job)['status'],'partial')
        self.assertFalse(service.offer_budget_continue(run))

    def test_no_offer_for_other_stops_or_without_progress(self):
        for code,progress in (('stopped',True),('work_stopped',True),('needs_setup',True),('input_required',True),
                              ('approval_required',True),('login_required',True),('attempt_budget',False),('effect_stop',True)):
            with self.subTest(code=code,progress=progress):
                service,store,job,run,_=self.stopped(code,progress)
                self.assertFalse(service.offer_budget_continue(run))
                self.assertEqual(service.budget_continue_offer(job),{})
        service,store,job,run,_=self.stopped()
        store.put(WORK_STOP_KEY,[job])
        self.assertFalse(service.offer_budget_continue(run))

    def test_expired_wrong_nonce_stopped_and_corrupt_ledger_fail_closed(self):
        for change in ('expiry','nonce','stop','corrupt','active'):
            with self.subTest(change=change):
                service,store,job,run,old=self.stopped()
                self.assertTrue(service.offer_budget_continue(run))
                row=service.budget_continue_offer(job)
                body={'work_id':job,'nonce':row['nonce']}
                if change=='expiry':store.put('budget_continue:'+job,{**row,'deadline':0})
                elif change=='nonce':body['nonce']='wrong'
                elif change=='stop':store.put(WORK_STOP_KEY,[job])
                elif change=='corrupt':store.put(WORK_LEDGER_KEY+':'+job,{'attempts':-1})
                else:store.put(WORK_LEDGER_KEY+':'+job,{**store.config(WORK_LEDGER_KEY+':'+job),'active':True})
                self.assertFalse(service.budget_continue_decision(body)['resumed'])
                self.assertEqual(store.job(job)['status'],'partial')

    def test_notification_binding_and_telegram_single_button(self):
        service,store,job,run,_=self.stopped(channel='telegram:g',chat_id=77)
        store.put('telegram',{'enabled':True,'generation':'g','user_id':77})
        self.assertTrue(service.offer_budget_continue(run))
        note=store.notification_of(job,'budget_continue')
        bound=notice_binding(service,note)
        self.assertEqual(notice_text(service,note,bound),bound['text'])
        markup=service.telegram_surface._notice_markup(note,bound)
        self.assertEqual(len(markup['inline_keyboard'][0]),1)
        self.assertEqual(markup['inline_keyboard'][0][0]['text'],msg('button.continue'))
        store.update_notification(note['id'],'sent',message_id=123,delivered=True)
        def tap(message_id=123):
            service.ingest_callback({'id':'budget-tap','from':{'id':77},
                'data':markup['inline_keyboard'][0][0]['callback_data'],
                'message':{'message_id':message_id,'chat':{'id':77,'type':'private'}}},'g')
        tap(124)
        self.assertEqual(store.job(job)['status'],'partial','the button must name the sent message')
        tap();tap()
        self.assertEqual(store.job(job)['status'],'queued')
        self.assertEqual(len([e for e in store.task_events(job) if e['tool']=='action_continuation' and e['status']=='queued']),1)
        self.assertIsNone(notice_binding(service,note))
        self.assertIsNone(service.task_progress(job)['selected']['budget_continue'])

    def test_reported_cli_usage_is_taken_from_the_existing_audit(self):
        service,store,job,run,_=self.stopped()
        with store.db() as db:
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (job,'subscription_engine','succeeded',json.dumps({'engine':'codex','continuation_read_only':True,
                        'usage':{'input_tokens':1234,'output_tokens':56},'cli_tool_calls':7,'num_turns':3,'cost_usd':0.25}),time.time()))
        self.assertTrue(service.offer_budget_continue(run))
        text=service.budget_continue_offer(job)['text']
        for value in ('1234','56','7','3','0.25'):self.assertIn(value,text)
        self.assertNotIn(msg('information_use.usage_not_reported'),text)
        self.assertIn('Continue?',service.task_progress(job)['selected']['budget_continue']['texts']['en'])

    def test_owner_needed_wins_even_when_a_budget_code_is_present(self):
        for wait in ('question','approval','login','setup','connection','unknown','unaccounted'):
            with self.subTest(wait=wait):
                service,store,job,run,_=self.stopped()
                if wait=='question':run.agency_report={'question':'Which source should I use?'}
                elif wait=='approval':run.approval_needed[0]=True
                elif wait=='login':run.waits_for_sign_in=True
                elif wait=='connection':
                    with store.db() as db:db.execute("UPDATE jobs SET status='awaiting_connection' WHERE id=?",(job,))
                else:
                    detail=({'code':'needs_setup','ran':False,'effect':'none'} if wait=='setup' else
                            {'effect':'unknown' if wait=='unknown' else 'changed'})
                    with store.db() as db:
                        db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                                   (job,'browser_open','failed' if wait!='unaccounted' else 'succeeded',json.dumps(detail),time.time()))
                self.assertFalse(service.offer_budget_continue(run))

    def test_eight_resumes_and_old_ledger_epoch_remain_bounded(self):
        service,store,job,run,old=self.stopped()
        row=store.config(WORK_LEDGER_KEY+':'+job)
        store.put(WORK_LEDGER_KEY+':'+job,{**row,'renewals':3})
        self.assertTrue(service.offer_budget_continue(run))
        self.assertTrue(self.press(service,job)['resumed'],'Continue has its own eight-resume cap')
        self.assertFalse(old.ledger.spend(200),'an old worker cannot spend the renewed allowance')
        fresh=service.work_budget(job)
        fresh.spend_attempt();fresh.ledger.pause()
        with store.db() as db:
            db.execute("UPDATE jobs SET status='partial' WHERE id=?",(job,))
            for index in range(7):
                db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                           (job,'action_continuation','queued',json.dumps({'resumed':True,'offer_digest':str(index)}),time.time()))
        self.assertFalse(service.offer_budget_continue(run))

    def test_progress_from_an_earlier_run_does_not_qualify_a_zero_progress_run(self):
        service,store,job,run,_=self.stopped()
        run.claimed_at=time.time()+1
        with store.db() as db:
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (job,'model','stopped',json.dumps({'code':'turn_budget'}),run.claimed_at+1))
        self.assertFalse(service.offer_budget_continue(run))

    def test_judged_owner_reply_resumes_the_open_offer_in_context(self):
        service,store,job,run,_=self.stopped(channel='telegram:g',chat_id=77)
        store.put('telegram',{'enabled':True,'generation':'g','user_id':77})
        self.assertTrue(service.offer_budget_continue(run))
        note=store.notification_of(job,'budget_continue')
        store.update_notification(note['id'],'sent',message_id=123,delivered=True)
        acts=service.owner_acts
        items=acts.open_items(store.job(job))
        self.assertEqual([item['kind'] for item in items],['budget-continue'])
        judge=mock.Mock(return_value={'item':items[0]['id'],'answer':'approve','rest':''})
        with mock.patch.object(service.decision_judge,'owner_act_answer',judge):
            answer=acts.read(store.job(job),'Carry on from that comparison.',target=123)
        self.assertEqual(judge.call_args.args[0],'Carry on from that comparison.')
        self.assertIsNotNone(answer)
        result=acts.settle(answer,store.job(job))
        self.assertEqual(result['text'],msg('budget_continue.resumed'))
        self.assertEqual(store.job(job)['status'],'queued')
        self.assertEqual(acts.open_items(store.job(job)),[])
