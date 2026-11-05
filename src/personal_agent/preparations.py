"""Owner-accepted preparations: reminders and prepared suggestions (SEC-ATTN-01, #659).

The anticipatory half of the secretary, bounded by
docs/secretary-agency-contract.en.md ("Attention in this program"):

* A **preparation** is one durable ``preparations`` row in the owner's
  QuickStore: a kind, a bounded goal text, a due time (UTC seconds) with the
  owner's time zone, an optional tiny recurrence and a delivery channel.
* It is ``proposed`` until the owner accepts it.  The owner's own request
  for it ("내일 9시에 알려줘") is that acceptance, but only on a
  DecisionEngine judgment (``ConversationJudgments.explicit_preparation_request``),
  never a text rule; otherwise the owner accepts it with an inline Telegram
  button or in Settings.  Only ``scheduled`` rows ever run.
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
import hashlib
import json
import math
import re
from dateutil.rrule import rrulestr
import time
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

OWNER = 'local-owner'
KIND_REMINDER, KIND_PREPARE = 'reminder', 'prepare'
KINDS = (KIND_REMINDER, KIND_PREPARE)
#: The whole recurrence language: deterministic, owner-readable, no cron.
RECURRENCES = ('daily', 'weekdays', 'weekly')
#: #719: "every N minutes from due until a deadline" (the model passes
#: ``every_minutes``/``until``; this value is stored, never offered).
RECURRENCE_WINDOW = 'window'
MIN_EVERY_MINUTES, MAX_EVERY_MINUTES = 5, 12 * 60
#: A watch never spans more than a day and never runs more than this often.
#: #846: a standing wish runs to the owner's deadline (days or weeks); about three checks a day for a month.
MAX_WINDOW_SECONDS = 31 * 86400
MAX_WINDOW_RUNS = 96
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
#: The Telegram notification kind of a ``notify`` decision.
NOTIFY_KIND = 'preparation_notify'
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
SUGGESTION_EXPIRY_SECONDS = 7 * 86400
#: #638: a look back that holds a one-off suggestion (no recurrence) is offered without waiting for the
#: owner's next Work, but only inside these owner-local hours; one whose time passed unanswered lapses.
SUGGESTION_OFFER_HOURS = range(8, 22)
#: ``accepted_by`` values: who turned a proposal into a schedule.
ACCEPTED_OWNER_REQUEST, ACCEPTED_OWNER_BUTTON, ACCEPTED_OWNER_SETTINGS = 'owner-request', 'owner-button', 'owner-settings'
#: #172: the owner's reply or reaction to the proposal message, judged a plain yes to it.
ACCEPTED_OWNER_ANSWER = 'owner-answer'

GOAL_MAX_CHARS = 500
#: #393: a ``prepare`` goal is the run's brief, which the owner no longer reads
#: inline when an offer is given, so it is not squeezed into a reminder's length.
BRIEF_MAX_CHARS = 1000
#: #393: what the owner reads and accepts: a short name and a few plain lines.
TITLE_MAX_CHARS = 60
OFFER_MAX_CHARS = 400
OFFER_MAX_LINES = 6
MAX_ACTIVE = 50
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
                     ('origin', 'TEXT'), ('offered_at', 'REAL'))


def migrate(db):
    """Add the post-#659 columns an older store lacks."""
    present = {row[1] for row in db.execute('PRAGMA table_info(preparations)')}
    for name, kind in COLUMN_MIGRATIONS:
        if name not in present:
            db.execute(f'ALTER TABLE preparations ADD COLUMN {name} {kind}')

