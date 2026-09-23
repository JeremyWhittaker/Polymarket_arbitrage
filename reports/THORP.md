> **Archived analysis — superseded by the September 2026 repair.** This report preserves Claude's original reasoning and review history. Its returns, “fresh holdout” descriptions, execution assurances and daily-profit projections are not accepted results. Use [the repair checklist](REPAIR_CHECKLIST.md), [corrected threshold results](THRESHOLDS.md), [original-rule esports rerun](research/esports_break_overreaction.md) and [execution limits](EXECUTION_PROTOCOL.md). The old $10–30/day estimate has no validated basis. No strategy here is approved for live trading.

# Thorp program: 12 edge hypotheses on Polymarket sports, backtested and attacked

*Written 2026-09-19. Development data runs up to 2026-06-30. The holdout is 2026-07-01 to about 2026-09-18, roughly
77-80 days. Per-hypothesis reports are in `reports/research/` and the code is in `pmsports/research/h_*.py`.*

## The short answer

**We found nothing that is profitable after costs.** None of the 12 hypotheses earned a PROFITABLE verdict. Here is
where they landed:

| Outcome | Count | Hypotheses |
|---|---|---|
| PROFITABLE | 0 | none |
| PROMISING, and the verdict survived review | 1 | Esports BO3 between-map break (the result is mostly noise) |
| DISPUTED: the author called it DEAD, but one reviewer says an execution fix makes it PROMISING | 2 | Esports game-4 "unplayed map pays 50-50" clause; MLB half-inning-break market making |
| DEAD | 9 | everything else |

We worked the way Ed Thorp did: read the contract rules and the market plumbing, find a structural reason someone
misprices, write the exact rule down, test it once on data it has never seen, then let skeptics try to break it.
Nearly every idea failed at one of three points:

1. **The price was already fair.** Draws late in tied matches, Over/BTTS, run lines, tennis "match completed", NRFI and
   totals are all priced within about 1-3c of what actually happens. That gap is the size of the spread plus the fee.
   The behavioural biases are real in the order flow (61-73% of soccer O/U and BTTS takers buy Over/Yes, for example),
   but they do not move the price.
2. **The spread in quiet windows belongs to whoever is already at the front of the queue.** MLB inning breaks, soccer
   halftime and thin side markets all show a real half-spread in the backtest. A new maker joins behind 7k-21k shares,
   though. What reaches the back of the queue is mostly fills where the quote has gone stale and gets picked off.
3. **Several headline profits were fills nobody could get, or plain luck.** Examples: sub-$1 fat-finger prints at 1-2c,
   naked legs that happened to win, and a few longshots carrying the whole period.

Since July 2026 the taker fee is 5% × p × (1-p), about 1.25c per share at 50c. That by itself is larger than most of
the mispricings we measured.

**Thorp's sizing rule says the stake is zero.** Thorp sized bets with the Kelly criterion, which stakes in
proportion to a *proven* edge. Every surviving edge here has a confidence interval that includes zero, and on that
lower bound Kelly says bet nothing. What is worth doing is a **paper test** of the one PROMISING rule, plus cheap
paper logs of the two disputed leads (Section 1). Even if all three are real, the realistic take is roughly
$10-30/day in total, because these markets are thin.

---

## How to read this report

- **DEV** is the development data, used to design and freeze each rule. Depending on the hypothesis it starts in 2025
  or 2026 and always ends 2026-06-30.
- **HOLDOUT** is 2026-07-01 onward. Each frozen rule was run on it once. The exceptions where it was re-run after
  review are flagged.
- **ROI per $1** is for taker bets: profit per dollar staked after the actual taker fee, held to resolution.
- **ROI on capital** is for maker strategies: profit divided by the collateral tied up.
- **c/share** is cents of profit per share traded.
- **[a, b]** is a 95% confidence interval, clustered by game or series so that correlated bets are not double-counted.
  When it includes 0, the data cannot tell the result apart from no edge at all.
- **+1c** means every fill priced 1 cent worse. This is the standard slippage stress.
- **Verdicts:**
  - **PROFITABLE:** holdout after costs > 0, the honest DEV interval lies entirely above 0, and no reviewer refuted it.
  - **PROMISING:** holdout > 0, but an interval crosses 0.
  - **DEAD:** holdout ≤ 0, or DEV significantly negative, or the edge only exists under fills that cannot be had.
- **Reviewers:** each hypothesis faced three adversarial reviewers:
  - *Look-ahead/selection*: does it use information from the future?
  - *Execution/fees/capacity*: could you actually get those fills, and how many dollars would it take?
  - *Statistics*: outliers, clustering, multiple testing.

  When a verdict was challenged, the author fixed the problems and a second round of reviewers checked the result.
- Taker and maker results use different units, so the ranking below is a judgment of **credible, realistic
  out-of-sample profit**. It is not a sort on one column.

---

## Ranking

Ranked by credible holdout profit after all costs, under realistic fills.

