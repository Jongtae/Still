# C2: entrusted work that comes due inside a sample (#887)

Status: design and frozen synthetic scenario evidence only. No harness support is
implemented here, no model was called, and no live scheduling/delivery claim is
made. This issue authorizes only this design and
`evals/scenarios/continuity-come-due.json`; the follow-on task implements the
harness. Nothing under `src/` changes. No commit or external submission is made.

## Outcome and boundaries

Extend the existing eight pre-due C2 situations with three short, self-contained
owner situations. Observe whether work actually runs, whether the owner receives
a useful result without asking again, and whether an exception-only check avoids
unnecessary interruption. Follow with an owner question after delivery, or after
settlement in the intentionally quiet case. The AI chooses how to schedule and
perform the task. The harness only sends owner words, waits, and records evidence.
It never creates a preparation, supplies an answer, repairs a schedule, approves
an action, invokes a runtime function, or changes the sandbox clock.

This follows the #827 measures and held-out rule in
`docs/outcome-experiments.en.md`, Constitution C15/C16, and the current C2 section
of `evals/README.md`. Existing eight scenarios remain byte-identical. Neither
scenario text nor evaluation control flow targets #799 L1/L2 behavior. No task,
site, skill, or scenario-id branch belongs in the implementation. The added
observation opportunity removes a measurement blocker; it does not implement an
owner request in Still code. The owner's configured decision AI and worker still
do the work. No new authority, real target, connector, payment, or destination is
introduced.

## Existing Solutions Review (offline)

Boundary: keep a real-clock sandbox alive until independently scheduled work
settles and, when requested, reaches the simulated owner; record enough typed
provenance to distinguish that output from an ordinary reply or run-now action.

The external/platform review precedes the design choice. Local Python docstrings
confirm `time.monotonic()` cannot go backward and `time.sleep(seconds)` accepts a
bounded interval. Installed SQLite is 3.54.0; Python's `sqlite3` URI read-only mode
is already used by this harness. `evals/requirements.txt` pins Inspect AI 0.3.271
(MIT) and the official OpenAI SDK 3.19.2 (Apache-2.0). The latter remains behind
Inspect's judge transport. Installed HTTPX 0.28.1 is unnecessary: the existing
client already uses stdlib `urllib.request`. No network or upstream release
check was possible or attempted; maintenance/release statements here are limited
to the checked-in pins and installed facilities, not current upstream health.

| Candidate | Decision and fit |
| --- | --- |
| Python `time.monotonic`, `time.sleep`, `sqlite3`, JSON; existing `AgentOSClient.get` over `urllib.request` | **Adopt**. Standard timers, read-only queries, serialization and HTTP suffice. Python/stdlib PSF licence and SQLite public-domain status are compatible with AGPL glue; no new dependency, network destination or packaging change. Bound each read as well as the outer timer. |
| Inspect AI task/log/epochs and existing worker pool | **Adapt** its already selected MIT evaluation stack, through `runner.run_turns` and existing run metadata. It supplies sample scheduling and logs, not a Still preparation/delivery predicate. Keep the wait inside its allocated sandbox slot. No new evaluator or scheduler. |
| Existing owner Settings `POST /api/preparations/request` with `operation: run_now` or `edit` | Supported, but **reject for this measurement**. `preparations_service.preparation_request` is used by `web/app.js:preparationAction`. `Preparations.run_now` calls `start(..., immediate=True)` with a current-time slot and bypasses unattended admission. This measures a second owner instruction, not autonomous due execution. Editing the due time is also a new owner intervention. Neither is a test-only hook, but neither answers C2's question. |
| C1 injected-clock traces, fixture clocks, direct `run_due_preparation(now=...)` | Keep for unit tests only. They do not move the real clock in the sandbox process or establish live local delivery. Monkeypatching the subprocess clock, DB writes and calling runtime internals are rejected. |
| A fixed sleep or fabricated time in a `note` | A sleep cannot establish settlement/delivery; a note does not advance the service clock. Reject as the success condition. |
| New generic `wait_due` variant plus observation checks | **Build**, not disguised as Adapt. Neither Inspect nor the current `say`/`note` schema can express a no-owner-message barrier or interpret Still's typed receipts. Build only the strict step schema and evidence predicate, using the facilities above. A general scheduler/retry/schema framework cannot supply these application-specific evidence joins; replacing the existing validator adds no missing capability. |

