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
- outside content sets a durable host-owned Work mark ([#744](https://github.com/Jongtae/Still/issues/744), owner disposition 2026-11-16): every tool result except a named owner-only set (`OWNER_ONLY_RESULT_ACTIONS` in `agent_runtime.py`) sets the mark, including cached/live capability catalogs, calendar/file metadata, skills and peer/specialist reports. Only host-constructed no-content returns are exempt; provider setup/unavailable flags cannot exempt outside text. Errors after dispatch or with unknown dispatch state also mark before delivery; explicit host pre-dispatch refusals do not. Permitted AI-side connector reads, inherited marked history and recalled conversation excerpts from marked Works count, including beyond the recent history window. Unmarked owner-only recall adds no mark. Marked non-payment external writes require exact one-use confirmation unless the owner's current connection write allowance covers them; “reversible” cannot waive it. Below existing deterministic browser floors, one minimized Judgment-AI call permits read/navigation without a question; other or unavailable judgments require confirmation in attended Works; scheduled payment judgments remain refused. Browser has no connection allowance; page-controlled element names can mislead that judgment, while deterministic floors still hold. Worker/single-Work upkeep facts unsupported by current/recent owner words become sourced MemoryCandidates. A literal miss costs one Judgment-AI call using only the bounded owner words and proposed key/value; only yes permits the direct save, and unmarked/literal saves add no call. That judgment can wrongly accept a close paraphrase; assistant answers and tool results never supply its evidence. Dream input excludes marked answers, including earlier misses; owner-turn learning and the quiet search-only diary keep direct saves. This is whole-Work tracking, not causal data-flow proof: history inheritance is transitive, owner quotations pass containment, the owner chose a native-search barrier on [2026-11-17](https://github.com/Jongtae/Still/issues/744#issuecomment-6931823807). Native-enabled CLI turns wait at mark-dependent write/Memory decisions until the stdout reader consumes the matching call start; live search/fetch intake commits first. Reads never wait, and enabling search alone does not mark a Work. Codex connector permission hooks can precede their start notification and therefore use the timeout fallback even without a search. After five seconds without a matching start, a durable timeout fact applies marked-Work rules (exact confirmation or a candidate, with existing allowances/owner-word checks). CLI built-in filesystem reads can still escape host provenance (notably trusted-local Codex); no filesystem isolation is claimed here. A returned code-run result marks the Work; reads/effects within one opaque execution lack result-level tracking;
- Still's own posts and forwards are not the owner typing ([#744](https://github.com/Jongtae/Still/issues/744), [#913](https://github.com/Jongtae/Still/pull/913)): a post Still sends to the paired Mattermost or Slack conversation through a Still-owned API connection is recognized by the post id the platform returns and is never taken as an owner-typed message, and a forwarded Telegram message stays readable but is not owner-typed. The correlation covers host-observed native create-post responses only. Posts made through AI-side MCP connectors or the browser have no host-observed post id and remain uncorrelated; when the platform reports the paired owner's identity, they can be mistaken for owner-typed instructions. Unsupported edit, ephemeral, scheduled and action-message routes, and processes without constructed surface objects, have the same limitation. An owner id alone does not prove human authorship;
- the information-use view compares outbound calls with Memory when it is opened, labelled as a later comparison; typed browser text, request bodies and code-run payloads are not retained and are named as not compared. An enforced check on private values leaving to new destinations, and further isolation, are deferred ([#28](https://github.com/Jongtae/Still/issues/28)).

Reports about these deferred areas are still welcome, and they inform the hardening program. They are not treated as regressions of an enforced guarantee.

The canonical boundaries are in the [Owner Control Contract](../docs/owner-control-contract.en.md), the [Secretary Agency Contract](../docs/secretary-agency-contract.en.md) and [Still Architecture](../docs/personal-agentos-architecture.en.md).
