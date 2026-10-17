# DRIVE-TG-01 — Telegram Drive Web OAuth Contract

## Owner journey

When paired Telegram requests need Drive context and no connection exists, Still sends an HTTPS `Google Drive 연결하기` button. The owner opens it in a normal browser, signs in and consents directly with Google, and returns to Telegram for a redacted connection result. A browser embedded in Telegram is not an OAuth trust boundary.

The owner-local callback seam publishes a concise connected or recovery result to the same paired Telegram owner. It accepts no browser callback as a Telegram command and does not include the OAuth code, state, token, or file data in the Telegram message.

The connection requests only `https://www.googleapis.com/auth/drive.file`. Still never searches the whole Drive. The owner selects files through Google Picker; only those file IDs may be read and supplied as bounded local context. A write, share, delete, Calendar, Gmail, service-account, or full-Drive request is rejected.

File content is read through an owner-local injected transport only after the Picker selection check. It is available for in-memory summarization in response to the owner's request; it is not written to Drive OAuth status, audit evidence, local configuration, or relay payloads.

## Local and relay boundary

The owner's local runtime creates PKCE verifier/challenge, a random HMAC-signed one-time state, expiry, and Telegram-owner binding. The browser callback is accepted only after state, owner, expiry, and single-use checks. The local runtime exchanges the code and writes tokens only to its encrypted secret store. The Drive handoff fails closed when given the ordinary `QuickStore`; its `EncryptedDriveSecretStore` uses authenticated encryption and receives its key from an owner-local keychain or runtime secret boundary, never from the data directory. Status/evidence contains state names and times only—never OAuth code, verifier, token, client secret, Telegram message body, file body, or selected excerpt.

The HTTPS handoff/relay may route the browser but must not persist any of those values. It must have no database, log, analytics event, or error payload containing secrets or user file data.

## Deployment procedure (not performed by this iteration)

1. In the publisher-owned Google Cloud project, create a distinct **Web application** OAuth client. Do not reuse the Desktop client.
2. Register one owned HTTPS callback URI exactly, e.g. `https://connect.example.invalid/oauth/google/callback`; configure the same URI in Still deployment settings.
3. Register the owned domain and public privacy-policy/homepage requirements before production publishing. Keep the app in Testing and restrict test users until that work is complete.
4. Deliver the client secret only into the owner-local encrypted deployment secret store; never source-control it or send it through Telegram.
5. Perform Google login, consent, token exchange, Picker selection, and any live Drive observation only after separate owner operating approval.

## Owner-approved local-only test mode

For a test conducted only on the owner's Mac, a separately configured Web client may register `http://localhost:<port>/oauth/google/callback`; Google documents localhost as an HTTPS exception. Set both the handoff and callback URLs to that same loopback runtime and enable the explicit local-only mode, which rejects every non-localhost URL. Open the Telegram button only from Telegram Desktop on that Mac, so the ordinary browser resolves localhost to the local Still runtime. A phone cannot use this flow because its localhost is the phone, not the Mac. No public tunnel, DNS record, or separate hosting is used.

## Recovery and evidence

Denied, mismatched, replayed, expired, missing-code, failed-exchange, revoked, and expired-token flows return a concise Telegram recovery message. A new connection request creates a new state; no callback or external message is replayed. Fixture tests prove normal completion, denial, expiry, replay, reauthentication, token redaction, and selected-file enforcement. This contract is mock validation, not live OAuth or Drive evidence.
