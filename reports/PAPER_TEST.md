# Forward paper test

Generated 2026-09-25 06:47 UTC. **Paper only: no orders are sent.** Rules: `v1`, spec hash `a8fef17dca77`. Protocols: [MLB](PROSPECTIVE_PROTOCOL.md), [soccer](PROSPECTIVE_SOCCER_PROTOCOL.md).

Activated 2026-09-25T06:45:29.161918+00:00 at commit `1a25ce33e5`; code hash `00932f4fa5ac` (matches the current code).

## Endpoints and gate

**MLB** (`mlb_10c`): 0/500 eligible games with outcome, day 0.0/365, 0 funded primary games. Endpoint not reached (provisional); fewer than 100 funded games is inconclusive.

| check                                           | status   |
|:------------------------------------------------|:---------|
| primary net cash > 0                            | n/a      |
| primary 95% interval lower bound > 0            | n/a      |
| +1c sensitivity net cash > 0                    | n/a      |
| +2 s delay sensitivity net cash > 0             | n/a      |
| ROI > 0 after removing best 1 / 3 / 1% of games | n/a      |
| funded primary games >= 100                     | fail     |

**SOCCER** (`soccer_added_time`): 0/200 funded primary games, day 0.0/183, 0 funded primary games. Endpoint not reached (provisional); fewer than 100 funded games is inconclusive.

| check                                           | status   |
|:------------------------------------------------|:---------|
| primary net cash > 0                            | n/a      |
| primary 95% interval lower bound > 0            | n/a      |
| +1c sensitivity net cash > 0                    | n/a      |
| +2 s delay sensitivity net cash > 0             | n/a      |
| ROI > 0 after removing best 1 / 3 / 1% of games | n/a      |
| funded primary games >= 100                     | fail     |
| primary beats matched soccer_ctrl_75_85         | n/a      |
| primary beats matched soccer_ctrl_70_80         | n/a      |

## Coverage

No `mlb_map.jsonl` discovery records yet.

Legacy `games.jsonl` MLB markets captured (shakedown-only mapping): 16.

| window   | match           |   soccer games |
|:---------|:----------------|---------------:|
| primary  | exact           |              1 |
| primary  | unmapped_league |              2 |

Missing captures among exact (pregame-admitted) games; these games stay in the counts above:

| sport   | window   |   exact games |   no state feed |   no signal logged |   no book snapshot |   primary decision |
|:--------|:---------|--------------:|----------------:|-------------------:|-------------------:|-------------------:|
| soccer  | primary  |             1 |               1 |                  1 |                  0 |                  0 |

Signals evaluated (mlb, all windows):

| policy         |   signals |   below_threshold |   after_first_signal |   no_reference |   qualified |   state:tied |   state:game_over |
|:---------------|----------:|------------------:|---------------------:|---------------:|------------:|-------------:|------------------:|
| mlb_10c        |       266 |               117 |                   55 |             27 |           5 |           46 |                16 |
| mlb_03c        |       266 |                85 |                   86 |             24 |           9 |           46 |                16 |
| mlb_10c_delay2 |       266 |               117 |                   55 |             27 |           5 |           46 |                16 |

Signals evaluated (soccer, all windows):

| policy            |   signals |
|:------------------|----------:|
| soccer_added_time |         0 |
| soccer_ctrl_75_85 |         0 |
| soccer_ctrl_70_80 |         0 |
| soccer_delay2     |         0 |
| soccer_delay10    |         0 |
| soccer_delay60    |         0 |

## Primary (prospective) results

### Policies

| policy                   | role        |   attempts |   filled |   partial |   unfilled |   rejected |   open |   settled | capital   | net cash   | ROI   | 95% CI     | equal-game ROI   | ex best 1   | ex best 3   | ex best 1%   | US-fee ROI   |
|:-------------------------|:------------|-----------:|---------:|----------:|-----------:|-----------:|-------:|----------:|:----------|:-----------|:------|:-----------|:-----------------|:------------|:------------|:-------------|:-------------|
| mlb_10c                  | primary     |          0 |        0 |         0 |          0 |          0 |      0 |         0 | $0.00     | $0.00      | n/a   | n/a .. n/a | n/a              | n/a         | n/a         | n/a          | n/a          |
| mlb_03c                  | control     |          0 |        0 |         0 |          0 |          0 |      0 |         0 | $0.00     | $0.00      | n/a   | n/a .. n/a | n/a              | n/a         | n/a         | n/a          | n/a          |
| mlb_10c_delay2           | sensitivity |          0 |        0 |         0 |          0 |          0 |      0 |         0 | $0.00     | $0.00      | n/a   | n/a .. n/a | n/a              | n/a         | n/a         | n/a          | n/a          |
| mlb_10c_plus1c           | sensitivity |          0 |        0 |         0 |          0 |          0 |      0 |         0 | $0.00     | $0.00      | n/a   | n/a .. n/a | n/a              | n/a         | n/a         | n/a          | n/a          |
| soccer_added_time        | primary     |          0 |        0 |         0 |          0 |          0 |      0 |         0 | $0.00     | $0.00      | n/a   | n/a .. n/a | n/a              | n/a         | n/a         | n/a          | n/a          |
| soccer_ctrl_75_85        | control     |          0 |        0 |         0 |          0 |          0 |      0 |         0 | $0.00     | $0.00      | n/a   | n/a .. n/a | n/a              | n/a         | n/a         | n/a          | n/a          |
| soccer_ctrl_70_80        | control     |          0 |        0 |         0 |          0 |          0 |      0 |         0 | $0.00     | $0.00      | n/a   | n/a .. n/a | n/a              | n/a         | n/a         | n/a          | n/a          |
| soccer_delay2            | sensitivity |          0 |        0 |         0 |          0 |          0 |      0 |         0 | $0.00     | $0.00      | n/a   | n/a .. n/a | n/a              | n/a         | n/a         | n/a          | n/a          |
| soccer_delay10           | sensitivity |          0 |        0 |         0 |          0 |          0 |      0 |         0 | $0.00     | $0.00      | n/a   | n/a .. n/a | n/a              | n/a         | n/a         | n/a          | n/a          |
| soccer_delay60           | sensitivity |          0 |        0 |         0 |          0 |          0 |      0 |         0 | $0.00     | $0.00      | n/a   | n/a .. n/a | n/a              | n/a         | n/a         | n/a          | n/a          |
| soccer_added_time_plus1c | sensitivity |          0 |        0 |         0 |          0 |          0 |      0 |         0 | $0.00     | $0.00      | n/a   | n/a .. n/a | n/a              | n/a         | n/a         | n/a          | n/a          |

