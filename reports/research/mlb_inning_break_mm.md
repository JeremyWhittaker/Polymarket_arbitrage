# MLB half-inning break market making

**Verdict: DEAD for a maker who has to queue at the touch.** This revises the earlier PROMISING.
The revision follows from the review below.

The first version scored a fill model that puts us at the front of the queue on every taker fill.
The protocol requires makers to assume realistic queue limits, so the verdict now comes from a
FIFO queue model calibrated on the recorded order books.

That model has three parts:
- The queue ahead of us is drawn from the live touch sizes.
- Cancellations at our level come from ahead of us in proportion to the queue ahead.
- The cancellation rate is drawn from the same live window as the queue.

Under that model the result is:
- **DEV: -0.086 c/share [-0.143, -0.036]**, significantly negative. DEV 2025 is -0.245 and
  DEV 2026H1 is -0.003.
- **HOLDOUT: +0.099 c/share [+0.057, +0.137]**, which is +0.20% on capital.

The holdout gain is smaller than the modelled maker rebate of 0.119 c/share. Excluding the rebate,
the spread captured is negative in every period: -0.245c, -0.080c and -0.020c.

Four more results point the same way:
- Hold-to-resolution in the holdout is -0.51c [-2.39, +1.37].
- A fill 1c worse gives -0.90c.
- The pre-registered back-of-queue gate (b) fails, at -0.444c [-0.545, -0.354].
- Holdout P&L is **+$300 over 77 days**.

The DEV CI lies entirely below 0 and the holdout CI entirely above it. So the result is neither
PROFITABLE (the DEV lower bound would have to be > 0) nor PROMISING (a CI would have to cross 0).
Under the protocol's definitions it is **DEAD**. This rule was written into the code before the
holdout re-run.

The half-spread earned during breaks is real, but it goes to incumbents with time priority. The
only fill-model assumption under which a newcomer makes money counts every cancellation at the
level as ahead of us. Under it the holdout is +0.257c and P&L is $1.1k over 77 days, and the gate
(b) still caps it at PROMISING.

Code: `pmsports/research/h_mlb_inning_break_mm.py`. Cache and results are in
`data/research/h_mlb_inning_break_mm/`:
- `results.json` and `results_dev.json`
- `fills_primary.parquet` (front-of-queue primary)
- `fills_emp_prop.parquet` (the verdict model)
- `fills_<variant>.parquet`
- `live_check.json` and `live_check_windows.parquet`

## Review fixes

Two reviews (execution and statistics) raised the issues below. Each row states the issue, what
changed and the effect. All fixes were built and checked with `--dev-only`. The verdict model and
the verdict rule were fixed in code before the single full re-run that includes the holdout.

