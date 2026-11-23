"""iMessage surface (#1141, tracker #1140): the owner's 1:1 iMessage conversation as an owner surface.

Model-free, Node-free and ``chat.db``-free.  ``FakeSidecar`` stands in for the
lane-B process transport: ``send_op(dict)`` records what Still writes to the
sidecar's stdin, and ``events()`` yields what the sidecar would have printed,
in the "Sidecar protocol v1" shapes of the #1140 Plan comment.  The fake
enforces nothing: these tests check that Still itself refuses what the
sidecar should already have filtered (defence in depth), and that Still owns
dedupe by ``guid``, pairing, generation and delivery state.

The owner-act judgment is scripted (``owner_act_script``); the collector, the
judgment's policy and every settle path run unchanged.
"""
import json
import logging
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from personal_agent import work_mode
from personal_agent.conversation_surface import SURFACE_NAMES, registered_surfaces, settle_unknown_deliveries
from personal_agent.decision import FixtureDecisionEngine
from personal_agent.imessage_surface import (CONFIG_KEY, DELIVERY_WINDOW_SECONDS, MAX_PAIRING_ATTEMPTS,
                                             PAIRING_SECONDS, REPLAY_OVERLAP_MS, IMessageSurface)
from personal_agent.messages import msg
from personal_agent.owner_acts import KIND_SETTINGS, KIND_STEP
from personal_agent.providers import ModelAdapter
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore
from owner_act_script import with_owner_acts

OWNER = '+821055550000'
INTRUDER = '+821099990000'
OTHER_SECRET = 'OTHER-CONVERSATION-7f3a91'
ANSWER = '저녁은 순두부찌개 어때요?'


READY = {'ev': 'ready', 'v': 1, 'kit': '3.0.0'}


def restart(sidecar, surface):
    """The sidecar's child exits and a new one becomes ready, one surface tick apart."""
    sidecar.ready = None
    surface.receive()
    sidecar.ready = dict(READY)


def now_ms():
    return int(time.time() * 1000)


class FakeSidecar:
    """What Still writes to the sidecar (``ops``) and what the sidecar prints back (``emit``)."""

    def __init__(self):
        self.ops = []
        self.queue = []
        self.down = False
        self.refusing = False    # as IMessageSidecar.send_op while stopped or restarting
        self.ready = dict(READY)  # IMessageSidecar.ready: None while stopped or restarting
        self._guid = 0

    def send_op(self, op):
        if self.down:
            raise BrokenPipeError('sidecar is not running')
        if self.refusing:
            return False
        self.ops.append(json.loads(json.dumps(op)))   # one JSON line, as on stdin
        return True

    def events(self):
        while self.queue:
            yield self.queue.pop(0)

    def emit(self, **event):
        self.queue.append(event)
        return event

    def guid(self):
        self._guid += 1
        return f'GUID-{self._guid:06d}'

    def message(self, handle, text, ts_ms=None, guid=None, reaction=None):
        return self.emit(ev='message', guid=guid or self.guid(), handle=handle, text=text,
                         ts_ms=now_ms() if ts_ms is None else ts_ms, reaction=reaction)

    def from_me(self, handle, text, ts_ms=None, guid=None, *, sent=True, delivered=True, error=None, flags=True):
        """A from-me row; protocol v1.1 (#1179) carries its ``sent``/``delivered``/``error`` flags."""
        event = dict(ev='from_me', guid=guid or self.guid(), handle=handle, text=text,
                     ts_ms=now_ms() if ts_ms is None else ts_ms)
        if flags:
            event.update(sent=sent, delivered=delivered, error=error)
        return self.emit(**event)

    def again(self, echo, **changes):
        """The same row re-observed after Messages changed its flags."""
        return self.emit(**{**echo, **changes})

    def sends(self):
        return [op for op in self.ops if op.get('op') == 'send']

    def scopes(self):
        return [op for op in self.ops if op.get('op') == 'scope']


def model(url, body, headers=None, timeout=60):
    tools = [t.get('function', {}).get('name') or t.get('name') for t in body.get('tools', [])]
    if 'agentos_connection_probe' in tools:
        return {'message': {'content': '', 'tool_calls': [
            {'id': 'probe', 'function': {'name': 'agentos_connection_probe', 'arguments': {}}}]}}
    return {'message': {'content': ANSWER}}


class IMessageCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = QuickStore(Path(temp.name) / 'data')
        self.sidecar = FakeSidecar()
        self.service = AgentService(self.store, ModelAdapter(model), lambda *a, **k: {'ok': True, 'result': True})
        self.service.imessage_transport = self.sidecar
        self.service.steer_spawn = lambda target: target()
        self.service.save_model({'provider': 'ollama', 'endpoint': 'http://127.0.0.1:11434', 'model': 'test-model',
                                 'api_key': ''})
        self.assertTrue(self.service.test_model()['ok'])
        self.surface = self.service.imessage_surface
        self.surface.reader_spawn = lambda target: target()   # FakeSidecar.events() returns once drained

    def pair(self, handle=OWNER):
        """Pair ``handle`` with a fresh code; returns the code message's ``ts_ms``."""
        code = self.surface.start_pairing()['code']
        ts = now_ms()
        self.sidecar.message(handle, code, ts_ms=ts)
        self.surface.receive()
        self.assertEqual(self.surface.config().get('handle'), handle)
        return ts

    def owner_says(self, text, handle=OWNER, **kwargs):
        event = self.sidecar.message(handle, text, **kwargs)
        return event, self.surface.receive()

    def im_jobs(self):
        return [job for job in self.store.jobs() if job['channel'].startswith('imessage:')]

    def sent_texts(self):
        return [op['text'] for op in self.sidecar.sends()]

    def answer_sends(self):
        return [op for op in self.sidecar.sends() if op['text'] == ANSWER]


