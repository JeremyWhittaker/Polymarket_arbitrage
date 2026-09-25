# Forward paper-trading engine: design

This engine implements [the MLB protocol](../../reports/PROSPECTIVE_PROTOCOL.md) and [the soccer protocol](../../reports/PROSPECTIVE_SOCCER_PROTOCOL.md). It never sends orders. Every decision is a deterministic function of receipt-timestamped capture files. Replaying the same files gives identical ledgers, so a restart cannot create or lose trades.

## Two processes

1. **Capture** (`pmsports record`, systemd `pmsports-record.service`) writes `data/live/<UTC day>/*.jsonl`. Each line is one JSON object with `recv_ms`, the local receive time in epoch milliseconds, stamped when the message arrived.
2. **Engine** (`pmsports paper run`, systemd `pmsports-paper.service`) tails those files in `recv_ms` order. It maintains books, trades and game state, makes decisions, and writes append-only decision records. A periodic step settles resolved positions and regenerates the report and desk ledgers.

## Capture record formats (contract between the two processes)

Existing streams stay unchanged. The new and extended ones:

| File | Record |
|---|---|
| `clob.jsonl` | Unchanged `{"recv_ms", "msg"}` websocket payloads, plus connection markers `{"recv_ms", "conn": "open"\|"closed"\|"pong", "n_assets": int}`. Ping every 2 seconds and log every PONG as a `pong` marker. On reconnect write `closed` (if known), then `open` after subscribing. |
| `mlb_map.jsonl` | Written at discovery and on each daily rollover: `{"recv_ms", "game_pk", "game_type", "scheduled_innings", "doubleheader", "game_number", "mlb_start_ts", "home_name", "away_name", "pm": {condition_id, slug, home_token, away_token, start_ts, fee_rate, fee_exponent, tick, min_size, seconds_delay}, "match": "exact"\|"ambiguous"\|"unmatched", "reason"}`. |
| `market_meta.jsonl` | Per condition id, whenever discovered or changed (refresh every 10 minutes for live and upcoming games): `{"recv_ms", "condition_id", "tokens": [..], "seconds_delay", "tick", "min_size", "fee_rate", "fee_exponent", "accepting_orders", "closed"}`. Source: CLOB `/markets/<cid>` plus Gamma `feeSchedule`. |
| `soccer_games.jsonl` | Written at discovery: `{"recv_ms", "event_slug", "series", "start_ts", "legs": {"home"\|"draw"\|"away": {condition_id, yes_token, no_token, fee_rate, fee_exponent, tick, min_size, seconds_delay}}, "espn": {path, id, home_id, away_id, home_name, away_name, kickoff_ts} \| null, "match": "exact"\|"ambiguous"\|"unmatched"\|"unmapped_league", "reason"}`. Leg orientation (which Polymarket leg is ESPN home) comes from team matching, never from slug order alone. |
| `espn.jsonl` | Soccer state changes only: `{"recv_ms", "espn_id", "path", "state", "period", "clock_s", "display_clock", "home", "away", "new_events": [{"type", "period", "clock_value", "clock_display", "team_id", "scoring", "text", "fp"}]}`. `fp` is a stable fingerprint of type, period, clock value, team and text, and deduplicates events across polls. |
| `sports.jsonl` | Polymarket sports websocket, now soccer and MLB (secondary feed, not used for decisions). |

Soccer tokens (all three Yes tokens per matched game) join MLB tokens in the one CLOB subscription. Discovery runs every 10 minutes, covering events kicking off within 6 hours or started less than 3 hours ago. ESPN polling shares the existing 5 requests/second host budget:

- scoreboard per active league path every 30 seconds;
- `summary` per game every 5 seconds from minute 60 of period 2 until final;
- the summary interval stretches automatically when many games are active at once.

## Engine

`pmsports/paper/engine.py` provides `Replayer(files, start_ms, end_ms=None, follow=False)`. It yields merged records in `recv_ms` order across all streams and days. Follow mode tails growing files, handles day rollover, and processes only records older than 2 seconds so slower streams can catch up. Reading `.jsonl.gz` is transparent.

