"""The Telegram surface: receiving updates, owner taps and notification delivery (#21 slice 2).

This module sits beside the channel projection modules (``conversation_projection``
for what a Work's reply says, ``telegram_presence`` for how it is shown).  It
holds what used to live inside ``AgentService``:

* **ingestion** - ``poll_telegram`` / ``receive_telegram`` read ``getUpdates``;
  ``ingest_update`` turns one owner message into Work, ``ingest_reaction`` and
  ``ingest_stop`` handle reactions and Stop;
* **callbacks** - ``ingest_callback``, ``ingest_settings_callback`` and
  ``ingest_engine_recovery_callback`` settle the owner's taps;
* **delivery** - ``queue_notification`` / ``deliver_notification`` send the
  queued notices, ``deliver_one`` the terminal reply, ``update_task_card`` and
  ``task_progress_text`` the task card, ``deliver_conversation_notice`` one
  conversation notice.

#212: ``TelegramSurface`` is the first implementation of the channel-neutral
``conversation_surface.ConversationSurface``: its pairing (``pairing``,
``owns``), its declared capabilities and the presence primitives the core
Presence code calls through the boundary (``source_message``, ``present``,
``show_draft``) live here, so that code branches on capabilities rather than
on Telegram.

``AgentService`` stays the composition root.  It keeps one entry point per
moved method (each forwards here through ``AgentService.telegram_surface``), so
callers and tests see the same service methods.  The channel holds no state of
its own: every read and write goes through the owning service (``self.service``)
exactly as before the move, so authority checks, approvals, budgets and database
semantics are unchanged.  The constants these methods share with the rest of
the service are still defined in ``quickstart_service``.
"""
import hashlib
import hmac
import json
import time

from .telegram_presence import (CONTROL_RETRY, WaitState, draft_frame, draft_id_for, render_telegram_html, reply_controls_markup,
                                rich_draft_blocks, with_note, without_consumed)
from .providers import ProviderError
from .conversation_handoff import TELEGRAM_UNSENT_BACKOFF_FIRST, TELEGRAM_UNSENT_BACKOFF_MAX, TelegramNotSent, TelegramRejected, transient_failure
from .conversation_surface import (PRESENCE_REACT, PRESENCE_TYPING, ConversationSurface, SurfaceCapabilities, SurfacePairing,
                                   notice_binding, notice_text)
from .agent_runtime import WORK_STOP_KEEP, WORK_STOP_KEY
from .messages import msg
from . import owner_model as om
from . import preparations as prep
from . import remote_login
from . import work_mode
from .quickstart_service import (APPROVE_BUTTON, BROWSER_LOGIN_PHONE_ALERT, BROWSER_LOGIN_PHONE_LABEL, BROWSER_LOGIN_REOPENED_TEXT,
                                 BROWSER_LOGIN_REOPENING_TEXT, BROWSER_LOGIN_SKIP_LABEL,
                                 DENY_BUTTON, LOCAL_DOCUMENT_APPROVAL_TEXT, LOCAL_DOCUMENT_RESUMED_TEXT, LOG, MAX_OWNER_MESSAGE_CHARS,
                                 MEMORY_CANDIDATES_EXPIRED_TEXT, MEMORY_CANDIDATES_OUTDATED_TEXT, MEMORY_PROMPT_KINDS, MEMORY_SAVED_KINDS,
                                 MEMORY_SAVED_OUTDATED_TEXT, MEMORY_SAVED_TTL_SECONDS, REACTION_EVENT, TELEGRAM_PHOTO_ALBUM_SETTLE_SECONDS,
                                 TELEGRAM_RECEIVE_GRACE_SECONDS, WORKING_NOTES_KIND, prepared_ids, truncated_owner_message)


