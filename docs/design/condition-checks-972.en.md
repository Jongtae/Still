# Condition checks: the AI's own code watches, and wakes the AI only on a hit (#972)

## Status

This is the design proposal for [#972](https://github.com/Jongtae/Still/issues/972) (tracker [#973](https://github.com/Jongtae/Still/issues/973)). It changes no code.

The implementation is a separate change. It runs AI-written code with the owner's secret sets unattended, on a schedule, so it needs the escalated review in the [Development Constitution](../development-constitution.en.md#escalated-review): a Codex review of the final head, required CI on that head, and a merge by the owner. That change also amends the contract text listed under *Contract amendments*.

## Owner outcome

The owner asks for something that has to be caught within seconds or a minute, such as a price crossing a level and staying there, a slot opening, or a status changing. Today every slot of such a watch is a full model turn: the AI reads all the data again, reasons about it, and usually finds nothing.

With a check:

- The owner's AI writes the condition once, as code, and proves it runs.
- Still runs that code on a short interval, in the same sandbox as `code_run`, with no model turn.
- Only when the code reports a hit is the AI woken, with the code's output as the handoff. The data it decides on is seconds old.
- Quiet runs cost no model tokens. The owner sees how often the check ran, what it reached and when it woke the AI, and can stop it in one act.

The AI writes the condition (C16). Still only runs it, bounds it and reports it.

## Live evidence (2026-11-18)

- A line row repeating every 5 minutes ran 14 times that day (read-only query of the owner's store). Each run made 7 to 22 `code_run` calls over 50 to 326 seconds. Per #972, one run took about 4 minutes and about 870k input tokens to re-read 30 symbols. By the time the AI decided, its data was minutes old.
- `code_run` is capped at 120 seconds (`MAX_SECONDS`). A condition that has to hold for 180 seconds therefore needs several model round trips inside one Work.
- Both costs come from one cause: the model was the loop. The condition itself was a few comparisons the AI could have written as code.

## What exists

All paths are under `src/personal_agent/` at the main revision this document was written against.

| Piece | Existing mechanism | State today |
|---|---|---|
| Sandboxed code the AI wrote | `code_run.CodeRunner.run`: pinned sandbox-runtime (`RUNTIME_VERSION`), `python -I`, fresh temporary directory, home unreadable. Every request is decided by `_decide`: GET/HEAD to declared hosts, plus a set's owner-reviewed reads | Present, Work-only |
| Bounds of one run | `MAX_SECONDS` = 120, `OUTPUT_BYTES` = 16 KB, `MAX_HOSTS` = 16, `MAX_SECRET_SETS` = 4, `INSTALL_SECONDS` = 180 for packages | Present |
| Secret scope | `CodeRunner.secret_sets`: `header_or_body` values masked and substituted only toward the set's hosts; `in_code` values limit the run to the set's hosts; stored values redacted from output | Present |
| State changes from code | Claimed once through `ApiRequests` per Work (`_claims`, `_settle`). A money set needs the owner's exact approval. With `approvals=None`, every state change is refused unsent | Present |
| Saved code | `procedures.ProcedureLibrary` (Jongtae/agentos#1486): one library per **code-run secret set** (not per line), importable as `lib_<set>`, outcome-gated, versioned (`current`, `kept`, `forgotten`) | Present |
| State between runs | `<SET>_STATE_FILE`: a small JSON per secret set, encrypted between runs (`ApiCredentials.read_state` / `write_state`) | Present, per set |
| Wake-up at a later time | Preparations (#659): `due`, `start`, one request key per slot (`request_key`, `preparation:<id>:<slot>`). `start(..., immediate=True)` is the existing run-now path | Present |
| Repeating watch | `recurrence='window'` (#719): every `MIN_EVERY_MINUTES` (5) to 720 minutes, until a deadline (at most 31 days), at most `max_runs` runs | Present, 5-minute floor |
| Deciding whether a run matters | `when_needed` (#719): after every finished run, one DecisionEngine question (`watch_judgment`). It runs **after** a full model turn | Present, model-based |
| Daily bound on unattended work | `scheduled_work_budget` (#867): 300 admissions a day by default, charged in `start`, shared by all scheduled work | Present |
| Line integration | `line_refusal` (stop and unreadable brief), `stop_line`, `forget_record line:<id>`; #938 adds a forget cancel and pieces | Present; #938 designed, not built |
| Outside-content mark | `QuickStore.mark_outside_content`; `record_outside_result` marks a Work after a `code_run` result | Present, per Work |
| **Run the AI's code on a schedule without a model turn** | `docs/connect-code-run-design.en.md` *Non-goals*: "No background or scheduled runs" | **Missing.** This is the blocker |

The service loop (`_work_iteration`) runs every 0.3 seconds and calls `run_due_preparation`, so a due time is noticed within a second. The missing piece is not the clock. It is a way to evaluate the condition without starting a Work.

## Design

### D1. What a check is

A check is attached to a `prepare` row. It is the exact arguments of one `code_run` call, saved with the row:

- `code`, `hosts`, `secrets` and `timeout_seconds`, as `code_run` takes them;
- for each named secret set with a library, the **version id** of that library as it was imported in the proving run (D2). A later library save does not change a running check. The AI re-points the check when it wants the new version (D6);
- `every_seconds`, the check interval (D3);
- a digest of all of the above. The digest is the check's identity in records and in the owner's view (C7).

A check names no `packages` in this design. `code_run` installs packages into each run's fresh directory, which takes up to `INSTALL_SECONDS`, longer than any check interval. A reusable environment would widen the sandbox's read scope, so it is a separate decision (*Owner decisions*, 4). The standard library, the set's library and the vendor's plain HTTP API cover the cases seen so far.

The row keeps its goal: the brief the woken run receives. A check row uses the window columns: the window end (`until`, required, at most `MAX_WINDOW_SECONDS`) and `max_runs`, which counts **wakes**, not check runs. A check row has no regular Work slots. Its Works start only from a hit or from the owner's run-now.

### D2. How a check is set up

`schedule_preparation` gains an optional `check` argument with the fields of D1. It is accepted where scheduling is accepted today: in a Work the owner's own message started (`owner-request`, `owner-work`), in the daily plan's run, and, once #938 is built, in a qualifying line run. No new caller is opened.

The check must be **proven** in the same Work:

- the Work ran `code_run` with exactly these arguments (same digest);
- that run ended on its own with exit status 0;
- it made no state-changing request and asked for no approval.

This reuses the Work's own `code_run` record; there is no extra run. It is input validation (a functional execution condition under C17): a check that never ran once would fail unattended, where nobody can fix it. The proving run's output also shows the AI what a hit and a quiet run look like.

The owner is told with the existing scheduled notice and its cancel button (*Act, then tell*), with one added line (D7).

### D3. How a check runs

- **Runner.** One check thread, started with the service, separate from the work loop, because one sandbox run takes seconds. It asks one indexed query for due checks. Nothing due costs nothing. Checks run one at a time, store-wide, so check runs never compete with each other for the machine (#970 decides lanes for Works, not for checks).
- **Same sandbox, same scope.** A check run is `CodeRunner.run` with the saved arguments, constructed with `approvals=None` and the check's own record id instead of a Work id. Everything that bounds a `code_run` applies unchanged: `_arguments`, `secret_sets` (resolved from the **current** slot at each run, so revoking or editing a set takes effect at the next run), the intranet and work-mode rules (`_intranet_host`, rechecked per request), `_session`, `_decide`, output caps and secret redaction. The only differences:
  - the library comes from the pinned version, not `for_run`;
  - no library is proposed or saved, and no library hint is given;
  - with `approvals=None`, every state change is refused unsent (D8).
- **Timeout.** The run's timeout is at most the check interval and at most `MAX_SECONDS`, so a check never overlaps itself.
- **Interval and lateness.** The next run is due one interval after the previous run **finished**. A late check runs once, never once per missed slot: the overlap policy is *skip*, as for recurrences after downtime.
- **Before each run**, in the claim transaction, the runner rechecks the row's state, `line_refusal`, the window end and the day's wake count. A row that is not `scheduled` (paused, cancelled, stopped with its line, expired) runs no check.

### D4. Output protocol

The convention is cron's (a job that prints nothing has nothing to report) and the Monitor tool's (a line of output is an event):

| Run ends | Meaning | What happens |
|---|---|---|
| exit 0, nothing on stdout | quiet | Counted. Next run after one interval |
| exit 0, anything on stdout | **hit** | The AI is woken with stdout as the handoff (D5) |
| non-zero exit, timeout, or a run Still refused | failure | Counted; see D9. stderr is kept as a short diagnostic |

The Nagios/Monitoring Plugins exit codes (0 OK, 1 WARNING, 2 CRITICAL) were considered and not adopted: an uncaught Python exception exits 1 and would read as WARNING.

**Check state.** The run gets `CHECK_STATE_FILE`, a JSON object of at most 16 KB that the code may read and rewrite. It is kept like a set's run state: encrypted between runs, bound to the check's digest, redacted from output, and discarded when the check is replaced or ends. A hold duration ("below the level for 180 seconds") and edge triggering ("report a crossing once, not every run") are the AI's code over this state. That is the Prometheus `for` pattern without a Still rule for it. The set's own `<SET>_STATE_FILE` (a cached token, for example) is shared with Works that use the set, last writer wins, as between two Works today.

### D5. The wake and the handoff

- **One transaction.** A hit holds the check, starts the row's run through the existing `start(..., immediate=True)` with the hit time as the slot, and charges `scheduled_work_budget`, all in one transaction. The request key is `preparation:<id>:<hit time>`, so a replay finds the same Work. A crash before the commit loses the hit, and the next run finds it again: a check only reads, so the condition is still there.
- **What the woken run reads.** The row's goal, then the hit's stdout as returned by the sandbox (at most `OUTPUT_BYTES`, stored secret values redacted), with the check's digest and the time it ran. For a line row, `line_run_input` adds the line's brief and recent outcomes as today. The pattern is the harness's progress file: the brief says what to do, and the handoff says what the check just saw.
- **One wake at a time.** The check stays held while its woken run is active. It resumes when that run settles, unless the run paused, cancelled or replaced it.
- **The woken run is an ordinary scheduled run of its row.** It has that row's delivery mode (`when_needed` keeps the existing quiet judgment for its report), its payment rules and its sign-in rules. This design changes none of them (D8).

### D6. What the woken run may do with its check

A scheduled run may already cancel or pause its own preparation. With a check it may also **replace its own check**: new code, interval, timeout or library version, proven in that run as in D2, naming the same secret sets and hosts as the current check or a subset of them. A replacement never adds a set or a host. A trailing level or a new threshold after an event is therefore the AI's own next step, without the owner. Adding a destination needs a Work in which scheduling with new hosts is allowed today.

### D7. What the owner sees

- **At setup:** the existing scheduled notice with its cancel button, plus one line: how often the check runs, until when, which hosts and secret sets it reaches, and that it wakes the AI only when it finds something.
- **In Settings (준비해 둔 일) and `entrusted_line list`:** the check's interval and end, its last run time and result (quiet, hit or failure), runs and wakes today against the wake cap, the hosts it reached today with request counts, and the check's code, read-only. The existing pause, resume, cancel and run-now controls act on the row and therefore on the check.
- **In the line's periodic account:** check runs and wakes since the last account.
- **Information use:** the setup Work's record names the check's hosts and sets. Each woken Work's record names what the hit run reached. Quiet runs are counted per host on the row; they create no Work and no per-run Evidence.
- **No notice per quiet run, and no new notice type.** Failures use the existing preparation-blocked notice (D9). Budget exhaustion uses the existing `scheduled_work_budget` notice.

### D8. Payment, state changes and outside text

- **A check only reads.** It runs with `approvals=None`, so every request that is not GET/HEAD or an owner-reviewed read is refused unsent, including a state change to a set's host that a Work could send. Why: per-Work dispatch claims and the replay guard (`_claims`, `_effect_guard`) are what make a state change from code safe to retry (C8), and a check has no Work. A check that acted would repeat its effect every interval. The cost is one wake: the woken run, which has a Work, takes the action. This is a functional execution condition, not a new owner restriction.
- **The woken run's payment rules are unchanged.** It is a scheduled run of its row. Whether a woken run of an owner-accepted row may place an order unattended is the open #973 decision and #619. A self-set row (`SELF_SET`) never pays, as today.
- **Outside text (C17, case 3).** A hit's stdout carries what the vendor's API returned. The woken Work is marked before its first step (`mark_outside_content`, category `outside-content`, operation `check`), so the narrowed write rules of a marked Work apply. The handoff is presented under the same frame as `code_run` output, as data. It never supplies owner authority. It cannot widen the check either (D6: subset only).

### D9. Failure and recovery

- **Failures.** After 3 consecutive failures (the Kubernetes probe default `failureThreshold`), the check pauses and the AI is woken once with the failure as the handoff: exit status and the redacted stderr tail. This is the same path as a hit and counts as a wake. The AI usually fixes its own code (D6), as with a saved function that failed (#770). If that wake cannot run (wake cap, budget), the check stays paused and the owner gets the existing preparation-blocked notice.
- **A set that is not ready** (removed, values unreadable, `needs_setup`) is a failure with its own reason. It pauses the check at once, without three tries, and the owner is told. Resume is the existing control.
- **A forgotten library version.** `forget_record procedure:<id>` of the pinned version pauses the check at its next run with that reason. The check never switches silently to another version: forgetting does what the owner asked.
- **Restart.** The next check time is durable on the row. After downtime an overdue check runs once. A run cut off by a restart is simply not counted: it could make no state change, and its check state is written only when it ends.
- **Machine sleep and clock jumps** behave like downtime: one run, no replay.

### D10. Stop, forget and lines

- **Stop the row** (cancel or pause): no further check runs. A running check finishes its current run; its result is dropped if the row is no longer `scheduled` at commit.
- **Stop the line:** `stop_line` cancels the line's rows, so their checks end. `line_refusal` is rechecked before each run (D3).
- **Forget the line:** with #938 D4, forgetting a line cancels its pending rows in the same transaction, and their checks with them.
- **Remove the secret set:** the next run fails `needs_setup` and pauses (D9).
- **The window ends:** the check ends with its row, and the window-closed report rule applies as today.

### D11. Bounds

| Bound | Value | Mechanism |
|---|---|---|
| Check interval floor | **15 seconds** (owner decision) | New. Default 60 seconds. The 5-minute window floor stays for rows without a check |
| Run timeout | ≤ interval and ≤ `MAX_SECONDS` (120) | New rule over the existing cap |
| Concurrent check runs | **1**, store-wide | New. One check thread |
| Active checks | **10** (owner decision) | New shared allowance within `MAX_ACTIVE` (50), like `MAX_ACTIVE_DREAM` |
| Wakes per check per owner-local day | **24** (owner decision) | New. Holds even with the shared budget off. A wake is a run of its row, not a #938 piece, so it does not use the line's piece cap |
| Wakes over the window | `max_runs` | Existing column, counting wakes |
| Window length | `MAX_WINDOW_SECONDS` (31 days) | Existing |
| Daily unattended work | `scheduled_work_budget` | Unchanged. Every wake is charged at admission; quiet check runs are not, because they start no Work and no model. When the budget is spent, a hit is not woken and the check pauses until the next owner-local day, with the existing budget notice |
| Check state | 16 KB | New, like a set's run state |
| Code, hosts, sets, output | `code_run` bounds | Unchanged |
| Failures before pause | 3 in a row | New, from the probe convention |

**Should `MAX_SECONDS` be configurable per check? No.** A check is short by design: it reads, compares and exits. Duration conditions come from `CHECK_STATE_FILE` across runs, not from one long run. The 180-second hold from the live case becomes 12 runs at 15 seconds, each a few seconds long, instead of a two-minute run that blocks a model turn. A run that genuinely needs more than 120 seconds is not a check. Raising `MAX_SECONDS` for Work `code_run` calls is a separate question, and this design removes the case that raised it.

**Cost, for scale.** 10 checks at the 15-second floor are 57,600 sandbox runs a day, one at a time, and no model tokens until a hit. The live case's 14 runs used about 4 minutes of model time each.

## Authority and threat model

| Concern | Path | Hold |
|---|---|---|
| Secret leaves to a party the owner did not choose (C17 case 1) | Check code sends a set's value elsewhere | Identical sandbox: masked values are substituted only toward the set's hosts; `in_code` values limit the run to the set's hosts; hosts are fixed at setup; output redacted (D3) |
| Money or another irreversible effect without approval (C17 case 2) | Check code places an order or changes state | `approvals=None`: refused unsent (D8). The woken run keeps its row's payment rules |
| Outside text as instruction (C17 case 3) | API response says "buy now" or "add host X" | The woken Work starts marked; the handoff is data; a replacement check cannot add hosts or sets (D6, D8) |
| Runaway CPU | Many checks, short intervals, slow code | Interval floor, timeout ≤ interval, one runner, 10 active checks (D11) |
| Wake storm | Condition stays true; output on every run | One wake at a time; wakes per day; `max_runs`; shared budget; edge triggering in the AI's check state (D4, D5) |
| Silent spend | Wakes that report nothing | Each wake is charged and counted in the owner's view; `when_needed` judges the woken run's report |
| Vendor rate limits or lockout | High-frequency reads | Interval floor; pause after 3 failures; the AI chooses the interval within the floor |
| Changed code runs unnoticed | Library saved or forgotten after setup | Pinned version and digest; a forget pauses the check; a replacement needs a proving run (D1, D6, D9) |
| Outliving stop or forget | Check after row cancel, line stop or forget | Row state and `line_refusal` rechecked in the claim transaction before each run; result dropped at commit if the row changed (D3, D10) |
| Duplicate wake | Crash or replay around a hit | Hit, hold, start and charge in one transaction; slot request key (D5) |
| Stale state across replacement | Old hold state applied to new code | Check state bound to the digest, discarded on replacement (D4) |
| Intranet in work mode | Check reaches a private host | Same per-request intranet rule as `code_run` (D3) |
| Sandbox defect | sandbox-runtime escape | Unchanged exact pin; capability disabled on load failure, as for `code_run` |

The new restrictions in this design are functional execution conditions under C17: a proving run (input validation), no packages (install time against the interval), read-only checks (no Work to carry the replay guard), and the bounds in D11. None withholds owner data from the owner's AI or re-asks the owner.

## Contract amendments

The implementation change amends, in the same PR:

- `docs/connect-code-run-design.en.md`, *Non-goals*: "No background or scheduled runs" becomes "No background runs outside a check (#972)". A *Checks* section states D1 to D4 and D8: same sandbox, `approvals=None`, pinned library, no packages, no library saves.
- `docs/secretary-agency-contract.en.md`, *Accepted watch windows*: add a paragraph on checks. The check's interval floor, wake-only Works, the one-wake rule and `max_runs` counting wakes. *Act, then tell*: "A scheduled run may cancel or pause its own preparation, and nothing else" gains "or replace its own check within the same secret sets and hosts (D6)".
- `docs/request-path-rule-census.en.md`: record the read-only check (C8, functional, with its cost: one wake to act) and the no-packages condition. Record that they serve none of the three restriction cases: they are functional execution conditions.

## Existing solutions review

External first, from primary sources read on 2026-11-18.

- **Claude Code Monitor and background tasks.** A script's stdout is the event stream: each line becomes a notification that wakes the agent, and exit ends the watch. Output must be selective; a monitor that produces too many events is stopped. Every monitor expires and is re-armed. **Adopt** the core idea: output is the event, and silence is quiet (D4). **Adapt** the process model. A Monitor is a long-lived process; a Still check is a short periodic run, so secrets are never held in a sandbox that stays up, a restart loses nothing, and the existing per-run sandbox is reused. "Too many events are stopped" becomes one wake at a time and the wake caps (D5, D11).
- **cron with condition scripts.** cron mails a job's output only when it prints something (`MAILTO`); moreutils `chronic` prints only on failure. **Adopt** the convention: nothing printed means nothing to report (D4). cron itself has no overlap protection (`flock` is added by hand) and no failure threshold; Still adds both (D3, D9).
- **Monitoring Plugins (Nagios, Icinga, Sensu) exit codes.** 0 OK, 1 WARNING, 2 CRITICAL, 3 UNKNOWN. **Rejected** as the hit signal: a Python exception exits 1 (D4).
- **Kubernetes exec probes.** A command runs every `periodSeconds` (default 10, minimum 1) with `timeoutSeconds`; exit 0 is success, a timeout is a failure, and `failureThreshold` (default 3) consecutive failures act. **Adopt** timeout-as-failure and the threshold of 3 (D9).
- **Cloud alerting.** Prometheus alerting rules: a condition must hold `for` a duration (pending, then firing), and `keep_firing_for` prevents flapping. CloudWatch alarms invoke actions only when the alarm changes state, evaluate M out of N datapoints, and offer high-resolution periods of 10 or 30 seconds. **Adopt** the semantics, but as the AI's code over `CHECK_STATE_FILE`, not as Still rules. Hold durations, M of N and edge triggering are part of the condition, and the condition is the AI's (C16). Still keeps only one wake at a time.
- **Temporal schedules.** An overlap policy decides what happens when a run is still going at the next time; the default is to skip. **Adopt** skip (D3). No workflow framework is introduced, as in #938.
- **Anthropic, [effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents).** A session starts from a progress file and leaves the next session a clean state. **Adopt:** the woken run starts from the brief and the hit handoff (D5). The check itself is the harness's "do not spend a session on what a script can check".
- **Internal.**
  - Preparations and windows (#659, #719): durable due times, slot keys, `start(immediate=True)` and the window columns are reused (D1, D5).
  - `when_needed` (#719): the model-based quiet judgment runs after every full turn. A check moves the cheap pre-filter into the AI's own code and keeps the judgment for the woken run's report.
  - `code_run` (Jongtae/agentos#1380) and its library (Jongtae/agentos#1486): the sandbox, scope, redaction and library are reused unchanged, behind one seam (`approvals=None`, a pinned library).
  - `<SET>_STATE_FILE`: the model for `CHECK_STATE_FILE`.
  - `scheduled_work_budget` (#867), entrusted lines (#798) and pieces (#938), and the outside-content mark.
- **Decision: Adapt.** The change opens the existing `code_run` sandbox to scheduled runs of a proven check, behind the existing preparation row. It adds columns to `preparations` (the check, its state, its next time and its counters) and one runner thread. There is no new store, dependency or framework.

## Non-goals

- Checks that act. Orders and other state changes stay in a Work (D8).
- Any change to payment autonomy, `SELF_SET`, or #619 approvals for unattended runs.
- Packages in checks, or a reusable environment (*Owner decisions*, 4).
- Long-lived watcher processes, push subscriptions or WebSocket streams. A stream source could later feed the same hit path; that is its own design.
- Raising `MAX_SECONDS`.
- Parallel check runs.

## Acceptance

Acceptance is by scenario, not by test counts.

1. **Level held for a duration.** The owner asks to be told, or to have the AI act, when a quoted value stays past a level for 3 minutes. In an owner-typed Work, the AI writes a check using its set's library, proves it, and sets it at 15 seconds. Quiet runs cost no model turn. When the value has held for 180 seconds by the check's own state, the AI is woken with the check's output within one interval, reports or acts under its row's existing rules, and moves the level by replacing its check.
2. **A slot opens.** A check reads an availability endpoint every minute and prints only when a slot appears. The woken run reports it through the row's delivery.
3. **Invariants across both scenarios.**
   - No model call on a quiet run.
   - No state change from a check run.
   - No wake while a woken run of the same check is active.
   - No check run after cancel, pause, line stop, line forget or the window's end.
   - No check reaching a host or set outside its saved arguments.
   - Every wake is charged to the daily budget and counted in the owner's view.

### Negative tests the implementation must carry

- A check whose arguments do not match a successful `code_run` of the same Work is refused. So is one whose proving run timed out, exited non-zero, made a state change or asked for an approval.
- A check with `packages` is refused.
- An interval under the floor is refused. A timeout above the interval or above `MAX_SECONDS` is clamped.
- The 11th active check is refused.
- A check run's non-GET request outside the set's reviewed reads is refused unsent, including to a set's host and on a money set.
- A masked value never reaches a host outside its set. An `in_code` check reaches only its set's hosts. Stored values are redacted from the handoff and the diagnostic.
- A hit wakes exactly one Work. A replayed hit transaction finds the same Work. A second hit while the woken run is active does not wake.
- The 25th wake in one owner-local day is refused, including with the shared budget off. With the budget spent, a hit does not wake and the check pauses.
- The woken Work starts marked as outside content.
- A woken run's replacement check that adds a host or a set is refused.
- Three consecutive failures pause the check and wake once. A `needs_setup` set pauses at once.
- A forgotten pinned library version pauses the check. It never runs another version.
- Cancel, pause, line stop and line forget stop the check. A check result whose row changed during the run is dropped at commit.
- After a restart, an overdue check runs once.

## Owner decisions in this design

Proposed, for the owner to accept or change:

1. Check interval floor: **15 seconds** (default 60 seconds).
2. Wakes per check per owner-local day: **24**, each charged to `scheduled_work_budget`.
3. Active checks store-wide: **10**, run one at a time.
4. **Not part of this design:** packages in checks. A reusable environment per check changes the sandbox's read scope and needs its own review.
5. **Not part of this design:** whether a woken run of an owner-accepted row may place an order unattended. That is the open #973 decision and #619. Without it, a woken run that reaches an order reports the exact order, and the owner acts.
6. Recommendation, no decision needed: `MAX_SECONDS` stays 120 and is not configurable per check (D11).

## Implementation slices

Delivered so far: slice 1 without the check state ([#1021](https://github.com/Jongtae/Still/pull/1021)), then the check state of D4, shipped as `CHECKSTATEFILE`: without an underscore, no secret set's `<SET>_<FIELD>` variable, such as a set named `check`, can take the same name and the worker guidance sentence of slice 2. Replacing a check from the woken run (D6) and the owner view (D7) remain.

1. **Check runner and setup.** D1 to D5, D8 to D11: storage, the proving rule, the runner thread, the `CodeRunner` seam, the wake transaction, stop and forget, the negative tests, the contract amendments and the escalated review.
2. **Replacing a check from the woken run** (D6) and the worker guidance as message content: one general sentence in the `schedule_preparation` description on writing a check (silence is quiet, keep hold and edge state in `CHECK_STATE_FILE`, prove it first). It names no task, site or domain (C16).
3. **Owner view** (D7): the setup notice line, the Settings entry, and the counts in the line's periodic account.

Related: #659, #719, #798, #867, #938, #970, #973; Jongtae/agentos#1380 and #1486 (code runs and their library).
