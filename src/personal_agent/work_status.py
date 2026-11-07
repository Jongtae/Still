"""Exact legacy Work-status questions (#664); these sets do not assign status.

Constant | Exact members | Question | Consumers
--- | --- | --- | ---
RESUMABLE_CONTINUATION | partial, failed | Can a stopped Work continue at a budget/login boundary? | owner_acts.py:settle; quickstart_service.py:_after_work; quickstart_service.py:_login_reopenable; quickstart_service.py:_offer_budget_continue; quickstart_service.py:_settle_browser_login; quickstart_service.py:budget_continue_current; quickstart_service.py:offer_budget_continue; quickstart_store.py:telegram_photo_terminal_cleanup (literal mirror)
RESUMABLE_SETTINGS | partial, failed, interrupted, awaiting_connection | Can setup recovery continue this Work? | quickstart_service.py:mcp_setup_recoverable
RESUMABLE_SETTINGS_SETTLEMENT | partial, failed, interrupted, succeeded, awaiting_connection | Can settled settings prerequisites resume this Work? | quickstart_service.py:resume_settings_work
RESUMABLE_ACTION | failed, partial, interrupted, succeeded | Can an action continuation requeue this Work? | quickstart_service.py:_queue_action_continuation
RESUMABLE_ACTION_DECISION | running, failed, partial, interrupted, succeeded | Can an action decision attach to this Work? | quickstart_service.py:_decide_browser_step.commit
RESUMABLE_LOGIN_EXPLICIT | failed, partial, succeeded | Can an explicit login request continue this Work? | quickstart_service.py:_settle_browser_login
RESUMABLE_INTERRUPTED | partial, failed, interrupted | Can recovery update the stopped Work cause? | quickstart_service.py:resume_action_work
INCOMPLETE_RESULT | failed, partial | Did the Work return an incomplete result for judgment or look-back? | agent_runtime.py:withheld_effect; agent_runtime.py:evidence_qualifiers; owner_model.py:turn_outcome; quickstart_service.py:orchestration_step; work_status.py:ended_abnormally (an unresumed owner wait, #690)
DELIVERED_RESULT | succeeded, partial | Did the Work deliver a usable result? | preparations.py:run_outcome; preparations.py:settle; quickstart.py:do_GET; quickstart_service.py:_owner_model_run; quickstart_service.py:fold_delivered_steer; quickstart_service.py:owner_model_eligible; quickstart_service.py:run_due_preparation; quickstart_store.py:__init__; quickstart_store.py:save_workspace_result
AVAILABLE_RESULT | succeeded, partial, failed | Is a result available for inspection or look-back? | owner_model.py:dream_works; quickstart_service.py:task_progress
REVIEWABLE_RESULT | succeeded, partial, failed, unknown | Can the Work be self-reviewed? | owner_model.py:REVIEWABLE_STATUSES; quickstart_service.py:_owner_model_run
ABNORMAL_END | failed, interrupted | Did execution fail or get interrupted (or, #690, end partial on an owner wait nothing resumes)? | quickstart_service.py:reply_controls; quickstart_service.py:safe_retry
NEEDS_ATTENTION | failed, partial, interrupted, unknown | Does the ended Work need attention? | quickstart_service.py:_progress_status; quickstart_service.py:task_progress
OBSERVED_STOP | failed, interrupted, cancelled | Is a stopped route outcome observable? | quickstart_service.py:_observed_route.outcome
STEER_FAILED | failed, interrupted, cancelled, unsupported | Did the steered Work fail to apply the steer? | quickstart_service.py:steer_disposition_notice
TERMINAL | succeeded, failed, partial, cancelled, interrupted, unknown, blocked | Has the Work finished for native handoff/photo cleanup? | owner_forget.py:FINISHED_WORK; owner_forget.py:_exclude_message; quickstart_service.py:settle_native_handoffs; quickstart_store.py:telegram_photo_terminal_cleanup (literal mirror)
TERMINAL_OUTCOME | succeeded, partial, failed, unknown, cancelled, interrupted | Has the Work decided a Telegram outcome? | telegram_presence.py:TERMINAL_OUTCOMES; telegram_presence.py:outcome_reaction
ACTIVE | queued, running | Is the Work queued or executing? | agent_runtime.py:legacy_work_sources; agent_runtime.py:_change; agent_runtime.py:_migrate_request; api_requests.py:_effect_guard; owner_model.py:claim_due; owner_model.py:enqueue_dream_if_due; portable_state.py:_portable_db; quickstart_service.py:_claim_queued_work; quickstart_service.py:_observed_route; quickstart_service.py:_progress_status; quickstart_service.py:_route_work; quickstart_service.py:cancel_focused_work; quickstart_service.py:home; quickstart_service.py:keep_sessions_alive; quickstart_service.py:_observed_route.outcome; quickstart_service.py:process_browser_logins; quickstart_service.py:resume_settings_work; quickstart_service.py:start_session_keepalive; quickstart_service.py:task_card_markup; quickstart_store.py:enqueue; telegram_surface.py:ingest_stop; terminal_surface.py:waiting_since
ACTIVE_PREPARATION | queued, running, awaiting_connection | Is the preparation run still active? | preparations.py:due; preparations.py:settle
ACTIVE_APPROVAL | queued, running, awaiting_approval | Is the Work active for context retention? | context_observations.py:_drop_finished_exposures
ACTIVE_PARKED | awaiting_connection, awaiting_drive, awaiting_context, queued, running | Is the Work active or parked for owner input? | quickstart_service.py:AgentService.PARKED_STATUSES; quickstart_service.py:remember_judged_intent
WAITING_PREREQUISITE | awaiting_context, awaiting_drive, awaiting_connection | Is the Work waiting for a prerequisite? | quickstart_service.py:_progress_status
WAITING_CONNECTION_DRIVE | awaiting_connection, awaiting_drive | Is the Work waiting for connection or Drive? | quickstart_service.py:_withdraw_disconnected_work
QUEUED_CONNECTION | awaiting_connection, queued | Can connection recovery update this queued/waiting Work? | owner_forget.py:_exclude_trust; quickstart_service.py:cancel_focused_work
QUEUED_CONTEXT | queued, awaiting_context | Can a context callback update the request? | telegram_surface.py:ingest_update
REFUSABLE_PREPARATION | queued, awaiting_connection, failed, partial, interrupted, succeeded | Can an expired preparation run be refused? | preparations.py:refuse_run
SUCCEEDED | succeeded | Was the request fully met? | quickstart_service.py:_after_work; quickstart_service.py:_progress_status; quickstart_service.py:mcp_setup_guard; quickstart_service.py:settings_prerequisites_observed; telegram_presence.py:outcome_reaction
FAILED | failed | Did the Work fail (self-review or usage-limit recovery)? | quickstart_service.py:resume_after_document_approval; quickstart_service.py:resume_ready_native_work; quickstart_service.py:_validate_usage_limit_recovery; quickstart_service.py:self_review_eligible; quickstart_service.py:usage_limit_route_options
CANCELLED | cancelled | Was the Work cancelled? | quickstart_service.py:_progress_status
EFFECT_UNKNOWN | unknown | Did the Work end with an unknown effect? | conversation_projection.py:terminal_text; quickstart_service.py:_steer_continuation_refusal; quickstart_service.py:orchestration_step; quickstart_service.py:steer_disposition_notice
AWAITING_CONNECTION | awaiting_connection | Can native connection recovery handle this Work? | quickstart_service.py:resume_ready_native_work; quickstart_service.py:settle_native_handoffs
INCOMPLETE_ANSWER | failed, partial, interrupted | Did the Work end with an incomplete answer? | conversation_projection.py:terminal_text
UNFINISHED_PORTION | partial, interrupted | Can the reply describe a verified portion after stopping? | conversation_projection.py:terminal_text
UNSPECIFIED_OR_SUCCEEDED | None, succeeded | May the reply use normal AI-answer rendering? | conversation_projection.py:terminal_text
INSPECTABLE_ANSWER | succeeded, partial, failed, interrupted, unknown | Can the owner inspect this finished answer and its information use? | quickstart_service.py:INFORMATION_USE_STATES; quickstart_service.py:information_use_tool.read
UNVERIFIED_RESULT | failed, partial, interrupted, unknown | Must stored assistant text carry an unverified-result qualifier? | conversation_projection.py:UNVERIFIED_OUTCOMES; conversation_projection.py:turn_qualifier; conversation_projection.py:context_message

Stored facts (#632 phase 2)
--------------------------
Goal ``reached`` / ``not_reached``: the outcome judgment said the reply served /
did not serve the request. ``owner_needed``: a recorded owner prerequisite
(approval, sign-in, draft or missing connection) stopped the Work. ``not_judged``:
a judgment was due but unavailable, the budget/deadline ended, or the worker failed.
``none``: no judgment applies (an unjudged direct fallback, a CLI Work without
orchestration, or a failure before judgment without orchestration).
Effect ``unknown``: a call has no definite answer, as in ``_work_has_unknown_effect``.
``withheld``: the #630 success floor (``goal_upgrade_allowed``) refuses for the run
that sets the status: one of its calls fell short (failed write, withheld
draft/proposal, incomplete change) or asked for the owner's sign-in, or a
browser-step approval or sign-in is still pending now.
``applied``: a call changed something outside the owner. ``none``: no call did.
Precedence: unknown > withheld > applied > none; only Still's own records count.
Both NULL: legacy, or a status written outside a worker run's end (cancel before
running, restart interruption, parking, built-in answer or peer Work). Readers
interpret NULL as "fact not recorded; read the status as before".

"""