| # | Hypothesis | Final verdict | Credible holdout result (after fees, realistic fills) | Honest DEV result | Money it could make |
|---|---|---|---|---|---|
| 1 | **Esports BO3 between-map break overreaction** | **PROMISING** (survived review; mostly noise) | Primary +29.3% [-27, +92] per $1 on 53 bets, +22.8% at +1c. With the originally frozen calibration: +3.5% [-45, +61] on 60 bets, -1.5% at +1c. Mirror rule: +6.4% [-3.8, +16.5] on 230 bets, +4.7% at +1c | Cross-fitted: primary +5.8% [-39, +58]; mirror +3.7% [-4.4, +12.2] | About $100-350/day can be deployed. At a 5% edge that is $5-20/day |
| 2 | Esports game-4 "unplayed map pays 50-50" clause | **DISPUTED**: DEAD per author, PROMISING (post-hoc) per execution reviewer | As registered ($1 fill-or-kill): +4.0% [-12.3, +20.5] on 94 bets, +1.4% at +1c. With a 0.50 limit price added after the holdout: +10.2% [-8.0, +29.2] on 84 bets, +7.4% at +1c | +5.0% [-12.0, +23.7]; with the limit, +10.3% [-8.9, +29.3] | About $500-1,000 staked per month, so tens of dollars a month |
| 3 | MLB half-inning break market making | **DISPUTED**: DEAD per author, PROMISING on markout per execution reviewer | After the reviewer's "never quote through the other side" fix: markout +0.25c/share [+0.21, +0.28], i.e. +0.50% on capital, about $8.5/day. Held to resolution: -1.6c/share [-3.7, +0.6], -$5.4k. Pre-registered queue-stress gate fails (-0.37c) | +0.13c [+0.06, +0.17] after the fix; -0.09c [-0.14, -0.04] before it | About $8/day of markout; negative if the inventory is held |
| 4 | Soccer halftime market making | DEAD | Realistic queue -0.28% on capital [-0.82, +0.12]. Back-of-queue -1.95% [-3.47, -0.88] | Realistic +0.08% [-0.08, +0.22] | About -$40/month |
| 5 | Late-match draw in tied soccer matches | DEAD | -2.0% [-15.6, +12.9] on 158 bets; -3.7% at +1c. Before fees +0.1% | -3.6% [-12.4, +5.2] on 415 | A few hundred $ per game; losing |
| 6 | "Prop tax" market making in MLB NRFI and total-8.5 markets | DEAD | -2.1% of notional [-4.7, +0.4] held to resolution; -4.1% at +1c | +0.8% [-1.3, +3.1] | $5-6k notional/day; -$125/day in the holdout |
| 7 | Sell Over 2.5 / BTTS-Yes to pregame retail | DEAD | -2.3% [-9.7, +5.0] over 991 markets; -4.5% at +1c | +3.7% [-3.7, +11.0] | $43-57k notional/day at the front of the queue; losing |
| 8 | Esports -1.5 handicap vs game-2 "identity" arbitrage | DEAD | Literal +8.4% [-6.6, +23.5] is luck on unhedged legs. Unwinding failed hedges the way a trader would gives -3.5% [-5.8, -1.3]. The riskless part is +1.4%, and -2.6% at +1c | -6.3% [-19.8, +9.1] | About $4/day on the riskless part |
| 9 | MLB run line: home -1.5 structurally rich | DEAD | -8.8% [-35.3, +17.8] on 52 bets. The naive structural bet is -2.5% on 525 bets | -1.7% [-22.8, +20.5] | Moot; the market already prices the effect |
| 10 | Tennis "match completed?" certainty premium | DEAD | As registered -44.5% on capital (mostly fat-finger fills). With a realistic resting ask at 97c: -25.6% on capital, -0.8c/share. Price-floored version -9.5% to -11.5% | -6.3c/share [-11.6, -0.8] | About $36/day of capital in the 97c+ zone |
| 11 | Soccer O/U 2.5 lags the moneyline pregame | DEAD | -19.4% [-44.7, +7.0] on 67 bets; -21.3% at +1c | Out-of-fold -8.8% | About $1k/day; losing |
| 12 | In-play certainty premium (sell the new 90-97c "lock") | DEAD | -20.2% [-30.5, -9.9]. The whole interval is below 0. -28.6% at +1c | -14.7% [-20.8, -8.2] | -$119/day at the cap |

---

## The 12 hypotheses in detail

### 1. Esports BO3 between-map break overreaction: PROMISING (mostly noise)

- **Mechanism.** After map 1 of a best-of-3 there is a 2-5 minute break in which nothing about the game changes.
  Retail piles onto the map-1 winner, even though map-pick and draft rules tend to favour the loser. Whoever takes
  the trailing team during the break is paid for providing that liquidity.
- **Rule.**
  - *Markets.* Esports BO3 series moneylines with at least $25k traded before the start.
  - *Map-1 end (t_end).* The first game-1 child-market print at 0.99 or above. That side is the leader L.
  - *Model.* From L's pregame price P0, solve P0 = p²(3-2p). The fair price assuming independent maps is
    1-(1-p)². A frozen DEV logistic calibration with title dummies then gives P_cal (coefficients in Section 1).
  - *Break price.* P_break is the median L-price over [t_end+30 s, t_end+150 s], with at least 3 fills.
  - *Trade.* If P_break - P_cal ≥ 0.04, buy the trailer at the next print on that side in [t_end+153 s, t_end+600 s].
    If none prints, use the implied ask. Hold to resolution; one bet per series.
  - *Mirror variant.* If P_break - P_cal ≤ -0.04, buy the leader.

| | Bets | ROI per $1 [95% CI] | +1c |
|---|---|---|---|
| DEV, in-sample calibration | 89 | +65.2% [+10.5, +128.4] | +55.5% |
| DEV, cross-fitted (honest) | 103 | +5.8% [-38.7, +58.0] | -1.0% |
| **HOLDOUT** (refit calibration) | **53** | **+29.3% [-27.0, +92.2]** | **+22.8%** |
| HOLDOUT, originally frozen calibration | 60 | +3.5% [-44.6, +61.3] | -1.5% |
| Mirror (buy leader), HOLDOUT | 230 | +6.4% [-3.8, +16.5] | +4.7% |
| Mirror, cross-fitted DEV + HOLDOUT pooled | 609 | +4.8% [-1.4, +11.0] | +3.1% |

