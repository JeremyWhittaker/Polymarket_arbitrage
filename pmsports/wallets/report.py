"""python -m pmsports wallets-report  ->  reports/WALLETS.md (+ CSVs)

Runs every copy-trading angle per sport family and overall:
  persistence (P1 2025 -> P2 2026), selection rules vs placebo, big-trade signal,
  walk-forward delay curve (all fills and pregame-only), leaderboard on-chain profile.
"""
from __future__ import annotations

import logging
import hashlib
import json
import os
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

def make_manifest(split, walk_start, walk_end):
    manifest = {
        "split": split,
        "walk_start": walk_start,
        "walk_end": walk_end,
        "code_hash": get_code_hash(),
        "inputs": {}
    }
    inputs = [OUT / "universe.parquet", OUT / "leaderboard.parquet", OUT / "leaderboard_onchain_2025.parquet"]
    from .tapes import TAPES
    if TAPES.exists():
        inputs.extend(sorted(TAPES.glob("*.parquet")))

    for f in inputs:
        if f.exists():
            st = f.stat()
            manifest["inputs"][str(f)] = {"mtime_ns": st.st_mtime_ns, "size": st.st_size}
    return manifest

def get_code_hash():
    h = hashlib.sha256()
    parent = Path(__file__).parent
    deps = [parent / "study.py", parent / "skill.py", parent / "report.py",
            parent / "tapes.py", parent / "universe.py", parent.parent / "polymarket.py",
            parent.parent / "execution.py"]
    for p in deps:
        if p.exists():
            h.update(p.read_bytes())
    return h.hexdigest()

def run(split: str = SPLIT, walk_start: str = "2025-07-01", walk_end: str | None = None) -> None:
    REPORTS.mkdir(exist_ok=True)
    walk_end = walk_end or pd.Timestamp.now(tz="UTC").strftime("%Y-%m-01")
    u = pd.read_parquet(OUT / "universe.parquet")
    meta = u.drop_duplicates("condition_id").set_index("condition_id")
    lb = pd.read_parquet(OUT / "leaderboard.parquet")
    t = load_trades(u)
    raw_rows = len(t)
    t = study.skill.valid_trades(t)
    log.info("excluded %d invalid economic/source rows before wallet analysis", raw_rows-len(t))
    log.info("loaded %d fills, %d wallets, %d markets", len(t), t.proxyWallet.nunique(), t.condition_id.nunique())

    cache = OUT / "report_cache"
    cache.mkdir(exist_ok=True)

    manifest = make_manifest(split, walk_start, walk_end)
    manifest_hash = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    run_id = manifest_hash[:12]

    manifest_path = cache / f"manifest_{run_id}.json"
    if not manifest_path.exists():
        tmp_man = cache / f"manifest_{run_id}.json.{os.getpid()}.tmp"
        with open(tmp_man, "w") as fh:
            json.dump(manifest, fh, sort_keys=True, indent=2)
        tmp_man.rename(manifest_path)

    def cached(name, fn):
        f = cache / f"{name}_{run_id}.pkl"
        if f.exists():
            return pd.read_pickle(f)
        obj = fn()
        tmp = cache / f"{name}_{run_id}.pkl.{os.getpid()}.tmp"
        pd.to_pickle(obj, tmp)
        tmp.rename(f)
        return obj

    fams, rules, decs = cached("families", lambda: _families(t, lb, split, meta))
    t2 = t[t.timestamp >= pd.Timestamp(split, tz="UTC").timestamp()]
    log.info("big-trade signal")
    big = cached("bigtrades", lambda: study.big_trade_signal(t2, meta=meta))
    del t2
    log.info("walk-forward")
    wf = cached("walkforward", lambda: _walk(t, walk_start, walk_end, meta))
    log.info("skilled-wallet decomposition")
    dec_sk = cached("skilled_decomp", lambda: study.decompose_skilled(t, split, meta=meta))
    wfa = _aggregate_walk(wf)
    _walk_cash_columns(wf).to_csv(REPORTS / "wallets_walkforward_monthly.csv", index=False)

    for name, df in [("wallets_families", fams), ("wallets_rules", rules), ("wallets_deciles", decs),
                     ("wallets_bigtrades", big), ("wallets_walkforward", wfa), ("wallets_skilled_decomp", dec_sk)]:
        df.to_csv(REPORTS / f"{name}.csv", index=False)
    (REPORTS / "WALLETS.md").write_text(_render(t, fams, rules, decs, big, wfa, lb, split, dec_sk, run_id))
    log.info("wrote %s", REPORTS / "WALLETS.md")


