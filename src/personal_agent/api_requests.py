"""API-READ-01 (#1216): an authenticated API call by secret-slot reference, with response provenance.

C16: the owner's AI does the work - it picks the call, reads the data and
writes the answer.  Still removes one blocker (the AI must call an API that
needs a key it may never hold) and keeps the invariants an AI must not own:

* **Slot.** The owner registers a named slot: its secret (stored with
  ``QuickStore.secret`` like every other owner API key, so
  ``current_context.redact_known_secrets`` covers it), the hosts it may be
  sent to, the header it goes in and, optionally, the subject the key belongs
  to.  The AI passes only the slot name; Still inserts the value.  A
  redirect is never followed, so the credential never leaves a bound host.
* **Effect.** The declared ``agent_runtime.EFFECT`` is never trusted to be
  lower than the method: anything but GET/HEAD is at least ``mutate``.
  Connected nonpayment mutations use pilot authority; payment needs exact
  one-use approval through ``browser_approvals_for``. Both acquire a durable
  Work dispatch claim before sending. Only reads retry (once on transient
  failure). A crash or uncertain response leaves a claim unresolved; changing
  workers or connection revision never licenses replay. Idempotency-Key is
  additional remote assistance, not a universal exactly-once guarantee.
* **Provenance.** Every result separates the raw response from what Still
  computed, and says where it came from, when it was retrieved, what time the
  data is as of (a declared field, ``Last-Modified``, or ``unknown``), whether
  it is fresh, and whether it is complete (``partial``) and self-consistent
  (``inconsistent``).  A response for a subject other than the slot's is
  withheld.  These are general Evidence semantics: nothing here knows what
  the API is about.

Field references use the RFC 9535 JSONPath syntax, only the subset ``$``,
``.name``, ``[n]`` and ``[*]``.
"""
import email.utils
import hashlib
import ipaddress
import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit, urlunsplit

#: Owner config row: ``{name: slot}`` (never the secret).
SLOTS_KEY = 'api_slots'
#: ``QuickStore.secret`` key prefix of a slot's value.
SECRET_PREFIX = 'api_slot:'
MAX_SLOTS = 16
NAME = re.compile(r'^[a-z0-9]+(-[a-z0-9]+)*$')
HEADER = re.compile(r'^[A-Za-z0-9-]{1,64}$')
HOST = re.compile(r'^[a-z0-9.-]{1,253}(:[0-9]{1,5})?$')
READ_METHODS = ('GET', 'HEAD')
METHODS = ('GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE')
#: #1380: a code-run secret set holds named values for ``code_run`` only; ``api_request`` never sends it.
CODE_KIND = 'code'
#: Where the code uses a value: masked and substituted on egress in headers/bodies, or the real value inside the run.
FIELD_USES = ('header_or_body', 'in_code')
#: Owner-reviewed non-GET requests that only read (``draft_formats.READ_PATTERN``).
MAX_READ_REQUESTS = 8
#: #1380: refusal codes ``ApiRequests`` raises only before anything is sent (no dispatch claim, no
#: transport).  A sent request leaves an ``api_dispatch`` record of its own; ``needs_setup`` and
#: response-time codes are not here because they can follow a dispatch.
PRE_DISPATCH_CODES = frozenset({'api_slot_code_only', 'api_slot_unknown', 'api_slot_invalid', 'api_argument_invalid',
                                'api_path_invalid', 'api_read_only', 'api_approval_unrepresentable', 'approval_required',
                                'api_effect_replayed', 'api_work_inactive'})
#: #1445: the pre-dispatch refusals of a call that could not be made as written (the wrong tool for a
#: set, an unknown slot, a malformed argument or path).  Like a click on a target that was not on the
#: page (#1276) no effect asked of the service was refused; an authority refusal is not here.
UNUSABLE_CALL_CODES = frozenset({'api_slot_code_only', 'api_slot_unknown', 'api_slot_invalid', 'api_argument_invalid',
                                 'api_path_invalid'})
#: ``agent_runtime.EFFECT`` in increasing order; read and navigate change nothing.
EFFECT_ORDER = ('read', 'navigate', 'mutate', 'payment')
STATE_CHANGING_EFFECTS = ('mutate', 'payment')
#: The same 15 minutes ``current_context`` uses for a stale observation.
DEFAULT_MAX_AGE = 15 * 60
MAX_AGE_LIMIT = 31 * 86400
#: A reference time this far ahead of retrieval is not believed.
CLOCK_SKEW = 5 * 60
TIMEOUT = 15
MAX_RESPONSE_BYTES = 1_000_000
MAX_BODY_BYTES = 64 * 1024
MAX_CHECKS = 8
MAX_PATH_CHARS = 200
#: The owner's short description of a slot's API (endpoints, fields), shown to the AI: content, not code.
MAX_NOTE_CHARS = 400

APPROVAL_TEXT = ('이 API 호출은 결제 요청이라 소유자 승인이 필요합니다. '
                 '소유자가 이 단계를 승인하면 이 요청을 한 번만 이어서 처리합니다.')
SENT_UNREADABLE_TEXT = '변경 요청은 접수됐지만 응답을 읽지 못해 실제 결과는 확인되지 않았습니다. 상대 서비스에서 확인해 주세요.'
UNKNOWN_EFFECT_TEXT = ('상태를 바꾸는 API 요청이 응답 없이 끝나 실제로 반영됐는지 알 수 없습니다. '
                       '다시 보내지 않았습니다. 상대 서비스에서 결과를 먼저 확인해 주세요.')


class ApiError(ValueError):
    """A refusal with a stable ``code``; ``agent_runtime`` turns it into a ``ToolError``."""

    def __init__(self, message, code, requires=None, effect=None):
        super().__init__(message)
        self.code, self.requires, self.effect = code, requires, effect


# -- slots ----------------------------------------------------------------------------

def _normal_hosts(hosts):
    out = []
    for host in hosts if isinstance(hosts, (list, tuple)) else ():
        host = str(host or '').strip().lower()
        if not HOST.match(host) or host.startswith(('.', '-')):
            raise ApiError(f'허용 호스트 형식이 아닙니다: {host[:60]}', 'api_slot_invalid')
        if host not in out:
            out.append(host)
    if not 1 <= len(out) <= 8:
        raise ApiError('슬롯에는 허용 호스트가 1~8개 있어야 합니다.', 'api_slot_invalid')
    return out




#: Name suffixes that never denote a public destination.
PRIVATE_SUFFIXES = ('.local', '.localhost', '.internal', '.lan', '.home.arpa')


def public_host(host):
    """Whether ``host`` (``name[:port]``) names a public destination: no IP literal, localhost or private name."""
    name = re.sub(r':\d{1,5}$', '', str(host or '').strip().lower())
    try:
        ipaddress.ip_address(name.strip('[]'))
        return False
    except ValueError:
        pass
    return '.' in name and name != 'localhost' and not name.endswith(PRIVATE_SUFFIXES)


