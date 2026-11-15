"""Preparations coordination extracted from AgentService (#21).

AgentService remains the composition root and owns all mutable service state.
The collaborator uses the service on every call, including calls between moved
methods, preserving service forwarding entry points and patch targets. Scheduling,
approvals, budgets, recovery, secrets and database transactions are unchanged.
The existing preparations.Preparations store retains persistence ownership.
"""

import json
import uuid
from contextlib import contextmanager
from datetime import datetime

from . import entrusted_lines as el
from . import preparations as prep
from . import owner_model as om
from . import work_status as ws
from .agent_runtime import ToolError, not_run, ran_field
from .conversation_handoff import JUDGMENT_NO, JUDGMENT_YES
from .conversation_projection import earlier_turn_line
from .messages import LANGUAGES, msg
from .telegram_locale import owner_language
from .prompt_files import prompt
from .providers import ProviderError
from .procedures import digest as source_digest
from .quickstart_service import (LOG, PREPARATION_CONVERSATION_CHARS,
                                 PREPARATION_CONVERSATION_TURNS, PREPARED_IDS_PREFIX,
                                 UNATTENDED_APPROVAL_TEXT, prepared_ids)


#: #780: the daily plan's brief: a method for reading the day, never a topic.
DAY_PLAN_LINES = prompt('day_plan_lines')
DAY_PLAN_GOAL = prompt('day_plan') + '\n\n' + DAY_PLAN_LINES
#: #780: the store config row for the daily plan: ``{'row': id}`` once set up, ``{'opted_out': True}``
#: after the owner cancelled or deleted it.
DAY_PLAN_KEY = 'day_plan'
#: #780: the recent conversation a plan run is shown, as an owner-present Work is.
PLAN_HISTORY_MESSAGES = 16
#: #798: the periodic account's brief: a method for giving an account of a line, never a topic.
LINE_ACCOUNT_GOAL = prompt('line_account')
BUNDLE_SETTLE_SECONDS = 1800
BUNDLE_CHARS = el.BUNDLE_CHARS


