# Soccer halftime market making (`halftime_intermission_mm`)

**Verdict: DEAD** (after review). Under realistic queue and fill assumptions there is no out-of-sample profit.

- **The pre-registered back-of-queue gate fails.** After a price-tolerance bug fix it loses **-1.95% of capital in the holdout, CI [-3.47%, -0.88%]**, and it also loses in development: -1.01%, CI [-1.61%, -0.50%].
- **A one-order queue model with measured halftime book depth also loses.** Q is drawn from depth measured in live books: soccer halftime live capture 2026-09-19 (229 prints, 5 legs, 3 games: elc-mil-whu-2026-09-19, epl-tot-ast-2026-09-19, lal-osa-ray-2026-09-19) (see "Queue depth, measured"). Holdout: -0.129 [-0.427, +0.088] c/sh, ROI -0.23% [-0.77, +0.16], negative for all 5 random seeds. With no maker rebate: ROI -0.41% [-0.96, -0.02]. DEV is only +0.18% [+0.04%, +0.29%] under this model.
- **The pre-registered front-of-queue fill is not realistic.** It needs priority at the level that just traded, which a newcomer does not have. Even so, it is only +0.17% in the holdout, CI [-0.26%, +0.49%], and -0.04% with no rebate.

The scheduled-blackout effect itself is real. Halftime beats the in-play control windows by 0.7-1.9c/share, depending on the fill model. What it produces is a **front-of-queue (incumbent) edge**: +0.41c/share (DEV) and +0.26c/share (holdout) on fills exactly at the level just printed. Fills where the print trades through our price lose 1.2c/share in the holdout.

Today's live books show why a newcomer cannot collect it. **Makers pile into the book when it is safe.** On universe-like legs, the queue at the last-print level is a median of 6,866 shares at halftime against 645 in live play, and it is empty only 6% of the time against 30% in live play. The spread goes to whoever is already at the front. A new maker joining after each print captures nothing: DEV +0.10c, holdout -0.13c/share.

Code: `pmsports/research/h_halftime_intermission_mm.py`. Results: `data/research/h_halftime_intermission_mm/results_full.json`. The as-originally-run results are kept as `results_full_v1_as_run.json`, and the MLB-calibrated re-run as `results_full_mlbQ.json`. Live-depth calibration: `live_depth.json`, `live_depth_{mlb,soccer}.parquet`, and the raw capture in `live/2026-09-19/`.

## Review fixes

Two reviews (execution and stats) found the PROMISING label unsupported. Every point was checked against the code and re-run. I found one further bug myself (N1). No parameter of the primary rule was changed.

| # | Issue (reviewer) | Valid? | What changed | Effect |
|---|---|---|---|---|
| E1 | The verdict rests on a front-of-queue fill: 87% of filled shares are prints exactly at our quote, which needs queue priority. Trade-through fills lose money. (execution) | Yes | Added a one-resting-order **queue model** to `simulate(queue=Q)`: one order of K=50 at our quote with Q shares ahead; a print at q == a eats the queue ahead first; a print at q > a trades through and fills us (capped at the print size); we rejoin at the back after a reprice, or at the next quote epoch once fully filled. Under the reviewer's reference semantics it matches `verify_halftime_intermission_mm_execution.sim(model='queue')` exactly (to the cent at Q = 0, 250 and inf). The verdict is now **gated on realistic execution** (see Verdict). The fill-type split is reported. | Holdout, front-of-queue fills: q == a +0.262c/sh [+0.08, +0.40]; q > a **-1.204c/sh [-1.95, -0.62]** |
| E2 | Queue-depth sensitivity: the holdout breaks even at about 250 shares ahead. (execution) | Yes | Q grid {0, 100, 250, 500, 1k, 2k, 5k, 20k, inf} in the script. | After the N1 fix, the holdout breaks even at about 1,000 shares ahead and DEV at about 4,000 (table below). |
| E3 | "Real queues are far deeper than that break-even (touch depth 1k-260k shares, MLB 20.7k at the touch)." (execution) | Method: partly disagree. Conclusion: **confirmed** for soccer | The queue a newcomer joins is not the touch at a random moment. It is the size resting at the *last-print price* when we post, about 3 s after the print. That level is often partly or fully cleared. I measured this directly with `live_depth()`, in the recorded MLB books and in a new **live capture of soccer halftime books** (2026-09-19). The realistic model draws Q from the measured distribution at every (re)join. | MLB inning breaks: median 1,603 shares ahead, **29% zero** (the level was cleared and a new order would be first; 99% of zeros have best ask > print price). **But soccer halftime, the market that matters, confirms the reviewer:** universe-like legs at halftime: median 6,866 shares ahead, 6% zero, p75 11,650 (229 prints, 3 games), about the full touch. The level is rarely cleared at halftime. The realistic holdout is ROI -0.23% [-0.77, +0.16]. With the shallower MLB-break calibration it would be +0.16% [-0.17, +0.44], and -0.03% without the rebate. |
| E4 | The pre-registered execution stress fails, so the verdict is DEAD, not PROMISING. (execution, stats) | Yes | Verdict logic added to `run()`: DEAD if any realistic model's holdout ROI is <= 0 (a tie counts as a failure); otherwise the protocol rules applied to realistic fills. | **DEAD.** The back-of-queue stress was also too lenient (N1). Corrected, it is -1.95% holdout and -1.01% DEV. |
| E5 | The positive holdout comes only from the assumed 15% per-fill rebate. 37% of holdout fills are in 3%-fee markets, not 5%. (execution, stats) | Yes | Rebate grid {0, 5, 10, 15, 20%} for every headline model, and a second verdict computed with rebate = 0. The fee label is fixed: holdout filled shares are 38% at fee_rate 0.03 and 62% at 0.05. The code already used each print's own `fee_rate`. | Front-of-queue holdout ROI by rebate: -0.04 / +0.03 / +0.10 / +0.17 / +0.24%. The verdict with no rebate is also DEAD. |
| E6 | The markout is not realizable P&L. (execution) | Yes | Unchanged; stated as a caveat. Hold-to-resolution variants are reported. | Flattening costs a 5% x p(1-p) taker fee (about 1.2c at p = 0.5) plus half the spread, far above the edge. |
| E7 | Capacity is negligible. (execution) | Yes | Capacity is recomputed for the front-of-queue, back-of-queue and realistic models. | See Capacity. |
| E8 | Latency, tick-grid, replenishment and same-second checks do not flip the sign. (execution) | Yes | Now in the script: quote delay 5/10/20 s, quotes rounded up to the tick, same-second prints as one taker event, trade-through filling the whole remaining order, and combinations with the queue model. | Neutral for the front-of-queue fill. With the realistic queue, both make the holdout worse: "tick + same-second + 5 s delay" gives -0.52%, and "trade-through fills whole order" gives -0.49%. |
| E9 | The markout ref needs a two-sided print in [KO+59, KO+61], which drops 2,139 of 4,617 legs based on post-window activity. (execution) | Yes | Added `ref_mode='asof'`: the ref is the last two-sided print mid at <= KO+61, of any age (the cache starts at KO+15), with a one-sided fallback. Only 5 legs lack any print. Primary, back-of-queue and realistic queue are reported under it. | Same conclusions: front-of-queue +0.23%, back-of-queue -2.01%, realistic -0.27% [-0.66, +0.04] in the holdout. |
| E10 | The post-hoc US/MX exclusion is not a false negative. (execution) | Agree | Kept as post-hoc only. The US/MX list now includes the Leagues Cup (`soccer-lec`), which was missing. | See Post-hoc diagnostic. |
| S1 | The holdout was not clean. `explore20.py` (the prior check behind the pre-registration) pooled "2026" with no cutoff, including Jul-Sep 2026, and the window-location volatility profile used all periods. (stats) | Yes | Disclosed in Holdout discipline. The quoted prior, "+0.10c in 2026 (CI > 0)", cleared 0 only because holdout data was in it (Mar-Jun alone: +0.094c [-0.006, +0.186]). | The holdout is not virgin, which biases it *toward* a positive result. The rule still fails. |
| S2 | The holdout sign flips below about an 8% rebate. (stats) | Yes | Rebate grid (E5). | After the N1 fix, the flip point is about 5%. |
| S3 | The sign depends on the World Cup (20 events). (stats) | Yes | World Cup exclusion and leave-one-league-out in `robustness()`. | Front-of-queue holdout without the World Cup: -0.19% [-0.87, +0.32]. |
| S4 | Outliers: the top 1% of events carry 70% of holdout P&L. (stats) | Yes | Drop-top/bottom-1%, trim, and the number of top events needed to flip the sign, all in `robustness()`. | After the fix: the top 3 events carry 41% of holdout P&L, and 9 events flip the sign (DEV needs 415). |
| S5 | Monthly instability in the holdout. (stats) | Yes | Month table. | Jul +0.49%, Aug -0.67%, Sep +0.57% (front of queue). |
| S6 | All holdout edge is in q == a fills. (stats) | Yes | Same as E1. | |
| S7 | The realistic-queue requirement means DEAD. (stats) | Yes | Same as E4. | DEAD |
| S8 | Clustering and CIs are fine. (stats) | Confirmed | Clustering by event, date, league-date and league is reported. | League clustering widens the holdout CI to [-1.18%, +0.64%]. |
| S9 | The ref drop does not bias the result. (stats) | Confirmed | As-of ref (E9). | |
| **N1** | **New (found while reviewing): price-equality tolerance bug.** Prices are float32 values derived from usdc/size, so one tick appears as e.g. 0.109999985 and 0.10999996. `EPS = 1e-9` treated such pairs as different prices. | Yes | `PX_TOL = 1e-6` (the smallest tick is 0.001) in the skip rule, the fill test, the back-of-queue "higher price" test and the trade-through test. `px_tol=EPS` reproduces the as-run numbers, which are shown next to the fixed ones. | (1) The skip rule dropped exact 3-tick spans whose float noise was positive: 474 events skipped, now 366. (2) The primary missed at-level prints with negative noise. (3) **Back-of-queue counted same-price prints as "higher"**, which made it too lenient: DEV +0.036c became -0.557c, and holdout -0.447c became -1.061c/sh. |

