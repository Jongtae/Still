"""Owner-model upkeep: keep the owner in mind, asynchronously and minimised (OWNER-MODEL-03, #805 phase 1).

Bounded by docs/secretary-agency-contract.en.md ("Amendment - #805 phase 1"):

* **Trigger.** A Work a worker AI answered that ends ``succeeded`` or
  ``partial`` records one durable ``owner_model_upkeep`` row, in the same
  transaction that settles it (idempotent per Work).  Failed, cancelled and
  unknown Works, rule/command replies (no worker AI ran), preparation runs
  and location continuations record none: typed markers, never text rules.
  Settle and idle ticks also arm independent retrieval-terms runs.
* **Tick.** The existing service loop checks the queue at each idle tick. At
  most one pending row is claimed per tick, atomically with the check that
  no Work is queued or running, and runs off the work thread (single-flight,
  a hard deadline). Terms run only after other kinds, within their own share
  of the same cap. Nothing pending costs no model call.
* **Judgment (C16).** The owner's Judgment AI (``DecisionEngine.structured``)
  reads the owner's request (owner words), the final answer excerpt
  (model-stated), the profile snapshot and the clock, every fact redacted
  first, and proposes at most ``MAX_PROPOSALS`` durable owner facts and
  their search terms. The terms pass reads eligible rows on the active model.
* **Validation (Still).** Out-of-enum, oversized, non-``profile.`` and
  duplicate proposals are dropped.  What is kept is saved at once as current
  Memory attributed to the source Work (#918 slice a, owner decision
  2026-09-30: the owner's own AI acts for the owner; #955: no notice tells
  it, the next answer it shapes does, and 내 기록 keeps its exact undo).  The per-fact
  #597 judgment and the MemoryCandidate ask are no longer on this path; C5
  is unchanged for third-party writers.
* **Minimisation.** An owner pause switch and a rolling 24-hour cap on
  every model call (``CONFIG_KEY``), checked before each call.  The newest
  pending row runs first and one older than ``MAX_PENDING_SECONDS`` expires
  without a call (#876: an ask must come while the conversation is live, not
  a day later when a backlog drains under the cap), and a claimed
  row a crash left behind expires as ``interrupted``. Terms never age out
  and keep a content-free manifest instead of a Work event. Each Work run is one
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
and, when it learned how to serve this owner better, explicit operations
on working lessons. No text rule reads the owner's wording; nothing names a
site, task or category.

**Working lessons (#1031).** Each lesson is a canonical Memory row at
``lesson.<id>``. Self-review and dream may add a lesson, update its wording,
or end an explicitly named lesson with a reason. Other lessons stay current.
The owner can forget and undo a forget of each lesson by its own Memory ref.
The one-time migration restores points from retained versions of the old
working-notes slot after the #19 move of earlier ``profile.working.`` rows.

**Retrieval terms (#921).** Trigger: settle and idle ticks arm an unanchored
``terms`` run for eligible Memory. Tick: other kinds go first; terms get
``max(1, daily_calls // 4)`` calls within the shared cap and never age out. Judgment:
the active Judgment model supplies index words, never canonical facts. Claims
and finishes retain a content-free manifest; no Work event uses a run id.
Transport-agnostic batch sizing starts at eight rows. A rejected multi-row
answer halves the sent row count; success doubles the configured cap up to 25.
Character/byte packing limits still apply; unsent or expired runs keep the cap.
Only a rejected singleton consumes a row's failure allowance.

Reuse review (approved v5, offline): **Adopt** SQLite transactions, the upkeep
queue (claim, cap, pause), ``_context``, ``judgments_read``, ``memory_terms`` and
``write_retrieval_terms``. **Build** the terms kind, arming, packing, validation
and manifest. The existing queue avoids a new scheduler or framework. No new
dependency, licence, platform, network authority or supply-chain change.

Nothing here names a site, provider, task or request category.
"""

from . import memory_terms
from . import work_status as ws
import contextlib
import hashlib
import json
import re
import time
import uuid
from datetime import datetime

from .memory_service import _PROFILE_ROWS_CAP, PROFILE_SECTION_CHARS, MemoryService
from .owner_forget import FORGOTTEN, MEMORY_PREFIX, MIGRATE, OwnerForget
from .prompt_files import prompt
from .messages import msg
from . import preparations as prep

#: The source Work's tool-event name of every upkeep run (#794 shows it).
EVENT_TOOL = 'owner_model'
#: The DecisionContext purpose of the proposal judgment.
PURPOSE = 'owner-model-upkeep'
#: #998: the two kinds of run one row may be; the row key is ``(job_id, kind)``.
KIND_UPKEEP, KIND_SELF_REVIEW = 'upkeep', 'self-review'
#: #402: the once-a-day look back over recent requests (the "dream"); its row's Work is the newest
#: request it covers (the anchor: its Evidence, notice and Memory provenance).
KIND_DREAM = 'dream'
KIND_TERMS = 'terms'
RUN_KINDS = (KIND_UPKEEP, KIND_SELF_REVIEW, KIND_DREAM, KIND_TERMS)
# Adapt to observed answers on any transport, without route-specific limits.
TERMS_MAX_ROWS = 25
TERMS_START_ROWS = 8
TERMS_QUESTION = (
    'For each numbered note, also give terms: search words a later request might use that the fact\'s own words lack: '
    'base forms without particles or endings, the key terms in Korean and in English, and words that tell this '
    'fact apart from similar ones. Space-separated words only. '
    'Return every note number n exactly once, with at most 400 characters of terms per note.')
TERMS_SCHEMA = {'type': 'object', 'additionalProperties': False,
    'properties': {'notes': {'type': 'array', 'maxItems': TERMS_MAX_ROWS, 'items': {
        'type': 'object', 'additionalProperties': False,
        'properties': {'n': {'type': 'integer'}, 'terms': {'type': 'string', 'maxLength': 400}},
        'required': ['n', 'terms']}}}, 'required': ['notes']}
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
DREAM_WAY_NOTES_CHARS = 3000
#: #496, #1031: earlier Works are lesson evidence only, never another fact-gathering window.
MISS_OUTCOMES = ('failed', 'corrected', 'retried')
TURN_OUTCOMES = ('done', 'partial', 'failed', 'corrected', 'retried')
DREAM_EARLIER_DAYS = 7
DREAM_EARLIER_MISSES = 12
DREAM_EARLIER_SUCCESSES = 12
DREAM_CORRECTION_CHARS = 300
DREAM_EARLIER_CHARS = 4000
DREAM_EARLIER_SUCCESS_CHARS = 4000
DREAM_WAY_NOTE_CHARS = 300
LESSON_CHECK_PURPOSE = 'lesson-owner-specific'
#: #420: the dream's one judgment runs on the engine's strongest model and may take this long
#: (inside the run's own ``RUN_SECONDS`` deadline).
DREAM_TIMEOUT_SECONDS = 240
#: #416: a dream is two judgments - a first pass that only finds facts in the turns, then the look back
#: that tidies what is remembered, the notes and the daily record - so no call starts after this.
DREAM_RUN_SECONDS = 600
DREAM_GATHER_PURPOSE = 'owner-model-dream-gather'
#: How many facts the first pass may keep: it reads up to a day of requests.
DREAM_GATHER_LIMIT = 10
#: #583: at most this many standing preparations one look back may suggest, and how many active
#: preparations it is shown so it does not suggest them again.
DREAM_SUGGESTIONS = 3
DREAM_PREPARATIONS_SHOWN = 20
DREAM_PREPARATIONS_CHARS = 2000
#: #402: one dream may end at most this share of the current profile rows (at least one);
#: more is refused whole, the OpenClaw ``maxPriorEntryLossFraction`` lesson.
DREAM_END_FRACTION = 0.25
#: #402: the daily record.  A Memory row outside ``profile.``, one per owner date, in the owner's language;
#: #442: a Work's brief carries the last days' records (``MemoryService.records_block``).
DIARY_PREFIX = 'diary.'
#: #442: one line per event, so a busy day needs more room than a summary did.
DIARY_CHARS = 1500
#: Why a self-review was enqueued (typed markers, never text rules).
REVIEW_FAILED, REVIEW_CORRECTION, REVIEW_RETRY, REVIEW_REACTION = 'failed', 'correction', 'retry', 'reaction'
REVIEW_REASONS = (REVIEW_FAILED, REVIEW_CORRECTION, REVIEW_RETRY, REVIEW_REACTION)
#: The Memory namespace lessons used before #19: its rows move into the working notes once (``WorkingNotes.migrate``),
#: and upkeep proposes no fact under it (working methods are not semantic facts).
LESSON_PREFIX = 'profile.working.'
#: Bounds retained by the legacy #19 migration.
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
LESSONS_PREFIX = 'lesson.'
LESSONS_MAX = 40
MAX_LESSON_OPS = 5
LESSONS_BRIEF_CHARS = 3000
LESSONS_MIGRATION_KEY = 'working_lessons_migration'
ORIGIN_SELF_REVIEW = 'self-review'
ORIGIN_LESSONS_MIGRATION = 'lessons-migration'
APPLIED_LESSONS = 'working-lessons'
NOTICE_KIND = 'working_notes_saved'
RESTORED_NOTICE_KIND = 'working_lessons_restored'
_LESSON_OPS = ('add', 'update', 'remove')
_LESSON_FIELDS = ('op', 'id', 'text', 'why', 'reason')
#: Bounds of the self-review facts and of a stored observation.
EVALUATION_CHARS = 1500
FOLLOWUP_CHARS = 1500
MAX_OBSERVATION_CHARS = 500
#: A reviewed Work must have settled; a queued or running one is not reviewed.
REVIEWABLE_STATUSES = ws.REVIEWABLE_RESULT
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


REVIEW_QUESTION = prompt('owner_model_review').format(max_ops=MAX_LESSON_OPS, point_chars=POINT_CHARS)
REVIEW_QUESTION += ('\n\nThe shared Voice instructions below apply to change, which is shown to the owner. '
                    'The internal lessons and blocker keep their formats above.\n\n' + prompt('voice'))

LESSON_OP = {'type': 'object', 'additionalProperties': False,
             'properties': {**{name: {'type': 'string'} for name in _LESSON_FIELDS},
                            'op': {'type': 'string', 'enum': list(_LESSON_OPS)}},
             'required': list(_LESSON_FIELDS)}
_LESSONS_SCHEMA = {'type': 'array', 'items': LESSON_OP}

REVIEW_SCHEMA = {'type': 'object', 'additionalProperties': False,
                 'properties': {'lessons': _LESSONS_SCHEMA, 'change': {'type': 'string'},
                                'blocker': {'type': 'string'}},
                 'required': ['lessons', 'change', 'blocker']}


def review_shape(data):
    """Types only; each operation's meaning is checked by ``validate_lessons``."""
    return (isinstance(data.get('lessons'), list)
            and all(isinstance(data.get(name), str) for name in ('change', 'blocker')))


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


def _one_line(value):
    return ' '.join(value.split()) if isinstance(value, str) else ''


def _lesson_drop(op, reason):
    return {'note': 'lesson', 'op': op, 'reason': reason}


def _lesson_op_refusal(item, current, texts, seen_ids, seen_adds, known, request):
    op, ident = item['op'], item['id']
    if op not in _LESSON_OPS:
        return 'op'
    if op == 'add' and ident:
        return 'add-id'
    if op == 'remove' and not ident:
        return 'remove-id'
    if op != 'add' and ident not in current:
        return 'unknown-id'
    text = normalized(item['text'])
    if ident in seen_ids or (op == 'add' and text in seen_adds):
        return 'duplicate'
    if op == 'add':
        seen_adds.add(text)
    else:
        seen_ids.add(ident)
    if op == 'remove':
        return None if 0 < len(item['reason']) <= CHANGE_CHARS else 'remove-reason'
    if not text:
        return 'empty'
    if len(item['text']) > POINT_CHARS:
        return 'point-length'
    if not 0 < len(item['why']) <= CHANGE_CHARS:
        return 'why'
    if text in texts:
        return 'unchanged'
    if text == request:
        return 'request'
    return 'profile-fact' if text in known else None


