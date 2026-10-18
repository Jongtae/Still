# Release procedure

Canonical English source. This writes down a procedure that previously existed
only as repository-only code in `scripts/dev/delivery.py`, where
`_run_release` returns unconditionally at its first statement, so the body
below it never executes. That guard stays; this document is not a request to
re-enable it, and `tests/test_delivery.py` continues to assert the helper fails
closed with `blocked-manual-governance-execution-required`.

## What the published artifact actually is

A Still release is a **GitHub tag source archive** of `Jongtae/Still`, not a
wheel. Both install paths consume that archive:

- `scripts/install.sh` pins its version and SHA-256;
- the Homebrew formula `Formula/agentos.rb` in this repository points at it:

```ruby
url "https://github.com/Jongtae/Still/archive/refs/tags/vX.Y.Z.tar.gz"
sha256 "<64 hex>"
```

There is no separate tap project (#45). This repository is its own tap:
`brew tap jongtae/still https://github.com/Jongtae/Still`, then
`brew install jongtae/still/agentos`. The formula keeps the `agentos` name,
command and data directory for compatibility with existing installs.
`deploy/homebrew/agentos.rb.template` is its render source: the release
workflow writes `Formula/agentos.rb` from it and refuses a formula whose body
drifted from it.

The predecessor's releases (`v1.0.4`–`v1.4.1`, from the private
`Jongtae/agentos` and its `Jongtae/homebrew-agentos` tap) are not Still
releases. Their manifest is preserved unchanged in
`docs/history/predecessor-release-manifest.json`.

## Before the first release

Still restarted its version at `0.1.0`, and `v0.1.0` is its first release.
Until it is published, `docs/release-manifest.json` has no `published` entry,
`scripts/install.sh` has empty release pins and stops without downloading
anything, `Formula/agentos.rb` does not exist, and the README and product-status
pages carry a "no Still release is published yet" sentence. The README installer
URL follows `main`, because there is no release commit to pin it to.
`scripts/stamp_site_release.py` refuses to stamp the site in this state, so
`deploy-pages` cannot publish a site that names a version. The first `record`
replaces each "no release yet" sentence with its released form, and the
following `pin` pins the README installer to a commit.

## Ordering, and why the checksum cannot be precomputed

The archive the `sha256` describes **does not exist until the tag is pushed**.
GitHub generates it on demand, and its gzip output is not guaranteed to be
byte-identical to a local `git archive`. So the checksum is irreducibly
post-publication. Any formula committed before that point must carry the
`__SHA256__` placeholder; a fabricated hex string would produce a formula that
looks reviewable and installs nothing.

## Steps

Steps 1–4 are reversible and form one release-preparation PR. Steps 5 onward
publish. Since #1472 `.github/workflows/release.yml` performs steps 5–7, 9 and
10 after the owner approves its `release` environment; step 8 stays with the
owner. The numbered steps below are what the workflow does and the manual
fallback when it cannot.

