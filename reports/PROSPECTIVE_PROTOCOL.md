# Prospective paper test: MLB leader discount

Specification date: 2026-09-23 UTC. **Paper research only; not activated and no orders authorized.** This document makes the next discriminating experiment concrete. It does not turn previously inspected history into a holdout or claim a completed prospective result.

The existing 10-cent, first-signal-per-game inning rule is the candidate: historical allocated-capital ROI +7.32%, nominal 95% game-bootstrap interval +1.0% to +13.5%. It came from the [full threshold grid](THRESHOLDS.md), with optimistic retrospective state clocks and transaction-based capacity. Its apparent return is a reason to test receipt-time execution, not a forecast of profit. The frozen original 3-cent rule remains the primary result of that earlier study; this is a new prospective primary chosen after inspecting history.

## Immutable rule and comparison

| Component | Fixed specification |
|---|---|
| Universe | Every discovered MLB game moneyline with exact event/outcome mapping and a known settlement rule. Discover before start; no eventual-volume or eventual-result screen. Record missing markets and collection failures. |
| Model | Existing `_prior_season_fair` / `_baseline_we`: prior seasons only, same state hierarchy, minimum 30 observations per cell, shrinkage `(wins+5)/(n+10)` and 0.5 final fallback. Save the baseline and code hashes before the first signal. An annual prior-season update follows the same rule and is logged before that season. |
| Signal state | A received, internally consistent half-inning-start state with a nonzero lead. Use the leader's actual contract, observed at local receipt; never substitute a retrospectively corrected event clock. Only the first qualifying signal per game may attempt entry, even if it gets no fill. |
| Signal price | Last valid leader transaction received strictly before the state decision and no more than 120 seconds old. Trigger when prior-season leader win probability minus that price is at least 0.10. Record this reference separately from the live ask and actual execution opportunity. |
| Entry | At signal receipt plus the actually applicable venue delay, inspect the then-received book. Simulate one marketable limit attempt, with limit equal to reference price plus 0.01 and $100 maximum inclusive of fees per game. Use only admissible tick/minimum sizes and observed opposite-side depth. Missing fee/delay/order rules or stale/disconnected books produce no fill. This live-book implementation is a disclosed new execution test, not an assertion that old later-print fills were executable. |
| Capacity | Partial fills retained. A price level's recorded available quantity is consumed once within a policy. Separate counterfactual policies do not share a fictitious portfolio. Record the observed book identity and elapsed receipt age; maximum book age 5 seconds. |
| Exit | Hold acquired shares to the contract's actual resolution, including void rules. No profit target, hindsight exit or outcome-based exclusion. |
| Control | The previously specified 3-cent first-signal rule, executed independently under identical receipt/book rules. It is a comparison, not a fallback primary. Report paired game-level cash differences as well as each policy's own ROI; their eligible populations can differ. |
| Sensitivities | One extra cent of adverse entry price and an additional 2 seconds of delay, both with unchanged signal selection and full no-fill accounting. These cannot replace a failed primary. |

A book crossing remains hypothetical until order admission and venue behavior are observed. The paper engine must use the venue's actual delay and expiration/cancellation rules at the time; a retrospective “expire on the next state” censor does not prove an in-flight order could have been canceled. Log any next-state transition during the pending delay and retain the resulting economic exposure. See [the execution protocol](EXECUTION_PROTOCOL.md) and [venue order lifecycle](https://docs.polymarket.com/concepts/order-lifecycle).

## Collection, stopping and decision

Before activation, checkpoint the source, model snapshot, rule JSON, collection start instant and event-universe manifest. Verify UTC synchronization, score/book receipt logging, full snapshots plus incremental depth updates, token orientation, reconnect gaps and current per-market fees/tick/minimums. Collect every discovered game, signal, rejected order and missing-data reason. The baseline/capture manifests and ledgers are required outputs, not optional diagnostic logs.

The endpoint is the first **500 discovered eligible games with complete outcome follow-up, or 365 calendar days after activation, whichever comes first**. A game is admitted by pregame metadata, before observing its signals or result; games with missing captures remain in coverage accounting. Fewer than 100 funded games makes the experiment inconclusive regardless of its point estimate. These are operational limits, not a power calculation or a guarantee of detecting a small edge. Do not extend the endpoint because results are unfavorable or stop early after profits. Safety/data-integrity interruptions are logged; changing the rule requires a new version and a new future evaluation interval.

At the fixed endpoint, report all attempts/fills/partials, allocated capital, entry fees, net cash, cash-weighted ROI and a whole-game 95% bootstrap interval (2,000 resamples, seed 0). Also report equal-game ROI, calendar segments, missing-data coverage, maximum concurrent exposure, drawdown, fill-size distribution, no-fill rate, and returns after removing the best 1, best 3 and best 1% of games. Do not sum overlapping policies as portfolio profit. Estimate running/data costs separately; the present project has no validated daily-income projection.

A result qualifies for further investigation only if the primary's net cash and lower nominal interval are positive, adverse-price/delay sensitivities remain positive, and the conclusion does not depend on a few games or favorable missingness. This gate is evidence for a subsequent limited execution experiment, not automatic permission to risk money. If the interval crosses zero, call it inconclusive. A negative result stays negative; switching to the control or a newly winning threshold starts a new experiment.

## Current blockers to a prospective claim

The project has only one recovered day of received-book evidence and no completed future run under this specification. Historical taker prints do not reveal the follower's order admission, queue priority or available future depth. These missing observations cannot be fixed by rerunning the old corpus. The repaired capture and research components can support the test, but an end-to-end prospective runner with this exact policy has not been deployed by this repair.
