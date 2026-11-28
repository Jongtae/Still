"""#1284: an entrusted line learns from settled outcomes (decision record -> settlement -> lesson -> recall).

Evidence class: model-free unit tests with the real QuickStore, ``AgentService``, preparations,
entrusted lines, working lessons and owner forget/undo, a FixtureDecisionEngine and a fake clock.
No live model is contacted. Still records, settles and recalls; the run's own AI judges (C16).
"""
import json

from personal_agent import line_decisions as ld
from personal_agent import owner_model as om
from personal_agent.agent_runtime import MEMORY_OWNER, ToolError
from personal_agent.decision import FixtureDecisionEngine
from personal_agent.messages import msg
from personal_agent.owner_forget import OwnerForget
from test_run_revises_own_plan_968 import _RunCase


HOUR = 3600


class _Case(_RunCase):
    def setUp(self):
        super().setUp()
        self.purposes = []

        def judge(context, proposition):
            self.purposes.append(context.purpose)
            return None
        self.service.use_decision_engine(FixtureDecisionEngine(judge=judge))
        self.brief = self.line()
        self.line_id = self.brief['line_id']
        self.row, self.run = self.running(line=self.line_id)

    def decide(self, run=None, **extra):
        args = {'action': 'decide', 'line': self.line_id, 'about': 'item-A',
                'claim': 'The list grows because the source added a weekly batch.',
                'expectation': 'The list has at least 3 more entries one hour later.',
                'settle_after': str(self.clock[0] + HOUR), 'check': 'count the entries again',
                **extra}
        return self.line_call(run or self.run, **args)

    def settle_decision(self, decision, run=None, **extra):
        args = {'action': 'settle', 'line': self.line_id, 'decision': decision, **extra}
        return self.line_call(run or self.run, **args)

    def learn(self, ops, run=None):
        return self.line_call(run or self.run, action='learn', line=self.line_id, lessons=json.dumps(ops))

    def settled(self, **extra):
        decision = self.decide(**extra)['decision']
        self.clock[0] += HOUR + 1
        self.settle_decision(decision, outcome='4 more entries', matched='yes')
        return decision

    def decisions(self):
        return ld.LineDecisions(self.store, lambda: self.clock[0])

    def lessons(self):
        return om.WorkingLessons(self.store, lambda: self.clock[0])

    def lesson_by_text(self, text):
        return next((lesson for lesson in self.lessons().current() if lesson['text'] == text), None)


class DecisionRecordTests(_Case):
    def test_a_run_records_a_decision_on_its_own_line_without_any_model_call(self):
        result = self.decide(lessons_applied='')
        self.assertEqual(result['state'], ld.PENDING)
        row = self.decisions().get(result['decision'])
        self.assertEqual((row['line_id'], row['work_id'], row['subject'], row['state']),
                         (self.line_id, self.run['id'], 'item-A', ld.PENDING))
        self.assertEqual(row['settle_after'], self.clock[0] + HOUR)
        self.assertEqual(row['made_at'], self.clock[0])
        self.assertEqual(self.purposes, [], 'recording a decision asks no judgment')

    def test_lessons_applied_are_kept_for_attribution(self):
        self.lessons().migrate()
        self.lessons().apply([{'op': 'add', 'id': '', 'text': 'Check the source twice.', 'why': 'w', 'reason': ''}],
                             'seed', None, origin='test')
        lesson = self.lesson_by_text('Check the source twice.')
        result = self.decide(lessons_applied=lesson['ref'])
        self.assertEqual(self.decisions().get(result['decision'])['lessons_applied'], [lesson['id']])

    def test_a_run_cannot_record_on_another_line(self):
        other = self.service.lines.open('owner-other', task='Another watch', report_promise='a note')['line_id']
        with self.assertRaises(ToolError) as refused:
            self.line_call(self.run, action='decide', line=other, about='s', claim='c', expectation='e',
                           settle_after=str(self.clock[0] + HOUR))
        self.assertEqual(refused.exception.code, 'unattended_run')
        self.assertEqual(self.decisions().line_rows(other), [])

    def test_missing_fields_and_past_or_unreadable_settle_time_are_refused(self):
        for extra in ({'claim': ''}, {'expectation': ''}, {'about': ''}, {'settle_after': 'soon'},
                      {'settle_after': str(self.clock[0] - 1)}):
            with self.subTest(extra=extra), self.assertRaises(ToolError) as refused:
                self.decide(**extra)
            self.assertEqual(refused.exception.code, 'line_decision_invalid')
        self.assertEqual(self.decisions().line_rows(self.line_id), [])

    def test_iso_settle_time_is_accepted(self):
        from datetime import datetime, timezone
        when = datetime.fromtimestamp(self.clock[0] + 2 * HOUR, timezone.utc).isoformat()
        row = self.decisions().get(self.decide(settle_after=when)['decision'])
        self.assertEqual(row['settle_after'], self.clock[0] + 2 * HOUR)

    def test_a_stored_secret_never_reaches_the_record(self):
        secret = 'stored-private-value-1284'
        self.store.secret('decision_model_key', secret)
        row = self.decisions().get(self.decide(claim=f'Use {secret} to read it.')['decision'])
        self.assertNotIn(secret, json.dumps(row))

    def test_one_run_records_a_bounded_number_of_decisions(self):
        for _ in range(ld.MAX_RUN_DECISIONS):
            self.decide()
        with self.assertRaises(ToolError) as refused:
            self.decide()
        self.assertEqual(refused.exception.code, 'line_decision_limit')

    def test_long_text_is_cut_to_the_field_limit(self):
        row = self.decisions().get(self.decide(claim='x' * (ld.CLAIM_CHARS + 50))['decision'])
        self.assertLessEqual(len(row['claim']), ld.CLAIM_CHARS)


