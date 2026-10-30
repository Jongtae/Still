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
previous document back, undoable at any time (#367: a forget is never purged).  Earlier
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

from .memory_service import _PROFILE_ROWS_CAP, PROFILE_SECTION_CHARS, MemoryService
from .owner_forget import OwnerForget
from .prompt_files import prompt

#: The source Work's tool-event name of every upkeep run (#794 shows it).
EVENT_TOOL = 'owner_model'
#: The DecisionContext purpose of the proposal judgment.
PURPOSE = 'owner-model-upkeep'
#: #998: the two kinds of run one row may be; the row key is ``(job_id, kind)``.
KIND_UPKEEP, KIND_SELF_REVIEW = 'upkeep', 'self-review'
#: #402: the once-a-day look back over recent requests (the "dream"); its row's Work is the newest
#: request it covers (the anchor: its Evidence, notice and Memory provenance).
KIND_DREAM = 'dream'
RUN_KINDS = (KIND_UPKEEP, KIND_SELF_REVIEW, KIND_DREAM)
#: The reviewed Work's tool-event name of every self-review run.
REVIEW_EVENT_TOOL = 'self_review'
#: The DecisionContext purpose of the self-review judgment.
REVIEW_PURPOSE = 'self-review'
#: #402: the anchor Work's tool-event name of every dream run, and its judgment's purpose.
DREAM_EVENT_TOOL = 'owner_model_dream'
DREAM_PURPOSE = 'owner-model-dream'
#: #402: when a dream is due.  At least ``DREAM_INTERVAL_SECONDS`` after the last one and
#: ``DREAM_MIN_WORKS`` upkeep-eligible requests since it, with nothing pending and no Work
#: running; inside the owner's night hours, or anywhere once ``DREAM_LATE_SECONDS`` have passed.
DREAM_INTERVAL_SECONDS = 20 * 3600
DREAM_LATE_SECONDS = 36 * 3600
DREAM_MIN_WORKS = 3
DREAM_NIGHT_HOURS = range(1, 6)
#: The service tick asks whether a dream is due at most this often.
DREAM_CHECK_SECONDS = 60
#: What one dream reads: requests since the last dream, at most this far back and this many
#: (the newest kept), each side bounded, and the whole bounded again by the host.
DREAM_WINDOW_SECONDS = 7 * 24 * 3600
DREAM_TURNS = 40
DREAM_OWNER_CHARS = 600
DREAM_ANSWER_CHARS = 300
DREAM_TURNS_CHARS = 12000
#: #420: the dream's one judgment runs on the engine's strongest model and may take this long
#: (inside the run's own ``RUN_SECONDS`` deadline).
DREAM_TIMEOUT_SECONDS = 240
#: #416: a dream is two judgments - a first pass that only finds facts in the turns, then the look back
#: that tidies what is remembered, the notes and the daily record - so no call starts after this.
DREAM_RUN_SECONDS = 600
DREAM_GATHER_PURPOSE = 'owner-model-dream-gather'
#: How many facts the first pass may keep: it reads up to a day of requests.
DREAM_GATHER_LIMIT = 10
#: #402: one dream may end at most this share of the current profile rows (at least one);
#: more is refused whole, the OpenClaw ``maxPriorEntryLossFraction`` lesson.
DREAM_END_FRACTION = 0.25
#: #402: the daily record.  A Memory row outside ``profile.``, so no brief reads it unless a
#: search finds it; one per owner date, in the owner's language.
DIARY_PREFIX = 'diary.'
DIARY_CHARS = 800
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
#: #233: how many times a self-review plans the move again when the rows changed under its plan.
MIGRATION_ATTEMPTS = 3
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

#: #398: ``plan`` holds what the owner says about their own plans - a goal, an upcoming need with its
#: amount and date, an obligation they carry - so a later request can be weighed against it.
CATEGORIES = ('identity', 'place', 'routine', 'schedule', 'preference', 'plan')
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
#: #220 (Codex review): plus room for the short id the upkeep's snapshot puts on each row (``[12345678] ``).
PROFILE_CHARS = PROFILE_SECTION_CHARS + _PROFILE_ROWS_CAP * (MemoryService.SHORT_ID_CHARS + 3)
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
#: #233: a self-review that asked nothing because the one-time move of the earlier lessons is not done yet.
STOPPED_MIGRATION = 'notes-migration-pending'


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

#: #240: the two states of one typed "awaiting the owner" item.
AWAITING_WAITING, AWAITING_SETTLED = 'waiting', 'settled'
#: #240: how much of the rendered items the plan call and the goal judgment read.
AWAITING_OWNER_CHARS = 600


def owner_decision_record(detail):
    """The record in one recorded tool result that waits for the owner's own decision, or None (#170, #240).

    The one per-record rule: the typed flags a result carries while its effect waits for the owner
    (``telegram_presence.OWNER_DECISION_FLAGS``, on its ``evidence`` when that carries one, else on
    the result itself), unless ``applied`` is true.  A write the policy refused and kept as a
    candidate (``refused_because``) waits for nothing.  Never the result's wording.
    """
    from .telegram_presence import OWNER_DECISION_FLAGS
    if not isinstance(detail, dict):
        return None
    evidence = detail.get('evidence') if isinstance(detail.get('evidence'), dict) else {}
    record = evidence if any(evidence.get(flag) for flag in OWNER_DECISION_FLAGS) else detail
    if (record.get('refused_because') or record.get('applied') is True
            or not any(record.get(flag) for flag in OWNER_DECISION_FLAGS)):
        return None
    return record


def awaiting_owner_items(rows, settled=None):
    """The typed "awaiting the owner" items of recorded ``(tool, status, detail)`` rows (#240).

    ``[{'kind', 'shown', 'settled', 'state'}]``, one per host action, in first-seen order: how many
    results of that kind were shown for the owner's own approval (``owner_decision_record``) and
    how many of them a later recorded event settled (``settled(draft_id)``, when given).  ``state``
    is ``waiting`` while any is unsettled.  Names and counts only, never content.  ``detail`` may
    be a dict or its JSON text; only finished calls (``succeeded`` / ``failed``) count.
    """
    counts = {}
    for tool, status, detail in rows or ():
        if status not in ('succeeded', 'failed'):
            continue
        if not isinstance(detail, dict):
            try:
                detail = json.loads(detail or '{}')
            except (TypeError, ValueError):
                continue
        record = owner_decision_record(detail)
        if record is None:
            continue
        ident = record.get('draft_id')
        done = bool(settled and isinstance(ident, str) and ident and settled(ident))
        shown = counts.setdefault(detail.get('host_action') or tool, [0, 0])
        shown[0] += 1
        shown[1] += 1 if done else 0
    return [{'kind': kind, 'shown': total, 'settled': done,
             'state': AWAITING_WAITING if total > done else AWAITING_SETTLED}
            for kind, (total, done) in counts.items()]


def work_awaiting_owner(store, job_id):
    """``awaiting_owner_items`` of ``job_id``'s recorded tool events; an item is settled by a later
    successful event of another Work that names its ``draft_id`` (#170, #240)."""
    with store.db() as db:
        rows = db.execute('SELECT tool,status,detail FROM tool_events WHERE job_id=? AND status IN (?,?) ORDER BY id',
                          (job_id, 'succeeded', 'failed')).fetchall()

        def settled(ident):
            return db.execute("SELECT 1 FROM tool_events WHERE job_id!=? AND status='succeeded' AND detail LIKE ? LIMIT 1",
                              (job_id, '%' + json.dumps(ident)[1:-1] + '%')).fetchone() is not None
        return awaiting_owner_items([(row['tool'], row['status'], row['detail']) for row in rows], settled)


def awaiting_owner_text(items):
    """The one rendering of typed awaiting items the judgments read (#170, #240); '' when none."""
    return '; '.join(f"{item['kind']}: {item['shown']} shown for the owner's own approval, "
                     f"{item['settled']} later settled by the owner" for item in items or ())

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
REVIEW_QUESTION += ('\n\nThe shared Voice instructions below apply to change, which is shown to the owner. '
                    'The internal notes and blocker keep their formats above.\n\n' + prompt('voice'))

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


DREAM_QUESTION = prompt('owner_model_dream').format(categories=', '.join(CATEGORIES), notes_points=NOTES_POINTS,
                                                    point_chars=POINT_CHARS, notes_chars=NOTES_CHARS,
                                                    diary_chars=DIARY_CHARS)
DREAM_QUESTION += ('\n\nThe shared Voice instructions below apply to diary and change, which the owner may read. '
                   'They never relax the rules above.\n\n' + prompt('voice'))

DREAM_GATHER_QUESTION = prompt('owner_model_dream_gather').format(categories=', '.join(CATEGORIES),
                                                                  gather_limit=DREAM_GATHER_LIMIT)

#: #424: why a dream ends a line.  ``restates``: the proposal says the same thing as that line (a tidy-up).
#: ``replaced``: an owner turn the proposal cites (``turn``) replaced or ended it.  ``unsure``: the judgment
#: doubts the line still holds but cannot ground the ending; it is held, not applied.  ``not_lasting``
#: (#429): the line itself was never a lasting fact (a request sentence, a passing reaction, one task's
#: condition or a current state kept as lasting).
END_RESTATES, END_REPLACED, END_UNSURE, END_NOT_LASTING = 'restates', 'replaced', 'unsure', 'not_lasting'
END_BECAUSE = (END_RESTATES, END_REPLACED, END_NOT_LASTING, END_UNSURE)

_DREAM_ITEM = json.loads(json.dumps(SCHEMA['properties']['proposals']['items']))
_DREAM_ITEM['properties']['sources'] = {'type': 'array', 'items': {'type': 'string'}}
_DREAM_ITEM['required'] = [*_DREAM_ITEM['required'], 'sources']
_DREAM_END = _DREAM_ITEM['properties']['invalidates']['items']
_DREAM_END['properties'].update(because={'type': 'string', 'enum': list(END_BECAUSE)}, turn={'type': 'string'})
_DREAM_END['required'] = [*_DREAM_END['required'], 'because', 'turn']
DREAM_SCHEMA = {'type': 'object', 'additionalProperties': False,
                'properties': {'proposals': {'type': 'array', 'items': _DREAM_ITEM},
                               'notes': {'type': 'string'}, 'change': {'type': 'string'},
                               'notes_sources': {'type': 'array', 'items': {'type': 'string'}},
                               'diary': {'type': 'string'}},
                'required': ['proposals', 'notes', 'change', 'notes_sources', 'diary']}


DREAM_GATHER_SCHEMA = {'type': 'object', 'additionalProperties': False,
                       'properties': {'proposals': {'type': 'array', 'items': _DREAM_ITEM}}, 'required': ['proposals']}


def gather_shape(data):
    """Types only; what may be kept is ``validate_dream``'s."""
    return isinstance(data.get('proposals'), list)


def dream_shape(data):
    """Types only; what may be kept is ``validate_dream``'s and the run's."""
    return (isinstance(data.get('proposals'), list) and isinstance(data.get('notes_sources'), list)
            and all(isinstance(data.get(name), str) for name in ('notes', 'change', 'diary')))


def dream_refs(sources, refs):
    """The distinct refs of ``sources`` that name a turn the dream read, or None when any names none (#402)."""
    if not isinstance(sources, list) or any(not isinstance(ref, str) for ref in sources):
        return None
    named = list(dict.fromkeys(ref.strip().strip('[]') for ref in sources if ref.strip()))
    return named if all(ref in refs for ref in named) else None


def validate_dream(proposals, refs, requests, known, pending, profile_rows, profile_ids=None, limit=MAX_PROPOSALS):
    """``(kept, dropped)`` of a dream's proposals (#402, deterministic).

    Each proposal must name what it rests on (``sources``): refs of turns the dream read, or, to tidy
    what is remembered, the short ids of current profile lines (``profile_ids``: short id -> key).  A
    proposal resting on no turn only restates or ends the lines it names - each named line is in its
    ``invalidates`` or is the row under its own key - so no new fact comes from the profile alone.
    An ``inferred`` fact drawn from turns needs two separate turns: a pattern is never drawn from one
    occasion.  A proposal that is one of the turns' request sentences is the request, not a fact.  The
    rest is ``validate``'s.  Naming the line under the proposal's own key in ``invalidates`` is no
    ending (saving the key replaces it) and is left out.  Every other ending must be grounded (#424):
    the line is among the proposal's own ``sources`` and the proposal restates it, a turn the proposal
    cites replaced it, or it was never a lasting fact (``because``/``turn``); an ending that is not, or that the judgment marks
    ``unsure``, is dropped (``held`` for the unsure one) while the fact itself stays.  Still checks
    citation only: what counts as the same fact or a replacement is the AI's (C16).  When the kept
    proposals together would end more than ``DREAM_END_FRACTION`` of the ``profile_rows`` current
    profile rows (at least one), every proposal that ends a line is refused and pure additions stay: a
    look back that removes much of what is known is not trusted to do it at once.
    """
    proposals = proposals if isinstance(proposals, list) else []
    profile_ids = profile_ids or {}
    cited, dropped = [], []
    request_texts = {normalized(text) for text in requests if normalized(text)}
    for item in proposals:
        named = dream_refs(item.get('sources') if isinstance(item, dict) else None, {**refs, **profile_ids})
        key = item.get('memory_key') if isinstance(item, dict) and isinstance(item.get('memory_key'), str) else None
        turns = [ref for ref in named or () if ref in refs]
        lines = [ref for ref in named or () if ref in profile_ids]
        ends = {str(end.get('id') or '').strip() for end in (item.get('invalidates') or ())
                if isinstance(end, dict)} if isinstance(item, dict) and isinstance(item.get('invalidates'), list) else set()
        reason = None
        if not named:
            reason = 'sources'
        elif not turns and not all(line in ends or profile_ids[line] == key for line in lines):
            reason = 'sources'
        elif turns and item.get('kind') == KIND_INFERRED and len(turns) < 2:
            reason = 'one-occasion'
        elif normalized(item.get('content')) in request_texts:
            reason = 'request'
        if reason:
            dropped.append({'key_digest': key_digest(key), 'reason': reason} if key else {'reason': reason})
        else:
            # The line under the proposal's own key is replaced by saving it, not ended: it is no loss.
            own = [end for end in item.get('invalidates') or ()
                   if not (isinstance(end, dict) and profile_ids.get(str(end.get('id') or '').strip()) == key)]
            cited.append({**item, 'invalidates': grounded_ends(own, key, turns, lines, dropped)})
    kept, refused = validate(cited, known, pending, limit=limit)
    dropped.extend(refused)
    if sum(len(item['invalidates']) for item in kept) > max(1, int(DREAM_END_FRACTION * max(0, profile_rows))):
        # Too much ended at once: every proposal that ends a line is refused; a pure addition stays.
        dropped.extend({'key_digest': key_digest(item['memory_key']), 'reason': 'mass-end'}
                       for item in kept if item['invalidates'])
        kept = [item for item in kept if not item['invalidates']]
    return kept, dropped


def grounded_ends(ends, key, turns, lines, dropped):
    """The ``ends`` of one dream proposal that are grounded (#424); the others go to ``dropped``.

    An ending stands only when its line is cited in the proposal's ``sources`` (``lines``) and the
    proposal restates that line, cites the owner turn (``turns``) that replaced it, or says the line was
    never a lasting fact (#429).  A
    malformed entry is kept for ``validate``, which refuses the whole proposal's shape.  A dropped
    ending names the line's short id (never its text): ``held`` when the judgment was unsure, so a
    later step can raise it with the owner; ``end-uncited`` or ``end-ungrounded`` otherwise.
    """
    kept = []
    for end in ends:
        if not isinstance(end, dict) or not isinstance(end.get('id'), str):
            kept.append(end)
            continue
        line = end['id'].strip()
        because, turn = end.get('because'), str(end.get('turn') or '').strip().strip('[]')
        if because == END_UNSURE:
            reason = 'held'
        elif line not in lines:
            reason = 'end-uncited'
        elif because in (END_RESTATES, END_NOT_LASTING) or (because == END_REPLACED and turn in turns):
            kept.append(end)
            continue
        else:
            reason = 'end-ungrounded'
        dropped.append({'key_digest': key_digest(key), 'reason': reason, 'line': line})
    return kept


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


def validate(proposals, known, pending, noted_keys=(), request='', limit=MAX_PROPOSALS):
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
        reason = 'over-limit' if index >= limit else _refusal(item)
        key = item.get('memory_key') if isinstance(item, dict) and isinstance(item.get('memory_key'), str) else None
        if reason is None:
            content = normalized(item['content'])
            if request_text and content == request_text:
                reason = 'request'
            elif (key in keys or content in contents or (key, content) in pending
                    # Codex review on #226: a key the Work already wrote may still get a last day (the worker's
                    # save_memory cannot give one); any other proposal under it stays one ask per message (#836).
                    # An explicit same-key replacement with an empty last day clears an inherited one.
                    or (key in noted_keys and not (content in known and (
                        item.get('valid_until') or item.get('invalidates')
                        or (item.get('supersedes_key') or '').strip() == key)))
                    or (content in known and not item.get('valid_until') and not item.get('invalidates')
                        and (item.get('supersedes_key') or '').strip() != key)):
                # Codex review on #226: the same words with a new last day or ending other rows are a lifecycle
                # update, not a duplicate.
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
                     'valid_until': (item.get('valid_until') or '').strip(),
                     # Same words as a current value: kept only as a lifecycle update (see ``Upkeep.run``).
                     'lifecycle': content in known})
    return kept, dropped


