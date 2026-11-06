# Connect an AI, messenger or Google account

[Quick start](../QUICKSTART.md) · [Operations](operations.en.md) · [Company networks](workplace.en.md) · [Product status](../product-status.en.md)

A configured connection, a successful check and an observed task are different facts. Keep the currently working configuration until the exact edited settings pass their check. Changing settings is not permission to send information or perform an action beyond the selected connection's scope.

## Other model connections

Still supports direct OpenAI and Anthropic API connections, OpenAI-compatible Chat Completions endpoints and an already running Ollama server, in addition to subscription CLIs. Choose the provider, endpoint and model, test that configuration and apply the successful draft. A test may contact the provider and incur usage. Keys belong in dedicated secret inputs, never ordinary conversation text. Editing a direct API configuration does not itself switch an active subscription route; inspect the selected AI.

External AI processing is not local-only. The request's information-use record identifies the selected worker and the context used. For company gateways and cloud credentials, use the [workplace guide](workplace.en.md#company-cloud-ai).

## Telegram

Create a dedicated bot with Telegram's BotFather (`/newbot`), without an existing webhook. Ask Still to connect Telegram and enter the token in its hidden input, or use Settings → External connections → Telegram on this computer. Saving the token produces a pairing link. Open it in your own Telegram account and press **Start**. Only the paired private account may submit work.

Still uses outbound polling; Telegram does not require a public inbound port. At connection, Still sets the default bot name, picture and introduction once. Later changes made in BotFather are left alone. A chat background is a Telegram app setting, not something the bot can change.

