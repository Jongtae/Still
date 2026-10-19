"""#1337: the owner's bot gets Still's name, photo and descriptions, once."""
import json
import tempfile
from pathlib import Path
import unittest
from unittest import mock

from personal_agent import conversation_handoff, still_profile
from personal_agent.conversation_handoff import TelegramChannel, TelegramRejected
from personal_agent.providers import ProviderError
from personal_agent.quickstart_store import QuickStore
from personal_agent.quickstart_service import AgentService


class FakeChannel:
    def __init__(self, refuse=()):
        self.calls, self.uploads, self.refuse = [], [], set(refuse)

    def call(self, method, body, timeout=None):
        self.calls.append((method, body, timeout))
        if method in self.refuse:
            raise TelegramRejected(400, 'Bad Request')
        return True

    def upload(self, method, fields, files, timeout=None):
        self.uploads.append((method, fields, files, timeout))
        if method in self.refuse:
            raise TelegramRejected(400, 'Bad Request')
        return True


class StillProfileTests(unittest.TestCase):
    def test_sets_name_descriptions_in_four_languages_and_photo(self):
        channel = FakeChannel()
        done = still_profile.apply(channel)
        self.assertTrue(all(done.values()))
        self.assertEqual(channel.calls[0][:2], ('setMyName', {'name': 'Still'}))
        langs = {body.get('language_code') for method, body, _ in channel.calls if method == 'setMyDescription'}
        self.assertEqual(langs, {None, 'ko', 'ja', 'zh'})
        for method, body, timeout in channel.calls:
            self.assertEqual(timeout, still_profile.PROFILE_TIMEOUT)
            if method == 'setMyShortDescription':
                self.assertLessEqual(len(body['short_description']), 120)
            if method == 'setMyDescription':
                self.assertLessEqual(len(body['description']), 512)
        (method, fields, files, timeout), = channel.uploads
        self.assertEqual(method, 'setMyProfilePhoto')
        self.assertEqual(timeout, still_profile.PHOTO_TIMEOUT, 'the photo is an upload and gets longer (#1541)')
        self.assertEqual(json.loads(fields['photo']), {'type': 'static', 'photo': 'attach://still'})
        name, content_type, data = files['still']
        self.assertEqual(content_type, 'image/jpeg')
        self.assertTrue(data.startswith(b'\xff\xd8'), 'the bundled avatar is a JPEG')

    def test_one_refused_step_does_not_stop_the_others(self):
        channel = FakeChannel(refuse={'setMyName'})
        done = still_profile.apply(channel)
        self.assertFalse(done['name'])
        self.assertTrue(done['photo'])
        self.assertTrue(done['description:ko'])

    def test_the_log_names_why_a_step_failed(self):
        class TimingOut(FakeChannel):
            def upload(self, method, fields, files, timeout=None):
                raise ProviderError('x', status='timeout')

        with self.assertLogs('personal_agent.still_profile', 'WARNING') as logs:
            done = still_profile.apply(TimingOut())
        self.assertFalse(done['photo'])
        self.assertEqual(logs.output, ['WARNING:personal_agent.still_profile:still profile: photo not set (ProviderError timeout)'])