class Registration(IMessageCase):
    def test_the_surface_is_registered_and_idle_until_paired(self):
        self.assertIn('imessage', SURFACE_NAMES)
        self.assertIn('imessage', [surface.name for surface in registered_surfaces(self.service)])
        self.assertIsInstance(self.surface, IMessageSurface)
        self.assertEqual(self.surface.channel_prefix, 'imessage:')
        self.assertIsNone(self.surface.pairing())
        self.assertIsNone(self.surface.receive())
        self.assertEqual(self.sidecar.sends(), [], 'nothing is sent before a pairing')

    def test_no_capability_that_the_protocol_cannot_carry(self):
        caps = self.surface.capabilities
        self.assertEqual((caps.typing, caps.live_drafts, caps.buttons, caps.native_stop, caps.action_approvals),
                         (False, False, False, False, False))

    def test_an_unconfigured_service_has_no_transport_and_sends_nothing(self):
        service = AgentService(QuickStore(Path(self.store.root).parent / 'other'))
        self.assertIsNone(service.imessage_surface.receive())
        self.assertFalse(service.imessage_surface.send_owner('hello'))


class Pairing(IMessageCase):
    def test_pairing_scopes_the_sidecar_then_pins_the_handle(self):
        started = self.surface.start_pairing()
        code = started['code']
        self.assertGreaterEqual(len(code), 6)
        self.assertGreater(started['expires_at'], time.time())
        pairing_scope = self.sidecar.scopes()[-1]
        self.assertEqual(pairing_scope['mode'], 'pairing')
        self.assertIsInstance(pairing_scope['since_ms'], int)
        self.assertLessEqual(pairing_scope['max_text_len'], 32)
        ts = now_ms()
        self.sidecar.message(OWNER, f'  {code}  ', ts_ms=ts)
        self.surface.receive()
        cfg = self.surface.config()
        self.assertEqual((cfg['enabled'], cfg['handle'], cfg['owner_user_id']), (True, OWNER, 'handle:' + OWNER))
        self.assertTrue(cfg['generation'])
        self.assertEqual(self.sidecar.scopes()[-1], {'op': 'scope', 'mode': 'paired', 'handle': OWNER, 'since_ms': ts})
        self.assertEqual(self.im_jobs(), [], 'the code message is never a request')
        self.assertTrue(all(op['to'] == OWNER for op in self.sidecar.sends()))
        self.assertNotIn(code, json.dumps(cfg), 'the code itself is not kept after use')

    def test_rows_after_the_code_in_the_same_drain_are_read_as_paired(self):
        code = self.surface.start_pairing()['code']
        ts = now_ms()
        self.sidecar.message(OWNER, code, ts_ms=ts)
        first = self.sidecar.message(OWNER, '첫 요청', ts_ms=ts + 1)   # same drain, right after the code
        self.sidecar.message(INTRUDER, '남의 대화', ts_ms=ts + 2)
        self.surface.receive()
        self.assertEqual(self.surface.config().get('handle'), OWNER)
        self.assertEqual(len(self.im_jobs()), 1, "the owner's first request is not dropped")
        self.sidecar.emit(**first)                                  # the paired backfill repeats it
        self.surface.receive()
        self.assertEqual(len(self.im_jobs()), 1)

    def test_status_follows_the_transport_readiness(self):
        self.pair()
        self.assertTrue(self.surface.status()['connected'])
        self.sidecar.ready = None
        self.assertEqual(self.surface.status()['state'], 'disconnected')
        self.sidecar.ready = dict(READY)
        self.sidecar.emit(ev='error', code='db_unreadable', detail='')
        self.surface.receive()
        self.assertEqual(self.surface.status()['state'], 'error')
        restart(self.sidecar, self.surface)
        self.surface.receive()
        self.assertEqual(self.surface.status()['state'], 'connected', 'a ready restart clears the error')

    def test_the_code_is_compared_in_constant_time(self):
        code = self.surface.start_pairing()['code']
        with mock.patch('personal_agent.imessage_surface.hmac.compare_digest', wraps=__import__('hmac').compare_digest) as compared:
            self.sidecar.message(OWNER, code)
            self.surface.receive()
        self.assertTrue(compared.called)
        self.assertEqual(self.surface.config().get('handle'), OWNER)

    def test_a_wrong_code_is_refused(self):
        code = self.surface.start_pairing()['code']
        self.sidecar.message(INTRUDER, 'WRONGCODE1')
        self.sidecar.message(INTRUDER, code.lower() + 'x')
        self.surface.receive()
        self.assertFalse(self.surface.config().get('handle'))
        self.assertIsNone(self.surface.pairing())
        self.assertEqual(self.im_jobs(), [])

    def test_an_expired_code_is_refused(self):
        started = self.surface.start_pairing(now=time.time())
        self.sidecar.message(OWNER, started['code'])
        self.surface.receive(now=started['expires_at'] + 1)
        self.assertFalse(self.surface.config().get('handle'))
        self.assertIsNone(self.surface.pairing())
        self.assertNotEqual(self.sidecar.scopes()[-1].get('mode'), 'pairing', 'an expired pairing stops reading')
        self.assertGreaterEqual(PAIRING_SECONDS, 60)

    def test_too_many_wrong_codes_end_the_pairing(self):
        code = self.surface.start_pairing()['code']
        for attempt in range(MAX_PAIRING_ATTEMPTS):
            self.sidecar.message(INTRUDER, f'BAD{attempt:05d}')
        self.sidecar.message(OWNER, code)   # the right code, after the attempts ran out
        self.surface.receive()
        self.assertFalse(self.surface.config().get('handle'))
        self.assertNotEqual(self.sidecar.scopes()[-1].get('mode'), 'pairing')

    def test_a_replayed_wrong_row_is_one_attempt(self):
        """A sidecar restart replays rows; one wrong row never uses up the pairing's attempts."""
        code = self.surface.start_pairing()['code']
        wrong = self.sidecar.message(INTRUDER, 'BADCODE1')
        for _ in range(MAX_PAIRING_ATTEMPTS + 1):
            self.sidecar.emit(**wrong)
            self.surface.receive()
        self.sidecar.message(OWNER, code)
        self.surface.receive()
        self.assertEqual(self.surface.config().get('handle'), OWNER)

    def test_a_code_from_a_used_pairing_cannot_pair_again(self):
        code = self.surface.start_pairing()['code']
        self.sidecar.message(OWNER, code)
        self.surface.receive()
        generation = self.surface.config()['generation']
        self.sidecar.message(INTRUDER, code)
        self.surface.receive()
        self.assertEqual((self.surface.config()['handle'], self.surface.config()['generation']),
                         (OWNER, generation))

    def test_repairing_bumps_the_generation_and_drops_old_replies(self):
        self.pair()
        old = self.surface.config()['generation']
        self.owner_says('질문')
        self.assertTrue(self.service.run_one())
        work = self.im_jobs()[0]
        self.pair()
        self.assertNotEqual(self.surface.config()['generation'], old)
        self.service.deliver_one()
        self.assertEqual(self.answer_sends(), [], 'a reply of the old generation is never sent')
        self.assertNotIn(self.store.job(work['id'])['delivery'], ('pending', 'sending', 'sent'))

    def test_repairing_another_handle_never_retargets_a_reply(self):
        self.pair()
        self.owner_says('질문')
        self.assertTrue(self.service.run_one())
        self.pair(INTRUDER)
        self.service.deliver_one()
        self.assertEqual(self.answer_sends(), [])
        self.owner_says('예전 번호에서', handle=OWNER)
        self.assertEqual(len(self.im_jobs()), 1, 'the previous handle is no longer read')

    def test_a_ready_sidecar_is_told_the_current_scope(self):
        """The sidecar keeps no state: after it (re)starts, Still repeats the scope it must enforce."""
        since = self.pair()
        self.sidecar.ops.clear()
        restart(self.sidecar, self.surface)
        self.surface.receive()
        scope = self.sidecar.scopes()[-1]
        self.assertEqual((scope['mode'], scope['handle']), ('paired', OWNER))
        self.assertGreaterEqual(scope['since_ms'], since)

    def test_a_restarted_sidecar_resumes_near_the_newest_row_not_the_pairing(self):
        since = self.pair()
        later = since + 3 * REPLAY_OVERLAP_MS
        self.owner_says('나중 메시지', ts_ms=later)
        restart(self.sidecar, self.surface)
        self.surface.receive()
        self.assertEqual(self.sidecar.scopes()[-1]['since_ms'], later - REPLAY_OVERLAP_MS)
        self.sidecar.message(OWNER, '겹치는 구간의 옛 메시지', ts_ms=later - 1000)
        self.surface.receive()
        self.assertEqual(len(self.im_jobs()), 2, 'a row in the overlap that was never seen is still read')

    def test_an_unpaired_ready_sidecar_reads_nothing(self):
        restart(self.sidecar, self.surface)
        self.surface.receive()
        self.assertEqual({op['mode'] for op in self.sidecar.scopes()}, {'off'})

    def test_disconnect_turns_reading_off(self):
        self.pair()
        self.surface.disconnect()
        self.assertEqual(self.sidecar.scopes()[-1], {'op': 'scope', 'mode': 'off'})
        self.assertIsNone(self.surface.pairing())
        self.owner_says('연결 끊긴 뒤')
        self.assertEqual(self.im_jobs(), [])


