"""MLB derivative markets (NRFI/YRFI, run line, totals, first-five) x game state x moneyline price.

What this builds
----------------
`data/research/h_mlb_derivs/`
  games.parquet    one row per MLB game we can join (play-by-play + moneyline market + outcomes)
  menu.parquet     every derivative market linked to those games (kind, line, payouts), with the
                   pre-registered per-game "core menu" flag and the sampled flag
  meta.parquet     Gamma question/description per sampled market (this is how the NRFI/YRFI
                   orientation flip is resolved -- see GOTCHAS below)
  trades/<cid>.parquet   raw Data-API taker fills per sampled derivative market (cache; re-runs free)
  gamma/<event>.json     raw Gamma event payload (cache)
  panel.parquet    THE PANEL: one row per derivative taker fill, annotated with the game state at
                   the moment of the trade, the moneyline price at that moment, the market's line,
                   its payout, and a sign-normalised probability of a canonical event.

Usage
-----
    systemd-run --user --scope -q -p MemoryMax=4G -p MemorySwapMax=0 \
        .venv/bin/python -m pmsports.research.h_mlb_derivs all

Steps: games | select | meta | fetch | panel | validate | all

GOTCHAS (read before using the panel)
-------------------------------------
1. The `-nrfi` slug covers TWO OPPOSITE questions with identical outcome labels ("Yes"/"No"):
   2025-09..2026-04 it is "NRFI: A vs B" (Yes = NO run in the 1st); from ~2026-04 onward it is
   "Will there be a run scored in the first inning?" (Yes = a run IS scored).  Only the Gamma
   question/description distinguishes them.  Use `p_event` / `won_event` (canonical event =
   "a run is scored in the 1st"), never the raw side index.
2. `volume` on a derivative market is TOTAL volume incl. in-play, so it is outcome-correlated.
   It is carried in the panel for capacity reporting only -- never select on it.
3. `closed_ts` of a derivative market is the resolution time (e.g. a total closes the moment the
   line is passed).  It is outcome information; use it for fetch windows, not for selection.
4. Selection here is on pre-decision information only: which game (systematic sample over the
   calendar), which market kind, and which line (standard run line 1.5 / median offered line).

5. `p_event` is built from a TAKER print, so it is an ASK on the side that taker bought and a BID
   on the other side: `taker_on_event` says which.  Averaging both sides makes the canonical event
   look ~4-6 c cheap in every market type -- that is the spread, not an edge.  To buy the event you
   pay the ask (`taker_on_event == True` prints, or the quoted ask).

panel.parquet columns
---------------------
  identity   event_slug game_pk condition_id market_slug kind line question-free `yes_means`
  fill       ts (on-chain, ~2.6 s after the trade) match_ts (=ts-2.6) wallet size
             s (side the taker ACQUIRED, 0/1) q (price paid for s) y (payout of s)
             p0 (price of outcome 0 implied by this print) fee_rate in_play holdout dev
  canonical  p_event (prob of `event_name`, sign-normalised) won_event taker_on_event
             o0 o1 y0 y1 mk_volume(!) game_start_ts mk_closed_ts
  state      play_idx inning half_bottom outs away_score home_score on_1b/2b/3b
             runs_so_far r1_so_far r5_so_far r1_settled r5_settled game_over
             since_play_s play_in_progress half_inning_break
  moneyline  ml_ask_home / ml_bid_home (+_age) ml_p_home (last print either side, +_age)
             ml_pre_p_home (pregame median from markets.parquet)
  outcome    final_runs final_margin_home r1 r5 final_inning home_won month

play_panel.parquet columns (one row per market x in-play plate appearance)
  play       play_idx inning half_bottom play_start_ts contact_ts play_end_ts event event_type
             is_scoring rbi runs_before/after away_before/after home_before/after outs_before/after
             on_1b/2b/3b home_scored away_scored
  price      p_before (+p_before_age): last print BEFORE the play
             entry_p / entry_ts: first print of any side at ts >= play_end + 3 s + 2.6 s
             ent0_q/ent0_ts/ent0_size, ent1_*: first print acquiring outcome 0 / 1 after that delay
             ml_p_home_before (+_age)
  outcome    y0 y1 won_event yes_means event_flipped final_runs final_margin_home r1 r5

market_summary.parquet: one row per sampled market with PREGAME liquidity/price
  (pre_usd, pre_n_fills, pre_p_event = median p_event in the last 60 min before start,
   pre_p_event_all), plus n_fills / n_in_play / taker_usd / mk_volume(!) and the outcome.

Resolution semantics (validated against play-by-play, see `validate`)
  total-XptY          outcome 0 = "Over",  wins iff final combined runs > X.Y
  spread-home-XptY    outcome 0 = home,    wins iff (home - away) > X.Y      (home lays the runs)
  spread-away-XptY    outcome 0 = away,    wins iff (away - home) > X.Y
  f5-total-XptY       outcome 0 = "Over",  wins iff runs through inning 5 > X.Y
  extra-innings       outcome 0 = "Yes",   wins iff the game went past 9 innings
  nrfi                orientation varies -- see gotcha 1.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import common as C
from .. import http as _http

# be polite: the guide caps the Data API at 5 rps for research studies
_http.HOST_RPS["data-api.polymarket.com"] = 5.0
_http.HOST_RPS["gamma-api.polymarket.com"] = 5.0

from .. import polymarket as pm  # noqa: E402  (after the rate-limit tweak)

OUT = C.DATA / "research" / "h_mlb_derivs"
GAMMA_DIR = OUT / "gamma"
TRADE_DIR = OUT / "trades"
PLAYS = C.DATA / "mlb" / "plays"

MAX_MARKETS = 2000          # study budget for per-market Data API fetches
CHAIN_LAG = 2.6             # s: on-chain settlement timestamp - real match time
HOLDOUT = pd.Timestamp("2026-07-01", tz="UTC").timestamp()

_PAT = [
    ("nrfi", re.compile(r"^-nrfi$")),
    ("extra_innings", re.compile(r"^-extra-innings$")),
    ("total", re.compile(r"^-total-(\d+)pt(\d)$")),
    ("spread_home", re.compile(r"^-spread-home-(\d+)pt(\d)$")),
    ("spread_away", re.compile(r"^-spread-away-(\d+)pt(\d)$")),
    ("f5_total", re.compile(r"^-f5-total-(\d+)pt(\d)$")),
    ("f5_spread_home", re.compile(r"^-f5-spread-home-(\d+)pt(\d)$")),
    ("f5_spread_away", re.compile(r"^-f5-spread-away-(\d+)pt(\d)$")),
]


def _classify(suffix: str) -> tuple[str, float]:
    for kind, rx in _PAT:
        m = rx.match(suffix)
        if m:
            line = float(f"{m.group(1)}.{m.group(2)}") if m.groups() else np.nan
            return kind, line
    return ("moneyline", np.nan) if suffix == "" else ("other", np.nan)


# --------------------------------------------------------------------------- step 1: games

def build_games() -> pd.DataFrame:
    """Per-game facts from data/mlb/plays + the Polymarket moneyline market."""
    OUT.mkdir(parents=True, exist_ok=True)
    g = pd.read_parquet(C.DATA / "mlb" / "games.parquet")
    g = g[g.game_pk.notna()].drop_duplicates("slug").rename(columns={"slug": "event_slug"})
    pks = [int(p) for p in g.game_pk]
    cols = ["game_pk", "play_idx", "inning", "half", "start_ts", "end_ts",
            "away_score", "home_score", "outs"]
    frames = []
    for pk in pks:
        f = PLAYS / f"{pk}.parquet"
        if f.exists():
            frames.append(pd.read_parquet(f, columns=cols))
    d = pd.concat(frames, ignore_index=True).sort_values(["game_pk", "play_idx"])
    d["runs"] = d.away_score + d.home_score

    last = d.groupby("game_pk").tail(1).set_index("game_pk")
    i1 = d[d.inning == 1].groupby("game_pk")
    i5 = d[d.inning <= 5].groupby("game_pk")
    facts = pd.DataFrame({
        "r1": i1.runs.max(), "r1_end_ts": i1.end_ts.max(),
        "r5": i5.runs.max(), "r5_end_ts": i5.end_ts.max(),
        "final_runs": last.runs, "final_margin_home": last.home_score - last.away_score,
        "final_inning_plays": last.inning, "n_plays": d.groupby("game_pk").size(),
        "first_play_ts": d.groupby("game_pk").start_ts.min(),
        "last_play_ts": d.groupby("game_pk").end_ts.max(),
    })
    facts["r1_complete"] = facts.r1_end_ts.notna()
    facts["r5_complete"] = facts.final_inning_plays >= 5

    mk = C.markets()
    mk = mk[(mk.family == "baseball") & (mk.league == "mlb")]
    mk = mk[["m", "condition_id", "event_slug", "game_start_ts", "closed_ts", "fee_rate",
             "pre_mid0", "pre_ask0", "pre_ask1", "pre_usd", "pre_n_fills"]]
    mk = mk.rename(columns={c: f"ml_{c}" for c in ["closed_ts", "fee_rate"]})

    out = (g[["event_slug", "game_pk", "condition_id", "home_outcome_idx", "game_ts",
              "away_abbr", "home_abbr", "away_name", "home_name", "away_score", "home_score",
              "final_inning", "game_type", "status", "double_header", "game_number"]]
           .merge(facts, left_on="game_pk", right_index=True, how="inner")
           .merge(mk, on=["event_slug", "condition_id"], how="left"))
    out["home_won"] = (out.home_score > out.away_score).astype("int8")
    out["date"] = pd.to_datetime(out.game_ts, unit="s", utc=True).dt.tz_convert("America/New_York").dt.date.astype(str)
    out["month"] = out.date.str.slice(0, 7)
    out.to_parquet(OUT / "games.parquet", index=False)
    print(f"[games] {len(out):,} games with play-by-play; {out.m.notna().sum():,} have a local moneyline tape")
    return out


# --------------------------------------------------------------------------- step 2: selection

def _menu(cand: pd.DataFrame) -> pd.DataFrame:
    """Pre-registered per-game core menu (pre-decision information only).

    nrfi                 the single NRFI/YRFI market
    spread_home          the standard MLB run line 1.5 (else the smallest home line offered)
    total                the MEDIAN total line offered for that game (the consensus main line)
    f5_total             the MEDIAN first-five total line offered
    """
    picks = []
    for kind, rule in [("nrfi", "only"), ("spread_home", "runline"),
                       ("total", "median"), ("f5_total", "median")]:
        sub = cand[cand.kind == kind]
        if sub.empty:
            continue
        if rule == "only":
            picks.append(sub.drop_duplicates("event_slug"))
        elif rule == "runline":
            s = sub.assign(d=(sub.line - 1.5).abs()).sort_values(["event_slug", "d", "line"])
            picks.append(s.drop_duplicates("event_slug").drop(columns="d"))
        else:
            med = sub.groupby("event_slug").line.median().rename("med")
            s = sub.merge(med, on="event_slug")
            s = s.assign(d=(s.line - s.med).abs()).sort_values(["event_slug", "d", "line"])
            picks.append(s.drop_duplicates("event_slug").drop(columns=["d", "med"]))
    return pd.concat(picks, ignore_index=True)


def select_markets(max_markets: int = MAX_MARKETS) -> pd.DataFrame:
    games = pd.read_parquet(OUT / "games.parquet")
    u = C.universe(columns=["condition_id", "token_id", "event_slug", "market_slug", "market_type",
                            "outcome", "outcome_idx", "payout", "game_start_ts", "closed_ts",
                            "fee_rate", "volume", "neg_risk"])
    u = u[u.event_slug.isin(set(games.event_slug))]
    w = (u.pivot_table(index="condition_id", columns="outcome_idx",
                       values="payout", aggfunc="first").rename(columns={0: "y0", 1: "y1"}))
    o = (u.pivot_table(index="condition_id", columns="outcome_idx",
                       values="outcome", aggfunc="first").rename(columns={0: "o0", 1: "o1"}))
    tok = (u.pivot_table(index="condition_id", columns="outcome_idx",
                         values="token_id", aggfunc="first").rename(columns={0: "tok0", 1: "tok1"}))
    base = (u.drop_duplicates("condition_id")
              .set_index("condition_id")[["event_slug", "market_slug", "market_type",
                                          "game_start_ts", "closed_ts", "fee_rate", "volume", "neg_risk"]]
              .join([w, o, tok]).reset_index())
    kl = [_classify(ms[len(es):]) for ms, es in zip(base.market_slug, base.event_slug)]
    base["kind"] = [k for k, _ in kl]
    base["line"] = [l for _, l in kl]
    cand = base[~base.kind.isin(["moneyline", "other"])].copy()
    cand = cand.merge(games[["event_slug", "game_pk", "month", "m"]], on="event_slug", how="inner")

    menu = _menu(cand)
    menu["in_menu"] = True
    # systematic sample over the calendar until the market budget is spent: no volume, no outcome.
    order = menu.sort_values(["month", "game_start_ts", "event_slug"])
    per_game = order.groupby(["month", "event_slug"], sort=False).size().reset_index(name="n")
    per_game["rank"] = per_game.groupby("month").cumcount()
    per_game["n_month"] = per_game.groupby("month")["event_slug"].transform("size")

    def take(frac: float) -> pd.Series:
        r = per_game["rank"].to_numpy()
        return pd.Series(np.floor((r + 1) * frac).astype(int) > np.floor(r * frac).astype(int),
                         index=per_game.index)

    lo, hi = 0.0, 1.0
    for _ in range(40):
        mid = (lo + hi) / 2
        if int(per_game.n[take(mid)].sum()) <= max_markets:
            lo = mid
        else:
            hi = mid
    keep = set(per_game.event_slug[take(lo)])
    menu["sampled"] = menu.event_slug.isin(keep)
    out = pd.concat([menu, cand[~cand.condition_id.isin(set(menu.condition_id))]
                     .assign(in_menu=False, sampled=False)], ignore_index=True)
    out.to_parquet(OUT / "menu.parquet", index=False)
    s = out[out.sampled]
    print(f"[select] {out.event_slug.nunique():,} games have derivatives; menu {out.in_menu.sum():,} markets; "
          f"sampled {len(s):,} markets over {s.event_slug.nunique():,} games (budget {max_markets})")
    print(s.groupby("kind").size().to_string())
    print(s.drop_duplicates("event_slug").groupby("month").size().to_string())
    return out


# --------------------------------------------------------------------------- step 3: Gamma metadata

def _yes_means(question: str, desc: str) -> str:
    q, dd = (question or "").lower(), (desc or "").lower()
    if re.search(r'resolve to ["“]?yes["”]?\s+if no runs', dd):
        return "no_run"
    if re.search(r'resolve to ["“]?yes["”]?\s+if at least one run', dd):
        return "run"
    if q.startswith("nrfi"):
        return "no_run"
    if "will there be a run scored in the first inning" in q:
        return "run"
    return "unknown"


def fetch_meta() -> pd.DataFrame:
    """One Gamma /events request per sampled game -> question text for every market of that game."""
    GAMMA_DIR.mkdir(parents=True, exist_ok=True)
    menu = pd.read_parquet(OUT / "menu.parquet")
    slugs = sorted(menu.loc[menu.sampled, "event_slug"].unique())
    rows, miss = [], 0
    for i, es in enumerate(slugs):
        f = GAMMA_DIR / f"{es}.json"
        if f.exists():
            j = json.loads(f.read_text())
        else:
            try:
                j = _http.get_json("https://gamma-api.polymarket.com/events",
                                   {"slug": es, "closed": "true"})
            except Exception as e:  # noqa: BLE001
                print(f"  [meta] {es}: {type(e).__name__} {e}")
                j = []
            f.write_text(json.dumps(j))
        if not j:
            miss += 1
            continue
        for m in (j[0].get("markets") or []):
            rows.append({"market_slug": m.get("slug"), "condition_id": m.get("conditionId"),
                         "question": m.get("question"), "description": m.get("description"),
                         "gamma_outcomes": m.get("outcomes"),
                         "gamma_start_ts": pm.parse_ts(m.get("gameStartTime")),
                         "gamma_closed_ts": pm.parse_ts(m.get("closedTime"))})
        if (i + 1) % 100 == 0:
            print(f"  [meta] {i + 1}/{len(slugs)}")
    meta = pd.DataFrame(rows).drop_duplicates("condition_id")
    meta["yes_means"] = [_yes_means(q, d) for q, d in zip(meta.question, meta.description)]
    meta.to_parquet(OUT / "meta.parquet", index=False)
    nr = meta[meta.market_slug.str.endswith("-nrfi", na=False)]
    print(f"[meta] {len(meta):,} markets described ({miss} events missing from Gamma); "
          f"nrfi orientation: {nr.yes_means.value_counts().to_dict()}")
    return meta


# --------------------------------------------------------------------------- step 4: fills

def fetch_trades(limit: int | None = None) -> None:
    """Data API taker fills for every sampled derivative market. Cached per market."""
    TRADE_DIR.mkdir(parents=True, exist_ok=True)
    menu = pd.read_parquet(OUT / "menu.parquet")
    s = menu[menu.sampled].sort_values(["game_start_ts", "market_slug"])
    if limit:
        s = s.head(limit)
    todo = [r for r in s.itertuples() if not (TRADE_DIR / f"{r.condition_id}.parquet").exists()]
    print(f"[fetch] {len(s):,} sampled markets, {len(todo):,} to download")
    t0 = time.time()
    for i, r in enumerate(todo):
        start = int(r.game_start_ts - 14 * 86400)
        end = int((r.closed_ts if pd.notna(r.closed_ts) else r.game_start_ts + 86400) + 3600)
        try:
            tr = pm.trades(r.condition_id, start, end)
        except Exception as e:  # noqa: BLE001
            print(f"  [fetch] {r.market_slug}: {type(e).__name__} {e}")
            continue
        df = pd.DataFrame(tr, columns=list(pm.TRADE_FIELDS))
        df.to_parquet(TRADE_DIR / f"{r.condition_id}.parquet", index=False)
        if (i + 1) % 100 == 0:
            el = time.time() - t0
            print(f"  [fetch] {i + 1}/{len(todo)}  {el:.0f}s  eta {el / (i + 1) * (len(todo) - i - 1):.0f}s")
    print(f"[fetch] done in {time.time() - t0:.0f}s")


# --------------------------------------------------------------------------- step 5: panel

def _state_at(pl: dict, t: np.ndarray) -> dict:
    """Game state visible at real-match times `t` (vectorised over fills of one game)."""
    end = pl["end_ts"]
    i = np.searchsorted(end, t, side="right") - 1          # last COMPLETED play
    ok = i >= 0
    j = np.clip(i, 0, len(end) - 1)
    nxt = np.clip(i + 1, 0, len(end) - 1)
    has_next = (i + 1) < len(end)
    out = {
        "play_idx": np.where(ok, pl["play_idx"][j], -1),
        "inning": np.where(ok, pl["inning"][j], 0),
        "half_bottom": np.where(ok, pl["half_bottom"][j], False),
        "outs": np.where(ok, pl["outs"][j], 0),
        "away_score": np.where(ok, pl["away_score"][j], 0),
        "home_score": np.where(ok, pl["home_score"][j], 0),
        "on_1b": np.where(ok, pl["on_1b"][j], False),
        "on_2b": np.where(ok, pl["on_2b"][j], False),
        "on_3b": np.where(ok, pl["on_3b"][j], False),
        "since_play_s": np.where(ok, t - end[j], np.nan),
        "play_in_progress": has_next & (pl["start_ts"][nxt] <= t),
        "half_inning_break": has_next & (pl["start_ts"][nxt] > t)
                             & (pl["half_idx"][nxt] != pl["half_idx"][j]),
    }
    runs = pl["runs"]
    out["runs_so_far"] = np.where(ok, runs[j], 0)
    i1, i5 = pl["last_i1"], pl["last_i5"]
    out["r1_so_far"] = np.where(ok, runs[np.minimum(j, i1)], 0) if i1 >= 0 else np.zeros(len(t))
    out["r5_so_far"] = np.where(ok, runs[np.minimum(j, i5)], 0) if i5 >= 0 else np.zeros(len(t))
    out["r1_settled"] = t >= pl["r1_end_ts"] if np.isfinite(pl["r1_end_ts"]) else np.zeros(len(t), bool)
    out["r5_settled"] = t >= pl["r5_end_ts"] if np.isfinite(pl["r5_end_ts"]) else np.zeros(len(t), bool)
    out["game_over"] = t >= pl["last_ts"]
    return out


def _asof(src_ts: np.ndarray, src_v: np.ndarray, q: np.ndarray):
    """Last value at or before each q; returns (value, age_seconds)."""
    if len(src_ts) == 0:
        return np.full(len(q), np.nan), np.full(len(q), np.nan)
    i = np.searchsorted(src_ts, q, side="right") - 1
    ok = i >= 0
    j = np.clip(i, 0, len(src_ts) - 1)
    return np.where(ok, src_v[j], np.nan), np.where(ok, q - src_ts[j], np.nan)


def build_panel() -> pd.DataFrame:
    games = pd.read_parquet(OUT / "games.parquet").set_index("event_slug")
    menu = pd.read_parquet(OUT / "menu.parquet")
    s = menu[menu.sampled].copy()
    meta = pd.read_parquet(OUT / "meta.parquet")[["condition_id", "question", "yes_means"]]
    s = s.merge(meta, on="condition_id", how="left")

    codes = sorted({int(x) for x in games.m.dropna()})
    ml = C.fills(markets=codes, columns=["m", "ts", "s", "q", "size", "fee_rate"])
    ml = ml.sort_values(["m", "ts"])
    ml_by_m = {m: d for m, d in ml.groupby("m")}

    out = []
    for es, grp in s.groupby("event_slug"):
        g = games.loc[es]
        f = PLAYS / f"{int(g.game_pk)}.parquet"
        pl_df = pd.read_parquet(f).sort_values("play_idx")
        half_bottom = (pl_df.half == "bottom").to_numpy()
        half_idx = (pl_df.inning.to_numpy() * 2 + half_bottom.astype(int))
        pl = {
            "play_idx": pl_df.play_idx.to_numpy(), "inning": pl_df.inning.to_numpy(),
            "half_bottom": half_bottom, "half_idx": half_idx,
            "start_ts": pl_df.start_ts.to_numpy(float), "end_ts": pl_df.end_ts.to_numpy(float),
            "outs": pl_df.outs.to_numpy(), "away_score": pl_df.away_score.to_numpy(),
            "home_score": pl_df.home_score.to_numpy(),
            "on_1b": pl_df.on_1b.to_numpy(), "on_2b": pl_df.on_2b.to_numpy(),
            "on_3b": pl_df.on_3b.to_numpy(),
            "runs": (pl_df.away_score + pl_df.home_score).to_numpy(),
            "last_i1": int(np.max(np.where(pl_df.inning.to_numpy() <= 1)[0], initial=-1)),
            "last_i5": int(np.max(np.where(pl_df.inning.to_numpy() <= 5)[0], initial=-1)),
            "r1_end_ts": float(g.r1_end_ts) if pd.notna(g.r1_end_ts) else np.inf,
            "r5_end_ts": float(g.r5_end_ts) if pd.notna(g.r5_end_ts) else np.inf,
            "last_ts": float(g.last_play_ts),
        }
        # ensure end_ts is monotone for searchsorted (7 games have a 1-2 s inversion)
        pl["end_ts"] = np.maximum.accumulate(pl["end_ts"])

        mlf = ml_by_m.get(int(g.m)) if pd.notna(g.m) else None
        hi = int(g.home_outcome_idx)
        if mlf is not None and len(mlf):
            mts = mlf.ts.to_numpy(float)
            php = np.where(mlf.s.to_numpy() == hi, mlf.q.to_numpy(float), 1 - mlf.q.to_numpy(float))
            hm = mlf.s.to_numpy() == hi
            ask_ts, ask_v = mts[hm], mlf.q.to_numpy(float)[hm]                 # paid for home
            bid_ts, bid_v = mts[~hm], 1 - mlf.q.to_numpy(float)[~hm]           # 1 - paid for away
        else:
            mts = php = ask_ts = ask_v = bid_ts = bid_v = np.array([])

        for r in grp.itertuples():
            tf = TRADE_DIR / f"{r.condition_id}.parquet"
            if not tf.exists():
                continue
            d = pd.read_parquet(tf)
            if d.empty:
                continue
            d = d.sort_values("timestamp").reset_index(drop=True)
            buy = d.side.to_numpy() == "BUY"
            oi = d.outcomeIndex.to_numpy().astype(int)
            price = d.price.to_numpy(float)
            side = np.where(buy, oi, 1 - oi)                   # side the TAKER acquired
            qq = np.where(buy, price, 1 - price)               # price paid for that side
            ts = d.timestamp.to_numpy(float)
            p0 = np.where(side == 0, qq, 1 - qq)               # implied price of outcome 0
            y = np.where(side == 0, r.y0, r.y1)

            st = _state_at(pl, ts - CHAIN_LAG)
            ml_last, ml_last_age = _asof(mts, php, ts)
            ml_ask, ml_ask_age = _asof(ask_ts, ask_v, ts)
            ml_bid, ml_bid_age = _asof(bid_ts, bid_v, ts)

            row = {
                "event_slug": es, "game_pk": int(g.game_pk), "condition_id": r.condition_id,
                "market_slug": r.market_slug, "kind": r.kind, "line": r.line, "yes_means": r.yes_means,
                "ts": ts, "match_ts": ts - CHAIN_LAG, "wallet": d.proxyWallet.to_numpy(),
                "size": d["size"].to_numpy(float), "s": side.astype(np.int8), "q": qq, "y": y,
                "p0": p0, "fee_rate": r.fee_rate,
                "o0": r.o0, "o1": r.o1, "y0": r.y0, "y1": r.y1,
                "game_start_ts": r.game_start_ts, "mk_closed_ts": r.closed_ts,
                "mk_volume": r.volume,
                "in_play": ts >= r.game_start_ts,
                "holdout": ts >= HOLDOUT,
                "ml_p_home": ml_last, "ml_p_home_age": ml_last_age,
                "ml_ask_home": ml_ask, "ml_ask_home_age": ml_ask_age,
                "ml_bid_home": ml_bid, "ml_bid_home_age": ml_bid_age,
                "ml_pre_p_home": (g.pre_mid0 if hi == 0 else 1 - g.pre_mid0),
                "final_runs": g.final_runs, "final_margin_home": g.final_margin_home,
                "r1": g.r1, "r5": g.r5, "final_inning": g.final_inning,
                "home_won": g.home_won, "month": g.month,
            }
            row.update(st)
            out.append(pd.DataFrame(row))

    p = pd.concat(out, ignore_index=True)

    # canonical, sign-normalised event probability (removes the NRFI/YRFI orientation trap)
    canon = pd.Series("side0", index=p.index, dtype=object)
    flip = (p.kind == "nrfi") & (p.yes_means == "no_run")
    p["p_event"] = np.where(flip, 1 - p.p0, p.p0)
    p["won_event"] = np.where(flip, p.y1, p.y0)
    p["event_name"] = p.kind.map({
        "nrfi": "run scored in the 1st inning",
        "total": "combined runs over the line",
        "f5_total": "runs through inning 5 over the line",
        "spread_home": "home team wins by more than the line",
        "spread_away": "away team wins by more than the line",
        "extra_innings": "game goes past 9 innings"})
    p.loc[(p.kind == "nrfi") & (p.yes_means == "unknown"), ["p_event", "won_event"]] = np.nan
    p["taker_on_event"] = np.where(flip, p.s == 1, p.s == 0)   # did this taker buy the canonical event
    # `holdout` is per fill; split on `game_holdout` so a late-June game whose fills spill past
    # midnight UTC stays whole (5 of 626 games straddle the boundary).
    p["game_holdout"] = p.game_start_ts >= HOLDOUT
    p["dev"] = ~p.game_holdout
    del canon

    p.to_parquet(OUT / "panel.parquet", index=False)
    _market_summary(p)
    print(f"[panel] {len(p):,} rows, {p.condition_id.nunique():,} markets, {p.game_pk.nunique():,} games")
    print(p.groupby("kind").agg(rows=("q", "size"), games=("game_pk", "nunique"),
                                in_play=("in_play", "mean")).to_string())
    return p


def _market_summary(p: pd.DataFrame) -> pd.DataFrame:
    """One row per sampled derivative market, with PREGAME liquidity and price.

    `pre_usd` / `pre_n_fills` / `pre_p_event` use only fills before the scheduled start, so they
    are legitimate pre-decision selectors -- unlike `mk_volume`, which includes in-play trading.
    """
    p = p.assign(usd=p["size"] * p.q)
    pre = p[~p.in_play]
    pre60 = pre[pre.ts >= pre.game_start_ts - 3600]
    agg = p.groupby("condition_id").agg(
        market_slug=("market_slug", "first"), kind=("kind", "first"), line=("line", "first"),
        event_slug=("event_slug", "first"), game_pk=("game_pk", "first"), month=("month", "first"),
        fee_rate=("fee_rate", "first"), y0=("y0", "first"), y1=("y1", "first"),
        won_event=("won_event", "first"), yes_means=("yes_means", "first"),
        game_start_ts=("game_start_ts", "first"), mk_closed_ts=("mk_closed_ts", "first"),
        mk_volume=("mk_volume", "first"), n_fills=("q", "size"), taker_usd=("usd", "sum"),
        first_fill_ts=("ts", "min"), last_fill_ts=("ts", "max"), n_in_play=("in_play", "sum"),
        holdout=("game_holdout", "first"), ml_pre_p_home=("ml_pre_p_home", "first"),
        final_runs=("final_runs", "first"), final_margin_home=("final_margin_home", "first"),
        r1=("r1", "first"), r5=("r5", "first"))
    agg["pre_n_fills"] = pre.groupby("condition_id").size()
    agg["pre_usd"] = pre.groupby("condition_id").usd.sum()
    agg["pre_p_event"] = pre60.groupby("condition_id").p_event.median()
    agg["pre_p_event_all"] = pre.groupby("condition_id").p_event.median()
    agg = agg.fillna({"pre_n_fills": 0, "pre_usd": 0.0}).reset_index()
    meta = pd.read_parquet(OUT / "meta.parquet")[["condition_id", "question"]]
    agg = agg.merge(meta, on="condition_id", how="left")
    agg.to_parquet(OUT / "market_summary.parquet", index=False)
    return agg


# --------------------------------------------------------------------------- step 5b: play panel

def build_play_panel() -> pd.DataFrame:
    """One row per (derivative market x in-play plate appearance), with an execution-realistic entry.

    For each play that ends at real time T while the market is open:
      p_before   the canonical-event price implied by the last print BEFORE the play (chain ts <= T,
                 i.e. traded at real time <= T - 2.6 s, so it cannot contain the play)
      ent{0,1}_* the first print acquiring outcome 0 / outcome 1 at chain ts >= T + 3 + 2.6 s
                 (decision at T, act 3 s later, on-chain settlement lags another 2.6 s)
    That is the entry a taker could actually have got after seeing the play.
    """
    p = pd.read_parquet(OUT / "panel.parquet")
    games_raw = pd.read_parquet(OUT / "games.parquet")
    _load_ml(games_raw)
    games = games_raw.set_index("event_slug")
    cols = ["game_pk", "play_idx", "inning", "half", "start_ts", "end_ts", "contact_ts", "event",
            "event_type", "is_scoring", "rbi", "away_score", "home_score", "outs",
            "on_1b", "on_2b", "on_3b"]
    plays_cache: dict[int, pd.DataFrame] = {}
    out = []
    for cid, d in p.groupby("condition_id", sort=False):
        d = d.sort_values("ts")
        r0 = d.iloc[0]
        pk = int(r0.game_pk)
        if pk not in plays_cache:
            pl = pd.read_parquet(PLAYS / f"{pk}.parquet", columns=cols).sort_values("play_idx")
            pl["end_ts"] = np.maximum.accumulate(pl.end_ts.to_numpy(float))
            pl["runs_after"] = pl.away_score + pl.home_score
            pl["runs_before"] = pl.runs_after.shift(1).fillna(0)
            pl["away_before"] = pl.away_score.shift(1).fillna(0)
            pl["home_before"] = pl.home_score.shift(1).fillna(0)
            pl["outs_before"] = np.where(pl.half.ne(pl.half.shift(1)) | pl.inning.ne(pl.inning.shift(1)),
                                         0, pl.outs.shift(1).fillna(0))
            plays_cache[pk] = pl
        pl = plays_cache[pk]
        close = float(r0.mk_closed_ts) if pd.notna(r0.mk_closed_ts) else np.inf
        g = games.loc[r0.event_slug]
        # a market stops being live once its scoring window closes; those boundaries are calendar
        # facts about the game (end of the 1st / 5th inning), not outcome information.
        if r0.kind == "nrfi":
            close = min(close, float(g.r1_end_ts) if pd.notna(g.r1_end_ts) else np.inf)
        elif str(r0.kind).startswith("f5_"):
            close = min(close, float(g.r5_end_ts) if pd.notna(g.r5_end_ts) else np.inf)
        sel = pl[(pl.end_ts >= float(r0.game_start_ts)) & (pl.end_ts <= close)]
        if sel.empty:
            continue
        T = sel.end_ts.to_numpy(float)
        ts, pe = d.ts.to_numpy(float), d.p_event.to_numpy(float)
        i = np.searchsorted(ts, T, side="right") - 1
        ok = i >= 0
        j = np.clip(i, 0, len(ts) - 1)
        row = {
            "condition_id": cid, "market_slug": r0.market_slug, "kind": r0.kind, "line": r0.line,
            "event_slug": r0.event_slug, "game_pk": pk, "month": r0.month,
            "holdout": bool(r0.game_holdout), "fee_rate": r0.fee_rate,
            "y0": r0.y0, "y1": r0.y1, "won_event": r0.won_event, "yes_means": r0.yes_means,
            "event_flipped": bool((r0.kind == "nrfi") and (r0.yes_means == "no_run")),
            "play_idx": sel.play_idx.to_numpy(), "inning": sel.inning.to_numpy(),
            "half_bottom": (sel.half == "bottom").to_numpy(), "play_end_ts": T,
            "play_start_ts": sel.start_ts.to_numpy(float), "contact_ts": sel.contact_ts.to_numpy(float),
            "event": sel.event.to_numpy(), "event_type": sel.event_type.to_numpy(),
            "is_scoring": sel.is_scoring.to_numpy(), "rbi": sel.rbi.to_numpy(),
            "runs_before": sel.runs_before.to_numpy(), "runs_after": sel.runs_after.to_numpy(),
            "away_before": sel.away_before.to_numpy(), "home_before": sel.home_before.to_numpy(),
            "away_after": sel.away_score.to_numpy(), "home_after": sel.home_score.to_numpy(),
            "outs_before": sel.outs_before.to_numpy(), "outs_after": sel.outs.to_numpy(),
            "on_1b": sel.on_1b.to_numpy(), "on_2b": sel.on_2b.to_numpy(), "on_3b": sel.on_3b.to_numpy(),
            "home_scored": (sel.home_score.to_numpy() > sel.home_before.to_numpy()),
            "away_scored": (sel.away_score.to_numpy() > sel.away_before.to_numpy()),
            "p_before": np.where(ok, pe[j], np.nan),
            "p_before_age": np.where(ok, T - ts[j], np.nan),
        }
        k = np.searchsorted(ts, T + 3 + CHAIN_LAG, side="left")
        has = k < len(ts)
        kk = np.clip(k, 0, len(ts) - 1)
        row["entry_p"] = np.where(has, pe[kk], np.nan)
        row["entry_ts"] = np.where(has, ts[kk], np.nan)
        for sd in (0, 1):
            msk = d.s.to_numpy() == sd
            t_s, q_s, z_s = ts[msk], d.q.to_numpy(float)[msk], d["size"].to_numpy(float)[msk]
            if len(t_s):
                ks = np.searchsorted(t_s, T + 3 + CHAIN_LAG, side="left")
                hs = ks < len(t_s)
                kc = np.clip(ks, 0, len(t_s) - 1)
                row[f"ent{sd}_ts"] = np.where(hs, t_s[kc], np.nan)
                row[f"ent{sd}_q"] = np.where(hs, q_s[kc], np.nan)
                row[f"ent{sd}_size"] = np.where(hs, z_s[kc], np.nan)
            else:
                for suf in ("ts", "q", "size"):
                    row[f"ent{sd}_{suf}"] = np.nan
        mls, mlv = _ml_arrays(g)
        row["ml_p_home_before"], row["ml_p_home_age"] = _asof(mls, mlv, T)
        row["final_runs"], row["final_margin_home"] = g.final_runs, g.final_margin_home
        row["r1"], row["r5"] = g.r1, g.r5
        out.append(pd.DataFrame(row))
    pp = pd.concat(out, ignore_index=True)
    pp.to_parquet(OUT / "play_panel.parquet", index=False)
    print(f"[play_panel] {len(pp):,} (market x play) rows; price before the play on "
          f"{float(pp.p_before.notna().mean()):.1%}, executable entry (>=3 s later) on "
          f"{float(pp.entry_p.notna().mean()):.1%}")
    return pp


_ML_CACHE: dict = {}


def _load_ml(games: pd.DataFrame) -> None:
    """Load the local moneyline tapes for every game once: code -> (ts, implied home prob)."""
    codes = sorted({int(x) for x in games.m.dropna()})
    d = C.fills(markets=codes, columns=["m", "ts", "s", "q"]).sort_values(["m", "ts"])
    hi = games.dropna(subset=["m"]).set_index(games.dropna(subset=["m"]).m.astype(int)).home_outcome_idx
    for code, sub in d.groupby("m"):
        h = int(hi.get(code, 0))
        _ML_CACHE[int(code)] = (sub.ts.to_numpy(float),
                                np.where(sub.s.to_numpy() == h, sub.q.to_numpy(float),
                                         1 - sub.q.to_numpy(float)))


def _ml_arrays(g) -> tuple[np.ndarray, np.ndarray]:
    """(ts, implied home prob) of the local moneyline tape for one game."""
    if pd.isna(g.m):
        return np.array([]), np.array([])
    return _ML_CACHE.get(int(g.m), (np.array([]), np.array([])))


# --------------------------------------------------------------------------- step 6: validation

def validate() -> None:
    games = pd.read_parquet(OUT / "games.parquet")
    menu = pd.read_parquet(OUT / "menu.parquet")
    p = pd.read_parquet(OUT / "panel.parquet")
    line = "-" * 78
    print(line + "\nVALIDATION\n" + line)

    # 1. play-by-play sanity over every game we could join
    cols = ["game_pk", "play_idx", "inning", "start_ts", "end_ts", "away_score", "home_score"]
    d = pd.concat([pd.read_parquet(PLAYS / f"{pk}.parquet", columns=cols)
                   for pk in games.game_pk], ignore_index=True).sort_values(["game_pk", "play_idx"])
    bad_score = d.groupby("game_pk")[["away_score", "home_score"]].apply(
        lambda x: bool((x.diff().fillna(0) < 0).any().any()))
    bad_clock = d.groupby("game_pk").apply(lambda x: bool((x.end_ts.diff().fillna(0) < 0).any()),
                                           include_groups=False)
    print(f"1. scores monotonic in {int((~bad_score).sum()):,}/{len(bad_score):,} games; "
          f"end_ts non-decreasing in {int((~bad_clock).sum()):,}/{len(bad_clock):,} games "
          f"(inversions are 1-2 s; the panel forces a monotone clock)")
    print(f"   start_ts <= end_ts on {float((d.start_ts <= d.end_ts).mean()):.5f} of plays; "
          f"final score from plays == boxscore on "
          f"{float((games.final_runs == games.away_score + games.home_score).mean()):.4f} of games")

    # 2. resolution semantics vs the play-by-play
    m = menu.merge(games[["event_slug", "r1", "r5", "final_runs", "final_margin_home", "final_inning"]],
                   on="event_slug")
    m = m[m.y0.notna() & (m.y0 + m.y1 == 1)]
    checks = {
        "total  Over==runs>line": ((m.kind == "total"), (m.final_runs > m.line)),
        "f5     Over==r5>line": ((m.kind == "f5_total"), (m.r5 > m.line)),
        "sp_home  home-line": ((m.kind == "spread_home"), (m.final_margin_home > m.line)),
        "sp_away  away-line": ((m.kind == "spread_away"), (-m.final_margin_home > m.line)),
        "extra    >9 innings": ((m.kind == "extra_innings"), (m.final_inning > 9)),
    }
    print("2. payout semantics (outcome 0 wins iff ...):")
    for name, (sel, cond) in checks.items():
        sub = m[sel]
        print(f"   {name:24s} n={len(sub):6,}  agree={float(((sub.y0 == 1) == cond[sel]).mean()):.5f}")
    nr = p[p.kind == "nrfi"].drop_duplicates("condition_id")
    if len(nr):
        agree = float((nr.won_event == (nr.r1 > 0)).mean())
        print(f"   {'nrfi canonical event':24s} n={len(nr):6,}  agree={agree:.5f}  "
              f"(orientation from Gamma text: {nr.yes_means.value_counts().to_dict()})")

    # 3. trading window
    inwin = (p.ts >= p.game_start_ts - 30 * 86400) & (p.ts <= p.mk_closed_ts + 3600)
    print(f"3. fills inside [listing-30d, close+1h]: {float(inwin.mean()):.5f}; "
          f"in-play share {float(p.in_play.mean()):.3f}; "
          f"after resolution {float((p.ts > p.mk_closed_ts).mean()):.5f}")
    ip = p[p.in_play]
    print(f"   in-play fills with a completed play behind them: {float((ip.play_idx >= 0).mean()):.4f}; "
          f"median seconds since the last play end: {float(ip.since_play_s.median()):.0f}s")

    # 4. moneyline price alignment
    for age in (60, 300, 900):
        print(f"4. in-play fills with a moneyline print within {age:4d}s: "
              f"{float((ip.ml_p_home_age <= age).mean()):.4f}")
    both = ip[(ip.ml_ask_home_age <= 300) & (ip.ml_bid_home_age <= 300)]
    if len(both):
        sp = (both.ml_ask_home - both.ml_bid_home)
        print(f"   implied moneyline spread (ask_home - bid_home), n={len(both):,}: "
              f"median {sp.median():.3f}, p90 {sp.quantile(.9):.3f}")

    # 4b. every fill price is a TAKER ASK on the side that taker acquired: p_event is an ask when
    #     the taker bought the canonical event and a bid when they sold it. Ignoring this makes the
    #     "market underprices X" illusion.
    pe = p[p.p_event.notna()]
    ask = pe[pe.taker_on_event].groupby("kind").p_event.mean()
    bid = pe[~pe.taker_on_event].groupby("kind").p_event.mean()
    cnt = pe.groupby("kind").taker_on_event.mean()
    print("4b. p_event is an ASK when taker_on_event else a BID -- mean by kind:")
    print(pd.DataFrame({"ask_side_mean": ask, "bid_side_mean": bid,
                        "share_taking_the_event": cnt}).round(3).to_string().replace("\n", "\n    "))

    # 5. spot checks: does the state actually move the derivative price?
    print("5. spot checks (does a run move the price?):")
    nr = p[(p.kind == "nrfi") & p.in_play & p.p_event.notna()]
    if len(nr):
        a = nr[(nr.r1_so_far == 0) & (~nr.r1_settled)].p_event
        b = nr[(nr.r1_so_far > 0)].p_event
        print(f"   YRFI prob before any 1st-inning run: {a.mean():.3f} (n={len(a):,}); "
              f"after a run scored: {b.mean():.3f} (n={len(b):,})")
    to = p[(p.kind == "total") & p.in_play & p.p_event.notna()].copy()
    if len(to):
        to["need"] = to.line - to.runs_so_far
        g = to.groupby(pd.cut(to.need, [-99, 0, 2, 4, 6, 99]), observed=True).p_event.agg(["mean", "size"])
        print("   total: mean Over price by runs still needed (line - runs so far)")
        print(g.to_string().replace("\n", "\n     "))
    sh = p[(p.kind == "spread_home") & p.in_play & p.ml_p_home.notna()]
    if len(sh):
        c = sh[["p_event", "ml_p_home"]].corr().iloc[0, 1]
        print(f"   spread_home: corr(run-line price, moneyline home price) in-play = {c:.3f} "
              f"(n={len(sh):,}); mean run-line {sh.p_event.mean():.3f} < moneyline {sh.ml_p_home.mean():.3f}: "
              f"{bool(sh.p_event.mean() < sh.ml_p_home.mean())}")

    # 6. coverage
    print("6. coverage")
    s = menu[menu.sampled]
    got = {f.stem for f in TRADE_DIR.glob("*.parquet")}
    nz = p.condition_id.nunique()
    print(f"   sampled markets {len(s):,}; fetched {len(got & set(s.condition_id)):,}; "
          f"with >=1 fill {nz:,} ({nz / len(s):.1%})")
    print(f"   games in panel {p.game_pk.nunique():,} of {s.event_slug.nunique():,} sampled "
          f"({games.event_slug.nunique():,} MLB games have play-by-play, "
          f"{menu.event_slug.nunique():,} of them have any derivative market)")
    dev, hold = p[p.dev], p[p.game_holdout]
    print(f"   dev (<2026-07-01): {len(dev):,} fills / {dev.game_pk.nunique():,} games; "
          f"holdout: {len(hold):,} fills / {hold.game_pk.nunique():,} games")
    p = p.assign(usd=p["size"] * p.q)
    print(p.groupby("kind").agg(markets=("condition_id", "nunique"), fills=("q", "size"),
                                taker_usd=("usd", "sum")).to_string())
    per_mkt = p.groupby(["kind", "condition_id"]).size().groupby("kind").describe()[
        ["count", "25%", "50%", "75%", "max"]]
    print("   fills per market:")
    print(per_mkt.to_string().replace("\n", "\n   "))
    if (OUT / "play_panel.parquet").exists():
        pp = pd.read_parquet(OUT / "play_panel.parquet")
        print(f"   play panel: {len(pp):,} (market x play) rows")
        cov = pp.groupby("kind").agg(rows=("p_before", "size"),
                                     have_price_before=("p_before", lambda x: float(x.notna().mean())),
                                     have_entry=("entry_p", lambda x: float(x.notna().mean())))
        cov["have_both"] = pp.assign(b=pp.p_before.notna() & pp.entry_p.notna()).groupby("kind").b.mean()
        print(cov.to_string().replace("\n", "\n   "))

        # 7. the decisive alignment test: a run must move the price between p_before and entry_p
        print("7. price reaction to the play (p_event before the play -> first print >=3 s after):")
        d = pp[pp.p_before.notna() & pp.entry_p.notna() & (pp.p_before_age <= 600)].copy()
        d["move"] = d.entry_p - d.p_before
        d["scored"] = d.runs_after > d.runs_before
        for kind, sub in d.groupby("kind"):
            a = sub[sub.scored].move
            b = sub[~sub.scored].move
            print(f"   {kind:12s} run scored: {a.mean():+.4f} (n={len(a):,})   "
                  f"no run: {b.mean():+.4f} (n={len(b):,})")
        sh = d[d.kind == "spread_home"]
        if len(sh):
            print(f"   spread_home by scorer -> home scored: {sh[sh.home_scored].move.mean():+.4f} "
                  f"(n={int(sh.home_scored.sum()):,}); away scored: "
                  f"{sh[sh.away_scored].move.mean():+.4f} (n={int(sh.away_scored.sum()):,}); "
                  f"neither: {sh[~(sh.home_scored | sh.away_scored)].move.mean():+.4f}")
        print("   (totals close the instant the line is passed, so Over-resolving markets contribute "
              "fewer, larger up-moves: do not read an unconditional per-play drift off this panel)")
        n1 = d[(d.kind == "nrfi") & (d.inning == 1)]
        if len(n1):
            a, b = n1[n1.scored].move, n1[~n1.scored].move
            print(f"   nrfi, 1st inning only -> run: {a.mean():+.4f} (n={len(a):,}); "
                  f"no run: {b.mean():+.4f} (n={len(b):,})")


def main(step: str) -> None:
    if step in ("games", "all"):
        build_games()
    if step in ("select", "all"):
        select_markets()
    if step in ("meta", "all"):
        fetch_meta()
    if step in ("fetch", "all"):
        fetch_trades()
    if step in ("panel", "all"):
        build_panel()
    if step in ("play_panel", "panel", "all"):
        build_play_panel()
    if step in ("validate", "all"):
        validate()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "all")
