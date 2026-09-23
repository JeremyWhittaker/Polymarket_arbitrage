# Five soccer hypotheses: causal continuation

**Literal-action correction pending rerun:** The tables below retain the earlier economic-acquisition proxy results. That version allowed SELL-complement prints to supply BUY-entry capacity. The repaired protocol requires an actual BUY of the intended native token and actual SELL of held tokens for unwind. These archived-comparison figures must not be described as the corrected literal-BUY results; a complete 38-configuration rerun is required.

**No executable edge is established.** These are historically explored transaction proxies. The atomic three-book/depth claim is blocked by absent soccer order books; no historical public-feed receipt clocks were captured.

Run: 4739 games, 106519 events; full local panel. Development precedes July 1, 2026; the later period was already inspected and is exploratory, not fresh confirmation.

| Hypothesis | Period | Selected | Funded signals | Capital | Net P&L | ROI | 95% game CI | +1c ROI |
|---|---|---:|---:|---:|---:|---:|---|---:|
| Red-card timescale | dev | 105 | 103 | $2976.05 | $473.35 | +15.91% | -4.94% to +35.62% | +15.29% |
| Red-card timescale | holdout | 62 | 61 | $2561.60 | $865.18 | +33.77% | +7.23% to +61.28% | +31.15% |
| Post-goal three-book dutch book | dev | 194 | 194 | $1937.15 | $-87.69 | -4.53% | -12.49% to +3.03% | -9.61% |
| Post-goal three-book dutch book | holdout | 29 | 29 | $564.14 | $-12.06 | -2.14% | -10.41% to +2.79% | -7.30% |
| Early substitution shock | dev | 121 | 120 | $2589.10 | $115.90 | +4.48% | -32.04% to +39.53% | +1.74% |
| Early substitution shock | holdout | 48 | 47 | $1320.36 | $451.23 | +34.17% | -36.40% to +122.16% | +29.79% |
| Draw-anchor team-pair basis | dev | 286 | 286 | $1267.05 | $-66.62 | -5.26% | -11.97% to +0.46% | -8.33% |
| Draw-anchor team-pair basis | holdout | 67 | 67 | $604.36 | $4.57 | +0.76% | -8.62% to +12.06% | -1.90% |
| Added-time leader | dev | 477 | 465 | $19144.35 | $85.33 | +0.45% | -3.17% to +4.01% | -0.65% |
| Added-time leader | holdout | 186 | 182 | $8203.23 | $489.08 | +5.96% | +0.73% to +10.48% | +4.86% |

## Frozen rules and causal corrections

Red-card primary: first qualifying card at minute ≤65 when the carded team is not leading; buy opponent Yes. Late ≥70 is separate. Substitution primary: first qualifying first-half substitution ≤25, opponent reference 15–85c; this is an early-substitution proxy, with explicit injury text separately labeled. Both decide 300s after the event, require three previously observed reference probabilities, then enter strictly after another 3s within 600s. These delay corrections replace future h300 selection. An opponent may itself have an earlier red; the primary follows the original team-side rule and does not establish the 11-versus-10 mechanism.

Dutch-book: first actual acquired-Yes observations strictly after event+3 through event+60, ≤5s span; decide on the latest print. Trigger all-in observed unit cost <0.995. Submit NEW later orders after 3s; the observation prints are never execution. The No mirror requires actual acquired-No observations and <1.995, not complemented Yes asks. Equal quantities are fixed from these known observations, capped by their known sizes and $100/event. A void may pay 1.5 across three Yes or No legs; the advertised $1/$2 locks require compatible nonvoid settlement rules. No atomic or depth-based implementation is claimed.

Draw anchor: analogous three acquired-Yes observations, 5s span, |sum−1|≥5c. Underround buys both teams Yes; overround buys both No only when actual acquired-No references are available within 5s. Fixed share counts are proportional to the required-side prices. The original unspecified proportional sizing coefficient is frozen at min($100,1000×|gap|,smaller known leg notional). Entries occur after a new 3s delay. One first qualifying order per game. The 60s observation lookout is a declared causal bound.

