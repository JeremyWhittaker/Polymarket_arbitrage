"""Hypothesis `esports_identity_pickoff`: stale thin BO3 handicap vs the liquid game-2 market.

Mechanism: after team X wins map 1 of a best-of-3, "X -1.5 maps" (X wins 2-0) pays exactly when X
wins map 2, i.e. the same event as X winning the game-2 child moneyline. The two trade in separate
books. If the thin handicap book is not re-marked when game 2 swings, buying X -1.5 plus notX in the
game-2 market locks a $1 payoff for less than $1.

Primary rule (pre-registered; parameters frozen before the holdout run):
  SAMPLE    esports series (universe event_slug) with child_moneyline '-game1' and '-game2', no '-game4',
            a series moneyline in C.markets() with pre_usd >= $25k, and a 1.5-map handicap market
            ('-map-handicap-(home|away)-1pt5' / '-game-handicap-(home|away)-1pt5'); series listing a BO5
            marker ('-game5', '-total-games-3pt5', '-total-games-4pt5') are dropped. Random sample of
            N_PER_PERIOD series per period (seeded). DEV = series start < 2026-07-01, HOLDOUT = after.
            Handicap orientation: outcome 0 is the -1.5 side (gamma question "A (-1.5) vs B (+1.5)",
            outcomes [A, B]; checked per market against the gamma description and on 7.9k 1-1 series).
  MAP-1 END T1 = ts of the first game1 fill whose implied price of either outcome is >= 0.99 (q >= .99 or
            q <= .01); that outcome is X. T2 = first game2 fill after T1 with implied price >= 0.99.
            Identity case only: the handicap market whose -1.5 side (outcome 0) is X.
  ASK PROXY ask_hc(t) = price paid by the last fill(s) acquiring 'X -1.5' with ts in [t-20, t]; ask_g2(t) =
            same for game-2 fills acquiring notX. If several fills share that last second, the HIGHEST
            price is used (end of a sweep; conservative).
  SIGNAL    t (a fill time on either needed side) in (T1+5, T2) with ask_hc + ask_g2 <= 0.98.
            At most one attempt per 30 s per series.
  EXECUTION each leg: the first second with fills on the needed side in [t+3, t+60]; price = VWAP of that
            second's fills on that side (conservative vs the first print), available size = their summed
            size; + the market's taker fee (shares*fee_rate*p*(1-p)). Pair size = min(avail_hc, avail_g2,
            100). If only one leg prints, hold that leg alone (size = min(avail, 100)) to resolution.
            A second of fills used by one attempt cannot be used again by a later attempt.
  PAYOFF    actual payouts from C.universe() (0.5 on voids).
  METRIC    $ P&L / $ deployed (fees included), C.cluster_ci by series event_slug.
Variants: (a) thr 0.97 / 0.99  (b) latency +5 s / +10 s (window shifted)  (c) reverse pair (notX +1.5
          + X in game 2)  (d) total-games-2pt5 Under instead of the handicap (45 cached probe series, DEV only:
          no fetch budget left)  (e) completed pairs only (conditions on the future; diagnostic).
          (f) POST-HOC, designed on DEV and frozen before the single holdout run: same signal and parameters,
          but buy the thin handicap leg first and hedge in game 2 only after it fills (walk game-2 prints in
          [ta+3, ta+60] up to the same share count). Tried on DEV while designing it: thresholds 0.97/0.98/0.99,
          reverse pair, a first-print-only hedge; 0.98 kept (= primary).
Extra diagnostics (not variants of the rule): first attempt per series only, uncapped size, +1c.

REVIEW FIXES (round 2; the rule and every parameter above are unchanged, nothing was tuned on the holdout):
  * Feasibility-probe contamination. The probe that motivated the hypothesis (scratchpad es_sample.py /
    es_test.py / ip_test45.py) looked at 45 series from 2026, 21 of them holdout series, with exactly the frozen
    parameters. Those 21 are listed in PROBE_HOLDOUT; the 8 of them that the seeded random draw put into the
    holdout sample are now EXCLUDED from every holdout number (analysis frame = sampled series minus probe
    holdout series). The sample is not re-drawn: dropping a fixed set from a uniformly random ordering leaves a
    uniformly random sample of the remaining frame, and the 2,000-market fetch budget is spent. The probe's
    DEV series stay in (DEV may be used freely).
  * Robustness block (per period, at the actual fee and at +1c, for all attempts and for one bet per game):
    drop the top ceil(1%), 3 and 5 series by P&L, one-sided clustered bootstrap P(ROI <= 0), series-level
    share profitable / equal-weight mean / median, month split, pooled DEV+holdout; the same block for the
    mechanism components (e) completed pairs and (f) sequential.
  * Verdict gate (ROBUST_GATE): on top of the protocol's label, PROMISING additionally requires the holdout
    ROI to stay > 0 at +1c after dropping the top 1% of series, BOTH with all attempts and with one bet per game
    (the protocol's default). Otherwise the rule is reported DEAD.
  * Outputs of this run carry the suffix RUN_TAG ('_r2'); the original run's files are kept unchanged.

Data: tapes fetched from the Data API (pmsports.polymarket.trades, window [start-1h, closed_ts], HOST_RPS=5)
into data/research/h_esports_identity_pickoff/tapes/. game1 is fetched for every sampled series; game2 and
the identity handicap only when X is the -1.5 side of an available handicap (X is known at T1, before any
signal). Remaining budget (total <= 2,000 markets) goes to total-games-2pt5 tapes for variant (d).

Run:  PYTHONPATH=. systemd-run --user --scope -q -p MemoryMax=4G -p MemorySwapMax=0 .venv/bin/python \
        -m pmsports.research.h_esports_identity_pickoff fetch        (sample + fetch, idempotent)
      ... -m pmsports.research.h_esports_identity_pickoff analyze    (DEV only)
      ... -m pmsports.research.h_esports_identity_pickoff analyze --holdout   (one-shot holdout evaluation)
"""
from __future__ import annotations

import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from pmsports.research import common as C

SLUG = "esports_identity_pickoff"
OUT = C.RESEARCH / f"h_{SLUG}"
TAPES = OUT / "tapes"
SCRATCH_TAPES = Path("/tmp/claude-1000/-mnt-data-projects-polymarket-arbitrage/"
                     "a3336124-029d-461c-8aa7-6f1b04b98478/scratchpad/es_tapes")
