# Independent review — FILE-WS-A-01 / #314

## Requested and observed context

Requested review: independently inspect the current #314 product-alignment diff
for product, data, and authority boundaries; Drive/Picker preservation; proof
that implementation substep B has not started; plan/document parity; and
truthful claims. This reviewer was assigned exclusive ownership only of this
artifact and made no changes to the reviewed files.

Observed worktree: `codex/314-file-workspace-product-alignment` in
`<workspace>/personal-agentos-314-file-workspace-product-alignment`.
The reviewed diff contains product, plan, contract, tracker, README, ledger,
and bilingual-verifier changes, plus the two new bilingual FILE-WS contract
documents. It contains no runtime, application, or test-code implementation
for file ingestion/search/storage.

## Scope and boundary assessment

The new bilingual contract and aligned product documents correctly state the
required boundary: connected reference folders are read-only by default;
managed-workspace writes are limited to an owner-granted scope; originals,
derived material, drafts, and final records are distinct; rebuildable indexes
are separate from durable queue/approval/evidence/recovery/authentication
state; and local storage does not authorize external transmission. They also
keep source overwrite/deletion, bulk relocation, arbitrary shell access,
broad-home access, and external AI/messenger/agent/recipient sends outside a
folder grant.

The plan and contracts correctly reserve FILE-WS-B-01 and FILE-WS-C-01 behind
FILE-WS-A-01. The current diff has no implementation-path changes, so B has not
started in this review scope. #308, #310, and open PR #311 are described as
preserved optional Drive/Picker work rather than closed, discarded, or a core
path predecessor; #312 remains optional connector architecture and #313
remains an independent informational/policy-site track. GitHub inspection
confirmed #308, #310, #312, #313, #314, #315, and #316 are open and PR #311 is
open on `codex/310-drive-capability-picker-summary`.

## Finding

1. **Resolve before opening the alignment PR: linked public README variants are
   materially stale.** `README.zh-CN.md` and `README.ja.md`, both linked from
   the English and Korean README language selector, still call Hub v2 the
   current product roadmap and describe the former Telegram-centred baseline.
   This conflicts with the reviewed English/Korean README assertion that #314
   is the active program and that Hub v2/Drive is historical evidence rather
   than the active selector. Align these translations to the new direction, or
   clearly mark them as historical/unmaintained before presenting this PR as
   repository-wide README alignment. This is a documentation parity and
   truthful-current-state issue; it does not require starting B.

No other blocking finding was identified in the reviewed current diff.

## Checks inspected

- Read the current `AGENTS.md`, Goal Execution Contract, and Development
  Governance contract.
- Reviewed the staged/uncommitted diff for `AGENTS.md`, `PRD.md`, product
  vision, bilingual long-term vision, root/package delivery plans, trackers,
  ledger, README files, historical `PLAN.md`, and the new bilingual contract.
- Compared root and package delivery-plan copies; `cmp -s` passed.
- Ran `python3 scripts/verify_master_plan_docs.py`; it reported `Master Plan
  bilingual documents verified`.
- Ran `git diff --check`; it passed.
- Queried current GitHub states for #308, #310, #312–#316 and PR #311.

## Limitations

This is a diff-and-contract review, not evidence that the future local-file
flow works. No personal folder was granted, no external credential/provider or
Telegram operation was configured, and no local-file, mock-model, or operating
validation from B/C was run or claimed. The review does not replace required
PR CI or the future B/C independent review and requirement-to-evidence audit.

## Resolution addendum — README.zh-CN.md and README.ja.md

Rechecked only the follow-up edits to the Chinese and Japanese README files
against the reviewed English and Korean README direction. The finding above is
resolved: both now identify files/folders as the material foundation and
conversation/work as the experience; state the read-only reference-folder and
owner-granted managed-workspace boundary; preserve the original/derived/draft/
final and index/durable-state distinctions; retain external transmission as a
separate approval boundary; describe Hub v2/Drive delivery history as
historical rather than the current selector; identify #314 as active; and keep
Drive optional rather than storage infrastructure. Both also truthfully say
that the first implementation has not started. `git diff --check --
README.zh-CN.md README.ja.md` passed. No remaining finding for these two edits.
