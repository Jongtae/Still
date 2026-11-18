# Bounded concurrent Work lanes (#970)

## Status

This is the design proposal for [#970](https://github.com/Jongtae/Still/issues/970). It changes no code.

The implementation is a separate change in slices (*Implementation slices*). Slice 2, the first that runs tool-capable Works at the same time, needs the escalated review in the [Development Constitution](../development-constitution.en.md#escalated-review): a Codex review of the final head, required CI on that head, and a merge by the owner. The reason is under *Escalated review*. Concurrency does not widen any Grant, scope, destination or approval.

## Owner outcome

- A message the owner types is never queued behind scheduled runs. It starts within one work-loop tick when no other Work of the owner's is running in that conversation.
- A scheduled run starts at its slot, not after whatever Work happens to be running. A long mail or calendar Work no longer holds back unrelated scheduled runs.
- Two Works that could step on each other never run at the same time: runs of the same entrusted line, Works of the same request chain, owner Works in the same conversation unless the owner's AI judged the new message independent, and Works that need the owner-logged-in browser profile.
- Nothing runs twice and no consequential effect happens twice because Works now overlap (C8).

## Live evidence (2026-11-18)

Read only, from the owner's store with `sqlite3 -readonly`. Counts and durations only; no message content. The day is the owner's local day.

| Observation | Value |
|---|---|
| Owner-typed Works | 4. Queue wait (`response_timing` stage `queue_wait`): mean 142 s, max 543 s. Ahead of the 543 s one were two scheduled runs, queued 2 s before it; they ran one after the other for 3.6 and 5.3 min |
| Scheduled runs (`preparation:` request keys) | 20. Queue wait mean 11 s, max 221 s. Active time mean 5.4 min, max 9.5 min. 17 of 19 finished runs took over 4 min |
| The five-minute row | 14 runs. Three slots started 193 s, 234 s and 483 s after their due time. Gaps between starts of 10, 13 and 14 min |
| Side answers (#17) | No `side_answer` event in the store's whole history. 40 `owner_steer` receipts |

The queue-wait stage under-reports the scheduled delay. A slot's Work is only created by the preparation tick, and the tick runs on the same thread as the Work (`_work_iteration`: `run_due_preparation`, then `run_one`). While a Work runs, no tick runs, so a late slot shows up as a late start (193 to 483 s), not as queue wait.

Two causes are mixed in the gaps:

1. **The single worker.** Slots started late because another Work held the work loop. Lanes remove this.
2. **A run longer than its interval.** A run of 7.4 min on a 5-min row skips one slot, because recurrence advances from *now* after the run (`preparations.py`, module docstring). Lanes do not change this, and should not: runs of one line stay serialized (D3). Long work belongs in pieces (#938).

## What exists

All paths are under `src/personal_agent/` at the main revision this document was written against.

| Piece | Existing mechanism | State today |
|---|---|---|
| The work loop | `AgentService.start` runs one `work` thread. Each `_work_iteration` calls, in order, `run_due_preparation`, `run_due_qualification`, `run_one`, `deliver_one`, `deliver_notification`, the sweeps, `process_browser_logins`, `start_session_keepalive`, then `run_owner_model_upkeep` | One thread does ticks, Works and deliveries |
| The worker | `WorkExecutor._run_one` holds `service.worker_lock` ("One tool-capable worker: ordering is shared across all connected channels") for the whole Work | **The blocker** |
| The claim | `_claim_queued_work`: one `BEGIN IMMEDIATE` transaction, the oldest `queued` row (`ORDER BY j.created`), checks (active parent in `WorkLedger.request_chain`, `run_refusal`, open album, forgotten request, task-card grace), then `status='running'` | Single claimant. No owner priority |
| Chain ordering | A Work whose request chain has an active parent is not claimable | Present; a keyed serialization in all but name |
| One run per slot | Request key `preparation:<id>:<slot>` is `UNIQUE`; the row is `running` until `settle` | Present |
| Answers beside a running Work (RESPONSIVE-01, #17, #332) | `consider_steer` (poll thread) finds the latest `running` Work in the same chat and channel; `judge_steer` asks `steers_running_work`. Yes: the message is handed to that Work (`add_steer`). No: `start_side_answer`, one **tool-less** Main AI call that commits `WHERE status='queued'`. The claim skips a message in `side_answer_flight` | Present. Tool-less only, one at a time, never ran live |
| Owner direction on #17 (2026-10-26) | One CLI session per running Work; routing by Work id; "replace the global `worker_lock` with a small concurrency limit"; engine-agnostic; limits are told; the Judgment AI picks steer, new Work beside, or wait | Recorded, not built. This design builds it |
| Off-worker judgments | #969 early judgments (`early_judgment_spawn`), steer judgments (`steer_spawn`), owner-model upkeep (`owner_model_flight`, #805) | Already run CLI and API calls next to the worker |
| Per-Work context | `current_work_id` and `_work_local.budget` are thread-local (`threading.local`, #826); `work_decision_scope` sets both | Ready for several threads |
| Per-Work live state | `live_workers`, `live_steps`, `live_agents`, `presence`, `steer_closed`, `steer_judging`, `_native_intake_barriers`: keyed by Work id | Ready |
| Early plan | `service._early_plan`: one plain attribute, set in `_route_work`, read in `_prepare_worker_turn`, cleared in `run_one` and `_settle_work_judgments` | **Assumes one worker** |
| After-work sweep | `sweep_after_work` takes `worker_lock` so it never runs a tail the running Work is still completing | **Assumes one worker** |
| Browser profile | `BrowserProfile`: one WebKit or Chromium worker, one encrypted jar, one holder lock. `driver_factory(work_id)` acquires on the first browser step; `close_browser` releases at the end of the attempt. A second holder gets `browser_busy` at once (`BUSY_WORK_TEXT`); only a keep-alive is waited for (`KEEPALIVE_YIELD_SECONDS`) | A keyed lock already, fail-fast |
| CLI turn | Each attempt runs in a fresh `turn-` `TemporaryDirectory` (`bounded_execution.py`), with its own MCP bridge and its own `BrowserRelay` (fresh `mkdtemp`, 0600 key, one Work). Codex reads the owner's `CODEX_HOME` itself; Claude Code gets its token per run | Nothing shared between Works |
| Credential refresh | Gmail and Calendar token refresh under module `_OAUTH_LOCK`; peer Codex auth copy under `_PEER_CODEX_AUTH_LOCK` (#673) | Thread-safe |
| Approvals | A Work that needs the owner parks (`awaiting_*`, browser step requests) and frees the worker. Settlement is compare-and-set (`BEGIN IMMEDIATE`, exact action, owner generation, `_local_approve_lock`). Unattended runs ask nothing (#619) | No worker is held while the owner decides |
| Restart | `QuickStore.recover` marks every `running` Work `interrupted` and fences their ledgers (`WorkLedger.fence_restart(db, running)`); nothing is re-run | Already handles several running Works |
| Store | SQLite, a new connection per `store.db()`, `timeout=10`, default rollback journal | One read in this review met `database is locked` while Still was writing |
| Idle-only work | Session keep-alive and owner-model upkeep start only with no `queued` or `running` Work | Unchanged; conservative |

## Design

### D1. A dispatcher and N lanes

- The `work` thread becomes the dispatcher. It keeps running every tick of `_work_iteration`, but it never runs a Work itself. Preparation ticks, deliveries, notifications and sweeps keep running while Works run.
- The Works run on a `concurrent.futures.ThreadPoolExecutor(max_workers=N)`, named `agentos-work-lane`. The dispatcher claims a Work, then submits `run_one(job)` to a free lane. Claiming stays on one thread, so there is still a single claimant.
- The pre-claim steps in `run_one` (`settle_native_handoffs`, `resume_ready_native_work`, `resume_action_work`, `resume_settings_work`) move into the dispatcher tick. They are compare-and-set already; they now run once per tick instead of once per Work.
- `worker_lock` is removed from `_run_one`. A lane registry (Work id → future) replaces it where one-worker exclusion was used: `sweep_after_work` skips a Work id that is still in a lane.
- `N` is the owner setting `work_lanes`, default **3** (owner decision). `1` keeps one Work at a time, which is the rollback switch. The owner-first claim order (D2) still applies with 1.

### D2. Owner Works first, with a reserved lane

- **Owner class.** A Work is in the owner class when the root of its request chain is `owner_typed=1`. Retries, location continuations and approval or settings resumes of an owner Work stay in the owner class. Everything else is background: preparation runs, look-back and daily-plan rows, line pieces, qualification and bare API requests.
- **Order.** The claim reads owner-class rows first, then background rows, each by `created`.
- **Reserve.** Background Works may hold at most `N − 1` lanes. With the default, two scheduled runs can run at once, and one lane is always free for the owner.
- **Effect on the live cases.** The 543 s wait would have been one tick. The 9.5-min mail and calendar Work would have held one lane, not the work loop.

### D3. Keys: what stays serialized

Each candidate gets a set of keys from its own row. No model is asked and no message text is read. The claim checks them inside its existing `BEGIN IMMEDIATE` transaction, against the Works that are `running` (at most `N` rows). A candidate whose key is held is skipped, and the claim takes the next candidate. Nothing is held in memory, so a restart cannot leave a stale lock: `recover` turns the running rows into `interrupted`, and the keys go with them.

| Key | Applies to | Held by | Why |
|---|---|---|---|
| `chain:<root>` | Every Work | An active parent in `WorkLedger.request_chain` | Existing check, unchanged |
| `line:<line_id>` | A preparation run whose row has a `line_id`; otherwise `prep:<preparation id>` | A running Work of the same line or row | Two runs of one line never act at once. A trade check cannot overlap the next check of the same position |
| `conv:<channel>:<chat_id>` | Owner-class Works | A running owner-class Work of the same conversation | Owner-visible order inside one conversation, and owner-act settlement, stay as today. Released for one message when the owner's AI judged it independent (D6) |
| Browser profile | A step that needs the owner-logged-in browser | The profile holder (`BrowserProfile._acquire`) | One profile, one jar. Taken on the first browser step, not at admission (D5) |

**Not keys:**

- **The owner-approval surface.** An approval never holds a lane. The Work parks, and settlement is the existing compare-and-set on the exact action. Several open items in one conversation are already read together by the owner-act judgment (#172), which settles only a judged answer to one named item. Unattended runs put nothing in front of the owner (#619).
- **An account connection.** Token refresh is already serialized (`_OAUTH_LOCK`). A consequential effect is bound to its Work's exact approval and effect ledger, so two Works on one account cannot reuse each other's approval. Background runs cannot pay (`SELF_SET`) or ask for approval (#619). The tools a Work will use are only known while it runs, so an account key could only be taken lazily, like the browser. The recommendation is not to add one (owner decision 3). The implementation records when two running Works used the same connector, so the question can be revisited with evidence.

### D4. Per-Work state off the service

| State | Change |
|---|---|
| `current_work_id`, `_work_local.budget`, the decision budget | None. Already thread-local (#826) |
| `service._early_plan` | Moves to the Work's own `WorkRun` (`run.early_plan`). `_route_work` sets it; `_prepare_worker_turn`, `_settle_work_judgments` and `run_one` read and clear that run's value |
| `live_workers`, `live_steps`, `live_agents`, `presence`, `steer_closed`, `steer_judging`, `_native_intake_barriers` | None. Keyed by Work id and popped in the lane's `finally` |
| `worker_lock` | Removed (D1). Tests that hold it to simulate a busy worker hold a lane instead |
| `side_answer_flight` | Becomes the set of messages with a judgment in flight (D6). The claim skips them, as now |
| `_after_work_sweep` | Dispatcher only |
| `consider_steer`'s comment "one tool-capable worker runs at a time" | Rewritten: several Works may run in one conversation; the judgment is told which (D6) |

The implementation slice greps for other `service.<name>=` writes inside the phases and moves each one to `WorkRun` or `_work_local`. The search for this document found only the ones above.

### D5. The browser and the CLI sessions

**Browser.** There is one owner-logged-in profile, and it stays one. A second worker on the same jar would race the cookie export, and per-Work browser profiles are a separate design (non-goal).

- A Work's first browser step waits while the profile is held by **another Work**, for at most `BROWSER_WAIT_SECONDS` (**120 s**, owner decision 4). After that it gets the existing `browser_busy` text, and the worker decides what to do next. A held login window or a keep-alive behaves as today.
- The wait fits inside the relay's call bound (`CALL_SECONDS`, 300 s) and the CLI's inactivity bound (600 s, #927). Like a busy browser today, it spends no browser step (#620). Stop interrupts it.
- **No deadlock.** Admission keys are taken all or nothing at the claim, and no running Work waits for an admission key. Two locks can be waited for after admission, always in the same order: the peer Codex auth copy (taken at turn launch), then the browser profile (taken during the turn). Both waits are bounded.

**CLI sessions.**

- One CLI process per running Work (owner requirement on #17). No session is shared. Each attempt already has its own temporary home, MCP bridge, relay socket and key.
- Codex reads the owner's `CODEX_HOME` itself, as the owner's own interactive Codex does. Several Codex and Claude Code processes on one login already run at once today: early judgments, steer judgments and the upkeep run beside the worker.
- **Subscription usage limits.** With N turns at once, a subscription reaches its usage limit sooner. The existing handling applies: the CLI's usage-limit signal (#873) marks that worker failed for the Work, and the plan may re-delegate to another worker (#1431). The default of 3 keeps this bounded.

### D6. Building on RESPONSIVE-01: a message beside a running Work

Today a message that arrives while a Work runs in the same conversation goes through `consider_steer`. With lanes:

- **The judgment sees the running Works of the conversation.** `steers_running_work` is given at most N running Works of that conversation, owner-class and background, each by its request and shown plan. It returns one of: steer the Work it names (by id), run beside, or wait. This is the three-way choice the owner set on #17 (2026-10-26), in the same single call; no call is added. #968 decides what a steer into a scheduled run does.
- **Beside** records a content-free `owner_steer` receipt with status `beside` on the new message. The claim reads it and releases the `conv:` key for that one message, so it runs as a full Work in a free lane, with tools, approvals and effects on the ordinary path. The tool-less side answer (`_side_answer`) remains only when `work_lanes` is 1.
- **Wait**, **unavailable** or no judgment leave the message queued behind the conversation's running owner Work, as today. They are still never behind scheduled runs, because background Works hold no `conv:` key.
- **No double handling.** While a judgment for a message is in flight, the claim skips that message, as `side_answer_flight` does today. A message cannot be claimed as its own Work and handed to a running Work as a steer. `steer_closed` and the finish guard (#308 S1) are unchanged, so a steer that arrives after a Work's reply was decided runs as its own Work.
- **Limits are told** (owner requirement on #17). An owner message that waits because the lanes are full, or because its conversation's Work is running and the judgment said wait, gets the surface's standard waiting state. `acknowledge_long_work` shows it today only for running Works; it is extended to queued owner Works. This replaces the silence of today's 543 s wait.

### D7. Budgets

Nothing is added and nothing is loosened.

- Each Work keeps its own `WorkBudget` and ledger: deadline, turns, tool calls and the decision budget.
- Scheduled runs are charged to `scheduled_work_budget` at admission (`Preparations.start`), as now. Concurrency changes when they run, not how many may run in a day.
- The store-wide caps (`MAX_ACTIVE`, `TICK_LIMIT`, the #938 piece bounds) are unchanged.
- The lane cap is a functional execution condition under C17, like a finite budget. It restricts nobody's authority.

### D8. Idempotency and recovery (C8)

| Hazard | Hold |
|---|---|
| One Work claimed twice | One claimant thread, and the claim is one `BEGIN IMMEDIATE` transaction that moves `queued` to `running` |
| One slot run twice | `UNIQUE` request key per slot; the row is `running` until settled |
| Two runs of one line at once | `line:` key (D3) |
| A retry or continuation beside its original | `chain:` key (existing) |
| A message run as a Work and also steered into another | The claim skips a message with a judgment in flight (D6) |
| A side answer and a lane both answering | Side answers only with one lane; the commit stays `WHERE status='queued'` |
| Two replies or notices for one Work | Deliveries already claim a pending reply atomically; notices are `UNIQUE(job_id, kind)` |
| One approval used by two Works | Approvals are bound to the exact Work and action, settled compare-and-set |
| The after-work tail run twice | The sweep skips a Work still in a lane (D1); markers are already compare-and-set |
| Restart with several Works in flight | `recover` marks all of them `interrupted` and fences their ledgers. None is re-run; `safe_retry` decides per Work, and an uncertain effect stays `unknown` |
| A lane fails outside the Work's handlers | `_run_one` already ends a claimed Work as failed or unknown (#893, `_fail_work_terminal`). The future's exception is logged and the lane is reused. `healthy()` still watches the dispatcher thread |
| Store contention | More writers on the rollback journal. Slice 1 counts `database is locked` errors. If they appear, adopt SQLite WAL (`PRAGMA journal_mode=WAL`), with the backup and restore scripts checked for it in the same slice |

## Bounds

| Bound | Value | Mechanism |
|---|---|---|
| Lanes | **3** (owner decision 1) | New `work_lanes` setting. `1` is today's behaviour, plus owner-first order |
| Lanes background Works may hold | **N − 1** | New reserve (D2) |
| Runs of one line at once | 1 | New `line:` key |
| Owner Works of one conversation at once | 1, unless judged beside | New `conv:` key, released by the D6 judgment |
| Browser profile holders | 1 | Existing; wait of up to **120 s** for another Work (owner decision 4) |
| Daily unattended work, per-Work budgets, store-wide preparation caps | Existing | Unchanged |

## Authority and threat model

| Concern | Path | Hold |
|---|---|---|
| Wider authority through concurrency | A lane runs with more than its Work's Grants | Each lane runs the unchanged `run_one` path with that Work's Grants, budget and decision scope. Nothing is shared between lanes except the store |
| Background crowding the owner | Scheduled runs fill every lane | Reserve of one lane for the owner class; owner-first order (D2) |
| Duplicate consequential effect | Two Works do the same thing | `line:`, `chain:` and slot keys; exact approvals; effect ledgers per Work (D8) |
| Outside text widening the run | A page tells a background run to act in another line | Unchanged. Background runs cannot pay or ask (#619, `SELF_SET`); the outside-content mark is per Work (C17 case 3) |
| Cross-Work leakage | Work A's context, budget or early plan read by Work B | Thread-local context (#826); early plan on `WorkRun` (D4); live state keyed by Work id |
| Steer routed to the wrong Work | Two Works run in one conversation | The judgment names the Work by id (D6); `add_steer` takes that id |
| Browser cookies of one Work seen by another | Sequential holders | Unchanged: one holder at a time, export on release |
| Secret in a prompt or log | More concurrent prompts | Unchanged redaction on every path; no new prompt text carries owner data except the running requests the steer judgment already reads |
| Starved idle work | Upkeep and keep-alive wait for an idle store | Unchanged and noted: with frequent scheduled runs these start less often. Not a safety issue |
| Silent waits | The owner is not told why | Waiting state (D6) |

None of the three C17 restriction cases is touched. The keys and the lane cap are functional execution conditions: each keeps a concrete purpose (C8, one browser profile) and a visible consequence (a waiting state, a `browser_busy` text).

## Escalated review

The implementation of slice 2 needs it. Concurrency widens no authority. But the #17 design and its review relied on the global lock to serialize every Work that can request or consume an approval or make an external effect. Removing that lock changes the conditions under which C8 and the approval compare-and-set must hold. That is the *declared safety invariant* criterion, so the Codex review checks the final head against the D8 table and the race tests below.

Slice 1 (dispatcher, owner-first order, D4 state moves, `work_lanes` = 1) changes no concurrency and needs no escalated review.

## Existing solutions review

External first, from the official documentation of each project.

- **Python `concurrent.futures` and `threading`** ([docs](https://docs.python.org/3/library/concurrent.futures.html)). `ThreadPoolExecutor(max_workers=N)` is a bounded pool; futures carry each task's exception. **Adopt** for the lanes (D1). Threads, not processes: the service state, the thread-local context and the in-process browser profile live in one process.
- **SQLite transactions** ([`BEGIN IMMEDIATE`](https://www.sqlite.org/lang_transaction.html), [WAL](https://www.sqlite.org/wal.html)). An immediate transaction takes the write lock at its start, so one claim at a time sees and changes the running set. **Adopt**: the claim already uses it, and the keys are checked inside it (D3). WAL is the standard answer if writer contention appears (D8).
- **Sidekiq** ([concurrency](https://github.com/sidekiq/sidekiq/wiki/Advanced-Options), [Enterprise rate limiting](https://github.com/sidekiq/sidekiq/wiki/Ent-Rate-Limiting)). One process runs N threads; queues are weighted or strict; a `concurrent` limiter is a named mutex with a count, held while a job runs. **Adapt** the pattern: N threads, strict owner-first queue order, a named limit of 1 per key. The limiter itself needs Redis.
- **Celery** ([workers](https://docs.celeryq.dev/en/stable/userguide/workers.html), [routing](https://docs.celeryq.dev/en/stable/userguide/routing.html)). `--concurrency` per worker; priority is done with dedicated queues and workers rather than message priority. **Adapt** the idea of a dedicated owner queue as the reserved lane (D2). Celery has no built-in keyed mutex; third-party singletons lock in Redis.
- **RQ** ([workers](https://python-rq.org/docs/workers/)). One job per worker process; concurrency is more processes. Not a fit: the browser profile and the service state are in one process.
- **GitHub Actions** ([`concurrency`](https://docs.github.com/en/actions/using-jobs/using-concurrency)). A `group` key lets one run per key proceed; others wait (with `cancel-in-progress: false`). **Adapt** the semantics for `line:` and `conv:`: wait, never cancel.
- **Internal.** RESPONSIVE-01: the steer judgment and side lane (#999, #308, #17), its owner requirements of 2026-10-26, the early judgments (#969, #309), the thread-local Work context (#826), `WorkLedger.request_chain`, the preparation slot keys (#659), `BrowserProfile`'s holder lock (#990, #1572), `_OAUTH_LOCK`, `_PEER_CODEX_AUTH_LOCK` (#673), `QuickStore.recover` and `fence_restart`, `history(through=…)` (#17), atomic delivery claims, the usage-limit signal (#873) and re-delegation (#1431).
- **Decision: Adapt.** The standard library pool and the SQLite claim are adopted. The keyed-wait and reserved-queue semantics are adapted from Sidekiq, Celery and GitHub Actions without their brokers. RESPONSIVE-01's judgment and the existing locks are extended. There is no new store, dependency or framework.
- **Rejected:** a broker-based job system (Celery, RQ, Sidekiq), because it needs Redis or a second process model and cannot hold the in-process browser profile or the thread-local Work context; a second full `_run_one` thread without keys (#17 rejected it for the same reason this design adds keys); an in-memory key map, which a restart could leave inconsistent with the store.

## Non-goals

- Per-Work browser profiles, or a second browser worker on the same jar.
- Running two runs of one line at once, or catching up a slot that a line's own long run skipped.
- An account-connection key (owner decision 3).
- Reusing a finished Work's CLI session (`codex exec resume`, `claude --resume`); tracked separately.
- Any change to payment, approvals, `SELF_SET`, #619 or the outside-content mark.

## Acceptance

Acceptance is by scenario, on injected runners and fake clocks, not by test counts.

1. **Owner question during scheduled runs.** Two five-minute rows of different lines and one owner message. The owner Work starts within one tick, in the reserved lane. Both rows start at their slots.
2. **Long Work beside scheduled runs.** A long owner Work runs; scheduled rows of two lines start at their slots in the background lanes.
3. **Same line.** A line's next slot comes due while its previous run is still running. It does not start, and it is settled as today when the run ends.
4. **Same conversation.** A second owner message while the first runs: judged steer, it reaches the named Work; judged beside, it runs at once in a free lane; judged wait or unavailable, it runs after the first and the owner sees the waiting state.
5. **Browser.** Two Works need the browser. The second waits up to the bound, then runs or gets `browser_busy`. Stop ends the wait.
6. **Restart.** Three Works in flight. After restart all three are `interrupted`, none is re-run, no reply or notice is sent twice.

### Race and negative tests the implementation must carry

- Two dispatcher passes cannot claim one Work. A Work claimed while its message's steer judgment is in flight is impossible.
- No two `running` Works share a `line:` key or a `chain:` root; checked under randomized interleavings of claim, settle and stop.
- Background Works never hold more than `N − 1` lanes. An owner Work is never behind a background Work in claim order.
- Early plan, budget and `current_work_id` of two concurrent Works never cross (each Work's decision records carry its own id).
- A steer goes only to the Work the judgment named.
- `sweep_after_work` never runs the tail of a Work still in a lane.
- With `work_lanes` = 1, the existing suite for the single worker passes, including the side-answer tests.
- Each concurrent Work's Grants, information-use record and approvals are exactly those it has when run alone.

## Owner decisions in this design

1. Lane count: **3** by default, one of them reserved for the owner's own Works. `1` turns concurrency off.
2. Same-conversation messages: run beside a running Work only when the owner's AI judged them independent (the #17 three-way choice). Otherwise they wait for that Work, never for scheduled runs.
3. No account-connection key now; record same-connector overlap and revisit with evidence.
4. Browser wait: a Work waits up to **120 s** for another Work's browser before it is told the browser is busy.

## Implementation slices

1. **Dispatcher, no concurrency.** D1 with `work_lanes` = 1, the D2 claim order, the D4 state moves, the pre-claim steps in the dispatcher tick, the lane registry for the sweep, a `database is locked` counter. Deliveries and preparation ticks no longer wait for a running Work. No escalated review.
2. **Lanes and keys.** `work_lanes` = 3, the reserve, the D3 keys in the claim, the D8 race tests, the browser wait. Escalated review.
3. **Beside.** The D6 judgment over the conversation's running Works, the `beside` receipt, the waiting state. Measured on a small scenario set before and after, as a judgment change.

Related: #17, #309, #332 (RESPONSIVE-01), #308, #826, #938, #968, #973.
