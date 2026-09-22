"""Where is the breaking point? Price vs realized win rate, per sport, and whether it pays.

python -m pmsports calibration  ->  reports/CALIBRATION.md (+ CSVs, charts, desk ledgers)

Part A (descriptive): every taker fill is "someone paid q for this side, and the side paid y".
Bucket by q and ask what fraction actually won. Fine buckets at the top end (90c..99c), split
pregame vs in-play and by sport.

Part B (tradable): for each market, the FIRST fill at or above a threshold T is an executable
entry (a taker really traded there). Buy $100 of that side, hold to resolution, pay the
market's actual taker fee. One bet per market, dev (< 2026-07-01) vs holdout, per sport.
The mirror rule buys the cheap side at or below 1-T.

Selection uses pregame information only (pre_usd >= $25k): total volume is outcome-correlated.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from ..polymarket import taker_fee
from ..research.common import cluster_ci, fills, markets

log = logging.getLogger("pmsports")
REPORTS = Path(__file__).resolve().parents[2] / "reports"
LEDGERS = Path(__file__).resolve().parents[2] / "data" / "research" / "ledgers"
PREGAME_MIN_USD = 25_000
SPLIT_TS = pd.Timestamp("2026-07-01", tz="UTC").timestamp()
STAKE = 100.0
THRESHOLDS = [0.60, 0.70, 0.80, 0.85, 0.90, 0.925, 0.95, 0.97, 0.98, 0.99]
EDGES = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.925, 0.95, 0.96, 0.97, 0.98, 0.99, 1.0]
SPORTS = ["baseball", "soccer", "american_football", "basketball", "tennis", "esports", "hockey"]


def _load() -> tuple[pd.DataFrame, pd.DataFrame]:
    mk = markets()
    mk = mk[(mk.pre_usd.fillna(0) >= PREGAME_MIN_USD) & mk.game_start_ts.notna()]
    f = fills(markets=mk.m.to_numpy(), columns=["m", "ts", "q", "y", "size", "fee_rate", "in_play"])
    f = f[(f.y != 0.5) & f.q.between(0.02, 0.995)]          # voids are not a win/loss question
    meta = mk.set_index("m")
    f["sport"] = pd.Categorical.from_codes(
        pd.factorize(meta.family.reindex(f.m).to_numpy())[0],
        categories=pd.Index(pd.factorize(meta.family.reindex(f.m).to_numpy())[1]))
    f["event"] = meta.event_slug.reindex(f.m).to_numpy()
    f["start"] = meta.game_start_ts.reindex(f.m).to_numpy()
    log.info("%d fills in %d markets (pregame volume >= $%s)", len(f), f.m.nunique(), f"{PREGAME_MIN_USD:,}")
    return f, mk


def calibration_table(f: pd.DataFrame, by_sport: bool = True, phase: str | None = None) -> pd.DataFrame:
    d = f if phase is None else f[f.in_play == (phase == "in_play")]
    d = d.assign(bucket=pd.cut(d.q, EDGES, include_lowest=True, right=False))
    keys = (["sport"] if by_sport else []) + ["bucket"]
    rows = []
    for k, g in d.groupby(keys, observed=True):
        if len(g) < 200:
            continue
        usd = (g["size"] * g.q).to_numpy()
        fee = taker_fee(1.0, g.q.to_numpy(), g.fee_rate.to_numpy())
        roi = (g.y.to_numpy() - g.q.to_numpy() - fee) / (g.q.to_numpy() + fee)
        mean, lo, hi = cluster_ci(roi, g.event.to_numpy(), weights=usd)
        rec = dict(zip(keys, k if isinstance(k, tuple) else (k,)))
        rec.update({
            "fills": len(g), "usd": float(usd.sum()), "avg_price": float(np.average(g.q, weights=usd)),
            "won_pct": float(np.average(g.y, weights=usd)), "fills_won_pct": float(g.y.mean()),
            "edge_pts": float(np.average(g.y, weights=usd) - np.average(g.q, weights=usd)),
            "roi_after_fee": mean, "ci_lo": lo, "ci_hi": hi,
        })
        rows.append(rec)
    out = pd.DataFrame(rows)
    if len(out):
        out["bucket"] = out.bucket.astype(str)
    return out


def threshold_bets(f: pd.DataFrame, thr: float, mirror: bool = False) -> pd.DataFrame:
    """First executable fill per market at/above thr (or at/below 1-thr for the mirror)."""
    d = f[f.q <= (1 - thr)] if mirror else f[f.q >= thr]
    if d.empty:
        return d
    first = d.sort_values("ts", kind="stable").groupby("m", as_index=False).head(1).copy()
    price = first.q.to_numpy()
    shares = STAKE / price
    fee = taker_fee(shares, price, first.fee_rate.to_numpy())
    payout = shares * first.y.to_numpy()
    first["entry_price"] = price
    first["fee_usd"] = fee
    first["payout"] = payout
    first["pnl_usd"] = payout - STAKE - fee
    first["roi"] = first.pnl_usd / (STAKE + fee)
    first["period"] = np.where(first.ts < SPLIT_TS, "dev", "holdout")
    first["threshold"] = thr
    first["phase"] = np.where(first.in_play, "in_play", "pregame")
    return first


def threshold_table(f: pd.DataFrame, by_sport: bool = True) -> pd.DataFrame:
    rows = []
    for thr in THRESHOLDS:
        for mirror in (False, True):
            b = threshold_bets(f, thr, mirror)
            if len(b) == 0:
                continue
            groups = list(b.groupby("sport", observed=True)) if by_sport else [("ALL", b)]
            for sport, g in groups + ([("ALL", b)] if by_sport else []):
                for period in ("dev", "holdout", "all"):
                    gg = g if period == "all" else g[g.period == period]
                    if len(gg) < 25:
                        continue
                    mean, lo, hi = cluster_ci(gg.roi, gg.event.to_numpy(), weights=np.full(len(gg), STAKE))
                    rows.append({
                        "side": "underdog <= " + f"{1 - thr:.0%}" if mirror else "favorite >= " + f"{thr:.0%}",
                        "threshold": thr, "sport": sport, "period": period, "bets": len(gg),
                        "avg_price": float(gg.entry_price.mean()), "won_pct": float(gg.y.mean()),
                        "edge_pts": float(gg.y.mean() - gg.entry_price.mean()),
                        "roi": mean, "ci_lo": lo, "ci_hi": hi,
                        "pnl_usd": float(gg.pnl_usd.sum()),
                        "in_play_share": float((gg.phase == "in_play").mean()),
                    })
    return pd.DataFrame(rows)


def chart(cal: pd.DataFrame, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    INK, INK2, GRID, SURF = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
    COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#4a3aa7", "#e34948"]
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.4), facecolor=SURF)
    ax = axes[0]
    ax.plot([0.5, 1], [0.5, 1], color=INK2, lw=1, ls="--", label="perfectly fair")
    for i, sp in enumerate([s for s in SPORTS if s in set(cal.sport)]):
        g = cal[(cal.sport == sp) & (cal.avg_price >= 0.5)].sort_values("avg_price")
        if len(g) > 2:
            ax.plot(g.avg_price, g.won_pct, color=COLORS[i % len(COLORS)], lw=2, marker="o", ms=4, label=sp)
    ax.set_xlim(0.5, 1.0)
    ax.set_ylim(0.4, 1.0)
    ax.set_title("Price paid vs how often that side won", color=INK, fontsize=11, loc="left")
    ax.set_xlabel("price paid", color=INK2, fontsize=9)
    ax.set_ylabel("actually won", color=INK2, fontsize=9)
    ax2 = axes[1]
    ax2.axhline(0, color=INK2, lw=1)
    for i, sp in enumerate([s for s in SPORTS if s in set(cal.sport)]):
        g = cal[(cal.sport == sp) & (cal.avg_price >= 0.5)].sort_values("avg_price")
        if len(g) > 2:
            ax2.plot(g.avg_price, 100 * g.roi_after_fee, color=COLORS[i % len(COLORS)], lw=2, marker="o", ms=4, label=sp)
    ax2.set_xlim(0.5, 1.0)
    ax2.set_title("Return per $1 at that price, after fees", color=INK, fontsize=11, loc="left")
    ax2.set_xlabel("price paid", color=INK2, fontsize=9)
    ax2.set_ylabel("% per $1", color=INK2, fontsize=9)
    for a in axes:
        a.set_facecolor(SURF)
        a.grid(color=GRID, lw=.6)
        a.tick_params(colors=INK2, labelsize=8)
        for s in a.spines.values():
            s.set_visible(False)
    axes[0].legend(frameon=False, fontsize=8, labelcolor=INK2, loc="upper left")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def write_ledger(f: pd.DataFrame, thr: float, mirror: bool = False) -> None:
    b = threshold_bets(f, thr, mirror).sort_values("ts")
    if b.empty:
        return
    slug = ("underdog_at_%d" % round((1 - thr) * 100)) if mirror else ("favorite_at_%d" % round(thr * 100))
    side = f"cheap side <= {1 - thr:.0%}" if mirror else f"first price >= {thr:.0%}"
    rows = []
    for i, r in enumerate(b.itertuples(index=False), 1):
        rows.append([i, r.period, pd.to_datetime(r.ts, unit="s", utc=True).strftime("%Y-%m-%d"),
                     r.sport, "", r.event, "", side, int(r.ts), round(float(r.entry_price), 4), STAKE,
                     round(float(r.fee_usd), 4), "resolution", int(r.ts) + 7200, float(r.y),
                     round(float(r.payout), 2), round(float(r.pnl_usd), 2), round(float(r.roi), 4),
                     ("in-play" if r.in_play else "pregame") + f" crossing at {r.entry_price:.3f}"])
    head = {}
    for period, g in b.groupby("period"):
        mean, lo, hi = cluster_ci(g.roi, g.event.to_numpy(), weights=np.full(len(g), STAKE))
        head[period] = {"bets": int(len(g)), "roi": round(float(mean), 5), "ci_lo": round(float(lo), 5),
                        "ci_hi": round(float(hi), 5), "pnl_usd": round(float(g.pnl_usd.sum()), 2)}
    doc = {
        "slug": slug,
        "title": (f"Buy any underdog at {1 - thr:.0%} or cheaper" if mirror else f"Buy any team once it reaches {thr:.0%}"),
        "group": "Price thresholds", "sport": "multi", "verdict": "DEAD",
        "hypothesis": ("At some price the market stops losing: if teams priced at " +
                       (f"{1 - thr:.0%} or less" if mirror else f"{thr:.0%} or more") +
                       " win more often than that price implies, buying every one of them pays."),
        "mechanism": ("Favourite-longshot effects: bettors may overpay for near-certainties (or for longshot "
                      "lottery tickets), leaving the other side underpriced."),
        "entry_rule": (f"The first executable fill in each market at {'or below ' + format(1 - thr, '.0%') if mirror else 'or above ' + format(thr, '.0%')}"
                       " (a taker really traded there), $100 flat, one bet per market, pregame or in-play."),
        "exit_rule": "Held to resolution.",
        "cost_model": "Polymarket taker fee at the market's own rate (0 in 2025, 3% Mar-Jun 2026, 5% since Jul 2026).",
        "periods": {"dev": "2025-01-01..2026-06-30", "holdout": "2026-07-01..2026-09-18"},
        "review": "",
        "caveats": ["Markets selected on pregame volume only (>= $25k), never total volume.",
                    "Entry is the first print at the threshold; a real order would queue behind it.",
                    "Voids (payout 0.5) are excluded."],
        "report_path": "reports/CALIBRATION.md", "code_path": "pmsports/analysis/calibration.py",
        "truncated": False, "n_total_trades": len(rows),
        "headline": head,
        "columns": ["id", "period", "date", "sport", "league", "event", "market", "side", "entry_ts",
                    "entry_price", "stake_usd", "fee_usd", "exit_kind", "exit_ts", "exit_price", "payout",
                    "pnl_usd", "roi", "note"],
        "rows": rows,
    }
    LEDGERS.mkdir(parents=True, exist_ok=True)
    (LEDGERS / f"{slug}.json").write_text(json.dumps(doc, separators=(",", ":")))
    log.info("ledger %s: %d bets", slug, len(rows))


def run() -> None:
    REPORTS.mkdir(exist_ok=True)
    f, mk = _load()
    cal_all = calibration_table(f)
    cal_pre = calibration_table(f, phase="pregame")
    cal_live = calibration_table(f, phase="in_play")
    overall = calibration_table(f, by_sport=False)
    thr = threshold_table(f)
    for name, df in [("calibration_by_sport", cal_all), ("calibration_pregame", cal_pre),
                     ("calibration_inplay", cal_live), ("calibration_overall", overall),
                     ("calibration_thresholds", thr)]:
        df.to_csv(REPORTS / f"{name}.csv", index=False)
    chart(cal_all, REPORTS / "calibration_curve.png")
    for t in (0.90, 0.95, 0.99):
        write_ledger(f, t)
    write_ledger(f, 0.90, mirror=True)
    (REPORTS / "CALIBRATION.md").write_text(_render(f, overall, cal_all, cal_pre, cal_live, thr))
    log.info("wrote %s", REPORTS / "CALIBRATION.md")


def _md(df: pd.DataFrame, cols=None, fmt=".3f") -> str:
    d = df if cols is None else df[cols]
    return d.to_markdown(index=False, floatfmt=fmt) if len(d) else "_no data_"


def _render(f, overall, cal, pre, live, thr) -> str:
    CAL_COLS = ["bucket", "fills", "usd", "avg_price", "won_pct", "edge_pts", "roi_after_fee", "ci_lo", "ci_hi"]
    top = thr[(thr.period == "holdout") & (thr.sport == "ALL")]
    L = ["# The breaking point: price vs. reality, by sport", "",
         f"Every taker fill in {f.m.nunique():,} sports markets ({len(f):,} fills, 2025-01..2026-09), where the "
         "price paid is known and the outcome is known. Markets are selected on PREGAME volume only "
         f"(>= ${PREGAME_MIN_USD:,}), never on total volume, which is outcome-correlated. Voids excluded.",
         "",
         "`won_pct` is dollar-weighted: of every $1 spent at that price, how much came back as a winner. "
         "`edge_pts` = won_pct - avg_price (positive means the side won more often than it cost). "
         "`roi_after_fee` is the return per $1 including the market's actual taker fee, with a 95% CI "
         "clustered by game.", "",
         "## 1. All sports together", "", _md(overall, CAL_COLS), "",
         "![calibration](calibration_curve.png)", "",
         "## 2. By sport (all fills, pregame and in-play)", "", _md(cal, ["sport"] + CAL_COLS), "",
         "## 3. Pregame only", "", _md(pre, ["sport"] + CAL_COLS), "",
         "## 4. In-play only", "", _md(live, ["sport"] + CAL_COLS), "",
         "## 5. The tradable version: buy the first price at or beyond a threshold", "",
         "One bet per market at the first executable print at/above the threshold (or at/below its mirror), "
         "$100 flat, held to resolution, actual fees. `dev` is before 2026-07-01, `holdout` after.", "",
         _md(thr[thr.sport == "ALL"], ["side", "threshold", "period", "bets", "avg_price", "won_pct",
                                        "edge_pts", "roi", "ci_lo", "ci_hi", "in_play_share"]), "",
         "### Per sport (holdout)", "",
         _md(thr[(thr.period == "holdout") & (thr.sport != "ALL")],
             ["side", "threshold", "sport", "bets", "avg_price", "won_pct", "edge_pts", "roi", "ci_lo", "ci_hi"]), "",
         "## What to read here", "",
         "- A price level only pays if `won_pct` exceeds `avg_price` by more than the fee. The fee at 95c is "
         "0.24c per share (5% x p x (1-p)), so a 95c favourite must win **more than ~95.3%** of the time.",
         "- `edge_pts` near zero across a row means the market is right at that price and there is nothing to take.",
         "- In-play prices at the extremes are where 'the team never loses' intuitions live; the in-play table "
         "is the one to check for that.", ""]
    return "\n".join(L)