| # | Issue raised | What changed | Effect |
|---|---|---|---|
| 1 | **Front-of-queue fill model** (execution and statistics). The primary gave us min(size, 50) of every touching taker fill, but a newcomer joins the back of a queue of about 21k shares on a one-tick book. | Accepted. `simulate()` now has a FIFO queue model (`queue=` argument). When our ask level changes we (re)join behind Q0 shares. A fill at q == a first depletes the queue ahead; a fill at q > a means the level is gone. Q0 comes from a grid (0, 500, 2k, 5k, 20,726) or is drawn from the 484 recorded live touch sizes. I added cancellation modelling (fix 1b), because FIFO without cancellations is a pessimistic bound. The verdict now comes from the realistic model. The front-of-queue primary is kept and reported as the pre-registered idealized case. | Break-even queue in DEV is about 500-2,000 shares. The live queue has p25 of about 3k and median of about 21k. Results are in the execution-model table below. |
| 1b | (Mine, not raised) FIFO without cancellations ignores the queue ahead that is cancelled. | Accepted. The live replay now tracks size decreases at the touch level. Cancels = decreases - trades: median 26% and mean 46% of the initial queue within the 70 s window. Three models bracket the answer. **No cancel ahead** is the pessimistic bound. **Proportional cancellation** is the standard neutral assumption and the **verdict model**: (Q0, λ) are drawn jointly from one live window and the queue ahead decays as exp(-λΔt) from our join time; median survival over 70 s is 78%. **All cancels ahead, applied at join** is the optimistic bound; its median effective queue is 3.6k, and it is 0 in 26% of windows. | DEV: -0.144 / **-0.086** / +0.124. HOLDOUT: +0.045 / **+0.099** / +0.257. Three seeds of the verdict model give DEV -0.086, -0.099, -0.087 and HOLDOUT +0.099, +0.104, +0.112. |
| 2 | **Split taker orders.** Rows sharing (m, w, ts, s, q) are one taker order, and each row got its own 50-share clip. | Accepted. `load_fills()` now merges them. Across all MLB markets, 4,260,826 rows become 3,953,740 taker orders. | Primary holdout +0.407 → +0.408 and DEV +0.331 → +0.334 (immaterial, as the reviewer found). |
| 3 | **Markout is not realized P&L; the martingale premise fails.** The reviewer computed a paired hold-minus-markout of -1.30c [-2.52, -0.05] in the holdout, and only about 42% of shares form matched pairs. | Accepted. For every model, `extras()` now reports hold-to-resolution next to the markout on the same fills, the paired test `cluster_ci((hold - mo)*100, game_pk, n)`, and the matched-pair share. | Front of queue, holdout: hold -0.645c [-1.82, +0.58] on the same fills; paired -1.05c [-2.23, +0.18] with merged orders (-1.30c on raw rows). Verdict model, holdout: hold -0.51c [-2.39, +1.37]; only 17.5% of shares are matched pairs, the rest is directional inventory. The premise is not supported, and realized P&L is negative in the holdout under every fill model. |
| 4 | **Short, overlapping markout horizon.** The reference at b0+105 is only 5-75 s after the fill, and the edge shrinks with horizon. | Accepted. Added per-fill fixed-horizon markouts using the two-sided mid at t+60, t+300 and t+600 s (`HORIZONS`). These replace the old drift-after-r diagnostic. | Front of queue, holdout: +0.375 / +0.277 / +0.242 [-0.12, +0.60]. Verdict model, holdout: +0.057 [+0.002, +0.111] / -0.103 [-0.57, +0.33] / +0.067 [-0.61, +0.73]. The decay is confirmed. |
| 5 | **Selection bias from the reference.** Dropping windows without a two-sided print inflates the result: the reviewer's one-sided fallback cut the holdout from +0.407 to +0.323. | Fix implemented; the size of the bias is disputed. `Book.mid_refs()` adds a relaxed reference. It uses the two-sided mid when available, otherwise the last print of the side that printed, shifted by a half-spread. The half-spread is estimated on **DEV two-sided windows only**: 1.00c in 2025 and 0.48c in 2026H1, frozen for the holdout. Validation: the one-sided construction applied to two-sided windows reproduces the two-sided markout (2025 +0.234 vs +0.233; 2026H1 +0.361 vs +0.365; holdout +0.400 vs +0.408). The reviewer's fallback (`verify_..._stats.py`, `mid_relaxed`) had a sign error when only side 1 printed: it used `1 - b - hs` where it should be `1 - b + hs`, which shifts mid0 by 2·hs (about 1c) on 47% of those shares. Re-scoring with the reviewer's sign reproduces their -0.02c for the dropped holdout windows; the correct sign gives +0.326c. | Front of queue, holdout: +0.408 → **+0.392** with the relaxed reference, so the bias is small (4%, not 20%). Verdict model, holdout: +0.099 → **+0.067** [+0.028, +0.104], because fills in dropped windows mark to -0.067c there. Both references are reported. |
| 6 | **Holdout seen before pre-registration.** The feasibility scripts (`explore19.py`, `explore22.py` in the session scratchpad) split years on `fee_rate == 0`, so their "2026" sample included Jul-Sep 2026. The window [b0+30, b0+100] was chosen with holdout data in view. | Accepted and disclosed. `feasibility_dev_only()` recomputes the explore22 number (taker-weighted markout of all fills in [b0+30, min(b0+100, b1-3)] against the mean print in [b1-60, b1+2]) with an explicit `game_start_ts < 2026-07-01` filter. | On DEV data only the motivating number is **+0.373c [+0.317, +0.438]** for 2026H1 (and +0.876c for 2025), so the hypothesis would have been proposed without holdout data. The window choice was still made with the holdout visible, so the front-of-queue holdout figure is **not a clean out-of-sample confirmation**. The FIFO models were fixed on DEV before this re-run. However, the execution reviewer had already run a FIFO grid on the holdout, so it was not blind either. |
| 7 | **Verdict label does not follow the protocol** (both reviews). | Accepted. The verdict is computed in code from the verdict model with the rule "PROFITABLE: holdout > 0, DEV CI lower bound > 0, and (b) holdout >= 0. DEAD: holdout <= 0, or DEV CI entirely < 0. Otherwise PROMISING." | **DEAD** (DEV -0.086 [-0.143, -0.036]). |
| 8 | Effect size implausible for a newcomer (+0.41c is 66% of the maximum). | Accepted. It is explained by fix 1: the edge sits in fills where the level held (q == a, +0.55c), and those go to the head of the queue. | Under FIFO the q > a pick-offs (-0.41c) are 45-63% of our shares, against 15-22% under front of queue. |
| 9 | Minor: the DEV 2025 front-of-queue P&L is concentrated (top 10% of games carry 87%). | Noted, unchanged. 2025 is negative under every realistic model anyway. | None. |
| 10 | Minor: the rebate is a per-fill approximation of a pool, and feed latency leaves a margin of about 0.4 s. | Unchanged; listed under Caveats. The rebate now matters more: it is larger than the whole verdict-model holdout edge. | None. |

