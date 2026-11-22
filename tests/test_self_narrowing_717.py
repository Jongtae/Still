"""#717: model-free current-slot narrowing, atomic receipts, owner undo and restart replay."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest import mock
from concurrent.futures import ThreadPoolExecutor

from personal_agent import preparations as prep
from personal_agent.conversation_surface import notice_binding, notice_text
from personal_agent.quickstart_store import QuickStore
from personal_agent.quickstart_service import AgentService
from personal_agent.providers import ModelAdapter
from test_owner_settings_conversation import _Case


class SelfNarrowing(_Case):
    def setUp(self):
        super().setUp()
        self.clock[0] = 1800000000.0
        self.service.preparations.clock = lambda: self.clock[0]
        self.preps = self.service.preparations
        self.serial = 0

    def running(self, *, channel='web', maximum=8, recurrence=None):
        self.serial += 1
        row = self.preps.create(kind='prepare', goal=f'Check material {self.serial}', due_at=self.clock[0], timezone='UTC',
                                recurrence=recurrence, channel=channel, created_from=None,
                                state=prep.STATE_SCHEDULED, accepted_by=prep.ACCEPTED_OWNER_SETTINGS,
                                window=(600, self.clock[0] + 7200, maximum), delivery_mode='when_needed')
        work = self.preps.start(row, channel='web', chat_id=None, now=self.clock[0])
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (work,))
        return self.preps.get(row['id']), self.store.job(work)

    def change(self, row, run, operation='edit', changes=None, settings=None):
        args = dict(category='preparations', setting=operation,
                    value=json.dumps(dict(id=row['id'], **(changes or {}))) if operation == 'edit' else row['id'],
                    reason='The source is no longer useful.')
        handler = ((settings or self.settings).work_tools('owner', 'web', run['id'], unattended=True,
                                                         own_preparation=prep.preparation_of(run['request_key'])))
        return handler('settings_change', args)

    def notices(self):
        with self.store.db() as db:
            return [dict(r) for r in db.execute('SELECT * FROM telegram_notifications WHERE kind LIKE ?',
                                                (prep.SELF_CHANGE_NOTICE_PREFIX + '%',))]

    def audits(self):
        return [e for e in self.store.config('settings_audit', []) if e['terminal'] == 'own-run']

    def settle(self, row, run):
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='succeeded',delivery='none' WHERE id=?", (run['id'],))
        self.preps.settle(row, self.clock[0])
        return self.preps.get(row['id'])

    def assert_refused(self, row, run, operation='edit', changes=None):
        before, audit, notices = self.preps.get(row['id']), self.audits(), self.notices()
        drafts = self.settings._drafts()
        result = self.change(row, run, operation, changes)
        self.assertEqual((result['state'], result['applied']), ('not_proposed', False), result)
        self.assertEqual(self.preps.get(row['id']), before)
        self.assertEqual(self.audits(), audit)
        self.assertEqual(self.notices(), notices)
        self.assertEqual(self.settings._drafts(), drafts)

    def test_narrow_bounds_and_settle(self):
        for changes in ({'until': prep.iso(self.clock[0] + 1200)}, {'max_runs': 2}, {'every_minutes': 20}):
            with self.subTest(changes=changes):
                row, run = self.running()
                result = self.change(row, run, changes=changes)
                self.assertTrue(result['applied'], result)
                narrowed = self.preps.get(row['id'])
                self.assertEqual(narrowed['run_count'], row['run_count'])
                before_slots, after_slots = [], []
                for schedule, slots in ((row, before_slots), (narrowed, after_slots)):
                    schedule = dict(schedule)
                    while (slot := prep.next_window_slot(schedule, schedule['due_at'])) is not None:
                        slots.append(slot)
                        schedule.update(due_at=slot, run_count=schedule['run_count'] + 1)
                self.assertTrue(set(after_slots) <= set(before_slots), (before_slots, after_slots))
                # Exercise real settle/start until no more slots, not just the pure helper.
                current = self.settle(row, run)
                while current['state'] == prep.STATE_SCHEDULED:
                    self.assertLessEqual(current['due_at'], narrowed['until_at'])
                    self.assertLess(current['run_count'], narrowed['max_runs'])
                    self.clock[0] = current['due_at']
                    work = self.preps.start(current, channel='web', chat_id=None, now=self.clock[0])
                    current = self.settle(self.preps.get(row['id']), self.store.job(work))
                self.assertLessEqual(current['run_count'], narrowed['max_runs'])

    def test_refused_fields_do_not_change_anything(self):
        row, run = self.running()
        invalid = {'until': prep.iso(row['until_at'] + 600), 'max_runs': 9, 'every_minutes': 5,
                   'due': prep.iso(row['due_at'] + 600), 'timezone': 'Asia/Seoul', 'recurrence': 'daily',
                   'rrule': 'FREQ=DAILY', 'delivery_mode': 'always', 'delivery': 'send', 'channel': 'telegram',
                   'destination': 'elsewhere', 'goal': 'Different work', 'goal_text': 'Different work',
                   'kind': 'reminder', 'run_count': 0, 'rule_start': row['due_at'],
                   'report_every_minutes': 20, 'payment_autonomy': True, 'authority': 'all'}
        for field, value in invalid.items():
            with self.subTest(field=field):
                self.assert_refused(row, run, changes={field: value})
        for value in ('', None, 0, True):
            with self.subTest(max_runs=value):
                self.assert_refused(row, run, changes={'max_runs': value})
        # Larger but not aligned to the old cadence adds new slots.
        self.assert_refused(row, run, changes={'every_minutes': 15})

    def test_nonrunning_other_and_stale_slots(self):
        row, run = self.running()
        other, _ = self.running()
        for operation in ('cancel', 'pause', 'edit'):
            self.assert_refused(other, run, operation, {'max_runs': 2})
        current = self.settle(row, run)
        for operation in ('cancel', 'pause', 'edit'):
            self.assert_refused(row, run, operation, {'max_runs': 2})
        self.clock[0] = current['due_at']
        self.preps.start(current, channel='web', chat_id=None, now=self.clock[0])
        for operation in ('cancel', 'pause', 'edit'):
            self.assert_refused(row, run, operation, {'max_runs': 2})

    def test_owner_restore_invalidates_original_work_even_before_settle(self):
        row, run = self.running()
        self.preps.cancel(row['id'])
        self.preps.restore(row['id'])
        self.assertEqual(self.preps.get(row['id'])['state'], prep.STATE_RUNNING)
        for operation in ('cancel', 'pause', 'edit'):
            self.assert_refused(row, run, operation, {'max_runs': 2})

    def test_owner_edit_invalidates_old_slot(self):
        row, run = self.running()
        self.settle(row, run)
        self.preps.edit(row['id'], every_minutes=20)
        for operation in ('cancel', 'pause', 'edit'):
            self.assert_refused(row, run, operation, {'max_runs': 2})

    def test_once_notice_and_audit_replay_and_owner_undo(self):
        for operation in ('cancel', 'pause', 'edit'):
            with self.subTest(operation=operation):
                row, run = self.running()
                audits, notices = len(self.audits()), len(self.notices())
                result = self.change(row, run, operation, {'max_runs': 2})
                self.assertTrue(result['applied'], result)
                self.assertEqual(len(self.audits()), audits + 1)
                self.assertEqual(len(self.notices()), notices + 1)
                event = self.audits()[-1]
                self.assertEqual(event['reason'], 'The source is no longer useful.')
                self.assertTrue(event['before'])
                self.assertNotEqual(event['before'], event['after'])
                restarted_service = AgentService(QuickStore(self.store.root), ModelAdapter(self._model), self._telegram)
                restarted_service.preparations.clock = lambda: self.clock[0]
                self.change(row, run, operation, {'max_runs': 2}, settings=restarted_service.settings_orchestrator)
                self.assertEqual(len(self.audits()), audits + 1)
                self.assertEqual(len(self.notices()), notices + 1)
                notice = self.notices()[-1]
                bound = notice_binding(self.service, notice)
                text = notice_text(self.service, notice, bound)
                self.assertIn(event['reason'], text)
                # #1097: named by its title (its goal, with none), never by its id.
                self.assertIn(prep.owner_title(row), text)
                self.assertNotIn(row['id'], text)
                # Owner settings confirm is the existing command path, including while Work runs.
                undo = 'resume' if operation == 'pause' else 'restore'
                draft = self.settings.propose('owner', 'http', 'preparations', undo, row['id'])
                applied = self.settings.confirm('owner', 'http', draft['draft_id'], draft['digest'])
                self.assertEqual(applied['state'], 'applied', applied)
                restored = self.preps.get(row['id'])
                self.assertEqual(restored['state'], prep.STATE_RUNNING)
                self.assertEqual(restored['max_runs'], row['max_runs'])
                self.assertEqual(restored['run_count'], row['run_count'])
                self.assertIsNone(restored['run_return_state'])
                for future in ('cancel', 'pause', 'edit'):
                    self.assert_refused(row, run, future, {'max_runs': 2})
                current = self.settle(row, run)
                self.clock[0] = current['due_at']
                new_work = self.preps.start(current, channel='web', chat_id=None, now=self.clock[0])
                self.assert_refused(row, self.store.job(new_work), changes={'max_runs': 3})

    def test_notice_is_delivered_for_keep_and_when_needed(self):
        for channel in ('web', 'telegram'):
            with self.subTest(channel=channel):
                row, run = self.running(channel=channel)
                self.assertTrue(self.change(row, run, 'cancel')['applied'])
                self.assertTrue(self.service.deliver_notification())
                sent = [body for method, body in self.telegram if method == 'sendMessage']
                self.assertIn(prep.owner_title(row), sent[-1]['text'])
                self.assertNotIn(row['id'], sent[-1]['text'])
                self.assertIn('The source is no longer useful.', sent[-1]['text'])
                count = len(sent)
                self.change(row, run, 'cancel')
                self.service.deliver_notification()
                self.assertEqual(len([1 for method, _ in self.telegram if method == 'sendMessage']), count)

    def test_outbox_failure_rolls_back_row_and_audit(self):
        row, run = self.running()
        before = self.preps.get(row['id'])
        with mock.patch.object(self.store.telegram, 'queue_notification', side_effect=RuntimeError('disk full')):
            with self.assertRaises(RuntimeError):
                self.change(row, run, changes={'max_runs': 2})
        self.assertEqual(self.preps.get(row['id']), before)
        self.assertEqual(self.audits(), [])
        self.assertEqual(self.notices(), [])

    def test_run_fact_includes_its_id_and_unattended_never_adds_authority(self):
        row, run = self.running()
        self.assertIn(row['id'], self.service.preparation_run_fact(run))
        handler = self.service.settings_tools(run)
        for category, setting, value in (('payment_autonomy', 'enabled', 'on'), ('api_connections', 'connect', '{}'),
                                         ('preparations', 'resume', row['id']), ('preparations', 'accept', row['id']),
                                         ('preparations', 'run_now', row['id'])):
            with self.subTest(category=category, setting=setting):
                result = handler('settings_change', dict(category=category, setting=setting, value=value))
                self.assertEqual(result['state'], 'not_proposed')
        self.assertEqual(self.settings._drafts(), {})
        self.assertEqual(self.store.config('settings_audit', []), [])

    def test_undo_after_settle_and_owner_edit_prevent_later_self_change(self):
        for undo in ('restore', 'edit'):
            with self.subTest(undo=undo):
                row, run = self.running()
                self.assertTrue(self.change(row, run, changes={'max_runs': 1})['applied'])
                settled = self.settle(row, run)
                self.assertNotEqual(settled['state'], prep.STATE_SCHEDULED)
                draft = self.settings.propose('owner', 'http', 'preparations', 'restore', row['id'])
                result = self.settings.confirm('owner', 'http', draft['draft_id'], draft['digest'])
                self.assertEqual(result['state'], 'applied', result)
                restored = self.preps.get(row['id'])
                self.assertEqual(restored['max_runs'], row['max_runs'])
                self.assertEqual(restored['run_count'], 1)
                if undo == 'edit':
                    self.preps.edit(row['id'], max_runs=6)
                restored = self.preps.get(row['id'])
                self.clock[0] = restored['due_at']
                work = self.preps.start(restored, channel='web', chat_id=None, now=self.clock[0])
                self.assert_refused(row, self.store.job(work), changes={'max_runs': 3})

    def test_distinct_revisions_have_distinct_notices_and_old_replay_is_inert(self):
        row, run = self.running()
        self.assertTrue(self.change(row, run, changes={'max_runs': 6})['applied'])
        self.assertTrue(self.change(row, run, changes={'max_runs': 4})['applied'])
        self.assertNotEqual(self.audits()[0]['revision'], self.audits()[1]['revision'])
        self.assertEqual(len(self.notices()), 2)
        self.assert_refused(row, run, changes={'max_runs': 6})
        self.assert_refused(row, run, changes={'max_runs': 4})

    def test_daily_window_cadence_is_a_subset_on_later_days_too(self):
        row, run = self.running(recurrence='daily')
        self.assertTrue(self.change(row, run, changes={'every_minutes': 20})['applied'])
        narrowed = self.preps.get(row['id'])
        original_next = {**row, **prep.next_day_window(row, row['until_at'] + 1)}
        narrowed_next = {**narrowed, **prep.next_day_window(narrowed, row['until_at'] + 1)}
        slots = []
        for schedule in (original_next, narrowed_next):
            future = []
            while schedule['run_count'] < schedule['max_runs']:
                future.append(schedule['due_at'])
                schedule['run_count'] += 1
                next_slot = prep.next_window_slot(schedule, schedule['due_at'])
                if next_slot is None:
                    break
                schedule['due_at'] = next_slot
            slots.append(set(future))
        self.assertTrue(slots[1] <= slots[0], slots)

    def test_concurrent_duplicate_calls_commit_only_one_receipt(self):
        row, run = self.running()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.change(row, run, changes={'max_runs': 2}), range(2)))
        self.assertEqual(sorted(result['state'] for result in results), ['applied', 'not_proposed'])
        self.assertEqual(len(self.audits()), 1)
        self.assertEqual(len(self.notices()), 1)

    def test_owner_and_run_audits_are_not_lost_to_concurrent_writers(self):
        row, run = self.running()
        def owner_audits():
            for index in range(10):
                self.settings._audit({'id': f'owner-{index}', 'target': 'preparations.resume'}, 'applied')
        with ThreadPoolExecutor(max_workers=2) as pool:
            owner = pool.submit(owner_audits)
            worker = pool.submit(self.change, row, run, 'edit', {'max_runs': 2})
            owner.result()
            self.assertTrue(worker.result()['applied'])
        self.assertEqual(len(self.store.config('settings_audit', [])), 11)
        self.assertEqual(len(self.audits()), 1)

    def test_owner_reapplies_before_values_with_edit_and_later_run_cannot_repeat(self):
        row, run = self.running()
        self.assertTrue(self.change(row, run, changes={'max_runs': 3})['applied'])
        self.settle(row, run)
        self.preps.edit(row['id'], max_runs=row['max_runs'])
        current = self.preps.get(row['id'])
        self.clock[0] = current['due_at']
        work = self.preps.start(current, channel='web', chat_id=None, now=self.clock[0])
        self.assert_refused(row, self.store.job(work), changes={'max_runs': 3})

    def test_continuation_cannot_revive_an_owner_invalidated_revision_at_same_clock(self):
        row, run = self.running()
        self.preps.cancel(row['id'])
        self.preps.restore(row['id'])
        continued = self.store.enqueue('Continue', run['request_key'] + '/loc:answer')
        with self.store.db() as db:
            self.assertTrue(self.preps.continue_run(db, run['id'], continued, self.clock[0]))
        for operation in ('cancel', 'pause', 'edit'):
            self.assert_refused(row, self.store.job(continued), operation, {'max_runs': 2})

    def test_interval_does_not_add_late_slots_across_a_half_hour_dst_gap(self):
        tz = ZoneInfo('Australia/Lord_Howe')
        self.clock[0] = datetime(2026, 10, 3, 1, tzinfo=tz).timestamp()
        row = self.preps.create(kind='prepare', goal='DST material', due_at=self.clock[0],
                                timezone='Australia/Lord_Howe', recurrence='daily', channel='web', created_from=None,
                                state=prep.STATE_SCHEDULED, accepted_by=prep.ACCEPTED_OWNER_SETTINGS,
                                window=(600, self.clock[0] + 3 * 3600, 7))
        work = self.preps.start(row, channel='web', chat_id=None, now=self.clock[0])
        row = self.preps.get(row['id'])
        result = self.change(row, self.store.job(work), changes={'every_minutes': 60})
        self.assertTrue(result['applied'], result)
        slots = []
        for schedule in (row, self.preps.get(row['id'])):
            future = set()
            for _ in range(4):
                while schedule['run_count'] < schedule['max_runs']:
                    slot = prep.next_window_slot(schedule, schedule['due_at'])
                    if slot is None:
                        break
                    future.add(slot)
                    schedule.update(due_at=slot, run_count=schedule['run_count'] + 1)
                advanced = prep.next_day_window(schedule, schedule['until_at'] + 1)
                schedule.update(advanced)
                future.add(schedule['due_at'])
                schedule['run_count'] = 1
            # Compare a common horizon even when an invalid closing time skips a whole day.
            slots.append({slot for slot in future if slot < datetime(2026, 10, 6, tzinfo=tz).timestamp()})
        self.assertTrue(slots[1] <= slots[0], slots)

    def rolled_self_narrowed_window(self, timezone_name, day):
        tz = ZoneInfo(timezone_name)
        self.clock[0] = datetime(*day, 9, tzinfo=tz).timestamp()
        self.serial += 1
        original = self.preps.create(
            kind='prepare', goal=f'Rollover material {self.serial}', due_at=self.clock[0],
            timezone=timezone_name, recurrence='daily', channel='web', created_from=None,
            state=prep.STATE_SCHEDULED, accepted_by=prep.ACCEPTED_OWNER_SETTINGS,
            window=(600, self.clock[0] + 7200, 8), delivery_mode='when_needed')
        work = self.preps.start(original, channel='web', chat_id=None, now=self.clock[0])
        row, run = self.preps.get(original['id']), self.store.job(work)
        self.assertTrue(self.change(row, run, changes={'until': prep.iso(self.clock[0] + 1200)})['applied'])
        narrowed = self.preps.get(row['id'])
        self.clock[0] = narrowed['until_at'] + 1
        rolled = self.settle(narrowed, run)
        self.assertEqual(rolled['state'], prep.STATE_SCHEDULED)
        self.assertNotEqual(rolled['until_at'], narrowed['until_at'])
        return original, rolled

    def test_self_narrow_undo_survives_daily_rollover_and_rebases_local_bounds(self):
        for timezone_name, day in (('Asia/Seoul', (2026, 11, 5)),
                                   ('America/New_York', (2026, 3, 7)),
                                   ('America/New_York', (2026, 10, 31))):
            with self.subTest(timezone=timezone_name, day=day):
                original, rolled = self.rolled_self_narrowed_window(timezone_name, day)
                self.assertTrue(self.preps.self_undoable(rolled, 'edit'))
                # Exercise the Settings option and its bound owner confirmation, not just the helper.
                draft = self.settings.propose('owner', 'http', 'preparations', 'restore', rolled['id'])
                result = self.settings.confirm('owner', 'http', draft['draft_id'], draft['digest'])
                self.assertEqual(result['state'], 'applied', result)
                restored = self.preps.get(rolled['id'])
                tz = ZoneInfo(timezone_name)
                current_day = datetime.fromtimestamp(rolled['due_at'], tz).date()
                expected_end = datetime.combine(current_day, datetime.fromtimestamp(original['until_at'], tz).time(),
                                                tzinfo=tz).timestamp()
                self.assertEqual(restored['until_at'], expected_end)
                self.assertEqual(restored['window_end'], expected_end + original['window_end'] - original['until_at'])
                self.assertEqual(prep.stored_window_terms(restored), prep.stored_window_terms(original))
                for key in ('state', 'due_at', 'rule_start', 'run_count'):
                    self.assertEqual(restored[key], rolled[key], key)
                self.assertFalse(self.preps.self_undoable(restored, 'edit'))
                # Restored per-day terms must also govern the following window.
                following = prep.next_day_window(restored, restored['until_at'] + 1)
                self.assertEqual(datetime.fromtimestamp(following['until_at'], tz).hour, 11)

    def test_owner_edit_after_daily_rollover_still_voids_self_undo(self):
        _original, rolled = self.rolled_self_narrowed_window('Asia/Seoul', (2026, 11, 5))
        self.assertTrue(self.preps.self_undoable(rolled, 'edit'))
        # This changes no field in the self-narrow's after-values: the owner veto must still apply.
        edited = self.preps.edit(rolled['id'], max_runs=7)
        self.assertEqual(edited['until_at'], rolled['until_at'])
        self.assertFalse(self.preps.self_undoable(edited, 'edit'))
        with self.assertRaises(prep.PreparationRefusal):
            self.preps.restore(rolled['id'])
        self.assertEqual(self.preps.get(rolled['id']), edited)
