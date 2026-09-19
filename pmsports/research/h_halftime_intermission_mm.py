"""Hypothesis `halftime_intermission_mm`: passive market making during the soccer halftime blackout.

Mechanism: a ~15 min scheduled interval with no game information. Realized volatility falls to ~1/3
of its in-play level while taker flow continues, so a resting quote at the last traded price earns the
spread (+ maker rebate) without being picked off by goals.

Primary rule (pre-registered; parameters frozen):
  UNIVERSE  soccer moneyline legs (family=='soccer', o0/o1 == Yes/No), pre_usd >= $25k.
            KO = game_start_ts (scheduled). DEV = KO < 2026-07-01, HOLDOUT = KO >= 2026-07-01.
  WINDOW    W = [KO+52 min, KO+60 min] (fixed from the schedule).
  SKIP      skip the whole event if, for any of its universe legs, the Yes-converted fill prices in
            [KO+49 min, KO+52 min) span > 0.03 (late-goal / late-kickoff proxy; known at KO+52).
  QUOTE     our ask on token s at taker-fill time t = price of the last fill acquiring s with
            ts <= t-3 s (the highest price at that last timestamp = end of a sweep); no quote if that fill
            is older than 120 s.
  FILL      a taker fill acquiring s at q >= our ask fills min(size, K=50) shares at our ask;
            cumulative cap 1,000 shares per token per leg.
  METRIC    per-share markout = a - ref_s + 0.15*fee_rate*a*(1-a), ref = the leg's two-sided print mid at
            KO+61 (last Yes-acquire and last No-acquire print in [KO+59, KO+61]; leg dropped if either is
            missing). Share-weighted, C.cluster_ci by event_slug. ROI = markout $ / capital $ with capital
            per share = 1-a (selling s at a = buying the complement at 1-a).
Variants: (a) K=200 (b) back-of-queue stress (c) 25% of each fill (d) hold to resolution
          (e) in-play control windows (f) basketball/hockey intermission fitted on DEV 2025.

VERDICT AFTER REVIEW: DEAD. The pre-registered back-of-queue gate and the queue model calibrated on live soccer
halftime books are both <= 0 in the holdout; the edge exists only for makers already at the front of the queue.

Review fixes (2026-09-19; no parameter of the primary rule changed):
  * The FILL rule above is a FRONT-of-queue model (every print at our price fills us, instantly replenished).
    The protocol requires realistic queue/fill limits, so the verdict is now gated on realistic execution:
      - the pre-registered back-of-queue stress (b), and
      - a one-resting-order QUEUE model (simulate(queue=Q)): one order of K at a with Q shares ahead; a print
        at q == a eats the queue ahead first, a print at q > a (level traded through) fills us; we rejoin at
        the back after a reprice or, once fully filled, at the next quote epoch. Q comes from MEASURED book
        depth at the last-print level 3 s after each print (live_depth(): today's soccer halftime capture,
        MLB breaks as fallback) and is drawn per (re)join from that empirical distribution.
    verdict = DEAD if either realistic holdout ROI <= 0; otherwise protocol rules under realistic fills.
  * Rebate grid {0, 5, 10, 15, 20%} and the verdict with no rebate (the 15% per-fill rebate is an assumption).
  * ref_mode="asof": markout ref = last two-sided print mid at <= KO+61 of any age (no leg is dropped for lack of
    post-window prints), next to the pre-registered ref.
  * Robustness block (robustness()): outliers, leave-one-league-out, World Cup excluded, months, alternative
    clustering, rebate grid, fills at q == a vs q > a.
  * Price-equality tolerance PX_TOL = 1e-6 (float32 prices; EPS = 1e-9 made the skip rule, the fill test and the
    back-of-queue 'higher price' test treat one tick as two prices). px_tol=EPS reproduces the as-run numbers.
  * Report-only live checks on the 2026-09-19 capture: live_depth() (queue at the last-print level, touch order
    flow) and book_sim()/live_check_soccer() (our rule against the recorded book, queue tracked from the book).

Run:  PYTHONPATH=. systemd-run --user --scope -q -p MemoryMax=4G -p MemorySwapMax=0 .venv/bin/python \
        -m pmsports.research.h_halftime_intermission_mm [--holdout]
      ... -m pmsports.research.h_halftime_intermission_mm --live-depth mlb|soccer   (queue-depth calibration)
      ... -m pmsports.research.h_halftime_intermission_mm --record HOURS            (live capture, read-only)
Without --holdout every row with KO >= 2026-07-01 is dropped right after loading.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from pmsports.research import common as C

SLUG = "halftime_intermission_mm"
CACHE = C.RESEARCH / f"h_{SLUG}"
T_2026 = pd.Timestamp("2026-01-01", tz="UTC").timestamp()
T_HOLD = pd.Timestamp("2026-07-01", tz="UTC").timestamp()
PRE_USD_MIN = 25_000
K_DEFAULT, CAP = 50, 1000
QUOTE_DELAY, QUOTE_MAX_AGE = 3, 120
SKIP_SPAN = 0.03
BOQ_SECONDS = 10
EPS = 1e-9                                  # as originally run (too tight for float32 prices, see PX_TOL)
PX_TOL = 1e-6                               # review fix: price equality tolerance. Prices are float32 derived from
                                            # usdc/size, so one tick shows up as e.g. 0.109999985 and 0.10999996;
                                            # the smallest tick is 0.001, so 1e-6 separates noise from real levels
MIN = 60

# soccer windows (minutes from scheduled KO): (window, skip window, ref window)
PRIMARY = dict(win=(52, 60), skip=(49, 52), ref=(59, 61))
CONTROLS = {"ctrl_1H_[20,30]": dict(win=(20, 30), skip=(17, 20), ref=(29, 31)),
            "ctrl_2H_[70,80]": dict(win=(70, 80), skip=(67, 70), ref=(79, 81))}
SOCCER_REL = (15 * MIN, 85 * MIN)          # fills cached per soccer leg (covers all windows + 120 s lookback)
BH_REL = (0, 200 * MIN)                    # basketball/hockey: fitting range
BH_GROUPS = {"NBA": ["nba", "nba-2026"], "WNBA": ["wnba"], "NHL": ["nhl", "nhl-2026"]}
BH_LEN = 8                                 # (f): window length (min) = soccer's
BH_SEARCH = (30, 100)                      # (f): window searched inside [KO+30, KO+100] min = the first intermission
BH_SEARCH_WIDE = (30, 170)                 #      (the unrestricted search picks game ends; reported as a diagnostic)

# review fixes
Q_GRID = [0, 100, 250, 500, 1000, 2000, 5000, 20000, np.inf]   # shares ahead of our order at the level
REBATE_GRID = [0.0, 0.05, 0.10, 0.15, 0.20]
JOIN_DELAY_MS = 3000                        # we (re)join the level >= 3 s after the print that set the quote
LIVE = CACHE / "live"
LIVE_DEPTH_JSON = CACHE / "live_depth.json"
MLB_LIVE_DAYS = ["2026-09-18", "2026-09-19"]
MIN_HT_PRINTS = 100                         # soccer-HT depth sample needed to calibrate Q (else MLB breaks)
US_MX = ("mls", "mex", "soccer-lec", "concacaf", "liga-mx", "usl")   # post-hoc diagnostic only (soccer-lec = Leagues Cup)


def period(ko):
    ko = np.asarray(ko, float)
    return np.where(ko < T_2026, "dev25", np.where(ko < T_HOLD, "dev26h1", "hold"))


# ----------------------------------------------------------------------------- data

def soccer_legs(holdout: bool) -> pd.DataFrame:
    mk = C.markets()
    L = mk[(mk.family == "soccer") & (mk.o0 == "Yes") & (mk.o1 == "No") & (mk.pre_usd >= PRE_USD_MIN)].copy()
    if not holdout:
        L = L[L.game_start_ts < T_HOLD]
    L["per"] = period(L.game_start_ts)
    L["leg"] = np.where(L.market_slug.str.endswith("-draw"), "draw", "team")
    return L[["m", "event_slug", "market_slug", "league", "game_start_ts", "fee_rate", "per", "leg", "y0", "y1"]]


def load_fills(legs: pd.DataFrame, rel: tuple[int, int], name: str) -> pd.DataFrame:
    """Fills of `legs` with rel = ts - KO in [rel0, rel1], cached (all periods) under CACHE/name."""
    CACHE.mkdir(parents=True, exist_ok=True)
    fn = CACHE / f"{name}.parquet"
    if fn.exists():
        f = pd.read_parquet(fn)
    else:
        f = C.fills(markets=legs.m.values, columns=["m", "ts", "size", "s", "q", "y", "fee_rate"])
        f = f.merge(legs[["m", "game_start_ts"]], on="m")
        f["rel"] = (f.ts - f.game_start_ts).astype(np.int64)
        f = f[(f.rel >= rel[0]) & (f.rel <= rel[1])].drop(columns="game_start_ts")
        f.to_parquet(fn, index=False)
    f = f[f.m.isin(legs.m)].copy()
    for c in ("size", "q", "y", "fee_rate"):
        f[c] = f[c].astype(np.float64)
    f["s"] = f["s"].astype(np.int64)
    f["g"] = f.m.astype(np.int64) * 2 + f.s
    f = f.sort_values(["g", "ts"], kind="stable").reset_index(drop=True)
    return f


def cache_soccer(holdout: bool):
    """Build the soccer cache from ALL periods once (so the holdout rows need no re-read), return filtered."""
    legs_all = soccer_legs(True)
    fn = CACHE / "soccer_fills.parquet"
    if not fn.exists():
        load_fills(legs_all, SOCCER_REL, "soccer_fills")
    legs = soccer_legs(holdout)
    return legs, load_fills(legs, SOCCER_REL, "soccer_fills")


# ----------------------------------------------------------------------------- simulation

def _last_price_table(f: pd.DataFrame, tie: str = "max"):
    """Per (g, ts): max price (= the end of a sweep: the operator fills maker orders best-first), or with
    tie="last" the last row in data order within that second (the h_mlb_inning_break_mm convention)."""
    gb = f.groupby(["g", "ts"], sort=True).q
    t = (gb.max() if tie == "max" else gb.last()).reset_index()
    key = (t.g.to_numpy(np.int64) << 32) + t.ts.to_numpy(np.int64)
    return key, t.ts.to_numpy(np.int64), t.q.to_numpy(float)


def leg_ref(f: pd.DataFrame, ref: tuple[float, float]) -> pd.Series:
    """Two-sided print mid of token 0 per leg: last token-0 acquire (ask0) and last token-1 acquire
    (bid0 = 1 - q1) in [ref0, ref1] (seconds from KO). NaN if either side is missing."""
    r = f[(f.rel >= ref[0]) & (f.rel <= ref[1])]
    last = r.groupby(["m", "s"]).ts.transform("max")
    r = r[r.ts == last].groupby(["m", "s"]).q.max().unstack()
    r = r.reindex(columns=[0, 1])
    return ((r[0] + (1 - r[1])) / 2).rename("mid0")


def leg_ref_asof(f: pd.DataFrame, t_end: float) -> pd.Series:
    """Review fix (no selection on post-window activity): two-sided print mid of token 0 from the LAST token-0
    and token-1 acquires at rel <= t_end, of any age (the cache starts at KO+15 min). If one side never traded,
    the other side's last print is used (one-sided). No leg with window fills can be dropped."""
    r = f[f.rel <= t_end]
    last = r.groupby(["m", "s"]).ts.transform("max")
    r = r[r.ts == last].groupby(["m", "s"]).q.max().unstack().reindex(columns=[0, 1])
    ask0, bid0 = r[0], 1 - r[1]
    return ((ask0 + bid0) / 2).fillna(ask0).fillna(bid0).rename("mid0")


