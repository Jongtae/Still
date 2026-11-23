"""The owner's iMessage conversation over the local sidecar protocol (#1141).

AI HTTP calls never post to iMessage, and Messages identifies its own outgoing
rows as ``from_me``. The URL-keyed outbound reservation and echo holds therefore
do not apply. A per-send ledger records what Still asked the sidecar to send.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import queue
import secrets
import threading
import time

from .conversation_surface import SurfaceCapabilities
from .direct_message_surface import (DirectMessageSurface, heartbeat_outbound_process,
                                     steer_if_owner_typed)
from .providers import ProviderError

LOG = logging.getLogger('personal_agent.imessage')
CONFIG_KEY = 'imessage'
TURNS_TABLE = 'imessage_turns'
PAIRING_SECONDS = 600
MAX_PAIRING_ATTEMPTS = 5
MAX_PAIRING_TEXT_LEN = 32
DELIVERY_WINDOW_SECONDS = 120
EVENT_RETENTION_SECONDS = 7 * 86400
#: How far before the newest row seen a re-installed paired scope starts again (guids dedupe the overlap).
REPLAY_OVERLAP_MS = 10 * 60 * 1000
PRUNE_SECONDS = 3600
PAIRING_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
ERROR_CODES = ('no_disk_access', 'messages_not_signed_in', 'db_unreadable', 'bad_request')
TAPBACK_NAMES = {'like': '+1', 'dislike': '-1', 'love': 'heart', 'laugh': 'grin'}


class IMessageError(ProviderError):
    """An iMessage sidecar failure."""


class IMessageNotSent(IMessageError):
    """Still could not hand the message to the sidecar."""


def owner_key(handle):
    """The owner identity of a paired handle in ``jobs.chat_id``: never numeric text, so SQLite keeps it as written."""
    return 'handle:' + handle


def _digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _wire_string(value, *, nonempty=False):
    if not isinstance(value, str) or (nonempty and not value):
        return False
    try:
        value.encode()
    except UnicodeError:
        return False
    return True


def _valid_message(event):
    at = event.get('ts_ms')
    return (_wire_string(event.get('guid'), nonempty=True)
            and _wire_string(event.get('handle'), nonempty=True)
            and _wire_string(event.get('text'))
            and type(at) is int and 0 <= at < 2**63)


def _valid_reaction(reaction):
    return (isinstance(reaction, dict)
            and _wire_string(reaction.get('kind'), nonempty=True)
            and _wire_string(reaction.get('target_guid'), nonempty=True)
            and type(reaction.get('removed')) is bool)


class IMessageSurface(DirectMessageSurface):
    name = 'imessage'
    channel_prefix = 'imessage:'
    capabilities = SurfaceCapabilities()
    poll_seconds = 3   # the shared surface poll's interval, as Mattermost's
    reoffer_unknown_notices = False
    CONFIG_KEY, STATUS_KEY, TURNS_TABLE = CONFIG_KEY, 'imessage_status', TURNS_TABLE
    REQUEST_KEY_PREFIX, MESSAGES = 'im', 'imessage'
    NOT_SENT, ERROR = IMessageNotSent, IMessageError
    POST_REPLY_STATUS = 'sending'
    # A notice is confirmed by its echo, so watch bookkeeping waits for ``_observe_echo``.
    NOTICE_CONFIRMED_ON_POST = False
    AT_THE_MAC = {'browser_approval_needed': 'imessage.approve_at_mac',
                  'context_approval_needed': 'surface.answer_at_mac', 'browser_login_needed': 'surface.answer_at_mac'}

    def __init__(self, service, transport=None):
        super().__init__(service)
        self._transport = transport
        self._scoped_transport = None
        self._scope = None
        self._seen = {}
        # The sidecar's ``events()`` may block until its next event, so one reader thread per
        # transport consumes it and ``receive`` drains what has arrived without waiting (#1141 review).
        self._inbox = queue.Queue()
        self._reader = None
        self._reader_lock = threading.Lock()
        self.reader_spawn = lambda target: threading.Thread(target=target, name='imessage-events', daemon=True).start()
        self._acting = set()
        self._acting_lock = threading.Lock()

    def outbound_target(self, cfg, url):
        return False

    def outbound_identity(self, cfg, url, status, raw):
        return None

    def present(self, action, *args):
        return False, True

    def _sidecar(self):
        return self._transport or getattr(self.service, 'imessage_transport', None)

    def _sync_scope(self, cfg):
        """Return the sidecar with the desired scope installed, or None on refusal."""
        transport = self._sidecar()
        if transport is None:
            return None
        pending = cfg.get('pairing')
        op = {'op': 'scope', 'mode': 'off'}
        if pending and not self._work_mode_refusal():
            op.update(mode='pairing', since_ms=pending['since_ms'], max_text_len=MAX_PAIRING_TEXT_LEN)
        elif self.usable(cfg):
            op.update(mode='paired', handle=cfg['handle'],
                      since_ms=max(cfg['since_ms'], int(cfg.get('seen_ms') or 0) - REPLAY_OVERLAP_MS))
        if transport is self._scoped_transport and op == self._scope:
            return transport
        try:
            accepted = transport.send_op(op) is not False
        except (OSError, IMessageNotSent):
            accepted = False
        if not accepted:
            # A refused scope is not installed; the next tick sends it again.
            self._scope = None
            return None
        self._scoped_transport, self._scope = transport, op
        return transport

    # -- Settings and pairing ------------------------------------------------------------
    def start_pairing(self, now=None):
        with self.service.lock:
            if refusal := self._work_mode_refusal():
                raise ValueError(refusal)
            now = time.time() if now is None else now
            code = ''.join(secrets.choice(PAIRING_ALPHABET) for _ in range(8))
            expires_at = now + PAIRING_SECONDS
            cfg = self.config()
            cfg['pairing'] = {'digest': _digest(code), 'expires_at': expires_at, 'attempts': 0,
                              'since_ms': int(now * 1000)}
            self.service.store.put(CONFIG_KEY, cfg)
            self._sync_scope(cfg)
            return {'code': code, 'expires_at': expires_at}

    def cancel_pairing(self):
        with self.service.lock:
            cfg = self.config()
            if cfg.pop('pairing', None) is not None:
                self.service.store.put(CONFIG_KEY, cfg)
            self._sync_scope(cfg)
            return self.status()

    def _pair_message(self, cfg, event):
        pending = cfg['pairing']
        text = event['text']
        if event.get('reaction') is not None or event['ts_ms'] <= pending['since_ms'] or len(text) > MAX_PAIRING_TEXT_LEN:
            return
        with self.service.store.db() as db:
            # A replayed row (a sidecar restart) is one attempt, never another. Only the guid is kept.
            if not self._remember_event(db, event['guid'], time.time()):
                return
        if hmac.compare_digest(_digest(text.strip().upper()), pending['digest']):
            handle = event['handle']
            cfg.pop('pairing')
            cfg.update(enabled=True, handle=handle, owner_user_id=owner_key(handle), channel_id=handle,
                       generation=secrets.token_hex(6), since_ms=event['ts_ms'])
            self.service.store.put(CONFIG_KEY, cfg)
            self._sync_scope(cfg)
            self.send_owner(self._text('connected_hello'), owner=owner_key(handle), generation=cfg['generation'])
        else:
            pending['attempts'] += 1
            if pending['attempts'] >= MAX_PAIRING_ATTEMPTS:
                self.cancel_pairing()
            else:
                self.service.store.put(CONFIG_KEY, cfg)

    def disconnect(self):
        with self.service.lock:
            self.service.store.put(self.STATUS_KEY, {})
            status = super().disconnect()
            self._sync_scope(self.config())
            return status

    def status(self):
        cfg = self.config()
        live = self.service.store.config(self.STATUS_KEY, {}) or {}
        state = live.get('state', 'disconnected') if self._sidecar() is not None else 'disconnected'
        status = {'connected': state == 'connected', 'handle_paired': bool(cfg.get('handle')),
                  'company_approved': cfg.get('company_approved') is True,
                  'usable': self._sidecar() is not None and self.usable(cfg),
                  'state': state, 'pairing': bool(cfg.get('pairing'))}
        if state == 'error':
            status['code'] = live['code']
        return status

    # -- Ingestion ------------------------------------------------------------------------
    def receive(self, now=None):
        """Drain sidecar events already available, including while unpaired."""
        with self.service.lock:
            now = time.time() if now is None else now
            self._expire_sends(now)
            transport = self._sidecar()
            if transport is None:
                return None
            cfg = self.config()
            pairing_batch = bool(cfg.get('pairing'))
            if pairing_batch and (now >= cfg['pairing']['expires_at'] or self._work_mode_refusal()):
                self.cancel_pairing()
            usable = self.usable(cfg)
            if usable:
                heartbeat_outbound_process(self.service.store)
                self.replay_ingress()
            created = []
            self._ensure_reader(transport)
            while True:
                try:
                    source, event = self._inbox.get_nowait()
                except queue.Empty:
                    break
                if source is not transport:
                    continue   # buffered from a transport that was replaced
                work_id = self._receive_event(event, now, pairing_batch)
                if work_id:
                    created.append(work_id)
            self._record_seen()
            self._sync_scope(self.config())
        self._run_pending_acts()
        return created if usable or created else None

    def _ensure_reader(self, transport):
        """Start one reader of ``transport.events()`` unless one is running for it."""
        with self._reader_lock:
            if self._reader is transport:
                return
            self._reader = transport

        def read():
            try:
                for event in transport.events():
                    self._inbox.put((transport, event))
            except Exception as exc:
                self._scope = None
                LOG.warning('%s (%s)', self._text('receive_failed'), type(exc).__name__)
            finally:
                with self._reader_lock:
                    if self._reader is transport:
                        self._reader = None   # the next tick starts another reader
        self.reader_spawn(read)

    def _record_seen(self):
        """Advance the replay watermark of the current pairing once per batch (rows of an old one are dropped)."""
        cfg = self.config()
        seen = self._seen.pop(cfg.get('generation'), 0)
        self._seen.clear()
        if seen > int(cfg.get('seen_ms') or 0):
            self.service.store.put(CONFIG_KEY, {**cfg, 'seen_ms': seen})

    def _receive_event(self, event, now, pairing_batch):
        if not isinstance(event, dict) or not isinstance(event.get('ev'), str):
            return None
        kind = event['ev']
        if kind == 'ready' and type(event.get('v')) is int and event['v'] == 1 and _wire_string(event.get('kit'), nonempty=True):
            self.service.store.put(self.STATUS_KEY, {'state': 'connected', 'kit': event['kit']})
            self._scope = None  # a restarted sidecar has forgotten its scope
            self._sync_scope(self.config())
        elif kind == 'error' and isinstance(event.get('code'), str) and event['code'] in ERROR_CODES and isinstance(event.get('detail'), str):
            self.service.store.put(self.STATUS_KEY, {'state': 'error', 'code': event['code']})
        elif kind == 'send_failed' and _wire_string(event.get('id'), nonempty=True) and isinstance(event.get('reason'), str):
            with self.service.store.db() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute("SELECT * FROM imessage_sends WHERE id=? AND state='sending'", (event['id'],)).fetchone()
                if row:
                    self._unknown(db, row, now)
        elif kind in ('message', 'from_me') and _valid_message(event):
            return self._receive_message(kind, event, now, pairing_batch)
        # The 'sent' acknowledgement only confirms handoff to Messages.
        return None

    def _receive_message(self, kind, event, now, pairing_batch):
        cfg = self.config()
        owner, generation = cfg.get('owner_user_id'), cfg.get('generation')
        if pairing_batch:
            # Rows still buffered from pairing scope cannot turn into owner input
            # when an earlier row in this batch ends pairing.
            if kind == 'message' and cfg.get('pairing'):
                self._pair_message(cfg, event)
            return None
        if not self.usable(cfg) or event['handle'] != cfg.get('handle') or event['ts_ms'] <= cfg['since_ms']:
            return None
        self._seen[generation] = max(self._seen.get(generation, 0), event['ts_ms'])
        if kind == 'from_me':
            self._observe_echo(cfg, event, now)
            return None
        guid, reaction, text = event['guid'], event.get('reaction'), event['text']
        with self.service.store.db() as db:
            if db.execute('SELECT 1 FROM imessage_events WHERE guid=?', (guid,)).fetchone():
                return None
            if reaction is not None:
                if not _valid_reaction(reaction) or reaction['removed']:
                    return None
                target = reaction['target_guid']
                known = db.execute(
                    'SELECT 1 FROM imessage_turns AS t JOIN jobs AS j ON j.id=t.job_id '
                    'WHERE t.reply_post_id=? AND t.owner=? AND j.channel=? '
                    'UNION ALL SELECT 1 FROM telegram_notifications '
                    "WHERE message_id=? AND chat_id=? AND generation=? AND surface='imessage' LIMIT 1",
                    (target, owner, self.channel_prefix + generation, target, owner, generation)).fetchone()
                if not known or not self._remember_event(db, guid, now):
                    return None
                # Recorded with the GUID, so a stop before the act is settled replays it.
                name = reaction['kind']
                db.execute('INSERT OR IGNORE INTO imessage_pending_acts(guid,target,emoji,generation,created) '
                           'VALUES (?,?,?,?,?)', (guid, target, TAPBACK_NAMES.get(name, name), generation, now))
        if reaction is not None:
            return None   # settled by ``_run_pending_acts`` after this batch
        # The base commits the Work and its request key together. If Still stops
        # before writing the GUID here, that key already protects the next poll.
        work_id = self._ingest_owner_message(guid, text, None, owner, generation, spoke=event['ts_ms'] / 1000)
        with self.service.store.db() as db:
            self._remember_event(db, guid, now)
        if work_id and self.service.is_natural_language(text):
            steer_if_owner_typed(self.service, work_id, text)
        return work_id

    def _run_pending_acts(self):
        """Hand each recorded tapback to the owner-act path once; clear it when its worker finishes."""
        service = self.service
        with service.lock:
            cfg = self.config()
            owner, generation = cfg.get('owner_user_id'), cfg.get('generation')
            with service.store.db() as db:
                db.execute('DELETE FROM imessage_pending_acts WHERE generation IS NOT ?', (generation,))
                rows = [dict(row) for row in db.execute(
                    'SELECT * FROM imessage_pending_acts ORDER BY created,rowid').fetchall()]
            if not self.usable(cfg):
                return
        for row in rows:
            guid = row['guid']
            with self._acting_lock:
                if guid in self._acting:
                    continue
                self._acting.add(guid)
            spawned = False
            try:
                spawned = self.ingest_reaction(row['target'], row['emoji'], owner, generation,
                                               done=lambda guid=guid: self._clear_act(guid)) == 'owner_act'
            except Exception as exc:
                LOG.warning('iMessage tapback act failed (%s)', type(exc).__name__)
                continue
            finally:
                if not spawned:
                    with self._acting_lock:
                        self._acting.discard(guid)
            if not spawned:
                self._clear_act(guid)

    def _clear_act(self, guid):
        with self.service.store.db() as db:
            db.execute('DELETE FROM imessage_pending_acts WHERE guid=?', (guid,))
        with self._acting_lock:
            self._acting.discard(guid)

    @staticmethod
    def _remember_event(db, guid, now):
        return db.execute('INSERT OR IGNORE INTO imessage_events(guid,created) VALUES (?,?)', (guid, now)).rowcount

    # -- Delivery -------------------------------------------------------------------------
    def _post_reply(self, cfg, text, job):
        return self._post(cfg, text, job_id=job['id'])

    def _post_notice(self, cfg, text, notification, bound):
        return self._post(cfg, text, notification_id=notification['id'])

    def _post(self, cfg, text, *, job_id=None, notification_id=None):
        with self.service.lock:
            current = self.config()
            generation, handle = cfg['generation'], cfg['handle']
            if (not self.usable(current) or current.get('pairing') or current['generation'] != generation
                    or current.get('handle') != handle):
                raise IMessageNotSent(self._text('not_connected'))
            transport = self._sync_scope(current)
            if transport is None:
                raise IMessageNotSent(self._text('not_connected'))
            text = str(text or '')
            send_id = secrets.token_hex(16)
            store = self.service.store
            with store.db() as db:
                db.execute('INSERT INTO imessage_sends(id,job_id,notification_id,generation,handle,text_digest,state,created) '
                           'VALUES (?,?,?,?,?,?,?,?)',
                           (send_id, job_id, notification_id, generation, handle, _digest(text.strip()), 'sending', time.time()))
            try:
                if transport.send_op({'op': 'send', 'id': send_id, 'to': handle, 'text': text}) is False:
                    raise IMessageNotSent(self._text('not_connected'))   # no ready sidecar took it
            except (OSError, IMessageNotSent):
                with store.db() as db:
                    db.execute('DELETE FROM imessage_sends WHERE id=?', (send_id,))
                self._scope = None
                raise IMessageNotSent(self._text('not_connected')) from None
            # There is no message GUID until the echo arrives.
            return None

    def _observe_echo(self, cfg, event, now):
        guid, ts_ms = event['guid'], event['ts_ms']
        store = self.service.store
        with store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if not self._remember_event(db, guid, now):
                return
            send = db.execute(
                "SELECT * FROM imessage_sends WHERE state='sending' AND generation=? AND handle=? AND text_digest=? "
                'AND created BETWEEN ? AND ? AND CAST(created * 1000 AS INTEGER) <= ? ORDER BY created,rowid LIMIT 1',
                (cfg['generation'], cfg['handle'], _digest(event['text'].strip()),
                 now - DELIVERY_WINDOW_SECONDS, now, ts_ms)).fetchone()
            if not send:
                return
            if send['job_id']:
                if not db.execute("UPDATE jobs SET delivery='sent' WHERE id=? AND delivery='sending'",
                                  (send['job_id'],)).rowcount:
                    self._unknown(db, send, now)
                    return
                db.execute('INSERT INTO imessage_turns(job_id,owner,created) VALUES (?,?,?) ON CONFLICT(job_id) DO NOTHING',
                           (send['job_id'], cfg['owner_user_id'], now))
                db.execute('UPDATE imessage_turns SET reply_post_id=? WHERE job_id=?', (guid, send['job_id']))
            notice = None
            if send['notification_id'] and db.execute(
                    "UPDATE telegram_notifications SET message_id=? WHERE id=? AND surface='imessage' "
                    "AND state IN ('sent','memory_listed')", (guid, send['notification_id'])).rowcount:
                notice = db.execute('SELECT * FROM telegram_notifications WHERE id=?', (send['notification_id'],)).fetchone()
            db.execute("UPDATE imessage_sends SET state='delivered',settled=? WHERE id=?", (now, send['id']))
        if notice is not None:
            self._notice_confirmed(dict(notice))
        if send['job_id']:
            try:
                job = store.job(send['job_id'])
                if job and not store.blocked_delivery_reply(job['id']):
                    self.service.queue_memory_candidates(job)
            except Exception as exc:
                LOG.warning('iMessage memory follow-up failed (%s)', type(exc).__name__)

    def _expire_sends(self, now):
        prune = now - getattr(self, '_pruned_at', 0) >= PRUNE_SECONDS
        with self.service.store.db() as db:
            if not prune and not db.execute("SELECT 1 FROM imessage_sends WHERE state='sending' AND created<? LIMIT 1",
                                            (now - DELIVERY_WINDOW_SECONDS,)).fetchone():
                return
            db.execute('BEGIN IMMEDIATE')
            for send in db.execute("SELECT * FROM imessage_sends WHERE state='sending' AND created<?",
                                   (now - DELIVERY_WINDOW_SECONDS,)).fetchall():
                self._unknown(db, send, now)
            db.execute('DELETE FROM imessage_events WHERE created<?', (now - EVENT_RETENTION_SECONDS,))
            db.execute('DELETE FROM imessage_sends WHERE settled<?', (now - EVENT_RETENTION_SECONDS,))
        if prune:
            self._pruned_at = now

    @staticmethod
    def _unknown(db, send, now):
        db.execute("UPDATE imessage_sends SET state='unknown',settled=? WHERE id=?", (now, send['id']))
        if send['job_id']:
            db.execute("UPDATE jobs SET delivery='unknown' WHERE id=? AND delivery='sending'", (send['job_id'],))
        if send['notification_id']:
            db.execute("UPDATE telegram_notifications SET state='unknown' WHERE id=? AND surface='imessage' "
                       "AND state IN ('sending','sent','memory_listed')", (send['notification_id'],))
