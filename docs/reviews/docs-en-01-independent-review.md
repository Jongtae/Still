# Independent review: DOCS-EN-01 (#319)

## Scope

Reviewed PR #326 at `11f9f21956807bb6b6a84fec4a046e6163670d6b` against
`main` commit `c188470197367d7c4a3c7f5cf3fad8b37525e4b5`. The review covers
the English-canonical internal-document policy, its verifier and regression
tests. It does not claim a runtime, external provider, scheduler, or user
documentation deployment was operated.

## Findings and resolution

The initial review found two in-scope gaps; both are resolved in the follow-up
commit recorded below.

* **F1 (P2, resolved):** the reference registry initially omitted five eligible
  internal pairs. It now includes D-MP2-01/02/03 and MP1 D-02/D-03, while an
  explicit exclusion list names the Korean-only historical documents. The
  verifier rejects an incomplete registry and a registered reference without
  its English canonical link.
* **F2 (P3, resolved):** the no-heading-parity regression now uses one English
  heading and two Korean headings, so restoring the removed heading-count gate
  would fail the regression.

* `AGENTS.md`, the goal-execution contract, the file-workspace contract, and
  development governance now name English as the canonical internal source;
  none retains a mandatory Korean--English parity requirement.
* `verify_master_plan_docs.py` replaces heading-count parity with checks for
  each declared English canonical document, its retained Korean historical
  reference, the reference's canonical local link, and local links in both
  files. It retains English MP1 phase IDs, delivery-plan design-to-
  implementation mapping, automated-evidence, completion-evidence, and
  file-workspace work-ID checks.
* The added regression permits a Korean historical reference to have a
  different heading structure, but fails when its English canonical link is
  absent. The inspected Korean internal references declare their historical
  status and point to their English counterpart.
* The PR diff contains no README, product-site, runtime, authority, or
  external-integration change.

## Validation observed

* `python3 scripts/verify_master_plan_docs.py` — passed.
* `python3 -m pytest -q tests/test_master_plan_traceability.py tests/test_governance_contract.py tests/test_delivery.py` — 29 passed.
* `git diff --check c188470..11f9f21` — passed.
* PR #326 required `validate` check was successful for reviewed head
  `11f9f21956807bb6b6a84fec4a046e6163670d6b` when inspected.
* Follow-up inspection on the current branch: `python3 scripts/verify_master_plan_docs.py`,
  `python3 -m pytest -q tests/test_master_plan_traceability.py tests/test_governance_contract.py tests/test_delivery.py`
  (30 passed), and `git diff --check` passed.

## Review limitation

This is a source and automated-test review. Korean historical material is
retained rather than retranslated; the review confirms policy/link treatment,
not semantic translation equivalence, which is intentionally no longer a
merge criterion.
