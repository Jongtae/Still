"""#212: the channel-neutral conversation surface boundary and its declared capabilities.

``TelegramSurface`` is the first ``ConversationSurface``.  These tests pin the
boundary's shape and that core Presence follows the surface's declared
``SurfaceCapabilities`` (reactions, typing, live drafts), not the surface's
name: a capability switched off on the surface removes exactly that part of the
presence, and the terminal reply is delivered either way.  Evidence class:
automated synthetic/fixture only - a fake Bot API transport records each call;
no live Telegram call is made and none is claimed.
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from personal_agent.conversation_surface import (PRESENCE_REACT, PRESENCE_TYPING, ConversationSurface,
                                                 SurfaceCapabilities, SurfacePairing)
from personal_agent.providers import ModelAdapter
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore
from personal_agent.telegram_surface import TelegramSurface

CHAT = 4242
GENERATION = 'g1'
PRESENCE_METHODS = ('setMessageReaction', 'sendChatAction', 'sendMessageDraft', 'sendRichMessageDraft')
DRAFT_METHODS = ('sendMessageDraft', 'sendRichMessageDraft')


class SurfaceCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = QuickStore(Path(self.temp.name) / 'data')
        self.calls = []
        self.during_model = None
        self.update_id = 100
        self.message_id = 500

        def transport(url, body=None, headers=None, timeout=60):
            method = url.rsplit('/', 1)[-1]
            self.calls.append((method, body))
            if method == 'sendMessage':
                return {'ok': True, 'result': {'message_id': 9000 + len(self.calls)}}
            return {'ok': True, 'result': True}

        def model(url, body, headers=None, timeout=60):
            tools = [t.get('function', {}).get('name') or t.get('name') for t in body.get('tools', [])]
            if 'agentos_connection_probe' in tools:
                return {'message': {'content': '', 'tool_calls': [
                    {'id': 'probe', 'function': {'name': 'agentos_connection_probe', 'arguments': {}}}]}}
            running = [job for job in self.store.jobs() if job['status'] == 'running']
            if self.during_model and running:
                hook, self.during_model = self.during_model, None
                hook(running[0])
            return {'message': {'content': '저녁은 순두부찌개 어때요?'}}

        self.service = AgentService(self.store, ModelAdapter(model), transport)
        self.service.turn_reaction_spawn = lambda target: target()
        self.store.put('telegram', {'enabled': True, 'user_id': CHAT, 'generation': GENERATION, 'cursor': 0})
        self.service.save_model({'provider': 'ollama', 'endpoint': 'http://127.0.0.1:11434',
                                 'model': 'test-model', 'api_key': ''})
        self.assertTrue(self.service.test_model()['ok'])
        self.calls.clear()

    def turn(self, text, waits=()):
        self.update_id += 1
        self.message_id += 1
        self.service.ingest_update({'update_id': self.update_id, 'message': {
            'message_id': self.message_id, 'from': {'id': CHAT},
            'chat': {'id': CHAT, 'type': 'private'}, 'text': text}}, GENERATION)
        if waits:
            self.during_model = lambda job: [self.service.acknowledge_long_work(now=job['created'] + t) for t in waits]
        job_id = self.store.jobs()[0]['id']
        self.service.run_one()
        self.service.deliver_one()
        return self.store.job(job_id)

    def methods(self):
        return [method for method, _body in self.calls]

    def with_capabilities(self, **declared):
        return mock.patch.object(TelegramSurface, 'capabilities', SurfaceCapabilities(**declared))


class BoundaryShape(SurfaceCase):
    def test_the_composed_surfaces_declare_what_they_show(self):
        surface = self.service.telegram_surface
        self.assertIsInstance(surface, ConversationSurface)
        # #213, #214: Mattermost and Slack are composed beside Telegram; each stays unpaired until connected in Settings.
        # #438: so is the terminal, until a terminal conversation attaches.
        self.assertEqual(self.service.surfaces, (surface, self.service.mattermost_surface, self.service.slack_surface,
                                                 self.service.terminal_surface))
        self.assertIsNone(self.service.mattermost_surface.pairing())
        self.assertIsNone(self.service.slack_surface.pairing())
        self.assertIsNone(self.service.terminal_surface.pairing())
        self.assertEqual(self.service.terminal_surface.capabilities, SurfaceCapabilities(buttons=True))
        self.assertEqual(surface.capabilities, SurfaceCapabilities(reactions=True, typing=True, live_drafts=True,
                                                                   buttons=True, native_stop=True, action_approvals=True))
        self.assertEqual(self.service.mattermost_surface.capabilities, SurfaceCapabilities(reactions=True, typing=True))
        self.assertEqual(self.service.slack_surface.capabilities,
                         SurfaceCapabilities(reactions=True, buttons=True, action_approvals=True, wait_status=True))

    def test_pairing_is_one_owner_identity_per_surface(self):
        surface = self.service.telegram_surface
        self.assertEqual(surface.pairing(), SurfacePairing(f'telegram:{GENERATION}', CHAT))
        paired = {'id': 'w1', 'channel': f'telegram:{GENERATION}', 'chat_id': CHAT}
        self.assertTrue(surface.owns(paired))
        self.assertIs(self.service.surface_of(paired), surface)
        for other in ({**paired, 'chat_id': CHAT + 1}, {**paired, 'channel': 'telegram:old'}, {**paired, 'channel': 'web'}):
            self.assertFalse(surface.owns(other))
            self.assertIsNone(self.service.surface_of(other))
        self.store.put('telegram', {'enabled': True, 'user_id': None, 'generation': GENERATION, 'cursor': 0})
        self.assertIsNone(surface.pairing())
        self.assertEqual(self.service.acknowledge_long_work(), [])

    def test_presence_actions_map_to_the_bot_api_and_never_raise(self):
        surface = self.service.telegram_surface
        self.assertEqual(surface.present(PRESENCE_REACT, CHAT, 77, '👀'), (True, False))
        self.assertEqual(surface.present(PRESENCE_TYPING, CHAT), (True, False))
        self.assertEqual(self.methods(), ['setMessageReaction', 'sendChatAction'])
        self.assertEqual(self.calls[1][1], {'chat_id': CHAT, 'action': 'typing'})


class CapabilitiesDrivePresence(SurfaceCase):
    def test_all_declared_shows_reaction_draft_and_typing(self):
        job = self.turn('오늘 저녁은 뭐해 먹을까?', waits=(2, 6))
        self.assertEqual(job['delivery'], 'sent')
        self.assertIn('setMessageReaction', self.methods())
        self.assertIn('sendChatAction', self.methods())
        self.assertTrue(any(method in DRAFT_METHODS for method in self.methods()))

    def test_without_reactions_no_reaction_is_set_and_the_reply_still_arrives(self):
        with self.with_capabilities(typing=True, live_drafts=True, buttons=True, native_stop=True):
            job = self.turn('오늘 저녁은 뭐해 먹을까?', waits=(2, 6))
        self.assertEqual(job['delivery'], 'sent')
        self.assertNotIn('setMessageReaction', self.methods())
        self.assertIn('sendChatAction', self.methods())
        self.assertTrue(any(method in DRAFT_METHODS for method in self.methods()))
        self.assertEqual(self.methods().count('sendMessage'), 1)

    def test_without_live_drafts_the_wait_is_typing_only(self):
        with self.with_capabilities(reactions=True, typing=True, buttons=True, native_stop=True):
            job = self.turn('긴 요청', waits=(6, 9))
        self.assertEqual(job['delivery'], 'sent')
        self.assertFalse(any(method in DRAFT_METHODS for method in self.methods()))
        self.assertIn('sendChatAction', self.methods())

    def test_without_typing_or_drafts_nothing_but_the_reply_is_sent_while_waiting(self):
        with self.with_capabilities(reactions=True, buttons=True, native_stop=True):
            job = self.turn('긴 요청', waits=(2, 6, 9))
        self.assertEqual(job['delivery'], 'sent')
        self.assertEqual([method for method in self.methods() if method in PRESENCE_METHODS and method != 'setMessageReaction'], [])
        self.assertEqual(self.methods().count('sendMessage'), 1)


if __name__ == '__main__':
    unittest.main()
