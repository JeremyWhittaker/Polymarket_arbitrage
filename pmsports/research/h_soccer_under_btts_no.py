"""Hypothesis `soccer_under_btts_no`: Yes/Over action bias -- sell Over 2.5 and BTTS-Yes to retail pregame.

Mechanism claimed: retail takers default to 'Over' / 'Yes' (fans root for goals). In soccer moneylines the
neg-risk conversion link lets arbitrageurs absorb a Yes premium; O/U 2.5 and BTTS are plain binary markets
with no such link and thin market making, so an Over/Yes premium could survive. Counterparty: the retail
taker clicking Over / Yes.

Rule as implemented (pre-registered; parameters frozen before the holdout run):
  SAMPLE   Universe (C.universe()) soccer markets whose slug is exactly '<match>-total-2pt5' (market_type
           'totals') or '<match>-btts' (market_type 'both_teams_to_score'), <match> = '<league>-<a>-<b>-<date>'.
           Leagues: premier-league, la-liga, bundesliga, serie-a, ligue-1, ucl, uel, europa-conference-league,
           efl-championship, ere, primeira-liga, mls, brazil-serie-a, mex, tur, soccer-fifwc, each optionally
           followed by a season suffix '-20xx' (so 'la-liga-2', 'bundesliga-2', 'tur2-games' are NOT included:
           those are second divisions, not season suffixes). Valid payouts (y0 + y1 == 1).
           game_start in [2025-10-01, latest resolved]. No volume filter. Seeded random sample of 1,000 markets
           per split: DEV = start < 2026-07-01, HOLDOUT = start >= 2026-07-01.
  START    T = min(market game_start_ts, same-match moneyline game_start_ts). (September 2025 O/U markets carry a
           start 4 h after the real kickoff -- excluded anyway by the 2025-10-01 bound -- and ~0.5% of later
           markets disagree with their moneyline; taking the earlier start guarantees no in-play fill counts.)
  FETCH    Data API taker fills in [T-24h, T) (pmsports.polymarket.trades, HOST_RPS=5). Side index from the token
           id (universe token -> outcome_idx), the API outcomeIndex only as fallback. A taker SELL of token k is
           acquiring the other token at 1 - price.
  TICKETS  One taker ticket = (transactionHash, side acquired): shares summed, price = share-weighted VWAP.
           (One maker at one level cannot be filled more than once by the same taker order.)
  PRIMARY  (maker selling Over/Yes) for every pregame taker ticket acquiring Over (O/U) or Yes (BTTS) we are the
           maker on the other side for sh = min(size, 100) shares at the taker's price q:
             P&L per share = q - y_over + 0.15 * fee_rate * q * (1 - q)          (no maker fee, 15% rebate)
           Per market: share-weighted mean P&L per share. Primary statistic = equal-weight mean across markets
           with >= 1 such ticket, 95% CI = C.cluster_ci by match (O/U and BTTS of a match share a cluster).
           ROI per $ = sum_m pnl_m / sum_m notional_m with notional per share = 1 - q (selling Over at q is
           buying Under at 1 - q), i.e. the same equal-market weighting, CI by match.
  +1c      maker sensitivity: every fill 1c worse (P&L - 0.01, notional + 0.01).
Variants (allowed, reported, not used for the verdict):
  (a) taker: at T-10 min buy Under / BTTS-No at the first taker ticket acquiring that side with
      ts in [T-600+3, T); taker fee at the market's fee_rate; with and without +1c; one bet per market.
  (b) symmetric control: maker selling Under/No (same rule, other side).
  (c) calibration: per market median implied Over/Yes price from all pregame prints in [T-60m, T)
      (fallback [T-24h, T)) vs hit rate, by month / split / type.
  (d) primary restricted to taker tickets < $100 notional (size * q).
  (e) share-weighted (every captured share weighted equally) instead of equal-weight by market.
Diagnostics (labelled as such): fee regime, league, hours-to-start, Over-price bucket, back-of-queue stress,
share of taker flow that is Over/Yes, raw taker P&L of Over/Yes buyers.

RESULT (report: reports/research/soccer_under_btts_no.md): DEAD. Primary ROI DEV +3.74% [-3.69, +11.01],
HOLDOUT -2.32% [-9.65, +4.99] (-4.47% at +1c). Takers do skew to Over/Yes (61% DEV, 73% HOLDOUT of tickets) but the
implied Over/Yes price matches the hit rate within ~1c.

Usage (every call under the memory cap, from the repo root):
  systemd-run --user --scope -q -p MemoryMax=4G -p MemorySwapMax=0 .venv/bin/python \
      -m pmsports.research.h_soccer_under_btts_no sample | fetch dev|holdout | run dev|holdout
"""
from __future__ import annotations

