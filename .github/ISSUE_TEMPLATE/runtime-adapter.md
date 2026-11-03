---
name: Runtime Adapter change
about: Design or implement a bounded execution Runtime Adapter
labels: ""
---

## User outcome

What capability becomes replaceable/portable through this runtime without moving owner state into the runtime?

## State / activation

Planned, active, blocked, or review-only? Name the active delivery-plan entry and explicit owner activation when executable. **Creating this issue does not activate it.**

## Constitution / spec

Relevant Development Constitution principles, D-RT/common-runtime contract, parent package contract, and predecessors:

## Runtime identity / revision

Runtime/framework/provider identity, exact revision/version where relevant, transport and compatibility assumptions:

## Work contract

Describe ContextSnapshot input, capability invocation, effective Grants, deadlines/budgets, cancellation, idempotency/checkpoints, Artifact/result output, Evidence, and MemoryCandidate output.

## Authority boundary

- What can this runtime read/write?
- Which Still capabilities/tools can it invoke?
- Can it start nested agents/tools, and how are parent Work Grants projected?
- What authority can it request but never grant itself?
- What canonical state is explicitly forbidden from runtime ownership/mutation?

## Secrets / external destinations

Credentials, connector references, network destinations, redaction and evidence rules:

## Runtime-local state / memory

What may persist locally to this adapter/package, how it is isolated/exported/removed, and why it is not canonical owner Memory:

## Failure / recovery

Timeout, cancellation, crash/restart, duplicate/retry, partial external effect, quarantine/disable and runtime replacement behavior:


## Existing solutions review (Adopt / Adapt / Build)

Required when this issue proposes a new design, contract, architecture element, loop, component or framework adoption, and before non-trivial implementation that introduces or replaces a component, abstraction, integration, dependency, framework, or commodity infrastructure. Look outside first: standards, platform facilities, official SDKs and maintained OSS; use a good external source when one covers the need. An internal candidate counts as reuse only when it already follows that solution. Adding a case, exception or list entry to existing Still machinery is `Build`, not `N/A`. For a data/content change, or a bug fix that introduces none of those and adds no case to existing machinery, set the decision to `N/A` and give a concrete reason.

- problem/boundary:
- standard-library/platform candidates:
- official SDK/reference/standard candidates:
- mature open-source/framework candidates:
- internal repository candidates:
- search evidence (paths/symbols/docs checked):
- maintenance/security/supply-chain fit:
- licence fit:
- runtime/deployment/compatibility fit:
- decision: `Adopt / Adapt / Build / N/A`
- why rejected candidates are insufficient:
- Still-owned policy/authority boundary kept outside the dependency:
- N/A reason:

## Acceptance criteria

- [ ]

## Negative conformance cases

At minimum cover Grant widening, direct canonical-Memory mutation, sealed-Evidence rewrite, nested-worker escape, deadline/cancel failure, secret/log leakage and unsupported capability claims.

## Non-goals

## Validation / evidence plan

Deterministic fixtures first; state separately what live provider/runtime observation, if any, is actually required for completion.

## Branch

`<type>/<issue>-<slug>` (Conventional Commits type, e.g. `feat`, `fix`, `docs`)
