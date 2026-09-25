# Project viability after independent repair

Assessment: 2026-09-23 UTC. Final wallet acceptance and deployed-desk verification are still being completed; the pending rows below must be resolved before this assessment is final.

**The project is useful for research. It has not demonstrated a trading edge ready for real money.** The strongest accepted leads are a deeper MLB inning-state discount and soccer added-time leaders. The MLB threshold was selected within an inspected grid; soccer weakens materially with longer entry delays. Both assume optimistic historical execution. The repairs make the historical results more credible, while exposing the execution and statistical limits.

## What your prompt actually asked

Your original prompt was recovered: Claude session `a3336124-029d-461c-8aa7-6f1b04b98478`, September 18, 2026, 21:52:44 UTC, transcript line 8. You asked whether observing a home run could beat repricing, what odds and standard deviations usually accompany a game state, and whether prices could be traded back toward their historical average. Later requests added favorites, sharp-wallet copying, every price threshold from 50¢, a complete trade desk, and three football/five soccer/four baseball hypotheses. [Original prompt mapping and peer review](PEER_REVIEW.md).

Claude built much of the foundation, but the mean-reversion backtest originally held to settlement instead of exiting at the mean; some wallet rankings used future information; fills and multi-leg capacity were overstated; stale/truncated desk exports hid results; and the last sports work stopped at the quota limit. The repair now tests a literal mean exit separately, separates decision and execution clocks, uses an explicit closure-based outcome-availability proxy for selection, retains partial/unfilled orders and failed hedges, versions caches, repairs game states, and exports complete ledgers for the corrected rules. [Checklist](REPAIR_CHECKLIST.md), [validation](REPAIR_VALIDATION.md), [execution contract](EXECUTION_PROTOCOL.md).

## Corrected results

Returns below divide net modeled cash by allocated capital including fees. Capital is summed across historical trades, not simultaneous account equity. Intervals are nominal game-cluster bootstrap intervals, not adjusted for searching many correlated rules. Historical evaluation periods were already inspected; none is a new confirmatory holdout. Independent variants overlap and must not be added together as portfolio profit. “2026 evaluation” starts January 1; “Jul onward” starts July 1, each ending with available local history. These boundaries do not establish complete market coverage. Favorites, thresholds, whales, soccer and wallet policies use decision/signal time; MLB model splits use game dates, and football/esports/baseball continuation splits use scheduled game/series start. Pooled rows combine development and evaluation; baseball totals has only two funded July-onward games.

