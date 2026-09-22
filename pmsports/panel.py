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
MAX_REFERENCE_AGE_S = 120.0
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
    p["state_expiry_ts"] = p.groupby("game_pk").end_ts.shift(-1)
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


def attach_execution(g: pd.DataFrame, tape, home_token: str, away_token: str) -> pd.DataFrame:
    """Causal reference and separate home/away first-print proxies, ending before next play.

    Raw settlement clock stands in for receipt; pending-order cancellation is NOT modeled.
    State expiry is an idealized order deadline, not evidence a venue can cancel pending orders.
    """
    g = g.copy()
    q = g.decision_ts.to_numpy(float)
    for col in ("mkt_p", "mkt_ts", "mkt_staleness", "mkt_size", "exec_p", "exec_ts", "exec_size"):
        g[col] = np.nan
    g["mkt_side"], g["exec_side"] = "", ""
    for name in ("home", "away"):
        for col in ("p", "ts", "size", "id"):
            g[f"exec_{name}_{col}"] = np.nan
    if tape is None or not len(tape[0]):
        return g
    ts, p, size, rawside, asset = tape
    known = np.isin(asset, [home_token, away_token]) & np.isin(rawside, ["BUY", "SELL"])
    raw_id = np.arange(len(ts))[known]
    ts, p, size, rawside, asset = (np.asarray(x)[known] for x in tape)
    if not len(ts):
        return g
    home = ((asset == home_token) & (rawside == "BUY")) | ((asset == away_token) & (rawside == "SELL"))
    label = np.char.add(np.char.add(rawside, "_"), np.where(asset == home_token, "HOME", "AWAY"))
    before = np.searchsorted(ts, q, side="left") - 1
    safe = np.maximum(before, 0)
    valid = (before >= 0) & (q - ts[safe] <= MAX_REFERENCE_AGE_S)
    for col, vals in (("mkt_p", p), ("mkt_ts", ts), ("mkt_size", size)):
        g[col] = np.where(valid, vals[safe], np.nan)
    g["mkt_staleness"] = q - g.mkt_ts
    g["mkt_side"] = np.where(valid, label[safe], "")
    expiry = g.state_expiry_ts.to_numpy(float)
    for name, mask in (("home", home), ("away", ~home), ("", np.ones(len(ts), bool))):
        ix = np.flatnonzero(mask)
        if not len(ix):
            continue
        candidate = np.searchsorted(ts[ix], q + EXEC_DELAY_S, side="right")
        j = ix[np.minimum(candidate, len(ix) - 1)]
        ok = (candidate < len(ix)) & np.isfinite(expiry) & (ts[j] < expiry)
        prefix = f"exec_{name}_" if name else "exec_"
        value = p if name != "away" else 1 - p
        for col, vals in (("p", value), ("ts", ts), ("size", size)):
            g[prefix + col] = np.where(ok, vals[j], np.nan)
        if name:
            g[prefix + "id"] = np.where(ok, raw_id[j], np.nan)
        else:
            g["exec_side"] = np.where(ok, label[j], "")
    return g


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

        t = tr_of(pk)
        g_row = games[games.game_pk == pk].iloc[0]
        g = attach_execution(g, t, str(g_row.home_token), str(g_row.away_token))

        parts.append(g)
    panel = pd.concat(parts, ignore_index=True)
    # stale states are dropped
    panel = panel[panel.mkt_p.between(0.005, 0.995) & panel.mkt_staleness.le(MAX_REFERENCE_AGE_S)]
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
            all_games_dfs.extend(pd.read_parquet(f) for f in sorted(sched_files))
        all_games = pd.concat(all_games_dfs, ignore_index=True).drop_duplicates("game_pk")
        game_types = all_games.set_index("game_pk")["game_type"]
        sched_inn = all_games.set_index("game_pk").get("scheduled_innings")
        b = state_rows(all_plays, game_types, sched_inn).merge(winner, left_on="game_pk", right_index=True)
        b = b[finals.loc[b.game_pk].home_score.values != finals.loc[b.game_pk].away_score.values]
        b["season"] = pd.to_datetime(b.start_ts, unit="s").dt.year
        _write(b, d / "baseline.parquet")
        log.info("baseline state rows %d from %d games", len(b), b.game_pk.nunique())