Nothing was tuned on the holdout. The queue-depth calibration rule was fixed before any soccer halftime was captured. "Use soccer halftime depth if >= 100 prints, otherwise MLB breaks" was in `realistic_queue()` before the first holdout run of this round. That run used the MLB fallback, since no soccer halftime existed yet. Two refinements were added at 07:55 UTC on 2026-09-19, after that run but still before any soccer halftime data existed: only universe-like legs (volume at kickoff >= $25k), and kickoff slots added in time order (11:30/12:00 first). The final calibration used the first slot group: 229 prints.

## Rule as implemented (primary frozen before the first holdout run)

| Item | Implementation |
|---|---|
| Universe | `C.markets()` with `family=='soccer'`, `o0=='Yes'`, `o1=='No'`, `pre_usd >= 25,000`. That is 4,617 legs in 3,088 events: DEV 2025 522 legs, DEV 2026H1 2,810, HOLDOUT 1,285. |
| KO / split | KO = `game_start_ts` (scheduled). DEV if KO < 2026-07-01 (reported as 2025 and 2026H1), HOLDOUT otherwise. |
| Window | Taker prints with `ts - KO` in [52, 60] min. |
| Skip rule | Drop the whole event if any universe leg has a Yes-converted fill-price span > 0.03 in [KO+49, KO+52) min, with price tolerance 1e-6. 366 of 3,088 events are skipped (474 as originally run). |
| Quote | Our ask on token s at a print at time t is the price of the last print acquiring s at ts <= t-3 s. When several prints share that second, take the highest (the end of a sweep). No quote if that print is older than 120 s. |
| Fill, pre-registered (front of queue) | A print acquiring s at q >= a fills min(size, 50) shares at a. Cumulative cap 1,000 shares per token per leg. |
| Fill, realistic (review fix) | (i) Back-of-queue stress (pre-registered gate): the fill counts only if another same-side print at a price > a (beyond tolerance) occurs within [t, t+10 s]. (ii) Queue model: one resting 50-share order, Q shares ahead, with Q drawn from the measured depth distribution at each (re)join. |
| P&L per share | Markout = a - ref_s + rebate x fee_rate x a(1-a), with rebate 0.15 (grid 0-0.20). ref = the leg's two-sided print mid at KO+61 (last print of each side in [KO+59, KO+61]); the as-of variant is in E9. |
| Statistics | Share-weighted. `C.cluster_ci` by `event_slug` (all 3 legs of a game form one cluster), 2,000 draws. ROI = markout $ / capital $, with capital 1 - a per share. |
| Verdict | For each realistic model: DEAD if holdout ROI <= 0; PROFITABLE if the holdout CI and the DEV CI are both > 0; else PROMISING. Final verdict = the worst over the realistic models. The same verdict is also computed with rebate = 0. |

## Main results

| Variant | DEV 2025 | DEV 2026H1 | DEV pooled c/sh [CI] | DEV ROI [CI] | HOLDOUT c/sh [CI] | HOLDOUT ROI [CI] | HOLDOUT events / fills | HOLDOUT P&L $ |
|---|---|---|---|---|---|---|---|---|
| Front of queue (pre-registered fill; float fix) | +0.373 | +0.331 | +0.334 [+0.290, +0.372] | +0.64% [+0.55, +0.71] | +0.089 [-0.138, +0.253] | +0.17% [-0.26, +0.49] | 288 / 10,816 | +263 |
| Front of queue, as originally run | +0.368 | +0.320 | +0.324 [+0.276, +0.366] | +0.63% [+0.53, +0.71] | +0.056 [-0.179, +0.238] | +0.11% [-0.35, +0.47] | 277 / 8,882 | +149 |
| **(b) Back-of-queue stress (pre-registered gate; float fix)** | -0.097 | -0.582 | -0.557 [-0.893, -0.278] | -1.01% [-1.61, -0.50] | -1.061 [-1.878, -0.488] | -1.95% [-3.47, -0.88] | 125 / 1,945 | -643 |
| (b) Back-of-queue, as originally run | -0.003 | +0.038 | +0.036 [-0.142, +0.176] | +0.06% [-0.24, +0.30] | -0.447 [-1.111, -0.036] | -0.75% [-1.87, -0.06] | 169 / 3,481 | -487 |
| **Realistic queue: Q ~ measured depth** (soccer halftime live capture 2026-09-19 (229 prints, 5 legs, 3 games: elc-mil-whu-2026-09-19, epl-tot-ast-2026-09-19, lal-osa-ray-2026-09-19)) | +0.169 | +0.093 | +0.098 [+0.023, +0.165] | +0.18% [+0.04, +0.29] | -0.129 [-0.427, +0.088] | -0.23% [-0.77, +0.16] | 222 / 2,555 | -108 |
| Queue, Q = measured median (6,866 shares) | +0.107 | -0.060 | -0.048 [-0.146, +0.039] | -0.09% [-0.27, +0.07] | -0.287 [-0.578, -0.056] | -0.54% [-1.07, -0.10] | 188 / 1,771 | -170 |
| Queue, Q = 0 (always first) | +0.370 | +0.375 | +0.374 [+0.347, +0.399] | +0.71% [+0.66, +0.75] | +0.188 [-0.028, +0.331] | +0.36% [-0.05, +0.63] | 288 / 11,000 | +504 |
| Queue, Q = inf (fill only on trade-through) | +0.116 | -0.147 | -0.124 [-0.246, -0.018] | -0.22% [-0.44, -0.03] | -0.598 [-1.036, -0.261] | -1.08% [-1.87, -0.47] | 176 / 1,371 | -237 |

