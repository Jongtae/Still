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
"""
from __future__ import annotations

import itertools
import logging
import threading

from .conversation_surface import SurfaceCapabilities
from .direct_message_surface import DirectMessageSurface
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
    #: Printed text only: no reactions, typing, drafts, buttons or exact action approvals (those are on the Still screen).
    capabilities = SurfaceCapabilities()
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
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        # ``terminal_turns`` is created with the store (``QuickStore``), as the other surfaces' turns tables.

    # -- attachment (the terminal loop is the only caller) ---------------------------------
    def attach(self, writer):
        """Start a terminal conversation that prints with ``writer``; its pairing generation."""
        with self.service.lock:
            previous = self.config().get('generation')
            generation = str(int(previous) + 1 if isinstance(previous, str) and previous.isdigit() else 1)
            # Company-approved: the terminal is this Mac itself, as the local screen in work mode.
            self.service.store.put(CONFIG_KEY, {'enabled': True, 'owner_user_id': OWNER, 'channel_id': 'tty',
                                                'generation': generation, 'company_approved': True})
            self._writer = writer
        return generation

    def detach(self):
        with self.service.lock:
            self._writer = None
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

    def _post(self, cfg, text):
        with self._lock:
            writer = self._writer
            if writer is None:
                raise TerminalNotSent('no terminal conversation is attached')
            writer(text)
        return f'{cfg.get("generation")}-out-{next(self._ids)}'