I checked whether this re-run tuned anything on the holdout. It did not:
- The half-spread fallback and T_stop are estimated on DEV.
- The queue and cancellation calibration comes from the only recorded books (2026-09-18/19). Those
  dates fall in the holdout calendar, but no holdout fills or outcomes are used.
- The verdict model was chosen from DEV-only runs of all three cancellation assumptions. Proportional
  is the standard middle assumption; the other two are bounds.
- Every model was then run once on the holdout, and all of them are reported.

## Hypothesis

In-play makers without a fast feed get picked off. Information arrives with every pitch, and the
book reprices in about 3.5 s. The half-inning break is a scheduled blackout. From the third out
until the next half's first pitch (about 100 s or more), nothing on the field can change, yet
retail keeps crossing the spread. The hypothesis is that a maker quoting only inside that window
collects the half-spread plus the 15% rebate with near-zero adverse selection.

## Rule as implemented

- **Universe.** MLB moneylines (`C.markets()` with family `baseball`) joined to
  `data/mlb/games.parquet` on `condition_id`, which gives `game_pk`. Five ambiguous `game_pk`s
  (10 markets) are dropped. Filter: `pre_usd >= $25k`, which is pregame-only information. Primary
  games: DEV_2025 1,032, DEV_2026H1 909, HOLDOUT 669.
- **Split.** DEV is `game_start_ts < 2026-07-01 UTC`. HOLDOUT is `game_start_ts >= 2026-07-01`,
  the 5% fee regime.
- **Fills.** Taker fill rows merged into taker orders by (m, wallet, ts, side, price). This is
  review fix 2.
- **Breaks.** b0 = max(`end_ts`) of the half's plate appearances, which is the third out.
  b1 = min(`start_ts`) of the next half.
- **Frozen T_stop.** The 5th percentile of b1 - b0 over all DEV games (61,119 breaks) is 101.1 s,
  rounded down to **100 s**. The quote window is W = [b0+30, b0+100] in on-chain time. It does not
  use b1, so 3.3-4.8% of windows leak into live play.
