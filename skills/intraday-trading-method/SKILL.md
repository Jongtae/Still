---
name: intraday-trading-method
description: A method for intraday trading on short closed bars within the owner's own limits - a judgment of the day's regime and macro backdrop recorded before one method is chosen and kept, closed-bar data, an in-session regime check with hysteresis, limit-only entries that never chase, ATR exits with the owner's stop and a time exit, stop-sized positions, a backtest gate before a rule goes live, a plan that a condition check carries out between runs, and every entry and skipped signal recorded to settle later. Use when an owner's line or request trades or plans intraday trades. It grants nothing.
license: AGPL-3.0-only
metadata:
  version: "3"
---
# An intraday trading method

Use this when the owner asks you to trade, plan or review intraday trades on any exchange-traded instrument from short bars (such as 1, 5 or 15 minutes), including a recurring line that plans on a schedule and leaves a condition check to act between runs. It covers judging the day and keeping one method for it, what data to trust, entries, exits, size, testing a rule change and recording each decision.

Do not use it to choose the owner's goal, instruments, budget, stop, daily loss limit, trade count or hours: those come from the owner's line or request, never from this skill. Its numbers are method defaults, tuned only through the backtest gate (section 9), never an owner limit. It does not predict prices and is not advice. It also grants nothing: an order still needs the owner's approval, or the owner's standing autonomy setting, exactly as before, and it never widens a budget, a stop or an approval.

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

## 3. Judge the day before choosing a method

A method that fits one kind of day loses on another, and a rule improvised fresh each run turns the result into a coin toss on which rule was picked. So before the first entry of a session, judge what kind of day it is, write the judgment down, and only then choose the method (section 4).

**Use only what was public at the decision time.** Each input carries the time it became public; an input published later cannot feed an earlier decision. When you replay a past session, this matters most: do not let later news, the session's close or later bars into the judgment. Name the source and its time for every input.

1. **Macro backdrop** (before the open, by your own research with `web_search`, a public page or a connected data tool): how the main overseas markets that lead this exchange closed in their last session, and their index futures since; the exchange rate of the instrument's currency against the main reserve currency; the direction of key interest rates or bond yields; and the day's scheduled events (central-bank decisions, major economic releases, large index or derivatives expiries, holidays abroad). Note what each points to: risk-on, risk-off, or no clear lean.
2. **The open:** the gap between the session's first price and the previous close, against its usual size over recent sessions.
3. **The initial range:** the high and low of the first part of the session (for example the first 30 to 60 minutes of closed bars), against the same window's usual width over recent sessions. A narrow initial range that price then leaves and holds outside for several closed bars leans to a trend day; a wide initial range that price keeps returning into leans to a range day.
4. **The in-session regime** (section 5) once enough closed bars exist.

Classify the day as one of:

- **Trend up** or **trend down:** the backdrop, the open and the initial range point the same way, or the in-session regime is trend and nothing material points against it;
- **Range:** no clear lean, a wide initial range that holds, and the in-session regime is range;
- **High volatility:** a large gap, an initial range far wider than usual, ATR far above its recent median, or a major scheduled event inside the session;
- **Unclear:** the inputs disagree or are missing.

Before the initial range is complete, the judgment is provisional: write the backdrop's lean and place no entry that depends on the day type until the initial range is in. Write the judgment in one or two lines with its inputs and sources, for example "risk-off backdrop (overseas indices lower, local currency weaker), gap down larger than usual, narrow initial range broken lower and held: trend down, provisional until 10:00".

## 4. Choose one method for the day, and keep it

Choose the method from the judgment, and write down which and why:

