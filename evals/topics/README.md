# Topic-page experiment (#1031 step 3)

Part of [#1031](https://github.com/Jongtae/Still/issues/1031). Development tooling only: nothing here is imported by
`src/personal_agent` or installed into Still. It reads a **copy** of one owner's store and writes real data and
outputs only under a private folder outside the repository. Public reports carry **counts only**: never owner words,
topic names or page text.

## Question

Does one page per recurring owner topic, added to today's brief, put more of the facts a later request needs in front
of the owner's AI, and make its answers follow the owner's lessons more often, **without** more misleading items? And is
a page better than spending the same space on raw transcript search?

Why it might: in the 2026-10-30/31 dream replays (needed facts 55% → 77%) every remaining miss came from an earlier
day. Anthropic's *Components of agent memory* (2026-11-06) reports that transcript search plus topic pages beat either
alone.

## Existing solutions review (Adopt / Adapt / Build)

- **Adopt** Still's own readers: `QuickStore.search_conversation` and `fts_prefix_expression` (FTS5 prefix matching
  over the existing `messages_search` index, for topic membership and for arm C), `MemoryService.profile_snapshot`
  (the profile block), `memory_service.conversation_block` (recall rendering), `OWNER_WORDS_SQL` (which Works are the
  owner's words), the `WorkingLessons.block` heading and line shape, `lessons_eval.snapshot` (read-only SQLite backup
  copy) and `lessons_eval.VERDICTS` (lesson-follow verdicts). The judge prompts adapt the private m7–m11 replay
  harness (`needed` yes/partial/no, traps, misleading).
- **Adapt** ACE (arXiv 2510.04618): pages are itemized and change only by add/update/remove operations, never by a
  rewritten whole page. Anthropic's topic pages: one page per recurring topic, quoting the owner.
- **Build** only the thin pieces nothing covers: the page item model and its operation checks, the day-by-day
  replay, the arm contexts, the judge prompts and the report. No new index, embedding, dependency or `src/` change.
- Rejected: an embedding/cluster library for topics (a new index and dependency; the AI names topics and Still's FTS
  matches them, C16); a hosted memory service (C1/C11).

## Pipeline

All commands run on a snapshot copy, never on the live database.

```sh
OUT=~/.local/share/agentos-experiments/topics-2026-11-20
PYTHONPATH=src:evals:evals/lessons:evals/topics python3 evals/topics/topics_eval.py snapshot --db ~/.local/share/agentos/private/quickstart.db --out $OUT
PYTHONPATH=... python3 evals/topics/topics_eval.py plan  --out $OUT     # counts and the call budget, no model call
PYTHONPATH=... python3 evals/topics/topics_eval.py find  --out $OUT     # topics named by the AI, members by FTS
PYTHONPATH=... python3 evals/topics/topics_eval.py heldout --out $OUT   # held-out requests per topic
PYTHONPATH=... python3 evals/topics/topics_eval.py gold  --out $OUT     # needed facts, traps, applicable lessons
PYTHONPATH=... python3 evals/topics/topics_eval.py build --out $OUT --run 1   # pages, day by day (runs 1..3)
PYTHONPATH=... python3 evals/topics/topics_eval.py contexts --out $OUT --run 1
PYTHONPATH=... python3 evals/topics/topics_eval.py drafts --out $OUT --run 1
PYTHONPATH=... python3 evals/topics/topics_eval.py judge --out $OUT --run 1
PYTHONPATH=... python3 evals/topics/topics_eval.py report --out $OUT  # counts only, every run so far
```

Every model step is resumable (finished items are kept) and refuses to start when the call budget is spent.

### 1. Topic finder (`find`)

- Input: the owner's own requests (`OWNER_WORDS_SQL`, forgotten rows excluded) **before the held-out window**
  (`--heldout-from`, default 2026-11-10). Held-out requests never shape the topics.
- The AI reads the listing and names recurring topics, each with a short neutral description and 2–8 literal search
  terms. Code never names a site, task or category (C16).