- **Quote.** At each fill time t in W, for each token s we rest an ask at a_s(t), the price of the
  last taker fill acquiring s with ts <= t-3 s. There is no quote on s if that fill is more than
  120 s old. The cap is 1,000 shares per side per break, and a fill gives us at most 50 shares per
  taker order.
- **Fill models.**
  - **Front of queue** (the pre-registered primary): every taker order acquiring s at q >= a_s
    fills us min(size, 50).
  - **FIFO** (review fix): fills as described in fix 1.
  - **Verdict model**: FIFO with (Q0, λ) drawn jointly from one of 484 live token-windows at every
    (re)join, with proportional cancellation from the join time (last print + 3 s). Seed 0; seeds 1
    and 2 are reported.
- **Metric (pre-registered).** Per-share maker markout = a_s - ref_s + 0.15 × fee_rate × a_s(1-a_s).
  - ref is the two-sided print mid at r = b0+105, using the last fill of each side in [r-60, r].
  - A break is dropped from the markout if only one side printed; the relaxed reference in fix 5
    is also reported.
  - The result is a share-weighted mean with `C.cluster_ci` clustered by `game_pk`.
  - ROI on capital divides by (1 - a_s).
- **Costs.** Makers pay no fee. The rebate uses each market's `fee_rate`: 0.000c in 2025,
  0.077c/share in 2026H1 and 0.119c/share in the holdout. Maker sensitivity: fill price 1c worse.

## Results by execution model

Markout in c/share [95% CI clustered by game]. "Live queue" means Q0 drawn from the recorded touch
sizes at b0+30 (median 20,726 shares).

| fill model | DEV 2025 | DEV 2026H1 | DEV pooled | HOLDOUT | holdout P&L |
|---|---|---|---|---|---|
| front of queue, as first run (split rows unmerged) | +0.235 | +0.360 | +0.331 [+0.307, +0.354] | +0.407 [+0.386, +0.428] | +$3,769 |
| front of queue (= FIFO with Q0 = 0) | +0.233 [+0.180, +0.282] | +0.365 [+0.338, +0.387] | +0.334 [+0.310, +0.356] | +0.408 [+0.388, +0.427] | +$3,630 |
| FIFO, Q0 = 500 | -0.119 [-0.209, -0.038] | +0.171 [+0.118, +0.216] | +0.087 [+0.043, +0.127] | +0.274 [+0.244, +0.304] | +$1,333 |
| FIFO, Q0 = 2,000 | -0.291 [-0.387, -0.201] | -0.024 [-0.106, +0.035] | -0.116 [-0.174, -0.063] | +0.140 [+0.097, +0.179] | +$464 |
| FIFO, Q0 = 5,000 | -0.349 [-0.455, -0.259] | -0.171 [-0.267, -0.095] | -0.240 [-0.308, -0.182] | +0.009 [-0.042, +0.054] | +$22 |
| FIFO, Q0 = 20,726 (live median) | -0.387 [-0.489, -0.292] | -0.366 [-0.490, -0.272] | -0.375 [-0.457, -0.305] | -0.240 [-0.304, -0.184] | -$411 |
| FIFO, live queue, no cancel ahead (pessimistic) | -0.275 [-0.367, -0.187] | -0.069 [-0.159, -0.001] | -0.144 [-0.204, -0.092] | +0.045 [+0.001, +0.087] | +$121 |
| **FIFO, live queue, proportional cancel (VERDICT)** | **-0.245 [-0.337, -0.159]** | **-0.003 [-0.078, +0.057]** | **-0.086 [-0.143, -0.036]** | **+0.099 [+0.057, +0.137]** | **+$300** |
| FIFO, live queue, all cancels ahead (optimistic bound) | -0.047 [-0.125, +0.024] | +0.196 [+0.146, +0.237] | +0.124 [+0.081, +0.163] | +0.257 [+0.223, +0.289] | +$1,145 |
| (b) back-of-queue stress (pre-registered gate) | -0.440 [-0.641, -0.277] | -0.521 [-0.724, -0.359] | -0.495 [-0.652, -0.375] | -0.444 [-0.545, -0.354] | -$435 |