import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

from pmsports.research import common as C

SLUG = "soccer_under_btts_no"
OUT = C.RESEARCH / f"h_{SLUG}"
TR = OUT / "trades"
REUSE = C.RESEARCH / "h_soccer_ou_vs_ml_pregame"          # optional read-only cache (identical window only)
SPLIT_TS = pd.Timestamp("2026-07-01", tz="UTC").timestamp()
START_TS = pd.Timestamp("2025-10-01", tz="UTC").timestamp()
SEED = 20260919
N_PER_SPLIT = 1000
LEAGUES = ["premier-league", "la-liga", "bundesliga", "serie-a", "ligue-1", "ucl", "uel", "europa-conference-league",
           "efl-championship", "ere", "primeira-liga", "mls", "brazil-serie-a", "mex", "tur", "soccer-fifwc"]
LEAGUE_RE = re.compile(r"^(" + "|".join(map(re.escape, LEAGUES)) + r")(-20\d\d)?$")

# frozen primary parameters
K = 100                      # max shares we can be filled per taker ticket
REBATE = C.MAKER_REBATE      # 0.15
WIN = 86400                  # pregame window [T-24h, T)
TAKER_DECISION = 600         # variant (a): decide at T-10 min
LAG = 3                      # enter at the first print >= decision + 3 s
EPS = 1e-6


# ----------------------------------------------------------------------------- sample

def build_sample() -> pd.DataFrame:
    u = C.universe(columns=["condition_id", "token_id", "outcome_idx", "outcome", "payout", "family", "league",
                            "market_type", "game_start_ts", "closed_ts", "event_slug", "market_slug", "fee_rate"])
    u = u[u.family == "soccer"]
    u["match"] = u.market_slug.str.extract(r"^(.*?-\d{4}-\d{2}-\d{2})")[0]
    # same-match moneyline start (moneylines are 'moneyline' or legacy 'other' Yes/No legs without total/btts/spread)
    ml = u[u.market_type.isin(["moneyline", "other"]) & ~u.market_slug.str.contains("total|btts|spread|corner")]
    ml_start = ml.groupby("match").game_start_ts.min().rename("ml_start")
    x = u[((u.market_type == "totals") & (u.market_slug == u.match + "-total-2pt5"))
          | ((u.market_type == "both_teams_to_score") & (u.market_slug == u.match + "-btts"))]
    x = x[x.league.map(lambda s: bool(LEAGUE_RE.match(s)))]
    pay = x.pivot_table(index="condition_id", columns="outcome_idx", values="payout", aggfunc="first")
    outs = x.pivot_table(index="condition_id", columns="outcome_idx", values="outcome", aggfunc="first")
    meta = x.drop_duplicates("condition_id").set_index("condition_id")[
        ["match", "league", "market_type", "event_slug", "market_slug", "game_start_ts", "closed_ts", "fee_rate"]]
    s = meta.join(pay.rename(columns={0: "y0", 1: "y1"})).join(outs.rename(columns={0: "o0", 1: "o1"}))
    s = s.reset_index().join(ml_start, on="match")
    s["mtype"] = np.where(s.market_type == "totals", "ou25", "btts")
    # index of the Over / Yes outcome, from the outcome names (never assumed)
    over_name = np.where(s.mtype == "ou25", "Over", "Yes")
    s["ov"] = np.where(s.o0 == over_name, 0, np.where(s.o1 == over_name, 1, -1))
    s["T"] = np.fmin(s.game_start_ts, s.ml_start.fillna(np.inf))
    ok = (s.y0.isin([0, 0.5, 1]) & s.y1.isin([0, 0.5, 1]) & ((s.y0 + s.y1) == 1) & s.game_start_ts.notna()
          & (s.ov >= 0))
    print(f"candidates {len(s)}  dropped (bad payout/start/outcome names) {(~ok).sum()}")
    s = s[ok & (s.game_start_ts >= START_TS)].copy()
    s["T_shift_h"] = (s.game_start_ts - s["T"]) / 3600
    print(f"T moved earlier than own start (moneyline start earlier): {(s.T_shift_h > 0).sum()} markets")
    s["split"] = np.where(s.game_start_ts < SPLIT_TS, "dev", "holdout")
    s["fee_rate"] = s.fee_rate.fillna(0.0)
    parts = []
    for sp, g in s.groupby("split"):
        print(f"  {sp}: {len(g)} candidate markets ({g.mtype.value_counts().to_dict()}), "
              f"{g.match.nunique()} matches")
        parts.append(g.sample(n=min(N_PER_SPLIT, len(g)), random_state=SEED))
    out = pd.concat(parts).reset_index(drop=True)
    OUT.mkdir(parents=True, exist_ok=True)
    out.to_parquet(OUT / "sample.parquet", index=False)
    s.drop(columns=[]).to_parquet(OUT / "candidates.parquet", index=False)   # for capacity (schedule only)
    print(out.groupby(["split", "mtype"]).size())
    return out


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
    rows = P.trades(cid, T - WIN, T - 1)             # inclusive end: [T-24h, T)
    df = pd.DataFrame(rows, columns=COLS)
    df.to_parquet(path, index=False)
    return len(df)


