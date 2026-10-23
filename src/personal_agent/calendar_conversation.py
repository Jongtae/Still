"""The owner's approval of the calendar drafts a Work made, in the one conversation.

#22 (owner decision 2026-10-19, option A).  The owner's chosen AI interprets a
calendar request through its ordinary tools: ``calendar_query`` to read, and
``calendar_draft_create`` / ``calendar_draft_update`` / ``calendar_draft_cancel``
to propose.  A draft has no external effect.  What this module keeps is the
approval half, unchanged in its authority:

* **Still renders the preview.**  When a Work ends, the drafts it made are
  read back from the connector (``CalendarConnector.preview``) and shown in
  Still's own words: every field that would be sent, with any line break or
  control character in a value shown escaped, so a value cannot add a line to
  the preview.  The model's prose never stands in for the preview.
* **Only the owner's judged approval of that preview executes.**  OWNER-ACT-01
  (#172, owner decision 2026-10-21, revising option A): which open item an
  owner act answers, and how, is one judgment over everything pending
  (``owner_acts``); no word list is read here.  ``approve`` runs only for the
  drafts that judgment named, and only while they are still exactly the
  pending set (compare-and-clear under a lock).  It checks every draft's state
  and payload hash against what was shown before anything is sent, then spends
  ``CalendarConnector.approve`` -> ``execute`` for each, which binds a one-time
  token to this owner, this draft, this payload hash and the write connector's
  revision.
* **Nothing else touches the drafts.**  A message that does not answer them
  leaves them pending.  They end by a judged decline (``decline``), by a newer
  Work's drafts replacing them, or by expiry after ``PENDING_TTL_SECONDS``.
  The other channel's drafts are never collected for this one.

No owner wording is parsed into a date, time or title here.  Durable state is
one ``calendar_conversation`` row per connector identity: the hashed owner,
the offering Work's id, the draft ids and payload hashes shown, and an expiry.
It holds no utterance text and no secret.
"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .calendar import CalendarError
from .connector_contract import _owner_key
from .messages import msg

STATE_KEY = 'calendar_conversation'
PENDING_TTL_SECONDS = 900
STATE_AWAITING_APPROVAL = 'awaiting-approval'
#: The worker's drafting tools whose drafts this module may offer for approval.
DRAFT_TOOLS = ('calendar_draft_create', 'calendar_draft_update', 'calendar_draft_cancel')
#: Characters that would break a preview line or hide inside one.  The preview
#: is rendered with ``white-space: pre-wrap``, where CR, LF, U+2028 and U+2029
#: are forced breaks, so a value carrying them could print a second "일시:"
#: line above the real one.  A value containing any of them is shown escaped.
_UNSAFE = re.compile(r'[\u0000-\u001f\u007f-\u009f\u00ad\u061c\u180e'
                     r'\u200b-\u200f\u2028\u2029\u202a-\u202e\u2060-\u2064'
                     r'\u2066-\u206f\ufeff\ufff9-\ufffb]')
_WEEKDAYS_KO = '월화수목금토일'

# --- owner-facing text -------------------------------------------------------
PREVIEW_HEADER = msg('calendar_conversation.here_is_the_calendar_draft')
PREVIEW_FOOTER = msg('calendar_conversation.reply_approve_to_apply_it_as')
CALENDAR_LINE = msg('calendar_conversation.calendar_google_primary_calendar')
REPLACED_NOTICE = msg('calendar_conversation.previous_draft_cancelled')
CANCELLED = msg('calendar_conversation.the_event_draft_was_canceled_no')
CHANGED_UNDER_US = msg('calendar_conversation.draft_changed')
CREATED = msg('calendar_conversation.the_event_was_added_to_the')
UPDATED = msg('calendar_conversation.the_event_was_changed')
CANCELLED_EVENT = msg('calendar_conversation.the_event_was_cancelled')
OUTCOME_UNKNOWN = msg('calendar_conversation.i_cannot_confirm_whether_the_event')
UPDATE_UNKNOWN = msg('calendar_conversation.i_cannot_confirm_whether_the_change')
CANCEL_UNKNOWN = msg('calendar_conversation.i_cannot_confirm_whether_the_cancellation')
FAILED = msg('calendar_conversation.not_applied')
NOT_APPLIED_REST = msg('calendar_conversation.rest_not_applied')
#: An update or cancellation whose target this Work's own calendar read did not show.
UNVERIFIED_TARGET = msg('calendar_conversation.unverified_target')
#: A failed draft, whole lines, so each is one catalog line in every language.
FAILED_RECONNECT = msg('calendar_conversation.not_applied_reconnect')
FAILED_RETRY = msg('calendar_conversation.not_applied_retry')
ACTION_LABELS = {'create': '새 일정', 'update': '일정 변경', 'cancel': '일정 취소'}
DONE_TEXT = {'create': CREATED, 'update': UPDATED, 'cancel': CANCELLED_EVENT}
#: What an unconfirmed outcome tells the owner to check, by action.
UNKNOWN_TEXT = {'create': OUTCOME_UNKNOWN, 'update': UPDATE_UNKNOWN, 'cancel': CANCEL_UNKNOWN}
#: The evidence tool name of an applied draft, by action.
EFFECT_TOOLS = {'create': 'calendar_create', 'update': 'calendar_update', 'cancel': 'calendar_cancel'}

def shown_value(value):
    """A value exactly as it would be sent, on one line: escaped when it carries a break or control character."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return json.dumps(text, ensure_ascii=False) if _UNSAFE.search(text) else text


