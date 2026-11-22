# Entering an API secret from the phone through a Telegram Mini App (#524)

## Status

This is the design proposal for [#524](https://github.com/Jongtae/Still/issues/524). It changes no code.

The implementation is a separate change. It adds a new way for a secret to enter Still, so it needs the escalated review in the [Development Constitution](../development-constitution.en.md#escalated-review). That means a Codex review of the final head, required CI on that head, and a merge by the owner. The *Authority and threat model* section below is the model that review checks the code against.

## Owner outcome

The owner is away from the Mac. A Work needs an API key the owner holds, so Still asks for it. Today Still does one of two things:

- it opens a temporary HTTPS tunnel to the Mac (ngrok or a Cloudflare quick tunnel) and sends the one-time link to the paired Telegram chat, or
- when no tunnel tool works, it sends a page that opens only on the Mac (#520).

With this change, the owner taps a button in the Telegram chat. A small page opens inside Telegram, the owner types the key, and the original Work continues. Nothing on the Mac is reachable from the internet at any point. Telegram's servers carry only ciphertext that only this Still can decrypt, and only once.

The owner's AI still does the work (C16). This change only removes a blocker: secret input from the phone no longer depends on a tunnel tool, an account with a tunnel provider, or the Mac being reachable.

## What exists

All paths are under `src/personal_agent/` at the main revision this document was written against (`372139f5`).

| Piece | Existing mechanism | Kept as is |
|---|---|---|
| The input request | `api_input.start`: a confirmed `api_connections` Settings draft in state `awaiting-input`, bound to its Work, owner generation and request digest (`guard`, `current`), with at most 4 attempts per Work | Yes |
| The secret boundary | `api_input.submit(service, session, body)`: checks the exact field set, takes `submit_lock`, settles the draft to `applying`, verifies the read probe or stores the code-run set through `api_requests.save_slot(..., encrypted=True)`, and resumes the Work | Yes. The Mini App path decrypts, then calls this same function |
| Encrypted storage | `api_credentials.ApiCredentials`: Fernet with a Keychain-held key (`browser_jar.platform_key`), immutable `api_credential:` references | Yes |
| The session | `phone_input.PhoneInput`: one session at a time, a 32-byte code, `SESSION_SECONDS` = 600, `finish` and `on_end` | Extended with a key pair; the tunnel and cookie parts stay for the Google client page |
| The tunnel | `https_tunnel.start_tunnel` via `PhoneInput.start` | No longer used for `api-slot` input when Telegram is paired (see *Decisions for the owner*) |
| The Mac-only page | `PhoneInput.start_local` (#520) | Yes, as the fallback |
| Owner chat check | `telegram_surface`: `private = chat.type == 'private' and chat.id == from.id`, `authorized = private and from.id == cfg['user_id']` | Reused for the new update |
| Update kinds | `conversation_handoff.TELEGRAM_POLL_UPDATE_KINDS` already includes `message`, which carries `web_app_data` | Yes |
| The public site | `site/`, published to GitHub Pages at `https://jongtae.github.io/Still/` by `deploy-pages.yml` (manual or release-dispatched only) | A new static page is added |
| Crypto library | `cryptography` (already a pinned runtime dependency, `pyproject.toml`) | Yes |

## Telegram facts this design rests on

From the [Mini Apps documentation](https://core.telegram.org/bots/webapps) and the [Bot API](https://core.telegram.org/bots/api), read on 2026-11-22:

1. `KeyboardButton.web_app` opens an HTTPS page. It is available in private chats only.
2. `Telegram.WebApp.sendData(data)` works only for a Mini App launched from a keyboard button. It sends a service message with up to 4096 bytes of data, and the Mini App closes.
3. The bot receives it as `Message.web_app_data` with `data` and `button_text`. The Bot API warns that a bad client can send arbitrary data.
4. **`initData` is empty when the Mini App was launched from a keyboard button.** There is no `user`, `hash` or `auth_date` to validate.
5. `ReplyKeyboardRemove` removes the keyboard, and a `one_time_keyboard` hides itself after use.

Fact 4 changes the brief. `initData` validation cannot be the authenticator on this path, because there is nothing to validate. Authentication comes from the update itself instead. Still receives it through its own `getUpdates` call, authenticated by the bot token, and the update names the chat and sender. That is the same check the surface applies to every owner message today. The one-time key adds a second binding (see below). If a later version uses an inline-button launch, `initData` would then be present and must be validated with the documented HMAC (`WebAppData` key over the sorted data-check-string, with an `auth_date` freshness bound). That launch mode cannot use `sendData`, though, so it would need a reachable server, which this issue rules out.

## The flow

1. `api_input.start` runs as today. When the Work's chat is the paired Telegram owner, work mode is off, and the page is published (see *Rollout*), Still does not start a tunnel. Instead it:
   - creates a one-time RSA-3072 key pair in memory inside the `PhoneInput` session, and a random 128-bit key id (`kid`);
   - builds the page URL: `https://jongtae.github.io/Still/secret-input/v1/` with the public key (base64url SPKI), the `kid`, the field names and labels, and the owner language in the query string;
   - sends one message to the paired chat with a `ReplyKeyboardMarkup` that holds a single `KeyboardButton` with `web_app: {url}`, `one_time_keyboard: true` and `resize_keyboard: true`.
2. The owner taps it. The static page shows the masked inputs. On *Save* it:
   - builds the same JSON body that `submit` accepts today (`{"secret": ...}`, `{"client_id", "client_secret"}`, or the code-run field names);
   - encrypts it as a JWE compact serialization (RFC 7516) with `alg: RSA-OAEP-256`, `enc: A256GCM` and `kid` in the protected header, using WebCrypto only (`generateKey` AES-GCM, `encrypt` AES-GCM, `wrapKey` RSA-OAEP with SHA-256);
   - clears the inputs and calls `sendData(jwe)`. If the token exceeds 4096 bytes, the page refuses and asks the owner to use the Mac page instead. It never truncates.
3. Still receives the `web_app_data` message. Before any other handling, and with nothing logged or stored, it checks:
   - the update is a private chat with `chat.id == from.id == telegram.user_id` (the paired owner);
   - a `PhoneInput` session of kind `api-slot` is alive and not expired;
   - the protected header has exactly the allowed `alg`, `enc` and this session's `kid`.
4. Still decrypts with the session's private key, then **destroys the key** whether or not decryption succeeded. It parses the JSON and calls `api_input.submit(service, session, body)` unchanged.
5. Still replies with the existing success or failure text and a `ReplyKeyboardRemove`. It then tries to delete the service message (`deleteMessage`, allowed in a private chat). If the probe failed and attempts remain, the owner gets a fresh button with a fresh key pair, exactly as a failed page would let them retry today.

A refused submission (wrong chat, no session, expired, wrong `kid`, bad header, decryption failure, replay) gets one short reply in the owner chat when it came from the owner chat, and nothing anywhere else. A foreign chat gets nothing, as an unpaired message does today.

## Authority and threat model

**What is protected.** One API secret (or a set of named values) on its way from the owner's phone to `ApiCredentials`.

**What each party sees.**

| Party | Sees |
|---|---|
| Telegram servers and history | The page URL (public key, `kid`, field names and labels) and the JWE ciphertext. After step 4 the private key no longer exists, so the stored ciphertext cannot be decrypted by anyone, Still included. |
| GitHub Pages | One request for the static page. The query string carries the public key, `kid`, field names and labels, and the language; it carries no secret. |
| The Telegram app on the phone | It hosts the WebView, so like any browser it could in principle read what is typed into the page. This is the same trust the owner already gives the app that carries their Still conversation. The design does not claim to hide the value from the owner's own client. |
| Still on the Mac | The plaintext, in memory only, for the duration of `submit`, then encrypted at rest as today. |
| The owner's AI, logs, Evidence | Nothing new. They never receive the plaintext or the ciphertext. |

**Key lifetime and single use.** The private key exists only in the memory of one `PhoneInput` session. It is never written to the store, the Keychain or a log. It is destroyed at the first of: one decryption attempt, session expiry (`SESSION_SECONDS`, 10 minutes), `finish` for any reason, and process exit. A restart therefore loses it, and `api_input.reconcile` already expires an `awaiting-input` draft without a live session.

**Replay.** A second `web_app_data` with the same or another ciphertext finds no key and is refused. The `kid` binds a ciphertext to one session, so a ciphertext from an earlier session never decrypts against a later key, even before that key is used.

**Foreign chats.** `KeyboardButton.web_app` exists only in private chats, and Still sends the button only to the paired owner. Anyone else who copies the page URL can open it in a browser, but `sendData` works only from a keyboard-button launch, so their data would go to a bot that showed them a button, never to this one. Even so, Still checks `chat.id == from.id == user_id` on every such update and refuses any other chat without decrypting.

**The update itself.** The `data` field is attacker-controllable in principle (Bot API fact 3). Still treats it as untrusted bytes. It parses only a five-part JWE compact token of bounded size, accepts exactly one `alg` and `enc` pair, and then hands the decrypted body to `submit`, which already rejects any body whose field set differs from the draft.

**Plaintext outside the boundary.**

- The new update branch runs before the conversation path. A `web_app_data` message is never stored as a conversation message, Work request, notice or Context observation, and never reaches a model.
- No new log line includes `data`, the JWE, the decrypted body or the private key. Log lines name only outcomes (`secret input received`, `refused reason=expired`).
- An exception inside decryption or `submit` is re-raised as the existing owner text (`api_input.input_failed`) with `from None`, so no traceback carries the value.

**Work mode (#208).** Work mode already pauses Telegram. While it is on, the Mini App path is not offered, and the page opens on the Mac only, as today.

**What this does not change.** Grants, folder scope, network destinations, payment approval and the read probe's destinations are untouched. The flow carries the same draft that the owner already confirmed in conversation. C17 is respected: the restriction kept here serves case 1, a credential leaving to a party the owner did not choose.

**Residual risks the PR must state.**

- The page is a supply-chain surface. Whoever can change what `jongtae.github.io/Still/secret-input/v1/` serves can make it exfiltrate what is typed. That means anyone with write access to the repository's `main` and Pages deployment, or GitHub itself. The mitigations are a tiny page with no external script, a restrictive CSP meta tag (`default-src 'none'`, `connect-src 'none'`, scripts only by hash; GitHub Pages sets no custom headers, so `frame-ancestors` is not available), and code review of every change under `site/secret-input/`. A versioned path lets Still pin the version it sends. No mitigation can make a compromised page safe.
- The Telegram client can read the page (above).

## Existing Solutions Review

**Problem/boundary.** Two pieces need a decision: the transport from the phone to Still with no inbound reachability, and the encryption of the value so that the transport sees only ciphertext.

**Transport.**

- *Adopt: Telegram Mini Apps with `KeyboardButton.web_app` and `sendData`.* This is Telegram's documented mechanism for exactly this case, a page that answers the bot "without communicating with any external servers". Still already polls the `message` update kind.
- Rejected: the existing tunnel (`https_tunnel`). It needs a tunnel tool and makes the Mac reachable, which is the problem.
- Rejected: an inline-button Mini App with `initData` and a server callback. That needs a reachable server.
- Rejected: typing the secret as a plain chat message. Telegram would store the plaintext.

**Telegram's bridge script.** The documented way to call `sendData` is `telegram-web-app.js`, served by `telegram.org`.

- Rejected: loading it from `telegram.org`. The file is unversioned, so Subresource Integrity cannot pin it, and it would give `telegram.org` script execution inside the page while the owner types the plaintext.
- *Adapt (recommended): a pinned copy* of the script at a reviewed revision, served from `site/secret-input/v1/`, with its licence recorded in the implementation PR. Fallback if that licence is unclear: a minimal inline bridge that posts the `web_app_data_send` event the same way the script does. The PR must then record this as Build, with the exact upstream revision it follows.

**Encryption.**

- *Adopt the standard: JWE compact serialization (RFC 7516) with `RSA-OAEP-256` and `A256GCM` (RFC 7518).* Both primitives are native in WebCrypto (`RSA-OAEP` with SHA-256, `AES-GCM`), so the page needs no crypto library. On the Mac side they are native in `cryptography` (`padding.OAEP(MGF1(SHA256), SHA256)`, `AESGCM`), which Still already pins. The Still code is format checking and two library calls. It invents no construction, KDF or mode. The format code is tested with the RFC 7516 Appendix A.1 vector (RSA-OAEP with SHA-1 and A256GCM), and the SHA-256 variant with a vector produced by WebCrypto in a real browser.
- Considered: HPKE (RFC 9180) with DHKEM(P-256). It is the modern choice, but WebCrypto has no HPKE. The page would then need a JS library, and the installed `cryptography` (46.0.6) exposes no HPKE API, so both sides would compose HKDF labels by hand. That is more custom crypto, not less.
- Considered: JWE ECDH-ES. Its Concat KDF is not native in WebCrypto and would be hand-written.
- Considered: `joserfc` (BSD-3-Clause) or `jwcrypto` (LGPL-3.0) for decryption. Both are mature and licence-compatible. Rejected for now: the decrypt is two calls into a library Still already trusts, and a general JOSE library adds algorithm negotiation that Still would have to switch off. The implementation PR may still choose `joserfc` if its review prefers that. The format is the same either way.
- Rejected: Fernet or any symmetric scheme. The page cannot hold a symmetric key without Telegram seeing it in the URL.

**Internal components reused.** `api_input.submit` (the secret boundary and every existing check), `ApiCredentials` (storage), `PhoneInput` (session, expiry, single-session rule), and the Telegram surface's owner-chat check. **Built:** the page under `site/secret-input/v1/`, the in-memory key pair in `PhoneInput`, the `web_app_data` branch in the Telegram surface, and the JWE decrypt function.

**Decision:** Adapt. Telegram Mini Apps and WebCrypto/JWE are adopted as they are, behind a thin Still adapter that reuses the existing input path.

## Tests the implementation must include

The negative tests hold the threat model above. All run with injected Telegram transport, a fake clock and a temporary store. No live Telegram or model calls.

- **Round trip.** Encrypt in Python the way the page does (and with one fixed WebCrypto-produced vector). The update is delivered, `submit` stores the value, the Work resumes, the key is gone, and a `ReplyKeyboardRemove` is sent.
- **Replay refused.** The same `web_app_data` delivered twice: the second is refused, and the store and the draft are unchanged by it.
- **Foreign chat refused.** A `web_app_data` from another `from.id`, or from a group chat, is refused without a decryption attempt (the decrypt function is a spy), with no reply to that chat.
- **Expired key refused.** Advance the fake clock past `SESSION_SECONDS`: the update is refused, and the draft expires as today.
- **Wrong `kid`, unexpected `alg` or `enc`, oversize token, malformed token, and tampered ciphertext (GCM tag failure):** each is refused, and the key is destroyed after the one attempt.
- **Plaintext never leaves the boundary.** With `caplog` at DEBUG, a capturing Telegram transport and a dump of every store table, a marker plaintext appears in none of them. Neither do the JWE or the private key bytes. The update creates no conversation message or Work.
- **Work mode on.** The Mini App button is not sent; the Mac page is.
- **Page tests.** The page loads no external resource (static check of `site/secret-input/v1/index.html`), and its CSP meta is the one above. A Node or browser test, run locally only like the existing real-WebKit tests, checks that the page's token decrypts in Python.

## Rollout

- The page must be live before Still sends a button that points to it. `deploy-pages.yml` runs only by dispatch, so the implementation PR lands the page under `site/` and keeps the Mini App path off until the deployed page is confirmed. One way is a pinned page version constant that matches a published `version.json` beside the page. The PR states how the owner sees which path was used.
- Fallbacks stay: when the Mini App path is unavailable, Still uses the Mac-only page, as #520 does today.
- The Google client JSON page (`google-client`) is out of scope. It takes a file, which `sendData`'s 4096 bytes and the JWE overhead do not reliably carry. It keeps its tunnel.

## Decisions for the owner

1. **Tunnel for API secrets.** Recommended: when Telegram is paired, the Mini App replaces the tunnel for API secret input, and the tunnel stays only for the Google client page. Alternative: keep the tunnel as a second fallback after the Mini App.
2. **Bridge script.** Recommended: a pinned, reviewed copy of `telegram-web-app.js` under `site/secret-input/v1/`. Alternative: a minimal inline bridge (Build).
   *Implemented (2026-11-23):* the pinned upstream file (sha256 `baa788a6f686f4238db1b82ce8e76470930af7411b18b521ebb95a2c1480a267`, Last-Modified 2026-11-09) carries no licence, so it is not vendored. `site/secret-input/v1/page.js` holds the minimal bridge that follows that revision's `postEvent`, with the web client's `postMessage` restricted to `https://web.telegram.org`.
3. **Forks and self-hosting.** The page URL is fixed to this repository's Pages site, so every install trusts this site. A configurable page base URL would let a fork host its own, but it would make the URL a setting that controls where secrets are typed. Recommended: keep it fixed for now.

## Non-goals

Remote sign-in (it needs a live page), the Google client page, Slack or Mattermost surfaces, and any change to how stored secrets are used.