- **Capacity.** The primary rule fires about 0.66 times a day, and the median first print is about $8. The execution
  reviewer walked the actual book and puts realistic deployable size at $100-350/day. The mirror fires about 2.9 times
  a day, roughly $320/day at first prints.
- **What the reviewers found.**
  - Round 1 said DEAD. The look-ahead reviewer showed that the DEAD result came from one dropped signal: nobody else
    printed on our side within 600 s, and that bet would have won. With it restored, the rule reads PROMISING.
  - Round 2 did not refute PROMISING, with these caveats:
    - The +29.3% headline is a second look at the holdout. The honest confirmatory number is +3.5%.
    - A threshold knife edge moves 9 marginal Dota 2 bets in or out of the sample.
    - Dropping the 3 best holdout series gives -5.8%.
  - Execution is solid. Spreads are about 1c, and the rule breaks even only at about 5.5c of slippage. Conservative
    asks give +27%.
- **Verdict: PROMISING, noise-dominated.** The only pattern that shows up in both periods is **Dota 2**: map-1 winners
  are about 4-5c too expensive at the break, on 206 series. The pre-registered momentum story is *backwards* in CS2,
  where the map-1 winner is 4-6c cheap. Buying every CS2 leader at the break returns +7.2% [+0.2, +14.1] in the holdout,
  but that was found by looking at both periods, so it is a lead, not a result.

### 2. Esports game-4 "unplayed map pays 50-50" clause: DISPUTED

- **Mechanism.** A BO5 "who wins map 4" market pays 50-50 if map 4 is never played (a 3-0 sweep); 30-37% of these
  markets end that way. A trader who prices the favourite as if map 4 will surely happen overpays by
  (1-P_played)·(p-0.5), which the hypothesis put at 5-8c.
- **Rule.**
  - *Markets.* Every esports "-game4" market starting in 2026, with no volume filter. T is the scheduled series start.
  - *Signal.* The first fill in [T-24h, T+60 min) where the favourite trades at 0.58 or above.
  - *Trade.* Buy the underdog at the next underdog print at least 3 s later and before T+60 min. Hold to resolution:
    payoff 1, 0, or 0.5 if map 4 is unplayed.
  - *Scoring.* After review, the "capacity-valid" primary counts a bet only if $1 could actually be filled at the
    entry print.

| | Bets | ROI per $1 [95% CI] | +1c | Equal-share ROI |
|---|---|---|---|---|
| Literal pre-registered, DEV / HOLDOUT | 109 / 96 | +43.5% / +59.8% | +19.8% / +39.5% | -4.0% / +4.6% |
| **Capacity-valid ($1 fill-or-kill), DEV** | 104 | +5.0% [-12.0, +23.7] | +2.2% | -3.3% |
| **Capacity-valid, HOLDOUT** | 94 | **+4.0% [-12.3, +20.5]** | +1.4% | +1.1% (-1.2% at +1c) |
| Reviewer's post-hoc fix: limit price 0.50, DEV | 94 | +10.3% [-8.9, +29.3] | +7.3% | |
| Reviewer's post-hoc fix: limit price 0.50, HOLDOUT | 84 | +10.2% [-8.0, +29.2] | +7.4% | +8.6% |

- **Capacity.** Tiny. The median entry print is about $7. Total stake is roughly $500-1,000 a month, which is tens of
  dollars of profit even at +10%.
- **What the reviewers found.**
  - The literal +43% / +60% comes from two sub-$1 fills at 1-2c. Those supplied 95% and 81% of the summed ROI, and
    nobody could have bought $1 there. Every reviewer agreed that result is void.
  - The author then applied a strict gate: the result had to stay positive at +1c on the equal-share and drop-top-1%
    estimators. It failed (-1.2%, -0.6%), so the author called it DEAD.
  - **The execution reviewer refuted DEAD.** The rule had no price limit, so it "bought the underdog" at 0.60-0.94 on
    junk prints in brand-new books. With a 0.50 limit it is about +10% in both periods and passes every gate check. That
    fix was made after seeing the holdout, though, it is not significant (t ≈ 1.3), it is driven by League of Legends,
    and it was -4.9% in September.
  - The look-ahead reviewer added a warning. Bets only exist where some *other* trader later bought the underdog, and
    those markets favoured the underdog: payoff about 0.42 against 0.12-0.21 where nobody did. That flatters every
    version. Buying at the ask at signal time (decision-time execution) looked much worse in DEV.
  - The underlying mispricing is about 3c, not 5-8c. That is roughly the spread plus the fee.
- **Verdict: disputed, at most PROMISING, post-hoc.** Worth a zero-cost paper log only (Section 1, Test B).

### 3. MLB half-inning break market making: DISPUTED

- **Mechanism.** Between half-innings, nothing about the game can change for about 2 minutes, yet retail keeps crossing
  the spread (about $94M of taker flow inside breaks). A maker quoting only in that window should collect the
  half-spread plus the rebate without being picked off.
- **Rule.**
  - *Markets.* MLB moneylines with at least $25k pregame volume.
  - *Window.* From 30 s to 100 s after the third out (b0 = end of the last plate appearance).
  - *Quote.* Rest an ask on each outcome token at the last taker price on that token from at least 3 s earlier (none if
    older than 120 s). Fill up to 50 shares per taker order, capped at 1,000 per side per break.
  - *Scoring.* Markout against the two-sided mid at b0+105 s, plus a 15% rebate of the taker fee.
  - *Gate.* A back-of-queue stress result must be ≥ 0 for PROFITABLE.

