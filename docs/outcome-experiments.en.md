# Outcome experiments: recall and continuing responsibilities

## Status

This document specifies experiments for [#827](https://github.com/Jongtae/Still/issues/827) under program [#822](https://github.com/Jongtae/Still/issues/822). It is **design evidence**. The fixtures it names are **fixture evidence**. Neither is live evidence.

- It changes no runtime code.
- It selects no goal and starts no scheduler, model call or account operation.
- Each capability gap below stays on its canonical issue. Implementation there needs that issue's own accepted scope and review.

The question it answers: does stored context, and work the owner hands over, reduce repeated instructions and improve outcomes across days? It also asks the reverse: when does extra context make an answer worse?

## What these experiments may not do

Each invariant below names the check that enforces it in the fixtures, so a broken invariant shows as a failing check, not as a lower score.

| Invariant | How the experiment holds it |
|---|---|
| No experiment or prompt/source change creates authority (C3, C4, C16). | Fixtures live under `evals/` and `tests/` only. Nothing under `src/personal_agent` is added or changed. `tests/test_no_scenario_code.py` keeps guarding core code against task, site and skill branches. A candidate retriever or brief changes **which current rows are read**. It never changes Grants, approvals, folder scope or effects. |
| No forgotten, superseded, corrected or expired fact is revived. | In the paired recall fixture, the scorer decides whether a returned row is eligible from the **seeded store's own state** (`state`, `valid_until`), not from anything the retriever reports. Any such row a retriever returns counts as a `trap` hit. A candidate with any trap hit is reported under `trap_regressions`, whatever it gained elsewhere. A retriever that writes to the store (which could re-create a forgotten value under a new id) is refused. The lexical baseline is held to zero trap hits by a test. |
| No experiment claims an outcome it did not observe (C6). | Outcomes come from Still's own records: `tool_events`, `line_outcomes`, and `work_status.derive_status` with `goal_outcome` and `effect_state`. The rubric judge only grades conversation text. A promised report or scheduled run that did not come due during a sweep is recorded as **not observed**, never as delivered. |
| No task-specific core branch (C16). | Cases differ by data, not by code path. The step vocabulary of a continuity trace is a closed set of existing primitives (`open_line`, `standing_correction`, `replace_preparation`, `stop_line`, …). Nothing in it is named after a task. |
| No owner data in the bundled set. | All bundled cases are synthetic, with made-up people, companies and places. Owner-derived cases stay in the owner-local scenario folder (`paths.local_scenario_dirs`) and are reported only as counts and patterns. |

## Foundations and gaps

Every proposed capability maps to an existing primitive and an existing issue. The fixtures exercise the primitive as it is today; the gap column says what the experiment cannot yet observe.

| Capability | Existing primitive (file:symbol) | Issue | Concrete gap |
|---|---|---|---|
| Lexical recall baseline | `quickstart_store.QuickStore.search_memories` (FTS5 `memories_search`, BM25 over quoted prefix terms joined with OR, `state='current'`, `valid_until >= holding_on`, bounded `LIKE` fallback reported as `search_mode`), called by `memory_service.MemoryService.search_memories` | [#403](https://github.com/Jongtae/Still/issues/403) | None. This is the baseline arm. |
| Candidate recall (meaning, not exact words) | None in `src/`. #403 step 1 is AI-written query phrasings over the same index. Step 2 is a local embedding index behind an adapter with an FTS5 fallback. | #403 | No candidate exists. The paired fixture accepts any `retriever(query) -> ids`, so a #403 candidate can run without changing the fixture. |
| Worker's own synonyms | `agent_runtime` tool schemas `search_memory`, `search_conversation`, `list_memory`; brief assembly in `quickstart_service.QuickStoreService.owner_words_block` | #403 | The in-conversation arm (R2) observes this only through answers. The tool payload is not graded. |
| Currentness and expiry | `memories.valid_until`, `state`, `invalidated_by`, `invalidation_reason`; `save_memory(..., valid_until=, invalidates=)` | [memory contract](engineering/contracts/memory.en.md) §3 | There is no `valid_from` and no "as of date X" recall, so dated contradictions are expressed as supersession plus `valid_until`. |
| Owner correction | `save_memory(..., corrected=True)` marks the wrong row `corrected`. `correct_memory` handles approval and undo. | memory contract | None for the fixture. Seeding uses the real call. |
| Forgetting | `owner_forget.OwnerForget.forget` sets the whole chain to `forgotten` and records `forgotten_values`. `is_forgotten` stops upkeep from saving the value again. `forgotten_turn()` excludes turns from the dream. | memory contract | The FTS index keeps forgotten text until `delete`, and it is filtered at the join. The experiment asserts that **retrieval results** exclude it. It does not assert anything about the index's contents. |
| Upkeep, self-review, dream | `owner_model.Upkeep.run`, `run_review`, `run_dream`, `validate_dream`, `grounded_ends` | memory contract; [memory and dream experiments](research/memory-and-dream-experiments-2026-10-22-to-11-05.ko.md) | The m7 night-by-night replay and its private sets are not in the repository. The public fixture covers retrieval only, not what the dream writes. |
| One responsibility across preparations | `entrusted_lines.EntrustedLines` (line id stored as `procedure_versions` slot `line:<id>`, versions with `stats.change`), `preparations.line_id`, `preparations_service.scheduled_line`, `line_outcomes`, `line_run_input`, `stop_line`, `line_version_read` | [#798](https://github.com/Jongtae/Still/issues/798) (closed), [#799](https://github.com/Jongtae/Still/issues/799) | A preparation's `replaces` is not stored, so a chain without a line has no join. Edits mutate in place, and the line-to-report link is a derived `request_key` join. The traces therefore assert identity through `line_id` only. |
| Promised interval report | `preparations.window_report_reason`, `window_terms.report_seconds` | [#813](https://github.com/Jongtae/Still/issues/813) | A skipped boundary loses its report, and there is no "owed since" cursor. The trace step is marked `gap: 813` and skipped. |
| Window length and run cap | `preparations.normalize_window` (`MAX_WINDOW_RUNS = 96`) | [#814](https://github.com/Jongtae/Still/issues/814) | The cap truncates the window silently. The trace step is marked `gap: 814` and skipped. |
| Authoritative outcome evidence | `tool_events`, `work_status.derive_status` (`goal_outcome`, `effect_state`), `preparations.settle`, `EvidenceLog` | [#462](https://github.com/Jongtae/Still/issues/462) | The #462 task map is not built. Outcome rows for continuing work live only in these experiment files for now. |
| Parallel independent reads | `agent_runtime.ReadOverlap`, `concurrent_read`, `CONCURRENT_READ_LIMIT = 4` (browser and code actions excluded), `mcp_bridge` capacity limiter; `WorkBudget`, `WorkLedger` | [#16](https://github.com/Jongtae/Still/issues/16) | There is no lease for shared browser or account mutations. Only `service.worker_lock` serializes them, so parallel **mutation** is out of scope (see P1). |
| Content a Work reads driving owner effects | — | [#744](https://github.com/Jongtae/Still/issues/744) | Owned by #744. These experiments add no untrusted-input authority finding and depend on none. |
| Recovery across restart | Restart by a new `QuickStore` + service in tests; `Preparations.start` claim idempotency (`request_key`) | [#826](https://github.com/Jongtae/Still/issues/826) | Checkpoint recovery is specified by #826. The traces only check that identity and the standing correction survive a restart. |

## Experiments

| ID | Question | Arms | Fixture | Evidence class |
|---|---|---|---|---|
| R1 | Does a candidate retriever deliver more needed current facts without surfacing traps? | lexical baseline vs candidate, the same cases paired | `evals/recall/recall-pairs-v1.json`, scored by `evals/agentos_eval/recall_pairs.py` | fixture (model-free); a model-written-phrasing candidate is a local model observation |
| R2 | Does stored context reach the answer, and does irrelevant context stay out? | baseline revision vs candidate revision of Still, the same scenarios | `evals/scenarios/recall-experiments.json` | live local observation of the sandbox, judged |
| C1 | Does one responsibility keep its identity across corrections, replacement, restart and stop, with outcomes taken from Still's records? | current code; gap steps skipped | `evals/continuity/line-traces-v1.json`, `tests/test_line_traces.py` | fixture (model-free, injected clock) |
| C2 | In conversation, does the owner's AI treat a change as the same responsibility and stay honest about runs it has not made? | baseline vs candidate revision | `evals/scenarios/continuity-experiments.json` | live local observation, judged |
| P1 | Does parallel reading improve completion or latency at a comparable budget? | `CONCURRENT_READ_LIMIT` 1 vs 4 as two checkouts, same `WorkBudget` | bundled scenarios tagged `travel`, `shopping` and `food`, fixed before the run | live local observation |

### R1. Paired recall: lexical vs candidate

**Inputs.** The fixture holds 25 synthetic Memory rows and 24 query cases (14 dev, 10 held-out). Rows are seeded in order through the real store:
- a same-key save supersedes the earlier row;
- `corrected: true` marks the earlier value as wrong;
- `valid_until` bounds currentness;
- `forgotten: true` goes through `OwnerForget.forget`.

Each case names its `split`, its `kind` and three lists:
- `needed`: rows that must be returned;
- `traps`: rows that are not current or have expired, which must never be returned;
- `irrelevant`, optional: current rows whose return is only noise.

**Kinds.**

| Kind | What it shows |
|---|---|
| `lexical-control` | The query shares the fact's words. The baseline must find it, so a candidate that loses it has regressed. |
| `paraphrase` | The meaning is the same but no content words are shared. This is the main reason for #403. |
| `cross-language` | The fact is stored in Korean and asked for in English, or the reverse. |
| `short-follow-up` | "그거", "아까 말한 데": the query alone carries little meaning, and the case's `context` holds the preceding owner turn. A retriever that reads the context can recall the row and one that does not cannot, so the case shows whether more context helps. |
| `dated-contradiction` | The later dated statement supersedes the earlier one, and the earlier one is a trap. |
| `correction` | The owner says the earlier value was wrong. The corrected row is a trap. |
| `currentness` | The fact ended before `today` (`valid_until` < `today`) and is a trap. |
| `forgetting` | The owner forgot the fact. The forgotten row is a trap, and `needed` may be empty. |
| `harm-control` | Nothing stored is needed, and every returned row counts as noise. It shows where extra context harms. |

**Arms.** The baseline is `MemoryService.search_memories` on the request text, with the service's date set to the fixture's `today`. It ignores `context`, as today's brief query uses the raw request text (`owner_words_block`). A candidate is any callable `retriever(query, context) -> [memory ids]` over the same seeded store that does not write to it, for example a #403 step-1 or step-2 implementation on that issue's branch. The fixture and the scorer do not change between arms.

**Measures, reported per split and per kind, never pooled across splits:**
- needed recall: needed rows returned divided by needed rows;
- number of cases with any trap hit;
- noise: irrelevant rows returned;
- a paired per-case comparison of `fixed`, `regressed`, `unchanged-pass` and `unchanged-fail`, in the shape of `judgments.compare`. A case passes only when every needed row is returned with no trap and no noise, so a retriever cannot pass by returning more rows;
- `trap_regressions`, listed on their own. Any candidate trap hit makes that case `regressed`, whatever else it found.

`compare` refuses to pair results from different fixture versions or contents (a digest of the fixture), or with different case selections.

**Decision rule.** A candidate is preferred only when all four hold:
- it has zero trap hits on both splits;
- it loses no `lexical-control` case;
- its dev gain holds on held-out, meaning held-out needed recall is not lower than baseline and at least one held-out case is `fixed`;
- no held-out case is `regressed`.

**Repetitions.** A deterministic retriever runs once, because repeating it gives the same result. A candidate that samples a model, such as #403 step 1, runs **three** times per case. The report shows per-case majority and spread. If the spread on a case exceeds its difference from baseline, that case is uncertain and does not count as fixed. The #403 comment's 64.6% vs 63.8% result, with 8 better, 5 worse and 27 tied, is the precedent: a difference inside run-to-run noise is no difference.

**Held-out rule.** Held-out cases are written once, in different domains and wording from dev cases of the same kind. They are not tuned against: a change that is motivated by a held-out failure moves that case to dev and needs a new held-out case. Reports record the fixture `version`, and `compare` refuses to pair results across versions.

**Limits.** R1 measures whether the right rows are **retrieved**. It does not measure whether the answer uses them well (R2 does). It also does not cover transcript recall (`search_conversation`) or the profile snapshot cut.

### R2. Recall in conversation

**Inputs.** `evals/scenarios/recall-experiments.json` uses the existing scenario format: 14 scenarios (8 dev, 6 held-out). The kinds are the R1 kinds except `lexical-control` (an owner correction is tagged `kind-owner-correction`), each tagged `exp-recall` and `kind-<kind>`, split `dev` / `heldout`. The owner tells a fact and later asks in other words. Dated turns carry their date with `note_in_message: true`, because the sandbox runs on the real clock.

**Arms.** One sweep per Still revision, baseline and candidate, on the **same scenarios, worker and epochs**, so the report's cohort comparison pairs them:

```
python -m agentos_eval sweep --no-local --scenarios <recall ids> --worker <w> --epochs 3
```

The sweep runs without `--seed`, so each sandbox starts from an empty data folder and no owner seed is involved.

**Expected observable output.** Each `good_secretary` states what must appear in the answer and what must **not**. A stale, superseded, corrected or forgotten value must not appear. In harm-control cases, the earlier context must not narrow an unrelated request (rubric `no_wrong_narrowing`). The deterministic checks `memory_any` and `no_memory_of_request` cover what is stored. The rubric judge covers the answer.

**Limits.**
- A scenario cannot seed Memory, so recall is within one sandbox conversation, not across real days.
- The held-out split has no currentness or harm-control scenario, so in conversation those directions are checked on dev only. R1 covers both on held-out.
- The scenario format has no deterministic answer-content check, so "the forgotten value is absent from the answer" is a judge grade, not a substring check. That gap is recorded and no format change is made here.
- A sweep's results apply to the exact checkout, worker and models it ran with.

### C1. One responsibility across days (model-free traces)

**Inputs.** `evals/continuity/line-traces-v1.json` holds traces of steps at clock offsets (`at_hours`). The step kinds are a closed vocabulary mapped by `tests/test_line_traces.py` onto the helpers that the #798 tests already use: an injected `now`, restart by a new store and service, and scripted model steps. Expected outputs are observable only:
- the same `line_id` across steps;
- the line version;
- whether a rule is present in the run input;
- the active row count;
- whether a run started, and which line version it read (from the `entrusted_line_read` tool event);
- whether a reply was sent;
- the report reason.

**Traces.** Five traces, three dev and two held-out:
- **Open, correct, replace, restart.** A standing correction made on day 1 is in the day-2 run input after a preparation replacement and a restart, under the same `line_id`.
- **Edit the schedule.** One line and one active row remain.
- **Stop.** A stop cancels every row of the line. The next due tick starts nothing and no reply is sent.
- **Today-only correction.** No version is written, and the correction is absent the next day.
- **Promised interval report across a skipped slot** (`gap: 813`), and a **window longer than the run cap** (`gap: 814`). Both are recorded and skipped, never asserted as passing or failing. The #814 step states only the owner-visible requirement and leaves the remedy to #814. The PR that closes either issue removes the step's `gap` marker, so the step runs from then on.

**Why model-free.** A sweep runs Still on the real clock for minutes, so a row a scenario schedules for later that day or later in the week does not come due during the run. A seed's own scheduled rows are cancelled when it is snapshotted (`sandbox.sanitize_database`). Multi-day behavior can therefore only be observed with an injected clock, as the existing unit tests do.

**Limits.** Each trace scripts its model steps. The traces show that the primitives keep identity and propagate corrections and stops. They do not show that a real model chooses to continue a line (C2 grades that in conversation) or that #799's AI-stood lines exist (they do not yet).

### C2. Continuing responsibility in conversation

`evals/scenarios/continuity-experiments.json` holds 8 scenarios (5 dev, 3 held-out) covering these kinds:
- a watch handed over with a report promise and deadline;
- changing what was just scheduled;
- a standing rule added;
- cancellation;
- a status question asked before any run;
- a deadline already passed.

They are tagged `exp-continuity` and `kind-<kind>`. Expected output: a change is treated as the **same** responsibility, not a second one; a cancellation claims no further runs; a status question before any run says nothing has run yet, with no invented results (`honest_unverified`). `expect.preparation` checks only that a row was created. Whether a change kept the same responsibility, or a cancellation took effect, is a **judge grade**, not verified completion. Runs, reports and deadlines do not come due during a sweep (C1 explains why), so C2 grades conversation only.

### P1. Single worker vs parallel reads at comparable budgets

**Arms.** Both arms share the same scenarios, worker, model and `WorkBudget` (deadline, turns, attempts):
- `CONCURRENT_READ_LIMIT = 1`, a single read at a time;
- `CONCURRENT_READ_LIMIT = 4`, today's default.

A sweep runs Still as a subprocess, so the harness cannot patch the constant. Each arm is a separate checkout: the baseline revision, and the same revision with only `CONCURRENT_READ_LIMIT` changed on an unmerged experiment branch. No runtime setting is added. Both arms run the same scenario ids, chosen and recorded before the run.

**Measures:**
- verified completion;
- wall-clock latency per turn, from the runner's sent and delivered times;
- model turns and attempts, from `WorkLedger`;
- tokens and cost, where the provider reports them.

**Scope.** Only effect-free reads overlap (`concurrent_read` already excludes browser and code actions). Shared browser and account **mutations** are not run in parallel by any arm: today `service.worker_lock` is their only ownership, and no per-account lease exists. P1 measures read concurrency only. Decomposition into delegated specialists (`delegate_agent`) is not measured by P1, and a specialist runs with its own bounds rather than the parent's cumulative budget, so it stays unselected until an arm with a comparable cumulative budget exists (#16).

**Limits.** Subscription CLIs hide their usage, so their cost is recorded as unavailable, not as zero. Latency on live web reads varies by site and time, so P1 needs three epochs and reports the spread.

## Measures

| Measure | Source | Notes |
|---|---|---|
| Verified completion | deterministic checks (`scoring.deterministic_checks`), trace expectations, `derive_status` `goal_outcome = reached` with Still-observed `effect_state` | A judge's "looks done" is not verified completion. |
| Avoidable owner interventions | the AI asking again for a fact Still already held, or for an action it was already entrusted with, counted per scenario by the judge with the turn index recorded. Owner turns are scripted, so the measure counts the AI's questions, not the owner's turns. | Required approvals are **counted separately**, never as avoidable. A payment or consequential-action approval is a real gate (C14). |
| Required approvals | approval events in `tool_events` and the scenario's `allow_status` | Reported next to interventions, not subtracted from them. |
| Latency | runner timestamps (sent → delivered) | Per turn, with the spread over epochs. |
| Cost | judge tokens from `budget.JudgeBudget`; worker tokens only where an API worker reports usage | Subscription-worker usage is unavailable, and the report says so. |
| Recovery | C1 restart steps; #826 receipts once specified | Identity and standing corrections after restart. |
| Proactive usefulness | judged on C2 and on line account outputs when they exist | Not graded where nothing ran. A report that never came due is not observed. |
| Context harm | R1 noise and trap hits; R2 `no_wrong_narrowing` grades | Shows where extra context made the outcome worse. |

## Why extra context helps or harms

Context is a finite budget. Irrelevant or stale context costs attention and can be wrong ([Anthropic, effective context engineering](https://www.anthropic.com/research/effective-context-engineering-for-ai-agents)). The cases are built so each direction is visible without a core branch:
- **Helps**: paraphrase, cross-language and short-follow-up cases, where the needed fact is current and the lexical baseline cannot reach it.
- **Harms**: harm-control cases, where any returned row is noise, and dated-contradiction, correction, currentness and forgetting cases, where the lexically closest row is a trap. A retriever that returns more rows raises recall on the first group and trap or noise counts on the second, and the paired report shows both.

## Judge and human disagreement

- Deterministic checks and trace expectations are never overridden by a judge. Authority invariants (trap hits, forgotten values, unobserved outcomes) are decided only by deterministic checks.
- For R2, C2 and P1, the owner (or a contributor, on synthetic cases) labels a sample. That is at least the held-out scenarios and every case where the judge's grade changed between arms. Labels use the same rubric scale.
- The report shows a per-dimension agreement table and lists every disagreement with its scenario id and both grades. A disagreement is kept, never resolved by rewriting the case. When the judge and human disagree on a dimension in more than one of five labelled samples, that dimension's judged deltas are reported as uncertain.
- A single judge model is used per comparison, and its identity is recorded with the report. The harness's cohort (`report.cohort`) keys only on scenario × worker pairs and epochs, so the experiment itself treats a changed judge model as a different comparison and does not pair across it.

## Resources

| Resource | Used by | Notes |
|---|---|---|
| Subscription workers (Codex CLI, Claude Code on the owner's own subscription) | R2, C2, P1 sweeps | Vendor-hidden usage: no token or cost figures. A sweep stops at the first usage limit (`report` marks it stopped and never uses it as a baseline). Claude Code needs a seed that holds its token, so a fresh-folder sweep runs on Codex. |
| API judge (configured model API) | rubric grades for R2, C2, P1 | Capped by `budget.JudgeBudget` (default USD 10 a day). Prompts pass Still's deterministic redaction before leaving the machine. |
| API worker (configured model API, optional) | P1 arms when token usage must be observed | Token usage is visible per call and recorded. |
| Model-free CI | R1 with the lexical baseline, C1, fixture validation | No model, no network. Runs with the repository test suite (`full-validate`). |
| External accounts | none | No owner account, live site sign-in or payment is part of any experiment. A case that would need one is out of scope here and stays on its issue's own live-observation acceptance. |

## Report shape

Every experiment report keeps four sections apart:

1. **Fixture support.** What the deterministic fixture and the model-free tests showed, with the fixture version and commit.
2. **Live evidence.** What a sweep observed, with the revision, worker, model, judge, epochs, seed ("fresh") and run id. The R2/C2 sweep results are a local observation of that exact setup.
3. **Uncertainty.** Spread over repetitions, cases inside noise, and judge-and-human disagreements.
4. **Unsupported paths.** What could not be observed: runs and reports that never came due during a sweep, steps marked `gap`, vendor-hidden usage, transcript recall, and #799 AI-stood lines.

Issue-specific live observation acceptance stays visible and unchanged: #403's acceptance (recall before vs after, the compatibility test, forget/undo removing vectors, the FTS5 fallback, the information-use audit), and #799, #813, #814 and #16's own criteria. Nothing here checks or waives them.

## Existing solutions review

- **Adopt.** The Inspect scenario harness (`evals/agentos_eval`, Inspect AI eval logs, epochs and cohorts) runs R2, C2 and P1 unchanged. The `dev`/`heldout` split and the scenario format are existing.
- **Adapt.** `recall_pairs.compare` follows the paired per-case shape of `judgments.compare` (fixed / regressed / unchanged, refusing mixed versions). The C1 driver reuses the #798 test helpers (`_LineCase`) and the injected-clock pattern.
- **Build**, kept small. A fixture format for seeded Memory rows and the R1 scorer. No existing harness seeds synthetic Memory or grades retrieval against traps: the judgment harness has no recall role, and scenarios cannot seed Memory. The scorer is development tooling under `evals/`, never imported by `src/personal_agent`.
- **Not selected.** There is no new memory, retrieval, scheduler or goal store. An embedding index is #403 step 2's own decision.
