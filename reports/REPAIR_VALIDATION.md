# Repair validation — 2026-09-22

This file preserves acceptance checkpoints in order; older counts and pending items describe their checkpoint, not the latest state. The current checklist is [REPAIR_CHECKLIST.md](REPAIR_CHECKLIST.md). Code acceptance is not evidence of profitable trading.

- 18 repository tests passed. They include a local real-game builder smoke and a mocked UTC rollover with unchanged token subscriptions. The real-data smoke skips on a clean clone without collected data; synthetic tests remain available. Two pre-existing empty-placebo fixture warnings occur in the tiny wallet test.
- Independent production smoke rebuilt cached game778480 into an isolated temporary directory:62 causal state rows, no duplicate game/play keys,10 later entry proxies. All reference timestamps strictly precede decisions; all entry proxies follow the configured delay. Appending an extreme future trade did not change any existing reference price/timestamp. Original data caches were untouched.
- Independent wallet checks covered string/categorical market IDs, future and missing resolution timestamps, exact selection boundary, missing category, and absent metadata. Unknown outcomes fail closed; metadata absence raises explicitly.
- Independent cache checks proved identity changes when a tape is added, changed, or deleted, and when the split changes. Manifests bind source dependencies plus input path/size/mtime_ns, not a cryptographic content hash of all54M rows. Backfills and report generation must remain serialized to keep inputs stable during reads.
- Sep19 metadata recovery loads15 records from adjacent captures without rewriting the2GB raw book file. This validates metadata recovery, not a completed full latency replay.

Command: OPENBLAS_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests

The first delegated drafts were rejected despite passing unit tests: one actual builder call raised a tuple-unpack error, and its state ordering would have broken expiry. They were preserved on a working branch and corrected before this checkpoint. Parent review separately verified the final production behavior.

Remaining panel execution work includes side-specific entry/capacity rules, explicit staleness limits, terminal-state expiry, and all schedule-file merging. These are assigned to the shared execution phase; the panel now separates causal references from later trade proxies, and neither is presented here as an observed available order-book ask. Historic evaluated periods have already been explored.


## Coverage and desk checkpoint — 2026-09-22

Accepted main commit8d9143e replaces the rejected draft semantics. Collection defaults to no eventual-volume filter, upgrades request market creation through closure with version/window/output manifests, and failures preserve old raw tapes. Pregame decision is start minus600s; only earlier fills supply signal and liquidity; first same-side print more than5s later supplies a size-capped proxy. These corrections do not make old selected/incomplete data representative.

Compact rebuilds now migrate market/wallet IDs from actual legacy tables, append new IDs, and preserve missing/empty old slots. Source/code/output identities and a staged promotion marker prevent silently mixed or stale versioned generations. Legacy caches explicitly warn. Actual miniature full rebuild and subsequent empty-market rebuild passed.

Fresh-environment full suite:35passed,1optionalbrowser-test skipped. Separate browser-enabled desk suite:9passed. Desktop1440x1100 and mobile390x844 inspected, no JS errors/viewport overflow. All25ledgers valid; four restored exports contain334409rows. Finalpage of190954-row ledger verified. Other favorite-at90filter now406trades/PnL-$1173.16, not17729globaltrades; football775development/65historicalevaluation, evaluationROI-1.1453%. These are reconciliation checks of historical reports, not accepted trading results. Warmindex6–12ms; coldrebuild~5.9s. Corrected API/UI deployed after commit/push via existing user service and checked at localhost8808 and Tailscale100.92.20.16:8808. Source remains private/noindex.

Recovered live replay of2026-09-19:61 qualifying scoring events across14games, selected for subsequent move>=3c. Median bookt50=2.856s, MLBfeed=41.095s, PMsportsfeed=71.412s relative to retrospective contact time. Paired median feedminusbook=37.114s and67.625s respectively. MLBfeed precededbook in1/61, PMsports0/61. This one selected capture provides adverse timing evidence for free-feed reaction, not a universal latency bound or trading PnL.

