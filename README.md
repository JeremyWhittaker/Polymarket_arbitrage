# Polymarket sports research (MLB)

Tests whether you can make money on Polymarket MLB moneylines by:

1. **Timing**: betting right after something happens in the game (e.g. a home run),
   before the market reprices.
2. **Averages**: using historical "team up X runs going into inning Z" outcomes to
   find prices that are too high or too low, then betting toward the historical
   average and holding to the end of the game.
3. **Pregame favorites**: finding out whether favorites win more often than their
   price implies.

The 2024 election-arbitrage scripts this repo started with are in
[`legacy/election_2024/`](legacy/election_2024/). They rely on the deprecated Goldsky
subgraph, Polygonscan scraping and Selenium, and nothing new depends on them.

Latest results: [`reports/REPORT.md`](reports/REPORT.md).

## Findings so far (2026-09-18: 4,664 games from 2025-26, baseline of 14.5k games from 2021-26)

| Hypothesis | Result |
|---|---|
| Pregame favorites beat their price | **No.** Favorites won 56.1% at an average price of 57.4% (calibration slope 0.84, se 0.08). Betting every favorite: **-4.3%** ROI with the 5% fee. Betting every underdog: +3.1% without fees (CI -0.1% to +6.6%) but **-2.0%** after fee + 1c. |
| "Up X runs going into inning Z" is mispriced | **No.** In every inning/lead cell, the market price matches the realized win rate within noise. Example: leading by 2 into the 7th, priced 0.824, won 0.833. |
| Trade toward a fair value (model or state average), out of sample | **No.** The market forecasts better than the model (log-loss 0.518 vs 0.527), and the stacking coefficient is ~0 (+0.003, se 0.091). The literal "bet toward the state's average price" rule loses **7-14%** in 2026. |
| Bet after seeing a play, before the market moves | **Not from a TV or a free feed.** Across 6,471 historical scoring plays, trades reach halfway to the new price a median ~7s after contact. Live ms-clock capture (8 plays so far): the **order book is halfway re-priced in 3.5s** (sometimes <1.5s), while **Polymarket's own score feed and the free MLB API update ~27s after contact**. Stale fills still available after 20s: ~2% of plays. |

Bugs that fake an edge (each one is fixed in the pipeline):

1. At first pitch Polymarket clears the book and prints a glitch bar (0.50 or an empty-book
   midpoint). Using that bar as the "pregame price" made favorites look 20 points overpriced.
2. With no trading, the 1-minute bar freezes. For example, it sat at 0.54 while a team led by 10.
   Using those bars made "buy the late-inning leader" look like +3% after fees. In-game states
   are now priced only from actual fills.
3. Data-API trade timestamps are on-chain settlement times, a median of 2.6s after the match.
   Measured by matching transaction hashes against the live websocket; corrected in the latency tests.

What could still work: being the **maker** rather than the taker (no fee, plus a 15%
rebate), or a data feed faster than Polymarket's own (~27s). The second one is what
courtsiders and official-data feeds sell. `record` + `live-latency` measure both.

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pmsports games          # discover Polymarket MLB events + join to MLB game ids (~1 min)
.venv/bin/python -m pmsports fetch          # plays + 1-min prices + tick trades per game (~35 min, resumable)
.venv/bin/python -m pmsports mlb-history --start 2021-04-01 --end 2024-11-05   # baseline seasons (~25 min)
.venv/bin/python -m pmsports panel          # join game state x market price
.venv/bin/python -m pmsports report         # hypothesis tests -> reports/REPORT.md
.venv/bin/python -m pmsports audit          # coverage by month
.venv/bin/python -m pmsports record --hours 6   # live capture (run in tmux during games)
.venv/bin/python -m pmsports live-latency --day 2026-09-18   # ms-clock latency from a recorded day
.venv/bin/python -m pytest -q tests
```

Every step is resumable: rerun it and it fills only what's missing. Data goes to
`data/` (git-ignored, ~0.7 GB for two seasons; the live recorder writes ~150 MB/hour
for a full slate). Panel/report stream per game and peak at ~1.6 GB RSS. On a shared host,
run them under a cap: `systemd-run --user --scope -p MemoryMax=6G ...`.

## Data sources (verified 2026-09)

| What | Endpoint | Notes |
|---|---|---|
| MLB game markets | `gamma-api.polymarket.com/events/keyset?series_id=3` | `/events` offset caps at ~2000, so use keyset (`after_cursor`). `series_id` comes from `GET /sports`. Each event has a `moneyline` market plus spreads, totals, NRFI and F5 markets. |
| 1-min price history | `clob.polymarket.com/prices-history?market=<token>&startTs&endTs&fidelity=1` | You must pass `startTs`/`endTs`: `interval=max` falls back to 10-min bars on closed markets. |
| Tick trades | `data-api.polymarket.com/trades?market=<conditionId>&start&end` | Taker fills with 1s timestamps. Offset paging is capped (~3k rows), so the collector bisects time windows instead. |
| Live order book | `wss://ws-subscriptions-clob.polymarket.com/ws/market` | Books plus `price_change` messages that carry best bid/ask. **No historical book exists anywhere**, so you have to record it. |
| Polymarket score feed | `wss://sports-api.polymarket.com/ws` | The same score feed the Polymarket UI shows. |
| MLB play-by-play | `statsapi.mlb.com/api/v1/game/<pk>/playByPlay` | Every pitch is timestamped. We use the in-play pitch as the moment of contact. |
| ~~Goldsky orderbook subgraph~~ | deprecated | Polymarket moved to its V2 exchange and the subgraph data is stale. The replacement, `edge.goldsky.com`, is paid (x402). |

## Things that change the math

- **Taker fee on sports**: `fee = shares * 0.05 * p * (1-p)`. That is 1.25c/share at
  50c, or **2.5% of notional**, and you pay it on every entry and every exit. Makers pay
  nothing and receive a 15% rebate from the fees. History: no fee in 2025, 3% Mar-Jun
  2026, 5% since Jul 2026 (`feeSchedule` on each market). A 51/49 edge is smaller than
  this fee.
- **Book cleared at first pitch** (`clearBookOnStart`): resting pregame orders don't
  carry into the game.
- **Home/away was reversed on some early-2025 Polymarket events** (~100 games). The
  collector re-orients every game to MLB's home/away (`pm_orientation_swapped`).
- Polymarket blocks US users. US customers must use the separate CFTC-regulated
  Polymarket US exchange, which has its own API that this code doesn't cover yet.

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
tests/             unit tests (fee formula, orientation fix, state machine, P&L math)
legacy/            2024 election-arbitrage scripts (unmaintained)
```
