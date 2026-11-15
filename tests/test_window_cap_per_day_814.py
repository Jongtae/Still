"""#814: local-date budgets and honest stored/confirmed window ends."""
import json
from html import unescape
import unittest
import tempfile
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from personal_agent import preparations as prep
from personal_agent.messages import msg
from personal_agent.quickstart_store import QuickStore
from personal_agent.settings_orchestrator import SettingsOrchestrator
from test_preparations import _WatchCase, call, watch_engine


SEOUL = ZoneInfo('Asia/Seoul')
DUE = datetime(2026, 11, 16, 9, tzinfo=SEOUL)


class DailyCapTests(unittest.TestCase):
    def normalize(self, due=DUE, until=None, runs=None, recurrence=None, minutes=5):
        until = until or due.replace(hour=20)
        return prep.normalize_window(minutes, until.isoformat(), runs, due.timestamp(),
                                     due.tzinfo.key, due.timestamp() - 60, recurrence)

    def test_weekday_repro_and_multi_day_windows(self):
        self.assertEqual(prep.MAX_DAILY_WINDOW_RUNS, 24 * 60 // prep.MIN_EVERY_MINUTES)
        self.assertEqual(self.normalize(recurrence='weekdays'), (300, DUE.replace(hour=20).timestamp(), 133))
        self.assertEqual(self.normalize(until=DUE + timedelta(days=1))[2], 288)
        self.assertEqual(self.normalize(until=DUE + timedelta(days=31))[2], 31 * 288)

    def test_daily_cap_wording_in_all_locales(self):
        for lang, daily in (('ko', '일일'), ('en', 'daily'), ('ja', '1日'), ('zh', '每日')):
            with self.subTest(lang=lang):
                fact = msg('preparations.run_daily_cap_end', lang, end='09:05', requested='10:00')
                self.assertIn(daily, fact)
                self.assertIn('09:05', fact)
                self.assertIn('10:00', fact)
        self.assertIn('每日上限', msg('preparations.field_max_runs', 'zh'))

    def test_partial_days_and_inclusive_endpoints(self):
        due = DUE.replace(hour=23, minute=50)
        with patch.object(prep, 'MAX_DAILY_WINDOW_RUNS', 3):
            # Crossing midnight does not grant a second whole day's allowance.
            end = due + timedelta(minutes=20)
            self.assertEqual(self.normalize(due, end), (300, due.timestamp() + 600, 3))
            self.assertEqual(self.normalize(due, end, runs=3)[2], 3)
            with self.assertRaises(prep.PreparationRefusal) as caught:
                self.normalize(due, end, runs=4)
            self.assertEqual(caught.exception.code, 'invalid_max_runs')
        every, end, count = self.normalize(until=DUE + timedelta(days=1))
        self.assertEqual((count, end), (288, DUE.timestamp() + 23 * 3600 + 55 * 60))
        self.assertEqual(end, DUE.timestamp() + every * (count - 1))

    def test_explicit_bound_and_one_check_recurring_window(self):
        self.assertEqual(self.normalize(runs=288)[2], 133)
        self.assertEqual(self.normalize(until=DUE + timedelta(days=31), runs=31 * 288)[2], 31 * 288)
        with self.assertRaises(prep.PreparationRefusal) as multi_day:
            self.normalize(until=DUE + timedelta(days=31), runs=31 * 288 + 1)
        self.assertEqual(multi_day.exception.code, 'invalid_max_runs')
        with self.assertRaises(prep.PreparationRefusal) as caught:
            self.normalize(runs=289)
        self.assertEqual(caught.exception.code, 'invalid_max_runs')
        with patch.object(prep, 'MAX_DAILY_WINDOW_RUNS', 1):
            window = self.normalize(recurrence='weekdays')
            self.assertEqual(window, (300, DUE.timestamp(), 1))
            terms = prep.window_terms(window, 'weekdays', DUE.timestamp(), 'Asia/Seoul', None, None,
                                      DUE.replace(hour=20).timestamp())
            self.assertEqual(json.loads(terms)['end_time'], '09:00:00')

    def test_a_dst_long_date_still_has_a_daily_bound(self):
        ny = ZoneInfo('America/New_York')
        due = datetime(2026, 11, 1, tzinfo=ny)
        until = due.replace(hour=23, minute=55)
        every, end, runs = self.normalize(due, until)
        self.assertEqual(runs, 288)
        self.assertEqual(end, due.timestamp() + every * 287)

    def test_explicit_total_below_daily_prefix_keeps_the_owners_end(self):
        requested = DUE.replace(hour=20).timestamp()
        for rhythm in (None, 'weekdays'):
            with self.subTest(rhythm=rhythm), patch.object(prep, 'MAX_DAILY_WINDOW_RUNS', 5):
                window = self.normalize(runs=2, recurrence=rhythm)
                self.assertEqual(window, (300, requested, 2))
                terms = prep.window_terms(window, rhythm, DUE.timestamp(), 'Asia/Seoul', None, None, requested)
                self.assertNotIn('requested_until', json.loads(terms or '{}'))
                self.assertNotIn('covering_every_minutes', json.loads(terms or '{}'))

    def test_cadence_edit_counts_completed_checks_on_each_date(self):
        row = dict(due_at=DUE.replace(minute=10).timestamp(), timezone='Asia/Seoul', recurrence=prep.RECURRENCE_WINDOW,
                   window_terms=None, rule_start=DUE.timestamp(), run_count=2, every_seconds=300,
                   until_at=DUE.replace(hour=20).timestamp(), max_runs=3, delivery_mode=prep.DELIVERY_WHEN_NEEDED)
        with patch.object(prep, 'MAX_DAILY_WINDOW_RUNS', 3):
            updated = prep.Preparations.edit_values(row, {'every_minutes': 10}, DUE.timestamp() - 60)
        self.assertEqual(updated['max_runs'], 3)
        self.assertEqual(updated['until_at'], row['due_at'])
        self.assertEqual(prep.stored_window_terms(updated)['requested_until'], row['until_at'])

    def test_cadence_edit_does_not_charge_previous_dates_to_the_pending_date(self):
        pending = DUE.replace(hour=0, minute=10)
        prior = [pending - timedelta(minutes=20), pending - timedelta(minutes=5)]
        row = dict(due_at=pending.timestamp(), timezone='Asia/Seoul', recurrence=prep.RECURRENCE_WINDOW,
                   window_terms=None, rule_start=prior[0].timestamp(), run_count=2, every_seconds=300,
                   until_at=(pending + timedelta(days=1)).timestamp(), max_runs=6, delivery_mode=prep.DELIVERY_WHEN_NEEDED)
        with patch.object(prep, 'MAX_DAILY_WINDOW_RUNS', 3):
            updated = prep.Preparations.edit_values(row, {'every_minutes': 10}, prior[0].timestamp() - 60,
                                                   completed_slots=[slot.timestamp() for slot in prior])
        self.assertEqual(updated['max_runs'], 4, 'one check on this date leaves two checks, despite a changed cadence')
        self.assertEqual(updated['until_at'], pending.timestamp() + 600)

    def test_lengthening_a_one_off_edit_uses_the_same_daily_bound(self):
        row = dict(due_at=DUE.timestamp(), timezone='Asia/Seoul', recurrence=prep.RECURRENCE_WINDOW,
                   window_terms=None, rule_start=None, run_count=0, every_seconds=300,
                   until_at=DUE.replace(minute=5).timestamp(), max_runs=2, delivery_mode=prep.DELIVERY_WHEN_NEEDED)
        requested = DUE.replace(hour=20)
        with patch.object(prep, 'MAX_DAILY_WINDOW_RUNS', 3):
            edited = {**row, **prep.Preparations.edit_values(row, {'until': requested.isoformat()}, DUE.timestamp() - 60)}
        self.assertEqual((edited['max_runs'], edited['until_at']), (3, DUE.replace(minute=10).timestamp()))
        self.assertEqual(prep.stored_window_terms(edited)['requested_until'], requested.timestamp())
        running = {**edited, 'due_at': DUE.replace(minute=5).timestamp(), 'run_count': 1}
        with patch.object(prep, 'MAX_DAILY_WINDOW_RUNS', 3):
            kept = prep.Preparations.edit_values(running, {'until': requested.isoformat()}, DUE.timestamp() - 60)
            self.assertEqual((kept['max_runs'], kept['until_at']), (3, DUE.replace(minute=10).timestamp()),
                             'an edit must not grant a fresh daily allowance after a run')
            self.assertEqual(prep.last_slot({**running, **kept, 'state': prep.STATE_SCHEDULED}), kept['until_at'])
            with self.assertRaises(prep.PreparationRefusal) as caught:
                prep.Preparations.edit_values(running, {'max_runs': 4}, DUE.timestamp() - 60)
            self.assertEqual(caught.exception.code, 'invalid_max_runs')
        extended = prep.Preparations.edit_values(edited, {'until': requested.isoformat()}, DUE.timestamp() - 60)
        self.assertEqual((extended['max_runs'], extended['until_at']), (133, requested.timestamp()))
        explicit = {**row, 'max_runs': 1}
        self.assertEqual(prep.Preparations.edit_values(explicit, {'until': requested.isoformat()}, DUE.timestamp() - 60)['max_runs'], 133,
                         'an extension derives its count just like creation unless max_runs is supplied in the edit')

    def test_last_check_accounts_for_pending_and_running_slots(self):
        end = DUE.replace(minute=10).timestamp()
        for rhythm in (None, 'weekdays'):
            terms = prep.window_terms((300, end, 3), rhythm, DUE.timestamp(), 'Asia/Seoul', None, None)
            row = dict(due_at=DUE.replace(minute=5).timestamp(), timezone='Asia/Seoul', every_seconds=300,
                       until_at=end, max_runs=3, rule_start=DUE.timestamp(), window_terms=terms)
            for state, count in ((prep.STATE_SCHEDULED, 1), (prep.STATE_RUNNING, 2)):
                with self.subTest(rhythm=rhythm, state=state):
                    self.assertEqual(prep.last_slot({**row, 'state': state, 'run_count': count}), end)


class WindowCapFlowTests(_WatchCase):
    def schedule(self, *, recurrence=None, until=None, max_runs=None):
        self.now = DUE.timestamp() - 60
        self.judge = watch_engine([False] * 6, preparation=True)
        self.service.use_decision_engine(self.judge)
        args = dict(kind='prepare', goal='새 결과 확인', due=DUE.isoformat(), timezone='Asia/Seoul',
                    until=(until or DUE.replace(hour=20)).isoformat(), every_minutes='5', delivery='when_needed')
        if recurrence:
            args['recurrence'] = recurrence
        if max_runs is not None:
            args['max_runs'] = str(max_runs)
        self.script = [{'content': None, 'tool_calls': [call('watch', 'schedule_preparation', **args)]},
                       {'content': '정한 시각에 확인할게요.'}]
        self.receive('정한 시간대에 새 결과가 있는지 계속 살펴봐 줘.')
        [result] = [json.loads(m['content']) for m in self.model_calls[-1]['messages'] if m['role'] == 'tool']
        self.assertTrue(result['scheduled'], result)
        self.watched = self.service.preparations.get(result['preparation_id'])
        return result

    def assert_honest(self, row, end):
        self.assertEqual(row['until_at'], end.timestamp())
        self.assertEqual(prep.last_slot(row), end.timestamp())
        self.assertIn(end.strftime('%H:%M') + '까지', prep.schedule_text(row, 'Asia/Seoul'))
        self.assertIn(end.strftime('%H:%M') + '까지', prep.proposal_summary(row, 'Asia/Seoul'))
        self.assertIsNone(prep.next_window_slot({**row, 'due_at': end.timestamp(), 'run_count': row['max_runs']}, end.timestamp()))

    def test_weekday_schedule_result_and_second_day_cover_2000(self):
        result = self.schedule(recurrence='weekdays')
        end = DUE.replace(hour=20)
        self.assertEqual(result['last_check'], prep.iso(end.timestamp(), 'Asia/Seoul'))
        self.assertEqual(result['max_runs'], 133)
        self.assertNotIn('requested_until', result)
        self.assert_honest(self.watched, end)
        self.assertIn('최대 133번', prep.schedule_text(self.watched, 'Asia/Seoul'))
        tomorrow = {**self.watched, **prep.next_day_window(self.watched, end.timestamp())}
        self.assert_honest(tomorrow, end + timedelta(days=1))

    def test_capped_one_off_result_confirmation_and_closing_report(self):
        with patch.object(prep, 'MAX_DAILY_WINDOW_RUNS', 2):
            result = self.schedule(until=DUE.replace(hour=10))
        end = DUE.replace(minute=5)
        self.assert_honest(self.watched, end)
        self.assertEqual(result['requested_until'], prep.iso(DUE.replace(hour=10).timestamp(), 'Asia/Seoul'))
        self.assertEqual(result['last_check'], prep.iso(end.timestamp(), 'Asia/Seoul'))
        self.assertEqual(result['until'], result['last_check'])
        self.assertEqual(result['covering_every_minutes'], 31)
        # The durable shortfall survives reopening the store and is bound in confirmation.
        self.service = self.make_service()
        row = self.service.preparations.get(self.watched['id'])
        self.assertEqual(prep.stored_window_terms(row)['requested_until'], DUE.replace(hour=10).timestamp())
        changed = {**row, 'window_terms': None}
        self.assertNotEqual(prep.digest([row]), prep.digest([changed]))
        first = self.run_slot('변화가 없어요.')
        self.assertNotEqual(first['last_decision_reason'], prep.REASON_WINDOW_CLOSED)
        self.now = end.timestamp() + 1
        self.assertTrue(self.tick())
        running = self.service.preparations.get(row['id'])
        work = self.store.job(running['last_run_job_id'])
        fact = self.service.preparation_run_fact(work)
        self.assertIn(msg('preparations.run_last'), fact)
        self.assertIn(msg('preparations.run_end', end=prep.local_text(end.timestamp(), 'Asia/Seoul')), fact)
        shortfall = msg('preparations.run_daily_cap_end', end=prep.local_text(end.timestamp(), 'Asia/Seoul'),
                        requested=prep.local_text(DUE.replace(hour=10).timestamp(), 'Asia/Seoul'))
        self.assertIn(shortfall, fact)
        self.assertIn('10:00', fact)
        self.script = [{'content': '09:05에 마지막 확인을 마쳤어요.'}] * 2
        self.assertTrue(self.service.run_one())
        self.service.deliver_one()
        self.assertTrue(self.tick())
        self.service.deliver_notification()
        final = self.service.preparations.get(row['id'])
        self.assertIn('09:05에 마지막 확인', '\n'.join(self.texts()))
        self.assertEqual(final['state'], prep.STATE_DELIVERED)
        self.assertEqual(final['last_decision_reason'], prep.REASON_WINDOW_CLOSED)
        self.assertEqual(prep.stored_window_terms(final)['requested_until'], DUE.replace(hour=10).timestamp())
        self.assertEqual(len(self.runs(final['id'])), 2)

    def test_uncapped_last_run_fact_has_no_daily_cap_shortfall(self):
        self.schedule(until=DUE.replace(minute=5))
        self.run_slot('변화가 없어요.')
        self.now = DUE.replace(minute=5).timestamp() + 1
        self.assertTrue(self.tick())
        row = self.service.preparations.get(self.watched['id'])
        fact = self.service.preparation_run_fact(self.store.job(row['last_run_job_id']))
        self.assertIn(msg('preparations.run_last'), fact)
        self.assertNotIn(msg('preparations.run_daily_cap_end',
                             end=prep.local_text(row['until_at'], 'Asia/Seoul'),
                             requested=prep.local_text(row['until_at'], 'Asia/Seoul')), fact)
        self.assertNotIn('일일 실행 한도', fact)

    def test_explicit_total_is_not_reported_as_a_daily_cap_shortfall(self):
        with patch.object(prep, 'MAX_DAILY_WINDOW_RUNS', 5):
            result = self.schedule(max_runs=2)
        self.assertEqual(result['until'], prep.iso(DUE.replace(hour=20).timestamp(), 'Asia/Seoul'))
        self.assertNotIn('requested_until', result)
        self.assertNotIn('covering_every_minutes', result)

    def test_edit_uses_recorded_checks_after_multiple_cadence_changes(self):
        self.schedule()
        self.run_slot('변화가 없어요.')
        self.run_slot('변화가 없어요.')
        with patch.object(prep, 'MAX_DAILY_WINDOW_RUNS', 3):
            # A second cadence edit must still count the checks that really ran at 09:00 and 09:05.
            self.service.preparations.edit(self.watched['id'], every_minutes=10)
            edited = self.service.preparations.edit(self.watched['id'], every_minutes=5)
        self.assertEqual((edited['run_count'], edited['max_runs']), (2, 3))
        self.assert_honest(edited, DUE.replace(minute=10))

    def test_web_edit_recomputes_shortfall_even_with_all_unchanged_fields(self):
        with patch.object(prep, 'MAX_DAILY_WINDOW_RUNS', 3):
            self.schedule(until=DUE.replace(hour=10))
            # app.js sends all five fields, including the displayed (capped) end.
            changes = dict(due='2026-11-16T09:00', timezone='Asia/Seoul', every_minutes=5,
                           until='2026-11-16T09:10', max_runs=3)
            edited = self.service.preparations.edit(self.watched['id'], **changes)
            self.assert_honest(edited, DUE.replace(minute=10))
            self.assertEqual(prep.stored_window_terms(edited)['requested_until'], DUE.replace(hour=10).timestamp())
            self.assertEqual(prep.stored_window_terms(edited)['covering_every_minutes'], 21)
            changes['max_runs'] = 2
            edited = self.service.preparations.edit(edited['id'], **changes)
            self.assertEqual(edited['until_at'], DUE.replace(hour=10).timestamp())
            self.assertNotIn('requested_until', prep.stored_window_terms(edited))
            self.assertNotIn('covering_every_minutes', prep.stored_window_terms(edited))

    def test_capped_rhythm_keeps_the_real_end_on_later_days(self):
        with patch.object(prep, 'MAX_DAILY_WINDOW_RUNS', 2):
            result = self.schedule(recurrence='weekdays', until=DUE.replace(hour=10))
        self.assertEqual(result['covering_every_minutes'], 31)
        self.assertEqual(result['requested_until'], prep.iso(DUE.replace(hour=10).timestamp(), 'Asia/Seoul'))
        self.assertEqual(result['last_check'], prep.iso(DUE.replace(minute=5).timestamp(), 'Asia/Seoul'))
        self.assertEqual(prep.stored_window_terms(self.watched)['end_time'], '09:05:00')
        for day in (0, 1, 2):
            row = self.watched if not day else {**self.watched, **prep.next_day_window(self.watched, (DUE + timedelta(days=day - 1, hours=1)).timestamp())}
            self.assert_honest(row, DUE.replace(minute=5) + timedelta(days=day))
            self.assertEqual(row['max_runs'], 2)
        self.run_slot('변화가 없어요.')
        final = self.run_slot('변화가 없어요.')
        self.assertEqual(final['last_decision_reason'], prep.REASON_WINDOW_CLOSED)
        self.assertEqual(final['due_at'], (DUE + timedelta(days=1)).timestamp())
        self.assertEqual(final['until_at'], (DUE.replace(minute=5) + timedelta(days=1)).timestamp())
        self.assertIn('requested_until', prep.stored_window_terms(final))

    def test_lengthening_edit_recalculates_budget_and_settings_receipt(self):
        self.schedule(recurrence='weekdays', until=DUE.replace(minute=5))
        requested = DUE.replace(hour=20)
        with patch.object(prep, 'MAX_DAILY_WINDOW_RUNS', 3):
            edited = self.service.preparations.edit(self.watched['id'], until=requested.isoformat())
            with self.assertRaises(prep.PreparationRefusal) as caught:
                self.service.preparations.edit(edited['id'], max_runs=4)
            self.assertEqual(caught.exception.code, 'invalid_max_runs')
        self.assertEqual(edited['max_runs'], 3)
        self.assert_honest(edited, DUE.replace(minute=10))
        terms = prep.stored_window_terms(edited)
        self.assertEqual(terms['requested_until'], requested.timestamp())
        self.assertEqual(terms['end_time'], '09:10:00')
        orchestrator = SettingsOrchestrator(self.store, service=self.service)
        receipt = orchestrator._preparation_receipt('edit', json.dumps({'id': edited['id']}))
        self.assertIn('09:10까지', receipt)
        self.assertNotIn('20:00', receipt)
        # Extending with the ordinary cap expands the derived count instead of retaining 3.
        extended = self.service.preparations.edit(edited['id'], until=requested.isoformat())
        self.assertEqual(extended['max_runs'], 133)
        self.assertNotIn('requested_until', prep.stored_window_terms(extended))

    def test_proposal_equality_includes_the_persisted_shortfall(self):
        self.now = DUE.timestamp() - 60
        requested = DUE.replace(hour=10).timestamp()
        with patch.object(prep, 'MAX_DAILY_WINDOW_RUNS', 2):
            window = prep.normalize_window(5, DUE.replace(hour=10).isoformat(), None,
                                           DUE.timestamp(), 'Asia/Seoul', self.now, recurrence='weekdays')
            args = dict(kind='prepare', goal='새 결과 확인', due_at=DUE.timestamp(), timezone='Asia/Seoul',
                        recurrence='weekdays', channel='web', state=prep.STATE_PROPOSED, window=window)
            first = self.service.preparations.create(**args, created_from='first', requested_until=requested)
            same = self.service.preparations.create(**args, created_from='second', requested_until=requested)
            different = self.service.preparations.create(**args, created_from='third', requested_until=requested + 3600)
        self.assertEqual(same['id'], first['id'])
        self.assertTrue(same['already_proposed'])
        self.assertNotEqual(different['id'], first['id'])

    def test_24_hour_inclusive_window_returns_the_real_capped_end(self):
        requested = DUE + timedelta(days=1)
        result = self.schedule(until=requested)
        end = requested - timedelta(minutes=5)
        self.assert_honest(self.watched, end)
        self.assertEqual(result['max_runs'], 288)
        self.assertEqual(result['requested_until'], prep.iso(requested.timestamp(), 'Asia/Seoul'))
        self.assertEqual(result['last_check'], prep.iso(end.timestamp(), 'Asia/Seoul'))
        self.assertEqual(result['covering_every_minutes'], 6)
        last_run = self.watched['due_at'] + self.watched['every_seconds'] * (self.watched['max_runs'] - 1)
        self.assertTrue(last_run + self.watched['every_seconds'] > self.watched['until_at'])

    def test_31_day_watch_hits_the_configured_cloud_budget_and_tells_the_owner(self):
        from personal_agent.cloud_engines import BUDGET_EXCEEDED
        from personal_agent.subscription_engines import SubscriptionEngines
        from personal_agent.agent_runtime import WORK_MODEL_TURNS, WORK_TOOL_ATTEMPTS, WORK_DEADLINE_SECONDS
        from test_cloud_engines import _Engine, BEDROCK_TOKEN

        self.now = DUE.timestamp() - 60
        self.judge = watch_engine([False] * 6)
        self.service.use_decision_engine(self.judge)
        requested = DUE + timedelta(days=31)
        window = prep.normalize_window(5, requested.isoformat(), None, DUE.timestamp(), 'Asia/Seoul', self.now)
        self.watched = self.service.preparations.create(
            kind='prepare', goal='새 결과 확인', due_at=DUE.timestamp(), timezone='Asia/Seoul', recurrence=None,
            channel='telegram', created_from='settings', state=prep.STATE_SCHEDULED,
            accepted_by=prep.ACCEPTED_OWNER_SETTINGS, window=window, requested_until=requested.timestamp(),
            delivery_mode=prep.DELIVERY_WHEN_NEEDED)
        self.assertEqual(self.watched['max_runs'], 31 * 288)
        budgets, seconds_at_dispatch = [], []

        class ObservedEngine(_Engine):
            def execute(inner, engine, prompt, tools, **kwargs):
                budgets.append(tools.capabilities.budget)
                seconds_at_dispatch.append(tools.capabilities.budget.remaining())
                return super().execute(engine, prompt, tools, **kwargs)

        engine = ObservedEngine()
        self.service.execution_adapter = engine
        self.service.subscription_engines = SubscriptionEngines(finder=lambda name: '/fixture/' + name, clock=lambda: self.now)
        self.service.connect_subscription_engine({'engine': 'claude-code', 'officially_authenticated': True})
        self.service.cloud_engines.clock = lambda: self.now
        self.service.save_cloud_profile({'engine': 'claude-code', 'provider': 'bedrock',
                                         'settings': {'AWS_REGION': 'us-east-1'}, 'daily_calls': 2})
        self.service.save_cloud_secret({'engine': 'claude-code', 'provider': 'bedrock',
                                        'name': 'AWS_BEARER_TOKEN_BEDROCK', 'value': BEDROCK_TOKEN})
        refusals = []
        admission = self.service.cloud_engines.admit

        def admit(launch):
            try:
                return admission(launch)
            except Exception as exc:
                refusals.append(exc.failure_class)
                raise

        # Wrap only to observe the typed refusal; the real transactional budget runs.
        with patch.object(self.service.cloud_engines, 'admit', side_effect=admit):
            self.run_slot('unused fixture answer')
            self.run_slot('unused fixture answer')
            blocked = self.run_slot('unused fixture answer')
            work = self.store.job(blocked['last_run_job_id'])
            self.assertEqual(work['status'], 'failed')
            reason = msg('cloud_engines.budget_exceeded', provider='Amazon Bedrock', cap=2)
            self.assertIn(reason, work['error'])
            self.assertEqual(refusals, [BUDGET_EXCEEDED])
            self.assertEqual((len(engine.launches), blocked['run_count']), (2, 3))
            self.assertEqual(blocked['last_decision_reason'], prep.REASON_RUN_FAILED_TOLD)
            self.assertIn(reason, unescape('\n'.join(self.texts())), 'the actual recorded Telegram send tells the owner')
            again = self.run_slot('unused fixture answer')
        self.assertEqual((len(engine.launches), again['run_count']), (2, 4), 'refused slots are counted; no worker launches beyond the cap')
        self.assertEqual(self.service.cloud_engines.used_today('claude-code', 'bedrock'), 2)
        self.assertEqual(again['state'], prep.STATE_SCHEDULED, 'the daily launch cap does not cancel the whole watch')
        self.assertEqual(len(budgets), 2)
        budget = budgets[0]
        self.assertIsNotNone(budget.ledger, 'scheduled worker tools share the durable Work budget')
        self.assertEqual((budget.turns, budget.attempts), (WORK_MODEL_TURNS, WORK_TOOL_ATTEMPTS))
        for seconds in seconds_at_dispatch:
            self.assertGreater(seconds, 0)
            self.assertLessEqual(seconds, WORK_DEADLINE_SECONDS)


class LegacyWindowMigrationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / 'state'
        self.store = QuickStore(self.root)
        self.preparations = prep.Preparations(self.store, clock=lambda: DUE.timestamp() - 60)

    def legacy(self, *, recurrence=None, until=None, state=prep.STATE_SCHEDULED):
        until = until or DUE.replace(hour=20)
        return self.preparations.create(kind='prepare', goal='새 결과 확인', due_at=DUE.timestamp(), timezone='Asia/Seoul',
                                        recurrence=recurrence, channel='web', created_from=None, state=state,
                                        window=(300, until.timestamp(), 96))

    def upgrade(self):
        with self.store.db() as db:
            db.execute("DELETE FROM config WHERE key='preparations_daily_cap_814'")
        self.store = QuickStore(self.root)
        self.preparations = prep.Preparations(self.store, clock=lambda: DUE.timestamp() - 60)

    def test_active_legacy_windows_cover_the_promised_end_on_restart(self):
        rhythm = self.legacy(recurrence='weekdays')
        one_off = self.legacy(until=DUE + timedelta(days=1))
        inactive = self.legacy(state=prep.STATE_PROPOSED)
        self.preparations.cancel(inactive['id'])
        # A partially consumed old one-off still needs its remaining checks.
        partial = self.legacy()
        seconds = self.legacy()
        with self.store.db() as db:
            db.execute('UPDATE preparations SET run_count=95,due_at=?,rule_start=NULL WHERE id=?',
                       (DUE.replace(hour=16, minute=55).timestamp(), partial['id']))
            db.execute('UPDATE preparations SET due_at=due_at+30,rule_start=rule_start+30,until_at=until_at+30,window_end=window_end+30 WHERE id=?',
                       (seconds['id'],))
        self.upgrade()
        row = self.preparations.get(rhythm['id'])
        self.assertEqual((row['max_runs'], row['until_at']), (133, DUE.replace(hour=20).timestamp()))
        tomorrow = {**row, **prep.next_day_window(row, row['until_at'])}
        self.assertEqual(prep.last_slot(tomorrow), (DUE.replace(hour=20) + timedelta(days=1)).timestamp())
        row = self.preparations.get(one_off['id'])
        self.assertEqual((row['max_runs'], row['until_at']), (288, (DUE + timedelta(hours=23, minutes=55)).timestamp()))
        self.assertEqual(prep.stored_window_terms(row)['requested_until'], (DUE + timedelta(days=1)).timestamp())
        self.assertEqual(self.preparations.get(partial['id'])['max_runs'], 133)
        self.assertEqual((self.preparations.get(seconds['id'])['max_runs'], self.preparations.get(seconds['id'])['until_at']),
                         (133, DUE.replace(hour=20, second=30).timestamp()))
        self.assertEqual(self.preparations.get(inactive['id'])['max_runs'], 96)
        # The migration is one-shot: a new explicit 96 remains intentional.
        explicit = self.legacy()
        with self.store.db() as db:
            prep.migrate(db)
        self.assertEqual(self.preparations.get(explicit['id'])['max_runs'], 96)

    def test_migration_shortens_recurring_terms_if_the_daily_cap_still_binds(self):
        row = self.legacy(recurrence='weekdays')
        with patch.object(prep, 'MAX_DAILY_WINDOW_RUNS', 3):
            self.upgrade()
        row = self.preparations.get(row['id'])
        self.assertEqual((row['max_runs'], row['until_at']), (3, DUE.replace(minute=10).timestamp()))
        self.assertEqual(prep.stored_window_terms(row)['end_time'], '09:10:00')
        self.assertEqual(prep.stored_window_terms(row)['requested_until'], DUE.replace(hour=20).timestamp())

    def test_invalid_legacy_rows_are_unchanged_and_do_not_block_migration(self):
        odd = self.legacy(recurrence='weekdays')
        bad_report = self.legacy()
        valid = self.legacy()
        with self.store.db() as db:
            db.execute('UPDATE preparations SET until_at=until_at+86400,window_end=window_end+86400 WHERE id=?',
                       (odd['id'],))
            db.execute('UPDATE preparations SET window_terms=? WHERE id=?',
                       (json.dumps({'report_seconds': 61}), bad_report['id']))
        before = [self.preparations.get(row['id']) for row in (odd, bad_report)]
        self.upgrade()
        self.assertEqual([self.preparations.get(row['id']) for row in (odd, bad_report)], before)
        self.assertEqual(self.preparations.get(valid['id'])['max_runs'], 133)
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT value FROM config WHERE key='preparations_daily_cap_814'").fetchone()[0], 'true')

    def test_shortfall_terms_do_not_make_run_now_rule_like_or_change_uncapped_digests(self):
        until = DUE.replace(hour=10).timestamp()
        plain = self.preparations.create(kind='prepare', goal='한 번 확인', due_at=DUE.timestamp(), timezone='Asia/Seoul',
                                         recurrence=None, channel='web', created_from=None, state=prep.STATE_SCHEDULED,
                                         window=(300, until, 13), requested_until=until)
        self.assertIsNone(plain['window_terms'])
        self.assertEqual(prep.digest([plain]), prep.digest([{**plain, 'window_terms': None}]))
        with patch.object(prep, 'MAX_DAILY_WINDOW_RUNS', 2):
            window = prep.normalize_window(5, DUE.replace(hour=10).isoformat(), None, DUE.timestamp(), 'Asia/Seoul', DUE.timestamp() - 60)
            capped = self.preparations.create(kind='prepare', goal='한 번 확인', due_at=DUE.timestamp(), timezone='Asia/Seoul',
                                              recurrence=None, channel='web', created_from=None, state=prep.STATE_SCHEDULED,
                                              window=window, requested_until=until)
        self.assertIn('requested_until', prep.stored_window_terms(capped))
        for row in (plain, capped):
            self.preparations.run_now(row['id'], channel='web', chat_id=None)
            self.assertEqual(self.preparations.get(row['id'])['run_count'], 1)
