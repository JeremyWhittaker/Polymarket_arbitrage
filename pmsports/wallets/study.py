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
MAX_COPY_ROWS = 150_000


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


def evaluate(t2: pd.DataFrame, s2: pd.DataFrame, wallets: list[str], rows_cache: dict) -> dict:
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
        if len(rows) > MAX_COPY_ROWS:   # sample whole events so the clustered CI stays honest
            ev = rows.event_slug.drop_duplicates().sample(frac=MAX_COPY_ROWS / len(rows), random_state=5)
            rows = rows[rows.event_slug.isin(ev)]
        if "_groups" not in rows_cache:
            rows_cache["_groups"] = skill.build_groups(t2)
        rows_cache[key] = skill.copy_prices(t2, rows, delays=DELAYS, groups=rows_cache["_groups"])
    rows = rows_cache[key]
    for d in DELAYS:
        c = skill.summarize_copy(skill.copy_returns(rows, d, stake="equal"))
        out[f"copy_d{d}_roi"] = c["roi"]
        out[f"copy_d{d}_ci"] = f"{c['ci_lo']:+.3f}..{c['ci_hi']:+.3f}"
        out[f"copy_d{d}_trades"] = c["trades"]
    # mirror their $ sizing too (big conviction bets weigh more)
    c = skill.summarize_copy(skill.copy_returns(rows, 30, stake="proportional"))
    out["copy_d30_prop_roi"], out["copy_d30_prop_ci"] = c["roi"], f"{c['ci_lo']:+.3f}..{c['ci_hi']:+.3f}"
    return out


def placebo(t2, s1, s2, n_draws=200, seed=3) -> pd.DataFrame:
    """Random groups of TOP_K active P1 wallets: their P2 own ROI (cheap) and copy ROI at d=30 (subset)."""
    rng = np.random.default_rng(seed)
    pool = s1[s1.markets >= MIN_MKTS].index.to_numpy()
    rows = []
    for i in range(n_draws):
        ws = [w for w in rng.choice(pool, size=min(TOP_K, len(pool)), replace=False) if w in s2.index]
        own = s2.loc[ws]
        rows.append({"draw": i, "p2_roi": own.pnl.sum() / own.staked.sum(),
                     "p2_roi_net_fee": own.pnl_net.sum() / own.staked.sum()})
    return pd.DataFrame(rows)


def run(t: pd.DataFrame, split: str, lb: pd.DataFrame | None = None, label: str = "all") -> dict:
    ts = pd.Timestamp(split, tz="UTC").timestamp()
    t1, t2 = t[t.timestamp < ts], t[t.timestamp >= ts]
    pos1, pos2 = skill.positions(t1), skill.positions(t2)
    s1, s2 = skill.wallet_stats(pos1), skill.wallet_stats(pos2)
    log.info("[%s] P1 %d wallets / %d fills, P2 %d wallets / %d fills", label, len(s1), len(t1), len(s2), len(t2))

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
        r = evaluate(t2, s2, ws, cache)
        p1 = s1.loc[[w for w in ws if w in s1.index]]
        r.update({"rule": name, "p1_roi": float(p1.pnl.sum() / p1.staked.sum()) if len(p1) else np.nan,
                  "p1_median_z": float(p1.z.median()) if len(p1) else np.nan})
        table.append(r)
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

def big_trade_signal(t2: pd.DataFrame, thresholds=(1_000, 10_000, 50_000), delays=(5, 30, 60)) -> pd.DataFrame:
    """Copy every taker trade >= $X regardless of who placed it ("follow the whale money")."""
    usd = t2["size"] * t2.q
    gb = skill.build_groups(t2)
    rows = []
    for x in thresholds:
        sub = t2[usd >= x]
        if sub.empty:
            continue
        cp = skill.copy_prices(t2, sub, delays=(0,) + tuple(delays), groups=gb)
        for phase, m in [("all", slice(None)), ("pregame", ~cp.in_play), ("in_play", cp.in_play)]:
            part = cp[m] if not isinstance(m, slice) else cp
            rec = {"min_usd": x, "phase": phase, "trades": len(part),
                   "leader_roi": float(((part.y - part.q) * part["size"]).sum() / (part.q * part["size"]).sum())
                   if len(part) else np.nan}
            for d in (0,) + tuple(delays):
                c = skill.summarize_copy(skill.copy_returns(part, d))
                rec[f"copy_d{d}"] = c["roi"]
                rec[f"copy_d{d}_ci"] = f"{c['ci_lo']:+.3f}..{c['ci_hi']:+.3f}"
            # information: does the side's price move the leader's way within 5 minutes?
            part5 = skill.copy_prices(t2, part.head(20000), delays=(300,), groups=gb)
            rec["price_move_5min"] = float((part5.q_d300 - part5.q).mean())
            rows.append(rec)
    return pd.DataFrame(rows)


