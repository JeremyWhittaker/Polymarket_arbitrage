# Soccer O/U 2.5 lagging the 3-way moneyline pregame (`soccer_ou_vs_ml_pregame`)

**Verdict: DEAD.** On the pre-registered primary rule the holdout loses -19.4% per $1 after fees
([-44.7%, +7.0%], 67 bets), and -21.3% with 1c slippage. The DEV figure (+10.8%, [-3.9%, +25.5%]) is in-sample
because the isotonic calibration was fitted on the same DEV events. Out-of-fold, the same DEV rule returns -8.8% (5-fold cross-fit)
and -3.5% (fit before March, test March-June). The mechanism itself is absent in both periods:

- After a signal, the O/U price does not move toward the moneyline-implied value (-0.05c DEV, -0.02c HOLDOUT).
- The O/U market is not stale at decision time: the last print is a median of 30-39 s old.
- Once you condition on the O/U price, the moneyline-implied total carries *negative* information about the outcome.

Code: `pmsports/research/h_soccer_ou_vs_ml_pregame.py`. Cache and results: `data/research/h_soccer_ou_vs_ml_pregame/`
(`events.parquet`, `ou_tokens.parquet`, `tapes/` (583 O/U tapes), `calibration_dev.json` (frozen), `panel_holdout.parquet`,
`bets_primary_{dev,holdout}.parquet`, `results_{dev,holdout,diag}.json`, `run_{dev,holdout}.log`).

## Rule as implemented (frozen before the holdout run)

- **Universe (pregame info only).** Soccer events in `C.markets()` that have both team-win legs (`<event_slug>-<team>`, not `-draw`),
  each with `pre_usd >= $50k`, plus a linked O/U 2.5 market `<event_slug>-total-2pt5` in `C.universe()` with valid payouts.
  That gives 603 events with both legs over $50k and 583 with an O/U 2.5 market: 448 DEV (T < 2026-07-01, 2025-12-04 .. 2026-06-30) and 135 HOLDOUT
  (Jul 1 - Sep 18 2026). T is the legs' `game_start_ts`. It is identical for both legs and for the O/U market in all 583 events.
- **O/U tapes.** Data API taker fills in [T-24h, T), fetched with HOST_RPS = 5. All 583 were fetched with no errors. The side comes from the token id, and the API's `outcomeIndex`
  agrees on 100% of rows. A taker SELL of a token counts as acquiring the other token at 1 - price. On the 46 markets that overlap with the
  `soccer_under_btts_no` cache, the tapes are identical.
- **Model at D = T-10 min.** pH and pA are the unweighted medians of Yes-converted team-leg fill prices over [T-20m, D], with at least 1 fill per leg.
  pD = 1 - pH - pA is clipped to [0.05, 0.5]. When the clip binds (2-3% of events), pH and pA are rescaled proportionally to 1 - pD. The rule then solves an
  independent Poisson (lam_h, lam_a) with P(H) = pH and P(A) = pA, and sets P_model = P(total >= 3).
  *Integrity filter (added after seeing DEV, uses only pre-decision data):* skip an event if a team leg's median is <= 1c or >= 99c.
  This removes 8 events (7 DEV, 1 HOLDOUT) whose scheduled start was after the real kickoff, so they were in play or finished at D.
  Examples: `efa-mnc-liv-2026-04-04` with the home leg at 0.999 at T-10m, and `efa-eve-sun-2026-01-10` with both legs at 0.001.
- **Calibration.** Isotonic regression of the Over outcome on P_model, fitted on the 441 DEV events and frozen (`calibration_dev.json`).
- **Market.** P_ou is the median Over-converted O/U fill price over [T-30m, D]. The event is skipped if there are fewer than 2 fills.
- **Signal.** dev = P_cal - P_ou. Buy Over if dev >= +0.04 and buy Under if dev <= -0.04.
- **Entry.** The first O/U print acquiring our side with ts in [D+3s, T), at that print's price plus the O/U market's own taker fee
  (`C.taker_roi`, fee_rate 0 / 0.03 in DEV and 0.05 / 0.03 in HOLDOUT). No print means no bet. The rule takes one bet per event and holds it to resolution. Assertions in the code
  check that entry ts >= D+3s, entry ts < T and event ids are unique.
- **Metric.** The mean per-$1 ROI with an equal stake per bet, and a 95% CI from `C.cluster_ci` by event (4,000 bootstraps). The +1c row adds 1c to every entry price.

## Sanity checks

