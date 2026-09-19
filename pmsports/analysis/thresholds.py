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
from ..wallets.universe import OUT
from .favorites import PREGAME_MIN_USD, favorite_bets
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
    m = pd.read_parquet(OUT / "pregame_prices.parquet")
    b = favorite_bets(m)
    b = b[b.pre_usd >= PREGAME_MIN_USD].copy()
    b["entry"] = np.where(b.fav_ask.notna(), np.maximum(b.fav_ask, b.fav_p), b.fav_p + 0.01)  # executable
    b["roi"] = _roi(b.entry, b.fav_won, b.fee_rate)
    b["roi_nofee"] = _roi(b.fav_p, b.fav_won, 0.0)

    def row(g, label):
        return {"games": len(g), "avg_price": g.fav_p.mean(), "win_rate": g.fav_won.mean(),
                "win_minus_price": g.fav_won.mean() - g.fav_p.mean(), "roi_before_costs": g.roi_nofee.mean(),
                "roi_after_fees": g.roi.mean(), "ci95": _ci(g.roi), "pnl_$100_per_game": 100 * g.roi.sum(), **label}

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
            rec[f">={t:.0%}"] = s.roi.mean() if len(s) >= 30 else np.nan
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
                              "roi_after_fees": s.roi.mean(), "ci95": _ci(s.roi)})
    cells = pd.DataFrame(cells)
    return {"cumulative": pd.DataFrame(cum)[["threshold"] + [c for c in cum[0] if c != "threshold"]],
            "bands": pd.DataFrame(bands)[["band"] + [c for c in bands[0] if c != "band"]],
            "by_sport": grid, "cells": cells, "n": len(b)}


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


