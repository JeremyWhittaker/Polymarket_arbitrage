"""Hypothesis `esports_unplayed_map_clause`: the 50-50 clause in esports game-4 child moneylines.

Mechanism claimed: a '-game4' child moneyline of a best-of-5 resolves 50-50 when map 4 is never played
(3-0 sweep). Its fair value is P_played*p + (1-P_played)*0.5, not p. Traders who price "who wins map 4"
before playability is known overprice the favourite by (1-P_played)*(p-0.5); buy the underdog.

Primary rule (pre-registered; parameters frozen before the holdout run):
  SAMPLE    every esports child_moneyline in C.universe() whose market_slug ends in '-game4', game_start_ts
            >= 2026-01-01, in seeded random order, <= 2,000 markets. No volume filter on the rule side.
            DEV = game_start_ts < 2026-07-01, HOLDOUT = game_start_ts >= 2026-07-01.
            Fetch budget (2,000 markets incl. variant e): every game4/game5 market with universe volume > 0 is
            fetched; zero-volume game4 markets are fetched as a random prefix of the seeded order (530 of 784)
            because universe `volume` turned out not to be a reliable "no fills" flag. Bets from that stratum
            are weighted by the inverse inclusion probability (ht_w ~1.5) -> estimates for the whole population.
            (Zero-volume game5 markets are not fetched: variant e covers volume > 0 game5 markets only.)
  WINDOW    T = series game_start_ts (== the child market's game_start_ts). Fills with ts in [T-24h, T+60 min).
  SIGNAL    the first fill in the window whose favourite price max(q, 1-q) >= 0.58 (q = price paid for the side
            acquired; a taker SELL of k at p is acquiring the other side at 1-p). t0 = its ts. Favourite = the
            side priced >= 0.5 on that fill, underdog = the other side (fixed from then on).
  ENTRY     the first second with ts in [t0+3, T+60 min) having a fill that acquires the underdog. Price = the
            HIGHEST price paid for the underdog in that second (end of a sweep; conservative). + taker fee
            shares*fee_rate*p*(1-p) with the market's fee_rate from C.universe().
  PAYOFF    hold to resolution; underdog payout from C.universe(): 1, 0, or 0.5 (map 4 not played).
  METRIC    taker ROI per $1 (C.taker_roi, fractional payoff), C.cluster_ci by series event_slug. +1c slippage.
            One bet per market (one game4 market per series).

Variants (all reported): (a) favourite threshold 0.55 / 0.65; (b) pregame-only cutoff ts < T;
  (c) calibration diagnostic: equal-weight mean(payoff - price) of all pre-cutoff fills by favourite /
  underdog side; (d) maker: a resting bid on the underdog at the price of the last fill that acquired the
  underdog at or before t0, live from t0+3 s, filled by the first later taker fill (before the cutoff) acquiring
  the favourite at >= 1 - bid; queue share phi = 0.5 of that fill's size (capacity only); C.maker_roi;
  "worse fills" = require a 1c trade-through; (e) game-5 child markets with the same rule and a T+90 min cutoff.
Extra diagnostics (not rule variants): BO7 placebo (game4 of a series that also lists game5/game6 markets is
  always played, so the clause is irrelevant there), month / title / entry-timing splits, the Jan-Feb 2026 listing
  regime (game4 markets were then created mid-series, mostly only when map 4 was going to be played), and a fair-
  value check of pregame game4 prices against the iid-map model implied by the series moneyline.

Review fixes (after the stats review; the rule, its parameters and the sample are unchanged, and the same fixes are
applied identically to DEV and HOLDOUT):
  CAPACITY-VALID PRIMARY  The literal per-$1 ROI counts bets whose entry print carried less than $1 (e.g. 40 shares
            at 0.02 = $0.80), so $1 could not have been bought at that price. The capacity-valid primary is the same
            rule with a fill-or-kill $1 order: a bet counts only if the underdog prints in the entry second carry
            >= STAKE = $1 at <= the entry price (`usd_at_entry`, observable at entry; USD_TOL = half a cent of
            tolerance because an exact $1 order prints as 0.999999). The literal number is still reported.
            Companions: stake = min(entry-print $, cap) for caps $10 / $100 (partial fills), and FOK $5.
  ROBUST ESTIMATORS (robust()) equal-share ROI sum(pnl)/sum(cost) with its own cluster bootstrap, per-bet ROI
            capped at +100% (winsorised), drop-top-1% / 2%, the per-share P&L t-statistic and the share of the
            summed ROI coming from the top 1/3/5 bets. The iid bootstrap "P(ROI <= 0)" was removed: with one 46x
            observation it only measures how often that bet is resampled.
  VERDICT GATE (gate(), `verdict` command) on the HOLDOUT capacity-valid primary: per-$1 ROI, drop-top-1% ROI
            and equal-share ROI must all be > 0 both after fees and at +1c. PROFITABLE also needs the DEV
            capacity-valid CI lower bound > 0. Failing the gate -> DEAD (INCONCLUSIVE if < 30 holdout bets).
  EXECUTION ROBUSTNESS  entry at the next underdog print second >= te + 3 s instead of the first (price2).
  STABILITY  equal-share ROI by month, title, entry timing, plus leave-one-title-out.

Usage (every call under the memory cap):
  python -m pmsports.research.h_esports_unplayed_map_clause sample
  python -m pmsports.research.h_esports_unplayed_map_clause fetch
  python -m pmsports.research.h_esports_unplayed_map_clause run dev|holdout
  python -m pmsports.research.h_esports_unplayed_map_clause verdict     (review gate on results_{dev,holdout}.json)
  python -m pmsports.research.h_esports_unplayed_map_clause pooled      (post-hoc diagnostics on saved bets)
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

from pmsports.research import common as C

SLUG = "esports_unplayed_map_clause"
OUT = C.RESEARCH / f"h_{SLUG}"
TR = OUT / "trades"
START_TS = pd.Timestamp("2026-01-01", tz="UTC").timestamp()
SPLIT_TS = pd.Timestamp("2026-07-01", tz="UTC").timestamp()
SEED = 20260919
MAX_MARKETS = 2000
N_ZERO_CHECK = 25
FETCH_PRE, FETCH_POST = 86400, 5400          # fetched window [T-24h, T+90 min]; the rule cuts it per market

# frozen primary parameters
PARAMS = dict(thr=0.58, cutoff=3600, lag=3, pre=86400)
EPS = 1e-6                                   # Data API prices carry float noise (0.4899999951 for 0.49)
PHI = 0.5                                    # maker queue share
STAKE = 1.0                                  # review fix: the per-$1 stake must be fillable at the entry print (FOK)
CAPS = (10.0, 100.0)                         # stake-weighted companions: stake = min(entry-print $, cap)
MIN_HOLDOUT_BETS = 30                        # fewer capacity-valid holdout bets -> INCONCLUSIVE
USD_TOL = 0.005                              # $ prints carry float noise (a $1 order prints as 0.999999)


# ----------------------------------------------------------------------------- sample

def build_sample() -> pd.DataFrame:
    u = C.universe(columns=["condition_id", "token_id", "outcome_idx", "outcome", "payout", "family", "league",
                            "market_type", "market_slug", "event_slug", "game_start_ts", "fee_rate", "volume"])
    u = u[(u.family == "esports") & (u.market_type == "child_moneyline")].copy()
    u["g"] = pd.to_numeric(u.market_slug.str.extract(r"-game(\d+)$")[0], errors="coerce")
    maxg = u.groupby("event_slug").g.max()
    u = u[u.g.isin([4, 5]) & (u.game_start_ts >= START_TS)]
    pay = u.pivot_table(index="condition_id", columns="outcome_idx", values="payout", aggfunc="first")
    outs = u.pivot_table(index="condition_id", columns="outcome_idx", values="outcome", aggfunc="first")
    meta = u.drop_duplicates("condition_id").set_index("condition_id")[
        ["league", "event_slug", "market_slug", "game_start_ts", "fee_rate", "volume", "g"]]
    s = meta.join(pay.rename(columns={0: "y0", 1: "y1"})).join(outs.rename(columns={0: "o0", 1: "o1"})).reset_index()
    s["g"] = s.g.astype(int)
    s["series_maxg"] = s.event_slug.map(maxg).astype(int)
    ok = s.y0.isin([0, 0.5, 1]) & s.y1.isin([0, 0.5, 1]) & ((s.y0 + s.y1) == 1)
    print(f"candidates {len(s)} (game4 {(s.g == 4).sum()}, game5 {(s.g == 5).sum()}); bad payout dropped {(~ok).sum()}")
    s = s[ok].copy()
    s["split"] = np.where(s.game_start_ts < SPLIT_TS, "dev", "holdout")
    s["fee_rate"] = s.fee_rate.fillna(0.0)
    rng = np.random.default_rng(SEED)
    s["order"] = rng.permutation(len(s))
    s = s.sort_values("order").reset_index(drop=True)
    zero = s.volume.fillna(0) <= 0
    # round 1 fetched every volume>0 market (game4 + game5) plus the first N_ZERO_CHECK zero-volume game4 markets
    # in random order. One of those spot checks had fills (after T+60 min), so `volume` is not a reliable "no fills"
    # flag: round 2 spends the rest of the 2,000-market budget on the next zero-volume game4 markets in the same
    # random order (a prefix of it, so round 1's spot checks are included), and bets from that stratum carry the
    # inverse inclusion probability as weight (ht_w) so estimates refer to the whole game4 population.
    n_zero_fetch = max(N_ZERO_CHECK, MAX_MARKETS - int((~zero).sum()))
    zc = s[zero & (s.g == 4)].head(n_zero_fetch).condition_id       # random: s is in random order
    s["zero_check"] = s.condition_id.isin(set(zc))
    s["fetch"] = ~zero | s.zero_check
    s["stratum"] = np.where(zero, "zero_volume", "volume_pos")
    s = s.sort_values(["g", "order"]).reset_index(drop=True)
    assert int(s.fetch.sum()) <= MAX_MARKETS
    n_all = s.groupby(["g", "split", "stratum"]).condition_id.transform("size")
    n_fet = s.groupby(["g", "split", "stratum"]).fetch.transform("sum")
    s["ht_w"] = np.where(n_fet > 0, n_all / n_fet.clip(lower=1), np.nan)
    OUT.mkdir(parents=True, exist_ok=True)
    s.to_parquet(OUT / "sample.parquet", index=False)
    print(s.groupby(["g", "split"]).agg(n=("condition_id", "size"), fetch=("fetch", "sum"),
                                        zero_vol=("volume", lambda v: (v.fillna(0) <= 0).sum()),
                                        void=("y0", lambda y: (y == 0.5).mean())))
    print("total fetch", int(s.fetch.sum()))
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
    rows = P.trades(cid, T - FETCH_PRE, T + FETCH_POST)
    df = pd.DataFrame(rows, columns=COLS)
    df.to_parquet(path, index=False)
    return len(df)


def fetch() -> None:
    import pmsports.http as H
    H.HOST_RPS["data-api.polymarket.com"] = 5
    TR.mkdir(parents=True, exist_ok=True)
    s = sample()
    s = s[s.fetch]
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


# ----------------------------------------------------------------------------- load

def token_map(cids) -> dict:
    """token_id -> outcome_idx (the token is authoritative; the Data API outcomeIndex disagrees on ~1%)."""
    u = C.universe(columns=["condition_id", "token_id", "outcome_idx"])
    u = u[u.condition_id.isin(set(cids))]
    return dict(zip(u.token_id, u.outcome_idx.astype(int)))


def _normalize(d: pd.DataFrame, tok: dict) -> pd.DataFrame:
    d = d[d["size"].astype(float) > 0]
    idx = d.asset.map(tok)
    bad = idx.isna().to_numpy()
    idx = np.where(bad, d.outcomeIndex, idx).astype(int)
    buy = (d.side == "BUY").to_numpy()
    px = d.price.astype(float).to_numpy()
    return pd.DataFrame({"ts": d.timestamp.astype(np.int64).to_numpy(), "s": np.where(buy, idx, 1 - idx),
                         "q": np.where(buy, px, 1 - px), "size": d["size"].astype(float).to_numpy(),
                         "oi_mismatch": idx != d.outcomeIndex.astype(int).to_numpy(), "no_token": bad})


def load_fills(s: pd.DataFrame) -> tuple[pd.DataFrame, list]:
    """Normalized taker fills for the fetched markets: s = side acquired, q = price paid, y = payout of s."""
    tok = token_map(s.condition_id)
    parts, missing = [], []
    for r in s.itertuples():
        p = TR / f"{r.condition_id}.parquet"
        if not p.exists():
            missing.append(r.condition_id)
            continue
        d = pd.read_parquet(p)
        if len(d) == 0:
            continue
        x = _normalize(d, tok)
        x["y"] = np.where(x.s == 0, r.y0, r.y1)
        parts.append(x.assign(condition_id=r.condition_id))
    f = pd.concat(parts, ignore_index=True)
    f = f.sort_values(["condition_id", "ts"], kind="stable").reset_index(drop=True)
    print(f"fills {len(f):,} in {f.condition_id.nunique()} markets; outcomeIndex != token on {f.oi_mismatch.mean():.2%}; "
          f"unknown token {f.no_token.mean():.2%}; missing tapes {len(missing)}")
    return f, missing


# ----------------------------------------------------------------------------- rule

def taker_bets(f: pd.DataFrame, s: pd.DataFrame, thr=0.58, cutoff=3600, lag=3, pre=86400) -> pd.DataFrame:
    """One row per market with a signal and an entry. cutoff is seconds after T (0 = pregame only)."""
    meta = s.set_index("condition_id")
    rows = []
    for cid, g in f.groupby("condition_id", sort=False):
        m = meta.loc[cid]
        T = int(m.game_start_ts)
        g = g[(g.ts >= T - pre) & (g.ts < T + cutoff)]
        if len(g) == 0:
            continue
        ts, sd, q, sz = g.ts.to_numpy(), g.s.to_numpy(), g.q.to_numpy(), g["size"].to_numpy()
        favp = np.maximum(q, 1 - q)
        hit = np.flatnonzero(favp >= thr - EPS)
        if len(hit) == 0:
            continue
        i0 = hit[0]
        t0 = ts[i0]
        fav = sd[i0] if q[i0] >= 0.5 else 1 - sd[i0]
        und = 1 - fav
        cand = np.flatnonzero((sd == und) & (ts >= t0 + lag))
        base = dict(condition_id=cid, event_slug=m.event_slug, league=m.league, g=int(m.g), T=T, t0=int(t0), ht_w=float(m.ht_w),
                    fav=int(fav), fav_p0=float(favp[i0]), fee_rate=float(m.fee_rate), y_und=float(m.y1 if und else m.y0),
                    series_maxg=int(m.series_maxg), month=pd.Timestamp(T, unit="s", tz="UTC").strftime("%Y-%m"),
                    n_window=len(g))
        if len(cand) == 0:
            rows.append({**base, "entered": False})
            continue
        te = ts[cand[0]]
        sec = cand[ts[cand] == te]
        price = float(q[sec].max())
        after = cand[ts[cand] >= te]
        # review fix (execution robustness): the next underdog print second >= te + lag, before the cutoff
        nxt = cand[ts[cand] >= te + lag]
        if len(nxt):
            te2 = ts[nxt[0]]
            sec2 = nxt[ts[nxt] == te2]
            nx = dict(te2=int(te2), price2=float(q[sec2].max()), usd_at_entry2=float((sz[sec2] * q[sec2]).sum()))
        else:
            nx = dict(te2=np.nan, price2=np.nan, usd_at_entry2=np.nan)
        rows.append({**base, "entered": True, "te": int(te), "price": price, "price_min": float(q[sec].min()),
                     "shares_at_entry": float(sz[sec].sum()), "usd_at_entry": float((sz[sec] * q[sec]).sum()),
                     "und_usd_rest": float((sz[after] * q[after]).sum()),
                     "und_usd_rest_le": float((sz[after] * q[after])[q[after] <= price + EPS].sum()), **nx})
    return pd.DataFrame(rows)


def maker_bets(f: pd.DataFrame, s: pd.DataFrame, thr=0.58, cutoff=3600, lag=3, pre=86400, through=0.0) -> pd.DataFrame:
    """Variant (d): bid on the underdog at the last underdog-acquiring fill price at or before t0; filled by the
    first later taker fill (ts in [t0+lag, T+cutoff)) acquiring the favourite at >= 1 - bid + through."""
    meta = s.set_index("condition_id")
    rows = []
    for cid, g in f.groupby("condition_id", sort=False):
        m = meta.loc[cid]
        T = int(m.game_start_ts)
        g = g[(g.ts >= T - pre) & (g.ts < T + cutoff)]
        if len(g) == 0:
            continue
        ts, sd, q, sz = g.ts.to_numpy(), g.s.to_numpy(), g.q.to_numpy(), g["size"].to_numpy()
        favp = np.maximum(q, 1 - q)
        hit = np.flatnonzero(favp >= thr - EPS)
        if len(hit) == 0:
            continue
        i0 = hit[0]
        t0 = ts[i0]
        fav = sd[i0] if q[i0] >= 0.5 else 1 - sd[i0]
        und = 1 - fav
        prev = np.flatnonzero((sd == und) & (np.arange(len(ts)) <= i0))
        if len(prev) == 0:
            continue
        b = float(q[prev[-1]])
        fill = np.flatnonzero((sd == fav) & (ts >= t0 + lag) & (q >= 1 - b + through - EPS))
        base = dict(condition_id=cid, event_slug=m.event_slug, league=m.league, g=int(m.g), fee_rate=float(m.fee_rate), ht_w=float(m.ht_w),
                    y_und=float(m.y1 if und else m.y0), bid=b, series_maxg=int(m.series_maxg))
        if len(fill) == 0:
            rows.append({**base, "filled": False})
            continue
        tf = ts[fill[0]]
        sec = fill[ts[fill] == tf]
        rows.append({**base, "filled": True, "tf": int(tf), "shares": PHI * float(sz[sec].sum())})
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- stats

def summ(b: pd.DataFrame, slip=0.0, maker=False, weights=None) -> dict:
    """Per-$1 ROI, equal stake per bet; bets from the zero-volume stratum carry their inverse inclusion
    probability (ht_w, ~1.5) so the estimate refers to the whole population. `weights` multiplies ht_w."""
    if len(b) == 0:
        return dict(bets=0)
    w = b.ht_w.to_numpy(float) if "ht_w" in b else np.ones(len(b))
    weights = w if weights is None else w * np.asarray(weights, float)
    if maker:
        r = C.maker_roi(b.bid, b.y_und, b.fee_rate)
        cost = b.bid.to_numpy()
    else:
        r = C.taker_roi(b.price, b.y_und, b.fee_rate, slip)
        cost = np.clip(b.price.to_numpy() + slip, 0.001, 0.999)
    r = np.asarray(r)
    mean, lo, hi = C.cluster_ci(r, b.event_slug, weights=weights)
    return dict(bets=int(len(b)), series=int(b.event_slug.nunique()), roi=round(mean, 4), ci_lo=round(lo, 4),
                ci_hi=round(hi, 4), avg_price=round(float(np.mean(cost)), 4), void_rate=round(float((b.y_und == 0.5).mean()), 3),
                und_win=round(float((b.y_und == 1).mean()), 3), mean_payoff=round(float(b.y_und.mean()), 4),
                med_roi_bet=round(float(np.median(r)), 4))


def fmt(d: dict) -> str:
    if not d.get("bets"):
        return "bets 0"
    return (f"bets {d['bets']:4d} ser {d['series']:4d}  ROI {d['roi']:+.4f} [{d['ci_lo']:+.4f}, {d['ci_hi']:+.4f}]  "
            f"px {d['avg_price']:.3f} payoff {d['mean_payoff']:.3f} void {d['void_rate']:.2f} win {d['und_win']:.2f}")


# ----------------------------------------------------------------------------- review fixes: robust / capacity-valid

def _parts(b: pd.DataFrame, slip=0.0, price_col="price"):
    """Per-bet ROI per $1, cost per share (price + slip + fee) and P&L per share."""
    c = np.clip(b[price_col].to_numpy(float) + slip, 0.001, 0.999)
    fee = C.taker_fee(1.0, c, b.fee_rate.to_numpy(float))
    pnl = b.y_und.to_numpy(float) - c - fee
    return pnl / (c + fee), c + fee, pnl


def _r4(x):
    return None if x is None or not np.isfinite(x) else round(float(x), 4)


def _ci(r, ev, w) -> list:
    m, lo, hi = C.cluster_ci(r, ev, weights=w, n_boot=4000)
    return [_r4(m), _r4(lo), _r4(hi)]


def robust(b: pd.DataFrame, slip=0.0, price_col="price") -> dict:
    """Estimators that a single sub-$1 longshot fill cannot dominate (review fix). All carry ht_w.
    per_usd_roi       the literal metric: equal $ stake per bet (mean of per-bet ROI)
    equal_share_roi   equal share count per bet: sum(pnl)/sum(cost) = cost-weighted mean ROI, cluster bootstrap
    winsor_1x_roi     per-bet ROI capped at +100%
    drop_top1pct/2pct per-$1 ROI without the best max(1, round(p*n)) bets
    t_per_share       t-statistic of the per-share P&L (unweighted, one bet per series)
    top_share_1_3_5   share of the summed per-bet ROI that comes from the top 1 / 3 / 5 bets (None if the sum <= 0)"""
    n = len(b)
    if n < 5:
        return dict(bets=int(n))
    r, cost, pnl = _parts(b, slip, price_col)
    w = b.ht_w.to_numpy(float)
    ev = b.event_slug.to_numpy()
    order = np.argsort(-r, kind="stable")
    out = dict(bets=int(n), per_usd_roi=_ci(r, ev, w), equal_share_roi=_ci(r, ev, w * cost),
               winsor_1x_roi=_ci(np.minimum(r, 1.0), ev, w))
    for p in (0.01, 0.02):
        keep = order[max(1, int(round(p * n))):]
        out[f"drop_top{int(round(p * 100))}pct"] = _r4((r[keep] * w[keep]).sum() / w[keep].sum())
    tot = float((r * w).sum())
    out["top_share_1_3_5"] = [_r4((r[order[:k]] * w[order[:k]]).sum() / tot) if tot > 0 else None for k in (1, 3, 5)]
    out["t_per_share"] = _r4(pnl.mean() / pnl.std(ddof=1) * np.sqrt(n)) if n > 2 else None
    return out


def fillable(b: pd.DataFrame, stake=STAKE) -> pd.DataFrame:
    """Capacity-valid subset: the entry second's underdog prints carry >= `stake` $ at <= the entry price, so a
    fill-or-kill order for `stake` would have filled at that price (the print size is visible at entry)."""
    return b[b.usd_at_entry >= stake - USD_TOL] if len(b) else b


def cv_brief(b: pd.DataFrame) -> dict:
    """Compact capacity-valid view of a bet set: literal, FOK-$1 per-$1 ROI and equal-share ROI, fees and +1c."""
    if len(b) == 0:
        return dict(bets=0)
    fb = fillable(b)
    out = dict(bets=int(len(b)), literal_roi=summ(b)["roi"], fillable_bets=int(len(fb)))
    if len(fb) >= 5:
        for slip, tag in ((0.0, ""), (0.01, "_plus1c")):
            rb = robust(fb, slip)
            out[f"fillable_roi{tag}"] = rb["per_usd_roi"]
            out[f"fillable_equal_share{tag}"] = rb["equal_share_roi"]
            out[f"fillable_drop_top1pct{tag}"] = rb["drop_top1pct"]
    return out


def stake_weighted(b: pd.DataFrame, cap: float, slip=0.0) -> dict:
    """Partial fills: stake = min(entry-print $, cap). ROI = P&L / $ staked, plus the same without the best bet by P&L."""
    if len(b) < 5:
        return dict(bets=int(len(b)))
    r, _, _ = _parts(b, slip)
    st = np.minimum(b.usd_at_entry.to_numpy(float), cap) * b.ht_w.to_numpy(float)
    pnl = r * st
    top = int(np.argmax(pnl))
    keep = np.arange(len(b)) != top
    return dict(bets=int(len(b)), roi=_ci(r, b.event_slug.to_numpy(), st), usd_staked=round(float(st.sum()), 0),
                pnl_usd=round(float(pnl.sum()), 1), top_bet=str(b.event_slug.iloc[top]), top_bet_pnl=round(float(pnl[top]), 1),
                roi_without_top_bet=_r4(pnl[keep].sum() / st[keep].sum()))


def capacity_valid(b: pd.DataFrame) -> dict:
    """The review's capacity-valid primary (FOK $1) with robust companions, FOK $5 and stake-weighted versions."""
    out = {}
    for lab, stake in (("fok_1usd", STAKE), ("fok_5usd", 5.0)):
        d = fillable(b, stake)
        out[lab] = {"dropped_unfillable": int(len(b) - len(d)), "after_fees": summ(d), "plus1c": summ(d, slip=0.01),
                    "robust": robust(d), "robust_plus1c": robust(d, 0.01)}
    for cap in CAPS:
        out[f"stake_min_entry_usd_{int(cap)}"] = stake_weighted(b, cap)
        out[f"stake_min_entry_usd_{int(cap)}_plus1c"] = stake_weighted(b, cap, 0.01)
    return out


