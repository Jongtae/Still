# MP1 D-03 — Google Drive Read-only Contract

## Scope

I-03 connects one owner Google Drive with the standard installed-app OAuth Authorization Code flow, PKCE, and the minimum read-only scope. The consumer only clicks Connect Google Drive and approves Google access; they never create a Google Cloud project, client ID, or token manually. Still ships the public OAuth client ID and uses a system browser plus a local redirect. The product publisher performs the one-time Google API enablement, OAuth client registration, consent-screen configuration, and privacy-policy setup for that distributed client. It supports search then selected-file read; it never performs full sync, write, delete, share, or permission changes.

## Data and security

Tokens remain only in the private connection store and are excluded from export, events, logs, engines, and browser responses. Search metadata and selected excerpts remain owner-local. A selected excerpt is owner-bound and capped at 4,000 characters; its one-time approval is required before an explicit A2A delegation can receive that exact context. Evidence records only lifecycle metadata, and portable export removes the excerpt, approval, owner identity, and file content.

## API and acceptance

The connector exposes connect, health, search, selected read, and disconnect. Disconnect revokes local token storage and leaves redacted audit metadata; an expired token becomes `reauth-required` until a new authorization completes. Fixtures cover PKCE state mismatch, expired token/re-auth, scope rejection, result redaction, selected-excerpt approval denial, and disconnect/reconnect. Automated tests cover every path; I-03 adds no Drive write capability.
