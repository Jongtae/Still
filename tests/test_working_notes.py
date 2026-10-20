"""STEADY-NOTES-01 (#19): one bounded, revisable working-notes document.

How to work for the owner lives in one document whose versions sit in the
reserved slot of the procedural version store (``procedures.NOTES_SLOT``),
not in ``profile.working.`` fact rows.  The self-review returns no change or a
whole revision; Still enforces the bounds, secret exclusion, versioning and
the exact undo (``owner_forget``), and moves the earlier ``profile.working.``
rows into the first version once, under undoable receipts.

Evidence class: model-free unit and service tests on a real ``QuickStore``
with a fake clock, a scripted Judgment AI (``FixtureDecisionEngine``) and an
injected Telegram transport.  No live model, no live Telegram.
"""
import json
import tempfile
import time
import unittest

from personal_agent import owner_model as om
from personal_agent.agent_runtime import MEMORY_OWNER, Capabilities
from personal_agent.api_requests import NAME
from personal_agent.decision import OUTCOME_DECIDED, FixtureDecisionEngine, StructuredDecision, fixture_confidence
from personal_agent.messages import msg
from personal_agent.owner_forget import MIGRATE, UNDO_SECONDS, ForgetError, OwnerForget
from personal_agent.procedures import NOTES_SLOT, ProcedureLibrary
from personal_agent.quickstart_service import WORKING_NOTES_KIND
from personal_agent.quickstart_store import QuickStore

from test_memory_candidate_confirm import CHAT, GENERATION, TelegramHarness

SECRET = 'plain-stored-secret-019'


