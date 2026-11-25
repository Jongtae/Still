"""Owner-accepted preparations: reminders and prepared suggestions (SEC-ATTN-01, #659).

The anticipatory half of the secretary, bounded by
docs/secretary-agency-contract.en.md ("Attention in this program"):

* A **preparation** is one durable ``preparations`` row in the owner's
  QuickStore: a kind, a bounded goal text, a due time (UTC seconds) with the
  owner's time zone, an optional tiny recurrence and a delivery channel.
* Rows set up by the owner's AI or the dream are scheduled at once and
  announced with a cancel button. Continuations still propose for the owner.
  The scheduler claims scheduled rows and settles their running Works.
* The existing service loop (``AgentService.start``) asks one indexed query
  per tick.  Nothing due costs no model and no network call.  A due row is
  claimed atomically together with its Work, keyed ``preparation:<id>:<slot>``,
  so a restart can neither run a slot twice nor lose it.
* A ``reminder`` Work carries the reminder text as its already-observed
  result and is delivered by the existing terminal delivery (``deliver_one``);
  no model runs.  A ``prepare`` Work is an ordinary queued Work whose request
  is the goal text; the normal loop (SEC-LOOP-01) runs it and its result is
  kept as the prepared answer.
* Recurrence advances after the run from *now*, so a missed window after
  downtime runs once, not once per missed slot.
* A **watch** (SEC-ATTN-02, #719) is a windowed recurrence: every N minutes
  from ``due`` until ``until``, never more than ``max_runs`` Works and never
  after ``until``.  Each run is an ordinary Work under the existing per-Work
  budget, so a watch costs at most ``max_runs`` bounded Works plus one
  judgment each.
* ``delivery_mode`` ``when_needed`` keeps every run in Still (작업 현황) and
  asks the DecisionEngine one bounded question per finished run - does this
  result need the owner now, and is it new since the last notification?  The
  typed decision (``notify`` / ``quiet``) and its reason are recorded on the
  row and in the run's Evidence; only ``notify`` queues one Telegram message,
  in the same transaction that settles the run.

Nothing here knows about calendars, sites, providers or question kinds: the
model reads the calendar with ``calendar_query`` and schedules with
``schedule_preparation`` like any other goal.
"""

from . import work_status as ws
from .messages import msg
from .prompt_files import prompt
import difflib
import hashlib
import json
import logging
import math
import re
from dateutil.rrule import rrulestr
import time
import uuid
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

LOG = logging.getLogger(__name__)

OWNER = 'local-owner'
KIND_REMINDER, KIND_PREPARE = 'reminder', 'prepare'
KINDS = (KIND_REMINDER, KIND_PREPARE)
#: The whole recurrence language: deterministic, owner-readable, no cron.
RECURRENCES = ('daily', 'weekdays', 'weekly')
#: #719: "every N minutes from due until a deadline" (the model passes
#: ``every_minutes``/``until``; this value is stored, never offered).
RECURRENCE_WINDOW = 'window'
MIN_EVERY_MINUTES, MAX_EVERY_MINUTES = 5, 12 * 60
#: #846: a standing wish runs to the owner's deadline, up to a month.
MAX_WINDOW_SECONDS = 31 * 86400
#: #814: a full local day at the minimum cadence fits; each date has its own budget.
MAX_DAILY_WINDOW_RUNS = 24 * 60 // MIN_EVERY_MINUTES
#: ``delivery_mode``: every result is delivered, or only a ``notify`` decision is.
DELIVERY_ALWAYS, DELIVERY_WHEN_NEEDED = 'always', 'when_needed'
DECISION_NOTIFY, DECISION_QUIET = 'notify', 'quiet'
#: Typed reasons of one run's decision (recorded, shown in Settings).
REASON_JUDGED_NEEDED = 'judged-needed'
REASON_JUDGED_NOT_NEEDED = 'judged-not-needed'
REASON_UNCHANGED = 'same-as-last-notification'
REASON_RUN_FAILED = 'run-did-not-finish'
#: #394: the first run without a result after a run that had one is told once.
REASON_RUN_FAILED_TOLD = 'run-did-not-finish-told'
REASON_RUN_STOPPED = 'run-stopped'
REASON_UNJUDGED = 'judgment-unavailable'
REASON_UNJUDGED_REPEAT = 'judgment-unavailable-already-notified'
REASON_WINDOW_CLOSED = 'window-closed-report'
REASON_PROMISED_REPORT = 'promised-report-due'
#: The Telegram notification kind of a ``notify`` decision.
NOTIFY_KIND = 'preparation_notify'
BLOCKED_NOTICE_PREFIX = 'preparation_blocked:'
# The existing preparation-status notice transport also carries self-change receipts.
SELF_CHANGE_NOTICE_PREFIX = BLOCKED_NOTICE_PREFIX + 'self-change:'
CHECK_RECORD_PREFIX = 'check:'
CHECK_DISPATCH_NOTICE_KIND = BLOCKED_NOTICE_PREFIX + 'check-dispatch'
#: #717, #968: what a run may change of its own preparation, each undone by the owner's restore.
SELF_CHANGE_OPERATIONS = ('edit', 'pause', 'cancel', 'goal', 'check')
EXPIRED_NOTICE_KIND = 'preparation_window_elapsed'
# #867: owner-wide scheduled Work admissions, independent of the chosen AI route.
SCHEDULED_BUDGET_KEY = 'scheduled_work_budget'
SCHEDULED_USAGE_PREFIX = 'scheduled_work_usage:'
SCHEDULED_SKIPPED_PREFIX = 'scheduled_work_skipped:'
SCHEDULED_BUDGET_REASON = 'scheduled-work-daily-budget'
SCHEDULED_BUDGET_NOTICE = 'scheduled_work_budget'
DEFAULT_SCHEDULED_DAILY_CALLS = 300
MAX_SCHEDULED_DAILY_CALLS = 14400  # 50 active watches * 288 slots; 0 disables the limit.
SCHEDULED_USAGE_RETENTION_DAYS = 3  # Today and the preceding two owner-local dates.

NOTIFIED_TEXT_BYTES = 1500
CHANNEL_TELEGRAM, CHANNEL_WEB = 'telegram', 'web'

STATE_PROPOSED, STATE_SCHEDULED, STATE_RUNNING = 'proposed', 'scheduled', 'running'
STATE_DELIVERED, STATE_UNKNOWN, STATE_FAILED, STATE_CANCELLED = 'delivered', 'unknown', 'failed', 'cancelled'
#: #719: a watch whose window closed before its slot could run (downtime).
STATE_EXPIRED = 'expired'
STATE_PAUSED = 'paused'
#: States whose row may still cause a run.
ACTIVE_STATES = (STATE_PROPOSED, STATE_SCHEDULED, STATE_RUNNING, STATE_PAUSED)

#: #582: a proposal the owner declined is not offered again for this long; at most this many are compared.
DECLINED_SECONDS = 30 * 86400
DECLINED_GOALS_LIMIT = 20
#: #583: the ``origin`` of a proposal the daily look-back suggested; one still unanswered this long
#: lapses as ``expired`` (not ``cancelled``, so it is not a decline).
ORIGIN_DREAM = 'dream'
#: #780: the daily plan itself: one run a day in which the owner's AI reads the day and sets up what
#: to have ready.  What that run sets up carries ``ACCEPTED_DAY_PLAN`` and no origin.
ORIGIN_DAY_PLAN = 'day-plan'
#: #798: the periodic account of an entrusted line: one repeating run per line that gives an account
#: of it (what was done and came of it, the rules in force, what was learned, what is open).
ORIGIN_LINE_ACCOUNT = 'line-account'
LINE_ACCOUNT_GOAL = prompt('line_account')
#: #938: the next piece of an entrusted line's work, which a run of that line scheduled itself.
ORIGIN_LINE_PIECE = 'line-piece'
#: #1027: a daily-plan offer to resume an owner line. Its origin does not give it acceptance.
ORIGIN_LINE_RESUME = 'line-resume'
ORIGIN_LINE_PLANNER = 'line-planner'
LINE_RESUME_EVENT = 'line_resume_proposed'
ORIGINS = (ORIGIN_DREAM, ORIGIN_DAY_PLAN, ORIGIN_LINE_ACCOUNT, ORIGIN_LINE_PIECE, ORIGIN_LINE_RESUME, ORIGIN_LINE_PLANNER)
#: #780: when the first daily plan runs, owner-local; the owner changes or cancels it like any preparation.
DAY_PLAN_HOUR, DAY_PLAN_MINUTE = 6, 30
#: #780: how far back a plan run is shown what earlier plan runs set up.
DAY_PLAN_RECORD_SECONDS = 2 * 86400
DAY_PLAN_RECORD_ITEMS = 20
#: #780: the Evidence row saying which prepared results were placed in a Work's context.
OFFERED_EVENT_TOOL = 'prepared_offered'
SUGGESTION_EXPIRY_SECONDS = 7 * 86400
#: Every scheduled dream row is told without waiting for another Work, inside these owner-local hours.
#: Only legacy unaccepted one-offs lapse when their time passes.
SUGGESTION_OFFER_HOURS = range(8, 22)
#: ``accepted_by`` values: who turned a proposal into a schedule.
ACCEPTED_OWNER_REQUEST, ACCEPTED_OWNER_BUTTON, ACCEPTED_OWNER_SETTINGS = 'owner-request', 'owner-button', 'owner-settings'
#: #172: the owner's reply or reaction to the proposal message, judged a plain yes to it.
ACCEPTED_OWNER_ANSWER = 'owner-answer'
#: #984: new work accepted by authenticated owner words delivered to its creating run.
ACCEPTED_OWNER_STEER = 'owner-steer'
#: #717: set up by the owner's AI inside a Work the owner's own typed message started; the owner
#: is told with a cancel button.  ``ACCEPTED_DREAM``: set up by the nightly look back, told the same way.
ACCEPTED_OWNER_WORK, ACCEPTED_DREAM = 'owner-work', 'dream'
#: #780: the daily plan, and what a daily-plan run set up.
ACCEPTED_DAY_PLAN = 'day-plan'
#: #938: a piece a line's run scheduled.  Who stands behind it is inherited from the chain's root row,
#: never from the piece's own call: ``ACCEPTED_LINE_PIECE_OWNER`` when the root is a row the owner
#: accepted (it follows the owner's payment autonomy, as that row does; owner decision 2026-11-18,
#: #973), ``ACCEPTED_LINE_PIECE`` when the root was set up by the owner's AI on its own (self-set).
ACCEPTED_LINE_PIECE, ACCEPTED_LINE_PIECE_OWNER = 'line-piece', 'line-piece-owner'
#: #717: set up without the owner asking; such a run never uses the owner's payment autonomy.
SELF_SET = (ACCEPTED_OWNER_WORK, ACCEPTED_DREAM, ACCEPTED_DAY_PLAN, ACCEPTED_LINE_PIECE)
#: #821: the owner asked for or accepted it; such a run may ask the owner to sign in.  An allowlist:
#: a missing, NULL or unknown ``accepted_by`` is never read as the owner's acceptance.
OWNER_ACCEPTED = (ACCEPTED_OWNER_REQUEST, ACCEPTED_OWNER_BUTTON, ACCEPTED_OWNER_SETTINGS, ACCEPTED_OWNER_ANSWER, ACCEPTED_OWNER_STEER)
#: #968 (#938 D1): rows of an owner-set-up line whose own run may revise its own plan: the owner
#: accepted the row, or it was set up in a Work the owner typed.  Look-back and daily-plan rows do not.
LINE_RUN_ACCEPTED = OWNER_ACCEPTED + (ACCEPTED_OWNER_WORK,)
#: #938: rows whose run may schedule the next piece of its line (D1); the look back's and the daily
#: plan's rows are not among them.
LINE_PIECE_PARENTS = (*OWNER_ACCEPTED, ACCEPTED_OWNER_WORK, ACCEPTED_LINE_PIECE, ACCEPTED_LINE_PIECE_OWNER)
#: #938 (D3, owner decision 2026-11-18): pieces scheduled per line per owner-local day, the pending
#: pieces of the whole store, and how far ahead a piece is due at least.  They hold with the shared
#: daily budget off, so a chain shows up as a count and never as silent spend.
LINE_PIECES_PER_DAY = 24
MAX_PENDING_PIECES = 10
PIECE_LEAD_SECONDS = MIN_EVERY_MINUTES * 60
#: States in which a piece has not started yet: its parent run may still replace it (D6).
PIECE_PENDING_STATES = (STATE_PROPOSED, STATE_SCHEDULED, STATE_PAUSED)
#: #1005: how many of a line's finished pieces Settings lists under the line's one entry.
FINISHED_PIECES_SHOWN = 10
#: #821: a waiting run's continuation refused because its preparation changed while it waited.
WAIT_CHANGED = 'preparation-changed'

GOAL_MAX_CHARS = 500
#: #393: a ``prepare`` goal is the run's brief, which the owner no longer reads
#: inline when an offer is given, so it is not squeezed into a reminder's length.
#: #766: it is the whole request each run executes (a plan, its limits and what to
#: re-check), so the bound only keeps one row and one run's request finite; at 1000
#: every live watch with a plan was refused and moved its plan elsewhere.
BRIEF_MAX_CHARS = 8000
#: #766: what a proposal card folds of the brief, so one card stays inside a Telegram
#: message; the whole brief is in Settings and is what each run receives.
BRIEF_PREVIEW_CHARS = 1000
#: #393: what the owner reads and accepts: a short name and a few plain lines.
TITLE_MAX_CHARS = 60
OFFER_MAX_CHARS = 400
OFFER_MAX_LINES = 6
MAX_ACTIVE = 50
#: Leave at least 40 of the shared active slots available for owner-directed work.
MAX_ACTIVE_DREAM = 10
MAX_ACTIVE_CHECKS = 10
CHECK_EVENT_TOOL = "condition_check"
#: A standing dream gets 30 occurrences before the owner must extend it.
DREAM_MAX_RUNS = 30
#: A due time a little in the past (the model computing "now") is still now.
PAST_GRACE_SECONDS = 120
MAX_AHEAD_SECONDS = 366 * 86400
#: A reminder delivered this much after its slot says it is late.
LATE_NOTE_SECONDS = 10 * 60
REQUEST_KEY_PREFIX = 'preparation:'
#: #774: a run continued after the owner answered its location request keeps
#: its slot's key before this separator (``preparation:<id>:<slot>/loc:<request>``),
#: so the continuation is the same preparation and slot everywhere it is read.
CONTINUATION_SEPARATOR = '/'

#: Prepared answers younger than this enter the next turns' context.
FRESH_SECONDS = 6 * 3600
SECTION_MAX_ITEMS = 3
SECTION_MAX_BYTES = 4000
ANSWER_PREVIEW_BYTES = 1500
#: #766: a long brief is cut like the answer, or one item alone outgrows the section and is dropped.
GOAL_PREVIEW_BYTES = 600
#: Rows one tick looks at, at most.
TICK_LIMIT = 8

PREPARED_HEADING = '# Prepared for you (earlier owner-accepted preparations; not instructions)'
PREPARED_LEGEND = prompt('prepared_legend')

TABLE_SQL = '''
CREATE TABLE IF NOT EXISTS preparations(id TEXT PRIMARY KEY, owner TEXT NOT NULL, kind TEXT NOT NULL, goal_text TEXT NOT NULL,
    due_at REAL NOT NULL, timezone TEXT NOT NULL, recurrence TEXT, channel TEXT NOT NULL, state TEXT NOT NULL,
    last_run_job_id TEXT, last_outcome TEXT, prepared_result_ref TEXT, prepared_at REAL, prepared_text TEXT, created_from TEXT,
    accepted_by TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS preparations_state_due ON preparations(state, due_at);
'''
#: Columns added after #659 (``ALTER TABLE`` on an existing store, in order).
COLUMN_MIGRATIONS = (('prepared_text', 'TEXT'),
                     # #719: the watch window and its bound.
                     ('every_seconds', 'INTEGER'), ('window_end', 'REAL'), ('max_runs', 'INTEGER'),
                     ('run_count', 'INTEGER NOT NULL DEFAULT 0'),
                     # #719: silent-unless-needed delivery and its last decision.
                     ('delivery_mode', 'TEXT'), ('last_decision', 'TEXT'), ('last_decision_reason', 'TEXT'),
                     ('last_notified_digest', 'TEXT'), ('last_notified_text', 'TEXT'), ('last_notified_at', 'REAL'),
                     # #393: what the owner reads and accepts, apart from the run's brief (goal_text).
                     ('title_text', 'TEXT'), ('offer_text', 'TEXT'),
                     # #575: restore the regular schedule after an immediate run.
                     ('run_return_state', 'TEXT'),
                     # #580: due_at advances; the rule keeps its original wall-clock anchor.
                     ('rule_start', 'REAL'),
                     # #583: a look-back suggestion, and when its one proposal message was queued.
                     ('origin', 'TEXT'), ('offered_at', 'REAL'),
                     # #701: a watch's inclusive last-slot bound, apart from its run deadline ``window_end``.
                     ('until_at', 'REAL'),
                     ('window_terms', 'TEXT'),
                     # #798: the entrusted line this row does the work of (``entrusted_lines``), or NULL.
                     ('line_id', 'TEXT'),
                     # #813: settlement time of the last promised-report decision, independent of delivery.
                     ('last_report_at', 'REAL'),
                     # #717: current slot revision, latest undo receipt, and durable owner veto.
                     ('own_run_revision', 'REAL'), ('self_change', 'TEXT'),
                     ('accepted_from', 'TEXT'),
                     ('self_change_undone', 'INTEGER NOT NULL DEFAULT 0'),
                     ('dream_held', 'INTEGER NOT NULL DEFAULT 0'),
                     # #938: a piece's parent run, as that run's slot key (one child per run).
                     ('piece_of', 'TEXT'), ('check_spec', 'TEXT'), ('check_next_at', 'REAL'), ('check_status', 'TEXT'))


def run_seconds():
    """#701: the time one run is given; a slot at ``until`` gets it like any other."""
    from .agent_runtime import WORK_DEADLINE_SECONDS
    return WORK_DEADLINE_SECONDS


