# Top-Level Specification Completion

## Status and authority

**Status: development complete; administrative closeout #282, TOP issue #265. No active successor.** The immutable deployment candidate is `53912eeb1357ced37031234b1e5376f024dd0a96`; its `main` `validate` run `34204797899` passed. Archived plans and the legacy `agentos/PRD.md` prototype are historical inputs, not implementation scope.

The top-level Goal stays active while an approved row below has a safe next action. A substep issue, PR, or closeout completes only that substep. The Agent immediately advances to the next already-enumerated executable row when its predecessors and authority are satisfied. It cannot add a feature, revive a reserved proposal, widen a data or permission boundary, or perform operating deployment.

## Requirement inventory

| ID | Source and owner outcome | Current evidence | Next action | Owner-setting dependency |
| --- | --- | --- | --- | --- |
| CORE-01 | Root Hub v2 PRD: local-first subscription assistant owns state, policy, tools, queue, approvals, evidence, recovery | Hub modules and fixture tests; MP1 R-01–R-05 issues #217–#225 | Closed by #277/#278 and main CI 34204797899 | Official engine login only in operating mode |
| CORE-02 | MP1: Personal Space, lifecycle, Drive, A2A, Calendar, ReAct, export/restore | Mock-contract evidence #180, #185, #191, #201, #205, #209, #217–#225 | Closed by #277/#278; no new feature | Drive/Calendar OAuth and explicit action approval |
| MP2-01 | Completed conversation settings, reviewed recommendation, owner-local retrieval | Automated evidence #235, #243, #251; status reconciled in #270 | Do not select a next MP2 feature | No marketplace/install/connection authority |
| STAB-01 | Telegram approved-note summary, bounded engine tool roundtrip, recovery | Fixture/CI evidence #255 | Closed by current #277 fixtures and CI | Telegram token and live provider remain owner-only |
| DEPLOY-01 | OP-01/OP-02: isolated Compose, default-deny egress, health, restore/no replay | PR #272 merge `410a003a1332ceb2ec6dd32a74146b0422ac1d42`: actual credential-free build, health, recreate, backup/restore, and cleanup | Preserve completed TOP-02/TOP-03 candidate evidence | Exact DNS allowlist, claim, official login, optional Telegram pairing |
| GOV-01 | Evidence-only delivery governance | Merged #263/#264 and controller tests | Preserve completed contract; no active successor | None |
| STATUS-01 | README, MP2 proposal, open Hub/UX epics | Reconciled in #270: v1.0.4 is unsupported, MP2 is development complete, and stale epics are historical administrative records | Preserve current claims in #282 final audit | None |
| LEGACY-01 | `agentos/PRD.md` prototype | Conflicts with Hub v2 and absent from current plans | Owner decision #279: retain as historical; do not revive | Resolved; future OS/appliance work needs a separately approved proposal |

## Detailed traceability

| ID | Implementation state and predecessor | Verification and completion evidence |
| --- | --- | --- |
| HUB-01 | Codex and Claude subscription adapters; no predecessor remains | `test_subscription_engines.py`; fixture evidence, not official login |
| HUB-02 | Paired Telegram request/progress/result/recovery; STAB-01 complete | `test_telegram*.py`, `test_isolated_engine_integration.py`; no token/pairing claim |
| HUB-03 | Isolated owner runtime, bounded tools, queue, approvals, evidence/recovery; OP-02 complete | `test_agent_runtime.py`, `test_operating_recovery.py`, `test_isolated_engine_integration.py` |
| HUB-04 | Opt-in clipboard/URL context with sensitive filtering and policy sharing | `test_context_inbox.py`; no external sharing is claimed |
| HUB-05 | Curated personal-records, research/briefing, and project-review assistant policies | `test_mp1_release.py`; current TOP-02/TOP-03 regression complete in #277 |
| HUB-06 | Web research, connected-document reads, notes, and reviewed delegation tool boundary | `test_agent_runtime.py`, `test_documents.py`, `test_mp1_release.py` |
| HUB-07 | Portable export/restore excluding connection secrets | `test_operating_recovery.py`, `test_mp1_release.py` |
| DEPLOY-01 | TOP-01 complete: isolated credential-free Compose lifecycle proves build, HTTP health, recreation persistence, secret-free restore, pairing exclusion, and cleanup | PR #272 required CI plus current actual local acceptance; no provider, credential, or live-operation claim |
| STATUS-01 | Documentation/issue-state reconciliation is complete | PR #270 and required CI; old epics are explicitly reconciled under owner-authorized #282 |

## Ordered substeps

| ID | Outcome | Preconditions | Completion evidence |
| --- | --- | --- | --- |
| TOP-00 | Inventory, governance transition, and status-source reconciliation | #265 active | Complete: PRs #266 and #270, historical inventory, plan/controller/template checks, CI |
| TOP-01 | Exact credential-free deployment candidate and Compose lifecycle proof | TOP-00 merged | Complete: PR #272 / runtime merge `410a003a1332ceb2ec6dd32a74146b0422ac1d42`, actual isolated build/health/recreate/restore/cleanup, required CI, independent review |
| TOP-02 | Full current product integration regression and defect repair | TOP-01 candidate | Complete: PR #275 and #277; full fixtures, isolated API connection regression, independent review |
| TOP-03 | Final requirement-to-evidence audit and owner operating checklist | TOP-02 merged | Complete: candidate `53912eeb1357ced37031234b1e5376f024dd0a96`, main validate `34204797899`, mechanical claim gate, no unresolved development review finding |

## Owner operating checklist

After TOP-03, the owner alone may approve operating deployment: review the exact candidate; configure approved exact provider DNS names; build/start locally; claim the runtime; complete official subscription login; enter a local Telegram token only if desired; authorize applicable OAuth; observe health and one live task; retain recovery material; use stop/restore if required. Mock evidence never proves these actions.

## Completion rule

TOP is complete only when every row has current merged implementation and verification evidence, or is explicitly classified as a genuine owner operating action (login, secret entry, live connection approval) or separate owner product decision. An implementation defect, missing technical environment proof, stale candidate, or unresolved review finding may never be classified as owner setup. A substep closeout, closed issue, local test, or release label alone cannot satisfy the top-level completion rule.
