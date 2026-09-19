# Tennis "Completed Match?": sell Yes to certainty buyers

**Verdict: DEAD.** The pre-registered maker rule loses money in development and in the holdout. Both CIs on the
primary statistic lie entirely below zero:

| period | markets with a Yes fill | match dates | fills | shares | capital | P&L | primary: c/share, equal-weight by market [95% CI] | c/share, share-weighted [95% CI] | ROI on capital [95% CI] |
|---|---|---|---|---|---|---|---|---|---|
| DEV (2026-05-09 .. 06-30) | 129 | 37 | 202 | 6,608 | $1,353 | -$76 | **-6.26 [-11.55, -0.82]** | -1.15 [-7.61, +4.69] | -5.6% [-51.1, +39.9] |
| DEV, fills 1c worse | 129 | 37 | 202 | 6,608 | $1,419 | -$142 | -7.26 [-12.55, -1.82] | -2.14 [-8.61, +3.69] | -10.0% [-54.3, +26.9] |
| **HOLDOUT (07-01 .. 09-18)** | **480** | **66** | **2,006** | **163,233** | **$11,494** | **-$5,113** | **-4.45 [-7.70, -1.70]** | -3.13 [-7.75, +0.68] | **-44.5% [-76.9, +12.2]** |
| HOLDOUT, fills 1c worse | 480 | 66 | 2,006 | 163,233 | $13,127 | -$6,735 | -5.45 [-8.69, -2.69] | -4.13 [-8.75, -0.31] | -51.3% [-79.4, -4.8] |

The certainty premium isn't there. Takers who buy "Yes, completed" before the match make money on average,
so the side that sells to them loses. Most of the loss comes from Yes bought below 97c, where the market
*under*prices completion. At 97-99c, which is the "free coupon" the hypothesis is about, selling Yes is about
break-even: +2.19c equal-weight [-0.16, +5.49] and +0.18c share-weighted [-1.65, +4.00] in the holdout. Even if
that slice were real, it would deploy only about $36 of capital a day across every ATP and WTA match.

Code: `pmsports/research/h_tennis_completion_no.py`. Caches and results are in
`data/research/h_tennis_completion_no/`:
- `sample.parquet`
- `trades/<condition_id>.parquet` (2,000 Data API tapes, [T-48h, T))
- `results_dev.json` and `results_holdout.json`
- `maker_markets_*.parquet`
- `variant_a_*.parquet` and `variant_x_*.parquet`

## Hypothesis

"Yes, completed" looks like a 97-99c coupon. In fact 6.5% of ATP and 5.2% of WTA completion markets since
2026-05-09 resolved No. The market type is new (May 2026), thinly made, and has no arbitrage anchor. If
yield-seekers overpay for Yes, a maker who sells Yes to them (equivalently, bids No) collects the premium.

**Resolution rule (checked on gamma, May and September 2026 markets).** Yes requires that the match be "played to
completion through normal play". "If a forfeit of any kind occurs, including but not limited to a walkover or
retirement, this market will resolve No." A match that is cancelled, not played, tied, or not finished within
7 days also resolves No; later markets allow 14 days. **A walkover before the first ball is therefore No.** In the
universe, 142 of the 451 No outcomes since 05-09 closed before T+30 min, which marks them as pre-match
withdrawals.

## Rule as implemented (pre-registered, frozen before the holdout run)

- **Universe.** Every `tennis_completed_match` market in `C.universe()` with league in {atp, wta} and
  `game_start_ts` >= 2026-05-09. That is 7,401 markets: 2,700 DEV and 4,701 HOLDOUT. There is no volume filter
  on the analysed set. T = `game_start_ts`, the scheduled start, which is the same T as the event's moneyline.
- **Fetch-budget shortcut (does not change the statistic).**
  - A market whose total volume is exactly 0 has no fills, so it can never enter a statistic defined over
    markets with >= 1 fill.
  - We therefore drew the fetch sample from markets with volume > 0 in seeded random order: all 1,098 DEV
    markets and 882 of the 1,683 HOLDOUT markets.
  - We also fetched 20 random volume = 0 markets as a check. All 20 were empty.
  - Total: 2,000 markets.
  - Caution: volume > 0 is itself strongly outcome-correlated. Its markets are 12% not completed, against
    2.5% for zero-volume markets. That is harmless here only because the zero-volume markets would have been
    dropped anyway.
