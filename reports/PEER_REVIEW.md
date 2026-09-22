# Independent peer review — 2026-09-22

**Verdict: Claude built a useful research foundation and addressed most of the original questions, but this project has not demonstrated an executable trading edge. Several backtest assumptions need repair before either positive or categorical negative conclusions deserve full confidence.** The latest request for new football, soccer and baseball studies was only partially delivered.

Reviewed state: commit `4fe2138`, plus the nine pre-existing untracked football study, ledger-exporter and report files. This is a review, not a repair or a live-trading authorization. Application code and cached research data were preserved. The 2024 legacy scripts were not treated as Claude's new work.

## Your original intent and what was delivered

The original human prompt was recovered from Claude session `a3336124-029d-461c-8aa7-6f1b04b98478`, transcript line 8, timestamp **2026-09-18 21:52:44 UTC**. Its key requests were:

> “if i'm watching a baseball game live and i see a homerun i can place a bet before the market moves (timing)”

> “what are the typical polymarket odsd and std deviations from this number. can we use these historical odds to trade to the average”

> “so analyze the pipeline make sure we are able to collect the data to be able to tests our hypothsis.”

The private source is `~/.claude/projects/-mnt-data-projects-polymarket-arbitrage/a3336124-029d-461c-8aa7-6f1b04b98478.jsonl`. Full transcripts were not copied into the repository.

| Request | Delivery | Assessment |
|---|---|---|
| Modernize data collection | Gamma discovery, trade tapes, MLB play-by-play, live recorder, Parquet caches | Substantial implementation; completeness and point-in-time correctness still need work. |
| Typical odds, dispersion and eventual wins by score/inning | H2 state tables and MLB baseline | Implemented; distinguish descriptive averages from a conditional forecast for a particular game. |
| Trade toward historical averages | H3 state/model comparisons and inning-discount rules | Implemented as **hold-to-resolution** bets. An exit when price returns to the estimated mean, with exit costs and a time limit, was not tested by H3. |
| Bet pregame favorites and ride to the end | MLB H1, all-sport favorites and threshold grids | Implemented; sample selection and execution qualifications below apply. |
| Beat repricing after a play | Historical scoring-play study and one-night live capture | Useful measurements; no end-to-end proof that an order submitted after an observed play can fill profitably. |
| Find and mirror skilled bettors/whales — line 1357 | Wallet selection, FDR, delayed-copy studies and monthly reselection | Broad implementation; monthly selection leaks future information. |
| Invent and test structural hypotheses — line 2253 | 12-study Thorp program, individual reports and adversarial reviews | Substantial work, including candid rejection of weak results. No verified profitable rule. |
| Show every test/trade in a web interface — line 2997 | Static explorer, JSON ledgers and paginated FastAPI desk | Useful delivery; ledger provenance and deployed index require correction. |
| New football/soccer/baseball hypotheses and sport tabs — line 3426 | Event data and three local football studies, existing sport tabs | Partial. The workflow hit Claude's weekly limit; the planned soccer/baseball studies and `reports/SPORTS.md` synthesis were not completed. |
| Breaking point, then every percentage from 50¢ — lines 3600/3698 | Calibration tables, threshold sweep, EV table and whale probe | Implemented, but the headline weighting and “holdout” interpretation need qualification. |
| Explain why dollar weighting cannot work — line 3842 | Decomposition of the 74–75¢ anomaly | The warning against blindly copying historical aggregate returns was warranted; declaring one bet per market the only tradable weighting was not. |

The final failed workflow notification is at transcript line 3970. It reports 13 completed and 13 failed agents; failure messages identify the quota limit. Current local files sometimes contain more review work than that notification's summary, so this review uses the actual files as the final evidence.

Claude deserves credit for separating the legacy code, building a usable historical dataset, correcting token/outcome orientation, rejecting frozen price bars, accounting for fees, using game-clustered uncertainty, and documenting failed hypotheses. Those are valuable foundations. The main shortfall is the gap between descriptive transaction research and a causal, capacity-constrained trading test, compounded by incomplete final delivery.

## Findings, ordered by effect on the edge search

### 1. High — volume-selection bias was reduced, not eliminated

