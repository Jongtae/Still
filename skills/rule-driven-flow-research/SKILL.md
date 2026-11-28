---
name: rule-driven-flow-research
description: A method for researching trades that rule-bound money must make - a leveraged or inverse fund rebalancing to its target exposure, index funds following a reconstitution or weight change, and similar mechanical flows - by estimating who must trade what, when and how much from official rules, checking whether prices already reflect it, and testing it with point-in-time data, real costs and every trial recorded. Use when the owner asks to study, watch or paper-trade such flows. It predicts no price, is not advice and grants nothing.
license: AGPL-3.0-only
metadata:
  version: "1"
---
# Researching rule-driven flows

Use this when the owner asks you to study, watch, paper-trade or review trades that some fund or strategy must make because of its own published rules: a leveraged or inverse fund resetting its exposure, index funds following an index change, a fund rolling futures, a target-volatility strategy cutting exposure. It applies to any market, venue and instrument.

The question is not "where will the price go" but **who must trade, by which rule, by when, in what, and how much**, and whether that trade is still ahead and large enough to matter after costs. Knowing that a forced trade exists does not mean it moves the price, and a price effect does not mean you can capture it after costs.

Do not use it to choose the owner's goal, instruments, budget or limits, or to recommend a trade. It never names an asset to buy or sell and gives no price view. It also grants nothing: an order still needs the owner's approval, or the owner's standing autonomy setting, exactly as before.

## 1. Start from observation, not orders

Unless the owner asks otherwise, run this as research: record each flow event and what the price then did, and paper-trade a rule. Move a rule toward real orders only after it passes the test in section 8 and the owner decides to, inside the owner's own limits. When the owner's line already trades, keep their limits as hard rules; this method never widens one.

## 2. Find the rule and its source

For each flow you study, write down from the **official** source (the fund's prospectus or fact sheet, the index provider's methodology and notices, the exchange's rules):

- the fund or strategy, and the rule that forces the trade (target multiple, index weights, roll schedule, volatility target);
- what it holds or tracks, and **how** it adjusts: shares, futures or swaps. A swap counterparty's hedge may trade elsewhere or at another time;
- the trigger, the reference time it uses, and when the adjustment is made;
- the source address, when it was published, when it takes effect, and the rule version.

If a rule is ambiguous, say so and record the readings; do not pick the convenient one. If the fund or provider changes how or when it adjusts, that is a new rule version: split your history at the change and test each side apart.

## 3. Use only what was known at the time

Every input carries two times: the time it describes and the time it became public. A decision may use an input only if its public time is before the decision time.

- Fund assets, units outstanding and creations or redemptions are usually published after the close. Today's figures cannot feed today's intraday decision; use yesterday's and say so.
- Preliminary and final index lists differ. Use the list that was out at the time.
- Keep corrections as new records; never overwrite what you knew then.

## 4. Estimate gross, then net, then what is still ahead

Compute in steps and keep a range, not one number, at each step. The formulas and their caveats are in `references/leveraged-fund-rebalancing.md` and `references/index-changes.md` (read them with `skill_resource`).

1. **Gross**: the exposure change the rule requires, from the formula.
2. **Net**: after creations and redemptions, and the instrument used. Leveraged and inverse funds on the same underlying do not cancel each other: their daily resets point the same way, so add them. Only creations and redemptions, or a fund whose multiple is between 0 and 1, offset the gross figure.
3. **Residual**: the part still ahead at your decision time. Exclude what has already been done (an earlier rebalance, a fund that adjusts during the day).

If a step cannot be estimated, say which one and keep the range wide. Do not fill an unknown with zero.

## 5. Size it against the market

A residual flow matters only in proportion to what normally trades while it is done:

pressure = residual flow / **predicted** traded value in the window it will be done

Predict the window's traded value from data before the decision (for example, the same window's average over recent sessions), never from the volume that later printed. Note the spread and depth now as well.

## 6. Has the price already moved for it?

Others can do the same arithmetic. Before any entry, compare the move so far with what similar days usually show. If the price has already run ahead in the flow's direction, or the residual range is wide, do not trade: record the event as observed and say why you stood aside.

## 7. Keep the two hypotheses apart

Test each as its own rule, with its own record, and do not switch between them after seeing the result:

- **Ahead of the flow**: the residual is large and not yet priced, so price moves further in its direction while it is done. Check that the edge survives entering one and five minutes later; if a few seconds' delay erases it, it is a speed race, not this method.
- **After the shock**: the flow pushed price away from where other information puts it, and price comes back once the flow is done. The competing explanation is real news (results, filings, rules). Search for it, and treat "found nothing" as unknown, not as "no news". An overnight hold adds gap risk; it is not safer just because it is slower.

## 8. Test before a rule trades

Follow the backtest gate of `intraday-trading-method` when it is installed, and add these checks for flow rules. Its data-limited mode does not apply here: flow events are rare, so a flow rule without enough past events to pass every check below stays paper-only.

- **Baselines**: compare against (a) a plain price rule using the same day's return and trend, (b) the flow formula alone, and (c) the flow formula with your document reading. Flow estimates already contain the day's return; a good result may only be "bought what was rising". Compare days with similar return, volatility and volume.
- **Stress**: higher costs, later entry, partial fills. A rule that disappears under a small change is rejected.
- **Out of sample**: design on one period and test on a later one. Several funds on the same underlying on one day are **one** event, not several.
- **Every trial recorded**: thresholds, variants and failures, not only the best run. Many tries make a lucky backtest likely.
- **Language-model look-ahead**: a model may already know what happened after a past date. In backtests, use the model only to extract facts from documents you give it; judge its forecasting only on records made live, from today on.
- **Fills you cannot know**: model decision time, arrival time, allowed order types and whether it would fill. Buying "at the close" after seeing the close is not executable; an auction fill you cannot check from the data is unknown, not assumed.

Pass needs more than a win rate: net result with its uncertainty, worst loss, dependence on a few special days, persistence after a rule or market change, and size large enough to cover data and running costs.

## 9. Costs

Run the `trading-costs` check for every planned order and every reported result: commission, taxes, spread, impact and financing. Report research results net of costs too. Leverage does not create an edge; it only enlarges a small one together with its losses.

## 10. Record each event so it can be settled

For each flow event keep one record:

- the rule and source (section 2), the inputs with their public times, the gross, net and residual ranges, the pressure, the already-priced check;
- the decision (entered, paper only, stood aside) and why;
- what price then did, and the result net of costs if traded;
- afterwards, **where** the estimate went wrong, if it did: the rule reading, the size, the timing, the price response or the fill.

A retrospective may propose a new rule. A proposal is a candidate for section 8, never a change to the rule that runs.

## 11. Reporting to the owner

Say what the rule required, what was still ahead, what you did and why, including when you did nothing: "The rule called for a large buy today, but net of creations the range is wide and the price had already moved, so the paper rule stood aside." Name each figure's source and whether it is an estimate. Never call a backtest a result, or a paper trade a trade.

## Never

- Never place an order that tries to move a price, fake demand or set a closing price, and never trade on non-public information. Studying public rules is the whole method.
- Never present an estimate as a confirmed order flow, or a past pattern as a promise.
- Never widen a budget, a stop, an instrument list or an approval because a flow looks large.
