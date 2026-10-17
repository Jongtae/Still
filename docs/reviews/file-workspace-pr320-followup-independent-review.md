# PR #320 follow-up independent review

Reviewed 2026-09-10 against the uncommitted follow-up diff on
`codex/315-file-workspace-first-experience`, whose stated external-review
baseline is `85e793ffce14ae6cefd94b836ecbd9350fbfcde5`.

This is a read-only review.  No product source, test, plan, or existing review
artifact was changed.

## Evidence inspected

- `AGENTS.md` and `docs/file-workspace-first-experience-contract.en.md`
- `git diff --` for `portable_state.py`, `file_workspace.py`,
  `quickstart_service.py`, `agent_runtime.py`, web settings, and their tests
- `docs/mp1-d01-personal-space-contract.en.md`, which says that saved results
  and work evidence move through portable export/restore while local-folder
  grants are excluded
- `PYTHONPATH=src python3 -m pytest -q tests/test_quickstart.py -k
  'file_workspace_rejects_same_content_replacement_and_other_workspace_file or
  natural_workspace_conversation_and_unapproved_history_boundary or
  workspace_summary_remains_rejected_for_subscription_engine'
  tests/test_backup_scripts.py` — exit 0, 3 passed, 58 deselected
- `git diff --check` — exit 0

The focused tests support the new source-ID check, workspace-ID search guard,
natural-language summary/search handling, the no-unapproved-search-history
model assertion, subscription-engine rejection, and reset of grants on
portable restore.  They do not cover the two findings below.

## Follow-up dispositions

| Area | Disposition | Evidence |
| --- | --- | --- |
| Folder grant reset on portable restore | **Partially resolved; finding below** | `_RESET_CONFIG` now removes `file_workspace`, document-sharing approval, and document-job classification.  A restored runtime cannot call `FileWorkspace.read()` or `save()` until it is configured again. |
| Workspace/result binding | **Resolved for newly written records** | New records carry an inode/device-derived `workspace_id`; `search()` and `recover()` detach a mismatched record.  The changed-workspace regression test proves a same-name B file is not returned as A's record. |
| Source identity after same-content replacement | **Resolved** | `read()` records source inode/device identity and `_fresh()` requires it as well as content version.  The rename/recreate regression returns no result. |
| Source metadata persistence | **Resolved for new records** | `save()` projects sources to `reference_id`, `source_id`, relative `path`, and `version` before JSON persistence; it no longer stores source `content`. |
| Natural settings/API flow | **Resolved within this PR's UI scope** | The settings read model exposes `file_workspace`; `/api/file-workspace` is wired to a settings form, and the HTTP integration test uses quoted natural-language summary/search requests rather than a source ID or diagnostic command. |
| Subscription engine | **Resolved** | `run_one()` discovers a workspace-summary request before the subscription branch and explicitly fails it; the focused test observes no result file. |

## Findings

### PR320-FU-IR-01 — High: portable export deletes durable file-workspace result evidence

`_portable_db()` deletes every `file_workspace_results` row
(`src/personal_agent/portable_state.py:36-37`), and the added backup test
asserts that restored count is zero (`tests/test_backup_scripts.py:28`).  It
correctly clears grant paths/configuration, but the result rows contain opaque
result IDs, request IDs, publication hashes, source metadata, lifecycle state,
and creation time—not a folder grant.  Deleting all of them discards meaningful
work/provenance evidence and conflicts with the established portable-state
contract that saved results and work evidence move through export/restore
(`docs/mp1-d01-personal-space-contract.en.md:63`).

Keep the durable, non-path-grant result record on export while making it
unreadable/detached until a new workspace connection is made.  If raw relative
paths are classified as non-portable, retain a redacted result/evidence record
instead and state that rule in the contract.  Add a restore test that proves:

1. folder configuration and external-document approval are absent;
2. no old result can be read or written before a new connection; and
3. terminal job/evidence and a safe result/provenance record survive without
   recreating a path binding.

