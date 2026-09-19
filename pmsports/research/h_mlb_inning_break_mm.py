"""MLB half-inning break market making (quote only when no game information can arrive).

Hypothesis (pre-registered, see reports/research/mlb_inning_break_mm.md):
  During the scheduled half-inning break nothing about the game can change for ~100+ s, yet
  retail keeps crossing the spread. A maker that rests asks at the last-traded ask only inside
  [b0+30 s, b0+T_stop] (b0 = third out) collects the half-spread + 15% rebate with little
  adverse selection.

Run:
  systemd-run --user --scope -q -p MemoryMax=4G -p MemorySwapMax=0 \
      .venv/bin/python -m pmsports.research.h_mlb_inning_break_mm [--dev-only] [--live]

  --dev-only   simulate development games only (game_start_ts < 2026-07-01); used while building.
  (default)    development + the single holdout evaluation (game_start_ts >= 2026-07-01).
  --live       realism check on the recorded order books of 2026-09-18/19 (report only); it also
               produces the queue-depth sample that calibrates the FIFO queue model.

Fill models (review fix, see the report's "Review fixes" section):
  FRONT OF QUEUE (the pre-registered primary fill model): every taker fill that reaches our ask
      fills us min(size, 50). This assumes we are always first in the queue at the touch.
  FIFO (the protocol-compliant model the verdict is derived from): on each side we join the back
      of a queue of Q0 shares whenever our ask level changes. A taker fill at q == a first depletes
      the queue ahead; a fill at q > a means the level is gone (queue ahead 0). Q0 is a fixed grid,
      or drawn from the touch sizes recorded in the live books ("emp").

Outputs: data/research/h_mlb_inning_break_mm/{results.json, fills_<variant>.parquet, ...}
"""
from __future__ import annotations

import argparse
import json
import sys

import numpy as np
import pandas as pd

from pmsports.research import common as C

OUT = C.RESEARCH / "h_mlb_inning_break_mm"
HOLDOUT_TS = pd.Timestamp("2026-07-01", tz="UTC").timestamp()
Y2026_TS = pd.Timestamp("2026-01-01", tz="UTC").timestamp()
PRE_USD_MIN = 25_000.0
WIN_START = 30.0          # quote window starts b0 + 30 s (fill / on-chain time)
DECISION_LAG = 3.0        # quote uses only fills with ts <= t - 3 s
QUOTE_MAX_AGE = 120.0     # no quote on a side whose last print is older than 120 s
REF_AFTER = 5.0           # reference mid at window end + 5 s
REF_LOOKBACK = 60.0       # last fill of each side within [r-60, r]
CAP_PER_SIDE = 1000.0     # shares per side per window
BQ_HORIZON = 10.0         # back-of-queue: a later fill at a strictly higher price within 10 s
K_CLIP = 50.0             # shares per taker fill (primary)
HORIZONS = (60, 300, 600)                           # per-fill fixed-horizon markouts (review fix)
QUEUE_GRID = (0.0, 500.0, 2000.0, 5000.0, 20726.0)  # FIFO queue-ahead grid (20,726 = live median)
EMP_SEEDS = (0, 1, 2)
EPS = 1e-6
PERIODS = ("DEV_2025", "DEV_2026H1", "DEV", "HOLDOUT")


# ----------------------------------------------------------------------------- data

def universe() -> pd.DataFrame:
    """MLB moneyline markets joined to MLB game_pk. One market per game_pk (ambiguous mappings dropped)."""
    mk = C.markets()
    mk = mk[mk.family == "baseball"][["m", "condition_id", "game_start_ts", "fee_rate", "pre_usd",
                                      "event_slug", "y0", "y1"]]
    g = pd.read_parquet(C.DATA / "mlb" / "games.parquet", columns=["condition_id", "game_pk"])
    g = g[g.game_pk.notna()]
    x = mk.merge(g, on="condition_id", how="inner")
    x["game_pk"] = x.game_pk.astype("int64")
    x["pre_usd"] = x.pre_usd.fillna(0.0)
    x["dup"] = x.game_pk.duplicated(keep=False)
    x["period"] = np.where(x.game_start_ts >= HOLDOUT_TS, "HOLDOUT",
                           np.where(x.game_start_ts >= Y2026_TS, "DEV_2026H1", "DEV_2025"))
    return x.reset_index(drop=True)


def plays(game_pks) -> pd.DataFrame:
    cache = OUT / "plays.parquet"
    if cache.exists():
        p = pd.read_parquet(cache)
        if set(game_pks) <= set(p.game_pk.unique()):
            return p[p.game_pk.isin(game_pks)]
    frames = []
    for gp in sorted(set(game_pks)):
        f = C.DATA / "mlb" / "plays" / f"{gp}.parquet"
        if f.exists():
            frames.append(pd.read_parquet(f, columns=["game_pk", "play_idx", "inning", "half", "start_ts", "end_ts"]))
    p = pd.concat(frames, ignore_index=True)
    OUT.mkdir(parents=True, exist_ok=True)
    p.to_parquet(cache, index=False)
    return p


def half_innings(p: pd.DataFrame) -> pd.DataFrame:
    """One row per half-inning: hs = first PA start, he = last PA end (third out), next half's hs."""
    p = p.dropna(subset=["start_ts", "end_ts"])
    h = (p.assign(hord=lambda d: d.inning * 2 + (d.half == "bottom").astype(int))
         .groupby(["game_pk", "inning", "half", "hord"], as_index=False)
         .agg(hs=("start_ts", "min"), he=("end_ts", "max"), n_pa=("play_idx", "size")))
    h = h.sort_values(["game_pk", "hord"]).reset_index(drop=True)
    nxt = h.groupby("game_pk").shift(-1)
    h["next_hs"] = nxt.hs
    h["next_hord"] = nxt.hord
    return h


def breaks(h: pd.DataFrame) -> pd.DataFrame:
    """Every half-inning followed by another half-inning: b0 = third-out time, b1 = next half's first PA start."""
    b = h[h.next_hs.notna()].copy()
    b["b0"], b["b1"] = b.he, b.next_hs
    b["blen"] = b.b1 - b.b0
    return b[["game_pk", "inning", "half", "b0", "b1", "blen"]].reset_index(drop=True)


