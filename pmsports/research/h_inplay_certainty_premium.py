"""Hypothesis `inplay_certainty_premium`: sell the new in-play 90-97c 'lock' as a maker and hold the tail.

Mechanism: when a side first becomes a 90%+ favourite during play, 'sure-thing' yield buyers and bettors piling
onto the winning side cross the spread to buy it, while variance-limited makers avoid holding 3-10c comeback
tails. If so, the tail is underpriced and a maker who sells certainty (holds the underdog) collects a premium.

Primary rule (pre-registered; parameters frozen):
  UNIVERSE  C.markets() with pre_usd >= $20k, all sports; voids pay y = 0.5.
            DEV / HOLDOUT by game_start_ts vs 2026-07-01 (DEV split 2025 / 2026H1 at 2026-01-01).
  SIGNAL    one per event_slug: the first in-play fill (ts >= game_start_ts) in any universe market of the event
            where the taker acquired side s at q in (0.90, 0.97] and s's pregame price was < 0.80
            (pre_mid0 for s=0, 1-pre_mid0 for s=1). t0 = its ts, a = q. Ties at t0: the lowest q (the first
            level a buy sweep matches), then the lowest market code.
  EXECUTION rest an ask on s at a (= a bid on 1-s at 1-a) from t0+3 s to t0+300 s; cap 200 shares per event.
  QUEUE     phi = 0.25. Every later taker order acquiring s at q >= a in [t0+3, t0+300] fills us
            min(size, remaining cap) if the level a was exhausted (a fill acquiring s at q > a with ts in
            [ts_i, ts_i+60]; a fill at q > a is itself proof that a is gone), else phi*min(size, remaining).
  P&L       per share = a - y_s + 0.15*fee_rate*a*(1-a) (maker, no fee, rebate), held to resolution;
            capital per share = 1-a. Share-weighted P&L/share and ROI on capital, C.cluster_ci by event_slug.
Variants (allowed list): (a) phi=0, phi=1; (b) taker: buy 1-s at the first print acquiring 1-s in
[t0+3, t0+300] (C.taker_roi, actual fee, +1c); (c) a buckets; (d) pre_usd >= $100k; (e) all in-play fills with
favourite price in [0.90, 0.98) (no pregame filter, the inplay_longshot_maker version), same queue model, no
cap, clustered by event; (f) control: sides already >= 0.85 pregame.

Split taker orders: rows sharing (m, w, ts, s, q) are one taker order matched against several makers; they are
merged before the queue model is applied (the cap and phi apply per taker order).

RESULT (single holdout run, 2026-09-19): DEAD. Primary DEV -1.22c/share, ROI -14.7% [-20.8, -8.2];
HOLDOUT -1.71c/share, ROI -20.2% [-30.5, -9.9]. Fills are adversely selected (the level is traded through when the
favourite keeps rising); see reports/research/inplay_certainty_premium.md.

Run:  PYTHONPATH=. systemd-run --user --scope -q -p MemoryMax=4G -p MemorySwapMax=0 .venv/bin/python \
        -m pmsports.research.h_inplay_certainty_premium [--holdout]
Without --holdout every signal/fill with game_start_ts >= 2026-07-01 is dropped before any statistic.
"""
from __future__ import annotations

import json
import sys
import time

import numpy as np
import pandas as pd

from pmsports.research import common as C

SLUG = "inplay_certainty_premium"
CACHE = C.RESEARCH / f"h_{SLUG}"
T_2026 = pd.Timestamp("2026-01-01", tz="UTC").timestamp()
T_HOLD = pd.Timestamp("2026-07-01", tz="UTC").timestamp()

PRE_USD_MIN = 20_000
PRE_USD_BIG = 100_000                 # variant (d)
Q_LO, Q_HI = 0.90, 0.97               # signal: q in (Q_LO, Q_HI]
PRE_MAX = 0.80                        # signal: pregame price of s < PRE_MAX
PRE_CTRL = 0.85                       # variant (f): pregame price of s >= PRE_CTRL
E_LO, E_HI = 0.90, 0.98               # variant (e): q in [E_LO, E_HI)
DELAY, WIN_END, EXH = 3, 300, 60      # join at t0+3, stop at t0+300, level-exhaust lookahead 60 s
CAP = 200.0
PHI = 0.25
REBATE = C.MAKER_REBATE
TOL = 1e-6                            # float32 price tolerance (smallest tick 0.001)
CHUNK = 1500                          # markets per fills read
FILL_COLS = ["m", "w", "ts", "size", "s", "q", "y", "fee_rate"]


