"""PROC-MEM-01 (#1482, #1486): procedural memory as code, a verified function library per code-run secret set.

What worked once on a connection runs fast the next time.  The owner's AI saves
the functions it used in a ``code_run`` that served the request; the next request
that names the same secret set finds them listed in its context and importable in
its run (``code_run``), so the AI calls them instead of re-reading the vendor's
documentation.  This module stores, gates and lists them and never reads what they
are for.  Core code knows four facts only: a connection has a function library,
functions take arguments and build requests, state changes are approved per request
(``code_run``), and a save is outcome-gated: a Work's candidate becomes the set's
next version only when that Work reached its outcome (``settle``).

A function is a method, never a permission.  Every state-changing request it makes
still needs the owner's approval of exactly that request.

States: ``candidate`` (one Work's proposal, used only inside that Work),
``current`` (at most one per set), ``kept`` (earlier versions, the rollback targets)
and ``forgotten`` (removed by the owner through ``forget_record``; ``owner_forget``
owns the undo window and the purge).

STEADY-NOTES-01 (#19): one reserved owner-level slot, ``NOTES_SLOT``, keeps the
versions of the owner's working-notes document (``owner_model.WorkingNotes``) in
the same store.  It has no candidate stage: a revision the host accepted is
saved at once (``commit``), the previous version stays ``kept``, and forgetting
the current version through ``owner_forget`` puts the previous one back.  No
secret set can name it (set names never hold ``:``), so no ``code_run`` imports it.
"""
import hashlib
import json
import re
import time
import uuid

DDL = '''
CREATE TABLE IF NOT EXISTS procedure_versions(
  id TEXT PRIMARY KEY, slot TEXT NOT NULL, version INTEGER, state TEXT NOT NULL,
  source TEXT NOT NULL, digest TEXT NOT NULL, functions_json TEXT NOT NULL DEFAULT '[]',
  stats_json TEXT NOT NULL DEFAULT '{}', work_id TEXT, created REAL NOT NULL, saved REAL);
CREATE INDEX IF NOT EXISTS procedure_versions_slot ON procedure_versions(slot, state);
'''
CANDIDATE, CURRENT, KEPT = 'candidate', 'current', 'kept'
#: The bound of one library module, as the AI passes it to ``code_run``.
MAX_SOURCE_BYTES = 32 * 1024
#: Public functions one library may hold, and how many the context line names.
MAX_FUNCTIONS = 24
LISTED_FUNCTIONS = 12
#: The context line's bound per secret set.
LISTING_CHARS = 900
#: A candidate whose Work never reached its outcome is dropped after this long.
CANDIDATE_SECONDS = 7 * 86400
REF_PREFIX = 'procedure:'
#: #19: the reserved owner-level slot of the working-notes document (``api_requests.NAME`` never matches it).
NOTES_SLOT = 'owner:working-notes'
IDENTIFIER = re.compile(r'^[A-Za-z][A-Za-z0-9_]{0,63}$')


#: Runs in a row in which a function only failed before the listing flags it (the remote side may have changed).
RECENT_FAILURES = 2


def add_counts(previous, done, failed):
    """``[succeeded, failed]`` plus, while it lasts, the runs in a row in which the function only failed."""
    ok, bad, streak = (list(previous or []) + [0, 0, 0])[:3]
    ok, bad = ok + min(done, 10_000), bad + min(failed, 10_000)
    streak = 0 if done else streak + 1 if failed else streak
    return [ok, bad, streak] if streak else [ok, bad]


def module_name(slot):
    """The importable module name of one secret set's library: ``lib_<set>``."""
    return 'lib_' + re.sub(r'[^a-z0-9]', '_', str(slot).lower())


def digest(source):
    return hashlib.sha256(source.encode('utf-8')).hexdigest()


def clean_functions(rows):
    """The listed functions of a library report: names, signatures and first docstring lines, bounded."""
    out = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not isinstance(row.get('name'), str) or not IDENTIFIER.match(row['name']):
            continue
        out.append({'name': row['name'], 'signature': ' '.join(str(row.get('signature') or '()').split())[:200],
                    'doc': ' '.join(str(row.get('doc') or '').split())[:160]})
        if len(out) >= MAX_FUNCTIONS:
            break
    return out