def load_fills(ms, merge_split: bool = True) -> pd.DataFrame:
    """Taker fills of markets `ms`. merge_split (review fix): rows sharing (m, w, ts, s, q) are one taker
    order matched against several makers; merge them so the per-fill clip applies per taker order."""
    f = C.fills(markets=ms, columns=["m", "w", "ts", "size", "s", "q", "y", "fee_rate"])
    if merge_split:
        f["q4"] = f.q.round(4)
        f = (f.groupby(["m", "w", "ts", "s", "q4"], sort=False, as_index=False)
             .agg(size=("size", "sum"), q=("q", "first"), y=("y", "first"), fee_rate=("fee_rate", "first"))
             .sort_values(["m", "ts"], kind="stable"))
    return f.drop(columns=[c for c in ("w", "q4") if c in f.columns])


# ----------------------------------------------------------------------------- simulation

class Book:
    """Per-market fill arrays (sorted by ts; original order kept within a second)."""

    def __init__(self, f: pd.DataFrame):
        self.ts = f.ts.to_numpy(np.float64)
        self.size = f["size"].to_numpy(np.float64)
        self.s = f.s.to_numpy(np.int8)
        self.q = f.q.to_numpy(np.float64)
        self.y = f.y.to_numpy(np.float64)
        self.fee = f.fee_rate.to_numpy(np.float64)
        self.side_ts = [self.ts[self.s == k] for k in (0, 1)]
        self.side_q = [self.q[self.s == k] for k in (0, 1)]

    def last(self, k: int, t: float, lookback: float):
        """Price of the last fill acquiring side k with ts <= t and ts >= t - lookback (None if none)."""
        st = self.side_ts[k]
        j = np.searchsorted(st, t, "right") - 1
        if j < 0 or st[j] < t - lookback:
            return None
        return self.side_q[k][j]

    def last_ts(self, k: int, t: float) -> float:
        st = self.side_ts[k]
        j = np.searchsorted(st, t, "right") - 1
        return -np.inf if j < 0 else st[j]

    def mid_two(self, t: float, lookback: float = REF_LOOKBACK) -> float:
        """Pre-registered reference: two-sided print mid of outcome 0 (NaN unless both sides printed)."""
        a, b = self.last(0, t, lookback), self.last(1, t, lookback)
        return np.nan if (a is None or b is None) else 0.5 * (a + 1.0 - b)

    def mid_refs(self, t: float, hs: float, lookback: float = REF_LOOKBACK):
        """(two-sided mid0, relaxed mid0, kind, one-sided mid0 from the most recent side).
        Relaxed (review fix): two-sided mid if both sides printed in [t-lb, t]; otherwise the last print
        of the one side that printed minus the half-spread hs (estimated on DEV two-sided windows)."""
        a, b = self.last(0, t, lookback), self.last(1, t, lookback)
        os0 = (a - hs) if a is not None else np.nan
        os1 = (1.0 - (b - hs)) if b is not None else np.nan
        if a is not None and b is not None:
            recent = os0 if self.last_ts(0, t) >= self.last_ts(1, t) else os1
            return 0.5 * (a + 1.0 - b), 0.5 * (a + 1.0 - b), 2, recent
        if a is None and b is None:
            return np.nan, np.nan, 0, np.nan
        one = os0 if a is not None else os1
        return np.nan, one, 1, one


def simulate(bk: Book, ws: float, we: float, K: float = K_CLIP, frac: float | None = None,
             back_of_queue: bool = False, queue=None, rng=None):
    """Maker asks on both tokens over [ws, we]; returns the list of simulated maker fills.

    Quote on side s at time t: a_s(t) = price of the last taker fill acquiring s with ts <= t-3
    (no quote if older than 120 s). A taker fill acquiring s at q >= a_s(t) reaches our ask.
    queue=None: front of queue (pre-registered primary): we get min(size, K) (or frac*size).
    queue=float or ndarray: FIFO. When our level a_s changes we (re)join behind Q0 shares (a fixed
      float, or a draw from the ndarray). q == a: the fill first depletes the queue ahead, and we get
      min(remaining, K). q > a: the level is gone, so the queue ahead is 0 and we get min(size, K).
      A 2-column ndarray draws (Q0, lambda) jointly from one recorded live window. The queue ahead then
      also decays as exp(-lambda * dt) from the join time (last print + 3 s): proportional cancellation,
      so cancellations at the level come from ahead of us in proportion to the queue ahead.
    Capped at CAP_PER_SIDE shares per side per window.
    """
    lo = np.searchsorted(bk.ts, ws, "left")
    hi = np.searchsorted(bk.ts, we, "right")
    out = []
    used = [0.0, 0.0]
    lvl = [None, None]
    qa = [0.0, 0.0]
    lam = [0.0, 0.0]
    tl = [0.0, 0.0]
    fifo = queue is not None
    emp = isinstance(queue, np.ndarray)
    joint = emp and queue.ndim == 2
    for i in range(lo, hi):
        s = int(bk.s[i])
        t = bk.ts[i]
        a = bk.last(s, t - DECISION_LAG, QUOTE_MAX_AGE - DECISION_LAG)
        if a is None:
            lvl[s] = None
            continue
        q, size = bk.q[i], bk.size[i]
        if fifo and (lvl[s] is None or abs(a - lvl[s]) > EPS):
            lvl[s] = a
            if joint:
                j = rng.integers(len(queue))
                qa[s], lam[s] = float(queue[j, 0]), float(queue[j, 1])
                tl[s] = min(bk.last_ts(s, t - DECISION_LAG) + DECISION_LAG, t)   # join time
            else:
                qa[s] = float(queue[rng.integers(len(queue))]) if emp else float(queue)
        if joint and lam[s] > 0 and qa[s] > 0:
            qa[s] *= np.exp(-lam[s] * (t - tl[s]))
            tl[s] = t
        if q < a - EPS:
            continue
        avail = size
        if fifo:
            if q > a + EPS:
                qa[s] = 0.0
            else:
                take = min(qa[s], size)
                qa[s] -= take
                avail = size - take
                if avail <= 1e-9:
                    continue
        if back_of_queue:
            st, sq = bk.side_ts[s], bk.side_q[s]
            k0 = np.searchsorted(st, t, "right")
            k1 = np.searchsorted(st, t + BQ_HORIZON, "right")
            if not (sq[k0:k1] > a + EPS).any():
                continue
        n = avail * frac if frac is not None else min(avail, K)
        n = min(n, CAP_PER_SIDE - used[s])
        if n <= 0:
            continue
        used[s] += n
        out.append((i, s, t, a, n, q, size, bk.y[i], bk.fee[i]))
    return out


