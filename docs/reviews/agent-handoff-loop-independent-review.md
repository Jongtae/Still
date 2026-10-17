# Independent review: state handoff loop (#321)

## Scope and evidence

Reviewed the uncommitted #321 changes in `src/personal_agent/handoff.py`,
`src/personal_agent/delivery.py`, `tests/test_handoff.py`, and
`docs/agent-handoff-loop.en.md`. This review is limited to the current source
and its fixtures. It makes no claim that a GitHub credential, Codex heartbeat,
launchd registration, schedule, or production worker has been inspected or
operated.

## Findings

### High — label transitions are not concurrency-safe (unresolved)

`GithubCliBoundary.transition()` first reads an issue and then runs `gh issue
edit` in a separate command. There is no GitHub conditional update or
compare-and-swap between those calls. A second actor can replace the state
label after the read and before the edit. The later edit can then add its new
label despite the stale precondition (and can leave multiple `agent:*` labels
depending on GitHub label-edit semantics). The process-local `flock` protects
only ticks sharing one local state file; it does not protect another host,
another state path, or a human/API edit. `test_active_writer_and_lock_prevent_duplicate_execution` covers the local lock and a fake active writer only, not this GitHub boundary race.

### High — the exposed CLI can strand an eligible issue in `agent:working` (unresolved)

`DeliveryController.handoff_tick()` constructs `StateHandoffLoop` without an
executor or reviewer. For an eligible implementer tick, `_implement()` claims
the issue (`ready`/`rework` to `working`) before it discovers that
`self.executor is None`, then returns `dispatch-required`. No configured
worker is passed by this CLI path, and `GithubCliBoundary.execution_active()`
always returns `False`. Thus a repeated CLI/heartbeat tick does not perform
the implementation but leaves a local lease and a GitHub `working` label. The
unit tests only inject an executor directly into `StateHandoffLoop`; they do
not exercise `delivery handoff --role implementer` with its production
construction.

### High — receipt timeout recovery does not cover the production transport (unresolved)

`_flush()` recovers only `TimeoutError` from `github.comment()`. The real
adapter calls `subprocess.run(..., timeout=60)` directly in `_run`; a command
timeout raises `subprocess.TimeoutExpired`, which escapes rather than being
converted to `TimeoutError` or reconciled by reading the marker. A network/API
timeout on the actual CLI path therefore lacks the documented read-back
behavior. The passing timeout test uses `FakeGithub.comment()` which appends
the receipt and explicitly raises `TimeoutError`; it does not model the real
subprocess exception or an ambiguous GitHub write.

### Medium — candidate disappearance can crash review rather than safely defer it (unresolved)

The first reviewability check handles a missing current candidate. The
post-review stale-candidate check does not: `self.github.candidate(issue.number).key()` dereferences `None` if the receipt/candidate becomes unavailable after the reviewer returns. This should be treated as stale/not-reviewable, without publishing an approval or crashing the tick. `test_stale_candidate_rejects_approval` changes one valid candidate to another; it does not cover disappearance.

### Medium — authority and dependency admission is weaker than the documented contract (unresolved)

`GithubCliBoundary.issues()` treats the presence of the two body headings
`## Executable goal` and `## Allowed authority` as both authorization and
dependency satisfaction. It does not parse the declared authority, non-goals,
dependency records, delivery-plan activation, or an explicit owner decision.
Consequently any open issue containing those literal headings and one queue
label can be selected, even if its contents do not grant the work the worker
performs. The fixtures set `authorized` and `dependencies_satisfied` together
and do not test the production parser against insufficient, contradictory, or
out-of-plan goal records. This is an authority-widening risk.

### Medium — scheduler-state statement is unsupported by this change's code/tests (unresolved)