The holdout is better than DEV in every model. Part of that is the rebate, which rises from 0 to
0.077c and then 0.119c per share. Under the verdict model, the markout excluding the rebate is
-0.245c (2025), -0.080c (2026H1) and -0.020c (holdout).

### Verdict model in detail

| quantity | DEV 2025 | DEV 2026H1 | DEV pooled | HOLDOUT |
|---|---|---|---|---|
| simulated fills / games | 7,017 / 847 | 17,302 / 881 | 24,319 / 1,728 | 9,801 / 640 |
| shares / $ notional at a | 248k / $123k | 472k / $225k | 721k / $348k | 302k / $151k |
| markout, pre-registered reference | -0.245 [-0.337, -0.159] | -0.003 [-0.078, +0.057] | -0.086 [-0.143, -0.036] | **+0.099 [+0.057, +0.137]** |
| ROI on capital | -0.49% [-0.67, -0.31] | -0.01% [-0.15, +0.11] | -0.17% [-0.28, -0.07] | +0.20% [+0.11, +0.28] |
| markout with 1c-worse fill | -1.245 | -1.003 | -1.086 | -0.901 (ROI -1.77%) |
| of which rebate | 0.000 | 0.077 | 0.050 | 0.119 |
| markout, relaxed one-sided reference | -0.297 [-0.365, -0.233] | -0.012 [-0.082, +0.040] | -0.129 [-0.176, -0.087] | +0.067 [+0.028, +0.104] |
| per-fill markout at t+60 s | -0.299 [-0.390, -0.213] | -0.025 [-0.086, +0.030] | -0.121 [-0.176, -0.070] | +0.057 [+0.002, +0.111] |
| per-fill markout at t+300 s | -0.491 [-1.001, +0.020] | +0.039 [-0.310, +0.422] | -0.110 [-0.404, +0.190] | -0.103 [-0.571, +0.326] |
| per-fill markout at t+600 s | -0.369 [-0.961, +0.225] | -0.124 [-0.641, +0.397] | -0.196 [-0.602, +0.236] | +0.067 [-0.610, +0.725] |
| **hold to resolution** (all fills) | -0.013 [-1.207, +1.183] | +0.262 [-1.124, +1.624] | +0.148 [-0.799, +1.116] | **-0.508 [-2.391, +1.369]** |
| paired hold minus markout | +0.477 [-1.040, +1.988] | +0.163 [-1.324, +1.692] | +0.271 [-0.827, +1.431] | -0.635 [-2.827, +1.502] |
| share of shares in matched pairs within a break | 22% | 18% | 20% | 17.5% |
| share of shares that are pick-offs (q > a) | 84% | 51% | 63% | 45% |
| markout on fills where the level held (q == a) | +0.611 | +0.465 | +0.486 | +0.515 [+0.486, +0.543] |
| markout on pick-offs (q > a) | -0.403 | -0.448 | -0.427 | -0.414 [-0.487, -0.353] |
| P&L: markout / hold, $ | -$608 / -$49 | -$13 / +$1,451 | -$621 / +$1,402 | **+$300 / -$1,937** |

The mechanism behind the gap is clear. Fills at our level while it holds (q == a) earn about +0.5c.
Fills where our stale ask is swept (q > a) lose about 0.42c. At the back of the queue, pick-offs are
most of what we get: 45-84% of shares, against 15-22% for the front-of-queue model.

### Front-of-queue primary: robustness added in the review

This is the idealized case. It is kept because it is the pre-registered primary fill model.