def run_windows(books: dict, win: pd.DataFrame, hs: dict | None = None, horizons=(), seed: int = 0,
                **kw) -> pd.DataFrame:
    """win: columns m, game_pk, period, wid, ws, we, r. Returns one row per simulated maker fill.
    hs: period -> half-spread for the relaxed one-sided reference. horizons: per-fill fixed-horizon
    markouts (two-sided print mid at t+H)."""
    rows = []
    n_win = n_ref = 0
    hs = hs or {}
    emp = isinstance(kw.get("queue"), np.ndarray)
    for w in win.itertuples(index=False):
        bk = books.get(w.m)
        if bk is None:
            continue
        n_win += 1
        rng = np.random.default_rng([seed, int(w.wid)]) if emp else None
        fl = simulate(bk, w.ws, w.we, rng=rng, **kw)
        mid0, mrx, kind, mos = bk.mid_refs(w.r, hs.get(w.period, 0.005))
        n_ref += kind == 2
        for (i, s, t, a, n, q, size, y, fee) in fl:
            # pre-registered: breaks without a two-sided print in [r-60, r] get ref = NaN (dropped from the
            # markout; kept for hold-to-resolution). ref_rx = relaxed one-sided fallback (review fix).
            ref = mid0 if s == 0 else 1.0 - mid0
            ref_rx = mrx if s == 0 else 1.0 - mrx
            ref_os = mos if s == 0 else 1.0 - mos
            hz = []
            for H in horizons:
                mh = bk.mid_two(t + H)
                hz.append(mh if s == 0 else 1.0 - mh)
            rows.append((w.m, w.game_pk, w.period, w.wid, w.ws, t, s, a, n, q, size, y, fee, ref, ref_rx, ref_os,
                         kind, *hz))
    cols = ["m", "game_pk", "period", "wid", "ws", "ts", "s", "a", "n", "q", "size", "y", "fee_rate", "ref",
            "ref_rx", "ref_os", "kind"] + [f"ref_h{H}" for H in horizons]
    d = pd.DataFrame(rows, columns=cols)
    d["rebate"] = C.MAKER_REBATE * d.fee_rate * d.a * (1 - d.a)
    d["mo"] = d.a - d.ref + d.rebate          # per-share maker markout (primary metric)
    d["mo_rx"] = d.a - d.ref_rx + d.rebate    # same, relaxed one-sided reference where two-sided is missing
    d["mo_os"] = d.a - d.ref_os + d.rebate    # one-sided construction everywhere (validation only)
    d["hold"] = d.a - d.y + d.rebate          # per-share hold-to-resolution P&L
    for H in horizons:
        d[f"mo_h{H}"] = d.a - d[f"ref_h{H}"] + d.rebate
    d.attrs.update(n_win=n_win, n_ref=n_ref)
    return d


# ----------------------------------------------------------------------------- windows

def break_windows(u: pd.DataFrame, b: pd.DataFrame, t_stop: float) -> pd.DataFrame:
    w = b.merge(u[["m", "game_pk", "period"]], on="game_pk")
    w["ws"], w["we"] = w.b0 + WIN_START, w.b0 + t_stop
    w["r"] = w.we + REF_AFTER
    w["wid"] = np.arange(len(w))
    return w


def live_windows(u: pd.DataFrame, p: pd.DataFrame, h: pd.DataFrame, length: float = 100.0) -> pd.DataFrame:
    """Negative control (f): [PA start, PA start + 100 s] during live play. Greedy non-overlapping PA
    windows inside each half-inning (window + reference point end before that half's third out)."""
    p = p.dropna(subset=["start_ts", "end_ts"]).sort_values(["game_pk", "start_ts"])
    p = p.merge(h[["game_pk", "inning", "half", "he"]], on=["game_pk", "inning", "half"])
    rows = []
    for (gp, inn, hf), g in p.groupby(["game_pk", "inning", "half"], sort=False):
        last = -np.inf
        he = g.he.iloc[0]
        for s0 in g.start_ts.to_numpy():
            if s0 >= last + length + REF_AFTER and s0 + length + REF_AFTER <= he:
                rows.append((gp, s0))
                last = s0
    w = pd.DataFrame(rows, columns=["game_pk", "ws"]).merge(u[["m", "game_pk", "period"]], on="game_pk")
    w["we"] = w.ws + length
    w["r"] = w.we + REF_AFTER
    w["wid"] = np.arange(len(w))
    return w


def pause_windows(u: pd.DataFrame, p: pd.DataFrame, gap: float = 90.0) -> pd.DataFrame:
    """Variant (g): mid-inning pauses (next PA in the same half starts > 90 s after this PA ended),
    window [PA_end + 30, PA_end + 80]."""
    p = p.dropna(subset=["start_ts", "end_ts"]).sort_values(["game_pk", "start_ts"]).copy()
    p["nxt"] = p.groupby(["game_pk", "inning", "half"]).start_ts.shift(-1)
    x = p[(p.nxt - p.end_ts) > gap]
    w = x[["game_pk", "end_ts"]].merge(u[["m", "game_pk", "period"]], on="game_pk")
    w["ws"], w["we"] = w.end_ts + 30.0, w.end_ts + 80.0
    w["r"] = w.we + REF_AFTER
    w["wid"] = np.arange(len(w))
    return w


# ----------------------------------------------------------------------------- stats

def by_period(d: pd.DataFrame, periods=PERIODS):
    for per in periods:
        g = d[d.period.str.startswith("DEV")] if per == "DEV" else d[d.period == per]
        yield per, g


def ci(v, c, w) -> list:
    return list(C.cluster_ci(np.asarray(v, float), np.asarray(c), np.asarray(w, float)))