def split_eq(b: pd.DataFrame, key) -> dict:
    """Per group: bets, literal per-$1 ROI, equal-share ROI (fees and +1c), and the fillable per-$1 ROI."""
    out = {}
    for k, g in b.groupby(key, observed=True):
        r, cost, _ = _parts(g)
        r1, cost1, _ = _parts(g, 0.01)
        w = g.ht_w.to_numpy(float)
        fg = fillable(g)
        rf = _parts(fg)[0] if len(fg) else np.array([])
        out[str(k)] = dict(bets=int(len(g)), roi=_r4((r * w).sum() / w.sum()),
                           equal_share=_r4((r * w * cost).sum() / (w * cost).sum()),
                           equal_share_plus1c=_r4((r1 * w * cost1).sum() / (w * cost1).sum()),
                           fillable_bets=int(len(fg)),
                           fillable_roi=_r4((rf * fg.ht_w).sum() / fg.ht_w.sum()) if len(fg) else None)
    return out


def gate(hold: dict) -> dict:
    """Review verdict gate on the HOLDOUT capacity-valid primary (FOK $1): every estimator > 0 after fees and at +1c."""
    fv = hold["capacity_valid"]["fok_1usd"]
    rb, rb1 = fv["robust"], fv["robust_plus1c"]
    return {"fillable_per_usd_roi_after_fees>0": fv["after_fees"]["roi"] > 0,
            "fillable_per_usd_roi_plus1c>0": fv["plus1c"]["roi"] > 0,
            "fillable_drop_top1pct_after_fees>0": rb["drop_top1pct"] > 0,
            "fillable_drop_top1pct_plus1c>0": rb1["drop_top1pct"] > 0,
            "fillable_equal_share_after_fees>0": rb["equal_share_roi"][0] > 0,
            "fillable_equal_share_plus1c>0": rb1["equal_share_roi"][0] > 0}