def period(ts):
    ts = np.asarray(ts, float)
    return np.where(ts < T_2026, "dev25", np.where(ts < T_HOLD, "dev26h1", "hold"))


# ----------------------------------------------------------------------------- data

def universe() -> pd.DataFrame:
    mk = C.markets()
    u = mk[mk.pre_usd >= PRE_USD_MIN].copy()
    u["ev"] = pd.factorize(u.event_slug)[0]
    return u[["m", "ev", "event_slug", "family", "league", "market_slug", "game_start_ts", "fee_rate",
              "pre_mid0", "pre_usd", "y0", "y1", "o0", "o1"]].reset_index(drop=True)


def merge_split(f: pd.DataFrame) -> pd.DataFrame:
    """Rows sharing (m, w, ts, s, q) = one taker order split across makers -> one row (sizes summed)."""
    f = f.assign(q4=np.round(f.q.astype(np.float64), 5))
    g = (f.groupby(["m", "w", "ts", "s", "q4"], sort=False, as_index=False)
         .agg(size=("size", "sum"), q=("q", "first"), y=("y", "first"), fee_rate=("fee_rate", "first")))
    return g.drop(columns="q4")


def _range_max(key_sorted: np.ndarray, vals: np.ndarray, lo_key: np.ndarray, hi_key: np.ndarray) -> np.ndarray:
    """max(vals[i]) over key_sorted[i] in [lo_key, hi_key] (inclusive), via a sparse table; -inf if empty."""
    n = len(vals)
    lo = np.searchsorted(key_sorted, lo_key, side="left")
    hi = np.searchsorted(key_sorted, hi_key, side="right")        # exclusive
    out = np.full(len(lo), -np.inf)
    if n == 0:
        return out
    tab = [vals.astype(np.float64)]
    k = 1
    while (1 << k) <= n:
        prev = tab[-1]
        h = 1 << (k - 1)
        tab.append(np.maximum(prev[:-h], prev[h:]))
        k += 1
    ln = hi - lo
    ok = ln > 0
    j = np.zeros(len(lo), np.int64)
    j[ok] = np.floor(np.log2(ln[ok])).astype(np.int64)
    for kk in np.unique(j[ok]):
        sel = ok & (j == kk)
        t = tab[kk]
        out[sel] = np.maximum(t[lo[sel]], t[hi[sel] - (1 << kk)])
    return out


def pass1(u: pd.DataFrame):
    """One read of every universe market's in-play fills. Returns
      cand : per (m, s) the first in-play taker order with q in (Q_LO, Q_HI] (ties: lowest q)
      erows: every in-play taker order with q in [E_LO, E_HI) with its level-exhausted flag (variant e)"""
    fc, fe = CACHE / "cand.parquet", CACHE / "erows.parquet"
    if fc.exists() and fe.exists():
        return pd.read_parquet(fc), pd.read_parquet(fe)
    CACHE.mkdir(parents=True, exist_ok=True)
    gst = u.set_index("m").game_start_ts
    ms = np.sort(u.m.unique())
    cands, erows = [], []
    t_start = time.time()
    n_raw = n_ip = n_ord = 0
    for i in range(0, len(ms), CHUNK):
        chunk = ms[i:i + CHUNK]
        f = C.fills(markets=chunk, columns=FILL_COLS)
        n_raw += len(f)
        f = f[f.ts.to_numpy() >= gst.reindex(f.m.to_numpy()).to_numpy()]
        n_ip += len(f)
        f = merge_split(f)
        n_ord += len(f)
        f["q"] = f.q.astype(np.float64)
        f = f.sort_values(["m", "s", "ts", "q"], kind="stable").reset_index(drop=True)
        # candidates for the signal: first (ts, lowest q) per (m, s)
        c = f[(f.q > Q_LO + TOL) & (f.q <= Q_HI + TOL)]
        c = c.groupby(["m", "s"], sort=False).head(1)
        cands.append(c[["m", "s", "ts", "q", "size", "y", "fee_rate"]])
        # variant (e): level-exhausted flag for every order at [E_LO, E_HI)
        g = f.m.to_numpy(np.int64) * 2 + f.s.to_numpy(np.int64)
        key = (g << 32) + f.ts.to_numpy(np.int64)
        e = (f.q >= E_LO - TOL) & (f.q < E_HI - TOL)
        ei = np.flatnonzero(e.to_numpy())
        mx = _range_max(key, f.q.to_numpy(float), key[ei], key[ei] + EXH)
        er = f.iloc[ei][["m", "s", "ts", "q", "size", "y", "fee_rate"]].copy()
        er["exh"] = mx > er.q.to_numpy() + TOL
        erows.append(er)
        print(f"  pass1 {i + len(chunk):>6}/{len(ms)} markets  raw {n_raw:,} in-play {n_ip:,} orders {n_ord:,}"
              f"  {time.time() - t_start:.0f}s", flush=True)
        del f
    cand = pd.concat(cands, ignore_index=True)
    er = pd.concat(erows, ignore_index=True)
    for d in (cand, er):
        d["s"] = d.s.astype(np.int8)
        d["size"] = d["size"].astype(np.float64)
    cand.to_parquet(fc, index=False)
    er.to_parquet(fe, index=False)
    json.dump(dict(n_raw=n_raw, n_inplay=n_ip, n_orders=n_ord), open(CACHE / "pass1_counts.json", "w"))
    return cand, er


