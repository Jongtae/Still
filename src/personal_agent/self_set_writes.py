"""#717: owner-owned non-payment authority for self-set preparations.

Uses the existing Settings drafts, exact step tokens and SQLite Work events.
No provider/tool name decides reversibility. A permission receipt is not proof
of success: the transport observer writes the after-notice separately.
"""
import hashlib
import json
import time

from .agent_runtime import ToolError, not_run
from .messages import msg

KEY = 'write_allowances'
ACTION = 'self_set_write'


def connections(service):
    from . import api_requests, trust_record, owner_mcp
    rows = {}
    for key, hand in trust_record.hands(service.store).items():
        if hand['state'] == 'active' and hand.get('engine'):
            rows['connector:' + key] = {'label': hand['name'], 'connection_revision': json.dumps([hand['revision'],hand['epoch'],
                owner_mcp.server_revision(service.store,hand['engine'],hand['identity']) if hand['kind']=='owner-mcp' else ''])}
    for engine in owner_mcp.ENGINES:
        for name in owner_mcp.confirmed(service.store, engine):
            key = f'connector:{engine}|owner-mcp|{name}'
            rows.setdefault(key, {'label': name, 'connection_revision': json.dumps(['','',owner_mcp.server_revision(service.store,engine,name)])})
    for name, slot in {**service.connection_slots({}), **api_requests.slots(service.store)}.items():
        rows['slot:' + name] = {'label': name, 'connection_revision': str(slot.get('revision', ''))}
    for key, peer in service.a2a_peers.rows().items():
        if not peer.get('removed') and not peer.get('blocked'):
            rows['peer:' + key] = {'label': peer.get('card', {}).get('name', key),
                                  'connection_revision': peer['revision']}
    return rows


def allowance(service, connection, *, db=None):
    row = service.store.config(KEY, {}, db=db).get(connection, {})
    return {'enabled': row.get('enabled') is True, 'revision': row.get('revision', 0),
            'connection_revision': row.get('connection_revision'),
            'authorized_by': row.get('authorized_by'), 'authorized_at': row.get('authorized_at')}


def settings(service):
    rows=[]
    for key,row in connections(service).items():
        mode=allowance(service,key)
        rows.append({'connection':key,**mode,**row,
                     'enabled':mode['enabled'] and mode['connection_revision']==row['connection_revision']})
    return {'label': msg('write_allowances.label'), 'note': msg('write_allowances.risk'),
            'enable': msg('write_allowances.enable'), 'disable': msg('write_allowances.disable'),
            'confirm': msg('write_allowances.confirm'),
            'connections': rows}


def set_allowance(service, connection, enabled, revision, owner, *, connection_revision=None):
    if not isinstance(connection,str) or type(enabled) is not bool or type(revision) is not int:
        raise ValueError(msg('write_allowances.changed'))
    current = connections(service).get(connection)
    if current is None or connection_revision is not None and current['connection_revision'] != connection_revision:
        raise ValueError(msg('write_allowances.changed'))
    with service.store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        rows = service.store.config(KEY, {}, db=db)
        old = allowance(service, connection, db=db)
        if old['revision'] != revision:
            raise ValueError(msg('write_allowances.changed'))
        record = {'enabled': enabled, 'revision': revision + 1, **current,
                  'authorized_by': owner, 'authorized_at': time.time()}
        rows[connection] = record
        service.store.put(KEY, rows, db=db)
        history = service.store.config(KEY + '_history', [], db=db)
        service.store.put(KEY + '_history', history + [{'connection': connection, **record}], db=db)


def authorize(service, job, binding, connection, operation, target, names=(), *, effect=None, description=None):
    if not service.unattended_work(job) or not service.self_set_run(job):
        return None
    if effect is None:
        try:
            metadata=operation
            if connection is None:
                from urllib.parse import urlsplit
                page=urlsplit(target)
                metadata += ' @ ' + page.scheme + '://' + page.netloc
            effect = service.decision_judge.connector_effect(metadata, sorted(names), '', job['id'])
        except Exception:
            effect = None
    if effect == 'payment':
        raise not_run(ToolError(msg('write_allowances.payment'), 'unattended_run'))
    # A known state change cannot be downgraded to read by a classifier.
    reversible = effect == 'reversible'
    scoped = {**binding, 'action': ACTION, 'source_action': binding['action'],
              'state_digest': hashlib.sha256(json.dumps([binding['action'],binding['state_digest']]).encode()).hexdigest()}
    digest = service._browser_step_keys(scoped)['digest']
    label = service._redact_known_secrets(f'{connection or "browser"} / {operation} / {target}' +
                                        ('\n' + description if description else ''))
    scopes = list(connection) if isinstance(connection, (list, tuple)) else [connection] if connection else []
    known = connections(service) if scopes else {}
    modes = {key: allowance(service,key) for key in scopes}
    allowed = bool(scopes) and all(key in known and modes[key]['enabled'] and
        modes[key]['connection_revision'] == known[key]['connection_revision'] for key in scopes)
    with service.store.db() as db:
        if any(json.loads(row['detail']).get('digest') == digest for row in db.execute(
                "SELECT detail FROM tool_events WHERE job_id=? AND tool=?", (job['id'], ACTION))):
            raise not_run(ToolError(msg('write_allowances.replayed'), 'effect_replayed'))
    if not reversible and not allowed and not service._consume_browser_step(job, scoped):
        service._request_browser_step(job, scoped, label)
        raise not_run(ToolError(msg('write_allowances.wait'), 'approval_required'))
    with service.store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        if allowed and any(allowance(service,key,db=db) != mode for key,mode in modes.items()):
            raise not_run(ToolError(msg('write_allowances.changed'), 'allowance_changed'))
        if service.work_stopped(job['id']):
            raise not_run(ToolError(msg('write_allowances.replayed'), 'work_stopped'))
        if any(json.loads(row['detail']).get('digest') == digest for row in db.execute(
                "SELECT detail FROM tool_events WHERE job_id=? AND tool=?", (job['id'], ACTION))):
            raise not_run(ToolError(msg('write_allowances.replayed'), 'effect_replayed'))
        ticket = {'digest': digest, 'connection': connection, 'operation': operation,
                  'target': service._redact_known_secrets(str(target)), 'allowance': allowed,
                  'revision': next(iter(modes.values()))['revision'] if allowed and len(modes)==1 else None,
                  'allowances': {key: value['revision'] for key,value in modes.items()} if allowed else {},
                  'authorized_by': 'connection-allowance' if allowed else 'reversible' if reversible else 'owner-step',
                  'authorized_at': time.time()}
        ticket['event'] = db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
            (job['id'], ACTION, 'dispatching', json.dumps(ticket), time.time())).lastrowid
    return ticket


def finished(service, job, ticket, *, observed=True):
    if not ticket:
        return
    text = msg('write_allowances.notice' if observed else 'write_allowances.unknown_notice', connection=ticket['connection'], operation=ticket['operation'], target=ticket['target'])
    with service.store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        changed = db.execute("UPDATE tool_events SET status=? WHERE id=? AND job_id=? AND tool=? AND status='dispatching'",
                             ('observed' if observed else 'unknown', ticket['event'], job['id'], ACTION)).rowcount
        if changed and ticket['allowance']:
            service.record_conversation_notice(text, job=job, db=db)
    if changed and ticket['allowance']:
        surface = service.surface_claiming(job)
        if surface is not None:
            try:
                surface.deliver_conversation_notice(text)
            except Exception:
                pass  # The durable conversation notice remains.