def verdict() -> dict:
    dev = json.loads((OUT / "results_dev.json").read_text())
    hold = json.loads((OUT / "results_holdout.json").read_text())
    hv, dv = hold["capacity_valid"]["fok_1usd"], dev["capacity_valid"]["fok_1usd"]
    enough = hv["after_fees"].get("bets", 0) >= MIN_HOLDOUT_BETS
    checks = gate(hold) if enough else {}
    if not enough:
        v = "INCONCLUSIVE"
    elif all(checks.values()):
        v = "PROFITABLE" if dv["after_fees"]["ci_lo"] > 0 else "PROMISING"
    else:
        v = "DEAD"
    out = dict(verdict=v, gate=checks,
               literal_preregistered=dict(dev=dev["primary"], holdout=hold["primary"], holdout_plus1c=hold["primary_plus1c"]),
               capacity_valid_primary=dict(dev=dv["after_fees"], dev_plus1c=dv["plus1c"], holdout=hv["after_fees"],
                                           holdout_plus1c=hv["plus1c"], dev_robust=dv["robust"], holdout_robust=hv["robust"],
                                           dev_robust_plus1c=dv["robust_plus1c"], holdout_robust_plus1c=hv["robust_plus1c"]))
    (OUT / "results_verdict.json").write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps(out, indent=1, default=str))
    return out