class Clock:
    def __init__(self, now=1_800_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def revision(*points, change='c'):
    text = om.notes_text(list(points))
    return {'points': list(points), 'text': text, 'change': change}


class Document(unittest.TestCase):
    """The document, its versions and the owner's exact undo, on a real store."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = QuickStore(tmp.name)
        self.clock = Clock()
        self.notes = om.WorkingNotes(self.store, self.clock)
        self.forget = OwnerForget(self.store, clock=self.clock)

    def save(self, *points, expected=None):
        return self.notes.save(revision(*points), 'w', expected=expected)

    def test_the_slot_is_reserved_and_no_secret_set_can_name_it(self):
        self.assertIsNone(NAME.match(NOTES_SLOT), 'set names never hold ":"')
        with self.assertRaises(ValueError):
            ProcedureLibrary(self.store).propose('w', NOTES_SLOT, 'def f():\n    pass\n', [])
        self.assertEqual(ProcedureLibrary(self.store).settle('w', True), [], 'the notes have no candidate stage')

    def test_each_revision_is_the_next_version_and_the_brief_carries_the_whole_document(self):
        self.assertIsNone(self.notes.current())
        self.assertEqual(self.notes.block(), '')
        first = self.save('요약은 세 줄로')
        self.clock.now += 60
        second = self.save('요약은 세 줄로', '링크는 맨 끝에', expected=first['id'])
        current = self.notes.current()
        self.assertEqual((first['version'], second['version'], current['id']), (1, 2, second['id']))
        self.assertEqual(current['points'], ['요약은 세 줄로', '링크는 맨 끝에'])
        block = self.notes.block()
        self.assertTrue(block.startswith(f"working_notes v2 (the assistant's own notes on how to work for this owner; "
                                         f"ref {second['ref']}, last change 2027-01-15: c; "))
        self.assertIn(f"forget_record forget {second['ref']} puts the previous notes back", block)
        self.assertTrue(block.endswith(':\n- 요약은 세 줄로\n- 링크는 맨 끝에'), 'one block, every point, in the owner\'s words')

    def test_a_save_based_on_a_document_that_changed_meanwhile_is_refused(self):
        first = self.save('a')
        self.assertIsNone(self.save('a', 'b', expected=None), 'the document exists now')
        self.save('a', 'c', expected=first['id'])
        self.assertIsNone(self.save('a', 'd', expected=first['id']), 'another revision replaced it')
        self.assertEqual(self.notes.current()['points'], ['a', 'c'])

    def test_forgetting_the_version_restores_the_previous_document_for_seven_days_then_it_is_purged(self):
        first = self.save('a')
        second = self.save('a', 'b', expected=first['id'])
        receipt = self.forget.forget(MEMORY_OWNER, second['ref'])
        self.assertEqual(receipt['undo_until'], self.clock.now + UNDO_SECONDS, 'the existing seven-day undo')
        self.assertEqual(self.notes.current()['id'], first['id'], 'the previous document is back')
        self.assertTrue(self.notes.forgotten(om.notes_text(['a', 'b'])), 'the host never saves it again')
        self.assertEqual(self.forget.undoable(MEMORY_OWNER, kind='procedure')[0]['item'], {'working_notes_version': 2})
        self.forget.undo(MEMORY_OWNER, receipt['receipt'])
        self.assertEqual(self.notes.current()['id'], second['id'], 'undo puts the forgotten version back, exactly')
        self.assertFalse(self.notes.forgotten(om.notes_text(['a', 'b'])))
        again = self.forget.forget(MEMORY_OWNER, second['ref'])
        self.clock.now += UNDO_SECONDS + 1
        self.assertEqual(self.forget.purge_due(), 1)
        with self.assertRaises(ForgetError) as refused:
            self.forget.undo(MEMORY_OWNER, again['receipt'])
        self.assertEqual(refused.exception.code, 'settled')
        with self.store.db() as db:
            self.assertIsNone(db.execute('SELECT 1 FROM procedure_versions WHERE id=?', (second['id'],)).fetchone())
            tombstones = [dict(row) for row in db.execute('SELECT * FROM forgotten_values')]
        self.assertEqual(self.notes.current()['id'], first['id'])
        # Review P2: the purge removes the text, but a content-free tombstone keeps the forget authoritative.
        self.assertTrue(self.notes.forgotten(om.notes_text(['a', 'b'])))
        self.assertNotIn('a\\n- b', json.dumps(tombstones))
        self.assertEqual({row['receipt_id'] for row in tombstones}, {again['receipt']})

    def test_the_notice_is_queued_with_the_version_for_a_telegram_work_only(self):
        work = self.store.enqueue('그거 찾아줘', 'wn-tg', channel='telegram:g9', chat_id=4040)
        web = self.store.enqueue('그거 찾아줘', 'wn-web')
        saved = self.notes.save(revision('a'), work)
        self.notes.save(revision('a', 'b'), web, expected=saved['id'])
        with self.store.db() as db:
            rows = [dict(row) for row in db.execute('SELECT job_id,chat_id,generation,kind,fingerprint,state '
                                                    'FROM telegram_notifications')]
        self.assertEqual(rows, [{'job_id': work, 'chat_id': 4040, 'generation': 'g9', 'kind': om.NOTICE_KIND,
                                 'fingerprint': json.dumps({'ref': saved['ref'], 'version': 1}), 'state': 'queued'}],
                         'review P1: durable with the version itself, never left untold by a crash')
        self.assertIsNone(self.save('a', 'c'), 'a refused save queues nothing either')
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM telegram_notifications').fetchone()[0], 1)

    def test_a_later_revision_wins_over_an_undo(self):
        first = self.save('a')
        second = self.save('a', 'b', expected=first['id'])
        receipt = self.forget.forget(MEMORY_OWNER, second['ref'])
        self.save('a', 'c', expected=first['id'])
        with self.assertRaises(ForgetError) as refused:
            self.forget.undo(MEMORY_OWNER, receipt['receipt'])
        self.assertEqual(refused.exception.code, 'later_value')
        self.assertEqual(self.notes.current()['points'], ['a', 'c'])

    def test_points_are_one_per_line_and_the_same_point_is_kept_once(self):
        self.assertEqual(om.notes_points(' * 요약은  세 줄로\n\n- 요약은 세 줄로 \n• Links last\n· links LAST\n-5도면 알려줘'),
                         ['요약은 세 줄로', 'Links last', '-5도면 알려줘'])
        self.assertEqual(om.notes_text(['a', 'b']), '- a\n- b')

    def test_bounds_are_checked_whole_and_never_cut(self):
        exact = [f'{n:02d}' + 'y' * 118 for n in range(om.NOTES_POINTS)]
        self.assertLessEqual(len(om.notes_text(exact)), om.NOTES_CHARS)
        kept, _blocker, dropped = om.validate_review({'notes': '\n'.join(exact), 'change': 'c', 'blocker': ''})
        self.assertEqual((len(kept['points']), dropped), (om.NOTES_POINTS, []))
        for notes, reason in (('\n'.join(exact + ['one more']), 'points'),
                              ('- ' + 'z' * (om.POINT_CHARS + 1), 'point-length'),
                              ('\n'.join(f'{n}' + 'w' * 198 for n in range(8)), 'length')):
            kept, _blocker, dropped = om.validate_review({'notes': notes, 'change': 'c', 'blocker': ''})
            self.assertEqual((kept, dropped), (None, [{'note': 'notes', 'reason': reason}]), reason)

    def test_a_reworded_marker_spacing_case_or_order_is_no_change(self):
        current = ['요약은 세 줄로', 'Links last']
        for notes in ('- 요약은 세 줄로\n- Links last', '*  요약은 세 줄로 \n• links LAST', '- Links last\n- 요약은 세 줄로',
                      '- 요약은 세 줄로\n- Links last\n- 요약은  세 줄로'):
            kept, _blocker, dropped = om.validate_review({'notes': notes, 'change': 'reworded', 'blocker': ''}, current)
            self.assertEqual((kept, dropped), (None, [{'note': 'notes', 'reason': 'unchanged'}]), notes)
        kept, _blocker, dropped = om.validate_review({'notes': '- 요약은 세 줄로', 'change': 'dropped the links point',
                                                      'blocker': ''}, current)
        self.assertEqual(kept['points'], ['요약은 세 줄로'], 'removing a point that proved wrong is a change')


class Migration(unittest.TestCase):
    """The earlier ``profile.working.`` lessons move into the first version once, under undoable receipts."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = QuickStore(tmp.name)
        self.clock = Clock()
        self.notes = om.WorkingNotes(self.store, self.clock)
        self.forget = OwnerForget(self.store, clock=self.clock)

    def save(self, key, content):
        self.clock.now += 1
        row = self.store.save_memory(key, content, MEMORY_OWNER, work_id='w0')
        with self.store.db() as db:
            db.execute('UPDATE memories SET created=? WHERE id=?', (self.clock.now, row['id']))
        return row

    def current_memory(self):
        return sorted((row['memory_key'], row['content']) for row in self.store.memories(MEMORY_OWNER))

    def test_rows_are_consolidated_into_v1_with_one_receipt_each_and_facts_stay_in_memory(self):
        self.save('profile.place.home', '분당')
        self.save('profile.working.references', '"그거"는 직전 메시지에서 말한 것')
        self.save('profile.working.summary', '요약은 세 줄로')
        self.save('profile.working.summary-again', '  요약은  세 줄로 ')
        record = self.notes.migrate()
        self.assertEqual((record['moved'], record['left'], record['points'], record['version']), (3, 0, 2, 1))
        current = self.notes.current()
        self.assertEqual(current['points'], ['"그거"는 직전 메시지에서 말한 것', '요약은 세 줄로'], 'oldest first, said once')
        self.assertEqual((current['change'], current['work_id']), (om.MIGRATION_CHANGE, None))
        self.assertEqual(self.current_memory(), [('profile.place.home', '분당')], 'the semantic fact stays in Memory')
        with self.store.db() as db:
            receipts = [dict(row) for row in db.execute('SELECT * FROM owner_state_receipts ORDER BY created')]
        self.assertEqual(sorted(row['id'] for row in receipts), sorted(record['receipts']))
        self.assertEqual({(row['operation'], row['kind'], row['state']) for row in receipts}, {(MIGRATE, 'memory', 'forgotten')})
        self.assertNotIn('세 줄', json.dumps(receipts, ensure_ascii=False), 'receipts keep no content')
        self.assertEqual(self.store.config(om.MIGRATION_KEY)['receipts'], record['receipts'])
        listed = self.forget.undoable(MEMORY_OWNER, kind='memory')
        self.assertEqual(sorted(item['item']['memory_key'] for item in listed),
                         ['profile.working.references', 'profile.working.summary', 'profile.working.summary-again'],
                         "the owner's AI can list what moved and undo it")
        self.assertIsNone(self.notes.migrate(), 'once')

    def test_a_migration_receipt_is_undone_like_a_forget_and_the_move_does_not_run_again(self):
        row = self.save('profile.working.summary', '요약은 세 줄로')
        record = self.notes.migrate()
        self.forget.undo(MEMORY_OWNER, record['receipts'][0])
        self.assertEqual(self.current_memory(), [('profile.working.summary', '요약은 세 줄로')], 'recovered exactly')
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT state FROM memories WHERE id=?', (row['id'],)).fetchone()['state'], 'current')
        self.assertIsNone(self.notes.migrate(), 'an undone row is not moved again')
        self.assertEqual(self.current_memory(), [('profile.working.summary', '요약은 세 줄로')])

    def test_the_receipts_follow_the_seven_day_purge(self):
        row = self.save('profile.working.summary', '요약은 세 줄로')
        record = self.notes.migrate()
        self.clock.now += UNDO_SECONDS + 1
        self.assertEqual(self.forget.purge_due(), 1)
        with self.store.db() as db:
            self.assertIsNone(db.execute('SELECT 1 FROM memories WHERE id=?', (row['id'],)).fetchone())
        with self.assertRaises(ForgetError):
            self.forget.undo(MEMORY_OWNER, record['receipts'][0])
        self.assertEqual(self.notes.current()['points'], ['요약은 세 줄로'], 'the lesson itself lives on in the notes')

    def test_bounds_secrets_and_long_values_leave_rows_in_memory_untouched(self):
        self.store.secret('telegram_token', SECRET)
        self.save('profile.working.secret', f'use {SECRET}')
        self.save('profile.working.long', 'z' * (om.POINT_CHARS + 1))
        for index in range(om.NOTES_POINTS + 2):
            self.save(f'profile.working.p{index:02d}', f'point {index:02d}')
        record = self.notes.migrate()
        self.assertEqual((record['moved'], record['left']), (om.NOTES_POINTS, 4))
        self.assertEqual(self.notes.current()['points'], [f'point {index:02d}' for index in range(2, om.NOTES_POINTS + 2)],
                         'the newest lessons that fit, oldest first')
        left = {key for key, _content in self.current_memory()}
        self.assertEqual(left, {'profile.working.secret', 'profile.working.long', 'profile.working.p00', 'profile.working.p01'})
        self.assertNotIn(SECRET, self.notes.current()['text'])

    def test_nothing_to_move_records_the_run_and_makes_no_document(self):
        record = self.notes.migrate()
        self.assertEqual((record['moved'], record['version'], record['receipts']), (0, None, []))
        self.assertIsNone(self.notes.current())
        self.save('profile.working.later', 'saved by the owner after the move')
        self.assertIsNone(self.notes.migrate())
        self.assertEqual(self.current_memory(), [('profile.working.later', 'saved by the owner after the move')])

    def test_upkeep_proposes_no_fact_in_the_old_lesson_namespace(self):
        proposal = {'category': 'preference', 'kind': 'stated', 'memory_key': 'profile.working.style', 'content': 'x',
                    'evidence': 'y', 'supersedes_key': ''}
        kept, dropped = om.validate([proposal], set(), set())
        self.assertEqual((kept, [item['reason'] for item in dropped]), ([], ['key']))


class Told(TelegramHarness):
    """A revision is told once on Telegram, in the owner's language; the AI forgets it with the existing tool."""

    def setUp(self):
        super().setUp()
        self.answers = []

        def structured(context, question, schema):
            if context.purpose == om.REVIEW_PURPOSE and self.answers:
                return StructuredDecision(OUTCOME_DECIDED, self.answers.pop(0), fixture_confidence(0.9))
            return None
        self.service.use_decision_engine(FixtureDecisionEngine(structured=structured))
        self.service.owner_model_spawn = lambda target: target()

    def reviewed(self, answer, text='그거 찾아줘'):
        job = self.store.enqueue(text, f'wn-{time.time()}-{text}', channel=f'telegram:{GENERATION}', chat_id=CHAT)
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='failed',response='못 찾았어요',provider='subscription',delivery='sent' WHERE id=?",
                       (job,))
        self.assertTrue(om.enqueue_self_review(self.store, job, om.REVIEW_FAILED))
        self.answers = [answer]
        self.assertTrue(self.service.run_owner_model_upkeep())
        return job

    def notices(self, job):
        return [row['state'] for row in self.notification(job, WORKING_NOTES_KIND)]

    def test_one_notice_per_change_in_the_owners_language_and_none_for_no_change(self):
        change = '"그거"가 직전 메시지의 대상을 가리킨다고 적었어요'
        job = self.reviewed({'notes': '- "그거"는 직전 메시지에서 말한 것', 'change': change, 'blocker': ''})
        self.assertTrue(self.service.deliver_notification())
        sent = self.sends()[-1]
        self.assertEqual(sent['text'], msg('working_notes.saved', change=change))
        self.assertTrue(sent['text'].startswith('일하는 방식 메모를 고쳤어요: "그거"가'))
        self.assertIsNone(sent.get('reply_markup'), 'no buttons: the owner answers in words (#996)')
        self.assertEqual(self.notices(job), ['sent'])
        again = self.reviewed({'notes': '*  "그거"는  직전 메시지에서 말한 것', 'change': '같은 내용', 'blocker': ''})
        self.assertEqual(self.notices(again), [], 'a reworded existing lesson: no version, no notice')
        self.assertEqual(om.WorkingNotes(self.store).current()['version'], 1)
        self.assertEqual(self.store.memories(MEMORY_OWNER), [], 'no fact row either')

    def test_a_revision_forgotten_before_the_send_is_not_told_and_the_brief_shows_the_previous_notes(self):
        self.reviewed({'notes': '- 요약은 세 줄로', 'change': '요약 길이', 'blocker': ''})
        self.assertTrue(self.service.deliver_notification())
        second = self.reviewed({'notes': '- 요약은 세 줄로\n- 링크는 맨 끝에', 'change': '링크 위치', 'blocker': ''})
        current = om.WorkingNotes(self.store).current()
        self.assertIn('- 링크는 맨 끝에', self.service.owner_profile_snapshot())
        undo = self.store.enqueue('방금 그거 되돌려', 'wn-undo')
        caps = Capabilities(self.store, None, {}, '', undo, lambda *event: None)
        result = caps.execute('forget_record', {'action': 'forget', 'ref': current['ref']})
        self.assertEqual(result['kind'], 'procedure')
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='succeeded' WHERE id=?", (undo,))
        self.assertTrue(self.service.deliver_notification())
        self.assertEqual(self.notices(second), ['cancelled'], 'a revision no longer in use is not told')
        snapshot = self.service.owner_profile_snapshot()
        self.assertIn('working_notes v1', snapshot)
        self.assertNotIn('링크는 맨 끝에', snapshot)
        # The same revision is not saved again from a later review while its forget stands.
        third = self.reviewed({'notes': '- 요약은 세 줄로\n- 링크는 맨 끝에', 'change': '링크 위치', 'blocker': ''})
        with self.store.db() as db:
            detail = json.loads(db.execute("SELECT detail FROM tool_events WHERE job_id=? AND tool='self_review'",
                                           (third,)).fetchone()['detail'])
        self.assertEqual(detail['dropped'], [{'note': 'notes', 'reason': om.FORGOTTEN_VALUE}])
        self.assertEqual(om.WorkingNotes(self.store).current()['version'], 1)

    def test_a_notice_lost_in_sending_is_told_again_once(self):
        job = self.reviewed({'notes': '- 요약은 세 줄로', 'change': '요약 길이', 'blocker': ''})
        [row] = self.notification(job, WORKING_NOTES_KIND)
        # A crash while the notice was being sent leaves 'sending'; recover() marks it 'unknown' (#581).
        self.store.update_notification(row['id'], 'sending')
        self.store.recover()
        self.assertEqual(self.notices(job), ['unknown'])
        self.service._memory_saved_release_sweep = 0
        self.service.release_memory_saved_notices()
        self.assertTrue(self.service.deliver_notification())
        self.assertEqual(self.sends()[-1]['text'], msg('working_notes.saved', change='요약 길이'))
        [again] = self.notification(job, WORKING_NOTES_KIND)
        self.assertEqual((again['state'], json.loads(again['fingerprint'])['retells']), ('sent', 1))
        # Review P1: told again at most once, like a saved-memory notice.
        self.store.update_notification(again['id'], 'unknown')
        self.service._memory_saved_release_sweep = 0
        self.service.release_memory_saved_notices()
        self.assertEqual(self.notices(job), ['unknown'])

    def test_a_secret_never_enters_the_notes_or_the_notice(self):
        self.store.secret('telegram_token', SECRET)
        job = self.reviewed({'notes': f'- 토큰은 {SECRET}', 'change': 'c', 'blocker': ''})
        self.assertIsNone(om.WorkingNotes(self.store).current())
        self.assertEqual(self.notices(job), [])

    def test_moved_lessons_leave_the_profile_rows_and_join_the_brief_as_one_block(self):
        self.store.save_memory('profile.working.summary', '요약은 세 줄로', MEMORY_OWNER, work_id='w0')
        om.WorkingNotes(self.store).migrate()
        self.assertEqual(om.WorkingNotes(self.store).current()['points'], ['요약은 세 줄로'])
        self.assertIn('working_notes v1', self.service.owner_profile_snapshot())
        self.assertNotIn('profile.working.summary', self.service.owner_profile_snapshot())

    def test_the_information_use_audit_names_the_notes_sent_never_their_points(self):
        from personal_agent import information_use
        self.store.save_memory('profile.place.home', '분당', MEMORY_OWNER, work_id='w0')
        self.reviewed({'notes': '- 요약은: 세 줄로\n- 링크는 맨 끝에', 'change': '요약 길이', 'blocker': ''})
        ref = om.WorkingNotes(self.store).current()['ref']
        refs = self.service.owner_information_refs({'profile': self.service.owner_profile_snapshot()})
        self.assertEqual(refs['profile_keys'], ['profile.place.home', f'working_notes v1 ({ref})'],
                         'review P1: the notes are recorded as used, as one reference')
        self.assertNotIn('세 줄', json.dumps(refs, ensure_ascii=False))


if __name__ == '__main__':
    unittest.main()
