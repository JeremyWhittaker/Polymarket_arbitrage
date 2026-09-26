# US-venue paper engine: design

This engine implements [the US venue protocol](../../../reports/PROSPECTIVE_US_VENUE_PROTOCOL.md). It never sends orders; the only exchange call is an order **preview** through `pmsports/paper/us_api.py`. It reuses the frozen v1 engine ([design](../DESIGN.md)) by import and subclassing only. No v1 file is changed.

## Composition

| Piece | Source |
|---|---|
| Receipt-ordered replay, follow mode, restart warmup, decision log, locks | v1 `Replayer`, `Engine`, `JsonlLog`, `out_lock` (`USReplayer` only re-ranks equal `recv_ms`: metadata, then US books and trades, then game state) |
| MLB half-inning signals, model snapshot, 10¢/3¢ thresholds, first signal per game | v1 `MLBStrategy`, unchanged |
| Soccer key-event trigger, windows, 0.60–0.97 band, terminal exclusion | v1 `SoccerStrategy`, unchanged |
| Reference, admission, book, freshness, execution, labels | `us/venue.py`, `us/engine.py` |

The frozen strategies call `engine.state.reference(token, t)`. In the US engine that call is answered by the US venue:

1. The international token names a game side, from the pregame `mlb_map` / `soccer_games` record.
2. The pregame `us_map` names that side's US market and orientation.
3. The answer is the last US trade on that side.

The strategies' outputs use v1 policy names. They are renamed to the US policies (`spec.TRIGGERS`), and the international market and token are kept as `intl_*` audit fields.

## Inputs

These are the capture files in `data/live/<UTC day>/`. See `pmsports/paper/capture_us.py` for the writer.

- **`us_map`:** admission uses the latest record per `key` received at or before its `start_ts`, and the record must be `exact`. The international game is found through `intl_key`: `game_pk`, `condition_id` and `event_slug` for MLB, and `event_slug` for soccer.

  A record is also late, whatever its `start_ts`, once the international game it names has been seen in play. This covers a game that starts before its listing says, such as a doubleheader game 2:
  - **MLB:** the v1 strategy has labelled a half-inning of the game. This is v1's own `mlb_map` guard (`mlb.py`), applied to `us_map`. A warm-up linescore (inning 1, top, 0 outs, about 30 minutes before the start) is not a signal and does not count.
  - **Soccer:** an ESPN response for the game reports state `in` or `post`. A `pre` response does not count.

  Each game's first in-play time is written to `coverage.jsonl` as `inplay|<kind>|<id>`, so the report's coverage and endpoint count apply the same rule.

  The following cases are logged with the signal and use no attempt:
  - several US events claim the same game;
  - a later pregame `no_us_market` record;
  - no pregame record at all.

  Orientation:
  - **MLB:** one instrument, `markets = {slug, long_team, ...}`. The long team buys long; the other team buys short.
  - **Soccer:** `markets = {home|draw|away: {slug, ...}}`, and each `atc-` market is bought long.

  The venue fields (`tick`, `min_qty`, `qty_increment`, `fee_coefficient`) follow the latest record of any time, as v1 market rules do. Admission and orientation never change after the start.
- **`us_book`:** MARKET_DATA full long-side snapshots, plus connection markers `open`, `sub`/`unsub`, `heartbeat` and `closed`.
- **`us_trade`:** TRADE messages.

## Execution

- **Entry time:** receipt plus `min_delay_s + extra_delay_s`. That is 0 s for MLB, 3 s for soccer, plus 2 s for the delay sensitivities, and 10 s or 60 s total for the longer soccer delays. The US venue publishes no matching delay.
- **Limit:** reference + 0.01, rounded down to the market tick, in the bought side's price. The API price for BUY_SHORT is 1 − limit, because US order prices are always long-side prices.
- **Quantity:** the largest multiple of `qty_increment` whose cost at the limit, including the fee `c × q × p × (1 − p)`, is at most $100. The increment defaults to one contract when the record has none; the capture sets it to `minimumTradeQty` (0.01). Because price plus fee rises with price, no fill at or below the limit can exceed the budget.
- **Book:** one IOC against the received book. Each market holds two crossing views in one v1 `ReceivedBook`:
  - `slug|long` has asks equal to the offers;
  - `slug|short` has asks at 1 − long bid.

  Each policy consumes a level once. Snapshots are applied lazily, taking the latest one before an execution.
- **Freshness:**
  - a book message for the market in [t − 5 s, t], after the connection's last `open`, with no `closed` marker at or after it;
  - a market `state` other than `MARKET_STATE_OPEN` is a recorded no-fill.

  A day without markers gives `connection_ok = "unknown"`, which is shakedown only.

## Outputs

These live in `data/paper_us/`: `decisions.jsonl`, `signals.jsonl`, `coverage.jsonl`, `checkpoint.json`, `settlements.json` and `previews.jsonl`.