def fetch(split: str) -> None:
    import pmsports.http as H
    H.HOST_RPS["data-api.polymarket.com"] = 5
    TR.mkdir(parents=True, exist_ok=True)
    s = sample()
    s = s[s.split == split]
    todo = [(r.condition_id, int(r.T)) for r in s.itertuples() if not (TR / f"{r.condition_id}.parquet").exists()]
    print(f"fetch {split}: {len(s)} markets, {len(todo)} to fetch", flush=True)
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


# ----------------------------------------------------------------------------- load

def token_map(cids) -> dict:
    u = C.universe(columns=["condition_id", "token_id", "outcome_idx"])
    u = u[u.condition_id.isin(set(cids))]
    return dict(zip(u.token_id, u.outcome_idx.astype(int)))


def load_tickets(s: pd.DataFrame) -> pd.DataFrame:
    """Taker tickets: one row per (market, tx, side acquired). s = side acquired, q = VWAP paid for s."""
    tok = token_map(s.condition_id)
    meta = s.set_index("condition_id")
    parts, n_rows, n_mis, n_notok, n_px = [], 0, 0, 0, 0
    for cid in s.condition_id:
        p = TR / f"{cid}.parquet"
        if not p.exists():
            continue
        d = pd.read_parquet(p)
        d = d[d["size"].astype(float) > 0]
        if len(d) == 0:
            continue
        T = meta.at[cid, "T"]
        d = d[(d.timestamp >= T - WIN) & (d.timestamp < T)]           # strictly pregame
        if len(d) == 0:
            continue
        idx = d.asset.map(tok)
        bad = idx.isna().to_numpy()
        idx = np.where(bad, d.outcomeIndex, idx).astype(int)
        buy = (d.side == "BUY").to_numpy()
        px = d.price.astype(float).to_numpy()
        f = pd.DataFrame({"ts": d.timestamp.astype(np.int64).to_numpy(), "s": np.where(buy, idx, 1 - idx),
                          "q": np.where(buy, px, 1 - px), "size": d["size"].astype(float).to_numpy(),
                          "tx": d.transactionHash.to_numpy()})
        n_rows += len(f)
        n_mis += int((idx != d.outcomeIndex.astype(int).to_numpy()).sum())
        n_notok += int(bad.sum())
        f["qs"] = f.q * f["size"]
        g = f.groupby(["tx", "s"], sort=False).agg(ts=("ts", "min"), size=("size", "sum"), qs=("qs", "sum"),
                                                  qmin=("q", "min"), qmax=("q", "max"), n=("q", "size"))
        g = g.reset_index()
        n_px += int((g.qmax - g.qmin > EPS).sum())
        g["q"] = g.qs / g["size"]
        parts.append(g.drop(columns=["qs"]).assign(condition_id=cid))
    t = pd.concat(parts, ignore_index=True)
    t = t.join(meta[["match", "mtype", "league", "split", "T", "fee_rate", "ov", "y0", "y1"]], on="condition_id")
    t["y"] = np.where(t.s == 0, t.y0, t.y1)
    t["is_over"] = t.s == t.ov
    t["hrs"] = (t["T"] - t.ts) / 3600
    t["usd"] = t["size"] * t.q
    t = t.sort_values(["condition_id", "ts", "tx"], kind="stable").reset_index(drop=True)
    print(f"raw pregame fill rows {n_rows:,}; outcomeIndex != token {n_mis / max(n_rows, 1):.2%}; "
          f"unknown token {n_notok / max(n_rows, 1):.2%}; tickets {len(t):,} (multi-price tickets {n_px:,})")
    return t


