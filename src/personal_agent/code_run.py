"""CONNECT-CODE-01 (#1380): the owner's AI runs code it wrote, in an isolated sandbox.

C16: the owner's AI reads an API's documentation and writes the code a
developer would write; Still only removes the blocker that the AI has no safe
place to run it.  The pinned Anthropic sandbox-runtime (Seatbelt on macOS,
bubblewrap on Linux) isolates the run: the owner's home is unreadable, only the
run's own temporary directory is writable, and every outbound HTTP request is
asked of this process before it leaves (``runner.mjs``).  Nothing here knows
what the code is for; no service, site or API shape is named.

Runs only read: GET/HEAD to the hosts the AI declared, plus the non-GET
requests the owner reviewed as reads for a code-run secret set (a token
exchange, for example).  A secret set's values reach the run masked (substituted
on egress to its hosts) or, when the owner saw they are used inside the code,
as real values with egress limited to those hosts.  Orders, transfers and
other state changes are refused; enabling them needs the existing
``api_requests`` effect guard and approval, not this module.

Design: ``docs/connect-code-run-design.en.md``.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from pathlib import Path
import queue
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time

from .prompt_files import prompt

RUNTIME_PACKAGE = '@anthropic-ai/sandbox-runtime'
RUNTIME_VERSION = '0.0.79'
SOURCE = Path(__file__).resolve().parent / 'sandbox'
RUNTIME_FILES = ('package.json', 'package-lock.json', 'runner.mjs')
MAX_CODE_BYTES = 64 * 1024
MAX_PACKAGES = 16
MAX_HOSTS = 16
MAX_SECRET_SETS = 4
OUTPUT_BYTES = 16 * 1024
MAX_SECONDS = 120
INSTALL_SECONDS = 180
#: Grace for the runner to start its proxies and report after the code's own timeout.
RUNNER_GRACE = 30
MIN_NODE = 18
PACKAGE_INDEX = ('pypi.org', 'files.pythonhosted.org')
READ_METHODS = frozenset({'GET', 'HEAD'})
PIN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,99}==[A-Za-z0-9][A-Za-z0-9.+!_-]{0,49}$')
HOST = re.compile(r'^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$')
NODE_CANDIDATES = ('/opt/homebrew/bin/node', '/usr/local/bin/node', '/usr/bin/node')
UNAVAILABLE_TEXT = '이 Mac에 Node.js 18 이상이 없어 코드를 실행할 수 없어요.'
#: #1456: what the run's code is told when a state-changing request waits for the owner's approval.
APPROVAL_REQUESTED_TEXT = ('this request changes state, so Still asked the owner to approve exactly this request '
                           '(method, path, query and body). First check it is the change the owner asked for: if it is a '
                           'lookup or a helper sent with the wrong method (the API documents it as GET), it was a mistake, '
                           'so do not stop here; fix the code and run again, and tell the owner to ignore that approval '
                           'request. If it is the change itself, do not retry it in this run. End this attempt and tell the owner '
                           'in one or two plain sentences what is waiting for approval (what it does, in their terms); do '
                           'not repeat how to approve, which the approval message itself says, and never call it a '
                           'Telegram message or a request ID; once approved, run the same code again and this exact request is '
                           'sent once. The approval message Still sends has no button: the owner approves by reacting '
                           'with a thumbs-up to it or replying that they approve, so never tell them to press a button. '
                           'Only when the API\'s own documentation sends this request as POST (or another non-GET method) '
                           'and it changes nothing (it only issues a token, computes a hash or a signature), it should not '
                           'need an approval each time: propose adding it to the set\'s reviewed reads with settings_change '
                           'api_connections connect (the same set JSON with this "METHOD host/path" added to reads, '
                           'continue_work=yes); the owner confirms once and no key is asked for again. A lookup the '
                           'documentation sends as GET is fixed in the code instead, never registered.')
#: #1456: the longest request body Still shows the owner for approval (the runner's bodyText bound).
APPROVAL_BODY_CHARS = 16 * 1024
#: #1470: the AI's owner-facing description of a state change, and the line that introduces the exact request.
APPROVAL_NOTE_CHARS = 300
APPROVAL_EXACT_LABEL = '보낼 요청:'
APPROVAL_CONTENT_LABEL = '내용:'
#: Owner choice 2026-10-14 (clear signing, after ERC-7730): the labelled values the owner reads instead of the
#: raw request, each checked by Still against the exact field of the request that is approved.
APPROVAL_FIELDS_LABEL = '확인한 값:'
APPROVAL_FIELDS_OK = '✅ 위 값은 실제로 보낼 요청과 같아요.'
APPROVAL_FIELDS_MISMATCH = '⚠️ 설명한 값을 실제 요청에서 찾지 못했어요. 아래 원문을 확인해 주세요.'
APPROVAL_FIELDS_CONTEXT = '요청에는 없는 참고 설명: {fields}'
APPROVAL_FIELDS_UNLABELED = '그 밖의 항목 {count}개는 아래 원문에 있어요.'
MAX_APPROVAL_FIELDS = 12
APPROVAL_MONEY_LINE = '돈이 오가는 요청이에요.'
#: #1486: a run with a saved or proposed library starts here.  ``python -I`` keeps the run
#: directory off ``sys.path``, so the launcher puts it back and runs ``main.py`` as ``__main__``.
LAUNCHER = "import runpy,sys;sys.path.insert(0,'.');runpy.run_path('main.py',run_name='__main__')"
#: #1486: appended to a library module.  It wraps each public function to count its calls and
#: failures and, when the run ends, writes them with the functions' signatures, first docstring
#: lines, required parameters and ``EXAMPLES`` to a report file.  The report is the AI's own
#: untrusted account: bounded and validated before use, never a grant.
LIBRARY_FOOTER = '''

def _agentos_library_report():
    import atexit, functools, inspect, json, os, sys
    module = sys.modules[__name__]
    calls, described = {}, []
    for name, value in list(vars(module).items()):
        if name.startswith('_') or not inspect.isfunction(value) or value.__module__ != __name__:
            continue
        try:
            signature = inspect.signature(value)
            text = str(signature)
            required = [item.name for item in signature.parameters.values()
                        if item.default is item.empty and item.kind in (item.POSITIONAL_ONLY, item.POSITIONAL_OR_KEYWORD, item.KEYWORD_ONLY)]
        except (TypeError, ValueError):
            text, required = '(...)', []
        lines = (inspect.getdoc(value) or '').strip().splitlines()
        described.append({'name': name, 'signature': text, 'doc': lines[0] if lines else '', 'required': required})

        def wrap(function=value, name=name):
            @functools.wraps(function)
            def call(*args, **kwargs):
                counts = calls.setdefault(name, [0, 0])
                try:
                    result = function(*args, **kwargs)
                except BaseException:
                    counts[1] += 1
                    raise
                counts[0] += 1
                return result
            return call
        setattr(module, name, wrap())
    examples = getattr(module, 'EXAMPLES', None)
    try:
        json.dumps(examples)
    except (TypeError, ValueError):
        examples = None
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), @REPORT@)

    def report():
        with open(path, 'w', encoding='utf-8') as handle:
            json.dump({'functions': described, 'examples': examples, 'calls': calls}, handle)
    atexit.register(report)


_agentos_library_report()
'''
LIBRARY_REPORT_BYTES = 64 * 1024
#: Work id -> the arguments of the run that asked the owner to approve a request (``rerun: approved``).
APPROVAL_RUNS_KEY = 'code_run_approval_runs'
LIBRARY_HINT = prompt('code_run_library_hint')
#: #1486: the parameter-flow check calls each function with its two examples; what it would send is
#: recorded, reads go through as in any run, and nothing else is sent or asked of the owner.
CHECK_TEXT = 'parameter check: Still recorded this request and did not send it'
CHECK_SESSION_SECONDS = 30
CHECK_TOTAL_SECONDS = 120
MAX_CHECKED_FUNCTIONS = 8
MAX_CHECKED_REQUESTS = 50


class CodeRunError(ValueError):
    """A refused or failed code run with a stable ``code``."""

    def __init__(self, message, code, requires=None):
        super().__init__(message)
        self.code, self.requires = code, requires


def find_node(which=shutil.which, run=subprocess.run):
    """The absolute path of a Node.js >= 18, or None."""
    for candidate in (which('node'), *NODE_CANDIDATES):
        if not candidate or not os.path.isfile(candidate) or not os.access(candidate, os.X_OK):
            continue
        try:
            done = run([candidate, '--version'], capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
            major = int(done.stdout.strip().lstrip('v').split('.')[0])
        except (OSError, ValueError, subprocess.SubprocessError):
            continue
        if done.returncode == 0 and major >= MIN_NODE:
            return os.path.realpath(candidate)
    return None


def _runtime_digest():
    digest = hashlib.sha256()
    for name in RUNTIME_FILES:
        digest.update(name.encode() + b'\0' + (SOURCE / name).read_bytes() + b'\0')
    return digest.hexdigest()


def ensure_runtime(store, node, *, run=subprocess.run):
    """The installed, pinned sandbox runtime directory for this Still revision.

    Installed once per lockfile and runner digest with ``npm ci --ignore-scripts``,
    so the lockfile's integrity hashes decide what runs and no install script does.
    """
    root = Path(store.root) / 'sandbox-runtime'
    target = root / _runtime_digest()[:16]
    marker = target / 'node_modules' / RUNTIME_PACKAGE / 'package.json'
    if marker.is_file():
        return target
    npm = Path(node).parent / 'npm'
    if not npm.is_file():
        raise CodeRunError('코드 실행 환경을 설치할 npm을 찾지 못했어요.', 'code_run_unavailable')
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='install-', dir=root))
    try:
        for name in RUNTIME_FILES:
            shutil.copyfile(SOURCE / name, staging / name)
        env = {'PATH': f'{Path(node).parent}:/usr/bin:/bin', 'HOME': str(staging), 'npm_config_update_notifier': 'false'}
        done = run([str(npm), 'ci', '--ignore-scripts', '--omit=dev', '--no-audit', '--no-fund'],
                   cwd=staging, env=env, capture_output=True, text=True, timeout=INSTALL_SECONDS, stdin=subprocess.DEVNULL)
        installed = staging / 'node_modules' / RUNTIME_PACKAGE / 'package.json'
        if done.returncode != 0 or not installed.is_file() \
                or json.loads(installed.read_text()).get('version') != RUNTIME_VERSION:
            raise CodeRunError('코드 실행 환경을 설치하지 못했어요.', 'code_run_unavailable')
        try:
            staging.rename(target)
        except OSError:
            if not marker.is_file():
                raise
    except subprocess.SubprocessError:
        raise CodeRunError('코드 실행 환경을 설치하지 못했어요.', 'code_run_unavailable') from None
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return target


def interpreter():
    """``(python, readable roots)``: this Still's own base interpreter and what it must read."""
    executable = os.path.realpath(getattr(sys, '_base_executable', None) or sys.executable)
    roots = {os.path.realpath(sys.base_prefix)}
    if not any(executable.startswith(root + os.sep) for root in roots):
        roots.add(os.path.dirname(executable))
    return executable, sorted(roots)


