# Recovered MLB hypotheses: corrected historical tests

These four tests use actual later transactions with finite printed size, historical fees and $100/game targets. They do **not** establish executable depth, timely feed receipt or a fresh holdout. All historical periods had already been inspected.

| Rule | Period | Signals/legs | Filled games | Capital incl. fees | Net P&L | Cash-weighted ROI | Cash ROI game-cluster 95% CI | +1c cash ROI |
|---|---|---:|---:|---:|---:|---:|---|---:|
| totals-pace-antiextrapolation | all | 53 | 19 | $246.76 | $120.14 | 48.69% | -66.58% to 216.87% | 42.00% |
| totals-pace-antiextrapolation | dev | 43 | 17 | $220.28 | $66.61 | 30.24% | -86.62% to 214.92% | 24.14% |
| totals-pace-antiextrapolation | holdout | 10 | 2 | $26.48 | $53.53 | 202.17% | n/a to n/a | 193.15% |
| nrfi-demand-premium | all | 136 | 84 | $2,052.42 | $104.20 | 5.08% | -30.97% to 46.31% | 9.33% |
| nrfi-demand-premium | dev | 76 | 48 | $887.04 | $-553.19 | -62.36% | -88.51% to -16.28% | -50.60% |
| nrfi-demand-premium | holdout | 60 | 36 | $1,165.38 | $657.39 | 56.41% | 4.39% to 98.69% | 48.42% |
| runline-1run-spike | all | 0 | 0 | $0.00 | $0.00 | n/a | n/a to n/a | n/a |
| runline-1run-spike | dev | 0 | 0 | $0.00 | $0.00 | n/a | n/a to n/a | n/a |
| runline-1run-spike | holdout | 0 | 0 | $0.00 | $0.00 | n/a | n/a to n/a | n/a |
| runline-margin-transition | all | 323 | 131 | $2,236.83 | $-327.54 | -14.64% | -42.55% to 14.31% | -16.62% |
| runline-margin-transition | dev | 174 | 69 | $1,006.91 | $-319.04 | -31.69% | -71.00% to 13.43% | -33.19% |
| runline-margin-transition | holdout | 149 | 62 | $1,229.93 | $-8.50 | -0.69% | -39.01% to 37.27% | -3.06% |

No deployable edge is established by these tests. Read positive totals/NRFI cash point estimates with the interval, tiny first-print capacity, opposite-period results and concentration below. The pair diagnostic tests whether the pricing claim exists before any hypothetical atomic-fill profit.

## Original NRFI equal-game inference

The original NRFI requirement is equal weighting by game with `C.cluster_ci`. For each funded game, sum every leg’s net P&L and fees-inclusive capital, divide those sums, then average the resulting game ROIs. Each game gets one bootstrap vote, regardless of its capital or number of legs. This differs from the cash-weighted ROI above. No-fill games have no defined ROI and do not enter either ROI interval; their signals remain in the audit.

| Period | Replay | Funded games | Equal-game ROI | Equal-game 95% CI | Cash-weighted ROI |
|---|---|---:|---:|---|---:|
| all | Primary | 84 | -11.45% | -33.40% to 10.99% | 5.08% |
| all | +1c | 72 | -15.07% | -38.40% to 8.38% | 9.33% |
| dev | Primary | 48 | -41.37% | -68.41% to -13.75% | -62.36% |
| dev | +1c | 42 | -34.42% | -64.04% to -4.59% | -50.60% |
| holdout | Primary | 36 | 28.43% | -5.94% to 60.84% | 56.41% |
| holdout | +1c | 30 | 12.03% | -24.29% to 48.65% | 48.42% |

The original pooled NRFI equal-game estimate is **-11.45%** (95% CI -33.40% to 10.99%); the cash-weighted estimate is 5.08%. The cash result cannot replace the original equal-game test. These already-explored periods do not establish a repeatable edge.

## Rules and repairs

- **Totals pace:** half innings completed = 2×(inning−1)+bottom; pace = runs−line×hic/18. A game-weighted linear regression of Over outcome minus its observed acquisition price on pace uses strictly prior seasons and hic≥4. First matching acquired-side signal/game requires ≥6c signed fitted residual; Over behind pace and Under ahead. At least five pregame prints; later entry before the next state. A season without 20 prior sampled games is unavailable, not a zero-return test. This is stricter than the recovered same-season DEV fit and prevents future-season training leakage.
- **NRFI:** buy only canonical YRFI, using Gamma question/description plus actual Yes/No labels. Prior-season rate counts r1≥1, never raw run-count means. First-inning totals come from the corrected inning-2 top checkpoint, including runs on the final out; actual schedule dates restrict training to earlier seasons. At least 100 prior games are required. This avoids the old derivative-only history, which had just one pre-2025 game and could invent a 100% prior. Begin looking T−20min; five strictly earlier prints must already exist. Signal is first observed YRFI acquisition ≤ prior rate; entry is a different later acquisition still ≤ that limit, before scheduled start. No completed-pregame liquidity is used at an earlier decision.
- **One-run pair:** at scheduled T−15min, last No acquisitions in each book within the preceding hour provide references. Trigger sum including fees <1.15. Equal requested quantities and per-leg cash reservations are fixed from these references and preceding-hour available printed quantity; future fill sizes never determine target quantity. Independent legs expire after 600s. Excess inventory receives one later size-limited unwind proxy, then residual settlement. The liquidation proxy is 1−the price of a later acquired complement, not a fabricated observed bid. Two later prints are not an atomic fill.
- **Margin transition:** a fixed logistic model uses inning, half, outs, occupied bases, margin, margin×inning and signed squared margin. Fit on corrected baseline states from strictly earlier seasons, with each game equal total training weight. First observed cheap-side acquisition with |model−canonical home-cover price|≥8c signals; later entry before the next state. No moneyline haircut or outcome-dependent state reconstruction.

