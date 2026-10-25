"""#213 (part A): a second conversation surface is composed beside Telegram without either touching the other's rows.

Before a Mattermost surface lands, every path that read Telegram's pairing or
claimed any pending row goes through the surface: delivery claims, connector
owner identity, the owner-pairing guards, owner acts and owner notices.  A
fake second surface (``side:`` channels, string owner ids, as Mattermost's
are) stands in for it.  Evidence class: automated synthetic/fixture only; no
Telegram or Mattermost call is made.
"""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from personal_agent import api_input, work_mode
from personal_agent.conversation_surface import ConversationSurface, SurfacePairing
from personal_agent.providers import ModelAdapter
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore

CHAT = 4242
GENERATION = 'g1'
SIDE_OWNER = 'u5mf3k8xq7rbtcd1ny9ewz4ahe'


class SideSurface(ConversationSurface):
    """A second surface: string owner ids, its own channel prefix, every send recorded."""

    name = 'side'
    channel_prefix = 'side:'

    def __init__(self, service):
        self.service = service
        self.paired = True
        self.generation = 's1'
        self.sent, self.delivered, self.cancelled = [], [], []

    def pairing(self):
        return SurfacePairing(f'side:{self.generation}', SIDE_OWNER) if self.paired else None

    def owns(self, job):
        return bool(job and self.paired and job.get('channel') == f'side:{self.generation}'
                    and job.get('chat_id') == SIDE_OWNER)

    def owner_generation(self):
        return self.generation if self.paired else ''

    def paired_owner(self, owner):
        return self.paired and owner == SIDE_OWNER

    def send_owner(self, text, reply_to=None):
        if not self.paired:
            return False
        self.sent.append(text)
        return True

    def reply_message(self, work_id, owner):
        return None

    def receive(self, now=None):
        return None

    def deliver_reply(self):
        store = self.service.store
        with store.db() as db:
            row = db.execute("SELECT id FROM jobs WHERE delivery='pending' AND channel LIKE 'side:%' "
                             'ORDER BY created LIMIT 1').fetchone()
            if row:
                db.execute("UPDATE jobs SET delivery='delivered' WHERE id=?", (row['id'],))
                self.delivered.append(row['id'])

    def queue_notification(self, job, kind, fingerprint=None):
        self.service.store.queue_notification(job['id'], job['chat_id'], self.generation, kind, fingerprint,
                                              surface=self.name)

    def deliver_notification(self):
        notification = self.service.store.next_notification(self.name)
        if not notification:
            return False
        self.service.store.update_notification(notification['id'], 'sent', message_id='post-1', delivered=True)
        self.delivered.append(notification['id'])
        return True

    def cancel_pending_deliveries(self):
        self.cancelled.append(True)
        with self.service.store.db() as db:
            replies = db.execute("UPDATE jobs SET delivery='cancelled' WHERE delivery='pending' AND channel LIKE 'side:%'").rowcount
            notices = db.execute("UPDATE telegram_notifications SET state='cancelled' WHERE state='queued' AND surface='side'").rowcount
        return replies, notices

    def deliver_conversation_notice(self, text):
        self.send_owner(text)

    def source_message(self, work_id):
        return None

    def present(self, action, *args):
        return False, False

    def show_draft(self, job, state, text, note=None):
        return False


class SurfaceBoundaryCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = QuickStore(Path(temp.name) / 'data')
        self.calls = []

        def transport(url, body=None, headers=None, timeout=60):
            self.calls.append((url.rsplit('/', 1)[-1], body))
            return {'ok': True, 'result': {'message_id': 9000 + len(self.calls)}}
        self.service = AgentService(self.store, ModelAdapter(lambda *a, **k: {'message': {'content': 'ok'}}), transport)
        self.store.put('telegram', {'enabled': True, 'user_id': CHAT, 'generation': GENERATION, 'cursor': 0})
        self.side = SideSurface(self.service)
        patcher = mock.patch.object(type(self.service), 'surfaces',
                                    property(lambda service: (service.telegram_surface, self.side)))
        patcher.start()
        self.addCleanup(patcher.stop)

    def work(self, channel, chat, delivery='pending', status='succeeded'):
        work_id = self.store.enqueue('hello', f'{channel}-{time.time_ns()}', channel=channel, chat_id=chat)
        with self.store.db() as db:
            db.execute('UPDATE jobs SET status=?, response=?, delivery=? WHERE id=?', (status, 'answer', delivery, work_id))
        return self.store.job(work_id)

    def sends(self):
        return [body for method, body in self.calls if method == 'sendMessage']