| Model | DEV (c/share) | HOLDOUT (c/share) |
|---|---|---|
| Front of queue (idealized, as pre-registered) | +0.334 | +0.408 [+0.388, +0.427] |
| Author's verdict model (FIFO queue from live books, proportional cancels) | -0.086 [-0.143, -0.036] | +0.099 [+0.057, +0.137] |
| Execution reviewer's fix: never rest an ask at or through the other side's implied bid | +0.127 [+0.064, +0.173] | **+0.246 [+0.206, +0.283]** (+0.50% on capital) |
| Back-of-queue gate (b), pre-registered | -0.495 | -0.444 (fails) |
| Hold to resolution, corrected model | | -1.62 [-3.68, +0.55], -$5,384 |

- **Capacity.** $172k of notional over 77 holdout days, about $8.5/day of markout P&L. Only 16-18% of shares pair off
  inside a break; the rest is directional inventory. The book queue is a median of 20,726 shares.
- **What the reviewers found.**
  - Round 1 killed the front-of-queue model: a newcomer never gets to the front, and the author agreed. The author then
    built a queue model from live order books and got DEAD, because DEV is significantly negative.
  - **The round-2 execution reviewer refuted DEAD.** The simulator was crediting fills to asks that could never have
    rested: they sat at or below the other side's implied bid and would have crossed. Those fills are the entire DEV
    loss. Fixing this gives a positive markout in both periods, which reads PROMISING (gate (b) still fails).
  - The look-ahead reviewer added two points. The DEAD-vs-PROMISING label also depends on how deep 2025 queues were,
    and the queue calibration comes from one September 2026 evening. Neither changes the economics: a few dollars a day,
    negative when held.
- **Verdict: disputed, at most PROMISING on the markout metric.** It is never PROFITABLE, because realized
  hold-to-resolution P&L is negative and the gate fails. Not worth engineering for profit (Section 1, Test C).

### 4. Soccer halftime market making: DEAD

- **Mechanism.** The same blackout idea as MLB, over a longer window. Halftime is about 15 minutes with no game
  information.
- **Rule.**
  - *Markets.* Soccer moneyline legs with at least $25k pregame volume.
  - *Window.* Quote as in #3 from kickoff+52 to kickoff+60 min.
  - *Skip.* Skip the event if any leg moved more than 3c in [KO+49, KO+52), as a proxy for a late goal or late kickoff.
  - *Scoring.* Markout at KO+61.
- **Results (ROI on capital).**
  - Front of queue: DEV +0.64%, HOLDOUT +0.17% [-0.26, +0.49]; -0.04% without the rebate.
  - Back-of-queue gate: DEV -1.01% [-1.61, -0.50], HOLDOUT -1.95% [-3.47, -0.88].
  - Realistic queue, using halftime depth measured live (median 6,866 shares ahead), after the reviewers' reprice-bug
    fix: DEV +0.08% [-0.08, +0.22], HOLDOUT -0.28% [-0.82, +0.12], and -0.46% without the rebate.
- **Capacity.** About $576/day of capital turnover, for about -$40/month.
- **What the reviewers found.** Round 1 refuted the original PROMISING because it was a front-of-queue fantasy. Round 2
  found one more bug, float noise treated as a reprice, which made the realistic model look *better* than it is. With
  it fixed, every queue calibration is ≤ 0.
- **Verdict: DEAD, and it survived review.** The blackout effect is real (halftime beats live play by 0.7-1.9c), but
  makers pile into the book at halftime and the spread goes to incumbents.
- **Post-hoc lead.** Excluding US/Mexico leagues, whose listed start is about 10 minutes early, gives +0.22%
  [-0.04, +0.42]. That needs fresh data.

### 5. Late-match draw in tied soccer matches: DEAD

- **Mechanism.** Late in a tied match, fans hold team-Yes, hope buyers chase a late winner, and knockout fans misread
  "win" as "advance". All three should leave the draw too cheap.
- **Rule.** Take the first draw-market print at or after KO+95 min. If it is priced 0.40-0.90, buy draw-Yes at the
  next Yes print within 5 minutes and hold. One bet per match. Every draw leg was fetched, so volume cannot bias the
  sample.
- **Results.**
  - DEV: -3.6% [-12.4, +5.2] on 415 bets.
  - HOLDOUT: -2.0% [-15.6, +12.9] on 158 bets; -3.7% at +1c; +0.1% before fees.
  - The draw came in 55.1% of the time at an average price of 55.0c, so the market is calibrated.
  - A maker version is worse (-11%), because resting draw bids fill mostly when a goal goes in.
- **Capacity.** A few hundred dollars per game.
- **What the reviewers found.** None refuted it. The earlier +2.4c "peek" came from a sample biased toward high-volume
  games. The statistics reviewer noted the test can only detect edges of about 13-21 points, so read it as "no
  detectable edge". A +2c edge is unlikely but not ruled out.
- **Verdict: DEAD.**

### 6. "Prop tax" market making in MLB NRFI and total-8.5 markets: DEAD

- **Mechanism.** Thin side markets draw recreational takers and have few makers, while the liquid moneyline anchors
  fair value. A maker should collect a wide spread and pull quotes when the moneyline moves.
- **Rule.**
  - *Markets.* NRFI and fixed-line total-8.5 markets for MLB games with a ≥ $25k moneyline; 2,000 markets fetched.
  - *Quoting.* From T-12h to T-15m, rest asks on both tokens at the last taker price (no older than 6 h), taking
    100 shares per fill. Cap net inventory at 500 shares.
  - *Pull.* Pull all quotes for 60 s after a moneyline move of 2c or more.
  - *Exit.* Hold to resolution.
- **Results.**
  - DEV: +0.41c/share, +0.84% [-1.25, +3.10], +$2,935.
  - HOLDOUT: -1.04c/share, -2.12% [-4.72, +0.36], -$7,407; -4.07% at +1c.
  - Back-of-queue stress: -3.6c (holdout) and -5.4c (DEV).
  - Markout to the pregame close: +0.15c/share in both periods. That is significant but far below the cost of exiting.
