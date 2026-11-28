"""Isolated Still instances for evaluation.

Two steps:

1. ``snapshot_seed`` makes a sanitized seed folder once, from a data folder
   such as the owner's (read-only; SQLite files are copied with the SQLite
   backup API so a running server is never disturbed).  Sanitizing keeps the
   engine login, model keys and owner state, and removes what would let a
   sandbox act on the owner's outside world: the Telegram token (a sandbox
   must never poll or answer the owner's bot), Google connector tokens,
   connected Mac folders, the embedded-browser profile and every AI-side
   connection binding (#484: confirmed MCP servers, the AI-connections switch,
   the trust record and pending step approvals).  Queued Work and
   scheduled preparations are closed so a sandbox never re-runs the owner's
   pending work.
2. ``Sandbox`` copies a seed into a fresh folder and starts
   ``personal_agent.quickstart`` on its own loopback port, with its own
   engine-run folder, no browser, and PyObjC blocked (``sandbox_site``) so no
   window can open.  It never uses the owner's live port or data folder.
3. A world run (``Sandbox(world=...)``, #484) also gets its own engine home
   inside the sandbox folder: a ``CODEX_HOME`` holding only a copy of the
   Codex login file and one ``[mcp_servers.world]`` table, and an empty
   ``HOME``.  The eval plays the owner's setup of that one server in the
   sandbox store (``worlds/owner_setup.py``) before the server starts.
4. A browser-world run (#1122, ``worlds/shop.py``) gets no engine home: its
   server environment carries the world's exact loopback origins, so the
   import guard enables the embedded browser on the slot's fresh profile and
   lets it load those origins only (``sandbox_site/eval_browser_reach.py``).
5. A sweep that pins the worker model (#1296) writes Still's own Main AI model
   setting into the sandbox store, and its server environment makes the plan
   keep that model (``sandbox_site/eval_worker_pin.py``).

Secrets are copied file-to-file between owner-private (0600/0700) folders and
are never printed, logged or written into the repository.
"""
import fcntl
import json
import os
import re
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import ExitStack, contextmanager
from pathlib import Path

from .paths import LIVE_DATA, LIVE_PORTS, SANDBOX_SITE, SRC, eval_home

#: Secret slots a sandbox may keep: engine/model credentials and Still's own
#: local signing keys.  Everything else (Telegram, OAuth tokens) is dropped.
KEEP_SECRETS = ('claude_code_token', 'model_key', 'lookup_state_key', 'memory_approval_secret', 'browser_step_secret')
KEEP_SECRET_PREFIXES = ('api_key:', 'decision_', 'search_key:')
#: Config rows removed from a seed: folder grants, the managed workspace and
#: (#484) every AI-side connection binding that could start or authorize the
#: owner's real servers in a sandbox: ``ai_mcp_servers`` (servers a Work
#: starts, ``owner_mcp.CONFIRMED_KEY``), ``ai_connections`` (the owner's switch
#: for claude.ai connectors and confirmed servers), ``trust_record`` (per-hand
#: operation decisions and money marking, contract §6) and
#: ``browser_step_requests`` (approvals pending or issued for the owner's Works).
DROP_CONFIG = ('file_roots', 'file_workspace', 'ai_mcp_servers', 'ai_connections', 'trust_record',
               'browser_step_requests')
#: Top-level files and folders of ``private/`` never copied into a seed.
SKIP_PRIVATE = {'logs', 'browser-profile', 'private', 'instance.lock', 'connections.lock', 'setup-link.txt',
                'drive-localhost-cert.pem', 'drive-localhost-key.pem'}
#: Environment names a sandbox server inherits from the eval process.
PASS_ENV = ('HOME', 'USER', 'LOGNAME', 'TMPDIR', 'LANG', 'LC_ALL', 'LC_CTYPE', 'CODEX_HOME', 'SHELL')
#: The Codex login file a world run's engine home copies (codex-cli's file credential store).
CODEX_LOGIN_FILE = 'auth.json'
#: The only worker a world run uses (#484): Claude Code world runs are out of scope.
WORLD_WORKER = 'codex'
#: #1296: Still's ``decision_adapters.MODEL_ID`` and ``EFFORT_LEVELS`` (the eval venv does not import Still).
#: Still ignores a stored value outside these, which would silently unpin a sweep, so refuse it here.
PIN_MODEL_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,99}$')
PIN_EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max', 'ultra')


