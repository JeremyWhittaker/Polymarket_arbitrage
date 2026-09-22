# Trade-ledger spec (feeds the trade-explorer web page)

One JSON file per strategy at `data/research/ledgers/<slug>.json`. Columnar, so it stays small.

```json
{
  "slug": "esports_break_overreaction",
  "title": "Esports BO3 between-map break overreaction",
  "group": "Thorp hypotheses",              // or "MLB studies" / "All-sports studies"
  "sport": "esports",                        // primary sport, or "multi"
  "verdict": "PROMISING",                    // PROFITABLE | PROMISING | DISPUTED | DEAD | INCONCLUSIVE
  "hypothesis": "One sentence: what we believed and why someone would lose money to us.",
  "mechanism": "The structural reason the counterparty misprices (2-3 sentences).",
  "entry_rule": "Exactly how a trade is entered: trigger, side, order type, price, size, timing.",
  "exit_rule": "Exactly how it ends: held to resolution / exit price / markout window.",
  "cost_model": "Fees and slippage actually charged in these numbers.",
  "periods": {"dev": "2025-01-01..2026-06-30", "holdout": "2026-07-01..2026-09-18"},
  "headline": {
    "dev": {"bets": 89, "roi": 0.6519, "ci_lo": 0.1052, "ci_hi": 1.2839, "pnl_usd": 5372.1},
    "holdout": {"bets": 53, "roi": 0.2925, "ci_lo": -0.27, "ci_hi": 0.9215, "pnl_usd": 2227.0}
  },
  "review": "What the adversarial reviewers said, one or two sentences.",
  "caveats": ["...", "..."],
  "report_path": "reports/research/<slug>.md",
  "code_path": "pmsports/research/h_<slug>.py",
  "truncated": false,                        // true if `rows` is a sample
  "n_total_trades": 142,                     // before any sampling
  "columns": ["id","period","date","sport","league","event","market","side","entry_ts","entry_price",
              "stake_usd","fee_usd","exit_kind","exit_ts","exit_price","payout","pnl_usd","roi","note"],
  "rows": [[1,"dev","2026-03-04","esports","lol","t1-vs-geng-2026-03-04","...-game2","YES @ map-2 break",
            1772890123,0.62,25.0,0.19,"resolution",1772899000,1.0,1.0,14.81,0.5924,"map-1 loser bounce"]]
}
```

Column semantics (all required, use `null` when genuinely not applicable):

| column | meaning |
|---|---|
| `id` | 1-based row number |
| `period` | `"dev"` or `"holdout"` |
| `date` | UTC date of entry, `YYYY-MM-DD` |
| `sport`, `league`, `event`, `market` | sport family, league, event slug, market slug |
| `side` | human-readable side bought (team/outcome name, or "Yes"/"No"/"Over") plus order type if it matters (e.g. "maker bid") |
| `entry_ts` | unix seconds of entry |
| `entry_price` | price paid per share, 0-1, INCLUDING modelled slippage |
| `stake_usd` | dollars at risk (shares x entry_price) |
| `fee_usd` | fee actually charged on this trade (0 for maker fills; negative if a rebate is credited) |
| `exit_kind` | `"resolution"`, `"exit_price"`, `"markout"`, or `"unfilled"` |
| `exit_ts`, `exit_price` | when/at what the position ended (`exit_price` = 1/0/0.5 for resolution) |
| `payout` | dollars received back |
| `pnl_usd` | payout - stake - fee (+ rebate) |
| `roi` | pnl_usd / stake_usd |
| `note` | anything that explains this specific trade (signal value, queue assumption, why skipped) |

Rules:
- Rows must be the **actual bets the backtest made**, not re-simulated by hand. Read them from
  the hypothesis's saved parquet/json in `data/research/h_<slug>/` (or re-run its script).
- `sum(pnl_usd)` and the stake-weighted `roi` per period MUST reproduce the headline in
  `reports/research/<slug>.md` (state the tolerance if it is not exact and explain why).
- Sort by `entry_ts`. Spec requires complete server ledgers; no silent truncation, no row limits.
  Full trades both periods, n_total_trades==len(rows). `truncated` MUST be false.
- For maker strategies where a "trade" is a fill, one row per fill; if fills are aggregated per
  market, say so in `exit_rule` and `note`.
- If a strategy has several variants, export the **pre-registered primary** one, and mention the
  variants in `caveats`.
- Write the exporter as `pmsports/research/ledger_<slug>.py` so it can be re-run.
