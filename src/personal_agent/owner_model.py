"""Owner-model upkeep: keep the owner in mind, asynchronously and minimised (OWNER-MODEL-03, #805 phase 1).

Bounded by docs/secretary-agency-contract.en.md ("Amendment - #805 phase 1"):

* **Trigger.** A Work a worker AI answered that ends ``succeeded`` or
  ``partial`` records one durable ``owner_model_upkeep`` row, in the same
  transaction that settles it (idempotent per Work).  Failed, cancelled and
  unknown Works, rule/command replies (no worker AI ran), preparation runs
  and location continuations record none: typed markers, never text rules.
* **Tick.** The existing service loop asks one indexed query per tick.  At
  most one pending row is claimed per tick, atomically with the check that
  no Work is queued or running, and runs off the work thread (single-flight,
  a hard deadline).  Nothing pending costs no model call.
* **Judgment (C16).** The owner's Judgment AI (``DecisionEngine.structured``)
  reads the owner's request (owner words), the final answer excerpt
  (model-stated), the profile snapshot and the clock, every fact redacted
  first, and proposes at most ``MAX_PROPOSALS`` durable owner facts.
* **Validation (Still).** Out-of-enum, oversized, non-``profile.`` and
  duplicate proposals are dropped.  What is kept is saved at once as current
  Memory attributed to the source Work (#918 slice a, owner decision
  2026-09-30: the owner's own AI acts for the owner and the owner is told
  afterwards with an undo, the Work's ``memory_saved`` notice).  The per-fact
  #597 judgment and the MemoryCandidate ask are no longer on this path; C5
  is unchanged for third-party writers.
* **Minimisation.** An owner pause switch and a rolling 24-hour cap on
  every model call (``CONFIG_KEY``), checked before each call.  The newest
  pending row runs first and one older than ``MAX_PENDING_SECONDS`` expires
  without a call (#876: an ask must come while the conversation is live, not
  a day later when a backlog drains under the cap), and a claimed
  row a crash left behind expires as ``interrupted``.  Each run is one
  ``owner_model`` tool event on the source Work: counts, applied keys, kinds,
  outcomes and the deciding route - never the input texts, and a dropped
  proposal's key only as a digest.

**Self-review (SELF-REVIEW-01, #998).** The same rows, tick, cap and pause
carry a second kind of run, ``self-review``: like a secretary who notices
she fell short, the assistant reviews a Work from its own records and keeps
what it learned.  Typed markers enqueue it - a Work a worker AI answered
that ended ``failed``, a Work the next owner turn's continuity judgment
linked as a ``correction`` or ``retry``, or an owner reaction another change
observes (``enqueue_self_review``) - one row per Work.  The Judgment AI reads
the owner's request, the answer excerpt, the orchestrator's evaluation
verdicts and the owner's follow-up (or the observation), every fact redacted
first, and returns at most one **blocker** (a cause only Still code can fix;
kept as Evidence on the reviewed Work, redacted, and never filed anywhere)
and, when it learned how to serve this owner better, the **working notes**
revised as a whole.  No text rule reads the owner's wording; nothing names a
site, task or category.

**Working notes (STEADY-NOTES-01, #19).**  How to work for this owner lives
in one bounded document, not in ``profile.`` fact rows: ``WorkingNotes``
keeps its versions in the reserved slot of the procedural version store
(``procedures.NOTES_SLOT``).  The judgment reads the current document and
returns either no change or the full revision with a one-line change.  Still
enforces the bounds (``NOTES_CHARS``, ``NOTES_POINTS``, ``POINT_CHARS``) and
refuses a stored secret or credential shape, a revision given with a blocker
(a software cause is not a preference), one that only rewords the marker,
spacing, case or order of the current points, one that adds a semantic owner
fact already in Memory, and one equal to a version the owner forgot.  A kept
revision is the next version, attributed to the reviewed Work; the owner is
told once, and forgetting it (``forget_record`` ``procedure:<id>``) puts the
previous document back, undoable for the existing seven days.  Earlier
``profile.working.`` Memory rows are moved into the first version once
(``WorkingNotes.migrate``), each under an undoable ``migrate`` receipt.
Semantic owner facts stay in Memory.

Nothing here names a site, provider, task or request category.
"""
import hashlib
import json
import re
import time
from datetime import datetime, timezone

from .memory_service import PROFILE_SECTION_CHARS
from .owner_forget import OwnerForget
from .prompt_files import prompt

#: The source Work's tool-event name of every upkeep run (#794 shows it).
EVENT_TOOL = 'owner_model'
#: The DecisionContext purpose of the proposal judgment.
PURPOSE = 'owner-model-upkeep'
#: #998: the two kinds of run one row may be; the row key is ``(job_id, kind)``.
KIND_UPKEEP, KIND_SELF_REVIEW = 'upkeep', 'self-review'
RUN_KINDS = (KIND_UPKEEP, KIND_SELF_REVIEW)
#: The reviewed Work's tool-event name of every self-review run.
REVIEW_EVENT_TOOL = 'self_review'
#: The DecisionContext purpose of the self-review judgment.
REVIEW_PURPOSE = 'self-review'
#: Why a self-review was enqueued (typed markers, never text rules).
REVIEW_FAILED, REVIEW_CORRECTION, REVIEW_RETRY, REVIEW_REACTION = 'failed', 'correction', 'retry', 'reaction'
REVIEW_REASONS = (REVIEW_FAILED, REVIEW_CORRECTION, REVIEW_RETRY, REVIEW_REACTION)
#: The Memory namespace lessons used before #19: its rows move into the working notes once (``WorkingNotes.migrate``),
#: and upkeep proposes no fact under it (working methods are not semantic facts).
LESSON_PREFIX = 'profile.working.'
#: #19, the inherited design: the working notes are about 1,500 characters, at most 12 short points, in the
#: owner's language.  A revision over a bound is refused whole, never cut.
NOTES_CHARS = 1500
NOTES_POINTS = 12
POINT_CHARS = 200
CHANGE_CHARS = 200
#: The config row that records the one-time move of ``profile.working.`` rows (receipts, counts, version).
MIGRATION_KEY = 'working_notes_migration'
#: The change line of the version the move creates (the brief reads it; the owner is not sent a notice for it).
MIGRATION_CHANGE = 'collected the earlier saved working lessons into these notes'
#: Outcome of a kept revision in the ``self_review`` Evidence.
APPLIED_NOTES = 'working-notes'
#: The Telegram notice of one revision, queued with the version (``fingerprint``: its ref and number).
NOTICE_KIND = 'working_notes_saved'
#: Bounds of the self-review facts and of a stored observation.
EVALUATION_CHARS = 1500
FOLLOWUP_CHARS = 1500
MAX_OBSERVATION_CHARS = 500
#: A reviewed Work must have settled; a queued or running one is not reviewed.
REVIEWABLE_STATUSES = ('succeeded', 'partial', 'failed', 'unknown')
#: The owner's controls: ``{'enabled': bool, 'daily_calls': int}``.
CONFIG_KEY = 'owner_model_upkeep'
DEFAULT_DAILY_CALLS = 20
MAX_DAILY_CALLS = 200
#: The cap is a rolling window, so it needs no time zone.
WINDOW_SECONDS = 24 * 3600
#: A pending upkeep this old is dropped without a model call (#876): its ask
#: would reach the owner after the conversation it is about has moved on.
MAX_PENDING_SECONDS = 3600
#: No model call starts after a run has been going this long (each call is also
#: bounded by its adapter's own timeout).
RUN_SECONDS = 300
#: A row still ``claimed`` this long after its claim was cut off by a crash.
CLAIM_STALE_SECONDS = 3600

