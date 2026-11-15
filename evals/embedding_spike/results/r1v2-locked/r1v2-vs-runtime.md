# R1 paired recall

Fixture: recall-pairs/2 · `3012317a6f6c97fda210a75bc9f76435cb7a53e47dd98444b9854880d662a086`
Today: 2026-03-25 · Baseline: runtime

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
| vector | 0.562 | 9/16 | 0 | 0 | 5 | 1 | 0 | 12 | 6 |
| hybrid | 0.750 | 12/16 | 0 | 15 | 1 | 0 | 0 | 13 | 10 |
| hybrid_lexical_context | 0.750 | 12/16 | 0 | 16 | 0 | 0 | 0 | 13 | 11 |
| lexical | 0.625 | 10/16 | 0 | 15 | 0 | 1 | 0 | 12 | 11 |
| lexical-context | 0.750 | 12/16 | 0 | 16 | 0 | 0 | 0 | 13 | 11 |
| runtime | 0.750 | 12/16 | 0 | 16 | 0 | 0 | 0 | 13 | 11 |

### vector (dev)

- d-follow (short-follow-up): fixed
- d-dated-current (dated-contradiction): regressed
- d-noise (harm-control): fixed
- h-correction (correction): fixed
- h-forget-role (forgetting): fixed
- h-noise (harm-control): fixed

Trap regressions:

None.

### hybrid (dev)

- d-follow (short-follow-up): fixed

Trap regressions:

None.

### hybrid_lexical_context (dev)


Trap regressions:

None.

### lexical (dev)

- h-follow (short-follow-up): regressed

Trap regressions:

None.

### lexical-context (dev)


Trap regressions:

None.

### runtime (dev)


Trap regressions:

None.

## heldout

| Arm | Needed recall | Needed hit/total | Cases with trap | Noise | Fixed | Regressed | Uncertain | Unchanged-pass | Unchanged-fail |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| vector | 0.238 | 5/21 | 0 | 5 | 3 | 0 | 0 | 2 | 19 |
| hybrid | 0.286 | 6/21 | 0 | 8 | 4 | 0 | 0 | 2 | 18 |
| hybrid_lexical_context | 0.286 | 6/21 | 0 | 8 | 3 | 0 | 0 | 2 | 19 |
| lexical | 0.143 | 3/21 | 0 | 5 | 3 | 0 | 0 | 2 | 19 |
| lexical-context | 0.143 | 3/21 | 0 | 8 | 0 | 0 | 0 | 2 | 22 |
| runtime | 0.143 | 3/21 | 0 | 8 | 0 | 0 | 0 | 2 | 22 |

### vector (heldout)

- h2-smoke (paraphrase): fixed
- h2-swimming (paraphrase): fixed
- h2-riddle (paraphrase): fixed

Trap regressions:

None.

### hybrid (heldout)

- h2-smoke (paraphrase): fixed
- h2-swimming (paraphrase): fixed
- h2-riddle (paraphrase): fixed
- h2-context-lift (lexical-control): fixed

Trap regressions:

None.

### hybrid_lexical_context (heldout)

- h2-smoke (paraphrase): fixed
- h2-swimming (paraphrase): fixed
- h2-riddle (paraphrase): fixed

Trap regressions:

None.

### lexical (heldout)

- h2-context-aquarium (harm-control): fixed
- h2-context-inkstone (harm-control): fixed
- h2-context-lift (lexical-control): fixed

Trap regressions:

None.

### lexical-context (heldout)


Trap regressions:

None.

### runtime (heldout)


Trap regressions:

None.

## Verdicts

- vector: preferred; failed: none. R1 retrieval evidence only; dev and heldout are evaluated separately. Any observed trap overrides majority; uncertain gains do not count as fixed.
- hybrid: preferred; failed: none. R1 retrieval evidence only; dev and heldout are evaluated separately. Any observed trap overrides majority; uncertain gains do not count as fixed.
- hybrid_lexical_context: preferred; failed: none. R1 retrieval evidence only; dev and heldout are evaluated separately. Any observed trap overrides majority; uncertain gains do not count as fixed.
- lexical: preferred; failed: none. R1 retrieval evidence only; dev and heldout are evaluated separately. Any observed trap overrides majority; uncertain gains do not count as fixed.
- lexical-context: not preferred; failed: c-heldout-gain-and-fix. R1 retrieval evidence only; dev and heldout are evaluated separately. Any observed trap overrides majority; uncertain gains do not count as fixed.
- runtime: baseline.