| quantity | DEV 2025 | DEV 2026H1 | DEV pooled | HOLDOUT |
|---|---|---|---|---|
| markout, pre-registered reference | +0.233 [+0.180, +0.282] | +0.365 [+0.338, +0.387] | +0.334 [+0.310, +0.356] | +0.408 [+0.388, +0.427] |
| markout, relaxed reference | +0.213 [+0.172, +0.253] | +0.360 [+0.337, +0.380] | +0.318 [+0.298, +0.336] | +0.392 [+0.373, +0.410] |
| fills in windows the pre-registered reference drops (19-33% of shares) | +0.172 [+0.114, +0.231] | +0.331 [+0.310, +0.352] | +0.254 [+0.224, +0.284] | +0.326 [+0.285, +0.358] |
| per-fill at t+60 / t+300 / t+600 s | +0.185 / +0.155 / +0.234 | +0.363 / +0.308 / +0.268 [+0.043, +0.485] | +0.320 / +0.279 / +0.261 [+0.062, +0.466] | +0.375 / +0.277 / +0.242 [-0.122, +0.601] |
| hold to resolution, same fills | +1.007 [-0.257, +2.159] | +0.064 [-0.702, +0.802] | +0.287 [-0.400, +0.941] | **-0.645 [-1.816, +0.576]** |
| paired hold minus markout | +0.774 [-0.503, +1.934] | -0.301 [-1.057, +0.437] | -0.047 [-0.735, +0.607] | -1.053 [-2.231, +0.177] |
| matched-pair share | 32% | 47% | 42% | 41.5% |
| markout if level held / if picked off | +0.725 / -0.403 | +0.515 / -0.446 | +0.551 / -0.426 | +0.555 / -0.415 |

Even with perfect queue priority, the markout decays with horizon, and its CI crosses 0 by
t+600 s in the holdout. Realized P&L held to resolution is negative in the holdout, at -$7,102 on
all primary fills.

## All pre-registered variants, both fill models

No other trading variants were run. The FIFO grid and the three cancellation assumptions are
execution-model calibrations, not strategy variants. Units are c/share.

**Front-of-queue fill model**

| variant | DEV 2025 | DEV 2026H1 | DEV pooled | HOLDOUT |
|---|---|---|---|---|
| primary (K = 50) | +0.233 [+0.180, +0.282] | +0.365 [+0.338, +0.387] | +0.334 [+0.310, +0.356] | +0.408 [+0.388, +0.427] |
| (a) K = 200 | +0.210 [+0.158, +0.261] | +0.342 [+0.322, +0.361] | +0.306 [+0.285, +0.326] | +0.376 [+0.355, +0.397] |
| (b) back-of-queue stress | -0.440 [-0.641, -0.277] | -0.521 [-0.724, -0.359] | -0.495 [-0.652, -0.375] | -0.444 [-0.545, -0.354] |
| (c) 25% of each fill | +0.095 [+0.007, +0.180] | +0.283 [+0.258, +0.307] | +0.231 [+0.199, +0.260] | +0.287 [+0.256, +0.315] |
| (d) all games | +0.225 [+0.179, +0.267] | +0.364 [+0.339, +0.386] | +0.327 [+0.308, +0.346] | +0.387 [+0.366, +0.407] |
| (e) hold to resolution | +0.831 [-0.200, +1.893] | +0.174 [-0.545, +0.872] | +0.365 [-0.224, +0.964] | -0.634 [-1.690, +0.460] |
| (f) negative control, live play | -0.903 [-1.013, -0.801] | -1.268 [-1.338, -1.201] | -1.210 [-1.270, -1.153] | -1.571 [-1.686, -1.458] |
| (g) mid-inning pauses | -0.200 [-0.372, -0.045] | -0.023 [-0.186, +0.114] | -0.072 [-0.197, +0.042] | +0.011 [-0.193, +0.181] |

**FIFO fill model with live queue and proportional cancellation (verdict model)**

