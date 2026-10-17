"""CONNECT-CODE-01 (#1380): sandboxed code runs, model-free.

The runner protocol is exercised with an injected process; the real sandbox
runs only with AGENTOS_REAL_SANDBOX_TESTS=1 (it starts Seatbelt and a proxy).
"""
import json
import os
import tempfile
import time
import types
import unittest
from unittest import mock

from personal_agent import code_run
from personal_agent.code_run import CodeRunError, CodeRunner


class FakeProcess:
    """A runner that asks about the given requests, then reports ``result``."""

    def __init__(self, requests=(), result=None, error=None, silent=False):
        self.decisions, self.config = [], None
        self._requests, self._result, self._error, self._silent = list(requests), result, error, silent
        self.terminated = False
        self.stdin = self
        self.stdout = LazyStdout(self)

    def write(self, text):
        message = json.loads(text)
        if self.config is None:
            self.config = message
        else:
            self.decisions.append(message)

    def flush(self):
        pass

    def _lines(self):
        for index, request in enumerate(self._requests, 1):
            yield json.dumps({'type': 'request', 'id': index, **request}) + '\n'
        if self._error:
            yield json.dumps({'type': 'error', 'message': self._error}) + '\n'
        elif not self._silent:
            yield json.dumps({'type': 'result', 'exit': 0, 'timedOut': False, **(self._result or {})}) + '\n'

    def poll(self):
        return None if not self.terminated else 0

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.terminated = True


class LazyStdout:
    """Yield the fake runner's lines only after the host sent its config."""

    def __init__(self, process):
        self.process = process

    def __iter__(self):
        while self.process.config is None:
            time.sleep(0.001)
        yield from self.process._lines()


def runner(process, **kwargs):
    store = types.SimpleNamespace(root=tempfile.mkdtemp())
    run = CodeRunner(store, 'work-1', popen=lambda *a, **k: process, node='/usr/bin/node',
                     install=lambda store, node: store.root, **kwargs)
    return run


class Arguments(unittest.TestCase):
    def test_refuses_what_this_slice_does_not_support(self):
        bad = [({}, 'code_run_argument_invalid'),
               ({'code': 'print(1)', 'packages': 'requests'}, 'code_run_argument_invalid'),
               ({'code': 'print(1)', 'hosts': 'not a host!'}, 'code_run_argument_invalid'),
               ({'code': 'print(1)', 'hosts': '10.0.0.1'}, 'code_run_argument_invalid'),
               # #1470: a declared mutate or payment is no obstacle (each request needs approval); nonsense is.
               ({'code': 'print(1)', 'effect': 'delete-everything'}, 'code_run_argument_invalid'),
               ({'code': 'print(1)', 'secrets': 'Not_A_Set'}, 'code_run_argument_invalid'),
               ({'code': 'print(1)', 'secrets': 'a,b,c,d,e'}, 'code_run_argument_invalid'),
               ({'code': 'x' * (code_run.MAX_CODE_BYTES + 1)}, 'code_run_argument_invalid')]
        for args, code in bad:
            with self.subTest(args=str(args)[:40]):
                with self.assertRaises(CodeRunError) as caught:
                    code_run._arguments(args)
                self.assertEqual(caught.exception.code, code)

    def test_accepts_comma_separated_hosts_and_pins(self):
        code, packages, hosts, names, seconds = code_run._arguments(
            {'code': 'print(1)', 'hosts': 'API.example.com:443, data.example.org', 'packages': 'a==1.0,b==2',
             'secrets': 'set-b, set-a', 'timeout_seconds': '999'})
        self.assertEqual(hosts, ['api.example.com', 'data.example.org'])
        self.assertEqual(names, ['set-a', 'set-b'])
        self.assertEqual(packages, ['a==1.0', 'b==2'])
        self.assertEqual(seconds, code_run.MAX_SECONDS)

    def test_private_roots_cover_the_owner_home(self):
        self.assertIn(os.path.realpath(os.path.expanduser('~')), code_run.private_roots())


class RequestPolicy(unittest.TestCase):
    def test_reads_to_declared_hosts_only(self):
        process = FakeProcess(requests=[
            {'method': 'GET', 'host': 'api.example.com', 'path': '/v1/items'},
            {'method': 'POST', 'host': 'api.example.com', 'path': '/v1/orders'},
            {'method': 'GET', 'host': 'other.example.net', 'path': '/'},
            {'method': 'HEAD', 'host': 'api.example.com:443', 'path': '/'}],
            result={'stdout': 'ok', 'stderr': ''})
        with mock.patch('personal_agent.current_context.redact_known_secrets', lambda store, text: text):
            result = runner(process).run({'code': 'print(1)', 'hosts': 'api.example.com'})
        self.assertEqual([item['allow'] for item in process.decisions], [True, False, False, True])
        self.assertEqual([row['allowed'] for row in result['requests']], [True, False, False, True])
        self.assertEqual(result['sources'], [{'url': 'https://api.example.com/v1/items'}, {'url': 'https://api.example.com/'}])
        # The sandbox never inherits the host environment, and home stays unreadable.
        self.assertEqual(set(process.config['env']), {'PATH', 'HOME', 'LANG', 'PYTHONIOENCODING', 'PYTHONDONTWRITEBYTECODE'})
        self.assertEqual(process.config['masked'], [])
        self.assertEqual(process.config['allowedDomains'], ['api.example.com'])
        self.assertIn(os.path.realpath(os.path.expanduser('~')), process.config['denyRead'])
        self.assertEqual(process.config['env']['HOME'], process.config['workdir'])

    def test_output_is_redacted_and_evidence_keeps_no_output_or_code(self):
        process = FakeProcess(result={'stdout': 'token=s3cr3t-value', 'stderr': ''})
        with mock.patch('personal_agent.current_context.redact_known_secrets',
                        lambda store, text: text.replace('s3cr3t-value', '[redacted]')):
            result = runner(process).run({'code': 'print("secret code")', 'hosts': ''})
        self.assertEqual(result['stdout'], 'token=[redacted]')
        kept = json.dumps(code_run.evidence(result))
        self.assertNotIn('secret code', kept)
        self.assertNotIn('token=', kept)
        self.assertIn('code_digest', kept)


