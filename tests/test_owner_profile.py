"""SEC-PROFILE-01 (#658): owner profile facts in canonical Memory.

Profile facts (allergies, food preferences, home/work places, preferred
stores) are ordinary ``memories`` rows under the ``profile.`` key namespace.
Nothing here adds a store or a classifier: the model chooses the key when
the owner states a fact in conversation, the owner types it in Settings, and
deterministic code validates only the key shape.

Three surfaces are exercised:

- **conversation**: the existing ``save_memory`` tool through the shipped
  worker, with the existing ``explicit_memory_request`` judgment (a fixture
  DecisionEngine) and the value-coverage rules deciding canonical row versus
  MemoryCandidate;
- **Settings API**: list / add / correct on ``/api/personal-space/profile``
  and delete on the existing type-bound Memory delete;
- **Settings UI**: the 프로필 group renderer, run in Node against a DOM stub.

**Evidence class: local, offline, fixture-transport integration** for the
first two (the only injected seams are the outbound model transport and the
DecisionEngine) and a Node DOM-stub unit check for the third.  No live
provider is exercised and no claim is made about a live model's judgment of
any sentence.
"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

from personal_agent import owner_model as om
from personal_agent.agent_runtime import API_TOOL_GUIDANCE, DEFINITIONS
from personal_agent.conversation_handoff import MEMORY_REQUEST_PROPOSITION
from personal_agent.decision import OUTCOME_DECIDED, BinaryDecision, FixtureDecisionEngine, fixture_confidence
from personal_agent.memory_service import PROFILE_KEY_GUIDANCE, PROFILE_SNAPSHOT_CHARS, MemoryService
from test_pa1_memory_candidate_owner_path import _OwnerSurface

WEB = Path(__file__).resolve().parents[1] / 'src' / 'personal_agent' / 'web'


def judging(answer):
    """A DecisionEngine that answers only the explicit-memory judgment."""
    def judge(context, proposition):
        if context.purpose == 'goal-reached':
            # #657: the stored row the claim cites is judged as the request fulfilled.
            return BinaryDecision(OUTCOME_DECIDED, True, fixture_confidence())
        if context.purpose != 'explicit-memory-request':
            return None
        if answer is None:
            return None
        return BinaryDecision(OUTCOME_DECIDED, answer, fixture_confidence())
    return FixtureDecisionEngine(judge=judge)


class ProfileJudgmentAndGuidance(unittest.TestCase):
    """What the model is told; the judgment itself stays the model's."""

    def test_the_memory_proposition_covers_asserted_first_person_facts_and_excludes_hedged_ones(self):
        """#658: one clause, for the judge, not a regex.  A scripted fixture
        cannot show how a live judge reads it; this pins the text that is
        sent."""
        self.assertIn('in the first person and without hedging', MEMORY_REQUEST_PROPOSITION)
        self.assertIn('an allergy or dietary restriction', MEMORY_REQUEST_PROPOSITION)
        self.assertIn('where they live or work', MEMORY_REQUEST_PROPOSITION)
        self.assertIn('even without the word "remember"', MEMORY_REQUEST_PROPOSITION)
        self.assertIn('hedged, uncertain or hypothetical rather than asserted', MEMORY_REQUEST_PROPOSITION)
        self.assertIn('This judgment does not write anything.', MEMORY_REQUEST_PROPOSITION)

    def test_save_memory_and_the_tool_guidance_name_the_profile_namespace(self):
        [save_memory] = [tool['function'] for tool in DEFINITIONS if tool['function']['name'] == 'save_memory']
        self.assertIn(PROFILE_KEY_GUIDANCE, save_memory['description'])
        self.assertIn('profile.allergy.peanut', save_memory['description'])
        # #794 phase 3 adds only the optional correction flag (a wrong value vs an outdated one); no key argument.
        self.assertEqual(sorted(save_memory['parameters']['properties']), ['content', 'correction', 'memory_key'],
                         'no new key argument: the model already chooses memory_key')
        self.assertEqual(save_memory['parameters']['required'], ['memory_key', 'content'])
        self.assertIn('"profile." memory_key', API_TOOL_GUIDANCE)
        self.assertIn('owner profile section', API_TOOL_GUIDANCE)

    def test_save_memory_asks_the_ai_to_save_what_the_owner_states(self):
        """#804: a stated durable fact is saved; #597 still decides Memory or candidate."""
        described = {tool['function']['name']: tool['function']['description'] for tool in DEFINITIONS}
        save_memory, list_memory, search_memory = described['save_memory'], described['list_memory'], described['search_memory']
        self.assertIn('When the owner states a durable fact about themselves', save_memory)
        self.assertIn('where they live or work, a preference, an allergy, a routine', save_memory)
        # #918 slice (a): remembered at once; the owner is told afterwards, with undo.
        self.assertIn('it is remembered at once and the owner is told afterwards with an undo', save_memory)
        # #918: an inferred durable fact is saved and told with undo too; a credential never.
        self.assertIn('as what you inferred', save_memory)
        self.assertIn('Never save a credential', save_memory)
        for text in (save_memory, list_memory, API_TOOL_GUIDANCE):
            self.assertNotIn('explicit owner request', text)
            self.assertNotIn('explicitly owner-authorized', text)
            self.assertNotIn('explicit owner-authorized', text)
        self.assertIn('save_memory when the owner states a durable fact about themselves', API_TOOL_GUIDANCE)
        self.assertIn('synonyms', search_memory)
        self.assertIn('source reference', search_memory)
        self.assertIn('search_memory', API_TOOL_GUIDANCE)


