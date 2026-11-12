# Still Secretary Agency Contract

## Current rule index and precedence

This contract defines owner-centered, goal-directed work and the pilot's policy boundaries. “Secretary” is a historical program name and a starting role, not a restriction of Still to one profession. [Professional Roles and Communication](professional-roles-and-communication-contract.en.md) defines the broader role/mandate distinction. A contract is not a claim of shipped or live-verified capability.

Current selection belongs to root [delivery-plan.yaml](../delivery-plan.yaml); GitHub owns execution and review status. Nothing on this page activates a program. This 2026-11-05 consolidation uses `6edc3380b613ab0c2897ba7a913ab658150a68f4`; the [original amendment sequence](https://github.com/Jongtae/Still/blob/6edc3380b613ab0c2897ba7a913ab658150a68f4/docs/secretary-agency-contract.en.md) remains available there.

| Concern | Current controlling source |
| --- | --- |
| Owner-AI and third-party Memory writes; upkeep, dream and working notes | [Memory and learning contract](engineering/contracts/memory.en.md): #918 direct save, C5 candidate boundary, #220/#367 lifecycle, #19 versioned notes, #422 quiet consolidation and later source refinements |
| Meaning, route planning and outcome judgment | [Decision Layer](decision-layer.en.md); judgment never creates authority or external-effect truth |
| Work, effects, budgets and recovery | [Assistant Execution](assistant-execution-contract.en.md) and current code/tests |
| Private context, public lookup and audit | [Pilot posture](#pilot-posture) and [information-use audit](#information-use-audit), consolidating #826 and later audit fixes |
| Connections, browser mediation and peer delegation | [Connection and Delegation](connection-delegation-contract.en.md); platform coverage is separately recorded |
| Accepted reminders, preparations and watches | [Attention](#attention-in-this-program), existing preparation state and the owner's acceptance |
| Owner-facing communication and intervention | [Presence](presence-experience-contract.en.md) and the roles/communication contract |

Where older Owner Control or Assistant Execution clauses describe restrictions explicitly removed for the pilot, the pilot posture governs that scope. Other authority, secret, owner-state, exact-effect and recovery invariants remain in force. The two source/text discrepancies found in dream timing and daily-record size are disclosed in the memory contract; this edit does not settle them by changing runtime or policy.

## Status and purpose

The original SECRETARY-01 activation on 2026-09-26 was a dated owner decision, not today's selection. The source [secretary study](research/secretary-agency-spec-2026-09-26.ko.md) remains a research record. The [historical delivery/resume contract](owner-model-phase3-delivery.en.md) retains its own scope; this reorganization neither resumes it nor declares its closeout complete.

## Why the program exists

The original problem was a system narrowed to guarded tool calls instead of useful owner outcomes. The durable aim is to use available capabilities, consider alternatives, recover within authority and report what was actually achieved. The model supplies semantic judgment; deterministic code preserves state and exact boundaries. No scenario-specific branch is justified merely because it makes a demonstration pass.

## Product definition

Still serves one owner across conversations, tasks, models and capabilities. Owner facts, current context, permissions, results and open obligations remain independent of the selected worker. Secretary work is one professional perspective among others. A role or mandate is not a permission grant, professional licence or claim that every domain is supported.

The original program considered personal context/memory, anticipatory preparation, goal-directed action, collaboration with another owner's agent and replaceable capabilities. These are design concerns, not a declaration that all six historical program axes have shipped. For the implemented/released distinction, use [product status](product-status.en.md).

### Cross-Work Memory recall (OWNER-MEMORY-01 #862)

The [memory contract](engineering/contracts/memory.en.md#9-recall-explanation-and-inspection) owns bounded snapshots, owner-scoped search, source/time attribution and the rebuildable index. Temporary situations remain current context rather than automatic durable profile facts.

## The agency loop

### Roles

The Judgment AI chooses a qualified worker/model and brief, evaluates the result and may re-plan within the Work's limits. The execution AI chooses and uses the tools available to its route. Still checks exact Grants, secrets, approvals, destinations, budgets and effect state, executes permitted calls and retains observations. A model's goal-reached verdict cannot turn an unobserved external effect into a confirmed result.

The #826 amendment removed the plan-selected tool subset and pinned default worker. Do not recreate `tools_mode`, `tools` or `tools_reason` as current requirements from old #710 prose. Each attempt receives the worker's tools already eligible under policy. Use the Decision Layer for the exact current interface, unavailable-route behavior and re-delegation bounds; this contract does not define a second orchestrator.

### Alternatives are mandatory

When a path fails to provide the needed result, consider another query, source, supported route or a decisive question to the owner. Do not repeat an identical unsuccessful attempt. Alternatives remain bounded by cancellation, time/cost limits, authority and external-effect uncertainty. No recovery rule permits replay of an uncertain effect or an action outside the owner's scope.

### Evidence-based completion

The result must be supported at the level claimed. A product match needs identity evidence; a reported cart addition needs observed cart state; a reminder needs a durable record with the intended schedule; a prepared recommendation needs attributable inputs and the owner's relevant constraints. “No error was raised” and model confidence are not completion evidence.

Requested, attempted, observed, partial, failed and unknown remain distinct. A partial result names what is confirmed, what remains and the next possible action. The final answer must not outrank the underlying outcome record.

### Failure modes

| Situation | Required response |
| --- | --- |
| Login needed | Use the dedicated owner sign-in path, never ask the model to enter credentials; continue unrelated permitted work where useful. |
| Page error or unexpected structure | The original recovery guidance was a bounded retry, inspection of the intended target, then another route. It never permits an unchecked target, duplicate effect or a task-specific core branch. |
| Irrelevant search results | Revise the query or source rather than repeating the same unsuccessful input and provider. |
| Action outside scope | Stop that action, state the missing permission or unsupported boundary, and preserve any independently useful result. |
| Partial or unknown effect | Report the observed state; verify before considering another external attempt. |

## Pilot posture

The pilot serves one owner on the owner's machine and accounts. The following distinctions consolidate the later explicit decisions, rather than restating the original 2026-09-26 restrictions as current policy.

**Secrets and payments.** Passwords, tokens, cookies and API keys must not enter a model prompt, log or Evidence. The owner signs in through the dedicated surface; the model receives mediated content and results, not session material. Every payment requires the applicable per-action approval by default; an owner-turned-on payment autonomy mode replaces it while on (#662). Non-payment mutations on connected services follow the current pilot and route-specific policy, not a blanket rule that every write asks again.

**Owner state and scope.** Folder grants and owner isolation still bound reads and writes. Installing, discovering or configuring a route does not create action authority. Owner-AI Memory saves and third-party candidates have different rules in the memory contract. A route's remaining exact approvals and capability limits are not removed by summarizing the pilot as ask-less.

**Private context and lookup (#826).** Private reads and the connected CLI's native web search may share a turn. Owner context in an earlier attempt does not disable that search, and the plan does not select a private/no-search subset. Still's own search tools step aside when the CLI uses its native search. Still-composed public lookups may carry owner values; the removed private-value/sensitivity gates must not be described as if they still block them. Stored secrets remain redacted. Data use is made inspectable through the information-use audit.

This is not a blanket assertion that every document path bypasses an existing sharing grant. Respect the current authority checks of the actual route. Local installation is not local-only model processing, and Still does not control every operation inside a connected external service.

Per-query sensitivity judgments, routine re-asks for ordinary lookup terms, the original research-only no-cart restriction and additional per-provider decisions after the owner configured that provider were removed for the pilot. Additional hardening remains separately selected work; its deferral is not proof that the corresponding threats are solved.

## Information-use audit

The audit is derived from existing `turn_provenance`, `tool_events` and `decision_audit`, not a second truth store. It identifies owner-information references, Memory/profile keys, calendar/file/context sources, included conversation counts, workers/routes/models, judgment calls, search and observed tool results. Store bounded labels and references after secret redaction, not unrestricted input or payload text. The owner can inspect it on task detail, at `GET /api/tasks/<id>/information-use`, or through the read-only `information_use` tool.

The #167/#233/#245 outbound comparison is **detection and display only**. It does not block, rewrite or delay the outgoing call. After a mediated public lookup, browser input/navigation, API request or permitted code-run request, compare bounded outbound text with current owner Memory and the Work's held private writes. The reviewed bounds are the first 1,000 text characters and newest 500 Memory rows. Report a truncated comparison explicitly.

Record matches as source/tool/destination labels, not the matched value or full text. A failed attempt handed to a transport is still an attempt; a pre-send refusal records `ran: false`. Preserve redirect destinations and the weather geocoding destination where applicable. An uncertain transport outcome must not become a confirmed delivery claim.

The existing matcher has known false matches; its Unicode/digit/case refinements and fixture count remain in code/tests. The audit says content *matches* a held value, not that a semantic disclosure was proved. Mail, file, Drive and calendar contents are not added to this comparison. Native CLI search does not pass through this comparison and must be disclosed as such. Blocking such disclosures remains a separate owner decision, not a feature introduced by this document.

## Capabilities

Capabilities are bounded interfaces with replaceable implementations, selected by model judgment. A declaration alone does not prove readiness or live execution.

| Area | Current reading |
| --- | --- |
| Search and page reading | Connected-AI native search is the default route; supported optional search sources and public-page reads retain their own configuration, scope and evidence. Historical provider-selection prose is not a current legal/availability review. |
| Browser | macOS uses system WebKit. Newer Linux desktop source uses pinned Chromium with a mandatory sandbox and platform keyring; see [platform coverage](guide/platforms.en.md). Do not carry forward the old macOS-only/no-download statement as a universal current rule. |
| Google, files and other services | Use the actual connected grant and route. [Connections](guide/connections.en.md) distinguishes read, modify/filter and Calendar event changes, and release coverage. |
| Memory and context | Existing stores and the memory/current-context contracts; no additional graph or canonical store is implied. |
| Peer delegation | Use the current [Connection and Delegation Contract](connection-delegation-contract.en.md). Do not use the historical statement that no A2A adapter exists as a description of the reviewed main source. |

**Browser output mediation.** Password, credential, one-time-code and card fields are excluded from returned content; saved-secret redaction also applies to page text. Raw DOM/storage and session material are not exposed as model or Evidence payloads. Keep the negative fixture proving that a rendered token does not leak. This is deterministic secret mediation, not a semantic decision that a request is harmless.

**Payment guard.** A worker's effect label may add an approval requirement, never remove one. Protected payment fields/form submissions and the existing deterministic purchase/payment floor require exact bound approval. #619 removed the extra per-press Judgment AI question, not that floor. A page confirmation during a step is not accepted as payment without the owner's approval of that press; outside a step nothing is auto-confirmed, and the owner answers in their own sign-in window. Declined dialogs are returned to the worker. The guard is not a universal detector of every payment button or stored-payment flow; retain the explicit limitation rather than claiming complete website coverage.

For the supported CLI relay, Memory, preparation, calendar and location actions execute in the service under the same effective gates as their direct counterparts. The removed #678 search/private-read separation is not revived by that relay. Strict-isolated profiles and a bridge without the required relay retain their restrictions.

### Location continuation

A requested Telegram location belongs to the pending owner Work and is task-scoped current context. It creates the existing exactly-once continuation with the original request/channel and a reference relation; it does not rerun the asking Work, accept a preparation or issue a Memory approval. An unrequested location starts no Work. Stopped/cancelled Works do not continue and withdraw their pending request.

Where an effect already occurred, the continuation first receives the recorded effect note and checks state rather than repeating it. A preparation's continuation retains its run/slot, isolated history, prefix and redaction; the slot settles from the continuation or the asking run on expiry. Withdraw the location keyboard on a consumed answer, retrying that UI removal only if its first send failed. The tool is offered only where the paired Telegram surface and supported direct/relay route provide it.

## Attention in this program

Attention prepares for an owner need; it does not create authority. Existing durable preparation state supplies the scheduler; no new general monitoring loop follows from this contract.

**Act, then tell (#717, owner direction 2026-11-07).** A preparation the owner's AI sets up inside a Work the owner's own typed message started is `scheduled` at once, whether the owner asked for it or the AI judged it serves them. So is one the daily look back sets up. The owner is told once with a cancel button, the same pattern as C5's ask-less memory policy (#918). The `explicit_preparation_request` judgment no longer decides scheduling. It records who asked: `owner-request`, or `owner-work` / `dream` for one the AI set up on its own. A run of a self-set preparation never uses payment autonomy. No scheduled run takes a step that needs the owner's approval (#619). A scheduled run may cancel or pause its own preparation, and nothing else, without leaving a draft. A Work the owner did not type (a continuation, retry or replay) still only proposes. A goal the owner declined is set up again only when the owner asks for it again.

**Daily plan (#780, owner decision 2026-11-10).** Preparing ahead of need is core to the product, and research and preparation need no owner acceptance. While the look back is on, Still keeps one daily preparation whose run is the owner's AI reading the day: today's calendar, the recent conversation, what is already scheduled, and Still's record of what earlier plan runs set up and in how many later requests each result was placed in the assistant's context. That run alone, among scheduled runs, may call `schedule_preparation`. What it sets up is recorded as `day-plan`, is scheduled at once, is not announced one by one (it is listed in Settings, and an answer that reuses a result says when it was prepared), never uses payment autonomy, takes no step that needs an approval and cannot schedule in turn. The plan's brief is a method and names no topic. The owner changes its time, pauses or cancels it like any preparation. Cancelling it or deleting it from the list is an opt-out that is kept, and a plan the owner gave an end stays finished: no plan is set up again until the owner turns the look back on again. While the look back is off, an existing plan's due slots pass unrun and a plan run already in progress can no longer schedule. Whether a prepared result was useful is the plan run's own judgment; Still records only that it was placed.

### Accepted watch windows

The original #719 watch window repeats an accepted preparation every 5–720 minutes within a period of at most 24 hours and at most 48 runs. A window that elapsed while the service was down expires rather than replaying missed runs. Each run is ordinary bounded Work. The later supported recurring-preparation interface is described by its current implementation and [release coverage](product-status.en.md); do not apply the old watch-window example as the only possible recurrence format.

When the owner agrees to a continuing preparation, the owner's AI uses the known timing or chooses a reasonable time from the conversation. The confirmation states the assumption and is sent after the schedule is stored. If a decisive detail cannot be inferred, the AI asks for it. The existing Work and its reply supply the context when the owner answers.

With `when_needed` delivery, a bounded judgment compares the accepted goal, scrubbed result and last notification on ordinary intermediate checks. Identical results stay quiet. If judgment is unavailable, the existing fallback sends once, then keeps later unjudged runs quiet. A check that closes the accepted window or is due to report at the owner's requested cadence queues its report independently of this judgment. For an intermediate check with a successful result, a bounded judgment can close that window early when the observed result establishes that nothing further can change. A day-level rhythm continues on its next eligible day. The accept prompt states interval, deadline, run bound and delivery, and the owner can cancel through the supported Settings or notification control.

An accepted preparation sends one notice to its paired conversation when a quiet run starts waiting for a connection or a window expires without its result. It uses the existing Work continuation when the connection is ready. A new connection wait after a resume can send its own notice. The day plan reads the recorded wait or expiry, and does not offer the same work again while it is waiting.

### Offered preparations

Under #582/#583, the worker may answer and propose a plausible repeating preparation, and the daily look-back may suggest one from requests on separate owner days. Proposals remained `proposed` until accepted; #717 replaced that with act-then-tell (above). A previously declined equivalent goal is not offered for 30 days; identity uses exact goal equality or the existing judgment. Do not substitute a hard-coded category list.

The look-back kept at most two suggestions, supported by at least two read turns on different owner days. Since #717 it keeps at most three: a rhythm, or a standing interest the turns show, needs at least two read turns on any days. It skips an active or declined equivalent goal, uses existing schedule normalizers and attributes the proposal to the anchor. Standing-only suggestions appear with the next owner Work, without a separate push. At most one proposal message is offered per day, bound to its exact rows, and none while paused. Unanswered proposals expire after seven days; expiry is not refusal.

Under #638, a definite dated need can instead support a one-off proposal from at least one read owner turn. It uses the same limit, normalizers, duplicate/decline checks and anchor. A look-back holding such a dated proposal can offer it on the preparation tick during local daytime 08:00–21:59, without waiting for the next owner Work. An unaccepted one-off expires when its time passes. No dated need means no periodic digest or notification. These rules do not authorize general habit mining. Execution before acceptance is limited to reading, researching and reporting (act, then tell, and the daily plan above).

## Family collaboration (phase 2)

The durable distinction is delegation to another person's agent, not acting with that person's session or Grants. The historical phase-2 program and its closeout obligation remain in their original delivery record; this page does not activate them. Current peer-agent implementation and retirement of the older same-host family setup/share paths are governed by the connection contract and named source changes. Do not revive retired setup instructions or inherit the peer owner's authority.

## Probe scenarios and the no-scenario-code rule

The original book-to-cart, schedule/reminder and contextual lunch examples were observation windows, not hard-coded product cases. The original closeout also reserved an owner-chosen holdout unused in development. Their exact wording and dated observations remain at the pinned pre-consolidation contract.

Decision/runtime code must not branch on a named site, provider, product category or question type to make a probe pass. Explicit versioned skill content can supply generalizable domain/site methods when the AI chooses to load it; core code neither selects a named skill by scenario nor injects probe-specific instructions. Every implementation PR explains whether it generalizes the loop or scripts a case. Reuse review identifies the supported library/API, existing symbol and thin Still-owned policy glue.

## Completion rule

The original SECRETARY-01 closeout requires one recorded head with: an observed alternative after failure; evidence-grounded completion; truthful requested/observed/failed/unknown reports; negative secret/payment checks and permitted-action positives; no scenario-specific code; the three original owner probes and a held-out owner case; and the separately audited phase-2 scope or an explicit owner decision to close at phase 1 and disposition phase 2. Live checks remain owner-operated and budgeted. A fixture pass does not replace them.

Those historical obligations are not declared complete, waived or reactivated by this edit. Completion selects no successor. The current selected work and any subsequent re-plan remain in their own authoritative records.

## Non-goals

No background execution that pays, sends, changes anything or needs an approval without the owner (reading, researching and reporting ahead of need is allowed, see Attention), no unbounded monitoring, multiple principals sharing one owner's authority, new canonical state store, arbitrary-code platform or deferred hardening program is introduced. No blanket permission to purchase, send, book or use a new connection follows from a conceptual example. Existing merged behavior is a regression input, not work to reset to an old prototype.

## Related documents

[Memory and learning](engineering/contracts/memory.en.md), [Current Context](current-context-contract.en.md), [Decision](decision-layer.en.md), [Assistant Execution](assistant-execution-contract.en.md), [Owner Control](owner-control-contract.en.md), [Presence](presence-experience-contract.en.md), [Connection and Delegation](connection-delegation-contract.en.md), and the original [secretary research](research/secretary-agency-spec-2026-09-26.ko.md).

## Earlier section links

The following explicit anchors keep older deep links useful. They lead to the current rule's home, not to a second copy of superseded policy.

<a id="amendment--422-2026-10-30-memory-is-shown-in-use-consolidation-is-quiet-and-reads-only-the-owners-words"></a>
<a id="amendment--402-2026-10-30-the-once-a-day-look-back-dream"></a>
<a id="amendment--220-2026-10-23-append--invalidate"></a>
<a id="amendment--19-steady-notes-01-2026-10-20-one-bounded-revisable-working-notes-document"></a>
<a id="amendment--998-self-review-01-2026-10-04-the-secretary-reviews-its-own-misses"></a>
<a id="amendment--918-slice-a-2026-10-02-memory-saves-without-an-ask"></a>
<a id="amendment--805-phase-1-2026-09-28"></a>
<a id="historical-amendment-pointer--836--818-memory-ask-chain"></a>
<a id="historical-amendment-pointer--818"></a>
Memory, notices, upkeep, notes and dream: [consolidated contract](engineering/contracts/memory.en.md).

<a id="amendment--826-2026-09-28"></a>
<a id="historical-amendment-pointer--701--705-search-separation"></a>
Private context and search: [pilot posture](#pilot-posture) and [information-use audit](#information-use-audit).

<a id="amendment--774-2026-09-28"></a>
<a id="amendment--677-2026-09-27"></a>
Supported relay, browser and location behavior: [capabilities](#capabilities) and [location continuation](#location-continuation).

<a id="amendment--710-2026-09-27"></a>
Orchestration: [agency loop](#the-agency-loop) and [Decision Layer](decision-layer.en.md).

<a id="amendment--719-2026-09-28"></a>
Accepted preparation/watch behavior: [Attention](#attention-in-this-program).
