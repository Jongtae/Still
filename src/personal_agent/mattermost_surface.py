"""MATTERMOST-01 (#213): a Mattermost direct message as an owner conversation surface, outbound only.

The owner talks to Still in a direct message with a bot account on their
Mattermost server, typically a company-hosted one.  Still connects **out** to
the server's REST API v4 and polls the one direct-message channel; nothing on
this Mac is exposed and no callback is received.

Still-owned boundary (C16: the owner's AI does the work; this module only
carries the conversation):

* **Pairing.** Exactly one owner identity: the Mattermost user the owner named
  in Settings on this Mac.  Only that user's posts in the bot's direct message
  with them are read; any other user, channel or post is never read as an act.
  The bot token is held in the secret store (``SECRET_KEY``) and is never in
  conversation, a log line or Evidence.
* **Ingestion.** ``posts?since=`` returns new posts, reaction changes and edits
  in one call (observed on Mattermost 11, see ``tests/test_mattermost_surface``).
  A new owner post becomes Work exactly once (its request key is the post id);
  an edit is never a new act; an owner reaction is an owner act only on a post
  that showed an open item (OWNER-ACT-01, #172) and otherwise an observation.
* **Approvals.** Ordinary approvals settle through typed replies and reactions
  judged by OWNER-ACT-01.  An exact one-use action approval (a payment or another
  step Still cannot run on pilot authority) is never answered here: owner
  decision 2026-10-25 on #213, "approve at the Mac".  The surface declares no
  ``action_approvals`` capability, and its prompt says where to approve.
* **Delivery.** A reply is marked ``sending`` before the post; a connect failure
  leaves it pending (nothing was sent), any other failure makes it ``unknown`` and
  it is never posted again.
* **Work mode (#208).** While work mode is on the surface is used only when the
  owner marked it company-approved in Settings; otherwise it is paused like
  Telegram and nothing queued while it was on is sent later.
"""
from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from urllib.parse import quote, urlsplit

from .conversation_surface import (PRESENCE_REACT, PRESENCE_TYPING, ConversationSurface, SurfaceCapabilities,
                                   SurfacePairing, notice_binding, notice_text)
from .messages import msg
from .providers import ProviderError
from . import work_mode

LOG = logging.getLogger('personal_agent.mattermost')

CONFIG_KEY = 'mattermost'
SECRET_KEY = 'mattermost_token'
TURNS_TABLE = 'mattermost_turns'
#: Seconds between two polls of the direct-message channel.
POLL_SECONDS = 3
REQUEST_SECONDS = 15
MAX_RESPONSE_BYTES = 4_000_000
#: Mattermost's post limit is 16383 characters; longer replies are sent in parts.
POST_CHARS = 16000
MAX_OWNER_POST_CHARS = 12000
#: Unicode presence emoji Still uses, by their Mattermost names.
EMOJI_NAMES = {'👀': 'eyes', '👌': 'ok_hand', '✍': 'writing_hand', '✍️': 'writing_hand', '👍': '+1', '👎': '-1',
               '🤔': 'thinking_face', '🙏': 'pray', '🔥': 'fire', '🎉': 'tada', '❤': 'heart', '✅': 'white_check_mark',
               '❌': 'x', '😢': 'cry', '😁': 'grin', '🤝': 'handshake', '⚡': 'zap', '💯': '100', '🫡': 'saluting_face'}
EMOJI_GLYPHS = {name: glyph for glyph, name in EMOJI_NAMES.items() if not glyph.endswith('️')}


class MattermostError(ProviderError):
    """A Mattermost API refusal or an unreadable answer; never carries the token."""


class MattermostNotSent(MattermostError):
    """The request never reached the server (connect or TLS failed): definitely not delivered."""


