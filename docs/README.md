# Documentation map

[Engineering hub](https://jongtae.github.io/Still/engineering/) — philosophy, ontology, decisions, Presence, model independence, authority and research, with implementation/evidence/design boundaries.

Still keeps current contracts, delivery selection, research, evidence and historical records separate. Start here instead of inferring authority from a document's age or filename.

## What is authoritative

| Question | Source of truth |
| --- | --- |
| Why the project exists and the concept map | [VISION.md](VISION.md) |
| Contribution invariants and repository workflow entry point | [AGENTS.md](../AGENTS.md) and the [Development Constitution](development-constitution.en.md) |
| Which goal/substep is selected now | repository-root [delivery-plan.yaml](../delivery-plan.yaml) `next_goal` and the selected program record |
| Issue, pull request, merge and required-check status | GitHub Issues, Pull Requests and Checks |
| Product/runtime behavior | the applicable current contract under `docs/` plus current code/tests; a contract is not by itself a shipped-capability claim |
| Released-product evidence and limits | [Product status](product-status.en.md) and the release manifest |
| Research and concept sources | [research/README.md](research/README.md); these inform decisions but do not activate implementation |

## Current contracts by concern

- Product architecture and owner control: [Still Architecture](personal-agentos-architecture.en.md), [Owner Control Contract](owner-control-contract.en.md), [Agent Distribution Platform Foundation](agent-distribution-platform-foundation.en.md).
- Owner-facing experience: [Presence Experience Contract](presence-experience-contract.en.md), [Secretary Agency Contract](secretary-agency-contract.en.md).
- AI judgment and execution: [Decision Layer](decision-layer.en.md), [Assistant Execution Contract](assistant-execution-contract.en.md).
- Connections, control levels, delegation and the owner's way: [Connection and Delegation Contract](connection-delegation-contract.en.md).
- Repository delivery: [Development Constitution](development-constitution.en.md), [Development Governance](development-governance.en.md), [Goal Execution Contract](goal-execution-contract.en.md), [Incremental Delivery](incremental-delivery.en.md).

Core current contracts now lead with a current-rule/precedence section. Superseded amendment chains and dated diagnostic evidence that can be separated safely are kept in the predecessor repository's `archive/history/contract-amendments/` (not carried into this import) rather than requiring readers to replay them inline. Mixed amendments stay canonical when they still contain live rules. This organization does not alter product semantics.

## Planning and status

Do not use `docs/roadmap.md`, `TASKS.md`, ledgers, archived plans or historical issue prose to infer the currently selected implementation unit. They preserve product/program direction and decisions. The root `delivery-plan.yaml` owns current goal selection and ordering; GitHub owns execution status.

A paused program can still have a valid resume contract. Do not archive or delete a contract merely because its program is not currently selected.

## Research, evidence and history

Research is intentionally retained. It captures owner thinking, external investigation, white-paper/editorial sources and design evidence that may remain valuable even after implementation changes. Research publication does not amend a canonical contract.

Validation/evidence records state what was observed at a particular revision and evidence class. They do not become current product behavior merely by remaining under `docs/`.

Finished one-off implementation records and superseded concept documents are kept in the original repository's archive (not carried into this import). Names kept from the predecessor (the `agentos` command, data directory, `AGENTOS_*` variables, service labels) and how predecessor issue links are treated are listed in the [name compatibility map](still-name-compatibility.en.md). Preserve dates, evidence boundaries and links when moving them.

## Editing rule

Prefer changing one canonical source and linking to it over copying current state into several documents. If a document needs a point-in-time snapshot, label it explicitly as historical or dated evidence. A documentation cleanup must not silently change runtime policy, owner authority, delivery selection or the meaning of prior evidence.

## Cross-role semantic and communication contract

[Professional Roles and Communication Contract](professional-roles-and-communication-contract.en.md) is the normative design/acceptance entry point for domain/role lenses, mandate versus authority, collaborative in-flight Presence, communication protocol and commitments. It complements (does not supersede) Architecture, Decision Layer, Presence and Secretary Agency. It is not implementation evidence or a change to `delivery-plan.yaml`.

## Community contributions, AI review and releases

- [Contributing](../.github/CONTRIBUTING.md) and [Governance](../GOVERNANCE.md) are public entry points for humans and their AI tools; [Code of Conduct](../CODE_OF_CONDUCT.md) applies to both.
- [Contributor Onboarding](contributor-onboarding.en.md) is where a new contributor starts: setup, code map, starting in a role, and the `contributor-onboarding` skill for Claude Code, Codex, Cursor and other assistants.
- [AI Contribution and Review Contract](governance/ai-contribution-review.en.md) defines per-task roles, human accountability, independence, findings and decision briefs. It does not override the Development Constitution.
- [Release Policy](governance/release-policy.en.md) governs version selection, readiness and publication authority; [Release Procedure](release.en.md) remains the operational tag/archive/Homebrew sequence.
