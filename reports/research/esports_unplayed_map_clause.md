# Esports game-4 markets: do traders ignore the 50-50 clause?

**Verdict: DEAD. This was PROMISING before the stats review.** The literal pre-registered number is +59.8% per $1 in HOLDOUT, but almost all of it comes from single fills of under $1 at 1-2c, which nobody could have bought $1 of. Once the per-$1 stake has to be fillable at the entry print (the capacity-valid primary), the same rule on the same bets returns **+5.0% in DEV [-12.0%, +23.7%] (104 bets) and +4.0% in HOLDOUT [-12.3%, +20.5%] (94 bets)**. With +1c slippage those fall to +2.2% and +1.4%. Estimators that one longshot cannot dominate put the effect at zero in both periods: equal-share ROI is -3.3% in DEV and +1.1% in HOLDOUT (+1c: -5.5% and -1.2%), and the per-share t-statistic is -0.54 and +0.13. The HOLDOUT sign flips negative at +1c once the top 1% of bets is dropped, and also on the equal-share basis. That fails the verdict gate adopted from the review. The mechanism is also mostly priced in: pregame game-4 favourites trade about 3c above the clause-adjusted fair value, not the 5-8c hypothesised. After the spread and a 5% fee (about 1.2c per share at p = 0.4), nothing tradeable is left.

Code: `pmsports/research/h_esports_unplayed_map_clause.py`. Outputs are in `data/research/h_esports_unplayed_map_clause/`: `results_{dev,holdout}.json`, `results_verdict.json`, `results_pooled_posthoc.json`, `bets_primary_*.parquet` and `run_*.log`. The pre-review versions are kept as `*_prereview.*` and the round-1 dev run as `*_round1.*`. The cached tapes are in `trades/`.

## Review fixes

The rule, its parameters, the sample and the tapes are unchanged. The saved primary bets are identical to the pre-review bets file (checked: same markets, same prices). The fixes below change only how the bets are scored and how the verdict is reached. Every fix is applied identically to DEV and HOLDOUT, and nothing was tuned on the holdout. Re-running the holdout only re-scores the same frozen bets, so the literal holdout number is unchanged at +59.75%.

