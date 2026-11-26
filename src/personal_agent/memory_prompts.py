"""Memory candidate prompts extracted from AgentService (#21), and the exact undo of a direct save.

AgentService remains the composition root and owns all mutable state. Calls
between methods go through the service so forwarding entry points and patch
targets stay live. Approval, undo, delivery, recovery and database semantics
are unchanged. Shared constants stay in quickstart_service; the service loads
this collaborator lazily like GoogleConnections and WorkExecutor. Classmethod
forwarders pass the service class through so its helper overrides remain live.
"""

import hmac
import json
import time

from .agent_runtime import MEMORY_OWNER
from .memory_service import MemoryService
from .messages import msg
from .providers import ProviderError
from .quickstart_service import (
    LESSON_NOTICE_RETELL_SECONDS,
    LOG,
    MEMORY_BUTTON_NO,
    MEMORY_BUTTON_YES,
    MEMORY_CANDIDATES_EXPIRED_TEXT,
    MEMORY_CANDIDATES_HEADER,
    MEMORY_CANDIDATES_KIND,
    MEMORY_CANDIDATES_OUTDATED_TEXT,
    MEMORY_CANDIDATES_SETTLED,
    MEMORY_CANDIDATES_SHOWN,
    MEMORY_CANDIDATES_SWEEP_SECONDS,
    MEMORY_CANDIDATES_TTL_SECONDS,
    MEMORY_CANDIDATE_CHARS,
    MEMORY_CANDIDATE_STATUS,
    MEMORY_KEY_SHAPED,
    MEMORY_PROMPT_KINDS,
    MEMORY_UNDO_TOOL,
    WORKING_LESSON_NOTICE_KINDS,
)



def retract_saved(store, job_id, item, asked=False):
    """Retract one direct save exactly and say what is in use again (#918; asking and 내 기록 share it, #955, #962).

    ``item`` is ``{'id', 'digest', 'key'?}``: the row must still be current with that digest
    (``retract_memory`` fails closed).  The retraction and its ``memory_undo`` Evidence event are one
    transaction (#918 review P2).  Returns ``status`` (``retracted`` or ``outdated``), ``restored`` (the
    superseded row back in use), ``restored_ended`` (rows under other keys the save had ended, #220),
    both only while they still hold (Codex review), and ``expired`` (restored rows past their last day).
    """
    evidence={'id':item['id'],'memory_key':item.get('key'),'auto_saved':True,**({'asked':True} if asked else {})}
    record={'job_id':job_id,'tool':MEMORY_UNDO_TOOL,'status':'recorded',
            'detail':{'host_action':MEMORY_UNDO_TOOL,'evidence':evidence}}
    try:
        receipt=store.retract_memory(MEMORY_OWNER,item['id'],item['digest'],record=record)
    except ValueError as exc:
        LOG.info('memory undo refused work=%s kind=%s',job_id,type(exc).__name__)
        return {'status':'outdated','restored':None,'restored_ended':[],'expired':[]}
    back=[memory_id for memory_id in [(receipt.get('restored') or {}).get('id'),*(receipt.get('restored_ended') or ())] if memory_id]
    holding={row['id'] for row in MemoryService(store,private_read_sink=MemoryService.NO_EGRESS_GUARD).holding(
        [store.memory(memory_id,MEMORY_OWNER,current_only=False) or {'id':None} for memory_id in back])}
    restored=(receipt.get('restored') or {}).get('id')
    return {'status':'retracted','restored':restored if restored in holding else None,
            'restored_ended':[memory_id for memory_id in receipt.get('restored_ended') or () if memory_id in holding],
            'expired':[memory_id for memory_id in back if memory_id not in holding]}