class MultipartTests(unittest.TestCase):
    def test_multipart_carries_fields_and_file_in_one_request(self):
        import httpx2
        seen = []

        def handler(request):
            seen.append(request)
            return httpx2.Response(200, json={'ok': True, 'result': True})

        client = httpx2.Client(transport=httpx2.MockTransport(handler))
        with mock.patch.object(conversation_handoff, '_telegram_http', lambda: client):
            out = conversation_handoff.telegram_request_multipart(
                'https://api.telegram.org/botT/setMyProfilePhoto', {'photo': '{"type":"static","photo":"attach://still"}'},
                {'still': ('still.jpg', 'image/jpeg', b'\xff\xd8JPEG')}, timeout=4)
        self.assertEqual(out, {'ok': True, 'result': True})
        [req] = seen
        self.assertEqual(req.method, 'POST')
        boundary = req.headers['Content-Type'].split('boundary=')[1]
        body = req.content
        self.assertIn(f'--{boundary}\r\nContent-Disposition: form-data; name="photo"'.encode(), body)
        self.assertIn(b'filename="still.jpg"\r\nContent-Type: image/jpeg\r\n\r\n\xff\xd8JPEG\r\n', body)
        self.assertTrue(body.endswith(f'--{boundary}--\r\n'.encode()))
        self.assertEqual(req.extensions['timeout']['read'], 4)
        self.assertEqual(req.extensions['timeout']['connect'], 4)

    def test_channel_upload_raises_on_refusal(self):
        channel = TelegramChannel(lambda: None, lambda: 'T',
                                  upload_transport_source=lambda: (lambda url, f, files, timeout: {'ok': False, 'error_code': 400, 'description': 'no'}))
        with self.assertRaises(TelegramRejected):
            channel.upload('setMyProfilePhoto', {}, {})


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = QuickStore(self.temp.name)
        self.service = AgentService(self.store)
        self.json_calls, self.uploads, self.refuse = [], [], set()

        def transport(url, body, headers=None, timeout=None):
            method = url.rsplit('/', 1)[1]
            self.json_calls.append((url, method, body))
            return {'ok': False, 'error_code': 400, 'description': 'no'} if method in self.refuse else {'ok': True, 'result': True}

        def upload(url, fields, files, timeout=None):
            self.uploads.append(url)
            return {'ok': False, 'error_code': 400, 'description': 'no'} if 'setMyProfilePhoto' in self.refuse else {'ok': True, 'result': True}

        self.service.telegram_transport = transport
        self.service.telegram_upload_transport = upload

    def connect(self, username='owner_bot', token='111:AAA'):
        self.store.secret('telegram_token', token)
        self.store.put('telegram', {'enabled': True, 'mode': 'owner-token', 'username': username, 'bot_name': 'My helper'})

    def test_applies_once_per_bot_keeps_a_marker_and_syncs_the_bot_name(self):
        self.connect()
        self.assertTrue(all(self.service.apply_still_profile().values()))
        marker = self.store.config('telegram_profile', {})
        self.assertEqual((marker['username'], marker['profile']), ('owner_bot', 'still'))
        self.assertEqual(self.store.config('telegram')['bot_name'], 'Still', 'share targets see the new name (#957)')
        self.assertTrue(all('/bot111:AAA/' in url for url, _, _ in self.json_calls))
        self.assertTrue(all('/bot111:AAA/' in url for url in self.uploads))
        calls = len(self.json_calls)
        self.assertIsNone(self.service.apply_still_profile(), 'the same bot is not renamed again')
        self.assertEqual(len(self.json_calls), calls)
        self.connect(username='another_bot', token='222:BBB')
        self.assertIsNotNone(self.service.apply_still_profile(), 'a newly connected bot gets the profile')

    def test_a_bot_replaced_mid_run_keeps_its_own_state(self):
        self.connect()
        original = self.service.telegram_transport

        def swap_on_first_call(url, body, headers=None, timeout=None):
            if not self.json_calls:
                self.connect(username='new_bot', token='333:CCC')
            return original(url, body, headers, timeout)

        self.service.telegram_transport = swap_on_first_call
        self.service.apply_still_profile()
        self.assertTrue(all('/bot111:AAA/' in url for url, _, _ in self.json_calls), 'every call stays on the starting bot')
        self.assertEqual(self.store.config('telegram_profile', {}), {}, 'no marker for a bot that is no longer connected')
        self.assertEqual(self.store.config('telegram')['bot_name'], 'My helper', 'the new bot keeps its own name')

    def test_nothing_happens_without_a_connected_bot(self):
        self.store.put('telegram', {'enabled': False, 'username': 'owner_bot'})
        self.assertIsNone(self.service.apply_still_profile())
        self.assertEqual(self.json_calls, [])

    def test_a_failed_photo_is_retried_alone_on_a_later_start(self):
        self.refuse = {'setMyProfilePhoto'}
        self.connect()
        self.assertFalse(self.service.apply_still_profile()['photo'])
        self.assertFalse(self.store.config('telegram_profile')['steps']['photo'])
        self.refuse = set()
        calls = len(self.json_calls)
        self.assertEqual(self.service.apply_still_profile(), {'photo': True})
        self.assertEqual(len(self.json_calls), calls, 'name and descriptions are not reapplied')
        self.assertEqual(len(self.uploads), 2)
        self.assertTrue(self.store.config('telegram_profile')['steps']['photo'])
        self.assertTrue(self.store.config('telegram_profile')['steps']['name'])
        self.assertIsNone(self.service.apply_still_profile(), 'nothing left to do')
        self.assertEqual(len(self.uploads), 2)

    def test_startup_applies_the_profile_for_a_bot_connected_in_the_terminal(self):
        route = (Path(__file__).resolve().parents[1] / 'src/personal_agent/quickstart.py').read_text(encoding='utf-8')
        start = route.index('    service.start()\n')
        self.assertIn('target=service.apply_still_profile', route[start:start + 300])

    def test_no_marker_when_telegram_refused_everything(self):
        self.refuse = {'setMyName', 'setMyDescription', 'setMyShortDescription', 'setMyProfilePhoto'}
        self.connect()
        self.service.apply_still_profile()
        self.assertEqual(self.store.config('telegram_profile', {}), {}, 'a later connect can try again')
        self.assertEqual(self.store.config('telegram')['bot_name'], 'My helper')

    def test_family_instances_keep_their_own_profile(self):
        from personal_agent.family_setup import setup_path
        self.store.write_private(setup_path(self.store), json.dumps({'instance': 'mom', 'finished': 1}))
        self.connect()
        self.assertIsNone(self.service.apply_still_profile())
        self.assertEqual(self.json_calls, [])
        route = (Path(__file__).resolve().parents[1] / 'src/personal_agent/quickstart.py').read_text(encoding='utf-8')
        self.assertIn('target=service.apply_still_profile', route)


class WelcomeTests(unittest.TestCase):
    def test_welcome_follows_the_owner_language_and_defaults_to_english(self):
        self.assertTrue(still_profile.welcome('ko').startswith('안녕하세요, Still이에요'))
        self.assertTrue(still_profile.welcome('ja-JP').startswith('こんにちは、Still です'))
        self.assertTrue(still_profile.welcome('zh-hans').startswith('你好，我是 Still'))
        self.assertTrue(still_profile.welcome('de').startswith("Hi, I'm Still"))
        self.assertTrue(still_profile.welcome(None).startswith("Hi, I'm Still"))

    def test_every_welcome_teaches_how_to_ask_and_stays_within_a_telegram_message(self):
        for lang, text in still_profile.WELCOME.items():
            with self.subTest(lang=lang):
                self.assertLessEqual(len(text), 4096)
                self.assertIn('/ai', text)
                self.assertGreaterEqual(text.count('\n·') + text.count('\n・'), 4, 'four everyday examples')
                self.assertNotIn('/search', text)


if __name__ == '__main__':
    unittest.main()