| # | Reviewer issue | Valid? | What changed | Effect |
|---|---|---|---|---|
| 1 | Both headline means rest on one sub-$1 fill each. `lol-gl-maz-2026-09-01` (0.02, $0.80) supplies 81% of the summed holdout ROI, and `codmw-par-tex-2026-06-07` (0.01, $0.90) supplies 95% of DEV's. | Yes | `robust()` reports the share of the summed ROI that comes from the top 1, 3 and 5 bets. The literal metric is no longer the headline. | Reported below: literal top-1 share is 95% in DEV and 81% in HOLDOUT. |
| 2 | Per-$1 ROI is not valid when $1 could not be bought at the entry print. | Yes | New **capacity-valid primary**: the same rule with a fill-or-kill $1 order. A bet counts only if the entry second's underdog prints carry at least $1 at or below the entry price (`usd_at_entry`, visible at entry). **One small correction to the reviewer's check:** an exact $1 market order prints as $0.999999 (float noise in price times size), so a plain `>= 1` test drops real $1 prints. I use a tolerance of $0.005 (`USD_TOL`). That drops 5 DEV and 2 HOLDOUT bets instead of 10 and 5. The companions are FOK $5 and partial fills with stake = min(entry-print $, $10 or $100). | HOLDOUT goes from +59.8% to +4.0% (+1.4% at +1c) and DEV from +43.5% to +5.0% (+2.2%). With the reviewer's strict `>= 1` test the figures are +3.4% / +0.9% (HOLDOUT) and +3.9% / +1.1% (DEV), the same conclusion. |
| 3 | Robust estimators put the effect at zero. | Yes | `robust()` adds: equal-share ROI (sum of P&L over sum of cost) with its own cluster bootstrap, per-bet ROI capped at +100%, drop-top-1% and drop-top-2%, and the per-share t-statistic. These are reported for the literal bets, the capacity-valid bets, every split and every variant (`cv_brief`). | Capacity-valid equal-share ROI: DEV -3.3%, HOLDOUT +1.1%. t: -0.54 and +0.13. |
| 4 | The stake-capped "realistic" +12.9% is one bet (`codmw-mh-tor-2026-07-17`, +$202 of +$234). | Yes | `stake_weighted()` now reports the top bet by P&L and the ROI without it, for caps of $10 and $100, after fees and at +1c. Dollar amounts now carry the zero-volume stratum weights (DEV $3,338 instead of $3,142; the ROI is unchanged). | HOLDOUT at the $100 cap: +12.9%, but +1.8% without the top bet, and -1.3% at +1c. |
| 5 | The percentile cluster bootstrap is unreliable with a single 46x observation, and "P(ROI <= 0) = 0.05" carries no information. | Yes | Removed the iid-bootstrap P(ROI <= 0). Literal-metric CIs are listed for the record only and are not used as evidence. The equal-share CI works on bounded per-share P&L (between -1 and +1 per share), and the capacity-valid set has no bet above +291%. Its top bet still supplies about 53% of the summed ROI, which is why the gate also requires the equal-share and drop-top-1% estimators. | The quoted "P(ROI <= 0)" is gone from the report. |
| 6 | Results are unstable across months. | Yes | `stability_by_month`: equal-share ROI and capacity-valid ROI by month. | HOLDOUT: July +25%, August +0.5%, September -0.7% (equal-share). DEV: March +8.5%, April -59%, May -3.1%, June +10.5%. |
| 7 | Results are unstable across titles. | Yes | `stability_by_title`, `leave_one_title_out`, `lol_only_drop_top3`. | HOLDOUT without LoL: +3.0% per $1 and +1.4% equal-share (22 bets). LoL alone without its top 3 bets: +1.9% (HOLDOUT), +5.5% (DEV). HoK is negative in both periods. |
| 8 | DEV depends on which print is used for entry. | Yes | `taker_bets` also records the next underdog print second at least 3 s after the entry (`price2`). `entry_first_vs_next_print` compares the two. | DEV: +64.2% at the first print becomes +5.3% at the next print (78 bets). Equal-share goes from +0.4% to -4.2%. On fillable bets both prints give +2% to +8%, and every CI crosses 0. |
| 9 | The profit is in-play, not in the clause mechanism. | Yes | `pregame_entries_robust`, `inplay_entries_robust`, `inplay_entries_price_ge_0.20`, and timing splits with equal-share ROI. | Pregame entries: DEV -1.7% (equal-share -9.3%), HOLDOUT +7.6% (+2.0%). In-play entries at 0.20 or above in HOLDOUT: -3.0%. |
| 10 | The recommended follow-up (pregame, BO5, fair-value-gated) was designed after seeing the holdout, so it must be tested on data after 2026-09-19. | Yes | The recommendation now says so. That applies to every post-hoc pattern in this report, including the entry-below-0.5 subset below. Today is 2026-09-19, so no such data exists yet. | No follow-up claim is made. |
| 11 | The honest label is DEAD or INCONCLUSIVE, not PROMISING. | Yes | `gate()` / `verdict` (below). The verdict is computed in code from the saved results. | **DEAD.** Not INCONCLUSIVE: 94-104 capacity-valid bets per period are enough to show the return is indistinguishable from zero, and the fair-value check measures the mispricing directly (about 3c, roughly the cost of crossing). INCONCLUSIVE means too little data, and that is not the situation here. |
| - | The reviewer's own "decision-time proxy" (buy at 1 - signal price + 1c) | Agreed, not used | It is an artifact of absurd 0.90/0.94 signal prints. | None. |

**Verdict gate** (`gate()`), fixed from the review's wording before looking at which way it would fall. On the HOLDOUT capacity-valid primary, the per-$1 ROI, the drop-top-1% ROI and the equal-share ROI must all be > 0, both after fees and at +1c. PROFITABLE additionally needs the DEV capacity-valid CI lower bound > 0. Failing the gate gives DEAD, or INCONCLUSIVE if there are fewer than 30 capacity-valid holdout bets.

