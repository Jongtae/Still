# R1 v3: bounded owner-memory recall fixture (#403)

This fixture measures semantic discrimination and omission from the product's
bounded profile supply. V2 has only 57 rows (about 2,078 content characters),
so its complete store fits in the snapshot; it cannot establish retrieval
quality when the profile bound cuts off relevant facts. V3 contains 1,000
synthetic rows and 36 fresh cases. All content is invented; no owner data or
real people's data is used. No model or embedding was run to construct or
qualify this fixture.

The first 57 rows retain v2's exact serialized bytes, refs, keys, content,
flags and order. V1 and v2 files are unchanged (SHA-256 pinned in the test).
V3 carries no earlier cases forward. It uses the existing strict row/case
schema, kinds, scorer and R1 decision rule, with `version: "recall-pairs/3"`.

## Integration boundary

The base checkout initially accepted only `/1` and `/2` in
`recall_pairs.VERSIONS`. The caller separately approved adding `/3`; this
fixture task preserves that loader change. Tests use normal `load` and `seed`
calls, with the v3 version and digest, and still reject an unknown `/4`.
The older tests' version expectations were updated within the expanded scope.
The schema, arms, search, state validation, `run`, scorer, gate, runtime and
product code are unchanged by this fixture task.

## Store design and size

| Rows | Count |
|---|---:|
| Unchanged v2 prefix | 57 |
| Handwritten current cluster siblings | 48 |
| Cluster historical/expired near-traps | 6 |
| Standalone cut targets | 15 |
| Particle targets | 3 |
| Exact lexical control targets | 3 |
| Irrelevant harm-control facts | 3 |
| Generated bilingual filler | 865 |
| Total | 1,000 |

There are 534 Korean and 466 English rows, without bilingual content within
new rows. Every new content value is 20–120 characters, on one line. All keys
pass `MemoryService.is_profile_key`. Standalone target keys are opaque
ordinals because English subject names would leak through the indexed keys.
Filler has unique keys and content. Its pure ordinal templates combine twenty
personal collection objects per language and nine storage locations, with
collection numbers and care habits; it never reads cases, split labels,
needed refs, traps or retrieval observations.

Measured sizes use newline-joined **content only**, before adding snapshot
keys, saved dates or annotations. `test_snapshot_cuts_store_size_and_cluster_visibility`
measures eligible content from the genuinely seeded store and records the
values as pytest properties.

| Content corpus | Rows | Characters | UTF-8 bytes |
|---|---:|---:|---:|
| All seed rows, including history | 1,000 | 63,537 | 95,577 |
| Current, holding rows on `today` | 981 | 62,982 | 94,598 |
| Product snapshot bound | — | 12,000 | 12,000 |
| Eligible content / bound | — | 5.25× | 7.88× |

Seed order is: unchanged v2 rows, standalone targets/controls, filler, then
clusters. All fifteen cut targets precede the newest 500-row candidate window.
Their `zzcut` keys would also sort after filler (`aa`) and clusters (`bb`).
The product prioritizes at most twenty literal request matches, then fills
alphabetically from its bounded newest-row candidates, stopping at the first
line that exceeds either bound. The test uses real seeding and
`MemoryService.profile_snapshot(owner, query=case['query'])`, with the fixture
date and default bounds: no context concatenation or reformulated selector.
Every cut target must be absent, every snapshot must have `omitted_count > 0`,
and both content-size lower bounds must exceed four times the product bound.

The twelve cluster cases also record needed-row visibility as an observation,
without making visibility a pass/fail condition. At qualification all twelve
were absent: recent clusters sit behind the alphabetical filler when no
request terms match them. This fixture therefore no longer supplies the
whole store to the plan or worker by default.

## Cases and facets

| Kind | Dev | Held-out |
|---|---:|---:|
| Paraphrase | 4 | 9 |
| Cross-language | 4 | 8 |
| Short follow-up | 2 | 3 |
| Lexical control | 1 | 2 |
| Harm control | 1 | 2 |
| Total | 12 | 24 |

Facet tags lead `note`; they add no schema fields or kinds.