- Code assigns members with `fts_prefix_expression(terms)` over `messages_search` on owner (`user`) rows. A Work whose
  `relation_kind` is `reference`, `correction`, `steer` or `retry` inherits its `related_job_id`'s topics.
- A topic is kept when its members span at least 5 distinct days **before the held-out window** (days inside the
  window never count). At most 8 topics.

### 2. Page builder (`build`)

- Replays history one day at a time, from the first day with a member Work. For each day, **one** judgment call sees
  each active topic's current page (with item ids) and that day's member Works (owner words in full, the assistant
  reply as a 600-character excerpt, each with its Work id). It returns, per topic, operations only:
  - `{"op":"add","section":"standard|decision|state","text":..,"quote":..,"source":<work id>}`
  - `{"op":"update","id":..,"text":..,"quote":..,"source":<work id>}`
  - `{"op":"remove","id":..,"reason":..,"source":<work id>}`
- `topic_pages.apply_answer` checks every operation. Refused, and the page left as it was for that operation:
  - no `source`, or a source that is not one of that day's member Works (`missing_source`, `unknown_source`);
  - a `standard` without a quote, or a quote that is not the owner's words verbatim in its source Work
    (`quote_not_verbatim`);
  - an update or remove of an id the page does not hold (`unknown_item`); a malformed operation (`bad_op`).
- **A page is never rewritten whole.** An answer that returns a page instead of operations, or that updates or
  removes more than `max(2, ceil(n/2))` of the page's `n` existing items in one day, is refused entirely
  (`rewrite`) and the page stays as it was.
- The page shape: a short header (topic name and description, from the finder, never rewritten), the owner's
  standards (quoted), decisions, and the latest state, each item with its date and `work:<id>` source ref. Each item
  keeps its own history of operations. Rendered within 2,500 characters, newest first, omitted count stated.
- The page in force for a request is the snapshot **at the start of the request's day**: no item may cite the
  request itself or any Work from that day or later. `topic_pages.assert_no_leak` checks every B context before it is
  written.

### 3. Held-out requests (`heldout`) and labels (`gold`)

- Per topic, up to 6 owner requests on or after `--heldout-from` whose **own words** match the topic's terms (the
  same matcher the brief would use), at most 40 in all, spread over topics. Follow-ups (any `relation_kind`) are
  left out: they repeat their parent, which A's recall already holds.
- Up to 10 extra **weak** requests whose words match exactly one of a topic's terms. They measure the harm of a page
  attached to a request it barely fits.
- Labels (Claude, `opus`, subscription route), exactly as the m7–m11 gold step: from all the owner's own messages and
  Settings entries **before the request's day** (assistant replies omitted), the facts an ideal assistant needs
  (`essential`/`helpful`), traps (earlier statements that mislead if shown as current), and which of the lessons in
  force apply. The labeller gets the **full** pre-day history with no topic filter (the owner's own words total
  about 24,000 characters, so nothing is cut). Requests with no needed fact and no applicable lesson are controls:
  their needed share is not scored, but their misleading items are.

### 4. Arms (`contexts`)

All three share the same base, built as of the start of the request's day:

| Arm | Context |
| --- | --- |
| **A** today's brief | `profile_snapshot(query=request)` over the Memory rows valid then; `working_lessons` in the `WorkingLessons.block` shape (every non-forgotten lesson learned before then, as #1040 now keeps them); recall `search_conversation(request, before=day start, skip_recent=0)` rendered by `conversation_block`. |
| **B** A + page | A, then the matched topic's page snapshot (the topic whose terms the request matches best; one page). |
| **C** A + search | A, then `search_conversation` (`before=day start`) with the topic's terms plus the request's words, then the terms together, the request alone, and each term alone (three exchanges per query): exchanges not already in A's recall, filled to the **length of B's actual page** for that request (whole exchanges, the last one clipped if whole ones leave it under 90%). |

C is the length-matched control: the same topic and the same space, raw transcript instead of a curated page.
Characters per arm per request are recorded. Work ids never reach a model: prompts use short aliases, and page refs
show `work:<first 8 hex>`.

### 5. Drafts and the blind judge (`drafts`, `judge`)