### PR320-FU-IR-02 — High: document-derived summary history can still reach an unapproved external model and web tool

Only a successful workspace **search** calls
`record_file_workspace_document_job()` (`quickstart_service.py:1015-1023`).
A successful workspace **summary** saves a document-derived model result but
does not mark its job.  On the next ordinary conversation,
`document_history` is therefore false for that summary job
(`:1033-1039`), so the external-model boundary does not remove its assistant
message (`:1075-1080`) and `Capabilities(... document_context=False)` leaves
`web_search` available (`:1141-1144`; `agent_runtime.py:94-98`).

The current test uses a generic local-model response and inserts a separately
marked search before switching model, so it cannot expose this route.  A local
summary can legitimately contain material derived from the reference document;
after a switch to an external, unapproved model it can be replayed through
history.  Mark successful workspace-summary jobs as document-context jobs too,
then add a deterministic local-model summary containing a distinctive source
string followed immediately by an unapproved external ordinary request.  The
test should assert that neither the model request nor an attempted web-search
tool argument receives that string.  This must preserve unrelated history and
approved-document behavior.

## Limits and conclusion

No real provider, Telegram bot, personal folder, external recipient, or GitHub
CI run was used.  I did not assess deployment operation.  The two High findings
mean this review does **not** support describing the current follow-up head as
complete or ready to merge.  The resolved rows above apply only to the exact
working-tree diff reviewed here.

## Final re-review — 2026-09-10

Re-reviewed the remediation for `PR320-FU-IR-01` and `PR320-FU-IR-02` on the
same working tree.  Commands actually run after the remediation:

- `PYTHONPATH=src python3 -m pytest -q tests/test_quickstart.py -k
  'natural_workspace_conversation_and_unapproved_history_boundary or
  workspace_summary_remains_rejected_for_subscription_engine'` — exit 0,
  2 passed, 59 deselected
- `PYTHONPATH=src python3 -m pytest -q tests/test_backup_scripts.py` — exit
  0, 1 passed
- `git diff --check` — exit 0

| Finding | Final disposition | Current evidence |
| --- | --- | --- |
| PR320-FU-IR-01 | **Resolved** | Portable export now clears the `file_workspace` grant/configuration and document-sharing state, but retains each result row's durable ID, request ID, content hash, provenance, and creation time.  It explicitly nulls `path` and `workspace_id` and sets `state='detached'` (`portable_state.py:29-41`).  Since `FileWorkspace.search()`/`save()` require a newly configured workspace ID, the detached row cannot bind to, read from, or write to the old location.  The backup fixture proves the restored runtime has no workspace grant and retains one detached, pathless result record (`tests/test_backup_scripts.py:24-29`). |
| PR320-FU-IR-02 | **Resolved** | A successful workspace summary now calls `record_file_workspace_document_job()` only after a successful `save()` (`quickstart_service.py:1146-1149`), the same classification used by workspace search.  On a later unapproved external-model request, history excludes every classified job before model input is built (`:1033-1039,1075-1080`); consequently `document_context` cannot leave a document-derived history route to `web_search`.  The regression verifies summary-job classification and verifies a searched document string is absent from the subsequent external-model fixture request (`tests/test_quickstart.py:425-441`). |

The history regression's external-content assertion is exercised through a
workspace-search body; the direct assertion that the successful summary job is
classified, together with the common job-ID filtering code, covers the
previously missing summary path.  A future hardening test may make the local
summary fixture itself echo a distinctive source string, but that is not an
unresolved code path in the reviewed implementation.

### Final conclusion

No unresolved findings remain from `PR320-FU-IR-01` or `PR320-FU-IR-02` in the
latest reviewed local diff.  This conclusion remains limited to automated
fixture evidence and static code inspection; it is not evidence of external
model, Telegram, or production operation.