def migrate(db):
    """Add the post-#659 columns an older store lacks."""
    present = {row[1] for row in db.execute('PRAGMA table_info(preparations)')}
    for name, kind in COLUMN_MIGRATIONS:
        if name not in present:
            db.execute(f'ALTER TABLE preparations ADD COLUMN {name} {kind}')
    db.execute('CREATE INDEX IF NOT EXISTS preparations_check_due ON preparations(state, check_next_at) '
               'WHERE check_spec IS NOT NULL')
    db.execute('CREATE INDEX IF NOT EXISTS preparations_line ON preparations(line_id)')
    # #701: a watch stored before ``until_at`` ran only strictly before ``window_end``; the same slots
    # are those up to one second before it, and its last slot gets a run's time after that.
    db.execute('UPDATE preparations SET until_at=window_end-1, window_end=window_end-1+? '
               'WHERE recurrence=? AND until_at IS NULL AND window_end IS NOT NULL',
               (run_seconds(), RECURRENCE_WINDOW))
    # #814: once per store, repair the end without exceeding the accepted count
    # unless the retained creating call proves this was the silent default.
    migration = 'preparations_daily_cap_814'
    if not db.execute('SELECT 1 FROM config WHERE key=?', (migration,)).fetchone():
        for record in db.execute('SELECT * FROM preparations WHERE recurrence=? AND max_runs=96 '
                                 'AND state IN (?,?,?,?)', (RECURRENCE_WINDOW, *ACTIVE_STATES)).fetchall():
            row = dict(record)
            try:
                terms = stored_window_terms(row)
                if not isinstance(terms, dict) or zone(row['timezone']) is None:
                    continue
                if not row['every_seconds'] or row['every_seconds'] % 60:
                    continue
                rhythm = terms.get('recurrence')
                done = int(row.get('run_count') or 0)
                start = row.get('rule_start')
                if rhythm:
                    tz = zone(row['timezone'])
                    day = datetime.fromtimestamp(row['due_at'], tz).date()
                    opening = datetime.fromtimestamp(start, tz).time()
                    start = datetime.combine(day, opening, tzinfo=tz).timestamp()
                elif start is None:
                    start = row['due_at'] - row['every_seconds'] * max(0, done - (row['state'] == STATE_RUNNING))
                _permitted, slots = daily_window_slots(row['every_seconds'], start, row['until_at'], row['timezone'], rhythm)
                if slots <= 96:
                    continue
                requested = row['until_at']
                implicit = _legacy_window_used_default(db, row, start, rhythm)
                window = normalize_window(row['every_seconds'] // 60,
                                          datetime.fromtimestamp(requested, zone(row['timezone'])).isoformat(),
                                          None if implicit else row['max_runs'],
                                          start, row['timezone'], start - 1, recurrence=rhythm)
                every, until, runs = window
                if not implicit:
                    runs = row['max_runs']
                    until = last_slot({**row, 'until_at': until})
                window = every, until, runs
                updated_terms = window_terms(window, rhythm, start, row['timezone'],
                                             terms.get('report_seconds', 0) // 60 or None, row.get('delivery_mode'), requested)
            except (ValueError, TypeError, KeyError, ArithmeticError):
                # One invalid legacy window must not prevent this store from opening.
                continue
            db.execute('UPDATE preparations SET max_runs=?,until_at=?,window_end=?,window_terms=?,rule_start=? WHERE id=?',
                       (runs, until, until + run_seconds(), updated_terms, row.get('rule_start') or start, row['id']))
        db.execute('INSERT INTO config VALUES (?,?)', (migration, 'true'))


def _legacy_window_used_default(db, row, start, rhythm):
    """Only expand a window whose unchanged creating call proves omission.

    Normal tool Evidence retains arguments and a preparation ID, paired by
    scope/call ID by ``trail_steps``. Failure details, digests and normalized
    proposal bindings alone cannot prove omission. Settings' conversation
    audit is bounded and direct Settings edits retain no arguments/revision;
    after any update we cannot prove which call last set the window. Runs also
    update the row, so consumed windows conservatively retain their bound.
    """
    if (not row.get('created_from') or row['updated_at'] != row['created_at']
            or row.get('run_count')):
        return False
    from .agent_runtime import trail_steps
    events = db.execute('SELECT tool,status,detail FROM tool_events WHERE job_id=? ORDER BY id',
                        (row['created_from'],)).fetchall()
    calls = []
    try:
        steps = trail_steps(events)
    except (ValueError, TypeError, KeyError):
        return False
    for _tool, action, state, detail, _reason in steps:
        evidence = detail.get('evidence')
        if (action == 'schedule_preparation' and state == 'succeeded' and isinstance(evidence, dict)
                and evidence.get('preparation_id') == row['id']):
            calls.append(detail)
    # A duplicate/idempotent call can return the same row with other arguments.
    if len(calls) != 1:
        return False
    args = calls[0].get('arguments')
    if not isinstance(args, dict) or 'max_runs' in args:
        return False
    try:
        due, _tz = parse_due(args.get('due'), row['timezone'], row['timezone'], start - 1)
        until, _tz = parse_due(args.get('until'), row['timezone'], row['timezone'], start - 1)
        return (due == start == row['due_at'] and until == row['until_at']
                and _whole_number(args.get('every_minutes')) * 60 == row['every_seconds']
                and normalize_recurrence(args.get('recurrence')) == rhythm
                and calls[0]['evidence'].get('max_runs') == row['max_runs'])
    except (ValueError, TypeError):
        return False


REFUSALS = {
    'self_change_refused': msg('preparations.self_change_refused'),
    'limit': msg('preparations.dream_limit', maximum=MAX_ACTIVE_DREAM),
    'declined_before': ('소유자가 최근에 같은 준비 제안을 거절했습니다. 다시 제안하지 말고, 지금 요청에 대한 답만 하세요. '
                        '소유자가 직접 요청하면 그때 예약할 수 있습니다.'),
    'invalid_kind': 'kind는 reminder(정해진 때에 이 문장을 알려 주기) 또는 prepare(정해진 때에 이 목표를 미리 처리해 두기)입니다.',
    # #617: the detail (empty, or over the kind's limit) is the whole reason; no contradictory prefix.
    'invalid_goal': '',
    'offer_required': ('a prepare needs offer: one plain line the owner reads to accept it (what you will have ready and when), '
                       'and a short title. Nothing was scheduled; call again with offer.'),
    'invalid_offer': (f'title은 {TITLE_MAX_CHARS}자 이내의 짧은 이름, offer는 {OFFER_MAX_LINES}줄·'
                      f'{OFFER_MAX_CHARS}자 이내로 적어 주세요.'),
    'invalid_due': 'due는 RFC3339 시각입니다(예: 2026-09-28T09:00:00+09:00). 오프셋이 없으면 timezone이 필요합니다.',
    'timezone_unknown': '시간대를 알 수 없습니다. due에 오프셋을 붙이거나 timezone(예: Asia/Seoul)을 주세요.',
    'due_in_past': '이 시각은 이미 지났습니다.',
    'due_too_far': '1년 이내의 시각만 예약할 수 있습니다.',
    'invalid_recurrence': 'recurrence는 비우거나 daily, weekdays, weekly 중 하나입니다.',
    'invalid_window': msg('preparations.invalid_window'),
    'window_retry_required': ('같은 목표의 반복 watch 요청이 이 Work에서 이미 있었습니다. 날짜별 단발 준비 여러 건으로 나누지 말고, '
                              'every_minutes와 until을 바로잡아 같은 watch를 다시 호출하세요. 필요한 값이 없으면 대체 일정을 만들지 말고 '
                              '소유자에게 한 가지 질문을 하세요. 이 호출은 새 준비를 만들지 않았습니다.'),
    'invalid_max_runs': msg('preparations.invalid_max_runs', daily_limit=MAX_DAILY_WINDOW_RUNS),
    'invalid_delivery': 'delivery when_needed는 kind prepare에만 쓸 수 있습니다.',
    'too_many': f'진행 중인 준비가 {MAX_ACTIVE}개를 넘어 더 만들지 않았습니다. 설정에서 필요 없는 준비를 정리해 주세요.',
    'not_found': '이 준비를 찾지 못했습니다.',
    'invalid_check': 'A check requires a prepare window and the arguments of a successful code_run.',
    'check_not_proven': 'Run these code_run arguments in this Work first. The run must exit 0 without a state change or approval request.',
    'check_credential': 'Use a secret set for credentials in a condition check.',
    'check_widened': msg('checks.widened'),
    'check_window': msg('checks.window'),
    'too_many_checks': 'Still allows up to 10 scheduled or running condition checks.',
    'line_standing_link': msg('lines.standing_link'),
    'line_executor_exists': msg('lines.refused_line_executor_exists'),
    'not_proposed': '이미 처리된 준비입니다.',
    'expired': '지켜보는 기간이 이미 끝나 시작하지 않았습니다.',
    'running': '지금 실행 중인 준비는 지울 수 없습니다. 먼저 취소하세요.',
    **{code: msg('preparations.' + code) for code in
       ('invalid_rrule', 'already_running', 'invalid_state', 'goal_not_editable', 'invalid_edit', 'resume_expired', 'queue_full',
        'replace_not_found', 'replace_not_allowed', 'line_inactive', 'line_piece_invalid', 'line_piece_exists',
        'invalid_line_resume_offer', 'line_resume_unavailable', 'line_resume_already_offered', 'line_resume_stale')},
    # #938: the D3 bounds of a line's pieces.
    'line_piece_too_soon': msg('preparations.line_piece_too_soon', minutes=PIECE_LEAD_SECONDS // 60),
    'line_pieces_daily': msg('preparations.line_pieces_daily', maximum=LINE_PIECES_PER_DAY),
    'line_pieces_limit': msg('preparations.line_pieces_limit', maximum=MAX_PENDING_PIECES),
    'line_stopped': msg('lines.refused_line_stopped'),
}


class PreparationRefusal(ValueError):
    """A typed refusal the broker turns into a ``ToolError``."""

    def __init__(self, code, detail=''):
        super().__init__(' '.join(part for part in (REFUSALS[code], detail) if part))
        self.code = code


# --- pure helpers (fake-clock unit tests) -----------------------------------

def zone(name):
    """An IANA zone or a fixed ``+HH:MM`` offset, or None."""
    if not isinstance(name, str) or not name:
        return None
    if name[0] in '+-' and len(name) == 6 and name[3] == ':':
        try:
            hours, minutes = int(name[1:3]), int(name[4:6])
        except ValueError:
            return None
        if hours > 18 or minutes > 59:
            return None
        sign = 1 if name[0] == '+' else -1
        return dt_timezone(sign * timedelta(hours=hours, minutes=minutes))
    if len(name) > 64 or name.startswith(('/', '.')) or '..' in name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def offset_name(parsed):
    """``+HH:MM`` for a timezone-aware datetime's offset."""
    total = int(parsed.utcoffset().total_seconds() // 60)
    sign = '+' if total >= 0 else '-'
    total = abs(total)
    return f'{sign}{total // 60:02d}:{total % 60:02d}'


def local_text(ts, timezone_name, home_zone=None):
    """Show a stored UTC time in the home zone when known."""
    home = zone(home_zone)
    tz = home or zone(timezone_name) or dt_timezone.utc
    local = datetime.fromtimestamp(ts, tz)
    weekday = local.strftime('%a')
    return local.strftime('%Y-%m-%d %H:%M') + (f' ({weekday})' if home else f" ({weekday}, {timezone_name or 'UTC'})")


def iso(ts, timezone_name=None):
    tz = zone(timezone_name) or dt_timezone.utc
    return datetime.fromtimestamp(ts, tz).isoformat(timespec='minutes')


def parse_due(due, timezone_name, default_timezone, now):
    """``(due_at, timezone)`` for one proposal, or a typed refusal.

    ``due`` is RFC3339; with no offset it is wall-clock time in ``timezone``
    (or the owner's zone).  The stored zone is the named IANA zone when one
    is known, otherwise the explicit offset, so a recurrence keeps the
    owner's wall-clock time.
    """
    if not isinstance(due, str) or not due.strip() or len(due) > 40:
        raise PreparationRefusal('invalid_due')
    try:
        parsed = datetime.fromisoformat(due.strip().replace('Z', '+00:00'))
    except ValueError:
        raise PreparationRefusal('invalid_due') from None
    named = timezone_name if zone(timezone_name) is not None else None
    if timezone_name and named is None:
        raise PreparationRefusal('timezone_unknown')
    if named is None and zone(default_timezone) is not None:
        named = default_timezone
    if parsed.tzinfo is None:
        if named is None:
            raise PreparationRefusal('timezone_unknown')
        parsed = parsed.replace(tzinfo=zone(named))
    stored = named or offset_name(parsed)
    due_at = parsed.timestamp()
    if not math.isfinite(due_at):
        raise PreparationRefusal('invalid_due')
    if due_at < now - PAST_GRACE_SECONDS:
        raise PreparationRefusal('due_in_past', f'지금은 {local_text(now, stored)}입니다.')
    if due_at > now + MAX_AHEAD_SECONDS:
        raise PreparationRefusal('due_too_far')
    return max(due_at, now), stored


def normalize_recurrence(value):
    if value in (None, '', 'none'):
        return None
    if isinstance(value, str) and value.upper().startswith('RRULE:'):
        return normalize_rrule(value)
    if value not in RECURRENCES:
        raise PreparationRefusal('invalid_recurrence')
    return value


# #580: validate our bounded language before handing anything to dateutil.
RULE_PARTS = ('FREQ', 'INTERVAL', 'BYDAY', 'BYMONTHDAY', 'UNTIL', 'COUNT')
RULE_DAYS = ('MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU')


def normalize_rrule(value):
    """A single bounded RFC 5545 rule, never arbitrary dateutil input (#580)."""
    if not isinstance(value, str) or not value or len(value) > 512:
        raise PreparationRefusal('invalid_rrule')
    text = value.strip().upper()
    if text.startswith('RRULE:'):
        text = text[6:]
    parts = {}
    for part in text.split(';'):
        pair = part.split('=')
        if len(pair) != 2 or pair[0] not in RULE_PARTS or pair[0] in parts or not pair[1]:
            raise PreparationRefusal('invalid_rrule')
        parts[pair[0]] = pair[1]
    if parts.get('FREQ') not in ('DAILY', 'WEEKLY', 'MONTHLY') or 'COUNT' in parts and 'UNTIL' in parts:
        raise PreparationRefusal('invalid_rrule')
    for key, maximum in (('INTERVAL', 12), ('COUNT', 366)):
        if key in parts:
            if not re.fullmatch(r'[0-9]{1,3}', parts[key]) or not 1 <= int(parts[key]) <= maximum:
                raise PreparationRefusal('invalid_rrule')
            parts[key] = str(int(parts[key]))
    if 'BYDAY' in parts:
        days = []
        for day in parts['BYDAY'].split(','):
            match = re.fullmatch(r'([+-]?[1-9][0-9]?)?(MO|TU|WE|TH|FR|SA|SU)', day)
            if not match or match[1] and (parts['FREQ'] != 'MONTHLY' or abs(int(match[1])) > 53):
                raise PreparationRefusal('invalid_rrule')
            days.append((str(int(match[1])) if match[1] else '') + match[2])
        parts['BYDAY'] = ','.join(dict.fromkeys(days))
    if 'BYMONTHDAY' in parts:
        days = parts['BYMONTHDAY'].split(',')
        if any(not re.fullmatch(r'(?:[1-9]|[12][0-9]|3[01]|-1)', day) for day in days):
            raise PreparationRefusal('invalid_rrule')
        # RFC 5545 excludes BYMONTHDAY on weekly rules.
        if parts['FREQ'] == 'WEEKLY':
            raise PreparationRefusal('invalid_rrule')
        parts['BYMONTHDAY'] = ','.join(dict.fromkeys(days))
    if 'UNTIL' in parts:
        until = parts['UNTIL']
        try:
            if not re.fullmatch(r'[0-9]{8}(?:T[0-9]{6}Z?)?', until):
                raise ValueError()
            datetime.strptime(until.rstrip('Z'), '%Y%m%d' if len(until) == 8 else '%Y%m%dT%H%M%S')
        except ValueError:
            raise PreparationRefusal('invalid_rrule') from None
    return 'RRULE:' + ';'.join(key + '=' + parts[key] for key in RULE_PARTS if key in parts)


def rule_parts(recurrence):
    return dict(part.split('=') for part in recurrence[6:].split(';'))


def next_rule_slot(due_at, timezone_name, recurrence, now, rule_start=None, run_count=0):
    """Expand naive local times and localise candidates, preserving DST wall time (#580)."""
    recurrence = normalize_rrule(recurrence)
    parts = rule_parts(recurrence)
    if 'COUNT' in parts and run_count >= int(parts['COUNT']):
        return None
    tz = zone(timezone_name) or dt_timezone.utc
    start = datetime.fromtimestamp(rule_start if rule_start is not None else due_at, tz).replace(tzinfo=None)
    # UNTIL dates include the entire local day; UTC bounds are converted to local
    # before parsing a naive rule. No TZID or other parser extensions are accepted.
    if 'UNTIL' in parts:
        until = parts['UNTIL']
        if len(until) == 8:
            until += 'T235959'
        elif until.endswith('Z'):
            until = datetime.strptime(until, '%Y%m%dT%H%M%SZ').replace(tzinfo=dt_timezone.utc).astimezone(tz).strftime('%Y%m%dT%H%M%S')
        parts['UNTIL'] = until
    text = 'RRULE:' + ';'.join(key + '=' + value for key, value in parts.items())
    rule = rrulestr(text + ';WKST=MO', dtstart=start)
    threshold = max(now, due_at)
    wall = datetime.fromtimestamp(threshold, tz).replace(tzinfo=None)
    for candidate in rule.xafter(wall):
        local = candidate.replace(tzinfo=tz)
        slot = local.timestamp()
        # Ignore nonexistent spring-forward wall times rather than shifting them.
        if datetime.fromtimestamp(slot, tz).replace(tzinfo=None) != candidate:
            continue
        if slot > threshold:
            return slot
    return None


def normalize_goal(goal, limit=GOAL_MAX_CHARS, kind=KIND_REMINDER):
    """The goal with whitespace collapsed, or ``invalid_goal`` saying the kind's limit (#574: one retry is enough)."""
    goal = ' '.join(str(goal or '').split())
    if not goal:
        raise PreparationRefusal('invalid_goal', 'goal이 비어 있습니다. 채워서 한 번 다시 호출하세요.')
    if len(goal) > limit:
        raise PreparationRefusal('invalid_goal', f'kind {kind}의 goal은 {limit}자 이내인데 지금 {len(goal)}자입니다. '
                                                 f'{limit}자 이내로 줄여서 한 번 다시 호출하세요.')
    return goal


def normalize_offer(title, offer):
    """``(title, offer)`` the owner reads and accepts (#393), each None when not given.

    The title is one short line; the offer keeps its line breaks (one point a
    line) with each line's whitespace collapsed.  Content is the AI's; only
    its shape is bounded here.
    """
    title = ' '.join(str(title or '').split()) or None
    lines = [' '.join(line.split()) for line in str(offer or '').splitlines()]
    offer = '\n'.join(line for line in lines if line) or None
    if title and len(title) > TITLE_MAX_CHARS:
        raise PreparationRefusal('invalid_offer')
    if offer and (len(offer) > OFFER_MAX_CHARS or offer.count('\n') >= OFFER_MAX_LINES):
        raise PreparationRefusal('invalid_offer')
    return title, offer


def _whole_number(value):
    """An integer argument (a JSON number or its digits), or None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def window_run_cap(due_at, until_at, timezone_name, recurrence=None):
    """One allowance per started 24 hours; an inclusive endpoint adds no extra day."""
    days = 1 if recurrence else max(1, math.ceil((until_at - due_at) / 86400))
    return days * MAX_DAILY_WINDOW_RUNS


def daily_window_slots(every, due_at, until_at, timezone_name, recurrence=None, *, used=None, allowance=None):
    """Return (permitted prefix, all slots), stopping before any date exceeds its budget.

    One-off windows use elapsed time; day-level rhythms use wall time, like
    next_window_slot. Keep a continuous window rather than silently skipping
    the rest of an over-budget day and resuming on another date. Edits supply
    consumed checks by date and the remaining whole-window allowance.
    """
    tz = zone(timezone_name)
    start = datetime.fromtimestamp(due_at, tz).replace(tzinfo=None) if recurrence else due_at
    end = datetime.fromtimestamp(until_at, tz).replace(tzinfo=None) if recurrence else until_at
    span = (end - start).total_seconds() if recurrence else end - start
    slots = math.floor(span / every) + 1
    counts = dict(used or {})
    cap = window_run_cap(due_at, until_at, timezone_name, recurrence) if allowance is None else allowance
    for index in range(slots):
        local = start + timedelta(seconds=index * every) if recurrence else datetime.fromtimestamp(start + index * every, tz)
        date = local.date()
        counts[date] = counts.get(date, 0) + 1
        if counts[date] > MAX_DAILY_WINDOW_RUNS:
            return min(index, cap), slots
    return min(slots, cap), slots


def normalize_window(every_minutes, until, max_runs, due_at, timezone_name, now, recurrence=None):
    """``(every_seconds, until_at, max_runs)`` of one watch (#719), or a typed refusal.

    #701: the window is ``[due_at, until]``, inclusive like RFC 5545 ``UNTIL``:
    a slot at ``until`` itself runs, none after it.  The run count is the
    number of slots in it, lowered by ``max_runs`` when given. Each local date
    is bounded by ``MAX_DAILY_WINDOW_RUNS``. A budget shortfall stores the real
    last check as the end. ``until`` is parsed like ``due`` (same zone rules).
    """
    minutes = _whole_number(every_minutes)
    if minutes is None or not MIN_EVERY_MINUTES <= minutes <= MAX_EVERY_MINUTES:
        raise PreparationRefusal('invalid_window', msg('preparations.window_minutes', value=repr(every_minutes),
                                                     minimum=MIN_EVERY_MINUTES, maximum=MAX_EVERY_MINUTES))
    if until in (None, ''):
        raise PreparationRefusal('invalid_window', msg('preparations.window_until_missing', value=repr(until)))
    try:
        until_at, _zone = parse_due(until, timezone_name, timezone_name, now)
    except PreparationRefusal as exc:
        reason = ('order' if exc.code == 'due_in_past' else
                  'span' if exc.code == 'due_too_far' else 'invalid')
        raise PreparationRefusal('invalid_window', msg('preparations.window_until_' + reason,
                                 value=repr(until), due=iso(due_at), days=MAX_WINDOW_SECONDS // 86400)) from None
    if until_at <= due_at:
        raise PreparationRefusal('invalid_window', msg('preparations.window_until_order',
                                 value=repr(until), due=iso(due_at)))
    if until_at - due_at > MAX_WINDOW_SECONDS:
        raise PreparationRefusal('invalid_window', msg('preparations.window_until_span',
                                 value=repr(until), due=iso(due_at), days=MAX_WINDOW_SECONDS // 86400))
    every = minutes * 60
    if recurrence:
        tz = zone(timezone_name)
        first = datetime.fromtimestamp(due_at, tz).replace(tzinfo=None)
        last = datetime.fromtimestamp(until_at, tz).replace(tzinfo=None)
        if first.date() != last.date() or last <= first:
            raise PreparationRefusal('invalid_window', msg('preparations.recurring_window_same_day'))
    permitted, slots = daily_window_slots(every, due_at, until_at, timezone_name, recurrence)
    if max_runs in (None, ''):
        runs = permitted
    else:
        runs = _whole_number(max_runs)
        if runs is None or not 1 <= runs <= window_run_cap(due_at, until_at, timezone_name, recurrence):
            raise PreparationRefusal('invalid_max_runs')
        runs = min(runs, permitted)
    if runs == permitted and permitted < slots:
        if recurrence:
            until_at = (first + timedelta(seconds=every * (runs - 1))).replace(tzinfo=tz).timestamp()
        else:
            until_at = due_at + every * (runs - 1)
    return every, until_at, runs


def window_terms(window, recurrence, due_at, timezone_name, report_every_minutes, delivery_mode, requested_until=None):
    """Validate the schedule facts the owner's AI supplied and preserve the local closing time."""
    if not window:
        if report_every_minutes not in (None, ''):
            raise PreparationRefusal('invalid_window', msg('preparations.report_interval'))
        return None
    every, until, runs = window
    terms = {}
    if recurrence:
        tz = zone(timezone_name)
        start, end = datetime.fromtimestamp(due_at, tz), datetime.fromtimestamp(until, tz)
        if recurrence not in RECURRENCES or start.date() != end.date() or start.time() > end.time():
            raise PreparationRefusal('invalid_window', msg('preparations.recurring_window_same_day'))
        terms.update(recurrence=recurrence, end_time=end.time().isoformat())
    if report_every_minutes not in (None, ''):
        seconds = (_whole_number(report_every_minutes) or 0) * 60
        if delivery_mode != DELIVERY_WHEN_NEEDED or seconds < every or seconds % every:
            raise PreparationRefusal('invalid_window', msg('preparations.report_interval'))
        terms['report_seconds'] = seconds
    if requested_until is not None and requested_until >= until + every:
        # A daily budget shortfall (or a retained legacy bound) shortens the end.
        terms['requested_until'] = requested_until
        for minutes in range(MIN_EVERY_MINUTES, math.floor((requested_until - due_at) / 60) + 2):
            permitted, slots = daily_window_slots(minutes * 60, due_at, requested_until, timezone_name, recurrence)
            if permitted == slots and slots <= runs:
                terms['covering_every_minutes'] = minutes
                break
    return json.dumps(terms, sort_keys=True) if terms else None


def stored_window_terms(row):
    return json.loads(row.get('window_terms') or '{}')


def next_day_window(row, now):
    """Find the next future slot, skipping elapsed and nonexistent local windows."""
    terms = stored_window_terms(row)
    recurrence = terms.get('recurrence')
    if not recurrence:
        return {}
    tz = zone(row['timezone'])
    anchor = datetime.fromtimestamp(row['rule_start'], tz)
    today = datetime.fromtimestamp(now, tz).replace(hour=0, minute=0, second=0, microsecond=0)
    after = max(row['until_at'], today.timestamp() - 1)
    for _ in range(8):
        start = next_due(row['rule_start'], row['timezone'], recurrence, after)
        local = datetime.fromtimestamp(start, tz)
        end = datetime.fromisoformat(f'{local.date()}T{terms["end_time"]}').replace(tzinfo=tz)
        until = end.timestamp()
        # The wall time of a skipped DST hour is not part of the accepted window.
        if local.time() == anchor.time() and datetime.fromtimestamp(until, tz).time() == end.time():
            future = start if start > now else next_window_slot(
                {**row, 'due_at': start, 'until_at': until, 'run_count': 0}, now)
            if future is not None:
                return dict(due_at=future, until_at=until, window_end=until + run_seconds(), run_count=0)
        after = max(start, now)
    return {}


def window_report_reason(row, now):
    """The report owed at settlement, independent of skipped slots or result equality."""
    if row.get('recurrence') != RECURRENCE_WINDOW or row.get('run_return_state'):
        return None
    if next_window_slot(row, now) is None:
        return REASON_WINDOW_CLOSED
    terms = stored_window_terms(row)
    seconds = terms.get('report_seconds')
    if seconds:
        first = row['rule_start']
        last_report = row.get('last_report_at')
        if terms.get('recurrence'):
            tz = zone(row['timezone'])
            local_slot = datetime.fromtimestamp(row['due_at'], tz).replace(tzinfo=None)
            anchor = datetime.fromtimestamp(first, tz)
            opening = datetime.combine(local_slot.date(), anchor.time())
            elapsed_now = (datetime.fromtimestamp(now, tz).replace(tzinfo=None) - opening).total_seconds()
            elapsed_sent = ((datetime.fromtimestamp(last_report, tz).replace(tzinfo=None) - opening).total_seconds()
                            if last_report is not None else 0)
        else:
            elapsed_now = now - first
            elapsed_sent = last_report - first if last_report is not None else 0
        due_index = math.floor(elapsed_now / seconds)
        sent_index = max(0, math.floor(elapsed_sent / seconds))
        if due_index >= 1 and due_index > sent_index:
            return REASON_PROMISED_REPORT
    return None


def next_window_slot(row, now):
    """The next slot of a watch strictly after ``now``, or None (#719).

    None once ``max_runs`` Works have started or the next slot is after
    ``until_at`` (#701).  Slots missed while Still was down are skipped, as for
    the other recurrences.
    """
    every, end = row.get('every_seconds'), row.get('until_at')
    if not every or end is None or int(row.get('run_count') or 0) >= int(row.get('max_runs') or 0):
        return None
    terms = stored_window_terms(row)
    if terms.get('recurrence'):
        tz = zone(row['timezone'])
        opening = datetime.fromtimestamp(row['rule_start'], tz).time()
        local_due = datetime.fromtimestamp(row['due_at'], tz)
        local_end = datetime.fromtimestamp(end, tz).replace(tzinfo=None)
        wall_slot = datetime.combine(local_due.date(), opening)
        cadence = timedelta(seconds=every)
        after = max(row['due_at'], now)
        while wall_slot <= local_end:
            slot = wall_slot.replace(tzinfo=tz).timestamp()
            if slot > after and datetime.fromtimestamp(slot, tz).replace(tzinfo=None) == wall_slot:
                return slot
            wall_slot += cadence
        return None
    slot = row['due_at'] + every * (int(max(0.0, now - row['due_at']) // every) + 1)
    return slot if slot <= end else None


def next_due(due_at, timezone_name, recurrence, now, rule_start=None, run_count=0):
    """The first slot of ``recurrence`` strictly after ``now`` (and ``due_at``), or None.

    Computed on the owner's wall clock, so a daily 11:30 stays 11:30 across a
    DST change.  Slots missed during downtime are skipped, never replayed.
    """
    if not recurrence:
        return None
    if recurrence.startswith('RRULE:'):
        return next_rule_slot(due_at, timezone_name, recurrence, now, rule_start, run_count)
    tz = zone(timezone_name) or dt_timezone.utc
    first = datetime.fromtimestamp(due_at, tz)
    day = max(first.date(), datetime.fromtimestamp(now, tz).date()) - timedelta(days=1)
    for _ in range(16):
        day += timedelta(days=1)
        if recurrence == 'weekdays' and day.weekday() >= 5:
            continue
        if recurrence == 'weekly' and day.weekday() != first.weekday():
            continue
        slot = datetime.combine(day, first.time(), tzinfo=tz).timestamp()
        if slot > now and slot > due_at:
            return slot
    raise AssertionError('recurrence has no slot within 16 days')  # pragma: no cover - unreachable


def request_key(preparation_id, due_at):
    """The Work request key of one slot: the idempotency key of a run."""
    return f'{REQUEST_KEY_PREFIX}{preparation_id}:{int(due_at)}'


def slot_key(work_request_key):
    """The slot's own request key of a run or of its continuation (#774)."""
    return str(work_request_key or '').split(CONTINUATION_SEPARATOR, 1)[0]


def preparation_of(work_request_key):
    """The preparation id a Work was started for (or continues, #774), or None."""
    key = slot_key(work_request_key)
    if not key.startswith(REQUEST_KEY_PREFIX):
        return None
    return key[len(REQUEST_KEY_PREFIX):].rsplit(':', 1)[0] or None


def preparation_lineage(db, preparation_id):
    """Read the host's line linkage, including after deleting a completed row."""
    row = db.execute('SELECT line_id,origin,created_from,piece_of FROM preparations WHERE id=?',
                     (preparation_id,)).fetchone()
    if row is not None:
        return row
    deleted = db.execute("SELECT detail FROM tool_events WHERE job_id=? AND tool='preparation_lineage' AND status='deleted'",
                         (preparation_id,)).fetchone()
    return json.loads(deleted['detail']) if deleted else None


def line_works_sql(column, *, include_deleted=False):
    """#938: SQL matching Works (by ``column``, a request key) of any row of one line.

    A line done in pieces keeps a row per piece, so its rows are matched by a subquery rather than
    by one ``LIKE`` term per row, which SQLite's expression-depth bound would refuse on a long line.
    Bind the line id once, or twice when including deleted preparation lineage.
    """
    start = len(REQUEST_KEY_PREFIX) + 1
    ids = 'SELECT id FROM preparations WHERE line_id=?'
    if include_deleted:
        ids += (" UNION SELECT job_id FROM tool_events WHERE tool='preparation_lineage' AND status='deleted' "
                "AND json_extract(detail,'$.line_id')=?")
    return (f"{column} LIKE '{REQUEST_KEY_PREFIX}%' AND substr({column},{start},"
            f"instr(substr({column},{start}),':')-1) IN ({ids})")


#: #798: ``run_refusal`` reasons of a row whose line was stopped or whose brief cannot be read.
LINE_STOPPED, LINE_UNREADABLE = 'line-stopped', 'line-unreadable'


def line_refusal(db, line_id):
    """#798: why a row of ``line_id`` may not run now, or None (no line, or an active one)."""
    if not line_id:
        return None
    from .entrusted_lines import STATE_ACTIVE, line_state
    state = line_state(db, line_id)
    return None if state == STATE_ACTIVE else LINE_STOPPED if state else LINE_UNREADABLE


def piece_standing(parent_accepted_by):
    """#938: the ``accepted_by`` of a piece scheduled by a run of a row with ``parent_accepted_by``, or None.

    Inherited from the chain's root, never raised by a piece: a row the owner accepted, or a piece of
    one, gives ``ACCEPTED_LINE_PIECE_OWNER`` (the owner's payment autonomy applies as it does to that
    row; owner decision 2026-11-18); a row the owner's AI set up in an owner-typed Work, or a piece of
    one, gives the self-set ``ACCEPTED_LINE_PIECE``.  Anything else (the look back, the daily plan, an
    unknown or missing value) gives None: no piece.
    """
    if parent_accepted_by in (*OWNER_ACCEPTED, ACCEPTED_LINE_PIECE_OWNER):
        return ACCEPTED_LINE_PIECE_OWNER
    if parent_accepted_by in (ACCEPTED_OWNER_WORK, ACCEPTED_LINE_PIECE):
        return ACCEPTED_LINE_PIECE
    return None


def run_outcome(kind, job):
    """What one run's Work shows: ``delivered``, ``unknown`` or ``failed``.

    ``delivered`` means the Work's result reached its channel (Telegram
    ``sent``) or was kept in Still (no chat, ``none``).  An uncertain
    Telegram send is ``unknown``, never re-sent and never called delivered.
    """
    if not job:
        return STATE_FAILED
    delivery = job.get('delivery')
    if delivery == 'unknown':
        return STATE_UNKNOWN
    if delivery not in ('sent', 'none'):
        return STATE_FAILED
    if kind == KIND_REMINDER:
        return STATE_DELIVERED
    return STATE_DELIVERED if ws.has_delivered_result(job.get('status')) else STATE_FAILED


def text_digest(text):
    """A whitespace-insensitive digest of one result text."""
    return hashlib.sha256(' '.join(str(text or '').split()).encode()).hexdigest()[:32]


def bounded_text(text, limit=NOTIFIED_TEXT_BYTES):
    raw = str(text or '').encode()
    return raw[:limit].decode('utf-8', 'ignore') + (' [...]' if len(raw) > limit else '')


def watch_decision(row, answer, decide=None, stopped=False, promised=None):
    """``(decision, reason)`` of one finished ``when_needed`` run (#719).

    Deterministic only where no judgment is needed. A run with no result
    is told once (#394): a watch whose runs fail must not look like a watch
    with nothing to report, so the first failure after a result reaches the
    owner and later failures stay quiet until a run has a result again. A
    run the owner ``stopped`` is quiet. A result identical to the last
    notification is quiet.
    Otherwise ``decide(row, answer)`` - the DecisionEngine's bounded
    judgment, never a text rule - says whether the owner needs it now.
    When no judgment is available the result is sent once, and later
    unjudged runs stay quiet until a judgment is available again: an
    outage neither hides the watch nor repeats it every slot.
    """
    if promised and not stopped:
        return DECISION_NOTIFY, promised
    if not str(answer or '').strip():
        if stopped:
            return DECISION_QUIET, REASON_RUN_STOPPED
        if row.get('last_decision_reason') in (REASON_RUN_FAILED_TOLD, REASON_RUN_FAILED):
            return DECISION_QUIET, REASON_RUN_FAILED
        return DECISION_NOTIFY, REASON_RUN_FAILED_TOLD
    if text_digest(answer) == row.get('last_notified_digest'):
        return DECISION_QUIET, REASON_UNCHANGED
    verdict = None
    if decide is not None:
        try:
            verdict = decide(row, answer)
        except Exception:
            verdict = None
    if verdict == 'yes':
        return DECISION_NOTIFY, REASON_JUDGED_NEEDED
    if verdict == 'no':
        return DECISION_QUIET, REASON_JUDGED_NOT_NEEDED
    if row.get('last_decision_reason') in (REASON_UNJUDGED, REASON_UNJUDGED_REPEAT):
        return DECISION_QUIET, REASON_UNJUDGED_REPEAT
    return DECISION_NOTIFY, REASON_UNJUDGED


def window_ended(row, now):
    """#395: a watch whose window has no slot left at ``now`` (#701: its last slot is ``until_at``)."""
    return row.get('recurrence') == RECURRENCE_WINDOW and row.get('until_at') is not None and row['until_at'] < now


def reminder_text(goal, due_at, timezone_name, now):
    text = '알림: ' + goal
    if now - due_at > LATE_NOTE_SECONDS:
        text += f'\n(예정 시각 {local_text(due_at, timezone_name)}보다 늦게 전달했습니다.)'
    return text


# #689: the locale seam (telegram_locale) tries longer template patterns first, so the
# enclosing schedule shapes use long placeholder names and the date short ones: a whole
# line is matched by its outer shape before its date, never the reverse.
def _local_parts(ts, timezone_name, lang='ko'):
    local = datetime.fromtimestamp(ts, zone(timezone_name) or dt_timezone.utc)
    weekday = msg('preparations.day_' + ('MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU')[local.weekday()], lang)
    return (msg('preparations.schedule_date', lang, m=local.month, d=local.day, w=weekday),
            local.strftime('%H:%M'), local.date())


def _minutes_text(minutes, lang='ko'):
    hours, rest = divmod(int(minutes), 60)
    if not hours:
        return msg('preparations.schedule_minutes', lang, minutes=rest)
    if rest:
        return msg('preparations.schedule_hours_minutes', lang, hours=hours, minutes=rest)
    return msg('preparations.schedule_hours', lang, hours=hours)


def last_slot(row):
    """#404: a watch's last scheduled slot (``max_runs`` never exceeds the slots before its deadline).

    What the model is told to plan by; a late start or a skipped slot can
    still run until the deadline, which is what the owner is shown (#399).
    """
    # A scheduled due slot is still pending; a running regular slot already
    # consumed its count. An immediate run leaves due_at on the pending slot.
    count = int(row.get('run_count') or 0)
    if row.get('state') != STATE_RUNNING or row.get('run_return_state'):
        count += 1
    if not stored_window_terms(row).get('recurrence'):
        every = int(row['every_seconds'])
        remaining = max(0, int(row['max_runs']) - count)
        before_end = max(0, math.floor((row['until_at'] - row['due_at']) / every))
        return row['due_at'] + every * min(remaining, before_end)
    slot = row['due_at']
    for count in range(count, int(row['max_runs'])):
        following = next_window_slot({**row, 'due_at': slot, 'run_count': count}, slot)
        if following is None:
            break
        slot = following
    return slot


def rule_text(recurrence, lang='ko', *, timezone_name=None, home_zone=None):
    """Plain owner words for the supported rule parts (#580)."""
    parts = rule_parts(recurrence)
    interval = int(parts.get('INTERVAL', 1))
    if interval == 1:
        key = 'preparations.rule_every_' + {'DAILY':'day', 'WEEKLY':'week', 'MONTHLY':'month'}[parts['FREQ']]
    elif interval == 2 and parts['FREQ'] == 'WEEKLY':
        key = 'preparations.rule_every_two_weeks'
    else:
        key = 'preparations.rule_' + parts['FREQ'].lower()
    # #689: whole recurrence shapes translate nested selectors and limits.
    text = msg(key, lang, interval=interval)
    if 'BYDAY' in parts:
        days = []
        for day in parts['BYDAY'].split(','):
            name = msg('preparations.day_' + day[-2:], lang)
            ordinal = day[:-2]
            days.append(msg('preparations.rule_ordinal', lang, ordinal=ordinal, day=name) if ordinal else name)
        text = msg(key + '_days', lang, n=interval, selected_recurrence_calendar_day_list='·'.join(days))
    if 'BYMONTHDAY' in parts:
        days = '·'.join(msg('preparations.rule_last_day', lang) if day == '-1' else
                        msg('preparations.rule_monthday', lang, day=day) for day in parts['BYMONTHDAY'].split(','))
        # #689: retain both selectors when a rule constrains weekdays and dates.
        if 'BYDAY' in parts:
            text += ' ' + days
        else:
            text = msg(key + '_days', lang, n=interval, selected_recurrence_calendar_day_list=days)
    if 'UNTIL' in parts:
        until = parts['UNTIL']
        home = zone(home_zone)
        stored = zone(timezone_name)
        if len(until) == 8:
            end = datetime.strptime(until, '%Y%m%d')
            if home and stored and home != stored:
                end = end.replace(hour=23, minute=59, second=59, tzinfo=stored).astimezone(home)
                shown = end.strftime('%Y-%m-%d %H:%M')
            else:
                shown = end.strftime('%Y-%m-%d')
        else:
            utc = until.endswith('Z')
            end = datetime.strptime(until.rstrip('Z'), '%Y%m%dT%H%M%S')
            source = dt_timezone.utc if utc else stored
            if home and source:
                end = end.replace(tzinfo=source).astimezone(home)
            shown = end.strftime('%Y-%m-%d %H:%M') + (' UTC' if utc and not home else '')
        text = msg('preparations.rule_with_until', lang, recurrence_rule=text, recurrence_end_datetime=shown)
    if 'COUNT' in parts:
        text = msg('preparations.rule_with_count', lang, recurrence_rule=text, recurrence_maximum_count=parts['COUNT'])
    return text


def schedule_text(row, home_zone=None, lang='ko'):
    """#393: one owner-readable line - when it runs and how the owner hears of it.

    A date in the owner's words (``10월 28일(수) 18:35``), the end time alone
    when it is the same day and the stored zone only when the home zone is unknown.
    A watch is stated as it is enforced (``next_window_slot``): slots up to
    and including ``until_at`` (#701) and at most ``max_runs`` times, so a
    skipped slot can never run past what the owner read.
    """
    # #689: render directly in the owner's language or as translatable Korean.
    terms = stored_window_terms(row)
    opening = row['due_at']
    if terms.get('recurrence') and row.get('rule_start') is not None:
        tz = zone(row['timezone'])
        local_day = datetime.fromtimestamp(opening, tz).date()
        start = datetime.fromtimestamp(row['rule_start'], tz).time()
        opening = datetime.combine(local_day, start, tzinfo=tz).timestamp()
    home_zone = home_zone if zone(home_zone) else None
    recurrence = row.get('recurrence') or ''
    # A repeating rule's days and times are the stored zone's wall clock: converting only its start
    # would contradict "every Monday", so a repeating row keeps its zone named when it is not home.
    repeats = recurrence.startswith('RRULE:') or recurrence in ('daily', 'weekdays', 'weekly') or bool(terms.get('recurrence'))
    at_home = bool(home_zone)
    if repeats and home_zone and row['timezone'] != home_zone:
        home_zone, at_home = None, False
    display_zone = home_zone or row['timezone']
    day, at, date = _local_parts(opening, display_zone, lang)
    zone_note = '' if at_home else f" ({display_zone})"
    if recurrence.startswith('RRULE:'):
        rule = rule_text(recurrence, lang, timezone_name=row['timezone'], home_zone=home_zone or row['timezone'])
        when = msg('preparations.rule_schedule', lang, start=f'{day} {at}{zone_note}', rule=rule)
    elif recurrence == RECURRENCE_WINDOW:
        runs = max(1, int(row['max_runs']))
        every = int(row['every_seconds'])
        end_day, end_at, end_date = _local_parts(row['until_at'], display_zone, lang)
        end = end_at if end_date == date else f'{end_day} {end_at}'
        end = msg('preparations.schedule_until_zone' if zone_note else 'preparations.rule_until', lang,
                  until=end, zone=row['timezone'])
        start = f'{day} {at}'
        if row.get('check_spec'):
            args = json.loads(row['check_spec'])['arguments']
            when = msg('checks.setup', lang, seconds=every, until=end, start=start, runs=runs,
                       hosts=', '.join(args['hosts']) or '-', secrets=', '.join(args['secrets']) or '-')
        elif runs == 1:
            when = msg('preparations.schedule_window_once', lang, preparation_start_time=start, preparation_deadline=end)
        else:
            when = msg('preparations.schedule_window_many', lang, preparation_start_time=start, preparation_deadline=end,
                       interval=_minutes_text(every // 60, lang), runs=runs)
    else:
        when = f'{day} {at}{zone_note}'
        if recurrence in ('daily', 'weekdays', 'weekly'):
            when = msg('preparations.schedule_' + recurrence, lang, preparation_start_time=when)
    if terms.get('recurrence'):
        when = msg('preparations.recurring_window_' + terms['recurrence'], lang, schedule=when)
    if terms.get('report_seconds'):
        when = msg('preparations.promised_interval', lang, schedule=when,
                   interval=_minutes_text(terms['report_seconds'] // 60, lang))
    if row['kind'] == KIND_REMINDER:
        return when
    if row.get('bundle_report_times'):
        return msg('preparations.schedule_delivery_bundle', lang,
                   owner_preparation_schedule_with_delivery=when,
                   report_times=', '.join(row['bundle_report_times']))
    if row.get('delivery_mode') == DELIVERY_WHEN_NEEDED:
        mode = 'when_needed'
    elif row.get('channel') == CHANNEL_WEB:
        mode = 'keep'
    else:
        mode = 'send'
    return msg('preparations.schedule_delivery_' + mode, lang, owner_preparation_schedule_with_delivery=when)


def offered(row):
    """#393: whether the AI wrote an owner-facing offer for this row."""
    return bool(row.get('title_text') or row.get('offer_text'))


def carded(row):
    """#950: whether this row is shown as a card (name, schedule, folded brief), not as one line.

    A preparation always is, with or without an offer: its goal is the run's brief, never the
    line the owner reads first.  A reminder without an offer stays one line (its goal is its text).
    """
    return offered(row) or row.get('kind') == KIND_PREPARE


def owner_title(row, limit=120):
    """#393: the short name the owner knows this preparation by: its title, else its goal cut short."""
    title = row.get('title_text')
    if title:
        return title
    goal = ' '.join(str(row.get('goal_text') or '').split())
    return goal[:limit] + ('…' if len(goal) > limit else '')


def name_key(text):
    """#1097: how two owner-facing names compare: case and spacing aside."""
    return ' '.join(str(text or '').split()).casefold()


def owner_name(row, others, home_zone=None, lang='ko'):
    """#1097: the name a notice gives a preparation, which the owner may say back.

    Its title (or goal cut short, ``owner_title``); with its schedule, as Settings lists it,
    when another row is known by the same title.
    """
    title = owner_title(row)
    if any(other['id'] != row['id'] and name_key(owner_title(other)) == name_key(title) for other in others):
        return ' · '.join((title, schedule_text(row, home_zone, lang)))
    return title


def named(rows, name, home_zone=None):
    """#1097: the rows an owner's name for a preparation fits: its title, or title and schedule."""
    from .messages import LANGUAGES
    wanted = name_key(name)
    if not wanted:
        return []
    return [row for row in rows if wanted == name_key(owner_title(row)) or any(
        wanted == name_key(' · '.join((owner_title(row), schedule_text(row, home_zone, lang)))) for lang in LANGUAGES)]


def brief_preview(row):
    """#766: the run's brief as a proposal card folds it, cut to ``BRIEF_PREVIEW_CHARS``."""
    goal = str(row.get('goal_text') or '')
    return goal[:BRIEF_PREVIEW_CHARS] + ('…' if len(goal) > BRIEF_PREVIEW_CHARS else '')


def changed_hunks(before, after, context=60):
    """#968: every place two briefs differ, as ``[(before_piece, after_piece), ...]`` (``difflib``).

    Each piece is complete, with up to ``context`` characters around it; nearby changes merge into
    one place.  Nothing changed is left out, so a judgment or receipt built from these sees all of it.
    """
    before, after = str(before or ''), str(after or '')
    changed = [op for op in difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes() if op[0] != 'equal']
    groups = []
    for _tag, i1, i2, j1, j2 in changed:
        if groups and i1 - groups[-1][1] <= 2 * context and j1 - groups[-1][3] <= 2 * context:
            groups[-1] = (groups[-1][0], i2, groups[-1][2], j2)
        else:
            groups.append((i1, i2, j1, j2))

    def cut(text, start, stop):
        start, stop = max(0, start - context), min(len(text), stop + context)
        return ('…' if start else '') + text[start:stop] + ('…' if stop < len(text) else '')
    return [(cut(before, i1, i2), cut(after, j1, j2)) for i1, i2, j1, j2 in groups]


def hunks_text(hunks):
    """The places of ``changed_hunks`` as one plain text: ``[n] before -> after`` per place."""
    return '\n'.join(f'[{number}] {was} -> {now}' for number, (was, now) in enumerate(hunks, 1))


def accepted_offer(row):
    """#393: the title and offer the owner accepted, without the schedule line.

    For a run's context and the watch judgment: a row's ``due_at`` advances
    to its next slot, so a schedule line rendered later would not be the one
    the owner read; the brief carries the run's own times.
    """
    return '\n'.join(part for part in (row.get('title_text'), row.get('offer_text')) if part)


def proposal_summary(row, home_zone=None, brief=True, lang='ko'):
    """What the owner reads for one proposal (Telegram, web, judgment context).

    #393: with an offer, its title, the schedule line and the offer come
    first; the run's brief follows under its own label (``brief`` False
    leaves it out).  Without one, one line as before.
    """
    if not carded(row):
        # #689: fixed kind labels must translate with the schedule on cards.
        kind = 'reminder' if row['kind'] == KIND_REMINDER else 'prepare'
        return msg('preparations.summary_' + kind, lang,
                   owner_preparation_schedule_with_delivery=schedule_text(row, home_zone, lang), goal_text=brief_preview(row))
    lines = [row.get('title_text') or owner_title(row), schedule_text(row, home_zone, lang)]
    if row.get('offer_text'):
        lines.append(row['offer_text'])
    if brief:
        # #689: the offered card's fixed brief label translates with its value.
        lines.append(msg('preparations.proposal_brief', lang, goal=brief_preview(row)))
    return '\n'.join(lines)


def proposal_html(shown, remaining, home_zone=None):
    """#393: the Telegram (HTML) proposal: the offer first, each run's brief folded."""
    from html import escape
    single = len(shown) == 1
    parts = [] if single else [escape(PROPOSAL_HEADER)]
    for row in shown:
        if not carded(row):
            parts.append(escape(proposal_summary(row, home_zone)))
            continue
        block = [f'<b>{escape(row.get("title_text") or owner_title(row))}</b>', escape(schedule_text(row, home_zone))]
        if row.get('offer_text'):
            block.append(escape(row['offer_text']))
        # #689: keep the folded brief as one translatable catalog block.
        block.append(msg('preparations.proposal_brief_html', goal=escape(brief_preview(row))))
        parts.append('\n'.join(block))
    if remaining:
        parts.append(escape(msg('preparations.more_proposals', remaining=remaining)))
    if single and not shown[0].get('offer_text'):
        parts.append(escape(PROPOSAL_QUESTION))
    return '\n\n'.join(parts)


PROPOSAL_HEADER = msg('preparations.shall_i_schedule_the_following_preparation')
#: A single proposal without AI offer prose uses the catalog question.
PROPOSAL_QUESTION = msg('preparations.proposal_question')
#: #393: the label of the run's brief under an offer.
BRIEF_LABEL = msg('preparations.brief_label')
#: One Telegram proposal message stays well under Telegram's 4096 characters.
PROPOSAL_TEXT_LIMIT = 3500


def proposal_page(rows):
    """``(shown, remaining)``: the proposals one Telegram message offers.

    Rows are taken in order while their lines fit ``PROPOSAL_TEXT_LIMIT``;
    the rest are only counted (the owner accepts them in the conversation, #1455).  The
    message's buttons are bound to ``digest(shown)``, never to rows it did
    not show.
    """
    shown, size = [], len(PROPOSAL_HEADER) + 120  # room for the "more" line
    for row in rows:
        size += 2 + len(proposal_summary(row))
        if shown and size > PROPOSAL_TEXT_LIMIT:
            break
        shown.append(row)
    return shown, len(rows) - len(shown)


def proposal_text(shown, remaining, home_zone=None):
    """The plain proposal; #393: a single offered proposal needs no generic header."""
    single = len(shown) == 1
    parts = ([] if single else [PROPOSAL_HEADER]) + [proposal_summary(row, home_zone) for row in shown]
    text = ('\n\n' if any(carded(row) for row in shown) else '\n').join(parts)
    if remaining:
        text += '\n' + msg('preparations.more_proposals', remaining=remaining)
    if single and not shown[0].get('offer_text'):
        text += '\n\n' + PROPOSAL_QUESTION
    return text


def digest(rows):
    """The exact set of proposals one Telegram message offers."""
    material = json.dumps(sorted((row['id'], row['kind'], row['goal_text'], row['due_at'], row.get('recurrence'),
                                  # #719: the watch's window, bound and delivery are part of what was offered.
                                  row.get('every_seconds'), row.get('window_end'), row.get('max_runs'),
                                  row.get('delivery_mode'))
                                 # #393: the offer the owner read is part of what is accepted; a row
                                 # without one keeps the digest an earlier message was bound to.
                                 + ((row.get('title_text'), row.get('offer_text')) if offered(row) else ())
                                 + ((row['window_terms'],) if row.get('window_terms') else ())
                                 + ((json.loads(row['check_spec'])['digest'],) if row.get('check_spec') else ())
                                 + ((row['line_id'], row['timezone'], row['channel'])
                                    if row.get('origin') == ORIGIN_LINE_RESUME else ())
                                 for row in rows), ensure_ascii=False)
    return hashlib.sha256(material.encode()).hexdigest()[:32]


# --- the owner's preparations -------------------------------------------------

class Preparations:
    """The ``preparations`` rows of one owner store.  No model, no network."""

    def __init__(self, store, clock=time.time, notice_destination=None, budget_notice_destinations=None,
                 bundle_report_times=None):
        self.store, self.clock = store, clock
        self.notice_destination = notice_destination
        self.budget_notice_destinations = budget_notice_destinations
        self.bundle_report_times = bundle_report_times
        self._invalid_budget_logged = False

    def budget_settings(self, db=None):
        try:
            value = self.store.config(SCHEDULED_BUDGET_KEY, {}, db=db)
        except (ValueError, TypeError):
            value = None
        cap = value.get('daily_calls', DEFAULT_SCHEDULED_DAILY_CALLS) if isinstance(value, dict) else None
        if type(cap) is not int or not 0 <= cap <= MAX_SCHEDULED_DAILY_CALLS:
            cap = DEFAULT_SCHEDULED_DAILY_CALLS
            if not self._invalid_budget_logged:
                self._invalid_budget_logged = True
                LOG.warning('Invalid stored scheduled Work budget; using default daily_calls=%s', cap)
        return {'daily_calls': cap}

    def set_budget(self, cap):
        if type(cap) is not int or not 0 <= cap <= MAX_SCHEDULED_DAILY_CALLS:
            raise ValueError(msg('scheduled_budget.invalid', limit=MAX_SCHEDULED_DAILY_CALLS))
        self.store.put(SCHEDULED_BUDGET_KEY, {'daily_calls': cap})
        return self.budget_settings()

    def _owner_zone(self, db=None):
        from .context_observations import ContextObservations, CONFIG_KEY
        # With no configured owner zone, use this owner's machine-local date (as cloud daily_calls does).
        return zone(ContextObservations._normalize(self.store.config(CONFIG_KEY, db=db))['timezone'])

    def budget_day(self, now, db=None):
        return datetime.fromtimestamp(now, self._owner_zone(db)).date().isoformat()

    def owner_day_start(self, now, db=None):
        """#938: when the owner-local day of ``now`` began, by the same zone as ``budget_day``."""
        return datetime.fromtimestamp(now, self._owner_zone(db)).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

    def _skip_budget_slot(self, db, row, key, now, day, cap):
        """Consume a refused slot atomically, retaining evidence without inventing an AI Work.

        The existing message/notification outbox owns the once-per-day notice, including
        local chat when no delivery surface is connected. No separate delivery state.
        """
        run = int(row['run_count'] or 0) + 1
        consumed = {**row, 'run_count': run}
        window = row['recurrence'] == RECURRENCE_WINDOW
        following = (next_window_slot(consumed, now) if window else
                     next_due(row['due_at'], row['timezone'], row['recurrence'], now, row.get('rule_start'), run))
        advance = next_day_window(consumed, now) if window and following is None else {}
        updates = {'run_count': run, 'updated_at': now, 'last_outcome': 'skipped',
                   'state': STATE_SCHEDULED if following is not None or advance else STATE_EXPIRED,
                   **({'due_at': following} if following is not None else {}), **advance}
        db.execute(f"UPDATE preparations SET {','.join(k + '=?' for k in updates)} WHERE id=?",
                   (*updates.values(), row['id']))
        detail = {'preparation_id': row['id'], 'slot': iso(row['due_at']), 'slot_at': row['due_at'],
                  'slot_local': local_text(row['due_at'], row['timezone']),
                  'reason': SCHEDULED_BUDGET_REASON,
                  'day': day, 'daily_calls': cap, 'ran': False}
        db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                   (key, SCHEDULED_BUDGET_KEY, 'skipped', json.dumps(detail), now))
        pending_key = SCHEDULED_SKIPPED_PREFIX + row['id']
        pending = self.store.config(pending_key, {}, db=db)
        self.store.put(pending_key, {'count': pending.get('count', 0) + 1,
                                    'first_slot_at': pending.get('first_slot_at', row['due_at']),
                                    'last_slot_at': row['due_at'], 'reason': SCHEDULED_BUDGET_REASON}, db=db)
        from .current_context import redact_known_secrets
        self._queue_budget_notice(db, row, now, day, cap, row['due_at'],
                                  redact_known_secrets(self.store, owner_title(row)),
                                  self.budget_notice_destinations() if self.budget_notice_destinations else ())
        self._clear_closed_budget_skips(db)

    def _queue_budget_notice(self, db, row, now, day, cap, slot, title, destinations):
        notice_id = SCHEDULED_USAGE_PREFIX + day
        if not db.execute('SELECT 1 FROM messages WHERE job_id=?', (notice_id,)).fetchone():
            text = msg('scheduled_budget.notice', day=day, cap=cap, title=title,
                       slot=local_text(slot, row['timezone']))
            db.execute('INSERT INTO messages(role,content,channel,created,job_id) VALUES (?,?,?,?,?)',
                       ('assistant', text, 'web', now, notice_id))
        # Owner-wide notice: its destinations never depend on the skipped row or its anchor.
        # The outbox's (job_id, kind) uniqueness needs a separate receipt per surface.
        for owner, generation, surface in destinations:
            self.store.telegram.queue_notification(notice_id + ':' + surface, owner, generation,
                                                   SCHEDULED_BUDGET_NOTICE, notice_id, surface, db=db, created=now)

    @staticmethod
    def _clear_closed_budget_skips(db):
        # The daily notice already covers all remaining checks, including a missing
        # closing report. Keep the immutable events, not pending input no run can carry.
        # A terminal row still owns its queued or uncertain final report until confirmed.
        db.execute(f'DELETE FROM config WHERE key GLOB ? AND NOT EXISTS '
                   f'(SELECT 1 FROM preparations WHERE id=substr(config.key,?) AND '
                   f'(state IN ({",".join("?" for _ in ACTIVE_STATES)}) OR EXISTS '
                   "(SELECT 1 FROM telegram_notifications WHERE job_id=preparations.last_run_job_id "
                   "AND kind=? AND state IN ('queued','sending','unknown'))))",
                   (SCHEDULED_SKIPPED_PREFIX + '*', len(SCHEDULED_SKIPPED_PREFIX) + 1, *ACTIVE_STATES, NOTIFY_KIND))

    def _clear_carried_budget_skips(self, db, preparation_id, work_id):
        """Consume only this report's snapshot, preserving skips accumulated since it ran."""
        carried = db.execute('SELECT detail FROM tool_events WHERE job_id=? AND tool=? AND status=? ORDER BY id LIMIT 1',
                             (work_id, SCHEDULED_BUDGET_KEY, 'unchecked-slots')).fetchone()
        pending_key = SCHEDULED_SKIPPED_PREFIX + preparation_id
        if carried and json.loads(carried['detail']) == self.store.config(pending_key, db=db):
            db.execute('DELETE FROM config WHERE key=?', (pending_key,))

    def budget_skipped_fact(self, job):
        """Bind unchecked slots to this Work; repeated worker/judge reads see the same fact.

        Pending facts survive restart, failed runs and quiet checks. A delivered or
        explicitly kept report clears only its snapshot; notification reports wait
        for record_notified. The skip events themselves remain Evidence.
        Uses SQLite/QuickStore's existing transaction and event store, with no new schema.
        """
        preparation_id = preparation_of(job.get('request_key'))
        if not preparation_id:
            return None
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self.store.require_read_checkpoint_writer(db)
            carried = db.execute('SELECT detail FROM tool_events WHERE job_id=? AND tool=? AND status=? ORDER BY id LIMIT 1',
                                 (job['id'], SCHEDULED_BUDGET_KEY, 'unchecked-slots')).fetchone()
            if carried:
                return json.loads(carried['detail'])
            current = db.execute('SELECT state,last_run_job_id FROM preparations WHERE id=?', (preparation_id,)).fetchone()
            if not current or current['state'] != STATE_RUNNING or current['last_run_job_id'] != job['id']:
                return None
            pending = self.store.config(SCHEDULED_SKIPPED_PREFIX + preparation_id, db=db)
            if pending:
                db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                           (job['id'], SCHEDULED_BUDGET_KEY, 'unchecked-slots', json.dumps(pending), self.clock()))
            return pending

    def _schedule_row(self, row, db):
        """Decorate a read for owner-facing schedules; nothing new is persisted."""
        if row is None:return None
        row = dict(row)
        if self.bundle_report_times:
            times = self.bundle_report_times(row, db)
            if times:row['bundle_report_times'] = times
        return row

    def notice_channel(self, row, db=None):
        """Owner-action/status send intent, independently of held result delivery.

        ``line_held.channel`` (including create's ``bundle_channel``) is the
        original intent. Per-run holding receipts have no channel of their own.
        Used also to address an owner's explicit run-now before it starts.
        Never use this to readdress an existing Work or a completed report.
        """
        if row['channel'] != CHANNEL_WEB:
            return row['channel']
        if db is None:
            with self.store.db() as db:return self.notice_channel(row, db)
        held = db.execute("SELECT json_extract(detail,'$.channel') AS channel FROM tool_events "
                          "WHERE tool='line_held' AND json_extract(detail,'$.preparation_id')=? "
                          "AND json_type(detail,'$.channel')='text' ORDER BY id LIMIT 1", (row['id'],)).fetchone()
        return held['channel'] if held else row['channel']

    def _queue_status_notice(self, db, row, job_id, kind, created, *, destination=...):
        if self.notice_destination is None or not row['accepted_by']:
            return
        if destination is ...:
            destination = self.notice_destination(self.notice_channel(row, db))
        if destination:
            owner, generation, surface = destination
            anchor = db.execute('SELECT channel,chat_id FROM jobs WHERE id=?', (row['created_from'],)).fetchone()
            if (anchor and str(anchor['channel']).startswith(surface + ':')
                    and anchor['chat_id'] is not None and str(anchor['chat_id']) != str(owner)):
                return
            self.store.telegram.queue_notification(job_id, owner, generation, kind, row['id'], surface,
                                                   db=db, created=created)

    def connection_wait_notice(self, db, work_id, message_id, created):
        """Bind a quiet run's new connection wait to its persisted guidance message."""
        row = db.execute('SELECT p.* FROM preparations p JOIN jobs j ON j.id=p.last_run_job_id '
                         "WHERE j.id=? AND j.chat_id IS NULL AND p.state=? AND j.status='awaiting_connection'",
                         (work_id, STATE_RUNNING)).fetchone()
        if row:
            self._queue_status_notice(db, row, work_id, BLOCKED_NOTICE_PREFIX + str(message_id), created)

    def get(self, preparation_id):
        with self.store.db() as db:
            row = db.execute('SELECT * FROM preparations WHERE id=?', (str(preparation_id or ''),)).fetchone()
            return self._schedule_row(row, db)

    def create(self, *, kind, goal, due_at, timezone, recurrence, channel, created_from, state,
               accepted_by=None, owner=OWNER, window=None, delivery_mode=None, title=None, offer=None, origin=None,
               report_every_minutes=None, line_id=None, max_runs=None, requested_until=None, bundle_channel=None, accepted_from=None,
               piece_of=None, db=None, check=None, replaces=None, deduplicate=True):
        """One new row (idempotent for the same Work, kind, goal and slot).

        #574: when another Work already proposed the same kind, goal, due and
        recurrence (and window) and the owner has not answered it yet, that row
        is returned with ``already_proposed`` instead of a duplicate. A line planner creates its own
        executor (``deduplicate=False``) after checking admission for the planner slot in the same transaction.

        ``max_runs`` bounds an ordinary daily, weekdays or weekly recurrence using RFC 5545 COUNT.
        The owner extends it through the ordinary recurrence-rule editor.

        ``window`` is ``(every_seconds, until_at, max_runs)`` from
        ``normalize_window`` (#719); it replaces ``recurrence``.  Its run
        deadline ``window_end`` is one run's time after ``until_at`` (#701).  ``origin``
        ``ORIGIN_DREAM`` marks a look-back suggestion (#583); #717: it starts scheduled (``ACCEPTED_DREAM``).
        #798: ``line_id`` is the entrusted line this row works for; the caller checked it is active.
        ``db`` joins a caller's transaction, so creation and its recovery evidence commit together.
        ``replaces`` is the validated row the caller will cancel in that transaction, excluded from the check cap.

        #938: ``piece_of`` is the slot key of the run that schedules this piece of its line (origin
        ``ORIGIN_LINE_PIECE``).  In this transaction the line must still be active (a stop at the same
        moment wins, ``line_stopped``) and the D3 bounds hold: the lead time, ``LINE_PIECES_PER_DAY``
        per line and owner-local day, ``MAX_PENDING_PIECES`` in the store.  One run has one child: a
        second call from the same run (or its continuation) changes that child's goal and time while it
        has not started, and is refused once it has started or the owner paused or cancelled it.
        """
        from .agent_runtime import not_run
        resume_offer = origin == ORIGIN_LINE_RESUME
        if resume_offer and not (state == STATE_PROPOSED and accepted_by is None and kind == KIND_PREPARE
                                 and isinstance(line_id, str) and line_id and created_from and offer
                                 and offer.count('\n') < 2 and not (window or recurrence or max_runs or replaces)):
            raise not_run(PreparationRefusal('invalid_line_resume_offer'))
        if piece_of is not None and not (origin == ORIGIN_LINE_PIECE and accepted_by in (ACCEPTED_LINE_PIECE, ACCEPTED_LINE_PIECE_OWNER)
                                         and kind == KIND_PREPARE and window is None and recurrence is None and max_runs is None
                                         and line_id and state == STATE_SCHEDULED):
            raise ValueError('a piece is one scheduled prepare of its line')
        if origin == ORIGIN_LINE_PIECE and piece_of is None:
            raise ValueError('a piece names the run that scheduled it')
        if kind not in KINDS:
            raise not_run(PreparationRefusal('invalid_kind'))
        if delivery_mode not in (None, DELIVERY_ALWAYS, DELIVERY_WHEN_NEEDED):
            raise ValueError('unknown delivery mode')
        if delivery_mode == DELIVERY_WHEN_NEEDED and kind != KIND_PREPARE:
            raise not_run(PreparationRefusal('invalid_delivery'))
        try:
            recurrence = normalize_recurrence(recurrence)
        except PreparationRefusal as exc:
            raise not_run(exc)
        # A finite ordinary recurrence uses the existing RFC 5545 COUNT normalizer
        # and expansion, leaving watch windows and slot settlement unchanged.
        if max_runs is not None and window is None:
            rule = {'daily': 'FREQ=DAILY', 'weekdays': 'FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR',
                    'weekly': 'FREQ=WEEKLY'}.get(recurrence, recurrence)
            recurrence = normalize_rrule(f'{rule};COUNT={max_runs}')
            max_runs = int(rule_parts(recurrence)['COUNT'])
        every_seconds = until_at = window_end = None
        try:
            terms = window_terms(window, recurrence, due_at, timezone, report_every_minutes, delivery_mode, requested_until)
        except PreparationRefusal as exc:
            raise not_run(exc)
        if window is not None:
            every_seconds, until_at, max_runs = window
            window_end = until_at + run_seconds()
            recurrence = RECURRENCE_WINDOW
        if state not in (STATE_PROPOSED, STATE_SCHEDULED):
            raise ValueError('a preparation starts proposed or scheduled')
        if origin not in (None, *ORIGINS):
            raise ValueError('unknown origin')
        if check is not None and (kind != KIND_PREPARE or not window or piece_of):
            raise not_run(PreparationRefusal('invalid_check'))
        check_spec = json.dumps(check, sort_keys=True) if check is not None else None
        now = self.clock()
        owns_transaction = db is None
        with (self.store.db() if owns_transaction else nullcontext(db)) as db:
            if owns_transaction:db.execute('BEGIN IMMEDIATE')
            self.store.require_read_checkpoint_writer(db)
            if resume_offer:
                brief = self._admit_line_resume(db, line_id, due_at, now)
            if piece_of is not None:
                child = self._piece_admission(db, line_id, piece_of, due_at, now)
                if child is not None:
                    db.execute('UPDATE preparations SET goal_text=?,due_at=?,timezone=?,title_text=?,offer_text=?,updated_at=? '
                               'WHERE id=? AND state=?', (goal, due_at, timezone, title, offer, now, child['id'], STATE_SCHEDULED))
                    return {**dict(db.execute('SELECT * FROM preparations WHERE id=?', (child['id'],)).fetchone()),
                            'piece_replaced': True}
            # A line account is kept once per line by ensure_line_account, even when a
            # single owner answer resumes several lines with the same account cadence.
            line_specific = (not deduplicate or piece_of is not None or resume_offer
                             or origin in (ORIGIN_LINE_ACCOUNT, ORIGIN_LINE_PLANNER))
            same = None if line_specific else db.execute(
                              'SELECT * FROM preparations WHERE created_from=? AND kind=? AND goal_text=? AND due_at=? '
                              'AND state IN (?,?,?,?) AND check_spec IS ? AND origin IS NOT ? AND origin IS NOT ?',
                              (created_from, kind, goal, due_at, *ACTIVE_STATES, check_spec,
                               ORIGIN_LINE_RESUME, ORIGIN_LINE_ACCOUNT)).fetchone()
            if same:
                return dict(same)
            # #574: another Work's identical proposal still waiting for the owner is that same proposal,
            # not a second one.  Scheduled rows are left alone: the owner may want two.
            # #938: a piece never takes up another Work's proposal (that would accept it).
            proposed = None if line_specific else db.execute(
                                  'SELECT * FROM preparations WHERE state=? AND created_from IS NOT ? AND kind=? AND goal_text=? '
                                  'AND due_at=? AND recurrence IS ? AND every_seconds IS ? AND window_end IS ? AND max_runs IS ? AND window_terms IS ? '
                                  'AND check_spec IS ? AND origin IS NOT ? ORDER BY created_at LIMIT 1',
                                  (STATE_PROPOSED, created_from, kind, goal, due_at, recurrence,
                                   every_seconds, window_end, max_runs, terms, check_spec, ORIGIN_LINE_RESUME)).fetchone()
            if proposed:
                return {**dict(proposed), 'already_proposed': True}
            active = db.execute('SELECT count(*) FROM preparations WHERE state IN (?,?,?,?)', ACTIVE_STATES).fetchone()[0]
            if origin == ORIGIN_DREAM or accepted_by == ACCEPTED_DREAM:
                # Stood accounts have their own origin, but share the dream's allowance.
                dreams = db.execute('SELECT count(*) FROM preparations WHERE (origin=? OR accepted_by=?) '
                                    'AND state IN (?,?,?,?)',
                                    (ORIGIN_DREAM, ACCEPTED_DREAM, *ACTIVE_STATES)).fetchone()[0]
                if dreams >= MAX_ACTIVE_DREAM:
                    raise not_run(PreparationRefusal('limit'))
            if active >= MAX_ACTIVE:
                raise not_run(PreparationRefusal('too_many'))
            if state == STATE_SCHEDULED:
                from . import continuous_lines as continuous
                if check_spec and continuous.active(db, line_id, now) and continuous.has_other_executor(db, line_id, replaces):
                    raise not_run(PreparationRefusal('line_executor_exists'))
                self._check_admission(db, check_spec, replaces)
            row = {'id': uuid.uuid4().hex[:16], 'owner': owner, 'kind': kind, 'goal_text': goal, 'due_at': due_at,
                   'timezone': timezone, 'recurrence': recurrence, 'channel': channel, 'state': state,
                   'last_run_job_id': None, 'last_outcome': None, 'prepared_result_ref': None, 'prepared_at': None, 'prepared_text': None,
                   'created_from': created_from, 'accepted_by': accepted_by, 'accepted_from': accepted_from, 'created_at': now, 'updated_at': now,
                   'every_seconds': every_seconds, 'window_end': window_end, 'until_at': until_at,
                   'max_runs': max_runs, 'run_count': 0,
                   'rule_start': due_at if window or terms or recurrence and recurrence.startswith('RRULE:') else None,
                   'window_terms': terms, 'last_report_at': None,
                   'delivery_mode': delivery_mode, 'last_decision': None, 'last_decision_reason': None,
                   'last_notified_digest': None, 'last_notified_text': None, 'last_notified_at': None,
                   'title_text': title, 'offer_text': offer, 'origin': origin, 'offered_at': None, 'line_id': line_id,
                   'piece_of': piece_of}
            if check_spec is not None:
                row.update(check_spec=check_spec, check_next_at=min(due_at, until_at))
            db.execute(f"INSERT INTO preparations({','.join(row)}) VALUES ({','.join('?' * len(row))})", tuple(row.values()))
            if resume_offer:
                # Keep the day cap even if the owner deletes the row. The same receipt binds the brief
                # version so a later owner stop or correction cannot be undone by an older offer.
                db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                           (created_from, LINE_RESUME_EVENT, 'succeeded',
                            json.dumps({'preparation_id': row['id'], 'line_id': line_id, 'version': brief['version']}), now))
            if bundle_channel is not None:
                # The stood row and its original send intent commit together, already on web.
                db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                           (created_from, 'line_held', 'succeeded',
                            json.dumps({'preparation_id': row['id'], 'channel': bundle_channel}), now))
            return row

    def _piece_admission(self, db, line_id, piece_of, due_at, now):
        """#938: refuse a piece the line or the D3 bounds do not admit; the run's pending child, or None."""
        from .agent_runtime import not_run
        from .entrusted_lines import STATE_ACTIVE as LINE_ACTIVE, line_state
        if line_state(db, line_id) != LINE_ACTIVE:
            raise not_run(PreparationRefusal('line_stopped'))
        if due_at < now + PIECE_LEAD_SECONDS:
            raise not_run(PreparationRefusal('line_piece_too_soon'))
        child = db.execute('SELECT * FROM preparations WHERE line_id=? AND piece_of=? ORDER BY created_at LIMIT 1',
                           (line_id, piece_of)).fetchone()
        if child is not None:
            if child['state'] != STATE_SCHEDULED:
                raise not_run(PreparationRefusal('line_piece_exists'))
            return dict(child)
        today = db.execute('SELECT count(*) FROM preparations WHERE line_id=? AND origin=? AND created_at>=?',
                           (line_id, ORIGIN_LINE_PIECE, self.owner_day_start(now, db))).fetchone()[0]
        if today >= LINE_PIECES_PER_DAY:
            raise not_run(PreparationRefusal('line_pieces_daily'))
        pending = db.execute(f"SELECT count(*) FROM preparations WHERE origin=? AND state IN ({','.join('?' * len(PIECE_PENDING_STATES))})",
                             (ORIGIN_LINE_PIECE, *PIECE_PENDING_STATES)).fetchone()[0]
        if pending >= MAX_PENDING_PIECES:
            raise not_run(PreparationRefusal('line_pieces_limit'))
        return None

    @staticmethod
    def _check_admission(db, check_spec, exclude=None):
        if check_spec is not None and db.execute(
                "SELECT count(*) FROM preparations WHERE check_spec IS NOT NULL "
                "AND state IN ('scheduled','running') AND id IS NOT ?", (exclude,)).fetchone()[0] >= MAX_ACTIVE_CHECKS:
            from .agent_runtime import not_run
            raise not_run(PreparationRefusal('too_many_checks'))

    @staticmethod
    def line_has_pending_work(db, line_id, exclude=None):
        """#1026: the periodic account is not a pending piece of the line's work."""
        return db.execute('SELECT 1 FROM preparations WHERE line_id=? AND state=? '
                          'AND origin IS NOT ? AND id IS NOT ? LIMIT 1',
                          (line_id, STATE_SCHEDULED, ORIGIN_LINE_ACCOUNT, exclude)).fetchone() is not None

    def line_resume_target(self, line_id, db=None):
        """The owner-opened, stopped or unscheduled brief eligible for an offer."""
        from .entrusted_lines import EntrustedLines, STATE_ACTIVE
        with (self.store.db() if db is None else nullcontext(db)) as db:
            lines = EntrustedLines(self.store, clock=self.clock)
            brief = lines.readable(line_id, db)
            provenance = lines.provenance(line_id, db) if brief else {}
            if provenance.get('change') != 'opened' or provenance.get('by', 'owner') != 'owner':
                return None
            if brief['state'] == STATE_ACTIVE and self.line_has_pending_work(db, line_id):
                return None
            return brief

    def _admit_line_resume(self, db, line_id, due_at, now):
        from .agent_runtime import not_run
        brief = self.line_resume_target(line_id, db)
        if brief is None:
            raise not_run(PreparationRefusal('line_resume_unavailable'))
        if due_at <= now or self.budget_day(due_at, db) != self.budget_day(now, db):
            raise not_run(PreparationRefusal('invalid_line_resume_offer'))
        if db.execute("SELECT 1 FROM tool_events WHERE tool=? AND created>=? "
                      "AND json_extract(detail,'$.line_id')=? LIMIT 1",
                      (LINE_RESUME_EVENT, self.owner_day_start(now, db), line_id)).fetchone():
            raise not_run(PreparationRefusal('line_resume_already_offered'))
        return brief

    @staticmethod
    def _exact_line_resume(row, offer_digest):
        if row['state'] != STATE_PROPOSED or row['origin'] != ORIGIN_LINE_RESUME or digest([row]) != offer_digest:
            raise PreparationRefusal('line_resume_stale')

    def decline_line_resume(self, preparation_id, offer_digest):
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self.store.require_read_checkpoint_writer(db)
            row = self._editable(db, preparation_id, (STATE_PROPOSED,))
            self._exact_line_resume(row, offer_digest)
            self.cancel(preparation_id, db=db)
            db.execute('UPDATE preparations SET last_outcome=? WHERE id=?', ('declined', preparation_id))

    def ensure_line_account(self, brief, *, created_from, db, accepted_by=ACCEPTED_OWNER_WORK,
                            channel=None, bundle_channel=None, replace_inactive=True):
        """Schedule the periodic account if absent, inside the caller's transaction."""
        from .entrusted_lines import account_rule, excerpt, line_name
        line_id = brief['line_id']
        rows = db.execute('SELECT state,channel,check_status FROM preparations WHERE line_id=? AND origin=? ORDER BY created_at DESC',
                          (line_id, ORIGIN_LINE_ACCOUNT)).fetchall()
        if any(row['state'] in ACTIVE_STATES for row in rows) or (rows and not replace_inactive):
            return None
        days = brief['account_every_days']
        account = self.create(kind=KIND_PREPARE, goal=LINE_ACCOUNT_GOAL, due_at=self.clock() + days * 86400,
                           timezone=str(self._owner_zone(db) or 'UTC'), recurrence=normalize_rrule(account_rule(days)),
                           channel=channel or (rows[0]['channel'] if rows else CHANNEL_TELEGRAM),
                           bundle_channel=bundle_channel, created_from=created_from,
                           state=STATE_SCHEDULED, accepted_by=accepted_by, origin=ORIGIN_LINE_ACCOUNT,
                           title=excerpt(msg('lines.account_title', task=line_name(brief)), TITLE_MAX_CHARS),
                           line_id=line_id, db=db)
        snapshots = [row['check_status'] for row in rows if row['check_status']]
        deleted = db.execute("SELECT json_extract(detail,'$.check_account') FROM tool_events "
                             "WHERE tool='preparation_lineage' AND status='deleted' "
                             "AND json_extract(detail,'$.line_id')=? AND json_extract(detail,'$.check_account') IS NOT NULL "
                             "ORDER BY json_extract(detail,'$.check_account.totals.runs') DESC, "
                             "json_extract(detail,'$.check_account.totals.wakes') DESC LIMIT 1", (line_id,)).fetchone()
        if deleted:
            snapshots.append(deleted[0])
        if snapshots:
            # The totals only increase. Deletion order may differ from the order in which accounts reported them.
            def reported(snapshot):
                counts = json.loads(snapshot)['totals']
                return counts['runs'], counts['wakes']

            previous = max(snapshots, key=reported)
            db.execute('UPDATE preparations SET check_status=? WHERE id=?', (previous, account['id']))
            account['check_status'] = previous
        return account

    def accept(self, preparation_id, accepted_by, *, accepted_from=None, db=None, replaces=None, offer_digest=None):
        """``proposed`` → ``scheduled``: the owner's explicit yes.

        #395: a watch whose window has already ended is refused (``expired``)
        in the same UPDATE, so a late yes never reads as a running watch.
        As in ``create``, ``replaces`` excludes the row the caller will cancel in the same transaction.
        """
        now = self.clock()
        owns_transaction = db is None
        with (self.store.db() if owns_transaction else nullcontext(db)) as db:
            if owns_transaction:
                db.execute('BEGIN IMMEDIATE')
                self.store.require_read_checkpoint_writer(db)
            row = db.execute('SELECT * FROM preparations WHERE id=?', (preparation_id,)).fetchone()
            if row and row['origin'] == ORIGIN_LINE_RESUME:
                # Build at the existing #659 transaction boundary: the owner act must activate both
                # the brief and row together. Ordinary scheduling, deduplication and restore cannot do it.
                self._exact_line_resume(dict(row), offer_digest)
                if accepted_by not in (ACCEPTED_OWNER_BUTTON, ACCEPTED_OWNER_ANSWER):
                    raise PreparationRefusal('line_resume_stale')
                if row['created_at'] < self.owner_day_start(now, db):
                    raise PreparationRefusal('line_resume_stale')
                brief = self.line_resume_target(row['line_id'], db)
                event = db.execute("SELECT detail FROM tool_events WHERE job_id=? AND tool=? "
                                   "AND json_extract(detail,'$.preparation_id')=? AND json_extract(detail,'$.line_id')=?",
                                   (row['created_from'], LINE_RESUME_EVENT, preparation_id, row['line_id'])).fetchone()
                if not (brief and event and json.loads(event['detail'])['version'] == brief['version']):
                    raise PreparationRefusal('line_resume_stale')
                from .entrusted_lines import EntrustedLines, STATE_STOPPED
                if brief['state'] == STATE_STOPPED:
                    after = EntrustedLines(self.store, clock=self.clock).restart(row['line_id'], accepted_from, db=db)
                    self.ensure_line_account(after, created_from=accepted_from, db=db)
            if row and row['state'] == STATE_PROPOSED and row['check_spec'] and row['until_at'] > now:
                from . import continuous_lines as continuous
                if continuous.active(db, row['line_id'], now) and continuous.has_other_executor(db, row['line_id'], replaces):
                    raise not_run(PreparationRefusal('line_executor_exists'))
                self._check_admission(db, row['check_spec'], replaces)
            changed = db.execute("UPDATE preparations SET state='scheduled',accepted_by=?,accepted_from=?,updated_at=? WHERE id=? AND state='proposed' "
                                 'AND (recurrence IS NOT ? OR until_at>=?) AND (check_spec IS NULL OR until_at>?)',
                                 (accepted_by, accepted_from, now, preparation_id, RECURRENCE_WINDOW, now, now)).rowcount
            row = self._schedule_row(db.execute('SELECT * FROM preparations WHERE id=?', (preparation_id,)).fetchone(), db)
            if not changed:
                if not row:
                    raise PreparationRefusal('not_found')
                raise PreparationRefusal('expired' if row['state'] == STATE_PROPOSED and (window_ended(row, now)
                                         or row['check_spec'] and now >= row['until_at']) else 'not_proposed')
            return row

    def cancel(self, preparation_id, *, db=None):
        """Stop every future run.  A Work already running finishes as its own Work."""
        now = self.clock()
        owns_transaction = db is None
        with (self.store.db() if owns_transaction else nullcontext(db)) as db:
            if owns_transaction:
                db.execute('BEGIN IMMEDIATE')
                self.store.require_read_checkpoint_writer(db)
            current = db.execute('SELECT updated_at FROM preparations WHERE id=?', (preparation_id,)).fetchone()
            # The cancellation's undo binding stays distinct even with an unchanged clock.
            revision = max(now, math.nextafter(current['updated_at'], math.inf)) if current else now
            changed = db.execute('UPDATE preparations SET state=?,updated_at=? WHERE id=? AND state IN (?,?,?,?)',
                                 (STATE_CANCELLED, revision, preparation_id, *ACTIVE_STATES)).rowcount
            self._clear_closed_budget_skips(db)
            row = db.execute('SELECT * FROM preparations WHERE id=?', (preparation_id,)).fetchone()
        if row is None:
            raise PreparationRefusal('not_found')
        return {**row, 'changed': bool(changed)}

    @staticmethod
    def owns_running(row, job):
        """Whether this Work owns the preparation's currently running slot (#717)."""
        return bool(row and job and row['state'] == STATE_RUNNING
                    and row['last_run_job_id'] == job['id']
                    and preparation_of(job['request_key']) == row['id'])

    @staticmethod
    def narrowing_values(row, changes, now):
        """Keep the existing slot anchor and a subset of its future slots (#717).

        Build: dateutil/RFC 5545 supplies recurrence, not Still's Work ownership or
        consumed-run budget. Reuse normalize_window and SQLite; no new scheduler.
        A multiple of the old interval preserves slots, including wall-clock windows.
        Lower the remaining count too: a larger interval must not extend a count-bound
        schedule beyond its previously final slot. Neither counts nor anchors reset.
        """
        if (row['recurrence'] != RECURRENCE_WINDOW or not changes
                or set(changes) - {'until', 'max_runs', 'every_minutes'}):
            raise PreparationRefusal('invalid_edit')
        maximum = changes.get('max_runs', row['max_runs'])
        maximum = _whole_number(maximum)
        if maximum is None or not row['run_count'] <= maximum <= row['max_runs']:
            raise PreparationRefusal('invalid_edit')
        terms = stored_window_terms(row)
        every, end, _ = normalize_window(
            changes.get('every_minutes', row['every_seconds'] // 60),
            changes.get('until', iso(row['until_at'])), None, row['due_at'], row['timezone'], now,
            recurrence=terms.get('recurrence'))
        if every < row['every_seconds'] or every % row['every_seconds'] or end > row['until_at']:
            raise PreparationRefusal('invalid_edit')
        report = terms.get('report_seconds')
        if report and report % every:
            raise PreparationRefusal('invalid_edit')
        ratio = every // row['every_seconds']
        maximum = min(maximum, row['run_count'] + (row['max_runs'] - row['run_count']) // ratio)
        if terms.get('recurrence'):
            # The next day's counter starts at zero; its last slot must also stay inside
            # the old daily count-bound schedule, not just inside today's remainder.
            maximum = min(maximum, (row['max_runs'] - 1) // ratio + 1)
            if maximum < row['run_count']:
                raise PreparationRefusal('invalid_edit')
            if ratio > 1:
                # A skipped DST hour can consume fewer of the coarser slots. A count
                # alone could then admit a later slot than the old daily count allowed.
                # Keep even the earliest possible old final wall slot as an end bound.
                tz = zone(row['timezone'])
                opening = datetime.fromtimestamp(row['rule_start'], tz).time()
                day = datetime.fromtimestamp(row['due_at'], tz).date()
                last = datetime.combine(day, opening) + timedelta(seconds=(row['max_runs'] - 1) * row['every_seconds'])
                end = min(end, last.replace(tzinfo=tz).timestamp())
                if end <= row['due_at']:
                    raise PreparationRefusal('invalid_edit')
        if end + run_seconds() > row['window_end']:
            raise PreparationRefusal('invalid_edit')
        updates = dict(every_seconds=every, until_at=end, window_end=end + run_seconds(), max_runs=maximum)
        if end != row['until_at'] and terms.get('recurrence'):
            terms['end_time'] = datetime.fromtimestamp(end, zone(row['timezone'])).time().isoformat()
            updates['window_terms'] = json.dumps(terms, sort_keys=True)
        return {key: value for key, value in updates.items() if value != row[key]}

    def change_own(self, preparation_id, work_id, operation, changes=None, *, reason='', notify_to=None):
        """Atomic current-slot change, receipt and outbox. Never creates a settings draft.

        Adopt sqlite3 BEGIN IMMEDIATE and TelegramStore.queue_notification(db=...).
        tool_events retains fingerprints even when the bounded settings audit rolls off.
        """
        fingerprint = hashlib.sha256(json.dumps([preparation_id, work_id, operation, changes],
                                               sort_keys=True).encode()).hexdigest()
        if operation == 'check':
            from . import condition_checks as checks
            previous_row = self.get(preparation_id)
            if (not self.owns_running(previous_row, self.store.job(work_id)) or not previous_row['check_spec']
                    or not isinstance(changes, dict) or set(changes) != {'check'}):
                raise PreparationRefusal('self_change_refused')
            old_spec = json.loads(previous_row['check_spec'])
            # A saved argument might only have become a known secret since setup.
            if checks._redact(self.store, old_spec['arguments']) != old_spec['arguments']:
                raise PreparationRefusal('check_credential')
            arguments, interval = checks.parse_for_store(self.store, changes['check'])
            spec = checks.prove(self.store, work_id, arguments, interval)
        now = self.clock()
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self.store.require_read_checkpoint_writer(db)
            row = db.execute('SELECT * FROM preparations WHERE id=?', (preparation_id,)).fetchone()
            job = db.execute('SELECT * FROM jobs WHERE id=?', (work_id,)).fetchone()
            if (not self.owns_running(row, job) or row['self_change_undone']
                    or row['own_run_revision'] is None or row['updated_at'] != row['own_run_revision']):
                raise PreparationRefusal('self_change_refused')
            if db.execute("SELECT 1 FROM tool_events WHERE job_id=? AND tool='preparation_self_change' "
                          "AND json_extract(detail,'$.fingerprint')=?", (work_id, fingerprint)).fetchone():
                raise PreparationRefusal('self_change_refused')
            row = dict(row)
            if operation == 'edit':
                if row.get('check_spec'):
                    raise PreparationRefusal('self_change_refused')
                updates = self.narrowing_values(row, changes, now)
            elif operation == 'check':
                if row['check_spec'] != previous_row['check_spec'] or spec == old_spec:
                    raise PreparationRefusal('self_change_refused')
                if any(set(arguments[key]) - set(old_spec['arguments'][key]) for key in ('hosts', 'secrets')):
                    raise PreparationRefusal('check_widened')
                if now + interval > row['until_at']:
                    raise PreparationRefusal('check_window')
                updates = {'check_spec': json.dumps(spec, sort_keys=True)}
            elif operation == 'goal':
                # #968: the run's own instruction for the next runs; the caller decided it may.
                goal = (changes or {}).get('goal') if isinstance(changes, dict) else None
                if row['kind'] != KIND_PREPARE or not isinstance(goal, str) or set(changes) != {'goal'}:
                    raise PreparationRefusal('self_change_refused')
                updates = {} if goal == row['goal_text'] else {'goal_text': goal}
            elif operation == 'cancel':
                updates = {'state': STATE_CANCELLED}
            elif operation == 'pause':
                updates = {} if row['run_return_state'] == STATE_PAUSED else {'run_return_state': STATE_PAUSED}
            else:
                raise PreparationRefusal('self_change_refused')
            if not updates:
                raise PreparationRefusal('self_change_refused')
            revision = max(now, math.nextafter(row['updated_at'], math.inf))
            event = dict(at=now, draft_ref=fingerprint, fingerprint=fingerprint, preparation_id=row['id'],
                         revision=revision, target='preparations.' + operation, terminal='own-run',
                         before={key: row[key] for key in updates}, after=dict(updates), reason=reason,
                         snapshot={key: row[key] for key in ('kind',
                             'due_at', 'timezone', 'recurrence', 'channel', 'delivery_mode', 'every_seconds',
                             'until_at', 'max_runs', 'window_terms', 'rule_start', 'run_count', 'state', 'run_return_state')},
                         run_key=job['request_key'].split('/', 1)[0])
            event['outside_content'] = bool(db.execute(
                'SELECT 1 FROM work_intake_sources WHERE job_id=? LIMIT 1', (work_id,)).fetchone())
            previous = json.loads(row['self_change'] or '{}')
            combined = dict(event)
            if previous and previous.get('run_key') == event['run_key'] and not row['self_change_undone']:
                combined['before'] = {**event['before'], **previous['before']}
                combined['after'] = {**previous['after'], **event['after']}
                combined['operations'] = list(dict.fromkeys([*previous.get('operations',
                    [previous['target'].split('.')[-1]]), operation]))
            else:
                combined['operations'] = [operation]
            if operation == 'check':
                status = json.loads(row['check_status'] or '{}')
                # A repair clears an automatic failure pause. An explicit self-pause still takes effect.
                if ('~wake-' in event['run_key'] and status.get('paused')
                        and 'pause' not in combined['operations']):
                    updates['run_return_state'] = None
                updates.update(every_seconds=interval, check_next_at=now + interval,
                               check_status=self._reset_check_result(status))
            updates.update(updated_at=revision, own_run_revision=revision, self_change=json.dumps(combined))
            db.execute(f"UPDATE preparations SET {','.join(k + '=?' for k in updates)} WHERE id=?",
                       (*updates.values(), preparation_id))
            audit = self.store.config('settings_audit', [], db=db)
            self.store.put('settings_audit', [*(audit if isinstance(audit, list) else []), event][-100:], db=db)
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (work_id, 'preparation_self_change', 'succeeded', json.dumps(event), now))
            # A separate status notice survives keep/when_needed result delivery and restarts.
            destination = notify_to
            anchor = db.execute('SELECT channel,chat_id FROM jobs WHERE id=?', (row['created_from'],)).fetchone()
            if (destination and anchor and str(anchor['channel']).startswith(destination[2] + ':')
                    and anchor['chat_id'] is not None and str(anchor['chat_id']) != str(destination[0])):
                destination = None
            # The same durable receipt is also rendered in Settings, including without a pairing.
            if destination and destination[2] != CHANNEL_WEB:
                self.store.telegram.queue_notification(work_id, *destination[:2],
                    SELF_CHANGE_NOTICE_PREFIX + fingerprint, fingerprint, surface=destination[2], db=db, created=now)
        return event

    @staticmethod
    def _reset_check_result(status):
        for key in ('failures', 'pending', 'paused', 'last_result'):
            status.pop(key, None)
        return json.dumps(status)

    @staticmethod
    def _self_change_values(row, values):
        """Interpret a recurring receipt on the current window's local date.

        Reuse datetime/zone and next_day_window's wall-time convention; this is
        receipt interpretation, not a new recurrence or settlement calculation.
        The run allowance after closing is elapsed time, even across midnight/DST.
        """
        values = dict(values)
        if (row.get('recurrence') != RECURRENCE_WINDOW or not stored_window_terms(row).get('recurrence')
                or values.get('until_at') is None):
            return values
        tz = zone(row['timezone'])
        closing = datetime.fromtimestamp(values['until_at'], tz)
        day = datetime.fromtimestamp(row['due_at'], tz).date()
        if day != closing.date():
            until = datetime.combine(day, closing.time().replace(fold=0), tzinfo=tz).timestamp()
            if 'window_end' in values:
                values['window_end'] = until + (values['window_end'] - values['until_at'])
            values['until_at'] = until
        return values

    @staticmethod
    def self_undoable(row, operation):
        event = json.loads(row.get('self_change') or '{}')
        operations = event.get('operations', [event.get('target', '').split('.')[-1]])
        # Settlement clears run_return_state and materializes the pause as state.
        after = Preparations._self_change_values(row, event.get('after', {}))
        if row['state'] == STATE_PAUSED and after.get('run_return_state') == STATE_PAUSED:
            after.pop('run_return_state')
        return bool(not row.get('self_change_undone') and operation in operations
                    and all(row.get(key) == value for key, value in after.items()))

    def _undo_self_change(self, db, row):
        """Restore the first before-values, leaving consumed runs and live Work intact."""
        row = dict(row)
        now = self.clock()
        from . import continuous_lines as continuous
        planner = row['origin'] == ORIGIN_LINE_PLANNER
        if planner and not continuous.current_planner(db, row, now):
            raise PreparationRefusal('resume_expired')
        event = json.loads(row['self_change'])
        updates = self._self_change_values(row, event['before'])
        if 'check_spec' in updates:
            # The spec is the receipt value. Derive the cadence and leave the counters alone.
            interval = json.loads(updates['check_spec'])['every_seconds']
            updates.update(every_seconds=interval,
                           check_next_at=min(row['until_at'], now + interval),
                           check_status=self._reset_check_result(json.loads(row['check_status'] or '{}')))
        updates.update(self_change_undone=1, updated_at=max(now, math.nextafter(row['updated_at'], math.inf)))
        if row['state'] not in ACTIVE_STATES:
            active = db.execute('SELECT count(*) FROM preparations WHERE state IN (?,?,?,?)', ACTIVE_STATES).fetchone()[0]
            if active >= MAX_ACTIVE:
                raise PreparationRefusal('too_many')
        job = db.execute('SELECT status,delivery FROM jobs WHERE id=?', (row['last_run_job_id'],)).fetchone()
        live = row['state'] == STATE_RUNNING or bool(job and (
            ws.is_active_preparation(job['status']) or job['delivery'] in ('pending', 'sending')))
        if live:
            updates['state'] = STATE_RUNNING
        elif planner:
            return_state = updates.get('run_return_state', row['run_return_state']) or STATE_SCHEDULED
            updates.update(state=return_state, run_return_state=None)
        else:
            restored = {**row, **updates}
            # Already scheduled means due_at is the unconsumed next slot, not the last run.
            following = row['due_at']
            return_state = updates.get('run_return_state', row.get('run_return_state') or
                (STATE_PAUSED if row['state'] == STATE_PAUSED else STATE_SCHEDULED)) or STATE_SCHEDULED
            if return_state == STATE_SCHEDULED and row.get('check_spec'):
                if now >= restored['until_at'] or row['run_count'] >= restored['max_runs']:
                    raise PreparationRefusal('resume_expired')
                updates.setdefault('check_next_at', min(restored['until_at'], max(now, row['check_next_at'])))
            elif return_state != STATE_PAUSED and (row['state'] != STATE_SCHEDULED or following < now):
                if following <= now:
                    following = (next_window_slot(restored, now) if row['recurrence'] == RECURRENCE_WINDOW
                                 else next_due(following, row['timezone'], row['recurrence'], now,
                                               row.get('rule_start'), row['run_count']))
                    if following is None and row['recurrence'] == RECURRENCE_WINDOW:
                        advance = next_day_window(restored, now)
                        updates.update(advance)
                        following = advance.get('due_at')
            if following is None:
                raise PreparationRefusal('resume_expired')
            updates.update(state=return_state,
                           run_return_state=None, due_at=following)
        if updates['state'] in (STATE_SCHEDULED, STATE_RUNNING):
            self._check_admission(db, row.get('check_spec'), row['id'])
        db.execute(f"UPDATE preparations SET {','.join(k + '=?' for k in updates)} WHERE id=?",
                   (*updates.values(), row['id']))
        if row['state'] in (STATE_PAUSED, STATE_CANCELLED) and updates['state'] in (STATE_SCHEDULED, STATE_RUNNING):
            continuous.executor_paused_or_resumed(db, {**row, **updates}, now, resumed=True)
        return dict(db.execute('SELECT * FROM preparations WHERE id=?', (row['id'],)).fetchone())

    def pause(self, preparation_id, *, work_id=None):
        """Keep a schedule without allowing the tick to run it (#575).

        A run pausing its own preparation keeps the row ``running`` so the run
        settles normally; ``settle`` then returns it as paused (#717).
        """
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self.store.require_read_checkpoint_writer(db)
            row = db.execute('SELECT * FROM preparations WHERE id=?', (preparation_id,)).fetchone()
            job = db.execute('SELECT id,request_key FROM jobs WHERE id=?', (work_id,)).fetchone() if work_id else None
            now = self.clock()
            if self.owns_running(row, job):
                db.execute('UPDATE preparations SET run_return_state=?,updated_at=? WHERE id=? AND state=?',
                           (STATE_PAUSED, now, row['id'], STATE_RUNNING))
            else:
                row = self._editable(db, preparation_id, (STATE_SCHEDULED, STATE_PAUSED))
                revision = max(now, math.nextafter(row['updated_at'], math.inf)) if row.get('check_spec') else now
                db.execute('UPDATE preparations SET state=?,updated_at=? WHERE id=?',
                           (STATE_PAUSED, revision, row['id']))
            from . import continuous_lines as continuous
            continuous.executor_paused_or_resumed(db, row, now)
        return self.get(preparation_id)

    @staticmethod
    def _editable(db, preparation_id, states):
        row = db.execute('SELECT * FROM preparations WHERE id=?', (preparation_id,)).fetchone()
        if row is None:
            raise PreparationRefusal('not_found')
        if row['state'] == STATE_RUNNING:
            raise PreparationRefusal('already_running')
        if row['state'] not in states:
            raise PreparationRefusal('invalid_state')
        return dict(row)

    def resume(self, preparation_id):
        """Resume at a future slot or re-enable a current planner's triggers."""
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self.store.require_read_checkpoint_writer(db)
            row = db.execute('SELECT * FROM preparations WHERE id=?', (preparation_id,)).fetchone()
            if row and self.self_undoable(dict(row), 'pause'):
                return self._undo_self_change(db, row)
        return self._resume(preparation_id, STATE_PAUSED)

    def restore(self, preparation_id, *, cancelled_at=None, self_change_run_key=None):
        """Undo cancellation, optionally binding the undo to a notice's revision."""
        # Owner-only restore also reapplies the audited bounds after a self-narrow.
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self.store.require_read_checkpoint_writer(db)
            row = db.execute('SELECT * FROM preparations WHERE id=?', (preparation_id,)).fetchone()
            if self_change_run_key is not None and (not row or
                    json.loads(row['self_change'] or '{}').get('run_key') != self_change_run_key):
                raise PreparationRefusal('self_change_refused')
            if row and (cancelled_at is None or row['updated_at'] == cancelled_at) and any(
                    self.self_undoable(dict(row), operation) for operation in SELF_CHANGE_OPERATIONS):
                return self._undo_self_change(db, row)
            if self_change_run_key is not None:
                raise PreparationRefusal('self_change_refused')
        return self._resume(preparation_id, STATE_CANCELLED, cancelled_at=cancelled_at)

    def _resume(self, preparation_id, state, *, cancelled_at=None):
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self.store.require_read_checkpoint_writer(db)
            now = self.clock()
            row = self._editable(db, preparation_id, (state,))
            if row['origin'] == ORIGIN_LINE_RESUME and row['accepted_by'] is None:
                raise PreparationRefusal('invalid_state')
            if cancelled_at is not None and row['updated_at'] != cancelled_at:
                raise PreparationRefusal('invalid_state')
            from . import continuous_lines as continuous
            planner = row['origin'] == ORIGIN_LINE_PLANNER
            if planner and not continuous.current_planner(db, row, now):
                raise PreparationRefusal('resume_expired')
            if state == STATE_CANCELLED:
                active = db.execute('SELECT count(*) FROM preparations WHERE state IN (?,?,?,?)', ACTIVE_STATES).fetchone()[0]
                if active >= MAX_ACTIVE:
                    raise PreparationRefusal('too_many')
                # #784: cancelled while its run was still working: it goes back to running, so that run
                # settles as usual and schedules the next slot; no second run starts beside it.
                run = (db.execute('SELECT status,delivery FROM jobs WHERE id=?', (row['last_run_job_id'],)).fetchone()
                       if row['last_run_job_id'] else None)
                in_progress = bool(run and (ws.is_active_preparation(run['status']) or run['delivery'] in ('pending', 'sending')))
            else:
                in_progress = False
            revision = max(now, math.nextafter(row['updated_at'], math.inf))
            if row['self_change']:
                db.execute('UPDATE preparations SET self_change_undone=1 WHERE id=?', (row['id'],))
            self._check_admission(db, row.get('check_spec'), row['id'])
            if in_progress or planner:
                db.execute('UPDATE preparations SET state=?,updated_at=? WHERE id=?',
                           (STATE_RUNNING if in_progress else STATE_SCHEDULED, revision, row['id']))
            elif row.get('check_spec'):
                if now >= row['until_at'] or row['run_count'] >= row['max_runs']:
                    raise PreparationRefusal('resume_expired')
                following = min(row['until_at'], max(row['check_next_at'], now))
                db.execute('UPDATE preparations SET state=?,check_next_at=?,updated_at=? WHERE id=?',
                           (STATE_SCHEDULED, following, revision, row['id']))
            else:
                following = row['due_at']
                if row['recurrence'] == RECURRENCE_WINDOW:
                    expired = window_ended(row, now) or row['run_count'] >= row['max_runs']
                    if expired or following <= now:
                        following = None if expired else next_window_slot(row, now)
                        if following is None:
                            advance = next_day_window(row, now)
                            following = advance.get('due_at')
                            if advance:
                                db.execute('UPDATE preparations SET until_at=?,window_end=?,run_count=? WHERE id=?',
                                           (advance['until_at'], advance['window_end'], advance['run_count'], row['id']))
                elif following <= now:
                    following = next_due(following, row['timezone'], row['recurrence'], now, row.get('rule_start'), row.get('run_count', 0))
                if following is None or following <= now:
                    raise PreparationRefusal('resume_expired')
                db.execute('UPDATE preparations SET state=?,due_at=?,updated_at=? WHERE id=?',
                           (STATE_SCHEDULED, following, revision, row['id']))
            continuous.executor_paused_or_resumed(db, row, now, resumed=True)
        return self.get(preparation_id)

    def edit(self, preparation_id, *, states=(STATE_PROPOSED, STATE_SCHEDULED, STATE_PAUSED), expected_max_runs=None, **changes):
        """Validate schedule changes without accepting a proposal or changing its goal (#575).

        ``states`` narrows which states may be edited, checked in the same transaction (#702).
        ``expected_max_runs`` binds a conversation's displayed count through the atomic write.
        """
        now = self.clock()
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self.store.require_read_checkpoint_writer(db)
            row = self._editable(db, preparation_id, states)
            updates = self.preview_edit(row, changes, now, db=db)
            if expected_max_runs is not None and updates.get('max_runs') != expected_max_runs:
                raise PreparationRefusal('invalid_edit')
            updates['updated_at'] = max(now, math.nextafter(row['updated_at'], math.inf))
            updates['self_change_undone'] = int(bool(row['self_change']) or row['self_change_undone'])
            if row['origin'] != ORIGIN_LINE_ACCOUNT:
                from . import continuous_lines as continuous
                if continuous.active(db, row['line_id'], now):
                    continuous.fence(db, row['line_id'], now)
            db.execute(f"UPDATE preparations SET {','.join(k + '=?' for k in updates)} WHERE id=?",
                       (*updates.values(), preparation_id))
        return self.get(preparation_id)

    def preview_edit(self, row, changes, now, *, db=None):
        """Use the same recorded slots for a Settings preview and the atomic edit."""
        if (row.get('check_spec') or row.get('origin') == ORIGIN_LINE_PLANNER
                or row['state'] == STATE_PROPOSED and row.get('origin') == ORIGIN_LINE_RESUME):
            raise PreparationRefusal('invalid_edit')
        if db is None:
            with self.store.db() as db:
                return self.preview_edit(row, changes, now, db=db)
        # A continuation has the same slot key; it never consumes another check.
        keys = db.execute('SELECT request_key FROM jobs WHERE request_key LIKE ?',
                          (REQUEST_KEY_PREFIX + row['id'] + ':%',)).fetchall()
        slots = {float(slot_key(record['request_key']).rsplit(':', 1)[1]) for record in keys}
        return self.edit_values(row, changes, now, completed_slots=slots)

    @staticmethod
    def edit_values(row, changes, now, *, completed_slots=None):
        """The same validation for a Settings draft and its application (#575)."""
        if 'goal' in changes or 'goal_text' in changes:
            raise PreparationRefusal('goal_not_editable')
        if not changes or set(changes) - {'due', 'timezone', 'recurrence', 'rrule', 'every_minutes', 'until', 'max_runs'}:
            raise PreparationRefusal('invalid_edit')
        due_at, timezone_name = row['due_at'], row['timezone']
        if 'due' in changes or 'timezone' in changes:
            due_at, timezone_name = parse_due(changes.get('due', datetime.fromtimestamp(due_at, zone(timezone_name)).isoformat()),
                                               changes.get('timezone', timezone_name), timezone_name, now)
        updates = {'due_at': due_at, 'timezone': timezone_name}
        if 'rrule' in changes and ('recurrence' in changes or row['recurrence'] == RECURRENCE_WINDOW
                                  or set(changes) & {'every_minutes', 'until', 'max_runs'}):
            raise PreparationRefusal('invalid_rrule')
        if row['recurrence'] == RECURRENCE_WINDOW:
            terms = stored_window_terms(row)
            if 'recurrence' in changes:
                raise PreparationRefusal('invalid_window', msg('preparations.window_recurrence', value=repr(changes['recurrence'])))
            # For a one-shot watch, #693 counts the remaining slots from the current due time.
            # A recurring watch's count is per day, independent of any skipped slots today.
            rhythm = terms.get('recurrence')
            rebased = (due_at, timezone_name) != (row['due_at'], row['timezone'])
            if rebased:
                updates['last_report_at'] = None
            anchor = due_at if rebased else row.get('rule_start')
            done = int(row.get('run_count') or 0)
            start = due_at
            if rhythm:
                tz = zone(timezone_name)
                date = datetime.fromtimestamp(due_at, tz).date()
                opening = datetime.fromtimestamp(anchor, tz).time()
                start = datetime.combine(date, opening, tzinfo=tz).timestamp()
                if datetime.fromtimestamp(row['due_at'], tz).date() != date:
                    updates['run_count'] = 0
                done = 0
            requested = changes.get('until', datetime.fromtimestamp(row['until_at'], zone(timezone_name)).isoformat())
            requested_until = parse_due(requested, timezone_name, timezone_name, now)[0]
            if not rebased and requested_until == row['until_at'] and terms.get('requested_until') is not None:
                # The web form submits unchanged fields too. Keep the owner's end,
                # then recompute whether the cap still binds the edited schedule.
                requested_until = terms['requested_until']
                if rhythm:
                    local = datetime.fromtimestamp(requested_until, zone(timezone_name))
                    requested_until = datetime.combine(date, local.time(), tzinfo=zone(timezone_name)).timestamp()
                requested = datetime.fromtimestamp(requested_until, zone(timezone_name)).isoformat()
            # A cadence/deadline edit keeps the pending slot. The original
            # budget anchor validates the allowance, not that slot's reachability.
            if requested_until <= now or requested_until < due_at:
                raise PreparationRefusal('invalid_window', msg('preparations.window_until_order',
                                         value=repr(requested), due=iso(max(now, due_at))))
            budget_start = start if rhythm else (anchor if anchor is not None else start - done * row['every_seconds'])
            every, until_at, remaining = normalize_window(
                changes.get('every_minutes', row['every_seconds'] // 60),
                requested,
                None, budget_start, timezone_name, now, recurrence=rhythm)
            if rhythm:
                remaining, slots = daily_window_slots(every, start, requested_until, timezone_name, rhythm)
            else:
                tz = zone(timezone_name)
                if completed_slots is None:
                    completed_slots = [start - row['every_seconds'] * i for i in range(1, done + 1)]
                used = {}
                for slot in completed_slots:
                    day = datetime.fromtimestamp(slot, tz).date()
                    used[day] = used.get(day, 0) + 1
                # Legacy/pruned history cannot grant another allowance. Charge
                # unlocated consumed checks to the pending date conservatively.
                day = datetime.fromtimestamp(start, tz).date()
                used[day] = used.get(day, 0) + max(0, done - len(completed_slots))
                remaining, slots = daily_window_slots(every, start, requested_until, timezone_name, used=used,
                    allowance=max(0, window_run_cap(budget_start, requested_until, timezone_name) - done))
            if rhythm and until_at < due_at:
                raise PreparationRefusal('invalid_window', msg('preparations.window_until_order',
                                         value=repr(changes.get('until')), due=iso(due_at)))
            # Like creation, a changed period derives its allowance unless the
            # edit explicitly supplies a total. Never retain a silent old cap.
            total = changes.get('max_runs', None if set(changes) & {'until', 'every_minutes'} else row['max_runs'])
            if total not in (None, '') and not 1 <= (_whole_number(total) or 0) <= window_run_cap(budget_start, requested_until, timezone_name, rhythm):
                raise PreparationRefusal('invalid_max_runs')
            runs = done + remaining if total in (None, '') else min(_whole_number(total), done + remaining)
            if runs < 1:
                raise PreparationRefusal('invalid_max_runs')
            cap_shortened = remaining < slots and runs == done + remaining
            until_at = requested_until
            if cap_shortened and runs > done:
                if rhythm:
                    local = datetime.fromtimestamp(start, zone(timezone_name)).replace(tzinfo=None)
                    until_at = (local + timedelta(seconds=every * (runs - done - 1))).replace(tzinfo=zone(timezone_name)).timestamp()
                else:
                    until_at = start + every * (runs - done - 1)
            updates.update(every_seconds=every, until_at=until_at, window_end=until_at + run_seconds(), max_runs=runs)
            report_every_minutes = terms['report_seconds'] // 60 if 'report_seconds' in terms else None
            updated_terms = window_terms((every, until_at, runs), rhythm, budget_start, timezone_name,
                                         report_every_minutes, row['delivery_mode'], requested_until)
            updates.update(window_terms=updated_terms, rule_start=anchor if anchor is not None else budget_start)
        else:
            if set(changes) & {'every_minutes', 'until', 'max_runs'}:
                raise PreparationRefusal('invalid_window', msg('preparations.window_edit',
                                         fields=', '.join(sorted(set(changes) & {'every_minutes', 'until', 'max_runs'}))))
            updates['recurrence'] = (normalize_rrule(changes['rrule']) if 'rrule' in changes else
                                     normalize_recurrence(changes.get('recurrence', row['recurrence'])))
            if row.get('max_runs') is not None:
                rule = updates['recurrence']
                count = rule_parts(rule).get('COUNT') if rule and rule.startswith('RRULE:') else None
                updates['max_runs'] = int(count) if count else None
            if (due_at, timezone_name, updates['recurrence']) != (row['due_at'], row['timezone'], row['recurrence']):
                updates['rule_start'] = due_at if updates['recurrence'] and updates['recurrence'].startswith('RRULE:') else None
                updates['run_count'] = 0
        return updates

    def run_now(self, preparation_id, *, channel, chat_id, now=None):
        """Claim an extra current-time slot through the tick's atomic claim (#575)."""
        now = self.clock() if now is None else now
        return self.start({'id': preparation_id}, channel=channel, chat_id=chat_id, now=now, immediate=True)

    def delete(self, preparation_id):
        """Remove the row.  Its past Works and their Evidence stay."""
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self.store.require_read_checkpoint_writer(db)
            row = db.execute('SELECT state,line_id,origin,created_from,piece_of,check_spec,check_status FROM preparations WHERE id=?',
                             (preparation_id,)).fetchone()
            if row is None:
                raise PreparationRefusal('not_found')
            if row['state'] == STATE_RUNNING:
                raise PreparationRefusal('running')
            if row['line_id']:
                # A later piece can still carry dispatch receipts from the deleted row's Works.
                # Keep only the host linkage, with no goal or result text.
                lineage = {key: row[key] for key in ('line_id', 'origin', 'created_from', 'piece_of')}
                if row['check_spec']:
                    from .condition_checks import totals
                    lineage['check_totals'] = totals(json.loads(row['check_status'] or '{}'))
                elif row['origin'] == ORIGIN_LINE_ACCOUNT and row['check_status']:
                    lineage['check_account'] = json.loads(row['check_status'])
                db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                           (preparation_id, 'preparation_lineage', 'deleted', json.dumps(lineage), self.clock()))
            db.execute('DELETE FROM preparations WHERE id=?', (preparation_id,))
            self._clear_closed_budget_skips(db)
        return {'deleted': True, 'id': preparation_id}

    def declined_goals(self, now, limit=DECLINED_GOALS_LIMIT):
        """Goals of proposals the owner declined or let lapse in the last ``DECLINED_SECONDS`` (#582).

        A declined proposal is a row cancelled without ever being accepted; newest first.  #717: so is
        a row set up without the owner asking (``SELF_SET``) that was then cancelled - its cancel button
        is the owner's no.
        """
        with self.store.db() as db:
            return [row['goal_text'] for row in db.execute(
                'SELECT goal_text FROM preparations WHERE state=? AND (accepted_by IS NULL OR accepted_by IN '
                f"({','.join('?' * len(SELF_SET))})) AND COALESCE(origin,'') NOT IN (?,?) "
                'AND updated_at>=? ORDER BY updated_at DESC LIMIT ?',
                # #938: a piece cancelled with its line (or by the owner) ends a chain; it is not a declined proposal.
                (STATE_CANCELLED, *SELF_SET, ORIGIN_DAY_PLAN, ORIGIN_LINE_PIECE, now - DECLINED_SECONDS, int(limit)))]

    def proposed_from(self, work_id, suggestions=True):
        """The proposals ``work_id`` created; ``suggestions`` False leaves out the look-back's (#583)."""
        with self.store.db() as db:
            return [dict(row) for row in db.execute("SELECT * FROM preparations WHERE created_from=? AND state='proposed' "
                                                    + ('' if suggestions else 'AND origin IS NULL ')
                                                    + 'ORDER BY created_at', (work_id,))]

    def has_active_goal(self, goal):
        """Whether a proposed, scheduled, running or paused preparation has exactly this goal (#583)."""
        with self.store.db() as db:
            return db.execute('SELECT 1 FROM preparations WHERE goal_text=? AND state IN (?,?,?,?) LIMIT 1',
                              (goal, *ACTIVE_STATES)).fetchone() is not None

    def active_titles(self, limit=20):
        """The owner's names of the active preparations, for the look-back not to suggest them again (#583)."""
        with self.store.db() as db:
            rows = db.execute('SELECT title_text,goal_text FROM preparations WHERE state IN (?,?,?,?) '
                              'ORDER BY created_at DESC LIMIT ?', (*ACTIVE_STATES, int(limit))).fetchall()
        return [owner_title(dict(row)) for row in rows]

    # -- the daily plan (#780) ------------------------------------------------

    def day_plan(self):
        """The daily plan's row in any state, newest first, or None (#780).

        A cancelled or deleted-then-absent plan is told apart by the caller: a row in any state
        means the plan was set up once, and the owner's cancel is kept.
        """
        with self.store.db() as db:
            row = db.execute('SELECT * FROM preparations WHERE origin=? ORDER BY created_at DESC LIMIT 1',
                             (ORIGIN_DAY_PLAN,)).fetchone()
        return dict(row) if row else None

    def defer(self, preparation_id, now):
        """Let a scheduled recurring row's due slot pass unrun (#780); whether it moved.

        The slot is not replayed later: ``due_at`` moves to the first slot after ``now``, as a slot
        missed during downtime does.
        """
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self.store.require_read_checkpoint_writer(db)
            row = db.execute('SELECT * FROM preparations WHERE id=? AND state=? AND origin IS NOT ? AND due_at<=?',
                             (preparation_id, STATE_SCHEDULED, ORIGIN_LINE_PLANNER, now)).fetchone()
            following = row and next_due(row['due_at'], row['timezone'], row['recurrence'], now, row['rule_start'], row['run_count'])
            if not following:
                return False
            return bool(db.execute('UPDATE preparations SET due_at=?,updated_at=? WHERE id=? AND state=?',
                                   (following, now, preparation_id, STATE_SCHEDULED)).rowcount)

    def set_by_day_plan(self, since, limit=DAY_PLAN_RECORD_ITEMS):
        """What daily-plan runs set up since ``since``, newest first, each with ``offered_works`` (#780).

        ``offered_works`` counts the distinct Works whose context a result of that preparation was
        placed in (``OFFERED_EVENT_TOOL`` rows): an observed fact, not a judgment that it was useful.
        """
        with self.store.db() as db:
            rows = [dict(row) for row in db.execute(
                'SELECT p.*, j.status AS last_work_status FROM preparations p '
                'LEFT JOIN jobs j ON j.id=p.last_run_job_id '
                'WHERE ((p.accepted_by=? AND p.origin IS NULL) OR p.origin=?) AND p.created_at>=? '
                'ORDER BY p.created_at DESC LIMIT ?', (ACCEPTED_DAY_PLAN, ORIGIN_LINE_RESUME, since, int(limit)))]
            offered = {}
            if rows:
                for event in db.execute('SELECT job_id,detail FROM tool_events WHERE tool=? AND created>=?',
                                        (OFFERED_EVENT_TOOL, since)):
                    try:
                        ids = json.loads(event['detail']).get('preparation_ids') or []
                    except (TypeError, ValueError, AttributeError):
                        continue
                    for preparation_id in ids:
                        offered.setdefault(preparation_id, set()).add(event['job_id'])
        for row in rows:
            row['offered_works'] = len(offered.get(row['id'], ()))
        return rows

    def record_offered(self, work_id, preparation_ids, now=None):
        """Record once per Work which prepared results were placed in its context (#780)."""
        ids = sorted({str(item) for item in preparation_ids})
        if not ids or not work_id:
            return False
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self.store.require_read_checkpoint_writer(db)
            if db.execute('SELECT 1 FROM tool_events WHERE job_id=? AND tool=? LIMIT 1',
                          (work_id, OFFERED_EVENT_TOOL)).fetchone():
                return False
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (work_id, OFFERED_EVENT_TOOL, 'recorded', json.dumps({'preparation_ids': ids}),
                        self.clock() if now is None else now))
        return True

    # -- look-back suggestions (#583) ----------------------------------------

    def untold_scheduled_suggestions(self):
        """Every dream-scheduled row still awaiting its own notice, across anchors."""
        with self.store.db() as db:
            return [dict(row) for row in db.execute(
                'SELECT * FROM preparations WHERE origin=? AND state=? AND offered_at IS NULL ORDER BY created_at,id',
                (ORIGIN_DREAM, STATE_SCHEDULED))]

    def unoffered_suggestions(self, now, one_off=False):
        """The oldest legacy look-back proposals not yet offered, ``(work_id, rows)``, or ``(None, [])``.

        A first slot that has passed meanwhile moves to the recurrence's next slot,
        so the message shows the schedule an acceptance would keep; a one-off whose
        time passed lapses (#638).  ``one_off`` takes only a look back that holds one.
        """
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self.store.require_read_checkpoint_writer(db)
            db.execute(f'UPDATE preparations SET state=?,updated_at=? WHERE {self._LAPSED_ONE_OFF}',
                       (STATE_EXPIRED, now, STATE_PROPOSED, ORIGIN_DREAM, now))
            anchor = db.execute('SELECT created_from FROM preparations WHERE origin=? AND state=? AND offered_at IS NULL '
                                + ('AND recurrence IS NULL ' if one_off else '')
                                + 'ORDER BY created_at LIMIT 1', (ORIGIN_DREAM, STATE_PROPOSED)).fetchone()
            if anchor is None:
                return None, []
            for row in db.execute('SELECT * FROM preparations WHERE created_from=? AND origin=? AND state=? AND due_at<=?',
                                  (anchor[0], ORIGIN_DREAM, STATE_PROPOSED, now)).fetchall():
                following = next_due(row['due_at'], row['timezone'], row['recurrence'], now)
                if following is not None:
                    db.execute('UPDATE preparations SET due_at=?,updated_at=? WHERE id=?', (following, now, row['id']))
            rows = [dict(row) for row in db.execute('SELECT * FROM preparations WHERE created_from=? AND origin=? '
                                                    'AND state=? AND offered_at IS NULL ORDER BY created_at',
                                                    (anchor[0], ORIGIN_DREAM, STATE_PROPOSED))]
        return anchor[0], rows

    def hold_suggestion(self, row, held, now):
        """A held dream never catches up a missed slot when delivery/upkeep releases it."""
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self.store.require_read_checkpoint_writer(db)
            current = db.execute('SELECT * FROM preparations WHERE id=? AND state=?',
                                 (row['id'], STATE_SCHEDULED)).fetchone()
            if not current:
                return
            updates = {'dream_held': int(held and (current['dream_held'] or current['due_at'] < now))}
            if not held and current['dream_held'] and current['due_at'] < now:
                current = dict(current)
                if current['recurrence'] == RECURRENCE_WINDOW:
                    following = next_window_slot(current, now)
                    if following is None:
                        advance = next_day_window(current, now)
                        updates.update(advance)
                        following = advance.get('due_at')
                else:
                    following = next_due(current['due_at'], current['timezone'], current['recurrence'], now,
                                         current.get('rule_start'), current['run_count'])
                updates.update(state=STATE_SCHEDULED if following is not None else STATE_EXPIRED, updated_at=now)
                if following is not None:
                    updates['due_at'] = following
            db.execute(f"UPDATE preparations SET {','.join(k + '=?' for k in updates)} WHERE id=?",
                       (*updates.values(), row['id']))

    def mark_offered(self, rows, now):
        """The rows one suggestion message offered: never offered again."""
        with self.store.db() as db:
            for row in rows:
                db.execute('UPDATE preparations SET offered_at=? WHERE id=? AND offered_at IS NULL', (now, row['id']))

    #: #638: an unanswered one-off suggestion whose time came (params: state, origin, now).
    _LAPSED_ONE_OFF = 'state=? AND origin=? AND recurrence IS NULL AND due_at<=?'

    def expire_suggestions(self, now):
        """Expire unanswered look-back suggestions (#638) and resume offers from an earlier local day.

        ``expired``, not ``cancelled``: the owner never said no, so ``declined_goals`` does not count it.
        Nothing to expire is one read.
        """
        cutoff = now - SUGGESTION_EXPIRY_SECONDS
        where = ('origin=? AND ((state=? AND (created_at<=? OR (recurrence IS NULL AND due_at<=?))) '
                 'OR (state=? AND accepted_by=? AND offered_at IS NULL AND created_at<=?)) '
                 'OR (origin=? AND state=? AND created_at<?)')
        with self.store.db() as db:
            params = (ORIGIN_DREAM, STATE_PROPOSED, cutoff, now, STATE_SCHEDULED, ACCEPTED_DREAM, cutoff,
                      ORIGIN_LINE_RESUME, STATE_PROPOSED, self.owner_day_start(now, db))
            if db.execute(f'SELECT 1 FROM preparations WHERE {where} LIMIT 1', params).fetchone() is None:
                return 0
            return db.execute(f'UPDATE preparations SET state=?,updated_at=? WHERE {where}',
                              (STATE_EXPIRED, now, *params)).rowcount

    def rows(self, limit=100, finished_pieces=True):
        """Every row with its last run's Work status and delivery, active first.

        ``finished_pieces`` False leaves out the line pieces (#938) that no longer run, which
        ``finished_pieces_by_line`` returns grouped instead (#1005): a busy line ends up to
        ``LINE_PIECES_PER_DAY`` of them a day, and they would take up ``limit``.
        """
        where, values = '', ()
        if not finished_pieces:
            where = (f"WHERE NOT (COALESCE(p.origin,'')=? AND p.state NOT IN ({','.join('?' * len(ACTIVE_STATES))})) ")
            values = (ORIGIN_LINE_PIECE, *ACTIVE_STATES)
        with self.store.db() as db:
            return [self._schedule_row(row, db) for row in db.execute(
                'SELECT p.*, j.status AS last_status, j.delivery AS last_delivery, j.response AS last_response '
                'FROM preparations p LEFT JOIN jobs j ON j.id=p.last_run_job_id ' + where +
                "ORDER BY CASE p.state WHEN 'proposed' THEN 0 WHEN 'running' THEN 1 WHEN 'scheduled' THEN 2 WHEN 'paused' THEN 3 ELSE 4 END, "
                'p.due_at LIMIT ?', (*values, int(limit)))]

    def finished_pieces_by_line(self, shown=FINISHED_PIECES_SHOWN):
        """#1005: each line's pieces that no longer run, as counts by state and the newest ``shown`` rows.

        Returns ``{line_id: {'count': n, 'states': {state: n}, 'rows': [newest first]}}``.
        """
        marks = ','.join('?' * len(ACTIVE_STATES))
        finished = (STATE_DELIVERED, STATE_UNKNOWN, STATE_FAILED, STATE_CANCELLED, STATE_EXPIRED)
        # One statement, so the counts and the listed rows come from the same snapshot.
        counts = ', '.join(f'SUM(p.state=?) OVER (PARTITION BY p.line_id) AS "count_{state}"' for state in finished)
        groups = {}
        with self.store.db() as db:
            for row in db.execute(
                    'SELECT * FROM (SELECT p.*, j.status AS last_status, j.delivery AS last_delivery, j.response AS last_response, '
                    'ROW_NUMBER() OVER (PARTITION BY p.line_id ORDER BY p.updated_at DESC, p.id DESC) AS newest, '
                    f'COUNT(*) OVER (PARTITION BY p.line_id) AS total, {counts} '
                    f'FROM preparations p LEFT JOIN jobs j ON j.id=p.last_run_job_id WHERE p.origin=? AND p.state NOT IN ({marks}) '
                    'AND p.line_id IS NOT NULL) WHERE newest<=? ORDER BY line_id, newest',
                    (*finished, ORIGIN_LINE_PIECE, *ACTIVE_STATES, int(shown))):
                row = dict(row)
                states = {state: row.pop('count_' + state) for state in finished}
                group = groups.setdefault(row['line_id'], {'count': row['total'], 'rows': [],
                                                           'states': {state: n for state, n in states.items() if n}})
                row.pop('newest'), row.pop('total')
                group['rows'].append(row)
        return groups

    # -- the tick ----------------------------------------------------------

    def due(self, now, limit=TICK_LIMIT, busy=()):
        """The tick's one indexed query: due slots and runs ready to settle.

        Scheduled planners are handled by the trigger mailbox. A timed
        ``scheduled`` row is due at ``due_at``; a ``running`` row is ready
        once its Work is no longer queued, running or awaiting a connection
        and its delivery is no longer pending or in flight. Expired windows
        also surface so an unanswered prerequisite can terminate without AI.
        Immediate runs settle even before the regular due time (#575).
        A run whose Work is in ``busy`` (#970: still in its lane) is not ready yet.
        """
        busy=tuple(busy)
        with self.store.db() as db:
            self._clear_closed_budget_skips(db)
            return [dict(row) for row in db.execute(
                'SELECT p.*, j.status AS job_status, j.delivery AS job_delivery FROM preparations p '
                'LEFT JOIN jobs j ON j.id=p.last_run_job_id '
                "WHERE ((p.state='scheduled' AND p.due_at<=? AND p.check_spec IS NULL AND p.origin IS NOT ?) OR p.state='running') "
                f"AND (p.state='scheduled' OR j.id IS NULL OR j.id NOT IN ({','.join('?'*len(busy))})) "
                "AND (p.state='scheduled' OR j.id IS NULL OR (p.origin IS NOT ? AND p.recurrence='window' AND p.window_end<=?) "
                f"OR (j.status NOT IN {ws.sql_in(ws.ACTIVE_PREPARATION)} "
                "AND j.delivery NOT IN ('pending','sending'))) ORDER BY p.due_at LIMIT ?",
                (now, ORIGIN_LINE_PLANNER, *busy, ORIGIN_LINE_PLANNER, now, limit))]

    @staticmethod
    def run_refusal(db, work_id, now):
        """Recheck the accepted run in the caller's queue/admission transaction."""
        work = db.execute('SELECT request_key FROM jobs WHERE id=?', (work_id,)).fetchone()
        preparation_id = preparation_of(work['request_key'] if work else None)
        if not preparation_id:
            return None
        row = db.execute('SELECT * FROM preparations WHERE id=?', (preparation_id,)).fetchone()
        from . import continuous_lines as continuous
        planner = row and row['origin'] == ORIGIN_LINE_PLANNER
        if planner and not continuous.current_planner(db, row, now):
            return continuous.STOP_REASON
        if row and ((not planner and continuous.lapsed(db, row['line_id'], now))
                    or (row['state'] == STATE_CANCELLED and continuous.member_revision(db, work_id, row['line_id']))):
            return continuous.STOP_REASON
        window = row and not planner and row['recurrence'] == RECURRENCE_WINDOW
        if window and now >= row['window_end']:
            return 'preparation-window-expired'
        # #798: stop wins (before the row's own state: stopping a line also cancels its rows); a line
        # whose brief cannot be read stops its run instead of running without its rules.
        refusal = line_refusal(db, row['line_id']) if row else None
        if refusal:
            return refusal
        if not row or row['state'] != STATE_RUNNING or row['last_run_job_id'] != work_id:
            return 'preparation-inactive'
        # The current run already consumed its count; equality is allowed.
        if window and row['run_count'] > row['max_runs']:
            return 'preparation-run-limit'
        return None

    @staticmethod
    def wait_stamp(row):
        """The accepted version a run's owner wait is bound to (#821): identity, acceptance, revision, run."""
        return {'id': row['id'], 'accepted_by': row['accepted_by'], 'revision': row['updated_at'],
                'run': row['last_run_job_id']}

    @staticmethod
    def wait_refusal(db, work_id, stamp, *, self_set_write=False):
        """``WAIT_CHANGED`` unless Work ``work_id`` is still the current run of the owner-accepted
        preparation version ``stamp`` names (#821); None when it is.

        Inside the caller's transaction.  ``updated_at`` is the row's revision: every owner change a
        running row admits (cancel, restore, the run's own pause) moves it, and editing or pausing a
        running row is refused (``already_running``).
        """
        if not isinstance(stamp, dict):
            return WAIT_CHANGED
        row = db.execute('SELECT * FROM preparations WHERE id=?', (stamp.get('id'),)).fetchone()
        if not (row and row['accepted_by'] in (SELF_SET if self_set_write else OWNER_ACCEPTED) and row['accepted_by'] == stamp.get('accepted_by')
                and row['state'] == STATE_RUNNING and row['last_run_job_id'] == work_id == stamp.get('run')
                and row['updated_at'] == stamp.get('revision')):
            return WAIT_CHANGED
        return None

    def refuse_run(self, db, work_id, now, reason):
        """Finish a refused queued/waiting run without presenting setup as its result.

        Called only after run_refusal in the same transaction. Retain prior
        response/effect evidence and delivery state; never re-send a receipt.
        Already running Work is stopped through the existing budget callback.
        """
        cause = '예약된 작업의 실행 기간이나 조건이 끝나 원래 요청을 계속 실행하지 않았습니다.'
        changed = db.execute("UPDATE jobs SET status='failed',error=?,owner_cause=? WHERE id=? "
                             f"AND status IN {ws.sql_in(ws.REFUSABLE_PREPARATION)} "
                             "AND (status!='failed' OR error IS NOT ?)", (cause, cause, work_id, cause)).rowcount
        if not changed:
            return False
        row = db.execute('SELECT * FROM preparations WHERE last_run_job_id=? AND state=?',
                         (work_id, STATE_RUNNING)).fetchone()
        advance = next_day_window(dict(row), now) if row and reason == 'preparation-window-expired' else {}
        # #798: a run its line stopped (or whose brief cannot be read) did not expire; it ended unfinished.
        state = ((row['run_return_state'] or STATE_SCHEDULED) if advance
                 else STATE_FAILED if reason in (LINE_STOPPED, LINE_UNREADABLE) else STATE_EXPIRED)
        updates = {'state': state, 'last_outcome': STATE_FAILED,
                   'updated_at': now, 'run_return_state': None, **advance}
        db.execute(f"UPDATE preparations SET {','.join(key + '=?' for key in updates)} "
                   'WHERE last_run_job_id=? AND state=?', (*updates.values(), work_id, STATE_RUNNING))
        self._clear_closed_budget_skips(db)
        db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                   (work_id, 'preparation', 'withheld', json.dumps({'reason': reason, 'resumed': False}), now))
        if row and reason == 'preparation-window-expired':
            job = db.execute('SELECT chat_id,delivery FROM jobs WHERE id=?', (work_id,)).fetchone()
            # A pending paired reply will already render the expiry as the Work's failure.
            if not job['chat_id'] or job['delivery'] != 'pending':
                self._queue_status_notice(db, row, work_id, EXPIRED_NOTICE_KIND, now)
        return True

    def start(self, row, *, channel, chat_id, now, immediate=False, db=None, wake=None):
        """Claim one due slot and create its Work in ONE transaction.

        Returns the Work id, or None when the slot is no longer due (another
        claim, a cancel) or the queue refused it (the row stays scheduled and
        is tried again on a later tick).  The Work's request key names the
        slot, so a replayed claim finds the same Work instead of a second.
        """
        slot = now if immediate or wake else row['due_at']
        key = request_key(row['id'], slot)
        planner = wake and wake.get('planner')
        if planner:
            key = f"{REQUEST_KEY_PREFIX}{row['id']}:{slot}~wake-{planner}"
        elif wake:
            # The check slot survives a retry; the complete spec has its own namespace.
            spec_key = hashlib.sha256(row['check_spec'].encode()).hexdigest()
            key = f"{REQUEST_KEY_PREFIX}{row['id']}:{wake['slot']}~wake-{spec_key}"
        owns_transaction = db is None
        with (self.store.db() if owns_transaction else nullcontext(db)) as db:
            if owns_transaction:
                db.execute('BEGIN IMMEDIATE')
                self.store.require_read_checkpoint_writer(db)
            if wake:
                existing = db.execute('SELECT id FROM jobs WHERE request_key=?', (key,)).fetchone()
                if existing:
                    return existing['id']
            current = db.execute('SELECT * FROM preparations WHERE id=?', (row['id'],)).fetchone()
            if immediate:
                current = self._editable(db, row['id'], (STATE_SCHEDULED, STATE_PAUSED))
            elif (not current or current['state'] != STATE_SCHEDULED
                  or (not planner and current['due_at'] != row['due_at'])):
                return None
            current = dict(current)
            if current.get('check_spec'):
                if immediate:
                    key += '~manual'
                    self._check_admission(db, current['check_spec'], current['id'])
                elif not wake or current['check_spec'] != row['check_spec']:
                    return None
            elif wake and not planner:
                return None
            if current['origin'] == ORIGIN_LINE_PLANNER and not planner:
                if immediate:
                    raise PreparationRefusal('invalid_state')
                return None
            from . import continuous_lines as continuous
            standing = (continuous.current_planner(db, current, now) if planner
                        else continuous.active(db, current['line_id'], now))
            if planner and not standing:
                return None
            if standing and immediate and current.get('check_spec'):
                raise PreparationRefusal('invalid_state', 'Use the separate line planner for Work.')
            if not planner and continuous.lapsed(db, current['line_id'], now):
                return None
            existing = db.execute('SELECT id FROM jobs WHERE request_key=?', (key,)).fetchone()
            if immediate and existing:
                return existing['id']
            window = not planner and current['recurrence'] == RECURRENCE_WINDOW
            missed_day = False
            if window and not immediate and stored_window_terms(current).get('recurrence'):
                tz = zone(current['timezone'])
                missed_day = datetime.fromtimestamp(slot, tz).date() < datetime.fromtimestamp(now, tz).date()
            if window and not existing and (missed_day or now >= current['window_end']
                                            or (not (immediate and current.get('check_spec'))
                                                and int(current['run_count'] or 0) >= int(current['max_runs'] or 0))):
                if immediate:
                    raise PreparationRefusal('resume_expired')
                # #719: a watch never runs after its deadline or beyond its bound.
                advance = next_day_window(current, now)
                if advance:
                    db.execute('UPDATE preparations SET due_at=?,until_at=?,window_end=?,run_count=?,updated_at=? WHERE id=?',
                               (advance['due_at'], advance['until_at'], advance['window_end'], advance['run_count'], now, current['id']))
                else:
                    db.execute('UPDATE preparations SET state=?,updated_at=? WHERE id=? AND state=?',
                               (STATE_EXPIRED, now, current['id'], STATE_SCHEDULED))
                self._clear_closed_budget_skips(db)
                self._queue_status_notice(db, current, key, EXPIRED_NOTICE_KIND, min(now, slot))
                return None
            refusal = None if existing else line_refusal(db, current.get('line_id'))
            if refusal:
                # #798: a stopped line runs nothing; an unreadable brief stops the row instead of running without it.
                state = STATE_CANCELLED if refusal == LINE_STOPPED else STATE_FAILED
                db.execute('UPDATE preparations SET state=?,last_outcome=?,updated_at=? WHERE id=? AND state IN (?,?)',
                           (state, state, now, current['id'], STATE_SCHEDULED, STATE_PAUSED))
                self._clear_closed_budget_skips(db)
                db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                           (key, 'preparation', 'withheld', json.dumps({'reason': refusal, 'resumed': False,
                                                                        'preparation_id': current['id']}), now))
                if immediate:
                    raise PreparationRefusal('line_inactive')
                return None
            # Owner-pressed run-now and builtin reminders use no unattended allowance.
            # Existing Works (and their WorkLedger retries/continuations) keep their admission.
            counted = not immediate and not existing and current['kind'] == KIND_PREPARE
            if counted:
                # A previously consumed skip cannot be re-admitted by a replayed slot.
                if db.execute('SELECT 1 FROM tool_events WHERE job_id=? AND tool=? AND status=?',
                              (key, SCHEDULED_BUDGET_KEY, 'skipped')).fetchone():
                    return None
                day = self.budget_day(now, db=db)
                usage_key = SCHEDULED_USAGE_PREFIX + day
                previous_usage = self.store.config(usage_key, db=db)
                calls = previous_usage if previous_usage is not None else 0
                cap = self.budget_settings(db=db)['daily_calls']
                if cap and calls >= cap:
                    if wake:
                        self._queue_budget_notice(db, current, now, day, cap, slot,
                                                  wake['title'], wake['budget_destinations'])
                        wake['budget_refused'] = True
                    else:
                        self._skip_budget_slot(db, current, key, now, day, cap)
                    return None
            if existing:
                job_id = existing['id']
            elif current['kind'] == KIND_REMINDER:
                # The reminder text is the Work's observed result ('builtin':
                # no AI ran, as for notes); the existing terminal delivery
                # sends it once (mark-before-send).
                job_id = str(uuid.uuid4())
                text = reminder_text(current['goal_text'], slot, current['timezone'], now)
                db.execute('INSERT INTO jobs(id,request_key,message,channel,chat_id,status,response,error,delivery,provider,model,created) '
                           'VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                           (job_id, key, current['goal_text'], channel, chat_id, 'succeeded', text, None,
                            'pending' if chat_id else 'none', 'builtin', 'preparation', now))
                db.execute('INSERT INTO messages(role,content,channel,created,job_id) VALUES (?,?,?,?,?)',
                           ('assistant', text, channel, now, job_id))
            else:
                try:
                    job_id = self.store.enqueue(current['goal_text'], key, channel, chat_id, db=db)
                except ValueError:
                    if immediate:
                        raise PreparationRefusal('queue_full') from None
                    if wake:
                        wake['queue_refused'] = True
                    return None
            if current.get('piece_of') and not existing:
                # #938 (D5): a piece's run starts with its parent run's outside-content marks, so a chain
                # never carries outside text into an unmarked run.  Same transaction as the Work.
                self._inherit_piece_marks(db, current['piece_of'], job_id, now)
            if counted:
                # As in cloud daily_calls, charge admission, even if the engine later fails.
                # Enqueue, counter and evidence commit together; queue-full charges nothing.
                if previous_usage is None:
                    cutoff = (datetime.fromisoformat(day) - timedelta(days=SCHEDULED_USAGE_RETENTION_DAYS - 1)).date().isoformat()
                    db.execute('DELETE FROM config WHERE key GLOB ? AND key<?',
                               (SCHEDULED_USAGE_PREFIX + '*', SCHEDULED_USAGE_PREFIX + cutoff))
                self.store.put(usage_key, calls + 1, db=db)
                db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                           (job_id, SCHEDULED_BUDGET_KEY, 'admitted',
                            json.dumps({'preparation_id': current['id'], 'slot': iso(slot), 'day': day}), now))
            # #580: an extra immediate run does not consume a rule's scheduled count.
            terms = stored_window_terms(current)
            extra_rule = immediate and (str(current['recurrence'] or '').startswith('RRULE:')
                                        or bool(current.get('check_spec') or terms.get('recurrence') or terms.get('report_seconds')))
            run = int(current['run_count'] or 0) + (0 if existing or extra_rule else 1)
            return_state = current['state'] if immediate else wake['return_state'] if wake else None
            revision = max(now, math.nextafter(current['updated_at'], math.inf)) if current.get('check_spec') else now
            db.execute('UPDATE preparations SET state=?,last_run_job_id=?,run_count=?,updated_at=?,own_run_revision=?,run_return_state=? WHERE id=?',
                       (STATE_RUNNING, job_id, run, revision, revision, return_state, current['id']))
            if standing and not existing:
                continuous.record_membership(db, job_id, current['line_id'], standing['revision'])
            if wake:
                db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                           (job_id, CHECK_EVENT_TOOL, 'woken', json.dumps(wake['handoff']), now))
                sources = wake['outside'] if planner else [wake['outside']]
                for source in sources:
                    if source:
                        self.store._insert_intake_source(db, job_id, json.dumps(source, sort_keys=True), now)
            # Evidence: preparation id -> Work id; the Work's delivery is the receipt.
            detail = {'preparation_id': current['id'], 'kind': current['kind'], 'slot': iso(slot),
                      'recurrence': current['recurrence'], 'accepted_by': current['accepted_by'],
                      'channel': channel.split(':', 1)[0]}
            if window:
                detail.update(run=run, max_runs=current['max_runs'], until=iso(current['until_at']))
            if current.get('delivery_mode') == DELIVERY_WHEN_NEEDED:
                detail['delivery_mode'] = DELIVERY_WHEN_NEEDED
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (job_id, 'preparation', 'succeeded', json.dumps(detail), now))
            return job_id

    def _inherit_piece_marks(self, db, parent_slot, job_id, now):
        """#938: copy the host marks of every Work of the parent slot (its continuations included) to ``job_id``."""
        parents = [row['id'] for row in db.execute('SELECT id FROM jobs WHERE request_key=? OR request_key LIKE ? ORDER BY created',
                                                   (parent_slot, parent_slot + CONTINUATION_SEPARATOR + '%'))]
        for parent in parents:
            for row in db.execute('SELECT detail FROM work_intake_sources WHERE job_id=? ORDER BY id', (parent,)).fetchall():
                try:
                    category = json.loads(row['detail']).get('category') or 'outside-content'
                except (ValueError, AttributeError):
                    category = 'outside-content'
                self.store._insert_intake_source(db, job_id, json.dumps(
                    {'category': category, 'operation': 'line-piece', 'source_work_id': parent},
                    ensure_ascii=False, sort_keys=True), now)

    @staticmethod
    def continue_run(db, work_id, continuation_id, now):
        """The running slot of Work ``work_id`` is now settled from ``continuation_id`` (#774).

        Inside the caller's transaction, when the owner's location answer
        continues a run that asked for it: the slot stays ``running`` and its
        one settlement keeps the continuation's answer, not the asking run's.
        A slot already settled, cancelled or run by another Work is unchanged.
        """
        preparation_id = preparation_of((db.execute('SELECT request_key FROM jobs WHERE id=?', (work_id,)).fetchone()
                                         or {'request_key': None})['request_key'])
        if not preparation_id:
            return False
        changed = db.execute('UPDATE preparations SET last_run_job_id=?,updated_at=?,own_run_revision=CASE WHEN updated_at=own_run_revision THEN ? ELSE NULL END WHERE id=? AND state=? '
                             'AND last_run_job_id=?', (continuation_id, now, now, preparation_id, STATE_RUNNING, work_id)).rowcount
        if changed:
            db.execute('INSERT OR IGNORE INTO line_work_memberships(work_id,line_id,revision) '
                       'SELECT ?,line_id,revision FROM line_work_memberships WHERE work_id=?', (continuation_id,work_id))
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (continuation_id, 'preparation', 'succeeded',
                        json.dumps({'preparation_id': preparation_id, 'continues_work': work_id}), now))
        return bool(changed)

    def settle(self, row, now, scrub=None, decide=None, notify_to=None, waiting=None, close=None):
        """Record one finished run and schedule the next slot, if any.

        A prepared answer is kept as ``scrub(job)`` - the service removes the
        stored secrets (#818: the Work's saved values are the owner's and stay).

        #719: for a ``when_needed`` row, ``decide(row, answer)`` is the
        service's DecisionEngine judgment (``'yes'``, ``'no'`` or None when
        unavailable), asked before the transaction.  The typed decision is
        recorded with the run; a ``notify`` decision queues exactly one
        Telegram notification to ``notify_to`` (``(chat_id, generation)``)
        in the same transaction, so a restart can neither lose nor repeat it.
        """
        if row.get('last_run_job_id') and waiting is not None and waiting(row['last_run_job_id']):
            return None
        answer = finished = None
        if row['kind'] == KIND_PREPARE:
            finished = self.store.job(row['last_run_job_id']) if row.get('last_run_job_id') else None
            if finished and ws.has_delivered_result(finished['status']):
                answer = scrub(finished) if scrub is not None else str(finished.get('response') or '')
        decision = None
        closed_early = False
        if (row.get('delivery_mode') == DELIVERY_WHEN_NEEDED and finished
                and not ws.is_active_preparation(finished['status'])
                and finished['delivery'] not in ('pending', 'sending')):
            promised = window_report_reason(row, now)
            if (close is not None and row.get('recurrence') == RECURRENCE_WINDOW
                    and not row.get('run_return_state') and promised != REASON_WINDOW_CLOSED
                    and finished['status'] == 'succeeded' and str(answer or '').strip()):
                closed_early = close(row, answer) is True
            decision = watch_decision(row, answer, decide, stopped=finished['status'] == 'cancelled',
                                      promised=REASON_WINDOW_CLOSED if closed_early else promised)
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self.store.require_read_checkpoint_writer(db)
            current = db.execute('SELECT * FROM preparations WHERE id=?', (row['id'],)).fetchone()
            if (not current or current['state'] != STATE_RUNNING
                    or current['last_run_job_id'] != row['last_run_job_id']):
                return None
            current = dict(current)
            job = db.execute('SELECT * FROM jobs WHERE id=?', (current['last_run_job_id'],)).fetchone()
            job = dict(job) if job else None
            if job and (ws.is_active_preparation(job['status'])
                        or job['delivery'] in ('pending', 'sending')
                        or waiting is not None and waiting(job['id'])):
                return None
            if finished and job and any(finished.get(key) != job.get(key) for key in ('status', 'response', 'delivery')):
                # A continuation completed while scrubbing/judging outside the
                # transaction. Retry from its actual answer on the next tick.
                return None
            outcome = run_outcome(current['kind'], job)
            updates = {'last_outcome': outcome, 'updated_at': now}
            if outcome == STATE_DELIVERED and job and current.get('delivery_mode') != DELIVERY_WHEN_NEEDED:
                self._clear_carried_budget_skips(db, current['id'], job['id'])
            if current['kind'] == KIND_PREPARE and job and ws.has_delivered_result(job['status']) and answer is not None:
                updates.update(prepared_result_ref=job['id'], prepared_at=now, prepared_text=answer)
            if current['origin'] == ORIGIN_LINE_PLANNER:
                from . import continuous_lines as continuous
                mandate = continuous.current_planner(db, current, now)
                return_state = current['run_return_state'] or STATE_SCHEDULED
                updates.update(state=return_state if mandate else STATE_CANCELLED, run_return_state=None)
                following = None
            else:
                if current.get('check_spec'):
                    change = json.loads(current['self_change'] or '{}')
                    if (change.get('run_key') == slot_key(job['request_key'] if job else None)
                            and change.get('after', {}).get('check_spec')):
                        # A replacement waits a full interval after the current Work returns.
                        updates['check_next_at'] = min(current['until_at'], max(
                            current['check_next_at'], now + current['every_seconds']))
                    following = None if closed_early or now >= current['until_at'] or current['run_count'] >= current['max_runs'] \
                        else updates.get('check_next_at', current['check_next_at'])
                elif current['recurrence'] == RECURRENCE_WINDOW:
                    following = None if closed_early else next_window_slot(current, now)
                else:
                    following = next_due(current['due_at'], current['timezone'], current['recurrence'], now,
                                         current.get('rule_start'), current.get('run_count', 0))
                if following is None:
                    advance = (next_day_window(current, now) if current['recurrence'] == RECURRENCE_WINDOW
                               and not current.get('run_return_state') else {})
                    if advance:
                        following = advance['due_at']
                        updates.update(state=STATE_SCHEDULED, **advance)
                    else:
                        updates['state'] = outcome
                else:
                    updates.update(state=STATE_SCHEDULED, due_at=current['due_at'] if current.get('check_spec') else following)
                if current.get('run_return_state'):
                    # #575: the extra run does not consume or advance the regular schedule.
                    updates.update(state=current['run_return_state'], due_at=current['due_at'], run_return_state=None)
                    following = current['due_at']
            notified = False
            if decision is not None and job:
                updates.update(last_decision=decision[0], last_decision_reason=decision[1])
                if decision == (DECISION_NOTIFY, REASON_PROMISED_REPORT):
                    updates['last_report_at'] = now
                if decision[0] == DECISION_NOTIFY and notify_to:
                    # Queued here; the "last notified" fields advance only once
                    # Telegram confirms the send (``record_notified``).
                    # #213: ``(owner, generation[, surface])``; the surface whose delivery claims the notice.
                    chat_id, generation, *surface = notify_to
                    db.execute('INSERT OR IGNORE INTO telegram_notifications(id,job_id,chat_id,generation,kind,fingerprint,state,message_id,created,surface) VALUES (?,?,?,?,?,?,?,?,?,?)',
                               (str(uuid.uuid4()), job['id'], chat_id, generation, NOTIFY_KIND, text_digest(answer),
                                'queued', None, now, surface[0] if surface else 'telegram'))
                    notified = True
            db.execute(f"UPDATE preparations SET {','.join(k + '=?' for k in updates)} WHERE id=? AND state=?",
                       (*updates.values(), current['id'], STATE_RUNNING))
            self._clear_closed_budget_skips(db)
            if job:
                detail = {'preparation_id': current['id'], 'outcome': outcome, 'delivery': job['delivery'],
                          'next_due': iso(following) if following else None}
                if decision is not None:
                    # #719: the typed decision is the run's Evidence; the answer is not.
                    detail.update(decision=decision[0], reason=decision[1], notification_queued=notified)
                db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                           (job['id'], 'preparation', 'succeeded' if outcome == STATE_DELIVERED else 'failed',
                            json.dumps(detail), now))
            return {**current, **updates}

    def record_notified(self, work_id, digest_, text, now, *, db=None):
        """A ``notify`` message for run ``work_id`` was sent (#719).

        Only a confirmed send becomes what the owner was last told: the
        deduplication digest and the text the next judgment compares with.
        A cancelled or uncertain send changes neither. ``db`` joins the
        caller's transaction so bundle propagation and its marker are atomic.
        """
        if db is None:
            with self.store.db() as db:
                db.execute('BEGIN IMMEDIATE')
                self.store.require_read_checkpoint_writer(db)
                return self.record_notified(work_id, digest_, text, now, db=db)
        job = db.execute('SELECT * FROM jobs WHERE id=?', (work_id,)).fetchone()
        job = dict(job) if job else None
        preparation_id = preparation_of((job or {'request_key': None})['request_key'])
        if not preparation_id:
            return False
        changed = bool(db.execute('UPDATE preparations SET last_notified_digest=?,last_notified_text=?,last_notified_at=? '
                                  'WHERE id=?', (digest_, bounded_text(text), now, preparation_id)).rowcount)
        if changed and run_outcome(KIND_PREPARE, job) == STATE_DELIVERED:
            self._clear_carried_budget_skips(db, preparation_id, work_id)
        return changed

    # -- consumption -------------------------------------------------------

    def fresh(self, now, exclude_job=None, fresh_seconds=FRESH_SECONDS):
        """Prepared answers younger than ``fresh_seconds``, newest first."""
        with self.store.db() as db:
            return [dict(row) for row in db.execute(
                'SELECT p.id, p.goal_text, p.timezone, p.recurrence, p.prepared_at, j.id AS job_id, j.status, '
                'p.prepared_text AS response '
                "FROM preparations p JOIN jobs j ON j.id=p.prepared_result_ref WHERE p.kind='prepare' "
                # #780: the daily plan's own reply is its run record, not an answer prepared for the owner.
                'AND p.origin IS NOT ? '
                # #798: a line's results reach an answer only when the AI judges the line relevant (``read``).
                'AND p.line_id IS NULL '
                'AND p.prepared_at>=? AND j.id IS NOT ? ORDER BY p.prepared_at DESC LIMIT ?',
                (ORIGIN_DAY_PLAN, now - fresh_seconds, exclude_job, SECTION_MAX_ITEMS))]


def render_prepared(rows, now, redact=lambda text: text):
    """The bounded "Prepared for you" section text, or None.

    Every goal and answer passes ``redact`` (the stored-secret pass) first.
    Oldest items are dropped until the text fits ``SECTION_MAX_BYTES``.
    """
    def cut(text, limit):
        return text if len(text.encode()) <= limit else text.encode()[:limit].decode('utf-8', 'ignore') + ' [...]'
    items = []
    for row in rows:
        answer = cut(redact(str(row.get('response') or '')), ANSWER_PREVIEW_BYTES)
        items.append({'ref': 'prep:' + row['id'], 'goal': cut(redact(row['goal_text']), GOAL_PREVIEW_BYTES),
                      'prepared_at': iso(row['prepared_at'], row['timezone']),
                      'age_min': max(0, int((now - row['prepared_at']) // 60)), 'outcome': row['status'],
                      'repeats': row.get('recurrence') or 'no', 'answer': answer})
    while items:
        text = PREPARED_LEGEND + '\n' + json.dumps({'items': items}, ensure_ascii=False, separators=(',', ':'))
        if len(text.encode()) <= SECTION_MAX_BYTES:
            return text
        items.pop()
    return None
