# Documentation map

Start with the question you have, not the order in which the project created files.

| Reading path | Start here | What it is for |
| --- | --- | --- |
| Use Still | [QUICKSTART](QUICKSTART.md), [product status](product-status.en.md) | Install, connect, operate, and distinguish released behavior from newer source and evidence limits |
| Understand the ideas | [Concepts — seven connected chapters (Korean)](concepts/README.md), [VISION](VISION.md) | Ownership, ontology, memory, judgment, Presence, authority and ecosystem hypotheses |
| Change the implementation | [Contributor onboarding](contributor-onboarding.en.md), the contracts below | Current rules, code responsibilities and relevant evidence |
| Develop and release | [CONTRIBUTING](../.github/CONTRIBUTING.md), [development workflow](development-governance.en.md), [release procedure](release.en.md) | Repository work, validation and publication, not end-user runtime behavior |
| Inspect the sources | [Research](research/README.md), [fixed publications](whitepapers/README.md) | Original studies, experiments, references and editorial provenance |

The [Engineering hub](https://jongtae.github.io/Still/engineering/) is the public explanatory route. The new concept chapters synthesize the existing compendium and later public repository decisions; they are not another policy source or a shipped-capability claim. Existing canonical paths stay stable while their contents and consumers are audited. The five reading paths above do not imply that every file has already been physically relocated.

## What is authoritative

| Question | Source of truth |
| --- | --- |
| Why the project exists | [VISION](VISION.md) |
| Contribution invariants | [AGENTS.md](../AGENTS.md) and the [Development Constitution](development-constitution.en.md) |
| Currently selected goal and order | Root [delivery-plan.yaml](../delivery-plan.yaml), `next_goal` and the selected program record |
| Issue, PR, merge and required-check status | GitHub Issues, Pull Requests and Checks |
| Intended product/runtime behavior | Applicable current contract plus current code/tests; a contract alone does not prove delivery |
| Released behavior and evidence limits | [Product status](product-status.en.md) and the [release manifest](release-manifest.json) |
| Explanation and rationale | [Concept chapters](concepts/README.md); normative contracts take precedence |
| Research or historical observation | The dated source at its original evidence level; it does not activate implementation |

## Current contracts by responsibility

| Responsibility | Canonical entry points |
| --- | --- |
| Architecture and owner state | [Architecture](personal-agentos-architecture.en.md), [Owner Control](owner-control-contract.en.md) |
| Meaning, roles and mandates | [Professional Roles and Communication](professional-roles-and-communication-contract.en.md) |
| Memory and present context | [Secretary Agency](secretary-agency-contract.en.md), [Current Context](current-context-contract.en.md), [implementation reference](current-context-implementation.en.md) |
| Judgment and execution | [Decision Layer](decision-layer.en.md), [Assistant Execution](assistant-execution-contract.en.md) |
| Relationship and collaboration | [Presence](presence-experience-contract.en.md), [Professional Roles and Communication](professional-roles-and-communication-contract.en.md) |
| Connections, skills and delegation | [Connection and Delegation](connection-delegation-contract.en.md), [connection support](connection-support.en.md) |
| Future distribution design | [Platform Foundation](agent-distribution-platform-foundation.en.md); not evidence that a marketplace ships |
| Repository delivery | [Development Governance](development-governance.en.md), [Goal Execution](goal-execution-contract.en.md), [Incremental Delivery](incremental-delivery.en.md) |

Some contracts still contain mixed live rules and dated amendments. Their current-rule/precedence sections control conflicts until a scoped consolidation removes superseded prose. Do not discard a still-live exception or a paused program's valid resume contract merely because it is old. The concept chapters do not replace these rules.

## Planning is not current execution status

[Roadmap](roadmap.md) is a short direction entry, and [TASKS](TASKS.md) now routes to the actual work-selection/status sources rather than repeating old program tables. Their previous contents remain accessible at the pinned pre-cleanup revision linked from each page. Immutable ledgers, predecessor plan/release records and unique evidence remain intact.

The obsolete utility-v1 HTML/CSS/JS study is retired from the active tree; its [compatibility entry](design/utility-v1/README.md) points to the original revision and current experience concepts. Retiring the study is not a change to current web behavior or its governing contracts.

Earlier source history that was never imported into Still remains in the private predecessor repository; do not pretend it can be recovered from this repository's Git history. [Name compatibility](still-name-compatibility.en.md) separates retained technical names and historical issue provenance from current operational links.

## Editing without rebuilding the clutter

Change one canonical rule and link to it. Concepts explain the reason and trade-offs without duplicating field-by-field contracts or release status. Research retains unique evidence and fixed editions, not copies of every current explanation. Retire one-off instructions and redundant prose after preserving their unique content and updating their consumers; do not move every obsolete file into another archive.

The cleanup is tracked in [#35](https://github.com/Jongtae/Still/issues/35). Core contract consolidation, QUICKSTART extraction and further path/reference migration remain separate uncompleted slices. This entry does not claim the whole cleanup is finished.

## Community, review and releases

[Contributing](../.github/CONTRIBUTING.md), [Governance](../GOVERNANCE.md) and [Code of Conduct](../CODE_OF_CONDUCT.md) apply to humans and their AI tools. [Contributor onboarding](contributor-onboarding.en.md) supplies one shared setup and role procedure. [AI contribution and review](governance/ai-contribution-review.en.md) distinguishes author self-review from independent review. [Release Policy](governance/release-policy.en.md) governs publication authority; [Release Procedure](release.en.md) supplies the operational steps.
