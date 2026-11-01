# Release governance and version policy

## Purpose
Separate code integration from public installation. **Merge != release**. This policy defines release decisions; [the release procedure](../release.en.md) remains authoritative for tag, checksum, formula, installer and manifest mechanics. Since #1472 those mechanics run in `.github/workflows/release.yml`, and only after the owner approves the `release` environment; the fail-closed `scripts/dev/delivery.py` helper stays disabled.

## Versioning
Use SemVer for the public interface and compatibility commitments: **patch** for compatible fixes, **minor** for compatible features, **major** for incompatible supported interfaces, configuration or durable-data behavior. Internal refactors do not automatically require major. Pre-releases such as `v1.4.0-rc.1` are opt-in validation candidates, never silently presented as stable. Public contract/compatibility boundaries must be specified before selecting a bump; do not imply unverified support for older versions.

### Public compatibility surface
For a local-first personal OS the compatibility promise is that **upgrading keeps the owner's state and setup working**. The surface is:

- durable owner state: Memory, Grants, approvals, Work/Event/Evidence and Artifacts, and the stores and migrations that hold them;
- configuration and the owner's connections, secret slots and connected folders;
- the `agentos` CLI commands and flags, and the install paths (Homebrew formula, one-line installer);
- AgentPackage and skill manifest schemas;
- supported platforms (macOS, Linux, Windows through WSL2) and Python versions.

Downgrade is not promised. Model behavior, prompt wording and UI text are not part of the surface.

| Bump | When | Examples |
|---|---|---|
| **major** | An upgrade breaks owner state or setup, or needs the owner to act | a store that does not migrate forward automatically; a removed or renamed CLI command or configuration key; an incompatible manifest schema; a dropped platform or Python version; an existing Grant or approval that would mean something different |
| **minor** | State is kept and the owner gets a new or changed capability | a new connector, connection kind or tool exposed to the owner's AI; a bundled skill; a new install path or platform; a schema addition with automatic forward migration; a visible default change such as model routing |
| **patch** | The surface is unchanged | fixes, compatible security fixes, performance, internal refactors, dependency updates without behavior change |
| none | — | docs, governance, site, tests |

The highest bump in the range wins. Conventional Commit types are a first signal (`feat` → minor candidate, `fix`/`perf` → patch candidate, `!` or `BREAKING CHANGE` → major); the Release Decision Brief makes the final call, because a `feat` that the owner never sees is a patch.

The in-tree version after a release is a **placeholder** (the next patch) that only keeps source distinguishable from the shipped build. It does not choose the next version; the release-preparation PR does.

## Scope and branches
A GitHub milestone named `vX.Y.Z` is the scope of that release. Issues move between version milestones as plans change; a version that is skipped is closed with a note, as v1.2.0 was.

Releases are tags on `main` or on a maintenance line. Release-preparation PRs use a short-lived `release/<issue>-vX.Y.Z` branch like any other issue branch.

When a patch must ship while `main` already carries the next minor's work, cut a maintenance line `maint/X.Y` from that minor's newest tag (#533), and only then. Fixes land on `main` first and reach the line as cherry-pick PRs (`git cherry-pick -x`); a fix lands only on the line when the bug no longer exists on `main`. The line publishes only `X.Y.*` patches, through the same workflow and the same owner approval as `main`. The `vX.Y.Z` milestone of a line's patch holds those cherry-pick PRs, so an unmerged one blocks the release; each issue keeps the milestone of the `main` release it ships in. A patch of an older minor published after a newer minor is recorded but never becomes what the formula, the installer or the site install.

## Release cadence and authority
Review readiness approximately monthly, **not** an automatic monthly publish. Security/critical regressions may justify an out-of-band patch. The authorized human release maintainer approves the exact tag and public distribution; an AI may prepare notes, compare risks and recommend a version, but may not independently tag, publish, update Homebrew, deploy, approve a `release` environment deployment or access release credentials. Release roles do not imply repository permissions.

## Gates for stable promotion
1. Identify exact merged revision, included issues/PRs, user-visible changes, supported platforms and proposed SemVer bump. The `vX.Y.Z` milestone has no open issues.
2. Check required CI plus risk-triggered full validation and security/authority negative tests. Cancelled, missing or stale checks are not passing.
3. Verify schema/config/memory compatibility, migration and recovery where changed; document rollback limitations.
4. Execute release procedure's installed-smoke/upgrade checks on the actual published artifact, including Homebrew formula and install script consistency; distinguish CI/mock, installed-smoke and owner-live evidence.
5. Reconcile `docs/release-manifest.json`, release notes, known limitations, product-status and security supported-version statements with actual published state. The manifest entry states `carries` and `not_claimed` before publication.
6. Prepare a Release Decision Brief: version/tag/head, scope, evidence, failures/unknowns, compatibility, known risks, blockers, publish recommendation and human authorization.
7. Explicit authorized approval precedes stable publication: the owner approves the `release` environment deployment of the release workflow. Follow the [release procedure](../release.en.md) ordering; the tag archive checksum exists only after the tag is published, so record post-publication verification and rollback response honestly.

If a blocker remains, postpone stable, issue a bounded RC for further validation where appropriate, or defer the change to a later release. Never label a failed/partial/unknown external check as verified. A release does not require a human to repeat all development tests, but release-specific install/upgrade evidence cannot be replaced by PR CI.

## Contribution and review boundary
All contributors may suggest release notes and identify blockers. The maintainer may delegate preparation but not implicit publish authority. Merge approval and release approval are separate records: merging the release-preparation PR starts the workflow, and the environment approval publishes. Protect release tags and credentials with GitHub rules and least privilege; this policy does not itself enforce repository settings.

## Rollout
Adopted as a documentation contract by #1267; #1472 adds the release workflow, which removes manual bookkeeping without bypassing the owner's approval. Existing `delivery-plan.yaml` selection and paused programs are unchanged.