def walk_forward(t: pd.DataFrame, start: str, end: str, lookback_days: int = 180,
                 rules=("z", "whales", "random"), delays=(0, 30), max_rows: int = 40_000) -> pd.DataFrame:
    """Each month: pick top-K wallets on the trailing window, copy their next-month trades.

    Positions are computed once (keyed by first-fill time); each month's selection is a
    vectorized sum over the trailing window, and one copy-price pass serves every rule.
    """
    pos = skill.positions(t)[["proxyWallet", "condition_id", "ts", "cost", "pnl", "var"]]
    months = pd.date_range(start, end, freq="MS", tz="UTC")
    rng = np.random.default_rng(17)
    out = []
    for m0, m1 in zip(months[:-1], months[1:]):
        a, b = m0.timestamp(), m1.timestamp()
        hist = pos[(pos.ts >= a - lookback_days * 86400) & (pos.ts < a)]
        nxt = t[(t.timestamp >= a) & (t.timestamp < b)]
        if hist.empty or nxt.empty:
            continue
        s = hist.groupby("proxyWallet", observed=True).agg(markets=("cost", "size"), staked=("cost", "sum"),
                                                           pnl=("pnl", "sum"), var=("var", "sum"))
        s["z"] = s.pnl / np.sqrt(s["var"].clip(lower=1e-9))
        act = s[s.markets >= MIN_MKTS]
        picks = {"z": act.nlargest(TOP_K, "z").index, "whales": s.nlargest(TOP_K, "staked").index,
                 "random": act.index[rng.choice(len(act), size=min(TOP_K, len(act)), replace=False)]
                 if len(act) else act.index}
        chosen = {r: nxt[nxt.proxyWallet.isin(picks[r])] for r in rules}
        chosen = {r: (x if len(x) <= max_rows else
                      x[x.event_slug.isin(x.event_slug.drop_duplicates().sample(
                          frac=max_rows / len(x), random_state=int(a) % 2**31))]) for r, x in chosen.items()}
        parts = [x.assign(_rule=r) for r, x in chosen.items() if len(x)]
        if not parts:
            continue
        allrows = pd.concat(parts)
        cp = skill.copy_prices(nxt, allrows, delays=tuple(delays))
        for r in rules:
            part = cp[cp._rule == r]
            for d in delays:
                rr = skill.copy_returns(part, d)
                if rr.empty:
                    continue
                out.append({"month": m0.strftime("%Y-%m"), "rule": r, "delay": d, "trades": len(rr),
                            "pnl_per_$1": float((rr.copy_roi * rr.w).sum()), "staked": float(rr.w.sum()),
                            "in_play_share": float(rr.in_play.mean())})
    return pd.DataFrame(out)


def decompose_skilled(t: pd.DataFrame, split: str, delays=(0, 1, 2, 5, 30)) -> pd.DataFrame:
    """The P1 FDR-significant wallets, copied in P2: timing (delay) x sizing (equal vs their $) x phase."""
    ts = pd.Timestamp(split, tz="UTC").timestamp()
    s1 = skill.wallet_stats(skill.positions(t[t.timestamp < ts]))
    fdr = skill.fdr_survivors(s1[s1.markets >= MIN_MKTS].z).tolist()
    t2 = t[t.timestamp >= ts]
    rows = t2[t2.proxyWallet.isin(fdr)]
    mk = t2[t2.condition_id.isin(rows.condition_id.unique())]
    rows = mk[mk.proxyWallet.isin(fdr)]
    cp = skill.copy_prices(mk, rows, delays=delays)
    out = []
    for phase, m in (("all", None), ("pregame", ~cp.in_play), ("in_play", cp.in_play)):
        part = cp if m is None else cp[m]
        for stake in ("proportional", "equal"):
            for d in delays:
                c = skill.summarize_copy(skill.copy_returns(part, d, stake=stake))
                out.append({"phase": phase, "stake": stake, "delay_s": d, "trades": c["trades"],
                            "copy_roi": c["roi"], "ci95": f"{c['ci_lo']:+.3f}..{c['ci_hi']:+.3f}"})
    df = pd.DataFrame(out)
    df.attrs.update(wallets=len(fdr), fills=len(rows), in_play_share=float(rows.in_play.mean()),
                    median_trade_usd=float((rows["size"] * rows.q).median()))
    return df
