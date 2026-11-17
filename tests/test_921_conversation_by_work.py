"""#921 PR-7: addressed source history; real temporary stores, fake API/CLI transports."""
import json
from unittest import mock

from personal_agent import agent_runtime, mcp_bridge
from personal_agent.agent_runtime import Capabilities, MEMORY_OWNER, ToolError
from personal_agent.bounded_execution import AgentOSMcpTools
from personal_agent.decision import BinaryDecision, FixtureDecisionEngine, OUTCOME_DECIDED, audit_record, fixture_confidence
from personal_agent.memory_service import conversation_block_refs, conversation_refs, recalled_tool_exchanges
from personal_agent.owner_forget import OwnerForget
import test_conversation_recall as recall_helpers
import test_information_use as audit_helpers
import test_orchestrator as orchestration_helpers
from test_information_use import steps


SOURCE_SENTENCE = ('To see the conversation a saved fact came from, pass its source work_id '
                   'from search_memory instead of a query.')


class ConversationByWork(audit_helpers._ServiceBase):
    exchange = recall_helpers.ConversationRecall.exchange
    age = recall_helpers.ConversationRecall.age
    service = audit_helpers.ApiRouteAudit.service

    def judge(self, service):
        seen = []

        def answer(context, proposition):
            seen.append(context)
            confidence = fixture_confidence()
            confidence.sent = True  # Simulate a transmitted judgment for the attribution audit.
            service.record_decision(audit_record(context, 'judge', OUTCOME_DECIDED, {'answer': True}, confidence, 1))
            return BinaryDecision(OUTCOME_DECIDED, True, confidence)

        service.use_decision_engine(FixtureDecisionEngine(judge=answer))
        return seen

    def caps(self):
        work = self.store.enqueue('Open a source conversation.', 'current-' + str(len(self.store.jobs())))
        return Capabilities(self.store, None, {}, '', work, lambda *args: None)

    def ids(self, work):
        with self.store.db() as db:
            return [row['id'] for row in db.execute('SELECT id FROM messages WHERE job_id=? ORDER BY id', (work,))]

    def record_recall(self, caps, result):
        with self.store.db() as db:
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (caps.job_id, 'search_conversation', 'succeeded', json.dumps({
                           'host_action': 'search_conversation',
                           'evidence': agent_runtime.evidence_summary('search_conversation', result)}), 1))

    def test_lexical_replay_keeps_recorded_turns_across_recent_window_boundary(self):
        source = self.exchange('Original owner question', 'First answer')
        with self.store.db() as db:
            db.executemany('INSERT INTO messages(role,content,channel,created,job_id) VALUES (?,?,?,?,?)',
                           [('assistant', 'archivetopic match', 'web', 12, source),
                            ('assistant', 'Newer answer', 'web', 13, source)])
        ids = self.ids(source)
        self.age(count=15)  # The default 16-row recent window starts at turn 4.
        caps = self.caps()
        result = caps.execute('search_conversation', {'query': 'archivetopic'})
        [exchange] = result['exchanges']
        self.assertEqual(exchange['matched_id'], ids[2])
        self.assertEqual([turn['id'] for turn in exchange['turns']], ids[:3])
        self.record_recall(caps, result)
        [replayed] = recalled_tool_exchanges(self.store, caps.job_id)
        self.assertEqual(replayed, exchange)
        self.assertEqual([turn['id'] for turn in replayed['turns']], ids[:3])
        # Replay also honors a noncontiguous set, without filling in a nearby turn.
        [subset] = self.store.search_conversation('', message_ids=[ids[0], ids[2]], limit=1)
        self.assertEqual([turn['id'] for turn in subset['turns']], [ids[0], ids[2]])

    def test_recent_addressed_result_replays_exactly_the_turns_sent(self):
        source = self.exchange()
        with self.store.db() as db:
            db.executemany('INSERT INTO messages(role,content,channel,created,job_id) VALUES (?,?,?,?,?)',
                           [('assistant', 'Third turn', 'web', 12, source),
                            ('assistant', 'Fourth turn', 'web', 13, source)])
        caps = self.caps()
        result = caps.execute('search_conversation', {'work_id': source})
        self.assertEqual([turn['id'] for turn in result['exchanges'][0]['turns']], self.ids(source)[:3])
        self.record_recall(caps, result)
        self.store.messages_search_available = False
        self.assertEqual(recalled_tool_exchanges(self.store, caps.job_id), result['exchanges'])

    def test_replay_excludes_a_turn_forgotten_after_the_tool_result(self):
        source = self.exchange()
        caps = self.caps()
        result = caps.execute('search_conversation', {'work_id': source})
        [exchange] = result['exchanges']
        self.record_recall(caps, result)
        owner, assistant = self.ids(source)
        OwnerForget(self.store).forget(MEMORY_OWNER, f'message:{assistant}')
        with self.store.db() as db:
            db.execute('INSERT INTO messages(role,content,channel,created,job_id) VALUES (?,?,?,?,?)',
                       ('assistant', 'Unrecorded later answer', 'web', 12, source))
        [replayed] = recalled_tool_exchanges(self.store, caps.job_id)
        self.assertEqual(replayed['turns'], exchange['turns'][:1])
        [stored] = self.store.search_conversation('', message_ids=[owner, assistant], limit=1)
        self.assertEqual(stored, replayed)

    def test_old_and_recent_work_return_the_same_exchange_shape_and_timestamps(self):
        old = self.exchange(delivery='sent')
        self.age()
        recent = self.exchange('최근 질문', '최근 답', delivery='sent')
        for work in (old, recent):
            with self.subTest(work=work):
                result = self.store.search_conversation(work_id=work)
                self.assertEqual(result['outcome'], 'found')
                [exchange] = result['exchanges']
                self.assertEqual(set(exchange), {'matched_id', 'work_id', 'turns'})
                self.assertEqual(exchange['work_id'], work)
                self.assertEqual(exchange['matched_id'], self.ids(work)[0])
                self.assertEqual([row['id'] for row in exchange['turns']], self.ids(work))
                owner, assistant = exchange['turns']
                self.assertEqual((owner['role'], owner['source_at'], owner['created']), ('user', 9, 10))
                self.assertEqual((owner['kind'], owner['delivered']), ('owner-stated', None))
                self.assertEqual((assistant['role'], assistant['source_at'], assistant['created']), ('assistant', None, 11))
                self.assertEqual((assistant['kind'], assistant['delivered'], assistant['delivery']), ('model-stated', True, 'sent'))
        [matched] = self.store.search_conversation('커피')
        [addressed] = self.store.search_conversation(work_id=old)['exchanges']
        self.assertEqual(matched['work_id'], addressed['work_id'])
        self.assertEqual(matched['turns'], addressed['turns'])
        self.assertEqual(matched['matched_id'], self.ids(old)[1], 'lexical match still names the matching assistant row')
        self.assertEqual(self.store.search_conversation('최근'), [], 'lexical recall still excludes recent rows')

    def test_addressed_read_ignores_query_dates_recent_cutoff_and_fts(self):
        work = self.exchange()
        self.store.messages_search_available = False
        with mock.patch('personal_agent.quickstart_store.fts_prefix_expression', side_effect=AssertionError('MATCH used')):
            result = self.store.search_conversation('unrelated selector', work_id=work,
                                                    before=0, after=100, skip_recent=10000)
            self.assertEqual(result['outcome'], 'found')
            self.assertEqual(result['exchanges'][0]['work_id'], work)
        self.assertEqual(self.store.search_conversation('커피'), [])

    def test_unknown_non_owner_and_bare_web_work_are_not_found(self):
        untyped = self.exchange(typed=False)
        bare = self.exchange()
        with self.store.db() as db:
            db.execute('UPDATE jobs SET owner_words=0 WHERE id=?', (bare,))
        for work in ('missing-work', untyped, bare):
            with self.subTest(work=work):
                self.assertEqual(self.store.search_conversation(work_id=work), {'outcome': 'not_found', 'exchanges': []})
        telegram = self.exchange(channel='telegram')
        with self.store.db() as db:
            db.execute('UPDATE jobs SET owner_words=0 WHERE id=?', (telegram,))
        self.assertEqual(self.store.search_conversation(work_id=telegram)['outcome'], 'found')

    def test_forgotten_owner_turn_never_reopens_even_if_reply_remains(self):
        for forget_reply in (False, True):
            with self.subTest(forget_reply=forget_reply):
                work = self.exchange()
                ids = self.ids(work)
                OwnerForget(self.store).forget(MEMORY_OWNER, 'message:' + ','.join(map(str, ids if forget_reply else ids[:1])))
                self.assertEqual(self.store.search_conversation(work_id=work), {'outcome': 'forgotten', 'exchanges': []})

    def test_partial_forgetting_excludes_only_forgotten_turns(self):
        work = self.exchange()
        owner, assistant = self.ids(work)
        OwnerForget(self.store).forget(MEMORY_OWNER, f'message:{assistant}')
        result = self.store.search_conversation(work_id=work)
        self.assertEqual(result['outcome'], 'found')
        self.assertEqual([row['id'] for row in result['exchanges'][0]['turns']], [owner])

    def test_document_work_is_reported_without_turns_even_without_fts(self):
        work = self.exchange('문서 요청', '문서 답')
        self.store.put('file_workspace_document_jobs', [work])
        self.store.messages_search_available = False
        self.assertEqual(self.store.search_conversation(work_id=work), {'outcome': 'document_work', 'exchanges': []})
        result = self.caps().execute('search_conversation', {'work_id': work})
        self.assertEqual((result['outcome'], result['exchanges']), ('document_work', []))

    def test_retired_secrets_exclude_exchange_and_current_secrets_are_redacted_before_clipping(self):
        self.store.secret('api_key:openai', 'old-credential-value')
        retired = self.exchange('ordinary question', 'old-credential-value')
        self.store.secret('api_key:openai', 'current-credential-value')
        current = self.exchange('x' * 175 + ' current-credential-value', 'current-credential-value answer')
        self.assertEqual(self.store.search_conversation(work_id=retired), {'outcome': 'not_found', 'exchanges': []})
        result = self.store.search_conversation(work_id=current)
        self.assertEqual(result['outcome'], 'found')
        self.assertNotIn('credential-value', json.dumps(result))
        self.assertEqual(result['exchanges'][0]['turns'][0]['content'], ('x' * 175 + ' [redacted]')[:180])
        self.assertEqual(result['exchanges'][0]['turns'][1]['content'], '[redacted] answer')

    def test_turn_character_exchange_byte_and_row_bounds(self):
        work = self.exchange('가' * 500, '나' * 500)
        with self.store.db() as db:
            db.execute('INSERT INTO messages(role,content,channel,created,job_id) VALUES (?,?,?,?,?)',
                       ('assistant', '다' * 500, 'web', 12, work))
        result = self.store.search_conversation(work_id=work)
        self.assertEqual(result['outcome'], 'found')
        [exchange] = result['exchanges']
        self.assertEqual([len(row['content']) for row in exchange['turns']], [180, 180, 180])
        self.assertLessEqual(len(json.dumps(exchange, ensure_ascii=False).encode()), 2600)
        # Four-byte characters can overflow the payload even after per-turn clipping.
        with self.store.db() as db:
            db.execute('UPDATE messages SET content=? WHERE job_id=?', ('😀' * 500, work))
        self.assertEqual(self.store.search_conversation(work_id=work), {'outcome': 'not_found', 'exchanges': []})
        for role in ('user', 'assistant'):
            oversized = self.exchange()
            with self.store.db() as db:
                db.execute('UPDATE messages SET content=? WHERE job_id=? AND role=?', ('x' * 16001, oversized, role))
            self.assertEqual(self.store.search_conversation(work_id=oversized)['exchanges'], [])
        maximum = self.exchange()
        with self.store.db() as db:
            db.execute("UPDATE messages SET content=? WHERE job_id=? AND role='user'", ('x' * 16000, maximum))
        self.assertEqual(self.store.search_conversation(work_id=maximum)['outcome'], 'found')

    def test_matched_and_addressed_reads_share_budget_on_api_and_cli(self):
        for index in range(2):
            self.exchange(f'archivetopic {index}', f'archivetopic answer {index}')
        self.age()
        addressed = self.exchange('another source', 'another answer')
        for cli in (False, True):
            with self.subTest(cli=cli):
                caps = self.caps()
                call = AgentOSMcpTools(caps).call if cli else caps.execute
                matched = call('search_conversation', {'query': 'archivetopic', 'limit': 2})
                source = call('search_conversation', {'work_id': addressed})
                self.assertEqual((len(matched['exchanges']), len(source['exchanges'])), (2, 1))
                self.assertEqual(source['outcome'], 'found')
                self.assertEqual(len(caps._conversation_refs), 3)
                for args in ({'work_id': addressed}, {'query': 'archivetopic'}):
                    refused = call('search_conversation', args)
                    self.assertTrue(refused['budget_exhausted'])
                    self.assertEqual(refused['exchanges'], [])
                with self.assertRaises((ToolError, ValueError)):
                    call('search_conversation', {'work_id': ''})

    def test_addressed_reads_reserve_budget_before_matched_search_and_after_restart(self):
        sources = [self.exchange('archivetopic ' + str(i)) for i in range(4)]
        self.age()
        caps = self.caps()
        for source in sources[:3]:
            result = caps.execute('search_conversation', {'work_id': source})
            self.assertFalse(result['budget_exhausted'])
            # Same content-free receipt the real tool pipeline persists.
            with self.store.db() as db:
                db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                           (caps.job_id, 'search_conversation', 'succeeded', json.dumps({
                               'host_action': 'search_conversation', 'evidence': agent_runtime.evidence_summary('search_conversation', result)}), 1))
        restarted = Capabilities(self.store, None, {}, '', caps.job_id, lambda *args: None)
        for args in ({'query': 'archivetopic'}, {'work_id': sources[-1]}):
            refused = restarted.execute('search_conversation', args)
            self.assertTrue(refused['budget_exhausted'])
            self.assertEqual(refused['exchanges'], [])

    def test_schema_sentence_and_api_cli_surfaces_agree(self):
        caps = self.caps()
        native = next(row['function'] for row in caps.definitions() if row['function']['name'] == 'search_conversation')
        facade = next(row for row in AgentOSMcpTools(caps).definitions() if row['name'] == 'search_conversation')
        self.assertEqual(native['parameters'], facade['inputSchema'])
        self.assertEqual(native['description'], facade['description'])
        self.assertIn(SOURCE_SENTENCE, native['description'])
        self.assertEqual(native['parameters']['properties']['work_id'], {'type': 'string'})
        self.assertNotIn('work_id', native['parameters']['required'])
        self.assertNotIn('query', native['parameters']['required'])
        work = self.exchange()
        self.assertEqual(caps.execute('search_conversation', {'work_id': work}),
                         AgentOSMcpTools(self.caps()).call('search_conversation', {'work_id': work}))
        for args in ({}, {'query': ''}, {'query': ' ', 'work_id': None}, {'work_id': 3}):
            with self.subTest(args=args), self.assertRaises(ToolError):
                caps.execute('search_conversation', args)

    def test_api_tool_audit_information_use_and_turn_provenance_keep_exact_ids(self):
        old = self.exchange()
        expected = conversation_refs(self.store.search_conversation(work_id=old)['exchanges'])
        service = self.service(steps([('search_conversation', {'work_id': old})], final='Source checked.'))
        seen = self.judge(service)
        work = self.turn(service, 'Open the source discussion.')
        [event] = [e for e in self.store.task_events(work) if e['tool'] == 'search_conversation' and e['status'] == 'succeeded']
        self.assertEqual(event['trace']['evidence']['exchange_refs'], expected)
        self.assertNotIn('content', json.dumps(event['trace']['evidence']))
        goal = next(context for context in seen if context.purpose == 'goal-reached')
        self.assertEqual(conversation_block_refs(goal.facts['observations']), expected)
        audit = service.work_information_use(work)
        self.assertEqual(audit['sent_to']['judgment_ai']['earlier_exchanges'][-1]['exchanges'], expected)
        self.assertEqual(self.store.turn_provenance(work)['conversation_readers'][-1]['exchanges'], expected)

    def test_cli_bridge_addressed_result_and_audit_use_same_exchange_ids(self):
        old = self.exchange()
        expected = conversation_refs(self.store.search_conversation(work_id=old)['exchanges'])
        replies = []

        def read(tools):
            class Relay:
                def call(self, name, args):
                    return tools.call(name, args)

            def protocol(handle, concurrent=None):
                definition = next(row for row in handle('tools/list', {})['tools'] if row['name'] == 'search_conversation')
                self.assertIn(SOURCE_SENTENCE, definition['description'])
                reply = handle('tools/call', {'name': 'search_conversation', 'arguments': {'work_id': old}})
                self.assertNotIn('isError', reply, reply)
                replies.append(json.loads(reply['content'][0]['text']))

            with mock.patch.object(mcp_bridge, 'RelayClient', return_value=Relay()), \
                    mock.patch.object(mcp_bridge, '_serve_stdio', side_effect=protocol):
                mcp_bridge.serve(str(self.store.root), tools.capabilities.job_id, browser_relay='/fixture-relay')

        service = audit_helpers.CliRouteAudit.service(self, read)
        self.judge(service)
        with mock.patch('personal_agent.work_executor.BrowserRelay') as relay:
            relay.return_value.address = '/fixture-relay'
            work = self.turn(service, 'Open the source discussion.')
        self.assertEqual(replies[0]['outcome'], 'found')
        self.assertEqual(conversation_refs(replies[0]['exchanges']), expected)
        [event] = [e for e in self.store.task_events(work) if e['tool'] == 'search_conversation' and e['status'] == 'succeeded']
        self.assertEqual(event['trace']['evidence']['exchange_refs'], expected)
        self.assertEqual(service.work_information_use(work)['sent_to']['earlier_exchanges']['worker_tool_reads'], expected)
        self.assertEqual(self.store.turn_provenance(work)['conversation_tool_reads'], expected)


