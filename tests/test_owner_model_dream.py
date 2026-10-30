"""#402: the once-a-day look back (the "dream") over recent requests.

A third kind of row on the #805 owner-model upkeep queue: the same tick, single flight, pause
switch and rolling cap.  It is enqueued only when due (interval, enough requests, idle, night or
late), the Judgment AI reads the scrubbed turns since the last one, and Still keeps what passes its
deterministic checks: cited facts (two occasions for an inferred one), no mass ending, working
notes resting on two separate turns, and a bounded daily record outside ``profile.``.

Evidence class: model-free unit and service tests.  A scripted ``FixtureDecisionEngine`` plays the
Judgment AI; the real ``AgentService`` tick, ``QuickStore`` Memory paths and the Work loop of
``tests/test_orchestrator.Harness`` run unchanged.  No live model.
"""
import json
import time
import unittest

from personal_agent import owner_model as om
from personal_agent.agent_runtime import MEMORY_OWNER
from personal_agent.decision import OUTCOME_DECIDED, BinaryDecision, FixtureDecisionEngine, StructuredDecision, fixture_confidence
from personal_agent.memory_service import MemoryService

from test_orchestrator import Harness

SECRET = 'plain-stored-secret-402'
GENERATION = 7
CHAT = 4242
HOUR = 3600


def fact(key, content, *sources, kind='stated', category='plan', invalidates=(), valid_until=''):
    """One dream proposal; each ``invalidates`` entry is ``(id, reason[, because[, turn]])``, a restate by default."""
    ends = [{'id': end[0], 'reason': end[1], 'because': end[2] if len(end) > 2 else om.END_RESTATES,
             'turn': end[3] if len(end) > 3 else ''} for end in invalidates]
    return {'category': category, 'kind': kind, 'memory_key': key, 'content': content, 'evidence': 'the owner said so',
            'supersedes_key': '', 'invalidates': ends, 'valid_until': valid_until, 'sources': list(sources)}


def dream(proposals=(), notes='', change='', notes_sources=(), diary=''):
    return {'proposals': list(proposals), 'notes': notes, 'change': change, 'notes_sources': list(notes_sources),
            'diary': diary}


class Dream(Harness):
    def setUp(self):
        super().setUp()
        self.dreams, self.asked, self.gathers, self.gathered = [], [], [], []

        def structured(context, question, schema):
            if context.purpose == om.DREAM_GATHER_PURPOSE:
                self.gathered.append((context, question, schema))
                if not self.gathers:
                    return None
                return StructuredDecision(OUTCOME_DECIDED, {'proposals': self.gathers.pop(0)}, fixture_confidence(0.9))
            if context.purpose == om.DREAM_PURPOSE:
                self.asked.append((context, question, schema))
                if not self.dreams:
                    return None
                return StructuredDecision(OUTCOME_DECIDED, self.dreams.pop(0), fixture_confidence(0.9))
            return None  # no plan, and upkeep proposes nothing

        def judge(context, proposition):
            if context.purpose == 'goal-reached':
                return BinaryDecision(OUTCOME_DECIDED, True, fixture_confidence())
            return None
        self.service.use_decision_engine(FixtureDecisionEngine(structured=structured, judge=judge))
        self.service.owner_model_spawn = lambda target: target()
        self.hour = 3
        self.service._owner_local_hour = lambda now: self.hour
        self.upkeep = self.service.owner_model

    def settled(self, text, answer='an answer', status='succeeded', provider='subscription', upkept=True, telegram=False,
                owner_words=True):
        """A settled Work a worker AI answered; with ``upkept`` its upkeep row is already done."""
        # #423: the owner's own words, typed on a conversation surface or declared by an API caller; without
        # ``owner_words`` it is a bare API request (a developer's verification call).
        options = {'channel': f'telegram:{GENERATION}', 'chat_id': CHAT} if telegram else {'owner_words': owner_words}
        job = self.store.enqueue(text, f'dr-{time.time()}-{text}', owner_typed=True, **options)
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status=?,response=?,provider=?,model='codex',delivery='sent' WHERE id=?",
                       (status, answer, provider, job))
            if upkept:
                self.upkeep.enqueue(db, job)
                db.execute("UPDATE owner_model_upkeep SET state='done' WHERE job_id=?", (job,))
        return job

    def day(self, *texts):
        return [self.settled(text) for text in texts]

    def tick(self, now=None):
        self.upkeep.dream_checked = None
        return self.service.run_owner_model_upkeep(now if now is not None else time.time() + 1)

    def dream_rows(self):
        with self.store.db() as db:
            return [dict(row) for row in db.execute('SELECT * FROM owner_model_upkeep WHERE kind=? ORDER BY created',
                                                    (om.KIND_DREAM,))]

    def evidence(self, job):
        with self.store.db() as db:
            rows = db.execute('SELECT status,detail FROM tool_events WHERE job_id=? AND tool=?',
                              (job, om.DREAM_EVENT_TOOL)).fetchall()
        return [(row['status'], json.loads(row['detail'])) for row in rows]

    def profile(self, key, content, **terms):
        return self.store.save_memory(key, content, MEMORY_OWNER, **terms)


