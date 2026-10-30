"""OWNER-ACT-01 (#172): one judgment reads each owner act against everything pending.

Owner decision 2026-10-21, revising #22 option A: approval is no longer a
literal word list, a fixed precedence chain or a first-message-only rule.  An
owner act - a typed message, a reaction, and the message it replies to or
reacts on - is read by one judgment (``ConversationJudgments.owner_act_answer``)
together with every item still waiting for the owner in this conversation.
Buttons stay exact acts and are not read here (``answer_exact`` settles a
button's one item without a judgment, #214).

This module is the thin glue around that judgment:

* ``open_items`` collects the open items for one conversation and identity,
  each ``{kind, id, shown_text, shown_at, message_id, expires_at}`` plus the
  reference its own settle path needs.  Every source and every check is the
  existing one: the pending calendar drafts (``CalendarConversation``), the
  pending settings drafts (``SettingsOrchestrator.pending_or_renewable``),
  every open step-approval prompt, the open saved-memory notice, open memory
  proposals and preparation proposals (their sent Telegram messages, bound as
  their buttons are).
* ``read`` narrows the candidates to the item(s) shown on the message a reply
  or reaction names (a hard binding, in code), asks the one judgment, and
  returns an answer only when it is a judged ``approve`` or ``decline`` of one
  item that is still open.  Unclear, unavailable, low-confidence, ``change``
  and ``unrelated`` settle nothing and the act is an ordinary turn.
* ``settle`` runs the chosen item's existing path, unchanged in authority:
  calendar compare-and-clear then the connector's digest-bound approve ->
  execute; settings ``settle_pending``; ``_decide_browser_step`` with the
  offer digest; the saved-memory undo; the memory-proposal and preparation
  button paths.  Nothing pending is deleted on a miss.

What stays code (enforcement): which conversation and identity an item
belongs to, the paired private chat and current bot generation, that an act
cannot answer an item shown after it, expiry, digests, compare-and-set,
``owner_typed``, and that replayed and photo turns never answer (the caller).
No owner wording is matched here.  Evidence class of the tests: model-free,
with a scripted judgment; no live model was asked.
"""
import json
import logging
import time

from .calendar import CALENDAR_WRITE_CONNECTOR_ID, CalendarError
from .calendar_conversation import CHANGED_UNDER_US
from .conversation_handoff import ConnectorHandoff
from .messages import msg
from . import preparations as prep

LOG = logging.getLogger('personal_agent.service')

#: The most items one judgment is asked about (newest first).
ITEM_LIMIT = 6
#: How much of each item's shown text the judgment reads.
SHOWN_CHARS = 700

KIND_CALENDAR = 'calendar-drafts'
KIND_SETTINGS = 'settings-drafts'
KIND_STEP = 'action-approval'
KIND_MEMORY_SAVED = 'memory-saved'
KIND_MEMORY_PROPOSAL = 'memory-proposal'
KIND_PREPARATION = 'preparation-proposal'

#: Items that stay open for many turns (days, or until answered); see ``OwnerActs._within_cost_bound``.
LONG_LIVED = frozenset({KIND_MEMORY_SAVED, KIND_MEMORY_PROPOSAL, KIND_PREPARATION})

#: What an answer does to an item of each kind, told to the judgment in generic words.
ANSWERS = {
    KIND_CALENDAR: 'approve applies the calendar changes exactly as shown; decline drops them',
    KIND_SETTINGS: 'approve applies the settings change as shown; decline keeps the current setting',
    KIND_STEP: 'approve lets the waiting action run once, exactly as shown; decline stops it',
    KIND_MEMORY_SAVED: 'decline removes what the assistant said it remembered; approve keeps it',
    KIND_MEMORY_PROPOSAL: 'approve remembers the facts shown; decline does not remember them',
    KIND_PREPARATION: 'approve schedules what was proposed; decline does not',
}


def _bounded(text):
    text = str(text or '')
    return text if len(text) <= SHOWN_CHARS else text[:SHOWN_CHARS - 1] + '…'