Fullcorpus strategy reruns, execution completion, nine unfinished selected sports protocols and final viability synthesis remain in progress. Historical reports retain no fresh-holdout status.

## Execution and full compact-cache checkpoint

Accepted b9ecdee: 57 tests passed, one optional browser test skipped in the fresh QA environment. The shared replay enforces strictly later acquired-side entries, leader exclusion, quantity and event caps, actual fees, shared remaining print capacity, partials and no-fills. All 50 threshold ledgers have a tested complete export path. Independent strategy comparisons reset liquidity separately; the wallet cache now includes the shared execution dependency. This validates code behavior, not a profitable result.

The first full rebuild exposed an empty-tape Arrow null/string mismatch. Regression and repair 2d5bff9 passed before the retry. The completed retry contains 53,771,660 fills and 38,370 markets; all 38,364 pre-existing market IDs are unchanged. Independent source/output manifest validation and complementary settlement payout checks passed. Corrected pregame coverage was regenerated for 38,423 moneyline markets.

Finite coverage diagnostics added six formerly absent markets (1,552 fills) and upgraded six legacy tapes from 5,320 to 5,797 fills. Earlier timestamps recovered by those upgrades prove the old bounded windows omitted history. These twelve targeted markets do not establish an unbiased population or complete provider history.


## Expanded acceptance — 2026-09-23

The combined source suite at this checkpoint passed **174 tests**, with 2 third-party deprecation warnings and no skips, including the browser gate. This included the wallet-index and literal soccer-action repairs; the later closure checkpoint below supersedes this count. Final generated-result acceptance and deployed-target refresh remain pending. All source checkpoints are pushed.

- Main economic inputs:53,771,657 valid fills; only3 negative-price rows excluded from53,771,660 source rows. Bounded Arrow checks found no other invalid economic fields. Full MLB panel267,781states/4,570games and prior-history baseline1,082,375states/14,538games were independently checked.
- Full calibration: all50 cent bins and50 cumulative threshold rules were rerun without sampling; cash and counts reconcile to all50 ledgers. Only91c is positive at+0.0566%, with a nominal interval crossing zero. Main MLB, favorites, whale, frozen esports, three football and four baseball studies were rerun and accepted. Their canonical reports identify cash, timing and coverage limits.
- Inning grid: all12 unadjusted and10 adjusted policies now have complete ledgers, exactly reconciled against threshold CSVs. LEAD_ROBUSTNESS.md reports capital weights, equal-game returns, year/direction splits and removal of best1/best3/best1% games. Independent technical audit accepted122 other ledgers/2,003,190rows, excluding the soccer/wallet artifacts being regenerated. Legacy maker rebate denominators and received-book total-fee denominators are explicitly different estimands.
- Soccer: the first full4,739-game/106,519-event run reconciled152 summary cells,782,633 candidate rows and40,062 trade legs. Subsequent independent raw-action review found a material original-protocol mismatch: economic SELL complements supplied BUY capacity. Those outputs are archived locally in `data/research/soccer_continuation_acquisition_proxy_20260923/` and superseded for primary conclusions. Repair a9f2992 requires actual BUY entries/detection and actual SELL unwinds, records native action/token provenance, and fails closed on unknown actions.49 focused soccer/engine tests pass. An independent frozen167-order red-card replay agrees with production to1e-8: evaluation61funded,$2,519.92capital,$528.11net,+20.96%; full corrected38-configuration rerun remains pending.
- Wallet: two14GiB runs failed by verified cgroup OOM, first in position aggregation and next in global replay-index construction. No failed run's results were accepted. Repairs9d98ec0/e4c1e16 batch complete markets and connected market/event order components while retaining all prints/signals and shared capacity. Exact old/new comparisons cover999,651 actual prints,327,671 positions and56,054 selected signals across both sizing policies/five delays.57 focused tests pass. The compact index uses49bytes/source row with production dtypes. This establishes exactness on the bounded comparison; successful full-report/ledger acceptance is still required.
- Received books:4,037,480-message replay rerun after b8fb1c4; fully liquidated positions now show actual book exit price/time, with settlement time only for residual inventory. Twelve mean-exit positions lose$87.79; cash did not change. Actual desktop/mobile detail views passed.
- Desk:128 indexed strategies tested on desktop1440/mobile390; no JS errors, unintended page overflow or index errors. Cold index22.03s, warm28ms in this check. Empty strategies retain their sport, compact rows resolve exact market labels, and total fees/evaluation labels match their meaning. These are local source checks; final service restart and live-target acceptance remain pending.

