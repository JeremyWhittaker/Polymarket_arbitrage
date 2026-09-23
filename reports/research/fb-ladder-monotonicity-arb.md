# fb-ladder-monotonicity-arb

**No executable edge established.** These are corrected historical transaction proxies, not observed fills available to a new order.

Scan same-side easier/harder football rungs for a 3c apparent cost gap using prints at most 120s apart; retain the first signal per rung pair. Equal-share payout is at least $1 only for consistent nonvoid contracts, but non-atomic partial execution is directional exposure.

The July–September 2026 window and related variants were already inspected. Development is descriptive; the historical holdout is exploratory. Intervals are nominal game-cluster bootstrap intervals, without a multiple-testing correction.

| Window | Signals | Funded | No fill | Capital incl. fees | Net P&L | ROI | 95% game CI |
|---|---:|---:|---:|---:|---:|---:|---|
| dev | 152 | 120 | 32 | $3284.94 | $-265.85 | -8.09% | -18.74% to +2.21% |
| holdout | 109 | 69 | 40 | $1473.06 | $-255.50 | -17.34% | -38.26% to +4.02% |

## Execution and coverage

Signals use only prior/observed raw settlement timestamps, without subtracting an estimated chain lag. Entries occur strictly after the 3s eligibility delay and before expiry. An ESPN historical play clock is an optimistic observation assumption; public receipt time is unavailable here. Prints are neither asks/bids nor available depth. Each source transaction has one shared capacity allocation per strategy replay. Fees are charged from historical market metadata; no fee is silently invented.

Pair quantities and per-leg dollar budgets are fixed from signal-time references. Each leg fills independently, using only the first relevant later print and its remaining size. A missing/partial hedge remains in total P&L. After the hedge window expires, one later opposite-side print can provide a bounded sale-price proxy; any unsold residual settles. Matched-share and unmatched P&L are reported separately. No pair is labeled executable arbitrage. Event capital is capped at $100 inclusive of entry fees and is not recycled after an unwind.

The already collected moneyline and rung subsets have legacy eventual-volume and collection-budget biases. Removing volume eligibility does not backfill absent markets. No unseen market can be claimed tested. Metadata and historical tape timestamps do not prove when a market or signal first became publicly visible.

Paired positions use one ledger row per signal. Displayed prices are aggregate realized cash per total acquired share, not quotes. `exit_kind=exit_price` records that cash accounting; residual settlement determines the final exit clock. Per-leg quantities, sales and residuals remain in the saved parquet audit. All signals, including no-fills, are in the complete desk ledger. Legacy `results*.json`/`bets*.parquet` files predate this repair and are not accepted inputs to the exporters.

## Full audit results

