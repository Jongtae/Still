"""The owner's approval of the calendar drafts a Work made, in the one conversation.

#22 (owner decision 2026-10-19, option A).  The owner's chosen AI interprets a
calendar request through its ordinary tools: ``calendar_query`` to read, and
``calendar_draft_create`` / ``calendar_draft_update`` / ``calendar_draft_cancel``
to propose.  A draft has no external effect.  What this module keeps is the
approval half of the earlier scripted conversation, unchanged in its authority:

* **Still renders the preview.**  When a Work ends, the drafts it made are
  read back from the connector (``CalendarConnector.preview``) and shown in
  Still's own words: every field that would be sent, with any line break or
  control character in a value shown escaped, so a value cannot add a line to
  the preview.  The model's prose never stands in for the preview.
* **Only the owner's explicit approval of that preview executes.**  The
  owner's next typed reply on the channel that was shown the preview,
  matched against the literal ``APPROVE_WORDS`` / ``CANCEL_WORDS`` below,
  approves or cancels the drafts shown.  Approval checks every draft's state
  and payload hash against what was shown before anything is sent, then
  spends ``CalendarConnector.approve`` -> ``execute`` for each, which binds a
  one-time token to this owner, this draft, this payload hash and the write
  connector's revision.
* **Anything else leaves the drafts inert.**  Another message drops the
  pending drafts (and says so) and runs as an ordinary Work; a newer Work's
  drafts replace them; they expire after ``PENDING_TTL_SECONDS``; the other
  channel cannot approve or cancel them.

No owner wording is parsed into a date, time or title here.  Durable state is
one ``calendar_conversation`` row per connector identity: the hashed owner,
the draft ids and payload hashes shown, and an expiry.  It holds no utterance
text and no secret.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime

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
REPLACED_NOTICE = '이전 일정 초안은 취소했습니다.'
DROPPED_NOTICE = '대기 중이던 일정 초안은 실행하지 않고 취소했습니다.'
CANCELLED = msg('calendar_conversation.the_event_draft_was_canceled_no')
NOTHING_PENDING = '승인할 일정 초안이 없어 아무 일정도 만들지 않았습니다. 새로 요청해 주세요.'
OTHER_CHANNEL = ('이 일정 초안은 다른 채널에서 만든 것이라 여기서 승인하지 않았습니다. '
                 '초안을 만든 채널에서 "승인"이라고 답해 주세요.')
CHANGED_UNDER_US = '초안 내용이 확인한 미리보기와 달라 캘린더에 아무것도 반영하지 않았습니다. 다시 요청해 주세요.'
CREATED = msg('calendar_conversation.the_event_was_added_to_the')
UPDATED = msg('calendar_conversation.the_event_was_changed')
CANCELLED_EVENT = msg('calendar_conversation.the_event_was_cancelled')
OUTCOME_UNKNOWN = msg('calendar_conversation.i_cannot_confirm_whether_the_event')
UPDATE_UNKNOWN = msg('calendar_conversation.i_cannot_confirm_whether_the_change')
CANCEL_UNKNOWN = msg('calendar_conversation.i_cannot_confirm_whether_the_cancellation')
FAILED = '캘린더에 반영하지 못했습니다. 이 초안은 반영되지 않았습니다.'
NOT_APPLIED_REST = '나머지 초안은 반영하지 않았습니다.'
#: An update or cancellation whose target this Work's own calendar read did not show.
UNVERIFIED_TARGET = ('이 작업에서 조회한 일정과 맞춰 볼 수 없는 일정 변경·취소 초안 {count}개는 대상 일정을 보여 드릴 수 없어 '
                     '여기서 승인할 수 없습니다. 아무것도 반영하지 않았습니다.')
RECONNECT_HINT = ' 캘린더를 다시 연결한 뒤 새로 요청해 주세요.'
RETRY_HINT = ' 새로 요청해 주세요.'
ACTION_LABELS = {'create': '새 일정', 'update': '일정 변경', 'cancel': '일정 취소'}
DONE_TEXT = {'create': CREATED, 'update': UPDATED, 'cancel': CANCELLED_EVENT}
#: What an unconfirmed outcome tells the owner to check, by action.
UNKNOWN_TEXT = {'create': OUTCOME_UNKNOWN, 'update': UPDATE_UNKNOWN, 'cancel': CANCEL_UNKNOWN}
#: The evidence tool name of an applied draft, by action.
EFFECT_TOOLS = {'create': 'calendar_create', 'update': 'calendar_update', 'cancel': 'calendar_cancel'}

# --- vocabularies ------------------------------------------------------------
# Every entry is a literal an owner can read and a reviewer can audit.
APPROVE_WORDS = frozenset({
    '승인', '승인해', '승인해줘', '승인해 줘', '승인할게', '승인합니다', '승인이요', '확인', '확인했어', '확인했어요',
    '네', '예', '응', '어', '좋아', '좋아요', '그래', '그래요', '맞아', '맞아요', '진행해', '진행해줘', '진행해 줘',
    '만들어', '만들어줘', '만들어 줘', '등록해줘', '등록해 줘', '그렇게 해줘', '그렇게 해', '해줘',
    'ok', 'okay', 'yes', 'y', 'yep', 'yeah', 'approve', 'approved', 'confirm', 'confirmed', 'go ahead',
    'do it', 'create it', 'looks good', 'lgtm', 'sure', 'go',
})
CANCEL_WORDS = frozenset({
    '취소', '취소해', '취소해줘', '취소해 줘', '아니', '아니요', '아니오', '아냐', '됐어', '됐어요', '그만', '그만해',
    '하지 마', '하지마', '하지 마세요', '없던 걸로', '없던걸로', '잊어', '잊어줘', '관둬', '말아', '말고',
    'no', 'nope', 'cancel', 'never mind', 'nevermind', 'forget it', 'stop', "don't", 'dont', 'scratch that',
    'not now', 'abort',
})
#: Tokens that may accompany a bare cancel without changing its meaning.
_CANCEL_FILLER = frozenset({'그거', '그건', '이거', '이건', '일정', '초안', '그냥', '전부', '다', '좀', 'it', 'that',
                            'this', 'the', 'draft', 'event', 'please', 'just', 'all'})


def _tokens(text):
    return [token for token in re.split(r'[\s,.!。！]+', text.strip().casefold()) if token]


def is_approval(text):
    value = re.sub(r'[\s.!。！~]+$', '', text.strip().casefold())
    value = value.strip(' \t"“”\'‘’')
    return value in APPROVE_WORDS


def is_cancel(text):
    value = re.sub(r'[.!。！~]+$', '', text.strip().casefold()).strip(' \t"“”\'‘’')
    if value in CANCEL_WORDS:
        return True
    tokens = _tokens(value)
    if not tokens:
        return False
    vocab = CANCEL_WORDS | _CANCEL_FILLER | {'그게', '아니라', '아니고'}
    return all(token in vocab for token in tokens) and any(token in CANCEL_WORDS for token in tokens)


def shown_value(value):
    """A value exactly as it would be sent, on one line: escaped when it carries a break or control character."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return json.dumps(text, ensure_ascii=False) if _UNSAFE.search(text) else text