def code_set(value, hosts):
    """``{'fields', 'reads'}`` of a code-run secret set, normalized and validated against its hosts (#1380).

    #1445 part 3: the format is ``draft_formats.CodeValues``, the same model whose JSON Schema
    the AI reads, so the rules cannot drift from the format text.
    """
    from .draft_formats import CodeValues, FormatError, validate
    # #1445: a set's values reach code that may call its hosts; a local or private address
    # would let that code reach services on the owner's Mac (Still itself included).
    for host in hosts:
        if not public_host(host):
            raise ApiError(f'호스트 {host}: 내부 주소나 IP 주소는 쓸 수 없어요. 서비스의 공개 도메인을 주세요.',
                           'api_slot_invalid')
    try:
        checked = validate(CodeValues, value if isinstance(value, dict) else {}, hosts=list(hosts))
    except FormatError as exc:
        raise ApiError(str(exc), 'api_slot_invalid') from None
    return {'fields': [item.model_dump() for item in checked.fields], 'reads': checked.reads}


def env_name(set_name, field):
    """The environment variable a code run sees for one value of one secret set (#1380)."""
    return re.sub(r'[^A-Z0-9]', '_', set_name.upper()) + '_' + field


def slots(store):
    """The configured slots, validated; a malformed row is skipped, never repaired."""
    try:
        rows = store.config(SLOTS_KEY, {})
    except Exception:
        return {}
    out = {}
    for name, slot in (rows.items() if isinstance(rows, dict) else ()):
        if not isinstance(name, str) or not NAME.match(name) or not isinstance(slot, dict):
            continue
        if not isinstance(slot.get('money', False), bool):
            continue
        if 'credential_ref' in slot:
            from .api_credentials import REFERENCE
            if not isinstance(slot['credential_ref'], str) or not REFERENCE.fullmatch(slot['credential_ref']):
                continue
        try:
            hosts = _normal_hosts(slot.get('hosts'))
        except ApiError:
            continue
        header = slot.get('header') if isinstance(slot.get('header'), str) and HEADER.match(slot['header']) else 'Authorization'
        subject = slot.get('subject') if isinstance(slot.get('subject'), dict) else None
        if subject is not None and not (isinstance(subject.get('field'), str) and isinstance(subject.get('value'), str)):
            subject = None
        max_age = slot.get('max_age_seconds')
        note = ' '.join(str(slot.get('note') or '').split())[:MAX_NOTE_CHARS]
        out[name] = {'note': note, 'name': name, 'hosts': hosts, 'header': header, 'money': slot.get('money', True),
                     'scheme': slot.get('scheme') if isinstance(slot.get('scheme'), str) else '',
                     'subject': subject, 'revision': int(slot.get('revision') or 1),
                     'max_age_seconds': max_age if isinstance(max_age, int) and 0 < max_age <= MAX_AGE_LIMIT else DEFAULT_MAX_AGE}
        if 'credential_ref' in slot:
            out[name]['credential_ref'] = slot['credential_ref']
        if slot.get('kind') == CODE_KIND:
            try:
                if 'credential_ref' not in slot or 'oauth' in slot:
                    raise ValueError
                out[name].update({'kind': CODE_KIND, **code_set(slot, hosts)})
            except (ApiError, ValueError):
                out.pop(name, None)
            continue
        if 'oauth' in slot:
            from .api_oauth import configuration
            try:
                out[name]['oauth'] = configuration(slot['oauth'])
                if not slot.get('credential_ref') or header != 'Authorization' or slot.get('scheme') != 'Bearer':
                    raise ValueError
                if 'token_cache' in slot:
                    out[name]['token_cache'] = dict(slot['token_cache'])
            except (ValueError, TypeError):
                out.pop(name, None)
    return out


