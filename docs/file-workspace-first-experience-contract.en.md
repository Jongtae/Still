# FILE-WS-A-01 — File-and-folder personal workspace decision

## Status and outcome

This is the design contract for FILE-WS-A-01 issue #318, within the
owner-authorized #314 program.
It changes the product criterion and plans; it does not implement file ingestion.

Still is a local-first personal agent whose material foundation is owner files
and folders and whose experience is conversation and work. A future first flow
is: an owner entrusts material, Still preserves the original, creates a
meeting note or summary as a normal TXT/MD file, and a later conversation finds
and reuses that result after restart.

## Product decision

Files and folders are the primary material system. Still is not a file picker
that repeatedly asks the owner to locate an already connected item, nor a cloud
sync service. Existing owner-selected sync applications may synchronize files;
Still does not add a sync engine or central authentication service.

Google Drive and other service connectors are optional capabilities: they may
import selected material or perform a service-specific action under their own
contracts. They are not a prerequisite or the core storage path. #308, #310,
#311, and #312 remain preserved optional Drive work. #313 remains an
independent informational/policy-site workstream and is not a prerequisite for
the local file experience.

## Storage and provenance boundary

| Class | Location and rule |
| --- | --- |
| Original | Preserve the supplied source file; never replace it with Markdown, a summary, or a transcription. |
| Derived material | Store extracted text, transcription, and summary separately and record the source relationship. |
| Work product | Keep draft and final records distinct; a final meeting note or summary is an ordinary owner-readable TXT/MD file in the managed workspace. |
| Rebuildable index | Keep search/index artifacts separate from owner files and make them regenerable. |
| Durable internal state | Keep work queue, approvals, evidence, recovery state, and credentials separate from the index; they are not disposable cache. |

The future implementation records stable opaque source/result references and
provenance without treating a pathname alone as immutable identity. It preserves
compatibility with existing owner data and performs no destructive migration.

## Access and action boundary

- A connected reference folder is read-only by default. Its approved scope may
  be searched and read without a fresh file-selection prompt.
- The Still-managed workspace may create new material and results only inside
  the owner-granted workspace scope.
- A folder grant does not grant home-directory access, arbitrary shell access,
  source overwrite, deletion, bulk move, or external transmission.
- Source overwrite/deletion, bulk relocation, and every external AI, messenger,
  agent, or recipient transmission remain separate explicit policy/approval
  boundaries. Local storage is not a claim that data is never transmitted.

## First implementation contract

FILE-WS-B-01 (#315) may activate only after this English canonical contract
merges and its goal-ready issue remains current. It must integrate the existing conversation
path, not only a demo script. TXT/MD are the initial validated formats; an
already-supported format may be reused only within its tested boundary.

The minimum refresh policy must notice an owner edit, rename, or deletion and
make the stale search/provenance state safe: update/reindex when available,
otherwise omit the stale result and provide deterministic recovery. It must not
silently read outside the grant or recreate a deleted original.

## Required automated evidence

FILE-WS-B-01 must use dedicated test folders and sample material—not personal
data—and prove the integrated flow: save → restart → search/reuse. It must also
cover grant-boundary/path-traversal rejection, original preservation,
duplicate-save behavior, and denial of unapproved external send. Mock model
tests, local-file tests, and live external engine/Telegram observations are
separate evidence classes.

## Non-goals and successors

This contract does not implement OCR, audio transcription, video analysis,
advanced large-library search, cloud-only document import, delegated material
bundles/results, a synchronization engine, new credentials, or live external
operation. Those are separately tracked follow-ups, not completion criteria for
the first flow.

FILE-WS-C-01 (#316) may run after FILE-WS-B-01 to produce its independent review, requirement-to-
evidence audit, and implementation PR closeout evidence.
