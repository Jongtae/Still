"""#1299: canonical connection preferences with injected AI and API wires.

Fixtures choose the route; tests prove persistence/context/authority plumbing,
not a live model's semantic preference judgment.
"""
import json
from datetime import datetime, timezone
from unittest import mock

from personal_agent import owner_mcp, trust_record
from personal_agent.api_requests import save_slot, remove_slot, slots
from personal_agent.memory_service import CONNECTION_PREFERENCE_GUIDANCE, PROFILE_SNAPSHOT_CHARS
from test_orchestrator import Harness, plan

KEY = 'profile.preference.connection.unseen'


class ConnectionPreferences(Harness):
    def save_choice(self, value):
        self.saved_results = []
        self.engine.before = lambda tools: self.saved_results.append(tools.call('save_memory', {'memory_key': KEY, 'content': value}))
        self.script([plan('codex', 'Keep the stated connection preference.')], goals=[True])
        job, _ = self.run_work('For Unseen my durable connection preference is ' + value)
        self.engine.before = None
        current = next(row for row in self.store.memories() if row['memory_key'] == KEY)
        return job, current

    def test_each_connection_kind_is_canonical_and_reaches_both_workers_and_planner(self):
        for choice in ('AI-side connection', 'Still-owned connection', 'signed-in browser'):
            with self.subTest(choice=choice):
                job, row = self.save_choice(choice)
                self.assertEqual(row['content'], choice)
                self.assertEqual(row['state'], 'current')
                self.assertFalse(owner_mcp.confirmed(self.store, 'codex'))
                self.assertFalse(trust_record.hands(self.store), 'a preference creates no grant')
                self.assertTrue(self.saved_results[-1]['auto_saved'])
                self.asked_plans.clear()
                self.script([plan('codex', 'Answer from the owner context.'), plan('openai', 'Answer from the owner context.')], goals=[True])
                self.run_work('Show the Unseen connection choice.', key='cli-' + choice)
                self.use_main('openai')  # #1431: the API route runs as the Main AI
                self.run_work('State the Unseen connection choice.', key='api-' + choice)
                self.use_main('codex')
                for text in (self.engine.turns[-1]['prompt'], self.transport.bodies[-1]['messages'][0]['content']):
                    self.assertIn(KEY + ': ' + choice, text)
                    self.assertIn(CONNECTION_PREFERENCE_GUIDANCE, text)
                self.assertTrue(all(KEY + ': ' + choice in item[0].facts['owner_profile'] for item in self.asked_plans))

    def prepare_slots(self):
        for name in ('unseen-preferred', 'unseen-alternate'):
            save_slot(self.store, name, ['unseen.example'], 'synthetic-secret-' + name,
                      subject_field='$.account', subject_value='same-synthetic-account', note='Unseen records')
        _, row = self.save_choice('Still-owned via unseen-preferred; same-synthetic-account only')
        return row

    def use_slot(self, name, explanation, before_read=None):
        calls = []
        payload = {'account': 'same-synthetic-account', 'as_of': datetime.now(timezone.utc).isoformat(),
                   'record': 'Friday deadline; two pending items'}
        def transport(method, url, headers, body, timeout):
            calls.append((method, url, headers))
            return 200, {'content-type': 'application/json'}, json.dumps(payload).encode()
        results = []
        def read(tools):
            if before_read is not None:
                before_read(tools)
            results.append(tools.call('api_request', {
                'slot': name, 'url': 'https://unseen.example/record', 'effect': 'read',
                'as_of_field': '$.as_of', 'required_fields': json.dumps(['$.record'])}))
        self.engine.before = read
        self.engine.answers = [explanation + ' Friday deadline; two pending items.']
        self.asked_plans.clear()
        self.script([plan('codex', 'Use the current connection choice and report observed records.')], goals=[True])
        with mock.patch('personal_agent.api_requests.http_request', transport):
            job, result = self.run_work('Summarize the Unseen record.', key='read-' + name)
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertIn('Friday deadline; two pending items', result['response'])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][2]['Authorization'], 'Bearer synthetic-secret-' + name)
        self.assertTrue(results)
        events = self.store.task_events(job)
        self.assertNotIn('synthetic-secret-', json.dumps(events))
        self.assertNotIn('synthetic-secret-', json.dumps(results))
        return result

    def test_multiple_usable_connections_keep_preferred_route_and_memory(self):
        row = self.prepare_slots()
        self.assertEqual(set(slots(self.store)), {'unseen-preferred', 'unseen-alternate'})
        self.use_slot('unseen-preferred', 'Used your preferred connection.')
        [current] = [item for item in self.store.memories() if item['memory_key'] == KEY]
        self.assertEqual((current['id'], current['content']), (row['id'], row['content']))
        self.assertIn(row['content'], self.asked_plans[0][0].facts['owner_profile'])

    def test_unavailable_preference_allows_explained_authorized_fallback_without_rewriting_it(self):
        row = self.prepare_slots()
        remove_slot(self.store, 'unseen-preferred')
        self.assertEqual(set(slots(self.store)), {'unseen-alternate'})
        result = self.use_slot('unseen-alternate', 'Your preferred connection is absent; used the already connected alternative for the same account.')
        self.assertIn('preferred connection is absent', result['response'])
        [current] = [item for item in self.store.memories() if item['memory_key'] == KEY]
        self.assertEqual((current['id'], current['content']), (row['id'], row['content']))
        self.assertFalse(trust_record.hands(self.store))

    def test_registry_outage_leaves_authorized_generic_fallback_and_original_result(self):
        from personal_agent.capability_search import CapabilitySearch
        row = self.prepare_slots()
        remove_slot(self.store, 'unseen-preferred')
        registry = mock.Mock(return_value=(503, {}, b'Unavailable'))
        self.service.capability_search = CapabilitySearch(registry)
        observations = []

        def discover(tools):
            result = tools.call('capability_search', {'source': 'registry', 'query': 'unseen'})
            observations.append(result)
            self.assertEqual(result['state'], 'unavailable')
            self.assertEqual(result['results'], [])
            self.assertFalse(self.service.settings_orchestrator._drafts())
            self.assertFalse(trust_record.hands(self.store))

        result = self.use_slot('unseen-alternate',
            'The registry is unavailable and your preferred connection is absent; '
            'used the already connected alternative for the same account.', before_read=discover)
        registry.assert_called_once()
        self.assertEqual(len(observations), 1)
        self.assertIn('registry is unavailable', result['response'])
        self.assertEqual(self.store.job(result['id'])['message'], 'Summarize the Unseen record.')
        current = next(item for item in self.store.memories() if item['memory_key'] == KEY)
        self.assertEqual((current['id'], current['content']), (row['id'], row['content']))
        self.assertFalse(self.service.settings_orchestrator._drafts())
        self.assertFalse(trust_record.hands(self.store))

    def test_correction_exact_undo_and_forget_change_only_canonical_preference(self):
        _, first = self.save_choice('Still-owned connection')
        _, changed = self.save_choice('signed-in browser')
        self.assertIn('signed-in browser', self.service.owner_profile_snapshot())
        receipt = self.store.retract_memory('local-owner', changed['id'], changed['content_digest'])
        self.assertTrue(receipt['retracted'])
        self.assertIn(KEY + ': Still-owned connection', self.service.owner_profile_snapshot())
        self.assertNotIn('signed-in browser', self.service.owner_profile_snapshot())
        from personal_agent.owner_forget import OwnerForget
        from personal_agent.agent_runtime import MEMORY_OWNER
        OwnerForget(self.store).forget(MEMORY_OWNER, 'memory:' + first['id'])
        self.assertNotIn(KEY, self.service.owner_profile_snapshot())
        self.assertFalse(trust_record.hands(self.store))

    def test_relevant_preference_is_retrieved_before_worker_selection_when_default_profile_is_full(self):
        # #217: enough rows to overflow the default snapshot bound.
        for index in range(PROFILE_SNAPSHOT_CHARS // 150):
            self.store.save_memory(f'profile.aaa.item{index}', 'Unrelated background ' * 8, owner_id='local-owner')
        _, row = self.save_choice('Still-owned connection')
        self.assertNotIn(KEY, self.service.owner_profile_snapshot())
        self.assertIn('omitted', self.service.owner_profile_snapshot())
        self.asked_plans.clear()
        self.script([plan('codex', 'Use the connection choice in the owner context.')], goals=[True])
        job, result = self.run_work('Read the Unseen record.', key='full-profile')
        self.assertEqual(result['status'], 'succeeded')
        self.assertIn(KEY + ': ' + row['content'], self.asked_plans[0][0].facts['owner_profile'])
        self.assertIn(KEY + ': ' + row['content'], self.engine.turns[-1]['prompt'])
        self.assertIn('omitted', self.asked_plans[0][0].facts['owner_profile'])

    def test_an_over_limit_cli_brief_cuts_optional_tails_and_keeps_the_profile_head(self):
        """Codex review on #223: fixed sections past the CLI byte limit are cut, least needed first,
        instead of the request-only fallback dropping the profile, the context and the instructions."""
        from personal_agent import quickstart_service
        from personal_agent.agent_runtime import CUT_TO_FIT_MARKER
        for index in range(40):
            self.store.save_memory(f'profile.aaa.item{index:02d}', '평소에 쓰는 가게와 방식 ' * 4, owner_id='local-owner')
        _, row = self.save_choice('Still-owned connection')
        self.script([plan('codex', 'Use the connection choice in the owner context.')], goals=[True])
        limit = 17000
        with mock.patch.object(quickstart_service, 'MAX_PROMPT_BYTES', limit):
            job, result = self.run_work('Read the Unseen record.', key='over-limit')
        self.assertEqual(result['status'], 'succeeded')
        prompt = self.engine.turns[-1]['prompt']
        self.assertLessEqual(len(prompt.encode()), limit)
        self.assertIn('# Still instructions', prompt, 'not the bare-request fallback')
        self.assertIn(KEY + ': ' + row['content'], prompt, 'the query match heads the profile and survives')
        self.assertIn(CUT_TO_FIT_MARKER, prompt)
        self.assertNotIn('profile.aaa.item39', prompt, 'the profile tail was cut')

    def test_the_early_plan_records_the_recalled_words_it_read(self):
        """Codex review on #227: a Work parked after its early plan still shows what that plan read."""
        import time as clock
        old = self.store.enqueue('Unseen records live in the archive app', 'said-early', owner_typed=True)
        with self.store.db() as db:
            db.execute('INSERT INTO messages(role,content,channel,created,job_id) VALUES (?,?,?,?,?)',
                       ('user', 'Unseen records live in the archive app', 'web', clock.time() - 864000, old))
            for index in range(16):
                db.execute('INSERT INTO messages(role,content,channel,created) VALUES (?,?,?,?)',
                           ('assistant', f'filler {index}', 'web', clock.time()))
        self.script([plan('codex', 'Read it.')], goals=[True])
        ident = self.store.enqueue('Read the Unseen record.', 'early-audit')
        self.assertIsNotNone(self.service.early_work_plan(self.store.job(ident), 'Read the Unseen record.'))
        self.assertIn('owner_said', self.asked_plans[-1][0].facts['owner_profile'])
        refs = self.store.turn_provenance(ident)['owner_information']
        self.assertIn('owner_said (1 earlier owner message)', json.dumps(refs))
        self.assertEqual(refs.get('prepared'), [], 'the plan context carries no prepared answer itself')

    def test_an_early_plan_that_never_asks_records_nothing(self):
        """Codex review on #259: the audit records the sections only once the plan call goes out."""
        from personal_agent.orchestrator import Orchestration
        ident = self.store.enqueue('Read the Unseen record.', 'early-no-call')
        with mock.patch.object(Orchestration, 'budget_allows', return_value=False):
            self.service.early_work_plan(self.store.job(ident), 'Read the Unseen record.')
        self.assertNotIn('owner_information', self.store.turn_provenance(ident) or {})

    def test_the_early_plan_audit_uses_the_bounded_facts_it_sent(self):
        """Codex review on #259: a section longer than the plan's bound is audited as sent, not whole."""
        from personal_agent.orchestrator import CURRENT_CONTEXT_FACT_CHARS
        seen = []
        ident = self.store.enqueue('Read the Unseen record.', 'early-bounded')
        self.script([plan('codex', 'Read it.')], goals=[True])
        with mock.patch.object(type(self.service), 'current_context_text', lambda service, job: 'x' * 5000), \
                mock.patch.object(type(self.service), 'owner_information_refs',
                                  lambda service, sections, spliced=(): seen.append(dict(sections)) or {}):
            self.service.early_work_plan(self.store.job(ident), 'Read the Unseen record.')
        [sent] = seen
        self.assertLessEqual(len(sent['current_context']), CURRENT_CONTEXT_FACT_CHARS)
        self.assertEqual(set(sent), {'profile', 'current_context'})

    def test_early_plan_and_regular_plan_use_same_retrieved_profile(self):
        for index in range(20):
            self.store.save_memory(f'profile.aaa.item{index}', 'Unrelated background ' * 8, owner_id='local-owner')
        self.save_choice('AI-side connection')
        self.asked_plans.clear()
        self.script([plan('codex', 'Use the saved connection choice.')], goals=[True])
        ident = self.store.enqueue('Read the Unseen record.', 'early-preference')
        job = self.store.job(ident)
        early = self.service.early_work_plan(job, job['message'])
        self.assertIsNotNone(early)
        self.assertIsNotNone(early['data'])
        self.assertIn(KEY + ': AI-side connection', self.asked_plans[0][0].facts['owner_profile'])
        self.script([plan('codex', 'Use the saved connection choice.')], goals=[True])
        self.asked_plans.clear()
        self.assertTrue(self.service.run_one())
        self.assertEqual(len(self.asked_plans), 1, 'ordinary path reuses its early plan')
        self.assertIn(KEY + ': AI-side connection', self.asked_plans[0][0].facts['owner_profile'])
        self.assertIn(KEY + ': AI-side connection', self.engine.turns[-1]['prompt'])