- **Judge** (Claude `opus`; the builder is Codex, so the judge is another family and cannot favour its own
  wording): one call per request per run. It sees the request, the labelled facts and traps, and the three contexts
  under shuffled labels `X1..X3` (seeded by request and run), never the arm names. For each context: per fact
  `yes`/`partial`/`no` with a **verbatim supporting span**; traps presented as current; other misleading statements;
  per applicable lesson whether the context states it. Code checks each span is in that context and downgrades a
  verdict whose span is not (`topic_pages.check_support`); downgrades are counted per arm.
- **Drafts** (exploratory lesson follow): arms A and B only, run 1 only, at most 20 requests with an applicable
  lesson. The owner's AI route (Codex `gpt-6.2-sol`, effort `low`, no tools) writes the reply it would send; the judge
  marks each applicable lesson `followed`, `not_followed`, `not_applicable` or `unclear`. Every arm carries the same
  lessons block and drafts show stated intent, not a full Work, so this is reported as exploratory only.

## Metrics

- **Needed-facts share** (primary): yes = 1, partial = 0.5, no = 0, over all labelled facts of scored requests.
  Secondary: the same over `essential` facts only.
- **Misleading items**: traps presented as current plus other misleading statements, per request, over **all**
  held-out requests (scored, controls and weak), and split by those three.
- **Lesson present** (per context) and **lesson follow** (exploratory, drafts): followed ÷ (followed + not_followed).
- Per arm and run, then mean and **range over runs** (the run-to-run spread). Paired deltas B−A, C−A and B−C: each
  request's delta is averaged over runs first, then whole topics are resampled (bootstrap, 95% interval). Three runs
  of the same requests are not three times the evidence.
- With about 30 scored requests and a per-request spread near 0.25, the smallest gain this can detect is about
  9 points (`MIN_GAIN`).

## Runs and stop rule

- **Three runs.** Each run rebuilds the pages (the builder is a model), redrafts and rejudges. A and C contexts are
  deterministic, so their spread is judge and draft noise; B's spread adds builder noise.
- Earlier replays varied about 10 points between single runs, so one run decides nothing.
- **Early stop**: after runs 1 and 2, run 3 is skipped and the result is "do not integrate" if B−A on needed facts is
  ≤ 0 in both and B's misleading mean is higher in both, or if the B−A interval over runs 1–2 lies entirely below
  `MIN_GAIN`.
- **Decision rule.** Integrate pages into the product only if all hold:
  1. B−A on needed facts is positive in every run, and its mean exceeds the larger of A's and B's run-to-run range;
  2. the bootstrap interval of B−A excludes 0;
  3. B's mean misleading items per request is not higher than A's;
  4. the B−C interval excludes 0 (B beats length-matched search; a tie goes to search, the cheaper product change).
  Lesson present and follow are reported beside it and decide nothing alone.

## Budget

`plan` prints the exact counts before any call and exits 2 when replay days exceed 60 or the worst case (every
possible retry) exceeds `--max-calls`; it is advisory. The hard cap is the shared call ledger: every step refuses a
call once `calls.jsonl` holds `--max-calls` calls. Upper bounds with the defaults (≤ 60 replay days, ≤ 50 held-out
requests, 3 runs):

| Step | Route | Calls |
| --- | --- | --- |
| find | Codex `gpt-6.2-sol` medium | ≤ 3 |
| gold | Claude `opus` | ≤ 50 |
| build | Codex `gpt-6.2-sol` medium | ≤ 60 × 3, plus at most 10 retries per run |
| drafts | Codex `gpt-6.2-sol` low | ≤ 20 × 2 arms × 1 run = 40 |
| judge | Claude `opus` | ≤ 50 × 3 = 150 |
| **total** | subscription routes only | **hard cap `--max-calls 400`** |

Both routes are subscription CLIs (`codex exec`, `claude -p`); nothing bills an API key. The tool never calls the
`agentos_eval` judge, which defaults to the owner's OpenAI key (#885). Claude's `total_cost_usd` (a notional figure
on a subscription) and Codex call counts and times are recorded in `calls.jsonl`.