def _http_transport():
    """A pooled HTTP client like the Telegram one (#121): no redirects, the OS trust store."""
    import httpx2
    client = httpx2.Client(transport=httpx2.HTTPTransport(verify=True, retries=2), follow_redirects=False)

    def send(method, url, headers, body, timeout):
        budget = httpx2.Timeout(timeout, connect=min(timeout, 10))
        try:
            with client.stream(method, url, content=body, headers=headers, timeout=budget) as response:
                data = bytearray()
                for chunk in response.iter_bytes():
                    data += chunk
                    if len(data) > MAX_RESPONSE_BYTES:
                        break
                return response.status_code, bytes(data)
        except (httpx2.ConnectTimeout, httpx2.PoolTimeout, httpx2.ConnectError):
            raise MattermostNotSent('Mattermost 서버에 연결하지 못했습니다.') from None
        except (httpx2.TransportError, OSError):
            raise MattermostError('Mattermost 서버의 응답을 받지 못했습니다.') from None
    return send


def server_url(value):
    """The normalized server base URL: https, or http only on this Mac (a local server)."""
    text = str(value or '').strip().rstrip('/')
    parts = urlsplit(text)
    host = (parts.hostname or '').lower()
    if (not host or parts.username or parts.password or parts.query or parts.fragment
            or parts.scheme not in ('https', 'http') or parts.scheme == 'http' and host not in ('127.0.0.1', 'localhost', '::1')):
        raise ValueError(msg('mattermost.invalid_server'))
    return text


class MattermostClient:
    """The few REST API v4 calls the surface makes, authenticated by the bot token."""

    def __init__(self, base_url, token, transport=None, guard=None):
        self.base_url, self._token = server_url(base_url), token
        self._transport = transport or _http_transport()
        #: #306 review: ``() -> None`` asked before every request; it raises ``MattermostNotSent`` when the
        #: request may not go (work mode, a destination that is no longer allowed), so nothing is sent.
        self._guard = guard

    def call(self, method, path, body=None):
        if self._guard is not None:
            self._guard()
        url = self.base_url + '/api/v4' + path
        headers = {'Authorization': f'Bearer {self._token}', 'Content-Type': 'application/json',
                   'X-Requested-With': 'XMLHttpRequest'}
        data = None if body is None else json.dumps(body).encode()
        status, raw = self._transport(method, url, headers, data, REQUEST_SECONDS)
        try:
            payload = json.loads(raw.decode('utf-8')) if raw else None
        except (UnicodeDecodeError, ValueError):
            payload = None
        if not 200 <= status < 300:
            code = payload.get('id') if isinstance(payload, dict) else None
            raise MattermostError(f'Mattermost가 요청을 거절했습니다 ({status}{", " + str(code) if code else ""}).',
                                  status=status)
        return payload

    def me(self):
        return self.call('GET', '/users/me')

    def user_by_username(self, username):
        return self.call('GET', '/users/username/' + quote(str(username).lstrip('@'), safe=''))

    def direct_channel(self, user_a, user_b):
        return self.call('POST', '/channels/direct', [user_a, user_b])

    def posts_since(self, channel_id, since):
        """``[post]`` created or changed since ``since`` (ms), oldest change first."""
        data = self.call('GET', f'/channels/{quote(channel_id, safe="")}/posts?since={int(since)}')
        posts = list((data or {}).get('posts', {}).values()) if isinstance(data, dict) else []
        return sorted((post for post in posts if isinstance(post, dict)), key=lambda post: post.get('update_at') or 0)

    def create_post(self, channel_id, message):
        return self.call('POST', '/posts', {'channel_id': channel_id, 'message': message})

    def add_reaction(self, user_id, post_id, emoji_name):
        return self.call('POST', '/reactions', {'user_id': user_id, 'post_id': post_id, 'emoji_name': emoji_name})

    def remove_reaction(self, user_id, post_id, emoji_name):
        return self.call('DELETE', f'/users/{quote(user_id, safe="")}/posts/{quote(post_id, safe="")}/reactions/'
                         + quote(emoji_name, safe=''))

    def typing(self, user_id, channel_id):
        return self.call('POST', f'/users/{quote(user_id, safe="")}/typing', {'channel_id': channel_id})


