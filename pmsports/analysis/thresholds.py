"""Threshold strategies.

Part A  Pregame: bet the team whenever its pregame price >= T (T = 50c..90c), per sport.
Part B  MLB in-game: buy the LEADING team at a half-inning start when its traded price is at
        least X points below the historical win rate for that state (inning, half, lead,
        home/away) - the "it usually wins 98% but it's trading at 93" rule. Fair value comes
        only from seasons before the game's season (no look-ahead). A second version uses a
        fair value that also knows the pregame odds (team strength), fitted on 2025, tested 2026.

python -m pmsports thresholds -> reports/THRESHOLDS.md
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm

from ..collect import sport_dir
from ..polymarket import taker_fee
from ..execution import panel_entries
from ..research.common import cluster_ci
from ..wallets.universe import OUT
from .favorites import PREGAME_MIN_USD, favorite_bets, cached_pregame_prices
from .hypotheses import _baseline_we, _features

log = logging.getLogger("pmsports")
REPORTS = Path(__file__).resolve().parents[2] / "reports"
THRESH = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90]
DISCOUNTS = [0.0, 0.02, 0.03, 0.05, 0.08, 0.10]


def _roi(price, won, fee_rate, slip=0.0):
    c = np.clip(np.asarray(price, float) + slip, 0.001, 0.999)
    f = taker_fee(1.0, c, np.nan_to_num(np.asarray(fee_rate, float)))
    return (np.asarray(won, float) - c - f) / (c + f)


def _ci(x, n_boot=2000, seed=2):
    x = np.asarray(x, float)
    if len(x) < 20:
        return ""
    rng = np.random.default_rng(seed)
    m = x[rng.integers(0, len(x), (n_boot, len(x)))].mean(1)
    return f"{np.percentile(m, 2.5):+.3f}..{np.percentile(m, 97.5):+.3f}"


# ----------------------------------------------------------------------------- Part A

def pregame_thresholds() -> dict[str, pd.DataFrame]:
    m = cached_pregame_prices()
    b = favorite_bets(m)
    b = b[b.pre_usd >= PREGAME_MIN_USD].copy()
    n_signals = len(b)
    b = b[b.fav_ask.notna() & b.fav_stake.gt(0)].copy()
    b["entry"] = b.fav_ask
    b["roi"] = _roi(b.entry, b.fav_won, b.fee_rate)
    b["roi_nofee"] = _roi(b.fav_p, b.fav_won, 0.0)

    def row(g, label):
        return {"games": len(g), "avg_price": g.fav_p.mean(), "win_rate": g.fav_won.mean(),
                "win_minus_price": g.fav_won.mean() - g.fav_p.mean(), "roi_before_costs": g.roi_nofee.mean(),
                "roi_after_fees": np.average(g.roi, weights=g.fav_stake) if len(g) else np.nan, "ci95": _weighted_ci(g.roi, g.event_slug, g.fav_stake), "actual_proxy_pnl_usd": (g.roi * g.fav_stake).sum(), "capital_usd": g.fav_stake.sum(), **label}

    cum = [row(b[b.fav_p >= t], {"threshold": f">= {t:.0%}"}) for t in THRESH]
    bands = [row(b[(b.fav_p >= lo) & (b.fav_p < hi)], {"band": f"{lo:.0%}-{hi:.0%}"})
             for lo, hi in zip(THRESH, THRESH[1:] + [1.0])]
    grid = []
    for fam, g in b.groupby("family"):
        if len(g) < 100:
            continue
        rec = {"sport": fam, "games": len(g)}
        for t in THRESH:
            s = g[g.fav_p >= t]
            rec[f">={t:.0%}"] = np.average(s.roi, weights=s.fav_stake) if len(s) >= 30 else np.nan
            rec[f"n>={t:.0%}"] = len(s)
        grid.append(rec)
    grid = pd.DataFrame(grid)
    # significance per cell (is any sport x threshold reliably positive?)
    cells = []
    for fam, g in b.groupby("family"):
        for t in THRESH:
            s = g[g.fav_p >= t]
            if len(s) >= 30:
                cells.append({"sport": fam, "threshold": f">= {t:.0%}", "games": len(s),
                              "roi_after_fees": np.average(s.roi, weights=s.fav_stake), "ci95": _weighted_ci(s.roi, s.event_slug, s.fav_stake)})
    cells = pd.DataFrame(cells)
    return {"cumulative": pd.DataFrame(cum)[["threshold"] + [c for c in cum[0] if c != "threshold"]],
            "bands": pd.DataFrame(bands)[["band"] + [c for c in bands[0] if c != "band"]],
            "by_sport": grid, "cells": cells, "n": len(b), "signals": n_signals}


# ----------------------------------------------------------------------------- Part B

def _prior_season_fair(panel: pd.DataFrame, baseline: pd.DataFrame) -> np.ndarray:
    """Historical home win rate for each row's state, from seasons strictly before its own."""
    fair = np.full(len(panel), np.nan)
    seasons = panel.event_date.str[:4].astype(int)
    for s in sorted(seasons.unique()):
        idx = np.flatnonzero(seasons.to_numpy() == s)
        rows = panel.iloc[idx].copy()
        fair[idx] = _baseline_we(baseline, rows, max_season=s - 1)
    return fair


