# #849 local embedding compatibility spike

Task A's recommendation below is retained as historical evidence. The final lane
decision, including R1 and its context ablation, is in [README.md](README.md).


## Scope and reproduction

Evidence class: **local operating observation on this Mac**, with **static inspection for CI Linux**. This is a disposable measurement harness, not a retriever implementation or product acceptance test. No Still runtime code, dependency manifest, owner database, or model file was changed. No downloads, commits, or external submissions were performed.

From the scratch worktree, with the supplied model directory and interpreters:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. "$VENV_PYENV" -B -m evals.embedding_spike.compat --model-dir "$MODEL_DIR" --out evals/embedding_spike/results/compat-pyenv-3.12.12.json
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. "$VENV_PBS" -B -m evals.embedding_spike.compat --model-dir "$MODEL_DIR" --out evals/embedding_spike/results/compat-pbs-3.12.13.json
```

The JSON files contain versions, build flags, hashes, source-scan locations, errors, timing samples, ranks, and fallback results. Import also passed under `python -B -S` with site-packages disabled; no spike package was imported. `--quick` was used for an initial validation pass, then replaced by full results.

## Recommendation within this spike

**Storage: plain BLOB + NumPy as the portable baseline.** It works on the owner's actual pyenv build and keeps derived rows accessible without a loadable module. sqlite-vec is a possible optional accelerator on supported builds; it was faster in the contended PBS measurements below, but cannot be required on this pyenv build. A BLOB schema also leaves `vec_distance_cosine` available as an optional scan without introducing a vec0 virtual-table dependency. A 50k full-read scan is demonstrably expensive here; this spike does not establish an interactive 50k latency target or justify silently caching stale eligibility decisions.

**Embedding: Adapt FastEmbed 0.9.1 with `onnx/model.onnx` (fp32) for the next bounded implementation candidate.** It already supplies masked pooling and normalization through its supported custom-model API and matched thin fp32 exactly on the fixture. Its measured dependency increment is modest relative to the model. The thin route is a useful reference/control, but these observations do not establish a contract FastEmbed cannot satisfy or a reliable speed advantage that justifies maintaining replacement pooling/tokenizer glue. Both paths still depend transitively on Hub in these environments.

**Model variants:** `model_O4.onnx` is a strong next validation candidate: it runs on arm64, approximately halves model bytes, and preserved all ranks in the ten-pair fixture. Only the thin route was tested with O4; FastEmbed+O4 and #827 R1 recall remain unobserved. The `avx512_vnni` filename does not mean the int8 graph is unusable on arm64: it ran, but lower-rank ordering changed. Retain fp32 as the measured reference rather than declaring quantized retrieval quality from ten pairs.

**Go for compatibility follow-up; no production-performance or absolute offline-guarantee sign-off.** Both fp32 1,000-passage attempts on PBS exceeded the 210-second whole-worker budget after completing the 100-passage benchmark. That is not a 210-second batch-duration measurement. Exact workload/padding/batch costs, owner-row eligibility, deletion/rebuild behavior, configured-interpreter packaging, native network enforcement and real retrieval quality still need their own bounded evidence. C17 does not require another consent prompt for this owner-authorized local work; these are capability/evidence limits, not new data restrictions.

The locally supplied FastEmbed wheel was independently rehashed: `fastembed-0.9.1-py3-none-any.whl` SHA-256 `95042ac7f685bda6697861066d49f8b119fbde8b75ed5162bbb7e84e2dad0e1d`.

## Measured results

Final artifacts share harness SHA-256 `e0e575daff06190d0736ae6e27ea1b3937002b1f886b9aaf4912aae3cb862ee2`. All durations below are seconds unless labelled ms; MiB = 2^20 bytes.

| Interpreter | Python build | SQLite | Extension API | Harness wall time (s) |
|---|---|---|---|---:|
| pyenv 3.12.12 | main / Mar  3 2026 19:20:19 | 3.54.0 | False | 535.07 |
| PBS 3.12.13 | main / Jul 18 2026 16:55:18 | 3.53.1 | True | 562.63 |

Both runs report `macOS-27.0.1-arm64-arm-64bit`. Installed versions and dependency-set bytes match between the venvs.

| Interpreter / path | Cold load s | Warm reload s | Query median ms | 100 passages s | 1000 passages | RSS MiB* |
|---|---:|---:|---:|---:|---|---:|
| pyenv / thin_fp32 | 16.652 | 14.270 | 132.68 | 45.839 | worker timeout | 709.0 |
| pyenv / fastembed_fp32 | 19.630 | 9.119 | 85.27 | 31.333 | worker timeout | 898.1 |
| pyenv / thin_O4 | 5.539 | 2.485 | 8.78 | not measured | not measured | 847.8 |
| pyenv / thin_qint8 | 5.560 | 3.144 | 8.11 | not measured | not measured | 586.6 |
| PBS / thin_fp32 | 18.574 | 15.127 | 324.07 | 94.618 | worker timeout | 485.2 |
| PBS / fastembed_fp32 | 20.641 | 10.011 | 576.40 | 43.754 | worker timeout | 537.2 |
| PBS / thin_O4 | 10.959 | 9.611 | 226.48 | not measured | not measured | 689.5 |
| PBS / thin_qint8 | 6.487 | 2.771 | 34.93 | not measured | not measured | 593.4 |

*For fp32 paths, RSS is the checkpoint before the timed-out batch. For variant probes it covers the completed worker. These are different workload peaks, not a controlled steady-state comparison. The worker cap is 210 s including loading, reload, fixture and prior batches; no complete 1,000-passage duration or final RSS was observed.

| Interpreter / comparison with thin fp32 | Mean cosine | Minimum cosine | Top-1 rank agreement | Mean Spearman rho |
|---|---:|---:|---:|---:|
| pyenv / fastembed_fp32 | 1.000000000 | 1.000000000 | 1.000 | 1.000000 |
| pyenv / thin_O4 | 0.999999400 | 0.999999188 | 1.000 | 1.000000 |
| pyenv / thin_qint8 | 0.996224194 | 0.993966995 | 1.000 | 0.981818 |
| PBS / fastembed_fp32 | 1.000000000 | 1.000000000 | 1.000 | 1.000000 |
| PBS / thin_O4 | 0.999999400 | 0.999999188 | 1.000 | 1.000000 |
| PBS / thin_qint8 | 0.996224194 | 0.993966995 | 1.000 | 0.981818 |

Every path/variant retrieved the intended passage first for 9/10 toy pairs. This fixture does not validate a recall claim.

| Interpreter | N | NumPy incl. SQLite read ms | Read + materialize ms | BLOB cosine scan ms | vec0 KNN ms |
|---|---:|---:|---:|---:|---:|
| pyenv | 1,000 | 1.67 | 1.39 | unavailable | unavailable |
| pyenv | 10,000 | 1428.18 | 1425.10 | unavailable | unavailable |
| pyenv | 50,000 | 6069.12 | 5896.80 | unavailable | unavailable |
| PBS | 1,000 | 9.86 | 9.73 | 5.36 | 1.68 |
| PBS | 10,000 | 256.63 | 251.89 | 134.19 | 42.39 |
| PBS | 50,000 | 2957.24 | 2367.34 | 989.85 | 913.04 |

All observed methods returned the same ordered top 20 at each N. Separate column medians do not add; raw five-sample timings are in JSON. Fifty thousand vector payloads occupy 76,800,000 bytes before SQLite overhead.

Both final runs recorded zero Python socket attempts. Variant worker stderr was empty, and no stderr was captured before the fp32 worker timeouts with `ORT_DISABLE_TELEMETRY=1`. For timed-out workers, attempt counts/RSS are checkpoint observations; they are not an OS network trace or complete post-checkpoint audit. Native offline enforcement remains unproven as described above.

## Storage findings

On PBS, sqlite-vec reported `v0.1.9`. Loading used `enable_load_extension(True)` → `load_extension(sqlite_vec.loadable_path())` → `enable_load_extension(False)` in `finally`. A second load raised `OperationalError: not authorized`. The actually loaded `vec0.dylib` SHA-256 was `193e480c50b59a55977d166f4aaf0e1bc8832d6963516e5950f39e4d2ce0b793`. Pyenv lacks the extension-loading API; no extension execution is claimed there.

| Plain connection, no extension | BLOB layout | vec0 layout (created on PBS) |
|---|---|---|
| Canonical memories/messages INSERT, SELECT, DELETE | All succeeded | All succeeded without coupling triggers |
| Vector INSERT, SELECT, DELETE | All succeeded | All failed: `no such module: vec0` |
| VACUUM | Succeeded | **Succeeded** |
| Canonical DELETE with trigger deleting a vec0 row | Not needed | Failed: `no such module: vec0` |

The surprise is the distinction: the presence of vec0 did **not** make unrelated canonical operations or VACUUM fail. Touching it, directly or through the demonstrated trigger, did. Backup/export of the real Still store was not exercised. It would be incorrect to claim this probe proved all portable exports fail.

Fallback used a real temporary FTS5 table, producing `[2, 1]`. Both simulated absent `onnxruntime` and an actually absent model directory returned exactly `[2, 1]`, mode `fts5`. The available-vector RRF example returned `[2, 1, 3]`, mode `fts5+vector`, with k=60. This demonstrates a function-level fallback, not integration into `QuickStore`.

## Method and boundaries

Each embedding path starts in a fresh subprocess. Cold load includes package imports and model construction, but does not flush the OS file cache. Warm reload is another constructor in that process after deleting the first model; single-query timing is the median of five calls. CPUExecutionProvider only, two intra/inter-op threads, batch size 16. Peak RSS is `resource.getrusage(RUSAGE_SELF)` high-water memory, including reload; it is not steady-state model memory. For timed-out workers it is the last completed checkpoint, not the final peak during the interrupted batch. Batch timings are single measurements, not medians.

The 100/1000-passage corpus is synthetic KO/EN memory-like text, 30–200 characters before the prefix. The explicit ten query/passage pairs include three cross-language pairs. Both paths use `query: ` and `passage: ` prefixes, masked mean pooling, L2 normalization and 512-token truncation. Long-input tokenization is checked; long-input inference is not benchmarked. Pair ranking is a small smoke test, not #827 R1 recall or a quality evaluation.

Search uses seed 849, normalized float32 vectors, dimension 384, top 20, five calls per method. It includes the SQLite SELECT, BLOB materialization, dot product, and top-k selection in the NumPy timing. Tables are on temporary disk; OS/SQLite caches are not flushed. vec0 uses cosine distance explicitly. Insertion/index construction is excluded. No eligibility filter, owner-state integration, write concurrency, migration, or restart lifecycle is implemented. This is a shared Mac with test activity, not an isolated performance lab.


Benchmark caveat: the initial full pyenv pass hit the 210-second worker cap for both fp32 paths and discarded their partial results. The harness was corrected to checkpoint completed stages to its parent before the expensive batches. The final PBS run and corrected pyenv rerun partly overlapped to bound further elapsed time. This adds CPU/memory contention: use these costs as observed local magnitudes and timeout evidence, not an isolated thin-versus-FastEmbed speed comparison. The initial quick pass plus retries and repository tests exceeded the requested aggregate 15-minute target; each harness run remained bounded by per-worker timeouts. No timed-out 1,000-passage cost is extrapolated from a smaller batch.

## Offline behavior and source inspection

Before imports, the recorded runs set `HF_HUB_OFFLINE=1`, `HF_HUB_DISABLE_TELEMETRY=1`, `HF_HUB_DISABLE_IMPLICIT_TOKEN=1`, and `ORT_DISABLE_TELEMETRY=1`. They also call ORT's `disable_telemetry_events()`. Python socket `connect`, `create_connection` and `getaddrinfo` are patched to record and raise for the parent and every worker. Model load, embedding and local search run within the guards. FastEmbed uses a fresh temporary cache and `local_files_only=True` with the supplied local path.

The initial quick pass, before adding the ORT environment switch, emitted native `telemetry_1ds.cc` device-ID persistence and Apple NetworkStorageDB warnings despite the API disable call. Static byte/string inspection of the installed `libonnxruntime.1.31.1.dylib` found `ORT_DISABLE_TELEMETRY` and `https://mobile.events.data.microsoft.com/OneCollector/1.0`. This exposed native initialization that the Python socket guard cannot observe. An attempted extra `sandbox-exec` deny-network wrapper was refused by the existing sandbox (`sandbox_apply: Operation not permitted`). No successful network transmission was observed, and no packet capture or native socket interception was available. **Zero Python socket attempts is not a proof of zero native networking.**

