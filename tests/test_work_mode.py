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
import time
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

    def test_was_on_since_reads_the_switch_history_and_fails_closed(self):
        clock = iter([100.0, 200.0, 300.0])
        self.assertFalse(work_mode.was_on_since(self.store, 50.0), 'never switched: personal mode')
        work_mode.set_enabled(self.store, True, surface='settings', clock=lambda: next(clock))   # on at 100
        work_mode.set_enabled(self.store, False, surface='settings', clock=lambda: next(clock))  # off at 200
        self.assertTrue(work_mode.was_on_since(self.store, 50.0), 'on at 100, inside the window')
        self.assertTrue(work_mode.was_on_since(self.store, 150.0), 'still on at 150')
        self.assertFalse(work_mode.was_on_since(self.store, 250.0), 'off since 200')
        self.assertTrue(work_mode.was_on_since(self.store, None), 'no start time: fail closed')
        self.store.put(work_mode.AUDIT_KEY, [{'at': 1000.0 + i, 'before': False, 'after': False}
                                             for i in range(work_mode.AUDIT_LIMIT)])
        self.assertTrue(work_mode.was_on_since(self.store, 500.0), 'older than the kept history: fail closed')

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

    def test_one_switch_at_a_time(self):
        held = []
        original = work_mode.set_enabled

        def recording(*args, **kwargs):
            held.append(self.service._work_mode_switch.locked())
            return original(*args, **kwargs)
        with mock.patch.object(work_mode, 'set_enabled', recording):
            self.service.set_work_mode({'enabled': True})
        self.assertEqual(held, [True], 'the state write and its clean-up run under one lock')

    def test_every_tunnel_caller_names_the_owner_store(self):
        """#208 review: ``store`` is required; a caller left without it would fail at run time."""
        import re
        root = pathlib.Path(__file__).resolve().parents[1]
        for path in [*root.joinpath('src').rglob('*.py'), *root.joinpath('scripts').rglob('*.py')]:
            for call in re.findall(r'(?<!def )start_tunnel\(([^)]*)\)', path.read_text(encoding='utf-8')):
                with self.subTest(path=str(path.relative_to(root)), call=call):
                    self.assertIn('store=', call)

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


class StaleChecks(WorkModeCase):
    """#208 review: a flow that passed its first check asks again right before each request."""

    def test_each_skill_download_asks_again(self):
        fetched = []
        library = SkillLibrary(self.store, transport=lambda url, *a, **k: fetched.append(url) or b'')
        library.transport('https://api.github.com/repos/example/skills/commits/main')
        self.on()
        with self.assertRaises(SkillError) as caught:
            library.transport('https://codeload.github.com/example/skills/tar.gz/abc')
        self.assertEqual((caught.exception.code, len(fetched)), ('work_mode', 1))

    def test_family_setup_direct_telegram_requests_ask_again(self):
        opened = []
        self.on()
        with self.assertRaises(family_setup.SetupError):
            family_setup._telegram_get('123:token', 'getMe', lambda *a, **k: opened.append(a), store=self.store)
        self.assertEqual(opened, [])

    def test_the_keep_alive_request_asks_again_after_the_scan(self):
        from personal_agent.quickstart_service import BROWSER_OWNER_SIGNINS_KEY
        service = AgentService(self.store, ModelAdapter(lambda *a, **k: {}))
        self.store.put(BROWSER_OWNER_SIGNINS_KEY, {'fixture.test': {'at': 1, 'host': 'fixture.test'}})
        refreshed = []

        def marks(host):
            self.on()  # the switch lands while the scan runs
            return [('session', 1, 0, 2)], 10 ** 10
        service.browser_profile = mock.Mock(available=lambda: True, site_cookie_marks=marks,
                                            refresh_session_http=lambda url: refreshed.append(url) or {'state': 'ok'})
        self.assertIsNone(service.keep_sessions_alive())
        self.assertEqual(refreshed, [])


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


