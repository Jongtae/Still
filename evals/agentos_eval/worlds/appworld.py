"""AppWorld sandbox-world: a real AI working against simulated apps.

Development tooling for train/dev tasks only, never held-out test tasks.
Use AppWorld's ASGI apps on loopback: ``appworld serve`` binds publicly and
kills existing port holders. AppWorld is Apache-2.0, but protected data/tests
may be redistributed only encrypted; nothing from AppWorld is committed here
and scoring outputs carry labels only. The task instruction is exposed only
through its read-only property. Evaluate once, after the agent acts: early or
repeated evaluation corrupts remote-mode scoring, so results are cached.
"""
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


class AppWorldWorld:
    name = 'appworld'

    def __init__(self, task_id: str, *, root: Path, python: Path | None = None,
                 popen=subprocess.Popen, urlopen=urllib.request.urlopen,
                 startup_timeout: float = 120.0):
        if not isinstance(task_id, str) or not re.fullmatch(r'[0-9a-f]{7}_[0-9]+', task_id):
            raise ValueError('invalid AppWorld task id')
        self.root = Path(root).expanduser().resolve()
        repo = Path(__file__).resolve().parents[3]
        if self.root == repo or repo in self.root.parents:
            raise ValueError('AppWorld root must be outside the repository')
        self.task_id = task_id
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

    @property
    def instruction(self):
        return self._instruction

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
                'args': ['-m', 'appworld.cli', 'serve', 'mcp', 'stdio', '--remote-apis-url', self._api_url,
                         '--root', str(self.root), '--output-type', 'content_only'],
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