The JSON source scan records file, enclosing function, line and matching text. It is a grep with AST attribution, not a transitive reachability proof. Manual inspection of the used paths:

| File:function | Finding |
|---|---|
| `fastembed/common/model_management.py:ModelManagement.download_model` | Returns `Path(specific_model_path)` before HF/URL download branches. A source identifier is nevertheless required by `ModelSource` during registration; the harness supplies `intfloat/multilingual-e5-small`. |
| `fastembed/common/model_management.py:download_files_from_huggingface`, other download helpers | HF `snapshot_download` and `requests.get` exist in the package; the specific-path branch bypasses them. |
| `fastembed/common/preprocessor_utils.py:load_tokenizer` | Reads local JSON files and uses `Tokenizer.from_file`. |
| `fastembed/common/onnx_model.py:OnnxModel._load_onnx_model` | Constructs a local ORT session; explicit CPU providers used here. |
| `huggingface_hub/utils/_telemetry.py:send_telemetry` | Returns before starting a telemetry thread when offline or telemetry disabled; `_send_telemetry_in_thread` otherwise sends a request. |
| `tokenizers` Python sources | Used path is the native `Tokenizer.from_file` binding. Remote pretrained/download helpers are not called; Python-source inspection cannot establish all Rust behavior. |
| `onnxruntime/__init__.py` and `capi/onnxruntime_inference_collection.py:InferenceSession` | Python exposes native telemetry enable/disable APIs and session loading. Native telemetry initialization is outside the Python scan; see the quick-pass finding above. |