def private_roots():
    """Absolute directories a run must never read: the owner's home and every user's."""
    roots = {os.path.realpath(os.path.expanduser('~'))}
    roots.update(path for path in ('/Users', '/home', '/root', '/var/root') if os.path.isdir(path))
    return sorted(roots)


def _host(value):
    host = str(value or '').strip().lower()
    if host.endswith(':443'):
        host = host[:-4]
    return host


def _ip_literal(host):
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _arguments(args):
    code = args.get('code')
    if not isinstance(code, str) or not code.strip() or len(code.encode()) > MAX_CODE_BYTES:
        raise CodeRunError('실행할 Python 코드를 64KB 이하로 주세요.', 'code_run_argument_invalid')
    packages = args.get('packages') or []
    if isinstance(packages, str):
        packages = [part for part in re.split(r'[\s,]+', packages) if part]
    if not isinstance(packages, list) or len(packages) > MAX_PACKAGES \
            or not all(isinstance(item, str) and PIN.match(item) for item in packages):
        raise CodeRunError('패키지는 name==version 형식으로 16개까지 주세요.', 'code_run_argument_invalid')
    hosts = args.get('hosts') or []
    if isinstance(hosts, str):
        hosts = [part for part in re.split(r'[\s,]+', hosts) if part]
    hosts = sorted({_host(item) for item in hosts if isinstance(item, str)})
    from .api_requests import public_host
    if len(hosts) > MAX_HOSTS or not all(HOST.match(item) and not _ip_literal(item) and public_host(item) for item in hosts):
        raise CodeRunError('접속할 호스트는 공개 도메인 이름으로 16개까지 주세요.', 'code_run_argument_invalid')
    # #1470: the AI's declaration is not authority: every state-changing request needs the owner's
    # approval of exactly it (#1456), whatever the run declared.
    effect = args.get('effect') or 'read'
    if effect not in ('read', 'navigate', 'mutate', 'payment'):
        raise CodeRunError('effect는 read, mutate 또는 payment로 주세요.', 'code_run_argument_invalid')
    from .api_requests import NAME
    names = args.get('secrets') or []
    if isinstance(names, str):
        names = [part for part in re.split(r'[\s,]+', names) if part]
    names = sorted(set(names)) if isinstance(names, list) else None
    if names is None or len(names) > MAX_SECRET_SETS or not all(isinstance(item, str) and NAME.match(item) for item in names):
        raise CodeRunError('비밀값 묶음 이름을 쉼표로 4개까지 주세요.', 'code_run_argument_invalid')
    try:
        seconds = int(args.get('timeout_seconds') or 60)
    except (TypeError, ValueError):
        raise CodeRunError('timeout_seconds는 정수로 주세요.', 'code_run_argument_invalid') from None
    return code, packages, hosts, names, max(1, min(seconds, MAX_SECONDS))