| HOLDOUT check (FOK $1, 94 bets) | Value | Pass |
|---|---|---|
| per-$1 ROI after fees | +4.0% | yes |
| per-$1 ROI at +1c | +1.4% | yes |
| drop top 1% after fees | +1.9% | yes |
| drop top 1% at +1c | **-0.6%** | **no** |
| equal-share ROI after fees | +1.1% | yes |
| equal-share ROI at +1c | **-1.2%** | **no** |
| (DEV capacity-valid CI lower bound, needed for PROFITABLE) | -12.0% | no |

**A caution on multiple testing.** Variant (a) with a 0.55 threshold narrowly passes the holdout gate: +5.6%, equal-share +2.2% at +1c. Its DEV equal-share is -3.7%, however, and choosing it now would mean selecting on the holdout. With 5 variants, 3 estimators and 2 cost levels, one variant passing by a percent or two is what chance predicts.

## Mechanism

A `-game4` child moneyline pays 50-50 if map 4 is never played. The rules text says so: "If Game 4 is not completed for any reason, this market will resolve 50-50." The contract is therefore worth `P_played*p + (1-P_played)*0.5`. With independent maps, a 3-0 sweep (no map 4) happens with probability `1-3p(1-p)`. At a per-map win probability of p = 0.65 the fair price is 0.59, not 0.65. If traders price "who wins map 4" at p, the favourite is overpriced by 4-8c, so the rule buys the underdog.

## Rule as implemented (pre-registered; parameters frozen before the single holdout run)

| Item | Implementation |
|---|---|
| Sample | Every esports `child_moneyline` in `C.universe()` whose slug ends in `-game4`, with `game_start_ts >= 2026-01-01`, in seeded random order, and no volume filter on the rule side. DEV: start before 2026-07-01 (1,275 markets). HOLDOUT: start from 2026-07-01 (841 markets). |
| Window | T = the series' scheduled `game_start_ts`, which equals the child market's. Only fills with ts in [T-24h, T+60 min) are used. |
| Signal | The first fill in the window whose favourite price `max(q, 1-q)` is at least 0.58. The favourite is the side priced at 0.5 or above on that fill, and the underdog is fixed from then on. |
| Entry | The first second in [t0+3 s, T+60 min) with a fill that acquires the underdog. The price is the highest underdog price paid in that second, plus the market's actual taker fee (`fee_rate`: 0 in Jan-Feb, 0.03 in Mar-Jun, 0.05 from July). |
| Payoff | Hold to resolution. The underdog pays 1, 0, or 0.5 if map 4 is not played (payouts from `C.universe()`). |
| Metric (literal, pre-registered) | `C.taker_roi` per $1 with an equal stake per bet. CI from `C.cluster_ci` by series `event_slug`. +1c and +2c slippage. One bet per market and one game4 market per series. |
| **Metric (capacity-valid primary, review fix)** | The same bets, counted only if a fill-or-kill $1 order would have filled: the entry second's underdog prints carry at least $1 (minus $0.005 float tolerance) at or below the entry price. Reported with the robust estimators above. |

**Sample and fetch.** The study used 2,000 Data API tapes, the per-study cap. Every game4 and game5 market with universe volume > 0 was fetched: 1,332 game4 and 138 game5. Universe `volume` turned out not to be a reliable "no fills" flag (a spot check found fills in zero-volume markets). The remaining budget therefore went on zero-volume game4 markets, taken as a random prefix of the same seeded order: 530 of 784, of which 314 are DEV and 216 HOLDOUT. Bets from that stratum carry the inverse inclusion probability as a weight: 1.50 in DEV and 1.45 in HOLDOUT. In DEV, 25 of the 314 zero-volume markets had fills in the rule window. In HOLDOUT none did.

## Results (taker, actual fees)

### Capacity-valid primary (fill-or-kill $1 at the entry print)

