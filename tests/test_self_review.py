"""SELF-REVIEW-01 (#998): the secretary reviews its own misses and keeps the lesson.

A second kind of row on the #805 owner-model upkeep queue: the same tick,
single flight, pause switch and rolling cap.  Typed markers enqueue it (a
failed Work a worker AI answered; a Work the continuity judgment linked as
a correction or retry; an observation another change hands in), the
Judgment AI reviews the Work from its own redacted records, a kept revision
of the working notes is their next version (#19, told once with an exact
undo; ``tests/test_working_notes.py`` covers the document itself), and a
blocker stays Evidence on the reviewed Work.

Evidence class: model-free unit and service tests.  A scripted
``FixtureDecisionEngine`` plays the Judgment AI; the real ``AgentService``
tick, ``QuickStore`` Memory paths and the Work loop of
``tests/test_orchestrator.Harness`` run unchanged.  No live model.
"""
import json
import sqlite3
import time
import unittest

from personal_agent import owner_model as om
from personal_agent.agent_runtime import MEMORY_OWNER
from personal_agent.conversation_handoff import FOLLOWUP_CORRECTION, FOLLOWUP_REFERENCE, FOLLOWUP_RETRY
from personal_agent.decision import OUTCOME_DECIDED, BinaryDecision, FixtureDecisionEngine, StructuredDecision, fixture_confidence
from personal_agent.quickstart_service import RETRY_REFUSED_RAN_CURRENT, AgentService

from test_orchestrator import Harness

SECRET = 'plain-stored-secret-998'
GENERATION = 7
CHAT = 4242


def review(notes='', change='', blocker=''):
    return {'notes': notes, 'change': change, 'blocker': blocker}


class SelfReview(Harness):
    def setUp(self):
        super().setUp()
        self.answers, self.facts, self.upkeep_answers = [], [], []

        def structured(context, question, schema):
            if context.purpose == om.REVIEW_PURPOSE:
                self.facts.append((context, question, schema))
                if not self.answers:
                    return None
                return StructuredDecision(OUTCOME_DECIDED, self.answers.pop(0), fixture_confidence(0.9))
            if context.purpose == om.PURPOSE:
                if not self.upkeep_answers:
                    return None
                return StructuredDecision(OUTCOME_DECIDED, {'proposals': self.upkeep_answers.pop(0)}, fixture_confidence(0.9))
            return None  # no plan: the default worker runs the raw request

        def judge(context, proposition):
            if context.purpose == 'goal-reached':
                return BinaryDecision(OUTCOME_DECIDED, True, fixture_confidence())
            return None
        self.fixture = FixtureDecisionEngine(structured=structured, judge=judge)
        self.service.use_decision_engine(self.fixture)
        # The run is off-thread in production; here it runs in place so each test reads its result.
        self.service.owner_model_spawn = lambda target: target()

    def settled(self, text, answer='an answer', status='failed', channel='web', chat_id=None):
        """A settled Work as the Work loop leaves it, with no queue row yet."""
        job = self.store.enqueue(text, f'sr-{time.time()}-{text}', channel=channel, chat_id=chat_id)
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status=?,response=?,provider='subscription',model='codex' WHERE id=?",
                       (status, answer, job))
        return job

    def missed(self, text, answer='an answer', status='failed', reason=om.REVIEW_FAILED, observation=None, **options):
        job = self.settled(text, answer, status, **options)
        self.assertTrue(om.enqueue_self_review(self.store, job, reason, observation))
        return job

    def corrected(self, job, text, relation=FOLLOWUP_CORRECTION):
        """The owner's next turn, linked to ``job`` and settled (the tick runs only when idle)."""
        later = self.settled(text, status='succeeded')
        self.service.record_continuity(later, job, relation)
        return later

    def evaluated(self, job, *texts):
        with self.store.db() as db:
            for index, text in enumerate(texts):
                db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                           (job, 'orchestrator', 'evaluated', json.dumps({'attempt': index + 1, 'text': text}), time.time()))

    def rows(self, kind=om.KIND_SELF_REVIEW):
        with self.store.db() as db:
            return {row['job_id']: dict(row) for row in db.execute('SELECT * FROM owner_model_upkeep WHERE kind=?', (kind,))}

    def evidence(self, job, tool=om.REVIEW_EVENT_TOOL):
        with self.store.db() as db:
            rows = db.execute('SELECT status,detail FROM tool_events WHERE job_id=? AND tool=? ORDER BY id', (job, tool)).fetchall()
        return [(row['status'], json.loads(row['detail'])) for row in rows]

    def review_calls(self):
        return [item for item in self.fixture.asked if item[0] == 'structured' and item[1].purpose == om.REVIEW_PURPOSE]

    def lessons(self):
        """The current working notes' points ([] when there is no document)."""
        current = om.WorkingNotes(self.store).current()
        return current['points'] if current else []