HOLDOUT_TS = pd.Timestamp("2026-07-01", tz="UTC").timestamp()
N_PER_PERIOD = 350
MAX_MARKETS = 2000
SEED = 20260701
MIN_PRE_USD = 25_000
HC_TL = ("map-handicap-home-1pt5", "map-handicap-away-1pt5", "game-handicap-home-1pt5", "game-handicap-away-1pt5")
KEEP_TL = ("game1", "game2", "game3", "game4", "game5", "total-games-2pt5", "total-games-3pt5",
           "total-games-4pt5") + HC_TL

# frozen primary parameters
THR = 0.98
LAT = 3          # entry window starts at signal + LAT
WIN = 57         # entry window length -> [t+3, t+60]
LOOK = 20        # ask-proxy lookback (s)
SPACING = 30     # min seconds between attempts per series
AFTER_T1 = 5     # signals only for t > T1 + 5
CAP = 100        # max shares per leg

RUN_TAG = "_r2"  # review round 2 outputs (the original run's results/trade tables are kept)
# Holdout series (start >= 2026-07-01) that the pre-registration feasibility probe had already looked at
# (scratchpad es_tapes/meta.parquet: 45 series with start >= 2026-01-01, 21 of them holdout). Excluded from the
# holdout analysis frame. Hard-coded so the exclusion survives the scratchpad being cleared.
PROBE_HOLDOUT = (
    "cs2-3dmax-bbl1-2026-09-10", "cs2-3dmax-fut-2026-07-26", "cs2-9z-mgc-2026-08-16", "cs2-big5-bb3-2026-09-08",
    "cs2-ice-mouz-2026-08-29", "cs2-lg6-ts7-2026-08-13", "cs2-lgc-fut-2026-08-27", "cs2-mouz-9z-2026-08-27",
    "cs2-mouz-fal2-2026-08-31", "cs2-nem-ast10-2026-07-21", "cs2-vit-furia-2026-09-18", "cs2-wc1-mglz-2026-07-26",
    "dota2-flc-lgd-2026-08-12", "lol-bar-ucam1-2026-07-22", "lol-c9-dsg-2026-08-08", "lol-dnf-ns-2026-08-12",
    "lol-flk-ucam1-2026-07-16", "lol-gen-kt-2026-08-19", "lol-th-g2-2026-08-09", "lol-we-lgd-2026-08-22",
    "val-kru1-sen-2026-08-08")
TRIM = 0.01      # robustness: drop the top ceil(1%) of series by P&L
ROBUST_GATE = "holdout ROI > 0 at +1c after dropping the top 1% of series, with all attempts AND one bet per game"


# ----------------------------------------------------------------------------- sample

def _suffix(slug: pd.Series) -> pd.Series:
    return slug.str.replace(r"^.*\d{4}-\d\d-\d\d-?", "", regex=True)


def market_table() -> pd.DataFrame:
    """One row per esports market of the needed kinds: condition_id, event_slug, tl, o0, o1, y0, y1, fee_rate..."""
    u = C.universe(columns=["condition_id", "family", "market_type", "event_slug", "market_slug", "outcome",
                            "outcome_idx", "payout", "game_start_ts", "closed_ts", "fee_rate"])
    u = u[u.family == "esports"].copy()
    u["tl"] = _suffix(u.market_slug)
    u = u[u.tl.isin(KEEP_TL)]
    w = u.pivot_table(index="condition_id", columns="outcome_idx", values=["outcome", "payout"], aggfunc="first")
    w.columns = [f"{'o' if a == 'outcome' else 'y'}{b}" for a, b in w.columns]
    meta = u.drop_duplicates("condition_id").set_index("condition_id")[
        ["event_slug", "market_slug", "tl", "market_type", "game_start_ts", "closed_ts", "fee_rate"]]
    return meta.join(w).reset_index()


def build_sample() -> tuple[pd.DataFrame, pd.DataFrame]:
    mt = market_table()
    mk = C.markets()
    ml = (mk[(mk.family == "esports") & (mk.market_type == "moneyline")]
          .sort_values("pre_usd", ascending=False).drop_duplicates("event_slug"))
    ml = ml[ml.pre_usd >= MIN_PRE_USD][["event_slug", "league", "game_start_ts", "pre_usd", "o0", "o1"]]
    g = mt.groupby("event_slug").tl.agg(set)
    ok = g.map(lambda s: "game1" in s and "game2" in s and "game4" not in s and any(h in s for h in HC_TL))
    ev = pd.Index(g[ok].index).intersection(pd.Index(ml.event_slug))
    ser = ml.set_index("event_slug").loc[ev].rename(columns={"game_start_ts": "start"})
    ser["bo5_marker"] = g.reindex(ser.index).map(lambda s: bool({"game5", "total-games-3pt5", "total-games-4pt5"} & s))
    ser["dup_game_mkts"] = mt[mt.event_slug.isin(ev)].groupby(["event_slug", "tl"]).size().groupby(level=0).max().reindex(ser.index) > 1
    ser["period"] = np.where(ser.start < HOLDOUT_TS, "dev", "holdout")
    print(f"eligible series {len(ser):,}  dev {int((ser.period == 'dev').sum())}  holdout {int((ser.period == 'holdout').sum())}"
          f"  with BO5 markers {int(ser.bo5_marker.sum())}  with duplicate child markets {int(ser.dup_game_mkts.sum())}")
    # BO5 series without a game4 market (total-games-3pt5/4pt5 or game5 listed) break the identity; drop them
    # before sampling (pre-decision market structure).
    ser = ser[~ser.bo5_marker]
    rng = np.random.default_rng(SEED)
    pick = []
    for p in ("dev", "holdout"):
        idx = ser.index[ser.period == p].to_numpy()
        pick.append(rng.permutation(idx)[:N_PER_PERIOD])
    # interleave dev/holdout so a truncated fetch stays balanced
    order = [x for pair in zip(*pick) for x in pair]
    ser = ser.loc[order].reset_index()
    ser["order"] = np.arange(len(ser))
    # Review fix: holdout series already seen by the feasibility probe are flagged here and dropped from the
    # analysis frame (analysis_frame). They are not removed before the draw so that the cached fetch (which ran
    # before the fix, budget allocated in this order) stays reproducible; removing a fixed set from a uniformly
    # random ordering leaves a uniformly random sample of the rest of the frame.
    ser["probe_holdout"] = (ser.period == "holdout") & ser.event_slug.isin(probe_holdout_series())
    mts = mt[mt.event_slug.isin(ser.event_slug)].copy()
    return ser, mts


