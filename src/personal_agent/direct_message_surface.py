"""A direct message with a bot as an owner conversation surface (#213, #214): what Mattermost and Slack share.

A surface of this kind pairs one owner identity with one direct-message channel of
a bot account on a server the owner chose (a Mattermost server, a Slack
workspace).  The subclass carries the protocol: how messages arrive, how a post is
sent, presence, connecting.  This base keeps the Still-owned conversation rules,
written once (moved unchanged from ``mattermost_surface``, #306 review rounds):

* **Pairing.** ``config()`` holds ``enabled``, ``owner_user_id``, ``channel_id``,
  ``generation`` and the owner's ``company_approved`` choice; Work of the surface
  runs on ``<prefix><generation>`` with the owner's id as its chat.
* **Ingestion.** An owner message becomes Work exactly once (its request key is
  the message id in this pairing), and only while the pairing that received it is
  still the current one (read again in the transaction that creates the Work).
  ``source_at`` is when the owner wrote it, on this Mac's clock.
* **Delivery.** A reply is marked ``sending`` before the post; ``NOT_SENT`` (the
  request never reached the server) leaves it pending, any other failure makes it
  ``unknown`` and it is never posted again.  Notices follow the shared notice
  logic (``notice_binding`` / ``notice_text``).
* **Work mode (#208).** While work mode is on the surface is used only when the
  owner marked it company-approved; nothing of a Work that ran while unapproved
  work mode was on is sent later.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import time

from .conversation_surface import ConversationSurface, SurfacePairing, notice_binding, notice_text
from .messages import msg
from .providers import ProviderError
from . import work_mode

LOG = logging.getLogger('personal_agent.direct_message')

MAX_OWNER_POST_CHARS = 12000
#: Unicode presence and reaction emoji, by the short names Mattermost and Slack both use.
EMOJI_NAMES = {'👀': 'eyes', '👌': 'ok_hand', '✍': 'writing_hand', '✍️': 'writing_hand', '👍': '+1', '👎': '-1',
               '🤔': 'thinking_face', '🙏': 'pray', '🔥': 'fire', '🎉': 'tada', '❤': 'heart', '✅': 'white_check_mark',
               '❌': 'x', '😢': 'cry', '😁': 'grin', '🤝': 'handshake', '⚡': 'zap', '💯': '100', '🫡': 'saluting_face'}
EMOJI_GLYPHS = {name: glyph for glyph, name in EMOJI_NAMES.items() if not glyph.endswith('️')}


OUTBOUND_POST = 'surface_outbound_post'
DEFERRED_POST = 'surface_deferred_post'
# Native source times use the adapters' existing measured server-clock offset.
# Two seconds covers sub-second wire precision/rounding and modest offset error,
# without turning an unknown send into a conversation-wide authority restriction.
OUTBOUND_TIME_SKEW_SECONDS = 2
# Matches Slack's existing seven-day event dedupe horizon; normal echo delivery
# takes seconds/minutes. Longer offline/replayed *identified* echoes are a limit.
OBSERVED_POST_RETENTION_SECONDS = 7 * 86400


def _reset_outbound_process():
    # A fork must not inherit its parent's identity, threads or locked mutex.
    global _PROCESS_PID, _PROCESS_TOKEN, _LIVE_OUTBOUND, _LIVE_OUTBOUND_LOCK
    _PROCESS_PID = os.getpid()
    _PROCESS_TOKEN = secrets.token_hex(32)
    _LIVE_OUTBOUND = {}
    _LIVE_OUTBOUND_LOCK = threading.Lock()


_reset_outbound_process()
if hasattr(os, 'register_at_fork'):
    os.register_at_fork(after_in_child=_reset_outbound_process)


def _reservation_key(store, event):
    # Event IDs are unique within a database, not across separate owner stores.
    return str(store.path), event


def _release_live_reservations(store, reservations):
    with _LIVE_OUTBOUND_LOCK:
        for _, event, _ in reservations:
            _LIVE_OUTBOUND.pop(_reservation_key(store, event), None)


def _attempt_is_dead(store, event, detail):
    if detail.get('process_token') == _PROCESS_TOKEN:
        with _LIVE_OUTBOUND_LOCK:
            key = _reservation_key(store, event)
            thread = _LIVE_OUTBOUND.get(key)
            if thread is not None and thread.is_alive():
                return False
            # Reap a thread that exited before observation, as well as an
            # explicit finally-release after a failed settlement transaction.
            _LIVE_OUTBOUND.pop(key, None)
            return True
    pid = detail.get('process_pid')
    if type(pid) is not int or pid <= 0:
        return False  # legacy/missing ownership is not proof of a crash
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except Exception:
        return False  # permission denied / invalid PID / unknown is not proof of death
    return False


# Never give a text redactor JSON syntax: it can consume closing delimiters,
# and its text limit is per value rather than a limit on a serialized record.
def redact_provenance_values(value, redact):
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {key: redact_provenance_values(item, redact) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_provenance_values(item, redact) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_provenance_values(item, redact) for item in value)
    return value


def _serialize_record(value, redact):
    return json.dumps(redact_provenance_values(value, redact), ensure_ascii=False, sort_keys=True)


# CASE keeps JSON extraction safe even if SQLite reorders WHERE predicates.
SAFE_EVENT_DETAIL = "CASE WHEN json_valid(detail) THEN detail ELSE '{}' END"


def _cancel_invalid_records(db):
    count = db.execute("UPDATE tool_events SET status='cancelled' "
                       "WHERE tool IN (?,?) AND status!='cancelled' "
                       "AND CASE WHEN json_valid(detail) THEN json_type(detail)!='object' ELSE 1 END",
                       (OUTBOUND_POST, DEFERRED_POST)).rowcount
    if count:
        LOG.warning('cancelled invalid surface records count=%s', count)


def _event_detail(db, row):
    try:
        detail = json.loads(row['detail'])
        if not isinstance(detail, dict):
            raise ValueError('not an object')
        return detail
    except (ValueError, TypeError):
        db.execute("UPDATE tool_events SET status='cancelled' WHERE id=?", (row['id'],))
        LOG.warning('cancelled invalid surface record id=%s', row['id'])
        return None


def steer_if_owner_typed(service, work_id, text):
    """Only host-recorded owner words may enter the running worker's owner-steer path."""
    work = service.store.job(work_id)
    if work and work.get('owner_typed') == 1:
        return service.consider_steer(work_id, text)
    return False


