---
name: trading-costs
description: A method for checking, before placing a buy or sell order, whether the expected gain survives commission, tax and spread; telling the owner instead of placing the order when costs would eat the gain; and reporting profit and loss net of costs. Use when the owner asks you to trade, or to report how a trade went. It is a cost check, not trading advice.
license: AGPL-3.0-only
metadata:
  version: "1"
---
# Checking trading costs before an order

Use this when the owner asks you to place, plan or review a buy or sell of any traded asset (shares, funds, currencies, coins or anything else with an order fee), and when you report how a trade turned out. It applies to any market, broker and amount.

Do not use it to choose what to trade, when, or how much. It never names an asset, an entry, a price target or a size, and it gives no view on where a price will go: those decisions stay the owner's. It also grants nothing: an order still needs the owner's approval, or the owner's standing autonomy setting, exactly as before.

## 1. Find the real costs

Take every cost from the account or the broker's own fee schedule for this order, not from memory or a typical figure:

- **Commission** on each side: buy and sell. Note any **minimum fee** per order; on a small order it can be most of the cost.
- **Taxes and levies**: transaction tax, stamp duty, exchange or regulatory fees, and on which side each applies (often only the sell).
- **Spread**: the gap between the best buy and sell price now. Buying at the ask and selling at the bid loses the spread even if the price does not move.
- **Other**: currency conversion, platform or network fees, if this order has them.

If you cannot find a cost, say which one is unknown and treat the check as incomplete. Do not assume it is zero.

## 2. Compute the round-trip cost and the break-even move

For a position you will open and later close, add both sides:

- round-trip cost = buy commission + sell commission + taxes and levies + spread + other fees, in money for this order size;
- break-even move = round-trip cost ÷ position value, as a percentage.

The price has to move by more than the break-even move, in the owner's favour, before the trade makes anything. Selling at the same price you bought is always a loss of the round-trip cost.

For a single order that closes an existing position, use the costs of that order plus those already paid to open it.

## 3. Compare with the expected gain before you place the order

The expected gain is the price move the owner's plan aims for (their target, or their sell price against the buy price), in money for this order size. Write both numbers down:

- **Gain clearly larger than cost:** go ahead as the owner asked, and mention the cost in one line.
- **Cost equal to or larger than the gain, or a planned sell at or below break-even:** do not place the order. Tell the owner, in one short message, the expected gain, the round-trip cost, the break-even move, and what would change it (a larger amount spreads a minimum fee; a wider target clears the cost). Then wait for their decision.
- **Gain unknown** (no target given): give the break-even move so the owner can judge, and proceed only as they decide.

When a request asks for many short trades in a time box, check this for each order, and say before starting if the order size makes costs dominate every trade.

## 4. Report profit and loss net of costs

When you report a trade or a session:

- give gross P&L (price difference × quantity), then every cost actually charged, then **net P&L**;
- take charged costs from the account's trade records when you can, and say when a figure is your estimate;
- report an open position's result as unrealised, with the costs still to pay on closing;
- never present gross P&L as the result.

Keep currencies, quantities and prices exactly as the account shows them.