# ----------------------------------------------------------------------------- statistics

def maker_side(t: pd.DataFrame, over: bool, k=K, rebate=REBATE, shift=0.0) -> pd.DataFrame:
    """Per-fill maker P&L when a taker acquires Over/Yes (over=True) or Under/No (over=False) and we sell it."""
    f = t[t.is_over == over].copy()
    f["sh"] = np.minimum(f["size"], k)
    f["pnl"] = f.q - f.y + rebate * f.fee_rate * f.q * (1 - f.q) - shift
    f["notional"] = 1 - f.q + shift
    return f


def per_market(f: pd.DataFrame) -> pd.DataFrame:
    f = f.assign(wp=f.sh * f.pnl, wn=f.sh * f.notional)
    g = f.groupby("condition_id").agg(sh=("sh", "sum"), wp=("wp", "sum"), wn=("wn", "sum"), n=("sh", "size"),
                                      match=("match", "first"), mtype=("mtype", "first"),
                                      league=("league", "first"), fee_rate=("fee_rate", "first"))
    g["pnl"] = g.wp / g.sh           # share-weighted mean P&L per share in the market
    g["notl"] = g.wn / g.sh          # mean notional per share
    return g


def summ_maker(f: pd.DataFrame, weight="market") -> dict:
    if len(f) == 0:
        return dict(markets=0)
    g = per_market(f)
    if weight == "market":
        m, lo, hi = C.cluster_ci(g.pnl, g.match)
        r, rlo, rhi = C.cluster_ci(g.pnl / g.notl, g.match, weights=g.notl)
    else:                                                     # (e) share-weighted
        m, lo, hi = C.cluster_ci(f.pnl, f.match, weights=f.sh)
        r, rlo, rhi = C.cluster_ci(f.pnl / f.notional, f.match, weights=f.sh * f.notional)
    return dict(markets=int(len(g)), matches=int(g.match.nunique()), fills=int(len(f)), shares=float(f.sh.sum()),
                dollars=float((f.sh * f.notional).sum()), pnl_usd=float((f.sh * f.pnl).sum()),
                c_per_share=100 * m, ci_lo=100 * lo, ci_hi=100 * hi, roi=r, roi_lo=rlo, roi_hi=rhi)


def fmt(d: dict) -> str:
    if not d.get("markets"):
        return "no fills"
    return (f"mkts {d['markets']:>4} matches {d['matches']:>4} fills {d['fills']:>6,} sh {d['shares']:>9,.0f} "
            f"${d['dollars']:>8,.0f} P&L ${d['pnl_usd']:>+8,.0f}  {d['c_per_share']:+.2f}c/sh "
            f"[{d['ci_lo']:+.2f},{d['ci_hi']:+.2f}]  ROI {100 * d['roi']:+.2f}% "
            f"[{100 * d['roi_lo']:+.2f},{100 * d['roi_hi']:+.2f}]")


