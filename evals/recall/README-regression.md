# #921 PR-8 recall regression

This is an eval harness, with model-free replay as its default evidence class.
No product file changes. No judge, embeddings, model comparison sweep or
held-out tuning. The v4 bytes and its 36 held-out cases are pinned in the
regression tests. Claude completed the one approved live recording at
`2474d74d`: 204 subscription CLI calls (11 requalification, 45 terms, 148 plans),
with no judge, no refusals and no failure. The full model-free replay passed;
see the [recordings README](recordings/README.md) for provenance, digests and
the #921 setup/results. Exact-head CI remains unobserved.

## Reuse review: Adapt

Boundary: record synthetic terms and decided plan inputs, then exercise the
shipped reply gate deterministically. The approved v5 spec's existing-solutions
review supplies the retrieval concepts. Python's installed JSON, hashlib,
SQLite backup, pathlib and mock APIs supply recording, isolation and scoped
instrumentation (PSF/SQLite public domain, existing supported Python runtime).
The shipped `CallCap`, `RoutedDecisionEngine`, `service_factory`,
`requalify_route`, `Sandbox.prepare`, `resolve_seed` and synthetic seed marker
supply the complete route/qualification path. `SandboxPool` supplies stable
slot locking and its per-seed Judgment config cache; no second cache or
qualification implementation. The injected `WorkBudget` and `WorkLedger`
clocks freeze the budget facts before the engine boundary. No second route
implementation. Round-1 recovery also adopts `SandboxPool`'s
existing stable-slot, per-seed qualification cache and JSON config read/write
APIs; no qualification proof is rewritten. Stdlib `Counter` supplies status
counts. The recorder reuses `memory_value_has_secret` for committed-input
checks. These are eval-only adaptations, with no product-code changes.
The executor, `plan_inventory`, profile cut, snapshot and guarded terms APIs
supply all product behavior. `recall_pairs` supplies load/seed/run and scoring;
`recall_report.run_arm` and its observed majority rule supply measurement.
Existing `Transport`/`Engine` seams from `test_orchestrator` are adapted locally;
evals never imports tests.

Round-2 adapts stdlib `mock.patch.object` to replace only worker dispatch while
retaining the original adapter's read-only discovery/qualification surfaces.
`FixtureDecisionEngine`, the shipped terms loop and plan schema supply dry-run
answers and execution; no parallel route writer or schema framework is added.
Tests reuse `test_decision_routes.ServiceFixture`'s shipped activation with fake
finder/qualification surfaces to populate seed-shaped route records. Python's
`try`/`except`/`finally` and the same JSON exit writer preserve partial results;
there is no new recovery store or dependency.

Round-3 adopts stdlib `gzip.open` with the existing JSON decoder for
suffix-selected `.json.gz` input; the recorder and report share one reader.
The existing file-only resume validation and `replay_case` supply binding and
sample checks. No third-party compression/parser package is needed: the
standard library covers the complete format without a new dependency.

Generic evaluation frameworks and schema packages do not supply these
Still-specific inventories, receipts or CAS boundaries. No dependency is added;
no external maintenance or release lookup was made under this task's no-network
limit. There is no new runtime, licence, supply-chain or deployment requirement.
No owner data or new authority is introduced. The real CLI worker execute
method and real API worker transport are guarded against use. Fixture workers return
one fixed answer, capture their dispatched context, and expose no tools to an
outside process. The CLI browser relay is also a fixture (no socket).

## Store and dev additions

The measured store has all 1,078 v4 rows plus 28 addition rows (1,106 total).
Addition rows precede v4, preserving the ordering of every v4 row. Their fixture
has its own version and SHA-256 binding to v4; `build_gate_dev.py` rebuilds its
JSON, and `--check` checks bytes. It adds four dev cases: celesta second-query,
octant saturation, marquetry irrelevant hit, and an annotated Korean notebook
already present in A. An annotation is introduced immediately after planning
for that last case in **both** recording and replay, without a model call.

The unchanged v3 forward-leakage scan covers all addition rows against every
v4 meaning query and context (including keys). The unchanged conservative stem
check separates addition queries/contexts from every v4 case. The existing
lexical, context-concat, lexical-context and prefix-lexical paths check that no
addition row supplies new noise to any v4 case. V4/held-out bytes stay unchanged.
The four case-behavior tests use author-written selectors only as **fixture
checks**; live measurements use exclusively the recorded plan's selector.