- **Fills.** Data API taker fills for [T-48h, T), normalised to "taker acquired side s at q". The token id is
  authoritative; `outcomeIndex` disagreed on 1 row of 4,189. Rows of one taker order at one price level (same
  tx, wallet, side, price) are merged. No fill in the analysis window has a timestamp after the market's close.
- **Primary (maker sells Yes).** Every fill acquiring Yes with ts in [T-6h, T) counts, up to 200 shares
  per fill.
  - P&L per share = q - y_yes + 0.15 · fee_rate · q(1-q). The maker pays no fee and receives a 15% rebate
    (< 0.1c here). Voids pay 0.5; the sample contains none.
  - Each market gets the share-weighted mean of its fills. The statistic is the equal-weight mean over markets
    with >= 1 such fill.
  - The 95% bootstrap CI is clustered by match date (UTC date of T).
  - Capital per share is 1 - q, the No collateral.
- **+1c sensitivity.** Every fill is priced 1c worse for us (q - 0.01).
- **Verdict statistic.** The equal-weight c/share. ROI on capital has the same sign in both periods.

## Why it fails: Yes buyers are the informed side

Primary markets split by outcome:

| period | outcome | markets | mean Yes price the taker paid | maker c/share |
|---|---|---|---|---|
| DEV | completed | 121 | 0.905 | -9.5 |
| DEV | not completed | 8 | 0.430 | +43.0 |
| HOLDOUT | completed | 458 | 0.916 | -8.4 |
| HOLDOUT | not completed | 22 | 0.777 | +77.7 |

- **Completion rate among markets with pregame Yes buying:** 93.8% in DEV and 95.4% in the holdout.
- **Prices paid are lower than the hypothesis assumed.** The share-weighted mean Yes price the takers paid was
  0.795 in DEV and 0.930 in the holdout.
- **Below 97c, the buyer wins.** On completed matches, the market charges less than the completion probability.
- **In non-completed matches, the buying stops early.** Takers bought Yes only at already-depressed prices
  (0.43 and 0.78), so a seller collects far less than a full coupon.

**Variant (c), by the Yes price of the fill** (maker sells Yes; c/share):

| bucket | DEV markets | DEV EW [CI] | DEV SW [CI] | HOLDOUT markets | HOLDOUT EW [CI] | HOLDOUT SW [CI] | HOLDOUT ROI on capital [CI] | HOLDOUT EW, 1c worse |
|---|---|---|---|---|---|---|---|---|
| q < 0.97 | 65 | -14.18 [-26.04, -5.65] | -5.07 [-18.26, +5.56] | 337 | -8.34 [-13.69, -3.84] | -5.90 [-13.40, -0.23] | -52.6% [-81.4, -2.9] | -9.33 |
| 0.97 <= q <= 0.985 | 41 | +0.14 [-2.40, +5.69] | +2.53 [-2.19, +12.23] | 184 | +1.08 [-1.15, +4.37] | +0.21 [-1.69, +3.92] | +10.0% [-81.5, +186] | +0.09 |
| q > 0.985 | 28 | +2.60 [-1.01, +11.03] | +1.75 [-1.07, +8.66] | 47 | +7.53 [+1.01, +17.37] | -0.54 [-0.94, +0.58] | -54.3% [-95.6, +59.0] | +6.54 |
| q >= 0.97 (union) | 66 | +1.26 [-1.84, +6.52] | +2.10 [-1.55, +8.28] | 215 | +2.19 [-0.16, +5.49] | +0.18 [-1.65, +4.00] | +8.9% [-81.2, +191] | +1.20 |

What the buckets show:
- **Only the 97-99c zone is positive, and it is not significant.** The pre-registered mechanism lives in
  this zone. Point estimates are positive in both periods, but every CI crosses 0.
- **The q > 0.985 holdout estimate is fragile.** Its equal-weight +7.5c CI excludes zero, but it rests on
  4 non-completions of 47 markets. Those markets had 5, 5, 1 and 1 share(s) bought at 0.99, and the Yes price
  later collapsed to 0.01-0.05 in three of them (late withdrawals). Share-weighted, the bucket is -0.54c.
