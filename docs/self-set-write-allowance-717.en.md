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
escalated final-head review was performed. No commit was created.
