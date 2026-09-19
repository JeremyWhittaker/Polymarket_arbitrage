# Esports BO3 identity pick-off: stale 1.5-map handicap vs the liquid game-2 market

**Verdict: DEAD.** This is round 2, after review.

- **The protocol's literal label would still be PROMISING.** On the clean holdout, which excludes 8 series the
  feasibility probe had already seen, the pre-registered rule returns +8.4% [-6.6%, +23.5%]. The DEV CI lower
  bound is -19.8%.
- **The positive holdout does not survive the robustness gate** adopted in this review round. The gate requires
  the holdout to stay above 0 at +1c after dropping the top 1% of series, both with all attempts and with one bet
  per game.
  - All attempts: +1.1%.
  - One bet per game: -3.4%. The gate fails.
- **The holdout gain is outcome variance on unhedged game-2 underdog legs.** Those legs produced 94% of the
  holdout P&L and lost 21% in DEV.
- **The parts that match the mechanism go negative in the holdout once the top 2 series are dropped.**
  - Completed pairs: -0.6%, and -2.6% at +1c.
  - The sequential variant: -0.7%, and -2.7% at +1c.
- **The contract identity is real, but the edge is too small.** The stale price is about 4c per share gross. The
  5% taker fee and buying at the next print consume it.

Code: `pmsports/research/h_esports_identity_pickoff.py`. Cache and logs: `data/research/h_esports_identity_pickoff/`.
- This round's files: `analyze_holdout_r2.log`, `results_all_r2.json`, `trades_primary_all_r2.parquet`,
  `trades_seq_*_r2.parquet`.
- The first run's files are kept unchanged: `analyze_holdout.log`, `results_all.json`, and the others.

## Review fixes

The pre-registered rule and every parameter are unchanged. Nothing was tuned on the holdout. The one rerun added
the exclusion and the robustness block below; the rule itself was not changed.

| # | Reviewer issue | Valid? | What changed | Result |
|---|---|---|---|---|
| 1 | Holdout sign depends on 3 to 5 of 177 series. | Yes | `robustness()` drops the top ceil(1%), 3 and 5 series by P&L, at the actual fee and at +1c, for all attempts and for one bet per game. | Clean holdout: +8.4% → +3.5% (top 2 dropped) → +1.7% (top 3) → -1.4% (top 5). At +1c: +5.9% → +1.1% → -0.7% → -3.7%. The top 2 series hold 60% of the P&L, and 45% of series are profitable. |
| 2 | Negative with one bet per game plus a 1% trim plus +1c. | Yes | Same block, one-bet-per-game frame (the first executed attempt per series). | +3.4% → -1.1% after the top-2 trim. At +1c: +1.0% → **-3.4%**. |
| 3 | Not distinguishable from zero. DEV has the opposite sign. Pooled is about 0. | Yes | `p_le0()` computes a one-sided clustered bootstrap P(ROI ≤ 0). The run also reports the pooled DEV + holdout result. | Clean holdout: P(≤0) = 0.14; at +1c, 0.22. DEV: 0.80. Pooled: +2.8% [-8.2, +13.3], and +0.3% at +1c (P(≤0) = 0.48). |
| 4 | The effect size is implausible and comes from naked game-2 legs. | Yes | Naked-leg diagnostics: share-weighted price paid, notX win rate, and a bootstrap of the DEV-to-holdout difference. | Clean holdout: naked legs are 94% of P&L ($803 of $858). notX won 43.8% at 0.379 in the holdout and 23.3% at 0.290 in DEV. The holdout-minus-DEV ROI difference is +34 pp [-9, +75], which is consistent with pure variance. The top 6 holdout series all come from naked legs. |
| 5 | The mechanism components (e) and (f) are not robust. | Yes | The same robustness block is run on completed pairs (e) and on the sequential variant (f). | Holdout, top 2 series dropped: (e) -0.60% [-2.1, +0.6] and (f) -0.66% [-3.8, +1.6]. At +1c: (e) -2.56% [-4.1, -1.4] and (f) -2.66% [-5.9, -0.4]. |
| 6 | Unstable across months and sports. | Yes | Month splits for the primary rule and for (f). | Primary, holdout: Jul +1.8%, Aug +13.8%, Sep -2.4%. DEV months range from -35% to +6%. (f), holdout: Jul -4.4%, Aug +2.7%, Sep +22% (only $458 deployed). |
| 7 | The holdout was seen before pre-registration, and this was not disclosed. | Yes | See the disclosure after this table. | Excluding the probe series changes the holdout from +8.90% to +8.38%. |
| 8 | Multiple testing on (f). | Yes | Disclosure below. | The DEV CI for (f) is optimistic. (f) does not replicate robustly in the holdout. |

