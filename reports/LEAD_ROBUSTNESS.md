# Lead robustness and canonical-ledger acceptance

Independent validation dated 2026-09-23 UTC. These are the existing fixed threshold rules; no new threshold was introduced or fit.

The existing **10c first/game inning-discount rule is the stronger lead**: positive in both seasons and both directions, and still positive after removing the best 1% of funded games. Its equal-game return is much smaller, and each season alone has a confidence interval crossing zero. The adjusted 8c leader rule has negative equal-game return, a confidence bound essentially at zero, and a negative adjacent 10c rule. Neither is confirmed as an executable edge. Both were selected from previously inspected grids and still rely on optimistic historical state/print execution.

## Actual execution and inference

Capital below is the sum of allocated historical cash including fees, not simultaneous account equity, recommended stake or achievable live profit. Every signal and no-fill remains in the canonical ledger. 95% CI uses a 2000-resample whole-game bootstrap, seed 0; intervals are nominal and unadjusted for the inspected hypotheses and threshold grids.

| Existing rule | Signals / funded / partial | Capital | PnL | Cash ROI | Accepted numeric-game 95% CI | Equal-game ROI / 95% CI |
|---|---:|---:|---:|---:|---|---|
| 10c inning discount, first/game | 1,426 / 1,229 / 984 | $40,559.49 | $2,969.81 | +7.32% | +1.03% to +13.52% | +2.27% / -1.85% to +6.45% |
| Adjusted 8c leader, first/game | 891 / 875 / 759 | $21,326.40 | $1,394.10 | +6.54% | +0.07% to +12.89% | -1.77% / -6.29% to +2.60% |

Numeric-game CI ordering exactly reproduces the accepted threshold report. The desk ledgers use event slugs as cluster identifiers, so fixed-seed bootstrap draws differ after sorting those identifiers. The cash estimand and totals are identical. Event-slug intervals are **+0.64% to +13.71%** for 10c and **−0.042% to +12.986%** for adjusted 8c. The adjusted 8c lower bound changing sign from accepted +0.068% under this harmless permutation is Monte Carlo fragility, not new evidence for or against the strategy. Its nominal “significant” label should carry no weight.

## Concentration

Removal ranks funded whole games by realized dollar PnL, then excludes those games and preserves every remaining no-fill. Top 1% means the ceiling of 1% of funded games. These are descriptive stress tests, not newly selected trading rules.

| Rule | Removed best games | Remaining capital | Remaining PnL | Remaining cash ROI | Removed share of original net profit |
|---|---:|---:|---:|---:|---:|
| 10c inning discount, first/game | 1 (best 1) | $40,459.49 | $2,806.66 | +6.94% | 5.5% |
| 10c inning discount, first/game | 3 (best 3) | $40,259.49 | $2,480.34 | +6.16% | 16.5% |
| 10c inning discount, first/game | 13 (top1pct) | $39,273.49 | $1,204.98 | +3.07% | 59.4% |
| Adjusted 8c leader, first/game | 1 (best 1) | $21,226.40 | $1,270.58 | +5.99% | 8.9% |
| Adjusted 8c leader, first/game | 3 (best 3) | $21,029.55 | $1,058.87 | +5.04% | 24.0% |
| Adjusted 8c leader, first/game | 9 (top1pct) | $20,435.50 | $572.14 | +2.80% | 59.0% |

After best 3 removal the 10c interval crosses zero; both top 1%-removed intervals cross zero. Positive remaining point estimates do not establish an edge.

## Season and direction

| Rule / split | Funded games | Capital | PnL | Cash ROI | Cash 95% CI, event-slug ordering | Equal-game ROI |
|---|---:|---:|---:|---:|---|---:|
| 10c inning discount, first/game / 2025 | 634 | $25,936.62 | $1,915.08 | +7.38% | -0.49% to +15.39% | +3.25% |
| 10c inning discount, first/game / 2026 | 595 | $14,622.88 | $1,054.73 | +7.21% | -3.30% to +17.04% | +1.23% |
| 10c inning discount, first/game / away | 732 | $23,919.39 | $2,341.86 | +9.79% | +1.12% to +19.11% | +3.26% |
| 10c inning discount, first/game / home | 497 | $16,640.10 | $627.95 | +3.77% | -4.95% to +12.46% | +0.82% |
| Adjusted 8c leader, first/game / 2026 | 875 | $21,326.40 | $1,394.10 | +6.54% | -0.04% to +12.99% | -1.77% |
| Adjusted 8c leader, first/game / away | 469 | $11,088.08 | $744.38 | +6.71% | -3.89% to +16.65% | -1.26% |
| Adjusted 8c leader, first/game / home | 406 | $10,238.32 | $649.72 | +6.35% | -2.86% to +14.83% | -2.37% |

