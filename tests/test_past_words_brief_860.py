"""#860: the brief's request-driven recall reads the request together with the owner's preceding turn.

A short follow-up ("그거 몇 시였지?") shares no word with the fact it refers to.  The profile
section (matching profile rows, the earlier-exchange block, the daily records) is therefore
searched with the request first and the owner's preceding turn of the same conversation after it.

Evidence class: unit / controlled local integration over a temporary store, with an injected
model transport.  No model, network or owner data; all content is synthetic.  Every positive
case has a control without the context, so a test fails if the context is not what recalls.
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

    def snapshot(self, service, job, *, with_context):
        context = service.preceding_owner_turn(job) if with_context else None
        return service.owner_profile_snapshot(job['message'], **({'context': context} if with_context else {}))

    # -- 1. the profile rows ---------------------------------------------------
    def test_profile_fact_of_the_preceding_turn_is_recalled_only_with_the_context(self):
        service = AgentService(self.store)
        self.big_profile()
        self.exchange(EARLIER_TURN, '예약은 오후 3시 30분이에요')
        job = self.follow_up()
        without = service.owner_profile_snapshot(job['message'])
        self.assertNotIn(DENTIST_FACT, without, 'control: the profile is large enough that the row is omitted')
        self.assertIn('omitted by the context limit', without)
        self.assertEqual(service.preceding_owner_turn(job), EARLIER_TURN)
        recalled = service.owner_profile_snapshot(job['message'], context=service.preceding_owner_turn(job))
        self.assertIn(DENTIST_FACT, recalled)
        # context=None is today's behaviour exactly.
        self.assertEqual(service.owner_profile_snapshot(job['message'], context=None), without)
        self.assertEqual(service.owner_profile_snapshot(job['message'], context=''), without)

    def test_english_follow_up_recalls_through_the_preceding_turn(self):
        service = AgentService(self.store)
        self.big_profile()
        self.store.save_memory('profile.zz-passport', 'passport renewal appointment is on the 14th (synthetic)', owner_id=OWNER)
        self.exchange('When is my passport renewal appointment?', 'On the 14th')
        job = self.follow_up('and what time was that?')
        self.assertNotIn('passport renewal', service.owner_profile_snapshot(job['message']))
        self.assertIn('passport renewal appointment is on the 14th',
                      service.owner_profile_snapshot(job['message'], context=service.preceding_owner_turn(job)))

    def test_the_request_comes_first_and_the_query_is_bounded(self):
        service = AgentService(self.store)
        seen = []
        original = service.store.search_conversation

        def spy(query, **kwargs):
            seen.append(query)
            return original(query, **kwargs)
        with mock.patch.object(service.store, 'search_conversation', side_effect=spy):
            service.owner_profile_snapshot('앞의 요청', context='뒤의 문맥 ' + 'x' * 800)
        self.assertEqual(len(seen), 1, 'one query feeds the earlier-exchange read')
        self.assertTrue(seen[0].startswith('앞의 요청'))
        self.assertIn('뒤의 문맥', seen[0])
        self.assertLessEqual(len(seen[0]), 500)

    # -- 2. the earlier-exchange block -----------------------------------------
    def test_old_exchange_sharing_words_with_the_preceding_turn_is_recalled_only_with_the_context(self):
        service = AgentService(self.store)
        old = self.exchange('치과 예약 시간 메모해줘', '오후 3시 30분으로 적어 둘게요')
        self.pad()
        self.exchange(EARLIER_TURN, '다시 확인해 볼게요')
        job = self.follow_up()
        without = service.owner_profile_snapshot(job['message'])
        self.assertNotIn('치과 예약 시간 메모해줘', without)
        recalled = service.owner_profile_snapshot(job['message'], context=service.preceding_owner_turn(job))
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
        recalled = service.owner_profile_snapshot(job['message'], context=service.preceding_owner_turn(job))
        self.assertIn('newvaluemarker', recalled, 'control: the current row of the same topic is recalled')
        for marker in controls:
            self.assertIn(marker, recalled, 'eligible copies must be found through the preceding owner turn')
        self.assertIn(visible, [ref['work_id'] for ref in conversation_block_refs(recalled)])
        for marker in ('forgotmarker', 'oldvaluemarker', 'expiredmarker', 'transcriptmarker', 'answermarker'):
            self.assertNotIn(marker, recalled)

    # -- 4. preceding_owner_turn eligibility ----------------------------------------
    def test_preceding_turn_is_the_latest_eligible_owner_message_of_the_same_conversation(self):
        service = AgentService(self.store)
        self.assertEqual(service.preceding_owner_turn(self.follow_up()), '', 'no earlier turn')
        self.exchange('first eligible turn', 'first answer')
        # Not owner-typed (a connector or machine request), another conversation, and a forgotten turn.
        self.exchange('machine generated request', typed=False)
        self.exchange('other chat turn', channel='telegram', chat_id=777)
        hidden = self.exchange('forgotten turn', 'answer to forgotten')
        OwnerForget(self.store).forget(OWNER, f'message:{self.message_id(hidden)}')
        job = self.follow_up()
        # The assistant's own words are never the owner's preceding turn: the latest assistant row is a reply.
        self.assertEqual(service.preceding_owner_turn(job), 'first eligible turn')

    def test_a_document_workspace_turn_and_the_requests_own_row_are_not_the_preceding_turn(self):
        service = AgentService(self.store)
        self.exchange('real earlier turn')
        document = self.exchange('document workspace request')
        self.store.put('file_workspace_document_jobs', [document])
        job = self.follow_up('다음 질문')
        self.assertEqual(service.preceding_owner_turn(job), 'real earlier turn')

    def test_preceding_turn_never_raises(self):
        service = AgentService(self.store)
        self.exchange('earlier turn')
        job = self.follow_up()
        with mock.patch.object(self.store, 'db', side_effect=RuntimeError('store down')):
            self.assertEqual(service.preceding_owner_turn(job), '')
        self.assertEqual(service.preceding_owner_turn({}), '')
        self.assertEqual(service.preceding_owner_turn(None), '')

    # -- the two brief callers --------------------------------------------------------
    def test_early_work_plan_builds_its_profile_section_with_the_preceding_turn(self):
        service = AgentService(self.store)
        self.big_profile()
        self.exchange(EARLIER_TURN, '예약은 오후 3시 30분이에요')
        job = self.follow_up()
        captured = {}

        def capture(job_, prompt, history, sections, *args, **kwargs):
            captured.update(sections)
            return None
        with mock.patch.object(service, 'work_orchestration', side_effect=capture):
            service.early_work_plan(job, job['message'])
        self.assertIn(DENTIST_FACT, captured['profile'])
        # The same brief for the same request without a preceding turn does not carry it (control).
        self.assertNotIn(DENTIST_FACT, service.owner_profile_snapshot(job['message']))

    def test_worker_brief_carries_the_recalled_row_and_the_information_use_record_names_it(self):
        sent = []

        def script(messages):
            sent.append(json.dumps(messages, ensure_ascii=False))
            return {'choices': [{'message': {'content': '오후 3시 30분이에요'}}]}
        service = self.service(script)
        self.big_profile()
        old = self.exchange('치과 예약 시간 메모해줘', '오후 3시 30분으로 적어 둘게요')
        self.pad()
        self.exchange(EARLIER_TURN, '다시 확인해 볼게요')
        work = self.store.enqueue(FOLLOW_UP, str(uuid.uuid4()), owner_typed=True, owner_words=True)
        self.assertTrue(service.run_one())
        brief = '\n'.join(sent)
        self.assertIn('profile.a000', brief, 'control: the profile section is in the worker brief at all')
        # Real brief path (_prepare_worker_turn): the follow-up has no word in common with either source.
        self.assertIn(DENTIST_FACT, brief)
        self.assertIn('치과 예약 시간 메모해줘', brief)
        # Information use: exactly the rows surfaced because of the context are named by the existing record.
        record = self.store.turn_provenance(work)['owner_information']
        self.assertIn(DENTIST_KEY, record['profile_keys'])
        self.assertIn(old, [ref['work_id'] for ref in record['earlier_exchanges']])
        audit = service.work_information_use(work)
        self.assertIn(old, [ref['work_id'] for ref in audit['sent_to']['earlier_exchanges']['worker_sections']])
