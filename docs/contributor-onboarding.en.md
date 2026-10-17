# Contributor onboarding

This guide is for someone who has just cloned the repository. It covers what to read, how to set up, where the code lives and how to start in your role. The rules themselves are in [CONTRIBUTING](../.github/CONTRIBUTING.md), [AGENTS.md](../AGENTS.md), the [Development Constitution](development-constitution.en.md) and the [AI contribution and review contract](governance/ai-contribution-review.en.md). When this guide and those files disagree, those files win.

## 1. Start with your AI assistant

You can use any AI or none. Open your coding assistant in the clone and ask it to onboard you, for example "onboard me as a reviewer". The procedure lives in one file, [`.agents/skills/contributor-onboarding/SKILL.md`](../.agents/skills/contributor-onboarding/SKILL.md), in the Agent Skills format. [AGENTS.md](../AGENTS.md) tells every assistant to follow it, so tools without skill support get the same steps.

| Tool | How it finds the repository instructions | How to start |
| --- | --- | --- |
| Claude Code | `CLAUDE.md` imports `AGENTS.md`; the skill is linked from `.claude/skills/` | `/contributor-onboarding`, optionally followed by your role |
| Codex | Reads `AGENTS.md`; loads skills from `.agents/skills/` | Ask for contributor onboarding |
| Cursor | Reads `AGENTS.md`; loads skills from `.agents/skills/` and `.claude/skills/` | Type `/` in Agent chat and pick `contributor-onboarding` |
| GitHub Copilot | `.github/copilot-instructions.md` points to `AGENTS.md` | Ask for contributor onboarding |
| Gemini CLI | `GEMINI.md` imports `AGENTS.md` | Ask for contributor onboarding |
| Any other assistant | May not read these files on its own | Tell it to read `AGENTS.md` and follow `.agents/skills/contributor-onboarding/SKILL.md` |

