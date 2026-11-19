# Lesson-retention eval (#1031)

Part of [#1031](https://github.com/Jongtae/Still/issues/1031). Development tooling only: nothing here is imported by
`src/personal_agent` or installed into Still. It reads a **copy** of an owner's store and reports **counts only**.

## Why

Still learns the owner's way of working as lessons in Memory (today: the `working_notes` document, slot
`owner:working-notes` in `procedure_versions`). #1031 found that a whole-document rewrite silently drops lessons: the
revision check never verifies that old points survive. Earlier memory experiments (the private replay harness) showed
single runs vary by about 10 points, so decisions on steps 2 and 3 of #1031 need a repeatable measure of one thing:
*does a lesson, once learned, still reach later Works and get followed?* This eval answers that in two parts.

## Existing solutions review (Adopt / Adapt / Build)

- **Adopt** Still's own readers and parsers: `owner_model.WorkingNotes.block()` (what a brief carries),
  `owner_model.notes_points` / `normalized` (what a point is), `QuickstartStore.search_conversation` (which Works a
  lesson's words match), and the eval package's `scoring.judge_call`, `budget.JudgeBudget`, `redaction.Redactor`,
  `cli.selected_judge` / `ensure_judge_key` / `judge_notice` (explicit judge route, spend cap, redaction, key handling).
- **Adapt** the retained-fact metric of the memory work (ACE, arXiv 2510.04618, measures survival of itemized bullets;
  Anthropic's multi-writer table result, 41 of 57 graded facts survived) as *share of learned lessons still carried*.
- **Build** only the thin pieces nothing covers: the history reader over `procedure_versions`, the matching of a learned
  lesson to a carried one, the judge prompt and the report. No new index, no embedding, no dependency (stdlib + the
  repository's own eval package; Inspect is imported only when a judge is chosen).

## Snapshot and live-profile access

`snapshot` copies the live database with SQLite's backup API from a `mode=ro` connection (with a busy timeout) into a
Still data folder `<out>/snapshot/private/quickstart.db`. Retention and application read Work and lesson data from the
copy. An explicitly chosen judge also loads stored literal secrets from the snapshot and live profile into memory for
redaction. Output paths must be outside the repository and live profile. Share only aggregate counts in PRs or issues.

## Metric 1: retention (model-free)

```sh
PYTHONPATH=src:evals:evals/lessons python3 evals/lessons/lessons_eval.py snapshot \
    --db ~/.local/share/agentos/private/quickstart.db --out ~/.local/share/agentos-experiments/lessons-2026-11-19
PYTHONPATH=src:evals:evals/lessons python3 evals/lessons/lessons_eval.py retention \
    --snapshot ~/.local/share/agentos-experiments/lessons-2026-11-19/snapshot \
    --out ~/.local/share/agentos-experiments/lessons-2026-11-19/retention.json
```

- **Learned**: every distinct lesson (a point, normalized as `owner_model.normalized`) that appears in any version of
  slot `owner:working-notes` whose state is not `forgotten`. A lesson the owner forgot is not a lesson the assistant
  should still carry, so it never counts.
- **Carried**: the points of `WorkingNotes.block()`, the text a Work's brief carries now. A successor storage (lessons as
  Memory items) is read by replacing `carried_lessons()` in `lessons_eval.py`; that one function is the adapter.
- **Rewordings** use token-set Jaccard. When a point meets the configured threshold against an earlier lesson, both
  share that lesson's identity for retention and drop events. The default threshold is `0.8`.
- **Matching** assigns exact normalized-text matches first. It then assigns each remaining lesson the best unused
  carried point that meets the threshold and counts that match as `reworded`.
- **Report** (counts only): `learned`, `carried`, `retained` (`exact`, `reworded`), `lost`, `retention` (retained ÷
  learned), `versions`, points per version, and `drop_events` (version transitions that lost at least one lesson, with
  how many). The tool cannot tell a deliberate removal from an accidental one; `drop_events` is the signal to read next
  to `retention`. `--detail` adds per-lesson rows with a **digest** (never text), first and last version it appeared in,
  and whether it is carried.

## Metric 2: application (opt-in, needs a judge)

```sh
# plan first: no model call, no key; prints pairs and the worst-case cost
PYTHONPATH=src:evals:evals/lessons python3 evals/lessons/lessons_eval.py application --snapshot <out>/snapshot --plan
# run: only with an explicitly named route
PYTHONPATH=src:evals:evals/lessons python3 evals/lessons/lessons_eval.py application --snapshot <out>/snapshot \
    --judge <route> --max-pairs 40 --out <private>/application.json
```

- **Pairs (model-free)** use `search_conversation` with the same FTS5 prefix terms as memory recall,
  `skip_recent=0`, and the lesson's application window. Application tracks lessons by normalized text. Eligible Works
  succeeded and have both a first user message and a last assistant message after excluding forgotten rows. A pair is
  skipped if either selected message exceeds 16,000 characters or contains a retired secret. Pairs are capped by
  `--max-pairs` and spread round-robin over lessons, with up to three search results per lesson.
- **Judge**: for each pair the judge sees the lesson, the Work's request and its final answer. All three are redacted
  before truncation. The judge returns one of `followed`, `not_followed`, `not_applicable`, `unclear`. The judge
  runs **only** with `--judge <route>` (or `AGENTOS_EVAL_JUDGE_MODEL`); the default is off and `--plan` never calls it.
  Same rule as every eval judge (#885): the default eval judge would bill the owner's key, so the route is always named.
  Spend goes through `JudgeBudget` (daily cap, worst-case reservation); a refused call is counted `budget_refused`.
- **Report** (counts only): `pairs`, `followed`, `not_followed`, `not_applicable`, `unclear`, `judge_error`,
  `budget_refused`, and `follow_rate` = followed ÷ (followed + not_followed). `not_applicable` and `unclear` are
  reported, not hidden: a high `not_applicable` share means the lesson's words matched topically but the lesson did not
  govern the Work, so the rate rests on few pairs.
- A pair says whether the Work's *answer* followed the lesson, not whether the lesson was *in the brief* of that Work.
  Read it beside retention: low retention with a high follow rate means lessons are being re-learned; low both means
  they are lost.

## Using it to decide steps 2 and 3

Run both metrics on the same snapshot before and after a change, several times: the judge's verdicts vary, so report
the spread of `follow_rate` over runs and adopt a step only when the gain exceeds it (#1031 criterion). Retention is
model-free and deterministic, so it needs one run.

## Limits (what this is not)

- A **live local observation of one owner's copy**, not a calibrated quality score. The judge is a model; its verdicts
  are not human labels.
- Digests are unsalted hashes of short sentences. Treat them as identifiers and keep detail files private because
  someone who guesses the text can reproduce the hash.
- Jaccard merging can join lessons that differ in a decisive word, such as a number or negation. Use
  `--threshold 1.0` for exact-only counts.
- SQLite opens the live database with `mode=ro`. A WAL database can still cause SQLite to create `-shm` or `-wal`
  side files. The connection does not write source data.
- The application window is **[first saved, first version without the lesson)**, where the end is the first
  non-forgotten version after the lesson's last appearance. The window is open-ended for lessons in the newest
  version. A lesson dropped and later re-learned has a single window spanning the gap.
- `search_conversation` matches words, so a lesson with generic wording gathers many unrelated Works
  (`not_applicable`), and one with unusual wording may gather none.
- Synthetic fixtures in `tests/` pin the behaviour; they say nothing about any real owner.

## Files

| File | What it does |
| --- | --- |
| `lessons_eval.py` | The `snapshot`, `retention` and `application` commands; the history and carried readers; the pair builder; the judge prompt and parser. |
| `schema.json` | JSON Schema of the two reports (counts only). |
| `tests/test_1031_lessons_eval.py` | Unit tests over synthetic stores: no live data, no model, no network. |
