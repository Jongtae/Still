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
| `rule-driven-flow-research` | A method for researching trades that rule-bound money must make (leveraged and inverse fund rebalancing, index changes and similar flows): official rule and source, point-in-time inputs, gross → net → residual estimates with ranges, pressure against predicted volume, an already-priced check, two hypotheses tested apart, and a test with baselines, stress, out-of-sample and every trial recorded. It predicts no price and grants nothing |

## Site and region know-how

A site skill adds what is known about one site or service to a method: where things are, how sign-in works, what differs there. This repository publishes methods, plus `emart-ssg` as one example of the site-skill format. It is not a recommendation of the site.

| Skill | What it is |
| --- | --- |
| `emart-ssg` | Site know-how for Emart mall on SSG.COM, from redacted Still observations. Use it with `shopping-cart` |

Site hints are observations, not guarantees. Pages change, and the method tells the AI to check each hint on the page.

## Adding a skill

### Site or region skills: your own repository

Publish a new site or region skill as an Agent Skills folder in your own GitHub repository, not here. The owner adds it by giving Still the folder address:

`github.com/<owner>/<repo>/tree/<ref>/<folder>`

Still pins the address to one commit, checks the folder like any external skill (for example, it needs a recognised licence and no scripts), and installs it only after the owner confirms. The repository must be public, and the folder name must match the skill's `name`.

A good site skill:

- says which method it extends;
- records where its hints came from, with personal details removed;
- carries no account, address, order, payment or other personal data;
- offers hints that are checked on the page.

### What belongs here

- A new method, when it is a reusable way of working, not the script of one request or one evaluation scenario (Constitution C16).
- A new map-service link reference in `map-directions/references/`, taken from that service's own documentation.