def _walk_cash_columns(wf):
    """Legacy backend name now contains actual USD, never a hypothetical$1 return."""
    return wf.rename(columns={"pnl_per_$1":"pnl_usd", "staked":"capital_usd"}).copy()


def _aggregate_walk(wf):
    columns = ["scope", "rule", "delay", "months_with_fills", "trades", "pnl_usd", "capital_usd", "copy_roi", "filled_months_positive"]
    if wf.empty:
        return pd.DataFrame(columns=columns)
    cash = _walk_cash_columns(wf)
    out = cash.groupby(["scope", "rule", "delay"]).agg(
        months_with_fills=("pnl_usd", "size"), trades=("trades", "sum"),
        pnl_usd=("pnl_usd", "sum"), capital_usd=("capital_usd", "sum"),
        filled_months_positive=("pnl_usd", lambda x: x.gt(0).mean())).reset_index()
    out["copy_roi"] = out.pnl_usd.div(out.capital_usd.where(out.capital_usd > 0))
    return out[columns]


def _walk(t, walk_start, walk_end, meta) -> pd.DataFrame:
    wf = [study.walk_forward(t, walk_start, walk_end, delays=(0, 1, 5, 30), meta=meta).assign(scope="all"),
          study.walk_forward(t[~t.in_play], walk_start, walk_end, rules=("z", "random"),
                             delays=(0, 60, 300), meta=meta).assign(scope="pregame")]
    for fam in ("soccer", "tennis", "basketball", "baseball", "esports", "hockey", "american_football"):
        tf = t[t.family == fam]
        if len(tf) > 100_000:
            wf.append(study.walk_forward(tf, walk_start, walk_end, rules=("z", "random"),
                                         delays=(0, 5, 30), meta=meta).assign(scope=fam))
    parts = [w for w in wf if len(w)]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def _families(t, lb, split, meta):
    fam_rows, rule_tables, deciles = [], [], []
    for fam in ["ALL"] + FAMILIES:
        tf = t if fam == "ALL" else t[t.family == fam]
        if tf.timestamp.min() >= pd.Timestamp(split, tz="UTC").timestamp() or len(tf) < 50_000:
            continue
        r = study.run(tf, split, lb if fam == "ALL" else None, fam, meta=meta)
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
    rules = pd.concat(rule_tables, ignore_index=True) if rule_tables else pd.DataFrame(columns=["family"])
    decs = pd.concat(deciles, ignore_index=True) if deciles else pd.DataFrame()
    return fams, rules, decs


