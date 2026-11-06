# Local web presentation contract

[Korean summary](web-management-contract.ko.md) · [Presence](presence-experience-contract.en.md) · [Owner Control](owner-control-contract.en.md)

This contract retains the rules for existing local result, Settings and approval surfaces. It is an implementation/acceptance specification, not proof of shipped behavior. The former #382 session plan and old screenshots are not instructions to reset the current UI. Original decision identifiers in this file belong to the predecessor unless explicitly qualified.

## 1. Product role

The governing direction is conversation-first management: the owner asks the assistant to do work or manage its settings; pages serve results that text cannot carry well and inputs that conversation cannot safely carry. The #880 Pages decision in the Presence contract superseded the earlier web-as-management-utility product framing.

This file governs the remaining local screens, not an exclusive list of conversation channels. It does not prohibit a separately supported browser conversation or claim that Telegram is the only channel. Still retains history, Memory, Grants, Work, Artifacts and Evidence independently of the surface. Removing or rearranging a view must not remove history, APIs, channel delivery or owner data.

## 2. Decision precedence

Presence controls the role of pages and owner-facing communication. Owner Control and the current Secretary pilot govern authority; this presentation contract cannot widen access or alter the meaning of approval, Memory or an external effect.

The original utility-v1 prototype's chat-first and compulsory-project navigation was superseded. Its [provenance entry](design/utility-v1/README.md) explains where to find the pinned study. Reuse only patterns consistent with the current requirements, such as stable drafts, compact setting rows and exact test/apply. Simulated controls, synthetic model names and instant-stop behavior are not runtime capabilities.

The original specification can be read with `git show 6edc3380b613ab0c2897ba7a913ab658150a68f4:docs/web-management-contract.en.md`. Retiring its one-off implementation sequence preserves that record without maintaining a second current plan. The [Goal Execution Contract](goal-execution-contract.en.md) and [Incremental Delivery](incremental-delivery.en.md) retain their repository workflow rules.

## 3. Information architecture

The existing management navigation uses task/activity and Settings destinations, with AI, external connections, files/permissions and memory/privacy categories; diagnostics and scheduled-work views remain contextual. Treat those as the inspected screen structure, not a requirement to add a parallel management console when conversation can handle the task.

The earlier generic all-records destination was removed by #562. Exact retained items use `#item/<note|memory|artifact|candidate>/<id>`, reached from typed records attached to the relevant Work or the Settings surface for that type. Do not infer a retained-item link from response wording or a keyword. The item view has no independent top-level navigation entry and must work without a parent destination.

Use one stable navigation and one visible destination. Selecting a destination replaces the content pane rather than appending another screen below a conversation. Preserve URL history, selection, scroll, focus and unsaved drafts. Mobile navigation must provide a clear path back from detail to list.

Projects remain optional grouping, not a compulsory setup step or a filesystem path. Preserve their data and access. Do not add an empty marketplace/agent-management shell before the corresponding capability exists. File setup is required only for a file-backed action. Missing configuration gets a relevant setup action rather than a permanent tutorial, dashboard, mascot, tips or recommendation panel.

## 4. Tasks are not messages

Represent useful work rather than every connection command or internal probe. Separating diagnostics changes visibility, not retention. Reuse the existing Work IDs and state; do not create a second authoritative task database.

Task detail presents the request and real state, then the result or next owner action, followed by observed execution/sources and optional redacted detail. A text answer must be inspectable, and an artifact must have a working result action. Do not show an empty result heading as proof that a result exists.

Keep partial, failed, interrupted and unknown outcomes distinct. Never invent a percentage, ETA, intermediate step or model identity. Development heartbeat state is not product progress. Original request context is revealed only within the authorized task and on expansion, not as an unrelated global transcript.

Approve, cancel and recover controls must correspond to real task-bound backend operations. Cancellation requested, cancellation confirmed and an uncertain remote outcome are different facts. A visual change does not establish every observability/recovery requirement.

## 5. Records and files

An exact item view shows that item, not an implicit cross-type browser. Notes, Memory, candidates, temporary material and artifacts retain their types. Their labels, counts, previews and actions must match the real record. A pending third-party MemoryCandidate is not saved Memory. The direct owner-AI path follows the [memory contract](engineering/contracts/memory.en.md), not an obsolete universal candidate rule.

Identify the item before removing it. Never send artifact deletion to a Memory endpoint, ignore a refused action, or delete an original source file as a side effect of deleting a record. Removing a navigation destination does not delete, migrate or re-scope stored data.

Temporary collection/sharing controls belong with the relevant type. Empty-state text explains what is absent without implying that ordinary conversation history was erased. Use **참고 폴더** for read inputs and **결과 저장 폴더** for generated files. Keep legacy folder/grant distinctions where their capabilities differ; a cleaner label does not merge authorities.

## 6. Connections, states and guidance

Show the effective route and actual precedence, not an assumed route split by channel. Editing/testing an API connection does not itself switch a selected subscription CLI. Do not say no AI is connected merely because one unused provider has no key. Where routes genuinely differ, show that distinction.

Configuration, owner-attested login, qualified readiness and observed execution/model are different states. An unknown model remains unknown. A local process may use an external model; data-location copy must follow the selected route and policy.

Preserve **edit draft -> test that exact draft -> apply**. Failed or stale tests retain the working connection. Changing an input invalidates its earlier verification. Do not carry an old key to a changed destination, demand an unnecessary API key for a CLI/local route, or make a paid call just because the owner opened Settings. Explain when a connection check contacts a provider.

Connected services show their current state and a manage/change action, not a blank credential form that implies reconnection is necessary. Put brief help and errors beside the relevant action, with longer explanation available on demand. Do not add unsupported controls to fill a layout. Render safe links and supported formatting, not arbitrary HTML or raw encoded payloads.

Polling must preserve draft text, caret/selection, search results, confirmations and expanded detail. Navigation must not discard unsaved input without an explicit decision. Opening a pane alone must not create a model request or external action.

## 7. Bounded implementation and evidence

A change must preserve channel ingestion/delivery, conversation APIs, owner data and the effective authority rules. Existing HTML/CSS/JS and backend state are the starting point; a framework, storage or messaging rewrite is not implicitly authorized by this document. Replacing obsolete presentation assertions requires replacement coverage for the retained behavior, not wholesale test deletion.

The original #382 A1–A8 acceptance covered destination replacement, preserved Telegram/task paths, task-specific events/results, stable drafts under polling/navigation, truthful CLI/API state, typed record actions and data survival, contextual setup, and desktop/mobile browser observations with synthetic fixtures. These remain evidence about that historical scope, not a claim that all current screens were revalidated by a documentation edit.

For new work, check the affected interactions and state transitions on the exact head. A fixture browser check is not a live owner session, and a source inspection is not a browser check. Keep required CI and any genuinely triggered authority review; do not make an unrelated audit or paid benchmark a default prerequisite.

## 8. Recording versus execution

The old #379/#380 decision and #382 WEB-ADMIN-01 handoff described one predecessor implementation sequence, W0 then explicitly activated W1–W3. It did not allow implementation from a readiness record alone or authorize #381/successor work. The [retired handoff pointer](design/utility-v1/CODEX_PROMPT.txt) preserves that no-activation boundary without directing a new session to repeat the old program.

Current work follows the selected issue and root delivery plan. A document, ready record or merged documentation PR does not start Codex, a heartbeat, a provider call or a release. Keep decision recorded, task specified, implementation executed and verification/merge distinct. Private input screenshots and conversations are not published as repository evidence by this consolidation.
