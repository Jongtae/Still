# Master Plan 1 — Personal Assistant Core

## Status and goal

**Status: development complete (mock-contract validated).** D-01 through I-06 remain historical boundary work; R-01 through R-05 then merged in dependency order to prove the policy-owned personal-assistant flow through automated fixtures. [MP1 Remediation](mp1-remediation.en.md) records that evidence. The remaining manual Telegram observations from UX-05 were removed from that release gate by owner decision; that decision does not claim those observations passed. External credentials and real connection activation remain deferred to the owner-controlled operating-mode deployment.

The goal is to validate a personal-assistant core in which one owner asks for outcomes from Still Personal Space and Still coordinates only reviewed connections while retaining personal state, approvals, evidence, and recovery.

## Fixed defaults and boundaries

- The default second brain is Still Personal Space, not an external service.
- The first external knowledge connection is read-only Google Drive.
- The first A2A connection is a compatibility test peer, not a named commercial runtime.
- An external agent runs only when the owner explicitly requests it.
- The first approved external action is creating a new Google Calendar event.
- Calendar modification/deletion, email sending, public MCP/A2A URLs, arbitrary runtime installation, and a marketplace are outside MP1.

## Design and implementation cadence

Every phase has a `D-*` design iteration and an `I-*` implementation iteration. A `D-*` fixes the user outcome, data flow, threat model, API/storage contract, fixtures, and automated acceptance. A normal PR merge and the defined automated checks may activate its `I-*` implementation iteration.

Every iteration has a GitHub issue, a `codex/` branch, small intentional commits, a PR, and automated validation. Completed work is recorded together in `TASKS.md`, `docs/roadmap.md`, and `docs/issue-branch-ledger.jsonl`.

When a future remediation or operating-mode iteration is activated, its issue must be made goal-ready under the [Goal Execution Contract](goal-execution-contract.en.md). This Master Plan supplies scope and completion criteria; `delivery-plan.yaml` supplies executable order.

External integrations and agents are developed from their documented contracts with fixtures and mock-based automated validation. OAuth clients, provider credentials, external endpoints, and real activation are deliberately deferred: after the whole Master Plan is complete, the owner configures them once while deploying Still in operating mode. Mock success completes MP1 delivery work; it never claims that the corresponding external service is already operating.

## Phase sequence

| Phase | Design iteration | Implementation outcome |
| --- | --- | --- |
| 1. Personal Space | D-01 Boundary for personal memory, source originals, and work evidence | I-01 Default Personal Space and single-assistant UX |
| 2. Capability lifecycle | D-02 MCP/A2A/runtime registry and on/off states | I-02 Reviewed catalogue, enable/pause/disconnect, audit |
| 3. Drive knowledge | D-03 Google OAuth, reads, and sharing boundary | I-03 Read-only Google Drive connector |
| 4. A2A delegation | D-04 Agent Card, task, artifact, and cancellation contract | I-04 Compatibility test peer and explicit delegation |
| 5. Calendar action | D-05 Incremental OAuth, preview, and one-time approval | I-05 Create a new Google Calendar event |
| 6. Core release | D-06 Integrated ReAct policy, fallback, and acceptance | I-06 Personal-assistant core release |

### Phase 1 — Personal Space

D-01 decides the boundary between explicitly saved memory, safe work summaries, source-of-truth originals, and work evidence. I-01 provides a single-assistant UX that does not force connection choice, plus memory, evidence, deletion, and export flows.

### Phase 2 — Capability lifecycle

D-02 decides the capability descriptor, states (`available`, `connected-disabled`, `enabled`, `paused`, `auth-required`, `error`, `disconnected`), grants, secret separation, and audit contract. I-02 lets an owner enable, pause, or disconnect reviewed entries and inspect execution evidence.

### Phase 3 — Drive knowledge

D-03 decides OAuth Authorization Code + PKCE, minimum read scope, secret storage, search-then-selected-read, and the external-model sharing boundary. I-03 provides Google Drive search, reads, sources, health checks, and disconnect without a full sync.

### Phase 4 — A2A delegation

D-04 decides the test peer's Agent Card, explicit delegation, task states, polling/SSE, cancellation, timeout, and artifact validation. I-04 provides an adapter that normalizes A2A results as untrusted evidence and never supplies Still secrets, local paths, or the full memory store.

### Phase 5 — Calendar action

D-05 decides an incremental OAuth write scope separate from Drive, the default primary calendar, event preview, owner-bound exact approval, payload hash, and idempotency. I-05 supports only new-event creation; modification, deletion, attendee invitations, and automatic conflict resolution are excluded.

### Phase 6 — Core release

D-06 decides the ReAct policy and fallback that coordinate Personal Space, Drive evidence, explicit A2A review, and a Calendar draft. I-06 runs end-to-end release acceptance from new installation through pause/disconnect and export/restore.

## Completion evidence

MP1 is development complete only when the remediation sequence proves the policy-owned flow covers Personal Space, Drive sources, explicit A2A delegation, Calendar creation approval, pause/disconnect invocation gating, and export/restore. The owner then performs a separate operating-mode deployment to configure real OAuth clients, credentials, endpoints, and connections. That deployment records its own evidence and does not retroactively change development completion.

The next selection follows the process in [Master Plan 2](master-plan-02-proposal.en.md).