def summarize(d: pd.DataFrame, col: str = "mo", periods=PERIODS) -> dict:
    res = {}
    d = d[d[col].notna()]
    for per, g in by_period(d, periods):
        if len(g) == 0:
            res[per] = dict(fills=0)
            continue
        m, lo, hi = C.cluster_ci(g[col].to_numpy() * 100, g.game_pk.to_numpy(), g.n.to_numpy())
        cap = (1 - g.a) * g.n
        roi, rlo, rhi = C.cluster_ci((g[col] / (1 - g.a)).to_numpy(), g.game_pk.to_numpy(), cap.to_numpy())
        m1, lo1, hi1 = C.cluster_ci((g[col].to_numpy() - 0.01) * 100, g.game_pk.to_numpy(), g.n.to_numpy())
        r1 = C.cluster_ci(((g[col] - 0.01) / (1 - g.a + 0.01)).to_numpy(), g.game_pk.to_numpy(),
                          ((1 - g.a + 0.01) * g.n).to_numpy())
        res[per] = dict(fills=int(len(g)), games=int(g.game_pk.nunique()), windows=int(g.wid.nunique()),
                        shares=float(g.n.sum()), notional_a=float((g.n * g.a).sum()),
                        capital=float(cap.sum()), c_per_share=m, ci_lo=lo, ci_hi=hi,
                        roi_on_capital=roi, roi_lo=rlo, roi_hi=rhi,
                        c_per_share_minus1c=m1, ci_lo_minus1c=lo1, ci_hi_minus1c=hi1, roi_minus1c=r1[0],
                        pnl_usd=float((g[col] * g.n).sum()),
                        rebate_c=float((g.rebate * g.n).sum() / g.n.sum() * 100),
                        halfspread_c=float(((g.a - g.ref) * g.n).sum() / g.n.sum() * 100) if col == "mo" else None)
    return res


def extras(d: pd.DataFrame, horizons=()) -> dict:
    """Review-fix statistics on one set of simulated fills: realized P&L (hold) next to the markout, the
    paired hold-minus-markout (martingale) test, relaxed-reference markout, per-fill fixed-horizon
    markouts, matched-pair share, and the split by fill type (q == a vs q > a)."""
    res = {}
    for per, g in by_period(d):
        if len(g) < 5:
            continue
        r = {}
        gr = g[g.mo.notna()]
        r["mo"] = ci(gr.mo * 100, gr.game_pk, gr.n)
        r["hold_all"] = ci(g.hold * 100, g.game_pk, g.n)
        r["hold_same_fills"] = ci(gr.hold * 100, gr.game_pk, gr.n)
        r["hold_minus_mo"] = ci((gr.hold - gr.mo) * 100, gr.game_pk, gr.n)
        rx = g[g.mo_rx.notna()]
        r["mo_rx"] = ci(rx.mo_rx * 100, rx.game_pk, rx.n)
        one = rx[rx.kind == 1]
        r["mo_rx_onesided_windows"] = ci(one.mo_rx * 100, one.game_pk, one.n) if one.game_pk.nunique() >= 5 else None
        r["share_shares_onesided"] = float(one.n.sum() / g.n.sum())
        r["share_shares_no_print"] = float(g.n[g.kind == 0].sum() / g.n.sum())
        two = g[(g.kind == 2) & g.mo_os.notna()]
        r["validation_onesided_on_twosided"] = dict(two_sided=ci(two.mo * 100, two.game_pk, two.n),
                                                    one_sided_construction=ci(two.mo_os * 100, two.game_pk, two.n))
        for H in horizons:
            x = g[g[f"mo_h{H}"].notna()]
            r[f"mo_h{H}"] = ci(x[f"mo_h{H}"] * 100, x.game_pk, x.n)
            r[f"mo_h{H}_coverage"] = float(x.n.sum() / g.n.sum())
        sw = g.groupby(["wid", "s"]).n.sum().unstack(fill_value=0.0)
        matched = float(np.minimum(sw.get(0, 0.0), sw.get(1, 0.0)).sum())
        r["matched_pair_share"] = 2 * matched / float(g.n.sum())
        gt = gr.q > gr.a + EPS
        r["q_gt_a_share"] = float(gr.n[gt].sum() / gr.n.sum())
        r["mo_q_eq_a"] = ci(gr[~gt].mo * 100, gr[~gt].game_pk, gr[~gt].n) if gr[~gt].game_pk.nunique() >= 5 else None
        r["mo_q_gt_a"] = ci(gr[gt].mo * 100, gr[gt].game_pk, gr[gt].n) if gr[gt].game_pk.nunique() >= 5 else None
        r["capacity"] = dict(games=int(g.game_pk.nunique()),
                             days=int(pd.to_datetime(g.ws, unit="s").dt.date.nunique()),
                             shares=float(g.n.sum()), usd_at_a=float((g.n * g.a).sum()),
                             usd_capital=float((g.n * (1 - g.a)).sum()),
                             markout_usd=float((gr.mo * gr.n).sum()), hold_usd=float((g.hold * g.n).sum()),
                             shares_per_window=float(g.n.sum() / max(g.wid.nunique(), 1)))
        res[per] = r
    return res


def fmt(res: dict, label: str) -> str:
    lines = []
    for per, r in res.items():
        if per.startswith("_"):
            continue
        if not r.get("fills"):
            lines.append(f"{label:38s} {per:11s} no fills")
            continue
        lines.append(f"{label:38s} {per:11s} fills={r['fills']:7d} games={r['games']:5d} win={r['windows']:6d} "
                     f"sh={r['shares']:10.0f} ${r['notional_a']:10.0f} | {r['c_per_share']:+.3f}c "
                     f"[{r['ci_lo']:+.3f},{r['ci_hi']:+.3f}] roi={r['roi_on_capital']*100:+.2f}% "
                     f"[{r['roi_lo']*100:+.2f},{r['roi_hi']*100:+.2f}] -1c={r['c_per_share_minus1c']:+.3f}c "
                     f"pnl=${r['pnl_usd']:+.0f}")
    return "\n".join(lines)


# ----------------------------------------------------------------------------- feasibility (DEV only)