def pre_price(u: pd.DataFrame, m, s) -> np.ndarray:
    p0 = u.set_index("m").pre_mid0.reindex(np.asarray(m)).to_numpy(float)
    return np.where(np.asarray(s) == 0, p0, 1 - p0)


def signals(u: pd.DataFrame, cand: pd.DataFrame, pre_usd_min=PRE_USD_MIN, ctrl=False) -> pd.DataFrame:
    """One signal per event: the earliest qualifying (m, s) candidate (ties: lowest q, then lowest m)."""
    uu = u[u.pre_usd >= pre_usd_min]
    c = cand[cand.m.isin(uu.m)].copy()
    c["pre_s"] = pre_price(u, c.m, c.s)
    c = c[(c.pre_s >= PRE_CTRL) if ctrl else (c.pre_s < PRE_MAX)]
    c = c.merge(uu[["m", "ev", "event_slug", "family", "league", "game_start_ts", "pre_usd"]], on="m")
    c = c.sort_values(["ev", "ts", "q", "m"], kind="stable").groupby("ev", sort=False).head(1)
    c = c.rename(columns={"ts": "t0", "q": "a", "y": "y_s"}).reset_index(drop=True)
    c["per"] = period(c.game_start_ts)
    return c


def windows(sig: pd.DataFrame, name: str) -> pd.DataFrame:
    """All taker orders (both sides) in the signal market with ts in [t0, t0+WIN_END+EXH]; cached."""
    fn = CACHE / f"win_{name}.parquet"
    if fn.exists():
        return pd.read_parquet(fn)
    s = sig[["ev", "m", "t0"]]
    out = []
    ms = np.sort(s.m.unique())
    for i in range(0, len(ms), CHUNK):
        chunk = ms[i:i + CHUNK]
        f = C.fills(markets=chunk, columns=FILL_COLS)
        f = f.merge(s[s.m.isin(chunk)], on="m")
        f = f[(f.ts >= f.t0) & (f.ts <= f.t0 + WIN_END + EXH)].drop(columns="t0")
        ev = f[["m", "ev"]].drop_duplicates()
        f = merge_split(f.drop(columns="ev")).merge(ev, on="m")
        out.append(f)
    w = pd.concat(out, ignore_index=True)
    w["q"] = w.q.astype(np.float64)
    w["size"] = w["size"].astype(np.float64)
    w = w.sort_values(["ev", "ts", "q"], kind="stable").reset_index(drop=True)
    w.to_parquet(fn, index=False)
    return w


# ----------------------------------------------------------------------------- simulation