SQLite backup restores each Work's pre-case database after observing it.
Thus all phase-B cases see the phase-A terms in the same store, without previous
eval replies, annotations, upkeep or conversation affecting the next case.
Synthetic monotonic creation times preserve fixture insertion order; executor
calls build facts and receipts using a fixed fixture-date clock. Each live
structured engine call (terms and plan) temporarily restores the real
`time.time` inside the capped routed engine; fixture time resumes immediately
afterwards, including on exceptions. Plan facts are built before that boundary.
The product Work budget/ledger use their injectable clocks at fixture time, so
remaining-seconds facts and input digests are stable across host scheduling
and resume boundaries; the live caller retains its real timeout clock. A preceding owner turn is enqueued and
retained as a succeeded Work/message when the case has context.

## Live recorder (Claude only, after budget approval and sweep lock)

First exercise the copied synthetic sandbox with **zero model calls**. From
this worktree, with `PYTHON` set to the project's venv interpreter and
`OUT` set to a fresh output directory outside the repository, the exact preflight
command is:

```sh
PYTHONPATH=src:evals "$PYTHON" -m agentos_eval recall-record \
  --seed synthetic-terra --dry-run --dry-run-cases 6 --max-calls 0 \
  --fixture evals/recall/recall-pairs-v4.json \
  --additions evals/recall/recall-gate-dev-1.json \
  --heldout-runs 3 --out "$OUT"
```

`--dry-run` uses the same `SandboxPool`/`Sandbox.prepare`, service factory and
stored decision route as recording. It checks the catalogue, installs fixture
workers when needed, seeds all rows, runs the terms loop over all eligible
rows, and executes phase B through `Executor.run` and `PurposeRouter`. Only
the structured terms/plan answers are canned fixtures. The first N run/case
pairs (default 6), plus at least one held-out and one dev-addition pair, run in
the original order. It skips requalification even with `--requalify`; it never
resolves the live engine or saves qualification changes to the cache.
`--max-calls` must be 0 or absent. Synthetic-marker, empty-seed and output
location checks still apply. `--terms` and `--resume` are refused so both
phases are exercised. The terms loop's completed-transport receipts are
fixtures; all admitted live-call counts remain zero. Both files carry
`evidence_class: dry-run (no model call)` and `complete: false`. Validation,
regression reports and terms reuse refuse them as regression inputs.
Codex tests this path only against temporary fixture seeds; the command above
has not been run against `synthetic-terra` during these fixes.

From this worktree, with `PYTHON` set to the project's venv interpreter and
`OUT` set to a fresh output directory outside the repository. Fresh recording:

```sh
PYTHONPATH=src:evals "$PYTHON" -m agentos_eval recall-record \
  --seed synthetic-terra --requalify --max-calls 248 \
  --fixture evals/recall/recall-pairs-v4.json \
  --additions evals/recall/recall-gate-dev-1.json \
  --heldout-runs 3 --out "$OUT"
```

To reuse a **complete** terms recording, set `TERMS` to its file and `REUSE_OUT`
to an output directory outside the repository. The maximum below allows 44
qualification calls if necessary plus all 148 plans; terms cost **zero** calls:

```sh
PYTHONPATH=src:evals "$PYTHON" -m agentos_eval recall-record \
  --seed synthetic-terra --requalify --max-calls 192 \
  --fixture evals/recall/recall-pairs-v4.json \
  --additions evals/recall/recall-gate-dev-1.json \
  --heldout-runs 3 --terms "$TERMS" --out "$REUSE_OUT"
```

To resume an **incomplete plans** recording, set `PLANS` and `TERMS` to that
run's files and `RESUME_OUT` to the next output directory outside the repo:

```sh
PYTHONPATH=src:evals "$PYTHON" -m agentos_eval recall-record \
  --seed synthetic-terra --requalify --max-calls 192 \
  --fixture evals/recall/recall-pairs-v4.json \
  --additions evals/recall/recall-gate-dev-1.json \
  --heldout-runs 3 --terms "$TERMS" --resume "$PLANS" --out "$RESUME_OUT"
```

Terms reuse and resume accept `.json` or `.json.gz`, selected by suffix.
`--resume` defaults `--terms` to the sibling `terms.json` or `terms.json.gz`,
matching the resume file's suffix. Writing stays plain JSON outside the repo.
Terms must validate against the composed fixture, filter and prompt digests
and be complete.
`apply_terms` installs them through the guarded product write path. The loaded
terms object is never changed: its identity, counts, manifests and metadata
stay intact, and `plans.terms_digest` is its digest as loaded. Plans record
`terms_source: reused` (or `recorded` for a fresh pass).

