"""CONNECT-TRUST-01 (#1297): the owner's trust record for AI-side hands.

Connection and Delegation Contract §6/§7.  Which operation of a connected
service may run is owner state, not a repository list:

* **Per hand** (an engine/kind/identity connection): the owner's decision per operation (``read``,
  ``mutate``, ``payment`` or ``refused``), whether the hand is
  money-capable, its rung on the trust ladder, and a short history of
  changes and corrections.
* **Raising is an owner decision.** Classes, money marking and a higher rung
  change only through a confirmed settings draft.  ``lower`` is the one
  automatic change (after a failure or an owner correction) and is recorded.
* **The seed list** (``connector_read_operations.json``) still answers for an
  operation the owner never decided, except on a money-capable hand, where
  only the owner's own ``read`` decision makes a call a read.
* **Names only.** No service, server or site is named here (C16); popularity,
  annotations and labels never move a rung (C9).
"""
from __future__ import annotations

import json
from contextlib import nullcontext
import re
import time
import uuid

KEY = 'trust_record'
CLASSES = ('read', 'mutate', 'payment', 'refused')
#: Contract §6: report → propose → act after per-action approval → act within a standing mandate.
RUNGS = ('report', 'propose', 'approve', 'mandate')
ENGINES = ('claude-code', 'codex')
KINDS = ('ai-connection', 'owner-mcp')
HAND = re.compile(r'^[^|\x00-\x1f]{1,64}$')
OPERATION = re.compile(r'^[A-Za-z0-9_.-]{1,60}$')
MAX_HANDS = 32
MAX_OPERATIONS = 64
MAX_HISTORY = 20


def valid_hand(name):
    return isinstance(name, str) and bool(HAND.match(name)) and name == name.strip()


def valid_operation(name):
    return isinstance(name, str) and bool(OPERATION.match(name))


def default_rung(money):
    """Contract §6 starting rung: report for a money-capable hand, act-after-approval otherwise."""
    return 'report' if money else 'approve'


def connection(engine, kind, identity, name=None):
    """Return the scoped identity used by the trust record.

    ``name`` is owner-facing text; ``identity`` is the stable namespace token
    from the actual tool name/configuration.  Both the engine and connection
    kind are part of the key so an approval cannot cross either boundary.
    """
    if engine not in ENGINES or kind not in KINDS or not valid_hand(identity):
        raise ValueError('연결 범위를 확인하세요.')
    display = name if name is not None else identity
    if not valid_hand(display):
        raise ValueError('연결 이름 형식이 아닙니다.')
    return {'engine': engine, 'kind': kind, 'identity': identity, 'name': display}


def _descriptor(value, engine=None, kind=None, identity=None):
    if isinstance(value, dict):
        engine, kind, identity = value.get('engine'), value.get('kind'), value.get('identity')
        name = value.get('name') or identity
        if engine is None and kind is None and valid_hand(identity) and valid_hand(name):
            return {'name': name, 'engine': None, 'kind': None, 'identity': identity}
        if engine not in ENGINES or kind not in KINDS or not valid_hand(identity) or not valid_hand(name):
            raise ValueError('연결 범위를 확인하세요.')
        return connection(engine, kind, identity, name)
    if engine is not None or kind is not None or identity is not None:
        return connection(engine, kind, identity, value)
    if not valid_hand(value):
        raise ValueError('연결 이름 형식이 아닙니다.')
    # Legacy, unscoped calls remain usable for local state migration/tests only.
    return {'name': value, 'engine': None, 'kind': None, 'identity': value}


def _key(descriptor):
    return (f"{descriptor['engine']}|{descriptor['kind']}|{descriptor['identity']}"
            if descriptor['engine'] else descriptor['identity'])


def _hands(store):
    rows = store.config(KEY, {}) or {}
    hands = rows.get('hands') if isinstance(rows, dict) else None
    return hands if isinstance(hands, dict) else {}


def hand(store, name, engine=None, kind=None, identity=None):
    """One hand's record, normalised; an unknown hand has the defaults and no decisions."""
    descriptor = _descriptor(name, engine, kind, identity)
    return _normalised_hand(descriptor, _hands(store).get(_key(descriptor)))


