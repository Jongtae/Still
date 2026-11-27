# Recorded recall inputs

[`terms.json.gz`](terms.json.gz) (`recall-terms/1`) and
[`plans.json.gz`](plans.json.gz) (`recall-plan/2`) are the #921 regression
inputs since #919. Claude recorded them under the sweep lock after the owner's
decision and setup ([#921 comment](https://github.com/Jongtae/Still/issues/921#issuecomment-6977447172)).

**What is recorded.**
- The planner prompt with #919's `recall_query` line, #1172's line on briefing a target-type request to reach it, #1183's `acknowledgement` field, and #1269's lines on a scheduled run's time.
- The store is the composed synthetic fixture:
  - v4's 1,078 rows;
  - 28 dev-addition rows;
  - the 24 rows of the fresh held-out layer [`recall-heldout-919-1.json`](../recall-heldout-919-1.json).
- The layer's 24 held-out cases replace v4's spent held-out cases (#827). Every dev case is unchanged.
- Dev cases and the 4 additions run once; the fresh held-out cases run three times. Workers are fixtures.

**Route and calls.**
- Route: the Codex subscription CLI (`live-route:subscription_cli`), with `gpt-5.6-terra` requested. The observed model is **not reported**.
- Tokens are **not observable**. There was no judge or embedding call.
- #1269 re-recorded the plans at code head `639878dd` after its planner prompt change, in one session with `--splits all` and a cap of 118 (`--max-calls 118`).
- Calls: 112 plan calls in all.
  - 112 plans were admitted and the recording is complete (no cap reached, no failure).
  - 0 qualification calls (cached) and 0 terms calls in these sessions.
- Terms: the committed `terms.json.gz` was reused unchanged (`--terms`). It was made for #919: the earlier composition's recording (`3af70b56…`, PR-8's) supplies the terms of the 1,106 rows both compositions share, and the product's terms pass keyed only the 24 new rows, in 2 ok batches (`--terms-base`).
- Result rows: 1,105 keyed, 4 cleared, 21 never eligible (superseded or expired traps).
- The measured results, including the old-prompt baseline on the same fresh cases, are on [#921](https://github.com/Jongtae/Still/issues/921).

**Commands.** In the commands below, `PYTHON` is the project's venv interpreter. `OUT` is a fresh output directory outside the repository; each resume used `--resume "$OUT/plans.json"` and the remaining cap.

```sh
PYTHONPATH=src:evals "$PYTHON" -m agentos_eval recall-record \
  --seed synthetic-terra --requalify --max-calls 25 --splits heldout \
  --heldout-layer evals/recall/recall-heldout-919-1.json --heldout-runs 3 \
  --terms "$TERMS" --out "$OUT"
```

- `TERMS` is `evals/recall/recordings/terms.json.gz` (for #919 it was the recording made with `--terms-base` on that file, which keyed the new rows).
- The last two sessions resumed with `--splits all` and added the dev split.

**Files.** The gzip files were made with `gzip -9 -n`: mtime 0, no stored filename. Decompression preserves the recorder's JSON bytes exactly, so do not regenerate or re-serialize them. The recorder writes plain JSON outside the repository.

Do not copy seed databases, credentials, owner state, raw plan-input text or model logs here. Reports stay outside the repository.

| Binding | Digest |
| --- | --- |
| Composed fixture SHA-256 | `87904d840cf5a3ba4915ea46ac86ba437a12a3d0a2d8ea1da7551dd8c8ac4d63` |
| Terms object SHA-256 (`plans.terms_digest`) | `b59d8586eae8d5e46fdd85299b3164a1d74656f6b884b99edb2d489fbb67cee0` |
| Reused terms object SHA-256 (`terms.terms_base`) | `3af70b562107789501ef3280d6d909c7989a290ab14ce0c224c692d565150e9d` |
| Terms filter source SHA-256 | `56bed506c794562a81328d5441459f2a7117fb25ed12978959e2aac9f7275a04` |
| Terms prompt/schema SHA-256 | `a01ca10a442aecab73c007bdc9b45aedf4957ed3a506296fe79c7282c1372ed1` |
| Plan prompt/schema binding | `0f09728110b1b5fc` |
| Held-out layer file SHA-256 | `8566ba9b8c7dde4c41ef576d81faf522d5f362078ab74fed10efbdbbda97fe63` |
| V4 fixture file SHA-256 | `06c7df08c4c9e97714c9d33c024e0464657f0173aed7ceda3a3e28f0881ea9fa` |
| `terms.json.gz` file SHA-256 | `0dfb6bf5181b66838133fd2839e69d93738eaff8e1ab19abd0d56ffbbc7acf15` |
| `plans.json.gz` file SHA-256 | `e9c02029d06f92e806d33f69ddf6d0178647e11b5804f9c04d213cc22d23b530` |

Object digests use the harness's canonical JSON digest, not a hash of the pretty-printed JSON bytes. The plan prompt/schema binding uses the existing short digest from `recall_arms`.

**Replay.**
- A strict replay reproduces this recording only on code whose plan inputs and reply gate match the recording.
- After a later change to either, add `--held-plan`. Each recorded plan's text is then held fixed while the product recomputes the inventories and the gate, and the report says so.
- For the full model-free replay (about 30 minutes), set `REPORT_OUT` to a directory outside the repository and run from the worktree:

```sh
PYTHONPATH=src:evals "$PYTHON" -m agentos_eval recall-regression \
  --fixture evals/recall/recall-pairs-v4.json \
  --additions evals/recall/recall-gate-dev-1.json \
  --heldout-layer evals/recall/recall-heldout-919-1.json \
  --terms evals/recall/recordings/terms.json.gz \
  --plans evals/recall/recordings/plans.json.gz --out "$REPORT_OUT"
```

Both commands accept `.json` or `.json.gz` inputs, selected by suffix.

The fast model-free `tests/test_921_recall_regression_recordings.py` validates:
- both complete recordings and their bindings;
- the evidence class and tokens;
- secret and path safety;
- the gzip headers.

It then replays only the first three recorded cases through `replay_case` with the recorded terms. The full command above remains the complete regression check. See the [harness README](../README-regression.md) for formats and attribution rules.

**History.** PR-8 (#960) recorded the previous inputs at `2474d74d`:
- 204 calls on the same route, with no judge;
- the v4 composition, with v4's own held-out cases;
- results in [this #921 comment](https://github.com/Jongtae/Still/issues/921#issuecomment-6960844558).

#919's prompt change makes those plans fail the plan prompt/schema binding. They stay in git history as evidence for that head. #919's own recording (114 plan calls at `305c8bff`, binding `abfebe76cba19942`), #1172's (112 plan calls at `8923cd31`, binding `2bdad1366ea76714`) and #1183's (112 plan calls at `884c2f9b`, binding `f8622073b284be5d`) likewise stay in git history; #1172's, #1183's and then #1269's prompt changes replaced them.