def calibration(f: pd.DataFrame, s: pd.DataFrame, cutoff=3600, pre=86400) -> dict:
    """Variant (c): equal-weight mean(payoff - price) of every pre-cutoff fill, by favourite/underdog side."""
    T = f.condition_id.map(s.set_index("condition_id").game_start_ts)
    x = f[(f.ts >= T - pre) & (f.ts < T + cutoff)].copy()
    x["ev"] = x.condition_id.map(s.set_index("condition_id").event_slug)
    x["edge"] = x.y - x.q
    x["w"] = x.condition_id.map(s.set_index("condition_id").ht_w).to_numpy(float)
    out = {}
    for name, sel in [("favourite_side(q>=0.5)", x.q >= 0.5), ("underdog_side(q<0.5)", x.q < 0.5),
                      ("underdog_q_0.30-0.45", (x.q >= 0.30) & (x.q < 0.45)), ("fav_q_0.55-0.70", (x.q >= 0.55) & (x.q < 0.70))]:
        y = x[sel]
        if len(y) < 10:
            out[name] = dict(fills=int(len(y)))
            continue
        m, lo, hi = C.cluster_ci(y.edge, y.ev, weights=y.w)
        mu, ulo, uhi = C.cluster_ci(y.edge, y.ev, weights=y.w * y["size"] * y.q)
        out[name] = dict(fills=int(len(y)), markets=int(y.condition_id.nunique()), mean_edge=round(m, 4), ci=[round(lo, 4), round(hi, 4)],
                         usd_weighted_edge=round(mu, 4), usd_ci=[round(ulo, 4), round(uhi, 4)], avg_q=round(float(y.q.mean()), 4),
                         usd=round(float((y["size"] * y.q).sum()), 0))
    return out


