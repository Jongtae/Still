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

## 2026-11-15 decision (R1 v2)

**Decision from fixture evidence: no-go for embedding implementation; close
#849 as a completed investigation with the reopen conditions below.** This
supersedes the 2026-11-14 go conditions and its instruction to keep #849 open.
It does not claim that the issue has been closed remotely, implement an index,
or activate #403 step 2. The earlier measurements remain historical evidence.

### Why no-go

**Fixture evidence:** the single locked R1 v2 run gives `preferred=false` for
all three frozen arms. Primary `vector` and `hybrid_lexical_context` lose the
`h2-context-lift` lexical control and regress held-out; `hybrid` regresses the
two harm controls `h2-context-aquarium` and `h2-context-inkstone`. All three
recover needed rows on the same held-out paraphrases (`h2-smoke`,
`h2-swimming`, `h2-riddle`) against both lexical references, with zero traps
on both splits. Exact needed recall is 5/21 for vector, 6/21 for each hybrid,
and 3/21 for lexical, lexical-context and runtime. Vector also regresses
`d-dated-current` on dev. Full pairs and costs are in
[R1v2.md](R1v2.md#held-out-result-single-locked-run) and the
[locked measurement](results/r1v2-locked/r1v2-measurement.json).

**Fixture evidence and interpretation:** the ranked-list gain is substantial:
held-out recall@1 is 0.43 for vector and 0.48 for both hybrids, versus 0.14 for
lexical and 0.10 for lexical-context/runtime; recall@5 is 0.67 versus 0.14.
Cross-language recall@1 reaches 0.29 versus zero, but no exact cross-language
case is fixed. These diagnostics precede the frozen cutoff and do not satisfy
the no-regression rule. The three common paraphrase fixes warrant preserving
the evidence, not disregarding the failed gate.

**Inference from 24 held-out fixture cases:** the two harm-control failures
(`h2-context-aquarium`, `h2-context-inkstone`) and hybrid_lexical_context's
noise-only lift failure also appear in model-free lexical-context and the
shipped #860 runtime path. For those failures, the pattern points to added
preceding context rather than an embedding-specific cause; it is not proof of
causality or general safety. **Fixture evidence:** vector's lift loss is an
effect of context combined with top_k=1 cosine admission: it returns only
`n28` and drops needed `n29`. Lexical-context and runtime return `[n28, n29]`;
hybrid_lexical_context returns `[n29, n28]`. They retain the needed row and
add noise; no model-free arm shows vector's needed-row loss. Against either
context baseline, hybrid fixes four cases (the three paraphrases plus the
lexical control) with no exact-case regressions; vector and
hybrid_lexical_context fix three with no exact-case regressions. Vector's lift
comparison is unchanged-fail despite its different returned rows. That does not permit replacing request-only lexical with
runtime after observing held-out. The pre-registered two-baseline rule still
fails; v2 held-out is now spent for embedding selection.

**Design judgment under #403:** step 2 must address a measured miss that
phrasing cannot fix. This run establishes misses of the measured lexical
queries and real embedding gains over them; it does not establish that the
owner's AI cannot recover the same rows with query phrasing. No phrasing arm
was tested in this locked run. A broader claim is not supported.

**Local operating observation + design judgment:** warm query costs are small
(6.9 ms median embedding plus 0.9 ms vector retrieval), but rebuilding 10k rows
takes 406 s, about seven minutes. Peak RSS is 891 MB, the O4 file is 235 MB and
the supplied directory is 863 MB. These lightly contended Mac observations
are not performance guarantees. Combined with the supplied Intel Mac wheel
gap, default ORT telemetry and the future escalated supply-chain review, these
costs are not justified by a candidate that fails its quality gate. No runtime
dependency enters the product lock in this decision.

### C15 Existing Solutions Review

**Design decision: Adapt for any future implementation, still subject to go.**
The boundary is a local, disposable recall index over eligible canonical rows,
not a new Memory store. This review reuses the external API/model-card and
compatibility evidence in [COMPAT.md](COMPAT.md) and [R1.md](R1.md), followed
by repository inspection; it adds no implementation and performs no network
lookup. The owner's chosen AI still performs the owner task; this investigates
a recall blocker rather than scripting requests (C16).

**Supplied external-source refresh, reported by Claude on 2026-11-15:** the
following PyPI JSON, OSV, GitHub and Hugging Face API findings were supplied
with this task. They are not independently refreshed or authenticated by this
offline decision task. “No advisory” is a lookup result, not a security audit.

| Candidate / exact revision | Licence | Maintenance, supply chain and fit in the supplied refresh |
| --- | --- | --- |
| onnxruntime 1.31.1 | MIT | No OSV advisory reported. 1.32.0 released 2026-11-13; not substituted into the measured run. 1.31.1 has macOS arm64 `macosx_14_0`, Linux x86_64 and aarch64 `manylinux_2_28` wheels. Neither 1.31.1 nor 1.32.0 has a macOS x86_64 wheel. Official builds enable telemetry by default. |
| tokenizers 0.23.3 | Apache-2.0 | Latest stable in the refresh; 1.0.0rc4 is available. FastEmbed caps `tokenizers<1.0`. Retain the measured stable version. |
| numpy 2.5.4 | Compound permissive licence | Use the distribution's full licence declarations, not just the simplified historical BSD label. Native closure remains subject to audit. |
| sqlite-vec 0.1.9 | MIT OR Apache-2.0 | No commits reported since 2026-05-18. Optional alternative only; extension availability and portable connections remain blockers to requiring it. |
| fastembed 0.9.1 | Apache-2.0 | No built-in multilingual-e5-small entry found in a partial check; prior spike measured its custom-model route. This is not an exhaustive current integration survey. |
| sentence-transformers 6.3.0 | Apache-2.0 | Requires torch; too heavy for this bounded direct-ONNX candidate. No new compatibility run or full closure audit. |
| intfloat/multilingual-e5-small at `614241f622f53c4eeff9890bdc4f31cfecc418b3` | MIT | Not gated; `main` still at this revision in the supplied HF API refresh. Use pinned `onnx/model_O4.onnx`, not mutable `main`. |

**Supplied licence assessment:** these licences are AGPL-3.0-only compatible.
That assessment does not certify the entire resolved native/transitive closure.
Prior release provenance for FastEmbed 0.9.1 (2026-10-24), ORT 1.31.1
(2026-10-26), sqlite-vec 0.1.9 (2026-03-31) and the model (2026-04-02) remains
in COMPAT/R1; no additional release-activity claim is made for other packages.

**Static inspection + prior local compatibility evidence; decisions:**

- **Adopt** stdlib `sqlite3` plain BLOB tables and NumPy dot product/cosine.
  Keep FTS5 as the available baseline. SQLite/NumPy cover storage and search;
  no bespoke vector database is justified. sqlite-vec cannot be mandatory:
  the observed pyenv interpreter lacks extension loading, and a canonical
  DELETE trigger touching a `vec0` table failed on a plain connection.
- **Adapt** official `onnxruntime.InferenceSession` and
  `tokenizers.Tokenizer.from_file` behind the existing thin model-card adapter.
  Reuse `embed.Embedder`, `MODEL_PINS`, `TOKENIZER_PIN`, `r1_arm._VectorIndex`
  and `_eligible` as experimental evidence. FastEmbed's custom path matched
  fp32 outputs in the earlier probe, but supplied no built-in integration for
  this model and carried HF/GCS download helpers that this offline path must
  bypass. The decision retains the earlier narrow adapter; no performance
  superiority or blanket inability of FastEmbed is claimed. Torch-based
  SentenceTransformers does not fit the bounded inference footprint.
- **Adopt** the pinned model and tokenizer files. COMPAT records O4 SHA-256
  `4654c156f3e4171abc9c716cdb771bf9116455d15ac1aab364aeeede0e3205b0`
  and tokenizer SHA-256
  `0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39`;
  the locked measurement agrees. Hashes identify files, not model safety.
- **Reuse search evidence:** `QuickStore.search_memories` and
  `MemoryService.search_memories` already use SQLite/FTS5;
  `portable_state._portable_db` uses stdlib backup/VACUUM;
  `node_runtime.PINS` / `ensure` provide the existing acquisition pattern.
  `recall_arms.lexical`, `lexical_context`, `runtime`, and
  `recall_report` provide the paired evaluation path. No new tokenizer,
  inference engine, database or evaluation framework is needed. Still-owned
  eligibility and authority stay outside dependencies.

**Platform evidence limits:** actual arm64 Mac operation is recorded; Linux
has only the prior static interpreter inspection plus supplied wheel metadata,
not observed inference. Linux wheels require the stated manylinux baseline;
macOS arm64 wheels require macOS 14 or later. Under this candidate, Intel Macs
remain FTS5-only because neither reviewed ORT release supplies an x86_64 Mac
wheel. No untested source-build route is promised.

**Supplied telemetry finding + future design requirement:**
`ORT_DISABLE_TELEMETRY=1` before initialization is the reliable switch;
`disable_telemetry_events()` alone suppresses only non-essential events.
Retain the pre-import environment controls in the earlier implementation
contract, CPU-only local-file inference and separate verified acquisition.
The run's zero Python network attempts do not establish native offline
containment. A future dependency adoption needs escalated supply-chain review,
including exact wheel provenance, transitive licences/advisories, native
network behavior and update/rollback paths. This documentation-only decision
changes none of those boundaries and requires no escalated review itself.

### Rebuildable index boundary retained

**Design requirement, not implemented lifecycle evidence:** retain the prior
plain BLOB design keyed by `memories.id` (never rowid), with content/model
digests. Re-check canonical owner, currentness, expiry, forgetting and content
eligibility at query time; cached membership cannot grant authority. Delete
or invalidate derived vectors on canonical changes. Drop vector tables from
the portable export copy before export/VACUUM and rebuild on restore.
Canonical Memory, messages, Work, approvals, recovery, auth state and Evidence
remain authoritative and independent of the index. This preserves the File
workspace boundary and adds no restriction on owner data under C17. Product
export/restore and forget/undo integration are still unimplemented and must
be tested if the work reopens.

### Reopen conditions

**Future acceptance requirements (design decisions, not passed evidence):**

1. Resolve or explain `h2-context-aquarium`, `h2-context-inkstone` and
   `h2-context-lift` on the shipped #860 path in a separate context-harm issue.
   Trace request-only versus combined-query ranking and the snapshot bound;
   record the cause and its effect on which rows reach the AI. An explanation
   alone does not waive the embedding no-regression rule. No embedding is in
   that investigation's scope.
2. Pre-register a v3 fixture with fresh held-out cases, primary arm,
   parameters, baselines and cost protocol before running it. V2 failures can
   be dev regressions only. The frozen embedding arm must be preferred against
   **lexical, lexical-context and runtime**, with zero traps on both splits,
   no lost lexical controls and no held-out regression. Require a common
   held-out needed-row recovery against all three. Include a bounded comparison
   with the #403 AI-written-phrasing path to identify a measured residual miss
   phrasing does not fix; report sampling uncertainty and do not claim that
   finitely many phrasings prove impossibility.
3. Meet the cost targets below under a predeclared uncontended owner-Mac
   protocol, with the supported Linux path separately checked. Freeze these
   targets before v3 and review any proposed change before that run. Validate
   visible FTS5-only operation on unsupported or missing-model paths. Complete
   the escalated supply-chain review and lifecycle/authority tests in the
   earlier implementation contract before product adoption.

**New design targets for reopening:** these replace the earlier unset-budget
requirement. They are engineering acceptance limits, not retrospective gates
for v2 or owner-approved performance claims. The build and RSS limits bound
rebuild waiting and resident cost; the warm-query limit protects interactive
use. The present 406 s build and 891 MB RSS do not meet them.

| Measure | Maximum for future implementation |
| --- | ---: |
| Fresh-process model load, OS cache state recorded | 2 s |
| Warm embedding + retrieval at 10k eligible rows, end-to-end p95 | 50 ms |
| Build 1k / 10k representative 30–200 character KO/EN rows | 15 s / 120 s |
| Search 50k rows including SQLite read and eligibility, p95 | 250 ms |
| Peak process RSS during load/query/build | 512 MB |
| Selected model file | 250 MB |
| Installed optional dependency closure | 150 MB |
| Complete required model/tokenizer/config directory | 300 MB |
| Materialized vector payload at 10k rows (database overhead separately reported) | 20 MB |

**Design boundary:** no successor implementation is authorized by closing
#849. Any later implementation must retain these quality, cost, portability
and canonical-state requirements in its own bounded issue.