class Trigger(SelfReview):
    def test_a_failed_work_a_worker_answered_enqueues_one_self_review_and_no_upkeep(self):
        self.engine.fail = [ValueError('worker failed')] * 3
        job, row = self.run_work('그거 찾아줘')
        self.assertEqual(row['status'], 'failed')
        self.assertEqual(self.rows(om.KIND_UPKEEP), {})
        [(reviewed, pending)] = self.rows().items()
        self.assertEqual((reviewed, pending['state'], pending['reason'], pending['observation']),
                         (job, om.STATE_PENDING, om.REVIEW_FAILED, None))
        self.assertFalse(om.enqueue_self_review(self.store, job, om.REVIEW_CORRECTION), 'idempotent per Work')
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.review_calls(), [], 'nothing runs on the request path')

    def test_a_succeeded_work_without_a_correction_gets_no_self_review(self):
        job, row = self.run_work('나는 판교에서 일해')
        self.assertEqual(row['status'], 'succeeded')
        self.assertEqual(list(self.rows(om.KIND_UPKEEP)), [job], 'upkeep as before')
        self.assertEqual(self.rows(), {})

    def test_eligibility_is_typed(self):
        eligible = AgentService.self_review_eligible
        job = {'request_key': 'k'}
        self.assertTrue(eligible(job, 'failed', 'subscription'))
        self.assertTrue(eligible(job, 'failed', None), 'the exception path settles no provider')
        for outcome in ('succeeded', 'partial', 'cancelled', 'unknown'):
            self.assertFalse(eligible(job, outcome, 'subscription'), outcome)
        self.assertFalse(eligible(job, 'failed', 'builtin'), 'a rule/command reply: no worker AI ran')
        from personal_agent import preparations as prep
        from personal_agent.context_observations import continuation_key
        self.assertFalse(eligible({'request_key': prep.REQUEST_KEY_PREFIX + 'p1:1'}, 'failed', 'subscription'))
        self.assertFalse(eligible({'request_key': continuation_key('r1')}, 'failed', 'subscription'))

    def test_a_correction_or_retry_the_continuity_judgment_linked_reviews_the_earlier_work(self):
        previous = self.settled('판교 사무실 근처 점심', status='succeeded')
        later = self.store.enqueue('아니 분당 사무실', 'sr-later')
        self.service.record_continuity(later, previous, FOLLOWUP_CORRECTION)
        self.assertEqual(self.rows()[previous]['reason'], om.REVIEW_CORRECTION)
        self.assertEqual(self.store.job(later)['related_job_id'], previous)
        retried = self.settled('다른 요청', status='partial')
        again = self.store.enqueue('다시 해줘', 'sr-again')
        self.service.record_continuity(again, retried, FOLLOWUP_RETRY, executed=True, source_work_id=retried)
        self.assertEqual(self.rows()[retried]['reason'], om.REVIEW_RETRY)
        self.assertEqual(len(self.rows()), 2)

    def test_a_reference_or_a_refused_retry_stored_as_reference_reviews_nothing(self):
        previous = self.settled('판교 사무실 근처 점심', status='succeeded')
        later = self.store.enqueue('그 중 두 번째', 'sr-ref')
        self.service.record_continuity(later, previous, FOLLOWUP_REFERENCE)
        other = self.settled('다른 요청', status='succeeded')
        refused = self.store.enqueue('다시', 'sr-refused')
        self.service.record_continuity(refused, other, RETRY_REFUSED_RAN_CURRENT, executed=False, reason='no',
                                       link_kind=FOLLOWUP_REFERENCE)
        self.assertEqual(self.rows(), {})

    def test_the_reaction_api_enqueues_with_a_bounded_observation(self):
        job = self.settled('그거 찾아줘', status='succeeded')
        self.assertTrue(om.enqueue_self_review(self.store, job, om.REVIEW_REACTION, '  the owner  reacted\n 👍'))
        row = self.rows()[job]
        self.assertEqual(row['reason'], om.REVIEW_REACTION)
        self.assertEqual(row['observation'], 'the owner reacted 👍')
        # Review P1: never cut (a cut could split a secret past the redactor); too long is not kept.
        other = self.settled('다른 것', status='succeeded')
        self.assertTrue(om.enqueue_self_review(self.store, other, om.REVIEW_REACTION, 'x' * (om.MAX_OBSERVATION_CHARS + 1)))
        self.assertIsNone(self.rows()[other]['observation'])
        with self.assertRaises(ValueError):
            om.enqueue_self_review(self.store, job, 'because')
        with self.assertRaises(ValueError):
            om.enqueue_self_review(self.store, '', om.REVIEW_REACTION)

    def test_paused_upkeep_enqueues_no_self_review(self):
        self.service.owner_model_request({'operation': 'set', 'enabled': False})
        job = self.settled('그거 찾아줘')
        self.assertFalse(om.enqueue_self_review(self.store, job, om.REVIEW_FAILED))
        self.engine.fail = [ValueError('worker failed')] * 3
        self.run_work('그거 찾아줘')
        self.assertEqual(self.rows(), {})

    def test_a_blocked_turn_the_owner_resolves_is_not_a_miss(self):
        from personal_agent.conversation_projection import BlockedTurn
        self.engine.fail = [BlockedTurn('login', '로그인이 필요합니다')] * 3
        job, row = self.run_work('그거 찾아줘')
        self.assertEqual(row['status'], 'failed')
        self.assertEqual(self.rows(), {})