## Artifact identity and installed footprint

Model: `intfloat/multilingual-e5-small`, pinned commit `614241f622f53c4eeff9890bdc4f31cfecc418b3`. Model SHA-256 values were computed from the files read for these runs.

| Model file | Bytes | SHA-256 |
|---|---:|---|
| `onnx/model.onnx` | 470,268,510 | `ca456c06b3a9505ddfd9131408916dd79290368331e7d76bb621f1cba6bc8665` |
| `onnx/model_O4.onnx` | 235,052,531 | `4654c156f3e4171abc9c716cdb771bf9116455d15ac1aab364aeeede0e3205b0` |
| `onnx/model_qint8_avx512_vnni.onnx` | 118,346,824 | `dd476dd0c2514e9b9be83aeb3853fac0763e0bdf4a71645407587d77c48a2d88` |
| `tokenizer.json` | 17,082,730 | `0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39` |

Installed logical size means the sum of RECORD-listed files inside site-packages, including metadata, not compressed wheels or allocated filesystem blocks. Both sets below are dependency closures in the supplied **full environment**.

| Distribution | Version | Bytes (pyenv) |
|---|---|---:|
| anyio | 4.15.1 | 539,391 |
| certifi | 2026.10.23 | 244,811 |
| charset-normalizer | 3.5.2 | 1,125,957 |
| click | 8.5.0 | 447,891 |
| fastembed | 0.9.1 | 478,920 |
| filelock | 4.2.5 | 584,078 |
| flatbuffers | 25.12.19 | 88,178 |
| fsspec | 2026.9.0 | 810,762 |
| h11 | 0.16.0 | 104,105 |
| hf-xet | 1.8.0 | 7,798,696 |
| httpcore | 1.0.9 | 288,194 |
| httpx | 0.28.1 | 295,551 |
| huggingface-hub | 1.33.0 | 3,291,542 |
| idna | 3.20 | 342,316 |
| loguru | 0.7.3 | 225,888 |
| mmh3 | 5.3.1 | 212,169 |
| numpy | 2.5.4 | 20,598,241 |
| onnxruntime | 1.31.1 | 78,012,619 |
| packaging | 26.3 | 499,357 |
| pillow | 13.0.0 | 11,967,486 |
| protobuf | 7.36.2 | 1,662,142 |
| py-rust-stemmers | 0.1.8 | 688,398 |
| pyyaml | 6.0.3 | 606,237 |
| requests | 2.34.2 | 234,577 |
| sqlite-vec | 0.1.9 | 164,772 |
| tokenizers | 0.23.3 | 9,991,742 |
| tqdm | 4.70.1 | 233,724 |
| typing-extensions | 4.16.0 | 182,965 |
| urllib3 | 2.8.0 | 450,637 |