def inning_discounts() -> dict[str, pd.DataFrame]:
    d = sport_dir("mlb")
    panel = pd.read_parquet(d / "panel.parquet")
    baseline = pd.read_parquet(d / "baseline.parquet")
    p = panel[panel.checkpoint & panel.mkt_p.notna() & (panel["diff"] != 0)].copy()
    p = p.sort_values(["game_pk", "state_ts"]).reset_index(drop=True)
    p["fair_home"] = _prior_season_fair(p, baseline)
    home_leads = p["diff"] > 0
    p["lead"] = p["diff"].abs().clip(upper=5)
    p["inn"] = p.inning.clip(upper=10)
    p["price_leader"] = np.where(home_leads, p.mkt_p, 1 - p.mkt_p)
    p["fair_leader"] = np.where(home_leads, p.fair_home, 1 - p.fair_home)
    p["pre_leader"] = np.where(home_leads, p.pre_p, 1 - p.pre_p)
    won = p.home_won_final.astype(float)
    p["leader_won"] = np.where(home_leads, won, 1 - won)
    p["discount"] = p.fair_leader - p.price_leader        # >0: leader cheaper than history says
    p["roi"] = _roi(p.price_leader, p.leader_won, p.fee_rate, slip=0.01)

    # what does a "discounted" leader look like? (team strength explains the gap)
    desc = []
    for x in DISCOUNTS[1:]:
        s = p[p.discount >= x]
        desc.append({"discount_at_least": f"{x:.0%}", "checkpoints": len(s), "games": s.game_pk.nunique(),
                     "avg_hist_win_rate": s.fair_leader.mean(), "avg_price": s.price_leader.mean(),
                     "actual_win_rate": s.leader_won.mean(),
                     "leader_was_pregame_underdog": (s.pre_leader < 0.5).mean(),
                     "avg_leader_pregame_price": s.pre_leader.mean()})
    desc = pd.DataFrame(desc)

    rules = []
    for x in DISCOUNTS:
        s = p[p.discount >= x]
        first = s.groupby("game_pk").head(1)             # one bet per game: first time the rule fires
        for label, g in (("first signal per game", first), ("every signal (half-inning starts)", s)):
            rules.append({"discount_at_least": f"{x:.0%}", "bets": label, "n": len(g),
                          "avg_price": g.price_leader.mean(), "hist_win_rate": g.fair_leader.mean(),
                          "actual_win_rate": g.leader_won.mean(), "roi_after_fees_1c": g.roi.mean(),
                          "ci95": _ci(g.roi) if label.startswith("first") else _ci_cluster(g),
                          "by_season": " / ".join(f"{yr}: {gg.roi.mean():+.3f} (n={len(gg)})"
                                                  for yr, gg in g.groupby(g.event_date.str[:4]))})
    rules = pd.DataFrame(rules)

    # by inning x lead at a 3-point discount, first signal per game
    s = p[p.discount >= 0.03].groupby("game_pk").head(1)
    cell = s.groupby(["inn", "lead"]).agg(bets=("roi", "size"), avg_price=("price_leader", "mean"),
                                          hist_win=("fair_leader", "mean"), won=("leader_won", "mean"),
                                          roi=("roi", "mean")).reset_index()
    cell = cell[cell.bets >= 20]

    # version 2: fair value that also knows the pregame odds; fit on 2025, test on 2026
    p["we"] = p.fair_home
    p["y"] = won
    tr, te = p[p.event_date < "2026-01-01"], p[p.event_date >= "2026-01-01"].copy()
    X = _features(tr)
    cols = [c for c in X.columns if X[c].std() > 1e-9]
    fit = sm.Logit(tr.y.to_numpy(), sm.add_constant(X[cols])).fit(disp=0)
    te["model_home"] = fit.predict(sm.add_constant(_features(te)[cols], has_constant="add"))
    hl = te["diff"] > 0
    te["model_leader"] = np.where(hl, te.model_home, 1 - te.model_home)
    te["edge_leader"] = te.model_leader - te.price_leader
    te["edge_trailer"] = -te.edge_leader
    adj = []
    for x in DISCOUNTS[1:]:
        for side, col, price, wcol in (("leader", "edge_leader", "price_leader", "leader_won"),
                                       ("trailer", "edge_trailer", None, None)):
            s = te[te[col] >= x].groupby("game_pk").head(1)
            if side == "trailer":
                pr, w = 1 - s.price_leader, 1 - s.leader_won
            else:
                pr, w = s[price], s[wcol]
            r = _roi(pr, w, s.fee_rate, slip=0.01)
            adj.append({"model_edge_at_least": f"{x:.0%}", "buy": side, "bets": len(s),
                        "avg_price": float(np.mean(pr)) if len(s) else np.nan,
                        "actual_win_rate": float(np.mean(w)) if len(s) else np.nan,
                        "roi_after_fees_1c": float(np.mean(r)) if len(s) else np.nan, "ci95": _ci(r)})
    return {"describe": desc, "rules": rules, "cells": cell, "adjusted": pd.DataFrame(adj),
            "n_checkpoints": len(p), "n_games": p.game_pk.nunique()}


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
         "takers actually paid for that side in the last 10 minutes (executable), plus the market's actual "
         "taker fee (0 in 2025, 3-5% in 2026); held to the end. `roi_before_costs` uses the pregame mid, no fee.",
         "", "Cumulative (every game at or above the threshold):", "", _md(a["cumulative"]), "",
         "Price bands (each game counted once):", "", _md(a["bands"]), "",
         "By sport: ROI per $1 after fees for each threshold (blank = fewer than 30 games):", "",
         _md(a["by_sport"][["sport", "games"] + [c for c in a["by_sport"].columns if c.startswith(">=")]]), "",
         f"Cells (sport x threshold) whose whole 95% CI is above zero: **{len(pos)} of {len(a['cells'])}**. "
         f"With {len(a['cells'])} overlapping tests, about {0.025 * len(a['cells']):.0f} would do that by chance.",
         "", _md(pos), "",
         "## B. MLB: buy the leader when it trades below its historical win rate", "",
         f"{b['n_checkpoints']:,} half-inning starts with a lead, in {b['n_games']:,} games. `hist_win_rate` = how "
         "often teams in exactly that spot (inning, half, lead, home/away) won, from seasons *before* the "
         "game's season. `discount` = historical win rate minus the leader's traded price. Entry at the "
         "traded price + 1c + the actual fee, held to the end.", "",
         "### Why do discounts appear? Look at who the discounted leaders are", "", _md(b["describe"]), "",
         "The market is not ignoring history: it is pricing the *teams*. A leader trading below the "
         "average rate is usually the weaker team (often a pregame underdog). Those leaders win at about "
         "their price, not at the historical average.", "",
         "### The rule: buy the leader when price <= historical win rate - X", "", _md(b["rules"]), "",
         "By inning x lead (discount >= 3 pts, first signal per game):", "", _md(b["cells"]), "",
         "### Same rule with a fair value that knows team strength (fit on 2025, tested on 2026)", "",
         "Fair value = logistic model on the historical state rate + pregame odds + how much game is left. "
         "Buy whichever side the model says is cheap by at least X.", "", _md(b["adjusted"]), ""]
    return "\n".join(L)
