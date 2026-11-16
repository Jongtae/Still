# R1 paired recall

Fixture: recall-pairs/3 · `a6359e1f05bc480b12b8c3dff2f4bdd26bc69fdbb368be3d3cb20ab78e41a612`
Today: 2026-03-25 · Baseline: lexical-context

- lexical: fixture (model-free); 1 run(s)
- lexical-context: fixture (model-free); 1 run(s)
- runtime: fixture (model-free); 1 run(s)
- vector: local operating observation (offline embedding model); 1 run(s)
- hybrid: local operating observation (offline embedding model); 1 run(s)

Sampling recall, hit totals and noise use observed majority representatives; trap case counts include every run. Per-run recall and uncertainty appear below.

## dev

| Arm | Needed recall | Needed hit/total | Cases with trap | Noise | Fixed | Regressed | Uncertain | Unchanged-pass | Unchanged-fail |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| lexical | 0.091 | 1/11 | 0 | 1 | 0 | 0 | 0 | 1 | 11 |
| lexical-context | 0.091 | 1/11 | 0 | 1 | 0 | 0 | 0 | 1 | 11 |
| runtime | 0.091 | 1/11 | 0 | 1 | 0 | 0 | 0 | 1 | 11 |
| vector | 0.091 | 1/11 | 0 | 0 | 1 | 0 | 0 | 1 | 10 |
| hybrid | 0.182 | 2/11 | 0 | 1 | 1 | 0 | 0 | 1 | 10 |

### lexical (dev)


Trap regressions:

None.

### lexical-context (dev)


Trap regressions:

None.

### runtime (dev)


Trap regressions:

None.

### vector (dev)

- d3-timbre (harm-control): fixed

Trap regressions:

None.

### hybrid (dev)

- d3-sieve (paraphrase): fixed

Trap regressions:

None.

## heldout

| Arm | Needed recall | Needed hit/total | Cases with trap | Noise | Fixed | Regressed | Uncertain | Unchanged-pass | Unchanged-fail |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| lexical | 0.091 | 2/22 | 0 | 1 | 1 | 0 | 0 | 2 | 21 |
| lexical-context | 0.091 | 2/22 | 0 | 2 | 0 | 0 | 0 | 2 | 22 |
| runtime | 0.091 | 2/22 | 0 | 1 | 1 | 0 | 0 | 2 | 21 |
| vector | 0.045 | 1/22 | 0 | 0 | 2 | 1 | 0 | 1 | 20 |
| hybrid | 0.091 | 2/22 | 0 | 1 | 1 | 0 | 0 | 2 | 21 |

### lexical (heldout)

- h3-vacuum (harm-control): fixed

Trap regressions:

None.

### lexical-context (heldout)


Trap regressions:

None.

### runtime (heldout)

- h3-vacuum (harm-control): fixed

Trap regressions:

None.

### vector (heldout)

- h3-astrolabe (lexical-control): regressed
- h3-fulcrum (harm-control): fixed
- h3-vacuum (harm-control): fixed

Trap regressions:

None.

### hybrid (heldout)

- h3-vacuum (harm-control): fixed

Trap regressions:

None.

## Verdicts

- lexical: preferred; failed: none. R1 retrieval evidence only; dev and heldout are evaluated separately. Any observed trap overrides majority; uncertain gains do not count as fixed.
- lexical-context: baseline.
- runtime: preferred; failed: none. R1 retrieval evidence only; dev and heldout are evaluated separately. Any observed trap overrides majority; uncertain gains do not count as fixed.
- vector: not preferred; failed: b-no-lexical-control-regressions, c-heldout-gain-and-fix, d-no-heldout-regressions. R1 retrieval evidence only; dev and heldout are evaluated separately. Any observed trap overrides majority; uncertain gains do not count as fixed.
- hybrid: preferred; failed: none. R1 retrieval evidence only; dev and heldout are evaluated separately. Any observed trap overrides majority; uncertain gains do not count as fixed.

## Bounded snapshot diagnostic (no verdict)