| | Bets | ROI per $1 [95% CI] | +1c | Equal-share ROI [CI] | Equal-share +1c | Winsorised (cap +100%) | Drop top 1% (fees / +1c) | t per share (fees / +1c) |
|---|---|---|---|---|---|---|---|---|
| **DEV** | 104 (5 unfillable dropped) | **+5.0%** [-12.0%, +23.7%] | +2.2% [-14.3%, +20.4%] | -3.3% [-19.1%, +14.5%] | -5.5% [-20.9%, +11.8%] | -4.5% [-18.6%, +10.3%] | +2.4% / -0.3% | -0.54 / -0.80 |
| **HOLDOUT** | 94 (2 dropped) | **+4.0%** [-12.3%, +20.5%] | +1.4% [-14.4%, +17.4%] | +1.1% [-15.0%, +17.9%] | -1.2% [-16.9%, +15.2%] | -2.8% [-17.2%, +11.6%] | +1.9% / -0.6% | +0.13 / -0.15 |

FOK $5 (a $5 order must fill at the entry print): DEV +9.6% [-16.2%, +34.2%] (60 bets; +1c +6.8%; equal-share -2.8%). HOLDOUT +0.2% [-19.1%, +21.5%] (60 bets; +1c -2.2%; equal-share -6.1%).

### Literal pre-registered metric (for the record; not capacity-valid)

| | Bets | ROI per $1 | +1c | +2c | Equal-share | Equal-share +1c | Drop top 1% (fees / +1c) | Top-1 bet's share of summed ROI | t per share |
|---|---|---|---|---|---|---|---|---|---|
| DEV | 109 | +43.5% [-10.9%, +138.8%] | +19.8% | +10.2% | -4.0% [-19.9%, +13.8%] | -6.2% | +2.3% / -0.4% | 95% | -0.61 |
| HOLDOUT | 96 | +59.8% [-5.0%, +174.0%] | +39.5% | +28.0% | +4.6% [-11.6%, +23.3%] | +2.2% | +11.3% / +7.5% | 81% | +0.53 |

The literal CIs are unreliable (issue 5) and are not used as evidence. The averages come from `codmw-par-tex-2026-06-07`, bought at 0.01 with $0.90 on the print and map 4 voided (+4,756%), and `lol-gl-maz-2026-09-01`, bought at 0.02 with $0.80 on the print and the underdog won (+4,666%). Both were stale asks in near-empty books. In DEV, the 0.01 print took all the liquidity at or below 0.01, and the next underdog print was at 0.50.

### Partial fills: stake = min(entry-print $, cap)

| | DEV | HOLDOUT |
|---|---|---|
| Cap $10 | +12.9% [-11.5%, +40.7%]; $698 staked, P&L +$90. Without the top bet (`codmw-par-tex`, +$43): +6.7%. At +1c: +7.0%, or +2.2% without the top bet. | +10.7% [-10.2%, +34.9%]; $619 staked, P&L +$67. Without the top bet (`lol-gl-maz`, +$37): +4.7%. At +1c: +6.1%, or +2.1% without the top bet. |
| Cap $100 | +10.9% [-17.1%, +40.0%]; $3,338 staked, P&L +$365. Without the top bet (`lol-wb-al`, +$186): +5.5%. At +1c: +7.5%, or +2.3% without it. | +12.9% [-13.0%, +43.2%]; $1,816 staked, P&L +$234. Without the top bet (`codmw-mh-tor`, +$202, an in-play CoD entry 3 s after a 0.85 favourite print): +1.8%. At +1c: +9.4%, or **-1.3%** without it. |

### Execution robustness: first versus next underdog print (issue 8)

| | DEV | HOLDOUT |
|---|---|---|
| Bets with a next underdog print at least 3 s after entry | 78 of 109 | 73 of 96 |
| First print (the rule): per-$1 / equal-share | +64.2% / +0.4% | +66.0% / +3.1% |
| Next print: per-$1 / equal-share | +5.3% / -4.2% | +64.9% / +4.8% (the 0.02 ask printed twice) |
| First print, fillable | +8.5% / +1.5% (75 bets) | +2.1% / -0.1% (72 bets) |
| Next print, fillable at its own print | +6.3% / -2.1% (76 bets) | +0.7% / +1.0% (66 bets) |

### Stability (issues 6, 7 and 9)

