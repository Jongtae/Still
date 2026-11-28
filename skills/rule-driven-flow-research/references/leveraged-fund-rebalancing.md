# Leveraged and inverse fund rebalancing

A fund that targets a fixed daily multiple `L` of an underlying's return (for example 2, 3, −1 or −2) must reset its exposure after the underlying moves, or its multiple drifts.

## The gross estimate

With assets `A` at the previous reset, target multiple `L` and underlying return `r` since then, the exposure change needed to restore `L` is approximately

`Q ≈ A × L × (L − 1) × r`

Worked example, ignoring flows, costs and any earlier adjustment: `A` = 1,000, `L` = 2, `r` = +1%.

| | |
| --- | ---: |
| exposure before | 2,000 |
| assets after the move | 1,020 |
| exposure after the move | 2,020 |
| target exposure (2 × 1,020) | 2,040 |
| **change needed** | **+20** |

The same day with `r` = −1% needs −20.

- `L × (L − 1)` is positive for every `L` above 1 **and** every negative `L`: for `L` = −2 it is 6. Inverse funds therefore add to the same direction as leveraged ones (buy after a rise, sell after a fall). Do not net them against each other as opposites.
- For `L` = −1 it is 2, for `L` = 3 it is 6.
- Sum across every fund on the same underlying, each with its own `A` and `L`.

## From gross to what actually trades

- **Creations and redemptions.** Money flowing into or out of the fund changes the exposure it needs. Flows often lean against the day's move and can cancel much of the gross figure. Use the latest published units and assets, at their public time, and keep a range for today's unknown flows.
- **Instrument.** Exposure may be adjusted with futures or swaps, not shares. A swap counterparty hedges on its own schedule and may net the fund against other clients. Do not read the gross figure as share orders at the close.
- **Timing.** Funds have rebalanced near the close, but some adjust during the day or spread it out, and practice can change after regulators or the provider act. Check the current practice from the provider's documents and recent behaviour, and split your history when it changes.
- **Fund trading volume is not rebalancing.** Investors trading the fund's units among themselves change nothing; only creations and redemptions do.

## What to record

`A`, `L` and units with their public times; `r` at the decision time; gross `Q`; the flow range; the instrument; the timing evidence; the net and residual ranges; and the predicted traded value for the window.
