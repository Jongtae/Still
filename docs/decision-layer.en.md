# Provider-independent Decision Layer

## Current rule index and precedence

Still owns the judgment interface, its callers and the validation of their answers. A provider supplies inference; it does not acquire authority over owner state or external actions. This document consolidates the adopted rules rather than requiring readers to replay amendments.

| Responsibility | Controlling rule |
| --- | --- |
| Semantic judgment | Use model-backed interpretation for language, intent, reference, relevance, planning and recovery. Keep exact authority, protocol and effect checks deterministic. |
| Planning | Preserve the owner's request and the full policy-eligible toolset. Supplementary notes may help the worker; they do not replace the request. |
| Outcome | Evaluate the answer against the request and recorded observations. A favorable judgment cannot override a pending approval, an unresolved state-changing failure or an unknown effect. |
| Routing | Distinguish the Work executor from the Judgment AI. Configuration, activation, qualification and observed operation are separate states. |
| Qualification | Bind checks to the actual route, model, operation and installed runtime. A historical version list or a passing typed probe is not qualification for every role. |
| Recovery | Preserve the Work budget, owner corrections and no-replay boundary. An unavailable judgment is not permission for a different provider or a semantic keyword fallback. |

The [Secretary Agency Contract](secretary-agency-contract.en.md) controls the pilot's egress policy; the [Memory contract](engineering/contracts/memory.en.md) controls writers, notices and correction. The [execution contract](assistant-execution-contract.en.md) controls effect and recovery truth; [Presence](presence-experience-contract.en.md) controls the owner-facing experience. Development selection remains in [delivery-plan.yaml](../delivery-plan.yaml).

## Status and purpose

This is an implementation and acceptance contract, not proof that every route works on an owner's account. The editorial baseline is `99fad1598b75837cf4428cfa43ad810266e5dff7`. The [pre-consolidation text](https://github.com/Jongtae/Still/blob/99fad1598b75837cf4428cfa43ad810266e5dff7/docs/decision-layer.en.md) preserves dated decisions, diagnostics and original evidence. References to predecessor issues keep their original repository meaning.

## Why this boundary exists

Choosing a relevant fact, resolving a correction and deciding whether an answer served a request are semantic judgments. They should not accumulate into request-specific keyword trees. Conversely, permission, approval binding and observed external effects must not become whatever a model finds plausible.

The boundary separates judgment, authority and execution while allowing the judgment provider to change. It does not require a new Context store, Work lifecycle, BDI runtime or multi-agent framework. The conceptual rationale is in [Judgment and execution](concepts/judgment-and-execution.ko.md).

## Decision is not authority

A decision cannot mint or widen a Grant, issue an approval, create an external destination, rewrite Evidence or certify an unobserved effect. Confidence is not permission. A proposal to remember something still passes the applicable writer and Memory rules; this does not reintroduce a per-fact judgment for the owner's direct-save path.

Deterministic enforcement keeps exact resource/action/scope checks, approval digests, revocation, idempotency, protocol validation and effect truth. The model chooses among already eligible options. Missing evidence remains missing even when the answer is fluent.

## Neutral Still vocabulary

The concrete Python interfaces are in [decision.py](../src/personal_agent/decision.py), not a separate illustrative TypeScript API.

| Interface | Purpose |
| --- | --- |
| `DecisionContext` | Bounded attributable caller input, including the applicable admission/budget context. |
| `BinaryDecision`, `SelectionDecision`, `SelectionSetDecision`, `ScoreDecision` | A typed answer to the declared question, with confidence and explicit non-answer outcomes. |
| `StructuredDecision` | A caller-schema answer, validated before use. |
| `DecisionEngine` | Provider-neutral judgment interface. |
| `DecisionPolicy` | Still-owned acceptance of the returned shape, candidates and confidence; never delegated authority. |
| `RoutedDecisionEngine` | Resolves the configured judgment route without adding private context of its own. |

`provider_unavailable`, `timeout`, `malformed`, `context_rejected` and `cancelled` are explicit outcomes. A low-confidence `decided` answer can still be reduced to unknown by policy. A fixture engine is for tests, not the production fallback for language understanding.

## Replaceable implementations

