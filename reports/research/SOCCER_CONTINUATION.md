# Five soccer hypotheses: causal continuation

**No executable edge is established.** These are historically explored transaction proxies. The atomic three-book/depth claim is blocked by absent soccer order books; no historical public-feed receipt clocks were captured.

Run: 4739 games, 106519 events; full local panel; action policy soccer-direct-buy-sell-v2. Development precedes July 1, 2026; the later period was already inspected and is exploratory, not fresh confirmation.

| Hypothesis | Period | Selected | Funded signals | Capital | Net P&L | ROI | 95% game CI | +1c ROI |
|---|---|---:|---:|---:|---:|---:|---|---:|
| Red-card timescale | dev | 105 | 103 | $3106.48 | $522.94 | +16.83% | -5.35% to +38.68% | +16.04% |
| Red-card timescale | holdout | 62 | 61 | $2519.92 | $528.11 | +20.96% | -1.36% to +43.23% | +18.88% |
| Post-goal three-book dutch book | dev | 173 | 173 | $2056.87 | $-213.44 | -10.38% | -19.88% to -1.88% | -15.13% |
| Post-goal three-book dutch book | holdout | 27 | 27 | $637.78 | $-13.71 | -2.15% | -11.28% to +3.40% | -6.05% |
| Early substitution shock | dev | 121 | 120 | $2552.65 | $0.79 | +0.03% | -34.88% to +34.86% | -2.53% |
| Early substitution shock | holdout | 48 | 47 | $1474.53 | $396.47 | +26.89% | -36.59% to +104.91% | +22.84% |
| Draw-anchor team-pair basis | dev | 254 | 254 | $1302.45 | $-45.00 | -3.46% | -10.05% to +2.20% | -6.91% |
| Draw-anchor team-pair basis | holdout | 53 | 52 | $391.15 | $18.52 | +4.73% | -9.20% to +21.48% | +3.31% |
| Added-time leader | dev | 477 | 461 | $19196.54 | $157.79 | +0.82% | -3.00% to +4.02% | -0.26% |
| Added-time leader | holdout | 186 | 182 | $8079.81 | $559.12 | +6.92% | +2.03% to +10.54% | +5.80% |

## Frozen rules and causal corrections

Red-card primary: first qualifying card at minute ≤65 when the carded team is not leading; buy opponent Yes. Late ≥70 is separate. Substitution primary: first qualifying first-half substitution ≤25, opponent reference 15–85c; this is an early-substitution proxy, with explicit injury text separately labeled. Both decide 300s after the event, require three previously observed reference probabilities, then enter strictly after another 3s within 600s. These delay corrections replace future h300 selection. An opponent may itself have an earlier red; the primary follows the original team-side rule and does not establish the 11-versus-10 mechanism.

Dutch-book: first literal BUY-Yes observations strictly after event+3 through event+60, ≤5s span; decide on the latest print. Trigger all-in observed unit cost <0.995. Submit NEW later orders after 3s; the observation prints are never execution. The No mirror requires literal BUY-No observations and <1.995, not complemented Yes asks. Equal quantities are fixed from these known observations, capped by their known sizes and $100/event. A void may pay 1.5 across three Yes or No legs; the advertised $1/$2 locks require compatible nonvoid settlement rules. No atomic or depth-based implementation is claimed.

Draw anchor: analogous three literal BUY-Yes observations, 5s span, |sum−1|≥5c. Underround buys both teams Yes; overround buys both No only when literal BUY-No references are available within 5s. Fixed share counts are proportional to the required-side prices. The original unspecified proportional sizing coefficient is frozen at min($100,1000×|gap|,smaller known leg notional). Entries occur after a new 3s delay. One first qualifying order per game. The 60s observation lookout is a declared causal bound.

Pairs receive a 60s entry window. Any leg failing its prescribed quantity triggers an attempted unwind of every acquired leg, using the first later literal SELL of the held native token strictly after deadline+3 within 120s and before the regulation whistle. The SELL normalizes to opposite-side q, so 1−q recovers its actual sale price before slippage; it is still a historical transaction proxy, shares are capped, exit fees are charged, and all unsold residuals settle. Complete pairs settle. Each policy uses a shared print-capacity pool; independent policies reset it. Capital is $100/event including entry fees and is never recycled. Exit fees are additionally reported in total cost and ROI. No fill is required for a signal to remain in the audit.

Added time: parse explicit base+N clock, period 2 only, no ties or terminal-whistle rows, leader reference 60–97c, first qualifying event. Enter after 3s within 120s; +1c is the headline ledger sensitivity. Price-matched 75–85 and 70–80 controls use the same rule and prespecified 1c decision-price bins. Both arms retain no-fills and compare only their common price support. Joint game bootstrap preserves same-game covariance. Control ROI is a reweighted comparison, not additional simulated capacity.

