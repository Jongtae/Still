# Sandboxed code runs for any API (CONNECT-CODE-01 #1380)

## Status

Design and threat model for phase 2 of [#1380](https://github.com/Jongtae/Still/issues/1380), a child of CONNECT-LIVE-01 [#1375](https://github.com/Jongtae/Still/issues/1375) under V1.4-01. This document selects an implementation. It is not evidence that the capability works; phases 3 and 4 supply that. The owner delegated the review disposition for this program to the implementer on 2026-10-11. Any AI review is recorded as self-review.

## Owner outcome

"Show my account list and balances at service X" must work when service X only offers a non-standard API. An experienced developer reads the manual and writes a few dozen lines against it. The owner's AI must be able to do the same: write the code, have it run, get the result. Owner-only steps (developer-portal registration, key issuance) stay with the owner. New non-standard APIs must need **no Still change**.

**C16 answer.** The owner's AI does the work: it reads the documentation, writes the code and interprets the result. Still removes one blocker, which is that the AI has nowhere safe to run code holding the owner's secrets. Still keeps the invariants an AI must not own: secrets, egress, payment approval, no replay, and Evidence. No service, site or API shape is named in core code.

## Why the earlier routes are insufficient

- `mcp_setup.RemoteMcpSetup` registers public HTTP MCP servers only.
- `api_requests` slots put one secret in one header (or RFC 6749 client credentials). Real APIs need a JSON-body token exchange, several secret headers, secrets in a query or body, request signing, or a vendor SDK.
- Adding one shape at a time never ends. That is the treadmill the owner rejected.

## Existing Solutions Review

- **Problem/boundary:** run AI-written code that calls a vendor API with the owner's secrets. Isolate it from owner files. Limit egress to declared hosts. Keep secret values out of the code, the model, logs and Evidence. Keep per-request effect control.
- **Internal candidates:**
  - `api_requests.ApiRequests`: slot hosts, effect identity, `_effect_guard` dispatch claim, `_approve` one-use payment approval, `_settle_effect`, provenance.
  - `api_credentials.ApiCredentials`: Fernet with a Keychain key, slot-bound fingerprint.
  - `api_input` + `phone_input.PhoneInput`: secure owner input page.
  - `quickstart_service.settings_review_ready`: `api-setting` continuation that resumes the same Work.
  - `bounded_execution.bounded_run`, `current_context.redact_known_secrets`, `information_use`.
  - Tool wiring: `agent_runtime` schemas, `manifests`, `bounded_execution` tool lists, `conversation_projection`, `telegram_presence`.
  - All of these are reused. Only the sandbox runner is new.
- **Platform:** macOS `sandbox-exec` (Seatbelt) and Linux bubblewrap. Both are low level. Writing profiles, an egress proxy and secret substitution by hand would be Build.
- **Official/reference:** Anthropic **sandbox-runtime** `@anthropic-ai/sandbox-runtime` 0.0.79. Apache-2.0, published 2026-10-07, maintained in `anthropics/sandbox-runtime`. It provides:
  - Seatbelt/bubblewrap/Windows isolation and an allow-only domain proxy;
  - `credentials.envVars` `mode: "mask"`: the sandbox sees a sentinel and the host proxy substitutes the real value only on egress to `injectHosts`, in headers and bodies;
  - `tlsTerminate` with an ephemeral CA;
  - a `filterRequest(Request)` per-request callback.

  It is a research preview: pin the exact version and review every update. `codex sandbox` (Codex CLI 0.153.4) wraps Seatbelt only, with no domain proxy or credential masking.
- **Mature OSS:** the Fly.io tokenizer (secret-injecting proxy, Go) and mitmproxy addons each cover one piece; combined they would be more glue than sandbox-runtime. Docker is rejected because it is an owner prerequisite (the owner's own criterion) and would add a daemon.
- **Compatibility test (2026-10-11, owner Mac, Node 26.9, Homebrew Python 3.13):**
  - the masked variable read as `fake_value_<uuid>` inside the sandbox;
  - httpbin received the real value in both an `X-Key` header and a JSON body field;
  - reading `~/.zshrc` raised `PermissionError`;
  - `example.com` was refused with 403;
  - `filterRequest` received method, host, path, headers and body carrying the sentinel, before substitution.
  - Caveat: Apple's `/usr/bin/python3` 3.9 rejected the terminating CA, so the runner uses a modern interpreter.
- **Maintenance/security/supply chain:** npm package with 4 runtime dependencies (`zod`, `commander`, `node-forge`, `@pondwader/socks5-server`). Install it from a committed lockfile with integrity hashes using `npm ci --ignore-scripts`. Record the version and digest in Evidence.
- **Licence:** Apache-2.0 is compatible with AGPL-3.0-only as an unmodified dependency.
- **Runtime fit:** needs Node ≥ 18 on the host. When Node is missing the capability is reported as unavailable, never faked.
- **Decision: Adapt.** sandbox-runtime runs behind a thin Still runner. Still owns policy, secrets, approval and Evidence.
- **Rejected:** hand-written Seatbelt profiles and a proxy (Build without an unmet contract); Docker (owner burden); one more fixed API shape (the treadmill).

## The capability

One worker tool, `code_run`, offered on the existing Still tool relay to every worker route, like `api_request`. Inputs:

| Field | Meaning |
|---|---|
| `code` | Python source the AI wrote (bounded size) |
| `packages` | optional `name==version` pins, installed into the run's own environment |
| `hosts` | public hosts the code may reach (in addition to any secret set's hosts) |
| `secrets` | names of owner secret sets the code needs |
| `effect` | `read`, `mutate` or `payment`: the AI's declaration for the run |
| `requests` | for `read` runs: the non-GET requests the code will make that do not change anything (for example a token endpoint), as `METHOD host path` |
| `timeout_seconds` | bounded by the Work's remaining budget |

The output is bounded stdout and stderr with known secret values redacted, the exit status, a request log (method, host, path, status, bytes; no bodies) and the installed package list.

Tool description, in essence: *run Python you wrote in an isolated sandbox. It has no access to the owner's files, reaches only the hosts you list, and sees each secret as a placeholder environment value that is substituted only on requests to that secret set's hosts.* There is no per-service text.

## Sandbox profile

- **Filesystem:** read is denied under the owner's home, except the run's temporary work directory and the interpreter prefix. Write is allowed only in the work directory. Each run gets a fresh directory, deleted afterwards.
- **Network:** `allowedDomains` = declared `hosts` ∪ the hosts of the named secret sets ∪ the package index during installation (`pypi.org`, `files.pythonhosted.org`). sandbox-runtime refuses loopback, link-local, private interface and metadata addresses. `tlsTerminate` uses an ephemeral per-run CA trusted only inside the sandbox. A certificate-pinning client fails closed.
- **Process:** Still's own modern Python creates a run venv. Wall-clock timeout comes from the Work budget. Output is capped. The process group is killed on timeout or on a Work stop.
- **No inherited environment:** the child gets only `PATH`, locale, the sandbox CA variables and masked secret variables.

## Secret sets

A secret set extends the existing API slot instead of adding a store:

- a name, its hosts and the `money` flag (existing slot fields);
- **named values** such as `APP_KEY`, `APP_SECRET` and `ACCOUNT_NO`. The AI labels them from the documentation; labels are public metadata and values are secret.
- Values are stored through `ApiCredentials` (same Fernet/Keychain boundary). The slot fingerprint also binds `kind`, `fields` and `reads`, so changing a reviewed read invalidates the stored values.
- A set is `kind: "code"`: `code_run` uses it, and `api_request` refuses it (`api_slot_code_only`).
- Each field declares its **use**:
  - `header_or_body` (default): exposed as an environment variable in `mode: "mask"` with `injectHosts` = the set's hostnames. The code holds a sentinel. sandbox-runtime substitutes it in headers and bodies only; it does **not** substitute URL paths or queries (observed 2026-10-12).
  - `in_code`: the real value inside the run, for a URL query or request signing. The input page says so. A run using such a value may reach the set's hosts and nothing else.
- Stored values are redacted from stdout, stderr and the request log (`redact_known_secrets` covers named fields).
- The run sees `<SET>_<FIELD>` (set name upper-cased, `-` as `_`). The current-context line and `settings_read` list those names and the reviewed reads, never values.

Owner input reuses the `api_input` flow:

1. The AI proposes the public draft (name, hosts, money, note, `fields`, `reads`) through `settings_change api_connections connect` with `continue_work=yes`. The confirmation summary shows each label and its use.
2. `PhoneInput` renders one password field per named value. Labels from the AI's draft are HTML-escaped.
3. There is no read probe: Still cannot know the API's shape. Readiness is the stored values; the original Work's own code run shows whether they work. The draft's `api-setting` continuation resumes the same Work.

Waiting for the owner may take hours. The Work parks in its existing awaiting state.

## Egress and effect policy

Every HTTP request the code sends reaches `filterRequest` before substitution, carrying only sentinels. The runner forwards a summary (method, host, path, query, body digest) to the Still host process over a pipe and waits for allow or deny.

**Implemented scope (pilot): reads only.**

| Request | Decision |
|---|---|
| GET/HEAD to a declared host or a set host | allow, logged |
| non-GET exactly matching a set's owner-reviewed `reads` (`METHOD host/path`, for example a token exchange) | allow, logged |
| any other non-GET to a named secret set's host | **owner approval of exactly that request** (#1456, below) |
| any other non-GET to another host | **deny** (403 to the code, shown in stderr) |
| any undeclared host | **deny** |

**Owner-approved state changes (#1456).** Owner decision 2026-10-13 ("let's go with A"): a code run may send an order, a transfer or another state change to a named secret set's host, once, with the owner's approval of exactly that request. It reuses `api_request`'s machinery rather than a new one:

- **What is approved:** the set, method, URL with its query and the body. The runner gives the host the body as text, with each masked value's sentinel replaced by `{{NAME}}`; sentinels differ per run, names do not. The binding is `step_binding(work, 'code_run', url, set|method|url, argument=body, state=code-effect-v1|set@revision|effect)`, and the effect is `payment` when the set is marked money. Stored values are redacted from the label the owner sees.
- **Flow:** the first run's request is refused with a note to the code, and the owner is asked through the existing step-approval surface (Telegram button, web). On approval the Work resumes, and the same code's same request consumes the approval and is sent once.
- **Replay and currentness:** `ApiRequests._effect_identity` and `_effect_guard` refuse a replay of the same request in the Work's request chain. They also refuse it after an unknown effect, on a stopped Work, or on a changed set. The dispatch claim is an `api_dispatch` row.
- **Outcome:** the sandbox sees requests, not responses. A run that ended on its own settles the claim `observed`, and its output shows what the service answered. A cut-off run leaves the effect unknown, and nothing further is sent until it is resolved. The run's Evidence lists the sent requests (`effects`, method, host and path only).
- **Not changed:** per-action approval for every state change. A watch cannot sell unattended, and a standing mandate for money connections remains a separate owner decision.

## Function library (PROC-MEM-01 #1486)

Each code-run secret set can keep a function library: verified procedural memory, saved as code (`procedures.py`). The rule is in the [Connection and Delegation Contract](connection-delegation-contract.en.md) §2.

- **Use:**
  - A run that names the set gets the library written as `lib_<set>.py` in its run directory. That is the Work's own pending proposal if it has one, otherwise the set's current version.
  - With a library present, `main.py` starts through a `runpy` launcher that puts the run directory on `sys.path`, because `python -I` leaves it off.
  - A footer appended to the module counts each public function's calls and failures. It writes them, with signatures, first docstring lines, required parameters and `EXAMPLES`, to a report file in the run directory. That report is the AI's own, untrusted account: Still bounds and validates it.
- **Save:**
  - A run passes `library` for its single named set.
  - The module becomes the Work's candidate only when the run ended with exit 0, at least one library call succeeded, every public function has a docstring and the source holds no stored secret value.
  - The **parameter-flow check** must also pass. Each function that takes arguments is called with its two `EXAMPLES` in its own sandbox session (same hosts, secrets and state as the run). Reads go through; every other request is recorded and refused, never sent and never shown for approval. The save is refused when:
    - the two calls made identical requests;
    - a differing argument reached neither call's own requests;
    - the first example's value still appears in the second call's requests.
  - **What is compared:** each recorded request's method, path, query, headers and body, as letters and digits only (#1489). An argument may therefore travel in a header (a token), and a function may reformat it (dashes, case). Masked values appear as `{{NAME}}`. The records stay in memory for the comparison only.
  - The candidate is committed as the set's next version when the Work settles `REACHED`.
- **Context:** the set's line lists the current version's functions (signature, call counts, first docstring line), its module name and its `procedure:<id>` ref.
- **Undo:** `forget_record` with `procedure:<id>` takes that version out of use and puts the newest kept version back. Undo within 7 days restores it, unless a newer version was saved since. Removing the secret set removes its library.
- **Limits:** the source is at most 32 KB, with 24 public functions. At most 8 functions with arguments are checked, with a 30-second session each and 120 seconds in total. The check text names the reason so the AI can fix it.

## Evidence, audit and Presence

- **Evidence:** code digest, sandbox-runtime version/digest, package pins, allowed hosts, the request log and outcome. It never holds request bodies or secret values.
- **`information_use`:** records each outbound host and which secret set was used.
- **Presence:** shows a plain step label such as "코드 실행" and the hosts contacted. The answer must come from observed output (existing outcome judgment).

## Threat model

| Abuse or failure | Mitigation |
|---|---|
| Prompt-injected documentation makes the AI write code that exfiltrates owner files | Home read denied; only the work directory is visible |
| Code sends a secret to an attacker host | The code holds only the sentinel; substitution happens only toward the set's hosts. Other hosts receive the sentinel or are refused |
| Code sends a secret to an attacker-controlled path on an allowed vendor host | Same trust as `api_request`: hosts are owner-confirmed vendor hosts |
| A vendor response echoes the real secret back | Known secret values are redacted from output before the model sees it |
| An order or transfer slips into a run | A non-GET that is not an owner-reviewed read is sent only after the owner approved exactly that request (set, method, URL, body); to any other host it is refused |
| The approved request differs from what is sent | The binding covers the body text with masked values by name; another body, query or path is another approval. Headers carry authentication only |
| A reviewed "read" is really a state change | The owner reviews each entry on confirmation; changing one rebinds the stored values; entries are exact paths, not prefixes |
| A crash or retry repeats an order | An approval is consumed once; the dispatch identity refuses the same request again in the request chain, and a cut-off run leaves the effect unknown, which blocks further state changes |
| Malicious package or install script | Pinned versions, PyPI hosts only during install, and the install runs inside the same sandbox without secrets. Names and versions are recorded |
| DNS rebinding or private network access | sandbox-runtime denies loopback, link-local, private interface and metadata addresses |
| Resource exhaustion | Timeout from the Work budget, output caps, process-group kill |
| sandbox-runtime defect (research preview) | Exact pin and lockfile, update review, capability disabled on load failure |
| MITM CA misuse | Ephemeral per-run CA, trusted only inside the sandbox, deleted with the run |
| A real value inside the sandbox (`in_code`: URL query or signing) | Use shown to the owner on confirmation and the input page; the run reaches only the set's hosts; output and request log redacted |

## Implementation slices (phase 3)

1. **Runner and tool without secrets** (PR #1399, merged): pinned sandbox-runtime install, Node runner, `code_run` wiring, filesystem/egress/timeout, GET/HEAD only.
2. **Secret sets and reviewed reads:** named values in `ApiCredentials`, multi-field secure input, masked and `in_code` values, redaction, owner-reviewed non-GET reads. All other non-GET requests are refused.
3. **State-changing requests:** deferred (see *Egress and effect policy*).

## Verification (phase 4)

- **Unit and model-free integration tests** with a local test server and fake clocks (#612).
- **Owner-machine operation:** the development-case request through Telegram on the installed build, end to end (discovery → route choice → owner registration → secure input → same-Work continuation → result).
- **Generality:** owner-chosen, undisclosed holdout services.
- Merges, tests or a connection receipt alone never close #1380.

## Non-goals

- No shell tool for the worker.
- No access to owner files from runs.
- No background or scheduled runs.
- No vendor SDK bundled by Still.
- No service-specific code, prompt or skill.
- Windows support is not part of this pilot.