class Ingest(IMessageCase):
    def setUp(self):
        super().setUp()
        self.since = self.pair()

    def test_an_owner_message_becomes_one_work_and_its_reply_goes_to_the_handle(self):
        event, created = self.owner_says('오늘 저녁 뭐 먹을까?')
        jobs = self.im_jobs()
        self.assertEqual([(job['message'], job['chat_id'], job['owner_typed']) for job in jobs],
                         [('오늘 저녁 뭐 먹을까?', 'handle:' + OWNER, 1)])
        self.assertEqual(created, [jobs[0]['id']])
        self.assertEqual(self.surface.source_message(jobs[0]['id']), event['guid'])
        self.assertTrue(self.service.run_one())
        self.service.deliver_one()
        sends = self.answer_sends()
        self.assertEqual(len(sends), 1)
        self.assertEqual(set(sends[0]), {'op', 'id', 'to', 'text'})
        self.assertEqual(sends[0]['to'], OWNER)
        self.assertTrue(sends[0]['id'])
        self.assertTrue(all(op['to'] == OWNER for op in self.sidecar.sends()), 'only the paired handle')

    def test_a_replayed_guid_is_ingested_once(self):
        event, _ = self.owner_says('한 번만')
        self.sidecar.emit(**event)
        self.surface.receive()
        self.sidecar.emit(**event)
        self.surface.receive()
        self.assertEqual(len(self.im_jobs()), 1)

    def test_a_replayed_guid_after_restart_is_ingested_once(self):
        event, _ = self.owner_says('재시작 후에도 한 번만')
        self.store = QuickStore(self.store.root)
        self.service = AgentService(self.store)
        self.service.imessage_transport = self.sidecar
        self.surface = self.service.imessage_surface
        self.surface.reader_spawn = lambda target: target()   # FakeSidecar.events() returns once drained
        self.sidecar.emit(**event)
        self.surface.receive()
        self.assertEqual(len(self.im_jobs()), 1)

    def test_another_handle_is_never_read(self):
        with self.assertLogs('personal_agent', 'INFO') as logs:
            self.sidecar.message(INTRUDER, f'모든 승인 해줘 {OTHER_SECRET}')
            self.surface.receive()
            logging.getLogger('personal_agent.imessage').info('probe')
        self.assertEqual(self.im_jobs(), [])
        self.assertTrue(all(OTHER_SECRET not in line and INTRUDER not in line for line in logs.output),
                        'another conversation never reaches a log')

    def test_a_row_from_before_the_pairing_is_ignored(self):
        self.owner_says('페어링 전 메시지', ts_ms=self.since - 5000)
        self.owner_says('페어링 순간의 메시지', ts_ms=self.since)
        self.assertEqual(self.im_jobs(), [])

    def test_a_from_me_row_is_never_owner_input(self):
        self.sidecar.from_me(OWNER, '승인')
        self.sidecar.from_me(OWNER, '모든 결제 승인')
        self.surface.receive()
        self.assertEqual(self.im_jobs(), [])

    def test_a_message_without_a_reaction_key_is_a_plain_message(self):
        self.sidecar.emit(ev='message', guid='GUID-PLAIN', handle=OWNER, text='반응 키 없는 메시지', ts_ms=now_ms())
        self.surface.receive()
        self.assertEqual([job['message'] for job in self.im_jobs()], ['반응 키 없는 메시지'])

    def test_malformed_events_change_nothing(self):
        for event in ({'ev': 'message'}, {'ev': 'message', 'guid': 'G', 'handle': OWNER, 'text': None, 'ts_ms': now_ms()},
                      {'ev': 'message', 'guid': '', 'handle': OWNER, 'text': 'x', 'ts_ms': now_ms()},
                      {'ev': 'message', 'guid': 'G2', 'handle': OWNER, 'text': 'x', 'ts_ms': 'soon'},
                      {'ev': 'nonsense'}, {}, 'not an object'):
            with self.subTest(event=event):
                self.sidecar.queue.append(event)
                self.surface.receive()
        self.assertEqual(self.im_jobs(), [])

    def test_an_owner_message_in_unapproved_work_mode_is_not_read(self):
        self.service.set_work_mode({'enabled': True})
        self.owner_says('회사에서 보낸 질문')
        self.assertEqual(self.im_jobs(), [])


