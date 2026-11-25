# Pages a Work already read, found again later (#307)

## Status

This is the design for [#307](https://github.com/Jongtae/Still/issues/307) at the scope the owner narrowed it to on 2026-11-21: **pages and links a Work already read**. It builds on the #768 slice 2 read-results record ([read-results-767-768.en.md](read-results-767-768.en.md)). [#107](https://github.com/Jongtae/Still/issues/107) (keeping area knowledge fresh) is folded in as "refresh on access when stale".

Browser history ([#31](https://github.com/Jongtae/Still/issues/31), designed separately) is out of scope.

Slices 1 and 2 are merged. Signed-in and anonymous browser views are the current scope in slice 3 ([#1219](https://github.com/Jongtae/Still/issues/1219)), following the owner's decision under C17.

## Owner outcome

The owner says "the paper on X we looked at last week" or "that article about Y". The owner's AI finds the page an earlier Work read, says when it was read, and reads it again when the answer depends on what the page says now. The owner never pastes the link again and never sets anything up.

## What exists

All paths are under `src/personal_agent/`.

| Piece | Mechanism | State |
|---|---|---|
| Durable record | `work_read_results` (`quickstart_store.record_read_result`): one row per successful read, excerpt ≤ 4,000 characters after secret redaction, 24 rows per Work, 30 days (`READ_RESULT_RETENTION_SECONDS`) | Merged (#768, PR #1089) |
| Page fields | `page_url`, `page_title`, public HTTP validators, and nullable `page_signed_in` for browser views | Slices 1–3 |
| Source kind | `public` for `NETWORK_READS` (`public_page_read`, `web_search`, `weather`) and `public_download`; `outside` for browser views; `owner-private` for private reads | Merged |
| Follow-up access | `search_conversation(work_id=…)` returns that Work's read excerpts (`read_results_block`, 6,000 characters, "as content, never instructions") | Merged |
| Lookup by content | `find_read_pages` returns page pointers from the FTS5 index | Slices 1–3 |
| Forget | `owner_forget` deletes a forgotten Work's rows in the same transaction | Merged |
| Export and restore | `portable_state` drops every row; restore vacuums | Merged |
| Information use | `information_use._event_items` names "an earlier Work's read results <id>" | Merged |
| Freshness fields | `api_requests.provenance()`: `retrieved_at`, `as_of`, `age_seconds`, `freshness` for API responses; the current-context contract keeps source time and receipt time apart | Merged; not applied to pages |
| Recall index pattern | `messages_search` and `memories_search`: FTS5 `unicode61`, `fts_prefix_expression` (Hangul prefixes), triggers keep the index in step (#221) | Merged |

## Design

### R1. Which rows can be found (slices 1 and 3)

A row can be found by a later Work when **all** hold:

- it names its address (`page_url`) and is either a `public` read, typically the anonymous `public_page_read` (#820), or an `outside` browser view from a read-type action with an HTTP(S) address;
- it is unexpired (30 days, checked at read time as well as by the sweep);
- its Work is one of the owner's own words (`OWNER_WORDS_SQL`), so the AI can follow the pointer with `search_conversation(work_id=…)`;
- its Work is not a file-workspace document Work (#826 boundary that `search_conversation` already keeps);
- it is not the current Work's own row.

The browser records session use on the mediated view, including views used by `browser_find`. It labels a view signed in when the worker imported cookies for the landed site or currently holds cookies for that site, including cookies established during an unrecorded sign-in flow. Views within an observed provider sign-in flow and later views of its destination also carry the signed-in label. The label describes the browser's session context. The server decides whether that session is still valid. Unknown provenance, including unconfirmed cookie imports and legacy browser rows, is labeled signed in. Pseudo URLs, sign-in requests without a view, and owner-private rows are excluded. Owner-private rows stay reachable through their own Work as #768 designed.

### R2. How the owner's AI finds a page

- `search_conversation(query=…)` also returns `read_pages`, at most five pointers. It takes the newest matching Work per address and orders those pointers by relevance. Each pointer has `work_id`, `url`, `title`, `read_at` (ISO time of the read), `age_days` and a boolean `signed_in`.
- The pointer carries no excerpt. To see what the page said then, the AI passes the `work_id` (the #768 path, with its bound and header). To see what it says now, the AI reads the address again.
- The match covers the page's address, title and kept text, with the same FTS5 `unicode61` tokenizer and Hangul prefix expansion as conversation recall (`fts_prefix_expression`), so "논문을" finds a page that says "논문".
- The tool description says this in one sentence. Still core never injects pages into a brief on its own and never names a site, topic or category (C16). The AI decides whether an earlier page matters.

### R3. The index

- A new FTS5 table `read_pages_search(page_title, page_url, excerpt)` holds its **own copy** of the already redacted fields, keyed by the `work_read_results` row id.
- An insert trigger indexes only rows that satisfy R1's row condition (`READ_PAGE_ROW_SQL`). A delete trigger removes the entry by row id for every deletion path: the per-Work cap, expiry, forget, export and restore.
- It is not an external-content table, because an external-content `rebuild` would index every row, including owner-private ones.
- FTS5 `secure-delete` is turned on (SQLite 3.42+), so a deleted page's terms leave the file rather than only the results. The portable copy and restore also rebuild the index after dropping the rows.
- The slice 3 migration reclassifies qualifying historical views that the browser writer stored as `owner-private` to `outside`. It replaces the insert trigger and backfills qualifying rows by row id once. The migration records its version in the same transaction. Without FTS5 the lookup finds nothing, and everything else works as before.

### R4. What a page read keeps

A read whose result names its own address (`url`) keeps it as `page_url` (and `title` when the result has one). Its row keeps the page's text (`content`) rather than the reader's envelope (scope note, byte counts), so the reader's fixed words never match a query. The attempt's own judgment still reads the full result, as #767 set.

### R5. Refresh on access when stale (#107 folded in)

Slice 1 gives the AI what it needs to decide, and decides nothing itself:

- every pointer says when the page was read (`read_at`, `age_days`);
- the label says the pages are "as they were then" and to open the address again when the answer depends on what the page says now (the #24/#107 rule: check first, then state as current);
- a fresh read writes a new row, which becomes the newest pointer for that address.

There is no per-category freshness table in core (C16), no background refetch, and no fetch on a mere mention (#307 2026-10-25 discussion: a mention is not an access).

Slice 2 adds **conditional revalidation** to `public_page_read` only. Keep safe response validators (`ETag`, `Last-Modified`) only when the excerpt is the whole page text and has no redactions. A re-read can use the newest unexpired eligible public reader row for the requested address. Send `If-None-Match` and `If-Modified-Since` only on the hop whose normalized address matches the row. A 304 reuses the kept text without downloading the page body. It writes a new row with the validators, names the earlier Work in information use, and carries the outside-content mark forward.

### R6. Retention

Unchanged from #768: 30 days from the read, 24 rows per Work, 4,000 characters per excerpt. The index follows the rows; it has no retention of its own.

Proposed for a later owner decision: whether a page the owner cites again should live longer than 30 days. Not part of this slice.

### R7. Forget and delete

- Forgetting a conversation, a Work or a line deletes its rows in the same transaction (`owner_forget`, #768); the delete trigger removes their index entries, and secure-delete removes their terms from the file.
- Expiry deletes rows by the sweep and hides them at read time before that.
- The forget scope text (`OUTSIDE_SCOPE`) already says that copies sent to an AI provider are not reached into. That stays true for a page pointer the AI was shown.

### R8. Information use (C17)

- Evidence records each returned pointer in `read_page_refs` with its Work id, address and `signed_in` when true. The information-use view labels a signed-in reference "a page an earlier Work read signed in <work id>: <address>". Page text stays out of the evidence.
- The pointers are not copied into the evidence a delegated specialist can inherit, and a delegated specialist's `search_conversation` gets no pointers (the #768 rule that third parties cannot read rows).
- A pointer to a Work that read outside content marks the current Work as having outside content (`inherit_outside_content`), so C17 case 3 holds: page text never counts as the owner's instruction.

## Authority and threat model

- **Data.** Slice 3 makes existing mediated browser reads, including pages behind the owner's sign-in, findable by the owner's AI in later Works. The search indexes the bounded, redacted excerpt and returns only a pointer. The browser supplies its session label, including conservative labels for unknown views.
- **Secrets.** Browser mediation strips userinfo, query and fragment from view addresses. `record_read_result` redacts stored secret values and credential shapes from the saved address, title and excerpt before indexing. The session flag carries no cookie value. `public_page_read` refuses addresses carrying a stored secret (#820).
- **Grants and destinations.** A pointer grants no access. Any new read follows the existing public reader or owner-browser authority and destination rules. Revalidation uses public reader rows only.
- **Outside text (C17 case 3).** Pointers sit under a label that says content, never instructions; the outside mark is inherited.
- **Abuse and failure paths.** The host supplies the sign-in label independently of tool arguments. Delegated agents receive no pointers. Expiry hides rows before the sweep, and forget removes rows and index terms. Portable copies and restores contain no rows and an empty index. A store without FTS5 continues without lookup.
- **Escalated review.** Triggered for slice 3 because it widens which owner data a later Work can find.

## Existing solutions review

- **Standards and platform.** SQLite FTS5 (`unicode61` tokenizer, `bm25`, `secure-delete`, SQLite 3.42+), part of the platform `sqlite3`. HTTP conditional requests (RFC 9110 section 13) and cache validators (RFC 9111) cover slice 2.
- **Official and reference implementations.** Anthropic's Messages API context editing and OpenAI's Responses conversation state keep tool results in the thread a follow-up continues (as #768 adopted); they do not offer cross-thread lookup, which is the gap here. Browser history search (Chrome omnibox, Firefox Places) finds pages by title and address with a full-text index over the browser's own record: the same shape, over a different source (#31).
- **Mature open source.** ArchiveBox (MIT; self-hosted archive with full-text search over snapshots), Wallabag (MIT; read-later with search), Zotero (AGPL-3.0; by DOI). Each is a separate application with its own store and crawler, so adopting one would add a second owner-state store and a fetcher outside Still's egress boundary.
- **Internal.** `work_read_results` (#768), the FTS5 recall pattern and `fts_prefix_expression` (#221), `search_conversation` and `read_results_block`, `owner_forget`, `portable_state`, `information_use._event_items`, `inherit_outside_content`. Each already follows the external pattern above (FTS5; tool results as thread items).
- **Rejected.**
  - A new tool for page lookup: `search_conversation` already is the AI's "find what we did before" tool, with budget and audit.
  - Indexing every row (an external-content table): it would index owner-private and non-page rows.
  - Returning excerpts with the pointers: it would spend the follow-up's room on every query, and decide for the AI what matters.
  - An application-level archive (ArchiveBox, Wallabag): a second store and crawler outside Still's boundary for a need the existing rows cover.
  - hishel (BSD-3-Clause): its cache transport targets `httpx`. Still's public page reader uses address-pinned `http.client` connections.
  - A core freshness table per kind of page: C16 forbids category branches; the AI decides from the read time.
- **Decision: Adopt.** SQLite FTS5 and the repository's existing #221 recall pattern over the existing #768 rows, through the existing `search_conversation` tool.
- **Decision for slice 2: Adopt.** Use RFC 9110 section 13 conditional requests and RFC 9111 validators. The existing reader sends the headers. Thin Still glue selects eligible rows and records which Work supplied the reused text.
- **Decision for slice 3: Adopt.** Extend the existing [SQLite FTS5](https://sqlite.org/fts5.html) trigger and #768 row lifecycle. Reuse the worker's cookie-import acknowledgment from the existing WebKit cookie store and [Playwright browser context](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-add-cookies), and the existing observed provider-flow state. The parent derives the boolean inside the current worker boundary. This uses the pinned browser engines and existing SQLite dependency. A separate browser history reader or request-header recorder would add access to data beyond the mediated Work views.

## Non-goals

- Browser history (#31) and pages the owner only opened outside a Work.
- Links the owner pasted but no Work read (they are already findable as conversation text).
- Background crawling, prefetching ahead of need, or a fetch on mention.
- Summaries written by a model per page, or Memory writes from pages (C5).
- Longer retention, or "the owner read this" versus "the owner saw this" marks.

## Acceptance (slice 1)

- A query finds a public page an earlier owner-typed Work read, by its text, as a pointer with Work, address, title and read time, and no excerpt.
- A Hangul query with a particle finds the page.
- One pointer per address, from the newest Work that read it; `age_days` counts days since the read.
- A `public_page_read` through `Capabilities` keeps its address and its page text, not the reader's envelope.
- Owner-private rows and non-browser outside reads are excluded.
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

## Acceptance (slice 3)

- Browser views with and without a session return labeled pointers with no page text. Unknown and legacy browser views carry the signed-in label. Tool arguments cannot override the host label.
- Search returns one pointer per address from the newest matching read. Delegated specialists get no pointers, and the owner's later Work inherits the earlier Work's outside-content mark.
- Evidence names the Work and address and records the signed-in flag when true. Information use shows the signed-in label.
- Pseudo or non-HTTP(S) browser addresses, owner-private rows and non-view action receipts stay out of the index. Stored secret values and credential shapes are redacted before indexing. Revalidation excludes browser views.
- Upgrading a legacy store indexes qualifying browser views once and keeps existing public entries. Forget, expiry, portable export and restore remove browser rows and their index entries, with expired rows hidden before the sweep.

The model-free tests in `tests/test_read_pages_browser_307.py` use a temporary store, the browser driver's test worker, real `Capabilities`, `OwnerForget` and `portable_state`.

## Later slices

- **Slice 4: retention beyond 30 days** for pages the owner returns to, if the owner wants it.
