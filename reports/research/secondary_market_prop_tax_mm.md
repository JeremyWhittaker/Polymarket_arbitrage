# Prop tax: anchored market making in MLB NRFI and total-8.5 markets

**Verdict: DEAD.** On the holdout, the pre-registered maker rule lost **-1.04 c per share filled
(-2.12% of notional)**, 95% CI by game [-2.32, +0.17] c. On development it made +0.41 c per share,
but that CI [-0.62, +1.52] c crosses zero. With fills 1 c worse, the holdout result is
-2.04 c per share, and that CI [-3.32, -0.83] c excludes zero. The premise does not hold in the
data. Total-8.5 books are not wide: the median quoted spread at our fills is 1 c, the same as the
moneylines. Takers in total-8.5 markets were not the losing side either: their dollar-weighted
gross P&L was +6.5% in development and +9.1% in the holdout.

Code: `pmsports/research/h_secondary_market_prop_tax_mm.py`. Cache and results:
`data/research/h_secondary_market_prop_tax_mm/` (`sample.parquet`, `trades/`,
`results_{dev,holdout}.json`, `fills_{dev,holdout}.parquet`).

## Rule as implemented (frozen before the holdout run)

| Step | Implementation |
|---|---|
| Sample | MLB games whose moneyline (`C.markets`) has `pre_usd >= $25k`. For each game, take `<event_slug>-nrfi` and `<event_slug>-total-8pt5` from `C.universe()` when they exist; the line is fixed, never chosen by volume. There are 3,411 candidates (DEV 2,093, HOLDOUT 1,318). A seeded random sample (seed 20260919) takes 1,000 DEV markets (475 NRFI / 525 total) and 1,000 HOLDOUT markets (505 / 495). DEV means game_start < 2026-07-01 UTC. |
| Data | Data API taker fills for [T-24h, T), T = scheduled `game_start_ts`: 2,000 markets, no errors. Side, price and payout are oriented by token id (`asset` -> universe `outcome_idx`), not by the API's `outcomeIndex` (see Bugs). Each (transactionHash, side acquired) is one taker order. The Data API already returns one row per order, at the order's VWAP. |
| Quote | From T-12h to T-15m we rest an ask on each token s. An ask on s is the same as a bid on the other token. Our ask on s at time t is the price of the last taker fill acquiring s with ts <= t-3 s. There is no quote if that fill is more than 6 h old. |
| Fill | A taker order acquiring s at q >= our ask fills us at our ask for min(size, K = 100) shares. Price comparisons use a tolerance of 1e-6. Net inventory is capped at 500 shares per market, and fills are truncated at the cap. |
| Pull | We hold no quotes in (t_ml, t_ml + 60 s] after any same-game moneyline fill whose outcome-0 price differs by >= 0.02 from the median of moneyline fills in [t_ml - 300 s, t_ml). The moneyline fills come from local tapes, oriented by token. Outcome-0 orientation gives the same triggers as home orientation. |
| P&L | Hold to resolution. Per share: `a - y_s + 0.15 * fee_rate * a * (1 - a)`. Makers pay no fee and collect a 15% rebate of the taker fee, using the market's own `fee_rate`: 0 in 2025, 0.03 in Mar-Jun 2026, 0.05 from Jul 2026. |
| Statistic | Total P&L / total shares filled, with a 95% bootstrap CI clustered by game (`C.cluster_ci` by `event_slug`, weighted by shares). ROI is P&L / notional, where notional = shares x (1 - a), our cost of the complementary side. Merging pairs would free capital, so this notional is conservative. |

## Main result

| Split | Our fills | Markets / games | Shares | Notional | P&L | c / share [95% CI] | ROI [95% CI] |
|---|---|---|---|---|---|---|---|
| DEV (2025-08..2026-06) | 25,252 | 814 / 673 | 714,549 | $351,051 | +$2,935 | **+0.41** [-0.62, +1.52] | +0.84% [-1.25, +3.10] |
| DEV, fills 1 c worse | | | | | -$4,211 | -0.59 [-1.62, +0.52] | -1.18% [-3.22, +1.04] |
| **HOLDOUT (2026-07-01..09-18)** | 19,043 | 844 / 586 | 709,036 | $348,713 | **-$7,407** | **-1.04** [-2.32, +0.17] | **-2.12%** [-4.72, +0.36] |
| HOLDOUT, fills 1 c worse | | | | | -$14,498 | -2.04 [-3.32, -0.83] | -4.07% [-6.62, -1.65] |

The holdout was run once, with parameters frozen. After that run, I only added the
spread-proxy diagnostic column. The re-run reproduced every number exactly (deterministic
seeds).

## Variants (all that were tried)

