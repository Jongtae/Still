"""#1143 (tracker #1140): iMessage in the service and Settings.

The real ``IMessageSidecar`` launcher (#1142) runs against the fake child
process of ``test_imessage_sidecar`` (protocol v1 over fake stdio), so the
service, the surface's supervisor and the launcher are exercised together.
Nothing here runs Node, reads ``~/Library/Messages/chat.db`` or runs
``osascript``.  The model is a fixture; no network.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from personal_agent import imessage_sidecar as sidecar_module
from personal_agent import imessage_surface
from personal_agent.imessage_surface import INFORMATION_ROUTE, IMessageSurface
from personal_agent.information_use import render_korean
from personal_agent.providers import ModelAdapter
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore
from test_imessage_sidecar import PIN, FakePopen, fake_node, installed_target

OWNER = '+821055550000'
ANSWER = '오늘은 비가 와요.'
ROUTE = "iMessage via this Mac's Messages app (Apple)"


def model(url, body, headers=None, timeout=60):
    tools = [t.get('function', {}).get('name') or t.get('name') for t in body.get('tools', [])]
    if 'agentos_connection_probe' in tools:
        return {'message': {'content': '', 'tool_calls': [
            {'id': 'probe', 'function': {'name': 'agentos_connection_probe', 'arguments': {}}}]}}
    return {'message': {'content': ANSWER}}


def wait_for(condition, seconds=5.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


class Case(unittest.TestCase):
    """A service whose iMessage surface supervises a real launcher over a fake child."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = QuickStore(Path(temp.name) / 'data')
        self.service = AgentService(self.store, ModelAdapter(model), lambda *a, **k: {'ok': True, 'result': True})
        self.service.steer_spawn = lambda target: target()
        self.service.save_model({'provider': 'ollama', 'endpoint': 'http://127.0.0.1:11434', 'model': 'test-model',
                                 'api_key': ''})
        self.assertTrue(self.service.test_model()['ok'])
        # A supported Mac, whatever host runs the test (CI is Linux).
        patcher = mock.patch.object(sidecar_module, 'prebuild_pin', return_value=PIN)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.popen = FakePopen()
        self.launchers = []
        node = fake_node(self.store.root)

        def factory(store):
            car = sidecar_module.IMessageSidecar(store, popen=self.popen, provide_node=lambda directory: node,
                                                install=installed_target, sleep=lambda seconds: None, ready_seconds=2)
            self.launchers.append(car)
            return car

        self.service.imessage_sidecar_factory = factory
        self.surface = self.service.imessage_surface
        self.surface.starter_spawn = lambda target: target()   # start synchronously in the tick
        self.addCleanup(self.service.stop_surfaces)

    # -- helpers ------------------------------------------------------------------------
    def process(self, index=-1):
        return self.popen.processes[index]

    def ops(self, index=-1):
        return self.process(index).ops

    def tick_until(self, condition, seconds=5.0):
        return wait_for(lambda: (self.surface.receive(), condition())[1], seconds)

    def pair(self, handle=OWNER):
        started = self.service.start_imessage_pairing()
        self.assertTrue(started['code'])
        self.surface.receive()   # starts the sidecar and installs the pairing scope
        self.assertTrue(wait_for(lambda: any(op.get('mode') == 'pairing' for op in self.ops())))
        self.process().stdout.push({'ev': 'message', 'guid': f'G-pair-{time.time_ns()}', 'handle': handle, 'text': started['code'],
                                    'ts_ms': int(time.time() * 1000) + 1, 'reaction': None})
        self.assertTrue(self.tick_until(lambda: self.surface.config().get('handle') == handle))
        return started

    def owner_says(self, text, guid):
        self.process().stdout.push({'ev': 'message', 'guid': guid, 'handle': OWNER, 'text': text,
                                    'ts_ms': int(time.time() * 1000) + 2, 'reaction': None})
        self.assertTrue(self.tick_until(lambda: any(job['message'] == text for job in self.im_jobs())))
        return next(job for job in self.im_jobs() if job['message'] == text)

    def im_jobs(self):
        return [job for job in self.store.jobs() if job['channel'].startswith('imessage:')]

    def sends(self, index=-1):
        return [op for op in self.ops(index) if op.get('op') == 'send']