Ignored operational evidence lives under `.foreman/repair-20260922/`: `lead-robustness.json`, `soccer-edge-review.json`, `wallet-memory-worker.md`, `live-exit-acceptance.json`, `ui-final-qa.json` and checkpoint logs. Full generated trade/signal data remain under ignored `data/`; portable source, tests and reports are versioned. Input identities generally bind path/size/mtime_ns and source SHA256, not a cryptographic hash of every historical trade.


## Closure and final source acceptance — 2026-09-23

**198 tests pass in 19.80 seconds**, with two third-party deprecation warnings and no skips, including the browser gate. Accepted code6a4a3e2/8d2492b and favorite results4391ba6 are pushed. Full strict-action soccer and final wallet result acceptance remain pending.

A bounded full-corpus clock scan found 5,537 prints at/after Gamma closure across 3,155 markets. A sequential 128-ledger audit used152MiB peak:122 other ledgers had1,886,900 funded rows, of which1,647,169 mapped exactly to compact closure metadata. The remaining239,731 were not independently closure-verified. Only one mapped funded entry followed its declared terminal: a canceled ATP match, $13.8852 allocation and −$0.2052 P&L. A direct resolution API check put actual reported resolution69 seconds after that entry, while Gamma closure was40 seconds before it. This establishes inconsistent analytical eligibility/exit clocks, not actual post-resolution trading. The84 legacy halftime equal-clock rows are explicitly labeled markouts, not liquidations.

Favorites now expire at the earlier of scheduled start and known closure. Missing closure fails closed. The accepted rerun retains all signals and nulls execution/exit fields for no-fills. All favorite cash is unchanged:14,442 funded, $613,085.65 capital, −$19,481.29 net (−3.18%). Exactly one underdog fill is removed:11,529 funded, $431,316.37 capital, −$4,587.55 net. The later-period underdog estimate is +1.1602%, nominal interval −4.38% to +7.28%.

Wallet copying now requires a small per-market closure lookup and bounds every delay's expiry before allocation. Selection comparisons, big-trade, monthly, decomposition and canonical export all propagate it; unknown closure is ineligible. Raw prints and rankings are unchanged. On999,651 actual prints/56,054 signals, the compact and original monolithic engines match exactly under the same cutoff. Nine of ten policy/delay cells are unchanged; a single proportional300s allocation of$0.00389745 is correctly censored. Computing just the requested sizing policy exactly preserves its output and takes7.97/9.00seconds versus15.75seconds for both.81 focused tests pass. This is numerical and behavioral acceptance, not full-data financial validation.

The former wallet r3 run was intentionally stopped with verified exit143 to add this cutoff; it was not an OOM or stall. It had passed full period statistics and index construction at12.9GiB peak. Its outputs are superseded. A fresh source-bound report and separate canonical-ledger run are required.

The favorites change also invalidated the broad compact-cache source hash. Before renewing its manifest, independent checks reproduced the old aggregate source hash, proved every other dependency/raw input unchanged, proved AST changes limited to favorite allocation/rendering, and rebuilt the complete current market table into a temporary path. All38,370rows/dtypes/order and Parquet bytes matched exactly. The previous manifest and machine-readable proof were preserved; the new manifest explicitly records revalidation rather than claiming a full fills/wallet rebuild. The freshness guard itself remains unchanged and passes. Evidence: `compact-closure-equivalence.json`, `closure-impact.json`, `favorite-closure-acceptance.json`, `wallet-closure-worker.md/json` under the operational evidence directory.

