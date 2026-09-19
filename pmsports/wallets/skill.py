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
    """Per (wallet, market) exposure, P&L and luck variance."""
    t = t.assign(cost=t["size"] * t.q, pnl=t["size"] * (t.y - t.q),
                 fee=taker_fee(t["size"], t.q, t.fee_rate.fillna(0).to_numpy()),
                 a=np.where(t.side_idx == 0, t["size"], 0.0), b=np.where(t.side_idx == 1, t["size"], 0.0),
                 p0w=t["size"] * np.where(t.side_idx == 0, t.q, 1 - t.q))
    g = t.groupby(["proxyWallet", "condition_id"], observed=True)
    pos = g.agg(cost=("cost", "sum"), pnl=("pnl", "sum"), fee=("fee", "sum"), a=("a", "sum"), b=("b", "sum"),
                p0w=("p0w", "sum"), shares=("size", "sum"), n=("q", "size"), ts=("timestamp", "min"),
                in_play=("in_play", "mean"), family=("family", "first"), event=("event_slug", "first"))
    pos = pos.reset_index()
    p0 = (pos.p0w / pos.shares).clip(0.001, 0.999)
    pos["var"] = (pos.a - pos.b) ** 2 * p0 * (1 - p0)
    pos["pnl_net"] = pos.pnl - pos.fee
    return pos.drop(columns=["p0w"])


def wallet_stats(pos: pd.DataFrame) -> pd.DataFrame:
    g = pos.groupby("proxyWallet", observed=True)
    s = g.agg(markets=("condition_id", "size"), staked=("cost", "sum"), pnl=("pnl", "sum"),
              pnl_net=("pnl_net", "sum"), var=("var", "sum"), wins=("pnl", lambda x: (x > 0).sum()),
              in_play=("in_play", "mean"), top_family=("family", lambda x: x.value_counts().index[0]),
              family_share=("family", lambda x: x.value_counts(normalize=True).iloc[0]))
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

def copy_prices(t: pd.DataFrame, rows: pd.DataFrame, delays=(0, 5, 30, 60), horizon: float = 300.0) -> pd.DataFrame:
    """For each trade in `rows`, the price a follower pays d seconds later.

    Uses the first print by a *different* wallet on the same market+side at or after
    ts+d: that print is a taker acquisition of the side, i.e. the executable ask then.
    NaN when nobody traded that side within `horizon` seconds (copy not executable).
    """
    out = rows.copy()
    tape = t[["condition_id", "side_idx", "timestamp", "q", "proxyWallet"]].sort_values(
        ["condition_id", "side_idx", "timestamp"])
    groups = {k: g for k, g in tape.groupby(["condition_id", "side_idx"], observed=True)}
    for d in delays:
        col = np.full(len(out), np.nan)
        if d == 0:
            out[f"q_d{d}"] = out.q.to_numpy()
            continue
        for key, idx in out.groupby(["condition_id", "side_idx"], observed=True).indices.items():
            g = groups.get(key)
            if g is None:
                continue
            ts, qs, ws = g.timestamp.to_numpy(float), g.q.to_numpy(float), g.proxyWallet.to_numpy()
            r = out.iloc[idx]
            want = r.timestamp.to_numpy(float) + d
            j = np.searchsorted(ts, want, side="left")
            me = r.proxyWallet.to_numpy()
            for k, (jj, w, t0) in enumerate(zip(j, me, want)):
                while jj < len(ts) and ws[jj] == w:     # skip the leader's own follow-on fills
                    jj += 1
                if jj < len(ts) and ts[jj] - t0 <= horizon:
                    col[idx[k]] = qs[jj]
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
