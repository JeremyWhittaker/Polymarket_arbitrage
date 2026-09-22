# Polymarket sports research

> Repairs are in progress following the [independent review](reports/PEER_REVIEW.md).
> Historical returns below predate the current causality and execution corrections.
> See the [repair checklist](reports/REPAIR_CHECKLIST.md) and [validation evidence](reports/REPAIR_VALIDATION.md); no corrected trading edge has yet been accepted.

Tests whether you can make money on Polymarket sports moneylines by:

1. **Timing**: betting right after something happens in the game (e.g. a home run),
   before the market reprices.
2. **Averages**: using historical "team up X runs going into inning Z" outcomes to
   find prices that are too high or too low, then betting toward the historical
   average and holding to the end of the game.
3. **Pregame favorites**: finding out whether favorites win more often than their
   price implies.
4. **Copying sharp bettors**: finding wallets that are genuinely good at predicting
   baseball, soccer, tennis and other sports (by win rate, P&L or luck-adjusted skill,
   and whales by size), then mirroring their trades.

Items 1-3 were tested on MLB and item 4 on all sports.

The 2024 election-arbitrage scripts this repo started with are in
[`legacy/election_2024/`](legacy/election_2024/). They rely on the deprecated Goldsky
subgraph, Polygonscan scraping and Selenium, and nothing new depends on them.

Latest results: [`reports/REPORT.md`](reports/REPORT.md).

Independent peer review (2026-09-22): [`reports/PEER_REVIEW.md`](reports/PEER_REVIEW.md)
maps the original prompts to delivered work, identifies remaining data/execution defects,
and rechecks the 74–75c whale signal. No executable edge is established; read the review's
qualifications before relying on the historical conclusions below.

## MLB findings (2026-09-18: 4,664 games from 2025-26, baseline of 14.5k games from 2021-26)

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

## Bet every pregame favorite, hold to the end (all sports) — [`reports/FAVORITES.md`](reports/FAVORITES.md)

This covers 16,242 games with at least $25k traded *before* the start (Jan 2025 to Sep 2026).
Favorites won **61.8%** of games at an average price of **62.7%**, which is **-1.6% per $1
before costs** and **-3.8% after the fees actually charged plus 1c**. At $100 per game that is
**-$61,900**. No sport, season or favorite-price bucket is reliably positive after costs.
The mirror bet (every underdog) is about -0.7%.

Trap: selecting games on *total* volume (>= $50k, which includes in-play trading) makes
underdogs look +3% to +9% profitable. Upsets draw more in-play volume, so thin markets clear
the cut more often when the dog wins. Selecting on pregame volume only removes the effect.

## The breaking point: price vs reality — [`reports/CALIBRATION.md`](reports/CALIBRATION.md)

35.8M fills in 17,810 markets (selected on pregame volume only), asking at every price level how
often that side actually won.

| Price paid | Actually won | Edge | Return per $1 after fees |
|---|---|---|---|
| 90-92.5c | 88.6% | -2.9 pts | -3.3% |
| 92.5-95c | 93.3% | -0.7 pts | -0.8% |
| 95-96c | 95.1% | -0.7 pts | -0.8% |
| 96-97c | 96.7% | +0.1 pts | +0.0% |
| 97-98c | 97.1% | -0.3 pts | -0.4% |
| 98-99c | 98.5% | +0.1 pts | +0.0% |
| 99c+ | 99.1% | +0.0 pts | +0.0% |

There is no breaking point: above ~96c the market is calibrated to within a tenth of a point, and
the only reliable deviations are *negative*. Tradable version (buy the first executable print at or
above a threshold, hold to resolution, actual fees): every threshold from 60c to 99c loses, dev and
holdout alike (e.g. >= 95c: -1.0% dev / -0.8% holdout on 17.6k bets; >= 99c: -0.5% / -0.3%). The
mirror (buying longshots) is worse: -4% to -11%. Ledgers for >= 90 / 95 / 99c and the <= 10c mirror
are browsable in the research desk.

## Every price point from 50c — [`reports/CALIBRATION_POINTS.md`](reports/CALIBRATION_POINTS.md)

At 1-cent resolution the dollar-weighted table shows a bump around 70-75c (74c: +7.8%, positive in
both dev and holdout). It is a weighting artifact, not an edge. The same 726,623 fills at 74-75c:
dollar-weighted **+6.8%**, equal-weighted per fill +0.8%, **one bet per market -0.4%**. The median
fill there is $11 and the top 1% of fills carry 56% of the dollars. Every tradable version loses:
buying the first print in a 73-76c band (dev -0.7%, holdout -1.0%) and every "buy once it crosses T"
threshold from 50c to 99c, in both windows.

## Open lead: big tickets at exactly 74-75c — [`pmsports/analysis/whale_prices.py`](pmsports/analysis/whale_prices.py)

Chasing the 74-75c bump found something unexplained. Taker fills of >= $10k at **exactly 74c and
75c** won 82.3% / 80.6% against those prices: **+10.6% and +7.1%** per $1 after fees, positive in
both development and holdout, across 1,016 wallets, 1,090 games and 8 sports, and still +8.3% after
dropping the 50 best games. A follower entering 3 seconds later on the same side keeps essentially
all of it (+10.6% / +6.6%). Ordinary-sized fills at the same prices earn ~+1%.

It is NOT an edge to trade, because it does not generalise: 73c, 76c, 77c, 80c, 85c, 90c and 95c are
flat or negative for the same whale filter, and nothing structural distinguishes these trades
(negRisk share 0.29 vs 0.30, in-play share 0.36 vs 0.40, ticket sizes, leagues and months all
ordinary). Information does not switch on at 74c and off at 73c. Treat as an anomaly to explain
before anything else: `reports/whale_prices.csv` reproduces the table.

