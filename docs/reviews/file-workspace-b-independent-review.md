# FILE-WS-B-01 independent security and recovery review

Reviewed 2026-09-10 against branch `codex/315-file-workspace-first-experience`.
The last committed implementation baseline was `5771e88` (`feat: save
conversation summaries to file workspace`).  This review covers the
uncommitted diff from that commit at review time:

- `delivery-plan.yaml`
- `src/personal_agent/delivery-plan.yaml`
- `src/personal_agent/file_workspace.py`
- `src/personal_agent/quickstart.py`
- `src/personal_agent/quickstart_service.py`
- `tests/test_delivery.py`
- `tests/test_quickstart.py`

## Evidence inspected

- `AGENTS.md`
- `docs/goal-execution-contract.en.md`
- `docs/file-workspace-first-experience-contract.en.md`
- `git diff 5771e88`, `git diff --check`, and the numbered source/test ranges
  in the files listed above
- `PYTHONPATH=src python3 -m pytest -q tests/test_quickstart.py -k
  'conversation_summary_saves_real_workspace_markdown or
  file_workspace_rejects_escape_and_cleans_up_failed_save or
  file_workspace_refresh_omits_modified_renamed_and_deleted_sources or
  workspace_summary_blocks_unapproved_external_model_before_send'`
  (exit 0: 4 passed, 51 deselected)

The passing focused tests demonstrate real temporary-file creation, source
hash provenance, source preservation, a new Python-process search, stale
omission, basic path/symlink rejection, and denial before an unapproved
external model call.  They do not resolve the findings below.

## Findings

### FWS-B-IR-01 — High: workspace configuration permits prohibited broad grants

`FileWorkspace.configure()` accepts every existing directory as both a
reference and managed workspace (`src/personal_agent/file_workspace.py:11-20`).
Unlike the existing `AgentService.save_roots()` boundary
(`src/personal_agent/quickstart_service.py:398-409`), it does not reject `/`,
the owner's home directory, or the runtime's private state directory.  The
authenticated `/api/file-workspace` route exposes that configuration
(`src/personal_agent/quickstart.py:240-250`).  This violates the contract and
`AGENTS.md` rule that a folder connection never implies home-directory or
internal-state access.  Reject those scopes for both references and managed
workspace, including resolved paths beneath `store.private`, and add API-path
tests.

### FWS-B-IR-02 — High: an existing document-sharing approval can authorize newly configured workspace references

The external-send guard checks `document_boundary()` before `run_agent()`
(`src/personal_agent/quickstart_service.py:1045,1100-1113`), which is correct
for an unapproved model.  However its approval fingerprint contains only model
details and `file_roots` IDs (`:360-375`), while
`configure_file_workspace()` merely writes the new grants and does not clear
or bind `document_sharing` (`:411-413`).  An owner could approve an external
model for an earlier/no `file_roots` selection, then configure a different
file-workspace reference; the summary injects that new source into model
history (`:1007-1016`) and sends it without a fresh approval.  Clear the
approval whenever file-workspace grants change, or include a canonical
workspace-grant fingerprint in the approval check; prove that reconfiguration
causes zero external calls until approval is renewed.

### FWS-B-IR-03 — High: result save has a time-of-check/time-of-use overwrite race

`save()` chooses an unused filename with `target.exists()` and then uses
`os.replace(temporary, target)` (`src/personal_agent/file_workspace.py:84-91`).
Another process can create `target` after the existence check; `os.replace`
will overwrite that pre-existing owner file.  This contradicts the required
new-result-only/no-silent-overwrite boundary.  Use a no-replace publication
strategy or reservation that is atomic against a competing creator, and add a
race/collision test that proves an existing file is never overwritten.

### FWS-B-IR-04 — Medium: interruption recovery cannot reconcile a published result with durable state

The file is published before the result row is inserted (`file_workspace.py:87-92`).
A process crash after `os.replace` and before SQLite commit leaves a real
result with no provenance row; a crash/OS error around publication has no
reconciliation or durable pending record.  The exception handler also deletes
the target blindly (`:93-94`), and `run_one()` only converts
`ValueError`, `ProviderError`, and `ExecutionError` into a failed job
(`quickstart_service.py:1121-1129`), not general storage errors.  The focused
test only simulates a caught `os.replace` error, not this interruption window.
Add a durable pending/finalization recovery protocol (or explicit orphan
reconciliation) and a subprocess/fault-injection test proving no false
success and deterministic recovery.

### FWS-B-IR-05 — Medium: source identity is not stable across grant reconfiguration

Provenance stores a random reference-folder ID, relative path, and content hash
(`file_workspace.py:17,40-42,90`), but folder IDs are regenerated on every
`configure()` call.  The contract requires stable opaque source/result
references and says paths alone are not immutable identity.  Result IDs are
opaque, but source identity is only stable for one configuration lifetime;
reconfiguration makes otherwise unchanged source provenance stale.  Preserve
an opaque folder/source identity across equivalent grants, or record a stable
source identifier plus version semantics and test reconfiguration/rename
recovery explicitly.

### FWS-B-IR-06 — Medium: restart search test does not exercise the new-conversation API entry point

The save half uses `/api/file-workspace` and `/api/chat`, but the restarted
process directly calls `QuickStore.enqueue()` and `AgentService.run_one()`
(`tests/test_quickstart.py:315-330`).  It verifies persistence and service
search behavior, but not the existing new-conversation request route after
restart.  Add a fresh server/session and `/api/chat` request after restart;
then assert the returned job response contains the result and its provenance.

## Positive observations

