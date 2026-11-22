"""The sandboxed Claude Code worker on an Anthropic API key instead of the owner's subscription (#545).

Owner decision 2026-11-21: the key from the Keychain entry ``agentos-evals`` /
``anthropic`` (the same one the judge uses) reaches **only the Claude Code
worker process** of an eval sandbox, so ``--worker claude-code`` and
``--worker both`` no longer share the owner's Claude subscription limit with
development sessions.  The sandbox's Still server never holds it: not in its
environment, its store, its prompts, its logs or its Evidence.

How (C15 review in the PR): Still finds its engine CLI on ``PATH``
(``subscription_engines.find_cli``) and starts it with a minimal environment
(``BoundedExecutionAdapter.environment``).  The eval puts one wrapper named
``claude`` first on the sandbox server's ``PATH``.  The wrapper is the only
thing that reads the key, from a 0600 file in an eval-owned folder beside the
slot (never inside the sandbox data folder), and starts the real CLI with:

* ``ANTHROPIC_API_KEY``, the CLI's documented API-key variable, which it uses
  in non-interactive mode in place of any subscription login;
* ``CLAUDE_CODE_OAUTH_TOKEN`` and ``ANTHROPIC_AUTH_TOKEN`` removed, so the
  subscription token the seed keeps can never be used beside it;
* ``CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1``, the CLI's documented switch that keeps
  its credentials for its own API calls and strips them from the Bash, hook and
  stdio MCP server environments (the same layer Still's cloud profiles use, #211).

Cost: every model launch first reserves ``reserve_usd`` against the existing
daily eval budget (``budget.JudgeBudget``, default USD 10 a day, shared with
the judge); the launch is refused when that would cross the cap.  When the CLI
reports its result, the reservation is settled to the per-model ``costUSD``
the CLI computes for each model it used (``modelUsage``), falling back to its
``total_cost_usd``; a launch whose cost is never reported keeps the reserve.
Only model names, token counts and USD are written to the usage file.

A launch that reaches no Anthropic model passes through untouched, with no
key and no charge: ``--version``/``--help``, and Still's model-free CLI
qualification, which points the CLI at a local fake model with
``ANTHROPIC_BASE_URL``.
"""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from .budget import JudgeBudget

#: The Keychain account the judge also reads (``cli.KEYCHAIN_SERVICE`` / ``anthropic``).
KEYCHAIN_ACCOUNT = 'anthropic'
#: Default worst-case charge per CLI model launch, settled to the reported cost afterwards.
DEFAULT_RESERVE_USD = 1.0
#: Credentials removed from the CLI environment so the subscription is never used beside the key.
SUBSCRIPTION_ENV = ('CLAUDE_CODE_OAUTH_TOKEN', 'ANTHROPIC_AUTH_TOKEN')
#: Invocations that reach no model.
NO_MODEL_ARGS = frozenset(('--version', '-v', '--help', '-h'))
REFUSED_EXIT = 3


class WorkerKeyError(RuntimeError):
    pass


class WorkerKey:
    """The API key held in the eval process's memory only; ``repr`` and ``str`` never show it."""

    def __init__(self, value, source):
        if not isinstance(value, str) or len(value.strip()) < 8:
            raise WorkerKeyError('the Anthropic API key for the eval worker is missing or too short')
        self._value = value.strip()
        self.source = source

    def __repr__(self):
        return f'WorkerKey(source={self.source!r})'

    __str__ = __repr__

    def reveal(self):
        return self._value

    def scrub(self, value):
        """``value`` (any JSON-shaped data) with every occurrence of the key replaced."""
        text = json.dumps(value, ensure_ascii=False)
        if self._value not in text:
            return value
        return json.loads(text.replace(self._value, '[redacted]'))


