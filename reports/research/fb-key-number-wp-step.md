# fb-key-number-wp-step

**No executable edge established.**

**Original key-specific mechanism criterion: DEAD** (failed).

Reconstruct raw cached plays with prefix-only timestamps and score/clock quality; require both current raw scores to agree with repaired scores. Stable Q3/Q4 football leader at margin 3/6/7, positive regulation time, last prior raw price within 3c of the original frozen smooth DEV model and no older than 300s (an added causal reference-age assumption). First signal per game; no future has_price or entry-price filter. Buy strictly after 3s, before the next state or original 120s entry expiry, using one print and at most $100 actual capital. Original mandatory key-specific mechanism criterion: DEAD (failed); the non-key control and its uncertainty are reported alongside the primary leg.

The original frozen smooth model, 3/6/7 margins and leading-team direction remain unchanged. Both current raw team scores must agree with their prefix-repaired scores before eligibility; this prevents a raw 27–20 state from becoming a manufactured 27–21 six-point signal. The gate uses only the current row and its prefix, never final game quality.

The original 120-second bound limits entry expiry after the signal (also censored at the next historical state). The separate 300-second maximum age of the prior price reference is an added causal signal assumption, not a replacement of that entry bound. Entry must occur strictly after 3 seconds. Historical play/transaction clocks do not reveal actual public receipt latency, order-book depth or real cancelability.

The historical holdout has already been explored. Intervals resample whole games and are nominal, without correction for the inspected variants.

| Leg | Window | Signals | Funded | Capital incl. fees | Net P&L | ROI | 95% game CI |
|---|---|---:|---:|---:|---:|---:|---|
| Primary 3/6/7 | dev | 375 | 248 | $10783.99 | $-62.90 | -0.58% | -10.90% to +9.45% |
| Primary 3/6/7 | holdout | 57 | 39 | $1474.53 | $83.34 | +5.65% | -24.85% to +30.47% |
| Mandatory control 2/5/8/9 | dev | 182 | 111 | $5505.88 | $-311.99 | -5.67% | -20.42% to +8.79% |
| Mandatory control 2/5/8/9 | holdout | 26 | 20 | $920.89 | $4.33 | +0.47% | -30.45% to +24.82% |

## Mandatory control and provenance

Original mandatory control: if the non-key 2/5/8/9 control is also profitable, the key-specific mechanism verdict is DEAD regardless of the main leg.

This prespecified point-estimate rule is not a significance test. Wide intervals can include loss; a positive control neither proves a reliable generic leading-team edge nor supports key-specific attribution. A nonpositive control alone cannot establish the primary mechanism.

432 of 432 selected signals match both current raw scores and the raw margin. The score-agreement gate rejected 776 otherwise eligible state rows, including 157 repaired key-margin rows.

| Absolute raw margin | DEV signals / funded | Holdout signals / funded |
|---|---:|---:|
| 3 | 165 / 120 | 22 / 16 |
| 6 | 49 / 33 | 12 / 8 |
| 7 | 161 / 95 | 23 / 15 |

## Limits

Buying leaders at margins 3 and 6 is not justified by the claimed plateau-step direction; 7 aligns but must be tested separately. Original directions remain visible, not selected away.

