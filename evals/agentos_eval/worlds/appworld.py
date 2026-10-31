"""AppWorld sandbox-world: a real AI working against simulated apps.

Development tooling for train/dev tasks only, never held-out test tasks.
Use AppWorld's ASGI apps on loopback: ``appworld serve`` binds publicly and
kills existing port holders. AppWorld is Apache-2.0, but protected data/tests
may be redistributed only encrypted; nothing from AppWorld is committed here
and scoring outputs carry labels only. The task instruction is exposed only
through its instruction method.

Default apps use AppWorld's ground-truth app list: an oracle setting analogous
to APIPredictor(mode='ground_truth'), at app granularity. It is a hint the AI
would not have with a real owner, so a world run's result does not show
unaided app discovery. The narrowing keeps the tools within Still's
64-operation trust record.

The MCP server (``appworld_mcp.py``) logs the task's supervisor in on the
world's side. It removes credential parameters from every tool and hides the
account and password tools, because Still denies any tool call that carries
credentials in its arguments.

Evaluate once, after the agent acts: early or repeated evaluation corrupts
remote-mode scoring, so results are cached.
"""
import copy
import json
import os
import re
import socket
import subprocess
import time
import urllib.request
import uuid
from pathlib import Path

APPWORLD_COMMIT = '42b5bcf3cd334fee33f0c37c02070a9f5807add5'
ACCOUNT_APIS = frozenset({'login', 'logout', 'signup', 'delete_account',
                          'send_password_reset_code', 'reset_password',
                          'send_verification_code', 'verify_account'})
HIDDEN_TOOLS = frozenset({'supervisor__show_account_passwords'})
WORLD_CREDENTIALS = ('access_token', 'file_system_access_token')


def exposed(app, api):
    return app not in ('admin', 'api_docs') and api not in ACCOUNT_APIS and f'{app}__{api}' not in HIDDEN_TOOLS


def strip_credentials(input_schema):
    schema = copy.deepcopy(input_schema)
    for name in WORLD_CREDENTIALS:
        schema.get('properties', {}).pop(name, None)
    if 'required' in schema:
        schema['required'] = [name for name in schema['required'] if name not in WORLD_CREDENTIALS]
    return schema


def validated_apps(apps, docs_dir):
    names = set()
    for app in apps:
        if not isinstance(app, str) or not re.fullmatch(r'[a-z_]{1,30}', app):
            raise ValueError('invalid AppWorld app name')
        if not (Path(docs_dir) / f'{app}.json').is_file():
            raise ValueError('missing AppWorld app docs')
        names.add(app)
    names.difference_update(('admin', 'api_docs', 'supervisor'))
    if not (Path(docs_dir) / 'supervisor.json').is_file():
        raise ValueError('missing AppWorld app docs')
    return tuple(sorted(names | {'supervisor'}))


def operations_from_docs(apps, docs_dir):
    operations = {}
    for app in apps:
        docs = json.loads((Path(docs_dir) / f'{app}.json').read_text())
        for api, doc in docs.items():
            if exposed(app, api):
                operations[f'{app}__{api}'] = 'read' if doc['method'] == 'GET' else 'mutate'
    return operations


