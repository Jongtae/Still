"""Legacy #19 migration and procedure receipts, plus working-lesson notice regressions.

These model-free tests use temporary stores, scripted judgments and an injected
Telegram transport.
"""
import json
import re
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
from test_self_review import add_lesson, review

SECRET = 'plain-stored-secret-019'


class Clock:
    def __init__(self, now=1_800_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


class Document(unittest.TestCase):
    """The reserved slot and legacy receipts remain usable."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = QuickStore(tmp.name)
        self.clock = Clock()
        self.notes = om.WorkingNotes(self.store, self.clock)
        self.forget = OwnerForget(self.store, clock=self.clock)

    def save(self, *points):
        row = self.notes.library.commit(NOTES_SLOT, om.notes_text(points), work_id='w')
        return {**row, 'ref': 'procedure:' + row['id']}

    def test_the_slot_is_reserved_and_no_secret_set_can_name_it(self):
        self.assertIsNone(NAME.match(NOTES_SLOT), 'set names never hold ":"')
        with self.assertRaises(ValueError):
            ProcedureLibrary(self.store).propose('w', NOTES_SLOT, 'def f():\n    pass\n', [])
        self.assertEqual(ProcedureLibrary(self.store).settle('w', True), [], 'the notes have no candidate stage')


    def test_cut_to_fit_removes_lessons_tail_before_profile_facts_without_changing_the_store(self):
        from personal_agent.agent_runtime import CUT_TO_FIT_MARKER, cut_to_fit
        from personal_agent.quickstart_service import AgentService
        service = AgentService(self.store)
        self.store.save_memory('profile.preference.format', 'Use numbered paragraphs', MEMORY_OWNER)
        lessons = om.WorkingLessons(self.store)
        saved = lessons.apply([add_lesson('Keep source links'),
                               add_lesson('Explain uncertainty and distinguish observations from assumptions')],
                              'Evidence style', None, origin=om.ORIGIN_SELF_REVIEW)
        profile = service.owner_profile_snapshot(recall=False)
        facts = service.owner_profile_snapshot(notes=False)
        block = lessons.block()
        self.assertEqual(profile, facts + '\n' + block)
        sections = {'profile': profile, 'request': 'Compare the alternatives', 'instructions': 'Fixed instructions',
                    'attachments': 'Attached source', 'surface': 'web'}
        # Cutting just one byte still removes a whole lesson line, including
        # enough extra bytes for the omission marker; facts stay at the head.
        out, cut = cut_to_fit(sections, 1)
        self.assertEqual(cut, ['profile'])
        self.assertEqual(out['profile'], profile.rsplit('\n', 1)[0] + '\n' + CUT_TO_FIT_MARKER)
        self.assertTrue(out['profile'].startswith(facts + '\n'))
        for name in sections.keys() - {'profile'}:
            self.assertEqual(out[name], sections[name])
        self.assertGreaterEqual(len(profile.encode()) - len(out['profile'].encode()), 1)
        self.assertEqual(sections['profile'], profile, 'the caller snapshot is not mutated')
        self.assertEqual([item['memory_id'] for item in lessons.current()],
                         [item['memory_id'] for item in saved['applied']])
        self.assertEqual(lessons.block(), block, 'prompt trimming leaves the durable lessons intact')


    def test_forgetting_the_version_restores_the_previous_document_and_keeps_the_record(self):
        first = self.save('a')
        second = self.save('a', 'b')
        receipt = self.forget.forget(MEMORY_OWNER, second['ref'])
        self.assertIsNone(receipt['undo_until'], '#367: no undo deadline')
        self.assertEqual(self.notes.current()['id'], first['id'], 'the previous document is back')
        self.assertTrue(self.notes.library.forgotten_digest(NOTES_SLOT, second['digest']))
        self.assertEqual(self.forget.undoable(MEMORY_OWNER, kind='procedure')[0]['item'], {'working_notes_version': 2})
        self.forget.undo(MEMORY_OWNER, receipt['receipt'])
        self.assertEqual(self.notes.current()['id'], second['id'], 'undo puts the forgotten version back, exactly')
        self.assertFalse(self.notes.library.forgotten_digest(NOTES_SLOT, second['digest']))
        again = self.forget.forget(MEMORY_OWNER, second['ref'])
        self.clock.now += UNDO_SECONDS + 1
        self.assertEqual(self.forget.purge_due(), 0, '#367: a forget is never purged')
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT state FROM procedure_versions WHERE id=?', (second['id'],)).fetchone()['state'],
                             'forgotten')
            tombstones = [dict(row) for row in db.execute('SELECT * FROM forgotten_values')]
        self.assertEqual(self.notes.current()['id'], first['id'])
        # The content-free tombstone keeps the forget authoritative: the host never saves the version again.
        self.assertIn(second['digest'], {row['digest'] for row in tombstones})
        self.assertNotIn('a\\n- b', json.dumps(tombstones))
        self.assertEqual({row['receipt_id'] for row in tombstones}, {again['receipt']})

    def test_the_notice_is_queued_with_the_operation_for_a_conversation_work_only(self):
        work = self.store.enqueue('그거 찾아줘', 'wn-tg', channel='telegram:g9', chat_id=4040)
        waiting = self.store.enqueue('저거 찾아줘', 'wn-tg-waiting', channel='telegram:g9', chat_id=4040)
        web = self.store.enqueue('그거 찾아줘', 'wn-web')
        with self.store.db() as db:
            db.execute("UPDATE jobs SET delivery='sent' WHERE id=?", (work,))
            db.execute("UPDATE jobs SET delivery='pending' WHERE id=?", (waiting,))
        lessons = om.WorkingLessons(self.store, self.clock)
        saved = lessons.apply([add_lesson('a')], 'a saved', work, origin=om.ORIGIN_SELF_REVIEW)
        second = lessons.apply([add_lesson('b')], 'b saved', waiting, origin=om.ORIGIN_SELF_REVIEW)
        lessons.apply([add_lesson('c')], 'c saved', web, origin=om.ORIGIN_SELF_REVIEW)
        self.assertEqual(lessons.apply([], '', work, origin=om.ORIGIN_SELF_REVIEW)['applied'], [])
        with self.store.db() as db:
            rows = [dict(row) for row in db.execute('SELECT job_id,chat_id,generation,kind,fingerprint,state '
                                                    'FROM telegram_notifications ORDER BY created,rowid')]
        self.assertEqual(rows, [{'job_id': work, 'chat_id': 4040, 'generation': 'g9', 'kind': om.NOTICE_KIND,
                                 'fingerprint': json.dumps({'change': 'a saved', 'items': saved['applied']}),
                                 'state': 'queued'},
                                {'job_id': waiting, 'chat_id': 4040, 'generation': 'g9', 'kind': om.NOTICE_KIND,
                                 'fingerprint': json.dumps({'change': 'b saved', 'items': second['applied']}),
                                 'state': self.store.NOTICE_HELD}])

    def test_a_later_revision_wins_over_an_undo(self):
        self.save('a')
        second = self.save('a', 'b')
        receipt = self.forget.forget(MEMORY_OWNER, second['ref'])
        self.save('a', 'c')
        with self.assertRaises(ForgetError) as refused:
            self.forget.undo(MEMORY_OWNER, receipt['receipt'])
        self.assertEqual(refused.exception.code, 'later_value')
        self.assertEqual(self.notes.current()['points'], ['a', 'c'])

    def test_points_are_one_per_line_and_the_same_point_is_kept_once(self):
        self.assertEqual(om.notes_points(' * 요약은  세 줄로\n\n- 요약은 세 줄로 \n• Links last\n· links LAST\n-5도면 알려줘'),
                         ['요약은 세 줄로', 'Links last', '-5도면 알려줘'])
        self.assertEqual(om.notes_text(['a', 'b']), '- a\n- b')


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

    def test_the_migrated_rows_are_kept_out_of_use_and_never_purged(self):
        """#367 (append + invalidate): the migrated row keeps its record past the old 7-day window."""
        row = self.save('profile.working.summary', '요약은 세 줄로')
        record = self.notes.migrate()
        self.clock.now += UNDO_SECONDS + 1
        self.assertEqual(self.forget.purge_due(), 0)
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT state FROM memories WHERE id=?', (row['id'],)).fetchone()['state'], 'forgotten')
        self.assertIn(record['receipts'][0], [entry['receipt'] for entry in self.forget.undoable(MEMORY_OWNER)])
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
        proposal = {'category': 'preference', 'kind': 'stated', 'memory_key': 'profile.working.style', 'content': 'x', 'terms': '',
                    'evidence': 'y', 'supersedes_key': ''}
        kept, dropped = om.validate([proposal], set(), set())
        self.assertEqual((kept, [item['reason'] for item in dropped]), ([], ['key']))


class Told(TelegramHarness):
    """Lesson changes are told in the owner's language and can be forgotten with the existing tool."""

    def setUp(self):
        super().setUp()
        self.answers = []

        def structured(context, question, schema):
            if context.purpose == om.REVIEW_PURPOSE and self.answers:
                return StructuredDecision(OUTCOME_DECIDED, self.answers.pop(0), fixture_confidence(0.9))
            return None
        self.service.use_decision_engine(FixtureDecisionEngine(structured=structured))
        self.service.owner_model_spawn = lambda target: target()

    def reviewed(self, answer, text='그거 찾아줘', delivery='sent'):
        job = self.store.enqueue(text, f'wn-{time.time()}-{text}', channel=f'telegram:{GENERATION}', chat_id=CHAT)
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='failed',response='못 찾았어요',provider='subscription',delivery=? WHERE id=?",
                       (delivery, job))
        self.assertTrue(om.enqueue_self_review(self.store, job, om.REVIEW_FAILED))
        self.answers = [answer]
        self.assertTrue(self.service.run_owner_model_upkeep())
        return job

    def notices(self, job):
        return [row['state'] for row in self.notification(job, WORKING_NOTES_KIND)]

    def test_one_notice_per_change_in_the_owners_language_and_none_for_no_change(self):
        change = '"그거"가 직전 메시지의 대상을 가리킨다고 적었어요'
        job = self.reviewed(review([add_lesson('"그거"는 직전 메시지에서 말한 것')], change))
        self.assertTrue(self.service.deliver_notification())
        sent = self.sends()[-1]
        self.assertEqual(sent['text'], msg('working_notes.saved', change=change))
        self.assertTrue(sent['text'].startswith('일하는 방식 메모를 고쳤어요: "그거"가'))
        self.assertIsNone(sent.get('reply_markup'), 'no buttons: the owner answers in words (#996)')
        self.assertEqual(self.notices(job), ['sent'])
        again = self.reviewed(review([add_lesson('"그거"는  직전 메시지에서 말한 것')], '같은 내용'))
        self.assertEqual(self.notices(again), [], 'a duplicate lesson queues no notice')
        self.assertEqual(len(om.WorkingLessons(self.store).current()), 1)
        self.assertEqual(self.store.memories(MEMORY_OWNER, key_prefix='profile.'), [], 'no profile fact row either')

    def test_lesson_operations_with_a_blocker_keep_current_rows_and_queue_no_notice(self):
        self.reviewed(review([add_lesson('Keep summaries short')], 'Summary length'))
        lessons = om.WorkingLessons(self.store)
        before = lessons.current()
        # Even a blocker too long to retain must veto otherwise valid operations.
        for blocker in ('The tool result was lost', 'x' * (om.MAX_TEXT_CHARS + 1)):
            with self.subTest(blocker_chars=len(blocker)):
                job = self.reviewed(review([add_lesson('Put links last')], 'Link placement', blocker))
                self.assertEqual(lessons.current(), before)
                self.assertEqual(self.notices(job), [])
                with self.store.db() as db:
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM memories WHERE memory_key LIKE ?',
                                                (om.LESSONS_PREFIX + '%',)).fetchone()[0], 1)
        # A subsequent unblocked answer can still add the lesson.
        job = self.reviewed(review([add_lesson('Put links last')], 'Link placement'))
        self.assertEqual(lessons.current()[:1], before)
        self.assertEqual([item['text'] for item in lessons.current()], ['Keep summaries short', 'Put links last'])
        self.assertEqual(self.notices(job), ['queued'])

    def test_an_english_owner_is_told_in_english(self):
        # #235: the two-line notice was sent in Korean to every en/ja/zh owner.
        self.store.put('telegram', {**self.store.config('telegram', {}), 'owner_language': 'en'})
        change = 'I now keep summaries to three lines'
        self.reviewed(review([add_lesson('Summaries stay at three lines')], change))
        self.assertTrue(self.service.deliver_notification())
        sent = self.sends()[-1]['text']
        self.assertEqual(sent, msg('working_notes.saved', 'en', change=change))
        self.assertIsNone(re.search('[가-힣]', sent), sent)

    def test_a_lesson_forgotten_before_the_send_is_not_told_and_other_lessons_stay(self):
        self.reviewed(review([add_lesson('요약은 세 줄로')], '요약 길이'))
        self.assertTrue(self.service.deliver_notification())
        second = self.reviewed(review([add_lesson('링크는 맨 끝에')], '링크 위치'))
        current = om.WorkingLessons(self.store).current()[-1]
        self.assertIn('- 링크는 맨 끝에', self.service.owner_profile_snapshot())
        undo = self.store.enqueue('방금 그거 되돌려', 'wn-undo')
        caps = Capabilities(self.store, None, {}, '', undo, lambda *event: None)
        result = caps.execute('forget_record', {'action': 'forget', 'ref': current['ref']})
        self.assertEqual(result['kind'], 'memory')
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='succeeded' WHERE id=?", (undo,))
        self.assertTrue(self.service.deliver_notification())
        self.assertEqual(self.notices(second), ['cancelled'], 'a lesson no longer in use is not told')
        snapshot = self.service.owner_profile_snapshot()
        self.assertIn('요약은 세 줄로', snapshot)
        self.assertNotIn('링크는 맨 끝에', snapshot)
        # The same lesson is not saved again from a later review while its forget stands.
        third = self.reviewed(review([add_lesson('링크는 맨 끝에')], '링크 위치'))
        with self.store.db() as db:
            detail = json.loads(db.execute("SELECT detail FROM tool_events WHERE job_id=? AND tool='self_review'",
                                           (third,)).fetchone()['detail'])
        self.assertEqual(detail['dropped'], [{'note': 'lesson', 'op': 'add', 'reason': om.FORGOTTEN_VALUE}])
        self.assertEqual(len(om.WorkingLessons(self.store).current()), 1)

    def test_a_notice_lost_in_sending_is_told_again_once(self):
        job = self.reviewed(review([add_lesson('요약은 세 줄로')], '요약 길이'))
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

    def test_a_notice_saved_before_the_reply_was_sent_follows_the_reply(self):
        """#233 (Codex review): "I updated my notes" never overtakes the answer it is about."""
        from personal_agent.conversation_handoff import TelegramNotSent
        change = '요약 길이를 적었어요'
        job = self.reviewed(review([add_lesson('요약은 세 줄로')], change), delivery='pending')
        self.assertEqual(self.notices(job), [self.store.NOTICE_HELD], 'held while the reply is awaited')
        outage, original = [True], self.service.telegram_transport

        def unreachable(url, body=None, headers=None, timeout=60):
            if url.endswith('/sendMessage') and outage[0]:
                self.calls.append(('sendMessage-not-sent', body))
                raise TelegramNotSent('connect failed after the client retries')
            return original(url, body, headers, timeout=timeout)
        self.service.telegram_transport = unreachable
        self.service.deliver_one()
        self.assertEqual(self.store.job(job)['delivery'], 'pending', 'the reply waits out its backoff')
        self.assertFalse(self.service.deliver_notification(), 'the work loop sends no notice ahead of it')
        self.service._memory_saved_release_sweep = 0
        self.service.release_memory_saved_notices()
        self.assertEqual(self.notices(job), [self.store.NOTICE_HELD], 'the sweep keeps it held too')
        self.assertEqual(self.sends(), [])
        outage[0] = False
        self.service._unsent_retry_at = 0
        self.service.deliver_one()
        self.assertEqual(self.store.job(job)['delivery'], 'sent')
        self.assertEqual(self.notices(job), ['queued'], 'released by the delivered reply')
        self.assertTrue(self.service.deliver_notification())
        reply, notice = self.sends()
        self.assertEqual(notice['text'], msg('working_notes.saved', change=change))
        self.assertNotEqual(reply['text'], notice['text'], 'the answer first, then the notice')

    def test_a_held_notice_is_released_by_the_sweep_once_the_reply_settled(self):
        """A crash between the reply's delivery and the release, or a cancelled reply, leaves it to the sweep."""
        job = self.reviewed(review([add_lesson('요약은 세 줄로')], '요약 길이'), delivery='pending')
        self.service._memory_saved_release_sweep = 0
        self.service.release_memory_saved_notices()
        self.assertEqual(self.notices(job), [self.store.NOTICE_HELD])
        with self.store.db() as db:
            db.execute("UPDATE jobs SET delivery='cancelled' WHERE id=?", (job,))
        self.service._memory_saved_release_sweep = 0
        self.service.release_memory_saved_notices()
        self.assertEqual(self.notices(job), ['queued'])

    def test_a_secret_never_enters_the_lessons_or_the_notice(self):
        self.store.secret('telegram_token', SECRET)
        job = self.reviewed(review([add_lesson(f'토큰은 {SECRET}')], 'c'))
        self.assertEqual(om.WorkingLessons(self.store).current(), [])
        self.assertEqual(self.notices(job), [])

    def test_moved_lessons_leave_the_profile_rows_and_join_the_brief_as_one_block(self):
        self.store.save_memory('profile.working.summary', '요약은 세 줄로', MEMORY_OWNER, work_id='w0')
        self.assertTrue(om.WorkingLessons(self.store).migrated())
        self.assertEqual([item['text'] for item in om.WorkingLessons(self.store).current()], ['요약은 세 줄로'])
        self.assertIn('working_lessons (', self.service.owner_profile_snapshot())
        self.assertNotIn('profile.working.summary', self.service.owner_profile_snapshot())

    def test_the_information_use_audit_names_the_lessons_sent_never_their_texts(self):
        self.store.save_memory('profile.place.home', '분당', MEMORY_OWNER, work_id='w0')
        self.reviewed(review([add_lesson('요약은: 세 줄로'), add_lesson('링크는 맨 끝에')], '요약 길이'))
        keys = {item['key'] for item in om.WorkingLessons(self.store).current()}
        refs = self.service.owner_information_refs({'profile': self.service.owner_profile_snapshot()})
        self.assertEqual(set(refs['profile_keys']), {'profile.place.home', *keys},
                         'each shown lesson has its own reference in the audit')
        self.assertNotIn('세 줄', json.dumps(refs, ensure_ascii=False))


if __name__ == '__main__':
    unittest.main()
