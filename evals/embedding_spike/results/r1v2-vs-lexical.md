# R1 paired recall

Fixture: recall-pairs/2 · `7afe5815fbad5f3857c46537041248776979d206c6c004e33bc36646e60a33d7`
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

Held-out not evaluated. All preference verdicts deferred.