def load_worker_key(environ=None, keychain=None):
    """The key from the Keychain entry ``agentos-evals`` / ``anthropic``, else ``ANTHROPIC_API_KEY``.

    Never copied into ``environ``: it travels only through ``WorkerKey``.
    """
    environ = os.environ if environ is None else environ
    if keychain is None:
        from .cli import keychain_secret as keychain
    value = keychain(KEYCHAIN_ACCOUNT)
    if value:
        return WorkerKey(value, 'keychain')
    if environ.get('ANTHROPIC_API_KEY'):
        return WorkerKey(environ['ANTHROPIC_API_KEY'], 'environment')
    raise WorkerKeyError('No Anthropic API key for the Claude Code worker: store it with '
                         '`security add-generic-password -s agentos-evals -a anthropic -w`, '
                         'or run with --claude-auth subscription.')


class WorkerApi:
    """One sweep's API-key setup for its Claude Code sandboxes."""

    def __init__(self, key, budget_path, cap_usd, reserve_usd=DEFAULT_RESERVE_USD, python=None, real_binary=None):
        self.key = key
        self.budget_path = Path(budget_path)
        self.cap_usd = float(cap_usd)
        self.reserve_usd = float(reserve_usd)
        if self.reserve_usd <= 0:
            raise ValueError('the per-launch reserve must be positive')
        self.python = python or sys.executable
        self.real_binary = real_binary

    @classmethod
    def from_environ(cls, key, budget_path, environ=None, **overrides):
        environ = os.environ if environ is None else environ
        budget = JudgeBudget.from_environ(budget_path, environ)
        reserve = float(environ.get('AGENTOS_EVAL_WORKER_RESERVE_USD') or DEFAULT_RESERVE_USD)
        return cls(key, budget_path, budget.cap, reserve, **overrides)

    def budget(self):
        return JudgeBudget(self.budget_path, cap_usd=self.cap_usd)

    def admits(self):
        """Whether the daily budget still has room for one launch's reserve."""
        status = self.budget().status()
        return status['usd'] + self.reserve_usd <= self.cap_usd

    def install(self, folder):
        """Write the key, the wrapper config and the ``claude`` wrapper into ``folder``; returns its bin dir."""
        real = self.real_binary or _real_claude()
        folder = Path(folder)
        if folder.exists():
            remove(folder)
        bin_dir = folder / 'bin'
        bin_dir.mkdir(parents=True, mode=0o700)
        os.chmod(folder, 0o700)
        key_file = folder / 'key'
        fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as handle:
            handle.write(self.key.reveal())
        config = {'binary': str(real), 'key_file': str(key_file), 'budget_path': str(self.budget_path),
                  'cap_usd': self.cap_usd, 'reserve_usd': self.reserve_usd, 'usage_file': str(folder / 'usage.jsonl')}
        config_file = folder / 'config.json'
        config_file.write_text(json.dumps(config))
        os.chmod(config_file, 0o600)
        evals_dir = Path(__file__).resolve().parents[1]
        wrapper = bin_dir / 'claude'
        wrapper.write_text(f'#!{self.python} -I\n'
                           '# Eval-only (#545): runs the real Claude Code CLI on the eval API key.\n'
                           'import sys\n'
                           f'sys.path.insert(0, {str(evals_dir)!r})\n'
                           'from agentos_eval.worker_key import wrapper_main\n'
                           f'sys.exit(wrapper_main({str(config_file)!r}, sys.argv[1:]))\n')
        os.chmod(wrapper, 0o700)
        return bin_dir


def _real_claude():
    import shutil
    found = shutil.which('claude')
    if not found:
        raise WorkerKeyError('the Claude Code CLI (claude) is not installed')
    return Path(found).resolve()


def remove(folder):
    import shutil
    folder = Path(folder)
    (folder / 'key').unlink(missing_ok=True)
    shutil.rmtree(folder, ignore_errors=True)


def usage_totals(folder):
    """``{'launches', 'settled', 'usd', 'models'}`` from a wrapper folder's usage file (no secret in it)."""
    totals = {'launches': 0, 'settled': 0, 'refused': 0, 'usd': 0.0, 'models': {}}
    try:
        lines = (Path(folder) / 'usage.jsonl').read_text().splitlines()
    except OSError:
        return totals
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get('refused'):
            totals['refused'] += 1
            continue
        totals['launches'] += 1
        totals['settled'] += 1 if row.get('settled') else 0
        totals['usd'] = round(totals['usd'] + float(row.get('usd') or 0), 6)
        for name, model in (row.get('models') or {}).items():
            into = totals['models'].setdefault(name, {})
            for metric, value in model.items():
                into[metric] = round(into.get(metric, 0) + value, 6)
    return totals