def validate_lessons(data, current, known=frozenset(), request=''):
    """Validate explicit operations against the lessons the judgment read."""
    data = data if isinstance(data, dict) else {}
    note = _one_line(data.get('blocker'))
    change = _one_line(data.get('change'))
    blocker = note if 0 < len(note) <= MAX_TEXT_CHARS else None
    dropped = [{'note': 'blocker', 'reason': 'content'}] if note and blocker is None else []
    proposed = data.get('lessons')
    if not isinstance(proposed, list):
        return [], change, blocker, dropped
    if len(proposed) > MAX_LESSON_OPS:
        return [], change, blocker, [*dropped, {'note': 'lesson', 'reason': 'ops'}]

    kept, seen_ids, seen_adds = [], set(), set()
    texts = {normalized(text) for text in current.values()}
    request = normalized(request)
    for raw in proposed:
        raw = raw if isinstance(raw, dict) else {}
        item = {name: raw[name] if isinstance(raw.get(name), str) else '' for name in _LESSON_FIELDS}
        item['text'] = _one_line(item['text'])
        item['why'] = _one_line(item['why'])
        item['reason'] = item['reason'].strip()
        reason = _lesson_op_refusal(item, current, texts, seen_ids, seen_adds, known, request)
        if reason:
            dropped.append(_lesson_drop(item['op'], reason))
            continue
        item['reason'] = _one_line(item['reason'])
        kept.append(item)

    room = LESSONS_MAX - len(current) + sum(item['op'] == 'remove' for item in kept)
    allowed = []
    for item in kept:
        if item['op'] == 'add':
            room -= 1
            if room < 0:
                dropped.append(_lesson_drop('add', 'lessons-full'))
                continue
        allowed.append(item)
    veto = 'blocker' if note else 'change' if not 0 < len(change) <= CHANGE_CHARS else None
    if veto:
        dropped.extend(_lesson_drop(item['op'], veto) for item in allowed)
        allowed = []
    return allowed, change, blocker, dropped


QUESTION = prompt('owner_model_facts').format(categories=', '.join(CATEGORIES))

SCHEMA = {'type': 'object', 'additionalProperties': False,
          'properties': {'proposals': {'type': 'array', 'items': {
              'type': 'object', 'additionalProperties': False,
              'properties': {'category': {'type': 'string', 'enum': list(CATEGORIES)},
                             'kind': {'type': 'string', 'enum': list(KINDS)},
                             'memory_key': {'type': 'string'},
                             'content': {'type': 'string'},
                             'terms': {'type': 'string', 'maxLength': memory_terms.TERMS_CHARS},
                             'evidence': {'type': 'string'},
                             'supersedes_key': {'type': 'string'},
                             # #220: earlier facts (by the id owner_profile shows) this fact ends, and its last day.
                             'invalidates': {'type': 'array', 'items': {
                                 'type': 'object', 'additionalProperties': False,
                                 'properties': {'id': {'type': 'string'}, 'reason': {'type': 'string'}},
                                 'required': ['id', 'reason']}},
                             'valid_until': {'type': 'string'}},
              'required': ['category', 'kind', 'memory_key', 'content', 'evidence', 'supersedes_key', 'invalidates',
                           'valid_until', 'terms']}},
                         'situation': {'type': 'string'}},
          'required': ['proposals', 'situation']}


def shape(data):
    """Types only; each proposal's meaning is ``validate``'s (an absent situation is no note)."""
    return isinstance(data.get('proposals'), list) and isinstance(data.get('situation', ''), str)


DREAM_QUESTION = (prompt('owner_model_dream') + '\n\n' + prompt('owner_model_dream_lines')).format(
    categories=', '.join(CATEGORIES), max_ops=MAX_LESSON_OPS, point_chars=POINT_CHARS,
    diary_chars=DIARY_CHARS, suggestions=DREAM_SUGGESTIONS, title_chars=prep.TITLE_MAX_CHARS)
DREAM_QUESTION += ('\n\nThe shared Voice instructions below apply to diary and change, which the owner may read. '
                   'They never relax the rules above.\n\n' + prompt('voice'))

DREAM_GATHER_QUESTION = prompt('owner_model_dream_gather').format(categories=', '.join(CATEGORIES),
                                                                  gather_limit=DREAM_GATHER_LIMIT)
LESSON_CHECK_QUESTION = prompt('owner_model_lesson_check')

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
#: #638: the recurrence of a one-off suggestion (a dated need the owner mentioned); stored as no recurrence.
SUGGESTION_ONCE = 'once'
#: #583: a dream preparation is scheduled at once and told with a cancel button; continuations still propose.
_DREAM_SUGGESTION = {'type': 'object', 'additionalProperties': False,
                     'properties': {'kind': {'type': 'string', 'enum': list(prep.KINDS)},
                                    'goal': {'type': 'string'}, 'due': {'type': 'string'},
                                    'recurrence': {'type': 'string', 'enum': [*prep.RECURRENCES, SUGGESTION_ONCE]},
                                    'delivery': {'type': 'string', 'enum': ['send', 'keep', prep.DELIVERY_WHEN_NEEDED]},
                                    'title': {'type': 'string'}, 'offer': {'type': 'string'},
                                    'line': {'type': 'string'},
                                    'sources': {'type': 'array', 'items': {'type': 'string'}}},
                     'required': ['kind', 'goal', 'due', 'recurrence', 'delivery', 'title', 'offer', 'sources', 'line']}
#: #799: bounded inferred writes; evidence dates are computed from the owner store.
LINE_ACTIONS, LINE_EVIDENCE_DAYS, LINE_INPUT_CHARS, LINE_WAITING_MAX = 2, 14, 8000, 3
_DREAM_FIRST = json.loads(json.dumps(_DREAM_SUGGESTION))
for _field in ('sources', 'line'):
    _DREAM_FIRST['properties'].pop(_field, None)
_DREAM_FIRST['required'] = [key for key in _DREAM_FIRST['required'] if key not in ('sources', 'line')]
_DREAM_FIRST['properties']['kind']['enum'] = [prep.KIND_PREPARE]
_DREAM_FIRST['properties']['recurrence']['enum'] = list(prep.RECURRENCES)
_DREAM_FIRST['properties']['delivery']['enum'] = ['send', prep.DELIVERY_WHEN_NEEDED]
_DREAM_LINE = {'type': 'object', 'additionalProperties': False, 'properties': {
    **{key: {'type': 'string'} for key in ('line', 'task', 'report_promise', 'open_matters')},
    'action': {'type': 'string', 'enum': ['stand', 'amend']},
    'expected_version': {'type': 'integer'},
    'rules': {'type': 'array', 'items': {'type': 'string'}},
    'drop_rules': {'type': 'array', 'items': {'type': 'integer'}},
    'first': {'type': 'array', 'maxItems': 1, 'items': _DREAM_FIRST},
    'sources': {'type': 'array', 'items': {'type': 'string'}},
    'supporting': {'type': 'array', 'items': {'type': 'string'}}}}
_DREAM_LINE['required'] = list(_DREAM_LINE['properties'])

DREAM_SCHEMA = {'type': 'object', 'additionalProperties': False,
                'properties': {'proposals': {'type': 'array', 'items': _DREAM_ITEM},
                               'lessons': _LESSONS_SCHEMA, 'change': {'type': 'string'},
                               'lesson_sources': {'type': 'array', 'items': {'type': 'string'}},
                               'diary': {'type': 'string'},
                               'suggestions': {'type': 'array', 'items': _DREAM_SUGGESTION},
                               'lines': {'type': 'array', 'items': _DREAM_LINE}},
                'required': ['proposals', 'lessons', 'change', 'lesson_sources', 'diary', 'suggestions', 'lines']}


DREAM_GATHER_SCHEMA = {'type': 'object', 'additionalProperties': False,
                       'properties': {'proposals': {'type': 'array', 'items': _DREAM_ITEM}}, 'required': ['proposals']}


def gather_shape(data):
    """Types only; what may be kept is ``validate_dream``'s."""
    return isinstance(data.get('proposals'), list)


def dream_shape(data):
    """Types only; what may be kept is ``validate_dream``'s and the run's (no ``suggestions`` is none)."""
    return (isinstance(data.get('proposals'), list) and isinstance(data.get('lesson_sources'), list)
            and isinstance(data.get('lessons'), list)
            and isinstance(data.get('suggestions', []), list) and isinstance(data.get('lines', []), list)
            and all(isinstance(data.get(name), str) for name in ('change', 'diary')))


def validate_suggestions(suggestions, refs, turn_days, limit=DREAM_SUGGESTIONS):
    """``(kept, dropped)`` of a look back's preparation suggestions (#583, deterministic).

    Every suggestion rests on at least one turn the dream read (``refs``).  The owner's AI judges
    whether those turns show a standing interest, a rhythm or a dated need.  At most ``limit`` are kept.  What the
    suggestion asks for is the AI's (C16); its fields are validated where it is stored, by
    ``schedule_preparation``'s own normalisers.  A dropped entry names its position and reason, never its text.
    """
    kept, dropped = [], []
    for index, item in enumerate(suggestions if isinstance(suggestions, list) else []):
        named = dream_refs(item.get('sources'), refs) if isinstance(item, dict) else None
        reason = None
        if not isinstance(item, dict):
            reason = 'shape'
        elif not named:
            reason = 'sources'
        if reason is None and len(kept) >= limit:
            reason = 'limit'
        if reason:
            dropped.append({'suggestion': index, 'reason': reason})
        else:
            kept.append({**item, 'sources': named, 'index': index})
    return kept, dropped


def validate_line_actions(items, refs, request_refs, limit=LINE_ACTIONS):
    """Shape and shown-reference checks only; store provenance is checked at application."""
    kept, dropped = [], []
    allowed = set(refs) | set(request_refs)
    for index, item in enumerate(items if isinstance(items, list) else []):
        reason = None
        named = dream_refs(item.get('sources'), allowed) if isinstance(item, dict) else None
        if (not isinstance(item, dict) or set(item) - set(_DREAM_LINE['properties'])
                or item.get('action') not in ('stand', 'amend')
                or any(not isinstance(item.get(key), str) for key in ('line', 'task', 'report_promise', 'open_matters'))
                or type(item.get('expected_version')) is not int
                or any(not isinstance(item.get(key), list) for key in ('rules', 'drop_rules', 'first', 'supporting'))
                or any(not isinstance(rule, str) for rule in item.get('rules', []))
                or any(type(pos) is not int or pos < 1 for pos in item.get('drop_rules', []))
                or any(not isinstance(ref, str) for ref in item.get('supporting', []))
                or len(item.get('first', [])) > 1
                or any(not isinstance(first, dict) or set(first) != set(_DREAM_FIRST['properties'])
                       or any(not isinstance(value, str) for value in first.values()) for first in item.get('first', []))):
            reason = 'line_shape'
        elif not named or len(named) < (2 if item['action'] == 'stand' else 1):
            reason = 'line_evidence_sources'
        elif item['action'] == 'stand' and (item['line'] or item['expected_version'] != 0 or item['drop_rules']):
            reason = 'line_shape'
        elif item['action'] == 'amend' and (not item['line'] or item['expected_version'] < 1 or item['first']):
            reason = 'line_shape'
        elif len(kept) >= limit:
            reason = 'line_limit'
        if reason:
            dropped.append({'line': index, 'reason': reason})
        else:
            kept.append({**item, 'sources': named, 'index': index})
    return kept, dropped


def _dream_way_note_suffix(notes):
    text = '; '.join(filter(None, map(_one_line, notes)))[:DREAM_WAY_NOTE_CHARS]
    return ' | inferred: ' + text if text else ''


