# #849 local embedding index: decision record

## What was asked

**Requirement (supplied issue-context summary):** #849 investigates #403 step 2,
a local, rebuildable meaning index beside canonical `memories` and `messages`.
The supplied acceptance text is:

> Check extension loading, pinned versions and digests, offline run after a one-time download, Korean/English recall on the #827 R1 fixture through the retriever interface, licence/maintenance/size/time, and fallback to FTS5 when extension or model is missing.
> Output: Adopt/Adapt/Build decision with evidence, go/no-go, implementation contract.

**Scope:** evidence only; no external vector database, provider embedding API,
Memory write-rule change, or project dependency. The owner's chosen AI still
does the work; a future index would remove a recall blocker, not script a task.

## Results in one table

| Acceptance item | Result | Evidence class |
|---|---|---|
| Extension loading on owner's macOS build | Live pyenv CPython 3.12.12 / SQLite 3.54.0 lacks `enable_load_extension`. PBS 3.12.13 / SQLite 3.53.1 loaded sqlite-vec 0.1.9, then rejected another load after disabling it. | Local operating observation, [COMPAT](COMPAT.md) / `compat-*.json` |
| Extension loading on CI | Supplied Linux CPython 3.12.16 artifact has build flag and symbols; actual Linux extension loading/inference was not run. | Static inspection, COMPAT |
| Pinned versions and digests | ORT 1.31.1, tokenizers 0.23.3, numpy 2.5.4; compared fastembed 0.9.1 and sqlite-vec 0.1.9. Model commit `614241f622f53c4eeff9890bdc4f31cfecc418b3`; model/tokenizer and loaded vec0 digests computed; wheel/lock provenance supplied in context. | Local operating observation + supplied static provenance, COMPAT and [R1](R1.md) |
| Offline run | Local files loaded with Python socket guards; zero Python connection attempts. Claude's later native socket polling saw none in either telemetry mode. Neither proves absence of all native traffic. | Local operating observation; static endpoint inspection, COMPAT |
| KO/EN recall on R1 | Hybrid held-out recall 5/6 vs baseline 4/6, zero traps; lexical context also gets 5/6 and fixes the same case. No embedding arm meets the preference rule against lexical context. | Fixture; context attribution is post-hoc, R1 |
| Licence | Declared MIT (model/ORT), Apache-2.0 (fastembed/tokenizers), MIT OR Apache-2.0 (sqlite-vec), permissive NumPy metadata. Compatible candidates for AGPL-3.0-only; full closure audit remains owed. | Supplied declarations + installed metadata static inspection, COMPAT/R1 |
| Maintenance | Supplied releases: sqlite-vec 2026-03-31, fastembed 2026-10-24, ORT 2026-10-26; model revision dated 2026-04-02. Not refreshed online. | Supplied static provenance, COMPAT/R1 |
| Size and time | ORT 78,012,619 installed bytes; measured thin closure 120.91 MiB. O4 model 235,052,531 bytes. Later contended O4/fp32 1,000-passage runs took 105.12/183.04 s. No uncontended budget result. | Local operating observation, COMPAT including Claude's sanity probe |
| FTS5 fallback | Missing model and simulated missing extra returned the same FTS5 IDs, visibly `fts5`; available-vector example used `fts5+vector`. Missing extension on pyenv permits BLOB search. No product fallback integration. | Fixture + local operating observation, COMPAT |

## Existing solutions review (Adopt / Adapt / Build) for a future implementation

**Design decision, informed by static inspection and local operating observation:**

- **Storage — Adopt** stdlib SQLite BLOB plus NumPy brute-force cosine. Reject
  sqlite-vec as a requirement: the owner's live interpreter cannot load it. A
  `vec0` table cannot be touched without its module; a demonstrated canonical
  DELETE trigger touching it failed. Plain connections such as `portable_state`
  would fail if they touched it. Unrelated operations and VACUUM succeeded in
  the probe; failure of every export is not established. BLOB search measured
  1k at 1.67–9.86 ms; later 50k observations included 109/933 ms (≲1 s), but
  also 1778 ms and earlier 2957.24–6069.12 ms. Contention prevents a latency bound.