def reserve_outbound(store, work_id, url):
    """Host-only reservation before dispatch; platform recognition belongs to adapters.

    The committed reservation closes the response/ingress race, including a crash
    after send but before receipt. A live attempt holds ingress until it ends.
    No bodies, credentials or message text are stored in this identity ledger.
    """
    reservations = []
    try:
        with store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            for surface in getattr(store, '_outbound_surfaces', {}).values():
                cfg = store.config(surface.CONFIG_KEY, {}, db=db) or {}
                if cfg.get('enabled') and surface.outbound_target(cfg, url):
                    reserved_at = time.time()
                    detail = {'reserved_at': reserved_at, 'surface': surface.name, 'generation': cfg['generation'],
                              'channel': cfg['channel_id'], 'owner': cfg['owner_user_id'],
                              'process_pid': _PROCESS_PID, 'process_token': _PROCESS_TOKEN}
                    event = db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                                       (work_id, OUTBOUND_POST, 'pending', json.dumps(detail), reserved_at)).lastrowid
                    reservations.append((surface, event, cfg))
                    with _LIVE_OUTBOUND_LOCK:
                        _LIVE_OUTBOUND[_reservation_key(store, event)] = threading.current_thread()
    except BaseException:
        _release_live_reservations(store, reservations)
        raise
    return reservations