def dropped(before, after):
    """#7: the function names of library ``before`` that library ``after`` no longer has, in ``before``'s order.

    A submitted library replaces the whole module (no merge), so a function left out is removed;
    the save says so, and the earlier version stays kept as the rollback target.
    """
    kept = {item['name'] for item in (after or {}).get('functions') or []}
    return [item['name'] for item in (before or {}).get('functions') or [] if item['name'] not in kept]


class ProcedureLibrary:
    def __init__(self, store, clock=time.time):
        self.store, self.clock = store, clock
        with store.db() as db:
            db.executescript(DDL)

    @staticmethod
    def _row(row):
        if row is None:
            return None
        return {'id': row['id'], 'slot': row['slot'], 'version': row['version'], 'state': row['state'],
                'source': row['source'], 'digest': row['digest'], 'functions': json.loads(row['functions_json'] or '[]'),
                'stats': json.loads(row['stats_json'] or '{}'), 'work_id': row['work_id'], 'saved': row['saved']}

    def current(self, slot, db=None):
        if db is None:
            with self.store.db() as db:
                return self.current(slot, db)
        return self._row(db.execute("SELECT * FROM procedure_versions WHERE slot=? AND state=?", (slot, CURRENT)).fetchone())

    def for_run(self, slot, work_id):
        """What a run of ``work_id`` imports for ``slot``: this Work's own candidate, else the current version."""
        with self.store.db() as db:
            row = db.execute("SELECT * FROM procedure_versions WHERE slot=? AND state=? AND work_id=? ORDER BY created DESC",
                             (slot, CANDIDATE, work_id)).fetchone()
            return self._row(row) or self.current(slot, db)

    def propose(self, work_id, slot, source, functions):
        """Keep ``source`` as this Work's candidate for ``slot`` (replacing an earlier one); returns its id."""
        if slot == NOTES_SLOT:
            raise ValueError('reserved procedure slot')
        now = self.clock()
        row_id = str(uuid.uuid4())
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("DELETE FROM procedure_versions WHERE state=? AND created<?", (CANDIDATE, now - CANDIDATE_SECONDS))
            db.execute("DELETE FROM procedure_versions WHERE state=? AND slot=? AND work_id=?", (CANDIDATE, slot, work_id))
            db.execute('INSERT INTO procedure_versions(id,slot,version,state,source,digest,functions_json,work_id,created) '
                       'VALUES (?,?,?,?,?,?,?,?,?)',
                       (row_id, slot, None, CANDIDATE, source, digest(source), json.dumps(clean_functions(functions)), work_id, now))
        return row_id

    def record_calls(self, row_id, calls):
        """Add one run's per-function ``[succeeded, failed]`` call counts to a version's stats."""
        if not isinstance(calls, dict) or not calls:
            return
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            found = db.execute('SELECT stats_json FROM procedure_versions WHERE id=?', (row_id,)).fetchone()
            if found is None:
                return
            stats = json.loads(found['stats_json'] or '{}')
            for name, counts in list(calls.items())[:MAX_FUNCTIONS]:
                if not isinstance(name, str) or not IDENTIFIER.match(name) or not isinstance(counts, list) or len(counts) != 2:
                    continue
                done, failed = (max(0, int(value)) if isinstance(value, int) and not isinstance(value, bool) else 0
                                for value in counts)
                stats[name] = add_counts(stats.get(name), done, failed)
            db.execute('UPDATE procedure_versions SET stats_json=? WHERE id=?', (json.dumps(stats, sort_keys=True), row_id))

    def settle(self, work_id, reached):
        """Commit ``work_id``'s candidates when the Work reached its outcome; returns the newly saved versions.

        A Work that did not reach its outcome commits nothing; its candidates stay usable by a
        continuation of the same Work (an approval, a sign-in) and are dropped after
        ``CANDIDATE_SECONDS``.  A candidate identical to the current version only adds its counts.
        """
        if not reached:
            return []
        now = self.clock()
        saved = []
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            for row in db.execute("SELECT * FROM procedure_versions WHERE state=? AND work_id=? ORDER BY created",
                                  (CANDIDATE, work_id)).fetchall():
                candidate = self._row(row)
                current = self.current(candidate['slot'], db)
                if current is not None and current['digest'] == candidate['digest']:
                    stats = current['stats']
                    for name, counts in candidate['stats'].items():
                        stats[name] = add_counts(stats.get(name), counts[0], counts[1])
                    db.execute('UPDATE procedure_versions SET stats_json=? WHERE id=?', (json.dumps(stats, sort_keys=True), current['id']))
                    db.execute('DELETE FROM procedure_versions WHERE id=?', (candidate['id'],))
                    continue
                version = self._next_version(db, candidate['slot'])
                db.execute("UPDATE procedure_versions SET state=? WHERE slot=? AND state=?", (KEPT, candidate['slot'], CURRENT))
                db.execute('UPDATE procedure_versions SET state=?,version=?,saved=? WHERE id=?', (CURRENT, version, now, candidate['id']))
                saved.append({'slot': candidate['slot'], 'version': version, 'ref': REF_PREFIX + candidate['id'],
                              'functions': [item['name'] for item in candidate['functions']],
                              'dropped': dropped(current, candidate)})
        return saved

    @staticmethod
    def _next_version(db, slot):
        return (db.execute('SELECT MAX(version) AS v FROM procedure_versions WHERE slot=?', (slot,)).fetchone()['v'] or 0) + 1

    def commit(self, slot, source, *, work_id=None, stats=None, db=None):
        """#19: save ``source`` as ``slot``'s next current version at once; the previous current one is kept.

        For a slot whose revisions the host has already validated (``NOTES_SLOT``): no candidate
        stage.  ``stats`` is the version's small metadata (for the notes, the one-line change).
        Inside the caller's transaction when ``db`` is given.  Returns the saved row.
        """
        if db is None:
            with self.store.db() as conn:
                conn.execute('BEGIN IMMEDIATE')
                return self.commit(slot, source, work_id=work_id, stats=stats, db=conn)
        now, row_id = self.clock(), str(uuid.uuid4())
        version = self._next_version(db, slot)
        db.execute("UPDATE procedure_versions SET state=? WHERE slot=? AND state=?", (KEPT, slot, CURRENT))
        db.execute('INSERT INTO procedure_versions(id,slot,version,state,source,digest,functions_json,stats_json,work_id,created,saved) '
                   'VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                   (row_id, slot, version, CURRENT, source, digest(source), '[]', json.dumps(stats or {}, sort_keys=True),
                    work_id, now, now))
        return self._row(db.execute('SELECT * FROM procedure_versions WHERE id=?', (row_id,)).fetchone())

    def forgotten_digest(self, slot, value_digest):
        """Whether a version of ``slot`` with this digest was forgotten and not yet purged (never re-saved by the host)."""
        with self.store.db() as db:
            return db.execute('SELECT 1 FROM procedure_versions WHERE slot=? AND digest=? AND state=?',
                              (slot, value_digest, 'forgotten')).fetchone() is not None

    def remove_slot(self, slot):
        """A removed secret set keeps no library."""
        with self.store.db() as db:
            db.execute('DELETE FROM procedure_versions WHERE slot=?', (slot,))

    def listing(self, slot):
        """The context text of a set's current library, or ''."""
        try:
            current = self.current(slot)
        except Exception:
            return ''
        if current is None:
            return ''
        parts = []
        for item in current['functions'][:LISTED_FUNCTIONS]:
            done, failed, streak = (list(current['stats'].get(item['name']) or []) + [0, 0, 0])[:3]
            counts = f' [{done} ok, {failed} failed]' if done or failed else ''
            if streak >= RECENT_FAILURES:
                # A remote API that changed shows here before the worker relies on the function again.
                counts += f' [failed in the last {streak} runs in a row: check the documentation and fix it before relying on it]'
            parts.append(f"{item['name']}{item['signature']}{counts}" + (f" - {item['doc']}" if item['doc'] else ''))
        more = len(current['functions']) - LISTED_FUNCTIONS
        # Live 2026-10-14: a worker read the saved functions (one lookup) as all the set can do and told the
        # owner orders were not possible.  They are what worked before, not a limit.
        text = (f"saved functions v{current['version']} (import {module_name(slot)}; ref {REF_PREFIX}{current['id']}; "
                "what worked before, not a limit on what this set can do - write code for anything else): "
                + '; '.join(parts) + (f'; and {more} more' if more > 0 else ''))
        return text if len(text) <= LISTING_CHARS else text[:LISTING_CHARS - 1] + '…'