The host must stay awake. Keep the foreground terminal open or explicitly register the [macOS login service](operations.en.md#background-service-macos). Pairing does not start a service at login.

## Mattermost

Still connects out to a Mattermost server and polls the owner's direct message with a bot. It opens no callback port. Ask the server administrator for a bot account and token. In local Settings → External connections → Mattermost, enter the HTTPS server address, bot token and your username. For a private address, declare the exact [intranet host](workplace.en.md#intranet-addresses) first.

Only the configured owner's posts in that direct message are read. Channels and other users are not read or answered; editing a post does not create another request. The surface supports a working reaction and typing, but no live drafts or buttons. Reply to, or react on, the specific proposal to accept or decline it.

**Exact action approvals, including payments, are not accepted on Mattermost.** Use the local approval surface named in the prompt. A reply known not to have arrived may be sent later; one that may have arrived is not replayed. In work mode, Mattermost stays paused unless the owner explicitly marks the server company-approved. Messages suppressed by the pause are not sent afterward.

Evidence includes fixture tests and a local Mattermost 11 preview-server observation of pairing, replies, reactions, ignored edits/other users and proposal handling. That is not validation on a company server.

## Slack

Still uses the official Slack SDK and an outbound Socket Mode WebSocket. It needs no public request URL or tunnel.

1. At [Slack's app console](https://api.slack.com/apps), create an app from the manifest shown in Still's local Slack settings. The manifest configures Agent Experience, Socket Mode, direct-message and reaction events, interactivity and the required scopes.
2. Install the app in the intended workspace. Copy its Bot User OAuth Token (`xoxb-…`). Create an app-level token (`xapp-…`) with `connections:write`.
3. Enter both tokens and your Slack member ID in Still's local Slack settings. Your profile's **Copy member ID** action supplies the ID.

The manifest requests `assistant:write`, `chat:write`, `im:history`, `im:write`, `reactions:read` and `reactions:write`, and subscribes to `message.im` and `reaction_added`. For an existing app, apply the manifest and reinstall it; enabling Agent Experience alone does not grant a scope. Update the stored bot token if Slack issued a different one.

Only the paired member's direct messages in the configured workspace are accepted. Other members, channels and workspaces are not read or answered. Edits and deletions are not new requests, and duplicate events are accepted once. Thread replies remain in their thread; observed, redacted wait status may appear there. Ordinary direct-message replies are not rewritten as live drafts.

Proposals can carry approve/decline buttons. Only the owner can settle the bound proposal, at most once; a reply or reaction can also be interpreted against it. Unlike Mattermost, Slack supports exact action approvals, including payments. Document-sharing/context approvals and browser sign-in still use the required local surface. Uncertain sends are not repeated.

In work mode, Slack disconnects unless explicitly marked company-approved, and suppressed messages are not sent later. The 2026-10-25 observation used the owner's test workspace; fixture transport checks and that observation do not establish operation in a company workspace or validate every release.

## Google Drive, Gmail and Calendar

Still's own Google connection is optional and uses an OAuth client you control. An AI provider's connector is a separate route: having a connector in another application does not prove that Still can invoke it with the same scope. Inspect the route and capabilities shown in Settings.

### Create and connect your client

In [Google Cloud](https://console.cloud.google.com/), create or select a project, enable the Drive, Gmail and Calendar APIs you need, and configure its OAuth audience and requested scopes. Follow Google's current [production-readiness guidance](https://developers.google.com/identity/protocols/oauth2/production-readiness/overview), including any applicable consent, branding, verification and organization requirements. Do not invent a privacy-policy URL or treat publishing status as a verification exemption.

For Still's shared-client route, create a **Desktop app** OAuth client and download its JSON. In local Settings → External connections → own Google client, upload or paste that JSON into the dedicated client form. Alternatively, set `AGENTOS_GOOGLE_CLIENT_FILE`; that environment file takes precedence over a saved client. Connect each service separately from this computer and review Google's consent screen. A phone cannot reach the host's loopback callback.

Google's [OAuth documentation](https://developers.google.com/identity/protocols/oauth2) explains that an external app in Testing normally receives a seven-day refresh token for these non-profile scopes. Personal-use apps may qualify for a verification exception, but a warning and a user cap can still apply; the [exception conditions](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification#exceptions) are not a promise that every unverified app is unrestricted. Continue through an unverified-app warning only for a client whose identity, owner and requested access you understand.

### Read and write are separate grants

| Connection | What it permits in Still |
| --- | --- |
| Drive read | Search and read Drive content. Google Docs, Sheets and Slides are returned as text; supported downloaded documents are extracted locally. It does not authorize Drive writes. |
| Gmail read | Search and read mail. It does not authorize sending or mailbox changes. |
| Gmail modify | Separately connected, opt-in mailbox organizing, introduced in v0.3.0. Sending remains unavailable. |
| Gmail filters | Separately connected, opt-in filter creation, introduced in v0.4.0. A forwarding filter is refused. An existing modify grant does not silently become this grant. |
| Calendar read | Read events and the calendar list under the granted read scopes. |
| Calendar write | Create, change, move or delete events through the allowed Calendar API paths. Still forces `sendUpdates=none`; it does not send attendee invitation/update emails. |

Read access never becomes write access. For the shared-client setup, the local entry points are `/google-drive-connect`, `/google-gmail`, `/google-calendar?grant=read` and `/google-calendar?grant=write` on `http://127.0.0.1:8787`. Use Settings for the additional Gmail grants and inspect the actual granted scopes.

The old Calendar draft-and-approval flow was removed in v0.3.0. With a valid Calendar write connection, the owner's AI now makes the supported changes without a separate per-change approval. It does not share calendars, create/delete calendars, import events or register watches. A refused change reports the provider's reason. Do not follow historical instructions that promise every calendar change will stop at a draft preview. The [release manifest](../release-manifest.json) records this transition and the limits of live evidence.

### Renewal, replacement and data handling

Still renews short-lived access tokens using the stored refresh token. A temporary network or provider failure keeps the connection and permits a later renewal; a refused grant requires reconnection. Replacing the shared client takes effect after a restart and requires reconnecting its services. Remove a saved client only after disconnecting the services that use it; remove an environment-supplied client by removing that configuration. A separately configured per-service client takes precedence for that service.

Client and token material stays in dedicated local stores; no shared Still server brokers the connection. Encrypted stores depend on the platform keyring or the supported separately managed key. See [platform notes](platforms.en.md) and the applicable configuration path. This does not mean Google content stays local when an external AI answers a request: relevant content can be sent to the chosen AI. Review the information-use record and the current [connection contract](../connection-delegation-contract.en.md).

### Google Calendar (source checkout)

The legacy per-service configuration uses a **web** OAuth client with redirect URI `http://localhost:8787/oauth/calendar/callback`, not the shared Desktop-client procedure above:

```sh
agentos calendar-config \
  --oauth-client-json ~/Downloads/client_secret_XXXX.json \
  --secret-file /Users/your-name/.agentos-secrets/calendar.json
AGENTOS_CALENDAR_LOCAL_ONLY=1 \
AGENTOS_CALENDAR_SECRET_FILE=/Users/your-name/.agentos-secrets/calendar.json \
  agentos start
```

The credential file must be absolute, owner-only (`0600`) and not already exist. Configuration does not connect an account. From the local owner session, connect the separate read/write grants; tunnel hosts are refused. The supported event operations and no-attendee-email rule above apply. The historical per-service fixture checks do not establish a live OAuth run; the owner's 2026-10-09 shared-client setup was a different path.

### Gmail (source checkout)

For the legacy per-service read-only path, use a web OAuth client with redirect URI `http://localhost:8787/oauth/gmail/callback`:

```sh
agentos gmail-config \
  --oauth-client-json ~/Downloads/client_secret_XXXX.json \
  --secret-file /Users/your-name/.agentos-secrets/gmail.json
AGENTOS_GMAIL_LOCAL_ONLY=1 \
AGENTOS_GMAIL_SECRET_FILE=/Users/your-name/.agentos-secrets/gmail.json \
  agentos start
```

The file must be absolute, owned by you, `0600`, outside the Still data directory and not already exist. The command generates the encryption key locally; do not put the secret or key into command-line arguments. The equivalent `AGENTOS_GMAIL_CLIENT_ID`, `AGENTOS_GMAIL_CLIENT_SECRET` and `AGENTOS_GMAIL_ENCRYPTION_KEY` settings remain compatibility inputs, but the dedicated file is the documented path.

Connect through local Settings or `/google-gmail` using the owner session. This path requests exactly `gmail.readonly`; it does not inherit the optional mailbox-organizing or filter grants described above. A parked request can resume after the required connection, but creating the credential file alone grants nothing. A phone/tunnel is not a substitute for local consent. Evidence for this legacy path is fixture-based, not a recorded live authorization.

## Sources and evidence limits

The Still behavior summarized here comes from the [release manifest](../release-manifest.json), the [connection contract](../connection-delegation-contract.en.md), and the [pre-edit operating guide](https://github.com/Jongtae/Still/blob/6edc3380b613ab0c2897ba7a913ab658150a68f4/docs/QUICKSTART.md). Google authentication guidance was checked against its official documentation on 2026-11-05. Provider documentation establishes provider behavior, not that a particular Still integration has passed an end-to-end test.
