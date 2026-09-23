# Trade ledger contract

Write one atomic JSON document to `data/research/ledgers/<slug>.json`. Save every submitted order, including partials and no-fills. Candidate rejections may also be included with zero cash and an explicit reason. Audit-row counts are not funded-trade counts.

Required metadata: `slug`, `title`, `group`, `sport`, `verdict`, `hypothesis`, `mechanism`, `entry_rule`, `exit_rule`, `cost_model`, `periods`, `headline`, `caveats`, `report_path`, `code_path`, `truncated`, `n_total_trades`, `columns`, `rows`. `columns` names each value in every row; additional audit columns are permitted. Use JSON null for unavailable values, never NaN/Infinity. The declared sport keeps a zero-row strategy visible; unknown/multi strategies with no rows appear in All.

| Column | Meaning |
|---|---|
| `id` | Unique row ID within the strategy |
| `period` | Historical development/evaluation label; `holdout` is a compatibility key, not proof of untouched data |
| `date` | UTC entry date, or signal date for an unfilled order |
| `sport`, `league`, `event`, `market` | Canonical sport family and actual source identities |
| `side` | Exact acquired outcome; never infer outcome index from home/away or eventual winner |
| `signal_ts` | Unix seconds when the rule signals; retain separately from entry |
| `entry_ts`, `entry_price` | Actual simulated allocation time and price, including modeled slippage; null for no-fill |
| `stake_usd` | Allocated shares times entry price |
| `fee_usd` | All entry and exit fees charged; a rebate is negative only when explicitly supported |
| `exit_kind` | Resolution, unwind, partial unwind plus resolution, descriptive markout, or unfilled |
| `exit_ts`, `exit_price` | Final economic disposition time and proceeds per original share; null for no position |
| `payout` | Total dollars returned from sales and residual settlement |
| `pnl_usd` | `payout - stake_usd - fee_usd` |
| `roi` | `pnl_usd / (stake_usd + fee_usd)`; null when no capital is deployed |
| `note` | Signal, allocation, failure, assumption and aggregation details |

For example, ten shares acquired at50c with12.5c total fees cost$5.125. A$10 settlement returns$4.875 profit: ROI is95.121951%, not97.5%. This is illustrative accounting, not a research result.

Every period/sport headline must reconcile with its filtered rows: sum dollar P&L, divide by total fee-inclusive capital, count funded rows separately from all audit rows. A headline confidence interval uses the study's documented whole-game bootstrap. Never reconstruct imaginary$100 bets from unit returns. Paired legs and received-book audit events need explicit aggregate/funded-position counting; an exit event is not a new bet.

Sort by entry time, falling back to signal time for unfilled rows. `n_total_trades == len(rows)` and `truncated == false`; pagination serves the entire ledger. `bets` means funded positions/legs as disclosed, not candidate or exit-event count. Empty strategies use zero capital/P&L and null ROI. Cumulative P&L ordered by entry is a strategy walkthrough, not a realized-cash or portfolio-turnover model.

A fully liquidated leg ends at its last actual exit; a partly liquidated leg ends when residual shares settle. Its aggregate exit price is total proceeds/original shares and must be labeled as combined, not a single observed quote. Void settlement uses the actual contract payout. Do not assign settlement cash to an unfilled order.

Generate rows from the study's saved audit or its reproducible producer. Record source/input identities with that audit. Export the original primary rule; identify previously inspected variants and sensitivities explicitly. Historical periods already examined cannot be relabeled fresh holdouts. Publish JSON via a temporary file followed by atomic replacement, and preserve numeric source IDs when display metadata resolves them to human-readable labels.