The source moneyline tape universe was selected using **at least $50,000 in eventual total volume** (`pmsports/__main__.py:49`, `pmsports/wallets/tapes.py:30`). Favorites then require only **$25,000 pregame volume** and claim that this guarantees admission regardless of outcome (`pmsports/analysis/favorites.py:178`). It does not: a game with $30,000 before kickoff and $10,000 afterward is missing, while an otherwise similar game with $30,000 afterward is included.

Calibration and whale studies inherit the same restricted universe (`pmsports/analysis/calibration.py:44`, `pmsports/analysis/whale_prices.py:37`). Their pregame entries also use the *completed* pregame-volume total, which was not yet known at an earlier entry time. Calling these samples selected on “pregame information only” is too strong.

The pipeline review counted **12,301 resolved moneylines since 2025 with total volume between $25k and $50k** in the discovery universe. They were not generally collected by the default tape run, so the headline sample cannot determine which would satisfy its pregame criterion.

**Required correction:** discover the opportunity universe without a future-volume filter and use cumulative volume available at each decision. A pregame-volume floor at least as large as the collection floor is a useful diagnostic within existing data, not a substitute for a coverage audit. Recompute both favorites and underdogs; do not assume the correction helps either side.

### 2. High — monthly wallet selection sees future trades and outcomes

`pmsports/wallets/study.py:170` aggregates each wallet/market position over the entire tape and assigns it the first fill's timestamp. Line 176 then selects these whole positions as historical observations. A market first traded in January can therefore bring February fills and its eventual result into the February 1 ranking.

The independent reproduction in `peer_review_checks.py` uses a $0.50 January purchase and a $900 February purchase in the same market. On February 1 the current selection sees **$900.50 cost and $100.50 eventual profit**, instead of only the pre-boundary **$0.50 cost**. Even slicing trades first is insufficient unless the ranking uses only results known by the cutoff.

**Required correction:** truncate fills before each selection time, require settlement/outcome availability before scoring, and handle open positions separately. The separate 2025/2026 study does slice fills first, so the specific whole-position defect must not be generalized to every wallet result; its outcome-availability boundary still needs auditing.

### 3. High — historical prints do not establish executable entry prices or capacity

`pmsports/analysis/calibration.py:83` selects the very print that first triggers the threshold and buys $100 at its price. Observing that print does not let a new order trade retroactively against it. Pregame medians similarly summarize prior transactions rather than available quotes. These can support descriptive price studies, but “executable” is an overstatement.

`pmsports/analysis/whale_prices.py:54` improves timing by looking later, but it can reuse one later print for several signals, does not check its size, and does not exclude the signal wallet's own continued fills. The general wallet copier excludes that wallet, but also treats subsequent prints as execution proxies.

In the independent combined 74–75¢ holdout probe, 550 delayed signal entries mapped to only **511 distinct prints**. With one signal per event and the signal wallet excluded, **83.1% of the 248 entry prints were below $100**. This does not prove $100 was unavailable elsewhere; it proves the tape does not establish that capacity.

The untracked football ladder-gap study records first-print sizes (`h_fb-ladder-key-number-gap.py:385`) but sizes a $100 pair without using them (`:488`). Its capacity summary sums subsequent prints at potentially different prices (`:807`). A pair also needs both legs, correct collateral accounting and an explicit failed-hedge policy.

The strategy reviewer checked the actual equal-share requirement `100 / (entry_lo + entry_hi)`: just **1 of 9 primary ladder-gap pairs** had sufficient size in both first prints for the assumed pair. The monotonicity study similarly liquidates an entire failed hedge at a subsequent opposite-side print without capping exit size (`h_fb-ladder-monotonicity-arb.py:577`). Both studies already conclude DEAD; these defects weaken capacity estimates rather than reveal a profitable arbitrage.