| Test | Tested period (UTC) | Funded sample | Net ROI | Nominal 95% interval | Interpretation |
|---|---|---:|---:|---:|---|
| All-sport pregame favorites | 2025–2026 pooled | 14,442 | −3.18% | −4.79% to −1.40% | Broad benchmark loses. |
| Two-way pregame underdogs | 2026 evaluation | 7,325 | +1.16% | −4.38% to +7.28% | Inconclusive; pooled 2025–2026 ROI −1.06%. |
| Every threshold from 50¢ through 99¢ | 2026 Jul onward | 50 rules | 49 negative | None has a positive lower bound | Only 91¢ is positive: +0.0566%, interval −0.8784% to +0.9964%. |
| MLB model-versus-market, original 2¢ gap | 2026 evaluation | 2,037 | −9.40% | −16.7% to −2.2% | All five 2–10¢ gap variants lose. |
| Historical one-standard-deviation discount, settlement exit | 2026 evaluation | 1,998 | −7.68% | −16.2% to +0.8% | No established cheap-versus-average advantage. |
| Literal return-to-mean exit, received books | 2026 Sep 19 capture | 12 games | −7.27% | −9.38% to −5.24% | All 12 positions lose in the captured session. |
| React to MLB score-feed receipt, 3s | 2026 Sep 19 capture | 15 games | −4.57% | −40.63% to +27.18% | No demonstrated free-feed timing profit. |
| React to Polymarket score-feed receipt, 3s | 2026 Sep 19 capture | 15 games | −7.47% | −43.53% to +23.93% | Same conclusion; tiny one-day sample. |
| Original inning discount ≥3¢, first signal/game | 2025–2026 pooled | 2,824 | +0.42% | −3.9% to +4.3% | Original rule shows no reliable advantage; 2026 alone −0.11%. |
| Inning discount ≥10¢, first signal/game | 2025–2026 pooled | 1,229 | +7.32% | +1.0% to +13.5% | Strongest accepted historical lead; exploratory grid result. |
| Adjusted inning model, leader edge ≥8¢ | 2026 evaluation | 875 | +6.54% | Approximately 0% to +13.0% | Fragile secondary: the 10¢ variant reverses to −0.57%. |
| Whale 74/75¢, first signal/event | 2026 Jul onward | 247 | −0.49% | −12.33% to +10.78% | Original one-event sizing does not establish an edge. |
| Same whale bands, capped proportional sizing | 2026 Jul onward | 440 entries / 248 games | +4.04% | −6.86% to +14.01% | Positive lead; +1¢ stress +2.71%, still uncertain. |
| Esports map-break, original frozen rule | 2026 Jul onward | 59 | −17.87% | −73.48% to +46.13% | Original rule fails; a later positive refit cannot replace it. |
| Football key-margin rule | 2026 Jul onward | 39 | +5.65% | −24.85% to +30.47% | Control also positive; original key-specific mechanism fails its stated gate. |
| Football ladder monotonicity pairs | 2026 Jul onward | 69 signals / 30 games | −17.34% | −38.26% to +4.02% | Failed/partial hedges remove the apparent arbitrage. |
| Baseball totals pace | 2026 pooled | 19 games | +48.69% | −66.58% to +216.87% | Only $246.76 total capital; remove best three games and ROI is −56.02%. |
| Baseball first-inning demand | 2025–2026 pooled | 84 games | +5.08% | −30.97% to +46.31% | Original equal-game ROI −11.45%, interval −33.40% to +10.99%; unstable across periods. |
| Baseball runline transition | 2025–2026 pooled | 131 games | −14.64% | −42.55% to +14.31% | No demonstrated derivative-model edge. |
| Soccer early red-card opponent | 2026 Jul onward | 61 games | +20.96% | −1.36% to +43.23% | Full literal-BUY replay accepted; +1¢ return +18.88%, both intervals cross zero. |
| Soccer early substitution opponent | 2026 Jul onward | 47 games | +26.89% | −36.59% to +104.91% | Coherence check passes; removing best three games turns profit into a $244.21 loss. |
| Soccer post-goal three-leg pair | 2026 Jul onward | 27 signals / 25 games | −2.15% | −11.28% to +3.40% | Every funded signal has an incomplete hedge; +1¢ return −6.05%. |
| Soccer draw-anchor pair | 2026 Jul onward | 52 games | +4.73% | −9.20% to +21.48% | Only $391.15 capital; 47 failed hedges; no confirmed anchor mechanism. |
| Soccer added-time leader, +1¢ headline | 2026 Jul onward | 180 games | +5.80% | +1.22% to +9.32% | Outperforms both matched controls; DEV −0.26%, longer-delay intervals cross zero. |
| Causal wallet selection and copying | 2026 evaluation | Pending final acceptance | — | — | Unsampled full-corpus run must replace this row. |

Sources: [favorites](FAVORITES.md), [50-point sweep](CALIBRATION_POINTS.md), [MLB models](REPORT.md), [inning grid](THRESHOLDS.md), [whales](WHALE_REPAIRED.md), [received books](research/LIVE_EXECUTION.md), [esports](research/esports_break_overreaction.md), [football WP](research/fb-key-number-wp-step.md), [football pairs](research/fb-ladder-monotonicity-arb.md), [baseball continuation](research/BASEBALL_CONTINUATION.md), [soccer continuation](research/SOCCER_CONTINUATION.md).

The favorites interval above was recomputed from the full ledger using capital weights. The equal-game interval in the older `favorites_overall.csv` refers to a different statistic and must not be attached to cash-weighted ROI. For the adjusted 8¢ leader row, event-identifier ordering changes the fixed-seed bootstrap lower bound from +0.068% to −0.042%; treat it as effectively zero. Its equal-game ROI is −1.77%. The cash totals agree, and neither weighting alone proves an artifact. Zero-signal results remain visible: the baseball one-run pair and football key-number ladder gap produced no primary orders in their observed opportunities. Missing opportunities are a coverage limitation, not proof that those markets can never misprice.

## Where an edge might remain

The **10¢ inning-discount rule** generated $2,969.81 net modeled profit on $40,559.49 allocated capital. Adjacent 5¢/8¢ rules are also positive, although their intervals include zero. That is more interesting than a single positive price bucket. However, 984 of its 1,229 fills were partial and 197 signals got no fill. Historical state timestamps precede real feed receipts; later transaction size does not prove accessible book depth. It remains positive after removing its best three games (+6.16%) and best 1% (+3.07%), and in both 2025 (+7.38%) and 2026 (+7.21%). Each year alone has an interval crossing zero; equal-game ROI is only +2.27%. [Complete concentration and grid audit](LEAD_ROBUSTNESS.md). The [prospective paper specification](PROSPECTIVE_PROTOCOL.md) freezes this candidate, an unchanged 3¢ comparison, receipt-time execution, costs and a fixed endpoint. It is not deployed or completed, and does not authorize orders.