CATEGORIES = ('identity', 'place', 'routine', 'schedule', 'preference')
KIND_STATED, KIND_INFERRED = 'stated', 'inferred'
KINDS = (KIND_STATED, KIND_INFERRED)
PROFILE_PREFIX = 'profile.'
MAX_PROPOSALS = 5
#: #220: earlier facts one proposal may end, each named by the short id owner_profile shows.
MAX_INVALIDATES = 3
_SHORT_ID = re.compile(r'[0-9a-f]{8}')
_DATE = re.compile(r'\d{4}-\d{2}-\d{2}')
MAX_TEXT_CHARS = 200
MAX_KEY_CHARS = 160
#: Bounds of the facts one judgment reads (the owner's request is never cut).
ANSWER_CHARS = 1500
#: #217: the whole profile the worker reads, so upkeep sees a fact before it proposes a new key.
PROFILE_CHARS = PROFILE_SECTION_CHARS
#: What was already noted from the same request (the earlier bound of the profile fact).
NOTED_CHARS = 1200
CLOCK_CHARS = 300
#: SITUATION-01 (#1004): the running note of the owner's day the same judgment returns
#: (``current_context.SITUATION_CHARS`` bounds what is stored).
SITUATION_CHARS = 400
#: The earlier turns a rewrite folds in, as the judgment reads them (#1005 review).
SITUATION_TURNS_CHARS = 4000

STATE_PENDING, STATE_CLAIMED = 'pending', 'claimed'
STATE_DONE, STATE_UNAVAILABLE, STATE_EXPIRED, STATE_GONE = 'done', 'unavailable', 'expired', 'gone'
#: Tool-event statuses: never ``succeeded``/``failed``, so an upkeep row is not a Work step.
EVENT_RECORDED, EVENT_UNAVAILABLE, EVENT_EXPIRED = 'recorded', 'unavailable', 'expired'
#: Outcomes of one applied proposal.
APPLIED_MEMORY, APPLIED_CANDIDATE = 'memory', 'candidate'
#: Historical refusal reasons of the #597 per-fact gate (Evidence rows written before #918 slice a).
REFUSED_INFERRED = 'inferred-stays-candidate'
REFUSED_BUDGET = 'no-call-budget'
REFUSED_DEADLINE = 'deadline'
#: Why a run stopped before its last proposal (recorded as ``stopped``).
STOPPED_PAUSED, STOPPED_CAP, STOPPED_DEADLINE = 'paused', 'cap', 'deadline'


def call_sent(decision):
    """Whether one decision reached its engine's transport (#805 review).

    An adapter reports ``sent``; an answer given before any transport (no
    route configured, invalid configuration, cancelled, too large, a
    structured-unsupported route) is not a call.  An engine that does not
    report it is counted, so the cap never undercounts.
    """
    confidence = decision.confidence
    return confidence.sent is not False and confidence.engine != STRUCTURED_UNSUPPORTED_ENGINE

#: ``decision.STRUCTURED_UNSUPPORTED`` (kept stdlib-only here; a test pins the two equal).
STRUCTURED_UNSUPPORTED_ENGINE = 'structured-unsupported'

TABLE_SQL = '''
CREATE TABLE IF NOT EXISTS owner_model_upkeep(job_id TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'upkeep',
    state TEXT NOT NULL, created REAL NOT NULL, claimed REAL, calls INTEGER NOT NULL DEFAULT 0,
    reason TEXT, observation TEXT, PRIMARY KEY(job_id, kind));
CREATE INDEX IF NOT EXISTS owner_model_upkeep_state ON owner_model_upkeep(state, created);
CREATE INDEX IF NOT EXISTS owner_model_upkeep_claimed ON owner_model_upkeep(claimed);
'''


def migrate(db):
    """Give a pre-#998 ``owner_model_upkeep`` table its ``kind`` key; returns whether it was rebuilt.

    SQLite cannot widen a primary key in place, so the old rows (all upkeep)
    are copied into the new shape once.  Idempotent: a table that already has
    ``kind`` is left alone.
    """
    columns = {row['name'] for row in db.execute('PRAGMA table_info(owner_model_upkeep)')}
    if not columns or 'kind' in columns:
        return False
    db.executescript('''
DROP INDEX IF EXISTS owner_model_upkeep_state;
DROP INDEX IF EXISTS owner_model_upkeep_claimed;
ALTER TABLE owner_model_upkeep RENAME TO owner_model_upkeep_v1;
''' + TABLE_SQL + '''
INSERT INTO owner_model_upkeep(job_id,kind,state,created,claimed,calls)
    SELECT job_id,'upkeep',state,created,claimed,calls FROM owner_model_upkeep_v1;
DROP TABLE owner_model_upkeep_v1;
''')
    return True


REVIEW_QUESTION = prompt('owner_model_review').format(notes_chars=NOTES_CHARS, notes_points=NOTES_POINTS,
                                                       point_chars=POINT_CHARS)

REVIEW_SCHEMA = {'type': 'object', 'additionalProperties': False,
                 'properties': {'notes': {'type': 'string'}, 'change': {'type': 'string'},
                                'blocker': {'type': 'string'}},
                 'required': ['notes', 'change', 'blocker']}


def review_shape(data):
    """Types only; each note's meaning is ``validate_review``'s."""
    return all(isinstance(data.get(name), str) for name in ('notes', 'change', 'blocker'))


def lesson_key(key):
    """Whether ``key`` is a well-formed key of the pre-#19 lesson namespace (a ``profile.working.`` Memory key)."""
    return profile_key(key) and key.startswith(LESSON_PREFIX) and len(key) > len(LESSON_PREFIX)


#: A list marker a line of the document may start with; the stored form is ``- <point>``.
_MARKER = re.compile(r'^[-*•·]\s+')