class RefusalNamesFieldTests(_Case):
    """#1309: each refusal names the field and the rule, so one retry can fix the call."""

    def refused(self, code, call, *named):
        with self.assertRaises(ToolError) as refused:
            call()
        self.assertEqual(refused.exception.code, code)
        for text in named:
            self.assertIn(text, str(refused.exception))
        return str(refused.exception)

    def due(self):
        decision = self.decide()['decision']
        self.clock[0] += HOUR + 1
        return decision

    def test_decide_names_each_missing_field(self):
        for field in ('about', 'claim', 'expectation', 'settle_after'):
            with self.subTest(field=field):
                self.refused('line_decision_invalid', lambda: self.decide(**{field: ''}), f': {field}.')
        self.refused('line_decision_invalid', lambda: self.decide(claim='', settle_after=''),
                     'claim, settle_after')

    def test_decide_names_an_unreadable_or_non_finite_settle_after(self):
        for value in ('soon', 'inf'):
            with self.subTest(value=value):
                self.refused('line_decision_invalid', lambda: self.decide(settle_after=value),
                             'settle_after:', 'ISO 8601')

    def test_decide_names_a_settle_after_that_is_not_in_the_future(self):
        self.refused('line_decision_invalid', lambda: self.decide(settle_after=str(self.clock[0])),
                     'settle_after:', '지금')

    def test_settle_names_a_missing_decision(self):
        self.refused('line_decision_invalid', lambda: self.settle_decision('', outcome='o', matched='yes'),
                     ': decision.')

    def test_settle_names_an_unknown_decision(self):
        self.refused('line_decision_unknown',
                     lambda: self.settle_decision('no-such-id', outcome='o', matched='yes'), 'decision:')

    def test_settle_names_a_closed_decision(self):
        decision = self.settled()
        self.refused('line_decision_settled',
                     lambda: self.settle_decision(decision, outcome='o', matched='yes'), 'decision:')

    def test_settle_says_when_a_decision_becomes_due(self):
        from datetime import datetime, timezone
        decision = self.decide()['decision']
        message = self.refused('line_decision_not_due',
                               lambda: self.settle_decision(decision, outcome='o', matched='yes'), 'decision:')
        due = datetime.fromisoformat(message.split('. ')[-1].split('부터')[0]).astimezone(timezone.utc)
        self.assertGreaterEqual(due.timestamp(), self.decisions().get(decision)['settle_after'])

    def test_settle_names_missing_outcome_and_matched(self):
        decision = self.due()
        self.refused('line_decision_invalid', lambda: self.settle_decision(decision, matched='yes'), ': outcome.')
        self.refused('line_decision_invalid', lambda: self.settle_decision(decision, outcome='o'), ': matched.')

    def test_settle_names_the_values_matched_takes(self):
        decision = self.due()
        self.refused('line_decision_invalid',
                     lambda: self.settle_decision(decision, outcome='o', matched='maybe'),
                     'matched:', 'yes, no, unclear')

    def test_settle_names_the_fields_that_cannot_go_with_unsettleable(self):
        decision = self.due()
        self.refused('line_decision_invalid',
                     lambda: self.settle_decision(decision, unsettleable='gone', outcome='o', matched='yes'),
                     'unsettleable:', 'outcome, matched')

    def test_settle_names_an_unreadable_observed_at(self):
        decision = self.due()
        self.refused('line_decision_invalid',
                     lambda: self.settle_decision(decision, outcome='o', matched='yes', observed_at='today'),
                     'observed_at:', 'ISO 8601')

    def test_settle_names_the_window_observed_at_must_fall_in(self):
        decision = self.due()
        self.refused('line_decision_invalid',
                     lambda: self.settle_decision(decision, outcome='o', matched='yes',
                                                  observed_at=str(self.clock[0] + 5)),
                     'observed_at:', '+00:00')

    def test_learn_names_the_broken_operation_field(self):
        decision = self.settled()
        good = {'op': 'add', 'text': 'A lesson.', 'decisions': [decision]}
        cases = (('lessons:', []), ('lessons:', {'op': 'add'}),
                 ('lessons:', [good] * (om.MAX_LESSON_OPS + 1)),
                 ('lessons[1]:', [good, 'text']),
                 ('lessons[1].op:', [good, {**good, 'op': 'drop'}]),
                 ('lessons[0].id:', [{**good, 'op': 'agree'}]),
                 ('lessons[0].text:', [{**good, 'text': 'x' * (om.POINT_CHARS + 1)}]),
                 ('lessons[0].why:', [{**good, 'why': 'x' * (om.CHANGE_CHARS + 1)}]))
        for named, ops in cases:
            with self.subTest(named=named, ops=ops):
                self.refused('line_lesson_invalid', lambda: self.learn(ops), named)
        self.refused('line_lesson_invalid',
                     lambda: self.line_call(self.run, action='learn', line=self.line_id, lessons='add it'),
                     'lessons:', 'JSON')

    def test_learn_names_the_operation_without_a_settled_citation(self):
        decision = self.settled()
        self.refused('line_lesson_uncited', lambda: self.learn(
            [{'op': 'add', 'text': 'One.', 'decisions': [decision]},
             {'op': 'add', 'text': 'Two.', 'decisions': []}]), 'lessons[1].decisions:')


