"""Model-free regressions for the #717 fresh-context findings."""
import itertools
import json
from unittest import mock

from personal_agent import preparations as prep, work_mode
from personal_agent.messages import msg
import test_self_narrowing_717 as sn
import test_preparation_suggestions as sugg
import test_preparations as pt


class SelfChanges(sn._Case):
    def setUp(self):
        super().setUp()
        self.clock[0] = 1800000000.0
        self.service.preparations.clock = lambda: self.clock[0]
        self.preps = self.service.preparations
        self.serial = 0

    running = sn.SelfNarrowing.running
    change = sn.SelfNarrowing.change
    settle = sn.SelfNarrowing.settle
    notices = sn.SelfNarrowing.notices

    def test_combined_changes_have_one_complete_undo(self):
        for sequence in itertools.permutations(('pause', 'edit', 'cancel')):
            for settled in (False, True):
                with self.subTest(sequence=sequence, settled=settled):
                    row, run = self.running()
                    # Cancellation ends the run's authority; exercise each prefix through cancel.
                    sequence = sequence[:sequence.index('cancel') + 1]
                    for operation in sequence:
                        self.assertTrue(self.change(row, run, operation, {'max_runs': 3})['applied'])
                    event = json.loads(self.preps.get(row['id'])['self_change'])
                    for key, value in event['before'].items():
                        self.assertEqual(value, row[key])
                    if settled:
                        self.settle(row, run)
                    restored = self.preps.restore(row['id'])
                    self.assertEqual(restored['max_runs'], 8)
                    self.assertIsNone(restored['run_return_state'])
                    self.assertEqual(restored['state'], prep.STATE_SCHEDULED if settled else prep.STATE_RUNNING)

    def test_pause_then_narrow_resume_undoes_both(self):
        for cancel in (False, True):
            row, run = self.running()
            self.change(row, run, 'pause')
            self.change(row, run, changes={'max_runs': 3})
            if cancel:
                self.assertTrue(self.change(row, run, 'cancel')['applied'])
            draft = self.settings.propose('owner', 'http', 'preparations', 'resume', row['id'])
            self.assertEqual(self.settings.confirm('owner', 'http', draft['draft_id'], draft['digest'])['state'], 'applied')
            restored = self.preps.get(row['id'])
            self.assertEqual(restored['max_runs'], 8)
            self.assertIsNone(restored['run_return_state'])

    def test_restore_preserves_already_scheduled_next_slot(self):
        row, run = self.running()
        self.change(row, run, changes={'max_runs': 3})
        settled = self.settle(row, run)
        restored = self.preps.restore(row['id'])
        self.assertEqual(restored['due_at'], settled['due_at'])

    def test_run_now_pause_undo_restores_return_state(self):
        row, run = self.running()
        self.settle(row, run)
        row = self.preps.get(row['id'])
        self.clock[0] += 1
        work = self.preps.start(row, channel='web', chat_id=None, now=self.clock[0], immediate=True)
        run = self.store.job(work)
        before = self.preps.get(row['id'])
        self.assertEqual(before['run_return_state'], prep.STATE_SCHEDULED)
        self.change(before, run, 'pause')
        self.assertEqual(self.preps.resume(row['id'])['run_return_state'], before['run_return_state'])

    def test_narrowing_undo_does_not_resume_an_owner_paused_run_now(self):
        row, run = self.running()
        self.settle(row, run)
        self.preps.pause(row['id'])
        self.clock[0] += 1
        row = self.preps.get(row['id'])
        work = self.preps.start(row, channel='web', chat_id=None, now=self.clock[0], immediate=True)
        run = self.store.job(work)
        self.change(row, run, changes={'max_runs': 3})
        settled = self.settle(self.preps.get(row['id']), run)
        self.assertEqual(settled['state'], prep.STATE_PAUSED)
        restored = self.preps.restore(row['id'])
        self.assertEqual(restored['state'], prep.STATE_PAUSED)
        self.assertEqual(restored['max_runs'], 8)
        self.assertEqual(restored['due_at'], row['due_at'])

    def test_no_surface_notice_is_visible_in_settings_and_not_queued(self):
        row, run = self.running()
        with mock.patch.object(self.service.preparations_service, 'notice_destination', return_value=None):
            self.assertTrue(self.change(row, run, changes={'max_runs': 3})['applied'])
        self.assertEqual(self.notices(), [])
        [visible] = self.service.preparations_status()['preparations']
        self.assertIn('The source is no longer useful.', visible['last_result'])
        self.assertIn(msg('button.restore_reservation'), visible['last_result'])
        self.assertEqual(self.preps.restore(row['id'])['max_runs'], 8)

    def test_notice_uses_human_schedule_and_local_undo(self):
        row, run = self.running()
        self.change(row, run, changes={'until': prep.iso(self.clock[0] + 1200), 'max_runs': 3})
        text = self.service.preparations_service.status_notice_text(self.notices()[0])
        for raw in ('until_at', 'max_runs', str(self.clock[0] + 1200), 'restore', 'resume'):
            self.assertNotIn(raw, text)
        self.assertIn(msg('button.restore_reservation'), text)
        self.assertIn(prep.proposal_summary(row, self.service.home_zone(), brief=False), text)

    def test_english_owner_gets_english_schedule_and_undo(self):
        cfg = self.store.config('telegram', {})
        self.store.put('telegram', {**cfg, 'owner_language': 'en'})
        row, run = self.running()
        self.change(row, run, changes={'max_runs': 3})
        text = self.service.preparations_service.status_notice_text(self.notices()[0])
        self.assertIn(prep.schedule_text(row, self.service.home_zone(), lang='en'), text)
        self.assertIn(msg('button.restore_reservation', 'en'), text)
        self.assertNotIn('max_runs', text)

    def test_budget_skip_keeps_narrowing_receipt_and_consumed_count_on_undo(self):
        self.preps.set_budget(1)
        row, run = self.running()
        self.assertTrue(self.change(row, run, changes={'max_runs': 3})['applied'])
        receipt = self.preps.get(row['id'])['self_change']
        current = self.settle(row, run)
        self.clock[0] = current['due_at']
        self.assertIsNone(self.preps.start(current, channel='web', chat_id=None, now=self.clock[0]))
        skipped = self.preps.get(row['id'])
        self.assertEqual((skipped['state'], skipped['run_count'], skipped['max_runs']),
                         (prep.STATE_SCHEDULED, 2, 3))
        self.assertEqual(skipped['self_change'], receipt)
        self.assertEqual(len(self.notices()), 1)
        self.assertEqual(len(self.store.jobs()), 1)
        # A skipped slot creates no current Work through which to change the reservation.
        self.assertFalse(self.change(row, run, 'pause')['applied'])
        restored = self.preps.restore(row['id'])
        self.assertEqual((restored['max_runs'], restored['run_count'], restored['due_at']),
                         (8, 2, skipped['due_at']))
        self.assertTrue(restored['self_change_undone'])

    def test_exhausted_budget_preserves_self_pause_and_cannot_be_raised_by_run(self):
        self.preps.set_budget(1)
        row, run = self.running()
        handler = self.settings.work_tools('owner', 'web', run['id'], unattended=True,
                                           own_preparation=row['id'])
        refused = handler('settings_change', {'category': 'scheduled_work', 'setting': 'daily_calls', 'value': '0'})
        self.assertFalse(refused['applied'])
        self.assertEqual(self.preps.budget_settings()['daily_calls'], 1)
        self.assertTrue(self.change(row, run, 'pause')['applied'])
        current = self.settle(row, run)
        self.clock[0] += 601
        self.assertEqual(current['state'], prep.STATE_PAUSED)
        self.assertEqual(self.preps.due(self.clock[0]), [])
        day = self.preps.budget_day(self.clock[0])
        # The owner's explicit run-now remains possible, even for the self-paused row.
        work = self.preps.start(current, channel='web', chat_id=None, now=self.clock[0], immediate=True)
        self.assertIsNotNone(work)
        self.assertEqual(self.store.config(prep.SCHEDULED_USAGE_PREFIX + day), 1)
        current = self.settle(self.preps.get(row['id']), self.store.job(work))
        self.assertEqual(current['state'], prep.STATE_PAUSED)