class DeliveryClaims(SurfaceBoundaryCase):
    def test_telegram_never_claims_or_cancels_another_surfaces_reply(self):
        side = self.work(f'side:s1', SIDE_OWNER)
        telegram = self.work(f'telegram:{GENERATION}', CHAT)
        self.service.telegram_surface.deliver_one()
        self.service.telegram_surface.deliver_one()
        self.assertEqual(self.store.job(side['id'])['delivery'], 'pending', 'Telegram left the other surface its reply')
        self.assertNotEqual(self.store.job(telegram['id'])['delivery'], 'pending')
        self.service.deliver_one()
        self.assertEqual((self.store.job(side['id'])['delivery'], self.side.delivered), ('delivered', [side['id']]))

    def test_a_reply_no_composed_surface_claims_is_cancelled_never_sent(self):
        orphan = self.work('gone:x1', 'someone')
        self.service.deliver_one()
        self.assertEqual(self.store.job(orphan['id'])['delivery'], 'cancelled')
        self.assertEqual(self.sends(), [])
        self.assertEqual(self.side.delivered, [])

    def test_notices_are_claimed_per_surface(self):
        side = self.work('side:s1', SIDE_OWNER, delivery='none')
        telegram = self.work(f'telegram:{GENERATION}', CHAT, delivery='none')
        self.service.queue_notification(side, 'memory_saved')
        self.service.queue_notification(telegram, 'memory_saved')
        side_row = self.store.notification_of(side['id'], 'memory_saved')
        self.assertEqual(side_row['surface'], 'side')
        while self.service.telegram_surface.deliver_notification():
            pass
        self.assertEqual(self.store.notification(side_row['id'])['state'], 'queued', 'Telegram left it queued')
        self.assertEqual(self.store.notification_of(telegram['id'], 'memory_saved')['surface'], 'telegram')
        while self.service.deliver_notification():
            pass
        self.assertEqual(self.store.notification(side_row['id'])['state'], 'sent')

    def test_a_notice_of_no_composed_surface_is_cancelled(self):
        job = self.work('gone:x1', 'someone', delivery='none')
        row = self.store.queue_notification(job['id'], 'someone', 'x1', 'memory_saved', surface='gone')
        self.service.deliver_notification()
        self.assertEqual(self.store.notification(row['id'])['state'], 'cancelled')

    def test_an_earlier_notice_row_is_telegrams(self):
        job = self.work(f'telegram:{GENERATION}', CHAT, delivery='none')
        with self.store.db() as db:
            db.execute("INSERT INTO telegram_notifications(id,job_id,chat_id,generation,kind,state,created) "
                       "VALUES ('legacy',?,?,?,'memory_saved','queued',?)", (job['id'], CHAT, GENERATION, time.time()))
        self.assertEqual(self.store.notification('legacy')['surface'], 'telegram')

    def test_work_mode_cancels_every_surfaces_queue(self):
        side = self.work('side:s1', SIDE_OWNER)
        self.service.set_work_mode({'enabled': True})
        self.assertEqual(self.side.cancelled, [True])
        self.assertEqual(self.store.job(side['id'])['delivery'], 'cancelled')