def notes_points(text):
    """The points of a working-notes text: one per non-empty line, whitespace collapsed, list marker removed.

    A point repeated in other spacing, marker or case is kept once (the first), so the
    document never holds the same point twice.
    """
    points, seen = [], set()
    for line in str(text or '').splitlines():
        point = _MARKER.sub('', ' '.join(line.split()), count=1).strip()
        if point and normalized(point) not in seen:
            seen.add(normalized(point))
            points.append(point)
    return points


def notes_text(points):
    """The stored form of the document: one ``- <point>`` line per point."""
    return '\n'.join('- ' + point for point in points)


def validate_review(data, current=(), known=frozenset(), request=''):
    """``(revision, blocker, dropped)``: what Still may keep of one self-review answer (deterministic).

    ``revision`` is ``{'points', 'text', 'change'}`` or None; ``blocker`` a
    short text or None.  ``current`` holds the points of the current working
    notes.  A revision is refused whole - never cut - when it breaks a bound
    (more than ``NOTES_POINTS`` points, a point over ``POINT_CHARS``, a
    document over ``NOTES_CHARS``); when it holds the same points as
    ``current`` (another marker, spacing, case or order is no change); when
    it comes with a blocker (a software cause is not a preference about the
    owner); without a one-line change; or when a point it adds is the request
    sentence or a current ``profile.`` value in ``known`` (semantic facts stay
    in Memory).  ``dropped`` lists each refused note with its reason, never
    its text.
    """
    data = data if isinstance(data, dict) else {}
    dropped, revision, blocker = [], None, None
    note = data.get('blocker')
    note = ' '.join(note.split()) if isinstance(note, str) else ''
    if note:
        if len(note) > MAX_TEXT_CHARS:
            dropped.append({'note': 'blocker', 'reason': 'content'})
        else:
            blocker = note
    text, change = data.get('notes'), data.get('change')
    points = notes_points(text if isinstance(text, str) else '')
    change = ' '.join(change.split()) if isinstance(change, str) else ''
    if points:
        before = {normalized(point) for point in current}
        added = [normalized(point) for point in points if normalized(point) not in before]
        reason = None
        if len(points) > NOTES_POINTS:
            reason = 'points'
        elif any(len(point) > POINT_CHARS for point in points):
            reason = 'point-length'
        elif len(notes_text(points)) > NOTES_CHARS:
            reason = 'length'
        elif not added and len(points) == len(before):
            reason = 'unchanged'
        elif note:
            # Review P2: any blocker the judgment gave, even one too long to keep as Evidence.
            reason = 'blocker'
        elif not change or len(change) > CHANGE_CHARS:
            reason = 'change'
        elif normalized(request) and normalized(request) in added:
            reason = 'request'
        elif any(point in known for point in added):
            reason = 'profile-fact'
        if reason is None:
            revision = {'points': points, 'text': notes_text(points), 'change': change}
        else:
            dropped.append({'note': 'notes', 'reason': reason})
    return revision, blocker, dropped

QUESTION = prompt('owner_model_facts').format(categories=', '.join(CATEGORIES))

SCHEMA = {'type': 'object', 'additionalProperties': False,
          'properties': {'proposals': {'type': 'array', 'items': {
              'type': 'object', 'additionalProperties': False,
              'properties': {'category': {'type': 'string', 'enum': list(CATEGORIES)},
                             'kind': {'type': 'string', 'enum': list(KINDS)},
                             'memory_key': {'type': 'string'},
                             'content': {'type': 'string'},
                             'evidence': {'type': 'string'},
                             'supersedes_key': {'type': 'string'},
                             # #220: earlier facts (by the id owner_profile shows) this fact ends, and its last day.
                             'invalidates': {'type': 'array', 'items': {
                                 'type': 'object', 'additionalProperties': False,
                                 'properties': {'id': {'type': 'string'}, 'reason': {'type': 'string'}},
                                 'required': ['id', 'reason']}},
                             'valid_until': {'type': 'string'}},
              'required': ['category', 'kind', 'memory_key', 'content', 'evidence', 'supersedes_key', 'invalidates',
                           'valid_until']}},
                         'situation': {'type': 'string'}},
          'required': ['proposals', 'situation']}


def shape(data):
    """Types only; each proposal's meaning is ``validate``'s (an absent situation is no note)."""
    return isinstance(data.get('proposals'), list) and isinstance(data.get('situation', ''), str)


#: #794 phase 3: an upkeep proposal whose exact value the owner forgot (``owner_forget``).
FORGOTTEN_VALUE = 'forgotten-value'


def normalized(text):
    return ' '.join(str(text or '').split()).casefold()


def profile_key(key):
    """Whether ``key`` is a well-formed ``profile.`` Memory key."""
    return (isinstance(key, str) and key.startswith(PROFILE_PREFIX) and len(PROFILE_PREFIX) < len(key) <= MAX_KEY_CHARS
            and not any(character.isspace() for character in key))


def _refusal(item):
    """Why one proposal is out of shape or bounds, or None."""
    if not isinstance(item, dict):
        return 'shape'
    if any(not isinstance(item.get(name), str) for name in ('category', 'kind', 'memory_key', 'content', 'evidence')):
        return 'shape'
    if item['category'] not in CATEGORIES:
        return 'category'
    if item['kind'] not in KINDS:
        return 'kind'
    if not profile_key(item['memory_key']) or item['memory_key'].startswith(LESSON_PREFIX):
        # #19: how to work for the owner lives in the working notes, not in a ``profile.`` fact.
        return 'key'
    if not item['content'].strip() or len(item['content'].strip()) > MAX_TEXT_CHARS:
        return 'content'
    if not item['evidence'].strip() or len(item['evidence'].strip()) > MAX_TEXT_CHARS:
        return 'evidence'
    supersedes = item.get('supersedes_key') or ''
    # #805 review: ``supersedes_key`` names only the proposal's own key; another key's row is ended
    # through ``invalidates`` by its id (#220), never through a key name.
    if not isinstance(supersedes, str) or (supersedes.strip() and supersedes.strip() != item['memory_key']):
        return 'supersedes'
    ends = item.get('invalidates', [])
    if not isinstance(ends, list) or len(ends) > MAX_INVALIDATES or any(
            not isinstance(end, dict) or not isinstance(end.get('id'), str) or not _SHORT_ID.fullmatch(end['id'].strip())
            or not isinstance(end.get('reason'), str) or not end['reason'].strip()
            or len(end['reason'].strip()) > MAX_TEXT_CHARS for end in ends):
        return 'invalidates'
    until = item.get('valid_until', '')
    if not isinstance(until, str):
        return 'valid_until'
    if until.strip():
        try:
            if not _DATE.fullmatch(until.strip()):
                raise ValueError
            datetime.fromisoformat(until.strip())
        except ValueError:
            return 'valid_until'
    return None


def key_digest(key):
    """A model-chosen key as Evidence keeps it for a dropped proposal: a short digest."""
    return hashlib.sha256(str(key).encode()).hexdigest()[:12]


