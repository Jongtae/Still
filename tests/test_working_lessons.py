"""#1031 step 1 ("Stop the loss"): working lessons are items, changed only by explicit operations.

Before #1031 the working notes were one document the self-review and the dream
rewrote whole; the revision check never verified that existing points
survived, and the live store lost most of its lessons that way (ACE's "context
collapse").  Now each lesson is its own canonical Memory row under
``owner_model.LESSONS_PREFIX`` (append plus invalidate, attributable to its
source Work, forgettable one at a time), and a judgment returns operations on
lesson ids: ``add``, ``update``, or ``remove`` with a reason.  A lesson leaves
current use only through an explicit ``remove`` that names it, or the owner's
forget.  A one-time migration restores the lessons the retained working-notes
versions hold, never a point found only in a version the owner forgot, and
tells the owner what came back.

Evidence class: model-free unit and service tests on a real ``QuickStore``
with a fake clock, a scripted Judgment AI (``FixtureDecisionEngine``) and an
injected Telegram transport.  No live model, no live Telegram.
"""
import json
import tempfile
import time
import unittest

from personal_agent import owner_model as om
from personal_agent.agent_runtime import MEMORY_OWNER, SECRET_SHAPED_VALUE
from personal_agent.decision import OUTCOME_DECIDED, FixtureDecisionEngine, StructuredDecision, fixture_confidence
from personal_agent.messages import msg
from personal_agent.owner_forget import OwnerForget
from personal_agent.procedures import NOTES_SLOT, REF_PREFIX, ProcedureLibrary
from personal_agent.quickstart_store import QuickStore

from test_memory_candidate_confirm import CHAT, GENERATION, TelegramHarness
from test_owner_model_dream import Dream
from test_self_review import SelfReview

SECRET = 'plain-stored-secret-1031'


