"""Join game state (MLB plays) to market price (Polymarket) into analysis tables.

Outputs (data/mlb/):
  pregame.parquet  one row per game: pregame home prob at actual first pitch, outcome
  panel.parquet    one row per plate appearance: post-play state + market home prob
                   ~60s after the play (settled) + outcome. `checkpoint` marks the
                   state at the start of each half-inning ("going into inning z").
  baseline.parquet same state rows from MLB-only seasons (no prices) for win-expectancy
"""
from __future__ import annotations

import glob
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from .collect import _write, sport_dir

log = logging.getLogger("pmsports")

SETTLE_S = 60        # market price sampled this long after the play ends
PREGAME_WIN_S = 600  # trade window before first pitch for the trade-based pregame price
MIN_TRADES = 3       # fills needed before a trade-median price is preferred over bars
TRADE_TS_LAG_S = 2.5 # Data-API timestamps are on-chain settlement, ~2.6s after the match


def _load_dir(path: Path) -> pd.DataFrame:
    fs = glob.glob(str(path / "*.parquet"))
    if not fs:
        return pd.DataFrame()
    return pd.concat((pd.read_parquet(f) for f in fs), ignore_index=True)


def state_rows(plays: pd.DataFrame) -> pd.DataFrame:
    """Post-play state per PA, plus a half-inning-start checkpoint row per half-inning.

    Checkpoints are emitted after the 3rd out of each half, labelled with the
    *next* half-inning (e.g. after the 3rd out in the bottom of the 5th ->
    "top 6th, 0 outs, bases empty"). The walk-off / final PA is dropped as a
    state because the game is over.
    """
    p = plays.sort_values(["game_pk", "play_idx"]).copy()
    p["diff"] = p.home_score - p.away_score       # home perspective
    p["checkpoint"] = False
    rows = [p]
    last = p[p.outs == 3].copy()
    if len(last):
        is_top = last.half == "top"
        last["inning"] = np.where(is_top, last.inning, last.inning + 1)
        last["half"] = np.where(is_top, "bottom", "top")
        last["outs"] = 0
        last[["on_1b", "on_2b", "on_3b"]] = False
        last["on_2b"] = last.inning >= 10  # extra-inning automatic runner (regular season)
        last["checkpoint"] = True
        rows.append(last)
    s = pd.concat(rows, ignore_index=True)
    # drop the game's final PA (and any checkpoint derived from it): no decision left
    last_idx = s.game_pk.map(p.groupby("game_pk").play_idx.max())
    s = s[s.play_idx != last_idx]
    s["outs"] = s.outs.where(s.outs < 3, 0)  # 3-out PA rows: state is really the next half's start
    s["bases"] = (s.on_1b.astype(int) + 2 * s.on_2b.astype(int) + 4 * s.on_3b.astype(int))
    s["state_ts"] = s.end_ts
    return s.drop(columns=["on_1b", "on_2b", "on_3b"])


def _asof(ts: np.ndarray, vals: np.ndarray, q: np.ndarray) -> np.ndarray:
    """Last value at or before each query time (NaN if none)."""
    if len(ts) == 0:
        return np.full(len(q), np.nan)
    i = np.searchsorted(ts, q, side="right") - 1
    return np.where(i >= 0, vals[np.clip(i, 0, None)], np.nan)