RESUMABLE_CONTINUATION = frozenset(('partial', 'failed'))
RESUMABLE_SETTINGS = frozenset(('partial', 'failed', 'interrupted', 'awaiting_connection'))
RESUMABLE_SETTINGS_SETTLEMENT = frozenset(('partial', 'failed', 'interrupted', 'succeeded', 'awaiting_connection'))
RESUMABLE_ACTION = frozenset(('failed', 'partial', 'interrupted', 'succeeded'))
RESUMABLE_ACTION_DECISION = frozenset(('running', 'failed', 'partial', 'interrupted', 'succeeded'))
RESUMABLE_LOGIN_EXPLICIT = frozenset(('failed', 'partial', 'succeeded'))
RESUMABLE_INTERRUPTED = frozenset(('partial', 'failed', 'interrupted'))
INCOMPLETE_RESULT = frozenset(('failed', 'partial'))
DELIVERED_RESULT = frozenset(('succeeded', 'partial'))
AVAILABLE_RESULT = frozenset(('succeeded', 'partial', 'failed'))
REVIEWABLE_RESULT = frozenset(('succeeded', 'partial', 'failed', 'unknown'))
ABNORMAL_END = frozenset(('failed', 'interrupted'))
NEEDS_ATTENTION = frozenset(('failed', 'partial', 'interrupted', 'unknown'))
OBSERVED_STOP = frozenset(('failed', 'interrupted', 'cancelled'))
STEER_FAILED = frozenset(('failed', 'interrupted', 'cancelled', 'unsupported'))
TERMINAL = frozenset(('succeeded', 'failed', 'partial', 'cancelled', 'interrupted', 'unknown', 'blocked'))
TERMINAL_OUTCOME = frozenset(('succeeded', 'partial', 'failed', 'unknown', 'cancelled', 'interrupted'))
ACTIVE = frozenset(('queued', 'running'))
ACTIVE_PREPARATION = frozenset(('queued', 'running', 'awaiting_connection'))
ACTIVE_APPROVAL = frozenset(('queued', 'running', 'awaiting_approval'))
ACTIVE_PARKED = frozenset(('awaiting_connection', 'awaiting_drive', 'awaiting_context', 'queued', 'running'))
WAITING_PREREQUISITE = frozenset(('awaiting_context', 'awaiting_drive', 'awaiting_connection'))
WAITING_CONNECTION_DRIVE = frozenset(('awaiting_connection', 'awaiting_drive'))
QUEUED_CONNECTION = frozenset(('awaiting_connection', 'queued'))
QUEUED_CONTEXT = frozenset(('queued', 'awaiting_context'))
REFUSABLE_PREPARATION = frozenset(('queued', 'awaiting_connection', 'failed', 'partial', 'interrupted', 'succeeded'))
SUCCEEDED = frozenset(('succeeded',))
FAILED = frozenset(('failed',))
CANCELLED = frozenset(('cancelled',))
EFFECT_UNKNOWN = frozenset(('unknown',))
AWAITING_CONNECTION = frozenset(('awaiting_connection',))
INCOMPLETE_ANSWER = frozenset(('failed', 'partial', 'interrupted'))
UNFINISHED_PORTION = frozenset(('partial', 'interrupted'))
UNSPECIFIED_OR_SUCCEEDED = frozenset((None, 'succeeded'))
INSPECTABLE_ANSWER = frozenset(('succeeded', 'partial', 'failed', 'interrupted', 'unknown'))
UNVERIFIED_RESULT = frozenset(('failed', 'partial', 'interrupted', 'unknown'))