The **soccer added-time leader rule** returned $469.87 on $8,095.95 allocated capital after the declared extra cent of cost: +5.80%, nominal interval +1.22% to +9.32%, across 180 funded games. It beats both prespecified price-matched control windows, and remains +4.66% after removing its best three games; equal-game return is +4.43%. However, development return is −0.26%, and the existing 10-second and 60-second entry-delay cases fall to +1.74% and +1.02%, both with intervals crossing zero. This is a credible exploratory lead whose real receipt timing and depth could erase the apparent advantage. It does not replace the already-frozen MLB paper experiment. [Soccer controls and robustness](SOCCER_ROBUSTNESS.md).

The **soccer early-red-card rule** is another research candidate. The accepted full rerun and independent fixed-order check enforce the original literal taker-BUY instruction, giving development +16.83%, evaluation +20.96%, and evaluation +18.88% under an extra cent of entry cost. Both evaluation intervals cross zero. Only 61 evaluation games fund. At +1¢, removing the best three leaves +$70.88 on $2,230.51 (+3.18%), with wide uncertainty. One old +$285.54 winner becomes +$5.04 because the actual BUY contains only 6.8 shares; SELL-complement volume cannot supply its entry. The rule waits 300 seconds after a qualifying card and buys the opponent, but measured ESPN receipt clocks and soccer depth are missing. The declared late-card, card-side branch loses 55.70% across 22 funded signals, so it does not support the proposed early/late mechanism. Its matched non-card diagnostic remains an explicitly unrun optional variant.

The **proportional whale policy** earned $230.74 on $5,715.54 total allocations; median allocation was about $10.26. It demonstrates that legitimate decision-time dollar weighting can differ from one-bet-per-event weighting. It does not demonstrate statistically reliable or scalable profit. Nearby 73/76¢ bands lost under both policies, public signal receipt latency is unknown, and the primary interval includes material losses.

Positive cash-weighted baseball totals/first-inning estimates and football WP estimates are much weaker: few funded games, small capital, broad uncertainty and concentration. The original first-inning test specifically requested equal-game returns: −11.45%, with an interval from −33.40% to +10.99%. Its later-period equal-game result is +28.43% across 36 games, with an interval still crossing zero (−5.94% to +60.84%). Football's manufactured six-point-margin artifact was removed with a prefix-only raw-score agreement gate; the remaining 39-game evaluation profit becomes a $113.16 loss after dropping the best three games. The required non-key control is also positive (+0.47%), so the original mechanism's own falsification condition is met. This is not proof that the control has an edge.

## Answer to the original timing idea

The original typical-odds and standard-deviation question is covered by the game-state tables in [the H2 analysis](REPORT.md). Those historical distributions describe the observed sample; they do not establish that a discounted quote can be bought profitably.

The recovered September 19 diagnostic contains 61 scoring events across 14 games selected for a subsequent move of at least 3¢. Median book half-repricing occurred 2.856 seconds after the retrospective play clock; MLB-feed receipt took 41.095 seconds and the Polymarket sports feed 71.412 seconds. Those feeds beat the book on only 1/61 and 0/61 selected events. This is adverse evidence for reacting to those free feeds. It does **not** measure your television/in-person viewing latency, every scoring event, or a universal speed limit.

The separate trading replay retains all 84 observed score increases per feed, with one attempt per game, and uses received depth rather than the diagnostic's future-move screen. Its three-second primary results lose. The mean-exit study also loses after entry/exit fees, with all acquired shares closed and no remaining inventory. One already inspected capture is enough to reject claims of demonstrated success, but too little to rule out all possible implementations.

## What cannot be repaired from the existing data

The compact corpus preserves 53,771,660 source rows and 38,370 market IDs; three invalid negative prices are excluded from economic calculations. The repaired MLB panel has 267,781 states across 4,570 games. These counts are coverage facts, not proof of completeness. The inherited universe used future volume and bounded tape windows. Finite backfills recovered missing history, but do not reconstruct every omitted market or prove provider completeness. Gamma volume and price-times-size notional use different units in the diagnostic; they cannot be casually treated as equal totals.

Historical taker prints do not reveal our possible queue position, order acceptance, contemporaneous ask/bid depth, minimum admissible size or successful cancellation during sports delays. Soccer has no captured live books; some supplemental soccer tapes discarded acquired-side identity and may serve only as reference observations. Confidence intervals cannot correct those measurement limitations or erase repeated strategy selection. Actual forward receipt/depth data are required.

There is no defensible $/day income projection or capital allocation recommendation from this project. The archived [Thorp report](THORP.md) preserves the original work but its old profit claims are superseded. Some legacy negative structural screens remain archived analyses rather than newly validated live-book tests. Passing software tests establishes behavior under the tested conditions, not a profitable strategy.

## Delivery and reproducibility

Final code/test/deployment acceptance is pending. Complete command and provenance evidence will be linked through [REPAIR_VALIDATION.md](REPAIR_VALIDATION.md), and the final sport-by-sport map will be in `SPORTS.md`. Raw data, captures and full JSON/Parquet ledgers remain local under ignored `data/`; repository reports and source are pushed separately. No real-money trades have been submitted.