The 10c rule is positive in all four year×direction cash splits: 2025 away +10.39%, 2025 home +3.11%, 2026 away +8.73%, 2026 home +4.98%. Every corresponding CI crosses zero. Its 2026 home equal-game ROI is −3.27%. All monthly and combined splits, without selection, are retained in the JSON. Adjusted 8c has only 2026 trading results; 2025 is its model-fit period and must not be presented as a second trading replication.

## Capacity and clock limits

| Rule | Median / mean funded allocation | No-fills | Full 100-dollar fills | Funded below 5 dollars | Effective capital-weighted games |
|---|---:|---:|---:|---:|---:|
| 10c inning discount, first/game | $10.67 / $33.00 | 197 | 245 | 397 | 521.5 |
| Adjusted 8c leader, first/game | $6.47 / $24.37 | 16 | 116 | 386 | 293.3 |

For both leads every funded entry is strictly later than eligible time and before expiry, every no-fill has a null entry timestamp, and every game stays within 100 inclusive dollars (floating-point tolerance). The 10c minimum entry lag is 5.001s; adjusted 8c is 5.002s. These are clocks in the historical records, not measured public receipt latency.

The unadjusted baseline excludes each game’s season. The adjusted logistic fit uses only pre-2026 states and labels, with prior-season expectancy, pregame strength and time remaining; 2026 evaluation does not refit it. At half-inning starts the prior price may be up to 120s old. The simulator allocates once at the first eligible same-side print after state time + 5s, plus 1c and historical fees; every-signal rules share print capacity and a 100-dollar game cap. No future minimum hedge size or synthetic ask is used.

Next-state expiry implicitly requires timely receipt and cancellation. Later trades are price/capacity proxies, not executable bid/ask quotes. Printed fragments below venue minimums may be unusable, and inferred capacity cannot be scaled as real liquidity. A prospective paper experiment must freeze the rule and model before new games, record actual local state receipt and order-book depth/constraints, apply delay from receipt, cancel only on received updates, retain every rejection/no-fill, and hold funded positions to settlement. This audit changes no trading rule and places no live orders.

## Complete fixed grid and reconciliation

All 12 existing unadjusted rows and 10 existing adjusted rows now have unique full ledgers. Each is independently selected from the full checkpoint panel; a first 10c crossing can occur after the first 3c crossing. The 3c compatibility alias remains unchanged. Every rule reconciled to accepted CSV signals, funded rows, unfilled, partial, capital and PnL before any grid output was published (rtol 1e−10,atol 1e−6).