class SettlementTests(_Case):
    def test_a_decision_settles_only_after_its_window(self):
        decision = self.decide()['decision']
        with self.assertRaises(ToolError) as refused:
            self.settle_decision(decision, outcome='2 more entries', matched='no')
        self.assertEqual(refused.exception.code, 'line_decision_not_due')
        self.clock[0] += HOUR + 1
        observed = self.clock[0] - 10
        result = self.settle_decision(decision, outcome='2 more entries', matched='no', observed_at=str(observed))
        self.assertEqual(result['state'], ld.SETTLED)
        row = self.decisions().get(decision)
        self.assertEqual((row['state'], row['outcome'], row['matched'], row['resolved_at'], row['settle_work_id']),
                         (ld.SETTLED, '2 more entries', 'no', observed, self.run['id']))
        self.assertEqual(self.purposes, [], 'Still records the outcome the AI checked; it judges nothing')

    def test_a_settled_decision_is_not_settled_again(self):
        decision = self.settled()
        with self.assertRaises(ToolError) as refused:
            self.settle_decision(decision, outcome='other', matched='no')
        self.assertEqual(refused.exception.code, 'line_decision_settled')

    def test_outcome_observed_in_the_future_or_before_the_decision_is_refused(self):
        decision = self.decide()['decision']
        made = self.decisions().get(decision)['made_at']
        self.clock[0] += HOUR + 1
        for observed in (self.clock[0] + 5, made - 5):
            with self.subTest(observed=observed), self.assertRaises(ToolError) as refused:
                self.settle_decision(decision, outcome='o', matched='yes', observed_at=str(observed))
            self.assertEqual(refused.exception.code, 'line_decision_invalid')

    def test_matched_is_yes_no_or_unclear(self):
        decision = self.decide()['decision']
        self.clock[0] += HOUR + 1
        with self.assertRaises(ToolError) as refused:
            self.settle_decision(decision, outcome='o', matched='maybe')
        self.assertEqual(refused.exception.code, 'line_decision_invalid')
        self.assertEqual(self.settle_decision(decision, outcome='o', matched='unclear')['state'], ld.SETTLED)

    def test_a_decision_that_cannot_be_settled_keeps_its_reason_and_ends_after_bounded_attempts(self):
        decision = self.decide()['decision']
        self.clock[0] += HOUR + 1
        for attempt in range(1, ld.SETTLE_ATTEMPTS):
            result = self.settle_decision(decision, unsettleable=f'source unavailable {attempt}')
            self.assertEqual(result['state'], ld.PENDING)
            row = self.decisions().get(decision)
            self.assertEqual((row['attempts'], row['reason']), (attempt, f'source unavailable {attempt}'))
        result = self.settle_decision(decision, unsettleable='source gone')
        self.assertEqual(result['state'], ld.UNSETTLEABLE)
        self.assertEqual(self.decisions().get(decision)['reason'], 'source gone')

    def test_a_decision_of_another_line_is_unknown_here(self):
        other = self.service.lines.open('owner-other', task='Another watch', report_promise='a note')['line_id']
        other_row, other_run = self.running(line=other)
        decision = self.line_call(other_run, action='decide', line=other, about='s', claim='c', expectation='e',
                                  settle_after=str(self.clock[0] + 1))['decision']
        self.clock[0] += 2
        with self.assertRaises(ToolError) as refused:
            self.settle_decision(decision, outcome='o', matched='yes')
        self.assertEqual(refused.exception.code, 'line_decision_unknown')