The handoff document says that on 2026-09-10 Codex heartbeats were paused and
an existing launchd registration was present but not running. No inspected
source or test queries Codex heartbeat state or `launchctl` state, and the
new code does not integrate a handoff role into an existing heartbeat. The
statement must be backed by separately recorded operational evidence, or be
rephrased as an unverified operational action. The code does correctly reject
`install_schedule()` and the existing delivery test verifies that it raises;
that is not evidence of any actual schedule's existence or status.

## Test coverage executed

`PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_handoff.py' -v`

Result: 6 tests passed. They cover an in-memory end-to-end label flow,
rework priority, non-reviewable pending CI, fake-comment `TimeoutError`
idempotency, a same-process lock/active-writer fixture, and a changed-head
stale candidate.

## Test limitations

No test ran the new delivery CLI construction, a real `gh` subprocess, a
GitHub label race, cross-host/cross-state-file concurrency, a subprocess
timeout, a candidate disappearing during review, real required-check
semantics, heartbeat discovery, or schedule/launchd observation. Accordingly,
the passing fixture suite is evidence for the in-memory loop only and does not
validate those external or operational boundaries.

## Final re-review addendum

This addendum reviews the later uncommitted revision of the same files and
supersedes the resolution status of the findings above. The reviewed tests
were run locally; no GitHub, heartbeat, scheduler, launchd, or production
worker was operated or inferred.

### Finding status

| Earlier finding | Current status | Evidence and remaining boundary |
| --- | --- | --- |
| Label-transition concurrency | **Resolved for the tested cooperative-writer path; recovery limitation remains (Medium)** | The claim prefix is correct: `agentos-handoff:claim:<issue>:<attempt>:...` splits into `agentos-handoff`, `claim`, issue, and attempt, so `split(":")[:4]` includes both issue and `ready`/`rework`. The end-to-end fixture now explicitly asserts both `:47:ready:` and `:47:rework:` claim markers and passes, proving that a rework claim is not blocked by the earlier ready claim in the fake boundary. A durable claim and post-edit confirmation also prevent a cooperating loser from running its executor. However, a worker that dies after publishing the winning claim but before transition leaves an unexpired, durable claim with no recovery/expiry protocol; later cooperative workers will return `claim-raced`. GitHub label edits are still not atomic against non-cooperative actors, although post-edit confirmation detects a conflicting final label set before execution. |
| No-executor CLI claim | **Resolved in source, fixture, and documentation** | `_implement()` now returns `dispatch-required` before `_claim()` when no executor is supplied. `test_dispatch_only_tick_never_claims_or_strands_ready_issue` confirms no label transition in the loop's dispatch-only construction, and the handoff document now says the result does not change the eligible issue label. |
| Receipt timeout | **Resolved in source; adapter path not directly tested** | `GithubCliBoundary._run()` now converts `subprocess.TimeoutExpired` to `TimeoutError`, which reaches `_flush()`'s read-back path. The idempotency fixture passes, but it still raises `TimeoutError` from `FakeGithub` rather than exercising `_run()` with a timed-out subprocess or an ambiguous remote write. |
| Disappeared candidate | **Resolved in source and fixture** | The post-review check now tests `not latest` before calling `latest.key()`. `test_disappearing_candidate_rejects_approval_without_crash` passes and retains `agent:review`. |
| Authority/dependency admission | **Partially resolved (Medium)** | The adapter now requires both executable-goal headings and explicit owner/dependency HTML markers before setting `authorized` and `dependencies_satisfied`. This closes the earlier heading-only admission. The markers are still free-form issue-body strings: the code does not validate their issuer, the actual declared authority/non-goals, or the matching active delivery-plan dependency. Tests instantiate `Issue` directly and do not verify the production `issues()` parser against missing or contradictory records. |
| Scheduler claims | **Resolved in documentation scope** | The document no longer asserts an observed heartbeat or launchd state, and requires an operator to inspect actual state before recurrence. Existing code/tests continue to establish only that `install_schedule()` rejects a new delivery schedule; they do not establish any schedule's presence or condition. |

