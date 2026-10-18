<div align="center">

<pre>
   ________________   __ 
  / __/_  __/  _/ /  / / 
 _\ \  / / _/ // /__/ /__
/___/ /_/ /___/____/____/
</pre>

**Your personal agent, on your own machine.<br>Change the AI underneath; keep its memory, open work and permissions.**

[![CI](https://github.com/Jongtae/Still/actions/workflows/validate.yml/badge.svg?branch=main)](https://github.com/Jongtae/Still/actions/workflows/validate.yml) [![Full test suite](https://github.com/Jongtae/Still/actions/workflows/full-validate.yml/badge.svg?branch=main)](https://github.com/Jongtae/Still/actions/workflows/full-validate.yml) [![Latest release](https://img.shields.io/github/v/release/Jongtae/Still?sort=semver&label=release)](https://github.com/Jongtae/Still/releases) [![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)](pyproject.toml) [![macOS | Linux | WSL2 limited](https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20WSL2%20limited-555)](docs/QUICKSTART.md) [![License: AGPL 3.0](https://img.shields.io/badge/license-AGPL--3.0--only-blue)](LICENSE)

[English](README.md) | [한국어](docs/i18n/README.ko.md) | [简体中文](docs/i18n/README.zh-CN.md) | [日本語](docs/i18n/README.ja.md)

[Quick start](#quick-start) · [What stays yours](#what-stays-yours) · [How it works](#how-it-works) · [Docs](#documentation)

</div>

<!-- readme-parity:v1 -->
<!-- readme-section:hero -->

## Can we have our own J.A.R.V.I.S. without owning its brain?

**The brain can be borrowed. The assistant should remain yours.**

Tony Stark had J.A.R.V.I.S.: an assistant that understood his context, stayed available and acted through his computers and systems. But Tony also owned the technology behind it. Most of us won't own the most capable AI brains we use. They come from providers whose models, prices, policies and availability can change.

So the question isn't just *can we build our own Jarvis?* It's **can we keep a personal assistant that is truly ours while borrowing its intelligence?** If a better model appears, should we have to give up what our assistant knows, the work we've left unfinished or the authority we've delegated?

Still explores an answer: *no*. It is an open-source environment you install and control. One personal agent (PA) keeps your context, open work and permissions there, while the AI doing the work can change.

<p align="center">
  <img src="docs/assets/readme/demo/demo-v5.en.gif" width="800" alt="Real recorded conversations: remembered coffee taste, F1 model research continued on Amazon, a bean recommendation and post-login cart confirmation.">
</p>
<p align="center"><sub>Edited from real recordings.</sub></p>

**Highlights**

- **Bring the AI you already subscribe to.** Your ChatGPT plan through Codex, or your Claude plan through Claude Code, does the work. Other connections are listed in [QUICKSTART](docs/QUICKSTART.md#other-model-connections).
- **One PA across conversations.** Memory, saved results and open-work state persist across conversations and restarts. Interrupted execution is reported rather than silently replayed.
- **Your boundaries.** Access begins with the folders, accounts and tools you connect. Supported payment flows require per-action approval; [current browser limitations](https://github.com/Jongtae/Still/issues/26) are documented. [Secrets](.github/SECURITY.md) are excluded from model prompts, logs and Evidence.
- **Told, not hidden.** When your AI saves something to memory it says so and offers an exact undo. Each task records which of your information it used and where it went.
- **Where you already talk.** Chat on Telegram from your phone, or in the browser on macOS or Linux. Windows works through [WSL2 with current limitations](docs/QUICKSTART.md#windows-wsl2).

<!-- readme-section:principles -->

## Five pillars of Still

These are not features of one particular AI model. They are the criteria by which we judge the assistant we are building.

- **Presence:** An assistant that stays with me where I already communicate, instead of making me open a separate app each time I need something done.
- **Portable Memory:** My memories and context are not locked into a single AI service.
- **Model Independence:** I can replace the AI model without losing the continuity of my assistant and our relationship.
- **Execution:** It doesn't only answer questions; it can act in real services and on computers.
- **Owner Control:** I retain control over my data, permissions and the outcomes of execution.

These are guiding principles, not a claim that every scenario already works. The [product status](docs/product-status.en.md), real demo above and [known browser limitations](https://github.com/Jongtae/Still/issues/26) show what has been observed and what still needs work.

<!-- readme-section:try-today -->

## Quick start

**Before you start, have ready:**

- A Mac, a Linux computer or Windows with WSL2, and an internet connection.
- One AI to do the work: a ChatGPT plan with the [Codex CLI](https://github.com/openai/codex), a Claude plan with [Claude Code](https://code.claude.com/docs/en/setup), or an API key (OpenAI, Anthropic, an OpenAI-compatible server) or [Ollama](https://ollama.com) on this computer. If the CLI is not installed yet, first setup opens its install page.
- Optional: [Telegram](https://telegram.org) on your phone or computer, to chat with Still through a bot you create with @BotFather.

On macOS, Linux or Windows (through WSL2), one command installs and starts Still. You do not need Homebrew, Python or Docker:

```sh
curl -LsSf https://raw.githubusercontent.com/Jongtae/Still/8c88eb1d96533ffd25351b6d54b4eac0f78f66cd/scripts/install.sh | sh
```

[Inspect the script](https://github.com/Jongtae/Still/blob/8c88eb1d96533ffd25351b6d54b4eac0f78f66cd/scripts/install.sh) before running it; it verifies the published Still archive and the uv installer it downloads. Each release pins this URL to the commit that installs that release.

On Windows, run `wsl --install` in PowerShell, restart, open Ubuntu and run the first command there. Windows details, what the installer does, running from source and every setting are in [QUICKSTART](docs/QUICKSTART.md). Keep `agentos start` running while you talk.

If you use [Homebrew](https://brew.sh) on macOS:

```sh
brew tap jongtae/still https://github.com/Jongtae/Still
brew install jongtae/still/agentos
agentos start
```

1. Setup runs in this terminal, like Claude Code's first start: a password, your AI (Codex, Claude Code or an API key) and a Telegram bot token, each skippable.
2. [http://127.0.0.1:8787](http://127.0.0.1:8787/) then opens for a final check. The screens are in English by default; switch to Korean, Simplified Chinese or Japanese at the bottom left. Some fixed Telegram status/progress lines and certain connection validation errors are still Korean. [Other connections](docs/QUICKSTART.md#other-model-connections).
3. Telegram: paste the @BotFather token in the terminal, open the pairing link it prints and press Start.
4. Ask for what you need: **“My cat has been off her food lately. What should I try?”** A few days later, just ask **“Find me a robot vacuum under $500.”** Your PA remembers the cat and takes it into account without being reminded.

**Release status:** both installers install **v0.1.0** (2026-10-18), built from the tagged main commit. The demo and the everyday-use image come from real sessions and are not release-specific end-to-end validation; the architecture figure is product direction. The [release manifest](docs/release-manifest.json) records what each release covers, and [product status](docs/product-status.en.md) separates shipped behavior, test evidence and direction.

**Uninstall:** `agentos uninstall` removes the background services and the program, and keeps your data unless you choose to delete it ([details](docs/QUICKSTART.md#uninstall)).

<!-- readme-section:ownership -->

## What stays yours

| Can change | Stays with you |
| --- | --- |
| The model and its provider | Memory and context about you |
| The CLI agent or API doing the work | Open work and its next step |
| Tools and connectors | Permissions and approvals |
| | The record of what actually ran |

When a better AI appears, you switch the worker, not the agent. [Whose agent?](docs/whitepapers/whose-agent.ko.md) (Korean whitepaper) takes the question further.

Local-first is not local-only: your subscription AI runs at OpenAI or Anthropic, so the context used for a request is sent to that provider.

<!-- readme-section:presence -->

## How it works

<picture>
  <source media="(max-width: 600px)" srcset="docs/assets/readme/presence-overview.en.narrow.svg">
  <img src="docs/assets/readme/presence-overview.en.svg" alt="Concept diagram: the owner relationship (goals, memory, preferences, commitments, authority) informs Assistant, Finance and Research professional lenses; domain, role, mandate and authority feed the Decision Model; Unified Presence keeps one relationship, and your interventions during the work feed back into decisions. Illustrative product direction, not shipped implementation.">
</picture>

- **Owner relationship** holds your goals, memory, preferences, commitments and authority under your control.
- **Professional lenses** read the same situation through the relevant domain and role. A mandate sets goals and limits; enforced authority stays separate.
- **The Decision Model** judges whether to reason, act, delegate, ask, report or follow up; it does more than route tools.
- **Unified Presence** is not a role: it keeps one relationship across all of them, so meaningful updates reach you and your corrections change the work in progress, as far as the runtime supports.

A role is not a licence or a permission to act, and several roles working together is product direction, not shipped behavior ([contract](docs/professional-roles-and-communication-contract.en.md)). The figure shows the intended design, not an observed run. Details: [architecture and ontology](docs/personal-agentos-architecture.en.md) · [Presence](docs/presence-experience-contract.en.md).

<!-- readme-section:ai-switch -->

## A conversation that continues when the AI changes

<p align="center">
  <img src="docs/assets/readme/ai-switch/ai-switch-conversation.png" alt="Three cropped Telegram screenshots in Korean. 1: after switching to Claude Code, the owner asks whether the Spam was added or taken out yesterday; the PA recalls the earlier cart conversation and asks to sign in. 2: after signing in, it reports one CJ Spam Classic 200 g in the cart and says some steps could not be checked. 3: after switching to Codex, the owner says to take the Spam out; the PA reports it removed and 22 items remaining.">
</p>
<p align="center"><sub>Excerpts from a real conversation (Telegram, Korean UI). The sign-in link is cropped out and the name is blurred. · <a href="docs/assets/readme/ai-switch/ai-switch-conversation.png">Open full size</a></sub></p>

① **Switch to Claude Code.** Asked “Did I end up adding the Spam yesterday, or taking it out?”, the PA picks up the earlier shopping conversation.

② **Check the current cart.** After signing in again, it answers that one 200 g can of Spam Classic is in the cart.

③ **Switch to Codex and ask for the next step.** “Take the Spam out.” In the same conversation it reports the removal and the 22 items still in the cart.

<!-- readme-section:conversation -->

## Everyday use

*Excerpts from real Telegram sessions (Korean interface), arranged with short scene notes.*

<picture>
  <source media="(max-width: 600px)" srcset="docs/assets/readme/readme-examples-v4.en.png">
  <img src="docs/assets/readme/readme-examples-v4.en.png" alt="Real screen excerpts: remembered taste, an F1 search continued on Amazon, and Jardin Espresso Blend 500 g, quantity 1, confirmed in the cart.">
</picture>

A latte you mentioned earlier comes back when you are choosing beans for the coffee machine. An F1 model search continues on Amazon without being restated, with a recommendation and the checks it could not make. Once you pick the beans, the signed-in cart shows them, quantity 1. **What you say or show → context you allowed, with source and time → a useful question or tool → the boundary for action → an observed result.** Adding to a cart and paying stay different actions.

**Next:** following what “it” and “the same one” mean across hours, so you keep talking instead of assembling a workflow.

> “We’re almost out of coffee.” · *hours later* · “Anywhere on my way out?” · *later* · “Just get the same one as last time.”

<!-- readme-section:more -->

## Documentation

| Document | What's inside |
| --- | --- |
| [QUICKSTART](docs/QUICKSTART.md) | Install, connect a model, files, Telegram, run from source |
| [Product status](docs/product-status.en.md) | What works, what still has friction, and the evidence for each |
| [Why this exists](docs/VISION.md) | The motivation behind the project |
| [Architecture and ontology](docs/personal-agentos-architecture.en.md) | Kernel primitives, packages, runtimes and owner control |
| [Documentation map](docs/README.md) | Every current contract and guide |
| [Acknowledgements](docs/acknowledgements.en.md) | Research and projects that shaped the design |

**Contributing.** Issues and pull requests are welcome. Start with [CONTRIBUTING](.github/CONTRIBUTING.md); [AGENTS.md](AGENTS.md) describes the development workflow. New to the code? The [contributor onboarding guide](docs/contributor-onboarding.en.md) maps it and suggests a first change. Tried it? Tell us what worked and what didn't through the [feedback form](https://github.com/Jongtae/Still/issues/new?template=feedback.yml) or [Discussions](https://github.com/Jongtae/Still/discussions).

<!-- readme-section:license -->

## License

[AGPL-3.0-only](LICENSE). The Still name and logo follow the [trademark notice](docs/TRADEMARKS.md).
