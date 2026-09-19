# MLB run line: is home -1.5 structurally rich? (walk-off and no-bottom-9th truncation)

**Verdict: DEAD.** The rule effect is real, but Polymarket's run-line market already prices it.
The effect itself is confirmed: at the same moneyline price, home favourites cover -1.5 far less
often than away favourites (40.8% vs 53.4% at 0.60-0.65). The hypothesis needed run-line makers to
convert moneyline to run line with a symmetric margin model. They do not. Across 1,281 markets
the market's own home discount is -0.22 logit (SE 0.005). The true discount is -0.20. So the
market prices the asymmetry fully, even slightly over.

| Pre-registered primary | Bets (games) | ROI after fees | 95% CI (clustered by game) | +1c slippage |
|---|---|---|---|---|
| DEV (Mar-Jun 2026, fee 3%) | 76 | **-1.7%** | [-22.8%, +20.5%] | -3.5% |
| HOLDOUT (Jul-Sep 2026, fee 5%/3%) | 52 | **-8.8%** | [-35.3%, +17.8%] | -10.6% |

The holdout ROI is below 0 and the DEV CI includes 0, so the verdict is DEAD. The naive
structural bet (NO on home -1.5, YES on away -1.5 at the next print) loses about the cost of
trading in both periods: -2.1% in DEV and -2.5% in the holdout.

Code: `pmsports/research/h_mlb_runline_home_trunc.py`. Cache and results are in
`data/research/h_mlb_runline_home_trunc/`:
- `results.json` and `results_dev.json`
- `features.parquet` (one row per run-line market)
- `bets_primary_{DEV,HOLDOUT}.parquet`, `bets_walkoff.parquet` and `walkoff_states.parquet`
- `tape_audit.parquet`
- `tapes_v2/` (1,999 run-line tapes)

## Mechanism and why it fails

The rules are exactly as described. A leading home team never bats in the bottom of the 9th, and
a walk-off ends the game when the winning run scores (except on a home run). Both cap home margins.
In the development training set (3,627 games from 2025-04 to 2026-06, pregame moneyline pre_p, MLB
finals), cover rates for the team laying 1.5 are:

| Moneyline price of the team laying 1.5 | Away: n | Away: cover | Home: n | Home: cover |
|---|---|---|---|---|
| 0.35-0.45 | 1,240 | 33.0% | 578 | 27.3% |
| 0.45-0.50 | 852 | 34.7% | 682 | 33.4% |
| 0.50-0.55 | 594 | 39.9% | 800 | 36.8% |
| 0.55-0.60 | 396 | 38.6% | 770 | 36.1% |
| 0.60-0.65 | 148 | 53.4% | 404 | 40.8% |
| 0.65-0.70 | 47 | 57.4% | 205 | 47.8% |

The FV model is a logistic regression of cover on logit(p), home and logit(p)×home, fit on DEV
and frozen. Its coefficients are [-0.503, 0.789, **-0.207** (SE 0.053), 0.049]. That is about
4-5 points lower P(cover) for the home team at the same price. For example, at p = 0.60 the model
gives home 0.409, away 0.454, and 0.423 from a symmetric model.

**Diagnostic (variant c): does the market price the asymmetry?** P_yes is the median Yes-converted
run-line fill price in [D-120 min, D]. I regressed logit(P_yes) on logit(FV_sym), where FV_sym is
the symmetric model, plus a home dummy. The same regression of logit(FV) gives the true
discount.

| | Home coefficient in the market | Home coefficient in the true FV | Market P_yes - FV (home) | Market P_yes - FV (away) | Realized cover - P_yes (home / away) |
|---|---|---|---|---|---|
| DEV (710 markets) | **-0.220** (SE 0.005) | -0.203 | +0.85c [0.71, 0.99] | +0.60c [0.38, 0.84] | +0.6c / +0.6c (CIs about ±5c) |
| HOLDOUT (571 markets) | **-0.222** (SE 0.005) | -0.200 | +0.80c [0.63, 0.97] | +0.57c [0.32, 0.80] | +0.1c / +2.8c (CIs about ±5c) |

Against the symmetric model, the market is 1.0c *below* it on home -1.5 and 2.9c *above* it on
away -1.5. That is exactly the asymmetry, already priced. The residual gap between market and
true FV is a uniform ~0.6-0.9c premium on the -1.5 side, on both home and away markets. It is
smaller than the half-spread plus fee that a taker pays, and realized cover rates match P_yes
within noise. The table below shows the same result binned by price.

