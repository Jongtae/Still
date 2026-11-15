"""#883: accepted plan selectors reach real profile, exchange and record readers.

Evidence: model-free fixtures, temporary stores and injected worker transports.
"""
import json
import uuid
from unittest import mock

import test_information_use as helpers
from personal_agent.memory_service import conversation_block_refs
from personal_agent.owner_forget import OwnerForget
from personal_agent.quickstart_service import AgentService

OWNER = 'local-owner'
FOLLOW_UP = '그거 몇 시였지?'
EARLIER_TURN = '치과 예약 시간 알려줘'
DENTIST_KEY = 'profile.zz-dentist'
DENTIST_FACT = '치과 예약은 오후 3시 30분 (synthetic)'
FILLER = 'unrelated filler fact about gardening tools and weather ' * 4


class PastWordsBrief(helpers._ServiceBase):
    service = helpers.ApiRouteAudit.service

    def setUp(self):
        super().setUp()
        self.clock = 100

    # -- fixtures through the real store APIs --------------------------------
    def exchange(self, owner, assistant='네, 알겠어요', *, typed=True, channel='web', chat_id=None):
        """One finished earlier Work with its two transcript rows (the same rows a real Work leaves)."""
        job = self.store.enqueue(owner, str(uuid.uuid4()), channel=channel, chat_id=chat_id,
                                 owner_typed=typed, owner_words=typed)
        self.clock += 2
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='succeeded',created=?,source_at=? WHERE id=?", (self.clock, self.clock - 1, job))
            db.execute('INSERT INTO messages(role,content,channel,created,job_id) VALUES (?,?,?,?,?)',
                       ('user', owner, channel, self.clock, job))
            db.execute('INSERT INTO messages(role,content,channel,created,job_id) VALUES (?,?,?,?,?)',
                       ('assistant', assistant, channel, self.clock + 1, job))
        return job

    def message_id(self, job, role='user'):
        with self.store.db() as db:
            return db.execute('SELECT id FROM messages WHERE job_id=? AND role=?', (job, role)).fetchone()['id']

    def pad(self, count=20):
        """Later unrelated owner exchanges: they push older ones out of the recent window (skip_recent=16)."""
        for index in range(count):
            self.exchange(f'zzpad{index} sentence', 'zzpad answer')

    def follow_up(self, text=FOLLOW_UP, **kwargs):
        """The new request, enqueued after everything seeded so far, with its own transcript row."""
        job = self.store.enqueue(text, str(uuid.uuid4()), owner_typed=True, owner_words=True, **kwargs)
        self.clock += 2
        with self.store.db() as db:
            db.execute('UPDATE jobs SET created=?,source_at=? WHERE id=?', (self.clock, self.clock - 1, job))
            db.execute('INSERT INTO messages(role,content,channel,created,job_id) VALUES (?,?,?,?,?)',
                       ('user', text, kwargs.get('channel', 'web'), self.clock, job))
        return self.store.job(job)

    def big_profile(self):
        """Enough profile rows that the 12000-char snapshot cannot hold the dentist row unless it matches."""
        for index in range(60):
            self.store.save_memory(f'profile.a{index:03d}', f'{FILLER} {index}', owner_id=OWNER)
        self.store.save_memory(DENTIST_KEY, DENTIST_FACT, owner_id=OWNER)

    def resolved(self,service,job,query=EARLIER_TURN):
        from test_orchestrator import OrchestrationUnit, decided, plan
        orchestration,_,_=OrchestrationUnit().orchestration(decided(dict(plan('a',''),recall_query=query)))
        return service.worker_profile_snapshot(job,orchestration.first())[0]

    # -- 1. the profile rows ---------------------------------------------------
    def test_profile_fact_is_recalled_only_with_accepted_query(self):
        service = AgentService(self.store)
        self.big_profile()
        self.exchange(EARLIER_TURN, '예약은 오후 3시 30분이에요')
        job = self.follow_up()
        without = service.owner_profile_snapshot(job['message'])
        self.assertNotIn(DENTIST_FACT, without, 'control: the profile is large enough that the row is omitted')
        self.assertIn('omitted by the context limit', without)
        recalled = self.resolved(service,job)
        self.assertIn(DENTIST_FACT, recalled)
        # recall_query=None is today's behaviour exactly.
        self.assertEqual(service.owner_profile_snapshot(job['message'], recall_query=None), without)
        self.assertEqual(service.owner_profile_snapshot(job['message'], recall_query=''), without)

    def test_english_follow_up_recalls_through_accepted_query(self):
        service = AgentService(self.store)
        self.big_profile()
        self.store.save_memory('profile.zz-passport', 'passport renewal appointment is on the 14th (synthetic)', owner_id=OWNER)
        self.exchange('When is my passport renewal appointment?', 'On the 14th')
        job = self.follow_up('and what time was that?')
        self.assertNotIn('passport renewal', service.owner_profile_snapshot(job['message']))
        self.assertIn('passport renewal appointment is on the 14th',
                      self.resolved(service,job,'passport renewal'))

    # -- 2. the earlier-exchange block -----------------------------------------
    def test_old_exchange_is_recalled_only_with_accepted_query(self):
        service = AgentService(self.store)
        old = self.exchange('치과 예약 시간 메모해줘', '오후 3시 30분으로 적어 둘게요')
        self.pad()
        self.exchange(EARLIER_TURN, '다시 확인해 볼게요')
        job = self.follow_up()
        without = service.owner_profile_snapshot(job['message'])
        self.assertNotIn('치과 예약 시간 메모해줘', without)
        recalled = self.resolved(service,job)
        self.assertIn('치과 예약 시간 메모해줘', recalled)
        self.assertIn(old, [ref['work_id'] for ref in conversation_block_refs(recalled)])

    # -- 3. traps stay ineligible even when the context names them ------------------
    def test_forgotten_superseded_expired_and_transcript_forgotten_items_stay_out(self):
        service = AgentService(self.store)
        self.big_profile()
        forgotten = self.store.save_memory('profile.zz-forgotten', '치과 잊은사실 forgotmarker', owner_id=OWNER)
        OwnerForget(self.store).forget(OWNER, 'memory:' + forgotten['id'])
        self.store.save_memory('profile.zz-superseded', '치과 옛값 oldvaluemarker', owner_id=OWNER)
        self.store.save_memory('profile.zz-superseded', '치과 새값 newvaluemarker', owner_id=OWNER)
        self.store.save_memory('profile.zz-expired', '치과 지난사실 expiredmarker', owner_id=OWNER, valid_until='2000-01-01')
        # Eligible copies share the traps' search words, with distinct markers.
        controls = ('currentforgottencontrol', 'currentsupersededcontrol', 'currentexpiredcontrol',
                    'currenttranscriptcontrol', 'currentanswercontrol', 'newvaluemarker')
        self.store.save_memory('profile.zz-current-forgotten', '치과 잊은사실 currentforgottencontrol', owner_id=OWNER)
        self.store.save_memory('profile.zz-current-superseded', '치과 옛값 currentsupersededcontrol', owner_id=OWNER)
        self.store.save_memory('profile.zz-current-expired', '치과 지난사실 currentexpiredcontrol', owner_id=OWNER)
        hidden = self.exchange('치과 사라질대화 transcriptmarker', '치과 사라질답 answermarker')
        # Transcript forgetting is per row; this trap forgets the whole exchange.
        OwnerForget(self.store).forget(OWNER, f"message:{self.message_id(hidden)},{self.message_id(hidden, 'assistant')}")
        visible = self.exchange('치과 사라질대화 currenttranscriptcontrol', '치과 사라질답 currentanswercontrol')
        self.pad()
        self.exchange(EARLIER_TURN)
        job = self.follow_up()
        without = service.owner_profile_snapshot(job['message'])
        self.assertIn('omitted by the context limit', without)
        for marker in controls:
            self.assertNotIn(marker, without, 'control: the request alone recalls none of the eligible copies')
        recalled = self.resolved(service,job)
        self.assertIn('newvaluemarker', recalled, 'control: the current row of the same topic is recalled')
        for marker in controls:
            self.assertIn(marker, recalled, 'eligible copies must be found through the accepted selector')
        self.assertIn(visible, [ref['work_id'] for ref in conversation_block_refs(recalled)])
        for marker in ('forgotmarker', 'oldvaluemarker', 'expiredmarker', 'transcriptmarker', 'answermarker'):
            self.assertNotIn(marker, recalled)