class Delivery(IMessageCase):
    def setUp(self):
        super().setUp()
        self.pair()
        self.owner_says('질문')
        self.work = self.im_jobs()[0]
        self.assertTrue(self.service.run_one())

    def delivery(self):
        return self.store.job(self.work['id'])['delivery']

    def test_sent_is_only_sending_and_the_echo_marks_delivered(self):
        self.service.deliver_one()
        send = self.answer_sends()[0]
        self.assertEqual(self.delivery(), 'sending', 'handed to the sidecar is not delivered')
        self.sidecar.emit(ev='sent', id=send['id'])
        self.surface.receive()
        self.assertEqual(self.delivery(), 'sending', 'osascript exiting is not delivered')
        echo = self.sidecar.from_me(OWNER, ANSWER)
        self.surface.receive()
        self.assertEqual(self.delivery(), 'sent')
        self.assertEqual(self.surface.reply_message(self.work['id'], 'handle:' + OWNER), echo['guid'])
        self.assertEqual(len(self.im_jobs()), 1, 'the echo is not ingested')

    def watch_notice(self):
        """A watch-result notice (``preparation_notify``) handed to the sidecar; returns its row and the spy."""
        from personal_agent import preparations as prep
        cfg = self.surface.config()
        queued = self.store.queue_notification(self.work['id'], cfg['owner_user_id'], cfg['generation'],
                                                        prep.NOTIFY_KIND, 'digest-1', surface='imessage')
        recorded = mock.patch.object(self.service.preparations, 'record_notified').start()
        self.addCleanup(mock.patch.stopall)
        mock.patch('personal_agent.direct_message_surface.notice_binding', return_value={}).start()
        mock.patch('personal_agent.direct_message_surface.notice_text', return_value='watch result').start()
        while self.service.deliver_notification():
            pass
        return self.store.notification(queued['id']), recorded

    def test_a_watch_notice_is_recorded_as_told_only_when_its_echo_arrives(self):
        notice, recorded = self.watch_notice()
        self.assertEqual(self.sidecar.sends()[-1]['text'], 'watch result')
        recorded.assert_not_called()
        self.assertEqual((notice['state'], notice['sent_at'], notice['message_id']), ('sending', None, None),
                         'handed to the sidecar is not told')
        echo = self.sidecar.from_me(OWNER, 'watch result')
        self.surface.receive()
        self.assertEqual(recorded.call_count, 1)
        self.assertEqual(recorded.call_args.args[:2], (self.work['id'], 'digest-1'))
        told = self.store.notification(notice['id'])
        self.assertEqual((told['state'], told['message_id']), ('sent', echo['guid']))
        self.assertIsNotNone(told['sent_at'])

    def test_a_failed_watch_notice_is_queued_again_and_told_once_it_echoes(self):
        notice, recorded = self.watch_notice()
        self.sidecar.emit(ev='send_failed', id=self.sidecar.sends()[-1]['id'], reason='osascript')
        self.surface.receive()
        self.assertEqual(self.store.notification(notice['id'])['state'], 'queued', 'not told, so retried')
        recorded.assert_not_called()
        while self.service.deliver_notification():
            pass
        self.assertEqual([op['text'] for op in self.sidecar.sends()].count('watch result'), 2)
        self.sidecar.from_me(OWNER, 'watch result')
        self.surface.receive()
        self.assertEqual(self.store.notification(notice['id'])['state'], 'sent')
        self.assertEqual(recorded.call_count, 1)

    def test_a_notice_that_keeps_failing_stops_at_unknown(self):
        from personal_agent.imessage_surface import IMessageSurface
        notice, recorded = self.watch_notice()
        for _ in range(IMessageSurface.MAX_NOTICE_SEND_FAILURES):
            self.sidecar.emit(ev='send_failed', id=self.sidecar.sends()[-1]['id'], reason='osascript')
            self.surface.receive()
            while self.service.deliver_notification():
                pass
        self.assertEqual(self.store.notification(notice['id'])['state'], 'unknown')
        self.assertEqual([op['text'] for op in self.sidecar.sends()].count('watch result'),
                         IMessageSurface.MAX_NOTICE_SEND_FAILURES)
        recorded.assert_not_called()

    def test_a_watch_notice_without_its_echo_is_never_recorded_as_told(self):
        notice, recorded = self.watch_notice()
        self.surface.receive(now=time.time() + DELIVERY_WINDOW_SECONDS + 5)
        self.assertEqual(self.store.notification(notice['id'])['state'], 'unknown')
        self.sidecar.from_me(OWNER, 'watch result')      # a late echo proves nothing
        self.surface.receive()
        recorded.assert_not_called()

    def test_without_the_echo_the_send_becomes_unknown_and_is_never_resent(self):
        self.service.deliver_one()
        send = self.answer_sends()[0]
        self.sidecar.emit(ev='sent', id=send['id'])
        self.surface.receive(now=time.time() + DELIVERY_WINDOW_SECONDS + 5)
        self.assertEqual(self.delivery(), 'unknown')
        self.service.deliver_one()
        self.assertEqual(len(self.answer_sends()), 1)

    def test_a_late_echo_does_not_claim_delivery_for_an_unknown_send(self):
        self.service.deliver_one()
        self.surface.receive(now=time.time() + DELIVERY_WINDOW_SECONDS + 5)
        self.sidecar.from_me(OWNER, ANSWER)
        self.surface.receive(now=time.time() + DELIVERY_WINDOW_SECONDS + 10)
        self.assertEqual(self.delivery(), 'unknown')

    def test_an_echo_of_other_text_or_another_handle_proves_nothing(self):
        self.service.deliver_one()
        self.sidecar.from_me(OWNER, '다른 내용')
        self.sidecar.from_me(INTRUDER, ANSWER)
        self.surface.receive()
        self.assertEqual(self.delivery(), 'sending')

    def test_a_failed_send_is_unknown_and_never_resent(self):
        self.service.deliver_one()
        send = self.answer_sends()[0]
        self.sidecar.emit(ev='send_failed', id=send['id'], reason='osascript exited 1')
        self.surface.receive()
        self.assertEqual(self.delivery(), 'unknown')
        self.service.deliver_one()
        self.assertEqual(len(self.answer_sends()), 1)

    # -- #1179: Messages writes a from-me row for a failed send too; its flags decide -------
    def send_states(self):
        with self.store.db() as db:
            return [row['state'] for row in db.execute('SELECT state FROM imessage_sends ORDER BY created,rowid')]

    def test_an_echo_with_an_error_is_failed_never_delivered(self):
        self.service.deliver_one()
        self.sidecar.from_me(OWNER, ANSWER, sent=False, delivered=False, error=22)
        self.surface.receive()
        self.assertEqual(self.send_states()[-1], 'failed')
        self.assertEqual(self.delivery(), 'unknown', 'a failed reply is never recorded as delivered')
        self.assertIsNone(self.surface.reply_message(self.work['id'], 'handle:' + OWNER))
        self.service.deliver_one()
        self.assertEqual(len(self.answer_sends()), 1, 'Messages may still retry it, so Still never sends it twice')

    def test_a_watch_notice_whose_echo_has_an_error_is_not_told_and_is_queued_again(self):
        notice, recorded = self.watch_notice()
        self.sidecar.from_me(OWNER, 'watch result', sent=True, delivered=False, error=22)
        self.surface.receive()
        self.assertEqual(self.store.notification(notice['id'])['state'], 'queued')
        recorded.assert_not_called()

    def test_a_sent_echo_stays_sending_and_its_later_delivery_marks_delivered(self):
        self.service.deliver_one()
        echo = self.sidecar.from_me(OWNER, ANSWER, sent=True, delivered=False)
        self.surface.receive()
        self.assertEqual(self.delivery(), 'sending', 'sent without a delivery receipt is not delivered')
        self.sidecar.again(echo, delivered=True)
        self.surface.receive()
        self.assertEqual(self.delivery(), 'sent')
        self.assertEqual(self.send_states()[-1], 'delivered')
        self.assertEqual(self.surface.reply_message(self.work['id'], 'handle:' + OWNER), echo['guid'])

    def test_a_sent_echo_that_later_fails_is_failed(self):
        notice, recorded = self.watch_notice()
        echo = self.sidecar.from_me(OWNER, 'watch result', sent=True, delivered=False)
        self.surface.receive()
        self.assertEqual(self.store.notification(notice['id'])['state'], 'sending')
        self.sidecar.again(echo, error=22)
        self.surface.receive()
        self.assertEqual(self.send_states()[-1], 'failed')
        self.assertEqual(self.store.notification(notice['id'])['state'], 'queued')
        recorded.assert_not_called()

    def test_a_late_failure_never_overrides_delivered(self):
        notice, recorded = self.watch_notice()
        echo = self.sidecar.from_me(OWNER, 'watch result', sent=True, delivered=True)
        self.surface.receive()
        self.sidecar.again(echo, error=22)
        self.surface.receive()
        self.assertEqual(self.send_states()[-1], 'delivered')
        self.assertEqual(self.store.notification(notice['id'])['state'], 'sent')
        self.assertEqual(recorded.call_count, 1)
        self.assertEqual(self.surface.status()['send_failure'], '')

    def test_a_sent_echo_without_a_delivery_receipt_becomes_unknown_after_the_window(self):
        self.service.deliver_one()
        self.sidecar.from_me(OWNER, ANSWER, sent=True, delivered=False)
        self.surface.receive()
        self.assertEqual(self.delivery(), 'sending')
        self.surface.receive(now=time.time() + DELIVERY_WINDOW_SECONDS + 5)
        self.assertEqual(self.delivery(), 'unknown')
        self.assertEqual(self.send_states()[-1], 'unknown')

    def test_an_error_after_the_window_still_shows_the_failure(self):
        # The sidecar keeps re-reading a row for longer than the surface waits: an error that Messages
        # sets after the window is still the truth about that send (review of #1179).
        self.service.deliver_one()
        echo = self.sidecar.from_me(OWNER, ANSWER, sent=True, delivered=False)
        self.surface.receive()
        later = time.time() + DELIVERY_WINDOW_SECONDS + 5
        self.surface.receive(now=later)
        self.assertEqual(self.send_states()[-1], 'unknown')
        self.sidecar.again(echo, error=22)
        self.surface.receive(now=later + 1)
        self.assertEqual(self.send_states()[-1], 'failed')
        self.assertEqual(self.delivery(), 'unknown', 'still never recorded as delivered, and never resent')
        self.assertEqual(self.surface.status()['send_failure'], msg('imessage.send_not_delivered'))
        self.service.deliver_one()
        self.assertEqual(len(self.answer_sends()), 1)

    def test_a_late_delivery_receipt_does_not_reopen_an_unknown_send(self):
        self.service.deliver_one()
        echo = self.sidecar.from_me(OWNER, ANSWER, sent=True, delivered=False)
        self.surface.receive()
        later = time.time() + DELIVERY_WINDOW_SECONDS + 5
        self.surface.receive(now=later)
        self.sidecar.again(echo, delivered=True)
        self.surface.receive(now=later + 1)
        self.assertEqual((self.send_states()[-1], self.delivery()), ('unknown', 'unknown'))

    def test_a_failure_of_an_earlier_pairing_is_not_shown_after_repairing_or_disconnect(self):
        self.service.deliver_one()
        self.sidecar.from_me(OWNER, ANSWER, sent=False, delivered=False, error=22)
        self.surface.receive()
        self.assertTrue(self.surface.status()['send_failure'])
        self.pair()
        self.assertEqual(self.surface.status()['send_failure'], '', 'a new pairing starts without the old failure')
        self.service.deliver_one()
        self.sidecar.from_me(OWNER, msg('imessage.connected_hello'), sent=False, delivered=False, error=22)
        self.surface.receive()
        self.assertTrue(self.surface.status()['send_failure'])
        self.surface.disconnect()
        self.assertEqual(self.surface.status()['send_failure'], '')

    def test_an_echo_without_delivery_flags_proves_nothing(self):
        self.service.deliver_one()
        self.sidecar.from_me(OWNER, ANSWER, flags=False)
        self.surface.receive()
        self.assertEqual(self.delivery(), 'sending')

    def test_settings_names_the_last_send_failure_in_plain_words(self):
        self.service.deliver_one()
        self.assertEqual(self.surface.status()['send_failure'], '')
        self.sidecar.from_me(OWNER, ANSWER, sent=False, delivered=False, error=22)
        self.surface.receive()
        shown = self.surface.status()['send_failure']
        self.assertEqual(shown, msg('imessage.send_not_delivered'))
        self.assertNotIn('22', shown, 'no raw error code')
        self.assertEqual(self.service.settings()['imessage']['send_failure'], shown)
        # A later send that Messages delivers clears it.
        self.owner_says('다음 질문')
        self.assertTrue(self.service.run_one())
        self.service.deliver_one()
        self.sidecar.from_me(OWNER, ANSWER, sent=True, delivered=True)
        self.surface.receive()
        self.assertEqual(self.surface.status()['send_failure'], '')

    def test_a_sidecar_that_is_down_leaves_the_reply_pending_then_sends_it_once(self):
        self.sidecar.down = True
        self.service.deliver_one()
        self.assertEqual(self.delivery(), 'pending')
        self.assertEqual(self.answer_sends(), [])
        self.sidecar.down = False
        self.service.deliver_one()
        self.assertEqual(len(self.answer_sends()), 1)
        self.assertEqual(self.delivery(), 'sending')

    def test_a_sidecar_that_refuses_the_send_leaves_the_reply_pending_then_sends_it_once(self):
        self.sidecar.refusing = True
        self.service.deliver_one()
        self.assertEqual(self.delivery(), 'pending', 'a refused send is not in flight')
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM imessage_sends WHERE job_id=?',
                                        (self.work['id'],)).fetchone()[0], 0)
        self.sidecar.refusing = False
        self.service.deliver_one()
        self.assertEqual(len(self.answer_sends()), 1)
        self.assertEqual(self.delivery(), 'sending')

    def test_a_refused_scope_is_sent_again_on_the_next_tick(self):
        scopes = len(self.sidecar.scopes())
        self.surface._scope = None
        self.sidecar.refusing = True
        self.surface.receive()
        self.assertEqual(len(self.sidecar.scopes()), scopes, 'refused, so not recorded by the fake')
        self.assertIsNone(self.surface._scope, 'a refused scope is not cached as installed')
        self.sidecar.refusing = False
        self.surface.receive()
        self.assertEqual(self.sidecar.scopes()[-1]['mode'], 'paired')

    def test_an_in_flight_send_across_a_restart_is_unknown(self):
        self.service.deliver_one()
        store = QuickStore(self.store.root)
        service = AgentService(store)
        service.imessage_transport = self.sidecar
        service.recover_interrupted_work()
        self.assertEqual(store.job(self.work['id'])['delivery'], 'unknown')
        self.sidecar.from_me(OWNER, ANSWER)
        service.imessage_surface.receive()
        self.assertEqual(store.job(self.work['id'])['delivery'], 'unknown', 'a late echo never upgrades it')

    def test_another_surface_never_touches_an_imessage_reply(self):
        self.service.telegram_surface.deliver_one()
        self.service.mattermost_surface.deliver_reply()
        self.assertEqual(self.delivery(), 'pending')


