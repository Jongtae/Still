# Connection support and evidence boundaries

This inventory applies the [Connection and Delegation Contract](connection-delegation-contract.en.md)
to CONNECT-01 [#1295](https://github.com/Jongtae/agentos/issues/1295).
It distinguishes supported mechanisms from the evidence available for them. It
is not a connection grant, live operating claim, or replacement for the contract's
discovery and route-choice rules and owner-choice precedence. The decision model chooses the worker;
the owner's AI selects and uses a route. Core preserves owner state and authority.

## Route disposition

| Route | Supported mechanism | Evidence and limits |
| --- | --- | --- |
| Already configured Claude Code or Codex MCP | Discover local names without starting a server; owner-confirmed definitions enter the Work; current per-call permission mediates operations | Injected CLI/bridge and authority tests. Discovery alone establishes neither tools nor authentication. Actual account operation is unobserved. |
| AI vendor connection | Existing Claude connector prefix and per-call permission path; credentials remain with the vendor | Existing connector/permission fixtures. Not a claim that every vendor catalogue, account identity or OAuth scope is visible. |
| Official MCP Registry | Bounded, cached public server-name lookup; source metadata remains untrusted | Registry/relay tests cover outages, malformed metadata, secret rejection and audit. Name substring search is not exhaustive semantic discovery. It installs or authenticates nothing. |
| New public remote MCP through Claude Code | Exact owner-confirmed HTTPS definition; official registration and native desktop login; selected-server status and required tool names; original-Work continuation | Synthetic discovery → actual bridge/Settings/queue → fresh worker → original result. Official CLI/SDK compatibility is separate local evidence. Remote account identity, consent scopes and input schemas remain unknown. |
| New server through Codex | No supported isolated selected-server readiness path | Compatibility work found app-server startup could launch unrelated configured servers. Do not use it as a readiness probe. Existing configured Codex execution is a distinct supported path. Offer an available authorized alternative or state the unmet condition. |
| Plugin-provided MCP tool | May use the reviewed configured-MCP/per-call path | Does not establish full plugin installation, hooks, package execution or a plugin lifecycle. Those are not activated by this program. |
| Public instruction/resource skill | Confirmed pinned content acquisition/enablement; immutable active binding; refreshed continuation catalogue; AI-chosen loading | Skill continuation tests cover original result, changed content and multiple prerequisites. No executable strategies/scripts, generated skills, or authority from content. |
| Still-owned API slot | Contextual static-key or RFC 6749 client-credentials input through Settings and the existing one-time local/phone page; encrypted immutable references; bounded required-read and subject checks; same-Work continuation; money marking floors non-read calls to payment; explicitly nonpayment connections use durable no-replay claims without per-action approval; unclassified legacy connections retain a payment approval floor until explicitly classified | `test_connect_api_effects.py` covers payment callback → queued original worker → verified result, nonpayment authority and durable no-replay/unknown outcomes; `test_connect_slot.py`, `test_connect_api_input.py` and `test_connect_api_oauth.py` cover storage/publication, actual local HTTP submission → readiness → original result, and oauthlib Basic/body token issuance/cache/renewal with synthetic wires. The probe accepts a credential-free public HTTPS URL without query parameters and explicit required JSON fields. OAuth support requires the contract's bounded credential/Bearer/expiry shape; other issuer contracts retain fallback. No live account claim. |
| Existing Still Google connections, signed-in browser and public reads | Existing authorized hands remain candidates under the contract's precedence | Preserve their existing regression/evidence classes. This connection program does not configure or prove live accounts. Native OAuth callbacks preserve the initiating owner and independent waits; current-ready crash recovery, atomic local grants and browser login settlement resume the original Work. `test_connect_native_*`, `test_connect_oauth_owner.py`, `test_connect_local_atomic.py` and `test_connect_browser_continuation.py` cover current authority and original results. |
| Still-native MCP client | Not introduced | A new protocol client is not an implicit fallback. Use an available supported hand or report the exact remaining requirement. |
| Delegated agent/A2A or executable package | Not activated by CONNECT-01 | A connection trust record does not prove delegation/mandate or executable-package support. Existing paused program boundaries remain. |

## What readiness proves

A new remote MCP receipt records observed connection status, required tool names,
observation time and current local definition/trust revisions. Registration or a
login command returning successfully is insufficient. Historical receipts shared
between eligible Works retain the observation time and limitations; they are not
a new health check. Unknown setup is reconciled by confirmed observation without
registration/login replay. Current authority and cancellation are checked again
at admission and continuation.

The status adapter does **not** certify an account, OAuth scope or input schema.
An original request that needs such proof must obtain it through a supported,
authorized operation or use another available route; otherwise that requirement
remains unresolved. A tool being listed cannot certify that a particular call
will succeed. A resumed worker still has to execute and verify the original task;
setup success must not become a substantive-result claim. Provider denials do not
justify switching accounts or bypassing a refusal. Universal account/scope
readiness is not claimed from the existing CLI status interface.

## Requirement-to-evidence map

The named files below are focused model-free fixtures using temporary stores and
injected provider/CLI/model wires. A scripted worker's route choice verifies the
plumbing and observed result; it does not establish live model judgment quality.

| #1299 requirement | Implementing boundary and focused evidence |
| --- | --- |
| Discovery before setup, bounded outage | `CapabilitySearch`, owner-state relay; `test_capability_search.py` validates public lookup, cache, malformed/outage handling and no discovery authority. `test_connect_preferences.py::test_registry_outage_leaves_authorized_generic_fallback_and_original_result` exercises outage through a substantive authorized API result. |
| Unseen service → confirmation → setup → original result | `test_connect_mcp_continuation.py::test_unseen_service_discovery_setup_and_fresh_cli_complete_original_work` crosses the real bridge process, Settings, queue and refreshed CLI configuration. |
| Registration/login is not readiness | `test_registration_or_login_success_is_not_readiness`, `test_authentication_needs_observed_tools_and_keeps_unknown_fields`; `test_mcp_setup.py` verifies isolated status, unsupported engines and unknown account/scope/schema fields. Missing required tools withhold authority and continuation. |
| Current authority and honest recovery | MCP continuation tests cover changed definitions, refusal/money floors, atomic commit rollback, Stop, forget/restore, restart, repeated/late confirmation and bounded known/unknown recovery. |
| More than one waiting Work | `test_shared_setup_runs_each_original_work_to_its_own_result` and surrounding shared-setup tests preserve separate results, cancellation and current revisions. |
| Content skill prerequisite | `test_connect_skill_continuation.py` covers actual apply/queue/fresh binding, substantive result, mixed prerequisites and immutable/content-integrity boundaries. |
| Canonical preference and permitted fallback | `test_connect_preferences.py` covers multiple usable routes, unavailable preferred route, account-bound generic reads, correction/undo/forget, bounded retrieval before planning, and Registry outage. A preference does not create a Grant. |
| No task-specific core implementation | `test_no_scenario_code.py` is a regression guard; diff review still checks C16. Synthetic service names appear in fixture content, not core branches. |

Merged increments and their exact-head CI/review receipts live in #1299 and its
PRs (#1335, #1336, #1339, #1341, #1345, #1347, #1348, #1349, #1352).
This document describes support and evidence, rather than mirroring GitHub status.

## Program acceptance and evidence

The final CONNECT-01 audit applies parent [#1295](https://github.com/Jongtae/agentos/issues/1295)
CC1–CC8 without changing their meaning. #1301 owns this cross-route mapping;
#1296 remains its historical per-call-decision dependency. The evidence below is
model-free: scripted models and synthetic services exercise actual stores,
callbacks, bridge processes, queues and workers. It establishes the implemented
continuation behavior, not live model discovery quality or real-account readiness.
GitHub Issues/PRs/Checks retain execution status and exact-head validation receipts.

| Requirement | Implemented boundary and current evidence |
| --- | --- |
| CC1: durable original request | Settings and exact action receipts retain original Work identity. `test_connect_decide.py::test_read_review_resumes_through_fresh_cli_and_relay_to_original_result`, `test_connect_mcp_continuation.py::test_unseen_service_discovery_setup_and_fresh_cli_complete_original_work`, `test_connect_api_input.py::test_resumed_worker_observes_and_reports_original_result`, and the browser/native/local continuation suites reach original results without a second request. Setup-only skill/MCP proposals create no invented Work. |
| CC2: observed readiness | `test_mcp_setup.py` distinguishes registration, authentication, required tools and unknown account/scope/schema fields. MCP continuation tests refuse registration/login-only success. API input/slot/OAuth suites exercise an unpublished credential's bounded read, subject/required-field/freshness checks and current publication. Native scopes and actual saved browser sign-in evidence are checked; page closure alone is insufficient. Explicitly nonpayment API connections and nonpayment AI effects do not add a per-action approval; unclassified legacy API connections conservatively retain payment approval. |
| CC3: fresh execution and owner choices | The resumed worker rebuilds tools/brief from current grants, connection/trust revisions and skill binding. MCP/decide real-bridge tests assert refreshed configuration; `test_connect_skill_continuation.py` asserts current pinned content and model-chosen `skill_load`, with immutable active snapshots. `test_connect_preferences.py` covers owner preference before planning, both workers, allowed fallback and unchanged canonical choice. |
| CC4: sequential needs and separate requests | `test_connect_decide.py::test_effect_then_read_review_then_payment_continues_only_remaining_work` crosses three real worker turns with the same Work. Trust/skill tests cover multiple prerequisites and exact confirmation scope. MCP/native multi-wait tests preserve independent original results and cancellation isolation; local approval cannot approve a sibling. Existing conversation handoff and `test_steer_running_work.py` distinguish unrelated requests, correction/replacement and delivered in-flight steering. Replayed original input is not a new approval. |
| CC5: remaining obligations and truthful effects | The existing exact action binding, durable dispatch claims, observed-effect context and no-redelegation rule remain controlling. The three-turn hook/relay test above and `test_connect_api_effects.py::test_actual_payment_callback_restarts_worker_and_finishes_original_result` verify covered effects and remaining readback/result. Changed input/account/revision invalidates approval. Timeout/crash/202/wrong-subject and cross-API/AI unknown claims block duplicate changes; authorized API reads can investigate without erasing unknown dispatch. Unaccounted browser/CLI effects cannot license restarting the worker. This is not universal exactly-once remote execution. |
| CC6: races, restart and stopping | Settings/action/API/MCP/skill suites cover late and duplicate decisions, atomic publication, cancellation, renewal, revoke/forget and restart. Native settlement/recovery covers lost callbacks, independent owners, exact claims, expiry and terminal outcomes. Local grant and browser login suites inject failures before/after commit and resume original results after restart; independent service instances share one queue receipt. Historical browser records without a queued Work or retained readiness are explicitly unverified, never silently replayed. |
| CC7: bounded original request | `test_connect_request_budget.py` covers counters across retry/input-related Work IDs, actual bridge access, migration and stale runs. `test_connect_decision_budget.py` covers planning, routing, early judgments, outcome evaluation and reaction threads against the same ledger/epoch. Owner waiting pauses active execution time rather than resetting it. Shared continuation/input/setup caps remain. `test_connect_schedule.py` and browser continuation tests preserve the existing accepted slot, deadline and run count through input/approval/login; expiry admits no new model run or duplicate schedule. |
| CC8: substantive result and evidence | Original-result tests cover trust, unseen MCP, static/OAuth input, API payment, native callback, selected folder, browser login and pinned skill loading. Reports stay in the originating Work/conversation with normal provenance and information-use paths. API fixtures retain subject, observation time, missing-field and unknown-effect qualifications; MCP discovery/setup audit distinguishes local/cached/outbound observations. `test_connect_goal_routes.py` makes proposal/setup-only answers incomplete through the existing outcome judgment and propagates the unchanged goal/discovery facts to both worker types. |

## Additional acceptance and limits

- The four contract §11 observation cases and one synthetic held-out capability
  are in `test_connect_goal_routes.py`. They exercise the same generic planning,
  brief and outcome contracts. The unseen MCP integration separately traverses
  discovery, exact owner confirmation, observed tools, refreshed worker and the
  substantive original result. Fixture route selection is not a live quality score.
- `test_no_scenario_code.py` inspects decision/runtime branching and the existing
  classified adapter exceptions. Changed-code review remains necessary alongside
  this bounded static guard. Service examples belong in fixtures/content, not
  task-specific core branches.
- An available alternative must preserve the owner's goal/account constraints.
  The preference suite includes Registry outage followed by an already-authorized
  same-account API result; no new trust grant or preference overwrite is inferred.
  The goal-route suite rejects a bare limitation or unbound proposal as successful
  execution. When supported evidence is only partial, report that partial result
  and its exact unmet condition; no universal service or outcome guarantee follows.
- Content skills use confirmed pinned acquisition, current digest checks and
  AI-chosen loading. Trust for configured hands does not certify executable skills,
  full plugin hooks, A2A/delegated agents or a new native MCP client. The route table
  above is the explicit disposition for those unactivated mechanisms.
- Reuse is concrete: existing Still Work/Settings/approval/SQLite boundaries;
  installed official CLI discovery/registration/permission interfaces and official
  Registry metadata; existing `oauthlib` client-credentials construction/parsing,
  encrypted credential stores and browser profile. Still keeps owner state,
  authority, effects and budgets; no new general workflow framework was introduced.
- No deployment, real account connection, account operation, scheduler activation,
  arbitrary downloaded strategy execution or live-model check is included. Actual
  Keychain, tunnel-provider and vendor account behavior retain their separately
  observed evidence classes. A real model's ability to find a useful route remains
  an owner-use observation, never inferred from these scripted tests.
