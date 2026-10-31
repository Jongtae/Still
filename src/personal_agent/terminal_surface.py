"""The terminal where the owner started Still, as a conversation surface (#438).

Right after the first setup, an interactive ``agentos start`` keeps the
terminal open as a chat: what the owner types becomes Work through the same
ingestion as Mattermost and Slack (``DirectMessageSurface``), and the answer is
printed back in the terminal. Nothing leaves this Mac to reach it.

The terminal is this Mac's own console, so it is the same owner scope as the
local web screen: its Work uses the ``local-owner`` connector identity and it
adds no paired source identity (a restart does not reset current context).
It is attached only while a terminal conversation runs in this process; a
reply that finishes after the terminal closed is not shown in a later one.

An input only the owner may type (a Telegram bot token) is asked for by a
confirmed settings draft (``request_input``); the terminal loop then reads it
with a hidden prompt and hands it straight to the service. It never becomes a
message, so it never reaches a model, a log or Evidence.
"""
from __future__ import annotations

import itertools
import logging
import threading

from .conversation_surface import SurfaceCapabilities
from .direct_message_surface import DirectMessageSurface
from .messages import msg
from .providers import ProviderError

LOG = logging.getLogger(__name__)
CONFIG_KEY = 'terminal'
OWNER = 'owner'


class TerminalNotSent(ProviderError):
    """Nothing was printed: no terminal conversation is attached."""