class OwnerIdentity(SurfaceBoundaryCase):
    def test_a_second_surfaces_work_keeps_its_own_connector_identity(self):
        side = self.work('side:s1', SIDE_OWNER)
        self.assertEqual(self.service.connector_owner_id(side), f'side:{SIDE_OWNER}',
                         'never the local owner (a string chat id fell into local-owner before #213)')
        self.assertEqual(self.service.connector_owner_id(self.work(f'telegram:{GENERATION}', CHAT)), f'telegram:{CHAT}')
        self.assertEqual(self.service.connector_owner_id(self.work('web', None, delivery='none')), 'local-owner')
        self.assertEqual(self.service._owner_generation(f'side:{SIDE_OWNER}'), 's1')
        self.assertEqual(self.service._owner_generation(f'telegram:{CHAT}'), GENERATION)
        self.assertEqual(self.service._owner_generation('local-owner'), '')

    def test_an_owner_notice_goes_only_to_that_surfaces_paired_owner(self):
        self.assertTrue(self.service._notify_owner(f'side:{SIDE_OWNER}', 'side notice'))
        self.assertEqual((self.side.sent, self.sends()), (['side notice'], []))
        self.assertFalse(self.service._notify_owner('side:someone-else', 'not paired'))
        self.assertTrue(self.service._notify_owner(f'telegram:{CHAT}', 'telegram notice'))
        self.assertEqual([body['text'] for body in self.sends()], ['telegram notice'])
        self.assertEqual(self.side.sent, ['side notice'])

    def test_a_settings_follow_up_reaches_the_surface_the_work_came_from(self):
        self.service.settings_followup(self.work('side:s1', SIDE_OWNER), 'applied')
        self.assertEqual((self.side.sent, self.sends()), (['applied'], []))


class PairingGuards(SurfaceBoundaryCase):
    def test_guards_hold_a_second_surfaces_work_to_its_current_pairing(self):
        """Before #213 these checks ran only for an int (Telegram) chat id; a string one skipped them."""
        side = self.work('side:s1', SIDE_OWNER)
        owner = self.service.settings_owner(side)
        draft = {'work_id': side['id'], 'owner': owner, 'channel': side['channel'],
                 'after': json.dumps({'engine': 'codex', 'name': 'tickets'})}
        self.service.mcp_setup_guard(draft)
        api_input.guard(self.service, draft)
        self.side.generation = 's2'   # re-paired: the Work's conversation is no longer current
        with self.assertRaises(ValueError):
            self.service.mcp_setup_guard(draft)
        self.side.paired = False
        with self.assertRaises(ValueError):
            api_input.guard(self.service, draft)


class OwnerActs(SurfaceBoundaryCase):
    def test_open_items_and_their_notices_belong_to_the_surface(self):
        side = self.work('side:s1', SIDE_OWNER, delivery='none')
        telegram = self.work(f'telegram:{GENERATION}', CHAT, delivery='none')
        for job, surface in ((side, 'side'), (telegram, 'telegram')):
            row = self.store.queue_notification(job['id'], job['chat_id'], 's1' if surface == 'side' else GENERATION,
                                                'preparation_proposed', surface=surface)
            self.store.update_notification(row['id'], 'sent', message_id='post-9' if surface == 'side' else 77, delivered=True)
        self.assertEqual([row['job_id'] for row in self.store.telegram.sent_of_kinds(SIDE_OWNER, ('preparation_proposed',),
                                                                                        surface='side')], [side['id']])
        self.assertEqual(self.store.telegram.sent_of_kinds(SIDE_OWNER, ('preparation_proposed',)), [],
                         'a Telegram read never sees the other surface')
        conversation = self.service.owner_acts._conversation(side)
        self.assertIs(conversation[0], self.side)
        self.assertEqual(conversation[1:], (SIDE_OWNER, 's1'))
        self.assertIsNone(self.service.owner_acts._conversation(self.work('side:s0', SIDE_OWNER)), 'an earlier pairing')

    def test_a_reaction_answer_is_told_on_its_own_surface(self):
        answer = {'item': {'kind': 'action-approval', 'ref': {'work_id': None}}, 'approve': True, 'rest': ''}
        acts = self.service.owner_acts
        with mock.patch.object(acts, 'read', return_value=answer), \
                mock.patch.object(acts, 'settle', return_value={'text': 'done'}):
            self.assertEqual(acts.answer_reaction(self.side, SIDE_OWNER, 's1', 'post-9', ['👍']), 'action-approval')
        self.assertEqual((self.side.sent, self.sends()), (['done'], []))


if __name__ == '__main__':
    unittest.main()
