"""Live recorder: the data that does not exist historically.

Polymarket keeps no public order-book history and trade timestamps are 1-second
block times, so the questions "what price could I actually have gotten?" and
"who sees the play first?" need a live capture. Every record is stamped with
local receive time (recv_ms, NTP-synced clock assumed; never decreasing per file):

  clob.jsonl         CLOB market websocket: full books, price_change (with best
                     bid/ask), last_trade_price for every live/upcoming MLB game and
                     matched soccer game, plus connection markers open/closed/pong
                     (PING every 2 s) and unsub (tokens dropped from the live connection)
  sports.jsonl       Polymarket's own sports websocket (MLB and soccer)
  mlb.jsonl          MLB Stats API linescore of every live game, all polled concurrently
                     every 2 s; state changes only, plus {"game_pk", "error"} when a game's
                     polling starts failing (its next good state is always written)
  games.jsonl        Polymarket MLB game metadata (discovery and daily rollover)
  mlb_map.jsonl      Polymarket MLB game -> MLB game_pk (exact / ambiguous / unmatched),
                     frozen at first pitch; a market first mapped after it is unmatched
  mlb_schedule.jsonl every MLB game before its start, with the markets claiming it, on
                     change; a game whose last row before its start has none had no market
  market_meta.jsonl  per market: CLOB tick, minimum size, delay + Gamma fee, on change
  soccer_games.jsonl, espn.jsonl   soccer discovery and ESPN state (paper/capture_soccer.py)

Record formats: pmsports/paper/DESIGN.md. Files land in data/live/<UTC date>/.
Run under tmux/systemd for a whole slate:
  python -m pmsports record --hours 6
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import websockets

from . import polymarket as pm
from .collect import DATA_DIR, _team_eq
from .http import get_json
from .paper import capture_soccer
from .paper.rotate import open_capture

log = logging.getLogger("pmsports")
CLOB_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
SPORTS_WS = "wss://sports-api.polymarket.com/ws"
MLB_API = "https://statsapi.mlb.com/api/v1"
PING_S = 2.0            # CLOB heartbeat; every PONG is logged as a freshness marker
DEAD_S = 15.0           # no message and no PONG for this long -> reconnect
SUB_CHECK_S = 30.0      # a live subscribe that yields no book snapshot at all -> reconnect
UNSUB_EVERY_S = 600.0
RECONNECT_S = 3.0
SPORTS_DEAD_S = 60.0    # sports feed: nothing at all, not even a server `ping`, for this long -> reconnect
MLB_TOKEN_TTL_S = 8 * 3600
MLB_POLL_S = 2.0        # linescore cadence per live game (activation addendum)
MLB_TIMEOUT_S = 3.0     # one linescore request, no retry: a slow game never holds up the others
MLB_WORKERS = 24        # >= live games (15) plus delayed starts; the statsapi bucket (8/s) paces them
MLB_LIVE_EVERY_S = 60.0  # light schedule refresh of the polled set (delayed starts turn Live in between)
MLB_LIVE_GRACE_S = 600.0  # a live game's tokens stay subscribed until 10 min after it was last seen live
_warned: dict = {}


def _warn(key, msg: str, every_s: float = 300.0) -> None:
    now = time.time()
    if now - _warned.get(key, 0.0) >= every_s:
        if len(_warned) > 5000:
            _warned.clear()
        _warned[key] = now
        log.warning(msg)


class _Sink:
    """Append-only data/live/<UTC day>/<name>.jsonl. Thread-safe; recv_ms never decreases."""

    def __init__(self, name: str, root: Path | None = None):
        self.name = name
        self.root = root
        self.fh = None
        self.day = None
        self.last_ms = 0
        self.lock = threading.Lock()

    def write(self, obj, recv_ms: int | None = None) -> None:
        with self.lock:
            ms = max(int(time.time() * 1000) if recv_ms is None else int(recv_ms), self.last_ms)
            day = time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))
            if day != self.day:
                if self.fh:
                    self.fh.close()
                d = (self.root or DATA_DIR / "live") / day
                d.mkdir(parents=True, exist_ok=True)
                self.fh = open(d / f"{self.name}.jsonl", "a", buffering=1)
                self.day = day
            self.fh.write(json.dumps({"recv_ms": ms, **obj}, separators=(",", ":")) + "\n")
            self.last_ms = ms


class MarketMeta:
    """market_meta.jsonl: CLOB /markets/<cid> plus the Gamma fee schedule, written on change."""

    def __init__(self, sink, get=get_json):
        self.sink, self.get = sink, get
        self.last: dict[str, tuple] = {}
        self.lock = threading.Lock()

    def update(self, cid: str, sport: str, fee_rate, fee_exponent) -> dict | None:
        """Fetch, write if any field changed, return the record (last known one on failure)."""
        try:
            m = self.get(f"{pm.CLOB}/markets/{cid}", retries=2, timeout=10)
            rec = {"condition_id": cid, "tokens": [str(t.get("token_id")) for t in m.get("tokens") or []],
                   "seconds_delay": m.get("seconds_delay"), "tick": m.get("minimum_tick_size"),
                   "min_size": m.get("minimum_order_size"), "fee_rate": fee_rate, "fee_exponent": fee_exponent,
                   "accepting_orders": m.get("accepting_orders"), "closed": m.get("closed"), "sport": sport}
        except Exception as exc:
            log.warning("clob market %s: %s", cid, exc)
            return (self.last.get(cid) or (None, None, 0))[1]
        key = json.dumps(rec, sort_keys=True)
        now = time.time()
        with self.lock:
            prev = self.last.get(cid)
            self.last[cid] = (key, rec, now)
            if prev is None or prev[0] != key:
                self.sink.write(rec)
            if len(self.last) > 2000:
                for k in [k for k, v in self.last.items() if now - v[2] > 86400]:
                    del self.last[k]
        return rec


def _slate(window_h: float) -> list[dict]:
    """Open MLB games that started < 5h ago or start within window_h."""
    now = time.time()
    out = []
    for e in pm.iter_events(pm.SERIES["mlb"], closed=False):
        g = pm.parse_game_event(e)
        if g and g["start_ts"] and -5 * 3600 < g["start_ts"] - now < window_h * 3600:
            out.append(g)
    return out


async def _clob(games_ref: dict, sink: _Sink, stop: float) -> None:
    """One market websocket for every captured token.

    Markers bracket each connection: `open` once subscribed, `pong` for every PING reply,
    `closed` when it ends for any reason. Tokens added later are subscribed on the live
    connection (no book gap for the others); if such a subscribe yields no snapshot at all
    the connection is rebuilt. Expired tokens (their games are over, see retoken) are
    unsubscribed every 10 minutes and logged as an `unsub` marker listing them: from then on
    their books are frozen on this connection.
    """
    while time.time() < stop:
        tokens = sorted(games_ref["tokens"])
        if not tokens:
            await asyncio.sleep(5)
            continue
        opened = False
        try:
            async with websockets.connect(CLOB_WS, max_size=2**24, ping_interval=None) as ws:
                await ws.send(json.dumps({"assets_ids": tokens, "type": "market",
                                          "custom_feature_enabled": True}))
                subscribed = set(tokens)
                sink.write({"conn": "open", "n_assets": len(subscribed)})
                opened = True
                next_ping = last_rx = last_unsub = time.time()
                batches: list[tuple[float, set]] = []      # live subscribes awaiting a snapshot
                while time.time() < stop:
                    now = time.time()
                    want = games_ref["tokens"]
                    add = want - subscribed
                    if add:
                        await ws.send(json.dumps({"assets_ids": sorted(add), "operation": "subscribe",
                                                  "custom_feature_enabled": True}))
                        subscribed |= add
                        batches.append((now, set(add)))
                        log.info("clob ws: subscribed %d more tokens (%d total)", len(add), len(subscribed))
                    if batches and now - batches[0][0] > SUB_CHECK_S:
                        raise ConnectionError(f"no snapshot for {len(batches[0][1])} newly subscribed tokens")
                    drop = subscribed - want
                    if drop and now - last_unsub > UNSUB_EVERY_S:
                        await ws.send(json.dumps({"assets_ids": sorted(drop), "operation": "unsubscribe"}))
                        subscribed -= drop
                        last_unsub = now
                        sink.write({"conn": "unsub", "assets": sorted(drop), "n_assets": len(subscribed)})
                    if now >= next_ping:
                        await ws.send("PING")
                        next_ping = now + PING_S
                    if now - last_rx > DEAD_S:
                        raise ConnectionError(f"no message or PONG for {DEAD_S:.0f}s")
                    try:
                        msg = await asyncio.wait_for(ws.recv(), max(0.05, next_ping - time.time()))
                    except asyncio.TimeoutError:
                        continue
                    last_rx = time.time()
                    if msg == "PONG":
                        sink.write({"conn": "pong"})
                        continue
                    try:
                        data = json.loads(msg)
                    except ValueError:
                        log.warning("clob ws: non-JSON message %.200r", msg)
                        continue
                    sink.write({"msg": data})
                    if batches:
                        books = {str(m.get("asset_id")) for m in (data if isinstance(data, list) else [data])
                                 if isinstance(m, dict) and m.get("event_type") == "book"}
                        batches = [b for b in batches if not (b[1] & books)]
        except Exception as exc:
            log.warning("clob ws: %s; reconnecting", exc)
        finally:
            if opened:
                try:
                    sink.write({"conn": "closed"})
                except Exception as exc:
                    log.warning("clob ws closed marker: %s", exc)
        if time.time() < stop:
            await asyncio.sleep(RECONNECT_S)


def _keep_sports(m, games_ref: dict) -> bool:
    """MLB and soccer (leagues/games found by soccer discovery) messages of the sports feed."""
    if not isinstance(m, dict):
        return False
    lg = str(m.get("leagueAbbreviation", "")).lower()
    return lg == "mlb" or lg in (games_ref.get("soccer_leagues") or ()) or \
        m.get("gameId") in (games_ref.get("soccer_game_ids") or {})


async def _sports(sink: _Sink, stop: float, games_ref: dict | None = None) -> None:
    """Secondary score feed. A connection that delivers nothing (not even the server's own
    `ping`) for SPORTS_DEAD_S is treated as dead and rebuilt."""
    games_ref = {} if games_ref is None else games_ref
    while time.time() < stop:
        try:
            async with websockets.connect(SPORTS_WS, ping_interval=None) as ws:
                last_rx = time.time()
                while time.time() < stop:
                    if time.time() - last_rx > SPORTS_DEAD_S:
                        raise ConnectionError(f"nothing received for {SPORTS_DEAD_S:.0f}s")
                    try:
                        msg = await asyncio.wait_for(ws.recv(), max(0.05, last_rx + SPORTS_DEAD_S - time.time()))
                    except asyncio.TimeoutError:
                        continue
                    last_rx = time.time()
                    if msg == "ping":
                        await ws.send("pong")
                        continue
                    try:
                        m = json.loads(msg)
                    except ValueError:
                        continue
                    if _keep_sports(m, games_ref):
                        sink.write({"msg": m})
        except Exception as exc:
            log.warning("sports ws: %s; reconnecting", exc)
        if time.time() < stop:
            await asyncio.sleep(RECONNECT_S)


def live_pks(sched: dict, now: float) -> set:
    """Games to poll: Live (warm-up, in progress, delayed, suspended), or still Preview after
    their scheduled start ('Delayed Start: ...' is Preview until the first pitch)."""
    out = set()
    for d in sched.get("dates", []):
        for g in d.get("games", []):
            st = (g.get("status") or {}).get("abstractGameState")
            ts = pm.parse_ts(g.get("gameDate"))
            if g.get("gamePk") is not None and (st == "Live" or (st == "Preview" and ts is not None and ts <= now)):
                out.add(g["gamePk"])
    return out


def _extend_live_tokens(games_ref: dict, now: float) -> None:
    """Keep the tokens of every polled game subscribed while it is live, then retoken."""
    toks = games_ref.setdefault("mlb_tokens", {})
    by_pk = games_ref.get("mlb_pk_tokens") or {}
    for pk in games_ref.get("game_pks") or ():
        for t in (by_pk.get(pk) or ((), 0.0))[0]:
            toks[t] = max(toks.get(t, 0.0), now + MLB_LIVE_GRACE_S)
    capture_soccer.retoken(games_ref, now)


def _linescore_state(pk, ls: dict) -> dict:
    t = ls.get("teams") or {}
    return {"game_pk": pk, "inning": ls.get("currentInning"), "half": ls.get("inningHalf"), "outs": ls.get("outs"),
            "away": (t.get("away") or {}).get("runs"), "home": (t.get("home") or {}).get("runs"),
            "offense": sorted(k for k in (ls.get("offense") or {}) if k in ("first", "second", "third"))}


async def _mlb(games_ref: dict, sink: _Sink, stop: float, every_s: float = MLB_POLL_S, get=get_json,
               live_every_s: float | None = MLB_LIVE_EVERY_S) -> None:
    """Linescore of every polled game every `every_s`, all games concurrently.

    Each cycle starts every game whose previous request has finished, so one slow or failing
    game never delays the others. A state is stamped when its response arrived and written
    only when it changed. When a game's polling starts failing a {"game_pk", "error"} marker
    is written (and a rate-limited warning logged); its next good state is always written.
    The polled set (live_pks) is refreshed from a light schedule call every `live_every_s`.
    """
    loop = asyncio.get_running_loop()
    pool = ThreadPoolExecutor(MLB_WORKERS, thread_name_prefix="mlb")
    last: dict = {}
    failing: dict = {}
    inflight: set = set()
    tasks: set = set()

    def fetch(pk):
        ls = get(f"{MLB_API}/game/{pk}/linescore", retries=1, timeout=MLB_TIMEOUT_S)
        return int(time.time() * 1000), ls

    async def poll(pk):
        try:
            try:
                ms, ls = await loop.run_in_executor(pool, fetch, pk)
                state = _linescore_state(pk, ls)
            except Exception as exc:
                err = f"{type(exc).__name__}: {exc}"[:200]
                if pk not in failing:
                    failing[pk] = time.time()
                    last.pop(pk, None)
                    sink.write({"game_pk": pk, "error": err})
                _warn(("linescore", pk), f"linescore {pk} failing for {time.time() - failing[pk]:.0f}s: {err}")
                return
            if failing.pop(pk, None) is not None:
                log.info("linescore %s recovered", pk)
            key = json.dumps(state, sort_keys=True)
            if last.get(pk) != key:          # log changes only
                last[pk] = key
                sink.write(state, recv_ms=ms)
        finally:
            inflight.discard(pk)

    async def refresh():
        try:
            today = datetime.now(timezone.utc).date()
            sched = await loop.run_in_executor(pool, lambda: get(
                f"{MLB_API}/schedule", {"sportId": 1, "startDate": (today - pd.Timedelta(days=1)).isoformat(),
                                        "endDate": (today + pd.Timedelta(days=1)).isoformat()},
                retries=2, timeout=10))
            now = time.time()
            games_ref["game_pks"] = live_pks(sched, now)
            _extend_live_tokens(games_ref, now)
        except Exception as exc:
            _warn("mlb-live", f"mlb live-game refresh: {exc}")
        finally:
            inflight.discard("live")

    def launch(key, coro):
        inflight.add(key)
        t = asyncio.create_task(coro)
        tasks.add(t)
        t.add_done_callback(tasks.discard)

    next_t = next_live = time.time()
    try:
        while (now := time.time()) < stop:
            if live_every_s is not None and now >= next_live and "live" not in inflight:
                next_live = now + live_every_s
                launch("live", refresh())
            for pk in sorted(set(games_ref.get("game_pks") or ()) - inflight, key=str):
                launch(pk, poll(pk))
            if len(last) > 500:              # bounded memory; live games keep their last state
                for pk in [pk for pk in last if pk not in games_ref["game_pks"]]:
                    del last[pk]
            for pk in [pk for pk in failing if pk not in games_ref["game_pks"]]:
                del failing[pk]
            next_t = max(next_t + every_s, now)
            await asyncio.sleep(max(0.0, min(next_t, stop) - time.time()))
    finally:
        for t in list(tasks):
            t.cancel()
        pool.shutdown(wait=False, cancel_futures=True)


# ----------------------------------------------------------------------------- MLB game mapping

def _sched_rows(sched: dict) -> list[dict]:
    """MLB schedule response (hydrate=team,linescore) -> one dict per game."""
    rows = []
    for d in sched.get("dates", []):
        for g in d.get("games", []):
            t = g.get("teams") or {}
            h, a = (t.get("home") or {}).get("team") or {}, (t.get("away") or {}).get("team") or {}
            st = g.get("status") or {}
            rows.append({"game_pk": g.get("gamePk"), "official_date": g.get("officialDate") or d.get("date"),
                         "game_ts": pm.parse_ts(g.get("gameDate")), "game_type": g.get("gameType"),
                         "doubleheader": g.get("doubleHeader"), "game_number": g.get("gameNumber"),
                         "scheduled_innings": (g.get("linescore") or {}).get("scheduledInnings", 9),
                         "home_name": h.get("name"), "away_name": a.get("name"),
                         "home_key": h.get("teamName") or h.get("name") or "",
                         "away_key": a.get("teamName") or a.get("name") or "",
                         "state": st.get("abstractGameState"), "detailed_state": st.get("detailedState")})
    return rows


def _pick_doubleheader(same: list[tuple], s: float | None) -> tuple[tuple | None, str]:
    """Choose among same-day games of the same two teams by start time, or explain why not."""
    if s is None or len(same) != 2 or any(r["game_ts"] is None for r, _ in same):
        return None, f"{len(same)} same-day games, cannot choose by start time"
    (r1, sw1), (r2, sw2) = sorted(same, key=lambda x: (x[0]["game_number"] or 0, x[0]["game_ts"]))
    d1, d2 = abs(s - r1["game_ts"]), abs(s - r2["game_ts"])
    near, far = sorted((d1, d2))
    if near <= 45 * 60 and far - near >= 90 * 60:
        return ((r1, sw1) if d1 < d2 else (r2, sw2)), f"doubleheader: nearest start ({near / 60:.0f} min)"
    if r1["doubleheader"] == "Y":        # traditional: game 2 follows game 1, its listed time is a placeholder
        if d1 <= 45 * 60:
            return (r1, sw1), "traditional doubleheader: start matches game 1"
        if s - r1["game_ts"] >= 2.5 * 3600:
            return (r2, sw2), "traditional doubleheader: starts >= 2.5 h after game 1"
    return None, (f"doubleheader: Polymarket start {s:.0f} vs MLB starts "
                  f"{r1['game_ts']:.0f}/{r2['game_ts']:.0f} is ambiguous")


def map_mlb_game(g: dict, rows: list[dict], meta: dict | None = None) -> dict:
    """mlb_map record: Polymarket game -> MLB game on the same date with both teams, nearest start."""
    meta = meta or {}
    p = {"condition_id": g.get("condition_id"), "slug": g.get("slug"), "home_token": g.get("home_token"),
         "away_token": g.get("away_token"), "start_ts": g.get("start_ts"), "fee_rate": g.get("fee_rate"),
         "fee_exponent": g.get("fee_exponent"), "tick": meta.get("tick"), "min_size": meta.get("min_size"),
         "seconds_delay": meta.get("seconds_delay")}
    rec = {"game_pk": None, "game_type": None, "scheduled_innings": None, "doubleheader": None,
           "game_number": None, "mlb_start_ts": None, "home_name": g.get("home_team"),
           "away_name": g.get("away_team"), "pm": p, "match": "unmatched", "reason": ""}
    date, s = str(g.get("event_date") or "")[:10], g.get("start_ts")
    teams = []
    for r in rows:
        if _team_eq(r["home_key"], g.get("home_team") or "") and _team_eq(r["away_key"], g.get("away_team") or ""):
            teams.append((r, False))
        elif _team_eq(r["home_key"], g.get("away_team") or "") and _team_eq(r["away_key"], g.get("home_team") or ""):
            teams.append((r, True))
    same = [x for x in teams if x[0]["official_date"] == date]
    if not same:
        # a game moved to another date keeps its teams and start time; series games are ~24 h apart
        near = [x for x in teams if s and x[0]["game_ts"] and abs(s - x[0]["game_ts"]) <= 45 * 60]
        if len(near) != 1:
            rec["reason"] = f"no MLB game between these teams on {date or '?'}" + \
                (f" ({len(near)} within 45 min of start on other dates)" if near else "")
            return rec
        same = near
        date_note = f"official date {near[0][0]['official_date']} != event date {date}; "
    else:
        date_note = ""
    if len(same) == 1:
        pick = same[0]
        dt = abs(s - pick[0]["game_ts"]) if s and pick[0]["game_ts"] else None
        why = "single same-day game" + (f", start differs by {dt / 60:.0f} min" if dt is not None else "")
        if dt is None or dt > 6 * 3600:
            rec.update(match="ambiguous", reason=why + " (unknown or > 6 h)")
            return rec
    else:
        pick, why = _pick_doubleheader(same, s)
        if pick is None:
            rec.update(match="ambiguous", reason=why)
            return rec
    r, swapped = pick
    why = date_note + why
    if swapped:          # MLB is ground truth for home/away; token identity comes from outcome names
        p["home_token"], p["away_token"] = p["away_token"], p["home_token"]
        why += "; Polymarket home/away swapped to MLB orientation"
    rec.update(game_pk=r["game_pk"], game_type=r["game_type"], scheduled_innings=r["scheduled_innings"],
               doubleheader=r["doubleheader"], game_number=r["game_number"], mlb_start_ts=r["game_ts"],
               home_name=r["home_name"], away_name=r["away_name"], match="exact", reason=why)
    return rec


VENUE_KEYS = ("fee_rate", "fee_exponent", "tick", "min_size", "seconds_delay")
FIRST_AFTER_START = "first mapped after start"


def _start(rec: dict) -> float | None:
    """Earliest known start of an mlb_map record: Polymarket start or MLB start."""
    ts = [x for x in ((rec.get("pm") or {}).get("start_ts"), rec.get("mlb_start_ts")) if x]
    return min(ts) if ts else None


def _frozen(pre: dict, fresh: dict) -> dict:
    """The pre-start mapping with the current venue fields (fee, tick, size, delay)."""
    rec = copy.deepcopy(pre)
    for k in VENUE_KEYS:
        if fresh["pm"].get(k) is not None:
            rec["pm"][k] = fresh["pm"][k]
    return rec


def _map_slate(games: list[dict], sched: dict, mlb_map, market_meta, rollover: bool, state: dict,
               now: float | None = None, sched_sink=None) -> list[dict]:
    """market_meta + mlb_map (+ mlb_schedule) for the slate (blocking; runs in a thread).

    A market's mapping (game_pk, match, MLB start, orientation) is frozen at its start: the
    earliest of the Polymarket and MLB starts, or the MLB game turning Live. Afterwards only
    venue fields follow market_meta. A market first mapped at or after its start is written as
    `unmatched` ("first mapped after start"). Records are written on change or rollover.
    `state` persists between calls: "last" (cid -> written key), "pre" (cid -> latest record
    written before start; see seed_mlb_map), "sched" (game_pk -> written key).
    """
    t0, m0 = (time.time() if now is None else now), time.monotonic()
    last, pre = state.setdefault("last", {}), state.setdefault("pre", {})
    rows = _sched_rows(sched)
    live = {r["game_pk"] for r in rows if r["state"] == "Live"}
    fresh = []
    for g in games:
        try:
            meta = market_meta.update(g["condition_id"], "mlb", g.get("fee_rate"), g.get("fee_exponent")) \
                if market_meta is not None and g.get("condition_id") else None
            fresh.append((g, meta, map_mlb_game(g, rows, meta)))
        except Exception as exc:
            log.warning("mlb_map %s: %s", g.get("slug"), exc)

    def started(x, t):
        return x is not None and ((_start(x) or float("inf")) <= t or x.get("game_pk") in live)
    recs = []                              # (record, frozen)
    for g, meta, r in fresh:
        t = t0 + time.monotonic() - m0
        p = pre.get(r["pm"]["condition_id"])
        if started(p, t) or (p is not None and started(r, t)):
            recs.append((_frozen(p, r), True))
        elif started(r, t):               # first mapping only now: it would use post-start information
            late = map_mlb_game(g, [], meta)
            late["reason"] = FIRST_AFTER_START
            recs.append((late, True))
        else:
            recs.append((r, False))
    by_pk: dict = {}
    for r, fz in recs:
        if r["match"] == "exact":
            by_pk.setdefault(r["game_pk"], []).append((r, fz))
    for pk, rs in by_pk.items():
        if len(rs) > 1:                   # one MLB game, one Polymarket market; frozen records keep theirs
            held = any(fz for _, fz in rs)
            for r, fz in rs:
                if not fz:
                    r.update(match="ambiguous", reason=f"game_pk {pk} claimed by {len(rs)} Polymarket events")
                    if held:              # do not unseat the market admitted before the start
                        r["game_pk"] = None
    for r, fz in recs:
        key = json.dumps(r, sort_keys=True)
        cid = r["pm"]["condition_id"]
        if mlb_map is not None and (rollover or last.get(cid) != key):
            mlb_map.write(r)
        last[cid] = key
        if not fz:
            pre[cid] = copy.deepcopy(r)
    out = [r for r, _ in recs]
    if len(last) > 500:
        keep = {r["pm"]["condition_id"] for r in out}
        for k in [k for k in last if k not in keep]:
            del last[k]
    for k in [k for k, r in pre.items() if (_start(r) or t0) < t0 - 2 * 86400]:
        del pre[k]
    if sched_sink is not None:
        _write_schedule(rows, out, sched_sink, rollover, state.setdefault("sched", {}), t0 + time.monotonic() - m0)
    return out


def _write_schedule(rows: list[dict], recs: list[dict], sink, rollover: bool, last: dict, now: float) -> None:
    """mlb_schedule.jsonl: each MLB game not yet started, with the markets whose mapping names it.

    Claims only cover markets in the slate window, so a game far ahead first shows none; its
    last row before its start is the coverage answer (empty = no Polymarket market)."""
    claims: dict = {}
    for r in recs:
        if r.get("game_pk") is not None:
            claims.setdefault(r["game_pk"], []).append(r["pm"]["condition_id"])
    for r in rows:
        if r["game_pk"] is None or r["game_ts"] is None or r["game_ts"] <= now or r["state"] != "Preview":
            continue
        rec = {k: r[k] for k in ("game_pk", "official_date", "game_ts", "game_type", "doubleheader", "game_number",
                                 "home_name", "away_name", "state", "detailed_state")}
        rec["condition_ids"] = sorted(str(c) for c in claims.get(r["game_pk"], []))
        key = json.dumps(rec, sort_keys=True)
        if rollover or last.get(r["game_pk"]) != key:
            sink.write(rec)
            last[r["game_pk"]] = key
    if len(last) > 2000:
        keep = {r["game_pk"] for r in rows}
        for k in [k for k in last if k not in keep]:
            del last[k]


def seed_mlb_map(state: dict, root: Path | None = None, now: float | None = None) -> None:
    """Restore each market's pre-start mapping from today's and yesterday's mlb_map.jsonl, so a
    restart keeps frozen mappings instead of re-mapping started games. As in _map_slate, a
    record counts only if it arrived before the start of the mapping kept so far and its own."""
    root = root or DATA_DIR / "live"
    now = time.time() if now is None else now
    pre = state.setdefault("pre", {})
    for day in [time.strftime("%Y-%m-%d", time.gmtime(now - d * 86400)) for d in (1, 0)]:
        for r in capture_soccer._read_jsonl(root / day / "mlb_map.jsonl"):
            cid = (r.get("pm") or {}).get("condition_id")
            s = [x for x in (_start(r), _start(pre[cid]) if cid in pre else None) if x]
            if cid and s and int(r.get("recv_ms") or 0) < min(s) * 1000:
                pre[cid] = {k: v for k, v in r.items() if k != "recv_ms"}


async def _refresh(games_ref: dict, window_h: float, stop: float, meta: _Sink,
                   mlb_map: _Sink | None = None, market_meta: MarketMeta | None = None,
                   sched_sink: _Sink | None = None, map_state: dict | None = None) -> None:
    last_day = None
    map_state = {} if map_state is None else map_state
    while (now := time.time()) < stop:
        try:
            today = datetime.now(timezone.utc).date()
            day_str = today.strftime("%Y-%m-%d")
            rollover = (day_str != last_day)

            games = await asyncio.to_thread(_slate, window_h)
            sched = await asyncio.to_thread(get_json, f"{MLB_API}/schedule",
                                            {"sportId": 1, "startDate": (today - pd.Timedelta(days=1)).isoformat(),
                                             "endDate": (today + pd.Timedelta(days=1)).isoformat(),
                                             "hydrate": "team,linescore"})
            toks = games_ref.setdefault("mlb_tokens", {})
            new = {t for g in games for t in (g["home_token"], g["away_token"])} - games_ref["tokens"]
            for g in games:
                exp = (g.get("start_ts") or now) + MLB_TOKEN_TTL_S
                for t in (g["home_token"], g["away_token"]):
                    toks[t] = max(toks.get(t, 0.0), exp)
            games_ref["game_pks"] = live_pks(sched, now)              # _mlb refreshes it every minute too
            _extend_live_tokens(games_ref, now)
            for g in games:
                if rollover or g["home_token"] in new:
                    meta.write({"game": g})
            if mlb_map is not None or market_meta is not None:
                recs = await asyncio.to_thread(_map_slate, games, sched, mlb_map, market_meta, rollover, map_state,
                                               None, sched_sink)
                by_pk = games_ref.setdefault("mlb_pk_tokens", {})    # game_pk -> tokens, for live extension
                for r in recs:
                    if r["match"] == "exact":
                        by_pk[r["game_pk"]] = ((r["pm"]["home_token"], r["pm"]["away_token"]), now)
                for pk in [pk for pk, (_, t) in by_pk.items() if now - t > 36 * 3600]:
                    del by_pk[pk]
                bad = [r["pm"]["slug"] for r in recs if r["match"] != "exact"]
                if bad:
                    log.warning("mlb_map: %d of %d games not exact: %s", len(bad), len(recs), bad[:5])
            if rollover:
                last_day = day_str
            log.info("slate: %d MLB markets, %d tokens subscribed, %d MLB games polled",
                     len(games), len(games_ref["tokens"]), len(games_ref["game_pks"]))
        except Exception as exc:
            log.warning("slate refresh: %s", exc)
        await asyncio.sleep(max(0.0, min(600.0, stop - time.time())))


async def _run(hours: float, window_h: float) -> None:
    stop = time.time() + hours * 3600
    games_ref = {"tokens": set(), "game_pks": set()}
    market_meta = MarketMeta(_Sink("market_meta"))
    soccer = capture_soccer.SoccerCapture(games_ref, _Sink("soccer_games"), _Sink("espn"), market_meta)
    map_state: dict = {}
    for name, seed in (("soccer", soccer.seed), ("mlb_map", lambda: seed_mlb_map(map_state))):
        try:
            seed()
        except Exception as exc:
            log.warning("%s seed: %s", name, exc)
    await asyncio.gather(
        _refresh(games_ref, window_h, stop, _Sink("games"), _Sink("mlb_map"), market_meta, _Sink("mlb_schedule"),
                 map_state),
        _clob(games_ref, _Sink("clob"), stop),
        _sports(_Sink("sports"), stop, games_ref),
        _mlb(games_ref, _Sink("mlb"), stop),
        soccer.discover_loop(stop),
        soccer.espn_loop(stop),
    )


def record(hours: float = 6.0, window_h: float = 4.0) -> None:
    asyncio.run(_run(hours, window_h))


def top_of_book(day: str) -> pd.DataFrame:
    """Flatten a recorded day's CLOB stream to best bid/ask per token over time."""
    rows = []
    with open_capture(DATA_DIR / "live" / day, "clob") as fh:          # plain or rotated .gz
        for line in fh:
            r = json.loads(line)
            if "msg" not in r:          # connection markers
                continue
            msgs = r["msg"] if isinstance(r["msg"], list) else [r["msg"]]
            for m in msgs:
                if "price_changes" in m:
                    for pc in m["price_changes"]:
                        rows.append((r["recv_ms"], int(m.get("timestamp") or 0), pc["asset_id"],
                                     pc.get("best_bid"), pc.get("best_ask")))
                elif "bids" in m and "asks" in m:
                    bb = max((float(x["price"]) for x in m["bids"]), default=None)
                    ba = min((float(x["price"]) for x in m["asks"]), default=None)
                    rows.append((r["recv_ms"], int(m.get("timestamp") or 0), m["asset_id"], bb, ba))
    df = pd.DataFrame(rows, columns=["recv_ms", "exch_ms", "token", "best_bid", "best_ask"])
    df[["best_bid", "best_ask"]] = df[["best_bid", "best_ask"]].astype(float)
    return df