class LessonTests(_Case):
    def add(self, text='Wait for a second batch before counting a change.'):
        decision = self.settled()
        result = self.learn([{'op': 'add', 'text': text, 'why': 'the batch arrived late', 'decisions': [decision]}])
        return decision, result, self.lesson_by_text(text)

    def strength(self, lesson):
        return self.decisions().strength(lesson['key'])

    def test_add_cites_a_settled_decision_and_starts_at_strength_two(self):
        decision, result, lesson = self.add()
        self.assertIsNotNone(lesson, result)
        self.assertEqual(self.strength(lesson), ld.ADD_STRENGTH)
        self.assertEqual(ld.ADD_STRENGTH, 2)
        self.assertEqual(lesson['origin'], ld.ORIGIN_LINE_OUTCOMES)
        self.assertEqual(self.decisions().lesson_line(lesson['key']), self.line_id)
        self.assertEqual(self.purposes, [], 'the run wrote the lesson; Still asked no judgment')

    def test_a_lesson_with_no_settled_evidence_is_refused(self):
        pending = self.decide()['decision']
        for cited in ([], [pending], ['unknown-id']):
            with self.subTest(cited=cited), self.assertRaises(ToolError) as refused:
                self.learn([{'op': 'add', 'text': 'A new lesson.', 'why': 'w', 'decisions': cited}])
            self.assertEqual(refused.exception.code, 'line_lesson_uncited')
        self.assertIsNone(self.lesson_by_text('A new lesson.'))

    def test_too_many_ops_in_one_reflection_are_refused(self):
        decision = self.settled()
        ops = [{'op': 'add', 'text': f'Lesson {n}.', 'why': 'w', 'decisions': [decision]}
               for n in range(om.MAX_LESSON_OPS + 1)]
        with self.assertRaises(ToolError) as refused:
            self.learn(ops)
        self.assertEqual(refused.exception.code, 'line_lesson_invalid')

    def test_agree_and_edit_raise_strength_and_edit_keeps_the_lesson_key(self):
        decision, _result, lesson = self.add()
        self.learn([{'op': 'agree', 'id': lesson['id'], 'decisions': [decision]}])
        self.assertEqual(self.strength(lesson), 3)
        self.learn([{'op': 'edit', 'id': lesson['id'], 'text': 'Wait for the second batch, then count.',
                     'why': 'clearer', 'decisions': [decision]}])
        edited = self.lesson_by_text('Wait for the second batch, then count.')
        self.assertEqual(edited['key'], lesson['key'])
        self.assertEqual(self.strength(edited), 4)

    def test_downvote_to_zero_ends_the_lesson_with_a_visible_reason_the_owner_can_undo(self):
        decision, _result, lesson = self.add()
        self.learn([{'op': 'downvote', 'id': lesson['id'], 'decisions': [decision]}])
        self.assertEqual(self.strength(lesson), 1)
        self.assertIsNotNone(self.lesson_by_text(lesson['text']))
        result = self.learn([{'op': 'downvote', 'id': lesson['id'], 'decisions': [decision]}])
        self.assertIsNone(self.lesson_by_text(lesson['text']))
        with self.store.db() as db:
            row = db.execute('SELECT state,invalidation_reason FROM memories WHERE id=?',
                             (lesson['memory_id'],)).fetchone()
        self.assertNotEqual(row['state'], 'current')
        self.assertEqual(row['invalidation_reason'], msg('lines.lesson_outcomes_contradicted'))
        [removed] = [item for item in result['applied'] if item['op'] == 'remove']
        forget = OwnerForget(self.store, lambda: self.clock[0])
        self.assertIn(removed['receipt'], [entry['receipt'] for entry in forget.undoable(MEMORY_OWNER)])
        forget.undo(MEMORY_OWNER, removed['receipt'])
        self.assertIsNotNone(self.lesson_by_text(lesson['text']), 'undo puts the lesson back')

    def test_downvote_counts_three_when_lessons_are_full(self):
        decision, _result, lesson = self.add()
        self.learn([{'op': 'agree', 'id': lesson['id'], 'decisions': [decision]}])
        self.learn([{'op': 'agree', 'id': lesson['id'], 'decisions': [decision]}])  # strength 4
        filler = [{'op': 'add', 'id': '', 'text': f'Filler lesson {n}.', 'why': 'w', 'reason': ''}
                  for n in range(om.LESSONS_MAX - 1)]
        for start in range(0, len(filler), om.MAX_LESSON_OPS):
            self.lessons().apply(filler[start:start + om.MAX_LESSON_OPS], 'fill', None, origin='test')
        self.assertEqual(len(self.lessons().current()), om.LESSONS_MAX)
        self.learn([{'op': 'downvote', 'id': lesson['id'], 'decisions': [decision]}])
        self.assertEqual(self.strength(lesson), 1)

    def test_a_lesson_from_owner_feedback_without_strength_starts_from_the_add_strength(self):
        self.lessons().migrate()
        self.lessons().apply([{'op': 'add', 'id': '', 'text': 'Owner said: short notes.', 'why': 'w', 'reason': ''}],
                             'seed', None, origin='test')
        lesson = self.lesson_by_text('Owner said: short notes.')
        decision = self.settled()
        self.learn([{'op': 'downvote', 'id': lesson['id'], 'decisions': [decision]}])
        self.assertEqual(self.strength(lesson), ld.ADD_STRENGTH - 1)

    def test_outcomes_never_rewrite_or_end_a_lesson_saved_another_way(self):
        self.lessons().migrate()
        self.lessons().apply([{'op': 'add', 'id': '', 'text': 'Owner said: keep notes short.', 'why': 'w', 'reason': ''}],
                             'seed', None, origin='test')
        lesson = self.lesson_by_text('Owner said: keep notes short.')
        decision = self.settled()
        with self.assertRaises(ToolError) as refused:
            self.learn([{'op': 'edit', 'id': lesson['id'], 'text': 'Long notes.', 'why': 'w', 'decisions': [decision]}])
        self.assertEqual(refused.exception.code, 'line_lesson_refused')
        for _ in range(ld.ADD_STRENGTH + 1):
            self.learn([{'op': 'downvote', 'id': lesson['id'], 'decisions': [decision]}])
        self.assertIsNotNone(self.lesson_by_text('Owner said: keep notes short.'), 'still current')
        self.assertEqual(self.strength(lesson), 0)

    def test_a_line_run_lesson_change_sends_no_messenger_notice(self):
        self.add()
        with self.store.db() as db:
            kinds = [row['kind'] for row in db.execute('SELECT kind FROM telegram_notifications')]
        self.assertNotIn(om.NOTICE_KIND, kinds)

    def test_a_forgotten_lesson_is_not_learned_again(self):
        _decision, _result, lesson = self.add()
        OwnerForget(self.store, lambda: self.clock[0]).forget(MEMORY_OWNER, lesson['ref'])
        decision = self.settled()
        with self.assertRaises(ToolError) as refused:
            self.learn([{'op': 'add', 'text': lesson['text'], 'why': 'again', 'decisions': [decision]}])
        self.assertEqual(refused.exception.code, 'line_lesson_refused')


