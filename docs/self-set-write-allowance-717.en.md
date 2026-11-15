# Self-set external writes (#717, owner decision 2026-11-14)

The owner's AI judges reversibility from minimized metadata. Still enforces
owner authority, exact one-use approvals, payment exclusion and dispatch
receipts. The scope is SELF_SET preparation runs only; typed requests and
OWNER_ACCEPTED runs retain their existing behavior. Browser writes have no
standing allowance. No undo is advertised without an actual undo operation.

## Existing solutions review and bounded plan

Build: the new owner-authority rule, using existing Still boundaries. The
structural gap is that pilot non-money mutation authority previously applied
regardless of who selected the scheduled run. This is one shared gate, not a
list of operation or site exceptions.

External sources checked:

- Python's maintained standard-library sqlite3 transaction interface
  (https://docs.python.org/3/library/sqlite3.html), adopted through the existing
  QuickStore transactions; no new database or dependency. Python/SQLite's
  existing platform support and licence remain unchanged.
- MCP Tools specification 2025-06-18
  (https://modelcontextprotocol.io/specification/2025-06-18/server/tools):
  tool annotations are untrusted without a trusted source. They cannot supply
  owner consent or establish reversibility. Its reference SDK transport remains
  unchanged. MCP's permission UX is implementation-owned.
- The installed A2A and MCP SDK transports remain responsible for their protocol
  operations; neither implements Still's preparation provenance, paired owner
  conversation, Settings revisions or exact Work continuation contract. Adopting
  a second agent orchestration framework cannot supply those owner-state facts.

Internal search: `payment_autonomy`, `_request_browser_step`,
`_consume_browser_step`, `wait_refusal`, `connector_permission`, `_effect_guard`
identified the existing Settings draft/confirmation flow, HMAC action bindings,
SQLite dispatch claims and #821 preparation-version continuation checks. These
are the thin sovereign-policy glue reused here; no duplicate approval store,
transport, protocol parser, scheduler or model engine is introduced. Existing
API, code-run and connector replay guards remain in place.

Security boundary: enabling is an owner-confirmed Settings draft or local
Settings action bound to the displayed revision, never a direct-request setting.
Revocation is immediate. Authorization records carry connection identity,
revision, owner and time. Payment remains under its existing mechanism.
Minimized judgment inputs exclude values. Dispatch records bind exact inputs
with the existing keyed digest; owner notices use redacted targets. An unknown
outcome cannot authorize another dispatch. A scheduled wait retains the exact
preparation version and paired conversation generation.

The owner's approval preview includes the request content or browser control
description. Those values never enter the reversibility judgment. A native
form that cannot be identified cannot receive an exact approval and is refused.
A run without a current paired conversation also refuses a write requiring
approval; it cannot manufacture an owner confirmation.

Judgment may yield while inputs change. API authorization therefore precedes
the existing final credential/revision check and dispatch claim. A self-set
browser press rechecks the bound form/page state after judgment and refuses a
changed target or newly payment-bearing control. Both regression tests failed
before these checks were placed correctly, with dispatch observed in the fake
transport/driver. Native held-form release already checks exact current state.

No dependencies, new network destinations, credential scopes or package trust
are added; AGPL-3.0-only and the existing dependency licences are unchanged.
Verification uses fake transports and temporary stores. This change generalizes
an authority boundary and does not script a domain task. It requires the
escalated final-head review before merge; no live external effect is claimed.

## Changed files

- `src/personal_agent/self_set_writes.py`: connection allowances and their
  authorization history, the shared self-set gate, replay reservation and
  transport-observed notices.
- `quickstart_service.py`, `browser_signin.py`, `quickstart_store.py`,
  `preparations.py`: service wiring and the existing exact-step/#821
  continuation path, restricted to the current self-set preparation version.
- `conversation_handoff.py`, `trust_record.py`: reversibility judgments and
  explicit owner classifications; ordinary callers retain mutation semantics.
- `api_requests.py`, `code_run.py`, `a2a_peers.py`: authorization before
  dispatch, connection identities and observed/unknown outcome receipts.
- `browser_session.py`, `browser_worker.py`: press and native-submit gates,
  exact held forms, continued release and unchanged payment boundaries.
- `settings_orchestrator.py`, `quickstart.py`, `web/app.js`: confirmed
  conversation drafts and local Settings rows, revision binding and revocation.
- `messages.json`: owner-facing labels, explanations and notices in Korean
  and English, with existing Japanese/Chinese catalogue parity preserved.
- `tests/test_self_set_writes_717.py`: model-free authority, continuation,
  dispatch, stale/replayed approval, browser-worker and allowance regressions.
- `tests/test_connect_effects.py`: both new judgment labels retain ordinary
  mutation behavior.
- `tests/test_owner_settings_conversation.py`: include the new allowance
  category in the existing redacted Settings projection check.
- `evals/agentos_eval/judgments.py`, `tests/test_eval_judgments.py`: preserve
  the existing ordinary-Work mutation rubric for both reversibility labels.

Unqualified implementation filenames above are under `src/personal_agent/`.

## Local verification

Python: `/Users/jongtaelee/Documents/project/Still/.venv/bin/python`.
Commands use `PYTHONPATH=src:/tmp`, `-m pytest -q -n 4 -p no:cacheprovider
-p skip_socket717`. The temporary `skip_socket717` fixture skips tests when
they attempt `socket.bind`, including Unix relays, as required by common.md;
it changes no repository test or runtime behavior. WebKit integration tests
requiring a real browser are excluded in the browser run.

- `tests/test_self_set_writes_717.py tests/test_scheduled_runs_ask_821.py
  tests/test_payment_autonomy_662.py tests/test_action_bound_approvals_619.py
  tests/test_preparations.py`: 280 passed, 845 subtests passed. This preceded
  the final browser audit receipt and owner-preview refinements; subsequent
  affected-path runs exercise those refinements.
- `tests/test_self_set_writes_717.py tests/test_browser_session.py
  tests/test_browser_webkit.py -k 'not WebKitIntegrationTests'`: 198 passed,
  3 skipped, 83 subtests passed after the browser audit receipt fix.
- Final browser rerun: `tests/test_self_set_writes_717.py
  tests/test_browser_session.py tests/test_browser_webkit.py
  tests/test_payment_autonomy_662.py tests/test_action_bound_approvals_619.py
  -k 'not WebKitIntegrationTests'`: **280 passed, 3 skipped, 161 subtests
  passed**, including all 20 #717 tests and the changed-during-judgment
  browser regression.
- `tests/test_connect_effects.py tests/test_self_set_writes_717.py`: 49 passed,
  25 subtests passed after updating the classification fixture, including the
  then-current 18 new #717 tests.
- `tests/test_self_set_writes_717.py tests/test_api_requests.py
  tests/test_connect_api_effects.py tests/test_payment_autonomy_662.py
  tests/test_scheduled_runs_ask_821.py`: 139 passed, 1 skipped, 74 subtests
  passed after the final API currentness fix.
- `tests/test_eval_judgments.py`: 25 passed, 19 subtests passed.
- `tests/test_messages.py`: 18 passed, 15,487 subtests passed after the final
  unknown-outcome wording change (an attempt, not an asserted dispatch).
- `tests/test_quickstart.py tests/test_conversation_connector_handoff.py
  tests/test_handoff.py tests/test_handoff_locale.py`: 150 passed, 18 skipped,
  240 subtests passed; 6 background-thread cleanup warnings about removed
  temporary databases.
- `tests/test_owner_settings_conversation.py tests/test_settings_orchestrator.py`:
  104 passed, 4 skipped, 46 subtests passed after updating the expected category
  list for `write_allowances`.
- `tests/test_settings_ui.py tests/test_management_layout.py
  tests/test_management_restructure.py tests/test_browser_settings_ui.py
  tests/test_settings_google_services.py tests/test_settings_guided_setup.py
  tests/test_settings_profile_input.py tests/test_settings_folder_picker.py
  tests/test_web_management_readiness.py -k
  'not test_browser_fixture_observer_captures_every_mutating_http_verb'`:
  86 passed, 1 skipped. The excluded case starts a subprocess HTTP listener;
  its unbounded stdout wait cannot run in this no-bind sandbox.
- `node --check src/personal_agent/web/app.js` and `git diff --check`: passed.

The broad affected-path sweep used the test files found with
`rg -l 'connector_permission|api_request|code_run|browser_session|a2a' tests/`,
plus the required and directly changed/affected test files (101 files). Its
command used the same prefix above, with
`-k 'not WebKitIntegrationTests and not test_base_import_and_optional_test_collection_without_a2a'`.
Result: **2,915 passed, 430 skipped, 12 failed, 19,321 subtests passed**, in
5,714 seconds. Two failures were fixtures corrected and rerun above (the old
`mutate` classification and the Settings category list). The remaining ten
failures/subfailures reproduced against unchanged HEAD as detailed below.
Refinements made during the long sweep have subsequent focused reruns; the
sweep is not claimed as a clean final-head run. Counts from overlapping runs
are not additive.

An A2A in-flight-scope test exceeded its five-second
thread-entry wait under the parallel run; the unchanged test passed in isolation
(`tests/test_a2a_peers.py::test_scope_change_withholds_inflight_answer[asgi]`).

Environment checks against an extracted, unchanged HEAD source reproduced:

- `tests/test_a2a_optional.py`: the no-A2A subprocess collection check exceeds
  its 30-second timeout. This check was excluded from the final broad sweep.
- `tests/test_agency_recovery.py::CliProcessStopTests`:
  `test_bounded_run_kills_the_group_when_interrupted` lacks its child PID file
  before the one-second interruption, and
  `test_the_shared_deadline_kills_the_cli_as_deadline_exceeded` reaches its
  deadline before the expected CLI failure record exists.
- `tests/test_orchestrator.py::UnmediatedEngine`:
  `test_a_trusted_local_attempt_whose_cli_ran_a_host_action_stops_with_effect`
  and
  `test_a_trusted_local_attempt_recorded_unmediated_without_a_cli_report_stops_with_effect`
  leave later subtests with a partial fixture after a Unix-socket skip. They
  fail the same `run_one()` assertion on unchanged HEAD.
- `tests/test_owner_model_dream.py::Misses`: the batch filtering and newest
  twelve misses tests both have an empty judgment capture on unchanged HEAD
  (`test_service_filters_batches_and_scrubs_earlier_misses_per_work` and
  `test_service_keeps_the_newest_twelve_eligible_misses_oldest_first`).
- `tests/test_recovered_steps.py::CliRouteOutcomeTests`:
  `test_a_navigation_still_observed_submitting_nothing_does_not_hold_a_reached_goal`
  and `test_reached_effect_free_failures_succeed_and_keep_the_evidence` also
  leave later subtests with a partial fixture after a socket skip, reproducing
  the same three `run_one()` subfailures on unchanged HEAD.

Baseline checks replace `src` in `PYTHONPATH` with the extracted
`/tmp/still717baseline/src`, using the same Python and `-m pytest -q
-p no:cacheprovider`, without `-n 4`. The socket-skip fixture is included
where the baseline test requires it.

No live model/account call, real browser integration, remote CI or independent
escalated final-head review was performed. The implementation was committed as `0083166`; the first verifier fixes below were subsequently recorded as `cef9da1`.


## Fresh-context verifier fixes (2026-11-15)

Scope remains #717 option ④. **State-changing GETs (`browser_open` and link
GETs) are out of scope.** The owner's configured decision AI judges browser
presses and submitting/mutate-labeled typing from origin, role, accessible
label and form field names. Typed values, page text, URL paths/query and form
state digests are not judgment inputs. A judged read/navigation proceeds;
irreversible/unknown writes wait. Plain local typing, typed Works,
owner-accepted runs, payment gates and sign-in eligibility retain their paths.
No keyword classifier or task/site/provider rule was added. The worker uses
`insertText`, not keyboard Enter; multiline local text remains local. Explicit
submit/Enter intent is gated, and page-triggered submits use the native guard.

- Chromium now installs the self-set state in every document-start guard,
  including after navigation, using the same guard source as WebKit.
- A cancelled non-payment form is reported once, for its submitting step.
  Its host-held reference expires after the approval window (600 seconds),
  without making a new approval request. Other reads/navigation continue;
  submitting again is a new attempt. Payment holds retain their behavior.
- Pre-dispatch API effect/work-mode/intranet checks (including code-run claims), connector claim/authority
  checks and browser target rechecks release unused write receipts to
  `not_run`. They restore only the original unexpired exact approval when no
  newer request occupies the Work, with its original owner/generation and
  preparation checks. A release is idempotent and never grants payment
  authority. Dispatch/unknown receipts still prevent duplicate writes.
- A2A cached messages go through existing `admit` identity/revision/input
  validation and return the cached answer without reserving another write.
  A not-fresh admission race and WorkModeRefused release unused tickets.
- Connection revision changes visibly turn an allowance off with a localized
  explanation. Revocation ignores stale connection/allowance revisions;
  enabling still checks both and requires owner confirmation. The Settings
  conversation schema exposes `write_allowances`.
- After-notices retain the owner-facing connection label even if the
  connection changes during transport. HTTP 4xx receipts/notices report a
  known rejection, not an unknown outcome, in both the allowance notice and
  the Work/API effect record. The existing non-self-set/payment response
  classification is unchanged.

Changed implementation files in this follow-up: `self_set_writes.py`
(authority release, Settings projection and receipts), `api_requests.py`
(preflight release and HTTP outcome), `a2a_peers.py` (cached return and release),
`browser_session.py` (press/type judgment and target recheck),
`code_run.py` (release on a refused state-change claim),
`browser_worker.py` (field-name metadata and one-shot held-form reports),
`browser_worker_chromium.py` (new-document state), `agent_runtime.py`
(Settings category), `quickstart_service.py` (release wiring and shared notice
helper), `settings_orchestrator.py` (stale revocation), `messages.json` and
`web/app.js` (visible lapse reason and rejected outcome). Regression tests are
in `tests/test_self_set_writes_717.py` and
`tests/test_browser_worker_chromium.py`. The latter also corrects the branch's
old exhaustive operation inventory to include `self_set_writes`.

### C15 follow-up review

Adapt the existing standard-library SQLite transaction path (`QuickStore.db`,
`consume_browser_step_approval`) and existing A2A SDK/`admit` boundary. The
external candidates and licence/platform/security review above still apply:
no package, protocol implementation or deployment changes are introduced.
SQLite transactions cover atomic state changes, but neither SQLite, the MCP
SDK nor the A2A SDK owns Still's exact owner approval, generation/preparation
binding or revision-specific allowance. This bounded sovereign-policy repair
therefore remains Build, using those adopted mechanisms. No new framework or
store is appropriate for this boundary.

The overlapping notice delivery shape is extracted into
`AgentService._deliver_authority_notice`, used by both payment autonomy and
self-set completion. Payment retains `surface_of`, its exact notice text,
logging and timing; self-set completion retains `surface_claiming` and the
durable fallback. Tickets and receipts cannot be unified without changing
payment behavior: payment's two-phase ticket rechecks mode revision and
records authorization/notice **before dispatch**, while self-set completion
records observed/rejected/unknown outcomes and notices **after transport**.
Their replay holds (`authorized` versus `dispatching`/terminal outcomes),
payloads and approval sources differ. Those are concrete existing contracts,
not a reason to duplicate the delivery helper. Payment tests are unchanged.

The installed Python 3.12 `http.HTTPStatus` documentation/classification was
also checked (`HTTPStatus.NOT_FOUND.is_client_error` is true). The existing
transport's numeric 4xx class is sufficient for this self-set rejection rule,
including non-enumerated 4xx values; another HTTP client/parser would not add
owner-authority semantics. Non-self-set and payment response handling retain
the existing refusal set for compatibility.

### Follow-up verification

All follow-up commands use
`PYTHONPATH=src:/tmp /Users/jongtaelee/Documents/project/Still/.venv/bin/python
-m pytest -q -n 4 -p no:cacheprovider -p skip_socket717`, except the two single
test runs (no `-n`). The temporary socket-skip fixture described above is
unchanged. No live model/account operation or remote CI is claimed.

- Original-source counterchecks use `git archive HEAD src` extracted into
  `/tmp/still717-verifier-baseline`, replacing `src` in `PYTHONPATH` with that
  directory's `src`. All **16 targeted regressions failed** against original
  commit `0083166`: the eight initial `VerifierRegressions` tests, the updated
  worker hold/browser mismatch/A2A cache tests, the Chromium navigation test,
  stale conversation revocation, the two separate A2A refusal/race tests, and
  the code-run claim-refusal test. These are model-free failure observations.
- `tests/test_self_set_writes_717.py tests/test_browser_worker_chromium.py
  tests/test_payment_autonomy_662.py tests/test_settings_orchestrator.py`:
  **325 passed, 1 skipped, 22 subtests passed**. Subsequent focused runs below
  include the final typing and notice-helper refinements.
- `tests/test_self_set_writes_717.py`: **31 passed** after splitting the A2A
  refusal/race tests and adding stale conversation revocation.
- `tests/test_self_set_writes_717.py::VerifierRegressions::test_type_gate_and_local_typing`:
  **1 passed** with multiline input kept local.
- `tests/test_self_set_writes_717.py tests/test_code_run.py
  tests/test_payment_autonomy_662.py tests/test_action_bound_approvals_619.py`:
  **123 passed, 1 skipped, 88 subtests passed**, including the code-run release.
- `node --check src/personal_agent/web/app.js` and `git diff --check`: passed.

The requested broad sweep selects the root `tests/test_*.py` files containing
`browser_worker` or `browser_session` (equivalent to `rg -l` on those root
files), all `tests/test_a2a*.py`, the seven explicitly requested files, and
`test_settings_orchestrator.py` and `test_messages.py`: 46 files. It uses the
same prefix and `-k 'not WebKitIntegrationTests'`. Socket binds are skipped
per common.md; real WebKit integration is unavailable in this sandbox.
Final affected-path rerun:
`tests/test_self_set_writes_717.py tests/test_browser_session.py
 tests/test_browser_worker_chromium.py tests/test_payment_autonomy_662.py
 tests/test_action_bound_approvals_619.py tests/test_settings_orchestrator.py`:
**465 passed, 2 skipped, 110 subtests passed**. Payment tests are unchanged.
The expanded HTTP test also failed on the intermediate implementation that
fixed the notice but still surfaced HTTP 404 as an unknown Work effect.
After aligning the self-set API effect record, the final run of
`tests/test_self_set_writes_717.py tests/test_api_requests.py
 tests/test_connect_api_effects.py tests/test_payment_autonomy_662.py` passed:
**132 passed, 1 skipped, 40 subtests passed**. Both 422 and 404 are covered;
non-self-set/payment behavior is unchanged.

Final notice checks:
`tests/test_self_set_writes_717.py::VerifierRegressions::test_connection_display_label_cannot_leak_a_stored_secret
 tests/test_self_set_writes_717.py::VerifierRegressions::test_rejected_http_has_label_and_known_outcome`
with `-n 2`: **2 passed**. Connection labels use the existing known-secret
redactor before entering tickets/receipts or after-notices.

Broad sweep: **1,787 passed, 253 skipped, 5 failed, 16,415 subtests passed**
in 2,563.50 seconds. This was not a clean final-source run: it collected the
old Chromium exhaustive-operation expectation and the earlier combined A2A
test fixture before their corrections. The A2A fixture used an invalid
WorkModeRefused egress name; the final tests use `a2a` and test the cached,
not-fresh and refused paths separately.

The final rerun of these cases (same prefix, `-n 2`) passed **4 tests**:

- `tests/test_browser_worker_chromium.py::test_every_shared_dispatch_op_has_an_implementation_and_unknown_is_typed`
- `tests/test_self_set_writes_717.py::Writes::test_peer_send_waits_and_allowance_dispatches_only_once`
- `tests/test_self_set_writes_717.py::VerifierRegressions::test_peer_work_mode_refusal_releases_ticket`
- `tests/test_self_set_writes_717.py::VerifierRegressions::test_peer_not_fresh_race_releases_ticket`

The remaining three subtest failures reproduce on the extracted original
`0083166` source with the same socket-skip fixture and `-n 2`:

- `tests/test_orchestrator.py::UnmediatedEngine::test_a_trusted_local_attempt_recorded_unmediated_without_a_cli_report_stops_with_effect`
- `tests/test_orchestrator.py::UnmediatedEngine::test_a_trusted_local_attempt_whose_cli_ran_a_host_action_stops_with_effect`

Baseline result: **3 failed, 2 passed, 2 skipped**. As in the earlier record
above, a Unix-socket skip leaves subsequent subtests with a partial fixture;
they fail the same `run_one()` assertion. These unrelated harness tests were
not changed or weakened. The broad sweep is reported with its actual failures,
not represented as green. The corrected paths have the passing reruns above.

Those follow-up fixes were subsequently recorded in commit `cef9da1` before
the PR #875 review below. That verification did not include real socket/browser
integration, live model/account operations, remote CI or a final-head Codex review.


## PR #875 review corrections (2026-11-15)

These corrections preserve option ④, payment behavior and the existing #821
eligibility rules. State-changing GETs (`browser_open` and link GETs) remain
out of scope. No commit is created by this correction session.

### Dispatch authority

`self_set_writes.recheck` checks the reserved event and the captured allowance
and connection revisions again at dispatch. Revocation, an off/on revision
change, a changed connection or a stopped Work refuses dispatch and releases
the unused reservation through the existing `release` path. That path also
restores an unused exact step approval when applicable. It never creates an
approval or silently upgrades a stale ticket.

The check sits beside the existing payment check in `ApiRequests._call_slot`
(after work-mode and DNS/intranet checks), `CodeRunner._claim_state_change`
(before returning the sandbox request decision), the connector permission
return and `BrowserSession._call` (before worker input/release). A2A checks
before invoking the SDK and again through `before_send` inside its bound HTTP
transport, after SDK work-mode, destination and peer checks. Thus even a
revision change during SDK preparation prevents the POST. Connection labels,
post-transport notices and payment checks keep their existing behavior.

### Held native form continuation

A self-set click/type that reaches a separately held non-payment form records
its input ticket as `held`. `BrowserSession` keeps the exact native form
reference while the existing `browser_submit` approval is pending. On the
approved continuation it releases that form directly, without another click
or typing. The continuation consumes only the issued approval for that form's
exact digest; it never reclassifies the held form as reversible or requests a
new approval implicitly. The native worker still verifies the current form
state and clears its held reference before its one permitted release.

`Capabilities` teardown previously destroyed the live DOM before an approved
Work could continue. `AgentService` now retains that Work's existing session
in host memory while its exact request remains pending, with a daemon timer
bounded to the existing ten-minute native hold window. The next capabilities
instance takes the session and rebinds its budget/approval context. Expired,
stopped or mismatched requests cannot recover it; expiry closes the session.
No typed values or DOM are added to durable state. Reads before approval keep
working without repeating the input or replacing the approval request. The
existing exclusive browser-profile lease remains occupied during this bounded
wait; this change does not add parallel browser sessions. A service/worker
restart loses the live form and cannot reconstruct or submit it from its digest.

### Settings polling and reuse

`renderWriteAllowances` retains unchanged connection rows in a JavaScript
`Map`. Identical polls and changes to another row retain the confirmation
button and keyboard focus. A change to that connection's row/revision, or
removal of the connection, discards its stale confirmation; the save closure
also checks that it still belongs to the current connected row. Enabling
still requires the owner's separate confirmation. Disabling remains direct.

C15: Adapt the already adopted SQLite transactions, A2A SDK/httpx bound
transport, Python standard-library `threading.Timer`/lock and browser DOM/Map
APIs. The supported packages, licences and platform/security review above
remain unchanged; this adds no dependency or protocol implementation. Still's
revision-bound owner authority and exact Work approval continuation remain
Build because none of those external facilities owns that policy. Reuse is
concrete: `release`, the payment-adjacent dispatch seams,
`_browser_request`/`_consume_browser_step`, the native `release_submit`, the
existing `Capabilities` session and the executable Settings DOM test harness.
A persisted form recreation or a separate approval/session framework is not
needed: a digest cannot reconstruct the live native form, whereas retaining
the existing session preserves its exact guard and one-use release.

The web review followed `web/AGENTS.md` and the pinned web-interface-guidelines
and frontend-design checklists: existing Settings surface, unchanged backend
contract, preserved confirmation/focus, no layout or copy redesign.

### PR #875 verification

All new tests are model-free. `tests/test_self_set_writes_717.py` adds six
pre-dispatch refusal cases (including both A2A boundaries), click and typing
continuations across capabilities teardown, a changed-form rejection and an
executable Node DOM polling regression. Against the original `cef9da1` source
extracted with `git archive HEAD src`, all nine new Python regressions failed.
The Node regression separately failed against that extracted `web/app.js`
with “poll must retain the confirmation”. All ten pass on the correction.

Commands use the same temporary socket-skip fixture and environment as the
preceding verification:

```sh
PYTHONPATH=src:/tmp /Users/jongtaelee/Documents/project/Still/.venv/bin/python -m pytest -q -n 4 -p no:cacheprovider -p skip_socket717 tests/test_self_set_writes_717.py -k review875
PYTHONPATH=src:/tmp /Users/jongtaelee/Documents/project/Still/.venv/bin/python -m pytest -q -n 4 -p no:cacheprovider -p skip_socket717 tests/test_self_set_writes_717.py tests/test_payment_autonomy_662.py tests/test_browser_session.py tests/test_code_run.py tests/test_settings_ui.py
node --check src/personal_agent/web/app.js
git diff --check
```

Results: **10 passed**; focused files **186 passed, 2 skipped, 56 subtests
passed**; JavaScript syntax and diff checks passed. Payment tests are unchanged.
The broad run repeats all 46 previously selected files and adds
`tests/test_code_run.py` and `tests/test_settings_ui.py`, using the same pytest
prefix and `-k 'not WebKitIntegrationTests'`. Its final result is recorded below.
Only model-free/fixture evidence is claimed; real socket/browser integration,
live model/account operations and remote CI are unavailable in this sandbox.


The 48-file sweep command was `python3 /tmp/run875suite.py`; the temporary
runner selected its files with the following model-free test inventory (and
invoked the pytest prefix above):

```python
files = {str(p) for p in Path('tests').glob('test_*.py')
         if 'browser_worker' in p.read_text() or 'browser_session' in p.read_text()}
files.update(str(p) for p in Path('tests').glob('test_a2a*.py'))
files.update('tests/' + name + '.py' for name in (
    'test_self_set_writes_717', 'test_scheduled_runs_ask_821',
    'test_payment_autonomy_662', 'test_action_bound_approvals_619',
    'test_connect_effects', 'test_owner_settings_conversation',
    'test_eval_judgments', 'test_settings_orchestrator', 'test_messages',
    'test_code_run', 'test_settings_ui'))
# pytest prefix + ['-k', 'not WebKitIntegrationTests', *sorted(files)]
```

Full result: **1,828 passed, 254 skipped, 3 failed, 16,425 subtests passed** in
483.97 seconds. The three failures are the same subtests in the two
`UnmediatedEngine` tests named in the preceding verification: the socket-skip
fixture leaves the next subtest with partial state. No new regression failed.

Re-running those two test nodes against the extracted, unchanged `cef9da1`
source with the same socket fixture and `-n 2` reproduced **3 failed, 2 passed,
2 skipped**. The failures are pre-existing harness limitations, not reported
as a green sweep or removed from the requested run. Final JavaScript syntax
and diff checks passed. No payment test was edited; no commit was created.


## Catalog register correction (2026-11-15)

Reviewed all 20 catalog entries added or changed against `origin/main`.
Six Korean `write_allowances.*` messages (risk, payment, wait, replayed,
notice and unknown_notice) needed soft 해요체 endings. Their meaning and
placeholders are preserved. The other Korean entries and all Japanese entries
already use the required register or neutral UI labels; Japanese has no
だ・である endings. No new non-catalog Korean/Japanese sentence needed correction.
C15 review: N/A for this content-only correction; it adds no mechanism,
component, dependency or case to existing policy machinery.

Before correction, `test_message_register.py` and `test_messages.py` reproduced
**1 failed, 19 passed, 15,511 subtests passed**, with the existing register test
identifying all six messages. After correction, the following model-free run
passed **261 tests and 15,634 subtests**, with **4 socket-dependent skips**
under the unchanged common.md sandbox fixture:

```sh
PYTHONPATH=src:/tmp /Users/jongtaelee/Documents/project/Still/.venv/bin/python -m pytest -q -n 4 -p no:cacheprovider -p skip_socket717 tests/test_message_register.py tests/test_messages.py tests/test_self_set_writes_717.py tests/test_scheduled_runs_ask_821.py tests/test_settings_orchestrator.py tests/test_owner_settings_conversation.py tests/test_connect_effects.py tests/test_settings_ui.py tests/test_payment_autonomy_662.py
```

`git diff --check` passed. Existing tests were not changed. No commit was created.


## Refreshed typing target correction (PR #875, head `5b30ee5`)

The self-set typing recheck resolved the intended control again but left the
original dispatch descriptor in place. Reordering controls during judgment
could preserve the exact binding and form state while moving that field to a
new index. The worker driver's refreshed `_expect` map would then validate the
other control now occupying the old index. `BrowserSession.type` now assigns
both `snapshot` and `element` from the successful recheck, as `click` already
does. Native dispatch, confirmation context and error reporting therefore use
the same refreshed snapshot.

The other recheck-to-dispatch paths were inspected:

- `click` already updates both descriptors after comparing the binding, before
  `_input` dispatches its index. Submit buttons use that same click path.
- `_cancelled_submit` and `_resume_self_set_submit` dispatch an exact held-form
  record through `WebKitWorkerDriver.release_submit`, not `_expect` or a cached
  control index. `Worker.op_release_submit` checks the recorded form identity
  and state before invoking the shared `RELEASE_SCRIPT` by hold ID. That script
  dereferences the original form/submitter, checks that they remain connected
  to the document with matching identity and live form state, then consumes
  the hold once. Both browser engines use this
  shared release path. No stale-index dispatch was found there.

C15: N/A for introducing machinery: this is a one-line correction to consume
an existing successful recheck, with no new component, dependency or policy
case. It reuses the assignment already present in `BrowserSession.click`.
The regression reuses `FakeDriver`'s HTML parser and the real
`WebKitWorkerDriver.snapshot`/`_target`/`type` methods, replacing only worker
startup and transport. The injected judgment swaps two fields in one form;
its binding remains valid, and the test verifies the descriptor, new index
and which field actually receives the text. It failed before the correction
with `Other field != Intended field` and passes afterwards.

Focused command (model-free, unchanged sandbox socket-skip fixture):

```sh
PYTHONPATH=src:/tmp /Users/jongtaelee/Documents/project/Still/.venv/bin/python -m pytest -q -p no:cacheprovider -p skip_socket717 tests/test_self_set_writes_717.py::VerifierRegressions::test_review875_typing_uses_target_refreshed_after_judgment
```

Result: **1 passed** after the fix (**1 failed** before it). The broader run
uses `python3 /tmp/run875typing.py`, the preceding 48-file selection plus
all `tests/test_browser*.py`, with the same `-n 4` prefix and
`-k 'not WebKitIntegrationTests'`. Final results follow below. Payment tests
are unchanged. No commit is created by this correction session.


The 49-file sweep finished with **1,833 passed, 254 skipped, 3 failed, 16,433
subtests passed** in 92.09 seconds. The browser/self-set tests passed. The
three failures were the same socket-skip-induced subtests in the two
`UnmediatedEngine` tests named above. Re-running those two nodes with the
same fixture and `-n 2` against `git archive HEAD src` extracted from unchanged
`5b30ee5` reproduced **3 failed, 2 passed, 2 skipped**. No test was weakened or
removed. `git diff --check` passed. Real WebKit/socket integration remains
unavailable in this sandbox; no live-model or remote-CI result is claimed.