Repository search evidence: `rg` for `run_now`, `parse_due`, `invalid_window`,
`report_seconds`, `line_account`, `request_key`, `last_delivery`, `sent_at`, and
`proactive_observation` identified the symbols below. `runner.chat_turn` already
waits for delivery; `run_turns` already injects clocks; `work_measurements` and
`connector_decisions` already read only the sandbox DB using `mode=ro`.
`AgentOSClient.snapshot` and `state_diff` preserve new preparation rows;
`FakeTelegram` supplies the loopback Bot API transport. These internal seams
follow stdlib/Inspect facilities. Reuse them; do not build a second transport,
clock, store, scheduler, or model judge. Existing pytest fixtures use fake clients
and clocks. No dependency addition means no new supply-chain surface; retain
existing redaction before persistence/judging and use bounded, explicit DB
columns rather than copying whole job/event records.

## Runtime facts that constrain the experiment

- `quickstart_service.AgentService`'s background `work()` loop calls
  `run_due_preparation`, `run_one`, `deliver_one`, and `deliver_notification`, then
  waits 0.3 seconds. Execution duration and queue contention, not just that sleep,
  determine actual latency. `preparations_service.run_due_preparation` consumes
  delivery receipts and calls `Preparations.settle`; queue insertion is not a
  delivery receipt.
- `preparations.parse_due` has no positive minimum lead time. It accepts an
  RFC3339 time three minutes ahead, has a 120-second past grace, and clamps a time
  inside that grace to now. The owner says **“3분 뒤”**, and the AI translates it
  using the real clock. No date substitution, note, or hidden template is needed.
  Record `sent_epoch`, `sent_at`, sandbox zone and actual due time; a late or
  incorrect schedule is a result, not something the harness silently fixes.
- `normalize_window` requires a repeated check interval of 5–720 minutes, an end
  after the first due time, and a bounded span. `window_terms` accepts a report
  interval only with a window and `when_needed`, at least the check interval and
  an exact multiple of it. A three-minute repeating/report window would be
  refused as `invalid_window`. These scenarios request **one check**, no window,
  recurrence, closing report, or periodic account. A one-off `prepare` may use
  `delivery_mode=when_needed`; `create` restricts that mode to `prepare`, not to
  windows. The quiet case therefore need not use an invalid short window.
- Model-free compatibility inspection on this checkout called the real
  `parse_due` with now+180 and observed a 180-second lead; `window_terms` accepted
  a no-window/no-report one-off; `normalize_window(every_minutes=3, ...)` refused
  `invalid_window`. This is function/fixture evidence, not proof that an AI will
  choose those arguments or execute successfully.
- **`line_account` is not “belongs to entrusted work.”** In
  `preparations_service.preparations_status` it means exactly
  `origin == ORIGIN_LINE_ACCOUNT`. `ensure_line_account` creates a separate
  repeating `prepare`, initially `now + account_every_days * 86400`, with its own
  account goal. An ordinary preparation may have `line_id` and still have
  `line_account=False`. Do not request an account, retime it, or forge its flag
  to make the current scorer pass.
- Current `scoring.proactive_observation` accepts only a **new** account row in
  `diff.preparations`, a distinct `last_work_id`, `last_delivery='sent'`, and
  nonempty `last_result` (the API truncates it to 280 characters). Waiting alone
  cannot make ordinary scheduled results observable under that rule. The second
  task must extend observation from actual provenance, as specified below.

## Frozen situations and isolation

| ID | Split | Observable owner outcome |
| --- | --- | --- |
| `due-workshop-missing-material` | dev | At three minutes, compare supplied packing lists, identify the missing glue and deliver a useful packing order; then consolidate the final checklist. |
| `due-exhibit-quiet-check` | dev | At three minutes, compare two supplied identical display lists; retain the result without interrupting; answer when subsequently asked. Re-asking known facts or permission, progress pings and repeated lists are avoidable. |
| `due-radio-rehearsal-cue` | heldout | At three minutes, deliver a fictional rehearsal cue using a title without inventing a plot; subsequently revise its opening and closing. Different domain and wording from dev. |