**Required correction:** separate signal time, receipt time, order eligibility time and fill time; replay bid/ask depth with size and partial fills. Enforce a per-event exposure cap and consume available liquidity only once. Current Polymarket documentation confirms configured sports order delays and that fills depend on available resting orders. [Order lifecycle](https://docs.polymarket.com/concepts/order-lifecycle).

### 4. High — the original MLB model needs a state/price alignment audit

`pmsports/panel.py:44` retains ordinary third-out plate appearances, adds correctly advanced checkpoint rows, then resets the original rows' outs to zero at line 62 without advancing their inning/half. H3 and the empirical baseline can therefore include a nonexistent state such as “top fifth, zero outs” immediately after the top fifth ended. The checkpoint-only H2 tables are not affected by this particular duplicate-state defect.

Prices for each state are medians from **15–75 seconds after the play** (`pmsports/panel.py:141`). The window is not bounded by the next state change, and the median is not known until its window ends. H3 then treats it as the state price and an entry reference. This can mix later information with an earlier score/base/out state.

The pipeline reviewer measured **45,420 of 137,384 non-checkpoint panel rows (33.1%)** whose next plate appearance's contact occurs before the +75s window ends. This is a material corpus issue, not just a synthetic corner case. It does not establish the sign of the resulting bias.

**Required correction:** emit one valid state transition, preserve game type for extra-inning rules, set an explicit decision timestamp, and price a subsequent order against the state actually known then. Rebuild the baseline/panel before retesting model-versus-market performance.

### 5. Medium — the “holdout” is now explored data, and dollar weighting was dismissed too strongly

The reports use July–September 2026 repeatedly across strategy families, sport/price cuts, later variants and review-driven changes. Some revisions are explicitly disclosed, which is good. Nevertheless, the next rule chosen from these findings cannot use the same period as a fresh confirmatory test. Positive development and holdout point estimates are insufficient, especially after many searches.

`pmsports/analysis/calibration.py:422` calls one bet per market “the only weighting that matters for trading.” That is incorrect. Equal-dollar-per-signal, proportional copying, capped conviction sizing and one-bet-per-event are different implementable policies **if their weights are knowable at entry and executable at the chosen size**. Failure of one policy does not disprove the others.

Likewise, the absence of an obvious mechanism distinguishing 74¢ from 73¢ is a reason for skepticism, not a statistical disproof. The right verdict is **unexplained and unvalidated**, with a fresh test required.

A smaller statistical reporting issue is `h_fb-key-number-wp-step.py:374`: the reported one-sided “p-value” is the fraction of ordinary bootstrap sample means below zero. That distribution is centered on the observed sample, not a simulated null; it should not be presented as a calibrated hypothesis-test p-value. This does not rescue the study: its confidence interval already spans substantial losses and gains.

### 6. Medium — changing inputs or parameters can silently reuse old results

`pmsports/wallets/report.py:42` caches studies as `families.pkl`, `walkforward.pkl`, etc. The keys omit the input-data version, code version, split date and evaluation window. A rerun after adding data, fixing side interpretation, or changing the split can load old results and render a newly dated report.

**Required correction:** record and validate a cache manifest keyed by code/data/parameters. The report should expose those identities and refuse mismatched caches. This is especially necessary when repairing the defects above.

### 7. Medium — the running desk hides new studies and does not expose every trade

Independent HTTP checks returned 200 for health, index and direct football-study requests. Health reported **25 ledgers**, but `/api/index` listed **22**. The missing slugs are `fb-key-number-wp-step`, `fb-ladder-key-number-gap`, and `fb-ladder-monotonicity-arb`. `pmsports/webapp/server.py:70` trusts an existing `_index.json` indefinitely; the newer files are not discovered until reindexing. This is a live delivery defect, not a missing-data explanation.

Four source ledgers were truncated before the paginated server read them: halftime 65,040 → 20,000 rows; MLB inning breaks 34,120 → 20,000; secondary props 44,295 → 20,000; soccer Under/BTTS 190,954 → 20,000. Server pagination cannot recover those missing rows. The ledger specification permits this cap, but the human request at transcript line 2997 explicitly asked for every trade. Likewise, all 50 price thresholds exist analytically, but only a few have individual web ledgers.

Sport tabs filter trade rows, while the Development/Holdout cards still render the all-sport `meta.headline` (`pmsports/webapp/static/app.js:85`). For `favorite_at_90`, the all-sport holdout card is −0.856%, while the football holdout trade filter returns −1.145% over 65 trades. The cards should be recalculated for the selected scope or plainly labeled as global.

**Required correction:** refresh index/cache from ledger identities or modification times; export full server-side ledgers; distinguish overall from filtered metrics. Commit the finished football artifacts only after checking their current state. They were pre-existing untracked work and were deliberately not staged by this review.

### 8. Medium — documented installation omits the web server dependencies

`requirements.txt` omits `fastapi` and `uvicorn`, imported by `pmsports/webapp/server.py:17` and `pmsports/webapp/__main__.py:7`. The existing environment contains them, so this host runs, but the README's clean-install sequence does not establish a runnable desk. Add these dependencies and validate a fresh-environment startup. The 11 existing tests do not exercise this path, index freshness, ledger completeness or UI filter semantics.

### 9. Medium — “every fill” actually means a bounded collection window

`pmsports/wallets/tapes.py:37` starts each tape only 24 hours before the scheduled game. Earlier trading is omitted by design, yet the tape and wallet reports describe all fills and `pre_usd` as pregame volume. The pipeline review found 15,653 of 38,364 tapes with their first observed fill within one hour of this artificial boundary, making truncation a substantial coverage concern. The first timestamp alone does not prove an earlier trade existed in every such market.

A direct read-only API check independently confirmed missing earlier trades for all three large markets sampled: `ucl-psg-ars-2026-05-30`, `fifwc-bel-sen-2026-07-01`, and `nfl-sea-ne-2026-02-08`. Each request for the interval 48–24 hours before scheduled start returned three fills inside that interval, outside the collector's requested window.

**Required correction:** collect from market creation or disclose and consistently apply the window. Audit coverage at both ends, reconcile fill counts/notional against a second source, and recompute pregame-volume and wallet statistics after any backfill. Do not silently label the existing window as lifetime activity.

### 10. Medium — UTC rollover prevents analysis of an existing live capture

The recorder rotates each output file by UTC day (`pmsports/record.py:45`), but writes game metadata only when a token is first discovered (`:148`). Games continuing across midnight therefore produce next-day book/score files without a next-day `games.jsonl`. `pmsports/analysis/live.py:34` requires that file unconditionally.

The September 19 directory actually contains about **2 GB of book messages**, MLB updates and sports updates, but no game metadata. The advertised `live-latency --day 2026-09-19` path consequently fails before analysis. The capture is recoverable using the preceding day's metadata; it should not be discarded. Snapshot active games on rollover or explicitly load the relevant metadata across day boundaries.

## Independent check of the 74–75¢ lead

I reproduced the existing signal selection from the compact cache and tested concentration and later-entry sensitivity. “74¢/75¢” here means the original code's ±0.5¢ bands, not exclusively exact ticks. The table uses historical market fees and game-clustered 95% bootstrap intervals. These are exploratory results on the already-seen July 1 onward window.

| Policy | Holdout observations | After-fee ROI | 95% interval |
|---|---:|---:|---:|
| Every ≥$10k signal near 74¢, original signal price | 326 signals / 150 games | +8.64% | −2.20% to +17.21% |
| Every ≥$10k signal near 75¢, original signal price | 245 signals / 147 games | +7.74% | −0.63% to +15.37% |
| Combined bands, first signal per game | 254 games | +3.96% | −2.85% to +10.23% |
| Combined, first signal per game, next other-wallet print ≥3s later | 248 games | +3.93% | −3.25% to +10.41% |
| Same delayed rule, in-play only and ≥$50k pregame volume | 130 games | +0.65% | −9.45% to +10.01% |

For completeness, keeping **all** combined-band signals gives +8.52% [0.88%, 15.68%] at signal prices, and +9.59% [2.31%, 16.38%] using later other-wallet prints. I do not discard these positives. They motivate auditing repeated exposure, public signal availability and liquidity; they do not establish a capacity-valid, selection-corrected strategy. The later probe still reuses some prints, while the narrower one-per-game checks change the strategy's weights.

The stricter in-play/volume subset is a sensitivity check, not a newly chosen winning rule or proof that the original effect is false. At the original signal price its +0.31% becomes −0.98% under an additional 1¢ price cost. It is particularly fragile relative to execution uncertainty.

## Which ideas deserve more work?

| Lead | Review judgment | Next useful evidence |
|---|---|---|
| Large visible trades near 74–75¢ | Best unresolved signal in this review; no deployment-quality evidence | Audit raw transactions/side mapping, deduplicate parent-order fragments, measure actual signal receipt and subsequent depth. Then freeze a capped sizing rule and test on new games. |
| Esports between-map overreaction | Exploratory; 53-bet headline is highly uncertain | Preserve the original pre-holdout, round-1 calibration and realistic waiting/size rules. That version is only +3.5% and −1.5% with +1¢, much weaker than the +29.3% headline. The later calibration used development data, but was revised after the first holdout had been inspected. |
| Maker strategies in quiet intervals | A distinct microstructure hypothesis, not proof from taker negatives | Live queue/depth replay, cancel latency, adverse selection, inventory unwind and fees. A positive markout is not realized profit. |
| Football key-number win probability | Inconclusive and weakly aligned with its own mechanism | The local report already downgrades it: 49 holdout bets, CI −10.5% to +23.0%; top-five removal turns it negative. The rule's selected directions at margins 3/6 contradict its stated mechanism. |
| Broad favorite/threshold/inning-average rules | No convincing positive evidence in the implemented versions | Correct the shared data/causality defects before treating the negative numbers as definitive. Avoid another broad parameter sweep. |
| TV/free-feed reaction trading | No demonstrated advantage | Measure observation-to-eligible-order-to-fill latency on the chosen venue. Eight live scoring events do not establish a universal timing bound. |

The other Thorp studies already fail on negative returns, uncertainty, execution or fragile unhedged gains: late soccer draws, soccer O/U versus moneyline, Over/BTTS demand, runline truncation, tennis completion, certainty premiums, secondary-market making, soccer halftime, esports identity pairs and unplayed-map clauses. Their negative or disputed verdicts should remain research outcomes rather than evidence that an entirely different implementation cannot work.

## A focused path to an edge

1. **Repair the measurement layer first:** point-in-time universe, valid game states, causal wallet ranking, versioned caches, and an execution model shared by the studies.
2. **Freeze one whale-signal experiment:** exact bands, minimum observable trade size, handling of same-wallet/order fragments, delay, allowed entry price, stake/exposure cap, exit rule and all exclusion rules. Keep a nearby-price comparator. Do not pick those settings from a new holdout after seeing its result.
3. **Collect new prospective evidence:** record every signal, including unavailable and unfilled entries; receipt clocks, book depth, actual market delay/fees and rejected/canceled orders. Begin with paper execution. Size and capacity must be established before interpreting dollar profit.
4. **Use game-level net outcomes and a fixed stopping rule:** report uncertainty, turnover, drawdown, capital occupied and net dollars per day. Set the sample horizon from the minimum useful net edge and expected variance; a few weeks or a favorable streak is not a statistical criterion.
5. **Revisit literal mean-reversion exits only as a separate registered test.** An exit at fair value or a time stop answers a different question from holding the bet to settlement and introduces a second spread/fee.

Your intuition that a small probability advantage can compound is sound **only when the advantage exceeds the purchase price and costs**. At 50¢, the documented sports fee adds 1.25¢ per share. A true 51% winner therefore has expected profit `0.51 − 0.50 − 0.0125 = −0.0025` per share before slippage, about −0.49% on capital. The project's basic fee arithmetic is correct. [Polymarket fees](https://docs.polymarket.com/trading/fees).

## Validation and limits

- Existing suite: **11 tests passed** with `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests`.
- Independent bounded diagnostics: `reports/peer_review_checks.py`, run under a 3 GB memory cap; aggregate output is `reports/PEER_REVIEW_EVIDENCE.json`. They reproduce the wallet-boundary defect and whale sensitivity tables without changing input caches.
- Three independent read-only review workers covered data collection, strategy studies, and prompt/UI delivery; the primary reviewer checked findings against source code and concrete evidence.
- Operational inspection: the existing user service was enabled and active; the desk returned HTTP 200 over the host's Tailscale address. The primary reviewer independently confirmed the 25-versus-22 ledger/index mismatch. No service was restarted or reindexed. Browser rendering on desktop/mobile was not visually inspected; this review's UI findings concern source logic and HTTP/data behavior.
- Current primary-source documentation supports the fee formula, sports delay requirement and time-window API pagination. It does not prove that historical quotes were available to this strategy. [Trade API](https://docs.polymarket.com/api-reference/core/get-trades-for-a-user-or-markets).
- This review did not recollect all market history, rerun every expensive study, submit orders, deploy changes or claim a profitable trading system. Passing unit tests is not evidence of statistical validity.

**Acceptance:** suitable as a research workbench after the identified corrections; not accepted as evidence of an executable edge. The immediate value is a narrower, falsifiable next experiment and a clearer account of what remains unproven.
