# In-play certainty premium: sell the new 90-97c "lock" as a maker

**Verdict: DEAD.** The pre-registered maker rule loses money in every period, and every CI lies
entirely below zero:

| period | events filled | shares | capital | P&L | c/share [95% CI] | ROI on capital [95% CI] |
|---|---|---|---|---|---|---|
| DEV 2025 | 3,719 | 601,600 | $47,650 | -$9,245 | -1.54 [-2.33, -0.68] | -19.4% [-29.4, -8.6] |
| DEV 2026H1 | 6,329 | 1,085,433 | $92,509 | -$11,387 | -1.05 [-1.70, -0.37] | -12.3% [-19.9, -4.4] |
| DEV pooled | 10,048 | 1,687,033 | $140,159 | -$20,632 | -1.22 [-1.73, -0.68] | -14.7% [-20.8, -8.2] |
| **HOLDOUT (Jul-Sep 2026)** | **3,309** | **554,094** | **$46,903** | **-$9,493** | **-1.71 [-2.59, -0.84]** | **-20.2% [-30.5, -9.9]** |
| HOLDOUT, fills 1c worse | 3,309 | 554,094 | $52,444 | -$15,001 | -2.71 [-3.58, -1.83] | -28.6% [-37.8, -19.3] |

The premium in the hypothesis is real only at the signal print, which cannot be traded. Selling one share at
the price the lock-buyer just paid would have earned +2.15c in 2025, +0.64c in 2026H1 and +0.31c
[-0.59, +1.21] in the holdout. So even this untradable version has decayed to zero. A maker who posts
at that price 3 s later gets filled mostly when the favourite keeps rising. The comebacks that carry
the premium are the games in which no taker comes back to buy at that price.

Code: `pmsports/research/h_inplay_certainty_premium.py`. Caches and results are in
`data/research/h_inplay_certainty_premium/`:
- `cand.parquet` (first 90-97c print per market side)
- `erows.parquet` (variant e)
- `win_*.parquet` (execution windows)
- `events_primary_full.parquet`
- `results_dev.json` and `results_full.json`

## Hypothesis

When a side first becomes a 90%+ favourite during play, two groups cross the spread to buy it:
"sure-thing" yield buyers, and bettors piling onto the winning side. Variance-limited makers avoid
holding 3-10c comeback tails near the end of a game. If so, the tail is underpriced, and whoever
sells certainty (holds the underdog) collects a premium. The proposer's prior was +2.2c/share in 2025
and +0.8c in 2026H1, measured at the print with an unconstrained fill model. This merges in
`inplay_longshot_maker`, which sells the same in-play favourites.

## Rule as implemented

- **Universe.** `C.markets()` with `pre_usd >= $20k` (pregame information only): 20,001 markets in
  18,157 events, all sports. Voids pay 0.5 (6 DEV signals).
- **Split.** By `game_start_ts`: DEV 2025 (< 2026-01-01), DEV 2026H1 (< 2026-07-01), HOLDOUT
  (>= 2026-07-01, 5% fee regime).
- **Fills.** Rows sharing (m, wallet, ts, side, price) are one taker order split across makers, so
  they are merged first. 25.4M in-play rows become 23.8M orders.
- **Signal (one per `event_slug`).** The first in-play fill (`ts >= game_start_ts`) in any universe
  market of the event where the taker acquired side s at q in (0.90, 0.97] and s's pregame price
  (`pre_mid0` for s = 0, `1 - pre_mid0` for s = 1) is < 0.80. Then t0 = its ts and a = q.
  - Ties within the first second go to the lowest q, which is the first level a buy sweep matches,
    then to the lowest market code.
  - Signals by period: DEV 2025 4,802; DEV 2026H1 7,163; HOLDOUT 3,795.
  - The median signal comes 99 min after the scheduled start.
- **Execution.** From t0+3 s to t0+300 s we rest an ask on s at a (the same as a bid on 1-s at 1-a).
  The cap is 200 shares per event.
- **Queue model (pre-registered, phi = 0.25).** Each later taker order acquiring s at q >= a in
  [t0+3, t0+300] fills us as follows:
  - **Level exhausted:** we get min(size, remaining cap). The level counts as exhausted when some fill
    acquires s at q > a with ts in [ts_i, ts_i + 60 s]. A print at q > a is itself proof that a is gone.
  - **Otherwise:** we get 0.25 × min(size, remaining cap).
- **P&L.** Per share: a - y_s + 0.15 × fee_rate × a(1-a). Makers pay no fee; the last term is the
  rebate. Positions are held to resolution, and capital per share is 1 - a.
- **Statistics.** Share-weighted c/share and ROI on capital, with `C.cluster_ci` clustered by
  `event_slug`.
- **Maker +1c sensitivity.** Every fill is priced 1c worse (a - 0.01).

## Why it fails: adverse selection of the fills

Each period below splits the signals into filled and unfilled events.

