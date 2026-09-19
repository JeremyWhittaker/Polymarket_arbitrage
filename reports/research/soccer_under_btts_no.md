# Selling Over 2.5 and BTTS-Yes to retail pregame (`soccer_under_btts_no`)

**Verdict: DEAD.** The flow bias is real: 61% (DEV) and 73% (HOLDOUT) of pregame taker tickets buy Over or Yes. The price bias is not. On the pre-registered primary rule, a maker selling Over/Yes loses in the holdout: -1.04c per share, ROI -2.32% [-9.65%, +4.99%]. With 1c worse fills it is -4.47%. In DEV the result was +3.74%, but its CI crosses 0 ([-3.69%, +11.01%]). Pooled over both periods, the implied Over/Yes price is within about 1c of the hit rate. The BTTS-Yes overpricing seen in DEV (-3.7c) reversed in the holdout (+0.8c).

Code: `pmsports/research/h_soccer_under_btts_no.py`. Cache and results: `data/research/h_soccer_under_btts_no/`
(`sample.parquet`, `candidates.parquet`, `trades/`, `fills_{dev,holdout}.parquet`, `results_{dev,holdout}.json`).

## Rule as implemented (frozen before the holdout run)

- **Sample.** Only the schedule is used to select markets: no volume filter, no outcome filter. From `C.universe()`, take soccer markets whose slug is exactly
  `<match>-total-2pt5` (market_type `totals`) or `<match>-btts` (`both_teams_to_score`). `<match>` is
  `<league>-<a>-<b>-<yyyy-mm-dd>`. League is one of premier-league, la-liga, bundesliga, serie-a, ligue-1, ucl, uel,
  europa-conference-league, efl-championship, ere, primeira-liga, mls, brazil-serie-a, mex, tur or soccer-fifwc, and may carry a
  `-20xx` season suffix. `la-liga-2`, `bundesliga-2` and `tur2-games` are second divisions, not season suffixes, so they are excluded.
  Start dates run from 2025-10-01 to 2026-09-18. The candidates are 6,621 DEV markets (3,396 matches) and 2,476 HOLDOUT markets
  (1,238 matches). The seeded random sample is 1,000 DEV markets (938 matches) and 1,000 HOLDOUT markets (792 matches), and every market was fetched.
- **Start time T** = min(the market's `game_start_ts`, the same match's moneyline `game_start_ts`). *This check found a trap.* The
  September-2025 O/U markets carry a start 4 hours after kickoff. For example, `ucl-juv-dor-2025-09-16-total-2pt5` has 23:00 UTC while the
  moneyline has 19:00, and its "pregame" tape contains Over bought at 0.96 during play. These markets fall before the 2025-10-01 bound. After
  that date, only 7 of 9,097 candidates disagree with their moneyline. Taking the earlier start makes sure no in-play print is counted.
- **Tape.** Data API taker fills in [T-24h, T), with HOST_RPS = 5. The side comes from the token id; the API's `outcomeIndex` agreed on 100% of rows.
  A taker SELL of a token counts as acquiring the other token at 1 - price. One ticket = (tx, side acquired). The Data API
  already returns one row per tx, with 0 multi-price tickets.
- **Primary: maker sells Over/Yes.** Every pregame taker ticket that acquires Over (O/U) or Yes (BTTS) fills us for
  `min(size, 100)` shares at the taker's price q. P&L per share = `q - y_over + 0.15 * fee_rate * q * (1-q)`, with no maker fee and the
  market's own fee_rate. Within a market, the mean is weighted by shares. The primary statistic is the equal-weight mean across markets with at least 1 fill.
  The 95% CI is `C.cluster_ci` by match, so the O/U and BTTS markets of one match share a cluster. ROI per $ = sum of per-market P&L / sum
  of per-market notional, where notional per share is 1 - q: selling Over at q is the same as buying Under at 1 - q.
- **+1c sensitivity (maker):** every fill 1c worse (P&L - 0.01, notional + 0.01).

## Sanity checks

| check | DEV | HOLDOUT |
|---|---|---|
| share of pregame taker tickets buying Over/Yes | 60.8% (BTTS 68%, O/U 58%) | 73.1% |
| same, tickets < $100 | 60.3% | 73.2% |
| share of prints with implied Over < 0.05 or > 0.95 in the last 10 min (in-play leak test) | 0.0% | 0.0% |
| median last-hour "spread" (last Over ask + last Under ask - 1) | 1c (p90 2c) | – |
| payout orientation: Over hit rate by implied-price bucket (BTTS, DEV) | 0.43 / 0.48 / 0.54 / 0.63 for p = .47 / .53 / .58 / .64 | monotone |
| fee rates in sample | 0 (707), 0.03 (291), 0.0175 (2) | 0.05 (880), 0.03 (120) |