def validate(proposals, known, pending, noted_keys=(), request=''):
    """``(kept, dropped)`` of the proposals Still may apply (deterministic).

    ``known`` is the normalized content of every current ``profile.`` Memory
    value and of everything the source Work already wrote (Memory or
    candidate), under any key: the same content under another key is a
    duplicate.  ``pending`` is ``{(memory_key, normalized content)}`` of the
    pending candidates.  ``noted_keys`` are the keys the source Work already
    wrote or proposed (#836: one ask per owner message, so upkeep never adds
    a second fact under a key that Work already covers).  ``request`` is the
    owner's request text: content that is that sentence is the request, not a
    fact about the owner (#846; an equality check on the value's shape, no
    intent detection).  ``dropped`` keeps a key only as a digest.
    """
    request_text = normalized(request)
    kept, dropped, keys, contents = [], [], set(), set()
    for index, item in enumerate(proposals if isinstance(proposals, list) else ()):
        reason = 'over-limit' if index >= MAX_PROPOSALS else _refusal(item)
        key = item.get('memory_key') if isinstance(item, dict) and isinstance(item.get('memory_key'), str) else None
        if reason is None:
            content = normalized(item['content'])
            if request_text and content == request_text:
                reason = 'request'
            elif (key in keys or key in noted_keys or content in contents or content in known
                    or (key, content) in pending):
                reason = 'duplicate'
        if reason is not None:
            dropped.append({'key_digest': key_digest(key), 'reason': reason} if key else {'reason': reason})
            continue
        keys.add(key)
        contents.add(content)
        kept.append({'category': item['category'], 'kind': item['kind'], 'memory_key': key,
                     'content': item['content'].strip(), 'supersedes_key': (item.get('supersedes_key') or '').strip(),
                     'invalidates': [{'id': end['id'].strip(), 'reason': ' '.join(end['reason'].split())}
                                     for end in item.get('invalidates') or ()],
                     'valid_until': (item.get('valid_until') or '').strip()})
    return kept, dropped


