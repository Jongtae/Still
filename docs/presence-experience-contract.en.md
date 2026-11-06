# Still Presence Experience Contract

## Status and purpose

This contract defines how Still communicates and collaborates with its owner across conversation, results, setup and recovery. It consolidates adopted presentation rules; it does not select development work or establish that every requirement has been observed on a live account.

The editorial baseline is `99fad1598b75837cf4428cfa43ad810266e5dff7`. The [original contract at that revision](https://github.com/Jongtae/Still/blob/99fad1598b75837cf4428cfa43ad810266e5dff7/docs/presence-experience-contract.en.md) preserves the dated amendments and observations. The [concept chapter](concepts/presence-and-collaboration.ko.md) explains the rationale; the [source register](concepts/references.md) distinguishes research findings from Still's design choices.

## Current rule index and precedence

| Concern | Current rule |
| --- | --- |
| Identity | One continuing assistant relationship across replaceable workers and professional roles. Technical identities remain inspectable. |
| Ordinary replies | Give the answer or useful next action, not routine received/running/completed narration. |
| Long work | Use transient progress presence, a model-selected initial plan preview and bounded meaningful updates. Do not invent progress. |
| User intervention | A correction must affect the relevant work or receive a truthful explanation of why it could not. |
| Outcomes | Distinguish succeeded, partial, failed and unknown. A good-looking reply cannot override recorded effects or approvals. |
| Memory | Follow the [Memory contract](engineering/contracts/memory.en.md): direct saves, third-party proposals, quiet consolidation and working-note changes have different notice/undo rules. |
| Pages | Use a page for a result or input that conversation cannot carry well or safely. Do not require users to operate an internal management console before asking for work. |
| Settings | Distinguish configured, verified, available and currently selected state; changing one is not proof of another. |

The [Secretary Agency Contract](secretary-agency-contract.en.md) owns pilot policy, the [execution contract](assistant-execution-contract.en.md) owns effect/recovery truth, and [Owner Control](owner-control-contract.en.md) owns authority. Presentation cannot widen those boundaries. [Professional Roles and Communication](professional-roles-and-communication-contract.en.md) defines cross-role collaboration. [delivery-plan.yaml](../delivery-plan.yaml), not this document, selects development work.

## North Star

**One continuous, truthful personal-assistant relationship, with the underlying machinery available for inspection rather than made the subject of every conversation.**

## Product definition of Presence

Presence is continuity across turns, tools, workers, failures, restarts and handoffs. It depends on context, memory, appropriate communication and recoverable work, not a claim of consciousness, a staged personality or artificial delay.

The internal record can remain `Work → Event → tool/runtime → Evidence → outcome`. The user should experience understanding, necessary choices, useful work, an honest result and a natural continuation. These are two views of the same work, not two authoritative databases.

## Experience principles

1. **Answer first; receipts on demand.** Lead with the useful result or next action. Keep technical detail accessible without making it the default response.
2. **One voice, replaceable workers.** A worker or role is not a separate speaking identity. A coherent voice must not disguise the actual worker, its limits or the absence of professional authority.
3. **Continue before restarting.** Resolve a follow-up against recent intent, open work, corrections and approvals when the reference is clear.
4. **Communicate what matters.** A significant finding, changed expectation, blocker, choice or verified outcome can justify an update. Tool count and scheduler state alone do not.
5. **Truth before fluency.** Uncertainty, failure and approval needs remain visible even when the prose is short and natural.
6. **Ask when needed.** Request a connection, input or covering authority in the context of the task, then resume through the existing bound continuation.
7. **Make memory useful and correctable.** Use a relevant remembered fact in the answer, state an inference tentatively and allow correction. Do not substitute repeated memory reports for useful help.
8. **Earn an interruption.** Proactive contact needs relevance, timing and an accepted execution mechanism; it is not required to make reactive conversation work.
9. **Let the model interpret meaning.** Code enforces exact state and authority, rather than growing a request-specific conversation rule tree.

## Model-first semantic interpretation

The [Decision Layer](decision-layer.en.md) interprets intent, relevance, references, corrections and recovery choices over bounded context. Its provider and model are configuration, not part of the assistant's identity. Fixture engines support deterministic tests; they are not a production keyword fallback.

The model can propose what to say and what to do next. It cannot create approval, erase an unknown effect or claim an unobserved operation. The same distinction applies to role-specific communication: a professional lens changes relevant standards and evidence, not authority or the number of personalities speaking.

## Surface responsibility

| Surface | Responsibility | Boundary |
| --- | --- | --- |
| Conversation | Requests, questions, corrections, useful answers and supported contextual decisions. | Not a raw Work-status or engine-log console. |
| Necessary input/approval surface | Secret entry, OAuth, local folder choice and exact approval where the applicable action requires it. | A remote conversation link cannot silently grant host authority. |
| Result page | Long comparisons, documents, calendars, owner-model sources and execution detail that text cannot present well. | Not mandatory setup or an empty result promise. |
| Existing Settings | Inspect, change and revoke supported configuration. | A read of the page makes no provider call; a saved value is not an active route. |
| Technical detail | Actual Work timeline, worker identity, sources, errors, approvals and effect uncertainty. | Neither concealed from the owner nor copied wholesale into every reply. |

## Conversation surfaces: shared and per-surface rules (#212)

The common boundary is [ConversationSurface](../src/personal_agent/conversation_surface.py). Telegram, Slack and Mattermost have their own adapters; the earlier statement that only Telegram exists is historical. Installation and release-specific support are listed in [product status](product-status.en.md) and the [connection guide](guide/connections.en.md).

A paired remote surface serves one owner identity in the owner's private conversation. A message, reaction or tap from another identity or unauthorized conversation starts no Work and receives no answer. Existing channel/generation bindings apply to continuations, prompts and callbacks.

Each Work has one terminal reply. Record send intent before sending. An uncertain send is not blindly replayed; a send known not to have reached the service follows the supported bounded retry/backoff path. Interim updates are not the terminal reply.

Typing, drafts and reactions are best-effort presentation. A failure there must not fail, duplicate or change the Work. Serialize presence calls and final delivery so a stale typing/draft update cannot follow the answer. Pending approval is the active interaction; approval/payment waits suppress the transient draft and progress reaction.

Optional behavior follows declared capabilities (`reactions`, `typing`, `live_drafts`, `buttons`, `native_stop`), not a core branch on a surface's name. Without reactions, show none; without drafts, use typing where available; without either, wait without fabricating another capability. Truthful final delivery remains required.

A typed reply or reaction concerning a pending owner item is interpreted in context through the existing owner-act judgment, settling at most the applicable item. A bound button is an exact action, not another model judgment. Unknown or unavailable interpretation does not approve anything.

### Telegram bindings and formatting

Pairing uses the one-time start code and the private owner identity. Work, prompts and callbacks bind to the bot generation and chat; reconnecting does not authorize callbacks from an earlier generation. A native Stop is reconciled with the actual state: cancelling queued work is not proof that a running remote action was stopped.

Replies use the supported Telegram HTML projection. Render Markdown tables as a header and one line per data row, preserving safe inline formatting and links; retain the model's original answer in the store. A single plain-text retry is allowed when the service explicitly refuses the formatting, not after an uncertain send. Slack uses its supported Markdown message/block path, with the same distinction between a formatting refusal and an uncertain delivery. Mattermost renders its supported Markdown. Media and input widgets are offered only where that adapter implements them.

## Conversation projection rules

### Short work

A short ordinary request normally produces one useful answer. Internal Work and Event records remain, but they do not generate receipt/running/completed bubbles by default. Do not add an acknowledgement merely to make the assistant seem active.

### Long work

Send additional information only when it changes what the owner needs to know. The paired Telegram path has no routine acknowledgement card: queued work can use the received reaction, and running work uses native thinking/typing presence.

The first plan may provide a **plan preview** when the model judges the work needs more than a short reply. Show the understanding, any assumption, approach and two to five short steps once, before the worker starts. The preview is a durable message the owner can correct. Do not add worker/model/reason machinery to that message; the preview shown to the owner also reaches the worker brief and steer judgment. A failed preview send does not change the Work's authority or result.

After a preview, `tell_owner` allows at most three brief, meaningful updates per Work. The worker chooses whether a finding deserves an interruption. Updates are transcript rows marked `delivery_projection='update'`, not the final answer or proof that the goal is complete. The transient draft remains ordinary waiting presence.

### Telegram presence: reactions, thinking draft and typing (#581, #835, #858)

These rules describe the adapter at the review baseline, not a guarantee that every client accepts every optional method.

**Reactions.** A natural-language turn receives the deterministic eyes fallback; exact commands do not. A qualified model choice from the curated supported emoji set may replace it using only the redacted owner message. Unavailable, unconfident or out-of-set choices keep the fallback. No per-tool reaction judgment runs. Closing reactions are allowed only for a succeeded, delivered, non-blocked answer with no owner item awaiting a decision. That judgment receives the owner message and content-free delivery/write facts, never the answer body or private source material. Otherwise remove the reaction without a positive replacement. A parked connection/context Work can keep its received reaction until it ends. Do not resend an unchanged reaction.

**Thinking draft.** At five seconds, the preferred rich draft can show the animated thinking block and Stop with the non-empty dots text. Check at the existing 1.5-second interval and resend only after a change or the 20-second keepalive. An explicit refusal disables that rich method for this Work; fall back to the plain draft, then typing only if that too is refused. Keep one draft identity for edits. An approval wait suppresses it. These fallbacks change presentation, never execution or result truth.

**Developer mode.** Default off. Exact `/dev on` and `/dev off` persist the preference; `/dev` or an unknown argument reports state/usage without a model call. When enabled, the draft can show the worker, model and effort actually passed to the current launch, or the explicit default-model label when none was passed. This is launch metadata, not a fabricated observed provider identity. With developer mode off, Telegram shows dots, the web step line is empty and Slack uses its native loading state. Approval/payment waits suppress either mode.

**Attention during waiting.** The existing draft may show one already prepared item: first an undelivered fresh prepared answer, then an accepted reminder due within twelve hours, then an eligible pending ask. A memory ask is eligible only within its first hour. Exclude the current Work's own run/proposals/asks, do not repeat an item within six hours, and keep at most one item on this Work's draft. This deterministic state projection makes no new model call and does not accept, deliver, dismiss or reschedule the item. Its durable message and buttons remain. Redact the wording and retain the presence event for the information-use audit. No such line appears during an approval/payment wait.

**Typing and closure.** Refresh typing at least every four seconds while the Work runs, with or without a draft. Final delivery and presence share the lock; the optional closing judgment runs after releasing it so it cannot block polling or Stop. Nothing in these signals is a promised completion time or invented progress percentage.

### Failure and recovery

Explain what could not be completed, what remains unconfirmed and the available next action. Do not default to sending the owner to the web console when the next step can be explained or initiated in conversation. Technical diagnostics stay accessible without exposing credentials, raw provider errors or internal element-matching details in the main answer.

### Partial outcome

The goal outcome is not a count of successful tool steps. A recovered intermediate error does not make a successfully met request fail. Conversely, pending approval, an unresolved state-changing failure or an unknown effect cannot be overridden by a favorable goal judgment.

When an AI answer exists, deliver that answer first with a short truthful closing note or needed next action. Prefer the supported gap explanation to an opaque step count. Do not restore the old machinery-first header, tool-name list or withheld-answer chain. Show verified and unverified portions distinctly; do not present an unsuccessful state-changing action as done. Technical step detail remains available. The same truth-qualified projection applies to supported watch notifications.

### Unknown external effect

State that the external result is unknown. Explain duplicate risk where relevant and do not repeat a consequential action without evidence and authority making that safe. This explicit statement takes precedence over an optimistic model reply. A connection error is not proof that nothing happened.

### Follow-up continuity

Resolve “try again”, “does it work now?”, a changed time or quantity, cancellation and correction against the relevant recent request when unambiguous. Keep the relationship attributable in Evidence. Ambiguity requires clarification or a non-answer, not a fabricated referent.

## Identity and worker changes

The owner continues speaking with Still when the execution route changes. Keep actual worker/model identity inspectable, and distinguish configured, launched and observed identity. The developer-mode transient display is the explicit limited exception to keeping routine execution machinery out of conversation.

## Cross-role, collaborative Presence refinement (2026-10-10)

Presence is not a professional role or a final formatting stage. It is the shared relationship across roles, tied to judgment while work runs. The [roles contract](professional-roles-and-communication-contract.en.md) separates ambient feedback, meaningful communication and collaborative intervention.

Every mid-work owner message must receive the applicable disposition: reflected in the ongoing answer, applied by a bounded continuation, not applicable because of an already completed effect or other limit, or answered as separate work with the reason. Merely receiving text is not proof that a worker consumed it. Corrections reach later planning and evaluation; an approval that no longer covers the changed parameters cannot be reused. Preserve the actual route's ability to take live input rather than promising all workers can be steered identically.

Speak naturally and accurately in the owner's language. Warmth must not hide disagreement, uncertainty or failure. A professional lens supplies relevant standards, not jargon for its own sake or a claim of professional status. Follow the shared voice preference where supported, not a different persona per topic.

## Contextual setup and authority

Offer one relevant next action when a task lacks a connection, input or covering permission. A supported Gmail connection or owner-local folder handoff resumes the original bound Work exactly once when complete. Conversation can initiate that flow; it cannot skip its owner/session/local-authority checks.

Use exact approval preview where the current action policy requires it, particularly the pilot's per-action payment approval. Do not infer a universal per-change approval rule from the historical Calendar draft example: the newer authorized Calendar API path has different connection/write semantics. The [connection guide](guide/connections.en.md) and current action contract determine the boundary. Natural language and smooth presentation neither add nor remove authority.

## Pages

Management should be achievable in conversation where supported. A page is appropriate when text cannot present the result well, or when conversation cannot safely collect the input. Result pages can show sources, long comparisons, files, calendars and detailed execution records. Necessary input surfaces include first-AI setup, secrets, OAuth, local folder choice and action approval required by policy.

Do not add a general management screen that the owner must learn to operate before ordinary work. Existing Settings remain until a separately scoped change classifies and replaces their functions. This principle does not remove existing supported conversation channels or turn an old screen-layout proposal into a permanent ban on future channel design. The retired utility prototype is not a specification for the current application.

## Settings contract

Existing Settings show understandable service/route/folder names, current values and one clear action. Distinguish **currently used**, **configured**, **available**, **connected**, **needs attention** and **not connected**. IDs, scopes, timestamps and diagnostics belong in detail unless needed for the decision. A successful configuration test does not silently select that configuration.

AI settings distinguish Main AI (default Work executor) and Judgment AI (semantic orchestration). Following Main AI is a preference, not a subordinate role. Preserve existing language controls and owner entry points. The [settings design](design/settings-ux-renewal-spec.en.md) describes the shared chooser and current-value/edit patterns; it does not supersede the Pages boundary.

Conversation can read the supported settings values and allowed choices, then propose a digest-bound `SettingsOrchestrator` change. The proposal applies nothing. A bound button or a judged plain confirmation from an owner-typed conversation settles at most that draft; unclear/unavailable judgment leaves it pending. Preparations, retries and other Still-generated messages cannot approve or cancel a draft. Compatibility command syntax may remain accepted but is not the required user-facing instruction.

Before applying, recheck that the starting value remains current and the choice remains allowed. The stale check and setter run under a per-category lock, so two drafts cannot both overwrite the same initial state. A setter that queues qualification is reported as requested, not already applied. Slow probe/qualification setters run in order off the calling thread and report in the original conversation. A restart during apply settles unknown rather than replaying the setter.

A route choice is restricted to connected/checked options and is still a destination decision. Time zones use validated IANA choices. Credentials, keys, tokens, login material and endpoints are not ordinary conversational settings values; use the dedicated secure input path. These tools retain their existing trusted-local versus strict/isolated availability. This edit adds no setting, endpoint or authority.

## Memory presentation

Do not duplicate storage policy here. The [Memory contract](engineering/contracts/memory.en.md) defines direct saves and notices, exact undo, invalidation versus deletion, background upkeep, dream and working-note revisions.

For foreground owner-AI saves, report the actually saved value with the supported notice/undo rather than asking again. For quiet background consolidation, do not send the retired per-dream summary; raise an applicable change or held question through the relevant later conversation. Working-note revision notices retain their separate rule. Show remembered information by using it when relevant, with source/inference/currentness limits visible where they matter.

## Third-party/delegated Memory presentation

A third-party MemoryCandidate is not yet remembered. Present one natural ask per applicable Work, values rather than keys, bound to exactly what was shown and is still current. The owner's direct-save path does not authorize a delegated writer. Do not collapse approval, forgetting, deletion and undo into a single vague “memory updated” message.

## Truth and authority invariants

Success, partial, failure and unknown remain distinct. Unknown effects and uncertain sends are not blindly replayed. Presentation cannot hide approval requests, destination changes, consequential outcomes or material blockers in on-demand-only detail. A claim of cancellation or remote deletion requires its own evidence.

No silent paid-provider/route fallback is introduced to make a conversation appear successful. A candidate is not remembered before the applicable canonical write. Secrets, unrestricted payloads and hidden reasoning are not transparency requirements. These rules preserve the existing action-specific approval and messenger privacy boundaries; they do not impose a new confirmation on every allowed write.

## Observable acceptance matrix

Use owner-visible transcripts together with the supporting state, not copy review alone.

| Case | Required observation |
| --- | --- |
| A. Short request | A useful answer without routine lifecycle bubbles. |
| B. Runtime failure | A truthful limitation and safe next step without a misleading success claim. |
| C. Follow-up | The relevant failed/open Work is continued, or ambiguity is stated. |
| D. Missing connection | A contextual handoff and exactly-once bound continuation. |
| E. Missing local folder | Owner-local selection and the actual read/write scope; no remote shortcut. |
| F. External change | The current action-specific permission/approval boundary is enforced; where approval is required, its exact preview matches the action. |
| G. Long work | Appropriate transient presence, at most one selected preview and bounded meaningful updates. |
| H. Partial answer | Useful answer first, confirmed and unconfirmed parts distinguished, concise truthful qualifier; no machinery-first header. |
| I. Unknown effect | Explicit uncertainty and no unsafe duplicate attempt. |
| J. Memory correction | The actually saved, pending, invalidated or deleted state is described accurately. |
| K. Mid-work intervention | The correction's recorded disposition agrees with later planning/execution, or the inability is disclosed. |

Capture message count/order, Work/Event/Evidence correspondence and opposing failure/partial/unknown cases. Include restart, stale approval, wrong-owner and unsupported-channel cases where material. A fixture transcript does not establish live messenger behavior; a live recording does not independently prove all account effects.

## Architecture implication

Reuse authoritative state and the existing conversation/surface boundaries. Keep technical and preference views as projections, not additional sources of truth. Useful participation requires an input path back into judgment and work, not just a better renderer after execution.

## Non-goals

No new kernel, conversation database, UI framework, semantic keyword engine, compulsory heartbeat or consciousness/personhood claim. This contract neither activates a development program nor removes existing supported channel behavior.

## Review rule

A conversation, recovery, model-route, setup or Settings change must show how continuity, truth, authority and inspectability remain intact. A friendlier sentence is not sufficient when the outcome or continuation is wrong. Inspect both the user-facing transcript and the state it describes.

## Historical program decisions

Dated Presence/Secretary/UX activation sequences, the old owner-AI ask flow and the web-management-only prototype remain in the [pinned original](https://github.com/Jongtae/Still/blob/99fad1598b75837cf4428cfa43ad810266e5dff7/docs/presence-experience-contract.en.md), not as a second current rule set. Their original observations retain their evidence class. These legacy anchors preserve navigation without reactivating those programs.

<a id="amendment--gov-ask-less-01--918-slice-a-2026-10-02-remembered-told-undoable"></a>
<a id="execution-integration-refinement--agency-600"></a>
<a id="relationship-to-planned-work"></a>

[Original research](research/presence-and-settings-ux.ko.md) · [Documentation map](README.md)