- `FileWorkspace.read()` rejects absolute paths, traversal components, hidden
  components, symlinked reference paths, and non-TXT/MD input
  (`file_workspace.py:25-42`).
- `find_reference()` uses configured grants and text queries, rather than an
  untrusted model-provided absolute path (`:44-62`).
- Source content is framed as untrusted instructions before model invocation
  (`quickstart_service.py:1014-1016`).
- The current unapproved-external-model test verifies no adapter call and no
  result file (`tests/test_quickstart.py:362-369`).
- Stale modified, renamed, and deleted sources are omitted rather than
  resurfaced as current (`file_workspace.py:96-115`; test `:350-360`).

## Review conclusion and limits

This is a read-only review; no implementation or test files were modified.
The three High findings block a completion/merge claim for the file-workspace
security boundary.  I did not inspect GitHub issue/PR/CI state, use personal
files, operate Telegram, or call a real external model.  The focused local
test command above is not a substitute for the complete required CI suite or
operating-mode evidence.

## Follow-up review — 2026-09-10

Re-reviewed the current uncommitted diff after the reported remediation. The
following commands were run against that exact worktree:

- `PYTHONPATH=src python3 -m pytest -q tests/test_quickstart.py` — exit 0,
  56 passed
- `PYTHONPATH=src python3 -m pytest -q tests/test_delivery.py` — exit 0,
  14 passed
- `git diff --check` — exit 0

### Follow-up disposition

| Finding | Disposition | Follow-up evidence / residual concern |
| --- | --- | --- |
| FWS-B-IR-01 | **Not fully resolved — High remains** | `configure()` now rejects `/`, `Path.home()`, and paths at/below `store.private` (`file_workspace.py:11-27`), and the HTTP test proves `/` rejection. But it still permits `store.root` and any ancestor containing `store.root`; a reference grant to such an ancestor can recursively reach the non-hidden `private` directory and its TXT/MD contents. A managed workspace can also be `store.root`, mixing ordinary results with app operating state. Reject the runtime root, all descendants, and ancestors that contain it for both grants; add tests for `store.root`, `store.private`, and a parent-of-root reference. |
| FWS-B-IR-02 | **Resolved** | The document fingerprint now includes file-workspace reference IDs/configuration and `configure_file_workspace()` clears `document_sharing` (`quickstart_service.py:359-366,413-417`). The external-model test approves, reconfigures, proves approval is required again, and observes no model call (`tests/test_quickstart.py:378-389`). |
| FWS-B-IR-03 | **Resolved** | Publication changed from replacement to `os.link(temporary, target)`, which atomically fails if a competing target exists (`file_workspace.py:113-123`). The collision test preserves `Summary.md` and publishes `Summary-2.md` (`tests/test_quickstart.py:345-363`). This removes the previously identified overwrite race. |
| FWS-B-IR-04 | **Partially resolved — Medium remains** | A durable `pending` record is made before publication and `recover()` verifies a published file hash before making it current (`file_workspace.py:82-128`). This closes the original process-crash window in design. There is still no fault-injection/subprocess test for crash after publication/before state finalization, and `AgentService.run_one()` still does not catch storage `OSError` (`quickstart_service.py:1119-1132`), so a failed save can leave the job running until general store recovery rather than recording a prompt failed result. |
| FWS-B-IR-05 | **Resolved** | Folder and source records now use opaque device/inode-derived IDs and the test proves reference ID stability on equivalent reconfiguration (`file_workspace.py:17,44-48`; `tests/test_quickstart.py:365-376`). Rename/delete remains safely stale rather than silently remapped. |
| FWS-B-IR-06 | **Resolved** | The restart subprocess now creates an authenticated fresh HTTP server/session and uses `/api/chat` for `/workspace-search` before `run_one()` (`tests/test_quickstart.py:316-343`). |

The unresolved High grant-containment finding and the Medium failure-state test
gap mean this follow-up does not support a final completion/merge claim yet.
This review remains read-only except for this reviewer-owned artifact.

## Final follow-up — 2026-09-10

Reviewed the final remediation for the two previously open findings. Command
actually run:

- `PYTHONPATH=src python3 -m pytest -q tests/test_quickstart.py -k
  'file_workspace_rejects_escape_and_cleans_up_failed_save or
  file_workspace_recovers_published_pending_result_after_interruption or
  workspace_save_failure_never_reports_success'` — exit 0, 3 passed,
  54 deselected
- `git diff --check` — exit 0

| Finding | Final disposition | Evidence |
| --- | --- | --- |
| FWS-B-IR-01 | **Resolved** | `FileWorkspace._broad_or_private()` now rejects `/`, home, `store.root`, every path below `store.private`, and every ancestor of `store.private` (`file_workspace.py:26-29`). The grant test rejects the runtime root for both a reference and workspace (`tests/test_quickstart.py:345-354`). This closes the earlier route to runtime-private TXT/MD material and result/internal-state mixing. |
| FWS-B-IR-04 | **Resolved** | The fault-injection test interrupts after publication but before `_mark_current()`, then `search()` invokes `recover()` and verifies the pending row/file hash before returning one current result (`tests/test_quickstart.py:380-390`; `file_workspace.py:84-95,104-136`). `run_one()` now turns `OSError` into a failed job, and the end-to-end save-failure test proves no success response or result file (`quickstart_service.py:1127-1132`; `tests/test_quickstart.py:402-411`). |

No unresolved findings from FWS-B-IR-01 through FWS-B-IR-06 remain in the
current reviewed diff. This conclusion covers the local automated evidence
named above and prior follow-up commands only; it does not claim external
provider, Telegram, or hosted operating-mode observation.