class Due(Dream):
    def test_it_waits_for_enough_requests_then_runs_once_on_the_newest(self):
        self.day('첫 요청', '둘째 요청')
        self.assertIsNone(self.upkeep.enqueue_dream_if_due(time.time() + 1, 3))
        newest = self.settled('셋째 요청')
        self.upkeep.dream_checked = None
        self.assertEqual(self.upkeep.enqueue_dream_if_due(time.time() + 1, 3), newest)
        [row] = self.dream_rows()
        self.assertEqual((row['job_id'], row['state']), (newest, om.STATE_PENDING))

    def test_the_anchor_is_the_newest_request_the_owner_can_be_told_on(self):
        self.store.put('telegram', {'enabled': True, 'user_id': CHAT, 'generation': GENERATION})
        self.settled('a')
        told = self.settled('b', telegram=True)
        self.settled('c')
        self.assertEqual(self.upkeep.enqueue_dream_if_due(time.time() + 1, 3), told)

    def test_after_a_dream_the_next_waits_the_interval_and_prefers_the_night(self):
        self.day('a', 'b', 'c')
        start = time.time() + 1
        self.assertIsNone(self.upkeep.enqueue_dream_if_due(start, 14), '#425: the first look back waits for the night too')
        self.upkeep.dream_checked = None
        self.assertIsNotNone(self.upkeep.enqueue_dream_if_due(start, 3))
        with self.store.db() as db:
            db.execute("UPDATE owner_model_upkeep SET state='done' WHERE kind=?", (om.KIND_DREAM,))
        later = start + 21 * HOUR
        for text in ('d', 'e', 'f'):
            job = self.settled(text, upkept=False)
            self.upkeep.enqueue(None, job, now=later - 60)
            with self.store.db() as db:
                db.execute("UPDATE owner_model_upkeep SET state='done' WHERE job_id=?", (job,))
        self.assertIsNone(self.upkeep.enqueue_dream_if_due(start + HOUR, 3), 'not again within the interval')
        self.upkeep.dream_checked = None
        self.assertIsNone(self.upkeep.enqueue_dream_if_due(later, 14), 'by day it waits for the night')
        self.upkeep.dream_checked = None
        self.assertIsNotNone(self.upkeep.enqueue_dream_if_due(later, 3))
        self.upkeep.dream_checked = None
        with self.store.db() as db:
            db.execute("UPDATE owner_model_upkeep SET state='done' WHERE kind=?", (om.KIND_DREAM,))
        # Long after, it runs whatever the hour.
        late = later + 37 * HOUR
        for text in ('g', 'h', 'i'):
            job = self.settled(text, upkept=False)
            self.upkeep.enqueue(None, job, now=late - 60)
            with self.store.db() as db:
                db.execute("UPDATE owner_model_upkeep SET state='done' WHERE job_id=?", (job,))
        self.assertIsNotNone(self.upkeep.enqueue_dream_if_due(late, 14))

    def test_paused_switched_off_busy_or_capped_enqueues_nothing(self):
        self.day('a', 'b', 'c')
        now = time.time() + 1
        self.upkeep.set_controls({'dream': False})
        self.assertIsNone(self.upkeep.enqueue_dream_if_due(now, 3))
        self.upkeep.set_controls({'dream': True, 'enabled': False})
        self.upkeep.dream_checked = None
        self.assertIsNone(self.upkeep.enqueue_dream_if_due(now, 3))
        self.upkeep.set_controls({'enabled': True, 'daily_calls': 0})
        self.upkeep.dream_checked = None
        self.assertIsNone(self.upkeep.enqueue_dream_if_due(now, 3))
        self.upkeep.set_controls({'daily_calls': 20})
        busy = self.store.enqueue('지금 하는 일', 'dr-busy')
        self.upkeep.dream_checked = None
        self.assertIsNone(self.upkeep.enqueue_dream_if_due(now, 3), 'never beside a queued Work')
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='succeeded' WHERE id=?", (busy,))
        self.upkeep.enqueue(None, busy)
        self.upkeep.dream_checked = None
        self.assertIsNone(self.upkeep.enqueue_dream_if_due(now, 3), 'a pending upkeep goes first')
        self.assertEqual(self.dream_rows(), [])

    def test_the_tick_asks_at_most_once_a_minute(self):
        self.day('a', 'b', 'c')
        now = time.time() + 1
        self.upkeep.set_controls({'dream': False})
        self.assertIsNone(self.upkeep.enqueue_dream_if_due(now, 3))
        self.upkeep.set_controls({'dream': True})
        self.assertIsNone(self.upkeep.enqueue_dream_if_due(now + 30, 3), 'asked again too soon')
        self.assertIsNotNone(self.upkeep.enqueue_dream_if_due(now + om.DREAM_CHECK_SECONDS, 3))

    def test_the_dream_switch_is_an_owner_setting(self):
        self.assertTrue(self.service.owner_model_request({'operation': 'read'})['dream'])
        self.assertFalse(self.service.owner_model_request({'operation': 'set', 'dream': False})['dream'])
        with self.assertRaises(ValueError):
            self.service.owner_model_request({'operation': 'set', 'dream': 'off'})