- **Embedding — Adapt** official `onnxruntime.InferenceSession` and HF
  `tokenizers.Tokenizer.from_file` behind a thin Still adapter. Follow the model
  card: `query: ` / `passage: ` prefixes, masked mean pooling, L2 normalization,
  512-token truncation. Reject fastembed 0.9.1 for this candidate: it has no
  built-in multilingual-e5-small entry, so only its custom-model escape hatch
  would be used. It adds about 14.7 MiB of measured closure, including requests,
  pillow, loguru, mmh3 and py-rust-stemmers, and ships reachable HF snapshot and
  Qdrant GCS fallback download helpers that Still must never run. Its measured
  fp32 output was identical to the thin path (cosine 1.0). **Task A recommended
  fastembed** to reuse its pooling rather than maintain glue; this lane instead
  selects the official APIs plus the model-card recipe because no built-in
  model path is gained and the extra download surface must be bypassed anyway.
  No measured speed advantage is claimed. The thin closure still includes Hub
  transitively through tokenizers. PyTorch/Transformers and SentenceTransformers
  are model-card alternatives, but do not supply this bounded direct ONNX path.
- **Model — Adopt** `intfloat/multilingual-e5-small` at the commit above. Choose
  `onnx/model_O4.onnx` (235 MB) over fp32 (470 MB): mean cosine 0.9999994 to fp32,
  unchanged toy ranks, and R1 per-case results identical without retuning.
  qint8 (118 MB) remains an option; it showed small toy rank changes despite
  identical R1 counts. Neither small fixture establishes general equivalence.

**Static inspection / reuse evidence:** external-first inputs are the supplied
official model card, installed ORT/Tokenizers APIs, and Task A's FastEmbed source
scan. Internal `rg` checks identify `QuickStore.search_memories`,
`MemoryService.search_memories`, `portable_state._portable_db`,
`node_runtime.PINS` / `ensure`, and `recall_pairs.run/score/compare`.
These reuse SQLite, official inference APIs and the existing scorer; no new
database, tokenizer, inference engine or scorer is needed. Overall **Adapt**;
no custom infrastructure Build is justified. Release/licence evidence is in the
table; native wheels/model graphs still require provenance, security and
platform review. Still retains eligibility, owner authority and audit policy.

## R1 result

**Fixture evidence:** `recall-pairs/1`, 25 rows, 14 dev cases, **10 held-out
cases and only 6 needed held-out rows**. Parameters were frozen on dev:
top-1, cosine floor 0.82, content-only passages, context enabled; hybrid uses
RRF k=60 and adds vectors only when lexical results are empty. A pass requires
all needed rows, no traps and no noise. All table trap counts are zero.

| Primary fp32 arm | Dev recall | Dev noise / passes | Held-out recall | Held-out noise / passes |
|---|---:|---:|---:|---:|
| baseline | 6/10 | 2 / 8/14 | 4/6 | 4 / 4/10 |
| vector | 8/10 | 2 / 10/14 | 4/6 | 2 / 6/10 |
| hybrid | 9/10 | 3 / 10/14 | 5/6 | 4 / 5/10 |

**Post-hoc fixture ablation:** same frozen parameters, no new held-out validation.

| Arm | Dev recall; noise; passes | Held-out recall; noise; passes | Held-out fixes vs `lexical_context` |
|---|---|---|---|
| baseline | 6/10; 2; 8/14 | 4/6; 4; 4/10 | None |
| lexical_context | 7/10; 2; 9/14 | 5/6; 4; 5/10 | Reference |
| vector | 8/10; 2; 10/14 | 4/6; 2; 6/10 | `h-correction` |
| hybrid | 9/10; 3; 10/14 | 5/6; 4; 5/10 | None |
| vector-no-context | 7/10; 2; 9/14 | 3/6; 3; 5/10 | `h-correction`; regresses `h-follow` |
| hybrid-no-context | 8/10; 3; 9/14 | 4/6; 5; 4/10 | None; regresses `h-follow` |
| hybrid-over-lexical-context | 9/10; 3; 10/14 | 5/6; 4; 5/10 | None |

