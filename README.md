# Polymarket sports research

This project investigates sports-feed timing, state-based prices, favorite calibration,
and copying informed wallets. It is a research tool; no real-money orders are placed.
The recovered [baseball](reports/research/BASEBALL_CONTINUATION.md),
[football](reports/research/fb-key-number-wp-step.md) and
[received-book](reports/research/LIVE_EXECUTION.md) studies now use corrected cash and timing.

**No executable trading edge has been established.** The repairs and corrected reruns are
complete. The [viability assessment](reports/VIABILITY.md) gives the bottom line and ranks
every corrected result. The [sports map](reports/SPORTS.md) covers the twelve selected
football, soccer and baseball hypotheses. The two strongest leads, a 10¢ MLB inning-state
discount and a soccer added-time leader rule, are exploratory. They rest on optimistic
historical execution, and neither is verified as tradable.
The [independent review](reports/PEER_REVIEW.md) maps Jeremy's original prompts to the
implementation. The [repair checklist](reports/REPAIR_CHECKLIST.md) and
[validation evidence](reports/REPAIR_VALIDATION.md) track what has actually been verified.
Older strategy reports and desk studies without a corrected rerun listed there, such as
[THORP.md](reports/THORP.md), remain archived exploratory work. July–September 2026 has already been inspected and is not a fresh
confirmatory holdout. Dollar weighting is legitimate when the weights and available
capital are known at decision time; disagreement with equal weighting alone proves no artifact.

The full, unsampled [wallet study](reports/WALLETS.md) ranks 833,133 wallets on
pre-2026 information. Copying the 97 selected wallets' 2026 trades 30 seconds later
loses 0.85% on $57,564 of allocated capital, with a nominal interval of −2.9% to +1.3%.
Faster copying, monthly reselection and large-trade following do not produce a reliable
positive result either.

A recovered September 19 live capture contains 61 scoring events across 14 games with a
subsequent price move of at least three cents. Median book half-move time was 2.86 seconds
after the retrospective contact clock, versus 41.10 seconds for the MLB feed and 71.41
seconds for the Polymarket sports feed. This selected sample weighs against reacting to
these free feeds before the market moves; it does not measure a trading return or all events.

The separate [received-book replay](reports/research/LIVE_EXECUTION.md) covers all observed
score increases and a literal mean-reversion exit rule on that capture. The fixed mean rule
lost $87.79 across 12 positions (−7.27% including entry and exit fees). Three-second score
reaction returned −4.57% using MLB receipts and −7.47% using Polymarket receipts. These
are hypothetical depth crossings on one already inspected day, with order acceptance and
historical minimum-size constraints unobserved; they do not establish future returns.

## Forward paper test (live since 2026-09-25)

Polymarket has no paper-trading mode, and this test needs no account. Instead, a frozen engine
applies the two leads to live public data as it arrives. It simulates each order against the
order book exactly as received, and **never sends an order**. The rules were frozen before any
data they judge existed. Test v1 was activated at 06:45:29 UTC on 2026-09-25
([manifest](reports/paper/ACTIVATION.json)). Only games scheduled to start after that moment count.

- **Soccer added-time leader** ([protocol](reports/PROSPECTIVE_SOCCER_PROTOCOL.md)): the endpoint
  is 200 funded games, expected in roughly two to three months.
- **MLB 10¢ inning discount** ([protocol](reports/PROSPECTIVE_PROTOCOL.md)): the endpoint is 500
  games. About 90 games remain in 2026, so it mostly accrues during the 2027 season.

At today's 0.05 fee, the MLB lead's historical interval already includes zero. The soccer lead's
interval stays positive. The engine refreshes `data/paper/PAPER_TEST.md` and the research desk's "Forward paper tests" group every 15 minutes. [PAPER_TEST.md](reports/PAPER_TEST.md) is a committed snapshot, updated at milestones. The
design is in
[pmsports/paper/DESIGN.md](pmsports/paper/DESIGN.md).

Polymarket US is a separate exchange with its own order book and a 0.0695 taker fee coefficient
from 2026-09-25. This test observes the international book and reports the US fee only as a
re-pricing scenario.

Three user services run the test:

| Service | Role |
|---|---|
| `pmsports-record.service` | Captures books, MLB linescores, ESPN soccer state and the Polymarket sports feed. |
| `pmsports-paper.service` | Runs the engine; it settles positions and refreshes the report periodically. |
| `pmsports-rotate.timer` | Gzips capture days more than two days old. |
| `pmsports-paper-us.service` | Runs the Polymarket US engine. |

A second frozen test runs the same triggers against the **Polymarket US** order book
([protocol](reports/PROSPECTIVE_US_VENUE_PROTOCOL.md)), using US fees and tick sizes. It
was activated at 04:32:14 UTC on 2026-09-26. At each simulated entry it asks the exchange
for an order *preview*: the exchange validates the order and reports its expected fills,
but never places it. The US client (`pmsports/paper/us_api.py`) can sign only read and
preview requests, and its key lives in the git-ignored `.env`.

[MLB_INNING_PERFORMANCE.md](reports/MLB_INNING_PERFORMANCE.md) runs the historical 10¢ rule on a $100k account. It covers:
- daily returns, drawdown, and Sharpe, Sortino and Calmar;
- $100–$5,000 per-game sizing under each fee schedule;
- real captured order-book depth;
- a variant that switches sides every half-inning.