The procedure sets up the development environment from [CONTRIBUTING](../.github/CONTRIBUTING.md#setting-up), checks it with one focused test, works out your role from what you asked, and then follows section 5 of this guide. It does not connect a real account, make a live model call or push. If you prefer to do it by hand, follow sections 2 to 6.

## 2. What the product is

Still is a local-first personal assistant for one person. It keeps that person's memory, open work, permissions, files and records on their own machine, and it lets them choose the AI that does the work. Read the [README](../README.md) for the pitch, [VISION](VISION.md) for the motivation and [product status](product-status.en.md) for what is actually observed working today.

## 3. Core terms

These terms appear throughout the code and documents. The full model is in the [architecture and ontology](personal-agentos-architecture.en.md).

- **Owner**: the one person the installation serves. Owner state is the source of truth; no AI tool or package owns it.
- **Context and Memory**: what the assistant knows about the owner. Memory written by a third party is a candidate until it is accepted.
- **Work**: one task the assistant carries out, with its state, approvals and record.
- **Grant**: an explicit permission for a specific action, resource or destination. Installing or connecting something does not create a Grant.
- **Artifact**: a file or result the assistant produces, kept with its provenance.
- **Evidence**: the record of what actually ran, kept separate from what was claimed.
- **Decision Model**: chooses how each request is handled, including which AI worker does it and with what brief, and judges the result.
- **Capability and Runtime**: a tool, connector, package or AI engine that does work. It is a bounded worker and never owns owner state.

## 4. Set up and find your way around the code

Run the setup block in [CONTRIBUTING](../.github/CONTRIBUTING.md#setting-up). It installs the locked dependencies into `.venv` and enables the repository hooks, which block direct commits and pushes to `main`. Then check the setup with one focused test:

```sh
.venv/bin/python -m pytest -q tests/test_owner_model_upkeep.py
```

`.venv/bin/agentos start` runs the app from your checkout. Tests do not need a real AI subscription or account.

Setup notes that come up often:

- Use Python 3.12 or newer. The macOS Command Line Tools Python (3.9) fails on the `cryptography` import; `pip install -e` refuses it outright.
- Use the locked `uv sync` from CONTRIBUTING, not an ad-hoc `pip install`, so your environment matches the lock. The `pip install -e '.[mcp-host]'` command in QUICKSTART is the end-user source install, not the development environment.
- The `mcp-host` extra (`mcp`, `claude-agent-sdk`) is not in the base install. Install it when you touch `mcp_setup.py` or `mcp_bridge.py`.
- In a git worktree, an editable install may point at the main checkout. Run tests with `PYTHONPATH=src` so they import the worktree's own source.
- On Intel Macs, `cryptography` is capped below 49 because no Intel wheel is published from 49.0.0 (#1531).

The Python package is `src/personal_agent`, and the `agentos` command starts at `personal_agent.quickstart:main`. The table groups the main modules; each module's docstring says what it owns.

| Area | Modules (examples) | What they do |
| --- | --- | --- |
| Start and local service | `quickstart`, `quickstart_store`, `service_control` | Launch the local agent and setup page, single-owner persistence, macOS launchd lifecycle |
| Conversation and Presence | `quickstart_service`, `conversation_projection`, `conversation_handoff`, `telegram_presence`, `telegram_locale` | The one owner conversation shared by web and Telegram, and what the owner sees |
| Decision | `orchestrator`, `decision`, `decision_routes`, `decision_adapters`, `decision_qualification` | The Decision Model orchestrating each Work, its selectable routes and qualification suite |
| AI connections | `main_ai`, `subscription_engines`, `providers`, `api_credentials`, `api_oauth` | The owner's chosen AI, subscription CLIs, model adapters and API credentials |
| Tools and skills | `agent_runtime`, `manifests`, `plugins`, `skills`, `capability_search`, `mcp_bridge`, `mcp_setup`, `bundled_skills/` | Tool loop and registry, declared tools, plugins, optional skill content, MCP |
| Owner state | `owner_model`, `memory_service`, `personal_knowledge`, `current_context`, `context_inbox`, `information_use` | Owner model upkeep, Memory and candidates, local retrieval, current context, per-Work information-use audit |
| Files | `file_workspace`, `folder_grants`, `document_reader`, `draft_formats` | Connected folders (read-only references, new result files) and document extraction |
| Connectors | `gmail`, `calendar`, `google_calendar`, `google_drive_read`, `connector_contract`, `connector_http`, `connector_revocation` | Owner-bound accounts, destination containment and disconnect |
| Isolation and code runs | `code_run`, `isolated_engine_gateway`, `isolated_mcp_proxy`, `limited_egress_proxy`, `sandbox/` | Running code and engines with scoped capabilities and default-deny egress |
| Browser | `browser_session`, `browser_worker`, `browser_jar`, `cli_browser_relay` | Browser actions in the owner's signed-in profile |
| Settings and web UI | `settings_orchestrator`, `web/` | Conversation-first settings and the local web page. Read `web/AGENTS.md` before changing `web/`. |

Tests are in `tests/`, mostly named `test_<area>.py`. Shared fixtures are in `tests/conftest.py` and `tests/fixtures/`; fakes such as `browser_fake_worker.py` replace live models and accounts. Tests that open a real macOS WebKit window or Keychain run only with `AGENTOS_REAL_BROWSER_TESTS=1`.

## 5. Start in your role

A role is assigned per task, not per person: you may implement one issue and review another PR on the same day. Duties and limits for every role are in the [role table](governance/ai-contribution-review.en.md#role-assignments-per-pr-or-task). A role changes what you and your AI do; it never grants merge, release or system permission.

| If you want to | Role | Read first | First steps |
| --- | --- | --- | --- |
| Shape a product idea or a change in direction | Proposer | [VISION](VISION.md), [product status](product-status.en.md), [architecture](personal-agentos-architecture.en.md), constitution [C15](development-constitution.en.md#c15-reuse-first-adopt--adapt--build) and [C16](development-constitution.en.md#c16-ai-is-the-engine-agentos-orchestrates-it-does-not-implement-requests) | Search existing issues and contracts, run the Existing Solutions Review, then open an issue with the **Idea** template. Product direction is decided by the maintainer. |
| Fix a bug or build an agreed change | Implementer | [CONTRIBUTING](../.github/CONTRIBUTING.md), the contract for the area you touch (find it in the [documentation map](README.md)) | Claim or open the issue, create the branch, make the smallest general change with focused tests, open a PR with the [PR template](../.github/pull_request_template.md). |
| Review someone else's pull request | Independent reviewer | [Review protocol](governance/ai-contribution-review.en.md#review-protocol), the contracts the PR touches | Review the exact head, report findings with severity and evidence, end with a Merge Decision Brief. Review of your own PR, even with another model, is self-review. |
| Prepare a release (maintainers) | Release preparation assistant | [Release policy](governance/release-policy.en.md), [release procedure](release.en.md) | Assemble scope and verification evidence. No tag, tap, deploy or publish without the maintainer. |

Which work is scheduled is decided by the maintainer in [`delivery-plan.yaml`](../delivery-plan.yaml), so an open issue is not automatically in progress. Ask on the issue before starting anything large.

## 6. How a change lands

1. **Issue first.** Describe the outcome, the change, the acceptance criteria and what is out of scope. Use the [issue templates](../.github/ISSUE_TEMPLATE).
2. **Branch named after the issue**: `<type>/<issue>-<slug>` with a Conventional Commits type, for example `fix/123-empty-reply`.
3. **Small commits, then a pull request** that fills in the template and names the issue it closes. The required `validate` check must pass.

The rules that shape every change, such as reuse first, AI as the engine, truthful evidence and keeping the four public READMEs in step, are in [CONTRIBUTING](../.github/CONTRIBUTING.md#rules-that-shape-every-change).

## 7. Finding the rest

Many files in `docs/` record a specific goal, some of them closed or paused. Start from the [documentation map](README.md), which lists the current contracts and guides, and find the contract for the area you are changing there.