**Fixture interpretation:** hybrid meets #827's four-part rule against the
defined context-blind baseline. Its only held-out fix, `h-follow`, is also fixed
by lexical `context + query`. Against `lexical_context`, no embedding arm fixes
a held-out case except `h-correction`: a post-hoc vector-only replacement
precision fix, offset by lost cross-language needed recall. No embedding arm
qualifies against that reference. Embedding-specific needed-recall fixes appear
only on dev (`d-para-focus`, `d-para-posture`); the dev noise regression
`d-expired-date` remains. Held-out paraphrase/cross-language needed similarities
are 0.778956294 / 0.747253537 (about 0.779 / 0.747), below the dev-tuned 0.82
floor and outside top-1. Harm-control noise remains. See R1 for per-kind tables
and paired judgments; this is retrieval evidence, not answer-quality evidence.

## Decision: no-go for the implementation PR now

**Lane decision:** #403 requires step 2 to address a measured miss that phrasing
cannot fix. This held-out evidence does not establish one. The candidate is not
rejected forever; #849 stays open on the go conditions.

**Local operating observation + static inspection:** the cost is a large native
closure (ORT about 78 MB installed, thin closure about 121 MiB), a 235–470 MB
model download, a native telemetry endpoint to disable, and contended embedding
of 1,000 rows taking about 1.7–3 minutes. The measured thin closure comes from
the supplied full environment with fastembed omitted, not an independently
installed thin lock. **Future review requirement:** runtime adoption needs
escalated supply-chain review. This evidence-only spike adds no dependency or
runtime authority and does not trigger that review itself.

## Go conditions

**Future acceptance, not observed completion:**