def _library_argument(args, names):
    """#1486: the library module a run proposes for its one named secret set, or None."""
    from .procedures import MAX_SOURCE_BYTES
    library = args.get('library') if isinstance(args, dict) else None
    if library is None or library == '':
        return None
    if not isinstance(library, str) or not library.strip() or len(library.encode()) > MAX_SOURCE_BYTES:
        raise CodeRunError(f'library는 {MAX_SOURCE_BYTES // 1024}KB 이하의 Python 모듈 소스로 주세요.', 'code_run_argument_invalid')
    if len(names) != 1:
        raise CodeRunError('library는 비밀값 묶음 하나를 secrets에 지정한 실행에서만 줄 수 있어요.', 'code_run_argument_invalid')
    return library


def parse_approval_fields(text):
    """``[(label, field, meaning)]`` from "label=FIELD[=meaning]; ..." (bounded), else []."""
    out = []
    for item in str(text or '').replace('\n', ';').split(';'):
        parts = [part.strip() for part in item.split('=', 2)]
        if len(parts) >= 2 and parts[0] and parts[1]:
            out.append((parts[0][:30], parts[1][:60], (parts[2] if len(parts) > 2 else '')[:40]))
        if len(out) >= MAX_APPROVAL_FIELDS:
            break
    return out


def request_values(body, query):
    """The request's fields as ``{name: text}``: a JSON object body (one level, dotted for nested), else form or query."""
    from urllib.parse import parse_qsl
    values = {}

    def walk(prefix, value):
        if isinstance(value, dict):
            for key, item in value.items():
                walk(f'{prefix}.{key}' if prefix else str(key), item)
        elif not isinstance(value, list):
            values[prefix] = '' if value is None else str(value)
    try:
        parsed = json.loads(body) if body else None
    except ValueError:
        parsed = None
    if isinstance(parsed, dict):
        walk('', parsed)
    elif body:
        values.update(dict(parse_qsl(body, keep_blank_values=True)))
    values.update({key: value for key, value in parse_qsl(query or '', keep_blank_values=True) if key not in values})
    return values


def approval_field_lines(fields, body, query, redact=str):
    """The labelled values and Still's check of them against the exact request (clear signing).

    A label names a request field ("label=FIELD") or states a value ("label=value"); either is shown
    only when the request carries it.  Owner feedback 2026-10-14 (too many alerts): a stated fact the
    request does not carry (the old price of an amendment) and the fields left unnamed are said
    plainly, without a warning; the one warning is a description none of whose values the request
    carries.  ``redact`` hides stored secret values; a field carrying one is never shown."""
    values = {key: redact(value) for key, value in request_values(body, query).items()}
    lines, context, named = [], [], set()
    for label, field, meaning in fields:
        if field in values:
            named.add(field)
            if values[field] and '[redacted]' not in values[field]:
                lines.append(f"{label} {values[field]}" + (f" ({meaning})" if meaning else ''))
            continue
        tokens = {token.replace(',', '') for token in re.findall(r'\d[\d,]*', field)}
        hits = [key for key, value in values.items()
                if value and (value == field.strip() or value.replace(',', '') in tokens)]
        if hits:
            named.update(hits)
            lines.append(f"{label} {field}" + (f" ({meaning})" if meaning else ''))
        else:
            context.append(label)
    # A masked value, a redacted one or an empty one explains itself.
    unlabeled = [key for key, value in values.items() if key not in named and value
                 and not value.startswith('{{') and '[redacted]' not in value]
    check = APPROVAL_FIELDS_OK if lines else APPROVAL_FIELDS_MISMATCH
    if context and lines:
        check += '\n' + APPROVAL_FIELDS_CONTEXT.format(fields=', '.join(context))
    if unlabeled:
        check += '\n' + APPROVAL_FIELDS_UNLABELED.format(count=len(unlabeled))
    return lines, check


def _scalar(value):
    return _plain(str(value)) if isinstance(value, (str, int, float)) and not isinstance(value, bool) else None


def _plain(text):
    """#1489: letters and digits only, case-folded, so a value a function reformats (dashes, case,
    spacing) still matches the request it reached."""
    return re.sub(r'[\W_]+', '', text.casefold())