def _weighted_ci(roi, event, weights):
    if len(roi) == 0:
        return ""
    _, lo, hi = cluster_ci(roi, np.asarray(event), weights=np.asarray(weights))
    return f"{lo:+.3f}..{hi:+.3f}"


def _execution_summary(g, home, slip=.01, cap=100.):
    r = panel_entries(g, home, slip=slip, event_cap_usd=cap)
    fill = r[r.cost_usd > 0]
    mean, lo, hi = cluster_ci(fill.roi, fill.event.to_numpy(), weights=fill.cost_usd.to_numpy()) if len(fill) else (np.nan,) * 3
    return dict(signals=len(r), n=len(fill), unfilled=int((r.cost_usd == 0).sum()),
                partial=int(r.status.eq("partial").sum()), avg_price=fill.entry_price.mean(),
                actual_win_rate=fill.y.mean(), capital_usd=fill.cost_usd.sum(), pnl_usd=fill.pnl_usd.sum(),
                roi_after_fees_1c=mean, ci95=f"{lo:+.3f}..{hi:+.3f}")


def inning_discounts() -> dict[str, pd.DataFrame]:
    d = sport_dir("mlb")
    panel = pd.read_parquet(d / "panel.parquet")
    baseline = pd.read_parquet(d / "baseline.parquet")
    p = panel[panel.checkpoint & panel.mkt_p.notna() & panel.mkt_staleness.le(120) & (panel["diff"] != 0)].copy()
    p = p.sort_values(["game_pk", "state_ts"]).reset_index(drop=True)
    p["fair_home"] = _prior_season_fair(p, baseline)
    home_leads = p["diff"] > 0
    p["lead"], p["inn"] = p["diff"].abs().clip(upper=5), p.inning.clip(upper=10)
    p["price_leader"] = np.where(home_leads, p.mkt_p, 1 - p.mkt_p)
    p["fair_leader"] = np.where(home_leads, p.fair_home, 1 - p.fair_home)
    p["pre_leader"] = np.where(home_leads, p.pre_p, 1 - p.pre_p)
    p["leader_won"] = np.where(home_leads, p.home_won_final, 1 - p.home_won_final)
    p["discount"] = p.fair_leader - p.price_leader
    desc, rules = [], []
    for x in DISCOUNTS:
        s = p[p.discount >= x]
        desc.append(dict(discount_at_least=x, checkpoints=len(s), games=s.game_pk.nunique(),
            avg_hist_win_rate=s.fair_leader.mean(), avg_price=s.price_leader.mean(),
            actual_win_rate=s.leader_won.mean(), leader_was_pregame_underdog=s.pre_leader.lt(.5).mean(),
            avg_leader_pregame_price=s.pre_leader.mean()))
        for label, g in (("first signal per game", s.drop_duplicates("game_pk")),
                         ("every signal;100 inclusive-dollar event cap", s)):
            rules.append(dict(discount_at_least=x, bets=label, hist_win_rate=g.fair_leader.mean(),
                              **_execution_summary(g, g["diff"] > 0)))
    first = p[p.discount >= .03].drop_duplicates("game_pk")
    cells = []
    for (inn, lead), g in first.groupby(["inn", "lead"]):
        cells.append(dict(inn=inn, lead=lead, **_execution_summary(g, g["diff"] > 0)))
    p["we"], p["y"] = p.fair_home, p.home_won_final.astype(float)
    model_ok = np.isfinite(_features(p)).all(axis=1) & p.pre_p.between(.02, .98)
    model_rows = p.loc[model_ok]
    tr, te = model_rows[model_rows.event_date < "2026-01-01"], model_rows[model_rows.event_date >= "2026-01-01"].copy()
    adj = []
    if len(tr) >= 100 and len(te):
        X = _features(tr)
        cols = [c for c in X if X[c].std() > 1e-9]
        fit = sm.Logit(tr.y.to_numpy(), sm.add_constant(X[cols], has_constant="add")).fit(disp=0)
        te["model_home"] = fit.predict(sm.add_constant(_features(te)[cols], has_constant="add"))
        te["edge_home"] = te.model_home - te.mkt_p
        for x in DISCOUNTS[1:]:
            for label, leading in (("leader", True), ("trailer", False)):
                buy_home = (te["diff"] > 0) == leading
                edge = np.where(buy_home, te.edge_home, -te.edge_home)
                g = te[edge >= x].drop_duplicates("game_pk")
                adj.append(dict(model_edge_at_least=x, buy=label,
                    **_execution_summary(g, (g["diff"] > 0) == leading)))
    return dict(describe=pd.DataFrame(desc), rules=pd.DataFrame(rules), cells=pd.DataFrame(cells),
                adjusted=pd.DataFrame(adj), n_checkpoints=len(p), n_games=p.game_pk.nunique(),
                model_input_exclusions=int((~model_ok).sum()))