1. Lane E (#848) supplies a larger R1 fixture version with fresh held-out
   paraphrase and cross-language cases. An embedding arm frozen on dev must fix
   at least one held-out case against **both** `baseline` and `lexical_context`,
   with zero traps; retain #827's other no-regression conditions.
2. An uncontended measurement on the owner's Mac meets the cost budgets in the
   implementation contract below. Current contended runs do not satisfy it.

## Cheaper finding handed off (not implemented here)

**Post-hoc fixture observation + static inspection:** lexical search reading the
preceding owner turn fixes both short follow-ups (`d-follow`, `h-follow`) without
a dependency. Today `QuickStoreService.owner_words_block` searches the raw
request only. Passing context is a candidate C16 blocker fix for the R1 runner
lane or a new issue. This is a suggestion, not an implementation decision or a
claim that memory-fixture results establish conversation-retrieval behavior.

## Implementation contract (applies only after go)

**Design requirements only; none is implemented or accepted by this record:**

- Use a thin adapter module and
  `<store>/tools/embedding/<repo>@<commit>/`, following `node_runtime`-style
  `PINS` / `ensure` and digest re-checks. Background embedding fills missing
  rows. A content/model digest mismatch means re-embed, never use the vector.
- Use plain tables
  `memory_vectors(memory_id TEXT PRIMARY KEY, content_digest, model_id, vector BLOB)`,
  `message_vectors(message_id INTEGER PRIMARY KEY, content_digest, model_id, vector BLOB)`,
  and `embedding_index_meta`. Key memories by `memories.id`, never rowid.
- Triggers delete vectors on state change away from current, DELETE, message
  blanking, and insertion into `forgotten_messages`. Undo re-queues the row.
  Re-check eligibility at every use by joining canonical rows: `owner_key`,
  `state='current'`, `valid_until`, `forgotten_messages`, `OWNER_WORDS_SQL` and
  the recent window as applicable. Cached membership never grants eligibility.
- Use RRF k=60 with a dev-frozen similarity floor. Expose `search_mode` as
  `fts5+vector` or `fts5`. Missing extra, model or valid digest runs visibly on
  FTS5 alone; no tool is hidden.
- Before imports set `HF_HUB_OFFLINE`, `HF_HUB_DISABLE_TELEMETRY`,
  `HF_HUB_DISABLE_IMPLICIT_TOKEN`, `ORT_DISABLE_TELEMETRY` to `1`; call
  `disable_telemetry_events()`. Only `CPUExecutionProvider`; no HF token is
  ever sent. Separate verified acquisition from inference; never invoke runtime
  download helpers.
- `portable_state` drops vector tables before VACUUM; restore rebuilds them.
  Keep owner originals and canonical state independent of replaceable indexes.
- Exact `==` pins belong in an optional extra: `onnxruntime==1.31.1`,
  `tokenizers==0.23.3`, `numpy==2.5.4`. `uv tool install` and Homebrew
  `pip install` paths do not check lock hashes; dependency pins alone do not
  establish wheel integrity. Define update, failure and rollback behavior and
  validate the supported interpreter/CI paths before adoption.
- Required negative/integration tests: socket guard; digest mismatch;
  unauthorized `load_extension` if ever used; forget/delete/undo/supersede
  vector removal and re-queue; traps never returned; rebuild equality;
  FTS5-only suite; export without vectors; audit naming the vector arm.
- **Cost budgets remain unset in the supplied context.** Before go, record
  explicit maximum cold-load/warm-query time, 1k-row embedding time, 50k-row
  search time, peak RSS and installed/model disk size. Accept only uncontended
  owner-Mac measurements within those limits. Do not turn contended samples or
  the spike's worker timeout into an approved performance budget.

## Reproduction

**Procedure, not a new run:** set `$MODEL_DIR` to the verified local snapshot,
`$VENV_PYENV` / `$VENV_PBS` to the supplied interpreter executables, and
`$SCRATCH` to disposable output space. `<site-packages>` denotes sanitized
installed-file locations. Optional dependencies are imported lazily.

```sh
PYTHONPATH=src:. "$VENV_PYENV" -B -S -c 'import evals.embedding_spike.compat, evals.embedding_spike.r1_arm, evals.embedding_spike.embed'
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. "$VENV_PYENV" -B -m evals.embedding_spike.compat --model-dir "$MODEL_DIR" --out "$SCRATCH/compat-pyenv.json"
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. "$VENV_PBS" -B -m evals.embedding_spike.compat --model-dir "$MODEL_DIR" --out "$SCRATCH/compat-pbs.json"
ORT_DISABLE_TELEMETRY=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. "$VENV_PYENV" -B -m evals.embedding_spike.r1_arm --model-dir "$MODEL_DIR" --out-dir "$SCRATCH/r1"
ORT_DISABLE_TELEMETRY=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. "$VENV_PYENV" -B -m evals.embedding_spike.r1_arm --model-dir "$MODEL_DIR" --out-dir "$SCRATCH/r1" --ablation
```

Use `--dev-sweep` only for dev tuning and `--model-file onnx/model_O4.onnx` or
`--model-file onnx/model_qint8_avx512_vnni.onnx` for frozen secondary runs.

**Supplied acquisition provenance:** wheels came from PyPI, resolved with
`--exclude-newer 2026-10-31`; model files came from the official HF repository
`intfloat/multilingual-e5-small` at the pinned commit, via `huggingface.co`
resolve/API cache and `us.aws.cdn.hf.co` LFS delivery. No download occurred in
this task. COMPAT/R1 and JSON retain exact versions, numbers and digests.
Published paths are placeholders; recorded artifact checksums describe the
original runs, before path sanitization, rather than hashes of rewritten JSON.