def probe_holdout_series() -> set[str]:
    """Holdout series the feasibility probe looked at before pre-registration (PROBE_HOLDOUT, cross-checked
    against the probe's own metadata while the scratchpad still exists)."""
    s = set(PROBE_HOLDOUT)
    f = SCRATCH_TAPES / "meta.parquet"
    if f.exists():
        st = pd.read_parquet(f, columns=["event_slug", "game_start_ts"]).groupby("event_slug").game_start_ts.min()
        seen = set(st[st >= HOLDOUT_TS].index)
        assert seen == s, (sorted(seen ^ s))
    return s


def analysis_frame(ser: pd.DataFrame) -> pd.DataFrame:
    """Sampled series minus the probe-contaminated holdout series."""
    ser = ser.copy()
    ser["probe_holdout"] = (ser.period == "holdout") & ser.event_slug.isin(probe_holdout_series())
    return ser[~ser.probe_holdout]


# ----------------------------------------------------------------------------- tapes

def tape_file(cid: str, tape_dir: Path | None = None) -> Path:
    return (tape_dir or TAPES) / f"{cid}.parquet"


def fetch_one(row) -> str:
    from pmsports import polymarket as PM
    fn = tape_file(row.condition_id)
    if fn.exists():
        return "cached"
    lo, hi = int(row.game_start_ts) - 3600, int(row.closed_ts)
    src = SCRATCH_TAPES / f"{row.condition_id}.parquet"
    if src.exists():   # feasibility-probe tape (window start-6h..closed+10m, a superset) -> trim to the rule's window
        d = pd.read_parquet(src).reindex(columns=list(PM.TRADE_FIELDS))
        d = d[(d.timestamp >= lo) & (d.timestamp <= hi)]
        how = "scratch"
    else:
        d = pd.DataFrame(PM.trades(row.condition_id, lo, hi), columns=list(PM.TRADE_FIELDS))
        how = "api"
    d.to_parquet(fn)
    return how


def fetch_many(rows: pd.DataFrame) -> pd.Series:
    import pmsports.http as H
    H.HOST_RPS["data-api.polymarket.com"] = 5.0
    with ThreadPoolExecutor(4) as ex:
        res = list(ex.map(fetch_one, [r for r in rows.itertuples()]))
    return pd.Series(res).value_counts()


def load_tape(cid: str, tape_dir: Path | None = None) -> pd.DataFrame | None:
    fn = tape_file(cid, tape_dir)
    if not fn.exists():
        return None
    d = pd.read_parquet(fn)
    if d.empty or "timestamp" not in d:
        return pd.DataFrame({"ts": np.array([], np.int64), "acq": np.array([], np.int8),
                             "q": np.array([], float), "size": np.array([], float)})
    k = d.outcomeIndex.astype(int).to_numpy()
    buy = (d.side == "BUY").to_numpy()
    px = d.price.astype(float).to_numpy()
    t = pd.DataFrame({"ts": d.timestamp.astype(np.int64).to_numpy(), "acq": np.where(buy, k, 1 - k).astype(np.int8),
                      "q": np.where(buy, px, 1 - px), "size": d["size"].astype(float).to_numpy()})
    t = t[t["size"] > 0]
    return t.sort_values("ts", kind="stable").reset_index(drop=True)


def map1(g1: pd.DataFrame, o: tuple[str, str]):
    """T1 and X from the game1 tape: first fill with implied price of either outcome >= 0.99."""
    p0 = np.where(g1.acq.to_numpy() == 0, g1.q.to_numpy(), 1 - g1.q.to_numpy())
    hit = np.flatnonzero((p0 >= 0.99) | (p0 <= 0.01))
    if not len(hit):
        return None, None
    i = hit[0]
    return int(g1.ts.iloc[i]), (o[0] if p0[i] >= 0.99 else o[1])


def gamma_orientation(slugs: list[str]) -> pd.DataFrame:
    """Per handicap market: gamma question/description; ok = description resolves to outcome 0 on a 2+ map win
    and the question lists '(-1.5)' first."""
    from pmsports.http import get_json

    def one(s):
        try:
            j = get_json("https://gamma-api.polymarket.com/markets", {"slug": s, "closed": "true"}) or \
                get_json("https://gamma-api.polymarket.com/markets", {"slug": s})
        except Exception as e:     # noqa: BLE001
            return dict(market_slug=s, question=None, desc=None, outcomes=None, err=str(e)[:80])
        if not j:
            return dict(market_slug=s, question=None, desc=None, outcomes=None, err="empty")
        m = j[0]
        return dict(market_slug=s, question=m.get("question"), desc=(m.get("description") or "")[:600],
                    outcomes=m.get("outcomes"), err=None)
    with ThreadPoolExecutor(4) as ex:
        return pd.DataFrame(list(ex.map(one, slugs)))


def fetch() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    TAPES.mkdir(parents=True, exist_ok=True)
    ser, mts = build_sample()
    ser.to_parquet(OUT / "series.parquet")
    mts.to_parquet(OUT / "markets.parquet")
    budget = MAX_MARKETS
    # 1) game1 for every sampled series
    g1 = mts[mts.tl == "game1"].drop_duplicates("event_slug")
    print("fetch game1", len(g1), fetch_many(g1).to_dict()); budget -= len(g1)
    # 2) X from game1 (information available at T1) -> game2 + identity handicap
    idt = identity_rows(g1, mts).merge(ser[["event_slug", "order"]], on="event_slug").sort_values("order")
    # budget: game2 + handicap for the first identity series in (random, dev/holdout-interleaved) sample order
    n_fit = budget // 2
    idt["fetched"] = False
    fit = idt.index[idt.hc_cid.notna()][:n_fit]
    idt.loc[fit, "fetched"] = True
    idt.to_parquet(OUT / "identity.parquet")
    need = idt[idt.fetched]
    print(f"series with T1 {int(idt.T1.notna().sum())}, identity handicap {int(idt.hc_cid.notna().sum())}, "
          f"within the 2,000-market budget {len(need)}")
    g2 = mts[(mts.tl == "game2") & mts.event_slug.isin(need.event_slug)].drop_duplicates("event_slug")
    hc = mts[mts.condition_id.isin(need.hc_cid)]
    assert len(g2) + len(hc) <= budget
    print("fetch game2", len(g2), fetch_many(g2).to_dict()); budget -= len(g2)
    print("fetch handicap", len(hc), fetch_many(hc).to_dict()); budget -= len(hc)
    # 3) total-games-2pt5 for variant (d) only with budget left (otherwise: the 45 cached feasibility series, DEV only)
    tot = mts[(mts.tl == "total-games-2pt5") & mts.event_slug.isin(need.event_slug)].drop_duplicates("event_slug")
    tot = tot.merge(ser[["event_slug", "order"]], on="event_slug").sort_values("order").head(max(budget, 0))
    if len(tot):
        print("fetch totals", len(tot), fetch_many(tot).to_dict()); budget -= len(tot)
    print("markets fetched/used:", MAX_MARKETS - budget)
    gamma()