def _ci_cluster(g: pd.DataFrame, n_boot=1000, seed=3) -> str:
    e = g.groupby("game_pk").roi.agg(["sum", "size"])
    if len(e) < 20:
        return ""
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(e), (n_boot, len(e)))
    m = e["sum"].to_numpy()[idx].sum(1) / e["size"].to_numpy()[idx].sum(1)
    return f"{np.percentile(m, 2.5):+.3f}..{np.percentile(m, 97.5):+.3f}"


def run() -> None:
    a = pregame_thresholds()
    b = inning_discounts()
    for k in ("cumulative", "bands", "by_sport", "cells"):
        a[k].to_csv(REPORTS / f"thresholds_pregame_{k}.csv", index=False)
    for k in ("describe", "rules", "cells", "adjusted"):
        b[k].to_csv(REPORTS / f"thresholds_inning_{k}.csv", index=False)
    (REPORTS / "THRESHOLDS.md").write_text(_render(a, b))
    log.info("wrote %s", REPORTS / "THRESHOLDS.md")


def _md(df, fmt=".3f"):
    return df.to_markdown(index=False, floatfmt=fmt) if len(df) else "_none_"


def _render(a, b) -> str:
    pos = a["cells"][a["cells"].ci95.str.startswith("+") & (a["cells"].roi_after_fees > 0)]
    L = ["# Threshold strategies", "",
         "## A. Bet the team whenever its pregame price is at least T", "",
         f"{a['n']:,} games (all sports, >= ${PREGAME_MIN_USD:,} traded before the start). Entry = the price "
         "of the first later same-side print after the pregame decision; shares capped by that print, plus actual "
         "taker fee (0 in 2025, 3-5% in 2026); held to the end. `roi_before_costs` uses the pregame mid, no fee.",
         "", "Cumulative (every game at or above the threshold):", "", _md(a["cumulative"]), "",
         "Price bands (each game counted once):", "", _md(a["bands"]), "",
         "By sport: ROI per $1 after fees for each threshold (blank = fewer than 30 games):", "",
         _md(a["by_sport"][["sport", "games"] + [c for c in a["by_sport"].columns if c.startswith(">=")]]), "",
         f"Cells (sport x threshold) whose whole 95% CI is above zero: **{len(pos)} of {len(a['cells'])}**. "
         "These overlapping historical tests do not provide a fresh confirmatory discovery.",
         "", _md(pos), "",
         "## B. MLB: buy the leader when it trades below its historical win rate", "",
         f"{b['n_checkpoints']:,} half-inning starts with a lead, in {b['n_games']:,} games. `hist_win_rate` = how "
         "often teams in exactly that spot (inning, half, lead, home/away) won, from seasons *before* the "
         "game's season. `discount` = historical win rate minus the leader's traded price. Entry at the "
         "side-specific later-print proxy +1c +actual fee, bounded by size and100/event. "
         "The5s clock starts at retrospective play time and is optimistic: receipt latency and depth are unknown. Held to settlement, with no mean-reversion exit tested.", "",
         "### Why do discounts appear? Look at who the discounted leaders are", "", _md(b["describe"]), "",
         "Compare pregame team strength, reference price and realized outcome directly in the descriptive table; "
         "these averages alone do not establish a tradable discrepancy.", "",
         "### The rule: buy the leader when price <= historical win rate - X", "", _md(b["rules"]), "",
         "By inning x lead (discount >= 3 pts, first signal per game):", "", _md(b["cells"]), "",
         "### Same rule with a fair value that knows team strength (fit on 2025, tested on 2026)", "",
         "Fair value = logistic model on the historical state rate + pregame odds + how much game is left. "
         "Buy whichever side the model says is cheap by at least X. "
         f"Excluded {b['model_input_exclusions']:,} checkpoints from this adjusted model because required inputs were missing or outside the declared 2–98c pregame range; the unadjusted rule retains them.", "", _md(b["adjusted"]), ""]
    return "\n".join(L)