One signal per game, at most $100 fee-inclusive capital, one strictly later size-bounded print, settlement exit. Every no-fill remains in the full ledger. The collected/matched universe retains historical selection and missing-market biases. Agreement proves consistency with the cached raw feed, not that the feed itself was correct. The frozen model is preserved for an honest test of the original rule; no retraining or choice among margins follows the holdout.

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
    "signals": 57,
    "bets": 39,
    "no_fill": 18,
    "cost_usd": 1474.5266932756665,
    "pnl_usd": 83.34160275708493,
    "roi": 0.05652091829680023,
    "ci_lo": -0.24845568075685828,
    "ci_hi": 0.3047345195451391,
    "events": 39,
    "top3_pnl_usd": 196.50021544583564,
    "drop_top3_pnl_usd": -113.15861268875072
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
      "signals": 23,
      "bets": 10,
      "no_fill": 13,
      "cost_usd": 432.4947159454976,
      "pnl_usd": 23.485250483845544,
      "roi": 0.05430182061878672,
      "ci_lo": -0.5746966434091864,
      "ci_hi": 0.5425884453325013,
      "events": 10,
      "top3_pnl_usd": 107.87904314295467,
      "drop_top3_pnl_usd": -84.39379265910912
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
    "holdout": 0.33425
  },
  "tail_mass_note": "Ordinary bootstrap tail mass is not a calibrated null-test p-value.",
  "direction_caveat": "Buying leaders at margins 3 and 6 is not justified by the claimed plateau-step direction; 7 aligns but must be tested separately. Original directions remain visible, not selected away.",
  "variants": {
    "control_2589": {
      "dev": {
        "signals": 182,
        "bets": 111,
        "no_fill": 71,
        "cost_usd": 5505.884905679442,
        "pnl_usd": -311.99181667442275,
        "roi": -0.05666515410676244,
        "ci_lo": -0.20422123422087038,
        "ci_hi": 0.08787547621418167,
        "events": 111,
        "top3_pnl_usd": 288.6792554682204,
        "drop_top3_pnl_usd": -600.6710721426432
      },
      "holdout": {
        "signals": 26,
        "bets": 20,
        "no_fill": 6,
        "cost_usd": 920.8917055087493,
        "pnl_usd": 4.327619689699404,
        "roi": 0.0046993795945947815,
        "ci_lo": -0.30447172199878936,
        "ci_hi": 0.24815225032275023,
        "events": 20,
        "top3_pnl_usd": 119.27575018043302,
        "drop_top3_pnl_usd": -114.9481304907336
      }
    },
    "neutral_other": {
      "dev": {
        "signals": 690,
        "bets": 345,
        "no_fill": 345,
        "cost_usd": 16454.00216860327,
        "pnl_usd": -159.3001125689633,
        "roi": -0.009681541969948932,
        "ci_lo": -0.0639895936634541,
        "ci_hi": 0.04164491779349105,
        "events": 345,
        "top3_pnl_usd": 194.8883529332748,
        "drop_top3_pnl_usd": -354.18846550223816
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
        "signals": 57,
        "bets": 34,
        "no_fill": 23,
        "cost_usd": 553.49564400346,
        "pnl_usd": -194.95556882054524,
        "roi": -0.3522260218895715,
        "ci_lo": -0.8673417707400081,
        "ci_hi": 0.9072747268970005,
        "events": 34,
        "top3_pnl_usd": 219.14483841160717,
        "drop_top3_pnl_usd": -414.10040723215246
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
        "signals": 52,
        "bets": 41,
        "no_fill": 11,
        "cost_usd": 1525.9014651037562,
        "pnl_usd": 31.49839413438384,
        "roi": 0.020642482397932628,
        "ci_lo": -0.2793109009673072,
        "ci_hi": 0.27113974992876305,
        "events": 41,
        "top3_pnl_usd": 196.50021544583564,
        "drop_top3_pnl_usd": -165.0018213114518
      }
    },
    "band5c": {
      "dev": {
        "signals": 409,
        "bets": 261,
        "no_fill": 148,
        "cost_usd": 11077.062155341093,
        "pnl_usd": 855.4754891792029,
        "roi": 0.07722945643730213,
        "ci_lo": -0.022679755228262273,
        "ci_hi": 0.17176613451367154,
        "events": 261,
        "top3_pnl_usd": 293.02461189140286,
        "drop_top3_pnl_usd": 562.4508772878003
      },
      "holdout": {
        "signals": 61,
        "bets": 45,
        "no_fill": 16,
        "cost_usd": 1957.3573121960758,
        "pnl_usd": 174.96571959064184,
        "roi": 0.0893887480331005,
        "ci_lo": -0.1931396398123619,
        "ci_hi": 0.34540976769899534,
        "events": 45,
        "top3_pnl_usd": 218.3224275919623,
        "drop_top3_pnl_usd": -43.356708001320456
      }
    }
  },
  "mandatory_control_criterion": {
    "criterion": "Original mandatory control: if the non-key 2/5/8/9 control is also profitable, the key-specific mechanism verdict is DEAD regardless of the main leg.",
    "status": "failed",
    "mechanism_verdict": "DEAD",
    "control_positive_point_estimate": true,
    "control_holdout": {
      "signals": 26,
      "bets": 20,
      "no_fill": 6,
      "cost_usd": 920.8917055087493,
      "pnl_usd": 4.327619689699404,
      "roi": 0.0046993795945947815,
      "ci_lo": -0.30447172199878936,
      "ci_hi": 0.24815225032275023,
      "events": 20,
      "top3_pnl_usd": 119.27575018043302,
      "drop_top3_pnl_usd": -114.9481304907336
    },
    "uncertainty": "This prespecified point-estimate rule is not a significance test. Wide intervals can include loss; a positive control neither proves a reliable generic leading-team edge nor supports key-specific attribution. A nonpositive control alone cannot establish the primary mechanism."
  },
  "raw_margin_provenance": {
    "candidate_states_before_current_score_gate": 69242,
    "rejected_current_score_disagreement": 776,
    "rejected_repaired_key_margin_states": 157,
    "eligible_states_after_current_score_gate": 68466,
    "gate": "Both current raw scores must equal their prefix-repaired values. No future rows or final game-quality flags select eligibility.",
    "selected_signals": 432,
    "selected_raw_score_and_margin_agree": 432,
    "selected_disagreement_rows": [],
    "by_absolute_raw_margin": {
      "3": {
        "dev": {
          "signals": 165,
          "funded": 120
        },
        "holdout": {
          "signals": 22,
          "funded": 16
        }
      },
      "6": {
        "dev": {
          "signals": 49,
          "funded": 33
        },
        "holdout": {
          "signals": 12,
          "funded": 8
        }
      },
      "7": {
        "dev": {
          "signals": 161,
          "funded": 95
        },
        "holdout": {
          "signals": 23,
          "funded": 15
        }
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
      "signals": 57,
      "bets": 39,
      "no_fill": 18,
      "cost_usd": 1480.6912365530125,
      "pnl_usd": 62.41092766068831,
      "roi": 0.04214985955206865,
      "ci_lo": -0.257851968823904,
      "ci_hi": 0.2858079571319177,
      "events": 39,
      "top3_pnl_usd": 188.4801709356928,
      "drop_top3_pnl_usd": -126.0692432750045
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
        "signals": 12,
        "bets": 8,
        "no_fill": 4,
        "cost_usd": 465.51032336193293,
        "pnl_usd": -158.5448348986481,
        "roi": -0.34058285486266215,
        "ci_lo": -0.8334045574579779,
        "ci_hi": 0.1861288649755287,
        "events": 8,
        "top3_pnl_usd": 43.129489724995906,
        "drop_top3_pnl_usd": -201.67432462364403
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
    "state_rows": 68466,
    "state_games": 1028,
    "legacy_subset": "eventual-volume-selected compact moneyline collection; not a causal full universe",
    "expiry": "next-state retrospective censoring, not proof a pending sports order can be canceled",
    "raw_prefix": "Raw cached plays and matched-game mapping, not the future-filtered NFL panel. Prefix-only score/clock anomaly counts; plausible nondecreasing timestamp rule can reject later states after an outlier. No final quality flag or final game length controls signals. The legacy matched/collected universe still excludes uncollected and unmatched games."
  },
  "execution_version": "football-causal-pairs-v2",
  "holdout_status": "previously explored; not confirmatory",
  "description": "Reconstruct raw cached plays with prefix-only timestamps and score/clock quality; require both current raw scores to agree with repaired scores. Stable Q3/Q4 football leader at margin 3/6/7, positive regulation time, last prior raw price within 3c of the original frozen smooth DEV model and no older than 300s (an added causal reference-age assumption). First signal per game; no future has_price or entry-price filter. Buy strictly after 3s, before the next state or original 120s entry expiry, using one print and at most $100 actual capital. Original mandatory key-specific mechanism criterion: DEAD (failed); the non-key control and its uncertainty are reported alongside the primary leg."
}
```
