"""Resumable collectors. Layout under data/<sport>/:

  pm_games.parquet     one row per Polymarket game event (moneyline market)
  mlb_games.parquet    MLB schedule rows
  games.parquet        pm_games joined to MLB game_pk (the master table)
  prices/<pk>.parquet  1-minute home-team price (CLOB prices-history)
  trades/<pk>.parquet  every taker fill, 1-second timestamps (Data API)
  plays/<pk>.parquet   timestamped plate appearances with post-play state
  mlb_only/plays/<pk>.parquet  plays for seasons with no Polymarket data (baseline tables)
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from . import mlb, polymarket as pm

log = logging.getLogger("pmsports")
DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def sport_dir(sport: str = "mlb") -> Path:
    d = DATA_DIR / sport
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(path)


# --------------------------------------------------------------------------- discovery

def discover_pm_games(sport: str = "mlb") -> pd.DataFrame:
    rows, skipped = [], 0
    for e in pm.iter_events(pm.SERIES[sport], closed=True):
        r = pm.parse_game_event(e)
        if r is None:
            skipped += 1
        else:
            rows.append(r)
    df = pd.DataFrame(rows).drop_duplicates("condition_id")
    log.info("polymarket %s: %d game events (%d non-game/unsupported skipped)", sport, len(df), skipped)
    _write(df, sport_dir(sport) / "pm_games.parquet")
    return df


def mlb_schedule(start: str, end: str, out: Path | None = None) -> pd.DataFrame:
    rows = []
    d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    while d0 <= d1:
        e = min(d0 + timedelta(days=30), d1)
        rows += mlb.schedule(d0.isoformat(), e.isoformat())
        d0 = e + timedelta(days=1)
    df = pd.DataFrame(rows).drop_duplicates("game_pk", keep="last")
    _write(df, out or sport_dir("mlb") / "mlb_games.parquet")
    return df


_ALIASES = {"dbacks": "diamondbacks"}


def _team_eq(mlb_team_name: str, pm_team: str) -> bool:
    key = pm._norm(mlb_team_name)
    return pm._match_outcome(_ALIASES.get(key, key), pm_team)


def match_mlb(pm_games: pd.DataFrame, mlb_games: pd.DataFrame) -> pd.DataFrame:
    """Attach MLB game_pk to each Polymarket event (date +/-1 day, team names, nearest start)."""
    mg = mlb_games[mlb_games.abstract_state == "Final"].copy()
    mg["official_date"] = pd.to_datetime(mg.official_date).dt.date
    by_date = {d: g for d, g in mg.groupby("official_date")}
    out = []
    for r in pm_games.itertuples(index=False):
        try:
            ed = date.fromisoformat(str(r.event_date)[:10])
        except ValueError:
            out.append((None, False))
            continue
        best, best_dt, exact = None, None, []
        for dd in (ed, ed - timedelta(days=1), ed + timedelta(days=1)):
            g = by_date.get(dd)
            if g is None:
                continue
            for c in g.itertuples(index=False):
                if _team_eq(c.away_team_name, r.away_team) and _team_eq(c.home_team_name, r.home_team):
                    swapped = False
                elif _team_eq(c.away_team_name, r.home_team) and _team_eq(c.home_team_name, r.away_team):
                    swapped = True   # early-2025 Gamma events sometimes list home team first
                else:
                    continue
                if dd == ed:
                    exact.append((c, swapped))
                dt = abs((c.game_ts or 0) - (r.start_ts or 0))
                if best is None or dt < best_dt:
                    best, best_dt = (c, swapped), dt
        if best is not None and best_dt <= 12 * 3600:
            out.append((best[0].game_pk, best[1]))
        elif len(exact) == 1:
            # some early-2025 events carry a bogus startTime; a unique same-day game is safe
            out.append((exact[0][0].game_pk, exact[0][1]))
        else:
            # > 12h apart means a different game of the series (or a postponement)
            out.append((None, False))
    df = pm_games.copy()
    df["game_pk"] = pd.array([o[0] for o in out], dtype="Int64")
    # MLB is ground truth for home/away; flip Polymarket's orientation where it disagrees
    sw = pd.Series([o[1] for o in out], index=df.index)
    df["pm_orientation_swapped"] = sw
    df.loc[sw, ["home_team", "away_team"]] = df.loc[sw, ["away_team", "home_team"]].values
    df.loc[sw, ["home_token", "away_token"]] = df.loc[sw, ["away_token", "home_token"]].values
    df.loc[sw, "home_outcome_idx"] = 1 - df.loc[sw, "home_outcome_idx"]
    df.loc[sw & df.home_won.notna(), "home_won"] = ~df.loc[sw & df.home_won.notna(), "home_won"].astype(bool)
    df = df.merge(mlb_games.drop(columns=["away_team_name", "home_team_name"]),
                  on="game_pk", how="left", suffixes=("", "_mlb"))
    # MLB is ground truth for the winner; flag disagreements with the PM resolution
    df["mlb_home_won"] = (df.home_score > df.away_score).where(df.home_score.notna())
    df["resolution_mismatch"] = df.home_won.notna() & df.mlb_home_won.notna() & (
        df.home_won.astype("boolean") != df.mlb_home_won.astype("boolean"))
    return df


def build_games(sport: str = "mlb", refresh_pm: bool = True) -> pd.DataFrame:
    d = sport_dir(sport)
    pmg = discover_pm_games(sport) if refresh_pm or not (d / "pm_games.parquet").exists() \
        else pd.read_parquet(d / "pm_games.parquet")
    dates = pd.to_datetime(pmg.event_date, errors="coerce", format="%Y-%m-%d").dropna()
    start = (dates.min() - pd.Timedelta(days=2)).date().isoformat()
    end = (dates.max() + pd.Timedelta(days=2)).date().isoformat()
    mg = mlb_schedule(start, end)
    games = match_mlb(pmg, mg)
    _write(games, d / "games.parquet")
    n_match = games.game_pk.notna().sum()
    log.info("matched %d/%d polymarket games to MLB game_pk (%d resolution mismatches)",
             n_match, len(games), int(games.resolution_mismatch.sum()))
    return games


# --------------------------------------------------------------------------- per-game fetch

def _fetch_one(g, d: Path, what: set[str]) -> dict:
    pk = int(g.game_pk)
    stats = {"game_pk": pk}
    end = next((t for t in (g.finished_ts, g.closed_ts) if pd.notna(t)), g.start_ts + 6 * 3600)
    end = min(end, g.start_ts + 10 * 3600) + 1800  # guard against stale closed_ts
    if "plays" in what and not (d / "plays" / f"{pk}.parquet").exists():
        p = pd.DataFrame(mlb.plays(pk))
        _write(p, d / "plays" / f"{pk}.parquet")
        stats["plays"] = len(p)
    if "prices" in what and not (d / "prices" / f"{pk}.parquet").exists():
        h = pm.price_history(g.home_token, int(g.start_ts - 24 * 3600), int(end))
        p = pd.DataFrame(h, columns=["t", "p"]).rename(columns={"t": "ts", "p": "home_p"})
        p.insert(0, "game_pk", pk)
        _write(p, d / "prices" / f"{pk}.parquet")
        stats["prices"] = len(p)
    if "trades" in what and not (d / "trades" / f"{pk}.parquet").exists():
        t = pd.DataFrame(pm.trades(g.condition_id, int(g.start_ts - 3 * 3600), int(end)),
                         columns=list(pm.TRADE_FIELDS))
        t.insert(0, "game_pk", pk)
        # implied probability of the HOME team from every fill, whichever token traded
        t["home_p"] = t.price.where(t.outcomeIndex == g.home_outcome_idx, 1 - t.price)
        _write(t, d / "trades" / f"{pk}.parquet")
        stats["trades"] = len(t)
    return stats


def fetch_games(sport: str = "mlb", what=("plays", "prices", "trades"), workers: int = 6,
                since: str | None = None, until: str | None = None, limit: int | None = None) -> None:
    d = sport_dir(sport)
    games = pd.read_parquet(d / "games.parquet")
    games = games[games.game_pk.notna() & games.home_won.notna() & games.start_ts.notna()]
    if since:
        games = games[games.event_date >= since]
    if until:
        games = games[games.event_date <= until]
    games = games.sort_values("start_ts", ascending=False)
    if limit:
        games = games.head(limit)
    what = set(what)
    todo = [g for g in games.itertuples(index=False)
            if any(not (d / w / f"{int(g.game_pk)}.parquet").exists() for w in what)]
    log.info("fetching %s for %d games (%d already complete)", sorted(what), len(todo), len(games) - len(todo))
    t0, done, failed = time.time(), 0, 0
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(_fetch_one, g, d, what): g for g in todo}
        for f in as_completed(futs):
            try:
                f.result()
                done += 1
            except Exception as exc:  # keep going; a rerun retries only missing files
                failed += 1
                log.warning("game %s (%s) failed: %s", futs[f].game_pk, futs[f].slug, exc)
            if (done + failed) % 50 == 0:
                rate = (done + failed) / (time.time() - t0)
                log.info("%d/%d games (%d failed) %.1f games/s, eta %.0f min", done + failed, len(todo),
                         failed, rate, (len(todo) - done - failed) / max(rate, 1e-9) / 60)
    log.info("done: %d ok, %d failed", done, failed)


def fetch_mlb_history(start: str, end: str, workers: int = 6) -> None:
    """Plays for MLB seasons without Polymarket coverage: widens the win-expectancy baseline."""
    d = sport_dir("mlb") / "mlb_only"
    sched = mlb_schedule(start, end, out=d / f"schedule_{start}_{end}.parquet")
    sched = sched[sched.abstract_state == "Final"]
    todo = [pk for pk in sched.game_pk if not (d / "plays" / f"{pk}.parquet").exists()]
    log.info("mlb-only plays for %d games", len(todo))

    def one(pk):
        _write(pd.DataFrame(mlb.plays(int(pk))), d / "plays" / f"{pk}.parquet")

    with ThreadPoolExecutor(workers) as ex:
        for i, f in enumerate(as_completed([ex.submit(one, pk) for pk in todo]), 1):
            try:
                f.result()
            except Exception as exc:
                log.warning("mlb-only play fetch failed: %s", exc)
            if i % 200 == 0:
                log.info("%d/%d", i, len(todo))
