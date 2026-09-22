# Repair validation — 2026-09-22

First accepted checkpoint: state/reference timing, wallet cutoff/cache identities, and UTC metadata recovery. This is code acceptance, not evidence of profitable trading. Full strategy reruns and execution corrections remain in REPAIR_CHECKLIST.md.

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