- **Multiple testing.** This is one of about 12 slices, so a single "significant" bucket is what chance
  produces.
- **Taker P&L confirms the pattern (holdout, [T-6h, T), capped at 200 shares).**

  | Yes price | shares | P&L to Yes buyers |
  |---|---|---|
  | below 0.90 | 5.7k | +$4,381 |
  | 0.90-0.97 | 83k | +$901 |
  | 0.97-0.985 | 72k | -$138 |
  | 0.985 and above | 2.7k | +$15 |

  Above 97c the book is close to fair. Below it, Yes is cheap.

## Variant (d): calibration of the pregame Yes price

The price is the last Yes-converted print in [T-6h, T), set against the completion rate. The gap is price minus
completion, in c.

| bucket | DEV n | DEV price | DEV completion | DEV gap [CI] | HOLD n | HOLD price | HOLD completion | HOLD gap [CI] |
|---|---|---|---|---|---|---|---|---|
| [0, 0.5) | 52 | 0.069 | 0.231 | -16.2 [-26.0, -5.5] | 93 | 0.046 | 0.656 | -61.0 [-70.6, -50.5] |
| [0.5, 0.9) | 9 | 0.693 | 1.000 | -30.7 | 7 | 0.631 | 1.000 | -36.9 |
| [0.90, 0.95) | 31 | 0.925 | 1.000 | -7.5 [-8.1, -6.2] | 199 | 0.940 | 0.970 | -3.0 [-5.4, -0.2] |
| [0.95, 0.97) | 12 | 0.953 | 1.000 | -4.7 [-4.9, -4.4] | 52 | 0.953 | 0.981 | -2.8 [-4.8, -0.6] |
| [0.97, 0.985) | 38 | 0.976 | 0.974 | +0.3 [-2.5, +6.2] | 115 | 0.978 | 0.983 | -0.5 [-2.2, +2.7] |
| [0.985, 0.995) | 25 | 0.990 | 0.960 | +3.0 [-1.1, +12.6] | 64 | 0.990 | 0.922 | +6.8 [+0.8, +14.4] |

What the calibration shows:
- **0.90-0.97.** Yes completes more often than its price implies in both periods, which is the opposite of
  the hypothesis.
- **0.97-0.985.** Price and completion match closely.
- **0.985-0.995.** Only the last-print version shows a premium (+3.0c DEV, +6.8c holdout). By VWAP the
  holdout bucket has 23 markets, all completed, a gap of -1.0c.
- **Below 0.5.** These rows are contaminated by dump prints. In the holdout, one wallet (0xcf218ae4…) bought
  Yes at 0.98 in 19 markets and sold the same size a few minutes later at 0.03-0.06, a ~95c loss each time.
  This looks like transfers between wallets or wash trading. Those prints set many "last prices" near 0 in
  matches that then completed. They do not enter the primary: the dumps acquire No, and only the 0.98 Yes
  purchases count, at 0.98.

## Other variants

**Variant (b), window [T-24h, T):**

| period | markets | EW c/share [CI] | SW c/share | ROI on capital [CI] |
|---|---|---|---|---|
| DEV | 312 | -4.20 [-7.17, -1.07] | +0.14 | +0.8% [-27.3, +21.5] |
| HOLDOUT | 519 | -4.43 [-7.80, -1.40] | -3.15 | -41.1% [-71.9, +10.1] |

**Variant (c), by tour (per-sport split; only tennis is in scope):**

| tour | DEV markets | DEV EW c/share [CI] | HOLDOUT markets | HOLDOUT EW c/share [CI] | HOLDOUT ROI on capital [CI] |
|---|---|---|---|---|---|
| ATP | 83 | -9.03 [-14.34, -4.40] | 353 | -3.85 [-7.42, -0.71] | -31.3% [-74.5, +37.1] |
| WTA | 46 | -1.26 [-9.41, +9.29] | 127 | -6.12 [-10.60, -2.38] | -77.7% [-97.3, -41.8] |