### Verdict table (ROI on capital)

| Fill model | DEV ROI [CI] | HOLDOUT ROI [CI] | Protocol verdict |
|---|---|---|---|
| front-of-queue primary (pre-registered fill, NOT realistic) | +0.64% [+0.55, +0.71] | +0.17% [-0.26, +0.49] | PROMISING |
| (b) back-of-queue (pre-registered gate) | -1.01% [-1.61, -0.50] | -1.95% [-3.47, -0.88] | DEAD |
| queue model Q~measured depth | +0.18% [+0.04, +0.29] | -0.23% [-0.77, +0.16] | DEAD |
| (b) back-of-queue, rebate 0 | -1.07% [-1.68, -0.56] | -2.14% [-3.67, -1.06] | DEAD |
| queue model Q~measured, rebate 0 | +0.11% [-0.02, +0.23] | -0.41% [-0.96, -0.02] | DEAD |
| (b) back-of-queue, as-of ref | -1.05% [-1.67, -0.57] | -2.01% [-3.26, -1.04] | DEAD |
| queue model Q~measured, as-of ref | +0.14% [+0.02, +0.25] | -0.27% [-0.66, +0.04] | DEAD |
| **FINAL (worst of the realistic models, 15% rebate)** | | | **DEAD** |
| **FINAL with rebate 0** | | | **DEAD** |

## Queue depth, measured (calibration of Q)

For each taker print, `live_depth()` replays the recorded order books (unified Yes/No books). It measures the shares resting at the print price on the acquiring side 3 s later: the queue a new maker joins when it quotes at the last print. The game phase comes from Polymarket's own sports feed: MLB `Mid`/`End` periods are breaks, and soccer `HT` is halftime.

| Sample | Prints | Games | Median shares ahead | p25 / p75 | Share zero (level cleared) | Share <= 250 / <= 1,000 | Median touch size | Median spread (c) |
|---|---|---|---|---|---|---|---|---|
| MLB inning breaks (2026-09-18/19, 15 games) | 1,201 | 15 | 1,603 | 0 / 20,646 | 29% | 37% / 45% | 3,877 | 1.0 |
| MLB live play | 11,837 | 15 | 74 | 0 / 2,796 | 44% | 58% / 66% | 1,185 | 1.0 |
| MLB pregame / other | 1,327 | 15 | 144,573 | 80,010 / 179,939 | 1% | 1% / 1% | 144,573 | 1.0 |
| Soccer halftime, universe-like legs (2026-09-19) | 229 | 3 | 6,866 | 2,015 / 11,650 | 6% | 8% / 15% | 6,972 | 1.0 |
| Soccer live play, universe-like legs | 2,602 | 5 | 645 | 0 / 4,828 | 30% | 43% / 53% | 1,563 | 1.0 |
| Soccer halftime, all captured legs | 300 | 4 | 6,322 | 1,403 / 10,585 | 9% | 14% / 22% | 6,511 | 1.0 |
| Soccer live play, all captured legs | 3,614 | 6 | 360 | 0 / 3,713 | 32% | 48% / 58% | 1,111 | 1.0 |

**Halftime timing on 2026-09-19** (Polymarket sports feed; minutes from the scheduled KO): kor-bch-san-2026-09-19: HT signal at KO+50.0, first 2H message at KO+64.5; epl-tot-ast-2026-09-19: HT signal at KO+55.4, first 2H message at KO+70.9; elc-mil-whu-2026-09-19: HT signal at KO+51.3, first 2H message at KO+67.3; lal-osa-ray-2026-09-19: HT signal at KO+48.5, first 2H message at KO+64.5. The first 2H message carries `elapsed` 46-49, so the second half started 1-4 min earlier. Prints in that gap are classed as halftime, which biases the halftime depth *down*, i.e. in the strategy's favour. In Tottenham v Aston Villa the first half ran to about KO+55 with a goal at about KO+50, so [52, 55] was live play. The skip rule dropped the event (Yes-price span 0.32 in [49, 52)). Osasuna v Rayo had no two-sided print in [59, 61], so its legs were dropped for lack of a ref (the E9 issue). Only Millwall v West Ham passed the rule's filters.

**Depth at the last-print level by leg and phase** (universe-like legs; live = 1H/2H):

| Leg (volume at KO) | Phase | Prints | Median shares ahead | Share zero | p75 | Median touch size | Taker shares in phase |
|---|---|---|---|---|---|---|---|
| elc-mil-whu-2026-09-19-whu ($63,220) | halftime | 28 | 1,674 | 7% | 3,767 | 1,763 | 9,608 |
| elc-mil-whu-2026-09-19-whu ($63,220) | live | 244 | 60 | 42% | 1,787 | 391 | 49,598 |
| epl-tot-ast-2026-09-19-ast ($181,318) | halftime | 97 | 11,695 | 4% | 17,204 | 11,695 | 46,010 |
| epl-tot-ast-2026-09-19-ast ($181,318) | live | 1052 | 1,197 | 28% | 4,797 | 1,952 | 287,920 |
| epl-tot-ast-2026-09-19-tot ($417,095) | halftime | 69 | 4,601 | 10% | 8,080 | 4,699 | 15,303 |
| epl-tot-ast-2026-09-19-tot ($417,095) | live | 834 | 1,908 | 22% | 7,598 | 3,118 | 369,539 |
| lal-osa-ray-2026-09-19-osa ($70,109) | halftime | 22 | 7,749 | 0% | 9,723 | 7,749 | 1,425 |
| lal-osa-ray-2026-09-19-osa ($70,109) | live | 244 | 47 | 41% | 931 | 500 | 99,418 |
| lal-osa-ray-2026-09-19-ray ($337,757) | halftime | 13 | 7,231 | 0% | 9,517 | 7,231 | 15,125 |
| lal-osa-ray-2026-09-19-ray ($337,757) | live | 220 | 80 | 38% | 1,045 | 521 | 68,170 |
| sea-bol-tor-2026-09-19-draw ($73,014) | live | 3 | 0 | 100% | 0 | 1,061 | 140 |
| sea-udi-cag-2026-09-19-draw ($51,186) | live | 5 | 208 | 20% | 481 | 481 | 226 |

**Order flow at the touch.** For every share traded at the best ask (within 1 tick), the following shares are cancelled and added:

| Sample | Traded shares | Cancelled per traded | Added per traded |
|---|---|---|---|
| MLB: other | 890,982 | 46.3 | 36.1 |
| MLB: live | 3,584,363 | 87.0 | 91.6 |
| MLB: break | 944,860 | 55.7 | 41.6 |
| Soccer, universe-like legs: other | 2,217,760 | 32.4 | 34.4 |
| Soccer, universe-like legs: live | 868,287 | 29.7 | 33.1 |
| Soccer, universe-like legs: halftime | 87,470 | 21.4 | 21.1 |

