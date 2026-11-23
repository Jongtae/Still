"""The owner's iMessage conversation over the local sidecar protocol (#1141).

AI HTTP calls never post to iMessage, and Messages identifies its own outgoing
rows as ``from_me``. The URL-keyed outbound reservation and echo holds therefore
do not apply. A per-send ledger records what Still asked the sidecar to send.
"""
from __future__ import annotations

import ctypes
import hashlib
import hmac
import logging
import os
import queue
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import imessage_sidecar
from .conversation_surface import SurfaceCapabilities
from .direct_message_surface import (DirectMessageSurface, heartbeat_outbound_process,
                                     steer_if_owner_typed)
from .messages import catalog, msg
from .providers import ProviderError

LOG = logging.getLogger('personal_agent.imessage')
INFORMATION_ROUTE = "iMessage via this Mac's Messages app (Apple)"
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
#: How long a disconnect waits for the ``scope off`` write before stopping the sidecar anyway.
SCOPE_OFF_SECONDS = 2
#: How long Still's shutdown waits for a sidecar stop under way (the launcher's bounded stop ends within this).
SHUTDOWN_SECONDS = 30
PAIRING_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
ERROR_CODES = ('no_disk_access', 'messages_not_signed_in', 'db_unreadable', 'bad_request')
TAPBACK_NAMES = {'like': '+1', 'dislike': '-1', 'love': 'heart', 'laugh': 'grin'}


class IMessageError(ProviderError):
    """An iMessage sidecar failure."""


class IMessageNotSent(IMessageError):
    """Still could not hand the message to the sidecar."""


def _parent_executable(pid):
    executable = ''
    try:
        pidpath = ctypes.CDLL('/usr/lib/libproc.dylib').proc_pidpath
        pidpath.argtypes = (ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32)
        pidpath.restype = ctypes.c_int
        buffer = ctypes.create_string_buffer(4096)
        if pidpath(pid, buffer, len(buffer)) > 0:
            executable = os.fsdecode(buffer.value)
    except (OSError, AttributeError):
        pass
    columns = 'ppid=' if executable else 'ppid=,comm='
    result = subprocess.run(['/bin/ps', '-ww', '-o', columns, '-p', str(pid)], check=True,
                            capture_output=True, text=True, timeout=1)
    fields = result.stdout.rstrip('\n').split(None, 1)
    return int(fields[0]), executable or (fields[1] if len(fields) > 1 else '')


