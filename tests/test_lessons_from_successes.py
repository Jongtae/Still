"""#1031 step 2: working lessons are learned from what worked too, and method_gap notes feed the dream.

The dream (#402) may add or refine a lesson from the same way of working seen on at least two
occasions on different owner days, at least one of them tonight, whether those occasions went
well, were corrected or failed.  Earlier Works that went well (observed markers only: finished,
no correction, retry or cancel link, no self-review) are shown as ``earlier_successes`` with
``s`` refs when they carry a way-of-working note.  The ``method_gap`` tool keeps its name and
surface; its notes ("the owner's way of working that had to be inferred") reach the dream input
as candidate evidence.  Each dream lesson that adds or rewords passes one narrow judgment that it
is about this owner, not a general rule.

Evidence class: model-free unit and service tests.  A scripted ``FixtureDecisionEngine`` plays the
Judgment AI; the real ``AgentService`` dream caller, ``QuickStore`` Memory paths and
``Upkeep.run_dream`` run unchanged.  No live model.
"""
import json
import time
import unittest

from personal_agent import owner_model as om
from personal_agent.agent_runtime import MEMORY_OWNER, SKILL_ACTIONS
from personal_agent.decision import OUTCOME_DECIDED, BinaryDecision, FixtureDecisionEngine, StructuredDecision, fixture_confidence

from test_owner_model_dream import HOUR, Dream, add_lesson, dream, fact
from test_skill_supply import _Store

TONIGHT = '2026-11-04 10:00'
YESTERDAY = '2026-11-03 10:00'


def lesson_rows(store):
    with store.db() as db:
        return [dict(row) for row in db.execute(
            "SELECT * FROM memories WHERE state='current' AND substr(memory_key,1,?)=? ORDER BY created,rowid",
            (len(om.LESSONS_PREFIX), om.LESSONS_PREFIX))]


def reasons(dropped):
    return [item.get('reason') for item in dropped]


class Lessons(Dream):
    """The look back with a scripted dream answer and a scripted owner-specific check."""

    def setUp(self):
        super().setUp()
        self.checks = []
        self.verdict = True
        structured = self.service.decision_judge.engine._structured

        def judge(context, proposition):
            if context.purpose == om.LESSON_CHECK_PURPOSE:
                self.checks.append((context, proposition))
                if self.verdict is None:
                    return None
                return BinaryDecision(OUTCOME_DECIDED, self.verdict, fixture_confidence(0.9))
            if context.purpose == 'goal-reached':
                return BinaryDecision(OUTCOME_DECIDED, True, fixture_confidence())
            return None
        self.service.use_decision_engine(FixtureDecisionEngine(structured=structured, judge=judge))

    def look_back(self, data, turns, earlier=(), *, source_works=None, earlier_works=None, way_notes=None):
        anchor = self.settled('anchor')
        self.dreams = [data]
        self.anchor = anchor
        state, calls, _status, detail = self.upkeep.run_dream(
            self.store.job(anchor), {}, self.service._owner_model_judgments(anchor), turns=turns, earlier=earlier,
            source_works=source_works, earlier_works=earlier_works, way_notes=way_notes, profile='',
            clock='2026-11-05 03:00', diary_date='2026-11-04', ends_after=time.time())
        self.calls = calls
        return detail


