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
from types import ModuleType, SimpleNamespace
from pathlib import Path
from contextlib import asynccontextmanager
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evals'))
MODULE_PATH = ROOT / 'evals/agentos_eval/worlds/appworld.py'


def load_module(path=MODULE_PATH):
    spec = importlib.util.spec_from_file_location('_still_eval_' + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


world_module = load_module()
AppWorldWorld = world_module.AppWorldWorld
SECRET = 'SECRET-TASK-TEXT'


def write_fixture(root):
    datasets = root / 'data/datasets'
    datasets.mkdir(parents=True)
    (datasets / 'train.txt').write_text('82e2fac_1\n')
    (datasets / 'dev.txt').write_text('82e2fac_2\n')
    ground_truth = root / 'data/tasks/82e2fac_1/ground_truth'
    ground_truth.mkdir(parents=True)
    (ground_truth / 'required_apps.json').write_text(json.dumps(['simple_note', 'venmo']))
    docs_dir = root / 'data/api_docs/standard'
    docs_dir.mkdir(parents=True)
    docs = {
        'supervisor': {'show_account_passwords': 'GET', 'show_profile': 'GET'},
        'simple_note': {'login': 'POST', 'signup': 'POST', 'show_note': 'GET',
                        'update_note': 'PATCH', 'read_file': 'GET'},
        'venmo': {'create_transaction': 'POST', 'add_payment_card': 'POST'},
        'file_system': {'login': 'POST', 'show_file': 'GET'},
        'admin': {}, 'api_docs': {},
    }
    for app, apis in docs.items():
        (docs_dir / f'{app}.json').write_text(json.dumps({
            api: {'method': method, 'parameters': [
                {'name': 'card_number' if api == 'add_payment_card' else
                 'file_system_access_token' if api == 'read_file' else 'access_token'}]}
            for api, method in apis.items()}))


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
        write_fixture(self.root)
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
                'args': ['-I', str(MODULE_PATH.with_name('appworld_mcp.py')),
                         '--remote-apis-url', f'http://127.0.0.1:{api_port}',
                         '--root', str(self.root), '--apps', ','.join(world.apps)],
                'env': {'APPWORLD_ROOT': str(self.root)}})
            self.assertTrue(Path(config['args'][1]).is_absolute())
            self.assertTrue(Path(config['args'][1]).is_file())
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
            self.assertEqual(world.instruction(), SECRET)
            self.assertIn('supervisor__complete_task', world.completion_note)
            self.assertNotIn(SECRET, world.completion_note)
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

    def test_train_dev_only_and_missing_datasets_fail_closed(self):
        for task in ('82e2fac_1', '82e2fac_2'):
            self.assertEqual(AppWorldWorld(task, root=self.root, apps=[]).task_id, task)
        with self.assertRaisesRegex(ValueError, '^AppWorld task is not a train/dev task$'):
            AppWorldWorld('82e2fac_3', root=self.root)
        for split in ('train', 'dev'):
            path = self.root / 'data/datasets' / f'{split}.txt'
            text = path.read_text()
            path.unlink()
            with self.assertRaisesRegex(ValueError, '^AppWorld task is not a train/dev task$'):
                self.world()
            path.write_text(text)

    def test_apps_oracle_and_explicit_override(self):
        self.assertEqual(self.world().apps, ('simple_note', 'supervisor', 'venmo'))
        path = self.root / 'data/tasks/82e2fac_1/ground_truth/required_apps.json'
        path.write_text(json.dumps(['admin', 'api_docs', 'simple_note', 'simple_note']))
        self.assertEqual(self.world().apps, ('simple_note', 'supervisor'))
        path.unlink()
        self.assertEqual(self.world(apps=['venmo']).apps, ('supervisor', 'venmo'))
        self.assertEqual(self.world(apps=[]).apps, ('supervisor',))
        for app in ('../venmo', 'Venmo', '', 'a' * 31, 'venmo\n', 'missing', 123):
            with self.subTest(app=app), self.assertRaises(ValueError):
                self.world(apps=[app])

    def test_operations_before_enter_and_shared_exposure(self):
        operations = self.world().operations()
        self.assertEqual(operations, {
            'simple_note__show_note': 'read', 'simple_note__update_note': 'mutate',
            'simple_note__read_file': 'read', 'supervisor__show_profile': 'read',
            'venmo__create_transaction': 'mutate', 'venmo__add_payment_card': 'mutate'})
        for name in operations:
            self.assertRegex(name, r'^[A-Za-z0-9_.-]{1,60}$')
        for api in world_module.ACCOUNT_APIS:
            self.assertFalse(world_module.exposed('venmo', api))
        self.assertFalse(world_module.exposed('supervisor', 'show_account_passwords'))

    def test_strip_credentials_is_independent_copy(self):
        schema = {'type': 'object', 'properties': {
            'access_token': {'type': 'string'}, 'file_system_access_token': {'type': 'string'},
            'card_number': {'type': 'string'}},
            'required': ['access_token', 'file_system_access_token', 'card_number']}
        before = json.loads(json.dumps(schema))
        stripped = world_module.strip_credentials(schema)
        self.assertEqual(stripped['properties'], {'card_number': {'type': 'string'}})
        self.assertEqual(stripped['required'], ['card_number'])
        stripped['properties']['card_number']['type'] = 'number'
        self.assertEqual(schema, before)
        self.assertEqual(world_module.strip_credentials({}), {})