| variant | DEV 2025 | DEV 2026H1 | DEV pooled | HOLDOUT |
|---|---|---|---|---|
| primary (K = 50) | -0.245 [-0.337, -0.159] | -0.003 [-0.078, +0.057] | -0.086 [-0.143, -0.036] | +0.099 [+0.057, +0.137] |
| (a) K = 200 | -0.167 [-0.259, -0.086] | +0.021 [-0.027, +0.068] | -0.051 [-0.093, -0.007] | +0.090 [+0.047, +0.128] |
| (b) back-of-queue filter on top of FIFO | -0.592 [-0.836, -0.405] | -0.643 [-0.908, -0.443] | -0.626 [-0.806, -0.473] | -0.485 [-0.589, -0.386] |
| (c) 25% of what reaches us | -0.151 [-0.287, -0.037] | +0.059 [+0.011, +0.108] | -0.016 [-0.072, +0.040] | +0.045 [-0.008, +0.095] |
| (d) all games | -0.295 [-0.368, -0.221] | +0.004 [-0.068, +0.060] | -0.110 [-0.162, -0.066] | +0.045 [-0.008, +0.094] |
| (e) hold to resolution | -0.013 [-1.207, +1.183] | +0.262 [-1.124, +1.624] | +0.148 [-0.799, +1.116] | -0.508 [-2.391, +1.369] |
| (f) negative control, live play | -1.730 [-1.887, -1.589] | -2.589 [-2.705, -2.487] | -2.437 [-2.529, -2.343] | -2.859 [-3.041, -2.687] |
| (g) mid-inning pauses | -0.982 [-1.268, -0.729] | -0.789 [-1.173, -0.487] | -0.861 [-1.120, -0.647] | -0.675 [-1.100, -0.343] |

The negative control is about 2.4-3.0c worse under FIFO than the break windows (-0.09 to +0.10c). So
breaks are much less toxic than live play; there is just not enough left for a queued newcomer.

## Live order-book realism check (2026-09-18/19, 15 games, 484 token-windows)

b0 is taken from the MLB feed (first message with outs == 3). At b0+30 I recorded the best ask and
its size, then replayed the book and trades through b0+100.

| quantity | value |
|---|---|
| spread at b0+30 | median 1.0c; 97.5% of samples are one tick, so a maker cannot improve on price |
| shares resting at the best ask | median **20,726** (p25 2,975, p75 55,619) |
| taker shares acquired at that ask during the window | mean 1,309, median 0 |
| windows where taker volume exceeded the initial queue | 3.1% |
| cancellations at the level (decreases - trades) as a share of the initial queue | median 26%, mean 46% |
| queue-ahead survival under proportional cancellation, 70 s | median 78%, mean 61% |
| queue ahead net of all cancellations (optimistic) | median 3,605; 0 in 26% of windows |
| replayed 50-share-clip maker, mean shares filled per token-window: front of queue / optimistic / FIFO | **28.0 / 9.5 / 6.2** |
| share of token-windows with any fill: front of queue / optimistic / FIFO | 44% / 12% / 6.6% |

A newcomer gets between a fifth and a third of the front-of-queue fills. It gets mostly the
level-sweeping ones.

## Sanity checks (unchanged from the first run)

- **Timestamps.** The mean |Δp0| between consecutive fills spikes at [b0-5, b0+10], which is the
  third-out repricing. It decays to about the bid-ask bounce from b0+30, and rises again at b1+5,
  the first pitch.
- **Payouts.** The fill-level `y` matches the markets table on 100% of MLB fills.
- **Hand check.** One random 2026 window was recomputed by hand and matches the code.
- **Reproduction.** Unmerged rows reproduce the first run exactly: DEV +0.331, holdout +0.407. The
  execution reviewer's FIFO grid is reproduced to within merge rounding. Their Q0 = 20,726 holdout
  was -0.244; this run gives -0.240.
- **Leakage.** Short breaks (a window that leaks into live play) are about 5% of fills and do not
  score worse.