Queues turn over mostly through cancellations. The tape queue model ignores cancellations ahead of us, so it understates how fast a real order moves up. Cancellations ahead of a resting order are also often informed makers pulling quotes just before informed flow arrives, so the Q = 0 rows are an upper bound, not the expectation.

**Live check on today's halftimes (report-only, small sample).** On the universe-like legs of the 2026-09-19 capture, the same [KO+52, KO+60] rule was run in two ways. First, the tape fill models were run on the live tape. Second, the rule was run against the recorded book (`book_sim()`), with our place in the queue tracked from the book itself. Trades at our price eat the queue ahead of us. Level decreases not matched by a trade within 1 s are cancellations, allocated pro rata, from behind us first, or ignored.

| Legs | Fill model (live tape / live book) | Fills | Shares | Share traded through | Markout c/sh (15% rebate) |
|---|---|---|---|---|---|
| rule filters (skip rule, KO+61 two-sided ref) | tape: front of queue (pre-registered fill) | 11 | 437 | 1% | +0.30 |
| rule filters (skip rule, KO+61 two-sided ref) | tape: (b) back-of-queue | 0 | 0 | - | - |
| rule filters (skip rule, KO+61 two-sided ref) | tape: queue Q=0 | 11 | 405 | 1% | +0.28 |
| rule filters (skip rule, KO+61 two-sided ref) | tape: queue Q ~ measured depth | 2 | 55 | 9% | +0.45 |
| rule filters (skip rule, KO+61 two-sided ref) | tape: queue Q=inf | 1 | 5 | 100% | -0.46 |
| rule filters (skip rule, KO+61 two-sided ref) | book sim: cancels proportional | 2 | 55 | 9% | -0.46 |
| rule filters (skip rule, KO+61 two-sided ref) | book sim: cancels behind_first | 2 | 55 | 9% | -0.46 |
| rule filters (skip rule, KO+61 two-sided ref) | book sim: cancels none | 2 | 55 | 9% | -0.46 |
| all universe-like legs (no skip, as-of ref) | tape: front of queue (pre-registered fill) | 139 | 3,973 | 4% | +0.49 |
| all universe-like legs (no skip, as-of ref) | tape: (b) back-of-queue | 10 | 272 | 41% | -0.30 |
| all universe-like legs (no skip, as-of ref) | tape: queue Q=0 | 130 | 3,413 | 4% | +0.52 |
| all universe-like legs (no skip, as-of ref) | tape: queue Q ~ measured depth | 25 | 640 | 28% | +0.33 |
| all universe-like legs (no skip, as-of ref) | tape: queue Q=inf | 10 | 177 | 100% | -0.18 |
| all universe-like legs (no skip, as-of ref) | book sim: cancels proportional | 14 | 432 | 29% | +0.19 |
| all universe-like legs (no skip, as-of ref) | book sim: cancels behind_first | 14 | 477 | 27% | +0.23 |
| all universe-like legs (no skip, as-of ref) | book sim: cancels none | 11 | 327 | 39% | +0.20 |

Only the three games whose halftime was captured before the final run contribute fills: Millwall v West Ham, Tottenham v Aston Villa and Osasuna v Rayo. The markouts come from a handful of games and are not evidence. The fill *volumes* are the point. With the queue tracked from the real book, a newcomer is filled on 327-477 shares, depending on how cancellations are allocated. That is 8-12% of the front-of-queue fill (3,973), and fewer than the tape queue model with Q drawn from the measured depth (640). The realistic tape model is therefore not too pessimistic.

## Queue model: shares ahead of us (Q grid)

| Variant | DEV 2025 | DEV 2026H1 | DEV pooled c/sh [CI] | DEV ROI [CI] | HOLDOUT c/sh [CI] | HOLDOUT ROI [CI] | HOLDOUT events / fills | HOLDOUT P&L $ |
|---|---|---|---|---|---|---|---|---|
| Q=0 shares ahead | +0.370 | +0.375 | +0.374 [+0.347, +0.399] | +0.71% [+0.66, +0.75] | +0.188 [-0.028, +0.331] | +0.36% [-0.05, +0.63] | 288 / 11,000 | +504 |
| Q=100 shares ahead | +0.301 | +0.299 | +0.299 [+0.261, +0.334] | +0.56% [+0.48, +0.62] | +0.150 [-0.026, +0.280] | +0.28% [-0.05, +0.53] | 256 / 5,991 | +314 |
| Q=250 shares ahead | +0.249 | +0.259 | +0.258 [+0.212, +0.300] | +0.48% [+0.39, +0.56] | +0.104 [-0.066, +0.233] | +0.19% [-0.12, +0.43] | 250 / 4,711 | +182 |
| Q=500 shares ahead | +0.172 | +0.205 | +0.203 [+0.149, +0.251] | +0.37% [+0.27, +0.46] | +0.052 [-0.160, +0.209] | +0.10% [-0.30, +0.39] | 243 / 3,828 | +75 |
| Q=1000 shares ahead | +0.138 | +0.144 | +0.144 [+0.078, +0.200] | +0.26% [+0.14, +0.37] | +0.001 [-0.224, +0.155] | +0.00% [-0.41, +0.29] | 227 / 3,069 | +1 |
| Q=2000 shares ahead | +0.130 | +0.068 | +0.072 [-0.007, +0.140] | +0.13% [-0.01, +0.26] | -0.088 [-0.323, +0.094] | -0.17% [-0.61, +0.18] | 207 / 2,447 | -79 |
| Q=5000 shares ahead | +0.116 | -0.028 | -0.017 [-0.109, +0.061] | -0.03% [-0.20, +0.11] | -0.231 [-0.516, -0.014] | -0.44% [-0.99, -0.03] | 191 / 1,891 | -151 |
| Q=20000 shares ahead | +0.102 | -0.116 | -0.098 [-0.209, -0.001] | -0.18% [-0.38, -0.00] | -0.445 [-0.814, -0.173] | -0.82% [-1.51, -0.32] | 181 / 1,532 | -213 |
| Q=inf shares ahead | +0.116 | -0.147 | -0.124 [-0.246, -0.018] | -0.22% [-0.44, -0.03] | -0.598 [-1.036, -0.261] | -1.08% [-1.87, -0.47] | 176 / 1,371 | -237 |

In the holdout the break-even queue is about 1,000 shares; in DEV it is about 4,000. The measured distributions put a newcomer at the front (Q = 0) 29% of the time at MLB breaks (6% at soccer halftime), and far behind the rest of the time.

## Execution and cost sensitivity