- **Report:** `reports/PAPER_TEST_US.md`. Follow mode keeps a live copy in `data/paper_us/PAPER_TEST.md`.
- **Desk ledgers:** `data/research/ledgers/paper_us_<policy>.json`, in group "Forward paper tests (US venue)".

### Preview audit

- **When:** follow mode only. It covers each newly written executed entry within 15 s of the wall clock.
- **How:** on a background thread, at most 2 requests per second.
- **Logged to** `previews.jsonl`: status, lag, and the order state, quantities and commission from the response.
- **Status:**
  - `accepted`: HTTP 2xx and the order is not rejected.
  - `rejected`: only when the exchange refused the order itself, by an order state containing REJECT or by a validation-type 4xx with a JSON body.
  - `error`: no response; HTTP 401, 403, 407, 408 or 429 (signature, time window, edge block, timeout, rate limit); 5xx; or a body that is not a JSON object. These say nothing about the order.

  The report's acceptance rate is taken over the previews the exchange answered. Errors are counted separately, by HTTP status.
- **Isolation:** decisions are byte-identical with or without it. A restart never previews again. Older entries are `not_previewed`.
- **What a preview shows:** on 2026-09-26 the preview echoed the order as `ORDER_STATE_PENDING_NEW` with zero fills and commission. It validates an order; it does not estimate fills.
- **Quantity:** `us_api.preview_buy` sends an integer quantity, so a fractional simulated quantity is previewed truncated and flagged.

### Settlement

- **Primary source:** `GET gateway.polymarket.us/v1/markets/<slug>/settlement` returns `{"slug", "settlement": x}`, where x is the long side's price. It was verified against three resolved MLB games. A short position receives 1 − x. Only this settles a position.
- **Provisional fallback:** while the US settlement is unpublished, `settlements.json` records the international resolution of the same team as `fallback_intl`. The report keeps such a position **open** in every statistic, the endpoint and the gate. Its provisional cash appears only in the "Provisional settlements (not counted)" table. The US settlement replaces the fallback once it publishes.
- **Polling:** each unresolved slug keeps `first_polled_ts`, `last_polled_ts` and `n_polls`, with status `pending`. A pass polls the due slugs least recently polled first, so a pass cut short by HTTP 429 cannot starve the same slugs next time. The cache is written at the end of every pass.
  - For 12 h after the first poll, a slug is polled every pass.
  - From 12 h to 3 days, it is polled hourly.
  - From 3 to 30 days, it is polled daily.
  - After 30 days it is flagged `unpublished`. From then on only a filled position's market is polled, weekly.

## Operation

`python -m pmsports paper-us run --follow` is the service entry point. In follow mode:

- The periodic settle and report runs every 15 minutes on a side thread, so the engine never waits for the network. Settlement polling paces requests at 0.5 s and stops a pass on HTTP 429, because the gateway's edge throttles bursts well below 20 requests per second. It polls a filled market only 2 h after its game's start, and backs off as described under Settlement.
- SIGTERM exits cleanly: logs are closed, a checkpoint is written and queued previews are drained for up to 30 s.
- `paper-us settle` and `paper-us report` can also be run by hand. `settle` needs the output lock, so it cannot run while the service is running.

## Engineering data seen before activation

The protocol's mapping addendum originally said the scratch US captures "will not be analysed". That was inaccurate, and it was corrected before activation. Before activation, the engine shakedown ran on the scratch capture of 2026-09-26 03:00–03:40 UTC (outside `data/live`). It produced these engineering measurements:

- **Stale books:** on five live MLB markets, the book was more than 5 s old for 0.2–1.4% of the time.
- **Signals:** the replay logged 548 signals. Against a US reference, the largest leader edge was 0.048, below the 10¢ primary threshold.
- **Fills:** one 3¢-control entry was simulated, `mlb_03c_us|824947`: 106.47 contracts at 0.925, labelled shakedown.

These measurements checked the code: freshness accounting, orientation and fills. No rule, threshold, delay or freshness setting was changed after they were seen. The rules, thresholds and settings are the protocol's, written before any US book or trade was captured. The only later rule text is the mapping addendum, which uses listings only. The shakedown decisions are not evidence and are not carried into `data/paper_us`.

## Activation and labels

`python -m pmsports paper-us activate` writes `reports/paper/ACTIVATION_US.json`. The code hash covers:

- `us/{__init__,spec,venue,engine}.py`;
- `us_api.py`;
- every v1 decision file;
- the `taker_fee` source.

`preview`, `settle`, `report`, `run`, `capture_us` and `record` are hashed separately under `support_files`, as v1 does.

A decision is shakedown when any of these holds:

- the US engine is not activated;
- a hash does not match;
- the game was scheduled before activation;
- the mapping is legacy;
- connection markers are missing;
- the MLB model snapshot is not the pinned one.
