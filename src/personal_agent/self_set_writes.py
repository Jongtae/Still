"""#717/#744: owner-owned non-payment authority for self-set and marked Works.

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
        revision = str(slot.get('revision', ''))
        # Separate write grants carry authority too; preserve legacy identities when absent.
        if slot.get('write_grant_revisions'):
            revision = json.dumps([revision, slot['write_grant_revisions']], sort_keys=True)
        rows['slot:' + name] = {'label': name, 'connection_revision': revision}
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
                     'enabled':mode['enabled'] and mode['connection_revision']==row['connection_revision'],
                     'reason':msg('write_allowances.invalidated') if mode['enabled'] and mode['connection_revision']!=row['connection_revision'] else ''})
    return {'label': msg('write_allowances.label'), 'note': msg('write_allowances.risk'),
            'enable': msg('write_allowances.enable'), 'disable': msg('write_allowances.disable'),
            'confirm': msg('write_allowances.confirm'),
            'connections': rows}


def set_allowance(service, connection, enabled, revision, owner, *, connection_revision=None):
    if not isinstance(connection,str) or type(enabled) is not bool or type(revision) is not int:
        raise ValueError(msg('write_allowances.changed'))
    current = connections(service).get(connection)
    if enabled and (current is None or connection_revision is not None and current['connection_revision'] != connection_revision):
        raise ValueError(msg('write_allowances.changed'))
    with service.store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        rows = service.store.config(KEY, {}, db=db)
        old = allowance(service, connection, db=db)
        if enabled and old['revision'] != revision:
            raise ValueError(msg('write_allowances.changed'))
        record = {'enabled': enabled, 'revision': old['revision'] + 1, **(current or {'connection_revision': old['connection_revision']}),
                  'authorized_by': owner, 'authorized_at': time.time()}
        rows[connection] = record
        service.store.put(KEY, rows, db=db)
        history = service.store.config(KEY + '_history', [], db=db)
        service.store.put(KEY + '_history', history + [{'connection': connection, **record}], db=db)


def scoped_binding(binding):
    return {**binding, 'action': ACTION, 'source_action': binding['action'],
            'state_digest': hashlib.sha256(json.dumps([binding['action'],binding['state_digest']]).encode()).hexdigest()}


def pending_submit(service, job, binding, *, issued=False):
    row = service._browser_request(job['id']) or {}
    return (row.get('action') == ACTION and row.get('source_action') == 'browser_submit'
            and row.get('state') in (('issued',) if issued else ('requested', 'issued'))
            and row.get('digest') == service._browser_step_keys(scoped_binding(binding))['digest']
            and time.time() < row.get('requested_at', 0) + 600
            and not service.work_stopped(job['id']))


def recheck(service, job, ticket, *, db=None):
    """Final dispatch check, like the payment ticket recheck; never creates authority."""
    if not ticket:
        return
    if db is None:
        try:
            with service.store.db() as db:
                return recheck(service, job, ticket, db=db)
        except ToolError:
            release(service, job, ticket)
            raise
    row = db.execute('SELECT status,detail FROM tool_events WHERE id=? AND job_id=? AND tool=?',
                     (ticket['event'], job['id'], ACTION)).fetchone()
    current = bool(row and row['status'] == 'dispatching' and json.loads(row['detail']).get('digest') == ticket['digest'])
    if ticket['allowance']:
        known = connections(service)
        for key, revision in ticket['allowances'].items():
            mode = allowance(service, key, db=db)
            current = current and mode['enabled'] and mode['revision'] == revision and key in known and (
                mode['connection_revision'] == known[key]['connection_revision'] == ticket.get('connection_revisions', {}).get(key))
    if not current or service.work_stopped(job['id']):
        raise not_run(ToolError(msg('write_allowances.changed'), 'allowance_changed'))


def _effect_replayed(db, work_id, digest):
    # Only a pre-dispatch release or definitive refusal proves no effect happened.
    # All other outcomes (including held, unknown and dispatching) block replay.
    return any(json.loads(row['detail']).get('digest') == digest for row in db.execute(
        "SELECT detail FROM tool_events WHERE job_id=? AND tool=? AND status NOT IN ('not_run','rejected')",
        (work_id, ACTION)))


#: #932: URLs listed per source group in an approval label; the rest are counted.
SOURCE_URLS_SHOWN = 3


def marked_lines(sources, values, label):
    """The readable lines a marked Work's approval label adds (#932, #744).

    Every source group (all its fields but the URL) is named with its count, so the
    owner sees which tool or connection supplied outside content; a few URLs per group
    are listed and the rest counted (the Work's information-use record keeps them all).
    Parameters already shown verbatim in ``label`` are not repeated.
    """
    groups = {}
    for source in sources:
        source = source if isinstance(source, dict) else {'source': source}
        key = ' / '.join(str(source[name]) for name in sorted(source) if name != 'url')
        groups.setdefault(key, []).append(source.get('url'))
    lines = [msg('write_allowances.sources', count=len(sources))]
    for key, urls in groups.items():
        lines.append(f'- {key}' + (f' ({len(urls)})' if len(urls) > 1 else ''))
        shown = list(dict.fromkeys(url for url in urls if url))
        lines += [f'  {url}' for url in shown[:SOURCE_URLS_SHOWN]]
        if len(shown) > SOURCE_URLS_SHOWN:
            lines.append('  ' + msg('write_allowances.sources_more', count=len(shown) - SOURCE_URLS_SHOWN))
    lines += [line for line in value_lines(values).splitlines() if line.partition(': ')[2] not in label]
    return '\n'.join(lines)


def value_lines(values):
    """``values`` as ``name: value`` lines an owner can read (#945).

    A one-line string is shown as is. Any other value or name stays exact JSON, including an
    empty string or one with a line break, control character or edge space, so no value can
    pass as another line and an empty value is still shown.
    """
    lines = []
    for name in sorted(values or {}):
        value = values[name]
        lines.append(f'{_plain_or_json(name)}: {_plain_or_json(value)}')
    return '\n'.join(lines)


def _plain_or_json(value):
    """A non-empty one-line string without edge spaces as is; anything else as exact JSON."""
    if isinstance(value, str) and value and value == value.strip() and value.isprintable():
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def authorize(service, job, binding, connection, operation, target, names=(), *, effect=None, description=None, request=True, values=None,
              note='', require_note=False, link=False):
    """``link`` (#971): the browser session's own snapshot found the press target a plain
    link (``browser_session.link_address``) under no payment/commit/submit floor, and the
    step declares reading or navigating.  Where presses are narrowed the answer is then
    ``FOLLOW_LINK``: the session opens that address like ``browser_open``, unjudged."""
    barrier = service.__dict__.get('_native_intake_barriers', {}).get(job['id'])
    awaiting = barrier is not None and barrier.pending()
    browser_press = connection is None and operation in ('browser_click', 'browser_type')
    if awaiting and not browser_press:
        barrier.wait()
    sources = service.store.outside_content_sources(job['id'])
    marked = bool(sources)
    if not marked and not awaiting and (not service.unattended_work(job) or not service.self_set_run(job)):
        return None
    from .browser_session import action_input_safe
    scopes = list(connection) if isinstance(connection, (list, tuple)) else [connection] if connection else []
    known = connections(service) if scopes else {}
    connection_labels = [known.get(key, {}).get('label', key) for key in scopes]
    # Check the original values, not their display rendering: JSON/URL escapes
    # can hide a stored secret from literal redaction. Nothing unsafe may reach
    # a judgment, approval card, ticket or the worker's continuation prompt.
    if not action_input_safe(service.store, {'connection': scopes, 'operation': operation, 'target': target,
            'names': list(names), 'description': description, 'values': values, 'connection_labels': connection_labels}):
        raise not_run(ToolError(msg('write_allowances.unrepresentable'), 'api_approval_unrepresentable'))
    # Browsing to read keeps its zero-question path. Use the existing minimized
    # judgment, never the owner's request/body, to classify a non-floor press.
    # Known non-browser writes need no additional judgment in owner Works.
    if (marked or awaiting) and browser_press or effect is None and service.self_set_run(job):
        if link is True and connection is None and operation == 'browser_click':
            # #971: a GET navigation, opened like browser_open; no judgment and no ticket.
            from .browser_session import FOLLOW_LINK
            return FOLLOW_LINK
        try:
            metadata=operation
            if connection is None:
                from urllib.parse import urlsplit
                page=urlsplit(target)
                metadata += ' @ ' + page.scheme + '://' + page.netloc
            # #1063: a press is judged as a press, with its target's role, name and form fields in order.
            effect = service.decision_judge.connector_effect(metadata, sorted(names), '', job['id'],
                                                             **({'target': tuple(names)} if browser_press else {}))
        except Exception:
            effect = None
    if browser_press and effect in ('read', 'navigate'):
        return None
    if awaiting:
        barrier.wait()
        sources = service.store.outside_content_sources(job['id'])
        marked = bool(sources)
        if not marked and (not service.unattended_work(job) or not service.self_set_run(job)):
            return None
    if effect == 'payment' and (service.unattended_work(job) or not (marked and browser_press)):
        raise not_run(ToolError(msg('write_allowances.payment'), 'unattended_run'))
    # A known state change cannot be downgraded to read by a classifier.
    # #1065 (owner decision 2026-11-21): in a Work the owner's own typed message started, the
    # owner's request covers a reversible press even after outside content was read; that
    # content only informs which one. Irreversible and payment presses still ask.
    reversible = effect == 'reversible' and (not marked or browser_press and service.owner_typed_work(job))
    scoped = scoped_binding(binding)
    digest = service._browser_step_keys(scoped)['digest']
    label = service._redact_known_secrets(f'{connection or "browser"} / {operation} / {target}' +
                                        ('\n' + description if description else ''))
    if note:
        # #953: the AI's plain words first; the exact request below them is what is approved.
        from .code_run import APPROVAL_EXACT_LABEL
        label = f'{service._redact_known_secrets(note)}\n\n{APPROVAL_EXACT_LABEL} {label}'
    if marked:
        label += '\n' + service._redact_known_secrets(marked_lines(sources, values, label))
    modes = {key: allowance(service,key) for key in scopes}
    allowed = bool(scopes) and all(key in known and modes[key]['enabled'] and
        modes[key]['connection_revision'] == known[key]['connection_revision'] for key in scopes)
    with service.store.db() as db:
        if _effect_replayed(db, job['id'], digest):
            raise not_run(ToolError(msg('write_allowances.replayed'), 'effect_replayed'))
    approval = service._browser_request(job['id']) if not reversible and not allowed else None
    if not reversible and not allowed and not service._consume_browser_step(job, scoped):
        asks = not service.unattended_work(job) or service.self_set_write_wait(job)
        if require_note and not note and asks:
            # #953 (owner choice 2026-11-17): no approval request without the AI's plain words.
            from .code_run import APPROVAL_NOTE_REQUIRED
            raise not_run(ToolError(APPROVAL_NOTE_REQUIRED, 'approval_note_required'))
        if request:service._request_browser_step(job, scoped, label)
        raise not_run(ToolError(msg('write_allowances.wait'), 'approval_required'))
    with service.store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        if allowed and any(allowance(service,key,db=db) != mode for key,mode in modes.items()):
            raise not_run(ToolError(msg('write_allowances.changed'), 'allowance_changed'))
        if service.work_stopped(job['id']):
            raise not_run(ToolError(msg('write_allowances.replayed'), 'work_stopped'))
        if _effect_replayed(db, job['id'], digest):
            raise not_run(ToolError(msg('write_allowances.replayed'), 'effect_replayed'))
        ticket = {'digest': digest, 'connection': connection, 'operation': operation,
                  'connection_label': service._redact_known_secrets(', '.join(connection_labels)),
                  'target': service._redact_known_secrets(str(target)), 'allowance': allowed,
                  'revision': next(iter(modes.values()))['revision'] if allowed and len(modes)==1 else None,
                  'allowances': {key: value['revision'] for key,value in modes.items()} if allowed else {},
                  'connection_revisions': {key: value['connection_revision'] for key,value in modes.items()} if allowed else {},
                  'authorized_by': 'connection-allowance' if allowed else 'reversible' if reversible else 'owner-step',
                  'authorized_at': time.time()}
        ticket['event'] = db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
            (job['id'], ACTION, 'dispatching', json.dumps(ticket), time.time())).lastrowid
    # The token is host-local and never goes into an event/connector receipt.
    if approval:
        service.__dict__.setdefault('_self_set_unspent', {})[ticket['event']] = approval
    return ticket


def release(service, job, ticket):
    if not ticket:
        return
    approval = service.__dict__.get('_self_set_unspent', {}).pop(ticket['event'], None)
    with service.store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        changed = db.execute("UPDATE tool_events SET status='not_run' WHERE id=? AND job_id=? AND tool=? AND status='dispatching'",
                             (ticket['event'], job['id'], ACTION)).rowcount
        if changed and approval:
            from .quickstart_service import BROWSER_REQUESTS_KEY
            rows = service.store.config(BROWSER_REQUESTS_KEY, {}, db=db)
            if job['id'] not in rows:
                token_hash = service.store._exact_memory_token_lookup(approval.get('token'))
                restored = db.execute("UPDATE memory_approvals SET state='issued',result_id=NULL WHERE token_hash=? AND state='consumed' AND expires>?",
                                      (token_hash, time.time())).rowcount
                if restored:
                    rows[job['id']] = approval
                    service.store.put(BROWSER_REQUESTS_KEY, rows, db=db)


def finished(service, job, ticket, *, observed=True, rejected=False, held=False):
    if not ticket:
        return
    service.__dict__.get('_self_set_unspent', {}).pop(ticket['event'], None)
    # Use the checked snapshot; a connection's current display name may have
    # changed since authorization and has not passed the decoded-value guard.
    label = service._redact_known_secrets(ticket['connection_label'])
    text = msg('write_allowances.rejected_notice' if rejected else 'write_allowances.notice' if observed else 'write_allowances.unknown_notice', connection=label, operation=ticket['operation'], target=ticket['target'])
    notice_job = service.preparations_service.notice_job(job)
    with service.store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        changed = db.execute("UPDATE tool_events SET status=? WHERE id=? AND job_id=? AND tool=? AND status='dispatching'",
                             ('held' if held else 'rejected' if rejected else 'observed' if observed else 'unknown', ticket['event'], job['id'], ACTION)).rowcount
        if changed and ticket['allowance']:
            service.record_conversation_notice(text, job=notice_job, db=db)
    if changed and ticket['allowance']:
        service._deliver_authority_notice(service.surface_claiming(notice_job), text)