- **Capacity.** $5-6k notional/day, even with front-of-queue fills.
- **What the reviewers found.** None refuted it. The premise is false: totals books have a 1c spread, the same as
  moneylines, and totals *takers* made +6.5% to +9.1% gross, so they are not a recreational tax. The holdout loss is
  mostly outcome drift after the close; the true maker edge is about +0.15c/share and cannot be captured.
- **Verdict: DEAD.**

### 7. Sell Over 2.5 / BTTS-Yes to pregame retail: DEAD

- **Mechanism.** Retail defaults to "Yes" and "Over". Unlike the moneyline, O/U and BTTS have no conversion arbitrage,
  so a premium could survive there.
- **Rule.**
  - *Markets.* 16 leagues, sampled by schedule only; 2,000 markets.
  - *Trade.* Act as the maker on every pregame taker ticket buying Over or BTTS-Yes, up to 100 shares at the taker's
    price, with a 15% rebate. Hold to resolution.
  - *Scoring.* Equal weight per market, clustered by match.
- **Results.**
  - DEV: +3.74% [-3.69, +11.01] on 949 markets.
  - HOLDOUT: -2.32% [-9.65, +4.99] on 991 markets; -4.47% at +1c.
  - The flow bias is real: 61% of takers bought Over/Yes in DEV and 73% in the holdout. Prices matched outcomes within
    about 1c anyway.
  - The mirror control (sell Under/No) did *better* in the holdout, +4.35%.
- **Capacity.** $43-57k notional a day at the front of the queue. At the back of the queue, only 4-8% of that fills,
  and those fills lose.
- **What the reviewers found.** None refuted it. The side convention was verified with a bid-ask bounce test, and
  payouts were checked for impossible combinations. The holdout sign is driven by September (-14.5%); July-August was
  +2.4%. The test rules out a premium of 3c or more, not a 1c one.
- **Verdict: DEAD.**

### 8. Esports -1.5 handicap vs game-2 "identity" arbitrage: DEAD

- **Mechanism.** After X wins map 1 of a BO3, "X -1.5 maps" pays exactly when X wins map 2. The thin handicap book
  should lag the liquid game-2 market, so buying both sides for less than $1 locks in a profit.
- **Rule.**
  - *Signal.* After map 1, trigger when the last handicap price plus the last game-2 price for not-X (both within 20 s)
    is 0.98 or less.
  - *Trade.* Buy both legs at the next prints in [t+3 s, t+60 s], up to 100 shares. If only one leg prints, hold it
    alone to resolution.
- **Results.**
  - DEV: -6.3% [-19.8, +9.1] on 350 attempts.
  - HOLDOUT, clean: +8.4% [-6.6, +23.5] on 552 attempts; +5.9% at +1c.
  - 94% of holdout P&L came from naked game-2 underdog legs. They won 44% of the time at 38c in the holdout, against
    23% at 29c in DEV.
  - Completed pairs: +3.3% (DEV) and +1.4% (holdout); -2.6% at +1c after dropping 2 series.
- **Capacity.** About $155/day deployed on the riskless part, for about $4/day. Handicap prints average 17-21 shares
  per pair.
- **What the reviewers found.**
  - The round-1 statistics reviewer refuted PROMISING: the result rested on 3-5 series and naked-leg luck.
  - In round 2 the look-ahead reviewer marked the naked legs to later market prices and got about 0 (-1.0% to +0.8%).
  - The execution reviewer unwound failed hedges at the next bid and got -3.5% [-5.8, -1.3]. A competing bot takes the
    first handicap print 34-38% of the time.
- **Verdict: DEAD.** The contract identity is real, and stale prints sit about 4c below parity. After the next print,
  the 5% fee and competition from bots, nothing is left.

### 9. MLB run line: home -1.5 structurally rich: DEAD

- **Mechanism.** A home team that is leading never bats in the bottom of the 9th, and a walk-off ends the game on the
  winning run. Both cap home margins. A maker who prices the run line symmetrically would overprice home -1.5.
- **Rule.**
  - *Model.* A frozen logistic model of P(cover -1.5) from the moneyline price and a home flag.
  - *Trade.* At 30 minutes before first pitch, trade the run line if its price differs from fair value by 2c plus the
    fee. Buy at the next print and hold. One bet per game.
- **Results.**
  - DEV: -1.7% [-22.8, +20.5] on 76 bets.
  - HOLDOUT: -8.8% [-35.3, +17.8] on 52 bets; -10.6% at +1c.
  - The naive structural bet (NO on home -1.5, YES on away -1.5) was -2.1% (DEV) and -2.5% (holdout) on about 525 bets
    each, which is the cost of trading.
  - The effect itself is real: at moneyline 0.60-0.65, home covers 40.8% against away 53.4%. But the market's home
    coefficient is -0.22 against a true -0.20, so it is fully priced.
- **What the reviewers found.** None refuted it. The primary sample is too small to detect a 5% edge (it would need
  about 39% ROI). The verdict rests on the pricing diagnostic, which covers 1,281 markets. A residual mispricing of
  about 3c either way cannot be excluded.
- **Verdict: DEAD.** The in-play walk-off variant made only 6 bets and was inconclusive.

### 10. Tennis "match completed?" certainty premium: DEAD

- **Mechanism.** "Yes, completed" looks like a free 97-99c coupon, yet 5-6% of matches are not completed
  (retirements, walkovers). A new, thinly made market should overprice Yes.
- **Rule.**
  - *Markets.* ATP and WTA.
  - *Trade.* Act as the maker on every taker buying "Yes" in the 6 hours before start, up to 200 shares at the
    taker's price. Hold to resolution.
  - *Scoring.* Equal weight per market, clustered by date.