Pairs receive a 60s entry window. Any leg failing its prescribed quantity triggers an attempted unwind of every acquired leg, using the first later opposite-side acquired print strictly after deadline+3 within 120s and before the regulation whistle. Its price 1−q is only a sale proxy, shares are capped, exit fees are charged, and all unsold residuals settle. Complete pairs settle. Each policy uses a shared print-capacity pool; independent policies reset it. Capital is $100/event including entry fees and is never recycled. Exit fees are additionally reported in total cost and ROI. No fill is required for a signal to remain in the audit.

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
      0.05921918389068915,
      -0.0037891843711104862,
      -0.0018080365747049777,
      -0.002364595449438282,
      -0.0006747820357715447,
      -0.0062338038403598195,
      -0.0001845832629642965
    ],
    "dev": {
      "n": 3032,
      "r2": 0.013983437633826812,
      "mean_abs_gap": 0.03560935259759235,
      "mean_abs_residual": 0.028745516984711967,
      "original_5c_exceedance": 0.1629287598944591,
      "residual_5c_exceedance": 0.14445910290237468
    },
    "holdout": {
      "n": 1129,
      "r2": -0.12145837016215255,
      "mean_abs_gap": 0.021550140310363147,
      "mean_abs_residual": 0.020042131994774403,
      "original_5c_exceedance": 0.09388839681133747,
      "residual_5c_exceedance": 0.0912311780336581
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
      "leader_roi": -0.00654901616393285,
      "control_roi": -0.027767646007335445,
      "difference": 0.021218629843402594,
      "difference_ci_lo": -0.03497335589355179,
      "difference_ci_hi": 0.08756772289220124,
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
      "leader_roi": 0.04856063204762412,
      "control_roi": -0.0713768581043849,
      "difference": 0.11993749015200902,
      "difference_ci_lo": 0.013335514770870182,
      "difference_ci_hi": 0.23721706129861977,
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
      "leader_roi": -0.00654901616393285,
      "control_roi": -0.004590223237421009,
      "difference": -0.001958792926511841,
      "difference_ci_lo": -0.047223521423687936,
      "difference_ci_hi": 0.04499785913626531,
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
      "leader_roi": 0.04856063204762412,
      "control_roi": -0.043666985076919365,
      "difference": 0.09222761712454348,
      "difference_ci_lo": -0.0013158101875223346,
      "difference_ci_hi": 0.18831875657267724,
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

Native asset IDs determine acquired direction; SELL complements its sold token. Extra tapes contain only ts/p_yes/size: they can provide past reference probabilities but never acquired-side detection, entry or exit capacity. References must be strictly earlier than decision (max age 600s, 120s for added time); no interpolation or invented asks. The all-three-price gate remains an explicit rejection reason. Market terminal payouts include valid 0.5 voids; no winner-only selection or future final-score/feed-consistency eligibility gate is used. Final quality flags are audit fields.

ESPN wallclocks are retrospectively stored event timestamps, not measured arrival at a free feed. Even the delayed entries may be optimistic. Regulation whistle/closure expiries analytically censor eligibility; they do not promise exchange-valid short GTD orders or guaranteed cancellation. Printed sizes bound hypothetical capacity, not available order-book depth, minimum order admissibility, or fills obtainable by us. API coverage remains incomplete and the collected universe carries legacy selection effects. ROI includes entry and exit fees; bootstrap intervals are nominal game-cluster intervals, not multiple-testing-adjusted evidence or p-values.

## Completed scope and remaining variants

Implemented: all five primary transaction protocols, red-card late branch, mandatory substitution coherence, anchor staleness, and added-time matched controls. Atomic three-book execution and soccer live depth: blocked by no captured soccer books. Detailed completed variant names and both slippage scenarios appear in results.json. Unrun optional variants: red-card matched non-card control, added draw-No leg and alternate late cuts; substitution <2c movement gate; Dutch pregame control; anchor per-leg draw-fair sizing. League/goal-type/lead/home-away splits remain available in full audits but are not claimed run. No variant is selected as a winner.

Artifacts: data/research/soccer_continuation/{signals.parquet,trades.parquet,results.json,manifest.json}; five complete canonical ledgers in data/research/ledgers. Every initial candidate is audited; trade rows include every submitted leg including no fills. Ledgers also include rejected candidates with zero capital, so candidate/leg totals differ from funded signal counts.

Rerun: `OPENBLAS_NUM_THREADS=1 .venv/bin/python -m pmsports.research.h_soccer_continuation --run`. Bounded smoke: append `--max-games 25`; smoke output is explicitly labeled and cannot be presented as full-corpus evidence.
