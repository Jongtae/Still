# MP1 D-04 — Compatibility A2A Delegation Contract

## Scope

I-04 lets an owner explicitly request one bounded delegation to the reviewed local compatibility A2A test peer. Still remains the owner of the user request, Personal Space, approval history, task state, artifacts, cancellation, and evidence. This contract adds neither a public A2A URL nor a commercial runtime connection.

## Data flow and threat model

1. The owner explicitly asks to delegate; Still creates a local delegation record in `requested` state.
2. Still reads the peer's fixed, bundled Agent Card and validates its protocol version, declared skill, input schema, and artifact schema before a task is created.
3. Still sends only the approved task prompt, an opaque delegation ID, and declared minimal inputs to the peer. It never sends Personal Space wholesale, owner credentials, OAuth tokens, connection secrets, host paths, filesystem grants, or approval records.
4. The peer returns task status through deterministic polling or server-sent events. Still normalizes it as untrusted evidence and records only redacted lifecycle metadata.
5. Still validates every artifact against the Agent Card schema and size/type limits before it can be shown to the owner. Invalid artifacts produce `failed` evidence, never a completion claim.

The peer is untrusted even when local. Still rejects unknown capabilities, malformed cards, cross-owner IDs, unsolicited artifacts, duplicate terminal transitions, missing correlation IDs, and timeout or cancellation races.

## API and storage contract

`A2ADelegation` exposes `discover()`, `delegate(owner_request)`, `status(delegation_id)`, `cancel(delegation_id)`, and `artifact(delegation_id, artifact_id)`. Valid states are `requested`, `working`, `input-required`, `completed`, `failed`, `canceled`, and `timed-out`; terminal states cannot transition again.

Delegation records retain `id`, requested peer/skill, state, timestamps, redacted error class, artifact metadata, and correlation ID in owner-local storage. Prompts and artifacts remain local evidence. Secrets and raw peer transport payloads are excluded from browser responses, exports, logs, and external engines.

`delegate` accepts only an explicit owner request and a catalogued peer skill. `cancel` is idempotent and forwards cancellation only while non-terminal. `artifact` returns validated, bounded artifact content only after the matching task is completed.

## Fixtures and automated acceptance

The I-04 mock peer suite must cover a valid Agent Card and completed artifact; malformed/unsupported Card; explicit-request requirement; minimum-context assertion; polling and SSE progress; timeout; owner cancellation; peer cancellation; duplicate terminal event; artifact schema/type/size rejection; correlation mismatch; and redacted evidence/export behavior.

Automated acceptance proves the Still adapter follows this contract using only fixtures and the local mock peer. No live A2A endpoint, credential, provider account, or owner manual test is required for MP1 delivery. Real endpoint configuration is deferred to the owner-controlled operating-mode deployment after MP1.

## Non-goals

I-04 excludes automatic delegation, public peer discovery, arbitrary Agent Card URLs, agent installation, marketplace search, payment, peer-side tool grants, and sending consequential actions through the peer.
