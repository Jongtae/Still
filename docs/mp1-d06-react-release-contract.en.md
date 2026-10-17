# MP1 D-06 — Integrated ReAct Policy and Release Contract

## Policy

The Personal Assistant Core follows a bounded ReAct loop: interpret an owner outcome, inspect owner-local Personal Space, select only an enabled reviewed capability, observe redacted evidence, and either answer, request approval, or recover. It never treats a model suggestion as permission to invoke A2A or create a Calendar event.

Drive is used only for read-only evidence; A2A runs only after explicit delegation; Calendar produces a local draft and requires the D-05 exact approval. Missing, paused, disconnected, invalid, timed-out, or failed capabilities produce a truthful local fallback with a recovery action, never fabricated results.

## Integrated acceptance

The release fixture creates owner-local Personal Space evidence, obtains a mocked Drive source, explicitly delegates to the compatibility peer, drafts and approves one mock Calendar event, pauses/disconnects a capability, and exports/restores owner state. It asserts source redaction, no secret export, no external write before approval, one idempotent Calendar result, and retained recovery evidence.

MP1 release completion is automated contract/fixture acceptance. Real credentials and external activation are deferred to the owner operating-mode deployment, which is recorded separately and is not a manual per-PR gate.

## Non-goals

No autonomous delegation, Calendar mutation/deletion, email, marketplace, public endpoint, arbitrary runtime, or claim that a real external service is configured.
