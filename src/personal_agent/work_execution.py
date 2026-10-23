"""The state one Work execution carries between its phases (#21 slice 1).

``AgentService._run_one`` claims one queued Work and runs it through named
phases, each an ``AgentService`` method that reads and writes this object:

1. ``_route_work``: the owner-turn judgments (the owner's answer to an open
   item, #172; continuity, retry/cancel) and the routing decision, before any
   capability.
2. ``_load_work_photos``: the Telegram photos the Work carries.
3. ``_run_rule_intent``: an intent Still answers itself; anything else is left
   to the worker (``handled`` stays false).
4. ``_prepare_worker_turn``: the worker's history, splices, event recorder and
   the orchestration plan.
5. ``_run_worker_attempts``: the orchestrated attempts (``_run_cli_attempt`` or
   ``_run_api_attempt``), re-delegated within the Work's bounds.
6. ``_settle_worker_outcome``: the outcome after the last attempt.
7. ``_finish_work`` or, on a failure, ``_fail_work``; then always
   ``_settle_work_judgments`` and ``_after_work``.

A phase returns ``PROCEED`` to continue; any other value ends the run and is
what ``_run_one`` returns, exactly where the single method returned before.
An attempt returns ``REDELEGATED`` when the orchestration chose another
attempt after a failure.  This is a plain record, not a framework: authority,
approvals, budgets and recovery stay in the service methods the phases call.
"""
from .agent_runtime import OWNER_CONVERSATION

#: A phase finished and the run continues with the next one.
PROCEED = object()
#: An attempt failed and the orchestration chose another attempt.
REDELEGATED = object()


class WorkRun:
    """The mutable state of one Work execution, shared by its phases."""

    def __init__(self, job, claimed_at, retry_note=None):
        self.job = job
        self.claimed_at = claimed_at
        self.response = ''
        self.provider = 'builtin'
        self.model = 'notes'
        self.outcome = 'succeeded'
        # #981: a CLI attempt may ask the owner to sign in; other routes
        # still reach the shared completion path without entering that branch.
        self.waits_for_sign_in = False
        self.resolved_blocker = False
        self.approval_needed = [False]
        self.context_approval_needed = [False]
        self.refusals = []
        self.owner_steps = []
        # #14: the last event id before the current attempt (None: the whole Work), for ``unrecovered_refusals``.
        self.attempt_start = None
        self.verified_parts = []
        #: #657: the direct route's typed requested/observed/failed/unknown/next report.
        self.agency_report = None
        #: The effect owner's own statement when this Work's consequential
        #: effect could not be observed (#598 I1).
        self.unknown_statement = None
        self.calendar_notice = ''
        #: #730 review: the factual note a refused retry's worker reads first.
        self.retry_note = retry_note
        # #605: the sources that enter this Work's context, recorded with
        # its reply.  Each branch adds a source *before* reading it.
        self.work_sources = {OWNER_CONVERSATION}
        self.work_capabilities = [None]
        self.work_budget = None
        self.early_intent = None
        # Set by ``_route_work``.
        self.prompt = None
        #: The Work whose message is ``prompt`` (a retry replays another's).
        self.prompt_work_id = None
        self.connector_owner = None
        self.owner = None
        self.photo_file_ids = None
        self.image_inputs = None
        self.continued = None
        self.continuity = None
        #: #172: the owner's judged answer to one open item (``owner_acts.read``), or None.
        self.owner_act = None
        #: What that settlement tells the owner when it was the whole turn; None when the turn goes on.
        self.act_reply = None
        #: The Work a settled settings answer keeps in focus.
        self.act_focus = None
        #: What the worker reads first about what Still already settled from this message.
        self.act_note = None
        self.owner_memory_request = None
        self.decision = None
        self.rule_note = None
        # Set by ``_run_rule_intent``.
        self.handled = None
        self.fallthrough_note = None
        self.orchestration = None
        self.result = None
        #: #211: each engine's cloud provider profile, resolved once per Work (None: the subscription).
        self.cloud_profiles = {}
        # Set by ``_prepare_worker_turn``.
        self.config = None
        self.key = None
        self.route_snapshot = None
        self.stored_history = None
        self.document_jobs = None
        self.document_history = None
        self.history = None
        self.history_rows = None
        self.turn_provenance = None
        self.workspace_request = None
        self.context_sources = None
        self.prepared_latest = None
        self.record = None
        self.section_values = None
        self.skill_binding = None
        self.owner_information = None
        self.base_history = None
        self.base_rows = None
        self.base_config = None
        self.base_key = None
        self.attempt = None
        # Set per attempt by ``_run_worker_attempts`` and the attempt it runs.
        self.subscription = None
        self.attempt_test = None
        self.brief = None
        self.unmediated_turn = False
        self.engine_meta = None
        self.work_private = None
        self.boundary = None
        self.capabilities = None
