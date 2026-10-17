---
name: contributor-onboarding
description: Onboard a new Still contributor in this repository. Use when someone has just cloned the repo, asks to get started, set up the development environment, or work as a proposer, implementer, reviewer or release preparer. Sets up the dev environment, checks it with one test, and starts the contributor in their role.
---

# Contributor onboarding

This skill is a thin adapter. The procedure and roles are defined in `docs/contributor-onboarding.en.md`; the rules are in `AGENTS.md`, `.github/CONTRIBUTING.md`, `docs/development-constitution.en.md` and `docs/governance/ai-contribution-review.en.md`. Read them from the repository rather than relying on this file, and follow them when they differ from it.

Talk to the contributor in their language. Report what you ran and what you observed; do not claim a step passed unless you saw it pass.

## 1. Set up

1. Read `.github/CONTRIBUTING.md`, section "Setting up". If `.venv/bin/python` does not exist, run that block's commands exactly as written from the repository root. If it exists, run only `.tools/uv/bin/uv lock --check`, and set the hooks path from that block only if `core.hooksPath` is unset.
2. Confirm `git config core.hooksPath` points to the repository's `.githooks` directory, either as `.githooks` or as its absolute path.
3. Run `.venv/bin/python -m pytest -q tests/test_owner_model_upkeep.py` and report the result. If anything fails, show the relevant error and stop setup there.

Do not change global git config, connect a real account, add credentials, run real-browser tests, make live model calls or push. Those need the contributor's explicit request and, for accounts or credentials, their own action.

## 2. Work out the role

Read section 5 of `docs/contributor-onboarding.en.md`. Infer the role from what the contributor asked: reviewing a PR means the reviewer role, fixing or building an issue means implementer, shaping an idea means proposer. Ask one short question only if the request does not say. A role changes what you do; it never grants merge, release or system permission.

## 3. Start in the role

Follow the "Read first" and "First steps" for that role in section 5. Summarise in a few lines what you read that matters for this contributor's task, then propose the first concrete step:

- **Proposer**: existing issues and contracts that already cover the idea, then an issue draft with an Existing Solutions Review. Do not open the issue without the contributor's confirmation.
- **Implementer**: the issue to work on, the branch name `<type>/<issue>-<slug>`, the contract for the area and the focused tests to run.
- **Independent reviewer**: the PR's exact head SHA and the contracts it touches, then the review protocol. If the contributor authored the PR, say this is self-review.
- **Release preparation**: the release policy and procedure; nothing is tagged or published without the maintainer.

Selecting work from `delivery-plan.yaml` on your own is not part of onboarding. Work starts from the contributor's request.