class TerminalSurface(DirectMessageSurface):
    """The owner's terminal on this Mac, for one ``AgentService``."""

    name = 'terminal'
    channel_prefix = 'terminal:'
    #: Printed text and an arrow-key choice in place of a button pair; no reactions, typing, drafts or exact
    #: action approvals (those are on the Still screen).
    capabilities = SurfaceCapabilities(buttons=True)
    #: Notice kinds answered with a choice, as Slack's button pair: the item is settled exactly, with no judgment.
    CHOICES = {'settings_change_proposed': ('button.apply', 'button.keep'),
               'preparation_proposed': ('button.accept', 'button.decline_schedule')}
    #: The inputs a confirmed settings draft may ask the terminal to read (``settings_orchestrator`` connect_chat).
    INPUTS = frozenset({'telegram'})
    #: Owner messages are pushed by the terminal loop (``say``), never polled.
    poll_seconds = None
    CONFIG_KEY, STATUS_KEY, TURNS_TABLE, SECRET_KEYS = CONFIG_KEY, 'terminal_status', 'terminal_turns', ()
    REQUEST_KEY_PREFIX, MESSAGES = 'tty', 'terminal'
    NOT_SENT, ERROR = TerminalNotSent, ProviderError
    AT_THE_MAC = {'browser_approval_needed': 'surface.answer_at_mac', 'approval_needed': 'surface.answer_at_mac',
                  'context_approval_needed': 'surface.answer_at_mac', 'browser_login_needed': 'surface.answer_at_mac'}

    def __init__(self, service):
        self.service = service
        self._writer = None
        self._interrupt = None
        self._requests = []
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        # ``terminal_turns`` is created with the store (``QuickStore``), as the other surfaces' turns tables.

    # -- attachment (the terminal loop is the only caller) ---------------------------------
    def attach(self, writer, interrupt=None):
        """Start a terminal conversation that prints with ``writer``; its pairing generation.

        ``interrupt`` ends the line being typed, so a requested input is asked for at once.
        """
        with self.service.lock:
            previous = self.config().get('generation')
            generation = str(int(previous) + 1 if isinstance(previous, str) and previous.isdigit() else 1)
            # Company-approved: the terminal is this Mac itself, as the local screen in work mode.
            self.service.store.put(CONFIG_KEY, {'enabled': True, 'owner_user_id': OWNER, 'channel_id': 'tty',
                                                'generation': generation, 'company_approved': True})
            self._writer, self._interrupt, self._requests = writer, interrupt, []
        return generation

    def detach(self):
        with self.service.lock:
            self._writer, self._interrupt, self._requests = None, None, []
            self.service.store.put(CONFIG_KEY, {**self.config(), 'enabled': False})

    def usable(self, cfg=None):
        return self._writer is not None and super().usable(cfg)

    def say(self, text):
        """One line the owner typed, as Work in the current conversation; the Work id or None."""
        cfg = self.config()
        if not self.usable(cfg):
            return None
        work_id = self._ingest_owner_message(f'{cfg["generation"]}-{next(self._ids)}', text, None, OWNER,
                                             cfg['generation'])
        if work_id and self.service.is_natural_language(text):
            self.service.consider_steer(work_id, text)
        return work_id

    def in_conversation(self, channel):
        """Whether ``channel`` is the terminal conversation attached now."""
        cfg = self.config()
        return self.usable(cfg) and channel == f'{self.channel_prefix}{cfg["generation"]}'

    def request_input(self, kind):
        """Ask the attached terminal to read one owner-only input (``INPUTS``); False when none is attached."""
        return kind in self.INPUTS and self._request(kind)

    def _request(self, item):
        with self.service.lock:
            if not self.usable():
                return False
            if item not in self._requests:
                self._requests.append(item)
            interrupt = self._interrupt
        if interrupt is not None:
            interrupt()
        return True

    def take_request(self):
        """The next request: an input kind (``INPUTS``) or a notice's choice (a dict), or None."""
        with self.service.lock:
            return self._requests.pop(0) if self._requests else None

    def waiting(self):
        """Whether a Work of this conversation is still to be answered here (for the terminal's status line)."""
        cfg = self.config()
        if not self.usable(cfg):
            return False
        with self.service.store.db() as db:
            row = db.execute("SELECT 1 FROM jobs WHERE channel=? AND (status IN ('queued','running') "
                             "OR delivery IN ('pending','sending')) LIMIT 1",
                             (f'{self.channel_prefix}{cfg["generation"]}',)).fetchone()
        return row is not None

    # -- the ConversationSurface boundary ---------------------------------------------------
    def connector_owner(self, job):
        return 'local-owner'

    def owns_connector_owner(self, owner_id):
        return False

    def paired_connector_owner(self, enabled_only=True):
        return None

    def receive(self, now=None):
        return []

    def present(self, action, *args):
        return False, False

    def _post_notice(self, cfg, text, notification, bound):
        labels = self.CHOICES.get(notification['kind'])
        if not labels:
            return self._post(cfg, text)
        # The notice is shown as part of its own choice (review on #456): a menu always names the item it
        # settles, however many notices arrive while another menu is open.
        with self._lock:
            if self._writer is None:
                raise TerminalNotSent('no terminal conversation is attached')
            post_id = f'{cfg.get("generation")}-out-{next(self._ids)}'
        ref = ({'work_id': notification['job_id']} if notification['kind'] == 'settings_change_proposed'
               else {'notification_id': notification['id']})
        if not self._request({'labels': labels, 'ref': ref, 'message': post_id, 'generation': cfg['generation'],
                              'text': str(text or '')}):
            raise TerminalNotSent('no terminal conversation is attached')
        return post_id

    def answer_choice(self, choice, approve):
        """The owner's choice on a notice, settled exactly through the button path (``owner_acts.answer_exact``)."""
        kind = self.service.owner_acts.answer_exact(self, OWNER, choice['generation'], choice['message'], choice['ref'],
                                                    approve)
        if kind is None:
            self.send_owner(msg('slack.button_stale'), owner=OWNER, generation=choice['generation'])
        return kind

    def _post(self, cfg, text):
        with self._lock:
            writer = self._writer
            if writer is None:
                raise TerminalNotSent('no terminal conversation is attached')
            writer(text)
        return f'{cfg.get("generation")}-out-{next(self._ids)}'