All owner text is Korean, all targets are fictional, and all needed facts are
in the owner's words. There is no nonexistent web page to fetch and no harness
injection of a desired result. The preparation/worker must carry the supplied
facts forward itself. The quiet case is an intentional no-delivery opportunity;
its later question does not pretend an alert arrived. The two delivery cases
ask their follow-up only once the barrier observes delivery. On an incomplete
barrier, skip the follow-up rather than inventing a receipt or feeding a cue.

The JSON uses existing fields, existing `expect` keys, `channel: telegram`,
`exp-continuity` and `kind-comes-due`, plus the one new turn variant. No new
`expect` key is proposed. Existing `expect.preparation` still means only that a
row was created, not completion. The wait's own generic postcondition supplies
the new deterministic execution/delivery check.

Freeze version: **continuity-come-due/1** (design identifier; do not add an
unsupported top-level JSON key). C2's existing `fixture_support` hashes each
contributing file's sorted relative path and exact bytes. Its digest is the
executable freeze/comparison identity. Never tune against the held-out result.
If it motivates a change, move that case to dev in a new fixture revision and
write a new, differently worded/domain held-out case before another comparison.
Retain the prior results and digest; never pair across revisions. Human labels
must cover held-out cases and changed grades under #827's agreement rules.
Fixtures test opportunities, not a guaranteed baseline failure or candidate win.

## Minimal step contract

```
{"wait_due": {"after_turn": 1, "until": "delivered", "timeout_seconds": 420}}
```

`until` is `delivered` or `settled`. `after_turn` is a 1-based earlier **scenario
step index** whose variant is `say`, identifying the entrusting owner Work.
Indices continue to count wait steps, so these files have steps 1 (owner), 2
(wait), 3 (owner). Require a positive integer timeout of at most 600 seconds;
booleans are not integers. Reject unknown keys, missing keys, bad enum values,
forward references, references to waits, and mixtures with `say`, `note`,
`note_in_message`, or `from_world`. Retain the 12-step bound. Initially require
Telegram for a wait: web can show a saved result but has no equivalent evidence
of push delivery. Reject world/wait combinations. Unsupported channels must
fail validation rather than time out or claim delivery.

The barrier sends **no message**. `owner_message` must not stringify it or send
an empty turn. Existing note semantics are unchanged: a note is judge-only
unless `note_in_message=true`. These fixtures have neither; timeout status,
receipt ids, expected answers and wait controls never enter owner words or the
worker prompt. Avoid a general templating language. If a later fixture needs
HH:MM, that needs an explicit public-clock-only rendering contract, not hidden
judge notes relabelled as owner statements.

### Runner algorithm

1. Start a lossless bounded delivery collector and take the before snapshot
   before sending the referenced owner turn, not at wait entry. The service can
   start/deliver due work while the owner's initial Work is still finishing.
   Retain call sequence numbers and wall timestamps independently of polling.
2. On the barrier, start one monotonic deadline (`entry + timeout_seconds`).
   Read `/api/preparations` and a narrowly scoped read-only snapshot of the
   sandbox's `private/quickstart.db`. Identify new preparations whose
   `created_from` equals the referenced owner Work, with a due slot within the
   sample's observation horizon. Use creation/slot event evidence if the row has
   already advanced; do not depend on seeing `scheduled` in one lucky poll.
   Select exactly one candidate for this minimal contract. Never match Korean
   goal text, target names, `line_id`, or scenario ids. Extra far-future rows are
   recorded but cannot satisfy the barrier. Multiple in-horizon candidates are
   an explicit `ambiguous_preparation` shortfall, not a first-row guess. A
   missing/late preparation remains visible at timeout. No accepting, editing,
   run-now call, clock mutation, or DB write is permitted.
3. Poll at the existing two-second interval. Each HTTP/DB operation is bounded
   by the remaining monotonic budget (short SQLite busy timeout, fresh read
   transaction; do not hold a snapshot across sleeps). Sleep no longer than the
   remaining time. Never call `client.wait` with its independent 900-second
   allowance inside this barrier. An HTTP request's timeout must also be
   clipped; a 30-second default must not overrun the declared cap.
4. Join the candidate to its Work and typed start/settlement events. The Work
   must be different from every scripted owner Work and created during this
   sample. Require actual settlement, not merely `status != running`: a queued,
   sending, pending resume, or awaiting-owner Work is not completed. Read
   settlement outcome and `last_decision` as observations, not success labels.
   A failed/refused run ends with a failure record. Budget refusal, no run,
   usage-limit, missing receipt and observation/transport error are distinct.