class SandboxError(RuntimeError):
    pass


def worker_pin(worker, model='', effort=''):
    """``{'worker', 'model', 'effort'}`` for a sweep that pins its worker model (#1296), or None."""
    if not model:
        if effort:
            raise ValueError('--worker-effort needs --worker-model')
        return None
    if worker not in ('codex', 'claude-code'):
        raise ValueError('--worker-model pins one worker: use --worker codex or claude-code, not both')
    if not PIN_MODEL_ID.fullmatch(model):
        raise ValueError(f'--worker-model {model!r} is not a model id Still accepts')
    if effort and effort not in PIN_EFFORTS:
        raise ValueError(f'--worker-effort must be one of {", ".join(PIN_EFFORTS)}')
    return {'worker': worker, 'model': model, 'effort': effort}


def pin_main_ai(data, pin):
    """Set the stopped sandbox's Main AI model and effort for one CLI, as Still's own route stores them.

    These are the rows ``main_ai.MainAi`` writes (``subscription_engine_models``,
    ``main_ai_model_choices``).  The owner route ``/api/main-ai/activate`` would
    also queue a new Judgment AI qualification (model calls) on every run.
    """
    keys = ('subscription_engine_models', 'main_ai_model_choices')
    stored = read_config(data, keys)
    models, choices = (json.loads(stored[key]) if stored.get(key) else {} for key in keys)
    models = {**(models if isinstance(models, dict) else {}), pin['worker']: pin['model']}
    choices = {**(choices if isinstance(choices, dict) else {}),
               pin['worker']: {'mode': 'pinned', 'effort': pin['effort']}}
    write_config(data, {keys[0]: json.dumps(models), keys[1]: json.dumps(choices)})


def baseline_readable_roots(src=SRC):
    """The product's ``bounded_execution.BASELINE_READABLE_ROOTS`` of the checkout a sandbox runs (#1199).

    Read from that checkout's source without importing Still, so a paired baseline run keeps its own
    checkout (``judgments.load_still`` refuses a mixed-checkout process). An older checkout without the
    check, or a test's stand-in src, has none: nothing is refused for it.
    """
    import ast
    try:
        tree = ast.parse((Path(src) / 'personal_agent' / 'bounded_execution.py').read_text(encoding='utf-8'))
    except OSError:
        return ()
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(target, 'id', None) == 'BASELINE_READABLE_ROOTS'
                                                for target in node.targets):
            return tuple(ast.literal_eval(node.value))
    return ()


#: The one seam for the refusal below (#1199): a real run keeps it on. The test suite turns it off in conftest
#: because fixture-transport tests never start a server or qualify a Judgment AI, and pytest's temp folder is /tmp.
REFUSE_BASELINE_READABLE_ROOT = True


def refuse_baseline_readable(root, src=SRC):
    """Refuse a sandbox root whose engine runs Still's strict-isolation check would reject (#1199).

    Still's Codex strict profile refuses a runtime root under a baseline-readable folder
    (``runtime-root-not-baseline-readable``), so a sandbox there never gets a qualified Judgment AI
    and every case measures the fail-closed fallback. Refuse before a server starts or a model is called.
    """
    resolved = Path(root).expanduser().resolve()
    for base in baseline_readable_roots(src):
        base = Path(base).resolve()
        if resolved == base or base in resolved.parents:
            raise SandboxError(f'sandbox root {resolved} is under {base}, which Still\'s strict isolation treats '
                               'as readable by every process, so its Judgment AI cannot qualify there; '
                               'set AGENTOS_EVAL_HOME outside the system temp folders')
    return resolved


def _keep_secret(name):
    return name in KEEP_SECRETS or any(name.startswith(prefix) for prefix in KEEP_SECRET_PREFIXES)


def sanitize_secrets(values):
    return {key: value for key, value in values.items() if _keep_secret(key)}