- **Results.**
  - DEV: -6.26c/share [-11.55, -0.82] on 129 markets.
  - HOLDOUT: -4.45c/share [-7.70, -1.70] on 480 markets. ROI on capital -44.5%; -51.3% at +1c.
- **What the reviewers found.**
  - None refuted DEAD. All three said the *size* of the loss is overstated: 88% of the holdout dollar loss comes from
    44 fat-finger or mis-sided fills at 1-5c that no Yes seller at 97c would ever have been on.
  - Corrected results: with a price floor of q ≥ 0.10 it is -1.31c [-3.03, +0.54] (-9.5% on capital). A realistic
    resting ask at 0.97 loses -0.77c/share (-25.6% on capital). The 97c+ zone is +0.18c/share and deploys about $36/day.
- **Verdict: DEAD.** The book is fair around 97-98.5c.
- **Side lead.** Takers who lifted mis-sided No bids made about $4.4k in the holdout sample. That is a latency race,
  not a research edge.

### 11. Soccer O/U 2.5 lags the moneyline pregame: DEAD

- **Mechanism.** The deep moneyline absorbs lineup news within minutes, while the separate O/U market goes stale. So
  trade the O/U toward the goal total the moneyline implies.
- **Rule.**
  - *Model.* At T-10 min, fit Poisson goal rates from the home and away win prices, giving P(3+ goals). Calibrate it
    with an isotonic curve fitted on DEV.
  - *Trade.* If the calibrated probability minus the O/U median price is 4c or more, buy Over; if -4c or less, buy
    Under. Next print, hold, one bet per match.
- **Results.**
  - DEV: in-sample +10.8%, but out-of-fold -8.8% (5-fold) and -3.5% (time-forward).
  - HOLDOUT: -19.4% [-44.7, +7.0] on 67 bets; -21.3% at +1c.
  - The O/U traded a median of 30-39 s before the decision and did not drift toward the moneyline afterward (0 ± 0.2c).
- **What the reviewers found.** None refuted it. Variant (b), +21.7% in the holdout, actually *fades* the moneyline. It
  does not survive a multiple-testing correction (adjusted p = 0.17) and is about 0 out-of-fold.
- **Verdict: DEAD.** The O/U market is not stale.

### 12. In-play certainty premium (sell the new 90-97c "lock"): DEAD

- **Mechanism.** When a side first becomes a 90%+ favourite in play, yield buyers pile in and makers avoid the tail. So
  holding the underdog tail should pay.
- **Rule.**
  - *Signal.* The first in-play fill, per game, where a side that was below 0.80 pregame trades at 0.90-0.97. All
    sports, pre_usd ≥ $20k.
  - *Trade.* Rest an ask on that side at that price from 3 s to 300 s later, up to 200 shares, with a queue model.
    Hold to resolution.
- **Results.**
  - DEV: -14.7% [-20.8, -8.2].
  - HOLDOUT: -20.2% [-30.5, -9.9]; -28.6% at +1c.
  - Front-of-queue, last-in-queue and taker versions are all negative. The most generous upper bound (filled at the
    price each taker actually paid) is still -2.2%.
- **What the reviewers found.** None refuted it. The loss is adverse selection. The maker gets filled when the
  favourite keeps rising, and in the comeback games that carry the premium nobody ever trades back to the maker's
  price.
- **Verdict: DEAD.** This is the most conclusive result of the 12.

---

## 1. What is worth a live paper test, and exactly how to run it

**Nothing here justifies real money today.** The tests below are paper tests: log what you *would* have done, with real
timestamps and real asks, and do not trade. Pre-register each one by saving these parameters in a dated file before
the first bet, and use only markets that start after 2026-09-19. Every holdout number above has been looked at at
least once, so new data is the only clean test.

General rules for all three:
- Log the best ask and its size at the moment you would send the order. Also log the fill the backtest convention
  would have given you (the next print on your side). The difference is your real slippage.
- Use actual fees: taker fee = shares × fee_rate × p × (1-p), with fee_rate currently 0.05.
- Do not change a parameter mid-test. If you want to try a change, start a new test.

### Test A: Esports BO3 between-map break (the one PROMISING result)

Run two rules side by side, plus one log-only line:
- **A1, the primary:** buy the trailer. It trades almost only Dota 2 and Valorant.
- **A2, the mirror:** buy the leader. It has more bets and is the steadier of the two.
- **A3, log only:** buy every CS2 leader at the break (a post-hoc lead).

**Markets.** Polymarket esports series moneylines (CS2, LoL, Dota 2, Valorant) that are BO3. The event must list
`-game1`, `-game2` and `-total-games-2pt5` markets, and none of `-game4`, `-game5`, `-total-games-3pt5` or
`-total-games-4pt5`. At least $25k must trade in the 24 hours before the scheduled start.

**Steps.**
1. **Before the match.** For each team, record P0 = the median price takers paid for that team in the last 10 minutes
   before the scheduled start.
2. **Detect the map-1 end.** t_end = the first trade in the `-game1` market at 0.99 or higher; that team is the
   leader L. Skip the series if t_end is less than 10 minutes after the scheduled start.
3. **Fair price** (frozen; do not refit).
   - Solve P0_L = p²(3 - 2p) for p, then fair = 1 - (1 - p)².
   - P_cal = 1 / (1 + exp(-z)), where
     z = 0.295 + 0.861·ln(fair / (1 - fair)) + 0.055·[LoL] - 0.414·[Dota 2] - 0.225·[Valorant]. CS2 is the baseline.
4. **Break price.** P_break = the median of L's traded price (converted from both sides) over
   [t_end+30 s, t_end+150 s]. Skip if fewer than 3 trades.
