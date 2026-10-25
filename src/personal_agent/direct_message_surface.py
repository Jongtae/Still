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

    def show_draft(self, job, state, text, note=None):
        return False   # no editable drafts on a direct-message surface

    # -- ingestion ----------------------------------------------------------------------
    def _ingest_owner_message(self, message_id, text, reply_to, owner, generation, spoke=None):
        """One owner message as Work, at most once and only in the current pairing; the Work id or None.

        ``spoke``: when the owner wrote it, in seconds on this Mac's clock (None when unknown).
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
            if db.execute('SELECT 1 FROM jobs WHERE request_key=?', (key,)).fetchone():
                return None
            work_id = service.store.enqueue(text, key, f'{self.channel_prefix}{generation}', owner, db=db, owner_typed=True)
            # #306 review: when the owner wrote it, not when it was fetched, so an act is never judged
            # against an item shown after it (``owner_acts.read(before=...)``); a time still ahead of
            # this Mac's clock is not trusted.
            now = time.time()
            if spoke is not None and 0 < spoke <= now + 60:
                db.execute('UPDATE jobs SET source_at=? WHERE id=?', (min(spoke, now), work_id))
            db.execute(f'INSERT INTO {self.TURNS_TABLE}(job_id,owner,source_post_id,reply_to_post_id,created) VALUES (?,?,?,?,?)',
                       (work_id, owner, message_id, reply_to, time.time()))
        LOG.info('%s: owner message queued work=%s', self.name, work_id)
        return work_id

    def ingest_reaction(self, message_id, emoji_name, owner, generation):
        """The owner's reaction (#172): an owner act on a message that showed an open item, else an observation."""
        service = self.service
        glyph = EMOJI_GLYPHS.get(emoji_name, f':{emoji_name}:')
        if service.owner_acts.bound_to(self, owner, generation, message_id):
            service.steer_spawn(lambda: service.owner_acts.answer_reaction(self, owner, generation, message_id, [glyph]))
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
                waiting=service.step_approval_pending(job['id'])
                or (not job.get('owner_note') and service.work_awaits_owner_preview(job)))
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
                post_id = self._post(cfg, text)
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
        fingerprint = service.document_fingerprint() if kind == 'approval_needed' else fingerprint
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