| period | filled events | win rate of s (filled) | share-weighted win rate of s | mean a | unfilled events | win rate of s (unfilled) | c/share if unfilled could be sold at a |
|---|---|---|---|---|---|---|---|
| DEV 2025 | 3,719 | 92.5% | 93.6% | 0.921 | 1,083 | 81.7% | +10.9 |
| DEV 2026H1 | 6,329 | 91.9% | 92.5% | 0.915 | 834 | 83.2% | +8.8 |
| HOLDOUT | 3,309 | 92.6% | 93.3% | 0.916 | 486 | 82.3% | +9.7 |

- **Fills that come through an exhausted level lose.** About 95% of our shares come from levels that
  were traded through, meaning the favourite kept rising. Those fills lose -1.57c, -1.22c and -1.83c
  in 2025, 2026H1 and holdout. All three CIs are below 0.
- **Queue-position fills break even.** The phi fills at a level that held are not significantly
  different from zero (-0.73c, +2.19c and +0.39c; all CIs cross 0).
- **The premium sits where no maker can fill.** The unfilled events (13-23% of signals) are the
  comebacks, where the price never returns to a. These events carry the whole premium, and a maker
  cannot sell into them.
- **Queue position does not rescue the rule.** At phi = 1, the rule is still -1.53c in the holdout.

## Variants (all pre-listed; holdout shown for completeness, the verdict uses only the primary)

ROI on capital (maker) or per $1 (taker), with 95% CIs clustered by event.