Core depends on the neutral interface. Provider SDK types and provider-specific vocabulary stay inside adapters. A route receives only the context the caller supplied, bounded by the applicable caller/adapter limit; it does not add history, Memory, files or connector content on its own.

## DecisionEngine routes (#580)

### Routes

| Route | Implementation and boundary |
| --- | --- |
| `direct_api` | `ModelDecisionEngine` uses the existing model adapter. The selected configuration determines provider, model and key. The historical default is not an architectural requirement. |
| `subscription_cli` | `SubscriptionCliDecisionEngine` uses the bounded CLI path, with the no-tool Judgment profile and runtime qualification below. |
| `jev` | Optional `JevDecisionEngine`; explicitly configured and activated, never a startup dependency or implicit fallback. |
| `off` | `UnavailableDecisionEngine`; no provider call. |

Main AI, Judgment AI and the assistant's speaking identity are distinct roles, not necessarily distinct providers. The Work route and decision route retain separate configuration and Evidence. A cloud profile remains limited to the engine's documented variable allowlist and its daily call cap; Work and Judgment calls both count.

### Configuration and owner actions

Saving a credential stores only the credential. A login check or model listing is not activation. `decision_route` changes only after the applicable successful activation/qualification; removing an active decision-only key leaves the route needing attention rather than borrowing a Work key.

There is **no cross-route fallback** for an unavailable active Judgment route. Legacy default resolution when no route row exists must be displayed as the effective fallback with its reason, not misleadingly as a working subscription or follow route. `DecisionRoutes.status()['effective']` distinguishes active, fallback, attention and off.

Follow mode is the default preference for a fresh installation. A follow row identifies the Main AI for which it was qualified. A row bound to an earlier Main AI remains unavailable until successfully resolved for the new one. An explicit or off Judgment choice is not changed by a Main AI switch.

After the owner's Main AI activation passes its own probe and commits, follow-mode qualification is queued through `decision_qualification_jobs`. Explicitly choosing follow mode queues the same process. Unsupported/no-Main preflight refusals make no provider call. There is at most one open job per follow route; a newer switch supersedes an older job. Jobs bind the route revision and chosen Main AI, run off the foreground work thread, and check those bindings again before committing. Failure, cancellation or supersession leaves the prior row unchanged. A queued cancellation stops immediately; a running one stops at its next case. An interrupted job is requeued once only while its Main AI is still current and followed; otherwise it ends interrupted.

Concurrent explicit activation is refused. Opening Settings only reads configuration; it does not launch a process, qualify a model or contact a provider. A model-refresh action is an owner-session, same-Origin POST; missing or cross-origin Origin is refused. Model lists and credentials are not placed in prompts merely to render Settings.

### Subscription model policy

| Policy | Selection rule |
| --- | --- |
| `fastest_qualified` | Default for follow and separate selection since Still #472. Run every candidate through the suite and choose among those that pass using the bounded latency/quality rule below. |
| `lowest_qualified` | Retained explicit option. Try the ranked candidates in order and use the first that passes. It is no longer the default policy. |
| `explicit` | Use the owner's offered/entered model only after the full applicable suite. A missing model flag, refused model or failed qualification leaves the previous route unchanged. |
| `engine_default` | Send no model flag. This means the isolated CLI's default, not the owner's ignored configuration file. It is offered, not silently preselected. |

For `fastest_qualified`, measure complete judgment-call latency with a monotonic clock. A qualified candidate whose p95 exceeds 1.5 times the best qualified p95 ranks behind candidates within that guard. Compare p50 next; within 10% of the fastest p50, use pass rate, then score, then the recorded rank as tie-breaks. Price influences that last rank, not a separate request-time routing rule.

A semantic case that receives an answer but fails policy can run up to three times, stopping when two runs agree. A timeout, authentication error, rejected model or guard failure is not semantic variance and is not repeated by this rule. `lowest_qualified` and `explicit` retain one run per case. Record actual calls and retries; do not advertise a fixed total that ignores repeated cases. Qualification runs outside the request path under the existing job controls.

Stored routes are not silently rewritten by an editorial or candidate-list change. A follow preference uses the current follow policy on its next authorized/automatic qualification; an explicit policy remains explicit. A legacy follow result can be shown as needing a fresh check rather than falsely described as selected by today's policy.

