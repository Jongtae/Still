"""Connection and expiry notices share the existing durable delivery path."""
from unittest.mock import patch

from personal_agent import preparations as prep
from personal_agent.messages import msg
from personal_agent.quickstart_store import QuickStore

from test_preparations import CHAT, GENERATION, _Case
from test_slack_surface import SlackCase


class PreparationStatusNoticeTests(_Case):
    def watch(self):
        due = self.now + 60
        return self.service.preparations.create(
            kind=prep.KIND_PREPARE, goal='공개 자료 확인', title='자료 점검', due_at=due,
            timezone='Asia/Seoul', recurrence=None, channel='telegram', created_from='settings',
            state=prep.STATE_SCHEDULED, accepted_by=prep.ACCEPTED_OWNER_SETTINGS,
            window=(600, due + 1200, 3), delivery_mode=prep.DELIVERY_WHEN_NEEDED,
        )

    def start_watch(self, row):
        self.now = row['due_at'] + 1
        self.assertTrue(self.tick())
        [work] = self.runs(row['id'])
        self.assertIsNone(work['chat_id'])
        return work

    def park(self, work):
        with patch.object(self.service, '_park_for_connector', return_value='private connector guidance'):
            self.assertTrue(self.service.park_for_connector_read(work, 'fixture-connector'))

    def restart(self):
        self.store = QuickStore(self.root)
        self.service = self.make_service()

    def drain(self):
        while self.service.deliver_notification():
            pass

    def test_one_notice_per_wait_and_final_report_use_the_same_work(self):
        row = self.watch()
        self.now = row['due_at'] + 1200
        self.assertTrue(self.tick())
        [work] = self.runs(row['id'])
        waiting = msg('preparations.notice_waiting', title='자료 점검')

        self.park(work)
        self.park(work)
        self.restart()
        self.drain()
        self.assertEqual(self.texts(), [waiting])
        self.assertTrue(self.service._schedule_resumed_work(work['id']))
        self.park(work)
        self.drain()
        self.assertEqual(self.texts(), [waiting, waiting])

        self.assertTrue(self.service._schedule_resumed_work(work['id']))
        answer = '자료가 확인됐어요.'
        self.script = [{'content': answer}] * 2
        self.assertTrue(self.service.run_one())
        self.assertTrue(self.tick())
        self.drain()
        self.assertEqual(sum(answer in text for text in self.texts()), 1)
        self.assertEqual(self.texts().count(waiting), 2)
        self.assertNotIn('private connector guidance', repr(self.texts()))

    def test_recovered_or_stopped_wait_does_not_send_a_stale_notice(self):
        for resolution in ('resume', 'cancel'):
            with self.subTest(resolution=resolution):
                row = self.watch()
                work = self.start_watch(row)
                self.park(work)
                if resolution == 'resume':
                    self.assertTrue(self.service._schedule_resumed_work(work['id']))
                    self.park(work)
                    self.drain()
                    self.assertEqual(len(self.texts()), 1, 'only the current wait was sent')
                else:
                    self.service.cancel_preparation(row['id'])
                    self.drain()
                    self.assertEqual(len(self.texts()), 1, 'cancelled wait stayed silent')

    def test_expired_wait_replaces_unsent_wait_and_does_not_restart(self):
        row = self.watch()
        work = self.start_watch(row)
        self.park(work)
        self.now = row['window_end']
        self.assertTrue(self.tick())
        self.restart()
        self.drain()
        self.assertEqual(self.texts(), [msg('preparations.notice_elapsed', title='자료 점검')])
        self.assertEqual(self.store.job(work['id'])['status'], 'failed')
        self.assertFalse(self.tick())
        self.drain()
        self.assertEqual(len(self.texts()), 1)

    def test_expiry_before_any_work_is_reported_once_after_restart(self):
        row = self.watch()
        self.now = row['window_end']
        self.assertTrue(self.tick())
        self.restart()
        self.drain()
        self.assertEqual(self.texts(), [msg('preparations.notice_elapsed', title='자료 점검')])
        self.assertEqual(self.runs(row['id']), [])
        self.assertFalse(self.tick())

    def test_notice_for_a_previous_pairing_cannot_reach_the_new_owner(self):
        row = self.watch()
        work = self.start_watch(row)
        self.park(work)
        self.store.put('telegram', {'enabled': True, 'user_id': 123, 'generation': 'new-pairing'})
        self.drain()
        self.assertEqual(self.texts(), [])

    def test_preparation_from_a_previous_owner_does_not_queue_on_new_pairing(self):
        original = self.store.enqueue('자료를 확인해 줘', 'source-797', channel=f'telegram:{GENERATION}', chat_id=CHAT)
        row = self.watch()
        with self.store.db() as db:
            db.execute('UPDATE preparations SET created_from=? WHERE id=?', (original, row['id']))
        self.store.put('telegram', {'enabled': True, 'user_id': 123, 'generation': 'new-pairing'})
        work = self.start_watch(row)
        self.park(work)
        self.assertIsNone(self.store.next_notification('telegram'))

    def test_expired_wait_is_not_sent_even_before_scheduler_settles_it(self):
        row = self.watch()
        work = self.start_watch(row)
        self.park(work)
        self.now = row['window_end']
        self.drain()
        self.assertEqual(self.texts(), [])
        self.assertTrue(self.tick())
        self.drain()
        self.assertEqual(self.texts(), [msg('preparations.notice_elapsed', title='자료 점검')])


class SlackPreparationStatusNoticeTests(SlackCase):
    def test_quiet_wait_posts_only_to_its_configured_conversation(self):
        self.connect()
        before = self.fake.posted()
        now = self.service.preparations.clock()
        row = self.service.preparations.create(
            kind=prep.KIND_PREPARE, goal='공개 자료 확인', title='자료 점검', due_at=now,
            timezone='Asia/Seoul', recurrence=None, channel='slack', created_from='settings',
            state=prep.STATE_SCHEDULED, accepted_by=prep.ACCEPTED_OWNER_SETTINGS,
            window=(600, now + 1200, 3), delivery_mode=prep.DELIVERY_WHEN_NEEDED,
        )
        self.assertTrue(self.service.run_due_preparation(now=now))
        work = self.store.job(self.service.preparations.get(row['id'])['last_run_job_id'])
        self.assertEqual((work['channel'], work['chat_id']), ('web', None))
        with patch.object(self.service, '_park_for_connector', return_value='private connector guidance'):
            self.assertTrue(self.service.park_for_connector_read(work, 'fixture-connector'))

        self.assertIsNone(self.store.next_notification('telegram'))
        self.assertTrue(self.surface.deliver_notification())
        self.assertFalse(self.surface.deliver_notification())
        self.assertEqual(self.fake.posted(), before + [msg('preparations.notice_waiting', title='자료 점검')])