5. `until=delivered` requires both settlement and a positively observed delivery
   of this run's result. `until=settled` permits a successfully retained quiet
   result; still collect any notifications. If settlement queued a notification,
   wait for its terminal delivery disposition. Include a **20-second post-settle
   drain**, reusing `DELIVERY_WAIT_SECONDS`, after the required disposition to
   capture immediate duplicate/noise messages. The drain is inside the 420-second
   cap. It must not end early merely because no message is present. This is
   bounded evidence of silence, never a promise of indefinite silence.
6. Store a wait record even on failure. On timeout freeze the latest evidence,
   mark `timed_out=true` with the unmet stage (`no_preparation`, `not_started`,
   `not_settled`, `not_delivered`, `drain_incomplete`), and stop later scripted
   steps. Preserve observed partial deliveries for judgment. Never turn a
   promised report into an observed one. A usage limit follows the existing
   stop/no-judge path; other behavioral shortfalls remain judgeable on the
   available transcript. An observation transport/DB error is infrastructure,
   not a product score. Sandbox cleanup remains the existing context manager.

### Evidence joins and run record

Use API rows for the owner-visible state and read-only DB evidence for provenance:

- `preparations`: id, created_from, creation time, due/last slot, kind, state,
  run_count, last_run_job_id, delivery_mode, last_decision/reason, prepared_at.
- `tool_events`: allowlist only relevant preparation start/settlement facts and
  `scheduled_work_budget` admission. For `prepare`, the `admitted` event ties
  preparation id and slot to a Work; `Preparations.start` emits it only for a
  scheduled (not immediate/run-now) admission. This proves unsolicited origin
  without relying on text. Pair it with the preparation start event and Work
  id; do not mistake a settlement event for start. Read the dedicated events
  directly, rather than losing them to `compact_events`' first-40 cap.
- `jobs`: id, request_key, created, status, delivery; no unbounded payload dump.
  `request_key` associates the slot, but by itself does not distinguish run-now.
- Delivery of a Work reply: `jobs.delivery='sent'` plus
  `telegram_turns.reply_message_id`/chat id matched to the stand-in's successful
  response message id. For a conditional result: `telegram_notifications`
  joined by Work id, `kind='preparation_notify'`, `state='sent'`, non-null
  `sent_at`, and matching message id. A notification can be delivered while
  the associated Work has `delivery='none'`; do not require `sent` on that Work.

Enhance `FakeTelegram._call` to retain returned message ids and a stable call
sequence under its existing lock; expose them through `chat_since` or a narrow
collector helper. Preserve successful send/edit text, timestamp, method, chat
and message identity. Match receipt ids, not `_same_reply`'s 60-character fuzzy
prefix. Keep distinct sends even with identical text; do not count control-only
edits or typing as messages. Preserve meaningful text edits as ordered observed
versions, and never call an unacknowledged attempt a delivered message. If a
multipart result cannot be completely linked, mark the excerpt/coverage limit
explicitly; do not credit uncollected bodies or guess their linkage.

Add `run.due_observations` containing, per barrier, its step/source indices,
start/end wall times, elapsed monotonic time, timeout, observed stage/reason,
preparation/Work ids, due slot, start and settlement evidence, result status,
delivery disposition and message references. Retain `last_result` as an API
excerpt labelled as such, but give the judge the actual delivered text (bounded
by the existing `ANSWER_CHARS`, with a truncation flag). Capture all owner-facing
messages during the interval, including redundant questions and progress;
not every message is a proactive **result**. Unknown-origin messages can still
be judged as interruptions but cannot prove successful scheduled delivery.

Keep `diff.preparations` genuine: `state_diff(before, final_snapshot)` retains
new rows and their real `last_work_id`, `last_delivery`, `last_result`,
`line_account` values. Never overwrite `line_account` or `last_delivery` to
satisfy a scorer. Keep explicit receipts separately so a final row edit cannot
erase an observed delivery. Old-row changes and unsupported origins remain
unsupported in this first version. All these scenarios start fresh.