### Judgment model policy (SEC-JUDGE-01 / #679)

Ranked candidates, aliases and current model descriptions are data in [decision_routes.py](../src/personal_agent/decision_routes.py). Do not maintain a competing list of contemporary model names in this contract. Availability in a catalogue is not proof that the account can run the model.

Codex discovery uses the installed binary's bundled catalogue with an empty per-call `CODEX_HOME`, never the command that updates the owner's shared model cache. Claude Code uses its supported aliases. Direct-provider discovery uses that route's saved credential and official model-list path on explicit action. An owner can still enter a model identifier where the route supports it; it must pass the applicable check. Listing failure preserves both configured and active choices.

Low effort is requested only where the model and installed CLI declare support. A missing flag is reported, not emulated with owner configuration; an unsupported explicit effort is refused before a call. Work-model selection is separate: the selected subscription Work model is passed explicitly and recorded. An isolated sidecar whose closed request contract has no model field refuses a non-empty unsupported setting rather than silently using another model.

<a id="strongest-model-judgments"></a>
**Strongest-model judgments.** A caller such as dream may request `DecisionContext(strongest=True)`. Resolve the strongest supported model from the route engine, not a pinned model name. Use a model flag only when supported and not for a cloud profile; request high effort only where supported. A provider rejection of that model may fall back once to the route's own model, with the answering model recorded. This is not permission to switch providers or accounts. The same isolation, admission and output checks apply. Dream's run window and the documented timing discrepancies belong in the [Memory contract](engineering/contracts/memory.en.md).

## Qualification suite

The recorded suite is `decision-qualification/4` in [decision_qualification.py](../src/personal_agent/decision_qualification.py): eleven synthetic cases covering follow-up retry, correction, reference, a new topic, ambiguous abstention, declared-candidate selection, no invented candidate, withdrawal and truthful failed/partial/unknown projection. Cases use the production judgment seam or the declared selection/policy boundary. Every required case must pass; provider failure is not a pass. Changes to cases require a suite-version change.

This suite covers **judge and choose**, not arbitrary structured planning or score calibration. Passing it does not qualify a route for those untested operations. Explicit direct-API/subscription model activation uses the full suite; the existing engine-default initial probe is narrower. Automatic update requalification uses the full suite even for engine-default. Keep these evidence levels visible.

## Authority and threat model

Only the caller-built, redacted context and question/schema go to the configured destination. Follow mode may change the Judgment destination as part of the owner's explicit Main AI switch; that action must disclose both roles' destinations. No successful plan can add a new destination or bypass the current pilot's information-use audit.

Keys remain in the private credential store and are omitted from read models, prompts, logs and Evidence. The CLI owns its official login. Still does not copy a Codex login file to make a separate decision profile. Secret redaction remains mandatory even where earlier private-read/search separation was explicitly removed.

### Subscription isolation

A judgment runs in a fresh per-call directory used as its HOME, with a restricted environment and path, no shell launch by Still, a separate process group and a timeout that ends that group. The Still tool bridge is not attached to the Judgment turn. Required CLI flags are checked as complete flags rather than by substring.

Codex uses the strict permissions profile, not the broadly readable trusted-local Work profile. Its feature plan disables every non-removed feature outside the verified allowlist, including unknown/default-enabled tool-bearing features; an unparseable plan fails closed. User configuration, exec-policy rules, project documents, web search and optional tool/context features are suppressed by the qualified launch. Feature names alone are not proof of an empty tool surface: the local runtime observation below is required. The strict sandbox still has its declared minimal OS/tmp read surface and is enforced by the CLI itself.

Claude Code's no-tool launch uses the restricted, strict-MCP, empty settings-source and no-session-persistence path, with the documented instruction/auto-memory exclusions. A historical help listing or environment-variable name is not a new run observation. Preserve the exact launch and tests in [bounded_execution.py](../src/personal_agent/bounded_execution.py) and the qualification implementation.

**Codex global instruction residual.** `project_doc_max_bytes=0` does not establish that global instruction files are absent. The recorded `$CODEX_HOME/AGENTS.override.md`, or otherwise `AGENTS.md`, can reach the model under the qualified launch. Treat this as untracked, untrusted owner-authored input at the already selected destination. Still checks file presence/size for the warning, not content. A schema-valid judgment still cannot grant authority. The three instruction-file states remain part of local qualification; do not erase this residual while shortening the historical observation.

