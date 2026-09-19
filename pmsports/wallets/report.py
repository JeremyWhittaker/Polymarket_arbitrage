"""python -m pmsports wallets-report  ->  reports/WALLETS.md (+ CSVs)

Runs every copy-trading angle per sport family and overall:
  persistence (P1 2025 -> P2 2026), selection rules vs placebo, big-trade signal,
  walk-forward delay curve (all fills and pregame-only), leaderboard on-chain profile.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import study
from .tapes import load_trades
from .universe import OUT

log = logging.getLogger("pmsports")
REPORTS = Path(__file__).resolve().parents[2] / "reports"
FAMILIES = ["soccer", "tennis", "esports", "basketball", "baseball", "hockey", "american_football",
            "cricket", "mma_boxing"]
SPLIT = "2026-01-01"


def _md(df, fmt=".3f"):
    return df.to_markdown(index=False, floatfmt=fmt) if len(df) else "_no data_"


def run(split: str = SPLIT, walk_start: str = "2025-07-01", walk_end: str | None = None) -> None:
    REPORTS.mkdir(exist_ok=True)
    walk_end = walk_end or pd.Timestamp.now(tz="UTC").strftime("%Y-%m-01")
    u = pd.read_parquet(OUT / "universe.parquet")
    lb = pd.read_parquet(OUT / "leaderboard.parquet")
    t = load_trades(u)
    log.info("loaded %d fills, %d wallets, %d markets", len(t), t.proxyWallet.nunique(), t.condition_id.nunique())

    cache = OUT / "report_cache"
    cache.mkdir(exist_ok=True)

    def cached(name, fn):
        f = cache / f"{name}.pkl"
        if f.exists():
            return pd.read_pickle(f)
        obj = fn()
        pd.to_pickle(obj, f)
        return obj

    fams, rules, decs = cached("families", lambda: _families(t, lb, split))
    t2 = t[t.timestamp >= pd.Timestamp(split, tz="UTC").timestamp()]
    log.info("big-trade signal")
    big = cached("bigtrades", lambda: study.big_trade_signal(t2))
    del t2
    log.info("walk-forward")
    wf = cached("walkforward", lambda: _walk(t, walk_start, walk_end))
    log.info("skilled-wallet decomposition")
    dec_sk = cached("skilled_decomp", lambda: study.decompose_skilled(t, split))
    wfa = wf.groupby(["scope", "rule", "delay"]).apply(lambda x: pd.Series({
        "months": len(x), "trades": x.trades.sum(), "copy_roi": x["pnl_per_$1"].sum() / x.staked.sum(),
        "months_positive": (x["pnl_per_$1"] > 0).mean()}), include_groups=False).reset_index()

    for name, df in [("wallets_families", fams), ("wallets_rules", rules), ("wallets_deciles", decs),
                     ("wallets_bigtrades", big), ("wallets_walkforward", wfa), ("wallets_skilled_decomp", dec_sk)]:
        df.to_csv(REPORTS / f"{name}.csv", index=False)
    (REPORTS / "WALLETS.md").write_text(_render(t, fams, rules, decs, big, wfa, lb, split, dec_sk))
    log.info("wrote %s", REPORTS / "WALLETS.md")


def _walk(t, walk_start, walk_end) -> pd.DataFrame:
    wf = [study.walk_forward(t, walk_start, walk_end, delays=(0, 1, 5, 30)).assign(scope="all"),
          study.walk_forward(t[~t.in_play], walk_start, walk_end, rules=("z", "random"),
                             delays=(0, 60, 300)).assign(scope="pregame")]
    for fam in ("soccer", "tennis", "basketball", "baseball", "esports", "hockey", "american_football"):
        tf = t[t.family == fam]
        if len(tf) > 100_000:
            wf.append(study.walk_forward(tf, walk_start, walk_end, rules=("z", "random"),
                                         delays=(0, 5, 30)).assign(scope=fam))
    return pd.concat([w for w in wf if len(w)], ignore_index=True)


def _families(t, lb, split):
    fam_rows, rule_tables, deciles = [], [], []
    for fam in ["ALL"] + FAMILIES:
        tf = t if fam == "ALL" else t[t.family == fam]
        if tf.timestamp.min() >= pd.Timestamp(split, tz="UTC").timestamp() or len(tf) < 50_000:
            continue
        r = study.run(tf, split, lb if fam == "ALL" else None, fam)
        plc = r["placebo"]
        fam_rows.append({"family": fam, "fills": len(tf), "p1_wallets": r["n_p1_wallets"],
                         "p2_wallets": r["n_p2_wallets"], "active_both": r["n_both"],
                         "tested_p1": r["n_tested"], "fdr_survivors": r["fdr_count"],
                         "expected_z>3_by_luck": round(r["n_tested"] * 0.00135, 1),
                         "rho_z": r["rho_z"], "rho_roi": r["rho_roi"],
                         "placebo_p2_roi": plc.p2_roi.mean(),
                         "placebo_p2_roi_5_95": f"{plc.p2_roi.quantile(.05):+.3f}..{plc.p2_roi.quantile(.95):+.3f}"})
        rt = r["rules"].assign(family=fam)
        rule_tables.append(rt)
        if len(r["deciles"]):
            deciles.append(r["deciles"].assign(family=fam))
        del r
    fams = pd.DataFrame(fam_rows)
    rules = pd.concat(rule_tables, ignore_index=True)
    decs = pd.concat(deciles, ignore_index=True) if deciles else pd.DataFrame()
    return fams, rules, decs


def _render(t, fams, rules, decs, big, wfa, lb, split, dec_sk=None) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    L = [f"# Can you copy skilled sports bettors on Polymarket? ({now})", "",
         "Generated by `python -m pmsports wallets-report`. Data: every taker fill (wallet-attributed) in "
         f"{t.condition_id.nunique():,} resolved sports moneyline markets with >= $50k volume, "
         f"{len(t):,} fills from {t.proxyWallet.nunique():,} wallets. Selection uses only data before "
         f"{split}; evaluation only data after (no look-ahead). A follower copies each trade at the first "
         "*other* taker print on the same side >= d seconds later (an executable price) and pays the "
         "market's taker fee; positions are held to resolution. `z` = P&L / its standard deviation if "
         "every price were fair (luck-adjusted skill).", ""]
    L += ["## 1. Does skill persist from 2025 to 2026?", "",
          "`rho` = rank correlation of a wallet's 2025 and 2026 z/ROI (wallets with >= 30 markets in both). "
          "`fdr_survivors` = 2025 wallets whose z is significant after correcting for testing thousands "
          "of wallets; `expected_z>3_by_luck` is how many would clear z > 3 with zero skill.", "",
          _md(fams), ""]
    if len(decs):
        L += ["2025 z-decile -> 2026 ROI (all sports):", "", _md(decs[decs.family == "ALL"].drop(columns="family")), ""]
    cols = ["family", "rule", "active_p2", "p1_roi", "p1_median_z", "p2_roi", "p2_roi_net_fee", "copy_d0_roi",
            "copy_d5_roi", "copy_d30_roi", "copy_d30_ci", "copy_d60_roi", "copy_d30_prop_roi", "copy_d30_trades"]
    cols = [c for c in cols if c in rules.columns]
    L += ["## 2. Selection rules: pick on 2025, copy in 2026", "",
          "`p2_roi` = the selected wallets' own 2026 return; `copy_dN_roi` = a follower's return copying "
          "them N seconds late (equal $ per trade; `prop` = mirroring their $ sizes). `lb_*` rows use "
          "Polymarket's all-time leaderboard, which already includes 2026 results (biased upward).", "",
          _md(rules[rules.family == "ALL"][cols].drop(columns="family")), "",
          "Per sport family (d = 30s):", "",
          _md(rules[rules.family != "ALL"][["family", "rule", "active_p2", "p2_roi", "copy_d0_roi",
                                            "copy_d30_roi", "copy_d30_ci"]]), ""]
    L += ["## 3. Walk-forward: re-select monthly, copy next month", "",
          "Each month pick the top 50 wallets on the trailing 180 days (by z, or biggest $ = whales, "
          "or random), copy their next-month trades. `pregame` scope uses only pre-game fills "
          "(no speed race: lines move slowly before the game).", "", _md(wfa), ""]
    bc = ["min_usd", "phase", "trades", "leader_roi", "copy_d0", "copy_d5", "copy_d30", "copy_d30_ci",
          "copy_d60", "price_move_5min"]
    L += ["## 4. Follow the big money (any wallet, 2026)", "",
          "Copy every taker trade >= $X. `price_move_5min` = how far the side's price moved the leader's "
          "way within 5 minutes (informed money moves prices; ~0 means no information).", "",
          _md(big[[c for c in bc if c in big.columns]], ".4f"), ""]
    if dec_sk is not None and len(dec_sk):
        a = dec_sk.attrs
        L += ["## 6. The genuinely skilled wallets: why copying them still fails", "",
              f"The {a.get('wallets', '?')} wallets whose 2025 z survives the FDR correction, copied on their "
              f"{a.get('fills', 0):,} fills in 2026 ({a.get('in_play_share', 0):.0%} in-play, median fill "
              f"${a.get('median_trade_usd', 0):.0f}). `proportional` mirrors their $ size; delay is seconds after "
              "their fill. Wallet identity is only visible after on-chain settlement (~2.6 s median), so any "
              "real follower has delay >= ~3 s.", "",
              _md(dec_sk[dec_sk.phase == "all"].drop(columns="phase"), ".4f"), "",
              "By phase (proportional stake):", "",
              _md(dec_sk[(dec_sk.phase != "all") & (dec_sk.stake == "proportional")].drop(columns="stake"), ".4f"), ""]
    prof_f = OUT / "leaderboard_onchain_2025.parquet"
    if prof_f.exists():
        lbi = lb.assign(rank_i=lb["rank"].astype(int))
        top = pd.concat([lbi[lbi.list == "PNL"].nsmallest(100, "rank_i"), lbi[lbi.list == "VOL"].nsmallest(100, "rank_i")])
        m = top.merge(pd.read_parquet(prof_f), left_on="proxyWallet", right_index=True, how="left")
        m["margin"] = m.pnl / m.vol
        m["kind"] = pd.cut(m.taker_share, [-.01, .25, .75, 1.01],
                           labels=["mostly maker (MM-like)", "mixed", "mostly taker (directional)"])
        m["kind"] = m.kind.cat.add_categories("no 2025 on-chain activity").fillna("no 2025 on-chain activity")
        g = m.groupby(["list", "kind"], observed=True).agg(wallets=("pnl", "size"), median_alltime_pnl=("pnl", "median"),
                                                          median_margin_pnl_over_vol=("margin", "median")).reset_index()
        L += ["## 5. Who is on the leaderboard? (on-chain, 2025)", "",
              "Top 100 all-time sports wallets by P&L and by volume, classified by the share of their 2025 "
              "on-chain volume traded as taker (CryptoHouse OrderFilled events). Makers earn the spread and "
              "rebates; their edge cannot be copied by a taker.", "", _md(g, ".4f"), ""]
    return "\n".join(L)