def _copy_sqlite(source, target):
    src = sqlite3.connect(f'file:{source}?mode=ro', uri=True)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def sanitize_database(path):
    """Close the owner's pending work and outside-world bindings inside a seed database copy."""
    db = sqlite3.connect(path)
    try:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'config' in tables:
            for key in DROP_CONFIG:
                db.execute('DELETE FROM config WHERE key=?', (key,))
            row = db.execute("SELECT value FROM config WHERE key='telegram'").fetchone()
            if row:
                try:
                    telegram = json.loads(row[0]) if row[0] else {}
                except ValueError:
                    telegram = {}
                telegram = telegram if isinstance(telegram, dict) else {}
                telegram['enabled'] = False
                db.execute("UPDATE config SET value=? WHERE key='telegram'", (json.dumps(telegram),))
        if 'jobs' in tables:
            db.execute("UPDATE jobs SET status='interrupted' WHERE status IN ('queued','running')")
        if 'preparations' in tables:
            db.execute("UPDATE preparations SET state='cancelled' WHERE state IN ('proposed','scheduled','running')")
        if 'owner_model_upkeep' in tables:
            # The owner's own pending upkeep must not run (and call a model) inside every sandbox.
            db.execute("UPDATE owner_model_upkeep SET state='expired' WHERE state IN ('pending','claimed')")
        if 'telegram_notifications' in tables:
            db.execute("DELETE FROM telegram_notifications WHERE state IN ('queued','sent')")
        db.commit()
    finally:
        db.close()


def snapshot_seed(source, target, overwrite=False):
    """Write a sanitized seed of data folder ``source`` into ``target``; returns a summary without values."""
    source, target = Path(source).expanduser().resolve(), Path(target).expanduser().resolve()
    if not (source / 'private' / 'quickstart.db').is_file():
        raise SandboxError(f'{source} is not an Still data folder (private/quickstart.db missing)')
    if target == source or source in target.parents:
        raise SandboxError('the seed must live outside the source data folder')
    if target.exists():
        if not overwrite:
            raise SandboxError(f'{target} exists; pass overwrite to replace it')
        shutil.rmtree(target)
    old_umask = os.umask(0o077)
    try:
        (target / 'private').mkdir(parents=True, mode=0o700)
        copied = []
        for item in sorted((source / 'private').iterdir()):
            if item.name in SKIP_PRIVATE or item.suffix == '.log' or item.name.endswith(('-wal', '-shm', '-journal')):
                continue
            destination = target / 'private' / item.name
            if item.name == 'connections.json':
                values = json.loads(item.read_text())
                kept = sanitize_secrets(values if isinstance(values, dict) else {})
                destination.write_text(json.dumps(kept))
                copied.append(f'connections.json ({len(kept)} of {len(values)} slots kept)')
            elif item.suffix == '.db':
                _copy_sqlite(item, destination)
                sanitize_database(destination)
                copied.append(item.name)
            elif item.is_dir():
                shutil.copytree(item, destination, symlinks=False)
                copied.append(item.name + '/')
            elif item.is_file():
                shutil.copy2(item, destination)
                copied.append(item.name)
        for item in sorted(source.iterdir()):
            # Owner plugins travel with the profile; logs and engine runs do not.
            if item.name == 'plugins' and item.is_dir():
                shutil.copytree(item, target / 'plugins', symlinks=False)
                copied.append('plugins/')
    finally:
        os.umask(old_umask)
    (target / 'SEED.json').write_text(json.dumps({'source': str(source), 'created': time.time(), 'copied': copied},
                                                 ensure_ascii=False, indent=2))
    return {'seed': str(target), 'copied': copied}


class PortReservation:
    """Held advisory locks for a sandbox's port pair; close only after its server stops."""

    def __init__(self, port, locks):
        self.port = port
        self._locks = locks

    def close(self):
        self._locks.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def reserve_port(start=18900, end=19900, taken=()):
    """Lock and probe a port pair across processes, independent of eval/slot roots.

    Adopt POSIX ``flock`` (also used for pool slots): closing the handles or
    process exit releases ownership. Never unlink lock files: other processes
    may already have the same inode open. Quickstart currently binds only the
    first port; retain the pool's existing pair allocation convention.
    """
    folder = Path(os.environ.get('AGENTOS_EVAL_PORT_LOCK_DIR') or
                  f'/tmp/still-eval-ports-{os.getuid()}').expanduser()
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    for port in range(start, end):
        pair = {port, port + 1}
        if pair.intersection(LIVE_PORTS) or pair.intersection(taken):
            continue
        with ExitStack() as locks:
            try:
                for number in sorted(pair):
                    handle = locks.enter_context(open(folder / f'{number}.lock', 'a'))
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                continue
            try:
                with ExitStack() as probes:
                    for number in sorted(pair):
                        probe = probes.enter_context(socket.socket(socket.AF_INET, socket.SOCK_STREAM))
                        probe.bind(('127.0.0.1', number))
            except OSError:
                continue
            return PortReservation(port, locks.pop_all())
    raise SandboxError('no free loopback port for a sandbox')


