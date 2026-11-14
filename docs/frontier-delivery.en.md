# Frontier delivery specification

Parent [#822](https://github.com/Jongtae/Still/issues/822), specification [#823](https://github.com/Jongtae/Still/issues/823). Owner direction dated 2026-11-14 selects issue-based development with Codex and Claude. Baseline `be1323a0b9ca2e071f8a676a1b2910b291702de4` was inspected against the current contracts and open issues. This document defines requirements and work boundaries. Root [delivery-plan.yaml](../delivery-plan.yaml) selects the finite wave; GitHub owns execution status.

## Outcome and capacity

The owner entrusts work across interruptions and days, corrects it in conversation and receives a result grounded in observed evidence. Measure verified completion, avoidable owner interventions, elapsed time, observable usage/cost, recovery and useful proactive outputs. Count required approvals separately from avoidable interventions. Compare products only on disclosed equivalent inputs, accessible tools, accounts and budgets, with held-out tasks and repeated observations. Record sample sizes and uncertainty. A passing unit suite establishes implementation behavior within its fixtures; frontier performance needs comparative operating evidence.

Both Codex and Claude are available development resources under the owner's instruction. Provider refusals remain observed execution facts. Use an eligible alternate agent on the same bounded issue when one route cannot run, preserving the branch and handoff. Subscription capacity does not remove product Work/upkeep limits, grant API judge spend or authorize external account operations. On-demand evaluation scope follows the issue being changed. The earlier calendar estimates and percentage targets were proposals; establish baselines before selecting numerical product targets.

## Existing foundations and requirement ownership

| Requirement | Existing foundation | Remaining work and canonical owner | Acceptance evidence |
| --- | --- | --- | --- |
| F01 Comparable evaluation | Production-caller judgment harness, Inspect scenarios, Git/prompt metadata | [#824](https://github.com/Jongtae/Still/issues/824), extending [#42](https://github.com/Jongtae/Still/issues/42), identifies case-content and effort differences | Normal report generation and comparison, historical reports, equivalent reordered data, changed expectations and effort |
| F02 Attempt attribution | Shared `turn_context`, context/provenance tests, bounded worker summaries | [#825](https://github.com/Jongtae/Still/issues/825) records instructions and actual offered tool-schema identities per attempt | Changed schemas under the same tool name; two attempts; API/CLI representation and visibility limits |
| F03 Recovery | `QuickStore.recover`, `WorkLedger`, continuation receipts, stale-run epochs | [#826](https://github.com/Jongtae/Still/issues/826) matrix and compatibility spike before bounded implementation | Fault injection around dispatch, receipt, settlement and delivery; legacy rows; unknown effects; cancellation and deadlines |
| F04 Useful memory | Source/currentness/expiry, forget/delete, FTS5, upkeep/dream | [#403](https://github.com/Jongtae/Still/issues/403), experiment design [#827](https://github.com/Jongtae/Still/issues/827) | Paraphrase retrieval, temporal conflicts, correction and forgetting, with lexical baseline and held-out cases |
| F05 Continuing work | `entrusted_lines`, `procedure_versions`, preparations and reports | [#799](https://github.com/Jongtae/Still/issues/799), timing [#813](https://github.com/Jongtae/Still/issues/813)/[#814](https://github.com/Jongtae/Still/issues/814), experiment design #827 | Stable responsibility identity, corrected subsequent steps, stop/cancel, promised interval report and deadline |
| F06 Owner-created scheduled waits | Existing action/settings/location/sign-in receipts | [#821](https://github.com/Jongtae/Still/issues/821), reconcile overlapping [PR #816](https://github.com/Jongtae/Still/pull/816) | Accepted preparation version, approval binding, expiry, duplicates, cancellation, self-initiated distinction |
| F07 Useful owner data | Information-use evidence, secret protection, owner-selected connections | [#817–#821](https://github.com/Jongtae/Still/issues/817) corrective program | Ordinary text preserved; actual recipient recorded; secrets and owner authority respected |
| F08 Source and authority | Current owner-act, exact approval and third-party candidate boundaries | [#744](https://github.com/Jongtae/Still/issues/744), scoped findings and explicit disposition | External content cannot become an owner instruction; test specific changed entry points |
| F09 Scoped parallelism | Worker lock and existing independent read concurrency | [#16](https://github.com/Jongtae/Still/issues/16), #827 experiment | Benefit against single-worker baseline; shared-resource order, cancellation, cumulative budget |
| F10 Judgment ownership | Routed decisions and Work ledger | [#722](https://github.com/Jongtae/Still/issues/722) | Bridge judgment tied to the actual Work for budget, Stop and audit |

Memory, scheduling and continuous responsibilities are already implemented in substantial part. This program extends their current identities and receipts. Existing [prompt review #202](https://github.com/Jongtae/Still/issues/202), [voice #198](https://github.com/Jongtae/Still/issues/198) and [calibration #348](https://github.com/Jongtae/Still/issues/348) retain semantic prompt ownership. [#462](https://github.com/Jongtae/Still/issues/462) owns task/capability evidence mapping. The [memory contract](engineering/contracts/memory.en.md) records remaining source/text discrepancies explicitly.

## Milestones and dependency sequence

| Milestone | Required result | Sequence |
| --- | --- | --- |
| M0 Measured baseline | Valid comparison conditions and actual request-path evidence | #823 and #824 in parallel; #42/#462 for existing measurement scope |
| M1 Reliable work | Recovery matrix with a chosen bounded implementation; actual effect and delivery state agree | #817–#821 corrections, #722 where relevant, #826 spike; #825 after overlapping provenance edits settle |
| M2 Persistent personalization | Context selection improves held-out outcomes with currentness and forgetting respected | #403 and #827 experiments on existing memory |
| M3 Long-horizon delegation | Continuing identity, correction, resume and resource ownership across sessions | Recovery contract plus #799 and scheduling defects; parallelism on measured fit |
| M4 Useful initiative | Continuing mandate leads to relevant action/report with less repeated owner effort | #799 acceptance and #821 origin distinction; existing operating evidence criteria remain visible |
| M5 Repeated improvement | Relevant regressions, held-out comparison and rollback support model/prompt changes | Extends existing harness continuously; no blanket paid-evaluation gate |

For this explicitly owner-authorized parallel wave, `active_substeps` selects the serial integration step. `parallel_authorization` records the disjoint specification and evaluation lanes used during preparation. On integration of the #823 PR, specification work is complete-on-merge and #824 is the sole selected integration step; `parallel_eligible_substeps` is empty at that point. These fields describe selection and ownership; GitHub issues and PRs describe actual work in flight. The controller continues selecting the parent program and cannot independently activate a child.

First-wave implementation is bounded to #823 and #824. #825–#827 are issued design/implementation contracts with explicit predecessors. Their readiness is assessed using current GitHub evidence and scoped file ownership. Closing one issue does not silently activate an unrelated historical program. The checked-in goal declaration does not itself start a background scheduler.

## Interface and state contracts

### Evaluation comparison, F01

Inputs are the validated case data actually used, runtime selection including reasoning effort, report version, case/trial identity and evidence class. Outputs include condition equivalence, concrete reasons and useful metric deltas. Digest canonical case content without adding raw case text to reports. Unknown historical metadata produces an explicit limitation. Preserve existing version mismatch behavior and old report readability. Regression tests cross the report producer and comparison consumer.

### Model context and attribution, F02

Keep packaged Markdown fragments and code-managed composition. Plan, execution and evaluation have explicit input fields and output schemas. `turn_context` and route renderers remain the shared foundation. Capture the effective instruction and tool-contract identities at dispatch, with the actual definitions passed to the worker. Descriptions are model inputs and belong in schema identity. A CLI may expose a wrapper or native tools that Still cannot fully observe; name that representation and mark unavailable portions. Avoid a second mutable tool-discovery read just to compute a digest. Metadata records preflight versus sent input accurately. Legacy data stays readable, and bounded worker history retains its documented truncation.

### Work recovery, F03/F06

The recovery design enumerates persisted state, pending operation, known effect, cancellation/forgetting, authority revision, active epoch, remaining allowance and delivery state. A restart after a dispatched write with no authoritative receipt remains an unknown effect. Completion requires an observation appropriate to the operation. Stale processes cannot spend a later run's allowance or apply callbacks after cancellation. Approval/input waits retain the accepted preparation version and original request lineage. Migration covers older rows with missing fields and interrupted migrations. The implementation slice names exactly which recovery states become supported.

Compare SQLite transaction/receipt adaptation with LangGraph checkpoints and Temporal activities. Evaluate CLI process boundaries, local installation, replay semantics, persistent owner data, dependencies and rollback. The spike selects a bounded solution based on an observed gap. A new workflow engine must justify migration from existing continuation records.

### Context and continuing responsibility, F04/F05/F09

Current memory remains authoritative. Retrieval indexes are rebuildable. Context carries source, applicable time/scope and correction history. Forgetting removes eligibility according to the current memory contract, including derived retrieval paths. Long-running responsibilities reuse entrusted-line identity and preparation linkage. Child execution carries goal, relevant facts, constraints, completion conditions and observed results, with cancel and budget propagation. Shared browser/account changes require explicit resource ownership. Use parallel independent work when an experiment shows an outcome or latency benefit at disclosed cost.

### Change lifecycle

Each implementation names additive versus replacement fields, old-record behavior, migration interruption handling and rollback. The current first-wave documents and evaluation metadata need no runtime data migration. A later migration must preserve receipts and provide compatibility fixtures. No current contract is silently rewritten to claim a historical implementation met new acceptance.

## Disposition of #817–#821

All five contain unresolved concrete defects and are retained with amendments published in their issue bodies.

| Issue | Retained outcome | Correction |
| --- | --- | --- |
| #817 | Owner-selected data and connections work without redundant consent | Authorized remote disclosure is recorded factually. The policy-gate principle coexists with validation, cancellation and recovery consistency. Predecessor EGRESS-OPEN-01 references mean `Jongtae/agentos#826`. |
| #818 | Saved ordinary words survive answers, future runs and page views | Withdraw fixed mask-ratio suppression; inspect destinations before deleting shared helpers; describe remote-model disclosure accurately. |
| #819 | Actual credentials are protected and useful errors remain readable | Length/entropy alone cannot define credentials. Preserve existing literal coverage, test short credentials in context, and measure existing cache behavior before replacing it. |
| #820 | Owner-requested reads and selected folders work | Source text cannot authorize disclosure to a new recipient. Deferred audit uses call-time evidence or labels later recomputation, respecting forgetting. |
| #821 | Owner-accepted scheduled work can ask and continue | Bind accepted version and exact callback; preserve current scheduled-payment semantics; reconcile PR #816's blanket unattended sign-in refusal. Presentation suffix removal can be its own slice. |

PR #816's grounded follow-up context and related execution facts remain useful. Its general unattended sign-in condition must be reconciled with owner-accepted versus self-initiated preparation semantics before conflicting integration. Existing risk-triggered review applies to each actual boundary change. Prompt injection findings stay on #744 with explicit evidence and disposition.

## Development roles and working protocol

| Role | Initial assignment | Deliverable and file boundary |
| --- | --- | --- |
| Coordinator | Codex root | Issue dependencies, spec and documented decisions, integration review; one writer for root plan and canonical spec |
| Evaluation implementer | Codex agent | #824, `evals/agentos_eval/judgments.py` and `tests/test_eval_judgments.py` |
| Plan contributor | Codex agent | #823 root plan and plan tests; coordinator owns other #823 documents |
| Cross-model reviewer | Claude when callable | Review actual diff and evidence, return concrete defects/counterexamples; provider refusal permits a recorded alternate |
| Recovery/experiment designer | Claude preferred, eligible alternate recorded | #826/#827 scoped specification and fixtures when ready |
| Runtime implementer | Issue-assigned Codex or Claude | One bounded runtime change per worktree after predecessors; shared files serialized |

Each issue has a purpose-named branch and isolated worktree based on an identified main revision. Before editing, read the current issue, relevant contracts, active PRs and file ownership. Root instructions are common to both tools. `quickstart_service.py`, `agent_runtime.py`, `quickstart_store.py` and `preparations_service.py` are shared hotspots. A second writer waits or takes a disjoint ready issue. Rebase/merge conflict resolution is owned by the implementer and checked against current contracts.

Implementation briefs contain the issue URL, base revision, permitted files, objective, required behavior, cases that must keep working, tests, dependencies and expected handoff. Review briefs contain exact head/base, diff, acceptance evidence and affected boundaries. Review the code and tests directly. Distinct models controlled by the same author are cross-model self-review; use the [existing review contract](governance/ai-contribution-review.en.md).

Use focused deterministic tests while editing, and the required repository validation before integration. If a finding identifies a reproducible defect, fix or rebut it with evidence. Routine cross-model review does not impose a fixed round count. The Constitution's material-boundary criteria govern escalated Codex review and owner merge. Public release follows the existing release policy. Live-account tests and their data/actions require their specific scope; synthetic local tests can proceed with the development instruction.

When a provider refuses, record the error, scope and any stated reset time. Reassign the same ready unit where possible, keeping ownership explicit. A prepared Claude brief is pending until a real Claude result is received. Avoid claiming a test or review ran because a process started.

Handoff fields are issue/PR, base/head, files, requested and observed engine/settings, implementation result, exact tests/evidence class, findings/disposition, integration state, operating evidence, remaining dependency, next actor/action and rollback. GitHub stores current execution status. Use the [incremental handoff](incremental-delivery.en.md) for integration waits.

## Existing solutions review

Decision **Adapt** for the first wave. SQLite transactions, Git worktrees and GitHub-native issue/PR records fit the current single-owner architecture. Inspect already supplies scenario evaluation; Still's stdlib judgment harness invokes production callers directly. Extend their identities and metadata rather than introduce another evaluation store. Prompt registries and tracing backends can be assessed if independent deployment or operations needs establish a gap. Runtime state ownership, approvals, secret handling and model-independent contracts remain in Still.

Primary comparisons consulted for the proposal include [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence), [Temporal activities](https://docs.temporal.io/activity-definition), [Inspect evaluation logs](https://inspect.aisi.org.uk/eval-logs.html), [stable agent interfaces](https://www.anthropic.com/research/managed-agents) and [context engineering](https://www.anthropic.com/research/effective-context-engineering-for-ai-agents). A later dependency choice records licence, maintained version, deployment impact and rollback in its own issue.

## Verification and completion

#823 verifies the requirement map, declared activation, issue dependencies, current-versus-historical contract pointers and local links. #824 verifies comparison through the existing harness and historical-report cases. Later units map every acceptance criterion to exact tests and observed outcomes. Required CI and applicable review precede integration. A document, local test or branch alone does not complete the parent program. No superiority, live provider reliability or deployed behavior is inferred from this planning specification.