## Privacy

- Every text sent to a model passes Still's credential-shape redaction first (`SECRET_PATTERN` and the stored-secret
  pass), as the replay harness did. Forgotten rows are excluded.
- Output folder `--out` must be outside the repository and the live profile; files are written `0600`.
- Stdout and `report.json` carry counts, run numbers and fixed codes only; topic names, owner words, page text and Work
  ids stay in the private folder.

## Result (2026-11-20, one owner's copy)

Counts only. Data from the live store copied read-only on 2026-11-20; 747 owner Works before the held-out window
(2026-11-10), 150 on or after it; 8 topics kept; 53 replay days; 36 held-out requests (26 regular, 10 weak), 34
labelled (2 labeller outputs failed twice and were skipped), 24 scored. 256 needed facts (106 essential), 155 traps.
Builder: 314–330 operations applied per run, 1–4 refused (`unknown_source`), no `rewrite`, no retries needed.

| Arm | needed (mean, range over 3 runs) | essential | misleading per request (all 34) | chars |
| --- | --- | --- | --- | --- |
| A today's brief | 40.2% (1.2) | 44.3% | 0.89 (0.21) | 8,462 |
| B + topic page | 54.3% (2.9) | 60.0% | 1.20 (0.29) | 10,823 |
| C + length-matched search | 42.6% (2.0) | 48.3% | 1.14 (0.24) | 10,737 |

- Paired needed-facts deltas (per request averaged over runs, topic-clustered bootstrap 95%): **B−A +14.3 points
  [+7.4, +24.7]**, B−C +12.0 [+6.9, +18.6], C−A +2.4 [+0.4, +6.7]. B−A is positive in every run (13.4–15.2) and in
  every topic; leaving any one topic out gives +11.3 to +16.2. 17 of 24 requests improve, none get worse.
- Misleading items per request rise with **any** added topic material: B−A +0.30 [+0.12, +0.53], C−A +0.25
  [+0.07, +0.47]. Labelled traps barely move (sum over runs A 44, B 51, C 47); the rise is mostly other misleading
  statements (A 47, B 71, C 69). In B they are mostly stale "latest state" items: what a list held earlier, a plan
  for a time already past, a count or a setting that later changed. Pages ended with about 200–220 state items against
  25–29 quoted standards and 45–51 decisions.
- Weak matches gain nothing (needed 33.9% → 35.5%) and get more misleading items (1.40 → 1.57).
- Exploratory lesson follow (run 1, 21 lesson/draft pairs per arm): A 19/21, B 19/21. Every arm carries the same
  lessons block, so `lesson_present` is at its ceiling (1.0) and tells nothing.
- Decision rule: **do not integrate** (`more_misleading`). The other four conditions held. `misleading_control`
  reads 0 because there were no control requests (`groups.control` is 0).
- Cost: 338 subscription calls of the 400 cap (Codex 200: find 1, build 159, drafts 40; Claude 138: labels with
  retries 36, judge 102), about 2.9 hours of summed call time, Claude notional USD 35.07. No API key was billed.

Reading: the page delivers needed facts that neither today's brief nor the same space of raw transcript does, beyond
run-to-run noise. Its misleading cost is the cost of adding old topic material at all, and in the page it sits in the
state section. A blind review agreed with the reading, and suggested comparing criterion 3 against C in a future
pre-registered rule rather than rescuing this one.

## Revision (#1046): pre-registered before any scoring