c/share with the 95% CI by game. ROI is per $ of notional.

| Variant | DEV c/sh [CI] | DEV ROI | HOLDOUT c/sh [CI] | HOLDOUT ROI |
|---|---|---|---|---|
| Primary (K=100, pull, hold) | +0.41 [-0.62, +1.52] | +0.84% | -1.04 [-2.32, +0.17] | -2.12% |
| (a) Markout to the last two-sided print mid in [T-60m, T) | +0.16 [+0.08, +0.24] | +0.33% | +0.15 [+0.07, +0.24] | +0.30% |
| (a) Same fills, held to resolution (markets with a mid) | +0.22 [-0.84, +1.29] | +0.44% | -1.20 [-2.50, +0.04] | -2.43% |
| (b) No pull rule | +0.39 [-0.64, +1.45] | +0.79% | -1.12 [-2.40, +0.11] | -2.27% |
| (c) K = 25 | +0.05 [-1.40, +1.56] | +0.11% | -1.12 [-3.10, +0.70] | -2.30% |
| (c) K = 500 | +0.60 [-0.19, +1.46] | +1.21% | -0.55 [-1.44, +0.30] | -1.11% |
| (d) Back-of-queue stress (fill only if a later same-side taker trades through our price within 60 s) | -5.36 [-9.64, -1.29] | -10.72% | -3.61 [-7.72, +0.65] | -7.36% |
| (e) NRFI only | -1.12 [-4.73, +2.54] | -2.34% | -1.24 [-4.52, +2.18] | -2.57% |
| (e) Total 8.5 only | +0.67 [-0.34, +1.67] | +1.36% | -0.98 [-2.23, +0.32] | -1.99% |

Diagnostic splits of the primary fills (not rules):

| Split | DEV c/sh [CI] | HOLDOUT c/sh [CI] |
|---|---|---|
| fee_rate 0 (2025-08..11) | +5.32 [+0.78, +11.03] (125 games) | - |
| fee_rate 0.03 | +0.02 [-1.03, +1.11] | -2.43 [-5.83, +1.07] (101 games) |
| fee_rate 0.05 | - | -0.85 [-2.22, +0.55] |
| 0.25-1 h before start | +0.59 [-2.14, +3.55] | +0.51 [-2.58, +3.46] |
| 1-3 h | +0.87 [-1.12, +3.02] | -2.53 [-5.73, +0.53] |
| 3-6 h | +1.19 [-1.83, +4.33] | -0.28 [-3.30, +2.78] |
| 6-12 h | -1.23 [-3.95, +1.43] | -1.41 [-4.16, +1.43] |
| Taker paid exactly our ask (we joined the touch) | +1.43 [+0.12, +2.84] | -0.78 [-2.28, +0.66] |
| Taker paid more than our ask (our stale quote was inside the book) | -4.17 [-7.94, -0.44] | -2.30 [-5.56, +1.03] |

By month (c/share): 2025-09 +9.5 (98 games), 2026-03 +3.8, 2026-04 -0.0, 2026-05 -1.2,
2026-06 +1.0, 2026-07 -1.5, 2026-08 -0.6, 2026-09 -0.9. The only strongly positive period was the
fee-free 2025 stretch, when these side markets were new. It is small (125 games), and from
April 2026 the edge is zero or negative.

(f) Taker diagnostic. Pregame taker P&L per $, [T-24h, T), dollar-weighted, 95% CI by game.

| Market | DEV gross | DEV net of taker fee | HOLDOUT gross | HOLDOUT net |
|---|---|---|---|---|
| Side markets, all ($10.4M DEV / $13.9M HOLDOUT) | +5.8% [-6.5, +17.9] | +4.3% | +7.5% [-1.4, +15.8] | +5.0% |
| NRFI ($0.53M / $1.58M) | -8.5% [-22.7, +7.3] | -9.5% | -5.3% [-18.9, +8.9] | -7.3% |
| Total 8.5 ($9.9M / $12.3M) | +6.5% [-6.7, +19.3] | +5.0% | +9.1% [-0.5, +18.5] | +6.6% |
| Same games' moneylines ($108M / $60M) | -3.1% [-9.1, +2.9] | -4.0% | -6.1% [-14.0, +1.5] | -8.1% [-15.9, -0.7] |

In total-8.5 markets the flow pays no "recreational tax": takers there did better than
moneyline takers in both periods. NRFI takers did lose on a dollar-weighted basis, but that loss
is not significant, and our capped last-print quotes did not capture it (NRFI maker result
-1.1 / -1.2 c).

## Why it fails

