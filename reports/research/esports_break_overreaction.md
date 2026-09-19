# Esports BO3 between-map break overreaction in the series moneyline

**Verdict (round 2, after review fixes): PROMISING, but the result is dominated by noise.** Round 1 said DEAD. That
verdict rested on one post-decision selection error, fixed here (see [Review fixes](#review-fixes-round-2)).

The pre-registered rule, with the two review fixes and the calibration refitted on DEV only, returns
**+29.3% per $1 after fees in the holdout, with a CI of [-27.0%, +92.2%] on 53 bets**, and +22.8% at +1c. It is not
PROFITABLE, because the honest DEV CI crosses zero:
- The in-sample DEV CI (+65.2% [+10.5, +128.4] on 89 bets) is inflated, because the calibration was fitted on the
  same series.
- With a 5-fold cross-fitted calibration, DEV is +5.8% [-38.7, +58.0].
- Over 20 different fold draws, the cross-fitted DEV ROI has a median of +28.6%. Its CI lower bound is above zero in
  only 4 of the 20.

**Why "noise-dominated" and not "an edge":**
- **The size of the holdout ROI depends on which calibration is frozen.** The round-1 calibration, with the same
  execution fix, gives +3.5% [-44.6, +61.3] on 60 bets, and -1.5% at +1c. Most of the difference is 9 marginal Dota 2
  bets. Their gap was just above 0.04 under the round-1 fit and just below it under the refit, and all 9 lost. The
  refit also adds 2 Valorant bets, one of which won. On the 50 bets common to both, ROI is +10.5%.
- **Three series carry the holdout.** Dropping the 3 best series turns it to -5.8%; dropping 5 gives -24.8%.
- **The bets are ~20c longshots.** 53 such bets give a CI half-width of about 60 percentage points. A plausible true
  edge (Dota 2 map-1 winners about 5c too expensive, which is ~20% ROI on a 20c trailer) cannot be confirmed or
  rejected at this sample size.
- **The pre-registered mechanism (retail momentum after map 1) is wrong in CS2 and LoL.** There the map-1 winner is
  *cheap* at the break. The rule trades almost only Dota 2 (and some Valorant), through the calibration's title
  dummies.

The **mirror** (buy the leader when the break price is at least 4c *below* the calibrated value) returns +6.4%
[-3.8, +16.5] on 230 holdout bets, and +4.7% at +1c. Pooled with the cross-fitted DEV it is +4.8% [-1.4, +11.0]
on 609 bets. It is the steadier lead, but it too needs a fresh, pre-registered test.

Code: `pmsports/research/h_esports_break_overreaction.py`, which runs end to end: `fetch`, then `analyze --rebuild`
(DEV; refits and freezes the calibration), then `analyze --holdout`, then `extras`.
Outputs are in `data/research/h_esports_break_overreaction/`:
- `calib.json` is the frozen round-2 calibration. The round-1 files are kept as `*_v1.*`.
- `round2_precommit.txt` records the headline choice and the verdict rule. It was written before the round-2 holdout
  run.
- `results_{dev,holdout,extras}.json`, `bets_{dev,holdout}.parquet` and `events.parquet` hold the results.

## Review fixes (round 2)

The reviewer raised one look-ahead / selection issue with several parts, plus some checks. Each part is below, with
what changed.

| # | Reviewer issue | Valid? | What changed | Effect |
|---|---|---|---|---|
| 1 | **A signal was dropped because nobody printed on our side after the decision.** The entry rule "first fill acquiring the trailer in [t_end+153, t_end+600] s" drops a signal when no *other* taker buys the trailer inside that window. That is selection on post-decision activity. One of 60 holdout primary signals was dropped: `dota2-re-lgd-2026-08-03`, a trailer that won at 13 to 14c. The book was two-sided throughout, and the next trailer print came at t_end+636 s. | **Yes.** The protocol's execution standard is "next print on your side *or a quoted ask*". The 600 s cap was a flaw in the pre-registered execution rule, and the code followed it faithfully. | `entry()`: if no fill acquires our side in the window, the bet is filled at a **quote proxy** built only from pre-decision prints: 1 − the last opposite-side print in [t_end+30, t_end+150] + 1c. That is the implied bid for our side plus a 1c spread. If every print in that window acquired our side, it uses the last of them + 1c. The same fix applies to every rule and to both legs of variant (e).<br>Two sensitivities are also reported for the primary and the mirror: (i) the next print on our side with no time cap, and (ii) every bet at the quote proxy.<br>I made the quote proxy the fallback, rather than the next print with no cap: a much later print can fall inside map 2 and carry outcome-correlated information. For this signal the two are identical (0.14). | Holdout signals without a window print: primary 1 (the reviewer's case, filled at 0.14), thr 0.03 1, thr 0.06 1, placebo 1, mirror 0, baselines 4 each. DEV: 0 primary, 0 mirror, 1 placebo, and 1 to 2 in the baselines.<br>With the round-1 calibration, the holdout primary goes from **−6.3% on 59 bets to +3.5% [−44.6, +61.3] on 60**, and −1.5% at +1c. This reproduces the reviewer's +3.52% exactly. |
| 2 | **The report did not disclose the drop.** It said every primary bet had an entry in [153, 600] s, without saying that 1 of 60 signals got no bet, or that the skipped signal was a winner. | **Yes.** | `bets()` now asserts that every signal becomes a bet under the executed entry. Every rule in the results JSON reports `signals`, `bets`, `entry_how` (window or quote) and the list of quote-fallback bets with their outcomes. | All tables below show signals = bets. Quote-fallback fills are counted in the text. |
| 3 | **The verdict should not be DEAD.** With the dropped signal filled, the holdout is positive; the DEV CI clears zero in-sample but not cross-fitted. So the verdict is PROMISING or INCONCLUSIVE and dominated by noise, not DEAD. | **Yes.** | The verdict rule was fixed and written down before the round-2 holdout run (`round2_precommit.txt`): the cross-fitted DEV CI is the honest DEV CI, which rules out PROFITABLE. A holdout above 0 is PROMISING; at or below 0 it is DEAD. | **PROMISING.** It is PROMISING under both calibrations: +29.3% (round 2) and +3.5% (round 1) are both above 0, and both DEV CIs cross 0 when cross-fitted. |
| 4 | **Knife-edge sensitivity** (disclosed, not a fix): refitting the calibration on DEV series with pre_usd ≥ $50k flips the holdout primary to +8.4% on 53 bets, which shows the sign rests on a handful of bets. | Valid as a disclosure. | Not used; it is a post-hoc choice. The same knife edge shows up in fix 6 below. | See the calibration-sensitivity rows in the Results table. |
| 5 | **The $50k total-volume gate in `C.markets()`.** The reviewer checked it and found it not material: the gate is outcome-correlated but almost never binds when pre_usd ≥ $25k, dropping about 0.3% of 2-map series, and restricting to pre_usd ≥ $50k makes the holdout worse. | I agree it is not material. | None; noted under Caveats. | None. |
| 6 | **pre_mid0 overlapped play.** pre_mid0 is taken from fills in [start − 10 min, start). In 9 DEV and 1 holdout series, map 1 ended before the scheduled start, so P0 and P_cal contain post-decision prices. The reviewer's fix: drop series with t_end < start + 10 min. | **Yes.** No primary bet was affected; 8 DEV mirror bets and 1 holdout mirror bet were. | `pre_clean = t_end ≥ game_start_ts + 600`. Both inputs are known at the decision. Series that fail it are dropped from the calibration fit and from every rule: 17 DEV and 2 holdout series.<br>Because the calibration's training set changed, **it was refitted on the cleaned DEV series only.** The Dota 2 dummy moved from −0.459 to **−0.414** (SE 0.20), a quarter of an SE. The round-1 fit is kept in `calib_v1.json` and evaluated as a sensitivity.<br>The choice of the refit as the headline was recorded before the holdout run. | The DEV primary drops from 109 to 89 signals: 21 marginal Dota 2 and 3 CS2 signals fall under the 0.04 threshold, and 4 Valorant signals are added.<br>The holdout primary drops from 60 to 53 signals. It loses 9 marginal Dota 2 bets (gap 0.042 to 0.047 under round 1, 0.031 to 0.039 under the refit; **all 9 lost**, at an average price of 0.29) and gains 2 Valorant bets (1 win).<br>Result: **+29.3% [−27.0, +92.2]** against +3.5% under the round-1 calibration.<br>If prices were fair, the chance that all 9 of those bets lose is about 4%. That is luck at the margin, not a property of the refit. |
| 7 | **Items the reviewer checked and found clean:** DEV-only calibration, child/series outcome order, leader detection (98%), payout orientation, the n_brk ≥ 3 filter, the BO3/BO5 markers, entry ≥ 3 s after the decision, and the title mapping. | — | None. | — |

**Protocol accounting.** The holdout has now been looked at three times:
1. The round-1 evaluation.
2. The reviewer's recomputation with the entry fix.
3. The round-2 run.

The second and third looks changed no threshold, window or model form. The only changes are execution (issue 1) and data hygiene
plus a DEV-only refit (issue 6). The 9-bet swing between calibrations shows that the choice of calibration moves the
size of the holdout ROI, but not the verdict. Treat the holdout numbers as descriptive; neither +29% nor +3.5% is a
reliable estimate of the edge.

## Rule as implemented

| Step | Implementation |
|---|---|
| Universe | Esports series moneylines in `C.markets()` with `pre_usd >= $25k`.<br>BO3 only: the event lists `-game1`, `-game2` and `-total-games-2pt5`, and none of `-game4`, `-game5`, `-total-games-3pt5` or `-total-games-4pt5`. These are listed before the match. The 2.5-map total excludes BO2 formats.<br>This gives 2,168 series: 1,367 DEV (start before 2026-07-01) and 801 holdout. |
| Map-1 end | `t_end` is the timestamp of the first game-1 child fill where the implied price of either outcome is ≥ 0.99; that outcome is the leader L. Prices are rounded to 1e-6, which fixes the round-1 float bug.<br>The child outcome names match the series `o0`/`o1` for every event. L equals the game-1 winner in 98.3% of DEV series and 97.9% of holdout series. |
| Hygiene (round 2) | Drop the series if t_end < game_start_ts + 10 min. This removed 17 DEV and 2 holdout series. |
| Model | P0 is L's `pre_mid0`, clipped to [0.01, 0.99]. Solve P0 = p²(3 − 2p); fair_iid = 1 − (1 − p)². |
| Calibration (DEV only, frozen) | Logistic regression of 1{L wins the series} on logit(fair_iid) plus title dummies, with CS2 as the base. Fitted on the 1,313 clean, non-void DEV series.<br>Coefficients: const +0.295, logit(fair_iid) +0.861, LoL +0.055, **Dota 2 −0.414** (SE 0.20), Valorant −0.225. |
| Signal | P_break is the median L-oriented series fill price over [t_end+30, t_end+150] s, with at least 3 fills. If P_break − P_cal ≥ 0.04, buy the trailing team T. |
| Execution | Decision at t_end+150.<br>(1) The first series fill acquiring T in [t_end+153, t_end+600] s, at its price.<br>(2) **If there is none (round-2 fix):** the quote proxy, 1 − the last L-acquiring print in [t_end+30, t_end+150] + 1c.<br>In both cases, add the taker fee (shares × fee_rate × p(1 − p)) at the fill's fee_rate. In the holdout, 52 of 53 bets filled at a window print and 1 at the quote proxy. The median entry is 183 s after t_end. |
| Bet and metric | One bet per series, held to resolution; void pays 0.5. ROI is `C.taker_roi` per $1 flat, with `C.cluster_ci` by series. +1c slippage is a sensitivity. |

## Results

ROI is per $1 after the actual taker fee, with a clustered 95% CI. Signals equal bets in every row.

| Rule | Period | Bets | ROI | 95% CI | ROI +1c | Win rate / average price |
|---|---|---|---|---|---|---|
| **Primary (gap ≥ +0.04, buy T)** | DEV, in-sample calibration | 89 | +65.2% | [+10.5, +128.4] | +55.5% [+4.7, +113.0] | 0.36 / 0.242 |
| Primary, cross-fitted calibration (5-fold, seed 0) | DEV | 103 | +5.8% | [−38.7, +58.0] | −1.0% | 0.21 / 0.252 |
| Primary, cross-fitted, 20 fold draws | DEV | median 97 | median +28.6% (range +5.2 to +66.8) | CI lower bound > 0 in 4 of 20 draws | — | — |
| **Primary** | **HOLDOUT** | **53** | **+29.3%** | **[−27.0, +92.2]** | **+22.8%** [−30.9, +81.9] | 0.28 / 0.203 |
| Primary, round-1 calibration (sensitivity) | DEV | 109 | +53.9% | [+8.6, +104.8] | +45.2% | 0.34 / 0.248 |
| Primary, round-1 calibration (sensitivity) | HOLDOUT | 60 | +3.5% | [−44.6, +61.3] | −1.5% | 0.23 / 0.217 |
| Primary, entry = next T print with no cap | HOLDOUT | 53 | +29.3% | [−27.0, +92.2] | +22.8% | identical: the one fallback's next print was also 0.14, at +636 s |
| Primary, entry = quote proxy for every bet | HOLDOUT | 53 | +34.0% | [−24.8, +100.0] | +27.0% | — |
| Primary, entry = quote proxy for every bet | DEV | 89 | +77.7% | [+13.9, +157.0] | +64.7% | — |
| Primary, cross-fitted DEV pooled with holdout | both | 156 | +13.7% | [−21.3, +54.4] | +7.1% | — |

**How fragile the primary rule is:**
- **Holdout, dropping the best series:** top 1 gives +16.0% [−37.7, +75.5]; top 3 gives **−5.8%**; top 5 gives −24.8%.
- **DEV, dropping the best series:** top 1 gives +50.3% [+5.2, +101.0]; top 3 gives +29.5% [−10.2, +73.2]; top 5 gives
  +16.9%.
- **Holdout by month:** Jul −15.0% on 16 bets; Aug +61.5% on 34; Sep −100% on 3.
- **Holdout by fee rate:** 0.05 on 46 bets gives +39.3%. 0.03 on 7 bets gives −36.6%; those markets were listed
  before July.
- **DEV by year:** 2025 +36.4% on 12 bets; 2026 H1 +69.7% [+8.1, +137.9] on 77.
- **By title:**
  - Holdout: Dota 2 34 bets, +30.7% [−31.8, +106.9]; Valorant 14 bets, +40.2%; CS2 4 bets, +11%; LoL 1 bet, −100%.
  - DEV: Dota 2 63 bets, +77.1% [+16.5, +146.0]; Valorant 21 bets, −1.4%.

### Variants (all tried are reported; round-2 calibration and execution)

| Variant | DEV | HOLDOUT |
|---|---|---|
| (a) threshold 0.03 | 134 bets, +34.1% [−6.4, +77.9]; +1c +26.6% | 79 bets, +8.0% [−37.5, +57.4]; +1c +2.4% (1 quote fill) |
| (a) threshold 0.06 | 25 bets, +71.9% [−5.1, +156.2]; +1c +64.6% | 14 bets, +46.7% [−51.1, +173.0]; +1c +40.0% (1 quote fill) |
| (b) mirror: gap ≤ −0.04, buy L | 379 bets, +8.1% [−0.2, +16.0]; +1c +6.4% | **230 bets, +6.4% [−3.8, +16.5]; +1c +4.7% [−5.4, +14.6]** |
| (b) mirror, cross-fitted DEV calibration | 379 bets, +3.7% [−4.4, +12.2]; over 20 fold draws the median is +6.1% and the CI lower bound is > 0 in 0 of 20 | not applicable |
| (b) mirror, cross-fitted DEV pooled with holdout | 609 bets, +4.8% [−1.4, +11.0]; +1c +3.1% | (pooled) |
| (b) mirror, round-1 calibration | 374 bets, +8.6% [+0.7, +16.8] | 225 bets, +6.0% [−4.7, +16.7] |
| (d) placebo: P_break − P_pre ≥ 0.04, buy T | 414 bets, −15.2% [−31.2, +2.0] (1 quote fill) | 246 bets, −12.6% [−33.9, +11.2] (1 quote fill) |
| (d) placebo mirror | 15 bets, −20.8% | 12 bets, −23.6% |
| Baseline: every trailer at the break | 1,199 bets, −10.5% [−22.3, +3.0] | 668 bets, −6.4% [−22.2, +9.5] |
| Baseline: every leader at the break | 1,199 bets, +2.9% [−0.5, +6.1] | 668 bets, +0.8% [−3.8, +5.5]; +1c −0.6% |
| (e) synthetic decider, long L's map 3 (series_L + game2_T), DEV only on 296 cached game-2 tapes, 227 priced | 42 bets, all with both legs (1 leg at the quote proxy), +3.7% [−6.9, +15.1]; +1c +2.0% | not run (DEV only) |
| (e) short L's map 3 | 0 signals: implied p3 is never above p̂ + 0.08 (mean p3 − p̂ = −4.4c) | not run |

**Notes on the variants:**
- **The threshold ladder is still not monotonic in the holdout:** 0.03 gives +8%, 0.04 gives +29%, 0.06 gives +47%, on
  79, 53 and 14 bets. That fits an effect concentrated in larger gaps, but also fits noise.
- **The placebo is negative, as it was in round 1.** Buying against pre-break drift loses.
- **Mirror robustness in the holdout:** dropping the top 3 series gives +3.9% [−6.2, +14.4]; dropping the top 5 gives
  +2.5%. By month: Jul +6.3%, Aug −1.9%, Sep +27.1%.

### Per-title calibration of the break price (y_L − P_break, round-2 clean sample)

| Title | Series DEV / holdout | DEV | Holdout | Every leader, holdout ROI | Every trailer, holdout ROI |
|---|---|---|---|---|---|
| CS2 | 510 / 289 | **+4.2c** (leader cheap) | **+5.6c** | +7.2% [+0.2, +14.1]; +1c +5.7% [−1.2, +12.5] | −21.1% |
| LoL | 418 / 213 | +3.0c | +0.1c | −0.7% | −2.1% |
| Dota 2 | 144 / 62 | **−4.2c** (leader dear) | **−5.3c** | −10.0% | +4.1% [−37.5, +50.3] |
| Valorant | 127 / 104 | +2.6c | −3.9c | −7.6% | +19.6% |

Pooled calibration: in DEV the calibrated model's Brier score is 0.1562, against 0.1568 for the break price. In the
holdout it is worse, 0.1706 against 0.1682.

## Interpretation

1. **The one pattern that persists is Dota 2.** Map-1 winners are about 4 to 5c too expensive at the break in both
   periods, on 206 series in total. The loser gets the next draft's first pick or side choice, which fits.
   - The primary rule turns that into ~20c longshot bets on Dota 2 trailers.
   - The holdout is positive (+30.7% on 34 Dota 2 bets), but its CI is [−32, +107].
2. **The momentum mechanism as pre-registered is wrong elsewhere.** In CS2 the map-1 winner is about 4 to 6c *cheap* at
   the break in both periods. This was found by looking at both periods, so it is a lead for a new pre-registered test
   on data after 2026-09-19, not a result. It is close to the dead "trade toward state prices" family.
3. **The holdout evidence is weak.** Two defensible calibrations fitted before the holdout give +3.5% and +29.3%. The 3
   best holdout series carry the whole positive result. The +1c result is −1.5% or +22.8%, depending on the calibration.
4. **What would settle it:** a pre-registered Dota 2-only test (buy the map-1 loser at the break when the price is at
   least 4c above the calibrated value), run on post-2026-09-19 data. The CI half-width is about 60 points on 53 bets, so
   getting it under 20 points needs about 450 to 500 bets. That is more than two years at the current 0.4 Dota 2 bets a
   day, unless maker execution improves the price.

## Capacity (holdout, 79.8 days)

- **Primary rule.** 0.66 bets a day. The median entry print is $7.9, which is about $95 a day at the first prints.
  - Other takers bought the trailer at no more than entry + 1c inside the entry window for a median of $255 per
    series. That puts the upper bound at about $1.4k a day, or roughly $3k to $40k a month, before market impact.
- **Mirror.** 2.9 bets a day. About $320 a day at the first prints, and at most about $15k a day in the window (a
  median of $973 per series).
- **Where the size is.** Dota 2, where the primary rule trades, has the thinnest in-play books.

## Caveats

- **The holdout was looked at three times** (see Review fixes). The round-2 choices were written down before the
  round-2 holdout run, but anyone who knows the round-1 and reviewer numbers should read the holdout as descriptive.
- **The DEV CI of the primary is optimistic in-sample.** Cross-fitted, it crosses zero, and it depends heavily on the
  fold draw: the ROI runs from +5% to +67% across 20 draws.
- **Knife edge.** The 0.04 threshold on a fitted calibration moves whole groups of marginal Dota 2 series in or out
  when the Dota 2 dummy shifts by 0.05. It is the dominant source of variation between reasonable specifications.
- **Multiple testing.** The run includes about 10 rule variants, 4 title splits, 3 entry conventions and 2
  calibrations. The significant-looking DEV cells are what K/20 chance would produce.
- **The quote proxy is an estimate.** It is the implied bid plus 1c, not an observed ask. It fills 1 of 53 holdout
  primary bets. Using it for every bet gives +34.0%, close to the headline, so the fill convention is not driving the
  result.
- **t_end is a proxy for the map end.** It is the first print at or above 0.99 in the game-1 child market. It can come
  before the ancient or nexus falls, or at CS2 match point. About 2% of series get the wrong leader because of glitch
  prints.
- **The total-volume gate** in `C.markets()` (≥ $50k) is outcome-correlated, but it almost never binds at pre_usd ≥
  $25k (reviewer check). Restricting to pre_usd ≥ $50k makes the round-1 holdout worse, so it does not create the
  positive result.
- **Tape sources.** 700 of the 2,168 game-1 tapes and all game-2 tapes were reused from the esports_identity_pickoff
  cache, which covers a superset window and was trimmed. 1,468 tapes were fetched fresh through the Data API at
  HOST_RPS 5.
- **Heavy tails.** Entries at 5 to 15c dominate the P&L. Equal-weight ROI per $1 swings by about 10 points for each
  14c winner.

## Round 1 (superseded)

Round 1 used the entry window with no fallback, no hygiene filter, and the calibration from `calib_v1.json`.

| Rule | DEV | HOLDOUT |
|---|---|---|
| Primary | 109 bets, +53.9% [+8.6, +104.8] | 59 bets from 60 signals, −6.3% [−50.8, +46.7]; +1c −10.7% → **DEAD** |
| Mirror | 382 bets, +8.3% [+0.9, +16.1] | 226 bets, +7.0% [−4.1, +17.6] |

A floating-point bug in the map-1 end detector (1 − 0.99 ≠ 0.01) was found in the first DEV run and fixed before the
round-1 holdout. The pre-fix DEV was +52.6%; it is kept in `results_dev_v0_floatbug.json`.