# ----------------------------------------------------------------------------- fair-value diagnostic

def bo_series(p, need):
    """P(win a first-to-`need` series) with iid map win prob p."""
    from math import comb
    return sum(comb(need - 1 + k, k) * p ** need * (1 - p) ** k for k in range(need))


def implied_p(P, need):
    lo, hi = 1e-4, 1 - 1e-4
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if bo_series(mid, need) < P else (lo, mid)
    return (lo + hi) / 2


def fair_value_check(f: pd.DataFrame, s: pd.DataFrame) -> dict:
    """Pregame (ts in [T-2h, T)) median game4 price vs the iid-map model implied by the series moneyline
    pregame mid (C.markets(): series with >= $50k volume only -> a diagnostic, not a sample for the rule).
    Orientation: team A = game4 outcome 0. naive = p; fair = 3p(1-p)*p + (1-3p(1-p))*0.5 for BO5."""
    mk = C.markets()
    mk = mk[mk.family == "esports"][["event_slug", "o0", "o1", "pre_mid0", "pre_n_fills"]].dropna(subset=["pre_mid0"])
    mk = mk.drop_duplicates("event_slug").set_index("event_slug")
    g4 = s[(s.g == 4) & (s.series_maxg == 4)].set_index("condition_id")
    T = f.condition_id.map(g4.game_start_ts)
    x = f[f.condition_id.isin(g4.index) & (f.ts >= T - 7200) & (f.ts < T)].copy()
    x["p0"] = np.where(x.s == 0, x.q, 1 - x.q)
    med = x.groupby("condition_id").p0.median()
    rows = []
    for cid, p0 in med.items():
        m = g4.loc[cid]
        if m.event_slug not in mk.index:
            continue
        sm = mk.loc[m.event_slug]
        if str(sm.o0).strip().lower() == str(m.o0).strip().lower():
            P = sm.pre_mid0
        elif str(sm.o1).strip().lower() == str(m.o0).strip().lower():
            P = 1 - sm.pre_mid0
        else:
            continue
        p = implied_p(P, 3)
        pp = 3 * p * (1 - p)
        rows.append(dict(condition_id=cid, event_slug=m.event_slug, P_series=P, p_map=p, naive=p, fair=pp * p + (1 - pp) * 0.5,
                         g4_price=p0, y0=m.y0))
    d = pd.DataFrame(rows)
    if len(d) == 0:
        return dict(n=0)
    # orient everything to the game4-price favourite
    flip = d.g4_price < 0.5
    for c in ["naive", "fair", "g4_price", "y0"]:
        d[c] = np.where(flip, 1 - d[c], d[c])
    d = d[(d.naive - 0.5).abs() > 0.02]
    return dict(n=int(len(d)), mean_g4_price=round(float(d.g4_price.mean()), 4), mean_naive_p=round(float(d.naive.mean()), 4),
                mean_fair=round(float(d.fair.mean()), 4), mean_payoff_fav=round(float(d.y0.mean()), 4),
                share_closer_to_naive=round(float(((d.g4_price - d.naive).abs() < (d.g4_price - d.fair).abs()).mean()), 3),
                slope_g4_on_naive=round(float(np.polyfit(d.naive - 0.5, d.g4_price - 0.5, 1)[0]), 3),
                slope_g4_on_fair=round(float(np.polyfit(d.fair - 0.5, d.g4_price - 0.5, 1)[0]), 3))


