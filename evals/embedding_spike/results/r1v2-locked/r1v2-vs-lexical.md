# R1 paired recall

Fixture: recall-pairs/2 · `3012317a6f6c97fda210a75bc9f76435cb7a53e47dd98444b9854880d662a086`
Today: 2026-03-25 · Baseline: lexical

- vector: local operating observation (offline embedding model); 1 run(s)
- hybrid: local operating observation (offline embedding model); 1 run(s)
- hybrid_lexical_context: local operating observation (offline embedding model); 1 run(s)
- lexical: fixture (model-free); 1 run(s)
- lexical-context: fixture (model-free); 1 run(s)
- runtime: fixture (model-free); 1 run(s)

Sampling recall, hit totals and noise use observed majority representatives; trap case counts include every run. Per-run recall and uncertainty appear below.

## dev

| Arm | Needed recall | Needed hit/total | Cases with trap | Noise | Fixed | Regressed | Uncertain | Unchanged-pass | Unchanged-fail |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| vector | 0.562 | 9/16 | 0 | 0 | 6 | 1 | 0 | 11 | 6 |
| hybrid | 0.750 | 12/16 | 0 | 15 | 2 | 0 | 0 | 12 | 10 |
| hybrid_lexical_context | 0.750 | 12/16 | 0 | 16 | 1 | 0 | 0 | 12 | 11 |
| lexical | 0.625 | 10/16 | 0 | 15 | 0 | 0 | 0 | 12 | 12 |
| lexical-context | 0.750 | 12/16 | 0 | 16 | 1 | 0 | 0 | 12 | 11 |
| runtime | 0.750 | 12/16 | 0 | 16 | 1 | 0 | 0 | 12 | 11 |

### vector (dev)

- d-follow (short-follow-up): fixed
- d-dated-current (dated-contradiction): regressed
- d-noise (harm-control): fixed
- h-follow (short-follow-up): fixed
- h-correction (correction): fixed
- h-forget-role (forgetting): fixed
- h-noise (harm-control): fixed

Trap regressions:

None.

### hybrid (dev)

- d-follow (short-follow-up): fixed
- h-follow (short-follow-up): fixed

Trap regressions:

None.

### hybrid_lexical_context (dev)

- h-follow (short-follow-up): fixed

Trap regressions:

None.

### lexical (dev)


Trap regressions:

None.

### lexical-context (dev)

- h-follow (short-follow-up): fixed

Trap regressions:

None.

### runtime (dev)

- h-follow (short-follow-up): fixed

Trap regressions:

None.

## heldout

| Arm | Needed recall | Needed hit/total | Cases with trap | Noise | Fixed | Regressed | Uncertain | Unchanged-pass | Unchanged-fail |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| vector | 0.238 | 5/21 | 0 | 5 | 3 | 3 | 0 | 2 | 16 |
| hybrid | 0.286 | 6/21 | 0 | 8 | 3 | 2 | 0 | 3 | 16 |
| hybrid_lexical_context | 0.286 | 6/21 | 0 | 8 | 3 | 3 | 0 | 2 | 16 |
| lexical | 0.143 | 3/21 | 0 | 5 | 0 | 0 | 0 | 5 | 19 |
| lexical-context | 0.143 | 3/21 | 0 | 8 | 0 | 3 | 0 | 2 | 19 |
| runtime | 0.143 | 3/21 | 0 | 8 | 0 | 3 | 0 | 2 | 19 |

### vector (heldout)

- h2-smoke (paraphrase): fixed
- h2-swimming (paraphrase): fixed
- h2-riddle (paraphrase): fixed
- h2-context-aquarium (harm-control): regressed
- h2-context-inkstone (harm-control): regressed
- h2-context-lift (lexical-control): regressed

Trap regressions:

None.

### hybrid (heldout)

- h2-smoke (paraphrase): fixed
- h2-swimming (paraphrase): fixed
- h2-riddle (paraphrase): fixed
- h2-context-aquarium (harm-control): regressed
- h2-context-inkstone (harm-control): regressed

Trap regressions:

None.

### hybrid_lexical_context (heldout)

- h2-smoke (paraphrase): fixed
- h2-swimming (paraphrase): fixed
- h2-riddle (paraphrase): fixed
- h2-context-aquarium (harm-control): regressed
- h2-context-inkstone (harm-control): regressed
- h2-context-lift (lexical-control): regressed

Trap regressions:

None.

### lexical (heldout)


Trap regressions:

None.

### lexical-context (heldout)

- h2-context-aquarium (harm-control): regressed
- h2-context-inkstone (harm-control): regressed
- h2-context-lift (lexical-control): regressed

Trap regressions:

None.

### runtime (heldout)

- h2-context-aquarium (harm-control): regressed
- h2-context-inkstone (harm-control): regressed
- h2-context-lift (lexical-control): regressed

Trap regressions:

None.

## Verdicts

- vector: not preferred; failed: b-no-lexical-control-regressions, d-no-heldout-regressions. R1 retrieval evidence only; dev and heldout are evaluated separately. Any observed trap overrides majority; uncertain gains do not count as fixed.
- hybrid: not preferred; failed: d-no-heldout-regressions. R1 retrieval evidence only; dev and heldout are evaluated separately. Any observed trap overrides majority; uncertain gains do not count as fixed.
- hybrid_lexical_context: not preferred; failed: b-no-lexical-control-regressions, d-no-heldout-regressions. R1 retrieval evidence only; dev and heldout are evaluated separately. Any observed trap overrides majority; uncertain gains do not count as fixed.
- lexical: baseline.
- lexical-context: not preferred; failed: b-no-lexical-control-regressions, c-heldout-gain-and-fix, d-no-heldout-regressions. R1 retrieval evidence only; dev and heldout are evaluated separately. Any observed trap overrides majority; uncertain gains do not count as fixed.
- runtime: not preferred; failed: b-no-lexical-control-regressions, c-heldout-gain-and-fix, d-no-heldout-regressions. R1 retrieval evidence only; dev and heldout are evaluated separately. Any observed trap overrides majority; uncertain gains do not count as fixed.