The outcome index is read from the outcome names (Over/Yes = index 0 in every market). The hit rate rises with the implied price, so
the payouts are not reversed. No prints from after kickoff leak into the pregame window.

## Primary results

| | markets | matches | captured $ | c / share [95% CI] | ROI [95% CI] | ROI +1c worse |
|---|---|---|---|---|---|---|
| **DEV pooled** | 949 | 890 | $1.64M | **+1.71 [-1.69, +5.04]** | **+3.74% [-3.69, +11.01]** | +1.53% [-5.74, +8.64] |
| DEV BTTS | 446 | 446 | $0.74M | +4.19 [-0.39, +8.69] | +9.27% [-0.85, +19.20] | |
| DEV O/U 2.5 | 503 | 503 | $0.91M | -0.48 [-4.81, +3.90] | -1.03% [-10.37, +8.39] | |
| **HOLDOUT pooled** | 991 | 787 | $1.83M | **-1.04 [-4.31, +2.22]** | **-2.32% [-9.65, +4.99]** | **-4.47% [-11.65, +2.69]** |
| HOLDOUT BTTS | 513 | 513 | $1.09M | -0.65 [-5.11, +3.82] | -1.45% [-11.34, +8.52] | |
| HOLDOUT O/U 2.5 | 478 | 478 | $0.74M | -1.45 [-5.71, +2.88] | -3.27% [-13.00, +6.48] | |
| DEV + HOLDOUT (context only) | 1,940 | 1,677 | $3.48M | +0.31 [-1.98, +2.67] | +0.68% [-4.42, +5.90] | |

Without the rebate the holdout result is -1.21c (-2.71%). World Cup markets are 12-21 matches holding 44% (DEV) and 62% (HOLDOUT) of all captured shares. Excluding them changes
nothing: DEV +4.27% [-3.08, +11.20], HOLDOUT -2.29% [-10.28, +5.44].

## Variants (all reported; none drives the verdict)

| variant | DEV | HOLDOUT |
|---|---|---|
| (a) taker buys Under/No at the first print ≥ T-10m+3s, taker fee, 1 bet/market | +4.82% [-4.90, +14.04], 558 bets | -4.92% [-14.16, +4.48], 585 bets |
| (a) same, +1c slippage | +2.54% [-6.96, +11.52] | -6.93% [-15.98, +2.24] |
| (a) BTTS-No only, +1c | +6.66% [-8.05, +20.53] | -6.61% [-20.22, +6.70] |
| (a) Under 2.5 only, +1c | -0.30% [-12.20, +11.59] | -7.18% [-18.11, +4.04] |
| (b) control: maker sells Under/No | +0.06c, +0.11% [-6.03, +6.39] | +2.38c, +4.35% [-2.08, +10.58] |
| (d) Over/Yes tickets < $100 only | +1.73c, +3.77% [-3.40, +11.11] | -1.08c, -2.41% [-9.74, +4.89] |
| (d) complement: tickets ≥ $100 | +1.24c, +2.71% [-5.92, +10.73] | -1.78c, -4.06% [-12.90, +4.40] |
| (e) share-weighted instead of equal-weight | -0.22c, -0.47% [-25.1, +23.4] | +1.38c, +2.91% [-39.5, +43.1] |
| diag: back-of-queue stress (fill only if a later same-side ticket trades through our price within 60 s) | -1.46c, -3.16% [-12.18, +5.68] | -1.94c, -4.33% [-15.16, +6.69] |

The control (b) is the key comparison. If retail overpaid for Over/Yes, selling Over should beat selling Under. In DEV it did: +1.7c
against +0.1c, a gap driven entirely by BTTS (+4.2c against -1.9c). In the holdout it reversed: -1.0c against +2.4c. This is what
noise around a fair price looks like.

### (c) Calibration: median implied Over/Yes price over [T-60m, T) against hit rate, per market

