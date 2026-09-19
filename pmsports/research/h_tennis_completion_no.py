"""Hypothesis: certainty premium in tennis "Completed Match?" markets -- sell Yes to yield-seekers.

Mechanism claimed: "Yes, completed" looks like a free 97-99c coupon, but ~6% of ATP/WTA matches are
not completed (walkover, retirement, cancellation all resolve No). The market type is new (May 2026),
thinly made, and has no arbitrage anchor, so takers who buy Yes may overpay.

Rules text (gamma, checked on 2026-05 and 2026-09 markets): Yes only if the match is "played to
completion through normal play"; "If a forfeit of any kind occurs, including but not limited to a
walkover or retirement ... No"; cancelled / not played / tie / not finished within 7 (later 14) days
-> No. So a walkover before the first ball is No. Voids (0.5/0.5) are paid at 0.5.

Rule as implemented (pre-registered; parameters frozen before the holdout run):
  SAMPLE   Every `tennis_completed_match` market in C.universe() with league in {atp, wta} and
           game_start_ts >= 2026-05-09 (7,401 markets). No volume filter on the analysed set.
           Fetch-budget shortcut: a market whose total volume is exactly 0 has no fills at all, so it
           cannot enter a statistic that is defined over markets with >= 1 fill; we therefore draw the
           fetch sample from markets with volume > 0 (checked on 20 random volume == 0 markets that are
           also fetched: all must be empty). Seeded random order. DEV (game_start < 2026-07-01): all
           1,098 such markets. HOLDOUT: a random 882, so the study fetches 2,000 markets in total.
           Taker fills fetched from the Data API for [T-48h, T), T = game_start_ts.
  FILLS    Data API rows normalised to "taker acquired side s (0 = Yes, 1 = No) at price q"; the token id
           is authoritative for the side. Rows of one taker order at one price level (same tx, wallet,
           side, price) are merged into one fill.
  PRIMARY  Maker selling Yes. For every fill acquiring Yes with ts in [T-6h, T):
             per-share P&L = q - y_yes + 0.15 * fee_rate * q * (1 - q)   (no maker fee, 15% rebate)
           on min(size, 200) shares. Per market: share-weighted mean. Statistic = equal-weight mean over
           markets with >= 1 such fill, 95% CI bootstrapped by match date (UTC date of T).
           Also reported: ROI on capital (capital per share = 1 - q, the No collateral).
  +1c      Every fill priced 1c worse for us (q - 0.01).
Variants (development-free, all reported):
  (a) taker No: decision D = T-10 min. If the median No-converted fill price in [T-60 min, D) is below
      base_rate - 0.01, buy No at the first fill acquiring No in [D+3 s, T), plus the taker fee. base_rate =
      per-tour non-completion rate of universe markets with start in [T-60 d, T) that closed before D
      (>= 50 such markets required).
  (b) primary with window [T-24h, T).
  (c) primary split by tour and by Yes fill price bucket (< 0.97, 0.97-0.985, > 0.985).
  (d) calibration: mean pregame Yes price vs completion rate.
  (e) ITF: not run (the 2,000-market fetch budget is spent on the pre-registered ATP/WTA sample).

Usage (every call under the memory cap):
  python -m pmsports.research.h_tennis_completion_no sample
  python -m pmsports.research.h_tennis_completion_no fetch
  python -m pmsports.research.h_tennis_completion_no run dev|holdout
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

from pmsports.research import common as C

SLUG = "tennis_completion_no"
OUT = C.RESEARCH / f"h_{SLUG}"
TR = OUT / "trades"
START = pd.Timestamp("2026-05-09", tz="UTC").timestamp()
SPLIT_TS = pd.Timestamp("2026-07-01", tz="UTC").timestamp()
SEED = 20260919
N_TOTAL = 2000
N_DEV_MAX = 1100
N_ZERO_CHECK = 20
LEAGUES = ("atp", "wta")

# frozen primary parameters
WIN = 6 * 3600        # quoting window [T - WIN, T)
CAP = 200             # max shares per taker fill
REBATE = C.MAKER_REBATE
FETCH_BACK = 48 * 3600
# variant (a)
A_DEC = 600
A_LOOK = 3600
A_LAG = 3
A_MARGIN = 0.01
A_BASE_DAYS = 60
A_BASE_MIN = 50


# ----------------------------------------------------------------------------- universe / sample

def completed_universe() -> pd.DataFrame:
    """One row per ATP/WTA tennis_completed_match market (all, incl. zero volume) with Yes/No tokens."""
    u = C.universe(columns=["condition_id", "token_id", "outcome", "outcome_idx", "payout", "league", "market_type",
                            "game_start_ts", "closed_ts", "event_slug", "market_slug", "volume", "fee_rate"])
    u = u[(u.market_type == "tennis_completed_match") & u.league.isin(LEAGUES)]
    y = u[u.outcome_idx == 0].set_index("condition_id")
    n = u[u.outcome_idx == 1].set_index("condition_id")
    assert (y.outcome == "Yes").all() and (n.outcome == "No").all()
    df = y[["league", "game_start_ts", "closed_ts", "event_slug", "market_slug", "volume", "fee_rate"]].copy()
    df["y_yes"] = y.payout
    df["y_no"] = n.payout.reindex(df.index)
    df["tok_yes"] = y.token_id
    df["tok_no"] = n.token_id.reindex(df.index)
    return df.reset_index()


def build_sample() -> pd.DataFrame:
    u = completed_universe()
    u = u[u.game_start_ts >= START]
    ok = u.y_yes.isin([0, 0.5, 1]) & ((u.y_yes + u.y_no) == 1)
    print(f"ATP/WTA completed-match markets since 2026-05-09: {len(u)}  (bad payout dropped: {(~ok).sum()})")
    u = u[ok].copy()
    u["split"] = np.where(u.game_start_ts < SPLIT_TS, "dev", "holdout")
    u["vpos"] = u.volume > 0
    print(u.groupby(["split", "vpos"]).size())
    rng = np.random.default_rng(SEED)
    u = u.iloc[rng.permutation(len(u))].reset_index(drop=True)          # random order
    zero = pd.concat([u[(u.split == sp) & ~u.vpos].head(N_ZERO_CHECK // 2) for sp in ("dev", "holdout")])
    dev = u[(u.split == "dev") & u.vpos].head(N_DEV_MAX)
    hold = u[(u.split == "holdout") & u.vpos].head(N_TOTAL - len(dev) - len(zero))
    s = pd.concat([dev.assign(role="sample"), hold.assign(role="sample"), zero.assign(role="zero_check")])
    s = s.reset_index(drop=True)
    OUT.mkdir(parents=True, exist_ok=True)
    s.to_parquet(OUT / "sample.parquet", index=False)
    pop = u[u.vpos].groupby("split").size()
    print(f"sample: dev {len(dev)}/{pop['dev']}  holdout {len(hold)}/{pop['holdout']}  zero-volume checks {len(zero)}")
    return s


def sample() -> pd.DataFrame:
    p = OUT / "sample.parquet"
    return pd.read_parquet(p) if p.exists() else build_sample()


# ----------------------------------------------------------------------------- fetch

COLS = ["timestamp", "side", "asset", "outcomeIndex", "price", "size", "transactionHash", "proxyWallet"]


def _fetch_one(cid: str, T: int) -> int:
    from pmsports import polymarket as P
    path = TR / f"{cid}.parquet"
    if path.exists():
        return -1
    rows = P.trades(cid, T - FETCH_BACK, T - 1)           # Data API end is inclusive -> [T-48h, T)
    pd.DataFrame(rows, columns=COLS).to_parquet(path, index=False)
    return len(rows)


def fetch() -> None:
    import pmsports.http as H
    H.HOST_RPS["data-api.polymarket.com"] = 5
    TR.mkdir(parents=True, exist_ok=True)
    s = sample()
    todo = [(r.condition_id, int(r.game_start_ts)) for r in s.itertuples()
            if not (TR / f"{r.condition_id}.parquet").exists()]
    print(f"fetch: {len(s)} markets, {len(todo)} to fetch", flush=True)
    done = err = 0
    with ThreadPoolExecutor(4) as ex:
        futs = {ex.submit(_fetch_one, c, t): c for c, t in todo}
        for f in as_completed(futs):
            try:
                f.result()
                done += 1
            except Exception as e:  # noqa: BLE001
                err += 1
                print("ERR", futs[f], e, flush=True)
            if (done + err) % 100 == 0:
                print(f"  {done + err}/{len(todo)} (errors {err})", flush=True)
    print(f"fetched {done}, errors {err}")


# ----------------------------------------------------------------------------- fills

def load_fills(s: pd.DataFrame) -> pd.DataFrame:
    """Merged taker fills for the sample: s = side acquired (0 Yes, 1 No), q = price paid for s."""
    parts, stats = [], dict(rows=0, oi_mismatch=0, unknown_token=0, missing_file=0)
    for r in s.itertuples():
        p = TR / f"{r.condition_id}.parquet"
        if not p.exists():
            stats["missing_file"] += 1
            continue
        d = pd.read_parquet(p)
        d = d[d["size"].astype(float) > 0]
        if len(d) == 0:
            continue
        tok = {r.tok_yes: 0, r.tok_no: 1}
        idx = d.asset.map(tok)
        unk = idx.isna().to_numpy()
        idx = np.where(unk, d.outcomeIndex, idx).astype(int)
        stats["rows"] += len(d)
        stats["unknown_token"] += int(unk.sum())
        stats["oi_mismatch"] += int((idx != d.outcomeIndex.astype(int).to_numpy()).sum())
        buy = (d.side == "BUY").to_numpy()
        px = d.price.astype(float).to_numpy()
        parts.append(pd.DataFrame({"condition_id": r.condition_id, "ts": d.timestamp.astype(np.int64).to_numpy(),
                                   "s": np.where(buy, idx, 1 - idx), "q": np.round(np.where(buy, px, 1 - px), 6),
                                   "size": d["size"].astype(float).to_numpy(), "tx": d.transactionHash.to_numpy(),
                                   "w": d.proxyWallet.to_numpy()}))
    print("fill rows:", stats)
    f = pd.concat(parts, ignore_index=True)
    # one taker order at one price level (split across several makers) -> one fill
    f = (f.groupby(["condition_id", "tx", "w", "s", "q"], sort=False)
          .agg(ts=("ts", "min"), size=("size", "sum")).reset_index())
    f = f.sort_values(["condition_id", "ts", "tx"], kind="stable").reset_index(drop=True)
    meta = s.set_index("condition_id")
    f["T"] = f.condition_id.map(meta.game_start_ts).astype(np.int64)
    f["y_yes"] = f.condition_id.map(meta.y_yes)
    f["fee_rate"] = f.condition_id.map(meta.fee_rate)
    f["q_yes"] = np.where(f.s == 0, f.q, 1 - f.q)             # Yes-converted price
    return f


# ----------------------------------------------------------------------------- statistics

def date_of(ts) -> np.ndarray:
    return pd.to_datetime(np.asarray(ts, dtype=float), unit="s", utc=True).strftime("%Y-%m-%d").to_numpy()


def maker_markets(f: pd.DataFrame, s: pd.DataFrame, win=WIN, shift=0.0, qmask=None) -> pd.DataFrame:
    """Per-market maker-sell-Yes results over Yes-acquiring fills in [T - win, T)."""
    g = f[(f.s == 0) & (f.ts >= f["T"] - win) & (f.ts < f["T"])].copy()
    if qmask is not None:
        g = g[qmask(g.q)]
    a = g.q - shift                                              # our sale price
    g["sh"] = np.minimum(g["size"], CAP)
    g["pnl_sh"] = a - g.y_yes + REBATE * g.fee_rate * a * (1 - a)
    g["pnl"] = g.pnl_sh * g.sh
    g["cap"] = (1 - a) * g.sh
    g["qsh"] = g.q * g.sh
    m = g.groupby("condition_id").agg(n_fills=("sh", "size"), shares=("sh", "sum"), pnl=("pnl", "sum"),
                                      capital=("cap", "sum"), qsh=("qsh", "sum"))
    m["c_share"] = m.pnl / m.shares
    m["q_mean"] = m.qsh / m.shares
    meta = s.set_index("condition_id")
    m["T"] = meta.game_start_ts.reindex(m.index)
    m["date"] = date_of(m["T"])
    m["league"] = meta.league.reindex(m.index)
    m["y_yes"] = meta.y_yes.reindex(m.index)
    m["closed_ts"] = meta.closed_ts.reindex(m.index)
    return m.reset_index()


def summarize_maker(m: pd.DataFrame, n_days: float | None = None) -> dict:
    if len(m) == 0:
        return dict(markets=0)
    ew, lo, hi = C.cluster_ci(m.c_share, m.date)
    sw, slo, shi = C.cluster_ci(m.c_share, m.date, weights=m.shares)
    roi, rlo, rhi = C.cluster_ci(m.pnl / m.capital, m.date, weights=m.capital)
    d = dict(markets=int(len(m)), dates=int(m.date.nunique()), fills=int(m.n_fills.sum()),
             shares=float(m.shares.sum()), capital=float(m.capital.sum()), pnl=float(m.pnl.sum()),
             c_share_ew=100 * ew, ci_lo=100 * lo, ci_hi=100 * hi,
             c_share_sw=100 * sw, sw_lo=100 * slo, sw_hi=100 * shi,
             roi=roi, roi_lo=rlo, roi_hi=rhi,
             mean_q=float((m.q_mean * m.shares).sum() / m.shares.sum()),
             not_completed_rate=float((m.y_yes < 1).mean()), ew_mean_q=float(m.q_mean.mean()))
    if n_days:
        d["capital_per_day"] = d["capital"] / n_days
        d["shares_per_day"] = d["shares"] / n_days
    return d


def fmt_maker(d: dict) -> str:
    if not d.get("markets"):
        return "no markets"
    return (f"mkts {d['markets']:>4} fills {d['fills']:>5} sh {d['shares']:>9,.0f} cap ${d['capital']:>8,.0f} "
            f"pnl ${d['pnl']:>8,.0f} | EW {d['c_share_ew']:+.2f}c [{d['ci_lo']:+.2f},{d['ci_hi']:+.2f}] "
            f"SW {d['c_share_sw']:+.2f}c [{d['sw_lo']:+.2f},{d['sw_hi']:+.2f}] "
            f"ROI {d['roi']:+.1%} [{d['roi_lo']:+.1%},{d['roi_hi']:+.1%}] "
            f"meanq {d['mean_q']:.3f} notcompl {d['not_completed_rate']:.1%}")


# ----------------------------------------------------------------------------- variant (a)

def base_rates(u: pd.DataFrame, s: pd.DataFrame) -> pd.Series:
    """Per sample market: non-completion rate of same-tour markets that started in [T-60d, T) and closed
    before the decision time T-10min (no look-ahead)."""
    out = {}
    for lg, g in u.groupby("league"):
        g = g.sort_values("game_start_ts")
        st, cl, no = g.game_start_ts.to_numpy(), g.closed_ts.to_numpy(), (g.y_yes < 1).to_numpy()
        for r in s[s.league == lg].itertuples():
            T = r.game_start_ts
            msk = (st >= T - A_BASE_DAYS * 86400) & (st < T) & (cl < T - A_DEC)
            out[r.condition_id] = (no[msk].mean(), int(msk.sum())) if msk.any() else (np.nan, 0)
    return pd.Series(out)


def variant_a(f: pd.DataFrame, s: pd.DataFrame, u: pd.DataFrame, slip=0.0) -> tuple[dict, pd.DataFrame]:
    br = base_rates(u, s)
    rows = []
    for cid, g in f.groupby("condition_id", sort=False):
        T = int(g["T"].iloc[0])
        D = T - A_DEC
        look = g[(g.ts >= T - A_LOOK) & (g.ts < D)]
        if len(look) == 0:
            continue
        rate, nb = br.get(cid, (np.nan, 0))
        if nb < A_BASE_MIN:
            continue
        med_no = float(np.median(1 - look.q_yes))
        if not med_no < rate - A_MARGIN:
            continue
        ex = g[(g.s == 1) & (g.ts >= D + A_LAG) & (g.ts < T)]
        if len(ex) == 0:
            rows.append(dict(condition_id=cid, signal=True, filled=False, med_no=med_no, base=rate))
            continue
        e = ex.iloc[0]
        c = min(float(e.q) + slip, 0.999)
        fee = C.taker_fee(1.0, c, e.fee_rate)
        y_no = 1 - e.y_yes
        rows.append(dict(condition_id=cid, signal=True, filled=True, med_no=med_no, base=rate, q=c, fee=float(fee),
                         y_no=y_no, roi=(y_no - c - fee) / (c + fee), size=float(e["size"]), T=T))
    r = pd.DataFrame(rows)
    if len(r) == 0 or not r.get("filled", pd.Series(dtype=bool)).any():
        return dict(signals=int(len(r)), bets=0), r
    b = r[r.filled].copy()
    b["date"] = date_of(b["T"])
    roi, lo, hi = C.cluster_ci(b.roi, b.date)
    cost = b.q + b.fee
    return dict(signals=int(len(r)), bets=int(len(b)), roi=roi, ci_lo=lo, ci_hi=hi, mean_q_no=float(b.q.mean()),
                mean_base=float(b.base.mean()), no_rate=float((b.y_no > 0.5).mean()),
                pnl_per_bet_c=float(100 * (b.y_no - cost).mean()),
                capital_1share=float(cost.sum()), med_size=float(b["size"].median())), r


# ----------------------------------------------------------------------------- post-hoc (x): reverse

X_LO, X_HI = 0.90, 0.97


def variant_x(f: pd.DataFrame, slip=0.0) -> tuple[dict, pd.DataFrame]:
    """POST-HOC, suggested by the DEV calibration table (not pre-registered; frozen before the holdout run):
    taker BUYS Yes. Signal t0 = first fill in [T-6h, T) whose Yes-converted price is in [0.90, 0.97).
    Entry = first fill acquiring Yes with ts in [t0+3 s, T), at its price (+slip) plus the taker fee.
    One bet per market; ROI per $1; CI by match date."""
    rows = []
    g0 = f[(f.ts >= f["T"] - WIN) & (f.ts < f["T"])]
    for cid, g in g0.groupby("condition_id", sort=False):
        sig = g[(g.q_yes >= X_LO - 1e-9) & (g.q_yes < X_HI - 1e-9)]
        if len(sig) == 0:
            continue
        t0 = int(sig.ts.iloc[0])
        ex = g[(g.s == 0) & (g.ts >= t0 + A_LAG)]
        if len(ex) == 0:
            rows.append(dict(condition_id=cid, filled=False))
            continue
        e = ex.iloc[0]
        c = min(float(e.q) + slip, 0.999)
        fee = float(C.taker_fee(1.0, c, e.fee_rate))
        rows.append(dict(condition_id=cid, filled=True, q=c, fee=fee, y_yes=float(e.y_yes), T=int(e["T"]),
                         roi=(e.y_yes - c - fee) / (c + fee), size=float(e["size"]), wait=int(e.ts) - t0))
    r = pd.DataFrame(rows)
    if len(r) == 0 or not r.filled.any():
        return dict(signals=int(len(r)), bets=0), r
    b = r[r.filled].copy()
    b["date"] = date_of(b["T"])
    roi, lo, hi = C.cluster_ci(b.roi, b.date)
    return dict(signals=int(len(r)), bets=int(len(b)), roi=roi, ci_lo=lo, ci_hi=hi, mean_q=float(b.q.mean()),
                completion=float(b.y_yes.mean()), pnl_per_bet_c=float(100 * (b.y_yes - b.q - b.fee).mean()),
                med_size=float(b["size"].median()), capital_1share=float((b.q + b.fee).sum())), r


# ----------------------------------------------------------------------------- variant (d)

def calibration(f: pd.DataFrame, s: pd.DataFrame, win=WIN) -> pd.DataFrame:
    """Per market with any fill in [T-win, T): last Yes-converted price and VWAP vs completion."""
    g = f[(f.ts >= f["T"] - win) & (f.ts < f["T"])].copy()
    g["qv"] = g.q_yes * g["size"]
    last = g.groupby("condition_id").q_yes.last()
    vw = g.groupby("condition_id").qv.sum() / g.groupby("condition_id")["size"].sum()
    m = pd.DataFrame({"last": last, "vwap": vw})
    m["y_yes"] = s.set_index("condition_id").y_yes.reindex(m.index)
    m["date"] = date_of(s.set_index("condition_id").game_start_ts.reindex(m.index))
    return m


def calib_table(m: pd.DataFrame, col="last") -> list[dict]:
    bins = [0, 0.5, 0.9, 0.95, 0.97, 0.985, 0.995, 1.0001]
    m = m.assign(b=pd.cut(m[col], bins, right=False))
    out = []
    for b, g in m.groupby("b", observed=True):
        gap, lo, hi = C.cluster_ci(g[col] - g.y_yes, g.date)
        out.append(dict(bucket=str(b), markets=int(len(g)), mean_price=float(g[col].mean()),
                        completion=float(g.y_yes.mean()), gap_c=100 * gap, gap_lo=100 * lo, gap_hi=100 * hi))
    gap, lo, hi = C.cluster_ci(m[col] - m.y_yes, m.date)
    out.append(dict(bucket="all", markets=int(len(m)), mean_price=float(m[col].mean()),
                    completion=float(m.y_yes.mean()), gap_c=100 * gap, gap_lo=100 * lo, gap_hi=100 * hi))
    return out


# ----------------------------------------------------------------------------- run

def run(split: str) -> dict:
    s_all = sample()
    zc = s_all[s_all.role == "zero_check"]
    s = s_all[(s_all.role == "sample") & (s_all.split == split)].reset_index(drop=True)
    f = load_fills(s)
    res: dict = {"split": split, "sampled_markets": int(len(s))}

    # sanity: zero-volume markets must be empty; per-market fill presence
    zn = [len(pd.read_parquet(TR / f"{c}.parquet")) for c in zc.condition_id if (TR / f"{c}.parquet").exists()]
    res["zero_volume_check"] = dict(fetched=len(zn), nonempty=int(sum(n > 0 for n in zn)))
    print("zero-volume check:", res["zero_volume_check"])
    in48 = f.groupby("condition_id").size()
    res["markets_with_any_fill_48h"] = int(len(in48))
    res["population_vpos"] = int(((completed_universe().pipe(lambda u: u[(u.game_start_ts >= START) & (u.volume > 0)])
                                   .assign(sp=lambda u: np.where(u.game_start_ts < SPLIT_TS, "dev", "holdout"))
                                   .sp == split)).sum())
    days = (s.game_start_ts.max() - s.game_start_ts.min()) / 86400 + 1
    res["days"] = days
    print(f"{split}: {len(s)} sampled markets, {len(in48)} with any fill in [T-48h,T), {days:.0f} days;"
          f" fills {len(f):,}")

    # timing sanity
    g = f[(f.ts >= f["T"] - WIN) & (f.ts < f["T"])]
    res["fills_in_window"] = int(len(g))
    res["fills_after_close"] = int((g.ts > g.condition_id.map(s.set_index("condition_id").closed_ts)).sum())
    res["yes_acq_share"] = float((g.s == 0).mean())

    # PRIMARY
    m = maker_markets(f, s)
    res["primary"] = summarize_maker(m, days)
    res["primary_plus1c"] = summarize_maker(maker_markets(f, s, shift=0.01), days)
    print("PRIMARY      ", fmt_maker(res["primary"]))
    print("PRIMARY +1c  ", fmt_maker(res["primary_plus1c"]))
    m.to_parquet(OUT / f"maker_markets_{split}.parquet", index=False)

    # outcome decomposition of the primary
    dec = []
    for yv, gg in m.groupby("y_yes"):
        dec.append(dict(y_yes=float(yv), markets=int(len(gg)), mean_q=float(gg.q_mean.mean()),
                        c_share_mean=float(100 * gg.c_share.mean())))
    res["primary_by_outcome"] = dec
    # diagnostic: markets whose close came before T + 30 min (resolved before it could have been played)
    early = m.closed_ts < m["T"] + 1800
    res["diag_closed_before_T30"] = dict(markets=int(early.sum()), not_completed=float((m[early].y_yes < 1).mean())
                                         if early.any() else None)
    res["diag_excl_early_close"] = summarize_maker(m[~early], days)
    print("diag excl. markets closed < T+30m:", fmt_maker(res["diag_excl_early_close"]))

    # (b) 24h window
    res["b_window24h"] = summarize_maker(maker_markets(f, s, win=24 * 3600), days)
    print("(b) [T-24h,T)", fmt_maker(res["b_window24h"]))

    # (c) by tour, by price bucket
    res["c_by_tour"] = {lg: summarize_maker(m[m.league == lg], days) for lg in LEAGUES}
    for lg, d in res["c_by_tour"].items():
        print(f"(c) {lg:<9}", fmt_maker(d))
    buckets = {"q<0.97": lambda q: q < 0.97 - 1e-9, "0.97<=q<=0.985": lambda q: (q >= 0.97 - 1e-9) & (q <= 0.985 + 1e-9),
               "q>0.985": lambda q: q > 0.985 + 1e-9, "q>=0.97 (union)": lambda q: q >= 0.97 - 1e-9}
    res["c_by_bucket"] = {k: summarize_maker(maker_markets(f, s, qmask=fn), days) for k, fn in buckets.items()}
    for k, d in res["c_by_bucket"].items():
        print(f"(c) {k:<14}", fmt_maker(d))
    res["c_by_bucket_plus1c"] = {k: summarize_maker(maker_markets(f, s, shift=0.01, qmask=fn), days)
                                 for k, fn in buckets.items()}

    # (d) calibration
    cal = calibration(f, s)
    res["d_calibration_last"] = calib_table(cal, "last")
    res["d_calibration_vwap"] = calib_table(cal, "vwap")
    print("(d) calibration (last Yes price in [T-6h,T) vs completion):")
    for r in res["d_calibration_last"]:
        print(f"    {r['bucket']:<16} n={r['markets']:>4} price {r['mean_price']:.3f} completion {r['completion']:.3f}"
              f" gap {r['gap_c']:+.2f}c [{r['gap_lo']:+.2f},{r['gap_hi']:+.2f}]")

    # (a) taker No
    u = completed_universe()
    a, arows = variant_a(f, s, u)
    a1, _ = variant_a(f, s, u, slip=0.01)
    res["a_taker_no"] = a
    res["a_taker_no_plus1c"] = a1
    arows.to_parquet(OUT / f"variant_a_{split}.parquet", index=False)
    print("(a) taker No:", json.dumps(a, default=float))
    print("(a) +1c     :", json.dumps(a1, default=float))

    # (x) post-hoc reverse rule (taker buys Yes in [0.90, 0.97)), frozen from DEV before the holdout run
    x, xrows = variant_x(f)
    x1, _ = variant_x(f, slip=0.01)
    res["x_posthoc_taker_yes"] = x
    res["x_posthoc_taker_yes_plus1c"] = x1
    xrows.to_parquet(OUT / f"variant_x_{split}.parquet", index=False)
    print("(x) post-hoc taker Yes [0.90,0.97):", json.dumps(x, default=float))
    print("(x) +1c                           :", json.dumps(x1, default=float))

    # capacity (scaled from the sample to the population of volume>0 markets in the split)
    scale = res["population_vpos"] / max(len(s), 1)
    p = res["primary"]
    if p.get("markets"):
        res["capacity"] = dict(scale=scale, capital_per_day_pop=p["capital"] * scale / days,
                               shares_per_day_pop=p["shares"] * scale / days,
                               pnl_per_day_pop=p["pnl"] * scale / days,
                               notional_sold_per_day_pop=float((m.qsh.sum()) * scale / days))
        print("capacity:", json.dumps(res["capacity"], default=float))

    with open(OUT / f"results_{split}.json", "w") as fh:
        json.dump(res, fh, indent=1, default=float)
    return res


if __name__ == "__main__":
    cmd = sys.argv[1:]
    if cmd == ["sample"]:
        build_sample()
    elif cmd == ["fetch"]:
        fetch()
    elif len(cmd) == 2 and cmd[0] == "run":
        run(cmd[1])
    else:
        print(__doc__)