def dream_turn_input(turns, notes=None):
    """The look back's whole newest turns and shown refs, shared with the owner-run replay."""
    refs = {turn[0]: turn[1] for turn in turns}
    # #452: each turn's owner date ('YYYY-MM-DD' of its local time), when the caller gave one.
    turn_days = {turn[0]: str(turn[3])[:10] for turn in turns if len(turn) > 3 and turn[3]}
    lines, used = [], 0
    for ref, owner, answer, *when in turns:
        # #438: the owner's local time of each request, so the daily record can date its events.
        stamp = f" {when[0]}" if when and when[0] else ''
        outcome = when[1] if len(when) > 1 and when[1] in TURN_OUTCOMES else 'unknown'
        line = (f"[{ref}]{stamp} ({outcome}) owner: {' '.join(str(owner or '').split())[:DREAM_OWNER_CHARS] or '(withheld)'}"
                f" | assistant: {' '.join(str(answer or '').split())[:DREAM_ANSWER_CHARS] or 'none'}")
        lines.append(line)
        used += len(line) + 1
    while lines and used > DREAM_TURNS_CHARS:
        # The oldest turns give way: the newest are what upkeep had the least time for.
        used -= len(lines.pop(0)) + 1
    shown = {line[1:line.index(']')] for line in lines}
    refs = {ref: owner for ref, owner in refs.items() if ref in shown}
    if notes:
        remaining = DREAM_WAY_NOTES_CHARS
        for index in range(len(lines) - 1, -1, -1):
            line = lines[index]
            ref = line[1:line.index(']')]
            suffix = _dream_way_note_suffix(notes.get(ref, ()))
            if len(suffix) <= remaining:
                lines[index] += suffix
                remaining -= len(suffix)
    return lines, refs, turn_days


def turn_outcome(status, linked_kinds):
    """#496: observed Work markers only; the owner's words never classify an outcome."""
    from .conversation_handoff import FOLLOWUP_CORRECTION, FOLLOWUP_RETRY
    if FOLLOWUP_CORRECTION in linked_kinds:
        return 'corrected'
    if FOLLOWUP_RETRY in linked_kinds:
        return 'retried'
    return status if ws.has_incomplete_result(status) else 'done'


def dream_refs(sources, refs):
    """The distinct refs of ``sources`` that name a turn the dream read, or None when any names none (#402)."""
    if not isinstance(sources, list) or any(not isinstance(ref, str) for ref in sources):
        return None
    named = list(dict.fromkeys(ref.strip().strip('[]') for ref in sources if ref.strip()))
    return named if all(ref in refs for ref in named) else None


#: #452: the share of a quoted evidence's words that must be the owner's own for "the owner said it".
OWNER_SAID_SHARE = 0.75
BASIS_OWNER_SAID, BASIS_INFERRED = 'owner_said', 'inferred'


def owner_said_evidence(evidence, content, owner_texts):
    """The owner text the evidence or the fact comes from, or None (#452; provenance, not meaning).

    The same word test as the explicit-save gate (``agent_runtime.owner_said``, stems, digits exact): the
    quoted evidence when at least ``OWNER_SAID_SHARE`` of its words (two or more) are in one owner text,
    or the fact's own words when all of them are.  Words the assistant said are never the owner's.
    """
    from .agent_runtime import memory_words, owner_covers, owner_said
    quoted = memory_words(evidence)
    for text in owner_texts:
        words = memory_words(text)
        if not words:
            continue
        if len(quoted) >= 2 and sum(owner_said(word, words) for word in quoted) >= OWNER_SAID_SHARE * len(quoted):
            return text
        if owner_covers(content, words):
            return text
    return None


def validate_dream(proposals, refs, requests, known, pending, profile_rows, profile_ids=None, limit=MAX_PROPOSALS,
                   held=None):
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
            cited.append({**item, 'invalidates': grounded_ends(own, key, turns, lines, dropped, held)})
    kept, refused = validate(cited, known, pending, limit=limit)
    dropped.extend(refused)
    for item in kept:
        item['sources']=[ref for ref in dream_refs(item['sources'], {**refs, **profile_ids}) if ref in refs]
    if sum(len(item['invalidates']) for item in kept) > max(1, int(DREAM_END_FRACTION * max(0, profile_rows))):
        # Too much ended at once: every proposal that ends a line is refused; a pure addition stays.
        dropped.extend({'key_digest': key_digest(item['memory_key']), 'reason': 'mass-end'}
                       for item in kept if item['invalidates'])
        kept = [item for item in kept if not item['invalidates']]
    return kept, dropped