class Run(SelfReview):
    def test_the_facts_are_the_request_answer_outcome_verdicts_followup_and_profile(self):
        from personal_agent.prompt_files import prompt
        preference = '일할 땐 짧게, 힘든 얘기엔 다정하게 말해 줘'
        self.store.save_memory('profile.place.home', '분당', work_id='w0')
        self.store.save_memory('profile.preference.communication', preference, work_id='w0')
        job = self.settled('그거 찾아줘', answer='사진이 없어 찾지 못했습니다')
        self.evaluated(job, 'The goal was not reached; the worker asked for a photo.', 'Not reached; stopped.')
        self.corrected(job, '사진이 아니라 아까 말한 책 말이야')
        self.answers = [review()]
        self.assertTrue(self.service.run_owner_model_upkeep())
        context, question, schema = self.facts[0]
        self.assertEqual(context.work_id, job)
        self.assertEqual(context.facts['review_reason'], om.REVIEW_CORRECTION)
        self.assertEqual(context.facts['owner_request'], '그거 찾아줘')
        self.assertEqual(context.facts['final_answer_excerpt'], '사진이 없어 찾지 못했습니다')
        self.assertEqual(context.facts['work_outcome'], 'failed')
        self.assertEqual(context.facts['evaluation'],
                         'The goal was not reached; the worker asked for a photo.\nNot reached; stopped.')
        self.assertEqual(context.facts['owner_followup'], '사진이 아니라 아까 말한 책 말이야')
        self.assertIn('profile.place.home: 분당', context.facts['owner_profile'])
        self.assertIn(preference, context.facts['owner_profile'])
        self.assertEqual(question.count(prompt('voice')), 1)
        self.assertEqual(context.facts['working_notes'], 'none', 'no document yet')
        self.assertEqual(question, om.REVIEW_QUESTION)
        self.assertEqual(schema, om.REVIEW_SCHEMA)
        self.assertEqual(sorted(schema['properties']), ['blocker', 'change', 'notes'])
        self.assertEqual(self.rows()[job]['state'], om.STATE_DONE)
        self.assertFalse(self.service.run_owner_model_upkeep(), 'a done review never runs again')

    def test_an_observation_stands_in_for_the_followup_when_no_message_is_linked(self):
        self.missed('그거 찾아줘', status='succeeded', reason=om.REVIEW_REACTION, observation='the owner reacted: thumbs down')
        self.answers = [review()]
        self.service.run_owner_model_upkeep()
        context = self.facts[0][0]
        self.assertEqual((context.facts['review_reason'], context.facts['owner_followup'], context.facts['evaluation']),
                         (om.REVIEW_REACTION, 'the owner reacted: thumbs down', 'none'))

    def test_a_revision_becomes_the_next_notes_version_attributed_to_the_reviewed_work_told_once(self):
        self.store.put('telegram', {'enabled': True, 'user_id': CHAT, 'generation': GENERATION})
        job = self.missed('그거 찾아줘', channel=f'telegram:{GENERATION}', chat_id=CHAT)
        with self.store.db() as db:
            # The reply was delivered, so the notice is queued at once (#233: before that it is held).
            db.execute("UPDATE jobs SET delivery='sent' WHERE id=?", (job,))
        self.answers = [review('- "그거"는 직전 메시지에서 말한 것을 가리켜요, 사진이 아니에요',
                               '"그거"가 무엇을 가리키는지 적었어요')]
        self.service.run_owner_model_upkeep()
        self.assertEqual(self.lessons(), ['"그거"는 직전 메시지에서 말한 것을 가리켜요, 사진이 아니에요'])
        self.assertEqual(self.store.memories(MEMORY_OWNER), [], 'no Memory row: a working method is not a fact')
        current = om.WorkingNotes(self.store).current()
        self.assertEqual((current['version'], current['work_id']), (1, job), 'provenance links back to the reviewed Work')
        with self.store.db() as db:
            notices = db.execute('SELECT kind,state,fingerprint FROM telegram_notifications WHERE job_id=?', (job,)).fetchall()
        self.assertEqual([(n['kind'], n['state']) for n in notices], [('working_notes_saved', 'queued')],
                         'the owner is told once, never silently')
        self.assertEqual(json.loads(notices[0]['fingerprint']), {'ref': current['ref'], 'version': 1})
        [(status, detail)] = self.evidence(job)
        self.assertEqual(status, om.EVENT_RECORDED)
        self.assertEqual(detail['notes'], {'outcome': om.APPLIED_NOTES, 'version': 1, 'ref': current['ref'],
                                           'previous_version': None, 'points': 1, 'chars': len(current['text']),
                                           'auto_saved': True})
        self.assertIsNone(detail['blocker'])
        self.assertNotIn('그거"는', json.dumps(detail, ensure_ascii=False), 'Evidence never keeps the text')
        # Later briefs carry the whole document as one block of the owner profile section.
        self.assertIn(f"working_notes v1 (the assistant's own notes on how to work for this owner; ref {current['ref']}",
                      self.service.owner_profile_snapshot())
        self.assertIn('- "그거"는 직전', self.service.owner_profile_snapshot())

    def test_a_second_revision_keeps_the_first_as_the_rollback_target(self):
        first = self.missed('그거 찾아줘')
        self.answers = [review('- 요약은 세 줄로', '요약 길이를 적었어요')]
        self.service.run_owner_model_upkeep()
        second = self.missed('다시 해줘')
        self.answers = [review('- 요약은 세 줄로\n- 링크는 맨 끝에', '링크 위치를 더했어요')]
        self.service.run_owner_model_upkeep()
        self.assertEqual(self.lessons(), ['요약은 세 줄로', '링크는 맨 끝에'])
        self.assertEqual(self.evidence(second)[0][1]['notes']['previous_version'], 1)
        self.assertEqual(self.facts[1][0].facts['working_notes'], '- 요약은 세 줄로', 'the judgment read the current document')
        self.assertEqual(self.evidence(first)[0][1]['notes']['version'], 1)

    def test_a_blocker_is_evidence_on_the_reviewed_work_redacted_and_filed_nowhere(self):
        self.store.secret('telegram_token', SECRET)
        job = self.missed('그거 찾아줘')
        before = len(self.transport.bodies)
        self.answers = [review(blocker=f'  The brief lacked the previous turn\'s   message; token {SECRET}  ')]
        with self.assertLogs('personal_agent.service', level='INFO') as logs:
            self.service.run_owner_model_upkeep()
        [(status, detail)] = self.evidence(job)
        self.assertEqual(status, om.EVENT_RECORDED)
        self.assertEqual(detail['blocker'], 'The brief lacked the previous turn\'s message; token [redacted]')
        self.assertIsNone(detail['notes'])
        self.assertEqual(self.lessons(), [])
        self.assertEqual(len(self.transport.bodies), before, 'nothing leaves the machine')
        self.assertEqual(self.store.memory_candidates(MEMORY_OWNER), [])
        line = next(line for line in logs.output if 'self-review' in line)
        self.assertIn('blocker=True', line)
        for raw in ('brief lacked', SECRET, '그거 찾아줘'):
            self.assertNotIn(raw, line, 'the log line is content-free')

    def test_out_of_bounds_request_fact_and_changeless_revisions_are_dropped_whole(self):
        self.store.save_memory('profile.preference.lunch', '김밥', work_id='w0')
        cases = [(review('\n'.join(f'- point {n}' for n in range(om.NOTES_POINTS + 1)), 'c'), 'points'),
                 (review('- ' + 'x' * (om.POINT_CHARS + 1), 'c'), 'point-length'),
                 (review('\n'.join('- ' + str(n) * 190 for n in range(9)), 'c'), 'length'),
                 (review('- a note'), 'change'),
                 (review('- a note', 'x' * 201), 'change'),
                 (review('- 그거 찾아줘', 'c'), 'request'),
                 (review('-  김밥 ', 'c'), 'profile-fact'),
                 (review('- a note', 'c', 'the brief lacked the previous turn'), 'blocker'),
                 (review(blocker='x' * 201), 'content')]
        for answer, reason in cases:
            job = self.missed('그거 찾아줘')
            self.answers = [answer]
            self.service.run_owner_model_upkeep()
            [(_status, detail)] = self.evidence(job)
            self.assertEqual([item['reason'] for item in detail['dropped']], [reason], answer)
            self.assertIsNone(detail['notes'])
            text = json.dumps(detail, ensure_ascii=False)
            self.assertNotIn('a note', text, 'a dropped revision is never kept as text')
        self.assertEqual(self.lessons(), [])
        self.assertEqual([row['content'] for row in self.store.memories(MEMORY_OWNER)], ['김밥'])

    def test_a_secret_shaped_revision_or_change_is_never_saved(self):
        self.store.secret('telegram_token', SECRET)
        for answer in (review(f'- use token {SECRET}', 'c'), review('- a plain note', f'added token {SECRET}'),
                       review('- send it with sk-abcdefghijklmnopqrstuvwx', 'c')):
            job = self.missed('그거 찾아줘')
            self.answers = [answer]
            self.service.run_owner_model_upkeep()
            self.assertEqual(self.lessons(), [])
            [(_status, detail)] = self.evidence(job)
            self.assertEqual(detail['dropped'], [{'note': 'notes', 'reason': 'secret-shaped-value'}])
            self.assertNotIn(SECRET, json.dumps(detail))

    def test_a_stored_secret_never_reaches_the_judgment_facts(self):
        self.store.secret('telegram_token', SECRET)
        job = self.missed(f'그거 찾아줘 {SECRET}', answer=f'확인 {SECRET} sk-abcdefghijklmnopqrstuvwx')
        self.evaluated(job, f'verdict {SECRET}')
        self.corrected(job, f'아니 {SECRET}')
        self.answers = [review()]
        self.service.run_owner_model_upkeep()
        rendered = self.facts[0][0].render()
        self.assertNotIn(SECRET, rendered)
        self.assertNotIn('sk-abcdefghijklmnopqrstuvwx', rendered)

    def test_an_unavailable_judgment_stores_nothing_and_says_so(self):
        job = self.missed('그거 찾아줘')
        self.assertTrue(self.service.run_owner_model_upkeep())
        self.assertEqual(self.rows()[job]['state'], om.STATE_UNAVAILABLE)
        [(status, detail)] = self.evidence(job)
        self.assertEqual((status, detail['reason'], detail['decision']), (om.EVENT_UNAVAILABLE, om.REVIEW_FAILED, 'provider_unavailable'))
        self.assertEqual(self.lessons(), [])

    def test_a_work_not_yet_settled_is_not_reviewed(self):
        job = self.missed('그거 찾아줘')
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (job,))
        self.assertFalse(self.service.run_owner_model_upkeep(), 'the tick waits while a Work runs')
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='cancelled' WHERE id=?", (job,))
        self.answers = [review('- note', 'c')]
        self.assertTrue(self.service.run_owner_model_upkeep())
        self.assertEqual(self.rows()[job]['state'], om.STATE_GONE)
        self.assertEqual(self.evidence(job), [(om.EVENT_UNAVAILABLE, {'reason': 'work-not-finished'})])
        self.assertEqual(self.review_calls(), [])

    def test_the_evidence_holds_no_input_text_and_is_not_a_work_step(self):
        job = self.missed('그거 찾아줘', answer='사진이 없습니다')
        self.evaluated(job, 'Not reached.')
        self.answers = [review('a lesson', 'a change', 'a blocker')]
        self.service.run_owner_model_upkeep()
        [(_status, detail)] = self.evidence(job)
        self.assertEqual(detail['inputs'], {'owner_request_chars': len('그거 찾아줘'), 'answer_chars': len('사진이 없습니다'),
                                            'evaluations': 1, 'followups': 0, 'observation': False, 'profile_chars': 0, 'awaiting_owner': False,
                                            'working_notes_version': None, 'working_notes_chars': 0, 'clock': True})
        text = json.dumps(detail, ensure_ascii=False)
        for raw in ('그거 찾아줘', '사진이 없습니다', 'Not reached', 'a lesson'):
            self.assertNotIn(raw, text)
        from personal_agent.agent_runtime import event_trail
        with self.store.db() as db:
            rows = db.execute('SELECT tool,status,detail FROM tool_events WHERE job_id=?', (job,)).fetchall()
        self.assertEqual(event_trail([(r['tool'], r['status'], r['detail']) for r in rows])[0], [])
        self.assertIn(om.REVIEW_EVENT_TOOL, AgentService.LOCAL_NON_TOOL_EVENTS)

    def test_no_revision_is_asked_or_saved_while_the_lessons_move_is_still_retrying(self):
        """#233 (Codex review): a version saved first would make the move's retry leave the lessons in Memory."""
        self.store.save_memory('profile.working.summary', '요약은 세 줄로', MEMORY_OWNER, work_id='w0')
        planned = []
        original = om.WorkingNotes.migrate

        def changed_under_the_plan(notes):
            # What ``migrate`` does when a planned row changed before its commit: nothing moved, no record.
            planned.append(1)
            return None
        om.WorkingNotes.migrate = changed_under_the_plan
        self.addCleanup(setattr, om.WorkingNotes, 'migrate', original)
        job = self.missed('그거 찾아줘')
        self.answers = [review('- 링크는 맨 끝에', '링크 위치')]
        self.service.run_owner_model_upkeep()
        self.assertEqual(len(planned), om.MIGRATION_ATTEMPTS, 'planned again at once, a bounded number of times')
        self.assertEqual(self.review_calls(), [], 'nothing is asked while the move is not done')
        self.assertIsNone(om.WorkingNotes(self.store).current(), 'no version is saved before the move')
        [(status, detail)] = self.evidence(job)
        self.assertEqual((status, detail['stopped'], detail['sent']), (om.EVENT_UNAVAILABLE, om.STOPPED_MIGRATION, False))
        self.assertEqual((self.rows()[job]['state'], self.rows()[job]['calls']), (om.STATE_UNAVAILABLE, 0))
        # The move's retry (the next start) still consolidates the earlier lesson into the first version.
        om.WorkingNotes.migrate = original
        record = om.WorkingNotes(self.store).migrate()
        self.assertEqual((record['moved'], record['version']), (1, 1))
        self.assertEqual(self.lessons(), ['요약은 세 줄로'])
        self.assertEqual(self.store.memories(MEMORY_OWNER), [])
        # Once it is done, the next review revises the moved notes as before.
        later = self.missed('저거 찾아줘')
        self.answers = [review('- 요약은 세 줄로\n- 링크는 맨 끝에', '링크 위치')]
        self.service.run_owner_model_upkeep()
        self.assertEqual(self.lessons(), ['요약은 세 줄로', '링크는 맨 끝에'])
        self.assertEqual(self.evidence(later)[0][1]['notes']['previous_version'], 1)

    def test_a_move_that_raises_is_not_planned_again_by_the_same_review(self):
        def broken(notes):
            raise RuntimeError('disk')
        original = om.WorkingNotes.migrate
        om.WorkingNotes.migrate = broken
        self.addCleanup(setattr, om.WorkingNotes, 'migrate', original)
        job = self.missed('그거 찾아줘')
        self.answers = [review('- 링크는 맨 끝에', '링크 위치')]
        self.service.run_owner_model_upkeep()
        self.assertEqual(self.review_calls(), [])
        self.assertEqual(self.evidence(job)[0][1]['stopped'], om.STOPPED_MIGRATION)