Each cell shows bets, then equal-share ROI, then capacity-valid per-$1 ROI.

| Month | DEV | HOLDOUT |
|---|---|---|
| 2026-03 / 2026-07 | 29: +8.5% / +18.8% | 19: +25.0% / +30.4% |
| 2026-04 / 2026-08 | 11: -59.1% / -33.6% | 39: +0.5% / +3.8% |
| 2026-05 / 2026-09 | 31: -3.1% / -0.7% | 38: -0.7% / -9.7% |
| 2026-06 | 36: +10.5% / +7.7% | |

In DEV, January and February have one bet each; see the caveat on the Jan-Feb listing regime.

The next table shows per-title splits (literal per-$1 / equal-share).

| Title | DEV | HOLDOUT |
|---|---|---|
| League of Legends | 68: +15.7% / +11.7% | 74: +76.6% / +5.5% (fillable +4.3%) |
| Honor of Kings | 10: -62.2% / -54.7% | 2: -57.8% / -57.8% |
| Call of Duty | 6: +726% / -48.2% (one 1c ticket) | 8: +1.4% / +4.2% |
| Counter-Strike | 8: +16.0% / +10.0% | 4: -15.2% / -16.6% |
| Valorant | 6: -24.0% / -23.8% | 4: +60.7% / +61.4% |
| Dota 2 | 3: +88.7% / +54.4% | 4: -2.9% / -9.0% |
| MLBB / Rocket League / SC2 | 4 / 3 / 1: +80.6% / -100% / -100% | none |
| Without LoL | 41: +92.0% (CoD ticket) / -26.8% | 22: +3.0% / +1.4% |
| LoL alone, without its top 3 bets | +5.5% | +1.9% |

The next table splits by entry timing relative to the scheduled start T (bets: equal-share / capacity-valid per-$1).

| Entry timing | DEV | HOLDOUT |
|---|---|---|
| before T-1h | 66: -11.9% / -0.1% | 60: -2.1% / +3.7% |
| T-1h to T | 14: +8.5% / +3.6% | 11: +25.6% / +29.0% |
| T to T+30 min | 13: +61.1% / +71.6% | 11: +0.8% / -11.6% |
| T+30 to T+60 min | 16: -24.3% / -35.1% | 14: +24.5% / -3.8% |
| All pregame entries (per-$1 / equal-share / t) | 80: -1.7% / -9.3% / -1.04 | 71: +7.6% / +2.0% / +0.23 |
| All in-play entries (per-$1 / equal-share / winsorised) | 29: +172% / +13.0% / +0.2% | 25: +208% / +13.8% / -8.4% |
| In-play entries priced at 0.20 or above (per-$1 / equal-share) | 27: +18.2% / +8.8% | 22: -3.0% / -0.9% |

None of these splits is stable across both periods. The most negative cells (DEV April, HoK, DEV before T-1h) and the most positive ones (July, the T-1h..T bucket) are single-digit to 30-bet groups.

## Variants (all that were tried)

Literal per-$1, then capacity-valid (FOK $1) per-$1 with its CI, then equal-share ROI after fees / at +1c.

| Variant | DEV | HOLDOUT |
|---|---|---|
| (a) Favourite threshold 0.55 | +42.1% (111); FOK +4.2% [-12.9%, +22.3%] (106); equal-share -3.7% / -5.9% | +58.6% (101); FOK +5.6% [-10.5%, +22.2%] (99); equal-share +4.6% / +2.2% |
| (a) Favourite threshold 0.65 | +56.7% (82); FOK +6.3% [-13.4%, +27.5%] (78); equal-share -4.6% / -6.7% | +85.6% (64); FOK +3.5% [-19.3%, +25.0%] (61); equal-share -1.4% / -3.7% |
| (b) Pregame only (ts < T) | -1.7% (80); FOK +0.5% [-17.9%, +20.2%] (77); equal-share -7.3% / -9.4% | +7.6% (71); FOK +7.6% [-9.2%, +25.3%] (71); equal-share +2.0% / -0.2% |
| (b) Pregame, BO5 only | +5.7% (74); FOK +8.4% [-10.7%, +27.9%] (71); equal-share +3.2% / +0.7% | identical to (b); there are no BO7 pregame bets in HOLDOUT |
| (d) Maker: bid at the last underdog print, phi = 0.5 | +9.3% [-16.3%, +33.8%] (30 fills of 66 candidates; 2,032 shares); weighted by filled $: +0.3% [-86%, +52%] | +6.9% [-25.8%, +43.2%] (24 of 42; 680 shares); weighted by filled $: +10.5% [-75%, +76%] |
| (d) Maker, 1c trade-through required | +8.2% [-17.8%, +34.3%] (27) | +5.8% [-29.0%, +42.4%] (23) |
| (e) Game-5 markets, cutoff T+90 min (volume > 0 only) | -86.2% (8); FOK -77.8% (5) | +34.8% (4; no CI) |