def simulate(sig: pd.DataFrame, win: pd.DataFrame, phi=PHI, cap=CAP, delay=DELAY, end=WIN_END,
             tick=False) -> pd.DataFrame:
    """Queue model. Returns one row per event with shares filled `sh`, exhausted shares `sh_exh`, and the price a.
    tick=True: a rounded UP to the 0.01 grid (0.001 above 0.96) - robustness only."""
    a_map = sig.set_index("ev")
    w = win.merge(sig[["ev", "s", "t0", "a"]].rename(columns={"s": "s_sig"}), on="ev")
    w = w[w.s == w.s_sig]
    if tick:
        a = w.a.to_numpy()
        grid = np.where(a <= 0.96 + TOL, 0.01, 0.001)
        w["a"] = np.round(np.ceil(a / grid - 1e-4) * grid, 6)
    w = w.sort_values(["ev", "ts", "q"], kind="stable").reset_index(drop=True)
    key = (w.ev.to_numpy(np.int64) << 32) + w.ts.to_numpy(np.int64)
    # level exhausted: some fill acquiring s at q > a with ts in [ts_i, ts_i + EXH]
    mx = _range_max(key, w.q.to_numpy(float), key, key + EXH)
    inwin = (w.ts >= w.t0 + delay) & (w.ts <= w.t0 + end) & (w.q >= w.a - TOL)
    d = w[inwin.to_numpy()].copy()
    d["exh"] = mx[inwin.to_numpy()] > d.a.to_numpy() + TOL
    ev, size, exh = d.ev.to_numpy(), d["size"].to_numpy(float), d.exh.to_numpy()
    sh = np.zeros(len(d))
    cur, rem = -1, 0.0
    for i in range(len(d)):
        if ev[i] != cur:
            cur, rem = ev[i], cap
        if rem <= 0:
            continue
        x = min(size[i], rem)
        x = x if exh[i] else phi * x
        sh[i] = x
        rem -= x
    d["sh"] = sh
    d["sh_exh"] = np.where(exh, sh, 0.0)
    r = d.groupby("ev").agg(sh=("sh", "sum"), sh_exh=("sh_exh", "sum"), n_fills=("sh", lambda x: (x > 0).sum()),
                            a_used=("a", "first"))
    out = sig.merge(r, left_on="ev", right_index=True, how="left")
    out[["sh", "sh_exh", "n_fills"]] = out[["sh", "sh_exh", "n_fills"]].fillna(0.0)
    out["a_used"] = out.a_used.fillna(out.a)
    return out


def maker_pnl(d: pd.DataFrame, a_col="a_used", shade=0.0) -> pd.DataFrame:
    """Per-share P&L of selling s at a (buying 1-s at 1-a) held to resolution, + rebate; `shade` = worse price."""
    a = d[a_col].to_numpy(float) - shade
    d = d.copy()
    d["pnl"] = a - d.y_s.to_numpy(float) + REBATE * d.fee_rate.to_numpy(float) * a * (1 - a)
    d["cap"] = 1 - a
    return d


def taker_b(sig: pd.DataFrame, win: pd.DataFrame, slip=0.0) -> pd.DataFrame:
    """(b) buy 1-s as a taker at the first print acquiring 1-s in [t0+3, t0+300]."""
    w = win.merge(sig[["ev", "s", "t0"]].rename(columns={"s": "s_sig"}), on="ev")
    w = w[(w.s != w.s_sig) & (w.ts >= w.t0 + DELAY) & (w.ts <= w.t0 + WIN_END)]
    w = w.sort_values(["ev", "ts", "q"], kind="stable").groupby("ev").head(1)
    d = sig.merge(w[["ev", "ts", "q", "y", "fee_rate"]].rename(columns={"fee_rate": "fr"}), on="ev")
    d["roi"] = C.taker_roi(d.q, d.y, d.fr, slip)
    d["cost"] = np.clip(d.q + slip, 0.001, 0.999) + C.taker_fee(1.0, np.clip(d.q + slip, 0.001, 0.999), d.fr)
    return d


# ----------------------------------------------------------------------------- statistics

def stat_maker(d: pd.DataFrame, cluster="event_slug") -> dict:
    d = d[d.sh > 0]
    if len(d) == 0:
        return dict(events=0)
    pps, lo, hi = C.cluster_ci(d.pnl * 100, d[cluster], d.sh)
    roi, rlo, rhi = C.cluster_ci(d.pnl / d.cap, d[cluster], d.sh * d.cap)
    return dict(events=int(d[cluster].nunique()), shares=float(d.sh.sum()), capital=float((d.sh * d.cap).sum()),
                pnl_usd=float((d.sh * d.pnl).sum()), c_per_share=pps, c_lo=lo, c_hi=hi,
                roi=roi, roi_lo=rlo, roi_hi=rhi, exh_share=float(d.sh_exh.sum() / d.sh.sum()) if "sh_exh" in d else None)