| Installed package set | Bytes (pyenv) | MiB |
|---|---:|---:|
| thin_installed_closure | 126,787,314 | 120.91 |
| full_installed_closure | 142,171,346 | 135.59 |

The separate supplied `thin.txt` was **not installed**. It resolves huggingface-hub 2.4.0 with httpx2/httpcore2/truststore; the measured full environment has huggingface-hub 1.33.0 with httpx/httpcore. Therefore the thin figure is the measured installed closure if FastEmbed is omitted, not a measurement of `thin.txt`. The thin dependency tree still includes Hub through Tokenizers. Per-directory sizes, all model/config digests, and PBS sizes are in JSON. Supplied wheel hashes in the brief/lock files are provenance inputs, distinct from the model and loaded vec0 binary hashes computed here.

## Reuse review

**Adapt** the installed official ONNX Runtime `InferenceSession` and Tokenizers `Tokenizer.from_file` APIs for the thin comparison; the harness only supplies the model-card pooling/prefix recipe. **Adopt** stdlib SQLite BLOB storage and NumPy dot product/top-k for the portable storage candidate. Compare the maintained external alternatives actually supplied: sqlite-vec 0.1.9 and Qdrant FastEmbed 0.9.1 (`TextEmbedding.add_custom_model`, `specific_model_path`). No external candidate was downloaded or replaced with a new framework.

