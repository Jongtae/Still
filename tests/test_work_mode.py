"""WORK-MODE-01 (#208): with work mode on, nothing is exposed and nothing leaves for an undeclared destination.

Model-free and network-free: fake tunnels, fake Telegram transports, fake
search openers and skill transports, and a socket guard that fails the test
if any code path opens a connection.  Each refusal is asserted at the egress
point itself (the fake was never called), not only by its message.
"""
import json
import pathlib
import socket
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from cryptography.fernet import Fernet

from personal_agent import family_setup, phone_input, remote_login, work_mode
from personal_agent.agent_runtime import Capabilities
from personal_agent.bounded_execution import BOUNDED_PROFILE
from personal_agent.local_tools import LocalTools, PublicPageReader
from personal_agent.providers import ModelAdapter
from personal_agent.quickstart import configured_service, make_handler
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore
from personal_agent.search_providers import ProviderRegistry, SearchProviderError
from personal_agent.settings_orchestrator import SettingsError
from personal_agent.skills import SkillError, SkillLibrary

CFG = {'provider': 'compatible', 'endpoint': 'https://openrouter.ai/api/v1', 'model': 'openrouter/free'}
CHAT = 4242
GENERATION = 'g1'


class FakeTunnel:
    def __init__(self):
        self.processes = []

    def __call__(self, argv, **kwargs):
        class Process:
            stdout = iter([json.dumps({'msg': 'started tunnel', 'url': 'https://abc123.ngrok-free.app'}) + '\n'])
            terminated = False

            def terminate(inner):
                inner.terminated = True
        process = Process()
        self.processes.append((argv, process))
        return process


def no_socket(*args, **kwargs):
    raise AssertionError('a connection was opened while work mode is on')


class WorkModeCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = pathlib.Path(temp.name)
        self.store = QuickStore(self.root / 'data')

    def on(self):
        work_mode.set_enabled(self.store, True, surface='settings')

    def phone_service(self):
        """A service that offers the own-Google-client phone link, as ``test_phone_input`` sets it up."""
        return configured_service(self.store, {'AGENTOS_GOOGLE_LOCAL_PORT': '8787',
                                               'AGENTOS_GOOGLE_OAUTH_KEY': Fernet.generate_key().decode()})

    def off(self):
        work_mode.set_enabled(self.store, False, surface='settings')


class Switch(WorkModeCase):
    def test_no_row_is_personal_mode_and_a_damaged_row_fails_closed(self):
        self.assertFalse(work_mode.active(self.store))
        self.assertIsNone(work_mode.refusal(self.store, 'web_search'))
        for damaged in ({'enabled': 'no'}, {'nothing': True}, 'off', 0, []):
            with self.subTest(damaged=damaged):
                self.store.put(work_mode.CONFIG_KEY, damaged)
                self.assertTrue(work_mode.active(self.store), 'a damaged switch reads as on')
        self.assertFalse(work_mode.active(None), 'no owner store is personal mode')

    def test_only_a_real_bool_switches_it_and_every_change_is_audited(self):
        self.on()
        for bad in (None, 'off', 0, 1, 'false'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                work_mode.set_enabled(self.store, bad, surface='settings')
        self.assertTrue(work_mode.active(self.store), 'a malformed value never turns it off')
        self.off()
        audit = self.store.config(work_mode.AUDIT_KEY)
        self.assertEqual([(row['before'], row['after'], row['surface']) for row in audit],
                         [(None, True, 'settings'), (True, False, 'settings')])
        self.assertEqual(work_mode.status(self.store)['last_change']['after'], False)

    def test_unknown_egress_class_is_a_programming_error(self):
        with self.assertRaises(ValueError):
            work_mode.refusal(self.store, 'somewhere')


class SettingsOnly(WorkModeCase):
    def setUp(self):
        super().setUp()
        self.service = self.phone_service()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.service))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.session = self.store.local_session()

    def post(self, path, body, tunneled=False):
        headers = {'Content-Type': 'application/json', 'Cookie': f'agentos_session={self.session}',
                   'Origin': self.base}
        if tunneled:
            headers.update({'X-Forwarded-For': '203.0.113.9', 'X-Forwarded-Proto': 'https'})
        try:
            with urlopen(Request(self.base + path, data=json.dumps(body).encode(), method='POST', headers=headers),
                         timeout=10) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read() or b'{}')

    def test_the_settings_switch_exists_and_its_state_is_visible(self):
        status, body = self.post('/api/work-mode', {'enabled': True})
        self.assertEqual(status, 200, body)
        self.assertTrue(body['enabled'])
        self.assertTrue(work_mode.active(self.store))
        self.assertTrue(self.service.work_mode_status()['enabled'])
        read = self.service.settings_orchestrator.read('local-owner')
        self.assertEqual((read['work_mode']['enabled'], read['work_mode']['changeable']), (True, False))
        self.assertIn('회사 모드: 켜짐', read['response'])
        status, body = self.post('/api/work-mode', {'enabled': False})
        self.assertEqual((status, body['enabled']), (200, False))

    def test_the_switch_is_refused_through_a_tunnel(self):
        self.on()
        status, body = self.post('/api/work-mode', {'enabled': False}, tunneled=True)
        self.assertEqual(status, 400)
        self.assertTrue(work_mode.active(self.store), 'a relayed request cannot turn it off')

    def test_a_conversation_proposal_cannot_turn_it_off(self):
        self.on()
        orchestrator = self.service.settings_orchestrator
        for setting, value in (('enabled', 'off'), ('enabled', False), ('mode', 'personal')):
            with self.subTest(setting=setting, value=value), self.assertRaises(SettingsError) as caught:
                orchestrator.propose('local-owner', 'web', 'work_mode', setting, value, reason='퇴근했어요')
            self.assertIn('설정 화면에서만', str(caught.exception))
        self.assertTrue(work_mode.active(self.store))
        self.assertEqual(len(self.store.config(work_mode.AUDIT_KEY)), 1, 'only the Settings switch was recorded')

    def test_turning_it_on_ends_a_live_tunnel(self):
        tunnel = FakeTunnel()
        self.service.phone_input_popen = tunnel
        self.service.local_server_port = self.server.server_port
        self.service.telegram.call = lambda method, body: {'message_id': 1}
        self.store.put('telegram', {'enabled': True, 'user_id': CHAT, 'generation': GENERATION, 'cursor': 0})
        self.service.start_phone_input(phone_input.GOOGLE_CLIENT)
        self.assertIsNotNone(self.service.phone_input_session())
        self.service.set_work_mode({'enabled': True})
        self.assertIsNone(self.service.phone_input_session())
        self.assertTrue(tunnel.processes[0][1].terminated)