def save_slot(store, name, hosts, secret, *, header='Authorization', scheme='Bearer', subject_field=None,
              subject_value=None, max_age_seconds=None, note='', money=None, encrypted=False,
              _verify=None, _publish=None, oauth=None, code=None):
    """Register or replace one slot; returns its owner-readable summary (no secret).

    Host-only hooks let the contextual input path check a staged credential and
    commit its exact Settings receipt in the metadata transaction. Neither hook
    is a tool parameter. Verification publishes no slot and returns no payload.
    ``code`` (#1380) makes the slot a code-run secret set: ``{'fields', 'reads'}``
    with ``secret`` an encrypted ``{FIELD: value}`` mapping of exactly those fields.
    """
    if not isinstance(name, str) or not NAME.match(name) or len(name) > 40:
        raise ApiError('슬롯 이름은 소문자·숫자·하이픈 40자 이하입니다.', 'api_slot_invalid')
    if code is not None:
        from .api_credentials import credential_values
        if oauth is not None or not encrypted or not isinstance(secret, dict):
            raise ApiError('코드 실행용 비밀값 묶음은 암호화된 입력 경로로만 저장합니다.', 'api_slot_invalid')
        code = code_set(code, _normal_hosts(hosts))
        if set(secret) != {item['name'] for item in code['fields']}:
            raise ApiError('입력한 값이 이 묶음의 항목과 맞지 않습니다.', 'api_slot_invalid')
        try:
            credential_values(secret)
        except ValueError:
            raise ApiError('각 값은 비어 있지 않은 4096자 이하 한 줄이어야 합니다.', 'api_slot_invalid') from None
    elif oauth is None:
        if not isinstance(secret, str) or not 8 <= len(secret.strip()) <= 4096 or any(ch in secret for ch in '\r\n'):
            raise ApiError('비밀값은 8자 이상 한 줄이어야 합니다.', 'api_slot_invalid')
        secret = secret.strip()
    else:
        from .api_credentials import credential_values
        from .api_oauth import configuration
        oauth = configuration(oauth)
        if not encrypted or not isinstance(secret, dict) or header != 'Authorization' or scheme != 'Bearer':
            raise ApiError('OAuth 연결은 암호화된 키 쌍과 Bearer 방식을 사용합니다.', 'api_slot_invalid')
        credential_values(secret)
    if not isinstance(header, str) or not HEADER.match(header):
        raise ApiError('헤더 이름 형식이 아닙니다.', 'api_slot_invalid')
    if not isinstance(scheme, str) or scheme and not HEADER.fullmatch(scheme):
        raise ApiError('인증 방식 이름 형식이 아닙니다.', 'api_slot_invalid')
    subject = None
    if subject_field or subject_value:
        if not (isinstance(subject_value, str) and subject_value):
            raise ApiError('대상 필드와 값은 함께 주세요.', 'api_slot_invalid')
        parse_path(subject_field)
        subject = {'field': subject_field, 'value': subject_value}
    if max_age_seconds is not None and not (isinstance(max_age_seconds, int) and 0 < max_age_seconds <= MAX_AGE_LIMIT):
        raise ApiError('최대 허용 경과 시간은 1초~31일입니다.', 'api_slot_invalid')
    rows = store.config(SLOTS_KEY, {})
    rows = rows if isinstance(rows, dict) else {}
    if name not in rows and len(rows) >= MAX_SLOTS:
        raise ApiError(f'슬롯은 {MAX_SLOTS}개까지입니다.', 'api_slot_invalid')
    previous = rows.get(name) if isinstance(rows.get(name), dict) else {}
    if not isinstance(encrypted, bool) or previous.get('credential_ref') and not encrypted:
        raise ApiError('암호화된 API 인증 정보는 비밀 입력 경로로만 바꿀 수 있습니다.', 'api_slot_invalid')
    if (_verify is not None or _publish is not None) and not encrypted:
        raise ApiError('입력 확인은 암호화된 인증 정보에만 적용합니다.', 'api_slot_invalid')
    if money is not None and not isinstance(money, bool):
        raise ApiError('돈이 오갈 수 있는 연결인지 참 또는 거짓으로 표시하세요.', 'api_slot_invalid')
    if not isinstance(previous.get('money', False), bool):
        raise ApiError('기존 슬롯의 돈 표시를 확인할 수 없어 바꾸지 않았습니다.', 'api_slot_invalid')
    if previous.get('money') is True and money is False:
        raise ApiError('기존 돈 표시를 새 슬롯 등록으로 낮출 수 없습니다.', 'api_slot_invalid')
    # Missing legacy classification must not silently become nonpayment.
    money = previous.get('money', bool(previous)) if money is None else money
    note = ' '.join(str(note or '').split())
    private_values = [secret] if isinstance(secret, str) else list(secret.values())
    if len(note) > MAX_NOTE_CHARS or any(value in note for value in private_values):
        raise ApiError(f'API 설명은 {MAX_NOTE_CHARS}자 이하이고 비밀값을 담지 않아야 합니다.', 'api_slot_invalid')
    row = {'note': note, 'hosts': _normal_hosts(hosts), 'header': header, 'scheme': scheme or '', 'subject': subject, 'money': money,
           'revision': int(previous.get('revision') or 0) + 1, 'saved_at': time.time()}
    if max_age_seconds is not None:
        row['max_age_seconds'] = max_age_seconds
    if code is not None:
        row.update({'kind': CODE_KIND, **code})
    if oauth is not None:
        row['oauth'] = oauth
    if encrypted:
        from .api_credentials import ApiCredentials
        credentials = ApiCredentials(store)
        reference = credentials.stage(name, row, secret)
        candidate = {**row, 'name': name, 'credential_ref': reference,
                     'max_age_seconds': row.get('max_age_seconds', DEFAULT_MAX_AGE)}
        try:
            if _verify is not None:
                _verify(candidate)
            if oauth is not None and candidate.get('token_cache'):
                row['token_cache'] = candidate['token_cache']
            with store.db() as db:
                db.execute('BEGIN IMMEDIATE')
                found = db.execute('SELECT value FROM config WHERE key=?', (SLOTS_KEY,)).fetchone()
                current = json.loads(found['value']) if found else {}
                current_config = {k: v for k, v in current.get(name, {}).items() if k != 'token_cache'}
                previous_config = {k: v for k, v in previous.items() if k != 'token_cache'}
                if current_config != previous_config or name not in current and len(current) >= MAX_SLOTS:
                    raise ApiError('API 연결이 그사이 바뀌어 저장하지 않았습니다.', 'api_slot_changed')
                db.execute('INSERT INTO config VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                           (SLOTS_KEY, json.dumps({**current, name: {**row, 'credential_ref': reference}})))
                if _publish is not None:
                    _publish(db, candidate)
        except Exception:
            credentials.discard(reference)
            if candidate.get('token_cache'):
                credentials.discard(candidate['token_cache']['reference'])
            raise
        # A successful migration must not leave the old plaintext slot value.
        # If cleanup fails the new encrypted metadata remains published, so the
        # caller must not report that nothing changed or resume as fully ready.
        finish_slot_cleanup(store, name, reference, row['revision'])
    else:
        store.secret(SECRET_PREFIX + name, secret.strip())
        store.put(SLOTS_KEY, {**rows, name: row})
    return describe(slots(store)[name])


def finish_slot_cleanup(store, name, reference, revision):
    """Finish a published migration only while its exact revision is current.

    Reuse the config transaction and secret-file lock; a recovery receipt must
    never remove a later connection's legacy credential. No API call is replayed.
    """
    try:
        with store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            found = db.execute('SELECT value FROM config WHERE key=?', (SLOTS_KEY,)).fetchone()
            current = (json.loads(found['value']) if found else {}).get(name, {})
            if current.get('credential_ref') != reference or current.get('revision') != revision:
                raise ValueError('connection changed')
            store.remove_secret(SECRET_PREFIX + name)
    except Exception:
        raise ApiError('암호화된 연결은 저장됐지만 이전 인증 정보 정리를 마치지 못했습니다.',
                       'api_secret_cleanup_pending') from None


def remove_slot(store, name):
    # #258 (Codex review on #259): the slot's credential and run-state plaintexts are retired before its
    # metadata goes, so an unreadable credential (owner-held key unavailable) stops here with the slot - and
    # its credential reference - intact for a later retry.
    from .api_credentials import ApiCredentials, STATE_PREFIX
    previous_slot = slots(store).get(name)
    credentials = ApiCredentials(store)
    if isinstance(previous_slot, dict) and previous_slot.get('credential_ref'):
        reference = previous_slot['credential_ref']
        token = store.secret_items(reference).get(reference)
        if token:
            store.retire_secret_values(credentials.values({reference: token}))
    state = store.secret_items(STATE_PREFIX + name).get(STATE_PREFIX + name)
    if state:
        store.retire_secret_values(credentials._state_strings(state))
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        found = db.execute('SELECT value FROM config WHERE key=?', (SLOTS_KEY,)).fetchone()
        rows = json.loads(found['value']) if found else {}
        rows = rows if isinstance(rows, dict) else {}
        previous = rows.pop(name, None)
        db.execute('INSERT INTO config VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                   (SLOTS_KEY, json.dumps(rows)))
    if isinstance(previous, dict) and previous.get('credential_ref'):
        from .api_credentials import ApiCredentials
        ApiCredentials(store).discard(previous['credential_ref'])
        if previous.get('token_cache'):
            ApiCredentials(store).discard(previous['token_cache']['reference'])
    from .api_credentials import ApiCredentials
    ApiCredentials(store).discard_state(name)  # #1380: a removed set keeps no run state; #258: its values retired
    from .procedures import ProcedureLibrary
    ProcedureLibrary(store).remove_slot(name)  # #1486: nor its function library
    return store.remove_secret(SECRET_PREFIX + name) or previous is not None


def secret_names(store):
    """``QuickStore.secret`` keys of every configured slot, for the stored-secret redactor."""
    try:
        rows = store.config(SLOTS_KEY, {})
    except Exception:
        return ()
    return tuple(SECRET_PREFIX + name for name in rows if isinstance(name, str) and NAME.match(name)) if isinstance(rows, dict) else ()


def encrypted_secret_values(store):
    """Encrypted slot values for the existing secret redaction boundary.

    An unreadable encrypted slot must not silently skip redaction. The caller
    withholds context until the owner-held key is available again.
    """
    from .api_credentials import ApiCredentials, PREFIX
    reader = getattr(store, 'secret_items', None)
    records = reader(PREFIX) if callable(reader) else {}
    if not records:
        return ()
    credentials = ApiCredentials(store)
    # #1380: tokens a code run saved in its secret set's run state are redacted like the values.
    return credentials.values(records) + credentials.state_values()


def describe(slot):
    """What the owner and the AI may see of a slot: never the value."""
    described = {'slot': slot['name'], 'hosts': list(slot['hosts']), 'note': slot['note'], 'subject_bound': slot['subject'] is not None,
                 'max_age_seconds': slot['max_age_seconds'], 'money': slot.get('money', False)}
    if slot.get('kind') == CODE_KIND:
        # #1380: the names a code run sees and the owner-reviewed reads; never a value.
        described.update({'kind': CODE_KIND, 'reads': list(slot['reads']),
                          'env': {env_name(slot['name'], item['name']): item['use'] for item in slot['fields']}})
    return described


#: The current-context line naming the registered slots (names and hosts only).
CONTEXT_LINE = 'API slots (api_request; Still inserts the credential, never shown): '


def context_line(store, connections=None):
    """One context line naming the registered and connection slots and their hosts, or None."""
    listed = {**(connections or {}), **slots(store)}
    if not listed:
        return None
    library = None
    if any(slot.get('kind') == CODE_KIND for slot in listed.values()) and callable(getattr(store, 'db', None)):
        from .procedures import ProcedureLibrary
        try:
            library = ProcedureLibrary(store)
        except Exception:
            library = None
    def entry(name, slot):
        text = f"{name} ({', '.join(slot['hosts'])}{': ' + slot['note'] if slot['note'] else ''})"
        if slot.get('kind') != CODE_KIND:
            return text
        # Live 2026-10-14: a worker read a set's setup note ("reads only, orders blocked", written before
        # #1456) as the rule and refused an owner's order.  The note is the AI's description at setup;
        # what Still enforces is stated next to it.
        # #1504 (live again): the worker still believed such a note and told the owner orders were not
        # supported.  A setup-time description goes stale when what Still enforces changes, so a
        # worker reads only what Still states; the note stays for the owner.
        text = f"{name} ({', '.join(slot['hosts'])})"
        # #1380: a code-run secret set: environment names and owner-reviewed reads, for code_run only.
        env = ', '.join(f"{env_name(name, item['name'])} [{item['use']}]" for item in slot['fields'])
        reads = f"; reviewed reads: {', '.join(slot['reads'])}" if slot['reads'] else ''
        # #1566: an add-only redraft keeps the stored values only with the same money flag.
        reads += f"; money: {'true' if slot.get('money') else 'false'}"
        # #1486: the functions saved from earlier requests that this set's runs can import.
        saved = library.listing(name) if library is not None else ''
        return (f"{text} [code_run secrets only: {env}{reads}; any other request to these hosts (an order, a change) "
                f"is sent once after the owner approves that exact request{'; ' + saved if saved else ''}]")
    return CONTEXT_LINE + '; '.join(entry(name, slot) for name, slot in sorted(listed.items()))


def effective_effect(method, declared):
    """The effect Still applies: the declaration, never lower than the method implies."""
    declared = declared if declared in EFFECT_ORDER else 'mutate'
    floor = 'read' if str(method or 'GET').upper() in READ_METHODS else 'mutate'
    return max(declared, floor, key=EFFECT_ORDER.index)


# -- RFC 9535 JSONPath subset -----------------------------------------------------------

MISSING = object()
WILDCARD = object()
_SEGMENT = re.compile(r'\.([A-Za-z_][A-Za-z0-9_-]*)|\[(\*|\d{1,6})\]')


def parse_path(path):
    if not isinstance(path, str) or not path.startswith('$') or len(path) > MAX_PATH_CHARS:
        raise ApiError(f'필드 경로는 $로 시작하는 JSONPath여야 합니다: {str(path)[:60]}', 'api_path_invalid')
    segments, pos, rest = [], 0, path[1:]
    while pos < len(rest):
        match = _SEGMENT.match(rest, pos)
        if not match:
            raise ApiError(f'지원하는 JSONPath는 $, .이름, [번호], [*]입니다: {path[:60]}', 'api_path_invalid')
        name, index = match.groups()
        segments.append(name if name is not None else (WILDCARD if index == '*' else int(index)))
        pos = match.end()
    return segments


def select(data, path):
    """Every value ``path`` reaches; ``MISSING`` where a branch has no such member."""
    values = [data]
    for segment in parse_path(path):
        out = []
        for value in values:
            if value is MISSING:
                out.append(MISSING)
            elif segment is WILDCARD:
                members = value if isinstance(value, list) else list(value.values()) if isinstance(value, dict) else None
                out.extend(members if members is not None else [MISSING])
            elif isinstance(segment, int):
                out.append(value[segment] if isinstance(value, list) and segment < len(value) else MISSING)
            else:
                out.append(value[segment] if isinstance(value, dict) and segment in value else MISSING)
        values = out
    return values


_NUMBER = re.compile(r'^-?\d+(\.\d+)?([eE][-+]?\d+)?$')


def decimal_of(value):
    """An exact ``Decimal`` of a JSON number or numeric string, else None (bools are not numbers)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            number = Decimal(str(value))
        except InvalidOperation:
            return None
        return number if number.is_finite() else None
    if isinstance(value, str) and _NUMBER.match(value.strip()):
        return Decimal(value.strip())
    return None


def time_of(value):
    """An aware UTC datetime of an RFC 3339 string or epoch seconds/milliseconds; None otherwise.

    A time without an offset is ambiguous and is not believed.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value > 0:
        seconds = value / 1000 if value > 1e12 else value
        try:
            return datetime.fromtimestamp(seconds, timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip())
        except ValueError:
            return None
        return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None
    return None


def _iso(moment):
    return moment.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z') if moment else None


def content_digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()[:32]


def _json_list(text, label):
    if text in (None, ''):
        return []
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        raise ApiError(f'{label}는 JSON 목록이어야 합니다.', 'api_argument_invalid') from None
    if not isinstance(value, list) or len(value) > MAX_CHECKS:
        raise ApiError(f'{label}는 {MAX_CHECKS}개 이하의 JSON 목록이어야 합니다.', 'api_argument_invalid')
    return value


def provenance(data, *, status, headers, retrieved_at, as_of_field=None, max_age_seconds=DEFAULT_MAX_AGE,
               checks=(), required_fields=(), live=False):
    """Reference time, freshness, completeness and Still-derived values of one response.

    ``derived`` holds only what Still computed from the raw data (an exact
    decimal sum compared with a reported total); the raw data is never
    rewritten.  ``live`` (#1329) is a slot that reads the owner's own records
    from the provider's live API: with no reference time asked for or sent,
    the response is those records as they are at retrieval.
    """
    as_of, as_of_source = None, 'unknown'
    if as_of_field:
        values = [value for value in select(data, as_of_field) if value is not MISSING]
        as_of = time_of(values[0]) if len(values) == 1 else None
        as_of_source = f'field:{as_of_field}' if as_of else f'field-unreadable:{as_of_field}'
    if as_of is None and headers.get('last-modified'):
        try:
            modified = email.utils.parsedate_to_datetime(headers['last-modified'])
        except (TypeError, ValueError):
            modified = None
        if modified is not None and modified.tzinfo is not None:
            as_of, as_of_source = modified.astimezone(timezone.utc), 'header:last-modified'
    if as_of is None and live and not as_of_field:
        as_of, as_of_source = retrieved_at, 'live-record'
    age = None
    if as_of is not None and (as_of - retrieved_at).total_seconds() > CLOCK_SKEW:
        as_of, as_of_source = None, 'future-time-not-believed'
    if as_of is not None:
        age = max(0, int((retrieved_at - as_of).total_seconds()))
    freshness = 'unknown' if age is None else ('fresh' if age <= max_age_seconds else 'stale')
    missing = []
    for path in required_fields:
        if not isinstance(path, str):
            raise ApiError('required_fields는 JSONPath 문자열 목록입니다.', 'api_argument_invalid')
        values = select(data, path)
        if not values or any(value is MISSING or value is None for value in values):
            missing.append(path)
    derived = []
    for check in checks:
        if not isinstance(check, dict) or set(check) != {'sum', 'equals'}:
            raise ApiError('checks 항목은 {"sum": 경로, "equals": 경로}입니다.', 'api_argument_invalid')
        parts = [decimal_of(value) for value in select(data, check['sum'])]
        reported = [decimal_of(value) for value in select(data, check['equals'])]
        row = {'expression': f"sum({check['sum']})", 'compared_to': check['equals']}
        if not parts or None in parts or len(reported) != 1 or reported[0] is None:
            row.update({'matches': None, 'reason': 'missing-or-not-a-number'})
            missing.append(check['sum'] if not parts or None in parts else check['equals'])
        else:
            total = sum(parts, Decimal(0))
            row.update({'value': str(total), 'reported': str(reported[0]), 'matches': total == reported[0],
                        'difference': str(total - reported[0])})
        derived.append(row)
    inconsistent = any(row.get('matches') is False for row in derived)
    partial = status == 206 or bool(missing)
    completeness = 'inconsistent' if inconsistent else ('partial' if partial else 'complete')
    return {'retrieved_at': _iso(retrieved_at), 'as_of': _iso(as_of), 'as_of_source': as_of_source,
            'age_seconds': age, 'max_age_seconds': max_age_seconds, 'freshness': freshness,
            'completeness': completeness, 'missing_fields': list(dict.fromkeys(missing))[:MAX_CHECKS],
            'derived': derived, 'content_digest': content_digest(data)}


# -- transport --------------------------------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A credential is bound to its hosts: a redirect is answered as the 3xx it is."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http_request(method, url, headers, body, timeout):
    """``(status, lower-cased headers, body bytes)`` of one request, bounded; the default transport."""
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        response = urllib.request.build_opener(_NoRedirect).open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        data = response.read(MAX_RESPONSE_BYTES + 1)
        status = getattr(response, 'status', None) or response.code
        return status, {key.lower(): value for key, value in response.headers.items()}, data


def _loopback(host):
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def scrub(value, secret):
    """``value`` with ``secret`` replaced in every string, after JSON decoding (review P1: escaped echoes)."""
    if isinstance(value, str):
        return value.replace(secret, '[redacted]')
    if isinstance(value, list):
        return [scrub(item, secret) for item in value]
    if isinstance(value, dict):
        return {scrub(key, secret): scrub(item, secret) for key, item in value.items()}
    return value


def _transient(exc):
    return isinstance(exc, (TimeoutError, ConnectionError, urllib.error.URLError)) or \
        (isinstance(exc, OSError) and not isinstance(exc, FileNotFoundError))


class ApiRequests:
    """One Work's authenticated API calls.  ``approvals`` has ``consume(binding)``/``request(binding, text)``."""

    def __init__(self, store, work_id, *, approvals=None, transport=None, clock=time.time, sleep=time.sleep,
                 connections=None):
        self.store, self.work_id, self.approvals = store, work_id, approvals
        self.transport = transport or http_request
        self.clock, self.sleep = clock, sleep
        #: CONN-API: ``() -> {name: slot}`` of Still-held connections (``connection_slots.build``).
        self.connections = connections

    def slots(self):
        """Owner-registered slots, plus Still-held connections; an owner slot of the same name wins."""
        try:
            held = self.connections() if callable(self.connections) else {}
        except Exception:
            held = {}
        return {**(held if isinstance(held, dict) else {}), **slots(self.store)}

    def _target(self, slot, url):
        parts = urlsplit(str(url or '').strip())
        host = (parts.hostname or '').lower()
        if parts.username or parts.password or not host:
            raise ApiError('주소에 사용자 정보 없이 호스트가 있어야 합니다.', 'api_host_not_allowed')
        if parts.scheme != 'https' and not (parts.scheme == 'http' and _loopback(host)):
            raise ApiError('API 주소는 https여야 합니다(이 컴퓨터 안의 주소만 http 허용).', 'api_host_not_allowed')
        try:
            port = parts.port
        except ValueError:
            raise ApiError('주소의 포트를 확인하세요.', 'api_host_not_allowed') from None
        named = f'{host}:{port}' if port else host
        if named not in slot['hosts']:
            raise ApiError(f"슬롯 '{slot['name']}'은 {', '.join(slot['hosts'])}에만 보낼 수 있어 보내지 않았습니다.",
                           'api_host_not_allowed')
        # CONN-API: a connection's token reaches only its own provider API on a shared host.
        paths = slot.get('paths')
        if paths:
            # Review P3: no dot segment or encoded separator may walk out of the prefix.
            from urllib.parse import unquote
            decoded = unquote(parts.path or '/')
            if any(segment in ('.', '..') for segment in decoded.split('/')) or '\\' in decoded or decoded != unquote(decoded) \
                    or '%2f' in (parts.path or '').lower() or '%5c' in (parts.path or '').lower():
                raise ApiError(f"연결 '{slot['name']}'의 경로를 확인할 수 없어 보내지 않았습니다.", 'api_host_not_allowed')
        if paths and not any((parts.path or '/').startswith(prefix) for prefix in paths):
            raise ApiError(f"연결 '{slot['name']}'은 {', '.join(paths)} 경로에만 쓸 수 있어 보내지 않았습니다.",
                           'api_host_not_allowed')
        return parts, named

    def _approve(self, slot, method, url, body, effect, named, path):
        """Consume the owner's approval of exactly this call, or request it and refuse."""
        from .browser_session import binding_digest, step_binding, action_input_safe
        if not action_input_safe(self.store, {'connection': slot['name'], 'url': url, 'body': body or ''}):
            raise ApiError('실행 내용을 비밀값 없이 온전히 확인할 수 없어 승인을 요청하지 않았습니다.',
                           'api_approval_unrepresentable')
        # The full URL (query included) is the target: an approval never covers another query.
        binding = step_binding(self.work_id, 'api_request', url, f"{slot['name']}|{method}|{url}", argument=body or '',
                               state=f"api-payment-v2|{slot['name']}@{slot['revision']}|{effect}")
        approvals = self.approvals
        if approvals is not None and approvals.consume(binding):
            return binding_digest(binding)[:32]
        if approvals is not None:
            # The owner approves what is sent: the query and the body come first, so a bounded label keeps them.
            query = urlsplit(url).query
            sent = ' '.join(part for part in (f'?{query}' if query else '', str(body or '')) if part)
            label = f"{method} {path} {sent} → {named} ({slot['name']}, {effect})"
            if len(label) > 8000:
                raise ApiError('실행 내용을 비밀값 없이 온전히 확인할 수 없어 승인을 요청하지 않았습니다.',
                               'api_approval_unrepresentable')
            approvals.request(binding, label)
        raise ApiError(APPROVAL_TEXT, 'approval_required', requires='api-step-approval')

    def _effect_identity(self, slot, method, url, body):
        from .browser_session import keyed_step_binding, step_binding
        # Deliberately independent of authority revision/effect declaration.
        # Reconnecting or relabelling the same external input is not a new action.
        from .agent_runtime import WorkLedger
        with self.store.db() as db:
            try:
                root = WorkLedger.request_chain(db, self.work_id)[-1]['id']
            except ValueError:
                raise ApiError('현재 작업을 확인할 수 없어 실행하지 않았습니다.', 'api_work_inactive') from None
        binding = step_binding(root, 'api_dispatch', '',
                               json.dumps([slot['name'], method, url]), argument=body or '')
        return keyed_step_binding(self.store, binding)['digest']

    def _effect_guard(self, slot, identity, *, method=None, effect=None, destination=None):
        """Recheck current authority and atomically reserve an exact dispatch.

        The preflight (no method) also refuses replay before requesting payment
        approval. Reservation is after credentials/currentness and immediately
        before transport. A crash after reservation is conservatively unknown.
        """
        from .agent_runtime import WorkLedger, external_effect_progress, work_stop_requested, unknown_effect
        from .api_credentials import fingerprint
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                chain = WorkLedger.request_chain(db, self.work_id)
            except ValueError:
                raise ApiError('현재 작업을 확인할 수 없어 실행하지 않았습니다.', 'api_work_inactive') from None
            if (chain[0]['status'] not in ('queued', 'running')
                    or any(work['status'] in ('queued', 'running') for work in chain[1:])
                    or any(work_stop_requested(self.store, work['id']) for work in chain)
                    or callable(getattr(self.approvals, 'api_effect_current', None))
                       and not self.approvals.api_effect_current()):
                raise ApiError('중단되었거나 실행 중이 아닌 작업이라 요청을 보내지 않았습니다.', 'api_work_inactive')
            current = self.slots().get(slot['name'])
            if current is None or fingerprint(slot['name'], current) != fingerprint(slot['name'], slot):
                raise ApiError('API 연결이 바뀌어 요청을 보내지 않았습니다.', 'needs_setup',
                               requires=f"api-slot:{slot['name']}")
            observed, unknown = external_effect_progress(self.store, self.work_id)
            if unknown:
                raise ApiError(UNKNOWN_EFFECT_TEXT, 'effect_unknown', effect='unknown')
            if any(row.get('kind') == 'api-slot' and row.get('input_digest') == identity for row in observed):
                raise ApiError('이미 보낸 API 동작이라 반복 실행하지 않았습니다. 기존 결과와 남은 일을 확인하세요.',
                               'api_effect_replayed')
            # Unmatched effect observations also prevent blind cross-route replay.
            if any(unknown_effect(event.get('trace')) for work in chain for event in self.store.task_events(work['id'])):
                raise ApiError(UNKNOWN_EFFECT_TEXT, 'effect_unknown', effect='unknown')
            if method is None:
                return None
            detail = {'scope': 'api', 'evidence': {'engine': 'agentos', 'kind': 'api-slot',
                      'identity': slot['name'], 'operation': method, 'effect': effect, 'host': destination,
                      'input_digest': identity, 'revision': slot['revision'], 'work_id': self.work_id}}
            return db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                              (chain[-1]['id'], 'api_dispatch', 'dispatching', json.dumps(detail), time.time())).lastrowid

    def _settle_effect(self, claim, status, response_status):
        try:
            if claim is not None:
                with self.store.db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    row = db.execute("SELECT detail FROM tool_events WHERE id=? AND tool='api_dispatch' AND status='dispatching'", (claim,)).fetchone()
                    if row is None:
                        raise ApiError('API 실행 기록을 확인할 수 없어 결과를 확정하지 않았습니다.', 'effect_unknown', effect='unknown')
                    detail = json.loads(row['detail'])
                    detail['evidence']['response_status'] = response_status
                    db.execute('UPDATE tool_events SET status=?,detail=? WHERE id=?', (status, json.dumps(detail), claim))
        except Exception:
            raise ApiError(UNKNOWN_EFFECT_TEXT, 'effect_unknown', effect='unknown') from None

    def call(self, args):
        slot = self.slots().get(args.get('slot'))
        if slot is None:
            names = ', '.join(sorted(self.slots())) or '없음'
            raise ApiError(f'등록된 API 슬롯이 아닙니다. 쓸 수 있는 슬롯: {names}.', 'api_slot_unknown')
        return self._call_slot(args, slot)

    def check_connection(self, candidate, args, *, current=lambda: None):
        """Host-only readiness of an unpublished encrypted input candidate.

        Reuse the ordinary transport, response/subject checks and redaction, but
        never resolve a same-named active slot or allow a mutation. The caller
        owns the current owner/Work/draft check and atomic publication afterwards.
        Only bounded readiness evidence escapes; the response is not task output.
        """
        from .api_credentials import ApiCredentials
        if (not isinstance(args, dict) or args.get('method', 'GET') not in READ_METHODS
                or args.get('effect', 'read') != 'read' or args.get('body') is not None):
            raise ApiError('연결 확인에는 읽기 요청만 사용할 수 있습니다.', 'api_read_only')
        current()
        if candidate.get('oauth'):
            from .api_oauth import resolve
            secret, finish = resolve(self.store, candidate['name'], candidate, transport=self.transport,
                                     clock=self.clock, current=current, staged=True)
        else:
            secret = ApiCredentials(self.store).read(candidate['name'], candidate)
            finish = lambda _status: current()
        probe = {**candidate, 'read_only': True,
                 'token': lambda: (secret, finish)}
        probe.pop('credential_ref', None)
        result = self._call_slot({**args, 'slot': candidate['name']}, probe, before_send=current)
        current()
        if (not 200 <= result['provenance']['status'] < 300 or result['data'] is None
                or result['partial'] or result['inconsistent'] or result['stale']):
            raise ApiError('필요한 응답을 확인하지 못해 연결을 저장하지 않았습니다.', 'api_not_ready')
        return evidence(result)

    def _call_slot(self, args, slot, *, before_send=None):
        """Run the same bounded transport for active calls and input readiness."""
        if slot.get('kind') == CODE_KIND:
            raise ApiError(f"'{slot['name']}'은 코드 실행(code_run) 전용 비밀값 묶음이라 api_request로 보내지 않았습니다.",
                           'api_slot_code_only')
        method = str(args.get('method') or 'GET').upper()
        if method not in METHODS:
            raise ApiError('지원하는 메서드는 ' + ', '.join(METHODS) + '입니다.', 'api_argument_invalid')
        if slot.get('read_only') and method not in READ_METHODS:
            # CONN-API: a read grant is used for reads only; changes go through the existing drafts and approvals.
            raise ApiError(f"연결 '{slot['name']}'은 읽기 전용이라 {method} 요청을 보내지 않았습니다.", 'api_read_only')
        url = str(args.get('url') or '').strip()
        parts, named = self._target(slot, url)
        path = parts.path or '/'
        # Fragments never reach HTTP; they cannot create a fresh dispatch identity.
        url = urlunsplit((parts.scheme.lower(), named, path, parts.query, ''))
        # CONN-API: a read grant used with GET/HEAD (enforced above) cannot change anything.
        effect = 'read' if slot.get('read_only') else effective_effect(method, args.get('effect'))
        if slot.get('money') is True and effect in STATE_CHANGING_EFFECTS:
            effect = 'payment'
        body = args.get('body') or None
        if body is not None and (method in READ_METHODS or len(body.encode()) > MAX_BODY_BYTES):
            raise ApiError('본문은 GET/HEAD가 아닌 요청에만, 64KB 이하로 보낼 수 있습니다.', 'api_argument_invalid')
        checks = _json_list(args.get('checks'), 'checks')
        required = _json_list(args.get('required_fields'), 'required_fields')
        if args.get('as_of_field'):
            parse_path(args['as_of_field'])
        max_age = slot['max_age_seconds']
        if args.get('max_age_seconds'):
            text = str(args['max_age_seconds']).strip()
            if not text.isdigit() or not 0 < int(text) <= MAX_AGE_LIMIT:
                raise ApiError('max_age_seconds는 1~2678400 사이 정수입니다.', 'api_argument_invalid')
            # The AI may only tighten the slot's freshness bound, never widen it (review P1).
            max_age = min(int(text), max_age)
        identity = self._effect_identity(slot, method, url, body) if effect in STATE_CHANGING_EFFECTS else None
        if identity:
            self._effect_guard(slot, identity)
        idempotency = self._approve(slot, method, url, body, effect, named, path) if effect == 'payment' else identity[:32] if identity else None
        claim = None
        finish = None
        if slot.get('credential_ref'):
            from .api_credentials import ApiCredentials
            try:
                if slot.get('oauth'):
                    from .api_oauth import resolve
                    secret, finish = resolve(self.store, slot['name'], slot, transport=self.transport, clock=self.clock,
                        current=lambda: ApiCredentials(self.store).assert_current(slot['name'], slot))
                else:
                    secret, finish = ApiCredentials(self.store).resolve(slot['name'], slot)
            except Exception:
                raise ApiError('API 인증 정보를 사용할 수 없어 호출하지 않았습니다.', 'needs_setup',
                               requires=f"api-slot:{slot['name']}") from None
        elif callable(slot.get('token')):
            # CONN-API: the connector resolves, renews and checks the token for this one call.
            try:
                secret = slot['token']()
                if isinstance(secret, tuple):
                    secret, finish = secret
            except Exception:
                raise ApiError(f"연결 '{slot['name']}'을 지금 쓸 수 없어 호출하지 않았습니다. 연결을 다시 확인해 주세요.",
                               'needs_setup', requires=f"connection:{slot['name']}") from None
        else:
            secret = self.store.secret(SECRET_PREFIX + slot['name'])
        if not secret:
            raise ApiError(f"슬롯 '{slot['name']}'의 비밀값이 없어 호출하지 않았습니다. 먼저 등록해 주세요.", 'needs_setup',
                           requires=f"api-slot:{slot['name']}")
        headers = {slot['header']: f"{slot['scheme']} {secret}".strip() if slot['scheme'] else secret,
                   'Accept': 'application/json', 'User-Agent': 'personal-agentos'}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        if idempotency:
            headers['Idempotency-Key'] = idempotency
        tries = 2 if effect not in STATE_CHANGING_EFFECTS else 1
        attempts = 0
        while True:
            if before_send is not None:
                before_send()
            if slot.get('credential_ref'):
                try:
                    ApiCredentials(self.store).assert_current(slot['name'], slot)
                except Exception:
                    raise ApiError('API 연결이 바뀌어 요청을 보내지 않았습니다.', 'needs_setup',
                                   requires=f"api-slot:{slot['name']}") from None
            if identity:
                claim = self._effect_guard(slot, identity, method=method, effect=effect, destination=named)
            attempts += 1
            from .agent_runtime import handed_off
            handed_off()   # #233: the request leaves Still's own checks here
            try:
                status, response_headers, raw = self.transport(method, url, headers, body.encode() if body else None, TIMEOUT)
            except Exception as exc:
                if identity:
                    raise ApiError(UNKNOWN_EFFECT_TEXT, 'effect_unknown', effect='unknown') from None
                if not _transient(exc):
                    raise ApiError('API 요청을 보내지 못했습니다.', 'api_unavailable') from None
                if attempts < tries:
                    self.sleep(1)
                    continue
                raise ApiError(f'API가 응답하지 않았습니다({attempts}회 시도). 잠시 후 다시 시도해 주세요.', 'api_unavailable') from None
            if (status >= 500 or status == 429) and attempts < tries:
                self.sleep(1)
                continue
            break
        if identity and status in (401, 403):
            # HTTP authentication/authorization refusal is not an accepted change.
            # Settle before token invalidation; never retry this transport here.
            self._settle_effect(claim, 'rejected', status)
        if callable(finish):
            # CONN-API review P2: a 401 marks the connection for reauthentication; a connection that
            # changed while the call was in flight leaves the response unused.
            try:
                finish(status)
            except Exception:
                raise ApiError(f"연결 '{slot['name']}'이 그사이 바뀌어 응답을 쓰지 않았습니다. 연결을 다시 확인해 주세요.",
                               'needs_setup', requires=f"connection:{slot['name']}",
                               effect='unknown' if identity and status not in (401, 403) else None) from None
        if identity and status not in (401, 403):
            from .api_credentials import fingerprint
            current = self.slots().get(slot['name'])
            if current is None or fingerprint(slot['name'], current) != fingerprint(slot['name'], slot):
                raise ApiError('API 연결이 그사이 바뀌어 응답을 쓰지 않았습니다.', 'needs_setup',
                               requires=f"api-slot:{slot['name']}", effect='unknown')
        retrieved = datetime.fromtimestamp(self.clock(), timezone.utc)
        if 300 <= status < 400:
            raise ApiError('API가 다른 주소로 보내려 해 따라가지 않았습니다(키는 허용 호스트에만 보냅니다).', 'api_redirect_refused',
                           effect='unknown' if identity else None)
        if status in (401, 403):
            raise ApiError(f"API가 인증을 거절했습니다(HTTP {status}). 슬롯 '{slot['name']}'의 키나 권한을 확인해 주세요.",
                           'api_unauthorized', requires=f"api-slot:{slot['name']}")
        if status >= 400:
            raise ApiError(f'API가 오류로 답했습니다(HTTP {status}, {method} {path}).'
                           + (' 이 경로가 없습니다. 슬롯 설명의 경로를 확인하세요.' if status == 404 else ''), 'api_error',
                           effect='unknown' if identity else None)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ApiError('응답이 너무 커서 읽지 않았습니다.' + (' ' + SENT_UNREADABLE_TEXT if effect in STATE_CHANGING_EFFECTS else ''),
                           'api_response_too_large', effect='unknown' if effect in STATE_CHANGING_EFFECTS else None)
        # The key never comes back to the AI, even if the service echoes it.
        text = raw.decode('utf-8', 'replace').replace(secret, '[redacted]')
        try:
            data = json.loads(text) if text.strip() else None
        except ValueError:
            raise ApiError('응답이 JSON이 아니어서 쓰지 않았습니다.' + (' ' + SENT_UNREADABLE_TEXT if effect in STATE_CHANGING_EFFECTS else ''),
                           'api_not_json', effect='unknown' if effect in STATE_CHANGING_EFFECTS else None) from None
        # An escaped echo (``\"`` or ``\u`` in the JSON text) decodes back to the key: scrub the decoded values too.
        data = scrub(data, secret)
        try:
            private_values = encrypted_secret_values(self.store)
        except Exception:
            raise ApiError('API 응답의 인증 정보 가림을 확인할 수 없어 응답을 쓰지 않았습니다.',
                           'api_redaction_unavailable', effect='unknown' if effect in STATE_CHANGING_EFFECTS else None) from None
        for value in private_values:
            data = scrub(data, value)
        # A read for another subject is withheld.  A sent change already happened, so it is not
        # reported as a failure; its response body is withheld instead when it is not the
        # slot's subject (review P1: another account's data must not reach the AI either way).
        subject = slot['subject']
        subject_verified = False
        if subject is not None:
            found = [value for value in select(data, subject['field']) if value is not MISSING]
            subject_verified = len(found) == 1 and str(found[0]) == subject['value']
            if not subject_verified and effect not in STATE_CHANGING_EFFECTS:
                raise ApiError(f"응답이 슬롯 '{slot['name']}'에 묶인 대상의 것인지 확인되지 않아 쓰지 않았습니다.",
                               'api_subject_mismatch')
            if not subject_verified:
                data = None
        record = provenance(data, status=status, headers=response_headers, retrieved_at=retrieved,
                            as_of_field=args.get('as_of_field') or None, max_age_seconds=max_age,
                            checks=checks, required_fields=required, live=slot.get('live') is True)
        record.update({'source': {'slot': slot['name'], 'host': named, 'path': path, 'method': method,
                                  **({'connection': slot['connection']} if slot.get('connection') else {}),
                                  # The owner can tell which resource was asked for (the key is never in a URL).
                                  **({'query': parts.query[:200]} if parts.query else {})},
                       'status': status, 'effect': effect, 'attempts': attempts,
                       'subject_verified': subject_verified})
        if claim is not None:
            record['dispatch_event'] = claim
        if claim is not None and (status == 202 or subject is not None and not subject_verified):
            record.update(state='outcome-unknown', completeness='partial')
        else:
            self._settle_effect(claim, 'observed', status)
        return {'data': data, 'provenance': record, 'sources': [f'{parts.scheme}://{named}{path}'],
                # Typed Evidence qualifiers (``agent_runtime.EVIDENCE_QUALIFIER_FLAGS``).
                'stale': record['freshness'] == 'stale', 'as_of_unknown': record['freshness'] == 'unknown',
                'partial': record['completeness'] == 'partial', 'inconsistent': record['completeness'] == 'inconsistent'}


def evidence(result):
    """The durable record of one call: provenance only, never the payload or the key."""
    record = result.get('provenance') if isinstance(result.get('provenance'), dict) else {}
    keep = ('source', 'status', 'effect', 'attempts', 'retrieved_at', 'as_of', 'as_of_source', 'age_seconds',
            'max_age_seconds', 'freshness', 'completeness', 'missing_fields', 'content_digest', 'subject_verified', 'dispatch_event', 'state')
    out = {key: record[key] for key in keep if key in record}
    out['derived'] = [{key: row[key] for key in ('expression', 'compared_to', 'matches', 'reason') if key in row}
                      for row in record.get('derived') or ()]
    out['sources'] = list(result.get('sources') or ())[:4]
    return out
