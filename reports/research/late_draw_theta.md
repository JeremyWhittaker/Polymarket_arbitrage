# Late-match draw underpricing in tied soccer games (`late_draw_theta`)

**Verdict: DEAD.** Late in tied soccer matches, the draw-Yes price is calibrated. Draws finished at 53.7%
in development against an average entry price of 55.2c, and at 55.1% in the holdout against 55.0c. So there is
no edge to harvest even before fees. After the actual taker fee the pre-registered rule returns **-3.6%** per $1 in
development (95% CI -12.4% to +5.2%) and **-2.0%** in the holdout (CI -15.6% to +12.9%). With +1c slippage it
returns -3.7% in the holdout. No variant has a development CI above zero. The maker variant is worse (-11% in the
holdout), because a resting draw bid is filled mostly when a goal is scored.

Code: `pmsports/research/h_late_draw_theta.py`. Cache and logs: `data/research/h_late_draw_theta/`
(`run_dev.log`, `run_holdout.log`, `results_*.json`, `bets_*.parquet`, 929 fetched tapes in `trades/`).

## Rule as implemented (pre-registered, nothing tuned)

- **Universe (pregame information only).** Soccer events with Yes/No legs where the larger team-win leg in
  `C.markets()` has `pre_usd >= $50k`. That gives 2,053 events: 1,512 in development (KO < 2026-07-01) and 541 in the
  holdout. Every event's `-draw` leg is taken from `C.universe()`, and all 2,053 have one. Draw fills come from the
  local tape when the draw leg is in `C.markets()` (1,124 legs, draw volume >= $50k). The other 929 are fetched from
  the Data API for [KO+50, KO+180] min, at 5 requests/s with 0 errors and 115k fills. So inclusion never depends on
  draw volume. Fills are normalized by token id: taker acquired draw-Yes or draw-No at q. That matches `C.fills` on
  200 of 200 legs checked, and outcomeIndex never disagreed with the token.
- **Signal.** The first draw fill with ts >= KO+95 min, searched in [KO+95, KO+180] min. That is the fetched window,
  and the same cut is applied to local legs. `p` = Yes-converted price of that fill. If 0.40 <= p <= 0.90, then
  t0 = its ts. The median signal time is KO+95.2 min, which is roughly match minute 77-80.
- **Entry.** Buy draw-Yes as a taker at the first Yes-acquiring fill with ts in [t0+3 s, t0+300 s]. The price is
  the highest Yes price printed in that second (the end of a sweep, conservative). Taking the lowest price instead
  changes ROI by less than 0.1 pt. The taker fee `fee_rate*p*(1-p)` uses the draw market's own `fee_rate`: 0 in
  2025, 0.03 in Mar-Jun 2026, and 0.05 or 0.03 in Jul+ 2026. The median entry delay is 14-15 s (p90 64-82 s).
  There is one bet per event (asserted), held to resolution on the Yes-token payout (1 = 90-minute draw).
- **Metric.** `C.taker_roi` per $1 staked per bet, with `C.cluster_ci` by `event_slug` (2,000 bootstrap draws).

Funnel:

| | Events | Draw fill after KO+95 | p in [0.40, 0.90] | Bets (entry found) |
|---|---|---|---|---|
| Development | 1,512 | 1,497 | 420 | 415 |
| Holdout | 541 | 530 | 160 | 158 |

Orientation check: of the draw legs with a fill after KO+150 min, 715 last traded at or below 0.05 and all 715
resolved No; 316 last traded at or above 0.95 and all resolved Yes. The knockout cases were checked against the
team legs. For example, in Cultural Leonesa vs Athletic in the Copa del Rey the draw paid 1 and both "win" legs paid
0, which is the 90-minute rule working as expected.

## Results (ROI per $1 after actual taker fees; 95% clustered CI)

| Rule | Period | Bets | ROI | CI | Draw rate | Avg price | Avg fee/share |
|---|---|---|---|---|---|---|---|
| **Primary** | development (all) | 415 | **-3.62%** | [-12.42%, +5.15%] | 0.537 | 0.552 | 0.0026 |
| | development 2025 (fee 0) | 59 | -6.31% | [-30.6%, +17.0%] | 0.525 | 0.559 | 0 |
| | development 2026 H1 | 356 | -3.18% | [-12.8%, +6.5%] | 0.539 | 0.551 | 0.0031 |
| | **holdout** | 158 | **-1.95%** | [-15.60%, +12.90%] | 0.551 | 0.550 | 0.0112 |
| Primary +1c slippage | development | 415 | -5.37% | [-14.0%, +3.2%] | | | |
| | **holdout** | 158 | **-3.74%** | [-17.1%, +10.8%] | | | |
| Primary +2c slippage | holdout | 158 | -5.47% | [-18.6%, +8.8%] | | | |
| Primary, before fees | development | 415 | -3.12% | [-11.9%, +5.7%] | draw minus price: -1.50c | | |
| | holdout | 158 | +0.14% | [-13.8%, +15.4%] | draw minus price: +0.09c | | |

The fee is not what kills this. The draw is priced at its hit rate at match minute ~78. The "theta" (the draw
getting more likely each goalless minute) is already in the price. Neither hope-holding nor the "win means advance"
misreading shows up as a gap between price and result.

## Variants (all reported; none rescues the hypothesis)