def flow_problem(name, examples, texts):
    """Why one function's two example calls show a fixed value, or None (#1486, owner concern in #1482).

    ``texts`` are the requests each call made, as text.  Refused: two calls that made the same
    requests; an argument that differs between the examples but appears in neither call's own
    requests; and a value from the first example that the second call still sends while its own
    value is missing (a value written into the function).  A function that makes no request is
    not checked.  Values and requests are compared as letters and digits only (``_plain``).
    """
    first, second = examples
    if first == second:
        return f'EXAMPLES[{name!r}] must give two different argument sets'
    if not texts[0] and not texts[1]:
        return None
    if not texts[0] or not texts[1]:
        return f'{name}: only one of its two examples made a request; give two examples that both run'
    if texts[0] == texts[1]:
        return (f'{name}: both examples made the same requests, so its arguments do not reach them (a fixed value?). '
                'If a call stopped early on a refused or failed request (a new token, for example), make it reach the '
                'request that uses the arguments: reuse a valid token kept in the set\'s state file')
    texts = [_plain(text) for text in texts]
    for key in sorted(set(first) | set(second)):
        a, b = _scalar(first.get(key)), _scalar(second.get(key))
        if not a or not b or a == b:
            continue
        if a not in texts[0] or b not in texts[1]:
            return (f'{name}: argument {key!r} did not reach the request it made; pass it through, '
                    'or give example values in the form the API takes')
        # Live 2026-10-14: a first example's start date equal to the second's end date is not a fixed value.
        others = {_scalar(value) for value in second.values()} - {None}
        if len(a) >= 4 and a not in b and a in texts[1] and not any(a in other for other in others):
            return f'{name}: the request for the second example still carries {key!r} from the first (a fixed value?)'
    return None


def _hostname(host):
    """A slot host without its port, as sandbox-runtime's ``injectHosts`` matches it."""
    return host.rsplit(':', 1)[0] if re.search(r':\d{1,5}$', host) else host


