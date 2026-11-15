"""L1 fixture evidence only: real store/service, injected judgment and fake owner clock."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
from unittest.mock import patch

from personal_agent import entrusted_lines as el
from personal_agent import owner_model as om
from personal_agent import preparations as prep
from personal_agent import browser_session as bs
from personal_agent.conversation_handoff import ConversationJudgments
from personal_agent.decision import BinaryDecision, FixtureDecisionEngine, OUTCOME_DECIDED, StructuredDecision, fixture_confidence
from personal_agent.owner_forget import OwnerForget
from personal_agent.quickstart_service import UNATTENDED_APPROVAL_TEXT
from personal_agent.agent_runtime import MEMORY_OWNER, ToolError

from test_preparations import _Case


class _LinesCase(_Case):
    def setUp(self):
        super().setUp()
        self.helper=self.service.preparations_service
        # The owner's calendar zone, so owner-local days do not follow the test host's zone (CI runs in UTC).
        from personal_agent.current_context import TIMEZONE_CONFIG_KEY
        self.store.put(TIMEZONE_CONFIG_KEY,'Asia/Seoul')
        self.service.owner_model.clock=lambda:self.now
        self.service.owner_model.set_controls({'enabled':True,'dream':True})
        self.counter=0
        self.a=self.owner('owner-private-799-a',self.now-86400)
        self.b=self.owner('owner-private-799-b',self.now-60)

    def owner(self, text, at=None, **options):
        self.counter+=1
        work=self.store.enqueue(text,f'fixture799-{self.counter}',channel=f'telegram:gen-1',chat_id=77,
                                owner_typed=True,**options)
        with self.store.db() as db:
            db.execute("UPDATE jobs SET created=?,provider='compatible',model='fixture',status='succeeded',response='done',delivery='sent' WHERE id=?",
                       (self.now if at is None else at,work))
            db.execute("INSERT INTO messages(job_id,role,content,channel,created) VALUES (?,'user',?,?,?)",
                       (work,text,f'telegram:gen-1',self.now if at is None else at))
        return work

    def item(self, **changes):
        return {'action':'stand','line':'','expected_version':0,'task':'Continuing responsibility',
                'report_promise':'at the agreed time','open_matters':'','rules':['Read and report'],
                'drop_rules':[],'first':[{'kind':'prepare','goal':'Read and report the current result '+changes.get('task',''),
                'due':self.due_iso(3600),'recurrence':'daily','delivery':'send','title':'Continuing work',
                'offer':'I will report what changed.'}], 'sources':[self.a,self.b],'supporting':[],**changes}

    def stand(self, **changes):
        outcomes,calls=self.helper.apply_line_actions(self.b,[self.item(**changes)],self.service.decision_judge)
        self.assertIn('line_id',outcomes[0],outcomes)
        return self.service.lines.current(outcomes[0]['line_id'])

    def amend(self, brief, **changes):
        item=self.item(**{'action':'amend','line':brief['line_id'],'expected_version':brief['version'],'first':[],
                          'task':'','report_promise':'','sources':[self.b],**changes})
        return self.helper.apply_line_actions(self.b,[item],self.service.decision_judge)[0][0]

    def announce(self, brief):
        self.helper.line_tick(self.now)
        with self.store.db() as db:
            rows=db.execute('SELECT * FROM telegram_notifications WHERE kind LIKE ?',
                            (el.NOTICE_OPENED+brief['line_id']+':%',)).fetchall()
        self.assertEqual(len(rows),1)
        return dict(rows[0])

    def deliver(self, notice):
        # The real delivery path, not a forged sent receipt.
        with patch('personal_agent.telegram_store.time.time',return_value=self.now):
            self.assertTrue(self.service.deliver_notification())
        self.assertEqual(self.store.notification_of(notice['job_id'],notice['kind'])['state'],'sent')
        self.helper.line_tick(self.now)

    def work_rows(self, brief):
        return [row for row in self.helper.line_rows(brief['line_id']) if row['origin']!=prep.ORIGIN_LINE_ACCOUNT]

    def assert_refused(self, item, code):
        outcomes,_=self.helper.apply_line_actions(self.b,[item],self.service.decision_judge)
        self.assertEqual(outcomes,[{'reason':code}])

class Lines(_LinesCase):
    def test_two_days_stand_one_line_then_rows_only_after_sent_and_restart_duplicates_nothing(self):
        brief=self.stand()
        self.assertEqual(self.helper.line_rows(brief['line_id']),[])
        notice=self.announce(brief)
        self.assertEqual(self.helper.line_rows(brief['line_id']),[])
        self.deliver(notice)
        self.assertEqual(len(self.work_rows(brief)),1)
        self.assertEqual(len(self.helper.line_rows(brief['line_id'])),2)  # work plus periodic account
        self.assertTrue(all(row['accepted_by']==prep.ACCEPTED_DREAM for row in self.helper.line_rows(brief['line_id'])))
        self.assertTrue(all(row['channel']==prep.CHANNEL_TELEGRAM for row in self.helper.line_rows(brief['line_id'])))
        self.service=self.make_service();self.helper=self.service.preparations_service
        self.helper.line_tick(self.now+30)
        again=self.stand()
        self.assertEqual(again['line_id'],brief['line_id'])
        self.assertEqual(len(self.helper.line_rows(brief['line_id'])),2)
        from personal_agent.messages import msg
        self.assertEqual(self.service.lines.provenance(brief['line_id'])['evidence']['days'],2)
        self.assertEqual(self.helper.line_notice_text(notice),msg('lines.stood_announced',
                         task=brief['task'],promise=brief['report_promise'],days=2))

    def test_unknown_cancelled_and_work_mode_announcements_retry_next_day_without_rows(self):
        for state in ('unknown','cancelled'):
            with self.subTest(state=state):
                if self.service.lines.lines():
                    # Separate task and evidence for the next fixture.
                    self.now+=86400
                    self.a=self.owner('new-a',self.now-86400);self.b=self.owner('new-b',self.now-60)
                brief=self.stand(task='Responsibility '+state)
                notice=self.announce(brief)
                with self.store.db() as db:db.execute('UPDATE telegram_notifications SET state=? WHERE id=?',(state,notice['id']))
                self.helper.line_tick(self.now)
                self.assertEqual(self.helper.line_rows(brief['line_id']),[])
                self.now+=86400
                self.helper.line_tick(self.now)
                with self.store.db() as db:
                    rows=db.execute('SELECT * FROM telegram_notifications WHERE kind LIKE ?',
                                    (el.NOTICE_OPENED+brief['line_id']+':%',)).fetchall()
                self.assertEqual(len(rows),1 if state=='unknown' else 2)
                if state=='unknown':
                    # Only delivery's reconciliation API may re-offer an unknown row. L1 never writes its state.
                    with self.store.db() as db:self.store.telegram.reoffer_unknown(notice['id'],db)
                self.deliver(dict(rows[-1]))
        # No Telegram notice surface (as in work mode) must not become a web receipt.
        self.now+=86400;self.a=self.owner('mode-a',self.now-86400);self.b=self.owner('mode-b',self.now-60)
        brief=self.stand(task='Mode responsibility')
        with patch.object(self.service,'queue_notification',return_value=None):self.helper.line_tick(self.now)
        self.assertFalse(self.helper.line_announced(brief['line_id']))
        self.assertEqual(self.helper.line_rows(brief['line_id']),[])

    def test_web_only_announcement_is_a_conversation_receipt_and_not_owner_last_work(self):
        with self.store.db() as db:db.execute("UPDATE jobs SET channel='web',chat_id=NULL,owner_words=1 WHERE id=?",(self.b,))
        brief=self.stand()
        self.helper.line_tick(self.now)
        self.assertTrue(self.helper.line_announced(brief['line_id']))
        self.assertEqual(self.helper.owner_last_work()['id'],self.b)
        self.assertEqual(len(self.work_rows(brief)),1)

    def test_waiting_line_hidden_from_index_but_list_shows_waiting_and_withdrawal_is_not_stop(self):
        brief=self.stand()
        self.assertNotIn(brief['line_id'],self.helper.lines_index(self.store.job(self.b)))
        self.assertEqual(self.helper.line_entry(brief)['state'],'waiting')
        old_sources=[self.a,self.b]
        self.now+=7*86400
        self.helper.line_tick(self.now)
        stopped=self.service.lines.current(brief['line_id'])
        self.assertEqual((stopped['state'],stopped['change']),('stopped','withdrawn'))
        self.assertEqual(self.helper.line_rows(brief['line_id']),[])
        self.a=self.owner('fresh-a',self.now-86400);self.b=self.owner('fresh-b',self.now-60)
        self.assert_refused(self.item(sources=old_sources),'line_evidence_held')
        self.assertNotEqual(self.stand()['line_id'],brief['line_id'])

    def test_two_lines_announced_on_consecutive_days(self):
        first=self.stand()
        self.a=self.owner('another-a',self.now-86400);self.b=self.owner('another-b',self.now-30)
        second=self.stand(task='Another responsibility')
        notice=self.announce(first)
        self.deliver(notice)
        self.assertFalse(self.helper.line_announced(second['line_id']))
        self.now+=86400
        self.deliver(self.announce(second))
        self.assertEqual(len(self.work_rows(second)),1)

    def test_queued_notice_blocks_another_attempt_and_offer_hours_and_daily_cap_hold(self):
        brief=self.stand()
        first=self.announce(brief)
        self.now+=86400
        self.helper.line_tick(self.now)
        self.assertEqual(self.store.notification_of(first['job_id'],first['kind'])['state'],'queued')
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM tool_events WHERE tool='line_announced'").fetchone()[0],1)

    def test_announcement_evidence_precedes_queue_and_a_crash_keeps_daily_cap(self):
        brief=self.stand()
        def crash(*args,**kwargs):
            with self.store.db() as db:
                rows=db.execute("SELECT detail FROM tool_events WHERE tool='line_announced'").fetchall()
            self.assertEqual(json.loads(rows[0]['detail'])['line_id'],brief['line_id'])
            raise RuntimeError('fixture crash before queue')
        with patch.object(self.service,'queue_notification',side_effect=crash):
            with self.assertRaises(RuntimeError):self.helper.line_tick(self.now)
        self.helper.line_tick(self.now)
        self.assertFalse(self.helper.line_announced(brief['line_id']))
        self.assertEqual(self.helper.line_rows(brief['line_id']),[])
        self.now+=86400
        notice=self.announce(brief)
        self.deliver(notice)
        self.assertEqual(len(self.work_rows(brief)),1)

    def test_announcements_wait_for_owner_local_offer_hours(self):
        from datetime import datetime
        brief=self.stand()
        self.now=datetime.fromtimestamp(self.now,prep.zone('Asia/Seoul')).replace(hour=3).timestamp()+86400
        self.helper.line_tick(self.now)
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM tool_events WHERE tool='line_announced'").fetchone()[0],0)
        self.assertEqual(self.helper.line_rows(brief['line_id']),[])
        self.now+=6*3600
        self.deliver(self.announce(brief))

    def test_cited_forged_run_retry_continuation_builtin_forgotten_and_unmarked_refs_refuse(self):
        from personal_agent.conversation_handoff import FOLLOWUP_RETRY
        cases=[('owner_typed',None),('provider','builtin'),('request_key','preparation:fake:123'),
               ('request_key','loc:fake'),('relation_kind',FOLLOWUP_RETRY),('channel','web')]
        original=self.store.job(self.a)
        for field,value in cases:
            with self.subTest(field=field,value=value):
                with self.store.db() as db:db.execute(f'UPDATE jobs SET {field}=? WHERE id=?',(value,self.a))
                self.assert_refused(self.item(),'line_evidence_sources')
                with self.store.db() as db:db.execute(f'UPDATE jobs SET {field}=? WHERE id=?',(original[field],self.a))
        self.assert_refused(self.item(sources=['forged',self.b]),'line_evidence_sources')
        # Forgotten owner turn, using the real forget store.
        with self.store.db() as db:
            row=db.execute("SELECT id FROM messages WHERE job_id=? AND role='user'",(self.a,)).fetchone()
        OwnerForget(self.store).forget(MEMORY_OWNER,'message:'+str(row['id']))
        self.assert_refused(self.item(),'line_evidence_sources')

    def test_one_local_day_out_of_window_and_proposals_alone_refuse(self):
        with self.store.db() as db:db.execute('UPDATE jobs SET created=? WHERE id=?',(self.now-120,self.a))
        self.assert_refused(self.item(),'line_evidence_one_day')
        with self.store.db() as db:db.execute('UPDATE jobs SET created=? WHERE id=?',(self.now-15*86400,self.a))
        self.assert_refused(self.item(),'line_evidence_sources')
        self.assert_refused(self.item(sources=[],supporting=['proposal']),'line_evidence_sources')

    def test_distinct_days_use_owner_zone_even_across_a_single_utc_day(self):
        from test_preparations import SEOUL_MIDNIGHT
        self.now=SEOUL_MIDNIGHT+3600
        with self.store.db() as db:
            db.execute('UPDATE jobs SET created=? WHERE id=?',(SEOUL_MIDNIGHT-1,self.a))
            db.execute('UPDATE jobs SET created=? WHERE id=?',(SEOUL_MIDNIGHT+1,self.b))
        self.assertEqual(self.helper.line_evidence([self.a,self.b],self.now)['days'],2)

    def test_first_spec_must_recur_without_window_and_waiting_cap_is_three(self):
        bad=self.item()
        bad['first'][0]['recurrence']='once'
        self.assert_refused(bad,'invalid_recurrence')
        bad=self.item();bad['first'][0]['delivery']='keep'
        self.assert_refused(bad,'invalid_recurrence')
        for index in range(3):
            self.a=self.owner('a'+str(index),self.now-86400);self.b=self.owner('b'+str(index),self.now-30)
            self.stand(task='Responsibility '+str(index))
        self.a=self.owner('overflow-a',self.now-86400);self.b=self.owner('overflow-b',self.now-30)
        self.assert_refused(self.item(task='One more responsibility'),'line_waiting_max')

    def test_refs_not_shown_and_bad_shapes_refused_and_empty_lines_do_nothing(self):
        item=self.item(sources=['t1','r1'])
        self.assertEqual(om.validate_line_actions([item],{'t1':self.b},{})[1][0]['reason'],'line_evidence_sources')
        self.assertEqual(len(om.validate_line_actions([item],{'t1':self.b},{'r1':self.a})[0]),1)
        self.assertEqual(om.validate_line_actions([],{},{}),([],[]))
        self.assertTrue(om.dream_shape({'proposals':[],'notes':'','change':'','notes_sources':[],'diary':''}))
        self.assertEqual(self.helper.apply_line_actions(self.b,[],self.service.decision_judge),([],0))
        self.assertEqual(self.service.lines.lines(),[])

    def test_input_uses_code_days_expired_is_not_declined_and_only_shown_refs_resolve(self):
        for state in (prep.STATE_PROPOSED,prep.STATE_EXPIRED,prep.STATE_CANCELLED):
            row=self.service.preparations.create(kind='prepare',goal='Preparation '+state,due_at=self.now+600,
                 timezone='Asia/Seoul',recurrence=None,channel='web',created_from=self.b,state=prep.STATE_PROPOSED,
                 accepted_by=prep.ACCEPTED_DREAM,origin=prep.ORIGIN_DREAM)
            with self.store.db() as db:db.execute('UPDATE preparations SET state=? WHERE id=?',(state,row['id']))
        text,refs=self.helper.line_judgment_input(self.now,refs=True,exclude=[self.b])
        data=json.loads(text)
        self.assertEqual(set(refs.values()),{self.a})
        self.assertEqual({item['state'] for item in data['proposals']},{'waiting','expired','declined'})
        self.assertLessEqual(len(text),om.LINE_INPUT_CHARS)
        self.assertEqual(self.helper.line_evidence([self.a,self.b],self.now)['days'],2)

    def test_inferred_drop_of_owner_rule_and_task_after_owner_revision_refused(self):
        brief=self.stand()
        _,changed=self.service.lines.write(brief['line_id'],self.b,1,'rule',lambda b:{**b,'rules':[
            *b['rules'],{'text':'Owner constraint','from':self.b,'at':self.now}]})
        self.assertEqual(self.amend(changed,drop_rules=[2]),{'reason':'line_owner_rule'})
        self.assertEqual(self.amend(changed,task='Changed responsibility'),{'reason':'line_change_needs_owner'})
        outcome=self.amend(changed,drop_rules=[1],rules=['Another inference'])
        self.assertEqual(outcome['version'],3)
        after=self.service.lines.current(brief['line_id'])
        self.assertEqual(after['rules'][0],changed['rules'][1])
        self.assertEqual(after['rules'][1]['basis'],'inferred')

    def test_amend_owner_opened_money_marked_and_stale_version_refused(self):
        opened=self.service.lines.open(self.b,task='Owner opened',report_promise='afterwards')
        self.assertEqual(self.amend(opened),{'reason':'line_change_needs_owner'})
        brief=self.stand()
        with patch.object(self.helper,'line_touches_money',return_value=True):
            self.assertEqual(self.amend(brief),{'reason':'line_change_needs_owner'})
        changed=self.amend(brief,open_matters='An open matter')
        self.assertEqual(changed['version'],2)
        self.assertEqual(self.amend(brief,open_matters='Stale'),{'reason':'line_version_conflict'})

    def test_exact_stopped_task_refuses_without_model_and_same_or_unavailable_fail_closed(self):
        brief=self.stand();self.helper.stop_line(brief['line_id'],self.b)
        self.a=self.owner('fresh-a',self.now-86400);self.b=self.owner('fresh-b',self.now-30)
        with patch.object(self.service.decision_judge,'declined_preparation_goal',side_effect=AssertionError('no call')):
            self.assert_refused(self.item(),'line_stopped_before')
        for answer in (None,True):
            engine=FixtureDecisionEngine(judge=lambda *args:None if answer is None else BinaryDecision(OUTCOME_DECIDED,answer,fixture_confidence()))
            result,calls=self.helper.apply_line_actions(self.b,[self.item(task='Different wording')],ConversationJudgments(engine))
            self.assertEqual(result,[{'reason':'line_stopped_before'}]);self.assertEqual(calls,1)
        self.assertEqual(self.amend(self.service.lines.current(brief['line_id'])),{'reason':'line_stopped'})

    def test_stopped_identity_judgment_is_counted_even_when_brief_validation_refuses(self):
        brief=self.stand();self.helper.stop_line(brief['line_id'],self.b)
        self.a=self.owner('fresh-a',self.now-86400);self.b=self.owner('fresh-b',self.now-30)
        no=ConversationJudgments(FixtureDecisionEngine(judge=lambda *args:BinaryDecision(OUTCOME_DECIDED,False,fixture_confidence())))
        result,calls=self.helper.apply_line_actions(self.b,[self.item(task='Different wording',rules=['x'*301])],no)
        self.assertEqual(result,[{'reason':'line_invalid_rules'}])
        self.assertEqual(calls,1)

    def test_evidence_spent_after_stop_and_owner_restart_reopens_and_locks(self):
        brief=self.stand();self.helper.stop_line(brief['line_id'],self.b)
        no=ConversationJudgments(FixtureDecisionEngine(judge=lambda *args:BinaryDecision(OUTCOME_DECIDED,False,fixture_confidence())))
        result,_=self.helper.apply_line_actions(self.b,[self.item(task='Different wording')],no)
        self.assertEqual(result,[{'reason':'line_evidence_held'}])
        after=self.helper.line_handler(self.store.job(self.b))({'action':'restart','line':brief['line_id']})
        self.assertEqual(after['state'],'active')
        self.assertTrue(self.service.lines.owner_written(brief['line_id']))
        self.assertEqual(len(self.helper.line_rows(brief['line_id'])),2) # owner restart is its announcement

    def test_forgetting_a_line_removes_its_stop_history_without_reusing_its_identity(self):
        brief=self.stand();self.helper.stop_line(brief['line_id'],self.b)
        OwnerForget(self.store).forget(MEMORY_OWNER,'line:'+brief['line_id'])
        self.assertIsNone(self.service.lines.current(brief['line_id']))
        after=self.stand()
        self.assertNotEqual(after['line_id'],brief['line_id'])

    def test_silence_ten_days_leaves_announced_line_active_and_provenance_unchanged(self):
        brief=self.stand();self.deliver(self.announce(brief))
        before=self.service.lines.provenance(brief['line_id'])
        self.now+=10*86400
        self.helper.line_tick(self.now)
        self.assertEqual(self.service.lines.current(brief['line_id'])['state'],'active')
        self.assertEqual(self.service.lines.provenance(brief['line_id']),before)
        self.assertEqual(len(self.work_rows(brief)),1)

    def test_day_plan_links_only_announced_stood_lines_and_stopped_link_refused(self):
        brief=self.stand()
        with self.assertRaises(ToolError):self.helper.scheduled_line(brief['line_id'],None,accepted=False,planning=True)
        with self.assertRaises(ToolError):self.helper.scheduled_line(brief['line_id'],None,accepted=True,planning=False)
        self.deliver(self.announce(brief))
        self.assertEqual(self.helper.scheduled_line(brief['line_id'],None,accepted=False,planning=True),brief['line_id'])
        opened=self.service.lines.open(self.b,task='Owner line',report_promise='later')
        with self.assertRaises(Exception):self.helper.scheduled_line(opened['line_id'],None,accepted=False,planning=True)
        self.helper.stop_line(brief['line_id'],self.b)
        result,_=self.helper.suggest_preparations(self.b,[{**self.item()['first'][0],'goal':'A fresh unlinked report','line':brief['line_id']}])
        self.assertIn('preparation_id',result[0])
        self.assertIsNone(self.service.preparations.get(result[0]['preparation_id'])['line_id'])

    def authority_state(self):
        with self.store.db() as db:
            names=[row['name'] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                   if any(word in row['name'] for word in ('grant','trust','approval'))]
            return {**{name:[tuple(row) for row in db.execute('SELECT * FROM '+name)] for name in names},
                    'config_bytes':[tuple(row) for row in db.execute('SELECT * FROM config ORDER BY key')]}

    def test_stand_amend_link_preserve_grants_trust_approvals_byte_equal(self):
        from personal_agent import trust_record
        trust_record.set_money(self.store,trust_record.connection('codex','owner-mcp','synthetic'),True)
        before=self.authority_state()
        brief=self.stand()
        self.amend(brief,open_matters='Open question')
        self.deliver(self.announce(self.service.lines.current(brief['line_id'])))
        args={**self.item()['first'][0],'goal':'A linked report','line':brief['line_id']}
        result,_=self.helper.suggest_preparations(self.b,[args])
        self.assertIn('preparation_id',result[0])
        self.assertEqual(self.service.preparations.get(result[0]['preparation_id'])['line_id'],brief['line_id'])
        self.assertEqual(before,self.authority_state())

    def started_stood_rows(self):
        brief=self.stand();self.deliver(self.announce(brief))
        rows=self.helper.line_rows(brief['line_id'])
        self.assertEqual(len(rows),2)
        self.assertEqual({row['origin'] for row in rows},{prep.ORIGIN_DREAM,prep.ORIGIN_LINE_ACCOUNT})
        for row in rows:
            self.assertEqual(row['accepted_by'],prep.ACCEPTED_DREAM)
            self.now=row['due_at']+1
            work=self.service.preparations.start(row,channel=self.store.job(self.b)['channel'],chat_id=77,now=self.now)
            self.assertIsNotNone(work)
            with self.store.db() as db:db.execute("UPDATE jobs SET status='running' WHERE id=?",(work,))
            job=self.store.job(work)
            self.assertTrue(self.service.self_set_run(job))
            yield row,job

    def test_stood_run_payment_autonomy_on_cannot_pay(self):
        from test_payment_autonomy_662 import Mode
        Mode.switch(self,True)
        for row,job in self.started_stood_rows():
            with self.subTest(origin=row['origin']):
                binding=bs.step_binding(job['id'],'api_request','https://example.test/','pay',argument='',state='s')
                self.assertIsNone(self.service._authorize_payment_autonomy(job,binding,'pay'))
                with self.assertRaises(ToolError) as caught:
                    self.service.browser_approvals_for(job).self_set_write(binding,None,'POST','https://example.test/',effect='payment')
                self.assertEqual(caught.exception.code,'unattended_run')
                self.assertIsNone(self.service._browser_request(job['id']))

    def test_stood_runs_payment_approval_step_denied_without_prompt(self):
        # #875 permits non-payment self-set-write prompts, never payment prompts.
        for row,job in self.started_stood_rows():
            with self.subTest(origin=row['origin']):
                binding=bs.step_binding(job['id'],'api_request','https://example.test/charge','POST /charge',
                                       argument='{}',state='api-payment-v2|slot@1|payment')
                with self.assertRaises(ToolError) as caught:
                    self.service.browser_approvals_for(job).request(binding,'POST /charge')
                self.assertEqual((caught.exception.code,str(caught.exception)),('unattended_run',UNATTENDED_APPROVAL_TEXT))
                self.assertIsNone(self.service._browser_request(job['id']))
                self.assertIsNone(self.store.notification_of(job['id'],'browser_approval_needed'))

    def test_stood_rows_irreversible_or_unknown_write_waits_then_runs_once(self):
        from personal_agent.api_requests import ApiRequests,ApiError,save_slot
        save_slot(self.store,'notes',['notes.test'],'fixture-notes-key-799',money=False)
        for row,job in self.started_stood_rows():
            sent=[]
            api=ApiRequests(self.store,job['id'],approvals=self.service.browser_approvals_for(job),
                            transport=lambda *args:sent.append(args) or (200,{},b'{}'))
            for effect in ('irreversible',None):
                with self.subTest(origin=row['origin'],effect=effect), patch.object(self.service.decision_judge,'connector_effect',return_value=effect):
                    args={'slot':'notes','url':'https://notes.test/items','method':'POST',
                          'body':json.dumps({'note':str(effect)}),'effect':'mutate'}
                    before=len(sent)
                    with self.assertRaises(ApiError) as caught:api.call(args)
                    self.assertEqual(caught.exception.code,'approval_required')
                    self.assertEqual(len(sent),before)
                    request=self.service._browser_request(job['id'])
                    self.assertEqual((request['action'],request['state']),('self_set_write','requested'))
                    self.assertEqual(request['preparation']['id'],row['id'])
                    self.assertIsNotNone(self.store.notification_of(job['id'],'browser_approval_needed'))
                    with self.store.db() as db:db.execute("UPDATE jobs SET status='partial',response='Waiting',delivery='none' WHERE id=?",(job['id'],))
                    approved=self.service._decide_browser_step(job['id'],True,request['offer_digest'])
                    self.assertTrue(approved['approved'])
                    self.assertTrue(approved['resumed'])
                    with self.store.db() as db:db.execute("UPDATE jobs SET status='running' WHERE id=?",(job['id'],))
                    api.call(args)
                    self.assertEqual(len(sent),before+1)
                    with self.assertRaises(ApiError):api.call(args)
                    self.assertEqual(len(sent),before+1)

    def test_stood_rows_reversible_or_owner_allowed_connection_needs_no_prompt(self):
        from personal_agent.api_requests import ApiRequests,save_slot
        from personal_agent.self_set_writes import allowance,set_allowance
        save_slot(self.store,'notes',['notes.test'],'fixture-notes-key-799',money=False)
        for row,job in self.started_stood_rows():
            sent=[]
            api=ApiRequests(self.store,job['id'],approvals=self.service.browser_approvals_for(job),
                            transport=lambda *args:sent.append(args) or (200,{},b'{}'))
            for effect in ('reversible','irreversible'):
                with self.subTest(origin=row['origin'],effect=effect), patch.object(self.service.decision_judge,'connector_effect',return_value=effect):
                    if effect=='irreversible':set_allowance(self.service,'slot:notes',True,allowance(self.service,'slot:notes')['revision'],'owner')
                    api.call({'slot':'notes','url':'https://notes.test/items','method':'POST',
                              'body':json.dumps({'note':effect}),'effect':'mutate'})
                    self.assertIsNone(self.service._browser_request(job['id']))
                    self.assertIsNone(self.store.notification_of(job['id'],'browser_approval_needed'))
                    if effect=='irreversible':set_allowance(self.service,'slot:notes',False,allowance(self.service,'slot:notes')['revision'],'owner')
            self.assertEqual(len(sent),2)

    def test_line_and_suggestion_run_cannot_stand_or_amend(self):
        brief=self.stand();self.deliver(self.announce(brief))
        row=self.work_rows(brief)[0]
        work=self.store.enqueue('run',prep.request_key(row['id'],row['due_at']),owner_typed=True)
        for action in ('stand','amend'):
            item=self.item(action=action,line=brief['line_id'] if action=='amend' else '',first=[])
            result,_=self.helper.apply_line_actions(work,[item],self.service.decision_judge)
            self.assertEqual(result,[{'reason':'unattended_run'}])

    def test_read_path_records_use_read_in_answers(self):
        brief=self.stand();self.deliver(self.announce(brief))
        self.helper.line_handler(self.store.job(self.b))({'action':'read','line':brief['line_id']})
        text,_=self.helper.line_judgment_input(self.now,refs=False)
        self.assertEqual(json.loads(text)['lines'][0]['use']['read_in_answers'],1)
        read=self.helper.line_read
        def changed_after_read(line_id):
            result=read(line_id)
            self.service.lines.write(line_id,self.b,1,'revised',lambda b:{**b,'open_matters':'Updated after the read'})
            return result
        with patch.object(self.helper,'line_read',side_effect=changed_after_read):
            self.helper.line_handler(self.store.job(self.b))({'action':'read','line':brief['line_id']})
        self.assertEqual(self.helper.line_version_read(self.b),(brief['line_id'],1))
        self.assertEqual(self.service.lines.current(brief['line_id'])['version'],2)


    def test_nightly_judgment_receives_both_inputs_and_applies_only_validated_lines(self):
        contexts=[]
        def structured(context,question,schema):
            if context.purpose!=om.DREAM_PURPOSE:return None
            contexts.append(context)
            return StructuredDecision(OUTCOME_DECIDED,{'proposals':[],'notes':'','change':'','notes_sources':[],
                'diary':'','suggestions':[],'lines':[self.item(sources=['t1','r1'])]},fixture_confidence())
        self.service.use_decision_engine(FixtureDecisionEngine(structured=structured))
        self.service._owner_model_dream({'job_id':self.b,'reason':str(self.now-3600)},self.store.job(self.b),self.now)
        self.assertEqual(len(contexts),1)
        self.assertIn('owner-private-799-b',contexts[0].facts['turns'])
        self.assertIn('owner-private-799-a',contexts[0].facts['lines'])
        [brief]=self.service.lines.lines()
        self.assertEqual(self.helper.line_rows(brief['line_id']),[])
        self.assertEqual(self.service.lines.provenance(brief['line_id'])['evidence']['days'],2)
        with self.store.db() as db:
            record=json.loads(db.execute('SELECT detail FROM tool_events WHERE tool=?',(om.DREAM_EVENT_TOOL,)).fetchone()[0])
        self.assertEqual(record['lines']['kept'][0]['line_id'],brief['line_id'])

    def test_replay_uses_backup_one_judgment_per_night_and_stdout_contains_no_owner_text(self):
        path=Path(__file__).resolve().parents[1]/'scripts/replay_lines_799.py'
        spec=importlib.util.spec_from_file_location('replay799',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        calls=[]
        def structured(context,question,schema):
            calls.append(context)
            block=json.loads(context.facts['lines'])
            refs=[item['ref'] for item in block['requests']]
            refs+=['t1'] if '[t1]' in context.facts['turns'] else []
            item=self.item(sources=refs[:2])
            return StructuredDecision(OUTCOME_DECIDED,{'proposals':[],'notes':'','change':'','diary':'','notes_sources':[],
                                                       'lines':[item],'suggestions':[]},fixture_confidence())
        with self.store.db() as db:before=[tuple(row) for row in db.execute('SELECT * FROM jobs')]
        out=io.StringIO()
        with contextlib.redirect_stdout(out):
            result=module.replay(self.store.path,nights=2,now=self.now+86400,
                                 judgment_transport=FixtureDecisionEngine(structured=structured))
            print(json.dumps(result))
        self.assertEqual(len(calls),2)
        self.assertEqual({context.purpose for context in calls},{om.DREAM_PURPOSE})
        self.assertNotIn('owner-private',out.getvalue())
        self.assertNotIn('Continuing responsibility',out.getvalue())
        self.assertIn('stand',result['counts'])
        with self.store.db() as db:self.assertEqual(before,[tuple(row) for row in db.execute('SELECT * FROM jobs')])
        with patch.object(module,'replay',side_effect=lambda *a,**kw:(print('owner-private-leak'),result)[1]):
            with contextlib.redirect_stdout(out):self.assertEqual(module.main(['--store',str(self.root),'--nights','1']),0)
        self.assertNotIn('leak',out.getvalue())


class Round2Tests(_LinesCase):
    """Review regressions through existing stores, injected transports and the owner clock."""

    def dream_with(self, payload, *, fail_input=False):
        contexts=[]
        def structured(context,question,schema):
            if context.purpose!=om.DREAM_PURPOSE:return None
            contexts.append(context)
            data=payload(context) if callable(payload) else payload
            return StructuredDecision(OUTCOME_DECIDED,{'proposals':[],'notes':'','change':'','notes_sources':[],
                                     'diary':'','suggestions':[],**data},fixture_confidence())
        self.service.use_decision_engine(FixtureDecisionEngine(structured=structured))
        if fail_input:
            with patch.object(self.helper,'line_judgment_input',side_effect=RuntimeError('fixture input failure')):
                with self.assertLogs(level='WARNING') as logged:
                    self.service._owner_model_dream({'job_id':self.b,'reason':str(self.now-3600)},self.store.job(self.b),self.now)
            self.assertTrue(any('look-back lines input unavailable' in line for line in logged.output))
        else:
            self.service._owner_model_dream({'job_id':self.b,'reason':str(self.now-3600)},self.store.job(self.b),self.now)
        return contexts

    def test_stood_rows_have_only_the_announcement_and_preserve_the_ordinary_offer(self):
        from personal_agent.quickstart_service import PREPARED_IDS_PREFIX
        brief=self.stand();self.deliver(self.announce(brief))
        self.script=[{'content':'Acknowledged.'}]
        owner_work=self.receive('An independent owner request')
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM telegram_notifications WHERE kind='preparation_scheduled'").fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM telegram_notifications WHERE kind LIKE ?',
                                       (el.NOTICE_OPENED+'%',)).fetchone()[0],1)
        self.assertIsNone(self.service.preparations.last_suggestion_offered())
        args={**self.item()['first'][0],'goal':'Another read for the line','recurrence':om.SUGGESTION_ONCE,'line':brief['line_id']}
        linked,_=self.helper.suggest_preparations(owner_work,[args])
        self.assertIn('preparation_id',linked[0])
        self.assertFalse(self.service.preparations.has_unoffered_one_off())
        # A receipt written by the old double-notice bug must not spend today's ordinary budget either.
        self.service.preparations.mark_offered(self.helper.line_rows(brief['line_id']),self.now)
        self.assertIsNone(self.service.preparations.last_suggestion_offered())
        ordinary,_=self.helper.suggest_preparations(owner_work,[{**args,'goal':'An ordinary preparation','line':''}])
        ordinary_id=ordinary[0]['preparation_id']
        self.assertTrue(self.service.preparations.has_unoffered_one_off())
        self.script=[{'content':'Acknowledged again.'}]
        self.receive('Another independent request')
        note=self.store.notification_of(owner_work,'preparation_scheduled')
        self.assertIsNotNone(note)
        self.assertEqual(note['fingerprint'],PREPARED_IDS_PREFIX+ordinary_id)
        self.assertEqual(self.service.preparations.last_suggestion_offered(),self.now)

    def test_cancelled_stood_account_is_never_recreated_by_ticks_or_restart(self):
        brief=self.stand();self.deliver(self.announce(brief))
        account=self.helper.line_account_row(brief['line_id'])
        self.service.cancel_preparation(account['id'])
        ids={row['id'] for row in self.helper.line_rows(brief['line_id'])}
        self.service.run_due_preparation(self.now+60)
        self.service=self.make_service();self.helper=self.service.preparations_service
        self.helper.line_tick(self.now+120)
        self.assertEqual({row['id'] for row in self.helper.line_rows(brief['line_id'])},ids)
        self.assertEqual(self.service.preparations.get(account['id'])['state'],prep.STATE_CANCELLED)
        self.assertIsNone(self.helper.line_account_row(brief['line_id']))

    def test_cancelled_stood_work_is_never_recreated_by_ticks(self):
        brief=self.stand();self.deliver(self.announce(brief))
        work=self.work_rows(brief)[0]
        self.service.cancel_preparation(work['id'])
        ids={row['id'] for row in self.helper.line_rows(brief['line_id'])}
        for offset in (60,120,86400):self.helper.line_tick(self.now+offset)
        self.assertEqual({row['id'] for row in self.helper.line_rows(brief['line_id'])},ids)
        self.assertEqual(self.service.preparations.get(work['id'])['state'],prep.STATE_CANCELLED)

    def test_owner_restart_announces_withdrawn_line_and_starts_rows_once(self):
        brief=self.stand();self.now+=7*86400
        self.helper.line_tick(self.now)
        self.assertEqual(self.service.lines.current(brief['line_id'])['change'],'withdrawn')
        work=self.owner('Restart this responsibility',self.now)
        self.helper.line_handler(self.store.job(work))({'action':'restart','line':brief['line_id']})
        self.assertTrue(self.helper.line_announced(brief['line_id']))
        rows=self.helper.line_rows(brief['line_id']);ids={row['id'] for row in rows}
        self.assertEqual(len(rows),2)
        self.assertTrue(all(row['accepted_by']==prep.ACCEPTED_DREAM for row in rows))
        self.service=self.make_service();self.helper=self.service.preparations_service
        self.helper.line_tick(self.now+60);self.helper.line_tick(self.now+8*86400)
        self.assertEqual(self.service.lines.current(brief['line_id'])['state'],el.STATE_ACTIVE)
        self.assertEqual({row['id'] for row in self.helper.line_rows(brief['line_id'])},ids)
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM telegram_notifications WHERE kind LIKE ?',
                                       (el.NOTICE_OPENED+'%',)).fetchone()[0],0)

    def test_inferred_amend_keeps_empty_open_matters_and_locks_owner_open_matters(self):
        brief=self.stand(open_matters='Still unresolved')
        changed=self.helper.amend_stood_line(self.b,self.item(action='amend',line=brief['line_id'],expected_version=1,
                          task='',report_promise='',open_matters='',first=[],sources=[self.b],rules=['An inferred rule']))
        self.assertEqual(changed['open_matters'],'Still unresolved')
        _before,owned=self.service.lines.write(brief['line_id'],self.b,changed['version'],'revised',
                                            lambda b:{**b,'open_matters':'Owner open matter'})
        item=self.item(action='amend',line=brief['line_id'],expected_version=owned['version'],task='',report_promise='',
                       open_matters='Replace the owner matter',first=[],sources=[self.b])
        out,_=self.helper.apply_line_actions(self.b,[item],self.service.decision_judge)
        self.assertEqual(out,[{'reason':'line_change_needs_owner'}])
        item['open_matters']=''
        out,_=self.helper.apply_line_actions(self.b,[item],self.service.decision_judge)
        self.assertIn('line_id',out[0])
        self.assertEqual(self.service.lines.current(brief['line_id'])['open_matters'],'Owner open matter')

    def test_supporting_ids_are_limited_to_the_bounded_proposals_actually_shown(self):
        ids=[]
        for index in range(15):
            row=self.service.preparations.create(kind='prepare',goal=f'Prior proposal {index}',title=f'Proposal {index}',
                  due_at=self.now+600,timezone='Asia/Seoul',recurrence='daily',channel='web',created_from=self.a,
                  state=prep.STATE_PROPOSED,accepted_by=prep.ACCEPTED_DREAM,origin=prep.ORIGIN_DREAM)
            ids.append(row['id'])
        shown=[]
        def payload(context):
            block=json.loads(context.facts['lines']);shown.extend(item['id'] for item in block['proposals'])
            self.assertTrue(shown);self.assertTrue(set(ids)-set(shown))
            return {'lines':[self.item(sources=['t1','r1'],supporting=[*ids,'arbitrary owner text'])]}
        with patch.object(om,'LINE_INPUT_CHARS',2000):self.dream_with(payload)
        [brief]=self.service.lines.lines()
        self.assertEqual(set(self.service.lines.provenance(brief['line_id'])['evidence']['supporting']),set(shown))

    def test_all_outstanding_announcement_states_hold_the_global_daily_queue(self):
        first=self.stand();notice=self.announce(first)
        self.a=self.owner('other-a',self.now-86400);self.b=self.owner('other-b',self.now-30)
        second=self.stand(task='Second responsibility')
        self.now+=86400
        for state in ('queued','sending','unknown'):
            with self.subTest(state=state):
                self.store.update_notification(notice['id'],state)
                self.helper.line_tick(self.now)
                with self.store.db() as db:
                    self.assertEqual(db.execute('SELECT count(*) FROM telegram_notifications WHERE kind LIKE ?',
                                               (el.NOTICE_OPENED+'%',)).fetchone()[0],1)
                self.assertEqual(self.helper.line_rows(second['line_id']),[])
        # Reconciliation belongs to delivery: L1 observes the same row after its re-offer.
        with self.store.db() as db:self.assertTrue(self.store.telegram.reoffer_unknown(notice['id'],db))
        self.deliver(notice)
        self.assertFalse(self.helper.line_announced(second['line_id']))
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM telegram_notifications WHERE kind LIKE ?',
                                       (el.NOTICE_OPENED+'%',)).fetchone()[0],1)
        self.now+=86400
        self.deliver(self.announce(second))
        self.assertEqual(len(self.work_rows(second)),1)

    def test_look_back_input_failure_logs_but_still_writes_the_daily_record(self):
        contexts=self.dream_with({'lines':[],'diary':'The owner asked and received a result.'},fail_input=True)
        self.assertEqual(len(contexts),1)
        self.assertEqual(contexts[0].facts['lines'],'none')
        with self.store.db() as db:
            detail=json.loads(db.execute('SELECT detail FROM tool_events WHERE tool=?',(om.DREAM_EVENT_TOOL,)).fetchone()[0])
        self.assertIsNotNone(detail['diary'])
        self.assertEqual(self.service.lines.lines(),[])

    def test_a_full_look_back_with_empty_lines_stands_nothing(self):
        contexts=self.dream_with({'lines':[]})
        self.assertEqual(len(contexts),1)
        self.assertIn('owner-private-799-a',contexts[0].facts['lines'])
        self.assertEqual(self.service.lines.lines(),[])
        with self.store.db() as db:
            detail=json.loads(db.execute('SELECT detail FROM tool_events WHERE tool=?',(om.DREAM_EVENT_TOOL,)).fetchone()[0])
            self.assertEqual(db.execute('SELECT count(*) FROM preparations').fetchone()[0],0)
        self.assertEqual(detail['lines'],{'proposed':0,'kept':[],'dropped':[]})

    def test_read_event_query_is_batched_across_lines(self):
        for task in ('First responsibility','Second responsibility'):
            brief=self.service.lines.open(self.b,task=task,report_promise='afterwards')
            self.helper.line_handler(self.store.job(self.b))({'action':'read','line':brief['line_id']})
        traced=[];original=self.store.db
        @contextlib.contextmanager
        def db():
            with original() as connection:
                connection.set_trace_callback(traced.append)
                yield connection
        with patch.object(self.store,'db',db):text,_=self.helper.line_judgment_input(self.now,refs=True)
        self.assertEqual(sum('FROM tool_events e JOIN jobs' in query for query in traced),1)
        self.assertEqual([line['use']['read_in_answers'] for line in json.loads(text)['lines']],[1,1])

    def test_one_line_row_refusal_does_not_block_starting_the_next(self):
        first=self.stand();self.a=self.owner('other-a',self.now-86400);self.b=self.owner('other-b',self.now-30)
        second=self.stand(task='Second responsibility')
        # Persist real owner acts while row creation is unavailable; recover both on one tick.
        with patch.object(self.helper,'start_announced_lines',return_value=None):
            for brief in (first,second):
                self.helper.line_handler(self.store.job(self.b))({'action':'restart','line':brief['line_id']})
        create=self.service.preparations.create;attempted=[]
        def fail_first(**kwargs):
            attempted.append(kwargs.get('line_id'))
            if kwargs.get('line_id')==first['line_id']:raise prep.PreparationRefusal('too_many')
            return create(**kwargs)
        with patch.object(self.service.lines,'lines',return_value=[first,second]):
            with patch.object(self.service.preparations,'create',side_effect=fail_first):
                with self.assertLogs(level='WARNING') as logged:self.helper.line_tick(self.now)
        self.assertEqual(attempted[0],first['line_id'])
        self.assertIn(second['line_id'],attempted)
        self.assertTrue(any(first['line_id'] in line for line in logged.output))
        self.assertEqual(self.helper.line_rows(first['line_id']),[])
        self.assertEqual(len(self.helper.line_rows(second['line_id'])),2)

    def test_one_withdrawal_conflict_does_not_block_withdrawing_the_next(self):
        first=self.stand();self.a=self.owner('other-a',self.now-86400);self.b=self.owner('other-b',self.now-30)
        second=self.stand(task='Second responsibility')
        write=self.service.lines.write
        def conflict(line_id,*args,**kwargs):
            if line_id==first['line_id']:raise el.LineConflict(2)
            return write(line_id,*args,**kwargs)
        self.now+=7*86400
        with patch.object(self.service.lines,'write',side_effect=conflict):
            with self.assertLogs(level='WARNING') as logged:self.helper.line_tick(self.now)
        self.assertTrue(any(first['line_id'] in line for line in logged.output))
        self.assertEqual(self.service.lines.current(first['line_id'])['state'],el.STATE_ACTIVE)
        self.assertEqual(self.service.lines.current(second['line_id'])['change'],'withdrawn')

    def test_invalid_suggestion_links_are_scheduled_unlinked(self):
        stood=self.stand()
        opened=self.service.lines.open(self.b,task='Owner responsibility',report_promise='later')
        for index,line in enumerate(('missing',stood['line_id'],opened['line_id'])):
            args={**self.item()['first'][0],'goal':f'Unlinked work {index}','line':line}
            out,_=self.helper.suggest_preparations(self.b,[args])
            self.assertIn('preparation_id',out[0])
            self.assertNotIn('reason',out[0])
            self.assertIsNone(self.service.preparations.get(out[0]['preparation_id'])['line_id'])


    def test_owner_restart_of_an_active_waiting_line_announces_and_locks_it(self):
        brief=self.stand()
        owner=self.owner('Restart this responsibility',self.now)
        self.helper.line_handler(self.store.job(owner))({'action':'restart','line':brief['line_id']})
        self.assertTrue(self.helper.line_announced(brief['line_id']))
        self.assertTrue(self.service.lines.owner_written(brief['line_id']))
        item=self.item(action='amend',line=brief['line_id'],expected_version=brief['version'],first=[],
                       task='An inferred replacement',report_promise='',sources=[self.b])
        result,_=self.helper.apply_line_actions(self.b,[item],self.service.decision_judge)
        self.assertEqual(result,[{'reason':'line_change_needs_owner'}])


class ReviewFixTests(_LinesCase):
    def reads(self, work_id):
        return [event['trace']['evidence'] for event in self.store.task_events(work_id)
                if event['tool']==el.READ_EVENT]

    def plan_work(self):
        from personal_agent.preparations_service import DAY_PLAN_GOAL
        row=self.service.preparations.create(kind='prepare',goal=DAY_PLAN_GOAL,due_at=self.now+60,
                timezone='Asia/Seoul',recurrence='daily',channel='web',created_from=None,
                state=prep.STATE_SCHEDULED,accepted_by=prep.ACCEPTED_DAY_PLAN,origin=prep.ORIGIN_DAY_PLAN)
        self.now+=120
        self.assertTrue(self.service.run_due_preparation(self.now))
        return self.store.job(self.service.preparations.get(row['id'])['last_run_job_id'])

    def test_day_plan_line_input_records_one_read_in_information_use_after_sending(self):
        brief=self.stand()
        work=self.plan_work()
        self.service.preparation_history(work)
        self.assertEqual(self.reads(work['id']),[], 'building an unsent input is not a read')
        seen=[]
        self.script=[lambda body:seen.append(body) or {'content':'Nothing needed.'}]
        self.assertTrue(self.service.run_one())
        self.assertTrue(any(brief['task'] in (message.get('content') or '')
                            for body in seen for message in body['messages']))
        self.assertEqual(self.reads(work['id']),[{'line_id':brief['line_id'],'version':1}])
        audit=self.service.work_information_use(work['id'])
        [used]=[item for item in audit['used'] if item['category']=='lines']
        self.assertTrue(any(brief['line_id'] in item for item in used['items']))
        # Rebuild and actually send the history again for the same Work; neither adds another read.
        history=self.service.preparation_history(work)
        self.helper.record_plan_line_reads(work['id'],history,history[-1]['content'])
        self.assertEqual(self.reads(work['id']),[{'line_id':brief['line_id'],'version':1}])

    def test_day_plan_input_failure_runs_without_line_reads(self):
        self.stand();work=self.plan_work()
        with patch.object(self.helper,'line_judgment_input',side_effect=RuntimeError('fixture failure')):
            with self.assertLogs(level='WARNING'):self.assertTrue(self.service.run_one())
        self.assertEqual(self.reads(work['id']),[])

    def test_only_lines_retained_in_sent_block_are_recorded(self):
        brief=self.stand()
        omitted=self.service.lines.open(self.b,task='Other responsibility',report_promise='later')
        work=self.plan_work()
        with patch.object(om,'LINE_INPUT_CHARS',1400):
            history=self.service.preparation_history(work)
            block=json.loads(history[-1]['line_judgment_input'])
            self.assertEqual([line['line_id'] for line in block['lines']],[brief['line_id']])
            self.assertTrue(self.service.run_one())
        self.assertEqual(self.reads(work['id']),[{'line_id':brief['line_id'],'version':1}])
        self.assertNotIn(omitted['line_id'],json.dumps(self.reads(work['id'])))

    def test_plan_preflight_failure_records_no_line_reads(self):
        self.stand();work=self.plan_work()
        self.store.put('model_test',{})
        self.assertTrue(self.service.run_one())
        self.assertEqual(self.model_calls,[])
        self.assertEqual(self.reads(work['id']),[])

    def test_nightly_line_input_records_reads_only_for_sent_judgment(self):
        brief=self.stand()
        contexts=[]
        def structured(context,question,schema):
            if context.purpose!=om.DREAM_PURPOSE:return None
            contexts.append(context)
            return StructuredDecision(OUTCOME_DECIDED,{'proposals':[],'notes':'','change':'','notes_sources':[],
                                     'diary':'','suggestions':[],'lines':[]},fixture_confidence())
        self.service.use_decision_engine(FixtureDecisionEngine(structured=structured))
        self.service._owner_model_dream({'job_id':self.b,'reason':str(self.now-3600)},self.store.job(self.b),self.now)
        self.assertEqual(len(contexts),1)
        self.assertIn(brief['task'],contexts[0].facts['lines'])
        self.assertEqual(self.reads(self.b),[{'line_id':brief['line_id'],'version':1}])

    def test_nightly_call_not_sent_records_no_line_reads(self):
        from personal_agent.decision import DecisionConfidence, OUTCOME_UNAVAILABLE
        self.stand()
        self.service.use_decision_engine(FixtureDecisionEngine(structured=lambda *args:
                StructuredDecision(OUTCOME_UNAVAILABLE,confidence=DecisionConfidence(sent=False))))
        self.service._owner_model_dream({'job_id':self.b,'reason':str(self.now-3600)},self.store.job(self.b),self.now)
        self.assertEqual(self.reads(self.b),[])

    def test_deleted_work_and_account_stay_deleted_across_ticks_and_service_restart(self):
        brief=self.stand();self.deliver(self.announce(brief))
        work=self.work_rows(brief)[0];account=self.helper.line_account_row(brief['line_id'])
        for row in (work,account):
            self.service.preparation_request({'operation':'delete','id':row['id']})
            remaining={item['id'] for item in self.helper.line_rows(brief['line_id'])}
            self.service=self.make_service();self.helper=self.service.preparations_service
            for offset in (60,120,86400):self.helper.line_tick(self.now+offset)
            self.assertEqual({item['id'] for item in self.helper.line_rows(brief['line_id'])},remaining)
        self.assertEqual(self.helper.line_rows(brief['line_id']),[])
        # Already-active restart leaves the brief version alone, but is still a new owner act.
        owner=self.owner('Restart this responsibility',self.now)
        self.helper.line_handler(self.store.job(owner))({'action':'restart','line':brief['line_id']})
        restarted=self.helper.line_rows(brief['line_id'])
        self.assertEqual(len(restarted),2)
        self.assertFalse({work['id'],account['id']} & {row['id'] for row in restarted})
        for offset in (60,120,86400):self.helper.line_tick(self.now+offset)
        self.assertEqual({row['id'] for row in self.helper.line_rows(brief['line_id'])},{row['id'] for row in restarted})

    def test_deleted_account_alone_stays_deleted_while_work_is_active(self):
        brief=self.stand();self.deliver(self.announce(brief))
        work=self.work_rows(brief)[0];account=self.helper.line_account_row(brief['line_id'])
        self.service.preparation_request({'operation':'delete','id':account['id']})
        for offset in (60,120,86400):self.helper.line_tick(self.now+offset)
        self.assertEqual([row['id'] for row in self.helper.line_rows(brief['line_id'])],[work['id']])
        owner=self.owner('Restart this responsibility',self.now)
        self.helper.line_handler(self.store.job(owner))({'action':'restart','line':brief['line_id']})
        self.assertEqual([row['id'] for row in self.work_rows(brief)],[work['id']])
        self.assertNotEqual(self.helper.line_account_row(brief['line_id'])['id'],account['id'])

    def test_crash_before_work_or_account_creation_retries_on_next_tick(self):
        brief=self.stand();notice=self.announce(brief)
        create=self.service.preparations.create
        def crash(**kwargs):
            raise RuntimeError('injected crash before creation')
        with patch.object(self.service.preparations,'create',side_effect=crash):
            with self.assertRaises(RuntimeError):self.deliver(notice)
        self.assertTrue(self.helper.line_announced(brief['line_id']))
        self.assertEqual(self.helper.line_rows(brief['line_id']),[])
        def fail_account(**kwargs):
            if kwargs.get('origin')==prep.ORIGIN_LINE_ACCOUNT:raise RuntimeError('account crash')
            return create(**kwargs)
        with patch.object(self.service.preparations,'create',side_effect=fail_account):
            with self.assertRaises(RuntimeError):self.helper.line_tick(self.now+60)
        self.assertEqual(len(self.work_rows(brief)),1)
        self.assertIsNone(self.helper.line_account_row(brief['line_id']))
        self.helper.line_tick(self.now+120)
        self.assertEqual(len(self.helper.line_rows(brief['line_id'])),2)

    def test_creation_and_started_marker_roll_back_together(self):
        brief=self.stand();notice=self.announce(brief)
        with patch.object(self.helper,'record_line_row_started',side_effect=RuntimeError('crash before commit')):
            with self.assertRaises(RuntimeError):self.deliver(notice)
        self.assertEqual(self.helper.line_rows(brief['line_id']),[])
        record=self.helper.record_line_row_started
        def crash_after_account(work_id,line_id,role,restart,db):
            if role=='account':raise RuntimeError('crash after account creation')
            return record(work_id,line_id,role,restart,db)
        with patch.object(self.helper,'record_line_row_started',side_effect=crash_after_account):
            with self.assertRaises(RuntimeError):self.helper.line_tick(self.now+60)
        self.assertEqual(len(self.work_rows(brief)),1)
        self.assertIsNone(self.helper.line_account_row(brief['line_id']))
        self.helper.line_tick(self.now+120)
        self.assertEqual(len(self.helper.line_rows(brief['line_id'])),2)
