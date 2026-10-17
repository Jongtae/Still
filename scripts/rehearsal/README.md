# Connection rehearsal (HARDEN-GENERIC-01 #1445, PROC-MEM-01 #1489)

Run the generic connection path end to end before the owner sees a change to it. The rehearsal uses the owner's real Judgment AI and workers, a scratch Still store, and synthetic vendors.

```sh
PYTHONPATH=src python3 scripts/rehearsal/run.py                      # both domains, about 15–30 minutes
PYTHONPATH=src python3 scripts/rehearsal/run.py --domains parcel     # one domain
```

## What it does

- `vendor.py` serves two synthetic APIs. Each is exposed through a public ngrok tunnel while its domain runs, so the product needs no rehearsal exception.
  - **brokerage** reproduces:
    - a documentation page;
    - a JSON-body token exchange (not RFC 6749);
    - several secret headers;
    - an account list and a per-account balance with a short product code;
    - one token per minute;
    - one transient 502.
  - **parcel** (non-finance) reproduces:
    - a documentation page;
    - one API-key header;
    - a tracking lookup that takes the invoice as 12 digits without dashes, while the owner writes it with dashes.
  - Both count documentation fetches.
- `run.py` sets up the scratch store:
  - it copies only the owner's AI route (`family_setup.share_ai_route` plus the Judgment AI's recorded CLI checks);
  - the store lives in `~/.local/share/agentos-rehearsal`, so the Judgment AI's qualification, which is bound to the store path, is earned once;
  - every run starts without a secret set, and so without a function library.
- **Per domain, two owner requests on the same connection.** The driver acts only where the owner acts: it confirms the proposed secret set and enters values on the one-time page, reading field names and labels as a person would.
  - **First request** (with the docs link) checks:
    - the Work succeeded and the answer is right;
    - `code_run` was used;
    - a function library was saved (#1486).
  - **Second request** (a different value, no docs link) checks:
    - the Work succeeded;
    - the answer is right for its own value, which proves no value carried over from the first request;
    - the saved library was imported;
    - no documentation was fetched;
    - it took fewer tool calls than the first request.
  - **Throughout:**
    - no key or secret appears in records or answers;
    - no answer asks for credentials in chat;
    - for brokerage, a valid token was reused: the first request issued fewer tokens than it ran code, and the second issued none.
- It prints a JSON report with each request's seconds, tool calls, documentation fetches, code runs and library states. It exits 0 only when every check passes, including that the Judgment AI orchestrated.

## Notes

- Uses the owner's subscription for a handful of model turns per request. Run it after changes to the connection, code-run, function-library, continuation, orchestration or budget paths, not on a schedule.
- Needs `ngrok` with an auth token. It holds a tunnel only while its domain runs, and stops it after.
- Nothing here names a real service. Do not add one: the vendors reproduce shapes, not providers.