5. **Decide** at t_end+150 s, where gap = P_break - P_cal:
   - A1: if gap ≥ +0.04, buy the trailer.
   - A2: if gap ≤ -0.04, buy the leader.
6. **Order.** From t_end+153 s, send a **fill-and-kill (FAK) marketable limit buy** for **$25**. Price limits:
   - A1: the trailer price must be at most (1 - P_cal) - 0.02.
   - A2: the leader price must be at most P_cal - 0.02.

   If nothing fills, retry on each new ask until t_end+600 s, then give up. The 2c guard is **new**; the backtest had
   none. Log the unguarded outcome too.
7. **Hold** to resolution. One bet per series.

**Kill and graduation criteria** (fixed in advance):
- **A2 mirror** (about 3 bets/day):
  - **Kill** at 150 bets if ROI after fees at your actual fills is below -6%.
  - **Kill** at any time if your average fill is more than 2c worse than the backtest convention.
  - **Graduate** to real money at $25/bet only if ROI is at least +4% after 300 bets (about 100 days).
- **A1 primary** (about 0.7 bets/day, ~20c longshots; very noisy):
  - **Kill** at 60 bets if ROI is below -30%, or at 120 bets if below 0.
  - **Graduate** only if ROI is at least +20% at 120 bets, and even then at $10-25/bet.
  - Confirming the Dota 2 effect to ±20 points would take about 450-500 bets, over two years at the current rate.
- **Sizing if a test passes.** At most a quarter of the Kelly stake computed from the *lower end* of the pooled
  confidence interval (backtest plus paper). Today that stake is zero.

### Test B: Esports game-4 clause with a 0.50 limit (disputed lead; paper log only)

**Markets.** Every esports `-game4` market (a BO5 series) whose scheduled start is after 2026-09-19. T is the
scheduled series start.

**Steps.**
1. **Signal.** The first trade in [T-24h, T+60 min) where either side trades at 0.58 or higher. That side is the
   favourite; the other side is the underdog.
2. **Order.** From the signal +3 s, send a **fill-or-kill buy of the underdog for $5**, limit price **0.50**, at the
   current best ask. If the ask is above 0.50, re-check on each new trade until T+60 min. One bet per series.
3. **Hold** to resolution. The payout is 1, 0, or 0.5 if map 4 is never played.
4. **Log** both your decision-time fill and the backtest-style fill (the next underdog print at 0.50 or below).

**Why "log only".** The look-ahead reviewer showed the backtest only traded where someone else later bought the
underdog, which flatters the result. Buying at the ask at signal time looked much worse in DEV. The upside is capped at
tens of dollars a month even if the edge is real.

**Kill** if ROI is below -20% at 50 bets, or below 0 at 100 bets (about 3 months). Do not scale beyond $25/bet under
any result; the books cannot take it.

### Test C: MLB half-inning break maker (disputed; not recommended for profit)

A paper test cannot tell you your queue position, and queue position is the whole question here. The expected upside
is also tiny: about $8/day of markout, and negative so far if the inventory is held. Only run this as a *measurement*
if you want to settle the dispute. Three things stand in the way:
- **Timing.** The MLB regular season ends in late September, so run it in the October postseason or wait for spring
  2027.
- **Speed.** You need to know the third out within about 30 s. Public MLB feeds lag about 27 s, so detect the break
  from the book repricing instead.
- **Order type.** Use real **post-only resting asks** of 5-10 shares (the exchange minimum for a resting order is 5 shares).
  - *Price.* The last taker price on that token from at least 3 s earlier.
  - *Never cross.* Never quote at or below the implied bid from the other token's more recent print.
  - *Window.* Quote from third out +30 s to +100 s, then cancel everything.

**Measure** over about 300 breaks: fill rate, the 60-second markout excluding the rebate, and realized P&L held to
resolution. **Kill** if markout excluding the rebate is at or below 0, or held P&L is negative.

### Other leads worth logging for free (all found after seeing the holdout; not results)

- **Soccer halftime maker**, excluding US and Mexico leagues: +0.22% [-0.04, +0.42] under the realistic queue.
- **Esports: every CS2 map-1 winner at the break:** +7.2% [+0.2, +14.1] in the holdout, +4.8% in DEV.
- **Soccer O/U variant (b), fade the moneyline:** +21.7% on 38 holdout bets, but about 0 out-of-fold. Probably noise.

---

## 2. What is dead and why