def _render(t, fams, rules, decs, big, wfa, lb, split, dec_sk=None, run_id="") -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    L = [f"# Can you copy skilled sports bettors on Polymarket? ({now})", "",
         f"Generated by `python -m pmsports wallets-report` (Run ID: {run_id}). Data: taker fills from bounded/selected historical tapes for "
         f"{t.condition_id.nunique():,} resolved sports moneyline markets in a legacy volume-selected corpus with bounded backfill, "
         f"{len(t):,} fills from {t.proxyWallet.nunique():,} wallets. Selection uses only data before "
         f"{split}, using Gamma closure before ranking as an analytical outcome-availability proxy; evaluation uses later fills. A follower enters strictly after "
         "the signal plus d seconds at the first other-wallet same-side transaction proxy, capped by remaining shares, and pays the "
         "market's taker fee; positions are held to resolution. `z` = P&L / its standard deviation if "
         "every price were fair (luck-adjusted skill).", "",
         "Follower targets are$100 inclusive of fees for the equal-target policy, or1% of observable leader notional "
         "capped$100 for proportional sizing. Each independent replay caps total invested capital at$100 per event. "
         "Actual allocations can be much smaller: summaries weight by allocated capital and never invent a full target "
         "for a tiny print. Public receipt latency and resting book depth are unobserved; delay0 still requires a later "
         "other-wallet print, not retroactive purchase at the signal. Previously explored2026 windows are not fresh confirmation.", ""]
    L += ["New copied entries expire at the earlier of their delay/horizon deadline and the market's known Gamma closure. "
          "Missing closure makes a signal ineligible, with zero allocation; signals and no-fills are retained. "
          "Gamma closure is an analytical cutoff, not an independently measured public resolution announcement or receipt clock. "
          "Raw observations and historical wallet rankings are not filtered by this entry cutoff.", ""]
    L += ["## 1. Does skill persist from 2025 to 2026?", "",
          "`rho` = rank correlation of a wallet's 2025 and 2026 z/ROI (wallets with >= 30 markets in both). "
          "`fdr_survivors` = wallets passing a normal-tail, fair-price-model screen with Benjamini–Hochberg "
          "at nominal 5%. This model-based screen does not prove skill or guarantee realized FDR with "
          "dependent markets and imperfectly calibrated tails. `expected_z>3_by_luck` is the model's "
          "expected count under its assumptions, not an empirical count of lucky wallets.", "",
          _md(fams), ""]
    if len(decs):
        L += ["2025 z-decile -> 2026 ROI (all sports):", "", _md(decs[decs.family == "ALL"].drop(columns="family")), ""]
    cols = ["family", "rule", "active_p2", "p1_roi", "p1_median_z", "p2_roi", "p2_roi_net_fee", "copy_d0_roi",
            "copy_d5_roi", "copy_d30_roi", "copy_d30_ci", "copy_d60_roi", "copy_d30_prop_roi", "copy_d30_trades"]
    cols = [c for c in cols if c in rules.columns]
    L += ["## 2. Selection rules: pick on 2025, copy in 2026", "",
          "`p2_roi` = the selected wallets' own 2026 return; `copy_dN_roi` = a follower's return copying "
          "them N seconds late ($100 target subject to print size/event cap; `prop` =1% of known leader notional capped$100). "
          "`p2_roi` is a descriptive leader return, not a realizable follower entry. `lb_*` rows use "
          "Polymarket's all-time leaderboard, which already includes 2026 results (retrospective/observational, excluded from causal strategy conclusion).", "",
          _md(rules[rules.family == "ALL"][cols].drop(columns="family", errors="ignore")), "",
          "Per sport family (d = 30s):", "",
          _md(rules[rules.family != "ALL"].reindex(columns=["family", "rule", "active_p2", "p2_roi", "copy_d0_roi",
                                            "copy_d30_roi", "copy_d30_ci"])), ""]
    L += ["## 3. Walk-forward: re-select monthly, copy next month", "",
          "Each month pick the top 50 wallets on the trailing 180 days (by z, or biggest $ = whales, "
          "or random), copy their next-month trades. `pregame` scope uses only pre-game fills "
          "rather than asserting public receipt/depth feasibility. Each monthly replay resets the event cap: the policy is "
          "$100 per event per month, not a single lifetime cap across month boundaries. Independent selection rules "
          "and delay comparisons each reset liquidity; they are not combined portfolios. `pnl_usd` is actual modeled "
          "dollar P&L, `capital_usd` includes fees, and `copy_roi` is their ratio. The old backend field `pnl_per_$1` "
          "contains these dollars and is renamed on export. Months with no fills are omitted by the backend, so "
          "`filled_months_positive` uses only months with fills; it is not an all-calendar-month success rate. "
          "The monthly audit CSV preserves cash totals. Every selected signal is replayed without future-event "
          "sampling or row truncation; the full FDR ledger also includes all signals. Invalid price, size, "
          "fee, side or payout inputs cannot rank a wallet or allocate a copied order.", "", _md(wfa), ""]
    bc = ["min_usd", "phase", "trades", "leader_roi", "copy_d0", "copy_d5", "copy_d30", "copy_d30_ci",
          "copy_d60", "funded_move_5_10min", "move_funded_signals"]
    L += ["## 4. Follow the big money (any wallet, 2026)", "",
          "Copy every taker trade >= $X. `funded_move_5_10min` is the unweighted mean copied-entry "
          "price minus leader signal price, only for actually funded equal-target orders. The candidate "
          "print must be strictly after signal+300 seconds and before signal+600 seconds: the300-second "
          "horizon begins after the300-second delay. `move_funded_signals` counts that cohort. Event caps, "
          "shared print capacity and no-fills determine which observations enter this conditional diagnostic; "
          "it is neither an exact-five-minute markout nor an unconditional test of information.", "",
          _md(big[[c for c in bc if c in big.columns]], ".4f"), ""]
    if dec_sk is not None and len(dec_sk):
        a = dec_sk.attrs
        L += ["## 6. Historically selected wallets: timing and allocation sensitivity", "",
              f"The {a.get('wallets', '?')} wallets whose 2025 z passes the model-based FDR screen, copied on their "
              f"{a.get('fills', 0):,} fills in 2026 ({a.get('in_play_share', 0):.0%} in-play, median fill "
              f"${a.get('median_trade_usd', 0):.0f}). `proportional` uses1% of the observed leader ticket capped$100 "
              "and subject to the same event/print constraints. Delay starts from the historical transaction timestamp; "
              "the real public receipt delay is not measured here. These results do not prove predictive skill or execution feasibility.", "",
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