**Issue 7 disclosure.** The feasibility probe that motivated this hypothesis ran before pre-registration. It used
`es_sample.py`, `es_test.py` and `ip_test45.py` in the scratchpad.
- **What it looked at.** It sampled 45 series from 2026, and 21 of them are holdout series. They are listed in
  `PROBE_HOLDOUT`.
- **What it used.** `es_test.py` used exactly the frozen parameters: sum < 0.98, 20 s lookback, entry in
  [t+3, t+60], 30 s spacing. `ip_test45.py` ran the primary rule on all 45 series, holdout included, before the
  official holdout run.
- **Where the headline came from.** The "+2.85c per pair" figure that motivated the hypothesis came from these
  series.
- **Overlap with the sample.** 8 of the 21 probe holdout series fell into the seeded holdout sample, and all 8 were
  fetched. They are now excluded from every holdout number (`analysis_frame()`).
- **What was not changed.** The sample is not re-drawn. The seeded draw is kept, and the 8 series are dropped
  afterwards.
  - Removing a fixed set from a uniformly random ordering leaves a uniformly random sample of the rest of the
    frame. That is the same distribution as excluding them before the draw, which is what the reviewer suggested.
  - The 2,000-market fetch budget is already spent. A re-draw would need new tapes.
  - The fetch itself is left as it ran, so it stays reproducible.
- **DEV probe series stay in**, because DEV may be used freely.
- **Result.** The clean holdout has 342 sampled series, 319 of them traded. The first-run figure, which included
  the probe series, is kept as a reference line in the log.

**Issue 8 disclosure.** (f) was chosen on DEV after about 9 configurations of the sequential rule:
- a first-print-only hedge with thresholds 0.97, 0.98 and 0.99, each for the identity pair and the reverse pair
  (6 configurations);
- a full-walk hedge with thresholds 0.97, 0.98 and 0.99 (3 configurations);
- plus latency, size-cap and first-per-series diagnostics.

The scripts (`ip_seq_dev*.py`) filter to DEV only. The only holdout runs were the first official run and this
rerun. Neither changed any parameter.

**Verdict gate (issue 3 fix).** The protocol's PROMISING label needs only holdout > 0. I adopted the reviewer's
stricter reading and coded it as `ROBUST_GATE`. The protocol names +1c as the cost sensitivity and one bet per game
as the default, and a PROMISING label should not rest on 2 series. So the label is kept only if the holdout ROI
stays > 0 at +1c after dropping the top 1% of series, with all attempts **and** with one bet per game.
- Gate values: all attempts +1.08%; one bet per game **-3.39%**.
- Gate fails, so the final verdict is **DEAD**.

**Correction.** Resolution failures (a voided game 2 and a wrong T1) cost the holdout pairs $39 of the $92 they
would have earned at $1 per share. The first report said "$23 of $53", which was a different and wrongly labeled
quantity.

## Mechanism
- After team X wins map 1 of a best-of-3, "X -1.5 maps" (X wins 2-0) pays exactly when X wins map 2.
- That is the same event as X winning the game-2 child moneyline.
- The two markets trade in separate books.
- If the thin handicap book lags when game 2 swings, buying X -1.5 plus notX in game 2 pays $1 for less than $1.

## Rule as implemented (primary, pre-registered, frozen)
- **Sample (pregame information only).** An esports series (universe `event_slug`) is eligible if all of these hold:
  - it has `-game1` and `-game2` child moneylines and no `-game4`;
  - its series moneyline is in `C.markets()` with `pre_usd >= $25k`;
  - it has a `-(map|game)-handicap-(home|away)-1pt5` market.

  That gives 2,073 series. I dropped 32 that list a best-of-5 marker (`-game5`, `-total-games-3pt5/4pt5`),
  because the identity fails for best-of-5. That leaves 2,041: 1,250 DEV (start < 2026-07-01) and 791 HOLDOUT. I
  drew a seeded random sample of 350 per period. The 8 sampled holdout series the probe had seen were then dropped,
  leaving an analysis frame of 350 DEV and 342 HOLDOUT series.
- **Data.** Trade tapes come from the Data API (`pmsports.polymarket.trades`, window [start-1h, closed_ts],
  `HOST_RPS=5`).
  - game1 was fetched for all sampled series, and X was taken from it.
  - game2 and the *identity* handicap were fetched only where X is the -1.5 side. X is known at T1, before any
    signal.
  - Budget cap: 2,000 markets.
  - **Traded series: 323 DEV and 319 HOLDOUT.**
