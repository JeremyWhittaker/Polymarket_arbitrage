# Repair and viability work — 2026-09-22

User instruction: resume Claude's work, fix the peer-review findings, and establish clear results on trading viability. All earlier peer-review deferrals are now implementation scope. Evidence will be updated at each accepted checkpoint.

| Item | Status | Evidence / next action |
|---|---|---|
| Causal market universe and volume screening | deferred | Repair collection coverage and point-in-time eligibility; recompute affected studies. |
| Wallet selection using only known fills/outcomes | implemented | study._causal_cutoff; fills sliced before monthly aggregation; known closed_ts required. Independent cutoff checks passed; full results rerun remains below. |
| Explicit signal/entry timing and size-limited execution | deferred | Shared execution corrections and study reruns. |
| MLB state transitions, runner rules and causal reference pricing | implemented | Unique third-out transitions, game-type/scheduled-inning rules and strictly preceding raw-timestamp references; real game778480 rebuild and future-append invariance passed. Execution completion and corpus rebuild remain below. |
| Holdout discipline, weighting and statistical claims | deferred | Relabel explored history; report uncertainty and freeze prospective rules. |
| Cache provenance and invalidation | implemented | Wallet caches bind dependency code, input identities and requested dates; atomic publication. Tape add/change/delete and split-change checks passed. |
| Desk index freshness and full trade ledgers | deferred | Repair then verify all ledgers visible and complete. |
| Sport-filtered headline metrics | deferred | Repair API/UI scope semantics. |
| Clean-install web dependencies | deferred | Declare and validate startup. |
| Tape-window coverage and backfill policy | deferred | Version collection windows; backfill needed research slice or document precise coverage limits. |
| Live UTC rollover metadata | implemented | Recorder snapshots active slate each new UTC date; adjacent-day recovery found15 metadata records for Sep19; mocked rollover regression passes. Raw capture unchanged. |
| Football capacity, failed hedges and bootstrap correction | deferred | Repair and integrate nine existing Claude files after verification. |
| Unfinished soccer/baseball hypotheses and synthesis | deferred | Recover exact original hypotheses, execute defensible tests and report all outcomes. |
| Rebuild MLB corpus and rerun corrected wallet statistics | deferred | Code checkpoint accepted; full jobs follow execution/coverage changes to avoid presenting stale results. |
| Corrected whale and esports lead evaluation | deferred | Causal, size-limited sensitivity with game-clustered uncertainty. |
| Desktop/mobile QA and deployed desk | deferred | Verify desktop/mobile rendering and live service after accepted changes. |
| Final viability decision and reproducible results | deferred | Fresh results report distinguishing negative, inconclusive and genuinely validated evidence. |

Statuses: implemented, blocked, deferred, not applicable. Deferred here means queued, not abandoned. No real-money trading is authorized or needed for this work.