class CliConversationByWork(orchestration_helpers.Harness):
    def test_recent_receipt_reopens_for_goal_audit_without_fts(self):
        source = recall_helpers.ConversationRecall.exchange(self)
        expected = conversation_refs(self.store.search_conversation(work_id=source)['exchanges'])

        def read(tools):
            result = tools.call('search_conversation', {'work_id': source})
            self.assertEqual(result['outcome'], 'found')
            self.assertEqual(conversation_refs(result['exchanges']), expected)
            # A real bridge persists this receipt before the service resumes.
            tools.capabilities.record('search_conversation', 'succeeded', json.dumps({
                'host_action': 'search_conversation', 'evidence': agent_runtime.evidence_summary('search_conversation', result)}))

        self.engine.before = read
        self.script([orchestration_helpers.plan('codex', 'Read the source discussion.')], goals=[True])
        self.store.messages_search_available = False
        with mock.patch('personal_agent.work_executor.BrowserRelay') as relay:
            relay.return_value.address = '/fixture-relay'
            work, row = self.run_work('Open the source discussion.')
        self.assertEqual(row['status'], 'succeeded')
        [goal] = self.asked_goals
        self.assertEqual(conversation_block_refs(goal.facts['observations']), expected)
        # FixtureDecisionEngine does not emit audit records; simulate its sent receipt.
        confidence = fixture_confidence()
        confidence.sent = True
        self.service.record_decision({**audit_record(goal, 'judge', OUTCOME_DECIDED, {'answer': True}, confidence, 1),
                                      'work_id': work})
        self.assertEqual(self.service.work_information_use(work)['sent_to']['judgment_ai']['earlier_exchanges'][-1]['exchanges'], expected)
        self.assertEqual(self.store.turn_provenance(work)['conversation_readers'][-1]['exchanges'], expected)