def _normalised_hand(descriptor, row):
    row = row if isinstance(row, dict) else {}
    if row.get('state') == 'forgotten':
        return {**descriptor, 'money': True, 'rung': 'report', 'operations': {}, 'history': [],
                'state': 'forgotten', 'revision': row.get('revision', ''), 'epoch': row.get('epoch', ''), 'seed_allowed': False}
    money = row.get('money') is True
    operations = row.get('operations') if isinstance(row.get('operations'), dict) else {}
    rung = row.get('rung') if row.get('rung') in RUNGS else default_rung(money)
    if money and rung == 'mandate':
        rung = 'approve'    # payment never reaches a standing mandate in the pilot
    return {'name': descriptor['name'], 'engine': descriptor['engine'], 'kind': descriptor['kind'],
            'identity': descriptor['identity'], 'money': money, 'rung': rung,
            'state': 'active', 'revision': row.get('revision', ''), 'seed_allowed': row.get('seed_allowed', True) is True,
            'epoch': row.get('epoch', ''),
            'operations': {op: cls for op, cls in operations.items() if valid_operation(op) and cls in CLASSES},
            'history': [item for item in (row.get('history') or []) if isinstance(item, dict)][-MAX_HISTORY:]}


def hands(store):
    """Every recorded hand, normalised."""
    result = {}
    for key in sorted(_hands(store)):
        row = _hands(store).get(key)
        if not isinstance(key, str) or not isinstance(row, dict):
            continue
        if all(isinstance(row.get(field), str) for field in ('engine', 'kind', 'identity')):
            try:
                descriptor = connection(row['engine'], row['kind'], row['identity'], row.get('name'))
            except ValueError:
                continue
            result[key] = hand(store, descriptor)
        elif valid_hand(key):
            result[key] = hand(store, key)
    return result


def read_revision_covers(record, expected_revision, operation):
    """Only new reads for *other* operations commute with a pending read approval.

    Follow the bounded authority history exactly. A missing link, correction,
    forget/restore, money/rung change or failure withholds stale approval.
    """
    cursor = record['revision']
    if cursor == expected_revision:
        return True
    for _ in range(MAX_HISTORY):
        change = next((row for row in reversed(record['history']) if row.get('id') == cursor), {})
        before = change.get('before') or {}
        if (change.get('change') != 'operation' or change.get('class') != 'read'
                or change.get('operation') == operation or change.get('owner_correction')
                or before.get('state') != 'active' or 'revision' not in before):
            return False
        cursor = before['revision']
        if cursor == expected_revision:
            return True
    return False