def _zone(name):
    """The named IANA zone, or None when there is none to compare an offset against."""
    try:
        return ZoneInfo(name) if isinstance(name, str) and name else None
    except (ValueError, ZoneInfoNotFoundError):
        return None


def _shown_time(moment, zone):
    """A wall-clock time exactly as sent: seconds and fractions when present, the offset unless ``zone`` already says it.

    #233: the value is sent to Google unchanged, so the preview must not round
    ``15:00:59`` to ``15:00`` or drop a ``Z`` / ``+00:00`` offset that places
    the event at another instant than the zone shown beside it.  An offset is
    left out only when the value carries none (Google reads it in the zone) or
    when it is the zone's own offset at that instant.
    """
    text = f"{moment:%H:%M}"
    if moment.second or moment.microsecond:
        text += f":{moment:%S}"
    if moment.microsecond:
        text += '.' + f"{moment.microsecond:06d}".rstrip('0')
    offset = moment.utcoffset()
    if offset is None:
        return text
    if zone is not None and moment.astimezone(zone).replace(tzinfo=None) == moment.replace(tzinfo=None):
        return text
    sign = '-' if offset.days < 0 else '+'
    minutes = abs(int(offset.total_seconds())) // 60
    return f"{text} UTC{sign}{minutes // 60:02d}:{minutes % 60:02d}"


def describe_span(start, end, zone):
    """``2026-09-23 (수) 15:00 – 16:00 (Asia/Seoul)`` from the RFC3339 times as sent, with nothing they say left out."""
    first = datetime.fromisoformat(start)
    last = datetime.fromisoformat(end)
    tz = _zone(zone)
    day = f"{first:%Y-%m-%d} ({_WEEKDAYS_KO[first.weekday()]})"
    if last.date() == first.date():
        return f"{day} {_shown_time(first, tz)} – {_shown_time(last, tz)} ({shown_value(zone)})"
    return (f"{day} {_shown_time(first, tz)} – {last:%Y-%m-%d} ({_WEEKDAYS_KO[last.weekday()]}) "
            f"{_shown_time(last, tz)} ({shown_value(zone)})")


def target_lines(target):
    """The event an update or cancellation acts on, as this Work's own calendar read returned it."""
    lines = [f"대상 일정: {shown_value(target.get('summary') or '(제목 없음)')}"]
    start, end = target.get('start'), target.get('end')
    if isinstance(start, str) and isinstance(end, str):
        try:
            lines.append(f"대상 일시: {describe_span(start, end, '')}".replace(' ()', ''))
        except ValueError:
            lines.append(f"대상 일시: {shown_value(start)} – {shown_value(end)}")
    if target.get('location'):
        lines.append(f"대상 장소: {shown_value(target['location'])}")
    return lines