### Automatic installed-CLI qualification — #346

Installed updates are admitted by the local qualifier, not membership in a source-code version allowlist. `tested_versions` and `CODEX_INSTRUCTION_FILES_QUALIFIED` retain manual observations only. Tested platform and runtime-root exclusion remain admission gates.

The local isolation check uses synthetic HOME/CODEX_HOME, a synthetic store, canaries and a scripted model on loopback. It checks readable turn space, denied protected/sibling space, feature exclusions, absence of host tools, the applicable real local MCP round trip, and Codex's override/regular/no-global-instruction states without skill/plugin canary leakage. A crash, timeout, missing request, bind error, unparseable output or a nonzero exit without an observed permission denial refuses admission. These checks use no owner credential or external provider.

Successful `qualified_identity` records are bounded to eight in-process entries. They bind route scope, resolved launcher/native identities, size, nanosecond mtime, inode/device and platform/runtime/store/home/profile paths, with both digests and observed version. Identity must remain stable through qualification. Failures are not memoized as admission. A Work qualification may cover narrower Judgment checks; a Judgment pass never qualifies the Work bridge. A strict Work never silently downgrades to trusted-local. The recorded residual for a replacement preserving all cache identity fields remains detection on restart, not every cache hit.

A changed binary/native identity or cloud profile also queues one production behavior qualification through the existing job mechanism. Judgment stays unavailable until the applicable suite passes. Follow mode keeps its policy and explicit mode its model; no other engine/account/profile substitutes for a failure. The job binds route revision and target identity, checked again before commit. Calls coalesce into that job. A failed identity stays failed across restart until identity changes or the owner explicitly retries. Settings can read a result without running probes; successful requalification quietly replaces the stale record.

The production behavior suite calls the chosen engine and may consume its allowance. It is different from the local loopback isolation check. Unit tests inject it; installed-CLI observations require the opt-in process harness and their own recorded revision.

Claude's stream-input compatibility retry is limited to a fast nonzero usage error naming the unsupported input flags, before any result or assistant/tool-work record. Retry plain input once and remember the limitation only for that binary. Slow/provider errors and errors after work do not replay. An unacknowledged steer returns to the existing continuation guard. Codex's one-shot path is not converted into a stream session by this rule.

## Orchestration (ORCH-01 / #710)

### Plan and worker context

The common plan fields are worker, model, effort, supplementary `brief.notes` and reason. The production schema may also carry bounded professional-lens, first-plan preview and location-handoff metadata where offered; use [orchestrator.py](../src/personal_agent/orchestrator.py) for the exact fields and bounds. The compact shape is not a second schema.

The owner message reaches the worker verbatim with the profile, current context, recent conversation and prepared answers. `ALWAYS_SECTIONS == SECTIONS`: the plan does not select away those sections. Notes must not restate, narrow or extend the request. The removed `brief.goal`, `brief.context`, `brief.completion_criteria` and tool-subset fields are not current interface requirements.

The catalogue is built from configured routes and recorded observations, without credentials. Only already connected, verified and currently runnable choices are offered. While the owner's Main AI can run, vary its model rather than choosing another vendor; another connected worker is offered when the Main AI is unavailable or its run failed in the Work. This is distinct from the older permanently pinned-worker rule.

Each attempt retains the worker's full toolset eligible under policy. The plan cannot invent a tool or use the former private-read/search split to remove tools. Model/effort choices are validated against the actual catalogue. A specific unsupported-model refusal is recorded without its text and expires after seven days; a generic failure mentioning a model is not such a refusal. A refused configured default resolves to an explicit runnable substitute, or the worker becomes unavailable.

Observed catalogue timings come from attributable terminal CLI events, including failures, paired with their matching start. Keep sample size, task-size/tool-call counts and reported usage alongside latency. Missing or unattributable data is not zero. These observations inform model judgment; they are not a deterministic task/category routing rule or a promised completion time.

### Owner intervention

An eligible first plan can ask for missing current location through the existing owner-input path before starting the worker. It is not offered on a location continuation or a re-plan. Failure to send the question does not create a fictional handoff. A successful continuation retains its bound route and is resumed once without re-judging the same capability.