(c) Calibration: equal-weight mean(payoff - price) over every pre-cutoff fill, with stratum weights and a 95% CI clustered by series.

| Fills | DEV (all pre-cutoff) | HOLDOUT (all pre-cutoff) | DEV pregame | HOLDOUT pregame |
|---|---|---|---|---|
| Favourite side (q >= 0.5) | -15.5% [-32.3, -2.0]; $-weighted -10.2% [-15.6, -2.9] | -11.6% [-19.7, -4.1]; $-weighted -4.6% [-19.8, +9.6] | -22.3% [-44.2, -1.4] | -10.5% [-18.2, -2.9] |
| Underdog side (q < 0.5) | +5.4% [-1.6, +11.4]; $-weighted +10.1% [+1.8, +18.5] | +7.6% [-1.4, +15.8]; $-weighted +6.5% [-6.3, +17.5] | +2.9% [-6.1, +10.8] | +8.1% [-0.4, +15.5] |

Takers buying the favourite lose on an equal-weight basis in both periods. Takers buying the underdog, the only side a taker strategy can trade, gain 3-8c, and those CIs mostly cross 0. On a $-weighted basis the HOLDOUT favourite loss is not significant either. The gap between the two sides is the spread plus a few absurd prints, such as both sides filled at 0.90 in newly listed books.

## Is the clause really neglected? (fair-value check, BO5 only)

For each BO5 game4 market, I took the median pregame game4 price over [T-2h, T). I compared it with two values implied by the series moneyline's pregame mid, inverted through the iid best-of-5 formula: the naive map-win probability p and the clause-adjusted fair value. The series moneylines come from `C.markets()`, which covers only series with at least $50k volume, so this is a diagnostic and not a rule sample.

| | n | Game4 favourite price | Naive p | Clause-adjusted fair value | Realised favourite payoff | Share of markets closer to naive |
|---|---|---|---|---|---|---|
| DEV | 64 | 0.620 | 0.651 | 0.593 | 0.563 | 30% |
| HOLDOUT | 54 | 0.610 | 0.634 | 0.581 | 0.565 | 24% |

The market does most of the clause arithmetic. Prices sit 2.7c (DEV) and 2.9c (HOLDOUT) above fair value, while a fully naive price would sit 5.8c and 5.3c above. That leaves roughly 3c of favourite overpricing, or about 3c of underdog underpricing on a 0.40 contract. The 5% fee costs 1.2c per share at p = 0.4, and crossing costs about another 0.5-1c. The remaining 1c or so per share (about 2-3% ROI) is what the capacity-valid estimates show, and it cannot be told apart from zero with about 100 bets per period (per-share P&L SD about 0.37, so the standard error is about 3.7c per share).

## Capacity

The median entry print was $7 (mean $73 in DEV, $25 in HOLDOUT). Summed over bets that is $7.9k across the 6 DEV months and $2.4k across the 2.6 HOLDOUT months, or about $1k per month at the prices the rule actually got. As an upper bound, counting every later underdog fill at or below the entry price before the cutoff gives $35k (DEV) and $30k (HOLDOUT), about $6-11k per month, much of it in-play flow carrying fresh map information. The maker variant would have filled about 2,000 shares in DEV and 700 in HOLDOUT. Even if the roughly 1c residual were real, it would earn tens of dollars a month.