### Tests executed for this re-review

* `PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_handoff.py' -v` — 9 passed.
* `PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_delivery.py' -v` — 14 passed.
* `git diff --check` — passed.

### Final disposition

The alleged High per-attempt prefix defect was a review-counting error and is
withdrawn. The corrected explicit rework-marker assertions are sufficient to
cover that specific regression in the in-memory boundary. The revised source
has no unresolved High finding from this review. The remaining Medium limits
are claim-crash recovery, free-form authority/dependency markers, and the lack
of real GitHub/heartbeat/scheduler integration evidence. They must be recorded
as limitations, not represented as operating proof.

## Current-head PR #322 re-review (`efa6403`)

This review examined the current PR head `efa64036423092fd7b61f2fb408f4ed6c8b5f5f2`
against `origin/main`, the owner review's R1–R6 criteria, and the current
test suite. It is source/fixture and GitHub-PR-metadata evidence only; it
does not claim a live worker, heartbeat, schedule, or production GitHub write
was operated.

### Validation observed

* Focused: `PYTHONPATH=src python3 -m pytest -q tests/test_handoff.py tests/test_delivery.py` — **30 passed**.
* Full pytest: `PYTHONPATH=src python3 -m pytest -q tests` — **280 passed, 46 subtests passed**.
* Full unittest: `PYTHONPATH=src python3 -m unittest discover -s tests -q` — **251 passed**.
* `python3 scripts/verify_master_plan_docs.py` and `git diff --check` — passed.
* GitHub PR #322's required `validate` check is successful for `efa6403` at
  review time.

### R1–R4 disposition

R1 is covered by retained-lease candidate refresh and the pending-CI fixture;
R2 queries `gh pr checks --required` and fails pending/unknown results closed;
R3 rechecks the candidate before replaying a pending review disposition and
reconciles a lost transition response; R4 binds claim cycles and review
receipts to immutable candidate identity. The corresponding focused tests
pass. These fixes address the cited fixture paths, subject to the external
boundary limitations already stated above.

### R5 — active-goal authority is still widened (**High, unresolved**)

`DeliveryController.handoff_tick()` builds `authorized_goals` from **every**
delivery-plan entry whose `activation_status` is
`owner-activated-goal-ready`. It does not restrict the map to the current
`next_goal`, its explicitly active top-level program/substep, or the selected
goal returned by `DeliveryPlan.select()`. In the current plan this admits,
among others, historical GOV/TOP/SCN/Drive entries and future
`DRIVE-LOCAL-OP-01` alongside the active `FILE-WS-A-01`. If any of those open
issues receives an eligible queue label, `GithubCliBoundary.issues()` marks it
authorized and `StateHandoffLoop` can select it. This violates the stated
single explicitly active-goal boundary. The current entrypoint fixture checks
only that issue 318 is included; it does not assert that non-active
goal-ready issues are excluded.

### R6 — production independent-role execution is still unconfigured (**High, unresolved**)

The new `handoff_workers` and `handoff_github_factory` are constructor-only
test seams. `main()` creates `DeliveryController` without either argument, so
the actual `delivery handoff --role` command still has no executor/reviewer
and returns dispatch-only results. There is also no production configuration
path that supplies `owner_login`, `implementer_logins`, or
`reviewer_logins` to `GithubCliBoundary`; with defaults, remote candidate and
feedback reads accept only the current viewer's comments. Therefore an
independent reviewer using a distinct configured identity cannot participate
through the real CLI construction. `test_handoff_entrypoint_dispatches_only_injected_bounded_worker` and the independent-role fixture use injected fake
objects, which proves the in-memory seam but not the production entrypoint.

### Current disposition

**Changes requested; do not merge yet.** R5 permits work outside the current
active goal, and R6 does not provide a production-configured worker/role
handoff path. Passing 30 focused and full suites are strong regression
evidence for the fixtures, but do not negate these authority and integration
failures. No schedule creation, automatic merge, or live operating claim was
observed.