- **Handicap orientation.** Outcome 0 is the -1.5 side. I checked this against gamma for all 642 traded
  handicaps, and all passed:
  - the question names the "(-1.5)" team;
  - the description resolves to outcome 0 "if it wins 2 or more maps";
  - gamma `outcomes[0]` equals outcome 0.
- **Map-1 end.**
  - T1 is the first game1 fill whose implied price for either outcome is ≥ 0.99. That outcome is X.
  - X matched the actual game-1 winner in 98.4% of series (11 misses).
  - T2 is the first game2 fill after T1 with an implied price ≥ 0.99.
- **Ask proxies at time t.**
  - `ask_hc` is the price of the last fill that bought X -1.5 in [t-20, t].
  - `ask_g2` is the price of the last game-2 fill that bought notX in [t-20, t].
  - If several fills share the last second, I use the highest price.
- **Signal.** A fill time t with t in (T1+5 s, T2) and `ask_hc + ask_g2 <= 0.98`. At most one attempt per 30 s
  per series.
- **Execution.**
  - Each leg fills at the first second with prints on its side in [t+3, t+60].
  - Price: that second's VWAP plus the taker fee `shares*fee_rate*p*(1-p)`.
  - Size: min(the two prints, 100 shares).
  - If only one leg prints, it is held alone to resolution.
  - Prints used by one attempt are not reused.
- **Payoff and metric.** Payoffs are the actual universe payouts (0.5 on voids). The metric is $ P&L per $
  deployed, with `C.cluster_ci` clustered by series.

## Results: primary rule (holdout = clean frame, probe series excluded)

| | trades (pairs / legged) | series | $ deployed | P&L | ROI | 95% CI | P(ROI≤0) | ROI +1c |
|---|---|---|---|---|---|---|---|---|
| **DEV** | 350 (182 / 168) | 102 | 6,223 | -394 | **-6.34%** | [-19.80, +9.14] | 0.80 | -8.72% [-21.9, +6.4] |
| **HOLDOUT** | 552 (238 / 314) | 172 | 10,238 | +858 | **+8.38%** | [-6.55, +23.49] | 0.14 | +5.85% [-8.8, +20.6] |
| Holdout incl. the 8 probe series (first run, reference) | 564 | 177 | 10,433 | +929 | +8.90% | [-5.92, +23.70] | 0.12 | +6.34% |
| Pooled DEV + holdout | 902 | 274 | 16,461 | +463 | +2.81% | [-8.19, +13.29] | 0.31 | +0.33% |

### Robustness (clean holdout; DEV in brackets)

"Top k" means the k most profitable series by P&L. 1% of series is 2 in both periods.

| frame | cost | full | drop top 1% (2) | drop top 3 | drop top 5 |
|---|---|---|---|---|---|
| all attempts | fee | +8.38% (-6.3%) | +3.48% (-11.8%) | +1.70% (-13.9%) | -1.41% (-17.6%) |
| all attempts | +1c | +5.85% (-8.7%) | **+1.08%** (-14.1%) | -0.65% (-16.1%) | -3.70% (-19.8%) |
| one bet per game | fee | +3.41% (-11.3%) | -1.14% (-17.7%) | -3.25% (-21.1%) | -7.21% (-26.8%) |
| one bet per game | +1c | +1.03% (-13.5%) | **-3.39%** (-19.8%) | -5.44% (-23.0%) | -9.30% (-28.7%) |

**Series-level view, clean holdout.**
- 45% of series are profitable.
- The equal-weight mean series ROI is +1.1%, and the median is -5.6%.
- The top 2 series account for 60% of the P&L.
- In DEV, 41% of series are profitable, with a median of -12.6%.

### Where the P&L came from

| | completed pairs (riskless part) | legged trades (one leg only) |
|---|---|---|
| DEV | +3.28% [2.05, 4.44], $3,670, +$120 | -20.2% [-53.0, +14.5], $2,552, -$515 |
| HOLDOUT | +1.36% [-1.38, 5.38], $3,887, +$53 | +12.7% [-11.6, +37.5], $6,351, +$805 |
| Pooled | +2.29% [0.67, 4.34] | leg_b +3.1% [-17.1, +23.8] |

- **Why legged trades happen.** The handicap book is so thin that its leg often does not print within 60 s. The
  rule then holds a naked game-2 bet on notX, usually an underdog.
- **Price and win rate:**

  | | notX price (share-weighted) | notX won |
  |---|---|---|
  | DEV | 0.290 | 23.3% |
  | Holdout | 0.379 | 43.8% |

