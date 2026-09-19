# Research guide: backtesting a Polymarket sports hypothesis

Read all of this before writing code. Every trap listed here has already produced a fake
"edge" in this repo once.

## Data (all local, no network needed unless noted)

```python
from pmsports.research import common as C
mk = C.markets()               # 38,364 sports moneyline markets (>= $50k total volume), code `m`
f  = C.fills(markets=[...], columns=[...])   # 53.8M taker fills; all 4 cols load in <1 s / 1 GB
```

`fills` columns: `m` market code; `w` wallet code (`C.wallet_ids()`); `ts` unix seconds;
`size` shares; `s` side the TAKER acquired (0/1 = outcome index; a taker SELL of k is
converted to acquiring the other side); `q` price paid for side `s`; `y` payout of side `s`
(1 won, 0 lost, 0.5 void); `fee_rate` (the market's taker fee rate); `in_play` (`ts` >=
scheduled start).

`markets` columns: `condition_id, family (soccer, tennis, esports, basketball, baseball,
hockey, american_football, cricket, mma_boxing, other), league, market_type, event_slug,
market_slug, game_start_ts (scheduled), closed_ts, fee_rate, volume (TOTAL, incl. in-play),
neg_risk, o0/o1 outcome names, y0/y1 payouts, pre_mid0 (median implied price of outcome 0
from fills in the last 10 min before start; 60 min fallback), pre_ask0/pre_ask1 (median price
takers paid to acquire outcome 0/1 in that window), pre_n_fills, pre_usd (taker $ traded
before start)`.

Soccer: each game has up to three binary Yes/No markets ("Will X win?", "Will Y win?",
"draw"); `o0 == "Yes"`. Two-way sports have team names as o0/o1.

Other data:
- `C.universe()`: 1.94M resolved sports game markets of every type (spreads, totals,
  props, exact score, halves, corners ...) with payouts. Only moneylines have fills. To get
  fills for others:
  `pmsports.polymarket.trades(condition_id, start_ts, end_ts)` (Data API, rate-limited by
  `pmsports.http`; keep it to <= 2,000 markets per study), or 1-minute prices via
  `pmsports.polymarket.price_history(token_id, start, end)`.
- MLB (`data/mlb/`): `games.parquet`, `plays/<game_pk>.parquet` (every plate appearance
  with Statcast contact time `contact_ts`, score, outs, bases), `panel.parquet` (state at
  each PA/half-inning start with market price from fills, `mkt_p` home prob), `baseline.parquet`
  (state rows for 14.5k MLB games 2021-2026 with outcomes), `pregame.parquet`.
- Live capture for one night (2026-09-18 UTC): `data/live/2026-09-18/clob.jsonl` (full
  order books + best bid/ask changes, ms timestamps), `sports.jsonl`, `mlb.jsonl`;
  `pmsports.record.top_of_book(day)` flattens the books.
- CryptoHouse (free on-chain SQL, 2022-11 .. 2026-01-05, maker AND taker fills):
  `pmsports.wallets.onchain.query(sql, ext)`. Limit: 2,000 rows / 1 MB / 60 s per query.

## Market mechanics that change the math

- **Taker fee** = `shares * rate * p * (1-p)`, where rate is the market's `fee_rate`: 0 for
  2025 sports, 0.03 Mar-Jun 2026, 0.05 since Jul 2026. That is 2.5% of notional at p = 0.5.
  **Makers pay 0** and receive ~15% of taker fees as rebates. Use `C.taker_roi` and
  `C.maker_roi`.
- Spreads are about 1c in liquid markets (8% of games are > 2c). Tick size is 0.001-0.01.
- Every fill here is the TAKER side. The makers on that fill acquired the *other* side at
  `1 - q`. So maker P&L on a fill = -(taker P&L), plus the rebate.
- The book is cleared at game start (`clearBookOnStart`).
- `ts` is on-chain settlement time, a median of 2.6 s (p10 1.8, p90 3.5) after the actual match.
  A wallet's identity is only visible after settlement.
- Resolution: 1/0, or 0.5/0.5 for void. Soccer moneyline "win" markets lose on a draw.

## Traps that already produced fake edges here (do not repeat)

1. **Outcome-driven selection.** `volume` is TOTAL volume including in-play. Upsets draw more
   in-play volume, so filtering or bucketing on `volume` over-represents upsets. This made
   "bet every underdog" look +3% to +9%. Select on pregame information only: `pre_usd`,
   `pre_n_fills`, scheduled time, league.
2. **Glitch prices.** 1-minute bars (`prices-history`) print 0.50 or an empty-book mid at game
   start, and freeze for innings when nobody trades (e.g. 0.54 while up 10 runs). Price
   from actual fills only.
3. **Look-ahead.** Any threshold, bucket, model or "historical rate" must be estimated only on
   data before the bet (e.g. fit on 2025, test on 2026; or strictly prior seasons). Report
   2025 and 2026 separately.
4. **Execution fantasy.** You cannot trade at a print that already happened. Enter at a
   *later* fill (first print on your side after your decision time + >= 3 s), or at the
   quoted ask. Add the taker fee. For maker strategies, fills are only those where a taker
   actually traded at your price, and you inherit the adverse selection of those fills.
5. **Correlated bets.** Many signals per game are not independent. Take one bet per game (the
   first signal), or cluster CIs by `event_slug` / game (`C.cluster_ci`).
6. **Multiple testing.** If you try K variants, expect ~K/20 to look significant by chance.
   Pre-register the main variant; report every variant you tried; prefer out-of-sample.
7. **Scheduled vs actual start.** `in_play` uses the scheduled start. Tennis and esports often
   start late. Treat "pregame" near the start as fuzzy for those sports.
8. **Capacity.** Report how much $ could actually be deployed (the fills available at the
   price), not just ROI on $1.

## Resource rules (shared machine)

- Run every Python process under a memory cap:
  `systemd-run --user --scope -q -p MemoryMax=5G -p MemorySwapMax=0 .venv/bin/python ...`
- Load only the columns and markets you need. Never materialize wallet/condition id
  strings for all fills.
- Put your code in `pmsports/research/h_<slug>.py` and your write-up in
  `reports/research/<slug>.md`. Do not edit other files. Do not git commit.

## What to report

For the main pre-registered variant and every other variant tried: bets, $ deployed, ROI
per $1 after actual fees (plus +1c slippage sensitivity for takers), clustered 95% CI,
2025 vs 2026 split, per-sport split when relevant, capacity, and the exact entry rule.
Verdict: PROFITABLE (OOS CI > 0 after all costs), PROMISING (positive but CI crosses 0),
or DEAD.