class SwitchedOnDuringStart(WorkModeCase):
    """#208 review: the switch lands while ngrok is still starting; the new tunnel must not stay up."""

    def racing_tunnel(self):
        tunnel = FakeTunnel()

        def spawn(argv, **kwargs):
            process = tunnel(argv, **kwargs)
            self.on()  # the owner switches work mode on mid-spawn, after start_tunnel's own check
            return process
        return tunnel, spawn

    def test_a_phone_input_tunnel_started_across_the_switch_is_terminated(self):
        tunnel, spawn = self.racing_tunnel()
        session = phone_input.PhoneInput(phone_input.GOOGLE_CLIENT, 'client', store=self.store, popen=spawn)
        with self.assertRaises(phone_input.PhoneInputError) as caught:
            session.start(8787)
        self.assertIn('이 Mac', str(caught.exception))
        self.assertTrue(tunnel.processes[0][1].terminated)
        self.assertIsNone(session.process)
        self.assertIsNone(session.link, 'no link was given out')

    def test_a_remote_login_tunnel_started_across_the_switch_is_terminated(self):
        tunnel, spawn = self.racing_tunnel()
        session = remote_login.RemoteLogin(object(), 'w', 'fixture.test', lambda reason: None, store=self.store,
                                           popen=spawn)
        with self.assertRaises(remote_login.RemoteLoginError):
            session.start(8787)
        self.assertTrue(tunnel.processes[0][1].terminated)
        self.assertIsNone(session.link)

    def test_a_session_finished_while_starting_does_not_keep_its_tunnel(self):
        """The switch's ``finish`` ran before the process was attached; the start ends it itself."""
        tunnel = FakeTunnel()
        holder = {}

        def spawn(argv, **kwargs):
            holder['session'].finish('work_mode')
            return tunnel(argv, **kwargs)
        session = holder['session'] = phone_input.PhoneInput(phone_input.GOOGLE_CLIENT, 'client', store=self.store,
                                                             popen=spawn)
        with self.assertRaises(phone_input.PhoneInputError):
            session.start(8787)
        self.assertTrue(tunnel.processes[0][1].terminated)