External inspection preceded internal inspection: installed FastEmbed `text/text_embedding.py`, `text/custom_text_embedding.py`, `common/preprocessor_utils.py`, `common/model_management.py`, and `common/onnx_model.py` establish supported registration, pooling, padding, session, and local-path APIs. `rg` then located `QuickStore.db` and `search_memories` in `src/personal_agent/quickstart_store.py`, plus `_portable_db` in `src/personal_agent/portable_state.py`. Existing connections use stdlib SQLite, and portable export uses backup and VACUUM without loading extensions. The harness avoids duplicating those components and never changes canonical Memory authority. This removes a potential retrieval/portability blocker for the owner's AI; it does not script an owner task.

The supplied offline brief records release dates of 2026-03-31 (sqlite-vec), 2026-10-24 (FastEmbed), and 2026-10-26 (ORT); these were not independently refreshed. Supplied licence declarations are MIT OR Apache-2.0 (sqlite-vec), Apache-2.0 (FastEmbed/Tokenizers), MIT (ORT/model), and BSD-3-Clause (NumPy): permissive candidates for this AGPL-3.0-only repository. Installed distribution metadata is retained in JSON. This is not a full transitive licence/security audit or production adoption approval. Native wheels/model graphs still need pinned provenance, controlled updates, offline enforcement and regression checks; hashes establish identity, not safety. Adding either library grants no owner-data authority.

## CI Linux: static only

The supplied actions/python-versions artifact is CPython 3.12.16 for linux-24.04-x64 (release `3.12.16-40260402378`). Independently rehashed `py.tgz`: `dec61ed20cd3e71143f1642ef9966945dea8ae4f3e96b306205a710da4f2191a`. Its `_sysconfigdata__linux_x86_64-linux-gnu.py` includes `--enable-loadable-sqlite-extensions`; `_sqlite3.cpython-312-x86_64-linux-gnu.so` contains `enable_load_extension` and `sqlite3_enable_load_extension`. This supports API availability, not successful loading of the Linux sqlite-vec wheel, Linux inference, native offline behavior, or Linux latency. None of those was executed.

## Validation