class Upkeep:
    """The durable upkeep queue and the owner's controls, in the owner's QuickStore."""

    def __init__(self, store, clock=time.time):
        self.store, self.clock = store, clock
        #: #402: when the tick last asked whether a dream is due (in memory: a restart asks again).
        self.dream_checked = None

    # -- controls -----------------------------------------------------------
    @staticmethod
    def _controls(value):
        value = value if isinstance(value, dict) else {}
        cap = value.get('daily_calls', DEFAULT_DAILY_CALLS)
        if isinstance(cap, bool) or not isinstance(cap, int):
            cap = DEFAULT_DAILY_CALLS
        return {'enabled': value.get('enabled', True) is not False, 'daily_calls': max(0, min(MAX_DAILY_CALLS, cap)),
                # #402: the once-a-day look back has its own switch; the pause above stops it too.
                'dream': value.get('dream', True) is not False}

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
        if 'dream' in body:
            if not isinstance(body['dream'], bool):
                raise ValueError('밤사이 기억 정리 사용 여부를 확인하세요.')
            current['dream'] = body['dream']
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

    # -- the dream (#402) ----------------------------------------------------
    def last_dream(self, db):
        """When the last dream was enqueued, or None (every state counts: one look back a day, whatever came of it)."""
        row = db.execute('SELECT MAX(created) FROM owner_model_upkeep WHERE kind=?', (KIND_DREAM,)).fetchone()
        return float(row[0]) if row and row[0] is not None else None

    def enqueue_dream_if_due(self, now, local_hour):
        """Enqueue one dream when it is due (see ``DREAM_*``); returns its anchor Work id or None.

        Asked by the service tick at most every ``DREAM_CHECK_SECONDS``.  Due needs the owner's
        upkeep and dream switches on, call budget left, no upkeep row pending or in flight and no
        Work queued or running, ``DREAM_INTERVAL_SECONDS`` since the last dream, and
        ``DREAM_MIN_WORKS`` upkeep-eligible requests (their upkeep rows) since it; then the owner's
        night hours, or ``DREAM_LATE_SECONDS`` since the last.  The anchor is the newest of those
        requests made on a conversation surface (so its notice can reach the owner), else the newest.
        ``reason`` holds the start of the period the dream reads.
        """
        if self.dream_checked is not None and now - self.dream_checked < DREAM_CHECK_SECONDS:
            return None
        self.dream_checked = now
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            settings = self.settings(db)
            if not settings['enabled'] or not settings['dream']:
                return None
            if settings['daily_calls'] - self.calls_used(now, db) < 1:
                return None
            if db.execute('SELECT 1 FROM owner_model_upkeep WHERE state IN (?,?) LIMIT 1',
                          (STATE_PENDING, STATE_CLAIMED)).fetchone():
                return None
            if db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running') LIMIT 1").fetchone():
                return None
            last = self.last_dream(db)
            if last is not None and now - last < DREAM_INTERVAL_SECONDS:
                return None
            if local_hour not in DREAM_NIGHT_HOURS and last is not None and now - last < DREAM_LATE_SECONDS:
                return None
            since = max(last or 0.0, now - DREAM_WINDOW_SECONDS)
            rows = db.execute('SELECT u.job_id,j.channel,j.chat_id FROM owner_model_upkeep u LEFT JOIN jobs j ON j.id=u.job_id '
                              'WHERE u.kind=? AND u.created>? ORDER BY u.created DESC', (KIND_UPKEEP, since)).fetchall()
            if len(rows) < DREAM_MIN_WORKS:
                return None
            # The newest request from a conversation surface, so the owner can be told there; else the newest.
            from .conversation_surface import surface_conversation
            anchor = next((row['job_id'] for row in rows if row['channel'] and surface_conversation(row['channel'], row['chat_id'])),
                          rows[0]['job_id'])
            inserted = db.execute('INSERT OR IGNORE INTO owner_model_upkeep(job_id,kind,state,created,reason) '
                                  'VALUES (?,?,?,?,?)', (anchor, KIND_DREAM, STATE_PENDING, now, repr(float(since))))
            return anchor if inserted.rowcount == 1 else None

    def dream_works(self, since, until, limit=DREAM_TURNS):
        """The settled requests a dream reads, oldest first: ``{'id', 'message', 'response', 'created', 'request_key',
        'provider'}``, the newest ``limit`` of those created in ``(since, until]``.  A request or answer the owner
        forgot is withheld (#367); the caller drops what upkeep would not read (typed markers).  Only Works whose
        text is the owner's own words (#423, ``OWNER_WORDS_SQL``) are read."""
        from .quickstart_store import OWNER_WORDS_SQL, forgotten_turn
        with self.store.db() as db:
            rows = db.execute(
                f"SELECT j.id,j.request_key,j.provider,j.created,"
                f"CASE WHEN {forgotten_turn('user', 'j.id')} THEN '' ELSE j.message END AS message,"
                f"CASE WHEN {forgotten_turn('assistant', 'j.id')} THEN '' ELSE j.response END AS response "
                f"FROM jobs j WHERE j.created>? AND j.created<=? AND j.status IN ('succeeded','partial','failed') "
                f"AND {OWNER_WORDS_SQL} ORDER BY j.created DESC LIMIT ?", (since, until, limit)).fetchall()
        return [dict(row) for row in reversed(rows)]

    def _notice_taken(self, job_id, kind):
        """Whether ``job_id`` already has its one notice of ``kind`` (a Work has at most one per kind)."""
        with self.store.db() as db:
            return db.execute('SELECT 1 FROM telegram_notifications WHERE job_id=? AND kind=? LIMIT 1',
                              (job_id, kind)).fetchone() is not None

    def diary_previous(self, owner_id):
        """The newest current daily record (``DIARY_PREFIX``) or None."""
        rows = self.store.memories(owner_id, limit=1, key_prefix=DIARY_PREFIX)
        return rows[0] if rows else None

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
        tool = {KIND_SELF_REVIEW: REVIEW_EVENT_TOOL, KIND_DREAM: DREAM_EVENT_TOOL}.get(kind, EVENT_TOOL)
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
        from .quickstart_store import forgotten_turn
        with self.store.db() as db:
            # #367: a follow-up the owner forgot is not reviewed.
            rows = db.execute(f"SELECT message FROM jobs WHERE related_job_id=? AND relation_kind IN (?,?) AND id!=? "
                              f"AND NOT {forgotten_turn('user')} "
                              'ORDER BY created LIMIT ?', (job_id, REVIEW_CORRECTION, REVIEW_RETRY, job_id, limit)).fetchall()
        return [str(row['message'] or '') for row in rows if row['message']]

    def awaiting(self, job_id):
        """What ``job_id`` left waiting for the owner's own decision, and what became of it (#170); '' when nothing.

        Read from recorded tool events only: each item a tool result marked as waiting for the owner
        (``telegram_presence.OWNER_DECISION_FLAGS``, not applied, not a refused write) and, by its
        ``draft_id``, whether a later recorded event settled it.  Names and counts only, no content.
        #240: the typed items (``work_awaiting_owner``) the plan call and the goal judgment read too.
        """
        return awaiting_owner_text(work_awaiting_owner(self.store, job_id))

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
        from .memory_service import MemoryService
        # #220 (Codex review): a fact past its last day no longer holds, so saying it again is not a duplicate.
        today = MemoryService(self.store, private_read_sink=MemoryService.NO_EGRESS_GUARD)._today()
        known, offset = set(), 0
        while True:
            page = self.store.memories(owner, limit=101, offset=offset, key_prefix=PROFILE_PREFIX, holding_on=today)
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

    def current_rows_by_short_id(self, owner_id, short_ids, job=None):
        """The full ids of the current ``profile.`` rows the short ids name, in order; None if any names none or several (#220).

        With ``job`` (the reviewed Work), a row saved after that Work arrived - by a later request -
        is refused unless the Work wrote it itself (Codex review: newest-first upkeep must never
        let an older request end a newer fact).
        """
        if not short_ids:
            return []
        owner_key = self.store._memory_binding(owner_id)
        since = float(job['created']) if job and job.get('created') is not None else None
        work_key = self.store._work_binding(job['id']) if job else None
        found = []
        with self.store.db() as db:
            for short in short_ids:
                rows = db.execute("SELECT id FROM memories WHERE owner_key=? AND state='current' AND memory_key LIKE 'profile.%' "
                                  "AND memory_key NOT LIKE ? AND substr(id,1,?)=? "
                                  "AND (? IS NULL OR created<=? OR work_key=?)",
                                  (owner_key, LESSON_PREFIX + '%', len(short), short, since, since, work_key)).fetchall()
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
        from .agent_runtime import MEMORY_OWNER
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
        applied, stopped = self._apply_facts(kept, job, dropped)
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

    def _apply_facts(self, kept, job, dropped, ends_after=None):
        """Save the kept proposals at once as current Memory attributed to ``job``; returns ``(applied, stopped)``.

        Shared by upkeep and the dream (#402).  ``dropped`` is extended in place.  ``ends_after``
        (the dream's claim time) widens which current rows a proposal may end: any row that existed
        then, instead of the rows that existed when ``job`` arrived.
        """
        from .agent_runtime import MEMORY_OWNER, SECRET_SHAPED_VALUE, memory_value_has_secret
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
            if item.get('lifecycle') and not item['invalidates']:
                # A new last day (or none: Codex review, a dated fact becoming lasting) for words already
                # current applies to that key's own row, never a second copy, and only when it changes it.
                same = self.store.current_memory(key, MEMORY_OWNER)
                if (not same or normalized(same['content']) != normalized(content)
                        or (same.get('valid_until') or '') == item['valid_until']):
                    dropped.append({'key_digest': key_digest(key), 'reason': 'duplicate'})
                    continue
            ends = self.current_rows_by_short_id(MEMORY_OWNER, [end['id'] for end in item['invalidates']],
                                                job=job if ends_after is None else {**job, 'created': ends_after})
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
                                                # '' is the AI saying the fact has no last day.
                                                valid_until=item['valid_until'])
            except ValueError:
                dropped.append({'key_digest': key_digest(key), 'reason': 'store-refused'})
                continue
            row.update(outcome=APPLIED_MEMORY, memory_id=memory['id'], superseded=bool(memory.get('supersedes')),
                       invalidated=len(ends), auto_saved=True)
            applied.append(row)
        return applied, stopped

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
        ``profile`` is the profile snapshot without the notes block.  While the
        one-time move of the earlier lessons is not done (``WorkingNotes.migrated``)
        nothing is asked and the run is ``unavailable`` (#233).
        """
        from .agent_runtime import MEMORY_OWNER
        notes = WorkingNotes(self.store, self.clock)
        reason = row.get('reason') or ''
        if not notes.migrated():
            # #233 (Codex review): a version saved before the one-time move would make the move's retry
            # leave every earlier lesson in Memory, so no revision is asked for until the move is done.
            # Nothing was sent and nothing is written; the move is retried here and at the next start.
            return STATE_UNAVAILABLE, 0, EVENT_UNAVAILABLE, {'reason': reason, 'sent': False,
                                                             'stopped': STOPPED_MIGRATION}
        current = notes.current()
        current_text = current['text'] if current else ''
        request = str(job.get('message') or '')
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
            self._save_notes(notes, current, revision, job['id'], judgments, dropped, detail)
        return STATE_DONE, calls, EVENT_RECORDED, detail

    def _save_notes(self, notes, current, revision, work_id, judgments, dropped, detail):
        """Save one validated working-notes revision attributed to ``work_id``, or record why not (#19; #402 shares it).

        Sets ``detail['notes']`` when kept and ``detail['stopped']`` when the owner paused; extends ``dropped``.
        """
        from .agent_runtime import SECRET_SHAPED_VALUE, memory_value_has_secret
        current_text = current['text'] if current else ''
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
            saved = notes.save(revision, work_id, expected=current['id'] if current else None)
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


    # -- one dream (#402) ---------------------------------------------------
    def run_dream(self, job, row, judgments, *, turns, profile, clock, diary_date, ends_after, cancelled=None):
        """Look back over ``turns`` once and keep the owner model in order; returns ``(state, calls, event_status, detail)``.

        ``job`` is the anchor Work (the newest request the dream covers): what is kept is attributed to
        it and its notice tells the owner, with an undo, as upkeep's saves do (#918 slice a).  ``turns``
        are ``(ref, owner words, answer)`` triples the caller scrubbed, oldest first; ``profile`` the
        snapshot with short ids and no notes.  One ``structured`` judgment
        (``ConversationJudgments.owner_model_dream``), then Still's deterministic checks:
        ``validate_dream`` for facts, ``validate_review`` and two separate cited turns for the working
        notes, the size bound, a secret shape and the pause for the daily record (``diary_date``).
        Facts may end any profile row that existed at ``ends_after`` (the claim).
        """
        from .agent_runtime import MEMORY_OWNER, SECRET_SHAPED_VALUE, memory_value_has_secret
        notes = WorkingNotes(self.store, self.clock)
        if not notes.migrated():
            return STATE_UNAVAILABLE, 0, EVENT_UNAVAILABLE, {'sent': False, 'stopped': STOPPED_MIGRATION}
        refs = {ref: owner for ref, owner, _answer in turns}
        lines, used = [], 0
        for ref, owner, answer in turns:
            line = (f"[{ref}] owner: {' '.join(str(owner or '').split())[:DREAM_OWNER_CHARS] or '(withheld)'}"
                    f" | assistant: {' '.join(str(answer or '').split())[:DREAM_ANSWER_CHARS] or 'none'}")
            lines.append(line)
            used += len(line) + 1
        while lines and used > DREAM_TURNS_CHARS:
            # The oldest turns give way: the newest are what upkeep had the least time for.
            used -= len(lines.pop(0)) + 1
        shown = {line[1:line.index(']')] for line in lines}
        refs = {ref: owner for ref, owner in refs.items() if ref in shown}
        snapshot = profile if callable(profile) else (lambda: profile)
        today = MemoryService(self.store, now=self.clock, private_read_sink=MemoryService.NO_EGRESS_GUARD)._today()

        def lines_now():
            rows = self.store.memories(MEMORY_OWNER, limit=101, key_prefix=PROFILE_PREFIX, holding_on=today)
            return rows, {row['id'][:MemoryService.SHORT_ID_CHARS]: row['memory_key'] for row in rows
                          if not row['memory_key'].startswith(LESSON_PREFIX)}

        # #416: the first pass only finds facts in the turns; what it keeps is in the profile the look back reads.
        found, gather = judgments.owner_model_dream_gather('\n'.join(lines), snapshot(), clock, work_id=job['id'],
                                                           cancelled=cancelled)
        calls = 1 if call_sent(gather) else 0
        self.count(job['id'], calls, KIND_DREAM)
        first = {'decision': gather.outcome, 'model': gather.confidence.model or None,
                 'observed_model': gather.confidence.observed_model or None}
        gathered, stopped = [], None
        if found is not None:
            known, pending = self.known_values(MEMORY_OWNER, job['id'])
            rows, ids = lines_now()
            kept, refused = validate_dream(found.get('proposals'), refs, refs.values(), known, pending, len(rows), ids,
                                           limit=DREAM_GATHER_LIMIT)
            gathered, stopped = self._apply_facts([{**item, 'invalidates': []} for item in kept], job, refused,
                                                  ends_after=ends_after)
            first.update(proposed=len(found.get('proposals') or []), applied=gathered, dropped=refused)
        current = notes.current()
        current_text = current['text'] if current else ''
        previous = self.diary_previous(MEMORY_OWNER)
        if stopped == STOPPED_PAUSED or (cancelled and cancelled()):
            return STATE_DONE, calls, EVENT_RECORDED, {'gather': first, 'sent': bool(calls), 'applied': gathered,
                                                       'dropped': [], 'notes': None, 'diary': None,
                                                       'stopped': stopped or STOPPED_DEADLINE,
                                                       'inputs': {'turns': len(lines)}}
        data, decision = judgments.owner_model_dream('\n'.join(lines), snapshot(), current_text,
                                                     previous['content'] if previous else '', clock,
                                                     work_id=job['id'], cancelled=cancelled)
        confidence = decision.confidence
        calls += 1 if call_sent(decision) else 0
        self.count(job['id'], calls, KIND_DREAM)
        detail = {'decision': decision.outcome, 'route': confidence.route or None, 'provider': confidence.provider or None,
                  'model': confidence.model or None, 'observed_model': confidence.observed_model or None,
                  'confidence': confidence.probability, 'sent': bool(calls),
                  'gather': first,
                  'inputs': {'turns': len(lines), 'turns_chars': sum(len(line) for line in lines), 'clock': bool(clock),
                             'working_notes_version': current['version'] if current else None,
                             'previous_diary': bool(previous)}}
        if data is None:
            detail.update(applied=gathered)
            return (STATE_DONE if gathered else STATE_UNAVAILABLE), calls, (EVENT_RECORDED if gathered else EVENT_UNAVAILABLE), detail
        known, pending = self.known_values(MEMORY_OWNER, job['id'])
        rows, ids = lines_now()
        kept, dropped = validate_dream(data.get('proposals'), refs, refs.values(), known, pending, len(rows), ids)
        applied, stopped = self._apply_facts(kept, job, dropped, ends_after=ends_after)
        applied = gathered + applied
        detail.update(proposed=len(data.get('proposals') or []), applied=applied, dropped=dropped, stopped=stopped,
                      notes=None, diary=None)
        if stopped != STOPPED_PAUSED:
            revision, _blocker, refused = validate_review({'notes': data.get('notes'), 'change': data.get('change'),
                                                          'blocker': ''},
                                                         current['points'] if current else (), known)
            dropped.extend(refused)
            if revision:
                cited = dream_refs(data.get('notes_sources'), refs)
                if not cited or len(cited) < 2:
                    # A lesson about how to serve the owner needs two separate occasions (#402).
                    dropped.append({'note': 'notes', 'reason': 'one-occasion' if cited else 'sources'})
                elif current and len(current['points']) > 1 and len(
                        {normalized(point) for point in current['points']}
                        - {normalized(point) for point in revision['points']}) * 2 > len(current['points']):
                    # A look back that drops more than half of the notes is not trusted to do it at once.
                    dropped.append({'note': 'notes', 'reason': 'notes-rewrite'})
                elif self._notice_taken(job['id'], NOTICE_KIND):
                    # The anchor's one working-notes notice was used already: a revision now would go untold.
                    dropped.append({'note': 'notes', 'reason': 'notice-taken'})
                else:
                    self._save_notes(notes, current, revision, job['id'], judgments, dropped, detail)
            stopped = detail.get('stopped') or stopped
        diary = ' '.join(str(data.get('diary') or '').split())
        if diary and stopped != STOPPED_PAUSED:
            key = DIARY_PREFIX + diary_date
            if len(diary) > DIARY_CHARS:
                dropped.append({'note': 'diary', 'reason': 'length'})
            elif self.allowance(self.clock())[1] == STOPPED_PAUSED:
                stopped = STOPPED_PAUSED
            elif memory_value_has_secret(self.store, key, diary):
                dropped.append({'note': 'diary', 'reason': SECRET_SHAPED_VALUE})
            else:
                try:
                    memory = self.store.save_memory(key, diary, MEMORY_OWNER, work_id=job['id'], notice=True)
                except ValueError:
                    dropped.append({'note': 'diary', 'reason': 'store-refused'})
                else:
                    detail['diary'] = {'outcome': APPLIED_MEMORY, 'memory_id': memory['id'], 'date': diary_date,
                                       'chars': len(diary), 'superseded': bool(memory.get('supersedes'))}
        detail['stopped'] = stopped
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

        When ``work_id`` is a Telegram Work, its one ``NOTICE_KIND`` notice is inserted in the same
        transaction (review P1, as a direct Memory save holds its notice), so a saved revision is
        never left untold by a crash.  #233 (Codex review): it is ``queued`` only once the Work's
        reply is settled (``DELIVERY_SETTLED``); before that it is held, and the reply's delivery
        (or the release sweep) queues it, so the notice never overtakes the answer it is about.
        Returns ``{'id', 'ref', 'version', 'digest'}`` or None when the document changed meanwhile.
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
            job = (db.execute('SELECT channel,chat_id,delivery FROM jobs WHERE id=?', (work_id,)).fetchone()
                   if work_id else None)
            from .conversation_surface import surface_conversation
            # #306 review: the notice of a Work from any conversation surface, on that surface's queue.
            conversation = surface_conversation(job['channel'], job['chat_id']) if job else None
            if conversation:
                state = 'queued' if job['delivery'] in self.store.DELIVERY_SETTLED else self.store.NOTICE_HELD
                db.execute('INSERT OR IGNORE INTO telegram_notifications(id,job_id,chat_id,generation,kind,fingerprint,state,message_id,created,surface) VALUES (?,?,?,?,?,?,?,?,?,?)',
                           (str(uuid.uuid4()), work_id, job['chat_id'], conversation[1], NOTICE_KIND,
                            json.dumps({'ref': saved['ref'], 'version': saved['version']}), state, None, self.clock(),
                            conversation[0]))
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

    def migrated(self):
        """Whether the one-time move is done, trying it now (at most ``MIGRATION_ATTEMPTS`` plans) when it is not.

        ``migrate`` returns without moving anything when a planned row changed before its
        commit; it is then planned again at once.  A move that keeps failing is left to the
        next review and the next start, and the caller saves no version meanwhile (#233).
        """
        for _attempt in range(MIGRATION_ATTEMPTS):
            if self.store.config(MIGRATION_KEY):
                return True
            try:
                self.migrate()
            except Exception:  # noqa: BLE001 - retried by the next review and at the next start
                break
        return bool(self.store.config(MIGRATION_KEY))

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