class EvidenceRule(Lessons):
    """Two occasions on two owner days, one of them tonight, of any outcome; never one occasion."""

    def lesson(self, *sources, text='비교할 때는 이 주인이 쓰는 기준표 순서대로'):
        return dream(lessons=[add_lesson(text)], change='비교 방식', lesson_sources=list(sources))

    def test_a_success_only_pattern_seen_once_adds_nothing(self):
        detail = self.look_back(self.lesson('t1'), [('t1', 'tonight words', 'answer', TONIGHT, 'done')])
        self.assertEqual(om.WorkingLessons(self.store).current(), [])
        self.assertIn('one-occasion', reasons(detail['dropped']))
        self.assertIsNone(detail['lessons_evidence']['rule'])

    def test_a_success_pattern_seen_on_two_occasions_adds_one_lesson_with_its_sources(self):
        tonight, earlier = self.settled('tonight work'), self.settled('earlier work')
        detail = self.look_back(self.lesson('t1', 's1'), [('t1', 'tonight words', 'answer', TONIGHT, 'done')],
                                [('s1', 'earlier words', 'earlier answer', YESTERDAY, 'done', '')],
                                source_works={'t1': tonight}, earlier_works={'s1': (earlier, None)})
        [lesson] = om.WorkingLessons(self.store).current()
        self.assertEqual(lesson['origin'], om.ORIGIN_DREAM)
        [row] = lesson_rows(self.store)
        sources = json.loads(row['source_works'])
        self.assertEqual(sources[0], self.anchor, 'the anchor Work stays first, as the notice and undo use it')
        self.assertIn(tonight, sources)
        self.assertIn(earlier, sources)
        self.assertEqual(detail['lessons_evidence'], {'tonight': 1, 'tonight_misses': 0, 'earlier_misses': 0,
                                                      'earlier_successes': 1, 'rule': 'occasions'})
        block = self.asked[0][0].facts['earlier_successes']
        self.assertIn(f'[s1] {YESTERDAY} (done) owner: earlier words', block)
        self.assertNotIn('earlier_successes', self.gathered[0][0].facts, 'the first pass never reads earlier Works')
        self.assertEqual(detail['inputs']['earlier_successes'], 1)
        for text in ('earlier words', 'earlier answer', 'tonight words'):
            self.assertNotIn(text, json.dumps(detail))

    def test_a_success_tonight_and_an_earlier_miss_are_two_occasions(self):
        detail = self.look_back(self.lesson('t1', 'm1'), [('t1', 'today', 'answer', TONIGHT, 'done')],
                                [('m1', 'old', 'answer', YESTERDAY, 'corrected', 'correction words')])
        self.assertEqual(len(om.WorkingLessons(self.store).current()), 1)
        self.assertEqual(detail['lessons_evidence']['rule'], 'occasions')
        self.assertEqual(detail['lessons_evidence']['earlier_misses'], 1)

    def test_two_misses_on_two_days_still_ground_a_lesson(self):
        detail = self.look_back(self.lesson('t1', 'm1'), [('t1', 'today', 'answer', TONIGHT, 'failed')],
                                [('m1', 'old', 'answer', YESTERDAY, 'retried', '')])
        self.assertEqual(len(om.WorkingLessons(self.store).current()), 1)
        self.assertEqual(detail['lessons_evidence']['tonight_misses'], 1)

    def test_two_occasions_on_one_owner_day_are_one_day(self):
        detail = self.look_back(self.lesson('t1', 's1'), [('t1', 'today', 'answer', '2026-11-04 20:00', 'done')],
                                [('s1', 'this morning', 'answer', '2026-11-04 08:00', 'done', '')])
        self.assertEqual(om.WorkingLessons(self.store).current(), [])
        self.assertIn('one-day', reasons(detail['dropped']))

    def test_earlier_occasions_alone_are_not_a_new_lesson(self):
        detail = self.look_back(self.lesson('s1', 'm1'), [('t1', 'today', 'answer', TONIGHT, 'done')],
                                [('m1', 'old', 'answer', '2026-11-02 10:00', 'failed', ''),
                                 ('s1', 'old too', 'answer', YESTERDAY, 'done', '')])
        self.assertEqual(om.WorkingLessons(self.store).current(), [])
        self.assertIsNone(detail['lessons_evidence']['rule'])

    def test_an_unshown_success_ref_supports_nothing(self):
        detail = self.look_back(self.lesson('t1', 's9'), [('t1', 'today', 'answer', TONIGHT, 'done')],
                                [('s1', 'old', 'answer', YESTERDAY, 'done', '')])
        self.assertEqual(om.WorkingLessons(self.store).current(), [])
        self.assertIn('sources', reasons(detail['dropped']))

    def test_earlier_successes_never_ground_facts_or_suggestions(self):
        data = dream([fact('profile.place.home', 'earlier home', 's1', category='place')])
        data['suggestions'] = [{'sources': ['s1']}]
        detail = self.look_back(data, [('t1', 'today', 'answer', TONIGHT, 'done')],
                                [('s1', 'earlier home', 'answer', YESTERDAY, 'done', '')])
        self.assertEqual(self.store.memories(MEMORY_OWNER, key_prefix='profile.place.home'), [])
        self.assertEqual(detail['suggestions']['dropped'], [{'suggestion': 0, 'reason': 'sources'}])

    def test_the_success_block_keeps_whole_newest_lines_under_its_own_cap(self):
        earlier = [(f's{i}', 'a' * 600, 'b' * 300, f'2026-11-03 10:{i:02}', 'done', '') for i in range(1, 13)]
        self.look_back(dream(), [('t1', 'today', 'answer', TONIGHT, 'done')], earlier)
        block = self.asked[0][0].facts['earlier_successes']
        self.assertLessEqual(len(block), om.DREAM_EARLIER_SUCCESS_CHARS)
        self.assertNotIn('[s1]', block)
        self.assertIn('[s12]', block)

    def test_the_mass_removal_guard_still_holds(self):
        lessons = om.WorkingLessons(self.store)
        self.assertTrue(lessons.migrated())
        lessons.apply([{'op': 'add', 'id': '', 'text': text, 'why': 'w', 'reason': ''} for text in ('가', '나', '다')],
                      'seed', None, origin=om.ORIGIN_SELF_REVIEW)
        ids = [lesson['id'] for lesson in lessons.current()]
        data = dream(lessons=[{'op': 'remove', 'id': ident, 'text': '', 'why': '', 'reason': 'r'} for ident in ids[:2]],
                     change='정리', lesson_sources=['t1', 's1'])
        detail = self.look_back(data, [('t1', 'today', 'answer', TONIGHT, 'done')],
                                [('s1', 'old', 'answer', YESTERDAY, 'done', '')])
        self.assertEqual(len(om.WorkingLessons(self.store).current()), 3)
        self.assertIn('lessons-rewrite', reasons(detail['dropped']))