def skipped_events(f: pd.DataFrame, legs: pd.DataFrame, skip: tuple[float, float] | None, px_tol=PX_TOL) -> set:
    if skip is None:
        return set()
    r = f[(f.rel >= skip[0]) & (f.rel < skip[1])]
    p0 = np.where(r.s == 0, r.q, 1 - r.q)
    span = pd.Series(p0).groupby(r.m.to_numpy()).agg(lambda x: x.max() - x.min())
    bad = span[span > SKIP_SPAN + px_tol].index
    return set(legs.loc[legs.m.isin(bad), "event_slug"])


def tick_ceil(a):
    """Round a quote UP to the tick grid (0.01 in [0.04, 0.96], else 0.001): averaged-sweep prints are off-grid."""
    a = np.asarray(a, float)
    grid = np.where((a >= 0.04) & (a <= 0.96), 0.01, 0.001)
    return np.round(np.ceil(a / grid - 1e-4) * grid, 6)


def _queue_fill(g, has, ep, a, q, size, K, cap, queue, tol, seed=0, through_full=False):
    """One resting order per token (sequential in time). `queue` = shares ahead of us when we (re)join the
    level: a number, or an array = empirical distribution drawn from at every (re)join (seeded).
    q == a: the print consumes the queue ahead first, we get what is left of it; q > a: the level was traded
    through, we are filled (<= print size; through_full=True: the whole remaining order = the reviewer's reference
    semantics, since a sweep recorded as several rows can be larger than the row that traded through). Rejoin at the back on a reprice (cancel/replace) or, after a full fill,
    at the next quote epoch (a new print setting the quote). Cumulative cap `cap` per token."""
    rng = np.random.default_rng(seed)
    dist = None if np.isscalar(queue) else np.asarray(queue, float)

    def draw():
        return float(queue) if dist is None else float(dist[rng.integers(len(dist))])

    sh = np.zeros(len(g))
    cur, p_ord, rem, qa, ep_ord, tot = None, np.nan, 0.0, 0.0, -2, 0.0
    for i in range(len(g)):
        if g[i] != cur:
            cur, p_ord, rem, qa, ep_ord, tot = g[i], np.nan, 0.0, 0.0, -2, 0.0
        if not has[i]:
            p_ord, rem = np.nan, 0.0              # no quote: order cancelled
            continue
        if not abs(a[i] - p_ord) < 1e-9:          # new price: cancel/replace, back of the queue
            p_ord, rem, qa, ep_ord = a[i], float(K), draw(), ep[i]
        elif rem <= 0 and ep[i] != ep_ord:        # fully filled earlier: repost at the back at the next epoch
            rem, qa, ep_ord = float(K), draw(), ep[i]
        if rem <= 0 or tot >= cap or q[i] < a[i] - tol:
            continue
        if q[i] > a[i] + tol:                     # level traded through
            x = rem if through_full else min(rem, size[i])
        else:
            x = min(max(size[i] - qa, 0.0), rem)
            qa = max(qa - size[i], 0.0)
        x = min(x, cap - tot)
        if x > 0:
            sh[i] = x
            rem -= x
            tot += x
    return sh


def simulate(f: pd.DataFrame, legs: pd.DataFrame, win, skip, ref, K=K_DEFAULT, cap=CAP, frac=1.0,
             boq=False, hold=False, shade=0.0, minutes=True, require_ref=True, tie="max", boq_strict=False,
             queue=None, rebate=C.MAKER_REBATE, ref_mode="window", delay=QUOTE_DELAY, tick=False, agg_second=False,
             px_tol=PX_TOL, through_full=False, seed=0):
    """Maker fills in window `win` (minutes from KO unless minutes=False). Returns one row per filled
    taker print with our shares `sh`, our price `a`, and the per-share P&L `mo` (markout or resolution).
    queue=None: pre-registered front-of-queue fill; queue=Q (number or empirical array): one-resting-order model.
    ref_mode='asof': ref = last two-sided print mid at <= ref end, any age (no ref-based leg drop).
    px_tol: price-equality tolerance (PX_TOL; px_tol=EPS reproduces the numbers as originally run)."""
    f = f.sort_values(["g", "ts"], kind="stable").reset_index(drop=True)   # index == position (BOQ lookup)
    sc = MIN if minutes else 1
    win = (win[0] * sc, win[1] * sc)
    ref = (ref[0] * sc, ref[1] * sc)
    skip = None if skip is None else (skip[0] * sc, skip[1] * sc)
    mid0 = leg_ref(f, ref) if ref_mode == "window" else leg_ref_asof(f, ref[1])
    bad_ev = skipped_events(f, legs, skip, px_tol)
    ok_legs = legs[~legs.event_slug.isin(bad_ev)].merge(mid0, left_on="m", right_index=True, how="left")
    if require_ref or not hold:   # the markout needs a ref; hold-to-resolution can keep every non-skipped leg
        ok_legs = ok_legs[ok_legs.mid0.notna()]
    info = dict(legs_total=int(len(legs)), events_total=int(legs.event_slug.nunique()),
                events_skipped=int(len(bad_ev)), legs_no_ref=int((~legs.m.isin(mid0.dropna().index)).sum()),
                legs_used=int(len(ok_legs)), events_used=int(ok_legs.event_slug.nunique()))

    key, tts, tq = _last_price_table(f, tie)
    cand = f[(f.rel >= win[0]) & (f.rel <= win[1]) & f.m.isin(ok_legs.m)]
    if agg_second:   # prints sharing (token, second) = one taker event (one taker order split across makers)
        assert not boq, "back-of-queue lookup needs row positions"
        cand = (cand.groupby(["g", "ts"], sort=True)
                .agg(m=("m", "first"), s=("s", "first"), rel=("rel", "first"), size=("size", "sum"),
                     q=("q", "max"), y=("y", "first"), fee_rate=("fee_rate", "first")).reset_index())
    info["taker_shares_in_window"] = float(cand["size"].sum())
    info["taker_shares_in_window_by_per"] = cand["size"].groupby(cand.m.map(legs.set_index("m").per)).sum().to_dict()
    g = cand.g.to_numpy(np.int64)
    t = cand.ts.to_numpy(np.int64)
    j = np.searchsorted(key, (g << 32) + t - delay, side="right") - 1
    jj = np.clip(j, 0, None)
    has = (j >= 0) & ((key[jj] >> 32) == g) & (tts[jj] >= t - QUOTE_MAX_AGE)
    a = np.where(has, tq[jj], np.nan)
    if tick:
        a = np.where(has, tick_ceil(a), np.nan)
    tol = max(px_tol, 1e-6) if tick else px_tol   # float32 prices vs exact float64 ticks
    q = cand.q.to_numpy(float)
    hit = has & (q >= a - tol)

    if boq:  # back-of-queue: a different same-side print at a price > a within [t, t+10 s]
        rows = np.flatnonzero(hit)
        gkey = (f.g.to_numpy(np.int64) << 32) + f.ts.to_numpy(np.int64)
        fq = f.q.to_numpy(float)
        cidx = cand.index.to_numpy()
        # default: any other same-side print in [t, t+10]; boq_strict: only prints at ts > t (MLB convention)
        lo = np.searchsorted(gkey, (g[rows] << 32) + t[rows], side="right" if boq_strict else "left")
        hi = np.searchsorted(gkey, (g[rows] << 32) + t[rows] + BOQ_SECONDS, side="right")
        keep = np.zeros(len(rows), bool)
        for k, (r, l, h) in enumerate(zip(rows, lo, hi)):
            seg = fq[l:h].copy()
            self_pos = cidx[r] - l
            if 0 <= self_pos < len(seg):
                seg[self_pos] = -1.0
            keep[k] = (seg > a[r] + tol).any()
        hit2 = np.zeros_like(hit)
        hit2[rows[keep]] = True
        hit = hit2

    cols = ["m", "g", "s", "ts", "rel", "size", "q", "y", "fee_rate"]
    if queue is None:   # pre-registered front-of-queue fill (unchanged)
        d = cand.loc[hit, cols].copy()
        d["a"] = a[hit]
        d["want"] = np.minimum(d["size"] * frac, K)
        cum = d.groupby("g").want.cumsum().clip(upper=cap)
        d["sh"] = cum - cum.groupby(d.g).shift(1).fillna(0.0)
    else:
        assert not boq and frac == 1.0
        sh = _queue_fill(g, has, jj, a, q, cand["size"].to_numpy(float), K, cap, queue, tol, seed=seed,
                         through_full=through_full)
        d = cand.loc[sh > 0, cols].copy()
        d["a"] = a[sh > 0]
        d["sh"] = sh[sh > 0]
    d = d[d.sh > 0].merge(ok_legs[["m", "event_slug", "league", "per", "leg", "mid0"]], on="m")
    ap = d.a - shade
    reb = rebate * d.fee_rate * ap * (1 - ap)
    d["ref_s"] = np.where(d.s == 0, d.mid0, 1 - d.mid0)
    d["mo"] = (ap - (d.y if hold else d.ref_s)) + reb
    d["capital"] = 1 - ap
    d["reb"] = reb
    d["ap"] = ap
    d["through"] = d.q > d.a + tol                # print traded through our price (no queue priority needed)
    return d, info