Extend `scoring.proactive_observation` with a second evidence route: a recorded
scheduled admission, distinct Work, settlement and linked successful delivery
with nonempty observed text. Keep the existing account route for historical
records, deduplicating by preparation/Work/delivery identity if both cover it.
Builtin reminders lack that admission event; this first extension must not
infer provenance for them. These fixtures request actual preparation work, not
pure alarms. A candidate choosing an unsupported route is reported as such;
never compensate by forcing an internal route or changing the fixture. A later
reminder extension would need its own typed proof.

### Transcript and deterministic scoring

Preserve one run-record entry per scenario step. A wait entry has
`kind='wait_due'`, no `say`, no owner task id, no fabricated answer, its observed
status, timing, events and delivered messages. Render it as a **harness wait,
no owner message**, followed by `t2.b1`, `t2.b2`, etc. Reuse existing indexed
message/ask shapes rather than adding a second judge-output schema: an ask during
step 2 has `turn: 2`, and an interruption identifies its actual `t2.bN`. There is
no `t2` answer. For waits, explain that `bN` denotes messages during the wait,
not messages before an owner-requested answer.

One stable delivery id must appear exactly once in the transcript. If delivery
occurs during the preceding owner Work, keep its chronological `t1.bN` location
and let the due receipt reference it. Deduplicate it from the wait collector;
never replay it as a new message at the follow-up. Include real timestamps so a
result before the intended due time is assessable. Do not discard ordinary
owner replies while doing this. The collector must also capture unprompted
messages that fall between `chat_turn`'s final reply and barrier entry.

`deterministic_checks` must restrict ordinary answer/owner-status checks to say
steps; otherwise the deliberately answerless wait fails `delivered` accidentally.
Add a generic `due_completed` check for scenarios containing waits, true only
when all wait postconditions and the bounded drain complete with successful
execution. A no-output quiet run can pass this check, but no run cannot. A
required delivery missing at timeout fails it even though proactive usefulness
is null. No new `expect` key is needed. `CHECK_NAMES`/report denominators must
include this check only for applicable scenarios (others unscored, not false).

No-output quiet success has proactive usefulness `not_observed`, **not 2**.
Unsupported or missing delivery also has no proactive grade, with its distinct
shortfall/coverage reason retained. A delivered useless result may score 0;
absence cannot be used to infer harmful output. The judge evaluates relevance
and content, not verified completion. The quiet case's later requested answer
is not proactive output. Required approvals remain a separate count and are
never treated as avoidable; this task adds no auto-approval. Questions are not
also interruptions. Keep per-message attribution so an ask in one message does
not erase a separate redundant progress ping in the same wait.

## Runtime and cost budget

Each new sample has two owner messages, one barrier and at most one intended
scheduled execution. All waits use a 180-second requested lead, 2-second polling,
a 20-second drain, and a 420-second hard barrier cap. Time spent on setup,
Judgment AI qualification, normal owner Works, upkeep and the two eval judge
calls is separate. The preparation may already have become due by wait entry.

Let A be seconds from sending the entrusting message to barrier entry and E be
due execution plus settlement/delivery latency. Approximate added barrier time:
`max(0, 180 - A) + E + 20`, capped at 420. For planning only, A=0–60 and E=30–90
gives roughly **170–290 seconds (3–5 minutes) per new sample**, at most **7
minutes**. Real contention can consume the cap; these are estimates, not ETAs.
The quiet check still runs and settles, so it has a similar budget.

Three additions × three epochs = **9 extra samples**. At `--instances 3`, their
wait component is about **9–15 wall minutes** under balanced occupancy, with
**21 minutes** for three all-timeout waves. Aggregate occupied-slot time is
about 27–45 minutes, at most 63 minutes. The full sweep grows from 24 to 33
samples (7 dev + 4 held-out scenarios, each three times). These increments do
not include the extra samples' owner execution/setup/judging time or guarantee
Inspect's scheduling efficiency. Serialized placement/stragglers can approach
63 minutes of wait contribution; compare actual elapsed and occupancy. A paired
baseline/candidate run doubles sample counts and costs; no live run is authorized
by this design.

