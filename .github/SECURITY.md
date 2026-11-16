# Security policy

## Reporting a vulnerability

Please report vulnerabilities **privately** through GitHub: open the repository's **Security** tab and choose **Report a vulnerability** ([direct link](https://github.com/Jongtae/Still/security/advisories/new)). Do not open a public issue, pull request or discussion for a suspected vulnerability.

A useful report includes the affected version or commit, what an attacker can do, and the steps to reproduce it. Still is maintained by one person, so there is no guaranteed response time. Reports are acknowledged and handled as quickly as that allows, and fixes are credited in the advisory unless you ask otherwise.

## Supported versions

| Version | Supported |
|---|---|
| `main` | Yes |
| Latest Homebrew release (currently 1.1.x) | Yes |
| Older releases | No. Please upgrade |

## What is in scope

Still runs on the owner's own machine and keeps their state there. These are security bugs:

- a secret (model key, OAuth token, Telegram bot token, saved login) reaching a model prompt, a log, Evidence or an export;
- a payment happening without the owner's per-action approval or the owner's standing payment-autonomy setting, or any payment from a preparation set up without the owner asking;
- another consequential action happening without the approval its action type requires;
- a read outside the folders the owner granted (a broad folder such as home needs the owner's confirmation in Settings on this computer; credential and system locations are always refused), or a write outside the owner's workspace;
- text from a page, a mail or another agent being taken as the owner's instruction;
- someone other than the paired owner controlling the assistant through Telegram or the local web app;
- installing, updating or enabling a package or connector granting authority the owner did not give;
- memory the owner deleted still being returned or exported.

## Current posture (pilot)

The project is in a single-owner pilot: one owner, on their own machine, with their own accounts. By the owner's decision, some protections are removed because they did not protect the owner from anyone (Constitution C17), and some are deliberately deferred to a later hardening program rather than missing by accident. [`AGENTS.md`](../AGENTS.md) (*Secretary agency re-plan*) records these decisions. In particular:

- the owner's machine is the owner's (Constitution C17, [#817](https://github.com/Jongtae/Still/issues/817)): the owner's own notes, memories and answers are not masked on their way to the owner's chosen AI or in the owner's own conversation, and owner-visible text shows real paths and error reasons. Only stored secret values and credential-shaped text are masked; exports and agent-to-agent payloads still mask paths;
- the assistant may use the owner's private material and web search in the same task, read a public page at any public address (loopback, private and link-local destinations are refused, redirects are rechecked, and an address carrying a stored secret or credential-shaped text is refused), and let a configured direct-API model read the owner's documents. Each task instead records which owner information it used and where that information went, including to a remote AI;
- outside content sets a durable host-owned Work mark ([#744](https://github.com/Jongtae/Still/issues/744), owner disposition 2026-11-16): host-mediated results/metadata, peer/specialist inputs, permitted AI-side connector reads and inherited marked history count. Marked non-payment writes require exact one-use confirmation unless the owner's current connection write allowance covers them; “reversible” cannot waive it. Below existing deterministic browser floors, one minimized Judgment-AI call permits read/navigation without a question; other or unavailable judgments require confirmation in attended Works; scheduled payment judgments remain refused. Browser has no connection allowance; page-controlled element names can mislead that judgment, while deterministic floors still hold. Worker/single-Work upkeep facts absent from current/recent owner words become sourced MemoryCandidates. Dream input excludes marked answers, including earlier misses; owner-turn learning and the quiet search-only diary keep direct saves. This is whole-Work tracking, not causal data-flow proof: history inheritance is transitive, owner quotations pass containment, host-invisible CLI built-in reads are not covered, and stream-only observations are retrospective. A returned code-run result marks the Work; reads/effects within one opaque execution lack result-level tracking;
- the information-use view compares outbound calls with Memory when it is opened, labelled as a later comparison; typed browser text, request bodies and code-run payloads are not retained and are named as not compared. An enforced check on private values leaving to new destinations, and further isolation, are deferred ([#28](https://github.com/Jongtae/Still/issues/28)).

Reports about these deferred areas are still welcome, and they inform the hardening program. They are not treated as regressions of an enforced guarantee.

The canonical boundaries are in the [Owner Control Contract](../docs/owner-control-contract.en.md), the [Secretary Agency Contract](../docs/secretary-agency-contract.en.md) and [Still Architecture](../docs/personal-agentos-architecture.en.md).
