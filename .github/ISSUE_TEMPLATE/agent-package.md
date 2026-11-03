---
name: AgentPackage change
about: Design or implement an AgentPackage, Package Manager, Registry, or package lifecycle change
labels: ""
---

## User outcome

What can the owner safely do after this work that they cannot do now?

## State / activation

Planned, active, blocked, or review-only? Name the active delivery-plan entry and explicit owner activation when executable. **Creating this issue does not activate it.**

## Constitution / spec

Relevant Development Constitution principles, architecture/foundation docs, parent epic, and predecessor contracts:

## Package identity / compatibility

Package/schema/API identifiers, publisher/version/digest expectations, supported Still/runtime versions:

## Requested capabilities and authority

- capabilities/actions:
- filesystem/data scopes:
- network destinations:
- secrets/connectors:
- Memory read selectors / MemoryCandidate writes:
- Event/background subscriptions:
- time/cost/resource budgets:
- consequential actions requiring approval:

## Lifecycle

Describe expected behavior for:

`inspect → verify → stage → health-check → installed-disabled → grant/connect/enable → run → update/rollback → disable/quarantine → uninstall`

## Trust / supply-chain model

Publisher identity, signature/integrity, provenance/SBOM, behavioural/conformance checks, advisories/revocation/quarantine, and what each signal does not prove:

## Threat model / negative cases

At minimum consider malicious package, permission expansion, secret exfiltration, undeclared egress, memory poisoning, prompt injection, dependency substitution, malicious update, confused deputy, replay/stale approval and package removal/recovery.


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

## Dependencies

## Validation / evidence plan

Positive cases, fail-closed negative cases, restart/recovery/idempotency, current CI, exact evidence class, and the escalated (Codex) review only when the Development Constitution's material security/authority escalation criteria apply:

## Historical authority / migration

Prior contracts/evidence that remain historically accurate and must not be retroactively widened:

## Branch

`<type>/<issue>-<slug>` (Conventional Commits type, e.g. `feat`, `fix`, `docs`)
