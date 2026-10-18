"""#105: first setup offers once to register the macOS login service, and hands over to it."""
import fcntl
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from personal_agent import first_run, quickstart
from personal_agent.quickstart import hand_over_to_login_service, login_service_status
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore
from test_first_run import FakeService, ScriptedTerminal

# Enter on the password, AI "later" and Telegram questions; the login question follows.
SKIP_TO_LOGIN = ['', '4', '']


class FakeServiceAction:
    def __init__(self, status='not_installed', install_ok=True):
        self.status, self.install_ok = status, install_ok
        self.calls = []

    def __call__(self, action, **options):
        self.calls.append((action, options))
        if action == 'status':
            return {'ok': True, 'status': self.status}
        if self.install_ok:
            return {'ok': True, 'status': 'running', 'operation': 'install'}
        return {'ok': False, 'error': 'A running Still background process was not observed.',
                'next_action': 'Run the service restart action.'}

    def installs(self):
        return [options for action, options in self.calls if action == 'install']


def start_args(**overrides):
    values = {'host': '127.0.0.1', 'port': 8787, 'drive_handoff_port': None, 'public_tunnel_host': [],
              'data': '/owner/data'}
    return SimpleNamespace(**{**values, **overrides})


class LoginServiceOfferTest(unittest.TestCase):
    def setUp(self):
        self.fresh()

    def fresh(self, action=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = QuickStore(Path(temp.name) / 'data')
        self.action = action or FakeServiceAction()

    def setup_then_hand_over(self, asks, *, platform='darwin', interactive=True, args=None):
        """What `agentos start` does: run first setup, then hand over on a recorded yes."""
        term = ScriptedTerminal(asks=asks)
        probe = login_service_status(args or start_args(), platform=platform, interactive=interactive,
                                     action=self.action)
        finished = first_run.run(FakeService(), self.store, term, login_service=probe)
        released = []
        if finished and first_run.wants_login_service(self.store):
            hand_over_to_login_service('/owner/data', term, lambda: released.append(True), action=self.action)
        return finished, term, released

    def test_yes_registers_the_login_service_once(self):
        finished, term, released = self.setup_then_hand_over(SKIP_TO_LOGIN + ['y'])
        self.assertTrue(finished)
        self.assertEqual(self.action.installs(), [{'data_dir': '/owner/data'}])
        self.assertEqual(released, [True])
        self.assertIn(first_run.msg('first_run.login_started'), term.text())
        # A later start finds setup finished and neither asks nor installs again.
        self.assertTrue(first_run.finished(self.store))
        self.assertEqual(len(self.action.installs()), 1)

    def test_no_and_enter_register_nothing(self):
        for answer in ('n', ''):
            with self.subTest(answer=answer):
                self.fresh()
                finished, term, released = self.setup_then_hand_over(SKIP_TO_LOGIN + [answer])
                self.assertTrue(finished)
                self.assertIn(first_run.msg('first_run.login_title'), term.text())
                self.assertEqual(self.action.installs(), [])
                self.assertEqual(released, [])
                self.assertFalse(first_run.wants_login_service(self.store))

    def test_the_question_is_shown_with_its_title(self):
        _, term, _ = self.setup_then_hand_over(SKIP_TO_LOGIN + [''])
        self.assertIn(first_run.msg('first_run.login_title'), term.text())

    def test_non_macos_and_non_terminal_starts_never_ask(self):
        for platform, interactive in (('linux', True), ('darwin', False)):
            with self.subTest(platform=platform, interactive=interactive):
                self.fresh()
                # The ScriptedTerminal would fail on a fourth question.
                finished, term, _ = self.setup_then_hand_over(SKIP_TO_LOGIN, platform=platform,
                                                              interactive=interactive)
                self.assertTrue(finished)
                self.assertNotIn(first_run.msg('first_run.login_title'), term.text())
                self.assertEqual(self.action.calls, [])

    def test_a_start_the_service_would_not_reproduce_never_asks(self):
        for override in ({'port': 8797}, {'host': '0.0.0.0'}, {'drive_handoff_port': 9000},
                         {'public_tunnel_host': ['still.example.org']}):
            with self.subTest(**{key: str(value) for key, value in override.items()}):
                self.assertIsNone(login_service_status(start_args(**override), platform='darwin',
                                                       interactive=True, action=self.action))

    def test_an_installed_service_is_not_offered_again(self):
        for status in ('running', 'stopped', 'loaded_not_running', 'unknown'):
            with self.subTest(status=status):
                self.fresh(FakeServiceAction(status=status))
                finished, term, _ = self.setup_then_hand_over(SKIP_TO_LOGIN)
                self.assertTrue(finished)
                self.assertNotIn(first_run.msg('first_run.login_title'), term.text())
                self.assertEqual(self.action.installs(), [])

    def test_the_status_read_names_this_starts_data_folder(self):
        self.setup_then_hand_over(SKIP_TO_LOGIN + [''])
        self.assertEqual(self.action.calls, [('status', {'data_dir': '/owner/data'})])

    def test_a_resumed_setup_does_not_ask_again(self):
        # The owner answered, then stopped before setup recorded itself finished.
        first_run._record(self.store, **{first_run.LOGIN_SERVICE: 'no'})
        finished, term, _ = self.setup_then_hand_over(SKIP_TO_LOGIN)
        self.assertTrue(finished)
        self.assertNotIn(first_run.msg('first_run.login_title'), term.text())
        self.assertEqual(self.action.calls, [])

    def test_finishing_keeps_the_recorded_answer(self):
        self.setup_then_hand_over(SKIP_TO_LOGIN + ['y'])
        self.assertEqual(self.store.config(first_run.SETUP_KEY, {}),
                         {first_run.LOGIN_SERVICE: 'yes', 'finished': True})


class HandOverTest(unittest.TestCase):
    def test_the_foreground_releases_its_port_and_lock_before_the_install(self):
        order = []
        action = FakeServiceAction()

        def recorded(name, **options):
            order.append(name)
            return action(name, **options)
        self.assertTrue(hand_over_to_login_service('/owner/data', ScriptedTerminal(),
                                                   lambda: order.append('release'), action=recorded))
        self.assertEqual(order, ['release', 'install'])

    def test_a_failed_install_reports_its_next_action_and_keeps_the_foreground(self):
        action = FakeServiceAction(install_ok=False)
        term = ScriptedTerminal()
        self.assertFalse(hand_over_to_login_service('/owner/data', term, lambda: None, action=action))
        self.assertIn(first_run.msg('first_run.login_failed', error='A running Still background process was not observed.',
                                    next_action='Run the service restart action.'), term.text())
        self.assertNotIn(first_run.msg('first_run.login_started'), term.text())


class StopServing(Exception):
    pass


class StartHandOverTest(unittest.TestCase):
    """`agentos start` itself: the port and data-folder lock go to the service, or stay here."""

    def start(self, answer, action):
        self.servers = servers = []
        test = self
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)

        class Server:
            server_port = 8787

            def __init__(self, *_args, **_kwargs):
                self.closed = self.served = False
                servers.append(self)

            def serve_forever(self):
                self.served = True
                # The foreground still holds the data-folder lock while it serves.
                self.lock_free = test.lock_is_free(Path(temp.name))
                raise StopServing()

            def server_close(self):
                self.closed = True

        term = ScriptedTerminal(asks=SKIP_TO_LOGIN + [answer])
        probe = lambda args: login_service_status(args, platform='darwin', interactive=True, action=action)
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(sys, 'argv', ['agentos', 'start', '--data', temp.name, '--no-browser']), \
             patch.object(sys.stdin, 'isatty', return_value=True), \
             patch.object(first_run, 'Terminal', return_value=term), \
             patch.object(quickstart, 'login_service_status', side_effect=probe), \
             patch.object(quickstart, 'hand_over_to_login_service',
                          side_effect=lambda data, t, release: hand_over_to_login_service(data, t, release,
                                                                                          action=action)), \
             patch.object(quickstart, 'ThreadingHTTPServer', Server), \
             patch.object(AgentService, 'start') as started, \
             patch.object(quickstart.signal, 'signal'), \
             patch('sys.stdout', io.StringIO()):
            try:
                code = quickstart.main()
            except StopServing:
                code = 'serving'
        return code, servers, started, Path(temp.name)

    @staticmethod
    def lock_is_free(data):
        with (data / 'private' / 'instance.lock').open('a') as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            fcntl.flock(handle, fcntl.LOCK_UN)
            return True

    def test_yes_hands_the_port_and_lock_to_the_service_and_exits(self):
        seen = {}
        action = FakeServiceAction()

        def install_after_release(name, **options):
            if name == 'install':
                seen['servers_closed'] = all(server.closed for server in self.servers)
                seen['lock_free'] = self.lock_is_free(Path(options['data_dir']))
            return action(name, **options)
        code, servers, started, _ = self.start('y', install_after_release)
        self.assertEqual(code, 0)
        self.assertEqual(seen, {'servers_closed': True, 'lock_free': True})
        self.assertEqual(len(action.installs()), 1)
        self.assertEqual(len(servers), 1)
        started.assert_not_called()

    def test_a_failed_install_keeps_serving_in_the_foreground(self):
        action = FakeServiceAction(install_ok=False)
        code, servers, started, _ = self.start('y', action)
        self.assertEqual(code, 'serving')
        self.assertEqual(len(action.installs()), 1)
        self.assertEqual([(server.closed, server.served) for server in servers], [(True, False), (True, True)])
        self.assertFalse(servers[1].lock_free)
        started.assert_called_once()

    def test_no_serves_in_the_foreground_without_installing(self):
        action = FakeServiceAction()
        code, servers, started, _ = self.start('', action)
        self.assertEqual(code, 'serving')
        self.assertEqual(action.installs(), [])
        self.assertEqual([server.served for server in servers], [True])
        self.assertFalse(servers[0].lock_free)
        started.assert_called_once()


if __name__ == '__main__':
    unittest.main()
