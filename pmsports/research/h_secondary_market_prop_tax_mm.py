"""Hypothesis: anchored market making in thin MLB NRFI and total-8.5 markets ("prop tax").

Mechanism claimed: side markets (NRFI, fixed-line total 8.5) are thin and recreational; a maker
quotes at the touch, pulls when the liquid moneyline moves, and collects the spread.

Rule as implemented (pre-registered, parameters frozen before the holdout run):
  SAMPLE   MLB games whose moneyline (C.markets) has pre_usd >= $25k. Per game the '<event>-nrfi'
           market and the '<event>-total-8pt5' market when they exist (line never chosen by
           volume). Seeded random sample of <= 1,000 markets from DEV (game_start < 2026-07-01)
           and <= 1,000 from HOLDOUT. Taker fills fetched for [T-24h, T), T = game_start_ts.
  QUOTE    From T-12h to T-15m we rest an ask on each token s (an ask on s == a bid on the other).
           Our ask on s at time t = price of the last taker fill acquiring s with ts <= t-3 s;
           no quote on s if that fill is > 6 h old. A taker order acquiring s at q >= our ask
           fills us min(size, K) shares at our ask (K = 100). Taker orders are one fill event per
           (transactionHash, side acquired): size summed, price = max price paid in the sweep.
           Net inventory cap 500 shares per market; fills are truncated at the cap.
  PULL     No quotes during (t_ml, t_ml + 60 s] after any same-event moneyline fill whose
           outcome-0 price differs by >= 0.02 from the median of moneyline fills in
           [t_ml - 300, t_ml). (Outcome-0 orientation gives the same triggers as home orientation.)
  METRIC   Hold to resolution. Per share P&L when a taker acquires s from us at a:
           a - y_s + 0.15 * fee_rate * a * (1 - a)   (no maker fee, 15% rebate of taker fee).
           Primary = total P&L / total shares, 95% CI bootstrapped by game (event_slug).
           Also shown per $ notional (our cost of the complementary side, 1 - a).

Usage (every call under the memory cap):
  python -m pmsports.research.h_secondary_market_prop_tax_mm sample
  python -m pmsports.research.h_secondary_market_prop_tax_mm fetch dev|holdout
  python -m pmsports.research.h_secondary_market_prop_tax_mm run dev|holdout
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

from pmsports.research import common as C

SLUG = "secondary_market_prop_tax_mm"
OUT = C.RESEARCH / f"h_{SLUG}"
TR = OUT / "trades"
SPLIT_TS = pd.Timestamp("2026-07-01", tz="UTC").timestamp()
SEED = 20260919
N_PER_SPLIT = 1000

# frozen primary parameters
PARAMS = dict(K=100, inv_cap=500, lag=3, stale=6 * 3600, q_start=12 * 3600, q_end=15 * 60,
              pull=True, pull_dev=0.02, pull_look=300, pull_len=60, back_queue=False, rebate=C.MAKER_REBATE)
EPS = 1e-6                     # Data API prices carry float noise (0.4899999951 for 0.49)


# ----------------------------------------------------------------------------- sample

def build_sample() -> pd.DataFrame:
    mk = C.markets()
    ml = mk[(mk.league == "mlb") & (mk.pre_usd >= 25_000)][["m", "event_slug", "game_start_ts", "pre_usd"]]
    u = C.universe(columns=["condition_id", "outcome_idx", "outcome", "payout", "league", "game_start_ts",
                            "event_slug", "market_slug", "fee_rate", "token_id"])
    u = u[(u.league == "mlb") & u.event_slug.isin(ml.event_slug)]
    u = u[u.market_slug.str.endswith("-nrfi") | u.market_slug.str.endswith("-total-8pt5")]
    pay = u.pivot_table(index="condition_id", columns="outcome_idx", values="payout", aggfunc="first")
    outs = u.pivot_table(index="condition_id", columns="outcome_idx", values="outcome", aggfunc="first")
    meta = u.drop_duplicates("condition_id").set_index("condition_id")[
        ["event_slug", "market_slug", "game_start_ts", "fee_rate"]]
    s = meta.join(pay.rename(columns={0: "y0", 1: "y1"})).join(outs.rename(columns={0: "o0", 1: "o1"}))
    s = s.reset_index()
    s["mtype"] = np.where(s.market_slug.str.endswith("-nrfi"), "nrfi", "total85")
    s = s.merge(ml.rename(columns={"m": "ml_m", "game_start_ts": "ml_start"}), on="event_slug", how="inner")
    ok = s.y0.isin([0, 0.5, 1]) & s.y1.isin([0, 0.5, 1]) & ((s.y0 + s.y1) == 1) & s.game_start_ts.notna()
    print(f"candidates {len(s)}  dropped (bad payout/start) {(~ok).sum()}")
    s = s[ok]
    s["split"] = np.where(s.game_start_ts < SPLIT_TS, "dev", "holdout")
    parts = []
    for sp, g in s.groupby("split"):
        print(f"  {sp}: {len(g)} candidate markets ({g.mtype.value_counts().to_dict()})")
        parts.append(g.sample(n=min(N_PER_SPLIT, len(g)), random_state=SEED))
    out = pd.concat(parts).sort_values("game_start_ts").reset_index(drop=True)
    OUT.mkdir(parents=True, exist_ok=True)
    out.to_parquet(OUT / "sample.parquet", index=False)
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
    rows = P.trades(cid, T - 86400, T - 1)          # inclusive end: [T-24h, T)
    df = pd.DataFrame(rows, columns=COLS)
    df.to_parquet(path, index=False)
    return len(df)


def fetch(split: str) -> None:
    import pmsports.http as H
    H.HOST_RPS["data-api.polymarket.com"] = 5
    TR.mkdir(parents=True, exist_ok=True)
    s = sample()
    s = s[s.split == split]
    todo = [(r.condition_id, int(r.game_start_ts)) for r in s.itertuples() if not (TR / f"{r.condition_id}.parquet").exists()]
    print(f"fetch {split}: {len(s)} markets, {len(todo)} to fetch")
    done = err = 0
    with ThreadPoolExecutor(4) as ex:
        futs = {ex.submit(_fetch_one, c, t): c for c, t in todo}
        for f in as_completed(futs):
            try:
                f.result()
                done += 1
            except Exception as e:  # noqa: BLE001
                err += 1
                print("ERR", futs[f], e)
            if (done + err) % 100 == 0:
                print(f"  {done + err}/{len(todo)} (errors {err})", flush=True)
    print(f"fetched {done}, errors {err}")


def token_map(cids) -> dict:
    """token_id -> outcome_idx from the universe. The Data API `outcomeIndex` field disagrees with the
    token (`asset`) on ~1% of fills (whole markets flip back and forth); the token is authoritative:
    prices are only self-consistent under the token mapping."""
    u = C.universe(columns=["condition_id", "token_id", "outcome_idx"])
    u = u[u.condition_id.isin(set(cids))]
    return dict(zip(u.token_id, u.outcome_idx.astype(int)))


def _normalize(d: pd.DataFrame, tok: dict) -> pd.DataFrame:
    d = d[(d["size"].astype(float) > 0)]
    idx = d.asset.map(tok)
    bad = idx.isna()
    idx = np.where(bad, d.outcomeIndex, idx).astype(int)
    buy = (d.side == "BUY").to_numpy()
    px = d.price.astype(float).to_numpy()
    return pd.DataFrame({"ts": d.timestamp.astype(np.int64).to_numpy(), "s": np.where(buy, idx, 1 - idx),
                         "q": np.where(buy, px, 1 - px), "size": d["size"].astype(float).to_numpy(),
                         "tx": d.transactionHash.to_numpy(), "w": d.proxyWallet.to_numpy(),
                         "oi_mismatch": (idx != d.outcomeIndex.astype(int).to_numpy()), "no_token": bad.to_numpy()})


def load_trades(s: pd.DataFrame) -> pd.DataFrame:
    """Normalized taker fills for the sampled side markets: s = side acquired, q = price paid."""
    tok = token_map(s.condition_id)
    parts = []
    for r in s.itertuples():
        p = TR / f"{r.condition_id}.parquet"
        if not p.exists():
            continue
        d = pd.read_parquet(p)
        if len(d) == 0:
            continue
        parts.append(_normalize(d, tok).assign(condition_id=r.condition_id))
    t = pd.concat(parts, ignore_index=True)
    print(f"side fills: outcomeIndex != token mapping on {t.oi_mismatch.mean():.2%} "
          f"({t[t.oi_mismatch].condition_id.nunique()} markets); unknown token {t.no_token.mean():.2%}")
    return t


def load_ml(s: pd.DataFrame) -> pd.DataFrame:
    """Raw moneyline tapes for the sampled games, normalized with the token mapping; y = payout of s."""
    from pmsports.wallets.tapes import TAPES
    mk = C.markets().set_index("m")
    games = s.drop_duplicates("event_slug")[["event_slug", "ml_m", "game_start_ts"]]
    games = games.assign(ml_cid=games.ml_m.map(mk.condition_id), y0=games.ml_m.map(mk.y0), y1=games.ml_m.map(mk.y1),
                         fee_rate=games.ml_m.map(mk.fee_rate))
    tok = token_map(games.ml_cid)
    parts = []
    for r in games.itertuples():
        d = pd.read_parquet(TAPES / f"{r.ml_cid}.parquet",
                            columns=["timestamp", "side", "asset", "outcomeIndex", "price", "size", "transactionHash",
                                     "proxyWallet"])
        d = d[(d.timestamp >= r.game_start_ts - 86400) & (d.timestamp < r.game_start_ts)]
        if len(d) == 0:
            continue
        x = _normalize(d, tok)
        x["y"] = np.where(x.s == 0, r.y0, r.y1)
        parts.append(x.assign(event_slug=r.event_slug, game_start_ts=r.game_start_ts, fee_rate=r.fee_rate))
    f = pd.concat(parts, ignore_index=True)
    print(f"moneyline fills [T-24h,T): {len(f):,}; outcomeIndex != token on {f.oi_mismatch.mean():.2%} "
          f"({f[f.oi_mismatch].event_slug.nunique()} games)")
    return f


def taker_orders(t: pd.DataFrame) -> pd.DataFrame:
    """One event per (market, tx, side acquired): size summed, max price paid (sweep limit), first ts."""
    g = t.groupby(["condition_id", "tx", "s"], sort=False).agg(ts=("ts", "min"), q=("q", "max"),
                                                                size=("size", "sum"), qv=("q", "mean"))
    return g.reset_index().sort_values(["condition_id", "ts", "tx"], kind="stable").reset_index(drop=True)


# ----------------------------------------------------------------------------- pull triggers

def pull_triggers(ml: pd.DataFrame, dev=0.02, look=300) -> dict[str, np.ndarray]:
    """event_slug -> sorted trigger times: moneyline fills whose outcome-0 price differs by >= dev
    from the median of moneyline fills in [t - look, t)."""
    out: dict[str, np.ndarray] = {}
    for ev_slug, g in ml.groupby("event_slug"):
        g = g.sort_values("ts", kind="stable")
        ts = g.ts.to_numpy()
        p0 = np.where(g.s.to_numpy() == 0, g.q.to_numpy(), 1 - g.q.to_numpy())
        lo = np.searchsorted(ts, ts - look, "left")
        hi = np.searchsorted(ts, ts, "left")               # strictly earlier fills
        trig = [ts[i] for i in range(len(ts))
                if hi[i] > lo[i] and abs(p0[i] - np.median(p0[lo[i]:hi[i]])) >= dev - EPS]
        out[ev_slug] = np.unique(np.asarray(trig, dtype=np.int64))
    return out


# ----------------------------------------------------------------------------- simulation

def simulate(ev: pd.DataFrame, s: pd.DataFrame, trig: dict, **kw) -> pd.DataFrame:
    """Our maker fills. ev = taker order events for all markets (sorted by market, ts)."""
    p = {**PARAMS, **kw}
    meta = s.set_index("condition_id")
    rows = []
    stats = dict(events_in_window=0, no_quote=0, pulled=0, below_ask=0, inv_capped=0, crossed=0, queue_skip=0)
    for cid, g in ev.groupby("condition_id", sort=False):
        m = meta.loc[cid]
        T = m.game_start_ts
        ts, sd, q, sz = g.ts.to_numpy(), g.s.to_numpy(), g.q.to_numpy(), g["size"].to_numpy()
        tr = trig.get(m.event_slug, np.array([], dtype=np.int64)) if p["pull"] else np.array([], dtype=np.int64)
        idx_side = [np.flatnonzero(sd == k) for k in (0, 1)]
        ts_side = [ts[ix] for ix in idx_side]
        n = 0.0                                             # net position: shares of 1 minus shares of 0
        for i in range(len(ts)):
            t = ts[i]
            if t < T - p["q_start"] or t >= T - p["q_end"]:
                continue
            stats["events_in_window"] += 1
            k = sd[i]
            # our ask on side k: last fill acquiring k with ts <= t - lag
            j = np.searchsorted(ts_side[k], t - p["lag"], "right") - 1
            if j < 0 or t - ts_side[k][j] > p["stale"]:
                stats["no_quote"] += 1
                continue
            a = q[idx_side[k][j]]
            # crossed-quote diagnostic: our other-side ask
            j2 = np.searchsorted(ts_side[1 - k], t - p["lag"], "right") - 1
            a_oth = q[idx_side[1 - k][j2]] if (j2 >= 0 and t - ts_side[1 - k][j2] <= p["stale"]) else np.nan
            if a + a_oth < 1 - EPS:
                stats["crossed"] += 1
            if len(tr):
                jj = np.searchsorted(tr, t, "left") - 1     # last trigger strictly before t
                if jj >= 0 and t - tr[jj] <= p["pull_len"]:
                    stats["pulled"] += 1
                    continue
            if q[i] < a - EPS:
                stats["below_ask"] += 1
                continue
            if p["back_queue"]:
                # back of queue: need a later same-side taker order trading through our price within 60 s
                later = idx_side[k][(ts_side[k] <= t + 60) & (idx_side[k] > i)]
                if not np.any(q[later] > a + EPS):
                    stats["queue_skip"] += 1
                    continue
            fill = min(sz[i], p["K"])
            room = p["inv_cap"] - n if k == 0 else p["inv_cap"] + n   # k==0: we acquire side 1 -> n up
            if room <= EPS:
                stats["inv_capped"] += 1
                continue
            fill = min(fill, room)
            n += fill if k == 0 else -fill
            y = m.y0 if k == 0 else m.y1
            rows.append((cid, m.event_slug, m.mtype, m.split, m.fee_rate, t, int(k), a, a_oth, q[i], fill, y))
    out = pd.DataFrame(rows, columns=["condition_id", "event_slug", "mtype", "split", "fee_rate", "ts", "s",
                                      "a", "a_oth", "q_taker", "sh", "y"])
    reb = p["rebate"] * out.fee_rate.fillna(0) * out.a * (1 - out.a)
    out["pnl"] = out.a - out.y + reb                         # per share, hold to resolution
    out["notional"] = 1 - out.a                              # our cost per share of the complement
    out.attrs["stats"] = stats
    return out


# ----------------------------------------------------------------------------- stats

def summarize(f: pd.DataFrame, pnl_col="pnl", shift=0.0, n_markets=None) -> dict:
    if len(f) == 0:
        return dict(fills=0, markets=0, games=0, shares=0.0, notional=0.0, pnl=0.0, c_per_share=np.nan,
                    ci_lo=np.nan, ci_hi=np.nan, roi=np.nan, roi_lo=np.nan, roi_hi=np.nan)
    v = f[pnl_col].to_numpy() - shift
    sh, notl = f.sh.to_numpy(), (f.notional.to_numpy() + shift)
    m, lo, hi = C.cluster_ci(v, f.event_slug, weights=sh)
    r, rlo, rhi = C.cluster_ci(v / notl, f.event_slug, weights=sh * notl)
    return dict(fills=int(len(f)), markets=int(f.condition_id.nunique()), games=int(f.event_slug.nunique()),
                shares=float(sh.sum()), notional=float((sh * notl).sum()), pnl=float((v * sh).sum()),
                c_per_share=100 * m, ci_lo=100 * lo, ci_hi=100 * hi, roi=r, roi_lo=rlo, roi_hi=rhi)


def fmt(d: dict) -> str:
    if d["fills"] == 0:
        return "no fills"
    return (f"fills {d['fills']:>6,}  mkts {d['markets']:>4}  games {d['games']:>4}  shares {d['shares']:>9,.0f}  "
            f"${d['notional']:>9,.0f}  P&L ${d['pnl']:>8,.0f}  {d['c_per_share']:+.2f}c/sh "
            f"[{d['ci_lo']:+.2f}, {d['ci_hi']:+.2f}]  ROI {100*d['roi']:+.2f}% [{100*d['roi_lo']:+.2f}, {100*d['roi_hi']:+.2f}]")


def markout_mids(ev: pd.DataFrame, s: pd.DataFrame) -> pd.Series:
    """Last two-sided pregame print mid (price of outcome 0): last fill acquiring 0 (ask0) and last fill
    acquiring 1 (bid0 = 1 - q) in [T-60 min, T)."""
    T = s.set_index("condition_id").game_start_ts
    e = ev.assign(T0=ev.condition_id.map(T))
    e = e[(e.ts >= e.T0 - 3600) & (e.ts < e.T0)]
    last = e.sort_values("ts").groupby(["condition_id", "s"]).q.last().unstack()
    last = last.dropna(subset=[0, 1]) if {0, 1} <= set(last.columns) else last.iloc[0:0]
    return ((last[0] + (1 - last[1])) / 2).rename("mid0")


def taker_diag(t: pd.DataFrame, s: pd.DataFrame, ml: pd.DataFrame) -> dict:
    """(f) pregame taker P&L per $ in side markets vs the same games' moneylines, [T-24h, T)."""
    meta = s.set_index("condition_id")
    x = t.join(meta[["event_slug", "game_start_ts", "y0", "y1", "fee_rate", "mtype"]], on="condition_id")
    x = x[(x.ts >= x.game_start_ts - 86400) & (x.ts < x.game_start_ts)]
    x["y"] = np.where(x.s == 0, x.y0, x.y1)
    x["fee"] = C.taker_fee(1.0, x.q, x.fee_rate)
    res = {}
    for name, g in [("side_all", x)] + [(f"side_{k}", g) for k, g in x.groupby("mtype")]:
        res[name] = _tk(g)
    f = ml.copy()
    f["fee"] = C.taker_fee(1.0, f.q, f.fee_rate)
    res["moneyline_same_games"] = _tk(f)
    return res


