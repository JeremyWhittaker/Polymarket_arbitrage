"""Are some sports bettors genuinely skilled, and can you copy them?

Every taker fill is normalized to "wallet bought side s at price q, side paid y" (see
tapes.load_trades). Then:

positions   per (wallet, market): cost, pnl = sum size*(y-q) (hold to resolution), and the
            luck variance under "prices are fair": (A-B)^2 p(1-p), A/B = shares on each side
stats       per wallet over a period: markets, $ staked, pnl, ROI, win rate, z = pnl/sqrt(var)
copy        replay a wallet's later trades at the first *other* taker print on the same side
            >= d seconds after theirs (an executable ask), minus the taker fee, held to
            resolution. d = 0 is the (unattainable) same-price upper bound.

Selection always uses data strictly before the evaluation window (no look-ahead), and every
selection rule is compared with random wallets of similar activity (placebo).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..polymarket import taker_fee


# ----------------------------------------------------------------------------- positions / stats

def positions(t: pd.DataFrame) -> pd.DataFrame:
    """Per (wallet, market) exposure, P&L and luck variance (lean: grouped on integer codes)."""
    size = t["size"].to_numpy(np.float64)
    q = t.q.to_numpy(np.float64)
    side0 = t.side_idx.to_numpy() == 0
    k = pd.DataFrame({
        "w": t.proxyWallet.cat.codes.to_numpy(), "m": t.condition_id.cat.codes.to_numpy(),
        "cost": size * q, "pnl": size * (t.y.to_numpy(np.float64) - q),
        "fee": taker_fee(size, q, np.nan_to_num(t.fee_rate.to_numpy(np.float64))),
        "a": np.where(side0, size, 0.0), "b": np.where(side0, 0.0, size),
        "p0w": size * np.where(side0, q, 1 - q), "shares": size,
        "ts": t.timestamp.to_numpy(), "in_play": t.in_play.to_numpy(np.float32)})
    g = k.groupby(["w", "m"], sort=False)
    pos = g.agg(cost=("cost", "sum"), pnl=("pnl", "sum"), fee=("fee", "sum"), a=("a", "sum"), b=("b", "sum"),
                p0w=("p0w", "sum"), shares=("shares", "sum"), n=("cost", "size"), ts=("ts", "min"),
                in_play=("in_play", "mean")).reset_index()
    del k
    p0 = (pos.p0w / pos.shares).clip(0.001, 0.999)
    pos["var"] = (pos.a - pos.b) ** 2 * p0 * (1 - p0)
    pos["pnl_net"] = pos.pnl - pos.fee
    wcat, mcat = t.proxyWallet.cat.categories, t.condition_id.cat.categories
    fam = t.groupby("condition_id", observed=True).family.first()
    ev = t.groupby("condition_id", observed=True).event_slug.first()
    pos["proxyWallet"] = pd.Categorical.from_codes(pos.w.to_numpy(), categories=wcat)
    pos["condition_id"] = pd.Categorical.from_codes(pos.m.to_numpy(), categories=mcat)
    mids = pos.condition_id.astype(object)
    pos["family"] = mids.map(fam).astype("category")
    pos["event"] = mids.map(ev).astype("category")
    return pos.drop(columns=["p0w", "w", "m"])


def wallet_stats(pos: pd.DataFrame) -> pd.DataFrame:
    g = pos.groupby("proxyWallet", observed=True)
    s = g.agg(markets=("cost", "size"), staked=("cost", "sum"), pnl=("pnl", "sum"), pnl_net=("pnl_net", "sum"),
              var=("var", "sum"), in_play=("in_play", "mean"))
    s["wins"] = (pos.pnl > 0).groupby(pos.proxyWallet, observed=True).sum()
    fam = pos.groupby(["proxyWallet", "family"], observed=True).cost.sum().reset_index()
    fam = fam.sort_values("cost", ascending=False).drop_duplicates("proxyWallet").set_index("proxyWallet")
    s["top_family"] = fam.family
    s["family_share"] = fam.cost / s.staked
    s["roi"] = s.pnl / s.staked
    s["win_rate"] = s.wins / s.markets
    s["z"] = s.pnl / np.sqrt(s["var"].clip(lower=1e-9))
    return s


def fdr_survivors(z: pd.Series, alpha: float = 0.05) -> pd.Series:
    """Benjamini-Hochberg on one-sided p-values (skill > 0)."""
    from scipy.stats import norm
    p = pd.Series(norm.sf(z.to_numpy()), index=z.index).sort_values()
    m = len(p)
    thresh = alpha * np.arange(1, m + 1) / m
    passed = p.to_numpy() <= thresh
    k = np.max(np.where(passed)[0]) + 1 if passed.any() else 0
    return pd.Series(p.index[:k])


# ----------------------------------------------------------------------------- copy execution

def build_groups(t: pd.DataFrame) -> dict:
    """(market, side) -> (timestamps, prices, wallet codes), sorted by time; reusable across calls."""
    tape = pd.DataFrame({"m": t.condition_id.cat.codes.to_numpy(), "s": t.side_idx.to_numpy(),
                         "ts": t.timestamp.to_numpy(np.float64), "q": t.q.to_numpy(np.float64),
                         "w": t.proxyWallet.cat.codes.to_numpy()}).sort_values(["m", "s", "ts"], kind="stable")
    keys = tape[["m", "s"]].to_numpy()
    cut = np.flatnonzero((np.diff(keys[:, 0]) != 0) | (np.diff(keys[:, 1]) != 0)) + 1
    bounds = np.concatenate([[0], cut, [len(tape)]])
    ts, q, w = tape.ts.to_numpy(), tape.q.to_numpy(), tape.w.to_numpy()
    mcat, wcat = t.condition_id.cat.categories, t.proxyWallet.cat.categories
    out = {}
    for a, b in zip(bounds[:-1], bounds[1:]):
        out[(mcat[keys[a, 0]], int(keys[a, 1]))] = (ts[a:b], q[a:b], w[a:b])
    return {"groups": out, "wcat": wcat}


def copy_prices(t: pd.DataFrame, rows: pd.DataFrame, delays=(0, 5, 30, 60), horizon: float = 300.0,
                groups: dict | None = None) -> pd.DataFrame:
    """For each trade in `rows`, the price a follower pays d seconds later.

    Uses the first print by a *different* wallet on the same market+side at or after
    ts+d: that print is a taker acquisition of the side, i.e. the executable ask then.
    NaN when nobody traded that side within `horizon` seconds (copy not executable).
    """
    assert rows.proxyWallet.cat.categories is t.proxyWallet.cat.categories or \
        rows.proxyWallet.cat.categories.equals(t.proxyWallet.cat.categories), "rows must be a subset of t"
    out = rows.copy()
    gb = groups or build_groups(t)
    grp = gb["groups"]
    for d in delays:
        col = np.full(len(out), np.nan)
        if d == 0:
            out[f"q_d{d}"] = out.q.to_numpy()
            continue
        for key, idx in out.groupby(["condition_id", "side_idx"], observed=True).indices.items():
            g = grp.get((key[0], int(key[1])))
            if g is None:
                continue
            ts, qs, ws = g
            r = out.iloc[idx]
            want = r.timestamp.to_numpy(float) + d
            j = np.searchsorted(ts, want, side="left")
            me = r.proxyWallet.cat.codes.to_numpy()      # rows share the tape's categories
            n = len(ts)
            while True:                                  # skip the leader's own follow-on fills
                own = (j < n) & (ws[np.minimum(j, n - 1)] == me)
                if not own.any():
                    break
                j = j + own
            hit = (j < n)
            hit[hit] = ts[j[hit]] - want[hit] <= horizon
            col[idx[hit]] = qs[j[hit]]
        out[f"q_d{d}"] = col
    return out


def copy_returns(rows: pd.DataFrame, delay: int, stake: str = "equal") -> pd.DataFrame:
    """Per-trade follower P&L per $ staked at the delayed price, taker fee included."""
    q = rows[f"q_d{delay}"].to_numpy(float)
    ok = ~np.isnan(q) & (q > 0.005) & (q < 0.995)
    r = rows.loc[ok].copy()
    q = q[ok]
    fee = taker_fee(1.0, q, r.fee_rate.fillna(0).to_numpy())
    r["copy_roi"] = (r.y.to_numpy() - q - fee) / (q + fee)
    r["copy_q"] = q
    r["w"] = 1.0 if stake == "equal" else (r["size"] * r.q).to_numpy()
    return r


def summarize_copy(r: pd.DataFrame, n_boot: int = 1000, seed: int = 11) -> dict:
    """Stake-weighted ROI with an event-clustered bootstrap CI (trades in one game move together)."""
    if r.empty:
        return {"trades": 0, "events": 0, "roi": np.nan, "ci_lo": np.nan, "ci_hi": np.nan}
    e = r.groupby("event_slug", observed=True).apply(
        lambda x: pd.Series({"pnl": (x.copy_roi * x.w).sum(), "w": x.w.sum()}), include_groups=False)
    roi = e.pnl.sum() / e.w.sum()
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(e), size=(n_boot, len(e)))
    boots = e.pnl.to_numpy()[idx].sum(1) / e.w.to_numpy()[idx].sum(1)
    return {"trades": len(r), "events": len(e), "roi": float(roi),
            "ci_lo": float(np.percentile(boots, 2.5)), "ci_hi": float(np.percentile(boots, 97.5))}