# ----------------------------------------------------------------------------- statistics

def summarize(d: pd.DataFrame, by="per") -> pd.DataFrame:
    rows = []
    groups = [("dev", d[d.per != "hold"])] + list(d.groupby(by)) if by == "per" else list(d.groupby(by))
    for k, g in groups:
        if len(g) == 0:
            continue
        mo, lo, hi = C.cluster_ci(g.mo * 100, g.event_slug, weights=g.sh)
        roi, rlo, rhi = C.cluster_ci(g.mo / g.capital, g.event_slug, weights=g.sh * g.capital)
        rows.append(dict(grp=k, events=g.event_slug.nunique(), legs=g.m.nunique(), fills=len(g),
                         shares=g.sh.sum(), capital=(g.sh * g.capital).sum(), pnl=(g.sh * g.mo).sum(),
                         mo_c=mo, mo_lo=lo, mo_hi=hi, roi=roi, roi_lo=rlo, roi_hi=rhi,
                         reb_c=100 * (g.sh * g.reb).sum() / g.sh.sum(), avg_a=(g.sh * g.a).sum() / g.sh.sum()))
    return pd.DataFrame(rows)


def fmt(t: pd.DataFrame) -> str:
    if t.empty:
        return "(empty)"
    t = t.copy()
    for c in ("shares", "capital", "pnl"):
        t[c] = t[c].map(lambda x: f"{x:,.0f}")
    for c in ("mo_c", "mo_lo", "mo_hi", "reb_c"):
        t[c] = t[c].map(lambda x: f"{x:+.3f}")
    for c in ("roi", "roi_lo", "roi_hi"):
        t[c] = t[c].map(lambda x: f"{100 * x:+.2f}%")
    t["avg_a"] = t["avg_a"].map(lambda x: f"{x:.3f}")
    return t.to_string(index=False)


def roi_ci(g: pd.DataFrame, cl="event_slug", mo=None) -> tuple[float, float, float]:
    mo = g.mo if mo is None else mo
    if len(g) == 0:
        return np.nan, np.nan, np.nan
    return C.cluster_ci(mo / g.capital, g[cl], weights=g.sh * g.capital)


def mo_ci(g: pd.DataFrame, cl="event_slug") -> tuple[float, float, float]:
    if len(g) == 0:
        return np.nan, np.nan, np.nan
    return C.cluster_ci(g.mo * 100, g[cl], weights=g.sh)


def robustness(d: pd.DataFrame, legs: pd.DataFrame) -> dict:
    """Stats-review block on one simulated fill set (DEV pooled and HOLDOUT): alternative clustering, outlier
    dependence, per-event equal weight, rebate grid, leave-one-league-out, World Cup / US-MX excluded, months,
    and fills where the print traded through our price vs fills exactly at it."""
    d = d.copy()
    ko = d.m.map(legs.set_index("m").game_start_ts)
    d["date"] = pd.to_datetime(ko, unit="s", utc=True).dt.strftime("%Y-%m-%d")
    d["month"] = pd.to_datetime(ko, unit="s", utc=True).dt.strftime("%Y-%m")
    d["league_date"] = d.league.astype(str) + "|" + d.date
    d["pnl"] = d.sh * d.mo
    d["cap_usd"] = d.sh * d.capital
    out = {}
    for p, g in (("dev", d[d.per != "hold"]), ("hold", d[d.per == "hold"])):
        if len(g) == 0:
            continue
        r = {"clustering": {cl: roi_ci(g, cl) for cl in ("event_slug", "date", "league_date", "league")}}
        ev = g.groupby("event_slug")[["pnl", "cap_usd"]].sum().sort_values("pnl")
        n = len(ev)
        k = max(1, int(np.ceil(0.01 * n)))
        tot = ev.pnl.sum()
        flip = 0
        if tot > 0:
            flip = int(np.argmax(tot - ev.pnl[::-1].cumsum().to_numpy() <= 0) + 1)
        r["outliers"] = dict(
            events=n, k_1pct=k, roi_full=tot / ev.cap_usd.sum(),
            roi_drop_top1pct=ev.pnl.iloc[:-k].sum() / ev.cap_usd.iloc[:-k].sum(),
            roi_drop_bottom1pct=ev.pnl.iloc[k:].sum() / ev.cap_usd.iloc[k:].sum(),
            roi_trim1pct_both=ev.pnl.iloc[k:-k].sum() / ev.cap_usd.iloc[k:-k].sum(),
            top1pct_share_of_pnl=(ev.pnl.tail(k).sum() / tot) if tot != 0 else np.nan,
            top_events_to_flip_sign=flip, frac_events_positive=float((ev.pnl > 0).mean()))
        er = ev.pnl / ev.cap_usd
        r["equal_weight_event_roi"] = C.cluster_ci(er.to_numpy(), er.index.to_numpy())
        r["rebate_grid"] = {f"{rb:.2f}": roi_ci(g, mo=g.mo - g.reb + rb * g.fee_rate * g.ap * (1 - g.ap))
                            for rb in REBATE_GRID}
        r["fill_type"] = {"q==a (queue-dependent)": dict(roi=roi_ci(g[~g.through]), mo_c=mo_ci(g[~g.through]),
                                                        share_of_shares=float(g.sh[~g.through].sum() / g.sh.sum())),
                          "q>a (traded through)": dict(roi=roi_ci(g[g.through]), mo_c=mo_ci(g[g.through]),
                                                       share_of_shares=float(g.sh[g.through].sum() / g.sh.sum()))}
        top = g.groupby("league").event_slug.nunique().sort_values(ascending=False).head(12)
        r["leave_one_league_out"] = {f"{lg} ({ne} ev)": roi_ci(g[g.league != lg]) for lg, ne in top.items()}
        wc = g.league.astype(str).str.contains("fifwc|world", case=False)
        usmx = g.league.astype(str).str.lower().str.startswith(US_MX)
        r["excl_world_cup"] = roi_ci(g[~wc])
        r["excl_us_mx (post-hoc)"] = roi_ci(g[~usmx])
        r["months"] = {mo_: dict(events=int(gg.event_slug.nunique()), roi=roi_ci(gg), mo_c=mo_ci(gg))
                       for mo_, gg in g.groupby("month")}
        r["fee_rate_mix_of_shares"] = (g.groupby("fee_rate").sh.sum() / g.sh.sum()).to_dict()
        out[p] = r
    return out


def print_robustness(name: str, r: dict) -> None:
    def f3(x):
        return f"{100 * x[0]:+.2f}% [{100 * x[1]:+.2f}, {100 * x[2]:+.2f}]"
    print(f"\n=== robustness: {name}")
    for p, x in r.items():
        print(f"  [{p}] clustering: " + "; ".join(f"{k} {f3(v)}" for k, v in x["clustering"].items()))
        o = x["outliers"]
        print(f"  [{p}] outliers: ROI full {100 * o['roi_full']:+.3f}%, drop top 1% ({o['k_1pct']} ev) "
              f"{100 * o['roi_drop_top1pct']:+.3f}%, drop bottom 1% {100 * o['roi_drop_bottom1pct']:+.3f}%, trim both "
              f"{100 * o['roi_trim1pct_both']:+.3f}%, top-1% share of P&L {o['top1pct_share_of_pnl']:+.2f}, "
              f"#top events to flip {o['top_events_to_flip_sign']}, events>0 {o['frac_events_positive']:.3f}")
        print(f"  [{p}] equal-weight event ROI {f3(x['equal_weight_event_roi'])}")
        print(f"  [{p}] rebate grid: " + "; ".join(f"{k}: {f3(v)}" for k, v in x["rebate_grid"].items()))
        for k, v in x["fill_type"].items():
            print(f"  [{p}] {k}: share {v['share_of_shares']:.3f} ROI {f3(v['roi'])} "
                  f"mo {v['mo_c'][0]:+.3f}c [{v['mo_c'][1]:+.3f}, {v['mo_c'][2]:+.3f}]")
        print(f"  [{p}] excl World Cup {f3(x['excl_world_cup'])}; excl US/MX (post-hoc) {f3(x['excl_us_mx (post-hoc)'])}")
        print(f"  [{p}] leave-one-league-out: " + "; ".join(f"{k} {100 * v[0]:+.2f}%" for k, v in x["leave_one_league_out"].items()))
        print(f"  [{p}] months: " + "; ".join(f"{k} ({v['events']}) {100 * v['roi'][0]:+.2f}%" for k, v in x["months"].items()))
        print(f"  [{p}] fee_rate mix: {x['fee_rate_mix_of_shares']}")


