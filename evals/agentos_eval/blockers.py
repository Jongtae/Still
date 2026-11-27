"""List recent self-review blockers as candidate revision targets (#1277, part of #1275).

A self-review keeps its blocker only as ``detail['blocker']`` on the reviewed
Work's ``self_review`` tool event, already redacted when stored.  This reads the
newest events of one named store with a single read-only query and prints that
text, the Work id and the date.  Nothing else of the Work is read out: no
request, answer or message.  Which blocker names a method in versioned text is
the developer's choice; no wording rule decides it here.  The cause kind is
the one the self-review stored (#1290); a row without one is ``unknown``.
"""
import json
import sqlite3
import sys
from collections import Counter
from contextlib import closing
from datetime import datetime
from pathlib import Path

from .paths import SRC

if str(SRC) not in sys.path:
    sys.path.append(str(SRC))
from personal_agent.owner_model import BLOCKER_KINDS, REVIEW_EVENT_TOOL, normalized  # noqa: E402

#: Newest tool events looked at: a rowid range, so the read never walks the whole table.
SCAN_ROWS = 20000
#: Listed for a row stored without a kind: older than #1290, or none the self-review could name.
UNKNOWN_KIND = 'unknown'
#: Seconds to wait for a writer's lock before giving up; the live store is never polled.
BUSY_SECONDS = 1.0

# #367: a Work the owner forgot any turn of is not listed; its blocker may describe what was forgotten.
QUERY = (
    'SELECT job_id,detail,created FROM tool_events '
    'WHERE id>(SELECT COALESCE(MAX(id),0) FROM tool_events)-? AND tool=? '
    'AND job_id NOT IN (SELECT m.job_id FROM forgotten_messages f JOIN messages m ON m.id=f.message_id '
    'WHERE m.job_id IS NOT NULL) '
    'ORDER BY id DESC'
)


def store_path(value):
    """The database file for ``value``: a Still data folder or the file itself."""
    path = Path(value).expanduser()
    if path.is_dir():
        path = path / 'private' / 'quickstart.db'
    if not path.is_file():
        raise ValueError(f'No store database at {path}')
    return path.resolve()


def connect(path):
    return sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=BUSY_SECONDS)


def read_blockers(value, scan=SCAN_ROWS):
    """``[{'work_id', 'created', 'kind', 'blocker'}]``, newest first, from one query."""
    with closing(connect(store_path(value))) as db:
        rows = db.execute(QUERY, (int(scan), REVIEW_EVENT_TOOL)).fetchall()
    found = []
    for work_id, detail, created in rows:
        try:
            data = json.loads(detail or '{}')
        except ValueError:
            continue
        blocker = data.get('blocker') if isinstance(data, dict) else None
        if isinstance(blocker, str) and blocker.strip():
            kind = data.get('blocker_kind')
            found.append({'work_id': work_id, 'created': created,
                          'kind': kind if kind in BLOCKER_KINDS else UNKNOWN_KIND, 'blocker': _printable(blocker)})
    return found


def _printable(text):
    # Stored text is model-written: one line, no terminal control characters.
    return ''.join(c for c in ' '.join(text.split()) if c.isprintable())


def group(rows):
    """Rows of one kind with identical wording after case and spacing (and a final period).

    Groups of the kind with the most rows come first, and within a kind the largest group.
    """
    groups = {}
    for row in rows:
        key = row['kind'], normalized(row['blocker']).rstrip('.。 ')
        groups.setdefault(key, []).append(row)
    order = {kind: position for position, (kind, _count) in enumerate(kind_counts(rows))}
    # Stable sort: equal counts keep the newest-first order of ``rows``.
    return sorted(groups.values(), key=lambda members: (order[members[0]['kind']], -len(members)))


def kind_counts(rows):
    """``[(kind, rows)]``, most rows first; ties keep the shared kinds' order, ``unknown`` last."""
    counts = Counter(row['kind'] for row in rows)
    return [(kind, counts[kind]) for kind in sorted((*BLOCKER_KINDS, UNKNOWN_KIND), key=lambda kind: -counts[kind])
            if counts[kind]]


def render(rows, scan=SCAN_ROWS):
    groups = group(rows)
    lines = [f'Self-review blockers: {len(rows)} in {len(groups)} groups (newest {scan} tool events)']
    if rows:
        lines.append('\nBy kind:')
        lines.extend(f'    {count}  {kind}' for kind, count in kind_counts(rows))
    for members in groups:
        lines.append(f"\n{len(members)}x [{members[0]['kind']}] {members[0]['blocker']}")
        for row in members:
            created = row['created']
            day = 'undated' if created is None else datetime.fromtimestamp(created).date().isoformat()
            lines.append(f"    {day}  {row['work_id']}")
    return '\n'.join(lines)