| Why it died | Hypotheses | The evidence in one line |
|---|---|---|
| **The market already prices it** | Late draw (#5), Over/BTTS (#7), run line (#9), tennis completion (#10), O/U vs moneyline (#11), prop tax (#6) | Outcomes match prices within about 1-3c. Run-line home asymmetry is priced at -0.22 against a true -0.20. The O/U is not stale. Totals takers win rather than pay a tax |
| **The maker gets picked off, not paid** | In-play certainty premium (#12) | Fills happen when the favourite keeps rising. The comebacks that carry the premium never trade back to your price |
| **The spread belongs to the front of the queue** | Halftime MM (#4), MLB break MM (#3, disputed), prop tax (#6) | Queues run 7k-21k shares. At the back you mostly get the fills where your stale quote is swept |
| **The arbitrage is real but too small and too contested** | Identity pick-off (#8) | Stale prints are about 4c off parity. The next-print fill, the 5% fee and bots taking 34-38% of first prints leave about 0 |
| **The "profit" was fills nobody could get, or luck** | Game-4 clause (#2, literal), tennis (#10 loss size), identity pick-off (#8 literal) | Sub-$1 prints at 1-2c, fat-finger fills at 1-5c, and naked legs that happened to win |
| **Fees** | Most of the above | The 5% taker fee costs about 1.25c/share at 50c, which is larger than most of the mispricings we measured |

---

## 3. Honest caveats

- **Multiple testing.** We ran 12 hypotheses with 5-15 variants and splits each. That is well over 100 holdout
  comparisons, and at the usual 5% threshold about 5-10 of them should look "significant" by pure chance. The one
  PROMISING result has a confidence interval about ±60 points wide, so it is fully consistent with chance. Every
  eye-catching cell found by looking at both periods (CS2 leaders, halftime without US/MX, soccer O/U variant b, the
  game-4 limit fix) is a lead for new data, not a result.
- **The holdout is short and a single regime.** It is about 77-80 days of summer 2026: MLB, soccer (World Cup plus the
  August-September leagues), esports and tennis, with no NBA or NHL. The holdout samples run from 52 bets (run line) to
  about 1,000 markets. The whole holdout sits under the 5% fee, while DEV mixes 0% (2025) and 3% (Mar-Jun 2026). Edges
  that existed in fee-free 2025 (prop tax +5.3c/share; in-play premium +2.2c/share at the signal print) did not
  carry over.
- **The holdout was not always untouched.** Feasibility scripts for four hypotheses (MLB break, halftime, identity
  pick-off, in-play premium) had looked at July-September data before the rules were frozen. Five repaired hypotheses
  had their holdout re-run after review, and the break-overreaction holdout has been seen three times. Contamination
  like this biases results toward false positives. So the DEAD verdicts are safe, and the PROMISING one deserves extra
  suspicion.
- **Fill assumptions.**
  - *No historical order books.* Taker fills are the next print by *another* taker on our side, not a quoted ask. The
    Data API reports sweeps at their average price. +1c is a gentle stress: game-4 and run-line books often run 2-6c
    wide.
  - *Maker queue models* are calibrated on one evening of live books (2026-09-18/19: 15 MLB games and 3 soccer games).
  - *The 15% maker rebate* is an approximation of a pool. It was 25% in the 3%-fee period.
  - *Minimum sizes* are $1 for marketable orders and 5 shares for resting orders. On-chain timestamps lag about 2.6 s;
    public sports feeds lag about 27 s.
- **Small samples.** Several primary tests could only detect very large edges (13-39 points of ROI). "DEAD" there means
  "no detectable edge and the mechanism check fails", not "proven zero".
- **Data problems found along the way; fix these regardless:**
  - *outcomeIndex bug.* The Data API `outcomeIndex` is wrong for many fills on 2026-05-13/14: 57-96% of fills in 13
    run-line markets, 0.8% of side-market fills, and 0.2-4.8% in 5 MLB moneylines. The shared `C.fills()` dataset and
    `data/mlb/trades` orient by `outcomeIndex`. They should orient by token id.
  - *NRFI labels.* "Yes" meant *no run* before April 2026 and *a run* after.
  - *Start times.* September-2025 soccer O/U markets carry a start time 4 hours late. US/MX soccer lists starts about
    10 minutes early.
  - *Bad prints.* Tennis and esports books contain absurd 1-5c prints that can dominate any per-$1 average.

---

## Hypotheses proposed but not tested

Three proposals were **merged** into tested hypotheses and ran as variants:

| Proposal | Where it went |
|---|---|
| In-play longshot maker | Became the queue model and variant (e) of the in-play certainty premium (#12). DEAD |
| MLB walk-off run line | Became variant (d) of the MLB run line (#9). Only 6 bets; the market is only about 1c rich in those states |
| Esports series/map synthetic | Became DEV-only variant (e) of the break overreaction (#1): +3.7% [-6.9, +15.1] on 42 bets. Never run on the holdout |

Seven were **rejected before testing**:

| Proposal | Idea | Why it was not tested |
|---|---|---|
| Pregame quiet-window market making | Quote pregame when no news arrives | Depends on front-of-queue fills. At 1c ticks and deep pregame queues, the proposer's own estimate was about 0 or negative. Top pregame maker wallets earn only about 0.04% of volume |
| Margin/fandom spreads | Fans overpay for their team's -1.5 | Soccer -1.5 books are about $200-300 deep. The "popularity" signal flagged lottery buying of bad teams, not fandom |
| Stale derivative sweep | Pick off stale orders in decided side markets | Adjacent checks were already dead. MLB winners hit 0.999 within 60 s; decided esports handicaps had under $100 below 0.98; soccer O/U repriced within 3 s in 54 of 58 goals |
| Soccer O/U goal lag | Trade O/U right after a goal before it reprices | Already falsified: the first O/U print 3 s or more after a goal was at fair price in 54 of 58 cases |
| NBA spread lag on injury news | Thin spread lags the moneyline | The holdout has no NBA games. The same mechanism was tested in soccer (#11) and failed |
| Exact-score bundle arbitrage | Sum of exact-score bids above $1 | Only 26 World Cup holdout events and about 117 events per 2,000-market fetch. Lottery variance, and the probe showed no riskless bound after about 4c of fees. It could be revisited with a bigger fetch budget |
| Soccer draw orphan | Synthetic draw from team legs | Pregame draws are calibrated (23.3% implied against 23.1% realized). The synthetic trade pays two taker fees |

---

## Files

| What | Where |
|---|---|
| Per-hypothesis reports, with every variant and review fix | `reports/research/<slug>.md` |
| Code (one script per hypothesis) | `pmsports/research/h_<slug>.py` |
| Cached data, results JSON and bet tables | `data/research/h_<slug>/` |
| Reviewer verification scripts | `data/research/verify_<slug>_<lens>.py` |
| Frozen break-overreaction calibration for Test A | `data/research/h_esports_break_overreaction/calib.json` |