Added **eval judge calls per sample: 0**. C2 still makes one rubric call and one
measures call (2 total) after collecting the complete transcript. Nine extra
samples add **18 calls** to a three-epoch arm, taking 48 to 66 calls if all are
eligible; both arms add 36. Larger transcripts add tokens, bounded by the
existing judge budget/redaction. Runtime decision/worker/notification judgments
are separate from these eval judge calls, may increase, and are not claimed
free. Subscription worker usage remains unavailable, never zero. Do not add a
judge call per poll or delivery. Usage-limit early stopping and budget refusal
must remain intact.

## Implementer's bounded list

The names marked **new** are proposed eval-only helpers, not runtime APIs.

| File | Functions / exact work |
| --- | --- |
| `evals/agentos_eval/scenarios.py` | `_TURN_KEYS`, `validate`, `owner_message`: add the strict disjoint wait variant, after-turn validation and Telegram-only scope; retain current say/note/world behavior and supported expect keys. Loader auto-discovers this file. |
| `evals/agentos_eval/runner.py` | `run_turns`, `run_scenario`: dispatch waits without sending, start capture before entrustment, preserve failures and skip dependent follow-ups, keep final diff. **New** `wait_due` and `due_evidence` helpers with injected monotonic/wall/sleep/readers; pass sandbox data path to the read-only observer. `work_measurements` includes deduplicated due Work ids as well as owner ids; guard missing wait task ids. `usage_limited`/`StopOnUsageLimit` must see limits in due Works. |
| `evals/agentos_eval/client.py` | Reuse `get`, `snapshot`, `state_diff`; add bounded per-call timeout plumbing needed by the barrier rather than a second transport. Do not change snapshot field meanings. |
| `evals/agentos_eval/fake_telegram.py` | `_call`, `chat_since`, `calls_since`: expose successful response message identity and stable sequence, preserving existing callers. Capture sends/meaningful edits and avoid duplicate collector consumption. |
| `evals/agentos_eval/scoring.py` | `proactive_observation`, `_transcript`, `message_ids`, `parse_measures`, `required_approvals_events`, `deterministic_checks`, `CHECK_NAMES`, `combine`: consume typed due receipts, render wait messages, scope ordinary checks to owner steps, add `due_completed`, preserve no-double-counting and null behavior. Carry wait evidence/latency in score metadata. Keep `score_run` at two judge calls. |
| `evals/continuity/initiative-rubric-v2.json` (**new** in implementation) | Keep four measures, scales and output keys; clarify wait-step indices, actual message provenance, bounded silence, and exclusion of requested follow-ups. Leave v1 frozen. Change `continuity.DEFAULT_RUBRIC` to v2; do not silently reinterpret old v1 logs. |
| `evals/agentos_eval/report.py` | `records_from_samples`: preserve wait outcomes, due observations and execution-vs-delivery timing, including error samples. Do not lose them through the current limited turn-field projection. |
| `evals/agentos_eval/continuity.py` | `fixture_support`, `_group`, `build_report`, `render_markdown`, `compare`, `labels_template`: keep both fixture files hashed; expose wait dispositions/coverage and applicable checks per split, separate wait duration from ordinary response latency, and carry receipt/message references for labels. Record an observation-format version and refuse cross-version comparisons, alongside existing fixture/rubric gates. |
| `evals/agentos_eval/cli.py` | `run_continuity`: retain tag-based selection, fresh folders, explicit judge and existing timeouts; include observation-format metadata. No new wait CLI flag is needed: timeouts are frozen in the fixture. |
| `evals/README.md` | C2: 11 total/7 dev/4 held-out, the real-clock barrier, supported Telegram provenance, quiet vs missing output, seven-minute cap and cost, new rubric/format identity, unchanged held-out policy. Explain that the #827 “nothing comes due” description still applies to the original eight, not this extension. |

C2 JSON/Markdown must show, per epoch, whether a due slot was created, started,
settled, delivered, quiet, failed, timed out, or unsupported, with observed times
and shortfall reason. Preserve failed waits in deterministic denominators and
show not-observed counts separately from unscored/infrastructure samples. Show
observable-result counts per split, so a candidate cannot look better merely by
losing deliveries from the proactive-score denominator. Coverage loss prevents
a `better` verdict; label missing or incomplete observation as partial/uncertain
rather than an improvement. Existing human disagreement, spread, stopped-sweep,
and judgment-unavailable rules remain. Report a successful quiet check separately
from a missing run; neither supplies a numeric proactive grade.