| Facet / dimension | Dev | Held-out |
|---|---:|---:|
| `[cluster]` paraphrase | 2 | 4 |
| `[cluster]` cross-language | 2 | 4 |
| `[cut]` cross-language | 2 | 4 |
| `[cut]` paraphrase | 1 | 3 |
| `[cut]` short follow-up | 2 | 3 |
| `[particle]` paraphrase | 1 | 2 |
| `[near-trap]` (overlaps cluster) | 4 | 8 |
| `[misleading-context]` (harm control) | 0 | 1 |
| `[incidental-noise]` (meaning cases) | 0 | 0 |

Cluster and cut cross-language cases each balance EN→KO and KO→EN: one of
each direction in dev, two of each in held-out. Every meaning case needs
exactly one row. Follow-up context names the referent in different words and
does not supply the requested answer.

The six clusters each have eight similar current siblings, with one selected
by a paraphrased or translated distinguishing attribute. Their other seven
siblings are explicitly irrelevant. Dev covers model railway locomotives and
percussion mallets; held-out covers tide probes, microscope slides, stage
puppets, and diving cylinders. A same-key older value is superseded in rail
and slides; an owner correction invalidates the older mallet and puppet
value; a separate older tide/cylinder value expires on 2026-03-01. The fixture
date is 2026-03-25. Tests verify these exclusions in real stored state,
independently of the trap labels, and verify that the unchanged scorer treats
returning each trap as an authority hit.

Both lexical controls and harm controls are fresh per split. The three exact
lexical controls must pass with no noise. Harm controls need no row; the dev
word-spacing and held-out etymology questions deliberately share words with
irrelevant facts, while the held-out physics question has misleading context.

## Leakage, qualification and held-out use

The v2 leakage scan applies to **all** dev and held-out meaning cases: no
query or context token may be a casefolded prefix of any needed content or
indexed key token. It scans every token, including numbers, Latin words and
Korean prefixes, without term/result limits. Counterexamples exercise both
fields and inputs, beyond twelve search terms and into a second needed row.
Real FTS5 seeding and `recall_pairs.run` additionally require zero needed hits
on all thirty meaning cases under `lexical`, `context-concat`, and
`lexical-context`. These are fixture-qualification checks, not candidate
performance observations. There is no residual incidental lexical noise on
meaning cases under these arms; the tests enforce this.

The only relaxed **stem-separation** cases are `[particle]`: dev
`거름체를`→`거름체`, held-out `다림추를`→`다림추` and
`도가니에서`→`도가니`. Each query intentionally contains the stored bare noun
plus a Korean particle. The query token is longer than the stored token, so
the product's quoted prefix FTS query cannot reach that stored token. These
cases still pass the forward-prefix leakage scan and zero-needed-hit checks;
only reverse stem overlap is allowed, and the test pins the exact pairs.
No other meaning case shares even a reverse token prefix with its needed
content. A conservative additional check strips common English inflections
and Korean particles and rejects content-word prefix stems shared between
dev and held-out queries, allowing only grammatical function words. This
simple check is a fixture guard, not a linguistic analyzer.

Under #827, held-out subjects and wording are written separately from dev.
After locking v3, use its held-out set **once in a locked candidate run**
(including predeclared sampling repetitions). Any change motivated by a
held-out failure moves that case to dev and requires a new held-out case;
do not repeatedly tune against these cases. Qualification during fixture
authoring does not claim a candidate result. The unchanged #849 gate requires
the candidate to beat both request-only `lexical` and context-first
`lexical-context`, with a common held-out needed-row recovery, no authority
hits, no lexical-control loss or held-out regression, and the existing
sampling uncertainty rules. `context-concat` remains diagnostic. This fixture
alone provides no evidence that any candidate passes that gate or uses recalled
facts correctly in an answer.

## Regeneration and verification

Run from the repository root (standard-library Python suffices for generation):
Set `PYTHON` to the project's venv interpreter.

```sh
python evals/recall/build_v3.py
python evals/recall/build_v3.py --check
PYTHONPATH=src:evals:. "$PYTHON" -m pytest -q tests/test_recall_pairs_v3.py tests/test_recall_pairs.py tests/test_recall_pairs_v2.py -p no:cacheprovider
```

The generator reads v2's rows and its own handwritten data, then writes UTF-8
JSON with stable field/row ordering and a final newline. `--check` compares
bytes without writing. It needs no randomness, network, model, embedding,
package download or live CLI.
