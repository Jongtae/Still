"""L2 fixture evidence: real store, run/settle and builtin delivery; only the wires are injected."""
import json
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
            with self.subTest(lang=lang),patch('personal_agent.preparations_service.msg',
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

    def test_owner_requested_row_on_stood_line_sent_at_slot_with_one_bundle_per_day(self):
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
                with patch.object(self.helper,step,side_effect=KeyError('fixture failure')):
                    work=self.report(row,'Held despite upkeep failure')
                    self.assertEqual(self.store.job(work)['channel'],'web')
                    self.assertEqual(self.store.job(work)['delivery'],'none')
                    self.assertEqual(self.sends(),[])
                    self.at(9);self.service.run_due_preparation();self.service.deliver_one()
                    self.assertEqual(len(self.sends()),1)
                    self.assertIn(work,[w for w,_ in self.receipts()])

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

    def test_l1_when_needed_notification_is_not_sent_again_in_bundle(self):
        self.at(10);_,row=self.line();self.now+=86400;self.at(8)
        with self.store.db() as db:
            db.execute("DELETE FROM tool_events WHERE tool='line_held'")
            db.execute('UPDATE preparations SET channel=?,delivery_mode=? WHERE id=?',('telegram',prep.DELIVERY_WHEN_NEEDED,row['id']))
        # Replay the L1 route through existing settle and notification delivery before enabling holding.
        with patch.object(self.helper,'hold_line_rows',return_value=None),patch.object(self.helper,'held_row',return_value=False),patch.object(self.service,'watch_judgment',return_value='yes'):
            work=self.report(row,'Previously sent result')
        with patch('personal_agent.telegram_store.time.time',return_value=self.now):self.service.deliver_notification()
        self.assertEqual(self.store.notification_of(work,prep.NOTIFY_KIND)['state'],'sent')
        self.now+=1;self.helper.hold_line_rows(self.now)
        self.at(9);self.assertIsNone(self.flush());self.assertEqual(self.receipts(),[])

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

    def test_when_needed_only_notify_is_bundled_and_web_bundle_is_delivered(self):
        self.at(10);brief,row=self.line();self.now+=86400;self.at(8)
        with self.store.db() as db:db.execute('UPDATE preparations SET delivery_mode=? WHERE id=?',(prep.DELIVERY_WHEN_NEEDED,row['id']))
        with patch.object(self.service,'watch_judgment',return_value='no'):self.report(row,'Silent result')
        self.at(9);self.assertIsNone(self.flush())
        self.at(10)
        with patch.object(self.service,'watch_judgment',return_value='yes'):work=self.report(row,'Notify result')
        web=self.owner('Latest web owner turn',self.now)
        with self.store.db() as db:db.execute("UPDATE jobs SET channel='web',chat_id=NULL WHERE id=?",(web,))
        self.at(18);bundle=self.flush();self.assertIsNotNone(bundle)
        self.assertEqual(self.store.job(bundle)['delivery'],'none')
        self.assertEqual(self.receipts(),[(work,bundle)])
        self.assertEqual(self.helper.line_outcomes(brief['line_id'])[0]['outcome'],el.OUTCOME_DELIVERED)