def free_port(start=18900, end=19900, taken=()):
    """Find an available pair without holding it (legacy probe-only callers)."""
    with reserve_port(start, end, taken) as reservation:
        return reservation.port


def sandbox_environment(data, base=None, src=SRC, telegram_root=None, engine_home=None, browser_origins=(),
                        worker_bin=None, worker_pin=None):
    """The minimal environment of one sandbox server: no AgentOS_* or connector settings inherited.

    ``worker_bin`` (#545) is the folder holding the eval's ``claude`` wrapper
    (``worker_key.WorkerApi.install``), put first on ``PATH`` so Still starts its
    Claude Code worker through it.  The API key itself is never in this environment.

    ``telegram_root`` is the eval's loopback Bot API stand-in (``fake_telegram``), when the run uses one.
    ``engine_home`` (``world_engine_home``) replaces ``HOME`` and ``CODEX_HOME`` for a world run.
    ``browser_origins`` (#1122): a browser world's exact loopback origins, for the import guard
    (``sandbox_site/eval_browser_reach.py``); the embedded browser is enabled only with them.
    """
    base = os.environ if base is None else base
    env = {name: base[name] for name in PASS_ENV if base.get(name)}
    extra_path = ['/opt/homebrew/bin', '/usr/local/bin',
                  str(Path(env.get('HOME', str(Path.home()))) / '.local' / 'bin')]
    env['PATH'] = os.pathsep.join(dict.fromkeys([*base.get('PATH', '').split(os.pathsep), *extra_path, '/usr/bin', '/bin']))
    env['PATH'] = os.pathsep.join(item for item in env['PATH'].split(os.pathsep) if item)
    if worker_bin:
        env['PATH'] = os.pathsep.join([str(worker_bin), env['PATH']])
    env['PYTHONPATH'] = os.pathsep.join([str(SANDBOX_SITE), str(src)])
    env['AGENTOS_ENGINE_RUNS'] = str(Path(data) / 'engine-runs')
    env['PYTHONUNBUFFERED'] = '1'
    if telegram_root:
        env['AGENTOS_EVAL_TELEGRAM_ROOT'] = telegram_root
    if engine_home:
        env.update(HOME=engine_home['HOME'], CODEX_HOME=engine_home['CODEX_HOME'])
    if browser_origins:
        env['AGENTOS_EVAL_BROWSER_ORIGINS'] = ','.join(browser_origins)
    if worker_pin:
        env['AGENTOS_EVAL_WORKER_PIN'] = json.dumps(worker_pin)
    return env


def _toml(value):
    # A JSON string is a valid TOML basic string (same escapes, \uXXXX included).
    return json.dumps(value, ensure_ascii=False)


def world_codex_config(definition):
    """``config.toml`` text with exactly one stdio table, ``[mcp_servers.world]``."""
    from .worlds import SERVER_NAME, check_server
    definition = check_server(definition)
    lines = [f'[mcp_servers.{SERVER_NAME}]', f'command = {_toml(definition["command"])}',
             f'args = [{", ".join(_toml(item) for item in definition["args"])}]']
    if definition['env']:
        lines += ['', f'[mcp_servers.{SERVER_NAME}.env]']
        lines += [f'{name} = {_toml(value)}' for name, value in sorted(definition['env'].items())]
    return '\n'.join(lines) + '\n'


def world_engine_home(data, definition, environ=None):
    """A fresh engine home for one world run inside the sandbox folder; returns ``{'HOME', 'CODEX_HOME'}``.

    ``CODEX_HOME`` holds only a copy of the Codex login file (0600) and a
    ``config.toml`` with the world's one server; ``HOME`` is empty.  The only
    thing read from the eval user's own Codex profile is that login file
    (never printed); nothing is written outside ``data``.
    """
    environ = os.environ if environ is None else environ
    profile = Path(environ.get('CODEX_HOME') or Path(environ.get('HOME') or Path.home()) / '.codex').expanduser()
    login = profile / CODEX_LOGIN_FILE
    if not login.is_file():
        raise SandboxError('a world run needs a Codex login: sign in to Codex first (no login file found)')
    home = Path(data) / 'engine-home'
    codex, user = home / 'codex', home / 'home'
    old_umask = os.umask(0o077)
    try:
        if home.exists():
            shutil.rmtree(home)
        for folder in (home, codex, user):
            folder.mkdir(mode=0o700)
        shutil.copyfile(login, codex / CODEX_LOGIN_FILE)
        os.chmod(codex / CODEX_LOGIN_FILE, 0o600)
        (codex / 'config.toml').write_text(world_codex_config(definition), encoding='utf-8')
    finally:
        os.umask(old_umask)
    return {'HOME': str(user), 'CODEX_HOME': str(codex)}