| variant | DEV 2025 | DEV 2026H1 | DEV pooled | HOLDOUT |
|---|---|---|---|---|
| **Primary, phi 0.25** | -19.4% [-29.4, -8.6] | -12.3% [-19.9, -4.4] | -14.7% [-20.8, -8.2] | **-20.2% [-30.5, -9.9]** |
| Primary, fills 1c worse | -28.4% | -21.5% | -23.9% [-29.3, -18.0] | -28.6% [-37.8, -19.3] |
| Primary, no rebate | -19.4% | -12.6% | -14.9% | -20.9% |
| (a) phi = 0 (only exhausted levels) | -19.8% | -13.3% | -15.5% [-21.9, -9.0] | -22.0% [-33.0, -10.9] |
| (a) phi = 1 (front of queue) | -17.3% | -10.3% | -12.7% [-18.8, -6.1] | -18.0% [-28.0, -7.2] |
| (b) taker: buy 1-s at next print, fee | -11.6% | -11.0% | -11.2% [-16.8, -5.2] | -10.5% [-21.6, +1.4] |
| (b) taker, fee + 1c slippage | -21.4% | -20.3% | -20.7% [-25.6, -15.7] | -21.5% [-30.2, -12.6] |
| (c) a in (0.90, 0.93] | -18.8% | -14.0% | -15.5% [-22.2, -8.5] | -22.4% [-33.4, -11.2] |
| (c) a in (0.93, 0.95] | -31.9% | +29.4% | -5.8% [-31.6, +23.4] | +7.9% [-42.7, +66.4] |
| (c) a in (0.95, 0.97] | +16.3% | -21.0% | +0.1% [-56.0, +67.5] | +87.3% [-75.5, +281.9] |
| (d) pre_usd >= $100k | -23.4% | -8.0% | -13.6% [-22.8, -4.2] | -22.5% [-40.2, -2.3] |
| (e) all in-play fills at [0.90, 0.98), phi 0.25, no cap | -6.1% | +2.8% | +0.6% [-15.8, +19.0] | +11.4% [-25.9, +52.7] |
| (e) phi = 0 | -11.4% | -4.5% | -6.1% [-22.3, +11.1] | +5.2% [-31.8, +49.4] |
| (e) phi = 1 | +2.6% | +17.5% | +13.6% [-4.7, +34.3] | +24.4% [-13.8, +68.2] |
| (e') as (e), with pregame of s < 0.80 | -13.3% | +10.8% | +4.6% [-15.0, +26.2] | +10.5% [-28.9, +55.9] |
| (f) control: s already >= 0.85 pregame | -32.2% | -5.7% | -13.6% [-34.9, +10.0] | -7.5% [-48.8, +41.9] |

In c/share, the primary is -1.54, -1.05, -1.22 and **-1.71** for DEV 2025, DEV 2026H1, DEV pooled and
HOLDOUT. Variant (b) buys the underdog at a mean of 10.5c (DEV) and 9.4c (holdout) and wins 9.4% and
8.7% of the time. After the lock, the spread on the tail is larger than any mispricing.

**Variant (e) is not a hidden edge.** It is uncapped and share-weighted, so a few very large games
dominate: the top 1% of events hold 17%, 25% and 37% of the shares in 2025, 2026H1 and holdout. Two
post-hoc diagnostics were computed after the holdout run and were not used for any decision:
- **Equal weight per event:** (e) at phi 0.25 is -0.94c [-1.41, -0.43], -1.70c [-2.02, -1.36] and
  -2.10c [-2.58, -1.62].
- **Capped at 200 shares per event, filled in time order:** +0.43c [-0.31, +1.18], -0.01c
  [-0.57, +0.59] and -0.26c [-1.07, +0.55].

Its positive share-weighted means come from the largest games, and its phi = 1 row is the
front-of-queue fantasy.

## Per sport (primary; c/share [95% CI], filled events)

| sport | DEV pooled | HOLDOUT |
|---|---|---|
| soccer | +0.01 [-1.34, +1.40] (1,789) | -1.19 [-2.97, +0.74] (884) |
| esports | -1.37 [-2.50, +0.00] (1,687) | -2.08 [-3.80, -0.17] (857) |
| tennis | -1.60 [-3.15, +0.05] (1,163) | -1.06 [-2.87, +0.93] (778) |
| baseball | -1.95 [-3.24, -0.62] (1,550) | -2.95 [-4.78, -0.85] (597) |
| basketball | -1.29 [-2.43, -0.05] (1,852) | -0.80 [-6.27, +5.56] (82) |
| american_football | -2.01 [-4.11, +0.26] (496) | +0.03 [-6.25, +8.35] (56) |
| hockey | -1.35 [-2.71, +0.20] (1,284) | no games (off-season) |
| mma_boxing | +2.41 [-3.63, +8.86] (84) | -1.67 [-8.41, +7.96] (32) |
| cricket | -0.80 [-5.56, +4.65] (105) | -8.28 (16) |
| other | -2.81 (38) | -8.27 (7) |

No sport is positive in both DEV and holdout with a CI above zero. Soccer is the only DEV sport near
zero, and it is -1.19c in the holdout.

## Capacity

Capacity is not the binding constraint; the sign is.
- **Rule deployment.** The rule fires on 47.5 events/day in the holdout and fills 167 shares per filled
  event on average (the 200-share cap binds in 66% of signals). That is about $590/day of capital
  deployed, and about -$119/day P&L at the modelled fills.
- **Taker flow in our windows.** Takers bought 27.2M shares of s at >= a inside our 5-minute windows
  in the holdout, about $2.4M of complement capital over 80 days.
- **All qualifying flow.** In-play taker notional at [0.90, 0.98) was $108M in 2025, $301M in 2026H1
  and $138M in the holdout.

## Checks for my own bugs

- **Payout orientation.** For all 11,965 DEV signals, `y_s` from fills equals `y0`/`y1` from
  `markets` for side s (0 mismatches). For variant (b), `y(1-s) + y_s = 1` holds for every bet (voids
  0.5/0.5).
- **Fees.** Maker rows pay no fee and get a 15% rebate from the market's `fee_rate` (0 in 2025). The
  rebate adds only +0.02c/share in 2026H1 and +0.05c in the holdout. Taker rows use `C.taker_roi`
  with the market fee.
- **Timestamps.**
  - Signals require ts >= `game_start_ts`, and fills require ts in [t0+3, t0+300].
  - A robustness run joins at t0+6 to cover the ~2.6 s on-chain lag. It gives -1.23c (DEV) and
    -1.80c (holdout), about the same as the primary.
  - Rounding a up to the price grid (35% of signal prints are off the 0.01 grid) gives -1.24c and
    -1.67c.
- **One bet per game.** There is one signal per `event_slug`, taken as the earliest across that
  event's markets. The cap applies per event.
- **Queue model.** A hand-coded loop reproduced the vectorised share counts on 5 random events.
  Window loads match a direct re-read of `C.fills` (orders and total size) on the same events.
- **Look-ahead.** Nothing in the signal uses post-t0 data. The exhausted flag looks up to 60 s past
  a fill, but only to decide whether our resting order would have been reached, which is a property
  of the book at that time. It works against us: it gives full fills when the favourite keeps rising.

## Caveats

- **The holdout is not fully clean.** The proposer had already looked at 2026H2 with an unconstrained
  fill model (sell at the signal print) before pre-registering this rule. In this study, the
  primary, the variants and the verdict rule (including "DEV CI entirely below 0 means DEAD") were
  fixed on DEV-only runs, and then the full run with the holdout was made once. The (e) weighting
  diagnostics above were computed after that run.
- **The queue model is stylised.** phi and the 60-s exhaust window are assumptions and were not
  calibrated on recorded books. But the sign does not depend on them: phi = 0, 0.25 and 1 are all
  significantly negative in every period.
- **We may cross at the join.** If at t0+3 the bid on s is already at or above a, a real order at a
  would cross and fill at once as a taker (at a better price, but paying the fee). The model instead
  waits for later prints. This affects few events and would not change the sign.
- **The reference number is not a strategy.** The untradable "sell at the signal print" figure only
  shows the size of the premium, and part of it is the bid-ask bounce: the signal is a taker buy at
  the ask.
- **In-play is defined by the scheduled start.** Tennis and esports often start late. 66 DEV signals
  come within 5 min of the scheduled start.

## Verdict

**DEAD.** The holdout ROI is -20.2% [-30.5, -9.9] after all costs, and -28.6% with 1c worse fills.
The DEV CI [-20.8%, -8.2%] lies entirely below zero. Every fill model (phi 0, 0.25, 1), the taker
version, the larger-market universe and the tick-rounded and delayed-join robustness runs are
negative. The certainty premium shows up only at prints that have already happened. A maker who
posts afterwards is filled when the lock holds and left out when the comeback comes.
