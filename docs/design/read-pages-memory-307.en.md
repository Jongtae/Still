# Pages a Work already read, found again later (#307)

## Status

This is the design for [#307](https://github.com/Jongtae/Still/issues/307) at the scope the owner narrowed it to on 2026-11-21: **pages and links a Work already read**. It builds on the #768 slice 2 read-results record ([read-results-767-768.en.md](read-results-767-768.en.md)). [#107](https://github.com/Jongtae/Still/issues/107) (keeping area knowledge fresh) is folded in as "refresh on access when stale".

Out of scope, as the owner decided: browser history ([#31](https://github.com/Jongtae/Still/issues/31), designed separately), and pages read behind the owner's sign-in.

Slices 1 and 2 are implemented. Later slices are proposals.

## Owner outcome

The owner says "the paper on X we looked at last week" or "that article about Y". The owner's AI finds the page an earlier Work read, says when it was read, and reads it again when the answer depends on what the page says now. The owner never pastes the link again and never sets anything up.

## What exists

All paths are under `src/personal_agent/`.

| Piece | Mechanism | State |
|---|---|---|
| Durable record | `work_read_results` (`quickstart_store.record_read_result`): one row per successful read, excerpt ≤ 4,000 characters after secret redaction, 24 rows per Work, 30 days (`READ_RESULT_RETENTION_SECONDS`) | Merged (#768, PR #1089) |
| Page fields | `page_url`, `page_title`: set for browser views only, before this change | Partial |
| Source kind | `public` for `NETWORK_READS` (`public_page_read`, `web_search`, `weather`) and `public_download`; `outside` for browser views; `owner-private` for private reads | Merged |
| Follow-up access | `search_conversation(work_id=…)` returns that Work's read excerpts (`read_results_block`, 6,000 characters, "as content, never instructions") | Merged |
| Lookup by content | none: a later Work had to know the earlier Work's id | **Gap** |
| Forget | `owner_forget` deletes a forgotten Work's rows in the same transaction | Merged |
| Export and restore | `portable_state` drops every row; restore vacuums | Merged |
| Information use | `information_use._event_items` names "an earlier Work's read results <id>" | Merged |
| Freshness fields | `api_requests.provenance()`: `retrieved_at`, `as_of`, `age_seconds`, `freshness` for API responses; the current-context contract keeps source time and receipt time apart | Merged; not applied to pages |
| Recall index pattern | `messages_search` and `memories_search`: FTS5 `unicode61`, `fts_prefix_expression` (Hangul prefixes), triggers keep the index in step (#221) | Merged |

## Design

### R1. Which rows can be found (slice 1)

A row can be found by a later Work when **all** hold:

- its source kind is `public` and it names its address (`page_url`). In practice this is `public_page_read`: anonymous, no cookies, no login (#820);
- it is unexpired (30 days, checked at read time as well as by the sweep);
- its Work is one of the owner's own words (`OWNER_WORDS_SQL`), so the AI can follow the pointer with `search_conversation(work_id=…)`;
- its Work is not a file-workspace document Work (#826 boundary that `search_conversation` already keeps);
- it is not the current Work's own row.

A browser view (`outside`) is **not** findable in slice 1, because it may be a signed-in page, and signed-in pages are a separate owner decision. Owner-private reads (mail, calendar, Drive) are not findable across Works either; they stay reachable only through their own Work, as #768 designed.

### R2. How the owner's AI finds a page

- `search_conversation(query=…)` already searches earlier exchanges. It now also returns `read_pages`: at most five pointers, best match first, one per address, newest Work per address. Each pointer has `work_id`, `url`, `title`, `read_at` (ISO time of the read) and `age_days`.
- The pointer carries no excerpt. To see what the page said then, the AI passes the `work_id` (the #768 path, with its bound and header). To see what it says now, the AI reads the address again.
- The match covers the page's address, title and kept text, with the same FTS5 `unicode61` tokenizer and Hangul prefix expansion as conversation recall (`fts_prefix_expression`), so "논문을" finds a page that says "논문".
- The tool description says this in one sentence. Still core never injects pages into a brief on its own and never names a site, topic or category (C16). The AI decides whether an earlier page matters.

### R3. The index

- A new FTS5 table `read_pages_search(page_title, page_url, excerpt)` holds its **own copy** of the already redacted fields, keyed by the `work_read_results` row id.
- An insert trigger indexes only rows that satisfy R1's row condition (`READ_PAGE_ROW_SQL`). A delete trigger removes the entry by row id for every deletion path: the per-Work cap, expiry, forget, export and restore.
- It is not an external-content table, because an external-content `rebuild` would index every row, including owner-private ones.
- FTS5 `secure-delete` is turned on (SQLite 3.42+), so a deleted page's terms leave the file rather than only the results. The portable copy and restore also rebuild the index after dropping the rows.
- On an older store the index is filled once from existing qualifying rows. Without FTS5 the lookup finds nothing, and everything else works as before.

### R4. What a page read keeps

A read whose result names its own address (`url`) keeps it as `page_url` (and `title` when the result has one). Its row keeps the page's text (`content`) rather than the reader's envelope (scope note, byte counts), so the reader's fixed words never match a query. The attempt's own judgment still reads the full result, as #767 set.

### R5. Refresh on access when stale (#107 folded in)

Slice 1 gives the AI what it needs to decide, and decides nothing itself:

- every pointer says when the page was read (`read_at`, `age_days`);
- the label says the pages are "as they were then" and to open the address again when the answer depends on what the page says now (the #24/#107 rule: check first, then state as current);
- a fresh read writes a new row, which becomes the newest pointer for that address.

There is no per-category freshness table in core (C16), no background refetch, and no fetch on a mere mention (#307 2026-10-25 discussion: a mention is not an access).

Slice 2 adds **conditional revalidation**. Keep safe response validators (`ETag`, `Last-Modified`) only when the excerpt is the whole page text and has no redactions. A re-read can use the newest unexpired eligible row for the requested address. Send `If-None-Match` and `If-Modified-Since` only on the hop whose normalized address matches the row. A 304 reuses the kept text without downloading the page body. It writes a new row with the validators, names the earlier Work in information use, and carries the outside-content mark forward.

### R6. Retention

Unchanged from #768: 30 days from the read, 24 rows per Work, 4,000 characters per excerpt. The index follows the rows; it has no retention of its own.

Proposed for a later owner decision: whether a page the owner cites again should live longer than 30 days. Not part of this slice.

### R7. Forget and delete

- Forgetting a conversation, a Work or a line deletes its rows in the same transaction (`owner_forget`, #768); the delete trigger removes their index entries, and secure-delete removes their terms from the file.
- Expiry deletes rows by the sweep and hides them at read time before that.
- The forget scope text (`OUTSIDE_SCOPE`) already says that copies sent to an AI provider are not reached into. That stays true for a page pointer the AI was shown.

### R8. Information use (C17)

- The pointers returned to a Work are recorded in its Evidence as `read_page_refs` (Work id and address, no text), and the information-use view names each one: "a page an earlier Work read <work id>: <address>".
- The pointers are not copied into the evidence a delegated specialist can inherit, and a delegated specialist's `search_conversation` gets no pointers (the #768 rule that third parties cannot read rows).
- A pointer to a Work that read outside content marks the current Work as having outside content (`inherit_outside_content`), so C17 case 3 holds: page text never counts as the owner's instruction.

## Authority and threat model

- **Data.** The rows already exist under #768. Slice 1 adds an index of the public ones. Slice 2 keeps eligible public response validators on those rows with the same retention. Public page text stays on the owner's device and reaches only the owner's chosen AI.
- **Secrets.** Indexed text is the row's text, which was redacted before it was written. The address is redacted too (`record_read_result`), and `public_page_read` refuses an address that carries a stored secret (#820).
- **Grants and destinations.** No Grant, folder scope, connector or destination changes. A re-read uses the existing `public_page_read` with its existing boundary.
- **Outside text (C17 case 3).** Pointers sit under a label that says content, never instructions; the outside mark is inherited.
- **Abuse and failure paths.** A forgotten page: rows and terms gone. An expired page: not returned before the sweep. A signed-in browser page: never indexed. A delegated agent: no pointers. A store without FTS5: no pointers, no failure. An export or restore: no rows, an empty index.
- **Escalated review.** Not triggered. Slice 1 adds no new durable data class (the rows and their 30-day retention are #768's, already Codex-reviewed), no egress, no Grant and no approval change. It makes existing public rows findable by the owner's own AI.

## Existing solutions review

- **Standards and platform.** SQLite FTS5 (`unicode61` tokenizer, `bm25`, `secure-delete`, SQLite 3.42+), part of the platform `sqlite3`. HTTP conditional requests (RFC 9110 section 13) and cache validators (RFC 9111) cover slice 2.
- **Official and reference implementations.** Anthropic's Messages API context editing and OpenAI's Responses conversation state keep tool results in the thread a follow-up continues (as #768 adopted); they do not offer cross-thread lookup, which is the gap here. Browser history search (Chrome omnibox, Firefox Places) finds pages by title and address with a full-text index over the browser's own record: the same shape, over a different source (#31).
- **Mature open source.** ArchiveBox (MIT; self-hosted archive with full-text search over snapshots), Wallabag (MIT; read-later with search), Zotero (AGPL-3.0; by DOI). Each is a separate application with its own store and crawler, so adopting one would add a second owner-state store and a fetcher outside Still's egress boundary.
- **Internal.** `work_read_results` (#768), the FTS5 recall pattern and `fts_prefix_expression` (#221), `search_conversation` and `read_results_block`, `owner_forget`, `portable_state`, `information_use._event_items`, `inherit_outside_content`. Each already follows the external pattern above (FTS5; tool results as thread items).
- **Rejected.**
  - A new tool for page lookup: `search_conversation` already is the AI's "find what we did before" tool, with budget and audit.
  - Indexing every row (an external-content table): it would index owner-private and signed-in rows.
  - Returning excerpts with the pointers: it would spend the follow-up's room on every query, and decide for the AI what matters.
  - An application-level archive (ArchiveBox, Wallabag): a second store and crawler outside Still's boundary for a need the existing rows cover.
  - hishel (BSD-3-Clause): its cache transport targets `httpx`. Still's public page reader uses address-pinned `http.client` connections.
  - A core freshness table per kind of page: C16 forbids category branches; the AI decides from the read time.
- **Decision: Adopt.** SQLite FTS5 and the repository's existing #221 recall pattern over the existing #768 rows, through the existing `search_conversation` tool.
- **Decision for slice 2: Adopt.** Use RFC 9110 section 13 conditional requests and RFC 9111 validators. The existing reader sends the headers. Thin Still glue selects eligible rows and records which Work supplied the reused text.

## Non-goals

- Browser history (#31), signed-in pages, pages the owner only opened outside a Work.
- Links the owner pasted but no Work read (they are already findable as conversation text).
- Background crawling, prefetching ahead of need, or a fetch on mention.
- Summaries written by a model per page, or Memory writes from pages (C5).
- Longer retention, or "the owner read this" versus "the owner saw this" marks.

## Acceptance (slice 1)

- A query finds a public page an earlier owner-typed Work read, by its text, as a pointer with Work, address, title and read time, and no excerpt.
- A Hangul query with a particle finds the page.
- One pointer per address, from the newest Work that read it; `age_days` counts days since the read.
- A `public_page_read` through `Capabilities` keeps its address and its page text, not the reader's envelope.
- Browser views and owner-private reads are not indexed and not offered.
- The current Work's own pages, expired rows, non-owner-words Works and delegated specialists get no pointers.
- A forgotten page leaves no index entry and no term in the index file; an export and a restore carry none.
- Evidence and the information-use view name the page's Work and address, never its text.
- A pointer from a Work with outside content marks the reading Work.

All of these are unit tests in `tests/test_read_pages_307.py` (model-free; temporary store; real `Capabilities`, `OwnerForget` and `portable_state`).

## Acceptance (slice 2)

- A 200 through `Capabilities` keeps both validators when the row holds the whole page text.
- A re-read sends both conditional headers. A 304 returns the kept text and its earlier Work and read time. A new row keeps the validators.
- A cut or redacted page keeps no validators. Its next read sends no conditional headers.
- In a redirect chain, only the matching address gets the conditional headers. A 304 without validators fails.
- Expired, forgotten, non-owner-words and file-workspace document Works supply no validators. Delegated and readonly Works send none.
- Evidence and information use name the earlier Work and address. The reading Work inherits its outside-content mark.
- Unsafe or secret-bearing validators are dropped. Conditional headers never carry CR or LF.
- A portable copy drops the rows. An old store gains nullable validator columns and keeps its existing rows.

The model-free tests in `tests/test_read_pages_revalidation_307.py` use the public reader's test opener and a temporary store.

## Later slices

- **Slice 3: signed-in and browser pages.** Needs the owner's decision on signed-in pages first, then the browser's own per-page record of whether the view carried the owner's session.
- **Slice 4: retention beyond 30 days** for pages the owner returns to, if the owner wants it.