def gamma() -> None:
    """Handicap orientation check against gamma (question / description / outcome order) for traded handicaps."""
    mts = pd.read_parquet(OUT / "markets.parquet")
    idt = pd.read_parquet(OUT / "identity.parquet")
    hc = mts[mts.condition_id.isin(idt[idt.fetched].hc_cid)]
    go = gamma_orientation(hc.market_slug.tolist())
    go.to_parquet(OUT / "gamma_hc.parquet")
    print("gamma rows", len(go), "errors", go.err.notna().sum())


# ----------------------------------------------------------------------------- backtest

class Leg:
    """Fills on one side of one market as arrays (ts sorted)."""
    def __init__(self, tape: pd.DataFrame, side: int, y: float, rate: float, name: str):
        d = tape[tape.acq == side]
        self.ts = d.ts.to_numpy(np.int64)
        self.q = d.q.to_numpy(float)
        self.sz = d["size"].to_numpy(float)
        self.y, self.rate, self.name = float(y), float(np.nan_to_num(rate)), name
        self.used: set[int] = set()

    def ask(self, t: int) -> float | None:
        j = np.searchsorted(self.ts, t, "right") - 1
        if j < 0 or self.ts[j] < t - LOOK:
            return None
        k = np.searchsorted(self.ts, self.ts[j], "left")
        return float(self.q[k:j + 1].max())

    def fill(self, a: int, b: int):
        """First unused second with fills in [a, b] -> (ts, vwap, size)."""
        i = np.searchsorted(self.ts, a, "left")
        e = np.searchsorted(self.ts, b, "right")
        while i < e:
            s = int(self.ts[i])
            k = np.searchsorted(self.ts, s, "right")
            if s not in self.used:
                sz = self.sz[i:k]
                return s, float((self.q[i:k] * sz).sum() / sz.sum()), float(sz.sum())
            i = k
        return None

    def fill_qty(self, a: int, b: int, qty: float):
        """Walk unused seconds with fills in [a, b] until `qty` shares are bought -> (first ts, vwap, shares)."""
        i = np.searchsorted(self.ts, a, "left")
        e = np.searchsorted(self.ts, b, "right")
        got, cost, first = 0.0, 0.0, None
        while i < e and got < qty - 1e-9:
            s = int(self.ts[i])
            k = np.searchsorted(self.ts, s, "right")
            if s not in self.used:
                sz = self.sz[i:k]
                take = min(float(sz.sum()), qty - got)
                cost += take * float((self.q[i:k] * sz).sum() / sz.sum())
                got += take
                first = s if first is None else first
                self.used.add(s)
            i = k
        return (first, cost / got, got) if got > 0 else None


def run_series(A: Leg, B: Leg, T1: int, T2: float, thr=THR, lat=LAT, seq=False, cap=CAP) -> list[dict]:
    """seq=False: the pre-registered simultaneous entry (both legs from the first print in [t+lat, t+lat+57]).
    seq=True (variant f, post-hoc, designed on DEV): buy the thin leg A first (first print in [t+lat, t+lat+57],
    min(print, cap) shares); only if it prints, hedge the same number of shares in B by walking B's prints in
    [ta+3, ta+60] (liquid game-2 book); any unhedged remainder is held; no trade if A does not print."""
    A.used.clear(); B.used.clear()
    cand = np.unique(np.concatenate([A.ts, B.ts]))
    cand = cand[(cand > T1 + AFTER_T1) & (cand < T2)]
    out, nxt = [], -np.inf
    for t in cand:
        if t < nxt:
            continue
        a, b = A.ask(int(t)), B.ask(int(t))
        if a is None or b is None or a + b > thr + 1e-9:
            continue
        nxt = t + SPACING
        fa = A.fill(int(t) + lat, int(t) + lat + WIN)
        if seq:
            fb = B.fill_qty(fa[0] + 3, fa[0] + 60, min(fa[2], cap)) if fa else None
        else:
            fb = B.fill(int(t) + lat, int(t) + lat + WIN)
        rec = dict(t=int(t), dt_T1=int(t - T1), sig=a + b, ask_a=a, ask_b=b,
                   ta=np.nan, pa=np.nan, avail_a=0.0, tb=np.nan, pb=np.nan, avail_b=0.0,
                   ya=A.y, yb=B.y, ra=A.rate, rb=B.rate, after_T2=False, seq=seq)
        if fa:
            rec.update(ta=fa[0], pa=fa[1], avail_a=fa[2]); A.used.add(fa[0])
        if fb:
            rec.update(tb=fb[0], pb=fb[1], avail_b=fb[2]); B.used.add(fb[0])
        if seq and fa:
            rec["avail_a"] = min(fa[2], cap)
        rec["after_T2"] = bool((fa and fa[0] >= T2) or (fb and fb[0] >= T2))
        rec["kind"] = "pair" if (fa and fb) else ("leg_a" if fa else ("leg_b" if fb else "none"))
        out.append(rec)
    return out


def pnl(tr: pd.DataFrame, slip=0.0, cap=CAP, uncapped=False) -> pd.DataFrame:
    """Adds size, cost, payoff, pnl columns for the trades table."""
    t = tr.copy()
    ca = np.minimum(t.avail_a, np.inf if uncapped else cap)
    cb = np.minimum(t.avail_b, np.inf if uncapped else cap)
    pair = t.kind == "pair"
    size_pair = np.minimum(ca, cb)
    seq = t["seq"].astype(bool) if "seq" in t else pd.Series(False, index=t.index)
    # simultaneous entry: pair size = min of the two prints; sequential: all of leg A, hedged as far as B's print allows
    t["na"] = np.where(pair, np.where(seq, ca, size_pair), np.where(t.kind == "leg_a", ca, 0.0))
    t["nb"] = np.where(pair, size_pair, np.where(t.kind == "leg_b", cb, 0.0))
    pa = np.clip(t.pa.fillna(0) + slip, 0.001, 0.999)
    pb = np.clip(t.pb.fillna(0) + slip, 0.001, 0.999)
    fa = C.taker_fee(t.na, pa, t.ra)
    fb = C.taker_fee(t.nb, pb, t.rb)
    t["cost"] = t.na * pa + fa + t.nb * pb + fb
    t["fees"] = fa + fb
    t["payoff"] = t.na * t.ya + t.nb * t.yb
    t["pnl"] = t.payoff - t.cost
    return t[t.cost > 0]