#: Run by path with the sandbox interpreter, so it imports the ``personal_agent`` the sandbox runs.
OWNER_SETUP = Path(__file__).resolve().parent / 'worlds' / 'owner_setup.py'


def world_owner_spec(operations, approvals, money=False):
    """What the eval, playing the owner, confirms for the world's hand: reads, money operations, marking."""
    from .worlds import check_operations
    operations = check_operations(operations)
    unknown = sorted(set(approvals) - set(operations))
    if unknown:
        raise SandboxError(f'the scenario names money operations the world does not expose: {unknown}')
    payments = sorted(approvals)
    reads = sorted(name for name, effect in operations.items() if effect == 'read' and name not in approvals)
    return {'reads': reads, 'payments': payments, 'money': bool(money)}


def confirm_world(data, spec, python, src=SRC, runner=subprocess.run):
    """Apply the owner's setup of the world server to the stopped sandbox store; returns its summary."""
    env = {'PATH': '/usr/bin:/bin', 'PYTHONPATH': str(src), 'HOME': str(Path(data) / 'engine-home' / 'home')}
    done = runner([python, str(OWNER_SETUP), '--data', str(data), '--spec', json.dumps(spec)], env=env,
                  capture_output=True, text=True, timeout=60)
    if done.returncode != 0:
        raise SandboxError('the owner setup of the world server failed: ' + (done.stderr or '').strip()[-300:])
    return json.loads(done.stdout)