Gamma closure is an analytical terminal/outcome-availability proxy, not independently measured public resolution receipt. The venue exposes a separate resolution-state endpoint; this distinction remains a data limitation. [Official resolution API](https://docs.polymarket.com/api-reference/markets/get-resolution-state).


## Original NRFI inference restored — 2026-09-23

The final prompt audit found that the original first-inning rule required equal-game uncertainty, while the repaired report included its point estimate and a cash-weighted interval. The supplement first aggregates every funded game's net P&L and inclusive capital, then bootstraps those per-game ROIs with equal weights. It preserves cash-weighted results as a separate statistic. Empty samples have null estimates/bounds; fewer than five funded games have no interval.

Original pooled NRFI equal-game return is **−11.45%, nominal 95% interval −33.40% to +10.99%**, versus cash-weighted +5.08%. The July-onward 36-game equal-game result is +28.43%, interval −5.94% to +60.84%; its positive cash estimate does not establish the requested equal-game result. All periods were already explored. The complete primary and +1¢ results are in [BASEBALL_CONTINUATION.md](research/BASEBALL_CONTINUATION.md).

Sixteen focused tests pass, including unequal-capital/opposing-sign estimands, multiple legs, split-fill invariance, no-fills and null bounds. The authoritative cached rerun took34.05seconds and peaked at2,375,108KiB (~2.27GiB), exceeding the worker's2GiB estimate; no memory failure occurred and the run was not repeated. Parent independently verified byte-identical eight trade Parquets, pair diagnostic and four canonical ledgers; all original summary values, model details and input identities are unchanged. All24 new equal-game summary cells independently reproduce direct bootstrap calculations. Only source identity and new inference fields/report text changed.

The final combined source suite now passes **200 tests in19.27seconds**, with two third-party deprecation warnings and no skips, including browser QA. Evidence: `nrfi-inference-parent-acceptance.json`, `nrfi-inference-worker.md/json`, and `final-tests-nrfi.log` under `.foreman/repair-20260922/`. Full strict soccer, final full wallet and deployed desk acceptance remain pending.


## Full strict-action soccer accepted — 2026-09-23

The corrected run completed all **4,739 games /106,519 events**,38 configurations and two cost cases in2,550.39seconds. ActualREADY fingerprint and source/input identities were verified, with noFAIL marker; exact job and lease-monitor processes exited. The user-service journal reports4.1GiB peak and no swap. The helper wake was consumed and reconciled.

Independent acceptance reconciles **152 summary cells**, five complete canonical ledgers,782,633 candidate-audit rows and37,948 trade legs. It traces50,491 entry/exit records back to physical raw action/token/asset/price/size and verifies strict clocks, no-fill nulls, partial/residual economics and event/print capacity. Source remains the accepted literal-BUY-entry/SELL-unwind implementation; no scientific input was rewritten.

Added-time’s required +1¢ case has180funded/186selected,121partials,six no-fills: $8,095.95capital,+$469.87net,+5.80%, nominal interval+1.22% to+9.32%. Fee-only182funded is a different case. Both price-matched comparisons are favorable, and best-three removal leaves+4.66%; DEV−0.26% and10/60second delay results+1.74/+1.02% with intervals crossing zero remain material limitations. Red-card primary+20.96% has an interval crossing zero; its late/card-side branch loses and does not establish the timescale mechanism. Other primary details are in [the full report](research/SOCCER_CONTINUATION.md).

The original Dutch one-goal/game inference is restored as a separate earliest-selected-goal diagnostic, globally selected before fills/outcomes. Parent and independent worker agree on all173selected IDs, with zero first-decision ties and no later entry-window overlap; every submitted leg is retained. Evaluation25games returns−2.51%cash or−5.40%equal-game; +1¢ also loses. Four cells and both interval types reproduce within1e−12 using shared2,000/seed0 bootstrap defaults. Production soccer intervals use1,000/seed20260922; the settings distinction and near-zero endpoint sensitivity are explicit. This supplement changes no production signal, trade, ledger or source identity.

[SOCCER_ROBUSTNESS.md](SOCCER_ROBUSTNESS.md) includes exact runnable reproduction, delay/control/concentration results and limits. Operational evidence: `soccer-direct-acceptance.json`, `soccer-supplement-parent.json`, `soccer-final-review.md/json`. No source changed after the200-test checkpoint; the supplement’s actual calculation and Markdown/link checks pass. Wallet report r4 is admitted for the full unsampled corpus, with a separate canonical-ledger phase required afterward. Neither wallet results nor the final deployed desk are accepted yet.


## Full-wallet memory repair and bounded acceptance — 2026-09-23

The fourth full report attempt failed after 1,547.46seconds. Its actual FAIL fingerprint, absent READY and exited job/guard/lease-monitor processes were independently verified. The kernel records `CONSTRAINT_MEMCG` and SIGKILL for the exact 16 GiB service cgroup; this was a resource failure, not a financial result. The wake was consumed/reconciled. No output from that failed attempt is accepted.

Two remaining allocations were repaired. The metadata reader previously retained all 3,872,026outcome rows for 1,936,013markets, including unused descriptive columns. A single-thread 50,000-row Arrow scanner now projects the 11 needed fields and restricts metadata to physical tape IDs. Independent sequential reading of the entire source proves exact values and source ordering for all 76,846 retained outcomes / 38,423 markets, including duplicate semantics; tape rows and market eligibility are unchanged.

Report calculations now replay complete market/event connected components one delay and sizing policy at a time, immediately reducing funded rows to narrow event totals. Phase filters follow allocation; every selected signal is replayed. The original per-event summation and random bootstrap sequence are preserved with bounded index temporaries. Canonical exports retain the full audit. The exporter also releases unused source/evaluation frames before replay and its copied audit before serialization. Family and walk-scope checkpoints use the existing source/input/date-bound run ID and atomic publication. Logs identify rule, delay, policy, batch counts and memory.

An independent frozen `05d59c8` comparison covers selection, large-trade copying, monthly reselection and skill-screen decomposition on 627,834 actual prints / 382 markets / 299 events. All outputs and attributes agree within 1e−12; formatted confidence intervals agree exactly. The bounded equivalence fixture uses a three-market eligibility minimum in both versions to exercise nonempty decomposition (168 selected wallets, 564 signals); production keeps its unchanged 30-market minimum. This is validation, not a strategy result.

A separate output-heavy stress has 220,000 signals, including 95,000 in one indivisible event, and both sizing policies across three delays/three phases. All 18 summary dictionaries match the frozen original exactly. Peak RSS falls from 995.45 MiB to 389.10 MiB (38.93 s versus 40.05 s). The full actual source has at most 94,978 raw prints in an event. These checks support bounded output memory but do not certify the full report. An adverse category-remapping/missing-event case independently exposed and fixed a component-identity mismatch before shipment; the corrected shared-cap allocation agrees with the monolithic replay.

Final source validation: **207 tests passed in 27.85 seconds**, two third-party deprecation warnings, no skips; browser gate enabled. Evidence under `.foreman/repair-20260922/`: `wallet-r4-failure.json`, `wallet-r5-reference/`, `verify-wallet-r5.py`, `wallet-r5-stress.py`, stress result JSONs, source-freeze hashes and `final-tests-wallet-r5.log`. Full unsampled report, fresh-process canonical wallet ledger and final deployed-desk acceptance remain required.


## Full unsampled wallet report and ledger accepted — 2026-09-25

The fifth full report attempt finished at 04:38 UTC on September 23: exit code 0, READY marker, 3,868.79 seconds, 9.44 GiB peak under the 16 GiB service limit. Codex, the previous driver, had exhausted its quota and never consumed the notification. Claude resumed the goal and independently checked the staged output before accepting it. The run ID 991ad924682a, the input-manifest hash, the full expected manifest and the cached manifest all match the pre-launch admission. The `ALL` family contains exactly 53,771,657 valid fills. All 60 walk-forward rows reconcile to the new monthly cash audit. All six frozen source and test hashes, and `main` at `f7dd510`, were unchanged.

The canonical ledger then ran as a fresh, separate process under the same 16 GiB, no-swap limit. It took 284.89 seconds, peaked at 7.63 GiB and exited 0 with a READY marker. `accept-wallet-final.py` passed every check. These include both READY markers with no FAIL, the expected run ID and full manifest, and valid-fill counts. They also include:

- cash identities (stake plus fee equals cost; payout minus cost equals P&L);
- entry strictly after the eligible time and before expiry;
- null execution and exit clocks for every no-fill;
- the $100 per-event cap;
- no truncation;
- agreement between the ledger, the report decomposition and the 30-second proportional FDR rule.

The previous 101,150-row flat-$100 ledger from September 20 has been replaced. It is not used.

Independent inspection of the 90,878-row ledger found 84,985 filled, 4,056 partial, 1,415 unfilled, 420 event-capped and 2 ineligible signals. The earliest entry comes 31 seconds after its signal. The 89,041 funded orders allocate $57,564.19 and lose $489.50 (−0.85%). The report's decomposition bootstrap gives a nominal interval of −2.9% to +1.3%; the ledger's own headline bootstrap, run separately, gives −3.00% to +1.27%. Removing the best three events gives −1.37%, and removing the best ten gives −2.11%. Equal-event ROI across 7,845 events is −4.02%. Median allocation is $0.026 because the leaders' median fill is $2.60. Faster-copy variants and monthly reselection are reported in [WALLETS.md](WALLETS.md); none has an interval above zero at the full-selection level.

Evidence under `.foreman/repair-20260922/`: `wallet-ledger-r5-admission.json`, `wallet-r5-report-wake-retired.json` and `wallet-final-acceptance.json`, plus the report and ledger run directories recorded there. No source changed, so the 207-test checkpoint remains current.


## Final synthesis, desk fix and deployment — 2026-09-25

The desk was restarted on the final source (user service `pmsports-web.service`). `deployed-final-qa.py` passes against `http://127.0.0.1:8808` and the Tailscale address. It checks 128 studies with no index errors, health, `X-Robots-Tag: noindex`, robots disallow and no canonical link. It also reconciles API totals, last page, evaluation filter and equity for the wallet, five soccer and underdog ledgers, plus desktop 1440 and mobile 390 screenshots without page overflow. The wallet ledger serves 90,878 rows with evaluation P&L −$489.50.

Reviewing those screenshots found a real display defect. The desk rounded dollars to two decimals and shares to one, so funded sub-cent copy orders appeared as "$0 = 0.0 shares" with P&L "−$0". This affected 39,389 of the 89,041 funded wallet orders. Commit 3c3b62b shows sub-cent values with two significant digits and renders float noise below 5e−7 as $0. A new opt-in browser test failed on the old formatter and passes now. The full suite passes **208 tests** (34.83 seconds, browser gate enabled, two third-party warnings, no skips). After the restart, the redeployed desk shows "$0.00002 = 0.020 shares".

Seven independent sonnet analysts traced 201 numeric claims in the VIABILITY and SPORTS drafts to their source reports. Adversarial checkers confirmed seven discrepancies:
- one mislabeled clock basis ("play" instead of "contact");
- one wallet interval taken from the ledger's separate headline bootstrap rather than the published report;
- five figures sourced only from private review notes.

Each figure was recomputed from the canonical ledgers or the accepted soccer trade output, and each matched: 3¢ inning rule 2026 −0.11%; substitution best-three −$244.21; red card +1¢ best-three +$70.88 on $2,230.51; draw anchor 47 of 52 funded pairs with failed or partial hedges. They are now published in LEAD_ROBUSTNESS.md and SOCCER_ROBUSTNESS.md and cited from the drafts. A completeness critic found every peer-review finding and original prompt item implemented, or explicitly blocked or deferred with a reason. Its three stale-status notes were corrected.

