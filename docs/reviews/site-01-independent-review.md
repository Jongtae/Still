# Independent review: SITE-01 (#313)

## Scope

Reviewed PR #327 at `5c112eff3f9f8c5b00f550e8341a671e723b4e93` against the
SITE-01 issue, delivery plan, static pages, manual Pages workflow, and focused
tests. This is source and local-validation evidence only: no Pages deployment,
domain, Google configuration, external OAuth operation, or runtime connection
was operated or inferred.

## Findings

### P1 — privacy draft omits required deletion and permission-withdrawal guidance

The issue requires the privacy page to describe local storage/protection/**deletion**
and how a user can **withdraw permissions**. `site/privacy.html` describes local
state, rebuildable indexes, and separate external-send boundaries, but provides
neither deletion guidance nor a withdrawal path for configured provider access.
The Korean section has the same omission. This must be supplied with
implementation-accurate, operator-approved wording before publication or merge
as the privacy-policy preparation is incomplete. Do not invent an operator,
support contact, effective date, or unverified runtime behavior to resolve it.

### Boundaries correctly preserved

* The four pages are static HTML/CSS only: no forms, script elements, dynamic
  request APIs, analytics integration, credential literals, callback endpoint,
  token broker, or Drive proxy is present. Text that explains these prohibited
  paths is informational and does not implement them.
* `deploy-pages.yml` has only `workflow_dispatch`; it packages only `site/`.
  The Pages token permissions are confined to that manually dispatched deploy
  job. No deployment has been claimed or triggered by this review.
* Each page contains English and Korean content, viewport metadata, stylesheet
  reference, and working local navigation. Product language distinguishes local
  storage from separately approved external AI, messaging, and provider sends.
* The policy and terms drafts explicitly leave legal operator, contact,
  jurisdiction, effective date, hosting configuration, and domain verification
  as operator inputs rather than inventing them. The delivery plan activates
  only SITE-01 and explicitly prohibits publishing, domain configuration, and
  Google URL registration without a separate authorized action.

## Validation observed

* `python3 -m pytest -q tests/test_public_site.py` — 3 passed.
* `python3 scripts/verify_master_plan_docs.py` — passed.
* `cmp -s delivery-plan.yaml src/personal_agent/delivery-plan.yaml` — passed.
* `git diff --check` — passed.
* A local HTML-parser check verified all four pages' relative navigation
  targets remain inside `site/` and exist.

## Disposition

**Changes requested.** Resolve the P1 policy omission, then rerun the focused
site checks and obtain CI for the resulting commit. Public HTTPS deployment,
operator/legal confirmation, owned-domain verification, Google registration,
and live local OAuth observation remain separate unperformed actions.

## Re-review: `76979fb7d16537380ac9f57f7186ec7d334a5032`

The new English and Korean privacy text and its four-test focused regression
do explicitly distinguish local deletion from provider permission revocation.
However, **P1 remains unresolved**: the text directs users to the local
runtime's “documented removal or reset controls,” but the current repository
does not document or implement such controls. Source and documentation search
found local capability-token revocation and the optional Drive disconnect
contract, not a documented local-state/managed-workspace removal interface.
The regression proves the wording is present; it does not establish that the
claimed controls exist.

Validation re-run: `python3 -m pytest -q tests/test_public_site.py` — 4
passed; `git diff --check` — passed. A resolution needs either an
implementation-accurate deletion route already supported by the product, or
wording that does not claim an unavailable documented control. This review
still makes no deployment or external-runtime claim.

## Final P1 re-review: `f7d964c6695401864ec2509b895040c10a722ece`

**P1 resolved.** The privacy page now accurately directs an owner to use the
operating system's file-management tools to remove the relevant local
application data and managed-workspace material for that installation. It does
not claim a nonexistent Still removal/reset control, explicitly preserves
connected reference-folder originals, and states that provider revocation is a
separate action in the provider's account permissions/security controls. The
Korean wording carries the same boundary. This is user guidance, not a claim
that a runtime deletion feature, Pages deployment, or provider operation was
observed.

Validation: `python3 -m pytest -q tests/test_public_site.py` — 4 passed;
`git diff --check` — passed.
