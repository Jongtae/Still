# MP1 D-01 — Personal Space Boundary Contract

## Status and user outcome

**Status: design complete.** This document is the decision record for MP1 I-01. A new owner starts with Still Personal Space and one assistant instead of choosing an external second brain, and can understand and control what is memory, source material, and work evidence.

## Current-state inventory

| Current store | Personal Space classification | Original and external sharing |
| --- | --- | --- |
| `notes` | Explicitly saved memory | Stored only when the owner requests `/note` or a memory/record action; supplied to a model only through Still tools |
| `workspace_results` | Owner-saved safe work result | Retains a result copy and redacted evidence; the source job remains in `jobs` |
| `context_events` | Temporary owner-submitted context | Retained locally until expiry; never shared without assistant policy and per-request approval |
| Documents in connected folders | Source-of-truth original | Not copied into the Still database; only excerpts that pass the existing document-sharing boundary may reach an external model |
| `messages`, `jobs`, `tool_events` | Conversation, work, and execution evidence | Not long-term memory; user-facing views show redacted summaries only |

## Data flow

1. When an owner explicitly saves a memory, Still records it in `notes` in the owner runtime. A selected execution engine receives a `list_notes` tool result only when needed for the request; it never receives direct database access.
2. When an owner saves a work result, `workspace_results` receives a safe copy and redacted evidence. The source work and execution history remain separately in `jobs` and `tool_events`.
3. Connected-folder originals and temporary context send only the minimum excerpt that passes the existing source policy and per-request approval boundary to an execution engine. The Personal Space aggregate neither reads nor copies originals.
4. The I-01 read model returns only permitted metadata from each store and redacted counts to the authenticated owner. A deletion applies only to the specified note or result, and does not propagate to evidence or originals.

## Fixed boundaries

1. **Explicitly saved memory** has one current source of truth: `notes`. Complete conversations, model reasoning, search results, and file content never become automatic memory.
2. **Safe work summary** means only `workspace_results` that the owner saved. I-01 does not have a model automatically create or store work summaries.
3. **Source originals** remain in connected folders and owner-submitted temporary context. Personal Space does not reindex originals or create a second copy.
4. **Execution evidence** remains in `jobs` and `tool_events`. Personal Space shows only source categories and counts, never request text, search queries, paths, tool payloads, or model reasoning.
5. **External sharing** preserves the current policy. Connected documents and the context inbox require source-specific approval; explicitly saved memory reaches a selected engine only through Still `list_notes`/`save_note` tools. This design adds no automatic sharing.

## Threat model and mitigations

| Threat | D-01 mitigation and remaining boundary |
| --- | --- |
| An execution engine or external model reads too much of the personal database | Engines receive only declared Still tools and the approved minimum result; owner databases and original paths are neither directly mounted nor exposed. |
| Conversation, reasoning, search, or originals become long-term memory unintentionally | Only `notes` and owner-saved `workspace_results` are Personal Space items; no automatic-save path is introduced. |
| A Personal Space view or API re-exposes sensitive context or payloads | Only the authenticated owner has access; context is metadata and evidence is redacted to category/count. |
| Deleting a result damages evidence/originals or prevents recovery | A deletion is limited to one note/result copy. Retention of jobs, tool events, and source originals remains a separate policy. |
| Export leaks credentials or connection grants | The portable-export exclusions for secrets, sessions, pairing, engine/model choice, and folder grants are preserved. |

## I-01 storage and API contract

I-01 does not create a new personal-memory table. It uses a read model that aggregates existing `notes`, `workspace_results`, `context_events`, `jobs`, and `tool_events`, avoiding duplicates and migration risk.

| Interface | Behaviour |
| --- | --- |
| `GET /api/personal-space` | Returns explicit-memory items/count, saved-result items/count, temporary-context metadata, and redacted evidence categories/counts |
| `DELETE /api/personal-space/memories/:id` | Deletes only one explicit-memory item; it does not delete jobs, messages, tool events, or source documents |
| `DELETE /api/personal-space/results/:id` | Deletes only one saved-result copy; the source job and evidence remain |

Responses are available only to the authenticated owner. Context items return metadata, never content. Every deletion is an owner action and idempotent on retry. A separate retention or purge policy for work evidence and conversation text is outside I-01.

## UI and channel contract

- The web home remains “My Assistant”; advanced management contains one Personal Space view explaining memories, saved results, context, and used evidence.
- A new owner is not forced to choose a connection list, MCP, A2A, or a second brain.
- Telegram keeps existing `/note`, `/notes`, work results, and evidence labels. I-01 adds neither automatic memory nor new data sharing to Telegram.
- Delete and export copy explain in plain language what is removed or moved and what remains.

## Retention, export, and restore

The current portable export excludes auth, sessions, connection secrets, Telegram pairing, engine/model selection, and local-folder grants while moving the owner database. I-01 preserves that contract. Explicit memory, saved results, and work evidence therefore move through export/restore; context-inbox items move only while they remain unexpired in the current database. Connected originals and secrets require reconnection.

## I-01 validation plan

| Validation | What it proves |
| --- | --- |
| Unit/store | The Personal Space aggregate classifies each source without duplication and returns redacted evidence only |
| API/auth | Unauthenticated access is rejected; an owner idempotently deletes only a note/result; context content is absent |
| Migration | The Personal Space read model works from existing QuickStore data without a schema migration |
| Export/restore | Notes, saved results, and evidence move; secrets, connections, and folder grants do not |
| Browser | A new owner saves a memory, views a saved result, deletes one item, and sees export guidance without choosing a connection |
| Live acceptance | A real owner verifies explicit saved memory and a saved result on web and paired Telegram, and completes a new-install flow without an external connection |

## Fixture and acceptance record

The implementation issue uses a `QuickStore` fixture with two explicit memories, one saved result, one unexpired context item, and two job/tool-evidence records for one owner, plus items for a second owner. The fixture proves owner isolation, non-duplicated counts, absence of context content, and that deleting a note/result does not affect evidence. Live acceptance records the real owner, channel, date, redacted observation, and failure/recovery result as named evidence. Automated and live results do not substitute for one another.

I-01 starts only after its implementation issue records all of these automated contracts and the named live-acceptance procedure.