# ----------------------------------------------------------------------------- run

def _in_window(f, s, cutoff=3600, pre=86400):
    T = f.condition_id.map(s.set_index("condition_id").game_start_ts)
    return f[(f.ts >= T - pre) & (f.ts < T + cutoff)]


def robustness(b: pd.DataFrame) -> dict:
    """Diagnostics on the primary bets (not rule variants): tail dependence and a sanity split on entry price."""
    r = np.asarray(C.taker_roi(b.price, b.y_und, b.fee_rate))
    r1 = np.asarray(C.taker_roi(b.price, b.y_und, b.fee_rate, 0.01))
    w = b.ht_w.to_numpy(float)
    out = {}
    for k in (1, 3, 5):
        keep = np.argsort(-r, kind="stable")[k:]
        out[f"drop_top{k}"] = round(float((r[keep] * w[keep]).sum() / w[keep].sum()), 4)
        out[f"drop_top{k}_plus1c"] = round(float((r1[keep] * w[keep]).sum() / w[keep].sum()), 4)
    # review fix: the iid-bootstrap "P(ROI <= 0)" was removed (with one 46x bet it only measures how often that bet
    # is resampled); see robust() for estimators that a single longshot cannot dominate.
    out["share_bets_profitable"] = round(float((r > 0).mean()), 3)
    out["entry_price_lt_0.5"] = summ(b[b.price < 0.5])
    out["entry_price_0.2_to_0.5"] = summ(b[(b.price >= 0.2) & (b.price < 0.5)])
    out["entry_price_0.2_to_0.5_plus1c"] = summ(b[(b.price >= 0.2) & (b.price < 0.5)], slip=0.01)
    return out


def _split_rows(b, key):
    return {str(k): summ(g) for k, g in b.groupby(key)} if len(b) else {}