## Threshold strategies — [`reports/THRESHOLDS.md`](reports/THRESHOLDS.md)

- **Bet any team priced >= T pregame (T = 50c..90c), per sport:** no threshold is profitable
  after fees. Before costs, favorites >= 65c break even (-0.2%) and 50-65c favorites are
  overpriced by 1-2 points. Only 1 of 72 sport x threshold cells has a CI above zero
  (basketball >= 90c, 126 games), and about 2 would by chance.
- **MLB: buy the leader when its price is X points below the historical win rate for that
  inning/lead:** discounted leaders are nearly always the weaker team (78-98% were pregame
  underdogs), and they win at about their price, not at the historical rate. First signal
  per game: **+6% in 2025** (fee-free, thin market; CI +1% to +10.5%) but **~0% in 2026**
  (+2-4% before costs, not significant, eaten by fees). With a fair value that knows the
  pregame odds, there is no edge on either side in 2026.

## Copy-the-sharps study (all sports) — [`reports/WALLETS.md`](reports/WALLETS.md)

Data: 53.8M wallet-attributed taker fills in 38,364 resolved sports moneyline markets with
at least $50k of volume (soccer, tennis, esports, basketball, baseball, hockey, NFL, cricket,
MMA), from 833k wallets. Wallets are selected on 2025 data and copied in 2026, with no
look-ahead. Each copy executes at the first *other* taker print on the same side, d seconds
later, pays the taker fee, and is held to resolution.

| Question | Answer |
|---|---|
| Do skilled bettors exist? | **Yes, a few.** 99 wallets clear a false-discovery-rate cut on 2025 luck-adjusted z, where ~31 would clear z > 3 by luck alone. They stayed profitable in 2026: **+3.9%** on their own fills (CI +2.1% to +5.9%). |
| Can you copy them? | **No.** 94% of their trades are in-play, with a median fill of $4 (bots). The edge falls from +3.9% at their price to +1.1% 1s later, +0.2% at 5s and -0.9% at 30s. Their wallet only becomes visible after on-chain settlement (~2.6s), so you are always late. |
| Does skill persist in general? | **Barely.** The 2025-vs-2026 rank correlation of z across 7,148 wallets is 0.04, and the z-decile table is flat. |
| Follow the whales (biggest $ wallets, or any $10k+/$50k+ trade)? | **No.** $10k+ trades copied at 30s: -1.1%. $1k+ trades: -1.5% (CI -1.9% to -1.0%). Prices don't move toward whale trades within 5 minutes. |
| Highest win rate / top ROI / top P&L / Polymarket leaderboard? | **None beat random wallets out of sample.** A few per-sport cells look positive (basketball top-ROI +39%, CI +1% to +95%), but that is expected by chance across ~60 sport × rule tests. |
| Monthly re-selection (walk-forward)? | Top-z wallets: -0.6% at their own price, **-5.4% one second later**. Whales: +1.6% at their price, -1.0% at 30s. Pregame-only top-z: +1.1% at 60s, positive in 8 of 14 months (not significant). |
| Who tops the leaderboard? | The top 100 by volume are market makers earning ~0.04% of volume, plus losing takers. Among the top 100 by P&L, 59 had no 2025 on-chain activity, and margins run 2-11%. Makers' edge (spread + rebates) cannot be copied by a taker. |

On-chain access: [CryptoHouse](https://crypto-clickhouse.clickhouse.com) (free SQL, user
`crypto`) has every CTF-exchange `OrderFilled` event through 2026-01-05 (the V2 exchange
migration). Public-user limits are 2,000 rows, 1 MB and 60s per query, so it is used for
wallet aggregates (`pmsports/wallets/onchain.py`). `/trades?user=` silently returns nothing for
most of 2025, so the study builds per-market tapes from `/trades?market=` instead.

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
.venv/bin/python -m pmsports universe       # every resolved sports game market, all leagues (~25 min)
.venv/bin/python -m pmsports tapes          # wallet-attributed fills, moneylines >= $50k (~3 h, resumable)
.venv/bin/python -m pmsports wallets-report # copy-the-sharps study -> reports/WALLETS.md (~40 min, 14 GB cap)
.venv/bin/python -m pmsports favorites      # bet every pregame favorite, all sports -> reports/FAVORITES.md (~2 min)
.venv/bin/python -m pmsports thresholds     # price thresholds by sport + MLB inning-discount rule -> reports/THRESHOLDS.md
.venv/bin/python -m pmsports calibration    # price vs realized win rate by sport -> reports/CALIBRATION.md (~2 min)
.venv/bin/python -m pmsports.webapp         # research desk on :8808 (systemd user service pmsports-web)
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
    favorites.py   pregame-favorite (and underdog) hold-to-resolution backtest, all sports
    thresholds.py  price-threshold grid by sport; MLB leader-below-historical-rate rule
    calibration.py price vs realized win rate by sport, and the tradable threshold rules
  wallets/
    universe.py    all resolved sports game markets (Gamma tag 100639), sport family, payouts
    tapes.py       per-market taker tapes; streaming loader (54M fills in ~2 GB)
    skill.py       positions, luck-adjusted z, FDR, executable copy prices, clustered bootstrap
    study.py       selection rules vs placebo, walk-forward, big-trade signal, skilled decomposition
    onchain.py     CryptoHouse queries (maker/taker profile per wallet)
    report.py      renders reports/WALLETS.md
tests/             unit tests (fee formula, orientation fix, state machine, P&L math)
legacy/            2024 election-arbitrage scripts (unmaintained)
```
