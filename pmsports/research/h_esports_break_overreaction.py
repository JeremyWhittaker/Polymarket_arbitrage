"""Repaired primary: frozen round1 calibration, historical later-print size proxy.

No entry is inferred from an opposite-side print or invented spread. Signals without
an entry stay in the audit with zero capital.100USD inclusive-fee target capped by
first actual print shares. Public receipt clocks and resting depth are unobserved.
The legacy discovery universe and prior rounds remain exploratory; July2026 was seen.
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from pmsports.research import common as C
from pmsports.execution import TapeReplay, VERSION

SLUG = "esports_break_overreaction"
OUT = C.RESEARCH / f"h_{SLUG}"
TAPES = OUT / "tapes"
IDENT_TAPES = C.RESEARCH / "h_esports_identity_pickoff" / "tapes"   # read-only cache reuse
HOLDOUT_TS = pd.Timestamp("2026-07-01", tz="UTC").timestamp()
MIN_PRE_USD = 25_000
MAX_FETCH = 2000
SEED = 20260919
BO5_TL = {"game4", "game5", "total-games-3pt5", "total-games-4pt5"}
KEEP_TL = ("game1", "game2", "game3", "game4", "game5", "total-games-2pt5", "total-games-3pt5", "total-games-4pt5")
TITLES = ("cs2", "lol", "dota2", "valorant", "other")

# frozen primary parameters (from the hypothesis; nothing tuned)
BRK_A, BRK_B = 30, 150          # break-price window after t_end
ENT_A, ENT_B = 153, 600         # entry window (decision at t_end+150, +3 s latency)
MIN_BRK_FILLS = 3
THR = 0.04
PRE_A, PRE_B = -150, -30        # placebo reference window
SYN_A, SYN_B, SYN_ENT, SYN_THR = 30, 180, 183, 0.08
SYN_MAX = 300
# review fixes (round 2; execution / data hygiene, no parameter re-tuned)
MIN_TEND_AFTER_START = 600      # drop series whose map 1 ended < 10 min after the scheduled start: pre_mid0
                                # (fills in [start-10 min, start)) would overlap map-1 play or post-decision prices
QUOTE_SPREAD = 0.01             # ask proxy = implied bid (1 - last opposite-side print) + 1c
N_CV_SEEDS = 20                 # cross-fit fold draws reported for the DEV robustness check


# ----------------------------------------------------------------------------- universe

def title_of(slug: str) -> str:
    p = slug.split("-")[0]
    return {"cs2": "cs2", "cs": "cs2", "lol": "lol", "dota2": "dota2", "dota": "dota2",
            "val": "valorant", "valorant": "valorant"}.get(p, "other")


def child_table() -> pd.DataFrame:
    u = C.universe(columns=["condition_id", "family", "event_slug", "market_slug", "outcome", "outcome_idx",
                            "payout", "game_start_ts", "closed_ts", "fee_rate"])
    u = u[u.family == "esports"].copy()
    u["tl"] = u.market_slug.str.replace(r"^.*\d{4}-\d\d-\d\d-?", "", regex=True)
    u = u[u.tl.isin(KEEP_TL)]
    w = u.pivot_table(index="condition_id", columns="outcome_idx", values=["outcome", "payout"], aggfunc="first")
    w.columns = [f"{'o' if a == 'outcome' else 'y'}{b}" for a, b in w.columns]
    meta = u.drop_duplicates("condition_id").set_index("condition_id")[
        ["event_slug", "market_slug", "tl", "game_start_ts", "closed_ts", "fee_rate"]]
    return meta.join(w).reset_index()


def build_universe() -> tuple[pd.DataFrame, pd.DataFrame]:
    mk = C.markets()
    ml = mk[(mk.family == "esports") & (mk.market_type == "moneyline") & (mk.pre_usd >= MIN_PRE_USD)]
    # one series market per event (a single duplicate event exists): keep the larger pregame book
    ml = ml.sort_values("pre_usd", ascending=False).drop_duplicates("event_slug")
    ct = child_table()
    tls = ct.groupby("event_slug").tl.agg(set)
    ok = tls.map(lambda s: {"game1", "game2", "total-games-2pt5"} <= s and not (s & BO5_TL))
    ser = ml[ml.event_slug.isin(tls[ok].index)].copy()
    g1 = ct[ct.tl == "game1"].set_index("event_slug")
    g2 = ct[ct.tl == "game2"].set_index("event_slug")
    assert not g1.index.duplicated().any() and not g2.index.duplicated().any()
    ser["g1_cid"] = g1.condition_id.reindex(ser.event_slug).to_numpy()
    ser["g1_closed"] = g1.closed_ts.reindex(ser.event_slug).to_numpy()
    ser["g1_y0"] = g1.y0.reindex(ser.event_slug).to_numpy()
    ser["g2_cid"] = g2.condition_id.reindex(ser.event_slug).to_numpy()
    ser["g2_y0"] = g2.y0.reindex(ser.event_slug).to_numpy()
    ser["g2_y1"] = g2.y1.reindex(ser.event_slug).to_numpy()
    ser["g2_fee"] = g2.fee_rate.reindex(ser.event_slug).to_numpy()
    # child outcome order must equal the series order
    same = ((g1.o0.reindex(ser.event_slug).to_numpy() == ser.o0.to_numpy())
            & (g1.o1.reindex(ser.event_slug).to_numpy() == ser.o1.to_numpy())
            & (g2.o0.reindex(ser.event_slug).to_numpy() == ser.o0.to_numpy())
            & (g2.o1.reindex(ser.event_slug).to_numpy() == ser.o1.to_numpy()))
    ser["names_ok"] = same
    ser["title"] = ser.event_slug.map(title_of)
    ser["period"] = np.where(ser.game_start_ts < HOLDOUT_TS, "dev", "holdout")
    rng = np.random.default_rng(SEED)
    ser = ser.iloc[rng.permutation(len(ser))].reset_index(drop=True)   # random fetch order
    ser["order"] = np.arange(len(ser))
    print(f"BO3 series with pre_usd >= $25k: {len(ser):,}  dev {int((ser.period == 'dev').sum())}  "
          f"holdout {int((ser.period == 'holdout').sum())}  child names identical {int(ser.names_ok.sum())}")
    print(pd.crosstab(ser.title, ser.period))
    return ser, ct


# ----------------------------------------------------------------------------- tapes

def tape_file(cid: str):
    return TAPES / f"{cid}.parquet"


def fetch_one(args) -> str:
    from pmsports import polymarket as PM
    cid, lo, hi, allow_api = args
    fn = tape_file(cid)
    if fn.exists():
        return "cached"
    src = IDENT_TAPES / f"{cid}.parquet"
    if src.exists():   # other study's tape over [start-1h, closed_ts] (superset) -> trim to this window
        d = pd.read_parquet(src).reindex(columns=list(PM.TRADE_FIELDS))
        d = d[(d.timestamp >= lo) & (d.timestamp <= hi)]
        how = "reused"
    elif allow_api:
        d = pd.DataFrame(PM.trades(cid, lo, hi), columns=list(PM.TRADE_FIELDS))
        how = "api"
    else:
        return "skipped"
    d.to_parquet(fn)
    return how


def fetch() -> None:
    import pmsports.http as H
    H.HOST_RPS["data-api.polymarket.com"] = 5.0
    OUT.mkdir(parents=True, exist_ok=True)
    TAPES.mkdir(parents=True, exist_ok=True)
    ser, ct = build_universe()
    ser.to_parquet(OUT / "series.parquet")
    lo = (ser.game_start_ts - 1800).astype(int)
    hi = np.maximum(ser.g1_closed.fillna(ser.closed_ts), ser.game_start_ts).astype(int)
    have = {p.stem for p in TAPES.glob("*.parquet")}
    reuse = {p.stem for p in IDENT_TAPES.glob("*.parquet")}
    n_api_left = MAX_FETCH - _api_count()
    jobs = []
    for cid, a, b in zip(ser.g1_cid, lo, hi):
        need_api = cid not in have and cid not in reuse
        allow = need_api and n_api_left > 0
        n_api_left -= int(allow)
        jobs.append((cid, int(a), int(b), allow))
    print(f"game1 tapes: {len(jobs)}  already here {sum(c in have for c, *_ in jobs)}  "
          f"reusable {sum((c in reuse) and (c not in have) for c, *_ in jobs)}  "
          f"api {sum(j[3] for j in jobs)}", flush=True)
    res = []
    with ThreadPoolExecutor(4) as ex:
        for i, r in enumerate(ex.map(fetch_one, jobs)):
            res.append(r)
            if i % 100 == 0:
                print(i, pd.Series(res).value_counts().to_dict(), flush=True)
    vc = pd.Series(res).value_counts().to_dict()
    print("game1 fetch:", vc)
    _log_api(vc.get("api", 0))
    # game2 tapes for variant (e): reuse the identity study's cache only (DEV series), no new API calls
    dev = ser[ser.period == "dev"]
    g2 = [(c, int(a) , int(b), False) for c, a, b in zip(dev.g2_cid, (dev.game_start_ts - 1800).astype(int),
                                                          dev.closed_ts.astype(int) + 86400)
          if (IDENT_TAPES / f"{c}.parquet").exists()]
    g2 = g2[:SYN_MAX]
    with ThreadPoolExecutor(4) as ex:
        vc2 = pd.Series(list(ex.map(fetch_one, g2))).value_counts().to_dict()
    print("game2 (variant e, reuse only):", vc2)


def _api_count() -> int:
    f = OUT / "api_fetch_count.json"
    return json.loads(f.read_text())["api"] if f.exists() else 0


def _log_api(n: int) -> None:
    f = OUT / "api_fetch_count.json"
    f.write_text(json.dumps({"api": _api_count() + int(n)}))


def load_tape(cid: str) -> pd.DataFrame | None:
    fn = tape_file(cid)
    if not fn.exists():
        return None
    d = pd.read_parquet(fn)
    if d.empty:
        return pd.DataFrame({"ts": np.array([], np.int64), "acq": np.array([], np.int8),
                             "q": np.array([], float), "size": np.array([], float)})
    k = d.outcomeIndex.astype(int).to_numpy()
    buy = (d.side == "BUY").to_numpy()
    px = d.price.astype(float).to_numpy()
    t = pd.DataFrame({"ts": d.timestamp.astype(np.int64).to_numpy(), "acq": np.where(buy, k, 1 - k).astype(np.int8),
                      "q": np.round(np.where(buy, px, 1 - px), 6), "size": d["size"].astype(float).to_numpy()})
    t = t[t["size"] > 0]
    return t.sort_values("ts", kind="stable").reset_index(drop=True)


# ----------------------------------------------------------------------------- model

def solve_p(P0: np.ndarray) -> np.ndarray:
    """Per-map probability p with p^2 (3 - 2p) = P0 (monotone on [0, 1]); vectorized bisection."""
    lo, hi = np.zeros_like(P0), np.ones_like(P0)
    for _ in range(60):
        mid = (lo + hi) / 2
        f = mid * mid * (3 - 2 * mid) < P0
        lo, hi = np.where(f, mid, lo), np.where(f, hi, mid)
    return (lo + hi) / 2


def logit(x):
    x = np.clip(np.asarray(x, float), 1e-6, 1 - 1e-6)
    return np.log(x / (1 - x))


def design(df: pd.DataFrame, titles: list[str]) -> np.ndarray:
    cols = [np.ones(len(df)), logit(df.fair_iid.to_numpy())]
    cols += [(df.title == t).to_numpy(float) for t in titles]
    return np.column_stack(cols)


def fit_logit(X: np.ndarray, y: np.ndarray, iters: int = 50) -> np.ndarray:
    """Plain IRLS logistic regression (no penalty)."""
    b = np.zeros(X.shape[1])
    for _ in range(iters):
        eta = X @ b
        mu = 1 / (1 + np.exp(-eta))
        W = np.clip(mu * (1 - mu), 1e-9, None)
        step = np.linalg.solve(X.T @ (X * W[:, None]) + 1e-9 * np.eye(X.shape[1]), X.T @ (y - mu))
        b = b + step
        if np.abs(step).max() < 1e-10:
            break
    return b


def calibrate(dev: pd.DataFrame) -> dict:
    d = dev[dev.cal_ok & (dev.yL != 0.5)]
    titles = [t for t in TITLES if t != "cs2" and (d.title == t).sum() >= 10]
    X = design(d, titles)
    b = fit_logit(X, d.yL.to_numpy(float))
    # standard errors
    mu = 1 / (1 + np.exp(-(X @ b)))
    cov = np.linalg.inv(X.T @ (X * (mu * (1 - mu))[:, None]))
    return {"coef": b.tolist(), "se": np.sqrt(np.diag(cov)).tolist(),
            "names": ["const", "logit_fair_iid"] + [f"title_{t}" for t in titles], "titles": titles, "n": int(len(d))}


def predict(df: pd.DataFrame, cal: dict) -> np.ndarray:
    X = design(df, cal["titles"])
    return 1 / (1 + np.exp(-(X @ np.asarray(cal["coef"]))))


# ----------------------------------------------------------------------------- per-series events

def first_fill(ts, s, q, size, rate, side, a, b):
    """First fill acquiring `side` with ts in [a, b] -> (ts, price, shares, fee_rate) or None."""
    i = np.searchsorted(ts, a, "right")
    e = np.searchsorted(ts, b, "left")
    k = np.flatnonzero(s[i:e] == side)
    if not len(k):
        return None
    j = i + k[0]
    return int(ts[j]), float(q[j]), float(size[j]), float(rate[j])


def quote_proxy(ts, s, q, rate, side, a, b):
    """Historical trades cannot reconstruct an ask; deliberately no executable fallback."""
    return None


def entry(ts, s, q, size, rate, side, dec, ent_a, ent_b, qa, qb):
    """First actual relevant print strictly after delayed eligibility; no synthetic quote."""
    assert qb <= dec and ent_a >= dec + 3
    tape = pd.DataFrame(dict(m=0, s=s, ts=ts, q=q, size=size, fee_rate=rate))
    orders = pd.DataFrame([dict(m=0, s=side, signal_ts=dec, expiry_ts=ent_b, y=np.nan)])
    row = TapeReplay(tape).replay(orders, delay_s=ent_a - dec).iloc[0]
    if row.shares <= 0:
        return None
    j = np.searchsorted(ts, row.fill_ts, side="left")
    match = np.flatnonzero((ts == row.fill_ts) & (s == side) & np.isclose(q, row.entry_price))
    j = match[0] if len(match) else j
    return float(row.fill_ts), float(row.entry_price), float(size[j]), float(rate[j]), "window"


def window_cap(ts, s, q, size, side, a, b, pmax):
    """$ of fills acquiring `side` in [a, b] at price <= pmax (liquidity actually taken at that price or better)."""
    i = np.searchsorted(ts, a, "left")
    e = np.searchsorted(ts, b, "right")
    m = (s[i:e] == side) & (q[i:e] <= pmax + 1e-9)
    return float((q[i:e][m] * size[i:e][m]).sum())


def med_window(ts, pL, a, b):
    i = np.searchsorted(ts, a, "left")
    e = np.searchsorted(ts, b, "right")
    return (float(np.median(pL[i:e])) if e > i else np.nan), int(e - i)


def build_events(ser: pd.DataFrame) -> pd.DataFrame:
    mk = C.markets().set_index("m")
    f = C.fills(markets=ser.m.tolist(), columns=["m", "ts", "s", "q", "size", "fee_rate"])
    f = f.sort_values(["m", "ts"], kind="stable")
    grp = {m: g for m, g in f.groupby("m", sort=False)}
    rows = []
    for r in ser.itertuples():
        rec = dict(event_slug=r.event_slug, m=r.m, title=r.title, period=r.period, start=r.game_start_ts,
                   pre_usd=r.pre_usd, pre_mid0=r.pre_mid0, fee_mkt=r.fee_rate, has_tape=False, t_end=np.nan, L=-1,
                   execution_version=VERSION)
        for name in ("T", "L", "NCT", "NCL", "QT", "QL"):
            for col in ("q", "ts", "sh", "rate", "cap"):
                rec[f"ent{name}_{col}"] = np.nan
            rec[f"ent{name}_how"] = "unfilled"
        g1 = load_tape(r.g1_cid)
        if g1 is not None:
            rec["has_tape"] = True
            rec["g1_fills"] = len(g1)
            # prices rounded to 1e-6: 1 - 0.99 is 0.0100000000009 in floating point and would miss the rule
            p0 = np.round(np.where(g1.acq.to_numpy() == 0, g1.q.to_numpy(), 1 - g1.q.to_numpy()), 6)
            hit = np.flatnonzero((p0 >= 0.99) | (p0 <= 0.01))
            if len(hit):
                i = hit[0]
                rec["t_end"] = int(g1.ts.iloc[i])
                rec["L"] = 0 if p0[i] >= 0.99 else 1
                rec["g1_winner"] = 0 if r.g1_y0 == 1 else (1 if r.g1_y0 == 0 else -1)
        if rec["L"] >= 0 and r.m in grp:
            L, te = rec["L"], rec["t_end"]
            g = grp[r.m]
            ts = g.ts.to_numpy(np.int64)
            s = g.s.to_numpy(np.int8)
            q = np.round(g.q.to_numpy(float), 6)   # float32 storage -> exact ticks
            sz = g["size"].to_numpy(float)
            rt = np.round(g.fee_rate.to_numpy(float), 6)
            pL = np.where(s == L, q, 1 - q)
            y = (mk.at[r.m, "y0"], mk.at[r.m, "y1"])
            rec["yL"] = float(y[L])
            rec["yT"] = float(y[1 - L])
            rec["P0"] = float(r.pre_mid0 if L == 0 else 1 - r.pre_mid0) if pd.notna(r.pre_mid0) else np.nan
            rec["P_break"], rec["n_brk"] = med_window(ts, pL, te + BRK_A, te + BRK_B)
            rec["P_pre"], rec["n_pre"] = med_window(ts, pL, te + PRE_A, te + PRE_B)
            # last L-oriented print strictly before t_end (diagnostic of the jump)
            j = np.searchsorted(ts, te, "left") - 1
            rec["P_last_pre"] = float(pL[j]) if j >= 0 else np.nan
            for nm, side in (("T", 1 - L), ("L", L)):
                # Actual later acquired-side print only; retain missing entry as unfilled.
                ff = entry(ts, s, q, sz, rt, side, te + BRK_B, te + ENT_A, te + ENT_B, te + BRK_A, te + BRK_B)
                if ff:
                    (rec[f"ent{nm}_ts"], rec[f"ent{nm}_q"], rec[f"ent{nm}_sh"], rec[f"ent{nm}_rate"],
                     rec[f"ent{nm}_how"]) = ff
                    rec[f"ent{nm}_cap"] = ff[1] * ff[2]
                # sensitivity 1: next print on our side after decision + 3 s with NO 600 s cap
                nc = first_fill(ts, s, q, sz, rt, side, te + ENT_A, np.inf)
                if nc:
                    rec[f"entNC{nm}_ts"], rec[f"entNC{nm}_q"], rec[f"entNC{nm}_sh"], rec[f"entNC{nm}_rate"] = nc
                # Deprecated quote reconstruction intentionally supplies no fill.
                qp = quote_proxy(ts, s, q, rt, side, te + BRK_A, te + BRK_B)
                if qp:
                    rec[f"entQ{nm}_q"], rec[f"entQ{nm}_rate"] = qp
            # Variant(e): independently observed actual later prints for each leg.
            rec["S_L"], rec["n_S"] = med_window(ts, pL, te + SYN_A, te + SYN_B)
            for nm, side in (("sT", 1 - L), ("sL", L)):
                ff = entry(ts, s, q, sz, rt, side, te + SYN_B, te + SYN_ENT, te + ENT_B, te + SYN_A, te + SYN_B)
                if ff:
                    rec[f"{nm}_q"], rec[f"{nm}_rate"], rec[f"{nm}_how"], rec[f"{nm}_sh"] = ff[1], ff[3], ff[4], ff[2]
        rows.append(rec)
    ev = pd.DataFrame(rows)
    P0 = np.clip(ev.P0.to_numpy(float), 0.01, 0.99)
    ev["p"] = solve_p(np.nan_to_num(P0, nan=0.5))
    ev["fair_iid"] = 1 - (1 - ev.p) ** 2
    ev.loc[ev.P0.isna(), ["p", "fair_iid"]] = np.nan
    # review fix: pre_mid0 is taken over [start - 10 min, start). If map 1 ended before start + 10 min, that window
    # overlaps map-1 play (or, when t_end < start, the post-decision break). Such series leave the calibration AND
    # every rule. t_end and the scheduled start are both known at the decision.
    ev["pre_clean"] = ev.t_end.ge(ev.start + MIN_TEND_AFTER_START)
    ev["cal_ok"] = ev.L.ge(0) & ev.P0.notna() & ev.yL.notna() & ev.pre_clean
    return ev


def synth_rows(ev: pd.DataFrame, ser: pd.DataFrame) -> pd.DataFrame:
    """Variant (e) inputs from cached game2 tapes (DEV only)."""
    s2 = ser.set_index("event_slug")
    rows = []
    for r in ev[(ev.period == "dev") & ev.cal_ok].itertuples():
        cid = s2.at[r.event_slug, "g2_cid"]
        g2 = load_tape(cid)
        if g2 is None or not len(g2):
            continue
        L, te = int(r.L), int(r.t_end)
        ts = g2.ts.to_numpy(np.int64)
        s = g2.acq.to_numpy(np.int8)
        q = g2.q.to_numpy(float)
        sz = g2["size"].to_numpy(float)
        rt = np.full(len(ts), float(s2.at[r.event_slug, "g2_fee"]))
        pL = np.where(s == L, q, 1 - q)
        gL, n_g = med_window(ts, pL, te + SYN_A, te + SYN_B)
        y2 = (s2.at[r.event_slug, "g2_y0"], s2.at[r.event_slug, "g2_y1"])
        rec = dict(event_slug=r.event_slug, gL=gL, n_g=n_g, y2L=float(y2[L]), y2T=float(y2[1 - L]))
        for nm, side in (("gT", 1 - L), ("gL", L)):
            ff = entry(ts, s, q, sz, rt, side, te + SYN_B, te + SYN_ENT, te + ENT_B, te + SYN_A, te + SYN_B)
            if ff:
                rec[f"{nm}_q"], rec[f"{nm}_rate"], rec[f"{nm}_how"], rec[f"{nm}_sh"] = ff[1], ff[3], ff[4], ff[2]
        rows.append(rec)
        if len(rows) >= SYN_MAX:
            break
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- bets / stats

def bets(ev: pd.DataFrame, side: str, cond: pd.Series, entry_kind: str = "") -> pd.DataFrame:
    """Retain all signals; unknown entry has zero allocated shares and zero PnL."""
    k = f"ent{entry_kind}{side}"
    b = ev[cond].copy()
    b["q"] = b.get(f"{k}_q", pd.Series(np.nan, index=b.index))
    b["rate"] = b.get(f"{k}_rate", pd.Series(np.nan, index=b.index)).fillna(b.fee_mkt)
    b["won"] = b[f"y{side}"]
    b["how"] = b.get(f"{k}_how", pd.Series("unfilled", index=b.index))
    b["available_shares"] = b.get(f"{k}_sh", pd.Series(0., index=b.index)).fillna(0)
    b["cap"] = (b.available_shares * b.q).fillna(0)
    b["first_usd"] = b.cap
    b["delay_s"] = b.get(f"{k}_ts", pd.Series(np.nan, index=b.index)) - b.t_end
    b["n_signal"] = len(b)
    b["signal_ts"] = b.t_end + BRK_B
    b["eligible_ts"] = b.signal_ts + 3
    b["fill_ts"] = b.get(f"{k}_ts", pd.Series(np.nan, index=b.index))
    valid = b.q.between(.000001, .999999) & b.fill_ts.gt(b.eligible_ts) & b.rate.ge(0)
    if entry_kind == "":
        valid &= b.fill_ts.lt(b.t_end + ENT_B)
    unitfee = C.taker_fee(1., b.q, b.rate)
    b["shares"] = np.where(valid, np.minimum(b.available_shares, 100 / (b.q + unitfee)), 0)
    b["stake_usd"] = (b.shares * b.q).fillna(0)
    b["fee_usd"] = (b.shares * unitfee).fillna(0)
    b["cost_usd"] = b.stake_usd + b.fee_usd
    b["payout"] = b.shares * b.won
    b["pnl_usd"] = b.payout - b.cost_usd
    b["roi"] = np.where(b.cost_usd > 0, b.pnl_usd / b.cost_usd, np.nan)
    b["status"] = np.where(b.cost_usd <= 0, "unfilled", np.where(b.cost_usd >= 100 - 1e-8, "filled", "partial"))
    return b


def summ(b: pd.DataFrame, slip: float = 0.0) -> dict:
    if b is None or len(b) == 0:
        return dict(signals=0, bets=0, unfilled=0, roi=np.nan, ci_lo=np.nan, ci_hi=np.nan)
    g = b[b.shares > 0].copy()
    c = g.q + slip
    valid = c.lt(1)
    g, c = g[valid], c[valid]
    fee1 = C.taker_fee(1., c, g.rate)
    shares = np.minimum(g.available_shares, 100 / (c + fee1))
    cost = shares * (c + fee1)
    pnl = shares * g.won - cost
    roi = pnl / cost
    m, lo, hi = C.cluster_ci(roi, g.event_slug, weights=cost) if len(g) else (np.nan,) * 3
    return dict(signals=len(b), bets=len(g), unfilled=len(b) - len(g), partial=int((cost < 100 - 1e-8).sum()),
                roi=round(m, 4), ci_lo=round(lo, 4), ci_hi=round(hi, 4),
                capital_usd=float(cost.sum()), pnl_usd=float(pnl.sum()),
                win=float(g.won.mean()), avg_q=float(g.q.mean()), avg_fee_rate=float(g.rate.mean()),
                first_print_usd=float(g.first_usd.sum()), window_cap_usd=float(g.cap.sum()),
                median_window_cap_usd=float(g.cap.median()))


def both(b: pd.DataFrame) -> dict:
    return {"fee": summ(b), "fee_plus_1c": summ(b, .01), "signals": len(b),
            "bets": int(b.shares.gt(0).sum()), "entry_how": b.how.value_counts().to_dict()}


def calib_table(e: pd.DataFrame) -> dict:
    e = e[e.cal_ok & e.P_break.notna() & (e.yL != 0.5)]
    out = dict(n=int(len(e)), mean_yL=round(float(e.yL.mean()), 4), mean_P_break=round(float(e.P_break.mean()), 4),
               mean_P_cal=round(float(e.P_cal.mean()), 4), mean_fair_iid=round(float(e.fair_iid.mean()), 4),
               brier_P_break=round(float(((e.P_break - e.yL) ** 2).mean()), 4),
               brier_P_cal=round(float(((e.P_cal - e.yL) ** 2).mean()), 4),
               brier_fair_iid=round(float(((e.fair_iid - e.yL) ** 2).mean()), 4))
    # does the gap predict the market's error? slope of (yL - P_break) on (P_break - P_cal)
    x = (e.P_break - e.P_cal).to_numpy()
    z = (e.yL - e.P_break).to_numpy()
    out["slope_err_on_gap"] = round(float(np.polyfit(x, z, 1)[0]), 4) if len(e) > 10 else np.nan
    bins = pd.cut(e.P_break - e.P_cal, [-1, -0.08, -0.04, 0, 0.04, 0.08, 1])
    tb = e.groupby(bins, observed=True).agg(n=("yL", "size"), yL=("yL", "mean"), P_break=("P_break", "mean"),
                                            P_cal=("P_cal", "mean"))
    out["by_gap"] = {str(k): {kk: round(float(vv), 4) for kk, vv in v.items()} for k, v in tb.iterrows()}
    return out


def evaluate(ev: pd.DataFrame, per: str, syn: pd.DataFrame | None = None) -> dict:
    e = ev[ev.period == per]
    ok = e.cal_ok & (e.n_brk >= MIN_BRK_FILLS)
    gap = e.P_break - e.P_cal
    res = {"period": per, "series": int(len(e)), "with_tape": int(e.has_tape.sum()),
           "with_map1_end": int((e.L >= 0).sum()),
           "dropped_t_end_lt_start_plus_10min": int(((e.L >= 0) & ~e.pre_clean).sum()),
           "cal_ok": int(e.cal_ok.sum()), "with_break_price": int(ok.sum())}
    det = e[(e.L >= 0) & e.g1_winner.isin([0, 1])]
    res["detect_L_equals_game1_winner"] = round(float((det.L == det.g1_winner).mean()), 4) if len(det) else np.nan
    res["t_end_minus_start_min"] = e.loc[e.L >= 0, "t_end"].sub(e.start).div(60).describe().round(1).to_dict()
    res["calibration"] = calib_table(e)
    B = {}
    B["primary_thr0.04"] = bets(e, "T", ok & (gap >= THR))
    B["a_thr0.03"] = bets(e, "T", ok & (gap >= 0.03))
    B["a_thr0.06"] = bets(e, "T", ok & (gap >= 0.06))
    B["b_mirror_buyL_thr0.04"] = bets(e, "L", ok & (gap <= -THR))
    okp = ok & (e.n_pre >= MIN_BRK_FILLS)
    B["d_placebo_buyT"] = bets(e, "T", okp & (e.P_break - e.P_pre >= THR))
    B["d_placebo_mirror_buyL"] = bets(e, "L", okp & (e.P_break - e.P_pre <= -THR))
    if "P_cal_cv" in e:
        B["x_crossfit_primary"] = bets(e.assign(P_cal=e.P_cal_cv), "T",
                                       ok & (e.P_break - e.P_cal_cv >= THR))
        B["x_crossfit_mirror"] = bets(e.assign(P_cal=e.P_cal_cv), "L",
                                      ok & (e.P_break - e.P_cal_cv <= -THR))
    B["x_all_trailers_no_signal"] = bets(e, "T", ok)
    B["x_all_leaders_no_signal"] = bets(e, "L", ok)
    # entry-convention sensitivities (review fix): same signals, different fill conventions
    B["s_primary_entry_next_print_nocap"] = bets(e, "T", ok & (gap >= THR), "NC")
    B["s_primary_entry_quote_all"] = bets(e, "T", ok & (gap >= THR), "Q")
    B["s_mirror_entry_next_print_nocap"] = bets(e, "L", ok & (gap <= -THR), "NC")
    B["s_mirror_entry_quote_all"] = bets(e, "L", ok & (gap <= -THR), "Q")
    res["rules"] = {k: both(v) for k, v in B.items()}
    nc = B["s_primary_entry_next_print_nocap"]
    res["primary_nocap_entry_delay_s"] = nc.delay_s.describe().round(0).to_dict() if len(nc) else {}
    prim = B["primary_thr0.04"]
    res["primary_by_title"] = {t: both(prim[prim.title == t]) for t in TITLES if (prim.title == t).any()}
    mir = B["b_mirror_buyL_thr0.04"]
    res["mirror_by_title"] = {t: both(mir[mir.title == t]) for t in TITLES if (mir.title == t).any()}
    for k in ("primary_thr0.04",):
        bb = B[k]
        yr = np.where(bb.start < pd.Timestamp("2026-01-01", tz="UTC").timestamp(), "2025", "2026")
        res[f"{k}_by_year"] = {y: both(bb[yr == y]) for y in ("2025", "2026") if (yr == y).any()}
        res[f"{k}_by_fee_rate"] = {str(fr): both(bb[bb.fee_mkt.fillna(0) == fr]) for fr in sorted(bb.fee_mkt.fillna(0).unique())}
        # robustness: drop the top 1% / 3 / 5 series by P&L (per $1)
        r = bb.pnl_usd.to_numpy()
        order = np.argsort(-r)
        rob = {}
        for n_drop in (int(np.ceil(0.01 * len(bb))), 3, 5):
            keep = np.ones(len(bb), bool)
            keep[order[:n_drop]] = False
            rob[f"drop_top_{n_drop}"] = summ(bb[keep])
        res[f"{k}_robust"] = rob
        res[f"{k}_gap_mean"] = round(float((bb.P_break - bb.P_cal).mean()), 4) if len(bb) else np.nan
    res["_bets"] = B
    if syn is not None and len(syn):
        res["e_synthetic_decider"] = eval_synth(e, syn)
    return res


def eval_synth(e: pd.DataFrame, syn: pd.DataFrame) -> dict:
    d = e.merge(syn, on="event_slug")
    d = d[(d.n_S >= MIN_BRK_FILLS) & (d.n_g >= MIN_BRK_FILLS) & (d.gL < 0.999)]
    d["p3"] = (d.S_L - d.gL) / (1 - d.gL)
    out = {"series_with_game2_tape": int(len(syn)), "with_prices": int(len(d)),
           "p3_minus_phat": d.eval("p3 - p").describe().round(4).to_dict()}
    rows = []
    for r in d.itertuples():
        if r.p3 < r.p - SYN_THR:
            legs = [("sL", r.yL), ("gT", r.y2T)]
            kind = "long_L_map3"
        elif r.p3 > r.p + SYN_THR:
            legs = [("sT", r.yT), ("gL", r.y2L)]
            kind = "short_L_map3"
        else:
            continue
        for slip in (0.0, 0.01):
            cost, pay, n_legs, n_quote = 0.0, 0.0, 0, 0
            for nm, y in legs:
                qv = getattr(r, f"{nm}_q", np.nan)
                if pd.isna(qv):
                    continue
                c = min(max(qv + slip, 0.001), 0.999)
                sh = min(1., max(0., getattr(r, f"{nm}_sh", 0.)))
                if sh <= 0:
                    continue
                cost += sh * (c + float(C.taker_fee(1.0, c, getattr(r, f"{nm}_rate"))))
                pay += sh * y
                n_legs += 1
                n_quote += int(getattr(r, f"{nm}_how", "") == "quote")
            rows.append(dict(event_slug=r.event_slug, kind=kind, slip=slip, n_legs=n_legs, n_quote=n_quote,
                             cost=cost, pay=pay))
    t = pd.DataFrame(rows)
    for kind in ("long_L_map3", "short_L_map3"):
        for slip in (0.0, 0.01):
            x = t[(t.kind == kind) & (t.slip == slip)] if len(t) else t
            if len(x) == 0:
                out[f"{kind}_slip{slip}"] = dict(bets=0)
                continue
            signals = len(x)
            x = x[x.cost > 0]
            m, lo, hi = C.cluster_ci((x.pay - x.cost) / x.cost, x.event_slug, weights=x.cost) if len(x) else (np.nan,) * 3
            out[f"{kind}_slip{slip}"] = dict(signals=signals, unfilled=signals-len(x), bets=int(len(x)), both_legs=int((x.n_legs == 2).sum()),
                                             quote_legs=int(x.n_quote.sum()),
                                             roi=round(m, 4), ci_lo=round(lo, 4), ci_hi=round(hi, 4))
    return out


def crossfit(ev: pd.DataFrame, k: int = 5, seed: int = SEED) -> np.ndarray:
    """Out-of-fold P_cal for DEV series (5 folds by series)."""
    out = np.full(len(ev), np.nan)
    dev_idx = np.flatnonzero((ev.period == "dev").to_numpy() & ev.cal_ok.to_numpy())
    rng = np.random.default_rng(seed)
    folds = rng.integers(0, k, len(dev_idx))
    for fo in range(k):
        tr = ev.iloc[dev_idx[folds != fo]]
        cal = calibrate(tr)
        te = dev_idx[folds == fo]
        out[te] = predict(ev.iloc[te], cal)
    return out


def crossfit_seeds(ev: pd.DataFrame, n: int = N_CV_SEEDS) -> dict:
    """DEV primary / mirror with the cross-fitted calibration under n different fold draws (the single-draw DEV CI
    decides between PROFITABLE and PROMISING, so its dependence on the fold assignment is reported)."""
    rows = []
    e0 = ev[ev.period == "dev"]
    for i in range(n):
        cv = crossfit(ev, seed=SEED + i)[(ev.period == "dev").to_numpy()]
        e = e0.assign(P_cal=cv)
        ok = e.cal_ok & (e.n_brk >= MIN_BRK_FILLS)
        for nm, side, cond in (("primary", "T", ok & (e.P_break - e.P_cal >= THR)),
                               ("mirror", "L", ok & (e.P_break - e.P_cal <= -THR))):
            s = summ(bets(e, side, cond))
            rows.append(dict(seed=i, rule=nm, bets=s["bets"], roi=s["roi"], ci_lo=s["ci_lo"], ci_hi=s["ci_hi"]))
    t = pd.DataFrame(rows)
    out = {}
    for nm, g in t.groupby("rule"):
        out[nm] = {"n_seeds": int(len(g)), "roi_median": round(float(g.roi.median()), 4),
                   "roi_min": round(float(g.roi.min()), 4), "roi_max": round(float(g.roi.max()), 4),
                   "ci_lo_median": round(float(g.ci_lo.median()), 4), "ci_lo_max": round(float(g.ci_lo.max()), 4),
                   "share_ci_lo_gt_0": round(float((g.ci_lo > 0).mean()), 3),
                   "bets_median": float(g.bets.median())}
    return out


def jsonable(o):
    if isinstance(o, dict):
        return {str(k): jsonable(v) for k, v in o.items() if not str(k).startswith("_")}
    if isinstance(o, (list, tuple)):
        return [jsonable(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    return o


def analyze(holdout: bool = False) -> None:
    ser = pd.read_parquet(OUT / "series.parquet")
    evf = OUT / "events.parquet"
    ev = build_events(ser)  # legacy event cache had unversioned execution assumptions
    ev.to_parquet(evf)
    calf = OUT / "calib_v1.json"
    if not calf.exists():
        raise RuntimeError("frozen round1 calibration is required; do not silently refit after inspection")
    cal = json.loads(calf.read_text())
    print("calibration:", json.dumps(cal))
    ev["P_cal"] = np.where(ev.cal_ok, predict(ev.assign(fair_iid=ev.fair_iid.fillna(0.5)), cal), np.nan)
    ev["P_cal_cv"] = crossfit(ev)
    syn = synth_rows(ev, ser)
    res = {"calibration_model": cal, "model_status": "frozen round1; historically explored windows",
           "execution": "strictly later actual print; first-print shares;100 inclusive-dollar cap; no quote fallback",
           "coverage": "legacy retrospective series discovery and bounded tapes; not an unbiased opportunity census",
           "dev": evaluate(ev, "dev", syn)}
    res["dev_crossfit_seeds"] = crossfit_seeds(ev)
    if holdout:
        res["holdout"] = evaluate(ev, "holdout")
        v1 = OUT / "calib.json"      # round2 after holdout inspection: exploratory sensitivity only
        if v1.exists():
            c1 = json.loads(v1.read_text())
            e1 = ev.assign(P_cal=np.where(ev.cal_ok, predict(ev.assign(fair_iid=ev.fair_iid.fillna(0.5)), c1), np.nan))
            res["exploratory_round2"] = {}
            for per in ("dev", "holdout"):
                x = e1[e1.period == per]
                okx = x.cal_ok & (x.n_brk >= MIN_BRK_FILLS)
                res["exploratory_round2"][per] = {
                    "primary": both(bets(x, "T", okx & (x.P_break - x.P_cal >= THR))),
                    "mirror": both(bets(x, "L", okx & (x.P_break - x.P_cal <= -THR)))}
    for per in ("dev", "holdout") if holdout else ("dev",):
        B = res[per]["_bets"]
        pd.concat([v.assign(rule=k) for k, v in B.items()]).to_parquet(OUT / f"bets_{per}.parquet")
    export_ledger(res, ser)
    tag = "holdout" if holdout else "dev"
    (OUT / f"results_{tag}.json").write_text(json.dumps(jsonable(res), indent=1))
    print(json.dumps(jsonable(res), indent=1))


def export_ledger(res, ser):
    """Canonical full ledger; downstream code must use allocated cash, never rescale to$1."""
    rows = []
    for per in ("dev", "holdout"):
        if per in res:
            rows.append(res[per]["_bets"]["primary_thr0.04"])
    b = pd.concat(rows, ignore_index=True)
    audit = b[["period", "event_slug", "title", "q", "fill_ts", "signal_ts", "eligible_ts",
               "shares", "stake_usd", "fee_usd", "cost_usd", "payout", "pnl_usd", "roi", "status"]].copy()
    audit = audit.rename(columns={"event_slug": "event", "title": "league", "q": "entry_price", "fill_ts": "entry_ts"})
    audit["id"] = np.arange(1, len(audit) + 1)
    audit["sport"], audit["exit_kind"] = "esports", "resolution"
    audit["exit_ts"] = audit.event.map(ser.set_index("event_slug").closed_ts)
    audit["exit_price"] = b.won
    audit["side"] = "map1 trailing team"
    audit["note"] = "Frozen round1; later actual transaction proxy; public receipt/book depth unknown"
    doc = dict(slug=SLUG, title="Esports between-map overreaction: frozen round1", group="Sports studies",
        sport="esports", verdict="UNVALIDATED", entry_rule="Frozen4point gap; actual same-side print strictly after3s;first-print size;100 inclusive-dollar cap",
        exit_rule="Settlement", cost_model="Actual historical fee", truncated=False, n_total_trades=len(audit),
        caveats=["No invented quote fills", "All selected no-fill signals retained", "Historically explored windows", "Legacy series discovery/tape coverage remains incomplete"],
        headline={per: res[per]["rules"]["primary_thr0.04"]["fee"] for per in ("dev", "holdout") if per in res},
        columns=list(audit), rows=json.loads(audit.to_json(orient="values")),
        code_path="pmsports/research/h_esports_break_overreaction.py", report_path="reports/research/esports_break_overreaction.md")
    target = C.RESEARCH / "ledgers" / f"{SLUG}.json"
    target.parent.mkdir(exist_ok=True, parents=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(jsonable(doc), separators=(",", ":"), allow_nan=False))
    tmp.replace(target)


def extras() -> None:
    """Post-holdout descriptive diagnostics (no refit, no new rule): per-title break-price calibration,
    all-leader / all-trailer baselines by title, mirror robustness and pooled estimate, month split, capacity."""
    ev = pd.read_parquet(OUT / "events.parquet")
    cal = json.loads((OUT / "calib_v1.json").read_text())
    ev["P_cal"] = np.where(ev.cal_ok, predict(ev.assign(fair_iid=ev.fair_iid.fillna(0.5)), cal), np.nan)
    bd, bh = pd.read_parquet(OUT / "bets_dev.parquet"), pd.read_parquet(OUT / "bets_holdout.parquet")

    def s(b, slip=0.0):
        return summ(b, slip)
    out = {}
    for per in ("dev", "holdout"):
        d = ev[(ev.period == per) & ev.cal_ok & (ev.n_brk >= MIN_BRK_FILLS) & (ev.yL != 0.5)]
        g = d.groupby("title").agg(n=("yL", "size"), yL=("yL", "mean"), P_break=("P_break", "mean"),
                                   P_cal=("P_cal", "mean"), fair_iid=("fair_iid", "mean"))
        g["yL_minus_P_break"] = g.yL - g.P_break
        out[f"calib_by_title_{per}"] = g.round(4).to_dict(orient="index")
        B = bd if per == "dev" else bh
        for rule, nm in (("x_all_leaders_no_signal", "all_leaders"), ("x_all_trailers_no_signal", "all_trailers")):
            x = B[B.rule == rule]
            out[f"{nm}_by_title_{per}"] = {t: {"fee": s(x[x.title == t]), "fee_plus_1c": s(x[x.title == t], 0.01)}
                                          for t in ("cs2", "lol", "dota2", "valorant")}
    mh = bh[bh.rule == "b_mirror_buyL_thr0.04"]
    rr = mh.pnl_usd.to_numpy()
    o = np.argsort(-rr)
    for k in (int(np.ceil(0.01 * len(mh))), 3, 5):
        keep = np.ones(len(mh), bool)
        keep[o[:k]] = False
        out[f"mirror_holdout_drop_top_{k}"] = {"fee": s(mh[keep]), "fee_plus_1c": s(mh[keep], 0.01)}
    pooled = pd.concat([bd[bd.rule == "x_crossfit_mirror"], mh])
    out["mirror_pooled_crossfitDEV_plus_holdout"] = {"fee": s(pooled), "fee_plus_1c": s(pooled, 0.01)}
    ph0 = bh[bh.rule == "primary_thr0.04"]
    pooled = pd.concat([bd[bd.rule == "x_crossfit_primary"], ph0])
    out["primary_pooled_crossfitDEV_plus_holdout"] = {"fee": s(pooled), "fee_plus_1c": s(pooled, 0.01)}
    # round 1 vs round 2 holdout primary bets (which series moved in or out, and why)
    v1f = OUT / "bets_holdout_v1.parquet"
    if v1f.exists():
        p1 = pd.read_parquet(v1f)
        p1 = p1[p1.rule == "primary_thr0.04"]
        c1 = json.loads((OUT / "calib_v1.json").read_text())
        e = ev.set_index("event_slug")
        e["P_cal_v1"] = np.where(e.cal_ok, predict(e.assign(fair_iid=e.fair_iid.fillna(0.5)), c1), np.nan)
        only1 = sorted(set(p1.event_slug) - set(ph0.event_slug))
        only2 = sorted(set(ph0.event_slug) - set(p1.event_slug))

        def row(sl, b):
            r = b.set_index("event_slug").loc[sl] if sl in set(b.event_slug) else None
            return dict(event_slug=sl, title=e.at[sl, "title"], pre_clean=bool(e.at[sl, "pre_clean"]),
                        gap_v1=round(float(e.at[sl, "P_break"] - e.at[sl, "P_cal_v1"]), 4),
                        gap_v2=round(float(e.at[sl, "P_break"] - e.at[sl, "P_cal"]), 4),
                        q=None if r is None else round(float(r.q), 4), won_T=float(e.at[sl, "yT"]))
        out["holdout_primary_only_round1"] = [row(x, p1) for x in only1]
        out["holdout_primary_only_round2"] = [row(x, ph0) for x in only2]
        # the round-1 bet list with the one dropped signal filled at its quote proxy (= s_holdout_calib_v1)
        common = sorted(set(p1.event_slug) & set(ph0.event_slug))
        out["holdout_primary_common_bets"] = s(ph0[ph0.event_slug.isin(common)])
    days = (ev[ev.period == "holdout"].start.max() - HOLDOUT_TS) / 86400
    ph = bh[bh.rule == "primary_thr0.04"]
    for nm, b in (("primary", ph), ("mirror", mh)):
        mo = pd.to_datetime(b.start, unit="s").dt.strftime("%Y-%m")
        out[f"{nm}_holdout_by_month"] = {m_: s(b[mo == m_]) for m_ in sorted(mo.unique())}
        out[f"{nm}_holdout_capacity"] = dict(
            days=round(days, 1), bets_per_day=round(len(b) / days, 2),
            first_print_usd_per_day=round(float(b.first_usd.sum()) / days, 0),
            window_cap_usd_per_day=round(float(b.cap.sum()) / days, 0),
            median_first_print_usd=round(float(b.first_usd.median()), 1),
            median_window_cap_usd=round(float(b.cap.median()), 0))
    (OUT / "results_extras.json").write_text(json.dumps(jsonable(out), indent=1))
    print(json.dumps(jsonable(out), indent=1))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "analyze"
    if cmd == "fetch":
        fetch()
    elif cmd == "analyze":
        analyze(holdout="--holdout" in sys.argv)
    elif cmd == "extras":
        extras()
