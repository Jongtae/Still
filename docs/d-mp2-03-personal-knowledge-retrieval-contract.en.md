# D-MP2-03 — Owner-Local Personal Knowledge Retrieval Contract

## Objective and scope

Still will let an owner retrieve relevant, already owner-local Personal Space memories, saved workspace results, and explicitly approved local-context metadata with transparent source evidence. Retrieval is a bounded read of existing owner data, not automatic memory creation, document ingestion, cloud synchronization, or external search.

This design adds no vector service, provider, OAuth credential, endpoint, external action, or operating deployment.

## Owner outcome and source selection

An owner asks a bounded retrieval question such as “What did I save about this project?” The policy owner selects only records owned by that runtime and returns short excerpts with source type, opaque local reference, capture/result time, and why the record matched. Context content is never returned merely because it was captured: only its approved sharing state and redacted metadata are eligible until a future, separately approved context-use contract says otherwise.

No model instruction, Telegram message, or web request may enumerate another owner’s data, search raw paths, create a new memory, or turn a retrieval into external sharing.

## Ranking and read model

`PersonalKnowledgeOrchestrator` is the sole policy owner. It accepts owner ID, channel, and a bounded query term. It ranks exact normalized term matches in title/content summary first, then recency, then stable opaque ID; result type is a deterministic final tiebreaker. It never embeds data, calls a model, or uses a remote index.

The read model includes a source label (`memory`, `workspace-result`, or `approved-context-metadata`), opaque reference, short bounded excerpt or redacted metadata, timestamp, match reason, and retrieval-safe recovery label. It excludes passwords, tokens, raw local paths, full message history, unapproved context content, provider payloads, approval material, and internal ranking scores.

## Owner, channel, and sharing boundary

Every request binds to the authenticated owner. HTTP, paired Telegram, and the local companion invoke the same read operation and receive equivalent source references, result ordering, redaction, and error classes. Rendering may differ; no adapter queries storage directly.

Retrieval alone never authorizes sharing. If a future assistant action would send a selected result to an external engine or Agent, it must create a separate exact, owner-bound sharing preview under the applicable policy. This contract returns only local evidence and has no implicit retry or external fallback.

## Audit, export, restore, and threats

Owner-local audit retains timestamp, owner-scoped opaque retrieval reference, source category counts, terminal state, and redacted error/recovery class. It excludes query text, excerpts, record IDs that can be correlated outside the runtime, and channel message bodies. Portable export retains only safe terminal category evidence; it never exports pending selections, unapproved context material, or reconstructed retrieval history.

Implementation must fail closed for invalid owner/channel binding, ambiguous or empty query, expired/deleted source, prompt injection in retrieved content, replayed sharing proposals, and attempts to bypass the policy owner. Deletion and restore preserve the existing Personal Space ownership/deletion semantics; a missing source produces a named recovery-safe result rather than a substitute.

## I-MP2-03 entry contract

I-MP2-03 may activate after this design PR merges, its issue closes, canonical-document/plan/ledger checks pass, and a new goal-ready issue exists. It must implement fixture-backed deterministic owner-local retrieval, source evidence/redaction, audit/export/restore safety, and HTTP/Telegram/local-companion parity. It excludes every external index, provider, connection, credential, OAuth, automatic long-term memory, document ingestion, external sharing/action, and operating-mode behavior.
