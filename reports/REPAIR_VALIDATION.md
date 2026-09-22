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