A long-work preview is sent once when the model selects it, before the worker starts. The worker and steer judgment see what the owner saw. Mid-work corrections must reach later plans, briefs and evaluation, not only the running tool loop. Preserve actual delivered/undelivered steer state and the bounded continuation rules; receiving a correction is not evidence that the worker applied it. Presentation is governed by [Presence](presence-experience-contract.en.md).

### Evaluation and bounded revision

One outcome judgment evaluates whether the answer served the request, using the owner's words, recent conversation, owner corrections and recorded tool observations. A necessary question can serve a request without pretending the external task is complete. The superseded second `owner_input_needed` call and criteria-recheck chain are not restored.

The final answer is labelled model-stated, not an observation. Evaluate it within the delivered-answer bound, against bounded recorded evidence. The CLI path includes mediated page text, code output and supported effect-free read results; the caller preserves room for substantive observations rather than letting step labels crowd them out. A native search that exposes only its query is looked up but not independently verified. It never proves a saved, booked, sent or changed effect.

A negative explained judgment can supply one short gap sentence to the next plan and the owner-facing closing note. The judgment audit keeps the boolean rather than the answer text; the Work's evaluated event carries the bounded explanation. An unsupported claim is not made true by a favorable score. Pending approval, unresolved state-changing failure and unknown-effect checks continue to constrain final status.

The normal loop allows at most `MAX_REDELEGATIONS = 2` after the first attempt, within one WorkBudget. No re-delegation follows an effect-bearing or unknown attempt, an owner-input/approval wait, an unavailable evaluation, insufficient budget or the existing terminal steer-continuation guard. The next plan reads factual earlier-attempt summaries and failures; model answer excerpts remain labelled. Identical worker/effective-model/effort/brief-and-context signatures are refused. A changed brief or context can revise on the same route; that does not authorize replaying an external action.

An attempt that remains not reached is not stored as succeeded merely because no attempts remain. Preserve the observed partial/failed/unknown distinctions and the current result projection. A bridge call left running without a result becomes `tool_incomplete`; its effect is unknown unless it was an effect-free read. Unexpected bridge exceptions return a typed failure without fabricating completion. Tool deadlines are bounded by the Work's remaining time and the offered tools' actual bounds, not an unqualified old timeout example.

### Fallback and delivery

When a plan is unavailable, malformed, unconfident or invalid, the existing fallback sends the raw request to the owner's default Main AI without an invented brief/model choice or re-delegation. Record why. If working orchestration becomes unavailable, say so once; do not announce a false outage on an installation where it never worked. This fallback is **not** a silent change of the active Judgment provider, account or destination.

An available AI answer is delivered with its truth-qualified outcome; uncertainty is not hidden by fluent wording. The explicit unknown-external-effect statement takes precedence. The [execution contract](assistant-execution-contract.en.md) owns the terminal-state rules, including conditions where further judgment or action cannot run.

## Work budget and provenance

`RoutedDecisionEngine` attaches host admission to `DecisionContext`. A supported adapter calls `context.admit()` once immediately before dispatch. Configuration/shape/size/unsupported preflight failures do not spend a provider-call allowance; a dispatched failure does. Failed admission makes no provider call.

Scope follows joined early tasks, connection/approval continuation and canonical request migration. Reaction threads use the existing-only ledger bridge and cannot open a new allowance or spend a resumed run's budget. Presentation judgments use the separate presentation counter. Reserve the final work turn for outcome judgment; a re-plan starts only when its plan, worker and evaluation can fit. Caller and routed engine do not charge the same call twice. A background source Work ID is attribution, not a new foreground budget.

`planned`, `fallback` and `evaluated` events identify attempts, validated worker/model choices, brief digests, bounded reasons and stop/redelegate disposition. Brief text is not persisted merely because it was sent. These are orchestration records, not additional executed tool steps. Route audits distinguish requested from observed model identity and say not reported when absent. CLI failures may record exit/provider status and a redacted reason, never unrestricted CLI text or a prompt echo. Hidden reasoning is not an audit requirement.

## Jev adapter boundary