def _write(store, name, change, update, *, authority=True, automatic=False, expected_revision=None, commuting_read=None, transaction=None):
    descriptor = _descriptor(name)
    key = _key(descriptor)
    with (store.db() if transaction is None else nullcontext(transaction)) as db:
        if transaction is None:
            db.execute('BEGIN IMMEDIATE')
        found = db.execute('SELECT value FROM config WHERE key=?', (KEY,)).fetchone()
        rows = json.loads(found['value']) if found else {}
        rows = dict(rows) if isinstance(rows, dict) else {}
        all_hands = dict(rows.get('hands') or {}) if isinstance(rows.get('hands'), dict) else {}
        if key not in all_hands and len(all_hands) >= MAX_HANDS:
            raise ValueError(f'기록할 수 있는 연결은 {MAX_HANDS}개까지입니다.')
        current = _normalised_hand(descriptor, all_hands.get(key))
        if (expected_revision is not None and current['revision'] != expected_revision
                and not (commuting_read and read_revision_covers(current, expected_revision, commuting_read))):
            raise ValueError('초안을 만든 뒤 신뢰 기록이 바뀌어 적용하지 않았어요.')
        if current['state'] == 'forgotten' and automatic:
            return current
        record = {'money': current['money'], 'rung': current['rung'], 'operations': dict(current['operations']),
                  'history': current['history'], 'state': 'active', 'revision': current['revision'],
                  'epoch': current['epoch'],
                  'seed_allowed': current['seed_allowed']}
        if descriptor['engine']:
            record.update({field: descriptor[field] for field in ('engine', 'kind', 'identity', 'name')})
        before = {field: record[field] for field in ('money', 'rung', 'operations', 'seed_allowed')}
        before['operations'] = dict(before['operations'])
        before['state'] = current['state']
        before['revision'] = current['revision']
        update(record)
        if len(record['operations']) > MAX_OPERATIONS:
            raise ValueError(f'한 연결에 기록할 수 있는 동작은 {MAX_OPERATIONS}개까지입니다.')
        if authority:
            record['revision'] = uuid.uuid4().hex
        entry = {'at': time.time(), **change}
        if authority:
            entry.update(id=record['revision'], before=before)
        history = record['history'] + [entry]
        retained = history[-MAX_HISTORY:]
        # Successful observations are not authority changes. Keep the current
        # exact-undo snapshot while rotating observations within the same bound.
        anchor = next((item for item in reversed(history) if item.get('id') == record['revision']), None)
        if anchor is not None and anchor not in retained:
            retained = [anchor, *retained[-(MAX_HISTORY - 1):]]
        record['history'] = retained
        if record['state'] == 'forgotten':
            record['epoch'] = record['revision']
            record = {field: record[field] for field in ('engine', 'kind', 'identity', 'name', 'state', 'revision', 'epoch') if field in record}
        all_hands[key] = record
        rows['hands'] = all_hands
        db.execute('INSERT INTO config VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                   (KEY, json.dumps(rows)))
    return _normalised_hand(descriptor, record)


def introduce_connection(store, name, read_tools, money, *, expected_revision, transaction):
    """Apply exactly the reads/marking named by an owner-confirmed setup draft.

    Setup cannot correct a refusal or downgrade a prior classification/marking.
    Its caller atomically settles the draft and server admission in the same DB.
    """
    if not isinstance(money, bool) or not all(valid_operation(op) for op in read_tools):
        raise ValueError('확인한 연결 권한을 검토하세요.')
    def update(record):
        if record['revision'] and record['money'] != money:
            raise ValueError('기존 돈 표시를 연결 설정으로 바꿀 수 없어요.')
        if any(record['operations'].get(op) not in (None, 'read') for op in read_tools):
            raise ValueError('기존 동작 결정을 연결 설정으로 바꿀 수 없어요.')
        if not record['revision']:
            record['money'], record['rung'] = money, default_rung(money)
        record['operations'].update({op: 'read' for op in read_tools})
    current = hand(store, name)
    if current['state'] == 'forgotten':
        raise ValueError('잊은 연결은 별도의 복원 확인이 필요해요.')
    return _write(store, name, {'change': 'connection-introduction', 'reads': list(read_tools), 'money': money},
                  update, expected_revision=expected_revision, transaction=transaction)


def set_operation(store, name, operation, cls, *, expected_revision=None):
    """The owner's decision for one operation (through a confirmed draft)."""
    if not valid_operation(operation) or cls not in CLASSES:
        raise ValueError('동작 이름이나 분류를 확인하세요.')
    change = {'change': 'operation', 'operation': operation, 'class': cls}
    def update(record):
        previous = record['operations'].get(operation)
        if previous is not None and previous != cls:
            record['rung'] = RUNGS[max(0, RUNGS.index(record['rung']) - 1)]
            change.update(owner_correction=True, rung=record['rung'])
        record['operations'][operation] = cls
    return _write(store, name, change, update, expected_revision=expected_revision,
                  commuting_read=operation if cls == 'read' else None)


def set_money(store, name, money, *, expected_revision=None):
    """Mark a hand money-capable (or not); marking resets the rung to that marking's starting rung."""
    def update(record):
        record['money'] = bool(money)
        if money:
            record['rung'] = default_rung(True)
    return _write(store, name, {'change': 'money', 'money': bool(money)}, update, expected_revision=expected_revision)


def set_rung(store, name, rung, *, expected_revision=None):
    """The owner's chosen rung (through a confirmed draft)."""
    if rung not in RUNGS:
        raise ValueError('신뢰 단계를 확인하세요.')
    def update(record):
        if rung == 'mandate' and record['money']:
            raise ValueError('돈이 오가는 연결은 상시 위임 단계로 올릴 수 없습니다.')
        record['rung'] = rung
    return _write(store, name, {'change': 'rung', 'rung': rung}, update, expected_revision=expected_revision)


def lower(store, name, reason):
    """The one automatic change: one rung down after a failure or an owner correction (recorded)."""
    change = {'change': 'lowered', 'reason': str(reason)[:120]}
    def update(record):
        rung = RUNGS[max(0, RUNGS.index(record['rung']) - 1)]
        record['rung'] = rung
        change['rung'] = rung
    return _write(store, name, change, update, automatic=True)


def undo_snapshot(record, change_id):
    """Only the current authority revision is exactly undoable; older changes cannot overwrite it."""
    if record['state'] != 'active' or not change_id or record['revision'] != change_id:
        raise ValueError('그 뒤에 신뢰 기록이 바뀌었어요. 지금 기록을 다시 확인해 주세요.')
    entry = next((row for row in reversed(record['history']) if row.get('id') == change_id), {})
    if not isinstance(entry.get('before'), dict):
        raise ValueError('정확히 되돌릴 이전 기록이 없어요.')
    return entry['before']


def undo(store, name, change_id):
    """Restore one exact prior authority snapshot, only through owner-confirmed Settings."""
    def update(record):
        record.update(undo_snapshot(record, change_id))
    return _write(store, name, {'change': 'undo', 'undoes': change_id}, update)


def observe(store, name, operation, outcome, work_id):
    """Observed tool outcome only, without arguments/results. Success never grants authority."""
    if not valid_operation(operation) or outcome not in ('succeeded', 'failed'):
        raise ValueError('관찰한 동작과 결과를 확인하세요.')
    change = {'change': 'observed', 'operation': operation, 'outcome': outcome, 'work_id': str(work_id)[:64]}
    def update(record):
        if outcome == 'failed':
            record['rung'] = RUNGS[max(0, RUNGS.index(record['rung']) - 1)]
            change['rung'] = record['rung']
    return _write(store, name, change, update, authority=outcome == 'failed', automatic=True)


def is_read(store, name, operation, seed=frozenset()):
    """Whether a call to ``name``'s ``operation`` is a read AgentOS may run.

    The owner's own decision wins; without one, the seed list answers, except
    on a money-capable hand (contract §4: there only an owner-reviewed read is a read).
    """
    record = hand(store, name)
    if record['state'] == 'forgotten':
        return False
    decided = record['operations'].get(operation)
    if decided is not None:
        return decided == 'read'
    return record['seed_allowed'] and not record['money'] and operation in (seed or ())


def readable(store, name, seed=frozenset()):
    """Whether ``name`` has at least one operation that may run as a read."""
    record = hand(store, name)
    if record['state'] == 'forgotten':
        return False
    if any(cls == 'read' for cls in record['operations'].values()):
        return True
    return record['seed_allowed'] and not record['money'] and any(record['operations'].get(op) is None for op in (seed or ()))


def describe(record):
    """An owner-readable one-line summary (names and decisions only)."""
    if record.get('state') == 'forgotten':
        return f"{record['name']}: 잊음 · 이전 권한 사용 중지"
    labels = {'read': '읽기', 'mutate': '변경', 'payment': '결제', 'refused': '막음'}
    rungs = {'report': '보고만', 'propose': '제안까지', 'approve': '건별 승인 후 실행', 'mandate': '위임 범위 안에서 실행'}
    operations = ', '.join(f'{op}({labels[cls]})' for op, cls in sorted(record['operations'].items())) or '검토한 동작 없음'
    scope = f"{record['engine']} {record['kind']} " if record.get('engine') else ''
    return f"{scope}{record['name']}: {rungs[record['rung']]}{' · 돈이 오가는 연결' if record['money'] else ''} · {operations}"