## Latest R5/R6 re-review (`e68a4bf`)

Reviewed the current PR head `e68a4bfa35c838c72269126970c6d95ae5e9a4c8`.
GitHub's required `validate` check is successful at review time. Per the
reported local evidence, an initial isolated-engine HTTP 400 occurred during
one full-suite attempt; the exact affected test was then rerun successfully.
This is recorded as a transient observed test event, not as proof of an
operating deployment.

### R5 — resolved in source; exclusion regression is not explicit

`handoff_tick()` now obtains exactly one item from
`DeliveryPlan.select(self.state_store.read())` and supplies only that issue to
`authorized_goals`. This removes the prior authorization of historical and
future goal-ready records. The source therefore fixes the identified
authority widening. The current focused tests do not explicitly construct a
non-active goal-ready issue with an `agent:ready` label and assert that the
entrypoint excludes it; adding that narrow regression assertion would make
the active-goal guarantee directly reviewable.

### R6 — source adds a CLI configuration route; production-entrypoint evidence remains incomplete

`--worker-factory personal_agent.module:function` is now passed from `main()`
to `handoff_tick()`, imports only a `personal_agent.*` factory, supplies the
selected role and root, and requires the returned object to be callable. This
is a real CLI configuration route, unlike the former constructor-only test
seam. It does not create a schedule or merge capability.

However, neither `tests/test_delivery.py` nor `tests/test_handoff.py` tests a
valid `--worker-factory` path (or `handoff_tick(..., worker_factory=...)`).
The present 30 focused tests exercise injected `handoff_workers`, not the new
production argument parsing/import/factory invocation. No bundled bounded
factory is identified by the reviewed diff, and role-login configuration for
distinct GitHub identities remains external to this CLI flag. The command's
actual configured-worker behavior is therefore unverified by automated
evidence.

### Disposition

**R5 accepted in source. R6 needs one focused production-entrypoint regression
test before this independent review can accept the R5/R6 repair as complete.**
That test should prove that a valid permitted factory is invoked once with the
requested role/root, its callable reaches `StateHandoffLoop`, and an invalid
or non-callable return fails before an issue label changes. The prior green
focused/full evidence and current green CI remain valid for their covered
paths; they do not cover this latest CLI path.

## Final R5/R6 current-head re-review (`6195322`)

Reviewed current head `6195322341c7736b392f1bfc2c70d8151ccf4080` and GitHub
Actions run `34473816543` (required `validate`: successful). The focused local
re-run `PYTHONPATH=src python3 -m pytest -q tests/test_delivery.py
tests/test_handoff.py` passed **31 tests**; the delivery unittest selection
passed **16 tests**; `git diff --check` passed.

R5 remains resolved: `handoff_tick()` derives `authorized_goals` from the
single result of `DeliveryPlan.select()` rather than every goal-ready record.
No historical or future plan entry is granted by that construction.

R6 now has a production CLI route and an automated regression check.
`--worker-factory` is parsed by `main()`, limited to a `personal_agent.*`
module reference, called with the selected role/root, checked for a callable
result before the GitHub boundary is constructed, and passed into the handoff
loop. `test_handoff_worker_factory_is_restricted_and_invoked` proves a
permitted factory is invoked with those arguments and a rejected `os:system`
reference fails closed.

The new fixture uses an empty issue list, so it does not itself invoke the
returned callable against an eligible issue; existing injected-worker coverage
exercises that loop wiring. This is a narrow coverage limitation, not a
blocking source finding: the current construction assigns the returned
callable to the selected role before constructing `StateHandoffLoop`.

### Current conclusion

**No remaining blocking R5/R6 finding.** The green CI and focused evidence
cover the active-goal restriction and the newly added factory parsing/invocation
path. As throughout this review, this is not evidence of a live GitHub worker,
heartbeat, schedule, or automatic merge.