class Offer(Case):
    def test_a_supported_mac_offers_imessage_in_settings(self):
        status = self.service.settings()['imessage']
        self.assertTrue(status['offered'])
        self.assertEqual(status['state'], 'off')
        self.assertEqual(self.popen.calls, [], 'nothing starts before the owner asks to pair')

    def test_on_another_host_the_surface_is_not_offered_and_nothing_starts(self):
        with mock.patch.object(sidecar_module, 'prebuild_pin', return_value=None):
            self.assertFalse(self.surface.offered())
            self.assertFalse(self.service.settings()['imessage']['offered'])
            with self.assertRaises(ValueError):
                self.service.start_imessage_pairing()
            # Even a pairing left in the store from elsewhere starts nothing here.
            self.store.put(imessage_surface.CONFIG_KEY, {'pairing': {'digest': 'x', 'expires_at': time.time() + 600,
                                                                     'attempts': 0, 'since_ms': 1}})
            self.surface.receive()
            self.service.receive_polled_surfaces()
        self.assertEqual(self.launchers, [], 'no sidecar was constructed')
        self.assertEqual(self.popen.calls, [], 'no process started')

    def test_the_service_composes_one_surface_and_starts_it_only_when_wanted(self):
        self.assertIsInstance(self.surface, IMessageSurface)
        self.service.receive_polled_surfaces()
        self.assertEqual(self.popen.calls, [])
        self.pair()
        self.assertEqual(len(self.popen.calls), 1, 'one sidecar for pairing and the paired conversation')
        self.assertEqual(self.surface.status()['state'], 'connected')
        self.assertEqual(self.surface.status()['handle'], OWNER)

    def test_stopping_the_service_turns_reading_off_and_stops_the_sidecar(self):
        self.pair()
        process = self.process()
        self.service.stop_surfaces()
        scopes = [index for index, op in enumerate(process.ops) if op == {'op': 'scope', 'mode': 'off'}]
        stops = [index for index, op in enumerate(process.ops) if op.get('op') == 'stop']
        self.assertTrue(scopes and stops and scopes[-1] < stops[0], process.ops)
        self.assertIsNotNone(process.returncode)


class Pairing(Case):
    def test_settings_shows_the_code_once_and_its_expiry_and_then_the_paired_handle(self):
        started = self.service.start_imessage_pairing()
        self.assertGreater(started['expires_at'], time.time())
        status = self.service.imessage_status()
        self.assertTrue(status['pairing'])
        self.assertEqual(status['pairing_expires_at'], started['expires_at'])
        self.assertNotIn(started['code'], json.dumps(self.service.settings(), ensure_ascii=False),
                         'the code is shown only in the response that made it')
        self.service.cancel_imessage_pairing()
        self.assertFalse(self.service.imessage_status()['pairing'])

    def test_a_pairing_code_that_arrives_over_the_real_launcher_pairs_the_handle(self):
        self.pair()
        self.assertTrue(self.surface.status()['usable'])
        self.assertTrue(any(op.get('mode') == 'paired' and op.get('handle') == OWNER for op in self.ops()))