def disk_access_target(pid=None, parent_of=None):
    """Name the outermost app responsible for Still, or Still's executable."""
    pid = os.getpid() if pid is None else pid
    starting_pid = pid
    parent_of = parent_of or _parent_executable
    executable, bundle, seen = None, None, set()
    try:
        while pid > 1:
            if pid in seen:
                raise ValueError('process ancestry loop')
            seen.add(pid)
            parent, path = parent_of(pid)
            if pid == starting_pid and os.path.isabs(path):
                executable = path
            pid = parent
            if not os.path.isabs(path):
                continue
            parts = Path(path).parts
            for index, part in enumerate(parts):
                if part.endswith('.app'):
                    bundle = str(Path(*parts[:index + 1]))
                    break
    except Exception:
        executable = bundle = None
    path = bundle or executable or os.path.realpath(sys.executable)
    target = {'name': Path(path).stem if bundle else os.path.basename(path), 'path': path}
    if not (bundle or executable):
        # The process chain could not be read: Still's interpreter is a best guess, and Settings says so.
        target['uncertain'] = True
    return target


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
    information_route = INFORMATION_ROUTE
    capabilities = SurfaceCapabilities()
    poll_seconds = 3   # the shared surface poll's interval, as Mattermost's
    reoffer_unknown_notices = False
    CONFIG_KEY, STATUS_KEY, TURNS_TABLE = CONFIG_KEY, 'imessage_status', TURNS_TABLE
    REQUEST_KEY_PREFIX, MESSAGES = 'im', 'imessage'
    NOT_SENT, ERROR = IMessageNotSent, IMessageError
    POST_REPLY_STATUS = 'sending'
    # A notice is delivered only when its echo is observed: it stays ``sending`` until
    # ``_observe_echo`` calls ``notice_delivered``; a failed send is queued again.
    NOTICE_CONFIRMED_ON_POST = False
    MAX_NOTICE_SEND_FAILURES = 3
    AT_THE_MAC = {'browser_approval_needed': 'imessage.approve_at_mac',
                  'context_approval_needed': 'surface.answer_at_mac', 'browser_login_needed': 'surface.answer_at_mac'}

    def __init__(self, service, transport=None):
        super().__init__(service)
        self._transport = transport
        # The service lock guards publication and detachment. The start marker
        # also carries cancellation while installation or the handshake runs.
        self._supervised = None
        self._starting = None
        self._stopping = False
        self._closed = False
        self._stopped = threading.Condition()   # signalled when a sidecar stop finishes
        self._retry_at = 0
        self._backoff = 1
        self._disk_access_target = None
        self.starter_spawn = lambda target: threading.Thread(target=target, name='imessage-start', daemon=True).start()
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
        self._was_ready = False
        self._acting_lock = threading.Lock()

    def outbound_target(self, cfg, url):
        return False

    def outbound_identity(self, cfg, url, status, raw):
        return None

    def present(self, action, *args):
        return False, True

    def _injected_transport(self):
        return self._transport if self._transport is not None else getattr(self.service, 'imessage_transport', None)

    def _sidecar(self):
        injected = self._injected_transport()
        if injected is not None:
            return injected
        return self._supervised if self._starting is None else None

    def offered(self):
        return imessage_sidecar.prebuild_pin() is not None

    def _wanted(self, cfg, now):
        pending = cfg.get('pairing')
        return bool(pending and pending['expires_at'] > now and not self._work_mode_refusal()) or self.usable(cfg)

    # -- Default sidecar lifecycle (injected transports are owned by their caller) -------
    def _supervise(self, now):
        starting = None
        with self.service.lock:
            if self._injected_transport() is not None or not self.offered() or not self._wanted(self.config(), now):
                sidecar = self._detach_sidecar()
            else:
                if self._closed or self._supervised is not None or self._starting is not None or self._stopping:
                    return
                if time.monotonic() < self._retry_at:
                    return
                self.service.store.put(self.STATUS_KEY, {})
                starting = self._starting = threading.Event()
                sidecar = None
        if sidecar is not None:
            self._stop_sidecar(sidecar)
        elif starting is not None:
            try:
                self.starter_spawn(lambda: self._start_sidecar(starting))
            except Exception as exc:
                LOG.warning('iMessage starter failed (%s)', type(exc).__name__)
                with self.service.lock:
                    if self._starting is starting:
                        self._starting = None
                        if not starting.is_set():
                            self._record_failure('not_ready')
                self._notify_stopped()

    def _start_sidecar(self, starting):
        retired = None
        try:
            if starting.is_set():
                return
            factory = getattr(self.service, 'imessage_sidecar_factory', None) or imessage_sidecar.IMessageSidecar
            sidecar = factory(self.service.store)
            with self.service.lock:
                if starting.is_set():
                    retired = sidecar
                    return
                self._supervised = sidecar
            ready = sidecar.start()
            with self.service.lock:
                if (not starting.is_set() and self._injected_transport() is None
                        and self.offered() and self._wanted(self.config(), time.time())):
                    self.service.store.put(self.STATUS_KEY, {'state': 'connected', 'kit': ready['kit']})
                    self._was_ready = True
                    self._backoff = 1
                    self._starting = None
                elif self._supervised is sidecar:
                    retired = self._detach_sidecar()
        except Exception as exc:
            LOG.warning('iMessage start failed (%s)', type(exc).__name__)
            with self.service.lock:
                if not starting.is_set():
                    code = exc.code if isinstance(exc, imessage_sidecar.IMessageSidecarError) else 'not_ready'
                    self._record_failure(code)
                    retired = self._detach_sidecar()
        finally:
            try:
                if retired is not None:
                    self._stop_sidecar(retired)
            finally:
                with self.service.lock:
                    if self._starting is starting:
                        self._starting = None
                self._notify_stopped()   # a start that ended (and reaped a failed child) lets shutdown finish

    def _record_failure(self, code):
        self.service.store.put(self.STATUS_KEY, {'state': 'error', 'code': code})
        self._retry_at = time.monotonic() + self._backoff
        self._backoff = min(300, self._backoff * 2)

    def _detach_sidecar(self):
        # Called under the service lock, which also guards the generation check and send.
        if self._starting is not None:
            self._starting.set()
        sidecar, self._supervised = self._supervised, None
        if sidecar is not None:
            self._stopping = True
            self._scoped_transport = self._scope = None
            self._was_ready = False
        return sidecar

    def _stop_sidecar(self, sidecar):
        try:
            def scope_off():
                try:
                    sidecar.send_op({'op': 'scope', 'mode': 'off'})
                except Exception as exc:
                    LOG.warning('iMessage scope shutdown failed (%s)', type(exc).__name__)
            # A child that stopped reading can block the write; ``stop()`` is reached either way,
            # and the launcher's own bounded shutdown ends that child.
            writer = threading.Thread(target=scope_off, name='imessage-scope-off', daemon=True)
            writer.start()
            writer.join(SCOPE_OFF_SECONDS)
            try:
                sidecar.stop()
            except Exception as exc:
                LOG.warning('iMessage sidecar shutdown failed (%s)', type(exc).__name__)
        finally:
            with self.service.lock:
                self._stopping = False
            self._notify_stopped()

    def _notify_stopped(self):
        with self._stopped:
            self._stopped.notify_all()

    def _settled(self):
        """No sidecar stop and no start (which reaps a child that failed its handshake) is under way."""
        # Plain reads, never the service lock: a notifier may hold it while waiting for this condition's lock.
        return not self._stopping and self._starting is None

    def shutdown(self):
        """Stop the sidecar and wait for any stop or start under way, so Still never exits before its child."""
        with self.service.lock:
            self._closed = True   # admitted under this lock: no start begins after shutdown
            sidecar = self._detach_sidecar()
        if sidecar is not None:
            self._stop_sidecar(sidecar)
        with self._stopped:
            self._stopped.wait_for(self._settled, timeout=SHUTDOWN_SECONDS)

    def retry(self):
        with self.service.lock:
            live = self.service.store.config(self.STATUS_KEY, {}) or {}
            sidecar = self._detach_sidecar() if live.get('state') == 'error' else None
            self._retry_at, self._backoff = 0, 1
            self.service.store.put(self.STATUS_KEY, {})
        if sidecar is not None:
            self._stop_sidecar(sidecar)
        return self.status()

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
            super().disconnect()
            self._retry_at, self._backoff = 0, 1
            sidecar = self._detach_sidecar()
            if self._injected_transport() is not None:
                self._sync_scope(self.config())
        if sidecar is not None:
            self._stop_sidecar(sidecar)
        return self.status()

    @staticmethod
    def _transport_ready(transport):
        """The transport's protocol-v1 ``ready`` frame, or None (#1142 ``IMessageSidecar.ready``)."""
        ready = getattr(transport, 'ready', None) if transport is not None else None
        if (isinstance(ready, dict) and type(ready.get('v')) is int and ready['v'] == 1
                and _wire_string(ready.get('kit'), nonempty=True)):
            return ready
        return None

    def _track_readiness(self, transport):
        """A sidecar that became ready has forgotten its scope and any earlier error."""
        ready = self._transport_ready(transport)
        if ready and not self._was_ready:
            self.service.store.put(self.STATUS_KEY, {'state': 'connected', 'kit': ready['kit']})
            self._scope = None
        self._was_ready = bool(ready)

    def status(self):
        cfg = self.config()
        live = self.service.store.config(self.STATUS_KEY, {}) or {}
        offered = self.offered()
        transport = self._sidecar()
        ready = self._transport_ready(transport)
        pending = cfg.get('pairing')
        if self._injected_transport() is not None:
            state = 'error' if live.get('state') == 'error' else 'connected' if ready else 'disconnected'
        elif offered and cfg.get('handle') and self._work_mode_refusal():
            state = 'paused'
        elif not offered or not self._wanted(cfg, time.time()):
            state = 'off'
        else:
            state = 'error' if live.get('state') == 'error' else 'connected' if ready else 'starting'
        status = {'connected': state == 'connected', 'handle_paired': bool(cfg.get('handle')),
                  'company_approved': cfg.get('company_approved') is True,
                  'usable': transport is not None and self.usable(cfg),
                  'offered': offered, 'state': state, 'pairing': bool(pending), 'handle': cfg.get('handle') or '',
                  'pairing_expires_at': pending['expires_at'] if pending else None, 'message': ''}
        if state == 'error':
            code = status['code'] = live['code']
            if code == 'no_disk_access':
                status['disk_access_app'] = self._disk_access_app().copy()
            status['message'] = self._status_message(code)
        elif state == 'paused':
            status['message'] = self._text('work_mode_unapproved')
        return status

    def _disk_access_app(self):
        with self.service.lock:
            if self._disk_access_target is None:
                self._disk_access_target = disk_access_target()
            return self._disk_access_target

    def _status_message(self, code):
        key = f'imessage.{code}'
        if code == 'no_disk_access':
            app = self._disk_access_app()
            return msg(key + ('_uncertain' if app.get('uncertain') else ''), app=app['name'])
        return msg(key if key in catalog() else 'imessage.not_ready')

    # -- Ingestion ------------------------------------------------------------------------
    def receive(self, now=None):
        """Drain sidecar events already available, including while unpaired."""
        now = time.time() if now is None else now
        with self.service.lock:
            self._expire_sends(now)
            cfg = self.config()
            if cfg.get('pairing') and (now >= cfg['pairing']['expires_at'] or self._work_mode_refusal()):
                self.cancel_pairing()
        self._supervise(now)
        with self.service.lock:
            transport = self._sidecar()
            if transport is None:
                return None
            cfg = self.config()
            usable = self.usable(cfg)
            if usable:
                heartbeat_outbound_process(self.service.store)
                self.replay_ingress()
            created = []
            self._ensure_reader(transport)
            self._track_readiness(transport)
            while True:
                try:
                    source, event = self._inbox.get_nowait()
                except queue.Empty:
                    break
                if source is not transport:
                    continue   # buffered from a transport that was replaced
                work_id = self._receive_event(event, now)
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
                LOG.warning('%s (%s)', self._text('receive_failed'), type(exc).__name__)
                with self.service.lock:
                    if transport is self._supervised:
                        code = exc.code if isinstance(exc, imessage_sidecar.IMessageSidecarError) else 'exited'
                        if code == 'exited' and isinstance(exc.__cause__, imessage_sidecar.IMessageSidecarError):
                            code = exc.__cause__.code
                        self._record_failure(code)
                        retired = self._detach_sidecar()
                    else:
                        retired = None
                        if transport is self._injected_transport():
                            self._scope = None
                if retired is not None:
                    self._stop_sidecar(retired)
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

    def _receive_event(self, event, now):
        if not isinstance(event, dict) or not isinstance(event.get('ev'), str):
            return None
        kind = event['ev']
        # Readiness comes from the transport (``_track_readiness``); its ``ready`` frames are the handshake.
        if kind == 'error' and isinstance(event.get('code'), str) and event['code'] in ERROR_CODES and isinstance(event.get('detail'), str):
            self.service.store.put(self.STATUS_KEY, {'state': 'error', 'code': event['code']})
        elif kind == 'send_failed' and _wire_string(event.get('id'), nonempty=True) and isinstance(event.get('reason'), str):
            with self.service.store.db() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute("SELECT * FROM imessage_sends WHERE id=? AND state='sending'", (event['id'],)).fetchone()
                if row and row['notification_id'] and db.execute(
                        "SELECT COUNT(*) FROM imessage_sends WHERE notification_id=? AND state='failed'",
                        (row['notification_id'],)).fetchone()[0] + 1 < self.MAX_NOTICE_SEND_FAILURES:
                    # Messages refused it, so the owner was not told: queue the notice again.
                    db.execute("UPDATE imessage_sends SET state='failed',settled=? WHERE id=?", (now, row['id']))
                    db.execute("UPDATE telegram_notifications SET state='queued' WHERE id=? AND state='sending'",
                               (row['notification_id'],))
                elif row:
                    self._unknown(db, row, now)
        elif kind in ('message', 'from_me') and _valid_message(event):
            return self._receive_message(kind, event, now)
        # The 'sent' acknowledgement only confirms handoff to Messages.
        return None

    def _receive_message(self, kind, event, now):
        cfg = self.config()
        owner, generation = cfg.get('owner_user_id'), cfg.get('generation')
        if cfg.get('pairing'):
            # Decided per row: once a row completes pairing, the rows after it in the same drain
            # (the owner's first request, the greeting's echo) pass the paired filter below.
            if kind == 'message':
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
            if send['notification_id']:
                notice = db.execute("SELECT * FROM telegram_notifications WHERE id=? AND surface='imessage' "
                                    "AND state='sending'", (send['notification_id'],)).fetchone()
            db.execute("UPDATE imessage_sends SET state='delivered',settled=? WHERE id=?", (now, send['id']))
        if notice is not None:
            self.notice_delivered(dict(notice), guid)
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
