# Esports between-map overreaction — corrected execution

The original frozen round1 rule does not establish an edge. In the already-inspected July–September evaluation,60signals produced59partial-or-full entries, deploying$1,235.69 and losing$220.84:−17.87% after fees,95%game-cluster interval[−73.48%,+46.13%]. With an additional1c price penalty,ROI is−21.37%. The later round2 refit is a separately labeled exploratory sensitivity, never substituted for the original rule.

Signal: map1-trailing team, frozen4point gap between break price and round1 calibrated probability. Entry requires a real same-side transaction strictly after3s, before the fixed entry window expires. Shares are bounded by the first relevant print and a$100 fee-inclusive target; no quote fallback and no artificial unit-stake rescaling. All selected no-fills remain. Historical public receipt and resting depth are unobserved, so this is a transaction-capacity proxy. Legacy series discovery and tape windows remain incomplete.

ROI below is a fraction of actually deployed capital including fees, not an equal-weighted average of unconstrained unit trades. Positive development results and alternate-model point estimates do not constitute independent validation.

| model              | period   | rule                             | cost        |   signals |   bets |   partial |   capital_usd |    pnl_usd |      roi |    ci_lo |    ci_hi |
|:-------------------|:---------|:---------------------------------|:------------|----------:|-------:|----------:|--------------:|-----------:|---------:|---------:|---------:|
| frozen_round1      | dev      | primary_thr0.04                  | fee         |       109 |    109 |   96.0000 |     2381.5692 |  1528.8422 |   0.6419 |  -0.3319 |   1.9539 |
| frozen_round1      | dev      | primary_thr0.04                  | fee_plus_1c |       109 |    109 |   94.0000 |     2419.9451 |  1252.3222 |   0.5175 |  -0.3499 |   1.6515 |
| frozen_round1      | dev      | a_thr0.03                        | fee         |       153 |    153 |  136.0000 |     3182.3062 |  1546.4082 |   0.4859 |  -0.2530 |   1.5121 |
| frozen_round1      | dev      | a_thr0.03                        | fee_plus_1c |       153 |    153 |  134.0000 |     3238.2419 |  1240.7988 |   0.3832 |  -0.2763 |   1.2732 |
| frozen_round1      | dev      | a_thr0.06                        | fee         |        43 |     43 |   40.0000 |      758.1145 |   377.6857 |   0.4982 |  -0.3620 |   1.4697 |
| frozen_round1      | dev      | a_thr0.06                        | fee_plus_1c |        43 |     43 |   39.0000 |      772.7108 |   341.8643 |   0.4424 |  -0.3777 |   1.3688 |
| frozen_round1      | dev      | b_mirror_buyL_thr0.04            | fee         |       376 |    376 |  305.0000 |    12890.2364 |   399.3087 |   0.0310 |  -0.0792 |   0.1424 |
| frozen_round1      | dev      | b_mirror_buyL_thr0.04            | fee_plus_1c |       376 |    376 |  301.0000 |    12982.1464 |   207.5625 |   0.0160 |  -0.0923 |   0.1250 |
| frozen_round1      | dev      | d_placebo_buyT                   | fee         |       413 |    412 |  350.0000 |    11804.6388 | -3342.8938 |  -0.2832 |  -0.4710 |  -0.0771 |
| frozen_round1      | dev      | d_placebo_buyT                   | fee_plus_1c |       413 |    412 |  344.0000 |    12025.1547 | -3716.1488 |  -0.3090 |  -0.4875 |  -0.1116 |
| frozen_round1      | dev      | d_placebo_mirror_buyL            | fee         |        16 |     16 |   14.0000 |      374.2670 |   -54.4060 |  -0.1454 |  -0.8341 |   0.3884 |
| frozen_round1      | dev      | d_placebo_mirror_buyL            | fee_plus_1c |        16 |     16 |   14.0000 |      377.4965 |   -59.4497 |  -0.1575 |  -0.8359 |   0.3659 |
| frozen_round1      | dev      | x_crossfit_primary               | fee         |       103 |    103 |   91.0000 |     2204.5748 |   406.3160 |   0.1843 |  -0.7543 |   1.6863 |
| frozen_round1      | dev      | x_crossfit_primary               | fee_plus_1c |       103 |    103 |   89.0000 |     2240.0542 |   155.6068 |   0.0695 |  -0.7607 |   1.3496 |
| frozen_round1      | dev      | x_crossfit_mirror                | fee         |       381 |    381 |  309.0000 |    12956.7877 |  -137.0587 |  -0.0106 |  -0.1223 |   0.1011 |
| frozen_round1      | dev      | x_crossfit_mirror                | fee_plus_1c |       381 |    381 |  305.0000 |    13046.9258 |  -322.1169 |  -0.0247 |  -0.1347 |   0.0853 |
| frozen_round1      | dev      | x_all_trailers_no_signal         | fee         |      1199 |   1197 | 1079.0000 |    26204.5286 | -5146.3821 |  -0.1964 |  -0.3482 |  -0.0181 |
| frozen_round1      | dev      | x_all_trailers_no_signal         | fee_plus_1c |      1199 |   1197 | 1063.0000 |    26881.3878 | -6288.8474 |  -0.2339 |  -0.3758 |  -0.0729 |
| frozen_round1      | dev      | x_all_leaders_no_signal          | fee         |      1199 |   1198 |  948.0000 |    43732.5045 |  1169.2802 |   0.0267 |  -0.0189 |   0.0721 |
| frozen_round1      | dev      | x_all_leaders_no_signal          | fee_plus_1c |      1199 |   1196 |  936.0000 |    43775.1330 |   604.6971 |   0.0138 |  -0.0313 |   0.0583 |
| frozen_round1      | dev      | s_primary_entry_next_print_nocap | fee         |       109 |    109 |   96.0000 |     2381.5692 |  1528.8422 |   0.6419 |  -0.3319 |   1.9539 |
| frozen_round1      | dev      | s_primary_entry_next_print_nocap | fee_plus_1c |       109 |    109 |   94.0000 |     2419.9451 |  1252.3222 |   0.5175 |  -0.3499 |   1.6515 |
| frozen_round1      | dev      | s_primary_entry_quote_all        | fee         |       109 |      0 |    0.0000 |        0.0000 |     0.0000 | nan      | nan      | nan      |
| frozen_round1      | dev      | s_primary_entry_quote_all        | fee_plus_1c |       109 |      0 |    0.0000 |        0.0000 |     0.0000 | nan      | nan      | nan      |
| frozen_round1      | dev      | s_mirror_entry_next_print_nocap  | fee         |       376 |    376 |  305.0000 |    12890.2364 |   399.3087 |   0.0310 |  -0.0792 |   0.1424 |
| frozen_round1      | dev      | s_mirror_entry_next_print_nocap  | fee_plus_1c |       376 |    376 |  301.0000 |    12982.1464 |   207.5625 |   0.0160 |  -0.0923 |   0.1250 |
| frozen_round1      | dev      | s_mirror_entry_quote_all         | fee         |       376 |      0 |    0.0000 |        0.0000 |     0.0000 | nan      | nan      | nan      |
| frozen_round1      | dev      | s_mirror_entry_quote_all         | fee_plus_1c |       376 |      0 |    0.0000 |        0.0000 |     0.0000 | nan      | nan      | nan      |
| frozen_round1      | holdout  | primary_thr0.04                  | fee         |        60 |     59 |   53.0000 |     1235.6862 |  -220.8423 |  -0.1787 |  -0.7348 |   0.4613 |
| frozen_round1      | holdout  | primary_thr0.04                  | fee_plus_1c |        60 |     59 |   53.0000 |     1269.3816 |  -271.3256 |  -0.2137 |  -0.7401 |   0.4006 |
| frozen_round1      | holdout  | a_thr0.03                        | fee         |        82 |     81 |   74.0000 |     1533.4489 |   369.9563 |   0.2413 |  -0.3706 |   0.9170 |
| frozen_round1      | holdout  | a_thr0.03                        | fee_plus_1c |        82 |     81 |   74.0000 |     1578.6192 |   293.3481 |   0.1858 |  -0.3981 |   0.8345 |
| frozen_round1      | holdout  | a_thr0.06                        | fee         |        22 |     21 |   21.0000 |      136.9242 |    78.7092 |   0.5748 |  -0.5553 |   1.8714 |
| frozen_round1      | holdout  | a_thr0.06                        | fee_plus_1c |        22 |     21 |   21.0000 |      144.0537 |    71.5797 |   0.4969 |  -0.5761 |   1.7359 |
| frozen_round1      | holdout  | b_mirror_buyL_thr0.04            | fee         |       225 |    225 |  200.0000 |     6007.6064 |   255.4310 |   0.0425 |  -0.1358 |   0.2126 |
| frozen_round1      | holdout  | b_mirror_buyL_thr0.04            | fee_plus_1c |       225 |    225 |  200.0000 |     6064.6041 |   146.9280 |   0.0242 |  -0.1499 |   0.1904 |
| frozen_round1      | holdout  | d_placebo_buyT                   | fee         |       246 |    245 |  219.0000 |     5511.5647 | -1751.6900 |  -0.3178 |  -0.5840 |  -0.0001 |
| frozen_round1      | holdout  | d_placebo_buyT                   | fee_plus_1c |       246 |    245 |  219.0000 |     5632.1799 | -1911.1406 |  -0.3393 |  -0.5948 |  -0.0353 |
| frozen_round1      | holdout  | d_placebo_mirror_buyL            | fee         |        12 |     12 |   11.0000 |      348.8609 |   -55.8554 |  -0.1601 |  -0.6964 |   0.2698 |
| frozen_round1      | holdout  | d_placebo_mirror_buyL            | fee_plus_1c |        12 |     12 |   11.0000 |      353.4961 |   -61.7067 |  -0.1746 |  -0.7032 |   0.2534 |
| frozen_round1      | holdout  | x_crossfit_primary               | fee         |         0 |      0 |  nan      |      nan      |   nan      | nan      | nan      | nan      |
| frozen_round1      | holdout  | x_crossfit_primary               | fee_plus_1c |         0 |      0 |  nan      |      nan      |   nan      | nan      | nan      | nan      |
| frozen_round1      | holdout  | x_crossfit_mirror                | fee         |         0 |      0 |  nan      |      nan      |   nan      | nan      | nan      | nan      |
| frozen_round1      | holdout  | x_crossfit_mirror                | fee_plus_1c |         0 |      0 |  nan      |      nan      |   nan      | nan      | nan      | nan      |
| frozen_round1      | holdout  | x_all_trailers_no_signal         | fee         |       668 |    664 |  593.0000 |    15306.2462 | -3803.7495 |  -0.2485 |  -0.4284 |  -0.0587 |
| frozen_round1      | holdout  | x_all_trailers_no_signal         | fee_plus_1c |       668 |    663 |  586.0000 |    15625.5227 | -4332.8064 |  -0.2773 |  -0.4508 |  -0.0872 |
| frozen_round1      | holdout  | x_all_leaders_no_signal          | fee         |       668 |    664 |  556.0000 |    21441.2671 |  -425.2764 |  -0.0198 |  -0.0922 |   0.0458 |
| frozen_round1      | holdout  | x_all_leaders_no_signal          | fee_plus_1c |       668 |    662 |  554.0000 |    21571.5124 |  -705.4362 |  -0.0327 |  -0.1013 |   0.0315 |
| frozen_round1      | holdout  | s_primary_entry_next_print_nocap | fee         |        60 |     60 |   54.0000 |     1237.7722 |  -208.6426 |  -0.1686 |  -0.7125 |   0.4939 |
| frozen_round1      | holdout  | s_primary_entry_next_print_nocap | fee_plus_1c |        60 |     60 |   54.0000 |     1271.6155 |  -259.2738 |  -0.2039 |  -0.7219 |   0.4271 |
| frozen_round1      | holdout  | s_primary_entry_quote_all        | fee         |        60 |      0 |    0.0000 |        0.0000 |     0.0000 | nan      | nan      | nan      |
| frozen_round1      | holdout  | s_primary_entry_quote_all        | fee_plus_1c |        60 |      0 |    0.0000 |        0.0000 |     0.0000 | nan      | nan      | nan      |
| frozen_round1      | holdout  | s_mirror_entry_next_print_nocap  | fee         |       225 |    225 |  200.0000 |     6007.6064 |   255.4310 |   0.0425 |  -0.1358 |   0.2126 |
| frozen_round1      | holdout  | s_mirror_entry_next_print_nocap  | fee_plus_1c |       225 |    225 |  200.0000 |     6064.6041 |   146.9280 |   0.0242 |  -0.1499 |   0.1904 |
| frozen_round1      | holdout  | s_mirror_entry_quote_all         | fee         |       225 |      0 |    0.0000 |        0.0000 |     0.0000 | nan      | nan      | nan      |
| frozen_round1      | holdout  | s_mirror_entry_quote_all         | fee_plus_1c |       225 |      0 |    0.0000 |        0.0000 |     0.0000 | nan      | nan      | nan      |
| exploratory_round2 | dev      | primary                          | fee         |        89 |     89 |   79.0000 |     1916.1089 |  1928.9125 |   1.0067 |  -0.1593 |   2.6142 |
| exploratory_round2 | dev      | primary                          | fee_plus_1c |        89 |     89 |   77.0000 |     1946.2719 |  1660.6054 |   0.8532 |  -0.1859 |   2.2223 |
| exploratory_round2 | dev      | mirror                           | fee         |       380 |    380 |  306.0000 |    13158.6706 |   476.6061 |   0.0362 |  -0.0781 |   0.1456 |
| exploratory_round2 | dev      | mirror                           | fee_plus_1c |       380 |    380 |  302.0000 |    13249.9400 |   279.7973 |   0.0211 |  -0.0912 |   0.1283 |
| exploratory_round2 | holdout  | primary                          | fee         |        53 |     52 |   48.0000 |      965.6874 |   299.1565 |   0.3098 |  -0.4783 |   1.1544 |
| exploratory_round2 | holdout  | primary                          | fee_plus_1c |        53 |     52 |   48.0000 |      995.8020 |   252.2540 |   0.2533 |  -0.4962 |   1.0581 |
| exploratory_round2 | holdout  | mirror                           | fee         |       230 |    230 |  203.0000 |     6275.5835 |   330.1745 |   0.0526 |  -0.1159 |   0.2122 |
| exploratory_round2 | holdout  | mirror                           | fee_plus_1c |       230 |    230 |  203.0000 |     6333.4146 |   217.6685 |   0.0344 |  -0.1305 |   0.1900 |

