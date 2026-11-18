# Entrusted work in pieces: a line run schedules its next piece (#938)

## Status

This is the design proposal for [#938](https://github.com/Jongtae/Still/issues/938). Slice 1 (the scheduling path) is implemented in the change that amends the contracts below; slices 2 and 3 are not.

The implementation is a separate change. It widens what an unattended run may do, so it needs the escalated review in the [Development Constitution](../development-constitution.en.md#escalated-review): a Codex review of the final head, required CI on that head, and a merge by the owner. That change also amends the contract text listed under *Contract amendments*.

## Owner outcome

The owner hands over long or multi-stage work, such as research then decide, buy then watch the exit, or book then watch for changes. The owner's AI does it in pieces:

- Each piece does one bounded step, checks it, and ends by saying where the work stands.
- When more is needed later, the piece schedules the next piece itself, for the time it is needed.
- No piece keeps a turn open to wait. The next piece starts from a recorded handoff, not from scratch.
- The owner sees what each piece found and can stop the whole line in one act.

## Owner decision (2026-11-17)

A run with no owner present may schedule the next piece of its own work.

Payment rules are unchanged: per-action approval, or owner-enabled payment autonomy. A run Still set up on its own never pays (C17 defaults, `SELF_SET`). Unattended runs also take no step that needs the owner's approval (#619). A piece that reaches an order therefore reports what it found, and the owner acts from that report.

## What exists

All paths are under `src/personal_agent/` at the main revision this document was written against.

| Piece of the loop | Existing mechanism | State today |
|---|---|---|
| Identity of continuing work | Entrusted line (#798). Brief versions live in `procedure_versions`; rows link through `preparations.line_id` | Present. At most 50 lines (`MAX_LINES`) |
| Wake-up at a later time | Preparations (#659): `schedule_preparation`, the due tick, one request key per slot `preparation:<id>:<slot>`. A continuation keeps the slot key | Present |
| What a line run reads | `line_run_input`: the current brief plus up to 6 earlier outcomes of the line's preparation rows, each at most 600 characters. Outcomes are marked delivered, kept, failed or open. A failed run shows its error, not its reply. The owner-typed Work that started the line is not among them | Present, lossy |
| Stop | `stop_line` marks the brief stopped and cancels pending rows and bundles. A running row is refused at its next step (`line_refusal`) | Present |
| Forget | `forget_record line:<id>` marks brief versions forgotten (`_exclude_line`). Pending rows stay scheduled and are refused when they run | Present; rows are **not** cancelled |
| Owner view | Line bundles (stood lines only, `held_row`), periodic account, Settings 준비해 둔 일, `entrusted_line list` | Present |
| Daily bound on unattended work | `scheduled_work_budget`: 300 calls a day by default, charged at admission, shared by all scheduled work. `0` turns it off | Present |
| Store-wide caps | `MAX_ACTIVE` = 50 active preparations; `MAX_ACTIVE_DREAM` = 10 for look-back rows | Present |
| No payment from self-set runs | `SELF_SET` = owner-work, dream, day-plan. Payment autonomy is refused for these rows; payment in any unattended run is refused (`self_set_writes`, `unattended_run`) | Present |
| No owner approval from unattended runs | #619: an unattended run asks nothing; an approval-requiring step is refused and the wait is retired | Present |
| Outside-content mark | `outside_content_sources(job)`: a Work that read outside content has its writes narrowed. The mark is per Work | Present |
| **A line run schedules its next piece** | `preparation_scheduler` refuses every unattended run except the day plan (`unattended_run`) | **Missing.** This is the blocker |

A run that keeps producing output is no longer cut at 600 s. #927 replaced the fixed cap with an inactivity bound and the Work's own deadline. Nothing needs to wait inside one turn; the remaining need is to continue later.

## Design

### D1. Who may schedule

A call from an unattended run is accepted only when all of these hold:

1. The run belongs to a qualifying row:
   - the row is a `prepare` row with a `line_id`;
   - its origin is not `line-account`;
   - its `accepted_by` is owner-request, owner-button, owner-settings, owner-answer, owner-work, or `line-piece`. In other words, the owner accepted the row, it was set up in an owner-typed Work, or it is itself a piece.

   A row the owner accepted (owner-request, -button, -settings, -answer) is not `SELF_SET`, so its own run keeps the owner's payment rules. A piece it schedules is `line-piece`, which is `SELF_SET`, so the piece never pays.

   Rows of the look back (`dream`) and of the daily plan (`day-plan`) do not qualify. Their own rules stand: the daily plan's runs cannot schedule in turn.
2. The run's line is active and announced. This is checked again inside the `create` transaction (D6).
3. The new row names the same line, is a one-off `prepare` (no window, recurrence or `rrule`), and carries no `replaces`.
4. A continuation of a qualifying run counts as that run, because it keeps the run's slot key.

Every other unattended run stays refused, as today. That covers ordinary scheduled preparations, look-back and daily-plan rows, periodic accounts, and runs outside a line.

**Why a line:** a line has an identity, a report promise, a stop control and a periodic account. A piece outside a line would be a schedule the owner was never told about and cannot stop as one unit.

### D2. The handoff

There is no new store and no new brief field. Pieces never write the brief.

- **The next piece's goal is the handoff note.** It holds up to 8,000 characters (the existing `prepare` bound). The piece that schedules it writes what was done, the state found, and what the next piece should check. In the long-running harness pattern, this is the progress file handed to the next session.
- **Outcomes stay the history.** `line_run_input` keeps giving each piece the line's recent outcomes, so a failed or open piece remains visible.
- **The first handoff comes from the owner-typed Work.** That Work starts the line's work (for example, the buy) and schedules the first piece with the state in its goal. It may also write the state into the brief's open matters, which it is already allowed to do.
- **Worker guidance is content, not code.** One general sentence is added to the line-run input text (`lines.run_input` in `messages.json`): do one step; end with where the work stands; when more is needed later, schedule the next piece for this line, writing the handoff into its goal; do not wait inside the run. It names no task, site or domain (C16).

### D3. Bounds

A loop must show up as a visible count, not as silent spend.

| Bound | Value | Mechanism |
|---|---|---|
| Children per run | **1** | New. A run (by its slot key) may have one child piece. A second call replaces that pending child (D6) |
| Pending pieces per line | 1 | Follows from the line above for a sequential chain |
| Piece runs per line per owner-local day | **24** (owner decision 2026-11-18) | New. The 25th schedule call that day is refused with `line_pieces_daily`. Holds even when the shared budget is off |
| Minimum lead time | **5 minutes** (`MIN_EVERY_MINUTES`) | New for pieces. A due time sooner than now + 5 minutes is refused |
| Pending pieces store-wide | **10** (owner decision 2026-11-18) | New shared allowance like `MAX_ACTIVE_DREAM`, so pieces never crowd out owner-directed rows |
| Daily unattended work | Existing `scheduled_work_budget` | Unchanged. Pieces are charged like every scheduled run |
| Goal length, window limits | Existing | Unchanged. These are functional execution conditions under C17 |

An earlier note in #938 said the goal bound is 1,000 characters. The code says 8,000 for `prepare`; that note was wrong.

### D4. What the owner sees and how it stops

- **Origin and acceptance:** a piece is recorded with origin `line-piece` (added to `ORIGINS`) and `accepted_by` `line-piece` (added to `SELF_SET`).
- **Delivery:** a piece inherits the recorded send intent and delivery mode of the row whose run created it. A chain started in an owner-typed Work therefore keeps that Work's destination. There is no new bundling and no routing from the brief's free text. A `when_needed` parent gives `when_needed` children: the existing quiet judgment sends a piece's report only when there is something for the owner.
- **Owner view:**
  - The line's entry in `entrusted_line list` and in Settings shows the pending piece with its due time under the existing next-run field.
  - The periodic account counts the pieces run.
  - There is no new notice type.
- **Stop:** the existing stop cancels the pending piece with the line's other rows.
- **Forget:** forgetting a line also cancels its pending pieces, in the same transaction (new). Undoing the forget restores the brief versions only. Cancelled pieces stay cancelled, and the owner restarts the work by asking.

### D5. Payment, approvals and outside text

- **No payment.** A piece is `SELF_SET`, so it never pays, even with payment autonomy on.
- **No approval requests.** A piece takes no step that needs the owner's approval, as with every unattended run (#619). When it reaches such a step (an order, an irreversible change), it reports the exact action it would take and stops. The owner acts from that report in a Work the owner starts.
  - A separate amendment could let unattended runs put an approval request in front of the owner. It is listed under *Owner decisions* and is not part of this design.
- **Other external changes.** These follow the shared #717/#875 rule, unchanged.
- **Outside text.** Text a piece reads from outside (a page, a mail, another agent's output) informs the piece but never supplies owner authority (C17, case 3). A child inherits its parent run's outside-content mark: the child's run starts marked, so the narrowed write rules of a marked Work apply to it as well. A chain cannot launder outside text into an unmarked run.

### D6. Idempotency and races

- **One child per run.** The key is (line, parent slot key).
  - A second schedule call from the same run, or from its continuation, replaces that pending child: same row, new due time and goal. No new row is created. This covers a continuation or a re-run step that recomputes the due time or rewords the goal.
  - Once the child has started, the parent can no longer replace it.
- **Retries.** An owner retry is `FOLLOWUP_RETRY` and is not unattended, so it never reaches this path. A run's own retry within a slot keeps the slot key.
- **Stop race.** The line's state is checked again inside `create`'s `BEGIN IMMEDIATE` transaction. A piece scheduled at the moment the line stops is refused (`line_stopped`) instead of being left scheduled on a stopped line.

## Authority and threat model

| Concern | Path | Hold |
|---|---|---|
| Runaway chain | Each piece schedules one more, forever | Per-line daily cap and minimum lead time (D3), which hold even with the shared budget off. Shared budget. Visible in the line's entry and account. Stop cancels |
| Fan-out | One run schedules several pieces | One child per run (D3, D6) |
| Crowding owner work | Many lines with pieces | Store-wide piece allowance of 10 (D3) |
| Payment without the owner | A piece reaches an order | `SELF_SET`; payment refused in unattended runs; no approval requests (D5) |
| Outside text as instruction | A page tells the piece to schedule or widen work | C17 case 3; the outside-content mark is inherited (D5); same line only; no `replaces` (D1) |
| Cancelling others' rows | `replaces` on the owner's row or the account row | `replaces` refused for pieces (D1) |
| Scope drift | A piece schedules for another line or for none | D1: same line only |
| Brief tampering | A piece rewrites rules or the report promise | Pieces cannot write the brief (existing refusal, kept) |
| Duplicate pieces | Continuation, re-run step | One child per (line, parent slot key) (D6) |
| Outliving stop or forget | Pending piece after stop, forget, or a stop race | Stop cancels; forget cancels (new); line state re-checked inside `create` (D6); `line_refusal` at start and at each step |
| Silent spend | Pieces run and report nothing | The quiet judgment governs sends. The account counts runs. The budget notice is unchanged |

## Contract amendments

The implementation change amends, in the same PR:

- `docs/secretary-agency-contract.en.md`, *Act, then tell*. The text "A Work the owner did not type … still only proposes" gains an exception for runs of qualifying line rows, under D1 to D6.
- *Daily plan*. The text "That run alone, among scheduled runs, may call `schedule_preparation`" becomes "That run and the runs of qualifying line rows (*Entrusted lines*)".
- *Entrusted lines*, "A line is not an authority". Add: a line unlocks no Grant, approval or payment. It only lets its own runs continue it in pieces within the D3 bounds.
- `docs/request-path-rule-census.en.md`, the unattended-run rows. Record the new scheduling case and the C17 case it serves: none of the three restriction cases. It removes a restriction, and its bounds are functional execution conditions.

## Existing solutions review

The review was done at proposal time ([#938 comments](https://github.com/Jongtae/Still/issues/938)), from primary sources read on 2026-11-17.

- **Anthropic, [effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents).** Each session works on one feature, tests it, commits, and updates a progress file. A new session reads progress and git history first. The named failures include doing everything at once and declaring done too early. **Adopt** the pattern (D2): the handoff note is the progress file, and line outcomes are the log.
- **Manus, [context engineering](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus).** Objectives are recited into the end of the context through a rewritten `todo.md`, and failures stay in the context. Covered by D2: the handoff leads the next request, and outcomes keep failed and open runs marked.
- **OpenAI, [scheduled tasks](https://help.openai.com/en/articles/10291617-scheduled-tasks-in-chatgpt).** Tasks are created when the user asks, with active caps of 3 to 15 depending on the plan. A task that needs approval pauses until it is reviewed. Monitoring tasks stop at a defined end condition. A run uses quota even when it posts nothing. **Adapt:** Still lets the line's own run create the next task, with per-line caps (D3). Still has no pause-for-approval in unattended runs (#619), so a piece reports instead (D5).
- **Temporal, [durable AI](https://docs.temporal.io/ai) and the [activity definition](https://docs.temporal.io/activity-definition).** Waits and approvals are durable. Activities may run more than once, so their effects need idempotency keys. The existing preparations already provide the durable wait. **Adopt** the idempotency rule (D6). No workflow framework is introduced: #822 requires a compatibility spike before any migration, and nothing here needs one.
- **Anthropic, [multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system).** Tightly coupled work is a poor fit for parallel agents. Pieces run in sequence, with one child per run.
- **Internal:** entrusted lines (#798, #799), preparations (#659), `scheduled_work_budget` (#867), `SELF_SET` (#717), unattended approvals (#619), the outside-content mark, the #927 turn bound.
- **Decision: Adapt.** The change opens the existing scheduling path to qualifying line runs under D1 to D6. There is no new store, dependency or framework.

## Non-goals

- Payment from pieces, approval requests from unattended runs, or any change to payment autonomy.
- Worker session resume (`codex exec resume`, `claude --resume`). It is an optimisation, tracked separately.
- Parallel pieces, window or recurring pieces.
- New brief fields, or brief writes from pieces.
- Pieces for look-back (stood) or daily-plan lines.

## Acceptance

Acceptance is by scenario, not by test counts.

1. **Trade within a budget.**
   - The owner entrusts a small trading budget. An owner-typed Work buys within approval or autonomy, writes the position into the first piece's goal, and schedules it.
   - Each check reads its handoff and the line's outcomes. It reports when there is something for the owner and schedules the next check while the position is open.
   - A check that would sell reports the exact order it would place. The owner places it from that report.
   - Stop ends the pending check.
2. **Multi-stage research.** A line's first piece collects sources and schedules the next with what it found. The last piece delivers the result. No piece waits inside its run.
3. **Invariants across both scenarios.**
   - No payments and no approval requests from pieces.
   - No second child per run.
   - No piece beyond the per-line daily cap or sooner than the lead time.
   - No piece after stop, forget, or a stop race.
   - Every piece is charged to the daily budget.

### Negative tests the implementation must carry

- These stay refused with `unattended_run`, and the existing tests are kept:
  - an ordinary scheduled run;
  - a look-back row;
  - a daily-plan row;
  - a periodic-account row;
  - any run outside a line.
- A qualifying run cannot schedule for another line, for no line, a reminder, a window or recurring row, or with `replaces`.
- A second call from the same run or its continuation replaces its pending child.
- The 25th piece of a line in one day is refused, including with the shared budget off. A due time inside the lead time is refused.
- The 11th pending piece store-wide is refused.
- A piece is `SELF_SET`. Payment is refused with payment autonomy on, and no approval is put to the owner.
- A child of a run that read outside content starts marked.
- Stop and forget cancel the pending piece. A piece scheduled while its line stops is refused.
- A piece cannot write the brief.

## Owner decisions in this design

The owner accepted the proposals on 2026-11-18:

1. Per-line daily piece cap: **24**.
2. Store-wide pending-piece allowance: **10**.
3. Qualifying rows: owner-set-up lines only, not look-back or daily-plan lines.
4. **Not part of this design:** whether unattended runs may put an exact approval request in front of the owner (an amendment to #619). Without it, a piece that reaches an order reports, and the owner acts.

## Implementation slices

1. **Scheduling path.** D1, D3, D4 origin and acceptance, the D4 forget cancel, D5 mark inheritance, D6. It carries the negative tests, the contract amendments and the escalated review.
2. **Line-run guidance sentence.** D2, as message content. Measured with a small scenario set before and after.
3. **Owner view.** The pending piece in the line entry, and the piece count in the periodic account.

Related: #798, #799, #822 (M3, M4), #927, #937, [`docs/research/rewrite-readiness-2026-11-10.ko.md`](../research/rewrite-readiness-2026-11-10.ko.md) §7–§12.