```json
{
  "dev": {
    "signals": 152,
    "bets": 120,
    "no_fill": 32,
    "cost_usd": 3284.9394150670805,
    "pnl_usd": -265.8544691870875,
    "roi": -0.08093131580073862,
    "ci_lo": -0.18741801424932447,
    "ci_hi": 0.022061406840714694,
    "events": 78,
    "top3_pnl_usd": 121.48274908850053,
    "drop_top3_pnl_usd": -387.33721827558793,
    "matched_pnl": 29.456375041671727,
    "unmatched_pnl": -295.3108442287592,
    "matched_shares": 886.2897078560742,
    "sold_a": 2478.785861284502,
    "sold_b": 560.9304102719439,
    "residual_a": 1778.5977719594875,
    "residual_b": 317.29655803954245
  },
  "holdout": {
    "signals": 109,
    "bets": 69,
    "no_fill": 40,
    "cost_usd": 1473.061807674726,
    "pnl_usd": -255.50114169544838,
    "roi": -0.17344903001644232,
    "ci_lo": -0.38260322485983994,
    "ci_hi": 0.04017619855663681,
    "events": 30,
    "top3_pnl_usd": 102.18651944559433,
    "drop_top3_pnl_usd": -357.68766114104267,
    "matched_pnl": -29.636609987874717,
    "unmatched_pnl": -225.86453170757363,
    "matched_shares": 209.75616827379486,
    "sold_a": 824.0879350678608,
    "sold_b": 313.2573762617775,
    "residual_a": 830.4289953961029,
    "residual_b": 646.5901684494866
  },
  "coverage": {
    "parseable_rungs": 11577,
    "unknown_orientation": 1389,
    "invalid_settlement": 3,
    "missing_game": 2547,
    "validated_rungs": 7638,
    "cached_rungs": 2548,
    "missing_rungs": 5090
  },
  "observable_pairs": 3854,
  "signal_pairs": 261,
  "void_leg_signals": 0,
  "median_observed_persistence_s": 63.0,
  "stress_1c": {
    "dev": {
      "signals": 152,
      "bets": 119,
      "no_fill": 33,
      "cost_usd": 3226.130026828856,
      "pnl_usd": -358.45813417910995,
      "roi": -0.11111087625053305,
      "ci_lo": -0.21859698925594345,
      "ci_hi": -0.00741736923319799,
      "events": 78,
      "top3_pnl_usd": 115.48111048222499,
      "drop_top3_pnl_usd": -473.93924466133484,
      "matched_pnl": -15.16347577312345,
      "unmatched_pnl": -343.29465840598647,
      "matched_shares": 804.3453099119442,
      "sold_a": 2453.8027057046415,
      "sold_b": 519.5667603086722,
      "residual_a": 1642.6847110125855,
      "residual_b": 307.8071389632516
    },
    "holdout": {
      "signals": 109,
      "bets": 69,
      "no_fill": 40,
      "cost_usd": 1480.6096930633507,
      "pnl_usd": -289.33785352924303,
      "roi": -0.1954180462851145,
      "ci_lo": -0.40029204075872715,
      "ci_hi": 0.012703684004755869,
      "events": 30,
      "top3_pnl_usd": 97.23694208821775,
      "drop_top3_pnl_usd": -386.5747956174608,
      "matched_pnl": -33.65969373865178,
      "unmatched_pnl": -255.67815979059125,
      "matched_shares": 209.24668524376096,
      "sold_a": 816.5625212053658,
      "sold_b": 302.1358015484087,
      "residual_a": 766.2709070531969,
      "residual_b": 629.9630027585742
    }
  },
  "secondary_payoff": {
    "-1": 1.0,
    "0": 0.5,
    "1": 0.0,
    "2": 1.0
  },
  "secondary_verdict": "NO(home ML)+YES(home by 1.5) pays zero at margin +1; not arbitrage. Tie adjustment cannot repair it.",
  "decay": [
    {
      "window_s": 0,
      "observable_pairs": 1374,
      "signal_pairs": 64,
      "rate": 0.046579330422125184
    },
    {
      "window_s": 5,
      "observable_pairs": 2165,
      "signal_pairs": 98,
      "rate": 0.045265588914549654
    },
    {
      "window_s": 15,
      "observable_pairs": 2720,
      "signal_pairs": 137,
      "rate": 0.05036764705882353
    },
    {
      "window_s": 30,
      "observable_pairs": 3124,
      "signal_pairs": 157,
      "rate": 0.05025608194622279
    },
    {
      "window_s": 60,
      "observable_pairs": 3497,
      "signal_pairs": 196,
      "rate": 0.0560480411781527
    },
    {
      "window_s": 120,
      "observable_pairs": 3854,
      "signal_pairs": 261,
      "rate": 0.06772184743124027
    },
    {
      "window_s": 300,
      "observable_pairs": 4291,
      "signal_pairs": 398,
      "rate": 0.09275227219762293
    },
    {
      "window_s": 900,
      "observable_pairs": 4740,
      "signal_pairs": 723,
      "rate": 0.15253164556962026
    },
    {
      "window_s": 3600,
      "observable_pairs": 5152,
      "signal_pairs": 1482,
      "rate": 0.2876552795031056
    }
  ],
  "floor_variants": {
    "0.02": {
      "dev": {
        "signals": 175,
        "bets": 136,
        "no_fill": 39,
        "cost_usd": 3654.4379869781733,
        "pnl_usd": -187.9349120970717,
        "roi": -0.051426488222467745,
        "ci_lo": -0.14309802386218515,
        "ci_hi": 0.04070890111476771,
        "events": 90,
        "top3_pnl_usd": 145.05340291513062,
        "drop_top3_pnl_usd": -332.9883150122024,
        "matched_pnl": 19.972797821813536,
        "unmatched_pnl": -207.90770991888522,
        "matched_shares": 1017.3553854053105,
        "sold_a": 2760.865396259549,
        "sold_b": 650.8144336799531,
        "residual_a": 1852.606296316953,
        "residual_b": 464.6066616785541
      },
      "holdout": {
        "signals": 126,
        "bets": 84,
        "no_fill": 42,
        "cost_usd": 1692.6446628162296,
        "pnl_usd": -216.5201534284837,
        "roi": -0.12791825607876642,
        "ci_lo": -0.3183132050328453,
        "ci_hi": 0.05008728807436834,
        "events": 35,
        "top3_pnl_usd": 102.18651944559433,
        "drop_top3_pnl_usd": -318.70667287407804,
        "matched_pnl": -23.77065393976555,
        "unmatched_pnl": -192.74949948871821,
        "matched_shares": 398.66180807456806,
        "sold_a": 805.7163640978155,
        "sold_b": 299.32366429893517,
        "residual_a": 776.3379109458614,
        "residual_b": 756.2364913786398
      }
    },
    "0.05": {
      "dev": {
        "signals": 103,
        "bets": 78,
        "no_fill": 25,
        "cost_usd": 2059.1769667572607,
        "pnl_usd": 1.5845885188829385,
        "roi": 0.0007695251765458097,
        "ci_lo": -0.13671095565601238,
        "ci_hi": 0.13997484983230726,
        "events": 59,
        "top3_pnl_usd": 111.69205543534557,
        "drop_top3_pnl_usd": -110.10746691646261,
        "matched_pnl": 61.711140909116224,
        "unmatched_pnl": -60.126552390233314,
        "matched_shares": 547.0695263760663,
        "sold_a": 2004.94862452988,
        "sold_b": 447.28434542040475,
        "residual_a": 954.0087543296102,
        "residual_b": 25.632171638180743
      },
      "holdout": {
        "signals": 68,
        "bets": 41,
        "no_fill": 27,
        "cost_usd": 838.021897171445,
        "pnl_usd": 15.07048131425353,
        "roi": 0.01798339800561365,
        "ci_lo": -0.23953499225351288,
        "ci_hi": 0.27526211169492176,
        "events": 19,
        "top3_pnl_usd": 103.30455609654985,
        "drop_top3_pnl_usd": -88.23407478229629,
        "matched_pnl": -14.387704715400124,
        "unmatched_pnl": 29.458186029653657,
        "matched_shares": 85.41691127379485,
        "sold_a": 548.5051777920669,
        "sold_b": 135.98646678234286,
        "residual_a": 502.08946392712295,
        "residual_b": 271.63318610629744
      }
    }
  },
  "execution_version": "football-causal-pairs-v2",
  "holdout_status": "previously explored; not confirmatory",
  "description": "Scan same-side easier/harder football rungs for a 3c apparent cost gap using prints at most 120s apart; retain the first signal per rung pair. Equal-share payout is at least $1 only for consistent nonvoid contracts, but non-atomic partial execution is directional exposure."
}
```