1. **The spread is not wide.** The quoted-spread proxy at our fills is our ask on s plus our
   ask on the other side, minus 1. For total 8.5 the median is 1.0 c (mean 1.0 c), and the
   market is two-sided 95-97% of the time. For NRFI the median is 2.0 c in DEV and 1.0 c in the
   holdout, and the market is two-sided only 77% of the time. Joining a 1 c spread gives at most
   0.5 c per share before adverse selection.
2. **Adverse selection takes almost all of it.** The markout to the pregame close is only
   +0.15 c per share. That is statistically positive in both periods but economically nil: 0.3%
   of notional, about $1.5-1.9 per market. Held to resolution, the result is dominated by outcome
   variance, and it is negative in the holdout.
3. **Stale quotes get picked off.** Our quote is the last print, which can be hours old in NRFI.
   When the next taker pays more than our ask, the book had already moved past us. Those fills
   lose -4.2 c (DEV) and -2.3 c (holdout) per share.
4. **The flow is one-directional.** 28% (DEV) and 39% (holdout) of filled markets hit the
   500-share inventory cap. NRFI takers bought "No" over "Yes" by 1.7:1 in DEV and 2.1:1 in the holdout. We therefore carry directional
   inventory rather than earning spread from both sides.
5. **The pull rule does almost nothing.** It removed 261 of 36,167 DEV events and 58 of 34,643
   holdout events. Its effect on P&L is 0.02-0.08 c per share. Pregame moneylines rarely jump
   2 c against their 5-minute median.
6. **Queue position.** The primary rule assumes we sit at the front of the queue at the touch.
   Under the back-of-queue stress we lose 3.6-5.4 c per share.

## Capacity

With front-of-queue fills we average about 710 shares and about $350 of notional per market.
There are 14.5 candidate markets per day in DEV and 16.9 in the holdout, so the whole
opportunity is about $5-6k of notional per day. P&L per market was +$2.93 in DEV and -$7.41 in
the holdout. That is about +$43 per day at the DEV rate and about -$125 per day at the holdout
rate. Even at the DEV rate this is not worth running a bot for, and the back-of-queue stress
turns it negative.

## Bugs found and fixed during development (before the holdout run)

- **The Data API's `outcomeIndex` is sometimes wrong.** In 11 of 1,000 DEV side markets (0.82% of
  fills), the same token (`asset`) appears with both outcomeIndex 0 and 1. Prices are
  self-consistent only under the token mapping: 0.55/0.54 for Over, against 0.45/0.55 flip-flops
  under outcomeIndex. Everything here is oriented by token. The same issue affects 0.06% of
  moneyline fills in 3 of the sampled games. **The shared `C.fills()` dataset
  (`wallets/tapes.load_trades`) orients by `outcomeIndex`, so a small share of its fills probably
  have the wrong side and payout.** This is worth checking for the whole repo.
- **Float noise in Data API prices.** A price of 0.49 arrives as 0.4899999951, so a strict
  `q >= a` comparison dropped many fills where the taker paid exactly our ask. Comparisons now
  use a tolerance of 1e-6. This fix left DEV at +0.41 c.
- **Taker-only check.** The Data API `/trades` default is taker-only: 9 rows by default against 19
  with `takerOnly=false` on a test market.
- **Hand check.** I traced one NRFI market (mlb-lad-ari-2026-06-03) fill by fill. The stale-quote
  cut-off, the 3 s lag, the T-15m cut-off, the below-ask skips, the payout sign (sold "Yes" at
  0.49, "Yes" lost, +0.49 plus rebate) and the rebate (0.15 x 0.03 x 0.49 x 0.51 = 0.0011) were
  all correct.

## Caveats

- **Fill model.** The fill model uses taker prints only. There are no historical order books,
  so "the touch" is the last taker price on each side. The Data API reports a sweeping order at
  its VWAP, so a VWAP can stand in for a tick. When the two last prints cross (DEV 348 events,
  holdout 683), our two quotes are crossed with each other. This is kept as specified; it hurts
  us.
- **Sample selection.** `pre_usd` covers moneyline volume up to T, while we quote until T-15m.
  This is a mild use of information after the decision, but it concerns liquidity, not the
  outcome, and it applies equally to both splits.
- **Fee regimes.** DEV mixes fee-free 2025 (only 125 games, where the result was positive) with
  the 3% fee regime of 2026. The holdout is entirely the 5% fee regime. Scheduled rather than
  actual first pitch sets T.
- **Sport coverage.** Only MLB was tested, by design, so there is no per-sport split beyond
  NRFI against total 8.5. Soccer over/under was excluded to avoid overlap with other hypotheses.
- **Variants.** Nine variants plus diagnostic splits were tried. None is positive in the holdout
  except the markout, which measures spread capture rather than a tradeable P&L and comes to
  0.15 c per share.