class OwnerActs(IMessageCase):
    def setUp(self):
        super().setUp()
        self.pair()
        self.service.use_decision_engine(with_owner_acts(FixtureDecisionEngine()))

    def settings_notice(self):
        self.owner_says('시간대를 도쿄로 바꿔줘')
        job = self.im_jobs()[0]
        settings = self.service.settings_orchestrator
        draft = settings.propose(self.service.settings_owner(job), job['channel'], 'current_context', 'timezone',
                                 'Asia/Tokyo', work_id=job['id'])
        self.service.queue_settings_confirmation(job)
        while self.service.deliver_notification():
            pass
        notice = self.store.notification_of(job['id'], 'settings_change_proposed')
        return job, draft['draft_id'], notice

    def test_a_typed_approval_settles_once_through_owner_acts(self):
        job, draft_id, notice = self.settings_notice()
        self.assertEqual(notice['surface'], 'imessage')
        self.assertTrue(any(op['to'] == OWNER for op in self.sidecar.sends()))
        self.sidecar.from_me(OWNER, self.sidecar.sends()[-1]['text'])
        self.surface.receive()
        self.assertEqual([item['kind'] for item in self.service.owner_acts.open_items(job)], [KIND_SETTINGS])
        with mock.patch.object(self.service.owner_acts, 'settle', wraps=self.service.owner_acts.settle) as settled:
            event, _ = self.owner_says('승인')
            while self.service.run_one():
                pass
            self.sidecar.emit(**event)          # the same row again
            self.surface.receive()
            while self.service.run_one():
                pass
        self.assertEqual(self.service.settings_orchestrator._drafts()[draft_id]['state'], 'applied')
        self.assertEqual(settled.call_count, 1)

    def test_an_unconfirmed_prompt_is_unknown_and_never_offered_again(self):
        job, draft_id, notice = self.settings_notice()
        prompts = len(self.sidecar.sends())
        self.surface.receive(now=time.time() + DELIVERY_WINDOW_SECONDS + 5)
        self.assertEqual(self.store.notification(notice['id'])['state'], 'unknown')
        settle_unknown_deliveries(self.service)
        while self.service.deliver_notification():
            pass
        self.assertEqual(self.store.notification(notice['id'])['state'], 'unknown', 'not re-queued')
        self.assertNotIn(self.sidecar.sends()[prompts - 1]['text'],
                         [op['text'] for op in self.sidecar.sends()[prompts:]], 'the prompt is not sent twice')

    def test_a_prompt_in_flight_across_a_restart_is_unknown(self):
        job, draft_id, notice = self.settings_notice()
        self.assertEqual(self.store.notification(notice['id'])['state'], 'sending', 'no echo yet')
        store = QuickStore(self.store.root)
        service = AgentService(store)
        service.recover_interrupted_work()
        self.assertEqual(store.notification(notice['id'])['state'], 'unknown')

    def test_a_typed_approval_from_another_handle_settles_nothing(self):
        job, draft_id, notice = self.settings_notice()
        self.owner_says('승인', handle=INTRUDER)
        while self.service.run_one():
            pass
        self.assertEqual(self.service.settings_orchestrator._drafts()[draft_id]['state'], 'awaiting-confirmation')

    def test_an_echo_of_an_approval_word_settles_nothing(self):
        job, draft_id, notice = self.settings_notice()
        self.sidecar.from_me(OWNER, '승인')
        self.surface.receive()
        while self.service.run_one():
            pass
        self.assertEqual(self.service.settings_orchestrator._drafts()[draft_id]['state'], 'awaiting-confirmation')

    def test_a_tapback_on_the_notice_is_read_as_an_owner_act_once(self):
        job, draft_id, notice = self.settings_notice()
        prompt = self.sidecar.sends()[-1]
        echo = self.sidecar.from_me(OWNER, prompt['text'])
        self.surface.receive()
        tapback = self.sidecar.message(OWNER, 'Liked a message', reaction={'kind': 'like', 'target_guid': echo['guid'],
                                                                           'removed': False})
        with mock.patch.object(self.service.owner_acts, 'answer_reaction',
                               wraps=self.service.owner_acts.answer_reaction) as answered:
            self.surface.receive()
            self.sidecar.emit(**tapback)
            self.surface.receive()
        self.assertEqual(answered.call_count, 1)
        self.assertEqual(self.service.settings_orchestrator._drafts()[draft_id]['state'], 'applied')
        self.assertEqual(len(self.im_jobs()), 1, 'a tapback is never a typed request')

    def test_a_tapback_act_interrupted_by_a_stop_is_settled_after_the_restart(self):
        job, draft_id, notice = self.settings_notice()
        echo = self.sidecar.from_me(OWNER, self.sidecar.sends()[-1]['text'])
        self.surface.receive()
        self.service.steer_spawn = lambda target: None          # Still stops before the worker runs
        self.sidecar.message(OWNER, 'Liked a message', reaction={'kind': 'like', 'target_guid': echo['guid'],
                                                                 'removed': False})
        self.surface.receive()
        self.assertNotEqual(self.service.settings_orchestrator._drafts()[draft_id]['state'], 'applied')
        service = AgentService(self.store, ModelAdapter(model), lambda *a, **k: {'ok': True, 'result': True})
        service.use_decision_engine(with_owner_acts(FixtureDecisionEngine()))
        service.imessage_transport = self.sidecar
        service.steer_spawn = lambda target: target()
        surface = service.imessage_surface
        surface.reader_spawn = lambda target: target()
        with mock.patch.object(service.owner_acts, 'answer_reaction', wraps=service.owner_acts.answer_reaction) as answered:
            surface.receive()
            surface.receive()
        self.assertEqual(answered.call_count, 1)
        self.assertEqual(service.settings_orchestrator._drafts()[draft_id]['state'], 'applied')
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM imessage_pending_acts').fetchone()[0], 0)

    def test_a_removed_tapback_is_no_act(self):
        job, draft_id, notice = self.settings_notice()
        with mock.patch.object(self.service.owner_acts, 'answer_reaction') as answered:
            self.sidecar.message(OWNER, 'Removed a like', reaction={'kind': 'like', 'target_guid': 'GUID-X',
                                                                     'removed': True})
            self.surface.receive()
        answered.assert_not_called()
        self.assertEqual(len(self.im_jobs()), 1)

    def test_an_action_approval_is_approved_at_the_mac(self):
        self.owner_says('결제해줘')
        job = self.im_jobs()[0]
        row = self.store.queue_notification(job['id'], 'handle:' + OWNER, self.surface.owner_generation(), 'browser_approval_needed',
                                            'offer-1', surface='imessage')
        with mock.patch.object(type(self.service), '_browser_request',
                               lambda service, work_id: {'state': 'requested', 'offer_digest': 'offer-1',
                                                         'requested_at': time.time()}), \
                mock.patch.object(type(self.service), 'browser_step_prompt', lambda service, work_id: '결제 승인 요청'):
            self.service.deliver_notification()
            prompt = self.sidecar.sends()[-1]['text']
            self.assertIn(msg('imessage.approve_at_mac'), prompt)
            echo = self.sidecar.from_me(OWNER, prompt)
            self.surface.receive()
            sent = self.store.notification(row['id'])
            self.assertEqual(sent['message_id'], echo['guid'], 'the echo names the prompt')
            items = self.service.owner_acts.open_items(job, target=sent['message_id'])
        self.assertNotIn(KIND_STEP, [item['kind'] for item in items])