def reported_cost(record):
    """``(usd, models)`` from one CLI result record, or ``(None, {})``.

    Per-model ``costUSD`` from ``modelUsage`` (the CLI's own price per model);
    ``total_cost_usd`` when no per-model figure is given.
    """
    if not isinstance(record, dict):
        return None, {}
    models = {}
    for name, usage in (record.get('modelUsage') or {}).items() if isinstance(record.get('modelUsage'), dict) else ():
        if isinstance(usage, dict):
            models[str(name)[:120]] = {key: usage[key] for key in
                                       ('inputTokens', 'outputTokens', 'cacheReadInputTokens',
                                        'cacheCreationInputTokens', 'costUSD')
                                       if isinstance(usage.get(key), (int, float)) and not isinstance(usage.get(key), bool)}
    per_model = [model['costUSD'] for model in models.values() if 'costUSD' in model]
    if per_model:
        return float(sum(per_model)), models
    total = record.get('total_cost_usd')
    if isinstance(total, (int, float)) and not isinstance(total, bool):
        return float(total), models
    return None, models


def _append(path, row):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, 'a') as handle:
        handle.write(json.dumps(row) + '\n')


def wrapper_main(config_path, argv, environ=None, stdout=None, popen=subprocess.Popen):
    """The ``claude`` wrapper: key and budget for model launches, untouched pass-through otherwise."""
    environ = dict(os.environ if environ is None else environ)
    config = json.loads(Path(config_path).read_text())
    binary = config['binary']
    if NO_MODEL_ARGS.intersection(argv) or _local_model(environ.get('ANTHROPIC_BASE_URL')):
        # No Anthropic model is reached: no key, no charge.
        return popen([binary, *argv], env=environ).wait()
    budget = JudgeBudget(config['budget_path'], cap_usd=config['cap_usd'])
    reserved = budget.reserve_usd(config['reserve_usd'], kind='worker')
    if reserved is None:
        _append(config['usage_file'], {'ts': time.time(), 'refused': True})
        sys.stderr.write('Eval worker refused: the daily eval budget (USD %g) is used up.\n' % config['cap_usd'])
        return REFUSED_EXIT
    for name in SUBSCRIPTION_ENV:
        environ.pop(name, None)
    environ['ANTHROPIC_API_KEY'] = Path(config['key_file']).read_text().strip()
    environ['CLAUDE_CODE_SUBPROCESS_ENV_SCRUB'] = '1'
    out = stdout if stdout is not None else sys.stdout.buffer
    usd, models = None, {}
    child = popen([binary, *argv], env=environ, stdout=subprocess.PIPE)
    environ.pop('ANTHROPIC_API_KEY')

    def forward(signum, _frame):
        try:
            child.send_signal(signum)
        except OSError:
            pass
    previous = {sig: signal.signal(sig, forward) for sig in (signal.SIGTERM, signal.SIGINT)} \
        if hasattr(signal, 'SIGTERM') and _main_thread() else {}
    try:
        for line in iter(child.stdout.readline, b''):
            out.write(line)
            out.flush()
            if b'"modelUsage"' in line or b'"total_cost_usd"' in line:
                try:
                    found, found_models = reported_cost(json.loads(line))
                except ValueError:
                    continue
                if found is not None:
                    usd, models = found, found_models
        code = child.wait()
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        settled = budget.settle_usd(reserved, usd, kind='worker')
        _append(config['usage_file'], {'ts': time.time(), 'usd': settled, 'settled': usd is not None,
                                       'models': models})
    return code


def _local_model(url):
    """Still's model-free CLI qualification points the CLI at a loopback fake model."""
    from urllib.parse import urlsplit
    if not url:
        return False
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return False
    return host in ('127.0.0.1', 'localhost', '::1')


def _main_thread():
    import threading
    return threading.current_thread() is threading.main_thread()