def feasibility_dev_only(U: pd.DataFrame, B: pd.DataFrame, books_raw: dict) -> dict:
    """Review fix: the feasibility scripts (explore22.py) split years by fee_rate == 0, so their '2026'
    sample included the Jul-Sep 2026 holdout. This recomputes the same number (taker-size-weighted maker
    markout of ALL taker fills in [b0+30, min(b0+100, b1-3)], reference = mean print p0 in [b1-60, b1+2],
    breaks 30..1200 s, all joined games, clustered by event_slug, no rebate) on DEV games only
    (game_start_ts < 2026-07-01). The reference uses b1, i.e. look-ahead: feasibility only."""
    u = U[U.period != "HOLDOUT"]
    b = B[(B.blen >= 30) & (B.blen <= 1200)].merge(u[["m", "game_pk", "period", "event_slug"]], on="game_pk")
    vals, wts, cl, per = [], [], [], []
    for w in b.itertuples(index=False):
        bk = books_raw.get(w.m)
        if bk is None:
            continue
        r0, r1 = np.searchsorted(bk.ts, w.b1 - 60, "left"), np.searchsorted(bk.ts, w.b1 + 2, "right")
        if r1 <= r0:
            continue
        p0 = np.where(bk.s[r0:r1] == 0, bk.q[r0:r1], 1 - bk.q[r0:r1]).mean()
        lo = np.searchsorted(bk.ts, w.b0 + 30, "left")
        hi = np.searchsorted(bk.ts, min(w.b0 + 100, w.b1 - 3), "right")
        if hi <= lo:
            continue
        s, q = bk.s[lo:hi], bk.q[lo:hi]
        vals.append(q - np.where(s == 0, p0, 1 - p0))
        wts.append(bk.size[lo:hi])
        cl.append(np.repeat(w.event_slug, hi - lo))
        per.append(np.repeat(w.period, hi - lo))
    v, wt, c, pr = (np.concatenate(x) for x in (vals, wts, cl, per))
    out = {}
    for p in ("DEV_2025", "DEV_2026H1"):
        k = pr == p
        out[p] = dict(fills=int(k.sum()), c_per_share=ci(v[k] * 100, c[k], wt[k]))
    return out


# ----------------------------------------------------------------------------- main