| check | DEV | HOLDOUT |
|---|---|---|
| events / with P_model / with P_ou / both | 448 / 441 / 443 / 438 | 135 / 134 / 133 / 132 |
| Over hit rate vs mean P_ou | 0.545 vs 0.560 | 0.644 vs 0.582 |
| payout orientation: Over hit rate by P_ou bucket (≤.4 / .4-.5 / .5-.6 / >.6) | .43 / .41 / .53 / .69 | .46 / .36 / .64 / .84 |
| corr(P_model, P_ou) | 0.90 | 0.95 |
| median implied total goals (lam_h + lam_a) / raw pD | 2.65 / 0.24 | 2.61 / 0.24 |
| O/U prints per event in 24h (median) / in [T-30m, D] | 258 / 34 | 164 / 23 |
| **age of the last O/U print at D (median)** | **30 s** | **39 s** |
| prints with Over < 5c or > 95c in the last 30 min (in-play leak test) | 0.10%, all in 5 events the integrity filter removes | 0.00% |
| entry lag after D (median) / entry price minus P_ou on our side (mean) | 53 s / +0.56c | 64 s / +0.43c |
| Brier: P_ou / P_cal / P_model | .2326 / .2309 (in-sample) / .2428 | **.2062** / .2257 / .2283 |

The payouts are oriented correctly, because the Over hit rate rises with the O/U price. The entry is a later print on our side, and it costs about half a
spread over the window median. Out of sample, the O/U price is a much better forecast than the calibrated moneyline model (Brier .206 vs .226).

## Primary results

| | bets | ROI after fees [95% CI] | ROI +1c | win rate | avg price | Over share |
|---|---|---|---|---|---|---|
| DEV, isotonic fitted on DEV (in-sample, as pre-registered) | 228 | +10.75% [-3.87, +25.50] | +8.34% [-5.95, +22.70] | .539 | .488 | 39% |
| DEV, 5-fold cross-fitted isotonic (honest) | 244 | **-8.78% [-21.37, +4.89]** | -10.64% | .463 | .502 | 39% |
| DEV, time-forward (fit < 2026-03-01, test Mar-Jun) | 156 | -3.48% [-18.45, +11.85] | -5.33% | .526 | .544 | 51% |
| **HOLDOUT (frozen, evaluated once)** | **67** | **-19.42% [-44.71, +6.99]** | **-21.25% [-45.71, +4.24]** | .388 | .469 | 27% |
| DEV, universe restricted to legs with ≥ $50k traded before D | 201 | +3.83% [-10.98, +18.75] | +1.58% | | | |
| HOLDOUT, same restriction | 58 | -21.71% [-50.59, +9.36] | -23.58% | | | |

The in-sample DEV number is a look-ahead artifact. The isotonic curve has about 10 flat steps fitted to these same outcomes, so inside a step the
"signal" is just P_ou against that step's realized hit rate. Fitting the curve out of fold turns +10.8% into -8.8%.

The `pre_usd` filter includes volume traded in the last 10 minutes. On 55 DEV and 19 HOLDOUT events one leg had less than $50k before D.
Restricting to strictly pre-decision volume changes nothing material.

### Splits (primary)

| split | DEV | HOLDOUT |
|---|---|---|
| buy Over | 90 bets, +13.57% [-4.42, +32.92] | 18 bets, -21.25% [-62.16, +21.88] |
| buy Under | 138 bets, +8.91% [-11.99, +29.05] | 49 bets, -18.75% [-49.95, +14.41] |
| World Cup (`soccer-fifwc`) | 49 bets, +5.77% [-22.16, +34.68] | 20 bets, -13.49% [-54.88, +29.31] |
| club / other | 179 bets, +12.11% [-4.82, +28.07] | 47 bets, -21.95% [-54.08, +10.59] |
| fee 0 (market-level; Dec 2025 - mid-Apr 2026) | 115 bets, +4.69% [-15.09, +25.54] | – |
| fee 0.03 | 113 bets, +16.92% [-3.11, +36.34] | 18 bets, -24.20% [-66.89, +24.04] |
| fee 0.05 | – | 49 bets, -17.66% [-47.89, +14.75] |
| calendar 2025 / 2026 | 23 bets +1.42% / 205 bets +11.80% | 67 bets -19.42% (all 2026) |

*Per-sport split:* the study is soccer only. By league, the HOLDOUT is negative in every league with 4 or more bets: World Cup -13%
(20 bets), EPL -40% (12), La Liga -13% (8), UCL -38% (5), Serie A -57% (5), Liga MX -100% (4). In DEV, EPL (66 bets) and La Liga (34) were
+24% each, UCL -16% (32) and Bundesliga -74% (9). None of these league figures means anything given the CIs.