def grounded_ends(ends, key, turns, lines, dropped, held=None):
    """The ``ends`` of one dream proposal that are grounded (#424); the others go to ``dropped``.

    An ending stands only when its line is cited in the proposal's ``sources`` (``lines``) and the
    proposal restates that line, cites the owner turn (``turns``) that replaced it, or says the line was
    never a lasting fact (#429).  A
    malformed entry is kept for ``validate``, which refuses the whole proposal's shape.  A dropped
    ending names the line's short id (never its text): ``held`` when the judgment was unsure, so a
    later step can raise it with the owner; ``end-uncited`` or ``end-ungrounded`` otherwise.  A held
    ending of a cited line is also added to ``held`` (a list) as ``{'line', 'reason'}`` (#425).
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
            if held is not None and line in lines:
                held.append({'line': line, 'reason': ' '.join(str(end.get('reason') or '').split())[:MAX_TEXT_CHARS]})
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


def owner_stated_fact(store, work_id, key, content, judgments=None, cancelled=None):
    """Literal owner words first; one owner-only paraphrase judgment on a miss (#744).

    Call only for a marked Work. Third-party writes never use this exception.
    Unavailable or failed judgment cannot promote a proposed value.
    """
    if store.owner_stated_value(work_id, content):
        return True, None
    words = store.owner_fact_words(work_id) if judgments is not None else []
    if not words:
        return False, None
    from .conversation_handoff import JUDGMENT_YES
    try:
        judged, decision = judgments.owner_stated_memory(words, key, content, work_id=work_id, cancelled=cancelled)
    except Exception:
        # The transport outcome is unknown; count conservatively under upkeep's cap.
        from .decision import BinaryDecision, OUTCOME_UNAVAILABLE
        return False, BinaryDecision(OUTCOME_UNAVAILABLE)
    # Owner-word withdrawal during the call invalidates that call's evidence.
    return judged.outcome == JUDGMENT_YES and store.owner_fact_words(work_id) == words, decision


def profile_key(key):
    """Whether ``key`` is a well-formed ``profile.`` Memory key."""
    return (isinstance(key, str) and key.startswith(PROFILE_PREFIX) and len(PROFILE_PREFIX) < len(key) <= MAX_KEY_CHARS
            and not any(character.isspace() for character in key))


def _refusal(item):
    """Why one proposal is out of shape or bounds, or None."""
    if not isinstance(item, dict):
        return 'shape'
    if any(not isinstance(item.get(name), str) for name in ('category', 'kind', 'memory_key', 'content', 'evidence', 'terms')):
        return 'shape'
    if len(item['terms']) > memory_terms.TERMS_CHARS:
        return 'terms'
    if item['category'] not in CATEGORIES:
        return 'category'
    if item['kind'] not in KINDS:
        return 'kind'
    if not profile_key(item['memory_key']) or item['memory_key'].startswith(LESSON_PREFIX):
        # Working methods use their own lesson keys. The older profile namespace is reserved for migration.
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
                     'content': item['content'].strip(), 'terms': item['terms'], 'supersedes_key': (item.get('supersedes_key') or '').strip(),
                     'invalidates': [{'id': end['id'].strip(), 'reason': ' '.join(end['reason'].split())}
                                     for end in item.get('invalidates') or ()],
                     'valid_until': (item.get('valid_until') or '').strip(),
                     # Same words as a current value: kept only as a lifecycle update (see ``Upkeep.run``).
                     'lifecycle': content in known,
                     # #452: the quoted evidence, checked against the owner's own words when saved.
                     'evidence': item.get('evidence') if isinstance(item.get('evidence'), str) else '',
                     **({'sources': item['sources']} if 'sources' in item else {})})
    return kept, dropped


def terms_identity(confidence):
    """Only the deciding call's reported identity, never an inferred route/model."""
    return {key: getattr(confidence, key, '') or '' for key in ('route', 'model', 'observed_model')}


def terms_manifest(batch, outcomes=None, *, cap, result=None, stale=()):
    # Keep the configured cap separate from the observed sent rows and per-row receipts.
    entries = [dict(id=item['id'], digest=item['content_digest'],
                    **({'outcome': outcomes[n]} if outcomes is not None else {}))
               for n, item in enumerate(batch)]
    entries.extend(dict(id=item['id'], digest=item['content_digest'], outcome='stale-before-send')
                   for item in stale)
    return json.dumps({'cap': cap, 'rows': len(batch) if result in ('ok', 'rejected') else 0,
                       **({'result': result} if result else {}), 'entries': entries}, separators=(',', ':'))


def terms_answer(data, size):
    """Host validation, including bool-as-int, duplicates and transport bypasses."""
    notes = data.get('notes') if isinstance(data, dict) else None
    return (isinstance(notes, list) and len(notes) == size
            and all(isinstance(item, dict) and type(item.get('n')) is int
                    and isinstance(item.get('terms'), str)
                    and len(item['terms']) <= memory_terms.TERMS_CHARS for item in notes)
            and {item['n'] for item in notes} == set(range(1, size + 1)))


class Upkeep:
    """The durable upkeep queue and the owner's controls, in the owner's QuickStore."""

    def __init__(self, store, clock=time.time):
        self.store, self.clock = store, clock
        #: #402: when the tick last asked whether a dream is due (in memory: a restart asks again).
        self.dream_checked = None

    # -- controls -----------------------------------------------------------
    @staticmethod
    def report_times(value):
        """Canonical owner-local times; reject rather than silently reorder the owner's input."""
        if (not isinstance(value, list) or not 1 <= len(value) <= 4
                or any(not isinstance(t, str) or len(t) != 5 or t[2] != ':'
                       or any(c not in '0123456789' for c in t[:2]+t[3:])
                       or t[:2] > '23' or t[3:] > '59' for t in value)
                or value != sorted(set(value))):
            raise ValueError(msg('settings_orchestrator.report_times_format'))
        return list(value)

    @staticmethod
    def _controls(value):
        value = value if isinstance(value, dict) else {}
        cap = value.get('daily_calls', DEFAULT_DAILY_CALLS)
        if isinstance(cap, bool) or not isinstance(cap, int):
            cap = DEFAULT_DAILY_CALLS
        try: times = Upkeep.report_times(value.get('report_times', ['09:00', '18:00']))
        except ValueError: times = ['09:00', '18:00']
        return {'report_times': times, 'enabled': value.get('enabled', True) is not False, 'daily_calls': max(0, min(MAX_DAILY_CALLS, cap)),
                # #402: the once-a-day look back has its own switch; the pause above stops it too.
                'dream': value.get('dream', True) is not False}

    def settings(self, db=None):
        """Normalised owner-model switches, call cap and local report times; enabled by default (#805)."""
        if db is None:
            return self._controls(self.store.config(CONFIG_KEY))
        row = db.execute('SELECT value FROM config WHERE key=?', (CONFIG_KEY,)).fetchone()
        try:
            return self._controls(json.loads(row['value']) if row else None)
        except (TypeError, ValueError):
            return self._controls(None)

    def set_controls(self, body):
        """The owner's pause/look-back switches, call cap and report times."""
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
        if 'report_times' in body:
            current['report_times'] = self.report_times(body['report_times'])
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
        if kind not in RUN_KINDS or kind == KIND_TERMS:
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

    # -- derived retrieval index (#921) ------------------------------------
    def terms_left(self, now, db):
        used = db.execute('SELECT COALESCE(SUM(calls),0) FROM owner_model_upkeep '
                          'WHERE kind=? AND claimed>=?', (KIND_TERMS, now - WINDOW_SECONDS)).fetchone()[0]
        return max(1, self.settings(db)['daily_calls'] // 4) - used

    def _terms_rows(self, db, today):
        from .agent_runtime import MEMORY_OWNER
        return db.execute(f"SELECT * FROM memories WHERE owner_key=? AND state='current' "
                          "AND memory_key LIKE 'profile.%' AND (valid_until IS NULL OR valid_until>=?) "
                          f"AND {memory_terms.ELIGIBLE_SQL} ORDER BY created DESC,id DESC",
                          (self.store._memory_binding(MEMORY_OWNER), today))

    def arm_terms(self, db=None, now=None, *, available=False):
        """One repeatable identity, under the settle transaction or our own immediate lock.

        ``available`` is the service's cheap engine preflight, never a model call.
        No Work or source conversation is needed. A parked run retains its identity.
        """
        from .current_context import owner_today
        now = self.clock() if now is None else now
        if not available:
            return None
        if db is None:
            with self.store.db() as conn:
                conn.execute('BEGIN IMMEDIATE')
                return self.arm_terms(conn, now, available=available)
        if not db.in_transaction:
            db.execute('BEGIN IMMEDIATE')
        if not self.settings(db)['enabled'] or self.terms_left(now, db) <= 0:
            return None
        if db.execute('SELECT 1 FROM owner_model_upkeep WHERE kind=? AND state IN (?,?)',
                      (KIND_TERMS, STATE_PENDING, STATE_CLAIMED)).fetchone():
            return None
        if self._terms_rows(db, owner_today(self.store, now)).fetchone() is None:
            return None
        n = db.execute("SELECT COALESCE(MAX(CAST(substr(job_id,14) AS INTEGER)),0)+1 "
                       "FROM owner_model_upkeep WHERE kind=?", (KIND_TERMS,)).fetchone()[0]
        run = f'memory-terms:{n}'
        db.execute('INSERT INTO owner_model_upkeep(job_id,kind,state,created) VALUES (?,?,?,?)',
                   (run, KIND_TERMS, STATE_PENDING, now))
        return run

    def terms_cap(self, db):
        """Learn only from the latest finished run; pending/claimed runs are not answers."""
        row = db.execute('SELECT state,observation FROM owner_model_upkeep WHERE kind=? AND state IN (?,?) '
                         'ORDER BY created DESC,rowid DESC LIMIT 1',
                         (KIND_TERMS, STATE_DONE, STATE_EXPIRED)).fetchone()
        manifest = json.loads(row['observation'] or '{}') if row else {}
        cap = manifest.get('cap', TERMS_START_ROWS)
        if row and row['state'] == STATE_DONE:
            if manifest.get('result') == 'rejected' and manifest.get('rows', 0) > 1:
                cap = max(1, manifest['rows'] // 2)
            elif manifest.get('result') == 'ok':
                cap = min(TERMS_MAX_ROWS, cap * 2)
        return cap

    def terms_batch(self, db, today, redact, *, cap):
        """Whole numbered facts, redacted before measuring the actual notes input."""
        return self._pack_terms(self._terms_rows(db, today), redact, cap=cap)

    def _pack_terms(self, rows, redact, *, cap):
        if redact is None:
            from .conversation_handoff import ConversationJudgments
            redact = ConversationJudgments().redact
        batch, text = [], ''
        for row in rows:
            fact = redact(f"{row['memory_key']}: {row['content']}")
            single = f'1. {fact}'
            large = len(single) > 5000 or len(single.encode('utf-8')) > 15000
            if large:
                if batch:
                    break
                batch.append(dict(row, rendered=single,
                                  oversize=len(single) > 4400 or len(single.encode('utf-8')) > 17600))
                break
            line = f'{len(batch)+1}. {fact}'
            joined = text + ('\n' if text else '') + line
            if len(joined) > 5000 or len(joined.encode('utf-8')) > 15000:
                break
            batch.append(dict(row, rendered=line, oversize=False))
            text = joined
            if len(batch) == cap:
                break
        return batch

    def run_terms(self, row, judgments, *, cancelled=None, read_scope=None):
        """One index-only judgment; guarded writes reject every stale row revision.

        The claim's call reservation is released before the allowance check, then
        restored before dispatch. A crash during dispatch keeps that reservation.
        An explicitly unsent answer parks the same run for a window, with no charge;
        it never turns into zero-call re-arming. Normal availability is checked again
        by the service before claiming. Outputs and source texts never enter receipts.
        """
        from .agent_runtime import MEMORY_OWNER
        from .current_context import owner_today
        run, batch = row['job_id'], row['batch']
        cap = json.loads(row['observation'])['cap']
        self.count(run, 0, KIND_TERMS)
        with self.store.db() as db:
            allowed = (self.settings(db)['enabled'] and self.terms_left(self.clock(), db) > 0
                       and self.settings(db)['daily_calls'] > self.calls_used(self.clock(), db))
        if not allowed or (cancelled and cancelled()):
            return STATE_PENDING, 0, EVENT_UNAVAILABLE, {'manifest': terms_manifest(batch, cap=cap, result='unsent')}
        # A claim is not a disclosure snapshot: forgetting (including forget/undo),
        # editing or another index writer must take effect before dispatch too.
        fresh, stale = [], []
        with self.store.db() as db:
            today = owner_today(self.store, self.clock())
            for item in batch:
                current = db.execute("SELECT * FROM memories WHERE id=? AND owner_key=? AND state='current' "
                                     "AND content_digest=? AND (valid_until IS NULL OR valid_until>=?) "
                                     "AND memory_key GLOB 'profile.*' AND retrieval_terms_meta IS ?",
                                     (item['id'], self.store._memory_binding(MEMORY_OWNER), item['content_digest'],
                                      today, item['retrieval_terms_meta'])).fetchone()
                if current is None:
                    stale.append(item)
                else:
                    fresh.append(current)
        batch = self._pack_terms(fresh, judgments.redact, cap=cap)
        if not batch:
            return STATE_DONE, 0, EVENT_RECORDED, {
                'manifest': terms_manifest(batch, cap=cap, result='stale' if stale else 'unsent', stale=stale)}
        decision, data, calls = None, None, 0
        oversize = bool(batch and batch[0]['oversize'])
        if batch and not oversize:
            sources = self.store.memory_sources([item['id'] for item in batch])
            works = list(dict.fromkeys(source['work_id'] for value in sources.values()
                         for source in value.get('sources', [value])))
            self.count(run, 1, KIND_TERMS)
            with (read_scope(works) if read_scope else contextlib.nullcontext()):
                data, decision = judgments.memory_terms('\n'.join(item['rendered'] for item in batch),
                                                        cancelled=cancelled)
            calls = int(call_sent(decision))
            self.count(run, calls, KIND_TERMS)
            if not calls:
                with self.store.db() as db:
                    db.execute('UPDATE owner_model_upkeep SET reason=? WHERE job_id=? AND kind=?',
                               ('unsent', run, KIND_TERMS))
                return STATE_PENDING, 0, EVENT_UNAVAILABLE, {
                    'manifest': terms_manifest(batch, cap=cap, result='unsent', stale=stale)}
        valid = terms_answer(data, len(batch))
        answers = {item['n']: item['terms'] for item in data['notes']} if valid else {}
        outcomes = []
        for n, item in enumerate(batch, 1):
            if not oversize and not valid and len(batch) > 1:
                outcomes.append('rejected-batch')
                continue
            if self.allowance(self.clock())[1] == STOPPED_PAUSED:
                outcomes.append('paused')
                continue
            text, failure_n = '', None
            if oversize:
                status = 'oversize'
            elif not valid:
                status = 'failed'
                failure_n = min(memory_terms.FAIL_LIMIT, memory_terms.parse_meta(item['retrieval_terms_meta']).get('n', 0) + 1)
            else:
                text, _ = memory_terms.filter_terms(self.store, item['memory_key'], item['content'], answers[n])
                status = 'keyed' if text else 'empty'
            meta = memory_terms.build_meta(status, previous=item['retrieval_terms_meta'],
                       **terms_identity(decision.confidence if decision else None), policy='terms-pass', run=run,
                       at=self.clock(), n=failure_n)
            committed = self.store.write_retrieval_terms(MEMORY_OWNER, item['id'], terms=text or None, meta=meta,
                        expected_meta=item['retrieval_terms_meta'], content_digest=item['content_digest'],
                        today=owner_today(self.store, self.clock()))
            outcomes.append(status if committed else 'stale')
        result = 'unsent' if not batch else 'oversize' if oversize else 'ok' if valid else 'rejected'
        return STATE_DONE, calls, EVENT_RECORDED, {
            'manifest': terms_manifest(batch, outcomes, cap=cap, result=result, stale=stale)}

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
        night hours, or ``DREAM_LATE_SECONDS`` since the last (#425: the first dream, with no last, waits for
        the night).  The anchor is the newest of those
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
            if db.execute('SELECT 1 FROM owner_model_upkeep WHERE state IN (?,?) AND kind!=? LIMIT 1',
                          (STATE_PENDING, STATE_CLAIMED, KIND_TERMS)).fetchone():
                return None
            if db.execute(f"SELECT 1 FROM jobs WHERE status IN {ws.sql_in(ws.ACTIVE)} LIMIT 1").fetchone():
                return None
            last = self.last_dream(db)
            if last is not None and now - last < DREAM_INTERVAL_SECONDS:
                return None
            if local_hour not in DREAM_NIGHT_HOURS and (last is None or now - last < DREAM_LATE_SECONDS):
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
        """The settled requests a dream reads, oldest first: text, Work markers and batched later links,
        the newest ``limit`` of those created in ``(since, until]`` (all when None). A request or answer the owner
        forgot is withheld (#367); the caller drops what upkeep would not read (typed markers).  Only Works whose
        text is the owner's own words (#423, ``OWNER_WORDS_SQL``) are read."""
        from .quickstart_store import OWNER_WORDS_SQL, forgotten_turn
        from .conversation_handoff import FOLLOWUP_CANCEL, FOLLOWUP_CORRECTION, FOLLOWUP_RETRY
        with self.store.db() as db:
            rows = db.execute(
                f"SELECT j.id,j.request_key,j.provider,j.created,j.status,j.relation_kind,"
                f"CASE WHEN {forgotten_turn('user', 'j.id')} THEN '' ELSE j.message END AS message,"
                f"CASE WHEN {forgotten_turn('assistant', 'j.id')} THEN '' ELSE j.response END AS response "
                f"FROM jobs j WHERE j.created>? AND j.created<=? AND j.status IN {ws.sql_in(ws.AVAILABLE_RESULT)} "
                f"AND {OWNER_WORDS_SQL} ORDER BY j.created DESC LIMIT ?", (since, until, limit if limit is not None else -1)).fetchall()
            # #496, #1031: later links in one batch; forgotten words remain withheld.
            links = db.execute(
                f"SELECT c.id,c.related_job_id,c.relation_kind,c.created,"
                f"CASE WHEN {forgotten_turn('user', 'c.id')} THEN '' ELSE c.message END AS message "
                f"FROM jobs c JOIN jobs j ON c.related_job_id=j.id WHERE j.created>? AND j.created<=? "
                f"AND c.id<>j.id AND c.created>=j.created AND c.relation_kind IN (?,?,?) ORDER BY c.created DESC,c.id",
                (since, until, FOLLOWUP_CORRECTION, FOLLOWUP_RETRY, FOLLOWUP_CANCEL)).fetchall()
            # Bind just the selected Works as one parameter, even when there is no count limit.
            work_ids = json.dumps([row['id'] for row in rows])
            reviewed = {row['job_id'] for row in db.execute(
                'SELECT job_id FROM owner_model_upkeep WHERE kind=? AND job_id IN (SELECT value FROM json_each(?))',
                (KIND_SELF_REVIEW, work_ids))}
            notes = db.execute(
                "SELECT e.job_id, CASE WHEN json_valid(e.detail) THEN "
                "CASE WHEN json_type(e.detail,'$.evidence.method_gap')='text' THEN "
                "json_extract(e.detail,'$.evidence.method_gap') END END AS note "
                "FROM tool_events e WHERE e.tool='method_gap' AND e.status='succeeded' "
                "AND e.job_id IN (SELECT value FROM json_each(?)) "
                f"AND NOT {forgotten_turn('assistant', 'e.job_id')} ORDER BY e.created,e.id", (work_ids,)).fetchall()
        way_notes = {}
        for row in notes:
            if row['note'] and row['note'].strip():
                way_notes.setdefault(row['job_id'], []).append(row['note'])
        result = []
        linked = {}
        for link in links:
            linked.setdefault(link['related_job_id'], []).append(dict(link))
        for row in reversed(rows):
            work = dict(row)
            following = linked.get(work['id'], [])
            work['linked_kinds'] = {link['relation_kind'] for link in following}
            corrections = [link for link in following if link['relation_kind'] != FOLLOWUP_CANCEL]
            work['correction'] = next((link for link in corrections if link['relation_kind'] == FOLLOWUP_CORRECTION),
                                      corrections[0] if corrections else None)
            work['reviewed'] = work['id'] in reviewed
            work['way_notes'] = way_notes.get(work['id'], [])
            result.append(work)
        return result

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
        """Expire crashed claims; terms retain their manifest, Work kinds get an interrupted event."""
        with self.store.db() as db:
            rows = [(row['job_id'], row['kind']) for row in db.execute(
                'SELECT job_id,kind FROM owner_model_upkeep WHERE state=? AND claimed<?',
                (STATE_CLAIMED, now - CLAIM_STALE_SECONDS))]
        for job_id, kind in rows:
            self.finish(job_id, STATE_EXPIRED, None, EVENT_EXPIRED, {'reason': 'interrupted'}, now, kind=kind)
        return len(rows)

    def claim_due(self, now, *, terms_available=True, redact=None):
        """Claim the newest pending row this tick may run, or None (#876: the live conversation first).

        One immediate transaction: the check that no Work is queued or
        running and the claim are atomic with ``run_one``'s own claim, so an
        upkeep never starts beside a Work that was already claimed.  A
        claimed row is never run again, even after a restart; it counts one
        call until the run records its real number.  ``remaining`` is the
        budget at claim time, for the record: the run re-reads it before each
        later call (``allowance``).
        """
        from .current_context import owner_today
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            settings = self.settings(db)
            if not settings['enabled']:
                return None
            if db.execute(f"SELECT 1 FROM jobs WHERE status IN {ws.sql_in(ws.ACTIVE)} LIMIT 1").fetchone():
                return None
            row = db.execute('SELECT * FROM owner_model_upkeep WHERE state=? AND kind!=? '
                             'ORDER BY created DESC LIMIT 1', (STATE_PENDING, KIND_TERMS)).fetchone()
            if row is None and terms_available and self.terms_left(now, db) > 0:
                row = db.execute('SELECT * FROM owner_model_upkeep WHERE state=? AND kind=? '
                                 'AND (reason IS NOT ? OR claimed<=?) ORDER BY created DESC LIMIT 1',
                                 (STATE_PENDING, KIND_TERMS, 'unsent', now - WINDOW_SECONDS)).fetchone()
            if row is None:
                return None
            expired = row['kind'] != KIND_TERMS and now - row['created'] > MAX_PENDING_SECONDS
            remaining = settings['daily_calls'] - self.calls_used(now, db)
            if not expired and remaining < 1:
                return None
            result = dict(row, expired=expired, remaining=remaining)
            if row['kind'] == KIND_TERMS:
                # A parked claim keeps its cap; only a new run learns from finished history.
                cap = json.loads(row['observation'] or '{}').get('cap')
                if cap is None:
                    cap = self.terms_cap(db)
                batch = self.terms_batch(db, owner_today(self.store, now), redact, cap=cap)
                result['batch'] = batch
                result['observation'] = terms_manifest(batch, cap=cap)
            db.execute('UPDATE owner_model_upkeep SET state=?,claimed=?,calls=?,observation=? WHERE job_id=? AND kind=?',
                       (STATE_CLAIMED, now, 0 if expired else 1, result['observation'], row['job_id'], row['kind']))
            return result

    def count(self, job_id, calls, kind=KIND_UPKEEP):
        """Record the calls a run has made so far (the cap sees them at once)."""
        with self.store.db() as db:
            db.execute('UPDATE owner_model_upkeep SET calls=? WHERE job_id=? AND kind=?', (int(calls), job_id, kind))

    def finish(self, job_id, state, calls, event_status, detail, now, kind=KIND_UPKEEP):
        """Settle a claim: a manifest for terms, otherwise one tool event on the source Work."""
        tool = {KIND_SELF_REVIEW: REVIEW_EVENT_TOOL, KIND_DREAM: DREAM_EVENT_TOOL}.get(kind, EVENT_TOOL)
        with self.store.db() as db:
            if calls is None:
                db.execute('UPDATE owner_model_upkeep SET state=? WHERE job_id=? AND kind=?', (state, job_id, kind))
            else:
                db.execute('UPDATE owner_model_upkeep SET state=?,calls=? WHERE job_id=? AND kind=?',
                           (state, int(calls), job_id, kind))
            if kind == KIND_TERMS:
                if 'manifest' in detail:
                    db.execute('UPDATE owner_model_upkeep SET observation=? WHERE job_id=? AND kind=?',
                               (detail['manifest'], job_id, kind))
                return
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
        fact_calls = [calls]
        applied, stopped = self._apply_facts(kept, job, dropped, judgments=judgments,
                                           cancelled=cancelled, judgment_calls=fact_calls, terms_confidence=confidence)
        calls = sum(fact_calls)
        detail['owner_fact_calls'] = sum(fact_calls[1:])
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

    def _apply_facts(self, kept, job, dropped, ends_after=None, origin=None, source_works=None,
                     judgments=None, cancelled=None, judgment_calls=None, terms_confidence=None):
        """Save eligible proposals or sourced candidates; returns ``(applied, stopped)``.

        Shared by upkeep and the dream (#402).  ``dropped`` is extended in place.  ``ends_after``
        (the dream's claim time) widens which current rows a proposal may end: any row that existed
        then, instead of the rows that existed when ``job`` arrived.  ``origin`` 'dream' (#425) marks the
        rows the look back wrote: they hold no notice of the Work's own and are raised by ``MemoryRaise``.
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
            # Single-Work upkeep sees the answer, so its unsupported facts are
            # candidates. Dream input excludes marked answers before either AI
            # call, retaining the existing owner-only learning/quiet diary path.
            sources = self.store.outside_content_sources(job['id']) if origin != ORIGIN_DREAM else []
            if sources:
                stated, decision = owner_stated_fact(self.store, job['id'], key, content,
                    judgments=judgments if _left > 0 else None, cancelled=cancelled)
                if decision is not None:
                    used = int(call_sent(decision))
                    if judgment_calls is not None:
                        judgment_calls.append(used)
                        self.count(job['id'], sum(judgment_calls))
                    if self.allowance(self.clock())[1] == STOPPED_PAUSED:
                        stopped = STOPPED_PAUSED
                        dropped.extend({'key_digest': key_digest(rest['memory_key']), 'reason': STOPPED_PAUSED}
                                       for rest in kept[index:])
                        break
                if not stated:
                    candidate = self.store.save_memory_candidate(job['id'], key, content, source=sources)
                    applied.append({'outcome':APPLIED_CANDIDATE, 'candidate_id':candidate['id'], 'memory_key':key})
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
            # current Memory, attributed to the source Work (#794), instead of a per-fact #597 judgment
            # and an ask; #955: told when it next shapes an answer, undone exactly from 내 기록 (#962).
            # A write is not a model call, so the cap and the deadline do not stop it; the pause does.
            cited_works = (list(dict.fromkeys(source_works[ref] for ref in item.get('sources', ())
                                              if ref in source_works)) if source_works is not None else None)
            # #452: what the fact rests on.  "The owner said it" only when its evidence is found in the owner's own
            # messages of the requests it cites (this Work's, for upkeep); otherwise it is an inference, and a
            # dream's inference needs two separate requests, as validate_dream asks of one marked inferred.
            basis, evidence, message = self._basis(item, job, cited_works)
            if basis is None and cited_works:
                if len(cited_works) < 2:
                    dropped.append({'key_digest': key_digest(key), 'reason': 'not-owner-words'})
                    continue
                basis = BASIS_INFERRED
            elif basis is None and cited_works is None:
                basis = BASIS_INFERRED
            row['basis'] = basis
            terms, _ = memory_terms.filter_terms(self.store, key, content, item['terms'])
            meta = (memory_terms.build_meta('keyed', previous=None,
                    **terms_identity(terms_confidence), policy='dream' if origin == ORIGIN_DREAM else 'upkeep',
                    run=job['id'], at=self.clock()) if terms else None)
            try:
                memory = self.store.save_memory(key, content, MEMORY_OWNER, work_id=job['id'],
                                                # A fact citing no turn (a pure tidy-up) records none: the legacy lookup stays.
                                                origin=origin, source_works=cited_works or None,
                                                invalidates=[(full, end['reason'])
                                                             for full, end in zip(ends, item['invalidates'])],
                                                # '' is the AI saying the fact has no last day.
                                                valid_until=item['valid_until'],
                                                basis=basis, evidence=evidence, evidence_message=message,
                                                retrieval_terms=terms or None, retrieval_terms_meta=meta)
            except ValueError:
                dropped.append({'key_digest': key_digest(key), 'reason': 'store-refused'})
                continue
            row.update(outcome=APPLIED_MEMORY, memory_id=memory['id'], superseded=bool(memory.get('supersedes')),
                       invalidated=len(ends), auto_saved=True)
            if source_works is not None:
                row['source_works']=cited_works
            applied.append(row)
        return applied, stopped

    def _basis(self, item, job, cited_works):
        """``(basis, evidence, message id)`` of one proposal (#452); basis None when "stated" is not found.

        A proposal citing no request (a dream's tidy-up of existing lines) keeps the basis of the line it restates.
        """
        from .agent_runtime import MEMORY_OWNER
        from .quickstart_store import FORGOTTEN_MESSAGE_IDS
        evidence = ' '.join(str(item.get('evidence') or '').split())[:300] or None
        if cited_works is not None and not cited_works:
            same = self.store.current_memory(item['memory_key'], MEMORY_OWNER) or {}
            return same.get('basis'), same.get('evidence'), same.get('evidence_message')
        works = cited_works if cited_works is not None else [job['id']]
        marks = ','.join('?' for _ in works)
        with self.store.db() as db:
            rows = [(row['id'], row['content']) for row in db.execute(
                f"SELECT id,content FROM messages WHERE role='user' AND job_id IN ({marks}) "
                f"AND id NOT IN ({FORGOTTEN_MESSAGE_IDS}) ORDER BY id", works)]
            if not rows:
                # A Work without its transcript row: its own request copy.
                rows = [(None, row['message']) for row in db.execute(
                    f"SELECT message FROM jobs WHERE id IN ({marks})", works) if row['message']]
        found = owner_said_evidence(evidence, item['content'], [text for _ident, text in rows])
        if item.get('kind') != KIND_STATED:
            return BASIS_INFERRED, evidence, None
        if found is None:
            return None, evidence, None
        return BASIS_OWNER_SAID, evidence, next(ident for ident, text in rows if text == found)

    # -- one self-review (#998) ---------------------------------------------
    def run_review(self, job, row, judgments, *, answer, profile, clock, cancelled=None):
        """Review one Work and save validated operations on its working lessons."""
        from .agent_runtime import MEMORY_OWNER
        lessons = WorkingLessons(self.store, self.clock)
        reason = row.get('reason') or ''
        if not lessons.migrated():
            return STATE_UNAVAILABLE, 0, EVENT_UNAVAILABLE, {'reason': reason, 'sent': False,
                                                             'stopped': STOPPED_MIGRATION}
        current = {lesson['id']: lesson for lesson in lessons.current()}
        current_text = self._lesson_input(current)
        request = str(job.get('message') or '')
        followups = self.followups(job['id'])
        followup = '\n'.join(followups) if followups else (row.get('observation') or '')
        evaluation = '\n'.join(self.evaluations(job['id']))
        awaiting = self.awaiting(job['id'])
        data, decision = judgments.self_review(request, answer, str(job.get('status') or ''), evaluation, followup,
                                               profile, reason, work_id=job['id'], cancelled=cancelled,
                                               working_lessons=current_text, awaiting_owner=awaiting)
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
                             'working_lessons': len(current), 'working_lessons_chars': len(current_text), 'clock': bool(clock)}}
        if data is None:
            return STATE_UNAVAILABLE, calls, EVENT_UNAVAILABLE, detail
        known, _pending = self.known_values(MEMORY_OWNER, job['id'])
        ops, change, blocker, dropped = validate_lessons(
            data, {ident: item['text'] for ident, item in current.items()}, known, request=request)
        detail.update(lessons=None, blocker=None, dropped=self._lesson_evidence_drops(dropped), stopped=None)
        if blocker:
            # Evidence on the reviewed Work only: redacted like every fact, never filed anywhere.
            detail['blocker'] = judgments.redact(blocker)[:MAX_TEXT_CHARS]
        if ops:
            self._save_lessons(lessons, current, ops, change, job['id'], judgments, detail['dropped'], detail,
                               origin=ORIGIN_SELF_REVIEW)
        return STATE_DONE, calls, EVENT_RECORDED, detail

    @staticmethod
    def _lesson_input(current):
        return '\n'.join(f"[{ident}] {item['text']}" for ident, item in current.items())

    @staticmethod
    def _lesson_evidence_drops(dropped):
        # Invalid operation names are arbitrary model text, so never retain them as Evidence.
        return [{**item, 'op': ''} if item.get('op') not in (*_LESSON_OPS, None) else item for item in dropped]

    def _save_lessons(self, lessons, current, ops, change, work_id, judgments, dropped, detail, *, origin, sources=None):
        """Save operations that still have their authority, recording only ids and counts."""
        if self.allowance(self.clock())[1] == STOPPED_PAUSED:
            detail['stopped'] = STOPPED_PAUSED
            dropped.extend(_lesson_drop(item['op'], STOPPED_PAUSED) for item in ops)
            return
        visible = []
        for item in ops:
            target = current.get(item['id'])
            if target and judgments.redact(target['text']) != target['text']:
                dropped.append(_lesson_drop(item['op'], 'lesson-withheld'))
            else:
                visible.append(item)
        result = lessons.apply(visible, change, work_id, origin=origin, sources=sources,
                               expected_rows={ident: item['memory_id'] for ident, item in current.items()})
        dropped.extend(result['dropped'])
        if applied := result['applied']:
            detail['lessons'] = {'outcome': APPLIED_LESSONS, 'applied': applied,
                                 'added': sum(item['op'] == 'add' for item in applied),
                                 'updated': sum(item['op'] == 'update' for item in applied),
                                 'removed': sum(item['op'] == 'remove' for item in applied),
                                 'auto_saved': True}

    # -- one dream (#402) ---------------------------------------------------
    def run_dream(self, job, row, judgments, *, turns, profile, clock, diary_date, ends_after, cancelled=None, source_works=None,
                  preparations='', suggest=None, earlier=(), earlier_works=None, way_notes=None, read_scope=None, lines='',
                  line_refs=None, stand=None, line_read=None):
        """Look back over ``turns`` once and keep the owner model in order; returns ``(state, calls, event_status, detail)``.

        ``job`` is the anchor Work (the newest request the dream covers): what is kept is attributed to
        it and its notice tells the owner, with an undo, as upkeep's saves do (#918 slice a).  ``turns``
        are ``(ref, owner words, answer[, local time[, outcome]])`` tuples the caller scrubbed, oldest first; ``profile`` the
        snapshot with short ids and no lessons. ``source_works`` optionally maps turn refs to
        their Work ids (#443); omitted by legacy callers. After fact gathering, the look back validates
        its proposals and checks that each new or reworded lesson is owner-specific.
        #496, #1031: ``earlier`` holds scrubbed ``(ref, owner words, answer, local time, outcome, correction words)``
        misses and successes for lessons only. A lesson needs two cited occasions on separate owner days,
        at least one tonight. ``way_notes`` maps refs to the worker's scrubbed notes about the owner's way.
        Facts may end any profile row that existed at ``ends_after`` (the claim).  #583: the same
        judgment may suggest standing preparations; ``preparations`` names the active ones (so they are
        not suggested again) and ``suggest(kept, judgments)`` -> ``(outcomes, calls)`` stores those that
        pass ``validate_suggestions`` as proposals, never while paused.
        """
        from .agent_runtime import MEMORY_OWNER, SECRET_SHAPED_VALUE, memory_value_has_secret
        from .conversation_handoff import JUDGMENT_NO, JUDGMENT_YES
        lessons = WorkingLessons(self.store, self.clock)
        if not lessons.migrated():
            return STATE_UNAVAILABLE, 0, EVENT_UNAVAILABLE, {'sent': False, 'stopped': STOPPED_MIGRATION}
        # #744: the look back learns only from owner words for marked source
        # Works. Filter before gather and synthesis, not from the model's later
        # citations. Unmarked answers retain their existing learning behavior.
        marked = {work for work in [*(source_works or {}).values(),
                  *(work for pair in (earlier_works or {}).values() for work in pair if work)]
                  if self.store.outside_content_sources(work)}
        turns = [(ref, owner, '' if (source_works or {}).get(ref) in marked else answer, *rest)
                 for ref, owner, answer, *rest in turns]
        way_notes = {ref: notes for ref, notes in (way_notes or {}).items()
                     if (source_works or {}).get(ref) not in marked
                     and not marked.intersection((earlier_works or {}).get(ref, ()))}
        from .quickstart_store import owner_words_work
        line_input = lines
        lines, refs, turn_days = dream_turn_input(turns, way_notes)
        # Earlier refs never enter the facts or suggestions gates.
        misses, successes = [], []
        for ref, owner, answer, when, outcome, correction in earlier:
            if outcome not in MISS_OUTCOMES and outcome != 'done':
                continue
            works = (earlier_works or {}).get(ref, ())
            if marked.intersection(works):
                answer = ''
                # Later correction text is eligible only on the owner channel.
                if len(works) < 2 or not owner_words_work(self.store.job(works[1])):
                    correction = ''
            line = (f"[{ref}] {when} ({outcome}) owner: {' '.join(str(owner or '').split())[:DREAM_OWNER_CHARS] or '(withheld)'}"
                    f" | assistant: {' '.join(str(answer or '').split())[:DREAM_ANSWER_CHARS] or 'none'}")
            if outcome in MISS_OUTCOMES:
                line += f" | correction: {' '.join(str(correction or '').split())[:DREAM_CORRECTION_CHARS] or 'none'}"
            line += _dream_way_note_suffix(way_notes.get(ref, ()))
            block = successes if outcome == 'done' else misses
            block.append((ref, str(when)[:10] if when else None, line))
        for block, cap in ((misses, DREAM_EARLIER_CHARS), (successes, DREAM_EARLIER_SUCCESS_CHARS)):
            used = sum(len(line) + 1 for _ref, _day, line in block)
            while block and used > cap:
                used -= len(block.pop(0)[2]) + 1
        earlier_days = {ref: day for ref, day, _line in misses + successes}
        miss_refs = {turn[0] for turn in turns if turn[0] in refs and len(turn) > 4 and turn[4] in MISS_OUTCOMES}
        earlier_miss_refs = {ref for ref, _day, _line in misses}
        earlier_success_refs = {ref for ref, _day, _line in successes}
        earlier_refs = earlier_miss_refs | earlier_success_refs
        lesson_refs = refs.keys() | earlier_refs
        snapshot = profile if callable(profile) else (lambda: profile)
        today = MemoryService(self.store, now=self.clock, private_read_sink=MemoryService.NO_EGRESS_GUARD)._today()

        def lines_now():
            rows = self.store.memories(MEMORY_OWNER, limit=101, key_prefix=PROFILE_PREFIX, holding_on=today)
            return rows, {row['id'][:MemoryService.SHORT_ID_CHARS]: row['memory_key'] for row in rows
                          if not row['memory_key'].startswith(LESSON_PREFIX)}

        # #818: each call's audit row is also linked to the Works its bounded input actually carries
        # (``read_scope``, ``AgentService.judgments_read``).
        scope = read_scope or (lambda works: contextlib.nullcontext())
        shown_works = [source_works[ref] for ref in refs if source_works and ref in source_works]
        shown_earlier_works = [work for ref, _day, _line in misses + successes
                               for work in (earlier_works or {}).get(ref, ()) if work]
        # #416: the first pass only finds facts in the turns; what it keeps is in the profile the look back reads.
        gather_lines, _, _ = dream_turn_input([turn for turn in turns if turn[0] in refs])
        with scope(shown_works):
            found, gather = judgments.owner_model_dream_gather('\n'.join(gather_lines), snapshot(), clock, work_id=job['id'],
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
                                                  ends_after=ends_after, origin=ORIGIN_DREAM, source_works=source_works,
                                                  terms_confidence=gather.confidence)
            first.update(proposed=len(found.get('proposals') or []), applied=gathered, dropped=refused)
        current = {lesson['id']: lesson for lesson in lessons.current()}
        current_text = self._lesson_input(current)
        previous = self.diary_previous(MEMORY_OWNER)
        if stopped == STOPPED_PAUSED or (cancelled and cancelled()):
            return STATE_DONE, calls, EVENT_RECORDED, {'gather': first, 'sent': bool(calls), 'applied': gathered,
                                                       'dropped': [], 'lessons': None, 'diary': None,
                                                       'stopped': stopped or STOPPED_DEADLINE,
                                                       'inputs': {'turns': len(lines), 'earlier_successes': 0}}
        with scope([*shown_works, *shown_earlier_works, *(line_refs or {}).values()]):
            data, decision = judgments.owner_model_dream('\n'.join(lines), snapshot(), current_text,
                                                         previous['content'] if previous else '', clock,
                                                         work_id=job['id'], cancelled=cancelled, preparations=preparations,
                                                         earlier='\n'.join(line for _ref, _day, line in misses),
                                                         earlier_successes='\n'.join(line for _ref, _day, line in successes),
                                                         lines=line_input)
        if line_input and line_read and call_sent(decision):line_read(line_input)
        confidence = decision.confidence
        calls += 1 if call_sent(decision) else 0
        self.count(job['id'], calls, KIND_DREAM)
        detail = {'decision': decision.outcome, 'route': confidence.route or None, 'provider': confidence.provider or None,
                  'model': confidence.model or None, 'observed_model': confidence.observed_model or None,
                  'confidence': confidence.probability, 'sent': bool(calls),
                  'gather': first,
                  'inputs': {'turns': len(lines), 'turns_chars': sum(len(line) for line in lines), 'clock': bool(clock),
                             'earlier_misses': len(misses), 'earlier_successes': len(successes),
                             'working_lessons': len(current), 'working_lessons_chars': len(current_text),
                             'previous_diary': bool(previous)}}
        if data is None:
            detail.update(applied=gathered)
            return (STATE_DONE if gathered else STATE_UNAVAILABLE), calls, (EVENT_RECORDED if gathered else EVENT_UNAVAILABLE), detail
        known, pending = self.known_values(MEMORY_OWNER, job['id'])
        rows, ids = lines_now()
        held = []
        kept, dropped = validate_dream(data.get('proposals'), refs, refs.values(), known, pending, len(rows), ids,
                                       held=held)
        applied, stopped = self._apply_facts(kept, job, dropped, ends_after=ends_after, origin=ORIGIN_DREAM, source_works=source_works, terms_confidence=confidence)
        applied = gathered + applied
        # #425: the look back is quiet; what the owner relies on that it changed, and what it held, waits to be
        # raised once at the next request it matters to (``MemoryRaise``), never as a message of its own.
        full = {row['id'][:MemoryService.SHORT_ID_CHARS]: row['id'] for row in rows}
        changed = [{'kind': RAISE_CHANGED, 'memory_id': row['memory_id']} for row in applied
                   if row.get('memory_id') and (row.get('superseded') or row.get('invalidated'))]
        unsure = [{'kind': RAISE_HELD, 'memory_id': full[item['line']], 'reason': item['reason']} for item in held
                  if item['line'] in full and self.store.memory(full[item['line']], MEMORY_OWNER)]
        if stopped != STOPPED_PAUSED and (changed or unsure):
            MemoryRaise(self.store, self.clock).add(changed + unsure)
        detail['raise'] = {'changed': len(changed), 'held': len(unsure)}
        detail.update(proposed=len(data.get('proposals') or []), applied=applied, dropped=dropped, stopped=stopped,
                      lessons=None, diary=None)
        if stopped != STOPPED_PAUSED:
            ops, change, _blocker, refused = validate_lessons(
                data, {ident: item['text'] for ident, item in current.items()}, known)
            dropped.extend(self._lesson_evidence_drops(refused))
            if ops:
                cited = dream_refs(data.get('lesson_sources'), lesson_refs)
                cited = cited or []
                tonight_cited = set(cited) & refs.keys()
                earlier_cited = {ref: (earlier_works or {}).get(ref, ()) for ref in cited if ref in earlier_refs}
                cited_works = {ref: pair[0] for ref, pair in earlier_cited.items() if pair and pair[0]}
                cited_works.update({ref: source_works[ref] for ref in tonight_cited
                                    if source_works and source_works.get(ref)})
                corrections = {pair[1]: cited_works.get(ref, ref) for ref, pair in earlier_cited.items()
                               if ref in earlier_miss_refs and len(pair) > 1 and pair[1]}
                # Seed the earlier days first so a cited correction keeps the miss's day.
                occasion_days = {cited_works.get(ref, ref): earlier_days[ref] for ref in earlier_cited}
                tonight_occasions = {}
                for ref in tonight_cited:
                    work = cited_works.get(ref, ref)
                    occasion = corrections.get(work, work)
                    tonight_occasions[ref] = occasion
                    occasion_days.setdefault(occasion, turn_days.get(ref))
                days = set(occasion_days.values()) - {None}
                legacy_turns = len(set(tonight_occasions.values())) >= 2 and any(
                    ref not in turn_days and occasion_days[occasion] is None
                    for ref, occasion in tonight_occasions.items())
                detail['lessons_evidence'] = {'tonight': len(tonight_cited),
                                              'tonight_misses': len(tonight_cited & miss_refs),
                                              'earlier_misses': len(earlier_miss_refs.intersection(cited)),
                                              'earlier_successes': len(earlier_success_refs.intersection(cited)),
                                              'rule': None}
                refused = None
                if not cited:
                    refused = 'sources'
                elif len(occasion_days) < 2:
                    refused = 'one-occasion'
                elif not tonight_cited or (len(days) < 2 and not legacy_turns):
                    refused = 'one-day'
                elif len(current) > 1 and sum(item['op'] in ('remove', 'update') for item in ops) * 2 > len(current):
                    refused = 'lessons-rewrite'
                elif self._notice_taken(job['id'], NOTICE_KIND):
                    refused = 'notice-taken'
                if refused:
                    dropped.extend(_lesson_drop(item['op'], refused) for item in ops)
                else:
                    checked = []
                    checks_exhausted = False
                    lesson_works = list(dict.fromkeys(cited_works[ref] for ref in cited if ref in cited_works))
                    for index, item in enumerate(ops):
                        left, stop_reason = self.allowance(self.clock())
                        if stop_reason == STOPPED_PAUSED:
                            stopped = STOPPED_PAUSED
                        elif cancelled and cancelled():
                            stopped = STOPPED_DEADLINE
                        if stopped in (STOPPED_PAUSED, STOPPED_DEADLINE):
                            dropped.extend(_lesson_drop(op['op'], stopped) for op in checked + ops[index:])
                            checked = []
                            break
                        if item['op'] == 'remove':
                            checked.append(item)
                            continue
                        if checks_exhausted or left <= 0:
                            checks_exhausted = True
                            dropped.append(_lesson_drop(item['op'], REFUSED_BUDGET))
                            continue
                        with scope(lesson_works):
                            verdict, check = judgments.lesson_owner_specific(item['text'], item['why'], work_id=job['id'],
                                                                             cancelled=cancelled)
                        if call_sent(check):
                            calls += 1
                            self.count(job['id'], calls, KIND_DREAM)
                            detail['sent'] = True
                        if verdict.outcome == JUDGMENT_YES:
                            checked.append(item)
                        else:
                            reason = 'general-rule' if verdict.outcome == JUDGMENT_NO else 'general-rule-unjudged'
                            dropped.append(_lesson_drop(item['op'], reason))
                    if checked and cancelled and cancelled():
                        stopped = STOPPED_DEADLINE
                        dropped.extend(_lesson_drop(item['op'], stopped) for item in checked)
                        checked = []
                    if checked:
                        self._save_lessons(lessons, current, checked, change, job['id'], judgments, dropped, detail,
                                           origin=ORIGIN_DREAM, sources=lesson_works)
                        if detail.get('lessons'):
                            detail['lessons_evidence']['rule'] = 'occasions'
            stopped = detail.get('stopped') or stopped
        if stopped != STOPPED_PAUSED:
            calls, stopped = self._lines(job, data.get('lines'), refs, line_refs or {}, source_works or {},
                                         judgments, stand, calls, detail, stopped, cancelled)
        if stopped != STOPPED_PAUSED:
            calls, stopped = self._suggest(job, data.get('suggestions'), refs, turn_days, judgments, suggest, calls,
                                           detail, stopped, cancelled)
        # #438: one line per event; each line's spacing is collapsed, the lines are kept.
        diary = '\n'.join(' '.join(line.split()) for line in str(data.get('diary') or '').splitlines() if line.strip())
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
                    # #425: the daily record is never told; it is a search-only record of the owner's turns.
                    memory = self.store.save_memory(key, diary, MEMORY_OWNER, work_id=job['id'], origin=ORIGIN_DREAM)
                except ValueError:
                    dropped.append({'note': 'diary', 'reason': 'store-refused'})
                else:
                    detail['diary'] = {'outcome': APPLIED_MEMORY, 'memory_id': memory['id'], 'date': diary_date,
                                       'chars': len(diary), 'superseded': bool(memory.get('supersedes'))}
        detail['stopped'] = stopped
        return STATE_DONE, calls, EVENT_RECORDED, detail


    def _lines(self, job, items, refs, request_refs, source_works, judgments, stand, calls, detail, stopped, cancelled):
        kept, dropped = validate_line_actions(items, refs, request_refs)
        record = {'proposed': len(items or []), 'kept': [], 'dropped': dropped}
        detail['lines'] = record
        if not kept or stand is None:
            return calls, stopped
        if self.allowance(self.clock())[1] == STOPPED_PAUSED or cancelled and cancelled():
            reason = STOPPED_PAUSED if self.allowance(self.clock())[1] == STOPPED_PAUSED else STOPPED_DEADLINE
            record['dropped'].extend({'line': item['index'], 'reason': reason} for item in kept)
            return calls, reason
        mapping = {**source_works, **request_refs}
        mapped = [{**item, 'sources': [mapping.get(ref, '') for ref in item['sources']]} for item in kept]
        outcomes, used = stand(mapped, judgments)
        calls += used
        if used:
            self.count(job['id'], calls, KIND_DREAM)
            detail['sent'] = True
        for item, outcome in zip(kept, outcomes):
            (record['kept'] if outcome.get('line_id') else record['dropped']).append(
                {'line': item['index'], 'sources': item['sources'], **outcome})
        return calls, stopped

    def _suggest(self, job, suggestions, refs, turn_days, judgments, suggest, calls, detail, stopped, cancelled):
        """#583: the look back's preparation suggestions; returns ``(calls, stopped)`` and records ``detail['suggestions']``.

        Only what passes ``validate_suggestions`` reaches ``suggest``, and nothing does while the owner
        paused upkeep or the run's deadline passed.  The Evidence holds refs, ids and reasons, no text.
        """
        suggestions = suggestions if isinstance(suggestions, list) else []
        if not suggestions:
            return calls, stopped
        kept, dropped = validate_suggestions(suggestions, refs, turn_days)
        record = {'proposed': len(suggestions), 'kept': [], 'dropped': dropped}
        detail['suggestions'] = record
        if not kept or suggest is None:
            return calls, stopped
        if self.allowance(self.clock())[1] == STOPPED_PAUSED:
            record['dropped'].extend({'suggestion': item['index'], 'reason': STOPPED_PAUSED} for item in kept)
            return calls, STOPPED_PAUSED
        if cancelled and cancelled():
            record['dropped'].extend({'suggestion': item['index'], 'reason': STOPPED_DEADLINE} for item in kept)
            return calls, stopped
        outcomes, used = suggest(kept, judgments)
        if used:
            calls += used
            self.count(job['id'], calls, KIND_DREAM)
            detail['sent'] = True
        for item, outcome in zip(kept, outcomes):
            entry = {'suggestion': item['index'], 'sources': item['sources'], **outcome}
            (record['kept'] if outcome.get('preparation_id') else record['dropped']).append(entry)
        return calls, stopped


#: #425: the ``memories.origin`` of what a dream writes (its facts and the daily record).
ORIGIN_DREAM = 'dream'
#: #425: a dream change the owner relies on, or an ending it held, waits this long to be raised.
RAISE_SECONDS = 7 * 86400
RAISE_KEY = 'owner_model.raise'
RAISE_LIMIT = 20
RAISE_CHANGED, RAISE_HELD = 'changed', 'held'
#: What the owner's AI reads on the line (model-facing; it speaks to the owner in their own words).
RAISE_NOTES = {
    RAISE_CHANGED: ('[changed in the overnight look back; before: {before}. If this matters to the current request, '
                    'mention the change in passing so the owner can correct it; otherwise say nothing about it.]'),
    RAISE_HELD: ('[may no longer hold: {reason}. If this matters to the current request, ask the owner once, briefly, '
                 'and say why; otherwise say nothing about it. Change the memory only on the owner\'s answer.]'),
}


class MemoryRaise:
    """#425: what a dream changed that the owner relies on, and the endings it held, until raised once.

    The look back tidies quietly: a new fact or a restated line is applied and listed in 내 기록, with no
    message.  A row it wrote that replaced or ended a line (``changed``), and a line whose ending it
    held because it was unsure (``held``), become one item each, kept in ``config`` (bounded, newest
    first).  An item is shown on its own line in the next brief whose request matches that line; the
    owner's AI then mentions it in passing or asks once, only if it matters (C16: no rule here decides
    what matters), and changes Memory only through its own tools on the owner's answer.  The first
    Work whose brief showed an item consumes it.  An item expires after ``RAISE_SECONDS`` and is
    dropped once its row is no longer current.
    """

    def __init__(self, store, clock=time.time):
        self.store, self.clock = store, clock

    def _items(self):
        items = self.store.config(RAISE_KEY, [])
        return [item for item in items if isinstance(item, dict) and isinstance(item.get('memory_id'), str)
                and item.get('kind') in RAISE_NOTES] if isinstance(items, list) else []

    def add(self, items):
        """Add ``{'kind', 'memory_id', 'reason'?}`` items; the newest replaces an older one for the same row."""
        now = self.clock()
        fresh = [{'kind': item['kind'], 'memory_id': item['memory_id'], 'reason': str(item.get('reason') or ''),
                  'at': now, 'shown_in': None} for item in items]
        if not fresh:
            return
        ids = {item['memory_id'] for item in fresh}
        kept = [item for item in self._items() if item['memory_id'] not in ids and now - float(item.get('at') or 0) < RAISE_SECONDS]
        self.store.put(RAISE_KEY, (fresh + kept)[:RAISE_LIMIT])

    def open(self, work_id=None):
        """``{memory_id: item}`` not expired and not consumed by a Work other than ``work_id``."""
        now = self.clock()
        return {item['memory_id']: item for item in self._items()
                if now - float(item.get('at') or 0) < RAISE_SECONDS
                and (item.get('shown_in') is None or (work_id is not None and item.get('shown_in') == work_id))}

    def shown(self, memory_ids, work_id):
        """The Work ``work_id`` showed these items: they are consumed (a second brief of that Work still shows them)."""
        ids = set(memory_ids)
        if not ids or not work_id:
            return
        items = self._items()
        for item in items:
            if item['memory_id'] in ids and item.get('shown_in') is None:
                item['shown_in'] = work_id
        self.store.put(RAISE_KEY, items)


class WorkingNotes:
    """Legacy reader and #19 mover for the reserved procedure slot."""

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


class WorkingLessons:
    """Canonical lesson rows and their migration from retained working notes."""

    def __init__(self, store, clock=time.time):
        self.store, self.clock = store, clock
        # As ``WorkingNotes`` did: the receipt and tombstone tables these lessons rely on exist from start-up
        # (other readers, such as entrusted lines, query them).
        self.forget = OwnerForget(store, clock)

    def _owner_key(self):
        from .agent_runtime import MEMORY_OWNER
        return self.store._memory_binding(MEMORY_OWNER)

    def current(self, db=None):
        """This owner's current lessons, oldest first."""
        if db is None:
            with self.store.db() as db:
                return self.current(db)
        lessons = []
        rows = db.execute(
            "SELECT * FROM memories WHERE owner_key=? AND state='current' AND substr(memory_key,1,?)=? "
            'ORDER BY created,rowid', (self._owner_key(), len(LESSONS_PREFIX), LESSONS_PREFIX))
        for row in rows:
            sources = json.loads(row['source_works'] or '[]')
            lessons.append({'id': row['memory_key'][len(LESSONS_PREFIX):], 'key': row['memory_key'],
                            'memory_id': row['id'], 'ref': MEMORY_PREFIX + row['id'],
                            'text': row['content'], 'why': row['evidence'] or '', 'saved': row['created'],
                            'work_id': next(iter(sources or ()), None), 'origin': row['origin']})
        return lessons

    @staticmethod
    def _new_key(text, occupied_ids):
        """Use the first wording to reproduce the key even after its row is purged."""
        norm = candidate = normalized(text)
        attempt = 0
        while True:
            ident = hashlib.sha256(candidate.encode()).hexdigest()[:8]
            if ident not in occupied_ids:
                return LESSONS_PREFIX + ident
            attempt += 1
            candidate = f'{norm}\n{attempt}'

    def _save(self, db, key, text, owner_key, work_id, origin, why=None, sources=None, saved_at=None):
        # The anchor Work first (the notice and undo use it), then each cited Work once.
        source_works = list(dict.fromkeys(([work_id] if work_id else []) + [work for work in sources or () if work]))
        return self.store._save_memory(
            db, key, text, owner_key, self.store._work_binding(work_id) if work_id else None,
            origin=origin, source_works=source_works or None, basis=BASIS_INFERRED, evidence=why, saved_at=saved_at)

    @staticmethod
    def _forgotten_texts(db, owner_key):
        rows = db.execute(
            "SELECT m.content FROM owner_state_receipts AS r, json_each(r.items_json, '$.rows') AS item "
            "JOIN memories AS m ON m.id=json_extract(item.value, '$.id') "
            "WHERE r.owner_key=? AND r.kind='memory' AND r.operation IN ('forget','delete') AND r.state=? "
            "AND json_extract(item.value, '$.table')='memories' AND m.owner_key=r.owner_key AND m.state=? "
            'AND substr(m.memory_key,1,?)=?',
            (owner_key, FORGOTTEN, FORGOTTEN, len(LESSONS_PREFIX), LESSONS_PREFIX))
        return {normalized(row['content']) for row in rows}

    def _forgotten(self, db, owner_key, key, text, forgotten_texts, *, add_key=None):
        if normalized(text) in forgotten_texts:
            return True
        digest = self.store.memory_digest(key, text)
        add_digest = self.store.memory_digest(add_key, text) if add_key and add_key != key else digest
        return db.execute(
            'SELECT 1 FROM forgotten_values AS f JOIN owner_state_receipts AS r ON r.id=f.receipt_id '
            "WHERE f.owner_key=? AND f.digest IN (?,?) AND r.operation IN ('forget','delete') LIMIT 1",
            (owner_key, digest, add_digest)).fetchone() is not None

    def _notice(self, db, jobs, kind, fingerprint):
        from .conversation_surface import surface_conversation
        for job in jobs:
            conversation = surface_conversation(job['channel'], job['chat_id'])
            if not conversation:
                continue
            state = 'queued' if job['delivery'] in self.store.DELIVERY_SETTLED else self.store.NOTICE_HELD
            db.execute('INSERT OR IGNORE INTO telegram_notifications'
                       '(id,job_id,chat_id,generation,kind,fingerprint,state,message_id,created,surface) '
                       'VALUES (?,?,?,?,?,?,?,?,?,?)',
                       (str(uuid.uuid4()), job['id'], job['chat_id'], conversation[1], kind,
                        json.dumps(fingerprint), state, None, self.clock(), conversation[0]))
            break

    def apply(self, ops, change, work_id, *, origin, expected_rows=None, sources=None, notify=True, db=None, saved_at=None):
        """Apply validated operations in one transaction, rechecking current state.

        Upkeep supplies the row ids that its judgment saw, so a replaced row
        cannot be updated or removed on the strength of older wording.
        ``sources`` are the cited Work ids, following the anchor Work.
        A caller passing ``db`` already holds the write transaction.
        """
        from .agent_runtime import MEMORY_OWNER, SECRET_SHAPED_VALUE, memory_value_has_secret
        from .current_context import redact_known_secrets
        if not ops:
            return {'applied': [], 'dropped': []}
        owner_key = self._owner_key()
        applied, dropped = {}, {}
        with (self.store.db() if db is None else contextlib.nullcontext(db)) as conn:
            if db is None:
                conn.execute('BEGIN IMMEDIATE')
            db = conn
            current = {lesson['id']: lesson for lesson in self.current(db)}
            forgotten = self._forgotten_texts(db, owner_key)
            # Removals make room for additions in the same answer, regardless of its order.
            for index, item in sorted(enumerate(ops), key=lambda pair: pair[1]['op'] != 'remove'):
                op, ident = item['op'], item['id']
                text, why, reason = item['text'], item['why'], item['reason']
                norm = normalized(text)
                target = current.get(ident)
                refused = None
                if op != 'add' and (target is None or (expected_rows is not None
                        and target['memory_id'] != expected_rows.get(ident))):
                    refused = 'lesson-changed'
                elif op != 'remove' and any(norm == normalized(lesson['text']) for lesson in current.values()):
                    refused = 'unchanged'
                elif op == 'add' and len(current) >= LESSONS_MAX:
                    refused = 'lessons-full'
                else:
                    add_key = self._new_key(text, current) if op != 'remove' else None
                    key = target['key'] if target else add_key
                    values = (text, why, reason) if op == 'remove' else (text, why)
                    if any(memory_value_has_secret(self.store, key, value) for value in values):
                        refused = SECRET_SHAPED_VALUE
                    elif op != 'remove' and self._forgotten(db, owner_key, key, text, forgotten, add_key=add_key):
                        refused = FORGOTTEN_VALUE

                if refused:
                    dropped[index] = _lesson_drop(op, refused)
                    continue
                change_record = {'op': op}
                if target:
                    receipt = self.forget.forget_in(db, MEMORY_OWNER, target['ref'], operation=MIGRATE, work_id=work_id)
                    change_record['receipt'] = receipt['receipt']
                if op == 'remove':
                    memory_id = target['memory_id']
                    db.execute('UPDATE memories SET invalidation_reason=?,invalidated_at=? WHERE id=?',
                               (reason, self.clock(), memory_id))
                    del current[ident]
                else:
                    memory_id = self._save(db, key, text, owner_key, work_id, origin, why,
                                           sources=sources, saved_at=saved_at)['id']
                    ident = key[len(LESSONS_PREFIX):]
                    current[ident] = {'key': key, 'memory_id': memory_id, 'text': text, 'ref': MEMORY_PREFIX + memory_id}
                applied[index] = {**change_record, 'id': ident, 'memory_id': memory_id}

            applied = [applied[index] for index in sorted(applied)]
            if applied and work_id and notify:
                jobs = db.execute('SELECT id,channel,chat_id,delivery FROM jobs WHERE id=?', (work_id,))
                self._notice(db, jobs, NOTICE_KIND,
                             {'change': redact_known_secrets(self.store, change), 'items': applied})
        return {'applied': applied, 'dropped': [dropped[index] for index in sorted(dropped)]}

    def block(self, query=''):
        """Render relevant lessons first, with exact refs and a bounded text budget."""
        from .agent_runtime import MEMORY_OWNER
        current = self.current()
        if not current:
            return ''
        by_id = {lesson['memory_id']: lesson for lesson in current}
        terms = MemoryService.search_terms((query or '')[:500])
        matches = self.store.search_memories(MEMORY_OWNER, terms, limit=LESSONS_MAX, key_prefix=LESSONS_PREFIX)
        ranked = {row['id']: by_id[row['id']] for row in matches if row['id'] in by_id}
        ranked.update((lesson['memory_id'], lesson) for lesson in reversed(current)
                      if lesson['memory_id'] not in ranked)
        heading = ("working_lessons (the assistant's own lessons on how to work for this owner, each saved on its own; "
                   "to drop one the owner does not want, forget_record forget its ref):")
        rendered = []
        used = omitted = 0
        for lesson in ranked.values():
            line = f"- {lesson['text']} ({lesson['ref']})"
            size = len(line) + 1  # Include the newline separating this line from the preceding one.
            if used + size > LESSONS_BRIEF_CHARS:
                omitted += 1
                continue
            rendered.append(line)
            used += size
        if omitted:
            rendered.append(f'{omitted} more working lessons omitted by the context limit; search_memory finds them.')
        return heading + '\n' + '\n'.join(rendered)

    def migrate(self):
        """Restore unique points from retained versions, once, under the same transaction as the notice."""
        from .agent_runtime import memory_value_has_secret
        from .procedures import CURRENT, KEPT, NOTES_SLOT, ProcedureLibrary
        if self.store.config(LESSONS_MIGRATION_KEY) is not None:
            return None
        ProcedureLibrary(self.store, self.clock)
        OwnerForget(self.store, self.clock)
        owner_key = self._owner_key()
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if self.store.config(LESSONS_MIGRATION_KEY, db=db) is not None:
                return None
            versions = db.execute('SELECT source,work_id FROM procedure_versions '
                                  'WHERE slot=? AND state IN (?,?) ORDER BY version',
                                  (NOTES_SLOT, CURRENT, KEPT)).fetchall()
            points, version_points = {}, []
            for version in versions:
                this_version = {normalized(point): point for point in notes_points(version['source'])}
                version_points.append(this_version)
                for norm, point in this_version.items():
                    points.setdefault(norm, (point, version['work_id']))
            current = self.current(db)
            occupied = {lesson['id'] for lesson in current}
            existing = {normalized(lesson['text']) for lesson in current}
            forgotten = self._forgotten_texts(db, owner_key)
            recent = dict.fromkeys(norm for version in reversed(version_points) for norm in version)
            selected = {}
            for norm in recent:
                if len(occupied) >= LESSONS_MAX:
                    break
                point, _ = points[norm]
                if len(point) > POINT_CHARS or norm in existing:
                    continue
                key = self._new_key(point, occupied)
                if memory_value_has_secret(self.store, key, point) or self._forgotten(db, owner_key, key, point, forgotten):
                    continue
                selected[norm] = key
                occupied.add(key[len(LESSONS_PREFIX):])

            # Keep the first appearance's wording, Work attribution and relative order.
            memory_ids = []
            for norm, (point, work_id) in points.items():
                if norm in selected:
                    row = self._save(db, selected[norm], point, owner_key, work_id, ORIGIN_LESSONS_MIGRATION)
                    memory_ids.append(row['id'])
            record = {'at': self.clock(), 'restored': len(memory_ids), 'skipped': len(points) - len(selected),
                      'versions': len(versions), 'memory_ids': memory_ids}
            self.store.put(LESSONS_MIGRATION_KEY, record, db=db)
            if memory_ids:
                settled = self.store.DELIVERY_SETTLED
                placeholders = ','.join('?' for _ in settled)
                jobs = db.execute('SELECT id,channel,chat_id,delivery FROM jobs '
                                  f'ORDER BY CASE WHEN delivery IN ({placeholders}) THEN 0 ELSE 1 END, '
                                  'created DESC,rowid DESC', settled)
                self._notice(db, jobs, RESTORED_NOTICE_KIND, {'count': len(memory_ids), 'memory_ids': memory_ids})
            return record

    def migrated(self):
        """Run the #19 move first, then restore the retained versions."""
        if not self.store.config(MIGRATION_KEY) and not WorkingNotes(self.store, self.clock).migrated():
            return False
        for _attempt in range(MIGRATION_ATTEMPTS):
            if self.store.config(LESSONS_MIGRATION_KEY) is not None:
                return True
            try:
                self.migrate()
            except Exception:  # noqa: BLE001 - retried by the next review and at the next start
                break
        return self.store.config(LESSONS_MIGRATION_KEY) is not None



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
