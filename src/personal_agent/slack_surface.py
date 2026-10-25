"""SLACK-01 (#214): a Slack direct message as an owner conversation surface, over Socket Mode.

The owner talks to Still in a direct message with the bot of a Slack app they
created in their workspace.  Still connects **out** to Slack: the Web API over
https, and the app's Socket Mode WebSocket for events and button presses.  No
public URL, tunnel or inbound listener exists.

Adopted (C15): the official ``slack_sdk`` (MIT, 3.45.0): ``WebClient`` and its
built-in ``socket_mode.builtin.SocketModeClient`` (standard library only).  Still
keeps only the owner-state boundary around it:

* **Pairing.** Exactly one owner identity: the Slack member id the owner gave in
  Settings on this Mac, in the one direct-message channel of the bot with them,
  in the workspace the bot token belongs to.  Any other user, channel, workspace
  or message kind is never read as an act.  Both tokens (bot ``xoxb-`` and app
  ``xapp-``) are held in the secret store and never in conversation, a log line
  or Evidence; a pairing records a digest of the tokens it was connected with.
* **Every request is guarded** (``_request_guard``, as on Mattermost): the
  surface is still usable (work mode, company approval) and the pairing the
  client was made for is still the current one.  The SDK reaches Slack only
  through that client, the Socket Mode URL included, so a reconnect after work
  mode was switched on is refused too.  The SDK's retry handlers are off: a
  request is never sent twice.
* **Ingestion.** An owner message becomes Work exactly once (its request key is
  the message ``ts``); an edit, a deletion or a bot message is never a new act.
  A reaction is an owner act on a message that showed an open item (OWNER-ACT-01,
  #172) and otherwise an observation; each event is taken once by its event id.
* **Buttons** (Block Kit, over the Socket Mode connection) are exact acts: a
  press by the paired owner on the message that showed an item settles that one
  item through its own path (``OwnerActs.answer_exact``), at most once, with no
  judgment.  Owner decision 2026-10-25 on #214: an exact one-use action approval
  (a payment or another step Still cannot run on pilot authority) may be given
  here, by its button or by a judged reply or reaction.
* **Delivery and work mode** are the shared direct-message rules
  (``direct_message_surface``).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import threading
import time

from .conversation_surface import PRESENCE_REACT, SurfaceCapabilities
from .direct_message_surface import EMOJI_NAMES, DirectMessageSurface
from .messages import msg
from .providers import ProviderError
from . import work_mode

LOG = logging.getLogger('personal_agent.slack')
#: The SDK's own logger: warnings only, so no request or payload dump reaches Still's log.
SDK_LOG = logging.getLogger('personal_agent.slack.sdk')
SDK_LOG.setLevel(logging.WARNING)

CONFIG_KEY = 'slack'
STATUS_KEY = 'slack_status'
BOT_SECRET, APP_SECRET = 'slack_bot_token', 'slack_app_token'
TURNS_TABLE = 'slack_turns'
EVENTS_TABLE = 'slack_events'
#: Seconds between two checks that the Socket Mode connection matches the pairing and work mode.
POLL_SECONDS = 3
REQUEST_SECONDS = 15
#: Slack shows at most 40,000 characters of a message; longer replies are sent in parts.
POST_CHARS = 39000
#: A section block holds 3,000 characters; a message at most 50 blocks.
SECTION_CHARS, MAX_SECTIONS = 2900, 45
OWNER_ID = re.compile(r'^[UW][A-Z0-9]{2,30}$')
#: Notice kinds that carry an approve / decline button pair (the item each one shows is settled exactly).
BUTTON_LABELS = {'settings_change_proposed': ('button.apply', 'button.keep'),
                 'preparation_proposed': ('button.accept', 'button.decline_schedule'),
                 'browser_approval_needed': ('button.approve', 'button.deny')}
ACTION_APPROVE, ACTION_DECLINE = 'still_approve', 'still_decline'


class SlackError(ProviderError):
    """A Slack refusal or an unreadable answer; never carries a token."""


class SlackNotSent(SlackError):
    """The request never reached Slack (connect, TLS, or the request guard): definitely not delivered."""


def token_digest(bot_token, app_token):
    """Which tokens a pairing was connected with, without keeping them in its configuration."""
    return hashlib.sha256(f'{bot_token}\n{app_token}'.encode()).hexdigest()[:16]


def web_client(token, guard):
    """The SDK's ``WebClient`` with ``guard()`` asked before every request and Slack errors made Still's.

    ``urllib`` raises ``URLError`` only while connecting or sending the request, so that is "not sent"; a
    failure after the request went out is unknown.  No retry handler: a request is never repeated.
    """
    from urllib.error import URLError
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError

    class GuardedWebClient(WebClient):
        def api_call(self, api_method, **kwargs):
            guard()
            try:
                return super().api_call(api_method, **kwargs)
            except SlackApiError as exc:
                response = getattr(exc, 'response', None)
                code = response.get('error') if response is not None else None
                status = getattr(response, 'status_code', None)
                raise SlackError(f'Slack이 요청을 거절했습니다 ({code or status}).', status=status) from None
            except URLError:
                raise SlackNotSent('Slack에 연결하지 못했습니다.') from None
            except Exception:
                raise SlackError('Slack의 응답을 받지 못했습니다.') from None

    return GuardedWebClient(token=token, timeout=REQUEST_SECONDS, retry_handlers=[], logger=SDK_LOG)


def socket_client(app_token, client):
    """The SDK's built-in Socket Mode client; every URL it opens comes from ``client`` (guarded)."""
    from slack_sdk.socket_mode.builtin import SocketModeClient
    return SocketModeClient(app_token=app_token, web_client=client, logger=SDK_LOG, concurrency=2)