class MattermostSurface(ConversationSurface):
    """The owner's Mattermost direct message, for one ``AgentService``."""

    name = 'mattermost'
    channel_prefix = 'mattermost:'
    #: A reaction on the owner's post and a typing indicator; no editable drafts, buttons or native Stop,
    #: and no exact action approvals (owner decision 2026-10-25: those are approved at the Mac).
    capabilities = SurfaceCapabilities(reactions=True, typing=True)
    poll_seconds = POLL_SECONDS

    def __init__(self, service, transport=None):
        self.service = service
        self._transport = transport
        self._lock = threading.Lock()
        # ``mattermost_turns`` is created with the store (``QuickStore``), never here: a surface is first
        # built inside other transactions.

    # -- configuration --------------------------------------------------------------
    def config(self):
        row = self.service.store.config(CONFIG_KEY, {}) or {}
        return row if isinstance(row, dict) else {}

    def client(self, cfg=None, token=None):
        cfg = self.config() if cfg is None else cfg
        token = self.service.store.secret(SECRET_KEY) if token is None else token
        if not cfg.get('server_url') or not token:
            raise MattermostError(msg('mattermost.not_connected'))
        url = cfg['server_url']
        return MattermostClient(url, token, transport=self._transport or getattr(self.service, 'mattermost_transport', None),
                                guard=lambda: self._request_guard(url))

    #: Seconds a destination check is reused (the system resolver is asked again after that).
    DESTINATION_SECONDS = 15

    def _request_guard(self, url):
        """#306 review: before every request, the surface is still usable (work mode, approval) and the
        server's current addresses are still allowed (#209: an undeclared private address is refused, a
        removed intranet host from the next request).  Raises ``MattermostNotSent``: nothing was sent."""
        if not self.usable():
            raise MattermostNotSent(msg('mattermost.work_mode_unapproved') if work_mode.active(self.service.store)
                                    else msg('mattermost.not_connected'))
        now = time.monotonic()
        cached = getattr(self, '_destination_checked', None)
        if cached and cached[0] == url and cached[1] > now:
            return
        try:
            self._check_destination(url, resolver=self._resolver)
        except ValueError as exc:
            self._destination_checked = None
            raise MattermostNotSent(str(exc)) from None
        self._destination_checked = (url, now + self.DESTINATION_SECONDS)

    @staticmethod
    def _resolver(host, port, **kwargs):
        import socket
        return socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)

    def usable(self, cfg=None):
        """Configured and, while work mode is on, marked company-approved by the owner."""
        cfg = self.config() if cfg is None else cfg
        if not (cfg.get('enabled') and cfg.get('owner_user_id') and cfg.get('channel_id') and cfg.get('generation')):
            return False
        return cfg.get('company_approved') is True or not work_mode.active(self.service.store)

    # -- the ConversationSurface boundary -------------------------------------------------
    def pairing(self):
        cfg = self.config()
        return SurfacePairing(f"mattermost:{cfg['generation']}", cfg['owner_user_id']) if self.usable(cfg) else None

    def owns(self, job):
        cfg = self.config()
        return bool(job and cfg.get('enabled') and cfg.get('owner_user_id')
                    and job.get('channel') == f"mattermost:{cfg.get('generation')}"
                    and job.get('chat_id') == cfg.get('owner_user_id'))

    def paired_connector_owner(self, enabled_only=True):
        cfg = self.config()
        if not cfg.get('owner_user_id') or enabled_only and not cfg.get('enabled'):
            return None
        return f"mattermost:{cfg['owner_user_id']}"

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
            LOG.warning('mattermost owner message not delivered (%s)', type(exc).__name__)
            return False
        return True

    def _post(self, cfg, text):
        """Post ``text`` (in parts when long) to the owner's direct message; the last post's id."""
        client = self.client(cfg)
        text = str(text or '')
        parts = [text[offset:offset + POST_CHARS] for offset in range(0, len(text), POST_CHARS)] or ['']
        post = None
        for index, part in enumerate(parts):
            try:
                post = client.create_post(cfg['channel_id'], part)
            except MattermostNotSent:
                if index == 0:
                    raise
                # #306 review: earlier parts were delivered, so the message is not "not sent": it is
                # unknown and is never posted again from the start.
                raise MattermostError('Mattermost에 메시지의 일부만 보냈습니다.') from None
        return post.get('id') if isinstance(post, dict) else None

    # -- turns (the owner's post a Work answers, and Still's reply) -------------------------
    def _turn(self, work_id):
        with self.service.store.db() as db:
            row = db.execute(f'SELECT * FROM {TURNS_TABLE} WHERE job_id=?', (work_id,)).fetchone()
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

    # -- presence -----------------------------------------------------------------------
    def present(self, action, *args):
        cfg = self.config()
        if not self.usable(cfg):
            return False, False
        try:
            client = self.client(cfg)
            if action == PRESENCE_TYPING:
                client.typing(cfg['bot_user_id'], cfg['channel_id'])
                return True, False
            if action == PRESENCE_REACT:
                # ``(owner, post id, emoji)``: one reaction at a time, as on Telegram; '' clears it.
                post_id, emoji = args[1], args[2] if len(args) > 2 else ''
                names = [EMOJI_NAMES.get(item) for item in ([emoji] if emoji else [])]
                if any(name is None for name in names):
                    return False, True     # an emoji Mattermost has no name for here: not shown
                previous = self._shown.get(post_id, [])
                for name in previous:
                    if name not in names:
                        client.remove_reaction(cfg['bot_user_id'], post_id, name)
                for name in names:
                    if name not in previous:
                        client.add_reaction(cfg['bot_user_id'], post_id, name)
                self._shown[post_id] = names
                return True, False
        except MattermostError as exc:
            return False, bool(exc.status and 400 <= exc.status < 500)
        except Exception:
            return False, False
        return False, True

    @property
    def _shown(self):
        if not hasattr(self, '_shown_reactions'):
            self._shown_reactions = {}
        return self._shown_reactions

    def show_draft(self, job, state, text, note=None):
        return False   # no editable drafts on this surface

    # -- ingestion ----------------------------------------------------------------------
    def receive(self, now=None):
        """One poll of the owner's direct message: new owner posts become Work; owner reactions are acts."""
        cfg = self.config()
        if not self.usable(cfg):
            return None
        with self._lock:
            try:
                posts = self.client(cfg).posts_since(cfg['channel_id'], int(cfg.get('cursor') or 0))
            except MattermostError as exc:
                self.service.store.put('mattermost_status', {'state': 'error', 'message': msg('mattermost.receive_failed')})
                LOG.warning('mattermost: receiving failed (%s)', exc.status or type(exc).__name__)
                return None
            self.service.store.put('mattermost_status', {'state': 'connected'})
            return self.ingest(posts, cfg)

    def ingest(self, posts, cfg=None):
        """Apply one batch of changed posts; returns the Work ids it created."""
        service, cfg = self.service, dict(self.config() if cfg is None else cfg)
        owner, generation = cfg['owner_user_id'], cfg['generation']
        cursor = int(cfg.get('cursor') or 0)
        # #306 review: every post and reaction of the batch is judged against the watermarks the batch
        # started from (posts arrive ordered by their last change, not their creation); they advance
        # only after the batch.  A post is taken once by its request key, a reaction by its own key.
        seen_from = int(cfg.get('seen_created') or 0)
        reacted_from = int(cfg.get('reaction_cursor') or 0)
        done_at = set(cfg.get('reactions_at_cursor') or ())
        seen, reacted = seen_from, reacted_from
        created, acts, keys = [], [], {}
        for post in posts:
            cursor = max(cursor, int(post.get('update_at') or 0))
            if post.get('channel_id') != cfg['channel_id']:
                continue
            if post.get('user_id') == owner and not post.get('type') and not post.get('delete_at'):
                made = int(post.get('create_at') or 0)
                # An edit of a post taken before is never a new act (#626): its request key exists.
                if made >= seen_from:
                    seen = max(seen, made)
                    work_id = self._ingest_post(post, owner, generation)
                    if work_id:
                        created.append((work_id, post.get('message') or ''))
                elif post.get('edit_at'):
                    LOG.info('mattermost: an edited owner post is not a new request')
            elif post.get('user_id') not in (owner, cfg.get('bot_user_id')):
                LOG.info('mattermost: a post from another user was ignored')
            for reaction in (post.get('metadata') or {}).get('reactions') or ():
                if not (isinstance(reaction, dict) and reaction.get('user_id') == owner and not reaction.get('delete_at')):
                    continue
                at = int(reaction.get('create_at') or 0)
                key = f"{post['id']}:{reaction.get('emoji_name') or ''}:{at}"
                if at < reacted_from or at == reacted_from and key in done_at or key in keys:
                    continue
                keys[key] = at
                acts.append((at, post['id'], reaction.get('emoji_name') or ''))
        if keys:
            reacted = max(reacted_from, *keys.values())
        at_cursor = sorted({key for key, at in keys.items() if at == reacted}
                           | (done_at if reacted == reacted_from else set()))[-50:]
        with service.lock:
            current = self.config()
            if current.get('generation') == generation:
                service.store.put(CONFIG_KEY, {**current, 'cursor': cursor, 'seen_created': seen,
                                               'reaction_cursor': reacted, 'reactions_at_cursor': at_cursor})
        acts = [(post_id, name) for _at, post_id, name in sorted(acts)]
        for work_id, text in created:
            if service.is_natural_language(text):
                service.consider_steer(work_id, text)
        for post_id, name in acts:
            self.ingest_reaction(post_id, name, owner, generation)
        return [work_id for work_id, _text in created]

    def _ingest_post(self, post, owner, generation):
        service = self.service
        text = str(post.get('message') or '')
        if not text.strip():
            return None
        if len(text) > MAX_OWNER_POST_CHARS:
            from .quickstart_service import truncated_owner_message
            text = truncated_owner_message(text)
        reply_to = post.get('root_id') or None
        with service.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM jobs WHERE request_key=?', (f"mm:{generation}:{post['id']}",)).fetchone():
                return None
            work_id = service.store.enqueue(text, f"mm:{generation}:{post['id']}", f'mattermost:{generation}', owner,
                                            db=db, owner_typed=True)
            # #306 review: when the owner wrote it, not when it was fetched, so an act is never judged
            # against an item shown after it (``owner_acts.read(before=...)``); a clock ahead of ours is not trusted.
            spoke = int(post.get('create_at') or 0) / 1000
            if 0 < spoke <= time.time() + 60:
                db.execute('UPDATE jobs SET source_at=? WHERE id=?', (spoke, work_id))
            db.execute(f'INSERT INTO {TURNS_TABLE}(job_id,owner,source_post_id,reply_to_post_id,created) VALUES (?,?,?,?,?)',
                       (work_id, owner, post['id'], reply_to, time.time()))
        LOG.info('mattermost: owner message queued work=%s', work_id)
        return work_id

    def ingest_reaction(self, post_id, emoji_name, owner, generation):
        """The owner's reaction (#172): an owner act on a post that showed an open item, else an observation."""
        service = self.service
        glyph = EMOJI_GLYPHS.get(emoji_name, f':{emoji_name}:')
        if service.owner_acts.bound_to(self, owner, generation, post_id):
            service.steer_spawn(lambda: service.owner_acts.answer_reaction(self, owner, generation, post_id, [glyph]))
            return 'owner_act'
        with service.store.db() as db:
            turn = db.execute(f'SELECT job_id FROM {TURNS_TABLE} WHERE owner=? AND reply_post_id=? ORDER BY created DESC LIMIT 1',
                              (owner, post_id)).fetchone()
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
            except MattermostNotSent:
                with service.store.db() as db:
                    db.execute("UPDATE jobs SET delivery='pending' WHERE id=? AND delivery='sending'", (job['id'],))
                LOG.warning('mattermost delivery waiting work=%s', job['id'])
                return None
            except ProviderError as exc:
                status = 'unknown'
                LOG.warning('mattermost delivery unknown work=%s status=%s', job['id'], exc.status)
            with service.store.db() as db:
                db.execute('UPDATE jobs SET delivery=? WHERE id=?', (status, job['id']))
                if post_id:
                    db.execute(f'INSERT INTO {TURNS_TABLE}(job_id,owner,created) VALUES (?,?,?) ON CONFLICT(job_id) DO NOTHING',
                               (job['id'], job['chat_id'], time.time()))
                    db.execute(f'UPDATE {TURNS_TABLE} SET reply_post_id=? WHERE job_id=?', (post_id, job['id']))
            for step in ((lambda: service.queue_memory_candidates(job)) if memory_pending and status == 'sent' else None,
                         (lambda: service.release_memory_saved(job)) if memory_saved else None,
                         lambda: service.release_working_notes(job['id'])):
                if step is None:
                    continue
                try:
                    step()
                except Exception as exc:
                    LOG.warning('mattermost follow-up failed work=%s kind=%s', job['id'], type(exc).__name__)
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

    #: What the owner does at the Mac for a notice this surface has no way to answer.
    AT_THE_MAC = {'browser_approval_needed': 'mattermost.approve_at_mac', 'approval_needed': 'mattermost.answer_at_mac',
                  'context_approval_needed': 'mattermost.answer_at_mac', 'browser_login_needed': 'mattermost.answer_at_mac'}

    def deliver_notification(self):
        service = self.service
        notification = service.store.next_notification(self.name)
        if not notification:
            return False
        from .quickstart_service import MEMORY_PROMPT_KINDS, MEMORY_SAVED_KINDS
        with service.lock:
            with service.store.db() as db:
                state = db.execute('SELECT state FROM telegram_notifications WHERE id=?', (notification['id'],)).fetchone()
            if state is None or state['state'] == 'cancelled':
                return True
            cfg = self.config()
            allowed = (notification['generation'] == cfg.get('generation') and notification['chat_id'] == cfg.get('owner_user_id')
                       and self._allowed(cfg, notification.get('created')))
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
                post_id = self._post(cfg, text)
                memory_prompt, memory_saved = bound.get('memory_prompt'), bound.get('memory_saved')
                listed = kind in MEMORY_PROMPT_KINDS and not service.memory_open(memory_prompt)
                service.store.update_notification(
                    notification['id'], 'memory_listed' if listed else 'sent', post_id,
                    json.dumps(memory_prompt) if kind in MEMORY_PROMPT_KINDS else
                    json.dumps(memory_saved) if kind in MEMORY_SAVED_KINDS else None, delivered=True)
            except MattermostNotSent:
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
    def connect(self, body):
        """Connect a server, a bot token and the owner's username; opens the direct message and says hello."""
        body = body if isinstance(body, dict) else {}
        url = server_url(body.get('server_url'))
        token = str(body.get('token') or '').strip() or self.service.store.secret(SECRET_KEY)
        username = str(body.get('owner_username') or '').strip().lstrip('@')
        if not token or len(token) > 200 or any(ch.isspace() for ch in token):
            raise ValueError(msg('mattermost.invalid_token'))
        if not username or len(username) > 64:
            raise ValueError(msg('mattermost.invalid_owner'))
        # #306 review: in work mode nothing reaches an unapproved server, connecting included.
        if work_mode.active(self.service.store) and self.config().get('company_approved') is not True:
            raise ValueError(msg('mattermost.work_mode_unapproved'))
        private = self._check_destination(url)
        client = MattermostClient(url, token, transport=self._transport or getattr(self.service, 'mattermost_transport', None))
        try:
            bot = client.me()
            owner = client.user_by_username(username)
            channel = client.direct_channel(bot['id'], owner['id'])
        except (MattermostError, KeyError, TypeError) as exc:
            raise ValueError(msg('mattermost.connect_failed')) from exc
        owner_id = str(owner.get('id') or '')
        if not owner_id or owner_id.isdigit() or owner_id == bot.get('id'):
            raise ValueError(msg('mattermost.invalid_owner'))
        # #306 review: the watermarks are the server's clock, not this Mac's: the hello post's own
        # timestamp.  Without it, a bounded overlap (posts are taken once by their key) covers a skew.
        try:
            hello = client.create_post(channel['id'], msg('mattermost.connected_hello'))
            start = int(hello.get('create_at') or 0) if isinstance(hello, dict) else 0
        except MattermostError:
            LOG.warning('mattermost: hello not delivered')
            start = 0
        start = start or int(time.time() * 1000) - self.CLOCK_OVERLAP_MS
        cfg = {'enabled': True, 'server_url': url, 'bot_user_id': bot['id'], 'bot_username': bot.get('username', ''),
               'owner_user_id': owner_id, 'owner_username': owner.get('username', username), 'channel_id': channel['id'],
               'generation': secrets.token_hex(6), 'cursor': start, 'seen_created': start, 'reaction_cursor': start,
               'company_approved': self.config().get('company_approved') is True, 'connected_at': time.time(),
               'private_server': bool(private)}
        self.service.store.secret(SECRET_KEY, token)
        self.service.store.put(CONFIG_KEY, cfg)
        return self.status()

    #: How far back, in ms, ingestion starts when the server's clock could not be read at connect.
    CLOCK_OVERLAP_MS = 120_000

    def _check_destination(self, url, resolver=None):
        """A server on a private address must be a declared intranet destination (#209); this Mac's own is a local test server."""
        from . import intranet
        from .local_tools import _bounded_system_resolver
        import ipaddress
        import socket
        found = intranet.destination(url)
        host, port = found
        if host in ('127.0.0.1', 'localhost', '::1'):
            return False
        try:
            records = (resolver or _bounded_system_resolver)(host, port, type=socket.SOCK_STREAM, timeout=10)
        except (OSError, ValueError, TimeoutError):
            raise ValueError(msg('mattermost.connect_failed')) from None
        declared = intranet.declared(self.service.store, host, port)
        for record in records:
            address = ipaddress.ip_address(str(record[4][0]).split('%')[0])
            if (declared and not intranet.address_admitted(address)) or (not declared and not address.is_global):
                raise ValueError(msg('mattermost.private_server'))
        return declared and any(not ipaddress.ip_address(str(record[4][0]).split('%')[0]).is_global for record in records)

    def disconnect(self):
        self.service.store.put(CONFIG_KEY, {'company_approved': self.config().get('company_approved') is True})
        self.service.store.secret(SECRET_KEY, '')
        return self.status()

    def set_company_approved(self, approved):
        if not isinstance(approved, bool):
            raise ValueError(msg('mattermost.invalid_value'))
        self.service.store.put(CONFIG_KEY, {**self.config(), 'company_approved': approved})
        return self.status()

    def status(self):
        cfg = self.config()
        live = self.service.store.config('mattermost_status', {}) or {}
        return {'connected': bool(cfg.get('enabled')), 'server_url': cfg.get('server_url', ''),
                'owner_username': cfg.get('owner_username', ''), 'bot_username': cfg.get('bot_username', ''),
                'company_approved': cfg.get('company_approved') is True,
                'usable': self.usable(cfg), 'state': live.get('state') if cfg.get('enabled') else 'disconnected',
                'message': live.get('message', '')}
