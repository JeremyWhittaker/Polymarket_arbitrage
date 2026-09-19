"""MLB run line: is home -1.5 structurally rich because of walk-off / no-bottom-9th truncation?

Hypothesis (pre-registered, see reports/research/mlb_runline_home_trunc.md):
  Two baseball rules cap home margins: a leading home team never bats in the bottom of the 9th,
  and a walk-off ends the game when the winning run scores (except a home run). At the same
  moneyline price a home favourite therefore covers -1.5 far less often than an away favourite.
  If Polymarket run-line makers convert moneyline to run line with a symmetric margin model,
  home -1.5 YES is overpriced and away -1.5 YES is cheap.

PRIMARY RULE (frozen before the holdout was evaluated):
  FV model  logistic regression of cover(team laying 1.5 wins by >= 2) on logit(p_team), home and
            logit(p_team)*home. p_team = pregame moneyline price of that team (data/mlb/pregame.parquet
            pre_p, oriented). Finals from MLB. Fit on every game whose first pitch is before
            2026-07-01 (minus 5 h so the outcome is also before the boundary); frozen.
  Markets   2026 MLB `spread-home-1pt5` and `spread-away-1pt5` markets of games whose moneyline
            pre_usd >= $25k. A seeded random sample of games (<= 1,000 run-line markets per period,
            Data API budget) is fetched with pmsports.polymarket.trades(cid, T-24h, closed_ts).
  Decision  D = actual first pitch - 30 min. p_team = median oriented moneyline fill price in
            [D-10 min, D] (local fills). FV_yes = model P(cover). P_yes = median Yes-converted
            run-line fill price in [D-120 min, D] (skip if < 2 fills).
  Signal    thr = 0.02 + fee_rate*P_yes*(1-P_yes). P_yes >= FV_yes + thr -> buy NO (the +1.5 side);
            P_yes <= FV_yes - thr -> buy YES. One bet per game: the larger |edge| if both markets signal.
  Entry     first run-line fill on our side with ts in [D+3 s, first pitch), at its price plus the
            taker fee (market fee_rate). Hold to resolution (market payout).
  Metric    C.taker_roi per $1, CI clustered by game; +1c slippage sensitivity.

Variants: (a) naive structural NO home -1.5 / YES away -1.5, (b) thresholds 0.01 / 0.04,
(c) pricing diagnostics vs a symmetric model and vs realized cover, (d) in-play walk-off
(bottom 9th+ with home diff in {0,-1}: buy away +1.5 after a home -1.5 print >= FV + 0.05),
(e) +1c slippage everywhere. Robustness: walk-forward FV model (fit on 2025 only) for DEV,
decision anchored on the scheduled start.

Run:
  systemd-run --user --scope -q -p MemoryMax=4G -p MemorySwapMax=0 \
      .venv/bin/python -m pmsports.research.h_mlb_runline_home_trunc [--fetch] [--dev-only]

  --fetch      fetch (or top up) the run-line tapes of the sample (Data API, 5 req/s)
  --dev-only   evaluate development games only (scheduled start < 2026-07-01)
  (default)    development + the single holdout evaluation

Outputs: data/research/h_mlb_runline_home_trunc/{sample.parquet, tapes/, features.parquet,
         bets_*.parquet, results.json}
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

from pmsports.research import common as C

log = logging.getLogger("h_mlb_runline_home_trunc")

OUT = C.RESEARCH / "h_mlb_runline_home_trunc"
TAPES = OUT / "tapes_v2"          # v1 (tapes/) lacked the asset column; see the report
# 115 run-line tapes fetched earlier in this session (window game_start-6h .. closed+600 s).
# Reused only when the market happens to fall into the random sample below.
SCRATCH_TAPES = Path("/tmp/claude-1000/-mnt-data-projects-polymarket-arbitrage/"
                     "a3336124-029d-461c-8aa7-6f1b04b98478/scratchpad/mlb")

HOLDOUT_TS = pd.Timestamp("2026-07-01", tz="UTC").timestamp()
Y2026_TS = pd.Timestamp("2026-01-01", tz="UTC").timestamp()
PRE_USD_MIN = 25_000.0
BUDGET_PER_PERIOD = 1000      # run-line markets fetched per period (2,000 total, Data API budget)
SEED = 20260701
DEC_OFFSET = 1800.0           # D = first pitch - 30 min
ML_WIN = 600.0                # moneyline price window [D-10 min, D]
RL_WIN = 7200.0               # run-line price window [D-120 min, D]
MIN_RL_FILLS = 2
LAG = 3.0                     # entry >= decision + 3 s
THR = 0.02                    # primary threshold (plus the fee term)
WO_EDGE = 0.05                # variant d trigger: home -1.5 print >= FV + 0.05
WO_START = 30.0               # variant d window starts state_ts + 30 s
TRAIN_END_PAD = 5 * 3600.0    # a training game must have first pitch + 5 h < 2026-07-01
TAPE_COLS = ["timestamp", "side", "asset", "outcomeIndex", "outcome", "price", "size"]


# ----------------------------------------------------------------------------- universe

def runline_universe() -> pd.DataFrame:
    """Every MLB +-1.5 run-line market joined to its moneyline market, MLB game_pk, finals and
    actual first pitch. One row per run-line market. `team` = the team laying 1.5 (outcome 0)."""
    u = C.universe(columns=["condition_id", "token_id", "outcome", "outcome_idx", "payout", "family", "market_type",
                            "game_start_ts", "closed_ts", "event_slug", "market_slug", "fee_rate"])
    u = u[(u.family == "baseball") & (u.market_type == "spreads")
          & u.market_slug.str.contains(r"-spread-(?:home|away)-1pt5$", regex=True)]
    meta = u.drop_duplicates("condition_id").set_index("condition_id")[
        ["event_slug", "market_slug", "game_start_ts", "closed_ts", "fee_rate"]]
    o = u.pivot_table(index="condition_id", columns="outcome_idx", values="outcome", aggfunc="first")
    y = u.pivot_table(index="condition_id", columns="outcome_idx", values="payout", aggfunc="first")
    tk = u.pivot_table(index="condition_id", columns="outcome_idx", values="token_id", aggfunc="first")
    rl = (meta.join(o.rename(columns={0: "o0", 1: "o1"})).join(y.rename(columns={0: "y0", 1: "y1"}))
          .join(tk.rename(columns={0: "t0", 1: "t1"})).reset_index())
    rl["side"] = rl.market_slug.str.extract(r"-spread-(home|away)-1pt5$")[0]

    ml = C.markets()
    ml = ml[ml.family == "baseball"][["m", "condition_id", "event_slug", "pre_usd", "o0", "o1"]].rename(
        columns={"condition_id": "ml_cid", "o0": "ml_o0", "o1": "ml_o1"})
    x = rl.merge(ml, on="event_slug", how="inner")
    g = pd.read_parquet(C.DATA / "mlb" / "games.parquet",
                        columns=["condition_id", "game_pk", "home_outcome_idx", "home_name", "away_name",
                                 "home_score", "away_score", "status", "game_type", "resolution_mismatch"])
    g = g[g.game_pk.notna()].rename(columns={"condition_id": "ml_cid"})
    x = x.merge(g, on="ml_cid", how="inner")
    x["game_pk"] = x.game_pk.astype("int64")
    pg = pd.read_parquet(C.DATA / "mlb" / "pregame.parquet", columns=["game_pk", "first_pitch_ts"])
    x = x.merge(pg.drop_duplicates("game_pk"), on="game_pk", how="left")
    x["pre_usd"] = x.pre_usd.fillna(0.0)
    x["fee_rate"] = x.fee_rate.fillna(0.0)
    x["home"] = (x.side == "home").astype(int)
    x["team"] = np.where(x.home == 1, x.home_name, x.away_name)
    x["orient_ok"] = x.o0 == x.team
    # moneyline orientation: home_outcome_idx must point at the home team's name
    ml_home_name = np.where(x.home_outcome_idx == 0, x.ml_o0, x.ml_o1)
    x["ml_orient_ok"] = [str(a).lower().split()[-1] in str(b).lower() or str(b).lower().split()[-1] in str(a).lower()
                         for a, b in zip(ml_home_name, x.home_name)]
    marg = x.home_score - x.away_score
    x["margin_team"] = np.where(x.home == 1, marg, -marg)
    x["cover_mlb"] = (x.margin_team >= 2).astype(float).where(x.home_score.notna())
    x["period"] = np.where(x.game_start_ts >= HOLDOUT_TS, "HOLDOUT",
                           np.where(x.game_start_ts >= Y2026_TS, "DEV", "PRE2026"))
    return x.reset_index(drop=True)


def draw_sample(x: pd.DataFrame) -> pd.DataFrame:
    """Seeded random sample of eligible 2026 games (pre-decision information only: moneyline
    pre_usd, scheduled date, market listing). Budget <= BUDGET_PER_PERIOD markets per period."""
    e = x[(x.period != "PRE2026") & (x.pre_usd >= PRE_USD_MIN) & x.first_pitch_ts.notna()]
    # data hygiene: one event per MLB game_pk (games.parquet maps a few doubleheaders / reschedules
    # of two different events onto one game_pk), and first pitch within 1 h of the scheduled start
    # (earlier = mis-mapped game; later = a rain delay, already visible at D = first pitch - 30 min)
    amb = e.groupby("game_pk").event_slug.nunique()
    e = e[e.game_pk.isin(amb[amb == 1].index)]
    e = e[(e.first_pitch_ts - e.game_start_ts).abs() <= 3600]
    rng = np.random.default_rng(SEED)
    keep = []
    for per in ("DEV", "HOLDOUT"):
        ep = e[e.period == per]
        n_by_game = ep.groupby("game_pk").size()
        games = n_by_game.index.to_numpy()
        games = games[rng.permutation(len(games))]
        cum = np.cumsum(n_by_game.loc[games].to_numpy())
        keep.extend(games[cum <= BUDGET_PER_PERIOD])
    s = e[e.game_pk.isin(keep)].copy()
    log.info("eligible markets %s games %s -> sample markets %s games %s",
             e.groupby("period").size().to_dict(), e.groupby("period").game_pk.nunique().to_dict(),
             s.groupby("period").size().to_dict(), s.groupby("period").game_pk.nunique().to_dict())
    return s


# ----------------------------------------------------------------------------- fetch

def fetch_tapes(s: pd.DataFrame, workers: int = 4) -> None:
    from pmsports import http as H
    H.HOST_RPS["data-api.polymarket.com"] = 5.0
    from pmsports import polymarket as PM
    TAPES.mkdir(parents=True, exist_ok=True)
    todo = [r for r in s.itertuples(index=False) if not (TAPES / f"{r.condition_id}.parquet").exists()]
    log.info("tapes to fetch: %d of %d", len(todo), len(s))

    def one(r):
        fn = TAPES / f"{r.condition_id}.parquet"
        sc = SCRATCH_TAPES / f"{r.condition_id}.parquet"
        if sc.exists():
            d = pd.read_parquet(sc)
            src = "scratch"
        else:
            d = pd.DataFrame(PM.trades(r.condition_id, int(r.game_start_ts - 86400), int(r.closed_ts)))
            src = "api"
        d = d.reindex(columns=TAPE_COLS) if len(d) else pd.DataFrame({c: [] for c in TAPE_COLS})
        d["timestamp"] = d.timestamp.astype("int64")
        d["side"] = d.side.astype(str)
        d["asset"] = d.asset.astype(str)
        d["outcome"] = d.outcome.astype(str)
        d["outcomeIndex"] = d.outcomeIndex.astype("int8")
        d["price"] = d.price.astype(float)
        d["size"] = d["size"].astype(float)
        tmp = fn.with_suffix(".tmp")
        d.to_parquet(tmp, index=False)
        os.replace(tmp, fn)
        return src, len(d)

    done = 0
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(one, r): r.condition_id for r in todo}
        for f in as_completed(futs):
            try:
                f.result()
            except Exception as e:  # keep going; a rerun tops up the missing tapes
                log.warning("fetch failed %s: %s", futs[f], e)
            done += 1
            if done % 50 == 0:
                log.info("fetched %d / %d", done, len(todo))


def load_tape(r) -> pd.DataFrame | None:
    """Run-line tape as 'taker acquired side acq at price q' (acq 0 = the team laying 1.5).

    Orientation comes from the traded token id (`asset`) matched to the universe token ids t0/t1,
    NOT from the Data API `outcomeIndex`: on 2026-05-13/14 several run-line markets report an
    outcomeIndex that is flipped relative to the resolved outcome/payout (their last prints sit at
    1 - payout). Rows whose asset matches neither token are dropped."""
    fn = TAPES / f"{r.condition_id}.parquet"
    if not fn.exists():
        return None
    d = pd.read_parquet(fn)
    d = d[d["size"] > 0]
    idx = np.where(d.asset.to_numpy() == str(r.t0), 0, np.where(d.asset.to_numpy() == str(r.t1), 1, -1))
    d = d[idx >= 0]
    idx = idx[idx >= 0]
    buy = (d.side == "BUY").to_numpy()
    px = d.price.to_numpy(float)
    t = pd.DataFrame({"ts": d.timestamp.to_numpy(float), "acq": np.where(buy, idx, 1 - idx),
                      "q": np.where(buy, px, 1 - px), "size": d["size"].to_numpy(float),
                      "api_idx_flip": d.outcomeIndex.to_numpy().astype(int) != idx})
    t["yes"] = np.where(t.acq == 0, t.q, 1 - t.q)       # Yes-converted price
    return t.sort_values("ts", kind="stable").reset_index(drop=True)


def tape_audit(s: pd.DataFrame) -> pd.DataFrame:
    """Per market: rows, rows with an unknown asset, share where the API outcomeIndex disagrees with
    the token's universe index, and whether the Data API outcome name agrees with the token's name."""
    out = []
    for r in s.itertuples(index=False):
        fn = TAPES / f"{r.condition_id}.parquet"
        if not fn.exists():
            continue
        d = pd.read_parquet(fn)
        idx = np.where(d.asset.to_numpy() == str(r.t0), 0, np.where(d.asset.to_numpy() == str(r.t1), 1, -1))
        known = idx >= 0
        name_u = np.where(idx == 0, r.o0, r.o1)
        out.append({"condition_id": r.condition_id, "n": len(d), "unknown_asset": int((~known).sum()),
                    "api_idx_flip": float((d.outcomeIndex.to_numpy()[known] != idx[known]).mean()) if known.any() else np.nan,
                    "name_mismatch": float((d.outcome.to_numpy()[known] != name_u[known]).mean()) if known.any() else np.nan})
    return pd.DataFrame(out)


# ----------------------------------------------------------------------------- FV models

def _logit(p):
    p = np.clip(np.asarray(p, float), 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


def training_rows(end_ts: float, start_ts: float = 0.0) -> pd.DataFrame:
    """Two rows per finished game (home laying 1.5, away laying 1.5) with pregame moneyline price."""
    pg = pd.read_parquet(C.DATA / "mlb" / "pregame.parquet", columns=["game_pk", "first_pitch_ts", "pre_p"])
    mg = pd.read_parquet(C.DATA / "mlb" / "mlb_games.parquet", columns=["game_pk", "home_score", "away_score", "status"])
    x = pg.merge(mg, on="game_pk", how="inner")
    x = x[x.status.isin(["Final", "Completed Early"]) & x.pre_p.between(0.03, 0.97)
          & (x.first_pitch_ts >= start_ts) & (x.first_pitch_ts + TRAIN_END_PAD < end_ts)]
    marg = x.home_score - x.away_score
    h = pd.DataFrame({"game_pk": x.game_pk, "p": x.pre_p, "home": 1, "cover": (marg >= 2).astype(int)})
    a = pd.DataFrame({"game_pk": x.game_pk, "p": 1 - x.pre_p, "home": 0, "cover": (-marg >= 2).astype(int)})
    return pd.concat([h, a], ignore_index=True)


def _design(p, home, symmetric=False):
    lp = _logit(p)
    home = np.asarray(home, float)
    if symmetric:
        return np.column_stack([np.ones_like(lp), lp])
    return np.column_stack([np.ones_like(lp), lp, home, lp * home])


def fit_fv(rows: pd.DataFrame, symmetric=False) -> dict:
    import statsmodels.api as sm
    X = _design(rows.p, rows.home, symmetric)
    res = sm.Logit(rows.cover.to_numpy(float), X).fit(disp=0)
    return {"beta": res.params.tolist(), "se": res.bse.tolist(), "n_rows": int(len(rows)),
            "n_games": int(rows.game_pk.nunique()), "symmetric": symmetric}


def fv_predict(model: dict, p, home) -> np.ndarray:
    X = _design(p, home, model["symmetric"])
    return 1 / (1 + np.exp(-(X @ np.asarray(model["beta"]))))


# ----------------------------------------------------------------------------- pregame features

def ml_price_at(s: pd.DataFrame, anchor_col: str) -> pd.Series:
    """Median home-team moneyline price from local fills in [D-10 min, D] per game (D = anchor - 30 min)."""
    games = s.drop_duplicates("game_pk")[["game_pk", "m", "home_outcome_idx", anchor_col]]
    f = C.fills(markets=games.m.unique(), columns=["m", "ts", "s", "q"])
    f["p0"] = np.where(f.s == 0, f.q, 1 - f.q)
    out = {}
    for g in games.itertuples(index=False):
        D = getattr(g, anchor_col) - DEC_OFFSET
        ff = f[f.m == g.m]
        w = ff[(ff.ts >= D - ML_WIN) & (ff.ts <= D)]
        if len(w) == 0:
            out[g.game_pk] = (np.nan, 0)
            continue
        p0 = float(np.median(w.p0))
        out[g.game_pk] = (p0 if g.home_outcome_idx == 0 else 1 - p0, len(w))
    return pd.DataFrame.from_dict(out, orient="index", columns=["p_home_ml", "n_ml"])


def market_features(s: pd.DataFrame, anchor_col: str = "first_pitch_ts") -> pd.DataFrame:
    """Per run-line market: P_yes, first entry prints on each side, capacity near those prints."""
    rows = []
    for r in s.itertuples(index=False):
        t = load_tape(r)
        fp = r.first_pitch_ts
        D = getattr(r, anchor_col) - DEC_OFFSET
        rec = {"condition_id": r.condition_id, "have_tape": t is not None}
        if t is None:
            rows.append(rec)
            continue
        w = t[(t.ts >= D - RL_WIN) & (t.ts <= D)]
        rec["n_rl"] = len(w)
        rec["P_yes"] = float(np.median(w.yes)) if len(w) >= MIN_RL_FILLS else np.nan
        rec["n_rl_tape"] = len(t)
        ent = t[(t.ts >= D + LAG) & (t.ts < fp)]
        for side in (0, 1):
            e = ent[ent.acq == side]
            if len(e):
                q0 = float(e.q.iloc[0])
                rec[f"entry_q{side}"] = q0
                rec[f"entry_ts{side}"] = float(e.ts.iloc[0])
                rec[f"entry_sz{side}"] = float(e["size"].iloc[0])
                near = e[e.q <= q0 + 0.01]                 # $ printed on our side within +1c
                rec[f"cap_usd{side}"] = float((near.q * near["size"]).sum())
            else:
                rec[f"entry_q{side}"] = np.nan
        rows.append(rec)
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- rules

def apply_rule(F: pd.DataFrame, thr: float, fv_col: str = "FV") -> pd.DataFrame:
    """Signal per market, one bet per game (largest |edge| among signalling markets), entry at the
    first print on our side in [D+3 s, first pitch)."""
    x = F[F.P_yes.notna() & F[fv_col].notna()].copy()
    x["edge"] = x.P_yes - x[fv_col]
    x["need"] = thr + x.fee_rate * x.P_yes * (1 - x.P_yes)
    x["bet_side"] = np.where(x.edge >= x.need, 1, np.where(x.edge <= -x.need, 0, -1))
    sig = x[x.bet_side >= 0].copy()
    sig = sig.assign(abs_edge=sig.edge.abs()).sort_values(["game_pk", "abs_edge"], ascending=[True, False])
    sig = sig.drop_duplicates("game_pk", keep="first")
    return _enter(sig)


def _enter(sig: pd.DataFrame) -> pd.DataFrame:
    sig = sig.copy()
    b = sig.bet_side.to_numpy()
    sig["entry_q"] = np.where(b == 0, sig.entry_q0, sig.entry_q1)
    sig["won"] = np.where(b == 0, sig.y0, sig.y1)
    sig["cap_usd"] = np.where(b == 0, sig.get("cap_usd0", np.nan), sig.get("cap_usd1", np.nan))
    sig["signalled"] = True
    bets = sig[sig.entry_q.notna()].copy()
    bets["roi"] = C.taker_roi(bets.entry_q, bets.won, bets.fee_rate)
    bets["roi_s1"] = C.taker_roi(bets.entry_q, bets.won, bets.fee_rate, slip=0.01)
    bets.attrs["n_signals"] = len(sig)
    return bets


def summarize(bets: pd.DataFrame, col: str = "roi") -> dict:
    if len(bets) == 0:
        return {"bets": 0, "roi": float("nan"), "ci_lo": float("nan"), "ci_hi": float("nan")}
    m, lo, hi = C.cluster_ci(bets[col], bets.game_pk)
    fee = C.taker_fee(1.0, bets.entry_q, bets.fee_rate)
    return {"bets": int(len(bets)), "games": int(bets.game_pk.nunique()), "roi": m, "ci_lo": lo, "ci_hi": hi,
            "dollars": float(len(bets)),              # $1 per bet
            "pnl": float(bets[col].sum()),
            "win_rate": float(bets.won.mean()), "avg_price": float(bets.entry_q.mean()),
            "avg_fee_per_share": float(fee.mean()),
            "signals": int(bets.attrs.get("n_signals", len(bets))),
            "cap_usd_median": float(np.nanmedian(bets.cap_usd)) if "cap_usd" in bets else float("nan")}


# ----------------------------------------------------------------------------- variant d (in-play)

def finals_all() -> pd.DataFrame:
    a = pd.read_parquet(C.DATA / "mlb" / "mlb_only" / "schedule_2021-04-01_2024-11-05.parquet",
                        columns=["game_pk", "home_score", "away_score", "status"])
    b = pd.read_parquet(C.DATA / "mlb" / "mlb_games.parquet", columns=["game_pk", "home_score", "away_score", "status"])
    f = pd.concat([b, a]).drop_duplicates("game_pk")
    return f[f.status.isin(["Final", "Completed Early"])]


def walkoff_fv() -> pd.DataFrame:
    """P(home wins by >= 2 | start of bottom half-inning >= 9th, home diff d) from 2021-2025 states."""
    b = pd.read_parquet(C.DATA / "mlb" / "baseline.parquet",
                        columns=["game_pk", "inning", "half", "diff", "checkpoint", "season"])
    b = b[b.checkpoint & (b.half == "bottom") & (b.inning >= 9) & b["diff"].isin([0, -1]) & (b.season <= 2025)]
    f = finals_all()
    b = b.merge(f, on="game_pk", how="inner")
    b["cover"] = ((b.home_score - b.away_score) >= 2).astype(float)
    b["extra"] = (b.inning >= 10).astype(int)
    t = b.groupby(["diff", "extra"]).agg(n=("cover", "size"), fv=("cover", "mean")).reset_index()
    return t


def walkoff_states(game_pks) -> pd.DataFrame:
    """Start of each bottom half-inning >= 9th with home diff in {0,-1}: state_ts (end of the 3rd-out
    PA of the top half) and next_contact_ts (contact of the first bottom-half PA)."""
    out = []
    for pk in game_pks:
        fn = C.DATA / "mlb" / "plays" / f"{pk}.parquet"
        if not fn.exists():
            continue
        p = pd.read_parquet(fn, columns=["play_idx", "inning", "half", "end_ts", "contact_ts", "start_ts",
                                         "outs", "home_score", "away_score"]).sort_values("play_idx")
        top3 = p[(p.half == "top") & (p.outs == 3) & (p.inning >= 9)]
        for r in top3.itertuples(index=False):
            d = r.home_score - r.away_score
            if d not in (0, -1):
                continue
            nxt = p[(p.inning == r.inning) & (p.half == "bottom")]
            if nxt.empty:
                continue
            c = nxt.contact_ts.iloc[0]
            c = c if np.isfinite(c) else nxt.end_ts.iloc[0]
            out.append((pk, int(r.inning), int(d), float(r.end_ts), float(c)))
    return pd.DataFrame(out, columns=["game_pk", "inning", "diff", "state_ts", "next_contact_ts"])


def variant_walkoff(s: pd.DataFrame, fv_tab: pd.DataFrame):
    hm = s[s.side == "home"].drop_duplicates("game_pk")
    st = walkoff_states(hm.game_pk.unique())
    st["extra"] = (st.inning >= 10).astype(int)
    st = st.merge(fv_tab[["diff", "extra", "fv"]], on=["diff", "extra"], how="left")
    st = st.merge(hm[["game_pk", "condition_id", "t0", "t1", "fee_rate", "y0", "y1", "period", "event_slug"]],
                  on="game_pk")
    bets, diag = [], []
    for r in st.itertuples(index=False):
        t = load_tape(r)
        if t is None:
            continue
        w = t[(t.ts >= r.state_ts + WO_START) & (t.ts < r.next_contact_ts)]
        diag.append({"game_pk": r.game_pk, "period": r.period, "inning": r.inning, "diff": r.diff, "fv": r.fv,
                     "n_fills": len(w), "med_yes": float(np.median(w.yes)) if len(w) else np.nan,
                     "cover": r.y0, "window_s": r.next_contact_ts - r.state_ts - WO_START})
        trig = w[w.yes >= r.fv + WO_EDGE]
        if trig.empty:
            continue
        t0 = float(trig.ts.iloc[0])
        e = t[(t.acq == 1) & (t.ts >= t0 + LAG) & (t.ts < r.next_contact_ts)]
        if e.empty:
            continue
        q0 = float(e.q.iloc[0])
        near = e[e.q <= q0 + 0.01]
        bets.append({"game_pk": r.game_pk, "inning": r.inning, "diff": r.diff, "fv": r.fv,
                     "trig_yes": float(trig.yes.iloc[0]), "entry_q": q0, "won": r.y1, "fee_rate": r.fee_rate,
                     "period": r.period, "cap_usd": float((near.q * near["size"]).sum())})
    B = pd.DataFrame(bets)
    if len(B):
        B["roi"] = C.taker_roi(B.entry_q, B.won, B.fee_rate)
        B["roi_s1"] = C.taker_roi(B.entry_q, B.won, B.fee_rate, slip=0.01)
    return B, pd.DataFrame(diag), len(st)


# ----------------------------------------------------------------------------- main

def _j(o):
    if isinstance(o, dict):
        return {str(k): _j(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_j(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    return o


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--dev-only", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", stream=sys.stdout)
    OUT.mkdir(parents=True, exist_ok=True)

    x = runline_universe()
    s = draw_sample(x)
    s.to_parquet(OUT / "sample.parquet", index=False)
    if a.fetch:
        fetch_tapes(s)
        return
    evaluate(x, s, dev_only=a.dev_only)


def evaluate(x: pd.DataFrame, s: pd.DataFrame, dev_only: bool) -> None:
    R: dict = {"params": {"PRE_USD_MIN": PRE_USD_MIN, "BUDGET_PER_PERIOD": BUDGET_PER_PERIOD, "SEED": SEED,
                          "DEC_OFFSET": DEC_OFFSET, "ML_WIN": ML_WIN, "RL_WIN": RL_WIN, "MIN_RL_FILLS": MIN_RL_FILLS,
                          "LAG": LAG, "THR": THR, "WO_EDGE": WO_EDGE}}
    # ---- data checks
    e26 = x[x.period != "PRE2026"]
    R["checks"] = {
        "runline_markets_2026": int(len(e26)),
        "orient_bad": int((~e26.orient_ok).sum()),
        "ml_orient_bad": int((~pd.Series(e26.ml_orient_ok)).sum()),
        "payout_vs_mlb_mismatch": int(((e26.cover_mlb.notna()) & (e26.y0.isin([0, 1]))
                                       & (e26.cover_mlb != e26.y0)).sum()),
        "void_markets": int((e26.y0 == 0.5).sum()),
        "sample_markets": s.groupby("period").size().to_dict(),
        "sample_games": s.groupby("period").game_pk.nunique().to_dict(),
        "tapes_present": int(sum((TAPES / f"{c}.parquet").exists() for c in s.condition_id)),
    }
    au = tape_audit(s).merge(s[["condition_id", "market_slug", "period"]], on="condition_id")
    au.to_parquet(OUT / "tape_audit.parquet", index=False)
    fl = au[au.api_idx_flip > 0.5]
    R["checks"]["tape_audit"] = {
        "tapes": int(len(au)), "fills": int(au.n.sum()), "unknown_asset_fills": int(au.unknown_asset.sum()),
        "markets_api_outcomeIndex_flipped": int(len(fl)),
        "flipped_by_period": fl.groupby("period").size().to_dict(),
        "flipped_dates": sorted(fl.market_slug.str.extract(r"(\d{4}-\d\d-\d\d)")[0].unique().tolist()),
        "markets_partially_flipped": int(((au.api_idx_flip > 0) & (au.api_idx_flip < 1)).sum()),
        "markets_outcome_name_mismatch": int((au.name_mismatch > 0).sum())}
    s = s[s.orient_ok].copy()          # outcome-0 must be the team laying 1.5
    if dev_only:
        s = s[s.period == "DEV"].copy()

    # ---- FV models (fit on development only, then frozen)
    tr_dev = training_rows(HOLDOUT_TS)
    tr_25 = training_rows(Y2026_TS)
    M = {"full": fit_fv(tr_dev), "sym": fit_fv(tr_dev, symmetric=True),
         "full_2025": fit_fv(tr_25), "sym_2025": fit_fv(tr_25, symmetric=True)}
    R["models"] = M
    # empirical claim check (dev rows)
    tr_dev["bin"] = pd.cut(tr_dev.p, [0, .35, .45, .5, .55, .6, .65, .7, 1])
    R["cover_table"] = (tr_dev.groupby(["bin", "home"], observed=True).cover.agg(["size", "mean"])
                        .unstack("home").round(4).reset_index().astype(str).to_dict(orient="records"))

    # ---- features
    ml = ml_price_at(s, "first_pitch_ts")
    s = s.merge(ml, left_on="game_pk", right_index=True, how="left")
    s["p_team"] = np.where(s.home == 1, s.p_home_ml, 1 - s.p_home_ml)
    F = s.merge(market_features(s), on="condition_id", how="left")
    F["FV"] = fv_predict(M["full"], F.p_team, F.home)
    F["FV_sym"] = fv_predict(M["sym"], F.p_team, F.home)
    F["FV_wf"] = np.where(F.period == "DEV", fv_predict(M["full_2025"], F.p_team, F.home), F.FV)
    F.loc[F.p_team.isna(), ["FV", "FV_sym", "FV_wf"]] = np.nan
    F.to_parquet(OUT / "features.parquet", index=False)
    R["coverage"] = {per: {"markets": int(len(g)), "tape": int(g.have_tape.sum()),
                           "ml_price": int(g.p_team.notna().sum()), "P_yes": int(g.P_yes.notna().sum()),
                           "both": int((g.P_yes.notna() & g.p_team.notna()).sum())}
                     for per, g in F.groupby("period")}

    periods = ["DEV"] if dev_only else ["DEV", "HOLDOUT"]
    res = {}
    for per in periods:
        Fp = F[F.period == per]
        r = {}
        # primary
        B = apply_rule(Fp, THR)
        B.to_parquet(OUT / f"bets_primary_{per}.parquet", index=False)
        r["primary"] = summarize(B)
        r["primary_s1"] = summarize(B, "roi_s1")
        r["primary_by_market_side"] = {f"{sd}_{'NO' if bs else 'YES'}": summarize(g)
                                       for (sd, bs), g in B.groupby(["side", "bet_side"])}
        r["primary_by_month"] = {str(k): summarize(g) for k, g in
                                 B.groupby(pd.to_datetime(B.first_pitch_ts, unit="s").dt.to_period("M"))}
        if per == "DEV":
            Bw = apply_rule(Fp, THR, fv_col="FV_wf")
            r["primary_walkforward_2025model"] = summarize(Bw)
            r["primary_walkforward_2025model_s1"] = summarize(Bw, "roi_s1")
        # (b) thresholds
        for th in (0.01, 0.04):
            Bt = apply_rule(Fp, th)
            r[f"thr_{th}"] = summarize(Bt)
            r[f"thr_{th}_s1"] = summarize(Bt, "roi_s1")
        # (a) naive structural: NO on home -1.5, YES on away -1.5 at first print >= D+3 s
        nv = Fp.assign(bet_side=np.where(Fp.home == 1, 1, 0))
        Bn = _enter(nv)
        r["naive_both"] = summarize(Bn)
        r["naive_both_s1"] = summarize(Bn, "roi_s1")
        r["naive_home_NO"] = summarize(Bn[Bn.home == 1])
        r["naive_home_NO_s1"] = summarize(Bn[Bn.home == 1], "roi_s1")
        r["naive_away_YES"] = summarize(Bn[Bn.home == 0])
        r["naive_away_YES_s1"] = summarize(Bn[Bn.home == 0], "roi_s1")
        # (c) diagnostics: does the market price the home/away asymmetry?
        d = Fp[Fp.P_yes.notna() & Fp.FV.notna()]
        diag = {}
        for sd, g in d.groupby("side"):
            diag[sd] = {"n": int(len(g)), "mean_P_yes": float(g.P_yes.mean()), "mean_FV": float(g.FV.mean()),
                        "mean_FV_sym": float(g.FV_sym.mean()),
                        "mean_P_minus_FV": float((g.P_yes - g.FV).mean()),
                        "mean_P_minus_FVsym": float((g.P_yes - g.FV_sym).mean()),
                        "cover_rate": float(g.y0.mean()),
                        "mean_cover_minus_P": float((g.y0 - g.P_yes).mean()),
                        "mean_cover_minus_FV": float((g.y0 - g.FV).mean())}
            m, lo, hi = C.cluster_ci(g.y0 - g.P_yes, g.game_pk)
            diag[sd]["cover_minus_P_ci"] = [lo, hi]
            m, lo, hi = C.cluster_ci(g.P_yes - g.FV, g.game_pk)
            diag[sd]["P_minus_FV_ci"] = [lo, hi]
        # does market's P_yes discriminate home vs away beyond a symmetric model? regress P_yes on FV_sym+home
        if len(d) > 20:
            import statsmodels.api as sm
            X = sm.add_constant(np.column_stack([_logit(d.FV_sym), d.home]))
            ols = sm.OLS(_logit(d.P_yes), X).fit(cov_type="cluster", cov_kwds={"groups": d.game_pk})
            Xf = sm.add_constant(np.column_stack([_logit(d.FV_sym), d.home]))
            ols_fv = sm.OLS(_logit(d.FV), Xf).fit()
            diag["logitP_on_logitFVsym_home"] = {"coef": ols.params.tolist(), "se": ols.bse.tolist()}
            diag["logitFV_on_logitFVsym_home"] = {"coef": ols_fv.params.tolist()}
        # binned by the laying team's moneyline price: market vs asymmetric FV vs symmetric FV vs realized
        bins = pd.cut(d.p_team, [0, .4, .5, .55, .6, .65, 1])
        tab = d.groupby([bins, "side"], observed=True).agg(
            n=("P_yes", "size"), P_yes=("P_yes", "mean"), FV=("FV", "mean"), FV_sym=("FV_sym", "mean"),
            cover=("y0", "mean")).round(4).reset_index()
        tab["p_team"] = tab.p_team.astype(str)
        diag["by_price_bin"] = tab.to_dict(orient="records")
        r["diagnostic"] = diag
        res[per] = r

    # (d) in-play walk-off variant
    fv_tab = walkoff_fv()
    R["walkoff_fv_table"] = fv_tab.to_dict(orient="records")
    W, Wd, n_states = variant_walkoff(s, fv_tab)
    W.to_parquet(OUT / "bets_walkoff.parquet", index=False)
    R["walkoff_states"] = int(n_states)
    Wd.to_parquet(OUT / "walkoff_states.parquet", index=False)
    R["walkoff_pricing"] = {}
    for per, g in Wd.groupby("period"):
        h = g[g.n_fills > 0]
        m, lo, hi = C.cluster_ci(h.med_yes - h.fv, h.game_pk) if len(h) else (np.nan, np.nan, np.nan)
        R["walkoff_pricing"][per] = {"states": int(len(g)), "states_with_fills": int(len(h)),
                                    "median_window_s": float(g.window_s.median()),
                                    "mean_med_yes": float(h.med_yes.mean()), "mean_fv": float(h.fv.mean()),
                                    "mean_yes_minus_fv": m, "ci": [lo, hi],
                                    "cover_rate_states_with_fills": float(h.cover.mean()),
                                    "cover_rate_all_states": float(g.cover.mean()), "mean_fv_all": float(g.fv.mean())}
    for per in periods:
        Wp = W[W.period == per] if len(W) else W
        res[per]["walkoff"] = summarize(Wp) if len(Wp) else {"bets": 0}
        res[per]["walkoff_s1"] = summarize(Wp, "roi_s1") if len(Wp) else {"bets": 0}

    # robustness (DEV only): decision anchored on the scheduled start instead of the actual first pitch
    Fs = s[s.period == "DEV"].drop(columns=["p_home_ml", "n_ml", "p_team"])
    ml2 = ml_price_at(Fs, "game_start_ts")
    Fs = Fs.merge(ml2, left_on="game_pk", right_index=True, how="left")
    Fs["p_team"] = np.where(Fs.home == 1, Fs.p_home_ml, 1 - Fs.p_home_ml)
    Fs = Fs.merge(market_features(Fs, "game_start_ts"), on="condition_id", how="left")
    Fs["FV"] = fv_predict(M["full"], Fs.p_team, Fs.home)
    res["DEV"]["primary_sched_anchor"] = summarize(apply_rule(Fs, THR))
    R["results"] = res
    (OUT / ("results_dev.json" if dev_only else "results.json")).write_text(json.dumps(_j(R), indent=1, default=str))
    print(json.dumps(_j(R), indent=1, default=str))


if __name__ == "__main__":
    main()