def protocol_verdict(hold: tuple, dev: tuple) -> str:
    """PROFITABLE: holdout ROI > 0 with CI > 0 and DEV CI lower bound > 0; PROMISING: holdout > 0 but a CI crosses
    0; DEAD: holdout <= 0 (ties count as failures)."""
    if not np.isfinite(hold[0]) or hold[0] <= 0:
        return "DEAD"
    if hold[1] > 0 and dev[1] > 0:
        return "PROFITABLE"
    return "PROMISING"


# ----------------------------------------------------------------------------- (f) basketball / hockey

def bh_legs(holdout: bool) -> pd.DataFrame:
    mk = C.markets()
    L = mk[mk.family.isin(["basketball", "hockey"]) & (mk.pre_usd >= PRE_USD_MIN)].copy()
    L["grp"] = None
    for gname, leagues in BH_GROUPS.items():
        L.loc[L.league.isin(leagues), "grp"] = gname
    L = L[L.grp.notna()]
    L["per"] = period(L.game_start_ts)
    L["leg"] = "ml"
    all_L = L
    if not holdout:
        L = L[L.game_start_ts < T_HOLD]
    return all_L, L[["m", "event_slug", "market_slug", "league", "grp", "game_start_ts", "fee_rate", "per", "leg"]]


def fit_window(f: pd.DataFrame, search=BH_SEARCH) -> tuple[int, pd.DataFrame]:
    """DEV-2025 fit: minute VWAP of the token-0 price per market; realized vol = |change| between
    consecutive traded minutes; score(start) = sum |dp| / sum $ volume over [start, start+BH_LEN)."""
    x = f[(f.rel >= 0)].copy()
    x["p0"] = np.where(x.s == 0, x.q, 1 - x.q)
    x["mi"] = (x.rel // MIN).astype(int)
    x["usd"] = x["size"] * x.q
    x["pw"] = x.p0 * x["size"]
    b = x.groupby(["m", "mi"]).agg(pw=("pw", "sum"), sz=("size", "sum"), usd=("usd", "sum")).reset_index()
    b["vw"] = b.pw / b.sz
    b["dp"] = b.groupby("m").vw.diff().abs().fillna(0.0)
    prof = b.groupby("mi").agg(dp=("dp", "sum"), usd=("usd", "sum"), games=("m", "nunique"))
    prof = prof.reindex(range(0, BH_REL[1] // MIN + 1), fill_value=0)
    scores = {}
    for s0 in range(search[0], search[1] - BH_LEN + 1):
        w = prof.loc[s0:s0 + BH_LEN - 1]
        scores[s0] = w.dp.sum() / max(w.usd.sum(), 1.0)
    best = min(scores, key=scores.get)
    prof["dp_per_$k"] = prof.dp / (prof.usd / 1e3).replace(0, np.nan)
    return best, prof


# ----------------------------------------------------------------------------- queue-depth calibration (review fix)

def _jl(line: str):
    """json.loads that skips a truncated line (a capture stopped mid-write)."""
    try:
        return json.loads(line)
    except ValueError:
        return None


def _period_lookup(sports_files: list[Path]):
    """gameId -> (recv_ms array, period array) from Polymarket's sports feed."""
    rows = []
    for fn in sports_files:
        if fn.exists():
            for line in open(fn):
                r = _jl(line)
                if r is None:
                    continue
                m = r["msg"]
                rows.append((r["recv_ms"], m.get("gameId"), str(m.get("period")), m.get("elapsed")))
    s = pd.DataFrame(rows, columns=["recv_ms", "game_id", "period", "elapsed"]).sort_values("recv_ms")
    return {gid: (g.recv_ms.to_numpy(), g.period.to_numpy()) for gid, g in s.groupby("game_id")}, s


def depth_at_prints(clob_files: list[Path], tok: dict, per_lookup: dict) -> pd.DataFrame:
    """Replay recorded books. For every taker trade (last_trade_price; deduplicated by tx/price/size), the token
    it acquired X at price p and, JOIN_DELAY_MS later, the shares resting at p on X's ask side = the queue a new
    maker joining the last-print level would stand behind (0 if the level was cleared and not refilled).
    tok: token -> dict(game_id, other). Books are unified (a token's asks mirror the other token's bids)."""
    books, pend, out, seen = {}, [], [], set()

    def ask_side(x):
        return books.get(x, {}).get("a", {})

    def flush(now):
        while pend and pend[0][0] <= now:
            _, x, p, sz, t_ms, gid, per = pend.pop(0)
            asks = ask_side(x)
            bids = books.get(x, {}).get("b", {})
            ba = min(asks) if asks else np.nan
            bb = max(bids) if bids else np.nan
            out.append((t_ms, gid, tok[x].get("leg"), x, p, sz, per, asks.get(p, 0.0), ba,
                        asks.get(ba, np.nan) if asks else np.nan, bb, bids.get(bb, np.nan) if bids else np.nan))

    for fn in clob_files:
        with open(fn) as fh:
            for line in fh:
                rec = _jl(line)
                if rec is None:
                    continue
                now = rec["recv_ms"]
                flush(now)
                for m in (rec["msg"] if isinstance(rec["msg"], list) else [rec["msg"]]):
                    if not isinstance(m, dict):
                        continue
                    if "bids" in m and "asks" in m:
                        x = m.get("asset_id")
                        if x in tok:
                            books[x] = {"a": {round(float(v["price"]), 4): float(v["size"]) for v in m["asks"]},
                                        "b": {round(float(v["price"]), 4): float(v["size"]) for v in m["bids"]}}
                    elif "price_changes" in m:
                        for pc in m["price_changes"]:
                            x = pc.get("asset_id")
                            if x not in books:
                                continue
                            sd = "b" if pc["side"] == "BUY" else "a"
                            p, sz = round(float(pc["price"]), 4), float(pc["size"])
                            if sz <= 0:
                                books[x][sd].pop(p, None)
                            else:
                                books[x][sd][p] = sz
                    elif m.get("event_type") == "last_trade_price":
                        x = m.get("asset_id")
                        if x not in tok:
                            continue
                        p, sz = float(m["price"]), float(m["size"])
                        acq, pp = (x, round(p, 4)) if m["side"] == "BUY" else (tok[x]["other"], round(1 - p, 4))
                        k = (m.get("transaction_hash"), acq, pp, sz)
                        if k in seen:
                            continue
                        seen.add(k)
                        gid = tok[x]["game_id"]
                        per = None
                        if gid in per_lookup:
                            tt, pp_ = per_lookup[gid]
                            i = np.searchsorted(tt, now, side="right") - 1
                            per = pp_[i] if i >= 0 else "pre"
                        pend.append((now + JOIN_DELAY_MS, acq, pp, sz, now, gid, per))
        print(f"  {fn}: {len(out):,} prints", flush=True)
    flush(np.inf)
    return pd.DataFrame(out, columns=["recv_ms", "game_id", "leg", "token", "p", "size", "period", "depth_at_p",
                                      "best_ask", "best_ask_sz", "best_bid", "best_bid_sz"])


def touch_flow(clob_files: list[Path], tok: dict, per_lookup: dict, phase_of) -> dict:
    """Order flow at the best ask (within 1 tick) of every token, by game phase: shares added, shares removed
    (decreases), shares traded (last_trade_price acquiring the token at that price); cancels = removed - traded.
    Measures how fast a queue turns over through cancellations, which the queue model ignores (conservative)."""
    books, acc = {}, {}

    def add(ph, k, v):
        acc.setdefault(ph, {"added": 0.0, "removed": 0.0, "traded": 0.0})[k] += v

    def phase(x, now):
        gid = tok[x]["game_id"]
        if gid not in per_lookup:
            return "other"
        tt, pp_ = per_lookup[gid]
        i = np.searchsorted(tt, now, side="right") - 1
        return phase_of(pp_[i] if i >= 0 else "pre")

    for fn in clob_files:
        with open(fn) as fh:
            for line in fh:
                rec = _jl(line)
                if rec is None:
                    continue
                now = rec["recv_ms"]
                for m in (rec["msg"] if isinstance(rec["msg"], list) else [rec["msg"]]):
                    if not isinstance(m, dict):
                        continue
                    if "bids" in m and "asks" in m:
                        x = m.get("asset_id")
                        if x in tok:
                            books[x] = {round(float(v["price"]), 4): float(v["size"]) for v in m["asks"]}
                    elif "price_changes" in m:
                        for pc in m["price_changes"]:
                            x = pc.get("asset_id")
                            if x not in books or pc["side"] != "SELL":
                                continue
                            asks = books[x]
                            p, sz = round(float(pc["price"]), 4), float(pc["size"])
                            best = min(asks) if asks else p
                            old = asks.get(p, 0.0)
                            if p <= best + 0.0100001:
                                d = sz - old
                                add(phase(x, now), "added" if d > 0 else "removed", abs(d))
                            if sz <= 0:
                                asks.pop(p, None)
                            else:
                                asks[p] = sz
                    elif m.get("event_type") == "last_trade_price":
                        x = m.get("asset_id")
                        if x not in tok:
                            continue
                        pr, sz = float(m["price"]), float(m["size"])
                        acq, pp = (x, round(pr, 4)) if m["side"] == "BUY" else (tok[x]["other"], round(1 - pr, 4))
                        asks = books.get(acq, {})
                        if asks and pp <= min(asks) + 0.0100001:
                            add(phase(acq, now), "traded", sz)
    out = {}
    for ph, v in acc.items():
        canc = max(v["removed"] - v["traded"], 0.0)
        out[ph] = dict(v, cancelled=canc, cancel_per_traded=canc / v["traded"] if v["traded"] > 0 else None,
                       added_per_traded=v["added"] / v["traded"] if v["traded"] > 0 else None)
    return out


def _depth_summary(x: pd.DataFrame) -> dict:
    if x.empty:
        return dict(prints=0)
    dq = x.depth_at_p
    return dict(prints=int(len(x)), games=int(x.game_id.nunique()), tokens=int(x.token.nunique()),
                depth_median=float(dq.median()), depth_p25=float(dq.quantile(.25)), depth_p75=float(dq.quantile(.75)),
                depth_mean=float(dq.mean()), share_depth_zero=float((dq <= 0).mean()),
                share_depth_le_250=float((dq <= 250).mean()), share_depth_le_1000=float((dq <= 1000).mean()),
                depth_usd_median=float((dq * x.p).median()),
                touch_ask_sz_median=float(x.best_ask_sz.median()),
                spread_median_c=float(100 * (x.best_ask - x.best_bid).median()),
                taker_shares_per_token=float(x.groupby(["game_id", "token"])["size"].sum().median()),
                taker_print_size_median=float(x["size"].median()))


def live_depth(kind: str) -> dict:
    """Queue-depth calibration from recorded books. kind='mlb': the 2026-09-18/19 MLB capture (breaks = sports-feed
    period 'Mid'/'End'); kind='soccer': today's capture (CACHE/live/<day>, halftime = period 'HT'). Writes the
    per-print rows to CACHE/live_depth_<kind>.parquet and the summary into live_depth.json."""
    if kind == "mlb":
        gm = pd.DataFrame([json.loads(l)["game"] for l in open(C.DATA / "live" / MLB_LIVE_DAYS[0] / "games.jsonl")])
        tok = {}
        for r in gm.itertuples():
            tok[str(r.home_token)] = dict(game_id=r.pm_game_id, other=str(r.away_token), leg=r.slug)
            tok[str(r.away_token)] = dict(game_id=r.pm_game_id, other=str(r.home_token), leg=r.slug)
        per, _ = _period_lookup([C.DATA / "live" / d / "sports.jsonl" for d in MLB_LIVE_DAYS])
        x = depth_at_prints([C.DATA / "live" / d / "clob.jsonl" for d in MLB_LIVE_DAYS], tok, per)
        x["phase"] = np.where(x.period.astype(str).str.match(r"^(Mid|End)"), "break",
                              np.where(x.period.astype(str).str.match(r"^(Top|Bot)"), "live", "other"))
    else:
        days = sorted(p for p in LIVE.iterdir() if p.is_dir())
        meta = [m for d in days for m in json.loads((d / "markets.json").read_text())]
        tok = {}
        for m in meta:
            tok[m["yes"]] = dict(game_id=int(m["game_id"]), other=m["no"], leg=m["market_slug"])
            tok[m["no"]] = dict(game_id=int(m["game_id"]), other=m["yes"], leg=m["market_slug"])
        per, sp = _period_lookup([d / "sports.jsonl" for d in days])
        x = depth_at_prints([d / "clob.jsonl" for d in days], tok, per)
        ph = {"HT": "halftime", "1H": "live", "2H": "live"}
        x["phase"] = x.period.map(ph).fillna("other")
        ev = pd.DataFrame(meta).drop_duplicates("game_id").set_index("game_id")
        x["event_slug"] = x.game_id.map(ev.event_slug)
        x["ko"] = x.game_id.map(ev.ko)
        x["rel_min"] = (x.recv_ms / 1000 - x.ko) / 60
        # universe proxy (pre-committed before any soccer halftime was captured): leg volume at KO = Gamma volume
        # at capture start + captured pregame trade $ >= PRE_USD_MIN
        lm = pd.DataFrame(meta).set_index("market_slug")
        pre = (x.p * x["size"])[x.rel_min < 0].groupby(x.leg[x.rel_min < 0]).sum()
        vol_ko = lm.volume.add(pre, fill_value=0.0)
        x["leg_vol_ko"] = x.leg.map(vol_ko)
        x["universe"] = x.leg_vol_ko >= PRE_USD_MIN
        x["ko_slot"] = pd.to_datetime(x.ko, unit="s", utc=True).dt.strftime("%H:%M")
        # halftime timing per game (sports feed): first/last HT message relative to scheduled KO
        sp = sp[sp.game_id.isin(ev.index)].copy()
        sp["rel_min"] = (sp.recv_ms / 1000 - sp.game_id.map(ev.ko)) / 60
        ht = sp[sp.period == "HT"].groupby("game_id").rel_min.agg(["min", "max"])
        s2 = sp[sp.period == "2H"].groupby("game_id").rel_min.min().rename("2H_start")
        timing = ht.join(s2, how="outer").join(ev[["event_slug", "volume"]] if "volume" in ev else ev[["event_slug"]])
    if kind == "mlb":
        flow = touch_flow([C.DATA / "live" / d / "clob.jsonl" for d in MLB_LIVE_DAYS], tok, per,
                          lambda p: "break" if str(p)[:3] in ("Mid", "End") else ("live" if str(p)[:3] in ("Top", "Bot") else "other"))
    else:
        uni = {t for t, v in tok.items() if x.loc[x.leg == v["leg"], "universe"].any()}
        flow = touch_flow([d / "clob.jsonl" for d in days], {t: v for t, v in tok.items() if t in uni}, per,
                          lambda p: {"HT": "halftime", "1H": "live", "2H": "live"}.get(str(p), "other"))
    CACHE.mkdir(parents=True, exist_ok=True)
    x.to_parquet(CACHE / f"live_depth_{kind}.parquet", index=False)
    res = json.loads(LIVE_DEPTH_JSON.read_text()) if LIVE_DEPTH_JSON.exists() else {}
    res[kind] = {ph_: _depth_summary(g) for ph_, g in x.groupby("phase")}
    res[kind]["touch_flow" + (" (universe-like legs)" if kind == "soccer" else "")] = flow
    if kind == "soccer":
        u = x[x.universe]
        res[kind]["universe_legs"] = {ph_: _depth_summary(g) for ph_, g in u.groupby("phase")}
        res[kind]["leg_vol_at_ko"] = x.groupby("leg").leg_vol_ko.first().to_dict()
    if kind == "soccer":
        res[kind]["halftime_timing_min_from_scheduled_ko"] = timing.reset_index().to_dict("records")
        res[kind]["halftime_by_game"] = {str(k): _depth_summary(g) for k, g in x[x.phase == "halftime"].groupby("event_slug")}
        res[kind]["halftime_by_universe_leg"] = {str(k): _depth_summary(g) for k, g in
                                                 x[(x.phase == "halftime") & x.universe].groupby("leg")}
    LIVE_DEPTH_JSON.write_text(json.dumps(res, indent=1, default=float))
    print(json.dumps(res[kind], indent=1, default=float))
    return res


def book_sim(clob_files: list[Path], tok: dict, cancel_mode: str = "proportional", K=K_DEFAULT, cap=CAP,
             win=PRIMARY["win"], buffer_ms: int = 1000) -> pd.DataFrame:
    """Report-only live check (small sample): our quote rule simulated against the RECORDED order book, with the
    queue position tracked from the book itself. Quote on token X = price of the last print acquiring X that is
    >= 3 s old (<= 120 s); orders only while rel in `win` minutes. On (re)posting we join behind the shares resting
    at that price. A trade at our price eats the queue ahead first; a trade above our price clears the level
    (fills us, <= print size). Level decreases not matched by a trade within `buffer_ms` are cancellations:
    'proportional' removes them from ahead/behind us pro rata, 'behind_first' only from behind us, 'none' ignores
    them (= the tape queue model with Q = measured depth). tok: token -> dict(leg, other, ko)."""
    import heapq
    asks, last, order, tot, fills, seen = {}, {}, {}, {}, [], set()
    timers, buf, credit = [], {}, {}      # timers: (t_ms, kind, token, price, print_ms)

    def rel_ok(x, t_ms):
        r = t_ms / 1000 - tok[x]["ko"]
        return win[0] * MIN <= r <= win[1] * MIN

    def post(x, a, now):
        if tot.get(x, 0.0) >= cap:
            order.pop(x, None)
            return
        order[x] = dict(a=a, qa=asks.get(x, {}).get(a, 0.0), rem=float(K), t=now)

    def decrease(x, p, amt, old, now):
        """A level decrease at (x, p): first matched against trade volume already seen, else buffered."""
        c = credit.get((x, p), 0.0)
        used = min(c, amt)
        credit[(x, p)] = c - used
        if amt - used > 0:
            buf.setdefault((x, p), []).append([now, amt - used, old])

    def flush(now):
        for key in list(buf):
            lst = buf[key]
            while lst and lst[0][0] <= now - buffer_ms:
                t0, amt, old = lst.pop(0)
                o = order.get(key[0])
                if o and abs(o["a"] - key[1]) < PX_TOL and t0 >= o["t"] and old > 0 and cancel_mode != "none":
                    if cancel_mode == "proportional":
                        o["qa"] -= amt * o["qa"] / old
                    else:
                        o["qa"] -= max(0.0, amt - (old - o["qa"]))
                    o["qa"] = max(o["qa"], 0.0)
            if not lst:
                del buf[key]

    for fn in clob_files:
        with open(fn) as fh:
            for line in fh:
                rec = _jl(line)
                if rec is None:
                    continue
                now = rec["recv_ms"]
                while timers and timers[0][0] <= now:
                    tj, kind, x, a, tp = heapq.heappop(timers)
                    if kind == "join":
                        if not rel_ok(x, tj):
                            order.pop(x, None)
                            continue
                        o = order.get(x)
                        if o is None or abs(o["a"] - a) > PX_TOL or o["rem"] <= 0:
                            post(x, a, tj)
                    elif last.get(x, (None,))[0] == tp:      # quote expired (last print > 120 s old)
                        order.pop(x, None)
                flush(now)
                for m in (rec["msg"] if isinstance(rec["msg"], list) else [rec["msg"]]):
                    if not isinstance(m, dict):
                        continue
                    if "bids" in m and "asks" in m:
                        x = m.get("asset_id")
                        if x in tok:
                            new = {round(float(v["price"]), 4): float(v["size"]) for v in m["asks"]}
                            o = order.get(x)
                            if o and x in asks:
                                old = asks[x].get(round(o["a"], 4), 0.0)
                                nw = new.get(round(o["a"], 4), 0.0)
                                if nw < old:
                                    decrease(x, round(o["a"], 4), old - nw, old, now)
                            asks[x] = new
                    elif "price_changes" in m:
                        for pc in m["price_changes"]:
                            x = pc.get("asset_id")
                            if x not in tok or pc["side"] != "SELL":
                                continue
                            a_ = asks.setdefault(x, {})
                            pz, sz = round(float(pc["price"]), 4), float(pc["size"])
                            old = a_.get(pz, 0.0)
                            o = order.get(x)
                            if o and abs(o["a"] - pz) < PX_TOL and sz < old:
                                decrease(x, pz, old - sz, old, now)
                            if sz <= 0:
                                a_.pop(pz, None)
                            else:
                                a_[pz] = sz
                    elif m.get("event_type") == "last_trade_price":
                        x = m.get("asset_id")
                        if x not in tok:
                            continue
                        pr, sz = float(m["price"]), float(m["size"])
                        acq, pp = (x, round(pr, 4)) if m["side"] == "BUY" else (tok[x]["other"], round(1 - pr, 4))
                        k = (m.get("transaction_hash"), acq, pp, sz)
                        if k in seen:
                            continue
                        seen.add(k)
                        # the trade explains buffered decreases at (acq, pp) first; any rest is credit for later ones
                        rest = sz
                        lst = buf.get((acq, pp), [])
                        while lst and rest > 0:
                            u = min(rest, lst[0][1])
                            lst[0][1] -= u
                            rest -= u
                            if lst[0][1] <= 1e-9:
                                lst.pop(0)
                        if rest > 0:
                            credit[(acq, pp)] = credit.get((acq, pp), 0.0) + rest
                        o = order.get(acq)
                        if o and o["rem"] > 0 and rel_ok(acq, now):
                            if pp > o["a"] + PX_TOL:
                                f = min(o["rem"], sz)
                            elif abs(pp - o["a"]) <= PX_TOL:
                                taken = min(sz, o["qa"])
                                o["qa"] -= taken
                                f = min(o["rem"], sz - taken)
                            else:
                                f = 0.0
                            f = min(f, cap - tot.get(acq, 0.0))
                            if f > 0:
                                fills.append((now, acq, tok[acq]["leg"], o["a"], pp, f))
                                o["rem"] -= f
                                tot[acq] = tot.get(acq, 0.0) + f
                        last[acq] = (now, pp)
                        heapq.heappush(timers, (now + JOIN_DELAY_MS, "join", acq, pp, now))
                        heapq.heappush(timers, (now + QUOTE_MAX_AGE * 1000, "exp", acq, pp, now))
    return pd.DataFrame(fills, columns=["recv_ms", "token", "leg", "a", "q", "sh"])


def live_check_soccer() -> dict:
    """Report-only: on the universe-like legs of the soccer capture, compare (i) the tape fill models of simulate()
    run on the live tape and (ii) book_sim() with the queue tracked from the recorded book. Small sample."""
    days = sorted(p for p in LIVE.iterdir() if p.is_dir())
    meta = pd.DataFrame([m for d in days for m in json.loads((d / "markets.json").read_text())])
    x = pd.read_parquet(CACHE / "live_depth_soccer.parquet")
    uni = x.groupby("leg").universe.first()
    meta = meta[meta.market_slug.map(uni).fillna(False).astype(bool)].reset_index(drop=True)
    meta["m"] = np.arange(len(meta))
    tok = {}
    for r in meta.itertuples():
        tok[r.yes] = dict(leg=r.market_slug, other=r.no, ko=r.ko, s=0, m=r.m)
        tok[r.no] = dict(leg=r.market_slug, other=r.yes, ko=r.ko, s=1, m=r.m)
    # live tape in the fills format (s = 0: Yes acquired)
    t = x[x.token.isin(tok)].copy()
    t["m"] = t.token.map(lambda z: tok[z]["m"]).astype(np.int64)
    t["s"] = t.token.map(lambda z: tok[z]["s"]).astype(np.int64)
    t["ts"] = (t.recv_ms // 1000).astype(np.int64)
    t = t.assign(q=t.p.astype(float), y=0.0, fee_rate=0.05)
    t["rel"] = (t.ts - t.m.map(meta.set_index("m").ko)).astype(np.int64)
    t["g"] = t.m * 2 + t.s
    f = t[["m", "ts", "size", "s", "q", "y", "fee_rate", "rel", "g"]].sort_values(["g", "ts"], kind="stable").reset_index(drop=True)
    legs = meta.assign(league=meta.event_slug.str.split("-").str[0], game_start_ts=meta.ko, fee_rate=0.05, per="hold",
                       leg=np.where(meta.market_slug.str.endswith("-draw"), "draw", "team"), y0=np.nan, y1=np.nan)
    q_dist, _, _ = realistic_queue()
    out = {}
    refw = (PRIMARY["ref"][0] * MIN, PRIMARY["ref"][1] * MIN)
    for vname, skip, ref_mode in (("rule filters (skip rule, KO+61 two-sided ref)", PRIMARY["skip"], "window"),
                                  ("all universe-like legs (no skip, as-of ref)", None, "asof")):
        blk = {}
        for name, kw in (("tape: front of queue (pre-registered fill)", {}), ("tape: (b) back-of-queue", dict(boq=True)),
                         ("tape: queue Q=0", dict(queue=0)), ("tape: queue Q ~ measured depth", dict(queue=q_dist)),
                         ("tape: queue Q=inf", dict(queue=np.inf))):
            d, info = simulate(f, legs, PRIMARY["win"], skip, PRIMARY["ref"], ref_mode=ref_mode, **kw)
            blk[name] = dict(legs_used=info["legs_used"], fills=int(len(d)), shares=float(d.sh.sum()),
                             share_traded_through=float(d.sh[d.through].sum() / d.sh.sum()) if len(d) else None,
                             mo_c=float(100 * np.average(d.mo, weights=d.sh)) if len(d) else None)
        mid0 = leg_ref(f, refw) if ref_mode == "window" else leg_ref_asof(f, refw[1])
        sk = skipped_events(f, legs, None if skip is None else (skip[0] * MIN, skip[1] * MIN))
        ok = set(legs.loc[~legs.event_slug.isin(sk) & legs.m.isin(mid0.dropna().index), "market_slug"])
        for mode in ("proportional", "behind_first", "none"):
            b = book_sim([d_ / "clob.jsonl" for d_ in days], tok, cancel_mode=mode)
            b = b[b.leg.isin(ok)].copy()
            if len(b):
                mm = b.leg.map(meta.set_index("market_slug").m)
                ref_s = np.where(b.token.map(lambda z: tok[z]["s"]) == 0, mm.map(mid0), 1 - mm.map(mid0))
                b["mo"] = b.a - ref_s + 0.15 * 0.05 * b.a * (1 - b.a)
            blk[f"book sim: cancels {mode}"] = dict(
                fills=int(len(b)), shares=float(b.sh.sum()) if len(b) else 0.0,
                share_traded_through=float(b.sh[b.q > b.a + PX_TOL].sum() / b.sh.sum()) if len(b) else None,
                mo_c=float(100 * np.average(b.mo, weights=b.sh)) if len(b) else None)
        blk["legs"] = sorted(ok)
        out[vname] = blk
    res = json.loads(LIVE_DEPTH_JSON.read_text())
    res.setdefault("soccer", {})["live_check_fill_models"] = out
    LIVE_DEPTH_JSON.write_text(json.dumps(res, indent=1, default=float))
    print(json.dumps(out, indent=1, default=float))
    return out


def realistic_queue() -> tuple[np.ndarray, float, str]:
    """Pre-committed Q calibration (review fix; fixed at 07:55 UTC 2026-09-19, before any soccer halftime was
    captured): the empirical distribution of shares resting at the last-print level 3 s after each print during
    soccer HALFTIME (sports-feed period 'HT') on universe-like legs (volume at KO >= $25k) in the live capture.
    Kickoff slots are added in time order (11:30/12:00, then 13:00, then 14:00 UTC) until >= MIN_HT_PRINTS halftime
    prints; if the whole capture has fewer, MLB inning breaks. Returns (distribution, median, source)."""
    fs, fm = CACHE / "live_depth_soccer.parquet", CACHE / "live_depth_mlb.parquet"
    if fs.exists():
        x = pd.read_parquet(fs)
        x = x[(x.phase == "halftime") & x.universe]
        for slots in (["11:30", "12:00"], ["11:30", "12:00", "13:00"], None):
            xs = x if slots is None else x[x.ko_slot.isin(slots)]
            if len(xs) >= MIN_HT_PRINTS:
                return xs.depth_at_p.to_numpy(float), float(xs.depth_at_p.median()), \
                    (f"soccer halftime live capture 2026-09-19 ({len(xs)} prints, {xs.leg.nunique()} legs, "
                     f"{xs.game_id.nunique()} games: {', '.join(sorted(xs.event_slug.unique()))})")
    x = pd.read_parquet(fm)
    x = x[x.phase == "break"]
    return x.depth_at_p.to_numpy(float), float(x.depth_at_p.median()), \
        f"MLB inning-break live capture ({len(x)} prints, {x.game_id.nunique()} games)"


# ----------------------------------------------------------------------------- main

def run(holdout: bool):
    t0 = time.time()
    out = {"holdout_evaluated": holdout}
    legs, f = cache_soccer(holdout)
    print(f"soccer legs {len(legs):,} ({legs.per.value_counts().to_dict()}), fills cached {len(f):,}  "
          f"[{time.time() - t0:.0f}s]", flush=True)

    res = {}
    frames = {}

    def run_var(name, keep=False, **kw):
        cfg = dict(PRIMARY)
        cfg.update({k: kw.pop(k) for k in ("win", "skip", "ref") if k in kw})
        d, info = simulate(f, legs, cfg["win"], cfg["skip"], cfg["ref"], **kw)
        s = summarize(d)
        res[name] = dict(info=info, table=s.to_dict("records"))
        print(f"\n=== {name}  {info}\n{fmt(s)}", flush=True)
        if keep:
            frames[name] = d
        return d, s

    d0, s0 = run_var("PRIMARY")
    # ---- sanity checks on the primary fills
    assert (d0.sh <= K_DEFAULT + 1e-9).all() and (d0.groupby("g").sh.sum() <= CAP + 1e-6).all()
    assert (d0.rel >= 52 * MIN).all() and (d0.rel <= 60 * MIN).all()
    assert (d0.q >= d0.a - PX_TOL).all() and d0.a.between(0, 1).all()
    san = {}
    for p, g in d0.groupby("per"):
        # payout orientation: E[y_s - ref_s] should be ~0 (ref is a fair-value estimate of token s);
        # a flipped orientation would give |.| ~ 0.3+
        san[p] = dict(y_minus_ref_c=100 * np.average(g.y - g.ref_s, weights=g.sh),
                      y_minus_ref_c_s0=100 * np.average(g.y[g.s == 0] - g.ref_s[g.s == 0], weights=g.sh[g.s == 0]),
                      y_minus_ref_c_s1=100 * np.average(g.y[g.s == 1] - g.ref_s[g.s == 1], weights=g.sh[g.s == 1]),
                      a_minus_ref_c=100 * np.average(g.a - g.ref_s, weights=g.sh),
                      share_s0=float(g.sh[g.s == 0].sum() / g.sh.sum()),
                      corr_q_ref=float(np.corrcoef(g.q, g.ref_s)[0, 1]),
                      frac_fills_q_gt_a=float((g.q > g.a + PX_TOL).mean()),
                      )
    res["sanity"] = san
    print("\n=== sanity (PRIMARY)\n", pd.DataFrame(san).T.round(4).to_string(), flush=True)

    # the float32 price-tolerance bug (review-round finding): the numbers as originally run used EPS = 1e-9
    run_var("PRIMARY as originally run (px_tol 1e-9)", px_tol=EPS)
    run_var("(b) back-of-queue as originally run (px_tol 1e-9)", boq=True, px_tol=EPS)
    run_var("-1c worse maker price (shade)", shade=0.01)
    run_var("(a) K=200", K=200)
    db, _ = run_var("(b) back-of-queue stress", boq=True)
    run_var("(b) back-of-queue stress, -1c worse price", boq=True, shade=0.01)
    run_var("(c) 25% of each fill", frac=0.25)
    run_var("(d) hold to resolution", hold=True)
    run_var("(d2) hold to resolution, all non-skipped legs (no ref-based drop)", hold=True, require_ref=False)
    run_var("(b)+(d2) back-of-queue, hold, no ref-based drop", boq=True, hold=True, require_ref=False)
    run_var("no skip rule (diagnostic)", skip=None)
    # robustness to implementation conventions (the rule's wording allows both; h_mlb_inning_break_mm uses
    # the "last row in data order" quote tie-break, strictly-later back-of-queue prints, and no K cap in (c))
    run_var("robust: quote tie = last row in data order", tie="last")
    run_var("robust: back-of-queue, strictly later prints only (ts > t)", boq=True, boq_strict=True)
    run_var("robust: back-of-queue strict + last-row tie", boq=True, boq_strict=True, tie="last")
    run_var("robust: (c) 25% of each fill, no K cap", frac=0.25, K=np.inf)
    for name, cfg in CONTROLS.items():
        run_var(f"(e) {name}", **cfg)
        run_var(f"(e) {name} back-of-queue", boq=True, **cfg)

    # ------------------------------------------------------------------ review fixes: realistic execution
    q_dist, q_med, q_src = realistic_queue()
    out["queue_calibration"] = dict(source=q_src, median=q_med, n=int(len(q_dist)),
                                    share_zero=float((q_dist <= 0).mean()), p25=float(np.quantile(q_dist, .25)),
                                    p75=float(np.quantile(q_dist, .75)))
    print(f"\n=== queue calibration: {out['queue_calibration']}", flush=True)
    for Q in Q_GRID:
        run_var(f"queue model, Q={Q:g} shares ahead", queue=Q)
    dq, _ = run_var("REALISTIC queue model, Q ~ measured depth distribution", queue=q_dist)
    for sd in (1, 2, 3, 4):   # the Q draws are random: seed sensitivity of the realistic model
        run_var(f"REALISTIC queue, draw seed {sd}", queue=q_dist, seed=sd)
    run_var(f"queue model, Q = measured median ({q_med:,.0f})", queue=q_med)
    run_var("REALISTIC queue, trade-through fills the whole remaining order", queue=q_dist, through_full=True)
    run_var("queue Q=0, trade-through fills the whole remaining order", queue=0, through_full=True)
    # combos (reviewer checks): tick-grid quotes, same-second prints as one taker event, 5 s quote delay
    run_var("queue Q=0 + tick grid + same-second agg + 5 s delay", queue=0, tick=True, agg_second=True, delay=5)
    run_var("queue Q~measured + tick grid + same-second agg + 5 s delay", queue=q_dist, tick=True, agg_second=True, delay=5)
    for dl in (5, 10, 20):
        run_var(f"primary, quote delay {dl} s", delay=dl)
    run_var("primary, quotes rounded up to the tick grid", tick=True)
    run_var("primary, same-second prints = one taker event", agg_second=True)
    # rebate: the 15% per-fill rebate is an assumption
    run_var("primary, rebate 0", rebate=0.0)
    dbr0, _ = run_var("(b) back-of-queue, rebate 0", boq=True, rebate=0.0)
    dqr0, _ = run_var("REALISTIC queue (Q~measured), rebate 0", queue=q_dist, rebate=0.0)
    # ref without selection on post-window activity
    run_var("primary, as-of ref (no ref-based leg drop)", ref_mode="asof")
    dba, _ = run_var("(b) back-of-queue, as-of ref", boq=True, ref_mode="asof")
    dqa, _ = run_var("REALISTIC queue (Q~measured), as-of ref", queue=q_dist, ref_mode="asof")
    # mechanism check under realistic fills: the in-play control windows with the same queue model
    for name, cfg in CONTROLS.items():
        run_var(f"(e) {name} REALISTIC queue (Q~measured, halftime calibration)", queue=q_dist, **cfg)

    # robustness blocks
    rob = {"PRIMARY (front of queue)": robustness(d0, legs), "(b) back-of-queue": robustness(db, legs),
           "REALISTIC queue (Q~measured)": robustness(dq, legs)}
    for k, v in rob.items():
        print_robustness(k, v)
    res["robustness"] = rob

    # ---- verdict (review fix): gated on realistic execution; ties count as failures
    def hd(d):
        return roi_ci(d[d.per == "hold"]), roi_ci(d[d.per != "hold"])
    V = {}
    for name, d in (("front-of-queue primary (pre-registered fill, NOT realistic)", d0),
                    ("(b) back-of-queue (pre-registered gate)", db),
                    ("queue model Q~measured depth", dq),
                    ("(b) back-of-queue, rebate 0", dbr0), ("queue model Q~measured, rebate 0", dqr0),
                    ("(b) back-of-queue, as-of ref", dba), ("queue model Q~measured, as-of ref", dqa)):
        h, dv = hd(d)
        V[name] = dict(holdout_roi=h, dev_roi=dv, verdict=protocol_verdict(h, dv) if holdout else "n/a (DEV only)")
    real = ["(b) back-of-queue (pre-registered gate)", "queue model Q~measured depth"]
    real0 = ["(b) back-of-queue, rebate 0", "queue model Q~measured, rebate 0"]
    order = ["DEAD", "PROMISING", "PROFITABLE"]
    if holdout:
        V["FINAL (worst of the realistic models, 15% rebate)"] = min((V[k]["verdict"] for k in real), key=order.index)
        V["FINAL with rebate 0"] = min((V[k]["verdict"] for k in real0), key=order.index)
    res["verdict"] = V
    print("\n=== verdict (realistic execution)")
    for k, v in V.items():
        print(f"  {k}: {v}")

    # descriptive splits of the primary rule
    for by in ("leg", "league"):
        tab = [summarize(g, by="per").assign(**{by: k}) for k, g in d0.groupby(by) if g.event_slug.nunique() >= 40]
        if tab:
            tt = pd.concat(tab)
            res[f"PRIMARY by {by}"] = tt.to_dict("records")
            print(f"\n=== PRIMARY by {by} (groups with >= 40 events)\n{fmt(tt[['grp', by] + [c for c in tt.columns if c not in ('grp', by)]])}")
    # capacity: per period, over the calendar span of the KOs of the legs we filled in
    tak = res["PRIMARY"]["info"]["taker_shares_in_window_by_per"]
    for cname, dd in (("capacity", d0), ("capacity (b) back-of-queue", db), ("capacity REALISTIC queue", dq)):
        cap = {}
        for p, g in dd.groupby("per"):
            ko = legs.loc[legs.m.isin(g.m), "game_start_ts"]
            days = max((ko.max() - ko.min()) / 86400, 1)
            cap[p] = dict(days=days, events=g.event_slug.nunique(), capital_per_day=(g.sh * g.capital).sum() / days,
                          pnl_per_day=(g.sh * g.mo).sum() / days, pnl_per_month=30 * (g.sh * g.mo).sum() / days,
                          shares_per_event_median=g.groupby("event_slug").sh.sum().median(),
                          capital_per_event_median=(g.sh * g.capital).groupby(g.event_slug).sum().median(),
                          share_of_window_taker_volume=g.sh.sum() / tak[p])
        res[cname] = cap
        print(f"\n=== {cname}\n", pd.DataFrame(cap).T.round(3).to_string(), flush=True)

    # ------------------------------------------------------------------ (f) basketball / hockey
    all_bh, bh = bh_legs(holdout)
    fn = CACHE / "bh_fills.parquet"
    if not fn.exists():
        load_fills(all_bh, BH_REL, "bh_fills")
    fb = load_fills(bh, BH_REL, "bh_fills")
    res["f"] = {}
    for gname in BH_GROUPS:
        lg = bh[bh.grp == gname]
        fg = fb[fb.m.isin(lg.m)]
        fit_legs = lg[lg.per == "dev25"]
        hold_games = int((lg.per == "hold").sum())
        # WNBA is the only (f) league with holdout games; report its holdout only if >= 50 games
        eval_legs = lg if (gname == "WNBA" and hold_games >= 50) else lg[lg.per != "hold"]
        out_g = dict(fit_games=int(len(fit_legs)), holdout_games=hold_games)
        for fit_name, search in (("fit[30,100]", BH_SEARCH), ("fit[30,170] (unrestricted, diagnostic)", BH_SEARCH_WIDE)):
            best, prof = fit_window(fg[fg.m.isin(fit_legs.m)], search)
            win = (best, best + BH_LEN)
            refw = (best + BH_LEN - 1, best + BH_LEN + 1)
            og = dict(window=win)
            vlist = (("base", {}), ("back-of-queue", dict(boq=True)), ("queue Q~measured", dict(queue=q_dist)),
                     ("hold", dict(hold=True)), ("hold, no ref-based drop", dict(hold=True, require_ref=False)))
            if fit_name.startswith("fit[30,170]"):
                vlist = vlist[:1]
            for vname, kw in vlist:
                d, info = simulate(fg, eval_legs, win, None, refw, **kw)
                s = summarize(d)
                og[vname] = dict(info=info, table=s.to_dict("records"))
                print(f"\n=== (f) {gname} {fit_name}: window KO+[{win[0]},{win[1]}] min "
                      f"(fit on {len(fit_legs)} DEV-2025 games) {vname}  legs_used={info['legs_used']}\n{fmt(s)}", flush=True)
            og["profile"] = prof.loc[max(win[0] - 10, 0):win[1] + 10, ["dp", "usd", "games", "dp_per_$k"]].reset_index().to_dict("records")
            out_g[fit_name] = og
        res["f"][gname] = out_g

    out["results"] = res
    CACHE.mkdir(parents=True, exist_ok=True)
    fn = CACHE / f"results_{'full' if holdout else 'dev'}.json"
    fn.write_text(json.dumps(out, default=lambda o: o.item() if hasattr(o, "item") else str(o), indent=1))
    print(f"\nsaved {fn}  [{time.time() - t0:.0f}s]")


# ----------------------------------------------------------------------------- live halftime book capture (review fix)
# Read-only capture of today's soccer order books + Polymarket's sports feed (period/score), written only to
# CACHE/live/<UTC date>/. Used to measure the queue a new maker joins at halftime (review issue: queue depth).
LIVE_SLUGS = ["kor-bch-san-2026-09-19", "elc-mil-whu-2026-09-19", "epl-tot-ast-2026-09-19", "lal-osa-ray-2026-09-19",
              "sea-bol-tor-2026-09-19", "sea-udi-cag-2026-09-19", "elc-bur-der-2026-09-19", "epl-bri-ars-2026-09-19",
              "epl-eve-ips-2026-09-19", "epl-new-hul-2026-09-19"]
BOOK_LEVELS_KEPT = 25     # levels per side kept from full-book snapshots (nearest the touch)


def record_live(slugs: list[str], hours: float) -> None:
    import asyncio

    import websockets

    from pmsports.http import get_json
    from pmsports.polymarket import parse_ts
    day = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d")
    out = LIVE / day
    out.mkdir(parents=True, exist_ok=True)
    meta = []
    for sl in slugs:
        e = get_json("https://gamma-api.polymarket.com/events", {"slug": sl})[0]
        for m in e["markets"]:
            if m.get("sportsMarketType") != "moneyline":
                continue
            yes, no = json.loads(m["clobTokenIds"])
            meta.append(dict(event_slug=sl, game_id=e.get("gameId"), market_slug=m["slug"],
                             condition_id=m["conditionId"], yes=yes, no=no, ko=parse_ts(m.get("gameStartTime")),
                             volume=float(m.get("volume") or 0)))
    (out / "markets.json").write_text(json.dumps(meta, indent=1))
    tokens = sorted({x["yes"] for x in meta} | {x["no"] for x in meta})
    gids = {int(x["game_id"]) for x in meta if x["game_id"]}
    stop = time.time() + hours * 3600
    print(f"recording {len(meta)} markets / {len(tokens)} tokens / {len(gids)} games until "
          f"{pd.Timestamp(stop, unit='s', tz='UTC')}", flush=True)

    def trim(msg):
        for m in (msg if isinstance(msg, list) else [msg]):
            if isinstance(m, dict) and "bids" in m and "asks" in m:   # best levels are at the END of each list
                m["bids"], m["asks"] = m["bids"][-BOOK_LEVELS_KEPT:], m["asks"][-BOOK_LEVELS_KEPT:]
        return msg

    async def clob():
        fh = open(out / "clob.jsonl", "a", buffering=1)
        while time.time() < stop:
            try:
                async with websockets.connect("wss://ws-subscriptions-clob.polymarket.com/ws/market",
                                              max_size=2**24, ping_interval=None) as ws:
                    await ws.send(json.dumps({"assets_ids": tokens, "type": "market", "custom_feature_enabled": True}))
                    last_ping = time.time()
                    while time.time() < stop:
                        if time.time() - last_ping > 10:
                            await ws.send("PING")
                            last_ping = time.time()
                        try:
                            msg = await asyncio.wait_for(ws.recv(), 5)
                        except asyncio.TimeoutError:
                            continue
                        if msg == "PONG":
                            continue
                        fh.write(json.dumps({"recv_ms": int(time.time() * 1000), "msg": trim(json.loads(msg))},
                                            separators=(",", ":")) + "\n")
            except Exception as exc:   # reconnect
                print("clob ws:", exc, flush=True)
                await asyncio.sleep(3)

    async def sports():
        fh = open(out / "sports.jsonl", "a", buffering=1)
        while time.time() < stop:
            try:
                async with websockets.connect("wss://sports-api.polymarket.com/ws", ping_interval=None) as ws:
                    while time.time() < stop:
                        try:
                            msg = await asyncio.wait_for(ws.recv(), 15)
                        except asyncio.TimeoutError:
                            continue
                        if msg == "ping":
                            await ws.send("pong")
                            continue
                        m = json.loads(msg)
                        if isinstance(m, dict) and m.get("gameId") in gids:
                            fh.write(json.dumps({"recv_ms": int(time.time() * 1000), "msg": m},
                                                separators=(",", ":")) + "\n")
            except Exception as exc:
                print("sports ws:", exc, flush=True)
                await asyncio.sleep(3)

    async def main():
        await asyncio.gather(clob(), sports())

    asyncio.run(main())
    print("capture done", flush=True)


if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--record" in argv:
        record_live(LIVE_SLUGS, float(argv[argv.index("--record") + 1]))
    elif "--live-depth" in argv:
        live_depth(argv[argv.index("--live-depth") + 1])
    elif "--live-check" in argv:
        live_check_soccer()
    else:
        run(holdout="--holdout" in argv)