class GeneralRule(Lessons):
    """Each dream lesson that adds or rewords passes a narrow judgment that it is about this owner."""

    def two_occasions(self, *ops):
        return self.look_back(dream(lessons=list(ops), change='바꿈', lesson_sources=['t1', 's1']),
                              [('t1', 'today words', 'answer', TONIGHT, 'done')],
                              [('s1', 'old words', 'answer', YESTERDAY, 'done', '')])

    def test_a_general_rule_is_refused(self):
        self.verdict = False
        detail = self.two_occasions(add_lesson('출처를 확인하고 정확하게 답한다'))
        self.assertEqual(om.WorkingLessons(self.store).current(), [])
        self.assertIn('general-rule', reasons(detail['dropped']))
        [(context, _proposition)] = self.checks
        serialized = json.dumps(context.facts, ensure_ascii=False)
        self.assertIn('출처를 확인하고 정확하게 답한다', serialized)
        for text in ('today words', 'old words'):
            self.assertNotIn(text, serialized, 'the check reads the lesson, not the turns')

    def test_an_unjudged_lesson_is_not_kept(self):
        self.verdict = None
        detail = self.two_occasions(add_lesson('이 주인의 보고서는 결론 한 줄을 맨 위에'))
        self.assertEqual(om.WorkingLessons(self.store).current(), [])
        self.assertIn('general-rule-unjudged', reasons(detail['dropped']))

    def test_an_owner_specific_lesson_is_kept_and_its_check_is_counted(self):
        detail = self.two_occasions(add_lesson('이 주인의 보고서는 결론 한 줄을 맨 위에'))
        self.assertEqual([lesson['text'] for lesson in om.WorkingLessons(self.store).current()],
                         ['이 주인의 보고서는 결론 한 줄을 맨 위에'])
        self.assertEqual(len(self.checks), 1)
        # The gather pass, the look back and the one check.
        self.assertEqual(self.calls, 3)
        self.assertNotIn('general-rule', reasons(detail['dropped']))

    def test_only_the_refused_operation_is_dropped(self):
        verdicts = iter([False, True])
        structured = self.service.decision_judge.engine._structured

        def judge(context, proposition):
            if context.purpose == om.LESSON_CHECK_PURPOSE:
                self.checks.append((context, proposition))
                return BinaryDecision(OUTCOME_DECIDED, next(verdicts), fixture_confidence(0.9))
            return None
        self.service.use_decision_engine(FixtureDecisionEngine(structured=structured, judge=judge))
        detail = self.two_occasions(add_lesson('답을 정확하게 한다'), add_lesson('이 주인의 표는 날짜 열이 맨 왼쪽'))
        self.assertEqual([lesson['text'] for lesson in om.WorkingLessons(self.store).current()],
                         ['이 주인의 표는 날짜 열이 맨 왼쪽'])
        self.assertEqual(reasons(detail['dropped']).count('general-rule'), 1)

    def test_a_remove_needs_no_check(self):
        lessons = om.WorkingLessons(self.store)
        self.assertTrue(lessons.migrated())
        lessons.apply([{'op': 'add', 'id': '', 'text': text, 'why': 'w', 'reason': ''} for text in ('가', '나', '다')],
                      'seed', None, origin=om.ORIGIN_SELF_REVIEW)
        ident = lessons.current()[0]['id']
        self.two_occasions({'op': 'remove', 'id': ident, 'text': '', 'why': '', 'reason': 'the owner asked otherwise'})
        self.assertEqual(self.checks, [])
        self.assertEqual(len(om.WorkingLessons(self.store).current()), 2)


