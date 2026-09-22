# Received-book execution study

Exploratory replay of 2026-09-19: 4,037,480 received book messages across 15 matched games. All score increases observed on the MLB and Polymarket sports feeds are retained; the first observation establishes a baseline. No subsequent price-move filter is used. Primary comparison is3s;1s and5s are declared latency sensitivities. Only the first score attempt per game/feed can trade. Limit is known initial ask+1c; requested shares are fixed before delay, with a100-dollar fee-inclusive target. Depth can be partially consumed, is not reused, and must have a received snapshot and an update within5s. Historical minimum size/tick and actual order acceptance are unobserved, so fills are hypothetical displayed-depth crossings.

Same-millisecond book messages precede receipt/timer events; sub-millisecond ordering is unavailable. Pending exits at capture end are identified as censored, with residual settlement kept separate from a tested exit.

Mean reversion fits market-price mean/std per exact inning/half/outs/bases/lead using prior-season games only, equally weighting each game within a cell. At an observed MLB state receipt, buy a side below mean minus one standard deviation; at least30 prior games required. Only one mean signal per game is attempted, after3s. Target is the fixed prior mean; a received bid reaching it submits one3s-delayed limit exit. After60s any residual submits a3s-delayed aggressive exit; unfilled shares remain through actual contract settlement. Entry and exit fees are both charged; ROI denominator is entry principal plus all fees, consistent with the desk. No optimization follows the result.

| policy            |   observations |   eligible_orders |   fills |   games |   partial |   capital_usd |   resolved_capital_usd |   pnl_usd |     roi |   ci_lo |   ci_hi |   unresolved |   top_game_pnl |   median_capital |   exit_shares |   residual_shares |
|:------------------|---------------:|------------------:|--------:|--------:|----------:|--------------:|-----------------------:|----------:|--------:|--------:|--------:|-------------:|---------------:|-----------------:|--------------:|------------------:|
| mean_reversion_3s |           1248 |                12 |      12 |      12 |         0 |     1207.3989 |              1207.3989 |  -87.7880 | -0.0727 | -0.0938 | -0.0524 |            0 |        -1.0574 |          97.9422 |     2874.8980 |            0.0000 |
| mlb_score_1s      |             84 |                15 |      15 |      15 |         0 |     1468.0907 |              1468.0907 |  -77.0372 | -0.0525 | -0.4142 |  0.2657 |            0 |        59.8476 |          98.5992 |        0.0000 |         3644.1300 |
| mlb_score_3s      |             84 |                15 |      15 |      15 |         0 |     1457.6957 |              1457.6957 |  -66.6423 | -0.0457 | -0.4063 |  0.2718 |            0 |        59.8476 |          98.5992 |        0.0000 |         3644.1300 |
| mlb_score_5s      |             84 |                15 |      14 |      14 |         1 |     1344.6596 |              1344.6596 |   46.3938 |  0.0345 | -0.3304 |  0.3380 |            0 |        59.8476 |          98.5329 |        0.0000 |         3043.2623 |
| sports_score_1s   |             84 |                15 |      15 |      15 |         2 |     1356.9230 |              1356.9230 |  -60.4089 | -0.0445 | -0.3995 |  0.2673 |            0 |        59.8476 |          98.5559 |        0.0000 |         2748.2364 |
| sports_score_3s   |             84 |                15 |      15 |      15 |         1 |     1401.1845 |              1401.1845 | -104.6704 | -0.0747 | -0.4353 |  0.2393 |            0 |        59.8476 |          98.5333 |        0.0000 |         3543.0588 |
| sports_score_5s   |             84 |                15 |      14 |      14 |         1 |     1353.7242 |              1353.7242 | -128.1811 | -0.0947 | -0.4565 |  0.2311 |            0 |        59.8476 |          98.4843 |        0.0000 |         3472.0877 |

Prior cells: 6,255; cells with at least30 games: 877. Actual token payouts verified for 15/15 games; unresolved positions cannot earn a claimed return.

Confidence intervals resample games, but one observed day is insufficient to validate a durable edge. Strategy variants share outcomes and are not independent discoveries. No exchange orders were submitted. Raw audits, reasons, source identities and summaries: data/research/live_execution/2026-09-19/.

| policy            | reason                         |   rows |
|:------------------|:-------------------------------|-------:|
| mean_reversion_3s |                                |     12 |
| mean_reversion_3s | earlier_mean_signal_per_game   |    870 |
| mean_reversion_3s | insufficient_prior_cell        |    216 |
| mean_reversion_3s | no_one_sd_discount_or_no_ask   |    149 |
| mean_reversion_3s | stale_ask_at_signal            |      1 |
| mlb_score_1s      |                                |     15 |
| mlb_score_1s      | earlier_score_attempt_per_game |     69 |
| mlb_score_3s      |                                |     15 |
| mlb_score_3s      | earlier_score_attempt_per_game |     69 |
| mlb_score_5s      |                                |     14 |
| mlb_score_5s      | earlier_score_attempt_per_game |     69 |
| mlb_score_5s      | no_depth_at_limit              |      1 |
| sports_score_1s   |                                |     15 |
| sports_score_1s   | earlier_score_attempt_per_game |     69 |
| sports_score_3s   |                                |     15 |
| sports_score_3s   | earlier_score_attempt_per_game |     69 |
| sports_score_5s   |                                |     14 |
| sports_score_5s   | earlier_score_attempt_per_game |     69 |
| sports_score_5s   | no_depth_at_limit              |      1 |
