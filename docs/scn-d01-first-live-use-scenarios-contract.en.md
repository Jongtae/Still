# SCN-D-01 — First Live-Use Scenarios Contract

## Objective and scope

This contract defines two owner-deliberate Still journeys. It is a design contract only: it specifies a local, attributable, approval-bound path and its implementation evidence. It does not configure a provider, sign in, create a connection, transmit a message, or claim that any external service is live.

Scenario 1 is **research to action**. From paired Telegram, the owner asks Still to prepare for a meeting, optionally approves relevant Google Drive context, receives a cited brief, and may approve one Calendar-event creation draft.

Scenario 2 is **reviewed sharing**. The owner supplies a news article, blog post, video, or its bounded text/transcript, chooses translation or summary, identifies a KakaoTalk recipient or conversation, reviews the exact outgoing text, and separately approves one delivery attempt.

## Scenario 1 — Research to action

The owner sends a meeting-preparation request. Still acknowledges the request without implying provider access, proposes only eligible read-only context with source labels, and waits for the owner to approve the exact context selection. It produces a concise brief with attributable Drive references and a local result reference. If asked to create an event, it renders a canonical Calendar draft (title, time, timezone, attendees, and description) and requires a distinct one-time creation approval.

Missing authorization, unavailable Drive/Calendar capability, expired source, malformed time, ambiguous timezone, duplicate approval, cancelled work, or provider failure produces a named, redacted recovery state. It never silently substitutes sources, sends context to an external engine, or creates an event because a meeting was mentioned.

## Scenario 2 — Reviewed sharing

The owner explicitly provides the content or an allowed, bounded source reference and selects either `summarize` or `translate`. Content is untrusted evidence: it cannot override policy, select a recipient, request tool use, or cause delivery. The generated draft includes source evidence, requested language/format, redaction outcome, and a stable draft reference; it is editable only by an owner-initiated revision that invalidates prior approval.

Recipient resolution is a separate, capability-owned preview. The owner may provide an exact KakaoTalk conversation label or a person name. An exact unique approved resolution may be previewed; zero, partial, or multiple matches must return a disambiguation request without exposing unrelated conversation details. The preview displays the exact destination label, exact immutable outgoing text, and a one-time final-send action. A changed draft, changed destination, expiry, replayed approval, cancellation, unavailable capability, or uncertain outcome invalidates that action.

Implementation must provide an offline fallback: copy/export the reviewed message with the destination label and clear instructions for owner manual delivery. This fallback is not evidence that KakaoTalk received anything. An observed provider receipt may be recorded only when a separately authorized, approved KakaoTalk capability returns an unambiguous delivery result; otherwise the terminal state is `not-sent`, `failed`, or `delivery-uncertain`, never `delivered`.

## Authority, privacy, and external boundary

Still retains local state, source evidence, drafts, approvals, terminal outcomes, and recovery records. It stores no raw credentials in exports or logs and records only redacted audit metadata necessary to prove the state transition. Recipient names, conversation labels, source content, and outgoing text are personal data: they are local by default, bounded in UI rendering, excluded from portable evidence, and never externally shared merely to generate a preview.

The design excludes unofficial KakaoTalk history scraping, background capture, automatic recipient inference, automatic messaging, arbitrary web retrieval, contact enumeration, broad host access, or a sender that bypasses the policy owner. Any future KakaoTalk implementation must use a reviewed, permitted capability and have its own explicit connection, scope, credential, and operating-mode authority.

## State, audit, and recovery contract

Both journeys use owner-and-channel-bound state transitions: `requested` → `context-preview` (when applicable) → `draft-ready` → `action-preview` → `approved-once` → terminal state. Terminal states are `completed`, `cancelled`, `rejected`, `failed`, `not-sent`, or `delivery-uncertain`; no adapter may invent a more successful state.

Audit retains opaque request/draft/action references, source and capability categories, approval and terminal timestamps, and redacted recovery class. It excludes message bodies, recipient identity, conversation history, raw source content, tokens, authorization material, and provider payloads. Restore never replays an approved external action; incomplete or uncertain requests return the owner to a fresh preview.

## SCN-I-01 entry contract

SCN-I-01 may activate only after this contract merges, #301 closes, canonical-contract and plan checks pass, and a new goal-ready implementation issue is created. It must implement deterministic fixtures for owner/channel binding, source eligibility and attribution, summary/translation draft generation, recipient ambiguity, exact-preview binding, final-approval one-time use, duplicate/cancel/expiry behavior, unavailable capability, send failure, delivery uncertainty, audit/export redaction, restore/no-replay, and Telegram/local-companion parity. It must not activate or operate a real KakaoTalk integration without separately authorized operating-mode evidence.
