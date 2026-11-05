# Documentation map

Start with the question you have, not the order in which the project created files.

| Reading path | Start here | Purpose |
| --- | --- | --- |
| Install and try Still | [Quick start](QUICKSTART.md) | Install, connect one AI and reach a first conversation |
| Use and maintain it | [Connections](guide/connections.en.md), [operations](guide/operations.en.md), [company networks](guide/workplace.en.md), [platforms](guide/platforms.en.md) | Task-specific setup, recovery and operating limits |
| Check current coverage | [Product status](product-status.en.md), [한국어](product-status.ko.md) | Published coverage, newer source, observations and historical evidence kept separate |
| Understand the ideas | [Concepts — seven connected chapters (Korean)](concepts/README.md), [VISION](VISION.md) | Ownership, ontology, memory, judgment, Presence, authority and ecosystem hypotheses |
| Change the implementation | [Contributor onboarding](contributor-onboarding.en.md), the contracts below | Current rules, code responsibilities and relevant tests |
| Develop and release | [CONTRIBUTING](../.github/CONTRIBUTING.md), [development workflow](development-governance.en.md), [release procedure](release.en.md) | Repository work and publication, not product runtime behavior |
| Inspect original sources | [Research](research/README.md), [publications](whitepapers/README.md), [concept references](concepts/references.md) | Dated studies, experiments, external references and editorial provenance |

The [Engineering hub](https://jongtae.github.io/Still/engineering/) is the public explanatory route. Concepts and guides explain the product; they do not create policy, activate work or establish shipped functionality.

## What is authoritative

| Question | Source of truth |
| --- | --- |
| Why the project exists | [VISION](VISION.md) |
| Contribution invariants | [AGENTS.md](../AGENTS.md) and the [Development Constitution](development-constitution.en.md) |
| Selected goal and order | Root [delivery-plan.yaml](../delivery-plan.yaml), `next_goal` and the selected program record |
| Issue, PR, merge and required-check state | GitHub Issues, Pull Requests and Checks |
| Required product behavior | Applicable current contract; code and tests establish implementation, not the requirement's existence |
| Released coverage and observations | [Release manifest](release-manifest.json) and the evidence named by [product status](product-status.en.md) |
| Explanation and rationale | [Concept chapters](concepts/README.md); current normative contracts take precedence |
| Research or historical observation | Its dated source and original evidence class, not a later restatement |

## Current contracts by responsibility

| Responsibility | Canonical entry points |
| --- | --- |
| Product boundary and owner rights | [Architecture](personal-agentos-architecture.en.md), [Owner Control](owner-control-contract.en.md) |
| Meaning, roles and mandates | [Professional Roles and Communication](professional-roles-and-communication-contract.en.md) |
| Memory and learning | [Memory contract](engineering/contracts/memory.en.md): saves, correction, upkeep, working notes and dream |
| Temporary present context | [Current Context](current-context-contract.en.md), [implementation reference](current-context-implementation.en.md) |
| Pilot policy and accepted preparations | [Secretary Agency](secretary-agency-contract.en.md); Memory details live in the shared contract above |
| Judgment and execution | [Decision Layer](decision-layer.en.md), [Assistant Execution](assistant-execution-contract.en.md) |
| Relationship and collaboration | [Presence](presence-experience-contract.en.md), [local web presentation](web-management-contract.en.md) |
| Connections, skills and delegation | [Connection and Delegation](connection-delegation-contract.en.md), [connection support](connection-support.en.md) |
| Future distribution design | [Platform Foundation](agent-distribution-platform-foundation.en.md); not evidence of a shipped marketplace |
| Repository delivery | [Development Governance](development-governance.en.md), [Goal Execution](goal-execution-contract.en.md), [Incremental Delivery](incremental-delivery.en.md) |

Architecture, Decision and Presence now lead with topic-based current rules. Their dated source text remains at pinned pre-edit revisions, rather than as competing amendment chains. The Memory contract is shared by agency and presentation; a new memory rule belongs there, not in three parallel copies.

A paused program can retain a valid resume contract. Age or a historical filename is not, by itself, a reason to delete a rule. Contracts not yet consolidated still use their explicit precedence sections. The two Memory source/text discrepancies are stated in that contract; an editorial cleanup does not decide them silently.

## Where historical material belongs

[Roadmap](roadmap.md) explains direction; [TASKS](TASKS.md) routes to selection and execution records rather than duplicating old program tables. Their previous text is available at the pinned revisions linked from each page. Immutable ledgers, predecessor plan/release records, fixed publications and unique experiments remain intact.

The retired [utility-v1 study](design/utility-v1/README.md) has a compact provenance entry rather than a second executable prototype. The original remains at its pinned commit. Source history never imported into Still remains in the private predecessor; it cannot be reconstructed from this repository's Git history by assumption. [Name compatibility](still-name-compatibility.en.md) identifies deliberate technical names and historical issue references.

## Editing rules

Use one canonical home for a rule and link to it. Concepts explain choices and unresolved questions; guides explain procedures; product status separates coverage from proof. Research keeps unique sources and fixed editions, not a parallel copy of every current explanation.

When moving a section, update its readers and preserve required deep links. A short compatibility pointer is preferable to a duplicated manual. Retire one-off instructions only after their still-live rules have a named successor. Do not change runtime policy, evidence meaning or activation through an editorial edit. Release-managed status sentences are also an interface: edit their script and tests together if their format changes.

Canonical root filenames and tool-required entry points remain stable where they have consumers. The new guide/concept/engineering directories organize substantial content, not empty placeholder folders. Further path moves need a consumer map; the [#35](https://github.com/Jongtae/Still/issues/35) work is not permission for a blind rename of every older plan or contract.

## Community, review and releases

[Contributing](../.github/CONTRIBUTING.md), [Governance](../GOVERNANCE.md) and [Code of Conduct](../CODE_OF_CONDUCT.md) apply to humans and their AI tools. [Contributor onboarding](contributor-onboarding.en.md) supplies one setup and role procedure. [AI contribution and review](governance/ai-contribution-review.en.md) distinguishes author self-review from independent review. [Release Policy](governance/release-policy.en.md) governs publication authority; [Release Procedure](release.en.md) supplies the operational steps.