class PreparationsService:
    """Preparation operations over one owning service's existing state."""

    def __init__(self, service):
        self.service = service

    def failed_preparation_watch_goals(self, job_id):
        """Redacted goals of this Work's failed bounded-watch attempts.

        Reconstruct from the normal tool Evidence so the retry guard survives
        a resumable Work and a service restart. The running event already
        stores the goal with the standard secret redactor; failed events pair
        it by tool-call ID and stable refusal code.
        """
        service=self.service
        with service.store.db() as db:
            rows=db.execute("SELECT status,detail FROM tool_events WHERE job_id=? AND tool='schedule_preparation' ORDER BY id",
                            (job_id,)).fetchall()
        goals_by_call={}
        failed=[]
        for row in rows:
            try:detail=json.loads(row['detail'])
            except (TypeError,ValueError):continue
            call_id=detail.get('call_id')
            if not isinstance(call_id,str):continue
            if row['status']=='running' and detail.get('host_action')=='schedule_preparation':
                args=detail.get('arguments')
                goal=args.get('goal') if isinstance(args,dict) else None
                if isinstance(goal,str) and not goal.startswith('[가림:'):
                    goals_by_call[call_id]=goal
            elif row['status']=='failed' and detail.get('code')=='invalid_window':
                goal=goals_by_call.get(call_id)
                if goal:failed.append(goal)
        return failed

    def repeats_refused_watch(self, job_id, goal):
        """Whether ``goal`` repeats a watch this Work was refused for lacking a window (#860, #173 slice B).

        The guard keeps a refused repeated check from fanning out into dated
        one-time preparations.  The same stored goal text is the exact identity;
        any other wording is the Judgment AI's ``same_preparation_goal`` call
        (asked only here, never per turn) instead of a particle/filler table.
        Unavailable keeps the guard closed: the worker's correction, a window,
        is always open to it.
        """
        service=self.service
        refused=service.failed_preparation_watch_goals(job_id)
        if not refused:
            return False
        if goal in refused:
            return True
        judged=service.decision_judge.same_preparation_goal(goal,refused,work_id=job_id)
        return judged.outcome!=JUDGMENT_NO

    def declined_before(self, job_id, goal, now, judge=None):
        """Did the owner recently decline a proposal of this same preparation (#582)?

        The same stored goal text is the exact identity; any other wording is the
        Judgment AI's ``declined_preparation_goal`` call, asked only when there are
        declined goals.  Unavailable lets the proposal through: it still waits for
        the owner's yes.  ``judge`` is the caller's ``ConversationJudgments`` (#583: the look-back's own).
        """
        service=self.service
        declined=service.preparations.declined_goals(now)
        if not declined:return False
        if goal in declined:return True
        return (judge or service.decision_judge).declined_preparation_goal(goal,declined,work_id=job_id).outcome==JUDGMENT_YES

    def prepared_text(self, job):
        """The bounded "Prepared for you" section for this Work, or None (#659).

        Fresh answers of owner-accepted ``prepare`` preparations, stored
        secrets redacted.  A failure here never blocks the turn.
        """
        service=self.service
        index=self.lines_index(job)
        try:
            now=service.preparations.clock()
            rows=service.preparations.fresh(now,exclude_job=job.get('id'))
            text=prep.render_prepared(rows,now,service._redact_known_secrets) if rows else None
            if index:
                # #798: which lines exist, so the AI can judge whether one bears on this request.
                text='\n\n'.join(part for part in (text,service._redact_known_secrets(index)) if part)
            if text and not service.unattended_work(job):
                # #780: which results were placed in the context of a Work the owner started, once per
                # Work: the observed half of a hit.  Whether one was useful is the daily plan's own judgment.
                shown=[row['id'] for row in rows if 'prep:'+row['id'] in text]
                try:service.preparations.record_offered(job.get('id'),shown,now)
                except Exception:LOG.info('prepared answers shown but not recorded job=%s',job.get('id'))
            return text
        except Exception:
            LOG.warning('prepared answers unavailable job=%s',job.get('id'))
            return None

    def preparation_conversation(self, job):
        """The exchange before this Work's request, as the acceptance judgment reads it (#573).

        The last ``PREPARATION_CONVERSATION_TURNS`` earlier owner and assistant
        rows, labelled as earlier Works like the plan call's (#170), document
        excerpts left out (#826), stored secrets redacted and the text bounded.
        '' when there is none or it cannot be read; the judgment then reads the
        message alone, as before.
        """
        service=self.service
        try:
            rows=service.store.history(through=job['id'])
            if not rows or rows[-1].get('job_id')!=job['id']:return ''
            earlier=service.planner_history(rows,service.store.config('file_workspace_document_jobs',[]))
            earlier=earlier[-PREPARATION_CONVERSATION_TURNS:]
            try:
                from .current_context import host_zone
                _zone,tz=host_zone(service.store)
            except Exception:
                tz=None
            lines=[earlier_turn_line(row,{'role':row['role'],'content':service._redact_known_secrets(row.get('content') or '')},tz)
                   for row in earlier]
            return '\n'.join(lines)[-PREPARATION_CONVERSATION_CHARS:]
        except Exception:
            LOG.info('preparation conversation unavailable job=%s',job.get('id'))
            return ''

    def preparation_fields(self, args, now, refused_watch=None):
        """The validated fields of one ``schedule_preparation`` call (#659), or a ``PreparationRefusal``.

        Shared by the worker's tool and the look-back's suggestions (#583), so both pass the same
        normalisers.  ``refused_watch(goal)`` is the tool's check that a refused watch is not split.
        """
        service=self.service
        has_window=args.get('every_minutes') not in (None,'') or args.get('until') not in (None,'')
        kind=args.get('kind')
        if kind not in prep.KINDS:raise prep.PreparationRefusal('invalid_kind')
        # Pilot boundary 1: a stored secret never becomes goal text.
        goal=prep.normalize_goal(service._redact_known_secrets(args.get('goal')),
                                 prep.BRIEF_MAX_CHARS if kind==prep.KIND_PREPARE else prep.GOAL_MAX_CHARS,kind)
        # #393: what the owner reads and accepts, apart from the run's brief (a reminder's goal already is it).
        title,offer=(prep.normalize_offer(service._redact_known_secrets(args.get('title') or ''),
                                          service._redact_known_secrets(args.get('offer') or ''))
                     if kind==prep.KIND_PREPARE else (None,None))
        if not has_window and refused_watch is not None and refused_watch(goal):
            raise prep.PreparationRefusal('window_retry_required')
        zone_name=service.context_observations.settings().get('timezone') or ''
        due_at,timezone=prep.parse_due(args.get('due'),args.get('timezone') or '',zone_name,now)
        # #580: rules and the legacy/window languages are mutually exclusive.
        if args.get('rrule') is not None and (args.get('recurrence') is not None or has_window
                                                 or args.get('max_runs') not in (None,'')):
            raise prep.PreparationRefusal('invalid_rrule')
        recurrence=(prep.normalize_rrule(args['rrule']) if args.get('rrule') is not None else
                    prep.normalize_recurrence(args.get('recurrence')))
        # #719: a watch - every N minutes from due until a deadline, bounded.
        window=None
        requested_until=None
        if has_window:
            window=prep.normalize_window(args.get('every_minutes'),args.get('until'),args.get('max_runs'),
                                         due_at,timezone,now,recurrence=recurrence)
            requested_until=prep.parse_due(args.get('until'),timezone,timezone,now)[0]
        when_needed=args.get('delivery')==prep.DELIVERY_WHEN_NEEDED
        if when_needed and kind!=prep.KIND_PREPARE:raise prep.PreparationRefusal('invalid_delivery')
        delivery_mode=prep.DELIVERY_WHEN_NEEDED if when_needed else None
        report_every_minutes=args.get('report_every_minutes')
        terms=prep.window_terms(window,recurrence,due_at,timezone,report_every_minutes,delivery_mode,requested_until)
        return {'kind':kind,'goal':goal,'title':title,'offer':offer,'due_at':due_at,'timezone':timezone,
                'recurrence':recurrence,'window':window,'delivery_mode':delivery_mode,'window_terms':terms,
                'report_every_minutes':report_every_minutes,'requested_until':requested_until}

    def preparation_channel(self, job, kind, delivery):
        """Where a preparation's results go (#306 review): the surface ``job`` came from, or kept in Still.

        A reminder is sent back to the surface the request came from (Telegram for a request made
        elsewhere, as before); ``CHANNEL_WEB`` keeps the result in Still.
        """
        service=self.service
        surface=service.surface_of(job) if job else None
        return ((surface.name if surface else prep.CHANNEL_TELEGRAM)
                if kind==prep.KIND_REMINDER or delivery in ('send',prep.DELIVERY_WHEN_NEEDED)
                else prep.CHANNEL_WEB)

    def preparation_scheduler(self, job, prompt):
        """The ``schedule_preparation`` handler bound to one Work (#659).

        #717 (owner direction 2026-11-07): what the owner's AI sets up inside a Work the owner's own
        typed message started is ``scheduled`` at once - the owner asked for it or the AI judged it
        serves them - and the owner gets the existing message with a cancel button (act, then tell,
        as C5's ask-less memory policy).  A prepared run still cannot take an approval-requiring
        step (#619), so this schedules reading and reporting only.  A goal the owner declined
        before is scheduled only when the owner's own message asks for it again (judged); otherwise
        it is refused, as before.  A Work the owner did not type - a continuation, a retry, a
        replayed turn - still only proposes; a scheduled run proposes nothing.
        """
        service=self.service
        def schedule(args):
            # #780: the daily plan's own run sets up reading-and-reporting work; what it sets up never
            # pays (``SELF_SET``) and cannot schedule in turn, so one misled plan run stays bounded.
            planning=self.day_plan_run(job) and self.day_plan_on()
            if service.unattended_work(job) and not planning:
                # #619: a scheduled run leaves no proposal waiting; it says what it would schedule.
                raise not_run(ToolError(UNATTENDED_APPROVAL_TEXT,'unattended_run'))  # #618: Still refused it, ran: false
            now=service.preparations.clock()
            try:
                fields=service.preparation_fields(args,now,lambda goal:service.repeats_refused_watch(job['id'],goal))
            except prep.PreparationRefusal as exc:
                # #785: the failure record keeps only bounded window arguments, never the brief or offer.
                window_args={key:service._redact_known_secrets(str(args.get(key)))[:160]
                             for key in ('every_minutes','until','max_runs','recurrence','due','report_every_minutes') if args.get(key) not in (None,'')}
                detail=service._redact_known_secrets(str(exc))[:600]+' '+json.dumps(window_args,ensure_ascii=False)
                raise not_run(ToolError(detail,exc.code)) from None
            kind,goal,title,offer=fields['kind'],fields['goal'],fields['title'],fields['offer']
            due_at,timezone,recurrence,window=fields['due_at'],fields['timezone'],fields['recurrence'],fields['window']
            delivery_mode=fields['delivery_mode']
            # #780: what a plan run wants the owner to hear goes where the owner last talked to Still.
            channel=service.preparation_channel(self.owner_last_work() if planning else job,kind,args.get('delivery'))
            summary=prep.proposal_summary({'kind':kind,'goal_text':goal,'due_at':due_at,'timezone':timezone,
                                           'recurrence':prep.RECURRENCE_WINDOW if window else recurrence,
                                           'every_seconds':window and window[0],'until_at':window and window[1],
                                           'max_runs':window and window[2],'delivery_mode':delivery_mode,'window_terms':fields['window_terms'],
                                           'channel':channel,'title_text':title,'offer_text':offer},service.home_zone())
            # #717: the owner's own typed Work (the same test as a direct settings request, #619);
            # #774: a location continuation re-runs an earlier message, so it is not one.
            owner_work=service.owner_typed_work(job)
            # #717: the judgment no longer decides whether it is scheduled, only who asked: a run the
            # owner asked for keeps what the owner's payment autonomy allows (``self_set_run``).
            asked=bool(owner_work and service.decision_judge.explicit_preparation_request(
                job['message'],summary,service.preparation_conversation(job)).outcome==JUDGMENT_YES)
            accepted_by=(prep.ACCEPTED_DAY_PLAN if planning else
                         prep.ACCEPTED_OWNER_REQUEST if asked else
                         prep.ACCEPTED_OWNER_WORK if owner_work else None)
            accepted=accepted_by is not None
            # #766: the preparation this one takes the place of (a changed target or plan); it stops
            # once this one is scheduled.  Only in the owner's own Work, which schedules at once, so a
            # proposal never stops what is running.
            replaces=args.get('replaces')
            current=None
            if replaces not in (None,''):
                current=service.preparations.get(replaces) if isinstance(replaces,str) else None
                if not current or current['state'] not in prep.ACTIVE_STATES:
                    raise not_run(ToolError(str(prep.PreparationRefusal('replace_not_found')),'replace_not_found'))
                # #780: a plan run adds work; it never stops what the owner or an earlier run set up.
                if not accepted or planning:
                    raise not_run(ToolError(str(prep.PreparationRefusal('replace_not_allowed')),'replace_not_allowed'))
            # #798: the entrusted line this row works for: the replaced row's line, kept through the
            # replacement, or the line the owner's AI continues when the owner starts the same work again.
            line_id=self.scheduled_line(args.get('line'),current,accepted=accepted,planning=planning)
            try:
                # #582: a goal the owner declined is not set up again unless they ask for it again.
                if not asked and service.declined_before(job['id'],goal,now):
                    raise not_run(prep.PreparationRefusal('declined_before'))
                row=service.preparations.create(kind=kind,goal=goal,due_at=due_at,timezone=timezone,recurrence=recurrence,
                                             channel=channel,created_from=job['id'],
                                             state=prep.STATE_SCHEDULED if accepted else prep.STATE_PROPOSED,
                                             accepted_by=accepted_by,
                                             window=window,delivery_mode=delivery_mode,title=title,offer=offer,
                                             report_every_minutes=fields['report_every_minutes'],line_id=line_id,
                                             requested_until=fields['requested_until'])
                # #574: another Work's identical proposal is this one; the owner's own Work accepts it.
                already=bool(row.get('already_proposed'))
                if already and accepted:row=service.preparations.accept(row['id'],accepted_by)
            except prep.PreparationRefusal as exc:
                raise ToolError(str(exc),exc.code,**ran_field(exc)) from None
            LOG.info('preparation %s id=%s work=%s kind=%s already_proposed=%s',row['state'],row['id'],job['id'],kind,already)
            scheduled=row['state']==prep.STATE_SCHEDULED
            when=prep.local_text(row['due_at'],row['timezone'])
            label='알림' if kind==prep.KIND_REMINDER else '준비'
            if already:
                # The earlier proposal's own message already went; no new message follows for it.
                next_step=(f'이미 제안되어 있던 {when} {label}을 소유자의 요청대로 예약했습니다. 대화로 취소를 요청할 수 있습니다.' if scheduled else
                           f'{when} {label}은 이미 제안되어 있어 새로 만들지 않았습니다. 소유자가 그 제안을 수락해야 예약됩니다(Telegram의 수락 버튼이나 대화로 수락).')
            elif planning:
                next_step=msg('preparations.day_plan_scheduled',when=when)
            else:
                # #1455: managed in the conversation: a cancel button follows, and the owner may just say so.
                next_step=(f'{when}에 {label}을 예약했습니다. 취소 버튼이 담긴 메시지가 따로 가고, 대화로 취소를 요청해도 됩니다.' if scheduled else
                           f'{when} {label}을 제안했습니다. 소유자가 수락해야 예약됩니다(Telegram의 수락 버튼이나 대화로 수락).')
            result={'preparation_id':row['id'],'kind':kind,'state':row['state'],'scheduled':scheduled,
                    'requires_owner_acceptance':row['state']==prep.STATE_PROPOSED,
                    'already_proposed':already,
                    'due':prep.iso(row['due_at'],row['timezone']),'due_local':when,'now_local':prep.local_text(now,row['timezone']),
                    'recurrence':row['recurrence'] or 'none','delivery':service.preparation_delivery(row),
                    'accepted_by':row['accepted_by'],'next_step':next_step}
            if row.get('line_id'):result['line_id']=row['line_id']
            if scheduled and replaces not in (None,'') and replaces!=row['id']:
                stopped=service.cancel_preparation(replaces)
                LOG.info('preparation replaced id=%s by=%s work=%s changed=%s',replaces,row['id'],job['id'],stopped['changed'])
                result['replaced']={'preparation_id':replaces,'state':stopped['state']}
                result['next_step']+=f' 이 준비가 대신하는 {replaces}는 더 실행되지 않습니다. 답에서 무엇을 멈추고 무엇으로 바꿨는지 한 줄로 말하세요.'
            if window:
                result.update(every_minutes=row['every_seconds']//60,until=prep.iso(row['until_at'],row['timezone']),
                              max_runs=row['max_runs'],
                              # #404: the model sees when its last check is (#701: at most until).
                              last_check=prep.iso(prep.last_slot(row),row['timezone']))
                terms=prep.stored_window_terms(row)
                if terms.get('requested_until') is not None:
                    result.update(requested_until=prep.iso(terms['requested_until'],row['timezone']),
                                  covering_every_minutes=terms['covering_every_minutes'])
                if terms.get('recurrence'):result['window_recurrence']=terms['recurrence']
                if terms.get('report_seconds'):result['report_every_minutes']=terms['report_seconds']//60
            return result
        return schedule

    def scheduled_line(self, line, replaced, *, accepted, planning):
        """#798: the line a new row is linked to, or None; a refusal when it cannot be."""
        inherited=(replaced or {}).get('line_id')
        if line in (None,''):
            if not inherited or self.service.lines.provenance(inherited).get('by')!='look-back':return inherited
            line=inherited
        if not isinstance(line,str):
            raise not_run(ToolError(msg('lines.refused_line_not_found'),'line_not_found'))
        if inherited and inherited!=line:
            raise not_run(ToolError(msg('lines.refused_line_mismatch'),'line_mismatch'))
        stood=self.service.lines.provenance(line).get('by')=='look-back'
        if stood and not self.line_announced(line):
            raise not_run(ToolError(msg('lines.refused_line_not_announced'),'line_not_announced'))
        if (planning or not accepted) and not (planning and stood and self.line_announced(line)):
            # A plan run or a proposal never continues the owner's line on its own.
            raise not_run(ToolError(msg('lines.refused_unattended_run'),'unattended_run'))
        brief=self.service.lines.readable(line)
        if brief is None:
            raise not_run(ToolError(msg('lines.refused_line_not_found'),'line_not_found'))
        if brief['state']!=el.STATE_ACTIVE:
            raise not_run(ToolError(msg('lines.refused_line_stopped'),'line_stopped'))
        return line

    def day_plan_run(self, job):
        """Whether ``job`` is a run of the daily plan itself (#780), not of something it set up."""
        preparation_id=prep.preparation_of((job or {}).get('request_key'))
        if preparation_id is None:return False
        row=self.service.preparations.get(preparation_id)
        return bool(row and row.get('origin')==prep.ORIGIN_DAY_PLAN)

    def owner_last_work(self):
        """The newest Work no schedule started (#780): where the owner last talked to Still, or None."""
        with self.service.store.db() as db:
            row=db.execute("SELECT * FROM jobs WHERE request_key IS NULL OR (request_key NOT LIKE ? AND request_key NOT LIKE 'line-announce:%' AND request_key NOT LIKE 'line-bundle:%') ORDER BY created DESC LIMIT 1",
                           (prep.REQUEST_KEY_PREFIX+'%',)).fetchone()
        return dict(row) if row else None

    def look_back_on(self):
        """The owner's look-back switch (and the pause above it), which the daily plan follows (#780)."""
        settings=self.service.owner_model.settings()
        return bool(settings.get('enabled') and settings.get('dream'))

    def day_plan_on(self):
        """Whether the daily plan may run and schedule now (#780): the switch is on and the owner has not opted out."""
        return self.look_back_on() and not (self.service.store.config(DAY_PLAN_KEY,{}) or {}).get('opted_out')

    def ensure_day_plan(self, now):
        """Keep the daily plan as the owner left it (#780); the row it created, or None.

        ``DAY_PLAN_KEY`` remembers the row Still set up.  When that row is cancelled or no longer
        exists (the owner deleted it from the list), that is the owner's no: it is recorded as an
        opt-out and nothing is set up again until the owner turns the look back on again
        (``day_plan_opt_in``).  While the look-back switch is off, the plan's due slots pass unrun
        and no plan is created.  The plan is one ordinary daily ``prepare`` whose result is kept;
        the owner changes its time or pauses it like any other.  Its brief is a method
        (``prompts/day_plan``), never a topic.
        """
        service=self.service
        state=dict(service.store.config(DAY_PLAN_KEY,{}) or {})
        if state.get('opted_out'):return None
        row=service.preparations.get(state['row']) if state.get('row') else None
        if state.get('row') and (row is None or row['state']==prep.STATE_CANCELLED):
            service.store.put(DAY_PLAN_KEY,{'opted_out':True})
            LOG.info('daily plan opted out id=%s',state['row'])
            return None
        if not state.get('row'):
            # The row and its marker are two writes: a stop between them leaves an active plan with
            # no marker.  That plan is the one Still set up; it is tracked again, never doubled.
            row=service.preparations.day_plan()
            if row and row['state'] in prep.ACTIVE_STATES:service.store.put(DAY_PLAN_KEY,{'row':row['id']})
            else:row=None
        on=self.look_back_on()
        if row:
            # The tracked plan is the owner's to shape: one they gave an end (no repeat, an end
            # date) stays finished and is not replaced by a new indefinite one.
            if not on and row['state'] in prep.ACTIVE_STATES:service.preparations.defer(row['id'],now)
            return None
        if not on:return None
        from .current_context import resolve_local_timezone
        timezone=self.home_zone() or resolve_local_timezone(service.store)
        today=datetime.fromtimestamp(now,prep.zone(timezone)).replace(hour=prep.DAY_PLAN_HOUR,minute=prep.DAY_PLAN_MINUTE,
                                                                      second=0,microsecond=0).timestamp()
        first=today if today>now else prep.next_due(today,timezone,'daily',now)
        row=service.preparations.create(kind=prep.KIND_PREPARE,goal=DAY_PLAN_GOAL,due_at=first,timezone=timezone,
                                        recurrence='daily',channel=prep.CHANNEL_WEB,created_from=None,
                                        state=prep.STATE_SCHEDULED,accepted_by=prep.ACCEPTED_DAY_PLAN,
                                        title=msg('preparations.day_plan_title'),offer=msg('preparations.day_plan_offer'),
                                        origin=prep.ORIGIN_DAY_PLAN)
        service.store.put(DAY_PLAN_KEY,{'row':row['id']})
        LOG.info('daily plan set up id=%s due=%s',row['id'],prep.iso(row['due_at'],timezone))
        return row

    def day_plan_opt_in(self):
        """The owner turned the look back on (#780): an earlier opt-out of the daily plan ends."""
        service=self.service
        state=dict(service.store.config(DAY_PLAN_KEY,{}) or {})
        # A plan cancelled or deleted since the last tick is an opt-out the tick has not recorded
        # yet; the owner's later yes ends it all the same, whichever came first.  So it does for a
        # plan the owner let finish.
        row=service.preparations.get(state['row']) if state.get('row') else None
        if state.get('opted_out') or (state.get('row') and (row is None or row['state'] not in prep.ACTIVE_STATES)):
            service.store.put(DAY_PLAN_KEY,{})

    def day_plan_record(self, job):
        """What earlier plan runs set up and how it went, as one block for a plan run (#780); '' when none.

        Observed fields only: when each was due, its state and last outcome, and in how many later
        Works its result was placed in the assistant's context.  Whether that was useful is the
        plan run's own judgment against the conversation it is shown.
        """
        service=self.service
        now=service.preparations.clock()
        rows=service.preparations.set_by_day_plan(now-prep.DAY_PLAN_RECORD_SECONDS)
        if not rows:return ''
        items=[]
        for row in rows:
            state=row['state']
            reason=None
            if state==prep.STATE_RUNNING and ws.is_awaiting_connection(row['last_work_status']):
                state,reason='blocked','awaiting_connection'
            elif state==prep.STATE_EXPIRED:
                reason='expired'
            item={'title':prep.owner_title(row),'due':prep.iso(row['due_at'],row['timezone']),'state':state,
                  'last_outcome':row.get('last_outcome') or 'none',
                  'result_ready_at':prep.iso(row['prepared_at'],row['timezone']) if row.get('prepared_at') else None,
                  'placed_in_later_requests':row['offered_works']}
            if reason:item['reason']=reason
            items.append(item)
        return 'earlier_plans: '+service._redact_known_secrets(json.dumps(items,ensure_ascii=False,separators=(',',':')))

    def preparation_delivery(self, row):
        """``send`` / ``keep`` / ``when_needed`` / ``bundle`` for one preparation row."""
        if self.held_row(row):return 'bundle'
        if row.get('delivery_mode')==prep.DELIVERY_WHEN_NEEDED:return prep.DELIVERY_WHEN_NEEDED
        return 'send' if row['channel']!=prep.CHANNEL_WEB else 'keep'

    def notice_destination(self, channel):
        """Resolve only the preparation's own conversation surface and current pairing."""
        surface = next((item for item in self.service.surfaces if item.name == channel), None)
        pairing = surface.pairing() if surface is not None else None
        if pairing is None:
            return None
        generation = surface.owner_generation()
        if generation and pairing.channel == surface.channel_prefix + generation:
            return pairing.owner, generation, surface.name
        return None

    def self_change_text(self, event):
        """One readable receipt, shared by messenger notices and the existing Settings result."""
        row = {**(self.service.preparations.get(event['preparation_id']) or {'goal_text': ''}),
               **event.get('snapshot', {})}
        operation = event['target'].split('.')[-1]
        lang = owner_language(self.service.store) or 'ko'
        def summary(values):
            schedule = {**row, **values}
            text = prep.proposal_summary(schedule, self.service.home_zone(), brief=False, lang=lang)
            if schedule.get('state') == prep.STATE_CANCELLED:
                text += '\n' + msg('preparations.self_change_cancel', lang)
            elif schedule.get('run_return_state') == prep.STATE_PAUSED or schedule.get('state') == prep.STATE_PAUSED:
                text += '\n' + msg('preparations.self_change_pause', lang)
            return text
        return self.service._redact_known_secrets(msg('preparations.self_change_notice', lang, id=event['preparation_id'],
                   change=msg('preparations.self_change_' + operation, lang), before=summary(event['before']),
                   after=summary(event['after']), reason=event['reason'],
                   undo=msg('button.restore_reservation', lang)))

    def budget_notice_destinations(self):
        """The same current owner pairings used by watches, across all conversation surfaces."""
        return [destination for surface in self.service.surfaces
                if (destination := self.notice_destination(surface.name)) is not None]

    def status_notice_text(self, notification):
        """Suppress obsolete waiting episodes and notices for stopped preparations."""
        service = self.service
        kind = notification['kind']
        if kind.startswith(prep.SELF_CHANGE_NOTICE_PREFIX):
            with service.store.db() as db:
                receipt = db.execute("SELECT detail FROM tool_events WHERE job_id=? AND tool='preparation_self_change' "
                                     "AND json_extract(detail,'$.fingerprint')=?",
                                     (notification['job_id'], notification['fingerprint'])).fetchone()
            if not receipt:
                return None
            event = json.loads(receipt['detail'])
            return self.self_change_text(event)
        if kind == prep.SCHEDULED_BUDGET_NOTICE:
            with service.store.db() as db:
                notice = db.execute('SELECT content FROM messages WHERE job_id IN (?,?) ORDER BY id LIMIT 1',
                                    (notification.get('fingerprint'), notification['job_id'])).fetchone()
            return notice['content'] if notice else None
        with service.store.db() as db:
            row = db.execute('SELECT * FROM preparations WHERE id=?',
                             (notification['fingerprint'],)).fetchone()
            if (not row or row['state'] == prep.STATE_CANCELLED
                    or row['channel'] != notification['surface']):
                return None
            if kind.startswith(prep.BLOCKED_NOTICE_PREFIX):
                job = db.execute('SELECT status FROM jobs WHERE id=?', (notification['job_id'],)).fetchone()
                latest = db.execute('SELECT id FROM telegram_notifications WHERE job_id=? AND kind GLOB ? '
                                    'ORDER BY rowid DESC LIMIT 1',
                                    (notification['job_id'], prep.BLOCKED_NOTICE_PREFIX + '*')).fetchone()
                if (row['state'] != prep.STATE_RUNNING or row['last_run_job_id'] != notification['job_id']
                        or row['window_end'] is not None and service.preparations.clock() >= row['window_end']
                        or not job or not ws.is_awaiting_connection(job['status'])
                        or not latest or latest['id'] != notification['id']):
                    return None
        title = service._redact_known_secrets(prep.owner_title(dict(row)))
        return msg('preparations.notice_waiting' if kind.startswith(prep.BLOCKED_NOTICE_PREFIX)
                   else 'preparations.notice_elapsed', title=title)

    def run_due_preparation(self, now=None):
        """One tick of owner-accepted preparations (#659).

        One indexed query.  Nothing due: no model, no network, no Telegram
        call.  A due slot is claimed together with its Work (``Preparations.start``);
        a finished run is settled and its recurrence advanced.  Delivery is the
        existing ``deliver_one``, so a slot is sent at most once.
        """
        service=self.service
        now=service.preparations.clock() if now is None else now
        # Consume delivery receipts, never queue insertion as evidence that the owner was told.
        for suggestion in service.preparations.rows():
            if suggestion['state'] != prep.STATE_SCHEDULED or suggestion.get('accepted_by') != prep.ACCEPTED_DREAM:
                continue
            notice = service.store.notification_of(
                f"{suggestion['created_from']}/dream:{suggestion['id']}", 'preparation_scheduled')
            if not notice or notice.get('sent_at') is None:
                # Before per-row keys, a confirmed notice could cover several rows on the anchor.
                legacy = service.store.notification_of(suggestion['created_from'], 'preparation_scheduled')
                if suggestion['id'] in prepared_ids(legacy):
                    notice = legacy
            sent_at = notice.get('sent_at') if notice else None
            if self.line_preparation_announced(suggestion):
                # The first work/account pair was told by the confirmed line announcement.
                # Preserve that observation; a queue insertion never qualifies.
                sent_at = suggestion.get('offered_at') or now
            if suggestion.get('offered_at') != sent_at:
                # Repair old queue-time marks too: only the existing send receipt is evidence.
                with service.store.db() as db:
                    db.execute('UPDATE preparations SET offered_at=? WHERE id=? AND state=?',
                               (sent_at, suggestion['id'], prep.STATE_SCHEDULED))
                suggestion['offered_at'] = sent_at
            held = not self.look_back_on() or suggestion.get('offered_at') is None
            # A first delivery after the due time must also release a previously unobserved hold.
            if suggestion.get('offered_at') is None or (notice and notice.get('sent_at') is not None
                                                       and notice['sent_at'] > suggestion['due_at']):
                service.preparations.hold_suggestion(suggestion, True, now)
            service.preparations.hold_suggestion(suggestion, held, now)
        # #583: an unanswered look-back suggestion lapses after a week (one read when there is none).
        service.preparations.expire_suggestions(now)
        # Tell each untold dream row in waking hours, without waiting for another owner Work.
        try:service.offer_one_off_suggestion(now)
        except Exception as exc:  # noqa: BLE001 - an offer never stops the preparations tick
            LOG.warning('dream suggestion not told kind=%s',type(exc).__name__)
        # #780: the daily plan exists once the look back is on (one read when it already does).
        try:self.ensure_day_plan(now)
        except Exception as exc:  # noqa: BLE001 - setting the plan up never stops the preparations tick
            LOG.warning('daily plan not set up kind=%s',type(exc).__name__)
        try:self.line_tick(now)
        except Exception as exc:  # noqa: BLE001 - line upkeep never stops existing preparations
            LOG.warning('line tick unavailable kind=%s',type(exc).__name__)
        # Fetch enough to skip paused/untold dream rows without hiding due owner work.
        rows=service.preparations.due(now,limit=prep.MAX_ACTIVE+prep.TICK_LIMIT)
        picked=0
        for row in rows:
            if row['state']==prep.STATE_SCHEDULED and row.get('accepted_by')==prep.ACCEPTED_DREAM:
                if not self.look_back_on() or row.get('offered_at') is None:
                    continue
            if picked>=prep.TICK_LIMIT:break
            picked+=1
            try:
                # If holding itself failed, leave legacy stood rows due until the hold can commit.
                if row['channel']!=prep.CHANNEL_WEB and self.held_row(row):continue
                # #306 review: delivered on the surface the preparation was made for, through its pairing now.
                surface=next((item for item in service.surfaces if item.name==row['channel']),None)
                pairing=surface.pairing() if surface is not None else None
                paired=pairing is not None
                when_needed=row.get('delivery_mode')==prep.DELIVERY_WHEN_NEEDED
                if row['state']==prep.STATE_RUNNING:
                    work_id=row.get('last_run_job_id')
                    if work_id:
                        with service.store.db() as db:
                            db.execute('BEGIN IMMEDIATE')
                            current=db.execute('SELECT state,last_run_job_id FROM preparations WHERE id=?',(row['id'],)).fetchone()
                            request=db.execute('SELECT id,state,expires FROM context_location_requests WHERE job_id=? '
                                               'ORDER BY created DESC LIMIT 1',(work_id,)).fetchone()
                            unanswered=(request and (request['state'] in ('expired','cancelled','superseded','cleared')
                                        or request['state']=='pending' and request['expires']<=now))
                            if current and current['state']==prep.STATE_RUNNING and current['last_run_job_id']==work_id and unanswered:
                                # #1301: the input prompt was delivered, not the
                                # requested result. Expiry/withdrawal must not
                                # promote that prompt into a prepared answer.
                                cause=msg('quickstart_service.scheduled_input_incomplete')
                                changed=db.execute("UPDATE jobs SET status='failed',error=?,owner_cause=? WHERE id=? "
                                                   f"AND status IN {ws.sql_in(ws.DELIVERED_RESULT)}",(cause,cause,work_id)).rowcount
                                if changed:
                                    if request['state']=='pending':
                                        db.execute("UPDATE context_location_requests SET state='expired' WHERE id=?",(request['id'],))
                                    db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                                               (work_id,'preparation','withheld',json.dumps({'reason':'owner-input-unanswered','resumed':False}),now))
                                    refusal=service.preparations.run_refusal(db,work_id,now)
                                    if refusal:service.preparations.refuse_run(db,work_id,now,refusal)
                    waiting=bool(work_id and service.work_has_pending_resume(work_id,now))
                    if work_id and (waiting or row.get('job_status')=='queued'):
                        with service.store.db() as db:
                            db.execute('BEGIN IMMEDIATE')
                            refusal=service.preparations.run_refusal(db,work_id,now)
                            current=db.execute('SELECT status FROM jobs WHERE id=?',(work_id,)).fetchone()
                            if refusal and current and (current['status']=='queued' or service.work_has_pending_resume(work_id,now)):
                                service.preparations.refuse_run(db,work_id,now,refusal)
                        if waiting:continue
                    # #719 and #797: settle a watch and queue any report in one transaction.
                    settled=service.preparations.settle(row,now,scrub=service.scrub_prepared_answer,decide=service.watch_judgment,
                                                     notify_to=(pairing.owner,surface.owner_generation(),surface.name) if paired else None,
                                                     waiting=lambda work:service.work_has_pending_resume(work,now),
                                                     close=self.watch_window_closed)
                    if settled:LOG.info('preparation settled id=%s outcome=%s next=%s decision=%s',row['id'],settled['last_outcome'],
                                        settled['state'],settled.get('last_decision') if when_needed else '-')
                    continue
                # #719: a when_needed run is kept in Still; only its notify decision reaches Telegram.
                send=paired and not when_needed
                work_id=service.preparations.start(row,channel=pairing.channel if send else 'web',
                                                chat_id=pairing.owner if send else None,now=now)
                if work_id:LOG.info('preparation started id=%s work=%s kind=%s',row['id'],work_id,row['kind'])
            except Exception as exc:
                LOG.warning('preparation tick failed id=%s kind=%s',row['id'],type(exc).__name__)
        return bool(picked)

    def watch_judgment(self, row, answer):
        """``'yes'`` / ``'no'`` / None: should this watch run reach the owner (#719)?

        The DecisionEngine's bounded judgment over the accepted goal, the
        run's scrubbed result and the last notification; never a text rule.
        """
        service=self.service
        result=str(answer or '')
        if len(result)>service.WATCH_RESULT_CHARS:result=result[:service.WATCH_RESULT_CHARS]+' [...]'
        brief=service.lines.readable(row['line_id']) if row.get('line_id') else None
        with self._judged_for_run(row):
            judged=service.decision_judge.watch_notification_needed(row['goal_text'],result,row.get('last_notified_text'),
                                                                 work_id=row.get('last_run_job_id'),
                                                                 offer=prep.accepted_offer(row) or None,
                                                                 report_promise=brief['report_promise'] if brief else None)
        return {JUDGMENT_YES:'yes',JUDGMENT_NO:'no'}.get(judged.outcome)

    @contextmanager
    def _judged_for_run(self, row):
        """Link a watch judgment's audit row to the run it judged (#818).

        The tick runs outside any Work, and ``record_decision`` links a judgment to the thread's current
        Work only.  The run's result now reaches the Judgment AI as written, so the run's information-use
        record must list that call.  No Work budget is attached; ``current_work_id`` is per thread.
        """
        service=self.service
        work_id=row.get('last_run_job_id')
        if not work_id or service.current_work_id:
            yield
            return
        service.current_work_id=work_id
        try:yield
        finally:service.current_work_id=None

    def watch_window_closed(self, row, answer):
        """A positive judgment ends only the current window, keeping any daily rhythm."""
        service=self.service
        result=str(answer)[:service.WATCH_RESULT_CHARS]
        try:
            with self._judged_for_run(row):
                judged=service.decision_judge.watch_window_closed(row['goal_text'],result,
                                                                  work_id=row.get('last_run_job_id'),
                                                                  offer=prep.accepted_offer(row) or None)
        except Exception as exc:  # A failed judgment leaves the accepted schedule active.
            LOG.warning('watch closing judgment unavailable kind=%s',type(exc).__name__)
            return False
        return judged.outcome==JUDGMENT_YES

    def watch_notification_text(self, notification):
        """The one Telegram message of a ``notify`` decision, or None when it no longer applies (#719)."""
        service=self.service
        job=service.store.job(notification.get('job_id'))
        row=service.preparations.get(prep.preparation_of((job or {}).get('request_key')))
        if not job or not row or row['state']==prep.STATE_CANCELLED:return None
        body=service.telegram_result_text(job['response'],
                                       job.get('owner_cause') or job.get('error'),job.get('status'),
                                       verified=job.get('owner_verified'),note=job.get('owner_note'))
        # #393: the watch is named as the owner accepted it, not by its run's brief.
        return ((msg('quickstart_service.watch_notification', title=prep.owner_title(row))+'\n\n')+body+
                ('\n\n'+msg('quickstart_service.stop_watching_hint')))

    def scrub_prepared_answer(self, job):
        """A prepared answer with stored secrets removed before reuse in later turns."""
        service=self.service
        return service.scrub_work_text(job['id'],job.get('response'))

    def scrub_work_text(self, work_id, text):
        """Apply only the stored-secret pass to text reused from a Work."""
        service=self.service
        return service._redact_known_secrets(str(text or ''))

    def preparation_run_fact(self, job):
        """What Still records about this run of a preparation, for the worker and the judgment (#671, #682).

        Which run it is, when the next is due (or that it is the last), when a watch's window ends,
        and that Still delivers the reply after the Work: read from the preparation row with the
        scheduler's own slot functions, so a reply that states them can be checked against them.
        '' for a Work no preparation started; the fixed #671 sentence when the row cannot be read.
        """
        service=self.service
        preparation_id=prep.preparation_of(job.get('request_key'))
        if not preparation_id or job.get('model')=='preparation':return ''
        try:
            row=service.preparations.get(preparation_id)
            if not row:return msg('preparations.run_fact')
            count,maximum,recurrence=int(row['run_count']),row.get('max_runs'),row.get('recurrence')
            # ``start`` counts the run and leaves ``due_at`` on its slot until the run is settled.
            if recurrence==prep.RECURRENCE_WINDOW:
                following=prep.next_window_slot(row,row['due_at'])
            else:
                following=prep.next_due(row['due_at'],row['timezone'],recurrence,row['due_at'],row.get('rule_start'),count)
            local=lambda slot:prep.local_text(slot,row['timezone'])
            schedule=(msg('preparations.run_next',due=local(following)) if following is not None
                      else msg('preparations.run_last'))
            if row.get('until_at') is not None:
                schedule+=msg('preparations.run_end',end=local(row['until_at']))
            terms=prep.stored_window_terms(row)
            if recurrence==prep.RECURRENCE_WINDOW and following is None and terms.get('requested_until') is not None:
                requested=terms['requested_until']
                if terms.get('recurrence'):
                    tz=prep.zone(row['timezone'])
                    requested=datetime.combine(datetime.fromtimestamp(row['until_at'],tz).date(),
                                               datetime.fromtimestamp(requested,tz).time(),tzinfo=tz).timestamp()
                schedule+=msg('preparations.run_daily_cap_end',end=local(row['until_at']),requested=local(requested))
            skipped=service.preparations.budget_skipped_fact(job)
            if skipped:
                facts={**skipped, 'first_slot':local(skipped['first_slot_at']), 'last_slot':local(skipped['last_slot_at'])}
                schedule+='\n'+msg('preparations.run_budget_skipped',facts=json.dumps(facts,ensure_ascii=False))
            delivery=msg('preparations.run_delivery_bundle') if self.held_row(row) else msg('preparations.run_delivery_needed' if row.get('delivery_mode')==prep.DELIVERY_WHEN_NEEDED
                         else 'preparations.run_delivery',channel=row['channel'])
            return msg('preparations.run_record', id=preparation_id,
                       position=msg('preparations.run_bounded',run=count,maximum=maximum) if maximum else str(count),
                       schedule=schedule,delivery=delivery)
        except Exception:
            LOG.info('preparation run fact unavailable job=%s',job.get('id'))
            return msg('preparations.run_fact')

    def preparation_history(self, job):
        """The only conversation a preparation's Work is shown (#659).

        Structural, not semantic: a Work started by a preparation (its
        request key) sees the scrubbed owner request that created it, its
        latest earlier delivered report and the accepted goal, never the
        unrelated conversation that happened since.  Profile, current context and
        prepared answers still arrive as the usual turn_context sections.
        """
        service=self.service
        rows=[]
        row=service.preparations.get(prep.preparation_of(job.get('request_key')))
        if row and row.get('origin')==prep.ORIGIN_DAY_PLAN:
            # #780: the daily plan reads the day, so it is shown the owner's recent conversation (the
            # same window as an owner-present Work, without other scheduled runs' turns) and Still's
            # record of what earlier plan runs set up.
            own=job.get('id')
            recent=[item for item in service.store.history(through=own)
                    if item.get('job_id')!=own and not prep.preparation_of(item.get('request_key'))][-PLAN_HISTORY_MESSAGES:]
            fact,record=service.preparation_run_fact(job),self.day_plan_record(job)
            # Existing rows keep their owner's schedule and brief; current line guidance reaches every run.
            goal=job['message']
            if DAY_PLAN_LINES not in goal:goal+='\n\n'+DAY_PLAN_LINES
            try:line_input=self.line_judgment_input(service.preparations.clock(),refs=False,lines_only=True)[0]
            except Exception as exc:  # noqa: BLE001 - optional context must not stop the plan
                LOG.warning('day-plan lines input unavailable kind=%s',type(exc).__name__)
                line_input=''
            content='\n\n'.join(part for part in (goal,fact,record,line_input) if part)
            return [*recent,{'role':'user','content':content,'job_id':own,'channel':job.get('channel'),'qualifier':None,
                             'line_judgment_input':line_input,
                             'request_key':job.get('request_key'),'created':job.get('created')}]
        # #583: a look-back suggestion was not made from its anchor's request; it is shown none.
        origin=(service.store.job(row['created_from'])
                if row and row.get('created_from') and row.get('origin')!=prep.ORIGIN_DREAM else None)
        # #367 review P1: a request the owner forgot is not shown to the preparation's Work again.
        if origin and origin.get('message') and not service.store.work_request_forgotten(origin['id']):
            rows.append({'role':'user','content':service.scrub_work_text(origin['id'],origin['message']),
                         'job_id':origin['id'],'channel':origin.get('channel'),'qualifier':None,
                         'request_key':origin.get('request_key'),'created':origin.get('created')})
        # #692: this preparation's latest earlier run that delivered a result, read from its own runs
        # (not the bounded recent window), so the run can say what changed since the last report.
        preparation_id=prep.preparation_of(job.get('request_key'))
        previous=None
        # #798: a line's runs read the reports of every row of the line, not only this row's.
        own=[preparation_id]
        if row and row.get('line_id'):
            own=[item['id'] for item in self.line_rows(row['line_id'])] or own
        if preparation_id and job.get('created') is not None:
            keys=' OR '.join('request_key LIKE ?' for _ in own)
            with service.store.db() as db:
                candidates=[dict(item) for item in db.execute(
                    f"SELECT * FROM jobs WHERE created<? AND id<>? AND ({keys}) "
                    "AND response IS NOT NULL AND TRIM(response)<>'' "
                    "AND (delivery='sent' OR (chat_id IS NULL AND delivery='none')) ORDER BY created DESC,id DESC",
                    (job['created'],job['id'],*(prep.REQUEST_KEY_PREFIX+item+':%' for item in own)))]
            previous=next((item for item in candidates
                           if prep.preparation_of(item['request_key']) in own and item['response'].strip()
                           and ws.has_delivered_result(item['status'])
                           and not service.store.work_request_forgotten(item['id'])),None)
        if previous:
            rows.append({'role':'assistant','content':service.scrub_work_text(previous['id'],previous['response']),
                         'job_id':previous['id'],'channel':previous.get('channel'),'qualifier':None,
                         'request_key':previous['request_key'],'created':previous['created']})
        # Both worker routes and the planner apply turn_context's existing earlier-turn bounds.
        # #671: the run is told it is one run of a schedule Still keeps and delivers.
        fact=service.preparation_run_fact(job)
        content=job['message']+('\n\n'+fact if fact else '')
        if previous:
            # #692: the worker's envelope shows earlier turns without their time; say which report it is.
            row_zone=(row or {}).get('timezone')
            content+='\n\n'+msg('preparations.previous_report',at=prep.local_text(previous['created'],row_zone))
        if row and prep.offered(row):
            # #393: the run works within what the owner read and accepted, not only its brief.
            content+='\n\n'+msg('preparations.accepted_offer_label')+'\n'+prep.accepted_offer(row)
        if row and row.get('line_id'):
            # #798: the line's current brief (its standing rules) and its earlier outcomes.
            content+='\n\n'+self.line_run_input(job,row)
        rows.append({'role':'user','content':content,'job_id':job['id'],'channel':job.get('channel'),'qualifier':None,
                     'request_key':job.get('request_key'),'created':job.get('created')})
        return rows

    def home_zone(self):
        """#393: the owner's time zone, so a proposal names a zone only when it differs."""
        service=self.service
        try:return service.context_observations.settings().get('timezone') or None
        except Exception:return None

    def preparation_proposal_html(self, bound):
        """#393: the Telegram proposal with each run's brief folded (expandable quote)."""
        service=self.service
        return service._redact_known_secrets(prep.proposal_html(bound['proposals'],bound['remaining'],service.home_zone()))

    def queue_preparation_proposal(self, job):
        """Offer this Work's unaccepted preparations to the paired owner once (#659).

        #1455: the ones the owner's own request scheduled get a message of their own with a cancel button,
        so a reservation is managed in the conversation, not in Settings first.
        """
        service=self.service
        rows=service.preparations.proposed_from(job['id'])
        if rows:service.queue_notification(job,'preparation_proposed',fingerprint=prep.digest(prep.proposal_page(rows)[0]))
        with service.store.db() as db:
            scheduled=[row['id'] for row in db.execute("SELECT id FROM preparations WHERE created_from=? AND state='scheduled' "
                                                      "AND accepted_by IN (?,?) ORDER BY created_at",(job['id'],prep.ACCEPTED_OWNER_REQUEST,prep.ACCEPTED_OWNER_WORK))]
        if scheduled:service.queue_notification(job,'preparation_scheduled',fingerprint=PREPARED_IDS_PREFIX+','.join(scheduled))

    def suggest_preparations(self, anchor_id, items, judgments=None):
        """Schedule the look-back's preparations (#583, #717) as rows of its anchor Work; ``(outcomes, calls)``.

        ``items`` passed ``om.validate_suggestions`` (at least one cited turn).  Each
        is then validated by ``schedule_preparation``'s own normalisers and skipped when a preparation
        with the same goal is active or the owner declined it recently (``declined_before``, whose
        judgment is the only model call here, counted in ``calls`` and asked only while the upkeep
        budget has one left).  #717 (owner direction 2026-11-07): it is scheduled at once
        (``ACCEPTED_DREAM``, never paying, ``self_set_run``); the owner is told once, in waking hours, with
        a cancel button (``queue_preparation_suggestion``).  An anchor that already had a proposal or a
        scheduled notice gets none.  Outcomes hold ids and reasons, no text.
        """
        service=self.service
        now=service.preparations.clock()
        anchor=service.store.job(anchor_id)
        with service.store.db() as db:
            offered = db.execute("SELECT 1 FROM telegram_notifications n JOIN preparations p "
                                 "ON n.job_id=p.created_from || '/dream:' || p.id "
                                 "WHERE p.created_from=? AND n.kind='preparation_scheduled' LIMIT 1",
                                 (anchor_id,)).fetchone()
        if (not anchor or offered or service.preparations.proposed_from(anchor_id)
                or service.store.notification_of(anchor_id,'preparation_proposed')
                or service.store.notification_of(anchor_id,'preparation_scheduled')):
            return [{'reason':'anchor-offered'} for _item in items],0
        if service.surface_of(anchor) is None:
            return [{'reason':'no-surface'} for _item in items],0
        outcomes,calls=[],0
        for item in items:
            args={name:item.get(name) for name in ('kind','goal','due','recurrence','delivery','title','offer')}
            once=args['recurrence']==om.SUGGESTION_ONCE
            if once:args['recurrence']=None
            try:
                line=item.get('line')
                if line:
                    brief=service.lines.readable(line)
                    if not (brief and brief['state']==el.STATE_ACTIVE and service.lines.provenance(line).get('by')=='look-back'
                            and self.line_announced(line)):
                        line=None
                fields=service.preparation_fields(args,now)
                if fields['recurrence'] is None and not once:raise prep.PreparationRefusal('invalid_recurrence')
                goal=fields['goal']
                if service.preparations.has_active_goal(goal):
                    outcomes.append({'reason':'active'})
                    continue
                declined=service.preparations.declined_goals(now)
                if declined and goal not in declined:
                    if service.owner_model.allowance(service.owner_model.clock())[0]<1:
                        outcomes.append({'reason':om.STOPPED_CAP})
                        continue
                    calls+=1
                if service.declined_before(anchor_id,goal,now,judge=judgments):
                    outcomes.append({'reason':'declined_before'})
                    continue
                row=service.preparations.create(kind=fields['kind'],goal=goal,due_at=fields['due_at'],timezone=fields['timezone'],
                                             recurrence=fields['recurrence'],
                                             channel=service.preparation_channel(anchor,fields['kind'],args['delivery']),
                                             created_from=anchor_id,state=prep.STATE_SCHEDULED,accepted_by=prep.ACCEPTED_DREAM,
                                             delivery_mode=fields['delivery_mode'],title=fields['title'],offer=fields['offer'],
                                             origin=prep.ORIGIN_DREAM,line_id=line or None,
                                             max_runs=None if once else prep.DREAM_MAX_RUNS)
            except prep.PreparationRefusal as exc:
                outcomes.append({'reason':exc.code})
                continue
            if row.get('already_proposed'):
                outcomes.append({'reason':'active'})
                continue
            outcomes.append({'preparation_id':row['id'],'kind':row['kind'],'recurrence':row['recurrence']})
            LOG.info('preparation scheduled by look back id=%s work=%s kind=%s',row['id'],anchor_id,row['kind'])
        return outcomes,calls

    def queue_preparation_suggestion(self, job, one_off=False):
        """Tell every untold scheduled dream once, each with its own existing cancel notice.

        All anchors are considered together in waking hours.  A stable per-row notice key uses
        the queue's existing uniqueness. Delivery receipts are consumed by the tick.
        Legacy unaccepted proposals keep their existing acceptance path.
        ``one_off`` remains a compatibility argument; standing rows are told on the tick too.
        """
        service=self.service
        now=service.preparations.clock()
        if not self.look_back_on():
            return None
        first=None
        tellable=service._owner_local_hour(now) in prep.SUGGESTION_OFFER_HOURS
        rows=service.preparations.untold_scheduled_suggestions() if tellable else []
        for row in rows:
            if self.line_preparation_announced(row):continue
            anchor=service.store.job(row['created_from'])
            if not anchor:continue
            surface=service.surface_of(anchor)
            if surface is None:continue
            notice_key=f"{row['created_from']}/dream:{row['id']}"
            # This new schedule is announced now; work-mode guards apply to this notice's
            # lifetime, not the older conversation used as evidence for the dream.
            notification=service.store.telegram.queue_notification(
                notice_key,anchor['chat_id'],surface.owner_generation(),'preparation_scheduled',
                fingerprint=PREPARED_IDS_PREFIX+row['id'],surface=surface.name,created=now)
            if notification is None:continue
            first=first or notification
            LOG.info('preparation suggestion queued anchor=%s id=%s',row['created_from'],row['id'])
        # Only legacy proposals still need the old grouped offer/acceptance message.
        anchor,rows=service.preparations.unoffered_suggestions(now,one_off=job is None)
        if rows:
            target=job or service.store.job(anchor)
            if target:
                rows=prep.proposal_page(rows)[0]
                service.queue_notification({**target,'id':anchor},'preparation_proposed',fingerprint=prep.digest(rows))
                notification=service.store.notification_of(anchor,'preparation_proposed')
                if notification is not None:
                    service.preparations.mark_offered(rows,now)
                    first=first or notification
        return first

    def offer_one_off_suggestion(self, now=None):
        """Tell all untold dream rows on the tick, within the owner's waking hours.

        The historical entry point also tells standing rows so they do not wait for a new Work.
        """
        service=self.service
        now=service.preparations.clock() if now is None else now
        if service._owner_local_hour(now) not in prep.SUGGESTION_OFFER_HOURS:return None
        return service.queue_preparation_suggestion(None)

    def preparation_cancel_markup(self, notification_id):
        """#1455: the one button that cancels exactly the reservations a message confirmed."""
        service=self.service
        return {'inline_keyboard':[[{'text':msg('button.cancel_reservation'),'callback_data':f"p7p:{notification_id}:cancel"}]]}

    def preparation_restore_markup(self, notification_id):
        """Undo only the cancellations recorded on this message."""
        return {'inline_keyboard':[[{'text':msg('button.restore_reservation'),'callback_data':f"p7p:{notification_id}:restore"}]]}

    def restore_preparation(self, preparation_id, *, cancelled_at=None):
        """Restore future runs without reopening a cancelled run's input requests."""
        return self.service.preparations.restore(preparation_id,cancelled_at=cancelled_at)

    def scheduled_notice_text(self, ids):
        """#1455: what a directly scheduled message says: each reservation, and that it is cancelled here."""
        service=self.service
        # A one-off can finish while its already-queued notice is still waiting for delivery.
        # Keep that receipt visible too, so settling the first run cannot silently erase it.
        rows=[row for row in (service.preparations.get(item) for item in ids) if row and (
            row['state'] in prep.ACTIVE_STATES or row.get('accepted_by')==prep.ACCEPTED_DREAM
            and row['state'] in (prep.STATE_DELIVERED,prep.STATE_FAILED,prep.STATE_UNKNOWN))]
        if not rows:return None
        return (msg('notice.preparation_scheduled')+'\n'+'\n'.join(service._redact_known_secrets(prep.proposal_summary(row,service.home_zone(),brief=False)) for row in rows)
                +'\n'+msg('notice.preparation_cancel_hint'))

    def offered_proposals(self, notification):
        """The exact proposals a proposal message shows, or [] if they changed (#659).

        Recomputed from the Work's current proposals; the buttons act only
        when the rendered set still has the digest the message was queued with.
        """
        service=self.service
        shown,remaining=prep.proposal_page(service.preparations.proposed_from(notification['job_id']))
        if not shown or prep.digest(shown)!=notification['fingerprint']:return [],0
        return shown,remaining

    def answer_preparation_proposal(self, notification, proposals, accept, by='button'):
        """The owner's yes or no to exactly the ``proposals`` one sent message offered (#659; #172 judged answers).

        The button's path, shared with a judged reply or reaction.  An accepted
        message keeps a cancel button bound to exactly what it accepted (#1455).
        Returns what the message now says.
        """
        service=self.service
        accepted,expired=[],[]
        for row in proposals:
            if not accept:
                service.cancel_preparation(row['id'])
                continue
            try:
                service.preparations.accept(row['id'],prep.ACCEPTED_OWNER_BUTTON if by=='button' else prep.ACCEPTED_OWNER_ANSWER)
                accepted.append(row)
            except prep.PreparationRefusal as exc:
                # #395: a watch whose window ended while the message waited is not started.
                if exc.code!='expired':raise
                service.cancel_preparation(row['id'])
                expired.append(row)
        result_kind=('preparation_accepted' if accepted else 'preparation_expired') if accept else 'preparation_denied'
        LOG.info('preparation %s by owner %s work=%s count=%s expired=%s','accept' if accept else 'deny',by,
                 notification['job_id'],len(proposals),len(expired))
        service.store.update_notification(notification['id'],result_kind,fingerprint=(
            PREPARED_IDS_PREFIX+','.join(row['id'] for row in accepted) if accepted else None))
        markup=service.preparation_cancel_markup(notification['id']) if accepted else {'inline_keyboard':[]}
        text=service.notification_text(result_kind)
        if accepted and expired:text+='\n'+service.notification_text('preparation_expired')
        if isinstance(notification.get('message_id'),int):
            try:service.telegram.edit_message_text(notification['chat_id'],notification['message_id'],text,markup)
            except ProviderError:pass
        return text

    def preparation_reply_prefix(self, job):
        """A prepared answer says what it was prepared for; a reminder needs nothing."""
        service=self.service
        preparation_id=prep.preparation_of(job.get('request_key'))
        if preparation_id is None or job.get('model')=='preparation':return ''
        row=service.preparations.get(preparation_id) or {'goal_text':job.get('message') or ''}
        return (msg('quickstart_service.prepared_result', title=prep.owner_title(row))+'\n\n')

    def preparations_status(self):
        """The Settings 준비해 둔 일 list: owner's own view (#659)."""
        service=self.service
        rows=[]
        lines={brief['line_id']:brief['task'] for brief in service.lines.lines()}
        for row in service.preparations.rows():
            # Preserve Lane S's decision Evidence; Settings claims a held failure was told
            # only after its bundle has the same receipt meaning as line_outcomes.
            if (row.get('last_decision_reason')==prep.REASON_RUN_FAILED_TOLD
                    and row.get('line_id') and not self.held_failure_told(row)):
                row={**row,'last_decision':prep.DECISION_QUIET,'last_decision_reason':prep.REASON_RUN_FAILED}
            last=str(row.get('last_response') or '')
            terms=prep.stored_window_terms(row)
            last=last[:280]+('…' if len(last)>280 else '')
            if row.get('self_change') and not row.get('self_change_undone'):
                last += '\n' + self.self_change_text(json.loads(row['self_change']))
            rows.append({'id':row['id'],'kind':row['kind'],'goal':row['goal_text'],'state':row['state'],
                         # #393: the owner's name and offer for it, when the AI wrote them.
                         'title':row.get('title_text'),'offer':row.get('offer_text'),
                         # #581: native form values are local wall times; parse_due owns zone resolution.
                         'due_local_input':datetime.fromtimestamp(row['due_at'],prep.zone(row['timezone'])).strftime('%Y-%m-%dT%H:%M'),
                         'due':row['due_at'],'due_iso':datetime.fromtimestamp(row['due_at'],prep.zone(row['timezone'])).isoformat(),'due_local':prep.local_text(row['due_at'],row['timezone']),
                         'recurrence_labels':{lang:prep.rule_text(row['recurrence'],lang) for lang in LANGUAGES} if str(row['recurrence'] or '').startswith('RRULE:') else None,
                         'timezone':row['timezone'],'recurrence':row['recurrence'],'delivery':service.preparation_delivery(row),
                         'accepted_by':row['accepted_by'],'last_outcome':row['last_outcome'],'last_work_id':row['last_run_job_id'],
                         'last_status':row.get('last_status'),'last_delivery':row.get('last_delivery'),
                         'last_result':last,'prepared_at':row['prepared_at'],
                         'created_at':row['created_at'],
                         # #719: the watch window, its bound and the last typed decision.
                         'every_minutes':(row['every_seconds']//60) if row.get('every_seconds') else None,
                         'until_local_input':datetime.fromtimestamp(row['until_at'],prep.zone(row['timezone'])).strftime('%Y-%m-%dT%H:%M') if row.get('until_at') else None,
                         'until_local':prep.local_text(row['until_at'],row['timezone']) if row.get('until_at') else None,
                         'until':datetime.fromtimestamp(row['until_at'],prep.zone(row['timezone'])).isoformat() if row.get('until_at') else None,
                         'max_runs':row.get('max_runs'),'run_count':row.get('run_count') or 0,
                         'max_window_runs':prep.MAX_DAILY_WINDOW_RUNS * (1 if terms.get('recurrence') else prep.MAX_WINDOW_SECONDS // 86400),
                         'window_recurrence':terms.get('recurrence'),
                         'report_every_minutes':terms['report_seconds']//60 if terms.get('report_seconds') else None,
                         'last_decision':row.get('last_decision'),'last_decision_reason':row.get('last_decision_reason'),
                         # #798: the entrusted line this row works for, by its task sentence.
                         'line_id':row.get('line_id'),'line_task':lines.get(row.get('line_id')),
                         'line_account':row.get('origin')==prep.ORIGIN_LINE_ACCOUNT})
        # #575: the page uses the same catalog in its selected language.
        labels={**{'budget_'+key:'scheduled_budget.'+key for key in ('label','help')}, 'save':'label.save', 'accept':'button.preparation_accept','decline':'button.preparation_decline',
                **{'delivery_'+mode:'preparations.delivery_'+mode for mode in ('send','keep','when_needed')},
                **{operation:'settings_orchestrator.label_preparations_'+operation for operation in ('pause','resume','edit','run_now')},
                **{field:'preparations.field_'+field for field in ('due','recurrence','every_minutes','until','max_runs',
                                                                              'rule','weekly_days','monthly_day','interval','rule_until')},
                **{'day_'+day:'preparations.day_'+day for day in prep.RULE_DAYS},
                'max_runs_hint':'preparations.max_runs_hint',
                'last_day':'preparations.rule_last_day','timezone':'label.time_zone','paused':'settings_orchestrator.paused','saved':'preparations.saved'}
        # The same daily notice is visible in local Settings when no sending surface is paired.
        from .telegram_locale import translate
        notice_id = prep.SCHEDULED_USAGE_PREFIX + service.preparations.budget_day(service.preparations.clock())
        with service.store.db() as db:
            notice = db.execute('SELECT content FROM messages WHERE job_id=? ORDER BY id LIMIT 1', (notice_id,)).fetchone()
        budget = {**service.preparations.budget_settings(), 'max':prep.MAX_SCHEDULED_DAILY_CALLS,
                  'notice':{lang:translate(notice['content'], lang) for lang in LANGUAGES} if notice else {}}
        return {'preparations':rows,'budget':budget,'management':{key:{lang:msg(message_id,lang) for lang in LANGUAGES} for key,message_id in labels.items()}}

    def cancel_preparation(self, preparation_id):
        """``Preparations.cancel`` plus withdrawing its run's pending location request (#774).

        A run that asked the owner for a location would otherwise continue on
        the answer and run the cancelled preparation's goal again.
        """
        service=self.service
        row=service.preparations.cancel(preparation_id)
        if row['state']==prep.STATE_CANCELLED and row.get('last_run_job_id'):
            if service.context_observations.cancel_work_requests(row['last_run_job_id']):
                service.store.remove_telegram_photo(row['last_run_job_id'])
        return row

    def preparation_request(self, body):
        """Owner Settings operations on one preparation (#659, #575)."""
        service=self.service
        if not isinstance(body,dict):raise ValueError(msg('quickstart_service.check_preparation_request'))
        operation=body.get('operation')
        if operation=='list':return service.preparations_status()
        if operation=='set_budget':
            service.preparations.set_budget(body.get('daily_calls'))
            return service.preparations_status()
        preparation_id=body.get('id')
        if not isinstance(preparation_id,str) or not preparation_id:raise ValueError(msg('quickstart_service.select_preparation'))
        if operation=='accept':service.preparations.accept(preparation_id,prep.ACCEPTED_OWNER_SETTINGS)
        elif operation=='cancel':service.cancel_preparation(preparation_id)
        elif operation=='delete':service.preparations.delete(preparation_id)
        elif operation=='pause':service.preparations.pause(preparation_id)
        elif operation=='resume':service.preparations.resume(preparation_id)
        elif operation=='restore':service.restore_preparation(preparation_id)
        elif operation=='edit':service.preparations.edit(preparation_id,**{key:value for key,value in body.items() if key not in ('operation','id')})
        elif operation=='run_now':
            row=service.preparations.get(preparation_id)
            if not row:raise prep.PreparationRefusal('not_found')
            surface=next((item for item in service.surfaces if item.name==row['channel']),None)
            pairing=surface.pairing() if surface is not None else None
            send=pairing is not None and row.get('delivery_mode')!=prep.DELIVERY_WHEN_NEEDED
            service.preparations.run_now(preparation_id,channel=pairing.channel if send else 'web',chat_id=pairing.owner if send else None)
        else:raise ValueError(msg('quickstart_service.unsupported_preparation'))
        LOG.info('preparation %s id=%s by owner settings',operation,preparation_id)
        return service.preparations_status()

    # -- #799: inferred lines, stood only by the look back -----------------------------------------

    def line_evidence(self, work_ids, now, *, minimum_days=2):
        """Resolve every citation from the store; no model-supplied provenance or dates."""
        from .current_context import owner_today
        from .quickstart_store import owner_words_work
        service=self.service
        if not isinstance(work_ids,list) or not work_ids:raise el.LineRefusal('line_evidence_sources')
        days=set()
        for work_id in work_ids:
            work=service.store.job(work_id) if isinstance(work_id,str) else None
            if (not owner_words_work(work) or not service.owner_typed_work(work)
                    or work.get('provider') in (None,'builtin') or work.get('status') not in ws.AVAILABLE_RESULT
                    or service.store.work_request_forgotten(work_id)
                    or service.store.managed_owner_state(work_id)
                    or not now-om.LINE_EVIDENCE_DAYS*86400 < work['created'] <= now):
                raise el.LineRefusal('line_evidence_sources')
            days.add(owner_today(service.store,work['created']))
        if len(days)<minimum_days:raise el.LineRefusal('line_evidence_one_day')
        return {'works':list(dict.fromkeys(work_ids)),'days':len(days)}

    def line_judgment_input(self, now, *, refs, exclude=(), lines_only=False):
        """Bounded lines/proposals/requests JSON and the whitelist of actually shown requests.

        ``lines_only`` is the daily plan's input: it may only link work to a line, so it is shown the
        lines and no proposals or owner requests (no owner words reach it outside its own history).
        """
        from .current_context import owner_today
        service=self.service
        with service.store.db() as db:
            reads=db.execute('SELECT e.job_id,e.detail FROM tool_events e JOIN jobs j ON j.id=e.job_id '
                             'WHERE e.tool=? AND j.owner_typed=1',(el.READ_EVENT,)).fetchall()
        read_ids={}
        for read in reads:
            detail=json.loads(read['detail'] or '{}')
            line_id=(detail.get('evidence') or detail).get('line_id')
            if line_id and service.owner_typed_work(service.store.job(read['job_id'])):
                read_ids.setdefault(line_id,set()).add(read['job_id'])
        lines=[]
        for brief in service.lines.lines():
            provenance=service.lines.provenance(brief['line_id'])
            outcomes=self.line_outcomes(brief['line_id'],limit=None)
            use={'runs':len(outcomes),'reached_owner':sum(o['outcome']==el.OUTCOME_DELIVERED for o in outcomes),
                 'kept':sum(o['outcome']==el.OUTCOME_KEPT for o in outcomes),
                 'failed':sum(o['outcome']==el.OUTCOME_FAILED for o in outcomes),'read_in_answers':len(read_ids.get(brief['line_id'],())),
                 'last_report':next((owner_today(service.store,o['at']) for o in outcomes if o['outcome']==el.OUTCOME_DELIVERED),None)}
            lines.append({**{key:brief[key] for key in ('line_id','version','task','report_promise','rules','change')},
                          'state':'waiting' if self.waiting_line(brief) else brief['state'],
                          'stood_by':provenance.get('by','owner'),'stood_on':owner_today(service.store,service.lines.first_saved(brief['line_id'])),
                          'use':use,'stopped_on':owner_today(service.store,brief['saved']) if brief['state']==el.STATE_STOPPED else None})
        if lines_only:
            block={'lines':lines}
            while len(json.dumps(block,ensure_ascii=False))>om.LINE_INPUT_CHARS//2 and lines:lines.pop()
            return json.dumps(block,ensure_ascii=False),{}
        with service.store.db() as db:
            rows=db.execute('SELECT * FROM preparations WHERE created_at>? AND created_at<=? '
                            'AND state IN (?,?,?) ORDER BY created_at DESC',
                            (now-om.LINE_EVIDENCE_DAYS*86400,now,prep.STATE_PROPOSED,prep.STATE_EXPIRED,prep.STATE_CANCELLED)).fetchall()
        proposals=[{'id':row['id'],'title':prep.owner_title(dict(row)),
                    'state':{prep.STATE_PROPOSED:'waiting',prep.STATE_EXPIRED:'expired',prep.STATE_CANCELLED:'declined'}[row['state']],
                    'date':owner_today(service.store,row['created_at']),'origin':row['origin']}
                   for row in rows if row['accepted_by'] in prep.SELF_SET or row['origin']==prep.ORIGIN_DREAM]
        block={'lines':lines,'proposals':proposals,'requests':[]}
        # Keep complete entries only. Truncated owner words never gain refs outside this block.
        while len(json.dumps(block,ensure_ascii=False))>om.LINE_INPUT_CHARS//2 and (proposals or lines):
            (proposals if proposals else lines).pop()
        mapping={}
        works=service.owner_model.dream_works(now-om.LINE_EVIDENCE_DAYS*86400,now,limit=None)
        for work in reversed(works):
            if work['id'] in exclude:continue
            try:self.line_evidence([work['id']],now,minimum_days=1)
            except el.LineRefusal:continue
            words=service.scrub_work_text(work['id'],work['message'])
            if not words.strip():continue
            ref=f'r{len(mapping)+1}' if refs else work['id']
            entry={'ref':ref,'date':owner_today(service.store,work['created']),'words':el.excerpt(words,200)}
            block['requests'].append(entry)
            if len(json.dumps(block,ensure_ascii=False))>om.LINE_INPUT_CHARS:
                block['requests'].pop()
                break
            mapping[ref]=work['id']
        return json.dumps(block,ensure_ascii=False),mapping

    def waiting_line(self, brief):
        return (brief['state']==el.STATE_ACTIVE and self.service.lines.provenance(brief['line_id']).get('by')=='look-back'
                and not self.line_announced(brief['line_id']))

    def stand_line(self, anchor_id, item, judgments, *, used_calls=0, shown_proposals=()):
        """Apply the look back's stand in refusal order; semantic identity belongs to its AI."""
        service=self.service
        now=service.preparations.clock()
        anchor=service.store.job(anchor_id)
        if not anchor or not service.owner_typed_work(anchor):raise el.LineRefusal('unattended_run')
        evidence=self.line_evidence(item['sources'],now)
        task=' '.join(service._redact_known_secrets(item['task']).split())
        key='look-back:'+anchor_id+':'+source_digest(task)
        briefs=service.lines.lines()
        for brief in briefs:
            if service.lines.provenance(brief['line_id']).get('stand_key')==key:return brief,0
        if sum(self.waiting_line(brief) for brief in briefs)>=om.LINE_WAITING_MAX:raise el.LineRefusal('line_waiting_max')
        stopped=[brief for brief in briefs if brief['state']==el.STATE_STOPPED and brief['change']!='withdrawn']
        goals=[brief['task'] for brief in stopped]
        calls=0
        if task in goals:raise el.LineRefusal('line_stopped_before')
        if goals:
            if service.owner_model.allowance(service.owner_model.clock())[0]<=used_calls:raise el.LineRefusal('line_stopped_before')
            calls+=1
            if judgments.declined_preparation_goal(task,goals,work_id=anchor_id).outcome!=JUDGMENT_NO:
                exc=el.LineRefusal('line_stopped_before');exc.calls=calls;raise exc
        for brief in briefs:
            provenance=service.lines.provenance(brief['line_id'])
            if (brief['state']==el.STATE_STOPPED or provenance.get('by')=='look-back') and set(evidence['works']) & set(provenance.get('evidence',{}).get('works',[])):
                exc=el.LineRefusal('line_evidence_held');exc.calls=calls;raise exc
        if any(brief['task']==task and brief['state']==el.STATE_ACTIVE for brief in briefs):
            exc=el.LineRefusal('line_active');exc.calls=calls;raise exc
        first=item.get('first')
        try:
            if not isinstance(first,list) or len(first)!=1 or not isinstance(first[0],dict):raise prep.PreparationRefusal('invalid_kind')
            args=first[0]
            fields=service.preparation_fields(args,now)
            if (fields['kind']!=prep.KIND_PREPARE or not fields['recurrence'] or fields['window']
                    or args.get('delivery') not in ('send',prep.DELIVERY_WHEN_NEEDED)):
                raise prep.PreparationRefusal('invalid_recurrence')
            if service.preparations.has_active_goal(fields['goal']):raise el.LineRefusal('line_active')
            declined=service.preparations.declined_goals(now)
            if declined and fields['goal'] not in declined:
                if service.owner_model.allowance(service.owner_model.clock())[0]<=calls+used_calls:raise el.LineRefusal(om.STOPPED_CAP)
                calls+=1
            if self.declined_before(anchor_id,fields['goal'],now,judge=judgments):raise el.LineRefusal('declined_before')
        except (prep.PreparationRefusal,el.LineRefusal) as exc:
            exc.calls=calls;raise
        brief={'task':task,'report_promise':service._redact_known_secrets(item['report_promise']),
               'open_matters':service._redact_known_secrets(item['open_matters']),
               'rules':[{'text':service._redact_known_secrets(text),'from':evidence['works'][0],'at':now,'basis':'inferred'}
                        for text in item['rules']]}
        stats={'change':'stood','by':'look-back','stand_key':key,
               'evidence':{**evidence,'supporting':list(dict.fromkeys(ref for ref in item['supporting'] if ref in shown_proposals))},'first':{**fields,'delivery':args['delivery']}}
        try:return service.lines.stand(anchor_id,brief,stats),calls
        except el.LineRefusal as exc:
            exc.calls=calls;raise

    def amend_stood_line(self, anchor_id, item):
        service=self.service
        anchor=service.store.job(anchor_id)
        if not anchor or not service.owner_typed_work(anchor):raise el.LineRefusal('unattended_run')
        line_id=item['line']
        current=service.lines.current(line_id)
        if current is None:raise el.LineRefusal('line_not_found')
        if current['state']!=el.STATE_ACTIVE:raise el.LineRefusal('line_stopped')
        if service.lines.provenance(line_id).get('by')!='look-back' or self.line_touches_money(line_id):
            raise el.LineRefusal('line_change_needs_owner')
        now=service.preparations.clock()
        evidence=self.line_evidence(item['sources'],now,minimum_days=1)
        def update(brief):
            drops=set(item['drop_rules'])
            if any(pos<1 or pos>len(brief['rules']) or brief['rules'][pos-1].get('basis')!='inferred' for pos in drops):
                raise el.LineRefusal('line_owner_rule')
            changes={key:service._redact_known_secrets(item[key]) for key in ('task','report_promise','open_matters') if item[key]}
            if service.lines.owner_written(line_id) and any(brief[key]!=value for key,value in changes.items()):
                raise el.LineRefusal('line_change_needs_owner')
            rules=[rule for pos,rule in enumerate(brief['rules'],1) if pos not in drops]
            rules.extend({'text':service._redact_known_secrets(text),'from':evidence['works'][0],'at':now,'basis':'inferred'}
                         for text in item['rules'])
            return {**brief,**changes,'rules':rules}
        _before,after=service.lines.write(line_id,anchor_id,item['expected_version'],'amended',update)
        return after

    def apply_line_actions(self, anchor_id, items, judgments, *, shown_proposals=()):
        """Only the look back's owner-typed anchor may produce these inferred writes."""
        service=self.service
        anchor=service.store.job(anchor_id)
        if not anchor or not service.owner_typed_work(anchor):return [{'reason':'unattended_run'} for _ in items],0
        outcomes,calls=[],0
        for item in items:
            if service.owner_model.allowance(service.owner_model.clock())[1]==om.STOPPED_PAUSED:
                outcomes.append({'reason':om.STOPPED_PAUSED});continue
            try:
                if item['action']=='stand':
                    brief,used=self.stand_line(anchor_id,item,judgments,used_calls=calls,shown_proposals=shown_proposals);calls+=used
                else:brief=self.amend_stood_line(anchor_id,item)
                outcomes.append({'line_id':brief['line_id'],'version':brief['version']})
            except (el.LineRefusal,prep.PreparationRefusal) as exc:
                calls+=getattr(exc,'calls',0)
                outcomes.append({'reason':exc.code})
        return outcomes,calls

    def line_announced(self, line_id):
        """A sent/web receipt or the owner's recorded restart; never an unattended inference."""
        with self.service.store.db() as db:
            return bool(db.execute("SELECT 1 FROM telegram_notifications WHERE kind LIKE ? AND state='sent' LIMIT 1",
                                   (el.NOTICE_OPENED+line_id+':%',)).fetchone()
                        or db.execute('SELECT 1 FROM jobs WHERE request_key=?',('line-announce:'+line_id+':1',)).fetchone()
                        or db.execute("SELECT 1 FROM procedure_versions WHERE slot=? AND state IN ('current','kept') "
                                      "AND work_id IS NOT NULL AND json_extract(stats_json,'$.change')='restarted' LIMIT 1",
                                      (el.slot(line_id),)).fetchone()
                        or db.execute("SELECT 1 FROM tool_events WHERE tool='line_restarted' AND status='succeeded' "
                                      "AND json_extract(detail,'$.line_id')=? LIMIT 1",(line_id,)).fetchone())

    def line_preparation_announced(self, row):
        """Only the initial work/account pair is covered by the line announcement.

        Later dream suggestions linked to the same line get their own per-row notice (#717).
        Identity comes from the saved first specification, independent of later brief versions.
        """
        line_id=row.get('line_id')
        if not line_id or row.get('accepted_by')!=prep.ACCEPTED_DREAM:return False
        stats=self.service.lines.provenance(line_id)
        if stats.get('by')!='look-back' or not self.line_announced(line_id):return False
        return (row.get('origin')==prep.ORIGIN_LINE_ACCOUNT
                or row.get('origin')==prep.ORIGIN_DREAM
                and row.get('created_from')==stats.get('stand_key','').split(':')[1]
                and row.get('goal_text')==stats.get('first',{}).get('goal'))

    def announce_stood_line(self, now):
        from .current_context import owner_today
        service=self.service
        day=owner_today(service.store,now)
        if service._owner_local_hour(now) not in prep.SUGGESTION_OFFER_HOURS:return
        anchor=self.owner_last_work()
        if not anchor:return
        waiting=sorted((brief for brief in service.lines.lines() if self.waiting_line(brief)),
                       key=lambda brief:service.lines.first_saved(brief['line_id']))
        for brief in waiting:
            line_id=brief['line_id']
            with service.store.db() as db:
                db.execute('BEGIN IMMEDIATE')
                events=db.execute("SELECT detail FROM tool_events WHERE tool='line_announced'").fetchall()
                if any(json.loads(row['detail']).get('day')==day for row in events):return
                notices=db.execute("SELECT n.state,n.sent_at FROM telegram_notifications n "
                                   "JOIN procedure_versions v ON n.kind LIKE 'line_opened:'||substr(v.slot,6)||':%' "
                                   "WHERE v.version=1 AND v.slot LIKE 'line:%' "
                                   "AND json_extract(v.stats_json,'$.by')='look-back'").fetchall()
                if any(row['state'] in ('queued','sending','unknown') for row in notices):return
                if any(row['sent_at'] is not None
                       and owner_today(service.store,row['sent_at'])==day for row in notices):return
                attempt=1+sum(json.loads(row['detail']).get('line_id')==line_id for row in events)
                db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                           (anchor['id'],'line_announced','succeeded',json.dumps({'line_id':line_id,'version':brief['version'],
                            'day':day,'via':anchor['channel']}),now))
            kind=f'{el.NOTICE_OPENED}{line_id}:{brief["version"]}:{attempt}'
            service.queue_notification(anchor,kind,fingerprint=f'{line_id}:{brief["version"]}')
            if anchor['channel']==prep.CHANNEL_WEB and not service.store.notification_of(anchor['id'],kind):
                # Web-only conversation: a durable assistant message is the announcement receipt.
                text=self.line_notice_text({'kind':kind,'fingerprint':f'{line_id}:{brief["version"]}'})
                if not text:return
                with service.store.db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    key='line-announce:'+line_id+':1'
                    if db.execute('SELECT 1 FROM jobs WHERE request_key=?',(key,)).fetchone():return
                    work_id=uuid.uuid4().hex
                    db.execute('INSERT INTO jobs(id,request_key,message,channel,status,response,delivery,provider,model,created) '
                               "VALUES (?,?,?,'web','succeeded',?,'none','builtin','line-announce',?)",(work_id,key,'',text,now))
                    db.execute("INSERT INTO messages(job_id,role,content,channel,created) VALUES (?,'assistant',?,'web',?)",
                               (work_id,text,now))
            return

    def start_announced_lines(self, now, *, line_id=None):
        service=self.service
        for brief in service.lines.lines():
            if line_id is not None and brief['line_id']!=line_id:continue
            try:
                stats=service.lines.provenance(brief['line_id'])
                if stats.get('by')!='look-back' or brief['state']!=el.STATE_ACTIVE or not self.line_announced(brief['line_id']):continue
                # The opening anchor is stable even after later amendments.
                anchor=service.store.job(stats['stand_key'].split(':')[1])
                if not anchor:continue
                first=stats['first']
                with service.store.db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    restart=self.line_start_restart(brief['line_id'],db)
                    rows=self.line_rows(brief['line_id'],db)
                    work=[row for row in rows if row.get('origin')!=prep.ORIGIN_LINE_ACCOUNT]
                    if not self.line_row_started(brief['line_id'],'work',restart,db):
                        if not work:
                            due=first['due_at']
                            if due<=now:due=prep.next_due(due,first['timezone'],first['recurrence'],now)
                            if due is None:continue
                            row=service.preparations.create(kind=first['kind'],goal=first['goal'],due_at=due,timezone=first['timezone'],
                                                           recurrence=first['recurrence'],channel=prep.CHANNEL_WEB,
                                                           bundle_channel=service.preparation_channel(anchor,first['kind'],first['delivery']),
                                                           created_from=anchor['id'],state=prep.STATE_SCHEDULED,accepted_by=prep.ACCEPTED_DREAM,
                                                           delivery_mode=first['delivery_mode'],title=first['title'],offer=first['offer'],
                                                           origin=prep.ORIGIN_DREAM,line_id=brief['line_id'],
                                                           db=db)
                            work=[row]
                        self.record_line_row_started(anchor['id'],brief['line_id'],'work',restart,db)
                # A cancelled work/account row is an owner decision, not a missing row to repair.
                # If creating the account failed, an active first row may retry that creation only.
                if any(row['state'] in prep.ACTIVE_STATES for row in work):
                    self.ensure_line_account(anchor,brief,accepted_by=prep.ACCEPTED_DREAM,channel=prep.CHANNEL_WEB,
                                             bundle_channel=service.preparation_channel(anchor,prep.KIND_PREPARE,'send'))
            except (prep.PreparationRefusal,el.LineRefusal) as exc:
                LOG.warning('line rows not started id=%s code=%s',brief['line_id'],exc.code)

    @staticmethod
    def line_start_restart(line_id, db):
        """Each explicit owner restart permits another start, even for an unchanged brief."""
        return db.execute("SELECT COALESCE(MAX(id),0) FROM tool_events WHERE tool='line_restarted' "
                          "AND status='succeeded' AND json_extract(detail,'$.line_id')=?",(line_id,)).fetchone()[0]

    @staticmethod
    def line_row_started(line_id, role, restart, db):
        return db.execute("SELECT 1 FROM tool_events WHERE tool='line_row_started' AND status='succeeded' "
                          "AND json_extract(detail,'$.line_id')=? AND json_extract(detail,'$.role')=? "
                          "AND json_extract(detail,'$.restart')=? LIMIT 1",(line_id,role,restart)).fetchone() is not None

    def record_line_row_started(self, work_id, line_id, role, restart, db):
        # Commit with creation: a failed creation retries, but deleting a created row stays deleted.
        db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                   (work_id,'line_row_started','succeeded',json.dumps({'line_id':line_id,'role':role,'restart':restart}),
                    self.service.preparations.clock()))

    def withdraw_unannounced(self, now):
        for brief in self.service.lines.lines():
            try:
                if self.waiting_line(brief) and now-self.service.lines.first_saved(brief['line_id'])>=prep.SUGGESTION_EXPIRY_SECONDS:
                    self.service.lines.write(brief['line_id'],None,brief['version'],'withdrawn',
                                             lambda item:{**item,'state':el.STATE_STOPPED})
            except el.LineRefusal as exc:
                LOG.warning('line not withdrawn id=%s code=%s',brief['line_id'],exc.code)

    def line_tick(self, now):
        for step in ('withdraw_unannounced','announce_stood_line','start_announced_lines',
                     'hold_line_rows','reconcile_line_bundles','flush_line_bundles'):
            try:getattr(self,step)(now)
            except (prep.PreparationRefusal,el.LineRefusal) as exc:
                LOG.warning('line tick step not applied step=%s code=%s',step,exc.code)
            except Exception as exc:
                # Upkeep failure must never skip holding or flushing already-held reports.
                LOG.warning('line tick step unavailable step=%s kind=%s',step,type(exc).__name__)

    def bundle_report_times(self, row, db):
        """Read-only display metadata using the same eligibility and frozen daily times as flushing."""
        if not self.held_row(row,db):return None
        # As with preparation budget days, an unset owner zone uses machine-local time.
        tz=prep.zone(self.home_zone())
        day=datetime.fromtimestamp(self.service.preparations.clock(),tz).date().isoformat()
        bundle=db.execute("SELECT e.detail FROM jobs j JOIN tool_events e ON e.job_id=j.id "
                          "WHERE e.tool='line_bundle' AND j.request_key LIKE ? ORDER BY j.created LIMIT 1",
                          ('line-bundle:'+day+':%',)).fetchone()
        return json.loads(bundle['detail'])['report_times'] if bundle else self.service.owner_model.settings(db)['report_times']


    def held_row(self, row, db=None):
        """Only unattended read/report rows of a look-back line spend the bundle budget.

        An event retains the send intent after routing the row to web. Owner-Work acceptance
        is deliberately excluded even though it is SELF_SET for payment purposes.
        """
        if (not row.get('line_id') or row.get('kind') != prep.KIND_PREPARE
                or row.get('accepted_by') not in (prep.ACCEPTED_DREAM, prep.ACCEPTED_DAY_PLAN)
                or row.get('recurrence') == prep.RECURRENCE_WINDOW):return False
        if self.service.lines.provenance(row['line_id']).get('by') != 'look-back':return False
        if row['channel'] != prep.CHANNEL_WEB or row.get('delivery_mode') == prep.DELIVERY_WHEN_NEEDED:return True
        if db is None:
            with self.service.store.db() as db:return self.held_row(row, db)
        return db.execute("SELECT 1 FROM tool_events WHERE tool='line_held' "
                          "AND json_extract(detail,'$.preparation_id')=?", (row['id'],)).fetchone() is not None

    def hold_line_rows(self, now):
        """Route held rows before the existing start/settle loop, without changing its slot logic."""
        with self.service.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            rows=[dict(r) for r in db.execute('SELECT * FROM preparations WHERE line_id IS NOT NULL')]
            for row in rows:
                if not self.held_row(row, db):
                    if row['accepted_by'] in (*prep.OWNER_ACCEPTED,prep.ACCEPTED_OWNER_WORK) and row['channel']==prep.CHANNEL_WEB:
                        held=db.execute("SELECT detail FROM tool_events WHERE tool='line_held' "
                                        "AND json_extract(detail,'$.preparation_id')=? ORDER BY id LIMIT 1",(row['id'],)).fetchone()
                        if held:
                            channel=json.loads(held['detail'])['channel']
                            db.execute('UPDATE preparations SET channel=? WHERE id=?',(channel,row['id']))
                    continue
                if not db.execute("SELECT 1 FROM tool_events WHERE tool='line_held' "
                                  "AND json_extract(detail,'$.preparation_id')=?", (row['id'],)).fetchone():
                    db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                               (row['created_from'],'line_held','succeeded',json.dumps({'preparation_id':row['id'],'channel':row['channel']}),now))
                db.execute('UPDATE preparations SET channel=? WHERE id=?', (prep.CHANNEL_WEB,row['id']))

    def line_bundle_parts(self, db, slot):
        """Re-read settled parts under the flush transaction; no latest-run pointer can lose a burst."""
        service=self.service
        parts=[]
        rows=[dict(r) for r in db.execute(
            "SELECT j.*, e.detail AS settlement, e.created AS settled_at FROM tool_events e JOIN jobs j ON j.id=e.job_id "
            "WHERE e.tool='preparation' AND json_type(e.detail,'$.outcome') IS NOT NULL "
            "AND j.delivery='none' "
            "AND EXISTS(SELECT 1 FROM tool_events h WHERE h.tool='line_held' "
            "AND json_extract(h.detail,'$.preparation_id')=json_extract(e.detail,'$.preparation_id') AND h.created<=e.created) "
            "AND NOT EXISTS(SELECT 1 FROM telegram_notifications n WHERE n.job_id=j.id AND n.kind=?) "
            # Consumption survives suppression: removed parts must never be bundled again.
            "AND NOT EXISTS(SELECT 1 FROM tool_events b WHERE b.job_id=j.id AND b.tool='line_bundled') "
            "AND NOT EXISTS(SELECT 1 FROM messages m JOIN forgotten_messages f ON f.message_id=m.id WHERE m.job_id=j.id) "
            "ORDER BY j.created,j.id",(prep.NOTIFY_KIND,))]
        seen=set()
        for job in rows:
            settlement=json.loads(job['settlement'])
            first_failure=(settlement.get('decision')==prep.DECISION_NOTIFY
                           and settlement.get('reason')==prep.REASON_RUN_FAILED_TOLD)
            if job['id'] in seen:continue
            if not first_failure and (not ws.has_delivered_result(job['status']) or not str(job['response'] or '').strip()):continue
            preparation_id=prep.preparation_of(job['request_key'])
            row=service.preparations.get(preparation_id)
            if not row:
                # Deleting a schedule retains its past Works/Evidence; it is not forgetting
                # the line or the finished answer. The first brief read is the run's
                # own line input; later tool calls may read unrelated lines too.
                read=db.execute("SELECT COALESCE(json_extract(detail,'$.evidence.line_id'),json_extract(detail,'$.line_id')) AS line_id "
                                "FROM tool_events WHERE job_id=? AND tool=? ORDER BY id LIMIT 1",(job['id'],el.READ_EVENT)).fetchone()
                if not read:continue
                row={'line_id':read['line_id'],'timezone':self.home_zone()}
            # L1 runs may already be in flight when the row is held. A report already handed
            # to notification delivery is excluded above, whatever that delivery's state.
            started=db.execute("SELECT detail,created FROM tool_events WHERE job_id=? AND tool='preparation' "
                               "AND json_type(detail,'$.slot') IS NOT NULL ORDER BY id LIMIT 1",(job['id'],)).fetchone()
            if started is None:continue
            run=json.loads(started['detail'])
            # Eligibility belongs to the run, not the row's later owner acceptance/channel.
            if run.get('accepted_by') not in (prep.ACCEPTED_DREAM,prep.ACCEPTED_DAY_PLAN) or run.get('channel')!=prep.CHANNEL_WEB:continue
            due=datetime.fromisoformat(run['slot']).timestamp()
            # An empty slot is not a licence to send a later report immediately. Only its
            # due work, finished within the settle allowance, can spend this slot.
            if due>slot or job['settled_at']>slot+BUNDLE_SETTLE_SECONDS:continue
            brief=service.lines.readable(row['line_id'])
            if not brief or brief['state']!=el.STATE_ACTIVE:continue
            # A stop discards old held parts permanently, including after an explicit restart.
            if db.execute("SELECT 1 FROM procedure_versions WHERE slot=? AND json_extract(stats_json,'$.change')='stopped' "
                          "AND saved>=?", (el.slot(row['line_id']),started['created'])).fetchone():continue
            if (run.get('delivery_mode')==prep.DELIVERY_WHEN_NEEDED
                    and settlement.get('decision')!=prep.DECISION_NOTIFY):continue
            text=service.lines.bundle_part_text(job)
            if not text:continue
            held=db.execute("SELECT detail FROM tool_events WHERE tool='line_held' "
                            "AND json_extract(detail,'$.preparation_id')=? ORDER BY id LIMIT 1",(preparation_id,)).fetchone()
            seen.add(job['id'])
            parts.append({'job':job,'row':row,'brief':brief,'text':text,
                          'destination':json.loads(held['detail'])['channel']})
        return parts

    def reconcile_line_bundles(self, now):
        """Advance watch notification state only after an unsuppressed bundle reaches the owner."""
        service=self.service
        with service.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            bundles=db.execute("SELECT j.id FROM jobs j WHERE j.model='line-bundle' AND j.provider='builtin' "
                               "AND (j.delivery='sent' OR (j.channel='web' AND j.delivery='none' "
                               "AND EXISTS(SELECT 1 FROM messages m WHERE m.job_id=j.id AND m.role='assistant'))) "
                               "AND EXISTS(SELECT 1 FROM tool_events e WHERE e.job_id=j.id AND e.tool='line_bundle') "
                               "AND NOT EXISTS(SELECT 1 FROM tool_events e WHERE e.job_id=j.id AND e.tool='line_bundle_notified') "
                               "ORDER BY j.created,j.id").fetchall()
            for bundle in bundles:
                parts=db.execute("SELECT j.* FROM tool_events b JOIN jobs j ON j.id=b.job_id "
                                 "WHERE b.tool='line_bundled' AND json_extract(b.detail,'$.bundle_id')=? "
                                 "AND NOT EXISTS(SELECT 1 FROM tool_events s WHERE s.job_id=b.job_id "
                                 "AND s.tool='line_bundle_suppressed' AND json_extract(s.detail,'$.bundle_id')=? "
                                 "AND json_extract(s.detail,'$.line_id')=json_extract(b.detail,'$.line_id')) "
                                 "ORDER BY j.created,j.id",(bundle['id'],bundle['id'])).fetchall()
                for part in parts:
                    text=service.lines.bundle_part_text(part)
                    service.preparations.record_notified(part['id'],prep.text_digest(text),text,now,db=db)
                db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                           (bundle['id'],'line_bundle_notified','succeeded',json.dumps({'parts':len(parts)}),now))

    def held_failure_told(self, row):
        """Settlement selects the first notice; the bundle's receipt says whether it was told."""
        with self.service.store.db() as db:
            held=db.execute("SELECT 1 FROM tool_events WHERE tool='line_held' "
                            "AND json_extract(detail,'$.preparation_id')=?",(row['id'],)).fetchone()
            if not held or db.execute("SELECT 1 FROM telegram_notifications WHERE job_id=? AND kind=?",
                                      (row['last_run_job_id'],prep.NOTIFY_KIND)).fetchone():return True
            return db.execute("SELECT 1 FROM tool_events b JOIN jobs j ON j.id=json_extract(b.detail,'$.bundle_id') "
                              "WHERE b.job_id=? AND b.tool='line_bundled' "
                              "AND (j.delivery='sent' OR (j.channel='web' AND j.delivery='none')) "
                              "AND NOT EXISTS(SELECT 1 FROM tool_events s WHERE s.job_id=b.job_id "
                              "AND s.tool='line_bundle_suppressed' AND json_extract(s.detail,'$.bundle_id')=j.id "
                              "AND json_extract(s.detail,'$.line_id')=json_extract(b.detail,'$.line_id'))",
                              (row['last_run_job_id'],)).fetchone() is not None

    def compose_line_bundle(self, parts):
        return self.service.lines.compose_line_bundle(parts)

    def flush_line_bundles(self, now):
        """Atomically hand each destination's local-slot bundle to existing builtin delivery."""
        service=self.service
        with service.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            # Ordinary preparations pay no per-run scan for a budget they do not use.
            if not db.execute("SELECT 1 FROM tool_events WHERE tool='line_held' LIMIT 1").fetchone():return None
            tz=prep.zone(self.home_zone())
            local=datetime.fromtimestamp(now,tz)
            day=local.date().isoformat()
            bundles=[dict(r) for r in db.execute("SELECT j.channel,j.created,e.detail FROM jobs j JOIN tool_events e ON e.job_id=j.id "
                                                "WHERE e.tool='line_bundle' AND j.request_key LIKE ? ORDER BY j.created",('line-bundle:'+day+':%',))]
            # Freeze today's schedule once it has spent a slot. Edits take effect tomorrow;
            # replacing times must not replenish a day's already-used attention budget.
            times=json.loads(bundles[0]['detail'])['report_times'] if bundles else service.owner_model.settings(db)['report_times']
            due=[t for t in times if t<=local.strftime('%H:%M')]
            if not due:return None
            at=due[-1]
            # The cap is per destination, including old bundles with unsuffixed keys.
            # Never backfill an earlier slot after this destination consumed the latest.
            used={b['channel'].split(':',1)[0] for b in bundles if json.loads(b['detail'])['slot']==at}
            slot=local.replace(hour=int(at[:2]),minute=int(at[3:]),second=0,microsecond=0).timestamp()
            if now<slot+BUNDLE_SETTLE_SECONDS:
                running=[dict(r) for r in db.execute('SELECT * FROM preparations WHERE state=? AND due_at<=?',(prep.STATE_RUNNING,slot))]
                if any(self.held_row(r,db) for r in running):return None
            parts=self.line_bundle_parts(db,slot)
            if not parts:return None
            destinations={}
            for part in parts:
                surface=next((s for s in service.surfaces if s.name==part['destination']),None)
                pairing=surface.pairing() if surface is not None else None
                # Same fallback as an unbundled preparation: keep it in the web
                # conversation when its recorded send surface is no longer paired.
                channel=pairing.channel if pairing else prep.CHANNEL_WEB
                chat=pairing.owner if pairing else None
                destination=channel.split(':',1)[0]
                destinations.setdefault(destination,{'channel':channel,'chat':chat,'parts':[]})['parts'].append(part)
            created=[]
            for destination,group in destinations.items():
                if destination in used:continue
                key='line-bundle:'+day+':'+at.replace(':','')+':'+destination
                text=self.compose_line_bundle(group['parts'])
                work_id=uuid.uuid4().hex
                channel,chat=group['channel'],group['chat']
                db.execute('INSERT INTO jobs(id,request_key,message,channel,chat_id,status,response,delivery,provider,model,created) '
                           "VALUES (?,?,?,?,?,'succeeded',?,?,'builtin','line-bundle',?)",
                           (work_id,key,'',channel,chat,text,'pending' if chat is not None else 'none',now))
                db.execute("INSERT INTO messages(job_id,role,content,channel,created) VALUES (?,'assistant',?,?,?)",(work_id,text,channel,now))
                for part in group['parts']:
                    db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                               (part['job']['id'],'line_bundled','succeeded',json.dumps({'bundle_id':work_id,'line_id':part['brief']['line_id']}),now))
                db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                           (work_id,'line_bundle','succeeded',json.dumps({'day':day,'slot':at,'report_times':times,'parts':len(group['parts'])}),now))
                created.append(work_id)
            return created[0] if created else None

    # -- #798: entrusted lines ---------------------------------------------------------------------

    def line_rows(self, line_id, db=None):
        """Every preparation row of one line, newest first."""
        if db is None:
            with self.service.store.db() as db:
                return self.line_rows(line_id, db)
        return [dict(row) for row in db.execute(
            'SELECT p.*, j.status AS last_status, j.delivery AS last_delivery FROM preparations p '
            'LEFT JOIN jobs j ON j.id=p.last_run_job_id WHERE p.line_id=? ORDER BY p.created_at DESC', (line_id,))]

    def line_outcomes(self, line_id, *, exclude=None, limit=el.OUTCOMES_SHOWN):
        """The line's earlier runs, newest first: each with what came of it and whether it reached the owner.

        Read from the runs of every row of the line (a replacement and a restart included), so a
        run sees what happened before under an earlier schedule.  A run whose request the owner
        forgot is left out; texts pass the stored-secret redaction (#818: saved values stay).
        """
        service=self.service
        with service.store.db() as db:
            ids=[row['id'] for row in db.execute('SELECT id FROM preparations WHERE line_id=?',(line_id,))]
            if not ids:return []
            keys=' OR '.join('j.request_key LIKE ?' for _ in ids)
            jobs=[dict(row) for row in db.execute(
                f"SELECT j.*,EXISTS(SELECT 1 FROM telegram_notifications n WHERE n.job_id=j.id AND n.kind=? AND n.state='sent') "
                f"AS notified, EXISTS(SELECT 1 FROM tool_events b JOIN jobs bundle ON bundle.id=json_extract(b.detail,'$.bundle_id') "
                f"WHERE b.job_id=j.id AND b.tool='line_bundled' "
                f"AND NOT EXISTS(SELECT 1 FROM tool_events s WHERE s.job_id=b.job_id AND s.tool='line_bundle_suppressed' "
                f"AND json_extract(s.detail,'$.bundle_id')=bundle.id "
                f"AND json_extract(s.detail,'$.line_id')=json_extract(b.detail,'$.line_id')) "
                f"AND (bundle.delivery='sent' OR (bundle.channel='web' AND bundle.delivery='none'))) "
                f"AS bundled FROM jobs j WHERE ({keys}) AND j.id IS NOT ? ORDER BY j.created DESC,j.id DESC LIMIT ?",
                (prep.NOTIFY_KIND,*(prep.REQUEST_KEY_PREFIX+item+':%' for item in ids),exclude,limit*4 if limit is not None else -1))]
        out=[]
        for job in jobs:
            if prep.preparation_of(job.get('request_key')) not in ids or service.store.work_request_forgotten(job['id']):
                continue
            # Reached the owner: its own reply was sent, or its watch notification was.
            label=el.outcome_label(job.get('status'),job.get('delivery')=='sent' or bool(job.get('notified')) or bool(job.get('bundled')))
            if label is None:continue
            text=(job.get('response') if label in (el.OUTCOME_DELIVERED,el.OUTCOME_KEPT)
                  else job.get('owner_cause') or job.get('error') or '')
            out.append({'work_id':job['id'],'at':job['created'],'outcome':label,
                        'text':el.excerpt(service.scrub_work_text(job['id'],text))})
            if limit is not None and len(out)>=limit:break
        return out

    def line_entry(self, brief):
        """One line as the list answer shows it: task, promise, last delivered report, next run or blocker."""
        service=self.service
        rows=self.line_rows(brief['line_id'])
        zone=service.home_zone() or (rows[0]['timezone'] if rows else None)
        delivered=[item for item in self.line_outcomes(brief['line_id'],limit=el.OUTCOMES_SHOWN)
                   if item['outcome']==el.OUTCOME_DELIVERED]
        entry={'line_id':brief['line_id'],'version':brief['version'],'state':brief['state'],'task':brief['task'],
               'report_promise':brief['report_promise'],
               'last_report':prep.local_text(delivered[0]['at'],zone) if delivered else None}
        if self.waiting_line(brief):entry['state']='waiting';return entry
        if brief['state']!=el.STATE_ACTIVE:return entry
        active=[row for row in rows if row['state'] in prep.ACTIVE_STATES]
        blocked=next((row for row in active if row['state']==prep.STATE_RUNNING
                      and row.get('last_status') in ws.WAITING_PREREQUISITE),None)
        if blocked:
            entry['blocked']=msg('lines.blocked_'+('connection' if blocked['last_status'] in ws.WAITING_CONNECTION_DRIVE
                                                   else 'owner_input'))
            return entry
        if any(row['state']==prep.STATE_RUNNING for row in active):
            entry['next']=msg('lines.running')
            return entry
        # The line's own work first; its periodic account is the fallback.
        work=[row for row in active if row['state']==prep.STATE_SCHEDULED and row.get('origin')!=prep.ORIGIN_LINE_ACCOUNT]
        account=[row for row in active if row['state']==prep.STATE_SCHEDULED and row.get('origin')==prep.ORIGIN_LINE_ACCOUNT]
        following=min(work or account,key=lambda row:row['due_at'],default=None)
        if following:
            entry['next']=prep.local_text(following['due_at'],following['timezone'])
            if not work:entry['next_is_account']=True
        elif any(row['state']==prep.STATE_PAUSED for row in active):
            entry['blocked']=msg('settings_orchestrator.paused')
        else:
            entry['blocked']=msg('lines.blocked_unscheduled')
        return entry

    def lines_listing(self):
        """``entrusted_line`` list: one entry per active line, then the stopped ones marked; text the AI may quote."""
        entries=[self.line_entry(brief) for brief in self.service.lines.lines()]
        if not entries:return {'lines':[],'text':msg('lines.list_none')}
        return {'lines':entries,'text':'\n'.join(el.entry_text(entry) for entry in entries)}

    def line_read(self, line_id):
        """``entrusted_line`` read: one line's brief and latest outcomes, for a related answer."""
        brief=self.service.lines.current(line_id) if isinstance(line_id,str) and line_id else None
        if brief is None:raise el.LineRefusal('line_not_found')
        return {**el.public_brief(brief),'ref':el.REF_PREFIX+brief['line_id'],
                'outcomes':self.line_outcomes(line_id,limit=el.READ_OUTCOMES),
                'text':el.brief_text(brief)+'\n'+el.outcomes_text(self.line_outcomes(line_id,limit=el.READ_OUTCOMES),
                                                               self.service.home_zone())}

    def line_touches_money(self, line_id):
        """Whether a run of this line declared or reached a payment, or called a connection the owner marked money-capable.

        Observed from the line's own runs (their recorded steps), never from the brief.
        """
        from . import trust_record
        service=self.service
        money={(hand.get('engine'),hand.get('identity')) for hand in trust_record.hands(service.store).values()
               if hand.get('money') and hand.get('state')=='active'}
        with service.store.db() as db:
            ids=[row['id'] for row in db.execute('SELECT id FROM preparations WHERE line_id=?',(line_id,))]
            if not ids:return False
            rows=db.execute("SELECT t.tool,t.detail,j.request_key FROM tool_events t JOIN jobs j ON j.id=t.job_id "
                            "WHERE j.request_key LIKE ? AND (t.tool IN ('payment_autonomy','connector_permission') "
                            "OR t.detail LIKE '%\"payment\"%')",(prep.REQUEST_KEY_PREFIX+'%',)).fetchall()
        for row in rows:
            if prep.preparation_of(row['request_key']) not in ids:continue
            if row['tool']=='payment_autonomy':return True
            try:detail=json.loads(row['detail'] or '{}')
            except ValueError:continue
            evidence=detail.get('evidence') if isinstance(detail.get('evidence'),dict) else {}
            if detail.get('declared_effect')=='payment' or evidence.get('effect')=='payment':return True
            if (evidence.get('engine'),evidence.get('identity')) in money:return True
        return False

    def line_owner_rules(self, brief):
        """The rules in force, each beside the owner's own words it came from (#798, money-touching lines)."""
        service=self.service
        lines=[]
        for number,rule in enumerate(brief['rules'],1):
            source=service.store.job(rule['from']) if rule.get('from') else None
            words=(service.scrub_work_text(source['id'],source.get('message'))
                   if source and not service.store.work_request_forgotten(source['id']) else '')
            lines.append(msg('lines.rule_with_words',number=number,rule=rule['text']+(' '+msg('lines.inferred') if rule.get('basis')=='inferred' else ''),
                             words=el.excerpt(words,el.WORDS_CHARS) or msg('label.not_recorded')))
        return '\n'.join(lines) or msg('lines.no_rules')

    def line_account_row(self, line_id):
        return next((row for row in self.line_rows(line_id) if row.get('origin')==prep.ORIGIN_LINE_ACCOUNT
                     and row['state'] in prep.ACTIVE_STATES),None)

    def ensure_line_account(self, job, brief, *, accepted_by=prep.ACCEPTED_OWNER_WORK, channel=None, bundle_channel=None):
        """The line's periodic account: one repeating ``prepare`` row of the line, delivered every time.

        It runs as any other row of the line (its brief and outcomes are its input), never pays
        (``ACCEPTED_OWNER_WORK`` is self-set, #717) and is cancelled with the line.
        """
        service=self.service
        stood=service.lines.provenance(brief['line_id']).get('by')=='look-back'
        rule=el.account_rule(brief['account_every_days'])
        now=service.preparations.clock()
        zone_name=service.home_zone() or 'UTC'
        with service.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            restart=self.line_start_restart(brief['line_id'],db)
            if stood and self.line_row_started(brief['line_id'],'account',restart,db):return None
            rows=[row for row in self.line_rows(brief['line_id'],db) if row.get('origin')==prep.ORIGIN_LINE_ACCOUNT]
            # A cancelled account also stays cancelled on a tick; only a new owner act may restart it.
            existing=any(row['state'] in prep.ACTIVE_STATES for row in rows) or (stood and not restart and bool(rows))
            result=None if existing else service.preparations.create(kind=prep.KIND_PREPARE,goal=LINE_ACCOUNT_GOAL,
                                           due_at=now+brief['account_every_days']*86400,timezone=zone_name,
                                           recurrence=prep.normalize_rrule(rule),
                                           channel=channel or service.preparation_channel(job,prep.KIND_PREPARE,'send'),
                                           bundle_channel=bundle_channel,
                                           created_from=job['id'],state=prep.STATE_SCHEDULED,
                                           accepted_by=accepted_by,origin=prep.ORIGIN_LINE_ACCOUNT,
                                           title=el.excerpt(msg('lines.account_title',task=brief['task']),prep.TITLE_MAX_CHARS),
                                           line_id=brief['line_id'],db=db)
            if stood:self.record_line_row_started(job['id'],brief['line_id'],'account',restart,db)
            return result

    def line_notice(self, job, kind, line_id, version):
        """Queue one owner notice of a line on ``job``'s conversation (``lines.NOTICE_*``).

        Returns '' when a surface took it, else the notice's text: this conversation has no
        surface that sends notices (the local web chat), so the reply says it instead.
        """
        service=self.service
        notice=f'{kind}{line_id}:{version}'
        service.queue_notification(job,notice,fingerprint=f'{line_id}:{version}')
        if service.store.notification_of(job['id'],notice):return ''
        return self.line_notice_text({'kind':notice,'fingerprint':f'{line_id}:{version}'}) or ''

    @staticmethod
    def told(next_step, untold):
        """The worker's next step, with the notice to say in the reply when Still could not send it."""
        return next_step if not untold else msg('lines.say_in_reply',text=untold)

    def stop_line(self, line_id, work_id=None):
        """Stop a line: its brief becomes stopped and every pending row of it is cancelled; ``(brief, changed)``.

        A run already going stops at its next step (``run_refusal``).  Nothing earlier is undone.
        """
        service=self.service
        with service.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            before,after=service.lines.write(line_id,work_id,None,'stopped',lambda brief:{**brief,'state':el.STATE_STOPPED},db=db)
            rows=self.line_rows(line_id,db)
            # Only replies still waiting are suppressed, including committed bundle Works.
            runs=[row['last_run_job_id'] for row in rows if row.get('last_run_job_id')]
            if runs:
                db.execute(f"UPDATE jobs SET delivery='cancelled' WHERE delivery='pending' AND id IN ({','.join('?'*len(runs))})",runs)
            service.lines.suppress_pending_bundles(line_id,db)
        for row in rows:
            if row['state'] in prep.ACTIVE_STATES:
                try:service.cancel_preparation(row['id'])
                except prep.PreparationRefusal:continue
        LOG.info('line stopped id=%s changed=%s',line_id,after['version']!=before['version'])
        return after,after['version']!=before['version']

    def stopped_line_run(self, job):
        """Whether ``job`` is a run of a line that was stopped (or whose brief is gone) since it started."""
        preparation_id=prep.preparation_of((job or {}).get('request_key'))
        if not preparation_id:return False
        with self.service.store.db() as db:
            row=db.execute('SELECT line_id FROM preparations WHERE id=?',(preparation_id,)).fetchone()
            return bool(row and prep.line_refusal(db,row['line_id']))

    def undo_line_version(self, line_id, version, work_id=None):
        """Put back the brief as it was before ``version``, only while ``version`` is still current."""
        service=self.service
        earlier=service.lines.version(line_id,int(version)-1) if int(version)>1 else None
        if earlier is None:raise el.LineRefusal('line_nothing_to_undo')
        def restore(brief):
            restored={key:earlier[key] for key in el.FIELDS}
            # Undo reverts a rule change; a stop since then stays the owner's.
            restored['state']=brief['state']
            return restored
        before,after=service.lines.write(line_id,work_id,int(version),'undo',restore)
        if after['account_every_days']!=before['account_every_days']:self.retime_line_account(before,after)
        return after

    def retime_line_account(self, before, after):
        """#802: a changed account interval changes the pending account too, not only its rule.

        The pending account moves to the start of its current period (when the account row was set
        up, or its last run's slot) plus the new interval; one already past that point is due a
        minute from now.  The start is read from the record, never from the due time, so a clamped
        due time does not shift later changes (Codex review of #803).
        """
        service=self.service
        row=self.line_account_row(after['line_id'])
        if not row or row['state']==prep.STATE_RUNNING:return
        now=service.preparations.clock()
        due=max(self.account_period_start(row)+after['account_every_days']*86400,now+60)
        service.preparations.edit(row['id'],rrule=el.account_rule(after['account_every_days']),
                                  due=datetime.fromtimestamp(due,prep.zone(row['timezone'])).isoformat())

    def account_period_start(self, row):
        """When the current period of a line's account began: its last run's slot, else when the row was set up."""
        start=row['created_at']
        prefix=prep.REQUEST_KEY_PREFIX+row['id']+':'
        with self.service.store.db() as db:
            keys=[item['request_key'] for item in db.execute('SELECT request_key FROM jobs WHERE request_key LIKE ?',(prefix+'%',))]
        # A run's request key names its slot in whole seconds (``request_key``); a continuation keeps it.
        for key in keys:
            slot=prep.slot_key(key)[len(prefix):]
            if slot.isdigit():start=max(start,float(slot))
        return start

    def line_handler(self, job):
        """The ``entrusted_line`` handler bound to one Work (#798).

        ``list`` and ``read`` answer from the store.  ``open``, ``revise``, ``undo``, ``stop`` and
        ``restart`` write a brief version and are refused unless the owner's own typed message
        started this Work: a scheduled run, a continuation or a replayed turn cannot add a rule,
        widen a limit or resume a stopped line.  A rule change on a line that touched a
        money-marked connection is made only when the owner's message asks for exactly it.
        """
        service=self.service
        def refused(code,detail=''):
            return not_run(ToolError(msg('lines.refused_'+code,detail=detail) if detail else msg('lines.refused_'+code),code))
        def handle(args):
            action=args.get('action')
            try:
                if action=='list':return self.lines_listing()
                if action=='read':
                    result=self.line_read(args.get('line'))
                    self._record_line_read(job['id'],result)
                    return result
                if action not in ('open','revise','undo','stop','restart'):raise refused('line_invalid_action')
                if service.unattended_work(job) or not service.owner_typed_work(job):raise refused('unattended_run')
                return getattr(self,'_line_'+action)(job,args)
            except el.LineConflict as exc:
                raise refused('line_version_conflict',str(exc.current_version)) from None
            except el.LineRefusal as exc:
                raise refused(exc.code) from None
        return handle

    def _line_open(self, job, args):
        service=self.service
        rules=el.split_rules(args.get('rules'))
        days=el.whole_days(args.get('account_every_days'))
        brief=service.lines.open(job['id'],task=service._redact_known_secrets(args.get('task') or ''),
                                 report_promise=service._redact_known_secrets(args.get('report_promise') or ''),
                                 rules=[service._redact_known_secrets(rule) for rule in rules],
                                 open_matters=service._redact_known_secrets(args.get('open_matters') or ''),
                                 account_every_days=el.ACCOUNT_DAYS if days is None else days)
        try:self.ensure_line_account(job,brief)
        except prep.PreparationRefusal as exc:LOG.warning('line account not scheduled id=%s code=%s',brief['line_id'],exc.code)
        untold=self.line_notice(job,el.NOTICE_OPENED,brief['line_id'],brief['version'])
        LOG.info('line opened id=%s work=%s',brief['line_id'],job['id'])
        return {**el.public_brief(brief),'next_step':msg('lines.opened_next',line=brief['line_id'])
                if not untold else msg('lines.opened_next_untold',line=brief['line_id'],text=untold)}

    def _line_revise(self, job, args):
        service=self.service
        line_id=args.get('line')
        if args.get('expected_version') in (None,''):raise el.LineRefusal('line_version_required')
        expected=el.whole_days(args.get('expected_version'))
        if expected is None:raise el.LineRefusal('line_version_required')
        rule=' '.join(service._redact_known_secrets(args.get('rule') or '').split())
        drop=el.whole_days(args.get('drop_rule'))
        changes={key:service._redact_known_secrets(args[key]) for key in ('task','report_promise','open_matters')
                 if isinstance(args.get(key),str) and args[key].strip()}
        days=el.whole_days(args.get('account_every_days'))
        current=service.lines.current(line_id) if isinstance(line_id,str) and line_id else None
        if current is None:raise el.LineRefusal('line_not_found')
        if current['state']!=el.STATE_ACTIVE:raise el.LineRefusal('line_stopped')
        if args.get('drop_rule') not in (None,'') and not (drop and 1<=drop<=len(current['rules'])):
            raise el.LineRefusal('line_invalid_rules')
        if not (rule or drop or changes or days is not None):raise el.LineRefusal('line_nothing_to_change')
        ruled=bool(rule or drop or set(changes)-{'open_matters'} or days is not None)
        money=ruled and self.line_touches_money(line_id)
        if money:
            change=el.change_text(current,rule=rule,drop=drop,changes=changes,days=days)
            judged=service.decision_judge.explicit_line_change_request(
                job['message'],change,self.line_owner_rules(current),service.preparation_conversation(job),work_id=job['id'])
            if judged.outcome!=JUDGMENT_YES:raise el.LineRefusal('line_change_needs_owner')
        now=service.preparations.clock()
        def update(brief):
            rules=[item for number,item in enumerate(brief['rules'],1) if number!=drop]
            if rule:rules.append({'text':rule,'from':job['id'],'at':now})
            return {**brief,**changes,'rules':rules,
                    **({'account_every_days':days} if days is not None else {})}
        before,after=service.lines.write(line_id,job['id'],expected,'rule' if rule else 'revised',update)
        if days is not None and days!=before['account_every_days']:self.retime_line_account(before,after)
        restated=bool(rule) and after['version']!=before['version']
        untold=self.line_notice(job,el.NOTICE_RULE,line_id,after['version']) if restated else ''
        LOG.info('line revised id=%s version=%s work=%s rule=%s',line_id,after['version'],job['id'],restated)
        return {**el.public_brief(after),'restated':restated,'money_rules':money,
                'next_step':self.told(msg('lines.revised_next' if restated else 'lines.revised_quiet_next'),untold)}

    def _line_undo(self, job, args):
        version=el.whole_days(args.get('version'))
        if version is None:raise el.LineRefusal('line_version_required')
        after=self.undo_line_version(args.get('line'),version,job['id'])
        return {**el.public_brief(after),'next_step':msg('lines.undone_next')}

    def _line_stop(self, job, args):
        line_id=args.get('line')
        if not isinstance(line_id,str) or self.service.lines.current(line_id) is None:raise el.LineRefusal('line_not_found')
        after,changed=self.stop_line(line_id,job['id'])
        untold=self.line_notice(job,el.NOTICE_STOPPED,line_id,after['version']) if changed else ''
        return {**el.public_brief(after),'changed':changed,'next_step':self.told(msg('lines.stopped_next'),untold)}

    def _line_restart(self, job, args):
        service=self.service
        line_id=args.get('line')
        current=service.lines.current(line_id) if isinstance(line_id,str) and line_id else None
        if current is None:raise el.LineRefusal('line_not_found')
        _before,after=service.lines.write(line_id,job['id'],None,'restarted',lambda brief:{**brief,'state':el.STATE_ACTIVE})
        if service.lines.provenance(line_id).get('by')=='look-back':
            # The owner's act is an announcement even when the already-active brief did not change.
            with service.store.db() as db:
                db.execute('BEGIN IMMEDIATE')
                if not db.execute("SELECT 1 FROM tool_events WHERE job_id=? AND tool='line_restarted' "
                                  "AND json_extract(detail,'$.line_id')=?",(job['id'],line_id)).fetchone():
                    db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                               (job['id'],'line_restarted','succeeded',json.dumps({'line_id':line_id,'version':after['version']}),
                                service.preparations.clock()))
        try:
            if service.lines.provenance(line_id).get('by')=='look-back':
                self.start_announced_lines(service.preparations.clock(),line_id=line_id)
                if any(row.get('origin')!=prep.ORIGIN_LINE_ACCOUNT for row in self.line_rows(line_id)):
                    self.ensure_line_account(job,after,accepted_by=prep.ACCEPTED_DREAM)
            else:self.ensure_line_account(job,after)
        except prep.PreparationRefusal as exc:LOG.warning('line account not scheduled id=%s code=%s',line_id,exc.code)
        return {**el.public_brief(after),'next_step':msg('lines.restarted_next',line=line_id)}

    def line_run_input(self, job, row):
        """What a run of a line reads with its request: the current brief and the line's earlier outcomes.

        The version read is recorded once per Work (Evidence), so a later change can reach the run's
        next step (``line_note``) and a reply can be checked against the rules it was given.
        """
        service=self.service
        brief=service.lines.current(row['line_id'])
        if brief is None:raise el.LineRefusal('line_unreadable')
        self._record_line_read(job['id'],brief)
        outcomes=self.line_outcomes(row['line_id'],exclude=job['id'])
        if row.get('origin')==prep.ORIGIN_LINE_ACCOUNT:
            lead=msg('lines.account_input')
        else:
            lead=msg('lines.run_input')
        return lead+'\n'+el.brief_text(brief)+'\n'+el.outcomes_text(outcomes,row.get('timezone'))

    def record_line_judgment_reads(self, work_id, text):
        """Record only briefs retained in the bounded block, after its call went out."""
        for brief in json.loads(text or '{}').get('lines',[]):
            self._record_line_read(work_id,brief,once=True)

    def record_plan_line_reads(self, work_id, rows, request):
        """A prepared history is not a read until a worker receives that exact block."""
        try:
            for row in rows:
                text=row.get('line_judgment_input')
                if text and self.service._redact_known_secrets(text) in request:
                    self.record_line_judgment_reads(work_id,text)
        except Exception as exc:  # noqa: BLE001 - the audit record must not fail or mask the run's own outcome
            LOG.warning('line read record failed work=%s kind=%s',work_id,type(exc).__name__)

    def _record_line_read(self, work_id, brief, *, once=False):
        with self.service.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if once and db.execute(
                    "SELECT 1 FROM tool_events WHERE job_id=? AND tool=? AND "
                    "COALESCE(json_extract(detail,'$.evidence.line_id'),json_extract(detail,'$.line_id'))=? LIMIT 1",
                    (work_id,el.READ_EVENT,brief['line_id'])).fetchone():return
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       # #802: as Evidence, so the information-use audit lists the line the run was given.
                       (work_id,el.READ_EVENT,'succeeded',json.dumps({'evidence':{'line_id':brief['line_id'],'version':brief['version']}}),
                        self.service.preparations.clock()))

    def line_version_read(self, work_id):
        """The newest brief version this Work recorded reading, as ``(line_id, version)``, or None."""
        with self.service.store.db() as db:
            row=db.execute('SELECT detail FROM tool_events WHERE job_id=? AND tool=? ORDER BY id DESC LIMIT 1',
                           (work_id,el.READ_EVENT)).fetchone()
        if row is None:return None
        detail=json.loads(row['detail'])
        # A read recorded before #802 kept its fields at the top level.
        read=detail.get('evidence') if isinstance(detail.get('evidence'),dict) else detail
        return read.get('line_id'),read.get('version')

    def line_note(self, job):
        """A changed brief for a running line's next step, once per change, or None.

        A stop needs no note: the run's own stop check ends it (``run_refusal``).
        """
        service=self.service
        try:
            read=self.line_version_read(job['id'])
            if not read:return None
            brief=service.lines.current(read[0])
            if brief is None or brief['state']!=el.STATE_ACTIVE or brief['version']==read[1]:return None
            self._record_line_read(job['id'],brief)
            return msg('lines.changed_during_run')+'\n'+el.brief_text(brief)
        except Exception:
            LOG.info('line note unavailable job=%s',job.get('id'))
            return None

    def lines_index(self, job):
        """The bounded pointer to the owner's active lines for a Work the owner started, or ''.

        Only each line's id and task sentence: whether one bears on the request is the AI's
        judgment, and it reads the brief and outcomes with ``entrusted_line`` read.
        """
        service=self.service
        if service.unattended_work(job):return ''
        try:
            active=[brief for brief in service.lines.lines() if brief['state']==el.STATE_ACTIVE and not self.waiting_line(brief)][:el.INDEX_ITEMS]
        except Exception:
            LOG.info('lines index unavailable job=%s',job.get('id'))
            return ''
        if not active:return ''
        return el.INDEX_HEADING+'\n'+'\n'.join(f"- {brief['line_id']}: {el.excerpt(brief['task'],el.INDEX_TASK_CHARS)}"
                                              for brief in active)

    def line_notice_text(self, notification):
        """The text of one queued line notice, or None when it no longer applies."""
        service=self.service
        kind=notification['kind']
        line_id,_sep,version=str(notification.get('fingerprint') or '').partition(':')
        brief=service.lines.readable(line_id)
        if brief is None or not version.isdigit():return None
        if kind.startswith(el.NOTICE_OPENED):
            if brief['state']!=el.STATE_ACTIVE:return None
            provenance=service.lines.provenance(line_id)
            if provenance.get('by')=='look-back':
                return service._redact_known_secrets(msg('lines.stood_announced',task=brief['task'],promise=brief['report_promise'],
                                                        days=provenance['evidence']['days']))
            return service._redact_known_secrets(msg('lines.announced',task=brief['task'],promise=brief['report_promise']))
        if kind.startswith(el.NOTICE_RULE):
            # Told while its rule is still in force (a later change or an undo may have removed it).
            stored=service.lines.version(line_id,int(version))
            rule=stored['rules'][-1]['text'] if stored and stored['rules'] else None
            if rule is None or rule not in [item['text'] for item in brief['rules']]:return None
            text=msg('lines.restated',rule=rule)
            if self.line_touches_money(line_id):
                text+='\n\n'+msg('lines.money_rules')+'\n'+self.line_owner_rules(stored)
            return service._redact_known_secrets(str(text or ''))
        if kind.startswith(el.NOTICE_STOPPED):
            return service._redact_known_secrets(msg('lines.stopped',task=brief['task']))
        return None