def preview_lines(preview, target=None):
    """Every field one draft would send, in Still's words.  Nothing is omitted or summarised.

    An update or cancellation also shows its target event as this Work's own
    calendar read returned it (``target``), so the owner can see which event
    the approval changes, not only its id.
    """
    payload = preview.get('payload') or {}
    lines = []
    if preview.get('action') in ('update', 'cancel'):
        if target:
            lines.extend(target_lines(target))
        lines.append(f"대상 일정 ID: {shown_value(preview.get('event_id', ''))}")
    if 'summary' in payload:
        lines.append(f"제목: {shown_value(payload['summary'])}")
    start, end = payload.get('start'), payload.get('end')
    if isinstance(start, str) and isinstance(end, str):
        try:
            lines.append(f"일시: {describe_span(start, end, payload.get('timezone', ''))}")
        except ValueError:
            lines.append(f"시작: {shown_value(start)}")
            lines.append(f"종료: {shown_value(end)}")
    else:
        if start is not None:
            lines.append(f"시작: {shown_value(start)}")
        if end is not None:
            lines.append(f"종료: {shown_value(end)}")
        if 'timezone' in payload:
            lines.append(f"시간대: {shown_value(payload['timezone'])}")
    if 'location' in payload:
        lines.append(f"장소: {shown_value(payload['location'])}")
    if 'description' in payload:
        lines.append(f"설명: {shown_value(payload['description'])}")
    known = {'summary', 'start', 'end', 'timezone', 'location', 'description'}
    for key in sorted(set(payload) - known):
        # The connector validates the payload; anything else it accepted is still shown.
        lines.append(f"{shown_value(key)}: {shown_value(payload[key])}")
    return lines


def work_draft_ids(events):
    """The calendar draft ids a Work's worker made, in order, from its durable tool events.

    ``events`` are ``(tool, detail)`` pairs of terminal tool events (a draft is
    recorded as a withheld step, since its effect waits for the owner).  The
    relayed CLI route records the host action in ``detail``; the direct route
    records the tool's own name.
    """
    ids = []
    for tool, detail in events:
        try:
            data = json.loads(detail or '{}')
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        action = data.get('host_action') or tool
        evidence = data.get('evidence') if isinstance(data.get('evidence'), dict) else {}
        ident = evidence.get('draft_id')
        if action in DRAFT_TOOLS and isinstance(ident, str) and ident and ident not in ids:
            ids.append(ident)
    return ids