class ProfileFactsInConversation(_OwnerSurface):
    def propose(self, key, content):
        self.model_plan = [('save_memory', json.dumps({'memory_key': key, 'content': content}, ensure_ascii=False))]
        self.model_text = '알겠습니다.'

    def test_an_explicit_statement_lands_under_a_profile_key(self):
        """The judged-explicit statement is stored where the model keyed it."""
        self.service.use_decision_engine(judging(True))
        self.model_claim = True
        self.propose('profile.allergy.peanut', '땅콩 알러지')
        job = self.ask('나 땅콩 알러지 있어')
        self.model_plan = []
        rows = self.store.memories()
        self.assertEqual([(row['memory_key'], row['content'], row['state']) for row in rows],
                         [('profile.allergy.peanut', '땅콩 알러지', 'current')])
        self.assertEqual(self.store.memory_candidates(), [], 'nothing left pending')
        self.assertEqual(self.store.job(job)['status'], 'succeeded')
        [link] = next(task for task in self.web('/api/tasks')['tasks'] if task['id'] == job)['retained']
        self.assertEqual((link['kind'], link['label']), ('memory', 'profile.allergy.peanut'))
        # The Settings group and the prompt snapshot see the same row.
        listed = self.web('/api/personal-space/profile')
        self.assertEqual([row['id'] for row in listed['memories']], [rows[0]['id']])
        snapshot = MemoryService(self.store, private_read_sink=MemoryService.NO_EGRESS_GUARD).profile_snapshot('local-owner')
        self.assertIn('profile.allergy.peanut: 땅콩 알러지 (saved ', snapshot['text'])

    def test_a_hedged_statement_is_saved_too_and_the_owner_can_undo(self):
        """#918 slice (a): no explicit-request judgment decides the write; the owner's worker saves, told with undo."""
        self.service.use_decision_engine(judging(False))
        self.model_claim = True
        self.propose('profile.allergy.peanut', '땅콩 알러지')
        job = self.ask('아마 땅콩 알러지 있을걸')
        self.model_plan = []
        [row] = self.store.memories()
        self.assertEqual((row['memory_key'], row['content'], row['state']), ('profile.allergy.peanut', '땅콩 알러지', 'current'))
        self.assertEqual(self.store.memory_candidates(include_decided=True), [])
        self.assertEqual(self.store.job(job)['status'], 'succeeded')
        self.assertEqual([item['id'] for item in self.web('/api/personal-space/profile')['memories']], [row['id']])
        self.assertEqual(self.web('/api/personal-space')['memory_candidate_count'], 0)
        [event] = [event for event in self.store.task_events(job) if event['tool'] == 'save_memory' and event['status'] == 'succeeded']
        self.assertTrue(event['trace']['evidence']['auto_saved'], 'the record says it was saved at once')

    def test_an_unavailable_judgment_does_not_hold_the_save(self):
        self.service.use_decision_engine(judging(None))
        self.model_claim = True
        self.propose('profile.place.home', '서울 마포구')
        self.ask('집은 서울 마포구야')
        self.model_plan = []
        self.assertEqual([row['memory_key'] for row in self.store.memories()], ['profile.place.home'])
        self.assertEqual(self.store.memory_candidates(), [])

    def test_a_value_the_owner_did_not_say_is_saved_and_undoable_not_held(self):
        """#918 slice (a): the value-coverage gate is off the owner-worker path; the undo is the correction.

        The ``profile.`` namespace is a key convention, not a second path.
        """
        self.service.use_decision_engine(judging(True))
        self.model_claim = True
        self.propose('profile.allergy.shrimp', '새우 알러지')
        self.ask('나 땅콩 알러지 있어')
        self.model_plan = []
        [row] = self.store.memories()
        self.assertEqual(row['memory_key'], 'profile.allergy.shrimp')
        self.assertEqual(self.store.memory_candidates(), [])
        receipt = self.store.retract_memory('local-owner', row['id'], row['content_digest'])
        self.assertTrue(receipt['retracted'])
        self.assertEqual(self.store.memories(), [])


def _facts(count=40):
    """A profile of ``count`` facts the size of the 2026-10 live one (~3.7k chars at 40)."""
    return {f'profile.preference.fact{index:02d}': f'선호 {index:02d}: ' + ('평소에 쓰는 가게와 방식 ' * 3).strip()
            for index in range(count)}