class TelegramSurface(ConversationSurface):
    """Telegram ingestion, callbacks and delivery for one ``AgentService``."""

    name = 'telegram'
    channel_prefix = 'telegram:'
    #: Telegram shows every optional presence: a reaction on the owner's message, ``typing…``, the
    #: thinking draft (``sendRichMessageDraft`` / ``sendMessageDraft``) with its Stop, and inline buttons.
    capabilities = SurfaceCapabilities(reactions=True, typing=True, live_drafts=True, buttons=True, native_stop=True,
                                       action_approvals=True)

    #: The Bot API client method behind each presence action.
    PRESENCE_METHODS = {PRESENCE_REACT: 'set_message_reaction', PRESENCE_TYPING: 'send_chat_action'}

    def __init__(self, service):
        self.service = service

    # -- the ConversationSurface boundary (#212) ------------------------------------
    def pairing(self):
        """The paired private chat of the current bot generation, or None while unpaired.

        #208: None while work mode is on, so presence (👀, typing, drafts) is not attempted.
        """
        if work_mode.active(self.service.store):return None
        cfg=self.service.store.config('telegram',{})
        if not (cfg.get('enabled') and isinstance(cfg.get('user_id'),int)):return None
        return SurfacePairing(f"telegram:{cfg.get('generation')}",cfg['user_id'])

    def owns(self, job):
        """Whether ``job`` came from the paired chat in the current bot generation."""
        cfg=self.service.store.config('telegram',{})
        return bool(job and cfg.get('enabled') and job.get('channel')==f"telegram:{cfg.get('generation')}"
                    and isinstance(job.get('chat_id'),int) and job.get('chat_id')==cfg.get('user_id'))

    # -- owner identity (#213) ------------------------------------------------------
    def claims(self, job):
        """A Work with a Telegram chat id, whatever its bot generation (the identity before #213)."""
        return bool(job) and isinstance(job.get('chat_id'),int)

    def owns_connector_owner(self, owner_id):
        return isinstance(owner_id,str) and owner_id.startswith('telegram:') and owner_id[len('telegram:'):].isdigit()

    def reply_to(self, work_id):
        return self.service.telegram_turns.reply_to(work_id)

    def reply_message(self, work_id, owner):
        """The message id of Still's reply to ``work_id`` in ``owner``'s chat (a calendar item's binding), or None."""
        turn=self.service.telegram_turns.get(work_id)
        return turn['reply_message_id'] if turn and turn['chat_id']==owner else None

    def paired_connector_owner(self, enabled_only=True):
        cfg=self.service.store.config('telegram',{})
        if (not isinstance(cfg,dict) or not isinstance(cfg.get('user_id'),int) or isinstance(cfg.get('user_id'),bool)
                or enabled_only and not cfg.get('enabled')):return None
        return f"telegram:{cfg['user_id']}"

    def owner_generation(self):
        generation=self.service.store.config('telegram',{}).get('generation')
        return generation if isinstance(generation,str) else ''

    def paired_owner(self, owner):
        cfg=self.service.store.config('telegram',{})
        return bool(cfg.get('enabled')) and owner is not None and str(owner)==str(cfg.get('user_id'))

    def send_owner(self, text, reply_to=None, owner=None, generation=None):
        cfg=self.service.store.config('telegram',{})
        if not (cfg.get('enabled') and isinstance(cfg.get('user_id'),int)):return False
        # #299 review: a result for a captured conversation is sent only while it is still the pairing.
        if owner is not None and str(owner)!=str(cfg['user_id']):return False
        if generation is not None and generation!=cfg.get('generation'):return False
        try:
            self.service.telegram.send_message(cfg['user_id'],text,**({'reply_to':reply_to} if reply_to is not None else {}))
        except Exception as exc:  # best effort: a lost bubble never changes durable state
            LOG.warning('owner message not delivered (%s)',type(exc).__name__)
            return False
        return True

    def receive(self, now=None):
        return self.service.receive_telegram(now)

    def deliver_reply(self):
        return self.deliver_one()

    def source_message(self, work_id):
        """The owner's Telegram message this Work answers (#581), or None."""
        source=self.service.telegram_turns.source(work_id)
        return source if isinstance(source,int) else None

    def present(self, action, *args):
        """One best-effort reaction (``chat, message_id, emoji``) or ``typing…`` (``chat``)."""
        if action==PRESENCE_TYPING:args=(*args,'typing')
        return self._attempt(self.PRESENCE_METHODS[action],*args)

    def _attempt(self, method, *args, **kwargs):
        """``(sent, refused)``: ``refused`` only when Telegram answered ``ok: false`` (``TelegramRejected``).

        A timeout or a lost connection is not a refusal (#908).  Presentation
        only: a failure is swallowed here and never changes Work status,
        delivery or Evidence, and nothing is retried in a way that could send
        a second durable bubble.
        """
        try:
            getattr(self.service.telegram,method)(*args,**kwargs)
            return True,False
        except Exception as exc:  # presentation only
            # INFO, so the owner-private log can tell a Telegram refusal of a
            # reaction/typing/draft apart from code that never sent it (#581
            # live discrepancy).  Only the method, error class and Telegram
            # status code are logged - never the description, text or token.
            LOG.info('telegram presence %s failed: %s status=%s',method,type(exc).__name__,getattr(exc,'status',None))
            # Only Telegram's own ok:false answer is a refusal; the transport's timeout
            # (status='timeout') or a lost connection is not (#909 review).
            return False,isinstance(exc,TelegramRejected)

    def show_draft(self, job, state, text, note=None):
        """One Stop-able draft edit: the dots after any step line (#718/#835/#858).

        First Telegram's animated thinking block (`sendRichMessageDraft`,
        `rich_draft_blocks`: the thinking block holds `text`, the #839
        attention `note` follows as a paragraph).  Once that is refused for
        this Work (`state.rich_draft_failed`) the plain `sendMessageDraft`
        carries the same text; a refusal there too is the caller's fallback
        to typing.  The text is never empty (an empty draft is a blank bubble
        on the owner's iOS client, #581).  The same `draft_id` maps Stop back
        to the Work either way (``ingest_stop``, the ``native_stop`` capability).
        """
        text=text or draft_frame('',0)
        draft_id=draft_id_for(job['id'])
        can_stop=self.capabilities.native_stop
        if not state.rich_draft_failed:
            sent,refused=self._attempt('send_rich_message_draft',job['chat_id'],draft_id,rich_draft_blocks(text,note),can_stop=can_stop)
            state.rich_draft_retry=not sent and not refused
            if sent:
                return True
            # #908 (live 2026-09-30 22:58): the first draft after a restart timed out and the
            # whole Work fell back to the plain draft.  Only Telegram refusing the rich draft
            # retires it for this Work; a timeout falls back this once and the next edit retries.
            if refused:state.rich_draft_failed=True
        return self._attempt('send_message_draft',job['chat_id'],draft_id,with_note(text,note),can_stop=can_stop)[0]

    def consume_control(self, chat_id, message_id, markup):
        """Make a used inline control visibly inert; fall back to removing it."""
        if self._attempt('edit_message_reply_markup',chat_id,message_id,markup)[0]:return
        self._attempt('edit_message_reply_markup',chat_id,message_id,without_consumed(markup))

    def ingest_stop(self, stopped, generation):
        """Reconcile Telegram's Stop on a draft with real Work state.

        Stop ends further drafts at once.  Whether anything was *cancelled*
        is decided by the existing `cancel_focused_work` state machine: queued
        Work is cancelled and that is said; running Work is not cancellable
        mid-turn, so the owner is told it continues - never that it stopped.
        Finished Work gets nothing, because its one reply is the answer.
        """
        service=self.service
        with service.lock:
            cfg=service.store.config('telegram',{})
            chat=stopped.get('chat',{}) if isinstance(stopped,dict) else {}
            draft_id=stopped.get('draft_id') if isinstance(stopped,dict) else None
            if not (cfg.get('enabled') and cfg.get('generation')==generation and isinstance(chat,dict)
                    and chat.get('type')=='private' and isinstance(chat.get('id'),int)
                    and chat.get('id')==cfg.get('user_id') and isinstance(draft_id,int)):
                return None
            with service.store.db() as db:
                rows=db.execute("SELECT * FROM jobs WHERE channel=? AND chat_id=? AND status IN ('queued','running')",
                                (f"telegram:{generation}",chat['id'])).fetchall()
            job=next((dict(row) for row in rows if draft_id_for(row['id'])==draft_id),None)
            if not job:return 'finished'
            state=service.presence.setdefault(job['id'],WaitState())
            # A repeated Stop update (double tap, redelivery) says nothing twice.
            if state.stopped:return 'duplicate'
            state.stopped=True
            cancelled,_reason=service.cancel_focused_work(job,service.issuer_owner_id(job))
            current=service.store.job(job['id'])
            if cancelled:
                service.presence.pop(job['id'],None)
                text=service.STOP_CANCELLED_TEXT
            elif current and current['status']=='running':
                # #606 T1: durable, so the CLI's separate MCP bridge process
                # refuses its next call too, not only this process's loop.
                service.store.append_config_list(WORK_STOP_KEY,job['id'],WORK_STOP_KEEP)
                # #774: a stopped Work's location request continues nothing.
                if service.context_observations.cancel_work_requests(job['id']):service.store.remove_telegram_photo(job['id'])
                text=service.STOP_RUNNING_TEXT
            else:
                return 'finished'
            try:service.telegram.send_message(job['chat_id'],text,reply_to=service.telegram_turns.source(job['id']))
            except ProviderError:pass
            return 'cancelled' if cancelled else 'running'

    def deliver_conversation_notice(self, text):
        """The best-effort half: the same text to the paired Telegram chat."""
        service=self.service
        cfg,paired,_channel=service._notice_channel()
        if paired:
            try:service.telegram.send_message(int(cfg['user_id']),text)
            except (ProviderError,TypeError,ValueError):LOG.warning('conversation notice not delivered')

    def queue_notification(self, job, kind, fingerprint=None):
        service=self.service
        cfg=service.store.config('telegram',{})
        if not (cfg.get('enabled') and job.get('channel')==f"telegram:{cfg.get('generation')}" and job.get('chat_id')==cfg.get('user_id')):return
        fingerprint=service.document_fingerprint() if kind=='approval_needed' else fingerprint
        service.store.queue_notification(job['id'],job['chat_id'],cfg['generation'],kind,fingerprint,surface=self.name)

    def _notice_markup(self, notification, bound):
        """Telegram's buttons for a notice that is still shown (``notice_binding``), or None."""
        service,kind,nid=self.service,notification['kind'],notification['id']
        if kind=='approval_needed':
            return {'inline_keyboard':[[{'text':APPROVE_BUTTON,'callback_data':f"p7a:{nid}:approve"},
                                        {'text':DENY_BUTTON,'callback_data':f"p7a:{nid}:deny"}]]}
        if kind=='context_approval_needed':
            return {'inline_keyboard':[[{'text':APPROVE_BUTTON,'callback_data':f"v1c:{nid}:approve"},
                                        {'text':DENY_BUTTON,'callback_data':f"v1c:{nid}:deny"}]]}
        if kind=='browser_login_needed':
            return {'inline_keyboard':[[{'text':msg('button.signed_in'),'callback_data':f"p7l:{nid}:done"},
                                        {'text':BROWSER_LOGIN_SKIP_LABEL,'callback_data':f"p7l:{nid}:skip"}],
                                       # #939: the same login window, driven from the phone through a one-time link.
                                       [{'text':BROWSER_LOGIN_PHONE_LABEL,'callback_data':f"p7l:{nid}:phone"}]]}
        if kind=='preparation_scheduled':
            return service.preparation_cancel_markup(nid)
        if kind=='preparation_proposed':
            return {'inline_keyboard':[[{'text':msg('button.accept'),'callback_data':f"p7p:{nid}:accept"},
                                        {'text':msg('button.decline_schedule'),'callback_data':f"p7p:{nid}:deny"}]]}
        if kind in MEMORY_PROMPT_KINDS:
            return service.memory_prompt_markup(nid,bound['memory_prompt'])
        if kind in MEMORY_SAVED_KINDS:
            return service.memory_saved_markup(nid,bound['memory_saved'])
        if kind=='settings_change_proposed':
            return {'inline_keyboard':[[{'text':msg('button.apply'),'callback_data':f"p7s:{nid}:confirm"},
                                        {'text':msg('button.keep'),'callback_data':f"p7s:{nid}:cancel"}]]}
        if kind==prep.NOTIFY_KIND:
            return {'inline_keyboard':[[{'text':msg('button.stop_watching'),'callback_data':f"p7q:{nid}:stop"}]]}
        # #1472 (owner choice 2026-10-13): a step approval has no buttons; the owner answers with a
        # reaction or a reply.  Working notes have none either (#996).
        return None

    def deliver_notification(self):
        # Persist the send intent first. An uncertain Telegram response is never
        # replayed after a restart because it might already have been delivered.
        service=self.service
        # #213: only this surface's queued notices; another surface's are never claimed or cancelled here.
        notification=service.store.next_notification(self.name)
        if not notification:return False
        with service.lock:
            # #208 review: the row was read before the lock; a switch to work mode may have cancelled
            # it since (``cancel_pending_deliveries`` holds the same lock), so a cancelled row stays cancelled.
            with service.store.db() as db:
                state=db.execute('SELECT state FROM telegram_notifications WHERE id=?',(notification['id'],)).fetchone()
            if state is None or state['state']=='cancelled':return True
            cfg=service.store.config('telegram',{})
            allowed=(cfg.get('enabled') and notification['generation']==cfg.get('generation')
                     and notification['chat_id']==cfg.get('user_id') and not work_mode.was_on_since(service.store,self._notification_since(notification)))
            service.store.update_notification(notification['id'],'sending' if allowed else 'cancelled')
            if not allowed:return True
            # #213: whether it is still shown, what it binds and what it says are shared by every surface
            # (``conversation_surface.notice_binding`` / ``notice_text``); the buttons are Telegram's.
            bound=notice_binding(service,notification)
            if bound is None:
                service.store.update_notification(notification['id'],'cancelled')
                return True
            memory_prompt,memory_saved=bound.get('memory_prompt'),bound.get('memory_saved')
            reply_markup=self._notice_markup(notification,bound)
            try:
                text=notice_text(service,notification,bound)
                if notification['kind']==prep.NOTIFY_KIND:
                    # #719: a result bubble, rendered like deliver_one's (#581); a parse refusal is a definite non-delivery.
                    try:
                        result=service.telegram.send_message(notification['chat_id'],render_telegram_html(text),reply_markup,parse_mode='HTML')
                    except TelegramRejected as exc:
                        if not exc.entity_parse_error:raise
                        result=service.telegram.send_message(notification['chat_id'],text,reply_markup)
                else:
                    # #1472: a request prompt reads its description first; the exact request is folded.
                    folded=(service.browser_step_prompt_html(notification.get('job_id'))
                            if notification['kind']=='browser_approval_needed' else None)
                    if folded and len(folded)<=3800:
                        try:
                            result=service.telegram.send_message(notification['chat_id'],folded,reply_markup,parse_mode='HTML')
                        except TelegramRejected as exc:
                            if not exc.entity_parse_error:raise
                            folded=None
                    else:
                        folded=None
                    if not folded:
                        if notification['kind'] in ('browser_approval_needed','settings_change_proposed') and len(text)>1900:
                            # Preserve every parameter. Only the last part carries
                            # approval controls, after earlier parts were delivered.
                            parts=[text[offset:offset+1900] for offset in range(0,len(text),1900)]
                            for index,part in enumerate(parts[:-1],1):
                                service.telegram.send_message(notification['chat_id'],f'승인 내용 ({index}/{len(parts)})\n{part}')
                            text=f'승인 내용 ({len(parts)}/{len(parts)})\n{parts[-1]}\n위에 나누어 보낸 전체 실행 내용을 확인한 뒤 승인하세요.'
                        result=service.telegram.send_message(notification['chat_id'],text,reply_markup)
                message_id=result.get('message_id') if isinstance(result,dict) else None
                # #818 review: a memory prompt with nothing tappable is only a list ('memory_listed').
                listed=notification['kind'] in MEMORY_PROMPT_KINDS and not service.memory_open(memory_prompt)
                service.store.update_notification(notification['id'],'memory_listed' if listed else 'sent',
                                               message_id if isinstance(message_id,int) else None,
                                               json.dumps(memory_prompt) if notification['kind'] in MEMORY_PROMPT_KINDS else
                                               json.dumps(memory_saved) if notification['kind'] in MEMORY_SAVED_KINDS else None,
                                               delivered=True)
                if notification['kind']=='browser_login_needed' and service.AUTO_PHONE_LOGIN:
                    # #953 (owner 2026-10-01): the person on Telegram is on their phone; the one-time
                    # link follows the prompt at once instead of waiting for 휴대폰에서 로그인.
                    try:
                        service._start_remote_login_for_work(notification.get('job_id'),notification.get('fingerprint'),
                                                          notification['chat_id'])
                    except Exception as exc:
                        LOG.warning('remote login auto start skipped (%s)',type(exc).__name__)
                if notification['kind']==prep.NOTIFY_KIND:
                    # #719: only a confirmed send is what the owner was last told.
                    try:
                        job=service.store.job(notification['job_id'])
                        service.preparations.record_notified(notification['job_id'],notification['fingerprint'],
                                                          service.scrub_prepared_answer(job) if job else '',service.preparations.clock())
                    except Exception as exc:
                        LOG.warning('watch notification record failed work=%s kind=%s',notification['job_id'],type(exc).__name__)
            except work_mode.WorkModeRefused:
                # #208 review: refused before hand-off, so definitely not delivered (never sent later).
                service.store.update_notification(notification['id'],'cancelled')
            except ProviderError:
                service.store.update_notification(notification['id'],'unknown')
        return True

    def task_progress_text(self, job_id):
        """Return safe operational evidence without request or tool payloads.

        A card is also the owner-facing recovery surface.  In particular, do
        not let a restart or a lost Telegram response look like work that is
        still running or a result that was certainly delivered.
        """
        service=self.service
        labels={'queued':'대기 중','running':'진행 중','succeeded':'완료','partial':'일부 완료',
                'failed':'완료하지 못함','cancelled':'취소됨','interrupted':'중단됨','unknown':'외부 결과 불확실',
                'awaiting_connection':'연결 대기'}
        with service.store.db() as db:
            job=db.execute('SELECT status,delivery FROM jobs WHERE id=?',(job_id,)).fetchone()
            rows=db.execute('SELECT tool,status FROM tool_events WHERE job_id=? AND tool!=? ORDER BY id LIMIT 12',(job_id,'model')).fetchall()
        if not job:return msg('service.i_can_t_find_this_task')
        lines=[f"작업 상태: {labels.get(job['status'],job['status'])}"]
        if rows:
            lines.append(f'필요한 단계를 {len(rows)}개 처리했습니다.')
        elif job['status']=='queued':
            lines.append(msg('service.waiting_to_run'))
        elif job['status']=='running':
            lines.append('에이전트가 작업을 처리하고 있습니다.')
        elif job['status']=='interrupted':
            lines.append(msg('service.the_task_was_interrupted_by_a'))
        elif job['status']=='awaiting_connection':
            lines.append('필요한 연결을 기다리고 있습니다. 아직 아무 작업도 실행하지 않았습니다.')
        else:
            lines.append(msg('service.see_the_full_result_in_the'))
        if job['delivery']=='unknown':
            lines.append(msg('service.telegram_delivery_could_not_be_verified'))
        elif job['delivery']=='cancelled':
            lines.append('Telegram 전달은 취소되었습니다.')
        return '\n'.join(lines)

    def update_task_card(self, job, state):
        """Reconcile a task card that still exists for this Work.

        Nothing sends a new card since #958 (queued Work shows only the 👀;
        running Work the draft with Stop).  Cards sent before that change may
        still sit in the owner's chat, so a state change keeps editing them
        (and `running` deletes them) exactly as before; a Work without a card
        is a no-op here.
        """
        service=self.service
        card=service.store.task_card(job['id'])
        if not card or card['message_id']<1 or card['state']==state:return
        if state=='running':
            if not service.store.mark_task_card_deleting(job['id'],card['message_id']):return
            try:
                deleted=service.telegram.delete_message(card['chat_id'],card['message_id'])
            except ProviderError:
                return
            if deleted is True:
                service.store.delete_task_card(job['id'],card['message_id'])
            return
        markup=service.task_card_markup(job['id'],state)
        try:
            service.telegram.edit_message_text(card['chat_id'],card['message_id'],
                                            service.task_card_text(job['message'],state),markup)
            service.store.save_task_card(job['id'],card['chat_id'],card['message_id'],state)
        except ProviderError:
            pass

    def ingest_settings_callback(self, callback, generation):
        """#814: the owner's apply/cancel for one Work's settings drafts.

        Exact: this notification, sent, this chat and message, the Work from
        this chat and generation, and the drafts still exactly the set the
        message offered (ids and digests).  The notification is consumed under
        the lock; the drafts are applied after it through the orchestrator's
        confirm (its own exactly-once state machine), so a slow Main/Judgment
        AI check does not hold the service lock.
        """
        service=self.service
        message=callback.get('message',{}) if isinstance(callback.get('message'),dict) else {}
        sender=callback.get('from',{}).get('id') if isinstance(callback.get('from'),dict) else None
        callback_id=callback.get('id')
        parts=str(callback.get('data') or '').split(':')
        rows,job,notification,renew_failure=[],None,None,None
        with service.lock:
            cfg=service.store.config('telegram',{})
            authorized=service._callback_authorized(cfg,generation,sender,message.get('chat',{}) if isinstance(message.get('chat'),dict) else {})
            if authorized and len(parts)==3 and parts[2] in ('confirm','cancel'):
                notification=service.store.notification(parts[1])
                job=service.store.job(notification['job_id']) if notification else None
                exact=(notification and notification['kind']=='settings_change_proposed' and notification['state']=='sent'
                       and notification['generation']==generation and notification['chat_id']==sender
                       and notification['message_id']==message.get('message_id') and job
                       and job['channel']==f"telegram:{generation}" and job['chat_id']==sender)
                rows=service.offered_settings_drafts(notification) if exact else []
                if exact and not rows and parts[2]=='confirm':
                    # The tap approves the exact effects the message showed; a timeout alone is not a dead end.
                    try:rows=service.settings_orchestrator.renew_expired(service.settings_owner(job),job['channel'],job['id'],notification.get('fingerprint'))
                    except ValueError as exc:rows,renew_failure=[],str(exc)
                if rows:service.store.update_notification(notification['id'],'settings_'+parts[2]+'ing')
                elif exact and parts[2]=='cancel':
                    # Nothing left to cancel (timed out or already settled): the owner's answer is still "no change".
                    service.store.update_notification(notification['id'],'settings_canceled')
                    rows=[{'_nothing_left':True}]
        if not authorized:return
        if rows and rows[0].get('_nothing_left'):
            if isinstance(callback_id,str):
                try:service.telegram.answer_callback_query(callback_id,msg('service.nothing_was_changed'),show_alert=False)
                except ProviderError:pass
            try:service.telegram.edit_message_text(sender,notification['message_id'],msg('service.nothing_was_changed'),{'inline_keyboard':[]})
            except ProviderError:pass
            return
        # #814 review P2-3: the tap is answered first; a slow setter then runs off this poll thread.
        if isinstance(callback_id,str):
            text=(msg('service.started_applying_the_change') if rows and parts[2]=='confirm' else msg('service.done') if rows else '')
            try:service.telegram.answer_callback_query(callback_id,text,show_alert=False)
            except ProviderError:pass
        if not rows:
            # Nothing to act on: the message shows what became of it instead of leaving dead buttons.
            this_message=(notification and notification['kind']=='settings_change_proposed' and notification['chat_id']==sender
                          and notification['message_id']==message.get('message_id'))
            if this_message:
                done=renew_failure or {'settings_confirmed':msg('service.i_ve_already_applied_it'),'settings_canceled':msg('service.i_didn_t_change_it')}.get(notification['state'])
                try:
                    if done:service.telegram.edit_message_text(sender,notification['message_id'],done,{'inline_keyboard':[]})
                    else:service.telegram.edit_message_reply_markup(sender,notification['message_id'],{'inline_keyboard':[]})
                except ProviderError:pass
            elif message.get('message_id'):
                try:service.telegram.edit_message_reply_markup(sender,message['message_id'],{'inline_keyboard':[]})
                except ProviderError:pass
            return
        lines=[]
        owner,channel=service.settings_owner(job),job['channel']
        for row in rows:
            try:
                result=(service.settings_orchestrator.confirm(owner,channel,row['id'],row['digest'],
                                                           notify=lambda text,job=job:service.settings_followup(job,text))
                        if parts[2]=='confirm' else service.settings_orchestrator.cancel(owner,channel,row['id']))
                lines.append(result.get('response') or msg('service.done'))
            except ValueError as exc:
                lines.append(f"{row['effect']}: {exc}")
        LOG.info('settings drafts %s by owner button work=%s count=%s',parts[2],job['id'],len(rows))
        service.store.update_notification(notification['id'],'settings_confirmed' if parts[2]=='confirm' else 'settings_canceled')
        try:service.telegram.edit_message_text(sender,notification['message_id'],'\n'.join(lines),{'inline_keyboard':[]})
        except ProviderError:pass

    def ingest_engine_recovery_callback(self, callback, generation):
        """Select an existing alternate route from this exact usage-limit reply."""
        service=self.service
        message=callback.get('message',{}) if isinstance(callback.get('message'),dict) else {}
        chat=message.get('chat',{}) if isinstance(message.get('chat'),dict) else {}
        sender=callback.get('from',{}).get('id') if isinstance(callback.get('from'),dict) else None
        callback_id=callback.get('id')
        parts=str(callback.get('data') or '').split(':')
        selected_name=None
        selected_engine=None
        reason=''
        job=None
        with service.lock:
            cfg=service.store.config('telegram',{})
            authorized=service._callback_authorized(cfg,generation,sender,chat)
            if authorized and len(parts)==3 and parts[0]=='p7e':
                job=service.store.job(parts[1])
                turn=service.telegram_turns.get(parts[1])
                provenance=service.store.turn_provenance(parts[1]) or {}
                engine_id=provenance.get('engine')
                exact=(job and job.get('status')=='failed' and job.get('channel')==f'telegram:{generation}'
                       and job.get('chat_id')==sender and provenance.get('route')=='subscription'
                       and provenance.get('failure_class')=='usage-limit' and not provenance.get('usage_limit_recovery_selected')
                       and turn and turn.get('chat_id')==sender and turn.get('reply_message_id')==message.get('message_id'))
                options=service.usage_limit_route_options(job) if exact else []
                option=next((item for item in options if item['id']==parts[2]),None)
                if option and engine_id:
                    selected_name=option['name']
                    selected_engine=parts[2]
        if selected_engine and job and engine_id:
            try:
                service.connect_subscription_engine({'engine':selected_engine,'officially_authenticated':True,
                                                  'recovery_work_id':job['id'],'expected_current':engine_id})
            except ValueError as exc:
                reason=str(exc)
                selected_name=None
            except Exception as exc:
                LOG.warning('usage-limit route change failed work=%s kind=%s',job['id'],type(exc).__name__)
                reason='AI 연결을 확인하지 못했어요. AI 설정에서 상태를 확인하세요.'
        if selected_name:
            # Keep the existing retry/details controls, but consume every route
            # choice on this one failed reply. The failed Work is never queued.
            try:
                controls=service.reply_controls(job,False)
                service.telegram.edit_message_reply_markup(sender,message.get('message_id'),
                                                        reply_controls_markup(job['id'],controls))
            except ProviderError:pass
            reason=f'{selected_name}로 전환했습니다. 실패한 요청은 자동으로 다시 실행하지 않았어요. 같은 요청을 다시 보내 주세요.'
        if isinstance(callback_id,str):
            try:service.telegram.answer_callback_query(callback_id,reason,show_alert=not bool(selected_name))
            except ProviderError:pass

    def ingest_callback(self, callback, generation):
        """Accept only paired-owner, exact-message task and approval callbacks."""
        service=self.service
        if isinstance(callback.get('data'),str) and callback['data'].startswith('p7e:'):
            return service.ingest_engine_recovery_callback(callback,generation)
        if isinstance(callback.get('data'),str) and callback['data'].startswith('p7s:'):
            return service.ingest_settings_callback(callback,generation)
        with service.lock:
            cfg=service.store.config('telegram',{})
            sender=callback.get('from',{}).get('id')
            message=callback.get('message',{})
            chat=message.get('chat',{}) if isinstance(message,dict) else {}
            callback_id=callback.get('id')
            data=callback.get('data','')
            authorized=service._callback_authorized(cfg,generation,sender,chat)
            changed=False
            #: (text, show_alert) for this tap's answerCallbackQuery.  Detail is
            #: shown as the tap's own alert (#581) instead of a new bubble.
            alert=None
            if authorized and isinstance(data,str) and data[:4] in ('p7v:','p7d:'):
                job_id=data[4:]
                job=service.store.job(job_id)
                card=service.store.task_card(job_id) if data.startswith('p7v:') else None
                turn=service.telegram_turns.get(job_id) if data.startswith('p7d:') else None
                exact_surface=((card and card['chat_id']==sender and card['message_id']==message.get('message_id'))
                               or (turn and turn['chat_id']==sender and turn['reply_message_id']==message.get('message_id')))
                if (job and exact_surface and job['channel']==f"telegram:{generation}" and job['chat_id']==sender):
                    progress=service.task_progress_text(job_id)
                    if len(progress)<=200:
                        alert=(progress,True)
                    else:
                        # An alert holds 200 characters; never truncate a
                        # truth line (unknown delivery) - send it instead.
                        try:service.telegram.send_message(sender,progress)
                        except ProviderError:pass
                    changed=True
            elif authorized and isinstance(data,str) and data.startswith('p7r:'):
                job_id=data[4:]
                job=service.store.job(job_id)
                turn=service.telegram_turns.get(job_id)
                if (job and turn and turn['chat_id']==sender and turn['reply_message_id']==message.get('message_id')
                        and job['channel']==f"telegram:{generation}" and job['chat_id']==sender):
                    retry_id,reason=service.retry_from_control(job,generation,sender)
                    if retry_id:
                        alert=(msg('service.i_ll_try_again'),False)
                        changed=True
                        self.consume_control(sender,message.get('message_id'),
                                              reply_controls_markup(job_id,(CONTROL_RETRY,),consumed=(CONTROL_RETRY,)))
                    else:
                        alert=(reason,True)
            elif authorized and isinstance(data,str) and data.startswith('p7c:'):
                job_id=data[4:]
                with service.store.db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    job=db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
                    card=service.store.telegram.card_row(db,job_id)
                    if (job and card and card['message_id']==-2 and isinstance(message.get('message_id'),int)
                            and card['chat_id']==sender and job['channel']==f"telegram:{generation}"
                            and job['chat_id']==sender and job['status']=='queued'):
                        service.store.telegram.confirm_unknown_card(db,job_id,message['message_id'])
                        card=service.store.telegram.card_row(db,job_id)
                    if (job and card and card['chat_id']==sender and card['message_id']==message.get('message_id')
                            and job['channel']==f"telegram:{generation}" and job['chat_id']==sender and job['status']=='queued'):
                        db.execute("UPDATE jobs SET status='cancelled',error='소유자가 작업 카드를 통해 취소했습니다.',delivery='cancelled' WHERE id=? AND status='queued'",(job_id,))
                        changed=db.total_changes==1
                        job=dict(job)
                if changed:
                    if service.context_observations.cancel_work_requests(job_id):service.store.remove_telegram_photo(job_id)
                    service.update_task_card(job,'cancelled')
                    service._clear_queued_presence(job)
            elif authorized and isinstance(data,str) and data.startswith('p7x:'):
                choice=service.store.telegram_context_choice(data[4:])
                exact=(choice and choice['state']=='offered' and choice['generation']==generation
                       and choice['chat_id']==sender and choice['message_id']==message.get('message_id'))
                if exact:
                    selected=service.store.select_telegram_context_choice(choice['token'])
                    with service.store.db() as db:
                        db.execute('BEGIN IMMEDIATE')
                        job=db.execute('SELECT * FROM jobs WHERE id=?',(selected['job_id'],)).fetchone()
                        if job and job['status']=='awaiting_context' and job['channel']==f"telegram:{generation}" and job['chat_id']==sender:
                            service.store.attach_context(selected['job_id'],[selected['event_id']],service.context_assistant_id(),db)
                            db.execute("UPDATE jobs SET status='queued',error=NULL WHERE id=?",(selected['job_id'],))
                            job=dict(job)
                        else: job=None
                    if job:
                        try:service.telegram.edit_message_text(sender,message.get('message_id'),msg('service.i_ve_attached_the_selected_context'),{'inline_keyboard':[]})
                        except ProviderError:pass
                        changed=True
            elif authorized and isinstance(data,str) and data.startswith('p7n:'):
                job_id=data[4:]
                choice=service.store.telegram_context_choice_for_job(job_id)
                exact=(choice and choice['generation']==generation and choice['chat_id']==sender and choice['message_id']==message.get('message_id'))
                with service.store.db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    job=db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
                    if exact and job and job['status']=='awaiting_context' and job['channel']==f"telegram:{generation}" and job['chat_id']==sender:
                        db.execute("UPDATE jobs SET status='queued',error=NULL WHERE id=?",(job_id,))
                        job=dict(job)
                    else:job=None
                if job:
                    try:service.telegram.edit_message_text(sender,message.get('message_id'),'컨텍스트 없이 이 요청을 시작할게요.',{'inline_keyboard':[]})
                    except ProviderError:pass
                    changed=True
            elif authorized and isinstance(data,str) and data.startswith('p7a:'):
                parts=data.split(':')
                if len(parts)==3 and parts[2] in ('approve','deny'):
                    notification=service.store.notification(parts[1])
                    current=service.document_boundary()
                    exact=(notification and notification['kind']=='approval_needed' and notification['state']=='sent'
                           and notification['generation']==generation and notification['chat_id']==sender
                           and notification['message_id']==message.get('message_id')
                           and notification['fingerprint']==service.document_fingerprint()
                           and current['requires_approval'])
                    if exact:
                        resumed=False
                        if parts[2]=='approve':
                            service.set_document_approval({'approved':True})
                            # #505: the folder-resumed Work continues once.
                            resumed=service.resume_after_document_approval(notification.get('job_id'))
                            result_kind='approved'
                        else:
                            service.store.put('document_sharing',{})
                            service.drop_document_resume(work_id=notification.get('job_id'))
                            result_kind='denied'
                        service.store.update_notification(notification['id'],result_kind)
                        try:service.telegram.edit_message_text(sender,notification['message_id'],
                            LOCAL_DOCUMENT_RESUMED_TEXT if resumed else service.notification_text(result_kind),{'inline_keyboard':[]})
                        except ProviderError:pass
                        changed=True
            elif authorized and isinstance(data,str) and data.startswith('p7w:'):
                # #656: one guarded browser step of one Work.  Exact: this
                # notification, sent, this chat and message, and the request
                # it names is still the pending one (fingerprint).
                parts=data.split(':')
                if len(parts)==3 and parts[2] in ('approve','deny'):
                    notification=service.store.notification(parts[1])
                    pending=service._browser_request(notification['job_id']) if notification else None
                    exact=(notification and notification['kind']=='browser_approval_needed' and notification['state']=='sent'
                           and notification['generation']==generation and notification['chat_id']==sender
                           and notification['message_id']==message.get('message_id') and pending
                           and pending.get('state')=='requested' and notification['fingerprint']==pending.get('offer_digest'))
                    if exact:
                        decision=service._decide_browser_step(notification['job_id'],parts[2]=='approve',notification['fingerprint'])
                        result_kind='browser_approved' if decision.get('approved') else 'browser_denied'
                        service.store.update_notification(notification['id'],result_kind)
                        # #1472: say what really happened (approved but not continued, or stale), never "continuing".
                        said=service.step_approval_result_text(notification['job_id'],decision,parts[2]=='approve')
                        try:service.telegram.edit_message_text(sender,notification['message_id'],said,{'inline_keyboard':[]})
                        except ProviderError:pass
                        changed=True
            elif authorized and isinstance(data,str) and data.startswith('p7l:'):
                # #709: the owner's answer to one Work's in-flow login.  Exact:
                # this notification, sent, this chat and message, and the login
                # it names is still the offered one (nonce as fingerprint).
                parts=data.split(':')
                if len(parts)==3 and parts[2]=='reopen':
                    # #1573: the expired prompt's one button; exact as below, for the expired login it named.
                    notification=service.store.notification(parts[1])
                    exact=(notification and notification['kind']=='browser_login_needed'
                           and notification['state']=='browser_login_expired'
                           and notification['generation']==generation and notification['chat_id']==sender
                           and notification['message_id']==message.get('message_id')
                           and service.reopen_browser_login(notification['job_id'],notification['fingerprint']))
                    if exact:
                        # #192: the old prompt sits far up the chat; it only loses its button, and the reopening
                        # is said at the bottom, where the owner is reading.
                        try:service.telegram.edit_message_text(sender,notification['message_id'],BROWSER_LOGIN_REOPENED_TEXT,{'inline_keyboard':[]})
                        except ProviderError:pass
                        # Only while still the expired prompt: the work loop may already have re-armed it, and
                        # then its new prompt is the bottom line.
                        if service.store.telegram.mark_login_reopened(notification['id']):
                            try:sent=service.telegram.send_message(sender,BROWSER_LOGIN_REOPENING_TEXT)
                            except ProviderError:sent=None
                            # A window that cannot open is then said on this bottom line (``_reopen_failed``).
                            if isinstance(sent,dict) and isinstance(sent.get('message_id'),int):
                                service.store.telegram.move_reopened_login(notification['id'],sent['message_id'])
                        changed=True
                elif len(parts)==3 and parts[2] in ('done','skip','phone'):
                    notification=service.store.notification(parts[1])
                    row=service._browser_login(notification['job_id']) if notification else None
                    exact=(notification and notification['kind']=='browser_login_needed' and notification['state']=='sent'
                           and notification['generation']==generation and notification['chat_id']==sender
                           and notification['message_id']==message.get('message_id') and row
                           and row.get('state')=='offered' and notification['fingerprint']==row.get('nonce'))
                    if exact and parts[2]=='phone':
                        # #939: the same window from the phone; the link goes to this (paired owner) chat only,
                        # from its own thread (the tunnel takes seconds to open).
                        started=service._start_remote_login_for_work(notification['job_id'],row['nonce'],sender)
                        alert=(BROWSER_LOGIN_PHONE_ALERT if started else remote_login.BUSY_TEXT,not started)
                        changed=started
                    elif exact:
                        # The work loop closes the window and settles it (never this poll thread).
                        decided=service._request_login_decision(notification['job_id'],row['nonce'],'resume' if parts[2]=='done' else 'skip')
                        changed=decided.get('state') is not None
            elif authorized and isinstance(data,str) and data.startswith('p7p:'):
                # #659: the owner's explicit yes/no for one Work's proposed
                # preparations.  Exact: this notification, sent, this chat
                # and message, and the proposals are still exactly the set
                # the message offered (fingerprint).
                parts=data.split(':')
                if len(parts)==3 and parts[2]=='cancel':
                    # #1455: cancel exactly the reservations this message confirmed (accepted here, or
                    # scheduled by the owner's own request); this chat and message only.
                    notification=service.store.notification(parts[1])
                    exact=(notification and notification['generation']==generation and notification['chat_id']==sender
                           and notification['message_id']==message.get('message_id')
                           and (notification['kind'],notification['state']) in (('preparation_proposed','preparation_accepted'),
                                                                                ('preparation_scheduled','sent')))
                    if exact:
                        cancelled=0
                        for preparation_id in prepared_ids(notification):
                            try:cancelled+=bool(service.cancel_preparation(preparation_id).get('changed'))
                            except prep.PreparationRefusal:continue
                        result_kind='preparation_cancelled' if cancelled else 'preparation_already_settled'
                        LOG.info('preparation cancel by owner button work=%s count=%s',notification['job_id'],cancelled)
                        service.store.update_notification(notification['id'],result_kind)
                        try:service.telegram.edit_message_text(sender,notification['message_id'],service.notification_text(result_kind),{'inline_keyboard':[]})
                        except ProviderError:pass
                        changed=True
                elif len(parts)==3 and parts[2] in ('accept','deny'):
                    notification=service.store.notification(parts[1])
                    # Only the rows this message rendered (digest of the shown page).
                    proposals=service.offered_proposals(notification)[0] if notification else []
                    exact=(notification and notification['kind']=='preparation_proposed' and notification['state']=='sent'
                           and notification['generation']==generation and notification['chat_id']==sender
                           and notification['message_id']==message.get('message_id') and proposals)
                    if exact:
                        service.answer_preparation_proposal(notification,proposals,parts[2]=='accept')
                        changed=True
            elif authorized and isinstance(data,str) and data.startswith('p7m:'):
                # #818: the owner's yes/no for one candidate (or all open ones) of a
                # memory prompt.  Exact: this notification, sent, this chat and
                # message, within its TTL, and a candidate not yet decided here;
                # each is then re-validated against what the message showed.
                parts=data.split(':')
                if len(parts)==4 and parts[3] in ('accept','reject'):
                    notification=service.store.notification(parts[1])
                    binding=service.memory_binding(notification) if notification and notification['kind'] in MEMORY_PROMPT_KINDS else None
                    # #818 review: only a candidate the message showed complete is tappable.
                    open_=service.memory_open(binding) if binding and 'sent' in binding else []
                    targets=(open_ if parts[2]=='a' else
                             [int(parts[2])] if parts[2].isdigit() and int(parts[2]) in open_ else [])
                    exact=(targets and notification['state']=='sent'
                           and notification['generation']==generation and notification['chat_id']==sender
                           and notification['message_id']==message.get('message_id'))
                    if exact:
                        answered=service.answer_memory_prompt(notification,binding,targets,parts[3]=='accept')
                        if answered['expired']:
                            alert=(MEMORY_CANDIDATES_EXPIRED_TEXT,True)
                        else:
                            if answered['outdated']:alert=(MEMORY_CANDIDATES_OUTDATED_TEXT,True)
                            changed=True
            elif authorized and isinstance(data,str) and data.startswith('p7u:'):
                # #918: the owner's undo of one saved fact (or all still open) of a
                # saved notice.  Exact: this notification, sent, this chat and
                # message, within its 7-day window, and a fact not yet decided
                # here; each is then re-validated against the bound Memory row
                # and content digest (``retract_memory`` fails closed).
                parts=data.split(':')
                if len(parts)==3:
                    notification=service.store.notification(parts[1])
                    binding=service.memory_saved_binding(notification) if notification and notification['kind'] in MEMORY_SAVED_KINDS else None
                    open_=service.memory_saved_open(binding) if binding and 'sent' in binding else []
                    targets=(open_ if parts[2]=='a' else
                             [int(parts[2])] if parts[2].isdigit() and int(parts[2]) in open_ else [])
                    exact=(targets and notification['state']=='sent'
                           and notification['generation']==generation and notification['chat_id']==sender
                           and notification['message_id']==message.get('message_id'))
                    if exact and time.time()-float(binding['sent'])>MEMORY_SAVED_TTL_SECONDS:
                        service.expire_memory_saved(notification,binding)
                    elif exact:
                        decided=service.undo_memory_saved(notification['job_id'],binding,targets)
                        finished=not service.memory_saved_open(binding)
                        service.store.update_notification(notification['id'],'memory_undone' if finished else 'sent',
                                                       fingerprint=json.dumps(binding))
                        outdated=any(binding['done'][item]=='outdated' for item in decided)
                        LOG.info('memory undo by owner button work=%s decided=%s outdated=%s',notification['job_id'],len(decided),outdated)
                        footer=MEMORY_SAVED_OUTDATED_TEXT if outdated else None
                        try:service.telegram.edit_message_text(sender,notification['message_id'],
                                                            service.memory_saved_text(binding,footer),
                                                            service.legacy_memory_saved_markup(notification['id'],binding))
                        except ProviderError:pass
                        changed=True
            elif authorized and isinstance(data,str) and data.startswith('p7q:'):
                # #719: stop one watch from its own notification.  Exact: this
                # notification, sent, this chat and message; the same cancel
                # as Settings, so a running Work finishes as its own Work.
                parts=data.split(':')
                if len(parts)==3 and parts[2]=='stop':
                    notification=service.store.notification(parts[1])
                    job=service.store.job(notification['job_id']) if notification else None
                    preparation_id=prep.preparation_of((job or {}).get('request_key'))
                    exact=(notification and notification['kind']==prep.NOTIFY_KIND and notification['state']=='sent'
                           and notification['generation']==generation and notification['chat_id']==sender
                           and notification['message_id']==message.get('message_id') and preparation_id)
                    if exact:
                        try:
                            stopped=service.cancel_preparation(preparation_id)
                        except prep.PreparationRefusal:
                            stopped=None
                        if stopped is not None:
                            LOG.info('preparation cancel by owner button id=%s changed=%s',preparation_id,stopped['changed'])
                            alert=(msg('service.watching_has_stopped_no_more_updates') if stopped['changed'] else '이미 멈춘 지켜보기입니다.',False)
                            try:service.telegram.edit_message_reply_markup(sender,notification['message_id'],{'inline_keyboard':[]})
                            except ProviderError:pass
                            changed=True
            elif authorized and isinstance(data,str) and data.startswith('v1c:'):
                parts=data.split(':')
                if len(parts)==3 and parts[2] in ('approve','deny'):
                    notification=service.store.notification(parts[1])
                    attachment=service.store.context_attachment(notification['job_id']) if notification else None
                    current_model=service.store.config('model',{})
                    exact=(notification and notification['kind']=='context_approval_needed' and notification['state']=='sent'
                           and notification['generation']==generation and notification['chat_id']==sender
                           and notification['message_id']==message.get('message_id') and attachment
                           and attachment['assistant_id']==service.context_assistant_id(current_model)
                           and not attachment['approved'] and service.external_model(current_model))
                    if exact:
                        if parts[2]=='approve':
                            service.store.approve_context_attachment(notification['job_id'])
                            # This job did not run a model turn; returning it to
                            # the durable queue is a continuation of this exact
                            # owner-approved request, not a replay after failure.
                            with service.store.db() as db:
                                db.execute("UPDATE jobs SET status='queued',error=NULL,delivery='none' WHERE id=? AND status='failed'",(notification['job_id'],))
                            result_kind='approved'
                        else:
                            result_kind='denied'
                            service.store.remove_telegram_photo(notification['job_id'])
                        service.store.update_notification(notification['id'],result_kind)
                        try:service.telegram.edit_message_text(sender,notification['message_id'],
                            (msg('service.context_sharing_for_this_task_is') if parts[2]=='approve' else '이 작업의 컨텍스트 공유를 허용하지 않았습니다.'),
                            {'inline_keyboard':[]})
                        except ProviderError:pass
                        changed=True
            if authorized and isinstance(callback_id,str):
                text,show=alert or (msg('service.done') if changed else '',False)
                try:service.telegram.answer_callback_query(callback_id,text,show_alert=show)
                except ProviderError:pass
            if authorized and not changed and not alert and message.get('message_id'):
                # A tap that can no longer act removes its dead buttons rather than leaving them to be pressed again.
                try:service.telegram.edit_message_reply_markup(sender,message['message_id'],{'inline_keyboard':[]})
                except ProviderError:pass

    def ingest_update(self, update, generation):
        service=self.service
        with service.lock:
            cfg=service.store.config('telegram',{})
            if not cfg.get('enabled') or cfg.get('generation')!=generation: return
            update_id=update.get('update_id')
            if not isinstance(update_id,int) or update_id<cfg.get('cursor',0): return
            # #626: an edit is a source revision of an earlier message, never
            # a new request or a pairing attempt, so its text is not a turn.
            edited=not isinstance(update.get('message'),dict) and isinstance(update.get('edited_message'),dict)
            message=update['edited_message'] if edited else update.get('message',{})
            sender=message.get('from',{}).get('id')
            chat=message.get('chat',{})
            photo=message.get('photo') if not edited else None
            has_photo=isinstance(photo,list) and bool(photo)
            media_group_id=message.get('media_group_id') if has_photo else None
            text='' if edited else (message.get('text') or message.get('caption',''))
            replied=message.get('reply_to_message') if isinstance(message.get('reply_to_message'),dict) else {}
            replied_to=replied.get('message_id') if isinstance(replied.get('message_id'),int) else None
            photo_file_id=None
            if has_photo:
                candidates=[item for item in photo if isinstance(item,dict) and isinstance(item.get('file_id'),str)
                            and item.get('file_id')]
                def photo_rank(item):
                    width=item.get('width');height=item.get('height');size=item.get('file_size')
                    pixels=(width*height if isinstance(width,int) and not isinstance(width,bool)
                            and isinstance(height,int) and not isinstance(height,bool) else 0)
                    return pixels,size if isinstance(size,int) and not isinstance(size,bool) else 0
                chosen=max(candidates,key=photo_rank) if candidates else {}
                photo_file_id=chosen.get('file_id') or 'invalid-telegram-file-id'
                if not isinstance(text,str) or not text.strip():text='[사진 첨부]'
            private=chat.get('type')=='private' and isinstance(sender,int) and chat.get('id')==sender
            authorized=private and sender==cfg.get('user_id')
            paired=False
            # #897: a family bot pairs only with the Telegram user who created it.
            creator=cfg.get('pair_user_id')
            creator_bound=isinstance(creator,int) and not isinstance(creator,bool)
            if private and creator_bound and sender==creator and not isinstance(cfg.get('user_id'),int) \
                    and isinstance(text,str) and text:
                # #927: Telegram attests the creator, so their first message pairs the
                # bot; Telegram's own bot-creation screen sends a plain /start.
                cfg.update(user_id=sender,pair_code='',pair_expires=0)
                authorized=True
                paired=True
                if text.split(' ',1)[0]=='/start':text='/start'
            elif private and isinstance(text,str) and text.startswith('/start ') and cfg.get('pair_code') and time.time()<cfg.get('pair_expires',0) \
                    and (not creator_bound or sender==creator):
                if hmac.compare_digest(text[7:].strip().encode(),cfg['pair_code'].encode()):
                    cfg.update(user_id=sender,pair_code='',pair_expires=0)
                    authorized=True
                    paired=True
                    text='/start'
            if private and not authorized and isinstance(text,str) and text:
                service._log_unpaired_message()
            if paired:
                # #1344: Still welcomes the owner in their Telegram language.
                lang=message.get('from',{}).get('language_code')
                if isinstance(lang,str) and 0<len(lang)<=16:cfg['owner_language']=lang
            if authorized and isinstance(text,str) and len(text)>MAX_OWNER_MESSAGE_CHARS:
                # #832 (B14): an over-long message reaches the worker truncated,
                # with a note that says so, instead of being dropped silently.
                text=truncated_owner_message(text)
            # #173 slice B: never on keywords.  The owner's explicit ``/context <request>``
            # opens the saved-context choice here; a request in prose opens it when the
            # capability-need judgment selects ``saved-context`` (``hold_for_saved_context``).
            guided_request=service.guided_context_request(text) if authorized else None
            if guided_request is not None:text=guided_request
            guided_context_requested=guided_request is not None and bool(service.context_inbox().list())
            unqueued=[]
            # #1305: the chat whose location just continued a request; its keyboard goes at once.
            located_chat=None
            with service.store.db() as db:
                db.execute('BEGIN IMMEDIATE')
                guided_context=False
                if authorized and isinstance(text,str) and 0<len(text)<=MAX_OWNER_MESSAGE_CHARS:
                    album_key=(f'tg-album:{generation}:{sender}:{hashlib.sha256(media_group_id.encode()).hexdigest()}'
                               if isinstance(media_group_id,str) and 1<=len(media_group_id)<=128 else None)
                    album=(service.store.telegram.album(db,generation,sender,media_group_id) if album_key else None)
                    album_created=False
                    if album_key:
                        if album:
                            task_id=album['job_id']
                        else:
                            existing=db.execute('SELECT id,status,message FROM jobs WHERE request_key=?',(album_key,)).fetchone()
                            album_caption=''
                            if existing and existing['status']=='queued':
                                task_id=existing['id']
                                album_caption=existing['message'] if existing['message']!='[사진 첨부]' else ''
                            elif existing:
                                # A protocol-late album member must not mutate a Work already in flight.
                                album_key=None
                            else:
                                task_id=service.store.enqueue('[사진 첨부]',album_key,f'telegram:{generation}',sender,db=db,owner_typed=True)
                            if album_key:
                                service.store.telegram.add_album(db,task_id,generation,sender,media_group_id,album_caption)
                                album_created=True
                        if album_key:
                            current=service.store.telegram.album_caption(db,task_id)
                            captions=[part for part in (current['caption'] or '').split('\n\n') if part]
                            if text!='[사진 첨부]' and text not in captions:captions.append(text)
                            caption='\n\n'.join(captions)[:MAX_OWNER_MESSAGE_CHARS]
                            parsed_album_context=service.parse_context_request(caption)
                            if parsed_album_context:
                                event_ids,caption=parsed_album_context
                                service.store.attach_context(task_id,event_ids,service.context_assistant_id(),db)
                            elif guided_context_requested:
                                db.execute("UPDATE jobs SET status='awaiting_context' WHERE id=? AND status='queued'",(task_id,))
                                guided_context=True
                            service.store.telegram.touch_album(db,task_id,caption)
                            db.execute("UPDATE jobs SET message=? WHERE id=? AND status IN ('queued','awaiting_context')",
                                       (caption or '[사진 첨부]',task_id))
                            service.store.attach_telegram_photo(task_id,photo_file_id,db=db)
                            service.store.mark_telegram_photo_attached(task_id,db=db)
                            if album_created:
                                service.telegram_turns.record_source(task_id,sender,message.get('message_id'),db=db)
                                service.context_observations.note_text_source(db,task_id,message,generation)
                            text=caption or '[사진 첨부]'
                        else:
                            album_key=None
                    if not album_key:
                        parsed=service.parse_context_request(text)
                        if parsed:
                            event_ids,text=parsed
                            task_id=service.store.enqueue(text,f'tg:{generation}:{update_id}',f'telegram:{generation}',sender,db=db,owner_typed=True)
                            service.store.attach_context(task_id,event_ids,service.context_assistant_id(),db)
                        else:
                            task_id=service.store.enqueue(text,f'tg:{generation}:{update_id}',f'telegram:{generation}',sender,db=db,owner_typed=True)
                            if guided_context_requested:
                                db.execute("UPDATE jobs SET status='awaiting_context' WHERE id=?",(task_id,))
                                guided_context=True
                        if photo_file_id:
                            service.store.attach_telegram_photo(task_id,photo_file_id,db=db)
                            service.store.mark_telegram_photo_attached(task_id,db=db)
                        # #581: the owner's own message is the reaction target and reply anchor.
                        # #172: the message it replies to, if any, binds the owner's answer to what it showed.
                        service.telegram_turns.record_source(task_id,sender,message.get('message_id'),db=db,
                                                             reply_to=replied_to)
                        service.context_observations.note_text_source(db,task_id,message,generation)
                else:
                    task_id=None
                if authorized and not paired:
                    # #626: a location or an edit is recorded (or refused) in
                    # this same transaction as the cursor; it never becomes
                    # a Work, a model call or a reply - except (#774) a
                    # location answering a pending request, which continues
                    # the asking Work once.
                    answered=[]
                    service.context_observations.ingest_telegram(db,update,generation,sender,answered)
                    for answer in answered:
                        continued=service.continue_located_work(db,answer,message,generation,sender)
                        if continued is False:
                            unqueued.append(answer['job_id'])
                        elif continued:
                            located_chat=sender
                cfg['cursor']=update_id+1
                db.execute('INSERT INTO config VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',('telegram',json.dumps(cfg)))
            for job_id in unqueued:
                # #774 review: told once per asking Work, after the commit; the worker sends it.
                job=service.store.job(job_id)
                if job:service.queue_notification(job,'location_not_continued')
            if paired:
                # #1344: the /start reply (Still's welcome) is the connection proof; no automatic search.
                service.store.put('telegram_status',{'state':'connected','message':msg('service.your_account_is_connected_still_sent')})
            if authorized and service.is_natural_language(text) and task_id:
                if guided_context:
                    service.offer_telegram_context_choices(task_id,sender,generation)
                # An ordinary request gets no card: short Work answers in one
                # bubble; queued Work shows only the 👀 on the owner's message
                # and running Work gets native typing/draft presence
                # (`acknowledge_long_work`, #510/#581/#958).
            if authorized and task_id and not paired and not guided_context and not has_photo \
                    and service.is_natural_language(text):
                service.consider_steer(task_id,text)
        if located_chat is not None:
            service.withdraw_location_keyboard(located_chat)

    def ingest_reaction(self, reaction, generation):
        """The owner's emoji on an assistant message (#996): an observation, judged by the Judgment AI.

        #172: on a message that showed an item still waiting for the owner (a
        calendar preview, an approval prompt, a settings or memory or
        preparation proposal, a saved-memory notice), the reaction is the
        owner's act on it, read by the one owner-act judgment over the item(s)
        shown on that message; only a judged plain approve or decline settles
        one, through its own path.  Only on a message that showed nothing open
        does an answer's reaction go to self-review.  No emoji is mapped to a
        meaning in code.  Returns what it was applied to.
        """
        service=self.service
        cfg=service.store.config('telegram',{})
        chat=reaction.get('chat') if isinstance(reaction.get('chat'),dict) else {}
        user=(reaction.get('user') or {}).get('id') if isinstance(reaction.get('user'),dict) else None
        message_id=reaction.get('message_id')
        if not (cfg.get('enabled') and cfg.get('generation')==generation and chat.get('type')=='private'
                and isinstance(user,int) and user==cfg.get('user_id') and chat.get('id')==user and isinstance(message_id,int)):
            return None
        emoji=[item.get('emoji') for item in reaction.get('new_reaction') or ()
               if isinstance(item,dict) and isinstance(item.get('emoji'),str)][:3]
        if not emoji:return None
        with service.store.db() as db:
            notice=service.store.telegram.notice_on_message(db,user,message_id,MEMORY_SAVED_KINDS)
            turn=db.execute('SELECT job_id FROM telegram_turns WHERE chat_id=? AND reply_message_id=? ORDER BY created DESC LIMIT 1',
                            (user,message_id)).fetchone()
        target=dict(notice)['job_id'] if notice else (turn['job_id'] if turn else None)
        if target:
            with service.store.db() as db:
                db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                           (target,REACTION_EVENT,'observed',
                            json.dumps({'emoji':emoji,'on':'memory_notice' if notice else 'answer'},ensure_ascii=False),time.time()))
            LOG.info('owner reaction work=%s on=%s',target,'memory_notice' if notice else 'answer')
        # #172: a reaction on a message that showed an open item is the owner's act on that item,
        # judged off the poll thread; nothing is settled unless the judgment names it.
        if service.owner_acts.bound_to(self,user,generation,message_id):
            service.steer_spawn(lambda:service.owner_acts.answer_reaction(self,user,generation,message_id,emoji))
            return 'owner_act'
        if not target or notice:return None
        # #998: the answer the owner reacted to gets one self-review in the idle tick, under the
        # shared cap and pause; the Judgment AI decides whether there is anything to learn.
        try:om.enqueue_self_review(service.store,target,om.REVIEW_REACTION,'owner reaction on the answer: '+' '.join(emoji))
        except ValueError as exc:LOG.info('reaction self-review not queued (%s)',type(exc).__name__)
        return 'answer'

    def receive_telegram(self, now=None):
        """One receive poll; report a failure, except a transport failure still inside its grace (#65).

        A connection reset that the next poll recovers from is not a receiving failure: the
        owner's log and Telegram status stay quiet. A refusal (409, 401, other 4xx) is told at once.
        """
        service=self.service
        now=time.monotonic() if now is None else now
        # #208: an enabled bot stands down while work mode is on: no poll, and Settings says why.
        if work_mode.active(service.store):
            paused={'state':'paused','message':msg('work_mode.refused.telegram')}
            if service.store.config('telegram_status')!=paused:service.store.put('telegram_status',paused)
            return
        try:
            service.poll_telegram()
            service.mark_telegram_connected()
        except (ProviderError,ValueError) as exc:
            if transient_failure(exc):
                if service._receiving_unsteady_since is None:service._receiving_unsteady_since=now
                if now-service._receiving_unsteady_since<TELEGRAM_RECEIVE_GRACE_SECONDS:return
            service.store.put('telegram_status',{'state':'error','message':msg('service.check_the_telegram_connection_receiving_will')})
            service._receiving_failure=service._log_receiving_failure(exc,service._receiving_failure)
            return
        service._receiving_unsteady_since=None
        if service._receiving_failure:LOG.info('telegram: receiving again')
        service._receiving_failure=None

    def _log_unpaired_message(self):
        """#1543: say, at most every ten minutes, that an unpaired chat was ignored."""
        service=self.service
        now=time.time()
        if now-getattr(service,'_unpaired_logged_at',0)<600:return
        service._unpaired_logged_at=now
        LOG.info('telegram: a message from a chat that is not paired with this Still was ignored; '
                 'open the pairing link from setup on this computer and press Start')

    def poll_telegram(self):
        service=self.service
        with service.lock:
            cfg=service.store.config('telegram',{})
            token=service.store.secret('telegram_token')
        if not cfg.get('enabled') or not token:
            # With no active update stream this is the explicit release path;
            # a failed enabled poll below must keep the marker until a later
            # successful poll confirms the album's quiet window.
            service.settle_expired_telegram_photo_albums()
            return
        updates=service.telegram.get_updates(cfg.get('cursor',0), timeout=1)
        for update in sorted(updates,key=lambda u:u.get('update_id',0)):
            # #208 review: work mode switched on while this poll was in flight: nothing more is
            # ingested and the cursor stays, so Telegram keeps these for after work mode.
            if work_mode.active(service.store):return
            control=None
            if isinstance(update.get('callback_query'),dict):
                control=lambda:service.ingest_callback(update['callback_query'],cfg['generation'])
            elif isinstance(update.get('stopped_message_generation'),dict):
                # #581: the owner pressed Stop on a draft.
                control=lambda:service.ingest_stop(update['stopped_message_generation'],cfg['generation'])
            elif isinstance(update.get('managed_bot'),dict):
                # #897: a family member created their bot through this bot's Managed Bots link.
                control=lambda:service.ingest_managed_bot(update['managed_bot'])
            elif isinstance(update.get('message_reaction'),dict):
                # #996: the owner's emoji on an assistant message.
                control=lambda:service.ingest_reaction(update['message_reaction'],cfg['generation'])
            if control:
                control()
                # Callback updates must advance the durable cursor too, or
                # Telegram will resend them after every restart.
                with service.lock:
                    current=service.store.config('telegram',{})
                    if current.get('generation')==cfg['generation'] and isinstance(update.get('update_id'),int) and update['update_id']>=current.get('cursor',0):
                        current['cursor']=update['update_id']+1
                        service.store.put('telegram',current)
            else:service.ingest_update(update,cfg['generation'])
        service.retry_family_handovers()
        service.settle_expired_telegram_photo_albums()

    def _notification_since(self, notification):
        """When the notification's content began: its Work's start, or its own queueing (#208 review)."""
        job=self.service.store.job(notification.get('job_id')) if notification.get('job_id') else None
        times=[value for value in ((job or {}).get('created'),notification.get('created'))
               if isinstance(value,(int,float)) and not isinstance(value,bool)]
        return min(times) if times else None

    def cancel_pending_deliveries(self):
        """#208 review: cancel every queued Telegram reply and notification as work mode goes on.

        Done at the switch, not left to the next delivery tick, so turning work
        mode off again before that tick cannot send them.  Returns the counts.
        """
        service=self.service
        with service.lock,service.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            replies=db.execute("UPDATE jobs SET delivery='cancelled' WHERE delivery='pending' AND substr(channel,1,?)=?",
                               (len(self.channel_prefix),self.channel_prefix)).rowcount
            notices=db.execute("UPDATE telegram_notifications SET state='cancelled' WHERE state='queued' AND surface=?",
                               (self.name,)).rowcount
        return replies,notices

    def settle_expired_telegram_photo_albums(self, now=None):
        """Release persisted album Works after their short update-collection window."""
        service=self.service
        cutoff=(time.time() if now is None else now)-TELEGRAM_PHOTO_ALBUM_SETTLE_SECONDS
        service.store.telegram.settle_albums(cutoff)

    def deliver_one(self):
        # Mark before send. A lost response may mean delivered; never auto-resend.
        # #123: a reply that never reached Telegram waits out its backoff before the next try.
        service=self.service
        if time.monotonic()<service._unsent_retry_at:return
        with service.lock:
            cfg=service.store.config('telegram',{})
            with service.store.db() as db:
                db.execute('BEGIN IMMEDIATE')
                # #213: only this surface's replies; another surface's pending reply is never claimed here.
                row=db.execute("SELECT * FROM jobs WHERE delivery='pending' AND substr(channel,1,?)=? ORDER BY created LIMIT 1",
                               (len(self.channel_prefix),self.channel_prefix)).fetchone()
                if not row:return
                job=dict(row)
                # #208: in work mode a Telegram reply is not sent (cancelled, never sent later); the Work keeps it.
                # #208 review: nor one whose Work was under way while work mode was on.
                allowed=(cfg.get('enabled') and job['channel']==f"telegram:{cfg.get('generation')}" and job['chat_id']==cfg.get('user_id')
                         and not work_mode.was_on_since(service.store,job.get('created')))
                db.execute('UPDATE jobs SET delivery=? WHERE id=?',('sending' if allowed else 'cancelled',job['id']))
            if not allowed:return
            blocked=service.store.blocked_delivery_reply(job['id'])
            # The owner-language cause when one was recorded (#598 X1); the
            # technical ``error`` remains the Task-detail record.
            # #1470: a Work waiting for the owner's approval is not "unconfirmed".  #173 slice C: nor is one
            # whose drafts or proposals wait on Still's own preview, unless something else is left to say.
            # #240: the one typed waiting state (``work_waits_for_owner``) every surface reads.
            waits=service.work_waits_for_owner(job)
            text=blocked or service.telegram_result_text(job['response'],
                                                      job.get('owner_cause') or job['error'],job.get('status'),
                                                      verified=job.get('owner_verified'),note=job.get('owner_note'),
                                                      waiting=service.wait_explains_outcome(job,waits))
            # #659: a prepared answer arrives without an owner turn; say what it is for.
            text=service.preparation_reply_prefix(job)+text
            try:route_options=service.usage_limit_route_options(job)
            except Exception as exc:
                LOG.debug('usage-limit route recovery unavailable kind=%s',type(exc).__name__)
                route_options=[]
            provenance=service.store.turn_provenance(job['id']) or {}
            if (not blocked and provenance.get('failure_class')=='usage-limit' and not route_options):
                text+='\n다른 AI 연결은 Still 웹의 AI 설정에서 선택할 수 있어요.'
            # #818: whether this Work left pending memory candidates; its one ask
            # follows the reply (#836: the ask is the ask, no line is appended).
            try:memory_pending=not blocked and bool(service.pending_memory_candidates(job['id'])[0])
            except Exception as exc:
                LOG.warning('memory candidate read failed work=%s kind=%s',job['id'],type(exc).__name__)
                memory_pending=False
            # #918: whether this Work saved facts at once; its one notice (with undo) follows the reply.
            try:memory_saved=not blocked and bool(service.work_saved_memories(job['id'])[0])
            except Exception as exc:
                LOG.warning('saved memory read failed work=%s kind=%s',job['id'],type(exc).__name__)
                memory_saved=False
            # #581: one durable reply, valid Telegram HTML (no leaked `**`),
            # anchored to the owner turn only when that clarifies it, with
            # bounded recovery controls only when the turn did not succeed.
            # Presentation choices are computed before the send; none of them
            # can turn a delivered reply into a second send.
            try:
                anchor=service.reply_anchor(job)
                controls=service.reply_controls(job,bool(blocked))
            except Exception as exc:  # presentation must never block the reply
                LOG.debug('telegram reply presentation failed: %s',type(exc).__name__)
                anchor,controls=None,()
            markup=reply_controls_markup(job['id'],controls,route_options=route_options)
            # #992: a reply without inline controls takes away a location keyboard whose request ended.
            # #1582: consume this Work's offer even when inline controls win.
            removal=None
            try:offer=service.location_offer_markup(job)
            except Exception as exc:
                LOG.info('location offer skipped work=%s kind=%s',job['id'],type(exc).__name__)
                offer=None
            if markup:offer=None
            if not markup:
                if offer:removal=offer
                else:
                    try:removal=service.location_keyboard_removal(job['chat_id'])
                    except Exception:removal=None
            message_id=None
            try:
                try:
                    result=service.telegram.send_message(job['chat_id'],render_telegram_html(text),markup or removal,
                                                      parse_mode='HTML',reply_to=anchor)
                except TelegramRejected as exc:
                    # Telegram answered that it could not parse the entities:
                    # a definite non-delivery, so one plain-text send cannot
                    # duplicate anything.  Every other failure stays unknown.
                    if not exc.entity_parse_error:raise
                    result=service.telegram.send_message(job['chat_id'],text,markup or removal,reply_to=anchor)
                message_id=result.get('message_id') if isinstance(result,dict) else None
                status='sent'
                service._unsent_backoff=0
                if offer:service.store.put(service.LOCATION_KEYBOARD_KEY,{'chat_id':job['chat_id']})
                elif removal:service.store.put(service.LOCATION_KEYBOARD_KEY,{})
            except TelegramNotSent:
                # #123: nothing reached Telegram, so the reply stays queued and is sent once it is
                # reachable; what follows a settled send (notices, outcome) waits for that send.
                with service.store.db() as db:
                    db.execute("UPDATE jobs SET delivery='pending' WHERE id=? AND delivery='sending'",(job['id'],))
                service._unsent_backoff=min(TELEGRAM_UNSENT_BACKOFF_MAX,max(TELEGRAM_UNSENT_BACKOFF_FIRST,service._unsent_backoff*2))
                service._unsent_retry_at=time.monotonic()+service._unsent_backoff
                LOG.warning('telegram delivery waiting work=%s retry_in=%ss',job['id'],service._unsent_backoff)
                return
            except work_mode.WorkModeRefused:
                # #208 review: refused before hand-off, so definitely not delivered (never sent later).
                status='cancelled'
            except ProviderError as exc:
                status='unknown'
                LOG.warning('telegram delivery unknown work=%s kind=%s status=%s',job['id'],type(exc).__name__,exc.status)
            with service.store.db() as db:
                db.execute('UPDATE jobs SET delivery=? WHERE id=?',(status,job['id']))
            # #996: every delivered answer, so an owner reaction on it finds its Work.
            if isinstance(message_id,int):
                service.telegram_turns.record_reply(job['id'],job['chat_id'],message_id)
            # #818/#836: after a reply confirmed sent (never 'unknown'), the Work's
            # one ask about its pending memory proposals.
            try:
                if memory_pending and status=='sent':service.queue_memory_candidates(job)
            except Exception as exc:
                LOG.warning('memory candidate offer failed work=%s kind=%s',job['id'],type(exc).__name__)
            # #918: after the reply - delivered ``sent`` or ``unknown`` (review: a save is never untold; the
            # notice cannot duplicate the reply and each undo is exact to its own message) - release the Work's
            # one notice of what it remembered, with undo.  A crash before this line is covered by the sweep.
            try:
                if memory_saved:service.release_memory_saved(job)
            except Exception as exc:
                LOG.warning('memory saved notice failed work=%s kind=%s',job['id'],type(exc).__name__)
            # #233: a working-notes notice a self-review saved before this reply settled follows it now.
            try:service.release_working_notes(job['id'])
            except Exception as exc:
                LOG.warning('working notes notice release failed work=%s kind=%s',job['id'],type(exc).__name__)
        # #835/#858: delivery is durable before the truth-gated outcome
        # presentation. This optional remote Judgment call runs outside the
        # service lock, so it cannot stall polling, Stop updates or work.
        service._present_outcome(job,delivered=status=='sent',blocked=bool(blocked),awaiting_owner=memory_pending)
        service.presence.pop(job['id'],None)
