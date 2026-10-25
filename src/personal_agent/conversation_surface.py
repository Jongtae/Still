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
import time
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
    * ``native_stop`` - the client's own Stop on the draft reaches Still;
    * ``action_approvals`` - the owner may answer an exact one-use action approval
      (a payment or another step Still cannot run on pilot authority) here.  A
      surface without it never settles one: its prompt says to approve at the Mac
      (#213, owner decision 2026-10-25).  Unlike the others this one narrows what
      an act on the surface can settle; it never widens anything.
    """
    reactions: bool = False
    typing: bool = False
    live_drafts: bool = False
    buttons: bool = False
    native_stop: bool = False
    action_approvals: bool = False


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
    #: Seconds between two ``receive`` passes on the service's shared surface poll, or None when the
    #: surface runs its own receive loop (Telegram's long poll).
    poll_seconds = None

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
    def reply_to(self, work_id):
        """The message the owner's message for ``work_id`` replied to (its owner-act target, #172), or None."""

    @abstractmethod
    def reply_message(self, work_id, owner):
        """The id of Still's reply to ``work_id`` in ``owner``'s conversation, or None."""

    @abstractmethod
    def paired_connector_owner(self, enabled_only=True):
        """The connector owner identity of the paired owner (whatever the work mode), or None.

        ``enabled_only`` False also names an owner whose pairing is switched off but still recorded.
        """

    @abstractmethod
    def owner_generation(self):
        """The current pairing's generation (a handoff is bound to it), or '' while unpaired."""

    @abstractmethod
    def paired_owner(self, owner):
        """Whether the surface is paired now and ``owner`` (an identity in ``SurfacePairing.owner`` form) is its owner."""

    @abstractmethod
    def send_owner(self, text, reply_to=None, owner=None, generation=None):
        """Best effort: ``text`` to the paired owner now; True when it was sent.  Never raises.

        ``owner`` / ``generation`` (#299 review): the conversation the text answers.  When given, it
        is sent only while that is still the current pairing, never retargeted to a newly paired owner.
        """

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


# -- notices (#213) ------------------------------------------------------------------
#
# Every surface shows the same queued notices.  Whether a notice is still to be shown,
# what it is bound to and what it says are decided here once; each surface only adds
# what it can (Telegram's buttons) and sends.

def notice_binding(service, notification):
    """What a queued notice is bound to now (``{}`` when nothing), or None when it is no longer to be shown."""
    from . import preparations as prep
    from .quickstart_service import MEMORY_PROMPT_KINDS, MEMORY_SAVED_KINDS, WORKING_NOTES_KIND, prepared_ids
    kind = notification['kind']
    if kind == 'approval_needed':
        # #594 item 11: sharing was approved (e.g. in Settings) after this prompt was queued.
        return {} if service.document_boundary()['requires_approval'] else None
    if kind == 'browser_approval_needed':
        pending = service._browser_request(notification.get('job_id'))
        if not pending or pending.get('state') != 'requested' or pending.get('offer_digest') != notification.get('fingerprint'):
            return None
        return {}
    if kind == 'browser_login_needed':
        # #709: only while this Work's login is still offered with this nonce.
        row = service._browser_login(notification.get('job_id'))
        if not row or row.get('state') != 'offered' or row.get('nonce') != notification.get('fingerprint'):
            return None
        return {}
    if kind == 'preparation_scheduled':
        text = service.scheduled_notice_text(prepared_ids(notification))
        return None if text is None else {'scheduled_text': text}
    if kind == 'preparation_proposed':
        # #659: the exact proposals of one Work; changed since -> not offered.
        proposals, remaining = service.offered_proposals(notification)
        return {'proposals': proposals, 'remaining': remaining} if proposals else None
    if kind in MEMORY_PROMPT_KINDS:
        # #818: bound now to the queued candidates still pending; none left -> not offered.
        memory_prompt = service.bind_memory_prompt(notification, time.time())
        return {'memory_prompt': memory_prompt} if memory_prompt else None
    if kind in MEMORY_SAVED_KINDS:
        # #918: bound now to the Work's current saved facts; none left -> nothing to tell.
        memory_saved = service.bind_memory_saved(notification, time.time())
        return {'memory_saved': memory_saved} if memory_saved else None
    if kind == WORKING_NOTES_KIND:
        # #19: told only while its revision is still the current notes.
        notes_text = service.working_notes_notice_text(notification)
        return None if notes_text is None else {'notes_text': notes_text}
    if kind == 'settings_change_proposed':
        # #814: the exact drafts of one Work; changed or expired since -> not offered.
        drafts = service.offered_settings_drafts(notification)
        return {'settings_drafts': drafts} if drafts else None
    if kind == prep.NOTIFY_KIND:
        # #719: one watch run the owner needs.
        watch_text = service.watch_notification_text(notification)
        return None if watch_text is None else {'watch_text': watch_text}
    return {}


def notice_text(service, notification, bound):
    """The text a notice shows, from what ``notice_binding`` bound."""
    from . import preparations as prep
    from .quickstart_service import LOCAL_DOCUMENT_APPROVAL_TEXT, MEMORY_PROMPT_KINDS, MEMORY_SAVED_KINDS, WORKING_NOTES_KIND
    kind = notification['kind']
    if kind == 'preparation_proposed':
        return prep.proposal_text(bound['proposals'], bound['remaining'])
    if kind == 'preparation_scheduled':
        return bound['scheduled_text']
    if kind in MEMORY_PROMPT_KINDS:
        return service.memory_prompt_text(notification['job_id'], bound['memory_prompt'])
    if kind in MEMORY_SAVED_KINDS:
        return service.memory_saved_text(bound['memory_saved'])
    if kind == WORKING_NOTES_KIND:
        return bound['notes_text']
    if kind == 'settings_change_proposed':
        return service.settings_orchestrator.confirmation_text(bound['settings_drafts'])
    if kind == prep.NOTIFY_KIND:
        return bound['watch_text']
    if kind == 'approval_needed' and service.document_resume_eligible(notification.get('job_id')):
        return LOCAL_DOCUMENT_APPROVAL_TEXT
    if kind == 'browser_approval_needed':
        return service.browser_step_prompt(notification.get('job_id'))
    if kind == 'browser_login_needed':
        return service.browser_login_prompt(notification.get('job_id'))
    return service.notification_text(kind)
