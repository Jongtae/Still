"""Preparations coordination extracted from AgentService (#21).

AgentService remains the composition root and owns all mutable service state.
The collaborator uses the service on every call, including calls between moved
methods, preserving service forwarding entry points and patch targets. Scheduling,
approvals, budgets, recovery, secrets and database transactions are unchanged.
The existing preparations.Preparations store retains persistence ownership.
"""

import json
import uuid
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone

from . import entrusted_lines as el
from . import condition_checks as checks
from . import continuous_lines as continuous
from . import preparations as prep
from . import owner_model as om
from . import work_status as ws
from .agent_runtime import WORK_STOP_KEEP, WORK_STOP_KEY, ToolError, WorkLedger, not_run, ran_field
from .api_credentials import CredentialError
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
        try:
            now=service.preparations.clock()
            rows=service.preparations.fresh(now,exclude_job=job.get('id'))
            text=prep.render_prepared(rows,now,service._redact_known_secrets) if rows else None
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
        has_check=args.get('check') not in (None,'')
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
        check_args=None
        if has_check:
            if (kind!=prep.KIND_PREPARE or recurrence is not None or
                    any(args.get(key) not in (None,'') for key in ('every_minutes','report_every_minutes'))):
                raise prep.PreparationRefusal('invalid_check')
            check_args,interval=checks.parse_for_store(service.store,args['check'])
            until_at=prep.parse_due(args.get('until'),timezone,timezone,now)[0]
            if not due_at<until_at<=due_at+prep.MAX_WINDOW_SECONDS:
                raise prep.PreparationRefusal('invalid_check')
            budget_zone=service.preparations._owner_zone()
            max_wakes=checks.DAILY_WAKES*((datetime.fromtimestamp(until_at,budget_zone).date()-
                                         datetime.fromtimestamp(due_at,budget_zone).date()).days+1)
            runs=max_wakes if args.get('max_runs') in (None,'') else prep._whole_number(args['max_runs'])
            if runs is None or not 1<=runs<=max_wakes:raise prep.PreparationRefusal('invalid_max_runs')
            window=interval,until_at,runs
        elif has_window:
            window=prep.normalize_window(args.get('every_minutes'),args.get('until'),args.get('max_runs'),
                                         due_at,timezone,now,recurrence=recurrence)
            requested_until=prep.parse_due(args.get('until'),timezone,timezone,now)[0]
        delivery=args.get('delivery')
        if kind==prep.KIND_PREPARE and window and not has_check and delivery=='send' and args.get('every_result') is not True:
            delivery=prep.DELIVERY_WHEN_NEEDED
        when_needed=delivery==prep.DELIVERY_WHEN_NEEDED
        if when_needed and kind!=prep.KIND_PREPARE:raise prep.PreparationRefusal('invalid_delivery')
        delivery_mode=prep.DELIVERY_WHEN_NEEDED if when_needed else None
        report_every_minutes=args.get('report_every_minutes')
        # Send already delivers each check, so an equal report interval adds nothing.
        if delivery=='send' and window and prep._whole_number(report_every_minutes)==window[0]//60:
            report_every_minutes=None
        terms=prep.window_terms(window,recurrence,due_at,timezone,report_every_minutes,delivery_mode,requested_until)
        return {'kind':kind,'goal':goal,'title':title,'offer':offer,'due_at':due_at,'timezone':timezone,
                'recurrence':recurrence,'window':window,'delivery_mode':delivery_mode,'window_terms':terms,
                'report_every_minutes':report_every_minutes,'requested_until':requested_until,'check_args':check_args}

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

        #968: a scheduled run the owner steered (``owner_steer_words``) may replace its own
        preparation (``replaces``, the owner-typed correction path), judged against those words.
        #984: without replaces it may start new work, with that steer recorded as its acceptance.
        """
        service=self.service
        def schedule(args):
            if args.get('line'):args={**args,'line':el.line_id_of(args['line'])}
            # #780: the daily plan's own run sets up reading-and-reporting work; what it sets up never
            # pays (``SELF_SET``) and cannot schedule in turn, so one misled plan run stays bounded.
            planning=self.day_plan_run(job) and self.day_plan_on()
            resume_offer=args.get('resume_line') is True
            # #961: every prepare is a card with the AI's one-line summary; refused before anything is scheduled.
            if args.get('kind')==prep.KIND_PREPARE and not ' '.join(str(args.get('offer') or '').split()):
                raise not_run(ToolError(str(prep.PreparationRefusal('offer_required')),'offer_required'))
            unattended=service.unattended_work(job)
            if resume_offer and not planning:
                if unattended:
                    raise not_run(ToolError(UNATTENDED_APPROVAL_TEXT,'unattended_run'))
                raise not_run(ToolError(msg('preparations.resume_line_day_plan_only'),'resume_line_day_plan_only'))
            steers=service.owner_steer_words(job)
            steered=bool(unattended and not planning and steers)
            planner=None
            if (unattended and not planning and not steered and args.get('kind')==prep.KIND_PREPARE
                    and args.get('check') not in (None,'')):
                running=self.own_running_row(job)
                if (running and running.get('origin')==prep.ORIGIN_LINE_PLANNER
                        and running.get('line_id') and args.get('line')==running['line_id']):
                    planner=running
            if unattended and not planning and planner is None:
                # #938: the run of a qualifying line row schedules its line's next piece, and only that.
                # #968/#984: a steered run's own replacement, or work that is not piece-shaped, takes the
                # owner-present path below instead.
                parent=self.piece_parent(job)
                if steered and parent is not None and not self.piece_shaped(args,parent['line_id']):parent=None
                if parent is not None:return self.schedule_piece(job,parent,args)
                if not steered:
                    # #619: a scheduled run leaves no proposal waiting; it says what it would schedule.
                    raise not_run(ToolError(UNATTENDED_APPROVAL_TEXT,'unattended_run'))  # #618: Still refused it, ran: false
            if steered and (not self.own_running_row(job) or args.get('replaces') not in
                            (None, '', prep.preparation_of(job.get('request_key')))):
                # A steer may start work or replace this row; it never replaces another row.
                raise not_run(ToolError(msg('preparations.steered_replace_only'),'unattended_run'))
            now=service.preparations.clock()
            try:
                fields=service.preparation_fields(args,now,lambda goal:service.repeats_refused_watch(job['id'],goal))
                if resume_offer and not self.piece_shaped(args,args.get('line')):
                    raise not_run(prep.PreparationRefusal('invalid_line_resume_offer'))
                spec=(checks.prove(service.store,job['id'],fields['check_args'],fields['window'][0])
                      if fields['check_args'] is not None else None)
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
            channel=service.preparation_channel(self.owner_last_work() if planning else job,kind,delivery_mode or args.get('delivery'))
            if resume_offer:
                return self.offer_line_resume(job,args.get('line'),fields,channel)
            summary=prep.proposal_summary({'kind':kind,'goal_text':goal,'due_at':due_at,'timezone':timezone,
                                           'recurrence':prep.RECURRENCE_WINDOW if window else recurrence,
                                           'every_seconds':window and window[0],'until_at':window and window[1],
                                           'max_runs':window and window[2],'delivery_mode':delivery_mode,'window_terms':fields['window_terms'],
                                           'channel':channel,'title_text':title,'offer_text':offer,
                                           'check_spec':json.dumps(spec) if spec else None},service.home_zone())
            # #717: the owner's own typed Work (the same test as a direct settings request, #619);
            # #774: a location continuation re-runs an earlier message, so it is not one.
            owner_work=service.owner_typed_work(job) or steered
            # #1234: the owner's correction of work they accepted is judged as that change, over the
            # accepted work and every changed place, not as a new schedule the owner never asked for.
            replaced=(service.preparations.get(args['replaces']) if owner_work and not planning and planner is None
                      and isinstance(args.get('replaces'),str) else None)
            judged=summary
            if replaced and replaced['state'] in prep.ACTIVE_STATES and replaced.get('accepted_by') in prep.OWNER_ACCEPTED:
                judged=('Change scheduled work the owner already accepted.\nAccepted work: '
                         +prep.proposal_summary(replaced,service.home_zone())+'\nReplacement: '+summary
                         +'\nIts instruction changed at these places:\n'
                         +(prep.hunks_text(prep.changed_hunks(replaced.get('goal_text'),goal)) or 'none'))
            # #717: the judgment no longer decides whether it is scheduled, only who asked: a run the
            # owner asked for keeps what the owner's payment autonomy allows (``self_set_run``).
            asked=bool(owner_work and service.decision_judge.explicit_preparation_request(
                service.owner_request_words(job),judged,service.preparation_conversation(job)).outcome==JUDGMENT_YES)
            if steered and service.store.outside_content_sources(job['id']) and not (asked and self.steer_asked(
                    job,('replace this scheduled work with: ' if args.get('replaces') else 'start new scheduled work: ')+summary
                    +'\nits whole instruction changed at these places:\n'
                    +prep.hunks_text(prep.changed_hunks((self.own_running_row(job) or {}).get('goal_text') if args.get('replaces') else '',goal)))):
                # #968, C17 case 3: after outside content, only what the owner's own words asked for, judged
                # over the whole replacement (every changed place of its instruction), not a display preview.
                raise not_run(ToolError(msg('preparations.steer_not_asked'),'unattended_run'))
            accepted_from=steers[-1][0] if steered and not args.get('replaces') else None
            # A planner-created executor gets its standing from the active mandate at run time.
            accepted_by=(prep.ACCEPTED_OWNER_WORK if planner else
                         prep.ACCEPTED_OWNER_STEER if accepted_from else
                         prep.ACCEPTED_DAY_PLAN if planning else
                         prep.ACCEPTED_OWNER_REQUEST if asked else
                         prep.ACCEPTED_OWNER_WORK if owner_work else None)
            accepted=accepted_by is not None
            # #766: the preparation this one takes the place of (a changed target or plan); it stops
            # once this one is scheduled. A proposal never stops what is running. The line planner's
            # replacement instead checks the automatic pause and mandate in the scheduling transaction.
            replaces=args.get('replaces')
            current=None
            if planner is None and replaces not in (None,''):
                current=service.preparations.get(replaces) if isinstance(replaces,str) else None
                if not current or current['state'] not in prep.ACTIVE_STATES:
                    raise not_run(ToolError(str(prep.PreparationRefusal('replace_not_found')),'replace_not_found'))
                # #780: a plan run adds work; it never stops what the owner or an earlier run set up.
                if not accepted or planning:
                    raise not_run(ToolError(str(prep.PreparationRefusal('replace_not_allowed')),'replace_not_allowed'))
            # #798: the entrusted line this row works for: the replaced row's line, kept through the
            # replacement, or the line the owner's AI continues when the owner starts the same work again.
            line_arg=args.get('line')
            if line_arg=='none':line_arg=None
            elif line_arg in (None,'') and current is None and accepted and not planning and service.owner_typed_work(job):
                # #1169: a Work holding a line it read must say whether this schedule continues it.
                held=self.held_line(job['id'])
                if held:raise not_run(ToolError(msg('lines.refused_line_unnamed',line=held),'line_unnamed'))
            line_id=self.scheduled_line(line_arg,current,accepted=accepted,planning=planning)
            explicitly_linked=False
            if line_id and owner_work and not planning:
                with service.store.db() as db:
                    active_line=continuous.active(db,line_id,now)
                if active_line and (asked or service.store.outside_content_sources(job['id'])):
                    brief=service.lines.current(line_id)
                    if brief:
                        preview=f"Schedule on the entrusted line {brief['task']!r}: {summary}"
                        explicitly_linked=service.decision_judge.explicit_preparation_request(
                            service.owner_request_words(job),preview,service.preparation_conversation(job)).outcome==JUDGMENT_YES
            stopped=None
            try:
                # #582: a goal the owner declined is not set up again unless they ask for it again.
                if not asked and service.declined_before(job['id'],goal,now):
                    raise not_run(prep.PreparationRefusal('declined_before'))
                with (service.store.db() if current or line_id else nullcontext()) as db:
                    if db is not None:
                        db.execute('BEGIN IMMEDIATE')
                        service.store.require_read_checkpoint_writer(db)
                        if planner is not None:
                            now=service.preparations.clock()
                        mandate=continuous.active(db,line_id,now)
                        if planner is not None:
                            self.require_planner_executor(db,job['id'],planner,mandate,window[1],now,replaces=replaces)
                        elif mandate and (planning or (not explicitly_linked
                                and continuous.member_revision(db,job['id'],line_id)!=mandate['revision'])):
                            raise not_run(prep.PreparationRefusal('line_standing_link'))
                        if replaces and planner is None:
                            target=db.execute('SELECT state FROM preparations WHERE id=?',(replaces,)).fetchone()
                            if not target or target['state'] not in prep.ACTIVE_STATES:
                                raise not_run(prep.PreparationRefusal('replace_not_found'))
                            if mandate:
                                if not asked:
                                    raise not_run(prep.PreparationRefusal('replace_not_allowed'))
                                continuous.fence(db,line_id,now,work_id=job['id'])
                        if line_id and prep.line_refusal(db,line_id):
                            raise not_run(prep.PreparationRefusal('line_inactive'))
                        if mandate and explicitly_linked and service.owner_typed_work(job) and not replaces:
                            continuous.record_membership(db,job['id'],line_id,mandate['revision'],refresh=True)
                    row=service.preparations.create(kind=kind,goal=goal,due_at=due_at,timezone=timezone,recurrence=recurrence,
                                                 channel=channel,created_from=job['id'],
                                                 state=prep.STATE_SCHEDULED if accepted else prep.STATE_PROPOSED,
                                                 accepted_by=accepted_by,accepted_from=accepted_from,
                                                 window=window,delivery_mode=delivery_mode,title=title,offer=offer,
                                                 report_every_minutes=fields['report_every_minutes'],line_id=line_id,
                                                 requested_until=fields['requested_until'],check=spec,db=db,replaces=replaces,
                                                 deduplicate=planner is None)
                    # #574: another Work's identical proposal is this one; the owner's own Work accepts it.
                    already=bool(row.get('already_proposed'))
                    if already and accepted:
                        row=service.preparations.accept(row['id'],accepted_by,accepted_from=accepted_from,db=db,replaces=replaces)
                    if replaces and row['state']==prep.STATE_SCHEDULED and replaces!=row['id']:
                        stopped=service.preparations.cancel(replaces,db=db)
                    if line_id and spec and row['state']==prep.STATE_SCHEDULED and continuous.active(db,line_id,now):
                        continuous.trigger(db,line_id,{'kind':'check_ready','check':spec['digest'],'at':now,'output':''})
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
                    'accepted_by':row['accepted_by'],'accepted_from':row.get('accepted_from'),'next_step':next_step}
            if row.get('line_id'):
                result['line_id']=row['line_id']
                if scheduled and service.owner_typed_work(job):
                    brief=service.lines.readable(row['line_id'])
                    note=self.line_schedule_note(brief,row)
                    if note:result['line_schedule_note']=note
            if spec:
                result['check']={key:spec['arguments'][key] for key in ('hosts','secrets')}
                result['check'].update(digest=spec['digest'],every_seconds=spec['every_seconds'])
                result['next_step']+=' '+msg('checks.scheduled')
            if stopped:
                self.withdraw_cancelled_location(stopped)
                LOG.info('preparation replaced id=%s by=%s work=%s changed=%s',replaces,row['id'],job['id'],stopped['changed'])
                result['replaced']={'preparation_id':replaces,'state':stopped['state']}
                result['next_step']+=f' 이 준비가 대신하는 {replaces}는 더 실행되지 않습니다. 답에서 무엇을 멈추고 무엇으로 바꿨는지 한 줄로 말하세요.'
            if window:
                result.update(every_minutes=row['every_seconds']//60,until=prep.iso(row['until_at'],row['timezone']),
                              max_runs=row['max_runs'],
                              # #404: the model sees when its last check is (#701: at most until).
                              last_check=prep.iso(row['until_at'] if spec else prep.last_slot(row),row['timezone']))
                if spec:result.pop('every_minutes')
                terms=prep.stored_window_terms(row)
                if terms.get('requested_until') is not None:
                    result.update(requested_until=prep.iso(terms['requested_until'],row['timezone']),
                                  covering_every_minutes=terms['covering_every_minutes'])
                if terms.get('recurrence'):result['window_recurrence']=terms['recurrence']
                if terms.get('report_seconds'):result['report_every_minutes']=terms['report_seconds']//60
            return result
        return schedule

    def require_planner_executor(self, db, work_id, planner, mandate, until_at, now, *, replaces):
        """Check planner authority in the same transaction that creates the executor (#1218)."""
        service=self.service
        job=db.execute('SELECT request_key,status FROM jobs WHERE id=?',(work_id,)).fetchone()
        line_id=planner['line_id']
        if (not mandate or mandate['planner_id']!=planner['id'] or not job or job['status']!='running'
                or prep.preparation_of(job['request_key'])!=planner['id']
                or prep.Preparations.run_refusal(db,work_id,now)
                or continuous.member_revision(db,work_id,line_id)!=mandate['revision']
                or service.stop.is_set() or service._stopped_in(db,work_id)):
            raise not_run(ToolError(UNATTENDED_APPROVAL_TEXT,'unattended_run'))
        if not now<until_at<=mandate['ends_at']:
            raise not_run(ToolError('The executor window must end by the mandate end time.','line_invalid_end'))
        if replaces not in (None,''):
            target=db.execute('SELECT updated_at,check_status FROM preparations WHERE id=? AND line_id=? '
                              'AND state=? AND check_spec IS NOT NULL',
                              (replaces,line_id,prep.STATE_PAUSED)).fetchone() if isinstance(replaces,str) else None
            status=json.loads(target['check_status'] or '{}') if target else {}
            # Older automatic pauses used the check time. An explicit pause advances updated_at.
            if (not target or status.get('paused')!='failures'
                    or target['updated_at']!=status.get('paused_at',status.get('last_run_at'))):
                raise not_run(ToolError(UNATTENDED_APPROVAL_TEXT,'unattended_run'))
        slot=prep.slot_key(job['request_key'])
        if db.execute('SELECT 1 FROM preparations p JOIN jobs j ON j.id=p.created_from '
                      'WHERE p.line_id=? AND p.check_spec IS NOT NULL '
                      'AND (j.request_key=? OR instr(j.request_key,?)=1) LIMIT 1',
                      (line_id,slot,slot+prep.CONTINUATION_SEPARATOR)).fetchone():
            raise not_run(prep.PreparationRefusal('line_executor_exists'))

    def offer_line_resume(self, job, line_id, fields, channel):
        """#1027: make an offer through #659 without scheduling or changing the line."""
        service=self.service
        try:
            with service.store.db() as db:
                db.execute('BEGIN IMMEDIATE')
                service.store.require_read_checkpoint_writer(db)
                if prep.Preparations.run_refusal(db,job['id'],service.preparations.clock()):
                    raise not_run(ToolError(UNATTENDED_APPROVAL_TEXT,'unattended_run'))
                row=service.preparations.create(kind=fields['kind'],goal=fields['goal'],due_at=fields['due_at'],
                                                timezone=fields['timezone'],recurrence=None,channel=channel,
                                                created_from=job['id'],state=prep.STATE_PROPOSED,
                                                delivery_mode=fields['delivery_mode'],title=fields['title'],offer=fields['offer'],
                                                origin=prep.ORIGIN_LINE_RESUME,line_id=line_id,db=db)
        except prep.PreparationRefusal as exc:
            raise ToolError(str(exc),exc.code,**ran_field(exc)) from None
        return {'preparation_id':row['id'],'kind':row['kind'],'state':row['state'],'scheduled':False,
                'requires_owner_acceptance':True,'accepted_by':None,'line_id':line_id,
                'due':prep.iso(row['due_at'],row['timezone']),'delivery':service.preparation_delivery(row),
                'next_step':msg('preparations.line_resume_proposed')}

    def piece_parent(self, job):
        """#938 (D1): the row ``job`` is the current run of, when that run may schedule its line's next piece.

        A one-off or repeating ``prepare`` row of a line the owner set up (not a look-back line), that the
        owner accepted, that an owner-typed Work set up, or that is itself a piece; never the look back's,
        the daily plan's or a periodic account's row.  Only the slot's current Work counts (a continuation
        keeps the slot, ``continue_run``).  None for every other unattended run, which stays refused.
        """
        service=self.service
        preparation_id=prep.preparation_of((job or {}).get('request_key'))
        row=service.preparations.get(preparation_id) if preparation_id else None
        if not (row and row.get('line_id') and row['kind']==prep.KIND_PREPARE
                and row.get('origin') in (None,prep.ORIGIN_LINE_PIECE,prep.ORIGIN_LINE_RESUME)
                and row.get('accepted_by') in prep.LINE_PIECE_PARENTS
                and row['state']==prep.STATE_RUNNING and row.get('last_run_job_id')==job.get('id')):
            return None
        if service.lines.provenance(row['line_id']).get('by')=='look-back':return None
        return row

    @staticmethod
    def piece_shaped(args, line_id=None):
        """#938: whether a ``schedule_preparation`` call is one plain ``prepare`` of the run's own line."""
        return not (args.get('kind')!=prep.KIND_PREPARE or args.get('line') not in (None,'',line_id)
                    or any(args.get(key) not in (None,'') for key in
                           ('recurrence','rrule','every_minutes','until','max_runs','report_every_minutes','replaces','check')))

    def schedule_piece(self, job, parent, args):
        """#938: schedule the next piece of ``parent``'s line from its run ``job`` (D1-D6).

        The piece is one ``prepare`` of the same line, with no recurrence, window or ``replaces``.  Its
        goal is the handoff the run writes.  Who stands behind it, where it goes and how it is told are
        inherited from ``parent`` and never read from the call: ``prep.piece_standing`` (payment follows
        the chain's owner-accepted root; owner decision 2026-11-18), the parent's channel and delivery
        mode.  The parent run, line and bounds are re-checked in the same transaction that keeps one
        child per run.  No proposal, no message, no declined-goal judgment: the owner's line and its stop
        control stand for it.
        """
        service=self.service
        def refused(code):
            return not_run(ToolError(str(prep.PreparationRefusal(code)),code))
        if not self.piece_shaped(args,parent['line_id']):
            raise refused('line_piece_invalid')
        now=service.preparations.clock()
        try:
            fields=service.preparation_fields(args,now)
        except prep.PreparationRefusal as exc:
            raise not_run(ToolError(service._redact_known_secrets(str(exc))[:600],exc.code)) from None
        accepted_by=prep.piece_standing(parent['accepted_by'])
        if accepted_by is None:raise refused('line_piece_invalid')
        try:
            ledger = getattr(service.current_decision_budget(),'ledger',None)
            with service.store.db() as db:
                db.execute('BEGIN IMMEDIATE')
                service.store.require_read_checkpoint_writer(db)
                current = db.execute('SELECT status FROM jobs WHERE id=?',(job['id'],)).fetchone()
                refusal=prep.Preparations.run_refusal(db,job['id'],now)
                if refusal in (prep.LINE_STOPPED,prep.LINE_UNREADABLE):raise refused('line_stopped')
                if refusal:raise not_run(ToolError(UNATTENDED_APPROVAL_TEXT,'unattended_run'))
                if (not current or current['status'] not in ('queued','running')
                        or (ledger is not None and (current['status']!='running' or ledger.lapse(db=db))) or service.work_stopped(job['id'])):
                    raise refused('line_piece_invalid')
                row=service.preparations.create(kind=prep.KIND_PREPARE,goal=fields['goal'],due_at=fields['due_at'],
                                                timezone=fields['timezone'],recurrence=None,channel=parent['channel'],
                                                created_from=job['id'],state=prep.STATE_SCHEDULED,accepted_by=accepted_by,
                                                delivery_mode=parent.get('delivery_mode'),title=fields['title'],offer=fields['offer'],
                                                origin=prep.ORIGIN_LINE_PIECE,line_id=parent['line_id'],
                                                piece_of=prep.slot_key(job['request_key']),db=db)
        except prep.PreparationRefusal as exc:
            raise ToolError(str(exc),exc.code,**ran_field(exc)) from None
        replaced=bool(row.get('piece_replaced'))
        LOG.info('line piece %s id=%s line=%s work=%s',('replaced' if replaced else 'scheduled'),row['id'],row['line_id'],job['id'])
        when=prep.local_text(row['due_at'],row['timezone'])
        return {'preparation_id':row['id'],'kind':row['kind'],'state':row['state'],'scheduled':True,
                'requires_owner_acceptance':False,'already_proposed':False,'replaced_pending':replaced,
                'due':prep.iso(row['due_at'],row['timezone']),'due_local':when,'now_local':prep.local_text(now,row['timezone']),
                'recurrence':'none','delivery':service.preparation_delivery(row),'accepted_by':row['accepted_by'],
                'line_id':row['line_id'],'next_step':msg('preparations.line_piece_scheduled',when=when)}

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
            resume_offer=row.get('origin')==prep.ORIGIN_LINE_RESUME
            if resume_offer and not service.lines.readable(row['line_id']):continue
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
            if resume_offer:
                item.update(line_id=row['line_id'],resume_offer=True,declined=row['last_outcome']=='declined')
            items.append(item)
        return 'earlier_plans: '+service._redact_known_secrets(json.dumps(items,ensure_ascii=False,separators=(',',':'))) if items else ''

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

    def notice_job(self, job):
        """Transient address for a held run's notices; its result Work stays web.

        Preserve current-run and paired-owner checks, including the pairing
        bound at start when present. A local/unpaired run gains no destination.
        """
        if not job or job.get('channel') != prep.CHANNEL_WEB or job.get('chat_id') is not None:
            return job
        service = self.service
        row = service.preparations.get(prep.preparation_of(job.get('request_key')))
        if (not row or row['last_run_job_id'] != job['id']
                or row.get('delivery_mode') == prep.DELIVERY_WHEN_NEEDED):
            return job
        channel = service.preparations.notice_channel(row)
        if channel == row['channel']:
            return job  # An ordinary quiet/unpaired run was never held for a bundle.
        destination = self.notice_destination(channel)
        if not destination:
            return job
        owner, generation, surface = destination
        with service.store.db() as db:
            try:origin = WorkLedger.request_chain(db, job['id'])[-1]
            except ValueError:return job
            anchor = db.execute('SELECT channel,chat_id FROM jobs WHERE id=?', (row['created_from'],)).fetchone()
            held = db.execute("SELECT detail FROM tool_events WHERE job_id=? AND tool='line_held' "
                              "AND json_extract(detail,'$.phase')='started' ORDER BY id LIMIT 1", (origin['id'],)).fetchone()
        if (anchor and str(anchor['channel']).startswith(surface + ':')
                and anchor['chat_id'] is not None and str(anchor['chat_id']) != str(owner)):
            return job
        if held and json.loads(held['detail'])['bundle_to'] != list(destination):
            return job
        return {**job, 'channel': surface + ':' + generation, 'chat_id': owner}

    def self_change_destination(self, row):
        """The existing self-change receipt destination, independent of report delivery."""
        service = self.service
        channel = service.preparations.notice_channel(row)
        if channel == prep.CHANNEL_WEB:
            origin = service.store.job(row['created_from']) if row.get('created_from') else self.owner_last_work()
            channel = self.preparation_channel(origin, prep.KIND_PREPARE, 'send')
        destination = self.notice_destination(channel)
        anchor = service.store.job(row['created_from']) if row.get('created_from') else None
        if (destination and anchor and str(anchor['channel']).startswith(destination[2] + ':')
                and anchor['chat_id'] is not None and str(anchor['chat_id']) != str(destination[0])):
            return None
        return destination

    def self_change_text(self, event):
        """One readable receipt, shared by messenger notices and the existing Settings result."""
        lang = owner_language(self.service.store) or 'ko'
        row = {**(self.service.preparations.get(event['preparation_id']) or {'goal_text': ''}),
               **event.get('snapshot', {})}
        operation = event['target'].split('.')[-1]
        check_specs = ({side: json.loads(event[side]['check_spec']) for side in ('before', 'after')}
                       if operation == 'check' else {})
        # #968: a rewritten goal is shown at every place it changed, before and after (none left out:
        # past a bound the receipt counts the rest and points to the whole goal in Settings).
        hunks = (prep.changed_hunks(event['before']['goal_text'], event['after']['goal_text'])
                 if 'goal_text' in event.get('after', {}) and 'goal_text' in event.get('before', {}) else [])
        # Display only: keep the complete hunks in the authorization judgment and stored receipt.
        # 1000 characters of numbered hunks + two 400-character summaries + a 400-character
        # reason leave room for the localized receipt, Settings pointer and undo below 3500.
        def excerpt(text):
            return text[:399] + '…' if len(text) > 400 else text

        shown, used, shortened = [], 0, False
        for hunk in hunks:
            displayed = tuple(excerpt(side) for side in hunk)
            # Include numbering and separators on both sides, including the first hunk.
            size = sum(map(len, displayed)) + 2 * (len(str(len(shown) + 1)) + 6)
            if used + size > prep.BRIEF_PREVIEW_CHARS:break
            shown.append(displayed)
            used += size
            shortened = shortened or displayed != hunk
        def summary(side):
            if operation == 'check':
                spec = check_specs[side]
                return msg('checks.change_spec', lang, seconds=spec['every_seconds'], digest=spec['digest'][:12],
                           hosts=', '.join(spec['arguments']['hosts']) or '-',
                           secrets=', '.join(spec['arguments']['secrets']) or '-')
            schedule = {**row, **event[side]}
            text = prep.proposal_summary(schedule, self.service.home_zone(), brief=False, lang=lang)
            if shown:
                text = excerpt(text)
                pieces = ' / '.join(f'[{number}] {hunk[0 if side == "before" else 1]}' for number, hunk in enumerate(shown, 1))
                text += '\n' + msg('preparations.proposal_brief', lang, goal=pieces)
                if len(hunks) > len(shown):
                    text += '\n' + msg('preparations.self_change_more', lang, count=len(hunks) - len(shown))
            if schedule.get('state') == prep.STATE_CANCELLED:
                text += '\n' + msg('preparations.self_change_cancel', lang)
            elif schedule.get('run_return_state') == prep.STATE_PAUSED or schedule.get('state') == prep.STATE_PAUSED:
                text += '\n' + msg('preparations.self_change_pause', lang)
            return text
        # #1097: named by its title (its schedule added when another row shares it), the same
        # name the owner may say back to undo; the id stays out of the owner's text.
        name = prep.owner_name({**row, 'id': event['preparation_id']},
                               self.service.preparations.rows(finished_pieces=False),
                               self.service.home_zone(), lang)
        text = msg('preparations.self_change_notice', lang, name=name,
                   change=msg('preparations.self_change_' + operation, lang), before=summary('before'),
                   after=summary('after'), reason=excerpt(event['reason']) if hunks else event['reason'],
                   undo=msg('button.undo', lang))
        if event.get('outside_content'):
            text += '\n' + msg('preparations.self_change_outside', lang)
        if check_specs and check_specs['before']['arguments']['code'] != check_specs['after']['arguments']['code']:
            text += '\n' + msg('checks.code_changed', lang)
        if shortened:
            text += '\n' + msg('preparations.self_change_excerpt', lang)
        return self.service._redact_known_secrets(text)

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
            if not row or service.preparations.notice_channel(row, db) != notification['surface']:
                return None
            if kind == prep.CHECK_DISPATCH_NOTICE_KIND:
                record = db.execute('SELECT content FROM messages WHERE job_id=?', (notification['job_id'],)).fetchone()
                return record['content'] if record else None
            if row['state'] == prep.STATE_CANCELLED:
                return None
            if kind.startswith(checks.PAUSED_NOTICE):
                if row['state']!=prep.STATE_PAUSED or not json.loads(row['check_status'] or '{}').get('paused'):
                    return None
                record=db.execute('SELECT content FROM messages WHERE job_id=?',(notification['job_id'],)).fetchone()
                return record['content'] if record else None
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
        with service.store.db() as db:
            expired=[item['line_id'] for item in db.execute(
                'SELECT line_id FROM line_mandates WHERE closed=0 AND ends_at<=?', (now,))]
        for line_id in expired:
            self.stop_line(line_id, end_line=False, due_before=now)
        try:
            picked=int(service.condition_checks.run_due_planner(now))
        except Exception as exc:
            LOG.warning('line planner admission failed kind=%s',type(exc).__name__)
            picked=0
        # Fetch enough to skip paused/untold dream rows without hiding due owner work.
        rows=service.preparations.due(now,limit=prep.MAX_ACTIVE+prep.TICK_LIMIT,busy=service.lane_work_ids())
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
                # Holding changes only the presentation Work. Retain the pairing
                # where the ordinary path would bind it: start for a reply,
                # settlement for a when-needed notification. Empty means local.
                bundle_to=None
                if self.held_row(row):
                    with service.store.db() as db:
                        held=db.execute("SELECT detail FROM tool_events WHERE tool='line_held' "
                                        "AND json_extract(detail,'$.preparation_id')=? ORDER BY id LIMIT 1",(row['id'],)).fetchone()
                    if held:
                        bundle_to=self.notice_destination(json.loads(held['detail'])['channel']) or []
                if row['state']==prep.STATE_RUNNING:
                    work_id=row.get('last_run_job_id')
                    if work_id:
                        with service.store.db() as db:
                            db.execute('BEGIN IMMEDIATE')
                            service.store.require_read_checkpoint_writer(db)
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
                            service.store.require_read_checkpoint_writer(db)
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
                    if settled and when_needed and bundle_to is not None:
                        self.record_held_pairing(row,work_id,bundle_to,'settled',now)
                    if settled:LOG.info('preparation settled id=%s outcome=%s next=%s decision=%s',row['id'],settled['last_outcome'],
                                        settled['state'],settled.get('last_decision') if when_needed else '-')
                    continue
                # #719: a when_needed run is kept in Still; only its notify decision reaches Telegram.
                send=paired and not when_needed
                work_id=service.preparations.start(row,channel=pairing.channel if send else 'web',
                                                chat_id=pairing.owner if send else None,now=now)
                if work_id and bundle_to is not None:
                    self.record_held_pairing(row,work_id,bundle_to,'started',now)
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
        # #1034: the result leads, so a notification preview shows it; the footer names the watch as
        # the owner accepted it (#393), not by its run's brief, with the stop hint.
        return (body+'\n\n'+msg('quickstart_service.watch_notification', title=prep.owner_title(row))+'\n'+
                msg('quickstart_service.stop_watching_hint'))

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
            # #969: the owner's goal and accepted risk lead; limits the AI set itself are its plan.
            if not row:return msg('preparations.run_fact')+'\n'+msg('preparations.own_limits')
            count,maximum=int(row['run_count']),row.get('max_runs')
            local=lambda slot:prep.local_text(slot,row['timezone'])
            planner=row.get('origin')==prep.ORIGIN_LINE_PLANNER
            recurrence=None if planner else row.get('recurrence')
            until_at=row.get('until_at')
            if planner:
                schedule=msg('checks.planner_triggered')
                following=None
                with service.store.db() as db:
                    mandate=continuous.current_planner(db,row,service.preparations.clock())
                until_at=mandate['ends_at'] if mandate else None
            else:
                # ``start`` counts the run and leaves ``due_at`` on its slot until the run is settled.
                if recurrence==prep.RECURRENCE_WINDOW:
                    following=prep.next_window_slot(row,row['due_at'])
                else:
                    following=prep.next_due(row['due_at'],row['timezone'],recurrence,row['due_at'],row.get('rule_start'),count)
                schedule=(msg('preparations.run_next',due=local(following)) if following is not None
                          else msg('preparations.run_last'))
                if row.get('check_spec'):
                    schedule=msg('checks.scheduled')
            if until_at is not None:
                schedule+=msg('preparations.run_end',end=local(until_at))
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
            if self.owner_requested_held_run(job):
                delivery=msg('preparations.run_delivery',channel=job['channel'].split(':',1)[0])
            else:
                delivery=msg('preparations.run_delivery_bundle') if self.held_row(row) else msg('preparations.run_delivery_needed' if row.get('delivery_mode')==prep.DELIVERY_WHEN_NEEDED
                             else 'preparations.run_delivery',channel=row['channel'])
            targets=service.condition_checks.state_targets(preparation_id, work_id=job['id'])
            check_state=('\n'+msg('checks.update_targets', targets=json.dumps(targets))) if targets else ''
            return msg('preparations.run_record', id=preparation_id,
                       position=msg('preparations.run_bounded',run=count,maximum=maximum) if maximum else str(count),
                       schedule=schedule,delivery=delivery)+'\n'+msg('preparations.own_limits')+check_state
        except Exception:
            LOG.info('preparation run fact unavailable job=%s',job.get('id'))
            return msg('preparations.run_fact')+'\n'+msg('preparations.own_limits')

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
            # #938: a line's rows by subquery (a line done in pieces has many); one row by its own key.
            keys,key=((prep.line_works_sql('request_key'),row['line_id']) if row and row.get('line_id')
                      else ('request_key LIKE ?',prep.REQUEST_KEY_PREFIX+preparation_id+':%'))
            with service.store.db() as db:
                candidates=[dict(item) for item in db.execute(
                    f"SELECT * FROM jobs WHERE created<? AND id<>? AND ({keys}) "
                    "AND response IS NOT NULL AND TRIM(response)<>'' "
                    "AND (delivery='sent' OR (chat_id IS NULL AND delivery='none')) ORDER BY created DESC,id DESC",
                    (job['created'],job['id'],key))]
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
            # #1035: a run after a delivered report writes the next update of a series, not a fresh answer.
            content+='\n'+msg('preparations.series_update')
        if row and prep.offered(row):
            # #393: the run works within what the owner read and accepted, not only its brief.
            content+='\n\n'+msg('preparations.accepted_offer_label')+'\n'+prep.accepted_offer(row)
        if row and row.get('line_id'):
            # #798: the line's current brief (its standing rules) and its earlier outcomes.
            content+='\n\n'+self.line_run_input(job,row)
        handoff=checks.handoff_event(service.store,job)
        if handoff:
            from .quickstart_store import owner_words_work
            trace=handoff['trace']
            for event in trace.get('events',()):
                source=service.store.job(event['source_work_id']) if event.get('source_work_id') else None
                if owner_words_work(source) and not service.store.work_request_forgotten(source['id']):
                    event['output']=service._redact_known_secrets(source['message'])
            content+='\n\n'+checks.handoff_text(service.store,trace,(row or {}).get('timezone'))
        rows.append({'role':'user','content':content,'job_id':job['id'],'channel':job.get('channel'),'qualifier':None,
                     'request_key':job.get('request_key'),'created':job.get('created')})
        return rows

    def home_zone(self):
        """The time zone for owner-facing schedule text, if configured."""
        service=self.service
        try:return service.context_observations.settings().get('timezone') or None
        except Exception:return None

    def preparation_proposal_html(self, bound):
        """#393: the Telegram proposal with each run's brief folded (expandable quote)."""
        service=self.service
        return service._redact_known_secrets(prep.proposal_html(bound['proposals'],bound['remaining'],service.home_zone()))

    def queue_preparation_proposal(self, job):
        """Offer this Work's unaccepted preparations to the paired owner once (#659).

        #1455, #984: work scheduled from an owner request or steer gets a message with a cancel button,
        so a reservation is managed in the conversation, not in Settings first.
        """
        service=self.service
        rows=service.preparations.proposed_from(job['id'])
        if rows:
            target=job
            if self.day_plan_run(job):
                # The plan's result stays local. Its offer goes to the owner's latest conversation.
                owner=self.owner_last_work()
                if owner:target={**job,'channel':owner['channel'],'chat_id':owner['chat_id']}
            service.queue_notification(target,'preparation_proposed',fingerprint=prep.digest(prep.proposal_page(rows)[0]))
        with service.store.db() as db:
            scheduled=[row['id'] for row in db.execute("SELECT id FROM preparations WHERE created_from=? AND state='scheduled' "
                                                      "AND accepted_by IN (?,?,?) ORDER BY created_at",
                                                      (job['id'],prep.ACCEPTED_OWNER_REQUEST,prep.ACCEPTED_OWNER_WORK,prep.ACCEPTED_OWNER_STEER))]
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

    def undo_self_change(self, fingerprint, *, preparation_id=None):
        """#1097: the self-change notice's button: undo exactly the run whose receipt it shows.

        True when undone; False when the row changed since (owner edit, another run, an
        earlier undo), so a stale button never reverts a later change.
        """
        if not isinstance(fingerprint,str):return False
        service=self.service
        with service.store.db() as db:
            receipt=db.execute("SELECT detail FROM tool_events WHERE tool='preparation_self_change' "
                               "AND json_extract(detail,'$.fingerprint')=?",(fingerprint,)).fetchone()
        if not receipt:return False
        event=json.loads(receipt['detail'])
        if preparation_id is not None and event['preparation_id'] != preparation_id:
            return False
        try:service.preparations.restore(event['preparation_id'],self_change_run_key=event['run_key'])
        except prep.PreparationRefusal:return False
        return True

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

    def answer_preparation_proposal(self, notification, proposals, accept, by='button', owner_work_id=None):
        """The owner's yes or no to exactly the ``proposals`` one sent message offered (#659; #172 judged answers).

        The button's path, shared with a judged reply or reaction.  An accepted
        message keeps a cancel button bound to exactly what it accepted (#1455).
        Returns what the message now says.
        """
        service=self.service
        accepted,expired,stale=[],[],[]
        capacity_refusal=None
        for row in proposals:
            try:
                resume_offer=row.get('origin')==prep.ORIGIN_LINE_RESUME
                if accept:
                    service.preparations.accept(row['id'],prep.ACCEPTED_OWNER_BUTTON if by=='button' else prep.ACCEPTED_OWNER_ANSWER,
                                                offer_digest=prep.digest([row]) if resume_offer else None,
                                                accepted_from=owner_work_id if resume_offer else None)
                    accepted.append(row)
                elif resume_offer:
                    service.preparations.decline_line_resume(row['id'],prep.digest([row]))
                else:
                    service.cancel_preparation(row['id'])
            except prep.PreparationRefusal as exc:
                if resume_offer and exc.code=='too_many':
                    capacity_refusal=str(exc)
                    continue
                if resume_offer and exc.code in ('line_resume_stale','invalid_state','not_found'):
                    # It may have changed between binding the message and settling the button.
                    stale.append(row)
                    continue
                # #395: a watch whose window ended while the message waited is not started.
                if exc.code!='expired':raise
                service.cancel_preparation(row['id'])
                expired.append(row)
        if capacity_refusal and not (accepted or expired or stale):
            return capacity_refusal  # Keep the exact offer open so the owner can retry.
        if accept:
            result_kind='preparation_accepted' if accepted else 'preparation_expired' if expired else 'preparation_already_settled'
        else:
            result_kind='preparation_denied' if len(stale)<len(proposals) else 'preparation_already_settled'
        LOG.info('preparation %s by owner %s work=%s count=%s expired=%s','accept' if accept else 'deny',by,
                 notification['job_id'],len(proposals),len(expired))
        service.store.update_notification(notification['id'],result_kind,fingerprint=(
            PREPARED_IDS_PREFIX+','.join(row['id'] for row in accepted) if accepted else None))
        markup=service.preparation_cancel_markup(notification['id']) if accepted else {'inline_keyboard':[]}
        text=service.notification_text(result_kind)
        if accepted and expired:text+='\n'+service.notification_text('preparation_expired')
        if stale:
            stale_text=msg('preparations.line_resume_stale')
            text=stale_text if result_kind=='preparation_already_settled' else text+'\n'+stale_text
        if capacity_refusal:text+='\n'+capacity_refusal
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

    def settings_decision(self, row):
        """The row with the last decision Settings may claim.

        Preserve Lane S's decision Evidence; Settings claims a held failure was told
        only after its bundle has the same receipt meaning as line_outcomes.
        """
        if (row.get('last_decision_reason')==prep.REASON_RUN_FAILED_TOLD
                and row.get('line_id') and not self.held_failure_told(row)):
            return {**row,'last_decision':prep.DECISION_QUIET,'last_decision_reason':prep.REASON_RUN_FAILED}
        return row

    @staticmethod
    def short_result(response):
        """A run's answer as Settings lists it: the first 280 characters."""
        last=str(response or '')
        return last[:280]+('…' if len(last)>280 else '')

    def preparations_status(self):
        """The Settings 준비해 둔 일 list: owner's own view (#659)."""
        service=self.service
        home_zone=service.home_zone()
        day=service.preparations.budget_day(service.preparations.clock())
        rows=[]
        lines={brief['line_id']:el.line_name(brief) for brief in service.lines.lines()}
        # #1005: a line's finished pieces come back once per line in ``piece_groups``.
        for row in service.preparations.rows(finished_pieces=False):
            # Preserve Lane S's decision Evidence; Settings claims a held failure was told
            # only after its bundle has the same receipt meaning as line_outcomes.
            row=self.settings_decision(row)
            last=self.short_result(row.get('last_response'))
            terms=prep.stored_window_terms(row)
            if row.get('self_change') and not row.get('self_change_undone'):
                last += '\n' + self.self_change_text(json.loads(row['self_change']))
            rows.append({'id':row['id'],'kind':row['kind'],'goal':row['goal_text'],'state':row['state'],
                         # #393: the owner's name and offer for it, when the AI wrote them.
                         'title':row.get('title_text'),'offer':row.get('offer_text'),
                         # #581: native form values are local wall times; parse_due owns zone resolution.
                         'due_local_input':datetime.fromtimestamp(row['due_at'],prep.zone(row['timezone'])).strftime('%Y-%m-%dT%H:%M'),
                         'due':row['due_at'],'due_iso':datetime.fromtimestamp(row['due_at'],prep.zone(row['timezone'])).isoformat(),'due_local':prep.local_text(row['due_at'],row['timezone'],home_zone),
                         'recurrence_labels':{lang:prep.rule_text(row['recurrence'],lang,timezone_name=row['timezone'],home_zone=row['timezone'])
                                              for lang in LANGUAGES} if str(row['recurrence'] or '').startswith('RRULE:') else None,
                         'timezone':row['timezone'],'recurrence':row['recurrence'],'delivery':service.preparation_delivery(row),
                         'accepted_by':row['accepted_by'],'last_outcome':row['last_outcome'],'last_work_id':row['last_run_job_id'],
                         'last_status':row.get('last_status'),'last_delivery':row.get('last_delivery'),
                         'last_result':last,'prepared_at':row['prepared_at'],
                         'created_at':row['created_at'],
                         # #719: the watch window, its bound and the last typed decision.
                         'every_minutes':(row['every_seconds']//60) if row.get('every_seconds') else None,
                         'until_local_input':datetime.fromtimestamp(row['until_at'],prep.zone(row['timezone'])).strftime('%Y-%m-%dT%H:%M') if row.get('until_at') else None,
                         'until_local':prep.local_text(row['until_at'],row['timezone'],home_zone) if row.get('until_at') else None,
                         'until':datetime.fromtimestamp(row['until_at'],prep.zone(row['timezone'])).isoformat() if row.get('until_at') else None,
                         'max_runs':row.get('max_runs'),'run_count':row.get('run_count') or 0,
                         'max_window_runs':prep.MAX_DAILY_WINDOW_RUNS * (1 if terms.get('recurrence') else prep.MAX_WINDOW_SECONDS // 86400),
                         'window_recurrence':terms.get('recurrence'),
                         'report_every_minutes':terms['report_seconds']//60 if terms.get('report_seconds') else None,
                         'last_decision':row.get('last_decision'),'last_decision_reason':row.get('last_decision_reason'),
                         # #798: the short name of the line this row works for.
                         'line_id':row.get('line_id'),'line_task':lines.get(row.get('line_id')),
                         'line_account':row.get('origin')==prep.ORIGIN_LINE_ACCOUNT})
            if row['state']==prep.STATE_PROPOSED and row.get('origin')==prep.ORIGIN_LINE_RESUME:
                rows[-1]['resume_offer_fingerprint']=prep.digest([row])
            if row.get('check_spec'):
                try:
                    view=checks.owner_view(service.store,row,day,home_zone)
                    labels={lang:service._redact_known_secrets(prep.schedule_text(row,home_zone,lang))
                            for lang in LANGUAGES}
                except CredentialError:
                    view={'code':'[redacted]'}
                    labels=dict.fromkeys(LANGUAGES,'[redacted]')
                rows[-1]['check']=view
                if prep.Preparations.self_undoable(row,'check'):
                    rows[-1]['undo_check']=json.loads(row['self_change'])['fingerprint']
                rows[-1]['every_minutes']=None
                rows[-1]['recurrence_labels']=labels
        # #575: the page uses the same catalog in its selected language.
        labels={'check_code':'checks.code','check_undo':'button.undo','check_undone':'preparations.self_change_undone',
                **{'check_'+key:'checks.'+key for key in ('today','last_run','allowed_requests','result_quiet',
                   'result_hit','result_failure','result_none','failures','state_unsaved','setup_missing','library',
                   'credential','requested_pause')},
                **{'budget_'+key:'scheduled_budget.'+key for key in ('label','help')}, 'save':'label.save', 'accept':'button.preparation_accept','decline':'button.preparation_decline',
                **{'delivery_'+mode:'preparations.delivery_'+mode for mode in ('send','keep','when_needed')},
                **{operation:'settings_orchestrator.label_preparations_'+operation for operation in ('pause','resume','edit','run_now')},
                **{field:'preparations.field_'+field for field in ('due','recurrence','every_minutes','until','max_runs',
                                                                              'rule','weekly_days','monthly_day','interval','rule_until')},
                **{'day_'+day:'preparations.day_'+day for day in prep.RULE_DAYS},
                'max_runs_hint':'preparations.max_runs_hint',
                'last_day':'preparations.rule_last_day','timezone':'label.time_zone','paused':'settings_orchestrator.paused','saved':'preparations.saved',
                'pieces_finished':'preparations.pieces_finished','pieces_newest':'preparations.pieces_newest'}
        # The same daily notice is visible in local Settings when no sending surface is paired.
        from .telegram_locale import translate
        notice_id = prep.SCHEDULED_USAGE_PREFIX + day
        with service.store.db() as db:
            notice = db.execute('SELECT content FROM messages WHERE job_id=? ORDER BY id LIMIT 1', (notice_id,)).fetchone()
        budget = {**service.preparations.budget_settings(), 'max':prep.MAX_SCHEDULED_DAILY_CALLS,
                  'notice':{lang:translate(notice['content'], lang) for lang in LANGUAGES} if notice else {}}
        groups=[]
        for line_id,group in service.preparations.finished_pieces_by_line().items():
            group['rows']=[self.settings_decision(piece) for piece in group['rows']]
            groups.append({'line_id':line_id,'line_task':lines.get(line_id),'count':group['count'],'states':group['states'],
                           'pieces':[{'id':piece['id'],'state':piece['state'],'goal':piece['goal_text'],
                                      'due_local':prep.local_text(piece['due_at'],piece['timezone'],home_zone),
                                      'last_work_id':piece['last_run_job_id'],'last_decision':piece.get('last_decision'),
                                      'last_decision_reason':piece.get('last_decision_reason'),
                                      'last_result':self.short_result(piece.get('last_response'))}
                                     for piece in group['rows']]})
        return {'preparations':rows,'piece_groups':groups,'budget':budget,'management':{key:{lang:msg(message_id,lang) for lang in LANGUAGES} for key,message_id in labels.items()}}

    def cancel_preparation(self, preparation_id):
        """``Preparations.cancel`` plus withdrawing its run's pending location request (#774).

        A run that asked the owner for a location would otherwise continue on
        the answer and run the cancelled preparation's goal again.
        """
        row=self.service.preparations.cancel(preparation_id)
        self.withdraw_cancelled_location(row)
        return row

    def withdraw_cancelled_location(self, row):
        service=self.service
        if row['state']==prep.STATE_CANCELLED and row.get('last_run_job_id'):
            if service.context_observations.cancel_work_requests(row['last_run_job_id']):
                service.store.remove_telegram_photo(row['last_run_job_id'])

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
        if operation in ('accept','cancel'):
            row=service.preparations.get(preparation_id)
            fingerprint=body.get('fingerprint')
            if row and row.get('origin')==prep.ORIGIN_LINE_RESUME and (row['state']==prep.STATE_PROPOSED or fingerprint is not None):
                if operation=='accept':
                    service.preparations.accept(preparation_id,prep.ACCEPTED_OWNER_BUTTON,offer_digest=fingerprint)
                else:
                    service.preparations.decline_line_resume(preparation_id,fingerprint)
            elif operation=='accept':
                service.preparations.accept(preparation_id,prep.ACCEPTED_OWNER_SETTINGS)
            else:
                service.cancel_preparation(preparation_id)
        elif operation=='delete':service.preparations.delete(preparation_id)
        elif operation=='pause':service.preparations.pause(preparation_id)
        elif operation=='resume':service.preparations.resume(preparation_id)
        elif operation=='restore':
            fingerprint=body.get('fingerprint')
            if fingerprint is None:
                service.restore_preparation(preparation_id)
            elif not self.undo_self_change(fingerprint,preparation_id=preparation_id):
                raise ValueError(msg('preparations.self_change_undo_stale'))
        elif operation=='edit':service.preparations.edit(preparation_id,**{key:value for key,value in body.items() if key not in ('operation','id')})
        elif operation=='run_now':
            row=service.preparations.get(preparation_id)
            if not row:raise prep.PreparationRefusal('not_found')
            held=self.held_row(row)
            channel=service.preparations.notice_channel(row) if held else row['channel']
            surface=next((item for item in service.surfaces if item.name==channel),None)
            pairing=surface.pairing() if surface is not None else None
            # The owner asked for this answer now. Only scheduled runs remain held;
            # the ordinary reply path rechecks work mode and this bound pairing.
            send=pairing is not None and (held or row.get('delivery_mode')!=prep.DELIVERY_WHEN_NEEDED)
            work_id=service.preparations.run_now(preparation_id,channel=pairing.channel if send else 'web',chat_id=pairing.owner if send else None)
            if held and work_id:
                destination=(pairing.owner,pairing.channel[len(surface.channel_prefix):],surface.name) if send else []
                self.record_held_pairing(row,work_id,destination,'owner_requested',service.preparations.clock())
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

        ``lines_only`` is the daily plan's input: it includes eligibility for a resume offer, without
        raw owner requests or the look back's proposals.
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
                lines[-1].update(open_matters=brief['open_matters'],
                                 can_offer_resume=bool(service.preparations.line_resume_target(brief['line_id'])))
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
                   for row in rows if (row['accepted_by'] in prep.SELF_SET or row['origin']==prep.ORIGIN_DREAM)
                   and row['origin']!=prep.ORIGIN_LINE_PIECE]
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
                service.store.require_read_checkpoint_writer(db)
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
                    service.store.require_read_checkpoint_writer(db)
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
                    service.store.require_read_checkpoint_writer(db)
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
            self.service.store.require_read_checkpoint_writer(db)
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

    def record_held_pairing(self, row, work_id, destination, phase, now):
        """Retain the original delivery identity in existing L2 holding Evidence.

        Start/settle remain lane S's transactions. If interrupted before this
        receipt commits, a missing identity keeps the part local, never rebound.
        """
        with self.service.store.db() as db:
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (work_id,'line_held','succeeded',json.dumps({'preparation_id':row['id'],
                        'bundle_to':destination,'phase':phase}),now))

    def owner_requested_held_run(self, job, db=None):
        """Run-now delivery belongs to the request, including its input continuations.

        Keep the local fallback out of bundles too: pairing later must neither
        readdress nor cancel an owner-requested answer that already stayed local.
        """
        if db is None:
            with self.service.store.db() as db:return self.owner_requested_held_run(job,db)
        try:origin=WorkLedger.request_chain(db,job['id'])[-1]
        except ValueError:return False
        return db.execute("SELECT 1 FROM tool_events WHERE job_id=? AND tool='line_held' "
                          "AND json_extract(detail,'$.phase')='owner_requested' LIMIT 1",(origin['id'],)).fetchone() is not None

    def line_bundle_parts(self, db, slot):
        """Re-read settled parts under the flush transaction; no latest-run pointer can lose a burst."""
        from . import work_mode
        from .direct_message_surface import DirectMessageSurface
        from .telegram_surface import TelegramSurface
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
            # An answered input keeps the same slot, with a new Work. Its original
            # start/pairing still owns bundle eligibility and the work-mode interval.
            try:origin = WorkLedger.request_chain(db, job['id'])[-1]
            except ValueError:continue
            if self.owner_requested_held_run(origin,db):continue
            started=db.execute("SELECT detail,created FROM tool_events WHERE job_id=? AND tool='preparation' "
                               "AND json_type(detail,'$.slot') IS NOT NULL ORDER BY id LIMIT 1",(origin['id'],)).fetchone()
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
            when_needed=run.get('delivery_mode')==prep.DELIVERY_WHEN_NEEDED
            restricted=False
            if when_needed:
                if settlement.get('decision')!=prep.DECISION_NOTIFY:continue
                # The ordinary notification is no longer bound after its
                # preparation was cancelled/deleted (watch_notification_text).
                restricted=not service.watch_notification_text({'job_id':job['id']})
            text=service.lines.bundle_part_text(job)
            if not text:continue
            held=db.execute("SELECT detail FROM tool_events WHERE tool='line_held' "
                            "AND json_extract(detail,'$.preparation_id')=? ORDER BY id LIMIT 1",(preparation_id,)).fetchone()
            destination=json.loads(held['detail'])['channel']
            surface=next((s for s in service.surfaces if s.name==destination),None)
            # Apply the destination's existing per-run egress check, using the
            # original Work start just as deliver_one/deliver_reply do. A new
            # bundle timestamp must never release work-mode history.
            since=origin['created']
            if when_needed:
                # Notification content dates from the earlier of Work creation
                # and queueing (settlement), including a clock moving backward.
                since=min(job['created'],job['settled_at'])
            if isinstance(surface,TelegramSurface):
                if when_needed:
                    since=surface._notification_since({'job_id':job['id'],'created':job['settled_at']})
                restricted=work_mode.was_on_since(service.store,since) or restricted
            elif isinstance(surface,DirectMessageSurface):
                restricted=not surface._allowed(surface.config(),since) or restricted
            pairing=surface.pairing() if surface is not None else None
            if pairing is not None:
                receipt=db.execute("SELECT detail FROM tool_events WHERE job_id=? AND tool='line_held' "
                                   "AND json_extract(detail,'$.phase')=? ORDER BY id LIMIT 1",
                                   (job['id'] if when_needed else origin['id'],'settled' if when_needed else 'started')).fetchone()
                bound=json.loads(receipt['detail']).get('bundle_to') if receipt else None
                # Older held runs have no pairing receipt; keep them locally.
                # Never substitute a later owner/generation for a run's own.
                restricted=bound!=[pairing.owner,surface.owner_generation(),surface.name] or restricted
            if restricted:
                # Match an unbundled refused reply: the answer stays in its
                # web transcript, outcome 'kept', delivery cancelled forever.
                # No bundle receipt may later turn it into 'delivered'.
                db.execute("UPDATE jobs SET delivery='cancelled' WHERE id=? AND delivery='none'",(job['id'],))
                continue
            seen.add(job['id'])
            parts.append({'job':job,'row':row,'brief':brief,'text':text,
                          'destination':destination})
        return parts

    def reconcile_line_bundles(self, now):
        """Advance watch notification state only after an unsuppressed bundle reaches the owner."""
        service=self.service
        with service.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            service.store.require_read_checkpoint_writer(db)
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
                    # As in notification delivery, compare the scrubbed answer,
                    # not its displayed projection, note or empty-failure body.
                    text=service.scrub_prepared_answer(dict(part))
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
            service.store.require_read_checkpoint_writer(db)
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

    def line_has_pending_work(self, line_id, exclude=None):
        """Whether a work row is scheduled, apart from the periodic account and the given row."""
        with self.service.store.db() as db:
            return prep.Preparations.line_has_pending_work(db,line_id,exclude)

    def line_schedule_note(self, brief, row):
        """The line's scheduling outlook after this row's current or first run."""
        if not brief or brief['state']!=el.STATE_ACTIVE or row.get('origin')==prep.ORIGIN_LINE_ACCOUNT:
            return ''
        if self.line_has_pending_work(brief['line_id'],exclude=row['id']):
            return ''
        if row.get('origin')==prep.ORIGIN_LINE_PLANNER:
            with self.service.store.db() as db:
                mandate=continuous.current_planner(db,row,self.service.preparations.clock())
            will_wait=row['state']==prep.STATE_SCHEDULED or row.get('run_return_state')==prep.STATE_SCHEDULED
            return '' if mandate and will_wait else msg('lines.no_following_run')
        now=max(self.service.preparations.clock(),row['due_at'])
        count=row['run_count']+(row['state']==prep.STATE_SCHEDULED)
        if row.get('run_return_state'):
            following=row['run_return_state']==prep.STATE_SCHEDULED
        elif row['recurrence']==prep.RECURRENCE_WINDOW:
            current={**row,'run_count':count}
            following=prep.next_window_slot(current,now) is not None or bool(prep.next_day_window(current,now))
        else:
            following=prep.next_due(row['due_at'],row['timezone'],row['recurrence'],now,row.get('rule_start'),count) is not None
        return '' if following else msg('lines.no_following_run')

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
            # #938: a line done in pieces has a row per piece (``line_works_sql``).
            keys=prep.line_works_sql('j.request_key')
            jobs=[dict(row) for row in db.execute(
                f"SELECT j.*,EXISTS(SELECT 1 FROM telegram_notifications n WHERE n.job_id=j.id AND n.kind=? AND n.state='sent') "
                f"AS notified, EXISTS(SELECT 1 FROM tool_events b JOIN jobs bundle ON bundle.id=json_extract(b.detail,'$.bundle_id') "
                f"WHERE b.job_id=j.id AND b.tool='line_bundled' "
                f"AND NOT EXISTS(SELECT 1 FROM tool_events s WHERE s.job_id=b.job_id AND s.tool='line_bundle_suppressed' "
                f"AND json_extract(s.detail,'$.bundle_id')=bundle.id "
                f"AND json_extract(s.detail,'$.line_id')=json_extract(b.detail,'$.line_id')) "
                f"AND (bundle.delivery='sent' OR (bundle.channel='web' AND bundle.delivery='none'))) "
                f"AS bundled FROM jobs j WHERE ({keys}) AND j.id IS NOT ? ORDER BY j.created DESC,j.id DESC LIMIT ?",
                (prep.NOTIFY_KIND,line_id,exclude,limit*4 if limit is not None else -1))]
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
                        'preparation_id':prep.preparation_of(job.get('request_key')),
                        'text':el.excerpt(service.scrub_work_text(job['id'],text))})
            if limit is not None and len(out)>=limit:break
        return out

    def line_entry(self, brief):
        """One line as the list answer shows it: name, promise, last delivered report, next run or blocker."""
        service=self.service
        rows=self.line_rows(brief['line_id'])
        home_zone=service.home_zone()
        zone=home_zone or (rows[0]['timezone'] if rows else None)
        delivered=[item for item in self.line_outcomes(brief['line_id'],limit=el.OUTCOMES_SHOWN)
                   if item['outcome']==el.OUTCOME_DELIVERED]
        entry={'line_id':brief['line_id'],'version':brief['version'],'state':brief['state'],
               'title':el.line_name(brief),'task':brief['task'],
               'report_promise':brief['report_promise'],
               'last_report':prep.local_text(delivered[0]['at'],zone) if delivered else None}
        day=service.preparations.budget_day(service.preparations.clock())
        for row in rows:
            if not row.get('check_spec'):continue
            spec=json.loads(row['check_spec'])
            status=json.loads(row['check_status'] or '{}')
            today=checks.daily_status(status,day)
            try:
                title=service._redact_known_secrets(prep.owner_title(row))
            except CredentialError:
                title='[redacted]'
            entry.setdefault('checks',[]).append({'title':title,'every_seconds':spec['every_seconds'],
                'last_result':status.get('last_result'),'runs':today.get('runs',0),'wakes':today.get('wakes',0),
                'daily_wakes':checks.DAILY_WAKES})
        if self.waiting_line(brief):entry['state']='waiting';return entry
        if brief['state']!=el.STATE_ACTIVE:return entry
        active=[row for row in rows if row['state'] in prep.ACTIVE_STATES]
        work=[row for row in active if row['state']==prep.STATE_SCHEDULED and row.get('origin')!=prep.ORIGIN_LINE_ACCOUNT]
        if not work:entry['nothing_scheduled']=True
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
        account=[row for row in active if row['state']==prep.STATE_SCHEDULED and row.get('origin')==prep.ORIGIN_LINE_ACCOUNT]
        following=min(work or account,key=lambda row:row['due_at'],default=None)
        if following:
            entry['next']=prep.local_text(following['due_at'],following['timezone'],home_zone)
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
        result={**el.public_brief(brief),'ref':el.REF_PREFIX+brief['line_id'],
                'outcomes':self.line_outcomes(line_id,limit=el.READ_OUTCOMES),
                'text':el.brief_text(brief)+'\n'+el.outcomes_text(self.line_outcomes(line_id,limit=el.READ_OUTCOMES),
                                                               self.service.home_zone())}
        with self.service.store.db() as db:
            now=self.service.preparations.clock()
            mandate=continuous.active(db,line_id,now)
            if mandate:
                day=self.service.preparations.budget_day(now,db)
                result['standing']={'ends_at':datetime.fromtimestamp(mandate['ends_at'],timezone.utc).isoformat(), 'owner_revision':mandate['revision'],
                                    'planner_wakes':mandate['wakes'] if mandate['wake_day']==day else 0,
                                    'daily_wakes':checks.DAILY_WAKES}
                plan=continuous.plan_target(db,line_id)
                if plan and plan['expires']>now:
                    check=db.execute('SELECT state,check_spec,until_at FROM preparations WHERE id=?',(plan['check_id'],)).fetchone()
                    if (check and check['state']==prep.STATE_SCHEDULED and check['check_spec'] and check['until_at']>now
                            and json.loads(check['check_spec'])['digest']==plan['digest']):
                        result['standing']['plan']={'id':plan['id'], 'expires':datetime.fromtimestamp(plan['expires'],timezone.utc).isoformat(), 'safe_mode':'hold'}
        return result

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
        lines=[msg('lines.rule_with_words',number=number,rule=self.rule_text(rule),words=self.rule_words(rule))
               for number,rule in enumerate(brief['rules'],1)]
        return '\n'.join(lines) or msg('lines.no_rules')

    @staticmethod
    def rule_text(rule, lang='ko'):
        return rule['text']+(' '+msg('lines.inferred',lang) if rule.get('basis')=='inferred' else '')

    def rule_words(self, rule, lang='ko'):
        """The owner's own words a rule came from, excerpted, or the not-recorded label."""
        service=self.service
        source=service.store.job(rule['from']) if rule.get('from') else None
        words=(service.scrub_work_text(source['id'],source.get('message'))
               if source and not service.store.work_request_forgotten(source['id']) else '')
        return el.excerpt(words,el.WORDS_CHARS) or msg('label.not_recorded',lang)

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
        with service.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            service.store.require_read_checkpoint_writer(db)
            restart=self.line_start_restart(brief['line_id'],db)
            if stood and self.line_row_started(brief['line_id'],'account',restart,db):return None
            # A cancelled account also stays cancelled on a tick; only a new owner act may restart it.
            result=service.preparations.ensure_line_account(
                brief,created_from=job['id'],db=db,accepted_by=accepted_by,bundle_channel=bundle_channel,
                channel=channel or service.preparation_channel(job,prep.KIND_PREPARE,'send'),
                replace_inactive=not stood or bool(restart))
            if stood:self.record_line_row_started(job['id'],brief['line_id'],'account',restart,db)
            return result

    def line_notice(self, job, kind, line_id, version, *, db=None, fingerprint=None):
        """Queue one owner notice of a line on ``job``'s conversation (``lines.NOTICE_*``).

        Returns '' when a surface took it, else the notice's text: this conversation has no
        surface that sends notices (the local web chat), so the reply says it instead.
        With a transaction, queue or record the notice alongside the state change itself.
        """
        service=self.service
        key=f'{line_id}:{version}'
        notice=f'{kind}{key}'
        fingerprint=fingerprint or key
        if db is None:
            service.queue_notification(job,notice,fingerprint=fingerprint)
            if service.store.notification_of(job['id'],notice):return ''
        else:
            surface=service.surface_of(job)
            if surface is not None:
                generation=job['channel'][len(surface.channel_prefix):]
                service.store.telegram.queue_notification(job['id'],job['chat_id'],generation,
                                                          notice,fingerprint,surface=surface.name,db=db)
                return ''
        text=self.line_notice_text({'kind':notice,'fingerprint':fingerprint,'job_id':job['id']}) or ''
        if db is not None and text:
            service.record_conversation_notice(text,channel=job['channel'],db=db)
            return ''
        return text

    def line_rule_notice(self, job, line_id, version):
        """#1227: the rule changes of one owner Work reach the owner as one notice.

        A later change in the same Work re-points the Work's still-queued notice of this line at
        ``line:first:last`` (a Work's notices wait while it is in its lane), when every version
        since ``first`` was written by this Work.  Otherwise, or with no surface, as ``line_notice``.
        """
        service=self.service
        if service.surface_of(job) is not None:
            prefix=f'{el.NOTICE_RULE}{line_id}:'
            with service.store.db() as db:
                row=db.execute("SELECT kind FROM telegram_notifications WHERE job_id=? AND kind LIKE ? AND state='queued' "
                               "ORDER BY rowid DESC LIMIT 1",(job['id'],prefix+'%')).fetchone()
            suffix=row['kind'].removeprefix(prefix) if row else ''
            if suffix.isdecimal():
                first=int(suffix)
                # Each version of the set was written by this Work and changed the rules and nothing else,
                # so the set's one undo reverts nothing the notice does not show.
                stored=[service.lines.version(line_id,number) for number in range(first-1,version+1)] if first<version else []
                texts=lambda brief:[rule['text'] for rule in (brief or {}).get('rules') or []]
                others=lambda brief:[brief.get(field) for field in el.FIELDS if field!='rules']
                if stored and all(prior and item and item['written_in']==job['id'] and texts(item)!=texts(prior)
                                  and others(item)==others(prior) for prior,item in zip(stored,stored[1:])):
                    if service.store.rebind_notification(job['id'],row['kind'],f'{line_id}:{first}:{version}'):return ''
        return self.line_notice(job,el.NOTICE_RULE,line_id,version)

    def rule_changes(self, line_id, first, last):
        """Return pairs of rules before and after the net change, with None for an absent rule."""
        service=self.service
        end=service.lines.version(line_id,last)
        if end is None:return []
        earlier=service.lines.version(line_id,first-1) if first>1 else None
        base={rule['text']:rule for rule in (earlier or {}).get('rules',[])}
        previous=base
        origins={}
        for number in range(first,last+1):
            stored=end if number==last else service.lines.version(line_id,number)
            if stored is None:
                # A missing version cannot establish a replacement across the gap.
                previous=None
                origins.clear()
                continue
            current={rule['text']:rule for rule in stored['rules']}
            if previous is not None:
                dropped=previous.keys()-current
                added=current.keys()-previous
                if len(dropped)==len(added)==1:
                    old=dropped.pop()
                    origins[added.pop()]=origins.pop(old,old)
            previous=current
        # Pair only net drops with net additions, even if a rule text returned later in the Work.
        dropped={text:rule for text,rule in base.items() if text not in current}
        changes=[(dropped.pop(origins.get(text),None),rule) for text,rule in current.items() if text not in base]
        changes.extend((rule,None) for rule in dropped.values())
        return changes

    @staticmethod
    def told(next_step, untold):
        """The worker's next step, with the notice to say in the reply when Still could not send it."""
        return next_step if not untold else msg('lines.say_in_reply',text=untold)

    def stop_line(self, line_id, work_id=None, *, end_line=True, due_before=None):
        """Cancel the line's work and optionally end its brief. Return ``(brief, changed)``.

        The announcement button ends the line. Within the tool, the owner may stop just its work.
        A running row refuses at its next step through the Work Stop or line-state check.
        """
        service=self.service
        with service.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            service.store.require_read_checkpoint_writer(db)
            mandate=continuous.record(db,line_id)
            if due_before is not None and not continuous.lapsed(db,line_id,due_before):
                return service.lines.current(line_id,db),False
            continuous.fence(db,line_id,service.preparations.clock(),close=True)
            if end_line:
                before,after=service.lines.write(line_id,work_id,None,'stopped',
                                                 lambda brief:{**brief,'state':el.STATE_STOPPED},db=db)
            else:
                before=after=service.lines.current(line_id,db)
                if after is None or after['state']!=el.STATE_ACTIVE:
                    if due_before is None:
                        if after is None:raise el.LineRefusal('line_not_found')
                        return after,False
            changes_before=db.total_changes
            keys=prep.line_works_sql('request_key')
            db.execute(f"UPDATE jobs SET delivery='cancelled' WHERE delivery='pending' AND {keys}",(line_id,))
            if not end_line:
                # Fence replies that have yet to be produced by a paired run.
                db.execute("UPDATE jobs SET delivery='cancelled' WHERE delivery='none' AND chat_id IS NOT NULL "
                           "AND id IN (SELECT last_run_job_id FROM preparations WHERE line_id=? AND state=?)",
                           (line_id,prep.STATE_RUNNING))
                # The brief stays active, so retire reports waiting to be bundled or notified.
                db.execute(f"UPDATE jobs SET delivery='cancelled' WHERE delivery='none' AND {keys} "
                           "AND EXISTS(SELECT 1 FROM tool_events e WHERE e.job_id=jobs.id AND e.tool='preparation' "
                           "AND json_type(e.detail,'$.outcome') IS NOT NULL AND EXISTS(SELECT 1 FROM tool_events h "
                           "WHERE h.tool='line_held' AND json_extract(h.detail,'$.preparation_id')="
                           "json_extract(e.detail,'$.preparation_id') AND h.created<=e.created)) "
                           "AND NOT EXISTS(SELECT 1 FROM tool_events b WHERE b.job_id=jobs.id AND b.tool='line_bundled') "
                           "AND NOT EXISTS(SELECT 1 FROM telegram_notifications n WHERE n.job_id=jobs.id AND n.kind=?)",
                           (line_id,prep.NOTIFY_KIND))
                db.execute("UPDATE telegram_notifications SET state='cancelled' WHERE kind=? AND state='queued' "
                           f"AND job_id IN (SELECT id FROM jobs WHERE {keys})",(prep.NOTIFY_KIND,line_id))
            service.lines.suppress_pending_bundles(line_id,db)
            replies_changed=db.total_changes!=changes_before
            cancelled=[]
            for row in self.line_rows(line_id,db):
                if row['state'] not in prep.ACTIVE_STATES:continue
                if row['state']==prep.STATE_RUNNING and row['last_run_job_id']:
                    # Ordinary row cancellation lets a running Work finish. This stop reaches its next step.
                    service.store.append_config_list(WORK_STOP_KEY,row['last_run_job_id'],WORK_STOP_KEEP,db=db)
                    db.execute("UPDATE jobs SET status='cancelled',delivery='cancelled' WHERE id=? AND status='queued'",
                               (row['last_run_job_id'],))
                cancelled.append(service.preparations.cancel(row['id'],db=db))
        for row in cancelled:self.withdraw_cancelled_location(row)
        changed=(after['version']!=before['version']) if end_line else bool(
            cancelled or replies_changed or (mandate and not mandate['closed']))
        LOG.info('line stop id=%s end_line=%s changed=%s',line_id,end_line,changed)
        return after,changed

    def stopped_line_run(self, job):
        """Whether a line stop already cancelled this reply, or the brief is stopped or gone."""
        preparation_id=prep.preparation_of((job or {}).get('request_key'))
        if not preparation_id:return False
        with self.service.store.db() as db:
            row=db.execute('SELECT p.line_id,j.delivery FROM preparations p JOIN jobs j ON j.id=? WHERE p.id=?',
                           (job['id'],preparation_id)).fetchone()
            return bool(row and row['line_id'] and
                        (row['delivery']=='cancelled' or prep.line_refusal(db,row['line_id'])))

    def undo_line_version(self, line_id, version, work_id=None, *, through=None):
        """Put back the brief as it was before ``version``, only while ``through`` (else ``version``) is still current.

        #1227: one rule notice covers versions ``version``..``through`` of one owner Work; its undo
        reverts the whole set, or nothing once a later change followed.
        """
        service=self.service
        earlier=service.lines.version(line_id,int(version)-1) if int(version)>1 else None
        if earlier is None:raise el.LineRefusal('line_nothing_to_undo')
        def restore(brief):
            restored={key:earlier[key] for key in el.FIELDS}
            # Undo reverts a rule change; a stop since then stays the owner's.
            restored['state']=brief['state']
            return restored
        with service.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            before,after=service.lines.write(line_id,work_id,int(through or version),'undo',restore,db=db)
            if after['version']!=before['version']:
                continuous.fence(db,line_id,service.preparations.clock(),work_id=work_id)
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

    def line_pieces_account(self, row, job):
        """#938 (D4): what the periodic account of ``row`` is told about the line's pieces, or ``''``.

        The account's own outcomes show only the newest runs; a line done in pieces may have run many
        more since the last account.  This counts every piece run of the period by what came of it
        (``line_outcomes``) and names the pending piece's due time.  Counts only; no new store.

        The period is bounded by observed times on both ends, so a late account counts no run twice:
        from when the account's previous run started to when this run started (``jobs.created``).
        """
        line_id=row['line_id']
        rows=self.line_rows(line_id)
        pieces={item['id'] for item in rows if item.get('origin')==prep.ORIGIN_LINE_PIECE}
        if not pieces:return ''
        current=prep.slot_key(job['request_key'])
        with self.service.store.db() as db:
            runs=[dict(item) for item in db.execute('SELECT request_key,created FROM jobs WHERE request_key LIKE ?',
                                                    (prep.REQUEST_KEY_PREFIX+row['id']+':%',))]
        since=max((item['created'] for item in runs if prep.slot_key(item['request_key'])!=current),default=row['created_at'])
        until=job['created']
        ran=[item for item in self.line_outcomes(line_id,exclude=job['id'],limit=None)
             if item['preparation_id'] in pieces and since<=item['at']<until]
        # Only a scheduled piece starts (``Preparations.start``); a paused one waits for the owner (as ``line_entry``).
        pending=min((item for item in rows if item['id'] in pieces and item['state']==prep.STATE_SCHEDULED),
                    key=lambda item:item['due_at'],default=None)
        paused=any(item['id'] in pieces and item['state']==prep.STATE_PAUSED for item in rows)
        if not ran and pending is None and not paused:return ''
        counts={label:sum(1 for item in ran if item['outcome']==label)
                for label in (el.OUTCOME_DELIVERED,el.OUTCOME_KEPT,el.OUTCOME_FAILED,el.OUTCOME_OPEN)}
        text=msg('lines.account_pieces',count=len(ran),**counts)
        if pending is not None:
            text+=' '+msg('lines.account_next_piece',at=prep.local_text(pending['due_at'],pending['timezone']))
        elif paused:
            text+=' '+msg('lines.account_piece_paused')
        return text

    def line_checks_account(self, row, job):
        """Pin the cumulative counts and their delta to the slot whose account first reads them."""
        store=self.service.store
        slot=prep.slot_key(job['request_key'])
        with store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            store.require_read_checkpoint_writer(db)
            account=db.execute('SELECT * FROM preparations WHERE id=?',(row['id'],)).fetchone()
            if not account:return ''
            saved=json.loads(account['check_status'] or '{}')
            if saved.get('slot')==slot:
                delta=saved['delta']
            elif prep.Preparations.owns_running(account,job):
                # Include deleted rows through their existing lineage receipts so the line total only grows.
                counts={'runs':0,'wakes':0}
                observations=db.execute(
                    "SELECT check_status AS detail FROM preparations WHERE line_id=? AND check_spec IS NOT NULL "
                    "UNION ALL SELECT json_extract(detail,'$.check_totals') FROM tool_events "
                    "WHERE tool='preparation_lineage' AND status='deleted' AND json_extract(detail,'$.line_id')=? "
                    "AND json_extract(detail,'$.check_totals') IS NOT NULL", (row['line_id'],row['line_id'])).fetchall()
                if not observations:return ''
                for item in observations:
                    for key,value in checks.totals(json.loads(item['detail'] or '{}')).items():
                        counts[key]+=value
                previous=saved.get('totals',{})
                delta={key:count-previous.get(key,0) for key,count in counts.items()}
                db.execute('UPDATE preparations SET check_status=? WHERE id=?',
                           (json.dumps(dict(slot=slot,totals=counts,delta=delta)),row['id']))
            else:
                return ''
        return msg('lines.account_checks',**delta)

    # -- #968: a scheduled run revises its own plan ---------------------------------------------

    def own_running_row(self, job):
        """The preparation whose current slot ``job`` runs (``Preparations.owns_running``), or None."""
        preparation_id=prep.preparation_of((job or {}).get('request_key'))
        row=self.service.preparations.get(preparation_id) if preparation_id else None
        return row if row and prep.Preparations.owns_running(row,job) else None

    def line_run_row(self, job):
        """#968 (#938 D1): the row of an owner-set-up line whose current run is ``job``, or None.

        A ``prepare`` row of an active line, not the line's periodic account, that the owner accepted
        or that was set up in a Work the owner typed (``LINE_RUN_ACCEPTED``).  Its own run may rewrite
        its goal and the line's open matters, then tell, marking the receipt if the run read outside content (#984).
        """
        row=self.own_running_row(job)
        if not (row and row['kind']==prep.KIND_PREPARE and row.get('line_id')
                and row.get('origin')!=prep.ORIGIN_LINE_ACCOUNT and row.get('accepted_by') in prep.LINE_RUN_ACCEPTED):
            return None
        brief=self.service.lines.current(row['line_id'])
        if brief is None or brief['state']!=el.STATE_ACTIVE:return None
        return row

    def steer_asked(self, job, change, standing=''):
        """#968: does the owner's steer into ``job`` ask for exactly ``change``?  Judged over those words only."""
        service=self.service
        words=service.owner_request_words(job)
        if not words:return False
        judged=service.decision_judge.explicit_line_change_request(words,change,standing or msg('lines.no_rules'),
                                                                   service.preparation_conversation(job),work_id=job['id'])
        return judged.outcome==JUDGMENT_YES

    def may_rewrite_own_goal(self, job, row, before, after):
        """#968: whether ``job``, the current run of ``row``, may rewrite its goal from ``before`` to ``after``.

        A run the owner steered may (``owner_steer_words``); after outside content only when the
        owner's words asked for exactly this. A qualifying owner-set-up line row may independently
        revise its own plan (``line_run_row``), including after outside content (#984).
        """
        service=self.service
        own=self.own_running_row(job)
        if not own or own['id']!=row['id'] or own['kind']!=prep.KIND_PREPARE:return False
        if self.line_run_row(job) is not None:return True
        if service.owner_steer_words(job):
            if not service.store.outside_content_sources(job['id']):return True
            # Every changed place, complete: what is judged is everything that would be saved.
            return self.steer_asked(job,'the scheduled work\'s own instruction, changed at these places:\n'
                                    +prep.hunks_text(prep.changed_hunks(before,after)))
        return False

    def own_line_authority(self, job, action, line_id):
        """#968: why a scheduled run may write its own line now: ``steer``, ``self`` (open matters) or None."""
        row=self.own_running_row(job)
        if not row or not row.get('line_id') or row['line_id']!=line_id:return None
        if self.service.owner_steer_words(job):
            return 'steer' if action in ('revise','stop') else None
        return 'self' if action=='revise' and self.line_run_row(job) else None

    def line_handler(self, job):
        """The ``entrusted_line`` handler bound to one Work (#798).

        ``list`` and ``read`` answer from the store.  ``open``, ``revise``, ``undo``, ``stop`` and
        ``restart`` are refused unless the owner's own typed message
        started this Work: a scheduled run, a continuation or a replayed turn cannot add a rule,
        widen a limit or resume a stopped line.  A rule change on a line that touched a
        money-marked connection is made only when the owner's message asks for exactly it.
        #968: a scheduled run may write its own line only (``own_line_authority``): revise or stop
        it as the owner's steer into the run asks, or, in a run of a line the owner set up, revise its open matters and send a separate notice.
        """
        service=self.service
        def refused(code,detail=''):
            return not_run(ToolError(msg('lines.refused_'+code,detail=detail) if detail else msg('lines.refused_'+code),code))
        def handle(args):
            if args.get('line'):args={**args,'line':el.line_id_of(args['line'])}
            action=args.get('action')
            try:
                if action=='list':return self.lines_listing()
                if action=='read':
                    result=self.line_read(args.get('line'))
                    self._record_line_read(job['id'],result,held=True)
                    if service.owner_typed_work(job):
                        with service.store.db() as db:
                            db.execute('BEGIN IMMEDIATE')
                            service.store.require_read_checkpoint_writer(db)
                            # Revision zero only links this Work to historical effect receipts.
                            if continuous.record(db,result['line_id']):
                                continuous.record_membership(db,job['id'],result['line_id'],0)
                    return result
                if action not in ('open','revise','undo','stop','restart','entrust'):raise refused('line_invalid_action')
                if service.owner_typed_work(job):return getattr(self,'_line_'+action)(job,args)
                # #968: a scheduled run writes only its own line: as the owner's steer asks, or its open matters.
                authority=self.own_line_authority(job,action,args.get('line'))
                if authority is None:raise refused('unattended_run')
                if action=='revise':return self._line_revise(job,args,authority)
                return self._line_stop(job,args,steered=True)
            except el.LineConflict as exc:
                raise refused('line_version_conflict',str(exc.current_version)) from None
            except el.LineRefusal as exc:
                raise refused(exc.code) from None
        return handle

    def _line_entrust(self, job, args):
        service=self.service
        line_id=args.get('line')
        now=service.preparations.clock()
        try:
            ends_at=continuous.parse_end(args.get('ends_at'))
        except (ValueError, OverflowError, OSError):
            raise el.LineRefusal('line_invalid_end') from None
        current=service.lines.current(line_id)
        if not current or current['state']!=el.STATE_ACTIVE:
            raise el.LineRefusal('line_stopped')
        goal=service._redact_known_secrets(str(args.get('planner_goal') or current['task'])).strip()
        if not goal or len(goal)>prep.BRIEF_MAX_CHARS:
            raise el.LineRefusal('line_invalid_planner')
        end=args['ends_at']
        summary=(f"Entrust the line {el.line_name(current)!r} until {end}. Task: {current['task']!r}. "
                 "Its runs follow the owner's current payment "
                 f"autonomy. Planner instruction: {goal}")
        from .quickstart_store import owner_words_work
        if not owner_words_work(job) or service.decision_judge.explicit_preparation_request(
                service.owner_request_words(job),summary,service.preparation_conversation(job)).outcome!=JUDGMENT_YES:
            raise el.LineRefusal('line_change_needs_owner')
        channel=service.preparation_channel(job,prep.KIND_PREPARE,'send')
        with service.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            service.store.require_read_checkpoint_writer(db)
            now=service.preparations.clock()
            if not now<ends_at<=service.condition_checks._next_day(now,db):
                raise el.LineRefusal('line_invalid_end')
            latest=service.lines.current(line_id,db)
            if not latest or latest['version']!=current['version']:
                raise el.LineRefusal('line_plan_stale')
            if continuous.active(db,line_id,now) or continuous.lapsed(db,line_id,now):
                raise el.LineRefusal('line_already_entrusted')
            executors=[row for row in self.line_rows(line_id,db)
                       if row.get('check_spec') and row['state'] in (prep.STATE_SCHEDULED,prep.STATE_RUNNING,prep.STATE_PAUSED)]
            if len(executors)>1 or any(row['state']==prep.STATE_RUNNING for row in executors):
                raise el.LineRefusal('line_executor_exists')
            try:
                row=service.preparations.create(
                    kind=prep.KIND_PREPARE,goal=goal,due_at=now,timezone=service.home_zone() or 'UTC',
                    recurrence=None,channel=channel,created_from=job['id'],state=prep.STATE_SCHEDULED,
                    accepted_by=prep.ACCEPTED_OWNER_WORK,origin=prep.ORIGIN_LINE_PLANNER,line_id=line_id,db=db)
            except prep.PreparationRefusal as exc:
                raise not_run(ToolError(str(exc),exc.code)) from None
            revision=continuous.entrust(db,line_id,job['id'],row['id'],now,ends_at)
            # Keep the brief and deadline of this entrustment even if a correction fences its plan.
            self.line_notice(job,el.NOTICE_ENTRUSTED,line_id,revision,db=db,
                             fingerprint=f"{line_id}:{revision}:{current['version']}:{end}")
        return {**el.public_brief(current),'ends_at':end,
                'owner_revision':revision,'planner_id':row['id']}

    def _line_open(self, job, args):
        service=self.service
        rules=el.split_rules(args.get('rules'))
        days=el.whole_days(args.get('account_every_days'))
        brief=service.lines.open(job['id'],task=service._redact_known_secrets(args.get('task') or ''),
                                 title=service._redact_known_secrets(el.normalize_title(args.get('title'))),
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

    def _line_revise(self, job, args, authority='owner'):
        """``authority``: ``owner`` (a Work the owner typed), ``steer`` (#968: a scheduled run the owner
        steered, judged against those words when the line touches money or the run read outside
        content) or ``self`` (a line run's own open matters only, recorded with undo, not sent as a notice (#1228))."""
        service=self.service
        line_id=args.get('line')
        if args.get('expected_version') in (None,''):raise el.LineRefusal('line_version_required')
        expected=el.whole_days(args.get('expected_version'))
        if expected is None:raise el.LineRefusal('line_version_required')
        rule=' '.join(service._redact_known_secrets(args.get('rule') or '').split())
        drop=el.whole_days(args.get('drop_rule'))
        changes={key:service._redact_known_secrets(args[key]) for key in ('task','report_promise','open_matters')
                 if isinstance(args.get(key),str) and args[key].strip()}
        if 'title' in args:
            changes['title']=service._redact_known_secrets(el.normalize_title(args['title']))
        days=el.whole_days(args.get('account_every_days'))
        current=service.lines.current(line_id) if isinstance(line_id,str) and line_id else None
        if current is None:raise el.LineRefusal('line_not_found')
        if current['state']!=el.STATE_ACTIVE:raise el.LineRefusal('line_stopped')
        if args.get('drop_rule') not in (None,'') and not (drop and 1<=drop<=len(current['rules'])):
            raise el.LineRefusal('line_invalid_rules')
        if not (rule or drop or changes or days is not None):raise el.LineRefusal('line_nothing_to_change')
        ruled=bool(rule or drop or set(changes)-{'open_matters'} or days is not None)
        with service.store.db() as db:
            entrusted=bool(continuous.active(db,line_id,service.preparations.clock()))
        if authority=='steer' and not ruled and self.line_run_row(job) and not entrusted:
            authority='self'  # #984: open matters need no steer authority for a qualifying line run.
        if authority=='self' and ruled:
            raise not_run(ToolError(msg('lines.refused_run_open_matters_only'),'unattended_run'))
        money=ruled and self.line_touches_money(line_id)
        marked=authority=='steer' and bool(service.store.outside_content_sources(job['id']))
        if entrusted and authority!='self' and not ruled:
            stop_request=service.decision_judge.explicit_line_change_request(
                service.owner_request_words(job),
                f"Close today's work on {el.line_name(current)!r} now and cancel its runs. Task: {current['task']}",
                self.line_owner_rules(current),service.preparation_conversation(job),work_id=job['id'])
            if stop_request.outcome==JUDGMENT_YES:
                return self._line_stop(job,{**args,'scope':'work'},confirmed=True)
        if money or marked or (entrusted and authority!='self'):
            change=el.change_text(current,rule=rule,drop=drop,changes=changes,days=days)
            judged=service.decision_judge.explicit_line_change_request(
                service.owner_request_words(job),change,self.line_owner_rules(current),service.preparation_conversation(job),work_id=job['id'])
            if judged.outcome!=JUDGMENT_YES:raise el.LineRefusal('line_change_needs_owner')
        now=service.preparations.clock()
        # A standing rule keeps the owner turn it came from: the typed Work, or the steer that asked.
        source=service.owner_steer_words(job)[-1][0] if authority=='steer' else job['id']
        def update(brief):
            rules=[item for number,item in enumerate(brief['rules'],1) if number!=drop]
            if rule:rules.append({'text':rule,'from':source,'at':now})
            return {**brief,**changes,'rules':rules,
                    **({'account_every_days':days} if days is not None else {})}
        # The plan version and before/after receipt commit together, even for withheld reports.
        own_row=self.own_running_row(job) if authority!='owner' and not ruled else None
        if authority!='owner' and not ruled and own_row is None:
            raise not_run(ToolError(msg('lines.refused_unattended_run'),'unattended_run'))
        with service.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            service.store.require_read_checkpoint_writer(db)
            before,after=service.lines.write(line_id,job['id'],expected,'rule' if rule else 'revised',update,db=db)
            if authority!='self' and after['version']!=before['version']:
                continuous.fence(db,line_id,now,work_id=job['id'])
            if own_row and after['version']!=before['version']:
                event={'target':'lines.open_matters','line_id':line_id,'version':after['version'],
                       'before':{'open_matters':before['open_matters']},
                       'after':{'open_matters':after['open_matters']},
                       'outside_content':bool(db.execute('SELECT 1 FROM work_intake_sources WHERE job_id=? LIMIT 1',
                                                         (job['id'],)).fetchone())}
                db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                           (job['id'],'line_self_change','succeeded',json.dumps(event),now))
        if days is not None and days!=before['account_every_days']:self.retime_line_account(before,after)
        changed=after['version']!=before['version']
        if authority!='owner' and changed and (self.line_version_read(job['id']) or (None,))[0]==line_id:
            # The run's own write is not a change made during the run for its next step (``line_note``).
            self._record_line_read(job['id'],after)
        # #1227: any change to the rules is told (a dropped rule too), once per owner Work.
        restated=changed and [item['text'] for item in before['rules']]!=[item['text'] for item in after['rules']]
        untold=self.line_rule_notice(job,line_id,after['version']) if restated else ''
        LOG.info('line revised id=%s version=%s work=%s rule=%s by=%s',line_id,after['version'],job['id'],restated,authority)
        result={**el.public_brief(after),'restated':restated,'money_rules':money,
                'next_step':self.told(msg('lines.revised_next' if restated else 'lines.revised_quiet_next'),untold)}
        if authority=='self' and changed:
            # #1228: the worker receives the receipt and undo version even when the report is withheld.
            result.update(open_matters_before=before['open_matters'], outside_content=event['outside_content'],
                          next_step=msg('lines.self_revised_next',version=after['version']))
        return result

    def _line_undo(self, job, args):
        version=el.whole_days(args.get('version'))
        if version is None:raise el.LineRefusal('line_version_required')
        after=self.undo_line_version(args.get('line'),version,job['id'])
        return {**el.public_brief(after),'next_step':msg('lines.undone_next')}

    def _line_stop(self, job, args, *, steered=False, confirmed=False):
        service=self.service
        line_id=args.get('line')
        scope=args.get('scope','work')
        if scope not in ('work','line'):raise el.LineRefusal('line_invalid_scope')
        current=service.lines.current(line_id) if isinstance(line_id,str) else None
        if current is None:raise el.LineRefusal('line_not_found')
        with service.store.db() as db:
            entrusted=bool(continuous.active(db,line_id,service.preparations.clock()))
        if not confirmed and ((steered and service.store.outside_content_sources(job['id'])) or entrusted):
            change=('end this entrusted mission and stop everything under it' if scope=='line' else
                    'stop the current work and pending runs while keeping this entrusted mission active')
            if not self.steer_asked(job,f"{change} on {el.line_name(current)!r}. Task: {current['task']}",
                                    self.line_owner_rules(current)):
                raise el.LineRefusal('line_change_needs_owner')
        after,changed=self.stop_line(line_id,job['id'],end_line=scope=='line')
        kind,next_key=(el.NOTICE_STOPPED,'lines.stopped_next') if scope=='line' else (
            el.NOTICE_WORK_STOPPED,'lines.work_stopped_next' if after['state']==el.STATE_ACTIVE else 'lines.refused_line_stopped')
        untold=self.line_notice(job,kind,line_id,after['version']) if changed else ''
        return {**el.public_brief(after),'scope':scope,'changed':changed,'next_step':self.told(msg(next_key),untold)}

    def _line_restart(self, job, args):
        service=self.service
        line_id=args.get('line')
        current=service.lines.current(line_id) if isinstance(line_id,str) and line_id else None
        if current is None:raise el.LineRefusal('line_not_found')
        after=service.lines.restart(line_id,job['id'])
        if service.lines.provenance(line_id).get('by')=='look-back':
            # The owner's act is an announcement even when the already-active brief did not change.
            with service.store.db() as db:
                db.execute('BEGIN IMMEDIATE')
                service.store.require_read_checkpoint_writer(db)
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
            pieces=self.line_pieces_account(row,job)
            if pieces:lead+='\n'+pieces
            check_counts=self.line_checks_account(row,job)
            if check_counts:lead+='\n'+check_counts
        else:
            lead=msg('lines.run_input')
            # #938 (D2): a run that may continue its line in pieces is told how, in one general sentence.
            if brief['state']==el.STATE_ACTIVE:
                if self.piece_parent(job) is not None:
                    lead+='\n'+msg('lines.piece_guidance',line=row['line_id'],minutes=prep.PIECE_LEAD_SECONDS//60)
                note=self.line_schedule_note(brief,row)
                if note:lead+='\n'+note
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

    def _record_line_read(self, work_id, brief, *, once=False, held=False):
        with self.service.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self.service.store.require_read_checkpoint_writer(db)
            if once and db.execute(
                    "SELECT 1 FROM tool_events WHERE job_id=? AND tool=? AND "
                    "COALESCE(json_extract(detail,'$.evidence.line_id'),json_extract(detail,'$.line_id'))=? LIMIT 1",
                    (work_id,el.READ_EVENT,brief['line_id'])).fetchone():return
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       # #802: as Evidence, so the information-use audit lists the line the run was given.
                       (work_id,el.READ_EVENT,'succeeded',json.dumps({'evidence':{'line_id':brief['line_id'],'version':brief['version'],**({'held':True} if held else {})}}),
                        self.service.preparations.clock()))

    def held_line(self, work_id):
        """#1169: the active line this Work itself read through ``entrusted_line``, or None.

        Only the model's own read counts, not a brief a run was handed; the line's subject is never read.
        """
        with self.service.store.db() as db:
            rows=db.execute('SELECT detail FROM tool_events WHERE job_id=? AND tool=? ORDER BY id DESC',(work_id,el.READ_EVENT)).fetchall()
        for row in rows:
            try:read=json.loads(row['detail']).get('evidence') or {}
            except (TypeError,ValueError,AttributeError):continue
            if not isinstance(read,dict):continue
            if read.get('held') and read.get('line_id'):
                brief=self.service.lines.readable(read['line_id'])
                if brief and brief['state']==el.STATE_ACTIVE:return read['line_id']
        return None

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
        """The one-line index of the owner's active lines for a Work the owner started, or '' (#798, #937).

        Each line's ref, task sentence and whether it has nothing scheduled: relevance is the AI's
        judgment, and it reads the brief and outcomes with ``entrusted_line`` read.  Since #937 it
        rides in the current context, so the plan and the outcome judgment read it too.  Look-back
        lines not yet announced are left out; stored secrets are redacted before any cut.
        """
        service=self.service
        if service.unattended_work(job):return ''
        try:
            active=[{**brief,'nothing_scheduled':not self.line_has_pending_work(brief['line_id'])}
                    for brief in service.lines.lines() if brief['state']==el.STATE_ACTIVE and not self.waiting_line(brief)]
            return el.held_text(active,service._redact_known_secrets)
        except Exception:
            LOG.info('lines index unavailable job=%s',job.get('id'))
            return ''

    def line_notice_text(self, notification):
        """The text of one queued line notice, or None when it no longer applies."""
        service=self.service
        kind=notification['kind']
        fingerprint=str(notification.get('fingerprint') or '')
        if kind.startswith(el.NOTICE_ENTRUSTED):
            parts=fingerprint.split(':',3)
            if len(parts)!=4 or not parts[2].isdigit():return None
            line_id,_revision,brief_version,end=parts
            brief=service.lines.version(line_id,brief_version)
            if brief is None:return None
            try:deadline=datetime.fromisoformat(end)
            except ValueError:return None
            zone=service.home_zone() or prep.offset_name(deadline)
            lang=owner_language(service.store,notification.get('job_id')) or 'ko'
            name=el.line_name(brief,service._redact_known_secrets)
            return msg('lines.entrusted',lang,title=name,end=prep.local_text(deadline.timestamp(),zone))
        line_id,_sep,version=fingerprint.partition(':')
        rule_notice=kind.startswith(el.NOTICE_RULE)
        versions=el.rule_notice_range(version) if rule_notice else None
        brief=service.lines.readable(line_id)
        if brief is None or not (versions if rule_notice else version.isdecimal()):return None
        name=el.line_name(brief,service._redact_known_secrets)
        if kind.startswith(el.NOTICE_OPENED):
            if brief['state']!=el.STATE_ACTIVE:return None
            provenance=service.lines.provenance(line_id)
            if provenance.get('by')=='look-back':
                return service._redact_known_secrets(msg('lines.stood_announced',task=name,promise=brief['report_promise'],
                                                        days=provenance['evidence']['days']))
            return service._redact_known_secrets(msg('lines.announced',task=name,promise=brief['report_promise']))
        if rule_notice:
            # #1227: what changed, never the whole list; told while each change is still in force
            # (a later change or an undo may have reverted it).  On a money line (C17 case 2) each
            # changed rule carries the owner's own words it came from.
            current={item['text'] for item in brief['rules']}
            # Each side is checked against the rules now in force, and only what still holds is told:
            # a replaced rule another Work put back is not told as removed (Codex review of #1232).
            held=lambda rule:rule if rule and rule['text'] in current else None
            gone=lambda rule:rule if rule and rule['text'] not in current else None
            changes=[(gone(before),held(after)) for before,after in self.rule_changes(line_id,*versions)]
            changes=[change for change in changes if change!=(None,None)]
            if not changes:return None
            money=self.line_touches_money(line_id)
            lang=owner_language(service.store,notification.get('job_id')) or 'ko'
            lines=[msg('lines.rules_changed',lang,title=name)]
            for before,after in changes:
                if before is None:
                    line=msg('lines.rule_added',lang,rule=self.rule_text(after,lang))
                elif after is None:
                    line=msg('lines.rule_dropped',lang,rule=self.rule_text(before,lang))
                else:
                    line=msg('lines.rule_replaced',lang,before=self.rule_text(before,lang),after=self.rule_text(after,lang))
                if money and after is not None:
                    line+=' '+msg('lines.your_words',lang,words=self.rule_words(after,lang))
                lines.append(line)
            return service._redact_known_secrets('\n'.join(lines))
        if kind.startswith(el.NOTICE_STOPPED):
            return service._redact_known_secrets(msg('lines.stopped',task=name))
        if kind.startswith(el.NOTICE_WORK_STOPPED):
            return service._redact_known_secrets(msg('lines.work_stopped',task=name))
        return None
