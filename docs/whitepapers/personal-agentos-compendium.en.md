# Still Compendium

## Borrow the brain, keep the assistant: problems, concepts, design, method and lessons

**Non-normative compendium | v0.1 | 2026-10-14 | baseline commit `f776955`**

> **Status:** This document gathers into one place the technologies, concepts, theory, concerns, problem statements and problem-solving methods spread across the Still repository. It draws on the repository's documents from 2026-09-05 to 2026-10-13 (to 2026-10-14 for the prompt audit and clean-up, §6.4), the key decision issues and pull requests, the commit history and the owner's research notes. It also integrates the argument of the industry and business whitepaper *[Whose Agent?](whose-agent.ko.md)* (Korean, v0.3.1). The original whitepaper stays unchanged because its reviewed edition is pinned.
>
> **Not normative:** This is not a contract or a specification. The repository's actual rules are set by [AGENTS.md](../../AGENTS.md), the [Development Constitution](../development-constitution.en.md) and the English contracts. Where this document and a source differ, the source wins. This document does not change `delivery-plan.yaml`, runtime authority or the active goal.
>
> **Language:** Korean is the source language. This English edition is a translation of the [Korean manuscript](personal-agentos-compendium.ko.md) with the same structure and claims.
>
> **Authorship and review:** An AI coding assistant wrote this at the owner's request, using sub-agents of the same family to sort the sources. Its review is self-review by the same authoring AI, not independent expert or human review.

---

## Introduction: if Jarvis's brain were borrowed

Suppose J.A.R.V.I.S. were an AI service run by another company. Smart enough, always available to talk, able to handle complex work. How much of his work and information could Tony Stark hand to it?

The answer does not depend on intelligence alone. Where does the memory of who he is live? Who decides how far the assistant may act? What happens if the provider changes its terms or shuts down? When a better AI appears, can he swap only the intelligence and keep the context and the work in progress?

The questions come down to one.

**Is Tony using his own Jarvis, or another company's service that he calls Jarvis?**

Still is an experiment on that question. The repository's opening line puts it this way: **the brain can be borrowed; the assistant should remain yours.** Most of us will never own the most capable AI brains. Providers change models, prices, policies and availability. So can we borrow the intelligence and still keep, as our own, the assistant that understands us and has worked with us?

[VISION](../VISION.md) says first that the project is not the final answer. It is an attempt to explore, in working software, what a personal agent serving one owner could and should become, and to turn that ideal into something that can be run, criticized, measured and continued by others.

> **It does not have to be the final answer. It should be a meaningful beginning.**