def build_panel(sport: str = "mlb") -> None:
    d = sport_dir(sport)
    games = pd.read_parquet(d / "games.parquet")
    games = games[games.game_pk.notna() & ~games.resolution_mismatch]
    games["game_pk"] = games.game_pk.astype(int)
    plays = _load_dir(d / "plays")
    log.info("loaded %d plays", len(plays))

    have = {int(f.stem) for f in (d / "prices").glob("*.parquet")} & set(plays.game_pk)
    games = games[games.game_pk.isin(have)].copy()
    first_pitch = plays.groupby("game_pk").start_ts.min()
    games["first_pitch_ts"] = games.game_pk.map(first_pitch)
    games["home_won_final"] = games.mlb_home_won.astype("boolean").fillna(games.home_won.astype("boolean"))

    # per-game readers (numeric columns only): memory stays flat with thousands of games
    def px_of(pk: int):
        g = pd.read_parquet(d / "prices" / f"{pk}.parquet", columns=["ts", "home_p"]).sort_values("ts")
        return g.ts.to_numpy(float), g.home_p.to_numpy(float)

    def tr_of(pk: int):
        f = d / "trades" / f"{pk}.parquet"
        if not f.exists():
            return None
        g = pd.read_parquet(f, columns=["timestamp", "home_p"]).sort_values("timestamp")
        return g.timestamp.to_numpy(float) - TRADE_TS_LAG_S, g.home_p.to_numpy(float)

    # ---- pregame
    pre = []
    for g in games.itertuples(index=False):
        ts, v = px_of(g.game_pk)
        fp = g.first_pitch_ts
        # NOT the last bar before first pitch: the book is cleared at game start and
        # prices-history prints a glitch bar (0.50 / empty-book midpoint) right there
        m_bar = (ts >= fp - 900) & (ts <= fp - 120)
        p_bar = float(np.median(v[m_bar])) if m_bar.any() else _asof(ts, v, np.array([fp - 120]))[0]
        p_tr, n_tr = np.nan, 0
        t = tr_of(g.game_pk)
        if t is not None:
            tts, tv = t
            m = (tts >= fp - PREGAME_WIN_S) & (tts < fp)
            n_tr = int(m.sum())
            if n_tr:
                p_tr = float(np.median(tv[m]))
        pre.append((g.game_pk, p_bar, p_tr, n_tr))
    pre = pd.DataFrame(pre, columns=["game_pk", "pre_p_bar", "pre_p_trades", "pre_n_trades"])
    pregame = games[["game_pk", "slug", "event_date", "game_type", "home_team", "away_team",
                     "volume", "fee_rate", "first_pitch_ts", "home_won_final"]].merge(pre, on="game_pk")
    # actual fills are the most trustworthy pregame price; bars only when trading was thin
    pregame["pre_p"] = pregame.pre_p_trades.where(pregame.pre_n_trades >= MIN_TRADES, pregame.pre_p_bar)
    _write(pregame, d / "pregame.parquet")

    # ---- in-game panel
    st = state_rows(plays[plays.game_pk.isin(games.game_pk)])
    st = st.merge(pregame[["game_pk", "event_date", "fee_rate", "home_won_final", "pre_p", "volume"]],
                  on="game_pk")
    parts = []
    for pk, g in st.groupby("game_pk"):
        ts, v = px_of(pk)
        q = g.state_ts.to_numpy(float)
        g = g.copy()
        g["mkt_p_bar"] = _asof(ts, v, q + SETTLE_S)
        g["mkt_p_trades"], g["mkt_n_trades"] = np.nan, 0
        t = tr_of(pk)
        if t is not None:
            tts, tv = t
            lo = np.searchsorted(tts, q + 15)
            hi = np.searchsorted(tts, q + 75)
            g["mkt_p_trades"] = [float(np.median(tv[a:b])) if b > a else np.nan for a, b in zip(lo, hi)]
            g["mkt_n_trades"] = hi - lo
        parts.append(g)
    panel = pd.concat(parts, ignore_index=True)
    # market price for a state = median fill 15-75s after the play (what was actually traded).
    # No bar fallback: with no fills the 1-min bar can sit frozen for innings (e.g. 0.54 while a
    # team leads by 10), which fabricates "edges" nobody could trade. Such states are dropped.
    panel["mkt_p"] = panel.mkt_p_trades.where(panel.mkt_n_trades >= MIN_TRADES)
    # stale bars after resolution (price pinned at 0/1) are not tradable states
    panel = panel[panel.mkt_p.between(0.005, 0.995)]
    _write(panel, d / "panel.parquet")
    log.info("pregame rows %d, panel rows %d (%d checkpoints)", len(pregame), len(panel),
             int(panel.checkpoint.sum()))

    # ---- MLB-only baseline states
    base_plays = _load_dir(d / "mlb_only" / "plays")
    all_plays = pd.concat([base_plays, plays], ignore_index=True).drop_duplicates(["game_pk", "play_idx"])
    if len(all_plays):
        finals = all_plays.sort_values("play_idx").groupby("game_pk").last()
        winner = (finals.home_score > finals.away_score).rename("home_won_final")
        b = state_rows(all_plays).merge(winner, left_on="game_pk", right_index=True)
        b = b[finals.loc[b.game_pk].home_score.values != finals.loc[b.game_pk].away_score.values]
        b["season"] = pd.to_datetime(b.start_ts, unit="s").dt.year
        _write(b, d / "baseline.parquet")
        log.info("baseline state rows %d from %d games", len(b), b.game_pk.nunique())