class DreamFindings(pt._Case):
    suggest = sugg.Offer.suggest
    deliver = sugg.Offer.deliver
    row_notification = sugg.Offer.row_notification

    def test_budget_does_not_consume_untold_or_upkeep_paused_dream_slots(self):
        anchor = self.receive('question')
        self.deliver()
        row = self.suggest(anchor, recurrence='daily')
        preps = self.service.preparations
        preps.set_budget(1)
        day = preps.budget_day(row['due_at'])
        self.store.put(prep.SCHEDULED_USAGE_PREFIX + day, 1)
        self.now = row['due_at']
        self.tick()  # The per-row notice is only queued; the dream is still untold.
        current = preps.get(row['id'])
        self.assertEqual((current['run_count'], current['due_at']), (0, row['due_at']))
        self.assertIsNone(current['offered_at'])
        self.assertEqual(self.runs(row['id']), [])
        self.assertIsNone(self.store.config(prep.SCHEDULED_SKIPPED_PREFIX + row['id']))
        self.deliver()
        self.service.owner_model.set_controls({'dream': False})
        self.tick()  # A confirmed notice cannot bypass paused upkeep, either.
        current = preps.get(row['id'])
        self.assertIsNotNone(current['offered_at'])
        self.assertEqual(current['run_count'], 0)
        self.assertEqual(self.runs(row['id']), [])
        self.assertIsNone(self.store.config(prep.SCHEDULED_SKIPPED_PREFIX + row['id']))
        self.service.owner_model.set_controls({'dream': True})
        self.tick()  # Once told and enabled, the same due slot reaches the daily budget.
        current = preps.get(row['id'])
        self.assertEqual((current['state'], current['run_count'], current['last_outcome']),
                         (prep.STATE_SCHEDULED, 1, 'skipped'))
        self.assertEqual(self.runs(row['id']), [])
        self.assertEqual(self.store.config(prep.SCHEDULED_SKIPPED_PREFIX + row['id'])['count'], 1)
        self.assertEqual(self.row_notification(row)['state'], 'sent')

    def legacy_scheduled_dream(self, anchor, goal):
        """An unbounded, already-offered recurring row from before per-row notices."""
        row = self.service.preparations.create(
            kind=prep.KIND_PREPARE, goal=goal, due_at=self.now + prep.SUGGESTION_EXPIRY_SECONDS + 86400,
            timezone='Asia/Seoul', recurrence='daily', channel=prep.CHANNEL_TELEGRAM, created_from=anchor,
            state=prep.STATE_SCHEDULED, accepted_by=prep.ACCEPTED_DREAM, origin=prep.ORIGIN_DREAM)
        self.service.preparations.mark_offered([row], self.now)
        return row

    def test_confirmed_legacy_notice_preserves_old_offered_dreams(self):
        offered_at = self.now
        rows = []
        for queued in (False, True):
            anchor = self.receive(f'question {queued}')
            row = self.legacy_scheduled_dream(anchor, f'legacy goal {queued}')
            notice = self.store.queue_notification(anchor, pt.CHAT, pt.GENERATION, 'preparation_scheduled',
                                                   fingerprint='ids:' + row['id'])
            with mock.patch('time.time', return_value=offered_at):
                self.store.update_notification(notice['id'], 'sent', message_id=9001, delivered=True)
            if queued:
                self.store.queue_notification(f"{anchor}/dream:{row['id']}", pt.CHAT, pt.GENERATION,
                                              'preparation_scheduled', fingerprint='ids:' + row['id'])
            rows.append((row, queued))
        self.now += prep.SUGGESTION_EXPIRY_SECONDS + 1
        self.service = self.make_service()  # Upgrade/restart consumes the persisted legacy receipts.
        self.model_calls.clear()
        self.tick()
        for row, queued in rows:
            current = self.service.preparations.get(row['id'])
            self.assertEqual(current['offered_at'], offered_at)
            self.assertEqual(current['state'], prep.STATE_SCHEDULED)
            self.assertEqual(self.row_notification(row) is not None, queued)
        self.assertEqual(self.model_calls, [])

    def test_legacy_notice_requires_exact_coverage_and_confirmed_delivery(self):
        rows = []
        for binding, confirmed in (('other-row', True), ('suffix', True), ('covered', False)):
            anchor = self.receive('question ' + binding)
            row = self.legacy_scheduled_dream(anchor, 'legacy goal ' + binding)
            notice = self.store.queue_notification(
                anchor, pt.CHAT, pt.GENERATION, 'preparation_scheduled',
                fingerprint='ids:' + (row['id'] if binding == 'covered' else
                                      row['id'] + '-other' if binding == 'suffix' else 'other-row'))
            with mock.patch('time.time', return_value=self.now):
                self.store.update_notification(notice['id'], 'sent', message_id=9001, delivered=confirmed)
            rows.append(row)
        self.now += prep.SUGGESTION_EXPIRY_SECONDS + 1
        self.tick()
        for row in rows:
            current = self.service.preparations.get(row['id'])
            self.assertIsNone(current['offered_at'])
            self.assertEqual(current['state'], prep.STATE_EXPIRED)
            self.assertEqual(self.runs(row['id']), [])
            self.assertIsNone(self.row_notification(row))

    def test_queued_or_cancelled_notice_never_releases_row(self):
        anchor = self.receive('question')
        row = self.suggest(anchor)
        self.service.queue_preparation_suggestion(None)
        notice = self.row_notification(row)
        self.assertIsNotNone(notice)
        self.assertIsNone(self.service.preparations.get(row['id'])['offered_at'])
        self.service.preparations.mark_offered([row], self.now)  # legacy queue-time mark
        self.store.telegram.update_notification(notice['id'], 'cancelled')
        self.now = row['due_at']
        self.tick()
        self.assertEqual(self.runs(row['id']), [])

    def test_notice_uses_its_queue_time(self):
        anchor = self.receive('question')
        self.now += 600
        row = self.suggest(anchor)
        self.service.queue_preparation_suggestion(None)
        self.assertEqual(self.row_notification(row)['created'], self.now)

    def test_work_mode_before_queue_does_not_cancel_the_new_notice(self):
        anchor = self.receive('question')
        self.deliver()
        with self.store.db() as db:
            db.execute('UPDATE jobs SET created=? WHERE id=?', (self.now, anchor))
        self.now += 10
        work_mode.set_enabled(self.store, True, surface='settings', clock=lambda: self.now)
        self.now += 10
        work_mode.set_enabled(self.store, False, surface='settings', clock=lambda: self.now)
        self.now += 10
        row = self.suggest(anchor)
        self.service.queue_preparation_suggestion(None)
        self.deliver()
        self.assertEqual(self.row_notification(row)['state'], 'sent')

    def test_delivery_cancellation_cannot_release_a_dream(self):
        for cause in ('generation', 'unbound', 'work-mode'):
            with self.subTest(cause=cause):
                anchor = self.receive('question ' + cause)
                self.deliver()
                row = self.suggest(anchor, goal='goal ' + cause)
                self.service.queue_preparation_suggestion(None)
                cfg = self.store.config('telegram', {})
                self.now += 1
                if cause == 'work-mode':
                    work_mode.set_enabled(self.store, True, surface='settings', clock=lambda: self.now)
                else:
                    self.store.put('telegram', {**cfg, **({'generation': 'new'} if cause == 'generation'
                                                       else {'user_id': pt.CHAT + 1})})
                self.service.deliver_notification()
                self.assertEqual(self.row_notification(row)['state'], 'cancelled')
                self.store.put('telegram', cfg)
                if cause == 'work-mode':
                    work_mode.set_enabled(self.store, False, surface='settings', clock=lambda: self.now)
                self.now = row['due_at']
                self.tick()
                self.assertEqual(self.runs(row['id']), [])
                self.assertIsNone(self.service.preparations.get(row['id'])['offered_at'])

    def test_no_current_anchor_surface_means_no_reserved_slots(self):
        anchor = self.receive('question')
        for anchor_id in ('missing', anchor):
            with mock.patch.object(self.service, 'surface_of', return_value=None):
                outcomes, calls = self.service.suggest_preparations(anchor_id, [sugg.suggestion(due=self.due_iso(86400))])
            self.assertNotIn('preparation_id', outcomes[0])
            self.assertEqual(calls, 0)
        cfg = self.store.config('telegram', {})
        self.store.put('telegram', {**cfg, 'generation': 're-paired'})
        outcomes, _ = self.service.suggest_preparations(anchor, [sugg.suggestion(due=self.due_iso(86400))])
        self.assertNotIn('preparation_id', outcomes[0])
        self.assertEqual(self.service.preparations.rows(), [])

    def test_untold_scheduled_rows_expire_and_release_capacity(self):
        anchor = self.receive('question')
        row = self.suggest(anchor)
        self.now += prep.SUGGESTION_EXPIRY_SECONDS + 1
        self.service.preparations.expire_suggestions(self.now)
        self.assertEqual(self.service.preparations.get(row['id'])['state'], prep.STATE_EXPIRED)
        self.assertEqual(self.service.preparations.untold_scheduled_suggestions(), [])

    def test_per_row_notice_blocks_another_batch_on_same_anchor(self):
        anchor = self.receive('question')
        row = self.suggest(anchor)
        self.service.queue_preparation_suggestion(None)
        self.service.preparations.cancel(row['id'])
        outcomes, _ = self.service.suggest_preparations(anchor, [sugg.suggestion(goal='different', due=self.due_iso(86400))])
        self.assertEqual(outcomes, [{'reason': 'anchor-offered'}])

    def test_release_skips_missed_slot_for_untold_and_disabled_rows(self):
        for once, disabled in itertools.product((False, True), repeat=2):
            with self.subTest(once=once, disabled=disabled):
                anchor = self.receive(f'question {once} {disabled}')
                self.deliver()
                row = self.suggest(anchor, goal=f'goal {once} {disabled}', recurrence='once' if once else 'daily')
                if disabled:
                    self.service.queue_preparation_suggestion(None)
                    self.deliver()
                    self.service.owner_model.set_controls({'dream': False})
                self.now = row['due_at'] + 1
                self.tick()
                self.assertEqual(self.runs(row['id']), [])
                self.service.owner_model.set_controls({'dream': True})
                self.tick()
                self.deliver()
                self.tick()
                current = self.service.preparations.get(row['id'])
                self.assertEqual(self.runs(row['id']), [])
                self.assertEqual(current['state'], prep.STATE_EXPIRED if once else prep.STATE_SCHEDULED)
                if not once:
                    self.assertGreater(current['due_at'], self.now)