| Variant | DEV 2025 | DEV 2026H1 | DEV pooled c/sh [CI] | DEV ROI [CI] | HOLDOUT c/sh [CI] | HOLDOUT ROI [CI] | HOLDOUT events / fills | HOLDOUT P&L $ |
|---|---|---|---|---|---|---|---|---|
| -1c worse maker price (shade) | -0.627 | -0.669 | -0.666 [-0.710, -0.628] | -1.24% [-1.33, -1.17] | -0.911 [-1.138, -0.748] | -1.73% [-2.15, -1.42] | 288 / 10,816 | -2,676 |
| (b) back-of-queue stress, -1c worse price | -1.097 | -1.582 | -1.557 [-1.893, -1.278] | -2.78% [-3.37, -2.26] | -2.062 [-2.878, -1.488] | -3.72% [-5.20, -2.67] | 125 / 1,945 | -1,249 |
| primary, quote delay 5 s | +0.365 | +0.313 | +0.317 [+0.262, +0.362] | +0.60% [+0.50, +0.69] | +0.102 [-0.114, +0.263] | +0.20% [-0.22, +0.51] | 288 / 10,644 | +295 |
| primary, quote delay 10 s | +0.366 | +0.294 | +0.299 [+0.238, +0.351] | +0.57% [+0.45, +0.67] | +0.045 [-0.195, +0.229] | +0.09% [-0.38, +0.45] | 288 / 10,468 | +128 |
| primary, quote delay 20 s | +0.360 | +0.273 | +0.280 [+0.207, +0.340] | +0.53% [+0.39, +0.65] | +0.049 [-0.167, +0.222] | +0.09% [-0.33, +0.43] | 286 / 10,164 | +135 |
| primary, quotes rounded up to the tick grid | +0.378 | +0.342 | +0.345 [+0.297, +0.386] | +0.66% [+0.57, +0.74] | +0.086 [-0.134, +0.262] | +0.16% [-0.26, +0.50] | 285 / 9,824 | +228 |
| primary, same-second prints = one taker event | +0.379 | +0.369 | +0.370 [+0.339, +0.398] | +0.70% [+0.64, +0.76] | +0.181 [-0.024, +0.319] | +0.35% [-0.05, +0.62] | 288 / 9,619 | +516 |
| primary, rebate 0 | +0.373 | +0.289 | +0.295 [+0.251, +0.333] | +0.56% [+0.48, +0.63] | -0.019 [-0.246, +0.145] | -0.04% [-0.48, +0.28] | 288 / 10,816 | -57 |
| (b) back-of-queue, rebate 0 | -0.097 | -0.619 | -0.592 [-0.928, -0.313] | -1.07% [-1.68, -0.56] | -1.165 [-1.993, -0.585] | -2.14% [-3.67, -1.06] | 125 / 1,945 | -706 |
| REALISTIC queue (Q~measured), rebate 0 | +0.169 | +0.055 | +0.063 [-0.012, +0.128] | +0.11% [-0.02, +0.23] | -0.228 [-0.529, -0.010] | -0.41% [-0.96, -0.02] | 222 / 2,555 | -190 |
| REALISTIC queue, draw seed 1 | +0.166 | +0.097 | +0.102 [+0.028, +0.169] | +0.18% [+0.05, +0.30] | -0.174 [-0.528, +0.066] | -0.32% [-0.95, +0.12] | 218 / 2,603 | -145 |
| REALISTIC queue, draw seed 2 | +0.193 | +0.077 | +0.086 [+0.007, +0.156] | +0.15% [+0.01, +0.28] | -0.129 [-0.395, +0.059] | -0.24% [-0.73, +0.11] | 216 / 2,583 | -105 |
| REALISTIC queue, draw seed 3 | +0.173 | +0.105 | +0.109 [+0.034, +0.176] | +0.20% [+0.06, +0.32] | -0.029 [-0.261, +0.184] | -0.05% [-0.48, +0.34] | 222 / 2,515 | -24 |
| REALISTIC queue, draw seed 4 | +0.152 | +0.081 | +0.086 [+0.007, +0.153] | +0.15% [+0.01, +0.28] | -0.151 [-0.442, +0.069] | -0.27% [-0.81, +0.12] | 223 / 2,550 | -125 |
| REALISTIC queue, trade-through fills the whole remaining order | +0.097 | +0.020 | +0.026 [-0.064, +0.107] | +0.05% [-0.12, +0.20] | -0.263 [-0.511, -0.048] | -0.49% [-0.95, -0.09] | 223 / 2,385 | -265 |
| queue Q=0, trade-through fills the whole remaining order | +0.346 | +0.343 | +0.343 [+0.313, +0.372] | +0.65% [+0.60, +0.71] | +0.125 [-0.077, +0.268] | +0.24% [-0.15, +0.52] | 288 / 10,754 | +350 |
| queue Q=0 + tick grid + same-second agg + 5 s delay | +0.369 | +0.386 | +0.385 [+0.360, +0.408] | +0.73% [+0.68, +0.77] | +0.233 [+0.048, +0.365] | +0.44% [+0.09, +0.69] | 285 / 8,701 | +527 |
| queue Q~measured + tick grid + same-second agg + 5 s delay | +0.028 | -0.008 | -0.006 [-0.100, +0.076] | -0.01% [-0.18, +0.14] | -0.279 [-0.609, -0.012] | -0.52% [-1.14, -0.02] | 204 / 1,570 | -160 |
| primary, as-of ref (no ref-based leg drop) | +0.380 | +0.329 | +0.334 [+0.294, +0.371] | +0.63% [+0.55, +0.70] | +0.122 [-0.032, +0.250] | +0.23% [-0.06, +0.48] | 687 / 15,335 | +491 |
| (b) back-of-queue, as-of ref | -0.099 | -0.610 | -0.579 [-0.903, -0.315] | -1.05% [-1.67, -0.57] | -1.063 [-1.715, -0.542] | -2.01% [-3.26, -1.04] | 231 / 2,443 | -801 |
| REALISTIC queue (Q~measured), as-of ref | +0.196 | +0.069 | +0.080 [+0.010, +0.143] | +0.14% [+0.02, +0.25] | -0.148 [-0.354, +0.021] | -0.27% [-0.66, +0.04] | 456 / 3,388 | -158 |

## Robustness (review block)

