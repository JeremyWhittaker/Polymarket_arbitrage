# fb-key-number-wp-step

**No executable edge established.** These are corrected historical transaction proxies, not observed fills available to a new order.

Reconstruct raw cached plays with prefix-only timestamps and score/clock quality. Stable Q3/Q4 football leader at margin 3/6/7, positive regulation time, last prior raw price within 3c of the original frozen smooth DEV model and no older than 300s. First signal per game; no future has_price or entry-price filter. Buy strictly after 3s, before next state/120s, using one print and at most $100 actual capital.

The July–September 2026 window and related variants were already inspected. Development is descriptive; the historical holdout is exploratory. Intervals are nominal game-cluster bootstrap intervals, without a multiple-testing correction.

| Window | Signals | Funded | No fill | Capital incl. fees | Net P&L | ROI | 95% game CI |
|---|---:|---:|---:|---:|---:|---:|---|
| dev | 375 | 248 | 127 | $10783.99 | $-62.90 | -0.58% | -10.90% to +9.45% |
| holdout | 58 | 40 | 18 | $1476.54 | $83.56 | +5.66% | -24.52% to +30.09% |

## Execution and coverage

Signals use only prior/observed raw settlement timestamps, without subtracting an estimated chain lag. Entries occur strictly after the 3s eligibility delay and before expiry. An ESPN historical play clock is an optimistic observation assumption; public receipt time is unavailable here. Prints are neither asks/bids nor available depth. Each source transaction has one shared capacity allocation per strategy replay. Fees are charged from historical market metadata; no fee is silently invented.

Pair quantities and per-leg dollar budgets are fixed from signal-time references. Each leg fills independently, using only the first relevant later print and its remaining size. A missing/partial hedge remains in total P&L. After the hedge window expires, one later opposite-side print can provide a bounded sale-price proxy; any unsold residual settles. Matched-share and unmatched P&L are reported separately. No pair is labeled executable arbitrage. Event capital is capped at $100 inclusive of entry fees and is not recycled after an unwind.

The already collected moneyline and rung subsets have legacy eventual-volume and collection-budget biases. Removing volume eligibility does not backfill absent markets. No unseen market can be claimed tested. Metadata and historical tape timestamps do not prove when a market or signal first became publicly visible.

Paired positions use one ledger row per signal. Displayed prices are aggregate realized cash per total acquired share, not quotes. `exit_kind=exit_price` records that cash accounting; residual settlement determines the final exit clock. Per-leg quantities, sales and residuals remain in the saved parquet audit. All signals, including no-fills, are in the complete desk ledger. Legacy `results*.json`/`bets*.parquet` files predate this repair and are not accepted inputs to the exporters.

## Full audit results