| p_team | Side | n (HOLDOUT) | P_yes | FV (asymmetric) | FV_sym | Realized cover |
|---|---|---|---|---|---|---|
| 0.55-0.60 | away | 38 | 0.447 | 0.430 | 0.401 | 0.500 |
| 0.55-0.60 | home | 94 | 0.392 | 0.385 | 0.403 | 0.447 |
| 0.60-0.65 | away | 37 | 0.503 | 0.473 | 0.441 | 0.487 |
| 0.60-0.65 | home | 42 | 0.438 | 0.428 | 0.440 | 0.476 |
| >0.65 | home | 43 | 0.515 | 0.488 | 0.492 | 0.465 |

## Primary rule as implemented

- **FV model.** A logistic regression as above, fit on `data/mlb/pregame.parquet` pre_p (the
  home moneyline price at first pitch) and `mlb_games.parquet` finals. It uses every game with
  first pitch + 5 h < 2026-07-01, so outcomes are also before the boundary, and pre_p in [0.03,
  0.97]. That is 3,627 games and 7,260 rows (home -1.5 and away -1.5 per game). Frozen.
- **Markets.** 2026 MLB `...-spread-home-1pt5` and `...-spread-away-1pt5` markets from
  `C.universe()`, joined to the moneyline market by event_slug and to game_pk and actual first
  pitch (`games.parquet`, `pregame.parquet`). Outcome 0 was verified to be the team laying 1.5 for
  all 4,344 2026 markets: the name matches, and the payout equals the MLB-computed cover with 0
  mismatches and 0 voids.
- **Eligibility.** All filters use pre-decision information only:
  - moneyline pre_usd >= $25k;
  - one event per game_pk, which drops 2 mis-mapped doubleheader or reschedule pairs;
  - first pitch within 1 h of the scheduled start. A later start is a rain delay that is already
    visible at D, and an earlier one is a mis-mapped game.

  That leaves 890 DEV and 657 HOLDOUT games. To stay within the Data API budget of 2,000 markets,
  I drew a **seeded random sample of games** (seed 20260701) up to 1,000 markets per period: 529
  DEV games (1,000 markets) and 510 HOLDOUT games (999 markets). Tapes are
  `pmsports.polymarket.trades(cid, game_start-24h, closed_ts)` at 5 req/s.
- **Decision.** D = actual first pitch - 30 min.
  - p_team is the median oriented moneyline fill price in [D-10 min, D] from local `C.fills`.
  - FV_yes = model(p_team, home).
  - P_yes is the median Yes-converted run-line fill price in [D-120 min, D], and needs at least 2
    fills.
  - Coverage: 710 of 1,000 DEV markets and 571 of 999 HOLDOUT markets have both prices.
- **Signal.** need = 0.02 + fee_rate·P_yes·(1-P_yes). If P_yes - FV >= need, buy NO (the +1.5
  side). If it is <= -need, buy YES. If both of a game's markets signal, only the larger |edge| is
  taken, so there is one bet per game.
- **Entry.** The first run-line fill on our side (a taker acquiring that token) with ts in
  [D+3 s, first pitch), at its price plus the taker fee at the market's own fee_rate. Entries are
  a median 7.3 min (DEV) and 9.8 min (HOLDOUT) after D. There is no bet without a print. The position is held to resolution
  and paid by the market payout.
- **Metric.** `C.taker_roi`, with `C.cluster_ci` clustered by game_pk (one bet per game), plus
  +1c slippage.

## All results

These are ROI per $1 after actual taker fees, with 95% CIs clustered by game. "+1c" adds 1c to
the entry price.

