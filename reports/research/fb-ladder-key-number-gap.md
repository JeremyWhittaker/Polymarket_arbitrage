# fb-ladder-key-number-gap

**No executable edge established.** These are corrected historical transaction proxies, not observed fills available to a new order.

At T−10min, buy Yes on K−0.5 and No on K+0.5 for K=3/7 when the prior 10-minute Yes-price gap is below 0.6 times strictly prior-season directional margin mass. Require three signal prints per rung. One signal per event/key; $100 event capital cap, +1c per-leg stress. The pair is a $1 floor plus a $1 exact-margin bonus only when equally filled and consistently settled.

The July–September 2026 window and related variants were already inspected. Development is descriptive; the historical holdout is exploratory. Intervals are nominal game-cluster bootstrap intervals, without a multiple-testing correction.

| Window | Signals | Funded | No fill | Capital incl. fees | Net P&L | ROI | 95% game CI |
|---|---:|---:|---:|---:|---:|---:|---|
| dev | 0 | 0 | 0 | $0.00 | $0.00 | n/a | n/a to n/a |
| holdout | 0 | 0 | 0 | $0.00 | $0.00 | n/a | n/a to n/a |

## Execution and coverage

Signals use only prior/observed raw settlement timestamps, without subtracting an estimated chain lag. Entries occur strictly after the 3s eligibility delay and before expiry. An ESPN historical play clock is an optimistic observation assumption; public receipt time is unavailable here. Prints are neither asks/bids nor available depth. Each source transaction has one shared capacity allocation per strategy replay. Fees are charged from historical market metadata; no fee is silently invented.

Pair quantities and per-leg dollar budgets are fixed from signal-time references. Each leg fills independently, using only the first relevant later print and its remaining size. A missing/partial hedge remains in total P&L. After the hedge window expires, one later opposite-side print can provide a bounded sale-price proxy; any unsold residual settles. Matched-share and unmatched P&L are reported separately. No pair is labeled executable arbitrage. Event capital is capped at $100 inclusive of entry fees and is not recycled after an unwind.

The already collected moneyline and rung subsets have legacy eventual-volume and collection-budget biases. Removing volume eligibility does not backfill absent markets. No unseen market can be claimed tested. Metadata and historical tape timestamps do not prove when a market or signal first became publicly visible.

Paired positions use one ledger row per signal. Displayed prices are aggregate realized cash per total acquired share, not quotes. `exit_kind=exit_price` records that cash accounting; residual settlement determines the final exit clock. Per-leg quantities, sales and residuals remain in the saved parquet audit. All signals, including no-fills, are in the complete desk ledger. Legacy `results*.json`/`bets*.parquet` files predate this repair and are not accepted inputs to the exporters.

## Full audit results

```json
{
  "dev": {
    "signals": 0,
    "bets": 0,
    "no_fill": 0,
    "cost_usd": 0.0,
    "pnl_usd": 0.0,
    "roi": null,
    "ci_lo": null,
    "ci_hi": null
  },
  "holdout": {
    "signals": 0,
    "bets": 0,
    "no_fill": 0,
    "cost_usd": 0.0,
    "pnl_usd": 0.0,
    "roi": null,
    "ci_lo": null,
    "ci_hi": null
  },
  "coverage": {
    "parseable_rungs": 11577,
    "unknown_orientation": 1389,
    "invalid_settlement": 3,
    "missing_game": 2547,
    "validated_rungs": 7638,
    "cached_rungs": 2548,
    "missing_rungs": 5090,
    "primary_decision": "T-10min, prior 10min references; earlier T-3h run retired, not silently reused"
  },
  "prescreen": {
    "3": {
      "ladders": 20,
      "median_implied": 0.09000000435,
      "median_prior_mass": 0.07526254375729288
    },
    "7": {
      "ladders": 12,
      "median_implied": 0.07999999999999999,
      "median_prior_mass": 0.038506417736289385
    }
  },
  "sensitivity_no_slippage": {
    "dev": {
      "signals": 0,
      "bets": 0,
      "no_fill": 0,
      "cost_usd": 0.0,
      "pnl_usd": 0.0,
      "roi": null,
      "ci_lo": null,
      "ci_hi": null
    },
    "holdout": {
      "signals": 0,
      "bets": 0,
      "no_fill": 0,
      "cost_usd": 0.0,
      "pnl_usd": 0.0,
      "roi": null,
      "ci_lo": null,
      "ci_hi": null
    }
  },
  "lambda_variants": {
    "0.5": {
      "dev": {
        "signals": 0,
        "bets": 0,
        "no_fill": 0,
        "cost_usd": 0.0,
        "pnl_usd": 0.0,
        "roi": null,
        "ci_lo": null,
        "ci_hi": null
      },
      "holdout": {
        "signals": 0,
        "bets": 0,
        "no_fill": 0,
        "cost_usd": 0.0,
        "pnl_usd": 0.0,
        "roi": null,
        "ci_lo": null,
        "ci_hi": null
      }
    },
    "0.7": {
      "dev": {
        "signals": 0,
        "bets": 0,
        "no_fill": 0,
        "cost_usd": 0.0,
        "pnl_usd": 0.0,
        "roi": null,
        "ci_lo": null,
        "ci_hi": null
      },
      "holdout": {
        "signals": 0,
        "bets": 0,
        "no_fill": 0,
        "cost_usd": 0.0,
        "pnl_usd": 0.0,
        "roi": null,
        "ci_lo": null,
        "ci_hi": null
      }
    }
  },
  "execution_version": "football-causal-pairs-v2",
  "holdout_status": "previously explored; not confirmatory",
  "description": "At T\u221210min, buy Yes on K\u22120.5 and No on K+0.5 for K=3/7 when the prior 10-minute Yes-price gap is below 0.6 times strictly prior-season directional margin mass. Require three signal prints per rung. One signal per event/key; $100 event capital cap, +1c per-leg stress. The pair is a $1 floor plus a $1 exact-margin bonus only when equally filled and consistently settled."
}
```