GOAL_REACHED, GOAL_NOT_REACHED, GOAL_OWNER_NEEDED, GOAL_NOT_JUDGED, GOAL_NONE = (
    'reached', 'not_reached', 'owner_needed', 'not_judged', 'none')
GOAL_OUTCOMES = frozenset((GOAL_REACHED, GOAL_NOT_REACHED, GOAL_OWNER_NEEDED, GOAL_NOT_JUDGED, GOAL_NONE))
EFFECT_NONE, EFFECT_APPLIED, EFFECT_WITHHELD, EFFECT_UNKNOWN_STATE = 'none', 'applied', 'withheld', 'unknown'
EFFECT_STATES = frozenset((EFFECT_NONE, EFFECT_APPLIED, EFFECT_WITHHELD, EFFECT_UNKNOWN_STATE))

def goal_outcome_of(terminal, judgment=None, orchestrated=False):
    """The terminal judgment, independent of the recorded effect floor (#632)."""
    if orchestrated and terminal is not None and terminal=='owner_needed':return GOAL_OWNER_NEEDED
    if judgment=='yes':return GOAL_REACHED
    if judgment=='no':return GOAL_NOT_REACHED
    if judgment=='unavailable':return GOAL_NOT_JUDGED
    if orchestrated and terminal is not None:
        if terminal=='reached':return GOAL_REACHED
        if terminal=='not_reached':return GOAL_NOT_REACHED
        return GOAL_NOT_JUDGED
    return GOAL_NONE

