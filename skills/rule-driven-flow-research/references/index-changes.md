# Index additions, deletions and weight changes

When an index changes its members or target weights, funds that replicate it must trade to match. This is slower than a daily rebalance (days to weeks), which suits careful document work.

## The estimate

For each security, sum over the funds `f` that actually replicate each affected index:

`adjustment ≈ Σ_f tracking assets_f × (new target weight_f − current weight_f)`

- Value both weights **at the same prices**. A provider's pro-forma weights are computed at its reference prices; compare them with the current weights at those same prices, not at today's.
- In a cap-weighted index, a replicating fund drifts with prices and has nothing to trade from drift alone. Its forced trades come from changes in members, shares, float and capping; work from those changes.
- Drift matters for equal-weight and capped indices, where weights are reset to targets: there, the trade is the gap between the target and the drifted current weight.
- Count only money that must replicate the index (index funds, index mandates). Assets merely benchmarked against it may not trade at all. Keep a range when the split is unknown.
- When a security moves between indices (for example from one size segment to another), net the selling by one group of trackers against the buying by the other.
- Float, share-count and capping changes in the same notice also move weights; include them.

## Dates and documents

From the index provider's own notices, record:

- the announcement, the preliminary list, any updates, the final list, and the effective date and time;
- which list was public at each decision time (section 3 of the method);
- exceptions and corrections, as new records.

Trackers usually trade near the effective time, but some trade earlier or spread it out. Note what you can observe; do not assume all trading lands at one close.

## What tends to go wrong

- Buying a widely known addition late: published research reports that the price effect of index additions has shrunk as more traders anticipate it. What is still ahead and not yet priced matters more than the announcement.
- Counting benchmarked money as forced.
- Using the final list for a decision made when only the preliminary list was out.

## A live observation run

An upcoming reconstitution with published dates is a good first exercise: save the notices and lists as they appear, estimate the adjustments, and compare them with prices and volumes around the effective date. This is observation, not a reason to buy any listed security.