def stat_by_period(d: pd.DataFrame, fn=stat_maker, periods=("dev25", "dev26h1", "dev", "hold")) -> dict:
    out = {}
    for p in periods:
        x = d[d.per.isin(["dev25", "dev26h1"])] if p == "dev" else d[d.per == p]
        if len(x):
            out[p] = fn(x)
    return out


def stat_taker(d: pd.DataFrame) -> dict:
    if len(d) == 0:
        return dict(bets=0)
    r, lo, hi = C.cluster_ci(d.roi, d.event_slug)
    return dict(bets=int(len(d)), roi=r, roi_lo=lo, roi_hi=hi, mean_price=float(d.q.mean()),
                win_rate=float(d.y.mean()))


def fmt(res: dict, label: str) -> str:
    rows = [f"{label}"]
    for p, r in res.items():
        if "c_per_share" in r:
            rows.append(f"   {p:8s} ev {r['events']:>6,} sh {r['shares']:>11,.0f} cap ${r['capital']:>10,.0f} "
                        f"P&L ${r['pnl_usd']:>9,.0f}  {r['c_per_share']:+.3f}c [{r['c_lo']:+.3f},{r['c_hi']:+.3f}]  "
                        f"ROI {100 * r['roi']:+.2f}% [{100 * r['roi_lo']:+.2f},{100 * r['roi_hi']:+.2f}]"
                        + (f"  exh {r['exh_share']:.2f}" if r.get("exh_share") is not None else ""))
        elif "roi" in r and "bets" in r:
            rows.append(f"   {p:8s} bets {r['bets']:>6,}  ROI {100 * r['roi']:+.2f}% [{100 * r['roi_lo']:+.2f},"
                        f"{100 * r['roi_hi']:+.2f}]  mean px {r['mean_price']:.3f} win {r['win_rate']:.3f}")
        else:
            rows.append(f"   {p:8s} {r}")
    return "\n".join(rows)


# ----------------------------------------------------------------------------- main

def verdict(dev: dict, hold: dict) -> str:
    if not hold or hold.get("events", 0) < 30 or not dev or dev.get("events", 0) < 30:
        return "INCONCLUSIVE"
    if hold["roi"] > 0 and dev["roi_lo"] > 0:
        return "PROFITABLE"          # protocol: holdout ROI > 0 AND DEV CI lower bound > 0
    if hold["roi"] > 0 and dev["roi_hi"] > 0:
        return "PROMISING"           # holdout > 0 but the DEV CI crosses 0
    return "DEAD"                    # holdout <= 0, or DEV CI entirely below 0 (fixed before the holdout run)


def run_set(sig, win, label, res, out, phi=PHI, **kw):
    d = maker_pnl(simulate(sig, win, phi=phi, **kw))
    r = stat_by_period(d)
    res[label] = r
    out.append(fmt(r, label))
    return d


