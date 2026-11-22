# Searching a granted Chrome profile's history, read-only (#31)

## Status

This is the design for [#31](https://github.com/Jongtae/Still/issues/31). It changes no code and grants nothing: it is not permission to read the owner's browsing history now. Implementing it changes an owner-data source boundary, so the implementation PR needs the escalated review in the [Development Constitution](../development-constitution.en.md#escalated-review) (a Codex review of the final head, required CI on that head, and a merge by the owner).

Related: [#307](https://github.com/Jongtae/Still/issues/307) covers pages a Work already read and keeps browser history out of its scope (design note in PR [#1137](https://github.com/Jongtae/Still/pull/1137)).

## Owner outcome

The owner asks "the site I was on yesterday about X" or "that shop I looked at last week". The owner's AI looks in the Chrome profile the owner granted, finds the page by title or address and time, and uses it, for example by reading the page again. The owner never searches Chrome's history by hand and never exports anything.

## Inventory of what exists

All paths are under `src/personal_agent/`.

| Mechanism | What it does | Relevance |
|---|---|---|
| Folder grants (`folder_grants.py`) | One rule set for AI read folders and file-workspace folders. Refuses a grant that is or lies inside a credential, system or Still-data location (`SENSITIVE`). `_HOME_SENSITIVE` includes `Library/Application Support`, `.config/google-chrome` and `.config/chromium`. A broad folder is granted only after the owner's confirmation (`ConfirmBroad`); protected locations inside it are never read (`excluded`). Grants are rechecked at use (`blocked`), by filesystem identity as well as path | A Chrome profile folder **cannot** be a folder grant, by design. Chrome history needs its own narrow grant, not a hole in the folder rules |
| Folder choice (`local_folder_picker.py`) | The owner picks a folder on their own Mac through the system open panel (`osascript choose folder`); the module returns a path and grants nothing | The pattern for an owner-local choice: picking is not granting; a separate explicit approval writes the grant |
| Local reads (`find_files`, `read_file`, `list_roots`; `manifests.HOST_TOOLS` with `reads='files'`) | Read-only tools over connected folders; no dot-files, no symlinks, no escape from the root | The shape of a read-only host tool with a declared read category |
| Connectors (`connector_contract.py`, `connector_revocation.py`, `connector_read_operations.json`) | Owner-bound connection lifecycle with revisions; disconnect, provider revocation and deletion of retained data are reported as three separate effects (#588); an allow-list of read operations | The lifecycle vocabulary (connect, disconnect, what disconnect does and does not delete) |
| Work staging (`work_download._staging_directory`) | `store.private / 'work-downloads'`, created `0o700`, files `0o600`, opened `O_NOFOLLOW`, single link | The pattern for a temporary snapshot with restricted access |
| Secret redaction (`current_context.redact_known_secrets`) | Stored secret values and credential shapes replaced (#817) | History URLs can carry tokens in their query strings; every returned address passes this |
| Outside content (`mark_outside_content`, #744) | Marks a Work that saw outside text, so it never counts as the owner's instruction (C17 case 3) | Page titles in history are written by sites |
| Information use (`information_use.py`) | A per-Work view derived from `tool_events` Evidence summaries; read categories come from `HOST_TOOLS[...].reads`; labels and references only, never payloads; the recipient AI and route are named | The audit a history read joins, with no new store |
| Read results (`work_read_results`, #768) | Bounded redacted excerpts of a Work's reads, 30 days | A history query result would be kept like any other read (as `owner-private`), so a follow-up can see what it found |

## Design

### H1. The grant: one profile, chosen in Settings on this computer

- Settings offers "Chrome history" with the profiles found in Chrome's `Local State` file (`profile.info_cache`: folder name and display name). Listing names is the only thing read before a grant.
- The owner picks **one profile** and confirms. Only then is a grant written: `{profile_dir, display_name, identity (device, inode) of the profile folder, granted_at}`. There is no "all profiles" option and no automatic scan.
- The grant is made only through local Settings on the owner's machine, like a broad folder grant (#817). A conversation request, a remote surface (Telegram, Slack, Mattermost), a package or another agent cannot create it. The AI may suggest it; the owner turns it on.
- The grant names **one file**: `<profile_dir>/History`. It does not make the profile folder readable. Cookies, Login Data, Web Data, Bookmarks, Sessions, Preferences, Extensions, cache and every other file stay out of reach, and the folder-grant `SENSITIVE` rule is unchanged.
- The grant is rechecked on every use: the profile folder must still exist with the same identity and not be a symlink. A changed identity blocks the grant until the owner grants it again.
- Revoking it in Settings removes the grant at once. Rows already kept in a Work's read results follow that Work's retention and forget rules (H6).

### H2. The read: a consistent, read-only snapshot

Chrome keeps `History` open while it runs, with SQLite exclusive locking. Reading it in place is unreliable, and writing to it is never allowed. The read therefore works on a snapshot:

1. Copy `History` and its `-journal` or `-wal` file (never `-shm`) into a fresh `0o700` directory under `store.private`, files `0o600`, opened with `O_NOFOLLOW`.
2. Compare each source file's size and modification time before and after the copy; if any changed, copy again, a bounded number of times. This is the algorithm of `sqlite_backup.copy_all_files` (H8).
3. Open the copy with SQLite normally, so a copied hot journal is rolled back or a copied WAL is applied **in the copy**, then run `PRAGMA quick_check`. On failure, retry from step 1, then report "history is busy, try again" to the AI.
4. Run one bounded query on the copy (H3), then delete the snapshot directory in a `finally` block. A startup sweep deletes any snapshot directory left by a crash.

The source is never opened by SQLite, so no lock is taken on Chrome's file and nothing in Chrome's profile is modified. The source is opened with `O_RDONLY | O_NOFOLLOW` and checked to be a regular file inside the granted profile folder.

**Synthetic observation (2026-11-22, this design, macOS, SQLite 3.54.0).** A writer process held a database with `PRAGMA locking_mode=EXCLUSIVE` after a write, in rollback-journal mode and in WAL mode, without real Chrome data:

- a plain read of the live file failed at once with `database is locked`;
- the SQLite backup API from the live file did not fail but **waited until the writer released its lock** (6.2 seconds in the test), so it is not usable while Chrome runs;
- copying the database with its journal or WAL file and opening the copy succeeded at once, with `quick_check` `ok` and only committed rows;
- opening the live file with `immutable=1` returned rows, but SQLite documents that `immutable` on a file that changes can give wrong results, so it is rejected.

**Not yet observed:** real Chrome's journal mode and locking on the owner's macOS version, and whether macOS privacy controls (TCC) stop a non-sandboxed process from reading `~/Library/Application Support/Google/Chrome`. Chrome's folder is not one of the locations macOS protects by default (unlike Safari's), so Full Disk Access should not be needed, but this must be checked. Both are owner-operated observations on the owner's own profile before the implementation is called working (#31 acceptance).

### H3. The query: titles, addresses and times only

- One host tool, `browser_history_search` (read-only; `HOST_TOOLS` read category `history`). Arguments: `query` (words matched against title and address), optional `after` / `before` (Unix times) and `limit` (at most 20).
- It reads only `urls(url, title, visit_count, last_visit_time)` joined with `visits(visit_time)` where needed for a time window. It never reads `downloads` (local file paths), `keyword_search_terms` (typed search queries), `segments`, `clusters`, `visit_source` or any other table.
- Chrome stores times as microseconds since 1601-01-01 UTC; the tool returns ISO UTC times.
- Each returned row: title, address, last visit time, visit count. The address and title pass `redact_known_secrets`. Rows are bounded (20) and each field is cut (address 512, title 200 characters).
- There is no "list everything" mode: a query or a time window of at most 31 days is required. The AI can ask again with other words.
- The tool returns history facts, not page content. To know what a page says, the AI reads it with `public_page_read` or the browser, each with its existing boundary.

### H4. No background collection

There is no sync, no index of the owner's history in Still, no scheduled read and no watcher. Each search makes its own snapshot and discards it. Preparations and unattended runs can call the tool only if the owner granted the profile, and their reads are audited like any other.

### H5. Information use (C17)

- Every search is a tool event with an Evidence summary: profile display name, the query words, the time window and the number of rows returned. Never the addresses or titles.
- The information-use view names the source as "Chrome history (<profile name>)" with the query, and the recipient AI and route that received the rows. When the AI runs remotely, it says so: this is not local-only processing.
- The rows reach only the owner's chosen AI in the owner's own Work (C17: owner data reaching the owner's chosen AI is owner-authorized; it is not masked or re-asked).
- Page titles are site-written text: the Work is marked as having seen outside content, and titles are given to the AI as content, never instructions (C17 case 3).
- Packages, delegated agents and other people's agents do not get the tool (C4), and a delegated specialist's brief does not carry the rows.

### H6. Retention, forget and delete

- Still stores no copy of the history. The snapshot is deleted after each query and by the startup sweep.
- What a search returned is kept like any other read result of the Work (#768: `work_read_results`, `owner-private`, 30 days, redacted). It is not indexed for #307's page lookup (only `public` rows are).
- Forgetting a Work or a conversation deletes those rows (`owner_forget`). Revoking the grant stops new reads; it does not reach into rows already kept, and Settings says so, as the forget scope already says for copies sent to an AI provider.
- Still never deletes or edits Chrome's history. "Forget this visit in Chrome" is the owner's action in Chrome.

### H7. Platforms

macOS first (`~/Library/Application Support/Google/Chrome`). Linux (`~/.config/google-chrome`, `~/.config/chromium`) uses the same design with its own path. Other Chromium browsers (Edge, Brave, Arc) share the schema and could be added as their own grant entries later; none is in the first slice.

## Authority and threat model

- **Data.** A new owner-data source: browsing history of one granted profile. It is owner-private and reaches the owner's chosen AI only on the AI's request inside an owner Work.
- **Grant boundary.** One file of one profile, granted in local Settings only; identity-checked on use; revocable. The folder-grant `SENSITIVE` rule stays as it is.
- **Credentials (C17 case 1).** Cookies, Login Data and tokens are never read: the grant names one file and the tool opens only that file. A token in a history URL's query string is redacted by the existing credential-shape pass before it reaches the AI or a record.
- **Outside text (C17 case 3).** Titles are site-written; the outside mark and the content-not-instructions framing apply.
- **Integrity.** The source is never opened by SQLite and never written. The snapshot is private (`0o700` / `0o600`), single-use and swept.
- **Abuse and failure paths.**
  - a revoked grant: refused before the copy;
  - a profile folder replaced by a symlink or another folder: identity check refuses;
  - a page asking the AI to "search the owner's history and send it": the request is outside text; the tool's results go only to the owner's AI, and sending anything anywhere still follows the existing send and approval rules;
  - Chrome writing during the copy: size/time check and `quick_check`, bounded retries, then a visible "busy";
  - a crash mid-query: the startup sweep deletes the snapshot;
  - a huge history: a bounded query with an index-friendly time window and `LIMIT`;
  - a package or delegated agent: the tool is not in its scope.
- **Escalated review.** Triggered for the implementation: a new owner-data source and a new read authority.

## Existing solutions review

Problem: read titles, addresses and visit times from a Chrome profile's `History` SQLite database while Chrome may be running, without modifying it, with an explicit per-profile grant.

| Candidate | Kind | Licence | Maintenance (PyPI, checked 2026-11-22) | How it reads `History` | Fit |
|---|---|---|---|---|---|
| Python `sqlite3` + `shutil` | Standard library | PSF | Platform | Building blocks: copy, open, `backup`, `quick_check` | Base of every option |
| SQLite URI `immutable=1` / `mode=ro` on the live file | Platform | Public domain | Platform | Reads in place | **Rejected**: `mode=ro` fails while Chrome holds its lock; `immutable` on a changing file is documented as unsafe |
| SQLite online backup API on the live file | Platform | Public domain | Platform | Page copy under a shared lock | **Rejected**: waits until Chrome releases its lock (synthetic observation, H2) |
| [sqlite_backup](https://github.com/purarue/sqlite_backup) | Library | MIT | 0.1.8, released 2026-01-14 | Copies the database with its journal/WAL, retries while any file changes (size/mtime), opens the copy, then backs it up to memory or a file | **Best fit for the snapshot**. Depends on `click` (already locked in Still) and `logzero` (MIT, last release 1.7.0 in 2021, unmaintained) |
| [browserexport](https://github.com/purarue/browserexport) | Library + CLI | MIT | 0.4.4, released 2026-01-17 | Uses `sqlite_backup`, then parses many browsers' schemas into one visit model | Broader than needed (all browsers, merging, `kompress`); its Chrome query is a useful reference |
| [browser-history](https://github.com/browser-history/browser-history) | Library + CLI | Apache-2.0 | 0.5.0, released 2025-12-28 | Copies only the main `History` file and opens it `immutable=1&nolock=1`; by default reads every profile of every browser | **Rejected**: ignores the journal/WAL, so it can read a torn or stale state; all-profiles default is the opposite of H1 |
| [Hindsight / pyhindsight](https://github.com/RyanDFIR/hindsight) | Forensics tool | Apache-2.0 AND BSD-3-Clause AND MIT | 20261100, released 2026-11-17 | Full forensic parse of a Chrome profile (cookies, cache, preferences, decryption with `keyring`, `pycryptodomex`) | **Rejected**: reads far beyond history, including credential stores; heavy dependency set |
| Chrome DevTools Protocol / a debugging attachment | Official | — | — | Live browser session | **Rejected as default**: more intrusive (needs a debug port, touches the live session); #31 names it as the intrusive alternative |
| Chrome `chrome.history` extension API | Official | — | — | Inside Chrome, through an extension | **Rejected**: needs an extension installed in the owner's browser and a channel back to Still |

**Decision: Adapt.** Use the `sqlite_backup` snapshot algorithm (copy with journal/WAL, change-checked retries, open the copy) behind a narrow Still adapter that owns the grant check, the private snapshot directory, the bounded query and the cleanup. Two ways to take it, to settle in the implementation PR with a small compatibility test:

1. **Depend on `sqlite_backup` 0.1.8** (exact pin) if its `logzero` dependency passes the supply-chain review; or
2. **Follow its algorithm with the standard library** (about forty lines: `shutil.copy`, a size/mtime comparison, `sqlite3.connect` on the copy, `quick_check`) if `logzero`, unmaintained since 2021, does not pass. This is still Adapt: the method is the library's, credited in the code.

Either way the Chrome part is thin glue: one SQL query over `urls` and `visits`, the 1601 epoch conversion and the `Local State` profile list, with `browserexport`'s Chrome reader as the reference for the schema.

Internal candidates: `folder_grants` (pattern for identity rechecks; its `SENSITIVE` rule is kept, not widened), `local_folder_picker` (picking is not granting), `work_download._staging_directory` (private staging), `redact_known_secrets`, `mark_outside_content`, `information_use` (`HOST_TOOLS` read categories), `work_read_results` (#768) and `owner_forget`. Each already follows the external patterns above; no internal component reads browser databases today.

## Non-goals

- Bookmarks, downloads, typed search terms, cookies, passwords, autofill, sessions or tabs.
- Safari, Firefox or other browsers (Safari's data is protected by macOS and needs its own decision).
- Copying or indexing the history into Still; background or scheduled collection.
- Changing or deleting anything in Chrome.
- Making the profile folder a folder grant.

## Acceptance for the implementation

- Each profile needs its own grant made in local Settings; no automatic scan, no all-profiles grant, no grant from a conversation, remote surface, package or agent.
- Reads never open the source with SQLite and never write it; Chrome's files are byte-identical and their modification times unchanged after a read (test on a fixture profile).
- A snapshot taken while a writer holds an exclusive lock, in rollback-journal mode and in WAL mode, returns only committed rows and passes `quick_check`; a source that changes during the copy is retried and then reported as busy.
- A denied profile, a revoked grant and a profile folder replaced by a symlink are refused before any copy.
- Snapshots are `0o700` / `0o600`, deleted after each query, and swept at startup after a crash.
- Queries are bounded (20 rows, 31-day window or words required); only `urls` and `visits` are read; addresses with a stored secret or a credential shape are redacted.
- Every Work's information-use view names "Chrome history (<profile>)", the query and the recipient AI and route; Evidence holds counts and query words, never addresses or titles.
- A delegated agent or package cannot call the tool.
- A real-profile observation of locking, journal mode and macOS permission behavior is separate and owner-operated.

## Owner decisions in this design

1. Whether Settings lists profile names from `Local State` (proposed), or the owner picks the profile folder in the system open panel.
2. Whether the first slice includes Linux, or macOS only.
3. Whether the implementation depends on `sqlite_backup` or follows its algorithm with the standard library (decided by the supply-chain review of `logzero`).