class Run(Dream):
    def test_cited_works_survive_both_dream_passes_and_replace_the_anchor_source(self):
        from personal_agent.agent_runtime import Capabilities
        from personal_agent.quickstart_store import QuickStore
        first, second, anchor = self.day('출근은 지하철로 해', '늘 지하철로 출근해', '오늘 일정 알려줘')
        self.gathers = [[fact('profile.routine.commute', '지하철 출근', '[t1]', 't2', 't1')]]
        self.dreams = [dream([fact('profile.preference.transport', '대중교통을 선호함', 't2', 't1', kind='inferred')])]
        self.assertTrue(self.tick())
        # Reopen the real store: citations are durable, rather than only in the event or runtime.
        reopened = QuickStore(self.tmp / 'state')
        rows = reopened.memories(MEMORY_OWNER, key_prefix='profile.')
        self.assertEqual(len(rows), 2)
        expected = {'profile.routine.commute': [first, second], 'profile.preference.transport': [second, first]}
        for row in rows:
            with reopened.db() as db:
                raw = db.execute('SELECT source_works,work_key FROM memories WHERE id=?', (row['id'],)).fetchone()
            self.assertEqual(json.loads(raw['source_works']), expected[row['memory_key']])
            self.assertEqual(raw['work_key'], reopened._work_binding(anchor))
            sources = reopened.memory_sources([row['id']])[row['id']]
            self.assertEqual([source['work_id'] for source in sources['sources']], expected[row['memory_key']])
            self.assertNotEqual(sources['work_id'], anchor)
        detail = self.evidence(anchor)[0][1]
        self.assertEqual([row['source_works'] for row in detail['applied']], [[first, second], [second, first]])
        self.assertNotIn('지하철', json.dumps(detail, ensure_ascii=False))
        capabilities = Capabilities(reopened, None, {}, '', anchor, lambda *args: None,
                                    allowed_tools=['list_memory', 'search_memory'])
        for result in (capabilities.execute('list_memory', {}),
                       capabilities.execute('search_memory', {'query': '지하철'})):
            self.assertTrue(result['memories'])
            for row in result['memories']:
                self.assertEqual(row['source_status'], 'found')
                self.assertEqual([source['work_id'] for source in row['sources']], expected[row['memory_key']])
                self.assertEqual(row['source']['work_id'], expected[row['memory_key']][0])

    def test_owner_memory_lists_mark_dream_rows_only(self):
        self.day('내 직장은 판교야', '둘째 요청', '셋째 요청')
        self.dreams = [dream([fact('profile.place.work', '판교', 't1')])]
        self.assertTrue(self.tick())
        ordinary = self.store.save_memory('profile.place.home', '서울', MEMORY_OWNER)
        for rows in (self.store.personal_records(record_filter='saved')['items'],
                     self.store.personal_space()['memories'],
                     self.service.memory_profile_request({'operation': 'list'})['memories']):
            by_id = {row['id']: row for row in rows}
            self.assertNotIn('origin', by_id[ordinary['id']])
            dream_rows = [row for row in rows if row['id'] != ordinary['id']]
            self.assertEqual([row['origin'] for row in dream_rows], [om.ORIGIN_DREAM])

    def test_one_dream_saves_cited_facts_notes_and_the_day_record_on_the_newest_request(self):
        first, second, newest = self.day('내년 3월까지 자동차 할부를 매달 45만 원씩 갚고 있어', '답은 표로 정리해줘',
                                         '이번에도 표로 보여줘')
        self.dreams = [dream([fact('profile.plan.car_installment', '2027년 3월까지 자동차 할부 매달 45만 원', 't1',
                                   valid_until='2027-03-31')],
                             notes='- 비교나 정리는 표로 보여 준다', change='표로 정리하는 습관을 적음',
                             notes_sources=['t2', 't3'],
                             diary='2026-10-29: 자동차 할부 계획을 말했고 정리를 두 번 표로 받았다.')]
        self.assertTrue(self.tick())
        [(context, question, schema)] = self.asked
        self.assertEqual(question, om.DREAM_QUESTION)
        self.assertEqual((context.strongest, context.timeout), (True, om.DREAM_TIMEOUT_SECONDS),
                         "#420: the dream asks for the engine's strongest model")
        self.assertEqual(schema, om.DREAM_SCHEMA)
        turns = context.facts['turns']
        self.assertRegex(turns, r'^\[t1\] \d{4}-\d{2}-\d{2} \d{2}:\d{2} owner: 내년 3월까지')
        self.assertRegex(turns, r'\[t3\] [0-9: -]+ owner: 이번에도 표로')
        self.assertEqual(context.work_id, newest)
        [plan] = self.store.memories(MEMORY_OWNER, key_prefix='profile.')
        self.assertEqual((plan['memory_key'], plan['valid_until']), ('profile.plan.car_installment', '2027-03-31'))
        with self.store.db() as db:
            work_key = db.execute('SELECT work_key FROM memories WHERE id=?', (plan['id'],)).fetchone()[0]
        self.assertEqual(work_key, self.store._work_binding(newest), 'kept facts are attributed to the newest request it covers')
        self.assertEqual(plan['origin'], om.ORIGIN_DREAM, '#425: written by the look back, outside the request\'s notice')
        self.assertEqual(om.WorkingNotes(self.store).current()['points'], ['비교나 정리는 표로 보여 준다'])
        [diary] = self.store.memories(MEMORY_OWNER, key_prefix=om.DIARY_PREFIX)
        self.assertTrue(diary['memory_key'].startswith(om.DIARY_PREFIX))
        [(status, detail)] = self.evidence(newest)
        self.assertEqual(status, om.EVENT_RECORDED)
        self.assertEqual((len(detail['applied']), detail['notes']['outcome'], detail['diary']['outcome']),
                         (1, om.APPLIED_NOTES, om.APPLIED_MEMORY))
        self.assertEqual(detail['period'], {'works': 3, 'turns': 3})
        self.assertNotIn('할부', json.dumps(detail, ensure_ascii=False), 'Evidence keeps no text')
        self.assertEqual(self.dream_rows()[0]['state'], om.STATE_DONE)

    def test_the_day_record_keeps_one_line_per_event(self):
        self.day('a', 'b', 'c')
        self.dreams = [dream(diary='- 2026-10-13 20:20 예약매수 요청, 접수 확인 안 됨\n\n-  2026-10-13   21:00 장바구니 담기 완료')]
        self.tick()
        [diary] = self.store.memories(MEMORY_OWNER, key_prefix=om.DIARY_PREFIX)
        self.assertEqual(diary['content'], '- 2026-10-13 20:20 예약매수 요청, 접수 확인 안 됨\n- 2026-10-13 21:00 장바구니 담기 완료')

    def test_the_day_record_is_found_by_search_but_never_read_by_a_brief(self):
        self.day('a', 'b', 'c')
        self.dreams = [dream(diary='2026-10-29: 이사 준비로 부동산 세 곳을 비교했다.')]
        self.tick()
        snapshot = self.service.owner_profile_snapshot('이사 준비', notes=True)
        self.assertNotIn('부동산', snapshot)
        memory = MemoryService(self.store, private_read_sink=MemoryService.NO_EGRESS_GUARD)
        found = memory.search_memories(MEMORY_OWNER, '부동산')['memories']
        self.assertEqual([row['memory_key'][:len(om.DIARY_PREFIX)] for row in found], [om.DIARY_PREFIX])

    def test_turns_leave_out_what_upkeep_never_reads(self):
        self.settled('명령 응답', provider='builtin')
        self.settled('비밀 들어간 요청 ' + SECRET)
        self.store.secret('telegram_token', SECRET)
        self.day('a', 'b')
        self.dreams = [dream()]
        self.tick()
        turns = self.asked[0][0].facts['turns']
        self.assertNotIn('명령 응답', turns)
        self.assertNotIn(SECRET, turns)

    def test_a_bare_api_request_is_never_a_turn_or_part_of_the_record(self):
        """#423, the live case: verification calls through ``/api/chat`` were summarised as the owner's requests."""
        self.settled('검증용 좌석 확인 요청', upkept=False, owner_words=False)
        self.day('a', 'b', 'c')
        self.dreams = [dream(diary='기록')]
        self.tick()
        turns = self.asked[0][0].facts['turns']
        self.assertNotIn('좌석', turns)
        self.assertEqual(turns.count('owner:'), 3)

    def test_a_merge_of_duplicates_ends_the_other_row_and_keeps_one(self):
        spouse = self.profile('profile.identity.spouse', '배우자가 있음')
        marital = self.profile('profile.identity.marital_status', '배우자가 있음')
        self.day('a', 'b', '아내랑 저녁 먹으러 가')
        self.dreams = [dream([fact('profile.identity.spouse', '배우자가 있음', spouse['id'][:8], marital['id'][:8],
                                   category='identity', invalidates=[(marital['id'][:8], 'same fact under another key')])])]
        self.tick()
        current = {row['memory_key']: row for row in self.store.memories(MEMORY_OWNER, key_prefix='profile.')}
        self.assertEqual(list(current), ['profile.identity.spouse'])
        self.assertEqual(current['profile.identity.spouse']['supersedes'], spouse['id'])

    def test_a_paused_owner_model_writes_nothing_from_a_dream_already_asked(self):
        self.day('a', 'b', 'c')
        self.dreams = [dream([fact('profile.plan.trip', '11월 제주 여행', 't1')], diary='기록')]
        original = self.upkeep.run_dream

        def paused(*args, **kwargs):
            self.upkeep.set_controls({'enabled': False})
            return original(*args, **kwargs)
        self.upkeep.run_dream = paused
        self.tick()
        self.assertEqual(self.store.memories(MEMORY_OWNER), [])