It is still an in-sample backtest.

## Data and execution rules

- New collection defaults to **no eventual-volume floor**. `--min-volume` is an explicitly
  retrospective restriction. The old corpus selected eventual volume of at least $50k;
  imposing $25k observed before a decision does not recover missing markets.
- Tape upgrades request market creation through closure plus 30 minutes. Missing creation
  metadata triggers a market lookup; otherwise coverage is explicitly bounded from `--since`
  (default January 1, 2025). Delayed games are not clipped at 14 hours. Per-tape manifests bind
  requested windows, collector version, retrieval status and output identity. API-returned
  history still does not prove provider completeness or available order-book depth.
- Pregame decisions occur ten minutes before scheduled start. Reference price and liquidity
  use only earlier prints. The entry proxy is the first same-side print more than five seconds
  later and before start. Its size caps a $100 target including fees; unfilled signals remain
  counted. Legacy `ask` columns hold these proxies, not historical quotes.
- Shared research rebuilds preserve existing integer market/wallet IDs, including missing or
  empty former tapes. New IDs append. Code and input identities accompany each generation;
  an interrupted promotion fails closed. Legacy caches remain readable with an explicit audit
  warning. Do not run cache writers concurrently with research readers.
- Historical taker prints alone cannot establish a follower's executable fill, queue position,
  simultaneous multi-leg prices, or cancellation success during a sports delay. Fee assumptions
  must use each market's metadata. Current venue rules are documented in
  [fees](https://docs.polymarket.com/trading/fees) and
  [order lifecycle](https://docs.polymarket.com/concepts/order-lifecycle).
- The [shared execution protocol](reports/EXECUTION_PROTOCOL.md) requires a strictly later
  acquired-side print, excludes a copied leader's own transactions, consumes each print's
  shares once, and retains partial fills and no-fills. Actual allocated capital includes fees.
  The compact cache preserves 53,771,660 normalized source rows across 38,370 market IDs.
  Three corrupt negative prices are excluded before volume, ranking and execution calculations;
  the full price analysis uses 53,771,657 valid-price prints. Existing market IDs are preserved.
  This remains a selected historical sample.

## Run

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pmsports --help
.venv/bin/python -m pmsports games
.venv/bin/python -m pmsports fetch
.venv/bin/python -m pmsports panel
.venv/bin/python -m pmsports live-latency --day 2026-09-19
.venv/bin/python -m pmsports universe
.venv/bin/python -m pmsports tapes --max-markets 100 --since 2025-01-01
.venv/bin/python -m pmsports favorites
.venv/bin/python -m pmsports.research.common build
.venv/bin/python -m pmsports wallets-report
.venv/bin/python -m pmsports.webapp
.venv/bin/python -m pytest -q tests
```

Data and raw live captures stay in ignored `data/`. Full collection/rebuilds are substantial
jobs; choose explicit limits and monitor resource usage. Failed tape upgrades preserve the
previous valid file. Versioned caches invalidate when inputs or dependent code change.
The research desk uses port 8808 and supports sport/period filters, paginated complete
ledgers, and noindex/robots exclusion. Build/startup alone is not browser or strategy validation.

## Sources

Gamma supplies market metadata; the Data API supplies attributed taker prints; CLOB supplies
price history and live order-book updates; MLB Stats API supplies play-by-play and schedules.
The local historical corpus has no complete order-book archive. Third-party archives may
exist, but access and coverage have not been verified for these studies. The original 2024
election scripts remain unmaintained under `legacy/election_2024/`.

## Layout

```
pmsports/
  http.py          rate-limited, retrying HTTP (per-host token bucket)
  polymarket.py    Gamma discovery, CLOB price history, Data-API trades, fee formula
  mlb.py           MLB schedule + timestamped plate appearances
  collect.py       games table (PM <-> MLB join), resumable per-game fetch
  panel.py         state x price panel, pregame table, MLB-only baseline
  record.py        live websocket recorder (books, PM scores, MLB linescore)
  analysis/
    hypotheses.py  H1 calibration, H2 state tables, H3 out-of-sample backtest, H4 latency
    report.py      renders reports/REPORT.md + CSV + charts
    live.py        per-scoring-play latency from a recorded day
    favorites.py   pregame-favorite (and underdog) hold-to-resolution backtest, all sports
    thresholds.py  price-threshold grid by sport; MLB leader-below-historical-rate rule
    calibration.py price vs realized win rate by sport, and the tradable threshold rules
  wallets/
    universe.py    all resolved sports game markets (Gamma tag 100639), sport family, payouts
    tapes.py       per-market taker tapes; compact categorical loader; budget memory for the full corpus
    skill.py       positions, luck-adjusted z, FDR, copy-trade execution proxies, clustered bootstrap
    study.py       selection rules vs placebo, walk-forward, big-trade signal, skilled decomposition
    onchain.py     CryptoHouse queries (maker/taker profile per wallet)
    report.py      renders reports/WALLETS.md
tests/             unit tests (fee formula, orientation fix, state machine, P&L math)
legacy/            2024 election-arbitrage scripts (unmaintained)
```
