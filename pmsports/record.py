"""Live recorder: the data that does not exist historically.

Polymarket keeps no public order-book history and trade timestamps are 1-second
block times, so the questions "what price could I actually have gotten?" and
"who sees the play first?" need a live capture. Three streams, every message
stamped with local receive time (ms, NTP-synced clock assumed):

  clob.jsonl       CLOB market websocket: full books, price_change (with best
                   bid/ask), last_trade_price for every live/upcoming MLB game
  sports.jsonl     Polymarket's own sports websocket (the score feed its UI uses)
  mlb.jsonl        MLB Stats API linescore polled every ~2s per live game
                   (inning/half/outs/runs; the free public feed's latency)

Files land in data/live/<UTC date>/. Run under tmux/systemd for a whole slate:
  python -m pmsports record --hours 6
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import websockets

from . import polymarket as pm
from .collect import DATA_DIR
from .http import get_json

log = logging.getLogger("pmsports")
CLOB_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
SPORTS_WS = "wss://sports-api.polymarket.com/ws"
MLB_API = "https://statsapi.mlb.com/api/v1"


class _Sink:
    def __init__(self, name: str):
        self.name = name
        self.fh = None
        self.day = None

    def write(self, obj) -> None:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if day != self.day:
            if self.fh:
                self.fh.close()
            d = DATA_DIR / "live" / day
            d.mkdir(parents=True, exist_ok=True)
            self.fh = open(d / f"{self.name}.jsonl", "a", buffering=1)
            self.day = day
        self.fh.write(json.dumps({"recv_ms": int(time.time() * 1000), **obj}, separators=(",", ":")) + "\n")


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
    while time.time() < stop:
        tokens = sorted(games_ref["tokens"])
        if not tokens:
            await asyncio.sleep(30)
            continue
        try:
            async with websockets.connect(CLOB_WS, max_size=2**24, ping_interval=None) as ws:
                await ws.send(json.dumps({"assets_ids": tokens, "type": "market",
                                          "custom_feature_enabled": True}))
                subscribed = set(tokens)
                last_ping = time.time()
                while time.time() < stop:
                    if games_ref["tokens"] - subscribed:     # new games on the slate -> resubscribe
                        break
                    if time.time() - last_ping > 10:
                        await ws.send("PING")
                        last_ping = time.time()
                    try:
                        msg = await asyncio.wait_for(ws.recv(), 5)
                    except asyncio.TimeoutError:
                        continue
                    if msg == "PONG":
                        continue
                    sink.write({"msg": json.loads(msg)})
        except Exception as exc:
            log.warning("clob ws: %s; reconnecting", exc)
            await asyncio.sleep(3)


async def _sports(sink: _Sink, stop: float) -> None:
    while time.time() < stop:
        try:
            async with websockets.connect(SPORTS_WS, ping_interval=None) as ws:
                while time.time() < stop:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), 15)
                    except asyncio.TimeoutError:
                        continue
                    if msg == "ping":
                        await ws.send("pong")
                        continue
                    m = json.loads(msg)
                    if str(m.get("leagueAbbreviation", "")).lower() == "mlb":
                        sink.write({"msg": m})
        except Exception as exc:
            log.warning("sports ws: %s; reconnecting", exc)
            await asyncio.sleep(3)


async def _mlb(games_ref: dict, sink: _Sink, stop: float, every_s: float = 2.0) -> None:
    last: dict[int, str] = {}
    while time.time() < stop:
        for pk in list(games_ref["game_pks"]):
            try:
                ls = await asyncio.to_thread(get_json, f"{MLB_API}/game/{pk}/linescore")
            except Exception as exc:
                log.debug("linescore %s: %s", pk, exc)
                continue
            state = {"game_pk": pk, "inning": ls.get("currentInning"), "half": ls.get("inningHalf"),
                     "outs": ls.get("outs"), "away": (ls.get("teams") or {}).get("away", {}).get("runs"),
                     "home": (ls.get("teams") or {}).get("home", {}).get("runs"),
                     "offense": sorted(k for k in (ls.get("offense") or {}) if k in ("first", "second", "third"))}
            key = json.dumps(state, sort_keys=True)
            if last.get(pk) != key:          # log changes only
                last[pk] = key
                sink.write(state)
        await asyncio.sleep(every_s)


async def _refresh(games_ref: dict, window_h: float, stop: float, meta: _Sink) -> None:
    last_day = None
    while time.time() < stop:
        try:
            today = datetime.now(timezone.utc).date()
            day_str = today.strftime("%Y-%m-%d")
            rollover = (day_str != last_day)

            games = await asyncio.to_thread(_slate, window_h)
            sched = await asyncio.to_thread(get_json, f"{MLB_API}/schedule",
                                            {"sportId": 1, "startDate": (today - pd.Timedelta(days=1)).isoformat(),
                                             "endDate": today.isoformat()})
            live_pks = {g["gamePk"] for d in sched.get("dates", []) for g in d["games"]
                        if g["status"].get("abstractGameState") == "Live"}
            new = {t for g in games for t in (g["home_token"], g["away_token"])} - games_ref["tokens"]
            games_ref["tokens"] |= new
            games_ref["game_pks"] = live_pks
            for g in games:
                if rollover or g["home_token"] in new:
                    meta.write({"game": g})
            if rollover:
                last_day = day_str
            log.info("slate: %d markets subscribed, %d MLB games live", len(games_ref["tokens"]) // 2, len(live_pks))
        except Exception as exc:
            log.warning("slate refresh: %s", exc)
        await asyncio.sleep(max(0.0, min(600.0, stop - time.time())))


async def _run(hours: float, window_h: float) -> None:
    stop = time.time() + hours * 3600
    games_ref = {"tokens": set(), "game_pks": set()}
    await asyncio.gather(
        _refresh(games_ref, window_h, stop, _Sink("games")),
        _clob(games_ref, _Sink("clob"), stop),
        _sports(_Sink("sports"), stop),
        _mlb(games_ref, _Sink("mlb"), stop),
    )


def record(hours: float = 6.0, window_h: float = 4.0) -> None:
    asyncio.run(_run(hours, window_h))


def top_of_book(day: str) -> pd.DataFrame:
    """Flatten a recorded day's CLOB stream to best bid/ask per token over time."""
    rows = []
    with open(DATA_DIR / "live" / day / "clob.jsonl") as fh:
        for line in fh:
            r = json.loads(line)
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
