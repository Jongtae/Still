# Work recovery matrix and compatibility spike

## Status

Design and fixture evidence for [#826](https://github.com/Jongtae/Still/issues/826), part of program [#822](https://github.com/Jongtae/Still/issues/822) (frontier requirement F03, recovery). It records what the code on `main` at `be1323a` persists and decides when Still stops at each of four failure-injection points, for each Work state. It does not claim a generic restart engine, and it changes no runtime code. The fixtures in `tests/test_recovery_matrix_826.py` drive the current entry points for the rows; a row Still does not handle today is an expected failure or a skip with its reason.

Evidence classes in this document:

- **code**: read from the source at the cited symbol; not executed.
- **fixture**: exercised by a test in `tests/test_recovery_matrix_826.py` or an existing test named in the row.
- **inferred**: derived from several code sites; no test executes it yet.

No live owner account, model or provider was used.

## Scope

**States.** `queued`, `running`, `waiting` (an owner wait: approval, sign-in, settings draft, location, connection, budget-continue), `interrupted`, `unknown` (an effect or a delivery whose outcome Still cannot know) and `delivery-pending` (a reply or notice not yet confirmed sent).

**Injection points.**

| Point | Still stops… |
|---|---|
| **P1** before dispatch | after the Work, step or continuation is admitted and persisted, before the external operation starts |
| **P2** after dispatch, before receipt | after the operation (worker turn, tool call, API or connector write, settings apply, location request, message send) has started, before its result is recorded |
| **P3** after receipt, before settlement | after the result is recorded, before the Work, wait, draft or ledger state is advanced |
| **P4** after delivery, before acknowledgement | after a reply, notice or prompt reached the owner surface, before Still records it as delivered, or after the owner's answer arrived, before it is settled |

**Cross-cutting conditions.** Old rows, expired windows, owner-accepted schedules against self-initiated runs, stale workers, cancellation and forgetting, and budget replenishment are rows X1–X12.

## What restart does today

On start, `AgentService.start` runs, in this order (code):

1. `a2a_peers.recover`: open peer turns end as `failed` (`peer_interrupted`), never replayed.
2. `recover_interrupted_work`, in three separate commits: it reads the running surface Works that have no task card (`running_without_task_cards`); it calls `QuickStore.recover`, whose one transaction sets `jobs.status running → interrupted` with the fixed error 실행 중 재시작되었습니다. 자동으로 재호출하지 않습니다, `jobs.delivery sending → unknown`, `telegram_notifications.state sending → unknown`, legacy task cards `message_id -1 → -2`, and counts into `config.last_recovery`; then it sets `delivery='pending'` on those Works if they are `interrupted` with `delivery='none'`, so one interrupted reply is sent. A stop between the second and third commit loses that reply (row X9).
3. `purge_forgotten`, then the one-time `WorkingNotes.migrate`.
4. `decision_routes.recover_qualification`: a running Judgment AI qualification is requeued once or fails `interrupted`.
5. `settings_orchestrator.reconcile`: a draft cut off in `applying` is settled `unknown` (audit reason `interrupted`) and never re-applied.
6. `retire_unattended_waits`: waits that scheduled runs left are withdrawn with an `action_continuation` `withheld` receipt, reason `unattended-run` (#619).

Each loop tick then runs `run_due_preparation`, `run_due_qualification`, `run_one` (which first calls `settle_native_handoffs`, `resume_ready_native_work`, `resume_action_work`, `resume_settings_work`), `deliver_one`, `deliver_notification`, `expire_memory_prompts`, `release_memory_saved_notices` and `process_browser_logins`. The reconcilers are level-triggered: they decide from stored rows and are safe to repeat.

`recover` reads no receipt. It does not set `jobs.status='unknown'` and does not touch `tool_events` or the WorkLedger row. Whether an interrupted Work's effect is unknown is recomputed later from `tool_events` by `continuation_reasons` and `safe_retry`.

### Authoritative inputs

| Input | Writer | Read by |
|---|---|---|
| `jobs.status`, `jobs.delivery`, `jobs.request_key`, `relation_kind`, `related_job_id` | `QuickStore.enqueue`, `_claim_queued_work`, `_finish_work`, `_fail_work`, `QuickStore.recover`, the continuation CAS updates | every reconciler |
| `tool_events` dispatch markers: a `running` row with a `call_id` (direct route, CLI MCP bridge), `subscription_engine` `running`, `api_dispatch` `dispatching` (stored under the request root's Work id), `connector_permission` `claiming` | `agent_runtime` tool dispatch, `mcp_bridge`, `work_executor`, `ApiRequests`, connector permission | `continuation_reasons` (`unknown`, `unaccounted`, `effect`), `safe_retry`, `ApiRequests._effect_guard` |
| `tool_events` continuation receipts: `action_continuation`, `settings_continuation` (`queued` / `withheld`, `offer_digest`, `draft_ids`) | `_queue_action_continuation`, `resume_settings_work`, `retire_unattended_waits` | the same paths, for exactly-once admission |
| WorkLedger `config['work_budget:<root>']`: counters, `deadline`, `active`, `remaining_seconds`, `epoch`, `active_work_id` | `WorkLedger._change` under `BEGIN IMMEDIATE`, `renew_for_owner_prerequisite` | every spend (`refusal`), `pause`, `remaining`, the bridge's `WorkBudget` |
| Owner waits: `BROWSER_REQUESTS_KEY`, `browser_login_requests`, `settings_change_drafts`, `context_location_requests`, `budget_continue:<id>`, `PENDING_WORK_KEY` | their services | `resume_action_work`, `process_browser_logins`, `resume_settings_work`, `continue_located_work`, `budget_continue_decision`, `resume_ready_native_work` |
| Settings and input receipts: `setup_receipt`, `api_receipt`, `owner_state_receipts` | `settings_continuation`, `api_input`, `owner_forget` | `resume_settings_work`, `api_input.reconcile`, forget restore |
| `preparations.state`, `last_run_job_id`, window end | `preparations` | `run_refusal` at claim and at every continuation |
| `telegram_notifications(state, generation, fingerprint)`, `telegram_turns.reply_message_id`, surface cursors | surfaces | button handlers (require `state='sent'`), `notice_binding`, reaction lookup |
| `WORK_STOP_KEY`, `forgotten_messages`, owner `generation` | stop, forget, pairing | `work_stopped`, `_claim_queued_work`, `deliver_one` |

### Transactions

`QuickStore.db()` commits once per `with` block; atomicity across writes exists only inside one block, taken with `BEGIN IMMEDIATE` where it matters. These receipt-and-settlement pairs share one transaction (code): action continuation receipt with `jobs → queued`; settings continuation receipt with `jobs → queued`; MCP `setup_receipt` with draft `applied` and the trust record; API input receipt with slot publication; an approval answer with the continuation; a location answer with request `consumed`, the continuation Work and the Telegram cursor; preparation start with its Work; a Work's terminal status with its reply and `delivery='pending'`; a forget receipt with its exclusion and the Works it cancels.

These are separate commits: the three steps of `recover_interrupted_work`; a generic settings setter and its `_settle('applied')`; `api_dispatch` `dispatching` and its `_settle_effect`; on Telegram, `delivery='sending'`, then `sent`, then `record_reply` (the Mattermost and Slack surfaces write `sent` and the reply post id together); `_finish_work`, the ledger `pause()` and `_after_work` (which queues confirmation, approval and proposal prompts); a budget spend (committed before the tool runs) and the tool's receipt; a withdrawn wait (`resume_action_work`, `retire_unattended_waits`) and its `withheld` receipt or `owner_cause`; the Telegram callback's effect and the cursor advance; the Mattermost cursor and the reactions and steers it covers; the Slack press key and the answer it starts.

### Facts common to each state

The rows below vary by injection point. These facts hold for every row of a state.

| State | Eligible restart point | Owner cancel / forget | Authority revision checked on resume | WorkLedger epoch / deadline |
|---|---|---|---|---|
| queued | The claim (`_claim_queued_work`) | Cancel by CAS while `queued`; a forgotten request message cancels at claim | Scheduled run: `run_refusal` (window, line, `last_run_job_id`); settings continuation: generation, trust epoch, digest at admission; owner generation only at delivery | A fresh Work has no row yet; a continuation inherits the request root's row |
| running | None: becomes `interrupted` | Running Works are not cancellable; a stop (`WORK_STOP_KEY`) refuses the next spend | Not applicable until resumed | Row stays `active` with its epoch and absolute deadline |
| waiting | The wait's own reconciler (`resume_action_work`, `resume_settings_work`, `continue_located_work`, `resume_ready_native_work`, `process_browser_logins`) | A stop withdraws the wait with a receipt; a trust forget cancels drafts and waiting Works | Request `generation` and expiry, `offer_digest`, draft digest, trust epoch, connector authority, location `context_epoch` | Paused (`active=False`) when the run released it; `renew_for_owner_prerequisite` renews an inactive row once per counted continuation |
| interrupted | An owner act: a continuation CAS (`RESUMABLE_ACTION`, `RESUMABLE_SETTINGS_SETTLEMENT`) or `safe_retry` (new Work id) | `cancelled` is never resumable; a forgotten message cancels the re-queued Work at claim | As for the owner act that resumes it | Same-id resume reuses the active row: no epoch advance, original deadline (a resume after `WORK_DEADLINE_SECONDS`, 1500 s, from the original start is refused at once with `deadline_exceeded`); a retry gets a new id and advances the epoch |
| unknown | None automatic | Not resumable | Not applicable | Unchanged |
| delivery-pending | `deliver_one` / `deliver_notification` | A cancelled or work-mode row is cancelled, never sent | Owner generation and `notice_binding` at send | Not used |

Each row's **Effect** column states the known effect at that point: *none* (nothing dispatched), *known* (a trustworthy receipt exists), *unknown* (dispatched, no receipt) or *delivery* (the effect is only the message to the owner).

## Matrix

Columns: **Inputs** are the authoritative facts the decision reads. **Effect** is the known effect (see above). **Decision** is what current code does on restart. **Visible** is what the owner sees. **Support**: *supported* (the decision is justified and no effect is duplicated or lost), *conservative* (safe, but reports more uncertainty than exists or drops progress), *unsupported* (an effect, an owner act or a reply can be lost, or a stale process can act). **Migration**: *none* (the row stays as it is), *next* (changed by the selected next change below), *follow-up* (named unsupported path, not selected).

### Queued

| Row | Point | Inputs | Effect | Decision | Visible | Support | Migration |
|---|---|---|---|---|---|---|---|
| Q1 | P1 | `jobs.status='queued'`, `request_key` (unique), `forgotten_messages`, `run_refusal` for a scheduled run, active parent in the request chain | none | `recover` leaves it. `_claim_queued_work` claims it once under `BEGIN IMMEDIATE`; a forgotten request message cancels it (`error='forgotten'`); an ended scheduled window, inactive line or replaced run fails it with `owner_cause`. No age limit and no owner-generation check at claim. | The normal reply; a stale-generation reply is cancelled at delivery. | supported | none |
| Q2 | P2 | For a continuation: the parent attempt's dispatch markers, read by `continuation_reasons(…,'resume')` at admission | unknown or known, from the parent | A continuation is admitted only after the one continuation rule: `unknown`/`effect` block admission for guarded actions (`REQUEST_APPROVAL_ACTIONS`, `browser_sign_in`, `budget_continue`), `limit` blocks every action. A fresh Work has nothing dispatched. | `quickstart_service.previous_effect_unknown` or `resume_limit` when withheld. | supported | none |
| Q3 | P3 | The continuation receipt and `jobs → queued` in one transaction | known | No half state exists. A second admission of the same `offer_digest` is refused by the `resumed` receipt. | Nothing extra. | supported | none |
| Q4 | P4 | Telegram `update_id` cursor committed with the Work; the 👀 acknowledgement is in memory only | delivery | A typed message is ingested exactly once. The acknowledgement is re-applied when the Work runs. | Nothing extra. | supported | none |

### Running

| Row | Point | Inputs | Effect | Decision | Visible | Support | Migration |
|---|---|---|---|---|---|---|---|
| R1 | P1 | `jobs.status='running'`, no dispatch marker in `tool_events` | none | `recover` sets `interrupted`; nothing is re-run. Claimed-but-not-dispatched is not distinguishable from mid-call, but with no dispatch marker `safe_retry` allows the owner's retry. | One interrupted reply (`projection.this_request_was_interrupted_i_did`); 다시 시도 when `safe_retry` allows. | supported | none |
| R2 | P2 | A `running` call with a `call_id` and no terminal record; `api_dispatch dispatching`; `connector_permission claiming`; `subscription_engine running`; the `ENGINE_UNMEDIATED` source label | unknown | `interrupted`; no re-dispatch. `continuation_reasons` returns `unknown` for a cut-off call or an unsettled claim; for `resume` only, it returns `unaccounted` for an engine turn with no accounting. `safe_retry` refuses on `unknown` (`retry_unknown_result`) and, through a separate source check, on a trusted-local engine run (`retry_unmediated_action`); a bare `subscription_engine running` record with no other blocker does not refuse a retry. `ApiRequests` holds the slot and any slot on the same host (`effect_unknown`) and refuses the same `input_digest` (`api_effect_replayed`). | Interrupted reply without 다시 시도 when refused; `service.the_previous_request_s_external_outcome` on a follow-up. | supported | none |
| R3 | P3 | Terminal `tool_events` receipts; the worker's reply text only in memory until `_finish_work` | known | `interrupted`; the reply text is lost. The recorded receipts are not used to settle the Work; they are read later by `continuation_reasons`, where a `changed` call blocks a guarded resume (`effect`). | Interrupted reply; the answer the worker produced is not shown. | conservative | follow-up (F-R3) |
| R4 | P4 | A prompt row (`approval_needed`, `browser_approval_needed`, others) `sending` | delivery | The row becomes `unknown`. Every Telegram button handler requires `state='sent'`, so the buttons are dead: a tap gets an empty callback answer and the buttons are removed. The wait itself survives: the web step decision checks the request, not the notice, and settings drafts stay answerable in Settings. | Counts only, on the web home (`quickstart_service.work_delivery_uncertain`); nothing in the conversation. | unsupported | follow-up (F-P4) |

### Waiting

| Row | Point | Inputs | Effect | Decision | Visible | Support | Migration |
|---|---|---|---|---|---|---|---|
| W1 | P1 | Step request `requested`/`issued` with `requested_at`/`issued_at`, `generation`, `offer_digest`; login requests; settings drafts; location request `pending` with `expires` and `context_epoch`; `budget_continue:<id>` with nonce and ledger epoch | none | `resume_action_work` withdraws a stopped, expired (`PAYMENT_APPROVAL_SECONDS`, 600 s) or owner-revoked request, then writes its `withheld` receipt and `owner_cause` in a later commit, and queues an `issued` one once. `process_browser_logins` expires a login offered before the restart. Location answers are consumed only while `pending`, unexpired and in the same epoch. The windows are absolute: downtime counts against them. | `quickstart_service.resume_authority_expired` as the Work's cause; a later sign-in page asks again. A stop between the withdrawal and its receipt leaves the wait withdrawn with no cause (row X10). | supported | none |
| W2 | P2 | Settings draft `applying` with `applying_at`; MCP setup `connection_setup running` with `remote_receipt:'unknown'`; API input `applying` without `api_receipt`; location request sent | unknown | Settings: `reconcile` settles `unknown`, never re-applies, also when the setter never ran (over-reports doubt). MCP setup: observe-only recovery (no register or login), at most 3 attempts within 3600 s. API input with no receipt: `expired`. Location: the request stays `pending` for its 900 s TTL; the cut-off `ask_location` call counts as `unknown`. | `settings_orchestrator.in_doubt` (check the current value). | conservative | none |
| W3 | P3 | MCP `setup_receipt` + draft `applied` (one transaction); `api_receipt` + slot publish (one transaction); generic setter committed, `_settle` not run; approval answer + continuation (one transaction); location observation + `consumed` + continuation + cursor (one transaction) | known, except the generic setter (unknown) | MCP: `resume_settings_work` queues the same Work once without repeating setup. API input: `applying → unknown → applied` once `ready()` passes. Generic setter: settled `unknown`; the current value is not read back, and the preparation receipt text (held in memory) is lost. Approval and location: no half state. | Generic setter: `settings_orchestrator.in_doubt`. Others: the resumed Work's reply. | supported, except the generic setter (conservative) | follow-up (F-W3) |
| W4 | P4 | Prompt rows (`approval_needed`, `browser_approval_needed`, `budget_continue`, `browser_login_needed`, `settings_change_proposed`, `preparation_proposed`) `sending`; Telegram callback cursor; Mattermost `cursor`/`reaction_cursor`; Slack press key | delivery | A `sending` prompt becomes `unknown` and its Telegram buttons are dead (as R4) until a new nonce rebinds it. Telegram callbacks are applied before the cursor advances, in a separate commit: a replay after restart is refused by state, so they are at-most-once in effect. Mattermost writes its cursor before applying the reactions and steers it covers, and Slack records the press before the answer starts: a stop between them loses the owner's act or correction, and the prompt stays `sent`. A Telegram notice that raised `TelegramNotSent` (definitely not sent) is set `unknown`, never retried; Mattermost and Slack requeue it. | Dead Telegram buttons; on Mattermost and Slack the owner's act has no effect and no notice. | unsupported | follow-up (F-P4, F-W4) |

### Interrupted

| Row | Point | Inputs | Effect | Decision | Visible | Support | Migration |
|---|---|---|---|---|---|---|---|
| I1 | P1 | `status='interrupted'`, the owner's later act (approval tap, settings confirmation, retry) | none | Stays `interrupted` until the owner acts. The only re-queue paths are the continuation CAS updates (`RESUMABLE_ACTION`, `RESUMABLE_SETTINGS_SETTLEMENT`) and `safe_retry`; each re-checks the one continuation rule and `run_refusal`. | 상세 always; 다시 시도 when `safe_retry` allows. A same-id resume more than 1500 s after the original start ends at once with `deadline_exceeded`. | supported | none |
| I2 | P2 | Cut-off calls of the interrupted attempt | unknown | `safe_retry` refuses on `unknown`. A step-approval resume of a non-guarded action is blocked only by `limit`: by the current #627 design the effect reasons hold only guarded actions, and the approval is for that exact step. | Interrupted reply; withheld cause when blocked. | supported (current contract) | none |
| I3 | P3 | Preparation `running` with `last_run_job_id` of an interrupted Work | known | `due()` surfaces it once delivery is no longer pending or sending; `settle` ends it `failed`; the slot is not re-run and the next slot proceeds. | The scheduled run's failure report. | supported | none |
| I4 | P4 | Interrupted reply `sending`; on Telegram, `telegram_turns.reply_message_id` written only after a confirmed `sent` | delivery | `unknown`, never resent. With no recorded reply id, 다시 시도/상세 from that message find no turn. | Counts on the web; the controls on that message do nothing. | conservative | follow-up (F-D3) |

### Unknown

| Row | Point | Inputs | Effect | Decision | Visible | Support | Migration |
|---|---|---|---|---|---|---|---|
| U1 | P1 | `jobs.status='unknown'` (written by `_finish_work`/`_fail_work`/`derive_status`, never by `recover`) | unknown | Not in any resumable set, and `safe_retry` refuses it, so nothing re-queues it. `continuation_reasons` itself returns no reason for a Work with no effect events: the hold is the status, not the continuation rule. | `state.unknown`. | supported | none |
| U2 | P2 | An unknown write followed by a successful read in the same Work | unknown | A later read never upgrades a write: `recovered()` accepts only effect-free reads as recovered; the write stays unknown. | The Work states the unknown effect. | supported | none |
| U3 | P3 | `api_dispatch dispatching` with no settlement; settings `unknown` | unknown | No sweeper settles an old `dispatching` claim from a trustworthy receipt; it stays `unknown` and holds the slot and host. Only API input (`api_receipt` + `ready()`) and MCP setup (observe-only) reconcile an unknown from a receipt. | `effect_unknown` refusals; `settings_orchestrator.in_doubt`. | conservative | follow-up (F-U3) |
| U4 | P4 | `delivery='unknown'`, notification `unknown` | delivery | Never resent; retry refused (`retry_delivery_unknown`). | Counts on the web home and in onboarding; nothing in the conversation. The counts are never cleared. | conservative | follow-up (F-P4) |

### Delivery-pending

| Row | Point | Inputs | Effect | Decision | Visible | Support | Migration |
|---|---|---|---|---|---|---|---|
| D1 | P1 | `jobs.delivery='pending'` (set with the terminal status); notification `queued` with `generation`, `fingerprint` | none | Sent once after restart; `sending` is written before the send. A stale generation, work mode or a changed `notice_binding` cancels it. No age limit. | The reply, possibly late. | supported | none |
| D2 | P2 | `delivery='sending'` | delivery | `unknown`, never resent; at-most-once. | `service.telegram_delivery_could_not_be_verified`, `quickstart_service.work_delivery_uncertain`. | supported | none |
| D3 | P3 | Telegram: delivery `sent` written, `record_reply` (the reply anchor) not written | delivery | Telegram only (Mattermost and Slack write both together): the anchor is lost, so a reaction or reply-control on that message cannot find its Work. `_after_work` prompts queued after `_finish_work` can also be lost (no sweeper). | Controls and reactions on that message do nothing. | unsupported | follow-up (F-D3) |
| D4 | P4 | Sent, `sent` not recorded | delivery | `unknown`: a delivered reply is counted as not verified. | Uncertain-delivery count. | conservative | none |

### Cross-cutting

| Row | Condition | Inputs | Decision | Visible | Support | Migration |
|---|---|---|---|---|---|---|
| X1 | Old rows | `ALTER TABLE … ADD COLUMN` guarded by `PRAGMA table_info`; WorkLedger `_validate` defaults missing counters and `epoch=0`, `_migrate_request` folds per-Work legacy budgets once; legacy task cards; login rows with no window id expire | Old rows read with defaults; nothing is replayed from them. | As for current rows. | supported | none |
| X2 | Expired windows | Approval 600 s, location 900 s, budget-continue 24 h, settings `expires_at`, preparation window via `run_refusal`, WorkLedger absolute deadline (1500 s) | Expired waits are withdrawn with receipts or refused at claim. Downtime counts against every window. | `resume_authority_expired`, `deadline_exceeded`, a scheduled run's refusal cause. | supported | none |
| X3 | Accepted schedules and self-initiated runs | `preparations` row and `unattended_work`; `run_refusal` | Today both follow #619: a scheduled run raises no approval, step approval or settings draft; earlier waits are withdrawn at start. Payment follows the payment-autonomy contract. #821 C1 changes the owner-accepted case only (see *Reconciliation*). | The run's report; no prompt. | supported (current contract) | none here; #821 |
| X4 | Stale workers | WorkLedger `epoch`, `active`, `active_work_id`; the bridge's `WorkLedger(store, job_id)` binds the epoch current when it starts; `_work_running` checks only `status='running'`; the CLI child runs in its own session and only a live host kills its process group | A run of a different Work in the request chain (retry, location continuation) or the resume of a paused ledger advances the epoch, and the old run's spends return `superseded`. After a crash the ledger stays `active` with its epoch. When the same Work id is resumed (an action or settings continuation; `fresh=True`, same `active_work_id`, active) the epoch does not advance, so a bridge bound before the restart is not superseded once the Work is `running` again. Calls it relays to the service already fail (the relay directory and key are per turn), but tools that run inside the bridge (reads, search, documents) can spend the resumed run's allowance. A refused bridge call, for any reason, records a `failed` row (`run_superseded`, `ran: false`) in the Work and still claims the owner's pending steers (`_with_steer`, fixture), so a stale bridge can take a correction meant for the resumed run; its rows fall inside that run's window and can suppress its Continue offer (inferred). Reaching this needs a CLI that outlived Still and an owner continuation of the same Work id within its deadline (inferred: narrow, most likely after an upgrade restart during a running Work). | Nothing; a correction can silently miss the resumed run. | unsupported | **next** |
| X5 | Cancel, stop, forget | `status='cancelled'`, `WORK_STOP_KEY` (survives restart), `forgotten_messages`, trust forget | `cancelled` is in no resumable set. A forgotten request cancels a queued Work at claim; a trust forget cancels drafts and waiting Works in its transaction; a stop is honoured at the first spend and by every reconciler. | The Work stays cancelled or stopped. | supported | none |
| X6 | Budget replenishment | `MAX_CONTINUATIONS=8` counted from durable `queued` receipts across the request chain; `renew_for_owner_prerequisite` only for an inactive ledger | A crash does not reset counters or the continuation count. Because the crash leaves the ledger `active`, a prerequisite continuation after a crash is not renewed. | `resume_limit` at the count. | supported | none |
| X7 | Late terminal write | `_finish_work`/`_fail_work` write `WHERE id=?` without a status compare | A same-process late writer overwrites `cancelled` or `interrupted` (fixture). After a real process death the writer is gone. | The later status. | conservative | follow-up (F-X7) |
| X8 | Trusted-local CLI actions | `ENGINE_UNMEDIATED` source label; the engine turn's accounting record | Actions a trusted-local CLI ran itself are never accounted. `safe_retry` refuses through the source check (`retry_unmediated_action`); `resume` is held by `unaccounted` when the engine turn's record lacks accounting. The label alone adds no continuation reason. Outside Still's control by design. | Interrupted or failed reply without 다시 시도. | supported (bounded by design) | none |
| X9 | Recovery's own commits | `recover_interrupted_work`'s three commits | A stop after `QuickStore.recover` commits and before `delivery='pending'` leaves the Work `interrupted` with `delivery='none'`; the next start selects only `running` Works, so its interrupted reply is never sent. | Nothing in the conversation for that Work. | unsupported | next |
| X10 | Withdrawal and receipt | `resume_action_work` and `retire_unattended_waits` withdraw a wait, then write its `withheld` receipt or `owner_cause` in a later commit | A stop between them leaves the wait withdrawn with no receipt or cause. Nothing is replayed. | The Work shows no reason. | conservative | follow-up (F-X10) |
| X11 | Finished before pause | `_finish_work` commits; `pause()` runs later in `_settle_work_judgments` | A stop between them leaves a terminal Work whose ledger is still `active`: a later prerequisite continuation is not renewed and no budget-continue can be offered (the offer needs an inactive ledger). `recover` touches only `running` Works, so the fence does not cover it. | A later continuation may stop at its old deadline. | conservative | follow-up (F-X11) |
| X12 | Other Work kinds | Judgment AI qualification jobs, owner-model upkeep rows, A2A peer turns | Qualification: a running job is requeued once, then fails `interrupted`. Upkeep: a `claimed` row from a crash expires as `interrupted` after 3600 s and is never re-run; `pending` rows are claimed later. Peers: open turns fail `peer_interrupted`, never replayed, and a cut-off exchange reads `peer_exchange_failed`, losing its unknown wording. | As each kind reports. | supported | none |

## Unsupported paths

| Id | Rows | Path |
|---|---|---|
| F-X4 | X4, X9 | A process from before the restart can spend, take the owner's steers and write records for a Work resumed under the same id; and a stop inside recovery can lose the interrupted reply. **Selected next change.** |
| F-P4 | R4, W4, U4 | A prompt cut off in `sending` becomes `unknown` and its Telegram buttons are dead; the owner is told only by a web count, never in the conversation, and the counts are never cleared. |
| F-W4 | W4 | Mattermost's cursor before reactions and steers, and Slack's press before answer, can lose an owner act or correction; a Telegram notice that is definitely not sent is never retried. |
| F-D3 | I4, D3 | On Telegram, `sent` without `record_reply` loses the reply anchor; `_after_work` prompts have no sweeper. |
| F-R3 | R3 | The worker's reply is not durable before `_finish_work`. |
| F-W3 | W3 | A generic setter cut off after its commit is reported in doubt without reading the current value back. |
| F-U3 | U3 | An `api_dispatch dispatching` claim is never reconciled from a later trustworthy receipt. |
| F-X7 | X7 | Terminal `jobs` writes carry no status compare. |
| F-X10 | X10 | A withdrawn wait's receipt is a separate commit. |
| F-X11 | X11 | A finished Work's ledger pause is a separate commit. |

Smaller observations, not separately tracked: queued Works and pending replies have no age limit; claim does not check the owner generation (the reply is cancelled at delivery instead, after a model call); a settings draft cut off in `applying` before its setter ran is reported in doubt.

## Existing solutions review (Adopt / Adapt / Build)

**Decision: Adopt** stdlib `sqlite3` transactional receipts through the existing `QuickStore.db()` / `BEGIN IMMEDIATE` path, which already follows the pattern (one transaction for receipt and state, a reservation before dispatch, `sending → unknown`). No new dependency.

| Candidate | What it offers at P1–P4 | Fit with Still | Licence, maintenance |
|---|---|---|---|
| SQLite transactional receipts (outbox/claim row, idempotency key, receipt and state in one transaction) | P1 a reserved claim; P2 the claim stays and becomes unknown, never replayed; P3 one transaction removes the gap; P4 a `sending` marker | Single owner, local, no server; works at Still's crash boundary (its own process and the CLI child); already in `ApiRequests` (claim + `input_digest`), settings/MCP continuation, `api_input`, `owner_forget`, `WorkLedger._change`. Additive `ADD COLUMN` migrations keep older releases readable. | Python stdlib (PSF), SQLite (public domain); no supply-chain cost |
| [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence) (checkpointers, threads, interrupts) | Checkpoints between nodes. On resume a node restarts from its beginning; the [interrupts guide](https://docs.langchain.com/oss/python/langgraph/interrupts) warns that side effects before `interrupt` repeat. | A CLI turn is one node: resuming it would replay actions Still did not observe, against the unknown/unaccounted rule. Its checkpoint tables cannot commit with QuickStore rows, so P3 stays open, and its thread state would be a second store for Work (C1). Legacy Works have no thread. | MIT; `langgraph` and `langgraph-checkpoint-sqlite` are active, but bring `langchain-core`, `langsmith`, `ormsgpack`, `aiosqlite`, `sqlite-vec` |
| [Temporal activities](https://docs.temporal.io/activity-definition) | At-least-once activities with retries, heartbeats and timeouts; the docs require idempotent activities | Retries are the opposite of "an unknown effect is never replayed"; with one attempt the benefit is gone and Still's receipt is still needed. Requires a Temporal server process; workflow history would become a second store for Work; every running or waiting Work would need re-hosting. | MIT; `temporalio` active; native core and a server binary |

Why the rejected candidates are insufficient: both re-execute the unit of work at Still's crash boundary, neither can commit its checkpoint in the same transaction as Still's Work, Event and receipt rows, and both add a second source of truth for Work state. Neither provides a capability the selected change needs. No framework migration is proposed.

## Selected next change

**Restart supersedes the pre-restart run (F-X4, with X9).** One bounded runtime change, filed as its own issue once #821 C1 and #818/#819 have merged: it edits `QuickStore.recover` (lane C's file while #821 is open) and the bridge's refused-call path (lane A's error results). It removes a blocker to correct recovery and scripts no request (C16).

**Behavior.**

1. *Fence.* Inside `QuickStore.recover`'s own transaction, for every Work it moves from `running` to `interrupted`, advance that Work's WorkLedger epoch by one, at the request-chain root `_change` uses. Counters, `deadline`, `active` and `remaining_seconds` stay as they are, so no allowance is replenished, `renew_for_owner_prerequisite` behaves as before (the row stays active), and an interrupted run keeps its original deadline. A Work with no ledger row gets none. A request chain that does not resolve, or an unreadable row, skips the fence for that Work and does not abort recovery: the Work is still `interrupted`, and an unreadable row still fails every spend closed. The fence must sit in `recover`'s transaction, not in `recover_interrupted_work`, or a stop between the two leaves an interrupted Work unfenced for good. `quickstart_store` does not import `agent_runtime`; the helper is imported lazily or lives beside the store.
2. *Recovery's reply in the same commit.* The `delivery='pending'` step for running surface Works with no task card moves into the same transaction (X9), so a stop inside recovery cannot lose the interrupted reply.
3. *A refused bridge call takes no steer.* A bridge call refused because the Work is not running or its ledger is superseded is already recorded as not run (`run_superseded`, `ran: false`; fixture). It must also not claim the owner's pending steers: today `_with_steer` runs on every refused call and hands the correction to the stale worker (fixture).

**Why this one.** It is the only matrix path where a process from before the restart can still act after the owner resumes the Work: spend the resumed run's allowance, take a correction meant for it, and write records that suppress its Continue offer. That contradicts #826's "stale epochs cannot revive work or replenish budget" and its user outcome ("no lost correction"). It is small, needs no schema change and has a single owner of each edited function. Its reach is narrow (inferred): a CLI that outlived Still plus an owner continuation of the same Work id within the deadline, most likely after an upgrade restart during a running Work.

**Migration.** None: the ledger row is JSON in `config` and `epoch` already exists (legacy rows default to 0). No column, table or message key.

**Rollback.** An older release reads the advanced epoch as an ordinary integer and the moved `pending` update as an ordinary column value; the only loss is the fence and the bridge refusal.

**Tests (unit, temporary store; the X4 and X9 strict expected failures in `tests/test_recovery_matrix_826.py` become passing tests).**

1. A ledger bound as `mcp_bridge.serve` binds it (no `fresh`) before a simulated restart returns `superseded` after `recover`, and still after the same Work id is resumed through an action continuation and a fresh host run opens. The new host run spends normally.
2. Counters, `deadline` and `active` are unchanged by `recover`; `MAX_CONTINUATIONS` and `renew_for_owner_prerequisite` behave as before; a budget-continue offer bound to the new epoch is honoured.
3. Queued, waiting and terminal Works, and Works with no ledger row, are untouched.
4. A chain that does not resolve and a corrupt row: recovery completes, the Work is `interrupted`, spends fail closed.
5. Two restarts: each advances only the Works it interrupted.
6. A stop injected after the status change inside recovery leaves nothing half done: either neither the status nor `pending` is written, or both are.
7. A stale bridge call claims no steer (the steer stays undelivered for the resumed run), and the resumed run's Continue offer survives.

**Escalation.** The change tightens a stale-worker boundary and widens nothing; its PR assesses whether the Constitution's escalated review applies.

**Next after it.** F-W4 and F-P4: owner acts and corrections lost on Mattermost and Slack, and prompts left dead with only a web count. They lose owner input more often than F-X4 (inferred), but they change surfaces lanes A and C are editing now.

## Reconciliation

- **#821 and PR #816.** PR #816 applies #619 at the in-flow login: a scheduled run records no login row. #821 C1 lets an owner-accepted scheduled run ask, bound to the accepted preparation's identity and version, and continue through the existing continuation. The matrix rows that change under #821: X3 (accepted runs may hold an owner wait), W1/W4 for those waits, and `retire_unattended_waits`, which must stop withdrawing an accepted run's waits. Self-initiated runs (look back, daily plan) keep X3 as written. The F-X4 change is independent of #821's behavior: it touches only the ledger epoch and recovery reply of Works being interrupted and the bridge's refused calls, and applies equally to scheduled and typed Works; it shares `quickstart_store.py` with lane C, so it is filed after #821 C1 merges.
- **#722.** Judgments made from CLI-bridge tool calls are not yet tied to their Work for audit, budget and stop. F-X4 bounds what a stale bridge can spend and stops it taking steers; it does not bind judgments, which stays #722.
- **#813 and #814.** A skipped boundary check losing a promised interval report (#813) and the 96-check cap ending a watch window early (#814) are watch-scheduling defects in the preparations path, not restart rows. Their invariant for this matrix: a restart must not create, drop or re-run a watch slot. Today I3 and X2 hold that: an interrupted slot is settled `failed` and the next slot proceeds.

## Fixtures

`tests/test_recovery_matrix_826.py` names each test after its row (`test_q1_…`, `test_x4_…`). Rows marked *unsupported* are `pytest.mark.xfail(strict=True)` with the row and the missing behavior as the reason, so they fail the suite if the behavior changes without the matrix being updated. A strict expected failure is paired with a passing test of today's persisted state, so each gap is demonstrated rather than assumed. A row that could not be driven without new seams would be skipped with its reason; none needed it. The existing tests cited by the sweeps, for example `test_quickstart.py::QuickstartTests::test_idempotent_requests_and_interrupted_recovery`, `test_connect_effects.py::EffectBoundary::test_restart_after_dispatch_cannot_replay_an_unobserved_effect`, `test_connect_api_effects.py`, `test_connect_mcp_continuation.py::McpContinuation::test_restart_after_atomic_commit_recovers_same_work_without_registration`, `test_owner_settings_conversation.py::ReviewRemediation::test_a_draft_cut_off_mid_apply_by_a_restart_is_in_doubt_not_failed` and `test_budget_continue.py::BudgetContinueTests::test_eight_resumes_and_old_ledger_epoch_remain_bounded`, remain the primary evidence for the supported rows; the new fixtures add the rows they do not reach.