def observe_outbound(store, reservations, url, status, raw):
    """Every finished attempt settles; unidentified sends bound authority by time.

    Receipt persistence and hold release are atomic. Failed replay is retried by
    the ordinary receive tick, and cannot turn a known send into an unknown effect.
    """
    try:
        settled_at = time.time()
        with store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            for surface, event, cfg in reservations:
                outcome = None
                try:
                    # A transient rate-limit/error or lost response does not attest
                    # that nothing was posted. No platform-specific error list here.
                    if status is not None and status != 429:
                        outcome = surface.outbound_identity(cfg, url, status, raw)
                    if outcome is not None:
                        if redact_provenance_values(outcome, surface.service._redact_provenance) != outcome:
                            outcome = None
                except Exception:
                    outcome = None  # redaction failure must not persist the unredacted identity
                    LOG.warning('%s: outbound identity unavailable', surface.name)
                row = db.execute('SELECT id,detail FROM tool_events WHERE id=?', (event,)).fetchone()
                if row is None:
                    continue
                detail = _event_detail(db, row)
                if detail is None:
                    continue
                detail['settled_at'] = settled_at
                result = 'unidentified'
                if outcome is not None:
                    channel, post_id = outcome
                    detail.update(channel=channel, post_id=post_id)
                    result = 'observed' if post_id else 'rejected'
                db.execute('UPDATE tool_events SET status=?,detail=? WHERE id=?',
                           (result, json.dumps(detail), event))
        # Release immediately on completion, including exceptions and every retry
        # attempt. Another live attempt on the pairing can still briefly defer it.
        for surface in {surface for surface, _, _ in reservations}:
            try:
                surface.replay_ingress()
            except Exception:
                LOG.warning('%s: deferred ingress awaits receive tick', surface.name)
    finally:
        _release_live_reservations(store, reservations)