[#1046](https://github.com/Jongtae/Still/issues/1046) fixes what made the rule above say "do not integrate", then
re-runs. This section was committed before any revised page was built or judged.

### What changes

- **Order.** Standards, then decisions, then state, and the budget is filled in that order: a state item never
  pushes a standard or a decision out.
- **State items carry dates.** Each state item shows `as of <its source day>`. The builder may give it a `thread`
  (a short label for what it tracks) and an `expires` date (the last day it holds; empty when open-ended). At render
  time, for the request's day:
  - a state item whose `expires` is before that day is not shown;
  - of the state items sharing a thread (compared case- and space-insensitively), only the newest is shown;
  - the hidden count is stated in one line. Items stay on the page with their history; only the rendering hides
    them, so nothing is rewritten.
- ~~**No page for a weak match.**~~ Dropped before any scoring; see the amendment below. A page is attached
  whenever the request matches the topic, as in #1043 (`topic_pages.attach_page`). Without a page, B and C both
  equal A for that request.

### Amendment before scoring (same day, before any label, page or judgment of this split)

The first version of this section attached no page when the request matched only one of the topic's terms. After
`heldout` selected this split, its match-strength counts (no outcome, no request text) showed 17 of 19 regular
requests match one term. Checking #1043's tuning data by match strength then showed the gate rests on a mislabel:

| #1043 group (per-request B−A, averaged over runs) | requests | needed facts | misleading items |
| --- | --- | --- | --- |
| regular, one term | 19 | +11.8 points | +0.33 |
| regular, two or more terms | 5 | +23.8 points | +0.47 |
| "weak" extras, one term | 10 | +1.9 points | +0.17 |

The "weak" extras match one term just as most regular requests do. They differ by selection: they are the overflow
of the largest topics after the first six requests. So the term count does not mark a request a page cannot help,
and the gate would have removed the page from most of the requests that gained. The gate is dropped and nothing
replaces it. The other three fixes and the decision rule are unchanged. The weak group stays reported on its own.

### Requests: an uninspected time split

#1043's requests (2026-11-10 onward) were inspected while finding these fixes, so they are not scored again.
Requests after 2026-11-20 are still accumulating. This run scores **2026-11-01 to 2026-11-09**
(`--heldout-from 2026-11-01 --heldout-to 2026-11-10`): requests nobody read while tuning. Topics are found again
from requests **before 2026-11-01**, so the finder never sees them. Pages replay through 2026-11-09. The result is
labelled **provisional** until it is confirmed on requests after 2026-11-20.

### Decision rule (revised)

Integrate only if all hold, on needed facts over scored requests and misleading items over scored and control
requests:

1. B−A is positive in every run, its mean exceeds the larger of A's and B's run-to-run range, and its
   topic-clustered 95% interval excludes 0;
2. B−C is positive in every run, its mean exceeds the larger of B's and C's run-to-run range, and its interval
   excludes 0;
3. traps presented as current, per request, are not higher in B than in A (mean over runs);
4. other misleading statements, per request, are not higher in B than in A (mean over runs).

Each condition is reported on its own. Early stop: after two runs, run 3 is skipped and the result is "do not
integrate" when the #1043 early-stop holds or B−C is ≤ 0 in both runs.

### Budget

Same subscription routes and the same 400-call cap, in a fresh output folder with its own ledger
(`topics-2026-11-20-r2`). No drafts this time: lesson follow sat at its ceiling in #1043 and decides nothing.

| Step | Calls |
| --- | --- |
| find (Codex) | ≤ 3 |
| gold (Claude) | ≤ 50 |
| build (Codex) | ≤ 44 replay days × 3, plus at most 10 retries per run |
| judge (Claude) | ≤ 50 × 3 |
| **total** | about 365 at most, hard cap 400 |

## Limits

- A local observation of **one owner's** copy, judged by a model, not human labels; the labels come from the owner's
  own earlier words only.
- Contexts are graded, and drafts are written without tools, so lesson follow measures the reply as briefed, not a
  full Work.
- The replay uses the start-of-day cutoff for every arm, so same-day context the live brief would carry is absent in
  all three alike.
- FTS matches words: a topic with generic terms over-collects; one with unusual wording may miss Works.

## Files

| File | What it does |
| --- | --- |
| `topic_pages.py` | Pure page model: operations and their checks, snapshots, rendering, the leak check, scoring and the decision rule. No model, no store. |
| `topics_eval.py` | The commands above: snapshot, finder, builder replay, held-out, labels, contexts, drafts, judge, report. |
| `schema.json` | JSON Schema of a page and of the counts-only report. |
| `tests/test_1031_topic_pages.py` | Unit tests over synthetic stores and fake judgments: no live data, no model, no network. |