def initialize_store(data, python, src=SRC, timeout=60):
    """Let Still's own interpreter/migrations create an empty sandbox store.

    Keep runtime imports out of the eval venv and finish initialization before
    pairing or launching the server. Existing seeded stores skip this step.
    """
    code = 'import sys\nfrom personal_agent.quickstart_store import QuickStore\nQuickStore(sys.argv[1])\n'
    try:
        done = subprocess.run([python, '-c', code, str(data)], cwd=str(data),
                              env=sandbox_environment(data, src=src),
                              capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SandboxError(f'sandbox store initialization failed: {type(exc).__name__}') from exc
    if done.returncode != 0:
        raise SandboxError('sandbox store initialization failed: ' + (done.stderr or '').strip()[-300:])


def pair_fake_telegram(data, token):
    """Pair an initialized store with the stand-in: its made-up token, a fresh cursor.

    The seed keeps the owner's chat id and generation (so the history reads as one conversation)
    but has no token and Telegram disabled (``sanitize_database``). Fresh stores
    use chat 77 and generation 'eval'. Returns the paired chat id.
    """
    db = sqlite3.connect(Path(data) / 'private' / 'quickstart.db')
    try:
        row = db.execute("SELECT value FROM config WHERE key='telegram'").fetchone()
        telegram = json.loads(row[0]) if row and row[0] else {}
        telegram = telegram if isinstance(telegram, dict) else {}
        chat = telegram.get('user_id') if isinstance(telegram.get('user_id'), int) else 77
        telegram.update(enabled=True, user_id=chat, cursor=0, generation=telegram.get('generation') or 'eval')
        db.execute("INSERT INTO config(key,value) VALUES('telegram',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (json.dumps(telegram),))
        db.execute("DELETE FROM config WHERE key='telegram_status'")
        db.commit()
    finally:
        db.close()
    secrets = Path(data) / 'private' / 'connections.json'
    values = json.loads(secrets.read_text()) if secrets.is_file() else {}
    values['telegram_token'] = token
    secrets.write_text(json.dumps(values))
    os.chmod(secrets, 0o600)
    return chat


#: Config rows that hold the Judgment AI qualification.  Its binding names the
#: store root and engine-run folder, so a qualification made in one sandbox
#: slot stays valid for that slot's stable path and is reused (``SandboxPool``).
JUDGMENT_KEYS = ('decision_route', 'decision_route_checks', 'decision_cli_capabilities', 'decision_model')


def seed_identity(seed):
    """What a cached qualification was made for: the seed path and its snapshot time."""
    if not seed:
        return 'fresh'
    try:
        created = json.loads((Path(seed) / 'SEED.json').read_text()).get('created')
    except (OSError, ValueError):
        created = None
    return f'{Path(seed).resolve()}@{created}'


def read_config(data, keys):
    """Config rows of a sandbox store (read-only; the server may be running)."""
    db = sqlite3.connect(f'file:{Path(data) / "private" / "quickstart.db"}?mode=ro', uri=True)
    try:
        marks = ','.join('?' * len(keys))
        return dict(db.execute(f'SELECT key, value FROM config WHERE key IN ({marks})', tuple(keys)).fetchall())
    finally:
        db.close()


def write_config(data, rows):
    db = sqlite3.connect(Path(data) / 'private' / 'quickstart.db')
    try:
        db.executemany('INSERT OR REPLACE INTO config(key, value) VALUES (?, ?)', list(rows.items()))
        db.commit()
    finally:
        db.close()


class Sandbox:
    """One fresh Still instance on a copy of ``seed`` (or an empty folder when ``seed`` is None).

    With ``slot`` the data folder is ``<root>/slot-<n>`` (a stable path, wiped
    and re-copied for every run); without it a new temporary folder is used.
    """

    def __init__(self, root, seed=None, port=None, slot=None, python=None, src=SRC, keep=False, start_timeout=60,
                 telegram=None, world=None, worker_api=None, worker_pin=None):
        self.root = Path(root)
        self.seed = Path(seed).expanduser() if seed else None
        self.port = port
        self.slot = slot
        self.python = python or os.environ.get('AGENTOS_EVAL_SERVER_PYTHON') or shutil.which('python3') or sys.executable
        self.src = Path(src)
        self.keep = keep
        self.start_timeout = start_timeout
        self.process = None
        self.data = None
        self.log = None
        #: The eval's Bot API stand-in (``fake_telegram.FakeTelegram``), or None for a web-only run.
        self.telegram = telegram
        #: ``{'world': an entered World, 'approvals': {op: decision}, 'money': bool}`` for a world run (#484).
        self.world = world
        self.engine_home = None
        self.world_setup = None
        #: ``worker_key.WorkerApi`` when this sandbox's Claude Code worker runs on the eval API key (#545).
        self.worker_api = worker_api
        #: #1296: the pinned worker model of this run (``worker_pin``), or None for Still's own choice.
        self.worker_pin = worker_pin
        self.worker_folder = None
        #: ``worker_key.usage_totals`` of the last run, read when the sandbox stops.
        self.worker_usage = None
        #: #1122: a browser world's origins; the sandbox browser may load these and nothing local else.
        self.browser_origins = ()
        self._port_reservation = None
        self.cache_applied = False
        self._pending_judgment = None

    @property
    def url(self):
        return f'http://127.0.0.1:{self.port}'

    def prepare(self):
        if REFUSE_BASELINE_READABLE_ROOT:
            refuse_baseline_readable(self.root, self.src)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.slot is None:
            self.data = Path(tempfile.mkdtemp(prefix='sandbox-', dir=self.root))
        else:
            self.data = self.root / f'slot-{self.slot}'
            if self.data.exists():
                shutil.rmtree(self.data)
            self.data.mkdir(mode=0o700)
        resolved = self.data.resolve()
        if resolved == LIVE_DATA.resolve() or LIVE_DATA.resolve() in resolved.parents:
            raise SandboxError('refusing to run a sandbox inside the live Still data folder')
        if self.seed:
            if not (self.seed / 'private' / 'quickstart.db').is_file():
                raise SandboxError(f'seed {self.seed} has no private/quickstart.db; run `snapshot` first')
            shutil.rmtree(self.data)
            shutil.copytree(self.seed, self.data, symlinks=False, ignore=shutil.ignore_patterns('SEED.json'))
        return self.data

    def start(self, config=None):
        """Copy the seed, apply ``config`` rows (a cached qualification) and start the server."""
        if self._port_reservation is None:
            self._port_reservation = (reserve_port() if self.port is None else
                                      reserve_port(start=self.port, end=self.port + 1))
        self.port = self._port_reservation.port
        try:
            self.prepare()
            self.cache_applied = False
            self._pending_judgment = None
            if config and (self.data / 'private' / 'quickstart.db').is_file():
                write_config(self.data, config)
                self.cache_applied = True
            elif config:
                self._pending_judgment = {key: value for key, value in config.items() if key in JUDGMENT_KEYS}
            if self.worker_pin is not None:
                if not (self.data / 'private' / 'quickstart.db').is_file():
                    initialize_store(self.data, self.python, src=self.src, timeout=self.start_timeout)
                pin_main_ai(self.data, self.worker_pin)
            if self.telegram is not None:
                from .fake_telegram import TOKEN
                if not (self.data / 'private' / 'quickstart.db').is_file():
                    initialize_store(self.data, self.python, src=self.src, timeout=self.start_timeout)
                self.telegram.chat_id = pair_fake_telegram(self.data, TOKEN)
            from .worlds import browser_origins
            if self.world is not None and browser_origins(self.world['world']):
                # #1122: a browser world needs no engine home or MCP setup: the owner's request
                # reaches it through Still's embedded browser on the sandbox's own fresh profile.
                self.browser_origins = browser_origins(self.world['world'])
                self.world_setup = {'browser_origins': list(self.browser_origins)}
            elif self.world is not None:
                world = self.world['world']
                self.engine_home = world_engine_home(self.data, world.mcp_server())
                spec = world_owner_spec(world.operations(), self.world.get('approvals') or {}, self.world.get('money'))
                self.world_setup = confirm_world(self.data, spec, self.python, src=self.src)
            argv = [self.python, '-m', 'personal_agent.quickstart', 'start', '--host', '127.0.0.1',
                    '--port', str(self.port),
                    '--data', str(self.data), '--no-browser']
            worker_bin = None
            if self.worker_api is not None:
                # Beside the data folder, never inside it: Still's store and its Works never see the key file.
                self.worker_folder = (self.root / f'slot-{self.slot}.claude-api' if self.slot is not None
                                      else Path(tempfile.mkdtemp(prefix='claude-api-', dir=self.root)))
                # The CLI Still would find on the server's own PATH (its install-directory fallbacks too).
                server_path = sandbox_environment(self.data, src=self.src, engine_home=self.engine_home)['PATH']
                worker_bin = self.worker_api.install(self.worker_folder, search_path=server_path)
            self.log = open(self.data / 'server.log', 'ab')
            env = sandbox_environment(self.data, src=self.src,
                                      telegram_root=self.telegram.root if self.telegram is not None else None,
                                      engine_home=self.engine_home, browser_origins=self.browser_origins,
                                      worker_bin=worker_bin, worker_pin=self.worker_pin)
            self.process = subprocess.Popen(argv, cwd=str(self.data), env=env,
                                            stdin=subprocess.DEVNULL, stdout=self.log, stderr=subprocess.STDOUT,
                                            start_new_session=True)
            return self.process
        except BaseException:
            self.stop()
            raise

    def wait_healthy(self, client, sleep=time.sleep, clock=time.monotonic):
        deadline = clock() + self.start_timeout
        while clock() < deadline:
            if self.process and self.process.poll() is not None:
                raise SandboxError(f'sandbox server exited with {self.process.returncode}; see {self.data}/server.log')
            if client.healthy():
                # It may have exited during the request, leaving someone else's listener.
                if self.process and self.process.poll() is not None:
                    raise SandboxError(f'sandbox server exited with {self.process.returncode}; see {self.data}/server.log')
                if self._pending_judgment:
                    # Seedless slots have no store before start. QuickStore.config
                    # and DecisionRoutes.engine read these rows on demand, so the
                    # healthy server can use the cache without a restart.
                    write_config(self.data, self._pending_judgment)
                    self.cache_applied = True
                    self._pending_judgment = None
                return True
            sleep(0.5)
        raise SandboxError(f'sandbox server did not become healthy within {self.start_timeout}s')

    def read_config(self, keys):
        return read_config(self.data, keys)

    def stop(self):
        try:
            if self.process and self.process.poll() is None:
                try:
                    os.killpg(self.process.pid, signal.SIGTERM)
                    self.process.wait(timeout=10)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    try:
                        os.killpg(self.process.pid, signal.SIGKILL)
                        self.process.wait(timeout=10)
                    except ProcessLookupError:
                        pass
            if self.log:
                self.log.close()
            if self.data and self.data.exists() and not self.keep:
                shutil.rmtree(self.data, ignore_errors=True)
        finally:
            if self.worker_folder is not None:
                from .worker_key import remove, usage_totals
                self.worker_usage = usage_totals(self.worker_folder)
                remove(self.worker_folder)
                self.worker_folder = None
            if self._port_reservation is not None:
                self._port_reservation.close()
                self._port_reservation = None


class SandboxPool:
    """At most ``size`` concurrent sandboxes; each scenario run gets a fresh copy of the seed.

    A fresh copy per run keeps trials independent (the docs' clean-state
    trials): Memory written by one scenario never leaks into another.  Runs
    use stable slot folders under ``root`` (one exclusive file lock per slot,
    so two sweeps never share one), which lets a Judgment AI qualification
    made in a slot be cached per slot, worker and seed and reused instead of
    re-running it (about a minute and a dozen model calls) on every run.
    """

    MAX_SLOTS = 16

    def __init__(self, size=3, seed=None, root=None, keep=False, sandbox_factory=Sandbox, worker_api=None,
                 worker_pin=None):
        if not 1 <= size <= self.MAX_SLOTS:
            raise ValueError(f'instances must be between 1 and {self.MAX_SLOTS}')
        self.size = size
        self.seed = seed
        self.root = Path(root) if root else eval_home() / 'sandboxes'
        self.keep = keep
        self.factory = sandbox_factory
        #: #545: ``worker_key.WorkerApi`` for Claude Code sandboxes, or None (the subscription).
        self.worker_api = worker_api
        #: #1296: ``worker_pin`` for every sandbox of that worker, or None.
        self.worker_pin = worker_pin
        self.slots = threading.BoundedSemaphore(size)
        self.lock = threading.Lock()
        self.ports = set()
        self.held = {}

    def _claim_slot(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        for slot in range(self.MAX_SLOTS):
            if slot in self.held:
                continue
            handle = open(self.root / f'slot-{slot}.lock', 'a')
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                handle.close()
                continue
            self.held[slot] = handle
            return slot
        raise SandboxError('every sandbox slot is in use by another sweep')

    def _release_slot(self, slot):
        handle = self.held.pop(slot, None)
        if handle:
            fcntl.flock(handle, fcntl.LOCK_UN)
            handle.close()

    def _cache_path(self, slot, worker):
        return self.root / f'slot-{slot}.{worker}.judgment.json'

    def cached_judgment(self, slot, worker):
        try:
            data = json.loads(self._cache_path(slot, worker).read_text())
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or data.get('seed') != seed_identity(self.seed):
            return None
        rows = data.get('rows')
        return rows if isinstance(rows, dict) and rows else None

    def store_judgment(self, slot, worker, rows):
        path = self._cache_path(slot, worker)
        path.write_text(json.dumps({'seed': seed_identity(self.seed), 'rows': rows, 'saved': time.time()}))
        os.chmod(path, 0o600)

    def forget_judgment(self, slot, worker):
        self._cache_path(slot, worker).unlink(missing_ok=True)

    @contextmanager
    def sandbox(self, worker=None, telegram=None, world=None):
        with self.slots:
            slot, reservation, box = None, None, None
            api = self.worker_api if worker == 'claude-code' else None
            pin = self.worker_pin if self.worker_pin and self.worker_pin['worker'] == worker else None
            try:
                if api is not None and not api.admits():
                    raise SandboxError(f'the daily eval budget (USD {api.cap_usd:g}) has no room for another '
                                       'Claude Code worker run')
                with self.lock:
                    slot = self._claim_slot()
                    reservation = reserve_port(taken=self.ports)
                    port = reservation.port
                    self.ports.update({port, port + 1})
                box = self.factory(self.root, seed=self.seed, port=port, slot=slot, keep=self.keep,
                                   **({'telegram': telegram} if telegram is not None else {}),
                                   **({'world': world} if world is not None else {}),
                                   **({'worker_api': api} if api is not None else {}),
                                   **({'worker_pin': pin} if pin is not None else {}))
                box._port_reservation = reservation
                box.cached = self.cached_judgment(slot, worker) if worker else None
                box.start(config=box.cached)
                yield box
            finally:
                try:
                    if box is not None:
                        box.stop()
                finally:
                    if reservation is not None:
                        reservation.close()
                    with self.lock:
                        if reservation is not None:
                            self.ports.difference_update({reservation.port, reservation.port + 1})
                        if slot is not None:
                            self._release_slot(slot)


def run_id():
    return time.strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:6]