class DirectMessageSurface(ConversationSurface):
    """The rules every direct-message surface shares; a subclass supplies the protocol."""

    #: Owner state of the surface: its configuration row, live status row, turns table, secrets.
    CONFIG_KEY = STATUS_KEY = TURNS_TABLE = ''
    SECRET_KEYS = ()
    #: The request-key prefix of a Work made from an owner message (``<prefix>:<generation>:<message id>``).
    REQUEST_KEY_PREFIX = ''
    #: The ``messages.json`` prefix of the surface's own texts.
    MESSAGES = ''
    #: The surface's errors: ``NOT_SENT`` means the request never reached the server.
    NOT_SENT = ERROR = ProviderError
    #: What the owner does at the Mac for a notice kind this surface has no way to answer.
    AT_THE_MAC = {}

    def __init__(self, service):
        self.service = service
        # Composition-root objects already share this store. No global registry,
        # credentials or new service dependency; reconstructed on every restart.
        if not hasattr(service.store, '_outbound_surfaces'):
            service.store._outbound_surfaces = {}
        service.store._outbound_surfaces[self.name] = self

    def outbound_target(self, cfg, url):
        raise NotImplementedError

    def outbound_identity(self, cfg, url, status, raw):
        """Native (channel, post id), (channel, None) for refusal, or None if unknown."""
        raise NotImplementedError

    def _outbound_scope(self, cfg):
        return self.name, cfg.get('generation'), cfg.get('channel_id'), cfg.get('owner_user_id')

    def _expire_outbound(self, db):
        from .api_requests import DISPATCH_RECOVERY_BOUND_SECONDS
        _cancel_invalid_records(db)
        # Per-operation socket timeouts do not bound an entire live send.
        # Age selects crash candidates only; ownership must prove they stopped.
        rows = db.execute("SELECT id,detail,created FROM tool_events WHERE tool=? "
                          "AND status='pending' AND created<=? AND json_valid(detail) "
                          f"AND json_extract({SAFE_EVENT_DETAIL},'$.surface')=?",
                          (OUTBOUND_POST, time.time() - DISPATCH_RECOVERY_BOUND_SECONDS, self.name))
        for row in rows:
            detail = _event_detail(db, row)
            if detail is None or not _attempt_is_dead(self.service.store, row['id'], detail):
                continue
            # Keep the requested finite crash window, never the restart time.
            db.execute("UPDATE tool_events SET status='unidentified', "
                       "detail=json_set(detail,'$.settled_at',created+?) "
                       "WHERE id=? AND status='pending'",
                       (DISPATCH_RECOVERY_BOUND_SECONDS, row['id']))

    def _outbound_rows(self, db, cfg, spoke=None):
        self._expire_outbound(db)
        # Filter before deserialization: current pairing only, observed receipts
        # within the echo horizon, unidentified windows intersecting this native
        # source time only. Historical windows remain protective for late data.
        rows = db.execute("SELECT status,detail,created,id FROM tool_events WHERE tool=? AND json_valid(detail) "
                          f"AND json_extract({SAFE_EVENT_DETAIL},'$.surface')=? "
                          f"AND json_extract({SAFE_EVENT_DETAIL},'$.generation')=? "
                          f"AND json_extract({SAFE_EVENT_DETAIL},'$.channel')=? "
                          f"AND json_extract({SAFE_EVENT_DETAIL},'$.owner')=? "
                          "AND (status='pending' OR (status='observed' AND created>=?) "
                          "OR (status='unidentified' AND ? BETWEEN created-? "
                          f"AND json_extract({SAFE_EVENT_DETAIL},'$.settled_at')+?))",
                          (OUTBOUND_POST, *self._outbound_scope(cfg),
                           time.time() - OBSERVED_POST_RETENTION_SECONDS,
                           spoke, OUTBOUND_TIME_SKEW_SECONDS, OUTBOUND_TIME_SKEW_SECONDS))
        for row in rows:
            detail = _event_detail(db, row)
            if detail is None:
                continue
            detail['reserved_at'] = row['created']
            yield row['status'], detail

    def replay_ingress(self):
        """Drain durable racing messages on the normal surface tick, also after restart.

        Poll cursors / Socket Mode acknowledgments can advance after retention.
        No model or owner act runs before the matching outbound receipt is durable.
        """
        with self.service.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self._expire_outbound(db)
            rows = list(db.execute("SELECT id,detail FROM tool_events WHERE tool=? AND status=? "
                                   "AND json_valid(detail) "
                                   f"AND json_extract({SAFE_EVENT_DETAIL},'$.surface')=? ORDER BY id",
                                   (DEFERRED_POST, 'pending', self.name)))
        for row in rows:
            with self.service.store.db() as db:
                detail = _event_detail(db, row)
                if detail is None:
                    continue
                # Normalize older, valid records to the same deterministic encoding
                # used by ingestion's dedupe/delete predicates, also redacting leaves.
                canonical = _serialize_record(detail, self.service._redact_provenance)
                db.execute("UPDATE tool_events SET detail=? WHERE id=? AND status='pending'",
                           (canonical, row['id']))
            detail = json.loads(canonical)
            if detail.pop('surface') != self.name:
                continue
            cfg = self.config()
            if detail['generation'] != cfg.get('generation') or detail['owner'] != cfg.get('owner_user_id'):
                with self.service.store.db() as db:
                    db.execute("UPDATE tool_events SET status='cancelled' WHERE id=?", (row['id'],))
                continue  # a held message is never retargeted to a new pairing
            work_id = self._ingest_owner_message(**detail)
            # A still-pending reservation leaves this row in place. The ingress
            # transaction deletes it only when it can classify the message.
            if work_id and self.service.is_natural_language(detail['text']):
                steer_if_owner_typed(self.service, work_id, detail['text'])

    # -- configuration --------------------------------------------------------------
    def config(self):
        row = self.service.store.config(self.CONFIG_KEY, {}) or {}
        return row if isinstance(row, dict) else {}

    def usable(self, cfg=None):
        """Configured and, while work mode is on, marked company-approved by the owner."""
        cfg = self.config() if cfg is None else cfg
        if not (cfg.get('enabled') and cfg.get('owner_user_id') and cfg.get('channel_id') and cfg.get('generation')):
            return False
        return cfg.get('company_approved') is True or not work_mode.active(self.service.store)

    def _text(self, key):
        return msg(f'{self.MESSAGES}.{key}')

    # -- the ConversationSurface boundary -------------------------------------------------
    def pairing(self):
        cfg = self.config()
        return SurfacePairing(f"{self.channel_prefix}{cfg['generation']}", cfg['owner_user_id']) if self.usable(cfg) else None

    def owns(self, job):
        cfg = self.config()
        return bool(job and cfg.get('enabled') and cfg.get('owner_user_id')
                    and job.get('channel') == f"{self.channel_prefix}{cfg.get('generation')}"
                    and job.get('chat_id') == cfg.get('owner_user_id'))

    def paired_connector_owner(self, enabled_only=True):
        cfg = self.config()
        if not cfg.get('owner_user_id') or enabled_only and not cfg.get('enabled'):
            return None
        return f"{self.name}:{cfg['owner_user_id']}"

    def owner_generation(self):
        generation = self.config().get('generation')
        return generation if isinstance(generation, str) else ''

    def paired_owner(self, owner):
        cfg = self.config()
        return bool(cfg.get('enabled')) and owner is not None and str(owner) == str(cfg.get('owner_user_id'))

    def send_owner(self, text, reply_to=None, owner=None, generation=None):
        cfg = self.config()
        if not self.usable(cfg):
            return False
        if owner is not None and str(owner) != str(cfg['owner_user_id']):
            return False
        if generation is not None and generation != cfg.get('generation'):
            return False
        try:
            self._post(cfg, text)
        except Exception as exc:  # best effort: a lost post never changes durable state
            LOG.warning('%s owner message not delivered (%s)', self.name, type(exc).__name__)
            return False
        return True

    def _post(self, cfg, text):
        """Post ``text`` to the owner's direct message; the (last) posted message's id.  Raises ``NOT_SENT``/``ERROR``."""
        raise NotImplementedError

    def _post_notice(self, cfg, text, notification, bound):
        """Post a notice; a surface with buttons adds them here."""
        return self._post(cfg, text)

    # -- turns (the owner's message a Work answers, and Still's reply) -------------------------
    def _turn(self, work_id):
        with self.service.store.db() as db:
            row = db.execute(f'SELECT * FROM {self.TURNS_TABLE} WHERE job_id=?', (work_id,)).fetchone()
        return dict(row) if row else None

    def source_message(self, work_id):
        turn = self._turn(work_id)
        return turn['source_post_id'] if turn else None

    def reply_to(self, work_id):
        turn = self._turn(work_id)
        return turn['reply_to_post_id'] if turn else None

    def reply_message(self, work_id, owner):
        turn = self._turn(work_id)
        return turn['reply_post_id'] if turn and turn['owner'] == owner else None

    @property
    def _shown(self):
        """The presence reactions shown on each owner message (by name)."""
        if not hasattr(self, '_shown_reactions'):
            self._shown_reactions = {}
        return self._shown_reactions

    def show_draft(self, job, state, text):
        return False   # no editable drafts on a direct-message surface

    # -- ingestion ----------------------------------------------------------------------
    def _ingest_owner_message(self, message_id, text, reply_to, owner, generation, spoke=None, turn_data=None):
        """One owner message as Work, at most once and only in the current pairing; the Work id or None.

        ``spoke``: when the owner wrote it, in seconds on this Mac's clock (None when unknown).
        ``turn_data``: trusted, subclass-owned routing columns, never event-supplied column names.
        """
        service = self.service
        text = str(text or '')
        if not text.strip():
            return None
        if len(text) > MAX_OWNER_POST_CHARS:
            from .quickstart_service import truncated_owner_message
            text = truncated_owner_message(text)
        key = f'{self.REQUEST_KEY_PREFIX}:{generation}:{message_id}'
        with service.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            # #306 review: the pairing is read again in this transaction; a disconnect or reconnect while
            # the message was in flight means it submits nothing.
            current = service.store.config(self.CONFIG_KEY, {}, db=db) or {}
            if not (isinstance(current, dict) and current.get('enabled') and current.get('generation') == generation
                    and current.get('owner_user_id') == owner and self.usable(current)):
                return None
            deferred_key = {'surface': self.name, 'message_id': message_id, 'text': text,
                            'reply_to': reply_to, 'owner': owner, 'generation': generation,
                            'spoke': spoke, 'turn_data': turn_data}
            # Redact bounded string leaves before deterministic serialization;
            # text redaction must never consume JSON delimiters or truncate JSON.
            detail = _serialize_record(deferred_key, service._redact_provenance)
            outbound = list(self._outbound_rows(db, current, spoke))
            if any(status == 'pending' for status, _ in outbound):
                if not db.execute('SELECT 1 FROM tool_events WHERE tool=? AND status=? AND detail=?',
                                  (DEFERRED_POST, 'pending', detail)).fetchone():
                    db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                               (None, DEFERRED_POST, 'pending', detail, time.time()))
                return None
            db.execute('DELETE FROM tool_events WHERE tool=? AND status=? AND detail=?',
                       (DEFERRED_POST, 'pending', detail))
            if any(status == 'observed' and post.get('post_id') == message_id for status, post in outbound):
                return None  # known host post is data, never an owner act or request
            if db.execute('SELECT 1 FROM jobs WHERE request_key=?', (key,)).fetchone():
                return None
            unidentified = [post for status, post in outbound if status == 'unidentified']
            work_id = service.store.enqueue(text, key, f'{self.channel_prefix}{generation}', owner,
                                            db=db, owner_typed=not bool(unidentified))
            if unidentified:
                service.store.merge_turn_provenance(work_id, {'surface_origin': {
                    'kind': 'unidentified_outbound_window', 'surface': self.name, 'message_id': message_id,
                    'source_at': spoke, 'windows': [{'reserved_at': post['reserved_at'],
                        'settled_at': post['settled_at']} for post in unidentified]}}, db=db)
            # #306 review: when the owner wrote it, not when it was fetched, so an act is never judged
            # against an item shown after it (``owner_acts.read(before=...)``); a time still ahead of
            # this Mac's clock is not trusted.
            now = time.time()
            if spoke is not None and 0 < spoke <= now + 60:
                db.execute('UPDATE jobs SET source_at=? WHERE id=?', (min(spoke, now), work_id))
            db.execute(f'INSERT INTO {self.TURNS_TABLE}(job_id,owner,source_post_id,reply_to_post_id,created) VALUES (?,?,?,?,?)',
                       (work_id, owner, message_id, reply_to, time.time()))
            # Internal surface routing metadata, in the same transaction as the turn.
            for column, value in (turn_data or {}).items():
                db.execute(f'UPDATE {self.TURNS_TABLE} SET {column}=? WHERE job_id=?', (value, work_id))
        LOG.info('%s: owner message queued work=%s', self.name, work_id)
        return work_id

    def ingest_reaction(self, message_id, emoji_name, owner, generation, done=None):
        """The owner's reaction (#172): an owner act on a message that showed an open item, else an observation.

        ``done``: called once an owner act's judgment and settlement, which run off the poll thread, have finished
        (#843), so the caller can clear the act's pending record; every other result is final when this returns.
        """
        service = self.service
        glyph = EMOJI_GLYPHS.get(emoji_name, f':{emoji_name}:')
        if service.owner_acts.bound_to(self, owner, generation, message_id):
            def act():
                try:
                    service.owner_acts.answer_reaction(self, owner, generation, message_id, [glyph])
                finally:
                    if done is not None:
                        done()
            service.steer_spawn(act)
            return 'owner_act'
        with service.store.db() as db:
            turn = db.execute(f'SELECT job_id FROM {self.TURNS_TABLE} WHERE owner=? AND reply_post_id=? '
                              'ORDER BY created DESC LIMIT 1', (owner, message_id)).fetchone()
        if not turn:
            return None
        from .quickstart_service import REACTION_EVENT
        from . import owner_model as om
        with service.store.db() as db:
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (turn['job_id'], REACTION_EVENT, 'observed',
                        json.dumps({'emoji': [glyph], 'on': 'answer'}, ensure_ascii=False), time.time()))
        try:
            om.enqueue_self_review(service.store, turn['job_id'], om.REVIEW_REACTION, 'owner reaction on the answer: ' + glyph)
        except ValueError as exc:
            LOG.info('reaction self-review not queued (%s)', type(exc).__name__)
        return 'answer'

    # -- delivery -----------------------------------------------------------------------
    def _allowed(self, cfg, since):
        """A send is allowed while usable; a Work under way while unapproved work mode was on is never sent later."""
        if not self.usable(cfg):
            return False
        return cfg.get('company_approved') is True or not work_mode.was_on_since(self.service.store, since)

    def _post_reply(self, cfg, text, job):
        """Terminal post; a surface can carry its turn's routing metadata."""
        return self._post(cfg, text)

    def deliver_reply(self):
        service = self.service
        with service.lock:
            cfg = self.config()
            with service.store.db() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute("SELECT * FROM jobs WHERE delivery='pending' AND substr(channel,1,?)=? ORDER BY created LIMIT 1",
                                 (len(self.channel_prefix), self.channel_prefix)).fetchone()
                if not row:
                    return None
                job = dict(row)
                allowed = self.owns(job) and self._allowed(cfg, job.get('created'))
                db.execute('UPDATE jobs SET delivery=? WHERE id=?', ('sending' if allowed else 'cancelled', job['id']))
            if not allowed:
                return None
            blocked = service.store.blocked_delivery_reply(job['id'])
            text = blocked or service.telegram_result_text(
                job['response'], job.get('owner_cause') or job['error'], job.get('status'),
                verified=job.get('owner_verified'), note=job.get('owner_note'),
                waiting=service.step_approval_pending(job['id']) # #631: silence is not an absent note.
                or (job.get('owner_note') is None and service.work_awaits_owner_preview(job)))
            text = service.preparation_reply_prefix(job) + text
            try:
                memory_pending = not blocked and bool(service.pending_memory_candidates(job['id'])[0])
            except Exception:
                memory_pending = False
            try:
                memory_saved = not blocked and bool(service.work_saved_memories(job['id'])[0])
            except Exception:
                memory_saved = False
            post_id = None
            try:
                post_id = self._post_reply(cfg, text, job)
                status = 'sent'
            except self.NOT_SENT:
                with service.store.db() as db:
                    db.execute("UPDATE jobs SET delivery='pending' WHERE id=? AND delivery='sending'", (job['id'],))
                LOG.warning('%s delivery waiting work=%s', self.name, job['id'])
                return None
            except ProviderError as exc:
                status = 'unknown'
                LOG.warning('%s delivery unknown work=%s status=%s', self.name, job['id'], exc.status)
            with service.store.db() as db:
                db.execute('UPDATE jobs SET delivery=? WHERE id=?', (status, job['id']))
                if post_id:
                    db.execute(f'INSERT INTO {self.TURNS_TABLE}(job_id,owner,created) VALUES (?,?,?) ON CONFLICT(job_id) DO NOTHING',
                               (job['id'], job['chat_id'], time.time()))
                    db.execute(f'UPDATE {self.TURNS_TABLE} SET reply_post_id=? WHERE job_id=?', (post_id, job['id']))
            for step in ((lambda: service.queue_memory_candidates(job)) if memory_pending and status == 'sent' else None,
                         (lambda: service.release_memory_saved(job)) if memory_saved else None,
                         lambda: service.release_working_notes(job['id'])):
                if step is None:
                    continue
                try:
                    step()
                except Exception as exc:
                    LOG.warning('%s follow-up failed work=%s kind=%s', self.name, job['id'], type(exc).__name__)
        service._present_outcome(job, delivered=status == 'sent', blocked=bool(blocked), awaiting_owner=memory_pending)
        service.presence.pop(job['id'], None)
        return job['id']

    def queue_notification(self, job, kind, fingerprint=None):
        service = self.service
        if not self.owns(job):
            return None
        return service.store.queue_notification(job['id'], job['chat_id'], self.owner_generation(), kind, fingerprint,
                                                surface=self.name)

    def deliver_notification(self):
        service = self.service
        notification = service.store.next_notification(self.name)
        if not notification:
            return False
        from .quickstart_service import MEMORY_PROMPT_KINDS, MEMORY_SAVED_KINDS
        from . import preparations as prep
        with service.lock:
            with service.store.db() as db:
                state = db.execute('SELECT state FROM telegram_notifications WHERE id=?', (notification['id'],)).fetchone()
            if state is None or state['state'] == 'cancelled':
                return True
            cfg = self.config()
            # #306 review: the earlier of the notice and its Work, as on Telegram (``_notification_since``),
            # so a follow-up of a Work that ran while unapproved work mode was on is never sent later.
            job = service.store.job(notification['job_id']) if notification.get('job_id') else None
            times = [value for value in ((job or {}).get('created'), notification.get('created'))
                     if isinstance(value, (int, float)) and not isinstance(value, bool)]
            since = min(times) if times else None
            allowed = (notification['generation'] == cfg.get('generation') and notification['chat_id'] == cfg.get('owner_user_id')
                       and self._allowed(cfg, since))
            service.store.update_notification(notification['id'], 'sending' if allowed else 'cancelled')
            if not allowed:
                return True
            bound = notice_binding(service, notification)
            if bound is None:
                service.store.update_notification(notification['id'], 'cancelled')
                return True
            kind = notification['kind']
            try:
                text = notice_text(service, notification, bound)
                hint = self.AT_THE_MAC.get(kind)
                if hint:
                    text = f'{text}\n\n{msg(hint)}'
                post_id = self._post_notice(cfg, text, notification, bound)
                memory_prompt, memory_saved = bound.get('memory_prompt'), bound.get('memory_saved')
                listed = kind in MEMORY_PROMPT_KINDS and not service.memory_open(memory_prompt)
                service.store.update_notification(
                    notification['id'], 'memory_listed' if listed else 'sent', post_id,
                    json.dumps(memory_prompt) if kind in MEMORY_PROMPT_KINDS else
                    json.dumps(memory_saved) if kind in MEMORY_SAVED_KINDS else None, delivered=True)
                if kind == prep.NOTIFY_KIND:
                    # #306 review: as on Telegram (#719), only a confirmed send is what the owner was last
                    # told, so an unchanged result of the next watch run is not told again.
                    try:
                        job = service.store.job(notification['job_id'])
                        service.preparations.record_notified(notification['job_id'], notification['fingerprint'],
                                                             service.scrub_prepared_answer(job) if job else '',
                                                             service.preparations.clock())
                    except Exception as exc:
                        LOG.warning('watch notification record failed work=%s kind=%s', notification['job_id'],
                                    type(exc).__name__)
            except self.NOT_SENT:
                service.store.update_notification(notification['id'], 'queued')
            except ProviderError:
                service.store.update_notification(notification['id'], 'unknown')
        return True

    def cancel_pending_deliveries(self):
        service = self.service
        if self.config().get('company_approved') is True:
            return 0, 0    # a company-approved surface keeps working in work mode
        with service.lock, service.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            replies = db.execute("UPDATE jobs SET delivery='cancelled' WHERE delivery='pending' AND substr(channel,1,?)=?",
                                 (len(self.channel_prefix), self.channel_prefix)).rowcount
            notices = db.execute("UPDATE telegram_notifications SET state='cancelled' WHERE state='queued' AND surface=?",
                                 (self.name,)).rowcount
        return replies, notices

    def deliver_conversation_notice(self, text):
        self.send_owner(text)

    # -- Settings (the local endpoint is the only caller) ---------------------------------
    def disconnect(self):
        self.service.store.put(self.CONFIG_KEY, {'company_approved': self.config().get('company_approved') is True})
        for key in self.SECRET_KEYS:
            self.service.store.secret(key, '')
        return self.status()

    def set_company_approved(self, approved):
        if not isinstance(approved, bool):
            raise ValueError(msg('surface.invalid_value'))
        self.service.store.put(self.CONFIG_KEY, {**self.config(), 'company_approved': approved})
        return self.status()

    def _work_mode_refusal(self):
        """In work mode nothing reaches an unapproved server, connecting included (#306 review)."""
        if work_mode.active(self.service.store) and self.config().get('company_approved') is not True:
            return self._text('work_mode_unapproved')
        return None
