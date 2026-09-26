# MLB inning-discount rule: $100K account performance

_Generated 2026-09-26T04:28:23Z by `pmsports/analysis/performance.py`. Data files are in `data/research/performance/` (git-ignored)._

> **This whole page is a historical backtest.** It uses 2025–26 MLB data we had already studied. The 10c cutoff was chosen as the best of 12 fixed rules tested on that same data. Execution uses trade prints as a stand-in for the order book. **Nothing here is forward evidence.** The forward paper test (`reports/PAPER_TEST.md`) is the only unbiased check.

## Bottom line

- **$100 per game, 0.05 fee (today's international rate):** +$2,440 on the $100k over 535 days. That is about +1.34% per season, with Sharpe 1.55, a worst drawdown of -0.42% and at most $499 in play at once. At the US fee: +$2,164 (+1.19% per season).
- **Most profitable feasible budget, $5,000 per game, 0.05 fee:** +$21,637, about +11.40% per season, with Sharpe 1.19 and a worst drawdown of -$8,058 (-6.30%). But 20 trades supply +$16,301 of that profit. The 2026 season made +5.33% on the $100k against +16.31% in 2025, and the account is still in its worst drawdown at the end of the data.
- **Chance a season loses money** (resampling observed games): 8% at $100/0.05, 10% at $100/US fee, 14% at $5,000/0.05. The 3c control rule loses in 58% of seasons.
- **Capital is not the constraint. Liquidity is.** Even the largest budget never had more than 10% of the $100k in play at once.
- **All of this is in-sample.** Read these figures as a ceiling, not a forecast.

## 1. The rule in one paragraph

At the start of each half-inning in an MLB game, look at the team that is **leading**. Our fair value is how often teams in that exact spot (inning, top/bottom, size of lead, home or away) won in earlier seasons. If the leader's Polymarket price is at least **10 cents** below that fair value, buy the leader and hold the shares until the game settles ($1 per share if they win, $0 if they lose). `10c_first` buys only on the first signal in a game. `10c_every` buys on every signal, with the game capped at one budget. `03c_first` uses a 3c cutoff and serves as the control.

**How a fill is simulated (ledger model).** We wait 5 seconds after the signal. We then take the first later trade on the same side, pay 1 cent more than it did, and buy at most as many shares as that trade printed, up to the budget with the fee included. If no trade prints before the next play, we get nothing; about 14% of signals end that way. Partial fills are kept. A larger budget therefore does **not** buy more than the market showed. It only stops cutting the fill at $100.

## 2. What the numbers mean

- **The old “+7.32%” is return on the money spent, over both seasons. It is not a yearly figure and it is not a return on an account.** In total, 1,229 trades cost $40,559 and made +$2,970. $2,970 ÷ $40,559 = +7.32%. That total took 535 calendar days (2025-04-02 to 2026-09-18), and no more than $497 was ever at risk at once.
- **Its “range +1.0% to +13.5%” is a 95% confidence interval.** We resample the games at random, thousands of times, and see how far the return moves. The 1,229 games we actually saw are one draw of luck. The interval shows how much a different draw of games could have changed the result. A range touching or crossing 0% means we cannot tell the edge apart from zero.
- **“Chosen after the fact” means we tried 12 cutoffs** (0, 2, 3, 5, 8 and 10c, each with first-signal-only and every-signal versions) on this same data and kept the one that looked best. When you pick the best of 12 on the same data, some of what made it look best is luck. That luck will not repeat. So expect future returns to be lower than the ones shown here. More games from the **past** cannot fix this. Only new games can.
- **Account view (this report).** The account starts with $100,000 on 2025-04-02. A trade's full cost, shares × price + fee, leaves cash when it fills and comes back as the payout when the game settles. The profit or loss counts on the day the game settles (UTC). **Daily return = that day's realized P&L ÷ equity at the start of the day.** Open positions are held at cost and never marked to market. Every calendar day is in the series, including the off-season, when the return is 0.
- **Annualization.** The main figures use **calendar days, periods = 365**, the standard QuantStats setting for a daily series with zero-return days. We also give a **season-days-only** version. It keeps only days inside an MLB season window (2025: 2025-04-02 to 2025-11-02; 2026: 2026-03-28 to 2026-09-18) and annualizes with 215 days, the length of the one complete season (2025). Because the off-season earns nothing, the season version is the better guide to what one steady full year would return. The calendar CAGR spans one off-season in 17.5 months, so it runs slightly higher than the season version.
- **Risk per game.** Each fill is a binary bet. The most it can lose is its full cost, so “capital risked on a game” in the tables means that game's cost.

## 3. Fees: what they are and how they are applied

Polymarket's taker fee is **fee = rate × shares × p × (1 − p)**, where p is the price paid. It follows a published formula and the rate is set per market, so it can be re-applied to past trades exactly. It does not change with the order book. It changes only when the venue changes its rate. Makers (resting limit orders) pay no fee. This model always takes liquidity, so it always pays the fee. The fee is counted **inside** the budget: shares = budget ÷ (price + fee per share).

| Fee regime | Rate | Fee per share at p = 0.65 | As % of price |
|---|---|---|---|
| as recorded, 2025 | 0.0000 | 0.00c | 0.00% |
| as recorded, Apr–Jun 2026 | 0.0300 | 0.68c | 1.05% |
| intl_0.05 (as recorded from Jul 2026) | 0.0500 | 1.14c | 1.75% |
| us_0.0695 (Polymarket US) | 0.0695 | 1.58c | 2.43% |

At the average entry price of 0.65, a 0.05 fee costs about 1.75% of the money spent, and the US rate about 2.43%. The scenarios `intl_0.05` and `us_0.0695` re-price **every** historical trade at that rate, 2025 included. That is the right basis for judging the rule today. `as_recorded` keeps the fee each trade actually paid: zero for all of 2025 and 0.03 then 0.05 in 2026. That is why its $184 in fees is so low.

## 4. Headline results on a $100,000 account

We name the **most profitable liquidity-feasible budget** for each fee regime. A budget is liquidity-feasible when (a) no fill is larger than a trade that actually printed, which is always true in the ledger model, and (b) the $100k account never runs short of cash. It is the most profitable when it has the highest total P&L among such budgets. The pick is **$5,000** for intl_0.05 and **$5,000** for us_0.0695. See section 7 for why those results rest on a few large fills. $5,000 is also the largest budget tested.

| Metric | 10c_first $100 as_recorded | 10c_first $100 intl_0.05 | 10c_first $100 us_0.0695 | 10c_first $5,000 intl_0.05 | 10c_first $5,000 us_0.0695 | 03c_first $100 intl_0.05 |
|---|---|---|---|---|---|---|
| Total P&L | +$2,970 | +$2,440 | +$2,164 | +$21,637 | +$20,325 | -$532 |
| Return on $100k (total) | +2.97% | +2.44% | +2.16% | +21.64% | +20.33% | -0.53% |
| CAGR (calendar, 365) | +2.02% | +1.66% | +1.47% | +14.30% | +13.45% | -0.36% |
| Per-season return (season days) | +1.63% | +1.34% | +1.19% | +11.40% | +10.74% | -0.29% |
| 2025 season on $100k | +1.92% | +1.45% | +1.27% | +16.31% | +15.48% | -0.34% |
| 2026 season on $100k (to Sep 18) | +1.05% | +0.99% | +0.89% | +5.33% | +4.84% | -0.19% |
| Annual volatility | 1.08% | 1.07% | 1.06% | 11.83% | 11.78% | 1.55% |
| Sharpe (365) | 1.85 | 1.55 | 1.38 | 1.19 | 1.13 | -0.23 |
| Sortino (365) | 2.84 | 2.31 | 2.04 | 2.11 | 1.98 | -0.30 |
| Calmar | 5.12 | 3.92 | 3.39 | 2.27 | 2.11 | -0.16 |
| Sharpe / Sortino (season days) | 1.67 / 2.55 | 1.39 / 2.08 | 1.25 / 1.83 | 1.07 / 1.90 | 1.02 / 1.78 | -0.20 / -0.27 |
| Max drawdown | -$402 (-0.39%) | -$429 (-0.42%) | -$440 (-0.43%) | -$8,058 (-6.30%) | -$8,085 (-6.38%) | -$2,230 (-2.20%) |
| Longest drawdown: calendar days (season days) | 211 (66) | 242 (97) | 242 (97) | 167 (43) | 167 (43) | 416 (271) ongoing |
| Win rate: trades / days | 66.6% / 61.0% | 66.6% / 61.0% | 66.6% / 61.0% | 66.6% / 62.3% | 66.6% / 62.3% | 69.3% / 57.4% |
| Profit factor (trades) | 1.25 | 1.20 | 1.18 | 1.43 | 1.40 | 0.98 |
| Avg win / avg loss | $18 / -$29 (0.62x) | $18 / -$29 (0.60x) | $17 / -$29 (0.59x) | $88 / -$124 (0.71x) | $87 / -$124 (0.70x) | $14 / -$32 (0.43x) |
| Best day | +$253 (+0.25%) | +$240 (+0.24%) | +$236 (+0.23%) | +$6,967 (+6.88%) | +$6,823 (+6.74%) | +$284 (+0.28%) |
| Worst day | -$272 (-0.27%) | -$273 (-0.27%) | -$273 (-0.27%) | -$5,011 (-3.92%) | -$5,012 (-3.95%) | -$327 (-0.33%) |
| % calendar days with a new trade | 60.6% | 60.6% | 60.6% | 60.6% | 60.6% | 67.7% |
| Trades (funded) / signals | 1,229 / 1,426 | 1,229 / 1,426 | 1,229 / 1,426 | 1,229 / 1,426 | 1,229 / 1,426 | 2,824 / 3,267 |
| Risked per trade: mean / median / max | $33 / $11 / $100 | $33 / $11 / $100 | $33 / $11 / $100 | $145 / $11 / $5,000 | $145 / $11 / $5,000 | $34 / $10 / $100 |
| Capital committed: avg / peak | $12 / $497 | $12 / $499 | $12 / $500 | $51 / $10,270 | $51 / $10,271 | $27 / $603 |
| Peak committed as % of equity | 0.5% | 0.5% | 0.5% | 10.2% | 10.2% | 0.6% |
| Capital deployed (sum of costs) | $40,559 | $40,748 | $40,853 | $177,727 | $178,608 | $94,689 |
| Fees paid | $184 | $685 | $948 | $3,144 | $4,359 | $1,390 |
| Return on capital deployed | +7.32% | +5.99% | +5.30% | +12.17% | +11.38% | -0.56% |
| Equal-weight game ROI | +2.27% | +1.15% | +0.48% | +1.15% | +0.48% | -0.83% |

QuantStats 0.0.81 computes the same ratios as a cross-check. The largest gap between our Sharpe/Sortino/CAGR/max-drawdown and QuantStats' over all scenarios is 1.67e-15, which is rounding. QuantStats counts a drawdown's length from its first day under water, so its “Longest DD Days” is usually one day shorter than ours, which counts from the last high. The tearsheets are `data/research/performance/tearsheet_<scenario>.html`.

**How to read the ratios.** The account holds cash nearly all the time. Its **return on $100k** is small (+1.34% per season at $100/game with the 0.05 fee), even where the return on money deployed looks healthy. Sharpe and Sortino do not change with leverage, so they are the fairest measure of the edge. Calmar compares the yearly return with the worst drawdown. The ratios are per year on calendar days. In-sample ratios from a rule picked out of a grid overstate what to expect going forward.

## 5. Every scenario (ledger execution)

Ledger model, $100k account. Each ROI is the return on capital deployed, and each CAGR is on the $100k with periods = 365. The full column set is in `metrics.csv`.

| Rule | Budget/game | Fees | Trades | Deployed | P&L | ROI deployed | CAGR | Sharpe | Sortino | Max DD | Calmar | Peak committed | P(season loss) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 03c_first | $100 | as_recorded | 2,824 | $94,352 | +$400 | +0.42% | +0.27% | 0.18 | 0.25 | -1.84% | 0.15 | $602 | 44% |
| 03c_first | $250 | as_recorded | 2,824 | $151,955 | -$1,372 | -0.90% | -0.94% | -0.30 | -0.40 | -3.98% | -0.24 | $1,256 | 60% |
| 03c_first | $500 | as_recorded | 2,824 | $204,226 | -$2,670 | -1.31% | -1.83% | -0.37 | -0.50 | -6.30% | -0.29 | $1,755 | 62% |
| 03c_first | $1,000 | as_recorded | 2,824 | $261,840 | -$5,929 | -2.26% | -4.08% | -0.52 | -0.69 | -9.10% | -0.45 | $2,992 | 68% |
| 03c_first | $2,000 | as_recorded | 2,824 | $324,498 | -$4,541 | -1.40% | -3.12% | -0.22 | -0.30 | -11.20% | -0.28 | $4,075 | 60% |
| 03c_first | $5,000 | as_recorded | 2,824 | $410,555 | +$11,789 | +2.87% | +7.90% | 0.52 | 0.80 | -11.01% | 0.72 | $8,672 | 36% |
| 03c_first | $100 | intl_0.05 | 2,824 | $94,689 | -$532 | -0.56% | -0.36% | -0.23 | -0.30 | -2.20% | -0.16 | $603 | 58% |
| 03c_first | $250 | intl_0.05 | 2,824 | $152,766 | -$2,853 | -1.87% | -1.96% | -0.64 | -0.84 | -4.73% | -0.41 | $1,256 | 71% |
| 03c_first | $500 | intl_0.05 | 2,824 | $205,476 | -$4,643 | -2.26% | -3.19% | -0.66 | -0.87 | -7.32% | -0.44 | $1,764 | 72% |
| 03c_first | $1,000 | intl_0.05 | 2,824 | $263,533 | -$8,438 | -3.20% | -5.84% | -0.76 | -0.99 | -11.45% | -0.51 | $3,002 | 74% |
| 03c_first | $2,000 | intl_0.05 | 2,824 | $326,938 | -$7,845 | -2.40% | -5.42% | -0.42 | -0.57 | -14.00% | -0.39 | $4,092 | 67% |
| 03c_first | $5,000 | intl_0.05 | 2,824 | $413,675 | +$7,635 | +1.85% | +5.15% | 0.37 | 0.56 | -12.08% | 0.43 | $8,808 | 41% |
| 03c_first | $100 | us_0.0695 | 2,824 | $94,893 | -$1,068 | -1.13% | -0.73% | -0.46 | -0.61 | -2.46% | -0.30 | $604 | 66% |
| 03c_first | $250 | us_0.0695 | 2,824 | $153,238 | -$3,699 | -2.41% | -2.54% | -0.83 | -1.08 | -5.20% | -0.49 | $1,256 | 77% |
| 03c_first | $500 | us_0.0695 | 2,824 | $206,175 | -$5,791 | -2.81% | -3.99% | -0.84 | -1.09 | -7.99% | -0.50 | $1,768 | 77% |
| 03c_first | $1,000 | us_0.0695 | 2,824 | $264,467 | -$9,895 | -3.74% | -6.86% | -0.90 | -1.16 | -12.74% | -0.54 | $3,006 | 79% |
| 03c_first | $2,000 | us_0.0695 | 2,824 | $328,306 | -$9,831 | -2.99% | -6.82% | -0.55 | -0.72 | -15.18% | -0.45 | $4,119 | 70% |
| 03c_first | $5,000 | us_0.0695 | 2,824 | $415,401 | +$4,970 | +1.20% | +3.36% | 0.27 | 0.41 | -12.82% | 0.26 | $8,862 | 43% |
| 10c_every | $100 | as_recorded | 2,130 | $64,807 | +$3,057 | +4.72% | +2.08% | 1.39 | 2.09 | -0.71% | 2.91 | $751 | 10% |
| 10c_every | $250 | as_recorded | 2,347 | $118,368 | +$6,154 | +5.20% | +4.16% | 1.36 | 2.05 | -1.66% | 2.51 | $1,453 | 11% |
| 10c_every | $500 | as_recorded | 2,480 | $172,753 | +$10,732 | +6.21% | +7.20% | 1.46 | 2.24 | -3.09% | 2.33 | $2,460 | 9% |
| 10c_every | $1,000 | as_recorded | 2,540 | $235,457 | +$17,939 | +7.62% | +11.92% | 1.55 | 2.44 | -4.24% | 2.81 | $3,221 | 8% |
| 10c_every | $2,000 | as_recorded | 2,611 | $311,215 | +$28,361 | +9.11% | +18.57% | 1.58 | 2.47 | -6.35% | 2.92 | $5,278 | 8% |
| 10c_every | $5,000 | as_recorded | 2,660 | $399,411 | +$41,010 | +10.27% | +26.42% | 1.49 | 2.45 | -11.04% | 2.39 | $11,278 | 10% |
| 10c_every | $100 | intl_0.05 | 2,127 | $65,011 | +$2,236 | +3.44% | +1.52% | 1.03 | 1.51 | -0.76% | 2.01 | $753 | 18% |
| 10c_every | $250 | intl_0.05 | 2,344 | $118,910 | +$4,632 | +3.90% | +3.14% | 1.04 | 1.52 | -1.85% | 1.69 | $1,457 | 17% |
| 10c_every | $500 | intl_0.05 | 2,477 | $173,857 | +$8,487 | +4.88% | +5.71% | 1.17 | 1.75 | -3.41% | 1.67 | $2,476 | 14% |
| 10c_every | $1,000 | intl_0.05 | 2,539 | $237,133 | +$14,758 | +6.22% | +9.85% | 1.29 | 1.99 | -4.57% | 2.15 | $3,258 | 13% |
| 10c_every | $2,000 | intl_0.05 | 2,609 | $313,658 | +$24,125 | +7.69% | +15.89% | 1.37 | 2.08 | -6.95% | 2.29 | $5,278 | 10% |
| 10c_every | $5,000 | intl_0.05 | 2,659 | $403,370 | +$36,101 | +8.95% | +23.40% | 1.33 | 2.14 | -11.40% | 2.05 | $11,278 | 13% |
| 10c_every | $100 | us_0.0695 | 2,126 | $65,129 | +$1,789 | +2.75% | +1.22% | 0.83 | 1.20 | -0.77% | 1.57 | $754 | 22% |
| 10c_every | $250 | us_0.0695 | 2,343 | $119,219 | +$3,806 | +3.19% | +2.58% | 0.86 | 1.25 | -1.93% | 1.34 | $1,459 | 22% |
| 10c_every | $500 | us_0.0695 | 2,474 | $174,429 | +$7,256 | +4.16% | +4.90% | 1.01 | 1.49 | -3.54% | 1.38 | $2,482 | 18% |
| 10c_every | $1,000 | us_0.0695 | 2,536 | $238,015 | +$13,060 | +5.49% | +8.74% | 1.15 | 1.76 | -4.76% | 1.83 | $3,272 | 14% |
| 10c_every | $2,000 | us_0.0695 | 2,608 | $314,947 | +$21,886 | +6.95% | +14.46% | 1.25 | 1.89 | -7.20% | 2.01 | $5,287 | 13% |
| 10c_every | $5,000 | us_0.0695 | 2,657 | $405,400 | +$33,372 | +8.23% | +21.71% | 1.24 | 1.98 | -11.76% | 1.85 | $11,287 | 14% |
| 10c_first | $100 | as_recorded | 1,229 | $40,559 | +$2,970 | +7.32% | +2.02% | 1.85 | 2.84 | -0.39% | 5.12 | $497 | 4% |
| 10c_first | $250 | as_recorded | 1,229 | $65,812 | +$5,107 | +7.76% | +3.46% | 1.62 | 2.47 | -1.02% | 3.40 | $1,003 | 7% |
| 10c_first | $500 | as_recorded | 1,229 | $87,099 | +$8,155 | +9.36% | +5.49% | 1.66 | 2.62 | -1.64% | 3.34 | $1,503 | 6% |
| 10c_first | $1,000 | as_recorded | 1,229 | $111,452 | +$10,540 | +9.46% | +7.08% | 1.36 | 2.08 | -2.48% | 2.85 | $2,268 | 11% |
| 10c_first | $2,000 | as_recorded | 1,229 | $141,654 | +$13,022 | +9.19% | +8.71% | 1.06 | 1.57 | -4.31% | 2.02 | $4,268 | 16% |
| 10c_first | $5,000 | as_recorded | 1,229 | $176,026 | +$24,066 | +13.67% | +15.85% | 1.29 | 2.37 | -6.18% | 2.57 | $10,268 | 12% |
| 10c_first | $100 | intl_0.05 | 1,229 | $40,748 | +$2,440 | +5.99% | +1.66% | 1.55 | 2.31 | -0.42% | 3.92 | $499 | 8% |
| 10c_first | $250 | intl_0.05 | 1,229 | $66,255 | +$4,217 | +6.36% | +2.86% | 1.36 | 2.01 | -1.12% | 2.55 | $1,012 | 11% |
| 10c_first | $500 | intl_0.05 | 1,229 | $87,808 | +$6,989 | +7.96% | +4.72% | 1.44 | 2.22 | -1.66% | 2.84 | $1,512 | 10% |
| 10c_first | $1,000 | intl_0.05 | 1,229 | $112,322 | +$9,023 | +8.03% | +6.07% | 1.18 | 1.77 | -2.52% | 2.41 | $2,270 | 14% |
| 10c_first | $2,000 | intl_0.05 | 1,229 | $142,923 | +$11,025 | +7.71% | +7.40% | 0.92 | 1.33 | -4.36% | 1.70 | $4,270 | 20% |
| 10c_first | $5,000 | intl_0.05 | 1,229 | $177,727 | +$21,637 | +12.17% | +14.30% | 1.19 | 2.11 | -6.30% | 2.27 | $10,270 | 14% |
| 10c_first | $100 | us_0.0695 | 1,229 | $40,853 | +$2,164 | +5.30% | +1.47% | 1.38 | 2.04 | -0.43% | 3.39 | $500 | 10% |
| 10c_first | $250 | us_0.0695 | 1,229 | $66,490 | +$3,752 | +5.64% | +2.54% | 1.22 | 1.78 | -1.16% | 2.20 | $1,016 | 13% |
| 10c_first | $500 | us_0.0695 | 1,229 | $88,160 | +$6,365 | +7.22% | +4.30% | 1.33 | 2.02 | -1.69% | 2.54 | $1,516 | 11% |
| 10c_first | $1,000 | us_0.0695 | 1,229 | $112,768 | +$8,229 | +7.30% | +5.54% | 1.09 | 1.61 | -2.55% | 2.17 | $2,271 | 17% |
| 10c_first | $2,000 | us_0.0695 | 1,229 | $143,578 | +$9,995 | +6.96% | +6.72% | 0.84 | 1.20 | -4.37% | 1.53 | $4,271 | 22% |
| 10c_first | $5,000 | us_0.0695 | 1,229 | $178,608 | +$20,325 | +11.38% | +13.45% | 1.13 | 1.98 | -6.38% | 2.11 | $10,271 | 16% |

## 6. Equity curve and drawdowns

- **10c_first · $100 · as_recorded.** Equity goes from $100,000 to $102,970. The largest drop is -$402 (-0.39%). It ran from a high on 2025-09-13 to a low on 2025-09-26 and was recovered by 2026-04-12. The longest spell below a previous high lasted 211 calendar days (2025-09-13 → 2026-04-12). Only 66 of those were season days; the rest is the off-season, when nothing trades. 13 of 14 trading months were positive. The best month was +0.922% and the worst -0.004%.
- **10c_first · $100 · intl_0.05.** Equity goes from $100,000 to $102,440. The largest drop is -$429 (-0.42%). It ran from a high on 2025-09-13 to a low on 2025-09-26 and was recovered by 2026-05-13. The longest spell below a previous high lasted 242 calendar days (2025-09-13 → 2026-05-13). Only 97 of those were season days; the rest is the off-season, when nothing trades. 11 of 14 trading months were positive. The best month was +0.835% and the worst -0.041%.
- **10c_first · $100 · us_0.0695.** Equity goes from $100,000 to $102,164. The largest drop is -$440 (-0.43%). It ran from a high on 2025-09-13 to a low on 2025-09-26 and was recovered by 2026-05-13. The longest spell below a previous high lasted 242 calendar days (2025-09-13 → 2026-05-13). Only 97 of those were season days; the rest is the off-season, when nothing trades. 10 of 14 trading months were positive. The best month was +0.802% and the worst -0.068%.
- **10c_first · $5,000 · intl_0.05.** Equity goes from $100,000 to $121,637. The largest drop is -$8,058 (-6.30%). It ran from a high on 2026-08-22 to a low on 2026-09-12 and had not recovered by the end of the data. The longest spell below a previous high lasted 167 calendar days (2025-10-27 → 2026-04-12). Only 43 of those were season days; the rest is the off-season, when nothing trades. 9 of 14 trading months were positive. The best month was +4.991% and the worst -3.360%.
- **10c_first · $5,000 · us_0.0695.** Equity goes from $100,000 to $120,325. The largest drop is -$8,085 (-6.38%). It ran from a high on 2026-08-22 to a low on 2026-09-12 and had not recovered by the end of the data. The longest spell below a previous high lasted 167 calendar days (2025-10-27 → 2026-04-12). Only 43 of those were season days; the rest is the off-season, when nothing trades. 9 of 14 trading months were positive. The best month was +4.917% and the worst -3.421%.
- **03c_first · $100 · intl_0.05.** Equity goes from $100,000 to $99,468. The largest drop is -$2,230 (-2.20%). It ran from a high on 2025-07-29 to a low on 2026-07-19 and had not recovered by the end of the data. The longest spell below a previous high lasted 416 calendar days (2025-07-29 → 2026-09-18, still under water at the end of the data). Only 271 of those were season days; the rest is the off-season, when nothing trades. 9 of 14 trading months were positive. The best month was +0.487% and the worst -1.087%.

**Monthly returns on the $100k** (compounded daily returns; months with no MLB games are omitted):

| Month | 10c_first · $100 · as_recorded | 10c_first · $100 · intl_0.05 | 10c_first · $100 · us_0.0695 | 10c_first · $5,000 · intl_0.05 | 10c_first · $5,000 · us_0.0695 | 03c_first · $100 · intl_0.05 |
|---|---|---|---|---|---|---|
| 2025-04 | +0.20% | +0.17% | +0.16% | +1.47% | +1.41% | +0.41% |
| 2025-05 | +0.10% | +0.02% | -0.01% | +3.48% | +3.23% | +0.34% |
| 2025-06 | +0.27% | +0.18% | +0.14% | +2.45% | +2.30% | +0.05% |
| 2025-07 | +0.92% | +0.83% | +0.80% | +4.83% | +4.75% | +0.49% |
| 2025-08 | +0.20% | +0.12% | +0.09% | -1.57% | -1.65% | -0.46% |
| 2025-09 | +0.03% | -0.04% | -0.07% | +3.12% | +3.06% | -1.09% |
| 2025-10 | +0.18% | +0.16% | +0.16% | +1.61% | +1.56% | +0.03% |
| 2025-11 | -0.00% | -0.00% | -0.00% | -0.00% | -0.00% | -0.09% |
| 2026-04 | +0.06% | +0.04% | +0.02% | -0.02% | -0.08% | -0.24% |
| 2026-05 | +0.41% | +0.39% | +0.37% | +2.24% | +2.19% | +0.03% |
| 2026-06 | +0.01% | -0.00% | -0.02% | +4.99% | +4.92% | +0.13% |
| 2026-07 | +0.32% | +0.32% | +0.31% | +1.62% | +1.61% | +0.03% |
| 2026-08 | +0.05% | +0.05% | +0.04% | -3.36% | -3.42% | +0.09% |
| 2026-09 | +0.16% | +0.17% | +0.15% | -0.77% | -0.89% | -0.23% |

Drawdowns are measured on daily closing equity, after realized results only. Measured after each game settles instead, the worst drop for the headline $100-per-game intl_0.05 case is -$446. Positions last about 3.5 hours (median), and most open and settle on the same day, so daily closing equity is close to marked-to-market equity. The daily series is in `daily_returns_<scenario>.csv`, and the dashboard JSON has the equity and drawdown series.

## 7. Sizing and capacity: how far can this be pushed?

- **Liquidity limits this strategy, not capital.** At $100 per game (intl_0.05), the average trade risks $33 and the median $11. Peak capital in play at once is $499, and the average is $12. Most of the $100,000 sits idle.
- **A 50x larger budget buys about 4x the money in play.** At $5,000 per game (5% of equity), the average trade is still only $145, the median $11, and peak commitment $10,270 (10.2% of equity). The ledger fills only what the first later print showed, and that print is usually small.
- **The extra profit at large budgets comes from a handful of trades.** At $5,000 (intl_0.05), the 20 largest trades hold 42% of the capital and contribute +$16,301 of the +$21,637 total. Without them, ROI on deployed capital is +5.18%. The same rule has an equal-weight game ROI of only +1.15% at every budget.
- **10c every-signal looks bigger but is weaker per game.** At $5,000 with the 0.05 fee it makes +$36,101 (+8.95% on $403,370). Its equal-weight game ROI is -2.57%, and the 20 largest trades supply +$13,442. It is the same large-print effect again, not a better rule.
- **Return on the $100k.** The best feasible budget earns +11.40% per season at intl_0.05 and +10.74% at the US fee. At $100 per game it earns +1.34% and +1.19%. These are in-sample upper estimates.
- **Print liquidity (upper bound, 10c first signals).** Counting only trades within 2c of the reference price, 68% of signals had none before the next play and 56% had none before the next half-inning. The median amount available is $0 in both cases, and the 90th percentile before the next half-inning is $1,277. At any price before the next half-inning, the median is $837 and the 90th percentile $8,081. Every one of those prints was another trader's fill, so these are ceilings, not what we could have taken.
- **Sweep upper bound.** If we could have matched **every** same-side print within 2c of the reference before the next half-inning (an optimistic ceiling), the 10c first-signal rule would have made +$74,215 at $5,000/game with the 0.05 fee: +12.06% on $615,574 deployed, +35.80% per season on the $100k, peak commitment $10,479. Only 631 of 1,426 signals get any fill under that rule.

**Allocation for a $100k account.** Capital is not the limit, so a per-game cap of 1–5% of equity ($1k–$5k) costs nothing in cash. It only lets the order take a large print when one appears. The real question is whether the edge is real after fees. At both current fee levels, the 95% ranges below include a loss for a season. Size for the forward test, not for these numbers.

## 8. Law of large numbers: what hundreds or thousands of games would show

We resample the per-game results we observed. A “season” is the number of games that had a signal in 2025, the one complete season: 818 games for 10c_first and 1,628 games for 03c_first. The day-block version resamples whole days, so games on the same day stay together. Both assume the future looks exactly like this sample, selection bias included.

| Scenario | P(season loss) | 5th pct P&L | Median P&L | 95th pct P&L | Return on $100k (5/50/95) | P(loss), day-block |
|---|---|---|---|---|---|---|
| 10c_first · $100 · as_recorded | 4.4% | +$58 | +$1,719 | +$3,334 | +0.06% / +1.72% / +3.33% | 4.9% |
| 10c_first · $100 · intl_0.05 | 7.7% | -$234 | +$1,394 | +$3,004 | -0.23% / +1.39% / +3.00% | 8.1% |
| 10c_first · $100 · us_0.0695 | 10.5% | -$390 | +$1,242 | +$2,836 | -0.39% / +1.24% / +2.84% | 9.9% |
| 10c_first · $5,000 · intl_0.05 | 14.4% | -$6,519 | +$12,206 | +$32,643 | -6.52% / +12.21% / +32.64% | 14.9% |
| 10c_first · $5,000 · us_0.0695 | 15.7% | -$7,372 | +$11,438 | +$31,224 | -7.37% / +11.44% / +31.22% | 17.1% |
| 03c_first · $100 · intl_0.05 | 58.3% | -$2,507 | -$274 | +$1,901 | -2.51% / -0.27% / +1.90% | 58.2% |

**How the 95% range of ROI (on capital deployed) narrows with more games**, drawn from the observed traded games:

| Scenario | 500 games | 1,000 games | 2,500 games | 5,000 games | Games for range to exclude 0 |
|---|---|---|---|---|---|
| 10c_first · $100 · as_recorded | -2.8% to +17.3% (P loss 8%) | +0.0% to +14.3% (P loss 2%) | +2.7% to +11.8% (P loss 0%) | +4.1% to +10.4% (P loss 0%) | 943 |
| 10c_first · $100 · intl_0.05 | -4.0% to +15.7% (P loss 11%) | -1.0% to +13.0% (P loss 5%) | +1.6% to +10.3% (P loss 0%) | +2.9% to +9.1% (P loss 0%) | 1,359 |
| 10c_first · $100 · us_0.0695 | -4.5% to +14.9% (P loss 14%) | -1.8% to +12.1% (P loss 7%) | +0.9% to +9.6% (P loss 1%) | +2.2% to +8.3% (P loss 0%) | 1,704 |
| 10c_first · $5,000 · intl_0.05 | -14.3% to +36.9% (P loss 18%) | -7.0% to +30.2% (P loss 11%) | +0.0% to +23.7% (P loss 2%) | +3.7% to +20.4% (P loss 0%) | 2,321 |
| 10c_first · $5,000 · us_0.0695 | -15.5% to +35.9% (P loss 20%) | -7.1% to +29.2% (P loss 11%) | -0.4% to +22.7% (P loss 3%) | +3.1% to +19.5% (P loss 0%) | 2,595 |
| 03c_first · $100 · intl_0.05 | -9.7% to +8.5% (P loss 55%) | -7.0% to +5.9% (P loss 57%) | -4.6% to +3.5% (P loss 60%) | -3.4% to +2.4% (P loss 64%) | never (edge ≤ 0) |

The range shrinks roughly as 1/√n. With 4x as many games it is half as wide. The last column gives the number of traded games at which the 95% range would stop including zero, **if the true edge equals the one observed here**. Remember why that assumption is optimistic. Resampling the past only measures luck in which games happened. It cannot remove the bias from choosing the 10c rule after looking. It also cannot remove the gap between trade-print fills and real fills. Only the forward test can.

## 9. Caveats (read before using any number above)

1. **Historical and already inspected.** 2025–26 is the data the rule was built and chosen on.
2. **Picked from a grid.** The 10c cutoff is the best of 12 fixed rules on this data. Expect regression toward the 3c control, which is about zero or negative after fees.
3. **Execution proxy.** A fill is someone else's later trade plus 1c. It is not our own order in the book. The capacity figures show the first print usually sits above the reference price. When no trade prints before the next play, a live order may get nothing, as the first live signal did.
4. **Optimistic clock.** Signals use a retrospective state time plus 5 s. Real latency and cancel behavior are not modeled.
5. **Realized-only accounting.** Positions are carried at cost until settlement, so the drawdowns shown understate what live marks would show during games.
6. **Concentration.** Large-budget results depend on a few big prints (section 7).
7. **Not forward evidence.** The forward paper tests are the only unbiased check.

## 10. Files

- `data/research/performance/metrics.csv`: one row per scenario (ledger plus sweep upper bounds), all metrics.
- `data/research/performance/daily_returns_<scenario>.csv`: date, pnl, equity, return, committed, drawdown.
- `data/research/performance/trades_<scenario>.csv`: every funded trade with entry, exit, price, shares, cost, fee, payout, pnl.
- `data/research/performance/tearsheet_<scenario>.html`: QuantStats tearsheets for the headline scenarios.
- `data/research/performance/lln.csv` and `bootstrap_seasons.csv`: probability analysis.
- `data/research/performance/dashboard.json`: compact data for a dashboard.
- `data/research/performance/sweep/`: the same files for the sweep (upper-bound) execution.

Reproduce: `PYTHONPATH=. nice .venv/bin/python -m pmsports.analysis.performance`
