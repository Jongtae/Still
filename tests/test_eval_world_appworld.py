"""#484 piece A: model-free AppWorld lifecycle and protected-text boundary checks.

No AppWorld installation or servers are needed; port reservations use a socket
double. The separate opt-in test observes the installed sandbox world.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evals'))
MODULE_PATH = ROOT / 'evals/agentos_eval/worlds/appworld.py'


def load_module():
    spec = importlib.util.spec_from_file_location('_still_eval_appworld', MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


world_module = load_module()
AppWorldWorld = world_module.AppWorldWorld
SECRET = 'SECRET-TASK-TEXT'


class Response:
    status = 200

    def __init__(self, body):
        self.body = body

    def read(self):
        return json.dumps(self.body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        pass


class Process:
    def __init__(self, exited=False, stubborn=False):
        self.returncode = 1 if exited else None
        self.stubborn = stubborn
        self.terminated = False
        self.killed = False
        self.waits = []

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        if not self.stubborn:
            self.returncode = -15

    def wait(self, timeout):
        self.waits.append(timeout)
        if self.returncode is None:
            raise subprocess.TimeoutExpired('fake-server', timeout)
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


class Transports:
    def __init__(self, *, fail=None, exited=False, stubborn=False):
        self.fail = fail
        self.exited = exited
        self.stubborn = stubborn
        self.starts = []
        self.processes = []
        self.calls = []

    def popen(self, argv, **kwargs):
        process = Process(exited=self.exited and not self.starts, stubborn=self.stubborn)
        self.starts.append((argv, kwargs))
        self.processes.append(process)
        return process

    def urlopen(self, request, timeout):
        url = request if isinstance(request, str) else request.full_url
        path = urlsplit(url).path
        body = None if isinstance(request, str) else json.loads(request.data)
        self.calls.append((url, path, body, timeout))
        if path == self.fail:
            raise OSError(SECRET)
        if path == '/initialize':
            return Response({'output': {'task_id': '82e2fac_1', 'instruction': SECRET,
                                        'supervisor': {}, 'datetime': '2026-01-01'}})
        if path == '/evaluate':
            return Response({'output': {'success': False, 'num_tests': 4, 'difficulty': 1,
                                        'passes': [{'label': 'pass', 'requirement': SECRET}],
                                        'failures': [
                                            {'label': label, 'requirement': SECRET, 'trace': SECRET}
                                            for label in ('no_op_fail', 'no_op_pass',
                                                          SECRET + ' long free text' * 10)]}})
        if path not in ('/docs', '/', '/save', '/close_all'):
            raise AssertionError('unexpected request path')
        return Response({})


class AppWorldAdapter(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        # The execution sandbox can deny even bind(..., 0); model-free tests
        # verify the reservation addresses without needing OS network access.
        self.binds = []
        binds = self.binds

        class PortReservation:
            def bind(self, address):
                binds.append(address)
                self.port = 19000 + len(binds)

            def getsockname(self):
                return ('127.0.0.1', self.port)

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                pass

        patcher = patch.object(world_module.socket, 'socket', PortReservation)
        patcher.start()
        self.addCleanup(patcher.stop)

    def world(self, fake=None, **kwargs):
        fake = fake or Transports()
        return AppWorldWorld('82e2fac_1', root=self.root, popen=fake.popen,
                             urlopen=fake.urlopen, **kwargs)

    def test_mcp_shape_loopback_and_minimal_child_environment(self):
        fake = Transports()
        world = self.world(fake)
        with self.assertRaises(RuntimeError):
            world.mcp_server()
        with world:
            config = world.mcp_server()
            api_port = fake.starts[0][0][-1]
            self.assertEqual(config, {
                'command': str(self.root / 'venv/bin/python'),
                'args': ['-m', 'appworld.cli', 'serve', 'mcp', 'stdio', '--remote-apis-url',
                         f'http://127.0.0.1:{api_port}', '--root', str(self.root),
                         '--output-type', 'content_only'],
                'env': {'APPWORLD_ROOT': str(self.root)}})
            self.assertTrue(all(isinstance(value, str) for value in
                                [config['command'], *config['args'], *config['env'].values()]))
            self.assertNotEqual(fake.starts[0][0][-1], fake.starts[1][0][-1])
            self.assertEqual(self.binds, [('127.0.0.1', 0), ('127.0.0.1', 0)])
            for (argv, options), app in zip(fake.starts, ('appworld.serve._apis:app',
                                                        'appworld.serve.environment:app')):
                self.assertEqual(argv[1:4], ['-m', 'uvicorn', app])
                self.assertEqual(argv[argv.index('--host') + 1], '127.0.0.1')
                self.assertNotIn('0.0.0.0', argv)
                self.assertNotIn('localhost', argv)
                self.assertEqual(options['cwd'], str(self.root))
                self.assertEqual(options['env']['APPWORLD_ROOT'], str(self.root))
                self.assertEqual(options['env']['ON_DISK'], 'false')
                self.assertLessEqual(set(options['env']),
                                     {'PATH', 'HOME', 'TMPDIR', 'LANG', 'APPWORLD_ROOT', 'ON_DISK'})
                self.assertEqual(options['stdout'], subprocess.DEVNULL)
                self.assertEqual(options['stderr'], subprocess.DEVNULL)
            initialize = next(call for call in fake.calls if call[1] == '/initialize')
            self.assertEqual(initialize[2], {'task_id': '82e2fac_1',
                                            'experiment_name': world.experiment_name,
                                            'remote_apis_url': f'http://127.0.0.1:{api_port}'})
            self.assertRegex(world.experiment_name, r'^still-eval-[0-9a-f]{8}$')
            self.assertNotIn('/evaluate', [call[1] for call in fake.calls])
        self.assertTrue(all(call[0].startswith('http://127.0.0.1:') for call in fake.calls))
        self.assertTrue(all(process.terminated for process in fake.processes))
        with self.assertRaises(RuntimeError):
            world.mcp_server()

    def test_initialize_error_cleans_up_and_does_not_expose_response_text(self):
        fake = Transports(fail='/initialize')
        world = self.world(fake)
        with self.assertRaisesRegex(RuntimeError, 'initialize') as caught:
            world.__enter__()
        self.assertNotIn(SECRET, str(caught.exception))
        self.assertTrue(all(process.terminated for process in fake.processes))
        calls = len(fake.calls)
        world.__exit__(None, None, None)
        self.assertEqual(len(fake.calls), calls)

    def test_startup_exit_terminates_the_other_process(self):
        fake = Transports(exited=True)
        with self.assertRaisesRegex(RuntimeError, 'exited during startup'):
            with self.world(fake):
                self.fail('startup should fail')
        self.assertEqual(len(fake.processes), 2)
        self.assertTrue(fake.processes[1].terminated)

    def test_readiness_timeout_cleans_up(self):
        fake = Transports()
        with self.assertRaisesRegex(RuntimeError, 'readiness timed out'):
            with self.world(fake, startup_timeout=0):
                self.fail('startup should fail')
        self.assertTrue(all(process.terminated for process in fake.processes))

    def test_second_spawn_failure_cleans_up_first_process(self):
        fake = Transports()

        def popen(argv, **kwargs):
            if fake.starts:
                raise OSError('spawn failed')
            return fake.popen(argv, **kwargs)

        world = self.world(fake)
        world._popen = popen
        with self.assertRaisesRegex(OSError, 'spawn failed'):
            world.__enter__()
        self.assertTrue(fake.processes[0].terminated)

    def test_body_error_and_stubborn_process_cleanup(self):
        fake = Transports(stubborn=True, fail='/close_all')
        with self.assertRaisesRegex(ValueError, 'body failed'):
            with self.world(fake):
                raise ValueError('body failed')
        for process in fake.processes:
            self.assertTrue(process.terminated)
            self.assertTrue(process.killed)
            self.assertEqual(process.waits, [10, 10])

    def test_label_only_result_and_evaluate_once_after_save(self):
        fake = Transports()
        with self.world(fake) as world:
            self.assertEqual(world.instruction, SECRET)
            with self.assertRaises(AttributeError):
                world.instruction = 'changed'
            self.assertNotIn(SECRET, repr(world))
            result = world.evaluate()
            self.assertEqual(result, {'passed': False, 'num_tests': 4,
                                      'failures': ['no_op_fail', 'unlabelled'],
                                      'collateral': ['no_op_pass']})
            self.assertNotIn(SECRET, json.dumps(result))
            self.assertEqual([call[1] for call in fake.calls][-2:], ['/save', '/evaluate'])
            self.assertEqual([call[2] for call in fake.calls][-2:], [{'task_id': '82e2fac_1'}] * 2)
            calls = len(fake.calls)
            self.assertEqual(world.evaluate(), result)
            result['failures'].append('caller mutation')
            self.assertEqual(world.evaluate()['failures'], ['no_op_fail', 'unlabelled'])
            self.assertEqual(len(fake.calls), calls)

    def test_failed_evaluation_cannot_be_retried(self):
        fake = Transports(fail='/evaluate')
        with self.world(fake) as world:
            with self.assertRaisesRegex(RuntimeError, 'evaluation failed'):
                world.evaluate()
            calls = len(fake.calls)
            with self.assertRaisesRegex(RuntimeError, 'already attempted'):
                world.evaluate()
            self.assertEqual(len(fake.calls), calls)

    def test_constructor_boundaries_and_interpreter_override(self):
        for root in (ROOT, ROOT / 'appworld-data'):
            with self.subTest(root=root), self.assertRaises(ValueError):
                AppWorldWorld('82e2fac_1', root=root)
        for task in ('../82e2fac_1', '82E2fac_1', '82e2fac_1\n', '82e2fac_', ''):
            with self.subTest(task=task), self.assertRaises(ValueError):
                AppWorldWorld(task, root=self.root)
        custom = self.root / 'custom-python'
        with self.world(python=custom) as world:
            self.assertEqual(world.mcp_server()['command'], str(custom))

    def test_import_does_not_load_appworld_package(self):
        previous = sys.modules.pop('appworld', None)
        try:
            loaded = load_module()
            self.assertNotIn('appworld', sys.modules)
            self.assertEqual(loaded.APPWORLD_COMMIT, '42b5bcf3cd334fee33f0c37c02070a9f5807add5')
        finally:
            if previous is not None:
                sys.modules['appworld'] = previous


INTEGRATION_ROOT = os.environ.get('AGENTOS_EVAL_APPWORLD_ROOT')


@unittest.skipUnless(INTEGRATION_ROOT and (Path(INTEGRATION_ROOT).expanduser() / 'venv/bin/python').is_file(),
                     'opt in with AGENTOS_EVAL_APPWORLD_ROOT and a separate AppWorld venv')
class InstalledAppWorld(unittest.TestCase):
    def test_train_task_without_agent_action(self):
        with AppWorldWorld('82e2fac_1', root=Path(INTEGRATION_ROOT)) as world:
            self.assertEqual(world.mcp_server()['args'][:5], ['-m', 'appworld.cli', 'serve', 'mcp', 'stdio'])
            processes = list(world._processes)
            result = world.evaluate()
            self.assertFalse(result['passed'])
            self.assertGreaterEqual(result['num_tests'], 1)
            self.assertIn('no_op_fail', result['failures'])
        self.assertTrue(all(process.poll() is not None for process in processes))


if __name__ == '__main__':
    unittest.main()