def main(dev_only: bool = False) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    U = universe()
    print(f"MLB moneylines joined to game_pk: {len(U)} markets, {U.game_pk.nunique()} games, "
          f"ambiguous game_pk rows {int(U.dup.sum())}")
    U_all = U[~U.dup].copy()                        # variant (d): all games
    U_pri = U_all[U_all.pre_usd >= PRE_USD_MIN].copy()   # primary universe (pregame-only selection)
    if dev_only:
        U_all = U_all[U_all.period != "HOLDOUT"]
        U_pri = U_pri[U_pri.period != "HOLDOUT"]
    print(U_pri.groupby("period").size().rename("primary games").to_string())

    P = plays(U_all.game_pk.unique())
    H = half_innings(P)
    B = breaks(H)
    print(f"plays: {len(P):,} PAs, {P.game_pk.nunique()} games; breaks {len(B):,}")

    # ---- frozen parameter T_stop: 5th pct of break length over all DEV games, rounded down to 5 s
    dev_g = U[(U.period != "HOLDOUT") & ~U.dup].game_pk
    bl = B[B.game_pk.isin(dev_g)].blen
    n_bad = int((bl <= 0).sum())
    p5 = float(np.percentile(bl[bl > 0], 5))
    t_stop = float(np.floor(p5 / 5.0) * 5.0)
    print(f"DEV break length (s): n={len(bl)} nonpositive={n_bad} p5={p5:.1f} -> T_stop={t_stop:.0f}s")
    res = dict(t_stop=t_stop, p5_raw=p5, dev_break_len=dict(n=int(len(bl)), nonpositive=n_bad,
               p10=float(bl.quantile(.1)), median=float(bl.median()), p90=float(bl.quantile(.9))))

    # ---- fills: split taker orders merged (review fix); raw rows kept for the before/after comparison
    F = load_fills(U_all.m.unique(), merge_split=True)
    books = {m: Book(g) for m, g in F.groupby("m", sort=False)}
    n_orders = len(F)
    del F
    Fr = load_fills(U_all.m.unique(), merge_split=False)
    res["split_rows"] = dict(rows=int(len(Fr)), taker_orders=n_orders, rows_per_order=len(Fr) / n_orders)
    books_raw = {m: Book(g) for m, g in Fr.groupby("m", sort=False)}
    del Fr
    print(f"books: {len(books)} markets; fill rows {res['split_rows']['rows']:,} -> taker orders {n_orders:,}")

    Wb = break_windows(U_pri, B, t_stop)
    Wb_all = break_windows(U_all, B, t_stop)
    Wb["leak"] = Wb.b1 + 2.6 < Wb.we
    res["leak_share"] = Wb.groupby("period").leak.mean().to_dict()

    # ---- half-spread for the relaxed one-sided reference, estimated on DEV two-sided windows only
    hs_rows = []
    for w in Wb[Wb.period != "HOLDOUT"].itertuples(index=False):
        bk = books.get(w.m)
        if bk is None:
            continue
        a, b = bk.last(0, w.r, REF_LOOKBACK), bk.last(1, w.r, REF_LOOKBACK)
        if a is not None and b is not None:
            hs_rows.append((w.period, 0.5 * (a + b - 1.0)))
    hsd = pd.DataFrame(hs_rows, columns=["period", "hs"]).groupby("period").hs.mean()
    HS = {"DEV_2025": float(hsd["DEV_2025"]), "DEV_2026H1": float(hsd["DEV_2026H1"]),
          "HOLDOUT": float(hsd["DEV_2026H1"])}           # holdout uses the frozen DEV 2026H1 estimate
    res["half_spread_fallback"] = HS
    print("half-spread for one-sided fallback (DEV-estimated):", HS)

    res["feasibility_dev_only"] = feasibility_dev_only(U_all, B, books_raw)
    print("feasibility (explore22 metric) on DEV only:", res["feasibility_dev_only"], flush=True)

    foq, fifo, xt = {}, {}, {}

    def go(store, name, win, bks=books, col="mo", horizons=(), save=True, **kw):
        d = run_windows(bks, win, hs=HS, horizons=horizons, **kw)
        if save:
            d.to_parquet(OUT / f"fills_{name}.parquet", index=False)
        s = summarize(d, col)
        s["_windows"] = dict(n_win=d.attrs["n_win"], n_with_ref=d.attrs["n_ref"])
        store[name] = s
        print(fmt(s, f"{name} [{col}]"), flush=True)
        return d

    # ================= FRONT-OF-QUEUE fill model (pre-registered primary + variants) =================
    go(foq, "primary_raw_rows", Wb, bks=books_raw, save=False)   # as originally run (split rows unmerged)
    del books_raw
    D = go(foq, "primary", Wb, horizons=HORIZONS)
    xt["foq_primary"] = extras(D, HORIZONS)
    go(foq, "a_K200", Wb, K=200.0)
    go(foq, "b_back_of_queue", Wb, back_of_queue=True)
    go(foq, "c_25pct", Wb, frac=0.25)
    go(foq, "d_all_games", Wb_all)
    foq["e_hold_to_resolution"] = summarize(D, "hold")
    print(fmt(foq["e_hold_to_resolution"], "e_hold_to_resolution [hold]"))
    go(foq, "f_negctrl_live", live_windows(U_pri, P, H))
    go(foq, "g_midinning_pause", pause_windows(U_pri, P))

    # ================= FIFO queue model (review fix; the verdict model) =================
    live = pd.read_parquet(OUT / "live_check_windows.parquet")
    Q_EMP = live.ask_sz.to_numpy(np.float64)
    res["queue_calibration"] = dict(source="live_check_windows.parquet ask_sz (touch size at b0+30, 2026-09-18/19)",
                                    n=int(len(Q_EMP)), median=float(np.median(Q_EMP)),
                                    p25=float(np.percentile(Q_EMP, 25)), p75=float(np.percentile(Q_EMP, 75)))
    Q_OPT = live.q0_eff_opt.to_numpy(np.float64)                       # optimistic bound
    Q_PROP = live[["ask_sz", "lam"]].to_numpy(np.float64)               # proportional cancellation (verdict)
    res["queue_calibration"].update(cancel_credited_median=float(np.median(Q_OPT)),
                                    prop_lambda_median=float(live.lam.median()),
                                    prop_survival_70s_median=float(live.surv_prop.median()))
    for q0 in QUEUE_GRID:
        go(fifo, f"Q0={q0:.0f}", Wb, queue=q0, save=False)
    # three queue-ahead assumptions bracketing cancellations (see live_check):
    #   emp       no cancellation ahead of us (pessimistic bound)
    #   emp_prop  proportional cancellation, (Q0, lambda) drawn jointly from one live window  <- VERDICT MODEL
    #   emp_cancel_credited  every cancellation at the level counted as ahead of us, at join (optimistic bound)
    for sd in EMP_SEEDS:
        nm = "emp_prop" if sd == 0 else f"emp_prop_seed{sd}"
        d = go(fifo, nm, Wb, queue=Q_PROP, seed=sd, horizons=HORIZONS if sd == 0 else (), save=(sd == 0))
        if sd == 0:
            DF = d
            xt["fifo_emp_prop"] = extras(DF, HORIZONS)
    d = go(fifo, "emp", Wb, queue=Q_EMP)
    xt["fifo_emp"] = extras(d)
    d = go(fifo, "emp_cancel_credited", Wb, queue=Q_OPT)
    xt["fifo_emp_cancel_credited"] = extras(d)
    # pre-registered variants re-run under the verdict (proportional-cancel FIFO) fill model
    go(fifo, "a_K200", Wb, queue=Q_PROP, K=200.0, save=False)
    go(fifo, "b_back_of_queue", Wb, queue=Q_PROP, back_of_queue=True, save=False)
    go(fifo, "c_25pct", Wb, queue=Q_PROP, frac=0.25, save=False)
    go(fifo, "d_all_games", Wb_all, queue=Q_PROP, save=False)
    fifo["e_hold_to_resolution"] = summarize(DF, "hold")
    print(fmt(fifo["e_hold_to_resolution"], "FIFO e_hold_to_resolution [hold]"))
    go(fifo, "f_negctrl_live", live_windows(U_pri, P, H), queue=Q_PROP, save=False)
    go(fifo, "g_midinning_pause", pause_windows(U_pri, P), queue=Q_PROP, save=False)
    res["foq"], res["fifo"], res["extras"] = foq, fifo, xt

    # ---- diagnostics on the front-of-queue primary fills (unchanged from the first run)
    diag = {}
    D = D.merge(Wb[["wid", "b0", "b1", "leak"]], on="wid")
    D["t_rel"] = D.ts - D.b0
    for per, g in D[D.ref.notna()].groupby("period"):
        cap = g.groupby(["wid", "s"]).n.sum()
        diag[per] = dict(
            leak_fill_share=float(g.leak.mean()),
            mo_leak_c=float((g.mo * g.n)[g.leak].sum() / max(g.n[g.leak].sum(), 1e-9) * 100),
            mo_noleak_c=float((g.mo * g.n)[~g.leak].sum() / g.n[~g.leak].sum() * 100),
            capped_side_windows=float((cap >= CAP_PER_SIDE - 1e-6).mean()),
            price_improve_share=float((g.q > g.a + EPS).mean()),
            mean_a=float((g.a * g.n).sum() / g.n.sum()),
            fills_per_game=float(len(g) / g.game_pk.nunique()),
            usd_per_game=float((g.n * g.a).sum() / g.game_pk.nunique()))
        g2 = g.assign(tb=pd.cut(g.t_rel, [WIN_START - 1, 50, 75, t_stop + 1]))
        diag[per]["mo_by_time_c"] = {str(k): float((v.mo * v.n).sum() / v.n.sum() * 100)
                                     for k, v in g2.groupby("tb", observed=True)}
    res["diagnostics"] = diag
    flow = {}
    for w in Wb.itertuples(index=False):
        bk = books.get(w.m)
        if bk is None:
            continue
        lo, hi = np.searchsorted(bk.ts, w.ws, "left"), np.searchsorted(bk.ts, w.we, "right")
        flow[w.period] = flow.get(w.period, 0.0) + float((bk.size[lo:hi] * bk.q[lo:hi]).sum())
    res["taker_usd_in_windows"] = flow

    # ---- verdict (rule fixed in the review before the holdout re-run; derived from the FIFO model with
    #      proportional cancellation calibrated on the live books)
    if not dev_only:
        R = fifo["emp_prop"]
        gate_b = foq["b_back_of_queue"]["HOLDOUT"]["c_per_share"] >= 0
        dev_lo, dev_hi, ho = R["DEV"]["ci_lo"], R["DEV"]["ci_hi"], R["HOLDOUT"]["c_per_share"]
        if ho > 0 and dev_lo > 0 and gate_b:
            v = "PROFITABLE"
        elif ho > 0 and dev_hi >= 0:
            v = "PROMISING"
        else:
            v = "DEAD"
        res["verdict"] = dict(verdict=v, model="FIFO, (queue ahead, cancel rate) drawn from live windows, seed 0",
                              holdout_c=ho, dev_ci=[R["DEV"]["c_per_share"], dev_lo, dev_hi], gate_b_passes=bool(gate_b),
                              rule="PROFITABLE: FIFO holdout markout > 0 and FIFO DEV CI lo > 0 and (b) holdout >= 0; "
                                   "DEAD: FIFO holdout markout <= 0 or FIFO DEV CI entirely < 0; else PROMISING")
        print("VERDICT", json.dumps(res["verdict"]))

    fn = OUT / ("results_dev.json" if dev_only else "results.json")
    with open(fn, "w") as fh:
        json.dump(res, fh, indent=1, default=float)
    print(f"wrote {fn}")
    return res