The primary leaned to Under in the holdout (73% of bets). The frozen isotonic curve maps the moneyline to a lower goal expectation than the O/U market
(mean P_cal .544 vs P_ou .582). The holdout was high-scoring (Over hit 64%), and the O/U price anticipated that better than the moneyline model did.

## Mechanism diagnostics: is the O/U stale relative to the moneyline?

| test | DEV | HOLDOUT |
|---|---|---|
| (c) CLV: bet marked to the O/U median over [T-5m, T), c/share | -0.61c [-0.70, -0.53] | -0.45c [-0.58, -0.31] |
| (c) O/U move from D to close, in the signal's direction (bets) | -0.05c [-0.16, +0.07] | -0.02c [-0.22, +0.18] |
| (c) all events: slope of the (close - P_ou) move on dev = P_cal - P_ou | -0.007 [-0.021, +0.005] (n 424) | -0.001 [-0.022, +0.018] (n 129) |
| (c) all events with \|dev\| ≥ 0.04: move toward signal | -0.05c [-0.17, +0.06] (233) | -0.07c [-0.25, +0.12] (76) |
| same with smooth logistic calibration | slope -0.005 [-0.022, +0.012] | slope -0.012 [-0.039, +0.016] |

A stale O/U would drift toward the moneyline-implied value before kickoff. It does not: the point estimates are ≤ 0, and the CIs rule out
drifts larger than about 0.2c. The negative CLV is
the half-spread paid on entry. The bets win or lose on resolution noise alone, with no price convergence behind them.

**Is the moneyline's information already in the O/U?** Logistic regressions of the Over outcome on logit(P_model) and logit(P_ou) (post-hoc and
descriptive, except for the DEV fit, which is variant b):

| sample | n | coef logit(P_model) (se) | coef logit(P_ou) (se) |
|---|---|---|---|
| DEV | 438 | -0.60 (0.33) | +1.84 (0.46) |
| HOLDOUT | 132 | -0.98 (0.97) | +2.52 (1.10) |
| pooled | 570 | **-0.74 (0.31)** | +2.07 (0.41) |

Once the O/U price is known, the moneyline-implied total adds nothing, and its sign is negative in both periods. The independent-Poisson
inversion of a 3-way price is a noisy estimator of goals. A 1c change in the draw price moves P_model by several cents. From T-120m to T-45m the median absolute
change is 3.2c for P_model against 1.0c for the O/U, and the standard deviations are 4.5-4.8c against 1.1-3.2c. The O/U book is quoted directly on the quantity we are trying to predict.

## Variants (all reported; none drives the verdict)

| variant | DEV bets, ROI [CI] | HOLDOUT bets, ROI [CI] | HOLDOUT +1c |
|---|---|---|---|
| (a) threshold 0.03 | 266, +11.28% [-2.04, +24.99] (in-sample calib.) | 78, -16.38% [-40.46, +8.59] | -18.23% |
| (a) threshold 0.06 | 168, +15.88% [-0.78, +33.35] (in-sample calib.) | 44, -22.18% [-54.27, +11.48] | -24.02% |
| (b) logistic over ~ logit(P_model) + logit(P_ou), signal = fitted - P_ou, thr 0.04 | 153, +10.23% [-5.87, +26.71] (in-sample fit) | 38, **+21.65% [-0.68, +42.15]** | +19.76% |
| diag: smooth 1-variable logistic calibration of P_model (instead of isotonic) | 180, -16.33% [-32.78, +1.89] | 63, -28.83% [-57.06, +2.58] | -30.79% |
| (d) dynamic, T-45m: \|ΔP_cal\| ≥ .04 since T-120m, O/U moved < half as far that way | 94, -6.13% [-27.39, +16.80]; CLV -0.70c | 26, -16.12% [-51.40, +20.11]; CLV -0.12c | -17.64% |
| (d) same with the smooth logistic calibration | 56, -14.04% [-40.13, +13.80]; CLV -0.44c | 14, -33.76% [-81.51, +18.98]; CLV -0.68c | -35.04% |

**(e) Lead-lag** (all events; ΔML = change in P_model, ΔOU = change in the O/U Over median; early = T-120m to T-45m, late = T-45m to T-10m for the moneyline
and T-45m to the [T-5m, T) close for the O/U):