| Check | Front of queue DEV | Front of queue HOLDOUT | Back-of-queue HOLDOUT | Realistic queue DEV | Realistic queue HOLDOUT |
|---|---|---|---|---|---|
| ROI, cluster by event | +0.64% [+0.55, +0.71] | +0.17% [-0.26, +0.49] | -1.95% [-3.47, -0.88] | +0.18% [+0.04, +0.29] | -0.23% [-0.77, +0.16] |
| ROI, cluster by KO date | +0.64% [+0.55, +0.71] | +0.17% [-0.27, +0.52] | -1.95% [-3.45, -0.93] | +0.18% [+0.02, +0.30] | -0.23% [-0.82, +0.17] |
| ROI, cluster by league-date | +0.64% [+0.54, +0.72] | +0.17% [-0.30, +0.52] | -1.95% [-3.54, -0.86] | +0.18% [+0.02, +0.30] | -0.23% [-0.82, +0.17] |
| ROI, cluster by league | +0.64% [+0.40, +0.74] | +0.17% [-1.18, +0.64] | -1.95% [-6.24, -0.48] | +0.18% [-0.28, +0.35] | -0.23% [-2.07, +0.31] |
| Equal-weight per-event ROI | +0.77% [+0.59, +0.95] | +0.39% [+0.02, +0.74] | -2.10% [-3.11, -1.15] | +0.18% [-0.08, +0.43] | -0.36% [-0.91, +0.13] |
| Drop top 1% events | +0.60% (12 ev) | +0.11% (3 ev) | -2.15% (2 ev) | +0.12% (10 ev) | -0.35% (3 ev) |
| Drop bottom 1% events | +0.75% | +0.43% | -1.25% | +0.34% | +0.08% |
| Trim 1% both tails | +0.73% | +0.38% | -1.40% | +0.30% | -0.02% |
| Top-1% events' share of P&L | +0.10 | +0.41 | -0.02 | +0.35 | -0.39 |
| Top events removed to flip sign | 415 | 9 | already <= 0 | 45 | already <= 0 |
| Share of events with P&L > 0 | 91% | 82% | 30% | 72% | 61% |
| Rebate 0% | +0.56% [+0.48, +0.63] | -0.04% [-0.48, +0.28] | -2.14% [-3.67, -1.06] | +0.11% [-0.02, +0.23] | -0.41% [-0.96, -0.02] |
| Rebate 5% | +0.59% [+0.50, +0.66] | +0.03% [-0.41, +0.35] | -2.08% [-3.60, -1.00] | +0.13% [+0.00, +0.25] | -0.35% [-0.89, +0.04] |
| Rebate 10% | +0.61% [+0.53, +0.68] | +0.10% [-0.34, +0.42] | -2.01% [-3.53, -0.94] | +0.16% [+0.02, +0.27] | -0.29% [-0.83, +0.10] |
| Rebate 15% | +0.64% [+0.55, +0.71] | +0.17% [-0.26, +0.49] | -1.95% [-3.47, -0.88] | +0.18% [+0.04, +0.29] | -0.23% [-0.77, +0.16] |
| Rebate 20% | +0.66% [+0.57, +0.73] | +0.24% [-0.19, +0.56] | -1.89% [-3.41, -0.83] | +0.20% [+0.06, +0.32] | -0.17% [-0.72, +0.22] |
| Fills at q == a: share / ROI | 89% / +0.78% [+0.71, +0.84] | 88% / +0.51% [+0.15, +0.78] | 52% / -1.00% [-2.55, -0.13] | 43% / +0.71% [+0.62, +0.80] | 56% / +0.49% [-0.01, +0.84] |
| Fills at q > a (traded through): share / ROI | 11% / -0.46% [-0.85, -0.14] | 12% / -2.21% [-3.56, -1.14] | 48% / -2.99% [-4.80, -1.51] | 57% / -0.23% [-0.46, -0.04] | 44% / -1.18% [-2.06, -0.52] |
| Fills at q > a: c/sh | -0.247 [-0.454, -0.073] | -1.204 [-1.951, -0.615] | -1.617 [-2.657, -0.808] | -0.126 [-0.253, -0.020] | -0.646 [-1.125, -0.290] |
| Excluding World Cup | +0.60% [+0.49, +0.69] | -0.19% [-0.87, +0.32] | -4.53% [-7.10, -2.24] | +0.06% [-0.13, +0.22] | -1.11% [-2.20, -0.27] |
| Excluding US/MX (post-hoc) | +0.69% [+0.63, +0.75] | +0.59% [+0.38, +0.75] | -0.75% [-1.71, -0.18] | +0.27% [+0.17, +0.36] | +0.22% [-0.04, +0.42] |
| Leave-one-league-out range | +0.60% .. +0.69% | -0.19% .. +0.44% | -4.53% .. -1.27% | +0.06% .. +0.26% | -1.11% .. -0.00% |
| Months (ROI) | 25-11 +0.80%; 25-12 +0.70%; 26-01 +0.62%; 26-02 +0.52%; 26-03 +0.90%; 26-04 +0.75%; 26-05 +0.34%; 26-06 +0.74% | 26-07 +0.49%; 26-08 -0.67%; 26-09 +0.57% | 26-07 -0.65%; 26-08 -5.76%; 26-09 -1.90% | 25-11 +0.53%; 25-12 +0.28%; 26-01 +0.21%; 26-02 -0.07%; 26-03 +0.49%; 26-04 +0.06%; 26-05 -0.23%; 26-06 +0.39% | 26-07 +0.31%; 26-08 -1.78%; 26-09 -0.19% |
| Fee-rate mix of filled shares | 0.000: 45%, 0.018: 0%, 0.030: 55% | 0.030: 38%, 0.050: 62% | 0.030: 38%, 0.050: 62% | 0.000: 42%, 0.018: 0%, 0.030: 58% | 0.030: 42%, 0.050: 58% |

## All variants tried (c/share; cluster CI for DEV pooled and HOLDOUT)

| Variant | DEV 2025 | DEV 2026H1 | DEV pooled c/sh [CI] | DEV ROI [CI] | HOLDOUT c/sh [CI] | HOLDOUT ROI [CI] | HOLDOUT events / fills | HOLDOUT P&L $ |
|---|---|---|---|---|---|---|---|---|
| (a) K=200 | +0.381 | +0.305 | +0.312 [+0.260, +0.359] | +0.60% [+0.50, +0.69] | +0.103 [-0.082, +0.246] | +0.20% [-0.16, +0.48] | 288 / 6,883 | +437 |
| (c) 25% of each fill | +0.321 | +0.304 | +0.306 [+0.258, +0.350] | +0.57% [+0.48, +0.65] | +0.152 [-0.064, +0.303] | +0.29% [-0.12, +0.57] | 288 / 13,816 | +348 |
| (d) hold to resolution | +2.729 | +0.840 | +0.985 [+0.265, +1.708] | +1.87% [+0.50, +3.27] | +1.429 [-0.301, +3.149] | +2.77% [-0.59, +6.11] | 288 / 10,816 | +4,198 |
| (d2) hold to resolution, all non-skipped legs (no ref-based drop) | +0.739 | +0.948 | +0.928 [+0.194, +1.640] | +1.75% [+0.37, +3.10] | +1.330 [-0.154, +2.850] | +2.56% [-0.30, +5.48] | 687 / 15,335 | +5,372 |
| (b)+(d2) back-of-queue, hold, no ref-based drop | +3.783 | +1.589 | +1.720 [-0.951, +4.365] | +3.13% [-1.71, +8.03] | +2.952 [-1.452, +7.898] | +5.59% [-2.78, +14.77] | 231 / 2,443 | +2,223 |
| no skip rule (diagnostic) | +0.368 | +0.267 | +0.274 [+0.207, +0.337] | +0.53% [+0.40, +0.65] | -0.050 [-0.277, +0.138] | -0.10% [-0.54, +0.27] | 357 / 14,212 | -193 |
| robust: quote tie = last row in data order | +0.372 | +0.325 | +0.329 [+0.282, +0.369] | +0.63% [+0.54, +0.70] | +0.079 [-0.148, +0.247] | +0.15% [-0.29, +0.48] | 288 / 10,840 | +232 |
| robust: back-of-queue, strictly later prints only (ts > t) | -0.140 | -0.650 | -0.625 [-1.020, -0.313] | -1.14% [-1.85, -0.57] | -1.197 [-2.188, -0.588] | -2.17% [-3.95, -1.08] | 120 / 1,793 | -672 |
| robust: back-of-queue strict + last-row tie | -0.137 | -0.673 | -0.647 [-1.035, -0.321] | -1.18% [-1.89, -0.58] | -1.216 [-2.192, -0.593] | -2.21% [-3.98, -1.08] | 120 / 1,861 | -696 |
| robust: (c) 25% of each fill, no K cap | +0.337 | +0.270 | +0.276 [+0.210, +0.332] | +0.53% [+0.40, +0.64] | +0.068 [-0.133, +0.221] | +0.13% [-0.25, +0.42] | 288 / 7,403 | +253 |
| (e) ctrl_1H_[20,30] | -0.997 | -0.344 | -0.401 [-0.535, -0.271] | -0.79% [-1.05, -0.53] | -1.135 [-1.507, -0.818] | -2.24% [-2.97, -1.60] | 399 / 18,549 | -5,595 |
| (e) ctrl_1H_[20,30] back-of-queue | -2.531 | -2.172 | -2.194 [-2.505, -1.883] | -4.35% [-4.98, -3.74] | -2.927 [-3.633, -2.313] | -5.89% [-7.31, -4.65] | 339 / 8,102 | -6,999 |
| (e) ctrl_2H_[70,80] | -0.509 | -0.439 | -0.444 [-0.598, -0.290] | -0.91% [-1.22, -0.59] | -0.878 [-1.267, -0.509] | -1.81% [-2.61, -1.05] | 466 / 20,645 | -4,926 |
| (e) ctrl_2H_[70,80] back-of-queue | -2.806 | -2.202 | -2.234 [-2.572, -1.908] | -4.72% [-5.45, -4.02] | -2.563 [-3.241, -1.969] | -5.22% [-6.59, -4.01] | 389 / 10,025 | -7,712 |
| (e) ctrl_1H_[20,30] REALISTIC queue (Q~measured, halftime calibration) | -1.541 | -1.040 | -1.081 [-1.275, -0.888] | -2.11% [-2.49, -1.73] | -1.257 [-1.592, -0.939] | -2.49% [-3.15, -1.86] | 393 / 7,904 | -2,757 |
| (e) ctrl_2H_[70,80] REALISTIC queue (Q~measured, halftime calibration) | -1.913 | -1.051 | -1.111 [-1.332, -0.906] | -2.30% [-2.75, -1.87] | -1.192 [-1.564, -0.816] | -2.44% [-3.22, -1.67] | 448 / 9,890 | -3,344 |