class DiskAccess(Case):
    def no_access(self):
        self.popen.process_kwargs['answer'] = [{'ev': 'error', 'code': 'no_disk_access',
                                                'detail': 'authorization denied /Users/someone/Library/Messages/chat.db'}]

    def test_missing_full_disk_access_is_a_visible_specific_status_naming_the_app(self):
        self.no_access()
        with mock.patch.object(imessage_surface, 'disk_access_target',
                               return_value={'name': 'Terminal', 'path': '/System/Applications/Utilities/Terminal.app'}):
            self.service.start_imessage_pairing()
            self.surface.receive()
            status = self.service.imessage_status()
        self.assertEqual(status['state'], 'error')
        self.assertEqual(status['code'], 'no_disk_access')
        self.assertEqual(status['disk_access_app'], {'name': 'Terminal',
                                                     'path': '/System/Applications/Utilities/Terminal.app'})
        self.assertIn('Terminal', status['message'])
        self.assertNotIn('chat.db', json.dumps(status), 'the sidecar detail is not shown')
        self.assertIsNotNone(self.process().returncode, 'the refused child was stopped')

    def test_a_failed_start_is_retried_only_after_backoff_or_when_the_owner_asks(self):
        self.no_access()
        self.service.start_imessage_pairing()
        self.surface.receive()
        self.surface.receive()
        self.assertEqual(len(self.popen.calls), 1, 'no tight restart loop')
        self.popen.process_kwargs.pop('answer')
        self.service.retry_imessage()
        self.surface.receive()
        self.assertTrue(wait_for(lambda: self.service.imessage_status()['state'] == 'connected'))
        self.assertEqual(len(self.popen.calls), 2)
        self.assertNotIn('code', self.service.imessage_status(), 'a ready start clears the error')

    def test_messages_not_signed_in_is_its_own_status(self):
        self.popen.process_kwargs['answer'] = [{'ev': 'error', 'code': 'messages_not_signed_in', 'detail': ''}]
        self.service.start_imessage_pairing()
        self.surface.receive()
        status = self.service.imessage_status()
        self.assertEqual((status['state'], status['code']), ('error', 'messages_not_signed_in'))
        self.assertTrue(status['message'])
        self.assertNotEqual(status['message'], self.surface._status_message('no_disk_access'))

    def test_a_wanted_surface_is_never_silently_off(self):
        held = []
        self.surface.starter_spawn = held.append   # the start has not finished yet
        self.service.start_imessage_pairing()
        self.surface.receive()
        self.assertEqual(self.service.imessage_status()['state'], 'starting')
        held[0]()
        self.assertEqual(self.service.imessage_status()['state'], 'connected')
        # The child dies and every restart fails: shown as an error, not as off.
        self.launchers[-1]._max_restarts = 0
        self.process().exit(1)
        self.assertTrue(self.tick_until(lambda: self.service.imessage_status()['state'] == 'error'))
        self.assertEqual(self.service.imessage_status()['code'], 'exited')

    def test_the_named_app_is_the_responsible_app_not_node(self):
        # Terminal → login → zsh → python: macOS checks the app that launched the chain.
        chain = {100: (90, '/opt/homebrew/Cellar/python@3.13/3.13.1/Frameworks/Python.framework/Versions/3.13/bin/python3.13'),
                 90: (80, '/bin/zsh'), 80: (70, '/usr/bin/login'),
                 70: (1, '/System/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal')}
        target = imessage_surface.disk_access_target(pid=100, parent_of=lambda pid: chain[pid])
        self.assertEqual(target, {'name': 'Terminal', 'path': '/System/Applications/Utilities/Terminal.app'})
        # A launchd service (parent pid 1) is its own responsible process: its executable image.
        service = {200: (1, '/opt/homebrew/Cellar/python@3.13/3.13.1/Frameworks/Python.framework/Versions/3.13/'
                            'Resources/Python.app/Contents/MacOS/Python')}
        self.assertEqual(imessage_surface.disk_access_target(pid=200, parent_of=lambda pid: service[pid]),
                         {'name': 'Python', 'path': '/opt/homebrew/Cellar/python@3.13/3.13.1/Frameworks/'
                                                    'Python.framework/Versions/3.13/Resources/Python.app'})
        plain = {300: (1, '/Users/someone/.pyenv/versions/3.13.1/bin/python3.13')}
        self.assertEqual(imessage_surface.disk_access_target(pid=300, parent_of=lambda pid: plain[pid]),
                         {'name': 'python3.13', 'path': '/Users/someone/.pyenv/versions/3.13.1/bin/python3.13'})

    def test_shell_and_command_names_in_the_chain_do_not_hide_the_app(self):
        # ``ps -o comm=`` reports argv[0] for some processes (``-zsh``, ``python3``); the walk keeps going.
        chain = {100: (90, 'python3'), 90: (80, '-zsh'), 80: (70, '/usr/bin/login'),
                 70: (1, '/System/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal')}
        self.assertEqual(imessage_surface.disk_access_target(pid=100, parent_of=lambda pid: chain[pid]),
                         {'name': 'Terminal', 'path': '/System/Applications/Utilities/Terminal.app'})

    @unittest.skipUnless(sys.platform == 'darwin', 'reads this process chain on macOS')
    def test_the_default_walk_reads_real_executable_paths(self):
        target = imessage_surface.disk_access_target()
        self.assertTrue(os.path.isabs(target['path']), target)

    def test_status_reads_the_process_chain_once(self):
        self.popen.process_kwargs['answer'] = [{'ev': 'error', 'code': 'no_disk_access', 'detail': ''}]
        with mock.patch.object(imessage_surface, 'disk_access_target',
                               return_value={'name': 'Terminal', 'path': '/Applications/Terminal.app'}) as walk:
            self.service.start_imessage_pairing()
            self.surface.receive()
            for _ in range(5):
                self.service.imessage_status()
                self.service.settings()
        self.assertEqual(walk.call_count, 1, 'a process ancestry does not change; it is read once')

    def test_an_unreadable_process_chain_still_names_something(self):
        def broken(pid):
            raise OSError('no such process')
        target = imessage_surface.disk_access_target(pid=400, parent_of=broken)
        self.assertTrue(target['name'])
        self.assertIs(target['uncertain'], True, 'a guess is labelled as one')
        self.assertNotIn('uncertain', imessage_surface.disk_access_target(
            pid=300, parent_of=lambda pid: (1, '/Users/someone/.pyenv/versions/3.13.1/bin/python3.13')))
        self.popen.process_kwargs['answer'] = [{'ev': 'error', 'code': 'no_disk_access', 'detail': ''}]
        with mock.patch.object(imessage_surface, 'disk_access_target', return_value=target):
            self.service.start_imessage_pairing()
            self.surface.receive()
            status = self.service.imessage_status()
        self.assertEqual(status['message'], imessage_surface.msg('imessage.no_disk_access_uncertain', app=target['name']))
        self.assertTrue(status['disk_access_app']['uncertain'])