def _ago(now, moment):
    if not moment:
        return 'unknown'
    minutes = max(0, int((now - float(moment)) // 60))
    return 'just now' if minutes < 1 else f'{minutes} minutes ago'


def _until(now, moment):
    if not moment:
        return 'no expiry'
    minutes = int((float(moment) - now) // 60)
    return 'expired' if minutes < 0 else f'in {minutes} minutes'


class OwnerActs:
    """The open owner-decision items of one conversation, the one judgment over an act, and its settlement."""

    def __init__(self, service):
        self.service = service

    # -- collect -------------------------------------------------------------------
    def _conversation(self, job):
        """``(surface, owner, generation)`` when ``job`` is a surface's current paired conversation, else None (#213)."""
        surface = self.service.surface_of(job)
        if surface is None:
            return None
        return surface, job.get('chat_id'), surface.owner_generation()

    def open_items(self, job, before=None, target=None):
        """Every item still waiting for the owner in ``job``'s conversation and identity, newest first.

        ``before``: an act made at that time cannot answer an item shown at or after it.
        ``target``: only the item(s) shown on that message, chosen before the
        ``ITEM_LIMIT`` cost cap, so a reply or reaction on an older message
        still reaches what it names (#188).
        """
        service = self.service
        now = time.time()
        conversation_of = self._conversation(job)
        items = []
        connector_owner = service.issuer_owner_id(job)
        conversation = service.calendar_conversation
        row = conversation.pending(connector_owner)
        if row:
            work_id = row.get('work_id')
            message_id = (conversation_of[0].reply_message(work_id, conversation_of[1])
                          if conversation_of and isinstance(work_id, str) else None)
            items.append({'kind': KIND_CALENDAR, 'shown_text': conversation.shown_text(connector_owner, row),
                          'shown_at': float(row.get('at') or 0), 'expires_at': float(row.get('expires') or 0),
                          'message_id': message_id,
                          'ref': {'owner': connector_owner, 'drafts': conversation.shown_drafts(row),
                                  'work_id': work_id}})
        settings = service.settings_orchestrator
        owner, channel = service.settings_owner(job), job.get('channel')
        groups = {}
        for draft in settings.pending_or_renewable(owner, channel):
            groups.setdefault(draft.get('work_id') or draft['id'], []).append(draft)
        for work_id, rows in groups.items():
            deadline = max(float(draft.get('expires_at') or 0) for draft in rows)
            if deadline <= now:
                deadline += settings.RENEW_GRACE_SECONDS
            notice = service.store.notification_of(work_id, 'settings_change_proposed') if conversation_of else None
            bound = (notice and notice.get('surface', 'telegram') == conversation_of[0].name
                     and notice['chat_id'] == conversation_of[1] and notice['generation'] == conversation_of[2]
                     and notice.get('message_id') is not None)
            items.append({'kind': KIND_SETTINGS, 'shown_text': settings.confirmation_text(rows),
                          'shown_at': min(float(draft.get('created_at') or 0) for draft in rows),
                          'expires_at': deadline, 'message_id': notice['message_id'] if bound else None,
                          'ref': {'owner': owner, 'channel': channel, 'rows': rows,
                                  'work_id': work_id if any(draft.get('work_id') for draft in rows) else None}})
        if conversation_of:
            items.extend(self._notification_items(*conversation_of, now, target=target))
            if not conversation_of[0].capabilities.action_approvals:
                # #213: an exact action approval is never settled on this surface (approve at the Mac).
                items = [item for item in items if item['kind'] != KIND_STEP]
        if before is not None:
            # Telegram dates an owner message in whole seconds, so an item shown later within that same
            # second is not treated as unseen; one shown in a later second is.
            items = [item for item in items if item['shown_at'] and int(item['shown_at']) <= int(float(before))]
        if target is not None:
            items = [item for item in items if item['message_id'] == target]
        items.sort(key=lambda item: item['shown_at'], reverse=True)
        items = items[:ITEM_LIMIT]
        for index, item in enumerate(items, 1):
            item['id'] = f'item-{index}'
        return items

    def _notification_items(self, surface, chat, generation, now, target=None):
        """The open prompts and notices sent to this chat in this bot generation (#1472, #996, #818, #659).

        With ``target``, only those sent as that message, however many newer ones were sent since (#188).
        """
        from .quickstart_service import (MEMORY_CANDIDATES_TTL_SECONDS, MEMORY_PROMPT_KINDS, MEMORY_SAVED_KINDS,
                                         MEMORY_SAVED_TTL_SECONDS)
        service = self.service
        kinds = ('browser_approval_needed', 'preparation_proposed', *MEMORY_SAVED_KINDS, *MEMORY_PROMPT_KINDS)
        items = []
        for notification in service.store.telegram.sent_of_kinds(chat, kinds, limit=ITEM_LIMIT * 3, message_id=target,
                                                                surface=surface.name):
            if notification['generation'] != generation:
                continue
            kind, base = notification['kind'], {'message_id': notification.get('message_id'),
                                                'ref': {'notification_id': notification['id'],
                                                        'work_id': notification['job_id']}}
            # #188: an item is shown when its send completed, not when it was requested, bound or
            # queued; a slow send or a prompt queued across a restart reaches the owner later.  A
            # prompt sent before ``sent_at`` existed keeps its earlier time.
            sent_at = notification.get('sent_at')
            if kind == 'browser_approval_needed':
                current = service.open_step_notification(notification)
                if not current:
                    continue
                requested = float(current[1].get('requested_at') or 0)
                items.append({**base, 'kind': KIND_STEP, 'shown_text': service.browser_step_prompt(notification['job_id']),
                              'shown_at': max(requested, float(sent_at or 0)), 'expires_at': requested + 600})
            elif kind in MEMORY_SAVED_KINDS:
                current = service.open_memory_notice(notification)
                if not current:
                    continue
                sent = float(current[1]['sent'])
                items.append({**base, 'kind': KIND_MEMORY_SAVED, 'shown_text': service.memory_saved_text(current[1]),
                              'shown_at': max(sent, float(sent_at or 0)), 'expires_at': sent + MEMORY_SAVED_TTL_SECONDS})
            elif kind in MEMORY_PROMPT_KINDS:
                binding = service.memory_binding(notification)
                if not binding or 'sent' not in binding or not service.memory_open(binding):
                    continue
                sent = float(binding['sent'])
                if now - sent > MEMORY_CANDIDATES_TTL_SECONDS:
                    continue
                items.append({**base, 'kind': KIND_MEMORY_PROPOSAL,
                              'shown_text': service.memory_prompt_text(notification['job_id'], binding),
                              'shown_at': max(sent, float(sent_at or 0)), 'expires_at': sent + MEMORY_CANDIDATES_TTL_SECONDS})
            else:
                proposals, remaining = service.offered_proposals(notification)
                if not proposals:
                    continue
                items.append({**base, 'kind': KIND_PREPARATION, 'shown_text': prep.proposal_text(proposals, remaining),
                              'shown_at': max(float(notification['created']), float(sent_at or 0)), 'expires_at': None})
        return items

    def _within_cost_bound(self, job, items):
        """The items an unbound typed act is judged against: a cost bound, not an interpretation rule.

        A saved-memory notice stays open for days and a proposal for a day or
        more, so one is almost always open; judging every typed message against
        it would add a Judgment AI call to nearly every turn.  An unbound typed
        act (no reply-to) therefore includes such a long-lived item only when it
        was shown after the owner's previous typed message in this conversation
        - the owner has not spoken since.  Short-lived items (calendar drafts,
        step approvals, settings drafts) are always included while open.  A
        long-lived item left out stays open and answerable by a reaction, a
        reply-to its message or its button.
        """
        if not any(item['kind'] in LONG_LIVED for item in items):
            return items
        with self.service.store.db() as db:
            row = db.execute('SELECT created FROM jobs WHERE channel=? AND chat_id IS ? AND owner_typed=1 '
                             'AND id IS NOT ? AND created<? ORDER BY created DESC LIMIT 1',
                             (job.get('channel'), job.get('chat_id'), job.get('id'),
                              float(job.get('created') or time.time()))).fetchone()
        spoke = float(row['created']) if row else None
        return [item for item in items
                if item['kind'] not in LONG_LIVED or spoke is None or float(item['shown_at'] or 0) > spoke]

    # -- judge ---------------------------------------------------------------------
    def read(self, job, act, *, target=None, before=None, reaction=False):
        """The owner's judged answer that settles one open item, or None.

        ``act`` is the typed text or ``reaction: <emoji>``; ``target`` the
        message it replies to or reacts on (``reaction``).  A target binds the
        act to the item(s) shown on that message before anything is judged; a
        target that showed nothing open settles nothing.  Returns
        ``{item, approve, rest, parts}``; a reaction has no rest.  ``parts``
        (#424) names numbered facts of a saved-memory notice the act alone
        answers; an act that answers part of any other item settles nothing.
        """
        service = self.service
        items = self.open_items(job, before=before, target=target)
        if target is None and not reaction:
            items = self._within_cost_bound(job, items)
        if not items:
            return None
        now = time.time()
        shown = [{'id': item['id'], 'kind': item['kind'], 'shown_text': _bounded(item['shown_text']),
                  'shown_at': _ago(now, item['shown_at']), 'expires_at': _until(now, item['expires_at']),
                  'answers': ANSWERS[item['kind']]} for item in items]
        how = 'a reaction on' if reaction else 'a reply to'
        described = f'{how} the message that showed {", ".join(item["id"] for item in items)}' if target is not None else ''
        try:
            answer = service.decision_judge.owner_act_answer(act, shown, described, work_id=job.get('id'))
        except Exception as exc:  # an outage settles nothing
            LOG.warning('owner act judgment failed (%s)', type(exc).__name__)
            return None
        if not answer:
            LOG.info('owner act judged work=%s items=%s answer=none', job.get('id'), len(items))
            return None
        item = next((item for item in items if item['id'] == answer['item']), None)
        LOG.info('owner act judged work=%s items=%s answer=%s kind=%s', job.get('id'), len(items),
                         answer['answer'], item['kind'] if item else None)
        if item is None or answer['answer'] not in ('approve', 'decline'):
            return None
        if item['kind'] == KIND_MEMORY_SAVED and answer['answer'] == 'approve':
            return None  # keeping what was remembered needs nothing settled
        parts = answer.get('parts') or []
        if parts and item['kind'] != KIND_MEMORY_SAVED:
            # #424: only a saved-memory notice settles part of itself; part of any other item is not what
            # was shown, so nothing executes (as a change).
            return None
        return {'item': item, 'approve': answer['answer'] == 'approve', 'rest': '' if reaction else answer['rest'],
                'parts': parts}

    # -- settle --------------------------------------------------------------------
    def settle(self, answer, job, *, evidence=None):
        """Settle the one item a judged answer named, through its own path.

        Returns ``{'text', 'note', 'source', 'focus'}``: what the owner is told
        (''), what the worker reads first when the turn goes on (None), the
        Work source it read, and the Work a settings answer keeps in focus.
        Raises ``CalendarError('unavailable')`` when the calendar connector is
        gone.
        """
        service = self.service
        item, approve, ref = answer['item'], answer['approve'], answer['item']['ref']
        kind = item['kind']
        result = {'text': '', 'note': None, 'source': None, 'focus': None}
        if kind == KIND_CALENDAR:
            conversation = service.calendar_conversation
            text = (conversation.approve(ref['owner'], ref['drafts'], evidence=evidence) if approve
                    else conversation.decline(ref['owner'], ref['drafts']))
            result.update(text=text or CHANGED_UNDER_US, source='owner-calendar')
        elif kind == KIND_SETTINGS:
            notify_job = job if job.get('id') else (service.store.job(ref['work_id']) if ref['work_id'] else None)
            outcome = service.settings_orchestrator.settle_pending(
                ref['owner'], ref['channel'], ref['rows'], approve,
                notify=(lambda text, notify_job=notify_job: service.settings_followup(notify_job, text)) if notify_job else None)
            LOG.info('settings drafts %s by owner answer work=%s', 'confirm' if approve else 'cancel', job.get('id'))
            result.update(text=service.settings_response(outcome), source='owner-settings', focus=ref['work_id'])
        elif kind == KIND_STEP:
            current = service.open_step_notification(service.store.notification(ref['notification_id']))
            result['text'] = (service.answer_step_approval(current[0], approve) if current
                              else msg('notice.step_approval_stale'))
        elif kind == KIND_MEMORY_SAVED:
            # #424: an act that names some of a notice's numbered facts undoes only those.
            withdrawn = service.withdraw_memory_notice({'id': ref['notification_id']}, parts=answer.get('parts'))
            if withdrawn:
                from .quickstart_service import MEMORY_WITHDRAWN_NOTE
                result['note'] = MEMORY_WITHDRAWN_NOTE.format(facts=withdrawn)
        elif kind == KIND_MEMORY_PROPOSAL:
            with service.lock:
                notification = service.store.notification(ref['notification_id'])
                binding = service.memory_binding(notification) if notification and notification['state'] == 'sent' else None
                open_ = service.memory_open(binding) if binding and 'sent' in binding else []
                if open_:
                    result['text'] = service.answer_memory_prompt(notification, binding, open_, approve, by='answer')['text']
        elif kind == KIND_PREPARATION:
            with service.lock:
                notification = service.store.notification(ref['notification_id'])
                proposals = (service.offered_proposals(notification)[0]
                             if notification and notification['state'] == 'sent' else [])
                if proposals:
                    result['text'] = service.answer_preparation_proposal(notification, proposals, approve, by='answer')
        return result

    # -- a reaction ----------------------------------------------------------------
    @staticmethod
    def _act_job(surface, chat, generation):
        """The conversation a reaction was made in, as a Work-shaped row (no Work of its own)."""
        return {'id': None, 'channel': f'{surface.channel_prefix}{generation}', 'chat_id': chat}

    def bound_to(self, surface, chat, generation, message_id):
        """Whether a message in this conversation of ``surface`` showed an item still waiting for the owner."""
        return bool(self.open_items(self._act_job(surface, chat, generation), target=message_id))

    def answer_exact(self, surface, chat, generation, message_id, ref, approve):
        """A button's exact act (#214): the one open item ``ref`` names on that message, settled with no judgment.

        ``ref`` is the item's own reference (``{'notification_id': ...}``, or ``{'work_id': ...}`` for a
        settings notice).  The item must still be open and shown on ``message_id`` in this conversation and
        pairing; its settle path enforces the rest (digests, compare-and-set).  Returns the settled kind,
        or None when nothing open matched.
        """
        job = self._act_job(surface, chat, generation)
        item = next((item for item in self.open_items(job, target=message_id)
                     if all(item['ref'].get(key) == value for key, value in ref.items())), None)
        if item is None:
            return None
        if item['kind'] == KIND_MEMORY_SAVED and approve:
            return item['kind']   # keeping what was remembered needs nothing settled
        self._settle_and_tell(surface, chat, generation, job, {'item': item, 'approve': approve, 'rest': ''})
        return item['kind']

    def _settle_and_tell(self, surface, chat, generation, job, answer):
        """Settle a reaction's or a button's answer and tell the owner on ``surface``; Evidence goes to the item's Work."""
        service = self.service
        work_id = answer['item']['ref'].get('work_id')

        def evidence(tool, status, detail):
            if isinstance(work_id, str):
                with service.store.db() as db:
                    db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                               (work_id, tool, status, json.dumps(detail), time.time()))
        try:
            settled = self.settle(answer, job, evidence=evidence)
        except CalendarError:
            settled = {'text': ConnectorHandoff.unavailable(CALENDAR_WRITE_CONNECTOR_ID)}
        # #299 review: told only to the owner who answered, in the pairing they answered in; a re-pairing
        # during the settlement drops the reply instead of retargeting it.
        if settled.get('text') and not surface.send_owner(settled['text'], owner=chat, generation=generation):
            LOG.warning('owner act reply not delivered')
        return settled

    def answer_reaction(self, surface, chat, generation, message_id, emoji):
        """The owner's reaction on a message that showed an open item: judged, and settled when it answers it.

        Runs off the poll thread.  What the owner is told is sent to the paired owner on ``surface``;
        the calendar effect's Evidence goes to the Work that offered the drafts.
        """
        job = self._act_job(surface, chat, generation)
        answer = self.read(job, 'reaction: ' + ' '.join(emoji), target=message_id, reaction=True)
        if not answer:
            return None
        self._settle_and_tell(surface, chat, generation, job, answer)
        return answer['item']['kind']