def effect_state_of(unknown, withheld, applied):
    """The strongest fact Still's own call records show (#632)."""
    return EFFECT_UNKNOWN_STATE if unknown else EFFECT_WITHHELD if withheld else EFFECT_APPLIED if applied else EFFECT_NONE

def facts_recorded(goal_outcome, effect_state):
    """Whether both facts were recorded, rather than a legacy status alone."""
    return goal_outcome is not None and effect_state is not None

def is_resumable_continuation(status: str | None) -> bool:
    """Can a stopped Work continue at a budget/login boundary?"""
    return status in RESUMABLE_CONTINUATION

def is_resumable_settings(status: str | None) -> bool:
    """Can setup recovery continue this Work?"""
    return status in RESUMABLE_SETTINGS

def is_resumable_settings_settlement(status: str | None) -> bool:
    """Can settled settings prerequisites resume this Work?"""
    return status in RESUMABLE_SETTINGS_SETTLEMENT

def is_resumable_action(status: str | None) -> bool:
    """Can an action continuation requeue this Work?"""
    return status in RESUMABLE_ACTION

def is_resumable_action_decision(status: str | None) -> bool:
    """Can an action decision attach to this Work?"""
    return status in RESUMABLE_ACTION_DECISION

def is_resumable_login_explicit(status: str | None) -> bool:
    """Can an explicit login request continue this Work?"""
    return status in RESUMABLE_LOGIN_EXPLICIT

def is_resumable_interrupted(status: str | None) -> bool:
    """Can recovery update the stopped Work cause?"""
    return status in RESUMABLE_INTERRUPTED

def has_incomplete_result(status: str | None) -> bool:
    """Did the Work return an incomplete result for judgment or look-back?"""
    return status in INCOMPLETE_RESULT

def has_delivered_result(status: str | None) -> bool:
    """Did the Work deliver a usable result?"""
    return status in DELIVERED_RESULT

def has_available_result(status: str | None) -> bool:
    """Is a result available for inspection or look-back?"""
    return status in AVAILABLE_RESULT

def has_reviewable_result(status: str | None) -> bool:
    """Can the Work be self-reviewed?"""
    return status in REVIEWABLE_RESULT

