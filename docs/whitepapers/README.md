# Industry whitepapers

These documents develop industry, product and business hypotheses from Still design work. They are **non-normative research/editorial artifacts**, not implementation contracts or statements that proposed capabilities have shipped.

## Still Compendium

**Still 총람 — 두뇌는 빌리고, 비서는 내 것으로**

- [Read the Korean manuscript — v0.1](personal-agentos-compendium.ko.md) or the [English edition](personal-agentos-compendium.en.md).
- Tracking issue: [#1483](https://github.com/Jongtae/agentos/issues/1483).

One document that gathers the project's technologies, concepts, theory, concerns, problem statements and problem-solving methods from 2026-09-05 to 2026-10-13 (2026-10-14 for the prompt audit and clean-up): the problem, the kernel concepts, "AI is the engine", memory and context, Presence, collaboration, authority and evidence, connection and delegation, skills, roles, the development method, dropped decisions with a timeline, business directions, and open questions. It integrates the argument of *Whose Agent?* below, which stays unchanged.

Each topic runs problem → deliberation → decision (including rejected or reversed alternatives) → current status, and is labelled by evidence class (case, hypothesis, proposal, contract, implemented, observed, historical). Sources are the repository's documents, key decision issues and pull requests, the commit history and the owner's research notes not yet committed; those notes are cited by topic only.

**Review status:** written by an AI coding assistant at the owner's request and checked only by that same assistant. It is self-review, not independent expert, human or separate-agent review. Korean is the source language; the English edition is a translation with the same structure and claims. Nothing here changes `delivery-plan.yaml`, runtime authority, the active program or root README product claims; where it differs from a canonical contract, the contract wins.

## Whose Agent?

**누구의 에이전트인가 — AI 공급자 중심의 통합 환경에서 사용자 주도형 Agent 생태계로**

- [Read the Korean manuscript — v0.3.1](whose-agent.ko.md), a wording-only copy edit of the reviewed v0.3 that was not itself re-reviewed. The [reviewed v0.3 artifact](https://github.com/Jongtae/agentos/blob/27834b2aaa1bfc697c799797946115d4365fb89e/docs/whitepapers/whose-agent.ko.md) stays pinned.
- [Executed review rounds, findings, source audit and final role decisions](review-plan.en.md).
- [Review follow-up — WHITEPAPER-02 #1045](https://github.com/Jongtae/agentos/issues/1045).
- [Original draft preservation — PR #1044](https://github.com/Jongtae/agentos/pull/1044).

The manuscript retains the Jarvis opening, the internet/mobile analogy, portable memory, replaceable intelligence, persistent Presence, human-agent collaboration, skills, verifiable execution, business incentives, counterarguments and an evaluation agenda. Version 0.3 clarifies the conditions for industry-layer separation, model capability gaps, memory-import conflicts, concurrent edits, evidence coverage and key trust, adoption incentives, outcome costs and fair evaluation.

**Review status:** ten sequential role-based self-review passes by the same authoring assistant are complete. Nine passes revised the text; the tenth verified it without further edits. The final editor and final verifier roles approved the same manuscript blob `19e8aa29d38316aa306f15147e4b028ad73bfe9c` for a non-normative strategy discussion. This is **not independent expert, human or separate-agent review**, empirical validation, legal certification or proof of product capability. The detailed record gives the source boundaries and remaining limitations. GitHub PR state and required Checks remain authoritative for merge/CI status.

Korean is the source language of this owner-facing manuscript. This guide and editorial record are in English; no English manuscript translation is claimed. Canonical development guidance remains in [AGENTS.md](../../AGENTS.md), the [Development Constitution](../development-constitution.en.md), and the relevant English product contracts. Nothing here changes `delivery-plan.yaml`, runtime authority, the active program or root README product claims.

The case-study source is the already-public [2026-09-23 Presence and Settings UX research](https://github.com/Jongtae/agentos/blob/435470d4f8a1b7a73ab9bd5e44e61aea6b2d6ef5/docs/research/presence-and-settings-ux.ko.md). Historical observations, industry inferences, hypothetical examples and proposed experiments remain distinguishable. Public references are in the manuscript's bibliography and the editorial source audit.