def run(split: str) -> dict:
    s0 = sample()
    s_all = s0[s0.split == split]
    s = s_all[s_all.fetch]
    f, missing = load_fills(s)
    res: dict = {"split": split, "params": PARAMS}
    g4 = s[s.g == 4]
    g5 = s[s.g == 5]
    f4 = f[f.condition_id.isin(set(g4.condition_id))]
    f5 = f[f.condition_id.isin(set(g5.condition_id))]
    zc = s[s.zero_check]
    res["coverage"] = dict(game4_population=int((s_all.g == 4).sum()), game4_fetched=int(len(g4)),
                           game4_zero_volume_not_fetched=int(((s_all.g == 4) & ~s_all.fetch).sum()),
                           zero_volume_fetched=int(len(zc)),
                           zero_volume_with_any_fill=int(f[f.condition_id.isin(set(zc.condition_id))].condition_id.nunique()),
                           zero_volume_with_rule_window_fill=int(_in_window(f[f.condition_id.isin(set(zc.condition_id))], s).condition_id.nunique()),
                           game4_markets_with_any_fill=int(f4.condition_id.nunique()),
                           game5_population=int((s_all.g == 5).sum()), game5_fetched=int(len(g5)),
                           missing_tapes=len(missing), population_void_rate_game4=round(float((s_all[s_all.g == 4].y0 == 0.5).mean()), 3))
    T4 = f4.condition_id.map(s.set_index("condition_id").game_start_ts)
    rel = (f4.ts - T4)
    res["fill_timing_game4"] = dict(fills_total=int(len(f4)), in_rule_window=int(((rel >= -86400) & (rel < 3600)).sum()),
                                    pregame=int(((rel >= -86400) & (rel < 0)).sum()),
                                    markets_with_rule_window_fill=int(f4[(rel >= -86400) & (rel < 3600)].condition_id.nunique()),
                                    markets_with_pregame_fill=int(f4[(rel >= -86400) & (rel < 0)].condition_id.nunique()))

    # ---- primary
    b_all = taker_bets(f4, s, **PARAMS)
    b = b_all[b_all.entered].copy() if len(b_all) else b_all
    b.to_parquet(OUT / f"bets_primary_{split}.parquet", index=False)
    res["signals"] = int(len(b_all))
    res["primary"] = summ(b)
    res["primary_plus1c"] = summ(b, slip=0.01)
    res["primary_plus2c"] = summ(b, slip=0.02)
    if len(b):
        bm = b.assign(price=b.price_min)
        res["primary_entry_min_price_in_second"] = summ(bm)
        res["primary_usd_weighted_by_entry_size"] = summ(b, weights=b.usd_at_entry.clip(lower=1e-9))
        res["dollars_1usd_per_bet"] = int(len(b))
        res["capacity"] = dict(median_usd_at_entry=round(float(b.usd_at_entry.median()), 2),
                               mean_usd_at_entry=round(float(b.usd_at_entry.mean()), 2),
                               sum_usd_at_entry=round(float(b.usd_at_entry.sum()), 0),
                               sum_und_usd_rest_at_or_below_entry=round(float(b.und_usd_rest_le.sum()), 0),
                               population_sum_usd_at_entry_ht=round(float((b.usd_at_entry * b.ht_w).sum()), 0),
                               population_bets_ht=round(float(b.ht_w.sum()), 1),
                               months=int(b.month.nunique()), bets_per_month=round(len(b) / max(b.month.nunique(), 1), 1))
        b["entry_rel_min"] = (b.te - b["T"]) / 60
        b["timing"] = pd.cut(b.entry_rel_min, [-1e9, -60, 0, 30, 60], labels=["<T-1h", "T-1h..T", "T..T+30m", "T+30..60m"])
        res["by_entry_timing"] = _split_rows(b, "timing")
        b["fmt"] = np.where(b.series_maxg >= 5, "BO7(placebo)", "BO5")
        res["by_format"] = _split_rows(b, "fmt")
        res["by_title"] = _split_rows(b, "league")
        res["by_month"] = _split_rows(b, "month")
        b["pbin"] = pd.cut(b.price, [0, 0.2, 0.3, 0.4, 0.5, 1.0])
        res["by_entry_price"] = _split_rows(b, "pbin")
        res["by_fee_rate"] = _split_rows(b, "fee_rate")
        b["stratum"] = np.where(b.ht_w > 1, "zero_volume(ht_w>1)", "volume_pos")
        res["by_stratum"] = _split_rows(b, "stratum")
        res["ex_jan_feb"] = summ(b[~b.month.isin(["2026-01", "2026-02"])])
        res["BO5_pregame_entries"] = summ(b[(b.series_maxg == 4) & (b.te < b["T"])])
        res["BO5_plus1c"] = summ(b[b.series_maxg == 4], slip=0.01)
        # clause-adjusted expected edge: realized underdog payoff vs price, split by void / not void
        res["robustness"] = robustness(b)
        # ---- review fixes: capacity-valid primary, robust estimators, stability, execution robustness
        res["capacity_valid"] = capacity_valid(b)
        res["robust_literal"] = robust(b)
        res["robust_literal_plus1c"] = robust(b, 0.01)
        res["stability_by_month"] = split_eq(b, "month")
        res["stability_by_title"] = split_eq(b, "league")
        res["stability_by_entry_timing"] = split_eq(b, "timing")
        res["stability_by_format"] = split_eq(b, "fmt")
        lot = {}
        for t in b.league.unique():
            d = b[b.league != t]
            r_, c_, _ = _parts(d)
            lot[t] = dict(bets=int(len(d)), roi=_r4((r_ * d.ht_w).sum() / d.ht_w.sum()),
                          equal_share=_r4((r_ * d.ht_w * c_).sum() / (d.ht_w * c_).sum()))
        res["leave_one_title_out"] = lot
        lol = b[b.league == "league-of-legends"]
        if len(lol) > 5:
            r_ = np.sort(_parts(lol)[0])[::-1]
            res["lol_only_drop_top3"] = _r4(r_[3:].mean())
        res["pregame_entries_robust"] = robust(b[b.te < b["T"]])
        res["inplay_entries_robust"] = robust(b[b.te >= b["T"]])
        res["inplay_entries_price_ge_0.20"] = robust(b[(b.te >= b["T"]) & (b.price >= 0.20)])
        b2 = b[b.price2.notna()]
        res["entry_first_vs_next_print"] = dict(
            bets_with_next_print=int(len(b2)), bets_without=int(len(b) - len(b2)),
            first_print=robust(b2), next_print=robust(b2, price_col="price2"),
            first_print_fillable=robust(fillable(b2)), next_print_fillable=robust(b2[b2.usd_at_entry2 >= STAKE - USD_TOL], price_col="price2"))
        # post-hoc only (documented in DEV as "absurd prints"; NOT a rule, needs data after 2026-09-19 to test)
        res["posthoc_fillable_entry_lt_0.5"] = dict(after_fees=robust(fillable(b[b.price < 0.5])),
                                                    plus1c=robust(fillable(b[b.price < 0.5]), 0.01))
        res["decomp"] = dict(payoff_minus_price=round(float((b.y_und - b.price).mean()), 4),
                             not_void_payoff_minus_price=round(float((b.y_und - b.price)[b.y_und != 0.5].mean()), 4),
                             void_share=round(float((b.y_und == 0.5).mean()), 3))
    # ---- variants
    res["var_a_thr055"] = summ(v := (lambda d: d[d.entered] if len(d) else d)(taker_bets(f4, s, **{**PARAMS, "thr": 0.55})))
    res["var_a_thr055_plus1c"] = summ(v, slip=0.01)
    res["var_a_thr055_capacity_valid"] = cv_brief(v)
    res["var_a_thr065"] = summ(v := (lambda d: d[d.entered] if len(d) else d)(taker_bets(f4, s, **{**PARAMS, "thr": 0.65})))
    res["var_a_thr065_plus1c"] = summ(v, slip=0.01)
    res["var_a_thr065_capacity_valid"] = cv_brief(v)
    vb = (lambda d: d[d.entered] if len(d) else d)(taker_bets(f4, s, **{**PARAMS, "cutoff": 0}))
    res["var_b_pregame"] = summ(vb)
    res["var_b_pregame_plus1c"] = summ(vb, slip=0.01)
    res["var_b_pregame_capacity_valid"] = cv_brief(vb)
    if len(vb):
        res["var_b_pregame_BO5"] = summ(vb[vb.series_maxg == 4])
        res["var_b_pregame_BO5_capacity_valid"] = cv_brief(vb[vb.series_maxg == 4])
        res["var_b_pregame_BO7_placebo"] = summ(vb[vb.series_maxg >= 5])
    res["var_c_calibration"] = calibration(f4, s)
    res["var_c_calibration_pregame"] = calibration(f4, s, cutoff=0)
    mb = maker_bets(f4, s, **PARAMS)
    mbf = mb[mb.filled] if len(mb) else mb
    res["var_d_maker"] = summ(mbf, maker=True)
    res["var_d_maker_candidates"] = int(len(mb))
    if len(mbf):
        res["var_d_maker_shares_phi"] = round(float(mbf.shares.sum()), 0)
        # review fix: weight each maker fill by the $ it would actually have filled (phi * shares * bid)
        res["var_d_maker_usd_weighted"] = summ(mbf, maker=True, weights=mbf.shares * mbf.bid)
    mb2 = maker_bets(f4, s, **PARAMS, through=0.01)
    res["var_d_maker_1c_through"] = summ(mb2[mb2.filled] if len(mb2) else mb2, maker=True)
    e5 = (lambda d: d[d.entered] if len(d) else d)(taker_bets(f5, s, **{**PARAMS, "cutoff": 5400}))
    res["var_e_game5"] = summ(e5)
    res["var_e_game5_plus1c"] = summ(e5, slip=0.01)
    res["var_e_game5_capacity_valid"] = cv_brief(e5)
    res["fair_value_check_BO5"] = fair_value_check(f4, s)
    (OUT / f"results_{split}.json").write_text(json.dumps(res, indent=1, default=str))
    _print(res)
    return res