| Variant | Period | Bets | ROI | 95% CI | +1c | Win rate | Avg price |
|---|---|---|---|---|---|---|---|
| **Primary** (thr 0.02 + fee) | DEV | 76 | -1.7% | [-22.8, +20.5] | -3.5% | 0.526 | 0.535 |
| **Primary** | HOLDOUT | 52 | **-8.8%** | [-35.3, +17.8] | -10.6% | 0.462 | 0.485 |
| Primary, walk-forward FV (fit on 2025 only) | DEV | 96 | +8.3% | [-10.5, +27.4] | +6.3% | 0.594 | 0.544 |
| Primary, D anchored on the scheduled start | DEV | 74 | -3.6% | [-24.6, +19.3] | - | 0.514 | 0.532 |
| (b) threshold 0.01 | DEV | 169 | +3.4% | [-12.0, +18.1] | +1.4% | 0.556 | 0.535 |
| (b) threshold 0.01 | HOLDOUT | 98 | -20.1% | [-39.1, -1.5] | -21.6% | 0.429 | 0.495 |
| (b) threshold 0.04 | DEV | 9 | +49.5% | [-12.8, +94.3] | +46.7% | 0.778 | 0.487 |
| (b) threshold 0.04 | HOLDOUT | 9 | -30.1% | [-78.9, +43.1] | -31.5% | 0.333 | 0.474 |
| (a) naive: NO home -1.5 and YES away -1.5 (2 bets/game, clustered) | DEV | 606 | -2.1% | [-11.4, +7.2] | -4.1% | 0.507 | 0.514 |
| (a) naive | HOLDOUT | 525 | -2.5% | [-11.5, +7.2] | -4.4% | 0.510 | 0.512 |
| (a) naive, NO on home -1.5 only | DEV | 329 | -2.1% | [-10.8, +6.2] | -3.6% | 0.611 | 0.619 |
| (a) naive, NO on home -1.5 only | HOLDOUT | 279 | -3.1% | [-12.9, +6.8] | -4.6% | 0.602 | 0.614 |
| (a) naive, YES on away -1.5 only | DEV | 277 | -2.1% | [-16.3, +13.1] | -4.7% | 0.383 | 0.390 |
| (a) naive, YES on away -1.5 only | HOLDOUT | 246 | -1.8% | [-17.4, +14.1] | -4.2% | 0.407 | 0.396 |
| (d) in-play walk-off (buy away +1.5) | DEV | 4 | -16.1% | n/a | -17.1% | 0.750 | 0.901 |
| (d) in-play walk-off | HOLDOUT | 2 | +13.0% | n/a | +11.8% | 1.000 | 0.881 |

Splits of the primary rule:
- **By market and direction.** Almost every signal is NO: the market's -1.5 side trades about
  0.7c over the model, so the +2c gate fires mostly on that side.

  | Split | DEV | HOLDOUT |
  |---|---|---|
  | NO on home -1.5 | 30 bets, -11.7% | 22 bets, +34.8% [-4.7, +71.9] |
  | NO on away -1.5 | 43 bets, +2.3% | 27 bets, -34.2% [-69.4, +3.5] |
  | YES bets | 3 | 3 |

  The signs flip between periods, which is what noise looks like.
- **By month.**
  - DEV: Apr +15.8% (14 bets), May -28.7% (28), Jun +11.6% (33).
  - HOLDOUT: Jul -4.2% (31), Aug +23.1% (11), Sep -58.1% (10).
- **By sport.** MLB only; the hypothesis is specific to baseball rules.

**Multiple testing.** I tried 5 pregame rule variants per period: primary, walk-forward,
scheduled anchor, and the two thresholds. The only CI that excludes 0 is threshold 0.01 in the
holdout, and it is *negative*. None is positive in both periods beyond noise.

### Variant d: the in-play walk-off state

The baseline FV is P(home by >= 2 | start of the bottom half-inning, 9th or later) from 2021-2025
states:

| State at the start of the bottom half | Baseline P(home by >= 2) |
|---|---|
| 9th, tied | 8.5% (n = 1,157) |
| 9th, down 1 | 1.4% (n = 1,144) |
| Extra innings, tied | 9.3% (n = 666) |
| Extra innings, down 1 | 5.7% (n = 439) |

There were 132 such states in DEV and 142 in HOLDOUT. The home -1.5 market printed in the
~3-minute window [state+30 s, next contact) in only 50 and 54 of them. In those windows the median
home -1.5 price was 7.5c and 8.2c, against FV of 6.4c and 7.3c. That is only **+1.1c [0.3, 2.1]
and +0.8c [0.1, 1.6] rich**. Realized cover was 8.0% and 3.7%.

A 1c overpricing on a ~92c NO leg is smaller than the half-spread plus the 5% fee. The
pre-registered +5c trigger fired 4 and 2 times. This variant is INCONCLUSIVE on sample size, but
the pricing diagnostic says there is little to capture.

## Capacity

- The primary signals on about 0.8 (DEV) and 0.7 (HOLDOUT) games per day within the sample. The
  sample holds 59% and 78% of the eligible games, so that is about 0.9-1.4 bets per day across
  the league.
- The run-line $ printed on our side within 1c of the entry print, inside the 30-minute entry
  window, has a median of $199 per bet in DEV (IQR $46-759) and $313 in the holdout (IQR
  $89-1,000).
- Realistic deployable capital is therefore a few hundred dollars per day. At a zero or negative
  edge that is irrelevant.
- The naive variant can deploy about 6 bets per day at a median of about $100 per bet.

## Bugs found and fixed before the holdout run