class Privacy(IMessageCase):
    def test_codes_other_conversations_and_sidecar_details_stay_out_of_logs_and_records(self):
        with self.assertLogs('personal_agent', level=logging.DEBUG) as logs:
            code = self.surface.start_pairing()['code']
            self.sidecar.message(INTRUDER, f'{OTHER_SECRET[:20]}')
            self.sidecar.message(OWNER, code)
            self.surface.receive()
            self.sidecar.message(INTRUDER, f'계좌 {OTHER_SECRET}')
            self.sidecar.emit(ev='error', code='db_unreadable', detail=f'/Users/x/Library/Messages/chat.db {OTHER_SECRET}')
            self.sidecar.emit(ev='send_failed', id='nope', reason=OTHER_SECRET)
            self.surface.receive()
            logging.getLogger('personal_agent.imessage').info('probe')
        self.assertTrue(all(OTHER_SECRET[:20] not in line and code not in line for line in logs.output))
        with self.store.db() as db:
            tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            dumped = ' '.join(str(tuple(row)) for table in tables for row in db.execute(f'SELECT * FROM "{table}"'))
        self.assertNotIn(OTHER_SECRET[:20], dumped)
        self.assertNotIn(INTRUDER, dumped)
        self.assertNotIn(code, dumped)
        status = self.surface.status()
        self.assertNotIn(OTHER_SECRET[:20], json.dumps(status))

    def test_a_sidecar_error_is_shown_by_its_code(self):
        self.pair()
        self.sidecar.emit(ev='error', code='no_disk_access', detail='/private/path')
        self.surface.receive()
        status = self.surface.status()
        self.assertEqual(status['state'], 'error')
        self.assertEqual(status.get('code'), 'no_disk_access')
        self.assertNotIn('/private/path', json.dumps(status))


