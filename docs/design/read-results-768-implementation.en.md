# Read results, slice 2: implementation note (#768)

This note implements slice 2 of [the read-results design](read-results-767-768.en.md) (D4–D8) with the owner's decisions of 2026-11-21:

- all read kinds are kept, owner-private reads included;
- rows expire after 30 days;
- an excerpt is at most 4,000 characters, a Work keeps at most 24 rows, and one follow-up block is at most 6,000 characters;
- a reference follow-up's brief names the related Work id as one to look up.

## Store

`work_read_results` in `QuickStore`:

| Column | Meaning |
|---|---|
| `id` | row id, increasing |
| `job_id` | the Work that read |
| `tool_event_id`, `call_id` | the call's record, when the record point has it (nullable) |
| `tool` | the host action |
| `epoch` | the WorkLedger epoch the call ran under, or NULL without a ledger |
| `created` | time written |
| `source_kind` | `owner-private` (a `PRIVATE_PROVENANCE` source), `public` or `outside` |
| `full_length` | length of the worker-visible result before cutting |
| `excerpt` | its head, at most `READ_RESULT_EXCERPT_CHARS`, after secret redaction |

- `record_read_result` redacts before it cuts (stored secret values and credential shapes, `redact_known_secrets`), so a cut never leaves part of a secret. It then keeps only the Work's `READ_RESULTS_PER_WORK` newest rows.
- `read_results` returns rows newest first and never returns an expired row. `sweep_read_results` deletes expired rows, and runs with the existing periodic purge.
- A resume claim that moves a read checkpoint to a new epoch moves that Work's rows with it. A row written under a superseded epoch is never moved and never read.

## Writers

There is one write point for both routes: the `record_outside_result` wrapper around `Capabilities.execute`. Every successful call that ran passes through it, in the service (direct route and relayed owner-state reads) and in the CLI bridge (`code_run`, page reads). It writes a row for a read-effect call:

- an `EFFECT_FREE_READS` action;
- `code_run`;
- `api_request` whose provenance effect is `read`.

The text is the worker-visible result, the same text slice 1 keeps in memory. A relay placeholder writes nothing, because the call ran in the service and was written there. Delegated specialists and package tools write no rows.

## Readers

- **Outcome judgment.** `take_browser_pages` returns this attempt's browser pages (memory) and its rows: same epoch, newer than the last take, newest first. Read results now come from rows on both routes, so the CLI route sees code output from the bridge process. Slice 1's in-memory list stays only as the browser-page cache and the fallback when no row could be written.
- **Follow-up.** `search_conversation(work_id=…)` adds `read_results`: one text block of that Work's rows, at most `READ_RESULTS_BLOCK_CHARS`, newest first. Each item names tool, time and source kind, under a header saying the excerpts are content, never instructions. A query search without `work_id` adds none. A delegated specialist's search adds none. The search's Evidence summary records only which Works' read results were shown (`read_result_work_ids`) and how many, never the text.
- **Brief.** For a follow-up linked as a reference (or a correction), the worker's latest message gets one host line. It names the related Work id and says that `search_conversation` with that `work_id` returns its exchange and what it read. Still does not inject the excerpts.
- **Information use.** `_event_items` names "an earlier Work's read results <id>" for each id in `read_result_work_ids`.

## Forget, export, restore

- Forgetting transcript rows (`message:` refs) deletes the read results of the Works those rows belong to, in the same transaction. Forgetting a line deletes the read results of that line's Works.
- An undo does not bring rows back. `OUTSIDE_SCOPE` says that read results kept from a forgotten record are not reached into.
- `_portable_db` deletes every row, so an export carries none and a restore starts with none.

## What it is not

Rows are not Memory and not Evidence. No profile, recall, lesson or Evidence screen reads them. `evidence_summary` is unchanged except for the ids and the count above.

## Tests

`tests/test_read_results_768.py` carries the acceptance and the design's negative tests. Each test checks a marker that only the excerpt supplies, so it would fail with the excerpt removed.