- **The holdout-minus-DEV ROI difference is +34 pp [-9, +75].** That is pure outcome variance. Every one of the 6
  most profitable holdout series made its money on naked legs; the largest was `lol-ns-dnf-2026-08-08`, +$288.

Sanity checks on completed pairs:

| | pairs | payoff per share | mean signal sum | mean executed sum | executed sum < 1 | fees per $ | gross edge / share |
|---|---|---|---|---|---|---|---|
| DEV | 182 | 1.0 in all 182 | 0.947 | 0.967 | 85% | 1.0c | 4.18c |
| HOLDOUT | 238 | 1.0 in 229, 0.5 in 6, 0.0 in 3 | 0.926 | 0.945 | 72% | 1.9c | 4.18c |

- **Where the non-1.0 payoffs come from.** Two series:
  - `cs2-sparta-g2a-2026-07-28`: game 2 was voided, while the handicap resolved normally.
  - `val-mibrlo-agal-2026-07-07`: T1 named the wrong map-1 winner.
- **Gross edge vs costs.** The gross edge is 4.2c per share in both periods. Fees take 1.0c at the 3% rate and
  1.9c at the 5% rate.
- **Resolution failures** cost the holdout pairs $39 of $92.
- **Slippage.** +1c on both legs costs 2c per pair, which leaves the holdout pairs negative.

Other diagnostics (clean holdout):
- first attempt per series: +3.4% [-13.7, +21.7];
- uncapped size: +25.6% [-12.6, +58.2], driven by a few huge prints (DEV: -9.3%).

## Variants (holdout = clean frame)

| variant | DEV ROI [CI] (bets) | HOLDOUT ROI [CI] (bets) |
|---|---|---|
| (a) threshold 0.97 | -6.89% [-22.9, +8.7] (295) | +5.50% [-10.3, +21.4] (476) |
| (a) threshold 0.99 | -7.09% [-21.7, +7.1] (411) | +8.60% [-5.4, +22.3] (642) |
| (b) latency +5 s (entry [t+8, t+65]) | -8.89% [-25.4, +7.9] (351) | +7.47% [-8.0, +23.3] (546) |
| (b) latency +10 s (entry [t+13, t+70]) | -18.96% [-37.5, +2.5] (351) | +9.81% [-5.9, +26.0] (550) |
| (c) reverse pair (notX +1.5 plus X in game 2) | -3.27% [-16.8, +10.9] (293) | +0.10% [-10.7, +10.2] (477) |
| (d) total-games-2pt5 Under plus notX in game 2 (45 probe series) | +0.30% [-23.0, +24.4] (131) | not run (DEV only) |
| (d) same, completed pairs only | +1.64% [-0.7, +4.3] (86) | not run |
| (d-ref) primary rule on the same 45 series | -8.66% [-41.8, +40.6] (48) | not run |
| (e) completed pairs only* | +3.28% [+2.05, +4.44] (182) | +1.36% [-1.38, +5.38] (238) |
| (e) completed pairs only, +1c* | +1.19% [+0.01, +2.30] | -0.68% [-3.32, +3.18] |
| (e) drop top 1% of series | +2.99% [1.85, 4.24] | -0.60% [-2.12, +0.60] |
| (e) drop top 1%, +1c | +0.84% [-0.20, 1.76] | -2.56% [-4.06, -1.39] |
| **(f) sequential, thin leg first then hedge† (post-hoc)** | **+4.67% [+2.24, +7.14] (187)** | **+2.43% [-2.12, +7.35] (241)** |
| (f) +1c | +2.59% [+0.19, +5.04] | +0.30% [-4.17, +5.00] |
| (f) first attempt per series | +3.54% [+1.95, +5.51] (69) | +2.92% [-1.27, +9.70] (114) |
| (f) drop top 1% of series | +3.38% [1.78, 4.96] | -0.66% [-3.82, +1.62] |
| (f) drop top 1%, +1c | +1.32% [-0.25, 2.87] | -2.66% [-5.85, -0.40] |
| (f) one bet per game, drop top 1%, +1c | +1.07% [-0.31, 2.67] | -1.88% [-3.96, -0.22] |
| (f) entry from t+8 | +5.19% [+1.54, +8.41] | +2.50% [-1.92, +8.11] |
| (f) uncapped size | +18.2% [+1.0, +23.5] ($51.8k, a few huge prints) | +0.25% [-5.4, +4.9] ($8.1k) |

\* (e) conditions on the second leg printing within 60 s. That is future information, so (e) is a diagnostic of the
riskless component, not a tradable rule.