class Upkeep:
    """The durable upkeep queue and the owner's controls, in the owner's QuickStore."""

    def __init__(self, store, clock=time.time):
        self.store, self.clock = store, clock

    # -- controls -----------------------------------------------------------
    @staticmethod
    def _controls(value):
        value = value if isinstance(value, dict) else {}
        cap = value.get('daily_calls', DEFAULT_DAILY_CALLS)
        if isinstance(cap, bool) or not isinstance(cap, int):
            cap = DEFAULT_DAILY_CALLS
        return {'enabled': value.get('enabled', True) is not False, 'daily_calls': max(0, min(MAX_DAILY_CALLS, cap))}

    def settings(self, db=None):
        """``{'enabled', 'daily_calls'}``: enabled by default (#805)."""
        if db is None:
            return self._controls(self.store.config(CONFIG_KEY))
        row = db.execute('SELECT value FROM config WHERE key=?', (CONFIG_KEY,)).fetchone()
        try:
            return self._controls(json.loads(row['value']) if row else None)
        except (TypeError, ValueError):
            return self._controls(None)

    def set_controls(self, body):
        """The owner's pause switch and call cap; nothing else."""
        body = body if isinstance(body, dict) else {}
        current = self.settings()
        if 'enabled' in body:
            if not isinstance(body['enabled'], bool):
                raise ValueError('알아 두기 사용 여부를 확인하세요.')
            current['enabled'] = body['enabled']
        if 'daily_calls' in body:
            cap = body['daily_calls']
            if isinstance(cap, bool) or not isinstance(cap, int) or not 0 <= cap <= MAX_DAILY_CALLS:
                raise ValueError(f'하루 판단 횟수는 0~{MAX_DAILY_CALLS} 사이로 입력하세요.')
            current['daily_calls'] = cap
        self.store.put(CONFIG_KEY, current)
        return self.status()

    def calls_used(self, now, db=None):
        """Every model call counted in the rolling window (proposal and per-fact judgments)."""
        if db is None:
            with self.store.db() as conn:
                return self.calls_used(now, conn)
        row = db.execute('SELECT COALESCE(SUM(calls),0) FROM owner_model_upkeep WHERE claimed>=?',
                         (now - WINDOW_SECONDS,)).fetchone()
        return int(row[0] or 0)

    def allowance(self, now):
        """``(calls the owner's current settings allow now, why none)``: re-read before every call.

        ``why`` is ``paused`` when the owner paused upkeep, ``cap`` when the
        rolling cap is spent (this run's calls so far included), else None.
        """
        with self.store.db() as db:
            settings = self.settings(db)
            if not settings['enabled']:
                return 0, STOPPED_PAUSED
            left = max(0, settings['daily_calls'] - self.calls_used(now, db))
        return left, (None if left else STOPPED_CAP)

    def status(self, now=None):
        now = self.clock() if now is None else now
        with self.store.db() as db:
            pending = db.execute('SELECT COUNT(*) FROM owner_model_upkeep WHERE state=?', (STATE_PENDING,)).fetchone()[0]
            # #832: a claimed row is an upkeep run in flight; read-only, so a caller can tell the queue is idle.
            running = db.execute('SELECT COUNT(*) FROM owner_model_upkeep WHERE state=?', (STATE_CLAIMED,)).fetchone()[0]
            used = self.calls_used(now, db)
        return {**self.settings(), 'pending': int(pending), 'running': int(running), 'calls_last_24h': used}

    # -- the queue ----------------------------------------------------------
    def enqueue(self, db, job_id, now=None, *, kind=KIND_UPKEEP, reason=None, observation=None):
        """One pending run of ``kind`` for ``job_id``; idempotent per ``(Work, kind)``.

        Inside the caller's transaction when ``db`` is given, else its own.
        ``reason`` and ``observation`` belong to a self-review row (#998);
        the observation is a short typed note (an owner reaction) that the
        judgment reads redacted, bounded here.
        """
        if kind not in RUN_KINDS:
            raise ValueError('upkeep kind')
        if db is None:
            with self.store.db() as conn:
                conn.execute('BEGIN IMMEDIATE')
                return self.enqueue(conn, job_id, now, kind=kind, reason=reason, observation=observation)
        if not self.settings(db)['enabled']:
            return False
        if observation is not None:
            # Review P1: never cut here - a cut can split a stored secret so the later
            # redactor no longer recognises it.  The judgment redacts, then bounds.
            # An observation too long to be a short typed note is not kept at all.
            observation = ' '.join(str(observation).split()) or None
            if observation is not None and len(observation) > MAX_OBSERVATION_CHARS:
                observation = None
        return db.execute('INSERT OR IGNORE INTO owner_model_upkeep(job_id,kind,state,created,reason,observation) '
                          'VALUES (?,?,?,?,?,?)',
                          (job_id, kind, STATE_PENDING, self.clock() if now is None else now, reason, observation)).rowcount == 1

    def expire_interrupted(self, now):
        """Claimed rows a crash left behind: expired with an ``interrupted`` event (call only with no run in flight)."""
        with self.store.db() as db:
            rows = [(row['job_id'], row['kind']) for row in db.execute(
                'SELECT job_id,kind FROM owner_model_upkeep WHERE state=? AND claimed<?',
                (STATE_CLAIMED, now - CLAIM_STALE_SECONDS))]
        for job_id, kind in rows:
            self.finish(job_id, STATE_EXPIRED, None, EVENT_EXPIRED, {'reason': 'interrupted'}, now, kind=kind)
        return len(rows)

    def claim_due(self, now):
        """Claim the newest pending row this tick may run, or None (#876: the live conversation first).

        One immediate transaction: the check that no Work is queued or
        running and the claim are atomic with ``run_one``'s own claim, so an
        upkeep never starts beside a Work that was already claimed.  A
        claimed row is never run again, even after a restart; it counts one
        call until the run records its real number.  ``remaining`` is the
        budget at claim time, for the record: the run re-reads it before each
        later call (``allowance``).
        """
        select = ('SELECT job_id,kind,created,reason,observation FROM owner_model_upkeep WHERE state=? '
                  'ORDER BY created DESC LIMIT 1')
        with self.store.db() as db:
            row = db.execute(select, (STATE_PENDING,)).fetchone()
            if row is None:
                return None
            db.execute('BEGIN IMMEDIATE')
            row = db.execute(select, (STATE_PENDING,)).fetchone()
            settings = self.settings(db)
            if row is None or not settings['enabled']:
                return None
            if db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running') LIMIT 1").fetchone():
                return None
            expired = now - row['created'] > MAX_PENDING_SECONDS
            remaining = settings['daily_calls'] - self.calls_used(now, db)
            if not expired and remaining < 1:
                return None
            db.execute('UPDATE owner_model_upkeep SET state=?,claimed=?,calls=? WHERE job_id=? AND kind=?',
                       (STATE_CLAIMED, now, 0 if expired else 1, row['job_id'], row['kind']))
            return {'job_id': row['job_id'], 'kind': row['kind'], 'created': row['created'], 'expired': expired,
                    'remaining': remaining, 'reason': row['reason'], 'observation': row['observation']}

    def count(self, job_id, calls, kind=KIND_UPKEEP):
        """Record the calls a run has made so far (the cap sees them at once)."""
        with self.store.db() as db:
            db.execute('UPDATE owner_model_upkeep SET calls=? WHERE job_id=? AND kind=?', (int(calls), job_id, kind))

    def finish(self, job_id, state, calls, event_status, detail, now, kind=KIND_UPKEEP):
        """Settle a claimed row and record its one tool event on the source Work (``self_review`` for a review)."""
        tool = REVIEW_EVENT_TOOL if kind == KIND_SELF_REVIEW else EVENT_TOOL
        with self.store.db() as db:
            if calls is None:
                db.execute('UPDATE owner_model_upkeep SET state=? WHERE job_id=? AND kind=?', (state, job_id, kind))
            else:
                db.execute('UPDATE owner_model_upkeep SET state=?,calls=? WHERE job_id=? AND kind=?',
                           (state, int(calls), job_id, kind))
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (job_id, tool, event_status, json.dumps(detail, ensure_ascii=False), now))

    def followups(self, job_id, limit=3):
        """The owner's later messages the continuity judgment linked to ``job_id`` as a correction or retry (#998).

        Read, not judged: only the stored ``relation_kind`` / ``related_job_id``
        link is used, oldest first.
        """
        with self.store.db() as db:
            rows = db.execute("SELECT message FROM jobs WHERE related_job_id=? AND relation_kind IN (?,?) AND id!=? "
                              'ORDER BY created LIMIT ?', (job_id, REVIEW_CORRECTION, REVIEW_RETRY, job_id, limit)).fetchall()
        return [str(row['message'] or '') for row in rows if row['message']]

    def awaiting(self, job_id):
        """What ``job_id`` left waiting for the owner's own decision, and what became of it (#170); '' when nothing.

        Read from recorded tool events only: each item a tool result marked as waiting for the owner
        (``telegram_presence.OWNER_DECISION_FLAGS``, not applied, not a refused write) and, by its
        ``draft_id``, whether a later recorded event settled it.  Names and counts only, no content.
        """
        from .telegram_presence import OWNER_DECISION_FLAGS
        with self.store.db() as db:
            rows = db.execute('SELECT tool,detail FROM tool_events WHERE job_id=? AND status IN (?,?) ORDER BY id',
                              (job_id, 'succeeded', 'failed')).fetchall()
            waiting = {}
            for row in rows:
                try:
                    detail = json.loads(row['detail'] or '{}')
                except (TypeError, ValueError):
                    continue
                if not isinstance(detail, dict):
                    continue
                evidence = detail.get('evidence') if isinstance(detail.get('evidence'), dict) else {}
                record = evidence if any(evidence.get(flag) for flag in OWNER_DECISION_FLAGS) else detail
                if (record.get('refused_because') or record.get('applied') is True
                        or not any(record.get(flag) for flag in OWNER_DECISION_FLAGS)):
                    continue
                action = detail.get('host_action') or row['tool']
                ident = record.get('draft_id')
                settled = False
                if isinstance(ident, str) and ident:
                    settled = db.execute("SELECT 1 FROM tool_events WHERE job_id!=? AND status='succeeded' AND detail LIKE ? LIMIT 1",
                                         (job_id, '%' + json.dumps(ident)[1:-1] + '%')).fetchone() is not None
                counts = waiting.setdefault(action, [0, 0])
                counts[0] += 1
                counts[1] += 1 if settled else 0
        if not waiting:
            return ''
        return '; '.join(f'{action}: {total} shown for the owner\'s own approval, {done} later settled by the owner'
                         for action, (total, done) in waiting.items())

    def evaluations(self, job_id, limit=6):
        """The orchestrator's recorded verdict texts on ``job_id``'s attempts, oldest first (#710 Evidence)."""
        with self.store.db() as db:
            rows = db.execute("SELECT detail FROM tool_events WHERE job_id=? AND tool='orchestrator' AND status='evaluated' "
                              'ORDER BY id LIMIT ?', (job_id, limit)).fetchall()
        texts = []
        for row in rows:
            try:
                trace = json.loads(row['detail'])
            except (TypeError, ValueError):
                continue
            if isinstance(trace, dict) and isinstance(trace.get('text'), str) and trace['text'].strip():
                texts.append(trace['text'].strip())
        return texts

    # -- known values -------------------------------------------------------
    def known_values(self, owner, job_id):
        """``(known, pending)`` for ``validate``: see there."""
        known, offset = set(), 0
        while True:
            page = self.store.memories(owner, limit=101, offset=offset, key_prefix=PROFILE_PREFIX)
            known |= {normalized(row['content']) for row in page}
            if len(page) < 101:
                break
            offset += 101
        work_key = self.store._work_binding(job_id)
        with self.store.db() as db:
            # #805 review: what this Work already wrote (the worker's own save_memory), any key or state.
            known |= {normalized(row['content']) for row in db.execute(
                "SELECT content FROM memories WHERE work_key=? AND state='current'", (work_key,))}
            known |= {normalized(row['content']) for row in db.execute(
                'SELECT content FROM memory_candidates WHERE work_key=?', (work_key,))}
            pending = {(row['memory_key'], normalized(row['content'])) for row in db.execute(
                "SELECT memory_key,content FROM memory_candidates WHERE state='pending' AND memory_key LIKE 'profile.%'")}
        return known, pending

    def current_rows_by_short_id(self, owner_id, short_ids):
        """The full ids of the current ``profile.`` rows the short ids name, in order; None if any names none or several (#220)."""
        if not short_ids:
            return []
        owner_key = self.store._memory_binding(owner_id)
        found = []
        with self.store.db() as db:
            for short in short_ids:
                rows = db.execute("SELECT id FROM memories WHERE owner_key=? AND state='current' AND memory_key LIKE 'profile.%' "
                                  "AND memory_key NOT LIKE ? AND substr(id,1,?)=?",
                                  (owner_key, LESSON_PREFIX + '%', len(short), short)).fetchall()
                if len(rows) != 1:
                    return None
                found.append(rows[0]['id'])
        return found if len(set(found)) == len(found) else None

    def work_noted(self, job_id):
        """``(keys, contents)`` the source Work already wrote or proposed, any state (#836).

        A rejected candidate keeps neither (the reject erases them).  The
        contents go to the proposal judgment as ``already_noted``; the keys to
        ``validate``.
        """
        work_key = self.store._work_binding(job_id)
        with self.store.db() as db:
            rows = [tuple(row) for row in db.execute(
                "SELECT memory_key,content FROM memory_candidates WHERE work_key=? AND memory_key!='' ORDER BY created,id",
                (work_key,))]
            rows += [tuple(row) for row in db.execute(
                "SELECT memory_key,content FROM memories WHERE work_key=? AND state='current' ORDER BY created,id",
                (work_key,))]
        return {key for key, _content in rows}, list(dict.fromkeys(content for _key, content in rows if content))

    # -- one run ------------------------------------------------------------
    def run(self, job, judgments, *, answer, profile, clock, cancelled=None, situation=None, note_situation=None,
            situation_turns=''):
        """Ask, validate and apply for one finished Work; returns ``(state, calls, event_status, detail)``.

        ``judgments`` is a ``ConversationJudgments`` whose redactor is scoped
        to this Work.  Only a confident structured answer is applied.  Only a
        call that reached a transport is counted.  Before every later call,
        and before each write, the owner's pause switch and the rolling cap
        are read again; a pause stops the run, a spent cap or the deadline
        (``cancelled()``) stops further calls, and ``stopped`` says which.

        SITUATION-01 (#1004): ``situation`` is the live running note of the
        owner's day ('' for none) and ``note_situation(text)`` records the
        judgment's rewrite of it; both are None while current context is off,
        and then no note is read or written.  An empty rewrite keeps the note.
        ``situation_turns`` are the turns since the note that this rewrite
        folds in (scrubbed by the caller), oldest first.
        """
        from .agent_runtime import MEMORY_OWNER, SECRET_SHAPED_VALUE, memory_value_has_secret
        stop = cancelled or (lambda: False)
        request = str(job.get('message') or '')
        noted_keys, noted = self.work_noted(job['id'])
        data, decision = judgments.owner_model_proposals(request, answer, profile, clock, work_id=job['id'],
                                                         cancelled=cancelled, noted=noted,
                                                         situation=situation if note_situation else None,
                                                         situation_turns=situation_turns if note_situation else '')
        confidence = decision.confidence
        calls = 1 if call_sent(decision) else 0
        self.count(job['id'], calls)
        detail = {'decision': decision.outcome, 'route': confidence.route or None, 'provider': confidence.provider or None,
                  'model': confidence.model or None, 'observed_model': confidence.observed_model or None,
                  'confidence': confidence.probability, 'sent': bool(calls),
                  'inputs': {'owner_request_chars': len(request), 'answer_chars': len(answer or ''),
                             'profile_chars': len(profile or ''), 'clock': bool(clock)}}
        if data is None:
            return STATE_UNAVAILABLE, calls, EVENT_UNAVAILABLE, detail
        known, pending = self.known_values(MEMORY_OWNER, job['id'])
        kept, dropped = validate(data.get('proposals'), known, pending, noted_keys, request=request)
        applied, stopped = [], None
        for index, item in enumerate(kept):
            key, content = item['memory_key'], item['content']
            _left, why = self.allowance(self.clock())
            if why == STOPPED_PAUSED:
                # The owner paused learning: nothing more is written for this run.
                stopped = STOPPED_PAUSED
                dropped.extend({'key_digest': key_digest(rest['memory_key']), 'reason': STOPPED_PAUSED}
                               for rest in kept[index:])
                break
            if memory_value_has_secret(self.store, key, content):
                # #918 review P1: a stored secret or a credential-shaped value never enters Memory; only a key digest is kept.
                dropped.append({'key_digest': key_digest(key), 'reason': SECRET_SHAPED_VALUE})
                continue
            if OwnerForget(self.store).is_forgotten(MEMORY_OWNER, key, content):
                # #794 phase 3: upkeep reprocesses a source Work; a value the owner forgot is not re-derived from it.
                dropped.append({'key_digest': key_digest(key), 'reason': FORGOTTEN_VALUE})
                continue
            row = {'memory_key': key, 'category': item['category'], 'kind': item['kind'],
                   'content_chars': len(content), 'supersedes_key': item['supersedes_key'] or None,
                   'valid_until': item['valid_until'] or None}
            ends = self.current_rows_by_short_id(MEMORY_OWNER, [end['id'] for end in item['invalidates']])
            if ends is None:
                # #220: an id that names no current profile row, or more than one, writes nothing.
                dropped.append({'key_digest': key_digest(key), 'reason': 'invalidates'})
                continue
            # #918 slice (a), owner decision 2026-09-30: the owner's own Judgment AI saves at once as
            # current Memory, attributed to the source Work (#794); the owner is told afterwards with
            # an undo (the Work's memory_saved notice) instead of a per-fact #597 judgment and an ask.
            # A write is not a model call, so the cap and the deadline do not stop it; the pause does.
            try:
                # Review: the owner notice is held in the same transaction as the row, so a save is never untold.
                memory = self.store.save_memory(key, content, MEMORY_OWNER, work_id=job['id'], notice=True,
                                                invalidates=[(full, end['reason'])
                                                             for full, end in zip(ends, item['invalidates'])],
                                                valid_until=item['valid_until'] or None)
            except ValueError:
                dropped.append({'key_digest': key_digest(key), 'reason': 'store-refused'})
                continue
            row.update(outcome=APPLIED_MEMORY, memory_id=memory['id'], superseded=bool(memory.get('supersedes')),
                       invalidated=len(ends), auto_saved=True)
            applied.append(row)
        detail.update(proposed=len(data.get('proposals') or []), applied=applied, dropped=dropped, stopped=stopped)
        note = data.get('situation')
        if note_situation is not None and isinstance(note, str) and note.strip() and stopped != STOPPED_PAUSED:
            # A write, not a call: only the pause holds it.  Evidence keeps the outcome and size, never the text.
            if self.allowance(self.clock())[1] == STOPPED_PAUSED:
                detail['situation'] = {'recorded': False, 'reason': STOPPED_PAUSED}
            else:
                result = note_situation(note) or {}
                detail['situation'] = {'recorded': bool(result.get('recorded')), 'reason': result.get('reason'),
                                       'chars': result.get('chars')}
        return STATE_DONE, calls, EVENT_RECORDED, detail

    # -- one self-review (#998) ---------------------------------------------
    def run_review(self, job, row, judgments, *, answer, profile, clock, cancelled=None):
        """Review one settled Work from its own records; returns ``(state, calls, event_status, detail)``.

        One ``structured`` judgment (``ConversationJudgments.self_review``)
        over the owner's request, the answer excerpt, the Work's outcome, the
        orchestrator's verdicts, the owner's follow-up (or the row's
        observation) and the current working notes, all redacted by
        ``judgments``.  A kept revision of the notes is saved at once as their
        next version attributed to the reviewed Work (#19; the owner's own AI
        writes and the owner is told with an exact undo, as #918 slice a); the
        blocker is kept in the event detail, redacted, and nowhere else.  The
        pause switch is read again before the write; a write is not a call.
        ``profile`` is the profile snapshot without the notes block.
        """
        from .agent_runtime import MEMORY_OWNER, SECRET_SHAPED_VALUE, memory_value_has_secret
        notes = WorkingNotes(self.store, self.clock)
        try:
            notes.migrate()
        except Exception:  # noqa: BLE001 - the move is retried at the next start; the review goes on
            pass
        current = notes.current()
        current_text = current['text'] if current else ''
        request = str(job.get('message') or '')
        reason = row.get('reason') or ''
        followups = self.followups(job['id'])
        followup = '\n'.join(followups) if followups else (row.get('observation') or '')
        evaluation = '\n'.join(self.evaluations(job['id']))
        awaiting = self.awaiting(job['id'])
        data, decision = judgments.self_review(request, answer, str(job.get('status') or ''), evaluation, followup,
                                               profile, reason, work_id=job['id'], cancelled=cancelled,
                                               working_notes=current_text, awaiting_owner=awaiting)
        confidence = decision.confidence
        calls = 1 if call_sent(decision) else 0
        self.count(job['id'], calls, KIND_SELF_REVIEW)
        detail = {'reason': reason, 'decision': decision.outcome, 'route': confidence.route or None,
                  'provider': confidence.provider or None, 'model': confidence.model or None,
                  'observed_model': confidence.observed_model or None, 'confidence': confidence.probability,
                  'sent': bool(calls),
                  'inputs': {'owner_request_chars': len(request), 'answer_chars': len(answer or ''),
                             'evaluations': len(self.evaluations(job['id'])), 'followups': len(followups),
                             'observation': bool(row.get('observation')), 'profile_chars': len(profile or ''),
                             'awaiting_owner': bool(awaiting),
                             'working_notes_version': current['version'] if current else None,
                             'working_notes_chars': len(current_text), 'clock': bool(clock)}}
        if data is None:
            return STATE_UNAVAILABLE, calls, EVENT_UNAVAILABLE, detail
        known, _pending = self.known_values(MEMORY_OWNER, job['id'])
        revision, blocker, dropped = validate_review(data, current['points'] if current else (), known, request=request)
        detail.update(notes=None, blocker=None, dropped=dropped, stopped=None)
        if blocker:
            # Evidence on the reviewed Work only: redacted like every fact, never filed anywhere.
            detail['blocker'] = judgments.redact(blocker)[:MAX_TEXT_CHARS]
        if revision:
            _left, why = self.allowance(self.clock())
            refused = None
            if why == STOPPED_PAUSED:
                detail['stopped'] = refused = STOPPED_PAUSED
            elif judgments.redact(current_text) != current_text:
                # The judgment read part of the document withheld: a whole revision would drop it.
                refused = 'notes-withheld'
            elif memory_value_has_secret(self.store, revision['text'], revision['change']):
                refused = SECRET_SHAPED_VALUE
            elif notes.forgotten(revision['text']):
                refused = FORGOTTEN_VALUE
            else:
                saved = notes.save(revision, job['id'], expected=current['id'] if current else None)
                if saved is None:
                    # The owner forgot or another revision replaced the document during the call.
                    refused = 'notes-changed'
                else:
                    detail['notes'] = {'outcome': APPLIED_NOTES, 'version': saved['version'], 'ref': saved['ref'],
                                       'previous_version': current['version'] if current else None,
                                       'points': len(revision['points']), 'chars': len(revision['text']),
                                       'auto_saved': True}
            if refused:
                dropped.append({'note': 'notes', 'reason': refused})
        return STATE_DONE, calls, EVENT_RECORDED, detail


