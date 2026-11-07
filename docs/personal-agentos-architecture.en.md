# Still Architecture

## Status and purpose

Still is a personal agent installed on an owner-controlled machine. It keeps the owner's memory, open work, permissions and results while the AI and tools doing the work can change. This document defines those responsibility boundaries. It is not a list of shipped integrations or a claim that the proposed package ecosystem is complete.

Read [product status](product-status.en.md) for release coverage and observations, [the concepts](concepts/README.md) for the reasoning, and [contributor onboarding](contributor-onboarding.en.md) for development. The detailed contracts linked below define behavior within their scope. The v0.1 [core/package specification](core-primitives-agentpackage-v0.1.en.md) and its schemas retain their versioned authority; this edit does not migrate records.

This consolidation uses revision `99fad1598b75837cf4428cfa43ad810266e5dff7`. The [earlier architecture](https://github.com/Jongtae/Still/blob/99fad1598b75837cf4428cfa43ad810266e5dff7/docs/personal-agentos-architecture.en.md) preserves the original program chronology and design wording.

## Product identity

**The product is the continuing personal agent, not whichever model, CLI or framework currently performs a task.** Its state and authority must remain independent of those workers.

“Operating environment” or “Personal AgentOS” describes this persistent AI layer. Still does not replace macOS or Linux, control the host kernel, or make a compromised host trustworthy. Local installation alone is neither isolation proof nor a guarantee that inference happens locally.

Assistant/secretary work is a starting role, not an exclusive product domain. Research, purchasing and other professional lenses can use the same owner context and work records. A role does not grant professional status or permission to act. Coding harnesses and repository delivery automation build or assist Still; they are not its product identity.

The long-term distribution direction remains an owner-authoritative personal AI core with an open agent/capability ecosystem. A useful assistant and observable owner control come before marketplace scale. Package discovery, third-party execution and marketplace commerce must be described separately from capabilities already supported by current code.

## Architecture thesis

Separate what the owner retains from the workers that can be replaced, and separate semantic judgment from permission and result truth.

```text
owner request, correction or decision
                  |
        conversation / necessary input
                  |
        owner state and current authority
                  |
          judgment and execution
                  |
       bounded model / CLI / tool / peer
                  |
         recorded observations and result
                  |
       useful answer / retained artifact
```

The arrows are responsibility and information flow, not a request to build another set of services. A worker can propose a plan or result; it does not become the source of truth for Memory, Grants, Work transitions or external effects. A page and a conversation are views of that state, not competing lifecycle stores. Owner corrections must feed back into work rather than only changing the final wording.

| Responsibility | Canonical definition |
| --- | --- |
| Owner rights and limits | [Owner Control](owner-control-contract.en.md) |
| Durable facts, learning and correction | [Memory](engineering/contracts/memory.en.md) and [Current Context](current-context-contract.en.md) |
| Meaning, roles and mandates | [Professional Roles and Communication](professional-roles-and-communication-contract.en.md) |
| Semantic planning and qualification | [Decision Layer](decision-layer.en.md) |
| Invocation, effects, recovery and result truth | [Assistant Execution](assistant-execution-contract.en.md) |
| Connections, skills and peer delegation | [Connection and Delegation](connection-delegation-contract.en.md) |
| Conversation and collaboration | [Presence](presence-experience-contract.en.md) |
| Future package/distribution design | [Platform Foundation](agent-distribution-platform-foundation.en.md) and the versioned package specification |

## Kernel primitives

These ten terms distinguish responsibilities. They are not ten mandatory databases or ten newly implemented services.

### Owner

The human principal whose state and choices are protected: identity, preferences, policies and trusted configuration. A provider, package or marketplace does not become that principal.

### Context

Attributable information assembled for a particular Work. Its task scope, source and lifetime are distinct from durable Memory.

### Memory

Owner knowledge kept with provenance, stated/inferred status, currentness, replacement, retention and correction semantics. The owner's direct-save path and a third party's candidate path remain different.

### Artifact

A retained result, file or document with provenance and ownership independent of the worker that produced it. A link or success sentence without a usable result is not an Artifact completion claim.

### Capability

An ability understood at the policy boundary, distinct from the particular tool or runtime implementing it. Listing or describing an ability does not prove it is reachable or authorized on this installation.

### Runtime

A bounded execution environment or adapter: a model route, subscription CLI, local tool, connector or delegated runtime. Replacement changes the worker, not ownership of the retained state.

### Grant

Authority over a resource, action, scope, destination and applicable limits. A model, role, package declaration or event cannot issue itself a Grant.

### Work

The durable unit of requested work, with the applicable context, permissions, execution identity, budgets, approvals, outcome and recovery records. A user message and an external effect are not automatically the same Work state.

### Event

A recorded trigger or transition. It can lead to policy evaluation or Work creation; it is not permission to execute an action.

### Evidence

An attributable record of what was requested, attempted, observed and left unknown, including relevant identity, authority and result information. Product execution records and development validation have different scopes. Model prose is not an independent observation.

## Observable owner control

The six owner rights remain inspection (OC-01), data choice (OC-02), destination visibility/control (OC-03), bounded actions (OC-04), stop/revoke (OC-05), and retained/portable state across worker replacement (OC-06). The [control contract](owner-control-contract.en.md) supplies enforcement and acceptance requirements. This architecture does not turn those requirements into claims that every current path is fully hardened.

Apply the current action-specific policy, including the [pilot rules](secretary-agency-contract.en.md). Public reading, cart/session mutation, personal-data entry, sending, reservation and payment are different effect classes. Where approval is required, it must cover the actual target and parameters; a change invalidates non-covering approval. Connecting or logging in does not grant every action the service could technically perform. Neither every write nor every Memory writer has the same confirmation rule.

Stop/revoke prevents new unauthorized work and stale queued replay through the supported boundary. It does not undo a completed external action or establish that an in-flight remote action stopped. Preserve unknown outcomes and the no-duplicate-effect rule. Useful allowed work and effective denial of unauthorized work are joint acceptance requirements.

Receipts distinguish configured, launched and observed model/runtime identity; unknown telemetry stays unknown. Preserve redacted targets, destinations, permissions and effects without making hidden reasoning, secrets or unrestricted raw payloads a transparency requirement.

## Execution location and data limits

Local code can use a local model or an external model. In the latter case, permitted context is sent to the selected destination; local-first does not mean local-only. A remote connector or peer does not place the remote service's internals, retention or deletion under Still's control.

Disconnecting a connection, revoking a Grant, removing a package, forgetting a fact and deleting data have different scopes. Account for local copies, rebuildable indexes, retained artifacts, backups and unresolved remote copies. A replacement does not inherit new authority merely because the old worker had it. Removing a worker must not silently erase the owner's results.

## Context and Memory boundary

A Work uses authorized, attributable context. Conversation history is not automatically a set of durable owner facts. Keep temporary conditions, lasting facts, working methods and dated retrieval aids distinct under the [Memory contract](engineering/contracts/memory.en.md).

A third-party worker proposes a MemoryCandidate through its declared writer path. The owner's own AI uses the separately adopted direct-save and notice/undo rules. Neither case gives the external runtime ownership of the canonical store. Revoked or deleted sources must not be reintroduced through stale context or indexes. Permission to retrieve locally is not, by itself, permission for a new external destination.

### Facts and role interpretations

A sourced fact and a role's interpretation are different records. In a hypothetical account, total balance and immediately available funds can differ without contradicting one another. Match the account, measure, unit, scope, time and source before comparing values; retain a genuine source conflict rather than silently choosing the convenient number.

A spending plan and an investment analysis can use that same account record for different judgments. They must not overwrite the source fact or infer transaction authority from a professional role. The [ontology chapter](concepts/ontology-and-roles.ko.md) and roles contract explain the distinctions; these examples do not advertise a financial service.

## Capability and Runtime contract

One capability can have several runtime implementations, and a runtime can expose several capabilities. The versioned semantic boundary covers capability discovery/preparation/invocation/cancellation/health; Work creation, lease/checkpoint/report/transition; effective-Grant evaluation/escalation; and attributable Evidence/result output. These conceptual operations are not a claim that a universal public API already implements them all.

A runtime receives Work-scoped context and effective authority. It can request escalation or propose an output, not mint Grants, rewrite sealed history or self-certify an unobserved external result. Nested workers cannot exceed their parent's applicable authority. Result, Artifact and MemoryCandidate outputs retain provenance and cancellation/idempotency limits. A reroute needs covering destination, data and budget authority.

### Provider-independent decision layer

Model-backed semantic interpretation is the normal boundary for language, reference, relevance and planning. Exact security, protocol and effect invariants remain deterministic. Test doubles are not a semantic keyword fallback. Providers implement Still's interface; provider-specific names do not become kernel concepts.

An implemented optional adapter is not necessarily qualified for every operation. For example, a typed judgment route is not automatically an arbitrary-schema planner. The [Decision Layer](decision-layer.en.md) owns route activation, model selection, qualification and bounded fallback; this architecture does not repeat a dated model list.

### Ruflo's place

Ruflo remains a possible bounded delegated runtime, not a required component or an implemented-runtime claim. Its own agents and memory would remain runtime-local. Retained owner state and any candidate proposals stay under the same authority rules as other third-party execution.

## Local service code boundaries

The existing local implementation uses named modules rather than a new framework. The module descriptions below explain code responsibilities, not independent services to deploy.

| Boundary | Existing responsibility |
| --- | --- |
| `quickstart_service.AgentService` | Composition root for the store, connectors, orchestrators, surfaces and queue worker. |
| `work_execution.WorkRun` | State carried between one Work's execution phases. |
| `work_executor.WorkExecutor` via `AgentService.work_executor` | Claim, route, prepare, execute, settle and finish/fail one Work, including judgment/budget settlement and post-work handling. The extraction does not create another state owner. |
| `browser_signin.BrowserSignin` | Browser sign-in windows, provider sign-in records and exact step approvals, with forwarding service entry points over the same owner state. |
| `orchestrator` and `settings_orchestrator` | Typed work planning/evaluation and bound setting drafts on existing state. |
| `google_connections.GoogleConnections` | Google OAuth connection and saved-client lifecycle, revocation, status rows and Drive Picker helpers over the service's existing state, with forwarding service entry points. |
| `conversation_handoff` and `conversation_surface` | Contextual continuation and the shared owner pairing, ingestion, delivery and presence interface. |
| `telegram_surface.TelegramSurface` | Telegram-specific ingestion, callbacks and delivery, with forwarding service entry points; it does not own a second store. |
| `conversation_projection` and `telegram_presence` | Truth-qualified owner-facing output and best-effort transient signals. |
| `telegram_store.TelegramStore` via `QuickStore.telegram` | Telegram tables in the owner's existing SQLite database, preserving caller transaction participation and recovery boundaries. |

`SurfaceCapabilities` declares reactions, typing, live drafts, buttons and native stop. Core presentation follows capabilities rather than a named-channel branch. Existing adapters and terminal/browser entry points retain their actual support limits; [Presence](presence-experience-contract.en.md) and [onboarding](contributor-onboarding.en.md) provide the behavior and code map. Sharing a conversation boundary is not evidence that all surfaces support identical widgets.

## AgentPackage

**This section and the distribution sections below define an extension design, not a shipped arbitrary-code marketplace.** The full manifest and lifecycle rules live in the [v0.1 specification](core-primitives-agentpackage-v0.1.en.md) and [platform foundation](agent-distribution-platform-foundation.en.md).

A package declares identity/version, capabilities and runtime requirements, requested data/destinations and secret references, Memory/event behavior, budgets, approvals, artifact interfaces, dependencies, isolation, lifecycle and provenance. The declaration describes requested potential authority; effective authority is resolved for each Work. A declaration-only or instruction-only package must not be advertised as evidence of arbitrary executable support.

## Package lifecycle and authority

`downloaded != installed != enabled != connected != authorized-for-action`.

The designed lifecycle distinguishes discovery, inspection, verification, staging and health checks from installed-disabled state, separate enabling/connection/grants, execution, update/rollback, quarantine and removal. Installation and health checks grant no implicit owner-data, paid-provider or action authority.

An update that expands data, destinations, Memory behavior, background events, budget, dependency or effect scope needs new applicable review/approval. Failed health checks cannot activate; failed updates preserve or restore supported known-good state. Rollback cannot restore revoked authority. Disable/quarantine/removal revoke the package's authority while preserving owner results and Evidence under retention policy. Actual isolation needs implementation evidence, not merely a valid schema.

## Distribution plane

### Package Manager

Local exact-revision validation, staging, health, installation, enabling, update, rollback, quarantine and removal. It coordinates lifecycle without creating authority.

### Registry

Publisher namespace, immutable versions/digests, compatibility, signatures, provenance, advisories and revocation. It is not a store for private owner Context, Memory or Work.

### Marketplace / discovery

Discovery, ranking, reviews, curation and possible commerce. Use minimized capability descriptions rather than private owner prompts. Popularity, payment and ranking cannot override a revoked release, quarantine or the owner's policy.

### Trust is multidimensional

Identity, integrity, build provenance, policy checks, behavioral evidence, requested permissions, incident history and popularity answer different questions. A signature or installation count is not a safety result. A verification label must name what was checked.

## Compatibility and ecosystem strategy

Reuse supported formats and interfaces only where metadata, licensing, authority and lifecycle semantics can be mapped explicitly. Unknown hooks or hidden authority are refused or labelled unsupported/partial, not silently accepted. MCP and peer-agent compatibility are interfaces, not delegated ownership. The current [connection contract](connection-delegation-contract.en.md) defines actual connection and peer control levels separately from this future distribution design.

## Useful bootstrap/reference agents

Prioritize a capable assistant and reusable tools that complete useful work. Measure source-grounded outcomes, continuity, latency/friction and denial/recovery behavior before adding orchestration or more packages. A future reference package must use the same public authority boundary as another author's package; hidden privilege is not a successful demonstration.

The older Files/USE-01/OBS-01 program order is historical, not the current queue. A benchmark seed is not an executed result. [Default Agent Usefulness](default-agent-usefulness.en.md) retains its scoped acceptance requirements; [delivery-plan.yaml](../delivery-plan.yaml) and current issues govern selected work.

## Autonomous acquisition

The design distinguishes manual installation, recommendation, an exact owner-approved install plan, standing low-risk acquisition and a package requesting another capability through the core. The latter acquisition levels are not implied current scope. Acquisition and action authority remain independent. They cannot silently accept a new destination, terms, payment, permissions or a human-only authentication step.

## Durable state versus replaceable workers

Retain the supported owner identity/policy, Context/Memory, permissions and connection references, open Work, results and provenance, approval/effect/recovery records and exact package/capability bindings. Models, CLIs, tools, connectors and experience surfaces are replaceable workers or views.

Portability must preserve meaning and ongoing-work limits, not just export bytes. Credentials, sessions and grants excluded from an export must be reconnected or separately authorized; an import is not permission to replay old work. Backups and rebuildable indexes have their own retention and integrity rules.

## Services and development harness

Research, files, communication and scheduling share owner state and authority rather than becoming independent agent silos. The current file-workspace evidence supports its own scope, not every future service.

Repository issues, branches, PRs, CI and handoff procedures are development infrastructure. They do not demonstrate an end-user agent running autonomously. Governance patterns are not mandatory runtime frameworks.

## Current implementation boundary

Read [product status](product-status.en.md), the [release manifest](release-manifest.json), named tests and dated operating observations together. The current code, a published build and a successful live task are different claims. Platform diagrams, schemas and catalogue entries do not erase this distinction.

The prior account of FILE-WORKSPACE-01, D-AP-01, DOGFOOD-01 and the unactivated USE-01 sequence is preserved in the [pinned architecture](https://github.com/Jongtae/Still/blob/99fad1598b75837cf4428cfa43ad810266e5dff7/docs/personal-agentos-architecture.en.md#current-implementation-boundary). It describes those historical scopes, not today's activation or support status. No deferred program is reopened by this consolidation.

## Design invariants

Owner state and authority remain independent of workers. Installation is not action authorization. Local-first is not local-only. Nested execution remains bounded. Memory writers retain their different dispositions. Outcome and recovery records survive supported replacement without duplicate effects. Evidence is attributable and truthful about uncertainty. Trust signals remain distinct. Useful permitted work and real enforcement must both be demonstrated.

These invariants refine acceptance without silently widening a runtime, changing a schema version or migrating a stored record. Refer to the responsible contract for exact limits rather than copying a second mutable policy here.

## Professional lenses across the owner-centered architecture (design direction)

Compose sourced world facts, relevant domain concepts, professional standards, owner context and a scoped mandate before deciding on execution. Role/ontology explains meaning; mandate describes objectives and constraints; Grant/approval controls what may actually happen. Skills supply expertise without becoming a domain-specific kernel router. Presence carries meaningful feedback and owner corrections throughout the work. This is a design direction with separately evidenced implementation, not a claim that every professional role is shipped.

<a id="current-execution-refinement"></a>
The historical execution refinement is now read through the current [execution](assistant-execution-contract.en.md) and [delivery/ownership](assistant-execution-delivery.en.md) contracts. Capability parity requires actual qualified invocations, not matching declarations or prompts.

[Documentation map](README.md)
