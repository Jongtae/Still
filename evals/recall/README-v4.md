# R1 v4: recall regression fixture (#921)

V3's `h3-*` held-out cases exposed the gaps motivating #921. Under #827,
an arm changed because of those observations needs fresh held-out cases.
V4 moves all 36 v3 cases to dev and adds 24 fresh `h4-*` held-out cases.
Claude supplied 12 additional `h4c-*` cases, giving 36 held-out cases
(24 GPT + 12 Claude) and 72 cases overall. All 36 pass the unchanged checks.
All content is invented; no owner data is used. Fixture qualification uses
model-free tests only, with no models, embeddings or live CLI calls.

The light-model comparison was dropped by owner decision on 2026-11-17
(#921 comment 6932582020); this fixture is kept as regression input.

V4 reads v3's JSON. Every v3 row retains its object, flags and relative order;
every v3 case retains all fields except `split`, which becomes `dev`. Owner
and fixture date (`2026-03-25`) are unchanged. V1, v2 and v3 JSON files remain
byte-identical, with their SHA-256 digests pinned in the v4 test. The only
loader change adds `recall-pairs/4` to `VERSIONS`; unknown versions remain
invalid. Schema, arms, scorer, gate and product code are unchanged.

## Authorship

Dev cases and `h4-*` rows and queries were written by Codex gpt-6.1-sol.
`h4c-*` rows and queries were both written by Claude (Anthropic), outside the
GPT family. Claude rewrote its cases after a first submission failed the
unchanged checks; the revised text was copied verbatim from
`claude-subset-v5.json`. The checks were not loosened. Held-out notes start
with `[author:gpt]` or `[author:claude]`; dev notes remain unchanged.

Write-time expansion by a same-family model can share vocabulary with the
queries (Doc2Query--, [arXiv 2301.03266](https://arxiv.org/abs/2301.03266)).
Report results per author subset; fixture qualification is model-free and
does not measure any expansion model's performance.

## Store

| Rows | Count |
|---|---:|
| Unchanged v3 rows | 1,000 |
| New standalone cut targets | 16 |
| New particle targets | 3 |
| New exact lexical controls | 2 |
| New irrelevant harm-control facts | 3 |
| New current cluster siblings | 48 |
| New historical/expired cluster traps | 6 |
| Total | 1,078 |

The 24 standalone facts sit immediately before v3's filler, outside the
newest-500 candidate window. Their keys use opaque ordinals (`t00`–`t15`
for GPT, `u00`–`u07` for Claude). Four GPT clusters
follow v3's clusters: mushroom cultures, meteorites, cheese ripening and
telegraph relays. Each has eight current siblings with contrasting attributes
and a near-trap: two superseded, one corrected and one expired. Claude's
teacups (opaque slug `kx`, corrected trap) and tents (opaque slug `ky`,
superseded trap) follow those four clusters.
Every new row has 20–120 content characters on one line, in one language.

New standalone subjects are equestrian saddles, origami cranes, snowshoes,
coffee roasters, greenhouse vents, fencing foils, watercolour palettes,
lacrosse sticks, hammocks, zithers, thimbles, inkwells, gyroscope cases,
embossing plate cases, grammar workbooks and phonograph covers. Claude's
standalone subjects are daegeum, sourdough starter, sansevieria, kayak spray
skirts, drill batteries, fountain pen nibs, millstones and lightning bookmarks.

| Content corpus (newline-joined content only) | Rows | Characters | UTF-8 bytes |
|---|---:|---:|---:|
| All rows, including history | 1,078 | 67,690 | 101,812 |
| Current and holding on fixture date | 1,053 | 66,811 | 100,373 |
| Product snapshot bound | — | 12,000 | 12,000 |

Real FTS5 seeding verifies eligibility and trap states. Request-only
`MemoryService.profile_snapshot` omits every new cut and particle target with
`omitted_count > 0`. Eligible content alone exceeds four times both bounds.

## Cases

| Kind | Dev (all v3 cases) | Held-out |
|---|---:|---:|
| Paraphrase | 13 | 14 |
| Cross-language | 12 | 12 |
| Short follow-up | 5 | 5 |
| Lexical control | 3 | 2 |
| Harm control | 3 | 3 |
| Total | 36 | 36 (24 GPT + 12 Claude) |

| Note facet / kind (after author tag) | Dev | Held-out |
|---|---:|---:|
| `[cluster]` paraphrase | 6 | 6 |
| `[cluster]` cross-language | 6 | 6 |
| `[cut]` cross-language | 6 | 6 |
| `[cut]` paraphrase | 4 | 5 |
| `[cut]` short-follow-up | 5 | 5 |
| `[particle]` paraphrase | 3 | 3 |
| `[near-trap]` (overlaps cluster) | 12 | 12 |
| `[misleading-context]` (harm control) | 1 | 1 |
| `[incidental-noise]` (meaning cases) | 0 | 0 |

Cluster and cut cross-language cases each balance three EN→KO and three
KO→EN cases. Every meaning case needs one row. Cluster queries distinguish
one sibling by a paraphrased or translated attribute; the other seven are
irrelevant. Follow-up contexts identify the referent without giving answers.

The v3 leakage scan covers every held-out meaning query and context,
including indexed keys. Query/context tokens never prefix a needed token.
The only reverse-stem exceptions are `골무를`→`골무`, `잉크병에서`→`잉크병`
and `맷돌을`→`맷돌`. The unchanged conservative content-word stem check
compares all 36 dev queries against the 36 held-out queries. Real `lexical`,
`context-concat` and `lexical-context` runs must recover zero needed rows
on fresh meaning cases, with no unlabelled noise or trap hits. Both new
lexical controls must pass without noise under `lexical`.

All 12 Claude cases run through the same checks, including snapshot
omission for all seven cut/particle targets, stored trap states, and all three
lexical paths. The tests pin the revised source text and check the built
fixture directly. The zero-hit check also requires zero unlabelled noise;
its noise assertion is unchanged.

## Held-out rule and regeneration

Use the fresh held-out cases **once in one locked run**, including any
predeclared sampling repetitions. A change motivated by a held-out failure
moves that case to dev and requires fresh held-out cases before evaluation.
Fixture qualification is not a candidate result, evidence of light-model
performance, or evidence that recalled facts are used correctly in answers.

From the repository root, with `PYTHON` set to the project's venv interpreter:

```sh
python evals/recall/build_v4.py
python evals/recall/build_v4.py --check
PYTHONPATH=src:evals:. "$PYTHON" -m pytest -q tests/test_recall_pairs_v4.py tests/test_recall_pairs_v3.py tests/test_recall_pairs_v2.py tests/test_recall_pairs.py -p no:cacheprovider
```

The builder uses only the standard library and writes deterministic UTF-8
JSON with a final newline. `--check` compares bytes without writing.