def _tk(g: pd.DataFrame) -> dict:
    sz, q, y, fee = (g[c].to_numpy(float) for c in ("size", "q", "y", "fee"))
    gross, glo, ghi = C.cluster_ci((y - q) / q, g.event_slug, weights=sz * q)
    net, nlo, nhi = C.cluster_ci((y - q - fee) / (q + fee), g.event_slug, weights=sz * (q + fee))
    return dict(fills=int(len(g)), usd=float((sz * q).sum()), gross=gross, gross_lo=glo, gross_hi=ghi,
                net=net, net_lo=nlo, net_hi=nhi)


# ----------------------------------------------------------------------------- run

def run(split: str) -> dict:
    s = sample()
    s = s[s.split == split].copy()
    have = s.condition_id.map(lambda c: (TR / f"{c}.parquet").exists())
    print(f"{split}: sample {len(s)} markets, fetched {have.sum()}")
    s = s[have]
    t = load_trades(s)
    ev = taker_orders(t)
    print(f"taker fills {len(t):,}  taker orders {len(ev):,}  markets with any fill {ev.condition_id.nunique()}")
    ml = load_ml(s)
    trig = pull_triggers(ml)
    print(f"pull triggers: {sum(len(v) for v in trig.values()):,} over {len(trig)} games")
    res: dict = {"split": split, "n_markets": int(len(s)), "n_games": int(s.event_slug.nunique()),
                 "n_taker_fills": int(len(t)), "n_taker_orders": int(len(ev))}

    prim = simulate(ev, s, trig)
    res["stats"] = prim.attrs["stats"]
    res["primary"] = summarize(prim)
    res["primary_plus1c"] = summarize(prim, shift=0.01)
    print("\nPRIMARY  ", fmt(res["primary"]))
    print("  +1c worse", fmt(res["primary_plus1c"]))
    print("  stats", prim.attrs["stats"])
    # quoted-spread proxy at our fills: our ask on s + our ask on the other side - 1 (both from last prints)
    spr = (prim.a + prim.a_oth - 1)
    res["spread_proxy"] = {k: dict(share_two_sided=float(g.a_oth.notna().mean()),
                                   median_c=float(100 * np.nanmedian(spr[g.index])),
                                   mean_c=float(100 * np.nanmean(spr[g.index])))
                           for k, g in prim.groupby("mtype")}
    print("  spread proxy at our fills", res["spread_proxy"])
    # capacity
    nm = len(s)
    days = pd.to_datetime(s.game_start_ts, unit="s").dt.date.nunique()
    res["capacity"] = dict(markets=nm, days=int(days), shares_per_market=float(prim.sh.sum() / nm),
                           usd_per_market=float((prim.sh * prim.notional).sum() / nm),
                           pnl_per_market=float((prim.sh * prim.pnl).sum() / nm),
                           markets_filled=int(prim.condition_id.nunique()),
                           candidate_markets_per_day=None)
    print("  capacity", res["capacity"])

    variants = {}
    # (a) markout
    mid0 = markout_mids(ev, s)
    pm = prim.join(mid0, on="condition_id")
    pm = pm[pm.mid0.notna()].copy()
    pm["mo"] = pm.a - np.where(pm.s == 0, pm.mid0, 1 - pm.mid0) + C.MAKER_REBATE * pm.fee_rate.fillna(0) * pm.a * (1 - pm.a)
    variants["a_markout_T"] = summarize(pm, "mo")
    variants["a_hold_same_fills"] = summarize(pm)
    # (b) no pull
    nop = simulate(ev, s, trig, pull=False)
    variants["b_no_pull"] = summarize(nop)
    # (c) K
    for K in (25, 500):
        variants[f"c_K{K}"] = summarize(simulate(ev, s, trig, K=K))
    # (d) back of queue
    bq = simulate(ev, s, trig, back_queue=True)
    variants["d_back_of_queue"] = summarize(bq)
    variants["d_back_of_queue_stats"] = bq.attrs["stats"]
    # (e) split by type
    for k, g in prim.groupby("mtype"):
        variants[f"e_{k}"] = summarize(g)
    # fee regime split (dev only has 0 and 0.03)
    for k, g in prim.groupby("fee_rate"):
        variants[f"fee_{k:.2f}"] = summarize(g)
    # by hours-to-start bucket (diagnostic)
    Tm = s.set_index("condition_id").game_start_ts
    hrs = (prim.condition_id.map(Tm) - prim.ts) / 3600
    for lo_, hi_ in [(0.25, 1), (1, 3), (3, 6), (6, 12)]:
        variants[f"hrs_{lo_}-{hi_}"] = summarize(prim[(hrs >= lo_) & (hrs < hi_)])
    # by whether the taker improved on our quote (q > a: we improved the touch) or joined (q == a)
    variants["join_q_eq_a"] = summarize(prim[prim.q_taker <= prim.a + EPS])
    variants["improve_q_gt_a"] = summarize(prim[prim.q_taker > prim.a + EPS])
    # (f) taker diagnostic
    variants["f_taker_diag"] = taker_diag(t, s, ml)
    res["variants"] = variants
    for k, v in variants.items():
        if isinstance(v, dict) and "fills" in v:
            print(f"  {k:<22}", fmt(v))
        else:
            print(f"  {k:<22}", json.dumps(v, default=float)[:600])
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
