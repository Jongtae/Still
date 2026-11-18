"""#970 slice 1: the dispatcher, owner-first claims and the command lane, on a real store.

Lanes are injected (``work_lane_spawn``) so each test decides when a lane runs; no
model or account is reached, and the transport fails any request it is sent.
"""
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest import mock

from personal_agent.decision import FixtureDecisionEngine
from personal_agent.messages import msg
from personal_agent.providers import ModelAdapter
from personal_agent.quickstart_service import AgentService, LANE_COMMAND, LANE_WORK
from personal_agent.quickstart_store import QuickStore


class Lanes(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = QuickStore(self.temp.name)
        self.calls = []
        self.store.put("work_lanes", 1)

        def transport(url, body, headers=None, timeout=60):
            self.calls.append(url)
            if url.endswith('/sendMessage'):
                return {'ok': True, 'result': {'message_id': len(self.calls)}}
            if url.endswith(('/editMessageText', '/deleteMessage', '/setMessageReaction', '/sendChatAction')):
                return {'ok': True, 'result': True}
            raise AssertionError(f'unexpected request {url}')
        self.service = AgentService(self.store, ModelAdapter(transport), transport)
        self.service.use_decision_engine(FixtureDecisionEngine())
        self.spawned = []
        self.service.work_lane_spawn = lambda target, name: self.spawned.append((name, target))

    def owner(self, text, key, chat=None, channel='web'):
        return self.store.enqueue(text, key, channel=channel, chat_id=chat, owner_typed=True)

    def background(self, text, key):
        return self.store.enqueue(text, key)

    def status(self, work):
        return self.store.job(work)['status']

    def finish(self, work):
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='succeeded' WHERE id=?", (work,))

    def run_lane(self, index=0):
        name, target = self.spawned.pop(index)
        target()
        return name


class OwnerFirstClaims(Lanes):
    def test_an_owner_work_is_claimed_before_older_background_works(self):
        first = self.background('scheduled check one', 'api-1')
        second = self.background('scheduled check two', 'api-2')
        owner = self.owner('what is on today', 'owner-1')
        self.assertEqual(self.service._claim_queued_work()['id'], owner)
        self.finish(owner)
        self.assertEqual(self.service._claim_queued_work()['id'], first)
        self.finish(first)
        self.assertEqual(self.service._claim_queued_work()['id'], second)
        self.assertIs(self.service._claim_queued_work(), False)

    def test_a_retry_of_an_owner_work_stays_in_the_owner_class(self):
        original = self.owner('look it up', 'owner-1')
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='failed' WHERE id=?", (original,))
        background = self.background('scheduled check', 'api-1')
        retry = self.store.enqueue('again', 'owner-2')
        with self.store.db() as db:
            db.execute("UPDATE jobs SET relation_kind='retry',related_job_id=? WHERE id=?", (original, retry))
        self.assertEqual(self.service._claim_queued_work()['id'], retry)
        self.finish(retry)
        self.assertEqual(self.service._claim_queued_work()['id'], background)

    def test_a_waiting_owner_work_holds_its_conversation_but_not_others(self):
        held = self.owner('first message', 'owner-1', chat=1, channel='telegram:g')
        behind = self.owner('second message', 'owner-2', chat=1, channel='telegram:g')
        other = self.owner('another chat', 'owner-3', chat=2, channel='telegram:g')
        background = self.background('scheduled check', 'api-1')
        self.service.side_answer_flight.add(held)
        self.assertEqual(self.service._claim_queued_work()['id'], other)
        self.finish(other)
        self.assertEqual(self.service._claim_queued_work()['id'], background)
        self.assertIs(self.service._claim_queued_work(), False, 'the second message never runs before the first')
        self.finish(background)
        self.service.side_answer_flight.discard(held)
        self.assertEqual(self.service._claim_queued_work()['id'], held)
        self.assertEqual(self.status(behind), 'queued')


class CommandLane(Lanes):
    def test_only_owner_typed_photo_free_first_run_explicit_forms_are_commands(self):
        command = self.owner('/notes', 'owner-1')
        natural = self.owner('show my notes', 'owner-2')
        scheduled = self.background('/notes', 'api-1')
        photo = self.owner('/notes', 'owner-3', chat=42, channel='telegram:g')
        self.store.attach_telegram_photo(photo, 'file-1')
        resumed = self.owner('/settings', 'owner-4')
        with self.store.db() as db:
            db.execute("INSERT INTO messages(role,content,channel,created,job_id) VALUES ('user','/settings','web',?,?)",
                       (time.time(), resumed))
        original = self.owner('/notes', 'owner-5')
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='failed' WHERE id=?", (original,))
        retry = self.owner('/notes', 'owner-6')
        with self.store.db() as db:
            db.execute("UPDATE jobs SET relation_kind='retry',related_job_id=? WHERE id=?", (original, retry))
        self.assertEqual(self.service._claim_queued_work(lanes=(LANE_COMMAND,))['id'], command)
        self.assertIs(self.service._claim_queued_work(lanes=(LANE_COMMAND,)), False)
        claimed = set()
        while (job := self.service._claim_queued_work(lanes=(LANE_WORK,))):
            claimed.add(job['id'])
            self.finish(job['id'])
        self.assertEqual(claimed, {natural, scheduled, photo, resumed, retry})

    def test_a_command_is_answered_while_a_tool_capable_work_runs_with_no_model_call(self):
        running = self.owner('a long request', 'owner-1')
        self.assertEqual(self.service.dispatch_work(), 1)
        self.assertEqual([name for name, _ in self.spawned], ['agentos-work-lane'])
        self.assertEqual(self.status(running), 'running')
        queued = self.owner('another long request', 'owner-2')
        command = self.owner('/notes', 'owner-3')
        if self.service.lane_work_ids(LANE_WORK):  # the tool-capable lane is busy
            self.assertEqual(self.service.dispatch_work(), 1)
            self.assertEqual(self.spawned[-1][0], 'agentos-command-lane')
            self.run_lane(-1)
        job = self.store.job(command)
        self.assertEqual(job['status'], 'succeeded')
        self.assertEqual(job['response'], msg('service.there_are_no_saved_notes_try'))
        self.assertEqual(self.calls, [], 'no model, tool or network request')
        self.assertEqual(self.status(running), 'running', 'the running Work is untouched')
        self.assertEqual(self.status(queued), 'queued', 'still one tool-capable Work at a time')
        self.assertNotIn(command, self.service.lane_work_ids())

    def test_an_explicit_form_is_never_judged_as_a_steer(self):
        self.owner('a long request', 'owner-1', chat=42, channel='telegram:g')
        self.service.dispatch_work()
        command = self.owner('/notes', 'owner-2', chat=42, channel='telegram:g')
        judged = []
        self.service.steer_spawn = judged.append
        self.assertFalse(self.service.consider_steer(command, '/notes'))
        self.assertEqual(judged, [])

    def test_a_steer_is_never_judged_against_a_running_command(self):
        command = self.owner('/notes', 'owner-1', chat=42, channel='telegram:g')
        self.service.dispatch_work()
        self.assertEqual(self.service.lane_work_ids(LANE_COMMAND), {command})
        message = self.owner('also check this', 'owner-2', chat=42, channel='telegram:g')
        judged = []
        self.service.steer_spawn = judged.append
        self.assertFalse(self.service.consider_steer(message, 'also check this'))
        self.assertEqual(judged, [])


class Dispatcher(Lanes):
    def test_the_dispatcher_never_runs_a_work_and_keeps_one_tool_capable_lane(self):
        first = self.owner('first request', 'owner-1')
        second = self.owner('second request', 'owner-2')
        with mock.patch.object(self.service, '_route_work', side_effect=AssertionError('ran on the dispatcher')):
            self.assertEqual(self.service.dispatch_work(), 1)
            self.assertEqual(self.service.dispatch_work(), 0)
        self.assertEqual((self.status(first), self.status(second)), ('running', 'queued'))
        self.assertEqual(self.service.lane_work_ids(LANE_WORK), {first})
        self.run_lane()
        self.assertNotIn(first, self.service.lane_work_ids())
        self.assertEqual(self.service.dispatch_work(), 1)
        self.assertEqual(self.service.lane_work_ids(LANE_WORK), {second})

    def test_a_lane_that_cannot_start_ends_its_work_and_frees_the_lane(self):
        work = self.owner('a request', 'owner-1')
        self.service.work_lane_spawn = mock.Mock(side_effect=RuntimeError('no thread'))
        self.assertEqual(self.service.dispatch_work(), 1)
        self.assertEqual(self.status(work), 'failed')
        self.assertEqual(self.service.lane_work_ids(), frozenset())

    def test_ticks_and_deliveries_run_while_a_work_runs(self):
        """Acceptance 2 in slice 1: a long Work holds its lane, not the work loop."""
        self.store.secret('telegram_token', '123456:TEST_TOKEN')
        self.store.put('telegram', {'enabled': True, 'generation': 'g', 'user_id': 42, 'cursor': 0})
        done = self.store.enqueue('earlier request', 'owner-0', channel='telegram:g', chat_id=42, owner_typed=True)
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='succeeded',response='earlier answer',delivery='pending' WHERE id=?", (done,))
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        def spawn(target, name):
            def run():
                try:target()
                finally:finished.set()
            threading.Thread(target=run, name=name, daemon=True).start()
        self.service.work_lane_spawn = spawn

        def long_route(run):
            entered.set()
            self.assertTrue(release.wait(10))
            raise ValueError('fixture end')
        self.owner('a long request', 'owner-1')
        with mock.patch.object(self.service, '_route_work', side_effect=long_route):
            started = time.monotonic()
            self.service._work_iteration(0, 0)
            self.assertTrue(entered.wait(5))
            self.service._work_iteration(0, 0)
            self.assertLess(time.monotonic() - started, 5, 'the work loop never waits for the Work')
            self.assertEqual(self.store.job(done)['delivery'], 'sent', 'another Work\'s reply went out meanwhile')
            release.set()
            self.assertTrue(finished.wait(10))
        self.assertEqual(self.service.lane_work_ids(), frozenset())


class LaneHeldWorks(Lanes):
    def setUp(self):
        super().setUp()
        self.store.secret('telegram_token', '123456:TEST_TOKEN')
        self.store.put('telegram', {'enabled': True, 'generation': 'g', 'user_id': 42, 'cursor': 0})
        self.work = self.store.enqueue('a request', 'owner-1', channel='telegram:g', chat_id=42, owner_typed=True)
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='succeeded',response='the answer',delivery='pending' WHERE id=?", (self.work,))

    def test_a_reply_waits_until_its_lane_finished_the_tail(self):
        self.service.hold_lane(self.work, LANE_WORK)
        self.service.deliver_one()
        self.assertEqual(self.store.job(self.work)['delivery'], 'pending')
        self.service.release_lane(self.work)
        self.service.deliver_one()
        self.assertEqual(self.store.job(self.work)['delivery'], 'sent')

    def test_a_notice_waits_until_its_lane_finished_the_tail(self):
        self.store.queue_notification(self.work, 42, 'g', 'context_approval_needed')
        self.assertIsNone(self.store.next_notification('telegram', {self.work}))
        self.assertEqual(self.store.next_notification('telegram')['job_id'], self.work)

    def test_a_preparation_run_in_its_lane_is_not_settled(self):
        with self.store.db() as db:
            db.execute("UPDATE jobs SET delivery='none' WHERE id=?", (self.work,))
            now = time.time()
            db.execute("INSERT INTO preparations(id,owner,kind,goal_text,due_at,timezone,channel,state,last_run_job_id,"
                       "created_at,updated_at) VALUES ('p1','local-owner','prepare','g',?,'UTC','web','running',?,?,?)",
                       (now, self.work, now, now))
        self.assertEqual([row['id'] for row in self.service.preparations.due(time.time(), busy={self.work})], [])
        self.assertEqual([row['id'] for row in self.service.preparations.due(time.time())], ['p1'])


class StoreContention(unittest.TestCase):
    def test_database_is_locked_is_counted_and_raised(self):
        with tempfile.TemporaryDirectory() as root:
            store = QuickStore(root)
            before = QuickStore.locked_errors
            with self.assertRaises(sqlite3.OperationalError), self.assertLogs('personal_agent.quickstart_store', 'WARNING'):
                with store.db():
                    raise sqlite3.OperationalError('database is locked')
            self.assertEqual(QuickStore.locked_errors, before + 1)
            with self.assertRaises(sqlite3.OperationalError):
                with store.db():
                    raise sqlite3.OperationalError('no such table: fixture')
            self.assertEqual(QuickStore.locked_errors, before + 1)


if __name__ == '__main__':
    unittest.main()
