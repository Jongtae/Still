---
name: Iteration
about: A bounded, acceptance-driven product increment
labels: iteration
---

## User outcome

## Runtime impact

## Goal-ready activation

Active delivery-plan iteration, predecessors satisfied, and the explicit owner activation that authorizes this goal:

## Top-level goal / substep

Parent top-level goal and enumerated substep ID (or `not applicable`):

## Allowed authority

Files, runtime boundaries, repositories, and external systems this goal may change:


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

## Non-goals

## Ordered work units

## Evidence and completion audit

Map each acceptance criterion to automated fixtures/checks, any risk-triggered review artifacts, required CI, and the merged PR/closeout evidence. A closed issue or local test alone is insufficient.

## Delegation and escalated review

Record exclusive files and requested/accepted/observed model and reasoning settings when delegation is used. Then state whether the work materially changes a security/authority boundary under the Development Constitution. If yes, name the trigger and escalated-review plan. Final closeout or ordinary recovery alone is not a review trigger.

## Blocked / restart rule

Name the concrete external blocker, safe recovery attempts, changed condition required before retry, and the minimum next authority.

## Validation plan

## Branch

`<type>/<issue>-<slug>` (Conventional Commits type, e.g. `feat`, `fix`, `docs`)