class Disconnect(Case):
    def test_disconnect_turns_reading_off_stops_the_sidecar_and_ends_the_generation(self):
        self.pair()
        generation = self.surface.config()['generation']
        process = self.process()
        self.service.disconnect_imessage()
        scopes = [index for index, op in enumerate(process.ops) if op == {'op': 'scope', 'mode': 'off'}]
        stops = [index for index, op in enumerate(process.ops) if op.get('op') == 'stop']
        self.assertTrue(scopes and stops and scopes[-1] < stops[0], 'scope off is written before stop')
        self.assertIsNotNone(process.returncode)
        self.assertNotEqual(self.surface.config().get('generation'), generation)
        self.assertEqual(self.service.imessage_status()['state'], 'off')
        # Nothing is read any more, even if the old child still prints a row.
        process.stdout.push({'ev': 'message', 'guid': 'G-late', 'handle': OWNER, 'text': '끊은 뒤',
                             'ts_ms': int(time.time() * 1000) + 5, 'reaction': None})
        self.surface.receive()
        self.service.receive_polled_surfaces()
        self.assertEqual([job for job in self.im_jobs() if job['message'] == '끊은 뒤'], [])
        self.assertEqual(len(self.popen.calls), 1, 'nothing restarts while off')

    def test_a_reply_of_the_current_generation_is_sent(self):
        # The control for the test below: the same steps without a disconnect do send the reply.
        self.pair()
        self.owner_says('날씨 어때?', 'G-ask')
        self.assertTrue(self.service.run_one())
        self.service.deliver_one()
        self.assertEqual([op['to'] for op in self.sends() if op['text'] == ANSWER], [OWNER])

    def test_a_reply_of_the_old_generation_is_dropped(self):
        self.pair()
        job = self.owner_says('날씨 어때?', 'G-ask')
        self.assertTrue(self.service.run_one())
        self.service.disconnect_imessage()
        # A new pairing on a new, live sidecar does not inherit the old reply.
        self.pair()
        self.assertEqual(len(self.popen.calls), 2)
        self.service.deliver_one()
        self.surface.receive()
        self.assertEqual([op for op in self.sends() if op['text'] == ANSWER], [])
        self.assertNotEqual(self.store.job(job['id']).get('delivery'), 'sent')


    def test_a_child_that_stopped_reading_does_not_hang_disconnect(self):
        self.pair()
        car = self.launchers[-1]
        stopped = threading.Event()
        blocked = threading.Event()
        real_stop = car.stop

        def stuck(op):
            blocked.wait(30)   # a full stdin pipe
            return False

        def stop():
            stopped.set()
            real_stop()

        car.send_op, car.stop = stuck, stop
        started = time.monotonic()
        with mock.patch.object(imessage_surface, 'SCOPE_OFF_SECONDS', 0.2):
            self.service.disconnect_imessage()
        blocked.set()
        self.assertTrue(stopped.is_set(), 'stop() is reached')
        self.assertLess(time.monotonic() - started, 5)