| Arm | Run | Split | Group | Needed shown/total | Request-only shown/total | Omitted |
| --- | --- | --- | --- | --- | --- | --- |
| lexical | 1 | dev | total | 1/11 | 1/11 | 4982 |
| lexical | 1 | dev | kind:cross-language | 0/4 | 0/4 | 1660 |
| lexical | 1 | dev | kind:harm-control | 0/0 | 0/0 | 416 |
| lexical | 1 | dev | kind:lexical-control | 1/1 | 1/1 | 416 |
| lexical | 1 | dev | kind:paraphrase | 0/4 | 0/4 | 1660 |
| lexical | 1 | dev | kind:short-follow-up | 0/2 | 0/2 | 830 |
| lexical | 1 | dev | facet:cluster | 0/4 | 0/4 | 1660 |
| lexical | 1 | dev | facet:cut | 0/5 | 0/5 | 2075 |
| lexical | 1 | dev | facet:near-trap | 0/4 | 0/4 | 1660 |
| lexical | 1 | dev | facet:particle | 0/1 | 0/1 | 415 |
| lexical | 1 | heldout | total | 2/22 | 2/22 | 9963 |
| lexical | 1 | heldout | kind:cross-language | 0/8 | 0/8 | 3320 |
| lexical | 1 | heldout | kind:harm-control | 0/0 | 0/0 | 831 |
| lexical | 1 | heldout | kind:lexical-control | 2/2 | 2/2 | 832 |
| lexical | 1 | heldout | kind:paraphrase | 0/9 | 0/9 | 3735 |
| lexical | 1 | heldout | kind:short-follow-up | 0/3 | 0/3 | 1245 |
| lexical | 1 | heldout | facet:cluster | 0/8 | 0/8 | 3320 |
| lexical | 1 | heldout | facet:cut | 0/10 | 0/10 | 4150 |
| lexical | 1 | heldout | facet:misleading-context | 0/0 | 0/0 | 415 |
| lexical | 1 | heldout | facet:near-trap | 0/8 | 0/8 | 3320 |
| lexical | 1 | heldout | facet:particle | 0/2 | 0/2 | 830 |
| lexical-context | 1 | dev | total | 1/11 | 1/11 | 4982 |
| lexical-context | 1 | dev | kind:cross-language | 0/4 | 0/4 | 1660 |
| lexical-context | 1 | dev | kind:harm-control | 0/0 | 0/0 | 416 |
| lexical-context | 1 | dev | kind:lexical-control | 1/1 | 1/1 | 416 |
| lexical-context | 1 | dev | kind:paraphrase | 0/4 | 0/4 | 1660 |
| lexical-context | 1 | dev | kind:short-follow-up | 0/2 | 0/2 | 830 |
| lexical-context | 1 | dev | facet:cluster | 0/4 | 0/4 | 1660 |
| lexical-context | 1 | dev | facet:cut | 0/5 | 0/5 | 2075 |
| lexical-context | 1 | dev | facet:near-trap | 0/4 | 0/4 | 1660 |
| lexical-context | 1 | dev | facet:particle | 0/1 | 0/1 | 415 |
| lexical-context | 1 | heldout | total | 2/22 | 2/22 | 9964 |
| lexical-context | 1 | heldout | kind:cross-language | 0/8 | 0/8 | 3320 |
| lexical-context | 1 | heldout | kind:harm-control | 0/0 | 0/0 | 832 |
| lexical-context | 1 | heldout | kind:lexical-control | 2/2 | 2/2 | 832 |
| lexical-context | 1 | heldout | kind:paraphrase | 0/9 | 0/9 | 3735 |
| lexical-context | 1 | heldout | kind:short-follow-up | 0/3 | 0/3 | 1245 |
| lexical-context | 1 | heldout | facet:cluster | 0/8 | 0/8 | 3320 |
| lexical-context | 1 | heldout | facet:cut | 0/10 | 0/10 | 4150 |
| lexical-context | 1 | heldout | facet:misleading-context | 0/0 | 0/0 | 416 |
| lexical-context | 1 | heldout | facet:near-trap | 0/8 | 0/8 | 3320 |
| lexical-context | 1 | heldout | facet:particle | 0/2 | 0/2 | 830 |
| runtime | 1 | dev | total | 1/11 | 1/11 | 4982 |
| runtime | 1 | dev | kind:cross-language | 0/4 | 0/4 | 1660 |
| runtime | 1 | dev | kind:harm-control | 0/0 | 0/0 | 416 |
| runtime | 1 | dev | kind:lexical-control | 1/1 | 1/1 | 416 |
| runtime | 1 | dev | kind:paraphrase | 0/4 | 0/4 | 1660 |
| runtime | 1 | dev | kind:short-follow-up | 0/2 | 0/2 | 830 |
| runtime | 1 | dev | facet:cluster | 0/4 | 0/4 | 1660 |
| runtime | 1 | dev | facet:cut | 0/5 | 0/5 | 2075 |
| runtime | 1 | dev | facet:near-trap | 0/4 | 0/4 | 1660 |
| runtime | 1 | dev | facet:particle | 0/1 | 0/1 | 415 |
| runtime | 1 | heldout | total | 2/22 | 2/22 | 9963 |
| runtime | 1 | heldout | kind:cross-language | 0/8 | 0/8 | 3320 |
| runtime | 1 | heldout | kind:harm-control | 0/0 | 0/0 | 831 |
| runtime | 1 | heldout | kind:lexical-control | 2/2 | 2/2 | 832 |
| runtime | 1 | heldout | kind:paraphrase | 0/9 | 0/9 | 3735 |
| runtime | 1 | heldout | kind:short-follow-up | 0/3 | 0/3 | 1245 |
| runtime | 1 | heldout | facet:cluster | 0/8 | 0/8 | 3320 |
| runtime | 1 | heldout | facet:cut | 0/10 | 0/10 | 4150 |
| runtime | 1 | heldout | facet:misleading-context | 0/0 | 0/0 | 415 |
| runtime | 1 | heldout | facet:near-trap | 0/8 | 0/8 | 3320 |
| runtime | 1 | heldout | facet:particle | 0/2 | 0/2 | 830 |
| vector | 1 | dev | total | 1/11 | 1/11 | 4981 |
| vector | 1 | dev | kind:cross-language | 0/4 | 0/4 | 1660 |
| vector | 1 | dev | kind:harm-control | 0/0 | 0/0 | 415 |
| vector | 1 | dev | kind:lexical-control | 1/1 | 1/1 | 416 |
| vector | 1 | dev | kind:paraphrase | 0/4 | 0/4 | 1660 |
| vector | 1 | dev | kind:short-follow-up | 0/2 | 0/2 | 830 |
| vector | 1 | dev | facet:cluster | 0/4 | 0/4 | 1660 |
| vector | 1 | dev | facet:cut | 0/5 | 0/5 | 2075 |
| vector | 1 | dev | facet:near-trap | 0/4 | 0/4 | 1660 |
| vector | 1 | dev | facet:particle | 0/1 | 0/1 | 415 |
| vector | 1 | heldout | total | 1/22 | 2/22 | 9961 |
| vector | 1 | heldout | kind:cross-language | 0/8 | 0/8 | 3320 |
| vector | 1 | heldout | kind:harm-control | 0/0 | 0/0 | 830 |
| vector | 1 | heldout | kind:lexical-control | 1/2 | 2/2 | 831 |
| vector | 1 | heldout | kind:paraphrase | 0/9 | 0/9 | 3735 |
| vector | 1 | heldout | kind:short-follow-up | 0/3 | 0/3 | 1245 |
| vector | 1 | heldout | facet:cluster | 0/8 | 0/8 | 3320 |
| vector | 1 | heldout | facet:cut | 0/10 | 0/10 | 4150 |
| vector | 1 | heldout | facet:misleading-context | 0/0 | 0/0 | 415 |
| vector | 1 | heldout | facet:near-trap | 0/8 | 0/8 | 3320 |
| vector | 1 | heldout | facet:particle | 0/2 | 0/2 | 830 |
| hybrid | 1 | dev | total | 2/11 | 1/11 | 4983 |
| hybrid | 1 | dev | kind:cross-language | 0/4 | 0/4 | 1660 |
| hybrid | 1 | dev | kind:harm-control | 0/0 | 0/0 | 416 |
| hybrid | 1 | dev | kind:lexical-control | 1/1 | 1/1 | 416 |
| hybrid | 1 | dev | kind:paraphrase | 1/4 | 0/4 | 1661 |
| hybrid | 1 | dev | kind:short-follow-up | 0/2 | 0/2 | 830 |
| hybrid | 1 | dev | facet:cluster | 0/4 | 0/4 | 1660 |
| hybrid | 1 | dev | facet:cut | 0/5 | 0/5 | 2075 |
| hybrid | 1 | dev | facet:near-trap | 0/4 | 0/4 | 1660 |
| hybrid | 1 | dev | facet:particle | 1/1 | 0/1 | 416 |
| hybrid | 1 | heldout | total | 2/22 | 2/22 | 9963 |
| hybrid | 1 | heldout | kind:cross-language | 0/8 | 0/8 | 3320 |
| hybrid | 1 | heldout | kind:harm-control | 0/0 | 0/0 | 831 |
| hybrid | 1 | heldout | kind:lexical-control | 2/2 | 2/2 | 832 |
| hybrid | 1 | heldout | kind:paraphrase | 0/9 | 0/9 | 3735 |
| hybrid | 1 | heldout | kind:short-follow-up | 0/3 | 0/3 | 1245 |
| hybrid | 1 | heldout | facet:cluster | 0/8 | 0/8 | 3320 |
| hybrid | 1 | heldout | facet:cut | 0/10 | 0/10 | 4150 |
| hybrid | 1 | heldout | facet:misleading-context | 0/0 | 0/0 | 415 |
| hybrid | 1 | heldout | facet:near-trap | 0/8 | 0/8 | 3320 |
| hybrid | 1 | heldout | facet:particle | 0/2 | 0/2 | 830 |