| | DEV n | DEV price | DEV hit | hit - price [CI] | HOLDOUT n | HOLDOUT price | HOLDOUT hit | hit - price [CI] |
|---|---|---|---|---|---|---|---|---|
| all | 959 | 0.539 | 0.527 | -1.24c [-4.41, +1.79] | 996 | 0.554 | 0.565 | +1.15c [-2.04, +4.44] |
| BTTS Yes | 454 | 0.546 | 0.509 | -3.68c [-8.25, +0.49] | 513 | 0.551 | 0.559 | +0.80c [-3.68, +5.24] |
| Over 2.5 | 505 | 0.533 | 0.543 | +0.94c [-3.17, +5.05] | 483 | 0.556 | 0.571 | +1.52c [-2.84, +6.01] |

By month, DEV runs from -3.6c to +7.5c and every monthly CI includes 0. The holdout months are Jul -0.4c, Aug -1.2c and Sep +6.2c [+0.1, +12.5].
There is no persistent sign.

## Splits (primary, c/share; all soccer, so the split is by league)

- Hours before kickoff (HOLDOUT): <10 min -1.42, 10-60 min -1.46, 1-3 h -2.10, 3-6 h -1.51, 6-24 h -1.82. The result is flat and ≤ 0 at every
  horizon. In DEV the same buckets were all between +1.0 and +2.6, and none was significant.
- Fee regime: DEV fee 0 +1.44 [-2.31, +5.27], DEV fee 0.03 +2.01 [-3.81, +7.58]. HOLDOUT fee 0.03 -3.45, HOLDOUT fee 0.05 -0.71
  [-4.23, +2.85].
- League (HOLDOUT, 10-179 markets each): the range runs from Serie A -16.6c to Turkey +8.2c, and every CI includes 0. DEV had one league
  CI above 0 (Ligue 1 +11.0c [+0.15, +22.4]), about what 16 leagues produce by chance. That league was -2.1c in the holdout.
- Over price bucket (HOLDOUT): q < 0.5 -3.6c, 0.5-0.6 +2.1c, 0.6-0.7 -3.2c. There is no structure.

## Capacity

With the optimistic front-of-queue fill model (every Over/Yes ticket, up to 100 shares each), a maker captures a median of about 700-960
shares, or $300-435 of notional, per market over 24 hours. The mean is $1,644 (DEV) and $1,831 (HOLDOUT), driven by World Cup markets.
There are about 26 (DEV) and 31 (HOLDOUT) such markets per day in these leagues, so the ceiling is roughly $43-57k of notional per day
(about $1.3-1.7M a month). At the holdout ROI of -2.3% that capacity loses money. Even at the pooled point estimate (+0.3c/share, not
significant) it would be worth about $2-11 per market. The realistic back-of-queue fills are about 4-8% of these shares and negative in both periods.

## Caveats

- **Power.** Each market settles 0/1, so the 95% CI half-width is about ±3.3c per share for 800-900 matches per period. An edge below
  about 2c/share could not be seen with 2,000 markets. The data can say that "there is no Over/Yes premium of ≥ 3c", not "there is exactly zero".
- **Fill model.** The primary assumes we are at the front of the queue on every Over/Yes print, which is optimistic. The back-of-queue stress is
  pessimistic because it keeps only fills where the price then moved against us. Both are ≤ 0 in the holdout.
- **Rebate.** The 15% rebate is an approximation. Without it the holdout result is -2.71% instead of -2.32%.
- **Correlation.** O/U 2.5 and BTTS in the same match are correlated (outcome correlation 0.53), so they share a cluster in every CI.
- **Start times.** Using the earlier of the market and moneyline starts guards against the September-2025 +4 h error. A game that kicked off
  later than scheduled only shortens our window, which does not bias the result.
- **Where the flow bias goes.** The Over/Yes skew in taker flow is real and grew under the 5% fee (73% of tickets in the holdout). The makers
  who take that flow price it fairly: Over/Yes buyers' fills show no measurable premium, and O/U and BTTS spreads are about 1c.
  A neg-risk link is not needed to absorb one-sided retail flow. Ordinary market makers lean their quotes.
- Multiple testing: 2 market types, 5 variants, and about 30 diagnostic splits. The few nominally significant cells (DEV Ligue 1, HOLDOUT
  September calibration) are what chance predicts, and they point in opposite directions.

## Verdict

**DEAD.** The holdout ROI after costs is negative (-2.32%, and -4.47% at +1c), and the DEV CI lower bound is below 0 (-3.69%).
Takers buying Under/No (variant a) are also negative in the holdout (-4.9% before slippage). There is no Over/Yes premium in Polymarket O/U 2.5 or BTTS
that survives out of sample.
