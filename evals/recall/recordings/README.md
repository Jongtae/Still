# Recorded recall inputs

The one approved synthetic run at code head `2474d74d` recorded
[`terms.json.gz`](terms.json.gz) (`recall-terms/1`) and
[`plans.json.gz`](plans.json.gz) (`recall-plan/2`). Claude ran it under the
sweep lock, using the frozen [setup](https://github.com/Jongtae/Still/issues/921#issuecomment-6958000217)
and [follow-up setup](https://github.com/Jongtae/Still/issues/921#issuecomment-6958712465).
The [results](https://github.com/Jongtae/Still/issues/921#issuecomment-6960844558)
include the successful full model-free replay, which raises on inventory or
executor-outcome mismatch.

Route: Codex subscription CLI (`live-route:subscription_cli`), requested model
`gpt-5.6-terra`; observed model **not reported**. Tokens are **not observable**.
There was no judge or embedding call. Of the 248-call cap, 204 calls were
admitted: 11 requalification, 45 terms and 148 plans. Zero calls were refused;
there was no failure. Terms keyed 1,081 rows in 45 ok batches; all 148 plans
were decided and accepted. The store is the composed synthetic v4 fixture
(1,078 rows) plus 28 dev-addition rows. Dev cases run once, held-out cases
three times, then the four additions once. Workers are fixtures.

The recorded command was the following, with `PYTHON` set to the project's venv
interpreter and `OUT` set to a fresh output directory outside the repository:

```sh
PYTHONPATH=src:evals "$PYTHON" -m agentos_eval recall-record \
  --seed synthetic-terra --requalify --max-calls 248 \
  --fixture evals/recall/recall-pairs-v4.json \
  --additions evals/recall/recall-gate-dev-1.json \
  --heldout-runs 3 --out "$OUT"
```

The gzip files were made with `gzip -9 -n`: mtime 0, no stored filename.
Decompression preserves the recorder's JSON bytes exactly; do not regenerate
or re-serialize them. The recorder continues to write plain JSON outside the
repository. Do not copy seed databases, credentials, owner state, raw plan-input
text or model logs here. Reports stay outside the repository.

| Binding | Digest |
| --- | --- |
| Composed fixture SHA-256 | `75b1c5ea898abc59b79cae78f5b97e082cda9bea60867c7d89a35f61026a0cb1` |
| Terms object SHA-256 (`plans.terms_digest`) | `3af70b562107789501ef3280d6d909c7989a290ab14ce0c224c692d565150e9d` |
| Terms filter source SHA-256 | `56bed506c794562a81328d5441459f2a7117fb25ed12978959e2aac9f7275a04` |
| Terms prompt/schema SHA-256 | `a01ca10a442aecab73c007bdc9b45aedf4957ed3a506296fe79c7282c1372ed1` |
| Plan prompt/schema binding | `aae631975aedb868` |
| V4 fixture file SHA-256 | `06c7df08c4c9e97714c9d33c024e0464657f0173aed7ceda3a3e28f0881ea9fa` |
| `terms.json.gz` file SHA-256 | `88cbb6c4f17eeda2adad11bb5e18d5c845091aa67bee65eb0a5c608fee5c0d41` |
| `plans.json.gz` file SHA-256 | `5720f60af256349902b97e507ba44432ba9906cdecb89ad3e20cf00aa854b62e` |

Object digests use the harness's canonical JSON digest, rather than hashing
the pretty-printed JSON bytes. The plan prompt/schema binding uses the existing
short digest from `recall_arms`.

A strict replay reproduces this recording only on code whose plan inputs and
reply gate match the recording head. #965 changed the gate, so on main add
`--held-plan`: each recorded plan's text is held fixed while the product
recomputes the inventories and the gate, and the report says so.

For the full model-free replay (about 26 minutes), set `REPORT_OUT` to a
directory outside the repository and run from the worktree:

```sh
PYTHONPATH=src:evals "$PYTHON" -m agentos_eval recall-regression \
  --fixture evals/recall/recall-pairs-v4.json \
  --additions evals/recall/recall-gate-dev-1.json \
  --terms evals/recall/recordings/terms.json.gz \
  --plans evals/recall/recordings/plans.json.gz --out "$REPORT_OUT"
```

Both commands accept `.json` or `.json.gz` inputs, selected by suffix.
The fast model-free `tests/test_921_recall_regression_recordings.py` validates
both complete recordings, bindings, evidence class, tokens, secret/path safety
and gzip headers, then replays only the first three recorded cases through
`replay_case` with the recorded terms. The full command above remains the
complete regression check. See the [harness README](../README-regression.md)
for formats and attribution rules.