`State` holds:

- one `ReceivedBook` (`pmsports/book_replay.py`) plus connection state (last open, last message, snapshot-since-open per token);
- the last trade price, size and time per token;
- market metadata;
- MLB and soccer game state.

Pending entries sit in a heap keyed by eligible time. Before applying any record with `recv_ms` greater than a pending entry's eligible time, the engine executes that entry against the book as received so far.

Book freshness for an entry at time t requires all three:

- a snapshot for the token since the last `open`;
- the last message or `pong` on the connection within 5,000 ms of t;
- no `closed` marker after that message.

Execution calls `ReceivedBook.cross(token, t, policy=<policy id>, buy=True, budget=100, limit=<limit>, fee_rate=<rate>, min_order_shares=<min_size>, stale_ms=<large>)`. Freshness is checked by the engine as above, because a quiet but live book is valid. The limit is rounded down to the market tick.

Strategies (`mlb.py`, `soccer.py`) receive state updates and emit `Signal` objects. Every decision writes one record to `data/paper/decisions.jsonl` (append-only), keyed by `(policy, game_id)`. Rejections are written too. On restart, the engine replays from 36 hours before the last processed record to rebuild state and skips keys already written. Replay determinism is covered by tests.

### MLB policies (per protocol)

- **Signal:** half-inning start from the linescore, as defined in the activation addendum. Only games with an `exact` `mlb_map` match count.
- **Fair value:** `fair_home(inning, half, outs, bases, diff, max_season)` loaded from a frozen JSON snapshot. The snapshot is generated by `pmsports/paper/mlb_model.py` with exactly `_baseline_we`'s hierarchy, 30-observation minimum, shrinkage and 0.5 fallback. Its SHA-256 is stored in the activation manifest. Leader probability is `fair_home` when home leads, else `1 − fair_home`.
- **Reference:** leader token last trade older than or equal to the signal receipt by at most 120 seconds.
- **Triggers:**
  - Primary `mlb_10c`: leader fair minus reference ≥ 0.10.
  - Control `mlb_03c`: ≥ 0.03.
  - Only the first qualifying signal per game per policy.
- **Entry:** eligible at receipt plus `seconds_delay`; limit reference + 0.01.
- **Sensitivities:** `mlb_10c_plus1c` (same fills, price +0.01 on every filled share, recomputed at report time) and `mlb_10c_delay2` (separate policy, eligible 2 seconds later, its own depth consumption).

### Soccer policies (per protocol)

