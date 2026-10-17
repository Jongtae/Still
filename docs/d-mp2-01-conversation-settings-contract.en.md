# D-MP2-01 — Conversation-First Settings Contract

## Objective and scope

Still will make conversation the default intent surface for safe settings reads and reviewed lifecycle changes, while retaining a local Chrome Settings-like companion for manual inspection and management. This design defines the contract only; it implements no UI, parser, credential, OAuth flow, provider connection, or external action.

The promotion basis is MP1's mock-contract development completion and the owner's explicit selection of this friction in issue #230. It is not a claim of live provider use or operating-mode evidence.

## Owner outcome and vocabulary

The first implementation supports a deliberately small reviewed vocabulary:

| Intent class | Owner expression | Result |
| --- | --- | --- |
| Read | “What is connected?”, “Show Drive status”, “What can I change?” | A redacted settings summary or category read model; no state mutation. |
| Draft change | “Pause Drive”, “Disconnect the A2A peer”, “Resume this reviewed connection” | An exact pending-change preview; no mutation. |
| Confirm | `Confirm <short draft ID>` or an owner-bound UI action carrying that ID | Applies only the exact unexpired preview once. |
| Cancel/recover | `Cancel <short draft ID>`, “How do I recover Drive?” | Cancels the draft or returns the named recovery action; no implicit retry. |
| Sensitive handoff | “Connect Drive”, “Re-authenticate Calendar” | Explains the scope and opens an authenticated local browser/web handoff after explicit confirmation; chat never receives secrets or OAuth artifacts. |

Ambiguous wording, model-proposed actions, bare “yes”, and unreviewed setting names never mutate state. The assistant asks for a reviewed intent or returns the manual settings route.

## Risk tiers and authority

| Tier | Examples | Conversation behavior |
| --- | --- | --- |
| R0 read | Status, category navigation, audit/recovery explanation | Immediate redacted answer. |
| R1 reversible local lifecycle | Pause a currently enabled reviewed capability | Exact preview and explicit owner confirmation. |
| R2 connection-affecting lifecycle | Disconnect a capability, resume an already approved capability | Exact preview and explicit owner confirmation; explain reconnection/recovery. |
| R3 sensitive or consequential | New OAuth scope, credential entry/replacement, provider endpoint, external write, payment | Not a conversational mutation. Require a separately designed authenticated local handoff or remain unsupported. |

Only existing reviewed lifecycle transitions are candidates for R1/R2. A conversational setting change cannot grant a new scope, install a capability, select an arbitrary runtime, or bypass the capability registry.

## Pending-change state machine

`SettingsOrchestrator` is the sole policy owner. It derives an exact canonical change from a recognized intent and current owner-local state.

```text
drafted -> awaiting-confirmation -> applied
             |        |                |
             |        +-> expired      +-> idempotent applied result
             +-> canceled
             +-> failed (stale state / lifecycle rejection / internal error)
```

Each pending record has an opaque ID, owner ID, originating channel, canonical target/action/before/after/effect/recovery fields, a digest, creation/expiry timestamps, and terminal state. It expires after 10 minutes. Confirmation must supply the matching opaque ID and digest, bind to the same owner, and re-check current state immediately before apply. The apply key is deterministic from the owner and digest: retries return the recorded applied result and never execute a transition twice. A cancel is idempotent. A stale or foreign confirmation fails closed and exposes only a recovery-safe explanation.

## Interfaces and channel parity

The policy interface is:

```text
read(owner, category?) -> redacted settings read model
draft(owner, channel, intent) -> pending preview
confirm(owner, channel, draft_id, digest) -> applied/idempotent/fail-closed result
cancel(owner, draft_id) -> canceled/idempotent result
recovery(owner, subject) -> named next action
```

HTTP and Telegram invoke these same operations and receive the same semantic state, draft ID, expiry, effect, recovery, and error class. Their rendering may differ, but neither channel calls adapters or `CapabilityRegistry.transition` directly. The local settings companion reads the same settings read model and routes a manual change through the same draft/confirm controller.

## Data, audit, export, and threat model

Read models expose capability IDs, reviewed state, declared scopes, redacted health category, and recovery labels only. They never expose tokens, authorization codes, passwords, raw paths, message bodies, Personal Space contents, pending confirmation material, or full approval records.

Owner-local audit records retain a bounded timestamp, owner-scoped opaque draft reference, intent class, target category, before/after lifecycle state, terminal state, and redacted error/recovery class. Evidence and portable export exclude confirmation IDs, canonical payloads, channel message bodies, secrets, raw paths, and sensitive connection records. Restore preserves only safe historical state and never reactivates a pending change.

The implementation must defend against ambiguous natural language, prompt/model confused-deputy behavior, channel/owner confusion, replay, stale preview, direct HTTP/Telegram bypass, secret exfiltration, and a manual UI that creates a second control plane. Every mutation is policy-owned, owner-bound, exact, short-lived, auditable, and recoverable.

## Local companion information architecture

The local authenticated companion follows a Chrome Settings-like navigation and search model, not a second runtime:

1. **Assistant** — chosen assistant profile, default behavior, and read-only explanation.
2. **Connections** — reviewed capability status, scope summary, health category, connect/re-auth handoff, pause/resume/disconnect drafts.
3. **Data & privacy** — Personal Space boundaries, source sharing policy, export/restore, and redaction explanation.
4. **Approvals & activity** — redacted pending/terminal setting activity and recovery links.
5. **Runtime & recovery** — local runtime health, diagnostics, backup/restore, and named recovery actions.

Search indexes labels and redacted descriptions only. Every manual control opens the same preview/confirm contract; it never accepts secrets in a chat transcript or duplicates state outside Still.

## I-MP2-01 entry contract

I-MP2-01 may activate only after this D-MP2-01 PR merges, its issue is closed, the canonical-document/plan/ledger checks pass, and a new goal-ready implementation issue is created. Its scope is R0 settings reads; R1/R2 lifecycle draft, confirm, cancel, expiry, idempotency, audit and recovery; HTTP/Telegram parity; and the local companion read/navigation model.

I-MP2-01 excludes real OAuth/credentials/provider activation, new scopes, capability installation, marketplace behavior, arbitrary settings, external actions, and any R3 mutation. Its automated fixtures must cover every vocabulary class, ambiguity, foreign/expired/replayed confirmation, stale state, duplicate submission, lifecycle rejection, channel parity, redaction, export/restore, and manual-view/controller parity. Operating-mode configuration remains a later separately authorized goal.
