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

SETTLE_S = 60
EXEC_DELAY_S = 5.0        # market price sampled this long after the play ends
PREGAME_WIN_S = 600  # trade window before first pitch for the trade-based pregame price
MIN_TRADES = 3       # fills needed before a trade-median price is preferred over bars
TRADE_TS_LAG_S = 2.5 # Data-API timestamps are on-chain settlement, ~2.6s after the match


def _load_dir(path: Path) -> pd.DataFrame:
    fs = glob.glob(str(path / "*.parquet"))
    if not fs:
        return pd.DataFrame()
    return pd.concat((pd.read_parquet(f) for f in fs), ignore_index=True)


def state_rows(plays: pd.DataFrame, game_types: pd.Series = None, scheduled_innings: pd.Series = None) -> pd.DataFrame:
    """Post-play state per PA, plus a half-inning-start checkpoint row per half-inning.

    Checkpoints are emitted after the 3rd out of each half, labelled with the
    *next* half-inning. The original 3rd-out rows are dropped so each state is unique.
    """
    p = plays.sort_values(["game_pk", "play_idx"]).copy()
    p["diff"] = p.home_score - p.away_score       # home perspective
    p["checkpoint"] = False
    last = p[p.outs == 3].copy()
    if len(last):
        is_top = last.half == "top"
        last["inning"] = np.where(is_top, last.inning, last.inning + 1)
        last["half"] = np.where(is_top, "bottom", "top")
        last["outs"] = 0
        last[["on_1b", "on_2b", "on_3b"]] = False
        if game_types is not None:
            gtypes = last.game_pk.map(game_types)
        else:
            gtypes = pd.Series(index=last.index, dtype=str)
        if scheduled_innings is not None:
            sched_inn = last.game_pk.map(scheduled_innings)
        else:
            sched_inn = pd.Series(9, index=last.index)

        last["on_2b"] = ((last.inning > sched_inn.fillna(9)) & (gtypes == "R")).fillna(False)
        last["checkpoint"] = True

    s = pd.concat([p[p.outs < 3], last], ignore_index=True) if len(last) else p[p.outs < 3].copy()

    # drop the game's final PA (no decision left)
    last_idx = s.game_pk.map(p.groupby("game_pk").play_idx.max())
    s = s[s.play_idx != last_idx].copy()
    s["bases"] = (s.on_1b.astype(int) + 2 * s.on_2b.astype(int) + 4 * s.on_3b.astype(int))
    s["state_ts"] = s.end_ts
    return s.sort_values(["game_pk", "state_ts", "play_idx"]).drop(columns=["on_1b", "on_2b", "on_3b"])


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
        g = pd.read_parquet(f, columns=["timestamp", "home_p", "size", "side", "asset"]).sort_values("timestamp")
        return (g.timestamp.to_numpy(float),
                g.home_p.to_numpy(float),
                g["size"].to_numpy(float),
                g.side.to_numpy(str),
                g.asset.to_numpy(str))

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
            tts, tv, _, _, _ = t
            m = (tts >= fp - PREGAME_WIN_S) & (tts < fp)
            n_tr = int(m.sum())
            if n_tr:
                p_tr = float(np.median(tv[m]))
        pre.append((g.game_pk, p_bar, p_tr, n_tr))
    pre = pd.DataFrame(pre, columns=["game_pk", "pre_p_bar", "pre_p_trades", "pre_n_trades"])
    cols = ["game_pk", "slug", "event_date", "game_type", "home_team", "away_team",
            "volume", "fee_rate", "first_pitch_ts", "home_won_final"]
    if "scheduled_innings" in games.columns:
        cols.append("scheduled_innings")
    pregame = games[cols].merge(pre, on="game_pk")
    # actual fills are the most trustworthy pregame price; bars only when trading was thin
    pregame["pre_p"] = pregame.pre_p_trades.where(pregame.pre_n_trades >= MIN_TRADES, pregame.pre_p_bar)
    _write(pregame, d / "pregame.parquet")

    # ---- in-game panel
    st = state_rows(plays[plays.game_pk.isin(games.game_pk)],
                    games.set_index("game_pk")["game_type"],
                    games.set_index("game_pk").get("scheduled_innings"))
    st_cols = ["game_pk", "event_date", "fee_rate", "home_won_final", "pre_p", "volume"]
    if "scheduled_innings" in pregame.columns:
        st_cols.append("scheduled_innings")
    st = st.merge(pregame[st_cols], on="game_pk")
    parts = []
    for pk, g in st.groupby("game_pk"):
        ts, v = px_of(pk)
        q = g.state_ts.to_numpy(float)
        g = g.copy()
        g["decision_ts"] = q
        g["mkt_p_bar"] = _asof(ts, v, q) # causal bar

    # mkt_p: causal reference (last fill strictly before decision_ts)
    # mkt_ts: timestamp of the causal reference
    # mkt_staleness: seconds since mkt_ts
    # mkt_size: size of the causal reference fill
    # mkt_side: side of the causal reference fill ("BUY" or "SELL")
    # exec_p: execution proxy (first fill strictly after decision_ts + EXEC_DELAY_S)
    # exec_ts: timestamp of the execution proxy fill
    # exec_size: size of the execution proxy fill
    # exec_side: side of the execution proxy fill ("BUY" or "SELL")

        g["mkt_p"], g["mkt_ts"], g["mkt_staleness"] = np.nan, np.nan, np.nan
        g["mkt_size"], g["mkt_side"] = np.nan, ""
        g["exec_p"], g["exec_ts"] = np.nan, np.nan
        g["exec_size"], g["exec_side"] = np.nan, ""

        t = tr_of(pk)
        if t is not None and len(t[0]) > 0:
            tts, tv, tsize, tside, tasset = t

            g_row = games[games.game_pk == pk].iloc[0]
            home_token = g_row.home_token
            away_token = g_row.away_token

            is_home = (tasset == home_token)
            is_away = (tasset == away_token)

            norm_side = np.where(is_home, np.char.add(tside, "_HOME"),
                                 np.where(is_away, np.char.add(tside, "_AWAY"), ""))

            idx_before = np.searchsorted(tts, q, side="left") - 1
            valid_before = idx_before >= 0

            g["mkt_p"] = np.where(valid_before, tv[np.clip(idx_before, 0, None)], np.nan)
            g["mkt_ts"] = np.where(valid_before, tts[np.clip(idx_before, 0, None)], np.nan)
            g["mkt_staleness"] = q - g["mkt_ts"]
            g["mkt_size"] = np.where(valid_before, tsize[np.clip(idx_before, 0, None)], np.nan)
            g["mkt_side"] = np.where(valid_before, norm_side[np.clip(idx_before, 0, None)], "")

            next_q = np.append(q[1:], np.inf)
            idx_after = np.searchsorted(tts, q + EXEC_DELAY_S, side="right")
            valid_after = (idx_after < len(tts)) & (tts[np.clip(idx_after, 0, len(tts)-1)] < next_q)

            g["exec_p"] = np.where(valid_after, tv[np.clip(idx_after, 0, len(tts)-1)], np.nan)
            g["exec_ts"] = np.where(valid_after, tts[np.clip(idx_after, 0, len(tts)-1)], np.nan)
            g["exec_size"] = np.where(valid_after, tsize[np.clip(idx_after, 0, len(tts)-1)], np.nan)
            g["exec_side"] = np.where(valid_after, norm_side[np.clip(idx_after, 0, len(tts)-1)], "")

        parts.append(g)
    panel = pd.concat(parts, ignore_index=True)
    # stale states are dropped
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
        sched_files = list((d / "mlb_only").glob("schedule_*.parquet"))
        all_games_dfs = [games]
        if sched_files:
            all_games_dfs.append(pd.read_parquet(sched_files[0]))
        all_games = pd.concat(all_games_dfs, ignore_index=True).drop_duplicates("game_pk")
        game_types = all_games.set_index("game_pk")["game_type"]
        sched_inn = all_games.set_index("game_pk").get("scheduled_innings")
        b = state_rows(all_plays, game_types, sched_inn).merge(winner, left_on="game_pk", right_index=True)
        b = b[finals.loc[b.game_pk].home_score.values != finals.loc[b.game_pk].away_score.values]
        b["season"] = pd.to_datetime(b.start_ts, unit="s").dt.year
        _write(b, d / "baseline.parquet")
        log.info("baseline state rows %d from %d games", len(b), b.game_pk.nunique())