class ModelTools(WorkModeCase):
    """A Work tool that would leave this Mac is refused before it runs, on every route."""

    class Network:
        def __init__(self):
            self.calls = []

        def execute(self, plan):
            self.calls.append(plan)
            return {'tool': plan['tool'], 'url': plan.get('url', ''), 'content': 'ok', 'sources': [], 'results': []}

    def capabilities(self, network, **options):
        return Capabilities(self.store, None, CFG, '', 'job', lambda *a: None, network=network, **options)

    def test_each_outbound_tool_is_refused_without_running(self):
        network = self.Network()
        caps = self.capabilities(network, public_page_scope=['https://example.com/'])
        self.on()
        for name, args in (('web_search', {'query': 'q'}), ('public_page_read', {'url': 'https://example.com/'}),
                           ('weather', {'city': 'Seoul'})):
            with self.subTest(tool=name), self.assertRaises(ValueError) as caught:
                caps.execute(name, args)
            self.assertIsInstance(caught.exception, work_mode.WorkModeRefused)
            self.assertIs(getattr(caught.exception, 'ran', None), False, 'recorded as refused before it ran')
            self.assertIn('회사 모드', str(caught.exception))
        self.assertEqual(network.calls, [])

    def test_every_listed_tool_maps_to_a_refused_class(self):
        self.on()
        for action in ('browser_open', 'browser_read', 'browser_find', 'browser_click', 'browser_type',
                       'browser_sign_in', 'api_request', 'code_run', 'capability_search'):
            with self.subTest(action=action):
                self.assertIsInstance(work_mode.tool_refusal(self.store, action), work_mode.WorkModeRefused)
        for action in ('read_file', 'find_files', 'save_note', 'list_notes'):
            with self.subTest(action=action):
                self.assertIsNone(work_mode.tool_refusal(self.store, action), 'local tools stay available')

    def test_a_public_url_request_opens_no_socket(self):
        caps = self.capabilities(LocalTools(page_reader=PublicPageReader(resolver=no_socket)),
                                 public_page_scope=['https://example.com/'])
        self.on()
        with mock.patch.object(socket, 'create_connection', no_socket), \
                mock.patch.object(socket.socket, 'connect', no_socket):
            with self.assertRaises(work_mode.WorkModeRefused):
                caps.execute('public_page_read', {'url': 'https://example.com/'})

    def test_it_applies_from_the_next_call_of_a_running_work_and_off_restores_it(self):
        network = self.Network()
        caps = self.capabilities(network)
        caps.execute('web_search', {'query': 'before'})
        self.on()
        with self.assertRaises(work_mode.WorkModeRefused):
            caps.execute('web_search', {'query': 'during'})
        self.off()
        caps.execute('web_search', {'query': 'after'})
        self.assertEqual([plan['query'] for plan in network.calls], ['before', 'after'],
                         'nothing refused while it was on is run again')


