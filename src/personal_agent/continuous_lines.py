"""Daily line standing, the planner mailbox and the executor's plan fence (#1218)."""
from datetime import datetime
import json
import re
import uuid
from urllib.parse import unquote

from . import preparations as prep
from .entrusted_lines import LineRefusal

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS line_mandates(
    line_id TEXT PRIMARY KEY, owner_work TEXT NOT NULL, ends_at REAL NOT NULL,
    revision INTEGER NOT NULL, planner_id TEXT NOT NULL, closed INTEGER NOT NULL DEFAULT 0,
    pending TEXT, wake_day TEXT, wakes INTEGER NOT NULL DEFAULT 0, active_plan TEXT
);
CREATE TABLE IF NOT EXISTS line_plan_revisions(
    id TEXT PRIMARY KEY, line_id TEXT NOT NULL, revision INTEGER NOT NULL, work_id TEXT NOT NULL,
    check_id TEXT NOT NULL, digest TEXT NOT NULL, expires REAL NOT NULL, sealed TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS line_work_memberships(
    work_id TEXT NOT NULL, line_id TEXT NOT NULL, revision INTEGER NOT NULL,
    PRIMARY KEY(work_id, line_id)
);
CREATE INDEX IF NOT EXISTS line_memberships_line ON line_work_memberships(line_id);
"""

STOP_REASON = 'line-day-ended'


def record(db, line_id):
    """Read mandate history for bookkeeping such as revisions, stop_line and effect scope.

    Never use this record to decide authority, handoff, approval, plan mode or scheduling.
    """
    return db.execute('SELECT * FROM line_mandates WHERE line_id=?', (line_id,)).fetchone() if line_id else None


def active(db, line_id, now):
    row = record(db, line_id)
    if row and not row['closed'] and now < row['ends_at'] and not prep.line_refusal(db, line_id):
        return row
    return None


def lapsed(db, line_id, now):
    """The deadline passed, but the preparations sweep has yet to close the mandate."""
    row = record(db, line_id)
    return bool(row and not row['closed'] and now >= row['ends_at'])


def record_membership(db, work_id, line_id, revision, *, refresh=False):
    db.execute('INSERT INTO line_work_memberships(work_id,line_id,revision) VALUES (?,?,?) '
               'ON CONFLICT(work_id,line_id) DO ' +
               ('UPDATE SET revision=excluded.revision' if refresh else 'NOTHING'), (work_id, line_id, revision))


def member_revision(db, work_id, line_id):
    recorded = db.execute('SELECT revision FROM line_work_memberships WHERE work_id=? AND line_id=?',
                          (work_id, line_id)).fetchone()
    return recorded['revision'] if recorded and recorded['revision'] > 0 else None


def effect_lines(db, work_id, preparation_id=None):
    lines = {row['line_id'] for row in db.execute(
        'SELECT line_id FROM line_work_memberships WHERE work_id=?', (work_id,))}
    if preparation_id:
        row = db.execute('SELECT line_id FROM preparations WHERE id=?', (preparation_id,)).fetchone()
        # Effect deduplication spans mandate history, including closed mandates.
        if row and record(db, row['line_id']):
            lines.add(row['line_id'])
    return lines


def parse_end(value):
    if not isinstance(value, str) or len(value)>40:
        raise ValueError
    date = datetime.fromisoformat(value)
    if date.tzinfo is None:
        raise ValueError
    return date.timestamp()


def trigger(db, line_id, event):
    """Persist one pending run, with the latest report of each kind."""
    row = active(db, line_id, event['at'])
    if not row:
        return
    pending = json.loads(row['pending']) if row['pending'] else {'id': uuid.uuid4().hex, 'events': {}}
    pending['events'][event['kind']] = event
    db.execute('UPDATE line_mandates SET pending=? WHERE line_id=?', (json.dumps(pending), line_id))


def fence(db, line_id, now, *, close=False, work_id=None, source_work_id=None):
    row = record(db, line_id) if close else active(db, line_id, now)
    if not row or row['closed']:
        return
    db.execute('UPDATE line_mandates SET revision=revision+1,active_plan=NULL,closed=?,pending=? WHERE line_id=?',
               (int(close), None if close else row['pending'], line_id))
    if not close:
        event = {'kind': 'owner_correction', 'at': now, 'check': '-', 'output': ''}
        if source_work_id:
            event['source_work_id'] = source_work_id
        trigger(db, line_id, event)
        if work_id:
            record_membership(db, work_id, line_id, row['revision'] + 1, refresh=True)


def entrust(db, line_id, work_id, planner_id, now, ends_at):
    previous = record(db, line_id)
    revision = previous['revision'] + 1 if previous else 1
    db.execute('INSERT INTO line_mandates(line_id,owner_work,ends_at,revision,planner_id) VALUES (?,?,?,?,?) '
               'ON CONFLICT(line_id) DO UPDATE SET owner_work=excluded.owner_work,ends_at=excluded.ends_at,'
               'revision=excluded.revision,planner_id=excluded.planner_id,closed=0,active_plan=NULL,pending=NULL',
               (line_id, work_id, ends_at, revision, planner_id))
    record_membership(db, work_id, line_id, revision, refresh=True)
    trigger(db, line_id, {'kind': 'entrusted', 'at': now, 'check': '-', 'output': ''})
    return revision


def has_other_executor(db, line_id, excluded=None):
    return db.execute("SELECT 1 FROM preparations WHERE line_id=? AND id IS NOT ? AND check_spec IS NOT NULL "
                      "AND state IN ('scheduled','running','paused') LIMIT 1", (line_id, excluded)).fetchone()


def plan_target(db, line_id):
    return db.execute('SELECT r.* FROM line_mandates m JOIN line_plan_revisions r ON r.id=m.active_plan '
                      'WHERE m.line_id=?', (line_id,)).fetchone()


def plan_matches(plan, row, digest):
    return (plan['check_id'] == row['id'] and plan['digest'] == digest
            and plan.get('check_revision') == row['updated_at'])


def clear_plan(db, line_id, plan_id):
    return db.execute('UPDATE line_mandates SET active_plan=NULL WHERE line_id=? AND active_plan=?',
                      (line_id, plan_id)).rowcount == 1


def executor_paused_or_resumed(db, row, now, *, resumed=False):
    """Drop the affected plan in the caller's transaction and wake on resume."""
    line_id = row['line_id']
    if not row['check_spec'] or not active(db, line_id, now):
        return
    target = plan_target(db, line_id)
    if target and target['check_id'] == row['id']:
        clear_plan(db, line_id, target['id'])
    if resumed:
        trigger(db, line_id, {'kind': 'executor_resumed', 'at': now,
                             'check': json.loads(row['check_spec'])['digest'], 'output': '',
                             'executor_id': row['id']})


def publish(db, *, line_id, revision, work_id, check_id, digest, expires, sealed, now):
    plan_id = uuid.uuid4().hex
    changed = db.execute('UPDATE line_mandates SET active_plan=? WHERE line_id=? AND revision=? '
                         'AND closed=0 AND ends_at>? AND ends_at>=?',
                         (plan_id, line_id, revision, now, expires)).rowcount
    if not changed:
        raise LineRefusal('line_plan_stale')
    db.execute('INSERT INTO line_plan_revisions VALUES (?,?,?,?,?,?,?,?)',
               (plan_id, line_id, revision, work_id, check_id, digest, expires, sealed))
    return plan_id


def path_key(path):
    if not isinstance(path, str) or not path.startswith('/') or len(path) > 1024:
        raise ValueError
    clean = unquote(path, errors='strict')
    if (any(char in clean for char in '%?#;\\') or '//' in clean
            or any(part in ('.', '..') for part in clean.split('/'))
            or any(ord(char) < 32 or ord(char) == 127 for char in clean)
            or clean.count('/') != path.count('/')):
        raise ValueError
    return clean


def plan_calls(calls):
    if not isinstance(calls, list) or len(calls) > 32:
        raise ValueError
    for call in calls:
        required = {'connection', 'method', 'host', 'path', 'action_id'}
        if not isinstance(call, dict) or not required <= set(call) <= required | {'idempotency_header'}:
            raise ValueError
        if any(not isinstance(value, str) or not 0 < len(value) <= 240 for value in call.values()):
            raise ValueError
        path = call['path'].removesuffix('*') if call['path'].endswith('/*') else call['path']
        if ('*' in path or path_key(path) != path or not re.fullmatch(r'[A-Z]{3,12}', call['method'])
                or call['method'] in ('GET', 'HEAD') or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,96}', call['action_id'])
                or ('idempotency_header' in call and not re.fullmatch(r'[A-Za-z0-9-]{1,64}', call['idempotency_header']))):
            raise ValueError
    return calls


def matching_action(calls, request):
    connection, method, host, path, headers = request
    try:
        path = path_key(path)
    except ValueError:
        return None
    matches = {call['action_id'] for call in calls
               if (connection, method, host) == (call['connection'], call['method'], call['host'])
               and (path.startswith(call['path'][:-1]) if call['path'].endswith('/*') else path == call['path'])
               and ('idempotency_header' not in call or headers.get(call['idempotency_header'].lower()) == call['action_id'])}
    return next(iter(matches)) if len(matches) == 1 else None
