"""Accepted preparation windows keep their report and recurrence promises."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from personal_agent import preparations as prep
from personal_agent.quickstart_store import QuickStore
from test_preparations import _WatchCase, call, watch_engine


class KeptWindowTests(_WatchCase):
    def setUp(self):
        super().setUp()
        self.judge = watch_engine([False] * 12)
        self.service.use_decision_engine(self.judge)

    def schedule(self, due, until, *, recurrence=None, report_every_minutes=None):
        args = dict(kind='prepare', goal='새 결과 확인', due=due.isoformat(), timezone=due.tzinfo.key,
                    until=until.isoformat(), every_minutes='10', delivery='when_needed')
        if recurrence:
            args['recurrence'] = recurrence
        if report_every_minutes:
            args['report_every_minutes'] = report_every_minutes
        self.script = [{'content': None, 'tool_calls': [call('watch', 'schedule_preparation', **args)]},
                       {'content': '정한 시각에 확인할게요.'}]
        self.receive('정한 시간대에 새 결과가 있는지 계속 살펴봐 줘.')
        [result] = [json.loads(m['content']) for m in self.model_calls[-1]['messages'] if m['role'] == 'tool']
        assert result['scheduled'], result
        self.watched = self.service.preparations.get(result['preparation_id'])
        return result

    def notifications(self):
        return [text for text in self.texts() if '\n\n지켜보던 일: ' in text]

    def test_due_report_and_closing_are_sent_even_with_an_unchanged_result(self):
        zone = ZoneInfo('Asia/Seoul')
        due = datetime.fromtimestamp(self.now + 60, zone)
        until = datetime.fromtimestamp(due.timestamp() + 30 * 60, zone)
        self.schedule(due, until, report_every_minutes='20')
        outcomes = [self.run_slot('변화가 없어요.')['last_decision_reason'] for _ in range(4)]
        self.assertEqual(outcomes, [prep.REASON_JUDGED_NOT_NEEDED, prep.REASON_JUDGED_NOT_NEEDED,
                                    prep.REASON_PROMISED_REPORT, prep.REASON_WINDOW_CLOSED])
        self.assertEqual(len(self.notifications()), 2)
        self.assertEqual(len(self.watch_asks()), 2, 'a promised result never asks the quiet judgment')

    def run_late_slot(self, minutes):
        row = self.service.preparations.get(self.watched['id'])
        self.now = row['due_at'] + 1
        self.assertTrue(self.tick())
        self.script = [{'content': '변화가 없어요.'}] * 2
        self.assertTrue(self.service.run_one())
        self.service.deliver_one()
        self.now += minutes * 60
        self.assertTrue(self.tick())
        self.service.deliver_notification()
        return self.service.preparations.get(row['id'])

    def test_promised_report_survives_a_skipped_boundary_slot(self):
        zone = ZoneInfo('Asia/Seoul')
        due = datetime.fromtimestamp(self.now + 60, zone)
        until = datetime.fromtimestamp(due.timestamp() + 60 * 60, zone)
        self.schedule(due, until, report_every_minutes='20')
        self.assertIsNone(self.watched['last_report_at'])
        self.run_slot('변화가 없어요.')  # +0
        row = self.run_late_slot(11)  # +10 settles after +20
        self.assertEqual(row['last_decision_reason'], prep.REASON_PROMISED_REPORT)
        self.assertEqual(row['last_report_at'], self.now)
        self.assertEqual(row['due_at'], due.timestamp() + 30 * 60)
        self.assertEqual(len(self.notifications()), 1)
        outcomes = [self.run_slot('변화가 없어요.')['last_decision_reason'] for _ in range(4)]
        self.assertEqual(outcomes, [prep.REASON_UNCHANGED, prep.REASON_PROMISED_REPORT,
                                    prep.REASON_UNCHANGED, prep.REASON_WINDOW_CLOSED])
        self.assertEqual(len(self.notifications()), 3)

    def test_multiple_missed_intervals_produce_one_report_then_resume(self):
        zone = ZoneInfo('Asia/Seoul')
        due = datetime.fromtimestamp(self.now + 60, zone)
        until = datetime.fromtimestamp(due.timestamp() + 90 * 60, zone)
        self.schedule(due, until, report_every_minutes='20')
        self.run_slot('변화가 없어요.')
        row = self.run_late_slot(41)  # +10 settles after both +20 and +40
        self.assertEqual(row['last_decision_reason'], prep.REASON_PROMISED_REPORT)
        self.assertEqual(row['last_report_at'], self.now)
        self.assertEqual(row['due_at'], due.timestamp() + 60 * 60)
        self.assertEqual(len(self.notifications()), 1)
        row = self.run_slot('변화가 없어요.')  # +60 is a fresh interval
        self.assertEqual(row['last_decision_reason'], prep.REASON_PROMISED_REPORT)
        self.assertEqual(len(self.notifications()), 2)

    def test_boundary_report_is_decided_once_before_delivery(self):
        zone = ZoneInfo('Asia/Seoul')
        due = datetime.fromtimestamp(self.now + 60, zone)
        until = datetime.fromtimestamp(due.timestamp() + 60 * 60, zone)
        self.schedule(due, until, report_every_minutes='20')
        self.run_slot('변화가 없어요.')
        self.run_slot('변화가 없어요.')
        self.now = due.timestamp() + 20 * 60
        self.assertTrue(self.tick())
        self.script = [{'content': '변화가 없어요.'}] * 2
        self.assertTrue(self.service.run_one())
        self.service.deliver_one()
        running = self.service.preparations.get(self.watched['id'])
        row = self.service.preparations.settle(running, self.now, notify_to=(77, 'gen-1'))
        self.assertEqual(row['last_decision_reason'], prep.REASON_PROMISED_REPORT)
        self.assertEqual(row['last_report_at'], self.now)
        self.assertIsNone(row['last_notified_at'], 'the marker records a decision, not delivery')
        self.assertEqual(prep.digest([row]), prep.digest([{**row, 'last_report_at': None}]))
        self.assertIsNone(self.service.preparations.settle(running, self.now, notify_to=(77, 'gen-1')))
        self.assertFalse(self.tick())
        self.assertTrue(self.service.deliver_notification())
        self.assertFalse(self.service.deliver_notification())
        self.assertEqual(len(self.notifications()), 1)
        row = self.run_slot('변화가 없어요.')  # +30 stays in the same interval
        self.assertEqual(row['last_decision_reason'], prep.REASON_UNCHANGED)
        self.assertEqual(row['last_report_at'], due.timestamp() + 20 * 60)
        self.assertEqual(len(self.notifications()), 1)

    def test_overdue_closing_report_takes_precedence(self):
        zone = ZoneInfo('Asia/Seoul')
        due = datetime.fromtimestamp(self.now + 60, zone)
        until = datetime.fromtimestamp(due.timestamp() + 60 * 60, zone)
        self.schedule(due, until, report_every_minutes='20')
        self.run_slot('변화가 없어요.')
        row = self.run_late_slot(51)  # +10 settles after the last slot, also owing a report
        self.assertEqual(row['last_decision_reason'], prep.REASON_WINDOW_CLOSED)
        self.assertIsNone(row['last_report_at'])
        self.assertEqual(len(self.notifications()), 1)
        self.assertFalse(self.tick())

    def test_stopped_overdue_run_leaves_the_report_owed(self):
        zone = ZoneInfo('Asia/Seoul')
        due = datetime.fromtimestamp(self.now + 60, zone)
        until = datetime.fromtimestamp(due.timestamp() + 60 * 60, zone)
        self.schedule(due, until, report_every_minutes='20')
        self.run_slot('변화가 없어요.')
        self.now = due.timestamp() + 10 * 60 + 1
        self.assertTrue(self.tick())
        [_, run] = self.runs(self.watched['id'])
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='cancelled',response=NULL,delivery='none' WHERE id=?", (run['id'],))
        self.now += 11 * 60
        self.assertTrue(self.tick())
        self.service.deliver_notification()
        row = self.service.preparations.get(self.watched['id'])
        self.assertEqual(row['last_decision_reason'], prep.REASON_RUN_STOPPED)
        self.assertIsNone(row['last_report_at'])
        self.assertEqual(len(self.notifications()), 0)
        row = self.run_slot('변화가 없어요.')  # +30 carries the still-owed +20 report
        self.assertEqual(row['last_decision_reason'], prep.REASON_PROMISED_REPORT)
        self.assertEqual(len(self.notifications()), 1)

    def test_daily_report_marker_does_not_suppress_the_next_days_report(self):
        zone = ZoneInfo('America/New_York')
        due = datetime(2026, 10, 31, 9, tzinfo=zone)
        self.now = due.timestamp() - 60
        self.schedule(due, due.replace(hour=10), recurrence='daily', report_every_minutes='20')
        for _ in range(3):
            row = self.run_slot('변화가 없어요.')
        self.assertEqual(row['last_decision_reason'], prep.REASON_PROMISED_REPORT)
        marker = row['last_report_at']
        self.now = due.replace(hour=10).timestamp()
        row = self.run_slot('변화가 없어요.')  # closing, then the next day (across DST)
        self.assertEqual(row['last_decision_reason'], prep.REASON_WINDOW_CLOSED)
        self.assertEqual(row['last_report_at'], marker)
        self.assertEqual(row['due_at'], due.replace(month=11, day=1).timestamp())
        row = self.run_slot('변화가 없어요.')
        self.assertEqual(row['last_decision_reason'], prep.REASON_UNCHANGED)
        self.run_slot('변화가 없어요.')
        row = self.run_slot('변화가 없어요.')
        self.assertEqual(row['last_decision_reason'], prep.REASON_PROMISED_REPORT)
        self.assertEqual(row['last_report_at'], self.now)
        self.assertEqual(len(self.notifications()), 3)

    def test_restart_inside_window_reports_at_first_overdue_settlement(self):
        zone = ZoneInfo('Asia/Seoul')
        due = datetime.fromtimestamp(self.now + 60, zone)
        until = datetime.fromtimestamp(due.timestamp() + 60 * 60, zone)
        self.schedule(due, until, report_every_minutes='20')
        self.run_slot('변화가 없어요.')
        self.now = due.timestamp() + 10 * 60 + 1
        self.assertTrue(self.tick())
        self.now = due.timestamp() + 31 * 60
        self.store = QuickStore(self.root)
        self.service = self.make_service()
        self.script = [{'content': '변화가 없어요.'}] * 2
        self.assertTrue(self.service.run_one())
        self.service.deliver_one()
        self.assertTrue(self.tick())
        self.service.deliver_notification()
        row = self.service.preparations.get(self.watched['id'])
        self.assertEqual(row['last_decision_reason'], prep.REASON_PROMISED_REPORT)
        self.assertEqual(row['due_at'], due.timestamp() + 40 * 60)
        self.assertEqual(len(self.notifications()), 1)
        # A second restart keeps the marker durable and the next interval reportable.
        self.store = QuickStore(self.root)
        self.service = self.make_service()
        self.assertEqual(self.service.preparations.get(row['id'])['last_report_at'], self.now)
        row = self.run_slot('변화가 없어요.')
        self.assertEqual(row['last_decision_reason'], prep.REASON_PROMISED_REPORT)
        self.assertEqual(len(self.notifications()), 2)

    def test_window_edit_resets_report_marker_only_when_reanchored(self):
        zone = ZoneInfo('Asia/Seoul')
        due = datetime.fromtimestamp(self.now + 60, zone)
        until = datetime.fromtimestamp(due.timestamp() + 60 * 60, zone)
        self.schedule(due, until, report_every_minutes='20')
        for _ in range(3):
            row = self.run_slot('변화가 없어요.')
        marker = row['last_report_at']
        self.assertIsNotNone(marker)
        row = self.service.preparations.edit(row['id'], max_runs=7)
        self.assertEqual(row['last_report_at'], marker)
        for changes in ({'due': self.due_iso(60)}, {'timezone': 'Asia/Tokyo'}):
            with self.subTest(changes=changes):
                self.assertIsNone(prep.Preparations.edit_values(row, changes, self.now)['last_report_at'])
        row = self.service.preparations.edit(row['id'], due=self.due_iso(60))
        self.assertIsNone(row['last_report_at'])
        self.assertEqual(row['rule_start'], row['due_at'])

    def test_report_marker_migration_preserves_an_existing_window(self):
        zone = ZoneInfo('Asia/Seoul')
        due = datetime.fromtimestamp(self.now + 60, zone)
        until = datetime.fromtimestamp(due.timestamp() + 60 * 60, zone)
        self.schedule(due, until, report_every_minutes='20')
        with self.store.db() as db:
            db.execute('ALTER TABLE preparations DROP COLUMN last_report_at')
            prep.migrate(db)
            prep.migrate(db)
        self.assertEqual(self.service.preparations.get(self.watched['id']), self.watched)

    def test_closing_queues_one_notification_when_the_quiet_judge_raises(self):
        self.watched = self.watch(runs=1)
        self.now = self.watched['due_at'] + 1
        self.tick()
        self.script = [{'content': '변화가 없어요.'}] * 2
        self.service.run_one()
        running = self.service.preparations.get(self.watched['id'])

        def unavailable(*_args):
            raise RuntimeError('judgment unavailable')

        first = self.service.preparations.settle(running, self.now, decide=unavailable, notify_to=(77, 'gen-1'))
        self.assertEqual(first['last_decision_reason'], prep.REASON_WINDOW_CLOSED)
        self.assertIsNone(self.service.preparations.settle(running, self.now, decide=unavailable, notify_to=(77, 'gen-1')))
        self.service.deliver_notification()
        self.assertEqual(len(self.notifications()), 1)

    def test_daily_window_repeats_through_restart_and_skips_a_missed_day(self):
        ny = ZoneInfo('America/New_York')
        due = datetime(2026, 10, 31, 9, tzinfo=ny)
        until = due.replace(minute=10)
        self.now = due.timestamp() - 60
        result = self.schedule(due, until, recurrence='daily')
        self.assertEqual(result['window_recurrence'], 'daily')
        auth = self.watched['accepted_by']
        self.run_slot('변화가 없어요.')
        row = self.run_slot('변화가 없어요.')
        self.assertEqual(row['due_at'], due.replace(month=11, day=1).timestamp())
        self.assertEqual((row['run_count'], row['accepted_by']), (0, auth))
        self.service = self.make_service()
        self.service.use_decision_engine(self.judge)
        self.service.context_observations.set_controls({'timezone': 'Asia/Tokyo'})
        self.run_slot('변화가 없어요.')
        row = self.run_slot('변화가 없어요.')
        self.assertEqual(row['due_at'], due.replace(month=11, day=2).timestamp())
        self.now = due.replace(month=11, day=2, hour=16).timestamp()
        self.tick()
        row = self.service.preparations.get(row['id'])
        self.assertEqual((row['due_at'], row['run_count']), (due.replace(month=11, day=3).timestamp(), 0))
        self.assertEqual(len(self.runs(row['id'])), 4, 'no expired slot was replayed')
        self.service.preparations.cancel(row['id'])
        self.now = row['due_at'] + 1
        self.assertFalse(self.tick())

    def test_confirmation_binding_changes_with_promised_interval(self):
        ny = ZoneInfo('America/New_York')
        due = datetime(2026, 10, 31, 9, tzinfo=ny)
        self.now = due.timestamp() - 60
        self.schedule(due, due.replace(minute=30), recurrence='daily', report_every_minutes='20')
        changed = dict(self.watched)
        terms = prep.stored_window_terms(changed)
        terms['report_seconds'] = 600
        changed['window_terms'] = json.dumps(terms)
        self.assertNotEqual(prep.digest([changed]), prep.digest([self.watched]))

    def test_late_restart_skips_to_next_slot_of_the_open_day(self):
        tz = ZoneInfo('Asia/Seoul')
        due = datetime(2026, 10, 31, 9, tzinfo=tz)
        self.now = due.timestamp() - 60
        self.schedule(due, due.replace(minute=30), recurrence='daily')
        self.now = due.replace(month=11, day=1, minute=14).timestamp()
        self.tick()
        row = self.service.preparations.get(self.watched['id'])
        self.assertEqual(row['due_at'], due.replace(month=11, day=1, minute=20).timestamp())
        self.assertEqual(self.runs(row['id']), [])

    def test_expired_queued_run_preserves_future_windows(self):
        tz = ZoneInfo('Asia/Seoul')
        due = datetime(2026, 10, 31, 9, tzinfo=tz)
        self.now = due.timestamp() - 60
        self.schedule(due, due.replace(minute=10), recurrence='daily')
        self.now = due.timestamp() + 1
        self.tick()
        [run] = self.runs(self.watched['id'])
        self.now = self.watched['window_end'] + 1
        self.tick()
        row = self.service.preparations.get(self.watched['id'])
        self.assertEqual(row['state'], prep.STATE_SCHEDULED)
        self.assertEqual(row['due_at'], due.replace(month=11, day=1).timestamp())
        self.assertEqual(self.store.job(run['id'])['status'], 'failed')

    def test_skipping_and_editing_do_not_shrink_future_days_run_cap(self):
        tz = ZoneInfo('Asia/Seoul')
        due = datetime(2026, 10, 31, 9, tzinfo=tz)
        self.now = due.timestamp() - 60
        self.schedule(due, due.replace(minute=30), recurrence='daily')
        self.now = due.timestamp() + 1
        self.tick()
        self.script = [{'content': '변화가 없어요.'}] * 2
        self.service.run_one()
        self.now = due.replace(minute=24).timestamp()
        self.tick()
        row = self.service.preparations.edit(self.watched['id'], max_runs=4)
        self.assertEqual(row['max_runs'], 4)
        row = self.run_slot('변화가 없어요.')
        self.assertEqual((row['max_runs'], row['run_count']), (4, 0))

    def test_dst_fold_preserves_wall_clock_slots(self):
        ny = ZoneInfo('America/New_York')
        due = datetime(2026, 10, 31, 1, 45, tzinfo=ny)
        self.now = due.timestamp() - 60
        self.schedule(due, due.replace(hour=2, minute=15), recurrence='daily')
        self.service.preparations.edit(self.watched['id'], every_minutes=30)
        self.run_slot('변화가 없어요.')
        self.run_slot('변화가 없어요.')
        row = self.run_slot('변화가 없어요.')
        self.assertEqual(row['due_at'], due.replace(month=11, day=1, hour=2, minute=15).timestamp())

    def test_nonexistent_opening_is_not_replayed_outside_window(self):
        ny = ZoneInfo('America/New_York')
        due = datetime(2026, 3, 7, 2, 10, tzinfo=ny)
        self.now = due.timestamp() - 60
        self.schedule(due, due.replace(hour=3, minute=0), recurrence='daily')
        self.now = due.replace(hour=3, minute=0).timestamp()
        row = self.run_slot('변화가 없어요.')
        self.assertEqual(row['due_at'], due.replace(day=9).timestamp())

    def test_fractional_opening_advances_without_losing_precision(self):
        tz = ZoneInfo('Asia/Seoul')
        due = datetime(2026, 10, 31, 9, microsecond=500000, tzinfo=tz)
        self.now = due.timestamp() - 60
        self.schedule(due, due.replace(minute=10), recurrence='daily')
        self.run_slot('변화가 없어요.')
        row = self.run_slot('변화가 없어요.')
        self.assertEqual(row['due_at'], due.replace(month=11, day=1).timestamp())

    def test_expired_immediate_run_does_not_unpause_the_next_day(self):
        tz = ZoneInfo('Asia/Seoul')
        due = datetime(2026, 10, 31, 9, tzinfo=tz)
        self.now = due.timestamp() - 60
        self.schedule(due, due.replace(minute=10), recurrence='daily')
        self.service.preparations.pause(self.watched['id'])
        self.service.preparations.run_now(self.watched['id'], channel='web', chat_id=None)
        self.now = self.watched['window_end'] + 1
        self.tick()
        row = self.service.preparations.get(self.watched['id'])
        self.assertEqual((row['state'], row['run_return_state']), (prep.STATE_PAUSED, None))
        self.now = row['due_at'] + 1
        self.assertFalse(self.tick())

    def test_midnight_restart_does_not_start_a_missed_yesterday_run(self):
        tz = ZoneInfo('Asia/Seoul')
        due = datetime(2026, 10, 31, 23, 49, tzinfo=tz)
        self.now = due.timestamp() - 60
        self.schedule(due, due.replace(minute=59), recurrence='daily')
        self.now = due.replace(month=11, day=1, hour=0, minute=1).timestamp()
        self.assertLess(self.now, self.watched['window_end'])
        self.tick()
        row = self.service.preparations.get(self.watched['id'])
        self.assertEqual(row['due_at'], due.replace(month=11, day=1).timestamp())
        self.assertEqual(self.runs(row['id']), [])
