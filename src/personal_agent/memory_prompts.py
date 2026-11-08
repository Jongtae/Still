"""Memory candidate prompts and saved notices extracted from AgentService (#21).

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
    MEMORY_SAVED_AGAIN,
    MEMORY_SAVED_BEFORE,
    MEMORY_SAVED_ENDED,
    MEMORY_SAVED_EXPIRED_TEXT,
    MEMORY_SAVED_HEADER,
    MEMORY_SAVED_KIND,
    MEMORY_SAVED_KINDS,
    MEMORY_SAVED_MORE,
    MEMORY_SAVED_STATUS,
    MEMORY_SAVED_TTL_SECONDS,
    MEMORY_SAVED_UNDONE,
    MEMORY_SAVED_UPKEEP_KIND,
    MEMORY_UNDO_BUTTON,
    MEMORY_UNDO_TOOL,
    WORKING_NOTES_KIND,
)


class MemoryPrompts:
    """Existing memory prompts and saved notices over one service's state."""

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
        """Close memory prompts older than ``MEMORY_CANDIDATES_TTL_SECONDS`` (#818) and saved notices older
        than ``MEMORY_SAVED_TTL_SECONDS`` (#918); at most once a minute."""
        service=self.service
        now=time.time() if now is None else now
        if now-getattr(service,'_memory_prompt_sweep',0)<MEMORY_CANDIDATES_SWEEP_SECONDS:return
        service._memory_prompt_sweep=now
        rows=service.store.telegram.notifications_in_state(MEMORY_PROMPT_KINDS+MEMORY_SAVED_KINDS,'sent')
        for row in rows:
            if row['kind'] in MEMORY_SAVED_KINDS:
                binding=service.memory_saved_binding(row)
                if binding and 'sent' in binding and now-float(binding['sent'])>MEMORY_SAVED_TTL_SECONDS:
                    service.expire_memory_saved(row,binding)
                continue
            binding=service.memory_binding(row)
            if binding and 'sent' in binding and now-float(binding['sent'])>MEMORY_CANDIDATES_TTL_SECONDS:
                service.expire_memory_prompt(row,binding)

    def work_saved_memories(self, job_id):
        """``(shown, remaining)``: the current Memory rows this Work wrote, oldest first (#918)."""
        service=self.service
        rows=service.store.work_memories(MEMORY_OWNER,job_id,limit=101)
        return rows[:MEMORY_CANDIDATES_SHOWN],max(0,len(rows)-MEMORY_CANDIDATES_SHOWN)

    def queue_memory_saved(self, job):
        """Tell the paired owner what this Work remembered, once (#918); the notice a save did not hold."""
        service=self.service
        shown,more=service.work_saved_memories(job['id'])
        if shown:service.queue_notification(job,MEMORY_SAVED_KIND,fingerprint=json.dumps(
            {'items':[[row['id'],row['content_digest']] for row in shown],'more':more}))

    def release_memory_saved(self, job):
        """The one entry point once this Work's reply is settled: tell what is current and unlisted (#918 review).

        ``held`` (the save held the row in its own transaction) or missing (a
        Work saved before this head): queued, bound at send time to every
        current row of the Work.  Otherwise - a retried or resumed Work that
        saved again after its notice was already told (re-review P2): a
        bound notice still ``sent`` gets the new facts by an edit
        (``join_memory_saved``); a settled or lost one gives them a notice of
        their own (``_queue_separate_saved_notice``); one still queued binds
        them when it is sent.  Caller holds ``self.lock``.
        """
        service=self.service
        rows=service._saved_notice_rows(job['id'])
        row=rows.get(MEMORY_SAVED_KIND)
        if row is None:
            service.queue_memory_saved(job);return
        if row['state'] in (service.store.NOTICE_HELD,'cancelled'):
            shown,more=service.work_saved_memories(job['id'])
            if shown:service.store.update_notification(row['id'],'queued',fingerprint=json.dumps(
                {'items':[[item['id'],item['content_digest']] for item in shown],'more':more}))
            elif row['state']==service.store.NOTICE_HELD:service.store.update_notification(row['id'],'cancelled')
            return
        if row['state']=='queued':return
        binding=service.memory_saved_binding(row)
        if row['state']=='sent' and binding and 'sent' in binding:
            service.join_memory_saved(row,binding);return
        service._queue_separate_saved_notice(job,rows)

    def release_memory_saved_notices(self, now=None):
        """Release saved-notices a crash or an unknown send left behind (#918 review); at most once a minute.

        ``held`` rows whose Work's reply is settled (``sent``, ``unknown`` or
        ``cancelled``) are queued; the release in ``deliver_one`` may have been
        cut off between the delivery update and the queue.  A notice whose own
        send came back ``unknown`` (or that a restart found ``sending``) is
        told again, at most ``MEMORY_SAVED_RETELLS`` times: a notice may be
        told twice rather than never, and each undo is exact to its own
        message.  A store without ``jobs`` for the row is left alone.
        """
        service=self.service
        now=time.time() if now is None else now
        if now-getattr(service,'_memory_saved_release_sweep',0)<MEMORY_CANDIDATES_SWEEP_SECONDS:return
        service._memory_saved_release_sweep=now
        with service.lock:
            for row in service.store.held_notifications(MEMORY_SAVED_KIND):
                if row.get('job_delivery') not in service.DELIVERY_SETTLED:continue
                job=service.store.job(row['job_id'])
                if job:service.release_memory_saved(job)
            # #233: a working-notes notice held behind a reply that has since settled.
            for row in service.store.held_notifications(WORKING_NOTES_KIND):
                if row.get('job_delivery') in service.DELIVERY_SETTLED:service.release_working_notes(row['job_id'])
            lost=service.store.telegram.notifications_in_state(MEMORY_SAVED_KINDS,'unknown')
            for row in lost:
                binding=service.memory_saved_binding(row) or {}
                retells=int(binding.get('retells') or 0)
                if retells>=service.MEMORY_SAVED_RETELLS or now-float(row['created'])>MEMORY_SAVED_TTL_SECONDS:continue
                items=[item if isinstance(item,list) else [item['id'],item['digest']] for item in binding.get('items',())
                       if isinstance(item,(list,dict))]
                service.store.update_notification(row['id'],'queued',fingerprint=json.dumps(
                    {'items':items,'more':int(binding.get('more') or 0),'retells':retells+1}))
                LOG.info('memory saved notice told again work=%s kind=%s',row['job_id'],row['kind'])
            # #19 review P1: a working-notes notice whose send came back unknown is told again on the same
            # bounded terms; it is sent only while its revision is still current (``working_notes_notice_text``).
            for row in service.store.telegram.notifications_in_state((WORKING_NOTES_KIND,),'unknown'):
                try:bound=json.loads(row.get('fingerprint') or '')
                except (TypeError,ValueError):continue
                if not isinstance(bound,dict):continue
                retells=int(bound.get('retells') or 0)
                if retells>=service.MEMORY_SAVED_RETELLS or now-float(row['created'])>MEMORY_SAVED_TTL_SECONDS:continue
                service.store.update_notification(row['id'],'queued',fingerprint=json.dumps({**bound,'retells':retells+1}))
                LOG.info('working notes notice told again work=%s',row['job_id'])

    def queue_upkeep_memory_saved(self, work_id):
        """After #805 upkeep saved facts: they join the Work's one notice, or get their own (#918).

        Only once the Telegram Work's reply is settled (sent, unknown or
        cancelled); before that the save held the notice, which is bound at
        send time to everything saved.  No notice (a Work saved before this
        head) or one cancelled because nothing was saved: this is the first.
        One held or queued binds them when it is sent.  One sent: the facts
        are added by editing it (``join_memory_saved``).  One already settled
        (undone, expired) or lost: a separate upkeep notice bound to these
        facts only, so a save is never silent.  Under ``self.lock``, like the
        taps and the sends.
        """
        service=self.service
        with service.lock:
            job=service.store.job(work_id)
            if not job or job.get('delivery') not in service.DELIVERY_SETTLED:return
            service.release_memory_saved(job)

    def _saved_notice_rows(self, work_id):
        service=self.service
        return {row['kind']:row for row in service.store.telegram.notifications_of_kinds(work_id,MEMORY_SAVED_KINDS)}

    def _queue_separate_saved_notice(self, job, rows):
        """A notice of its own for the saved facts no notice of this Work showed (#918)."""
        service=self.service
        listed=set()
        for kind_row in rows.values():
            bound=service.memory_saved_binding(kind_row)
            listed|={item['id'] for item in (bound or {}).get('items',()) if isinstance(item,dict)}
            listed|={item[0] for item in (bound or {}).get('items',()) if isinstance(item,list) and item}
        upkeep=rows.get(MEMORY_SAVED_UPKEEP_KIND)
        if upkeep and upkeep['state'] not in ('cancelled',):return
        fresh=[item for item in service.store.work_memories(MEMORY_OWNER,job['id'],limit=101) if item['id'] not in listed]
        if not fresh:return
        fingerprint=json.dumps({'items':[[item['id'],item['content_digest']] for item in fresh[:MEMORY_CANDIDATES_SHOWN]],
                                'more':max(0,len(fresh)-MEMORY_CANDIDATES_SHOWN)})
        if upkeep:service.store.update_notification(upkeep['id'],'queued',fingerprint=fingerprint)
        else:service.queue_notification(job,MEMORY_SAVED_UPKEEP_KIND,fingerprint=fingerprint)

    @staticmethod
    def memory_saved_binding(notification):
        """A saved notice's recorded binding (queued ids, or the send-time binding with ``sent``), or None (#918)."""
        try:binding=json.loads((notification or {}).get('fingerprint') or '')
        except (TypeError,ValueError):return None
        return binding if isinstance(binding,dict) and isinstance(binding.get('items'),list) else None

    def memory_saved_item(self, row):
        """One saved fact as the notice binds it: id, content digest, key and what it superseded (#918)."""
        service=self.service
        previous=service.store.memory(row['supersedes'],MEMORY_OWNER,current_only=False) if row.get('supersedes') else None
        return {'id':row['id'],'digest':row['content_digest'],'key':row['memory_key'],
                'previous':[previous['id'],previous['content_digest']] if previous else None,
                # #220: the earlier facts this save ended under other keys, so the notice names them too.
                'ended':[[item['id'],item['content_digest']] for item in service.store.ended_by(MEMORY_OWNER,row['id'])]}

    def bind_memory_saved(self, notification, now):
        """What a saved notice shows, bound at send time; None when the Work has nothing current (#918).

        The Work's notice binds every current Memory row of the Work when it
        is sent, oldest first, up to ``MEMORY_CANDIDATES_SHOWN``; an upkeep
        notice binds only its queued ids.
        """
        service=self.service
        queued=service.memory_saved_binding(notification)
        if not queued or 'sent' in queued:return None
        shown,more=service.work_saved_memories(notification['job_id'])
        if notification['kind']!=MEMORY_SAVED_KIND:
            ids=[item for item in queued['items'] if isinstance(item,list) and len(item)==2]
            shown,more=[row for row in shown if [row['id'],row['content_digest']] in ids],int(queued.get('more') or 0)
        items=[service.memory_saved_item(row) for row in shown]
        if not items:return None
        return {'items':items,'more':more,'sent':now,'done':{},'restored':{},'retells':int(queued.get('retells') or 0)}

    @staticmethod
    def memory_saved_open(binding):
        """The 1-based indices of the saved facts whose undo is still open (#918)."""
        return [index for index,item in enumerate(binding['items'],1) if item['id'] not in binding['done']]

    def memory_saved_text(self, binding, footer=None):
        """The notice in the owner's words: "기억했어요: <fact>" and, once undone, "되돌렸어요" (#918).

        Values only, never a key; bounded; stored secrets and credential
        shapes removed (``memory_fact``).  A fact that superseded a value
        says so ("전에는 …"), and an undo that restored it says so ("다시 …"),
        so a replacement is never silent.  It asks nothing.
        """
        service=self.service
        items,done,restored=binding['items'],binding['done'],binding.get('restored') or {}
        def fact_of(memory_id):
            row=service.store.memory(memory_id,MEMORY_OWNER,current_only=False) or {}
            return service.memory_fact(row.get('content'),MEMORY_CANDIDATE_CHARS,row.get('memory_key'))[0]
        def line_of(item):
            status=done.get(item['id'])
            text=fact_of(item['id'])
            # Review P3: a previous or restored row deleted from 내 기록 since has no text; nothing empty is shown.
            if status=='retracted':
                again=[restored.get(item['id'])] if restored.get(item['id']) else []
                again+=list((binding.get('restored_ended') or {}).get(item['id']) or ())
                back=', '.join(fact for fact in (fact_of(memory_id) for memory_id in again) if fact)
                return text+(f' ({MEMORY_SAVED_AGAIN.format(facts=back)})' if back else '')
            if status=='outdated':return text
            # #425: the value this fact replaced under its own key, then the other facts it retired, each named so.
            before=fact_of(item['previous'][0]) if item.get('previous') else ''
            ended=', '.join(fact for fact in (fact_of(pair[0]) for pair in item.get('ended') or ()
                                             if isinstance(pair,list) and pair) if fact)
            notes=[*([MEMORY_SAVED_BEFORE.format(facts=before)] if before else []),
                   *([MEMORY_SAVED_ENDED.format(facts=ended)] if ended else [])]
            return text+(f" ({'; '.join(notes)})" if notes else '')
        single=len(items)==1
        all_done=bool(done) and not service.memory_saved_open(binding)
        every_retracted=all_done and all(done.get(item['id'])=='retracted' for item in items)
        if single:
            item=items[0];status=done.get(item['id'])
            head=MEMORY_SAVED_UNDONE if status=='retracted' else MEMORY_SAVED_HEADER
            lines=[f'{head}: {line_of(item)}']
            if status=='outdated':lines[0]+=' → '+MEMORY_SAVED_STATUS['outdated']
        else:
            lines=[MEMORY_SAVED_UNDONE if every_retracted else MEMORY_SAVED_HEADER]
            for index,item in enumerate(items,1):
                status=done.get(item['id'])
                line=f'{index}. '+line_of(item)
                if status:line+=' → '+MEMORY_SAVED_STATUS[status]
                lines.append(line)
        if binding.get('more'):lines.append(MEMORY_SAVED_MORE.format(count=binding['more']))
        if footer and footer not in lines:lines.append(footer)
        return '\n'.join(lines)

    @classmethod
    def memory_saved_markup(cls, notification_id, binding):
        """No buttons (#996): the owner answers a saved notice in words or with a reaction.

        A notice sent before #996 keeps its [되돌리기] taps working
        (``legacy_memory_saved_markup`` and the ``p7u`` callback), and an
        edit of such a notice removes its buttons.
        """
        return {'inline_keyboard':[]}

    @classmethod
    def legacy_memory_saved_markup(cls, notification_id, binding):
        """One [되돌리기] per open fact (numbered when several), plus 모두 when more than one is open (#918)."""
        open_=cls.memory_saved_open(binding)
        if len(binding['items'])==1:
            return {'inline_keyboard':[[{'text':MEMORY_UNDO_BUTTON,'callback_data':f'p7u:{notification_id}:1'}]] if open_ else []}
        rows=[[{'text':f'{index} {MEMORY_UNDO_BUTTON}','callback_data':f'p7u:{notification_id}:{index}'}] for index in open_]
        if len(open_)>1:rows.append([{'text':msg('quickstart_service.all_button', label=MEMORY_UNDO_BUTTON),'callback_data':f'p7u:{notification_id}:a'}])
        return {'inline_keyboard':rows}

    def undo_memory_item(self, job_id, item):
        """Retract one bound fact: ``retracted`` (previous value restored when one was superseded) or ``outdated`` (#918).

        Exact: the row must still be current with the bound content digest
        (``retract_memory`` fails closed otherwise).  The undo is recorded on
        the Work as a ``memory_undo`` event for its information-use audit and
        Evidence.  Returns ``(status, restored_id)``.
        """
        service=self.service
        # #918 review P2: the retraction and its Evidence row are one transaction (``record``), so an
        # undo is never durable without the event the audit promises.
        record={'job_id':job_id,'tool':MEMORY_UNDO_TOOL,'status':'recorded',
                'detail':{'host_action':MEMORY_UNDO_TOOL,'evidence':{'id':item['id'],'memory_key':item.get('key'),'auto_saved':True}}}
        try:
            receipt=service.store.retract_memory(MEMORY_OWNER,item['id'],item['digest'],record=record)
        except ValueError as exc:
            LOG.info('memory undo refused work=%s kind=%s',job_id,type(exc).__name__)
            return 'outdated',None,[]
        # #220 (Codex review): also the rows under other keys the save had ended and the undo made current again.
        # Codex review: a row whose last day passed meanwhile is not "back in use"; the notice leaves it out.
        holding={row['id'] for row in MemoryService(service.store,private_read_sink=MemoryService.NO_EGRESS_GUARD).holding(
            [service.store.memory(memory_id,MEMORY_OWNER,current_only=False) or {'id':None}
             for memory_id in [(receipt.get('restored') or {}).get('id'),*(receipt.get('restored_ended') or ())] if memory_id])}
        restored=(receipt.get('restored') or {}).get('id')
        return ('retracted',restored if restored in holding else None,
                [memory_id for memory_id in receipt.get('restored_ended') or () if memory_id in holding])

    def undo_memory_saved(self, job_id, binding, targets):
        """Apply one tap to the open ``targets`` (1-based) of a bound notice; returns the ids it decided (#918)."""
        service=self.service
        decided=[]
        for index in targets:
            item=binding['items'][index-1]
            status,restored,ended=service.undo_memory_item(job_id,item)
            binding['done'][item['id']]=status
            if restored:binding.setdefault('restored',{})[item['id']]=restored
            if ended:binding.setdefault('restored_ended',{})[item['id']]=ended
            decided.append(item['id'])
        return decided

    def join_memory_saved(self, notification, binding, now=None):
        """Facts upkeep saved after the Work's notice was sent join it by an edit (#918, as #836 joins the ask).

        Bound only when the edit is confirmed, so an undo button never covers
        a fact the owner was not shown; the notice's undo window restarts at
        the join (review P3), so a joined fact is undoable for the full week.
        A refused edit falls back to a notice of its own for the new facts
        (review P3), so they are never left untold.  Past
        ``MEMORY_CANDIDATES_SHOWN`` a fact is only counted.
        """
        service=self.service
        now=time.time() if now is None else now
        job_id=notification['job_id']
        listed={item['id'] for item in binding['items']}
        new=[row for row in service.store.work_memories(MEMORY_OWNER,job_id,limit=101) if row['id'] not in listed]
        if not new:return
        room=max(0,MEMORY_CANDIDATES_SHOWN-len(binding['items']))
        added=[service.memory_saved_item(row) for row in new[:room]]
        shown={**binding,'items':binding['items']+added,'more':len(new)-len(added),'sent':now}
        edited=False
        if isinstance(notification.get('message_id'),int):
            try:
                service.telegram.edit_message_text(notification['chat_id'],notification['message_id'],
                                                service.memory_saved_text(shown),service.memory_saved_markup(notification['id'],shown))
                edited=True
            except ProviderError:
                pass
        final=shown if edited else binding
        # #188 (Codex on #200): a confirmed edit that added facts is a new showing of the notice.
        service.store.update_notification(notification['id'],'sent' if service.memory_saved_open(final) else 'memory_undone',
                                       fingerprint=json.dumps(final),delivered=edited and bool(added))
        LOG.info('memory saved notice joined work=%s added=%s edited=%s',job_id,len(added) if edited else 0,edited)
        if not edited:
            job=service.store.job(job_id)
            if job:service._queue_separate_saved_notice(job,service._saved_notice_rows(job_id))

    def expire_memory_saved(self, notification, binding):
        """Refuse further undo taps on one saved notice and remove its buttons (#918)."""
        service=self.service
        service.store.update_notification(notification['id'],'expired')
        if isinstance(notification.get('message_id'),int):
            try:service.telegram.edit_message_text(notification['chat_id'],notification['message_id'],
                                                service.memory_saved_text(binding,MEMORY_SAVED_EXPIRED_TEXT),{'inline_keyboard':[]})
            except ProviderError:pass