| regression | DEV slope [CI], corr | HOLDOUT slope [CI], corr |
|---|---|---|
| ΔOU_early on ΔML_early (same time) | +0.33 [+0.06, +0.59], +0.50 | +0.06 [+0.02, +0.11], +0.26 |
| ΔOU_late on ΔML_early (moneyline leads O/U?) | +0.04 [-0.01, +0.08], +0.10 | +0.05 [-0.01, +0.11], +0.16 |
| ΔML_late on ΔOU_early (O/U leads moneyline?) | +0.09 [-0.30, +0.43], +0.03 | +0.50 [-0.15, +1.26], +0.12 |
| ΔOU_late on ΔML_late (same time) | +0.06 [+0.02, +0.10], +0.17 | +0.05 [-0.02, +0.10], +0.15 |

The two markets move together within the same window. Any lead of the moneyline over the O/U is at most +0.04 to +0.05 per unit, which is about 0.2c of later O/U
move per 4.5c moneyline-implied move. That is far below the ~1c spread. There is no reverse lead either.

**About variant (b)'s positive holdout.** It is the one positive holdout number, but it is not evidence for this hypothesis. DEV fitted the
moneyline coefficient as *negative* (-0.60), so 71% (DEV) and 75% (HOLDOUT) of (b)'s signals point *against* the raw moneyline-implied value. In practice
(b) is "fade the moneyline-implied total and stretch the O/U price." It has 38 holdout bets, a CI touching 0 and 7 variants tried, so it is a lead
for a new, separately pre-registered hypothesis tested on data after 2026-09-19. It is not a result.

## Capacity

The rule makes about 1.1 (DEV) and 0.8 (HOLDOUT) bets a day. The print we copy is small (median $10-13), but the same-side O/U flow within 1c of our entry
price between D+3s and kickoff has a median of $900 (DEV) and $1,150 (HOLDOUT) per event, with a p25 of about $100-150. That flow is heavy-tailed: World Cup
games reach $0.9M, and the 5 largest events hold about half of all capacity. A realistic deployable size is about $1k per event, or about $1k a day
(roughly $30k a month) at these prices. At the holdout ROI that loses money.

## Caveats

- **Small holdout.** Only 135 HOLDOUT events clear the $50k-per-leg bar, and 67 trade, so the holdout CI is about ±25%. The verdict does not rest on
  the -19% point estimate. It rests on three things together: the honest DEV estimate is ≤ 0, the O/U never converges toward the model (a low-noise test,
  CIs of ±0.1-0.2c), and the moneyline carries no incremental information given the O/U price.
- **The pre-registered DEV figure is in-sample.** The isotonic curve was fitted on all DEV events, as the hypothesis specified. Only the cross-fitted
  and time-forward DEV numbers are honest, and both are negative.
- **Integrity filter.** The 1c/99c team-leg filter was added after seeing DEV. It uses only pre-decision prices and removes events whose
  scheduled start was wrong. Without it, the clip plus the Poisson solve would have produced nonsense P_model values for games already in play.
  More subtle start-time errors, such as a kickoff 5-10 minutes early, cannot be ruled out. They would shorten the window and would not bias toward profit.
- **Model form.** Independent Poisson ignores draw inflation and game-state effects. For example, `fifwc-alg-aut-2026-06-27` had pD = 0.46 in a final group game where a draw
  suited both teams, which maps to P_model 0.08 while the O/U traded at 0.30. The isotonic and logistic calibrations are meant to absorb this.
  A richer model (bivariate or zero-inflated Poisson) might extract more, but it would still have to beat an O/U book that already prices the game directly.
- **Fill model.** We copy the first on-side print after D+3s at its price. With prints about 30 s apart this is realistic for $10-1,000 sizes. The
  cost relative to the window median is about half a cent, and it is included.
- **Multiple testing.** 1 primary rule, 7 variants and about 15 diagnostic splits. No DEV CI (honest or in-sample) excludes 0, and the single holdout-positive variant
  (b) is the mechanism reversed.

## Verdict

**DEAD.** The holdout ROI after actual fees is -19.4% (-21.3% at +1c). The DEV CI lower bound is below 0 even in-sample (-3.9%), and the honest DEV
estimate is -8.8%. The staleness premise fails directly: in these liquid events the O/U 2.5 market prints about every 30 s near kickoff, co-moves with the
moneyline in the same window, and does not drift toward the moneyline-implied total after a gap opens. The O/U price is the better forecaster. This
also argues against the analogous `nba_spread_lag_injury` idea: a linked market with active pregame quoting incorporates news at the same pace as the moneyline.
