# Prospective paper test: soccer added-time leader

Specification date: 2026-09-25 UTC. **Paper research only. No orders are sent and none are authorized.** This protocol is frozen before any live soccer book or feed capture exists in this project. The historical result that motivated it was already inspected, so this is a new prospective primary, not a confirmation of that sample.

The historical added-time rule returned +5.80% on $8,095.95 across 180 funded evaluation games (July 2026 onward, +1¢ headline case), nominal interval +1.22% to +9.32%. Re-pricing those same fills at the current 0.05 international and 0.0695 US taker coefficients leaves +5.78% and +5.62%, because entries near 93¢ pay little fee. Development-period return was −0.26% and longer entry delays weaken it. See [SOCCER_ROBUSTNESS.md](SOCCER_ROBUSTNESS.md). Historical entries used later trade prints within a 120-second window, not a received order book.

## Immutable rule

| Component | Fixed specification |
|---|---|
| Universe | Every Polymarket international soccer three-way moneyline event (separate home, draw and away Yes/No markets) discovered before kickoff from Gamma sports games. The event must map to an ESPN soccer league path and match exactly one ESPN event before kickoff (same league path, kickoff within 30 minutes, team-name agreement). Unmapped, ambiguous and unmatched games stay in coverage accounting with a reason. There is no volume or result screen. |
| State feed | ESPN public `summary` endpoint, polled. Receipt time is the local time the response arrived. Retrospective ESPN wallclocks are logged, never used for decisions. |
| Trigger | The first key event newly received in period 2 whose clock parses to at least 90 minutes (explicit base plus added time), when the score in that same response shows a nonzero lead. Only the first qualifying event per game may attempt an order, even if it gets no fill. |
| Reference price | Last trade in the leader's Yes token received strictly before the trigger and no more than 120 seconds old. It must lie between 0.60 and 0.97 inclusive; otherwise record a rejection. |
| Entry | At trigger receipt plus the larger of 3 seconds and the market's `seconds_delay`, inspect the then-received book. Simulate one fill-and-kill marketable buy of the leader's Yes token with limit equal to the reference price plus 0.01, rounded down to the market tick, and a $100 maximum including fees. Observed asks are consumed once per policy. Partial fills are kept. The book must have a snapshot since the last connection open and a confirmed-current connection within 5 seconds. Orders below the market's minimum size, stale books and missing metadata produce recorded no-fills. |
| Exit | Hold to the market's actual resolution, including 0.5 voids. Polymarket settles these markets on regulation time plus stoppage time. There is no profit target and no outcome-based exclusion. |
| Fees | Primary: the market's Gamma fee schedule at trigger time (`rate × shares × p × (1 − p)`). A separate scenario reprices the same fills at the Polymarket US coefficient 0.0695. That venue has its own order book, which this capture does not observe. |
| Controls | The identical rule triggered by the first key event with a lead in minutes 75–85, and separately 70–80, of period 2. Each is an independent policy. The comparison uses common whole-cent reference-price bins, reweighted to the primary's bin distribution, with a joint game bootstrap. |
| Sensitivities | One cent of adverse price on every filled share; 2 seconds of extra delay; 10- and 60-second entry delays. These never replace a failed primary. |

## Collection, stopping and decision

Before activation, checkpoint the source hash, rule JSON, collection start and the discovery manifest format. Verify UTC time synchronization, receipt logging, full book snapshots plus incremental updates, connection markers, token orientation and per-market fee, tick, minimum size and delay.

The endpoint is the first **200 funded primary games, or 183 days after activation, whichever comes first**. Fewer than 100 funded games make the result inconclusive regardless of its sign. Do not extend the endpoint because results are unfavorable, and do not stop early after profits. Data-integrity interruptions are logged. Changing any rule requires a new version and a new evaluation interval.

At the endpoint, report attempts, fills, partial fills, no-fills with reasons, allocated capital including fees, net cash, cash-weighted ROI with a whole-game 95% bootstrap interval (2,000 resamples, seed 0), equal-game ROI, results after removing the best 1, best 3 and best 1% of games, calendar and league segments, and coverage of unmapped or unmatched games.

The result qualifies for a subsequent small real-money execution experiment only if all of the following hold:

- Primary net cash and the interval's lower bound are positive.
- The +1¢ and +2-second sensitivities stay positive.
- The primary beats both matched controls.
- The conclusion does not depend on a few games or on favorable missing data.

Passing is evidence, not permission to risk money. An interval that crosses zero is inconclusive; a negative result stays negative.