Jev remains an optional adapter, not a kernel vocabulary or orchestration dependency. Its typed operations map at the boundary: `noul` to a binary answer and chosen-side probability, `choice` to declared candidates plus none-of-these, and `score` to the declared scale mapping. Unsupported selection-set/structured operations return unavailable rather than pretending compatibility.

Explicit Jev discovery uses the saved key and the provider's model-list interface; a listed name is unverified. Saving a model does not replace an already activated choice. Jev activation performs its existing one-probe check and records no full-suite qualification. Display that distinction. Owner-account operation, Korean quality and comparative latency remain separate observations, not consequences of implementing the adapter.

## Provider evaluation

Compare only operations and roles both candidates actually support. Freeze the relevant question/rubric, expected behavior, suite version and environment before comparison. Record accuracy, abstention/errors, confidence calibration, p50/p95, observed usage/cost, failures and practical credential/egress/maintenance costs. Vendor speed or price claims are not Still measurements; confidence scales are not automatically comparable.

A judge/choose pass does not qualify score or arbitrary structured planning. New score cases need their own declared scale and expected mapping; structured orchestration needs eligibility, schema, recovery and evidence checks. Do not narrow the assistant's agency to fit a specialized endpoint. Promotion or removal requires the relevant owner decision and evidence for the claimed role. Live trials are opt-in and budgeted under the repository's verification policy, not triggered by reading this document.

## Reuse-first implementation rule

Adopt maintained provider mechanics where suitable, adapt them behind the existing neutral interface, and build only the missing Still-owned boundary. Keep Grants, Memory, Work, Evidence, egress and revocation outside the dependency's authority. A new semantic phrasing should not require another product-rule branch.

## Implementation status

Relevant implementation sources are [decision.py](../src/personal_agent/decision.py), [decision_routes.py](../src/personal_agent/decision_routes.py), [decision_adapters.py](../src/personal_agent/decision_adapters.py), [decision_qualification.py](../src/personal_agent/decision_qualification.py), [cli_qualification.py](../src/personal_agent/cli_qualification.py), [conversation_handoff.py](../src/personal_agent/conversation_handoff.py) and [orchestrator.py](../src/personal_agent/orchestrator.py). A symbol's existence is not a live-quality result.

Historical capability-routing rules, Calendar draft examples, per-fact owner-memory judgments, private-search separation, old model defaults and manually qualified CLI versions are not a current request-path specification. Current call sites and scoped regression evidence must be inspected when changing those areas. The explicit preparation-acceptance and same-goal judgments retain their applicable authority/no-window protections; no wording change in this document grants a schedule or external action.

## Non-goals

This consolidation installs no provider, changes no runtime or permission, selects no delivery goal and makes no new performance claim. It does not introduce a semantic rule engine, additional state store, mandatory System-One provider or BDI state machine.

## Historical references

The old text remains at the pinned baseline above rather than being copied into another active archive. The following legacy anchors lead here so old issue and guide links remain navigable. They identify historical sections, not instructions to reactivate them.

<a id="pilot-posture--653-2026-09-26"></a>
<a id="amendment--826-2026-09-28"></a>
<a id="amendment--710-2026-09-27"></a>
<a id="candidate-uses"></a>
<a id="relationship-to-attention-and-bdi-inspired-design"></a>
<a id="external-provider-boundary"></a>
<a id="verified-capability-sources"></a>
<a id="provider-comparison-and-selection-reassessment-2026-10-10-609"></a>
<a id="capability-compatibility"></a>
<a id="alternatives-and-evidence-classification"></a>
<a id="comparison-protocol-and-promotion-gate"></a>
<a id="staged-implementation"></a>
<a id="stage-a--contract-done-in-417"></a>
<a id="stage-b--narrow-integration-first-point-done-in-417"></a>
<a id="stage-c--provider-experiments"></a>
<a id="stage-d--evidence-based-promotion"></a>
<a id="related-work"></a>
<a id="historical-codex-01621-compatibility--1561-2026-10-15"></a>
<a id="historical-codex-01640-compatibility--344-2026-10-26"></a>

[Original decisions and observations](https://github.com/Jongtae/Still/blob/99fad1598b75837cf4428cfa43ad810266e5dff7/docs/decision-layer.en.md) · [Documentation map](README.md)