class RecallTests(_Case):
    def test_a_run_reads_its_due_and_settled_decisions_labelled_as_information(self):
        due = self.decide(about='due-one')['decision']
        settled = self.settled(about='settled-one')
        later = self.decide(about='later-one', settle_after=str(self.clock[0] + 5 * HOUR))['decision']
        self.clock[0] += 1
        text = self.service.preparations_service.line_run_input(self.run, self.row)
        self.assertIn(msg('lines.decisions_heading'), text)
        self.assertIn(due, text)
        self.assertIn('due-one', text)
        self.assertIn(settled, text)
        self.assertIn('4 more entries', text)
        self.assertNotIn(later, text, 'a decision not yet due is only counted')
        self.assertIn(msg('lines.decisions_waiting', count=1), text)

    def test_recall_is_point_in_time(self):
        decision = self.decide()['decision']
        made = self.clock[0]
        self.clock[0] += HOUR + 1
        self.settle_decision(decision, outcome='4 more entries', matched='yes')
        self.assertNotIn('4 more entries', self.decisions().recall_text(self.line_id, as_of=made + 10))
        self.assertIn('4 more entries', self.decisions().recall_text(self.line_id, as_of=self.clock[0]))

    def test_recall_lists_newest_own_settled_decisions_only(self):
        ids = [self.settled(about=f'subject-{n}') for n in range(ld.RECALL_SETTLED + 2)]
        text = self.decisions().recall_text(self.line_id, as_of=self.clock[0])
        for old in ids[:2]:
            self.assertNotIn(old, text)
        for new in ids[2:]:
            self.assertIn(new, text)

    def test_own_line_lessons_and_a_few_strongest_cross_line_lessons(self):
        own = self.settled()
        self.learn([{'op': 'add', 'text': 'Own line lesson.', 'why': 'w', 'decisions': [own]}])
        cross_texts = []
        for n in range(ld.RECALL_CROSS + 1):
            other = self.service.lines.open(f'owner-other-{n}', task=f'Watch {n}', report_promise='a note')['line_id']
            _row, run = self.running(line=other)
            decision = self.line_call(run, action='decide', line=other, about='s', claim='c', expectation='e',
                                      settle_after=str(self.clock[0] + 1))['decision']
            self.clock[0] += 2
            self.line_call(run, action='settle', line=other, decision=decision, outcome='o', matched='yes')
            text = f'Cross lesson {n}.'
            cross_texts.append(text)
            self.line_call(run, action='learn', line=other, lessons=json.dumps(
                [{'op': 'add', 'text': text, 'why': 'w', 'decisions': [decision]}]))
            for _ in range(n):  # later lines' lessons are stronger
                lesson = self.lesson_by_text(text)
                self.line_call(run, action='learn', line=other, lessons=json.dumps(
                    [{'op': 'agree', 'id': lesson['id'], 'decisions': [decision]}]))
        text = self.decisions().recall_text(self.line_id, as_of=self.clock[0])
        self.assertIn('Own line lesson.', text)
        self.assertNotIn(cross_texts[0], text, 'only the strongest cross-line lessons')
        for kept in cross_texts[1:]:
            self.assertIn(kept, text)

    def test_recall_is_bounded(self):
        for n in range(ld.RECALL_SETTLED):
            self.settled(claim='y' * ld.CLAIM_CHARS, about=f's{n}')
        self.assertLessEqual(len(self.decisions().recall_text(self.line_id, as_of=self.clock[0])), ld.RECALL_CHARS)

    def test_a_line_with_no_decisions_adds_only_the_short_how_to(self):
        text = self.decisions().recall_text(self.line_id, as_of=self.clock[0])
        self.assertEqual(text, msg('lines.decisions_how'))


class RetentionTests(_Case):
    def test_oldest_settled_decisions_rotate_out_and_pending_ones_stay(self):
        pending = self.decide(settle_after=str(self.clock[0] + 100 * HOUR))['decision']
        for _ in range(ld.MAX_LINE_DECISIONS + 2):
            self.row, self.run = self.running(line=self.line_id)
            self.settled()
            self.settle(self.row, self.run)
            self.preps.cancel(self.row['id'])  # Retire the fixture schedule before creating the next one.
        rows = self.decisions().line_rows(self.line_id)
        self.assertLessEqual(len(rows), ld.MAX_LINE_DECISIONS)
        self.assertIn(pending, [row['id'] for row in rows])