## Mandatory checks and interpretation

Substitution coherence uses the same selected rows for all three leg errors, with a declared 1c mean-sum tolerance. The sum equals payout sum minus reference-price sum; failure is a coherence-gate failure, not proof of a particular causal selection mechanism. Anchor staleness regresses gap on log(1+age/count) using DEV only, reports historical out-of-period fit and residual violation rates. Failure of that simple model to explain a gap does not establish a valid draw anchor. Positive added-time ROI alone is insufficient: it must outperform the price-matched control, with uncertainty and overlap reported.

```json
{
  "sub_coherence": {
    "dev": {
      "n": 121,
      "sum_leg_errors": -0.0012167388569330865,
      "ci_lo": -0.0036998407675145244,
      "ci_hi": 0.0014089966693143273,
      "leg_errors": {
        "home": -0.061124313113206805,
        "draw": 0.0013528559092163123,
        "away": 0.0585547183470574
      },
      "mean_price_sum": 1.001216738856933,
      "mean_payout_sum": 1.0,
      "gate": "passes_1c_tolerance"
    },
    "holdout": {
      "n": 48,
      "sum_leg_errors": -0.006232130381961023,
      "ci_lo": -0.011070769827779424,
      "ci_hi": -0.0013458861132540976,
      "leg_errors": {
        "home": -0.13395842460298935,
        "draw": 0.046622426960611675,
        "away": 0.08110386726041666
      },
      "mean_price_sum": 1.006232130381961,
      "mean_payout_sum": 1.0,
      "gate": "passes_1c_tolerance"
    }
  },
  "anchor_staleness": {
    "status": "descriptive_dev_fitted_control",
    "features": [
      "intercept",
      "age_home_pre",
      "age_draw_pre",
      "age_away_pre",
      "n_home_5m",
      "n_draw_5m",
      "n_away_5m"
    ],
    "coefficients": [
      0.05460581412616544,
      -0.00439758479238433,
      -0.000808107902287983,
      -0.0028199626205241074,
      -0.0007087930086568644,
      -0.005013697471183816,
      -0.000598794704270782
    ],
    "dev": {
      "n": 2664,
      "r2": 0.01258472773276964,
      "mean_abs_gap": 0.034917502913288295,
      "mean_abs_residual": 0.028014373025401135,
      "original_5c_exceedance": 0.16216216216216217,
      "residual_5c_exceedance": 0.14564564564564564
    },
    "holdout": {
      "n": 1006,
      "r2": -0.13143908991104492,
      "mean_abs_gap": 0.02092096200109344,
      "mean_abs_residual": 0.019610640580962388,
      "original_5c_exceedance": 0.08449304174950298,
      "residual_5c_exceedance": 0.09343936381709742
    },
    "interpretation": "Prediction of gap from age/count alone; failure to predict does not prove a correct draw anchor. No mechanism pass is inferred from an insignificant coefficient."
  },
  "added_time_vs_control": {
    "dev": {
      "status": "matched_exploratory",
      "overlap_bins": [
        61,
        64,
        65,
        66,
        67,
        68,
        71,
        72,
        73,
        74,
        75,
        76,
        77,
        78,
        79,
        80,
        81,
        82,
        83,
        84,
        85,
        86,
        87,
        88,
        89,
        90,
        91,
        92,
        93,
        94,
        95,
        96,
        97
      ],
      "leader_signals": 477,
      "control_signals": 780,
      "leader_overlap_fraction": 1.0,
      "control_overlap_fraction": 0.9038238702201622,
      "leader_roi": -0.002631893426889091,
      "control_roi": -0.02738225410400908,
      "difference": 0.02475036067711999,
      "difference_ci_lo": -0.031526463696446676,
      "difference_ci_hi": 0.09310901798839782,
      "slip": 0.01
    },
    "holdout": {
      "status": "matched_exploratory",
      "overlap_bins": [
        60,
        62,
        64,
        66,
        69,
        71,
        74,
        76,
        77,
        78,
        79,
        80,
        81,
        82,
        83,
        84,
        85,
        86,
        87,
        88,
        89,
        90,
        91,
        92,
        93,
        94,
        95,
        96,
        97
      ],
      "leader_signals": 186,
      "control_signals": 291,
      "leader_overlap_fraction": 1.0,
      "control_overlap_fraction": 0.8016528925619835,
      "leader_roi": 0.058037732358246066,
      "control_roi": -0.07081317903333847,
      "difference": 0.12885091139158455,
      "difference_ci_lo": 0.025739648357448273,
      "difference_ci_hi": 0.24094007987095614,
      "slip": 0.01
    }
  },
  "added_time_vs_70_80": {
    "dev": {
      "status": "matched_exploratory",
      "overlap_bins": [
        61,
        64,
        65,
        66,
        67,
        68,
        71,
        72,
        73,
        74,
        75,
        76,
        77,
        78,
        79,
        80,
        81,
        82,
        83,
        84,
        85,
        86,
        87,
        88,
        89,
        90,
        91,
        92,
        93,
        94,
        95,
        96,
        97
      ],
      "leader_signals": 477,
      "control_signals": 764,
      "leader_overlap_fraction": 1.0,
      "control_overlap_fraction": 0.8967136150234741,
      "leader_roi": -0.002631893426889091,
      "control_roi": -0.02954827044814658,
      "difference": 0.02691637702125749,
      "difference_ci_lo": -0.029855909867280903,
      "difference_ci_hi": 0.09080775767120826,
      "slip": 0.01
    },
    "holdout": {
      "status": "matched_exploratory",
      "overlap_bins": [
        60,
        62,
        64,
        66,
        69,
        71,
        74,
        76,
        77,
        78,
        79,
        80,
        81,
        82,
        83,
        84,
        85,
        86,
        87,
        88,
        89,
        90,
        91,
        92,
        93,
        94,
        95,
        96,
        97
      ],
      "leader_signals": 186,
      "control_signals": 269,
      "leader_overlap_fraction": 1.0,
      "control_overlap_fraction": 0.7620396600566572,
      "leader_roi": 0.058037732358246066,
      "control_roi": -0.057588958437580336,
      "difference": 0.11562669079582641,
      "difference_ci_lo": 0.01632881857653139,
      "difference_ci_hi": 0.22036901943261628,
      "slip": 0.01
    }
  }
}
```

