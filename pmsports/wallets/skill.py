"""Are some sports bettors genuinely skilled, and can you copy them?

Every taker fill is normalized to "wallet bought side s at price q, side paid y" (see
tapes.load_trades). Then:

positions   per (wallet, market): cost, pnl = sum size*(y-q) (hold to resolution), and the
            luck variance under "prices are fair": (A-B)^2 p(1-p), A/B = shares on each side
stats       per wallet over a period: markets, $ staked, pnl, ROI, win rate, z = pnl/sqrt(var)
copy        replay a wallet's later trades at the first *other* taker print on the same side
            strictly later than signal+d, size bounded, minus the taker fee, held to
            resolution. These prints are proxies, not executable asks; d=0 remains strictly later.

Selection always uses data strictly before the evaluation window (no look-ahead), and every
selection rule is compared with random wallets of similar activity (placebo).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..polymarket import taker_fee
from ..execution import TapeReplay


# ----------------------------------------------------------------------------- positions / stats

def positions(t: pd.DataFrame) -> pd.DataFrame:
    """Per (wallet, market) exposure, P&L and luck variance.

    numpy sort + reduceat on a combined integer key: memory stays ~a few arrays of len(t)
    (a pandas groupby over a wide temp frame OOMs at ~50M fills).
    """
    if t.empty:
        out = pd.DataFrame({c: pd.Series(dtype=float) for c in ("cost", "pnl", "fee", "a", "b", "shares", "in_play", "n", "ts", "var", "pnl_net")})
        for target, source in (("proxyWallet", "proxyWallet"), ("condition_id", "condition_id"), ("family", "family"), ("event", "event_slug")):
            out[target] = t[source].iloc[:0].reset_index(drop=True)
        return out

    w = t.proxyWallet.cat.codes.to_numpy().astype(np.int64)
    m = t.condition_id.cat.codes.to_numpy().astype(np.int64)
    nm = int(m.max()) + 1 if len(m) else 1
    order = np.argsort(w * nm + m, kind="stable")
    key = (w * nm + m)[order]
    starts = np.flatnonzero(np.r_[True, key[1:] != key[:-1]])
    del w, m

    def red(x):
        return np.add.reduceat(np.asarray(x)[order], starts) if len(starts) else np.array([])

    size = t["size"].to_numpy(np.float64)
    q = t.q.to_numpy(np.float64)
    side0 = t.side_idx.to_numpy() == 0
    out = {"cost": red(size * q), "pnl": red(size * (t.y.to_numpy(np.float64) - q)),
           "fee": red(taker_fee(size, q, t.fee_rate.to_numpy(np.float64))),
           "a": red(np.where(side0, size, 0.0)), "b": red(np.where(side0, 0.0, size)),
           "p0w": red(size * np.where(side0, q, 1 - q)), "shares": red(size),
           "in_play": red(t.in_play.to_numpy(np.float64))}
    n = np.diff(np.r_[starts, len(key)])
    out["in_play"] = out["in_play"] / n
    out["n"] = n
    out["ts"] = np.minimum.reduceat(t.timestamp.to_numpy()[order], starts) if len(starts) else np.array([])
    del size, q, side0, order
    pos = pd.DataFrame(out)
    k0 = key[starts]
    wcode, mcode = (k0 // nm).astype(np.int32), (k0 % nm).astype(np.int32)
    p0 = (pos.p0w / pos.shares).clip(0.001, 0.999)
    pos["var"] = (pos.a - pos.b) ** 2 * p0 * (1 - p0)
    pos["pnl_net"] = pos.pnl - pos.fee
    pos["proxyWallet"] = pd.Categorical.from_codes(wcode, categories=t.proxyWallet.cat.categories)
    pos["condition_id"] = pd.Categorical.from_codes(mcode, categories=t.condition_id.cat.categories)
    # per-market attributes via first row of each market code
    mc = t.condition_id.cat.codes.to_numpy()
    first = np.full(len(t.condition_id.cat.categories), -1, dtype=np.int64)
    first[mc[::-1]] = np.arange(len(mc))[::-1]
    for c, src in (("family", "family"), ("event", "event_slug")):
        codes = t[src].cat.codes.to_numpy()[first[mcode]]
        pos[c] = pd.Categorical.from_codes(codes, categories=t[src].cat.categories)
    return pos.drop(columns=["p0w"])


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
    """Reusable normalized tape index; raw row identity preserves shared liquidity."""
    tape = pd.DataFrame({"m": t.condition_id.cat.codes.to_numpy(), "s": t.side_idx.to_numpy(),
                         "ts": t.timestamp.to_numpy(float), "q": t.q.to_numpy(float),
                         "size": t["size"].to_numpy(float), "fee_rate": t.fee_rate.to_numpy(float),
                         "w": t.proxyWallet.cat.codes.to_numpy()})
    return {"replay": TapeReplay(tape), "wcat": t.proxyWallet.cat.categories,
            "mcat": t.condition_id.cat.categories}


def copy_prices(t: pd.DataFrame, rows: pd.DataFrame, delays=(0, 5, 30, 60), horizon: float = 300.0,
                groups: dict | None = None) -> pd.DataFrame:
    """Later other-wallet print proxies, with one-use shares and explicit no-fill metadata.

    Each delay and sizing policy is an independent strategy replay. Zero delay still
    requires a strictly later print: it is a latency sensitivity, not a same-print buy.
    Proportional policy is1% of observable leader notional, capped at$100/order/event.
    """
    out = rows.copy()
    extra = {}
    gb = groups or build_groups(t)
    orders = pd.DataFrame({"m": gb["mcat"].get_indexer(rows.condition_id), "s": rows.side_idx.to_numpy(),
        "signal_ts": rows.timestamp.to_numpy(float), "leader_w": gb["wcat"].get_indexer(rows.proxyWallet),
        "event": rows.event_slug.to_numpy(), "y": rows.y.to_numpy(float)})
    for d in delays:
        for name, budget in (("", np.full(len(rows), 100.)),
                             ("prop_", np.minimum(100., .01 * rows["size"].to_numpy() * rows.q.to_numpy()))):
            result = gb["replay"].replay(orders.assign(budget_usd=budget), delay_s=d,
                                        horizon_s=horizon, event_cap_usd=100.)
            extra[f"{name}q_d{d}"] = result.entry_price.to_numpy()
            for col in ("signal_ts", "receipt_ts", "eligible_ts", "expiry_ts", "fill_ts", "print_id", "shares",
                        "stake_usd", "fee_usd", "cost_usd", "payout", "pnl_usd", "roi", "status"):
                extra[f"{name}{col}_d{d}"] = result[col].to_numpy()
    return pd.concat([out, pd.DataFrame(extra, index=out.index)], axis=1)


def copy_returns(rows: pd.DataFrame, delay: int, stake: str = "equal") -> pd.DataFrame:
    """Follower results weighted by actually allocated capital, not imaginary$100 bets."""
    prefix = "" if stake == "equal" else "prop_"
    cost_col = f"{prefix}cost_usd_d{delay}"
    if cost_col not in rows:
        raise ValueError("legacy copy-price cache lacks consumed-share audit; rerun copy_prices")
    r = rows[rows[cost_col] > 0].copy()
    r["copy_roi"] = r[f"{prefix}roi_d{delay}"]
    r["copy_q"] = r[f"{prefix}q_d{delay}"]
    r["copy_shares"] = r[f"{prefix}shares_d{delay}"]
    r["copy_print_id"] = r[f"{prefix}print_id_d{delay}"]
    r["w"] = r[cost_col]
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