class ProfileSupplyToEveryReader(_OwnerSurface):
    """#217: the worker, the plan, the outcome judgment and the memory upkeep read one
    selection of the owner profile, whole at today's size, and the worker's copy does not
    depend on the plan call."""

    def save(self, facts):
        for key, content in facts.items():
            self.store.save_memory(key, content, owner_id='local-owner')

    def readers(self, section):
        """What each reader's own bound leaves of the profile section the worker was given."""
        from personal_agent.agent_runtime import owner_context_fact
        from personal_agent.conversation_handoff import ConversationJudgments
        from personal_agent.decision import FixtureDecisionEngine
        from personal_agent.orchestrator import Orchestration
        from test_orchestrator import OrchestrationUnit
        seen = []
        engine = FixtureDecisionEngine(structured=lambda context, question, schema: seen.append(context))
        Orchestration(ConversationJudgments(engine), OrchestrationUnit.catalogue(None), request='다시',
                      sections={'profile': section, 'current_context': ''}).first()
        ConversationJudgments(engine).owner_model_proposals('다시', '답', section, '2026-10-23 09:00')
        plan, upkeep = seen
        judgment = owner_context_fact({'profile': section}, lambda text: text)
        self.assertFalse(plan.too_large())
        self.assertFalse(upkeep.too_large(), 'the upkeep is not turned away by its context bound')
        return {'plan': plan.facts['owner_profile'], 'upkeep': upkeep.facts['owner_profile'],
                'judgment': judgment.split('\ncurrent_context: ')[0][len('owner_profile: '):]}

    def test_every_reader_gets_the_whole_profile_at_todays_size(self):
        facts = _facts()
        self.save(facts)
        for request in (None, '다시', '그것도 담아줘'):
            section = self.service.owner_profile_snapshot(request)
            self.assertNotIn('omitted', section, request)
            for key, content in facts.items():
                self.assertIn(f'{key}: {content}', section, (request, key))
        section = self.service.owner_profile_snapshot('다시')
        for name, text in self.readers(section).items():
            self.assertEqual(text, section, f'{name} reads exactly what the worker was given')

    def test_a_profile_over_the_bound_is_cut_once_query_first_and_marked(self):
        from personal_agent.memory_service import PROFILE_SECTION_CHARS
        facts = _facts(PROFILE_SNAPSHOT_CHARS // 60)
        facts['profile.zzz.last_in_key_order'] = '산책은 저녁에 한강 쪽으로'
        self.save(facts)
        points = [f'{index:02d} ' + '가' * (om.POINT_CHARS - 6) for index in range(om.NOTES_POINTS)]
        while len(om.notes_text(points)) > om.NOTES_CHARS:
            points.pop()
        self.assertIsNotNone(om.WorkingNotes(self.store).save(
            {'points': points, 'text': om.notes_text(points), 'change': '나' * om.CHANGE_CHARS}, None))
        section = self.service.owner_profile_snapshot('한강 산책')
        self.assertTrue(section.startswith('profile.zzz.last_in_key_order: 산책은 저녁에 한강 쪽으로'), section[:80])
        self.assertIn('saved profile facts omitted by the context limit', section)
        self.assertIn('working_notes v1', section)
        self.assertLessEqual(len(section), PROFILE_SECTION_CHARS, 'the section fits every reader bound')
        for name, text in self.readers(section).items():
            self.assertEqual(text, section, f'{name} gets the same selection, not a second cut')

    def test_the_upkeep_and_self_review_get_the_selection_the_worker_got(self):
        """#241: over the bound, the production upkeep and self-review calls select by the Work's request."""
        import re
        facts = _facts(PROFILE_SNAPSHOT_CHARS // 60)
        facts['profile.zzz.last_in_key_order'] = '산책은 저녁에 한강 쪽으로'
        self.save(facts)
        self.model_plan = []
        work = self.ask('한강 산책 코스 추천해줘')
        prompts = [json.dumps(body, ensure_ascii=False) for url, body in self.calls if url.endswith('/chat/completions')]
        seen = {}

        class Upkeep:
            @staticmethod
            def clock():
                return 0.0

            def run(self, job, judgments, *, profile, **kwargs):
                seen['upkeep'] = profile
                return om.STATE_DONE, 0, om.EVENT_RECORDED, {}

            def run_review(self, job, row, judgments, *, profile, **kwargs):
                seen['self-review'] = profile
                return om.STATE_DONE, 0, om.EVENT_RECORDED, {}

            def finish(self, *args, **kwargs):
                pass
        self.service.owner_model = Upkeep()
        for kind in (om.KIND_UPKEEP, om.KIND_SELF_REVIEW):
            self.service._owner_model_run({'job_id': work, 'kind': kind, 'expired': False}, 0.0)
        upkeep = '\n'.join(re.sub(r'^\[[0-9a-f]{8}\] ', '', line) for line in seen['upkeep'].splitlines())
        self.assertEqual(upkeep, seen['self-review'], 'the upkeep and the self-review read one selection')
        self.assertTrue(seen['self-review'].startswith('profile.zzz.last_in_key_order: 산책은 저녁에 한강 쪽으로'),
                        'query matches first, not the key-ordered head')
        self.assertIn('saved profile facts omitted by the context limit', seen['self-review'], 'marked partial')
        self.assertTrue(any(json.dumps(seen['self-review'], ensure_ascii=False)[1:-1] in prompt for prompt in prompts),
                        'the same lines the worker was given')

    def test_a_cjk_profile_is_bounded_in_bytes_so_the_cli_brief_keeps_its_context(self):
        """Codex review on #223: characters are not bytes; the CLI prompt guard counts bytes."""
        from personal_agent.agent_runtime import CLI_NATIVE_SEARCH_GUIDANCE, CLI_TOOL_GUIDANCE, CONTEXT_BUDGET_BYTES, CORE_INSTRUCTIONS
        from personal_agent.memory_service import PROFILE_SNAPSHOT_BYTES
        self.save({f'profile.preference.fact{index:03d}': '가' * 190 for index in range(60)})
        snapshot = MemoryService(self.store, private_read_sink=MemoryService.NO_EGRESS_GUARD).profile_snapshot('local-owner')
        self.assertLessEqual(len(snapshot['text'].encode()), PROFILE_SNAPSHOT_BYTES)
        self.assertLess(len(snapshot['text']), PROFILE_SNAPSHOT_CHARS, 'the byte bound is the one that cut')
        self.assertTrue(snapshot['truncated'])
        section = self.service.owner_profile_snapshot()
        self.assertIn('saved profile facts omitted by the context limit', section)
        # The fixed part of a CLI brief with the largest profile section and working notes
        # (Korean, 3 bytes a character) still leaves the conversation budget room.
        notes = '가' * om.NOTES_CHARS + 'x' * 600
        instructions = CORE_INSTRUCTIONS + CLI_TOOL_GUIDANCE + CLI_NATIVE_SEARCH_GUIDANCE
        from personal_agent.memory_service import OWNER_WORDS_BYTES
        fixed = len(instructions.encode()) + PROFILE_SNAPSHOT_BYTES + len(notes.encode()) + OWNER_WORDS_BYTES
        self.assertLess(fixed, CONTEXT_BUDGET_BYTES - 4000)

    def test_cut_to_fit_cuts_whole_lines_in_order_and_marks_each_cut(self):
        from personal_agent.agent_runtime import CUT_TO_FIT_MARKER, cut_to_fit
        sections = {'profile': '\n'.join(f'p{index}' for index in range(100)), 'skills': 's' * 50 + '\n' + 's' * 50,
                    'prepared': None, 'current_context': 'c\n' * 10, 'brief': 'b'}
        out, cut = cut_to_fit(sections, 10)
        self.assertEqual(cut, ['skills'])
        self.assertEqual(out['skills'], 's' * 50 + '\n' + CUT_TO_FIT_MARKER)
        self.assertEqual(out['profile'], sections['profile'])
        out, cut = cut_to_fit(sections, 200)
        self.assertEqual(cut, ['skills', 'brief', 'current_context', 'profile'])
        self.assertEqual((out['skills'], out['brief'], out['current_context']), ('', '', ''))
        self.assertTrue(out['profile'].startswith('p0\np1\n') and out['profile'].endswith(CUT_TO_FIT_MARKER))
        freed = sum(len(str(sections[name] or '').encode()) - len(str(out[name] or '').encode()) for name in sections)
        self.assertGreaterEqual(freed, 200)

    def said(self, *messages, role='user', days_ago=10, owner_typed=True):
        """Transcript rows of their own Works, as the shipped paths leave them (#814 ``owner_typed``)."""
        import time as clock
        import uuid
        for text in messages:
            job = self.store.enqueue(text, f'said-{uuid.uuid4()}', owner_typed=owner_typed)
            with self.store.db() as db:
                db.execute('INSERT INTO messages(role,content,channel,created,job_id) VALUES (?,?,?,?,?)',
                           (role, text, 'web', clock.time() - days_ago * 86400, job))

    def test_the_owners_earlier_words_join_the_section_dated_and_outside_the_recent_window(self):
        """#221: the owner's own earlier words that share words with the request, for every reader."""
        from personal_agent.memory_service import OWNER_WORDS_HEADING
        secret = 'sk-fixture-provider-key-0123456789'
        self.store.secret('api_key:openai', secret)
        self.said('건전지는 늘 동네 마트 앱 장바구니에 담아줘', f'마트 앱 키는 {secret}')
        self.said('마트 앱 장바구니 확인했어요', role='assistant')
        self.said(*[f'오늘 할 일 {index}' for index in range(15)], days_ago=0)
        self.said('마트 앱 장바구니 다시 담아줘', days_ago=0)  # the current request, newest
        section = self.service.owner_profile_snapshot('마트 앱 장바구니 다시 담아줘')
        block = section[section.index(OWNER_WORDS_HEADING):]
        self.assertRegex(block, r'\[said \d{4}-\d{2}-\d{2}\] 건전지는 늘 동네 마트 앱 장바구니에 담아줘')
        self.assertNotIn(secret, section)
        self.assertNotIn('확인했어요', block, 'the assistant\'s words are not the owner\'s')
        self.assertNotIn('다시 담아줘', block, 'the request and the recent window are left out')
        for name, text in self.readers(section).items():
            self.assertEqual(text, section, name)
        self.assertNotIn(OWNER_WORDS_HEADING, self.service.owner_profile_snapshot('마트 앱', notes=False),
                         'the upkeep reads the profile without it')
        self.assertNotIn(OWNER_WORDS_HEADING, self.service.owner_profile_snapshot('전혀 다른 질문'))

    def test_only_owner_typed_words_are_recalled_and_never_into_a_preparation(self):
        """Codex review on #227: Still-enqueued goals are not the owner's words; a preparation sees only its origin."""
        from personal_agent.memory_service import OWNER_WORDS_HEADING
        self.said('매주 마트 장바구니 가격을 확인해 둔다', owner_typed=False)  # a preparation's generated goal
        self.said('마트 장바구니는 동네 마트 앱으로')
        self.said(*[f'최근 {index}' for index in range(16)], days_ago=0)
        section = self.service.owner_profile_snapshot('마트 장바구니')
        self.assertIn('동네 마트 앱으로', section)
        self.assertNotIn('가격을 확인해 둔다', section)
        self.assertNotIn(OWNER_WORDS_HEADING, self.service.owner_profile_snapshot('마트 장바구니', recall=False))

    def test_recalled_words_are_audited_and_dated_in_the_owner_zone(self):
        from datetime import datetime, timezone
        from personal_agent import information_use
        self.store.put('calendar_timezone', 'Asia/Seoul')
        self.said('마트 장바구니는 동네 마트 앱으로', '마트 앱 쿠폰도 챙겨')
        sent = datetime(2026, 10, 1, 16, 30, tzinfo=timezone.utc).timestamp()  # 2026-10-02 01:30 in Seoul
        with self.store.db() as db:
            db.execute("UPDATE jobs SET source_at=? WHERE message='마트 장바구니는 동네 마트 앱으로'", (sent,))
        self.said(*[f'최근 {index}' for index in range(16)], days_ago=0)
        section = self.service.owner_profile_snapshot('마트 장바구니 쿠폰')
        self.assertIn('[said 2026-10-02] 마트 장바구니는 동네 마트 앱으로', section)
        self.assertEqual(information_use.profile_keys(section)[0], 'owner_said (2 earlier owner messages)')
        crowded = '\n'.join(f'profile.k{index}: v (saved 2026-10-01)' for index in range(60)) + '\n' + section
        self.assertEqual(information_use.profile_keys(crowded)[0], 'owner_said (2 earlier owner messages)',
                         'kept ahead of the caps')

    def test_a_secret_straddling_the_clip_is_redacted_before_it(self):
        """Codex review on #227: clipping first would keep a prefix the stored-value redaction no longer matches."""
        password = 'quietharbor-river-tulip'
        self.store.secret('api_key:openai', password)
        self.said('마트 장바구니 ' + '가' * 285 + ' ' + password)
        self.said(*[f'최근 {index}' for index in range(16)], days_ago=0)
        section = self.service.owner_profile_snapshot('마트 장바구니')
        self.assertIn('마트 장바구니 ' + '가' * 10, section)
        self.assertNotIn('quieth', section)

    def test_a_queued_web_message_is_dated_by_its_receipt(self):
        """Codex review on #227: without ``source_at``, the Work's receipt, not when the transcript row was written."""
        from datetime import datetime, timezone
        self.store.put('calendar_timezone', 'Asia/Seoul')
        self.said('마트 장바구니는 동네 마트 앱으로')
        received = datetime(2026, 10, 1, 14, 50, tzinfo=timezone.utc).timestamp()  # 23:50 in Seoul
        with self.store.db() as db:
            db.execute("UPDATE jobs SET created=?, source_at=NULL WHERE message='마트 장바구니는 동네 마트 앱으로'", (received,))
            db.execute("UPDATE messages SET created=? WHERE content='마트 장바구니는 동네 마트 앱으로'", (received + 1800,))
        self.said(*[f'최근 {index}' for index in range(16)], days_ago=0)
        self.assertIn('[said 2026-10-01] 마트 장바구니는', self.service.owner_profile_snapshot('마트 장바구니'))

    def test_a_message_holding_a_replaced_or_removed_secret_is_never_recalled(self):
        """Codex review on #227: after rotation the current redaction no longer knows the old value."""
        old, gone = 'quietharbor-river-tulip', 'amber-lantern-orchid'
        self.store.secret('api_key:openai', old)
        self.store.secret('api_key:anthropic', gone)
        self.said(f'마트 앱 비번은 {old}', f'마트 앱 예전 키 {gone}', '마트 앱 장바구니는 동네 마트로')
        self.said(*[f'최근 {index}' for index in range(16)], days_ago=0)
        self.store.secret('api_key:openai', 'brand-new-value-12345')
        self.store.remove_secret('api_key:anthropic')
        section = self.service.owner_profile_snapshot('마트 앱 장바구니')
        self.assertIn('동네 마트로', section)
        self.assertNotIn('quietharbor', section)
        self.assertNotIn('amber-lantern', section)
        stored = self.store.secret_path.read_text()
        self.assertNotIn(old, stored, 'only a keyed digest of the retired value is kept')
        self.assertNotIn(gone, stored)
        self.store.retire_secret_values(['decrypted-api-credential-1'])
        self.assertTrue(self.store.holds_retired_secret('x decrypted-api-credential-1 y'))

    def test_retired_marks_are_never_evicted_and_rotating_tokens_are_not_tracked(self):
        """Codex review on #259: a password retired long ago stays excluded; token rotation adds nothing."""
        first = 'first-retired-password-001'
        self.store.secret('api_key:openai', first)
        for index in range(300):
            self.store.secret('api_key:openai', f'rotated-value-{index:04d}-xx')
        self.assertTrue(self.store.holds_retired_secret(f'old note {first} here'))
        before = self.store.retired_secrets_path.read_text()
        for index in range(5):
            self.store.secret('google_calendar_oauth', f'oauth-token-rotation-{index:04d}')
        self.assertEqual(self.store.retired_secrets_path.read_text(), before)

    def test_a_portable_export_carries_no_secret_typed_into_the_conversation(self):
        """Codex review on #259: the export is secret-free, so current and retired values leave the transcript."""
        import sqlite3
        import tarfile
        from personal_agent.portable_state import DB_RELATIVE, ROOT, export_owner_state
        current, retired = 'current-mart-password-42', 'quietharbor-river-tulip'
        self.store.secret('api_key:openai', retired)
        self.store.secret('api_key:openai', current)
        self.said(f'마트 앱 예전 비번 {retired} 이고 지금은 {current} 야', '그냥 메모')
        archive = self.root / 'export.tar.gz'
        export_owner_state(self.data, archive)
        out = self.root / 'unpacked'
        with tarfile.open(archive) as bundle:
            bundle.extractall(out, filter='data')
        with sqlite3.connect(out / ROOT / DB_RELATIVE) as db:
            text = ' '.join(str(row[0]) for row in db.execute('SELECT content FROM messages')) + ' '.join(
                str(row[0]) for row in db.execute('SELECT message FROM jobs'))
        self.assertNotIn(current, text)
        self.assertNotIn(retired, text)
        self.assertIn('마트 앱 예전 비번 [redacted] 이고 지금은 [redacted] 야', text)
        self.assertIn('그냥 메모', text)
        self.assertNotIn('retired-secret-marks', ' '.join(tarfile.open(archive).getnames()))

    def test_every_redacted_namespace_is_retired_and_exports_use_the_runtime_redaction(self):
        """Codex review on #259: cloud credentials are retired; the export redacts what the runtime redacts,
        decrypted values included, and stops when a value cannot be read."""
        from unittest import mock
        from personal_agent import api_requests, cloud_engines, current_context
        from personal_agent.portable_state import export_owner_state
        for key in (*current_context.KNOWN_SECRET_NAMES, api_requests.SECRET_PREFIX + 'x',
                    cloud_engines.SECRET_PREFIX + 'codex:bedrock:AWS_SECRET_ACCESS_KEY'):
            self.assertTrue(current_context.is_redacted_secret_key(key), key)
        cloud = cloud_engines.SECRET_PREFIX + 'codex:bedrock:AWS_SECRET_ACCESS_KEY'
        self.store.secret(cloud, 'old-aws-secret-value-123')
        self.store.remove_secret(cloud)
        self.assertTrue(self.store.holds_retired_secret('x old-aws-secret-value-123 y'))
        self.said('클라우드 키는 decrypted-api-password-9 야')
        real = current_context.stored_secret_values
        with mock.patch.object(current_context, 'stored_secret_values',
                               lambda store: [*real(store), 'decrypted-api-password-9']):
            export_owner_state(self.data, self.root / 'a.tar.gz')
        import sqlite3, tarfile
        from personal_agent.portable_state import DB_RELATIVE, ROOT
        with tarfile.open(self.root / 'a.tar.gz') as bundle:
            bundle.extractall(self.root / 'a', filter='data')
        with sqlite3.connect(self.root / 'a' / ROOT / DB_RELATIVE) as db:
            text = ' '.join(str(row[0]) for row in db.execute('SELECT content FROM messages'))
        self.assertIn('클라우드 키는 [redacted] 야', text)
        def unreadable(store):
            raise RuntimeError('owner-held key unavailable')
        with mock.patch.object(current_context, 'stored_secret_values', unreadable):
            with self.assertRaises(ValueError):
                export_owner_state(self.data, self.root / 'b.tar.gz')
        self.assertFalse((self.root / 'b.tar.gz').exists(), 'nothing exported')

    def test_recent_history_for_models_has_retired_values_replaced(self):
        """Codex review on #259: the 16 recent messages, not only recall."""
        self.store.secret('api_key:openai', 'quietharbor-river-tulip')
        self.said('비번은 quietharbor-river-tulip 이야', days_ago=0)
        self.store.secret('api_key:openai', 'brand-new-value-12345')
        rows = self.service.history_for_models(self.store.history()[-16:])
        text = ' '.join(str(row.get('content')) for row in rows)
        self.assertIn('비번은 [redacted] 이야', text)
        self.assertNotIn('quietharbor', text)

    def test_an_unreadable_encrypted_credential_is_not_removed(self):
        """Codex review on #259: the ciphertext stays until its plaintext is retired."""
        from unittest import mock
        from personal_agent.api_credentials import ApiCredentials, CredentialError
        reference = 'api_credential:' + 'a' * 48
        self.store.secret(reference, 'ciphertext-blob')
        with mock.patch.object(ApiCredentials, 'values', side_effect=CredentialError()):
            with self.assertRaises(CredentialError):
                ApiCredentials(self.store).discard(reference)
        self.assertEqual(self.store.secret_items(reference), {reference: 'ciphertext-blob'})

    def test_the_export_redacts_every_owner_content_table_and_retries_a_moving_snapshot(self):
        """Codex review on #259: notes and Memory too; secrets rotated mid-export repeat the snapshot."""
        import sqlite3, tarfile
        from unittest import mock
        from personal_agent.portable_state import DB_RELATIVE, ROOT, export_owner_state
        from personal_agent.quickstart_store import QuickStore
        value = 'current-mart-password-42'
        self.store.secret('api_key:openai', value)
        with self.store.db() as db:
            db.execute("INSERT INTO memories(id,memory_key,content,created,state,owner_key) VALUES (?,?,?,?,?,?)",
                       ('m-secret', 'profile.note.key', f'키는 {value}', 1.0, 'current', self.store._memory_binding('local-owner')))
        export_owner_state(self.data, self.root / 'c.tar.gz')
        with tarfile.open(self.root / 'c.tar.gz') as bundle:
            bundle.extractall(self.root / 'c', filter='data')
        with sqlite3.connect(self.root / 'c' / ROOT / DB_RELATIVE) as db:
            dump = '\n'.join(db.iterdump())
        self.assertNotIn(value, dump)
        self.assertIn('키는 [redacted]', dump)
        # Structural columns are never rewritten, even when a secret equals a status word (Codex review on #259).
        self.store.secret('api_key:anthropic', 'succeeded')
        job = self.store.enqueue('결과는 succeeded 였어', 'structural')
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='succeeded' WHERE id=?", (job,))
        export_owner_state(self.data, self.root / 'e.tar.gz')
        with tarfile.open(self.root / 'e.tar.gz') as bundle:
            bundle.extractall(self.root / 'e', filter='data')
        with sqlite3.connect(self.root / 'e' / ROOT / DB_RELATIVE) as db:
            status, message = db.execute('SELECT status, message FROM jobs WHERE id=?', (job,)).fetchone()
        self.assertEqual((status, message), ('succeeded', '결과는 [redacted] 였어'))
        calls = []
        real = QuickStore.secret_redaction_material
        def moving(store):
            calls.append(1)
            return ([f'rotating-{len(calls)}-value'], None)
        with mock.patch.object(QuickStore, 'secret_redaction_material', moving):
            with self.assertRaises(ValueError):
                export_owner_state(self.data, self.root / 'd.tar.gz')
        self.assertFalse((self.root / 'd.tar.gz').exists())
        self.assertEqual(len(calls), 4, 'read, then once after each of three snapshots')

    def test_every_model_bound_text_has_retired_values_replaced(self):
        """Codex review on #259: a profile row or any judgment text, not only the transcript."""
        old = 'quietharbor-river-tulip'
        self.store.secret('api_key:openai', old)
        self.store.save_memory('profile.preference.note', f'예전 키 메모 {old}', owner_id='local-owner')
        self.store.secret('api_key:openai', 'brand-new-value-12345')
        self.assertNotIn(old, self.service.owner_profile_snapshot())
        self.assertIn('예전 키 메모 [redacted]', self.service.owner_profile_snapshot())
        self.assertNotIn(old, self.service.redact_judgment_text(f'linked reply said {old}'))

    def test_swap_retires_and_replaces_under_one_lock_and_an_unreadable_ledger_withholds(self):
        """Codex review on #259: retire-then-replace is one step; a corrupt ledger never reads as empty."""
        from personal_agent.current_context import redact_known_secrets
        self.store.secret('api_credential_state:set', 'cipher-a')
        self.store.swap_secret('api_credential_state:set', 'cipher-b', lambda old: [f'plain-of-{old}-token'])
        self.assertEqual(self.store.secret_items('api_credential_state:set'), {'api_credential_state:set': 'cipher-b'})
        self.assertTrue(self.store.holds_retired_secret('x plain-of-cipher-a-token y'))
        def unreadable(old):
            raise RuntimeError('key unavailable')
        with self.assertRaises(RuntimeError):
            self.store.swap_secret('api_credential_state:set', None, unreadable)
        self.assertEqual(self.store.secret_items('api_credential_state:set'), {'api_credential_state:set': 'cipher-b'})
        self.said('마트 장바구니 메모')
        self.said(*[f'최근 {index}' for index in range(16)], days_ago=0)
        self.store.retired_secrets_path.write_text('{not json')
        with self.assertRaises(ValueError):
            redact_known_secrets(self.store, 'anything')
        with self.assertRaises(ValueError):
            self.service.history_for_models(self.store.history()[-16:])
        self.assertNotIn('owner_said', self.service.owner_words_block('마트 장바구니'))

    def test_turn_provenance_fields_merge_in_one_transaction(self):
        """Codex review on #259: the early plan's thread and the worker turn never drop each other's fields."""
        import threading
        job = self.store.enqueue('x', 'merge-provenance')
        writers = [threading.Thread(target=self.service.record_turn_provenance, args=(job,), kwargs={f'field{index}': index})
                   for index in range(8)]
        for writer in writers:writer.start()
        for writer in writers:writer.join()
        record = self.store.turn_provenance(job)
        self.assertEqual({key: record[key] for key in record if key.startswith('field')}, {f'field{i}': i for i in range(8)})

    def test_the_retired_scan_costs_one_digest_per_position_whatever_the_ledger(self):
        """Codex review on #259: 300 retired values of 300 lengths stay cheap on a 12,000-character text."""
        import time
        self.store.retire_secret_values([f'retired-{"x" * (8 + index)}' for index in range(300)])
        text = '가나다라 ' * 2400 + ' retired-' + 'x' * 120 + ' tail'
        started = time.perf_counter()
        clean = self.store.redact_secret_spans(text, (), self.store.retired_secret_ledger())
        self.assertLess(time.perf_counter() - started, 1.0)
        self.assertTrue(clean.endswith(' [redacted] tail'))

    def test_the_owners_earlier_words_are_bounded_in_bytes(self):
        from personal_agent.memory_service import OWNER_WORDS_BYTES, OWNER_WORDS_HEADING
        self.said(*[f'마트 장바구니 메모 {index} ' + '가' * 400 for index in range(20)])
        self.said(*[f'최근 {index}' for index in range(16)], days_ago=0)
        section = self.service.owner_profile_snapshot('마트 장바구니')
        block = section[section.index(OWNER_WORDS_HEADING):]
        self.assertLessEqual(len(block.encode()), OWNER_WORDS_BYTES)
        self.assertTrue(all(len(line) <= 320 for line in block.splitlines()[1:]))

    def test_the_worker_gets_the_profile_without_a_plan_call(self):
        """Memory supply is common start context: no decision engine, no plan, still the whole profile."""
        facts = _facts()
        self.save(facts)
        self.ask('다시')
        prompt = json.dumps([body for url, body in self.calls if url.endswith('/chat/completions')][-1],
                            ensure_ascii=False)
        for key, content in facts.items():
            self.assertIn(f'{key}: {content}', prompt, key)


class ProfileFactsInSettings(_OwnerSurface):
    def remember(self, key, content):
        return self.web('/api/personal-space/profile/request',
                        {'operation': 'remember', 'memory_key': key, 'content': content})

    def profile(self):
        return self.web('/api/personal-space/profile')

    def test_settings_lists_only_profile_rows_in_key_order(self):
        self.store.save_memory('meeting-time', 'mornings')
        self.remember('profile.store.books', '교보문고')
        self.remember('profile.allergy.peanut', '땅콩')
        listed = self.profile()
        self.assertEqual(listed['state'], 'current')
        self.assertEqual([(row['memory_key'], row['content']) for row in listed['memories']],
                         [('profile.allergy.peanut', '땅콩'), ('profile.store.books', '교보문고')])
        self.assertTrue(listed['private_content_included'])
        # The general Memory list still carries the rows, keyed, so the
        # 기억과 메모 list can label them 프로필.
        records = self.web('/api/personal-records?filter=saved')['items']
        self.assertEqual(sorted(row['memory_key'] for row in records),
                         ['meeting-time', 'profile.allergy.peanut', 'profile.store.books'])

    def test_add_is_canonical_and_the_same_key_corrects(self):
        first = self.remember('profile.place.home', '서울 마포구')
        self.assertEqual(first['state'], 'current')
        self.assertEqual(self.store.memory_candidates(), [], 'an owner write is not a candidate')
        second = self.remember('profile.place.home', '서울 마포구 합정동')
        self.assertEqual(second['supersedes'], first['id'])
        rows = self.profile()['memories']
        self.assertEqual([(row['id'], row['content']) for row in rows], [(second['id'], '서울 마포구 합정동')])
        # The exact-item view opens the current row as Memory with its key.
        opened = self.web(f"/api/personal-space/items/memory/{second['id']}")['item']
        self.assertEqual((opened['kind'], opened['memory_key']), ('memory', 'profile.place.home'))

    def test_delete_follows_the_existing_forget_semantics(self):
        first = self.remember('profile.allergy.peanut', '땅콩')
        second = self.remember('profile.allergy.peanut', '땅콩, 호두')
        deleted = self.web(f"/api/personal-space/memories/{second['id']}", method='DELETE')
        self.assertEqual((deleted['deleted'], deleted['kind'], deleted['deleted_memory_count']), (True, 'memories', 2),
                         'the whole supersession chain is forgotten, not only the current value')
        self.assertEqual(self.profile()['memories'], [])
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM memories').fetchone()[0], 0)
        # A repeated delete, or one aimed at the superseded version, reports
        # "not deleted" rather than inventing a second deletion.
        for memory_id in (second['id'], first['id']):
            self.assertFalse(self.web(f"/api/personal-space/memories/{memory_id}", method='DELETE')['deleted'])

    def test_only_a_profile_shaped_key_and_a_value_are_accepted(self):
        for key in ('allergy.peanut', 'profile.', 'profile..x', 'profile.al lergy', '', None):
            status, message = self.refused('/api/personal-space/profile/request',
                                           {'operation': 'remember', 'memory_key': key, 'content': '땅콩'})
            self.assertEqual(status, 400, key)
            self.assertIn('profile.', message)
        self.assertEqual(self.refused('/api/personal-space/profile/request',
                                      {'operation': 'remember', 'memory_key': 'profile.allergy', 'content': ' '})[0], 400)
        self.assertEqual(self.refused('/api/personal-space/profile/request', {'operation': 'forget'})[0], 400)
        self.assertEqual(self.store.memories(), [])

    def test_the_owner_sees_a_secret_like_value_but_the_prompt_never_does(self):
        """#664 review P1: Settings shows the owner their own row; the model-bound
        snapshot has the stored secret's value removed deterministically."""
        secret = 'sk-fixture-provider-key-0123456789'
        self.store.secret('api_key:openai', secret)
        self.remember('profile.wifi.password', secret)
        self.assertEqual(self.profile()['memories'][0]['content'], secret, 'the owner surface is not redacted')
        snapshot = self.service.owner_profile_snapshot()
        self.assertNotIn(secret, snapshot)
        self.assertRegex(snapshot, r'^profile\.wifi\.[^\n]*\[redacted\] \(saved ')

    def test_owner_profile_prompt_reports_omitted_rows(self):
        for index in range(PROFILE_SNAPSHOT_CHARS // 60):
            self.remember(f'profile.preference.item{index}', ('기억 내용 ' * 8) + str(index))
        text = self.service.owner_profile_snapshot()
        self.assertIn('saved profile facts omitted by the context limit; use search_memory if relevant', text)

    def test_settings_copy_says_profile_text_reaches_the_ai_and_excludes_secrets(self):
        html = (WEB / 'index.html').read_text(encoding='utf-8')
        section = html[html.index('id="owner-profile"'):html.index('id="saved-memory"')]
        self.assertIn('요청할 때마다 AI에게 함께 보내므로 비밀번호, 토큰, API 키는 적지 마세요', section)
        self.assertIn('비밀번호나 키는 적지 마세요', section[section.index('owner-profile-form'):])

    def test_the_profile_surface_needs_the_owner_session(self):
        from http.cookiejar import CookieJar
        from urllib.request import HTTPCookieProcessor, build_opener
        stranger = build_opener(HTTPCookieProcessor(CookieJar()))
        self.assertNotEqual(self.refused('/api/personal-space/profile', opener=stranger)[0], 200)
        self.assertNotEqual(self.refused('/api/personal-space/profile/request',
                                         {'operation': 'remember', 'memory_key': 'profile.x', 'content': 'y'},
                                         opener=stranger)[0], 200)
        self.assertEqual(self.store.memories(), [])


UI_CHECK = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const app=fs.readFileSync(process.argv[1],'utf8'),ids=new Map();
class Element {
 constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.attrs={};this.className='';this.hidden=false;this._text='';}
 set id(value){this._id=value;ids.set(value,this);} get id(){return this._id;}
 set textContent(value){this._text=String(value);this.children=[];} get textContent(){return this._text+this.children.map(node=>typeof node==='string'?node:node.textContent).join('');}
 append(...nodes){this.children.push(...nodes);} replaceChildren(...nodes){this._text='';this.children=[];this.append(...nodes);}
 setAttribute(key,value){this.attrs[key]=value;} focus(){} get isConnected(){return true;}
 get classList(){const node=this;return {toggle(name,on){node._cls=Boolean(on);},add(){},remove(){},contains:()=>Boolean(node._cls)};}
 querySelector(){return null;} querySelectorAll(){return [];}
}
function descendants(node){return node.children.flatMap(child=>typeof child==='string'?[]:[child,...descendants(child)]);}
for(const id of ['owner-profile-list','owner-profile-feedback','saved-memory-list','saved-memory-feedback'])new Element('div').id=id;
const $=id=>ids.get(id),document={getElementById:$,createElement:tag=>new Element(tag)};
const part=(start,end)=>app.slice(app.indexOf(start),app.indexOf(end)),line=start=>{const at=app.indexOf(start);return app.slice(at,app.indexOf('\n',at)+1);};
const source=part('const LANGUAGES=','function normalizeEndpoint(')+line('function formatTimeParts(')+part('function element(','function setError(')+
 'let ownerProfile=null,ownerProfileLoadSequence=0,ownerProfileEdit=null,ownerProfileDeletePending=null,savedMemory=null;function __setSaved(rows){savedMemory=rows;}\n'+
 part('function renderSavedMemory(','// Reload the list on the existing poll')+part('const PROFILE_PREFIX=','function rememberWorkspaceResultSummary(');
const calls=[];let refreshes=0;
const ctx={document,$,console,calls,
 api:async(path,body,method)=>{calls.push({path,body,method:method||(body===undefined?'GET':'POST')});if(path==='/api/personal-space/profile')return {memories:ctx.profileRows};return {deleted:true};},
 refresh:async()=>{refreshes++;},busy:async(button,fn)=>fn(),loadSavedMemory:async()=>{},
 setError:(id,error)=>{$(id).textContent=error?.message||String(error||'');},setFeedback:(id,text)=>{$(id).textContent=text||'';},
 itemLink:(kind,id,text,className)=>{const a=new Element('a');a.textContent=text;a.className=className;a.href='#item/'+kind+'/'+id;return a;},
 itemTitle:row=>row.memory_key||row.content,itemLabel:kind=>kind==='memory'?'기억':'메모'};
vm.createContext(ctx);vm.runInContext(source,ctx);vm.runInContext("setLanguage('ko')",ctx);
const rows=()=>$('owner-profile-list').children.filter(node=>node.tag==='div'&&node.className.split(' ')[0]==='settings-row');
const texts=(node,cls)=>descendants(node).filter(n=>n.className===cls).map(n=>n.textContent);
const buttons=node=>descendants(node).filter(n=>n.tag==='button').map(n=>n.textContent);
(async()=>{
 // Empty group: an honest empty state, nothing invented.
 ctx.profileRows=[];await ctx.loadOwnerProfile();
 assert.equal($('owner-profile-list').children.length,1);assert.equal($('owner-profile-list').children[0].className,'settings-empty');
 // Rows: title without the namespace, content with the saved time, the 프로필 state, and 수정/삭제/열기.
 ctx.profileRows=[{id:'m1',memory_key:'profile.allergy.peanut',content:'땅콩 알러지',created:1700000000},{id:'m2',memory_key:'profile.place.home',content:'서울 마포구',created:1700000000}];
 await ctx.loadOwnerProfile();
 assert.deepEqual(rows().map(r=>texts(r,'settings-row-title')[0]),['allergy.peanut','place.home']);
 assert(texts(rows()[0],'settings-row-description')[0].startsWith('땅콩 알러지 · '));
 assert.deepEqual(rows().map(r=>texts(r,'settings-state neutral')[0]),['프로필','프로필']);
 assert.deepEqual(buttons(rows()[0]),['수정','삭제']);
 assert.equal(descendants(rows()[0]).find(n=>n.tag==='a').href,'#item/memory/m1');
 // Delete is two presses: the first press asks, calls nothing; the second deletes through the existing Memory delete.
 const first=descendants(rows()[0]).find(n=>n.tag==='button'&&n.textContent==='삭제');await first.onclick({currentTarget:first});
 assert.equal(calls.filter(c=>c.method==='DELETE').length,0,'first press deletes nothing');
 assert.deepEqual(buttons(rows()[0]),['수정','취소','삭제 확인']);
 assert(texts(rows()[0],'confirm-text')[0].includes('되돌릴 수 없습니다'));
 assert.equal(buttons(rows()[1]).includes('삭제 확인'),false,'only the pressed row is armed');
 const confirm=descendants(rows()[0]).find(n=>n.tag==='button'&&n.textContent==='삭제 확인');ctx.profileRows=[ctx.profileRows[1]];await confirm.onclick({currentTarget:confirm});
 assert.deepEqual(calls.filter(c=>c.method==='DELETE').map(c=>c.path),['/api/personal-space/memories/m1']);
 assert.equal($('owner-profile-feedback').textContent,'지웠습니다.');assert.equal(rows().length,1);assert(refreshes>0);
 // Edit: the key is fixed, the value is editable, saving is the keyed owner write to the same key.
 const edit=descendants(rows()[0]).find(n=>n.tag==='button'&&n.textContent==='수정');edit.onclick({currentTarget:edit});
 const form=descendants($('owner-profile-list')).find(n=>n.tag==='form');assert(form,'an inline edit form');
 const area=descendants(form).find(n=>n.tag==='textarea');assert.equal(area.value,'서울 마포구');
 assert.equal(descendants($('owner-profile-list')).some(n=>n.tag==='input'),false,'the key is not re-typed on edit');
 area.value='서울 마포구 합정동';await form.onsubmit({preventDefault(){}});
 const saved=calls.find(c=>c.path==='/api/personal-space/profile/request');
 assert.equal(JSON.stringify(saved.body),JSON.stringify({operation:'remember',memory_key:'profile.place.home',content:'서울 마포구 합정동'}));
 assert.equal($('owner-profile-feedback').textContent,'프로필을 저장했습니다.');
 assert.equal(descendants($('owner-profile-list')).some(n=>n.tag==='form'),false,'the edit form closes after saving');
 // The 기억과 메모 list labels a profile row 프로필 and any other Memory 기억.
 ctx.__setSaved({items:[{id:'m2',type:'memory',memory_key:'profile.place.home',content:'서울',created:1},{id:'m3',type:'memory',memory_key:'meeting-time',content:'mornings',created:1},{id:'n1',type:'note',content:'우유',created:1}],has_more:false});
 ctx.renderSavedMemory();
 assert.deepEqual(descendants($('saved-memory-list')).filter(n=>n.className.startsWith('settings-state')).map(n=>n.textContent),['프로필','기억','메모']);
 // Key shape only: the helper never judges content.
 for(const key of ['profile.allergy','profile.place.home','profile.알러지'])assert(ctx.isProfileKey(key),key);
 for(const key of ['profile','profile.','allergy','profile.a b',null])assert(!ctx.isProfileKey(key),String(key));
 console.log('ok');
})().catch(error=>{console.error(error);process.exit(1);});
"""


class ProfileSettingsUi(unittest.TestCase):
    def test_profile_group_lists_edits_and_deletes_with_the_existing_grammar(self):
        node = shutil.which('node')
        if node is None:
            raise unittest.SkipTest('Node is needed for the DOM-stub check')
        result = subprocess.run([node, '-e', UI_CHECK, str(WEB / 'app.js')], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip().splitlines()[-1], 'ok')


if __name__ == '__main__':
    unittest.main()