class AppWorldMCP(unittest.TestCase):
    def setUp(self):
        self.module = load_module(MODULE_PATH.with_name('appworld_mcp.py'))
        self.schema = {'properties': {'access_token': {}, 'file_system_access_token': {}, 'title': {}},
                       'required': ['access_token', 'file_system_access_token']}
        self.doc = {'name': 'simple_note__read_file', 'description': 'Read file',
                    'input_schema': self.schema, 'output_schema': {}}

    def test_import_only_stdlib_and_private_sibling(self):
        names = ('appworld', 'mcp', 'munch')
        previous = {name: sys.modules.pop(name) for name in names if name in sys.modules}
        try:
            module = load_module(MODULE_PATH.with_name('appworld_mcp.py'))
            self.assertTrue(all(name not in sys.modules for name in names))
            self.assertEqual(module.rules.__name__, '_still_appworld_world')
        finally:
            sys.modules.update(previous)

    def test_fill_arguments_uses_schema_and_world_tokens(self):
        calls = []

        def token(app):
            calls.append(app)
            return 'world-' + app

        arguments = {'access_token': 'caller', 'file_system_access_token': 'caller-file', 'title': 'note'}
        filled = self.module.fill_arguments('simple_note', self.schema, arguments, token)
        self.assertEqual(filled, {'title': 'note', 'access_token': 'world-simple_note',
                                  'file_system_access_token': 'world-file_system'})
        self.assertEqual(calls, ['simple_note', 'file_system'])
        self.assertEqual(arguments['access_token'], 'caller')
        self.assertEqual(self.module.fill_arguments('supervisor', {}, arguments, token), {'title': 'note'})
        self.assertEqual(calls, ['simple_note', 'file_system'])

    def test_filter_and_calls_use_original_schema(self):
        docs = [self.doc, {**self.doc, 'name': 'simple_note__login'},
                {**self.doc, 'name': 'supervisor__show_account_passwords'},
                {**self.doc, 'name': 'venmo__show_profile'}]
        filtered = self.module.filter_tools(docs, ('simple_note', 'supervisor'))
        self.assertEqual(set(filtered), {'simple_note__read_file'})
        self.assertEqual(filtered[self.doc['name']]['input_schema']['properties'], {'title': {}})
        self.assertIn('access_token', self.schema['properties'])
        seen = []
        apis = {'simple_note': {'read_file': lambda **kwargs: seen.append(kwargs) or {'response': 'ok'}}}
        response = self.module.call_tool(self.doc['name'], {'title': 'note'},
                                         {self.doc['name']: self.doc}, apis, lambda app: app)
        self.assertEqual(response, {'response': 'ok'})
        self.assertEqual(seen, [{'title': 'note', 'access_token': 'simple_note',
                                 'file_system_access_token': 'file_system'}])

    def test_unknown_hidden_and_login_errors(self):
        for name in ('unknown', 'venmo__unknown', 'simple_note__login',
                     'supervisor__show_account_passwords'):
            with self.subTest(name=name):
                tools = {self.doc['name']: self.doc, 'simple_note__login': self.doc,
                         'supervisor__show_account_passwords': self.doc}
                response = self.module.call_tool(name, {}, tools, {}, None)
                self.assertEqual(response, {'response': {
                    'message': f"Tool '{name}' not found.", 'is_error': True}})

        def fail(app):
            raise ValueError('fake-token fake-password')

        response = self.module.call_tool(self.doc['name'], {}, {self.doc['name']: self.doc}, {}, fail)
        self.assertEqual(response['response']['message'], 'world login failed for simple_note')
        self.assertNotIn('fake-token', json.dumps(response))
        self.assertNotIn('fake-password', json.dumps(response))
        response = self.module.call_tool(self.doc['name'], {}, {self.doc['name']: self.doc}, {}, lambda app: app)
        self.assertEqual(response['response']['message'], 'world tool call failed')

    def test_login_shortcut_is_lazy_cached_and_unwraps(self):
        calls = []
        api_module = ModuleType('appworld.collections.apis')

        def shortcut(user, login, app, disable_expiry=True):
            calls.append((user, login, app))
            self.assertFalse(disable_expiry)
            return login(username=user.email, password=user.account_passwords[app],
                         _system_datetime=None)['access_token']

        api_module._access_token_from = shortcut
        munch_module = ModuleType('munch')
        munch_module.Munch = SimpleNamespace
        supervisor = SimpleNamespace(
            show_profile=lambda: {'response': {'email': 'owner@example.org', 'phone_number': '123'}},
            show_account_passwords=lambda: {'response': [
                {'account_name': app, 'password': 'fake-password-' + app}
                for app in ('simple_note', 'file_system')]})

        class Apis(dict):
            pass

        login_calls = []

        def login(app, **arguments):
            login_calls.append(arguments)
            return {'response': {'access_token': 'fake-token-' + app}}

        apis = Apis({app: SimpleNamespace(login=lambda app=app, **kwargs: login(app, **kwargs))
                     for app in ('simple_note', 'file_system')})
        apis.supervisor = supervisor
        secrets = []
        with patch.dict(sys.modules, {'appworld.collections.apis': api_module, 'munch': munch_module}):
            getter = self.module.make_token_getter(apis, secrets)
            self.assertEqual(calls, [])
            self.assertEqual(getter('simple_note'), 'fake-token-simple_note')
            self.assertEqual(getter('simple_note'), 'fake-token-simple_note')
            self.assertEqual(getter('file_system'), 'fake-token-file_system')
            self.assertEqual(len(calls), 2)
            self.assertEqual(login_calls[0], {'username': 'owner@example.org',
                                             'password': 'fake-password-simple_note',
                                             '_system_datetime': None})
            self.assertEqual(calls[0][0].account_passwords['simple_note'], 'fake-password-simple_note')
            api_module._access_token_from = lambda *args: (_ for _ in ()).throw(ValueError('fake-password'))
            with self.assertRaisesRegex(RuntimeError, '^world login failed for venmo$'):
                getter('venmo')
        text = self.module.response_text({'response': secrets}, secrets)
        self.assertNotIn('fake-token', text)
        self.assertNotIn('fake-password', text)
        self.assertEqual(self.module.unwrap({'plain': 1}), {'plain': 1})

    def test_cli_loopback_and_apps_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_fixture(root)
            base = ['--root', str(root), '--apps', 'supervisor,simple_note', '--remote-apis-url']
            self.assertEqual(self.module.parse_args(base + ['http://127.0.0.1:1234']).apps,
                             ('simple_note', 'supervisor'))
            for url in ('http://0.0.0.0:1234', 'https://127.0.0.1:1234',
                        'http://127.0.0.1:1234@evil.org', 'http://127.0.0.1:bad'):
                with self.subTest(url=url), patch('sys.stderr'), self.assertRaises(SystemExit):
                    self.module.parse_args(base + [url])
            for apps in ('simple_note', 'supervisor,../venmo', 'supervisor,missing'):
                with self.subTest(apps=apps), patch('sys.stderr'), self.assertRaises(SystemExit):
                    self.module.parse_args(['--root', str(root), '--apps', apps,
                                            '--remote-apis-url', 'http://127.0.0.1:1234'])

    def test_stdio_callbacks_load_file_system_without_exposing_it(self):
        args = SimpleNamespace(root=Path('/fake-root'), apps=('simple_note', 'supervisor'),
                               remote_apis_url='http://127.0.0.1:1234')
        modules = {name: ModuleType(name) for name in (
            'appworld', 'appworld.collections.api_docs', 'appworld.collections.apis',
            'mcp.server', 'mcp.server.stdio', 'mcp.types')}
        modules['appworld'].update_root = Mock()
        builder = Mock()
        builder.build.return_value.mcp.return_value = [
            self.doc, {**self.doc, 'name': 'file_system__show_file'}]
        modules['appworld.collections.api_docs'].ApiDocCollection = builder
        loader = Mock()
        seen = []
        loader.load.return_value = ({'simple_note': {
            'read_file': lambda **kwargs: seen.append(kwargs) or {'response': {'title': 'note'}}}}, None)
        modules['appworld.collections.apis'].ApiCollection = loader
        for name in ('CallToolRequestParams', 'CallToolResult', 'ListToolsResult',
                     'PaginatedRequestParams', 'TextContent', 'Tool'):
            setattr(modules['mcp.types'], name, SimpleNamespace)
        modules['mcp.server'].ServerRequestContext = SimpleNamespace
        test = self

        class Server:
            def __init__(self, name, **handlers):
                self.handlers = handlers

            def create_initialization_options(self):
                return 'options'

            async def run(self, read, write, options):
                test.assertEqual((read, write, options), ('read', 'write', 'options'))
                result = await self.handlers['on_list_tools'](None, None)
                test.assertEqual([tool.name for tool in result.tools], ['simple_note__read_file'])
                test.assertNotIn('access_token', result.tools[0].input_schema['properties'])
                result = await self.handlers['on_call_tool'](None, SimpleNamespace(
                    name='simple_note__read_file', arguments={'title': 'note', 'access_token': 'caller'}))
                test.assertIsNone(result.structured_content)
                test.assertEqual(json.loads(result.content[0].text), {'response': {'title': 'note'}})

        @asynccontextmanager
        async def stdio_server():
            yield 'read', 'write'

        modules['mcp.server'].Server = Server
        modules['mcp.server.stdio'].stdio_server = stdio_server
        with patch.dict(sys.modules, modules), patch.object(self.module, 'parse_args', return_value=args), \
                patch.object(self.module, 'make_token_getter', return_value=lambda app: 'token-' + app):
            self.module.main()
        modules['appworld'].update_root.assert_called_once_with('/fake-root')
        builder.build.assert_called_once_with(load_apps=('simple_note', 'supervisor'))
        loader.load.assert_called_once_with(
            random_seed=100, load_apps=('file_system', 'simple_note', 'supervisor'),
            remote_apis_url=args.remote_apis_url, raise_on_failure=False, wrap_response=True,
            unwrap_response=False, max_num_requests=None, skip_setup=True)
        self.assertEqual(seen, [{'title': 'note', 'access_token': 'token-simple_note',
                                 'file_system_access_token': 'token-file_system'}])


INTEGRATION_ROOT = os.environ.get('AGENTOS_EVAL_APPWORLD_ROOT')


@unittest.skipUnless(INTEGRATION_ROOT and (Path(INTEGRATION_ROOT).expanduser() / 'venv/bin/python').is_file(),
                     'opt in with AGENTOS_EVAL_APPWORLD_ROOT and a separate AppWorld venv')
class InstalledAppWorld(unittest.TestCase):
    def test_train_task_without_agent_action(self):
        with AppWorldWorld('82e2fac_1', root=Path(INTEGRATION_ROOT)) as world:
            self.assertEqual(world.mcp_server()['args'][0], '-I')
            self.assertTrue(world.instruction())
            self.assertTrue(world.operations())
            processes = list(world._processes)
            result = world.evaluate()
            self.assertFalse(result['passed'])
            self.assertGreaterEqual(result['num_tests'], 1)
            self.assertIn('no_op_fail', result['failures'])
        self.assertTrue(all(process.poll() is not None for process in processes))


if __name__ == '__main__':
    unittest.main()