## Concentration and calibration checks

dev frozen-primary robustness (unchanged output from the corrected run):

```json
{
  "drop_top_2": {
    "signals": 107,
    "bets": 107,
    "unfilled": 0,
    "partial": 95,
    "roi": -0.0544,
    "ci_lo": -0.5148,
    "ci_hi": 0.4382,
    "capital_usd": 2183.1352527401195,
    "pnl_usd": -118.77788277876223,
    "win": 0.32710280373831774,
    "avg_q": 0.2499871401869159,
    "avg_fee_rate": 0.01850467289719626,
    "first_print_usd": 9200.979281058135,
    "window_cap_usd": 9200.979281058135,
    "median_window_cap_usd": 5.8
  },
  "drop_top_3": {
    "signals": 106,
    "bets": 106,
    "unfilled": 0,
    "partial": 95,
    "roi": -0.183,
    "ci_lo": -0.5895,
    "ci_hi": 0.2668,
    "capital_usd": 2083.13525274012,
    "pnl_usd": -381.2109685702977,
    "win": 0.32075471698113206,
    "avg_q": 0.2497983396226415,
    "avg_fee_rate": 0.01839622641509434,
    "first_print_usd": 9023.664888968293,
    "window_cap_usd": 9023.664888968293,
    "median_window_cap_usd": 5.734999999999999
  },
  "drop_top_5": {
    "signals": 104,
    "bets": 104,
    "unfilled": 0,
    "partial": 95,
    "roi": -0.4003,
    "ci_lo": -0.71,
    "ci_hi": 0.0051,
    "capital_usd": 1883.1352527401195,
    "pnl_usd": -753.851703568774,
    "win": 0.3076923076923077,
    "avg_q": 0.24801252884615385,
    "avg_fee_rate": 0.018173076923076924,
    "first_print_usd": 5314.273125732452,
    "window_cap_usd": 5314.273125732452,
    "median_window_cap_usd": 5.46
  }
}
```