class Budget(SelfReview):
    def test_a_review_counts_against_the_same_cap_and_the_pause_stops_it(self):
        self.service.owner_model_request({'operation': 'set', 'daily_calls': 1})
        upkeep = self.settled('나는 판교에서 일해', status='succeeded')
        with self.store.db() as db:
            self.service.owner_model.enqueue(db, upkeep)
        reviewed = self.missed('그거 찾아줘')
        self.upkeep_answers, self.answers = [[]], [review('- note', 'c')]
        self.assertTrue(self.service.run_owner_model_upkeep())
        self.assertEqual(self.rows()[reviewed]['state'], om.STATE_DONE, 'newest first')
        self.assertEqual(self.rows(om.KIND_UPKEEP)[upkeep]['state'], om.STATE_PENDING, 'the cap holds the next run')
        self.assertFalse(self.service.run_owner_model_upkeep())
        self.assertEqual(self.service.owner_model_request()['calls_last_24h'], 1)
        self.service.owner_model_request({'operation': 'set', 'enabled': False})
        with self.store.db() as db:
            db.execute('UPDATE owner_model_upkeep SET claimed=? WHERE job_id=?', (time.time() - om.WINDOW_SECONDS - 1, reviewed))
        self.assertFalse(self.service.run_owner_model_upkeep(), 'paused')

    def test_a_pause_during_the_run_writes_no_lesson(self):
        job = self.missed('그거 찾아줘')
        self.answers = [review('- note', 'c')]
        pause = self.service.owner_model_request

        def structured(context, question, schema):
            pause({'operation': 'set', 'enabled': False})
            return StructuredDecision(OUTCOME_DECIDED, self.answers.pop(0), fixture_confidence(0.9))
        self.service.use_decision_engine(FixtureDecisionEngine(structured=structured))
        self.service.run_owner_model_upkeep()
        self.assertEqual(self.lessons(), [])
        [(_status, detail)] = self.evidence(job)
        self.assertEqual((detail['stopped'], detail['dropped'][0]['reason']), (om.STOPPED_PAUSED, om.STOPPED_PAUSED))

    def test_an_old_pending_review_expires_without_a_call(self):
        job = self.missed('그거 찾아줘')
        with self.store.db() as db:
            db.execute('UPDATE owner_model_upkeep SET created=? WHERE job_id=?', (time.time() - om.MAX_PENDING_SECONDS - 60, job))
        self.assertTrue(self.service.run_owner_model_upkeep())
        self.assertEqual(self.rows()[job]['state'], om.STATE_EXPIRED)
        self.assertEqual(self.evidence(job), [(om.EVENT_EXPIRED, {'reason': 'expired'})])
        self.assertEqual(self.review_calls(), [])

    def test_a_claim_a_crash_left_expires_as_interrupted(self):
        job = self.missed('그거 찾아줘')
        with self.store.db() as db:
            db.execute('UPDATE owner_model_upkeep SET state=?,claimed=?,calls=1 WHERE job_id=?',
                       (om.STATE_CLAIMED, time.time() - om.CLAIM_STALE_SECONDS - 5, job))
        self.assertFalse(self.service.run_owner_model_upkeep())
        self.assertEqual(self.rows()[job]['state'], om.STATE_EXPIRED)
        self.assertEqual(self.evidence(job), [(om.EVENT_EXPIRED, {'reason': 'interrupted'})])