def build_trades(ser, mts, idt, pair="hc", thr=THR, lat=LAT, tape_dir=None, seq=False, cap=CAP) -> pd.DataFrame:
    """pair: 'hc' (X -1.5 + notX g2), 'rev' (notX +1.5 + X g2), 'tot' (Under 2.5 + notX g2)."""
    by = mts.set_index("condition_id")
    rows = []
    tapes: dict[str, pd.DataFrame | None] = {}

    def tape(cid):
        if cid not in tapes:
            tapes[cid] = load_tape(cid, tape_dir)
        return tapes[cid]
    use = idt[idt.T1.notna()] if pair == "tot" else idt[idt.hc_cid.notna()]
    if "fetched" in use and pair != "tot":
        use = use[use.fetched]
    for r in use.itertuples():
        g2m = mts[(mts.event_slug == r.event_slug) & (mts.tl == "game2")].iloc[0]
        g2 = tape(g2m.condition_id)
        if g2 is None:
            continue
        o2 = (g2m.o0, g2m.o1)
        if r.X not in o2:
            continue
        xi = o2.index(r.X)
        p2 = np.where(g2.acq.to_numpy() == xi, g2.q.to_numpy(), 1 - g2.q.to_numpy())
        after = g2.ts.to_numpy() > r.T1
        h = np.flatnonzero(after & ((p2 >= 0.99) | (p2 <= 0.01)))
        T2 = float(g2.ts.iloc[h[0]]) if len(h) else np.inf
        yg = (g2m.y0, g2m.y1)
        if pair == "tot":
            tm = mts[(mts.event_slug == r.event_slug) & (mts.tl == "total-games-2pt5")]
            if tm.empty or tape(tm.condition_id.iloc[0]) is None:
                continue
            tm = tm.iloc[0]
            if "Under" not in (tm.o0, tm.o1):
                continue
            ui = (tm.o0, tm.o1).index("Under")
            A = Leg(tape(tm.condition_id), ui, (tm.y0, tm.y1)[ui], tm.fee_rate, "under")
            B = Leg(g2, 1 - xi, yg[1 - xi], g2m.fee_rate, "g2_notX")
        else:
            hm = by.loc[r.hc_cid]
            hct = tape(r.hc_cid)
            if hct is None:
                continue
            if pair == "hc":
                A = Leg(hct, 0, hm.y0, hm.fee_rate, "hc_X-1.5")
                B = Leg(g2, 1 - xi, yg[1 - xi], g2m.fee_rate, "g2_notX")
            else:
                A = Leg(hct, 1, hm.y1, hm.fee_rate, "hc_notX+1.5")
                B = Leg(g2, xi, yg[xi], g2m.fee_rate, "g2_X")
        for rec in run_series(A, B, int(r.T1), T2, thr=thr, lat=lat, seq=seq, cap=cap):
            rec.update(event_slug=r.event_slug, T2=T2)
            rows.append(rec)
    tr = pd.DataFrame(rows)
    if tr.empty:
        return tr
    s = ser.set_index("event_slug")
    tr["period"] = s.period.reindex(tr.event_slug).to_numpy()
    tr["start"] = s.start.reindex(tr.event_slug).to_numpy()
    tr["sport"] = tr.event_slug.str.split("-").str[0]
    return tr


def p_le0(t: pd.DataFrame, n_boot=20000, seed=1) -> float:
    """One-sided clustered bootstrap P(ROI <= 0) (resampling whole series), cost-weighted like cluster_ci."""
    if t.empty:
        return float("nan")
    g = t.groupby("event_slug")[["pnl", "cost"]].sum()
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(g), (n_boot, len(g)))
    b = g.pnl.to_numpy()[idx].sum(1) / g.cost.to_numpy()[idx].sum(1)
    return float((b <= 0).mean())


def summarize(t: pd.DataFrame, label: str, p0=False) -> dict:
    if t.empty:
        return dict(desc=label, bets=0, roi=float("nan"), ci_lo=float("nan"), ci_hi=float("nan"), dollars=0.0,
                    pnl=0.0, series=0)
    roi, lo, hi = C.cluster_ci(t.pnl / t.cost, t.event_slug, weights=t.cost)
    d = dict(desc=label, bets=int(len(t)), series=int(t.event_slug.nunique()), dollars=float(t.cost.sum()),
             pnl=float(t.pnl.sum()), roi=roi, ci_lo=lo, ci_hi=hi,
             pairs=int((t.kind == "pair").sum()), legged=int((t.kind != "pair").sum()))
    if p0:
        d["p_le0"] = p_le0(t)
    return d


def fmt(d: dict) -> str:
    s = (f"{d['desc']:<48} bets {d['bets']:>5} series {d.get('series', 0):>4} $dep {d['dollars']:>9.0f} "
         f"P&L {d['pnl']:>8.0f} ROI {d['roi']*100:>6.2f}% CI [{d['ci_lo']*100:>6.2f}, {d['ci_hi']*100:>6.2f}]")
    return s + (f"  P(ROI<=0) {d['p_le0']:.3f}" if "p_le0" in d else "")


def first_per_series(P: pd.DataFrame) -> pd.DataFrame:
    """One bet per game: the first executed attempt of each series."""
    return P.sort_values("t", kind="stable").drop_duplicates("event_slug")


def robustness(tr: pd.DataFrame, label: str) -> list[dict]:
    """Trim / one-bet-per-game / +1c robustness for one trade table (one period).
    For each slip in (0, +1c) and each frame (all attempts, one bet per game): the full frame and the frame
    without the top k series by P&L (k = ceil(TRIM * n_series), 3, 5), each with P(ROI <= 0)."""
    rows = []
    for slip in (0.0, 0.01):
        P = pnl(tr, slip=slip)
        for frame, X in (("all", P), ("one/game", first_per_series(P))):
            if X.empty:
                continue
            ev = X.groupby("event_slug").pnl.sum().sort_values(ascending=False)
            k1 = int(np.ceil(TRIM * len(ev)))
            for k in sorted({0, k1, 3, 5}):
                d = summarize(X[~X.event_slug.isin(ev.index[:k])],
                              f"{label} {frame} {'+1c' if slip else 'fee'} drop top {k}", p0=True)
                d.update(slip=slip, frame=frame, drop_top=k, trim_1pct=bool(k == k1))
                rows.append(d)
    return rows


