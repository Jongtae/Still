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


class Lines(_Case):
    def setUp(self):
        super().setUp()
        self.helper=self.service.preparations_service
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
        self.assertTrue(self.service.deliver_notification())
        self.assertEqual(self.store.notification_of(notice['job_id'],notice['kind'])['state'],'sent')
        self.helper.line_tick(self.now)

    def work_rows(self, brief):
        return [row for row in self.helper.line_rows(brief['line_id']) if row['origin']!=prep.ORIGIN_LINE_ACCOUNT]

    def assert_refused(self, item, code):
        outcomes,_=self.helper.apply_line_actions(self.b,[item],self.service.decision_judge)
        self.assertEqual(outcomes,[{'reason':code}])

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
        self.assertIn('2',self.helper.line_notice_text(notice))

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
                self.assertEqual(len(rows),2)
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
        self.assertEqual(self.helper.line_rows(brief['line_id']),[]) # still awaits announcement

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
        result,_=self.helper.suggest_preparations(self.b,[{**self.item()['first'][0],'line':brief['line_id']}])
        self.assertEqual(result,[{'reason':'line_not_announced'}])

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

    def test_stood_run_payment_autonomy_on_cannot_pay(self):
        from test_payment_autonomy_662 import Mode
        Mode.switch(self,True)
        brief=self.stand();self.deliver(self.announce(brief))
        row=self.work_rows(brief)[0]
        work=self.store.enqueue('run',prep.request_key(row['id'],row['due_at']))
        binding=bs.step_binding(work,'api_request','https://example.test/','pay',argument='',state='s')
        self.assertIsNone(self.service._authorize_payment_autonomy(self.store.job(work),binding,'pay'))
        self.assertTrue(self.service.self_set_run(self.store.job(work)))

    def test_stood_runs_approval_step_denied_without_prompt(self):
        brief=self.stand();self.deliver(self.announce(brief))
        row=self.work_rows(brief)[0]
        work=self.store.enqueue('run',prep.request_key(row['id'],row['due_at']))
        job=self.store.job(work)
        binding=bs.step_binding(work,'api_request','https://example.test/charge','POST /charge',
                               argument='{}',state='api-payment-v2|slot@1|payment')
        with self.assertRaises(ToolError) as caught:
            self.service.browser_approvals_for(job).request(binding,'POST /charge')
        self.assertEqual((caught.exception.code,str(caught.exception)),('unattended_run',UNATTENDED_APPROVAL_TEXT))
        self.assertIsNone(self.service._browser_request(work))
        self.assertIsNone(self.store.notification_of(work,'browser_approval_needed'))

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