class Unit(unittest.TestCase):
    def test_validate_review_bounds(self):
        revision, blocker, dropped = om.validate_review(review(' * a  note \n\n- b ', ' added  b ', ' fix  this '))
        self.assertEqual((revision, blocker, dropped),
                         (None, 'fix this', [{'note': 'notes', 'reason': 'blocker'}]), 'a blocker is not a preference')
        # Review P2: a blocker too long to keep as Evidence still marks a software cause.
        revision, blocker, dropped = om.validate_review(review('- b', 'added b', 'x' * 201), ['a note'])
        self.assertEqual((revision, blocker, dropped), (None, None, [{'note': 'blocker', 'reason': 'content'},
                                                                     {'note': 'notes', 'reason': 'blocker'}]))
        revision, blocker, dropped = om.validate_review(review(' * a  note \n\n- b ', ' added  b '), ['a note'])
        self.assertEqual((revision, blocker, dropped),
                         ({'points': ['a note', 'b'], 'text': '- a note\n- b', 'change': 'added b'}, None, []))
        self.assertEqual(om.validate_review(review(), set()), (None, None, []))
        self.assertEqual(om.validate_review('nonsense', set()), (None, None, []))
        self.assertEqual(om.validate_review(review('- note', 'c'), (), {'note'}),
                         (None, None, [{'note': 'notes', 'reason': 'profile-fact'}]))
        self.assertEqual(om.validate_review(review('- note', 'c'), ['note'], {'note'}),
                         (None, None, [{'note': 'notes', 'reason': 'unchanged'}]), 'a point already there is not a fact added')
        self.assertTrue(om.review_shape(review()))
        self.assertFalse(om.review_shape({'notes': 1, 'change': '', 'blocker': ''}))

    def test_the_question_names_no_task(self):
        for word in ('photo', 'book', 'cart', 'calendar', 'lunch', 'map', 'coupang', 'naver', 'direction'):
            self.assertNotIn(word, om.REVIEW_QUESTION.lower(), word)
        self.assertIn('This judgment writes nothing', om.REVIEW_QUESTION)
        # #1463 and #19: the owner's language, no reworded duplicate, a software blocker is not a lesson,
        # the whole document or no change, and the inherited bounds.
        for rule in ("language of the owner's own words", 'even in other words', 'give the blocker and no notes',
                     'return the whole revised document', 'keeps the document unchanged',
                     f'at most {om.NOTES_POINTS} points of at most {om.POINT_CHARS} characters each and at most '
                     f'{om.NOTES_CHARS} characters in all'):
            self.assertIn(rule, om.REVIEW_QUESTION)
        self.assertEqual((om.NOTES_CHARS, om.NOTES_POINTS), (1500, 12), 'the inherited design (#19)')

    def test_a_step_the_owner_already_took_that_still_failed_is_a_blocker(self):
        # #154: the owner approved and nothing was applied; three lessons recorded the dead end as
        # an owner preference, one of them asking to skip the approval.
        question = om.REVIEW_QUESTION
        for rule in ('the owner already did their part - approved, answered, connected or gave the value',
                     "the cause is the software, not the owner's habit: give the blocker and no notes",
                     'A note never asks to skip, assume or relax an approval or confirmation the software requires'):
            self.assertIn(rule, question)
        # After the missing-capability rule (#1463) it refines, and before the bounds that close the question.
        self.assertLess(question.index('missing tool or capability'), question.index('already did their part'))
        self.assertLess(question.index('A note never asks to skip'), question.index('The blocker is at most'))

    def test_an_old_table_is_rebuilt_once_keyed_by_work_and_kind(self):
        db = sqlite3.connect(':memory:')
        db.row_factory = sqlite3.Row
        db.executescript('''CREATE TABLE owner_model_upkeep(job_id TEXT PRIMARY KEY, state TEXT NOT NULL, created REAL NOT NULL,
            claimed REAL, calls INTEGER NOT NULL DEFAULT 0);
            CREATE INDEX owner_model_upkeep_state ON owner_model_upkeep(state, created);
            CREATE INDEX owner_model_upkeep_claimed ON owner_model_upkeep(claimed);
            INSERT INTO owner_model_upkeep VALUES ('w1','done',1.0,2.0,1),('w2','pending',3.0,NULL,0);''')
        self.assertTrue(om.migrate(db))
        db.executescript(om.TABLE_SQL)
        self.assertFalse(om.migrate(db), 'idempotent')
        rows = [tuple(row) for row in db.execute('SELECT job_id,kind,state,created,claimed,calls,reason,observation '
                                                 'FROM owner_model_upkeep ORDER BY job_id')]
        self.assertEqual(rows, [('w1', 'upkeep', 'done', 1.0, 2.0, 1, None, None), ('w2', 'upkeep', 'pending', 3.0, None, 0, None, None)])
        db.execute("INSERT INTO owner_model_upkeep(job_id,kind,state,created,reason) VALUES ('w1','self-review','pending',4.0,'failed')")
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute("INSERT INTO owner_model_upkeep(job_id,kind,state,created) VALUES ('w1','self-review','pending',5.0)")
        self.assertEqual({row['name'] for row in db.execute('PRAGMA index_list(owner_model_upkeep)')}
                         >= {'owner_model_upkeep_state', 'owner_model_upkeep_claimed'}, True)
        self.assertFalse(om.migrate(sqlite3.connect(':memory:')), 'no table: nothing to rebuild')


if __name__ == '__main__':
    unittest.main()


class FollowupRedactedBeforeItIsBounded(unittest.TestCase):
    """Review P1: a stored secret straddling the follow-up bound is redacted whole, never sent as a prefix."""

    def test_a_secret_across_the_bound_does_not_leak_as_a_prefix(self):
        from personal_agent.conversation_handoff import ConversationJudgments
        from personal_agent.decision import FixtureDecisionEngine
        secret = 'OWNERSTOREDSECRET-0123456789'
        seen = []

        def structured(context, question, schema, shape=None):
            seen.append(dict(context.facts))
            return None
        judge = ConversationJudgments(FixtureDecisionEngine(structured=structured),
                                      redactor=lambda text, private=True: text.replace(secret, '[redacted]'))
        followup = 'a' * (om.FOLLOWUP_CHARS - 10) + secret
        judge.self_review('요청', '답', 'failed', '', followup, '', om.REVIEW_CORRECTION)
        self.assertTrue(seen)
        self.assertNotIn(secret[:10], seen[0]['owner_followup'])