class CodeRunner:
    """One Work's sandboxed code runs; every request decision is made here."""

    def __init__(self, store, work_id, *, popen=subprocess.Popen, node=None, interrupted=None,
                 install=ensure_runtime, clock=time.monotonic, approvals=None):
        self.store, self.work_id = store, work_id
        self.popen, self.interrupted, self.install, self.clock = popen, interrupted, install, clock
        self._node = node
        #: #1456: the Work's per-action approval surface (``consume``/``request``), as ``api_request`` uses.
        #: Without one, every state-changing request is refused as before.
        self.approvals = approvals
        #: Per run: host -> (set name, slot) of the named secret sets, approval requests, dispatch claims.
        self._owners, self._asked, self._claims = {}, [], []
        #: #1489: the sets this Work's runs were already told how to keep their steps for.
        self._hinted = set()
        self._fields, self._note = [], ''
        #: #167: ``[(text, host)]`` of the requests the last run let out (path, query, body, header values),
        #: held in memory for the information-use comparison only; never returned or recorded.
        self.sent = []

    def node(self):
        if self._node is None:
            self._node = find_node() or ''
        return self._node or None

    def available(self):
        return self.node() is not None

    def secret_sets(self, names, hosts):
        """``(masked, plain env, {host: reviewed reads})`` for the named code-run secret sets (#1380).

        A ``header_or_body`` value is masked: the sandbox sees a sentinel that
        sandbox-runtime replaces only in headers and bodies sent to the set's
        hosts.  An ``in_code`` value is the real value inside the run, so such a
        run may reach the set's hosts and nothing else.
        """
        from .api_credentials import ApiCredentials
        from .api_requests import CODE_KIND, env_name, slots
        current = slots(self.store)
        masked, plain, set_hosts = [], {}, {}
        # #1456: each run starts with no set hosts, approval requests or dispatch claims of an earlier run.
        self._owners, self._asked, self._claims = {}, [], []
        for name in names:
            slot = current.get(name)
            if slot is None or slot.get('kind') != CODE_KIND:
                raise CodeRunError(f"코드 실행용 비밀값 묶음 '{name}'이 없어요. 먼저 연결을 준비해 주세요.", 'needs_setup',
                                   requires=f'api-slot:{name}')
            try:
                values, _finish = ApiCredentials(self.store).resolve(name, slot)
            except ValueError:
                raise CodeRunError(f"비밀값 묶음 '{name}'을 지금 쓸 수 없어요. 연결을 다시 확인해 주세요.", 'needs_setup',
                                   requires=f'api-slot:{name}') from None
            for item in slot['fields']:
                env = env_name(name, item['name'])
                if item['use'] == 'in_code':
                    plain[env] = values[item['name']]
                else:
                    masked.append({'name': env, 'value': values[item['name']],
                                   'injectHosts': sorted({_hostname(host) for host in slot['hosts']})})
            for host in slot['hosts']:
                set_hosts.setdefault(_host(host), set()).update(slot['reads'])
                self._owners.setdefault(_host(host), (name, slot))
        if plain and set(hosts) - set(set_hosts):
            raise CodeRunError('코드 안에서 직접 쓰는 비밀값이 있는 실행은 그 묶음의 호스트에만 접속할 수 있어요.',
                               'code_run_argument_invalid')
        return masked, plain, set_hosts

    def run(self, args, *, seconds=None):
        # #233: first, so a run refused below never reports an earlier run's requests as its own.
        self.sent = []
        # Live 2026-10-14: a resumed Work rewrote the code that asked for approval, so the request no
        # longer matched the approval.  ``rerun: approved`` runs exactly the run that asked.
        if isinstance(args, dict) and str(args.get('rerun') or '').strip().lower() == 'approved':
            kept = self._approval_runs().get(self.work_id)
            if not isinstance(kept, dict):
                raise CodeRunError('이 작업에는 승인을 요청한 코드 실행이 없어요. 코드를 직접 주세요.', 'code_run_argument_invalid')
            args = dict(kept)
        code, packages, hosts, names, timeout = _arguments(args if isinstance(args, dict) else {})
        proposed = _library_argument(args, names)
        # #1470: the AI's plain words for a state change this run may ask the owner to approve.
        note = args.get('approval_note') if isinstance(args, dict) else None
        self._note = ('\n'.join(' '.join(line.split()) for line in note.splitlines() if line.strip())[:APPROVAL_NOTE_CHARS]
                      if isinstance(note, str) else '')
        # Owner choice 2026-10-14 (clear signing): "label=FIELD[=meaning]; ..." the owner reads instead of the raw request.
        fields = args.get('approval_fields') if isinstance(args, dict) else None
        self._fields = parse_approval_fields(fields)
        if seconds is not None:
            timeout = max(1, min(timeout, int(seconds)))
        node = self.node()
        if node is None:
            raise CodeRunError(UNAVAILABLE_TEXT, 'code_run_unavailable')
        masked, plain, set_hosts = self.secret_sets(names, hosts)
        libraries = self._libraries(names, proposed)
        runtime = self.install(self.store, node)
        python, roots = interpreter()
        requests = []
        with tempfile.TemporaryDirectory(prefix='agentos-run-') as directory:
            workdir = os.path.realpath(directory)
            Path(workdir, 'main.py').write_text(code, encoding='utf-8')
            # #1486: each named set's library, importable as ``lib_<set>``.
            for item in libraries.values():
                Path(workdir, item['module'] + '.py').write_text(
                    item['source'] + LIBRARY_FOOTER.replace('@REPORT@', repr(item['report'])), encoding='utf-8')
            prefix = [python, '-I', '-S']
            if packages:
                script = (f'{shlex.quote(python)} -I -m venv .venv && .venv/bin/python -m pip install --no-input '
                          '--disable-pip-version-check --no-cache-dir --only-binary=:all: '
                          + ' '.join(shlex.quote(item) for item in packages))
                # The install phase never carries a secret and reaches only the package index.
                setup = self._session(node, runtime, workdir, ['/bin/sh', '-c', script],
                                      {host: set() for host in PACKAGE_INDEX}, roots, INSTALL_SECONDS, requests, 'install')
                if setup['timed_out'] or setup['exit_status'] != 0:
                    return self._result(code, packages, hosts, setup, requests, names, failed='install')
                prefix = ['.venv/bin/python', '-I']
            command = prefix + (['-c', LAUNCHER] if libraries else ['main.py'])
            policy = {**{host: set() for host in hosts}, **set_hosts}
            states = self._load_states(names, workdir, plain)
            outcome = self._session(node, runtime, workdir, command, policy, roots, timeout, requests, 'run',
                                    masked=masked, plain=plain)
            saved = self._save_states(states)
            kept = self._keep_libraries(libraries, workdir, outcome, (node, runtime, prefix, policy, roots, masked, plain))
        result = self._result(code, packages, hosts, outcome, requests, names)
        if saved:
            result['state_saved'] = saved
        if kept:
            result['library'] = kept
        if self._asked:
            # The run that asked the owner: kept for this Work so its continuation can run it unchanged.
            self._keep_approval_run(args)
        # #1489 (rehearsal): a worker that got a set working rarely kept its steps unprompted, so the
        # next request read the documentation again.  A run that worked on a set with no saved
        # functions says, once it worked, how to keep them.
        unsaved = [name for name in names if name not in libraries and name not in self._hinted] \
            if callable(getattr(self.store, 'db', None)) else []
        if unsaved and not outcome.get('timed_out') and outcome.get('exit_status') == 0:
            from .procedures import module_name
            self._hinted.add(unsaved[0])
            result['library_hint'] = LIBRARY_HINT.format(set=unsaved[0], module=module_name(unsaved[0]))
        self._settle(outcome, result)
        return result

    def _approval_runs(self):
        try:
            rows = self.store.config(APPROVAL_RUNS_KEY, {})
        except Exception:
            return {}
        return rows if isinstance(rows, dict) else {}

    def _keep_approval_run(self, args):
        if not callable(getattr(self.store, 'put', None)):
            return
        keep = {key: args[key] for key in ('code', 'hosts', 'packages', 'secrets', 'effect', 'timeout_seconds', 'approval_note',
                                          'approval_fields', 'library') if isinstance(args, dict) and key in args}
        rows = self._approval_runs()
        rows[self.work_id] = keep
        # Bounded: the newest runs only.
        self.store.put(APPROVAL_RUNS_KEY, dict(list(rows.items())[-20:]))

    def _libraries(self, names, proposed):
        """#1486: ``{set: item}`` of the library each named set's run imports.

        A proposed module (the ``library`` argument) replaces the saved one for this run; otherwise the
        Work's own earlier proposal, else the set's current version.  A set without one has no entry.
        """
        if not names or not callable(getattr(self.store, 'db', None)):
            return {}
        from .procedures import ProcedureLibrary, digest, module_name
        library = ProcedureLibrary(self.store)
        out = {}
        for name in names:
            row = library.for_run(name, self.work_id)
            if proposed is not None:
                same = row is not None and row['digest'] == digest(proposed)
                item = {'source': proposed, 'row': row if same else None, 'proposed': not same}
            elif row is not None:
                item = {'source': row['source'], 'row': row, 'proposed': False}
            else:
                continue
            module = module_name(name)
            out[name] = {**item, 'module': module, 'report': f'.agentos-library-{module}.json'}
        return out

    @staticmethod
    def _library_report(workdir, name):
        """The run's own library report (untrusted): ``{'functions', 'examples', 'calls'}`` or None."""
        path = Path(workdir, name)
        try:
            if not path.is_file() or path.stat().st_size > LIBRARY_REPORT_BYTES:
                return None
            report = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, UnicodeDecodeError, ValueError):
            return None
        if not isinstance(report, dict):
            return None
        calls = report.get('calls') if isinstance(report.get('calls'), dict) else {}
        return {'functions': [row for row in report.get('functions') or [] if isinstance(row, dict)][:64]
                if isinstance(report.get('functions'), list) else [],
                'examples': report.get('examples') if isinstance(report.get('examples'), dict) else {},
                'calls': {key: value for key, value in calls.items()
                          if isinstance(value, list) and len(value) == 2
                          and all(isinstance(count, int) and not isinstance(count, bool) for count in value)}}

    def _keep_libraries(self, libraries, workdir, outcome, session):
        """#1486: count a used library's calls; keep a proposed one as this Work's candidate when it passes the gate.

        The candidate becomes the set's saved version only when the Work reaches its outcome
        (``ProcedureLibrary.settle``).
        """
        if not libraries:
            return {}
        from .procedures import ProcedureLibrary, clean_functions, dropped
        store = ProcedureLibrary(self.store)
        kept = {}
        for name, item in libraries.items():
            report = self._library_report(workdir, item['report'])
            calls = report['calls'] if report else {}
            if not item['proposed']:
                store.record_calls(item['row']['id'], calls)
                kept[name] = {'module': item['module'], 'state': 'used', 'version': item['row']['version']}
                continue
            problem = self._library_problem(item, report, outcome, workdir, session)
            if problem:
                kept[name] = {'module': item['module'], 'state': 'not_saved', 'reason': problem}
                continue
            row_id = store.propose(self.work_id, name, item['source'], report['functions'])
            store.record_calls(row_id, calls)
            functions = clean_functions(report['functions'])
            kept[name] = {'module': item['module'], 'state': 'saved_if_fulfilled',
                          'functions': [row['name'] for row in functions],
                          'note': ('Kept for this request. It becomes the saved library of this set when the request '
                                   'is judged fulfilled; until then later runs of this request import it too.')}
            # #7: library replaces the whole module, so a saved function left out of it is removed on save.
            current = store.current(name)
            removed = dropped(current, {'functions': functions})
            if removed:
                kept[name]['dropped'] = removed
                kept[name]['note'] += (f" It replaces saved library v{current['version']}, and these saved functions are "
                                       f"not in it: {', '.join(removed)}. If you still need them, run again with library "
                                       'set to the whole module including them; the earlier version stays kept.')
        return kept

    def _library_problem(self, item, report, outcome, workdir, session):
        """Why a proposed library is not kept, or None (#1486): the run, its report, secrets and the parameter flow."""
        from .current_context import stored_secret_values
        from .procedures import IDENTIFIER, MAX_FUNCTIONS
        if outcome.get('timed_out') or outcome.get('exit_status') != 0:
            return 'the run did not finish successfully, so the library was not checked'
        if not report:
            return f"the run did not import {item['module']}"
        functions = [row for row in report['functions'] if isinstance(row.get('name'), str) and IDENTIFIER.match(row['name'])]
        if not functions:
            return 'the library has no public functions'
        if len(functions) > MAX_FUNCTIONS:
            return f'keep at most {MAX_FUNCTIONS} public functions in one library'
        if not any(counts[0] > 0 for counts in report['calls'].values()):
            return 'no library function completed in this run; call the functions from the code'
        undocumented = [row['name'] for row in functions if not str(row.get('doc') or '').strip()]
        if undocumented:
            return 'give each public function a docstring: ' + ', '.join(undocumented[:8])
        # #1489 (rehearsal): the stored values themselves; a credential-shaped line such as an
        # Authorization header built from the environment is how a library should read a key.
        if any(value in item['source'] for value in stored_secret_values(self.store)):
            return 'the library contains a stored secret value; read values from the environment variables instead'
        examples = report['examples']
        checked = [row for row in functions if row.get('required') or row['name'] in examples]
        if len(checked) > MAX_CHECKED_FUNCTIONS:
            return f'at most {MAX_CHECKED_FUNCTIONS} functions with arguments can be checked in one library'
        deadline = self.clock() + CHECK_TOTAL_SECONDS
        for index, row in enumerate(checked):
            pair = examples.get(row['name'])
            if not (isinstance(pair, list) and len(pair) == 2 and all(isinstance(value, dict) for value in pair)):
                # #1489 (rehearsal): a worker invented an unused argument to satisfy this; say the other way out.
                return (f"give EXAMPLES[{row['name']!r}]: two dicts of arguments that differ, so Still can check that "
                        'the arguments reach the requests. A function whose requests never change needs no arguments '
                        '(read keys and tokens inside it); do not add an argument the request does not need')
            texts = []
            for number, arguments in enumerate(pair):
                left = deadline - self.clock()
                if left < 5:
                    return 'the parameter check ran out of time; save fewer functions at once'
                texts.append(self._check_call(item['module'], row['name'], arguments, workdir, session,
                                              f'{index}_{number}', left))
            problem = flow_problem(row['name'], pair, texts)
            if problem:
                return problem
        return None

    def _check_call(self, module, function, arguments, workdir, session, tag, seconds):
        """One example call in its own sandbox session; what it would send, as text (#1486)."""
        node, runtime, prefix, policy, roots, masked, plain = session
        script = f'agentos_check_{tag}.py'
        Path(workdir, script).write_text(
            'import json, sys\nsys.path.insert(0, ".")\n'
            f'import {module} as library\n'
            'try:\n'
            f'    getattr(library, {function!r})(**json.loads({json.dumps(arguments)!r}))\n'
            'except BaseException:\n'
            '    pass\n', encoding='utf-8')
        recorded = []
        try:
            self._session(node, runtime, workdir, [*prefix, script], policy, roots,
                          max(1, min(CHECK_SESSION_SECONDS, int(seconds))), [], 'check',
                          masked=masked, plain=plain, recorded=recorded)
        except CodeRunError as exc:
            if exc.code == 'code_run_interrupted':
                raise
        # Held in memory for the comparison only (an in_code value may be in a header); never kept or shown.
        return '\n'.join(f"{row['method']} {row['host']}{row['path']}?{row['query']}\n{row['headers']}\n{row['body']}"
                         for row in recorded)

    def _settle(self, outcome, result):
        """#1456: a sent state-changing request is settled ``observed`` once the run ended on its own.

        The sandbox shows Still each request, not its response; the run's code sees the response and
        its output says what happened.  A run cut off (timeout) leaves the dispatch unknown, so no later
        attempt sends another state change until it is resolved.
        """
        from .api_requests import ApiError, ApiRequests
        if self._claims:
            result['effects'] = [{'method': method, 'host': host, 'path': path} for _claim, method, host, path in self._claims]
            if outcome.get('timed_out') or outcome.get('exit_status') is None:
                result['effect'] = 'unknown'
            else:
                for claim, *_rest in self._claims:
                    try:
                        ApiRequests(self.store, self.work_id)._settle_effect(claim, 'observed', None)
                    except ApiError:
                        result['effect'] = 'unknown'
        if self._asked:
            result['approval_requested'] = list(self._asked)
        self._asked, self._claims = [], []

    def _load_states(self, names, workdir, env):
        """#1380: each named set's run state as ``<SET>_STATE_FILE`` in the run directory.

        A small JSON object the code may read and rewrite (an access token and its expiry, for
        example), so a run reuses a valid token instead of requesting a new one each time.  It is
        kept encrypted between runs, bound to the set's exact metadata, and redacted from output.
        """
        from .api_credentials import ApiCredentials
        from .api_requests import env_name, slots
        current, states = slots(self.store), {}
        for name in names:
            slot = current.get(name)
            if slot is None:
                continue
            path = Path(workdir, f'state-{name}.json')
            path.write_text(ApiCredentials(self.store).read_state(name, slot) or '{}', encoding='utf-8')
            env[env_name(name, 'STATE_FILE')] = str(path)
            states[name] = (slot, path)
        return states

    def _save_states(self, states):
        from .api_credentials import ApiCredentials
        saved = {}
        for name, (slot, path) in states.items():
            try:
                text = path.read_text(encoding='utf-8') if path.is_file() else ''
            except (OSError, UnicodeDecodeError):
                text = None
            saved[name] = text is not None and ApiCredentials(self.store).write_state(name, slot, text)
        return saved

    def _decide(self, message, policy, check=False):
        """Reads only: GET/HEAD to a host this run may reach, or a non-GET request the owner reviewed as a read.

        ``check`` (#1486): a parameter-flow check, where every other request is refused unsent.
        """
        method = str(message.get('method') or '').upper()
        host = _host(message.get('host'))
        # A host named without a port covers every port of that host, as in sandbox-runtime's
        # allowedDomains (live: an API served on :9443 under a set listing the bare host).
        if host not in policy:
            host = _hostname(host)
        if host not in policy:
            return False, f'host not allowed in this run; allowed: {", ".join(sorted(policy)) or "none"}'
        if method in READ_METHODS or f"{method} {host}{message.get('path') or '/'}" in policy[host]:
            return True, ''
        if check:
            return False, CHECK_TEXT
        if self.approvals is not None and host in self._owners:
            return self._state_change(message, method, host)
        # Name what is allowed (live: a worker read a refused path as "POST is blocked" and gave up).
        reviewed = ', '.join(sorted(policy[host])) or 'none'
        return False, (f'only GET/HEAD and owner-reviewed reads are allowed; reviewed reads here: {reviewed}. '
                       'Check the exact path, or propose the read in the secret set.')

    def _state_change(self, message, method, host):
        """#1456: a state-changing request to a secret set's host, sent once with the owner's exact approval.

        Reuses ``api_request``'s machinery: the per-action approval binding (``step_binding`` over the set,
        method, URL with its query and the body with masked values as ``{{NAME}}``), the replay and
        currentness guard and the dispatch claim (``ApiRequests._effect_guard``).  Without a consumed
        approval the request is refused and the owner is asked to approve exactly it.
        """
        from .api_requests import ApiError, ApiRequests
        from .browser_session import step_binding
        from .current_context import redact_known_secrets
        name, slot = self._owners[host]
        body = message.get('bodyText')
        if body is None or len(body) > APPROVAL_BODY_CHARS:
            return False, 'this request body cannot be shown to the owner for approval (not text, or too long)'
        path, query = str(message.get('path') or '/'), str(message.get('query') or '')
        url = f'https://{host}{path}' + (f'?{query}' if query else '')
        effect = 'payment' if slot.get('money', True) else 'mutate'
        api = ApiRequests(self.store, self.work_id, approvals=self.approvals)
        try:
            identity = api._effect_identity(slot, method, url, body)
            api._effect_guard(slot, identity)
        except ApiError as exc:
            return False, str(exc)
        binding = step_binding(self.work_id, 'code_run', url, f'{name}|{method}|{url}', argument=body,
                               state=f"code-effect-v1|{name}@{slot['revision']}|{effect}")
        if not self.approvals.consume(binding):
            shown = redact_known_secrets(self.store, ' '.join(part for part in (f'?{query}' if query else '', body) if part))
            # #1470: the AI's description comes first for the owner to read; the exact request below is
            # what is approved, so the description can never widen it.  Live 2026-10-14 (owner): plain
            # words first, money said plainly, no internal set or effect names.
            note = redact_known_secrets(self.store, getattr(self, '_note', ''))
            lines = [note] if note else []
            if effect == 'payment':
                lines.append(APPROVAL_MONEY_LINE)
            if self._fields:
                shown_lines, check = approval_field_lines(self._fields, body, query,
                                                          lambda value: redact_known_secrets(self.store, value))
                lines.append('\n'.join([APPROVAL_FIELDS_LABEL, *shown_lines, check]))
            exact = f"{APPROVAL_EXACT_LABEL} {method} {host}{redact_known_secrets(self.store, path)}"
            lines.append(exact + (f"\n{APPROVAL_CONTENT_LABEL} {shown}" if shown else ''))
            label = '\n\n'.join(lines)
            self.approvals.request(binding, label)
            self._asked.append({'method': method, 'host': host, 'path': redact_known_secrets(self.store, path)})
            return False, APPROVAL_REQUESTED_TEXT
        try:
            claim = api._effect_guard(slot, identity, method=method, effect=effect, destination=host)
        except ApiError as exc:
            return False, str(exc)
        self._claims.append((claim, method, host, redact_known_secrets(self.store, path)))
        return True, ''

    def _session(self, node, runtime, workdir, argv, policy, roots, timeout, requests, phase, masked=(), plain=None, recorded=None):
        config = {'workdir': workdir, 'argv': argv, 'allowedDomains': sorted(policy),
                  'denyRead': private_roots(), 'allowRead': roots,
                  'env': {**(plain or {}), 'PATH': '/usr/bin:/bin', 'HOME': workdir, 'LANG': 'en_US.UTF-8',
                          'PYTHONIOENCODING': 'utf-8', 'PYTHONDONTWRITEBYTECODE': '1'},
                  'masked': list(masked), 'timeoutMs': timeout * 1000, 'outputBytes': OUTPUT_BYTES}
        process = self.popen([node, str(Path(runtime) / 'runner.mjs')], cwd=str(runtime), stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1,
                             env={'PATH': f'{Path(node).parent}:/usr/bin:/bin', 'HOME': workdir})
        lines = queue.Queue()

        def pump():
            for line in process.stdout:
                lines.put(line)
            lines.put(None)
        threading.Thread(target=pump, daemon=True).start()
        deadline = self.clock() + timeout + RUNNER_GRACE
        try:
            process.stdin.write(json.dumps(config) + '\n')
            # #233: the runner process exists and has its configuration, so the code may run and send from
            # here; a failure to start it (``popen``) or to hand it the configuration let nothing out.  The
            # line is complete once written (line buffered), so the mark comes before the flush.
            from .agent_runtime import handed_off
            handed_off()
            process.stdin.flush()
            while True:
                if self.interrupted is not None and self.interrupted():
                    raise CodeRunError('작업이 중단되어 코드 실행을 멈췄어요.', 'code_run_interrupted')
                if self.clock() > deadline:
                    raise CodeRunError('코드 실행 환경이 제때 끝나지 않아 멈췄어요.', 'code_run_timeout')
                try:
                    line = lines.get(timeout=0.5)
                except queue.Empty:
                    continue
                if line is None:
                    raise CodeRunError('코드 실행 환경이 결과 없이 끝났어요.', 'code_run_failed')
                message = json.loads(line)
                if message.get('type') == 'request':
                    # #1486: a parameter-flow check records what each call would send.
                    if recorded is not None and len(recorded) < MAX_CHECKED_REQUESTS:
                        entry = {key: str(message.get(field) or '')[:APPROVAL_BODY_CHARS] for key, field in
                                 (('method', 'method'), ('host', 'host'), ('path', 'path'), ('query', 'query'),
                                  ('body', 'bodyText'))}
                        # #1489: an argument may travel in a header (a token, an account), as the request sends it.
                        pairs = message.get('headers') if isinstance(message.get('headers'), list) else []
                        entry['headers'] = '\n'.join(f'{pair[0]}: {pair[1]}' for pair in pairs[:64]
                                                     if isinstance(pair, list) and len(pair) == 2)[:APPROVAL_BODY_CHARS]
                        recorded.append(entry)
                    allow, reason = self._decide(message, policy, check=recorded is not None)
                    if allow and len(self.sent) < MAX_CHECKED_REQUESTS:
                        pairs = message.get('headers') if isinstance(message.get('headers'), list) else []
                        parts = [message.get('path'), message.get('query'), message.get('bodyText'),
                                 *(pair[1] for pair in pairs[:64] if isinstance(pair, list) and len(pair) == 2)]
                        self.sent.append(('\n'.join(str(part) for part in parts if part)[:APPROVAL_BODY_CHARS],
                                          _host(message.get('host'))[:253]))
                    requests.append({'phase': phase, 'method': str(message.get('method') or '')[:10],
                                     'host': _host(message.get('host'))[:253], 'path': str(message.get('path') or '/')[:300],
                                     'allowed': allow})
                    process.stdin.write(json.dumps({'id': message.get('id'), 'allow': allow, 'reason': reason}) + '\n')
                    process.stdin.flush()
                elif message.get('type') == 'result':
                    return {'exit_status': message.get('exit'), 'timed_out': bool(message.get('timedOut')),
                            'stdout': str(message.get('stdout') or ''), 'stderr': str(message.get('stderr') or '')}
                elif message.get('type') == 'error':
                    raise CodeRunError('코드 실행 환경을 시작하지 못했어요.', 'code_run_failed')
        except (OSError, ValueError) as exc:
            if isinstance(exc, CodeRunError):
                raise
            raise CodeRunError('코드 실행 환경과 통신하지 못했어요.', 'code_run_failed') from None
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()

    def _result(self, code, packages, hosts, outcome, requests, names=(), failed=None):
        from .current_context import redact_known_secrets
        # An in_code value can appear in a request path; the log keeps it redacted like the output.
        requests = [{**row, 'path': redact_known_secrets(self.store, row['path'])} for row in requests[:200]]
        result = {'exit_status': outcome['exit_status'], 'timed_out': outcome['timed_out'],
                  'stdout': redact_known_secrets(self.store, outcome['stdout']),
                  'stderr': redact_known_secrets(self.store, outcome['stderr']),
                  'requests': requests, 'packages': packages, 'hosts': hosts, 'secrets': list(names),
                  'sandbox': f'sandbox-runtime {RUNTIME_VERSION}',
                  'code_digest': hashlib.sha256(code.encode()).hexdigest()[:16],
                  'sources': [{'url': f"https://{row['host']}{row['path']}"} for row in requests
                              if row['allowed'] and row['phase'] == 'run'][:20]}
        if failed:
            result['failed_phase'] = failed
        return result


def evidence(result):
    """What Evidence keeps of a run: never its output, code or request bodies.

    #1456: the state-changing requests it sent (``effects``), an unresolved one (``effect: unknown``)
    and the ones waiting for the owner (``refused_because``), by method, host and path only.
    """
    kept = {key: result.get(key) for key in ('exit_status', 'timed_out', 'packages', 'hosts', 'secrets', 'sandbox', 'code_digest',
                                             'failed_phase', 'effects', 'effect') if key in result}
    if result.get('approval_requested'):
        kept['refused_because'] = 'owner-approval-requested'
        kept['approval_requested'] = result['approval_requested']
    if isinstance(result.get('library'), dict):
        # #1486: what happened to each set's library, never its source.  #1489: and why a proposal was not
        # kept (Still's own text naming functions and arguments), so the Work's records can show it.
        kept['library'] = {name: {key: (str(item[key])[:300] if key == 'reason' else item[key])
                                  for key in ('state', 'version', 'functions', 'dropped', 'reason') if item.get(key) is not None}
                           for name, item in result['library'].items() if isinstance(item, dict)}
    return kept | {'requests': [{key: row.get(key) for key in ('phase', 'method', 'host', 'path', 'allowed')}
                                              for row in (result.get('requests') or [])[:50]]}