REFUSALS = {
    'declined_before': ('소유자가 최근에 같은 준비 제안을 거절했습니다. 다시 제안하지 말고, 지금 요청에 대한 답만 하세요. '
                        '소유자가 직접 요청하면 그때 예약할 수 있습니다.'),
    'invalid_kind': 'kind는 reminder(정해진 때에 이 문장을 알려 주기) 또는 prepare(정해진 때에 이 목표를 미리 처리해 두기)입니다.',
    # #617: the detail (empty, or over the kind's limit) is the whole reason; no contradictory prefix.
    'invalid_goal': '',
    'invalid_offer': (f'title은 {TITLE_MAX_CHARS}자 이내의 짧은 이름, offer는 {OFFER_MAX_LINES}줄·'
                      f'{OFFER_MAX_CHARS}자 이내로 적어 주세요.'),
    'invalid_due': 'due는 RFC3339 시각입니다(예: 2026-09-28T09:00:00+09:00). 오프셋이 없으면 timezone이 필요합니다.',
    'timezone_unknown': '시간대를 알 수 없습니다. due에 오프셋을 붙이거나 timezone(예: Asia/Seoul)을 주세요.',
    'due_in_past': '이 시각은 이미 지났습니다.',
    'due_too_far': '1년 이내의 시각만 예약할 수 있습니다.',
    'invalid_recurrence': 'recurrence는 비우거나 daily, weekdays, weekly 중 하나입니다.',
    'invalid_window': (f'every_minutes는 {MIN_EVERY_MINUTES}~{MAX_EVERY_MINUTES} 사이의 정수이고, until은 due보다 늦고 '
                       f'due부터 {MAX_WINDOW_SECONDS // 86400}일 이내인 RFC3339 시각입니다. recurrence와 함께 쓰지 않습니다.'),
    'window_retry_required': ('같은 목표의 반복 watch 요청이 이 Work에서 이미 있었습니다. 날짜별 단발 준비 여러 건으로 나누지 말고, '
                              'every_minutes와 until을 바로잡아 같은 watch를 다시 호출하세요. 필요한 값이 없으면 대체 일정을 만들지 말고 '
                              '소유자에게 한 가지 질문을 하세요. 이 호출은 새 준비를 만들지 않았습니다.'),
    'invalid_max_runs': f'max_runs는 1~{MAX_WINDOW_RUNS} 사이의 정수입니다.',
    'invalid_delivery': 'delivery when_needed는 kind prepare에만 쓸 수 있습니다.',
    'too_many': f'진행 중인 준비가 {MAX_ACTIVE}개를 넘어 더 만들지 않았습니다. 설정에서 필요 없는 준비를 정리해 주세요.',
    'not_found': '이 준비를 찾지 못했습니다.',
    'not_proposed': '이미 처리된 준비입니다.',
    'expired': '지켜보는 기간이 이미 끝나 시작하지 않았습니다.',
    'running': '지금 실행 중인 준비는 지울 수 없습니다. 먼저 취소하세요.',
    **{code: msg('preparations.' + code) for code in
       ('invalid_rrule', 'already_running', 'invalid_state', 'goal_not_editable', 'invalid_edit', 'resume_expired', 'queue_full')},
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


def local_text(ts, timezone_name):
    """``2026-09-28 09:00 (Mon, Asia/Seoul)`` for a stored UTC time."""
    tz = zone(timezone_name) or dt_timezone.utc
    local = datetime.fromtimestamp(ts, tz)
    return local.strftime('%Y-%m-%d %H:%M') + f" ({local.strftime('%a')}, {timezone_name or 'UTC'})"


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


def normalize_window(every_minutes, until, max_runs, due_at, timezone_name, now):
    """``(every_seconds, window_end, max_runs)`` of one watch (#719), or a typed refusal.

    The window is ``[due_at, until)``: a slot runs strictly before the
    deadline, never at or after it.  The run count is the number of slots
    in it, lowered by ``max_runs`` when given and never above
    ``MAX_WINDOW_RUNS``.  ``until`` is parsed like ``due`` (same zone rules).
    """
    minutes = _whole_number(every_minutes)
    if minutes is None or not MIN_EVERY_MINUTES <= minutes <= MAX_EVERY_MINUTES:
        raise PreparationRefusal('invalid_window')
    try:
        window_end, _zone = parse_due(until, timezone_name, timezone_name, now)
    except PreparationRefusal:
        raise PreparationRefusal('invalid_window') from None
    if window_end <= due_at or window_end - due_at > MAX_WINDOW_SECONDS:
        raise PreparationRefusal('invalid_window')
    every = minutes * 60
    slots = math.ceil((window_end - due_at) / every)
    if max_runs in (None, ''):
        runs = min(slots, MAX_WINDOW_RUNS)
    else:
        runs = _whole_number(max_runs)
        if runs is None or not 1 <= runs <= MAX_WINDOW_RUNS:
            raise PreparationRefusal('invalid_max_runs')
        runs = min(runs, slots)
    return every, window_end, runs


def next_window_slot(row, now):
    """The next slot of a watch strictly after ``now``, or None (#719).

    None once ``max_runs`` Works have started or the next slot is not
    before ``window_end``.  Slots missed while Still was down are skipped, as for
    the other recurrences.
    """
    every, end = row.get('every_seconds'), row.get('window_end')
    if not every or end is None or int(row.get('run_count') or 0) >= int(row.get('max_runs') or 0):
        return None
    slot = row['due_at'] + every * (int(max(0.0, now - row['due_at']) // every) + 1)
    return slot if slot < end else None


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
        slot = datetime(day.year, day.month, day.day, first.hour, first.minute, first.second, tzinfo=tz).timestamp()
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


def watch_decision(row, answer, decide=None, stopped=False):
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
    """#395: a watch whose window has no slot left at ``now``."""
    return row.get('recurrence') == RECURRENCE_WINDOW and row.get('window_end') is not None and row['window_end'] <= now


def reminder_text(goal, due_at, timezone_name, now):
    text = '알림: ' + goal
    if now - due_at > LATE_NOTE_SECONDS:
        text += f'\n(예정 시각 {local_text(due_at, timezone_name)}보다 늦게 전달했습니다.)'
    return text


WEEKDAYS = '월화수목금토일'


def _local_parts(ts, timezone_name):
    local = datetime.fromtimestamp(ts, zone(timezone_name) or dt_timezone.utc)
    return f'{local.month}월 {local.day}일({WEEKDAYS[local.weekday()]})', local.strftime('%H:%M'), local.date()


def _minutes_text(minutes):
    hours, rest = divmod(int(minutes), 60)
    if not hours:
        return f'{rest}분'
    return f'{hours}시간' + (f' {rest}분' if rest else '')


def last_slot(row):
    """#404: a watch's last scheduled slot (``max_runs`` never exceeds the slots before its deadline).

    What the model is told to plan by; a late start or a skipped slot can
    still run until the deadline, which is what the owner is shown (#399).
    """
    return row['due_at'] + int(row['every_seconds']) * (max(1, int(row['max_runs'])) - 1)


def rule_text(recurrence, lang='ko'):
    """Plain owner words for the supported rule parts (#580)."""
    parts = rule_parts(recurrence)
    interval = int(parts.get('INTERVAL', 1))
    if interval == 1:
        text = msg('preparations.rule_every_' + {'DAILY':'day', 'WEEKLY':'week', 'MONTHLY':'month'}[parts['FREQ']], lang)
    elif interval == 2 and parts['FREQ'] == 'WEEKLY':
        text = msg('preparations.rule_every_two_weeks', lang)
    else:
        text = msg('preparations.rule_' + parts['FREQ'].lower(), lang, interval=interval)
    if 'BYDAY' in parts:
        days = []
        for day in parts['BYDAY'].split(','):
            name = msg('preparations.day_' + day[-2:], lang)
            ordinal = day[:-2]
            days.append(msg('preparations.rule_ordinal', lang, ordinal=ordinal, day=name) if ordinal else name)
        text += ' ' + '·'.join(days)
    if 'BYMONTHDAY' in parts:
        text += ' ' + '·'.join(msg('preparations.rule_last_day', lang) if day == '-1' else
                               msg('preparations.rule_monthday', lang, day=day) for day in parts['BYMONTHDAY'].split(','))
    if 'UNTIL' in parts:
        until = parts['UNTIL']
        text += ' · ' + msg('preparations.rule_until', lang, until=until[:4] + '-' + until[4:6] + '-' + until[6:8] +
                    ((' ' + until[9:11] + ':' + until[11:13] + (' UTC' if until.endswith('Z') else '')) if len(until) > 8 else ''))
    if 'COUNT' in parts:
        text += ' · ' + msg('preparations.rule_count', lang, count=parts['COUNT'])
    return text


def schedule_text(row, home_zone=None):
    """#393: one owner-readable line - when it runs and how the owner hears of it.

    A date in the owner's words (``10월 28일(수) 18:35``), the end time alone
    when it is the same day and the zone only when it is not ``home_zone``.
    A watch is stated as it is enforced (``start``/``next_window_slot``): runs
    only *before* its deadline (the window ``[due, until)`` excludes it) and
    at most ``max_runs`` times, so a late start or a skipped slot can never
    run past what the owner read.  When the minute before the deadline is
    itself a slot (an owner's "11시 반까지" set as ``until`` 11:31, #412),
    that minute is named inclusively ("11:30까지"): no run can start after
    it, so the minute-level statement stays exact.
    """
    day, at, date = _local_parts(row['due_at'], row['timezone'])
    zone_note = '' if home_zone and row['timezone'] == home_zone else f" ({row['timezone']})"
    recurrence = row.get('recurrence') or ''
    if recurrence.startswith('RRULE:'):
        when = msg('preparations.rule_schedule', start=f'{day} {at}{zone_note}', rule=rule_text(recurrence))
    elif recurrence == RECURRENCE_WINDOW:
        runs = max(1, int(row['max_runs']))
        every, deadline = int(row['every_seconds']), row['window_end']
        last_minute = deadline - 60
        inclusive = (deadline % 60 == 0 and last_minute >= row['due_at']
                     and (last_minute - row['due_at']) % every == 0)
        end_day, end_at, end_date = _local_parts(last_minute if inclusive else deadline, row['timezone'])
        end = (end_at if end_date == date else f'{end_day} {end_at}') + ('까지' if inclusive else ' 전까지')
        if runs == 1:
            when = f'{day} {at}부터 {end}{zone_note} 한 번'
        else:
            when = f"{day} {at}부터 {end}{zone_note}, {_minutes_text(every // 60)}마다 최대 {runs}번"
    else:
        when = f'{day} {at}{zone_note}' + {'daily': '부터 매일', 'weekdays': '부터 평일마다', 'weekly': '부터 매주'}.get(recurrence, '')
    if row['kind'] == KIND_REMINDER:
        return when
    if row.get('delivery_mode') == DELIVERY_WHEN_NEEDED:
        tell = msg('preparations.delivery_when_needed')
    elif row.get('channel') == CHANNEL_WEB:
        tell = msg('preparations.delivery_keep')
    else:
        tell = msg('preparations.delivery_send')
    return f'{when} · {tell}'


def offered(row):
    """#393: whether the AI wrote an owner-facing offer for this row."""
    return bool(row.get('title_text') or row.get('offer_text'))


def owner_title(row, limit=120):
    """#393: the short name the owner knows this preparation by: its title, else its goal cut short."""
    title = row.get('title_text')
    if title:
        return title
    goal = str(row.get('goal_text') or '')
    return goal[:limit] + ('…' if len(goal) > limit else '')


def accepted_offer(row):
    """#393: the title and offer the owner accepted, without the schedule line.

    For a run's context and the watch judgment: a row's ``due_at`` advances
    to its next slot, so a schedule line rendered later would not be the one
    the owner read; the brief carries the run's own times.
    """
    return '\n'.join(part for part in (row.get('title_text'), row.get('offer_text')) if part)


def proposal_summary(row, home_zone=None, brief=True):
    """What the owner reads for one proposal (Telegram, web, judgment context).

    #393: with an offer, its title, the schedule line and the offer come
    first; the run's brief follows under its own label (``brief`` False
    leaves it out).  Without one, one line as before.
    """
    if not offered(row):
        kind = '알림' if row['kind'] == KIND_REMINDER else '미리 준비'
        return f"[{kind}] {schedule_text(row, home_zone)} — {row['goal_text']}"
    lines = [row.get('title_text') or owner_title(row), schedule_text(row, home_zone)]
    if row.get('offer_text'):
        lines.append(row['offer_text'])
    if brief:
        lines.append(f"{BRIEF_LABEL}: {row['goal_text']}")
    return '\n'.join(lines)


def proposal_html(shown, remaining, home_zone=None):
    """#393: the Telegram (HTML) proposal: the offer first, each run's brief folded."""
    from html import escape
    single = len(shown) == 1
    parts = [] if single else [escape(PROPOSAL_HEADER)]
    for row in shown:
        if not offered(row):
            parts.append(escape(proposal_summary(row, home_zone)))
            continue
        block = [f'<b>{escape(row.get("title_text") or owner_title(row))}</b>', escape(schedule_text(row, home_zone))]
        if row.get('offer_text'):
            block.append(escape(row['offer_text']))
        block.append(f'<blockquote expandable>{escape(BRIEF_LABEL)}\n{escape(row["goal_text"])}</blockquote>')
        parts.append('\n'.join(block))
    if remaining:
        parts.append(escape(MORE_TEXT.format(remaining=remaining)))
    if single and not shown[0].get('offer_text'):
        parts.append(escape(PROPOSAL_QUESTION))
    return '\n\n'.join(parts)


PROPOSAL_HEADER = msg('preparations.shall_i_schedule_the_following_preparation')
#: A single proposal without AI offer prose uses the catalog question.
PROPOSAL_QUESTION = msg('preparations.proposal_question')
#: #393: the label of the run's brief under an offer.
BRIEF_LABEL = msg('preparations.brief_label')
MORE_TEXT = '그 밖의 제안 {remaining}개는 이 메시지로 수락되지 않습니다. "예약 보여줘"처럼 대화로 확인하고 수락할 수 있어요.'
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
    text = ('\n\n' if any(offered(row) for row in shown) else '\n').join(parts)
    if remaining:
        text += '\n' + MORE_TEXT.format(remaining=remaining)
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
                                 for row in rows), ensure_ascii=False)
    return hashlib.sha256(material.encode()).hexdigest()[:32]


# --- the owner's preparations -------------------------------------------------

class Preparations:
    """The ``preparations`` rows of one owner store.  No model, no network."""

    def __init__(self, store, clock=time.time):
        self.store, self.clock = store, clock

    def get(self, preparation_id):
        with self.store.db() as db:
            row = db.execute('SELECT * FROM preparations WHERE id=?', (str(preparation_id or ''),)).fetchone()
            return dict(row) if row else None

    def create(self, *, kind, goal, due_at, timezone, recurrence, channel, created_from, state,
               accepted_by=None, owner=OWNER, window=None, delivery_mode=None, title=None, offer=None, origin=None):
        """One new row (idempotent for the same Work, kind, goal and slot).

        #574: when another Work already proposed the same kind, goal, due and
        recurrence (and window) and the owner has not answered it yet, that row
        is returned with ``already_proposed`` instead of a duplicate.

        ``window`` is ``(every_seconds, window_end, max_runs)`` from
        ``normalize_window`` (#719); it replaces ``recurrence``.  ``origin``
        ``ORIGIN_DREAM`` marks a look-back suggestion (#583); it only ever starts proposed.
        """
        if kind not in KINDS:
            raise PreparationRefusal('invalid_kind')
        if delivery_mode not in (None, DELIVERY_ALWAYS, DELIVERY_WHEN_NEEDED):
            raise ValueError('unknown delivery mode')
        if delivery_mode == DELIVERY_WHEN_NEEDED and kind != KIND_PREPARE:
            raise PreparationRefusal('invalid_delivery')
        recurrence = normalize_recurrence(recurrence)
        every_seconds = window_end = max_runs = None
        if window is not None:
            if recurrence:
                raise PreparationRefusal('invalid_window')
            every_seconds, window_end, max_runs = window
            recurrence = RECURRENCE_WINDOW
        if state not in (STATE_PROPOSED, STATE_SCHEDULED):
            raise ValueError('a preparation starts proposed or scheduled')
        if origin not in (None, ORIGIN_DREAM) or (origin and state != STATE_PROPOSED):
            raise ValueError('a suggestion starts proposed')
        now = self.clock()
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            same = db.execute('SELECT * FROM preparations WHERE created_from=? AND kind=? AND goal_text=? AND due_at=? '
                              'AND state IN (?,?,?,?)', (created_from, kind, goal, due_at, *ACTIVE_STATES)).fetchone()
            if same:
                return dict(same)
            # #574: another Work's identical proposal still waiting for the owner is that same proposal,
            # not a second one.  Scheduled rows are left alone: the owner may want two.
            proposed = db.execute('SELECT * FROM preparations WHERE state=? AND created_from IS NOT ? AND kind=? AND goal_text=? '
                                  'AND due_at=? AND recurrence IS ? AND every_seconds IS ? AND window_end IS ? AND max_runs IS ? '
                                  'ORDER BY created_at LIMIT 1',
                                  (STATE_PROPOSED, created_from, kind, goal, due_at, recurrence,
                                   every_seconds, window_end, max_runs)).fetchone()
            if proposed:
                return {**dict(proposed), 'already_proposed': True}
            active = db.execute('SELECT count(*) FROM preparations WHERE state IN (?,?,?,?)', ACTIVE_STATES).fetchone()[0]
            if active >= MAX_ACTIVE:
                raise PreparationRefusal('too_many')
            row = {'id': uuid.uuid4().hex[:16], 'owner': owner, 'kind': kind, 'goal_text': goal, 'due_at': due_at,
                   'timezone': timezone, 'recurrence': recurrence, 'channel': channel, 'state': state,
                   'last_run_job_id': None, 'last_outcome': None, 'prepared_result_ref': None, 'prepared_at': None, 'prepared_text': None,
                   'created_from': created_from, 'accepted_by': accepted_by, 'created_at': now, 'updated_at': now,
                   'every_seconds': every_seconds, 'window_end': window_end, 'max_runs': max_runs, 'run_count': 0,
                   'rule_start': due_at if recurrence and recurrence.startswith('RRULE:') else None,
                   'delivery_mode': delivery_mode, 'last_decision': None, 'last_decision_reason': None,
                   'last_notified_digest': None, 'last_notified_text': None, 'last_notified_at': None,
                   'title_text': title, 'offer_text': offer, 'origin': origin, 'offered_at': None}
            db.execute(f"INSERT INTO preparations({','.join(row)}) VALUES ({','.join('?' * len(row))})", tuple(row.values()))
            return row

    def accept(self, preparation_id, accepted_by):
        """``proposed`` → ``scheduled``: the owner's explicit yes.

        #395: a watch whose window has already ended is refused (``expired``)
        in the same UPDATE, so a late yes never reads as a running watch.
        """
        now = self.clock()
        with self.store.db() as db:
            changed = db.execute("UPDATE preparations SET state='scheduled',accepted_by=?,updated_at=? WHERE id=? AND state='proposed' "
                                 'AND (recurrence IS NOT ? OR window_end>?)',
                                 (accepted_by, now, preparation_id, RECURRENCE_WINDOW, now)).rowcount
        if not changed:
            row = self.get(preparation_id)
            if not row:
                raise PreparationRefusal('not_found')
            raise PreparationRefusal('expired' if row['state'] == STATE_PROPOSED and window_ended(row, now) else 'not_proposed')
        return self.get(preparation_id)

    def cancel(self, preparation_id):
        """Stop every future run.  A Work already running finishes as its own Work."""
        now = self.clock()
        with self.store.db() as db:
            changed = db.execute('UPDATE preparations SET state=?,updated_at=? WHERE id=? AND state IN (?,?,?,?)',
                                 (STATE_CANCELLED, now, preparation_id, *ACTIVE_STATES)).rowcount
        row = self.get(preparation_id)
        if row is None:
            raise PreparationRefusal('not_found')
        return {**row, 'changed': bool(changed)}

    def pause(self, preparation_id):
        """Keep a schedule without allowing the tick to run it (#575)."""
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self._editable(db, preparation_id, (STATE_SCHEDULED, STATE_PAUSED))
            db.execute('UPDATE preparations SET state=?,updated_at=? WHERE id=?',
                       (STATE_PAUSED, self.clock(), row['id']))
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
        """Resume at a future slot, never catch up missed slots (#575)."""
        now = self.clock()
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self._editable(db, preparation_id, (STATE_PAUSED,))
            following = row['due_at']
            if row['recurrence'] == RECURRENCE_WINDOW:
                if window_ended(row, now) or row['run_count'] >= row['max_runs']:
                    raise PreparationRefusal('resume_expired')
                if following <= now:
                    following = next_window_slot(row, now)
            elif following <= now:
                following = next_due(following, row['timezone'], row['recurrence'], now, row.get('rule_start'), row.get('run_count', 0))
            if following is None or following <= now:
                raise PreparationRefusal('resume_expired')
            db.execute('UPDATE preparations SET state=?,due_at=?,updated_at=? WHERE id=?',
                       (STATE_SCHEDULED, following, now, row['id']))
        return self.get(preparation_id)

    def edit(self, preparation_id, **changes):
        """Validate schedule changes without accepting a proposal or changing its goal (#575)."""
        now = self.clock()
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self._editable(db, preparation_id, (STATE_PROPOSED, STATE_SCHEDULED, STATE_PAUSED))
            updates = self.edit_values(row, changes, now)
            updates['updated_at'] = now
            db.execute(f"UPDATE preparations SET {','.join(k + '=?' for k in updates)} WHERE id=?",
                       (*updates.values(), preparation_id))
        return self.get(preparation_id)

    @staticmethod
    def edit_values(row, changes, now):
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
            if 'recurrence' in changes:
                raise PreparationRefusal('invalid_window')
            every, end, runs = normalize_window(changes.get('every_minutes', row['every_seconds'] // 60),
                                                changes.get('until', datetime.fromtimestamp(row['window_end'], zone(timezone_name)).isoformat()),
                                                changes.get('max_runs', row['max_runs']), due_at, timezone_name, now)
            updates.update(every_seconds=every, window_end=end, max_runs=runs)
        else:
            if set(changes) & {'every_minutes', 'until', 'max_runs'}:
                raise PreparationRefusal('invalid_window')
            updates['recurrence'] = (normalize_rrule(changes['rrule']) if 'rrule' in changes else
                                     normalize_recurrence(changes.get('recurrence', row['recurrence'])))
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
            row = db.execute('SELECT state FROM preparations WHERE id=?', (preparation_id,)).fetchone()
            if row is None:
                raise PreparationRefusal('not_found')
            if row['state'] == STATE_RUNNING:
                raise PreparationRefusal('running')
            db.execute('DELETE FROM preparations WHERE id=?', (preparation_id,))
        return {'deleted': True, 'id': preparation_id}

    def declined_goals(self, now, limit=DECLINED_GOALS_LIMIT):
        """Goals of proposals the owner declined or let lapse in the last ``DECLINED_SECONDS`` (#582).

        A declined proposal is a row cancelled without ever being accepted; newest first.
        """
        with self.store.db() as db:
            return [row['goal_text'] for row in db.execute(
                "SELECT goal_text FROM preparations WHERE state=? AND accepted_by IS NULL AND updated_at>=? "
                'ORDER BY updated_at DESC LIMIT ?', (STATE_CANCELLED, now - DECLINED_SECONDS, int(limit)))]

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

    # -- look-back suggestions (#583) ----------------------------------------

    def last_suggestion_offered(self):
        """When the last look-back suggestion message was queued, or None."""
        with self.store.db() as db:
            row = db.execute('SELECT MAX(offered_at) FROM preparations WHERE origin=?', (ORIGIN_DREAM,)).fetchone()
        return float(row[0]) if row and row[0] is not None else None

    def has_unoffered_one_off(self):
        """Whether a look back holds a one-off suggestion not offered yet (#638; one read)."""
        with self.store.db() as db:
            return db.execute('SELECT 1 FROM preparations WHERE state=? AND origin=? AND offered_at IS NULL '
                              'AND recurrence IS NULL LIMIT 1', (STATE_PROPOSED, ORIGIN_DREAM)).fetchone() is not None

    def unoffered_suggestions(self, now, one_off=False):
        """The oldest look-back's suggestions not offered yet, ``(work_id, rows)``, or ``(None, [])``.

        A first slot that has passed meanwhile moves to the recurrence's next slot,
        so the message shows the schedule an acceptance would keep; a one-off whose
        time passed lapses (#638).  ``one_off`` takes only a look back that holds one.
        """
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
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
        return anchor[0], self.proposed_from(anchor[0])

    def mark_offered(self, rows, now):
        """The rows one suggestion message offered: never offered again."""
        with self.store.db() as db:
            for row in rows:
                db.execute('UPDATE preparations SET offered_at=? WHERE id=? AND offered_at IS NULL', (now, row['id']))

    #: #638: an unanswered one-off suggestion whose time came (params: state, origin, now).
    _LAPSED_ONE_OFF = 'state=? AND origin=? AND recurrence IS NULL AND due_at<=?'

    def expire_suggestions(self, now):
        """Look-back suggestions still unanswered after ``SUGGESTION_EXPIRY_SECONDS``, or one-offs whose time
        came (#638), lapse; returns how many.

        ``expired``, not ``cancelled``: the owner never said no, so ``declined_goals`` does not count it.
        Nothing to expire is one read.
        """
        cutoff = now - SUGGESTION_EXPIRY_SECONDS
        where = 'state=? AND origin=? AND (created_at<=? OR (recurrence IS NULL AND due_at<=?))'
        params = (STATE_PROPOSED, ORIGIN_DREAM, cutoff, now)
        with self.store.db() as db:
            if db.execute(f'SELECT 1 FROM preparations WHERE {where} LIMIT 1', params).fetchone() is None:
                return 0
            return db.execute(f'UPDATE preparations SET state=?,updated_at=? WHERE {where}',
                              (STATE_EXPIRED, now, *params)).rowcount

    def rows(self, limit=100):
        """Every row with its last run's Work status and delivery, active first."""
        with self.store.db() as db:
            return [dict(row) for row in db.execute(
                'SELECT p.*, j.status AS last_status, j.delivery AS last_delivery, j.response AS last_response '
                'FROM preparations p LEFT JOIN jobs j ON j.id=p.last_run_job_id '
                "ORDER BY CASE p.state WHEN 'proposed' THEN 0 WHEN 'running' THEN 1 WHEN 'scheduled' THEN 2 WHEN 'paused' THEN 3 ELSE 4 END, "
                'p.due_at LIMIT ?', (int(limit),))]

    # -- the tick ----------------------------------------------------------

    def due(self, now):
        """The tick's one indexed query: due slots and runs ready to settle.

        A ``scheduled`` row is due at ``due_at``; a ``running`` row is ready
        once its Work is no longer queued, running or awaiting a connection
        and its delivery is no longer pending or in flight. Expired windows
        also surface so an unanswered prerequisite can terminate without AI.
        Immediate runs settle even before the regular due time (#575).
        """
        with self.store.db() as db:
            return [dict(row) for row in db.execute(
                'SELECT p.*, j.status AS job_status, j.delivery AS job_delivery FROM preparations p '
                'LEFT JOIN jobs j ON j.id=p.last_run_job_id '
                "WHERE ((p.state='scheduled' AND p.due_at<=?) OR p.state='running') "
                "AND (p.state='scheduled' OR j.id IS NULL OR (p.recurrence='window' AND p.window_end<=?) "
                f"OR (j.status NOT IN {ws.sql_in(ws.ACTIVE_PREPARATION)} "
                "AND j.delivery NOT IN ('pending','sending'))) ORDER BY p.due_at LIMIT ?", (now, now, TICK_LIMIT))]

    @staticmethod
    def run_refusal(db, work_id, now):
        """Recheck the accepted run in the caller's queue/admission transaction."""
        work = db.execute('SELECT request_key FROM jobs WHERE id=?', (work_id,)).fetchone()
        preparation_id = preparation_of(work['request_key'] if work else None)
        if not preparation_id:
            return None
        row = db.execute('SELECT * FROM preparations WHERE id=?', (preparation_id,)).fetchone()
        if row and row['recurrence'] == RECURRENCE_WINDOW and now >= row['window_end']:
            return 'preparation-window-expired'
        if not row or row['state'] != STATE_RUNNING or row['last_run_job_id'] != work_id:
            return 'preparation-inactive'
        # The current run already consumed its count; equality is allowed.
        if row['recurrence'] == RECURRENCE_WINDOW and row['run_count'] > row['max_runs']:
            return 'preparation-run-limit'
        return None

    @staticmethod
    def refuse_run(db, work_id, now, reason):
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
        db.execute('UPDATE preparations SET state=?,last_outcome=?,updated_at=? '
                   'WHERE last_run_job_id=? AND state=?', (STATE_EXPIRED, STATE_FAILED, now, work_id, STATE_RUNNING))
        db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                   (work_id, 'preparation', 'withheld', json.dumps({'reason': reason, 'resumed': False}), now))
        return True

    def start(self, row, *, channel, chat_id, now, immediate=False):
        """Claim one due slot and create its Work in ONE transaction.

        Returns the Work id, or None when the slot is no longer due (another
        claim, a cancel) or the queue refused it (the row stays scheduled and
        is tried again on a later tick).  The Work's request key names the
        slot, so a replayed claim finds the same Work instead of a second.
        """
        slot = now if immediate else row['due_at']
        key = request_key(row['id'], slot)
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT * FROM preparations WHERE id=?', (row['id'],)).fetchone()
            if immediate:
                current = self._editable(db, row['id'], (STATE_SCHEDULED, STATE_PAUSED))
            elif not current or current['state'] != STATE_SCHEDULED or current['due_at'] != row['due_at']:
                return None
            current = dict(current)
            existing = db.execute('SELECT id FROM jobs WHERE request_key=?', (key,)).fetchone()
            if immediate and existing:
                return existing['id']
            window = current['recurrence'] == RECURRENCE_WINDOW
            if window and not existing and (now >= current['window_end']
                                            or int(current['run_count'] or 0) >= int(current['max_runs'] or 0)):
                if immediate:
                    raise PreparationRefusal('resume_expired')
                # #719: a watch never runs after its deadline or beyond its bound.
                db.execute('UPDATE preparations SET state=?,updated_at=? WHERE id=? AND state=?',
                           (STATE_EXPIRED, now, current['id'], STATE_SCHEDULED))
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
                    return None
            # #580: an extra immediate run does not consume a rule's scheduled count.
            extra_rule = immediate and str(current['recurrence'] or '').startswith('RRULE:')
            run = int(current['run_count'] or 0) + (0 if existing or extra_rule else 1)
            db.execute('UPDATE preparations SET state=?,last_run_job_id=?,run_count=?,updated_at=?,run_return_state=? WHERE id=?',
                       (STATE_RUNNING, job_id, run, now, current['state'] if immediate else None, current['id']))
            # Evidence: preparation id -> Work id; the Work's delivery is the receipt.
            detail = {'preparation_id': current['id'], 'kind': current['kind'], 'slot': iso(slot),
                      'recurrence': current['recurrence'], 'accepted_by': current['accepted_by'],
                      'channel': channel.split(':', 1)[0]}
            if window:
                detail.update(run=run, max_runs=current['max_runs'], until=iso(current['window_end']))
            if current.get('delivery_mode') == DELIVERY_WHEN_NEEDED:
                detail['delivery_mode'] = DELIVERY_WHEN_NEEDED
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (job_id, 'preparation', 'succeeded', json.dumps(detail), now))
            return job_id

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
        changed = db.execute('UPDATE preparations SET last_run_job_id=?,updated_at=? WHERE id=? AND state=? '
                             'AND last_run_job_id=?', (continuation_id, now, preparation_id, STATE_RUNNING, work_id)).rowcount
        if changed:
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (continuation_id, 'preparation', 'succeeded',
                        json.dumps({'preparation_id': preparation_id, 'continues_work': work_id}), now))
        return bool(changed)

    def settle(self, row, now, scrub=None, decide=None, notify_to=None, waiting=None):
        """Record one finished run and schedule the next slot, if any.

        A prepared answer is kept as ``scrub(job)`` - the service removes the
        Work's saved private values and stored secrets - never raw.

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
        if (row.get('delivery_mode') == DELIVERY_WHEN_NEEDED and finished
                and not ws.is_active_preparation(finished['status'])
                and finished['delivery'] not in ('pending', 'sending')):
            decision = watch_decision(row, answer, decide, stopped=finished['status'] == 'cancelled')
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
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
            if current['kind'] == KIND_PREPARE and job and ws.has_delivered_result(job['status']) and answer is not None:
                updates.update(prepared_result_ref=job['id'], prepared_at=now, prepared_text=answer)
            if current['recurrence'] == RECURRENCE_WINDOW:
                following = next_window_slot(current, now)
            else:
                following = next_due(current['due_at'], current['timezone'], current['recurrence'], now,
                                     current.get('rule_start'), current.get('run_count', 0))
            if following is None:
                updates['state'] = outcome
            else:
                updates.update(state=STATE_SCHEDULED, due_at=following)
            if current.get('run_return_state'):
                # #575: the extra run does not consume or advance the regular schedule.
                updates.update(state=current['run_return_state'], due_at=current['due_at'], run_return_state=None)
                following = current['due_at']
            notified = False
            if decision is not None and job:
                updates.update(last_decision=decision[0], last_decision_reason=decision[1])
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

    def record_notified(self, work_id, digest_, text, now):
        """A ``notify`` message for run ``work_id`` was sent (#719).

        Only a confirmed send becomes what the owner was last told: the
        deduplication digest and the text the next judgment compares with.
        A cancelled or uncertain send changes neither.
        """
        job = self.store.job(work_id)
        preparation_id = preparation_of((job or {}).get('request_key'))
        if not preparation_id:
            return False
        with self.store.db() as db:
            return bool(db.execute('UPDATE preparations SET last_notified_digest=?,last_notified_text=?,last_notified_at=? '
                                   'WHERE id=?', (digest_, bounded_text(text), now, preparation_id)).rowcount)

    # -- consumption -------------------------------------------------------

    def fresh(self, now, exclude_job=None, fresh_seconds=FRESH_SECONDS):
        """Prepared answers younger than ``fresh_seconds``, newest first."""
        with self.store.db() as db:
            return [dict(row) for row in db.execute(
                'SELECT p.id, p.goal_text, p.timezone, p.recurrence, p.prepared_at, j.id AS job_id, j.status, '
                'p.prepared_text AS response '
                "FROM preparations p JOIN jobs j ON j.id=p.prepared_result_ref WHERE p.kind='prepare' "
                'AND p.prepared_at>=? AND j.id IS NOT ? ORDER BY p.prepared_at DESC LIMIT ?',
                (now - fresh_seconds, exclude_job, SECTION_MAX_ITEMS))]


def render_prepared(rows, now, redact=lambda text: text):
    """The bounded "Prepared for you" section text, or None.

    Every goal and answer passes ``redact`` (the stored-secret pass) first.
    Oldest items are dropped until the text fits ``SECTION_MAX_BYTES``.
    """
    items = []
    for row in rows:
        answer = redact(str(row.get('response') or ''))
        if len(answer.encode()) > ANSWER_PREVIEW_BYTES:
            answer = answer.encode()[:ANSWER_PREVIEW_BYTES].decode('utf-8', 'ignore') + ' [...]'
        items.append({'ref': 'prep:' + row['id'], 'goal': redact(row['goal_text']),
                      'prepared_at': iso(row['prepared_at'], row['timezone']),
                      'age_min': max(0, int((now - row['prepared_at']) // 60)), 'outcome': row['status'],
                      'repeats': row.get('recurrence') or 'no', 'answer': answer})
    while items:
        text = PREPARED_LEGEND + '\n' + json.dumps({'items': items}, ensure_ascii=False, separators=(',', ':'))
        if len(text.encode()) <= SECTION_MAX_BYTES:
            return text
        items.pop()
    return None