class WorkMode(IMessageCase):
    def setUp(self):
        super().setUp()
        self.pair()

    def test_an_unapproved_surface_is_paused_and_sends_nothing_later(self):
        self.owner_says('질문')
        self.assertTrue(self.service.run_one())
        self.service.set_work_mode({'enabled': True})
        self.assertTrue(work_mode.active(self.store))
        self.assertIsNone(self.surface.pairing())
        self.service.deliver_one()
        self.service.set_work_mode({'enabled': False})
        self.service.deliver_one()
        self.assertEqual(self.answer_sends(), [], 'nothing queued while it was on is sent later')


if __name__ == '__main__':
    unittest.main()


class RealSidecarTransport(unittest.TestCase):
    """The merged #1142 ``IMessageSidecar`` (blocking ``events()``) over a fake child process."""

    def setUp(self):
        from test_imessage_sidecar import FakePopen, launcher
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = QuickStore(Path(temp.name) / 'data')
        self.service = AgentService(self.store, ModelAdapter(model), lambda *a, **k: {'ok': True, 'result': True})
        self.popen = FakePopen()
        self.sidecar = launcher(popen=self.popen)
        self.sidecar.start()
        self.addCleanup(self.sidecar.stop)
        self.service.imessage_transport = self.sidecar
        self.surface = self.service.imessage_surface
        self.child = self.popen.processes[-1]

    def receive_until(self, check, seconds=5):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            started = time.monotonic()
            self.surface.receive()
            self.assertLess(time.monotonic() - started, 1, 'receive() returns while the sidecar is idle')
            if check():
                return
            time.sleep(0.02)
        self.fail('the sidecar event never reached the surface')

    def pair(self):
        code = self.surface.start_pairing()['code']
        self.surface.receive()
        self.assertEqual([op['mode'] for op in self.child.ops if op.get('op') == 'scope'][-1], 'pairing')
        self.child.stdout.push({'ev': 'message', 'guid': 'G-1', 'handle': OWNER, 'text': code, 'ts_ms': now_ms(),
                                'reaction': None})
        self.receive_until(lambda: self.surface.config().get('handle') == OWNER)

    def test_an_idle_sidecar_never_holds_the_service_lock(self):
        started = time.monotonic()
        self.surface.receive()
        self.assertLess(time.monotonic() - started, 1)
        self.assertTrue(self.service.lock.acquire(timeout=1), 'receive() released the service lock')
        self.service.lock.release()

    def test_pairing_and_a_send_go_through_the_real_sidecar(self):
        self.pair()
        self.assertTrue(any(op.get('op') == 'send' and op['to'] == OWNER for op in self.child.ops), 'hello sent')

    def test_a_stopped_sidecar_refuses_the_send_and_nothing_is_in_flight(self):
        self.pair()
        with self.store.db() as db:
            before = db.execute('SELECT COUNT(*) FROM imessage_sends').fetchone()[0]
        self.sidecar.stop()
        with self.assertRaises(self.surface.NOT_SENT):
            self.surface._post(self.surface.config(), 'after the stop')
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM imessage_sends').fetchone()[0], before,
                             'a refused send leaves no in-flight row')

    def test_a_failed_greeting_through_the_real_sidecar_is_failed_not_delivered(self):
        # The 2026-11-24 live check (#1179): the greeting's from-me row appeared, but Messages could not deliver it.
        self.pair()
        greeting = [op for op in self.child.ops if op.get('op') == 'send'][-1]
        self.child.stdout.push({'ev': 'sent', 'id': greeting['id']})
        echo = {'ev': 'from_me', 'guid': 'G-2', 'handle': OWNER, 'text': greeting['text'], 'ts_ms': now_ms(),
                'sent': False, 'delivered': False, 'error': None}
        self.child.stdout.push(echo)
        self.child.stdout.push({**echo, 'error': 22})

        def failed():
            with self.store.db() as db:
                return [row['state'] for row in db.execute('SELECT state FROM imessage_sends')] == ['failed']
        self.receive_until(failed)
        self.assertEqual(self.surface.status()['send_failure'], msg('imessage.send_not_delivered'))

    def test_the_real_sidecar_reports_connected_and_disconnected(self):
        self.surface.receive()
        status = self.surface.status()
        self.assertEqual((status['connected'], status['state']), (True, 'connected'))
        self.sidecar.stop()
        self.surface.receive()
        self.assertEqual(self.surface.status()['state'], 'disconnected')