class Failures(unittest.TestCase):
    def test_runner_error_and_silent_exit_are_failures(self):
        for process, code in ((FakeProcess(error='boom'), 'code_run_failed'),
                              (FakeProcess(silent=True), 'code_run_failed')):
            with self.subTest(code=code), self.assertRaises(CodeRunError) as caught:
                runner(process).run({'code': 'print(1)'})
            self.assertEqual(caught.exception.code, code)
            self.assertTrue(process.terminated)

    def test_a_stopped_work_stops_the_run(self):
        process = FakeProcess(silent=True)
        with self.assertRaises(CodeRunError) as caught:
            runner(process, interrupted=lambda: 'stopped').run({'code': 'print(1)'})
        self.assertEqual(caught.exception.code, 'code_run_interrupted')
        self.assertTrue(process.terminated)

    def test_without_node_the_capability_is_unavailable(self):
        store = types.SimpleNamespace(root=tempfile.mkdtemp())
        with mock.patch.object(code_run, 'find_node', return_value=None):
            run = CodeRunner(store, 'work-1')
            self.assertFalse(run.available())
            with self.assertRaises(CodeRunError) as caught:
                run.run({'code': 'print(1)'})
        self.assertEqual(caught.exception.code, 'code_run_unavailable')


class Runtime(unittest.TestCase):
    def test_install_uses_the_lockfile_without_scripts(self):
        calls = []

        def fake_run(argv, cwd, **kwargs):
            calls.append(argv)
            package = os.path.join(cwd, 'node_modules', '@anthropic-ai', 'sandbox-runtime')
            os.makedirs(package)
            with open(os.path.join(package, 'package.json'), 'w') as handle:
                json.dump({'version': code_run.RUNTIME_VERSION}, handle)
            return types.SimpleNamespace(returncode=0)
        with tempfile.TemporaryDirectory() as root:
            node_dir = os.path.join(root, 'bin')
            os.makedirs(node_dir)
            for name in ('node', 'npm'):
                open(os.path.join(node_dir, name), 'w').close()
            store = types.SimpleNamespace(root=os.path.join(root, 'store'))
            target = code_run.ensure_runtime(store, os.path.join(node_dir, 'node'), run=fake_run)
            self.assertEqual(calls[0][1:], ['ci', '--ignore-scripts', '--omit=dev', '--no-audit', '--no-fund'])
            self.assertTrue((target / 'runner.mjs').is_file())
            self.assertEqual(code_run.ensure_runtime(store, os.path.join(node_dir, 'node'), run=fake_run), target)
            self.assertEqual(len(calls), 1)

    def test_lockfile_pins_the_reviewed_runtime(self):
        lock = json.loads((code_run.SOURCE / 'package-lock.json').read_text())
        entry = lock['packages']['node_modules/@anthropic-ai/sandbox-runtime']
        self.assertEqual(entry['version'], code_run.RUNTIME_VERSION)
        self.assertTrue(all(item.get('integrity') for key, item in lock['packages'].items() if key))


@unittest.skipUnless(os.environ.get('AGENTOS_REAL_SANDBOX_TESTS') == '1', 'starts a real sandbox and proxy')
class RealSandbox(unittest.TestCase):
    def test_home_is_unreadable_and_writes_stay_in_the_run(self):
        store = types.SimpleNamespace(root=tempfile.mkdtemp())
        code = ('import os\n'
                f'for p in [{os.path.expanduser("~/.zshrc")!r}, "/tmp/agentos-run-escape"]:\n'
                '    try:\n'
                '        open(p, "a").close(); print("OPENED", p)\n'
                '    except OSError as e: print("denied", type(e).__name__)\n'
                'open("ok.txt", "w").write("x"); print("workdir ok")\n')
        with mock.patch('personal_agent.current_context.redact_known_secrets', lambda store, text: text):
            result = CodeRunner(store, 'work-1').run({'code': code})
        self.assertNotIn('OPENED', result['stdout'])
        self.assertIn('workdir ok', result['stdout'])


if __name__ == '__main__':
    unittest.main()