### No-fill and rejection reasons

_none_

### Paired and matched comparisons

MLB `mlb_10c` minus `mlb_03c` over 0 common settled games: total $0.00, mean n/a (95% CI n/a .. n/a).

Soccer primary minus `soccer_ctrl_75_85` (common cent bins 0, control reweighted to primary): n/a (95% CI n/a .. n/a).
Soccer primary minus `soccer_ctrl_70_80` (common cent bins 0, control reweighted to primary): n/a (95% CI n/a .. n/a).

### Primary diagnostics

`mlb_10c`:

_no fills_

`soccer_added_time`:

_no fills_


## Shakedown (not evidence)

### Policies

| policy                   | role        |   attempts |   filled |   partial |   unfilled |   rejected |   open |   settled | capital   | net cash   | ROI   | 95% CI     | equal-game ROI   | ex best 1   | ex best 3   | ex best 1%   | US-fee ROI   |
|:-------------------------|:------------|-----------:|---------:|----------:|-----------:|-----------:|-------:|----------:|:----------|:-----------|:------|:-----------|:-----------------|:------------|:------------|:-------------|:-------------|
| mlb_10c                  | primary     |          5 |        5 |         0 |          0 |          0 |      5 |         0 | $0.00     | $0.00      | n/a   | n/a .. n/a | n/a              | n/a         | n/a         | n/a          | n/a          |
| mlb_03c                  | control     |          9 |        9 |         0 |          0 |          0 |      9 |         0 | $0.00     | $0.00      | n/a   | n/a .. n/a | n/a              | n/a         | n/a         | n/a          | n/a          |
| mlb_10c_delay2           | sensitivity |          5 |        5 |         0 |          0 |          0 |      5 |         0 | $0.00     | $0.00      | n/a   | n/a .. n/a | n/a              | n/a         | n/a         | n/a          | n/a          |
| mlb_10c_plus1c           | sensitivity |          5 |        5 |         0 |          0 |          0 |      5 |         0 | $0.00     | $0.00      | n/a   | n/a .. n/a | n/a              | n/a         | n/a         | n/a          | n/a          |
| soccer_added_time        | primary     |          0 |        0 |         0 |          0 |          0 |      0 |         0 | $0.00     | $0.00      | n/a   | n/a .. n/a | n/a              | n/a         | n/a         | n/a          | n/a          |
| soccer_ctrl_75_85        | control     |          0 |        0 |         0 |          0 |          0 |      0 |         0 | $0.00     | $0.00      | n/a   | n/a .. n/a | n/a              | n/a         | n/a         | n/a          | n/a          |
| soccer_ctrl_70_80        | control     |          0 |        0 |         0 |          0 |          0 |      0 |         0 | $0.00     | $0.00      | n/a   | n/a .. n/a | n/a              | n/a         | n/a         | n/a          | n/a          |
| soccer_delay2            | sensitivity |          0 |        0 |         0 |          0 |          0 |      0 |         0 | $0.00     | $0.00      | n/a   | n/a .. n/a | n/a              | n/a         | n/a         | n/a          | n/a          |
| soccer_delay10           | sensitivity |          0 |        0 |         0 |          0 |          0 |      0 |         0 | $0.00     | $0.00      | n/a   | n/a .. n/a | n/a              | n/a         | n/a         | n/a          | n/a          |
| soccer_delay60           | sensitivity |          0 |        0 |         0 |          0 |          0 |      0 |         0 | $0.00     | $0.00      | n/a   | n/a .. n/a | n/a              | n/a         | n/a         | n/a          | n/a          |
| soccer_added_time_plus1c | sensitivity |          0 |        0 |         0 |          0 |          0 |      0 |         0 | $0.00     | $0.00      | n/a   | n/a .. n/a | n/a              | n/a         | n/a         | n/a          | n/a          |

### No-fill and rejection reasons

_none_

## Notes

- ROI = net cash / fee-inclusive capital over settled positions; open positions are listed but excluded.
- `+1c` rows reprice every filled share one cent higher (fees recomputed); `US-fee` reprices fees at 0.0695.
- Intervals resample whole games (2,000 resamples, seed 0). Overlapping policies are never summed.
- Shakedown = scheduled before activation, code/spec hash mismatch, legacy capture (no connection markers or mlb_map), or a model snapshot that is not the pinned one / not logged before the game.
