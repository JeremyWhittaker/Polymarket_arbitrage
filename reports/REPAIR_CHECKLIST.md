# Repair and viability work — 2026-09-22

User instruction: resume Claude's work, fix the peer-review findings, and establish clear results on trading viability. All earlier peer-review deferrals are now implementation scope. Evidence will be updated at each accepted checkpoint.

| Item | Status | Evidence / next action |
|---|---|---|
| Causal market universe and volume screening | implemented | Default eventual-volume floor removed; favorite decision/volume strictly predecision. Existing corpus remains selected; backfill/reruns tracked below. tests/test_coverage.py. |
| Wallet selection using only known fills/outcomes | implemented | study._causal_cutoff; fills sliced before monthly aggregation; known closed_ts required. Independent cutoff checks passed; full results rerun remains below. |
| Explicit signal/entry timing and size-limited execution | implemented | b9ecdee shared replay: strict later entry, acquired side, consumed shares, fee-inclusive event caps, partials/no-fills; 57 tests pass. Full results reruns remain below. |
| MLB state transitions, runner rules and causal reference pricing | implemented | Unique third-out transitions, game-type/scheduled-inning rules and strictly preceding raw-timestamp references; real game778480 rebuild and future-append invariance passed. Execution completion and corpus rebuild remain below. |
| Holdout discipline, weighting and statistical claims | deferred | Relabel explored history; report uncertainty and freeze prospective rules. |
| Cache provenance and invalidation | implemented | Wallet caches bind dependency code, input identities and requested dates; atomic publication. Tape add/change/delete and split-change checks passed. |
| Desk index freshness and full trade ledgers | implemented | 25 valid ledgers; four full exports334409rows, no20k truncation; fingerprint cache lifecycle tests and actual final-page retrieval pass. |
| Sport-filtered headline metrics | implemented | Cards/table/equity/rail share canonical sport and period filters; Other406trades; browser stale-response guard passes. |
| Clean-install web dependencies | implemented | FastAPI/uvicorn/httpx declared; fresh isolated install, startup,35tests pass; opt-in browser gate9tests passes separately. |
| Tape-window coverage and backfill policy | implemented | Creation-to-close requests, metadata fallback, manifests/version/output checks, prior-file preservation. Coverage backfill itself remains below. |
| Live UTC rollover metadata | implemented | Recorder snapshots active slate each new UTC date; adjacent-day recovery found15 metadata records for Sep19; mocked rollover regression passes. Raw capture unchanged. |
| Football capacity, failed hedges and bootstrap correction | implemented | 6ea04df: three complete studies, causal raw-play prefixes, fixed paired quantities and failed-hedge costs; 13 regression tests and full cash reconciliation. |
| Four unfinished baseball hypotheses | implemented | 75c4c15; BASEBALL_CONTINUATION.md, 14 tests, 2,170 tapes and four complete ledgers. No reliable positive result. |
| Five unfinished soccer hypotheses and synthesis | deferred | Five primaries / 38 named configurations implemented, 22 tests pass; guarded full 4,739-game evaluation active. |
| Backfill measured research coverage | implemented | Six missing markets added, six old tapes upgraded; 251 exact away-runline tapes fetched and verified. The twelve-market source-volume diagnostic proves missing early history and differing volume units, not population completeness. |
| Rebuild MLB corpus | implemented | 267,781 states / 4,570 games; 1,082,375 baseline states / 14,538 games; no duplicate state keys or priced rows with missing fees. Postponed aliases and causal training history repaired; full main reports regenerated. |
| Rerun corrected wallet statistics | deferred | Causal cutoff, corrupted-row exclusion and all-signal execution are implemented; final unsampled corpus rerun follows memory acceptance. |
| Corrected whale and esports lead evaluation | deferred | Causal, size-limited sensitivity with game-clustered uncertainty. |
| Literal trade-to-mean exit and actual received-book replay | implemented | LIVE_EXECUTION.md:4,037,480 received messages,15 verified resolved contracts;12 mean positions lose$87.79. Fixed prior-season model, delayed bid-depth exits/timeout, fees, partials and capture censoring. Seven engine/strategy tests and independent cash/source review; one-day hypothetical crossing, not validated exchange fills. |
| Desktop/mobile QA and deployed desk | implemented | Actual25ledger desktop1440/mobile390 visual QA passed; accepted8d9143e pushed, user service restarted and localhost/Tailscale target checked. |
| Final viability decision and reproducible results | deferred | Fresh results report distinguishing negative, inconclusive and genuinely validated evidence. |

Statuses: implemented, blocked, deferred, not applicable. Deferred here means queued, not abandoned. No real-money trading is authorized or needed for this work.

Additional acceptance findings: invalid normalized prices are rejected before causal volume or wallet ranking; one-percent float boundaries are exact at stored precision; calibration ROI weights fee-inclusive capital; H3 training baselines exclude their own season; H2 price/outcome rows and clustered uncertainty agree. Full point sweep uses bounded whole-market volume accumulation without sampling. The final UI preserves empty studies and resolves actual market/outcome labels; 15 browser-enabled tests plus actual desktop/mobile evidence passed at f8714c7. Final corpus/deployment checks remain pending.
