"""Hypothesis `soccer_ou_vs_ml_pregame`: soccer O/U 2.5 lags the deep 3-way moneyline pregame.

Mechanism claimed: the neg-risk moneyline legs are deep and kept consistent by arbitrage bots, so they absorb
lineup / injury news (~T-60 min) quickly. O/U 2.5 lives in a separate '-more-markets' event with no conversion
link. When news moves the moneyline-implied goal expectation, a stale O/U maker is the counterparty for anyone
trading toward the moneyline-implied value.

Rule as implemented (pre-registered; parameters frozen from DEV before the single holdout run):
  UNIVERSE  soccer events in C.markets() whose two team-win legs ('<event_slug>-<team>', not '-draw') are both
            present with pre_usd >= $50k each, and whose linked O/U 2.5 market '<event_slug>-total-2pt5' exists in
            C.universe() with valid 0/1 (or 0.5 void) payouts. T = the legs' game_start_ts (identical for both
            legs and for the O/U market in all 584 events). DEV: T < 2026-07-01, HOLDOUT: T >= 2026-07-01.
  MODEL     D = T - 10 min. pH, pA = median Yes-converted fill prices of the two team legs over [T-20 min, D]
            (>= 1 fill per leg, else skip). pD = 1 - pH - pA clipped to [0.05, 0.5]; when clipped, pH and pA are
            rescaled proportionally to sum to 1 - pD. Independent Poisson (lam_h, lam_a) solved so that
            P(H) = pH and P(A) = pA; P_model = P(total goals >= 3).
  CALIB     isotonic regression of the Over outcome on P_model, fitted on every DEV event with a P_model and a
            0/1 O/U payout, then frozen (P_cal). DEV numbers with this calibration are IN-SAMPLE; a 5-fold
            cross-fitted DEV figure is reported next to them.
  MARKET    P_ou = median Over-converted O/U fill price over [T-30 min, D]; skip if < 2 fills.
  RULE      dev = P_cal - P_ou; dev >= +0.04 -> buy Over; dev <= -0.04 -> buy Under.
  ENTRY     the first O/U print acquiring our side with ts in [D+3 s, T), at its price plus the market's taker fee
            (C.taker_roi with the O/U fee_rate); no print -> no bet. One bet per event, held to resolution.
  METRIC    mean per-$1 ROI (equal stake per bet), C.cluster_ci by event; +1c slippage sensitivity.
Variants: (a) thresholds 0.03 / 0.06; (b) logistic calibration over ~ logit(P_model) + logit(P_ou) (DEV-fit),
signal = fitted - P_ou; (c) CLV: mark each bet to the O/U median over [T-5 min, T); plus the no-trade staleness
regression of the O/U move on dev; (d) dynamic at T-45 min: |dP_cal| >= 0.04 since T-120 min and the O/U moved
less than half as much in that direction; (e) lead-lag: does an O/U move predict a later moneyline-implied move
(and vice versa)?

Usage (from the repo root, every call under the memory cap):
  systemd-run --user --scope -q -p MemoryMax=4G -p MemorySwapMax=0 .venv/bin/python \
      -m pmsports.research.h_soccer_ou_vs_ml_pregame universe | fetch | run dev | run holdout | diag
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
from scipy.optimize import least_squares
from scipy.stats import poisson

from pmsports.research import common as C

SLUG = "soccer_ou_vs_ml_pregame"
OUT = C.RESEARCH / f"h_{SLUG}"
TAPES = OUT / "tapes"
SPLIT_TS = pd.Timestamp("2026-07-01", tz="UTC").timestamp()

# frozen primary parameters
MIN_PRE_USD = 50_000
WIN = 86_400            # O/U tape window [T-24h, T)
DEC = 600               # D = T - 10 min
ML_WIN = 1_200          # legs median over [T-20 min, D]
OU_WIN = 1_800          # O/U median over [T-30 min, D]
LAG = 3                 # entry >= D + 3 s
THR = 0.04
PD_CLIP = (0.05, 0.5)
MAXG = 20               # goals grid for the Poisson model


# ============================================================================= universe

def build_universe() -> pd.DataFrame:
    mk = C.markets()
    s = mk[(mk.family == "soccer")].copy()
    s["leg"] = [ms[len(es) + 1:] if isinstance(ms, str) and ms.startswith(es + "-") else None
                for ms, es in zip(s.market_slug, s.event_slug)]
    team = s[s.leg.notna() & (s.leg != "draw")]
    assert (team.o0 == "Yes").all() and (team.o1 == "No").all(), "team legs must be Yes/No"
    n = team.groupby("event_slug").m.size()
    team = team[team.event_slug.isin(n[n == 2].index)]
    # home = the team listed first in the event slug (label only: the Poisson P(total>=3) is symmetric)
    rows = []
    for es, g in team.groupby("event_slug"):
        parts = es.split("-")
        g = g.set_index("leg")
        legs = list(g.index)
        home = parts[1] if parts[1] in legs else legs[0]
        away = [l for l in legs if l != home][0]
        h, a = g.loc[home], g.loc[away]
        rows.append(dict(event_slug=es, league=h.league, T=h.game_start_ts, T_a=a.game_start_ts,
                         m_h=int(h.m), m_a=int(a.m), pre_h=h.pre_usd, pre_a=a.pre_usd,
                         yh=h.y0, ya=a.y0, ml_fee=h.fee_rate))
    e = pd.DataFrame(rows)
    print(f"soccer events with exactly 2 team legs in C.markets(): {len(e)}; start disagree: {(e['T'] != e.T_a).sum()}")
    e = e[(e.pre_h >= MIN_PRE_USD) & (e.pre_a >= MIN_PRE_USD) & (e["T"] == e.T_a)].copy()
    print(f"both legs pre_usd >= $50k: {len(e)}")
    u = C.universe(columns=["condition_id", "token_id", "outcome_idx", "outcome", "payout", "market_type",
                            "game_start_ts", "event_slug", "market_slug", "fee_rate"])
    ou = u[u.market_slug.isin(set(e.event_slug + "-total-2pt5"))]
    tok = ou[["condition_id", "token_id", "outcome_idx", "outcome"]].copy()
    pay = ou.pivot_table(index="condition_id", columns="outcome_idx", values="payout", aggfunc="first")
    nam = ou.pivot_table(index="condition_id", columns="outcome_idx", values="outcome", aggfunc="first")
    meta = ou.drop_duplicates("condition_id").set_index("condition_id")[
        ["market_slug", "market_type", "game_start_ts", "event_slug", "fee_rate"]]
    meta = meta.join(pay.rename(columns={0: "p0", 1: "p1"})).join(nam.rename(columns={0: "n0", 1: "n1"}))
    meta["ov"] = np.where(meta.n0 == "Over", 0, np.where(meta.n1 == "Over", 1, -1))
    meta["y_over"] = np.where(meta.ov == 0, meta.p0, meta.p1)
    meta["es"] = meta.market_slug.str.replace(r"-total-2pt5$", "", regex=True)
    meta = meta.reset_index().rename(columns={"condition_id": "ou_cid", "game_start_ts": "ou_T",
                                              "event_slug": "ou_event", "fee_rate": "fee_rate"})
    assert meta.es.is_unique
    e = e.merge(meta[["es", "ou_cid", "ou_T", "ou_event", "market_type", "fee_rate", "ov", "p0", "p1", "y_over"]],
                left_on="event_slug", right_on="es", how="inner").drop(columns="es")
    ok = (e.ov >= 0) & e.p0.isin([0, 0.5, 1]) & e.p1.isin([0, 0.5, 1]) & ((e.p0 + e.p1) == 1)
    print(f"with linked O/U 2.5: {len(e)}; bad payout / outcome names dropped: {(~ok).sum()}; "
          f"O/U start != moneyline start: {(e.ou_T != e['T']).sum()}")
    e = e[ok].copy()
    e["fee_rate"] = e.fee_rate.fillna(0.0)
    e["split"] = np.where(e["T"] < SPLIT_TS, "dev", "holdout")
    e["T"] = e["T"].astype(np.int64)
    print(e.split.value_counts().to_dict(), e.groupby("split").fee_rate.value_counts().to_dict())
    OUT.mkdir(parents=True, exist_ok=True)
    e.to_parquet(OUT / "events.parquet", index=False)
    tok[tok.condition_id.isin(set(e.ou_cid))].to_parquet(OUT / "ou_tokens.parquet", index=False)
    return e


def events() -> pd.DataFrame:
    p = OUT / "events.parquet"
    return pd.read_parquet(p) if p.exists() else build_universe()


# ============================================================================= fetch

COLS = ["timestamp", "side", "asset", "outcomeIndex", "price", "size", "transactionHash", "proxyWallet"]


def _fetch_one(cid: str, T: int) -> int:
    from pmsports import polymarket as P
    path = TAPES / f"{cid}.parquet"
    if path.exists():
        return -1
    rows = P.trades(cid, T - WIN, T - 1)          # [T-24h, T): pregame only
    pd.DataFrame(rows, columns=COLS).to_parquet(path, index=False)
    return len(rows)


def fetch() -> None:
    import pmsports.http as H
    H.HOST_RPS["data-api.polymarket.com"] = 5
    TAPES.mkdir(parents=True, exist_ok=True)
    e = events()
    todo = [(r.ou_cid, int(r.T)) for r in e.itertuples() if not (TAPES / f"{r.ou_cid}.parquet").exists()]
    print(f"fetch: {len(e)} O/U markets, {len(todo)} to fetch", flush=True)
    done = err = 0
    with ThreadPoolExecutor(4) as ex:
        futs = {ex.submit(_fetch_one, c, t): c for c, t in todo}
        for f in as_completed(futs):
            try:
                f.result()
                done += 1
            except Exception as ex_:  # noqa: BLE001
                err += 1
                print("ERR", futs[f], ex_, flush=True)
            if (done + err) % 50 == 0:
                print(f"  {done + err}/{len(todo)} (errors {err})", flush=True)
    print(f"fetched {done}, errors {err}")


# ============================================================================= loading

def load_ou(e: pd.DataFrame) -> pd.DataFrame:
    """O/U prints: event_slug, ts, over (True if the taker acquired Over), q (price paid for the acquired side),
    po (Over-converted price), size. A taker SELL of token k = acquiring the other token at 1 - price."""
    tok = pd.read_parquet(OUT / "ou_tokens.parquet")
    tmap = dict(zip(tok.token_id, tok.outcome_idx.astype(int)))
    parts, n_rows, n_mis, n_unk = [], 0, 0, 0
    for r in e.itertuples():
        p = TAPES / f"{r.ou_cid}.parquet"
        if not p.exists():
            continue
        d = pd.read_parquet(p)
        d = d[(d["size"].astype(float) > 0) & (d.timestamp >= r.T - WIN) & (d.timestamp < r.T)]
        if len(d) == 0:
            continue
        idx = d.asset.map(tmap)
        unk = idx.isna().to_numpy()
        idx = np.where(unk, d.outcomeIndex, idx).astype(int)
        n_rows += len(d)
        n_unk += int(unk.sum())
        n_mis += int((idx != d.outcomeIndex.astype(int).to_numpy()).sum())
        buy = (d.side == "BUY").to_numpy()
        px = d.price.astype(float).to_numpy()
        s = np.where(buy, idx, 1 - idx)
        q = np.where(buy, px, 1 - px)
        over = s == r.ov
        parts.append(pd.DataFrame({"event_slug": r.event_slug, "ts": d.timestamp.to_numpy(np.int64), "over": over,
                                   "q": q, "po": np.where(over, q, 1 - q),
                                   "size": d["size"].astype(float).to_numpy()}))
    f = pd.concat(parts, ignore_index=True).sort_values(["event_slug", "ts"], kind="stable").reset_index(drop=True)
    print(f"O/U pregame prints {n_rows:,} in {f.event_slug.nunique()} markets; unknown token {n_unk}; "
          f"outcomeIndex != token map {n_mis}")
    return f


def load_ml(e: pd.DataFrame) -> pd.DataFrame:
    """Team-leg pregame fills: event_slug, leg ('h'/'a'), ts, py (Yes-converted price)."""
    legs = pd.concat([e[["event_slug", "m_h", "T"]].rename(columns={"m_h": "m"}).assign(leg="h"),
                      e[["event_slug", "m_a", "T"]].rename(columns={"m_a": "m"}).assign(leg="a")])
    f = C.fills(markets=legs.m.tolist(), columns=["m", "ts", "s", "q", "size"])
    f = f.merge(legs, on="m")
    # pre-decision taker $ per leg (robustness check of the pre_usd >= $50k universe filter)
    pre = f[f.ts < f["T"] - DEC].assign(usd=lambda d: d["size"] * d.q).groupby(["event_slug", "leg"]).usd.sum()
    load_ml.pre_usd_D = pre.unstack("leg")
    f = f[(f.ts >= f["T"] - 4 * 3600) & (f.ts < f["T"])]
    f["py"] = np.where(f.s == 0, f.q, 1 - f.q).astype(float)       # o0 == 'Yes'
    return f[["event_slug", "leg", "ts", "py", "size"]].sort_values(["event_slug", "leg", "ts"]).reset_index(drop=True)


def win_median(f: pd.DataFrame, col: str, lo: pd.Series, hi: pd.Series, keys=("event_slug",), incl_hi=True):
    """Per-key median and count of f[col] over ts in [lo, hi] (hi exclusive when incl_hi=False). lo/hi indexed by
    event_slug."""
    x = f.join(lo.rename("_lo"), on="event_slug").join(hi.rename("_hi"), on="event_slug")
    m = (x.ts >= x._lo) & ((x.ts <= x._hi) if incl_hi else (x.ts < x._hi))
    g = x[m].groupby(list(keys))[col].agg(["median", "size"])
    return g


# ============================================================================= model

def _poisson_probs(lh: float, la: float):
    k = np.arange(MAXG + 1)
    ph, pa = poisson.pmf(k, lh), poisson.pmf(k, la)
    M = np.outer(ph, pa)
    home = np.tril(M, -1).sum()
    away = np.triu(M, 1).sum()
    return home, away


def poisson_over(pH: float, pA: float) -> tuple[float, float, float]:
    """Solve independent Poisson (lam_h, lam_a) with P(H)=pH, P(A)=pA (after the pD clip); return
    (P(total >= 3), lam_h, lam_a)."""
    if not (np.isfinite(pH) and np.isfinite(pA)) or pH <= 0 or pA <= 0:
        return np.nan, np.nan, np.nan
    pD = 1 - pH - pA
    pDc = float(np.clip(pD, *PD_CLIP))
    if pDc != pD:
        sc = (1 - pDc) / (pH + pA)
        pH, pA = pH * sc, pA * sc
    def res(x):
        h, a = _poisson_probs(np.exp(x[0]), np.exp(x[1]))
        return [h - pH, a - pA]
    sol = least_squares(res, x0=[np.log(1.4), np.log(1.1)], bounds=([np.log(0.02)] * 2, [np.log(8.0)] * 2),
                        xtol=1e-12, ftol=1e-12, gtol=1e-12)
    lh, la = np.exp(sol.x)
    if max(abs(v) for v in sol.fun) > 2e-3:
        return np.nan, lh, la
    return float(1 - poisson.cdf(2, lh + la)), float(lh), float(la)


def model_at(ml: pd.DataFrame, e: pd.DataFrame, t_off: int, win: int = ML_WIN) -> pd.DataFrame:
    """Moneyline-implied P_model at decision time T - t_off from leg medians over [T-t_off-win, T-t_off]."""
    Ti = e.set_index("event_slug")["T"]
    g = win_median(ml, "py", Ti - t_off - win, Ti - t_off, keys=("event_slug", "leg"))
    med = g["median"].unstack("leg")
    cnt = g["size"].unstack("leg")
    out = pd.DataFrame(index=e.event_slug)
    out["pH"], out["pA"] = med.get("h"), med.get("a")
    out["nH"], out["nA"] = cnt.get("h"), cnt.get("a")
    # integrity (pre-decision): a team-win leg at <= 1c or >= 99c cannot be a pregame soccer price; these are
    # events whose scheduled start is later than the real kickoff (in play / finished at D) -> skip.
    out["glitch"] = (np.fmin(out.pH, out.pA) <= 0.01) | (np.fmax(out.pH, out.pA) >= 0.99)
    res = [poisson_over(h, a) if (nh >= 1 and na >= 1 and not gl) else (np.nan, np.nan, np.nan)
           for h, a, nh, na, gl in zip(out.pH, out.pA, out.nH.fillna(0), out.nA.fillna(0), out.glitch)]
    out["P_model"] = [r[0] for r in res]
    out["lam_tot"] = [r[1] + r[2] for r in res]
    return out


def ou_at(ou: pd.DataFrame, e: pd.DataFrame, t_off: int, win: int = OU_WIN) -> pd.DataFrame:
    Ti = e.set_index("event_slug")["T"]
    g = win_median(ou, "po", Ti - t_off - win, Ti - t_off)
    out = pd.DataFrame(index=e.event_slug)
    out["P_ou"] = g["median"]
    out["n_ou"] = g["size"]
    out["n_ou"] = out.n_ou.fillna(0).astype(int)
    out.loc[out.n_ou < 2, "P_ou"] = np.nan
    return out


def ou_close(ou: pd.DataFrame, e: pd.DataFrame, secs: int = 300) -> pd.Series:
    """O/U Over median over [T-secs, T) (low-noise closing mark); NaN if no print."""
    Ti = e.set_index("event_slug")["T"]
    g = win_median(ou, "po", Ti - secs, Ti, incl_hi=False)
    return g["median"].reindex(e.event_slug)


# ============================================================================= calibration

class Iso:
    def __init__(self, x=None, y=None, xs=None, ys=None):
        from sklearn.isotonic import IsotonicRegression
        if xs is None:
            ir = IsotonicRegression(y_min=0, y_max=1, out_of_bounds="clip").fit(x, y)
            xs, ys = ir.X_thresholds_, ir.y_thresholds_
        self.xs, self.ys = np.asarray(xs, float), np.asarray(ys, float)

    def __call__(self, x):
        return np.interp(np.asarray(x, float), self.xs, self.ys)   # = IsotonicRegression.predict (clip + linear)

    def to_json(self):
        return {"xs": self.xs.tolist(), "ys": self.ys.tolist()}


def logit(p):
    p = np.clip(np.asarray(p, float), 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


def fit_logit(X, y):
    import statsmodels.api as sm
    r = sm.GLM(np.asarray(y, float), sm.add_constant(np.asarray(X, float)), family=sm.families.Binomial()).fit()
    return r.params.tolist(), r.bse.tolist()


def pred_logit(params, X):
    z = params[0] + np.asarray(X, float) @ np.asarray(params[1:], float)
    return 1 / (1 + np.exp(-z))


# ============================================================================= trading

def enter(ou: pd.DataFrame, sig: pd.DataFrame, t_dec: pd.Series) -> pd.DataFrame:
    """sig: index event_slug, column 'buy_over' (bool). Entry = first print acquiring our side with ts in
    [t_dec + LAG, T). Returns one row per event with an entry."""
    x = ou.join(sig[["buy_over"]], on="event_slug", how="inner").join(t_dec.rename("t_dec"), on="event_slug")
    x = x[(x.over == x.buy_over) & (x.ts >= x.t_dec + LAG)]
    first = x.groupby("event_slug", sort=False).head(1).set_index("event_slug")
    # capacity: $ of prints on our side in [t_dec+LAG, T) at <= entry price + 1c
    x = x.join(first.q.rename("q_entry"), on="event_slug")
    cap = x[x.q <= x.q_entry + 0.01].assign(usd=lambda d: d["size"] * d.q).groupby("event_slug").usd.sum()
    assert (first.ts >= first.t_dec + LAG).all() and first.index.is_unique
    out = first[["ts", "q", "size"]].rename(columns={"ts": "ts_entry", "q": "q_entry", "size": "size_entry"})
    out["cap_usd"] = cap
    return out


def book(e: pd.DataFrame, bets: pd.DataFrame, slip: float = 0.0) -> pd.DataFrame:
    b = bets.join(e.set_index("event_slug")[["y_over", "fee_rate", "league", "split", "T"]])
    assert b.index.is_unique and (b.ts_entry < b["T"]).all()          # one bet per event, strictly pregame
    b["won"] = np.where(b.buy_over, b.y_over, 1 - b.y_over)
    b["roi"] = C.taker_roi(b.q_entry, b.won, b.fee_rate, slip=slip)
    return b


def summ(b: pd.DataFrame, n_boot=4000) -> dict:
    if len(b) == 0:
        return {"bets": 0}
    m, lo, hi = C.cluster_ci(b.roi, b.index, n_boot=n_boot)
    r1 = C.taker_roi(b.q_entry, b.won, b.fee_rate, slip=0.01)
    m1, lo1, hi1 = C.cluster_ci(r1, b.index, n_boot=n_boot)
    return {"bets": int(len(b)), "roi": m, "lo": lo, "hi": hi, "roi_1c": m1, "lo_1c": lo1, "hi_1c": hi1,
            "over_share": float(b.buy_over.mean()), "win_rate": float(b.won.mean()),
            "avg_price": float(b.q_entry.mean()), "cap_usd_median": float(b.cap_usd.median()),
            "cap_usd_total": float(b.cap_usd.sum()), "entry_usd_total": float((b.size_entry * b.q_entry).sum())}


def fmt(d: dict) -> str:
    if not d.get("bets"):
        return "no bets"
    return (f"n={d['bets']:4d} ROI {d['roi']*100:+6.2f}% [{d['lo']*100:+6.2f}, {d['hi']*100:+6.2f}]  "
            f"+1c {d['roi_1c']*100:+6.2f}% [{d['lo_1c']*100:+6.2f}, {d['hi_1c']*100:+6.2f}]  "
            f"over {d['over_share']:.2f} win {d['win_rate']:.3f} px {d['avg_price']:.3f}")


def rule_bets(e, ou, P_sig, P_ou, thr, t_dec):
    dev = (P_sig - P_ou).dropna()
    sig = pd.DataFrame({"dev": dev})
    sig = sig[sig.dev.abs() >= thr - 1e-12]
    sig["buy_over"] = sig.dev > 0
    ent = enter(ou, sig, t_dec)
    return book(e, sig.join(ent, how="inner"))


# ============================================================================= main analysis

def panel(e: pd.DataFrame, ml: pd.DataFrame, ou: pd.DataFrame) -> pd.DataFrame:
    """All per-event quantities at the fixed decision times (no calibration yet)."""
    p = e.set_index("event_slug")[["split", "league", "T", "y_over", "fee_rate", "ou_cid"]].copy()
    m10 = model_at(ml, e, DEC)
    p = p.join(m10)
    p = p.join(ou_at(ou, e, DEC))
    p["ou_close5"] = ou_close(ou, e, 300)
    # dynamic / lead-lag decision points
    for mins in (45, 120):
        mm = model_at(ml, e, mins * 60)
        p[f"P_model_{mins}"] = mm.P_model
        oo = ou_at(ou, e, mins * 60)
        p[f"P_ou_{mins}"] = oo.P_ou
    # staleness descriptors at D
    Ti = p["T"]
    last = ou[ou.ts <= ou.event_slug.map(Ti) - DEC].groupby("event_slug").ts.max()
    p["ou_age_at_D"] = (Ti - DEC) - last
    n24 = ou.groupby("event_slug").size()
    p["n_ou_24h"] = n24.reindex(p.index).fillna(0).astype(int)
    return p


def calib_fit(p: pd.DataFrame) -> dict:
    d = p[(p.split == "dev") & p.P_model.notna() & p.y_over.isin([0, 1])]
    iso = Iso(d.P_model.to_numpy(), d.y_over.to_numpy())
    dl = d[d.P_ou.notna()]
    lp, lse = fit_logit(np.c_[logit(dl.P_model), logit(dl.P_ou)], dl.y_over)
    l1, l1se = fit_logit(np.c_[logit(d.P_model)], d.y_over)
    return {"iso": iso.to_json(), "n_iso": int(len(d)), "logit2": lp, "logit2_se": lse, "n_logit2": int(len(dl)),
            "logit1": l1, "logit1_se": l1se}


def crossfit_iso(p: pd.DataFrame, k=5, seed=0) -> pd.Series:
    """Out-of-fold isotonic P_cal for DEV events (honest DEV estimate)."""
    d = p[(p.split == "dev") & p.P_model.notna()].copy()
    rng = np.random.default_rng(seed)
    fold = rng.integers(0, k, len(d))
    out = pd.Series(np.nan, index=d.index)
    for f in range(k):
        tr = d[(fold != f) & d.y_over.isin([0, 1])]
        iso = Iso(tr.P_model.to_numpy(), tr.y_over.to_numpy())
        out[d.index[fold == f]] = iso(d.P_model[fold == f])
    return out


def run(which: str) -> dict:
    e = events()
    ml = load_ml(e)
    ou = load_ou(e)
    p = panel(e, ml, ou)
    calf = OUT / "calibration_dev.json"
    if which == "dev":
        cal = calib_fit(p)
        calf.write_text(json.dumps(cal, indent=1))
        print("calibration frozen ->", calf)
    else:
        cal = json.loads(calf.read_text())               # frozen from the DEV run
    iso = Iso(xs=cal["iso"]["xs"], ys=cal["iso"]["ys"])
    p["P_cal"] = iso(p.P_model)
    p["P_lg2"] = pred_logit(cal["logit2"], np.c_[logit(p.P_model), logit(p.P_ou)])
    p["P_lg1"] = pred_logit(cal["logit1"], np.c_[logit(p.P_model)])
    for mins in (45, 120):
        p[f"P_cal_{mins}"] = iso(p[f"P_model_{mins}"])
        p[f"P_lg1_{mins}"] = pred_logit(cal["logit1"], np.c_[logit(p[f"P_model_{mins}"])])
    splits = ["dev"] if which == "dev" else ["dev", "holdout"]
    if which == "dev":
        p["P_cal_cf"] = crossfit_iso(p)
    res = {"which": which, "calibration": cal}
    tD = p["T"] - DEC
    e_by = e.set_index("event_slug")

    def evaluate(tag, P_sig, thr=THR, t_dec=tD, sub=None):
        out = {}
        for sp in splits:
            ix = p.index[(p.split == sp) & (True if sub is None else sub)]
            b = rule_bets(e[e.event_slug.isin(ix)], ou[ou.event_slug.isin(ix)], P_sig.reindex(ix),
                          p.P_ou.reindex(ix), thr, t_dec.reindex(ix))
            b = b.join(p[["P_ou", "ou_close5", "P_model", "P_cal"]])
            out[sp] = summ(b)
            out[sp + "_bets"] = b
            print(f"  {tag:34s} {sp:7s} {fmt(out[sp])}")
        return out

    # ---- sanity / descriptive
    for sp in splits:
        q = p[p.split == sp]
        desc = {"events": int(len(q)), "with_P_model": int(q.P_model.notna().sum()),
                "with_P_ou": int(q.P_ou.notna().sum()), "both": int((q.P_model.notna() & q.P_ou.notna()).sum()),
                "over_rate": float(q.y_over[q.y_over.isin([0, 1])].mean()), "void": int((q.y_over == 0.5).sum()),
                "P_model_mean": float(q.P_model.mean()), "P_cal_mean": float(q.P_cal.mean()),
                "P_ou_mean": float(q.P_ou.mean()),
                "corr_Pmodel_Pou": float(q[["P_model", "P_ou"]].corr().iloc[0, 1]),
                "lam_tot_median": float(q.lam_tot.median()),
                "pD_raw_median": float((1 - q.pH - q.pA).median()),
                "pD_clipped_share": float(((1 - q.pH - q.pA) < PD_CLIP[0]).mean() + ((1 - q.pH - q.pA) > PD_CLIP[1]).mean()),
                "ou_age_at_D_median_s": float(q.ou_age_at_D.median()),
                "n_ou_24h_median": float(q.n_ou_24h.median()),
                "n_ou_window_median": float(q.n_ou.median())}
        # Brier: market vs calibrated model
        v = q[q.P_ou.notna() & q.P_cal.notna() & q.y_over.isin([0, 1])]
        for c in ["P_ou", "P_cal", "P_model", "P_lg2"]:
            desc[f"brier_{c}"] = float(((v[c] - v.y_over) ** 2).mean())
        desc["n_brier"] = int(len(v))
        # payout orientation: Over hit rate by market-implied Over price bucket (must rise with price)
        bk = pd.cut(v.P_ou, [0, 0.4, 0.5, 0.6, 1])
        desc["over_hit_by_P_ou_bucket"] = {str(k): [int(len(g)), round(float(g.P_ou.mean()), 3),
                                                    round(float(g.y_over.mean()), 3)]
                                           for k, g in v.groupby(bk, observed=True)}
        desc["span_days"] = float((q["T"].max() - q["T"].min()) / 86400)
        # dev distribution
        dv = (q.P_cal - q.P_ou).dropna()
        desc["dev_abs_ge_thr_share"] = float((dv.abs() >= THR).mean())
        desc["dev_quantiles"] = dv.quantile([.05, .25, .5, .75, .95]).round(4).tolist()
        res[f"desc_{sp}"] = desc
        print(sp, json.dumps(desc, indent=0))

    # ---- primary
    print("PRIMARY (thr 0.04, isotonic P_cal, D = T-10m)")
    prim = evaluate("primary", p.P_cal)
    for sp in splits:
        res[f"primary_{sp}"] = prim[sp]
    if which == "dev":
        cf = evaluate("primary DEV cross-fitted iso", p.P_cal_cf)
        res["primary_dev_crossfit"] = cf["dev"]
        # time-forward: calibrate on DEV events before 2026-03-01, evaluate Mar-Jun
        cut = pd.Timestamp("2026-03-01", tz="UTC").timestamp()
        tr = p[(p.split == "dev") & (p["T"] < cut) & p.P_model.notna() & p.y_over.isin([0, 1])]
        iso_tf = Iso(tr.P_model.to_numpy(), tr.y_over.to_numpy())
        tf = evaluate("primary DEV time-fwd (fit<Mar,test>=Mar)", pd.Series(iso_tf(p.P_model), index=p.index),
                      sub=(p["T"] >= cut))
        res["primary_dev_timefwd"] = tf["dev"]

    # ---- per-bet details / splits
    for sp in splits:
        b = prim[sp + "_bets"]
        if len(b) == 0:
            continue
        b.to_parquet(OUT / f"bets_primary_{sp}.parquet")
        b["wc"] = b.league.str.contains("fifwc")
        by = {}
        for key, g in [("over", b[b.buy_over]), ("under", b[~b.buy_over]), ("world_cup", b[b.wc]),
                       ("non_world_cup", b[~b.wc])] + [(f"fee_{fr}", g) for fr, g in b.groupby("fee_rate")]:
            by[key] = summ(g)
            print(f"    {sp} {key:14s} {fmt(by[key])}")
        lg = b.groupby("league").agg(n=("roi", "size"), roi=("roi", "mean")).sort_values("n", ascending=False)
        by["league"] = lg.head(15).round(4).reset_index().to_dict("records")
        # CLV (c): mark to the O/U close median [T-5m, T) on our side
        mark = np.where(b.buy_over, b.ou_close5, 1 - b.ou_close5)
        clv = pd.Series(mark - b.q_entry, index=b.index).dropna()
        m, lo, hi = C.cluster_ci(clv, clv.index)
        by["clv_c"] = {"n": int(len(clv)), "mean": m, "lo": lo, "hi": hi}
        print(f"    {sp} CLV (mark = O/U median [T-5m,T)) {m*100:+.2f}c [{lo*100:+.2f}, {hi*100:+.2f}] n={len(clv)}")
        # gap closure: did P_ou move toward P_cal by the close?  (entry-free)
        mv = pd.Series(np.where(b.buy_over, b.ou_close5 - b.P_ou, b.P_ou - b.ou_close5), index=b.index).dropna()
        m, lo, hi = C.cluster_ci(mv, mv.index)
        by["ou_move_toward_signal"] = {"n": int(len(mv)), "mean": m, "lo": lo, "hi": hi}
        print(f"    {sp} O/U move D->close in signal direction {m*100:+.2f}c [{lo*100:+.2f}, {hi*100:+.2f}]")
        res[f"primary_{sp}_by"] = by

    # ---- variants
    print("VARIANT (a) thresholds")
    for thr in (0.03, 0.06):
        v = evaluate(f"(a) thr {thr}", p.P_cal, thr=thr)
        for sp in splits:
            res[f"va_thr{thr}_{sp}"] = v[sp]
    print("VARIANT (b) logistic calibration over ~ logit(P_model) + logit(P_ou)")
    v = evaluate("(b) logistic2 thr 0.04", p.P_lg2)
    for sp in splits:
        res[f"vb_{sp}"] = v[sp]
    print("DIAG: smooth 1-var logistic calibration of P_model (instead of isotonic)")
    v = evaluate("(diag) logistic1 thr 0.04", p.P_lg1)
    for sp in splits:
        res[f"vdiag_lg1_{sp}"] = v[sp]

    # (c) staleness regression on ALL events (no trading): O/U move D -> close vs dev
    for sp in splits:
        q = p[(p.split == sp)].copy()
        q["dev"] = q.P_cal - q.P_ou
        q["mv"] = q.ou_close5 - q.P_ou
        q["dev_lg1"] = q.P_lg1 - q.P_ou
        q = q.dropna(subset=["dev", "mv"])
        out = {"n": int(len(q))}
        for c in ["dev", "dev_lg1"]:
            X = q[c].to_numpy()
            y = q.mv.to_numpy()
            beta = np.polyfit(X, y, 1)[0]
            rng = np.random.default_rng(1)
            bs = []
            for _ in range(2000):
                i = rng.integers(0, len(q), len(q))
                bs.append(np.polyfit(X[i], y[i], 1)[0])
            out[c] = {"slope": float(beta), "lo": float(np.percentile(bs, 2.5)), "hi": float(np.percentile(bs, 97.5))}
            big = q[q[c].abs() >= THR]
            mvs = np.sign(big[c]) * big.mv
            m, lo, hi = C.cluster_ci(mvs, big.index)
            out[c + "_big_move_toward"] = {"n": int(len(big)), "mean": m, "lo": lo, "hi": hi}
        res[f"vc_staleness_{sp}"] = out
        print(f"  (c) staleness {sp}: {json.dumps(out)}")

    # (d) dynamic: at T-45 min
    print("VARIANT (d) dynamic at T-45m")
    for label, a120, a45 in [("iso", "P_cal_120", "P_cal_45"), ("lg1", "P_lg1_120", "P_lg1_45")]:
        dm = p[a45] - p[a120]
        do = p["P_ou_45"] - p["P_ou_120"]
        ok = (dm.abs() >= 0.04) & ((do * np.sign(dm)) < 0.5 * dm.abs())
        sig_P = pd.Series(np.where(ok, np.sign(dm), np.nan), index=p.index)
        # express as a pseudo-signal for rule_bets: P_sig - P_ou = +/-1 on triggered events
        out = {}
        t45 = p["T"] - 45 * 60
        for sp in splits:
            ix = p.index[(p.split == sp) & ok.fillna(False)]
            sig = pd.DataFrame({"dev": sig_P.reindex(ix)})
            sig["buy_over"] = sig.dev > 0
            ent = enter(ou[ou.event_slug.isin(ix)], sig, t45.reindex(ix))
            b = book(e[e.event_slug.isin(ix)], sig.join(ent, how="inner"))
            b = b.join(p[["P_ou_45", "ou_close5"]])
            out[sp] = summ(b)
            if len(b):
                mark = np.where(b.buy_over, b.ou_close5, 1 - b.ou_close5)
                clv = pd.Series(mark - b.q_entry, index=b.index).dropna()
                m, lo, hi = C.cluster_ci(clv, clv.index)
                out[sp]["clv"] = {"n": int(len(clv)), "mean": m, "lo": lo, "hi": hi}
            print(f"  (d) {label:4s} {sp:7s} triggered {int(len(ix))} {fmt(out[sp])}  clv {out[sp].get('clv')}")
            res[f"vd_{label}_{sp}"] = out[sp]

    # (e) lead-lag, in P_model probability units (smooth, no calibration)
    print("VARIANT (e) lead-lag")
    for sp in splits:
        q = p[p.split == sp].copy()
        q["dML_early"] = q.P_model_45 - q.P_model_120
        q["dOU_early"] = q.P_ou_45 - q.P_ou_120
        q["dML_late"] = q.P_model - q.P_model_45          # T-45 -> T-10
        q["dOU_late"] = q.ou_close5 - q.P_ou_45           # T-45 -> close
        out = {}
        for y, x in [("dML_late", "dOU_early"), ("dOU_late", "dML_early"), ("dOU_early", "dML_early"),
                     ("dOU_late", "dML_late")]:
            d = q[[x, y]].dropna()
            if len(d) < 20:
                continue
            X, Y = d[x].to_numpy(), d[y].to_numpy()
            beta = np.polyfit(X, Y, 1)[0]
            rng = np.random.default_rng(2)
            bs = [np.polyfit(X[i], Y[i], 1)[0] for i in (rng.integers(0, len(d), len(d)) for _ in range(2000))]
            out[f"{y}~{x}"] = {"n": int(len(d)), "slope": float(beta), "lo": float(np.percentile(bs, 2.5)),
                               "hi": float(np.percentile(bs, 97.5)), "corr": float(np.corrcoef(X, Y)[0, 1]),
                               "sd_x": float(X.std()), "sd_y": float(Y.std())}
        res[f"ve_{sp}"] = out
        for k_, v_ in out.items():
            print(f"  (e) {sp} {k_:22s} n={v_['n']} slope {v_['slope']:+.3f} [{v_['lo']:+.3f}, {v_['hi']:+.3f}] "
                  f"corr {v_['corr']:+.3f} sd_x {v_['sd_x']:.4f} sd_y {v_['sd_y']:.4f}")

    # ---- universe robustness: legs' taker $ traded before D (strictly pre-decision) vs the pre_usd filter
    pu = load_ml.pre_usd_D.reindex(p.index)
    low = (pu.min(axis=1) < MIN_PRE_USD)
    res["universe_predecision_usd"] = {sp: {"events": int((p.split == sp).sum()),
                                            "legs_usd_before_D_lt_50k": int((low & (p.split == sp)).sum())}
                                       for sp in splits}
    for sp in splits:
        b = prim[sp + "_bets"]
        if len(b):
            keep = b[~low.reindex(b.index).fillna(True)]
            res[f"primary_{sp}_predecision_universe"] = summ(keep)
            print(f"  primary {sp} restricted to legs >= $50k before D: {fmt(res[f'primary_{sp}_predecision_universe'])}")
    p.to_parquet(OUT / f"panel_{which}.parquet")
    clean = {k: v for k, v in res.items() if not k.endswith("_bets")}
    for k in list(clean):
        if isinstance(clean[k], dict):
            clean[k] = {kk: vv for kk, vv in clean[k].items() if not kk.endswith("_bets")}
    (OUT / f"results_{which}.json").write_text(json.dumps(clean, indent=1, default=float))
    return clean




# ============================================================================= post-hoc diagnostics (after the holdout run)

def diag() -> dict:
    """Descriptive, post-hoc (no parameter is chosen here): run after `run holdout`."""
    p = pd.read_parquet(OUT / "panel_holdout.parquet")
    cal = json.loads((OUT / "calibration_dev.json").read_text())
    iso = Iso(xs=cal["iso"]["xs"], ys=cal["iso"]["ys"])
    p["P_cal"] = iso(p.P_model)
    p["P_lg2"] = pred_logit(cal["logit2"], np.c_[logit(p.P_model), logit(p.P_ou)])
    out = {"glitch_events": p.index[p.glitch.fillna(False).astype(bool)].tolist()}
    v = p[p.P_model.notna() & p.P_ou.notna() & p.y_over.isin([0, 1])]
    for name, g in [("dev", v[v.split == "dev"]), ("holdout", v[v.split == "holdout"]), ("pooled", v)]:
        prm, se = fit_logit(np.c_[logit(g.P_model), logit(g.P_ou)], g.y_over)
        out[f"glm_{name}"] = {"n": int(len(g)), "const": prm[0], "b_model": prm[1], "se_model": se[1],
                              "b_ou": prm[2], "se_ou": se[2]}
        prm1, se1 = fit_logit(np.c_[logit(g.P_ou)], g.y_over)
        out[f"glm_ou_only_{name}"] = {"const": prm1[0], "b_ou": prm1[1], "se_ou": se1[1]}
        print(f"GLM {name:8s} n={len(g)}  over ~ {prm[0]:+.2f} {prm[1]:+.2f}(se {se[1]:.2f})*logit(P_model) "
              f"{prm[2]:+.2f}(se {se[2]:.2f})*logit(P_ou)   | O/U only: slope {prm1[1]:.2f} (se {se1[1]:.2f})")
    for sp in ["dev", "holdout"]:
        b = pd.read_parquet(OUT / f"bets_primary_{sp}.parquet")
        yr = pd.to_datetime(b["T"], unit="s").dt.year
        for y_, g in b.groupby(yr):
            s_ = summ(g)
            out[f"primary_{sp}_{y_}"] = s_
            print(f"primary {sp} {y_}: {fmt(s_)}")
        span = (p[p.split == sp]["T"].max() - p[p.split == sp]["T"].min()) / 86400
        out[f"capacity_{sp}"] = {"bets": int(len(b)), "days": float(span), "bets_per_day": float(len(b) / span),
                                 "cap_usd_median": float(b.cap_usd.median()),
                                 "cap_usd_p25": float(b.cap_usd.quantile(.25)),
                                 "cap_usd_total": float(b.cap_usd.sum()),
                                 "cap_usd_total_ex_top5": float(b.cap_usd.sort_values().iloc[:-5].sum()),
                                 "entry_print_usd_median": float((b.size_entry * b.q_entry).median()),
                                 "entry_lag_s_median": float((b.ts_entry - (b["T"] - DEC)).median())}
        print(f"capacity {sp}: {out[f'capacity_{sp}']}")
    # what variant (b) bets on: sign of (P_model - P_ou) among its bets
    q = p[p.P_ou.notna() & p.P_lg2.notna()].copy()
    q["dev_b"] = q.P_lg2 - q.P_ou
    q = q[q.dev_b.abs() >= THR]
    q["b_over"] = q.dev_b > 0
    q["ml_says_over"] = q.P_model > q.P_ou
    out["vb_agree_with_ml_share"] = {sp: float((g.b_over == g.ml_says_over).mean()) for sp, g in q.groupby("split")}
    print("variant (b) signals that point the same way as raw P_model - P_ou:", out["vb_agree_with_ml_share"])
    (OUT / "results_diag.json").write_text(json.dumps(out, indent=1, default=float))
    return out


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "universe":
        build_universe()
    elif cmd == "fetch":
        fetch()
    elif cmd == "run":
        run(sys.argv[2])
    elif cmd == "diag":
        diag()
    else:
        print(__doc__)