class Search(WorkModeCase):
    def test_every_search_is_refused_at_the_registry(self):
        opened = []

        def opener(*args, **kwargs):
            opened.append(args)
            raise AssertionError('a search provider was contacted')
        self.store.secret('search_brave_key', 'fixture-key')
        registry = ProviderRegistry.from_store(self.store, opener=opener)
        self.on()
        with self.assertRaises(SearchProviderError) as caught:
            registry.search('company roadmap')
        self.assertEqual(caught.exception.code, 'work_mode')
        self.assertEqual(opened, [])

    def test_the_cli_own_web_search_stays_off(self):
        service = AgentService(self.store, ModelAdapter(lambda *a, **k: {}))
        enabled, _reason = service.cli_native_search('codex', BOUNDED_PROFILE, False)
        self.on()
        self.assertEqual(service.cli_native_search('codex', BOUNDED_PROFILE, False), (False, 'work_mode'))
        self.off()
        self.assertEqual(service.cli_native_search('codex', BOUNDED_PROFILE, False)[0], enabled)


class SessionKeepAlive(WorkModeCase):
    def test_signed_in_sites_are_not_refreshed(self):
        from personal_agent.quickstart_service import BROWSER_OWNER_SIGNINS_KEY
        service = AgentService(self.store, ModelAdapter(lambda *a, **k: {}))
        self.store.put(BROWSER_OWNER_SIGNINS_KEY, {'fixture.test': {'signed_in_at': 1}})
        service.browser_profile = mock.Mock(available=mock.Mock(side_effect=AssertionError('browser touched')))
        self.on()
        self.assertIsNone(service.keep_sessions_alive())


class Skills(WorkModeCase):
    def test_skill_acquisition_fetches_nothing(self):
        fetched = []

        def transport(url, *args, **kwargs):
            fetched.append(url)
            raise AssertionError('GitHub was contacted')
        library = SkillLibrary(self.store, transport=transport)
        self.on()
        with self.assertRaises(SkillError) as caught:
            library.install('https://github.com/example/skills/tree/main/notes')
        self.assertEqual(caught.exception.code, 'work_mode')
        self.assertEqual(fetched, [])


class Tunnels(WorkModeCase):
    def test_no_tunnel_process_is_ever_started(self):
        tunnel = FakeTunnel()
        self.on()
        with self.assertRaises(work_mode.WorkModeRefused):
            family_setup.start_tunnel(8787, popen=tunnel, store=self.store)
        self.assertEqual(tunnel.processes, [])

    def test_remote_login_refuses_with_a_do_it_at_the_mac_reason_and_no_spawn(self):
        tunnel = FakeTunnel()
        self.on()
        session = remote_login.RemoteLogin(object(), 'w', 'fixture.test', lambda reason: None, store=self.store,
                                           popen=tunnel)
        with self.assertRaises(remote_login.RemoteLoginError) as caught:
            session.start(8787)
        self.assertIn('이 Mac', str(caught.exception))
        self.assertEqual(tunnel.processes, [])

    def test_phone_input_refuses_with_a_do_it_at_the_mac_reason_and_no_spawn(self):
        tunnel = FakeTunnel()
        service = self.phone_service()
        service.phone_input_popen = tunnel
        service.local_server_port = 8787
        sent = []
        service.telegram.call = lambda method, body: sent.append(method) or {'message_id': 1}
        self.store.put('telegram', {'enabled': True, 'user_id': CHAT, 'generation': GENERATION, 'cursor': 0})
        self.on()
        with self.assertRaises(ValueError) as caught:
            service.start_phone_input(phone_input.GOOGLE_CLIENT)
        self.assertIn('이 Mac', str(caught.exception))
        self.assertEqual(tunnel.processes, [])
        self.assertEqual(sent, [])
        self.assertIsNone(service.phone_input_session())

    def test_family_setup_refuses_before_telegram_or_a_tunnel(self):
        tunnel = FakeTunnel()
        opened = []
        self.on()
        with self.assertRaises(family_setup.SetupError) as caught:
            family_setup.prepare_family_setup(self.store, 'spouse', None, service_action=lambda *a, **k: {},
                                              opener=lambda *a, **k: opened.append(a), popen=tunnel)
        self.assertIn('이 Mac', str(caught.exception))
        self.assertEqual((tunnel.processes, opened), ([], []))