class TwoPasses(Dream):
    """#416: the first pass only finds facts; the look back reads the profile with them and tidies."""

    def test_the_first_pass_saves_found_facts_before_the_look_back_reads_the_profile(self):
        self.day('주말엔 보통 자가용으로 다녀', '집에서 김치찌개 끓여 먹었어', '오늘도 차로 갈게')
        self.gathers = [[fact('profile.routine.car', '주말에는 보통 자가용으로 이동함', 't1', 't3', kind='inferred',
                              category='routine'),
                         fact('profile.preference.cooking', '집에서 요리해 먹음', 't2', category='preference',
                              invalidates=[('aaaaaaa1', 'not in this pass')])]]
        self.dreams = [dream()]
        self.tick()
        [(gather_context, question, schema)] = self.gathered
        self.assertEqual((question, schema), (om.DREAM_GATHER_QUESTION, om.DREAM_GATHER_SCHEMA))
        self.assertEqual((gather_context.strongest, gather_context.timeout), (True, om.DREAM_TIMEOUT_SECONDS))
        self.assertRegex(gather_context.facts['turns'], r'\[t1\] [0-9: -]+ owner: 주말엔 보통 자가용으로')
        saved = {row['memory_key'] for row in self.store.memories(MEMORY_OWNER, key_prefix='profile.')}
        self.assertEqual(saved, {'profile.routine.car', 'profile.preference.cooking'})
        [(look_context, _question, _schema)] = self.asked
        self.assertIn('주말에는 보통 자가용으로 이동함', look_context.facts['owner_profile'],
                      'the look back reads what the first pass saved')
        [(_status, detail)] = self.evidence(self.dream_rows()[0]['job_id'])
        self.assertEqual((detail['gather']['proposed'], len(detail['gather']['applied']), len(detail['applied'])), (2, 2, 2))

    def test_the_first_pass_keeps_up_to_its_own_limit(self):
        self.day(*[f'요청 {i}' for i in range(3)])
        self.gathers = [[fact(f'profile.plan.item{i}', f'계획 {i}', 't1') for i in range(om.DREAM_GATHER_LIMIT + 2)]]
        self.dreams = [dream()]
        self.tick()
        self.assertEqual(len(self.store.memories(MEMORY_OWNER, key_prefix='profile.')), om.DREAM_GATHER_LIMIT)
        self.assertGreater(om.DREAM_GATHER_LIMIT, om.MAX_PROPOSALS)

    def test_an_unavailable_first_pass_still_lets_the_look_back_run(self):
        self.day('a', 'b', 'c')
        self.dreams = [dream(diary='2026-10-29: 기록')]
        self.tick()
        self.assertEqual(len(self.gathered), 1)
        self.assertEqual(len(self.store.memories(MEMORY_OWNER, key_prefix=om.DIARY_PREFIX)), 1)


