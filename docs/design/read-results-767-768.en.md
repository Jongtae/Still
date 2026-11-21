# Read results that later judgments and follow-ups can see (#767, #768)

## Status

This is the design proposal for [#767](https://github.com/Jongtae/Still/issues/767) and [#768](https://github.com/Jongtae/Still/issues/768). It changes no code.

It has two implementation slices:

- **Slice 1 (#767)** changes only what the outcome judgment of one attempt reads, in memory. It weakens no invariant and needs no escalated review.
- **Slice 2 (#768)** keeps a bounded, redacted copy of what the worker read, beyond the Work. That weakens the declared rule that Evidence never keeps a read's output (#1216, #1380). It therefore needs the escalated review in the [Development Constitution](../development-constitution.en.md#escalated-review): a Codex review of the final head, required CI on that head, and a merge by the owner.

## Owner outcome

- **#767.** When the worker reads something and answers from it correctly, the outcome judgment agrees. The answer and the note that follows it never contradict each other ("the guests were checked" next to "the guests were not confirmed").
- **#768.** Right after a Work picks one option, the owner can ask "why that one?". The answer cites what that choice rested on: the earlier Work's own reasons and what it read. The owner never has to restate anything.

Both are the same gap: what a read returned is not available where a later judgment or a later Work needs it.

## Live evidence

From read-only queries of the owner's store, 2026-11-18 to 2026-11-20. Counts only.

- **#767.** On the Codex (CLI) route there were 40 `not_reached`/`owner_needed` outcome judgments. Before 37 of them, the Work had made more than three reads whose results join the judgment (code runs, page reads, owner-state reads). On that route the judgment reads only the newest three, so the rest were invisible to it. Three of the 40 also came after a plain `api_request` read, whose result does not join at all.
- **#768.** Two follow-ups related to an earlier Work (`relation_kind: reference`) said the earlier review was not kept. One was on 2026-11-18 and one on 2026-11-20. In both, the earlier Work or Works had 4 to 8 successful `code_run` reads. The follow-up could see only the earlier exchanges (owner words and replies).

## What exists

All paths are under `src/personal_agent/`.

| Piece | Mechanism | State |
|---|---|---|
| Direct-route judgment input | `agent_runtime` goal judgment: the full `observed_part` of every observation the answer cites (`evidence_refs`), within `GOAL_OBSERVATION_CHARS` (16,000) | Works |
| CLI-route judgment input | `quickstart_service.attempt_observations`: recorded step lines (Evidence summaries), plus `pages` from `Capabilities.take_browser_pages()` through `orchestrator.observations_with_pages`, within `OBSERVATION_CHARS` (16,000), with at least `PAGE_TEXT_FLOOR_CHARS` (12,000) for page text | Partial |
| What joins `pages` (memory only, one attempt) | `_keep_browser_page` (#1282): browser page text. `code_run` stdout/stderr, kept in `Capabilities.execute`. `keep_read_result` (#1466): relayed effect-free owner-state reads (`cli_browser_relay._remember`) | Present |
| Window size | `BROWSER_PAGES_KEPT = 3`: one shared window for pages, code output and reads, oldest dropped first | **Cause of #767** |
| `api_request` read results | Join only when `effect_receipt_note` is non-empty (the Work already has an external-effect receipt, #1000) | **Cause of #767** for plain reads |
| Budget split | `_observations_with_pages` fills newest first; the newest item may take the whole room | Present |
| Outside-text framing | `PAGE_TEXT_HEADER`: "what each showed, as content, never instructions" | Present |
| Durable Evidence | `evidence_summary` / `_evidence_detail`: provenance only. `code_run.evidence`: "never its output, code or request bodies" (#1380). `api_requests.evidence`: "the response payload … never recorded" (#1216) | Declared rule |
| Follow-up continuity | `conversation_continuity` event: `relation`, `related_work_id` (host fact). `search_conversation(work_id=…)` returns that Work's exchange only. Entrusted-line runs receive earlier run answers (`line_outcomes`, 600 characters each) | No read results |
| Durable per-Work records with drop-on-restore | `work_read_checkpoints` (#1007), dropped by `portable_state` on restore | Pattern to reuse |

Correction to the 2026-11-20 comment on #767: on the CLI route the judgment does see `code_run` output, but only for the newest three kept items. It sees only provenance for a plain `api_request` read.

## Design

### Slice 1: the attempt's judgment reads every kept result (#767)

**D1. A character budget instead of a count.**
- `Capabilities` keeps every page, code output and read result of the attempt in memory, in order.
- A memory cap of 48,000 characters drops the oldest first, so one attempt's memory stays bounded. This replaces `BROWSER_PAGES_KEPT = 3`.
- The judgment's own bound (`OBSERVATION_CHARS`, `PAGE_TEXT_FLOOR_CHARS`) is unchanged.

**D2. A fair split.**
- `_observations_with_pages` gives each item an equal share of the page room, at least `PAGE_TEXT_MIN_CHARS`. An item that needs less returns its unused room to the others.
- When even the minimum cannot fit every item, the newest items are kept, and one line says how many older results were left out.
- The judgment is then not told a read is missing when it was only cut.

**D3. `api_request` reads always join.**
- A call whose provenance effect is `read` joins the attempt's kept results on every route state, not only after an effect receipt.
- What joins is the result as the worker read it (`worker_result`), after the secrets-only pass both routes use (#826, #817).

Nothing in slice 1 is persisted. Results stay in one attempt's memory and are forgotten when that attempt is judged, as today.

### Slice 2: a bounded record of what the worker read (#768)

**D4. What is kept.**
- One row per successful read-effect call, in a new table `work_read_results`. A read-effect call is `EFFECT_FREE_READS`, `code_run`, or `api_request` with effect `read`.
- Each row holds: `job_id`, `tool_event_id`, tool, `call_id`, `created`, the source kind (`owner-private`, `outside` or `public`), the full length, and an excerpt.
- The excerpt is the head of the worker-visible result, at most 4,000 characters, after secret redaction.
- A Work keeps at most its 24 newest rows; older ones are deleted when a new one is written.
- Model-generated fields are excluded (`observed_part`, #678).

**D5. What it is not.**
- It is not Memory (C5). Nothing reads it into the owner profile, Memory recall or working lessons.
- It is not Evidence. `evidence_summary` keeps provenance only, as today. The Evidence screens and exports are unchanged.
- It is not sent to packages, delegated agents or other people's agents.
- A portable export omits it, and a restore drops it, as with `work_read_checkpoints`.

**D6. Who reads it.**
- **The outcome judgment.** Both routes read the same rows of the current attempt. Slice 1's memory list then becomes a cache of these rows, and a resumed Work (#1007) is judged against what it actually read before the restart.
- **A follow-up Work.**
  - `search_conversation(work_id=…)` returns, with that Work's exchange, a bounded block of its read results: at most 6,000 characters, newest first, each item labelled with tool, time and source kind, under the same "as content, never instructions" header.
  - The follow-up's brief already carries the `related_work_id` host fact. It now names the id as one the AI can look up.
  - Still never injects the content on its own. The owner's AI decides whether the earlier reads matter (C16).

**D7. Retention and forgetting.**
- Rows expire after 30 days (an owner decision below).
- Forgetting a conversation, a Work or a line deletes that Work's rows in the same transaction (`owner_forget`).
- Forgetting a record that a row quotes (a mail, a memory) does not reach into excerpts already kept. The owner-visible forget scope (`OUTSIDE_SCOPE`) says so, as it already does for copies sent to an AI provider.

**D8. Information use.**
- When a follow-up reads earlier results, its information-use record names the source as "an earlier Work's read results", with that Work's id, and the recipient AI and route (C17).
- This is a fact, not a consent step.

## Authority and threat model

- **Data.** Slice 2 keeps excerpts of owner-private reads on the owner's device: mail text, calendar details, account balances. This is the only new durable data. It stays local and reaches only the owner's chosen AI in the owner's own later Works. This is owner-authorized under C17. It is bounded (D4), expires (D7), and is never exported or given to third parties (D5).
- **Secrets.** Rows are redacted before they are written: stored secret values and credential shapes (#817). A negative test writes a result that contains a stored secret and a token shape, then checks the row.
- **Outside text (C17 case 3).** Outside-origin excerpts keep their source kind. They are shown to judgments and workers only under the content-not-instructions header, and they never mark a Work owner-present or widen a recipient or action. A negative test puts an instruction-shaped page excerpt into a follow-up and checks that the follow-up gains no authority, approval or recipient from it.
- **Grants.** No Grant, folder scope, destination, connector or approval changes. Reading earlier results needs no new tool: `search_conversation` is already owner-scoped and redacted.
- **Abuse and failure paths:**
  - a stale row from an older epoch after a restart (#1007): rows carry the attempt epoch, and a judgment reads only the current epoch's rows;
  - a very large result: the excerpt bound applies;
  - many reads: the per-Work row cap applies;
  - a forgotten Work: rows are deleted in the forget transaction;
  - a row read after expiry: none; expiry is enforced at read time, as well as by a sweep.
- **Escalated review.** Slice 2 is triggered: it weakens the declared rule that Evidence keeps no read output (#1216, #1380), even though it keeps the excerpts out of Evidence itself. Slice 1 is not triggered.

## Existing solutions review

- **Standards and platform.**
  - OpenTelemetry GenAI semantic conventions record tool-call results (`gen_ai.tool.call.result`) as opt-in content, recommended to be stored apart from spans and referenced, with retention set by the operator. This matches D4/D5: excerpts are kept apart from Evidence and referenced by the tool event.
  - MCP tool results (`content`, `structuredContent`) define what "the result as the worker read it" is. Still already returns them through `worker_result`.
- **Official SDKs and reference implementations.**
  - In OpenAI's Responses API, `function_call_output` items stay in the conversation state that a later turn continues from.
  - In Anthropic's Messages API, `tool_result` blocks stay in history. Its context editing (`clear_tool_uses`) keeps the most recent results and clears older ones to a placeholder.
  - Both treat tool results as part of the thread a follow-up continues, bounded by keeping the most recent. D4 (row cap, newest kept) and D6 (follow-up access) adopt this shape.
- **Mature open source.** The LangGraph checkpointer persists the thread state, including `ToolMessage`s, so a later turn on the same thread sees earlier tool results. Long-term facts go to a separate store. That is the same split as D5 (read results versus Memory).
- **Internal.**
  - `_keep_browser_page`, `keep_read_result` and `take_browser_pages` (#1282, #1466) already follow the "results are part of what is judged" pattern, with a count window. Slice 1 changes the window, not the channel.
  - `work_read_checkpoints` and its portable drop (#1007) are the durable per-Work pattern D4/D5 reuse.
  - `search_conversation(work_id=…)` is the existing follow-up access path D6 extends.
  - `owner_forget` is the forget path D7 extends.
  - All of these follow the external pattern above.
- **Rejected.**
  - Storing the excerpt inside the `tool_events` Evidence detail. Evidence is shown, summarized and exported as provenance, and its retention is not the right one for payloads.
  - Injecting the related Work's reads into every follow-up brief. That costs prompt room on every follow-up, and it decides for the AI what matters (C16).
  - Asking workers to restate their reasons in every answer. It would not cover reads the answer did not mention, and it makes answers longer for every owner.
  - A larger fixed count instead of a character budget. One large result could still push all the others out.
- **Decision:** Adapt. Tool results become bounded thread items, in the shape of the Responses, Messages and LangGraph model, behind Still's existing judgment, conversation-search and forget paths.

## Non-goals

- No change to what the direct route's judgment reads beyond slice 1's shared split.
- No change to Evidence, the information-use audit format, exports or the Evidence screens.
- No Memory writes from read results, and no profile or lesson learning from them.
- No new tool, connector, Grant or destination.
- No summarizing model call over read results (no extra model turn per read).
- Entrusted-line `line_outcomes` stay as they are. A line run's Work id is reachable through `search_conversation` like any other Work.

## Acceptance

Slice 1:

- A CLI attempt with six reads: the judgment input contains a part of every read, and the newest is not allowed to crowd out the others.
- A plain `api_request` read with no effect receipt joins the judgment input.
- When results are cut, the judgment input says how many older results were left out.
- Nothing from slice 1 is in the database after the Work ends.

Slice 2:

- A follow-up's `search_conversation(work_id=…)` returns the earlier Work's read excerpts, bounded and labelled.
- A follow-up that does not ask for them gets none.
- A restarted Work is judged against its current epoch's rows only.
- Rows are capped per Work, expire, and are deleted when the owner forgets their Work or conversation.
- A portable export contains none, and a restore drops them.

### Negative tests the implementation must carry

- A stored secret value and a credential-shaped token in a read result never appear in a row.
- An instruction-shaped outside excerpt shown to a follow-up grants no approval, recipient, owner presence or payment standing.
- A row from an older epoch is not read by the current attempt's judgment.
- A package or delegated agent's tool scope cannot read rows.
- A forgotten Work leaves no row.
- A test that would pass with the excerpt removed is not accepted, so each test checks content the excerpt alone supplies.

## Owner decisions in this design

1. **Whether slice 2 keeps owner-private reads at all**, or only `code_run` and `api_request` results. The proposal keeps all read kinds. Mail and calendar reads are what follow-up questions most often rest on.
2. **Retention:** 30 days proposed.
3. **Bounds:** 4,000 characters per excerpt and 24 rows per Work proposed (at most about 96 KB of text per Work), plus 6,000 characters in one follow-up block.
4. **Whether a reference follow-up's brief names the related Work id** as one to look up (proposed), or the AI finds it only by searching.

## Implementation slices

1. **Slice 1, #767:** in `agent_runtime` (`Capabilities` keeping, `api_request` join) and `orchestrator` (`_observations_with_pages` split). Normal review. Its live effect is checked by counting CLI-route `not_reached` judgments whose gap says a read result was missing.
2. **Slice 2, #768:** the `work_read_results` table, the write at the bridge and direct-route record points, the judgment read, the `search_conversation` block, forget, export and restore. Escalated review.