class WorkMode(Case):
    def test_work_mode_pauses_a_paired_surface_visibly_and_the_owner_can_approve_it(self):
        self.pair()
        self.service.set_work_mode({'enabled': True})
        self.surface.receive()
        status = self.service.imessage_status()
        self.assertEqual(status['state'], 'paused')
        self.assertTrue(status['message'])
        self.assertIsNotNone(self.process().returncode, 'the sidecar stops while paused')
        self.service.set_imessage_company_approved({'approved': True})
        self.assertTrue(self.tick_until(lambda: self.service.imessage_status()['state'] == 'connected'))
        self.assertEqual(len(self.popen.calls), 2)
        self.service.set_imessage_company_approved({'approved': False})
        self.surface.receive()
        self.assertEqual(self.service.imessage_status()['state'], 'paused')


class LostAccess(Case):
    def test_losing_disk_access_while_running_names_disk_access_not_a_generic_exit(self):
        self.pair()
        self.launchers[-1]._max_restarts = 1
        self.popen.process_kwargs['answer'] = [{'ev': 'error', 'code': 'no_disk_access', 'detail': 'denied'}]
        self.process().exit(1)
        self.assertTrue(self.tick_until(lambda: self.service.imessage_status()['state'] == 'error'))
        self.assertEqual(self.service.imessage_status()['code'], 'no_disk_access')
        self.assertIn('disk_access_app', self.service.imessage_status())


class InformationRoute(Case):
    def test_the_information_use_record_names_the_imessage_route(self):
        self.assertEqual(INFORMATION_ROUTE, ROUTE)
        self.assertEqual(IMessageSurface.information_route, ROUTE)
        self.pair()
        job = self.owner_says('비 와?', 'G-rain')
        self.assertTrue(self.service.run_one())
        audit = self.service.work_information_use(job['id'])
        self.assertEqual(audit['sent_to']['conversation'], {'surface': 'imessage', 'route': ROUTE})
        self.assertIn(ROUTE, render_korean(audit))

    @unittest.skipUnless(shutil.which('node'), 'Node renders the web audit')
    def test_the_web_audit_shows_the_route_too(self):
        script = r"""
const ui=require(process.argv[1]);
class Element{constructor(){this.children=[];this.textContent='';}append(...nodes){this.children.push(...nodes);}}
global.document={createElement:()=>new Element()};
const box=new Element();ui.informationUseView({information_use:{sent_to:{conversation:{surface:'imessage',route:process.argv[2]}}}},box);
const facts=box.children[0].children,index=facts.findIndex(row=>row.textContent===ui.t('대화 채널'));
if(index<0||facts[index+1].textContent!==process.argv[2])process.exit(3);
"""
        web = Path(imessage_surface.__file__).with_name('web') / 'app.js'
        subprocess.run([shutil.which('node'), '-e', script, str(web), ROUTE], check=True, capture_output=True, timeout=20)

    def test_a_work_from_elsewhere_names_no_imessage_route(self):
        for surface in self.service.surfaces:
            if surface.name != 'imessage':
                self.assertNotEqual(getattr(surface, 'information_route', None), ROUTE)
        job_id = self.store.enqueue('웹에서 물어본 것', 'web-question')
        self.assertTrue(self.service.run_one())
        audit = self.service.work_information_use(job_id)
        self.assertNotIn('conversation', audit['sent_to'])
        self.assertNotIn(ROUTE, render_korean(audit))


class SettingsPage(unittest.TestCase):
    def test_the_settings_page_has_the_imessage_section_and_endpoints(self):
        web = Path(imessage_surface.__file__).with_name('web')
        html = (web / 'index.html').read_text(encoding='utf-8')
        script = (web / 'app.js').read_text(encoding='utf-8')
        for marker in ('id="imessage"', 'id="imessage-pair"', 'id="imessage-disconnect"', 'id="imessage-retry"',
                       'id="imessage-status"', 'id="imessage-company-approved"'):
            self.assertIn(marker, html)
        self.assertIn('settings?.imessage', script.replace('settings.imessage', 'settings?.imessage'))
        # A section the host does not offer is hidden, and the add-connection chooser skips hidden sections.
        self.assertIn('<section id="imessage"', html)
        self.assertRegex(html, r'<section id="imessage"[^>]*\bhidden\b')
        chooser = script[script.index('function openAdd('):script.index('function showAddType(')]
        self.assertIn('if(node.hidden)continue;', chooser)
        server = (Path(imessage_surface.__file__).with_name('quickstart.py')).read_text(encoding='utf-8')
        for path in ('/api/imessage/pair', '/api/imessage/pair/cancel', '/api/imessage/disconnect', '/api/imessage/retry',
                     '/api/imessage/company-approved'):
            self.assertIn(f"'{path}'", server)


if __name__ == '__main__':
    unittest.main()