1. **The Data API `outcomeIndex` is unreliable on 2026-05-13/14 (fixed).** The first build
   oriented run-line fills by `outcomeIndex`, and those tapes were fetched without the `asset`
   column. A check of final prints against payouts found 13 markets from those two dates whose
   last prints sat at 1 - payout. For example, `mlb-col-pit-2026-05-14-spread-away-1pt5` showed
   P_yes = 0.72 for a +145 underdog's -1.5, which looks like a 43c "edge".

   I re-fetched the same 1,999 markets with `asset`. The per-trade `outcomeIndex` disagrees with
   the traded token in 57-96% of fills in 13 markets, and in about 20% of fills in 4 more, all on
   those two dates. The `outcome` name field is always right. Orientation now comes from the
   token id (`asset` matched to the universe t0/t1). After the fix, all 1,999 tapes resolve
   consistently except 19 thin markets whose last print was mid-game. The fix moved the DEV
   primary from +3.1% (80 bets) to -1.7% (76 bets).

   **This matters for the rest of the repo.** `C.fills` and `data/mlb/trades` also orient by
   `outcomeIndex`. For moneylines the damage is small: 5 games on 2026-05-14 have 0.2-4.8% of
   fills flipped, and p_team here agrees with `pregame.pre_p` within 3c for every sampled game.
   Still, any study of 2026 secondary markets from the Data API should orient by `asset`.
2. **Mis-mapped games.** `games.parquet` maps two different events onto one game_pk twice (TOR-CWS
   04-02/04-03 and the CHC-CLE doubleheader), and a few games have a first pitch 3-5 h before the
   scheduled start. These are excluded before sampling (see Eligibility).
3. **Checks that passed:**
   - one bet per game (no duplicate game_pk);
   - every entry at or after D+3 s and before first pitch;
   - `won` equals the cover recomputed from MLB finals for every bet;
   - ROI recomputed by hand from price, fee_rate and payout matches exactly;
   - the fee is the market's own fee_rate (DEV 0.03; HOLDOUT 29 bets at 0.05 and 23 at 0.03,
     from early-July markets created before the fee switch).

## Caveats

- **Small primary sample.** Only 76 and 52 bets, because run-line markets are thin pregame. The
  median is 5 fills in the 2-hour window in DEV and 2 in the holdout, and 28% (DEV) and 42%
  (HOLDOUT) of markets have fewer than 2. The CIs are
  ±20-27%. The verdict rests mainly on the mechanism diagnostic, which covers 1,281 markets and is
  precise (SE 0.005 on the home coefficient). It shows no structural mispricing to harvest.
- **FV is in-sample for DEV.** The primary FV model includes the DEV games, as the rule specifies.
  The walk-forward version (fit on 2025 only) gives DEV +8.3% [-10.5, +27.4]. That is not
  significant. Its FV is on average 0.4c lower, which lets more NO signals through the gate; the
  home term is nearly identical (-0.200 vs -0.207).
- **P_yes is a fill median over 2 hours.** It mixes bid-side and ask-side prints and can be stale
  against the moneyline at D. That adds noise signals, but it cannot hide a 4-5c structural
  mispricing, which would show up in the diagnostic.
- **Random sample.** The 59%/78% sample of eligible games is random and seeded, and selection uses
  only pre-decision information: moneyline pre_usd, the listing, the schedule, and whether a rain
  delay was already visible at D. pre_usd includes the last 30 min before the scheduled start,
  after D; that is pregame and not outcome-related.
- **Rain delays.** D uses the actual first pitch. Games whose first pitch was more than 1 h
  off-schedule are excluded. Anchoring on the scheduled start instead gives -3.6%.
- **Variant d.** It is tested only on games in the sample, and plays timestamps are Statcast while
  fills are on-chain (+~2.6 s).

## Reproduce

```
systemd-run --user --scope -q -p MemoryMax=4G -p MemorySwapMax=0 \
    .venv/bin/python -m pmsports.research.h_mlb_runline_home_trunc --fetch     # tapes (cached)
systemd-run --user --scope -q -p MemoryMax=4G -p MemorySwapMax=0 \
    .venv/bin/python -m pmsports.research.h_mlb_runline_home_trunc --dev-only  # development
systemd-run --user --scope -q -p MemoryMax=4G -p MemorySwapMax=0 \
    .venv/bin/python -m pmsports.research.h_mlb_runline_home_trunc             # + single holdout
```

These need `PYTHONPATH` set to the repo root when run outside the package directory. The old
`tapes/` directory (v1, without `asset`) is superseded by `tapes_v2/` and is not read.
