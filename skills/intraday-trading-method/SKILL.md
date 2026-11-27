---
name: intraday-trading-method
description: A method for intraday trading on short closed bars within the owner's own limits - closed-bar data, regime with hysteresis, limit-only entries that never chase, ATR exits with the owner's stop and a time exit, stop-sized positions, a backtest gate before a rule goes live, a plan that a condition check carries out between runs, and every entry and skipped signal recorded to settle later. Use when an owner's line or request trades or plans intraday trades. It grants nothing.
license: AGPL-3.0-only
metadata:
  version: "1"
---
# An intraday trading method

Use this when the owner asks you to trade, plan or review intraday trades on any exchange-traded instrument from short bars (such as 1, 5 or 15 minutes), including a recurring line that plans on a schedule and leaves a condition check to act between runs. It covers what data to trust, when to stand aside, how to enter without chasing, how to exit and size, how to test a rule change, and how to record each decision so it can be checked later.

Do not use it to choose the owner's goal, instruments, budget, stop, daily loss limit, trade count or hours: those come from the owner's line or request, never from this skill. Its numbers are method defaults, tuned only through the backtest gate (section 7), never an owner limit. It does not predict prices and is not advice. It also grants nothing: an order still needs the owner's approval, or the owner's standing autonomy setting, exactly as before, and it never widens a budget, a stop or an approval.

## 1. The owner's limits come first

Before anything else, read from the owner's line or request, and write down in your plan:

- the instruments allowed, the budget, the stop per position, the daily loss stop, any maximum number of trades or open positions, and the hours the owner allows;
- what the owner wants this line to achieve.

If one of these is missing, use the most cautious reading (no new position) and say which limit you lacked. Never widen a limit because a signal looks strong. A limit the owner set is a hard rule: the method below only decides what to do inside it.

## 2. Data: closed bars only

- Get bars and quotes from the owner's connected broker or market-data tool. Use a public source only for reference data the broker lacks, and say which source each figure came from.
- **Act only on closed bars.** The last bar is closed only when its end time is at or before the data source's current time. Drop a bar that is still forming; never compute a signal or an entry from it.
- Keep every timestamp in the exchange's own timezone, and take session hours, holidays, half days and auctions from the exchange calendar. Do not mix sessions: the first bars after the open and the last bars before the close (auction periods) are not normal bars; skip the first one or two bars of the session for signals.
- **Stale data stops the run.** If the newest closed bar is older than about two bar lengths during the session, or bars are missing, do not plan new entries. Say the data was stale and keep existing exits in force.
- Note the instrument's tick size and lot size from the broker. Every price you plan is rounded to a valid tick (buy limits down, sell limits up).

## 3. Regime, with hysteresis

Classify each instrument on closed bars before looking for a signal:

- **Trend:** ADX(14) at or above 25, and price on the same side of EMA(50) as the slope of EMA(50) over the last 10 bars.
- **Range:** ADX(14) below 20 and EMA(50) roughly flat.
- **Unclear:** anything else, including ADX between 20 and 25 with no previous regime.
- **Volatility:** ATR(14) ÷ price. Very low volatility makes costs dominate (check them with a cost method if one is available); very high volatility, such as ATR far above its recent median, means stand aside.

Regimes flip back and forth when the reading sits near a threshold, and a method that flips every run trades noise. So:

- **Hysteresis:** enter trend at ADX ≥ 25 but leave it only when ADX falls below 20; enter range below 20 but leave it only above 25.
- **Persistence:** change the regime only after the new reading has held for 3 closed bars in a row.
- **Carry it forward:** write the current regime and the time it started into the plan, and read the previous plan or record at the start of each run. A new run continues the earlier regime unless the change rules above are met.
- **At most one change per instrument per hour.** If the readings would change it more often, the instrument is unclear: stand aside.

**When to stand aside:** unclear regime, stale data, a session edge (first bars, the last part of the session before the time exit), very high volatility, a pending event the owner or a reliable source names, or a daily loss stop already hit. Standing aside is a decision too: record it (section 9).

## 4. Signals by regime

- **Trend:** buy pullbacks in an uptrend, not breakouts after a run. A signal is a closed bar that touches or comes near EMA(20) while price stays above VWAP and EMA(50). Sell or stand aside in a downtrend unless the owner's line allows shorting.
- **Range:** mean reversion. A signal is a closed bar below the lower Bollinger band (20, 2) with RSI(14) at or below 30, while price is not in a fresh downtrend.
- Only one signal per instrument per bar, and no new signal while a position or an open entry order is live in that instrument.

## 5. Entries: limit orders only, never chase

Chasing is the most common way an intraday plan turns a good idea into a bad fill: the limit is raised again and again while price runs away, and the position opens at the top.

- Enter only with a **limit order**. Never use a market order to enter.
- Set the limit at or below the lowest of the last close, VWAP and EMA(20), minus one tick.
- **Ceiling:** never place or move a buy limit above VWAP + 1 × ATR(14) (for a sell, below VWAP − 1 × ATR). If the price is already past the ceiling, there is no entry: record the skipped signal.
- **No upward re-pricing.** Once placed, a buy limit is never raised. If it does not fill within the time the plan states (for example 3 bars), cancel it and record it as unfilled. A later signal gets a new limit computed from scratch, still under the ceiling. At most one new limit per instrument per signal.
- Do not average down, and do not add to a losing position.

