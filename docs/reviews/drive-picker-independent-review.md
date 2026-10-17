# Independent security and recovery review: PR #311

## Scope and evidence

Reviewed PR #311 at `faf42326f18ceecc9d79ffa001de3bf76320b4a4` against
`origin/main`, concentrating on the seven previously unresolved Drive Picker
threads. This review is based on current source, fixtures, a local
multi-instance grant reproduction, and PR CI metadata. It does not claim that
Google OAuth, Google Picker, Telegram, a browser CSP, or a real Drive file was
operated.

## Validation observed

* `PYTHONPATH=src python3 -m pytest -q tests/test_drive_web_oauth.py tests/test_quickstart.py` — **80 passed**.
* PR #311 required `validate` check is successful for this head at review
  time.
* The source and fixtures show a nonce-bearing Picker bootstrap CSP without
  `unsafe-inline`, complete local Picker configuration before enablement,
  owner-only external secret-file validation, bounded production reads (1 MB
  per response; at most 20 selected files and 60 KB assembled context), and
  preservation of Drive context when selected local context is appended.

## Blocking findings

### High — Picker grant is not atomic across local runtime instances

`DriveWebOAuthHandoff.select_files_for_grant()` uses an instance-local
`threading.Lock`. The encrypted store's read of the unused grant and write of
`{"used": true}` are separate operations, with no durable transaction/CAS.
Two local runtime instances (or a restart/overlap) using the same secret store
therefore do not share the lock and can both accept the one-use grant.

Independent local reproduction created two `DriveWebOAuthHandoff` objects over
one `EncryptedDriveSecretStore`, then concurrently called
`select_files_for_grant()` with the same grant. Both calls returned success
(`ok:a`, `ok:b`). The fixture covers one sequential replay in one instance,
not this cross-instance race. This contradicts the one-use atomic grant
requirement.

### High — rejected token does not prevent subsequent direct selected-file reads

On a 401/403, `AgentService.selected_drive_context()` calls
`mark_reauthentication_required()`, which records `reauth-required` but does
not clear `TOKEN_KEY`; `_connected()` does not require the status to be
`connected`. The normal `run_one()` path checks status before invoking the
read, but `selected_drive_context()` itself can be called again and reuses the
rejected access token.

Independent local reproduction made the first selected-file transport raise
HTTP 401, observed status `reauth-required`, replaced the transport with a
successful one, then called `selected_drive_context()` again. It returned the
selected content. The current fixture asserts only the status after the first
failure, not that a second read is rejected until a fresh OAuth connection.
This fails the rejected-token reauthentication boundary.

## Reviewed non-blocking areas and limits

* The Picker page deliberately includes a browser selection grant and a
  referrer-restricted developer key; it does not include the server OAuth
  access token in the covered fixture. The two external Google scripts are
  explicitly allowed by CSP; no live browser/CSP enforcement was observed.
* When `AGENTOS_DRIVE_SECRET_FILE` is supplied, configuration reads all four
  required Drive values from that owner-only external file and ignores
  environment values for those fields. Missing Picker configuration leaves the
  capability disabled. The reviewed tests cover these paths.
* The bounded production `drive_read` transport applies its 1 MB limit before
  returning bytes. Test doubles can bypass that transport, and no live Drive
  response behavior was observed.
* The source appends owner-selected context to the already assembled Drive
  prompt, preserving the Drive content; there is no focused fixture that
  asserts the combined Drive-plus-attached-context model payload.

## Disposition

**Changes requested; do not merge #311 yet.** A durable cross-instance
consume-once primitive for Picker grants and a fail-closed post-401/403 token
boundary (including regression tests) are required. The passing focused suite
and green CI do not cover either reproduced failure. No schedule, merge, or
live external operation is recommended or claimed by this review.

## Final re-review of the two blocking findings (`b99a485`)

Reviewed `b99a48503033fe04e60a6d4944297c4d472f2a51` without modifying source
or tests.

### Picker grant consume-once

The grant lock is now the module-global `PICKER_GRANT_LOCK`, assigned to every
`DriveWebOAuthHandoff` instance. It serializes the encrypted grant
read/consume/write path across handler instances in the same local process,
which is the exact boundary that allowed the earlier independent reproduction.
`test_picker_grant_is_single_use_across_handoff_instances` uses two handoff
objects, a barrier, and concurrent consumption of one grant; it passes with
one success and one rejection. The earlier High finding is resolved for the
implemented process-wide runtime boundary.

### Reauthentication revocation

`mark_reauthentication_required()` now clears the encrypted token and selected
file record before recording `reauth-required`. Consequently `_connected()`
rejects a subsequent selected-file read for lack of a token, and the former
direct-read reproduction cannot reuse the rejected token.
`test_reauthentication_clears_selected_files_and_token` passes and confirms a
selected read does not invoke its transport after revocation. The earlier High
finding is resolved.

### Evidence and limits

* `PYTHONPATH=src python3 -m pytest -q tests/test_drive_web_oauth.py tests/test_quickstart.py` — **82 passed**.
* `PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_drive_web_oauth.py' -v` — **15 passed**.
* `git diff --check` — passed.
* At this review point, GitHub `validate` for this head is **pending**; no CI
  success is claimed here.

The global lock is intentionally process-wide, not an inter-process locking
or database-CAS primitive. The reviewed runtime is a single local server
process; a future multi-process deployment would need a durable inter-process
consume-once design and separate validation. No live OAuth, Picker, Telegram,
or Drive operation was observed.

### Final disposition

**No remaining blocking finding for the two reviewed regressions in the
single-process local-runtime scope.** Merge readiness still depends on the
pending required CI and the separately stated live-operation limits.
