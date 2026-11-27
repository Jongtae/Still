# Backtest gate sketch

A starting point for section 7 of the method, for `code_run` with the packages
`backtesting==0.6.6`, `pandas-ta-classic==0.9.0`, `pandas` and `numpy`. Adapt it to
the rule you are testing; it is a sketch, not a finished strategy.

Inputs you supply in the code:

- `bars`: a pandas DataFrame of closed bars indexed by timestamp in the exchange
  timezone, with columns `Open`, `High`, `Low`, `Close`, `Volume`, covering at least
  20 sessions for the full gate (fewer in data-limited mode), from the owner's
  broker or data tool joined with the closed bars earlier runs saved;
- `cash`: the owner's budget;
- `commission`: the broker's real commission as a fraction of the order value
  (add taxes that apply to every fill, or pass a function of size and price);
- `tick`: the instrument's tick size;
- `stop_frac`: the owner's stop as a fraction of the fill price;
- `daily_loss_stop`: the owner's daily loss stop, in money;
- `risk`: the money risked per trade (a fraction of `daily_loss_stop`);
- `close_time`: the time exit, as `datetime.time`.

```python
import numpy as np
import pandas as pd
import pandas_ta_classic as ta
from backtesting import Backtest, Strategy


def vwap(df):
    day = df.index.date
    price = (df['High'] + df['Low'] + df['Close']) / 3
    return (price * df['Volume']).groupby(day).cumsum() / df['Volume'].groupby(day).cumsum()


class Pullback(Strategy):
    atr_mult_tp = 1.5
    ceiling_atr = 1.0
    cancel_bars = 3

    def init(self):
        df = self.data.df
        self.ema20 = self.I(lambda: ta.ema(df['Close'], 20).to_numpy(), name='ema20')
        self.ema50 = self.I(lambda: ta.ema(df['Close'], 50).to_numpy(), name='ema50')
        self.atr = self.I(lambda: ta.atr(df['High'], df['Low'], df['Close'], 14).to_numpy(), name='atr')
        self.adx = self.I(lambda: ta.adx(df['High'], df['Low'], df['Close'], 14)['ADX_14'].to_numpy(), name='adx')
        self.vwap = self.I(lambda: vwap(df).to_numpy(), name='vwap')
        self.placed_at = None

    def next(self):
        now = self.data.index[-1]
        if now.time() >= close_time:  # time exit: flat, no open entry
            for order in self.orders:
                order.cancel()
            if self.position:
                self.position.close()
            return
        # cancel an unfilled entry after cancel_bars; never re-price it upward
        if self.position:  # filled: its stop and take profit stay in force
            self.placed_at = None
            return
        if self.orders and self.placed_at is not None and len(self.data) - self.placed_at >= self.cancel_bars:
            for order in self.orders:
                order.cancel()
            self.placed_at = None
        if self.orders:
            return
        atr, close = self.atr[-1], self.data.Close[-1]
        if np.isnan(atr) or np.isnan(self.adx[-1]):
            return
        trend_up = self.adx[-1] >= 25 and close > self.ema50[-1] and self.ema50[-1] > self.ema50[-10]
        near_ema = self.data.Low[-1] <= self.ema20[-1] * 1.001 and close > self.vwap[-1]
        if not (trend_up and near_ema):
            return
        ceiling = self.vwap[-1] + self.ceiling_atr * atr
        limit = np.floor((min(close, self.vwap[-1], self.ema20[-1]) - tick) / tick) * tick
        if limit > ceiling:
            return  # past the ceiling: a skipped signal, no entry
        stop = max(limit * (1 - stop_frac), limit - 1.5 * atr)
        qty = int(min(risk // (limit - stop), cash // limit))
        if qty < 1:
            return
        self.buy(size=qty, limit=limit, sl=stop, tp=limit + self.atr_mult_tp * atr)
        self.placed_at = len(self.data)


bt = Backtest(bars, Pullback, cash=cash, commission=commission, spread=tick / bars['Close'].median(),
              trade_on_close=False, finalize_trades=True)
stats = bt.run()
trades = stats['# Trades']
expectancy = stats['_trades']['PnL'].mean() if trades else float('nan')
sessions = len(set(bars.index.date))
if sessions >= 20:  # full gate: normal size or observe-only
    mode = 'full'
    passed = trades >= 30 and expectancy > 0 and -stats['Max. Drawdown [%]'] / 100 * cash <= 3 * daily_loss_stop
    decision = 'normal size' if passed else 'observe-only'
else:  # data-limited: observe-only only when clearly negative, otherwise reduced risk
    mode = 'data-limited'
    clearly_negative = trades >= 10 and expectancy < 0
    decision = 'observe-only' if clearly_negative else 'reduced risk'
print({'mode': mode, 'sessions': sessions, 'bars': len(bars), 'trades': int(trades),
       'win_rate_pct': stats['Win Rate [%]'], 'expectancy': expectancy,
       'max_drawdown_pct': stats['Max. Drawdown [%]'], 'return_pct': stats['Return [%]'], 'decision': decision})
```

Notes:

- `spread` here stands in for one tick of slippage per fill; use the instrument's
  real spread when you know it.
- `stats['_trades']['PnL']` is after commission. Check that taxes are inside
  `commission`, or subtract them.
- Do not tune parameters on the same sessions you report. If you try several
  values (`bt.optimize`), hold back the most recent sessions and report the gate
  on those.
- With no trades at all (too little history), `expectancy` is NaN and the
  data-limited decision is reduced risk; say that nothing could be tested.
- Print only the summary; `code_run` output is bounded.