## 6. Exits and position size

Place the exits together with the entry, so they are in force the moment the entry fills:

- **Stop:** the owner's stop from the fill (section 1). If a tighter technical stop (for example below the signal bar's low or 1.5 × ATR) is closer, use the closer one; never use a wider stop than the owner's.
- **Take profit:** 1.5 to 2 × ATR(14) from the fill, or a return to VWAP in a range trade, whichever comes first.
- **Time exit:** close every intraday position before the session's closing auction, at a time the plan states, and leave no open entry order past it.
- An exit is never moved against the position: a stop may be raised to break-even after a gain of 1 × ATR, never lowered.

**Size from the stop, inside the budget:**

- risk per trade = a fraction of the owner's daily loss stop (for example a half), so that two losing trades end the day;
- quantity = floor(risk per trade ÷ (entry price − stop price)), rounded down to the lot size;
- then cap it so quantity × entry price stays within the owner's budget and the cash actually available. If the capped quantity is zero, there is no trade.
- Count costs. Both sides' commission, taxes and the spread come out of every trade; a target smaller than the round-trip cost is no trade.

**Daily loss stop:** add realised losses, costs included, and open losses at the stop. When a new trade could take the day past the owner's daily loss stop, place no new entry for the rest of the day, keep existing exits in force and tell the owner once. The owner's maximum trades per day, if set, works the same way.

## 7. Backtest gate before a rule goes live

Before a new rule or a changed parameter (a threshold, a multiple, a bar length) trades with money, test it on history in `code_run`. Running the same unchanged rules again does not need a new test.

- Packages, pinned: `backtesting==0.6.6` (AGPL-3.0) for the backtest and `pandas-ta-classic==0.9.0` (MIT, imported as `pandas_ta_classic`) for indicators, plus `pandas` and `numpy`. `TA-Lib` (BSD-2-Clause) is optional for speed. Stay well within `code_run`'s package limit and its time limit.
- Data: at least 20 recent sessions of the same bar length and instrument, closed bars only, from the owner's broker or data tool.
- Costs: the broker's real commission on each side, taxes, and slippage of one tick per fill.
- **Gate:** at least 30 trades, positive expectancy after costs, and a maximum drawdown no larger than three times the owner's daily loss stop. Otherwise the rule stays in observe-only mode: you plan and record signals (section 9) but place no order with it.
- Report the result in the plan: sessions tested, trades, win rate, average gain and loss after costs, expectancy, maximum drawdown and pass or fail. A sketch is in `references/backtest-gate.md`; read it with `skill_resource` when you run the gate.

A backtest is evidence about the past under assumptions, not a promise. Say so when you report it.

## 8. Plan, then let the condition check act

On a recurring line, each run plans and the line's condition check carries the plan out between runs. A run is short, so:

- **Finish inside the run's time.** Install the packages once per run, fetch only the bars you need, compute in one `code_run` call where you can, and leave time to write the plan. If time runs short, write a no-new-entry plan rather than none.
- **Write the plan as conditions the check can evaluate without judgment:** for each instrument, the regime and since when; either no entry (and why) or the exact limit price, the ceiling, the quantity, the cancel-after time, the stop, the take profit and the time exit; and the daily loss figures used. Every number is concrete and every time is in the exchange timezone.
- The condition check only does what the plan states. It never re-prices, never widens a stop, and never opens a position the plan did not state. Anything the plan did not foresee waits for the next run.
- The next run starts by reading what actually happened (fills, cancels, exits, realised profit and loss from the account), not what the plan expected, and continues from there.

## 9. Record each decision so it can be settled later

Record every entry, every exit decision, every skipped signal and every stand-aside as a decision whose outcome can be checked later, using whatever decision or outcome record this Still offers your Work. Skipped signals matter as much as trades: they are how the line learns whether standing aside was right.

Each record states:

- **subject:** the instrument;
- **claim:** the regime, the signal and why you acted or stood aside, in one or two lines;
- **checkable expectation:** a measurable outcome, for example "hits the take profit before the stop", "return to the session close is positive" or, for a skip, "price did not reach the take profit level that an entry would have had";
- **settle by:** when the outcome can be known (for example the session close or a stated time);
- **how to check:** the data and calculation that settles it (fetch the closed bars after the decision and compute the metric);
- **lessons used:** any earlier lesson or settled outcome you relied on.

When a run starts, settle the records whose settle-by time has passed, by checking the data, before planning. State each one plainly as met, not met or unknown, and use what was learned when you plan. A lesson that would change a rule goes through the backtest gate first.

## 10. Reporting to the owner

Report what was observed, not what was planned: fills, exits, cancels and profit and loss net of costs from the account. Keep planned, placed, filled and unknown apart. When nothing was traded, say why in one line (which rule stood aside). Never claim an order was placed or filled without the broker's result.