All commands used the supplied pyenv `.venv/bin/python`, `PYTHONDONTWRITEBYTECODE=1`, `PYTHONPATH=src`, `-B`, and `pytest -q -p no:cacheprovider`.

- Requested globs: `tests/test_verify_src_layout*.py tests/test_*governance*.py tests/test_*doc*.py -x`. Interrupted after 192.92 s in the existing regex-based `scripts/ci/test_scope.py:text_tests` selection scan: 20 passed, 1 skipped, 5 subtests passed. No assertion failure was observed before interruption.
- The same globs were rerun with `--deselect=tests/test_governance_contract.py::test_the_test_scope_is_full_unless_every_changed_path_is_text -o faulthandler_timeout=60 -x`; final result is recorded below. The slow test is not claimed to pass.
- `rg` over tests for `evals/` and `evals` found ten related test modules. The first group stopped at `tests/test_eval_fake_telegram.py::StandIn::test_another_token_is_refused`: sandbox denied binding a localhost server (`PermissionError: Operation not permitted`), after 5 passes (19.10 s).
- The next group stopped at `tests/test_eval_loop.py::SandboxTest::test_pool_uses_locked_stable_slots_and_a_seed_bound_judgment_cache`: no loopback port could be bound in this sandbox, after 65 passes and 30 subtest passes (35.87 s). This run included `test_eval_judgments.py` and `test_eval_loop.py`.
- Remaining `test_eval_world_appworld.py test_eval_world_wiring.py test_fresh_delivery_plan.py test_line_traces.py test_owner_usefulness_spec.py test_recall_pairs.py`: **127 passed, 3 skipped, 76 subtests passed**, 282.11 s. `test_dependency_update_contract.py` passed in the first group.
- `python -B -S` imported `evals.embedding_spike.compat`, checked that no NumPy/ORT/Tokenizers/FastEmbed/sqlite-vec module was loaded, and asserted unchanged FTS ordering: passed.

No test was edited, and no sandbox restriction was disabled. These results are not a claim that the full repository suite or exact-head CI passes.


The requested-glob rerun completed: **57 passed, 1 skipped, 1 deselected, 5 subtests passed**, 507.24 s. The diagnostic timeout stack during that run was in an existing doctor subprocess probe; it subsequently completed successfully.

An additional model-free check injected `subprocess.TimeoutExpired` with a worker checkpoint: the completed 100-passage cost was retained, the 1,000-passage field recorded `TimeoutExpired`, and return code remained unknown (`null`). Passed. This is unit/fixture evidence for harness error handling, not a completed large-batch observation.

## Sanity probe outside the sandbox (Claude, 2026-11-14 22:31)

Evidence class: **local operating observation reported by Claude**, on the same
Mac, PBS 3.12.13 venv, thin onnxruntime+tokenizers path, with
`ORT_DISABLE_TELEMETRY=1`. Load average was 72–84 while other lanes ran. These
are contended magnitudes only, not performance claims or an uncontended rerun.

| model file | load s | first query ms | warm query ms | 100 passages s (batch 16) | 1000 passages s (batch 32) | 50k numpy search incl. SQLite read ms | peak RSS MiB |
|---|---:|---:|---:|---:|---:|---:|---:|
| onnx/model.onnx | 14.1 | 520 | 244 | 13.04 | 183.04 | 933 | 827 |
| onnx/model_O4.onnx | 8.05 | 275 | 138 | 6.60 | 105.12 | 109 | 803 |
| onnx/model_qint8_avx512_vnni.onnx | 2.97 | 70 | 7 | 1.93 | 120.23 | 1778 | 607 |

Network observation: the same probe ran twice, with and without
`ORT_DISABLE_TELEMETRY=1`. `lsof -nP -a -i -p <pid>` was polled every 0.2 s for
the whole run; no socket was observed in either mode. Static inspection with
`strings libonnxruntime.1.31.1.dylib` found
`https://mobile.events.data.microsoft.com/OneCollector/1.0` and
`ORT_DISABLE_TELEMETRY`. This is not an OS-level packet trace; a short polling
gap could miss a very short-lived connection. These supplied observations do
not replace or alter the earlier JSON measurements.