- **Primary** `soccer_added_time`, **controls** `soccer_ctrl_75_85` and `soccer_ctrl_70_80`, **sensitivities** `soccer_delay2`, `soccer_delay10`, `soccer_delay60`, plus +1¢ at report time.
- **Trigger:** the first newly received key event in period 2, in the minute window, with a nonzero lead in the same response.
- **Minute** parsing reuses `true_minute` from `h_soccer_continuation`.
- **Reference:** leader Yes last trade at most 120 seconds old, in 0.60–0.97.
- **Entry:** eligible at receipt plus max(3, `seconds_delay`) (plus the sensitivity's extra delay); limit reference + 0.01.

## Settlement, ledgers and report

`settle.py` polls Gamma for filled markets. It reuses `terminal_payouts` and applies the 0 / 0.5 / 1 validity gate. A position stays open until the market resolves.

`report.py` builds these outputs from `decisions.jsonl` plus settlements:

- `reports/PAPER_TEST.md`: coverage, attempts, fills, no-fill reasons, capital, net cash, ROI with game bootstrap (2,000 resamples, seed 0), equal-game ROI, best-1/best-3/best-1% removal, US-fee scenario, the endpoint countdown and the gate status;
- desk ledgers `data/research/ledgers/paper_<policy>.json` in `group "Forward paper tests"` with `period "prospective_paper"`;
- extra columns `book_age_ms`, `fee_usd_us`, `connection_ok`, `reference_age_ms`.

Only games whose scheduled start is after the activation instant count toward endpoints. Earlier captured games are labelled shakedown.

## Activation manifest

`reports/paper/ACTIVATION.json` records:

- the git commit;
- SHA-256 of every `pmsports/paper/*.py`, `book_replay.py`, and `polymarket.taker_fee` source;
- rule JSON hashes;
- the MLB model snapshot hash;
- the activation UTC instant.

The engine refuses to label decisions primary if its code hash differs from the manifest; a code change requires a new version.

## Implementation notes (v1, before activation)

These refine the design above. Each was adopted during independent review, and each has a test.

**Capture**
- **New tokens:** added to the live CLOB connection without reconnecting. If no snapshot arrives within 30 seconds, the connection is fully reconnected.
- **Unsubscribes:** expired tokens are unsubscribed and logged as `{"conn": "unsub", "assets": [...]}`. Tokens live while their game is live: MLB until the game ends plus 10 minutes (floor of start + 8 hours); soccer until ESPN final plus 10 minutes.
- **MLB polling:** linescores are polled concurrently on a 2-second cycle measured from cycle start. A failing game writes one `{"game_pk", "error"}` record.
- **MLB schedule stream:** `mlb_schedule.jsonl` records every scheduled MLB game and which markets claim it, so games without a market are visible.
- **Frozen mappings:** `mlb_map` freezes at the earliest of the Polymarket start, the MLB start or the game going Live. Soccer matches freeze at kickoff. A game first mapped after its start is written as `unmatched` with that reason.
- **ESPN events:** events are deduplicated by ESPN event id. Edits to already-received events go to `revised_events`, never `new_events`.
- **ESPN polling:** the scoreboard is logged until minute 60 of period 2; after that only full summaries are, so the lead always comes from the same response as the events. A game whose scoreboard has not changed for 2 minutes after kickoff + 75 minutes moves to summary polling.
- **Receipt times:** `recv_ms` never goes backwards within a file.
- **League map:** verified live overrides for the Polymarket series → ESPN league path mapping live in `capture_soccer.py`; the historical map stays untouched.
- **Rotation:** `rotate.py` gzips days no newer than today − 2, verifies line counts before deleting, and never touches 2026-09-18 or 2026-09-19. Those two days are read by frozen research code.

**Engine**
- **Follow watermark:** records are released at the last directory scan minus 2 seconds, so a file created between scans is never skipped. A record arriving behind the watermark is counted as `late`.
- **Admission:** only the latest mapping record received at or before a game's scheduled start admits it. Later records never admit, re-orient or drop a game.
- **Soccer terminal exclusion:** a response with `state == "post"`, period above 2, a final or full-time status, or a new `end-regular-time` / `end-match` event ends trading for that game. This matches the historical rule's exclusion of terminal-whistle rows; these markets settle at the end of regulation.
- **Attempt slot:** a missing or out-of-band reference is logged in `signals.jsonl` and does not use a game's single attempt. A missing leader leg does use it and is recorded as a no-fill. An MLB signal with no model snapshot is logged only.
- **Soccer delays:** `soccer_delay10` and `soccer_delay60` are total delays: receipt + max(10 or 60, `seconds_delay`). `soccer_delay2` adds 2 seconds to the primary's delay.
- **Tolerance:** thresholds and price bands compare with a 1e-9 tolerance.
- **Legacy days:** days without connection markers or `mlb_map` are shakedown-only. They use token-message age for freshness and a legacy mapping.
- **Outputs:** the engine writes `signals.jsonl` (every detected signal and its verdict per policy), `coverage.jsonl` and `checkpoint.json` under `data/paper/`. An exclusive lock on `data/paper/.lock` keeps a second writer out.
- **Model snapshots:** logged in `reports/paper/model_snapshots.jsonl`. A season pinned in the manifest must match its hash exactly.
- **Code hash scope:** the activation code hash covers only decision code: `paper/{__init__,engine,mlb,soccer,spec,mlb_model}.py`, `book_replay.py`, `research/h_soccer_continuation.py` and the `taker_fee` source. Capture, settlement, report and CLI files are hashed separately under `support_files`, so coverage or reporting fixes stay auditable without relabelling decisions.