Hold to resolution (d/d2) is above the markout because the tokens takers buy at halftime resolved below their KO+61 mid: E[y - ref] is -0.5 to -2.4c, driven by Yes buys. That is a separate, noisy favorite-longshot-type effect, and every holdout CI includes 0.

The in-play control windows are strongly negative under every fill model. Halftime beats them by 0.7-1.9c/share, depending on the fill model, so the relative blackout effect is robust. The absolute edge after adverse selection, for a maker who is not first in the queue, is what fails.

## (f) Basketball and hockey intermissions

The window was fitted on DEV 2025 only. For each game I took minute-VWAP token-0 prices and chose the 8-minute window with the lowest sum of |Δp| per $ volume. The start was searched in [KO+30, KO+100]; the unrestricted search [30, 170] picked game ends and is reported as a diagnostic. There is no skip rule. The realistic queue uses the same measured distribution as soccer.

| League | Fitted window (fit games) | Model | DEV 2025 c/sh [CI] (ev) | DEV 2026H1 c/sh [CI] (ev) | HOLDOUT c/sh [CI] (ev) |
|---|---|---|---|---|---|
| NBA | KO+[72, 80]  (1317) | base | -0.020 [-0.138, +0.092] (871) | +0.106 [+0.034, +0.172] (641) | - |
| NBA | KO+[72, 80]  (1317) | back-of-queue | -1.202 [-1.549, -0.876] (579) | -1.053 [-1.274, -0.826] (548) | - |
| NBA | KO+[72, 80]  (1317) | queue Q~measured | -0.552 [-0.774, -0.361] (829) | -0.464 [-0.643, -0.310] (625) | - |
| NBA | KO+[72, 80]  (1317) | hold | +0.027 [-0.938, +1.047] (871) | +0.285 [-0.252, +0.846] (641) | - |
| NBA | KO+[72, 80]  (1317) | hold, no ref-based drop | +0.027 [-0.959, +0.993] (1274) | +0.280 [-0.273, +0.785] (667) | - |
| NBA | KO+[162, 170] (unrestricted diagnostic) (1317) | base | -1.709 [-2.528, -0.864] (71) | -0.431 [-0.949, +0.035] (37) | - |
| WNBA | KO+[61, 69]  (32) | base | -2.432 [-7.290, +2.671] (5) | -0.469 [-1.631, +0.385] (34) | -0.316 [-1.049, +0.450] (61) |
| WNBA | KO+[61, 69]  (32) | back-of-queue | -6.715 [+nan, +nan] (2) | -3.001 [-5.482, -1.545] (27) | -1.708 [-3.808, +0.337] (35) |
| WNBA | KO+[61, 69]  (32) | queue Q~measured | -1.030 [-6.911, +5.190] (5) | -0.885 [-2.137, +0.176] (33) | -1.178 [-2.187, +0.081] (51) |
| WNBA | KO+[61, 69]  (32) | hold | -3.073 [-40.333, +22.631] (5) | -3.790 [-10.275, +1.102] (34) | +1.126 [-2.395, +5.301] (61) |
| WNBA | KO+[61, 69]  (32) | hold, no ref-based drop | -9.638 [-21.131, +3.241] (21) | -0.880 [-6.383, +4.224] (57) | +1.645 [-2.052, +5.642] (88) |
| WNBA | KO+[150, 158] (unrestricted diagnostic) (32) | base | -6.283 [+nan, +nan] (1) | -3.020 [+nan, +nan] (1) | - |
| NHL | KO+[56, 64]  (917) | base | -0.098 [-0.522, +0.319] (281) | -0.007 [-0.246, +0.230] (455) | - |
| NHL | KO+[56, 64]  (917) | back-of-queue | -2.274 [-3.814, -0.884] (122) | -1.605 [-2.454, -0.818] (300) | - |
| NHL | KO+[56, 64]  (917) | queue Q~measured | -0.664 [-1.212, -0.188] (233) | -0.725 [-1.129, -0.324] (409) | - |
| NHL | KO+[56, 64]  (917) | hold | +1.235 [-2.515, +5.173] (281) | -1.573 [-3.359, +0.140] (455) | - |
| NHL | KO+[56, 64]  (917) | hold, no ref-based drop | +2.062 [-0.975, +5.176] (673) | -1.640 [-3.228, -0.004] (601) | - |
| NHL | KO+[112, 120] (unrestricted diagnostic) (917) | base | -0.303 [-0.880, +0.188] (339) | -0.055 [-0.269, +0.140] (477) | - |

The effect does not carry over to basketball or hockey. At best it is +0.1c in NBA DEV 2026H1 with front-of-queue fills, and every league is negative under the back-of-queue and realistic-queue models.

## Per-league and per-leg split (front-of-queue primary)

| Group | DEV pooled c/sh [CI] (events) | HOLDOUT c/sh [CI] (events) |
|---|---|---|
| Draw legs | +0.422 [+0.367, +0.472] (163) | +0.361 [+0.268, +0.448] (35) |
| Team legs | +0.323 [+0.273, +0.366] (1,117) | +0.041 [-0.197, +0.230] (287) |
| Premier League | +0.398 [+0.366, +0.428] (178) | +0.481 [+0.403, +0.552] (28) |
| Serie A | +0.432 (131) | +0.440 (19) |
| UCL | +0.371 (76) | +0.368 (33) |
| World Cup | +0.386 (53) | +0.344 (22) |
| Ligue 1 | +0.382 (74) | +0.687 (13) |
| UEL | +0.387 (50) | +0.341 (12) |
| La Liga | +0.395 (127) | -0.171 [-1.29, +0.38] (25) |
| Bundesliga | +0.073 (87) | +0.316 (9) |
| MLS | **-1.652** [-3.05, -0.26] (37) | **-2.816** [-4.68, -1.16] (23) |

### Post-hoc diagnostic (not used for the verdict)

