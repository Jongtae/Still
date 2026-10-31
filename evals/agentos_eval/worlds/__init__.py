"""Simulated worlds a sandbox run acts on (#484): the World protocol, a fake world and the registry.

A world is a controllable set of simulated apps with state-based tests.  It
reaches Still the way the owner's own tools do: as one owner-confirmed stdio
MCP server, every call decided by Still (``owner_mcp``, ``codex_permission_hook``).
Nothing here is imported by ``src/personal_agent``.

The protocol is duck-typed; one instance serves one run::

    class World:                      # a context manager
        name: str
        def __init__(self, task_id, *, root): ...
        def __enter__(self): ...      # starts the world's loopback servers with a fresh state
        def mcp_server(self): ...     # {'command': str, 'args': [str, ...], 'env': {str: str}}
        def instruction(self): ...    # the task's owner request, read from the world at run time
        def operations(self): ...     # {tool name: 'read' | 'mutate'}: every tool the server exposes
        def evaluate(self): ...       # {'passed', 'num_tests', 'failures', 'collateral'}; no task text
        def __exit__(self, *exc): ... # stops everything it started

``instruction`` keeps task text out of the repository: a scenario stores only
the task id.  ``operations`` lets the eval play the owner's trust-record
review of the world's tools (reads as reads; the scenario names the money
operations).  A tool name is what the MCP server lists, without Codex's
``mcp__<server>__`` prefix.
"""
import importlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

#: The one MCP server name a world run's engine home defines and the sandbox store confirms.
SERVER_NAME = 'world'
#: A real AI against simulated apps: never a live service.
EVIDENCE_CLASS = 'sandbox-world'
EFFECTS = ('read', 'mutate')
#: What Still's trust record accepts as an operation name (``trust_record.OPERATION``).
OPERATION = re.compile(r'^[A-Za-z0-9_.-]{1,60}$')
ENV_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,127}$')
#: ``name -> 'module:Class'``; imported only when a run asks for that world.
REGISTRY = {'appworld': 'agentos_eval.worlds.appworld:AppWorldWorld', 'fake': 'agentos_eval.worlds:FakeWorld'}


class WorldError(RuntimeError):
    """A world that cannot start, or answered outside the protocol."""


def load(name, task_id, root):
    """A new, not yet entered World ``name`` for ``task_id`` with its state under ``root``."""
    target = REGISTRY.get(name)
    if target is None:
        raise WorldError(f'unknown world {name!r}; known: {sorted(REGISTRY)}')
    module_name, _sep, class_name = target.partition(':')
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise WorldError(f'world {name!r} is not installed ({exc})') from None
    return getattr(module, class_name)(task_id, root=Path(root))


def check_server(definition):
    """The world's stdio MCP server definition, validated; raises ``WorldError``."""
    if not isinstance(definition, dict) or set(definition) - {'command', 'args', 'env'}:
        raise WorldError('mcp_server() must be {command, args, env}')
    command, args, env = definition.get('command'), definition.get('args', []), definition.get('env', {})
    if not isinstance(command, str) or not command:
        raise WorldError('mcp_server() needs a command')
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        raise WorldError('mcp_server() args must be a list of text')
    if not isinstance(env, dict) or not all(isinstance(k, str) and ENV_NAME.match(k) and isinstance(v, str)
                                            for k, v in env.items()):
        raise WorldError('mcp_server() env must map variable names to text')
    return {'command': command, 'args': list(args), 'env': dict(env)}


def check_operations(operations):
    """``{tool name: 'read' | 'mutate'}``, validated; raises ``WorldError``."""
    if not isinstance(operations, dict) or not operations:
        raise WorldError('operations() must name the tools the server exposes')
    for name, effect in operations.items():
        if not isinstance(name, str) or not OPERATION.match(name) or effect not in EFFECTS:
            raise WorldError(f'operations(): {name!r} must be a tool name with effect read or mutate')
    return dict(operations)


def check_result(result):
    """``evaluate()``'s answer, normalized; anything else is a world failure, never a pass."""
    if not isinstance(result, dict) or not isinstance(result.get('passed'), bool):
        raise WorldError('evaluate() must return {passed: bool, num_tests, failures, collateral}')
    def texts(key):
        items = result.get(key) or []
        return [str(item)[:300] for item in items] if isinstance(items, list) else [str(items)[:300]]
    num = result.get('num_tests')
    return {'passed': result['passed'], 'num_tests': num if isinstance(num, int) and num >= 0 else None,
            'failures': texts('failures'), 'collateral': texts('collateral')}


# -- the fake world (model-free tests) ------------------------------------------------

FAKE_SERVER = Path(__file__).resolve().parents[3] / 'tests' / 'fake_world_mcp.py'
#: Synthetic tasks: what the owner asks, and what the final state must be.
FAKE_TASKS = {
    'fake-pay': {'instruction': 'Pay the corner shop 12 for the order.',
                 'expect': {'payments': [{'to': 'corner-shop', 'amount': 12}]}},
    'fake-note': {'instruction': 'Write a note saying the shop is closed on Monday.',
                  'expect': {'notes': ['the shop is closed on Monday']}},
}


class FakeWorld:
    """A tiny world for model-free tests: a state file, a stdio MCP server script and a held loopback stand-in.

    ``__enter__`` starts one background process (as a real world starts its
    API server); ``__exit__`` stops it and removes the state, even after an error.
    """

    name = 'fake'

    def __init__(self, task_id, *, root, server=FAKE_SERVER):
        if task_id not in FAKE_TASKS:
            raise WorldError(f'unknown fake task {task_id!r}')
        self.task_id = task_id
        self.root = Path(root)
        self.server = Path(server)
        self.folder = None
        self.state = None
        self.process = None

    def __enter__(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.folder = Path(tempfile.mkdtemp(prefix='fake-world-', dir=self.root))
        self.state = self.folder / 'state.json'
        self.state.write_text(json.dumps({'payments': [], 'notes': [], 'items': ['bread', 'milk']}))
        self.process = subprocess.Popen([sys.executable, str(self.server), '--hold'], stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return self

    def mcp_server(self):
        return {'command': sys.executable, 'args': [str(self.server), '--state', str(self.state)], 'env': {}}

    def instruction(self):
        return FAKE_TASKS[self.task_id]['instruction']

    def operations(self):
        return {'list_items': 'read', 'add_note': 'mutate', 'pay': 'mutate'}

    def evaluate(self):
        state = json.loads(self.state.read_text())
        expect = FAKE_TASKS[self.task_id]['expect']
        failures = [f'{key} differ' for key, value in expect.items() if state.get(key) != value]
        collateral = [f'unexpected {key}' for key in ('payments', 'notes') if key not in expect and state.get(key)]
        return {'passed': not failures and not collateral, 'num_tests': len(expect) + 1,
                'failures': failures, 'collateral': collateral}

    def __exit__(self, *exc):
        if self.process is not None and self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
                self.process.wait(timeout=10)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        if self.folder is not None:
            shutil.rmtree(self.folder, ignore_errors=True)
        return False