class WayNotes(Lessons):
    """method_gap notes of the shown Works reach the dream as candidate evidence; tonight's and earlier successes'."""

    def note(self, work, text, status='succeeded'):
        with self.store.db() as db:
            db.execute("INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,'method_gap',?,?,?)",
                       (work, status, json.dumps({'host_action': 'method_gap', 'evidence': {'method_gap': text}}),
                        time.time()))

    def earlier(self, text, since, offset, status='succeeded', note=None):
        work = self.settled(text, answer=text + ' answer', status=status)
        with self.store.db() as db:
            db.execute('UPDATE jobs SET created=? WHERE id=?', (since - 86400 + offset, work))
        if note:
            self.note(work, note)
        return work

    def run_service_dream(self, anchor, since):
        self.dreams = [dream()]
        now = self.store.job(anchor)['created'] + 1
        self.service._owner_model_dream({'job_id': anchor, 'reason': str(since)}, self.store.job(anchor), now)
        return self.asked[0][0]

    def test_method_gap_notes_reach_the_dream_input(self):
        now = time.time() + 1
        since = now - HOUR
        went_well = self.earlier('old went well', since, 1, note='표는 날짜순으로 원함')
        self.earlier('old no note', since, 2)
        corrected = self.earlier('old corrected', since, 3, note='corrected note')
        cancelled = self.earlier('old cancelled', since, 4, note='cancelled note')
        reviewed = self.earlier('old reviewed', since, 5, note='reviewed note')
        self.earlier('old partial', since, 6, status='partial', note='partial note')
        for kind, target, text in (('correction', corrected, 'fix it'), ('cancel', cancelled, 'stop')):
            follow = self.settled(text)
            with self.store.db() as db:
                db.execute('UPDATE jobs SET relation_kind=?,related_job_id=?,created=? WHERE id=?',
                           (kind, target, since - 86400 + 50, follow))
        om.enqueue_self_review(self.store, reviewed, om.REVIEW_REACTION, 'owner reaction on the answer')
        anchor = self.settled('tonight request')
        self.note(anchor, '비교는 주인의 기준표 순서로 짐작함')
        self.note(anchor, 'a failed note', status='failed')
        self.note(anchor, 'a running note', status='running')
        context = self.run_service_dream(anchor, since)
        turns = context.facts['turns']
        self.assertIn('owner: tonight request', turns)
        self.assertIn('inferred: 비교는 주인의 기준표 순서로 짐작함', turns)
        self.assertNotIn('a failed note', turns)
        self.assertNotIn('a running note', turns)
        block = context.facts['earlier_successes']
        self.assertIn('owner: old went well', block)
        self.assertIn('inferred: 표는 날짜순으로 원함', block)
        for text in ('old no note', 'old corrected', 'old cancelled', 'old reviewed', 'old partial'):
            self.assertNotIn(text, block)
        self.assertEqual(len(block.splitlines()), 1)
        self.assertNotIn(went_well, block, 'refs, never Work ids')
        detail = self.evidence(anchor)[0][1]
        self.assertEqual(detail['inputs']['earlier_successes'], 1)
        for text in ('표는 날짜순으로 원함', '비교는 주인의 기준표 순서로 짐작함'):
            self.assertNotIn(text, json.dumps(detail, ensure_ascii=False))

    def test_notes_are_scrubbed_with_their_own_works_values(self):
        now = time.time() + 1
        since = now - HOUR
        old = self.earlier('old went well', since, 1, note='old private note')
        anchor = self.settled('tonight request')
        self.note(anchor, 'tonight private note')
        scrub = self.service.scrub_work_text
        self.service.scrub_work_text = lambda work, text: scrub(work, text).replace(
            'old private', 'scrubbed old' if work == old else 'wrong scope').replace(
            'tonight private', 'scrubbed tonight' if work == anchor else 'wrong scope')
        context = self.run_service_dream(anchor, since)
        self.assertIn('inferred: scrubbed tonight note', context.facts['turns'])
        self.assertIn('inferred: scrubbed old note', context.facts['earlier_successes'])
        self.assertNotIn('wrong scope', json.dumps(context.facts, ensure_ascii=False))

    def test_a_marked_works_notes_are_withheld(self):
        """#744: a note is the assistant's text; a Work that read outside content keeps its notes out, like its answer."""
        now = time.time() + 1
        since = now - HOUR
        anchor = self.settled('tonight request')
        self.store.mark_outside_content(anchor, {'category': 'connected-document', 'operation': 'read_file', 'path': 'memo.txt'})
        self.note(anchor, 'poisoned way of working')
        context = self.run_service_dream(anchor, since)
        self.assertIn('owner: tonight request', context.facts['turns'])
        self.assertNotIn('poisoned way of working', json.dumps(context.facts, ensure_ascii=False))

    def test_a_note_line_is_bounded(self):
        notes = {'t1': ['n' * 200, 'o' * 200, 'p' * 200]}
        self.look_back(dream(), [('t1', 'today', 'answer', TONIGHT, 'done')], way_notes=notes)
        [line] = self.asked[0][0].facts['turns'].splitlines()
        inferred = line[line.index('| inferred: ') + len('| inferred: '):]
        self.assertLessEqual(len(inferred), om.DREAM_WAY_NOTE_CHARS)

    def test_without_notes_the_turn_lines_are_unchanged(self):
        self.look_back(dream(), [('t1', 'today', 'answer', TONIGHT, 'done')])
        self.assertNotIn('inferred:', self.asked[0][0].facts['turns'])
        self.assertNotIn('earlier_successes', self.asked[0][0].facts)


class PromptNamesItsInputs(unittest.TestCase):
    def test_the_dream_question_names_every_lesson_input(self):
        for name in ('earlier_misses', 'earlier_successes', 'inferred:', 'lesson_sources'):
            with self.subTest(name=name):
                self.assertIn(name, om.DREAM_QUESTION)


class ToolSurface(_Store):
    """The skill catalogue stays optional: with skills off nothing is offered; with skills on only the skill actions."""

    def test_with_skills_off_the_tool_surface_is_unchanged(self):
        self.assertEqual(SKILL_ACTIONS, frozenset({'skill_load', 'skill_resource', 'method_gap'}))
        off = {d['function']['name'] for d in self.caps().definitions()}
        self.assertFalse(SKILL_ACTIONS & off)
        self.library.set_enabled(True)
        on = {d['function']['name']: d['function'] for d in self.caps(skills=self.library.binding()).definitions()}
        self.assertEqual(set(on) - off, set(SKILL_ACTIONS))
        self.assertEqual(off - set(on), set())
        gap = on['method_gap']['parameters']
        self.assertIn('note', gap['properties'])
        self.assertEqual(gap['required'], ['note'])


if __name__ == '__main__':
    unittest.main()