class WorkingNotes:
    """#19: the owner's one working-notes document, its versions in the reserved procedure slot.

    Read by every brief (``block``) and revised only by the self-review
    (``Upkeep.run_review``) after Still's deterministic checks.  Forgetting a
    version and its undo are ``owner_forget``'s (``procedure:<id>``).
    """

    def __init__(self, store, clock=time.time):
        from .procedures import ProcedureLibrary
        self.store, self.clock = store, clock
        self.library = ProcedureLibrary(store, clock)
        # Its tables hold the receipts and the forget tombstones this document relies on.
        self.forget = OwnerForget(store, clock)

    def current(self, db=None):
        """``{'id', 'ref', 'version', 'text', 'points', 'change', 'saved', 'work_id'}`` of the current version, or None."""
        from .procedures import NOTES_SLOT, REF_PREFIX
        row = self.library.current(NOTES_SLOT, db)
        if row is None:
            return None
        return {'id': row['id'], 'ref': REF_PREFIX + row['id'], 'version': row['version'], 'text': row['source'],
                'points': notes_points(row['source']), 'change': str(row['stats'].get('change') or ''),
                'saved': row['saved'], 'work_id': row['work_id']}

    def block(self):
        """The document as one brief block ('' when there is none): a header with its version, ref and last change."""
        current = self.current()
        if current is None or not current['points']:
            return ''
        try:
            day = datetime.fromtimestamp(float(current['saved']), timezone.utc).date().isoformat()
        except (TypeError, ValueError, OverflowError, OSError):
            day = 'unknown-date'
        return (f"working_notes v{current['version']} (the assistant's own notes on how to work for this owner; "
                f"ref {current['ref']}, last change {day}: {current['change'] or 'none'}; when the owner does not "
                f"want the last change, forget_record forget {current['ref']} puts the previous notes back):\n"
                + notes_text(current['points']))

    def save(self, revision, work_id, *, expected=None):
        """Save a validated revision as the next version unless the current one is no longer ``expected``.

        When ``work_id`` is a Telegram Work, its one ``NOTICE_KIND`` notice is queued in the same
        transaction (review P1, as a direct Memory save holds its notice), so a saved revision is
        never left untold by a crash.  Returns ``{'id', 'ref', 'version', 'digest'}`` or None when
        the document changed meanwhile.
        """
        import uuid
        from .procedures import NOTES_SLOT, REF_PREFIX
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            current = self.library.current(NOTES_SLOT, db)
            if (current['id'] if current else None) != expected:
                return None
            row = self.library.commit(NOTES_SLOT, revision['text'], work_id=work_id,
                                      stats={'change': revision['change']}, db=db)
            saved = {'id': row['id'], 'ref': REF_PREFIX + row['id'], 'version': row['version'], 'digest': row['digest']}
            job = db.execute('SELECT channel,chat_id FROM jobs WHERE id=?', (work_id,)).fetchone() if work_id else None
            channel = str(job['channel'] or '') if job else ''
            if job and channel.startswith('telegram:') and isinstance(job['chat_id'], int):
                db.execute('INSERT OR IGNORE INTO telegram_notifications(id,job_id,chat_id,generation,kind,fingerprint,state,message_id,created) VALUES (?,?,?,?,?,?,?,?,?)',
                           (str(uuid.uuid4()), work_id, job['chat_id'], channel[len('telegram:'):], NOTICE_KIND,
                            json.dumps({'ref': saved['ref'], 'version': saved['version']}), 'queued', None, self.clock()))
        return saved

    def forgotten(self, text):
        """Whether the owner forgot a version with exactly this text: the host does not save it again.

        The forget leaves a content-free digest tombstone (``owner_forget``, review P2) that outlives
        the seven-day purge of the version itself; undoing the forget removes it.
        """
        from .agent_runtime import MEMORY_OWNER
        from .procedures import NOTES_SLOT, digest
        value = digest(text)
        with self.store.db() as db:
            tombstone = db.execute('SELECT 1 FROM forgotten_values WHERE owner_key=? AND digest=?',
                                   (self.store._memory_binding(MEMORY_OWNER), value)).fetchone()
        return tombstone is not None or self.library.forgotten_digest(NOTES_SLOT, value)

    def migrate(self):
        """Move the current ``profile.working.`` Memory rows into the first version, once; returns the record or None.

        Newest first, a row joins while the bounds hold (a row repeating a
        point joins that point); a row with a stored secret or credential
        shape, an over-long value or one past the bounds stays in Memory
        untouched.  Each moved row leaves current use under its own undoable
        ``migrate`` receipt (``owner_forget``), in the same transaction as the
        version and the config record, so the move is all or nothing; an
        undo puts the row back in Memory.  Runs once: the record stays even
        when there was nothing to move.
        """
        from .agent_runtime import MEMORY_OWNER, memory_value_has_secret
        from .owner_forget import MEMORY_PREFIX, MIGRATE
        from .procedures import NOTES_SLOT
        if self.store.config(MIGRATION_KEY):
            return None
        forget = self.forget
        owner_key = self.store._memory_binding(MEMORY_OWNER)
        select = ("SELECT id,memory_key,content FROM memories WHERE owner_key=? AND state='current' "
                  'AND substr(memory_key,1,?)=? ORDER BY created DESC,id DESC')
        args = (owner_key, len(LESSON_PREFIX), LESSON_PREFIX)
        with self.store.db() as db:
            rows = [dict(row) for row in db.execute(select, args)]
        points, seen, moved, left = [], set(), [], 0
        for row in rows:
            point = ' '.join(str(row['content'] or '').split())
            if (not lesson_key(row['memory_key']) or not point or len(point) > POINT_CHARS
                    or memory_value_has_secret(self.store, row['memory_key'], row['content'])):
                left += 1
                continue
            if normalized(point) not in seen:
                if len(points) >= NOTES_POINTS or len(notes_text([*points, point])) > NOTES_CHARS:
                    left += 1
                    continue
                seen.add(normalized(point))
                points.append(point)
            moved.append(row)
        points.reverse()
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if self.store.config(MIGRATION_KEY, db=db):
                return None
            if [dict(row) for row in db.execute(select, args)] != rows:
                # A row changed since the plan: nothing is moved now; the next start plans again.
                return None
            version = None
            if points and self.library.current(NOTES_SLOT, db) is None:
                version = self.library.commit(NOTES_SLOT, notes_text(points), stats={'change': MIGRATION_CHANGE}, db=db)
            elif points:
                moved, left = [], len(rows)
            receipts = [forget.forget_in(db, MEMORY_OWNER, MEMORY_PREFIX + row['id'], operation=MIGRATE)['receipt']
                        for row in moved]
            record = {'at': self.clock(), 'moved': len(moved), 'left': left, 'points': len(points) if version else 0,
                      'version': version['version'] if version else None, 'receipts': receipts}
            db.execute('INSERT INTO config(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                       (MIGRATION_KEY, json.dumps(record)))
        return record


def enqueue_self_review(store, work_id, reason, observation=None, *, db=None, now=None, clock=time.time):
    """One pending self-review of ``work_id`` (#998); returns whether a row was added.

    The API another trigger uses (for example an owner reaction, #996):
    ``reason`` is one of ``REVIEW_REASONS``; ``observation`` is an optional
    short typed note the judgment reads as the owner's follow-up when no
    later message is linked to the Work.  Idempotent per Work: a second
    self-review of the same Work is not added.  Nothing runs here; the
    existing idle tick claims the row under the owner's pause switch and
    the rolling call cap.  With ``db`` it joins the caller's transaction.
    """
    if reason not in REVIEW_REASONS:
        raise ValueError('self-review reason')
    if not isinstance(work_id, str) or not work_id:
        raise ValueError('self-review work')
    return Upkeep(store, clock).enqueue(db, work_id, now, kind=KIND_SELF_REVIEW, reason=reason, observation=observation)
