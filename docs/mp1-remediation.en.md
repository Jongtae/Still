# MP1 Remediation — Product Integration Requirements

## Status

**Status: complete remediation (mock-contract validated).** The original D-01 through I-06 work produced boundary contracts and isolated mock adapters. R-01 through R-05 subsequently merged with their stated automated acceptance, proving the policy-owned personal-assistant product flow. This is development evidence only; operating-mode deployment remains separate.

## Gap analysis

| Requirement | Existing evidence | Missing product behavior |
| --- | --- | --- |
| ReAct assistant | Direct adapter fixture | A policy-owned orchestrator that selects enabled capabilities, applies approval, exposes evidence, and produces fallback/recovery |
| Capability lifecycle | Registry state transitions | Invocation gate, health contract, secret revoke, and export exclusion exercised through real adapter use |
| Drive | PKCE/read adapter mocks | Read/selected-excerpt approval boundary and runtime wiring; expiry/re-auth failure recovery |
| A2A | Direct compatibility-peer adapter | Agent Card rejection matrix, polling/SSE, timeout/cancellation races, minimum-context assertion, and owner-visible evidence |
| Calendar | Direct create-only adapter | Scope/error matrix, exact payload mutation rejection, expiry, foreign owner, timeout/malformed-response recovery, and assistant wiring |
| Core release | One direct fixture | A new-install owner journey through the actual orchestration path, pause/disconnect, and portable export/restore |

## Ordered remediation iterations

| ID | Depends on | Outcome | Automated acceptance |
| --- | --- | --- | --- |
| MP1-R-01 ReAct orchestration | I-06 | Introduce an Still-owned `PersonalAssistantOrchestrator` that classifies a request, reads Personal Space, selects only enabled reviewed capabilities, creates approval-required Calendar drafts, records redacted evidence, and returns deterministic fallback/recovery. | Request matrix: local answer; disabled/paused/disconnected capability; Drive evidence; explicit A2A only; Calendar draft without approval; unknown request. No adapter invocation bypasses policy. |
| MP1-R-02 A2A contract completion | R-01 | Wire the local compatibility peer through the orchestrator and complete Card, progress, timeout, cancellation, artifact, and evidence behavior. | Unsupported/malformed Card; polling and SSE events; timeout; owner/peer cancellation; duplicate terminal event; correlation mismatch; type/size/schema rejection; minimum-context spy; redacted export. |
| MP1-R-03 Calendar contract completion | R-02 | Wire Calendar drafts and exact owner approval through the orchestrator and complete its failure/recovery matrix. | Missing/denied scope; expired/foreign approval; changed canonical payload; duplicate submit; timeout/malformed response; one POST only after matching approval; secret-free evidence/export. |
| MP1-R-04 Drive contract completion | R-03 | Wire Drive search and selected-read evidence through the document approval boundary and complete auth recovery. | Expired token/re-auth; scope denial; selected excerpt only; no engine sharing before approval; disconnect/reconnect; redacted health/evidence/export. |
| MP1-R-05 Core release acceptance | R-04 | Run a fresh owner-local end-to-end flow through the orchestrator and portable-state boundary. | New installation; Personal Space; Drive source; explicit A2A; Calendar preview/approval; enabled→paused→disconnected invocation gate; export/restore excludes secrets and retains evidence; each fallback has one recovery action. |

## Non-negotiable contracts

- Adapter methods are internal boundaries: the assistant and HTTP/Telegram surfaces invoke capabilities only through the orchestrator and lifecycle gate.
- Every state transition, capability call, approval, result, fallback, and recovery leaves owner-local, secret-redacted evidence.
- Mock peers reject undeclared requests and retain an inspectable request log so tests prove minimum context and no hidden external call.
- External provider configuration stays deferred to operating-mode deployment. Mock acceptance is development evidence, never a live-service claim.
- Existing MP1 non-goals remain unchanged: no public URLs, marketplace, arbitrary runtime installation, Calendar mutation/deletion, email, or automatic consequential actions.

## Completion rule

MP1 is development complete only after R-01 through R-05 merge in order, all stated automated acceptance passes, the tracker/ledger records are updated, and the documents no longer contain a contradictory completion claim. Operating-mode deployment remains a separate later configuration step.