from test_orchestrator import Harness, plan


class ResolvedWorkerRecall(Harness):
    pad=PastWordsBrief.pad
    big_profile=PastWordsBrief.big_profile

    def setUp(self):
        super().setUp()
        self.clock_value=100
        # Exchange fixtures use a separate numeric clock from the harness context clock.

    def exchange(self,*args,**kwargs):
        clock=self.clock
        self.clock=getattr(self,'clock_value',100)
        try: return PastWordsBrief.exchange(self,*args,**kwargs)
        finally: self.clock_value=self.clock; self.clock=clock

    def seed_recall(self):
        self.big_profile()
        old=self.exchange('치과 예약 시간 메모해줘','오후 3시 30분')
        self.pad()
        self.exchange(EARLIER_TURN)
        self.store.save_memory('diary.2000-01-01','치과 오래된기록 recordmarker',owner_id=OWNER)
        return old

    def selected(self,worker,query):
        return dict(plan(worker,''),recall_query=query)

    def test_resolved_followup_reaches_worker_profile_exchanges_and_records(self):
        old=self.seed_recall()
        for route in ('openai','codex'):
            with self.subTest(route=route):
                self.use_main(route)
                self.script([self.selected(route,'치과 예약')],[True])
                start=len(self.asked_plans)
                work,row=self.run_work(FOLLOW_UP,key='resolved-'+route,owner_words=True)
                self.assertEqual(len(self.asked_plans)-start,1)
                if route=='openai': text=json.dumps(self.transport.bodies[-1],ensure_ascii=False)
                else: text=self.engine.turns[-1]['prompt']
                self.assertIn(DENTIST_FACT,text)
                self.assertIn('치과 예약 시간 메모해줘',text)
                self.assertIn('recordmarker',text)
                self.assertNotIn(DENTIST_FACT,self.asked_plans[-1][0].facts['owner_profile'])
                refs=self.store.turn_provenance(work)['owner_information']
                self.assertIn(DENTIST_KEY,refs['profile_keys'])
                self.assertIn(old,[ref['work_id'] for ref in refs['earlier_exchanges']])

    def test_no_plan_worker_does_not_recall_raw_preceding_topic(self):
        self.seed_recall()
        self.script([None],[True])
        work,_=self.run_work(FOLLOW_UP,owner_words=True)
        self.assertEqual(len(self.asked_plans),1)
        self.assertNotIn(DENTIST_FACT,self.engine.turns[-1]['prompt'])
        entry=self.store.turn_provenance(work)['attempt_inputs'][-1]
        self.assertEqual(entry['recall_selector']['source'],'request')

    def test_redelegation_refreshes_profile_from_new_plan_query(self):
        self.seed_recall()
        self.store.save_memory('profile.zz-quartz','quartzclang flagmarker',owner_id=OWNER)
        self.script([self.selected('codex','치과'),self.selected('codex','quartzclang')],[False,True],[False])
        self.run_work(FOLLOW_UP,owner_words=True)
        self.assertEqual(len(self.asked_plans),2)
        self.assertEqual(len(self.engine.turns),2)
        self.assertIn(DENTIST_FACT,self.engine.turns[0]['prompt'])
        self.assertNotIn('flagmarker',self.engine.turns[0]['prompt'])
        self.assertIn('flagmarker',self.engine.turns[1]['prompt'])
        self.assertNotIn(DENTIST_FACT,self.engine.turns[1]['prompt'])

    def test_worker_recall_refs_and_selector_digest_match_actual_sent_sections(self):
        import hashlib
        from personal_agent import information_use
        from personal_agent.work_execution import PROCEED
        for route in ('openai','codex'):
            with self.subTest(route=route):
                self.use_main(route)
                self.seed_recall()
                self.script([self.selected(route,'치과')],[True])
                prepared_entries=[]
                sent_refs=[]
                full_profiles=[]
                original_snapshot=self.service.worker_profile_snapshot
                def snapshot(*args,**kwargs):
                    result=original_snapshot(*args,**kwargs)
                    full_profiles.append(result[0])
                    return result
                original_sent=self.service.record_turn_sent
                original_prepare=self.service._prepare_worker_turn
                splice={'kind':'fixture','ref':'fixture-source','label':'fixture-source'}
                def prepare(run):
                    result=original_prepare(run)
                    if result is PROCEED:
                        run.spliced_refs.append(splice)
                        run.base_history[-1]['content']+='\nSource: fixture-source (synthetic)'
                    return result
                def recorded(job_id,**kwargs):
                    ref=original_sent(job_id,**kwargs)
                    prepared_entries.append(self.store.turn_provenance(job_id)['attempt_inputs'][-1])
                    sent_refs.append(kwargs['owner_information'])
                    return ref
                with mock.patch.object(self.service,'worker_profile_snapshot',side_effect=snapshot), \
                        mock.patch.object(self.service,'record_turn_sent',side_effect=recorded), \
                        mock.patch.object(self.service,'_prepare_worker_turn',side_effect=prepare), \
                        mock.patch('personal_agent.work_executor.MAX_PROMPT_BYTES',7000):
                    work,_=self.run_work(FOLLOW_UP,key='receipt-'+route,owner_words=True)
                receipt=self.store.turn_provenance(work)
                entry=receipt['attempt_inputs'][-1]
                self.assertEqual(prepared_entries[-1]['input'],'prepared')
                self.assertEqual(entry['input'],'sent')
                self.assertEqual(entry['recall_selector'],dict(source='plan',
                    query_digest=hashlib.sha256(('치과 '+FOLLOW_UP).encode()).hexdigest()[:16],query_chars=len('치과 '+FOLLOW_UP),recall_enabled=True))
                self.assertNotIn('recall_query',entry)
                self.assertIn(DENTIST_KEY,receipt['owner_information']['profile_keys'])
                if route=='codex':
                    context=self.engine.turns[-1]['context']
                    self.assertTrue(context.get('cut_to_fit'))
                    # Compare exact complete source lines, independently of the receipt parser.
                    sent_lines=set(context.get('profile','').splitlines())
                    full_lines=[line for line in full_profiles[-1].splitlines() if line.startswith('profile.')]
                    expected_keys=sorted(line.split(':',1)[0] for line in full_lines if line in sent_lines)
                    self.assertTrue(expected_keys)
                    self.assertLess(len(expected_keys),len(full_lines))
                    self.assertEqual(sorted(sent_refs[-1]['profile_keys']),expected_keys)
                    self.assertIn('fixture-source',self.engine.turns[-1]['prompt'])
                else:
                    text=json.dumps(self.transport.bodies[-1],ensure_ascii=False)
                    self.assertIn('fixture-source',text)
                    self.assertEqual(sent_refs[-1]['spliced'],[splice])
                # Plan sources remain in the union even when the worker has cut a tail.
                self.assertIn('profile.a000',receipt['owner_information']['profile_keys'])
                self.assertEqual(len(self.asked_plans),1 if route=='openai' else 2)