**Recovered algebra error:** the No/No pair pays 1+1{|margin|≤1}; for completed nontied games its fair value is 1+s₁. The price-implied one-run probability is therefore **pair price−1**, not `2−pair price` from the original prompt. A price below 1.15 is a directional one-run bet, not guaranteed profit. Settlement uses each actual contract payout, including voids.

## Capacity and concentration

| Rule | No fills | Partial legs | Median filled capital | Top-three-game P&L | Cash ROI excluding top three | Equal-game ROI | Equal-game 95% CI |
|---|---:|---:|---:|---:|---:|---:|---|
| totals-pace-antiextrapolation | 34 | 19 | $2.18 | $214.25 | -56.02% | 107.01% | -1.30% to 223.52% |
| nrfi-demand-premium | 52 | 77 | $4.64 | $395.97 | -16.65% | -11.45% | -33.40% to 10.99% |
| runline-1run-spike | 0 | 0 | $0.00 | $0.00 | n/a | n/a | n/a to n/a |
| runline-margin-transition | 192 | 123 | $3.72 | $254.93 | -29.44% | -8.80% | -30.60% to 15.04% |

## Coverage and inference

Coverage: 2170 selected markets, 2170 local tapes, 70,318 prints joined to corrected states. Ambiguous game aliases excluded: 5. Missing tapes: 0. Newly fetched away-runline targets: 251; all requested creation-to-close, 25,076 returned prints, two empty tapes. Existing derivative/away caches retain their earlier sampling and bounded windows; the sample is not a complete unbiased MLB universe.
Canonical outcome 0 is verified from actual Over/Under labels or home/away team labels; legacy Yes/No runlines additionally require matching Gamma question and resolution text. Unknown orientation markets excluded: 0. Missing first-inning totals or final margins are excluded from priors, never counted as false outcomes. Ledger no-fills retain signal time with null entry time; full and partial unwinds carry their actual economic exit clocks/prices.
Pair diagnostic: {"n": 112, "median_pair_cost": 1.31358993382521, "median_implied_one_run": 0.2999999582499999, "median_prior_one_run": 0.2830789323320545, "median_reference_skew_s": 707.0}.
Frozen NRFI rates: {"2025": {"games": 9879, "base": 0.48942200627593885}, "2026": {"games": 12251, "base": 0.49212309199249044}}.
Totals models: {"2025": {"status": "unavailable", "prior_games": 0}, "2026": {"prior_games": 49, "intercept": 0.029152506945383225, "slope": -0.012805487123895079, "zero_crossing": 2.2765636842494303}}. The fitted zero-crossing is reported rather than forced to zero; a nonzero crossing weakens the original mechanism.
The corrected MLB panel replaces old phantom/duplicate states. Raw transaction timestamps are used without subtracting estimated chain lag. Historical state timestamps stand in for public knowledge; free MLB feed delay measured elsewhere is much longer than the 3s replay assumption. Next-state expiry is analytical censoring, not proof of canceling a pending sports order. Totals can close on crossing the line: results condition on observed state and surviving prints, and do not estimate unconditional late-game pricing error.
Only the four recovered primary rules and the declared +1c stress were executed. No parameter scan or post-result winning variant is hidden. Models, source file identities, every signal/fill/no-fill, pair references and all four uncapped desk ledgers are saved. Both 95% bootstrap intervals use C.cluster_ci with 2,000 whole-game resamples and seed 0: cash intervals divide resampled total net P&L by resampled total fees-inclusive capital; equal-game intervals average the resampled per-game aggregate ROIs. Empty samples have null point estimates and bounds; fewer than five funded games have no interval. Neither interval corrects historical selection or multiple testing. A positive point estimate is exploratory until independently collected receipt/depth data validate entry, capacity and a frozen rule.

The +1c replay retains the original limit: for NRFI it can skip an originally affordable print and take another later print, so its filled sample can change and aggregate ROI need not decrease. This is an executable-limit sensitivity within the transaction proxy, not a same-filled-sample causal cost estimate. Primary and stress Parquets retain every such change.

## Reproduction

```bash
OPENBLAS_NUM_THREADS=1 .venv/bin/python -m pmsports.research.h_baseball_continuation
.venv/bin/python -m pytest -q tests/test_baseball_continuation.py
```

Outputs: `data/research/baseball_continuation/results.json`, full primary/stress Parquets, `pair_diagnostic.parquet`, and `data/research/ledgers/<rule>.json`. No network calls occur in the study.