def ended_abnormally(status: str | None, unresumed_owner_wait: bool = False) -> bool:
    """Did execution fail or get interrupted?

    The retry consumers' status rule (``safe_retry``, ``reply_controls``).  #690: a Work that waits
    for the owner ends ``partial``, not ``failed``; when it ended on such a wait that no automatic
    path resumes (``unresumed_owner_wait``: not a sign-in the in-flow login re-queues, not a Work
    parked for a connection or a folder, which is not ended), it may be retried as a failed one
    could, so the status change never loses the owner's way back.  Every other retry guard
    (unknown or unaccounted effects, a mutation, unsafe replay inputs) still applies.
    """
    return status in ABNORMAL_END or (unresumed_owner_wait and status in INCOMPLETE_RESULT)

def needs_attention(status: str | None) -> bool:
    """Does the ended Work need attention?"""
    return status in NEEDS_ATTENTION

def has_observed_stop(status: str | None) -> bool:
    """Is a stopped route outcome observable?"""
    return status in OBSERVED_STOP

def has_failed_steer(status: str | None) -> bool:
    """Did the steered Work fail to apply the steer?"""
    return status in STEER_FAILED

def is_terminal(status: str | None) -> bool:
    """Has the Work finished for native handoff/photo cleanup?"""
    return status in TERMINAL

def is_terminal_outcome(status: str | None) -> bool:
    """Has the Work decided a Telegram outcome?"""
    return status in TERMINAL_OUTCOME

def is_active(status: str | None) -> bool:
    """Is the Work queued or executing?"""
    return status in ACTIVE

def is_active_preparation(status: str | None) -> bool:
    """Is the preparation run still active?"""
    return status in ACTIVE_PREPARATION

def is_active_approval(status: str | None) -> bool:
    """Is the Work active for context retention?"""
    return status in ACTIVE_APPROVAL

def is_active_parked(status: str | None) -> bool:
    """Is the Work active or parked for owner input?"""
    return status in ACTIVE_PARKED

def is_waiting_prerequisite(status: str | None) -> bool:
    """Is the Work waiting for a prerequisite?"""
    return status in WAITING_PREREQUISITE

def is_waiting_connection_drive(status: str | None) -> bool:
    """Is the Work waiting for connection or Drive?"""
    return status in WAITING_CONNECTION_DRIVE

def is_queued_connection(status: str | None) -> bool:
    """Can connection recovery update this queued/waiting Work?"""
    return status in QUEUED_CONNECTION

def is_queued_context(status: str | None) -> bool:
    """Can a context callback update the request?"""
    return status in QUEUED_CONTEXT

def is_refusable_preparation(status: str | None) -> bool:
    """Can an expired preparation run be refused?"""
    return status in REFUSABLE_PREPARATION

def is_succeeded(status: str | None) -> bool:
    """Was the request fully met?"""
    return status in SUCCEEDED

def is_failed(status: str | None) -> bool:
    """Did the Work fail (self-review or usage-limit recovery)?"""
    return status in FAILED

def is_cancelled(status: str | None) -> bool:
    """Was the Work cancelled?"""
    return status in CANCELLED

def is_effect_unknown(status: str | None) -> bool:
    """Did the Work end with an unknown effect?"""
    return status in EFFECT_UNKNOWN

def is_awaiting_connection(status: str | None) -> bool:
    """Can native connection recovery handle this Work?"""
    return status in AWAITING_CONNECTION



def has_incomplete_answer(status: str | None) -> bool:
    """Did the Work end with an incomplete answer?"""
    return status in INCOMPLETE_ANSWER



def has_unfinished_portion(status: str | None) -> bool:
    """Can the reply describe a verified portion after stopping?"""
    return status in UNFINISHED_PORTION



def is_unspecified_or_succeeded(status: str | None) -> bool:
    """May the reply use normal AI-answer rendering?"""
    return status in UNSPECIFIED_OR_SUCCEEDED


def has_inspectable_answer(status: str | None) -> bool:
    """Can the owner inspect this finished answer and its information use?"""
    return status in INSPECTABLE_ANSWER


def has_unverified_result(status: str | None) -> bool:
    """Must stored assistant text carry an unverified-result qualifier?"""
    return status in UNVERIFIED_RESULT


def sql_in(statuses: frozenset[str]) -> str:
    """Render trusted status constants as a stable SQL IN list (never owner input)."""
    return '('+','.join("'"+status.replace("'", "''")+"'" for status in sorted(statuses))+')'