| Day judgment | Method |
|---|---|
| Trend up | Trend pullback (section 6) on the instrument that gains when the market rises |
| Trend down | Trend pullback on an instrument the owner allowed that gains when the market falls (an inverse fund, or a short if the owner's line allows shorting); otherwise stand aside |
| Range | Range reversion (section 6), or stand aside when the range is too narrow to cover costs |
| High volatility | Stand aside, or the matching method at reduced risk (a half or less of the normal risk per trade) after the volatility has eased for several closed bars |
| Unclear | Stand aside and record why |

- **Use this method; do not improvise another.** When the owner names no rule, this skill's methods are the rule: say so ("the method I use when you name no rule"), not that you chose one because none was given. A rule outside these methods (another crossover, a breakout, other thresholds) is a rule change: it goes through the backtest gate (section 9) before it trades, and until then it is at most observed.
- **Keep it for the session.** Every later run of the same session reads the recorded judgment and method and continues with them.
- **Switch only on a clear change,** and record the reason: the in-session regime changes under its hysteresis and persistence rules (section 5) against the day judgment, the initial range is left and held in the other direction, or unscheduled news of the size of a scheduled event arrives. At most one method switch per session in the usual case, and never a switch just because the method has lost or has not traded yet. When a switch would come late in the session, stand aside instead.
- **Read what the line learned first.** When this Work belongs to a line, read its working lessons and settled decisions (they come with the run, or through `entrusted_line` `read`) before choosing, and look for lessons about the same day type. A lesson can make you stand aside on a day type where the method has been losing, or prefer one of the listed methods over another; it cannot add a method outside the table without the backtest gate.

## 5. In-session regime, with hysteresis

Classify each instrument on closed bars before looking for a signal. This is the in-session check that confirms the day judgment (section 3) or shows a clear change (section 4):

- **Trend:** ADX(14) at or above 25, and price on the same side of EMA(50) as the slope of EMA(50) over the last 10 bars.
- **Range:** ADX(14) below 20 and EMA(50) roughly flat.
- **Unclear:** anything else, including ADX between 20 and 25 with no previous regime.
- **Volatility:** ATR(14) ÷ price. Very low volatility makes costs dominate (check them with a cost method if one is available); very high volatility, such as ATR far above its recent median, means stand aside.

Regimes flip back and forth when the reading sits near a threshold, and a method that flips every run trades noise. So:

- **Hysteresis:** enter trend at ADX ≥ 25 but leave it only when ADX falls below 20; enter range below 20 but leave it only above 25.
- **Persistence:** change the regime only after the new reading has held for 3 closed bars in a row.
- **Carry it forward:** write the current regime and the time it started into the plan, and read the previous plan or record at the start of each run. A new run continues the earlier regime unless the change rules above are met.
- **At most one change per instrument per hour.** If the readings would change it more often, the instrument is unclear: stand aside.

**When to stand aside:** unclear regime, stale data, a session edge (first bars, the last part of the session before the time exit), very high volatility, a pending event the owner or a reliable source names, or a daily loss stop already hit. Standing aside is a decision too: record it (section 11).

## 6. Signals by method

- **Trend pullback:** buy pullbacks in the direction of the day, not breakouts after a run. In a trend-down day this applies to the instrument the owner allowed that gains when the market falls, judged on its own bars. A signal is a closed bar that touches or comes near EMA(20) while price stays above VWAP and EMA(50). Do not buy the instrument that loses in the day's direction.
- **Range reversion:** mean reversion. A signal is a closed bar below the lower Bollinger band (20, 2) with RSI(14) at or below 30, while price is not in a fresh downtrend.
- Only one signal per instrument per bar, and no new signal while a position or an open entry order is live in that instrument.

## 7. Entries: limit orders only, never chase

Chasing is the most common way an intraday plan turns a good idea into a bad fill: the limit is raised again and again while price runs away, and the position opens at the top.

- Enter only with a **limit order**. Never use a market order to enter.
- **Anchor the entry to the level the signal refers to.** For a trend pullback that is EMA(20); for range reversion it is the lower Bollinger band. VWAP stays a filter of the trend pullback signal (section 6), not its entry price: on a trend day price holds away from VWAP, so a limit or ceiling tied to VWAP would never meet the pullback the method looks for.
- Set the limit at or below the lower of the last close and the anchor, minus one tick.
- **Ceiling:** never place or move a buy limit above anchor + 1 × ATR(14) (for a sell, below anchor − 1 × ATR). If the last close is already past the ceiling, the pullback is over and there is no entry: record the skipped signal.
- **No upward re-pricing.** Once placed, a buy limit is never raised. If it does not fill within the time the plan states (for example 3 bars), cancel it and record it as unfilled. A later signal gets a new limit computed from scratch, still under the ceiling. At most one new limit per instrument per signal.
- Do not average down, and do not add to a losing position.

## 8. Exits and position size

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

## 9. Backtest gate before a rule goes live

Before a new rule or a changed parameter (a threshold, a multiple, a bar length) trades with money, test it on history in `code_run`. Running the same unchanged rules again does not need a new test.

- Packages, pinned: `backtesting==0.6.6` (AGPL-3.0) for the backtest and `pandas-ta-classic==0.9.0` (MIT, imported as `pandas_ta_classic`) for indicators, plus `pandas==3.1.1` and `numpy==2.5.5` (exact versions: `code_run` accepts only `name==version`). `TA-Lib` (BSD-2-Clause) is optional for speed. Stay well within `code_run`'s package limit and its time limit.
- Data: the full history is at least 20 recent sessions of the same bar length and instrument, closed bars only, from the owner's broker or data tool and the bars earlier runs saved.
- Costs: the broker's real commission on each side, taxes, and slippage of one tick per fill.
- **Full gate**, when that history is available: at least 30 trades, positive expectancy after costs, and a maximum drawdown no larger than three times the owner's daily loss stop. Pass: the rule trades at normal size. Fail: the rule stays in observe-only mode: you plan and record signals (section 11) but place no order with it.
- **Data-limited mode**, when the source cannot supply that history (for example a broker that returns only today's bars): run the same test on whatever closed-bar history you have and state how much (sessions, bars, trades). If the result is clearly negative after costs (negative expectancy over at least 10 trades), the rule stays observe-only. Otherwise, including when there is too little history to test, the rule may trade at reduced risk: a quarter of the normal risk per trade (section 8) and at most two new entries per day, with every owner limit unchanged and every decision recorded (section 11). Say plainly in the plan and in each report that the rule is data-limited.
- **Let history accumulate.** Each run saves the closed bars it fetched, by your own means (for example a file per instrument and session in the line's workspace), adding to what earlier runs saved and never keeping a forming bar. Read the saved bars first when you run the gate. Once they cover the full history above, the full gate applies and decides between normal size and observe-only.
- **Recorded outcomes also decide.** A rule whose settled decisions (section 11) turn clearly negative after costs, by the same measure, returns to observe-only whatever its backtest said, until a changed rule passes the gate.
- Report the result in the plan: the mode (full or data-limited), sessions tested, trades, win rate, average gain and loss after costs, expectancy, maximum drawdown and pass or fail. A sketch is in `references/backtest-gate.md`; read it with `skill_resource` when you run the gate.

Data-limited mode lowers risk; it never widens a budget, a stop, a daily loss stop or an approval, and it grants nothing.

A backtest is evidence about the past under assumptions, not a promise. Say so when you report it.

## 10. Plan, then let the condition check act

On a recurring line, each run plans and the line's condition check carries the plan out between runs. A run is short, so:

- **Finish inside the run's time.** Install the packages once per run, fetch only the bars you need, compute in one `code_run` call where you can, and leave time to write the plan. If time runs short, write a no-new-entry plan rather than none.
- **Write the plan as conditions the check can evaluate without judgment:** the day judgment and the method (sections 3 and 4) with the time they were set; for each instrument, the regime and since when; either no entry (and why) or the exact limit price, the ceiling, the quantity, the cancel-after time, the stop, the take profit and the time exit; and the daily loss figures used. Every number is concrete and every time is in the exchange timezone.
- The condition check only does what the plan states. It never re-prices, never widens a stop, and never opens a position the plan did not state. Anything the plan did not foresee waits for the next run.
- The next run starts by reading what actually happened (fills, cancels, exits, realised profit and loss from the account), not what the plan expected, and continues from there.

## 11. Record each decision so it can be settled later

Record the day judgment and method choice, every method switch, every entry, every exit decision, every skipped signal and every stand-aside as a decision whose outcome can be checked later. When the Work belongs to an entrusted line, use `entrusted_line` with `action` `decide` (`about`, `claim`, `expectation`, `settle_after`, and `check` and `lessons_applied` where they apply); otherwise keep the same fields in the plan and the answer. Skipped signals matter as much as trades: they are how the line learns whether standing aside was right.

Each record states:

- **about:** the instrument, or the session for the day judgment;
- **claim:** starting with the day judgment and the method, for example "day: range; method: range reversion", then the signal and why you acted or stood aside, in one or two lines. Keep the same words for the same day type and method every time, so outcomes can be compared by them;
- **checkable expectation:** a measurable outcome, for example "hits the take profit before the stop", "return to the session close is positive" or, for a skip, "price did not reach the take profit level that an entry would have had";
- **settle by:** when the outcome can be known (for example the session close or a stated time), as `settle_after`;
- **how to check:** the data and calculation that settles it (fetch the closed bars after the decision and compute the metric), as `check`;
- **lessons used:** any earlier lesson or settled outcome you relied on, as `lessons_applied`.

For the day judgment itself, the expectation is what the day type predicts, for example "the session closes outside the initial range in the judged direction" for a trend day or "the session closes inside the initial range" for a range day, settled at the close. That is how the line learns whether its judgments are right, apart from whether its trades were.

When a run starts, settle the records whose settle-by time has passed, by checking the data, before planning (`entrusted_line` `settle` on a line). State each one plainly as met, not met or unknown, and use what was learned when you plan.

**Learn by day type and method.** After settling, when several settled decisions share a day type and method, write or update a short lesson that names both and what the settled outcomes showed, with the decisions it rests on (`entrusted_line` `learn` on a line), for example "range day, range reversion: four settled, three not met; stand aside on narrow range days". Do not write a lesson from one outcome. Read these lessons before choosing the next method (section 4). A lesson that would change a rule's thresholds or add a method goes through the backtest gate first.

## 12. Reporting to the owner

Say in passing which day judgment and method you used, and when and why you switched if you did, for example "I judged a range day (wide initial range, no clear lead from overseas), so I used range reversion". Report what was observed, not what was planned: fills, exits, cancels and profit and loss net of costs from the account. Keep planned, placed, filled and unknown apart. When nothing was traded, say why in one line (which rule stood aside). Never claim an order was placed or filled without the broker's result.