1. **Decide the version.** Close or move the open issues of the `vX.Y.Z`
   milestone, choose the bump with the
   [version policy](governance/release-policy.en.md#versioning), and set
   `version` in `pyproject.toml` (and `uv lock`). It must differ from the
   newest tag; `tests/test_release_traceability.py` fails if they converge,
   because an in-tree version equal to the last release makes source
   indistinguishable from the shipped build.
2. **Record what the release carries.** Replace the placeholder `unpublished`
   entry in `docs/release-manifest.json` with the release's own: `carries`
   (the journeys/issues in it) and `not_claimed` (what was not observed, such
   as a live external effect). The workflow refuses an entry without both.
3. **Run the full suite and the verifier gates** on the exact commit:
   `pytest tests`, `python3 -m unittest discover -s tests -t tests`,
   `scripts/verify_master_plan_docs.py`, and `scripts/verify_src_layout.py`.
4. **Merge to `main`** through the normal issue/PR/CI path from a
   `release/<issue>-vX.Y.Z` branch, with the Release Decision Brief in the PR.
   That merge starts the release workflow, which checks the branch name, the
   manifest entry, that the tag does not exist and that the milestone has no
   open issue, then waits for the owner.

--- everything below publishes ---

The owner approves the `release` environment deployment in the Actions run.
The `release` environment admits `main` only, requires the owner as reviewer
and holds `RELEASE_TOKEN`, a fine-grained token limited to contents and pull
requests on this repository; the workflow stops
before any change if that secret is missing, and before the tag until
`validate` has passed on the release commit itself. Code that runs with the
credential comes from the approved release commit, never from a newer `main`.
Every workflow step is safe to re-run after a partial failure; a re-run
accepts an existing PR only when it is merged or still open.

5. **Tag and push** `vX.Y.Z` at the merge commit, and create the GitHub release.
   The workflow uses `gh release create --target <merge commit>` with notes
   from the manifest entry (`scripts/dev/release.py notes`) followed by
   GitHub's generated notes.
6. **Compute the archive checksum** of
   `https://github.com/Jongtae/Still/archive/refs/tags/vX.Y.Z.tar.gz`.
   The workflow downloads it twice and fails if the two digests differ.
7. **Write the formula.** Only after the archive exists, render
   `Formula/agentos.rb` from `deploy/homebrew/agentos.rb.template` with the
   tag and `sha256` (`scripts/dev/release.py formula`). An existing formula
   must match the template in every line except `url` and `sha256`, or the
   workflow refuses it. In particular, `pip install` must include the
   `[mcp-host]` extra and the formula test must import `mcp.server.stdio`;
   a plain `pip install buildpath` leaves the CLI bridge dependency absent.
   The workflow opens the formula PR in this repository, waits for its checks,
   including the `install-smoke` macOS `brew install`/`brew test` of that exact
   formula, and merges only when they pass.

   Change the formula only in the template, never in `Formula/agentos.rb`.
8. **Verify the install**: `brew update`, `brew upgrade jongtae/still/agentos`,
   `brew test jongtae/still/agentos` (which also checks the bridge import), then
   `python3 scripts/quickstart_install_check.py`. This is the owner's step;
   the workflow records `observed` as "not yet recorded" until a follow-up
   change adds what the owner saw.

9. **Record the published release.** Add the computed `sha256`, the tag and
   the version to the `published` list in `docs/release-manifest.json`, and
   remove the matching `unpublished` entry. In the same change, set
   `AGENTOS_VERSION` and `AGENTOS_ARCHIVE_SHA256` in `scripts/install.sh` to
   that release; `tests/test_install_script.py` fails until they match, and
   the `install-smoke` workflow then runs the real installer on Linux and macOS.
   The workflow also moves the release-status sentences in the four READMEs
   and `docs/product-status.*.md` to the new version
   (`scripts/dev/release.py record`), and refuses to guess when one was
   reworded.
10. **Bump `pyproject.toml` to the next version immediately.** Steps 9 and 10
    must land together. `test_the_in_tree_version_is_not_a_published_version`
    fails the moment a version appears in `published` while `pyproject.toml`
    still names it, so recording the release without bumping turns `main` red
    — which is the intended signal that source and shipped build have become
    indistinguishable, not a test defect. The workflow opens steps 9–10 as one
    PR and merges it once its checks pass, then opens a second PR, merged the
    same way, that pins the README one-line installer to that merged commit
    (`scripts/dev/release.py pin`), because a commit cannot name itself. Like
    the formula PR, neither relies on GitHub auto-merge, which needs branch
    protection on `main`, and a failing check stops the workflow before merge.

After the workflow finishes, the site still shows the previous version until
the owner runs `deploy-pages`, which stamps the new pin and version into
`site/`; that run is the owner's promotion decision. RC tags
(`vX.Y.Z-rc.N`) are not handled by the workflow: create them by hand with
`gh release create --prerelease` and do not record them as published.

## Evidence classes

Steps 1–4 produce **contract/static** evidence. Step 8 is the only step that
produces **installed-smoke** evidence, and a real login service surviving a
machine restart plus a Telegram result with no terminal open remain
**owner live** — `scripts/quickstart_install_check.py` returns
`"background_service": "owner_validation_pending"` by construction and does not
install or mutate a launchd service.

Do not describe a release as verified on the strength of steps 1–4.

## Release decision policy

Before following the publication steps above, consult [Release Governance and Version Policy](governance/release-policy.en.md). Merging ordinary code does not trigger a release; merging a release-preparation PR starts a workflow that publishes nothing until the owner approves it. AI may prepare the release brief but may not authorize publication or approve the `release` environment. The fail-closed `scripts/dev/delivery.py` helper stays disabled; archive-checksum ordering, Homebrew formula update, install smoke and manifest evidence rules are unchanged.