class Gates(unittest.TestCase):
    """``validate_dream``: Still's deterministic checks on one dream's facts."""
    refs = {'t1': '나는 판교에서 일해', 't2': '매주 금요일엔 재택이야', 't3': '이번 금요일도 재택'}

    lines = {'aaaaaaa1': 'profile.identity.spouse', 'aaaaaaa2': 'profile.identity.marital_status'}

    def check(self, *proposals, profile_rows=8):
        return om.validate_dream(list(proposals), self.refs, self.refs.values(), set(), set(), profile_rows, self.lines)

    def test_tidying_may_rest_on_the_profile_lines_it_restates_or_ends_only(self):
        merge = fact('profile.identity.spouse', '배우자가 있음', 'aaaaaaa1', 'aaaaaaa2', category='identity',
                     invalidates=[('aaaaaaa2', 'same fact')])
        new_fact = fact('profile.identity.child', '아이가 둘', 'aaaaaaa1', category='identity')
        kept, dropped = self.check(merge, new_fact)
        self.assertEqual([item['memory_key'] for item in kept], ['profile.identity.spouse'])
        self.assertEqual([item['reason'] for item in dropped], ['sources'], 'no new fact from the profile alone')

    def test_every_fact_names_turns_it_read(self):
        kept, dropped = self.check(fact('profile.place.work', '판교', category='place'),
                                   fact('profile.place.home', '분당', 't9', category='place'))
        self.assertEqual(kept, [])
        self.assertEqual([item['reason'] for item in dropped], ['sources', 'sources'])

    def test_a_pattern_needs_two_separate_turns(self):
        kept, dropped = self.check(fact('profile.routine.remote', '금요일 재택', 't2', kind='inferred', category='routine'),
                                   fact('profile.routine.remote_friday', '금요일 재택', 't2', 't3', kind='inferred',
                                        category='routine'))
        self.assertEqual([item['memory_key'] for item in kept], ['profile.routine.remote_friday'])
        self.assertEqual([item['reason'] for item in dropped], ['one-occasion'])

    def test_a_request_sentence_is_not_a_fact(self):
        kept, dropped = self.check(fact('profile.place.work', '나는 판교에서 일해', 't1', category='place'))
        self.assertEqual((kept, [item['reason'] for item in dropped]), ([], ['request']))

    def test_ending_more_than_a_quarter_of_the_profile_refuses_the_endings_and_keeps_additions(self):
        self.lines = {**self.lines, 'aaaaaaa3': 'profile.place.home'}
        ids = ['aaaaaaa1', 'aaaaaaa2', 'aaaaaaa3']
        ends = [(line, 'old', om.END_REPLACED, 't1') for line in ids]
        kept, dropped = self.check(fact('profile.place.work', '판교', 't1', *ids, category='place', invalidates=ends),
                                   fact('profile.schedule.dentist', '10월 31일 치과', 't3', category='schedule'),
                                   profile_rows=8)
        self.assertEqual([item['memory_key'] for item in kept], ['profile.schedule.dentist'])
        self.assertEqual([item['reason'] for item in dropped], ['mass-end'])
        kept, _dropped = self.check(fact('profile.place.work', '판교', 't1', *ids[:2], category='place',
                                         invalidates=ends[:2]), profile_rows=8)
        self.assertEqual(len(kept[0]['invalidates']), 2)

    def test_naming_the_own_keys_line_is_a_replacement_not_an_ending(self):
        date_fix = fact('profile.identity.spouse', '2026년부터 배우자가 있음', 'aaaaaaa1', category='identity',
                        invalidates=[('aaaaaaa1', 'absolute date')])
        merge = fact('profile.identity.marital_status', '배우자(아내)가 있음', 'aaaaaaa2', 'aaaaaaa1', category='identity',
                     invalidates=[('aaaaaaa1', 'same fact')])
        kept, dropped = self.check(date_fix, profile_rows=4)
        self.assertEqual((len(kept), kept[0]['invalidates'], dropped), (1, [], []))
        kept, _dropped = self.check(merge, profile_rows=4)
        self.assertEqual([end['id'] for end in kept[0]['invalidates']], ['aaaaaaa1'])

    def test_a_small_profile_may_still_end_one_row(self):
        kept, _dropped = self.check(fact('profile.place.work', '판교', 't1', 'aaaaaaa1', category='place',
                                         invalidates=[('aaaaaaa1', 'moved', om.END_REPLACED, 't1')]), profile_rows=2)
        self.assertEqual([end['id'] for end in kept[0]['invalidates']], ['aaaaaaa1'])

    # -- #424: an ending must be grounded -------------------------------------------
    def test_an_ending_of_a_line_the_proposal_does_not_cite_is_dropped_and_the_fact_stays(self):
        """The live case: a fact drawn from turns ended an unrelated line it never cited."""
        self.lines = {**self.lines, 'bbbbbbb1': 'profile.identity.account_keys'}
        kept, dropped = self.check(fact('profile.identity.spouse', '아내가 있음', 't1', 'aaaaaaa1', category='identity',
                                        invalidates=[('bbbbbbb1', 'duplicate')]))
        self.assertEqual(([item['memory_key'] for item in kept], kept[0]['invalidates']), (['profile.identity.spouse'], []))
        self.assertEqual([(item['reason'], item['line']) for item in dropped], [('end-uncited', 'bbbbbbb1')])

    def test_a_replacement_must_name_a_turn_the_proposal_cites(self):
        uncited_turn = fact('profile.place.work', '판교', 't1', 'aaaaaaa1', category='place',
                            invalidates=[('aaaaaaa1', 'moved', om.END_REPLACED, 't2')])
        no_turn = fact('profile.place.work', '판교', 't1', 'aaaaaaa1', category='place',
                       invalidates=[('aaaaaaa1', 'moved', om.END_REPLACED, '')])
        for proposal in (uncited_turn, no_turn):
            kept, dropped = self.check(proposal)
            self.assertEqual((len(kept), kept[0]['invalidates']), (1, []))
            self.assertEqual([item['reason'] for item in dropped], ['end-ungrounded'])

    def test_a_line_that_was_never_lasting_may_be_ended_when_cited(self):
        """#429: a request sentence or current state kept as lasting is ended by citing it, with no turn."""
        kept, dropped = self.check(fact('profile.place.work', '판교', 't1', 'aaaaaaa1', category='place',
                                        invalidates=[('aaaaaaa1', 'a current state', om.END_NOT_LASTING)]))
        self.assertEqual(([end['id'] for end in kept[0]['invalidates']], dropped), (['aaaaaaa1'], []))
        kept, dropped = self.check(fact('profile.place.work', '판교', 't1', category='place',
                                        invalidates=[('aaaaaaa1', 'a current state', om.END_NOT_LASTING)]))
        self.assertEqual([item['reason'] for item in dropped], ['end-uncited'])

    def test_an_unsure_ending_is_held_not_applied(self):
        kept, dropped = self.check(fact('profile.place.work', '판교', 't1', 'aaaaaaa1', category='place',
                                        invalidates=[('aaaaaaa1', 'may be over', om.END_UNSURE, '')]))
        self.assertEqual(kept[0]['invalidates'], [])
        self.assertEqual([(item['reason'], item['line']) for item in dropped], [('held', 'aaaaaaa1')])

    def test_the_dream_schema_asks_why_each_line_ends(self):
        end = om.DREAM_SCHEMA['properties']['proposals']['items']['properties']['invalidates']['items']
        self.assertEqual(end['properties']['because']['enum'], list(om.END_BECAUSE))
        self.assertIn('turn', end['required'])
        upkeep_end = om.SCHEMA['properties']['proposals']['items']['properties']['invalidates']['items']
        self.assertNotIn('because', upkeep_end['properties'], 'the per-request upkeep schema is unchanged')