# ----------------------------------------------------------------------------- live realism check

def live_check(t_stop: float) -> dict:
    """Report-only realism check on the recorded order books of 2026-09-18/19 (UTC evening of 09-18).

    Break start b0 = receive time of the first MLB-feed message with outs == 3 for a half-inning
    (feed latency makes b0 a few seconds late). For each moneyline token and break: the best ask and
    the shares resting there at b0+30 s (the queue a new maker would join behind), and the taker
    volume that acquired the token at that price during [b0+30, b0+T_stop] (BUY of the token at p,
    or SELL of the other token at 1-p; deduplicated by transaction hash).

    Review fix: it also replays a 50-share-clip maker who joins that level at b0+30 under two queue
    rules. FIFO: only trades at the level move us forward. Cancel-credited (optimistic bound): every
    size decrease at the level (trades and cancellations) counts as coming from ahead of us. Trades
    above the level mean the level is gone, and both rules fill us. q0_eff_opt = queue ahead net of
    all cancellations at the level during the window (optimistic queue sample for the FIFO backtest)."""
    import re
    from collections import defaultdict
    gm = pd.DataFrame([json.loads(l)["game"] for l in open(C.DATA / "live" / "2026-09-18" / "games.jsonl")])
    sch = pd.read_parquet(C.DATA / "live" / "2026-09-18" / "schedule.parquet",
                          columns=["game_pk", "away_name", "home_name"])
    gm = gm.merge(sch, left_on=["away_team", "home_team"], right_on=["away_name", "home_name"])
    cid_of_pk = dict(zip(gm.game_pk, gm.condition_id))
    toks = {c: (str(h), str(a)) for c, h, a in zip(gm.condition_id, gm.home_token, gm.away_token)}
    tok2cid = {t: c for c, tt in toks.items() for t in tt}
    other = {tt[0]: tt[1] for tt in toks.values()} | {tt[1]: tt[0] for tt in toks.values()}
    pat = re.compile("|".join(re.escape(c) for c in toks))
    rows = []

    def lvl_update(w, new):
        if new < w["lvl"]:
            d = w["lvl"] - new
            w["dec"] += d
            w["qa_opt"] -= d
            w["h_dec"] += d / w["lvl"]
        w["lvl"] = new

    def fill(w, key, n):
        n = min(n, K_CLIP, CAP_PER_SIDE - w[key])
        if n > 0:
            w[key] += n

    for day in ("2026-09-18", "2026-09-19"):
        base = C.DATA / "live" / day
        st = pd.DataFrame([json.loads(l) for l in open(base / "mlb.jsonl")])
        st = st[st.outs == 3].sort_values("recv_ms").drop_duplicates(["game_pk", "inning", "half"])
        st["cid"] = st.game_pk.map(cid_of_pk)
        st = st[st.cid.notna()]
        # skip the final half (game over): require a later message for the same game
        last = pd.DataFrame([json.loads(l) for l in open(base / "mlb.jsonl")]).groupby("game_pk").recv_ms.max()
        st = st[st.recv_ms + 60_000 < st.game_pk.map(last)]
        ev = []
        for r in st.itertuples():
            for t in toks[r.cid]:
                ev.append((r.recv_ms + WIN_START * 1000, 0, t, r.game_pk, r.inning, r.half))
                ev.append((r.recv_ms + t_stop * 1000, 1, t, r.game_pk, r.inning, r.half))
        ev.sort()
        books, open_w, by_tok, ei = {}, {}, defaultdict(set), 0
        with open(base / "clob.jsonl") as fh:
            for line in fh:
                if not pat.search(line):
                    continue
                rec = json.loads(line)
                now = rec["recv_ms"]
                while ei < len(ev) and ev[ei][0] <= now:
                    tm, kind, t, gp, inn, hf = ev[ei]
                    key = (t, gp, inn, hf)
                    if kind == 0:
                        bk = books.get(t)
                        if bk and bk["a"] and bk["b"]:
                            pa, pb = min(bk["a"]), max(bk["b"])
                            s0 = bk["a"][pa]
                            open_w[key] = dict(token=t, game_pk=gp, inning=inn, half=hf, ask=pa, ask_sz=s0, bid=pb,
                                               vol_at=0.0, vol_above=0.0, vol_all=0.0, tx=set(), lvl=s0, dec=0.0,
                                               qa_fifo=s0, qa_opt=s0, fill_fifo=0.0, fill_opt=0.0,
                                               fill_foq=0.0, h_dec=0.0, h_tr=0.0)
                            by_tok[t].add(key)
                    elif key in open_w:
                        w = open_w.pop(key)
                        by_tok[t].discard(key)
                        w.pop("tx")
                        rows.append(w)
                    ei += 1
                msgs = rec["msg"] if isinstance(rec["msg"], list) else [rec["msg"]]
                for m in msgs:
                    et = m.get("event_type")
                    if "bids" in m and "asks" in m:
                        t = m.get("asset_id")
                        if t in tok2cid:
                            books[t] = {"a": {float(x["price"]): float(x["size"]) for x in m["asks"]},
                                        "b": {float(x["price"]): float(x["size"]) for x in m["bids"]}}
                            for key in by_tok.get(t, ()):
                                w = open_w[key]
                                lvl_update(w, books[t]["a"].get(w["ask"], 0.0))
                    elif "price_changes" in m:
                        for pc in m["price_changes"]:
                            t = pc.get("asset_id")
                            if t not in books:
                                continue
                            sd = "b" if pc["side"] == "BUY" else "a"
                            p, sz = float(pc["price"]), float(pc["size"])
                            if sz <= 0:
                                books[t][sd].pop(p, None)
                            else:
                                books[t][sd][p] = sz
                            if sd == "a":
                                for key in by_tok.get(t, ()):
                                    w = open_w[key]
                                    if p == w["ask"]:
                                        lvl_update(w, max(sz, 0.0))
                    elif et == "last_trade_price":
                        t = m.get("asset_id")
                        if t not in tok2cid:
                            continue
                        p, sz = float(m["price"]), float(m["size"])
                        # taker acquired token `acq` at price `pp`
                        acq, pp = (t, p) if m["side"] == "BUY" else (other[t], round(1 - p, 4))
                        for key in by_tok.get(acq, ()):
                            w = open_w[key]
                            txk = (m.get("transaction_hash"), round(pp, 4), sz)
                            if txk in w["tx"]:
                                continue
                            w["tx"].add(txk)
                            w["vol_all"] += sz
                            if abs(pp - w["ask"]) < 1e-6:
                                w["vol_at"] += sz
                                if w["lvl"] > 0:
                                    w["h_tr"] += min(sz, w["lvl"]) / w["lvl"]
                                fill(w, "fill_foq", sz)
                                take = min(w["qa_fifo"], sz)
                                w["qa_fifo"] -= take
                                fill(w, "fill_fifo", sz - take)
                                if w["qa_opt"] <= 1e-9:
                                    fill(w, "fill_opt", sz)
                            elif pp > w["ask"] + 1e-6:
                                w["vol_above"] += sz
                                w["qa_fifo"] = w["qa_opt"] = 0.0
                                fill(w, "fill_foq", sz)
                                fill(w, "fill_fifo", sz)
                                fill(w, "fill_opt", sz)
        print(day, "windows so far", len(rows), flush=True)
    r = pd.DataFrame(rows)
    if r.empty:
        return dict(note="no windows reconstructed")
    r["spread"] = r.ask - r.bid
    r["fill_back"] = np.clip(r.vol_at - r.ask_sz, 0, None)
    r["cancel"] = np.clip(r.dec - r.vol_at, 0, None)
    r["q0_eff_opt"] = np.clip(r.ask_sz - r.cancel, 0, None)
    # proportional cancellation: cancel hazard = sum(decrease/level) - sum(trade/level); survival of the
    # queue ahead over the window = exp(-hazard); lam = hazard per second of window
    r["haz_cancel"] = np.clip(r.h_dec - r.h_tr, 0, None)
    r["surv_prop"] = np.exp(-r.haz_cancel)
    r["lam"] = r.haz_cancel / (t_stop - WIN_START)
    r.to_parquet(OUT / "live_check_windows.parquet", index=False)
    out = dict(token_windows=int(len(r)), games=int(r.game_pk.nunique()),
               touch_ask_shares_median=float(r.ask_sz.median()), touch_ask_shares_p25=float(r.ask_sz.quantile(.25)),
               touch_ask_shares_p75=float(r.ask_sz.quantile(.75)),
               touch_ask_usd_median=float((r.ask_sz * r.ask).median()),
               spread_median_c=float(r.spread.median() * 100), share_spread_le_1c=float((r.spread <= 0.0101).mean()),
               taker_vol_at_touch_median=float(r.vol_at.median()), taker_vol_at_touch_mean=float(r.vol_at.mean()),
               taker_vol_any_price_mean=float(r.vol_all.mean()),
               share_windows_vol_exceeds_queue=float((r.vol_at > r.ask_sz).mean()),
               mean_back_of_queue_fill_shares=float(np.minimum(r.fill_back, CAP_PER_SIDE).mean()),
               ratio_vol_at_to_queue_median=float((r.vol_at / r.ask_sz.clip(lower=1)).median()),
               cancel_share_of_queue_median=float((r.cancel / r.ask_sz.clip(lower=1)).median()),
               cancel_share_of_queue_mean=float((r.cancel / r.ask_sz.clip(lower=1)).clip(upper=1).mean()),
               prop_survival_median=float(r.surv_prop.median()), prop_survival_mean=float(r.surv_prop.mean()),
               prop_queue_left_at_end_median=float((r.ask_sz * r.surv_prop).median()),
               q0_eff_opt_median=float(r.q0_eff_opt.median()), q0_eff_opt_p25=float(r.q0_eff_opt.quantile(.25)),
               share_q0_eff_opt_zero=float((r.q0_eff_opt <= 1e-6).mean()),
               replay_fill_fifo_mean=float(r.fill_fifo.mean()), replay_fill_fifo_any=float((r.fill_fifo > 0).mean()),
               replay_fill_opt_mean=float(r.fill_opt.mean()), replay_fill_opt_any=float((r.fill_opt > 0).mean()),
               replay_fill_front_of_queue_mean=float(r.fill_foq.mean()),
               replay_fill_front_of_queue_any=float((r.fill_foq > 0).mean()))
    print(json.dumps(out, indent=1), flush=True)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev-only", action="store_true")
    ap.add_argument("--live", action="store_true")
    a = ap.parse_args()
    if a.live:
        rr = json.load(open(OUT / "results_dev.json"))
        lv = live_check(rr["t_stop"])
        json.dump(lv, open(OUT / "live_check.json", "w"), indent=1)
        sys.exit(0)
    main(dev_only=a.dev_only)