def message_id(ts):
    """Still's id of a Slack message: its ``ts`` kept as text.  A bare ``ts`` looks like a number, and a store
    column of numeric affinity would turn it into a float (losing the exact value it is compared by)."""
    return f'ts:{ts}' if isinstance(ts, str) and ts else None


def slack_ts(message):
    """The Slack ``ts`` of a Still message id, or None."""
    return message[3:] if isinstance(message, str) and message.startswith('ts:') else None


def _unescape(text):
    """Slack's three escapes in message text (``&amp;`` last)."""
    return str(text or '').replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')


class SlackSurface(DirectMessageSurface):
    """The owner's Slack direct message, for one ``AgentService``."""

    name = 'slack'
    channel_prefix = 'slack:'
    #: Reactions and buttons; exact action approvals (owner decision 2026-10-25 on #214).  Slack has no
    #: typing indicator for an app's bot, and replies are not edited as live drafts.
    capabilities = SurfaceCapabilities(reactions=True, buttons=True, action_approvals=True)
    poll_seconds = POLL_SECONDS
    CONFIG_KEY, STATUS_KEY, TURNS_TABLE, SECRET_KEYS = CONFIG_KEY, STATUS_KEY, TURNS_TABLE, (BOT_SECRET, APP_SECRET)
    REQUEST_KEY_PREFIX, MESSAGES = 'sl', 'slack'
    NOT_SENT, ERROR = SlackNotSent, SlackError
    AT_THE_MAC = {'approval_needed': 'surface.answer_at_mac', 'context_approval_needed': 'surface.answer_at_mac',
                  'browser_login_needed': 'surface.answer_at_mac'}

    def __init__(self, service, web=None, socket=None):
        self.service = service
        #: ``(token, guard) -> client`` and ``(app token, client) -> socket``; tests inject fakes.
        self._web, self._socket_factory = web, socket
        self._lock = threading.Lock()
        self._button_lock = threading.Lock()
        self._socket = self._socket_generation = None
        self._next_attempt, self._failures = 0.0, 0

    # -- configuration --------------------------------------------------------------
    def _tokens(self):
        return self.service.store.secret(BOT_SECRET) or '', self.service.store.secret(APP_SECRET) or ''

    def client(self, cfg=None):
        """A Web API client of the current pairing, guarded on every request."""
        cfg = self.config() if cfg is None else cfg
        bot, app = self._tokens()
        if not (cfg.get('enabled') and bot and app):
            raise SlackError(msg('slack.not_connected'))
        if cfg.get('token_digest') != token_digest(bot, app):
            # The tokens belong to the pairing they were connected with: while a reconnect is between storing
            # the new tokens and its configuration, nothing is sent with either.
            raise SlackNotSent(msg('slack.not_connected'))
        generation = cfg.get('generation')
        return self._make_web(bot, lambda: self._request_guard(generation=generation))

    def _make_web(self, token, guard):
        factory = self._web or getattr(self.service, 'slack_web', None) or web_client
        return factory(token, guard)

    def _request_guard(self, connecting=False, generation=None):
        """Before every request: still usable (work mode, approval) and still this pairing.  Raises ``SlackNotSent``."""
        if connecting:
            refusal = self._work_mode_refusal()
            if refusal:
                raise SlackNotSent(refusal)
        elif generation is not None and self.config().get('generation') != generation:
            raise SlackNotSent(msg('slack.not_connected'))
        elif not self.usable():
            raise SlackNotSent(msg('slack.work_mode_unapproved') if work_mode.active(self.service.store)
                               else msg('slack.not_connected'))

    def usable(self, cfg=None):
        cfg = self.config() if cfg is None else cfg
        return bool(cfg.get('team_id')) and super().usable(cfg)

    # -- posting --------------------------------------------------------------------
    def _post(self, cfg, text):
        """Post ``text`` (in parts when long) to the owner's direct message; the last message's ``ts``."""
        client = self.client(cfg)
        text = str(text or '')
        parts = [text[offset:offset + POST_CHARS] for offset in range(0, len(text), POST_CHARS)] or ['']
        posted = None
        for index, part in enumerate(parts):
            try:
                posted = client.chat_postMessage(channel=cfg['channel_id'], text=part)
            except SlackNotSent:
                if index == 0:
                    raise
                # Earlier parts were delivered: the message is unknown and is never posted again from the start.
                raise SlackError('Slack에 메시지의 일부만 보냈습니다.') from None
        return message_id(self._ts(posted))

    @staticmethod
    def _ts(posted):
        """The posted message's Slack ``ts``, or None."""
        try:
            value = posted.get('ts') if posted is not None else None
        except (AttributeError, TypeError):
            value = None
        return value if isinstance(value, str) else None

    @staticmethod
    def _sections(text):
        text = str(text or '') or ' '
        return [{'type': 'section', 'text': {'type': 'plain_text', 'text': text[offset:offset + SECTION_CHARS], 'emoji': True}}
                for offset in range(0, len(text), SECTION_CHARS)]

    def _post_notice(self, cfg, text, notification, bound):
        """A notice whose item a button can settle carries an approve / decline pair; others are plain posts."""
        from .quickstart_service import MEMORY_PROMPT_KINDS
        kind = notification['kind']
        labels = BUTTON_LABELS.get(kind) or (('button.approve', 'button.deny') if kind in MEMORY_PROMPT_KINDS else None)
        sections = self._sections(text)
        if not labels or len(sections) > MAX_SECTIONS:
            return self._post(cfg, text)
        nid = notification['id']
        actions = {'type': 'actions', 'block_id': 'still_answer', 'elements': [
            {'type': 'button', 'action_id': ACTION_APPROVE, 'style': 'primary', 'value': f'{nid}:approve',
             'text': {'type': 'plain_text', 'text': msg(labels[0]), 'emoji': True}},
            {'type': 'button', 'action_id': ACTION_DECLINE, 'value': f'{nid}:decline',
             'text': {'type': 'plain_text', 'text': msg(labels[1]), 'emoji': True}}]}
        posted = self.client(cfg).chat_postMessage(channel=cfg['channel_id'], text=str(text or '')[:SECTION_CHARS],
                                                   blocks=[*sections, actions])
        return message_id(self._ts(posted))

    # -- presence -----------------------------------------------------------------------
    def present(self, action, *args):
        cfg = self.config()
        if not self.usable(cfg) or action != PRESENCE_REACT:
            return False, action != PRESENCE_REACT
        try:
            client = self.client(cfg)
            # ``(owner, message id, emoji)``: one reaction at a time, as on Telegram; '' clears it.
            mid, emoji = args[1], args[2] if len(args) > 2 else ''
            ts = slack_ts(mid)
            if ts is None:
                return False, True
            names = [EMOJI_NAMES.get(item) for item in ([emoji] if emoji else [])]
            if any(name is None for name in names):
                return False, True     # an emoji Slack has no name for here: not shown
            previous = self._shown.get(mid, [])
            for name in previous:
                if name not in names:
                    client.reactions_remove(channel=cfg['channel_id'], timestamp=ts, name=name)
            for name in names:
                if name not in previous:
                    client.reactions_add(channel=cfg['channel_id'], timestamp=ts, name=name)
            self._shown[mid] = names
            return True, False
        except SlackError as exc:
            return False, bool(exc.status and 400 <= exc.status < 500)
        except Exception:
            return False, False

    # -- the Socket Mode connection ------------------------------------------------------
    def receive(self, now=None):
        """Keep the Socket Mode connection matching the pairing: open while usable, closed otherwise.

        Messages, reactions and button presses arrive on the SDK's threads (``_on_request``); this tick only
        opens, replaces or closes the connection, with a growing pause after a failed attempt.
        """
        now = time.monotonic() if now is None else now
        with self._lock:
            cfg = self.config()
            wanted = cfg.get('generation') if self.usable(cfg) else None
            if self._socket is not None and (self._socket_generation != wanted or not wanted):
                self._close_socket()
            if not wanted or self._socket is not None or now < self._next_attempt:
                return None
            app, socket = self._tokens()[1], None
            try:
                client = self.client(cfg)
                socket = (self._socket_factory or getattr(self.service, 'slack_socket', None) or socket_client)(app, client)
                socket.socket_mode_request_listeners.append(
                    lambda sdk, request, generation=wanted: self._on_request(sdk, request, generation))
                socket.connect()
            except Exception as exc:
                self._failures += 1
                self._next_attempt = now + min(300, 2 ** min(self._failures, 8))
                self.service.store.put(STATUS_KEY, {'state': 'error', 'message': msg('slack.receive_failed')})
                LOG.warning('slack: connecting failed (%s)', type(exc).__name__)
                if socket is not None:
                    try:
                        socket.close()
                    except Exception:
                        pass
                return None
            self._socket, self._socket_generation, self._failures = socket, wanted, 0
            self.service.store.put(STATUS_KEY, {'state': 'connected'})
            LOG.info('slack: connected')
        return None

    def _close_socket(self):
        socket, self._socket, self._socket_generation = self._socket, None, None
        if socket is not None:
            try:
                socket.close()
            except Exception as exc:
                LOG.warning('slack: closing failed (%s)', type(exc).__name__)

    def _on_request(self, sdk, request, generation):
        """One Socket Mode envelope: a button press is acknowledged first (Slack waits 3 s); an event only after it was
        durably taken, so one that failed is not acknowledged and Slack delivers it again (#316 review)."""
        from slack_sdk.socket_mode.response import SocketModeResponse

        def ack():
            try:
                sdk.send_socket_mode_response(SocketModeResponse(envelope_id=request.envelope_id))
            except Exception as exc:
                LOG.warning('slack: acknowledging failed (%s)', type(exc).__name__)
        try:
            if request.type == 'interactive':
                ack()
                self.ingest_interaction(request.payload, generation)
                return
            if request.type == 'events_api':
                self.ingest_event(request.payload, generation)
            ack()
        except Exception as exc:
            LOG.warning('slack: an envelope was not taken (%s)', type(exc).__name__)

    def _first_time(self, key):
        """Whether ``key`` (an event id, a button press) is seen for the first time; Slack redelivers on a slow ack."""
        if not key:
            return False
        with self.service.store.db() as db:
            db.execute(f'DELETE FROM {EVENTS_TABLE} WHERE created<?', (time.time() - 7 * 86400,))
            return db.execute(f'INSERT OR IGNORE INTO {EVENTS_TABLE}(key,created) VALUES (?,?)',
                              (str(key)[:200], time.time())).rowcount == 1

    def _forget(self, key):
        with self.service.store.db() as db:
            db.execute(f'DELETE FROM {EVENTS_TABLE} WHERE key=?', (str(key)[:200],))

    def _current(self, generation, team):
        cfg = self.config()
        return cfg if (generation and cfg.get('generation') == generation and team == cfg.get('team_id')
                       and self.usable(cfg)) else None

    # -- ingestion ----------------------------------------------------------------------
    def ingest_event(self, payload, generation):
        """One Events API event of the pairing ``generation``: an owner message or an owner reaction."""
        payload = payload if isinstance(payload, dict) else {}
        event = payload.get('event') if isinstance(payload.get('event'), dict) else {}
        cfg = self._current(generation, payload.get('team_id'))
        if cfg is None:
            return None
        owner, kind = cfg['owner_user_id'], event.get('type')
        if kind == 'message':
            if event.get('channel') != cfg['channel_id']:
                LOG.info('slack: a message outside the direct message was ignored')
                return None
            if event.get('subtype') not in (None, 'file_share', 'thread_broadcast') or event.get('bot_id'):
                if event.get('subtype') == 'message_changed':
                    LOG.info('slack: an edited owner message is not a new request')
                return None
            if event.get('user') != owner:
                LOG.info('slack: a message from another user was ignored')
                return None
            ts = event.get('ts')
            if not isinstance(ts, str):
                return None
            thread = event.get('thread_ts')
            reply_to = message_id(thread) if isinstance(thread, str) and thread != ts else None
            try:
                # Slack's time on this Mac's clock (the offset measured at connect), as on Mattermost.
                spoke = float(ts) + float(cfg.get('clock_offset') or 0)
            except ValueError:
                spoke = None
            text = _unescape(event.get('text'))
            # A message is taken once by its request key, written with its Work in one transaction (#316 review):
            # no separate marker that could outlive a failed ingestion.
            work_id = self._ingest_owner_message(message_id(ts), text, reply_to, owner, generation, spoke=spoke)
            if work_id and self.service.is_natural_language(text):
                self.service.consider_steer(work_id, text)
            return work_id
        if kind == 'reaction_added':
            item = event.get('item') if isinstance(event.get('item'), dict) else {}
            if event.get('user') != owner or item.get('type') != 'message' or item.get('channel') != cfg['channel_id']:
                return None
            name = str(event.get('reaction') or '').split('::', 1)[0]
            reacted = message_id(item.get('ts'))
            key = 'event:' + str(payload.get('event_id') or '')
            if not (name and reacted) or not self._first_time(key):
                return None
            try:
                return self.ingest_reaction(reacted, name, owner, generation)
            except Exception:
                self._forget(key)   # not taken: Slack's redelivery is read again (#316 review)
                raise
        return None

    def ingest_interaction(self, payload, generation):
        """A Block Kit button press: only the paired owner's, on the message that showed the item, once."""
        payload = payload if isinstance(payload, dict) else {}
        team = payload.get('team') if isinstance(payload.get('team'), dict) else {}
        cfg = self._current(generation, team.get('id'))
        if cfg is None or payload.get('type') != 'block_actions':
            return None
        user = payload.get('user') if isinstance(payload.get('user'), dict) else {}
        channel = payload.get('channel') if isinstance(payload.get('channel'), dict) else {}
        container = payload.get('container') if isinstance(payload.get('container'), dict) else {}
        actions = payload.get('actions') if isinstance(payload.get('actions'), list) else []
        action = actions[0] if len(actions) == 1 and isinstance(actions[0], dict) else {}
        ts = container.get('message_ts')
        if user.get('id') != cfg['owner_user_id'] or channel.get('id') != cfg['channel_id'] or not isinstance(ts, str):
            LOG.info('slack: a button press from another user or channel was refused')
            return None
        nid, _, answer = str(action.get('value') or '').partition(':')
        expected = {ACTION_APPROVE: 'approve', ACTION_DECLINE: 'decline'}.get(action.get('action_id'))
        if not nid or answer != expected:
            return None
        if not self._first_time(f"press:{ts}:{action.get('action_ts') or ''}"):
            return None
        message = payload.get('message') if isinstance(payload.get('message'), dict) else {}
        blocks = [block for block in message.get('blocks') or () if isinstance(block, dict) and block.get('type') != 'actions']
        owner, pressed = cfg['owner_user_id'], message_id(ts)
        self.service.steer_spawn(lambda: self.answer_button(nid, answer == 'approve', pressed, owner, generation, blocks))
        return 'pressed'

    def answer_button(self, nid, approve, pressed, owner, generation, blocks=()):
        """Settle the one item the pressed notice showed, through its own path; its buttons are then removed."""
        service = self.service
        with self._button_lock:
            notification = service.store.notification(nid)
            exact = (notification and notification.get('surface') == self.name and notification['generation'] == generation
                     and notification['chat_id'] == owner and notification.get('message_id') == pressed
                     and self.owner_generation() == generation and self.usable())
            kind = None
            if exact:
                ref = ({'work_id': notification['job_id']} if notification['kind'] == 'settings_change_proposed'
                       else {'notification_id': nid})
                kind = service.owner_acts.answer_exact(self, owner, generation, pressed, ref, approve)
        if exact and kind is None:
            self.send_owner(msg('slack.button_stale'), owner=owner, generation=generation)
        if exact:
            try:
                cfg = self.config()
                self.client(cfg).chat_update(channel=cfg['channel_id'], ts=slack_ts(pressed), blocks=list(blocks) or self._sections(''),
                                             text=msg('slack.answered'))
            except Exception as exc:
                LOG.info('slack: the answered buttons were not removed (%s)', type(exc).__name__)
        LOG.info('slack: button %s kind=%s', 'approve' if approve else 'decline', kind)
        return kind

    # -- delivery -------------------------------------------------------------------------
    def cancel_pending_deliveries(self):
        if self.config().get('company_approved') is not True:
            with self._lock:
                self._close_socket()   # work mode is on: the connection closes now, not at the next tick
        return super().cancel_pending_deliveries()

    # -- Settings (the local endpoint is the only caller) ---------------------------------
    def connect(self, body):
        """Connect the bot token, the app token and the owner's member id; opens the direct message and says hello."""
        body = body if isinstance(body, dict) else {}
        stored_bot, stored_app = self._tokens()
        bot = str(body.get('bot_token') or '').strip() or stored_bot
        app = str(body.get('app_token') or '').strip() or stored_app
        owner_id = str(body.get('owner_user_id') or '').strip().upper()
        for token, prefix in ((bot, 'xoxb-'), (app, 'xapp-')):
            if not token.startswith(prefix) or len(token) > 300 or any(ch.isspace() for ch in token):
                raise ValueError(msg('slack.invalid_token'))
        if not OWNER_ID.match(owner_id):
            raise ValueError(msg('slack.invalid_owner'))
        refusal = self._work_mode_refusal()
        if refusal:
            raise ValueError(refusal)
        client = self._make_web(bot, lambda: self._request_guard(connecting=True))
        try:
            auth = client.auth_test()
            team_id, bot_user = auth.get('team_id'), auth.get('user_id')
            if not team_id or not bot_user or owner_id == bot_user:
                raise ValueError(msg('slack.invalid_owner'))
            channel = (client.conversations_open(users=owner_id).get('channel') or {}).get('id')
            if not isinstance(channel, str) or not channel.startswith('D'):
                raise ValueError(msg('slack.invalid_owner'))
            # The app token is checked without opening the socket: a URL is issued only for a valid one.
            self._make_web(app, lambda: self._request_guard(connecting=True)).apps_connections_open(app_token=app)
        except SlackNotSent as exc:
            raise ValueError(str(exc) or msg('slack.connect_failed')) from exc
        except SlackError as exc:
            raise ValueError(msg('slack.connect_failed')) from exc
        # Slack's clock read on this Mac's: the hello message's own ts (as on Mattermost).
        offset = 0.0
        try:
            sent = time.time()
            hello = self._ts(client.chat_postMessage(channel=channel, text=msg('slack.connected_hello')))
            if hello:
                offset = (sent + time.time()) / 2 - float(hello)
        except (SlackError, ValueError):
            LOG.warning('slack: hello not delivered')
        cfg = {'enabled': True, 'team_id': team_id, 'team': str(auth.get('team') or ''), 'bot_user_id': bot_user,
               'owner_user_id': owner_id, 'channel_id': channel, 'generation': secrets.token_hex(6),
               'company_approved': self.config().get('company_approved') is True, 'connected_at': time.time(),
               'token_digest': token_digest(bot, app), 'clock_offset': offset}
        self.service.store.secret(BOT_SECRET, bot)
        self.service.store.secret(APP_SECRET, app)
        self.service.store.put(CONFIG_KEY, cfg)
        self._next_attempt, self._failures = 0.0, 0
        return self.status()

    def disconnect(self):
        with self._lock:
            self._close_socket()
        return super().disconnect()

    def status(self):
        cfg = self.config()
        live = self.service.store.config(STATUS_KEY, {}) or {}
        return {'connected': bool(cfg.get('enabled')), 'team': cfg.get('team', ''),
                'owner_user_id': cfg.get('owner_user_id', ''), 'company_approved': cfg.get('company_approved') is True,
                'usable': self.usable(cfg), 'state': live.get('state') if cfg.get('enabled') else 'disconnected',
                'message': live.get('message', '')}

    @staticmethod
    def manifest():
        """The reviewed app manifest (#214): DM conversation scopes only, Socket Mode, no request URL."""
        return json.loads(json.dumps(MANIFEST))


#: The app the owner creates in their workspace (Settings shows it; QUICKSTART has the steps).
MANIFEST = {
    'display_information': {'name': 'Still'},
    'features': {'bot_user': {'display_name': 'Still', 'always_online': True},
                 'app_home': {'messages_tab_enabled': True, 'messages_tab_read_only_enabled': False}},
    'oauth_config': {'scopes': {'bot': ['chat:write', 'im:history', 'im:write', 'reactions:read', 'reactions:write']}},
    'settings': {'event_subscriptions': {'bot_events': ['message.im', 'reaction_added']},
                 'interactivity': {'is_enabled': True}, 'org_deploy_enabled': False, 'socket_mode_enabled': True,
                 'token_rotation_enabled': False},
}