## Post-hoc observations (not rules; untestable until data after 2026-09-19 exists)

- **Genuine-underdog entries.** In 13 DEV and 14 HOLDOUT bets the rule bought an "underdog" above 0.5 because of absurd prints in new books (both sides at 0.90). This was documented in DEV but not removed before the holdout run. Restricting the capacity-valid bets to entries below 0.5 gives a consistent sign: DEV +13.6% [-5.1%, +33.5%], equal-share +11.5% (+8.6% at +1c), t = 1.12 (90 bets). HOLDOUT gives +9.3% [-9.3%, +28.5%], equal-share +10.7% (+7.9% at +1c), t = 1.15 (80 bets). Pooled over 0.20-0.50 entries: +13.6% [-0.0%, +27.5%], equal-share +11.6% [-1.3%, +25.0%], t = 1.67 (167 bets). The filter was chosen after the holdout was seen, neither period is significant on its own, and it is larger than the roughly 3c fair-value gap would predict. It is a candidate for a pre-registered forward test on data after 2026-09-19, not a finding.
- The pooled capacity-valid primary (DEV + HOLDOUT, 198 bets) is +4.5% [-8.0%, +17.2%], with equal-share -1.3% and t = -0.32.

## Caveats and checks

- **The premise that playability is unknown until T+60 min is false for fast titles.** Rocket League, CoD and SC2 series, and some MLBB and HoK series, finish three maps within an hour. Tapes show game4 prints at 0.98-0.999 at T+51-57 min. The rule stays causal, since every entry is a later print, but part of the primary result is an in-play strategy (see issue 9).
- **Jan-Feb 2026 listing regime.** Game4 markets were then created mid-series, mostly only when map 4 was going to be played: 9 of 172 February series were sweeps, against about 40% later. This affects 2 DEV bets.
- **BO7 series are in the population** (about 10%, identified by their game5/game6 markets). The clause is irrelevant there. They are kept as pre-registered. On capacity-valid bets, BO5 alone gives DEV +9.8% (97 bets) and HOLDOUT +1.8% (91).
- **Absurd prints.** 13 DEV and 14 HOLDOUT bets entered above 0.5, at -49% and -27%. They are kept as pre-registered (see the post-hoc note).
- **Payout orientation verified.** Fills are mapped by token, with 0.00% mismatches. Payouts sum to 1 in every market. 693 of 700 voids are 3-0 sweeps, and 79 of the 86 non-void sweeps are BO7 series.
- **Fee and execution.** Each market's own `fee_rate` is used via `C.taker_roi`. The entry is the first underdog print at least 3 s after the signal print, at the highest price within that second (the lowest price gives the same result).
- **Bugs found and fixed.** Before the holdout: (1) `b.T` in pandas is the transpose, which crashed the first run; (2) the zero-volume fetch shortcut was a latent selection on total volume, fixed by the round-2 fetch and weights. In the review round: (3) the per-$1 metric counted unfillable sub-$1 prints; (4) the fillability test needed a half-cent float tolerance.
- **Multiple testing.** 5 rule variants, about 15 splits and several estimators were reported. Nothing was tuned on the holdout.
- `ts` is on-chain time, about 2.6 s after the match. T is the scheduled start, and esports often start late.

## Bottom line

Thorp's instinct was right that the clause changes the contract's value. The market already prices most of it, though: game4 favourites trade about 3c above the clause-adjusted fair value, not 6c. Priced so that the stake could actually have been bought, the pre-registered rule returns +5% (DEV) and +4% (HOLDOUT) per $1, with CIs about ±17%, equal-share ROI of -3% and +1%, and per-share t-statistics of -0.5 and +0.1. The sign does not survive 1c of slippage together with any trimming. Capacity is about $1k per month even at the prices that printed. **DEAD: do not trade it.** Any follow-up, whether pregame-only BO5 gated on series-implied fair value or genuine-underdog entries only, was designed after the Jul-Sep holdout had been seen. It must be pre-registered and tested only on markets starting after 2026-09-19.