**Variant (a), taker buys No near the start: untestable.**
- **Rule.** At D = T-10 min, if the median No price over [T-60, T-10 min] is below base_rate - 1c, buy No at
  the first No-acquiring print in [D+3 s, T). base_rate is the tour's non-completion rate over markets that
  started in the prior 60 days and closed before D; at least 50 such markets are required.
- **Signals fire but cannot fill.** There were 25 signals in DEV and 114 in the holdout, because No trades at
  1-3c against a 7% base rate. But nobody acquires No in the last 10 minutes: 0 DEV bets and 1 holdout bet
  (bought No at 0.01, lost). No inference is possible.
- **The base rate overstates the tradeable one.** It includes walkovers that are public long before T.

**Variant (e), including ITF: not run.** The 2,000-market fetch budget went to the pre-registered ATP/WTA sample.

**(x) Post-hoc reverse rule: noise.** Taker buys Yes after a 0.90-0.97 print. This rule came from the DEV
calibration, was not pre-registered, and was frozen before the holdout run.
- **Rule.** The signal is the first print in [T-6h, T) with a Yes price in [0.90, 0.97). Buy Yes at the first
  Yes-acquiring print at least 3 s later and before T, and pay the taker fee.
- **Results.**

  | period | signals | bets | completion | P&L per bet [CI] | capital-weighted ROI [CI] | P&L, 1c worse [CI] |
  |---|---|---|---|---|---|---|
  | DEV | 45 | 7 | — | -9.4c | — | — |
  | HOLDOUT | 340 | 134 | 95.5% | +0.53c [-3.78, +4.27] | +0.56% [-3.9, +4.7] | -0.42c [-4.73, +3.31] |

- **Why it fails.** The cheap-Yes pattern in the calibration table sits in prints that already happened. The
  next executable print is roughly fairly priced.

## Capacity

The holdout sample covers 882 of 1,683 volume > 0 markets. Scaled to all ATP/WTA completion markets:
- **All pregame Yes buying (primary):** takers bought about 3,900 Yes shares a day in [T-6h, T), about
  $3,600 of notional. Taking the other side of all of it needed about $270 of capital a day and lost about
  $120 a day.
- **The 97c-and-above slice:** about 1,760 shares a day, about $36 of capital a day at 2c collateral per
  share. P&L is +$3 a day share-weighted (CI about -$30 to +$70), statistically indistinguishable from 0.
- **DEV (May-June):** 10-30 times thinner than the holdout (129 active markets in 54 days).

Even a genuine 1-2c edge here would be pocket change.

## Checks and caveats

- **Orientation.** Yes = `outcome_idx` 0 in all 27,979 tennis completion markets (checked; asserted in code for ATP/WTA). y_yes is the
  universe payout of the Yes token. The Data API token matches the universe token map on every row but one.
- **Timing.**
  - T is the scheduled start. Tennis usually starts later, so [T-6h, T) is pregame by construction.
  - Five DEV and four holdout primary markets closed before T+30 min, meaning a walkover or an earlier start.
    Dropping them changes little (-7.19c DEV, -4.75c holdout).
- **The maker fill model is generous.**
  - We assume we are the maker on every Yes-acquiring fill (up to 200 shares), at whatever price the taker
    paid. A real maker quoting at 97-99c would never have sold at 0.43 or 0.90, so the primary really
    measures "the counterparty of all pregame Yes buyers".
  - That is exactly what the hypothesis said was mispriced. It isn't: the counterparty loses.
  - The price-bucket rows show what a disciplined high-price seller would have seen, with queue position
    ignored, which again flatters us.
- **Rebates.** The feeSchedule shows `rebateRate` 0.25 for May-early July markets and 0.15 later. At these
  prices the rebate is < 0.1c per share either way.
- **Small, lumpy outcomes.** Each market is a Bernoulli event with ~5% No. The high-price buckets carry 1-4
  non-completions, and single late withdrawals move the equal-weight means by several cents.
- **Market history is short.** The market type exists only since 2026-05-09. DEV has 129 active markets in
  37 match dates.
- **Selection.** No volume, outcome or post-start information enters the sample. The only filter is volume > 0
  (explained above; verified on 20 empty markets). Rows are included when there was a pregame Yes fill in the
  window, which is information available before the decision time.