def taker_under(t: pd.DataFrame, s: pd.DataFrame, slip=0.0) -> pd.DataFrame:
    """(a) at T-10 min buy Under/No at the first taker ticket acquiring Under/No with ts in [T-600+3, T)."""
    u = t[(~t.is_over) & (t.ts >= t["T"] - TAKER_DECISION + LAG) & (t.ts < t["T"])]
    b = u.sort_values(["condition_id", "ts", "tx"], kind="stable").groupby("condition_id").head(1).copy()
    b["roi"] = C.taker_roi(b.q, b.y, b.fee_rate, slip=slip)
    b["cost"] = np.clip(b.q + slip, 0.001, 0.999) + C.taker_fee(1.0, np.clip(b.q + slip, 0.001, 0.999), b.fee_rate)
    return b


def summ_taker(b: pd.DataFrame, s_total: int) -> dict:
    if len(b) == 0:
        return dict(bets=0)
    r, lo, hi = C.cluster_ci(b.roi, b.match)
    return dict(bets=int(len(b)), of_markets=int(s_total), matches=int(b.match.nunique()), roi=r, ci_lo=lo,
                ci_hi=hi, mean_price=float(b.q.mean()), win_rate=float(b.y.mean()))


def calibration(t: pd.DataFrame, s: pd.DataFrame) -> pd.DataFrame:
    """(c) per market median implied Over/Yes price from all pregame prints in [T-60m, T) (fallback 24 h)."""
    x = t.assign(p_over=np.where(t.is_over, t.q, 1 - t.q))
    last = x[x.ts >= x["T"] - 3600].groupby("condition_id").p_over.median()
    allp = x.groupby("condition_id").p_over.median()
    p = last.reindex(allp.index).fillna(allp).rename("p_over")
    m = s.set_index("condition_id").join(p, how="inner")
    m["y_over"] = np.where(m.ov == 0, m.y0, m.y1)
    m["month"] = pd.to_datetime(m["T"], unit="s").dt.strftime("%Y-%m")
    return m


def calib_rows(m: pd.DataFrame, by: str) -> list[dict]:
    rows = []
    for k, g in m.groupby(by):
        d, lo, hi = C.cluster_ci(g.y_over - g.p_over, g.match)
        rows.append(dict(key=str(k), markets=int(len(g)), mean_p=float(g.p_over.mean()),
                         median_p=float(g.p_over.median()), hit=float(g.y_over.mean()), hit_minus_p=d,
                         ci_lo=lo, ci_hi=hi))
    return rows


def back_of_queue(f: pd.DataFrame, t: pd.DataFrame, horizon=60) -> pd.DataFrame:
    """Diagnostic: keep a maker fill only if a LATER taker ticket acquiring the same side within `horizon` s traded
    at a strictly higher price (our level was traded through, so even a back-of-queue order would have filled)."""
    keep = pd.Series(False, index=f.index)
    side = {k: g for k, g in t.groupby(["condition_id", "s"], sort=False)}
    for (cid, sd), g in f.groupby(["condition_id", "s"], sort=False):
        h = side[(cid, sd)].sort_values("ts", kind="stable")
        ts, qm = h.ts.to_numpy(), h.qmax.to_numpy()
        lo = np.searchsorted(ts, g.ts.to_numpy(), "right")
        hi = np.searchsorted(ts, g.ts.to_numpy() + horizon, "right")
        q = g.q.to_numpy()
        keep.loc[g.index] = [bool(b > a and qm[a:b].max() > qi + EPS) for a, b, qi in zip(lo, hi, q)]
    return f[keep]


# ----------------------------------------------------------------------------- run