class Notes(Dream):
    def test_a_lesson_from_one_occasion_or_a_large_rewrite_is_refused(self):
        self.day('a', 'b', 'c')
        self.dreams = [dream(notes='- 답은 짧게', change='짧게', notes_sources=['t1'])]
        self.tick()
        self.assertIsNone(om.WorkingNotes(self.store).current())
        [(_status, detail)] = self.evidence(self.dream_rows()[0]['job_id'])
        self.assertIn({'note': 'notes', 'reason': 'one-occasion'}, detail['dropped'])

    def test_dropping_more_than_half_of_the_notes_at_once_is_refused(self):
        notes = om.WorkingNotes(self.store)
        self.assertTrue(notes.migrated())
        first = notes.save({'points': ['가', '나', '다'], 'text': om.notes_text(['가', '나', '다']), 'change': 'start'},
                           None)
        self.day('a', 'b', 'c')
        self.dreams = [dream(notes='- 라', change='바꿈', notes_sources=['t1', 't2'])]
        self.tick()
        self.assertEqual(notes.current()['id'], first['id'])
        [(_status, detail)] = self.evidence(self.dream_rows()[0]['job_id'])
        self.assertIn({'note': 'notes', 'reason': 'notes-rewrite'}, detail['dropped'])


class Told(Dream):
    """#425: the look back is quiet; a change the owner relies on, or an ending it held, is raised once on the
    line of the next request it matters to, and nothing is pushed."""

    def notice(self, job, kind, state='sent', fingerprint=None):
        from personal_agent.conversation_surface import surface_conversation
        surface, generation = surface_conversation(f'telegram:{GENERATION}', CHAT)
        with self.store.db() as db:
            db.execute('INSERT INTO telegram_notifications(id,job_id,chat_id,generation,kind,fingerprint,state,message_id,created,surface) '
                       'VALUES (?,?,?,?,?,?,?,?,?,?)', (f'n-{kind}', job, CHAT, generation, kind,
                                                        json.dumps(fingerprint or {'items': [], 'more': 0, 'sent': 1}), state,
                                                        11, time.time(), surface))

    def notices(self, job):
        with self.store.db() as db:
            return {row['kind']: row['state'] for row in db.execute(
                'SELECT kind,state FROM telegram_notifications WHERE job_id=?', (job,))}

    def brief(self, request, work):
        self.service.current_work_id = work
        try:
            return self.service.owner_profile_snapshot(request)
        finally:
            self.service.current_work_id = None

    def test_a_dream_pushes_no_notice_and_its_rows_stay_out_of_the_requests_own_notice(self):
        from personal_agent.quickstart_service import MEMORY_SAVED_KIND
        self.store.put('telegram', {'enabled': True, 'user_id': CHAT, 'generation': GENERATION})
        self.day('a', 'b')
        newest = self.settled('11월에 제주 여행 가', telegram=True)
        self.notice(newest, MEMORY_SAVED_KIND)
        self.dreams = [dream([fact('profile.plan.trip', '2026년 11월 제주 여행', 't3')], diary='제주 여행 계획을 말했다.')]
        self.tick()
        self.assertEqual(self.notices(newest), {MEMORY_SAVED_KIND: 'sent'}, 'nothing queued, no edit')
        rows = self.store.memories(MEMORY_OWNER)
        self.assertEqual(sorted(row['memory_key'].split('.')[0] for row in rows), ['diary', 'profile'])
        self.assertEqual({row['origin'] for row in rows}, {om.ORIGIN_DREAM}, 'the fact and the daily record')
        self.assertEqual(self.store.work_memories(MEMORY_OWNER, newest), [],
                         'a later notice of the request never lists what the look back wrote')

    def test_a_replacement_is_raised_once_on_the_line_of_the_next_request_it_matters_to(self):
        old = self.profile('profile.place.office', '판교 사무실')
        self.day('a', 'b', '이제 분당 사무실로 출근해')
        self.dreams = [dream([fact('profile.place.office', '분당 사무실', 't3', old['id'][:8], category='place',
                                   invalidates=[(old['id'][:8], 'moved', om.END_REPLACED, 't3')])])]
        self.tick()
        self.assertNotIn('overnight look back', self.brief('점심 메뉴 추천', 'w0'), 'not raised where it does not matter')
        shown = self.brief('사무실 가는 길', 'w1')
        self.assertIn('분당 사무실', shown)
        self.assertIn('changed in the overnight look back; before: 판교 사무실', shown)
        self.assertEqual(self.brief('사무실 가는 길', 'w1'), shown, 'every reader of that Work reads the same line')
        self.assertNotIn('overnight look back', self.brief('사무실 주차', 'w2'), 'raised once')

    def test_an_unsure_ending_is_held_and_asked_about_at_most_once(self):
        old = self.profile('profile.place.office', '판교 사무실')
        self.day('a', 'b', 'c')
        self.dreams = [dream([fact('profile.plan.trip', '11월 제주 여행', 't1', old['id'][:8], category='plan',
                                   invalidates=[(old['id'][:8], '요즘 다른 곳 이야기가 많음', om.END_UNSURE)])])]
        self.tick()
        self.assertEqual(self.store.memory(old['id'], MEMORY_OWNER)['content'], '판교 사무실', 'held, not ended')
        shown = self.brief('사무실 가는 길', 'w1')
        self.assertIn('may no longer hold: 요즘 다른 곳 이야기가 많음', shown)
        self.assertNotIn('may no longer hold', self.brief('사무실 가는 길', 'w2'))

    def test_a_raised_item_expires_after_a_week(self):
        now = [1000.0]
        raising = om.MemoryRaise(self.store, clock=lambda: now[0])
        row = self.profile('profile.place.office', '판교 사무실')
        raising.add([{'kind': om.RAISE_HELD, 'memory_id': row['id'], 'reason': 'unsure'}])
        self.assertIn(row['id'], raising.open())
        now[0] += om.RAISE_SECONDS
        self.assertEqual(raising.open(), {})

    def test_the_upkeep_and_the_dream_read_the_profile_without_raise_notes(self):
        old = self.profile('profile.place.office', '판교 사무실')
        om.MemoryRaise(self.store).add([{'kind': om.RAISE_HELD, 'memory_id': old['id'], 'reason': 'unsure'}])
        self.assertNotIn('may no longer hold', self.service.owner_profile_snapshot('사무실', notes=False))
        self.assertNotIn('may no longer hold', self.service.owner_profile_snapshot('사무실', notes=False, ids=True))
        self.assertIn(old['id'], om.MemoryRaise(self.store).open(), 'reading it there consumes nothing')

    def test_a_notes_revision_whose_notice_is_taken_is_not_saved(self):
        self.store.put('telegram', {'enabled': True, 'user_id': CHAT, 'generation': GENERATION})
        self.day('a', 'b')
        newest = self.settled('c', telegram=True)
        self.notice(newest, om.NOTICE_KIND, fingerprint={'ref': 'procedure:x', 'version': 1})
        self.dreams = [dream(notes='- 답은 짧게', change='짧게', notes_sources=['t1', 't2'])]
        self.tick()
        self.assertIsNone(om.WorkingNotes(self.store).current())
        [(_status, detail)] = self.evidence(newest)
        self.assertIn({'note': 'notes', 'reason': 'notice-taken'}, detail['dropped'])


class Diary(Dream):
    def test_a_secret_shaped_or_oversized_record_is_not_kept(self):
        self.store.secret('telegram_token', SECRET)
        self.day('a', 'b', 'c')
        self.dreams = [dream(diary='x' * (om.DIARY_CHARS + 1))]
        self.tick()
        self.assertEqual(self.store.memories(MEMORY_OWNER, key_prefix=om.DIARY_PREFIX), [])
        [(_status, detail)] = self.evidence(self.dream_rows()[0]['job_id'])
        self.assertIn({'note': 'diary', 'reason': 'length'}, detail['dropped'])

    def test_nothing_to_review_settles_without_a_call(self):
        for text in ('a', 'b', 'c'):
            self.settled(text, provider='builtin')
        self.assertIsNotNone(self.upkeep.enqueue_dream_if_due(time.time() + 1, 3))
        self.tick()
        self.assertEqual(self.asked, [])
        self.assertEqual(self.dream_rows()[0]['state'], om.STATE_GONE)


if __name__ == '__main__':
    unittest.main()