def describe_span(start, end, zone):
    """``2026-09-23 (수) 15:00 – 16:00 (Asia/Seoul)`` from the RFC3339 times as sent."""
    first = datetime.fromisoformat(start)
    last = datetime.fromisoformat(end)
    day = f"{first:%Y-%m-%d} ({_WEEKDAYS_KO[first.weekday()]})"
    if last.date() == first.date():
        return f"{day} {first:%H:%M} – {last:%H:%M} ({shown_value(zone)})"
    return f"{day} {first:%H:%M} – {last:%Y-%m-%d} ({_WEEKDAYS_KO[last.weekday()]}) {last:%H:%M} ({shown_value(zone)})"


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

    def pending_elsewhere(self, owner_id):
        """Another identity holds live drafts.

        Used only to answer "that one is on your other channel"; it never reads
        or touches the other row beyond its existence.
        """
        key = _owner_key(owner_id)
        return any(other != key and self._live(row) and row.get('state') == STATE_AWAITING_APPROVAL
                   for other, row in self._rows().items())

    def clear(self, owner_id=None):
        rows = self._rows()
        if owner_id is None:
            previous = next((r for r in rows.values() if r.get('state')), None)
            self.store.put(STATE_KEY, {})
            return previous
        previous = rows.pop(_owner_key(owner_id), None)
        self.store.put(STATE_KEY, rows)
        return previous if isinstance(previous, dict) and previous.get('state') else None

    def _save(self, owner_id, drafts):
        row = {'owner': _owner_key(owner_id), 'state': STATE_AWAITING_APPROVAL, 'drafts': drafts,
               'at': float(self.now()), 'expires': float(self.now()) + self.ttl_seconds}
        rows = self._rows()
        rows[_owner_key(owner_id)] = row
        self.store.put(STATE_KEY, rows)
        return row

    # -- the Work's drafts --------------------------------------------------------
    def offer(self, owner_id, draft_ids, targets=None):
        """Make a finished Work's drafts the ones this identity may approve; Still's preview text, or ''.

        Each draft is read back from the connector, so the preview shows what
        the connector holds, not what a tool result or the model said.  A draft
        this owner cannot see, or one no longer awaiting approval, is not shown
        and cannot be approved here.  An update or cancellation is offered only
        when ``targets`` - this Work's own calendar reads, by (event id,
        version) - show its target event, so the owner sees which event it
        changes.  Earlier pending drafts are replaced.
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
                               'action': preview.get('action')} for preview in shown])
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
    def claims(self, owner_id, text):
        """Whether the owner's message answers this identity's pending drafts.

        Only the literal approve and cancel words do.  An approval or cancel
        aimed at the other channel's drafts is claimed too, so the answer can
        say where they are.
        """
        if not isinstance(text, str) or not (is_approval(text) or is_cancel(text)):
            return False
        return self.has_pending(owner_id) or self.pending_elsewhere(owner_id)

    def handle(self, owner_id, text, *, evidence=None):
        """Answer one approve or cancel reply that ``claims`` accepted."""
        row = self.pending(owner_id)
        if row is None:
            if (is_approval(text) or is_cancel(text)) and self.pending_elsewhere(owner_id):
                # Named, not touched: the other channel's drafts are neither
                # approved nor destroyed from here.
                return OTHER_CHANNEL
            return NOTHING_PENDING
        if row.get('owner') != _owner_key(owner_id):
            return OTHER_CHANNEL
        if is_cancel(text):
            self.clear(owner_id)
            return CANCELLED
        if is_approval(text):
            return self._approve(owner_id, row, evidence)
        return NOTHING_PENDING

    def _approve(self, owner_id, row, evidence):
        connector = self.connector_for(owner_id)
        if connector is None:
            self.clear(owner_id)
            raise CalendarError('unavailable')
        previews = []
        for shown in self._drafts(row):
            try:
                preview = connector.preview(shown['draft_id'], owner_id)
            except ValueError:
                self.clear(owner_id)
                return CHANGED_UNDER_US
            if preview['state'] != STATE_AWAITING_APPROVAL or preview['payload_hash'] != shown.get('payload_hash'):
                self.clear(owner_id)
                return CHANGED_UNDER_US
            previews.append(preview)
        # Every draft matched what was shown before anything is sent.  The
        # pending record is dropped *before* dispatch: the worker is serialised,
        # so a second "승인" finds nothing, and the connector's own
        # approved/executing/completed states are the further barriers.
        self.clear(owner_id)
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
                    hint = RECONNECT_HINT if 'reconnect' in (error.recovery or '') else RETRY_HINT
                    replies.append(FAILED + hint)
                # Nothing after an unconfirmed or failed step is sent.
                if index + 1 < len(previews):
                    replies.append(NOT_APPLIED_REST)
                break
            if evidence is not None:
                evidence(tool, 'succeeded', {'draft_id': preview['id'], 'event_id': result.get('id', ''),
                                             'effect': 'observed'})
            replies.append('\n'.join((DONE_TEXT.get(action, CREATED), *preview_lines(preview))))
        return '\n\n'.join(replies)