class MemoryPrompts:
    """Existing memory prompts over one service's state."""

    def __init__(self, service):
        self.service = service

    def pending_memory_candidates(self, job_id):
        """``(shown, remaining)``: this Work's pending MemoryCandidates, oldest first (#818).

        At most ``MEMORY_CANDIDATES_SHOWN`` are shown; the rest are only
        counted (the owner decides them in 내 기록).
        """
        service=self.service
        rows=sorted(service.store.memory_candidates(MEMORY_OWNER,job_id,limit=101),key=lambda row:(row['created'],row['id']))
        return rows[:MEMORY_CANDIDATES_SHOWN],max(0,len(rows)-MEMORY_CANDIDATES_SHOWN)

    def pending_candidate_works(self):
        """A test for "this Work id has a pending MemoryCandidate" (#818 review).

        Candidates carry the Work only as its opaque binding (``work_ref``),
        so a Work id is compared through ``QuickStore._work_binding``.
        """
        service=self.service
        keys,offset=set(),0
        while True:
            page=service.store.memory_candidates(MEMORY_OWNER,limit=101,offset=offset)
            keys|={row['work_ref'][len('workref:'):] for row in page}
            if len(page)<101:break
            offset+=101
        return lambda work_id:bool(keys) and isinstance(work_id,str) and service.store._work_binding(work_id) in keys

    def queue_memory_candidates(self, job):
        """Ask the paired owner about this Work's pending MemoryCandidates: one ask per Work (#818, #836).

        Queued only after the reply was delivered ``sent``; it is bound when it
        is sent to every candidate of the Work still pending then, whoever
        proposed it (the worker or #805 upkeep).
        """
        service=self.service
        shown,more=service.pending_memory_candidates(job['id'])
        if shown:service.queue_notification(job,MEMORY_CANDIDATES_KIND,fingerprint=json.dumps(
            {'candidates':[[row['id'],row['content_digest']] for row in shown],'more':more}))

    def queue_upkeep_memory_candidates(self, work_id):
        """After #805 upkeep: its candidates join the Work's one ask (#836).

        Only for a Telegram Work whose reply was delivered ``sent``; before
        that, the reply's own ask is bound at send time to everything pending.
        No ask yet: this is the first.  An ask still queued binds them when it
        is sent.  An ask sent, listed or answered: ``join_memory_prompt``.  An
        ask whose buttons expired or whose delivery is unknown leaves them to
        내 기록.  Under ``self.lock``, like the taps and the sends.
        """
        service=self.service
        with service.lock:
            job=service.store.job(work_id)
            if not job or job.get('delivery')!='sent':return
            row=service.store.notification_of(work_id,MEMORY_CANDIDATES_KIND)
            if row is None:
                service.queue_memory_candidates(job)
                return
            state=row['state']
            if state=='cancelled':
                # Nothing was left to ask when the reply's ask was due: this is the Work's first ask.
                shown,more=service.pending_memory_candidates(work_id)
                if shown:service.store.update_notification(row['id'],'queued',fingerprint=json.dumps(
                    {'candidates':[[item['id'],item['content_digest']] for item in shown],'more':more}))
                return
            binding=service.memory_binding(row)
            if state in ('sent','memory_listed','memory_decided') and binding and 'sent' in binding:
                service.join_memory_prompt(row,binding)

    @staticmethod
    def memory_binding(notification):
        """A memory prompt's recorded binding (queued ids, or the send-time binding with ``sent``), or None (#818)."""
        try:binding=json.loads((notification or {}).get('fingerprint') or '')
        except (TypeError,ValueError):return None
        return binding if isinstance(binding,dict) and isinstance(binding.get('candidates'),list) else None

    def memory_candidate_still(self, job_id, candidate_id, content_digest):
        """The candidate while it is still pending with exactly that content, else None (#818)."""
        service=self.service
        row=service.store.memory_candidate(candidate_id,MEMORY_OWNER,job_id) if isinstance(candidate_id,str) else None
        return row if row and row['state']=='pending' and hmac.compare_digest(str(row['content_digest']),str(content_digest)) else None

    def current_memory_ref(self, memory_key):
        """``[id, content_digest]`` of the current Memory under ``memory_key``, or None (#818)."""
        service=self.service
        current=service.store.current_memory(memory_key,MEMORY_OWNER)
        return [current['id'],current['content_digest']] if current else None

    def memory_display(self, text, limit):
        """``(shown, complete)``: ``text`` as a prompt shows it, and whether that is the whole value unchanged (#818 review).

        Whitespace is collapsed, stored secrets and credential shapes are
        removed and the result is bounded; only when none of that changed the
        text does the owner see exactly the value a tap would approve.
        """
        service=self.service
        text=str(text or '')
        shown=service._redact_known_secrets(' '.join(text.split()))
        complete=shown==text and len(shown)<=limit
        return (shown if len(shown)<=limit else shown[:limit-1]+'…'),complete

    def memory_fact(self, text, limit, key=None):
        """``(shown, complete)``: one fact as the owner reads it in the ask, never a memory key (#836).

        The ask shows the value only.  A value that *is* the candidate's own
        memory key (``food_preference.rolls_and_rolls_sushi``) is shown as its
        words and is never complete, so it cannot be tapped: the owner would
        otherwise accept a stored value they were not shown (#838 review).
        Anything else - including ordinary dotted values such as
        ``amazon.com`` - is shown as ``memory_display``.  Presentation only.
        """
        service=self.service
        text=str(text or '')
        stripped=text.strip()
        keys={str(key or ''),str(key or '').removeprefix('profile.')}-{''}
        if stripped in keys and MEMORY_KEY_SHAPED.fullmatch(stripped):
            return service.memory_display(stripped.rsplit('.',1)[-1].replace('_',' '),limit)[0],False
        return service.memory_display(text,limit)

    def memory_prompt_item(self, row):
        """One candidate as an ask binds it (#818, #836).

        Its id, content digest and key, the current Memory under the key (a
        yes must still find it), and ``tap``: set only when the ask shows the
        value complete and unchanged (#818 review); the key is never shown.
        """
        service=self.service
        return {'id':row['id'],'digest':row['content_digest'],'key':row['memory_key'],
                'current':service.current_memory_ref(row['memory_key']),
                'tap':bool(service.memory_fact(row['content'],MEMORY_CANDIDATE_CHARS,row['memory_key'])[1])}

    def bind_memory_prompt(self, notification, now):
        """What a memory ask shows, bound at send time; None when nothing is pending (#818, #836).

        The Work's one ask binds every candidate of the Work still pending
        when it is sent, oldest first, up to ``MEMORY_CANDIDATES_SHOWN``.  (A
        former upkeep prompt, #818 review, binds only its queued ids.)
        """
        service=self.service
        queued=service.memory_binding(notification)
        if not queued or 'sent' in queued:return None
        shown,more=service.pending_memory_candidates(notification['job_id'])
        if notification['kind']!=MEMORY_CANDIDATES_KIND:
            ids=[item for item in queued['candidates'] if isinstance(item,list) and len(item)==2]
            shown,more=[row for row in shown if [row['id'],row['content_digest']] in ids],int(queued.get('more') or 0)
        candidates=[service.memory_prompt_item(row) for row in shown]
        if not candidates:return None
        return {'candidates':candidates,'more':more,'sent':now,'done':{}}

    @staticmethod
    def memory_open(binding):
        """The 1-based indices of the tappable candidates not yet decided (#818)."""
        return [index for index,item in enumerate(binding['candidates'],1) if item.get('tap') and item['id'] not in binding['done']]

    @staticmethod
    def memory_answered(binding):
        """The owner's one answer to an ask whose every tappable fact is decided, or None (#836).

        ``accepted`` or ``rejected`` when every decision was the same; None
        while a fact is open, when nothing was decided, or for mixed answers.
        """
        # #838 review: every bound fact must be decided, tappable or not, before the answer
        # can settle what follows; an untappable fact left open keeps the ask open.
        if not binding['done'] or any(item['id'] not in binding['done'] for item in binding['candidates']):return None
        answers={status for status in binding['done'].values() if status in MEMORY_CANDIDATES_SETTLED}
        return answers.pop() if len(answers)==1 else None

    def memory_prompt_text(self, job_id, binding, footer=None):
        """The ask in the owner's words: each fact as a short line, never a key (#818, #836).

        Open: "기억해 둘까요?" with each fact, the value it would replace and
        each decision so far.  Answered: a settled first line ("기억해 둘게요." /
        "기억하지 않을게요.") with what is kept.  Bounded; stored secrets and
        credential shapes removed.  A fact the ask cannot show complete is
        left to 내 기록.
        """
        service=self.service
        items,done=binding['candidates'],binding['done']
        finished=bool(done) and not service.memory_open(binding)
        decided={status for status in done.values() if status in MEMORY_CANDIDATES_SETTLED}
        numbered=len(items)>1 and not finished
        if not finished:lines=[MEMORY_CANDIDATES_HEADER]
        elif decided:lines=[MEMORY_CANDIDATES_SETTLED[decided.pop() if len(decided)==1 else 'mixed']]
        else:lines=[MEMORY_CANDIDATES_OUTDATED_TEXT]
        for index,item in enumerate(items,1):
            status=done.get(item['id'])
            prefix=f'{index}. ' if numbered else '• '
            if status=='rejected':
                # A no erases the candidate's value; only its place in the numbered list stays.
                if numbered:lines.append(prefix+MEMORY_CANDIDATE_STATUS['rejected'])
                continue
            row=service.store.memory_candidate(item['id'],MEMORY_OWNER,job_id) or {}
            line=prefix+service.memory_fact(row.get('content'),MEMORY_CANDIDATE_CHARS,row.get('memory_key'))[0]
            if status=='accepted':
                if numbered:line+=' → '+MEMORY_CANDIDATE_STATUS['accepted']
            elif status:line+=' → '+MEMORY_CANDIDATE_STATUS[status]
            else:
                old=service.store.memory(item['current'][0],MEMORY_OWNER,current_only=False) if item.get('current') else None
                if old:line=msg('quickstart_service.memory_current_line', fact=line, current=service.memory_fact(old['content'],60,old.get('memory_key'))[0])
                if not item.get('tap'):line=msg('quickstart_service.memory_in_records_line', fact=line)
            lines.append(line)
        if binding.get('more'):lines.append(msg('quickstart_service.memory_more', count=binding['more']))
        if footer and footer not in lines:lines.append(footer)
        return '\n'.join(lines)

    @classmethod
    def memory_prompt_markup(cls, notification_id, binding):
        """Per tappable candidate [👍] [👎], plus both for all when more than one is open (#818).

        #881: emoji buttons, not message reactions - the Bot API delivers a
        reaction change only where the bot is a chat administrator, which a
        private chat has none of, so a reaction could not decide a fact.
        Each button still names exactly one fact (or all) in its callback.
        """
        def pair(target,accept,reject):
            return [{'text':accept,'callback_data':f'p7m:{notification_id}:{target}:accept'},
                    {'text':reject,'callback_data':f'p7m:{notification_id}:{target}:reject'}]
        open_=cls.memory_open(binding)
        yes,no=MEMORY_BUTTON_YES,MEMORY_BUTTON_NO
        if len(binding['candidates'])==1:return {'inline_keyboard':[pair(1,yes,no)] if open_ else []}
        rows=[pair(index,f'{index} {yes}',f'{index} {no}') for index in open_]
        if len(open_)>1:rows.append(pair('a',msg('quickstart_service.all_button', label=yes),msg('quickstart_service.all_button', label=no)))
        return {'inline_keyboard':rows}

    def decide_memory_item(self, job_id, item, accept):
        """One bound candidate decided: ``accepted``, ``rejected`` or ``outdated`` (#818).

        It must still be pending with the shown content and, for a yes, the
        current Memory under its key must still be the one the ask bound;
        otherwise it is ``outdated`` and nothing is written.  A yes is the
        existing owner approval path (exact approval bound to owner, Work,
        candidate and content digest, then accept); a no is the existing reject.
        """
        service=self.service
        if not service.memory_candidate_still(job_id,item['id'],item['digest']) or (
                accept and service.current_memory_ref(item['key'])!=item.get('current')):
            return 'outdated'
        try:
            if accept:
                approval=service.store.issue_candidate_memory_approval(MEMORY_OWNER,job_id,item['id'],item['digest'])
                service.store.accept_memory_candidate(MEMORY_OWNER,job_id,item['id'],item['digest'],approval['approval_token'])
                return 'accepted'
            service.store.reject_memory_candidate(MEMORY_OWNER,job_id,item['id'],item['digest'])
            return 'rejected'
        except ValueError as exc:
            LOG.info('memory candidate %s refused work=%s kind=%s',('accept' if accept else 'reject'),job_id,type(exc).__name__)
            return 'outdated'

    def decide_memory_prompt(self, job_id, binding, targets, accept):
        """Apply one tap to the open ``targets`` (1-based) of a bound prompt; returns the ids it decided (#818)."""
        service=self.service
        decided=[]
        for index in targets:
            item=binding['candidates'][index-1]
            binding['done'][item['id']]=service.decide_memory_item(job_id,item,accept);decided.append(item['id'])
        return decided

    def join_memory_prompt(self, notification, binding, now=None):
        """A candidate proposed after the Work's ask was sent joins that same ask (#836).

        Owner feedback 2026-09-28: one ask per owner message, and answering it
        settles what follows.  While the ask is open (or only a list), a new
        candidate is added by editing the message, and it is bound only when
        the edit is confirmed, so a tap never covers a fact the owner was not
        shown.  Once the owner answered every fact with one answer, that
        answer covers what they said in that message: a no rejects the new
        candidate; a yes accepts it through the owner approval path when the
        ask can show it complete and it replaces no current Memory (otherwise
        it reopens the ask).  A candidate settled this way is recorded in the
        binding under ``by_answer`` (provenance: the owner's answer to this
        Work's ask), and the edit shows it.  Mixed answers reopen the ask.
        Past ``MEMORY_CANDIDATES_SHOWN`` a candidate stays in 내 기록.
        """
        service=self.service
        now=time.time() if now is None else now
        job_id=notification['job_id']
        listed={item['id'] for item in binding['candidates']}
        new=[row for row in sorted(service.store.memory_candidates(MEMORY_OWNER,job_id,limit=101),
                                   key=lambda row:(row['created'],row['id'])) if row['id'] not in listed]
        if not new:return
        answer=service.memory_answered(binding)
        settled,opened,beyond=[],[],0
        for row in new:
            if len(binding['candidates'])+len(opened)>=MEMORY_CANDIDATES_SHOWN:
                beyond+=1
                continue
            item=service.memory_prompt_item(row)
            if answer=='rejected' or (answer=='accepted' and item['tap'] and item['current'] is None):
                binding['candidates'].append(item)
                binding['done'][item['id']]=service.decide_memory_item(job_id,item,answer=='accepted')
                binding.setdefault('by_answer',[]).append(item['id'])
                settled.append(item['id'])
            else:
                opened.append(item)
        # #838 review: ``new`` already holds every unbound candidate, including those hidden before.
        binding['more']=beyond
        shown={**binding,'candidates':binding['candidates']+opened}
        if binding['done'] and not service.memory_open(binding) and service.memory_open(shown):
            shown['sent']=now   # an answered ask reopened: its buttons get their own time
        edited=False
        if isinstance(notification.get('message_id'),int):
            try:
                service.telegram.edit_message_text(notification['chat_id'],notification['message_id'],
                                                service.memory_prompt_text(job_id,shown),
                                                service.memory_prompt_markup(notification['id'],shown))
                edited=True
            except ProviderError:
                pass
        final=shown if edited else binding
        state=('sent' if service.memory_open(final) else 'memory_decided' if final['done'] else 'memory_listed')
        # #188 (Codex on #200): a confirmed edit that shows new open candidates is a new showing; an owner
        # message dated before it cannot answer what it added.
        service.store.update_notification(notification['id'],state,fingerprint=json.dumps(final),
                                       delivered=edited and bool(opened))
        LOG.info('memory candidates joined the ask work=%s settled_by_answer=%s added=%s edited=%s',
                 job_id,len(settled),len(opened) if edited else 0,edited)

    def answer_memory_prompt(self, notification, binding, targets, accept, by='button'):
        """The owner's yes or no to the open ``targets`` of one sent memory prompt (#818; #172 judged answers).

        The button's path, shared with a judged reply or reaction: past its TTL
        the prompt expires and nothing is decided; otherwise each target is
        decided against what the message showed, the binding is stored and the
        message is edited.  Returns ``{'expired', 'outdated', 'text'}``.
        """
        service=self.service
        if time.time()-float(binding['sent'])>MEMORY_CANDIDATES_TTL_SECONDS:
            service.expire_memory_prompt(notification,binding)
            return {'expired':True,'outdated':False,'text':MEMORY_CANDIDATES_EXPIRED_TEXT}
        decided=service.decide_memory_prompt(notification['job_id'],binding,targets,accept)
        finished=not service.memory_open(binding)
        service.store.update_notification(notification['id'],'memory_decided' if finished else 'sent',
                                       fingerprint=json.dumps(binding))
        outdated=any(binding['done'][item]=='outdated' for item in decided)
        LOG.info('memory candidates %s by owner %s work=%s decided=%s outdated=%s','accept' if accept else 'reject',
                 by,notification['job_id'],len(decided),outdated)
        text=service.memory_prompt_text(notification['job_id'],binding,MEMORY_CANDIDATES_OUTDATED_TEXT if outdated else None)
        if isinstance(notification.get('message_id'),int):
            try:service.telegram.edit_message_text(notification['chat_id'],notification['message_id'],text,
                                                service.memory_prompt_markup(notification['id'],binding))
            except ProviderError:pass
        return {'expired':False,'outdated':outdated,'text':text}

    def expire_memory_prompt(self, notification, binding):
        """Refuse further taps on one memory prompt and remove its buttons (#818)."""
        service=self.service
        service.store.update_notification(notification['id'],'expired')
        if isinstance(notification.get('message_id'),int):
            try:service.telegram.edit_message_text(notification['chat_id'],notification['message_id'],
                                                service.memory_prompt_text(notification['job_id'],binding,MEMORY_CANDIDATES_EXPIRED_TEXT),
                                                {'inline_keyboard':[]})
            except ProviderError:pass

    def expire_memory_prompts(self, now=None):
        """Close memory prompts older than ``MEMORY_CANDIDATES_TTL_SECONDS`` (#818); at most once a minute."""
        service=self.service
        now=time.time() if now is None else now
        if now-getattr(service,'_memory_prompt_sweep',0)<MEMORY_CANDIDATES_SWEEP_SECONDS:return
        service._memory_prompt_sweep=now
        for row in service.store.telegram.notifications_in_state(MEMORY_PROMPT_KINDS,'sent'):
            binding=service.memory_binding(row)
            if binding and 'sent' in binding and now-float(binding['sent'])>MEMORY_CANDIDATES_TTL_SECONDS:
                service.expire_memory_prompt(row,binding)

    def release_lesson_notices(self, now=None):
        """Release lesson notices a crash or an unknown send left behind; at most once a minute.

        ``held`` rows whose Work's reply is settled (``sent``, ``unknown`` or
        ``cancelled``) are queued; the release in ``deliver_one`` may have been
        cut off between the delivery update and the queue.  A notice whose own
        send came back ``unknown`` is told again, at most
        ``NOTICE_RETELLS`` times, within ``LESSON_NOTICE_RETELL_SECONDS``.
        """
        service=self.service
        now=time.time() if now is None else now
        if now-getattr(service,'_lesson_release_sweep',0)<MEMORY_CANDIDATES_SWEEP_SECONDS:return
        service._lesson_release_sweep=now
        with service.lock:
            for kind in WORKING_LESSON_NOTICE_KINDS:
                for row in service.store.held_notifications(kind):
                    if row.get('job_delivery') in service.DELIVERY_SETTLED:service.release_working_notes(row['job_id'])
            for row in service.store.telegram.notifications_in_state(WORKING_LESSON_NOTICE_KINDS,'unknown'):
                try:bound=json.loads(row.get('fingerprint') or '')
                except (TypeError,ValueError):continue
                if not isinstance(bound,dict):continue
                retells=int(bound.get('retells') or 0)
                if retells>=service.NOTICE_RETELLS or now-float(row['created'])>LESSON_NOTICE_RETELL_SECONDS:continue
                service.store.update_notification(row['id'],'queued',fingerprint=json.dumps({**bound,'retells':retells+1}))
                LOG.info('working lesson notice told again work=%s',row['job_id'])
