"""Contextual API secret input over Settings, PhoneInput and the common queue.

Only public connection metadata goes through the AI. This module mediates the
owner's input and readiness; the chosen AI still performs the original request.
"""
import hashlib
import json
import threading
import time

from . import api_requests, phone_input, work_mode
from .api_requests import ApiError
from .current_context import redact_known_secrets
from .mcp_setup import remote_url, SECRET_PATTERN, SENSITIVE


def value(store, text):
    """Canonical non-secret draft; unsupported credential shapes stay explicit."""
    if not isinstance(text, str) or len(text) > 4000:
        raise ValueError('인증 정보를 제외한 API 연결 정보를 JSON으로 주세요.')
    try:
        data = json.loads(text)
    except ValueError:
        raise ValueError('인증 정보를 제외한 API 연결 정보를 JSON으로 주세요.') from None
    fields = {'name', 'hosts', 'header', 'scheme', 'money', 'note', 'subject_field', 'subject_value', 'probe'}
    if redact_known_secrets(store, text) != text or SECRET_PATTERN.search(text) or SENSITIVE.search(text):
        raise ValueError('대화에는 비밀값 없이 API 연결의 공개 정보만 주세요.')
    if isinstance(data, dict) and 'fields' in data:
        # #1380: a code-run secret set; #1445 part 3: its format is one model (``draft_formats``).
        from .draft_formats import CodeSetDraft, FormatError, validate
        try:
            draft = validate(CodeSetDraft, data)
        except FormatError as exc:
            raise ValueError(str(exc)) from None
        return json.dumps(draft.model_dump(), sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    if not isinstance(data, dict) or set(data) not in (fields, fields | {'oauth'}):
        raise ValueError('API 연결 정보의 키를 settings_read의 형식대로 주세요.')
    if not isinstance(data['name'], str) or not api_requests.NAME.fullmatch(data['name']) or len(data['name']) > 40:
        raise ValueError('연결 이름을 소문자·숫자·하이픈 40자 이하로 주세요.')
    data['hosts'] = api_requests._normal_hosts(data['hosts'])
    for key in ('header', 'scheme'):
        if not isinstance(data[key], str) or not (key == 'scheme' and not data[key] or api_requests.HEADER.fullmatch(data[key])):
            raise ValueError('API 인증 헤더와 방식 이름을 확인하세요.')
    if not isinstance(data['money'], bool):
        raise ValueError('돈이 오갈 수 있는 연결인지 표시하세요.')
    if not isinstance(data['note'], str) or len(data['note']) > api_requests.MAX_NOTE_CHARS:
        raise ValueError('연결 설명은 400자 이하로 주세요.')
    if data['subject_field'] is not None or data['subject_value'] is not None:
        api_requests.parse_path(data['subject_field'])
        if not isinstance(data['subject_value'], str) or not data['subject_value'] or len(data['subject_value']) > 200:
            raise ValueError('확인할 대상 필드와 값을 함께 주세요.')
    probe = data['probe']
    if not isinstance(probe, dict) or set(probe) != {'url', 'required_fields'}:
        raise ValueError('읽기 확인 주소와 필요한 응답 필드를 주세요.')
    probe['url'] = remote_url(probe['url'], lambda v: redact_known_secrets(store, v))
    fields = probe['required_fields']
    if not isinstance(fields, list) or not 1 <= len(fields) <= api_requests.MAX_CHECKS:
        raise ValueError('필요한 응답 필드는 1~8개로 주세요.')
    for field in fields:
        api_requests.parse_path(field)
    if 'oauth' in data:
        from .api_oauth import configuration
        data['oauth'] = configuration(data['oauth'])
        if data['header'] != 'Authorization' or data['scheme'] != 'Bearer':
            raise ValueError('OAuth 연결은 Authorization 헤더의 Bearer 방식을 사용합니다.')
    api_requests.ApiRequests(store, '')._target({'name': data['name'], 'hosts': data['hosts']}, probe['url'])
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def code_draft(data):
    """Whether a canonical draft is a code-run secret set (#1380)."""
    return 'fields' in data


def kept_fields(store, data):
    """Names of an existing code-run set's values that a draft keeps while only adding values (#1566).

    Live #1380: the first set lacked an identifier only the owner holds, and adding it re-asked
    for every key already saved.  A draft that keeps the set's hosts, money flag and every
    stored field (same name and use) and adds at least one field asks only for the added
    values; the stored ones stay inside the host.  Any other change asks for every value.
    """
    if not code_draft(data):
        return frozenset()
    slot = api_requests.slots(store).get(data['name'])
    if not isinstance(slot, dict) or slot.get('kind') != api_requests.CODE_KIND or not slot.get('credential_ref'):
        return frozenset()
    stored = {item['name']: item['use'] for item in slot['fields']}
    drafted = {item['name']: item['use'] for item in data['fields']}
    if (sorted(slot['hosts']) != sorted(data['hosts']) or bool(slot.get('money')) != bool(data['money'])
            or len(drafted) == len(stored) or any(drafted.get(name) != use for name, use in stored.items())):
        return frozenset()
    return frozenset(stored)


def before(store, text):
    name = json.loads(text)['name']
    row = store.config(api_requests.SLOTS_KEY, {}).get(name)
    if isinstance(row, dict):
        row = {key: item for key, item in row.items() if key != 'token_cache'}
    return hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()


def guard(service, draft):
    job = service.store.job(draft.get('work_id')) if draft.get('work_id') else None
    if (not job or service.settings_owner(job) != draft['owner'] or job['channel'] != draft['channel']
            or service.work_stopped(job['id'])):
        raise ValueError('원래 작업의 소유자와 현재 상태를 확인하지 못했습니다.')
    owner_id = service.connector_owner_id(job)
    if isinstance(job.get('chat_id'), int):
        cfg = service.store.config('telegram', {})
        if not cfg.get('enabled') or cfg.get('user_id') != job['chat_id']:
            raise ValueError('원래 작업의 연결된 대화를 확인하지 못했습니다.')
    return {'generation': service._owner_generation(owner_id),
            'request': hashlib.sha256(job['message'].encode()).hexdigest()}


def current(service, draft, session=None):
    row = service.settings_orchestrator._drafts().get(draft['id']) or {}
    if (row.get('digest') != draft['digest'] or row.get('digest') != service.settings_orchestrator._digest(row)
            or row.get('state') not in ('applying', 'awaiting-input')
            or guard(service, row) != row.get('setup_guard')
            or before(service.store, row['after']) != row['before']
            or session is not None and not session.code_ok(session.code)):
        raise ValueError('입력 요청이 만료되었거나 원래 작업 또는 연결이 바뀌었습니다.')
    return row


def reads_update(service, draft):
    """A confirmed code-run draft that changes only an existing set's reviewed reads: applied in place, or False.

    Live 2026-10-14: a request that changes nothing (a hash, a signature) needed adding to the
    set's reviewed reads, and re-saving the set would ask the owner to type every key again.
    The owner's confirmation of the draft is the review; the stored values, hosts, fields and
    money flag stay as they are.  The receipt binds the draft to the new revision, so the
    original Work continues as after an input page.
    """
    data = json.loads(draft['after'])
    if not code_draft(data):
        return False
    current(service, draft)
    slot = api_requests.slots(service.store).get(data['name'])
    if (not isinstance(slot, dict) or slot.get('kind') != api_requests.CODE_KIND
            or sorted(slot['hosts']) != sorted(data['hosts']) or bool(slot.get('money')) != bool(data['money'])
            or [(item['name'], item['use']) for item in slot['fields']] != [(item['name'], item['use']) for item in data['fields']]
            or sorted(slot['reads']) == sorted(data['reads'])):
        return False
    orchestrator = service.settings_orchestrator
    with service.store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        found = db.execute('SELECT value FROM config WHERE key=?', (api_requests.SLOTS_KEY,)).fetchone()
        rows = json.loads(found['value']) if found else {}
        row = rows.get(data['name'])
        if not isinstance(row, dict) or row.get('credential_ref') != slot.get('credential_ref'):
            raise ValueError('연결이 그사이 바뀌어 적용하지 않았습니다.')
        revision = hashlib.sha256(json.dumps([row.get('revision'), sorted(data['reads'])]).encode()).hexdigest()[:16]
        row.update(reads=sorted(data['reads']), revision=revision)
        db.execute('UPDATE config SET value=? WHERE key=?', (json.dumps(rows), api_requests.SLOTS_KEY))
        found = db.execute("SELECT value FROM config WHERE key='settings_change_drafts'").fetchone()
        drafts = json.loads(found['value']) if found else {}
        latest = drafts.get(draft['id']) or {}
        if latest.get('state') != 'applying' or latest.get('digest') != draft['digest'] \
                or orchestrator._digest(latest) != draft['digest']:
            raise ValueError('확인 요청이 바뀌어 적용하지 않았습니다.')
        latest['api_receipt'] = {'reference': row['credential_ref'], 'revision': revision, 'readiness': {'stored': True}}
        db.execute("UPDATE config SET value=? WHERE key='settings_change_drafts'", (json.dumps(drafts),))
    return True


def start(service, draft):
    current(service, draft)
    data = json.loads(draft['after'])
    orchestrator = service.settings_orchestrator
    attempts = [row for row in orchestrator._drafts().values() if isinstance(row, dict)
                and row.get('work_id') == draft['work_id'] and row.get('category') == 'api_connections'
                and 'applying_at' in row]
    if len(attempts) > 3:
        raise ValueError('이 작업의 입력 링크 생성 한도에 도달했어요. 다른 연결 방법을 검토하세요.')

    def ended(reason):
        if reason not in ('done', 'cancelled'):
            orchestrator._settle(draft['id'], 'expired', 'expired', settled_at=orchestrator.now())
            service.resume_settings_work()

    with service.lock:
        if service.phone_input_session() is not None:
            raise ValueError(phone_input.BUSY_TEXT)
        session = phone_input.PhoneInput(phone_input.API_SLOT, data['name'] + ' API 연결', store=service.store, on_end=ended,
                                        popen=service.phone_input_popen or phone_input.subprocess.Popen)
        session.api_draft = dict(draft)
        session.api_kept = kept_fields(service.store, data)
        session.submit_lock = threading.Lock()
        service._phone_input = session
        service._phone_input_started = True
    try:
        # #208: in work mode the one-time page opens on this Mac only, never through a tunnel or Telegram.
        if isinstance(service.store.job(draft['work_id']).get('chat_id'), int) and not work_mode.active(service.store):
            cfg = service.store.config('telegram', {})
            job = service.store.job(draft['work_id'])
            if not cfg.get('enabled') or cfg.get('user_id') != job.get('chat_id'):
                raise ValueError(phone_input.NOT_PAIRED_TEXT)
            session.start(service.local_server_port)
            service.telegram.call('sendMessage', {'chat_id': cfg['user_id'],
                'text': phone_input.LINK_TEXT.format(label=session.label, minutes=10, link=session.link),
                'link_preview_options': {'is_disabled': True}})
            return 'API 인증 정보를 입력할 일회용 링크를 Telegram으로 보냈어요. 확인되면 원래 작업을 이어갑니다.'
        session.start_local(service.local_server_port)
        return 'API 인증 정보는 이 일회용 화면에 입력해 주세요. 확인되면 원래 작업을 이어갑니다.\n' + session.link
    except Exception:
        session.finish('failed')
        raise ValueError('비밀 입력 링크를 열지 못했습니다. 같은 연결 요청에서 다시 시도해 주세요.') from None


def published(service, draft):
    """A still-current publication can wait for its key/cleanup to recover."""
    receipt = draft.get('api_receipt') or {}
    try:
        if guard(service, draft) != draft.get('setup_guard'):
            return None
        name = json.loads(draft['after'])['name']
        slot = api_requests.slots(service.store).get(name) or {}
        if (not receipt.get('reference') or receipt['reference'] != slot.get('credential_ref')
                or receipt.get('revision') != slot.get('revision')):
            return None
        return name, slot
    except (ValueError, KeyError, TypeError):
        return None


def ready(service, draft, *, cleanup=False):
    try:
        binding = published(service, draft)
        if binding is None:
            return False
        name, slot = binding
        from .api_credentials import ApiCredentials
        ApiCredentials(service.store).read(name, slot)
        if cleanup:
            api_requests.finish_slot_cleanup(service.store, name, slot['credential_ref'], slot['revision'])
        return not service.store.secret(api_requests.SECRET_PREFIX + name)
    except (ValueError, KeyError, TypeError):
        return False


def reconcile(service, orchestrator=None):
    """Recover input waits and committed receipts without repeating an API call."""
    orchestrator = orchestrator or service.settings_orchestrator
    for row in list(orchestrator._drafts().values()):
        if not isinstance(row, dict) or row.get('category') != 'api_connections':
            continue
        if row.get('state') == 'awaiting-input':
            session = service.phone_input_session()
            if session is None or getattr(session, 'api_draft', {}).get('id') != row['id'] or session.expired():
                orchestrator._settle(row['id'], 'expired', 'expired', settled_at=orchestrator.now())
        elif row.get('state') in ('applying', 'unknown') and row['id'] not in orchestrator._in_flight:
            if not row.get('api_receipt'):
                # Publication and receipt are atomic. A missing receipt means
                # no connection was installed; a read probe can be retried only
                # through a fresh owner input page, within the same attempt cap.
                orchestrator._settle(row['id'], 'expired', 'expired', settled_at=orchestrator.now())
                continue
            if row.get('state') == 'applying':
                row = orchestrator._settle(row['id'], 'unknown', 'unknown', 'interrupted', settled_at=orchestrator.now())
            if row.get('api_receipt') and ready(service, row, cleanup=True):
                orchestrator._settle(row['id'], 'applied', 'applied',
                                    expected={'state': 'unknown', 'digest': row['digest']})


def submit(service, session, body):
    """HTTP-only secret boundary. One in-flight submission per input page."""
    if session.kind != phone_input.API_SLOT or not isinstance(body, dict):
        raise ValueError('이 링크의 인증 정보 입력을 확인하세요.')
    data = json.loads(session.api_draft['after'])
    kept = getattr(session, 'api_kept', frozenset())
    expected = ({item['name'] for item in data['fields']} - kept if code_draft(data)
                else {'client_id', 'client_secret'} if data.get('oauth') else {'secret'})
    if set(body) != expected:
        raise ValueError('이 링크의 인증 정보 입력을 확인하세요.')
    if not session.submit_lock.acquire(blocking=False):
        raise ValueError('입력을 확인하고 있어요. 잠시 기다려 주세요.')
    orchestrator = service.settings_orchestrator
    draft = session.api_draft
    try:
        current(service, draft, session)
        with orchestrator._confirming:
            row = orchestrator._settle(draft['id'], 'applying', None,
                expected={'state': 'awaiting-input', 'digest': draft['digest']})
            orchestrator._in_flight.add(draft['id'])
        data = json.loads(row['after'])
        api = api_requests.ApiRequests(service.store, row['work_id'],
                                      transport=getattr(service, 'api_input_transport', None),
                                      clock=getattr(service, 'api_input_clock', time.time))
        observed = {}

        def verify(candidate):
            observed.update(api.check_connection(candidate,
                {'url': data['probe']['url'], 'required_fields': json.dumps(data['probe']['required_fields'])},
                current=lambda: current(service, draft, session)))

        def publish(db, candidate):
            # The transaction already contains the candidate slot; validate the
            # input/owner here without comparing it to the old slot again.
            found = db.execute("SELECT value FROM config WHERE key='settings_change_drafts'").fetchone()
            drafts = json.loads(found['value']) if found else {}
            latest = drafts.get(draft['id']) or {}
            if (latest.get('state') != 'applying' or latest.get('digest') != draft['digest']
                    or orchestrator._digest(latest) != draft['digest'] or not session.code_ok(session.code)
                    or guard(service, latest) != latest.get('setup_guard')):
                raise ValueError('입력 요청 또는 원래 작업이 바뀌어 저장하지 않았습니다.')
            latest['api_receipt'] = {'reference': candidate['credential_ref'], 'revision': candidate['revision'],
                                     'readiness': observed}
            db.execute("UPDATE config SET value=? WHERE key='settings_change_drafts'", (json.dumps(drafts),))

        if code_draft(data):
            # #1380: no probe can know the API's shape; the stored values are the readiness,
            # and the original Work's own code run shows whether they work.
            observed['stored'] = True
            values = dict(body)
            if kept:
                # current() above proved the stored set unchanged since the draft.
                from .api_credentials import ApiCredentials
                stored = ApiCredentials(service.store).read(data['name'], api_requests.slots(service.store)[data['name']])
                values.update({name: stored[name] for name in kept})
            api_requests.save_slot(service.store, data['name'], data['hosts'], values,
                note=data['note'], money=data['money'], encrypted=True, _publish=publish,
                code={'fields': data['fields'], 'reads': data['reads']})
            response = '값을 안전하게 저장했습니다. 원래 작업을 이어가며 실제로 동작하는지 확인합니다.'
        else:
            api_requests.save_slot(service.store, data['name'], data['hosts'], body if data.get('oauth') else body['secret'],
                header=data['header'], scheme=data['scheme'], note=data['note'], money=data['money'],
                subject_field=data['subject_field'], subject_value=data['subject_value'], encrypted=True,
                _verify=verify, _publish=publish, oauth=data.get('oauth'))
            response = '연결에 필요한 읽기를 확인했습니다. 원래 작업을 이어갑니다.'
        orchestrator._settle(draft['id'], 'applied', 'applied',
                            expected={'state': 'applying', 'digest': draft['digest']})
        session.finish_later('done')
        service.resume_settings_work()
        return {'ok': True, 'response': response}
    except Exception as exc:
        latest = orchestrator._drafts().get(draft['id']) or {}
        # Publication with incomplete cleanup is explicitly uncertain; never
        # invite a blind replacement or claim that nothing changed.
        if latest.get('state') == 'applying':
            state = 'unknown' if latest.get('api_receipt') else 'awaiting-input'
            orchestrator._settle(draft['id'], state, state, settled_at=orchestrator.now())
        from .settings_orchestrator import SettingsError
        message = str(exc) if isinstance(exc, (ApiError, SettingsError)) else '입력 또는 작업 상태를 확인하지 못했습니다. 연결 요청을 확인해 주세요.'
        raise ValueError(message) from None
    finally:
        orchestrator._in_flight.discard(draft['id'])
        session.submit_lock.release()