For US/Mexico-scheduled competitions (MLS, Liga MX, Leagues Cup, CONCACAF), the scheduled `game_start_ts` is about 10 minutes before the real kickoff. The fixed [52, 60] window therefore trades live first-half play there. Excluding these leagues after the fact: front of queue HOLDOUT +0.59% [+0.38, +0.75], realistic queue HOLDOUT +0.22% [-0.04, +0.42], back-of-queue HOLDOUT -0.75% [-1.71, -0.18].

This is the one lead, and it is weak. A rule that places the window per league from the DEV volatility trough, or better, from the live sports-feed `HT` signal, is identifiable before the match and could be pre-registered. But it was found on the holdout, so it needs data after 2026-09-19. Under realistic fills it is +0.22% with a CI crossing 0, and that depends on the rebate. Even the front-of-queue edge in the big European leagues is about 0.4c/share, a few dollars per game.

What could change the answer is being the incumbent. That means resting at the price level *before* the halftime flow arrives (posting at the HT signal and holding), not joining after each print. Testing that needs order-book history, which only a live capture provides. `--record` plus `book_sim()` is the harness for a paper test on future match days.

## Capacity

| Model | Period | Days | Events | Capital $/day | Expected P&L $/day | P&L $/month | Median shares / event | Our share of window taker shares |
|---|---|---|---|---|---|---|---|---|
| Front of queue | dev26h1 | 180 | 981 | 3,355 | +21.14 | +634 | 655 | 2.9% |
| Front of queue | hold | 80 | 288 | 1,901 | +3.30 | +99 | 440 | 1.5% |
| Back-of-queue | dev26h1 | 180 | 533 | 462 | -4.90 | -147 | 100 | 0.4% |
| Back-of-queue | hold | 79 | 125 | 419 | -8.16 | -245 | 150 | 0.3% |
| Realistic queue | dev26h1 | 180 | 845 | 775 | +1.30 | +39 | 133 | 0.6% |
| Realistic queue | hold | 80 | 222 | 576 | -1.35 | -40 | 137 | 0.4% |

Front of queue, we take 1.5-3% of the window's taker shares; with a realistic queue, 0.4-0.6%. The book is not the constraint. The constraints are queue position and an edge of a few tenths of a cent per share. Even the unreachable front-of-queue DEV rate is about $630/month on $3.4k/day of capital turnover. The realistic model loses about $40/month in the holdout.

## Checks run

- **Reproduction.** With `px_tol=1e-9` the script reproduces the originally reported numbers exactly: DEV +0.324c; holdout +0.056c, ROI +0.11%, P&L $149; back-of-queue holdout -0.447c.
- **Queue model vs the reviewer's reference.** With the reviewer's semantics patched in, the fill totals and P&L match the reviewer's `sim(model='queue')` to the cent at Q = 0, 250 and inf. The two deliberate differences are the consistent 1e-6 tolerance and trade-through fills capped at print size; the latter is also reported the reviewer's way.
- **Brute force (earlier round).** A row-by-row re-implementation on 300 DEV events matched the vectorized base and back-of-queue simulator exactly.
- **Live depth replay.** At MLB breaks, 99% of zero-depth prints have best ask > print price, meaning the level was genuinely cleared. On deep pregame books, `book_sim()` without cancellations fills about like the tape Q = inf model (18 vs 15 fills, both nearly all trade-throughs), as it should.
- **Seed sensitivity** of the random Q draws (5 seeds): see Execution and cost sensitivity.
- **Payout orientation.** Share-weighted E[y_s - ref_s] is -2.4 / -0.5 / -1.3c (DEV25 / DEV26H1 / HOLDOUT), and corr(q, ref_s) >= 0.988. A flipped orientation would give about ±30c.
- **Fees.** Rebates use each print's own `fee_rate`. Makers pay no fee.
- **Timing.** Every fill has rel in [52, 60] min (asserted). Quotes use only prints at <= t-3 s. The skip decision uses only prints before KO+52.

## Holdout discipline

- **The holdout was partly seen before pre-registration (review S1).** The feasibility script (`explore20.py`, session scratchpad) pooled every fee-paying 2026 fill as "2026" with no 2026-07-01 cutoff, so 34% of its 2026 window shares were holdout. The pre-registration quoted "+0.10c in 2026 (CI > 0)". Mar-Jun alone was +0.094c [-0.006, +0.186]; the holdout part was +0.102c [+0.017, +0.179]. The KO+[50, 60] volatility profile used to place the window also used all periods. The holdout was therefore not virgin, which biases it toward a positive result, and the rule failed anyway. Future feasibility SQL should filter `mk.game_start_ts < 1782864000`.
- DEV runs drop every row with KO >= 2026-07-01 at load time. The primary rule's parameters were never changed.
- This round re-ran the holdout with: bug fixes (N1) applied to the unchanged rule; realistic-execution models the reviewers required; and a queue-depth calibration fixed in code before the soccer data existed. Nothing was selected on holdout results.

## Caveats

1. **Queue model assumptions.** The model has one order of 50 shares, Q drawn independently of the tape at each (re)join, no cancellations ahead of us (conservative), and no competing joiners at the same moment (optimistic). The calibration is soccer halftime live capture 2026-09-19 (229 prints, 5 legs, 3 games: elc-mil-whu-2026-09-19, epl-tot-ast-2026-09-19, lal-osa-ray-2026-09-19): one Saturday, three games, 229 prints. The occasional empty level (6% of draws) is why the distribution row beats the median-Q row. The shallower MLB-break calibration gives a holdout of +0.16% [-0.17, +0.44] (`results_full_mlbQ.json`), still <= 0 without the rebate. On today's games, a simulation against the real book (queue tracked from the book, cancellations included) filled fewer shares than the tape model with measured Q. So the realistic model is not too pessimistic overall.
2. **The markout is not realized P&L.** Inventory must be held to resolution (noisy) or flattened (the 5% x p(1-p) taker fee plus half the spread exceeds the edge).
3. **The rebate is an approximation.** It is modeled as 15% of the taker fee on our own fills. The real rebate is a pool paid by Polymarket's formula. Every realistic result is <= 0 without it.
4. **Scheduled KO.** The fixed clock breaks where the listed start differs from the real kickoff (US/MX). The skip rule is only a partial guard.
5. **Multiple testing.** This round adds many execution and robustness rows. The verdict uses only the pre-registered primary and gate, plus the pre-committed realistic queue model.

## Reproduce

```
PYTHONPATH=. systemd-run --user --scope -q -p MemoryMax=4G -p MemorySwapMax=0 .venv/bin/python -m pmsports.research.h_halftime_intermission_mm --live-depth mlb     # MLB break depth (~5 min)
PYTHONPATH=. systemd-run --user --scope -q -p MemoryMax=4G -p MemorySwapMax=0 .venv/bin/python -m pmsports.research.h_halftime_intermission_mm --live-depth soccer  # soccer halftime depth
PYTHONPATH=. systemd-run --user --scope -q -p MemoryMax=4G -p MemorySwapMax=0 .venv/bin/python -m pmsports.research.h_halftime_intermission_mm            # DEV only
PYTHONPATH=. systemd-run --user --scope -q -p MemoryMax=4G -p MemorySwapMax=0 .venv/bin/python -m pmsports.research.h_halftime_intermission_mm --holdout  # full (~80 s)
# live capture (read-only websockets; written to data/research/h_halftime_intermission_mm/live/<date>/):
PYTHONPATH=. systemd-run --user --scope -q -p MemoryMax=1G -p MemorySwapMax=0 .venv/bin/python -m pmsports.research.h_halftime_intermission_mm --record HOURS
```