class CalendarConversation:
    """The pending approval of one Work's calendar drafts, per connector identity.

    ``connector_for(owner_id)`` returns the owner-bound ``CalendarConnector``
    or None.  It is resolved per turn because the service builds one per
    connector identity.
    """

    def __init__(self, store, connector_for, *, now=time.time, ttl_seconds=PENDING_TTL_SECONDS):
        self.store = store
        self.connector_for = connector_for
        self.now = now
        self.ttl_seconds = ttl_seconds
        #: One answer settles a pending row at most once, whichever thread reads it (a reply or a reaction).
        self._lock = threading.Lock()

    # -- state ----------------------------------------------------------------
    def _rows(self):
        """The per-owner pending map.

        Keyed per connector identity - the web owner and the paired Telegram
        chat - so a message from one identity can neither approve nor destroy
        the drafts the other was shown.
        """
        stored = self.store.config(STATE_KEY, None)
        if not isinstance(stored, dict):
            return {}
        # A pre-upgrade single row is dropped rather than migrated.
        if 'state' in stored:
            return {}
        return {key: row for key, row in stored.items() if isinstance(row, dict)}

    @staticmethod
    def _drafts(row):
        """The drafts a pending row shows; a row written before #22 held one ``draft_id``."""
        drafts = row.get('drafts')
        if isinstance(drafts, list):
            return [item for item in drafts if isinstance(item, dict) and isinstance(item.get('draft_id'), str)]
        if isinstance(row.get('draft_id'), str) and row.get('draft_id'):
            return [{'draft_id': row['draft_id'], 'payload_hash': row.get('payload_hash', '')}]
        return []

    def shown_drafts(self, row):
        """The drafts a pending row showed (what an answer to it must still find)."""
        return self._drafts(row)

    def pending(self, owner_id=None):
        rows = self._rows()
        if owner_id is None:
            key, row = next(((k, r) for k, r in rows.items() if self._live(r)), (None, None))
        else:
            row = rows.get(_owner_key(owner_id))
        # Only a shown preview is pending; a pre-#22 half-collected request is not.
        if row is None or row.get('state') != STATE_AWAITING_APPROVAL or not self._drafts(row):
            return None
        if not self._live(row):
            self.clear(owner_id)
            return None
        return row

    def _live(self, row):
        return isinstance(row, dict) and float(row.get('expires', 0) or 0) > float(self.now())

    def has_pending(self, owner_id=None):
        return self.pending(owner_id) is not None

    def clear(self, owner_id=None):
        rows = self._rows()
        if owner_id is None:
            previous = next((r for r in rows.values() if r.get('state')), None)
            self.store.put(STATE_KEY, {})
            return previous
        previous = rows.pop(_owner_key(owner_id), None)
        self.store.put(STATE_KEY, rows)
        return previous if isinstance(previous, dict) and previous.get('state') else None

    def _save(self, owner_id, drafts, work_id=None):
        row = {'owner': _owner_key(owner_id), 'state': STATE_AWAITING_APPROVAL, 'drafts': drafts,
               'at': float(self.now()), 'expires': float(self.now()) + self.ttl_seconds}
        if isinstance(work_id, str) and work_id:
            # The Work whose reply showed the preview: a reaction or reply-to on it is bound to it (#172).
            row['work_id'] = work_id
        rows = self._rows()
        rows[_owner_key(owner_id)] = row
        self.store.put(STATE_KEY, rows)
        return row

    # -- the Work's drafts --------------------------------------------------------
    def offer(self, owner_id, draft_ids, targets=None, work_id=None):
        """Make a finished Work's drafts the ones this identity may approve; Still's preview text, or ''.

        Each draft is read back from the connector, so the preview shows what
        the connector holds, not what a tool result or the model said.  A draft
        this owner cannot see, or one no longer awaiting approval, is not shown
        and cannot be approved here.  An update or cancellation is offered only
        when ``targets`` - this Work's own calendar reads, by (event id,
        version) - show its target event, so the owner sees which event it
        changes.  Earlier pending drafts are replaced.  ``work_id`` is the
        offering Work, whose reply carries the preview.
        """
        targets = targets or {}
        if not draft_ids:
            return ''
        connector = self.connector_for(owner_id)
        if connector is None:
            return ''
        shown, unverified = [], 0
        for ident in draft_ids:
            try:
                preview = connector.preview(ident, owner_id)
            except ValueError:
                continue
            if preview.get('state') != STATE_AWAITING_APPROVAL:
                continue
            if preview.get('action') in ('update', 'cancel'):
                target = targets.get((preview.get('event_id'), preview.get('event_version')))
                if not target:
                    unverified += 1
                    continue
                preview = {**preview, 'target': target}
            shown.append(preview)
        refused = UNVERIFIED_TARGET.format(count=unverified) if unverified else ''
        if not shown:
            return refused
        notice = REPLACED_NOTICE + '\n\n' if self.pending(owner_id) else ''
        self._save(owner_id, [{'draft_id': preview['id'], 'payload_hash': preview['payload_hash'],
                               'action': preview.get('action')} for preview in shown], work_id)
        return notice + self._preview_text(shown) + ('\n\n' + refused if refused else '')

    @staticmethod
    def _preview_text(previews):
        lines = [PREVIEW_HEADER]
        for index, preview in enumerate(previews, 1):
            label = ACTION_LABELS.get(preview.get('action'), shown_value(preview.get('action')))
            lines.append(f"{index}. {label}" if len(previews) > 1 else label)
            lines.extend(preview_lines(preview, preview.get('target')))
        lines.append(CALENDAR_LINE)
        lines.append(PREVIEW_FOOTER)
        return '\n'.join(lines)

    # -- the owner's answer ------------------------------------------------------
    def shown_text(self, owner_id, row):
        """The preview of a pending row as the connector holds it now, in Still's words, or ''.

        Read for the owner-act judgment (#172) only; nothing here approves.
        """
        connector = self.connector_for(owner_id)
        if connector is None:
            return ''
        previews = []
        for shown in self._drafts(row):
            try:
                previews.append(connector.preview(shown['draft_id'], owner_id))
            except (ValueError, CalendarError):
                return ''
        return self._preview_text(previews) if previews else ''

    def _take(self, owner_id, drafts):
        """The pending row when it still shows exactly ``drafts``, cleared in the same step; else None.

        Compare-and-clear (#172): of two answers to the same preview only the
        first gets the row, and a row replaced since the answer was judged is
        not the one the owner answered.
        """
        with self._lock:
            row = self.pending(owner_id)
            if row is None or row.get('owner') != _owner_key(owner_id) or self._drafts(row) != drafts:
                return None
            self.clear(owner_id)
            return row

    def approve(self, owner_id, drafts, *, evidence=None):
        """Execute exactly the pending ``drafts`` the owner's judged approval named; the reply, or None.

        None when they are no longer the pending set (expired, replaced or
        already answered): nothing is sent.
        """
        row = self._take(owner_id, drafts)
        return None if row is None else self._approve(owner_id, row, evidence)

    def decline(self, owner_id, drafts):
        """Drop exactly the pending ``drafts`` the owner's judged decline named; the reply, or None."""
        return None if self._take(owner_id, drafts) is None else CANCELLED

    def _approve(self, owner_id, row, evidence):
        # The pending record was dropped before anything below (``_take``), so a
        # second answer finds nothing; the connector's own approved/executing/
        # completed states are the further barriers.
        connector = self.connector_for(owner_id)
        if connector is None:
            raise CalendarError('unavailable')
        previews = []
        for shown in self._drafts(row):
            try:
                preview = connector.preview(shown['draft_id'], owner_id)
            except ValueError:
                return CHANGED_UNDER_US
            if preview['state'] != STATE_AWAITING_APPROVAL or preview['payload_hash'] != shown.get('payload_hash'):
                return CHANGED_UNDER_US
            previews.append(preview)
        # Every draft matched what was shown before anything is sent.
        replies = []
        for index, preview in enumerate(previews):
            action = preview.get('action')
            tool = EFFECT_TOOLS.get(action, 'calendar_create')
            try:
                approval = connector.approve(preview['id'], owner_id)
                result = connector.execute(preview['id'], approval['approval_id'], owner_id)
            except CalendarError as error:
                if evidence is not None:
                    evidence(tool, 'failed', {'draft_id': preview['id'], 'error': error.reason,
                                              'effect': error.effect, 'recovery': error.recovery})
                if error.effect == 'unknown':
                    replies.append(UNKNOWN_TEXT.get(action, OUTCOME_UNKNOWN))
                else:
                    replies.append(FAILED_RECONNECT if 'reconnect' in (error.recovery or '') else FAILED_RETRY)
                # Nothing after an unconfirmed or failed step is sent.
                if index + 1 < len(previews):
                    replies.append(NOT_APPLIED_REST)
                break
            if evidence is not None:
                evidence(tool, 'succeeded', {'draft_id': preview['id'], 'event_id': result.get('id', ''),
                                             'effect': 'observed'})
            replies.append('\n'.join((DONE_TEXT.get(action, CREATED), *preview_lines(preview))))
        return '\n\n'.join(replies)