def series_stats(P: pd.DataFrame) -> dict:
    """Series-level view: share of series with P&L > 0, equal-weight mean and median series ROI."""
    g = P.groupby("event_slug")[["pnl", "cost"]].sum()
    r = g.pnl / g.cost
    return dict(series=int(len(g)), share_profitable=float((g.pnl > 0).mean()), eq_mean_roi=float(r.mean()),
                median_roi=float(r.median()), top1_series_pnl=float(g.pnl.max()),
                top_1pct_pnl_share=float(g.pnl.nlargest(int(np.ceil(TRIM * len(g)))).sum() / g.pnl.sum())
                if g.pnl.sum() else float("nan"))


def eligible_counts() -> dict:
    """Eligible (pre-sampling) series per period, for scaling sample capacity to the population."""
    fn = OUT / "eligible.json"
    if not fn.exists():
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            mt = market_table()
        mk = C.markets()
        ml = mk[(mk.family == "esports") & (mk.pre_usd >= MIN_PRE_USD)].groupby("event_slug").game_start_ts.min()
        g = mt.groupby("event_slug").tl.agg(set)
        ok = g.map(lambda s: "game1" in s and "game2" in s and "game4" not in s and any(h in s for h in HC_TL)
                   and not ({"game5", "total-games-3pt5", "total-games-4pt5"} & s))
        st = ml.reindex(g[ok].index).dropna()
        fn.write_text(json.dumps({"dev": int((st < HOLDOUT_TS).sum()), "holdout": int((st >= HOLDOUT_TS).sum())}))
    return json.loads(fn.read_text())


def identity_rows(g1: pd.DataFrame, mts: pd.DataFrame, tape_dir=None) -> pd.DataFrame:
    rows = []
    for r in g1.itertuples():
        t = load_tape(r.condition_id, tape_dir)
        T1, X = map1(t, (r.o0, r.o1)) if t is not None and len(t) else (None, None)
        hcs = mts[(mts.event_slug == r.event_slug) & mts.tl.isin(HC_TL) & (mts.o0 == X)]
        rows.append(dict(event_slug=r.event_slug, T1=T1, X=X, hc_cid=hcs.condition_id.iloc[0] if len(hcs) else None))
    return pd.DataFrame(rows)


def cached45():
    """The 45 series of the feasibility probe (scratchpad es_tapes: game1, game2, handicaps, total-games-2pt5).
    Used only for variant (d) (DEV only). NB: that probe selected series on TOTAL child-market volume >= $20k."""
    meta = pd.read_parquet(SCRATCH_TAPES / "meta.parquet")
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):
        mt = market_table()
    m45 = mt[mt.event_slug.isin(meta.event_slug.unique())]
    st = m45.groupby("event_slug").game_start_ts.min()
    s45 = pd.DataFrame({"event_slug": st.index, "start": st.to_numpy()})
    s45["period"] = np.where(s45.start < HOLDOUT_TS, "dev", "holdout")
    i45 = identity_rows(m45[m45.tl == "game1"].drop_duplicates("event_slug"), m45, SCRATCH_TAPES)
    return s45, m45, i45


def trades(ser, mts, idt, periods, pair="hc", thr=THR, lat=LAT, tape_dir=None, seq=False, cap=CAP) -> pd.DataFrame:
    tr = build_trades(ser, mts, idt, pair, thr=thr, lat=lat, tape_dir=tape_dir, seq=seq, cap=cap)
    if tr.empty:
        return tr
    tr = tr[tr.period.isin(periods)]
    # protocol: a DEV trade must have ts < 2026-07-01, a HOLDOUT trade ts >= 2026-07-01
    return tr[((tr.period == "dev") & (tr.t < HOLDOUT_TS)) | ((tr.period == "holdout") & (tr.t >= HOLDOUT_TS))]