```json
{
  "dev": {
    "signals": 375,
    "bets": 248,
    "no_fill": 127,
    "cost_usd": 10783.989611249332,
    "pnl_usd": -62.89683408001963,
    "roi": -0.005832427176525529,
    "ci_lo": -0.10902693185968879,
    "ci_hi": 0.09448439614698984,
    "events": 248,
    "top3_pnl_usd": 266.00335002746294,
    "drop_top3_pnl_usd": -328.9001841074828
  },
  "holdout": {
    "signals": 58,
    "bets": 40,
    "no_fill": 18,
    "cost_usd": 1476.5367923085182,
    "pnl_usd": 83.55619678415431,
    "roi": 0.05658930899616588,
    "ci_lo": -0.24522628117756481,
    "ci_hi": 0.3009415031435202,
    "events": 40,
    "top3_pnl_usd": 196.50021544583564,
    "drop_top3_pnl_usd": -112.94401866168128
  },
  "frozen_coefficients": {
    "coef": [
      0.11832954011441037,
      -0.001094585277217017,
      2.1302764088988693,
      0.7853675787771434,
      -0.18791226948354953
    ],
    "intercept": -0.243246084341285,
    "features": [
      "margin",
      "sqrt_reg_s",
      "margin_over_sqrt_reg",
      "up_off_home",
      "is_cfb"
    ],
    "n_rows": 38205,
    "n_games": 822,
    "brier": 0.1394254499728765
  },
  "clock_audit": {
    "raw_play_rows": 200018,
    "prefix_clock_rows": 178699,
    "raw_games": 1112,
    "mapped_games": 1113,
    "legacy_panel_rows": 190808,
    "legacy_game_ok_excluded_games": 115
  },
  "legacy_game_quality_descriptive": {
    "False": {
      "signals": 24,
      "bets": 11,
      "no_fill": 13,
      "cost_usd": 434.50481497834943,
      "pnl_usd": 23.699844510914964,
      "roi": 0.054544492244800256,
      "ci_lo": -0.5828013613934315,
      "ci_hi": 0.5395495673652007,
      "events": 11,
      "top3_pnl_usd": 107.87904314295467,
      "drop_top3_pnl_usd": -84.17919863203971
    },
    "True": {
      "signals": 409,
      "bets": 277,
      "no_fill": 132,
      "cost_usd": 11826.0215885795,
      "pnl_usd": -3.0404818067801784,
      "roi": -0.00025710098565323765,
      "ci_lo": -0.09472254824224545,
      "ci_hi": 0.08663996385791362,
      "events": 277,
      "top3_pnl_usd": 267.0351237985948,
      "drop_top3_pnl_usd": -270.07560560537513
    }
  },
  "legacy_before_repair": {
    "stable_rows": 69664,
    "state_games": 966,
    "holdout_signals": 51,
    "holdout_funded": 36,
    "holdout_roi": 0.13967725749985077,
    "note": "Rejected intermediate result still conditioned on complete-game quality; superseded by this raw-prefix run."
  },
  "bootstrap_tail_mass_below_zero": {
    "dev": 0.55225,
    "holdout": 0.339
  },
  "tail_mass_note": "Ordinary bootstrap tail mass is not a calibrated null-test p-value.",
  "direction_caveat": "Buying leaders at margins 3 and 6 is not justified by the claimed plateau-step direction; 7 aligns but must be tested separately. Original directions remain visible, not selected away.",
  "variants": {
    "control_2589": {
      "dev": {
        "signals": 185,
        "bets": 113,
        "no_fill": 72,
        "cost_usd": 5705.884905679442,
        "pnl_usd": -391.3645543581907,
        "roi": -0.06858963347974997,
        "ci_lo": -0.21253546692363412,
        "ci_hi": 0.06997683996488584,
        "events": 113,
        "top3_pnl_usd": 288.6792554682204,
        "drop_top3_pnl_usd": -680.043809826411
      },
      "holdout": {
        "signals": 28,
        "bets": 21,
        "no_fill": 7,
        "cost_usd": 921.9232044994824,
        "pnl_usd": 3.2961206989662983,
        "roi": 0.0035752660122659367,
        "ci_lo": -0.3119942077199081,
        "ci_hi": 0.24423902910727946,
        "events": 21,
        "top3_pnl_usd": 119.27575018043302,
        "drop_top3_pnl_usd": -115.97962948146673
      }
    },
    "neutral_other": {
      "dev": {
        "signals": 694,
        "bets": 344,
        "no_fill": 350,
        "cost_usd": 16351.569366474767,
        "pnl_usd": -156.8504718304505,
        "roi": -0.009592380297883658,
        "ci_lo": -0.06773130384663256,
        "ci_hi": 0.04322053333429311,
        "events": 344,
        "top3_pnl_usd": 194.8883529332748,
        "drop_top3_pnl_usd": -351.738824763725
      },
      "holdout": {
        "signals": 93,
        "bets": 44,
        "no_fill": 49,
        "cost_usd": 1556.6250564885008,
        "pnl_usd": -139.18602758314967,
        "roi": -0.08941525578235986,
        "ci_lo": -0.32421317925364723,
        "ci_hi": 0.12525602219390017,
        "events": 44,
        "top3_pnl_usd": 74.27995799752573,
        "drop_top3_pnl_usd": -213.46598558067535
      }
    },
    "K37": {
      "dev": {
        "signals": 334,
        "bets": 218,
        "no_fill": 116,
        "cost_usd": 9368.018763569984,
        "pnl_usd": 67.8600204233313,
        "roi": 0.007243796381709113,
        "ci_lo": -0.09609483574700979,
        "ci_hi": 0.10881892498011374,
        "events": 218,
        "top3_pnl_usd": 266.00335002746294,
        "drop_top3_pnl_usd": -198.14332960413208
      },
      "holdout": {
        "signals": 52,
        "bets": 36,
        "no_fill": 16,
        "cost_usd": 1145.4128524267023,
        "pnl_usd": 224.1793407473656,
        "roi": 0.1957192467959594,
        "ci_lo": -0.043836384938987456,
        "ci_hi": 0.37943132399788687,
        "events": 36,
        "top3_pnl_usd": 196.50021544583564,
        "drop_top3_pnl_usd": 27.67912530152995
      }
    },
    "mirror": {
      "dev": {
        "signals": 375,
        "bets": 237,
        "no_fill": 138,
        "cost_usd": 7295.549254004172,
        "pnl_usd": -3187.1017529366636,
        "roi": -0.4368556282705402,
        "ci_lo": -0.6607032494869506,
        "ci_hi": -0.17481491472169314,
        "events": 237,
        "top3_pnl_usd": 997.2085782448887,
        "drop_top3_pnl_usd": -4184.310331181552
      },
      "holdout": {
        "signals": 58,
        "bets": 35,
        "no_fill": 23,
        "cost_usd": 553.7235830274204,
        "pnl_usd": -195.18350784450558,
        "roi": -0.3524926765397313,
        "ci_lo": -0.8598608256140234,
        "ci_hi": 0.8096101898165166,
        "events": 35,
        "top3_pnl_usd": 219.14483841160717,
        "drop_top3_pnl_usd": -414.32834625611275
      }
    },
    "band2c": {
      "dev": {
        "signals": 337,
        "bets": 219,
        "no_fill": 118,
        "cost_usd": 9605.092446945133,
        "pnl_usd": 326.10072424776394,
        "roi": 0.033950815783296215,
        "ci_lo": -0.06792334744695684,
        "ci_hi": 0.13618146041631884,
        "events": 219,
        "top3_pnl_usd": 275.8908077007261,
        "drop_top3_pnl_usd": 50.20991654703812
      },
      "holdout": {
        "signals": 53,
        "bets": 42,
        "no_fill": 11,
        "cost_usd": 1527.9115641366081,
        "pnl_usd": 31.71298816145326,
        "roi": 0.0207557746834475,
        "ci_lo": -0.2660041297718676,
        "ci_hi": 0.27148348355874335,
        "events": 42,
        "top3_pnl_usd": 196.50021544583564,
        "drop_top3_pnl_usd": -164.78722728438237
      }
    },
    "band5c": {
      "dev": {
        "signals": 411,
        "bets": 262,
        "no_fill": 149,
        "cost_usd": 11078.062154314977,
        "pnl_usd": 856.0139502215703,
        "roi": 0.07727109112563946,
        "ci_lo": -0.025561017156902097,
        "ci_hi": 0.17168707839265945,
        "events": 262,
        "top3_pnl_usd": 293.02461189140286,
        "drop_top3_pnl_usd": 562.9893383301674
      },
      "holdout": {
        "signals": 62,
        "bets": 46,
        "no_fill": 16,
        "cost_usd": 1959.3674112289273,
        "pnl_usd": 175.18031361771128,
        "roi": 0.08940656694286706,
        "ci_lo": -0.19482342780014691,
        "ci_hi": 0.34252274834065294,
        "events": 46,
        "top3_pnl_usd": 218.3224275919623,
        "drop_top3_pnl_usd": -43.14211397425099
      }
    }
  },
  "stress_1c": {
    "dev": {
      "signals": 375,
      "bets": 246,
      "no_fill": 129,
      "cost_usd": 10705.097245477422,
      "pnl_usd": -207.63604616614276,
      "roi": -0.019395998131064393,
      "ci_lo": -0.1181859026280224,
      "ci_hi": 0.07703980042949016,
      "events": 246,
      "top3_pnl_usd": 255.48782579529774,
      "drop_top3_pnl_usd": -463.1238719614403
    },
    "holdout": {
      "signals": 58,
      "bets": 40,
      "no_fill": 18,
      "cost_usd": 1482.7226837404783,
      "pnl_usd": 62.60417353314374,
      "roi": 0.0422224426857837,
      "ci_lo": -0.25363399615088966,
      "ci_hi": 0.2824652562221399,
      "events": 40,
      "top3_pnl_usd": 188.4801709356928,
      "drop_top3_pnl_usd": -125.87599740254903
    }
  },
  "actual_margin_partition": {
    "3": {
      "dev": {
        "signals": 165,
        "bets": 120,
        "no_fill": 45,
        "cost_usd": 4657.889747066643,
        "pnl_usd": -359.19344369817486,
        "roi": -0.07711505922276947,
        "ci_lo": -0.2579661927831307,
        "ci_hi": 0.10368804087541349,
        "events": 120,
        "top3_pnl_usd": 266.00335002746294,
        "drop_top3_pnl_usd": -625.1967937256377
      },
      "holdout": {
        "signals": 22,
        "bets": 16,
        "no_fill": 6,
        "cost_usd": 503.2745150971749,
        "pnl_usd": 214.56479868297322,
        "roi": 0.42633750020412586,
        "ci_lo": -0.04230806720462342,
        "ci_hi": 0.6316726285890907,
        "events": 16,
        "top3_pnl_usd": 196.50021544583564,
        "drop_top3_pnl_usd": 18.064583237137537
      }
    },
    "6": {
      "dev": {
        "signals": 49,
        "bets": 33,
        "no_fill": 16,
        "cost_usd": 1494.8284448470743,
        "pnl_usd": -114.90732743783063,
        "roi": -0.07686990960998605,
        "ci_lo": -0.35580343126672115,
        "ci_hi": 0.17456395834799093,
        "events": 33,
        "top3_pnl_usd": 100.12614898773163,
        "drop_top3_pnl_usd": -215.0334764255623
      },
      "holdout": {
        "signals": 13,
        "bets": 9,
        "no_fill": 4,
        "cost_usd": 467.52042239478476,
        "pnl_usd": -158.33024087157872,
        "roi": -0.3386595179319911,
        "ci_lo": -0.8573228435407994,
        "ci_hi": 0.18935187143632468,
        "events": 9,
        "top3_pnl_usd": 43.129489724995906,
        "drop_top3_pnl_usd": -201.45973059657462
      }
    },
    "7": {
      "dev": {
        "signals": 161,
        "bets": 95,
        "no_fill": 66,
        "cost_usd": 4631.271419335613,
        "pnl_usd": 411.2039370559859,
        "roi": 0.0887885636197448,
        "ci_lo": -0.01873937571080219,
        "ci_hi": 0.18154275066192294,
        "events": 95,
        "top3_pnl_usd": 159.90770015668048,
        "drop_top3_pnl_usd": 251.29623689930543
      },
      "holdout": {
        "signals": 23,
        "bets": 15,
        "no_fill": 8,
        "cost_usd": 505.74185481655877,
        "pnl_usd": 27.321638972759864,
        "roi": 0.0540228947091395,
        "ci_lo": -0.26923147645781187,
        "ci_hi": 0.19655152829239023,
        "events": 15,
        "top3_pnl_usd": 57.42268020022256,
        "drop_top3_pnl_usd": -30.101041227462694
      }
    }
  },
  "coverage": {
    "state_rows": 69242,
    "state_games": 1030,
    "legacy_subset": "eventual-volume-selected compact moneyline collection; not a causal full universe",
    "expiry": "next-state retrospective censoring, not proof a pending sports order can be canceled",
    "raw_prefix": "Raw cached plays and matched-game mapping, not the future-filtered NFL panel. Prefix-only score/clock anomaly counts; plausible nondecreasing timestamp rule can reject later states after an outlier. No final quality flag or final game length controls signals. The legacy matched/collected universe still excludes uncollected and unmatched games."
  },
  "execution_version": "football-causal-pairs-v2",
  "holdout_status": "previously explored; not confirmatory",
  "description": "Reconstruct raw cached plays with prefix-only timestamps and score/clock quality. Stable Q3/Q4 football leader at margin 3/6/7, positive regulation time, last prior raw price within 3c of the original frozen smooth DEV model and no older than 300s. First signal per game; no future has_price or entry-price filter. Buy strictly after 3s, before next state/120s, using one print and at most $100 actual capital."
}
```
