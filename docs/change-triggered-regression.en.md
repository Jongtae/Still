# Change-triggered regression and promotion (#855)

This is development evidence for program #822, not a runtime gate. The owner's
AI still performs the work. The modules only select synthetic evaluation cases
and turn an observed comparison into a reviewable record. They neither change
routes nor execute rollback commands, and never read owner-local scenarios.

## Existing Solutions Review (C15)

**Boundary and decision: Adopt/Adapt.** Adopt Python's `fnmatch.fnmatchcase`, JSON,
`argparse` and argv-only `subprocess.run`; adapt a declarative path-to-target map
to the existing eval loaders. The standard-library compatibility probe matched
`src/personal_agent/prompts/goal_reached.md` against the prompt glob. This uses
case-sensitive shell globs over the whole repository-relative string: `*` can
cross `/`; it is not gitignore/CODEOWNERS parsing or last-match-wins semantics.
All matching rules contribute. No custom glob parser or dependency graph is added.

External options were reviewed before implementation:
[Testmon](https://www.testmon.org/blog/determining-affected-tests/) derives affected
Python tests from recorded coverage; [Bazel query](https://docs.bazel.build/versions/main/query-how-to.html)
traverses declared build dependencies. Those are useful established approaches,
but neither supplies the semantic relationship between an instruction anchor,
a judgment proposition, route settings and these JSON evaluation cases. No
coverage database or Bazel build graph exists for those relationships here.
Neither package is adopted or installed. This bounded adapter records those
relationships as versioned data instead of creating a general affected-test system.
Release activity for these rejected tools was not independently audited; their
selection mechanisms, rather than version-dependent APIs, informed this decision.

Adopt JSON Schema draft 2020-12 and the existing `schema-validation` extra's
[`jsonschema.Draft202012Validator`](https://python-jsonschema.readthedocs.io/en/stable/validate/)
for the record. The prepared environment has jsonschema 4.26.0, whose stable API
supports this draft; `pyproject.toml` already declares `jsonschema[format]>=4.23`.
Its MIT licence and the standard library's PSF licence are compatible with this
AGPL-3.0-only repository. No new dependency, runtime authority, secret access,
network destination or durable owner-state store is introduced. Schemas use no
remote references. Python and Git are existing development requirements.

Internal search (`rg` for `PASS_THRESHOLD`, judgment role names, prompt files,
`instructions_digest`, plus eval loader inspection) found the existing public
`judgments.load_cases`, `judgments.load_still`, qualification `CASE_IDS`,
`scenarios.load_file` and `recall_pairs.load`. Selection reuses those loaders and
IDs; no case parser, scorer or qualification runner is duplicated. The paired
module owns `judgments.compare` reuse and held-out interpretation. Promotion
consumes that output instead of implementing another paired runner. Existing
report modules and scenario/case contents are unchanged.

## Selection data and API

`evals/selection/affected-cases-v1.json` has version `affected-cases/1`. Each rule
has `id`, `match`, `select` and a nonempty `reason`. Match keys are `kinds`,
`paths`, `prompt_fragments`, `propositions`, `routes`, `tags` or `description`.
Each holds glob patterns. A rule fires when **any** input value matches any
pattern in the corresponding field. This union is intentionally conservative
when a whole shared source file changes.

Each selector names `set` (`judgments`, `qualification`, `scenarios`, `recall`)
and optionally `ids`, `roles`, `tags`, `splits`. Fields within a selector intersect;
values within a field are alternatives. With just `set`, every member is selected.
Unknown fields, versions, IDs, roles, tags, empty reasons and empty selectors fail
validation even when the invalid rule would not fire. A newly added source case
must also have its mapping reviewed; this is explicit data, not inferred coverage.

Judgment splits come from `paired.load_partners()` and every case set declared
in `heldout-partners-v1.json`: the 59 existing cases are dev and the 35 new
`heldout-v1.json` cases are held-out. Selecting a dev and its explicit partner
still runs the partner only once. Qualification IDs use the existing harness prefix
`qualification-` and have a null split. Scenarios and R1 retain their declared
splits. Task B's `paired.partners(case_ids, root=...)` expands selected dev judgment
IDs into held-out partners. Selection does not invent partner IDs or change the
old case set: that would invalidate #824's case-content fingerprint. It also does
not treat the selected scenario/recall cases as already executed by a judgment run.

`selection.select(change, *, root=REPO)` returns the normalized change, sorted
selected cases with reasons, and unmatched `{field, value}` inputs. Every selected
row records the matching rule and signals. Duplicate inputs/cases are combined.
Structured changes use these keys (omitted arrays default to empty):

```json
{"kinds": [], "paths": [], "prompt_fragments": ["goal_reached"],
 "propositions": [], "routes": [], "tags": [], "description": ""}
```

Examples: `propositions: ["owner_act_answer"]` selects that role;
`prompt_fragments: ["goal_reached"]` also selects orchestration escalation;
`routes: ["fastest_qualified"]` or `kinds: ["model"]` selects every judgment role,
qualification and worker scenarios. Tags such as `exp-recall` retain dev and
held-out R1/R2 cases. Tool description and worker instruction mappings live in
JSON, never branches on case IDs or tasks in Python. Unknown paths, anchors or
free text are reported in `unmatched`, including alongside known changes. Empty
input is unmatched too. Use structured anchors for a precise selection; arbitrary
prose is retained but cannot establish coverage without a matching rule.

## Select → paired → promote

Run from `evals/` with the prepared interpreter (and `PYTHONPATH=../src`):

```sh
../.venv/bin/python -m agentos_eval.selection \
  --change /tmp/change.json --diff BASE..HEAD --output /tmp/selection.json
../.venv/bin/python -m agentos_eval.paired --help
```

`--change` and `--diff` may be used separately or together. Git only reads changed
paths (`git diff --name-only -z`); `selection.diff_paths(..., git_run=...)` makes
that boundary injectable in tests. There is no shell interpolation.

Create a synthetic seed through shipped activation once (this spends real calls):

```sh
../.venv/bin/python -m agentos_eval.synthetic_seed \
  --out synthetic-terra --engine codex --model gpt-5.6-terra
```

A bare `--out NAME` resolves to `eval_home()/seeds/NAME`, normally
`~/.local/share/agentos-evals/seeds/NAME`. Seeds and live sandboxes must be outside
system temp. Stores under `/tmp`, `/private/tmp` or the platform temp directory
fail Codex's shipped `protected-directory-denied` probe: Codex can read system
temp. Such seed output is refused with that reason; do not set
`AGENTOS_EVAL_HOME` to system temp for a live run. `--dry-run` makes no calls.

The paired runner requires `--seed NAME` with `--live`. It resolves the seed
through `cli.resolve_seed`, requires its `synthetic-seed.json` to declare
`owner_data: "none"`, and records that manifest, including creation time and
requested model, in each arm report. Owner snapshots belong to `judgments run`.
Each arm copies the synthetic seed with `Sandbox(eval_home()/"sandboxes",
seed=..., src=checkout/"src").prepare()`, constructs the shipped service, reads
the seed's route, pins `--model`, then requalifies through the shipped #346 job.
The copied proof is path-bound and cannot be reused as proof for the sandbox.
Qualification and measurement share `CallCap`; `sandbox.stop()` runs in
`finally`, including on setup errors. No route row is hand-written.

`--requalify-attempts N` allows 1–3 shipped-job attempts (default 2). A failed
job is not accepted as qualification. The runner reapplies the same explicit
pin with `judgments.pin_model` to clear that sandbox revision's cached failure,
then invokes the same `requalify_route` job again. Every attempt's state and
calls are retained in `requalification.attempts`; the summary calls sum all
attempts. The same `CallCap` spans setup attempts and measurement. No extra
attempt starts after cap exhaustion. A still-unavailable route produces
`status: "unavailable"` and a reason, with standard `not_run` rows. Comparison
adds `<arm>.route-unavailable` to unknown conditions and returns `incomparable`,
never better, worse or no-gain.

The plan reserves a full suite per allowed attempt. For #752's 42 selected
judgments at three repetitions, each arm reserves 144 measurement calls plus
22 calls for up to two qualifications: 332 calls for a new pair, or 166 for
one newly run arm. Actual early failures can use fewer calls.


A plan makes no calls and needs no existing seed; append `--live` to spend:

```sh
../.venv/bin/python -m agentos_eval.selection \
  --diff c3876f6^..c3876f6 --output selection.json
../.venv/bin/python -m agentos_eval.paired \
  --baseline c3876f6^ --candidate c3876f6 --cases @selection.json \
  --seed synthetic-terra --model gpt-5.6-terra --repetitions 3 \
  --max-calls 332 --out /tmp/still-752-paired
```

The output directory may be temporary; qualified stores may not. Selected
scenario and recall rows are retained as `selected_not_run` with their set, id, split
and an explicit reason to use their own runner. Plans, comparisons, promotion
records and the collapsed PR-comment list preserve this disclosure. Only an
input with no judgment or qualification rows is refused. When any qualification
id is selected, the runner measures the complete shipped `qualification-suite`
role through `j.run`, as `judgments.live_run` does. The plan explicitly expands
and budgets all suite definitions at each repetition; those rows have a null
split and appear in the comparison table. Requalification's setup calls stay
separate in `requalification`, and both setup and measured suite calls count
against the same cap. A qualification-only run retains the shipped base case
set as a schema/catalogue anchor, but executes only the suite role.
To re-run only a failed arm, reuse the successful report. With `PAIR` set to
the original pair752 directory, run from `evals/` with the same selection file:

```sh
../.venv/bin/python -m agentos_eval.paired \
  --baseline 'c3876f6^' --candidate c3876f6 --cases @selection.json \
  --seed synthetic-terra --model gpt-5.6-terra --repetitions 3 \
  --requalify-attempts 2 --max-calls 166 \
  --reuse-arm candidate="$PAIR/candidate.json" --out "$PAIR/baseline-retry" --live
```

`--reuse-arm baseline=REPORT` works symmetrically. Only one arm can be reused.
Before a new checkout or any calls, the saved report's commit must resolve to
the corresponding selected ref. Splits, case-set version/content digest,
partner version/digest, catalogue digest, explicit model pin, seed manifest and
manifest digest, and case/trial selections must match the new plan. Requested
effort and observed model identities are checked too. Mismatches are refused;
this never silently pairs a different fixture or model.

Plans, comparisons and promotion records disclose `reused_arm` with its arm,
original report byte SHA-256, parsed-content digest, source path and
`measured_at`. The saved report is never rewritten, including when the output
folder is its original folder. Only the new arm and comparison are written;
keep the original saved report alongside them. New arm reports record a UTC
completion timestamp. The completed comparison reports the absolute time gap
between arm completion timestamps; the plan's gap is provisional against the
planning clock. Historical reports without a measurement timestamp retain
`measured_at: null` and `time_gap_seconds: null`. A separately labelled file
mtime proxy (`time_gap_proxy_seconds`) discloses the observable interval without
pretending that file modification time is a known measurement time. Time gaps
are disclosure, not an assertion of provider stationarity.

With reuse, `--max-calls` caps only the newly run arm. `cost.calls` still
includes both arms; `cost.new_calls` and `cost.reused_calls` separate this run's
spend from historical calls. The record's `budget.actual` uses new calls only;
it cannot label historical reused calls as a new overrun. The generated rollback
rerun reuses the original candidate/head as its baseline so the one-new-arm cap
remains valid, and preserves the explicit requalification-attempt limit.

The stable Python handoff is:

```python
partner_ids = paired.partners(selected_dev_judgment_ids)
comparison = paired.compare_paired(baseline_report, candidate_report, splits=splits)
```

Run the same selected cases and held-out partners at both revisions, with an
explicit `--model MODEL` pin, identical case content, effort and disclosed
conditions. Use the paired runner's repetition and call cap controls. The
`fastest_qualified` route can move after a flaky qualification; the route name
alone does not pin the model. Every observed-model tuple is checked against the
pin using the existing `judgments.MODEL_FIELDS` layout. A differing requested
model, or an observed model other than the pin or `not reported`, is disclosed
by arm, tuple index, field and value as unknown. Conditions also state whether
the CLI reported the observed model. `not reported` supports the precise claim
"requested model pinned; observed model not reported by the CLI", not a claim
that the provider's actual model revision was observed. Unknown or unequal conditions, different fixture
versions and missing observations cannot establish equivalence. Selected scenarios
and recall cases need their corresponding lane runs; do not label unrun cases as
passing. Preserve their evidence separately when the paired runner does not cover
them. Never pool dev and held-out results.

The comparison records resolved `revisions`, both reports' `prompt_digests`,
per-arm latency and cost, conditions, case changes and `selected_not_run`.
Use `promotion --from-pair comparison.json` to derive base/head and the
candidate model, route, effort and CLI version directly from that evidence.
A different baseline identity remains visible in `conditions.baseline`;
unknown candidate fields stay null. Do not type an observed identity by hand.
The legacy `--paired ... --change-identity ...` form remains available.

`change_identity.prompt_digests` copies only differing files from the two
reports' harness-generated per-file hashes, retaining null for a file absent
on one side. Instruction and tool-schema digest arms remain null with an
explicit reason: #825 per-attempt digests are recorded on Work turns, not on
judgment calls. A judgment run cannot invent that Work-turn provenance.

Rollback and re-run instructions are generated from the comparison, never typed
as evidence. For a Git change, the writer adopts Git's read-only
`rev-list --reverse --topo-order BASE..HEAD` to record the commits oldest first
and emits `git revert --no-edit BASE..HEAD`. It records the same commit list in
the identity and validates the command against the exact base/head range.
For a setting change at the same code revision, it copies prior route fields
from baseline conditions and emits the shipped owner activation path,
`POST /api/decision-route/activate` with its activation body. Nothing executes
a revert or changes an owner setting.

The generated rerun is `python -m agentos_eval.paired --baseline HEAD
--candidate '<REVERT_SHA>' ... --live`. `<REVERT_SHA>` is a literal placeholder
for the commit created by the revert. Cases (including the original
`@selection.json` argument), seed, pinned model, repetitions and call cap come
from the run plan and are recorded in `change_identity.run`. Validation binds
the rerun to those values and revisions; it cannot silently replace the seed
or model. The comparison's plan is persisted in both arm reports, so
`paired --compare` can recover it when both copies agree.

```sh
../.venv/bin/python -m agentos_eval.promotion \
  --selection selection.json --from-pair /tmp/still-752-paired/comparison.json \
  --output /tmp/promotion-record.json --markdown /tmp/promotion-comment.md
```

`promotion.build_record(selection, paired, change_identity=None, *, budget=None,
evidence_class=None, rollback=None, root=REPO)` derives identity, evidence,
budget, rollback and rerun from the comparison. The optional legacy identity,
`budget={"calls_cap": N}`, evidence class and rollback parameters are assertions
and must match the derived values. CLI `--calls-cap`, `--evidence-class` and
`--rollback` work only as matching assertions; `--from-pair` needs none of them.
`validate_record(record)` checks schema and cross-field invariants;
`markdown(record)` renders `evals/templates/promotion-comment.md`.
Both modules expose `main(argv=None) -> int`. CLI integration is left to the
coordinator; task A changes no existing runner, CLI or runtime module.

## Decision, evidence and budget

Uncertainty is evaluated **per case**, never by comparing a pooled spread to a
mean gain. For a case, `difference` is candidate pass rate minus baseline pass
rate; `spread` is the larger of the two arms' ranges of repetition pass
indicators (0 or 1). A positive difference counts as `fixed` only if
`spread <= abs(difference)`; otherwise it is `uncertain-fixed`. A negative
difference is `regressed` or `uncertain-regressed` by the same rule. Thus
0/3 → 3/3 is fixed; 1/3 → 3/3 and 0/3 → 2/3 are uncertain-fixed.
One repetition has spread 0 but is insufficient for a sampling arm. `--live`
requires at least three repetitions before checkout or spending. A comparison
with fewer than three trials on any held-out case is at best `inconclusive`,
with the short cases named. Only two reports explicitly declaring
`deterministic: true` with fixture evidence may use fewer repetitions. Equal rates are
`same` when paired outcomes agree, otherwise `mixed`.

Conditions that differ or are unknown make the verdict `incomparable` first.
A certain held-out regression makes it `worse`. At least one certain held-out
fix, no certain **or uncertain** regression and a mean held-out pass rate that
is not lower make it `better`. An `uncertain-regressed` held-out case blocks
`better` and yields `inconclusive`, even if other fixes raise the mean. If every held-out case is `same`, the verdict is `no-gain`.
Only uncertain/mixed changes, no held-outs, or a lower mean without a certain
regression give `inconclusive`. Dev results never enter that verdict.
Per-case spread/difference and change-kind counts are reported; top-level
spread (max held-out case spread) and difference (mean held-out rate change)
are display values only. A noisy unchanged case cannot erase another case's
certain fix.

Promotion consumes that verdict without reapplying a pooled spread gate:
comparable `better` → `promote`, comparable `worse` → `roll-back`, otherwise
`inconclusive`. Unmatched signals, selected-but-unrun sets and budget overruns
remain explicit for review. An exceeded cap forces `inconclusive`, even when
the held-out verdict is better or worse. This recommends a decision; it never merges,
changes routes or executes rollback.

Held-out cases are written once in different wording/domains. If a failure there
motivates a change, move that case to dev and author a new held-out partner;
never tune against the held-out set. The paired module applies #827's rule.

The record and PR table show base/head, route, pinned model, CLI/effort and input
identities, both arms' conditions and unknowns, selection reasons, per-case
passed/trial counts and changes, held-out reasons, latency, calls and usage.
Evidence classes stay distinct: `fixture` is model-free/injected evidence;
`actual-cli-synthetic` exercises the actual CLI with synthetic cases; `owner-live`
is possible only when both source arm reports explicitly say `owner-live`.
The paired runner never emits owner-live evidence. Class is derived from both
arms: both fixture gives fixture; matching live-route classes plus both
synthetic seed manifests give actual-cli-synthetic; disagreement or unsupported
source classes are refused. The record retains this arm evidence and manifests,
and validation rejects a relabelled class.
A table row establishes only its named route/model/class; unobserved #42 matrix
cells remain unobserved. Subscription usage is `"unavailable"`, never zero.
Known usage objects are preserved, without invented currency prices.

Disclose the combined two-arm call cap before running; the record copies actual
calls from the comparison, including the runner's counted retries/qualification.
The record's budget is `{calls_cap, planned_calls, actual, exceeded}`. The cap
and planned count come only from `paired.plan.max_calls` and
`paired.plan.planned_calls`; actual calls come from `paired.cost.calls` for a new pair, or verified
`paired.cost.new_calls` for a resumed pair. Both arms remain in `cost.calls`.
An optional asserted cap cannot replace the recorded one. Validation checks
these bindings and an overrun prevents a promotion/rollback recommendation.
The writer cannot retroactively enforce a runner budget.

Workers have a finite timeout of planned arm calls × 120 seconds plus 60 seconds
for startup. A failed or timed-out worker reports the last 4,000 characters of
its own stderr without a new redaction layer. Both failures unwind the owned
worktree context, removing that worktree; no other session's worktree is pruned.
#612 requires focused, change-triggered, explicitly budgeted samples, not a blanket
paid gate, every model/route combination, or routine live-model calls. Fixture
unit tests need no owner data or external effect.

Tests cover hand-built serialization fixtures and an end-to-end A/B contract:
real selection, injected-engine judgment run/report, paired comparison, record
schema validation and Markdown rendering. They do not measure a real revision's quality. The coordinator
must still attach task B's measured comparison for a real recent change and the
resulting record to meet that issue-level acceptance criterion. No recent change
is claimed promoted by these fixture tests.
