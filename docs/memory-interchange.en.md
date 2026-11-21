# Memory interchange (Portable Memory adapter)

Status: implemented by #38 (`src/personal_agent/memory_interchange.py`, `scripts/agentos-memory-interchange.py`).
Evidence class: model-free unit tests with the pinned SDK (`tests/test_memory_interchange_38.py`). No exchange with another
product has been observed, and none is claimed.

## What it is

An optional adapter between Still's Memory and the [Portable Memory](https://github.com/MacPaw/portable-memory) `.mem` bundle
(format 1.1.0, reference SDK `portable-memory` 0.3.0 at commit `f341f07`, MIT). The SDK writes and reads the bundle itself: its
directory layout, canonical JSONL, `CHECKSUMS`, manifest, and the import that applies tombstones before anything else. Still
implements only the SDK's store seam (`PortableMemoryStore`) to map its own rows.

Still's `memories` table stays canonical. A bundle is an exchange copy, not a second store. It is not a backup either; the
owner-state backup stays `scripts/agentos-backup.py`.

## Re-validation against the current SDK (2026-11-21)

The predecessor spike (Jongtae/agentos#894, 2026-09-30) was made at SDK 0.3.0, commit `f341f07`. On 2026-11-21 the
default branch, the latest release and PyPI were still at that commit and version, so its findings still hold:

- Every schema allows extra keys, but the Python codec writes and reads only the dataclass fields. An extra key on a `fact`
  is dropped. The `ext` carrier exists for episodes only.
- Candidate and approval state has no native kind. A passthrough stream would be opaque to every other reader.
- The validator is an L0 check: manifest, checksums, no unlisted files, and each known kind decodes. It does not apply
  the JSON Schemas.
- A tombstone needs a stable, identity-bearing target id. Still's memory ids and its persisted forget/delete receipts
  (`owner_state_receipts`) provide one.

The adapter therefore uses native `fact` and `tombstone` records only. It writes no passthrough or `ext` streams for
Still-only fields: those fields are left out and listed below.

## Portable scope

| Still | Portable Memory | Direction | Notes |
|---|---|---|---|
| `memories` row (`current`) | `fact` with `tValidTo` null | export, import (as a candidate) | `subject` = `owner`, `predicate` = memory key, `object` = value, `text` = `key: value` |
| `memories` row (`superseded`, `corrected`) | `fact` with `tValidTo` and `supersededBy` | export only | Still links new→old (`supersedes`); the bundle links old→new. An imported historical fact is counted, not stored |
| `valid_until` (the fact's last day, an ISO date) | `tValidTo` = the next midnight in the owner's time zone | export | Kept on a current row too, even after that day (Still keeps such a row `current` and leaves it out of reads by date) |
| A fact with a future `tValidTo`, or a `tValidFrom` still to come | — | import: counted as `time_bounded_skipped` | A candidate cannot carry a first or last day; accepting it would make a time-bounded fact current now and lasting |
| A fact whose `subject` is not `owner` or `user` | — | import: counted as `other_subject` | Dropping the subject would make a fact about someone else read as the owner's own |
| `created` (float seconds) | `tValidFrom`, `createdAt` | both | Whole-second UTC: sub-second precision is lost |
| Forget receipt (`forget`, `migrate`), state `forgotten` | `tombstone` `op: delete` | export | Receipts carry no content; the fact itself is not exported |
| Delete receipt, state `deleted` | `tombstone` `op: redact` | export | |
| Undone receipt | none | — | The row is current again and exported as a fact |
| `retracted` row (a save that was undone) | `tombstone` `op: delete`, reason `retracted` | export | Covers an earlier export that carried it |
| Bundle tombstone | withdraws a still-pending imported candidate, and is kept (`portable_import_tombstones`, fact ids only) | import | A later bundle that still carries the fact cannot revive it. Current Memory is never changed by an import; the owner forgets here if they want to |
| — | `confidence`, `importance` | export | Required by the spec; Still tracks neither, so both are a constant 0.5 that carries no meaning |
| — | `episodeID` | export | Required; Still keeps no portable episode, so it is empty |
| Memory candidates, approvals | — | never | Approval tokens are runtime-secret-bound; candidates are not knowledge yet |
| Profile snapshot, search terms | (`core`) | never | Derived from facts; a receiver rebuilds its own |
| `source_works`, `evidence`, `basis`, `origin` | — | never | Work-local references and quotes; the extra keys would be dropped by the codec anyway |
| Secrets, credentials, sessions, Grants, folder grants | — | never | Values are redacted with `redact_known_secrets`; an imported value holding a stored secret is refused |
| `episode`, `core`, `entity`, `edge`, `procedure`, `preference`, passthrough streams | — | import: counted under `not_imported` | Never stored |

## Import rules

- Before any write, the SDK verifies the manifest and every checksum; a corrupt bundle is refused whole.
- Only facts that are current in the bundle, have no last day and are about the owner become pending `MemoryCandidate` rows, with `source.kind = portable_import` (C5).
  The owner reviews them in Personal Space (`/api/personal-space/memory-candidates`) through the existing approval path.
  Nothing imported becomes an instruction, an approval or a Grant.
- A value is skipped when it is already current here or already proposed, including one the owner declined earlier.
  Importing the same bundle twice proposes nothing new.
- A value the owner forgot here (`forgotten_values`) is never proposed again by an import.

## Invariants (tested)

- Exporting the same state twice gives byte-identical `items/*.jsonl`, `audit/tombstones.jsonl` and `CHECKSUMS`. The
  manifests differ only in `createdAt`.
- Export → import → owner accepts → re-export gives the same set of current (key, value) facts. Ids, sub-second
  times and history are not carried over by design.
- Conformance level written: L0. There is no audit log, which L1 needs.

## Upstream format feedback (not yet sent)

These are drafts. Sending them to the upstream repository is an external post and needs the owner's approval first.

1. Carry `ext` (or keep unknown keys) for every kind, not only `episode`, so a host can keep its own fields on a
   `fact` across a round trip.
2. Allow `confidence` and `importance` to be absent, or define a value that means "not tracked". A host without them
   must otherwise write a number that looks meaningful.
3. Allow `episodeID` to be absent for a fact the owner stated directly, rather than requiring a placeholder episode.
4. Define a native proposed or unreviewed state for a record, so a receiver can tell a reviewed fact from a suggestion.