def analyze(holdout: bool) -> None:
    ser_all = pd.read_parquet(OUT / "series.parquet")
    mts = pd.read_parquet(OUT / "markets.parquet")
    idt_all = pd.read_parquet(OUT / "identity.parquet")
    periods = ["dev", "holdout"] if holdout else ["dev"]
    # review fix: drop the holdout series the feasibility probe had already seen
    ser = analysis_frame(ser_all)
    ser_p = ser[ser.period.isin(periods)]
    idt = idt_all[idt_all.event_slug.isin(ser_p.event_slug)]
    probe = probe_holdout_series()
    res: dict = {"periods": periods, "robust_gate": ROBUST_GATE,
                 "probe_excluded": dict(probe_holdout_series=len(probe),
                                        in_holdout_sample=int(ser_all.event_slug.isin(probe).sum()),
                                        fetched=int(idt_all[idt_all.event_slug.isin(probe)].fetched.sum()),
                                        slugs=sorted(set(ser_all.event_slug) & probe))}
    print("probe-contaminated holdout series excluded:", res["probe_excluded"])

    # --- sanity: X correctness and handicap orientation
    g1 = mts[mts.tl == "game1"].drop_duplicates("event_slug").set_index("event_slug")
    j = idt[idt.X.notna()].join(g1[["o0", "o1", "y0", "y1"]], on="event_slug")
    xw = np.where(j.X == j.o0, j.y0, j.y1)
    print(f"series sampled {len(ser_p)}  T1 found {int(idt.T1.notna().sum())}  X == game1 winner {np.mean(xw == 1):.4f}"
          f"  identity handicap {int(idt.hc_cid.notna().sum())}  within budget (traded) {int(idt.fetched.sum())}")
    res["sanity"] = dict(series=int(len(ser_p)), t1_found=int(idt.T1.notna().sum()), x_correct=float(np.mean(xw == 1)),
                         x_wrong=int((xw != 1).sum()), identity=int(idt.hc_cid.notna().sum()),
                         traded_series=int(idt.fetched.sum()))
    for p in periods:
        e = ser_p.event_slug[ser_p.period == p]
        res["sanity"][f"traded_series_{p}"] = int(idt[idt.event_slug.isin(e)].fetched.sum())
    go_f = OUT / "gamma_hc.parquet"
    if go_f.exists():
        go = pd.read_parquet(go_f).merge(mts[["market_slug", "o0", "o1"]], on="market_slug", how="left")
        go = go[go.market_slug.isin(mts[mts.condition_id.isin(idt.hc_cid)].market_slug)]
        q_ok = go.question.fillna("").str.contains(r"\(-1\.5\)", regex=True)
        d_ok = [bool(re.search(r'resolve to "' + re.escape(str(o)) + r'" if ' + re.escape(str(o)) + r" wins 2 or more",
                               str(d))) for o, d in zip(go.o0, go.desc)]
        out0 = [isinstance(x, str) and x.startswith("[") and json.loads(x)[0] == o for x, o in zip(go.outcomes, go.o0)]
        print(f"gamma handicap checks: n {len(go)}  question has a '(-1.5)' team {q_ok.mean():.3f}  "
              f"description resolves to o0 on 2+ wins {np.mean(d_ok):.3f}  gamma outcomes[0]==o0 {np.mean(out0):.3f}")
        res["sanity"].update(gamma_n=int(len(go)), gamma_q_ok=float(q_ok.mean()), gamma_desc_ok=float(np.mean(d_ok)),
                             gamma_out0_ok=float(np.mean(out0)))

    # --- primary
    tr = trades(ser, mts, idt, periods)
    tr.to_parquet(OUT / f"trades_primary_{'all' if holdout else 'dev'}{RUN_TAG}.parquet")
    print(f"\nattempts {len(tr)}  kinds {tr.kind.value_counts().to_dict()}")
    P = pnl(tr)
    elig = eligible_counts()
    for p in periods:
        x = P[P.period == p]
        print(f"\n== {p} ==")
        s = summarize(x, f"PRIMARY {p}", p0=True); res[f"primary_{p}"] = s; print(fmt(s))
        s1 = summarize(pnl(tr[tr.period == p], slip=0.01), f"PRIMARY {p} +1c", p0=True)
        res[f"primary_{p}_1c"] = s1; print(fmt(s1))
        pr = x[x.kind == "pair"]
        if len(pr):  # completed pairs should pay exactly 1 per share when X is right and nothing is void
            per = (pr.payoff / pr.na).round(3).value_counts().to_dict()
            d = dict(pairs=int(len(pr)), payoff_per_share=str(per), mean_signal_sum=float(pr.sig.mean()),
                     mean_exec_sum=float((pr.pa + pr.pb).mean()), exec_after_T2=float(pr.after_T2.mean()),
                     fees_per_dollar=float(pr.fees.sum() / pr.cost.sum()), mean_size=float(pr.na.mean()),
                     share_exec_sum_below_1=float(((pr.pa + pr.pb) < 1).mean()))
            res[f"pairs_{p}"] = d
            print("   pair diagnostics", d)
        for k in ("pair", "leg_a", "leg_b"):
            s = summarize(x[x.kind == k], f"   {p} {k}"); print(fmt(s)); res[f"primary_{p}_{k}"] = s
        s = summarize(x.sort_values("t").drop_duplicates("event_slug"), f"   {p} first attempt per series only")
        print(fmt(s)); res[f"primary_{p}_first"] = s
        s = summarize(pnl(tr[tr.period == p], uncapped=True), f"   {p} uncapped size (min of the two prints)")
        print(fmt(s)); res[f"primary_{p}_uncapped"] = s
        for fr_, xx in x.groupby(x.rb.round(3)):
            s = summarize(xx, f"   {p} fee_rate {fr_}"); print(fmt(s)); res[f"primary_{p}_fee_{fr_}"] = s
        for sp, xx in x.groupby("sport"):
            s = summarize(xx, f"   {p} sport {sp}"); print(fmt(s)); res[f"primary_{p}_sport_{sp}"] = s
        if len(x):  # capacity: $ deployed at these prices, sample and scaled to all eligible series
            n_s = int((ser.period == p).sum())
            scale = elig[p] / n_s
            days = (x.t.max() - x.t.min()) / 86400
            months = pd.to_datetime(x.t, unit="s").dt.strftime("%Y-%m")
            cap = dict(sample_series=n_s, eligible_series=elig[p], scale=scale, dollars=float(x.cost.sum()),
                       pnl=float(x.pnl.sum()), days=float(days),
                       dollars_per_day_scaled=float(x.cost.sum() * scale / max(days, 1)),
                       pnl_per_day_scaled=float(x.pnl.sum() * scale / max(days, 1)),
                       by_month_dollars={k: round(v, 0) for k, v in x.groupby(months).cost.sum().items()},
                       by_month_pnl={k: round(v, 0) for k, v in x.groupby(months).pnl.sum().items()})
            res[f"capacity_{p}"] = cap
            print("   capacity", cap)
        # --- review fix: robustness of the primary (trim top series, one bet per game, +1c, P(ROI<=0))
        print(f"   -- robustness {p} --")
        rob = robustness(tr[tr.period == p], f"   {p}")
        for d in rob:
            print(fmt(d))
        res[f"robust_{p}"] = rob
        res[f"series_stats_{p}"] = series_stats(x); print("   series-level", res[f"series_stats_{p}"])
        months = pd.to_datetime(x.t, unit="s").dt.strftime("%Y-%m")
        res[f"primary_{p}_months"] = [summarize(xx, f"   {p} month {m}") for m, xx in x.groupby(months)]
        for d in res[f"primary_{p}_months"]:
            print(fmt(d))
        lb = x[x.kind == "leg_b"]
        if len(lb):   # the naked game-2 leg: share-weighted price paid and win rate of notX
            res[f"leg_b_{p}"] = dict(n=int(len(lb)), mean_price=float(np.average(lb.pb, weights=lb.nb)),
                                     win_rate=float(np.average(lb.yb, weights=lb.nb)), pnl=float(lb.pnl.sum()),
                                     pnl_share_of_total=float(lb.pnl.sum() / x.pnl.sum()) if x.pnl.sum() else None)
            print("   naked game-2 legs", res[f"leg_b_{p}"])

    if holdout:
        # reference only: the holdout exactly as first run, i.e. INCLUDING the probe-contaminated series
        ref = pnl(trades(ser_all, mts, idt_all[idt_all.event_slug.isin(ser_all.event_slug)], ["holdout"]))
        res["primary_holdout_incl_probe"] = summarize(ref, "REFERENCE holdout incl. probe series (first run)", p0=True)
        print("\n" + fmt(res["primary_holdout_incl_probe"]))
        # pooled DEV + holdout
        for slip in (0.0, 0.01):
            d = summarize(pnl(tr, slip=slip), f"POOLED dev+holdout {'+1c' if slip else 'fee'}", p0=True)
            res[f"pooled{'_1c' if slip else ''}"] = d; print(fmt(d))
        for k in ("pair", "leg_b"):
            d = summarize(P[P.kind == k], f"POOLED {k}", p0=True); res[f"pooled_{k}"] = d; print(fmt(d))
        # DEV vs holdout difference of the naked-leg ROI (clustered bootstrap): is the holdout gain just variance?
        a = P[(P.period == "dev") & (P.kind == "leg_b")].groupby("event_slug")[["pnl", "cost"]].sum()
        b = P[(P.period == "holdout") & (P.kind == "leg_b")].groupby("event_slug")[["pnl", "cost"]].sum()
        rng = np.random.default_rng(2)
        ia, ib = rng.integers(0, len(a), (20000, len(a))), rng.integers(0, len(b), (20000, len(b)))
        dd = (b.pnl.to_numpy()[ib].sum(1) / b.cost.to_numpy()[ib].sum(1)
              - a.pnl.to_numpy()[ia].sum(1) / a.cost.to_numpy()[ia].sum(1))
        res["leg_b_holdout_minus_dev"] = dict(mean=float(dd.mean()), ci_lo=float(np.percentile(dd, 2.5)),
                                              ci_hi=float(np.percentile(dd, 97.5)))
        print("naked-leg ROI holdout - DEV", res["leg_b_holdout_minus_dev"])

    # --- variants
    V = []
    for p in periods:
        for thr in (0.97, 0.99):
            V.append(summarize(pnl(trades(ser, mts, idt, [p], thr=thr)), f"{p} (a) thr {thr}"))
        for lat in (8, 13):
            V.append(summarize(pnl(trades(ser, mts, idt, [p], lat=lat)), f"{p} (b) entry window [t+{lat}, t+{lat + WIN}]"))
        V.append(summarize(pnl(trades(ser, mts, idt, [p], pair="rev")), f"{p} (c) reverse pair notX+1.5 & X g2"))
        if p == "dev":   # no budget left for totals in the main sample -> the 45 cached probe series, DEV only
            s45, m45, i45 = cached45()
            print(f"(d) cached probe series: {len(s45)} ({int((s45.period == 'dev').sum())} DEV), "
                  f"T1 found {int(i45.T1.notna().sum())}")
            t_ = trades(s45, m45, i45, ["dev"], pair="tot", tape_dir=SCRATCH_TAPES)
            V.append(summarize(pnl(t_), "dev (d) total-games-2pt5 Under & notX g2 [45 cached series]"))
            V.append(summarize(pnl(t_[t_.kind == "pair"]), "dev (d) totals, completed pairs only [45 cached]"))
            t_ = trades(s45, m45, i45, ["dev"], pair="hc", tape_dir=SCRATCH_TAPES)
            V.append(summarize(pnl(t_), "dev (d-ref) primary handicap rule on the same 45 cached series"))
        x = P[P.period == p]
        V.append(summarize(x[x.kind == "pair"], f"{p} (e) completed pairs only"))
        V.append(summarize(pnl(tr[(tr.period == p) & (tr.kind == "pair")], slip=0.01), f"{p} (e) completed pairs +1c"))
        res[f"robust_e_{p}"] = robustness(tr[(tr.period == p) & (tr.kind == "pair")], f"{p} (e) pairs")
        # (f) POST-HOC (designed on DEV before the holdout run, parameters = primary's): thin leg first, then hedge
        ts_ = trades(ser, mts, idt, [p], seq=True)
        Ps = pnl(ts_)
        res[f"robust_f_{p}"] = robustness(ts_, f"{p} (f) seq")
        mo = pd.to_datetime(Ps.t, unit="s").dt.strftime("%Y-%m")
        res[f"seq_{p}_months"] = [summarize(xx, f"{p} (f) month {m}") for m, xx in Ps.groupby(mo)]
        res[f"series_stats_seq_{p}"] = series_stats(Ps)
        V.append(summarize(Ps, f"{p} (f) sequential: handicap first, then hedge g2"))
        V.append(summarize(pnl(ts_, slip=0.01), f"{p} (f) sequential +1c"))
        V.append(summarize(Ps.sort_values("t").drop_duplicates("event_slug"), f"{p} (f) sequential, first attempt/series"))
        V.append(summarize(pnl(trades(ser, mts, idt, [p], seq=True, lat=8)), f"{p} (f) sequential, entry from t+8"))
        V.append(summarize(pnl(trades(ser, mts, idt, [p], seq=True, cap=np.inf), uncapped=True),
                           f"{p} (f) sequential, uncapped size"))
        for sp, xx in Ps.groupby("sport"):
            V.append(summarize(xx, f"{p} (f) sequential, sport {sp}"))
        if len(Ps):
            n_s = int((ser.period == p).sum())
            days = max((Ps.t.max() - Ps.t.min()) / 86400, 1)
            res[f"capacity_seq_{p}"] = dict(dollars=float(Ps.cost.sum()), pnl=float(Ps.pnl.sum()), days=float(days),
                                            scale=elig[p] / n_s,
                                            dollars_per_day_scaled=float(Ps.cost.sum() * elig[p] / n_s / days),
                                            pnl_per_day_scaled=float(Ps.pnl.sum() * elig[p] / n_s / days),
                                            kinds=Ps.kind.value_counts().to_dict(),
                                            attempts=int(len(ts_)),
                                            hedge_ratio=float((Ps.nb / Ps.na.where(Ps.na > 0)).mean()))
            print(f"   (f) {p} capacity", res[f"capacity_seq_{p}"])
            Ps.to_parquet(OUT / f"trades_seq_{p}{RUN_TAG}.parquet")
    print("\nVARIANTS")
    for v in V:
        print(fmt(v))
    res["variants"] = V
    print("\nROBUSTNESS OF THE MECHANISM COMPONENTS (e) completed pairs, (f) sequential")
    for p in periods:
        for key in (f"robust_e_{p}", f"robust_f_{p}"):
            for d in res[key]:
                if d["drop_top"] == 0 or d["trim_1pct"]:
                    print(fmt(d))
        for d in res[f"seq_{p}_months"]:
            print(fmt(d))
        print(f"   (f) {p} series-level", res[f"series_stats_seq_{p}"])

    # --- verdict: protocol label, then the robustness gate (review fix)
    if holdout:
        ho, dv = res["primary_holdout"], res["primary_dev"]
        label = ("PROFITABLE" if ho["roi"] > 0 and dv["ci_lo"] > 0 else "PROMISING" if ho["roi"] > 0 else "DEAD")
        gate = {f"{d['frame']}": d["roi"] for d in res["robust_holdout"] if d["slip"] == 0.01 and d["trim_1pct"]}
        passed = all(v > 0 for v in gate.values()) and len(gate) == 2
        res["verdict"] = dict(protocol_label=label, gate=ROBUST_GATE, gate_rois=gate, gate_passed=passed,
                              final=label if (passed or label == "DEAD") else "DEAD")
        print("\nVERDICT", res["verdict"])
    (OUT / f"results_{'all' if holdout else 'dev'}{RUN_TAG}.json").write_text(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a or a[0] == "fetch":
        fetch()
    if a and a[0] == "gamma":
        gamma()
    if not a or a[0] == "analyze":
        analyze(holdout="--holdout" in a)
