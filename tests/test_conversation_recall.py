"""#205 Slice B: model-free source history supply across store, API and CLI relay."""
import json
import sqlite3
import uuid
from unittest import mock

from personal_agent import agent_runtime, information_use
from personal_agent.agent_runtime import goal_judgment
from personal_agent.conversation_handoff import ConversationJudgments
from personal_agent.decision import BinaryDecision, FixtureDecisionEngine, OUTCOME_DECIDED, fixture_confidence
from personal_agent.memory_service import conversation_block, conversation_block_refs, conversation_refs
from personal_agent.owner_forget import OwnerForget
from personal_agent.quickstart_service import AgentService
import test_information_use as audit_helpers
from test_information_use import steps


class ConversationRecall(audit_helpers._ServiceBase):
    service = audit_helpers.ApiRouteAudit.service

    def exchange(self, owner='같이 이야기해요', assistant='커피 마시면서 여기서 만나요', *, typed=True, channel='web', delivery='none'):
        job=self.store.enqueue(owner,str(uuid.uuid4()),owner_typed=typed,channel=channel)
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='succeeded',created=10,source_at=9,delivery=? WHERE id=?",(delivery,job))
            db.execute('INSERT INTO messages(role,content,channel,created,job_id) VALUES (?,?,?,?,?)',('user',owner,channel,10,job))
            db.execute('INSERT INTO messages(role,content,channel,created,job_id) VALUES (?,?,?,?,?)',('assistant',assistant,channel,11,job))
        return job

    def age(self, count=380):
        # Unrelated messages push the actual source beyond history()'s own 100 rows.
        with self.store.db() as db:
            db.executemany('INSERT INTO messages(role,content,channel,created) VALUES (?,?,?,?)',
                           [('user',f'unrelated {i}','web',20+i) for i in range(count)])

    def judge(self, service):
        seen=[]
        def answer(context, proposition):
            seen.append(context)
            return BinaryDecision(OUTCOME_DECIDED,True,fixture_confidence())
        service.use_decision_engine(FixtureDecisionEngine(judge=answer))
        return seen

    def test_local_sqlite_prefix_semantics_and_hangul_bounds(self):
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE VIRTUAL TABLE words USING fts5(text,tokenize="unicode61")')
            db.execute('INSERT INTO words VALUES (?)',('커피도 만나요',))
            self.assertEqual(db.execute('SELECT count(*) FROM words WHERE words MATCH ?',('"커피"',)).fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM words WHERE words MATCH ?',('"커피"*',)).fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM words WHERE words MATCH ?',('"만난"*',)).fetchone()[0],0)
        self.exchange();self.age()
        for query in ('커피','커피도','만나요','만나'):
            self.assertEqual(len(self.store.search_conversation(query)),1,query)
        # Shared syllables, not arbitrary typo correction: 만난 and 만나 differ.
        self.assertEqual(self.store.search_conversation('만난까'),[])
        self.assertEqual(self.store.search_conversation('만날까'),[])

    def test_old_exchange_both_sides_sources_and_recent_exclusion(self):
        job=self.exchange();self.age()
        recent=self.exchange('커피도 괜찮아요','최근 답')
        rows=self.store.owner_words('커피도')
        self.assertEqual([row['work_id'] for row in rows],[job])
        self.assertNotEqual(rows[0]['work_id'],recent)
        owner,assistant=rows[0]['turns']
        self.assertEqual((owner['role'],owner['source_at'],owner['created']),('user',9,10))
        self.assertEqual((assistant['role'],assistant['kind'],assistant['delivered']),('assistant','model-stated',None))
        self.assertEqual(self.store.search_conversation('커피',before=9),[])
        self.assertEqual(self.store.search_conversation('커피',after=10),[])
        self.assertEqual(len(self.store.search_conversation('커피',after=9,before=10)),1)
        self.assertEqual(len(self.store.history()),100)

    def test_action_continuation_keeps_first_owner_row_and_recall(self):
        owner='커피 이야기 이어가요'
        work=self.exchange(owner)
        with self.store.db() as db:
            original=[dict(row) for row in db.execute('SELECT * FROM messages WHERE job_id=? ORDER BY id',(work,))]
        between=self.exchange('중간 질문','중간 답변')
        for index in range(2):
            # A completed login requeues the original Work, not a new owner utterance.
            service=AgentService(self.store)
            with self.store.db() as db:
                self.assertTrue(service._queue_action_continuation(db,self.store.job(work),{
                    'action':'browser_sign_in','state':'offered',
                    'digest':f'login-{index}','offer_digest':f'offer-{index}'}))
            self.assertEqual(service._claim_queued_work()['id'],work)
            history=self.store.history(through=work)
            self.assertEqual((history[-1]['role'],history[-1]['content']),('user',owner))
            self.assertGreater(history[-1]['id'],original[-1]['id'])
            self.assertIn(original[-1]['id'],[row['id'] for row in history])
            self.assertIn(between,[row['job_id'] for row in history])
            with self.store.db() as db:
                db.execute("UPDATE jobs SET status='succeeded' WHERE id=?",(work,))
        self.age()
        [exchange]=self.store.owner_words('커피')
        self.assertEqual([turn['id'] for turn in exchange['turns']],[row['id'] for row in original])
        self.assertEqual(exchange['turns'][0]['created'],10)
        self.assertEqual(exchange['turns'][0]['source_at'],9)
        self.assertEqual(service.owner_words_block('커피').count(owner),1)
        self.assertEqual(self.store.search_conversation('커피'),[exchange])
        self.assertEqual(self.store.search_conversation('',message_ids=[exchange['matched_id']]),[exchange])

    def test_identical_owner_messages_in_different_works_are_both_written_and_recalled(self):
        owner='커피 이야기 이어가요'
        service=AgentService(self.store)
        works=[]
        for index in range(2):
            work=self.store.enqueue(owner,f'separate-{index}',owner_typed=True)
            works.append(work)
            self.assertEqual(service._claim_queued_work()['id'],work)
            with self.store.db() as db:
                db.execute("UPDATE jobs SET status='succeeded' WHERE id=?",(work,))
        self.age()
        exchanges=self.store.owner_words('커피')
        self.assertEqual([exchange['work_id'] for exchange in exchanges],works)
        self.assertEqual([turn['content'] for exchange in exchanges for turn in exchange['turns']],[owner,owner])
        self.assertEqual(service.owner_words_block('커피').count(owner),2)

    def test_ownership_documents_secrets_and_delivery(self):
        self.exchange(typed=False)
        document=self.exchange('커피 문서 요청','커피 문서 답')
        self.store.put('file_workspace_document_jobs',[document])
        self.store.secret('api_key:openai','current-credential-value')
        safe=self.exchange('커피 current-credential-value','커피 답 current-credential-value',channel='telegram',delivery='pending')
        self.store.secret('api_key:anthropic','old-credential-value')
        self.exchange('커피 old-credential-value')
        self.store.secret('api_key:anthropic','replacement-credential-value')
        self.age()
        rows=self.store.search_conversation('커피')
        self.assertEqual([row['work_id'] for row in rows],[safe])
        self.assertFalse(rows[0]['turns'][1]['delivered'])
        self.assertNotIn('credential-value',json.dumps(rows))
        self.assertIn('[redacted]',json.dumps(rows))
        self.assertNotIn('문서',conversation_block(rows))

    def test_recalled_forgotten_value_cannot_be_resaved_by_worker_or_upkeep(self):
        from personal_agent import owner_model as om
        from personal_agent.decision import StructuredDecision
        from test_owner_model_upkeep import proposal
        key,value='profile.preference','특별한원두'
        self.exchange('커피 취향은 '+value);self.age()
        forget=OwnerForget(self.store)
        for delete in (False,True):
            with self.subTest(delete=delete):
                saved=self.store.save_memory(key,value,owner_id='local-owner')
                forget.forget('local-owner','memory:'+saved['id'],delete=delete)
                work=self.store.enqueue('이전 대화 다시 확인',str(uuid.uuid4()))
                caps=agent_runtime.Capabilities(self.store,None,{},'',work,lambda *args:None)
                recalled=caps.execute('search_conversation',{'query':'커피'})
                block=conversation_block(recalled['exchanges'])
                self.assertIn(value,block,'Memory deletion does not delete recorded conversation')
                refused=caps.execute('save_memory',{'memory_key':key,'content':value})
                self.assertEqual((refused['saved'],refused['refused_because']),(False,'forgotten-value'))
                seen=[]
                def propose(context,question,schema):
                    seen.append(context)
                    return StructuredDecision(OUTCOME_DECIDED,{'proposals':[
                        proposal(key,value,category='preference')]},fixture_confidence())
                judgments=ConversationJudgments(FixtureDecisionEngine(structured=propose))
                _state,_calls,_event,detail=om.Upkeep(self.store).run(
                    self.store.job(work),judgments,answer=block,profile='',clock='2026-10-27T12:00:00+09:00')
                self.assertIn(value,seen[0].facts['final_answer_excerpt'])
                self.assertEqual([item['reason'] for item in detail['dropped']],[om.FORGOTTEN_VALUE])
                self.assertIsNone(self.store.current_memory(key,'local-owner'))
                self.assertTrue(forget.is_forgotten('local-owner',key,value))

    def test_auto_supply_worker_and_goal_judge_audit_exact_ids(self):
        job=self.exchange();self.age()
        service=self.service(steps([('list_memory',{})],final='이어 이야기해요'))
        seen=self.judge(service)
        work=self.turn(service,'커피도 괜찮겠죠')
        profile=service.owner_profile_snapshot('커피도 괜찮겠죠')
        refs=conversation_block_refs(profile)
        self.assertEqual([ref['work_id'] for ref in refs],[job])
        goal=next(context for context in seen if context.purpose=='goal-reached')
        self.assertIn('커피 마시면서 여기서 만나요',goal.facts['owner_context'])
        self.assertIn('model-stated',goal.facts['owner_context'])
        record=self.store.turn_provenance(work)
        self.assertEqual(record['owner_information']['earlier_exchanges'],refs)
        audit=service.work_information_use(work)
        self.assertEqual(audit['sent_to']['earlier_exchanges']['worker_sections'],refs)
        self.assertEqual(sum(len(row['message_ids']) for row in refs),2)

    def test_api_tool_supplies_exchange_to_judge_without_auto_match(self):
        old=self.exchange();self.age()
        service=self.service(steps([('search_conversation',{'query':'커피도','limit':3})],final='이어 이야기해요'))
        seen=self.judge(service)
        work=self.turn(service,'그때 얘기 이어가요')
        events=[e for e in self.store.task_events(work) if e['tool']=='search_conversation' and e['status']=='succeeded']
        self.assertEqual(len(events),1,self.store.task_events(work))
        [event]=events
        evidence=event['trace']['evidence']
        self.assertEqual(evidence['exchange_count'],1)
        self.assertEqual(evidence['exchange_refs'][0]['work_id'],old)
        self.assertNotIn('content',json.dumps(evidence))
        goal=next(context for context in seen if context.purpose=='goal-reached')
        self.assertIn('커피 마시면서 여기서 만나요',goal.facts['observations'])
        self.assertIn('search_conversation',agent_runtime.EFFECT_FREE_READS)
        self.assertFalse(agent_runtime.declared_effect('search_conversation',{}))
        self.assertIn('conversation',{row['category'] for row in service.work_information_use(work)['used']})

    def test_cli_relay_same_recall_and_bounded_tool_results(self):
        for i in range(5):self.exchange(f'커피 기록 {i}',f'커피 대답 {i}')
        self.age()
        replies=[]
        def call(tools):
            from personal_agent import mcp_bridge
            class Relay:
                def call(self,name,args):return tools.call(name,args)
            def protocol(handle, concurrent=None):
                listed=handle('tools/list',{})['tools']
                definition=next(row for row in listed if row['name']=='search_conversation')
                self.assertTrue(definition['annotations']['readOnlyHint'])
                for limit in (2,3):
                    reply=handle('tools/call',{'name':'search_conversation','arguments':{'query':'커피','limit':limit}})
                    self.assertNotIn('isError',reply,reply)
                    replies.append(json.loads(reply['content'][0]['text']))
            with mock.patch.object(mcp_bridge,'RelayClient',return_value=Relay()), mock.patch.object(mcp_bridge,'_serve_stdio',side_effect=protocol):
                mcp_bridge.serve(str(self.store.root),tools.capabilities.job_id,browser_relay='/fixture-relay')
        service=audit_helpers.CliRouteAudit.service(self,call)
        # The sandbox forbids Unix socket listeners too. Inject only relay
        # construction; the CLI facade still executes the real service tools.
        with mock.patch('personal_agent.quickstart_service.BrowserRelay') as relay:
            relay.return_value.address='/fixture-relay'
            work=self.turn(service,'이전 대화 확인')
        self.assertEqual(len(replies[0]['exchanges']),2)
        self.assertLessEqual(sum(len(row['exchanges']) for row in replies),3)
        self.assertTrue(all(len(json.dumps(row,ensure_ascii=False).encode())<9000 for row in replies))
        from personal_agent.bounded_execution import profile_actions,STRICT_PROFILE
        self.assertNotIn('search_conversation',profile_actions(STRICT_PROFILE))

    def test_direct_judge_sees_tool_history_even_when_claim_does_not_cite_it(self):
        for i in range(3):self.exchange(f'커피 질문 {i}',f'커피 대답 {i}')
        self.age()
        rows=self.store.search_conversation('커피')
        self.assertEqual(len(rows),3)
        observed=[]
        def judge(context, proposition):
            observed.append(context)
            return BinaryDecision(OUTCOME_DECIDED,True,fixture_confidence())
        goal_judgment(ConversationJudgments(FixtureDecisionEngine(judge=judge)),'요청',
                      {'summary':'답','evidence_refs':[]}, {'x':('search_conversation','search_conversation','succeeded',{'exchanges':rows[:1]}),
                       'y':('search_conversation','search_conversation','succeeded',{'exchanges':rows[:2]}),
                       'z':('search_conversation','search_conversation','succeeded',{'exchanges':rows[2:]})},[])
        self.assertEqual(conversation_block_refs(observed[0].facts['observations']),conversation_refs(rows))

    def test_deleted_transcript_and_long_three_turn_exchange(self):
        work=self.exchange('커피 '+'가'*500,'커피 '+'나'*500)
        with self.store.db() as db:
            db.execute('INSERT INTO messages(role,content,channel,created,job_id) VALUES (?,?,?,?,?)',
                       ('assistant','커피 '+'다'*500,'web',12,work))
        self.age()
        rows=self.store.search_conversation('커피')
        self.assertEqual(len(rows),1)
        self.assertEqual(len(rows[0]['turns']),3)
        self.assertLessEqual(len(conversation_block(rows).encode()),3000)
        with self.store.db() as db:db.execute('DELETE FROM messages WHERE job_id=?',(work,))
        self.assertEqual(self.store.search_conversation('커피'),[])

    def test_recall_is_read_only(self):
        self.exchange();self.age()
        original=self.store.db
        from contextlib import contextmanager
        @contextmanager
        def read_only():
            with original() as db:
                db.execute('PRAGMA query_only=ON')
                yield db
        with mock.patch.object(self.store,'db',read_only):
            self.assertTrue(self.store.search_conversation('커피'))

    def test_malformed_tool_arguments_and_legacy_tombstone_does_not_disable_recall(self):
        self.exchange();self.age()
        for kwargs in ({'limit':True},{'limit':4},{'before':'yesterday'},{'after':float('nan')}):
            with self.assertRaises(ValueError):self.store.search_conversation('커피',**kwargs)
        OwnerForget(self.store)
        with self.store.db() as db:
            db.execute('INSERT INTO forgotten_values(owner_key,digest,receipt_id,created) VALUES (?,?,?,?)',
                       (self.store._memory_binding('local-owner'),'unknown','legacy',1))
        self.assertTrue(self.store.search_conversation('커피'))
        with self.store.db() as db:
            self.assertEqual([row['name'] for row in db.execute('PRAGMA table_info(forgotten_values)')],
                             ['owner_key','digest','receipt_id','created'])


