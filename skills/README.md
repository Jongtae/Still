# Still reference skills

This folder holds the reference skills Still publishes. Each one is an [Agent Skills](https://agentskills.io/specification) folder, licensed AGPL-3.0-only like the rest of the repository.

They are not bundled into Still. The owner adds one in conversation ("add a shopping skill"). Still then pins it to an exact commit and checks it like any external skill before installing it, and each skill can be removed on its own (Constitution C16, #974).

## Methods

A method works for any owner, market, shop or service, in the owner's own language. It names no site, provider or region; where it needs one service's details (a map service's link format) it reads them from a file in its `references/` folder.

| Skill | What it is |
| --- | --- |
| `shopping-cart` | A method for changing an online cart safely: exact product, quantity meaning, change only what was asked, read the cart back, and keep product price, delivery fee and total apart for a final-price answer |
| `map-directions` | A method for answers that send the owner somewhere: starting point, when to leave, mode, approximate time, parking, and one route link in the owner's preferred map service (link references: KakaoMap, Google Maps) |
| `summary-or-analysis` | A method for telling which answer the owner asked for (summary, comparison, analysis, recommendation) and when an answer of that form is finished |
| `claim-analysis` | A method for analysing claims and opinions: support, counter-evidence, disagreement, uncertainty and a justified conclusion |
| `source-cross-check` | A method for checking a fact across sources: the original, independent confirmation, dates and definitions, and an honest report of agreement or conflict |
| `first-conversations` | A method for first conversations and missing owner facts: open with what the AI is good at and one easy start, use the best evidence it has as a stated assumption, and ask one gentle, task-framed question only when a request needs it |
| `trading-costs` | A method for trading costs: before an order, check that the expected gain survives commission, tax and spread, tell the owner instead of ordering when it does not, and report profit and loss net of costs. It names no asset, entry or trade, so it is not advice |
| `intraday-trading-method` | A method for intraday trading on short closed bars inside the owner's own limits: closed-bar data, regime with hysteresis, limit-only entries under a VWAP + 1×ATR ceiling with no upward re-pricing, ATR take profit with the owner's stop and a time exit, stop-sized positions and a daily loss stop, a backtest gate before a rule change trades, a plan a condition check carries out between runs, and every entry and skipped signal recorded to settle later. It sets no owner limit and grants nothing |

## Site and region know-how

A site skill adds what is known about one site or service to a method: where things are, how sign-in works, what differs there. It is useful only to owners who use that site, and is an example of the pattern rather than a recommendation of the site. It carries no account, address or order data, and every hint is checked on the page.

| Skill | What it is |
| --- | --- |
| `emart-ssg` | Site know-how for Emart mall on SSG.COM, from redacted Still observations. Use it with `shopping-cart` |

Site hints are observations, not guarantees. Pages change, and the method tells the AI to check each hint on the page.

## Adding a skill

A new method belongs here when it is a reusable way of working, not the script of one request or one evaluation scenario (Constitution C16). A new site or region skill follows `emart-ssg`: it says which method it extends, records where its hints came from (with personal details removed), and adds no payment, account or personal data. A new map service is a new file in `map-directions/references/` taken from that service's own documentation.