This document is a record of that beginning. In the roughly forty days from the first commit on 5 September 2026 to 13 October, the repository accumulated about 860 commits, 715 issues (numbered up to #1482 together with pull requests) and 188 documents under `docs/`. They mix first hypotheses, designs dropped quickly, failures found in real use, and the principles drawn from those failures. This document rewrites them in the order: what was worried about and why, how it was solved, what was dropped, and what is not yet confirmed.

---

## Summary: ten core claims

1. **Supplying intelligence and keeping a person's continuity are separable layers.** Supplying an AI model and keeping one person's context, delegations and ongoing work are different roles. One company can provide both, but nothing requires binding them into one irreplaceable whole. (Chapter 1)
2. **Separating technology layers is not the same event as strengthening user control.** The history of the internet and mobile shows that separation can open new business space and also create new control points. (Chapter 2)
3. **The kernel is agent-independent.** Ten concepts — Owner, Context, Memory, Artifact, Capability, Runtime, Grant, Work, Event and Evidence — define the state that survives when models and tools change. Installing is not authorizing. (Chapter 5)
4. **AI is the engine.** The owner's chosen AI does the work; a decision model chooses the worker and the brief and judges the result. Still code never implements a specific request. What remains for it is to guard owner state, authority, secrets, payment approval and evidence, and to remove the blockers in the AI's way. (Chapter 6)
5. **Moving memory is transferring meaning and control, not exporting data.** Data, meaning and continuity of use are different levels, and moving memory is not copying authority. The repository moved its memory policy toward "save without asking, tell the owner, allow an exact undo." (Chapter 7)
6. **Presence is an experience in which continuity shows, not a tone of voice.** The problem found in real use was not tone but projection: internal machinery leaking into the conversation. The owner manages Still by talking to it; pages exist only for results text cannot carry and inputs conversation cannot safely carry. (Chapter 8)
7. **"Done" is not proof that it was done.** A request, an observation, a service's processing receipt and a ledger registration answer different questions. The repository separates requested, observed, failed and unknown, and never writes an unconfirmed effect as success. (Chapter 10)
8. **Borrow the brain and the hands; keep the record and the say.** Whichever hand acts, Still decides what it may do, defines the delegation, judges the result, and keeps memory, judgment and history. The owner's own way comes before any default. (Chapter 11)
9. **Domain know-how is skill content, not code.** Site or domain know-how exists only as versioned content that the owner's AI chooses to load, and it creates no authority. (Chapter 12)
10. **Evidence classes are never mixed.** Design, schema, fixture, unit test, CI, local operation and live-account observation are different evidence. Weak evidence does not support a strong claim. This is also the principle most often broken in this project and then re-established. (Chapters 16 and 18)

---

## How to read this document

### Status labels

The repository separates "built", "works" and "observed in real use" strictly. This document follows that separation and puts these labels at the end of a section or next to a key claim.

| Label | Meaning |
|---|---|
| **[Case]** | A confirmed external case with a source and a date |
| **[Hypothesis]** | An industry or product hypothesis. Cases do not prove the whole hypothesis |
| **[Proposal]** | A design proposal or research conclusion. Not implementation or adoption |
| **[Contract]** | A rule set by a normative repository document, regardless of implementation |
| **[Implemented]** | Confirmed by code and unit or integration tests. Excludes live accounts and services |
| **[Observed]** | Actually observed on the owner's installation or live accounts |
| **[Historical]** | A superseded or dropped decision, kept only as evidence of its own scope |

### Issue numbers

A number written as `[#712]` is an issue or pull request in the GitHub repository [Jongtae/agentos](https://github.com/Jongtae/agentos). All dates are in 2026 and follow the source document or issue.

### Structure

- **Part 1, The problem,** asks why the project is needed. The whitepaper's industry argument starts here.
- **Part 2, Identity and core concepts,** covers what the product became, its foundational concepts and the principle that AI is the engine.
- **Part 3, Problems and solutions by area,** treats memory, Presence, collaboration, authority and evidence, connection and delegation, skills, and roles in the order problem → deliberation → decision → status.
- **Part 4, How it is built,** covers the Development Constitution, reuse first, the philosophy of verification, and humans and AI building together.
- **Part 5, The record of trial and error,** collects dropped decisions and a timeline.
- **Part 6, Industry and business,** covers the whitepaper's business directions and counterarguments.
- **Part 7, Open questions and the verification agenda,** sets out what is not yet confirmed.
- **The appendices** hold a glossary, a document map and references.

---

# Part 1. The problem

## 1. Whose agent? Separating the supply of intelligence from personal continuity

### 1.1. The central hypothesis

The project starts by separating an AI model from a personal agent. An AI model is a component that supplies intelligent processing such as reasoning and generation. A personal agent is a persistent system that keeps a user's goals and context and organizes several means of execution.

In this view a personal agent's identity is not set by a model name alone. What the user wanted, which preferences they corrected, how far they allowed action, what work is in progress and what actually changed in earlier attempts are all part of that identity. The whitepaper calls this the **operating context that builds up between a person and their agent**. **[Hypothesis]**

When supplying the model and holding the operating context are bound into one service, choosing a model is no longer just a comparison of performance and price. The user has to weigh the new model's advantages against the burden of rebuilding their context.

> **Does the user choose the AI provider, or does the context already built up limit the user's choice?**

This is the core of the lock-in the project deals with. It goes beyond being unable to download data. It is the question **whether you can carry on with your work after you leave**.

### 1.2. Model differences should show up as differences in quality

Models should not be treated as interchangeable like network carriers. Change the model and its judgment and quality change too. Being able to replace a model is not a promise that every model gives the same result.

**Differences between models should show up as differences in the quality of work. A user should not have to give up their context and control first in order to compare them.**

A logical separation of layers does not mean splitting companies either. An integrated provider can offer a convenient product and still let users move their memory and work state; that does not conflict with this direction. **[Hypothesis]**

### 1.3. The question for the next agent

The whitepaper's closing sentence is also the question of this whole document.

> **The question for evaluating the next agent industry should add, to "how smart is it", the questions "whose context does it keep, under whose authority does it act, and in whose interest?"**

---

## 2. Learning the conditions for separation from history

### 2.1. From integrated online experiences to separable services

Early online services offered many activities in one environment. According to the Computer History Museum, CompuServe and its competitors offered user-generated content such as discussion groups, commercial news and services, and email and chat together.[^compuserve] Choosing a service meant choosing much of one's online experience with it. **[Case]**

The open distribution of the web and the design principles of the internet matter for understanding how that bundle came apart. CERN put the web software in the public domain on 30 April 1993 and later distributed it under an open licence.[^cern] RFC 3724 explains the relation between the end-to-end principle, service innovation and user choice, noting that it is generally easier to change the systems at the ends than the inside of the network to create a new service.[^rfc3724] **[Case]**

The interpretation drawn here: **when the provider of the base technology no longer needs to decide every experience built on top of it, there can be more room for new services to appear and compete independently.** The base provider does not become unimportant. A new business boundary can appear between base supply and the end-user experience. **[Hypothesis]**

### 2.2. The mobile lesson: differentiation also creates new control points

Apple's 2008 iPhone SDK and App Store announcement opened a path for outside developers to build and sell applications. The same announcement stated Apple's approval of outside applications and exclusive distribution through the App Store.[^apple2008] The UK Competition and Markets Authority's 2022 mobile ecosystems study described Apple's and Google's influence and the limits on competition around operating systems, app stores and browsers. That is an agency's judgment about the UK market at that time.[^cma2022] **[Case]**

**Separating technology layers is not the same event as strengthening user control.** Even if models and agents separate in AI, a new agent platform that again monopolizes memory, authority and service access only moves the lock-in. So alongside "which layer appears" one must ask "who can change that layer". **[Hypothesis]**

### 2.3. Conditions, not endings

A historical analogy is not grounds for naming the future winner. The material above does not support concluding that network operators were uniformly trapped in infrastructure or that control kept moving to users. Applied to AI, three conditions need examining.

- Is there a stable connection boundary that outside businesses can use?
- Can users change providers without the burden of losing context?
- Do participating companies have an economic reason to open and maintain connections?

If these conditions are weak, layers that are technically separable stay bundled in the market. **A historical analogy is a starting point for finding the conditions under which separation holds.**

---

## 3. The user-side agent layer

### 3.1. What to separate and what to keep connected

The whitepaper proposes the following division of responsibility. It is a model for examining industry and product design, not a description of what Still has implemented. **[Proposal]**

| Responsibility | Value provided | What to check when the provider changes |
|---|---|---|
| Supplying intelligence | Reasoning, generation, planning | Changes in quality, cost and supported features |
| Execution environment | Running tools, browsers, local programs, external services | Actual reachable scope and results |
| Personal context and memory | Preferences, corrections, relevant material, ways of working | Preservation of meaning, source and scope |
| Continuity of work | Work in progress, reasons for waiting, failure and resume state | Whether work can continue without duplicate execution |
| Authority and evidence | Delegation scope, approval, cancellation, confirming actual results | Whether re-approval is needed and how trustworthy records are |
| User experience | Conversation, intervention, recovery, persistent Presence | Whether the user feels it is the same work continuing |

### 3.2. Five pillars

The README reduces the same idea to five criteria. They are not features of a model but "the criteria by which we judge the assistant we are building". **[Contract]**

- **Presence:** an assistant that stays with me where I already communicate, instead of making me open a separate app each time.
- **Portable Memory:** my memories and context are not locked into a single AI service.
- **Model Independence:** I can replace the AI model without losing the continuity of my assistant and our relationship.
- **Execution:** it doesn't only answer questions; it can act in real services and on computers.
- **Owner Control:** I retain control over my data, permissions and the outcomes of execution.

The README adds that these are "guiding principles, not a claim that every scenario already works".

### 3.3. Being able to choose is not having to choose every time

In this structure the user does not manage every internal component. **Being able to choose is different from having to choose every time.** Good defaults and an integrated experience stay; when needed, the user can change providers, withdraw authority, move data and check what was actually executed.

VISION says the same from another angle: the complexity underneath may be substantial, but **the owner should not have to become the operator of that complexity.** Later this becomes concrete in "never make the owner operate the machinery" (C16) and "the owner manages Still by talking to it" (#880).

"User ownership" also needs to be precise. It does not mean legal ownership of all software and information. It means a **practical ability to control**: to check and correct memory within what is permitted, to limit and withdraw delegations, and to move the context needed when changing services.

Local installation alone does not give that control. A local product can still lock context into a proprietary format and make moving hard; a managed service can be built with enough means to move, verify and withdraw. Repository documents keep repeating that "local install is not local-only processing": a subscription AI still runs on its provider's servers, so context is transmitted outside. This project therefore handles control by **making destinations and consent visible** rather than by keeping everything local.

**The key is not location but boundaries: what information is where, who may do what, and how the user can change that relationship.**

---

# Part 2. Identity and core concepts

## 4. How the product identity changed

Still's identity changed several times in forty days. What did not change was a single question: borrow the brain, keep the assistant. What follows is a record of how the container for that question changed.

### 4.1. Start: a multi-tenant platform on Kubernetes (2026-09-05)

The first execution design pictured Still as a **Kubernetes-based agent platform**: an API Deployment, a dispatcher, Runtime Jobs, connectors, PostgreSQL and workspace PVCs, isolated per user by namespace, with default-deny egress. It inherited the principles "runtime first, user data ownership, explicit external connections, execution evidence recording" from an even earlier prototype repository (`agentos-os-prototype`).

The first pivot happened inside that design document. The product unit changed from *per-task execution* to *a persistent environment per owner* ("post-agreement change: personal Still"). The document also contains a sentence that survives today: "product work state is owned by PostgreSQL, and Kubernetes owns the state of execution resources." It is the first form of the idea of separating durable state from replaceable workers. **[Historical]**

### 4.2. A one-day pivot: a single installed process (2026-09-06)

The Kubernetes direction was dropped almost at once. On 6 September Still 0.2 ran as a single local process installed with Homebrew. It normalized the tool-call formats of OpenRouter, Ollama and Anthropic into one loop and read files only inside connected folders. It passed 42 automated tests and five acceptance cases in the same conversation. A later plan fixed the direction: "no Docker or Kubernetes dependency; Kubernetes is a deployment option, not a prerequisite." A document of this period says it is "not a feature that displays internal model reasoning" — an early form of the principle of showing real tool events but not reasoning. **[Historical]**

### 4.3. Hub v2: the Mac, a personal bot, engines (2026-09-07)

The next day Hub v2 separated four layers: the Mac (hardware, OS, original files), Still (an always-running personal-state runtime), Codex and Claude Code (reasoning and execution engines called per task), and a control plane (account and operations metadata only; no message bodies, documents or bot tokens). The owner created their own Telegram bot through BotFather and connected it. The key sentence was **"Still is not a mere relay"**: it decides which assistant to assign, supplies only permitted context, and holds tool execution, approval, cancellation, retry and evidence. In the same period the external project b3os was reviewed as a design reference, giving the first instance of the reuse discipline "adapt the principles, take no code". **[Historical]**

### 4.4. Master Plans and the first closeout: "mock-contract complete" versus "operating" (2026-09-08)

Master Plan 1 paired design (D) and implementation (I) iterations for the Personal Space, the capability lifecycle, Google Drive reading, an A2A delegation test, creating a Google Calendar event and a bounded ReAct loop. Master Plan 2 addressed "don't make the owner leave the conversation to understand settings". Both chose to validate against mock contracts **before any real credential was entered**.

The first closeout on 8 September ([#282]) set this project's evidence discipline for the first time. It declared "development complete" on repository and CI evidence while explicitly excluding real subscription login, external connections, operating deployment and real-use success. The rule was: "An implementation defect, missing technical environment proof, stale candidate or unresolved review finding may never be classified as owner setup." Do not relabel an engineering gap as "the owner just hasn't logged in yet".

The discipline was not perfect from the start. MP1's original D-01 to I-06 work produced only unconnected mock adapters, with no policy-owning orchestrator actually wiring them together. Only after a remediation sequence, MP1-R-01 to R-05, could "development complete" be said honestly. In operating readiness, OP-01's "ready" claim turned out to be false (the image contained no subscription-engine executable), and OP-02 corrected it. **The evidence discipline did not hold by itself; it had to be re-established again and again.** **[Historical]**

### 4.5. Personal AI Kernel and Agent Distribution Platform

In this period the product's long-term identity was set down in one sentence.

> **Still = an owner-authoritative Personal AI Kernel + an open Agent Distribution Platform**

The kernel is **agent-independent**; the product experience and the distribution platform may be **agent-centric**. Some day the owner will install and replace better agents like apps, but memory and authority stay in the kernel. The documents also repeat what the product is not: not "a chatbot", "a coding harness", "a swarm framework", "a host kernel or hypervisor" or "a replacement for macOS or Linux". They take particular care that the GitHub issues, pull requests, CI and delegation automation used to build the repository are not mistaken for the product itself.

The Agent Distribution Platform foundation (D-AP-01, [#334]) fixed the schemas for the ten kernel concepts and the AgentPackage manifest with RFC 2119 rigour. The follow-up work in that line and the "useful default agent" issues, however, were closed as not planned on 2026-09-24 ([#523]). The platform remained a long-term direction, and the immediate focus moved to an assistant genuinely useful to one person. **[Contract]**

### 4.6. Real use changes direction: Presence → Secretary → C16 → Connect

From late September, problems found in the owner's own use set the direction. Commits cluster in this period too (122 on 24 September alone, 55 on 28 September).

- **2026-09-23, Presence research.** From the owner's real use it recorded that Telegram felt like a task and status console, that failures sent the owner to a management screen instead of continuing the conversation, and that the configured AI and the actual execution path differed. The problem was not tone but **projection: internal concepts leaking into the conversation**. (Chapter 8)
- **2026-09-24, the decision model.** A model-backed judgment boundary was chosen instead of a rule engine ([#415]). (Chapter 6)
- **2026-09-26, Secretary.** The owner raised the problem that the decision model had become a rule-bound blocker instead of a gate for finding alternatives. "It's been narrowed to book search" was emblematic. The worldview in the documents (secretary, personal memory, attention, goal-directed action, family collaboration, replaceable capabilities) differed from what the code did. The Secretary Agency program followed ([#653], [#662]), and the **pilot posture** removed protection layers from the request path. (Chapter 10)
- **2026-09-27, C16 "AI is the engine".** A constitutional principle came out of the realization that the repository was accumulating per-request features — directions, carts, particular kinds of lookup — in Still code ([#712]). (Chapter 6)
- **2026-10-10, Connect.** CONNECT-01 ([#1295]), which handles every service through the flow of the Connection and Delegation Contract, became the active goal ([#1302]). The Secretary program paused at an exact resume point. (Chapter 11)

### 4.7. The secretary is a starting point, not a boundary (2026-10-10)

The Professional Roles and Communication Contract of 10 October widened the product thesis. **Still is not a secretary-only app but an owner-centred operating environment for many professional roles.** The secretary is one role among finance, investment, research, purchasing, travel, projects and health coordination, and only today's starting point. The same contract drew the line that a role creates no authority. (Chapter 13)

The current public release is `v1.3.0` (2026-10-10). The [product status](../product-status.en.md) page records the published release, current main, owner pilot recordings, the README demo and product direction as different kinds of evidence. Autonomous purchasing, arbitrary computer use, universal web verification, live stock and checkout verification, arbitrary third-party package execution and a public marketplace remain "no current claim". **[Observed]·[Contract]**

---

## 5. Kernel concepts: what survives when the model changes

### 5.1. Ten basic concepts

Still's conceptual system came from one need: **a seam so that "the AI, model or runtime changes" never implies "the owner's state changes".** So durable, owner-authoritative things were separated from replaceable workers at the level of concepts. These ten barely changed from the early PRD to today's architecture document. **[Contract]**

| Concept | Definition |
|---|---|
| **Owner** | The single human principal Still protects, with identity, policy, devices and trust settings. Authority does not pass to a package or marketplace |
| **Context** | Bounded, attributable information issued per Work. It expires and does not automatically become memory |
| **Memory** | Canonical memory under Still authority. What a third party submits is a `MemoryCandidate` until accepted |
| **Artifact** | An owner-owned durable result or material, with content digest, lifecycle, retention and provenance; original, derived, draft and final stay distinguishable |
| **Capability** | A callable action contract registered with Still. Registration is neither enablement nor authority |
| **Runtime** | A replaceable execution adapter: model provider, Codex or Claude Code, local model, delegated framework. It cannot mint authority or certify its own completion |
| **Grant** | The only authority record. Issued by Still from an owner or policy decision, bound to subject, Work, scope, destination and expiry. Ambiguity fails closed |
| **Work** | The durable execution lifecycle Still owns. Only Still settles the terminal state |
| **Event** | An immutable fact or request bound to an Owner or Work. Neither a Grant nor proof that an action occurred |
| **Evidence** | A sealed record that names its evidence class. A stronger class is never inferred from a weaker one |

Two concepts were added. An **AgentPackage** is the installable product unit, with a manifest that *describes* identity, capabilities, scopes, budgets, approvals and provenance; the manifest cannot create authority. A **ContextSnapshot** is Work-scoped context whose `canonicalMemory` is always false.

### 5.2. Installing is not authorizing

The single line most repeated in the repository is this:

> `downloaded != installed != enabled != connected != authorized-for-action`

Downloaded, installed, enabled, connected and authorized to act are all different states. The line is there to prevent the common confusion in which a package that exists, is enabled, is signed or is popular gets read as "may act". Trust is also split into dimensions: publisher identity, signature and integrity, build provenance, static validation, behavioural conformance, permission risk, incident history, popularity, user reviews and curation are separate signals. Writing "verified" means saying what was verified. Popularity never grants permission (Constitution C3, C9). **[Contract]**

D-AP-01 found three places in existing code that did not meet the principle: the old plugin install model that recorded an enabled host action, non-cryptographic fixture digests, and the old capability registry that stored scope lists. It declared them "non-conforming" instead of quietly reinterpreting them as meeting the new schema. **[Contract]**

### 5.3. Context is not memory

Task context is bounded and attributable. A whole conversation or a package's internal state does not automatically become the owner's canonical memory (Constitution C5). The early Personal Space design said the same: only explicitly saved memory is canonical, and "complete conversations, model reasoning, search results and file content never become automatic memory." This principle is later refined into an exception — the AI the owner chose saves without asking but tells the owner and allows an undo (Chapter 7). Third-party writes remain candidates. **[Contract]**

### 5.4. Who declares completion

Work follows a fixed state machine. `planned → ready → running → completionProposed → completed` is the basic flow, with `waitingApproval`, `cancelled` and `failed`. `failed → ready` is allowed only under bounded retry and idempotency policy. A worker's report is an observation, not completion; Still validates completion.

The execution contract states it more sharply. "No error was raised" is not completion. **Discovery is not a weather lookup; HTTP success is not a grounded answer; exit code zero is not completion.** An earlier failure stays in the audit even if a later route meets the goal. Step results carry fixed codes such as `ok`, `partial`, `input_required`, `connection_required`, `approval_required`, `policy_denied` and `effect_unknown`. **[Contract]**

### 5.5. Evidence classes

D-AP-01 named and ordered evidence classes: design specification, schema validation, deterministic fixture, repository review, repository CI, local operation, authenticated connection, live external operation. **A stronger class cannot be inferred from a weaker one.** The distinction is used for product claims, for the development process and for the status labels in this document. In skill evaluation the same idea reappears as U (unit and fixture), I (model-free integration), P (live public read without an account), S (static and structural) and L (live model and account). **[Contract]**

### 5.6. Owner control: six observable rights

For "control" not to end as a slogan, it has to be falsifiable. The Owner Control Contract sets six rights and requires each to have a real enforcement point and both allowed and denied evidence. **[Contract]**

| Right | Content |
|---|---|
| OC-01 Inspect | Before installing, see publisher, source, version, digest, location, dependencies and requested authority |
| OC-02 Choose data | Choose and check which folders, memory and accounts were used |
| OC-03 Know destinations | Know whether something stays local or goes to a model, a remote agent or a recipient |
| OC-04 Bound actions | Keep install, connect, read, write, remote change, send, pay, delete and background distinct |
| OC-05 Stop and revoke | Block new calls and queued replays, and reconcile in-flight uncertainty instead of calling it "safely undone" |
| OC-06 Keep and replace | Removing an agent keeps results and memory; a replacement agent inherits no authority |

Actions that look alike are also separated. **Disconnect ≠ revoke ≠ remove the agent ≠ forget or delete.** Remote effects already completed cannot be cancelled retroactively, and local deletion is not proof of remote deletion. Control must not become owner micromanagement either: low-risk reads already covered by explicit authority do not trigger repeated approvals.

### 5.7. File workspace: a material layer that protects originals

The first useful product slice used the owner's files and folders as material and conversation and work as the experience. There are five storage boundaries. **Originals** are never replaced by a summary or a conversion. **Derived material** sits separately with its source relationship. **Work products** separate draft and final. **Rebuildable indexes** sit separately. **Durable internal state** (work queue, approvals, evidence, recovery, credentials) is "not disposable cache".

Access boundaries are explicit. A connected reference folder is read-only by default. A folder grant does not mean home-directory access, arbitrary shell, overwriting originals, deletion, bulk moves or external transmission. Two anti-patterns were rejected: a file picker that keeps asking for an item already connected, and becoming a cloud sync service. **[Contract]·[Implemented]**

---

## 6. AI is the engine (Constitution C16)

### 6.1. The problem: per-request features piling up in code

On 2026-09-27 the owner pointed out that the repository was **accumulating per-request features — directions, carts, particular kinds of lookup — directly in Still code**. That signalled Still turning into a scripted task executor instead of a layer that orchestrates and guards authority. The realization became Constitution C16 ([#712]).

### 6.2. The principle: the AI works, the decision model orchestrates, Still guards

> **The owner's chosen AI does the work of meeting the owner's needs as a secretary.** It may be a subscription CLI (Codex, Claude Code) or a configured model API. The AI searches, reads, browses, reasons, writes and acts through its tools. Still never re-implements that work.

- **The decision model orchestrates.** For each request it chooses which AI tool and model to use, writes or adjusts the brief (goal, relevant context, completion criteria), evaluates the result, and re-delegates with an adjusted brief or a different worker when the goal is not met. It does not do the task itself and is not a rule engine.
- **Still code keeps only what an AI must not own:** canonical owner state, Grants and approvals, secrets, payment and consequential-action approval, budgets, Work, Event and Evidence, and recovery. Its other job is to **remove the blockers that stop the AI tools**: approval layers that duplicate Still's own, login friction, rendering instability, tools not exposed, wrong routing.
- **Never implement a specific request in code.** No per-task features, no branches named after a site, provider, category or task, and no request-specific guidance text in runtime code. When a request fails, fix the blocker or the orchestration decision, not the task.
- **Prefer known public code.** A custom build needs a concrete unsupported contract (C15).
- **Never make the owner operate the machinery.** No context-reset commands, setup rituals or manual steps that an AI or Still could handle.

Every proposal and pull request must answer one question: **"Which AI does the work here, and is this change removing a blocker or orchestrating — or is it scripting the task?"** Scripting the task is rejected. A static scanner (`test_no_scenario_code.py`) also enforces the principle. **[Contract]·[Implemented]**

VISION reduces it to one line: **borrow the brain and the hands; keep the record and the say.**

### 6.3. The judgment boundary: judgment, policy, execution

Even before C16 the repository was asking where "judging meaning" should live. Agent loops usually let one general model do both generation and small judgments such as relevance, routing, confidence, completion and reference resolution. Moving those judgments into code produces "an accumulating rule engine" in which keyword and regex branches pile up with every new phrasing.

On 2026-09-24 the owner chose model-backed semantic judgment as the default ([#415]). The first code boundary ([#417]) separated three things. **[Contract]·[Implemented]**

| Layer | What it does | Who |
|---|---|---|
| Judgment and inference | A bounded answer with uncertainty (`choose`, `score`, `judge`, `choose_many`, `structured`) | A replaceable decision engine |
| Policy and authority | Thresholds, escalation conditions, authority checks | Still's deterministic rules |
| Execution | Calling capabilities and runtimes under current Grants | Runtimes |

> **A decision engine returns bounded judgment. Still policy decides what may happen next.**

Deterministic code is kept for invariants that must be exact: authority, approval binding, idempotency, effect and evidence qualification, cancellation and revocation, schema and protocol validation. Anything about meaning — language, intent, relevance, reference, routing, choice of recovery, conversational projection — normally stays behind the model-backed judgment boundary. A model cannot mint or widen authority, skip approval, change canonical memory on confidence alone, rewrite evidence or certify its own completion.

The owner chooses the decision engine's route (direct API, Jev, subscription CLI, off). A route is recorded only when the owner explicitly activates it; saving a key or checking a login does not change it. Before activation a route must pass a fixed qualification suite (`decision-qualification/4`). When the judgment AI follows the main AI, it starts from the cheapest qualified model. The documents record the remaining risk too: no flag stopped Codex's global instruction file from reaching the judgment model, so activation is refused unless the installed version is qualified. No live judgment with the owner's accounts has been observed yet. **[Implemented]**

### 6.4. How orchestration evolved: remove rather than add

On 27 September the decision engine became the actual per-Work orchestrator ([#710]). In the first design the judgment AI *rewrote* the owner's request into `brief.goal` and `completion_criteria` and chose a subset of tools for the worker. In real use this caused problems. The owner's diagnosis: **"almost every live failure came from Still layers between the owner and the AI worker, not from the model."**

There is a representative case. On 28 September the owner said, "I already had lunch." It was a statement, not a question. The plan wrote "short acknowledgement" and gave no tools. The worker and the judgment ran three times, and because the judgment read only tool observations and not the owner's words, the Work was stored as `failed`.

The fix was to find the root cause. The owner said, "Finding failures one at a time does not scale. Find the root cause." So **every deterministic rule and judgment on the request path was censused line by line** ([#820]). Each of 63 rule rows was classified by what it protects: secrets (a), payment approval (b), no replay and truthful effects (c), memory authority (d), private-document egress (g), isolation (i), resource bounds, or nothing (e). The result was clear. Every rule protecting a, b, c or d was kept. Of 23 rules protecting nothing, 9 were removed, 7 were reduced to notes or labels, 4 were kept as harmless dedupes and 3 were deferred as setup blockers. [#826] removed all 8 g rows.

Three rules remained. **[Contract]·[Implemented]**

1. **The owner's words reach the worker verbatim.** The plan may add only optional notes (`brief.notes`); it never restates, narrows or extends the request.
2. **The AI's answer is always delivered.** The only exception is the mandatory sentence stating that an external effect is unknown.
3. **One judgment decides the outcome.** It asks whether the answer served the owner's message in the context of the conversation (`goal_reached`). Tool results are evidence, not the test itself.

The command and intent rule layer in front of the worker was also downgraded to "a hint, not a route" ([#832]). What a natural-language rule judged now reaches the worker only as a factual note marked `[Still observation, not an owner instruction]`. Re-delegation happens at most twice and never after a consequential effect.

The lesson is one of the most important in this project. **When something fails, before adding a guard or a branch, suspect the layers that have crept between the AI and the owner, and remove them.**

**The same discipline for prompt text ([#1507], [#1508])**

On 14 October the same question was asked of the prompt text that reaches the models, not of the code ([#1507]). Reading the repository code and commit history (at `73ecfe8`) found:

- Model-facing prompt text was spread across at least 8 modules.
- The worker's system prompt was about 8,800 characters: 59 sentences on one line.
- Since 1 September, 19 commits had changed that prompt. Most added one sentence per incident, and none removed one.

As a result, four problems reached the model unchanged.

1. **A rule the owner removed was still there.** After [#826] removed the separation between private reads and web search, the sentence "do not send file contents through web search" remained.
2. **Instructions contradicted each other.** "Ask a concise question if required context is missing" stood next to "look it up first instead of asking; do not ask for routine choices". The owner's policy is the second ([#918]).
3. **The same method appeared in two places.** The way to collect candidate connection routes appeared in almost the same words in the planner question and in the worker prompt. A fix to one would leave the other unchanged.
4. **Some sentences sat on the C16 boundary.** Request types were named in the core prompt, such as "use this tool for a product comparison or travel plan" or "about three checks a day".

Public sources pointed the same way. **[Case]**

- Anthropic's context-engineering article says to divide a prompt into sections. It also says not to pile up lists of edge cases, and to add instructions for observed failures and then prune them again[^context-eng].
- OpenAI's GPT-5 prompting guide says GPT-5-family models waste reasoning on reconciling contradictory instructions, and recommends an explicit priority order[^gpt5-guide]. The Main AI is in this family ([#1431]).
- OpenAI's prompt optimizer has a contradiction-checking stage[^optimize-prompts]. Instead of adopting the tool, the project ran the same check once.
- A study of instruction counts (IFScale) found that reasoning models follow hundreds of instructions well[^ifscale]. So the problem was judged to be contradictions, stale rules and duplication, not length.

The proposal-time reuse review (C15) ended with no prompt framework.

- Prompt-file formats such as dotprompt[^dotprompt] and POML[^poml] bind model settings into the prompt. That does not fit a decision model that chooses the model per request.
- Optimizers such as DSPy need repeated live-model calls, which conflicts with [#612].
- OpenHands' Jinja2 templates would add a dependency, and `str.format` covered the placeholders needed[^openhands].
- Cline removing its component and variant registry for short per-mode templates was read as a warning sign[^cline].
- So only OpenAI Codex's format of prompts kept as Markdown files was adopted[^codex-prompt]. There is no new dependency.

The clean-up took three steps. **[Implemented]**

- **Move only ([#1511]).** 15 prompts moved to `src/personal_agent/prompts/*.md`. Digests confirmed that every assembled string was byte-identical before and after the move.
- **Consolidate ([#1513]).**
  - The worker prompt and the planner question were divided into sections, with one priority order stated at the top. The order is: invariants Still enforces in code, then the current request, then saved preferences, then the default working style.
  - The four problems were fixed. A change table records each changed sentence with its original issue and the reason.
  - The owner's AI (Codex) read the assembled prompts three times to find pairs of sentences that cannot both be followed. Each one found was written as an exception next to the rule it qualifies.
- **Check.**
  - The 10 scenarios related to the change ran once each before and after the clean-up (EVAL-LOOP-01). The pass count was the same: 8 of 10.
  - The two scenarios whose score dropped on one dimension were re-run three times each. The result was 1 of 6 before and 4 of 6 after, so it was not a real regression.
  - These are isolated sandbox samples run on the owner's Mac, mostly single trials. They are not a general quality claim. **[Observed]**·isolated sandbox sample

The same clean-up addressed one gap in procedural memory ([#1510]).

- The function-library instruction applied only when "a set has no saved functions yet". So once a read function was saved, change steps such as an order were no longer kept.
- The instruction became one interface sentence: a library keeps every step that worked, a change as well as a read, and a saved function never stands in for an approval.
- A change step being saved has not yet been observed. **[Implemented]·awaiting observation**

The lesson is the same as the rule census. **Prompt sentences that are only ever added end up contradicting each other. Prune as much as you add, and keep each rule in one place.**

### 6.5. Skills are content (C16 amendment, #974)

C16 originally banned "task-named guidance text" as well as site and category branches. That would have made even a skill package carrying site knowledge a violation. The amendment of 2 October ([#974]) separated core code from declared package content.

- Domain and site know-how may exist only as **explicit, inspectable, versioned skill content** in an AgentPackage, with pinned identity and licence.
- The owner's AI chooses whether to load it. The only way skill text enters a Work is the model's own `skill_load`. Core code never injects or selects skill text on its own.
- A skill cannot grant authority, change host behaviour or certify an unobserved effect. Core code never special-cases a particular skill by name or content.
- Moving scripted behaviour into another core module, or into text that core code injects on its own, is not compliance.
- A skill that Still itself writes and bundles gets the same review as an external one. It must be a reusable method, not the script of one test scenario, and the owner must be able to switch it off or remove it on its own. **[Contract]·[Implemented]**

### 6.6. Changing only within the main AI's family (#1431)

The first orchestration idea had the orchestrator choose freely among workers from several providers. On 2026-10-12 the owner narrowed the worker catalogue to **only the current main AI while it can run** ([#1431]). A plan varies the model and the effort, not the vendor. This narrows the original idea considerably, and the documents give no detailed justification beyond the owner's decision. **[Contract]·[Implemented]**

### 6.7. Remaining tensions

- **The decision engine and mid-work correction.** When the owner steers a Work in progress, the steer reaches the running worker but not the orchestrator, so a re-delegation plan can be made without the correction. Chapter 13 returns to this gap.
- **The decision on specialized judgment APIs keeps being deferred.** Whether to promote a specialized judgment route such as Jev has repeatedly ended as "evaluate, but defer the decision" ([#609]).
- **The boundary of deterministic code.** The line "meaning to the model, invariants to code" is clear, but a few keyword rules awaiting the owner's decision remain in code (for example "book" among calendar verbs). They are recorded as "pending owner disposition" rather than silently accepted or fixed.
- **Prompts can pile up again.** After the clean-up, the easy way to fix a prompt is still to add a sentence. The change table and the contradiction check were a procedure followed once, not a standing mechanism ([#1508]). The evaluation that measures prompt changes has limits too. The judge sees only the replies and not the tool results, so it sometimes marked recent product names that a search had found and sourced as invented.

---

# Part 3. Problems and solutions by area

## 7. Memory and context

### 7.1. Moving memory has three levels

Defining Portable Memory as downloading a conversation history does not address the problem. The whitepaper separates three levels of moving memory. **[Proposal]**

- **Moving data:** bringing records over in a readable format.
- **Moving meaning:** preserving whether a record is a fact, a user preference or a model's guess, and whether it was later corrected.
- **Continuity of use:** applying that information appropriately in the new environment so the user does not have to repeat what they already explained or corrected.

If "I'd rather avoid beef on weekdays" is saved as "vegetarian", the data moved but the meaning was distorted. Carrying a temporary business-trip condition over as a permanent lifestyle preference is the same. So moving memory must carry, along with the content, its source, time, scope, whether the user confirmed it, its correction history and its validity period. What moves is stored information that can be checked; it is not a promise to move learning effects that exist only inside a model and cannot be extracted.

### 7.2. How this differs from existing transfer features

Memory transfer already exists. Anthropic's official guide describes importing memory from and exporting it to other AI providers, and says importing is experimental and may not always be applied successfully.[^claude-memory] So framing the problem as "nobody can move memory" is wrong. The next step is **transfer quality the user can check: what moved, what was left out and what meaning changed**. **[Case]·[Proposal]**

### 7.3. Moving memory is not copying authority

Knowing that a user prefers a particular bookstore is separate from the authority to buy from that bookstore account. Moving an ongoing purchase review is different from moving payment authority. A transfer result should therefore show **imported items, excluded items, conflicting items and items that could not be checked**. If an old record conflicts with a recent explicit correction, the old record is not revived. Imported sentences are reference data, not commands that grant new authority. Connection secrets are not moved inside a memory bundle. Existing material is not deleted automatically before the user has checked the result in the new environment.

The role ontology research turned the same idea into four categories of transfer result: **preserved / converted / lost / needs re-approval**. And it put it in one sentence: **"Portability of memory is not portability of execution authority."** The standard applies to Still itself. If it can connect many models but cannot be left, it has gained model independence but not enough user independence. **[Proposal]**

> **The final test of Portable Memory is not whether data can be exported, but whether the user can still control their context after moving to another environment.**

### 7.4. The repository's memory policy: from asking to telling

Constitution C5 makes third-party durable memory writes candidates (`MemoryCandidate`) by default, subject to review. That was a safety invariant. Real use exposed a different problem: *asking* about every fact to remember was itself a bad experience. It was especially odd to ask permission every time from the owner's own AI, which the owner already trusted with the work. The memory policy changed in five steps.

| When | Decision | Content |
|---|---|---|
| [#597] | Only explicit requests become canonical | An explicit "remember this" judgment and the owner's "yes" make canonical memory; otherwise a candidate |
| [#805] (09-28) | Owner-model upkeep | After a successful Work the judgment AI asynchronously proposes up to five facts, distinguishing "stated" from "inferred" |
| [#818], [#836] (09-28) | Ask only once per Work | Fixed a double-ask bug, show values not keys, removed machinery text like "the memory hasn't been saved yet" |
| [#918] (a) (10-02) | Save without asking, tell, undo | For the owner's own AI only, save immediately as canonical memory, with a "Remembered: …" notice and an exact 7-day undo |
| [#998] (10-04) | Self-review | After a failure or correction the assistant reviews its own misses, saving at most one lesson as memory and one Still-fixable cause only in evidence |

The owner's words that prompted [#836] are preserved: "It asked twice; answering the first should settle the later one too. And it handled Still like a system tool — our concept is presence." The decision behind [#918] was just as short: "I think these should just be handled without asking. Every act of asking is not a good user experience."

[#918] (a) did not repeal C5; it was recorded as the "separately reviewed policy" C5 allows, and C5's text names the exception. Package tools, delegated specialists and other third parties still write candidates. The boundary is set by the `third_party_memory_write` capability declaration, not by a package name. Credential-shaped values are still refused. **[Contract]·[Implemented]**

The documents also record the remaining risk. A `save_memory` induced by text on an untrusted web page now becomes current memory at once. Today this is mitigated only by attribution and undo; injection defence is parked (the "trifecta" proposal, parked 2026-09-28). That is a recorded risk, not a solution.

### 7.5. Current context: don't mix facts, interpretations and hypotheses

On 2026-09-26 the owner requested the current-context contract ([#625]). The goal is "to do useful work with fewer repeated questions, using what the owner already supplied, its source time and relevant observations", without turning an estimate into a measurement, consent or completed action.

The core of this contract is **a table of kinds of truth**. **[Contract]**

| Kind | Example | Character |
|---|---|---|
| Canonical memory | Home and work locations the owner confirmed | Existing memory policy; no automatic promotion |
| Source observation or report | A location shared at a specific time | What the source reported; no proof against spoofing or of permanence |
| Interpretation of a statement | The model's reading of "I'm working from home today" | An attributable interpretation, not sensor-verified |
| Current-state hypothesis | "Probably near home now" | Rebuildable, source-dependent, time-limited, correctable |
| Habit or pattern | "Usually at work on weekday mornings" | Future scope; not evidence the person moved today |

The rules are concrete. A future appointment is a plan. A forwarded location is a reference. A quoted or third-person statement is not automatically about the owner. An empty calendar is not availability. A model summary is not a second independent observation. No invented numbers like "97% confidence"; use supported, uncertain, conflicting or unknown with evidence. A fresh correction of the same proposition may supersede an older claim, but **there is no global "newest always wins" rule across different subjects or time intervals.**

Time and place defaults are stated as engineering defaults, not accuracy guarantees: source and hypothesis retention 24 hours; freshness of a directly received current position 15 minutes; "working from home today" until local midnight; a context budget of 8 entries and 4096 bytes (inside the existing Work budget); habit learning off. There are pause and clear; clearing raises the **context epoch** so queued or late data cannot revive the cleared context. The feature is off by default on upgrade and the owner turns it on.

Understanding and disclosure are separated too. The full context a worker reasons with differs from the projection actually sent to a search or weather service. The weather tool receives a host-resolved `location_ref` instead of raw coordinates and uses the coarsest sufficient precision. Rounding is minimization, not anonymization.

On 4 October a **daily situation note** was added ([#1004]). With current context on, the existing owner-model upkeep rewrites a one-line note after each answered Work, with no new model call. It expires after six hours and is at most 400 characters. The document says it is "the model's summary of the same day's conversation: not the owner's words, not a measurement, not memory." **[Contract]**

The implementation design put reuse first. It builds no new context inbox, memory service, broker, event bus or conversation database; it adds tables in the same store and one tool the normal model calls mid-turn, `propose_current_state`, to the existing tool broker. There is no separate pre-pass or observer model. The temporal graph engine Graphiti was not adopted as a dependency for this scope: "reassess for a concrete temporal relation query, not because graph terminology sounds useful." Standard vocabularies (Schema.org, OWL-Time, PROV-O, SOSA/SSN) were borrowed as concepts only, with no runtime. **[Contract]·[Implemented]**

### 7.6. Owner model and decision journal: what is believed, and why it was judged so

The research of 28 September ([owner model and decision journal](../research/owner-model-and-decision-journal-2026-09-28.ko.md)) and four research notes from the same day dealt with two questions. What does Still believe about its owner (the owner model)? And why did it judge as it did (the decision journal)?

**The conclusion first: there is no open-source library to adopt as is.** Nine memory systems were reviewed and none could serve as canonical. Licences were almost all Apache-2.0 or MIT, compatible with an AGPL product. The obstacle was storage: Mem0 needs a vector store, Graphiti needs Neo4j or FalkorDB, Langfuse needs Postgres and ClickHouse. Adopting them would move canonical owner state outside Still. The conclusion was to **Build** Still's own SQLite schema by combining proven ideas. **[Proposal]**

The ideas borrowed:

- **Zep and Graphiti's** bitemporal fact model and "invalidate, don't delete".
- **W3C PROV's** provenance vocabulary (Entity, Activity, Agent; wasGeneratedBy, used, wasDerivedFrom, wasInvalidatedBy).
- **Generative Agents'** evidence links, so a derived belief carries the IDs of its evidence and can answer "why do you think so?". Consolidating in the background when importance accumulates fits the verification budget because it calls no model per turn. Recency decay is used only for ranking: a fact like an allergy, said once and never asked again, must not decay away.
- **Mem0's** history table and **Letta's** labelled, size-limited blocks and `read_only` flag.
- On the judgment side, **OpenTelemetry GenAI conventions** and the **MLflow Assessment** record shape — as an export vocabulary only, without tying the canonical schema to still-unstable conventions.

The research proposed one distinction specific to Still. Graphiti folds two cases into a single `t_invalid`.

> **"It was true then and isn't now"** (close the validity interval) differs from **"we were wrong"** (record the retraction time and reason).

Answering "why did you think X last Tuesday?" needs that difference. Wikidata's rank system (preferred, normal, deprecated; values never deleted) is the model. Belief precedence is stated > observed > inferred > hypothesis, and a hypothesis must have an expiry. The research also noted that hard-coding thresholds per category would violate C16: judging a contradiction is a judgment of meaning, so it belongs to the decision model or the worker, not to regex.

The key grounding for the decision journal is research. **Chain-of-thought does not faithfully reveal actual reasoning.** Even when models used a hint, they often mentioned it less than 20% of the time.[^cot] Research on agent visibility likewise names identifiers, real-time monitoring and activity logging, not chain-of-thought.[^visibility] So the decision journal records, instead of hidden reasoning: **[Proposal]**

- the decision label;
- a short "stated rationale" written by the model, marked as *the model's claim*, not a faithful reasoning trace;
- digests of input and evidence, and references to the beliefs and evidence used;
- tool calls and outcomes actually observed (requested, observed, failed, unknown);
- the model and version.

Thinking blocks and `reasoning`-typed content are neither stored nor exported.

User research pointed the same way. In a study of 2,050 memories from 80 real ChatGPT users, **96% were created unilaterally by the system**, 28% contained personal data and 52% psychological insights.[^selfportrait] None of the major commercial products offered a provenance link from a memory entry back to its source conversation, or cascading deletion; three providers' help pages all say deleting a conversation does not delete memories derived from it. When accurate, contextual inferences were shown, users reacted with curiosity rather than distress; discomfort came mainly from inaccurate inferences or third-party transmission.[^inference] The four conditions of Kay's "scrutable user model" from the 1990s — what the system believes, the evidence, the meaning, and the ability to change it — are still not met by commercial products in 2026.[^scrutable]

The research concluded: **a local ledger in which every belief carries its source, deletion propagates to derivatives and a deletion receipt remains** is where Still can differentiate. The owner's chosen AI does fact extraction, contradiction judgment and goal evaluation; Still code handles only validation, storage, time boundaries, provenance and deletion. The research summarized: "the gap found is one of accountability, not technology." **[Proposal]**

### 7.7. Forgetting is technically hard

Research on forgetting pointed out concrete SQLite pitfalls. `PRAGMA secure_delete` is off by default, and even when on, traces can remain in FTS shadow tables. FTS5's `secure-delete` option arrived in SQLite 3.42.0 and changes the file format when enabled. WAL mode keeps old content until a checkpoint. The Python standard library has no AEAD cipher, so crypto-shredding needs a separate dependency review. The recommendation was the **"forgettable payloads"** pattern: keep personal content in a separately deletable table. Plain hashes of short, low-entropy facts (such as city names) can be reversed, so use an HMAC with a destroyable key or omit them.

For tamper evidence a standard-library hash chain was judged sufficient, with the limit stated honestly: "anyone who can rewrite the SQLite file can also recompute a hash chain." Real tamper evidence against an attacker needs periodic anchoring outside (a phone, a message and so on). The conflict between chaining and forgetting is resolved by chaining only content-free event bodies and keeping payloads separately deletable. Regulatory cases (the EDPB's 2025 coordinated enforcement action, the UK ICO's "beyond use" standard) showed that deleting from backups and lacking a documented erasure procedure are recurring failures. **[Proposal]**

### 7.8. The shape of memory items and retrieval (partially complete research)

Research on memory shape, started the same day, stopped midway because of a usage limit, and the document records only what was confirmed. The problem was concrete: the model invented memory keys on the fly and keys sprawled (discount information stored under `profile.place.*`). Maintained systems avoid this with two layers: **small fixed slots** always in context, and a **collection of self-contained facts** retrieved on query. Classification lives in metadata, not in key strings.

Retrieval numbers were collected too. When memory is small, putting all of it in context was strongest (on LoCoMo, full context 72.9 versus Mem0 66.9). As memory grew, scores fell about 30 points (on LongMemEval with GPT-4o, oracle 0.870 versus full 0.606). Indexing raw text together with facts raised recall by 9.4%. Testing directly on the owner's Mac, FTS5 trigram returned zero results for two-character Korean queries (회사 "company", 초밥 "sushi"), while unicode61 with prefix queries found them. The synonym problem — finding "스시" from "초밥" — cannot be solved with FTS. The local Python's SQLite had extension loading disabled, so sqlite-vec could not be used directly, and brute-force search over 5,000 1024-dimensional vectors took 6.9 ms. The recommendation: keep slots as data (settings) and let the AI decide which slot a fact belongs in (C16); retrieve with FTS5 and a `search_memory` tool used by the AI; add vector search only after measuring. **[Proposal]·partially complete**

### 7.9. Correct, forget, delete completely (#794 phase 3)

Correcting and forgetting memory has to happen inside the conversation, without a management console. The owner-model phase 3 contract separates three operations precisely. **[Contract]**

- **Correct** ("you remembered that wrong…"): change the identified memory or current-state claim through its owning service, distinguishing "it was wrong" from "it is no longer true". Ask only when the target is genuinely ambiguous.
- **Forget**: exclude from current use immediately, leave a durable receipt, allow an exact 7-day undo, then purge when the window expires. Survives a restart.
- **Delete completely** ("erase it completely"): purge immediately with no undo window, reporting success, stale target, partial success and failure distinctly.

Undo is bound to the owner, the exact item and its version. It never restores another owner's item, revives a purged item or overwrites a later correction. The receipt **contains no content**. Forgetting locally is not claimed to erase provider copies, exports or backups. This change alters the meaning of owner state, so it falls under risk-based independent review. The Secretary program is currently paused at exactly this point, and the resume order is #794 phase 3 → [#660] owner-operated trial → [#875] Track A → [#661] family delegation.

---

## 8. Presence: an experience in which continuity shows

### 8.1. The relationship should continue when the model changes

The Presence research says the relationship with the same assistant should continue even when the model or worker changes. Memory must be checkable, correctable and withdrawable, and a failure or partial result should lead into the next interaction, not end the conversation. Extended to the industry, this suggests that a personal agent's value may lie not only in a particular model's output but in **continuity of work that survives across several intelligence providers**. **[Hypothesis]**

The README includes a real conversation excerpt that shows this. After switching the AI to Claude Code, the owner asks, "Did I end up adding the Spam yesterday, or taking it out?" The assistant picks up the earlier shopping conversation, asks the owner to sign in again, and answers that one 200 g can is in the cart. After switching to Codex, the owner says, "Take the Spam out," and in the same conversation it reports the removal and the 22 items remaining. These are edited real-use screens, not release-specific end-to-end validation. **[Observed]**

That requires separating "which model is configured" from "which model actually ran". This is a repository rule too: configured identity is never shown as observed, and if a provider reports no model name, nothing is recorded as "observed". Automatic switching must not become a device for justifying cost increases or wider data sharing the user doesn't know about. **Continuity does not hide differences in capability; it keeps those differences from costing the user their work in progress and their control.**

### 8.2. The problem was projection, not tone (2026-09-23)

The Presence research started from the owner's real use. Telegram felt like a task and status console. Failures told the owner to go to the web management screen instead of continuing the conversation. Follow-up requests were not handled as a continuation of the earlier failure. The configured AI and the actual execution path did not match. Settings demanded an understanding of routes, capability IDs and file paths.

The research diagnosed **projection**: kernel concepts such as Work, engine selection, connector state, tool results, Grants, task cards, retries and evidence were leaking straight into the owner's conversation and settings screens. So the goal was set:

> **The goal is not "a chatbot that speaks more like a human". It is to keep the mechanical, strict owner-controlled kernel exactly as it is, while letting its complexity recede behind one continuous, truthful personal-assistant relationship.**

Presence is defined as "the experience of one continuous, context-aware personal assistant across turns, tools, workers, failures, restarts and capability handoffs", while underneath Still keeps enforcing truthful evidence, explicit owner authority and inspectable technical state. The research wrote it as a formula. **[Contract]**

> **Presence = continuity + one voice + situational responsiveness + memory + availability + conversational recovery**
> Truthfulness and owner authority are non-negotiable constraints; proactivity is an optional amplifier, not a required term.

Pretending to be conscious or acting out emotion is not the goal. In VISION's words, "The goal is not anthropomorphic theater. The goal is useful continuity."

The research also graded the strength of its evidence honestly. Long-term relational-agent studies are strong but involved embodied, pre-LLM agents. The CHI 2016 study "Like having a really bad PA" shows the risk of a mismatch between capability and expectation. Another study finds short acknowledgements help but more is not better. The research itself wrote that "the core hypothesis is conditionally supported, but the causal claim itself is not fully proven experimentally." **[Proposal]**

### 8.3. Nine experience principles and ten anti-patterns

The Presence contract has nine experience principles. **[Contract]**

1. Conversation first, receipts on demand.
2. One assistant voice over many workers.
3. Continue before restarting.
4. Communicate meaningful progress, not scheduler state.
5. Truth before fluency.
6. Ask at the moment of need; the agent does the managing.
7. Memory is useful, scoped and correctable.
8. Proactivity must earn the interruption.
9. Model-first semantics; rules only where exactness is unavoidable.

It rejects ten anti-patterns: every natural-language message becoming a task card; received → running → completed → result boilerplate for short questions; the engine naming itself to the owner; "open the web console" as the default recovery from failure; making the owner choose connectors and folders in settings before they know why; showing raw capability IDs; conflating configured, connected and active; losing context after a failure or model change; smooth language masking evidence; persona and tone substituting for real continuity.

### 8.4. Conversation rules

Real use turned the principles into concrete rules. **[Contract]·[Implemented]**

- **Short work** ends with one useful answer, without received, running and completed chatter by default. **Long work** may get one concise acknowledgement when useful. The number of events must never decide the number of messages.
- **The goal decides the outcome, and the owner gets the answer** ([#752], 09-28). A Work's outcome is whether the request was met, not whether every intermediate step succeeded. A worker that retried a click or worked around a login has not failed. The exceptions are a pending owner approval, and a state-changing action that fell short without later succeeding.
- **The answer is the message** ([#847], 09-28). It fixed an observed case where a good answer was buried under three lines of machinery: a completion percentage, a raw host error and an "AI answer (not confirmed)" header. Now the answer comes first, closed by a short truthful note; the machinery stays in "details".
- Telegram cannot draw tables, so Markdown tables are sent as a bold header and rows joined with `·` ([#848]).

### 8.5. Small practices in Telegram

A fixed receipt reaction "meant nothing" ([#835]). So 👀 is the default reaction, replaced by an emoji the judgment AI chooses from the owner's message alone. The closing judgment does not use the answer body, because the answer may contain private source material.

The "thinking…" text felt stiff and the `typing…` indicator ended too early ([#858]). After five seconds a draft message with a stop button and cycling dots (`·`, `· ·`, `· · ·`) appears. The thinking text is never empty, because an empty draft becomes a blank bubble on iOS.

The owner suggested using the wait as an opportunity: "While showing that it's thinking, we should use the gap as an attention opportunity." ([#839]) While the dots run, one already-prepared item (an unread prepared answer, an accepted reminder due within 12 hours, a pending question) can be shown in one line, with no model call. Not during approval or payment, not repeated within six hours, never about the Work's own content.

None of these reactions, chat actions or drafts can fail, duplicate or change a Work if they fail. **[Implemented]**

### 8.6. Pages only for results (#880)

The web management contract of 2026-09-20 defined the local web as "the utility used to manage Still": a place to see work status, records and connection settings. Ten days later the owner reversed it: **"Designing settings as a management page was a misjudgment."** ([#880], 09-30)

> **Because the agent does the work, the owner manages Still by talking to it.**

Pages exist in only two cases. **[Contract]**

- **Result pages:** opened from a link in the conversation for results text cannot carry well — the owner model with its sources, a Work's decision tree and information-use audit, long tables or comparisons, Artifacts, calendars.
- **Necessary input surfaces:** inputs a conversation cannot carry *safely* — secret entry, OAuth consent, the OS folder picker, approval of payments and consequential effects, connecting the first AI.

No new management pages. Existing settings screens remain only until a separate census reclassifies each screen. A way to change settings in conversation was added too ([#814], [#855]). The owner's AI reads settings with `settings_read` and drafts a change with `settings_change`; nothing applies until the owner confirms with a button or plain language in the same conversation. Slash-command syntax is never shown. Credentials, keys, tokens, logins and endpoints are not handled in conversation; they stay on input surfaces.

The settings renewal showed the same attitude. A subscription route showing "currently in use" when its login had never been checked — looking verified when it was not — was caught as P1. Configured identity, verification and observed execution identity got different wording, and selection alone never produces "available". A component framework (Radix Primitives) was reviewed and not adopted: native HTML elements were enough, and no contract required a React integration. The main AI and the judgment AI were presented as two equal choices, not a hierarchy. **[Implemented]**

### 8.7. The stronger the Presence, the more important it is to be able to leave

The more a personal agent knows of the user's context and the deeper it goes into daily life, the heavier it becomes to leave. So Presence and Portable Memory are not opposing demands. One builds continuity in the relationship; the other keeps that continuity from turning into forced dependence on one provider. **[Hypothesis]**

> **A good personal agent should give reasons to use it for a long time, but must not make the user unable to leave because they used it for a long time.**

### 8.8. The limits of the evidence

The Presence acceptance scenarios A to J were turned into automated fixture tests in PRESENCE-EVAL-01 ([#512]). They used real service code and the real local approval handler, but Telegram, the model, the decision engine, Gmail, Calendar and the web were fakes. The document labels every result "automated synthetic fixture evidence". And it has one decisive line: **"The owner reported on 2026-09-25 that the six live Telegram checks for [#581] did not behave as the fixture expectations predict. The cause is unknown."** Product gaps were left as `expectedFailure` tests that become "unexpected successes" once the product meets the bar: an internal result UUID leaking into an answer, the verified part of a partial result not stated in conversation, a Work with an unknown effect recorded as `succeeded`, and others. **[Implemented]·inconsistent with real-use observation**

---

## 9. Collaboration: filling a cart together

### 9.1. Autonomy and collaboration are different axes

The whitepaper proposes this scenario. The user first puts a few books in a cart. The agent suggests other books based on interests and a budget the user has allowed. When the user removes one or changes an edition, the agent respects the change. Items not yet decided are held, and in the next conversation the same cart is picked up again. The purchase itself happens within a delegation given in advance or through a separate confirmation step. **[Proposal]**

The centre of this scenario is not whether the agent finishes everything alone. It is **whether the user and the agent can share the same object of work, understand each other's changes and pass the initiative back and forth**. So the whitepaper proposes treating autonomy (finishing without human intervention) and collaboration (continuing while reflecting the human's intent when they step in midway) as different evaluation axes.

The cart is an emblematic scene in this project. The Secretary program's first observation scenario (probe A) was "Put *When Do Leaders Make a Difference?* in the cart so I can buy it." The real-use excerpts in the README include a scene where the agent remembers a coffee taste mentioned earlier, helps pick beans, and confirms them in the signed-in cart with quantity 1. The README summarizes that scene: "What you say or show → context you allowed, with source and time → a useful question or tool → the boundary for action → an observed result. Adding to a cart and paying stay different actions."

The repository, however, treats such scenarios as **observation windows**, not completion criteria. At closeout the owner picks a fourth scenario never seen during development (held out), and if it fails for a reason that would have required scenario-specific code, completion is rejected. **[Contract]**

### 9.2. Collaboration needs shared work state

A natural conversation is not enough. Proposed items must be distinguishable from actually applied state. The user's latest change must not be overwritten by an earlier plan. Actions already executed must not repeat across interruption and resume. When the actual result is uncertain, check the state instead of speaking as if it succeeded.

So the unit of collaborative design must be larger than a single tool call. It covers **the object, current state, change history, actor, delegation scope and completion evidence** together. If the agent tries to increase a quantity based on an old cart while the user is removing that product, the old plan is not applied as is. Conflicts are checked with versions or change conditions, or, failing that, the state is observed again first. The user's deletion is not reverted without agreement; the plan is adjusted to the latest state or only the conflicts that need resolving are explained. **[Proposal]**

The repository's shopping skill (`shopping-cart`) writes this down as a site-independent method: quantity semantics that distinguish add, set and ensure; exact product matching; one change at a time; reading the cart back after a change; reconciling before retrying. Prices, stock, login state and cart contents are *current facts*, so they are not cacheable completion evidence. **Reuse the method; re-read the facts.** **[Implemented]**

### 9.3. Requests that look the same but are not

The role ontology research broke the cart request down into units of meaning. "Put this book in my usual bookstore's cart" and "Buy this book too next time I order" are not the same request. Folding both into one `add_to_cart()` loses meaning. Six things need checking for each request: product identity, preferred merchant, **purchase stage** (adding to a cart, ordering and paying differ even within one purchase), quantity and constraints, authority, and completion evidence. **[Proposal]**

The target shape of the answer is written down too: "I added one copy of the book you asked for to the cart at the bookstore you named. I did not order or pay." And it guards against delegation creep: a delegation to add to a cart never grows into a delegation to pay.

### 9.4. The gap of mid-work correction

The real test of collaboration is when the user steps in *midway*. On 2026-10-10 the Professional Roles and Communication Contract inspected the current code statically and found four gaps between the decision model and Presence. **[Contract]·diagnosis of current behaviour**

1. There is no judgment of "should this be reported now" for Work in progress. The only mid-Work surface is a 40-character tool status, so fixed tool-name lines fill the space.
2. An owner's mid-work correction (steer) reaches the running worker but not the orchestrator, so a re-delegation plan can be made without the correction (a stale plan).
3. The owner is never told how a correction was handled. A folded steer gets only a reaction, and one the worker never received silently runs later as a separate Work.
4. Relevance judgments carry no role or mandate, so "important" has a single (assistant) standard.

The contract separated a near-term track that closes these gaps on the existing structure from a long-term track that designs the full lens, mandate and ontology. "Neither requires finishing the other first." It also requires counterexample tests for stale plans, unauthorized effects and follow-up promises that cannot be kept.

### 9.5. Businesses can work with agents without building one

It does not follow that every company must run its own general-purpose agent. If what a company must provide is accurate product information, clear execution conditions, safe ways to change things and verifiable results, then offering those through a trustworthy interface is enough to collaborate with a user's agent. Commerce protocols already cover product discovery, carts, account linking, checkout and order management.[^ucp] Rather than claiming agent commerce is empty territory, the thing to verify is how consistently people and agents can collaborate on top of it. **[Hypothesis]**

> **When a user's agent uses our service, does it get the same state, authority and results as a person using it directly?**

The human interface need not disappear either. People must be able to browse, compare, change their minds and review results themselves. The goal is not to replace human screens with agents but to have both ways of working handle the same work consistently.

---

## 10. Authority, approval and evidence

### 10.1. Pilot posture: few protections, each one real

When the Secretary program started on 2026-09-26, the owner redefined how the request path is protected ([#653]). This is a pilot: one owner, using their own machine and their own accounts. In this phase two rules are enforced on every request. **[Contract]**

- **Secrets never enter a model prompt, a log or Evidence.**
- **Each payment needs the owner's approval.**

Per-request sensitivity judgments, re-asking about ordinary search terms, per-provider gates and the "no cart authority" rule were removed from the request path. Changing a cart or making a hold on a connected site no longer needs per-action approval; only payment does. These were deferred to a separately activated hardening program, and the end of the pilot does not start that program by itself. Folder grants, owner scope, secrets, per-action payment approval, no replay and memory authority did not change.

VISION puts it this way: **"Few protections, each one real."** Instead of adding guards, each Work shows which owner information it used and where it went.

### 10.2. The record of removing protection layers

This decision was not made in one step. A design to keep private values from leaking into search queries was built elaborately first and then removed piece by piece. **[Historical]**

| When | Step | Content |
|---|---|---|
| Around 09-26 | Elaborate blocking ([#605]) | A `lookup_term_sensitivity` judgment before every public lookup. Word allow-lists, Hangul jamo-level folding, digit-run matching, separator policy and per-Work caps to keep saved private values out of search queries |
| 09-26 | Sensitivity judgment removed ([#654]) | Under the pilot posture, the judgment and its "block when uncertain" path were removed |
| 09-28 | Native search gate removed ([#705]) | The gate that blocked a CLI's own web search once earlier conversation had touched a private store was removed on the owner's direction. It weakened a boundary, so it received independent review |
| 09-28 | The separation itself removed ([#826]) | Private reads and web search may share a turn. Instead, a **per-Work information-use audit** was introduced |

The owner's words on [#826] show the nature of the decision: "Giving it this information makes a huge difference. Instead, it's enough to provide an audit so I can check what information was used. I'll allow it."

The information-use audit shows, for each Work, which owner information was used (profile keys, memory rows, calendar entries, files, current context, conversation), which worker, route and model it went to, whether the judgment AI was called, which queries a web search used if it ran, and what came back. The removed blocking machinery was not thrown away; it was kept in an archive document "for future hardening and threat-model reference, not as current request-path policy". **[Implemented]·[Historical]**

This is not a risk-free decision. **An audit tells you what happened; it does not prevent it.** The proposal for injection and exfiltration defences against the so-called "lethal trifecta" — private data, internet access and autonomous action together — was parked by owner decision on 2026-09-28, and the Connection and Delegation Contract states that it "does not reopen that decision". The documents do not hide this choice and repeat it often.

### 10.3. Effects are different

Reading public information, changing a cart or hold, entering identity details, confirming a reservation, sending a message and paying are **different effects even when a website handles them in one flow**. Default research does not grant cart or payment authority. If parameters or authority change, an existing approval that no longer covers them becomes invalid. **[Contract]**

Payment protection does not rely on the model's judgment. Typing into a card number, CVC or OTP field always needs bound owner approval, whatever the model calls the action ([#698]). If a connection is marked "money-capable", every non-GET/HEAD call is raised to the payment class. **A label can only add a requirement, never remove one.** The model's own effect label and an MCP server's `readOnlyHint` are both untrusted hints. **[Implemented]**

### 10.4. "Done" is not proof that it was done

Suppose an agent said "done" and later the result is not there. What should be checked? The agent may not have sent the request. The service may have received it without applying it. It may have been applied and then changed again by the user or another system. What is needed is not an agent that remembers confidently but **evidence that separates request, processing, observation and later change**. The whitepaper divides the evidence like this. **[Proposal]**

| Kind of record | What it checks | What it cannot settle alone |
|---|---|---|
| Execution request record | Which actor submitted which request | Whether the request was actually applied |
| Observation record | What screen or response was seen at a given time | The service's full internal processing and change history |
| Service processing receipt | What the service confirmed it did with which request | Issuer error or falsehood; the current state afterwards |
| Ledger inclusion proof | Whether a record was included in a verifiable structure | Truth of the content; whether every event was recorded |

This table is not a ranking from weak to strong evidence; it **separates evidence that answers different questions**. Digital signatures do not remove these boundaries either. The W3C Verifiable Credentials model states that verifiability does not mean the claims inside are true.[^vc]

### 10.5. The cart case: what succeeded then, and what is true now

A design case that assumes a store provides processing receipts and change history. At 10:00 Still submits a request to add milk, and the store issues a receipt saying the cart moved from version 41 to 42. At 11:00 a request to remove the milk arrives from the mobile app on the same account, and the cart becomes version 43. At 13:00 the user asks why there is no milk. Still answers:

> "According to the service's records, the 10:00 request to add it was applied, and a removal submitted from this account's mobile app at 11:00 was applied. A check at 13:00 shows there is no milk."

This explanation is different from "I think I added it". It also does not assert "you deleted it yourself", because the records identify the account and the client path, not the person who actually acted. **That a past action succeeded is different from its result still holding now. An authenticated account, a client, a delegated agent and an actual person must not be treated as the same actor.** **[Proposal]**

### 10.6. Unrecorded changes, ledgers and blockchains

In collaboration, **missing records** matter as much as altered ones. If Still records only its own requests and not the user's app actions, it cannot fully explain how the cart changed. What is needed is not a structure that stores every agent's thoughts but **one in which every path that changes the same business object leaves a common processing and change record**. So the whitepaper proposes that the service that actually changes the state be responsible for linking the change to the issuing of a receipt, rather than having an agent write a diary after executing. The transactional outbox pattern helps, but it only reduces inconsistency between changing state and storing the record; whether the issuer can be trusted is a separate problem.[^outbox] HTTP Message Signatures (RFC 9421) do not define task-specific success conditions either.[^http-signatures] **[Proposal]**

A blockchain is an option for preserving evidence, not a device that creates facts.[^nist-blockchain] Writing to a ledger does not check whether the input is true. Checking execution history inside one organization and sharing records among several organizations without depending on one operator call for different choices. There are alternatives such as Certificate Transparency's Merkle-tree proofs (RFC 9162)[^ct] and the SCITT architecture (RFC 9943).[^scitt] SCITT itself states the possibility of an issuer's false claims or selective registration, and it is a foundation centred on software supply chains, not a standard defining completion conditions for agent work. A service's **processing receipt** and a transparency service's **registration receipt** can be linked but are not the same thing.

If one operator can rebuild both the originals and the whole hash chain and there is no external reference to compare against, the claim that a local hash chain alone can detect that operator's rewriting does not hold. It is no accident that the forgetting research in 7.7 reached the same conclusion.

### 10.7. Portable execution evidence

Along with Portable Memory, the whitepaper proposes **portable execution evidence**. When changing agents, users should be able to take, as far as needed, not only preferences and conversations but the processing evidence for work being handed over. The new agent should not accept the previous agent's summary as fact but be able to re-check it against preserved originals and issuer information. A summary that alters a signed original does not replace the original evidence. If the key or original can no longer be checked, mark it "not re-verifiable" and never use old evidence as current state or new execution authority. Moving evidence is not granting authority. Verifiability is not the same as publishing every record either. The goal is not to turn a person's life into a permanent public history but **to let a person present and check evidence for the facts that matter while controlling their own information**. **[Proposal]**

The industrial meaning is clear. Service companies can consider providing not only functions for agents to call but **verifiable results of how those calls were processed**. Personal agent businesses can add value by linking that evidence, separating uncertainty from omission, and explaining it so the user understands. In this structure, the AI's role is not to settle execution facts with its own narrative. Execution services and verification code handle record creation and verification; the AI explains what confirmed facts mean and what remains uncertain. There is no reason to replace cryptographic verification with a majority vote of other AIs or to add model calls to every task. **[Hypothesis]**

> **A trustworthy agent ecosystem is not one that assumes every participant is always right. It is one in which the remaining evidence can still be compared when participants are wrong, lose their memory or are replaced.**

### 10.8. The repository's truthfulness rules

Much of the whitepaper's proposal still needs cooperation from outside services. But what Still can do internally is already set as rules. **[Contract]·[Implemented]**

- Progress and action receipts are built only from observed Work, broker and tool events. **Requested, observed, failed and unknown** are kept distinct. No invented ETA, tool execution, model identity or successful external effect.
- The effect of a step result is one of `none`, `confirmed` or `unknown`. An external effect whose result is unknown always carries a sentence saying so, and the same effect is never silently re-run.
- Records the owner can inspect do not require hidden reasoning, raw system prompts, secrets or unrestricted payload logs. Redaction and retention are applied before persistence.
- A failure or partial result is never reported as success. Telegram no longer reports failed or partial Works as successful ([#476]) and no longer calls a refused save successful ([#488]). The product status page also states plainly that the same honesty has not yet fully reached the web view.
- The KakaoTalk sharing scenario (SCN-D-01) never claims "delivered" without an unambiguous receipt from the provider. Terminal states are limited to completed, cancelled, rejected, failed, not-sent and delivery-uncertain, and "no adapter may invent a more successful state." The document admits that keeping truthful can reduce apparent usefulness.

These rules actually caught a bug. In the first real-use sample of the skill evaluation (2026-10-03), the AI saw that the owner's store session was signed out, asked for sign-in without touching the cart, and honestly answered that nothing was added. Yet **the Work was recorded as `succeeded` although its goal was waiting for a sign-in.** That is exactly the kind of truthfulness gap the language of evidence classes exists to catch. The defect was not fixed inside the evaluation document but left as a separate follow-up. **[Observed]**

---

## 11. Connection and delegation

### 11.1. Who holds what

The owner subscribes to AI tools and switches them. The product must not be captive to any one provider, and no provider may become the source of truth for owner state. The Connection and Delegation Contract ([#1221], 10-09) divides the roles into five. **[Contract]**

| Part | What it is | Who holds it | Replaceable? |
|---|---|---|---|
| Brain | The AI the owner chose | The owner's subscription | Yes |
| Hands | Connectors, MCP, plugins, the browser | The AI side or Still | Yes |
| Methods | Skills (public first, then Still reference) | Versioned content | Yes |
| The owner's way | Which service, which option, what counts as news worth telling | Still owner state | No |
| Memory and records | Memory, judgment records, Work, Event, Evidence, trust records | Still | No |

The table comes with a test: **"If the owner drops one AI subscription tomorrow, the bottom two rows stay whole. A design that fails this test is rejected."**

### 11.2. The order for finding a capability

When a Work needs a capability it lacks, where should the AI look? If code decides that per task, it violates C16. The contract sets only **the order of places to look**. **[Contract]·[Implemented]**

1. AI ecosystem capabilities: connectors, MCP servers and plugins already present, or the official MCP Registry and vendor directories.
2. The service's own developer documentation and API, and maintained public implementations.
3. Skills (public skills, then Still reference skills): "how to do it", never authority.
4. Generic channels: a signed-in browser, API reads that use a secret slot, sandboxed code runs.
5. Building something new, only for a concrete unmet contract after a C15 review.

At first there was a rule: "move to the next step only when the current one has nothing usable". That rule prevented choosing a lighter route (a vendor's REST API) over a usable but heavy one (an MCP server that needs Docker). So it was amended ([#1380]). The choice now weighs the owner's install and registration burden, whether a route is official, standard and maintained, scope match (read-only when a read suffices), secret handling and reliability, and **list order only breaks ties**. Capability search (`capability_search`) finds metadata only; it installs, connects and authenticates nothing. Generating a skill from scratch inside a Work is not in this order yet; AI-written owner skills need their own decision and a C16 reading first.

### 11.3. Connection precedence and control levels

There is a precedence for which hand to use per service. An **AI-side connection** is the default. If the AI side offers no decision point for the needed effects, the owner wants full control, or the AI side is unavailable, an **Still-owned connection** is used. Next comes the **signed-in browser**. If there is nothing, say so, guide the owner to connect, and **never guess**. The owner's choice per service ("I'd like Still to hold this one") is stored and not asked again. Still never copies AI-side credentials. Secrets Still owns live in a local store encrypted with a macOS Keychain key. OAuth adopts `oauthlib`'s client-credentials flow as is (no new dependency or OAuth store). **[Contract]·[Implemented]**

How Still holds a hand falls into three control levels.

| Control level | Meaning | Applies to |
|---|---|---|
| **Mediate** | Still makes the call itself: secret injection, allow-list, payment guard, provenance, budget | Still-owned connections, the browser |
| **Decide per call** | The AI's CLI stops before each tool call and Still allows or refuses, seeing the tool name and actual input | AI-side connections that offer a decision point |
| **Delegate** | Still sets the mandate and judges the result, without stepping in at each step | Other agents, A2A, external services acting on their own |

The combinations of effect and control level are also set. Reads are audited everywhere. A non-payment change on a service the owner connected is "done, then told" under Mediate and Decide per call. On an AI-side connection with no decision point it is **not offered at all**, because "Still could neither stop nor reliably record it". Payment needs per-action approval wherever it is offered and is never delegated in the pilot.

The decision points were confirmed by observation ([#1296], 10-10). Claude Code could answer every AI-side call not pre-approved through `--permission-prompt-tool`. Codex's session-flag `PreToolUse` hook received the tool name and input, but without `--dangerously-bypass-hook-trust` the hook was skipped and the call simply ran, so Still passes that flag when it loads owner servers. And it **fails closed after the fact too**: if a call is found to have completed without Still's allow decision, the owner's servers are switched off for every engine until they are confirmed again. **[Observed]·[Implemented]**

### 11.4. Delegation, the trust ladder and the owner's way

**The mandate.** It sets who (delegator and delegate), purpose and completion condition, scope (data, hands, effect classes), exclusions, budget and validity, what to report, and when to come back to the owner. The owner does not fill in a form, though: **a clear one-off request is its own mandate.** Nested authority is a subset: a delegate never exceeds the delegating Work's authority, and two delegates' scopes do not add up. When asking for a second opinion, give **context, not conclusions**: goal, facts and criteria, but not the delegator's preferred answer. A brief that carries a conclusion must say so, and its result is not reported as independent. Revoking a mandate stops new calls but does not undo effects that already happened. **[Contract]**

**The trust ladder.** One ladder applies to every third-party hand, skill and agent: rung 1 Report, rung 2 Propose, rung 3 Act after per-action approval, rung 4 Act within a standing mandate. The owner chooses the starting rung at introduction (rung 1 by default for money-capable domains, rung 3 otherwise). **Raising a rung is always the owner's decision, never automatic.** Lowering after a failure or correction may be automatic. In the pilot, payment never reaches rung 4. Popularity, install counts and ratings never move a rung. **[Contract]·[Implemented]**

**The owner's way.** The contract says, "Personalisation is not a goal here." The goal is *not overriding* a known owner choice: which service, which option, time margins, alert thresholds, which hand per service. The precedence is **the owner's way > a loaded skill's default > Still's default**. No provider, skill or package default silently replaces a known owner choice. A skill declares the choice slots it honours (for example a directions skill's "preferred map service"). If the owner's preference is outside those slots, the AI looks for another hand that can honour it, or states exactly what it could not honour. The owner's way is learned from the owner's statements, corrections and Still's own records. Wholesale observation of the owner outside Still is not a learning source. **[Contract]**

**Goal first, then alternatives.** The decision model interprets the desired outcome and constraints before choosing tools, including useful routes the owner did not name ([#1301]). But "novelty is not a requirement, and an alternative never overrides a required method, identity, refusal or later correction." And **a receipt saying setup finished is not completion.** On 11 October the owner made this more concrete ([#1375]): the chosen AI must actually find and prepare a usable connection, bind the owner's input, and return **the substantive result of the original request** in the same Work.

### 11.5. Truthful data and cost

Everything read from outside keeps its source, as-of time and raw values, along with two independent states: **freshness** (fresh, stale, unknown) and **completeness** (complete, partial, inconsistent). "A report never upgrades a state." These semantics entered generic evidence for API reads, and core code has no domain-specific types. **[Contract]·[Implemented]**

Cost has principles too. **"Explore once, repeat cheaply."** Once a capable model works out a method, later runs reuse it (skill plus the owner's way) on the cheapest adequate model — but a situation-dependent reply never drops to the lowest-cost model ([#1008]). A watch costs nothing when nothing changed: the same result stays quiet with no model call ([#719]). And **repeating a method is not replaying an effect.** Every effect is authorized again, and there is never a replay.

### 11.6. Carrying on: continuity acceptance criteria CC1 to CC8

When a request runs into a missing capability, authentication, input or approval midway, "setup succeeded" must not be reported as the result. CONNECT-01 ([#1295]) set eight acceptance criteria. **[Contract]·[Implemented]**

| Criterion | Content |
|---|---|
| CC1 Bound continuation | Resume automatically without a second owner request. Do not invent a pending task for a setup-only request |
| CC2 Actual readiness | Keep discovery, configuration, installation, authentication, schema, scope and action authority distinct. Non-payment decisions are not re-asked; payment always is |
| CC3 Fresh execution context | Rebuild the worker catalogue and brief from current grants and revisions |
| CC4 Multiple waits | Bind sequential or unrelated requests to independent Works |
| CC5 Effects | Continue only the remaining obligations. No universal exactly-once claim |
| CC6 Durable settlement | No duplicate execution from races or restarts |
| CC7 Cumulative bounds | Continuation, retry and cost limits belong to the request |
| CC8 Outcome evidence | A substantive result, the information-use audit, freshness and completeness. Configuration or connection evidence alone is not task completion |

The evidence is model-free testing. Scripted models and synthetic services go through real stores, callbacks, bridge processes, queues and workers, but they do not prove the real model's discovery quality or live-account readiness. The per-route support table is honest too: adding a new server through Codex is "unsupported", and an Still-native MCP client was not introduced. "A new protocol client is not an implicit fallback."

### 11.7. Sandboxed code runs for APIs without a standard (code_run)

"Show my account list and balances at service X" must work even when X has only a non-standard API. An experienced developer reads the manual and writes code; the AI should be able to do the same. And **a new non-standard API should need no change to Still.** Adding secret shapes one at a time was "the treadmill the owner rejected". Real APIs exchange tokens in JSON bodies, use several secret headers, put secrets in queries or bodies, or sign requests. **[Contract]**

The design ([#1380]) is a good example of a reuse review. Internally it reused effect identity, dispatch claims, single-use payment approval, Keychain encryption, bounded execution, secret redaction and the information-use audit. Platform features (macOS Seatbelt, Linux bubblewrap) were too low-level. The decision was to **Adapt** Anthropic's `@anthropic-ai/sandbox-runtime` (Apache-2.0, research preview). It provided OS isolation, a proxy that lets only allowed domains through, and secret masking in which the real value is substituted only on the way out to declared hosts. A compatibility test on the owner's Mac confirmed masking, header and body substitution, refusal of home-directory reads and refusal of undeclared hosts. A hand-built Seatbelt and proxy, Docker, and "one more fixed API shape" were rejected. An observed limit was recorded too: masked secrets were substituted in headers and bodies but not in the URL path or query (10-12).

The egress policy in the pilot is mostly reads. GET and HEAD to declared hosts are allowed and logged. Owner-reviewed non-GET requests used for reads are allowed too. Any other non-GET to a secret set's host needs owner approval of exactly that request. Everything else is refused. On 13 October the owner decided "Let's go with A" ([#1456]), allowing an order, transfer or state change to be sent once with approval. This too builds no new mechanism; it reuses the existing API request machinery's effect identity and replay prevention. An interrupted run leaves the effect "unknown" and blocks further state changes until it is resolved. **A watch cannot sell unattended.** **[Contract]·[Implemented]**

### 11.8. Open connections are not user independence

Existing connection technology should not be underestimated. MCP provides an exchange structure between AI applications and external tools and data, and does not decide how an application uses models or manages context.[^mcp] A2A standardizes communication and collaboration between agents on different systems.[^a2a] This project's question is not to replace them. The question is **who chooses the elements that can now be connected, who manages personal context and authority among them, and how the user can change the whole environment**.

Being able to connect to a tool does not make the result trustworthy. Agents being able to talk to each other does not guarantee they understand each other's authority. Being able to import memory does not mean work in progress can continue safely. So **actually testing a switch** matters more than declaring an open format. When changing the model, the memory store, the personal agent product itself, or when stopping use of a particular skill or service, check what remains and what is lost. **[Hypothesis]**

> **The product meaning of interoperability lies not only in a longer list of connections but in the user being able to form, adjust and end relationships.**

---

## 12. Skills and packages

### 12.1. The skill economy: from instructions to trustworthy capability

The public Agent Skills format bundles a `SKILL.md` of instructions with optional scripts, references and templates as a unit for distributing expertise and procedures.[^skills] But the whitepaper **distinguishes a distributable format from a tradable capability**. Providing a file that describes how to do a job, maintaining it so it works in a given environment, verifying the quality of results and supporting failures are different products. A "receipt organizing skill" name alone does not tell a buyer what they get: which inputs it handles, which tools it needs, which models and environments it was tested on, how it checks for duplicates and omissions, and who maintains it when a service changes. **[Hypothesis]**

A skill market can also create new lock-in. Even if the file format is open, competition is limited if the required runtime, reputation, payment and access to user context are tied to one business. Execution evidence can be part of the grounds for confirming capability, but issuing evidence is not a quality guarantee or proof of user satisfaction. Not every skill needs to be a dynamically reasoning agent either; software running a stable procedure, fixed validation rules or a human-reviewed professional service may fit better.

> **The purpose of a skill economy is not to rename every service an agent but to make professional capability discoverable, delegable and verifiable.**

### 12.2. Supply first, adapt second, learn only the gaps

On 2026-10-01 the owner said the AI should not have to rediscover a site's mechanics by trial and error every time it repeats a job (such as a particular hypermarket's cart). "Someone may already have built an Emart skill, or Emart may publish one officially. There's no reason for each user to repeat the same trial and error." The principle of the skill supply program ([#959]) was **supply first, adapt second, learn only the gaps**. **[Contract]**

Its requirements R1 to R14 compress this project's way of thinking. Supply comes before per-user learning. Discovery is bounded and its results are reused (no searching again every turn). Importing a skill, connecting a tool and delegating a task are different integration modes. The lifecycle is not authority (`allowed-tools` grants nothing). Reuse methods, re-read facts. Reconcile before replaying. Knowledge is not authority (sharing a public skill with family is not sharing accounts or history). A single successful trace is not automatically promoted to a trusted skill. Additions preserve existing behaviour.

The owner added one more thing: "Preserve the cart and other behaviour that worked before, and don't compensate for a big redesign with token-hungry tests." So the program was reshaped around interface-first design and bounded verification, not a rebuild.

### 12.3. The interface decision (#960)

The decision record defined a skill like this: **a skill is optional, versioned knowledge carried by an existing AgentPackage, not a new executor or permission.** The worker reaches the content through two effect-free host actions (`skill_load`, `skill_resource`). They are defined in one place, so the direct API, Codex and Claude Code routes all get them identically. Foreign formats are normalized once on acquisition (an anti-corruption layer). Rollout uses **Branch by Abstraction**: with skills off, the tool surface is byte-identical to before skills existed. Per-site strategy classes, site inheritance trees, mandatory specialist chains, translator agents and skill-specific verifiers were not adopted. **[Contract]·[Implemented]**

The reuse review table was kept with exact revisions.

| Candidate | Decision | Reason |
|---|---|---|
| Agent Skills specification | **Adopt** | The format and its progressive-disclosure model. Still execution metadata stays outside it |
| `skills-ref` | Rejected as a runtime dependency | Describes itself as "alpha, not for production". Used only as a conformance reference in tests |
| Microsoft Agent Framework `SkillsProvider` | **Adapt** the semantics only | Follow its path-containment and symlink rules, but its own agent loop and approval layer duplicate Still authority |
| PyYAML | **Adopt** | Used only through a `SafeLoader` subclass that rejects duplicate keys. The feature's only new dependency |
| `python-frontmatter` | Rejected | Adds no needed contract and silently accepts duplicate keys |
| skills.sh CLI, Hermes Skills Hub | **Adapt** conventions only | Source-spec shape, archive caps, quarantine and trust tiers. Not the CLI itself, and its hashes and regex scans are not proof of safety |

Budgets were also placed inside the existing Work budget: at most 32 descriptors and 8 KB in the catalogue, three skill loads and eight resource reads per Work, and so on. The first external supplier was one public repository pinned to a commit, accepting only folders whose licence file is Apache-2.0. Skills without a licence file were marked `licence_unknown`, and source-available but not open-source skills were excluded.

The risk of an AI CLI quietly showing the worker unreviewed skills from the owner's home was found and closed. Codex is passed a setting that turns off skill-instruction inclusion, and Claude Code is passed a flag that disables slash commands. This was confirmed with a real `codex`, a fake model and a canary skill. **[Implemented]**

### 12.4. The first real-use sample and the honest scope of claims

The skill evaluation ([#964]) begins by saying it "is not a second test project". It maps existing evidence to the inventory, adds only the checks that were really missing, and states what remains unproven. The owner approved the live sample plan: four cases in sequence with the real Codex and a signed-in browser, changing only one product line, never paying, with caps on Works, turns, tool attempts and time, stopping at the first false completion or unrequested effect, and restoring the original state at the end.

The first run on 3 October stopped at the first case. The session was signed out, and the AI asked for sign-in at once without touching the cart. And, as noted earlier, a truthfulness bug appeared: the Work was recorded as `succeeded`. The answer inferring an account owner not present in the request was recorded as well.

So what can and cannot be claimed is clear. Fixtures, one real bridge process and one real public skill install confirmed supply, pinning, revocation, route parity and preservation of the switched-off path. Cart success rates on real sites, how well the model follows the method, conversation quality and token or time savings are **not claimed**. The supply premise ("someone has already built an Emart skill") was not confirmed either. Three public shopping skill candidates were rejected because they needed Playwright scripts, injected JavaScript for one overseas supermarket, or their own MCP servers. So the evidence for importing an external skill and the evidence for site-knowledge content remain two separate pieces and were not merged into one stronger claim. **[Observed]·[Implemented]**

### 12.5. AgentPackages and the distribution platform

In the long run the owner will install and replace better agents like apps. The lifecycle goes: discover → inspect and download → verify → stage → health check → install disabled → separately grant, enable and connect → run under Work-scoped Grants → update or roll back → disable or quarantine → uninstall. An update that widens authority, data destinations, memory or background behaviour, dependencies, budgets or consequential actions needs new authority (C10). A failed update defines a path back to a known good state. Uninstalling removes the package's authority but does not delete the owner's Artifacts or retained evidence. A package's internal memory is not canonical. **[Contract]**

Capability acquisition has a ladder from L1 to L5: manual install → recommendation → owner-approved plan → bounded standing acquisition → kernel-mediated self-request. L4 and L5 are outside the first MVP. "An agent completing installation, activation or privilege escalation on its own is not the default behaviour." A recommendation is information only and never changes capability state. A model's suggestion, a catalogue entry or a bare "yes" is not an install or an approval.

### 12.6. Relationships with upstream, and dependency updates

As reuse grows, relationships with outside projects must be recorded precisely. The upstream relationship map ([#1023]) separates attack surface (code installed) from actual authority (what is actually projected at runtime): "neither implies the other". Relationships are classified with six terms: reference-only, vendored/patched, runtime package, protocol or schema conformance, versioned content and external service. For example, model providers are reached by implementing their wire contracts directly without an SDK, and the document names this "an explicit uninspected gap". Ontology and provenance standards (OWL 2, SKOS, SHACL, PROV-O) are strictly reference-only; there is no RDF or reasoner runtime. **[Contract]**

Dependency management adopted a single `uv` lock and Dependabot. Renovate could have tracked commit pins of skill content but was rejected, because "changing a skill commit cannot safely refresh copied bytes, licences, digests, local wrappers and evidence of semantic impact". A bot pull request is "a discovery candidate, not implementation authority". The official MCP SDK was **Adapted** only on the host's stdio path, as an optional extra. The decision states that the 13 transitive packages the SDK brings in increase the installed attack surface, and each licence was checked.

Upstream contribution was careful too. A defect in one release of a public-suffix-list package — its wheel and sdist bundled different list snapshots — was reproduced, and the dependency was pinned to a known good version. A draft upstream issue was prepared, but submission was left to a separate owner approval. A response-handling problem in the MCP SDK overlapped an already open upstream issue, so nothing new was submitted to avoid duplicating work; instead a local adapter was kept with tests, to be removed once an upstream fix is released. **[Implemented]**

---

## 13. Roles and professional work

### 13.1. The secretary is one role among many

The contract of 2026-10-10 widened the product thesis. Still is an operating environment for many professional roles for one person. Different professional perspectives — asset management, research, purchasing, project management — interpret the same owner reality. The role ontology research put it in one line: **"One owner, consistent facts, multiple professional interpretations, explicit delegation, verifiable execution."** **[Contract]·[Proposal]**

That research set an unusual success criterion: "The goal is not to add new names or layers. If existing ones suffice, using them is this document's success." And it repeats that publication is not implementation approval or a declaration of features.

### 13.2. The same fact, interpreted differently by role

The single fact of a cash balance in an account is read by the secretary as readiness to spend, by asset management as part of liquidity, and by a discretionary investment role as funds it may deploy within its mandate. When basis, unit, time and scope differ, these are not contradictions. When all of them match and the values still differ, it is recorded as **a source conflict** and never silently overwritten by a role's preference.

> **Consistent facts do not mean every role must judge the same way. They mean being able to explain, from the same evidence, why the judgments differ.**

### 13.3. Separating role, mandate, authority and the decision model

The heart of the research is not mixing four concepts. A **role** is a professional perspective. A **mandate** is the goal, scope and constraints the owner actually entrusted. **Authority** is the boundary of actions permitted right now. The **decision model** is the boundary for judging alternatives, not a tool classifier. "A model inferring a mandate or choosing a role name is different from the owner's will, target and conditions being confirmed and reflected in what is actually permitted."

Concepts were borrowed from outside research: MOISE's distinction between adopting a role and committing to a mission, ODRL's distinction between an offer and an agreement, and OntoUML's distinction between a relational role and identity. No platform was installed; only the concepts were taken. Policy engines such as Cedar served only as points of comparison, and the current Grant and approval system remains the source of authority. The executable scope was written as an illustration:

> executable scope = candidate ∩ current mandate and request scope ∩ applicable policy ∩ currently valid authority ∩ actual tool capability ∩ execution preconditions

And **an unresolved required value is never treated as "allowed".**

For the same holding of 1,000 shares, each role asks different questions. A discretionary investment role must first check whether the holding is within its mandate at all before judging anything. Observations of execution differ by stage too: reviewed options → decided to adjust → attempted to submit an order → the external system accepted → partially or fully filled → confirmed the changed state. None of them stands in for a later one. If the goal was to adjust 100 units and only 40 were confirmed, the report is not "complete" but splits what was confirmed from what was not. **[Proposal]**

> **Activating a role is not widening authority.** Even if an order-submission tool and an investment-management role pack are both installed, that must never be inferred as a trading mandate. "What do you think?" is never "go ahead and execute".

### 13.4. A professional lens is not a persona

The Professional Roles and Communication Contract defines a professional lens by seven elements: domain semantics, professional practice, mandate, authority, evidence standard, communication protocol and commitment lifecycle. Each maps onto an existing mechanism: skill content, Work scope and brief, Grants, the `goal_reached` judgment, the decision model's relevance judgments, and preparation and watch scheduling. **"A lens needs no new store, router or kernel branch."** A role pack is not a new package format but content delivered through the existing skill mechanism. **[Contract]**

Presence is not a role. The layers stack like this: core world concepts → domain lens → professional role lens → owner context → mandate → authority (enforced independently) → decision model → execution → evidence, outcome and commitment. Ontology (what is understood), the decision model (what to do) and Presence (how the relationship is kept) are the three dimensions; a professional lens is not a fourth layer but gives professional context to all three. The **communication grammar** common to every role is part of Presence; a role-specific **communication protocol** is part of the lens. The same market drop is read by the assistant as "notable news", by a wealth manager as "a risk to asset goals", and by a discretionary investment role as "an event bearing on mandate constraints".

**Communication never creates authority.** "I'm leaving this to you" is input to a mandate, never a Grant or a regulated status. Understanding the discretionary role does not mean holding a legal or contractual discretionary mandate.

### 13.5. Warmth must not cost accuracy

The contract also gathers evidence-based guidance on how to talk. Respond to what was said before moving on. Meet good news actively. For small delays, thank rather than over-apologize. And the most important one: **warmth must not cost accuracy.** The evidence is a study in which models tuned for warmth were about 40% more likely to affirm a user's mistaken belief, worst when the user sounded sad.[^warmth] In Korean, speak in the soft 해요체 register rather than the stiff 합니다체, without system vocabulary (Work, tool, route) and without titles.

### 13.6. The research method is itself a case

The role ontology document was rewritten ten times through editorial, fact-checking, ontology, architecture and domain review roles (F01–F27), then went through ten more rounds to close a gap named "missing substantive prior-art and reuse comparison" (F28) (F29–F48). After that, a real external GitHub review bot pointed out that the final editor and final reviewer criteria were hard-coded to this manuscript's thesis and would wrongly bind future manuscripts (F49). Twenty internal reviews had missed it. Each time the document stated that this was "sequential, role-separated review by one model, not independent experts or multiple models", and after the gap was found it did not automatically restore the earlier approval. It applied to its own research process the evidence-class discipline it demands of the product.

---

# Part 4. How it is built

This part is about how the product is built, not the product itself. The repository's issue, branch and pull-request flow, the Goal Execution Contract, CI and the delegation automation are **development infrastructure used to build Still**. They are not end-user features, a runtime dependency of the OS, or evidence of live autonomous operation. AGENTS.md repeats the distinction: a coding harness may be a service or a development aid, but it is not the product's identity.

## 14. The Development Constitution and spec-driven development

### 14.1. The sequence

Every material change goes through eight stages. **[Contract]**

> Constitution → Spec → Authority/Threat Model → Plan → Tasks → Implement → Verify → Converge

- **Constitution:** check that durable owner-authority, safety, evidence and portability invariants are not violated.
- **Spec:** after checking whether an existing contract, standard or concept already covers the behaviour (C15), state the owner outcome, behaviour, boundaries, non-goals and acceptance criteria, without choosing an implementation yet.
- **Authority/Threat Model:** state which data, Grants, runtimes, packages, networks, secrets and external effects are involved, and find abuse and failure paths.
- **Plan:** run the Existing Solutions Review and choose architecture, compatibility, migration strategy and evidence plan.
- **Tasks:** create bounded, issue-linked work units with dependencies and stopping rules.
- **Implement:** change only the explicitly activated goal. Under C16, remove blockers or improve orchestration; never script a specific request.
- **Verify:** map every acceptance criterion to current evidence, including negative tests where authority or security is involved.
- **Converge:** before merge and closeout, reconcile contradictions, stale documents, open findings and GitHub state.

The sequence borrows the Constitution/Specify/Plan/Tasks/Implement/Converge discipline of GitHub Spec Kit, the standards and context-injection ideas of BuilderMethods Agent OS, and the role, state, authority and receipt patterns of Open Still-style governance. **Only the patterns** were borrowed. These frameworks are not runtime dependencies of Still, and the repository using their patterns gives them no authority over kernel state (C11). One sentence is attached: **"Issue creation, planning documents or passing local tests alone do not activate or complete a goal."**

### 14.2. Sixteen principles

| Principle | Gist |
|---|---|
| C1 One owner, one durable personal state | Canonical state belongs to the owner-controlled Still domain. No model, package, runtime, registry or development framework silently becomes the source of truth |
| C2 The kernel is agent-independent | The product experience may present agents, but kernel semantics are the ten concepts |
| C3 Install is not authorization | `downloaded != installed != enabled != connected != authorized-for-action` |
| C4 Minimum authority, explicit external boundaries | Read authority never implies write, delete, send, privilege or payment |
| C5 Context is not automatic memory | Third-party durable memory writes are candidates. The owner's own AI is the exception (#918 a) |
| C6 Evidence before capability claims | Designs, issues, mocks, fixtures, signatures, static scans, local tests, CI and live observations are different evidence classes |
| C7 Exact revision and provenance | A mutable name such as `latest` cannot be completion evidence |
| C8 Recoverable, idempotent Work | Restart or retry never duplicates a consequential external effect. A failure is never rewritten as completion |
| C9 Trust is multidimensional | "Verified" must say what was verified. Popularity never grants permission |
| C10 Safe update, rollback, disable and removal | An update that widens authority needs new authority |
| C11 Framework and runtime replaceability | No framework gains authority over kernel state because it is used |
| C12 Historical authority is immutable evidence | A closed contract is evidence of its own scope. Old scopes are not widened to make a new feature look previously implemented |
| C13 No automatic successor selection | Completing a substep or program does not authorize the next feature |
| C14 Human gates remain real gates | Identity verification, MFA and CAPTCHA, agreement acceptance, payment and sensitive external approvals are never simulated or bypassed for automation |
| C15 Reuse first | Adopt → Adapt → Build |
| C16 AI is the engine | Still orchestrates and does not implement requests |

Changing the constitution needs a dedicated issue, an impact analysis, independent review where it applies and required CI; if it widens durable authority or weakens a safety invariant, explicit owner approval before merge. C16, its amendment (#974) and the C5 exception (#918) all went through this. On 10 October a review rule was added: changes to ontology, judgment or Presence are reviewed against the Professional Roles and Communication Contract. It refines review criteria; it is not a new source of authority.

---

## 15. Reuse first: Adopt → Adapt → Build (C15)

### 15.1. Why the principle was needed

Coding agents and people alike lean toward writing new infrastructure rather than finding what exists, because writing is easier. The result was a pile-up of unnecessary custom protocol, parser and transport code. The constitution faces this directly: **unnecessary bespoke infrastructure is not a Still differentiator and must not be implemented by default.**

Before building or replacing a component, abstraction, integration, dependency, framework, protocol, SDK, OAuth flow, connector transport, parser, scheduler, storage or migration utility, schema validator or model-provider compatibility layer, run an **Existing Solutions Review**. Start inside the repository, then look at the standard library and platform, relevant standards and official SDKs or reference implementations, and mature, maintained open source. The decision order is: **[Contract]**

1. **Adopt:** use an existing Still component, standard library facility, official SDK or established implementation as is when it fits.
2. **Adapt:** put a mature external implementation behind a narrow Still adapter when direct adoption would tie external semantics to kernel policy.
3. **Build:** only when a concrete unmet requirement is documented after searching inside and out.

"Fewer dependencies", "simpler to write ourselves", "familiarity" and "the coding agent prefers it" are not reasons to Build. Equally, reuse first is not permission to add dependencies lightly. Every new dependency needs licence, provenance, security, maintenance and compatibility review and must not silently widen runtime authority, egress, secrets or ownership of durable state. External libraries stay replaceable details behind these boundaries.

### 15.2. Review at proposal time too (#567)

At first the review was required before implementation ([#538], 09-24). The next day there was a failure. An assistant proposed writing a new "turn loop contract" without checking existing material, and most of it already existed: MP1 D-06's bounded outer loop, the architecture's Work-scoped ContextSnapshot, and the Codex and Claude CLIs' support for system prompts, model selection and event streams. The real gap was not a missing specification but a reuse failure: the subscription adapter had never adopted those existing contracts. In the issue's words, "a proposal made before any code or PR exists escapes the review."

So [#567] extended the review to **proposal time**. It covers recommendations in conversation, design documents, contracts, issues and plans. A proposal made without the review is incomplete and must not be recommended as the next step. For a behaviour-only specification, the review asks whether an existing contract or standard already covers the behaviour; choosing an implementation is still a plan-stage decision. **[Contract]**

### 15.3. Decisions in practice

Gathering the Adopt, Adapt and Build decisions scattered across this document shows how the principle was actually used.

| Problem | Decision | Grounds |
|---|---|---|
| Referencing an external team's operating principles (b3os) | Adapt (principles only) | No code, assets or templates; not presented as a partnership |
| OAuth client credentials | Adopt `oauthlib` | No new OAuth store |
| Dependency locking | Adopt `uv` | One hashed lock across platforms, versions and extras |
| Update bot | Adopt Dependabot, reject Renovate | Semantic compatibility of skill content cannot be judged automatically |
| MCP stdio | Adapt the official SDK on the host only | States the attack surface added by 13 transitive packages |
| Running non-standard APIs | Adapt sandbox-runtime | Rejected a hand-built Seatbelt and proxy, and Docker |
| Skill format | Adopt Agent Skills, Adopt PyYAML | Rejected another runtime's agent loop and approval layer as duplicating authority |
| Temporal graph | Graphiti not adopted | This scope needs neither graph ingestion nor a second authority path |
| UI components | Radix not adopted | Native HTML elements were enough |
| Owner model and decision journal | Build (own SQLite schema) | All nine candidates required storage that moves canonical state outside |
| Kubernetes and Docker deployment config | Removed afterwards ([#1198]) | Not the real install path, which is Homebrew and uv. "Reuse review: N/A (pure removal)" |

The last row matters. Reuse first was applied not only to new proposals but to removing, a month later, infrastructure that had been there from the start and was never actually used. Stable custom code, however, is not rewritten on impulse just because an open-source alternative exists; replacement needs its own issue with migration, regression, security and evidence criteria. Replacing the transport library ([#420]) was closed as not planned because of a structural blocker: a 1,426-line `AgentService` with no seam between transport and policy. "The blocker is structural, not sequential."

---

## 16. The philosophy of verification

### 16.1. Evidence classes: the principle most often broken and re-established

The most load-bearing discipline in this repository is separating a passing mock from real capability. Contract and static evidence, mock and fixture evidence, local temporary integration evidence, repository CI evidence, installed-smoke evidence, owner-operated local evidence and live external or account evidence are not interchangeable. **A lower class never silently proves a higher one.** AGENTS.md says: "Never claim a Codex, Claude, Ruflo, MCP, Registry, Marketplace, package or connector path works merely because its manifest, adapter, mock or fixture exists." **[Contract]**

The principle was not established once but re-established repeatedly. The record is candid.

- MP1's first "development complete" had only unconnected mock adapters and needed a remediation sequence (4.4).
- OP-01's operating-readiness claim was false and OP-02 corrected it.
- The PA1 program closed as "development complete" without installed or real-use evidence for any journey, and its closeout record says so.
- The Presence fixture tests passed, but six of the owner's real Telegram checks behaved differently ([#581]).
- CONNECT-01 closed as complete on 11 October, and **the same day** real use exposed evidence and outcome truthfulness gaps the fixtures had missed, opening a corrective issue ([#1375]). In its words: "development-complete fixture evidence is not proof of this live owner experience."
- The first real-use sample of the skill evaluation found a truthfulness bug at once (10.8).

These cases are a list of failures, and also a record that separating evidence classes made it possible to know exactly *what was not yet proven*.

### 16.2. Unit tests first (#612)

The original verification policy ([#602]) required red/green/mutation/trace bundles per child task, three live-model trials for every case and route, and an unsupported universal 90% threshold. On 25 September the owner raised token cost and reinvention, and [#612] replaced it. **[Contract]**

- Normal development uses **focused unit tests** with injected model and tool transports, fake clocks and temporary stores. No live-model requests by default.
- When a serializer, broker, process or resume boundary changes, add a small model-free integration test. Do not mock that boundary away.
- Mutation tests and counterexamples are reserved for load-bearing authority, idempotency and reachability checks.
- Live-model checks are **opt-in, change-triggered and explicitly budgeted**. Only when a model, prompt or tool description changed, there is an unexplained model-dependent defect or a scoped quality claim is made, pick the affected cases. More trials need evidence of uncertainty, not an automatic quota.
- On success, report a short summary; on failure, look only at the relevant diagnostics.

One sentence in the issue sums up the policy: **"Missing live authority does not hold development closure hostage."** It only prevents claiming live behaviour that was not observed.

The principles for measuring usefulness are the same. The 24-case evaluation seed and its static tests are specification evidence, not performance results. Build the evaluator first and freeze the rubric first. "Labelling everything unknown cannot pass a task that requires supported facts." Report first-attempt and repeat consistency, not the best of three. Do not quietly change the rubric to manufacture success.

### 16.3. Independent review is an escalation, not a default gate (#560)

Independent review had become the default gate for nearly every closeout, producing expensive loops of implementation → CI → review → fix → CI → re-review. There were records of review fixes introducing new defects. [#560] (09-24) turned independent review into a risk-based escalation. It is required only when a change alters filesystem, network, secret, connector or runtime authority; OAuth and credential boundaries; approval semantics for consequential actions; sandbox and isolation; private-data egress; package and supply-chain trust; ownership of canonical owner state; or weakens a declared safety invariant. Final closeout, ordinary UI, conversation or settings work, bug fixes unrelated to authority, refactors, documentation and tracker cleanup do not require review for that reason alone. Required CI, exact-head validation, mapping acceptance criteria to evidence and truthful evidence are always required. **[Contract]**

One important distinction was added: **"A second model is still self-review."** Different models, prompts or sessions controlled by one author do not make that author's PR review independent. The original whitepaper, the role ontology research and this compendium are all labelled self-review under this rule. **[Contract]**

### 16.4. Five days of lightening the process

Between 20 and 25 September four corrections landed one after another, each addressing a different face of the same problem. **[Historical]**

| Issue | What it fixed |
|---|---|
| [#408] (09-20) | Loops of small fix → full test suite → re-review. One full validation and review per stable head, one consolidated fix cycle, and a recorded reason if a third is needed |
| [#560] (09-24) | Independent review as the default gate. Made risk-based |
| [#565] (09-24–25) | Commits that only copied merge state into planning files. GitHub is the source of execution status; planning files change only when their decision content changes |
| [#612] (09-25) | A default live-model evaluation matrix. Unit tests first |

None of the four weakened an actual safety or authority gate. The common diagnosis was that **the volume of process, not the quality of evidence, was driving the cost**. The remedy was the same too: put an explicit soft budget on that volume, allowed to be exceeded for correctness, with the reason recorded. Weeks later the lesson stands in the same words: targeted tests while editing, batched fixes, pushes at coherent checkpoints, CI and review on a stable head, no duplicate review or polling.

---

## 17. Humans and AI building together

### 17.1. Only the owner activates a goal

The root `delivery-plan.yaml` holds the selection, order, scope, dependencies and authority of the current goal. Only its `next_goal` is something an autonomous session may execute. If its status is not `active`, nothing is selected. "Prepared" (owner-activated-goal-ready) and "active" differ, and open does not mean active. The next piece of work is never inferred from open issues, open pull requests, tracker files or a closed program's records (C13). **[Contract]**

This principle was tested several times. One pause changed only the program-status layer and not the iteration activation status that the selection logic actually reads, and was "nearly ineffective" ([#443]). Conversely, a finished program was found to be re-selectable by changing one field, so a separate "already complete" gate was added so that it cannot be selected again even if `next_goal` is forced to `active` ([#467]). Planning state drifting away from actual GitHub state happened almost mechanically, and about a dozen GOV issues were needed to fix it. The structural fix was the rule "GitHub is the source of execution status" ([#565]).

The rule was also once over-applied. For a change the owner asked for directly in conversation, a session created unnecessary plan activation and closeout PRs. [#1191] added one sentence: **a change the owner asks for directly in the conversation is authorized by that request.** Use the normal issue → branch → PR flow, and don't record it in the plan file unless the owner wants a tracked program. This compendium was made under that rule.

### 17.2. Incremental delivery and waiting on merge

Sessions blocked on external gates (pending review, disabled auto-merge, flaky required checks) used to claim false completion, repeat the same "still blocked" report or try unsafe workarounds. [#365] separated implementation, integration and operating evidence into three independent dimensions. If only integration remains, a session may end with a useful handoff receipt (`integration_pending`), but may not mark the goal complete, close the issue or activate a successor. Admin merges, force pushes, false checks, dismissing approvals and removing protections were explicitly rejected. **"A running review is not a failed implementation."** **[Contract]**

### 17.3. Each session in its own workspace

On 30 September the owner found that the main working copy running their live server had been moved onto another session's feature branch, with more than 30 stale worktrees piled up. [#884] set the rule: concurrent sessions each work in their own worktree, and the main working copy stays on `main`. Because worktrees share the stash stack, bare `git stash` is not used. After merging, a session removes only its own worktree and branch. **[Contract]**

### 17.4. The delegation handoff loop and its review

The handoff loop for autonomous delivery is a one-shot check that reads GitHub issue labels (`agent:ready`, `working`, `review`, `rework`, `approved`, `blocked`) and hands work to an implementer or reviewer. It never creates schedules, model sessions, branches, PRs, merges or issues by itself. **A label alone is not authority**; an executable goal, allowed authority and owner-maintained markers are required.

The loop's independent review record shows what review actually finds. Label transitions were not concurrency-safe (a read-then-edit race). An issue could be claimed as "working" and stranded when no executor existed. Receipt recovery did not work on the real timeout path. The mere presence of heading text was treated as both authority and dependency satisfaction. And authority was being built from every prepared goal instead of the one active goal. Each was fixed and re-reviewed, and a finding later shown to be a review-counting error was explicitly withdrawn. **[Historical]**

### 17.5. Many AIs and many people contributing (#1267)

On 10 October the owner opened the project to contributors who each run several AI tools and roles at their own expense. AI provider, number of subscriptions and compute budget **never determine eligibility or voting power.** Six roles were defined: proposer, implementer, self-reviewer, independent reviewer, maintainer decision assistant and release preparation assistant. One person may hold several roles on different PRs but may not independently review their own PR with another model. AI roles never get GitHub merge or release permissions. Findings come with evidence, and a Merge Decision Brief records the head, checks, evidence inspected, independent review status, unresolved findings and a verdict. Nothing is merged on an AI score, a number of models or consensus alone. **The human contributor remains accountable.** **[Contract]**

Three days later a small side effect appeared. Contribution was open to "any AI or none", yet the last 60 merged PRs, including Claude Code work, still used the `codex/` branch prefix from 8 September, when Codex was the only implementer. [#1378] made branch names describe purpose, not tool (`<type>/<issue>-<slug>`), and kept old names as immutable evidence (C12).

### 17.6. Merge is not release

The release policy separates merge from stable publication. It also defined what the "public interface" of a local-first personal OS is: durable owner state, stores and migrations; configuration with connections, secret slots and folders; CLI commands, flags and install paths; AgentPackage and skill manifest schemas; supported platforms and Python versions. "One version = one milestone = one top-level goal" became the rule ([#1200], 10-09). On 13 October three hand-made release PRs and the tag, checksum and Homebrew steps moved into a workflow behind a single owner approval ([#1472]). Automatic or unapproved publication, and an AI approving the release environment deployment, were rejected. **[Contract]**

### 17.7. The economics of tokens

When developing with AI, cost was as important a constraint as quality. [#408] began from the observation that repeated small fixes, full test runs and re-reviews were using more context than the implementation itself, and [#612] began from the token cost and reinvention problem the owner raised. The outcome became a standing rule of the repository: targeted tests while editing, findings collected and fixed at once, pushes at coherent checkpoints, CI and review on a stable head, no repeated review or polling. The Goal Execution Contract says: "Repeated broad cycles without a new reason are a signal to stop micro-fixing and batch remediation." Live-model evaluation does not run by default either; only the cases matching what changed are chosen. Judging results by what the owner accepted rather than by tokens spent is the same idea as "economics measured on successful work" in Chapter 20.

---

# Part 5. The record of trial and error

## 18. Changes of direction and dropped decisions

This project does not erase what it drops. Issues closed as not planned remain design records, and removed protections remain in archive documents (C12). If something is needed again, a small successor is created from the then-current architecture instead of reopening the old issue. Below are the changes of direction that appear in this document. **[Historical]**

| When | Dropped | Replaced by | Why |
|---|---|---|---|
| 09-05 | A multi-tenant Kubernetes platform | A persistent environment per owner → a single installed process (09-06) | The product unit is the owner, not the task. Too many failure points for a first run |
| 09-08 | "Complete" with unconnected mock adapters | MP1 remediation R-01 to R-05 | Completion needs a policy-owning orchestrator that actually wires things together |
| 09-24 | The Agent Distribution Platform epic and its 12 children ([#333]) | A useful default assistant first | Immediate usefulness over a marketplace. Design kept as records |
| 09-24 | Rule-based first-use remediation (FU1) | Model-first Presence ([#523]) | Decision engine first rather than per-feature rule fixes |
| 09-24 | A generic "my records" screen | Links straight from the Work ([#562]) | Browsing all records without context was not needed |
| 09-24 | Replacing the transport library ([#420]) | Not planned | Structural blocker: a giant service class without a seam |
| 09-25 | A blanket verification matrix ([#602]) | Unit tests first ([#612]) | Token cost and reinvention |
| 09-26 | The Presence program ([#508]) | The Secretary program ([#653]) | Implementation had narrowed to a rule-guarded search path |
| 09-26–28 | Per-request sensitivity judgment, search gate, private/public separation | Information-use audit ([#654], [#705], [#826]) | Protection blocked usefulness. Only two real protections kept |
| 09-26 | The "no cart authority" rule | In the pilot, carts and holds without approval; payment only with approval | Non-payment changes on the owner's own accounts |
| 09-27 | Naver Search API, separate browser install | AI built-in web search, system WebKit ([#677]) | Terms prohibit AI use; installation burden |
| 09-28 | A plan in which the orchestrator rewrites the request and picks tools | The owner's words verbatim, notes only ([#820], [#826]) | Most failures came from the layers between the owner and the AI |
| 09-30 | Intent rules that decide the route | Rule judgments as hints ([#832]) | Same reason |
| 09-30 | The web as a management console ([#379]) | Manage in conversation; pages only for results and necessary inputs ([#880]) | "Designing settings as a management page was a misjudgment" |
| 10-01 | Telegram status cards | Reaction emoji, typing indicator and a stoppable draft ([#958]) | Status cards felt like a console |
| 10-02 | Asking about every memory | Save, tell, undo ([#918] a) | "Every act of asking is not a good user experience" |
| 10-02 | A blanket ban on task-named guidance text | Allowed only as skill content ([#974]) | An over-broad ban that blocked even site-knowledge packages |
| 10-09 | Docker and Kubernetes deployment config | Removed ([#1198]) | Not the real install path |
| 10-09 | Ad hoc manual releases | One version = one milestone = one goal ([#1200]) | Many open issues and no active goal |
| 10-11 | "Next step only when nothing is usable" | Weigh the routes ([#1380]) | A lighter route could not be chosen |
| 10-11 | Tool-named `codex/` branches | Purpose-named branches ([#1378]) | Contribution opened to any AI |
| 10-12 | A worker catalogue spanning providers | Only within the main AI's family ([#1431]) | Owner decision |
| 10-13 | Three hand-made release PRs | A workflow behind one approval ([#1472]) | Repetitive manual work |
| 10-14 | Adding one prompt sentence per incident | Prompt files with sections and a priority order, and a change table ([#1508]) | Stale rules, contradictions, duplication |

A few currents run through this table.

- **Remove rather than add.** Many decisions removed the layers that crept in between the AI and the owner rather than adding guards or branches when something failed (6.4, 10.2).
- **Protection is a dial, not a ratchet.** Sensitivity judgments, private/public separation and ask-first were built and then removed for the pilot. The two invariants, secrets and payment, stayed throughout. What was removed was not declared "wrong" but "deferred to a separately activated hardening program".
- **Scenario-specific code is not a fight won once.** C16, its amendment, the Secretary program's founding rule, CONNECT-01's non-goals and PROC-MEM-01's generality guards all restate the same constraint. In AI-driven development, sliding toward task-specific implementation is a standing pressure that has to be pushed back at every new feature.
- **Usefulness over ambition.** The big vision of a marketplace and registry was not left quietly open but closed as not planned, with the design kept as citable history.

---

## 19. Timeline

| Period | Phase | Main events |
|---|---|---|
| 09-05–06 | Bootstrap | First commit, the Kubernetes design and its immediate reversal, Homebrew-installed 0.2 |
| 09-07–08 | Automated delivery sprint | About 156 commits in two days. Hub v2, MP1 and MP2, the first closeout ([#282]), repository restructuring |
| 09-09–19 | A slower pace | Incremental delivery ([#365]), the file workspace, the distribution platform foundation (D-AP-01), the first dogfood slice, the usefulness audit |
| 09-20–23 | Parallel worktree program | The PA1 Telegram assistant, the web management contract, a series of plan-state corrections, the Presence research (09-23) |
| 09-24 | The day of the big pivot | 122 commits in a day. Reuse infrastructure, retiring old surfaces, the repository rename, closing the distribution platform epic, activating model-first Presence, the decision engine ([#415]) |
| 09-25–26 | Governance corrections and Secretary | Correcting verification overload, the execution contract, current context ([#625]), Secretary and the pilot posture ([#653]) |
| 09-27–30 | C16 and opening up | C16 ([#712]), the orchestrator ([#710]), the request-path census ([#820]), opening egress with the information-use audit ([#826]), dropping management pages ([#880]), the worktree rule ([#884]), not asking ([#918]) |
| 10-01–05 | Owner model and skills | Skill supply ([#959]), the C16 amendment ([#974]), the first real-use sample, self-review ([#998]), the role ontology research, settling the resume point ([#1021]) |
| 10-06 | The day of documents | The upstream program's five children completed, canonical contracts separated from superseded amendment history, the whitepaper v0.3 review |
| 10-07–09 | Install and release | uv and WSL2 install ([#1167]), Docker and Kubernetes removed ([#1198]), version-based releases, `v1.3.0` |
| 10-10–11 | The Connect sprint | The Connection and Delegation Contract ([#1221]), CONNECT-01 activated and its six children completed ([#1295]), the professional roles contract, contribution governance ([#1267]), a corrective issue ([#1375]) |
| 10-12 | Polishing | The README demo, main-AI family only ([#1431]), secret sets for code runs, unified effect classification |
| 10-13 | Release automation | The release workflow ([#1472]), terminal first-run setup, approval UX honesty fixes, approved state changes ([#1456]), the procedural memory program begins ([#1482]) |

The most recent turning point in the repository is procedural memory (PROC-MEM-01, [#1482]). Instead of rediscovering API documentation from scratch for every request, it would accumulate, per connection, function libraries that are verified, parameterized and gated by outcomes. It too requires generality guards from the start: two different synthetic verification domains and the no-scenario-code test. It also cites research showing that naive AI self-generated skills performed poorly. **[Contract]·in progress**

---

# Part 6. Industry and business

This part is not a verified market size or revenue forecast. It covers business directions drawn from the hypotheses above and the counterarguments to them, carried over from chapters 10 and 11 of the whitepaper *Whose Agent?* and fitted to this document's context. **[Hypothesis]**

## 20. Business directions: where companies provide value

### 20.1. Redefining the unit of competition

| Actor | Direction of value | What must be verified |
|---|---|---|
| Model providers | Intelligence worth choosing even inside other agent environments | Task quality, cost, reliability, ease of connection |
| Personal agent businesses | Integrated management of context, authority, work continuity and recovery | Repeated-use value and real switchability |
| Existing service companies | Services people and agents can both use | Accurate shared state, execution conditions, result confirmation |
| Skill and professional service providers | Maintained, verified capability for specific tasks | Success conditions, support scope, reproducibility, operating cost |
| Migration, verification and operations businesses | Lower burden of switching providers and running several environments | Transfer loss, cost of verifying evidence, quality of failure recovery |

This structure does not remove existing businesses. Model providers can run integrated products and external supply together, and device, telecom and cloud businesses can find roles in execution environments and operations. What matters is not predicting "which company will win" but seeing **how existing businesses' strengths and weaknesses change once customers start choosing things separately**.

### 20.2. Who pays for a personal agent

If you propose an agent that acts on the user's side, the revenue model has to be examined alongside that promise. In a user subscription, users must feel recurring benefit; keeping memory and control may not be enough, and actual task completion and recovery have to give a reason to pay. Where a company pays, its work authority must be kept apart from the employee's personal context; paying the bill must never be read as unlimited access to all personal information and activity. Where revenue comes from seller fees or transactions, how commercial interests shape recommendations and actions must be visible.

> **Who pays is an accounting question and also a product question: it explains on whose behalf the agent acts.**

### 20.3. Measure economics on successful work

Don't evaluate the economics of agent services by token cost alone. Count failed attempts, re-runs, maintenance when external services change, customer support, and the user's review and recovery burden, and look at **the total cost of delivering one completed result the user accepted**. Divide the total delivery cost in an observation period by the number of accepted completions, include resources spent on failures and cancellations, and report the success rate alongside. With zero completions, there is no low unit cost to report. Show the user's review time separately from the business's cash cost, and disclose the assumptions if time is converted to money.

If a professional skill shortens execution but takes more time to check for errors, the benefit is small. Conversely, even without full automation, getting the user's judgment at the right moment and reducing errors and recovery has value. When adding execution evidence, weigh the cost of issuing, transmitting, storing and verifying it against the investigation, dispute and support costs it saves. So the goal is not "as much autonomous action as possible" but **a trustworthy completion experience at a cost people can pay**.

The repository's working principles point the same way: "explore once, repeat cheaply", "no cost when nothing changed", and in the development process itself, judging by the quality of evidence rather than the volume of process (11.5, 17.7).

### 20.4. Switching should not require the whole ecosystem to move at once

If value appears only once every service issues standard evidence and every memory moves perfectly, adoption is hard. Create usefulness first within connections already allowed and a limited range of tasks, then expand by reducing the recurring burden of moving, recovering and collaborating. A personal agent business can start with checking and correcting memory and resuming work; a service business can start with state queries and change confirmation for tasks with proven demand. Sellers have reason to weigh the benefit of allowing outside agents against fewer customer touchpoints, integration maintenance and the cost of handling wrong executions. **The test for adoption is not agreement on openness but whether, for each participant, the benefit exceeds the burden of integration, operation and verification.**

---

## 21. Counterarguments and alternative scenarios

### 21.1. An integrated product may be better

A product that optimizes model, memory and execution environment together may give a more consistent experience; this is an important counterargument. If a structure that separates the user-side layer complicates setup, adds failure points and lowers quality, theoretical choice does not become real benefit. So separation is not a goal in itself. What has to be verified is whether real replaceability can be offered while keeping the convenience of integration.

The repository's experience does not dismiss this. The decision to vary models only within the main AI's family ([#1431]) can be read as a practical judgment that consistency within one family is better than moving freely between providers. Dropping Kubernetes was likewise a choice of simple installation over a structure with many failure points.

### 21.2. Users may choose convenience over portability

Users finding provider lock-in inconvenient does not mean they will pay for or often use transfer features. Portability may be a latent, insurance-like value and a low priority in everyday purchase decisions. So a product has to be chosen **for the value of the work it does now**, not just for control. That is also why the repository keeps writing "few bundles is acceptable; intentionally weak basic functionality is not" and chose a useful default assistant before a distribution platform.

### 21.3. A personal agent can itself become a new control point

If a personal agent mediates the choice and execution of many services, it can gain influence over which services are discovered, recommended and used. Becoming independent of model providers only to be locked into an agent platform is not the outcome this project aims at. Service selection criteria, commercial relationships and the history of authority use must be reviewable along with memory and work state. Evidence systems are no exception: if the information and access needed for verification are tied to one business, execution evidence becomes a new lock-in point.

Several of the repository's rules answer this risk. Popularity and ratings cannot raise a trust rung. The owner's way comes before any default. The information-use audit shows where information went. The test "if you cannot leave Still, user independence is not enough" is applied to itself.

### 21.4. Not assuming a single future

Three structures may compete or coexist. In an **integrated structure** one provider offers everything from the model to the assistant experience. In a **connected platform structure** many models and services are connected but a central platform manages core context and distribution. In a **user-led structure** the user keeps control over personal context and delegation while changing providers and execution environments. These are scenarios for comparison, not predictions with probabilities, and they are not mutually exclusive: an integrated product can also offer user-led transfer and revocation. The point is **to set criteria for what switchability and means of control users must have, whichever structure is chosen**.

### 21.5. User control does not mean owning external services' authority

Even when a person manages their own memory and delegations, they cannot invent access paths or records an external service does not provide. Account access, data use and a service's processing conditions must be checked for each connection. The banner of a user-led agent is no excuse for bypassing a service's access restrictions or asserting unproven results as facts. The repository's Constitution C14 (identity verification, MFA, CAPTCHA, agreement acceptance and payment are never simulated or bypassed) and connection precedence's "if there is nothing, say so and never guess" are the same principle. When a needed connection or evidence cannot be obtained, present the allowed alternatives, user intervention and what cannot be confirmed. If these limits recur in major tasks and cost more than the benefit of resolving them, the business case for an independent agent layer weakens. That possibility must not be left out while presenting differentiation as the inevitable future.

---

# Part 7. Open questions and the verification agenda

## 22. What is confirmed and what is not

### 22.1. Still is a case, not a proof

This document does not treat Still as a finished product that proves the industry's future. It treats it as a development case that makes industry questions concrete and draws out what needs verifying. The real-use research of 2026-09-23 reported that ordinary requests were expressed as task and status messages, failures sent the owner to a management screen instead of continuing the conversation, and follow-up requests were not handled as a continuation of the earlier failure. It also recorded that the configured AI and the actual execution path did not match. The interpretation drawn: **having an internal structure that manages task execution is separate from the user feeling they collaborate with a persistent assistant. Having a selection screen is different from that selection actually taking effect.**

> **One project's observations do not prove how often something happens across the industry, but they can show concretely what the industry needs to verify.**

### 22.2. The current boundary of evidence

Regrouping what this document covered by evidence class:

| Class | Examples of what is confirmed |
|---|---|
| **[Observed]** | Public release `v1.3.0` (Homebrew and install script). Real-use recording excerpts in the README (a remembered taste, a continued search, confirming a cart after sign-in, a cart conversation continuing across an AI switch). The first skill-evaluation sample's sign-in request and the truthfulness bug it found. Tool-call decision points in Claude Code and Codex. The compatibility test of the code-run sandbox |
| **[Implemented]** | The decision-engine boundary and routes, the three orchestration rules, the information-use audit, saving memory without asking with undo, settings in conversation, skill supply, pinning and revocation, connection precedence and trust records, the continuity acceptance criteria, code run phase 1. All on unit and integration tests |
| **[Contract]** | Kernel concepts and schemas, owner control rights, Presence principles, current context, correct/forget/delete, the mandate, separating role and mandate |
| **[Proposal]** | The owner-model ledger and decision-journal schema, memory slots and retrieval, portable execution evidence, the role ontology |

The product status page explicitly leaves these as "no current claim": autonomous purchasing, arbitrary computer use, universal web verification, live stock and checkout verification, arbitrary third-party package execution, and a public marketplace. Other things not yet observed are noted throughout this document: the decision engine actually running on the owner's accounts, the cause of the mismatch between real Telegram behaviour and the Presence fixtures, how well a model follows a skill's method on real sites, and actual continuation for a service request that was not prepared in advance ([#1375]).

### 22.3. The next role

Still's next role is to grow into a reference implementation and evaluation environment where these hypotheses can be tested repeatedly. That is a research role this document proposes, not an instruction that activates the current development plan. Much of the open-source proposal ([#875]) is also undecided. Only contribution readiness (Track C) has run; Portable Memory interoperability (Track A), verifiable claims (Track B) and open research and evaluation (Track D) await the owner's decision.

---

## 23. The verification agenda

If it stops at listing principles that are easy to agree with, this direction will not become an empirical claim. The core verification items the whitepaper proposes: **[Proposal]**

| Hypothesis | Comparison | Main indicators |
|---|---|---|
| Moving memory reduces the real burden of switching | An environment with transferred context versus starting fresh | Time spent re-explaining, loss of meaning, wrongly applied memories, number of corrections |
| Model replacement and work continuity can be offered together | Resume the same work state after switching models | State preservation, completion quality, cost change, authority violations |
| Presence gives benefits beyond tone | Improving wording only versus improving continuity and recovery too | Recovery time, repeated explanation, understanding of state, error awareness |
| A collaborative agent is a useful mode in its own right | Manual work, full delegation and joint work compared | User effort, corrections applied, errors, acceptance of results |
| Skills can become sustainable professional services | General handling versus a professional skill | Total cost per successful result, maintenance burden, reuse and willingness to pay |
| Retained execution evidence helps separate causes after switching agents | Moving only a conversation summary versus also moving processing and change evidence | Accuracy of cause attribution, unfounded actor claims, detection of omissions, investigation time, cost of evidence |

Evaluation does not pick only successes. Include failures during model switches, stale memories creeping in, expired authority, users changing course midway, and external effects that could not be confirmed. Evaluations of execution evidence include delete-after-add, lost responses, duplicate requests, stale state, unrecorded changes and unverifiable signatures, and check that success or actors are not asserted when records are missing or conflicting. Presence evaluation does not aim for maximal trust; noticing errors, understanding authority and being able to step in at the right moment matter too.

Set the criteria before the experiment, not after seeing results. Define first which losses are acceptable and which authority violations are not, and how much reduction in user effort counts as meaningful for the product. Match tasks, models, tools and budgets as far as possible, and separate comparisons across models from comparisons of structure on the same model. Include a well-working integrated product as a comparison. Evaluate on separate tasks fixed in advance, not only on those used to build the examples. The repository's held-out scenario rule (9.1) and "first attempt, not the best of three" (16.2) are the same idea.

The first units of verification can be small: a test that moves memory to another environment and back, one complete task in which a person and an agent edit a cart together, stop and resume, and a test in which a new agent separates earlier processing from later changes. **Choosing small cases is not to shrink the industry hypothesis to a small feature but to turn a big claim into units of observation a reader can check.** If experiments show the benefit of transfer is small, or the complexity and cost of an independent agent layer keep exceeding the benefit, the product strategy must change. This table is not a table of results from experiments performed; participants, repetitions, budget and prior criteria are not yet set.

---

## 24. Open questions

The repository's documents do not hide unresolved tensions. Here are those that came up repeatedly across the five streams of source digests.

**Authority and protection**

- **Can transparency replace prevention?** The information-use audit tells you what happened but does not stop it. Injection and exfiltration defences are parked. The risk that a memory save induced by text on an untrusted page becomes current memory at once is only recorded.
- **How temporary is the pilot?** Pilot exceptions are layered over the base rules of several contracts. The rule that actually applies is often the newest amendment rather than the base text, so a future reader may apply stale text. The hardening program is not yet defined.
- **How does a mandate connect to existing Grants?** The role ontology research adapted only at the level of meaning and deferred runtime integration. It remains on that research's own list of open questions.

**Judgment and orchestration**

- **Mid-work corrections do not reach the orchestrator.** It is a named gap in which re-delegation can happen on a stale plan (9.4).
- **The specialized judgment route keeps being deferred.** No real judgment on the owner's accounts has been observed yet.
- **Restricting to the main AI's family narrows the original multi-provider orchestration idea considerably.** The recorded basis is a single owner decision.
- **Codex and Claude Code are not equal in connection and skill control.** How they use decision points, how a new server is prepared and how thoroughly skill discovery lockout was verified all differ.

**Memory**

- **How should confidence be expressed?** Three research streams independently reached the same conclusion: no standard or product defines calibrated confidence for a personal agent's beliefs; store the model-reported value, show it as a category, and never hard-code thresholds in code. A strong convergence, but a design judgment, not an experimental result.
- **The three altitudes of forgetting do not yet mesh.** The correct/forget/delete contract (#794) and the SQLite-level forgetting research do not refer to each other directly. The link has to be checked when it is implemented.
- **The bitemporal recommendation in the memory design is far finer than the current implementation.** Today's memory table has no validity period or retraction time.

**Skills and connections**

- **AI-written owner skills are undecided.** Between "supply first" and "learn only the gaps" there is no mechanism yet for an AI to safely write a new skill for the owner. The procedural memory program ([#1482]) is approaching that question.
- **The safest route may be the least capable.** On AI-side connections without a decision point, changes are not offered at all, so real work gets pushed to Still-owned connections or code runs.
- **The premise of "supply first" is unconfirmed.** No official skill for a particular site was confirmed in the review.

**Product**

- **Local first is not local only.** Subscription AIs run on provider servers. Control is handled by making destinations and consent visible rather than by keeping everything local.
- **When truthfulness and apparent usefulness conflict.** The rule of never saying "delivered" without a receipt can end many shares as "delivery uncertain".
- **The distance between fixtures and real use.** Development completion has been contradicted by real use several times. Ways to narrow that distance — designing real-use samples, held-out scenarios, owner-operated trials — are still being refined.

---

## Conclusion: choosing an AI should not mean giving up one's digital environment

The future this project pictures is not one where every user assembles models, memory stores and execution tools themselves. Users should be able to choose one convenient experience. And even when the provider of that experience changes, they should be able to keep the context they built up as far as possible, know what they lose, and decide again what to entrust to whom.

The forty-day record shows what it looks like to turn that into working software. It started on Kubernetes and became an installed process within a day. It built elaborate protections, then removed them, keeping two real ones. It changed an orchestrator that rewrote requests into one that passes on the owner's words verbatim. It changed an assistant that asked about every memory into one that saves, tells and can undo. It replaced management screens with conversation. Along the way, "development complete" was contradicted by real use several times, and each time the evidence classes were re-established. What will last longest from this record is probably not particular code but a few principles.

- **AI is the engine.** Still does not implement requests; it guards what must be guarded and removes blockers.
- **Installing is not authority, a role is not authority, and talking is not authority.**
- **Moving memory is not moving authority.**
- **Saying "done" is not evidence that it was done.**
- **Don't make a strong claim on weak evidence.**
- **Before adding, check whether there is a layer to remove.**

> **Intelligence can be supplied. Execution can be delegated.**
> **But there is no reason to hand over personal context and the power to choose along with them.**

Can these principles be realized as a convenient product experience and a sustainable business? That is the challenge this project poses. To close with VISION's last line: **It does not have to be the final answer. It should be a meaningful beginning.**

---

# Appendices

## Appendix A. Glossary

| Term | Meaning | See |
|---|---|---|
| AgentPackage | The installable product unit. Its manifest describes authority but cannot create it | 5.1, 12.5 |
| Artifact | An owner-owned durable result or material; original, derived, draft and final stay distinct | 5.1, 5.7 |
| Bitemporal | Recording both when something was true in the world and when the system held it | 7.6 |
| C1–C16 | The principles of the Development Constitution | 14.2 |
| CC1–CC8 | Continuity acceptance criteria for carrying on a request blocked midway | 11.6 |
| `code_run` | A sandboxed code-execution tool for non-standard APIs | 11.7 |
| Context | Bounded information issued per Work. Never automatically memory | 5.1, 5.3 |
| Context epoch | A counter that keeps late data from reviving context after a clear | 7.5 |
| Control levels | Mediate / Decide per call / Delegate | 11.3 |
| DecisionEngine | A provider-neutral boundary for semantic judgment. Not a source of authority | 6.3 |
| Evidence and evidence classes | Sealed records. A stronger class is never inferred from a weaker one | 5.5, 16.1 |
| Existing Solutions Review | The required review for an Adopt → Adapt → Build decision, also at proposal time | 15 |
| Forgettable payloads | Keeping personal content in a separately deletable table | 7.7 |
| Grant | The only authority record, bound to subject, Work, scope, destination and expiry | 5.1 |
| Held-out scenario | A test scenario never seen during development, chosen by the owner at closeout | 9.1 |
| Information-use audit | A record of which owner information a Work used and where it went | 10.2 |
| Mandate | The goal, scope and constraints the owner actually entrusted. Not authority by itself | 11.4, 13.3 |
| Memory and MemoryCandidate | Canonical memory, and a third-party proposal awaiting acceptance | 5.1, 7.4 |
| Pages principle | Pages only for results and necessary inputs; manage in conversation | 8.6 |
| Pilot posture | The pilot's principle of enforcing only secrets and payment approval on every request | 10.1 |
| Presence | One assistant experience that continues across turns, tools, workers, failures and restarts | 8.2 |
| Projection | Internal concepts leaking into the owner's conversation and screens | 8.2 |
| Skill | Versioned knowledge the owner's AI chooses to load. Not authority | 6.5, 12 |
| The owner's way | A known choice of which service or option to use. Comes before every default | 11.4 |
| Trust ladder | Report → Propose → Act after approval → Act within a standing mandate | 11.4 |
| Work | The execution lifecycle Still owns. Only Still settles its end | 5.4 |

## Appendix B. Document map

This compendium is a summary. The sources below set the rules.

| Topic | Sources |
|---|---|
| Why this project | [VISION](../VISION.md), [README](../../README.md) |
| The original industry and business whitepaper | [Whose Agent? (Korean)](whose-agent.ko.md), [editorial and review record](review-plan.en.md) |
| Development rules | [AGENTS.md](../../AGENTS.md), [Development Constitution](../development-constitution.en.md), [Development Governance](../development-governance.en.md), [Goal Execution Contract](../goal-execution-contract.en.md), [Incremental Delivery](../incremental-delivery.en.md) |
| Architecture and kernel | [Architecture](../personal-agentos-architecture.en.md), [Core primitives and AgentPackage v0.1](../core-primitives-agentpackage-v0.1.en.md), [Agent Distribution Platform Foundation](../agent-distribution-platform-foundation.en.md) |
| Judgment and execution | [Decision layer](../decision-layer.en.md), [Assistant Execution Contract](../assistant-execution-contract.en.md), [Request-path rule census](../request-path-rule-census.en.md) |
| Secretary and the pilot | [Secretary Agency Contract](../secretary-agency-contract.en.md), [Secretary spec research (Korean)](../research/secretary-agency-spec-2026-09-26.ko.md) |
| Presence | [Presence Experience Contract](../presence-experience-contract.en.md), [Presence and settings UX research (Korean)](../research/presence-and-settings-ux.ko.md), [PRESENCE-EVAL-01](../presence-eval-01.en.md) |
| Owner control and usefulness | [Owner Control Contract](../owner-control-contract.en.md), [Default agent usefulness](../default-agent-usefulness.en.md) |
| Memory and context | [Current Context Contract](../current-context-contract.en.md), [implementer playbook](../current-context-implementation.en.md), [Owner model phase 3](../owner-model-phase3-delivery.en.md), [Owner model and decision journal research (Korean)](../research/owner-model-and-decision-journal-2026-09-28.ko.md) |
| Connection and delegation | [Connection and Delegation Contract](../connection-delegation-contract.en.md), [Connection support](../connection-support.en.md), [Code run design](../connect-code-run-design.en.md) |
| Skills | [Skill supply preparation](../skill-supply-execution-preparation.en.md), [Interface decision](../skill-prep-01-interface-decision.en.md), [Skill evaluation](../skill-eval-01-coverage.en.md) |
| Roles | [Professional Roles and Communication Contract](../professional-roles-and-communication-contract.en.md), [Role ontology and mandate research (Korean)](../research/role-ontology-and-mandate-2026-10-05.ko.md) |
| Outside relationships | [Upstream relationship map](../upstream-relationship-map.en.md), [Dependency update contract](../dependency-update-contract.en.md), [Acknowledgements](../acknowledgements.en.md) |
| Contribution and release | [GOVERNANCE](../../GOVERNANCE.md), [AI contribution and review](../governance/ai-contribution-review.en.md), [Release policy](../governance/release-policy.en.md) |
| Current status | [Product status](../product-status.en.md), [Roadmap](../roadmap.md) |

Research notes the owner has not yet committed to the repository (memory item shape and retrieval, memory architectures, provenance, time and forgetting, decision tracing and audit, memory transparency UX) were also used as grounds for Chapter 7. Because they are not in the public repository, the text cites them by topic only.

## Appendix C. References

The sources below support historical facts, the scope of public technologies and research findings. They do not prove that industry layers must separate, that there is market demand or that the proposed structure is superior. Sources taken from the whitepaper were checked as of 2026-10-06. Research sources on memory and decision journals are cited as the 2026-09-28 research notes cite them.

[^compuserve]: Computer History Museum, [CompuServe](https://www.computerhistory.org/revolution/the-web/20/400/2337). An example of content and communication features bundled by early services; not an analysis of the Korean PC-communication industry as a whole.
[^cern]: CERN, [The birth of the Web](https://home.cern/science/computing/the-birth-of-the-web/). Release on 30 April 1993 and later open-licence distribution.
[^rfc3724]: IAB, [RFC 3724 — The Rise of the Middle and the Future of End-to-End](https://www.rfc-editor.org/rfc/rfc3724.html), March 2004. An Informational document on internet architecture.
[^apple2008]: Apple, [Apple Announces iPhone 2.0 Software Beta](https://www.apple.com/newsroom/2008/03/06Apple-Announces-iPhone-2-0-Software-Beta/), 6 March 2008.
[^cma2022]: UK Competition and Markets Authority, [CMA plans market investigation into mobile browsers and cloud gaming](https://www.gov.uk/government/news/cma-plans-market-investigation-into-mobile-browsers-and-cloud-gaming), 10 June 2022.
[^claude-memory]: Anthropic, [Import and export your memory from Claude](https://support.claude.com/en/articles/12123587-import-and-export-your-memory-from-claude). Product documentation on the experimental nature of importing.
[^ucp]: Universal Commerce Protocol, [official documentation](https://ucp.dev/). Reference for the protocol's scope.
[^vc]: W3C, [Verifiable Credentials Data Model v2.0](https://www.w3.org/TR/vc-data-model-2.0/). Separates verifiability from the truth of claims.
[^outbox]: AWS Prescriptive Guidance, [Transactional outbox pattern](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html).
[^http-signatures]: IETF, [RFC 9421 — HTTP Message Signatures](https://www.rfc-editor.org/rfc/rfc9421.html), February 2024.
[^nist-blockchain]: NIST, [Blockchain](https://www.nist.gov/blockchain).
[^ct]: IETF, [RFC 9162 — Certificate Transparency Version 2.0](https://www.rfc-editor.org/rfc/rfc9162.html), December 2021.
[^scitt]: IETF, [RFC 9943 — An Architecture for Trustworthy and Transparent Digital Supply Chains](https://www.rfc-editor.org/rfc/rfc9943.html), June 2026.
[^skills]: Agent Skills, [Overview](https://agentskills.io/home). An open format's existence does not prove the quality or profitability of skills.
[^mcp]: Model Context Protocol, [Architecture overview, 2026-07-28 edition](https://modelcontextprotocol.io/docs/2026-07-28/learn/architecture).
[^a2a]: A2A Protocol, [official overview](https://a2a-protocol.org/latest/).
[^cot]: Chen et al., [Reasoning Models Don't Always Say What They Think](https://arxiv.org/abs/2505.05410), arXiv 2505.05410. Models often mentioned hint use in their chain of thought less than 20% of the time.
[^visibility]: Chan et al., [Visibility into AI Agents](https://arxiv.org/abs/2401.13138), FAccT 2024. Names identifiers, real-time monitoring and activity logging as visibility mechanisms.
[^selfportrait]: Dash et al., [The Algorithmic Self-Portrait](https://arxiv.org/abs/2602.01450), WWW 2026. Analysis of 2,050 memories from 80 ChatGPT users.
[^inference]: [When Are LLM Inferences Acceptable?](https://arxiv.org/abs/2605.10013), arXiv 2605.10013. Eighteen users rated 215 inferences.
[^scrutable]: Holden & Kay, [The scrutable user model and beyond](https://www.semanticscholar.org/paper/The-scrutable-user-model-and-beyond-Holden-Kay/3728db543fb75562c02fe0836885087c7780e555).
[^warmth]: Ibrahim et al., [Nature](https://www.nature.com/articles/s41586-026-10410-0). Models tuned for warmth were about 40% more likely to affirm a user's mistaken belief.
[^context-eng]: Anthropic, [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents). Sections, avoiding lists of edge cases, adding for observed failures and pruning. Checked 2026-10-14.
[^gpt5-guide]: OpenAI, [GPT-5 prompting guide](https://developers.openai.com/cookbook/examples/gpt-5/gpt-5_prompting_guide). Contradictory instructions waste reasoning; an explicit instruction hierarchy. Checked 2026-10-14.
[^optimize-prompts]: OpenAI, [Optimize prompts](https://developers.openai.com/cookbook/examples/optimize_prompts). The contradiction-checking stage of the prompt optimizer. The tool was not adopted. Checked 2026-10-14.
[^ifscale]: Jaroslawicz et al., [How Many Instructions Can LLMs Follow at Once?](https://arxiv.org/abs/2507.11538), arXiv 2507.11538. Instruction-following accuracy by instruction count.
[^dotprompt]: Google, [dotprompt](https://github.com/google/dotprompt). A prompt-file format that carries model settings. Not adopted.
[^poml]: Microsoft, [POML](https://microsoft.github.io/poml/latest/). A prompt markup format. Not adopted.
[^openhands]: OpenHands, [PR #3360](https://github.com/OpenHands/OpenHands/pull/3360). A `PromptManager` using Jinja2 templates. Not adopted.
[^cline]: Cline, [System prompts and tool definitions](https://deepwiki.com/cline/cline/4.6-system-prompts-and-tool-definitions). Describes the former component and variant registry. Current `main` has short per-mode templates instead (checked through the GitHub API, 2026-10-14).
[^codex-prompt]: OpenAI Codex, [`codex-rs/core/gpt_5_codex_prompt.md`](https://github.com/openai/codex/blob/main/codex-rs/core/gpt_5_codex_prompt.md). A Markdown prompt file per model family. Format adopted, no dependency.

[#282]: https://github.com/Jongtae/agentos/issues/282
[#333]: https://github.com/Jongtae/agentos/issues/333
[#334]: https://github.com/Jongtae/agentos/issues/334
[#365]: https://github.com/Jongtae/agentos/issues/365
[#379]: https://github.com/Jongtae/agentos/issues/379
[#408]: https://github.com/Jongtae/agentos/issues/408
[#415]: https://github.com/Jongtae/agentos/issues/415
[#417]: https://github.com/Jongtae/agentos/issues/417
[#420]: https://github.com/Jongtae/agentos/issues/420
[#443]: https://github.com/Jongtae/agentos/issues/443
[#467]: https://github.com/Jongtae/agentos/issues/467
[#476]: https://github.com/Jongtae/agentos/issues/476
[#488]: https://github.com/Jongtae/agentos/issues/488
[#508]: https://github.com/Jongtae/agentos/issues/508
[#512]: https://github.com/Jongtae/agentos/issues/512
[#523]: https://github.com/Jongtae/agentos/issues/523
[#538]: https://github.com/Jongtae/agentos/issues/538
[#560]: https://github.com/Jongtae/agentos/issues/560
[#562]: https://github.com/Jongtae/agentos/issues/562
[#565]: https://github.com/Jongtae/agentos/issues/565
[#567]: https://github.com/Jongtae/agentos/issues/567
[#581]: https://github.com/Jongtae/agentos/issues/581
[#597]: https://github.com/Jongtae/agentos/issues/597
[#602]: https://github.com/Jongtae/agentos/issues/602
[#605]: https://github.com/Jongtae/agentos/issues/605
[#609]: https://github.com/Jongtae/agentos/issues/609
[#612]: https://github.com/Jongtae/agentos/issues/612
[#625]: https://github.com/Jongtae/agentos/issues/625
[#653]: https://github.com/Jongtae/agentos/issues/653
[#654]: https://github.com/Jongtae/agentos/issues/654
[#660]: https://github.com/Jongtae/agentos/issues/660
[#661]: https://github.com/Jongtae/agentos/issues/661
[#662]: https://github.com/Jongtae/agentos/issues/662
[#677]: https://github.com/Jongtae/agentos/issues/677
[#698]: https://github.com/Jongtae/agentos/issues/698
[#705]: https://github.com/Jongtae/agentos/issues/705
[#710]: https://github.com/Jongtae/agentos/issues/710
[#712]: https://github.com/Jongtae/agentos/issues/712
[#719]: https://github.com/Jongtae/agentos/issues/719
[#752]: https://github.com/Jongtae/agentos/issues/752
[#805]: https://github.com/Jongtae/agentos/issues/805
[#814]: https://github.com/Jongtae/agentos/issues/814
[#818]: https://github.com/Jongtae/agentos/issues/818
[#820]: https://github.com/Jongtae/agentos/issues/820
[#826]: https://github.com/Jongtae/agentos/issues/826
[#832]: https://github.com/Jongtae/agentos/issues/832
[#835]: https://github.com/Jongtae/agentos/issues/835
[#836]: https://github.com/Jongtae/agentos/issues/836
[#839]: https://github.com/Jongtae/agentos/issues/839
[#847]: https://github.com/Jongtae/agentos/issues/847
[#848]: https://github.com/Jongtae/agentos/issues/848
[#855]: https://github.com/Jongtae/agentos/issues/855
[#858]: https://github.com/Jongtae/agentos/issues/858
[#875]: https://github.com/Jongtae/agentos/issues/875
[#880]: https://github.com/Jongtae/agentos/issues/880
[#884]: https://github.com/Jongtae/agentos/issues/884
[#918]: https://github.com/Jongtae/agentos/issues/918
[#958]: https://github.com/Jongtae/agentos/issues/958
[#959]: https://github.com/Jongtae/agentos/issues/959
[#964]: https://github.com/Jongtae/agentos/issues/964
[#974]: https://github.com/Jongtae/agentos/issues/974
[#998]: https://github.com/Jongtae/agentos/issues/998
[#1004]: https://github.com/Jongtae/agentos/issues/1004
[#1008]: https://github.com/Jongtae/agentos/issues/1008
[#1021]: https://github.com/Jongtae/agentos/issues/1021
[#1023]: https://github.com/Jongtae/agentos/issues/1023
[#1167]: https://github.com/Jongtae/agentos/issues/1167
[#1191]: https://github.com/Jongtae/agentos/issues/1191
[#1198]: https://github.com/Jongtae/agentos/issues/1198
[#1200]: https://github.com/Jongtae/agentos/issues/1200
[#1221]: https://github.com/Jongtae/agentos/issues/1221
[#1267]: https://github.com/Jongtae/agentos/issues/1267
[#1295]: https://github.com/Jongtae/agentos/issues/1295
[#1296]: https://github.com/Jongtae/agentos/issues/1296
[#1301]: https://github.com/Jongtae/agentos/issues/1301
[#1302]: https://github.com/Jongtae/agentos/issues/1302
[#1375]: https://github.com/Jongtae/agentos/issues/1375
[#1378]: https://github.com/Jongtae/agentos/issues/1378
[#1380]: https://github.com/Jongtae/agentos/issues/1380
[#1431]: https://github.com/Jongtae/agentos/issues/1431
[#1456]: https://github.com/Jongtae/agentos/issues/1456
[#1472]: https://github.com/Jongtae/agentos/issues/1472
[#1482]: https://github.com/Jongtae/agentos/issues/1482
[#1507]: https://github.com/Jongtae/agentos/issues/1507
[#1508]: https://github.com/Jongtae/agentos/issues/1508
[#1510]: https://github.com/Jongtae/agentos/issues/1510
[#1511]: https://github.com/Jongtae/agentos/pull/1511
[#1513]: https://github.com/Jongtae/agentos/pull/1513