Resume rejects a complete plan recording, changed fixture/terms/catalogue/
prompt-schema bindings, a changed held-out repetition count, invalid existing
entries or unexpected run/case pairs. It preserves existing entries and records
only missing pairs in the original dev → held-out repetitions → additions
order. `call_budget.sessions` keeps each session's phase counts, cap, admitted
and refused counts, stop reason and model identity. Aggregate counts and caps
are sums across sessions; `reached` means any session reached its cap. The new
`--max-calls` limits only the new session. The previous failed pair is retried.

The opaque `sandbox_cache_key` retains a stable sandbox path across successive
resumes, including when their output directories differ. The existing
`SandboxPool` cache keeps qualification config **privately under eval-home**,
separate from recordings, bound to the seed and slot. The product still checks
the real binding and CLI identity. `--requalify` therefore costs no calls when
the cached qualification remains valid; if it is stale or absent, the shipped
qualification path runs within the new session cap. An interrupted terms pass
cannot be resumed through `--resume`; it requires a new terms recording.

`synthetic-terra` must already have been created by `synthetic_seed.build`;
this command does not create or re-activate a seed. It refuses a missing seed,
missing synthetic marker, missing explicit cap, repository output, and seeds
containing any Memory or Work. `Sandbox.prepare` makes one copy in a stable,
locked slot; no server is started. Each session restores an empty synthetic
seed, not previous Works. The shipped requalification path is invoked once if
requested, and
only runs when needed. Its calls, terms and plans share one cap. The service's
own route resolver is wrapped with the cap **in place of its Work budget**, so
an inner routed engine cannot overwrite the admission callback.

Terms use the shipped arm → claim → run_terms → finish loop, including its
8 → 16 → 25 packing and halving after rejection. A scoped sandbox-only
`Upkeep.settings` override increases `daily_calls` to at least four times the
shared cap, lifting the quarter-share constraint; the original controls and
pause are preserved. The exact override is recorded. Terms stop at 60 calls;
qualification is allowed at most 44. No extra plan is admitted within a Work.

Phase B uses `service.run_one` on queued Works, exactly like gate replay.
Only `orchestrator.PURPOSE` reaches the capped live engine. Every other purpose
uses goal-yes/unavailable fixtures. A non-plan live-forward attempt or a second
plan in a Work stops recording, even if product exception handling catches it.
A non-decided plan (unavailable, malformed, timed out or refused) also stops at
that case. No failed entry is stored: the run is `complete: false`, with its
case/run, `plan_outcome`, `plan_failure` and reason in `stop` and the session
stop record. Product audit failure codes are retained when reported; otherwise
the decision outcome is the failure reason. Decided entries store both fields,
and validation rejects any entry whose `plan_outcome` is not `decided`. A
decided plan with `accepted: false` remains real fallback-worker behavior.
The service's original execution adapter stays installed throughout activation,
Work preparation, route-status checks and the admitted plan call, preserving
`finder`, fingerprint and qualification surfaces. `PurposeRouter` never swaps
it. Only its worker `execute` dispatch is scoped to `FixtureWorker.execute`;
the real `BoundedExecutionAdapter.execute` raises if reached, and an installed
isolated engine adapter's `execute` is blocked too. API workers may send only
through `FixtureTransport`. These fixtures launch no worker process or network
request; API captures the final context at the same commit/dispatch boundary
as CLI. Code-runner availability is fixture-disabled during Work tool discovery
so listing tools cannot spawn `node --version` either.

The catalogue is read from the sandbox service's stored configuration, with
worker identities, kinds, names, model/tool declarations and descriptions
recorded, but never route keys, secrets or local paths. It is injected during
replay. If the synthetic seed has no Main AI (the builder qualifies Judgment
only), activating a real worker would require a non-plan live probe: use
`_runtime_catalogue` and configure a fixture Main AI through fixture probes
inside the sandbox. This activates **only OpenAI API**, with the fake key and
`FixtureTransport` connection probe. It does not activate Codex or run a CLI
login probe, and suppresses the follow-Main switch so the configured decision
route stays intact. Offline replay separately activates CLI identities through
its fixture login adapter. The recording states that fallback and why. A configured
service uses its own catalogue without substitution.

Order is v4 dev once, v4 held-out three times, then the four additions once:
148 plans maximum. Terms failures can exhaust the cap before phase B; such
recordings are marked incomplete and refused as regression evidence. Tokens
are `not observable`, never estimated. The one-screen summary reports phase
calls, refusals, cap reached, completion and stop reason. Nothing is written
inside the repo; Claude copies approved recordings here afterwards.