import test_orchestrator as orchestration_helpers


class CliRecallJudgment(orchestration_helpers.Harness):
    def test_cli_auto_and_tool_history_reach_goal_judgment(self):
        old=ConversationRecall.exchange(self)
        ConversationRecall.age(self)
        for request,tool in (('커피도 괜찮겠죠',False),('그때 얘기 이어가요',True)):
            with self.subTest(tool=tool):
                replies=[]
                def read(tools):
                    result=tools.call('search_conversation',{'query':'커피도'})
                    replies.append(result)
                    # The real bridge writes this content-free event after it
                    # returns the observation (exercised above through serve).
                    tools.capabilities.record('search_conversation','succeeded',json.dumps({
                        'host_action':'search_conversation','evidence':agent_runtime.evidence_summary('search_conversation',result)}))
                self.engine.before=read if tool else None
                self.script([orchestration_helpers.plan('codex','Read the supplied source history.')],goals=[True])
                with mock.patch('personal_agent.quickstart_service.BrowserRelay') as relay:
                    relay.return_value.address='/fixture-relay'
                    work,row=self.run_work(request)
                self.assertEqual(row['status'],'succeeded')
                goal=self.asked_goals[-1]
                fact=goal.facts['observations' if tool else 'owner_context']
                self.assertIn('커피 마시면서 여기서 만나요',fact)
                refs=conversation_block_refs(fact)
                self.assertEqual(refs[0]['work_id'],old)
                if tool:self.assertEqual(refs,conversation_refs(replies[0]['exchanges']))
                else:
                    self.assertIn('커피 마시면서 여기서 만나요',self.engine.turns[-1]['prompt'])
                    self.assertEqual(refs,self.store.turn_provenance(work)['workers'][-1]['earlier_exchanges'])

    def test_judgment_audit_counts_only_transmitted_complete_exchanges(self):
        from personal_agent.decision import DecisionContext, audit_record
        ConversationRecall.exchange(self);ConversationRecall.age(self)
        block=conversation_block(self.store.search_conversation('커피'))
        context=DecisionContext('goal-reached',{'owner_context':block,'observations':block})
        confidence=fixture_confidence();confidence.sent=True
        record=audit_record(context,'judge',OUTCOME_DECIDED,{'answer':True},confidence,1)
        self.assertEqual(record['earlier_exchanges'],conversation_block_refs(block))
        self.assertEqual(len(record['earlier_exchanges']),1,'same exchange in two facts is counted once')
        work=self.store.enqueue('new request','audit-refs')
        self.service.record_decision({**record,'work_id':work})
        audit=self.service.work_information_use(work)
        self.assertEqual(audit['sent_to']['judgment_ai']['earlier_exchanges'][0]['exchanges'],record['earlier_exchanges'])
        self.assertEqual(self.store.turn_provenance(work)['conversation_readers'][0]['exchanges'],record['earlier_exchanges'])
        confidence.sent=False
        self.assertNotIn('earlier_exchanges',audit_record(context,'judge',OUTCOME_DECIDED,{'answer':True},confidence,1))
        clipped=DecisionContext('goal-reached',{'owner_context':block[:-10]})
        confidence.sent=True
        self.assertNotIn('earlier_exchanges',audit_record(clipped,'judge',OUTCOME_DECIDED,{'answer':True},confidence,1))