## Coverage and limitations

```json
{
  "native_leg_games": 8776,
  "extra_reference_leg_games": 1297,
  "all_three_native_games": 1331,
  "missing_metadata_legs": 10,
  "soccer_captured_book_games": 0,
  "book_validation_status": "blocked: local capture is MLB-only; no soccer CID overlap",
  "extra_tape_schema": "ts,p_yes,size only; acquired direction and wallet identity unavailable"
}
```

Native asset IDs identify the traded token. Literal BUY of the intended token is mandatory for detection/entry, and literal SELL of the held token is mandatory for unwind; a complementary trade cannot substitute for either action. The saved trade audit preserves raw action, native token, raw price and print identity. Historical single-leg reference probabilities may still use normalized SELL complements, explicitly separated from execution. Extra tapes contain only ts/p_yes/size: they can provide past reference probabilities but never acquired-side detection, entry or exit capacity. References must be strictly earlier than decision (max age 600s, 120s for added time); no interpolation or invented asks. The all-three-price gate remains an explicit rejection reason. Market terminal payouts include valid 0.5 voids; no winner-only selection or future final-score/feed-consistency eligibility gate is used. Final quality flags are audit fields.

ESPN wallclocks are retrospectively stored event timestamps, not measured arrival at a free feed. Even the delayed entries may be optimistic. Regulation whistle/closure expiries analytically censor eligibility; they do not promise exchange-valid short GTD orders or guaranteed cancellation. Printed sizes bound hypothetical capacity, not available order-book depth, minimum order admissibility, or fills obtainable by us. API coverage remains incomplete and the collected universe carries legacy selection effects. ROI includes entry and exit fees; bootstrap intervals are nominal game-cluster intervals, not multiple-testing-adjusted evidence or p-values.

## Completed scope and remaining variants

Implemented: all five primary transaction protocols, red-card late branch, mandatory substitution coherence, anchor staleness, and added-time matched controls. Atomic three-book execution and soccer live depth: blocked by no captured soccer books. Detailed completed variant names and both slippage scenarios appear in results.json. Unrun optional variants: red-card matched non-card control, added draw-No leg and alternate late cuts; substitution <2c movement gate; Dutch pregame control; anchor per-leg draw-fair sizing. League/goal-type/lead/home-away splits remain available in full audits but are not claimed run. No variant is selected as a winner.

Artifacts: data/research/soccer_continuation/{signals.parquet,trades.parquet,results.json,manifest.json}; five complete canonical ledgers in data/research/ledgers. Every initial candidate is audited; trade rows include every submitted leg including no fills. Ledgers also include rejected candidates with zero capital, so candidate/leg totals differ from funded signal counts.

Rerun: `OPENBLAS_NUM_THREADS=1 .venv/bin/python -m pmsports.research.h_soccer_continuation --run`. Bounded smoke: append `--max-games 25`; smoke output is explicitly labeled and cannot be presented as full-corpus evidence.