def run(split: str) -> dict:
    s = sample()
    s = s[s.split == split].copy()
    have = s.condition_id.map(lambda c: (TR / f"{c}.parquet").exists())
    print(f"{split}: sample {len(s)} markets ({s.match.nunique()} matches), fetched {have.sum()}")
    s = s[have]
    t = load_tickets(s)
    res: dict = {"split": split, "n_markets": int(len(s)), "n_matches": int(s.match.nunique()),
                 "markets_with_pregame_fills": int(t.condition_id.nunique()), "n_tickets": int(len(t))}

    # sanity checks ------------------------------------------------------------------------------------------
    sanity = {}
    sanity["over_share_tickets"] = float(t.is_over.mean())
    sanity["over_share_shares"] = float((t["size"] * t.is_over).sum() / t["size"].sum())
    sanity["over_share_small_tickets"] = float(t[t.usd < 100].is_over.mean())
    po = np.where(t.is_over, t.q, 1 - t.q)
    sanity["p_over_extreme_last10m"] = float(((po > 0.95) | (po < 0.05))[t.hrs < 1 / 6].mean()) if (t.hrs < 1 / 6).any() else None
    sanity["p_over_extreme_1to6h"] = float(((po > 0.95) | (po < 0.05))[(t.hrs >= 1) & (t.hrs < 6)].mean())
    sanity["ticket_hours_before_T_quantiles"] = {str(k): float(v) for k, v in t.hrs.quantile([0, .1, .5, .9]).items()}
    sanity["fee_rates"] = {str(k): int(v) for k, v in s.fee_rate.value_counts().items()}
    sanity["payout_orientation_check"] = {  # Over hit rate vs Over price across all markets
        "mean_over_price_all_tickets": float(po.mean()), "over_tickets_y_mean": float(t[t.is_over].y.mean())}
    res["sanity"] = sanity
    print("sanity", json.dumps(sanity, default=float))

    # primary -------------------------------------------------------------------------------------------------
    prim = maker_side(t, over=True)
    res["primary"] = summ_maker(prim)
    res["primary_by_type"] = {k: summ_maker(g) for k, g in prim.groupby("mtype")}
    res["primary_plus1c"] = summ_maker(maker_side(t, over=True, shift=0.01))
    res["primary_plus1c_by_type"] = {k: summ_maker(g) for k, g in maker_side(t, over=True, shift=0.01).groupby("mtype")}
    res["primary_no_rebate"] = summ_maker(maker_side(t, over=True, rebate=0.0))
    print("\nPRIMARY (maker sells Over/Yes)", fmt(res["primary"]))
    for k, v in res["primary_by_type"].items():
        print(f"   {k:<6}", fmt(v))
    print("   +1c   ", fmt(res["primary_plus1c"]))
    print("   no reb", fmt(res["primary_no_rebate"]))

    v = {}
    # (a) taker Under / No
    for sl in (0.0, 0.01):
        b = taker_under(t, s, slip=sl)
        key = "a_taker_under" + ("_plus1c" if sl else "")
        v[key] = summ_taker(b, len(s))
        for k, g in b.groupby("mtype"):
            v[f"{key}_{k}"] = summ_taker(g, int((s.mtype == k).sum()))
    # (b) symmetric control
    ctrl = maker_side(t, over=False)
    v["b_maker_sell_under"] = summ_maker(ctrl)
    for k, g in ctrl.groupby("mtype"):
        v[f"b_maker_sell_under_{k}"] = summ_maker(g)
    # (d) tickets < $100
    v["d_tickets_lt_100usd"] = summ_maker(prim[prim.usd < 100])
    v["d_tickets_ge_100usd"] = summ_maker(prim[prim.usd >= 100])
    # (e) share-weighted
    v["e_share_weighted"] = summ_maker(prim, weight="share")
    for k, g in prim.groupby("mtype"):
        v[f"e_share_weighted_{k}"] = summ_maker(g, weight="share")
    res["variants"] = v
    for k, d in v.items():
        if "bets" in d:
            print(f"  {k:<32} bets {d['bets']:>4} ROI {100 * d.get('roi', np.nan):+.2f}% "
                  f"[{100 * d.get('ci_lo', np.nan):+.2f},{100 * d.get('ci_hi', np.nan):+.2f}] "
                  f"p {d.get('mean_price', np.nan):.3f} win {d.get('win_rate', np.nan):.3f}")
        else:
            print(f"  {k:<32}", fmt(d))

    # (c) calibration
    cm = calibration(t, s)
    res["c_calibration"] = {"all": calib_rows(cm.assign(all="all"), "all"), "mtype": calib_rows(cm, "mtype"),
                            "month": calib_rows(cm, "month"), "fee_rate": calib_rows(cm, "fee_rate")}
    print("\n(c) calibration: implied Over/Yes price vs hit rate")
    for grp, rows in res["c_calibration"].items():
        for r in rows:
            print(f"   {grp:<8} {r['key']:<10} n {r['markets']:>4} p {r['mean_p']:.3f} hit {r['hit']:.3f} "
                  f"hit-p {100 * r['hit_minus_p']:+.2f}c [{100 * r['ci_lo']:+.2f},{100 * r['ci_hi']:+.2f}]")

    # diagnostics ---------------------------------------------------------------------------------------------
    dg = {}
    for k, g in prim.groupby("fee_rate"):
        dg[f"fee_{k:.4f}"] = summ_maker(g)
    for k, g in prim.groupby("league"):
        dg[f"league_{k}"] = summ_maker(g)
    for lo_, hi_ in [(0, 1 / 6), (1 / 6, 1), (1, 3), (3, 6), (6, 24.01)]:
        dg[f"hrs_{lo_:.2f}-{hi_:.0f}"] = summ_maker(prim[(prim.hrs >= lo_) & (prim.hrs < hi_)])
    for lo_, hi_ in [(0, 0.4), (0.4, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 1.0)]:
        dg[f"q_{lo_:.1f}-{hi_:.1f}"] = summ_maker(prim[(prim.q >= lo_) & (prim.q < hi_)])
    boq = back_of_queue(prim, t)
    dg["back_of_queue_stress"] = summ_maker(boq)
    dg["back_of_queue_stress_plus1c"] = summ_maker(boq.assign(pnl=boq.pnl - 0.01, notional=boq.notional + 0.01))
    # raw taker P&L of Over/Yes buyers (per $, share weighted, fee included) -- mirror image of the maker side
    ov = t[t.is_over]
    fee = C.taker_fee(1.0, ov.q, ov.fee_rate)
    r, lo, hi = C.cluster_ci((ov.y - ov.q - fee) / (ov.q + fee), ov.match, weights=ov["size"] * (ov.q + fee))
    dg["over_takers_net_roi_usd_weighted"] = dict(roi=r, ci_lo=lo, ci_hi=hi, usd=float((ov["size"] * ov.q).sum()))
    un = t[~t.is_over]
    fee = C.taker_fee(1.0, un.q, un.fee_rate)
    r, lo, hi = C.cluster_ci((un.y - un.q - fee) / (un.q + fee), un.match, weights=un["size"] * (un.q + fee))
    dg["under_takers_net_roi_usd_weighted"] = dict(roi=r, ci_lo=lo, ci_hi=hi, usd=float((un["size"] * un.q).sum()))
    res["diagnostics"] = dg
    print("\ndiagnostics")
    for k, d in dg.items():
        if "markets" in d:
            print(f"  {k:<34}", fmt(d))
        else:
            print(f"  {k:<34}", json.dumps(d, default=float))

    # capacity ------------------------------------------------------------------------------------------------
    cand = pd.read_parquet(OUT / "candidates.parquet")
    cand = cand[cand.split == split]
    days = (cand["T"].max() - cand["T"].min()) / 86400
    g = per_market(prim)
    nm = len(s)
    res["capacity"] = dict(sample_markets=nm, markets_filled=int(len(g)),
                           usd_per_sampled_market=float((prim.sh * prim.notional).sum() / nm),
                           pnl_per_sampled_market=float((prim.sh * prim.pnl).sum() / nm),
                           candidate_markets=int(len(cand)), candidate_days=float(days),
                           candidate_markets_per_day=float(len(cand) / max(days, 1)))
    res["capacity"]["usd_per_day"] = res["capacity"]["usd_per_sampled_market"] * res["capacity"]["candidate_markets_per_day"]
    res["capacity"]["pnl_per_day"] = res["capacity"]["pnl_per_sampled_market"] * res["capacity"]["candidate_markets_per_day"]
    print("\ncapacity", json.dumps(res["capacity"], default=float))

    OUT.mkdir(parents=True, exist_ok=True)
    prim.to_parquet(OUT / f"fills_{split}.parquet", index=False)
    with open(OUT / f"results_{split}.json", "w") as fh:
        json.dump(res, fh, indent=1, default=float)
    return res


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "sample":
        build_sample()
    elif cmd == "fetch":
        fetch(sys.argv[2])
    elif cmd == "run":
        run(sys.argv[2] if len(sys.argv) > 2 else "dev")
