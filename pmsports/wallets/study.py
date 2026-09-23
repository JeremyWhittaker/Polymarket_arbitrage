"""Copy-the-sharps study: select wallets on period 1, evaluate on period 2 (no look-ahead).

Selection rules (all on P1 data only):
  pnl        biggest P1 hold-to-resolution profit
  roi        best P1 ROI, >= MIN_MKTS markets
  z          best P1 luck-adjusted z-score (pnl / sd under "prices are fair"), >= MIN_MKTS markets
  win_rate   best P1 share of winning markets, >= MIN_MKTS markets (the naive "who wins most")
  whales     largest P1 $ staked
  fdr        wallets whose P1 z survives Benjamini-Hochberg at 5% (skill after multiple testing)
  lb_pnl/lb_vol  Polymarket's all-time sports leaderboard (includes P2 results -> biased up)
  placebo    random wallets with >= MIN_MKTS P1 markets (200 draws) - what selection must beat

Evaluation on P2: the group's own ROI, and a follower's ROI copying each P2 trade after
d seconds at the next executable print, with the market's taker fee.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from . import skill

log = logging.getLogger("pmsports")
MIN_MKTS = 30
TOP_K = 50
DELAYS = (0, 5, 30, 60)


def _causal_cutoff(t: pd.DataFrame, max_ts: float, meta: pd.DataFrame) -> pd.Series:
    """Filter to prior fills and prior Gamma closure for analytical outcome availability.
    closed_ts is a proxy, not an independently recorded public resolution receipt.
    """
    if meta is None:
        raise ValueError("metadata is required for causal cutoffs (prevents outcome leakage)")

    if isinstance(t.condition_id.dtype, pd.CategoricalDtype):
        c_codes = t.condition_id.cat.codes.to_numpy()
        closed_ts_lut = meta["closed_ts"].reindex(t.condition_id.cat.categories).to_numpy(dtype=np.float64)
        t_closed_ts = np.full(len(t), np.nan, dtype=np.float64)
        valid = c_codes >= 0
        if len(closed_ts_lut) > 0:
            # Mask indices that are out of bounds as an extra safety measure
            safe_codes = np.clip(c_codes[valid], 0, len(closed_ts_lut) - 1)
            t_closed_ts[valid] = closed_ts_lut[safe_codes]
    else:
        t_closed_ts = t.condition_id.map(meta["closed_ts"]).to_numpy(dtype=np.float64)

    return (t.timestamp < max_ts) & (t_closed_ts < max_ts) & ~np.isnan(t_closed_ts)


def selections(s1: pd.DataFrame, lb: pd.DataFrame | None) -> dict[str, list[str]]:
    act = s1[s1.markets >= MIN_MKTS]
    sel = {
        "pnl": s1.nlargest(TOP_K, "pnl").index.tolist(),
        "roi": act.nlargest(TOP_K, "roi").index.tolist(),
        "z": act.nlargest(TOP_K, "z").index.tolist(),
        "win_rate": act.nlargest(TOP_K, "win_rate").index.tolist(),
        "whales": s1.nlargest(TOP_K, "staked").index.tolist(),
        "fdr": skill.fdr_survivors(act.z).tolist(),
    }
    if lb is not None:
        sel["lb_pnl"] = lb[lb.list == "PNL"].nsmallest(TOP_K, "rank_i").proxyWallet.tolist()
        sel["lb_vol"] = lb[lb.list == "VOL"].nsmallest(TOP_K, "rank_i").proxyWallet.tolist()
    return sel


def evaluate(t2: pd.DataFrame, s2: pd.DataFrame, wallets: list[str], rows_cache: dict,
             meta: pd.DataFrame | None = None, label: str = "selection") -> dict:
    ws = [w for w in wallets if w in s2.index]
    own = s2.loc[ws]
    out = {"selected": len(wallets), "active_p2": len(ws),
           "p2_markets": int(own.markets.sum()), "p2_staked": float(own.staked.sum()),
           "p2_roi": float(own.pnl.sum() / own.staked.sum()) if len(ws) else np.nan,
           "p2_roi_net_fee": float(own.pnl_net.sum() / own.staked.sum()) if len(ws) else np.nan}
    if not ws:
        return out
    key = tuple(sorted(ws))
    if key not in rows_cache:
        rows = t2[t2.proxyWallet.isin(ws)]
        if "_groups" not in rows_cache:
            rows_cache["_groups"] = skill.build_groups(t2, meta=meta)
        summary = {}
        # Retain only per-event reductions, never an all-signal audit frame.
        for d in DELAYS:
            c = skill.copy_summary(t2, rows, d, groups=rows_cache["_groups"], label=label)['all']
            summary[f"copy_d{d}_roi"] = c["roi"]
            summary[f"copy_d{d}_ci"] = f"{c['ci_lo']:+.3f}..{c['ci_hi']:+.3f}"
            summary[f"copy_d{d}_trades"] = c["trades"]
            if d == 30:
                c = skill.copy_summary(t2, rows, d, stake="proportional", groups=rows_cache["_groups"], label=label)['all']
                summary["copy_d30_prop_roi"] = c["roi"]
                summary["copy_d30_prop_ci"] = f"{c['ci_lo']:+.3f}..{c['ci_hi']:+.3f}"
        rows_cache[key] = summary
    out.update(rows_cache[key])
    return out


def placebo(t2, s1, s2, n_draws=200, seed=3) -> pd.DataFrame:
    """Random groups of TOP_K active P1 wallets: their P2 own ROI (cheap) and copy ROI at d=30 (subset)."""
    rng = np.random.default_rng(seed)
    pool = s1[s1.markets >= MIN_MKTS].index.to_numpy()
    rows = []
    for i in range(n_draws):
        ws = [w for w in rng.choice(pool, size=min(TOP_K, len(pool)), replace=False) if w in s2.index]
        own = s2.loc[ws]
        capital = own.staked.sum()
        rows.append({"draw": i, "p2_roi": own.pnl.sum() / capital if capital > 0 else np.nan,
                     "p2_roi_net_fee": own.pnl_net.sum() / capital if capital > 0 else np.nan})
    return pd.DataFrame(rows)


def run(t: pd.DataFrame, split: str, lb: pd.DataFrame | None = None, label: str = "all", meta: pd.DataFrame | None = None) -> dict:
    ts = pd.Timestamp(split, tz="UTC").timestamp()
    t1 = t[_causal_cutoff(t, ts, meta)]
    t2 = t[t.timestamp >= ts]
    n1 = len(t1)
    s1 = skill.wallet_stats(skill.positions(t1))
    del t1
    # Reduce each period before materializing the other's positions. Copy replay
    # needs only the evaluation tape and these much smaller wallet summaries.
    s2 = skill.wallet_stats(skill.positions(t2))
    log.info("[%s] P1 %d wallets / %d fills, P2 %d wallets / %d fills", label, len(s1), n1, len(s2), len(t2))

    # does skill persist at all? rank correlation of P1 vs P2 z across wallets active in both
    both = s1[s1.markets >= MIN_MKTS].join(s2[s2.markets >= MIN_MKTS], lsuffix="_1", rsuffix="_2", how="inner")
    rho_z = spearmanr(both.z_1, both.z_2).statistic if len(both) > 10 else np.nan
    rho_roi = spearmanr(both.roi_1, both.roi_2).statistic if len(both) > 10 else np.nan
    # decile table: P1 z decile -> P2 ROI (stake-weighted)
    if len(both) >= 50:
        both["dec"] = pd.qcut(both.z_1.rank(method="first"), 10, labels=False) + 1
        dec = both.groupby("dec").apply(lambda x: pd.Series({
            "wallets": len(x), "p1_z_median": x.z_1.median(), "p1_roi": x.pnl_1.sum() / x.staked_1.sum(),
            "p2_roi": x.pnl_2.sum() / x.staked_2.sum(), "p2_roi_net_fee": x.pnl_net_2.sum() / x.staked_2.sum()}),
            include_groups=False).reset_index()
    else:
        dec = pd.DataFrame()

    if lb is not None:
        lb = lb.assign(rank_i=lb["rank"].astype(int))
    sel = selections(s1, lb)
    cache: dict = {}
    table = []
    for name, ws in sel.items():
        skill.log_progress(f'{label} rule={name} start', selected=len(ws), evaluation_fills=len(t2))
        r = evaluate(t2, s2, ws, cache, meta=meta, label=f'{label} rule={name}')
        p1 = s1.loc[[w for w in ws if w in s1.index]]
        r.update({"rule": name, "p1_roi": float(p1.pnl.sum() / p1.staked.sum()) if len(p1) else np.nan,
                  "p1_median_z": float(p1.z.median()) if len(p1) else np.nan})
        table.append(r)
        skill.log_progress(f'{label} rule={name} done', selected=len(ws))
    # the market as a whole: every P2 taker fill (what an average taker earns)
    everyone = {"rule": "all takers", "p2_roi": float(s2.pnl.sum() / s2.staked.sum()),
                "p2_roi_net_fee": float(s2.pnl_net.sum() / s2.staked.sum()), "active_p2": len(s2)}
    table.append(everyone)
    plc = placebo(t2, s1, s2)
    return {"label": label, "n_p1_wallets": len(s1), "n_p2_wallets": len(s2), "n_both": len(both),
            "rho_z": rho_z, "rho_roi": rho_roi, "deciles": dec, "rules": pd.DataFrame(table),
            "placebo": plc, "s1": s1, "s2": s2, "fdr_count": len(sel["fdr"]),
            "n_tested": int((s1.markets >= MIN_MKTS).sum())}


# ----------------------------------------------------------------------------- more angles

def big_trade_signal(t2: pd.DataFrame, thresholds=(1_000, 10_000, 50_000), delays=(5, 30, 60),
                     meta: pd.DataFrame | None = None) -> pd.DataFrame:
    """Copy every taker trade >= $X regardless of who placed it ("follow the whale money")."""
    usd = t2["size"] * t2.q
    gb = skill.build_groups(t2, meta=meta)
    rows = []
    for x in thresholds:
        sub = t2[usd >= x]
        if sub.empty:
            continue
        summaries = {d: skill.copy_summary(t2, sub, d, groups=gb, phases=("all","pregame","in_play"),
                                           diagnostic_move=d == 300, label=f'bigtrade min_usd={x}')
                     for d in dict.fromkeys((0,) + tuple(delays) + (300,))}
        for phase, m in [("all", slice(None)), ("pregame", ~sub.in_play), ("in_play", sub.in_play)]:
            part = sub[m] if not isinstance(m, slice) else sub
            rec = {"min_usd": x, "phase": phase, "trades": len(part),
                   "leader_roi": float(((part.y - part.q) * part["size"]).sum() / (part.q * part["size"]).sum())
                   if len(part) else np.nan}
            for d in (0,) + tuple(delays):
                c = summaries[d][phase]
                rec[f"copy_d{d}"] = c["roi"]
                rec[f"copy_d{d}_ci"] = f"{c['ci_lo']:+.3f}..{c['ci_hi']:+.3f}"
            # This is conditional on an allocated later order, not an exact-time markout.
            rec["funded_move_5_10min"] = summaries[300][phase]['funded_move_5_10min']
            rec["move_funded_signals"] = summaries[300][phase]['move_funded_signals']
            rows.append(rec)
    return pd.DataFrame(rows)


def walk_forward(t: pd.DataFrame, start: str, end: str, lookback_days: int = 180,
                 rules=("z", "whales", "random"), delays=(0, 30),
                 meta: pd.DataFrame | None = None) -> pd.DataFrame:
    """Each month: pick top-K wallets on the trailing window, copy their next-month trades.

    Positions are computed dynamically for the trailing window to avoid leaking
    fills or market outcomes that occur after the selection boundary.
    """
    months = pd.date_range(start, end, freq="MS", tz="UTC")
    rng = np.random.default_rng(17)
    out = []

    for m0, m1 in zip(months[:-1], months[1:]):
        a, b = m0.timestamp(), m1.timestamp()

        mask = _causal_cutoff(t, a, meta) & (t.timestamp >= a - lookback_days * 86400)

        hist_t = t[mask]
        nxt = t[(t.timestamp >= a) & (t.timestamp < b)]

        if hist_t.empty or nxt.empty:
            continue

        pos = skill.positions(hist_t)[["proxyWallet", "cost", "pnl", "var"]]
        s = pos.groupby("proxyWallet", observed=True).agg(markets=("cost", "size"), staked=("cost", "sum"),
                                                           pnl=("pnl", "sum"), var=("var", "sum"))
        s["z"] = s.pnl / np.sqrt(s["var"].clip(lower=1e-9))
        del hist_t, pos
        act = s[s.markets >= MIN_MKTS]
        picks = {"z": act.nlargest(TOP_K, "z").index, "whales": s.nlargest(TOP_K, "staked").index,
                 "random": act.index[rng.choice(len(act), size=min(TOP_K, len(act)), replace=False)]
                 if len(act) else act.index}
        if not any(len(picks[r]) for r in rules):
            continue
        # These are independent policies, not orders in one combined portfolio.
        # Reuse the immutable tape index, resetting liquidity for each policy.
        groups = skill.build_groups(nxt, meta=meta)
        for r in rules:
            chosen = nxt[nxt.proxyWallet.isin(picks[r])]
            if chosen.empty:
                continue
            for d in delays:
                c = skill.copy_summary(nxt, chosen, d, groups=groups, cash_details=True,
                                       label=f'month={m0:%Y-%m} rule={r}')['all']
                if not c['trades']:
                    continue
                out.append({"month": m0.strftime("%Y-%m"), "rule": r, "delay": d, "trades": c['trades'],
                            "pnl_per_$1": c['pnl'], "staked": c['capital'],
                            "in_play_share": c['in_play_share']})
    return pd.DataFrame(out)


def decompose_skilled(t: pd.DataFrame, split: str, delays=(0, 1, 2, 5, 30), meta: pd.DataFrame | None = None) -> pd.DataFrame:
    """The P1 FDR-significant wallets, copied in P2: timing (delay) x sizing (equal vs their $) x phase."""
    ts = pd.Timestamp(split, tz="UTC").timestamp()
    t1 = t[_causal_cutoff(t, ts, meta)]
    s1 = skill.wallet_stats(skill.positions(t1))
    del t1
    fdr = skill.fdr_survivors(s1[s1.markets >= MIN_MKTS].z).tolist()
    t2 = t[t.timestamp >= ts]
    rows = t2[t2.proxyWallet.isin(fdr)]
    mk = t2[t2.condition_id.isin(rows.condition_id.unique())]
    rows = mk[mk.proxyWallet.isin(fdr)]
    del t2
    groups = skill.build_groups(mk, meta=meta)
    summaries = {(stake,d): skill.copy_summary(mk, rows, d, stake=stake, groups=groups,
                        phases=('all','pregame','in_play'), label='decomposition')
                 for stake in ('proportional','equal') for d in delays}
    out = []
    for phase in ('all','pregame','in_play'):
        for stake in ("proportional", "equal"):
            for d in delays:
                c = summaries[stake,d][phase]
                out.append({"phase": phase, "stake": stake, "delay_s": d, "trades": c["trades"],
                            "copy_roi": c["roi"], "ci95": f"{c['ci_lo']:+.3f}..{c['ci_hi']:+.3f}"})
    df = pd.DataFrame(out)
    df.attrs.update(wallets=len(fdr), fills=len(rows), in_play_share=float(rows.in_play.mean()),
                    median_trade_usd=float((rows["size"] * rows.q).median()))
    return df