| Variant | Development bets | Development ROI [CI] | Holdout bets | Holdout ROI [CI] |
|---|---|---|---|---|
| (a) Maker bid at p-1c, filled on touch within 5 min (no fee, 15% rebate) | 231 | -6.78% [-19.8, +5.1] | 58 | -11.06% [-35.4, +13.6] |
| (a) Maker, trade-through only (back of queue) | 164 | -13.84% [-28.0, +0.9] | 44 | -19.02% [-46.4, +8.6] |
| (b) Early: first fill >= KO+60, p in [0.20, 0.45] | 806 | -0.89% [-11.0, +9.7] | 269 | +0.38% [-16.9, +18.7] |
| (b) Early +1c | 806 | -4.01% [-13.8, +6.2] | 269 | -2.72% [-19.5, +15.1] |
| (c) Knockouts (World Cup round of 32 onward, domestic cups, super cups) | 13 | -54.8% [-100, -5.6] | 18 | -6.59% [-49.2, +37.4] |
| (c) League / non-knockout | 402 | -1.97% [-10.9, +7.2] | 140 | -1.35% [-16.8, +14.2] |
| (c) Knockouts, maker touch | 11 | -60.4% | 7 | -41.9% |
| (d) +1c slippage | see primary | -5.37% | | -3.74% |
| (e) Team leg pre_usd >= $100k | 288 | -4.15% [-14.6, +6.9] | 88 | -0.37% [-20.0, +21.0] |
| (e) +1c | 288 | -5.89% | 88 | -2.18% |

The maker variant shows adverse selection directly. Its bids fill on 56% (development) and 37% (holdout) of
signals, and the filled bets draw only 49.8% and 48.3% of the time, against 53.7% and 55.1% for all signals. The
bid gets hit when a goal is scored.

The knockout samples are tiny: 13 bets in development, 18 in the holdout. World Cup knockouts in the holdout went
6 draws in 10 bets for +20% ROI (CI -41% to +81%), but World Cup knockouts in development went 1 in 4. That is noise,
not a mechanism.

### Split by league group (primary rule; soccer is the only sport in this hypothesis)

| Group | Development bets | Development ROI | Holdout bets | Holdout ROI |
|---|---|---|---|---|
| Top-5 leagues | 231 | -0.5% | 41 | -11.1% |
| Other leagues | 70 | -1.3% | 53 | +12.7% [-9.8, +36.0] |
| UEFA club competitions | 36 | -7.5% | 17 | +1.8% |
| MLS / Liga MX / Leagues Cup | 16 | -6.6% | 29 | -15.1% |
| Internationals (qualifiers, friendlies) | 27 | -16.3% | 0 | - |
| World Cup group stage | 22 | +10.7% | 0 | - |
| World Cup knockouts | 4 | -46.5% | 10 | +20.3% |
| Domestic cups and super cups | 9 | -58.4% | 8 | -40.2% |

No group is positive in both periods with more than a handful of bets.

### Diagnostics (not variants)

- **The volume bias behind the earlier +2.4c peek.** On draw legs that are in the local set (draw volume >= $50k,
  which includes in-play volume) the rule returns +0.35% in development, against -12.4% on the fetched lower-volume
  legs. In the holdout the two are -2.7% and -1.3%. The earlier positive number came from selecting on draw volume,
  which is partly outcome-driven. The full, unbiased sample is negative.
- **Entry timing.** Entries within 30 s of t0 return -4.3% in development and +1.3% in the holdout; entries 31-300 s
  after t0 return -1.5% and -8.0%. There is no consistent sign.
- **Entry price buckets.** Development and holdout together: 0.5-0.6 gives +1.7% / -5.8% and 0.6-0.7 gives
  -13.8% / -6.2%. The 0.7-0.9 bucket has 9 and 7 bets, which is too few to read.

## Capacity

About 43 bets a month in development and about 62 in the holdout. Takers bought draw-Yes at or below the entry
price +1c within the 5-minute window for a median of $168 (development) and $104 (holdout) per bet, a 25th
percentile of $17-37, and a mean of $1.6-1.9k (skewed by the big games). That is roughly $70-117k a month of other
takers' flow, which is an upper bound. A single strategy could realistically deploy a few hundred dollars a game. The
maker variant's fill is capped by the counterparty's size: a median of about 10 shares.

## Caveats

- The kickoff time is the scheduled `game_start_ts`. A delayed kickoff moves the signal earlier in the match. This
  is the same in both periods and is part of the pre-registered rule.
- The entry is the next Yes print within 300 s. If a goal falls between t0+3 s and that print, the simulated entry is
  at the post-goal price. For per-$1 ROI a loss is -100% at any price, so this is roughly neutral, and entries within
  30 s show the same picture.
- The maker model uses public taker prints only: it is front of queue on touch, with trade-through as the
  back-of-queue stress. There is no cancel-on-goal logic. That would need sub-second score data, which the guide
  shows is not available to us (public feeds lag about 27 s).
- The CIs are wide (the holdout half-width is about 14 pt) because each bet is a single outcome around 50/50. But the
  point estimates straddle zero before fees in both periods (-1.5c and +0.1c), and costs of 1.1c fee plus 1c spread
  make it negative. An edge large enough to clear 2c of costs would need draw rate minus price of +2c or more; the
  development period rules that out as a likely value.

## Verdict

**DEAD.** Holdout ROI after actual fees is -1.95% (-3.74% with +1c), and the development CI lower bound is -12.4%.
Late draws in tied games are calibrated on Polymarket. The maker version inherits goal-driven adverse selection and
is worse.