holdout frozen-primary robustness (unchanged output from the corrected run):

```json
{
  "drop_top_1": {
    "signals": 59,
    "bets": 58,
    "unfilled": 1,
    "partial": 53,
    "roi": -0.3814,
    "ci_lo": -0.804,
    "ci_hi": 0.2414,
    "capital_usd": 1135.6862356366253,
    "pnl_usd": -433.1314537279156,
    "win": 0.20689655172413793,
    "avg_q": 0.21639043103448274,
    "avg_fee_rate": 0.04793103448275861,
    "first_print_usd": 4611.836560730625,
    "window_cap_usd": 4611.836560730625,
    "median_window_cap_usd": 6.40499972820282
  },
  "drop_top_3": {
    "signals": 57,
    "bets": 56,
    "unfilled": 1,
    "partial": 52,
    "roi": -0.6987,
    "ci_lo": -0.9162,
    "ci_hi": -0.3034,
    "capital_usd": 1004.5312353197003,
    "pnl_usd": -701.8213478151226,
    "win": 0.17857142857142858,
    "avg_q": 0.2135889107142857,
    "avg_fee_rate": 0.047857142857142855,
    "first_print_usd": 4097.923958287266,
    "window_cap_usd": 4097.923958287266,
    "median_window_cap_usd": 5.730800132751465
  },
  "drop_top_5": {
    "signals": 55,
    "bets": 54,
    "unfilled": 1,
    "partial": 50,
    "roi": -0.8562,
    "ci_lo": -0.9558,
    "ci_hi": -0.662,
    "capital_usd": 959.3099303565884,
    "pnl_usd": -821.3300462089444,
    "win": 0.14814814814814814,
    "avg_q": 0.2111292407407408,
    "avg_fee_rate": 0.04777777777777776,
    "first_print_usd": 4054.2684571123386,
    "window_cap_usd": 4054.2684571123386,
    "median_window_cap_usd": 5.459900112152099
  }
}
```

Original calibration file: `data/research/h_esports_break_overreaction/calib_v1.json`. Full machine evidence: `results_holdout.json`, `bets_dev.parquet`, `bets_holdout.parquet`; canonical desk ledger contains169primary signal rows. The standalone exporter verifies original calibration, actual cash, fees, clocks and first-print shares. Reproduce: `.venv/bin/python -m pmsports.research.h_esports_break_overreaction analyze --holdout --rebuild`, then `.venv/bin/python -m pmsports.research.ledger_esports_break_overreaction`. All periods have already been inspected; fresh prospective data is required.
