"""The channel-neutral owner conversation surface (#212, a #21 follow-up).

A *conversation surface* is where the owner talks to Still away from the local
web page: today Telegram, later Mattermost (#213) and Slack (#214).  This module
names what such a surface does for Still, so a second one is built against a
Still-owned boundary instead of against ``TelegramSurface``:

* **pairing** - exactly one owner identity per surface (``pairing``,
  ``owns``): a Work belongs to the surface's paired conversation, nothing else
  is answered;
* **ingestion** - ``receive`` reads the surface's updates and turns one owner
  message into Work;
* **delivery** - ``deliver_reply`` sends the terminal reply, ``queue_notification``
  / ``deliver_notification`` the queued notices and approval prompts, and
  ``deliver_conversation_notice`` one conversation notice;
* **presence** - ``source_message``, ``present`` and ``show_draft`` carry the
  best-effort presence the core decides on (reactions, typing, the draft);
* **owner acts** - approvals, declines, reactions and Stop are settled through
  the existing OWNER-ACT-01 judgment (``owner_acts``, #172); a surface only
  reports the act and the message it names.

What a surface can show differs, so the optional parts are declared as
``SurfaceCapabilities`` and core Presence branches on those, never on a
surface's name.  A capability is presentation only: declaring or lacking one
never grants, widens or removes authority, and a presence call that fails
never changes Work, delivery or Evidence.

This is a narrow base class, not a plugin framework: ``AgentService`` stays the
composition root and builds each surface itself (``AgentService.surfaces``).
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass


#: Presence actions ``ConversationSurface.present`` carries (the draft has its own ``show_draft``).
PRESENCE_REACT = 'react'
PRESENCE_TYPING = 'typing'


@dataclass(frozen=True)
class SurfaceCapabilities:
    """The optional presentation one surface supports; all off by default.

    * ``reactions`` - an emoji reaction on the owner's message (received,
      progress and closing reactions, #581/#835/#858);
    * ``typing`` - a refreshed typing indicator while the Work runs;
    * ``live_drafts`` - an editable draft of the reply in flight (the dots and
      the observed step, #718/#839);
    * ``buttons`` - tappable controls on a sent message, answered on the
      surface (approve, decline, retry);
    * ``native_stop`` - the client's own Stop on the draft reaches Still.
    """
    reactions: bool = False
    typing: bool = False
    live_drafts: bool = False
    buttons: bool = False
    native_stop: bool = False


@dataclass(frozen=True)
class SurfacePairing:
    """The one paired owner conversation of a surface: its ``jobs.channel`` value and owner identity."""
    channel: str
    owner: object


class ConversationSurface(ABC):
    """The Still-owned boundary every owner conversation surface implements.

    A surface holds no state of its own: it reads and writes through the
    owning service, so authority, approvals, budgets and database semantics are
    the service's.
    """

    #: Short stable name of the surface (log and prompt wording, never a branch).
    name = ''
    capabilities = SurfaceCapabilities()
    #: #213: the prefix of every ``jobs.channel`` value this surface creates (any pairing generation).
    #: A surface claims, delivers and cancels only Work on its own channels, never another surface's.
    channel_prefix = ''

    def claims_channel(self, channel):
        """Whether ``channel`` is one of this surface's (current or earlier pairing)."""
        return bool(self.channel_prefix) and isinstance(channel, str) and channel.startswith(self.channel_prefix)

    # -- pairing ---------------------------------------------------------------
    @abstractmethod
    def pairing(self):
        """The current paired owner conversation (``SurfacePairing``), or None while unpaired."""

    @abstractmethod
    def owns(self, job):
        """Whether ``job`` came from this surface's current paired owner conversation."""

    # -- owner identity (#213) ------------------------------------------------------
    def claims(self, job):
        """Whether ``job`` came from this surface at all, in its current pairing or an earlier one.

        A claimed Work keeps this surface's connector identity and is held to its
        pairing by every guard, even after the pairing changed.
        """
        return bool(job) and self.claims_channel(job.get('channel')) and job.get('chat_id') is not None

    def connector_owner(self, job):
        """The durable connector owner identity of a Work this surface claims, stable across pairings."""
        return f"{self.name}:{job.get('chat_id')}"

    def owns_connector_owner(self, owner_id):
        """Whether ``owner_id`` (a ``connector_owner`` value) names this surface."""
        return isinstance(owner_id, str) and owner_id.startswith(f'{self.name}:')

    @abstractmethod
    def reply_message(self, work_id, owner):
        """The id of Still's reply to ``work_id`` in ``owner``'s conversation, or None."""

    @abstractmethod
    def owner_generation(self):
        """The current pairing's generation (a handoff is bound to it), or '' while unpaired."""

    @abstractmethod
    def paired_owner(self, owner):
        """Whether the surface is paired now and ``owner`` (an identity in ``SurfacePairing.owner`` form) is its owner."""

    @abstractmethod
    def send_owner(self, text, reply_to=None):
        """Best effort: ``text`` to the paired owner now; True when it was sent.  Never raises."""

    # -- ingestion -------------------------------------------------------------
    @abstractmethod
    def receive(self, now=None):
        """One receive pass: owner messages become Work, owner acts go to their settle paths."""

    # -- delivery --------------------------------------------------------------
    @abstractmethod
    def deliver_reply(self):
        """Send one pending terminal reply; an uncertain send is never replayed."""

    @abstractmethod
    def queue_notification(self, job, kind, fingerprint=None):
        """Queue one notice or approval prompt for ``job``'s owner conversation."""

    @abstractmethod
    def deliver_notification(self):
        """Send one queued notice or approval prompt; True when one was handled."""

    @abstractmethod
    def cancel_pending_deliveries(self):
        """Cancel this surface's pending replies and queued notices (work mode goes on); returns the counts."""

    @abstractmethod
    def deliver_conversation_notice(self, text):
        """Best effort: one conversation notice to the paired owner."""

    # -- presence (best effort; only what ``capabilities`` declares is asked) -------
    @abstractmethod
    def source_message(self, work_id):
        """The owner's message a Work answers on this surface (the reaction target), or None."""

    @abstractmethod
    def present(self, action, *args):
        """One best-effort presence call (``PRESENCE_REACT``, ``PRESENCE_TYPING``).

        Returns ``(sent, refused)``; ``refused`` only when the surface answered
        that it will not show it (a timeout or lost connection is not a
        refusal).  Never raises.
        """

    @abstractmethod
    def show_draft(self, job, state, text, note=None):
        """Show or refresh the draft of a running Work; True when it was shown."""