| Canonical slug | Signals | Funded | Unfilled | Capital | PnL | Cash ROI |
|---|---:|---:|---:|---:|---:|---:|
| `mlb_inning_adjusted_02c_leader` | 2,020 | 1,957 | 63 | $50,669.41 | $990.96 | +1.96% |
| `mlb_inning_adjusted_02c_trailer` | 1,482 | 1,430 | 52 | $26,157.97 | -$3,049.75 | -11.66% |
| `mlb_inning_adjusted_03c_leader` | 1,896 | 1,837 | 59 | $48,181.54 | $650.99 | +1.35% |
| `mlb_inning_adjusted_03c_trailer` | 1,117 | 1,087 | 30 | $19,298.42 | -$2,329.38 | -12.07% |
| `mlb_inning_adjusted_05c_leader` | 1,525 | 1,484 | 41 | $39,669.59 | $1,712.72 | +4.32% |
| `mlb_inning_adjusted_05c_trailer` | 531 | 521 | 10 | $9,068.39 | -$963.53 | -10.63% |
| `mlb_inning_adjusted_08c_leader` | 891 | 875 | 16 | $21,326.40 | $1,394.10 | +6.54% |
| `mlb_inning_adjusted_08c_trailer` | 156 | 151 | 5 | $2,161.06 | $265.85 | +12.30% |
| `mlb_inning_adjusted_10c_leader` | 580 | 572 | 8 | $14,304.44 | -$82.02 | -0.57% |
| `mlb_inning_adjusted_10c_trailer` | 78 | 76 | 2 | $1,252.05 | -$70.68 | -5.65% |
| `mlb_inning_discount_00c_every` | 21,406 | 10,672 | 10,734 | $300,970.39 | $74.85 | +0.02% |
| `mlb_inning_discount_00c_first` | 4,086 | 3,443 | 643 | $115,306.08 | -$840.11 | -0.73% |
| `mlb_inning_discount_02c_every` | 14,131 | 8,148 | 5,983 | $231,120.28 | $1,190.26 | +0.51% |
| `mlb_inning_discount_02c_first` | 3,553 | 3,045 | 508 | $102,056.71 | $77.85 | +0.08% |
| `mlb_inning_discount_03c_every` | 11,595 | 6,981 | 4,614 | $200,149.73 | $2,291.90 | +1.15% |
| `mlb_inning_discount_03c_first` | 3,267 | 2,824 | 443 | $94,351.83 | $399.65 | +0.42% |
| `mlb_inning_discount_05c_every` | 8,046 | 5,086 | 2,960 | $150,391.55 | $3,013.07 | +2.00% |
| `mlb_inning_discount_05c_first` | 2,723 | 2,350 | 373 | $80,472.18 | $2,638.88 | +3.28% |
| `mlb_inning_discount_08c_every` | 4,566 | 3,092 | 1,474 | $92,929.16 | $2,621.37 | +2.82% |
| `mlb_inning_discount_08c_first` | 1,916 | 1,651 | 265 | $55,252.60 | $2,732.42 | +4.95% |
| `mlb_inning_discount_10c_every` | 3,049 | 2,130 | 919 | $64,806.53 | $3,056.88 | +4.72% |
| `mlb_inning_discount_10c_first` | 1,426 | 1,229 | 197 | $40,559.49 | $2,969.81 | +7.32% |

## Canonical ledger integrity

Audited **122 completed canonical ledgers / 2,003,190rows**. Strict JSON parsing rejects nonfinite numeric tokens. Filename/slug, unique columns and IDs, row width, full-count/nontruncation, headline counts/cash, and row payout−principal−fees=PnL were checked. After resolving producer conventions and the accepted desk adapter, **0 unresolved technical discrepancies** remain in this scope.

- Five soccer ledgers being rebuilt and the old wallet ledger were deliberately excluded; this audit does not attest their pending outputs. The index is not a trade ledger.
- Eight whale raw ledgers intentionally retain compact physical source codes. I independently ran current `_read_ledger`/`_compact_labels` over all their rows: every date, event, market and outcome resolves, with no missing display label and no cash mutation. Missing raw date/side fields are therefore not a producer defect.
- Live mean reversion retains entry-only `cost_usd` and all-fee `deployed_usd`; canonical fee includes exit fees. `deployed_usd = stake_usd + fee_usd = cost_usd + exit_fee_usd`. PnL and ROI reconcile on that all-fee denominator. The rebuilt live ledgers now include economic exit prices/clocks.
- In-play certainty and tennis completion legacy maker headlines use principal before negative rebates as their capital denominator. Inclusive cash is separately recorded in this audit; do not combine those old headline ROIs with a new denominator.
- Esports identity has 117 four-decimal row-rounding residuals no larger than 0.0001 USD. They are within the documented 0.0002 arithmetic tolerance, not missing cash.
- This is technical integrity evidence, not confirmation of every older strategy’s causal validity. Numeric source/result identities are stored in `lead-robustness.json`.

## Reproduction

```sh
OPENBLAS_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pmsports.research.ledger_studies --inning-grid --verify-reports reports
OPENBLAS_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_ledger_adapters.py
```

Actual export exit 0. Adapter suite 20 passed, including distinct first 10c/first 3c triggers, all 22 unique exports/no-fill retention, untouched 3c alias, and rejected count/cash discrepancies. The shared ledger cash and row adapters were unchanged.
