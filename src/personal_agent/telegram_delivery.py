"""Telegram lifecycle and delivery coordination extracted from AgentService (#21).

AgentService remains the composition root and owns every mutable value. The
existing TelegramSurface still handles ingestion, callbacks and Telegram sends;
this collaborator retains the service-side lifecycle, helpers and multi-surface
delivery coordination. Calls between methods go through the service so existing
entry points and patch targets remain live. Authority, approvals, budgets,
recovery and database semantics are unchanged. Shared constants remain in
quickstart_service; the service loads this module lazily like TelegramSurface.
"""

import secrets
import time

from . import work_mode
from . import work_status as ws
from .conversation_handoff import TelegramChannel
from .conversation_projection import terminal_text
from .messages import msg
from .providers import ProviderError
from .quickstart_service import (BROWSER_APPROVAL_PROMPT, LOCATION_NOT_CONTINUED_TEXT,
                                 LOG, SUPERSEDED_WORK_ERROR, TELEGRAM_VERIFICATION_QUERY)


class TelegramDelivery:
    """Existing Telegram lifecycle and delivery over one service's state."""

    def __init__(self, service):
        self.service = service

    def telegram_call(self,token,method,body):
        """Thin delegation to the transport seam for an unstored bot token."""
        service=self.service
        return service.telegram.call_with_token(token,method,body)

    def telegram_method(self, method, body):
        """Thin delegation to the transport seam for the stored bot token."""
        service=self.service
        return service.telegram.call(method,body)

    def telegram_generation(self):
        service=self.service
        generation=service.store.config('telegram',{}).get('generation')
        return generation if isinstance(generation,str) else ''

    def connect_telegram(self, body):
        service=self.service
        token=body.get('token','')
        if not isinstance(token,str) or not 10<=len(token)<=300 or not all(c.isalnum() or c in ':_-' for c in token):
            raise ValueError(msg('quickstart_service.enter_bot_token'))
        me=service.telegram.get_me(token)
        webhook=service.telegram.get_webhook_info(token)
        username=me.get('username') if isinstance(me,dict) else None
        if not isinstance(username,str) or not 5<=len(username)<=64 or not username.replace('_','').isalnum():
            raise ProviderError(msg('quickstart.telegram_bot_invalid'))
        if not isinstance(webhook,dict):
            raise ProviderError(msg('quickstart.telegram_webhook_unchecked'))
        if webhook.get('url'):
            raise ValueError(msg('quickstart_service.bot_webhook_active'))
        bot_name=service._bot_name_of(me)
        with service.lock:
            service.store.secret('telegram_token',token)
            service.store.put('telegram',{'enabled':True,'mode':'owner-token','username':username,'bot_name':bot_name,'generation':secrets.token_hex(12),'cursor':0,'user_id':None})
            service.store.put('telegram_status',{'state':'pairing','message':msg('service.connect_your_personal_telegram_account')})
        return service.pair_telegram()

    @staticmethod
    def _bot_name_of(me):
        name=me.get('first_name') if isinstance(me,dict) else None
        return ' '.join(name.split())[:64] if isinstance(name,str) and name.strip() else None

    def apply_still_profile(self):
        """#1337: give the owner's own bot Still's name, photo and descriptions, once per bot.

        Best-effort and off the request path.  Pinned to the bot that was
        connected when it started: every call uses that token, and the
        marker and stored name are written only if that bot is still the
        connected one.
        """
        service=self.service
        cfg=service.store.config('telegram',{})
        username=cfg.get('username')
        token=service.store.secret('telegram_token')
        if not (cfg.get('enabled') and isinstance(username,str) and username and token):return None
        marker=service.store.config('telegram_profile',{})
        # #1541: a failed photo is retried alone; name and descriptions are never reapplied.
        photo_only=marker.get('username')==username
        if photo_only and (marker.get('steps') or {}).get('photo')!=False:return None
        pinned=TelegramChannel(lambda:service.telegram_transport,lambda:token,
                               lambda:service.telegram_file_transport,lambda:service.telegram_upload_transport,
                               guard=lambda:work_mode.refuse(service.store,'telegram'))
        still=__import__('personal_agent.still_profile',fromlist=['apply'])
        if photo_only:
            photo=still.apply_photo(pinned)
            with service.lock:
                current=service.store.config('telegram_profile',{})
                if current.get('username')==username and service.store.secret('telegram_token')==token:
                    current['steps']={**(current.get('steps') or {}),'photo':photo}
                    service.store.put('telegram_profile',current)
            return {'photo':photo}
        done=still.apply(pinned)
        with service.lock:
            current=service.store.config('telegram',{})
            if current.get('username')!=username or service.store.secret('telegram_token')!=token:
                return done
            if done.get('name'):
                current['bot_name']=still.NAME
                service.store.put('telegram',current)
            if any(done.values()):
                service.store.put('telegram_profile',{'username':username,'profile':'still','applied_at':time.time(),'steps':done})
        return done

    def pair_telegram(self):
        service=self.service
        # #208: pairing completes through the receive loop, which work mode stops.
        work_mode.refuse(service.store,'telegram')
        with service.lock:
            cfg=service.store.config('telegram',{})
            if not cfg.get('enabled'): raise ValueError(msg('quickstart_service.connect_bot_first'))
            code=secrets.token_urlsafe(24)
            cfg['pair_code']=code
            cfg['pair_expires']=time.time()+600
            service.store.put('telegram',cfg)
            return {'url':f"https://t.me/{cfg['username']}?start={code}",'expires_in':600}

    def queue_telegram_connection_verification(self):
        """Queue one harmless, idempotent proof for a paired owner chat."""
        service=self.service
        with service.lock:
            cfg=service.store.config('telegram',{})
            subscription=service.store.config('subscription_engine',{})
            generation=cfg.get('generation','')
            if not (cfg.get('enabled') and isinstance(cfg.get('user_id'),int) and generation):
                return {'queued':False,'reason':'paired Codex Telegram connection is required'}
            if subscription.get('id')!='codex':
                # #132 keeps the automatic search proof Codex-only; other
                # routes must not incur an unrequested provider call, so say so.
                message=msg('quickstart_service.telegram_connected_manual_check')
                service.store.put('telegram_status',{'state':'connected','message':message,'verification':'skipped-non-codex'})
                return {'queued':False,'reason':'automatic verification runs only on the Codex route','message':message}
            request_key=f'telegram-verify:{generation}'
            job_id=service.store.enqueue(TELEGRAM_VERIFICATION_QUERY,request_key,f'telegram:{generation}',cfg['user_id'])
            service.store.put('telegram_status',{'state':'verifying','message':msg('service.agentos_is_automatically_checking_the_connection')})
            return {'queued':True,'job_id':job_id}

    def disconnect_telegram(self):
        service=self.service
        with service.lock:
            cfg=service.store.config('telegram',{})
            cfg.update(enabled=False,pair_code='',user_id=None)
            service.store.put('telegram',cfg)
            service.store.secret('telegram_token','')
            service.store.put('telegram_status',{'state':'disabled','message':msg('service.disconnected_telegram')})
        return {'ok':True}

    @staticmethod
    def task_card_text(message, state):
        labels={state:msg('state.'+state) for state in ('queued','running','succeeded','failed','cancelled','interrupted','unknown')}
        # A request can itself contain a secret or pasted document excerpt.
        # Cards are status controls, never a copy of user-provided content.
        if state=='queued':return msg('card.queued')
        if state=='running':return msg('card.running')
        # #581: no "처리가 끝났습니다 → 결과 상태 보기" framing; the answer
        # itself is the next bubble and speaks for the turn.
        if state=='succeeded':return msg('card.succeeded')
        # The card sits directly above the terminal bubble.  A partial turn
        # must not be announced here as a finished result the bubble then
        # refuses to show.
        if state=='partial':return msg('line.only_some_steps_were_completed_see')
        if state=='interrupted':return msg('card.interrupted')
        if state=='awaiting_connection':return msg('card.awaiting_connection')
        if state=='superseded':return SUPERSEDED_WORK_ERROR
        return msg('card.state',state=labels.get(state,state))

    @staticmethod
    def notification_text(kind):
        return {'completed':msg('notice.completed'),
                'failed':msg('notice.failed'),
                'approval_needed':msg('service.approval_is_required_to_send_connected'),
                'context_approval_needed':msg('service.approval_for_this_task_is_required'),
                'approved':msg('notice.document_approved'),
                'denied':msg('notice.document_denied'),
                'browser_approval_needed':BROWSER_APPROVAL_PROMPT,
                'browser_approved':msg('notice.step_approved'),
                'browser_denied':msg('notice.step_denied'),
                'preparation_accepted':msg('notice.preparation_accepted'),
                'preparation_denied':msg('notice.preparation_denied'),
                'preparation_cancelled':msg('notice.preparation_cancelled'),
                'preparation_already_settled':msg('notice.preparation_already_settled'),
                'preparation_expired':msg('notice.preparation_expired'),
                'location_not_continued':LOCATION_NOT_CONTINUED_TEXT}.get(kind,msg('notice.generic'))

    def queue_notification(self, job, kind, fingerprint=None):
        """#213: queued on the surface the Work came from; a Work of no paired surface gets none."""
        service=self.service
        surface=service.surface_of(job)
        return surface.queue_notification(job, kind, fingerprint) if surface is not None else None

    def deliver_notification(self):
        """One queued notice per surface (#213: each claims only its own); True when one was handled."""
        service=self.service
        handled=service._cancel_orphan_deliveries()
        for surface in service.surfaces:
            handled=bool(surface.deliver_notification()) or handled
        return handled

    def _cancel_orphan_deliveries(self):
        """#213: a pending reply or queued notice that no composed surface claims is cancelled, never sent.

        Before #213 Telegram's delivery cancelled every row it did not own; with
        claims partitioned per surface, rows of a surface that is not composed
        (none today) are settled here instead of waiting forever.  True when one was.
        """
        service=self.service
        prefixes=[surface.channel_prefix for surface in service.surfaces if surface.channel_prefix]
        names=[surface.name for surface in service.surfaces]
        with service.lock,service.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            replies=db.execute("UPDATE jobs SET delivery='cancelled' WHERE delivery='pending' AND NOT ("
                               +(' OR '.join(['substr(channel,1,?)=?']*len(prefixes)) or '0')+')',
                               [value for prefix in prefixes for value in (len(prefix),prefix)]).rowcount
            notices=service.store.telegram.cancel_orphan_notifications(db,names)
        return bool(replies or notices)

    @staticmethod
    def task_card_markup(job_id, state):
        # #581: a succeeded card has served its purpose and keeps no control;
        # other terminal states keep one on-demand 상세 (technical detail).
        if state=='succeeded':return {'inline_keyboard':[]}
        progress_label=msg('button.progress') if ws.is_active(state) else msg('button.details')
        buttons=[{'text':progress_label,'callback_data':f'p7v:{job_id}'}]
        if state=='queued':
            buttons.append({'text':msg('button.cancel_work'),'callback_data':f'p7c:{job_id}'})
        return {'inline_keyboard':[buttons]}

    @staticmethod
    def _callback_authorized(cfg, generation, sender, chat):
        """A tap from the paired owner's private chat of the current bot generation."""
        return bool(cfg.get('enabled') and cfg.get('generation')==generation and isinstance(sender,int)
                    and sender==cfg.get('user_id') and chat.get('type')=='private' and chat.get('id')==sender)

    @staticmethod
    def _log_receiving_failure(exc, previous):
        """#1543: log a Telegram receiving failure once per cause; return the cause."""
        status=getattr(exc,'status',None)
        cause=(type(exc).__name__,status)
        if cause!=previous:
            hint=' - another Still is receiving this bot; stop it or give each computer its own bot' if status==409 else ''
            LOG.warning('telegram: receiving failed (%s%s)%s',cause[0],f' {status}' if status else '',hint)
        return cause

    @staticmethod
    def telegram_result_text(response, error=None, outcome=None, verified=None, note=None, waiting=False):
        """The one terminal bubble; the truth rules live in conversation_projection (#476/#488/#510/#598/#847)."""
        return terminal_text(response,error,outcome,verified=verified,note=note,waiting=waiting)

    def deliver_one(self):
        """One pending terminal reply per surface (#213: each claims only its own channels)."""
        service=self.service
        service._cancel_orphan_deliveries()
        for surface in service.surfaces:
            surface.deliver_reply()

    def mark_telegram_connected(self):
        service=self.service
        cfg=service.store.config('telegram',{})
        if cfg.get('enabled') and isinstance(cfg.get('user_id'),int):
            # Keep the more specific skip explanation while it is still true.
            current=service.store.config('telegram_status') or {}
            if current.get('verification')=='skipped-non-codex' and service.store.config('subscription_engine',{}).get('id')!='codex':
                return
            service.store.put('telegram_status',{'state':'connected','message':msg('service.your_personal_telegram_account_is_connected')})
