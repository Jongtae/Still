# Subscription-Engine Telegram Stabilization Contract

## Objective and scope

An owner who has selected a previously owner-confirmed subscription engine can request a summary of approved personal notes from the paired Telegram conversation. Still supplies the exact approved notes and only its declared tools to the bounded engine turn, retains the result and redacted execution evidence, and recovers safely after rejection, timeout, malformed output, restart, or duplicate delivery.

This stabilization cycle changes no provider credentials, OAuth configuration, live external activation, connector catalogue, A2A capability, permission scope, host mount, arbitrary shell access, or broad network access.

## Input, approval, and policy boundary

The paired Telegram owner invokes the explicit `/summarize` command. The request is bound to the paired channel, chat, queue job, and selected engine. Still reads only notes already stored in that owner runtime; it does not create memory, enumerate another runtime, attach arbitrary context, or send the notes to a provider other than the selected bounded engine.

The engine input must contain the approved-note summary instruction and the bounded note payload, never merely the original command. Notes are untrusted data: their instructions are not executable. The engine receives no store handle, home directory, credentials, raw data path, document roots, or arbitrary environment.

## Declared tool round trip

Still exposes only `list_notes`, `save_note`, and `web_search` through a per-turn MCP bridge. The engine configuration must reference that bridge. Every tool call is validated by Still, runs through the existing capability facade, and records redacted start/terminal evidence under the queue job. The bridge has a per-turn private local endpoint and closes before the temporary run directory is removed.

Codex receives an explicit per-turn MCP configuration override; Claude Code retains its strict MCP configuration. Unsupported engines never claim tool support. A fixture engine must demonstrate an actual request → declared tool invocation → result response round trip; tool-list presence or a fabricated final answer alone is insufficient.

## Results, failures, and recovery

The terminal result is stored in the job and one assistant message. Telegram delivery remains a single terminal bubble. Rejected commands, timeout, malformed engine output, tool errors, and bridge protocol errors produce a redacted failed job with a named recovery action; they do not retry an external engine implicitly.

Restart recovery preserves the existing queue and delivery semantics. A job is claimed once, duplicate Telegram updates retain the existing request key, and a terminal job cannot execute the engine or tool bridge again. Evidence records engine lifecycle and declared tool lifecycle but excludes note bodies, prompts, credentials, raw paths, socket locations, and provider payloads.

## Validation and completion

Automated fixtures prove the pre-fix command-only prompt defect, approved-note input after the fix, declared MCP configuration use, tool round trip, result/evidence persistence, rejection, timeout, malformed output, restart, and duplicate suppression. CI runs the complete pytest suite plus plan/document/ledger checks. The `validate` GitHub check is required for pull requests into `main`.

Development completion is mock/fixture evidence only. Operating validation remains separate: it requires an owner-selected subscription engine and paired Telegram deployment, neither of which this cycle configures or claims.
