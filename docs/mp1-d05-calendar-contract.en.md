# MP1 D-05 — Google Calendar Create-Event Approval Contract

## Scope

I-05 creates one new event on the owner's primary Google Calendar through a mock-validated adapter. It requests a Calendar write scope separate from Drive, but only after the owner has previewed and approved one exact event payload. Update, deletion, invitations, attendee lookup, conflict resolution, and automatic scheduling are excluded.

## Flow and security

The assistant produces a local draft with summary, start/end, timezone, location, description, and primary-calendar target. Still validates timestamps and computes a canonical payload hash. It displays the redacted preview and creates an owner-bound, one-time approval record. Only a matching approval, payload hash, owner identity, and unexpired request can call `create`.

The adapter sends the minimum event payload to the Calendar mock, with an idempotency key derived from the approval ID and payload hash. Repeated submission returns the same local result and never creates a second event. Tokens remain in the private connection store; previews, approvals, audit events, and exports contain no token. Missing scope, expired approval, changed payload, foreign owner, malformed response, and provider timeout fail closed and retain redacted recovery evidence.

## API and storage

`CalendarCreate` exposes `draft(input, owner_id)`, `preview(draft_id, owner_id)`, `approve(draft_id, owner_id)`, `create(draft_id, approval_id, owner_id)`, and `status(draft_id, owner_id)`. States are `awaiting-approval`, `approved`, `created`, `failed`, and `expired`; terminal `created` is immutable. The policy-owned orchestrator is the only service/channel entry point for approval and create. Owner-local records retain the canonical draft hash, one-time approval ID, idempotency key, result metadata, and redacted error class; portable export retains only non-content recovery metadata.

## Fixtures and automated acceptance

Fixtures cover valid preview/create, no approval, foreign approval, expired approval, changed draft/hash, duplicate submit, denied OAuth scope, malformed provider result, timeout, and no-write-before-approval. Tests use only a local Calendar mock and prove exactly one `POST /calendars/primary/events` request after valid approval.

No live Calendar account, OAuth client, or manual acceptance is required for MP1 delivery. Operating configuration happens after MP1 under the repository development governance.

## Non-goals

Calendar modification/deletion, email, invitations, attendees, public calendars, recurring events, arbitrary calendars, and automated external actions are out of scope.