class RunningNativeSearchTurn(WorkModeCase):
    """#208 review: a CLI turn that started with its own web search is ended when work mode goes on."""

    def test_the_poll_reports_work_mode_before_the_works_own_reasons(self):
        from personal_agent.bounded_execution import WORK_MODE_INTERRUPT, work_mode_interrupt
        poll = work_mode_interrupt(self.store, lambda: None)
        self.assertIsNone(poll())
        self.assertEqual(work_mode_interrupt(self.store, lambda: 'stopped')(), 'stopped')
        self.on()
        self.assertEqual(poll(), WORK_MODE_INTERRUPT)
        self.assertEqual(work_mode_interrupt(self.store, None)(), WORK_MODE_INTERRUPT)

    def test_a_running_cli_process_is_killed_when_the_switch_goes_on(self):
        import subprocess
        import sys
        import time
        from personal_agent.bounded_execution import EngineInterrupted, bounded_run, work_mode_interrupt
        timer = threading.Timer(0.5, self.on)
        timer.start()
        self.addCleanup(timer.cancel)
        started = time.monotonic()
        with self.assertRaises(EngineInterrupted) as caught:
            bounded_run(subprocess.run, [sys.executable, '-c', 'import time; time.sleep(30)'], cwd=str(self.root),
                        env={'PATH': '/usr/bin:/bin'}, timeout=30, interrupted=work_mode_interrupt(self.store, None))
        self.assertEqual(caught.exception.reason, 'work_mode')
        self.assertLess(time.monotonic() - started, 10)

    def test_a_native_search_work_turn_is_ended_by_the_switch(self):
        """The service route: a Codex turn launched with its own web search is killed, typed ``work_mode``."""
        import time
        from personal_agent.agent_runtime import WORK_STOP_KEEP, WORK_STOP_KEY
        from personal_agent.bounded_execution import BoundedExecutionAdapter
        from personal_agent.subscription_engines import SubscriptionEngines
        from test_agency_recovery import CliProcessStopTests, _gone
        script, marker = CliProcessStopTests.fake_cli(self, self.root)
        (self.root / 'codex-home').mkdir()
        adapter = BoundedExecutionAdapter(finder=lambda _: str(script), runtime_root=self.root / 'turns',
                                          codex_home=self.root / 'codex-home')
        service = AgentService(self.store, subscription_engines=SubscriptionEngines(finder=lambda _: str(script),
                                                                                    clock=lambda: 1),
                               execution_adapter=adapter)
        service.connect_subscription_engine({'engine': 'codex', 'officially_authenticated': True})
        job = self.store.enqueue('긴 조사 해줘', 'work-mode-native', channel='web')

        def switch():
            while not marker.exists():
                time.sleep(0.05)
            first = int(marker.read_text())
            self.on()
            _gone(first)
            # Whatever the orchestration does next is stopped, so the test never waits on a re-run.
            self.store.append_config_list(WORK_STOP_KEY, job, WORK_STOP_KEEP)
        threading.Thread(target=switch, daemon=True).start()
        started = time.monotonic()
        service.run_one()
        self.assertLess(time.monotonic() - started, 30)
        failures = [event['trace'] for event in self.store.task_events(job)
                    if event['tool'] == 'subscription_engine' and event['status'] == 'failed']
        self.assertEqual(failures[0]['failure_class'], 'work_mode')


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

    def test_a_delivery_refused_after_it_was_claimed_is_cancelled_not_unknown(self):
        job_id = self.store.enqueue('질문', 'work-mode-claimed', f'telegram:{GENERATION}', CHAT)
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='succeeded',response='answer',delivery='pending' WHERE id=?", (job_id,))

        def refused(*args, **kwargs):
            raise work_mode.WorkModeRefused('telegram')  # the switch landed after the row was claimed
        with mock.patch.object(self.service.telegram, 'send_message', refused):
            self.service.deliver_one()
        self.assertEqual(self.store.job(job_id)['delivery'], 'cancelled')

    def test_a_photo_retry_after_the_switch_is_not_sent(self):
        from personal_agent.providers import ProviderError
        self.service.telegram_transport = lambda url, body, headers=None, timeout=None: {
            'ok': True, 'result': {'file_path': 'photos/a.jpg', 'file_size': 10}}
        fetched = []

        def file_transport(url, timeout=None, max_bytes=None):
            fetched.append(url)
            self.on()  # the switch lands during the retry back-off
            raise ProviderError('reset', status='unreachable')
        self.service.telegram_file_transport = file_transport
        self.service.telegram._sleep = lambda seconds: None
        with self.assertRaises(work_mode.WorkModeRefused):
            self.service.telegram.download_photo('file-id')
        self.assertEqual(len(fetched), 1, 'the retry was refused unsent')

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

    def test_switching_on_cancels_queued_deliveries_even_if_it_is_switched_off_before_the_next_tick(self):
        job_id = self.store.enqueue('질문', 'work-mode-queued', f'telegram:{GENERATION}', CHAT)
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='succeeded',response='company answer',delivery='pending' WHERE id=?",
                       (job_id,))
        self.store.queue_notification(job_id, CHAT, GENERATION, 'preparation_scheduled')
        self.service.set_work_mode({'enabled': True})
        self.service.set_work_mode({'enabled': False})
        self.service.deliver_one()
        self.service.deliver_notification()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.store.job(job_id)['delivery'], 'cancelled')
        self.assertIsNone(self.store.next_notification())

    def test_a_notification_read_before_the_switch_is_not_reclaimed(self):
        job_id = self.store.enqueue('질문', 'work-mode-stale', f'telegram:{GENERATION}', CHAT)
        # A kind that is sent whenever it is claimed (no other state can cancel it first).
        self.store.queue_notification(job_id, CHAT, GENERATION, 'context_approval_needed')
        stale = self.store.next_notification()
        self.service.set_work_mode({'enabled': True})   # cancels it
        self.service.set_work_mode({'enabled': False})
        with mock.patch.object(self.store, 'next_notification', lambda surface='telegram': stale):
            self.service.deliver_notification()
        self.assertEqual(self.calls, [])
        with self.store.db() as db:
            state = db.execute('SELECT state FROM telegram_notifications WHERE id=?', (stale['id'],)).fetchone()
        self.assertEqual(state['state'], 'cancelled')

    def test_items_produced_while_on_are_not_sent_after_it_is_switched_off(self):
        """A Telegram Work running across the switch finishes and queues after the one-time cancel."""
        job_id = self.store.enqueue('질문', 'work-mode-across', f'telegram:{GENERATION}', CHAT)
        self.service.set_work_mode({'enabled': True})
        with self.store.db() as db:   # the Work finishes while work mode is on
            db.execute("UPDATE jobs SET status='succeeded',response='company answer',delivery='pending' WHERE id=?",
                       (job_id,))
        self.store.queue_notification(job_id, CHAT, GENERATION, 'context_approval_needed')
        self.service.set_work_mode({'enabled': False})
        self.service.deliver_one()
        self.service.deliver_notification()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.store.job(job_id)['delivery'], 'cancelled')
        later = self.store.enqueue('새 질문', 'work-mode-later', f'telegram:{GENERATION}', CHAT)
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='succeeded',response='answer',delivery='pending',created=? WHERE id=?",
                       (time.time() + 5, later))
        self.service.deliver_one()
        self.assertIn('sendMessage', self.calls, 'a Work begun after it was switched off is delivered')

    def test_updates_from_a_poll_in_flight_at_the_switch_are_not_ingested(self):
        def transport(url, body, headers=None, timeout=None):
            self.calls.append(url.rsplit('/', 1)[-1])
            self.on()  # the switch lands while getUpdates is still open
            return {'ok': True, 'result': [{'update_id': 7, 'message': {
                'message_id': 1, 'from': {'id': CHAT}, 'chat': {'id': CHAT, 'type': 'private'}, 'text': '이거 해줘'}}]}
        self.service.telegram_transport = transport
        self.service.poll_telegram()
        self.assertEqual(self.store.jobs(), [], 'no Work was created')
        self.assertEqual(self.store.config('telegram')['cursor'], 0, 'Telegram keeps the update for later')

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
        draft = {'id': 'draft-1', 'work_id': work_id, 'after': json.dumps({'name': 'Fixture', 'fields': []})}
        with mock.patch.object(api_input, 'current', lambda service, draft: None), \
                mock.patch.object(api_input, 'kept_fields', lambda store, data: {}):
            answer = api_input.start(service, draft)
            self.assertIn('http://localhost:8787', answer)
            self.assertEqual((tunnel.processes, sent), ([], []))
            service.phone_input_session().finish('closed')
            # #208 review: an input verified by a request to its API is refused before any page opens.
            probed = {**draft, 'after': json.dumps({'name': 'Fixture', 'probe': {'url': 'https://api.fixture.test/me'}})}
            with self.assertRaises(ValueError) as caught:
                api_input.start(service, probed)
            self.assertIn('회사 모드', str(caught.exception))
            self.assertIsNone(service.phone_input_session())


