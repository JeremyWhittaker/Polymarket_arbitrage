"""Latency from a recorded live day (python -m pmsports live-latency --day YYYY-MM-DD).

For every scoring plate appearance (truth clock = MLB Statcast in-play time):
  book_t50   seconds until the order-book mid is >= halfway to its post-play level
  sports_t   seconds until Polymarket's own score websocket shows the new score
  mlb_t      seconds until the free MLB Stats API linescore shows it (2s polling)
  spread     best ask - best bid on the home token just before contact

The gap between book_t50 and "when you would see it" (TV/stream delay, or the
free feed) is the whole timing hypothesis.
"""
from __future__ import annotations

import json
import logging

import numpy as np
import pandas as pd

from .. import mlb
from ..collect import DATA_DIR, match_mlb, mlb_schedule
from ..record import top_of_book

log = logging.getLogger("pmsports")


def _jsonl(path):
    with open(path) as fh:
        return [json.loads(line) for line in fh]


def load_metadata(day: str) -> pd.DataFrame:
    '''Load games metadata for a day, looking at previous days to recover cross-day captures.'''
    dt = pd.to_datetime(day)
    games = []
    for i in range(3):
        d_str = (dt - pd.Timedelta(days=i)).strftime("%Y-%m-%d")
        p = DATA_DIR / "live" / d_str / "games.jsonl"
        if p.exists():
            for r in _jsonl(p):
                if "game" in r:
                    games.append(r["game"])
    return pd.DataFrame(games).drop_duplicates("condition_id") if games else pd.DataFrame()

def live_latency(day: str, min_move: float = 0.03) -> pd.DataFrame:
    d = DATA_DIR / "live" / day
    games = load_metadata(day)
    if games.empty:
        return pd.DataFrame()
    days = sorted({str(x)[:10] for x in games.event_date})
    sched = mlb_schedule(min(days), max(days), out=d / "schedule.parquet")
    sched = sched.assign(abstract_state="Final")         # match live games too
    games = match_mlb(games, sched).dropna(subset=["game_pk"])

    book = top_of_book(day)
    book = book[book.exch_ms > 0]
    book["mid"] = (book.best_bid + book.best_ask) / 2
    sports = pd.DataFrame([{"recv_ms": r["recv_ms"], **r["msg"]} for r in _jsonl(d / "sports.jsonl")]) \
        if (d / "sports.jsonl").exists() else pd.DataFrame()
    mlbf = pd.DataFrame(_jsonl(d / "mlb.jsonl")) if (d / "mlb.jsonl").exists() else pd.DataFrame()

    out = []
    for g in games.itertuples(index=False):
        pk = int(g.game_pk)
        plays = pd.DataFrame(mlb.plays(pk))
        if plays.empty:
            continue
        hb = book[book.token == g.home_token].sort_values("exch_ms")
        if len(hb) < 10:
            continue
        ts, mid = hb.exch_ms.to_numpy() / 1000.0, hb.mid.to_numpy()
        spr = (hb.best_ask - hb.best_bid).to_numpy()
        sp = sports[sports.gameId == g.pm_game_id].sort_values("recv_ms") if len(sports) else sports
        ml = mlbf[mlbf.game_pk == pk].sort_values("recv_ms") if len(mlbf) else mlbf
        for p in plays[plays.is_scoring].itertuples(index=False):
            t0 = p.contact_ts
            i_pre = np.searchsorted(ts, t0 - 1) - 1
            i_post = np.searchsorted(ts, t0 + 60) - 1
            if i_pre < 0 or i_post <= i_pre:
                continue
            sign = 1.0 if p.half == "bottom" else -1.0
            move = sign * (mid[i_post] - mid[i_pre])
            if move < min_move:
                continue
            frac = sign * (mid - mid[i_pre]) / move
            idx = np.where((ts >= t0 - 2) & (frac >= 0.5))[0]
            rec = {"game_pk": pk, "slug": g.slug, "inning": p.inning, "half": p.half, "event": p.event_type,
                   "move": move, "spread": spr[i_pre], "book_t50": ts[idx[0]] - t0 if len(idx) else np.nan}
            score = f"{int(p.away_score)}-{int(p.home_score)}"
            if len(sp):
                hit = sp[(sp.recv_ms / 1000 >= t0 - 5) & (sp.score == score)]
                rec["sports_t"] = hit.recv_ms.iloc[0] / 1000 - t0 if len(hit) else np.nan
            if len(ml):
                hit = ml[(ml.recv_ms / 1000 >= t0 - 5) & (ml.away == p.away_score) & (ml.home == p.home_score)]
                rec["mlb_t"] = hit.recv_ms.iloc[0] / 1000 - t0 if len(hit) else np.nan
            out.append(rec)
    df = pd.DataFrame(out)
    if len(df):
        df.to_csv(d / "latency_events.csv", index=False)
    return df
