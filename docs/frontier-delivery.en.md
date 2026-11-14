# Frontier delivery specification

Parent [#822](https://github.com/Jongtae/Still/issues/822), specification [#823](https://github.com/Jongtae/Still/issues/823). Owner direction dated 2026-11-14 selects issue-based development with Codex and Claude. Baseline `be1323a0b9ca2e071f8a676a1b2910b291702de4` was inspected against the current contracts and open issues. This document defines requirements and work boundaries. Root [delivery-plan.yaml](../delivery-plan.yaml) selects the finite wave; GitHub owns execution status.

## Outcome and capacity

The owner entrusts work across interruptions and days, corrects it in conversation and receives a result grounded in observed evidence. Measure verified completion, avoidable owner interventions, elapsed time, observable usage/cost, recovery and useful proactive outputs. Count required approvals separately from avoidable interventions. Compare products only on disclosed equivalent inputs, accessible tools, accounts and budgets, with held-out tasks and repeated observations. Record sample sizes and uncertainty. A passing unit suite establishes implementation behavior within its fixtures; frontier performance needs comparative operating evidence.

Both Codex and Claude are available development resources under the owner's instruction; observed availability is recorded under *Observed engine availability* below. Provider refusals remain observed execution facts. Use an eligible alternate agent on the same bounded issue when one route cannot run, preserving the branch and handoff. Subscription capacity does not remove product Work/upkeep limits, grant API judge spend or authorize external account operations. On-demand evaluation scope follows the issue being changed. The earlier calendar estimates and percentage targets were proposals; establish baselines before selecting numerical product targets.

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

The plan's first wave is bounded to #823 and #824. The parallel lanes below work on #817–#821 and #825–#827 under the owner's direct 2026-11-14 instruction (#822), each on its own issue, branch and PR. A reserved plan unit (#825–#827) is recorded as selected or complete by its own PR, with its dependency disposition, as the Goal Execution Contract requires; until then it stays reserved in the plan. #825–#827 are issued design/implementation contracts with explicit predecessors. Their readiness is assessed using current GitHub evidence and scoped file ownership. Closing one issue does not silently activate an unrelated historical program. The checked-in goal declaration does not itself start a background scheduler.

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

Owner instruction 2026-11-14 moved coordination from the stalled Codex coordinator to a Claude Code coordinator session. The [coordinator handoff on #822](https://github.com/Jongtae/Still/issues/822#issuecomment-6873291477) holds the live lane table; this section records the same topology. Where the two differ, the latest coordinator comment on #822 wins and this section is corrected in the next lane-I change.

### Lanes

Each lane is one Claude Code session with its own worktree and one purpose-named issue branch per PR. Inside a lane, Codex implements a named piece in a scratch worktree, and Claude subagents take the other roles below.

| Lane | Issues / PRs | Lead | Codex piece (scratch worktree) | Review |
| --- | --- | --- | --- | --- |
| I integration | #823 / PR #828, #824 / PR #829, #817 constitution text | Claude | None for #828/#829, which Codex authored; the lane-I brief later gave Codex the #817 governance-test update | #828/#829 not escalated: auto-merge; #817 escalated: owner merges |
| A masking and credentials | #818 then #819, stacked on the same functions | Claude | #818 removal sweep; #819 pattern and error pass-through | Escalated, owner merges |
| B reads | #820 as B1 read/consent and B2 deferred audit | Claude (B1) | B2 deferred information-use computation | B1 escalated; B2 assessed in its PR |
| C scheduled work | PR #816, then #821 as C1 owner-accepted runs may ask and C2 no unconfirmed suffix | Claude (C1) | C2 suffix removal and the C1 acceptance-matrix tests | C1 escalated; #816 and C2 not |
| D recovery specification | #826 | Claude | Fixture and spike tests | Design evidence; Codex adversarial review |
| E recall and continuity experiments | #827 | Claude | Fixture cases | Design evidence |
| F attempt provenance | #825 | Claude | Digest helper and tests | Assessed in its PR |

### Roles inside a lane

| Role | Engine and model | Deliverable and boundary |
| --- | --- | --- |
| Coordinator | Claude Code session (Claude Opus 5.5) | Lane table, function ownership, merge order, cross-lane questions; writes no runtime code |
| Lane lead | Claude Code session | Integrates diffs, runs the full suite outside the Codex sandbox, commits, pushes, opens the PR ready, records evidence on the issue |
| Codex implementer | `gpt-6.1-sol`, medium effort, `codex-companion task --write` | Its named piece in a scratch worktree, self-check against acceptance, draft PR body; never writes in the lane worktree and never pushes |
| Caller sweep | Claude subagent, Explore on Sonnet | Read-only search for callers and pinned wording |
| Authority/threat model | Claude subagent, Plan on Opus | Threat model of an escalated slice |
| Disjoint implementer and fresh-context verifier | Claude subagent, general-purpose on Opus | A disjoint implementation piece; verification given the diff and the acceptance list, not the author's conclusions |
| Tables, fixtures and PR-body check | Claude subagent, general-purpose on Sonnet | Should/should-not tables, fixtures, `scripts/verify_reuse_review.py` |
| Suite runner and CI triage | Claude subagent, general-purpose on Haiku | Full-suite runs with `PYTHONPATH=src`, grepping `^FAILED` and `SUBFAILED`; CI log triage |

Distinct models under one author's control are cross-model self-review under the [existing review contract](governance/ai-contribution-review.en.md), not independent review.

### Function-level ownership

`quickstart_service.py`, `agent_runtime.py`, `quickstart_store.py` and `preparations_service.py` are shared hotspots, so ownership is assigned per function rather than per file.

- **A**: `browser_session.redact_private_values` and its callers, `preparations_service.scrub_*`, `quickstart_service.scrub_envelope` / `_redact_known_secrets` / `_redact_reason`, `Capabilities.judgment_text`, `agent_runtime.work_written_values` with the `lookup_sources` / `inherited_excluded` exclusion plumbing, `context_inbox.SENSITIVE`, `current_context.redact_known_secrets`, `bounded_execution._SECRET` / `redact_reason`, `mcp_bridge` error results, `telegram_presence.MASK_MARK`. A may drop `private_sources` from the signature and callers of `record_turn_sent`, nothing else in that method.
- **B**: `agent_runtime` `page_scope` / `public_page_read` / `search_result_urls` and the `_read_network` scope, `quickstart_service.document_boundary`, `folder_grants`, `outbound_evidence` / `held_value_matches`, `information_use`.
- **C**: `browser_signin`, `decision`, `orchestrator` (the PR #816 hunks), `quickstart_service.unattended_work` / `_queue_action_continuation` and the continuation methods, the scheduling and line-change refusals in `preparations_service`, `settings_orchestrator`, `conversation_projection`.
- **F**: `work_executor`, and the bodies of `quickstart_service.record_turn_sent` / `record_turn_worker`.
- **D** and **E**: new documents and new fixture/test files only.
- **I**: this document, `delivery-plan.yaml`, `tests/test_fresh_delivery_plan.py`, `evals/README.md`, `evals/agentos_eval/judgments.py`, `docs/owner-model-phase3-delivery.en.md`, `docs/engineering/contracts/memory.en.md`, `docs/README.md`, `docs/development-constitution.en.md`, and the AGENTS.md lines named by #817.

Shared one-line conflicts, such as the `Capabilities.__init__` signature or `messages.json` keys, are resolved by the lane that merges later, at rebase. A lane that needs a function another lane owns asks the coordinator first.

### Merge order

1. PR #828, then PR #829 (lane I).
2. Runtime: PR #816, then #818, then #820-B1, #821-C1 and #825 in readiness order. Each later PR rebases on `origin/main` and re-runs its focused tests before asking for review. PR #816 merges first only after its general unattended sign-in condition is reconciled with #821's owner-accepted versus self-initiated distinction. #825 may merge before #819/#820 because the function-level ownership below isolates its edits; its PR records that disposition.
3. #819 is built on #818's branch and retargeted to `main` before #818 merges, because deleting a stacked PR's base closes it.
4. #817 closeout last: the census amendment naming every removed rule and the README, SECURITY.md and product-status update, after #818–#821 merge.

### Working protocol

Each branch is based on an identified `origin/main` revision. Before editing, read the current issue, the relevant contracts, active PRs and the ownership above. Root instructions are common to both tools. Codex runs without network and cannot bind sockets, so the lane prepares the scratch worktree's `.venv` before the brief and runs socket and real-CLI tests itself. A run stopped on capacity resumes with `task --write --resume-last`.

Implementation briefs contain the issue URL, base revision, permitted functions and files, objective, required behaviour, cases that must keep working, tests, dependencies and expected handoff. Review briefs contain the exact head and base, the diff, the acceptance list and the affected boundaries. Review the code and tests directly.

Use focused deterministic tests while editing and the required repository validation before integration. A reproducible finding is fixed or rebutted with evidence. Routine cross-model review has no fixed round count. The Constitution's material-boundary criteria govern escalated Codex review of the exact final head, `validate` and `full-validate`, and owner merge; non-escalated PRs open ready with squash auto-merge armed. Public release follows the existing release policy. Live-account tests need their own scope; synthetic local tests proceed under the development instruction.

### Observed engine availability

Earlier on 2026-11-14 a read-only Claude review request returned `429 usage_limit_reached` before model execution, with a stated reset at 14:00 KST, and author-controlled Codex review was used instead. Later the same day Claude was observed available: the coordinator and lane-I sessions ran as Claude Code sessions on Claude Opus 5.5, and lane I observed Opus and Sonnet subagents completing work. Haiku subagents are assigned by the roster; their runs are recorded on the lanes that use them. This replaces the earlier 429-only record. When a provider refuses again, record the error, scope and any stated reset time, and reassign the same ready unit where possible with ownership kept explicit. A prepared brief is pending until a real result is received; a started process is not a test or review that ran.

### Handoff

Handoff fields are issue/PR, base/head, functions and files, requested and observed engine/settings, implementation result, exact tests and evidence class, findings and disposition, integration state, operating evidence, remaining dependency, next actor and action, and rollback. Status lives on each issue and PR, so a stopped lane can resume from them. Use the [incremental handoff](incremental-delivery.en.md) for integration waits.

## Existing solutions review

Decision **Adapt** for the first wave. SQLite transactions, Git worktrees and GitHub-native issue/PR records fit the current single-owner architecture. Inspect already supplies scenario evaluation; Still's stdlib judgment harness invokes production callers directly. Extend their identities and metadata rather than introduce another evaluation store. Prompt registries and tracing backends can be assessed if independent deployment or operations needs establish a gap. Runtime state ownership, approvals, secret handling and model-independent contracts remain in Still.

Primary comparisons consulted for the proposal include [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence), [Temporal activities](https://docs.temporal.io/activity-definition), [Inspect evaluation logs](https://inspect.aisi.org.uk/eval-logs.html), [stable agent interfaces](https://www.anthropic.com/research/managed-agents) and [context engineering](https://www.anthropic.com/research/effective-context-engineering-for-ai-agents). A later dependency choice records licence, maintained version, deployment impact and rollback in its own issue.

## Verification and completion

#823 verifies the requirement map, declared activation, issue dependencies, current-versus-historical contract pointers and local links. #824 verifies comparison through the existing harness and historical-report cases. Later units map every acceptance criterion to exact tests and observed outcomes. Required CI and applicable review precede integration. A document, local test or branch alone does not complete the parent program. No superiority, live provider reliability or deployed behavior is inferred from this planning specification.