class TransportRecheck(WorkModeCase):
    """#208 review: a tool already running when the switch lands is refused at its next request."""

    def test_api_requests_refuse_every_request_unsent(self):
        from personal_agent.api_requests import ApiError, ApiRequests
        sent = []
        api = ApiRequests(self.store, 'job', transport=lambda *args: sent.append(args) or (200, {}, b'{}'))
        api.transport('GET', 'https://api.fixture.test/a', {}, None, 5)
        self.on()
        with self.assertRaises(ApiError) as caught:
            api.transport('GET', 'https://api.fixture.test/b', {}, None, 5)
        self.assertEqual(caught.exception.code, 'work_mode')
        self.assertEqual(len(sent), 1)

    def test_a_state_changing_call_refused_at_the_switch_leaves_no_reservation(self):
        """A mutating API call: refused before or right after its effect reservation, nothing stays reserved."""
        from personal_agent.api_requests import ApiError, ApiRequests, save_slot
        service = AgentService(self.store)
        work = self.store.enqueue('Apply the change', 'work-mode-effect')
        save_slot(self.store, 'synthetic', ['synthetic.example'], 'synthetic-private-credential')
        sent = []
        call = {'slot': 'synthetic', 'url': 'https://synthetic.example/change', 'method': 'POST', 'effect': 'mutate',
                'body': '{"value":"x"}'}

        def client():
            return ApiRequests(self.store, work, transport=lambda *a: sent.append(a) or (200, {}, b'{"ok":true}'),
                               approvals=service.browser_approvals_for(self.store.job(work)))

        def dispatch_rows():
            with self.store.db() as db:
                return db.execute("SELECT status FROM tool_events WHERE tool='api_dispatch'").fetchall()
        self.on()
        with self.assertRaises(ApiError) as caught:
            client().call(call)
        self.assertEqual((caught.exception.code, sent, dispatch_rows()), ('work_mode', [], []))
        self.off()
        api = client()
        reserve = api._effect_guard

        def reserve_then_switch(*args, **kwargs):
            claim = reserve(*args, **kwargs)
            if claim is not None:
                self.on()  # the switch lands after the reservation committed (not after the preflight)
            return claim
        api._effect_guard = reserve_then_switch
        with self.assertRaises(ApiError) as caught:
            api.call(call)
        self.assertEqual((caught.exception.code, sent, dispatch_rows()), ('work_mode', [], []))
        self.assertEqual(service.connector_effect_progress(work)[1], [], 'no unknown effect blocks a later retry')

    def test_a_code_run_request_is_refused_unsent(self):
        from personal_agent.code_run import CodeRunner
        runner = CodeRunner(self.store, 'job')
        self.on()
        allow, reason = runner._decide({'method': 'GET', 'host': 'api.fixture.test', 'path': '/'},
                                       {'api.fixture.test': set()})
        self.assertFalse(allow)
        self.assertIn('회사 모드', reason)

    def test_a_page_redirect_after_the_switch_is_not_followed(self):
        class Opener:
            def __init__(inner):
                inner.urls = []

            def open(inner, request, timeout=None):
                inner.urls.append(request.full_url)
                self.on()  # the switch lands while the first hop is in flight

                class Redirect:
                    status = 302
                    url = request.full_url
                    headers = {'Location': 'https://second.fixture.test/'}

                    def read(self, *a):
                        return b''

                    def close(self):
                        pass
                return Redirect()
        opener = Opener()
        tools = LocalTools(page_reader=None, providers=ProviderRegistry.from_store(self.store))
        tools.page_reader.opener = opener
        tools.page_reader.resolver = lambda *a, **k: [(None, None, None, None, ('93.184.216.34', 443))]
        with self.assertRaises(ValueError) as caught:
            tools.page_reader.read('https://first.fixture.test/')
        self.assertIn('회사 모드', str(caught.exception))
        self.assertEqual(opener.urls, ['https://first.fixture.test/'])
        self.assertIsNot(getattr(caught.exception, 'ran', None), False, 'the first hop was sent: an attempt (#233)')
        with self.assertRaises(ValueError) as caught:
            tools.page_reader.read('https://first.fixture.test/')
        self.assertIs(caught.exception.ran, False, 'refused before any request: not sent (#233)')
        self.assertEqual(len(opener.urls), 1)

    def test_a_native_search_resume_after_the_switch_is_not_sent(self):
        registry = ProviderRegistry.from_store(self.store)
        from personal_agent.search_providers import AiNativeProvider
        provider = AiNativeProvider.__new__(AiNativeProvider)
        provider.refusal = lambda: registry.egress_refusal('web_search')
        provider.budget = None
        sent = []
        provider.transport = lambda *args: sent.append(args) or {}
        provider._send('https://api.fixture.test/v1/messages', {}, {})
        self.on()
        with self.assertRaises(SearchProviderError) as caught:
            provider._send('https://api.fixture.test/v1/messages', {}, {})
        self.assertEqual((caught.exception.code, len(sent)), ('work_mode', 1))

    def test_weather_is_refused_between_its_two_requests(self):
        from personal_agent import local_tools
        tools = LocalTools(providers=ProviderRegistry.from_store(self.store))
        calls = []

        def request_json(url, body, timeout=None):
            calls.append(url)
            self.on()
            return {'results': [{'name': 'Seoul', 'latitude': 37.5, 'longitude': 127.0, 'population': 9_000_000,
                                 'country_code': 'KR', 'timezone': 'Asia/Seoul'}]}
        with mock.patch.object(local_tools, 'request_json', request_json), self.assertRaises(ValueError) as caught:
            tools.weather('Seoul')
        self.assertEqual(len(calls), 1, 'the forecast request was never sent')
        self.assertIsNot(getattr(caught.exception, 'ran', None), False, 'the city already went out: an attempt (#233)')
        with mock.patch.object(local_tools, 'request_json', request_json), self.assertRaises(ValueError) as caught:
            tools.weather('Seoul')
        self.assertIs(caught.exception.ran, False)
        self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