An exception escaping after any admitted live call writes the terms and plans
accumulated so far before it is re-raised. `plans.json` is incomplete and keeps
the exception class/message, phase, case/run when known and per-phase/session
call counts. Terms interrupted during phase A are also incomplete, include the
stop reason and retain whatever row metadata and manifests exist, even when
`run_terms` or `finish` raises. Such terms are diagnostic only and `--terms`
refuses them; completed terms survive a phase-B crash for reuse. An exception
during qualification can produce an empty diagnostic terms export. None of
these partial recordings is regression evidence.

## Formats and replay

`terms.json` (`recall-terms/1`): composed fixture version/digest, filter source
version digest, terms prompt/schema digest, route/model identity, manifests
(cap/row count/result/outcomes with fixture refs, no text), terms and lifecycle
meta per fixture ref, call counts, override and completion. `status_counts`
contains row counts for keyed, empty, failed, oversize, cleared and `no_meta`,
and manifest result counts for ok, rejected, stale, unsent and any other
observed result. Required categories include explicit zeroes. Seed state is never
exported. Replay filters the terms again and uses `write_retrieval_terms` with
its exact current meta and canonical content digest. Reapplying identical
terms/meta is idempotent; stale input or a refused guarded write is an error.

`plans.json` (`recall-plan/2`): composed fixture and final terms-recording
digests, existing `plan_prompt_schema_digest` bound to the recorded catalogue,
route/model identity and shared budget. Each run/case binds query/context and
records the complete structured plan (including reply, selector, worker and
flags), acceptance, A mapped to fixture refs, a SHA-256 digest of exact facts,
question and schema at the engine boundary, and the executor outcome/receipts,
checked refresh and dispatched worker refs. The boundary digest is provenance,
not a reconstruction of facts. No plan-input text is saved.

Replay enforces both A equality and executor-outcome equality. It never
reimplements the gate. Worker profile attribution mirrors
`WorkExecutor._commit_profile_shown`: the full item line must be at its exact
recorded position in the final context; bare-request dispatch has no items.
The original method still commits annotations. Need/trap eligibility uses the
real seeded store; gate-cost classification compares the actual checked
matched refresh with A, including annotation/revision identity. Every withheld
row requires the reconstructed fresh-item count to equal the product receipt's
`new` count; a mismatch raises instead of classifying noise.

```sh
PYTHONPATH=src:evals "$PYTHON" -m agentos_eval recall-regression \
  --fixture evals/recall/recall-pairs-v4.json \
  --additions evals/recall/recall-gate-dev-1.json \
  --terms "$OUT/terms.json" --plans "$OUT/plans.json" --out "$REPORT_OUT"
```

Reports also belong outside the repo. Lexical and runtime run request-only on
plain and recorded-terms stores. Prefix-lexical is report-only. Gate uses only
recorded terms/plans. Overall/dev/held-out author facets show observed majority
representatives and per-run counts, planner versus worker supply, inventories,
withheld/replies/no-reply and noise-only withholding. Each gate facet summary
(majority and each run, including empty facets) has `plans_not_accepted` and
`plans_not_accepted_cases`; those cases retain worker attribution. Both JSON
and Markdown also show the same row-status and manifest-result counts as the
terms recording, so actual terms coverage is visible. A sampled case's majority
representative follows v3's first actual row in the majority pass/fail group;
attribution is not separately synthesized or averaged. Trap counts per run
remain visible. Named particle and cross-language lists show individual cases.

Steer, preparation, location and equal-digest early-revision variants replay
suitable accepted dev plans without any live calls and report separate pass/fail
checks. Preparation uses public `Preparations.create` and `start` (which
enqueues atomically with its request key and accepted-run state), so recall is
off for both the plan sections A and the gate refresh B. It checks B ⊆ A and
that the reply is used. A missing suitable recorded reply is an explicit
failed check, never a
made-up plan or a measurement. The early-revision check changes the first row's
revision while leaving rendered text equal and verifies frozen A was adopted.


## Committed-input check

When both `recordings/terms.json.gz` and `recordings/plans.json.gz` exist, a
model-free test validates versions, digests, completeness, decided entries,
`tokens: not observable` and `live-route:subscription_cli`. It also checks both
complete objects for credential shapes with the existing
`memory_value_has_secret` and for absolute POSIX,
Windows drive and UNC paths with a path regex, plus canonical gzip headers
(mtime 0, no stored name). A separate test replays only the first three recorded
cases with the committed terms. The tests skip when both recordings are absent;
`recall-regression` remains the explicit full replay (about 26 minutes), with
the gzip command in the [recordings README](recordings/README.md).