class AppWorldWorld:
    name = 'appworld'

    def __init__(self, task_id: str, *, root: Path, apps=None, python: Path | None = None,
                 popen=subprocess.Popen, urlopen=urllib.request.urlopen,
                 startup_timeout: float = 120.0):
        if not isinstance(task_id, str) or not re.fullmatch(r'[0-9a-f]{7}_[0-9]+', task_id):
            raise ValueError('invalid AppWorld task id')
        self.root = Path(root).expanduser().resolve()
        try:
            tasks = set()
            for split in ('train', 'dev'):
                tasks.update((self.root / 'data/datasets' / f'{split}.txt').read_text().splitlines())
        except OSError:
            raise ValueError('AppWorld task is not a train/dev task') from None
        if task_id not in tasks:
            raise ValueError('AppWorld task is not a train/dev task')
        repo = Path(__file__).resolve().parents[3]
        if self.root == repo or repo in self.root.parents:
            raise ValueError('AppWorld root must be outside the repository')
        self.task_id = task_id
        if apps is None:
            apps = json.loads((self.root / 'data/tasks' / task_id /
                               'ground_truth/required_apps.json').read_text())
            if not isinstance(apps, list):
                raise ValueError('invalid AppWorld required apps')
        self.apps = validated_apps(apps, self.root / 'data/api_docs/standard')
        self.python = Path(python) if python is not None else self.root / 'venv/bin/python'
        self.experiment_name = f'still-eval-{uuid.uuid4().hex[:8]}'
        self._popen = popen
        self._urlopen = urlopen
        self.startup_timeout = startup_timeout
        self._processes = []
        self._api_url = None
        self._env_url = None
        self._instruction = ''
        self._active = False
        self._entered = False
        self._evaluation_attempted = False
        self._result = None

    def instruction(self):
        return self._instruction

    def operations(self):
        return operations_from_docs(self.apps, self.root / 'data/api_docs/standard')

    def __enter__(self):
        if self._entered:
            raise RuntimeError('AppWorld instances are single-use')
        self._entered = True
        try:
            # Hold both reservations together so the OS cannot return the same port.
            with socket.socket() as api, socket.socket() as env:
                api.bind(('127.0.0.1', 0))
                env.bind(('127.0.0.1', 0))
                api_port, env_port = api.getsockname()[1], env.getsockname()[1]
            self._api_url = f'http://127.0.0.1:{api_port}'
            self._env_url = f'http://127.0.0.1:{env_port}'
            process_env = {key: os.environ[key] for key in ('PATH', 'HOME', 'TMPDIR', 'LANG')
                           if key in os.environ}
            process_env.update(APPWORLD_ROOT=str(self.root), ON_DISK='false')
            for app, port in (('appworld.serve._apis:app', api_port),
                              ('appworld.serve.environment:app', env_port)):
                self._processes.append(self._popen(
                    [str(self.python), '-m', 'uvicorn', app, '--host', '127.0.0.1', '--port', str(port)],
                    cwd=str(self.root), env=process_env.copy(), stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            self._wait_ready()
            output = self._post('/initialize', {
                'task_id': self.task_id, 'experiment_name': self.experiment_name,
                'remote_apis_url': self._api_url})['output']
            self._instruction = output['instruction']
            self._active = True
            return self
        except BaseException:
            self._stop()
            raise

    def _wait_ready(self):
        deadline = time.monotonic() + self.startup_timeout
        pending = {self._api_url + '/docs', self._env_url + '/'}
        while True:
            if any(process.poll() is not None for process in self._processes):
                raise RuntimeError('AppWorld server exited during startup')
            if not pending:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError('AppWorld server readiness timed out')
            for url in tuple(pending):
                try:
                    with self._urlopen(url, timeout=min(2.0, remaining)) as response:
                        if response.status == 200:
                            pending.remove(url)
                except Exception:
                    pass
            if pending:
                time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))

    def _post(self, path, body, timeout=120.0):
        request = urllib.request.Request(self._env_url + path, data=json.dumps(body).encode(),
                                         headers={'Content-Type': 'application/json'})
        try:
            with self._urlopen(request, timeout=timeout) as response:
                return json.loads(response.read())
        except Exception:
            # Transport/JSON errors may contain protected response text.
            raise RuntimeError(f'AppWorld request failed: {path}') from None

    def mcp_server(self):
        if not self._active:
            raise RuntimeError('AppWorld is not active')
        return {'command': str(self.python),
                'args': ['-I', str(Path(__file__).resolve().parent / 'appworld_mcp.py'),
                         '--remote-apis-url', self._api_url, '--root', str(self.root),
                         '--apps', ','.join(self.apps)],
                'env': {'APPWORLD_ROOT': str(self.root)}}

    def evaluate(self):
        if self._result is not None:
            return {**self._result, 'failures': list(self._result['failures']),
                    'collateral': list(self._result['collateral'])}
        if not self._active:
            raise RuntimeError('AppWorld is not active')
        if self._evaluation_attempted:
            raise RuntimeError('AppWorld evaluation already attempted')
        self._evaluation_attempted = True
        try:
            self._post('/save', {'task_id': self.task_id})
            output = self._post('/evaluate', {'task_id': self.task_id})['output']
            failures, collateral = [], []
            for failure in output['failures']:
                label = str(failure.get('label', ''))
                if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,40}', label):
                    label = 'unlabelled'
                (collateral if label == 'no_op_pass' else failures).append(label)
            self._result = {'passed': bool(output['success']), 'num_tests': int(output['num_tests']),
                            'failures': failures, 'collateral': collateral}
        except Exception:
            raise RuntimeError('AppWorld evaluation failed') from None
        return self.evaluate()

    def _stop(self):
        self._active = False
        if len(self._processes) == 2:
            try:
                self._post('/close_all', {}, timeout=2.0)
            except Exception:
                pass
        processes, self._processes = self._processes, []
        for process in processes:
            try:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
            except Exception:
                pass

    def __exit__(self, *_exc):
        self._stop()