### Fixture-only acceptance tests for the implementation task

No live models, wall-clock minutes, real Telegram, network service or owner data.
Use injected clocks/clients and temporary SQLite stores. Add
`tests/test_eval_come_due.py` for the barrier/evidence joins; extend the existing
`tests/test_eval_loop.py`, `tests/test_eval_fake_telegram.py` and
`tests/test_continuity_report.py` for their respective boundaries.

- Schema accepts the frozen file and old variants; rejects bad bounds/bools,
  unknown/mixed keys, wrong channel, world mixing and invalid references. Verify
  2 dev/1 held-out additions and unchanged existing eight; test the held-out
  content structurally only, never tune answer phrases against a worker.
- Fake-clock pending → admitted → running → settled → delivered trace: zero
  owner sends during wait; exactly one follow-up after completion; actual due
  and timestamps retained. Test delivery before wait entry and between polls.
- Quiet settled trace: no message needed; wait includes drain, then follow-up;
  proactive null with `due_completed=true`. Failed or never-started work cannot
  pass merely because the chat is silent. Unexpected delivered/noisy text is
  retained for the judge rather than filtered using the desired outcome.
- Every timeout stage, an out-of-horizon due time, ambiguous multiple rows,
  pending delivery/resume, observation error and usage limit: truthful reason,
  no dependent follow-up, no cap overrun, appropriate judge/early-stop behavior.
  Advance the fake wall clock backward/forward; monotonic budget still bounds
  the wait. Blocked HTTP/SQLite reads must also respect the remaining budget.
- A temporary real SQLite schema/reader test joins genuine-shaped start,
  admission, settlement, Work, turn and notification rows; do not mock away the
  serializer/query boundary. Wrong Work/preparation/slot/message id, old rows,
  missing admission, run-now-only events, queued/unknown notifications and an
  owner Work falsely offered as a scheduled result must fail observation.
- Test Work delivery and conditional-notification delivery separately; the
  latter can have `jobs.delivery='none'`. Do not promote `last_result` alone,
  text similarity, empty text, row existence or `line_id` to delivery evidence.
  Preserve `line_account=False`; retain existing account-route regression tests.
- Stand-in response identity round-trip, meaningful edit ordering, distinct
  repeated sends, multipart/unknown coverage, and collection deduplication.
  Transcript lists each delivered message once with valid `tN.bJ` labels; wait
  never renders as OWNER or as a fake answer. A later requested reply cannot
  become proactive. Include a redundant ask and a separate repeated ping so
  both relevant counts can move without counting a question twice.
- Stub judge replies, using evidence-backed positive and negative examples,
  demonstrate useful output 2, thin output 1, harmful/noise output 0 and absent
  output null. Required gates stay separate. `score_run` makes two calls for a
  complete sample, no per-wait calls, and no calls after a usage limit.
- Reports round-trip wait evidence; old scenarios do not fail a nonexistent wait
  check; timeout/unsupported coverage cannot improve a verdict. Fixture/rubric/
  observation-version mismatches refuse pairing. Both splits, three epochs,
  label references, spread and uncertainty survive without pooling splits.

Suggested focused command after implementation:
`PYTHONPATH=src pytest -q tests/test_eval_come_due.py tests/test_eval_loop.py tests/test_eval_fake_telegram.py tests/test_continuity_report.py`.
Tests establish fixture behavior only. Any subsequent budgeted live local run
must report the exact revision, worker/judge, fixture/rubric digests, observed
coverage and timeouts; do not change these frozen situations to rescue a result.

## Open risks and current deliverable limits

The existing loader intentionally rejects `wait_due` until the second task lands;
this design-only change is not a runnable C2 release. No loader fallback may
silently ignore the waits. Queue load and AI schedule interpretation can exceed
the seven-minute cap. The ordinary/conditional delivery joins and complete
message identity coverage need the specified model-free integration test before
live use. A one-off `when_needed` path is accepted by code, but actual AI choice,
quiet judgment and follow-up recall have not been observed. None is guaranteed.
If a runtime limitation is exposed, report it as a finding on its own scope;
do not add a task branch or change `src/` in the harness task. Twenty seconds of
drain cannot establish the absence of all later interruptions. Existing-row
updates, builtin reminder provenance and unlinked multipart bodies remain
explicit coverage limits, not silent success.