class Clock:
    def __init__(self, now=1_800_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def add(text, why='the owner corrected this twice'):
    return {'op': 'add', 'id': '', 'text': text, 'why': why, 'reason': ''}


def update(lesson_id, text, why='a later correction refined it'):
    return {'op': 'update', 'id': lesson_id, 'text': text, 'why': why, 'reason': ''}


def remove(lesson_id, reason='the owner asked otherwise since'):
    return {'op': 'remove', 'id': lesson_id, 'text': '', 'why': '', 'reason': reason}


def answer(*ops, change='c', blocker=''):
    return {'lessons': list(ops), 'change': change, 'blocker': blocker}


def reasons(dropped):
    return [item['reason'] for item in dropped if item.get('note') == 'lesson']


class Store(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = QuickStore(tmp.name)
        self.clock = Clock()
        self.lessons = om.WorkingLessons(self.store, self.clock)
        self.forget = OwnerForget(self.store, clock=self.clock)

    def apply(self, *ops, change='c', work_id='w-1031'):
        current = {lesson['id']: lesson['text'] for lesson in self.lessons.current()}
        kept, change, _blocker, dropped = om.validate_lessons(answer(*ops, change=change), current)
        result = self.lessons.apply(kept, change, work_id, origin=om.ORIGIN_SELF_REVIEW)
        return result, dropped + result['dropped']

    def texts(self):
        return sorted(lesson['text'] for lesson in self.lessons.current())

    def seed(self, *texts):
        self.apply(*(add(text) for text in texts))
        return {lesson['text']: lesson for lesson in self.lessons.current()}


class Operations(Store):
    """No existing lesson can disappear without an explicit remove that names it."""

    def test_a_judgment_returning_only_a_new_lesson_leaves_every_existing_lesson_current(self):
        before = self.seed('요약은 세 줄로', '링크는 맨 끝에', '"그거"는 직전 메시지의 대상')
        rows_before = {lesson['memory_id'] for lesson in before.values()}
        self.clock.now += 60
        result, dropped = self.apply(add('비교는 표로'))
        self.assertEqual((len(result['applied']), dropped), (1, []))
        after = self.lessons.current()
        self.assertEqual(sorted(lesson['text'] for lesson in after),
                         sorted(['요약은 세 줄로', '링크는 맨 끝에', '"그거"는 직전 메시지의 대상', '비교는 표로']))
        self.assertTrue(rows_before <= {lesson['memory_id'] for lesson in after},
                        'the existing rows themselves stay current, untouched')

    def test_an_empty_operation_list_changes_nothing(self):
        before = self.seed('요약은 세 줄로', '링크는 맨 끝에')
        result, dropped = self.apply()
        self.assertEqual((result['applied'], dropped), ([], []))
        self.assertEqual({lesson['memory_id'] for lesson in self.lessons.current()},
                         {lesson['memory_id'] for lesson in before.values()})

    def test_the_old_whole_document_answer_removes_nothing(self):
        before = self.seed('요약은 세 줄로', '링크는 맨 끝에')
        # A judgment that still answers in the pre-#1031 shape (one whole document of one point).
        self.assertFalse(om.review_shape({'notes': '- 요약은 세 줄로', 'change': 'c', 'blocker': ''}))
        kept, _change, _blocker, _dropped = om.validate_lessons({'notes': '- 요약은 세 줄로', 'change': 'c', 'blocker': ''},
                                                                {l['id']: l['text'] for l in before.values()})
        self.assertEqual(kept, [])
        self.assertEqual(self.texts(), ['링크는 맨 끝에', '요약은 세 줄로'])

    def test_remove_without_an_id_or_a_reason_is_refused(self):
        before = self.seed('요약은 세 줄로', '링크는 맨 끝에')
        target = before['요약은 세 줄로']['id']
        for op, expected in ((remove('', 'no longer holds'), 'remove-id'),
                             (remove(target, ''), 'remove-reason'),
                             (remove(target, '   '), 'remove-reason'),
                             (remove('ffffffff', 'no longer holds'), 'unknown-id')):
            with self.subTest(op=op):
                result, dropped = self.apply(op)
                self.assertEqual(result['applied'], [])
                self.assertEqual(reasons(dropped), [expected])
                self.assertEqual(self.texts(), ['링크는 맨 끝에', '요약은 세 줄로'])

    def test_an_explicit_remove_ends_only_the_named_lesson_and_keeps_its_record(self):
        before = self.seed('요약은 세 줄로', '링크는 맨 끝에')
        gone = before['요약은 세 줄로']
        result, dropped = self.apply(remove(gone['id'], 'the owner now wants full summaries'))
        self.assertEqual((len(result['applied']), dropped), (1, []))
        self.assertEqual(self.texts(), ['링크는 맨 끝에'])
        with self.store.db() as db:
            row = dict(db.execute('SELECT state,invalidation_reason,invalidated_at FROM memories WHERE id=?',
                                  (gone['memory_id'],)).fetchone())
        self.assertNotEqual(row['state'], 'current')
        self.assertNotEqual(row['state'], 'forgotten', 'an AI removal is not the owner forgetting it')
        self.assertEqual(row['invalidation_reason'], 'the owner now wants full summaries', 'append + invalidate')
        self.assertIsNotNone(row['invalidated_at'])

    def test_update_keeps_the_lesson_id_supersedes_its_row_and_touches_no_other(self):
        before = self.seed('요약은 세 줄로', '링크는 맨 끝에')
        target, other = before['요약은 세 줄로'], before['링크는 맨 끝에']
        result, dropped = self.apply(update(target['id'], '요약은 세 줄로, 숫자는 그대로'))
        self.assertEqual((len(result['applied']), dropped), (1, []))
        now = {lesson['id']: lesson for lesson in self.lessons.current()}
        self.assertEqual(now[target['id']]['text'], '요약은 세 줄로, 숫자는 그대로')
        self.assertEqual(now[target['id']]['key'], target['key'], 'the same lesson, a new row')
        self.assertNotEqual(now[target['id']]['memory_id'], target['memory_id'])
        self.assertEqual(now[other['id']]['memory_id'], other['memory_id'])
        with self.store.db() as db:
            old = db.execute('SELECT state FROM memories WHERE id=?', (target['memory_id'],)).fetchone()['state']
        self.assertEqual(old, 'superseded', 'the earlier wording stays as history')

    def test_unknown_ids_unchanged_texts_and_duplicate_targets_are_refused(self):
        cases = [(lambda lid: (update('ffffffff', 'x'),), ['unknown-id']),
                 (lambda lid: (update(lid, '  요약은  세 줄로 '),), ['unchanged']),
                 (lambda lid: (add('요약은 세 줄로'),), ['unchanged']),
                 (lambda lid: (add('x', why=''),), ['why']),
                 (lambda lid: (add('x', why='w' * (om.CHANGE_CHARS + 1)),), ['why']),
                 (lambda lid: (add('n', why='w'), add('n', why='w')), ['duplicate']),
                 (lambda lid: (update(lid, 'a'), remove(lid)), ['duplicate']),
                 (lambda lid: (dict(add('x'), id=lid),), ['add-id']),
                 (lambda lid: ({'op': 'rewrite', 'id': '', 'text': 'x', 'why': 'w', 'reason': ''},), ['op'])]
        for index, (make, expected) in enumerate(cases):
            with self.subTest(case=index):
                self.setUp()  # a fresh store per case
                lesson_id = self.seed('요약은 세 줄로')['요약은 세 줄로']['id']
                ops = make(lesson_id)
                result, dropped = self.apply(*ops)
                self.assertEqual(reasons(dropped), expected)
                self.assertEqual(len(result['applied']), len(ops) - len(expected))
                self.assertIn(lesson_id, {lesson['id'] for lesson in self.lessons.current()},
                              'a refused operation never removes the lesson it names')

    def test_bounds_are_checked_per_lesson_and_never_cut(self):
        long = 'z' * (om.POINT_CHARS + 1)
        result, dropped = self.apply(add(long), add('ok'))
        self.assertEqual(reasons(dropped), ['point-length'])
        self.assertEqual(self.texts(), ['ok'], 'a too-long lesson is refused, never cut; the other op stands')
        kept, _c, _b, dropped = om.validate_lessons(answer(*(add(f'p{i}') for i in range(om.MAX_LESSON_OPS + 1))), {})
        self.assertEqual((kept, reasons(dropped)), ([], ['ops']), 'too many operations at once: refused whole')

    def test_adds_past_the_lesson_cap_are_refused_and_existing_lessons_stay(self):
        for start in range(0, om.LESSONS_MAX, om.MAX_LESSON_OPS):
            self.apply(*(add(f'lesson {index:03d}') for index in range(start, min(start + om.MAX_LESSON_OPS, om.LESSONS_MAX))))
        self.assertEqual(len(self.lessons.current()), om.LESSONS_MAX)
        result, dropped = self.apply(add('one more'))
        self.assertEqual((result['applied'], reasons(dropped)), ([], ['lessons-full']))
        self.assertEqual(len(self.lessons.current()), om.LESSONS_MAX)

    def test_a_blocker_or_a_missing_change_line_vetoes_every_operation(self):
        self.seed('요약은 세 줄로')
        current = {lesson['id']: lesson['text'] for lesson in self.lessons.current()}
        kept, _change, blocker, dropped = om.validate_lessons(answer(add('링크는 맨 끝에'), blocker='a tool was missing'),
                                                              current)
        self.assertEqual((kept, blocker, reasons(dropped)), ([], 'a tool was missing', ['blocker']))
        kept, _change, _blocker, dropped = om.validate_lessons(answer(add('링크는 맨 끝에'), change=''), current)
        self.assertEqual((kept, reasons(dropped)), ([], ['change']))

    def test_the_request_sentence_and_a_known_profile_value_are_not_lessons(self):
        kept, _c, _b, dropped = om.validate_lessons(answer(add('그거 찾아줘'), add('분당')), {},
                                                    known={om.normalized('분당')}, request='그거 찾아줘')
        self.assertEqual((kept, sorted(reasons(dropped))), ([], ['profile-fact', 'request']))

    def test_a_secret_shaped_lesson_is_never_saved(self):
        self.store.secret('telegram_token', SECRET)
        result, dropped = self.apply(add(f'토큰은 {SECRET}'), add('ok', why=f'because {SECRET}'))
        self.assertEqual(result['applied'], [])
        self.assertEqual(reasons(dropped), [SECRET_SHAPED_VALUE, SECRET_SHAPED_VALUE])
        self.assertEqual(self.lessons.current(), [])
        with self.store.db() as db:
            self.assertNotIn(SECRET, json.dumps([dict(row) for row in db.execute('SELECT * FROM memories')]))

    def test_an_update_or_remove_of_a_lesson_that_changed_meanwhile_is_refused(self):
        before = self.seed('요약은 세 줄로')
        lesson_id = before['요약은 세 줄로']['id']
        current = {lesson_id: '요약은 세 줄로'}
        kept, change, _b, _d = om.validate_lessons(answer(update(lesson_id, '요약은 두 줄로')), current)
        self.forget.forget(MEMORY_OWNER, before['요약은 세 줄로']['ref'])
        result = self.lessons.apply(kept, change, 'w-later', origin=om.ORIGIN_SELF_REVIEW)
        self.assertEqual((result['applied'], reasons(result['dropped'])), ([], ['lesson-changed']))
        self.assertEqual(self.lessons.current(), [], 'a forgotten lesson is not brought back by a stale update')


class Attribution(Store):
    """Each lesson is attributable (source Work, basis, saved time) and forgotten on its own."""

    def test_each_lesson_row_records_its_work_basis_why_origin_and_saved_time(self):
        self.apply(add('요약은 세 줄로', why='the owner shortened two answers'), work_id='w-src')
        [lesson] = self.lessons.current()
        self.assertTrue(lesson['key'].startswith(om.LESSONS_PREFIX))
        self.assertEqual(lesson['key'], om.LESSONS_PREFIX + lesson['id'])
        self.assertEqual(lesson['ref'], 'memory:' + lesson['memory_id'])
        self.assertEqual((lesson['why'], lesson['work_id'], lesson['origin']),
                         ('the owner shortened two answers', 'w-src', om.ORIGIN_SELF_REVIEW))
        self.assertIsNotNone(lesson['saved'])
        row = self.store.memory(lesson['memory_id'], MEMORY_OWNER)
        self.assertEqual((row['memory_key'], row['content'], row['basis'], row['evidence']),
                         (lesson['key'], '요약은 세 줄로', om.BASIS_INFERRED, 'the owner shortened two answers'))
        with self.store.db() as db:
            sources = db.execute('SELECT source_works FROM memories WHERE id=?', (lesson['memory_id'],)).fetchone()[0]
        self.assertEqual(json.loads(sources), ['w-src'])

    def test_forgetting_one_lesson_leaves_the_others_and_undo_brings_it_back(self):
        before = self.seed('요약은 세 줄로', '링크는 맨 끝에', '비교는 표로')
        receipt = self.forget.forget(MEMORY_OWNER, before['링크는 맨 끝에']['ref'])
        self.assertEqual(self.texts(), ['비교는 표로', '요약은 세 줄로'])
        self.assertEqual({lesson['memory_id'] for lesson in self.lessons.current()},
                         {before['비교는 표로']['memory_id'], before['요약은 세 줄로']['memory_id']})
        self.assertNotIn('링크는 맨 끝에', self.lessons.block(''))
        self.forget.undo(MEMORY_OWNER, receipt['receipt'])
        self.assertEqual(self.texts(), ['링크는 맨 끝에', '비교는 표로', '요약은 세 줄로'])

    def test_a_forgotten_lesson_is_not_added_again_under_a_new_id(self):
        before = self.seed('요약은 세 줄로', '링크는 맨 끝에')
        self.forget.forget(MEMORY_OWNER, before['링크는 맨 끝에']['ref'])
        result, dropped = self.apply(add('  링크는  맨 끝에 '))
        self.assertEqual((result['applied'], reasons(dropped)), ([], [om.FORGOTTEN_VALUE]))
        result, dropped = self.apply(update(before['요약은 세 줄로']['id'], '링크는 맨 끝에'))
        self.assertEqual((result['applied'], reasons(dropped)), ([], [om.FORGOTTEN_VALUE]))
        self.assertEqual(self.texts(), ['요약은 세 줄로'])

    def test_lesson_rows_are_not_profile_facts(self):
        from personal_agent.memory_service import MemoryService
        self.store.save_memory('profile.place.home', '분당', MEMORY_OWNER)
        self.seed('요약은 세 줄로')
        snapshot = MemoryService(self.store, private_read_sink=MemoryService.NO_EGRESS_GUARD).profile_snapshot(MEMORY_OWNER)
        self.assertIn('분당', snapshot['text'])
        self.assertNotIn('요약은 세 줄로', snapshot['text'], 'lessons are their own block, not profile lines')
        proposal = {'category': 'preference', 'kind': 'stated', 'memory_key': om.LESSONS_PREFIX + 'abcd1234',
                    'content': 'x', 'terms': '', 'evidence': 'y', 'supersedes_key': ''}
        kept, dropped = om.validate([proposal], set(), set())
        self.assertEqual((kept, [item['reason'] for item in dropped]), ([], ['key']),
                         'upkeep never writes a lesson as a fact')


class Brief(Store):
    """The brief carries the relevant lessons within its budget and states how many it left out."""

    def test_the_brief_carries_every_lesson_with_its_ref_when_they_fit(self):
        before = self.seed('요약은 세 줄로', '링크는 맨 끝에')
        block = self.lessons.block('')
        for lesson in before.values():
            self.assertIn(lesson['text'], block)
            self.assertIn(lesson['ref'], block, 'the AI can forget one lesson by its exact ref')
        self.assertNotIn('omitted', block)
        self.assertEqual(om.WorkingLessons(self.store).block(''), block)

    def test_no_lessons_is_no_block(self):
        self.assertEqual(self.lessons.block('anything'), '')

    def test_over_budget_the_matching_lessons_come_first_and_the_omitted_count_is_stated(self):
        filler = 'x' * (om.POINT_CHARS - 20)
        self.assertGreater(om.LESSONS_MAX * len(filler), om.LESSONS_BRIEF_CHARS,
                           'the lesson cap exceeds the brief budget, so the brief must select')
        for start in range(0, om.LESSONS_MAX, om.MAX_LESSON_OPS):
            self.apply(*(add(f'{filler} {index:03d}') for index in range(start, min(start + om.MAX_LESSON_OPS, om.LESSONS_MAX))))
        self.assertEqual(len(self.lessons.current()), om.LESSONS_MAX)
        # The matching lesson is the oldest of all, so only the request selects it.
        matching = self.lessons.current()[0]
        self.apply(remove(self.lessons.current()[-1]['id'], 'make room'))
        self.apply(update(matching['id'], '부동산 시세 비교는 출처 두 곳 이상으로'))
        with self.store.db() as db:
            db.execute("UPDATE memories SET created=created-200000 WHERE memory_key=? AND state='current'",
                       (matching['key'],))
        block = self.lessons.block('부동산 시세 다시 비교해줘')
        self.assertIn('부동산 시세 비교는 출처 두 곳 이상으로', block)
        current = self.lessons.current()
        shown = [lesson for lesson in current if lesson['ref'] in block]
        omitted = len(current) - len(shown)
        self.assertGreater(omitted, 0)
        self.assertLessEqual(sum(len(lesson['text']) for lesson in shown), om.LESSONS_BRIEF_CHARS)
        self.assertRegex(block, rf'(?<!\d){omitted}(?!\d)[^\n]*omitted')
        self.assertTrue(all(lesson['text'] in block for lesson in shown))


class Migration(Store):
    """The lessons the retained working-notes versions hold come back once; forgotten-only points never do."""

    def commit(self, *points, work_id=None):
        self.clock.now += 60
        return ProcedureLibrary(self.store, self.clock).commit(NOTES_SLOT, om.notes_text(list(points)), work_id=work_id,
                                                                stats={'change': 'c'})

    def history(self):
        """v1 {a, b} kept; v2 {a, b, c} forgotten; v3 {a, d} kept; v4 {d} current (the collapse)."""
        self.commit('a 요약은 세 줄로', 'b 링크는 맨 끝에', work_id='w-v1')
        second = self.commit('a 요약은 세 줄로', 'b 링크는 맨 끝에', 'c 잊은 교훈', work_id='w-v2')
        self.forget.forget(MEMORY_OWNER, REF_PREFIX + second['id'])
        self.commit('a 요약은 세 줄로', 'd 비교는 표로', work_id='w-v3')
        self.commit('d 비교는 표로', work_id='w-v4')

    def test_points_of_retained_versions_return_once_and_forgotten_only_points_never(self):
        self.history()
        record = self.lessons.migrate()
        self.assertEqual(sorted(self.texts()), ['a 요약은 세 줄로', 'b 링크는 맨 끝에', 'd 비교는 표로'])
        self.assertEqual(record['restored'], 3)
        self.assertNotIn('c 잊은 교훈', self.lessons.block(''))
        self.assertEqual({lesson['origin'] for lesson in self.lessons.current()}, {om.ORIGIN_LESSONS_MIGRATION})
        by_text = {lesson['text']: lesson for lesson in self.lessons.current()}
        self.assertEqual(by_text['b 링크는 맨 끝에']['work_id'], 'w-v1', 'attributed to the version it came from')
        self.assertEqual(self.store.config(om.LESSONS_MIGRATION_KEY)['restored'], 3)
        self.assertIsNone(self.lessons.migrate(), 'exactly once')
        self.assertEqual(len(self.lessons.current()), 3)

    def test_a_point_also_in_a_retained_version_comes_back_even_if_a_forgotten_version_held_it(self):
        self.history()
        self.lessons.migrate()
        self.assertIn('a 요약은 세 줄로', self.texts(), 'a and b were in the forgotten v2 and in the kept v1')

    def test_a_restored_lesson_the_owner_forgets_is_not_restored_again(self):
        self.history()
        self.lessons.migrate()
        target = {lesson['text']: lesson for lesson in self.lessons.current()}['d 비교는 표로']
        self.forget.forget(MEMORY_OWNER, target['ref'])
        self.assertIsNone(self.lessons.migrate())
        self.assertEqual(sorted(self.texts()), ['a 요약은 세 줄로', 'b 링크는 맨 끝에'])

    def test_over_long_and_secret_shaped_points_are_skipped_and_counted(self):
        self.store.secret('telegram_token', SECRET)
        self.commit('ok 1', 'z' * (om.POINT_CHARS + 1), f'토큰은 {SECRET}')
        record = self.lessons.migrate()
        self.assertEqual(self.texts(), ['ok 1'])
        self.assertEqual(record['restored'], 1)
        self.assertEqual(record['skipped'], 2)
        self.assertNotIn(SECRET, json.dumps(record))

    def test_nothing_to_restore_still_records_the_run(self):
        record = self.lessons.migrate()
        self.assertEqual(record['restored'], 0)
        self.assertEqual(self.lessons.current(), [])
        self.commit('saved after the run')
        self.assertIsNone(self.lessons.migrate())
        self.assertEqual(self.lessons.current(), [])

    def test_migrated_runs_the_earlier_profile_move_first_and_reports_done(self):
        self.store.save_memory('profile.working.summary', '요약은 세 줄로', MEMORY_OWNER, work_id='w0')
        self.assertTrue(self.lessons.migrated())
        self.assertEqual(self.texts(), ['요약은 세 줄로'])
        self.assertTrue(self.lessons.migrated())
        self.assertEqual(len(self.lessons.current()), 1)

    def test_no_new_working_notes_version_is_written_after_the_migration(self):
        self.history()
        self.lessons.migrate()
        with self.store.db() as db:
            count = db.execute('SELECT COUNT(*) FROM procedure_versions WHERE slot=?', (NOTES_SLOT,)).fetchone()[0]
        self.apply(add('e 새 교훈'))
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM procedure_versions WHERE slot=?', (NOTES_SLOT,)).fetchone()[0],
                             count, 'the versions are history now; lessons live in Memory')


class ReviewPath(SelfReview):
    """The self-review returns operations; the old whole-document answer can no longer drop lessons."""

    def current(self):
        return {lesson['text']: lesson for lesson in om.WorkingLessons(self.store).current()}

    def seed(self, *texts):
        lessons = om.WorkingLessons(self.store)
        self.assertTrue(lessons.migrated())
        lessons.apply([{'op': 'add', 'id': '', 'text': text, 'why': 'w', 'reason': ''} for text in texts], 'seed', None,
                      origin=om.ORIGIN_SELF_REVIEW)
        return self.current()

    def test_a_review_that_adds_one_lesson_keeps_the_others_and_is_attributed_to_the_reviewed_work(self):
        before = self.seed('요약은 세 줄로', '링크는 맨 끝에', '비교는 표로')
        job = self.missed('그거 찾아줘')
        self.answers = [answer(add('"그거"는 직전 메시지의 대상'), change='"그거"가 무엇인지 적었어요')]
        self.service.run_owner_model_upkeep()
        after = self.current()
        self.assertEqual(sorted(after), sorted([*before, '"그거"는 직전 메시지의 대상']))
        self.assertEqual(after['"그거"는 직전 메시지의 대상']['work_id'], job)
        for text, lesson in before.items():
            self.assertEqual(after[text]['memory_id'], lesson['memory_id'])
        [(_status, detail)] = self.evidence(job)
        self.assertNotIn('직전 메시지', json.dumps(detail, ensure_ascii=False), 'Evidence never keeps lesson text')

    def test_the_judgment_reads_every_current_lesson_with_its_id_and_answers_operations(self):
        before = self.seed('요약은 세 줄로')
        self.missed('그거 찾아줘')
        self.answers = [answer()]
        self.service.run_owner_model_upkeep()
        context, _question, schema = self.facts[-1]
        lesson = before['요약은 세 줄로']
        self.assertIn(lesson['id'], context.facts['working_lessons'])
        self.assertIn('요약은 세 줄로', context.facts['working_lessons'])
        self.assertEqual(sorted(schema['properties']), ['blocker', 'change', 'lessons'])
        ops = schema['properties']['lessons']['items']
        self.assertEqual(sorted(ops['properties']['op']['enum']), ['add', 'remove', 'update'])
        self.assertEqual(sorted(ops['required']), ['id', 'op', 'reason', 'text', 'why'])

    def test_a_remove_without_a_reason_from_the_review_leaves_the_lesson(self):
        before = self.seed('요약은 세 줄로', '링크는 맨 끝에')
        job = self.missed('그거 찾아줘')
        self.answers = [answer(remove(before['요약은 세 줄로']['id'], ''), change='줄였어요')]
        self.service.run_owner_model_upkeep()
        self.assertEqual(sorted(self.current()), sorted(before))
        [(_status, detail)] = self.evidence(job)
        self.assertIn('remove-reason', reasons(detail['dropped']))


class DreamPath(Dream):
    """The dream answers operations too, under its two-occasion rule and its no-mass-removal rule."""

    def look_back(self, data, turns):
        anchor = self.settled('anchor')
        self.dreams = [data]
        return self.upkeep.run_dream(self.store.job(anchor), {}, self.service._owner_model_judgments(anchor),
                                     turns=turns, profile='', clock='2026-11-05 03:00',
                                     diary_date='2026-11-04', ends_after=time.time())[3]

    def two_days(self):
        return [('t1', 'first words', 'answer', '2026-11-03 10:00', 'done'),
                ('t2', 'second words', 'answer', '2026-11-04 10:00', 'done')]

    def seed(self, *texts):
        lessons = om.WorkingLessons(self.store)
        self.assertTrue(lessons.migrated())
        lessons.apply([{'op': 'add', 'id': '', 'text': text, 'why': 'w', 'reason': ''} for text in texts], 'seed', None,
                      origin=om.ORIGIN_SELF_REVIEW)
        return {lesson['text']: lesson for lesson in lessons.current()}

    def dream(self, *ops, sources=('t1', 't2')):
        return {'proposals': [], 'lessons': list(ops), 'change': '바꿈', 'lesson_sources': list(sources), 'diary': ''}

    def test_a_dream_that_adds_one_lesson_keeps_every_existing_lesson(self):
        before = self.seed('가', '나', '다')
        self.look_back(self.dream(add('라')), self.two_days())
        after = {lesson['text']: lesson for lesson in om.WorkingLessons(self.store).current()}
        self.assertEqual(sorted(after), ['가', '나', '다', '라'])
        for text, lesson in before.items():
            self.assertEqual(after[text]['memory_id'], lesson['memory_id'])
        self.assertEqual(after['라']['origin'], om.ORIGIN_DREAM)

    def test_a_lesson_from_one_occasion_is_refused(self):
        self.seed('가')
        detail = self.look_back(self.dream(add('라'), sources=('t1',)), self.two_days())
        self.assertEqual([lesson['text'] for lesson in om.WorkingLessons(self.store).current()], ['가'])
        self.assertIn('one-occasion', reasons(detail['dropped']))

    def test_removing_more_than_half_of_the_lessons_at_once_is_refused(self):
        before = self.seed('가', '나', '다')
        detail = self.look_back(self.dream(remove(before['가']['id']), remove(before['나']['id'])), self.two_days())
        self.assertEqual(sorted(lesson['text'] for lesson in om.WorkingLessons(self.store).current()), ['가', '나', '다'])
        self.assertIn('lessons-rewrite', reasons(detail['dropped']))

    def test_the_dream_schema_carries_operations_not_a_document(self):
        props = om.DREAM_SCHEMA['properties']
        self.assertIn('lessons', props)
        self.assertIn('lesson_sources', props)
        self.assertNotIn('notes', props)
        self.assertNotIn('notes_sources', props)


class Told(TelegramHarness):
    """A kept operation set is told once with the existing notice; the restore is told once with its count."""

    def setUp(self):
        super().setUp()
        self.answers = []

        def structured(context, question, schema):
            if context.purpose == om.REVIEW_PURPOSE and self.answers:
                return StructuredDecision(OUTCOME_DECIDED, self.answers.pop(0), fixture_confidence(0.9))
            return None
        self.service.use_decision_engine(FixtureDecisionEngine(structured=structured))
        self.service.owner_model_spawn = lambda target: target()

    def telegram_work(self, text='그거 찾아줘', status='failed'):
        job = self.store.enqueue(text, f'wl-{time.time()}-{text}', channel=f'telegram:{GENERATION}', chat_id=CHAT)
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status=?,response='못 찾았어요',provider='subscription',delivery='sent' WHERE id=?",
                       (status, job))
        return job

    def reviewed(self, data):
        job = self.telegram_work()
        self.assertTrue(om.enqueue_self_review(self.store, job, om.REVIEW_FAILED))
        self.answers = [data]
        self.assertTrue(self.service.run_owner_model_upkeep())
        return job

    def test_one_notice_per_kept_change_with_the_change_line(self):
        change = '"그거"가 직전 메시지의 대상을 가리킨다고 적었어요'
        job = self.reviewed(answer(add('"그거"는 직전 메시지에서 말한 것'), change=change))
        self.assertTrue(self.service.deliver_notification())
        self.assertEqual(self.sends()[-1]['text'], msg('working_notes.saved', change=change))
        self.assertEqual([row['state'] for row in self.notification(job, om.NOTICE_KIND)], ['sent'])
        again = self.reviewed(answer(add('"그거"는  직전 메시지에서 말한 것'), change='같은 내용'))
        self.assertEqual(self.notification(again, om.NOTICE_KIND), [], 'nothing kept: no notice')

    def test_a_change_whose_lessons_were_all_forgotten_before_the_send_is_not_told(self):
        job = self.reviewed(answer(add('요약은 세 줄로'), change='요약 길이'))
        [lesson] = om.WorkingLessons(self.store).current()
        OwnerForget(self.store).forget(MEMORY_OWNER, lesson['ref'])
        self.service.deliver_notification()
        self.assertEqual([row['state'] for row in self.notification(job, om.NOTICE_KIND)], ['cancelled'])

    def test_the_restore_is_told_once_with_how_many_lessons_came_back(self):
        library = ProcedureLibrary(self.store)
        library.commit(NOTES_SLOT, om.notes_text(['요약은 세 줄로', '링크는 맨 끝에']), stats={'change': 'c'})
        library.commit(NOTES_SLOT, om.notes_text(['비교는 표로']), stats={'change': 'c'})
        latest = self.telegram_work('오늘 일정', status='succeeded')
        # Simulate the store as it was before the upgrade: the restore has not run yet.
        with self.store.db() as db:
            db.execute('DELETE FROM config WHERE key=?', (om.LESSONS_MIGRATION_KEY,))
        record = om.WorkingLessons(self.store).migrate()
        self.assertEqual(record['restored'], 3)
        rows = self.notification(latest, om.RESTORED_NOTICE_KIND)
        self.assertEqual(len(rows), 1, 'told on the latest conversation Work')
        self.assertTrue(self.service.deliver_notification())
        self.assertEqual(self.sends()[-1]['text'], msg('working_lessons.restored', count=3))
        self.assertIsNone(om.WorkingLessons(self.store).migrate())
        self.assertEqual(len(self.notification(latest, om.RESTORED_NOTICE_KIND)), 1, 'once')

    def test_the_information_use_audit_names_the_lessons_sent_never_their_text(self):
        self.reviewed(answer(add('요약은: 세 줄로'), add('링크는 맨 끝에'), change='요약 길이'))
        keys = sorted(lesson['key'] for lesson in om.WorkingLessons(self.store).current())
        refs = self.service.owner_information_refs({'profile': self.service.owner_profile_snapshot('요약')})
        for key in keys:
            self.assertIn(key, json.dumps(refs))
        self.assertNotIn('세 줄', json.dumps(refs, ensure_ascii=False))

    def test_the_brief_carries_the_lessons_block(self):
        self.reviewed(answer(add('요약은 세 줄로'), change='요약 길이'))
        self.assertIn('요약은 세 줄로', self.service.owner_profile_snapshot('요약해줘'))
        self.assertNotIn('요약은 세 줄로', self.service.owner_profile_snapshot('요약해줘', notes=False))


if __name__ == '__main__':
    unittest.main()