def pooled() -> dict:
    """Post-hoc diagnostics read from the saved primary bets of both periods (no re-evaluation of the rule):
    pooled ROI, tail dependence, the 0.2-0.5 entry-price subset, and a stake capped at the entry print's $ size."""
    parts = []
    for sp in ("dev", "holdout"):
        b = pd.read_parquet(OUT / f"bets_primary_{sp}.parquet").assign(split=sp)
        parts.append(b)
    a = pd.concat(parts, ignore_index=True)
    a["roi"] = C.taker_roi(a.price, a.y_und, a.fee_rate)
    a["roi1"] = C.taker_roi(a.price, a.y_und, a.fee_rate, 0.01)

    def ci(x, col="roi", w=None):
        ww = x.ht_w.to_numpy(float) * (1 if w is None else np.asarray(w, float))
        m, lo, hi = C.cluster_ci(x[col], x.event_slug, weights=ww, n_boot=4000)
        return dict(bets=int(len(x)), roi=round(m, 4), ci=[round(lo, 4), round(hi, 4)])

    out = {"pooled_primary": ci(a), "pooled_primary_plus1c": ci(a, "roi1")}
    r, w = a.roi.to_numpy(), a.ht_w.to_numpy(float)
    for k in (1, 3, 5, 10):
        keep = np.argsort(-r, kind="stable")[k:]
        out[f"pooled_drop_top{k}"] = round(float((r[keep] * w[keep]).sum() / w[keep].sum()), 4)
    sane = a[(a.price >= 0.2) & (a.price < 0.5)]
    out["pooled_entry_0.2_0.5"] = ci(sane)
    out["pooled_entry_0.2_0.5_plus1c"] = ci(sane, "roi1")
    pg = a[(a.series_maxg == 4) & (a.te < a["T"])]
    out["pooled_BO5_pregame_entry"] = ci(pg)
    for sp, g in a.groupby("split"):
        stake = np.minimum(g.usd_at_entry.to_numpy(), 100.0)
        out[f"{sp}_stake_min_entry_usd_100"] = {**ci(g, w=stake), "plus1c": ci(g, "roi1", w=stake),
                                                "usd_deployed": round(float(stake.sum()), 0),
                                                "pnl_usd": round(float((g.roi * stake).sum()), 1)}
    # review fix: the pooled numbers above are tail-driven; capacity-valid and robust pooled versions
    out["pooled_robust_literal"] = robust(a)
    out["pooled_fillable_1usd"] = robust(fillable(a))
    out["pooled_fillable_1usd_plus1c"] = robust(fillable(a), 0.01)
    out["pooled_fillable_entry_0.2_0.5"] = robust(fillable(sane))
    out["pooled_fillable_entry_0.2_0.5_plus1c"] = robust(fillable(sane), 0.01)
    (OUT / "results_pooled_posthoc.json").write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps(out, indent=1))
    return out


def _print(res: dict) -> None:
    for k, v in res.items():
        if isinstance(v, dict) and ("series" in v or v.get("bets") == 0):
            print(f"{k:38s} {fmt(v)}")
        elif isinstance(v, dict) and v and all(isinstance(x, dict) and ("series" in x or x.get("bets") == 0) for x in v.values()):
            print(k)
            for kk, vv in v.items():
                print(f"    {kk:34s} {fmt(vv)}")
        else:
            print(f"{k:38s} {json.dumps(v, default=str)}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "sample":
        build_sample()
    elif cmd == "fetch":
        fetch()
    elif cmd == "run":
        run(sys.argv[2] if len(sys.argv) > 2 else "dev")
    elif cmd == "pooled":
        pooled()
    elif cmd == "verdict":
        verdict()