- **Feasibility on DEV only (fix 6).** The explore22 metric is +0.876c [+0.736, +1.001] for 2025
  and +0.373c [+0.317, +0.438] for 2026H1.

## Capacity

| model | holdout $ notional at a | holdout markout P&L | holdout hold-to-resolution P&L | per day (77 days) |
|---|---|---|---|---|
| front of queue (idealized) | $441k (merged) | +$3,630 | -$7,102 | +$47 markout |
| FIFO, optimistic bound | $220k | +$1,145 | -$1,402 | +$15 |
| **FIFO, proportional (verdict)** | **$151k** | **+$300** | **-$1,937** | **+$4** |
| FIFO, no cancel ahead | $134k | +$121 | -$3,063 | +$2 |

Taker flow inside the primary windows was $18.0M in the holdout, but almost none of it reaches a
newcomer's order at a profit.

## Caveats

1. **Queue calibration comes from one evening.** It uses 15 games in September 2026, a holdout-period
   date, applied to all periods. In 2025 spreads were wider (about 2c) and books probably thinner.
   But DEV 2025 is negative even at Q0 = 500 (-0.119c), so the 2025 result does not depend on this.
2. **The verdict depends on the cancellation assumption.** Under the optimistic bound (every
   cancellation at the level ahead of us, applied instantly at join), DEV would be +0.124 and the
   holdout +0.257. That would be PROMISING, capped by gate (b), at about $15/day. The proportional
   model is the standard neutral choice; real queue position is unobservable in L2 data. Only live
   resting-order tests can settle it.
3. **The verdict-model holdout edge is the rebate.** The rebate is modelled as 15% of the taker fee
   on our fill price, per fill. The real rebate is a pool distributed by Polymarket's formula.
4. **Realized P&L is negative in the holdout.** Hold-to-resolution is negative under every fill
   model. Only 17-42% of shares form matched pairs within a break; the rest is directional
   inventory carried into the next half.
5. **The holdout was not blind for the front-of-queue model** (fix 6). The FIFO models were fixed on
   DEV before this re-run, but the execution reviewer had already run a FIFO grid on the holdout.
6. **Feed timing.** The rule needs the third out known by b0+30 in fill time. Public feeds lag about
   27 s, which leaves about 0.4 s of margin.
7. **Monte Carlo noise.** Queue draws are seeded per (seed, window id). Three seeds move the
   verdict model by about 0.01c.

## Verdict

**DEAD.** The protocol requires maker results to assume realistic queue and fill limits. The first
PROMISING rested entirely on front-of-queue fills, which a newcomer cannot get on a one-tick book
with about 21k shares resting at the touch.

With a FIFO queue calibrated on the recorded books and proportional cancellation, development is
significantly negative (-0.086c [-0.143, -0.036]). The holdout gain (+0.099c) is smaller than the
modelled rebate. Realized hold-to-resolution P&L is negative in the holdout, the pre-registered
back-of-queue gate fails, a 1c-worse fill gives -0.90c, and capacity is about $4/day.

The break-time half-spread is real (+0.4c at the front of the queue, against -1.6c for the same rule
during live play), but it belongs to makers who already hold time priority.

## Reproduce

```
systemd-run --user --scope -q -p MemoryMax=4G -p MemorySwapMax=0 .venv/bin/python -m pmsports.research.h_mlb_inning_break_mm --dev-only   # development only
systemd-run --user --scope -q -p MemoryMax=4G -p MemorySwapMax=0 .venv/bin/python -m pmsports.research.h_mlb_inning_break_mm --live       # order-book replay (queue + cancel calibration)
systemd-run --user --scope -q -p MemoryMax=4G -p MemorySwapMax=0 .venv/bin/python -m pmsports.research.h_mlb_inning_break_mm              # dev + holdout
```

`--live` reads T_stop from `results_dev.json` and writes `live_check_windows.parquet`. The main run
reads that file for the FIFO calibration. Total runtime is about 2 minutes plus about 30 s for
`--live`.