† (f) uses the same signal. It buys the handicap leg at its next print, and only after that fills does it hedge the
same share count in game 2, walking game-2 prints in [ta+3, ta+60]. It was designed on DEV after about 9
configurations (see issue 8) and frozen before the first holdout run.
- (f) by holdout month: Jul -4.4%, Aug +2.7%, Sep +22.2% (on $458).
- Series-level, holdout: 60% of series profitable, median +1.2%.

Variant (d) data: the 45 probe series were chosen on **total** child-market volume ≥ $20k, which is outcome- and
post-start-driven (trap #1). The run uses only their 24 DEV series.

## Per-sport split (holdout = clean frame)

| sport | primary DEV | primary HOLDOUT | (f) DEV | (f) HOLDOUT |
|---|---|---|---|---|
| cs2 | -8.4% (194 bets, 51 series) | -1.0% (216, 72) | +5.6% [1.8, 8.6] | +5.8% [-2.1, 15.6] |
| lol | +11.1% (130, 40) | +15.1% (236, 64) | +2.8% [0.3, 4.6] | +3.4% [-1.0, 10.3] |
| dota2 | -64.2% (22, 8) | +4.9% (51, 18) | +10.4% (8 bets) | +2.3% [0.2, 4.6] (19 bets) |
| val | -83.5% (4, 3) | +17.1% (49, 18) | +9.4% (2 bets) | -29.5% [-75, -4] (17 bets) |

- Every per-sport primary CI crosses zero.
- The LoL holdout gain is naked-leg outcomes.

## Capacity
The binding constraint is handicap print size: 17 to 21 shares per pair on average.

| | sample deployed | scaled to all eligible series | P&L | note |
|---|---|---|---|---|
| Primary, holdout | $10.2k over 77 days (Jul $2.9k, Aug $6.1k, Sep $1.3k) | ×2.31 (791 eligible / 342 sampled) → about **$307/day, roughly $9k/month** | about $26/day | from unhedged legs; not repeatable |
| Variant (f), holdout | — | about **$155/day** | about $3.8/day | the trimmed and +1c versions are negative |

## Caveats
1. **Probe contamination (fixed).** 21 holdout series were inspected before pre-registration, with the frozen
   parameters. The 8 that fell into the sample are excluded. The effect on the headline was small, 8.90% to
   8.38%.
2. **The positive primary holdout is not evidence for the mechanism.** 94% of it comes from naked game-2 legs. It
   disappears after dropping 2 to 5 series, and it is negative with one bet per game after a 1% trim.
3. **Execution assumes we get the next print on our side, at that second's VWAP.** The signal is usually another
   taker lifting the stale handicap quote. We then buy the next level, a median of 10 to 13 s later. Book depth
   and queue position are unknown.
4. **Timestamps have 1-second, on-chain resolution.** The within-second order of fills is ambiguous. I handled it
   conservatively: the maximum price for the signal, and VWAP for execution.
5. **Non-identity risks are in the P&L:** voided child markets, false T1 prints (1.6% of series), and forfeits.
6. **The traded sample is a subset.** Only 642 of the 2,041 eligible series were traded, because of the fetch
   budget. The sample is random and seeded. Capacity is scaled by eligible/sampled.
7. **Fees differ by period.** DEV has 0 or 3%; the holdout has 5%. The completed-pair edge shrinking from +3.3% to
   +1.4% is consistent with the fee rise plus noise.
8. **Variant (f) is post-hoc,** selected from about 9 DEV configurations. Its DEV CI is optimistic.

## Verdict
- **Final: DEAD.** The protocol's literal label is PROMISING: the clean holdout is +8.4% > 0 and the CIs cross 0.
  The label fails the robustness gate, which requires the holdout to stay above 0 at +1c after a 1% series trim,
  with one bet per game.
  - The holdout's P(ROI ≤ 0) is 0.14.
  - DEV has the opposite sign, with P(ROI ≤ 0) = 0.80.
  - Pooled at +1c, the rule is +0.3%.
- **Substantive reading.** The identity is real: completed pairs pay exactly $1, and stale handicap prints sit
  about 4c below parity. But buying at the *next* print and paying the 5% taker fee leaves about 1c per $, with
  CIs that include 0. That edge is negative once the top 2 series are dropped, and it is gone at +1c. The
  riskless versions, (e) and (f), fail the same robustness checks in the holdout.
- **A possible retest.** A maker version, or a book-watching bot that lifts the stale quote itself rather than the
  next print, would be a *new* hypothesis. It would need live order-book capture and fresh post-September data.