def main(holdout: bool = False) -> dict:
    t_start = time.time()
    u = universe()
    cand, er = pass1(u)
    print(f"universe markets {len(u):,} events {u.ev.nunique():,}; candidates {len(cand):,}; e-rows {len(er):,}"
          f"  ({time.time() - t_start:.0f}s)")
    keep = (lambda d: d) if holdout else (lambda d: d[d.game_start_ts < T_HOLD].copy())
    res: dict = {"holdout_included": holdout}
    out: list[str] = []

    sets = {"primary": signals(u, cand), "d_preusd100k": signals(u, cand, pre_usd_min=PRE_USD_BIG),
            "f_ctrl_pre85": signals(u, cand, ctrl=True)}
    wins = {k: windows(v, k) for k, v in sets.items()}          # cached with all periods
    evp = {k: v.set_index("ev").game_start_ts for k, v in sets.items()}
    sets = {k: keep(v) for k, v in sets.items()}
    wins = {k: w[w.ev.isin(sets[k].ev)] for k, w in wins.items()}
    sig, win = sets["primary"], wins["primary"]
    res["n_signals"] = sig.per.value_counts().to_dict()
    print(f"signals {res['n_signals']}  ({time.time() - t_start:.0f}s)")

    # ---- primary (pre-registered)
    d = run_set(sig, win, "PRIMARY phi=0.25 cap=200", res, out)
    d.to_parquet(CACHE / ("events_primary_full.parquet" if holdout else "events_primary_dev.parquet"), index=False)
    res["primary_fill"] = {p: dict(signals=int((d.per == p).sum()), filled=int(((d.per == p) & (d.sh > 0)).sum()),
                                   mean_sh_if_filled=float(d.sh[(d.per == p) & (d.sh > 0)].mean()),
                                   cap_hit=float((d.sh[(d.per == p)] >= CAP - 1e-6).mean()))
                           for p in sorted(d.per.unique())}
    # adverse-selection decomposition: filled vs unfilled signals, level-exhausted vs phi shares
    res["primary_decomp"] = {}
    for p in sorted(d.per.unique()):
        x = d[d.per == p]
        fl = x[x.sh > 0]
        ph = fl.sh - fl.sh_exh
        res["primary_decomp"][p] = dict(
            filled_n=int(len(fl)), filled_win_s=float(fl.y_s.mean()), filled_a=float(fl.a.mean()),
            filled_sh_wtd_win_s=float((fl.sh * fl.y_s).sum() / fl.sh.sum()),
            unfilled_n=int((x.sh == 0).sum()), unfilled_win_s=float(x.y_s[x.sh == 0].mean()),
            unfilled_a=float(x.a[x.sh == 0].mean()),
            unfilled_c_per_share_if_sold=float(100 * (x.a - x.y_s)[x.sh == 0].mean()),
            exh_c=C.cluster_ci(fl.pnl * 100, fl.event_slug, fl.sh_exh) if fl.sh_exh.sum() > 0 else None,
            phi_c=C.cluster_ci(fl.pnl * 100, fl.event_slug, ph) if ph.sum() > 0 else None)
    out.append("DECOMP " + json.dumps(res["primary_decomp"], default=float))
    # +1c: sold 1c worse
    d1 = maker_pnl(d, shade=0.01)
    res["primary_plus1c"] = stat_by_period(d1)
    out.append(fmt(res["primary_plus1c"], "PRIMARY, fills 1c worse"))
    # no rebate
    d0 = d.copy(); d0["pnl"] = d0.a_used - d0.y_s
    res["primary_no_rebate"] = stat_by_period(d0)
    out.append(fmt(res["primary_no_rebate"], "PRIMARY, no rebate"))
    # per sport
    res["primary_by_sport"] = {}
    for fam, x in d.groupby("family"):
        res["primary_by_sport"][fam] = stat_by_period(x)
        out.append(fmt(res["primary_by_sport"][fam], f"  sport {fam}"))
    # unconstrained reference: sell every signal at a at t0 (the print that already happened - NOT tradable)
    dr = d.copy(); dr["sh"] = 1.0; dr["sh_exh"] = 0.0; dr["pnl"] = dr.a - dr.y_s
    res["ref_unconstrained_sell_at_signal_print"] = stat_by_period(dr)
    out.append(fmt(res["ref_unconstrained_sell_at_signal_print"], "REF (not tradable): sell 1 share at the signal print"))

    # ---- (a) phi
    for phi in (0.0, 1.0):
        run_set(sig, win, f"(a) phi={phi}", res, out, phi=phi)
    # ---- (b) taker
    for slip in (0.0, 0.01):
        tb = taker_b(sig, win, slip)
        r = stat_by_period(tb, fn=stat_taker)
        res[f"(b) taker slip={slip}"] = r
        out.append(fmt(r, f"(b) taker buy 1-s, fee, slip={slip}"))
    # ---- (c) buckets of a
    for lo, hi in ((0.90, 0.93), (0.93, 0.95), (0.95, 0.97)):
        x = d[(d.a > lo + TOL) & (d.a <= hi + TOL)]
        r = stat_by_period(x)
        res[f"(c) a in ({lo},{hi}]"] = r
        out.append(fmt(r, f"(c) a in ({lo},{hi}]"))
    # ---- (d) pre_usd >= 100k
    run_set(sets["d_preusd100k"], wins["d_preusd100k"], "(d) pre_usd>=100k", res, out)
    # ---- (e) all fills at [0.90, 0.98), no cap, same queue model
    e = er.merge(u[["m", "event_slug", "family", "game_start_ts", "pre_mid0"]], on="m")
    e = keep(e)
    e["per"] = period(e.game_start_ts)
    e = e.rename(columns={"q": "a_used", "y": "y_s"})
    for phi, lab in ((PHI, "(e) all fills [0.90,0.98) phi=0.25 no cap"), (0.0, "(e) phi=0"), (1.0, "(e) phi=1")):
        x = e.copy()
        x["sh"] = np.where(x.exh, x["size"], phi * x["size"])
        x["sh_exh"] = np.where(x.exh, x["size"], 0.0)
        x = maker_pnl(x)
        r = stat_by_period(x)
        res[lab] = r
        out.append(fmt(r, lab))
    # (e) with the primary's pregame filter (diagnostic)
    x = e[np.where(e.s == 0, e.pre_mid0, 1 - e.pre_mid0) < PRE_MAX].copy()
    x["sh"] = np.where(x.exh, x["size"], PHI * x["size"]); x["sh_exh"] = np.where(x.exh, x["size"], 0.0)
    res["(e') all fills, pregame<0.80"] = stat_by_period(maker_pnl(x))
    out.append(fmt(res["(e') all fills, pregame<0.80"], "(e') all fills, pregame<0.80, phi=0.25"))
    # qualifying taker notional (capacity context): all in-play taker $ at [0.90,0.98)
    res["e_taker_notional_usd"] = e.assign(n=e["size"] * e.a_used).groupby("per").n.sum().to_dict()
    # ---- (f) control
    run_set(sets["f_ctrl_pre85"], wins["f_ctrl_pre85"], "(f) control pregame>=0.85", res, out)

    # ---- robustness (not variants: execution checks on the primary)
    run_set(sig, win, "robust: join at t0+6 (on-chain lag)", res, out, delay=6)
    run_set(sig, win, "robust: a rounded up to tick grid", res, out, tick=True)

    # ---- capacity (primary)
    for p in sorted(d.per.unique()):
        x = d[d.per == p]
        days = (x.t0.max() - x.t0.min()) / 86400
        res.setdefault("capacity", {})[p] = dict(days=days, capital=float((x.sh * x.cap).sum()),
                                                 capital_per_day=float((x.sh * x.cap).sum() / max(days, 1)),
                                                 pnl_per_day=float((x.sh * x.pnl).sum() / max(days, 1)),
                                                 events_per_day=float(len(x) / max(days, 1)))
    # pool we sell into: taker shares acquiring s at >= a in the window (cap-free)
    w2 = win.merge(sig[["ev", "s", "t0", "a"]].rename(columns={"s": "s_sig"}), on="ev")
    w2 = w2[(w2.s == w2.s_sig) & (w2.ts >= w2.t0 + DELAY) & (w2.ts <= w2.t0 + WIN_END) & (w2.q >= w2.a - TOL)]
    w2["per"] = period(sig.set_index("ev").game_start_ts.reindex(w2.ev).to_numpy())
    res["window_pool_shares"] = w2.groupby("per")["size"].sum().to_dict()
    res["window_pool_usd_capital"] = (w2["size"] * (1 - w2.a)).groupby(w2.per).sum().to_dict()

    dev = res["PRIMARY phi=0.25 cap=200"].get("dev", {})
    hold = res["PRIMARY phi=0.25 cap=200"].get("hold", {})
    res["verdict"] = verdict(dev, hold) if holdout else "DEV ONLY"
    out.append(f"VERDICT: {res['verdict']}")
    print("\n".join(out))
    print(json.dumps({k: res[k] for k in ("primary_fill", "capacity", "window_pool_shares", "e_taker_notional_usd")},
                     indent=1, default=float))
    json.dump(res, open(CACHE / ("results_full.json" if holdout else "results_dev.json"), "w"), indent=1,
              default=float)
    print(f"done {time.time() - t_start:.0f}s")
    return res


if __name__ == "__main__":
    main(holdout="--holdout" in sys.argv)