class Telegram(WorkModeCase):
    def setUp(self):
        super().setUp()
        self.calls = []

        def transport(url, body, headers=None, timeout=None):
            self.calls.append(url.rsplit('/', 1)[-1])
            return {'ok': True, 'result': [] if url.endswith('/getUpdates') else {'message_id': 1}}
        self.service = AgentService(self.store, ModelAdapter(lambda *a, **k: {}), transport)
        self.store.secret('telegram_token', '123456:fixture-token')
        self.store.put('telegram', {'enabled': True, 'user_id': CHAT, 'generation': GENERATION, 'cursor': 0,
                                    'username': 'fixture_bot'})

    def test_no_bot_api_request_leaves(self):
        self.on()
        with self.assertRaises(work_mode.WorkModeRefused):
            self.service.telegram.send_message(CHAT, 'company answer')
        with self.assertRaises(work_mode.WorkModeRefused):
            self.service.telegram.download_photo('file-id')
        self.assertEqual(self.calls, [])

    def test_an_enabled_bot_stops_polling_and_says_why(self):
        self.service.receive_telegram()
        self.assertEqual(self.calls, ['getUpdates'])
        self.on()
        self.service.receive_telegram()
        self.assertEqual(self.calls, ['getUpdates'], 'no poll while work mode is on')
        self.assertEqual(self.store.config('telegram_status')['state'], 'paused')
        self.service.set_work_mode({'enabled': False})
        self.assertIsNone(self.store.config('telegram_status'), 'the paused status does not outlive work mode')
        self.service.receive_telegram()
        self.assertEqual(self.calls, ['getUpdates', 'getUpdates'], 'off restores polling')

    def test_presence_stands_down_without_an_error_status(self):
        job_id = self.store.enqueue('긴 작업', 'work-mode-presence', f'telegram:{GENERATION}', CHAT)
        self.on()
        self.assertEqual(self.service.acknowledge_long_work(), [])
        self.assertEqual(self.calls, [])
        self.assertNotEqual((self.store.config('telegram_status') or {}).get('state'), 'error')
        self.assertEqual(self.store.job(job_id)['status'], 'queued')

    def test_a_reply_and_a_notification_are_not_sent(self):
        job_id = self.store.enqueue('질문', 'work-mode-reply', f'telegram:{GENERATION}', CHAT)
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='succeeded',response='company answer',delivery='pending' WHERE id=?",
                       (job_id,))
        self.store.queue_notification(job_id, CHAT, GENERATION, 'preparation_scheduled')
        self.on()
        self.service.deliver_one()
        self.service.deliver_notification()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.store.job(job_id)['delivery'], 'cancelled', 'never sent later either')

    def test_telegram_cannot_be_enabled(self):
        self.store.put('telegram', {})
        self.on()
        with self.assertRaises(ValueError):
            self.service.connect_telegram({'token': '123456:another-fixture-token'})
        with self.assertRaises(ValueError):
            self.service.pair_telegram()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.store.config('telegram'), {})


class ApiInput(WorkModeCase):
    def test_the_alternative_page_opens_on_this_mac_only(self):
        """A Telegram-born Work's API input page opens locally in work mode, with no tunnel or Telegram."""
        from personal_agent import api_input
        service = AgentService(self.store, ModelAdapter(lambda *a, **k: {}))
        tunnel = FakeTunnel()
        service.phone_input_popen = tunnel
        service.local_server_port = 8787
        sent = []
        service.telegram.call = lambda method, body: sent.append(method) or {'message_id': 1}
        self.store.put('telegram', {'enabled': True, 'user_id': CHAT, 'generation': GENERATION, 'cursor': 0})
        work_id = self.store.enqueue('API 연결', 'api-input', f'telegram:{GENERATION}', CHAT)
        self.on()
        draft = {'id': 'draft-1', 'work_id': work_id, 'after': json.dumps({'name': 'Fixture'})}
        with mock.patch.object(api_input, 'current', lambda service, draft: None), \
                mock.patch.object(api_input, 'kept_fields', lambda store, data: {}):
            answer = api_input.start(service, draft)
        self.assertIn('http://localhost:8787', answer)
        self.assertEqual((tunnel.processes, sent), ([], []))
        service.phone_input_session().finish('closed')


if __name__ == '__main__':
    unittest.main()
