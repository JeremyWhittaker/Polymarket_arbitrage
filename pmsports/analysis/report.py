"""Run every hypothesis test and write reports/REPORT.md + CSVs + charts."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from ..collect import sport_dir  # noqa: E402
from ..panel import _load_dir  # noqa: E402
from . import hypotheses as H  # noqa: E402

log = logging.getLogger("pmsports")
OUT = Path(__file__).resolve().parents[2] / "reports"

INK, INK2, GRID, SURF = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
S1, S2 = "#2a78d6", "#eb6834"


def _style(ax, title, xlabel, ylabel):
    ax.set_facecolor(SURF)
    ax.set_title(title, color=INK, fontsize=11, loc="left")
    ax.set_xlabel(xlabel, color=INK2, fontsize=9)
    ax.set_ylabel(ylabel, color=INK2, fontsize=9)
    ax.tick_params(colors=INK2, labelsize=8)
    ax.grid(color=GRID, linewidth=0.6)
    for s in ax.spines.values():
        s.set_visible(False)


def _md(df: pd.DataFrame, floatfmt=".3f") -> str:
    return df.to_markdown(index=False, floatfmt=floatfmt)


def chart_calibration(t: pd.DataFrame, path: Path):
    fig, ax = plt.subplots(figsize=(5.2, 4.2), facecolor=SURF)
    ax.plot([0.5, 0.85], [0.5, 0.85], color=INK2, lw=1, ls="--", label="perfect calibration")
    ax.plot(t.implied, t.actual, color=S1, lw=2, marker="o", ms=7, label="pregame favorites")
    for r in t.itertuples():
        ax.annotate(f"n={r.games}", (r.implied, r.actual), textcoords="offset points", xytext=(6, -12),
                    fontsize=7, color=INK2)
    _style(ax, "Pregame favorite: price vs. actual win rate", "Polymarket price at first pitch",
           "Actual win rate")
    ax.legend(frameon=False, fontsize=8, labelcolor=INK2)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def chart_latency(curve: pd.DataFrame, n: int, path: Path):
    fig, ax = plt.subplots(figsize=(6.4, 4.0), facecolor=SURF)
    ax.fill_between(curve.sec, curve.p25, curve.p75, color=S1, alpha=0.15, lw=0, label="middle 50% of plays")
    ax.plot(curve.sec, curve.median_frac, color=S1, lw=2, label="median")
    ax.axvline(0, color=INK2, lw=1)
    ax.set_ylim(-0.2, 1.2)
    _style(ax, f"Price response to scoring plays (n={n})", "Seconds after ball in play (MLB Statcast clock)",
           "Share of eventual price move done")
    ax.legend(frameon=False, fontsize=8, labelcolor=INK2, loc="upper left")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def run_all(split_date: str = "2026-01-01", sport: str = "mlb") -> None:
    d = sport_dir(sport)
    OUT.mkdir(exist_ok=True)
    if not (d / "panel.parquet").exists():
        from ..panel import build_panel
        build_panel(sport)
    games = pd.read_parquet(d / "games.parquet")
    pregame = pd.read_parquet(d / "pregame.parquet")
    panel = pd.read_parquet(d / "panel.parquet")
    baseline = pd.read_parquet(d / "baseline.parquet")

    log.info("H1 calibration")
    h1 = H.h1_calibration(pregame)
    log.info("H2 state tables")
    h2 = H.h2_state_tables(panel, baseline)
    log.info("H3 fair-value backtest")
    h3 = H.h3_fair_value(panel, baseline, split_date)
    log.info("H4 latency")
    plays = _load_dir(d / "plays")
    g = games[games.game_pk.notna()].copy()
    g["game_pk"] = g.game_pk.astype(int)
    h4 = H.h4_latency(plays, d / "trades", g)

    chart_calibration(h1["table"], OUT / "h1_calibration.png")
    if "curve" in h4:
        chart_latency(h4["curve"], len(h4["events"]), OUT / "h4_latency.png")

    h1["table"].to_csv(OUT / "h1_calibration.csv", index=False)
    h1["strategies"].to_csv(OUT / "h1_strategies.csv", index=False)
    h2["market_cells"].to_csv(OUT / "h2_state_market_cells.csv", index=False)
    h2["baseline_win_pct"].to_csv(OUT / "h2_baseline_win_pct.csv")
    if "backtest" in h3:
        h3["backtest"].to_csv(OUT / "h3_backtest.csv", index=False)
        h3["naive"].to_csv(OUT / "h3_naive_state_average.csv", index=False)
    if "summary" in h4:
        h4["summary"].to_csv(OUT / "h4_latency_summary.csv", index=False)
        h4["events"].to_csv(OUT / "h4_latency_events.csv", index=False)

    lines = _render(h1, h2, h3, h4, games, pregame, panel, split_date)
    (OUT / "REPORT.md").write_text("\n".join(lines))
    summary = {"generated": datetime.now(timezone.utc).isoformat(), "n_games": int(h1["n_games"]),
               "fav_win_rate": h1["fav_win_rate"], "fav_avg_price": h1["fav_avg_price"],
               "calib_slope": h1["calib_slope"]}
    (OUT / "summary.json").write_text(json.dumps(summary, indent=1))
    log.info("wrote %s", OUT / "REPORT.md")


def _latency_table(s: pd.DataFrame) -> str:
    rows = []
    for r in s.itertuples(index=False):
        d = r._asdict()
        rows.append({"events": d["events"], "n": d["n"], "median move": f"{d['median_move']:.3f}",
                     "t50 median (s)": f"{d['t50_median_s']:.1f}", "t50 p90 (s)": f"{d['t50_p90_s']:.1f}",
                     "t90 median (s)": f"{d['t90_median_s']:.1f}"})
    out = pd.DataFrame(rows).to_markdown(index=False)
    buckets = [c.replace("events_w_stale_fill_", "") for c in s.columns if c.startswith("events_w_stale_fill_")]
    stale = pd.DataFrame({"seconds after contact": buckets})
    for r in s.itertuples(index=False):
        d = r._asdict()
        vals = list(s.loc[s.events == d["events"]].iloc[0][[c for c in s.columns if c.startswith("events_w_stale_fill_")]])
        stale[f"{d['events']}: share of plays with a stale fill"] = [f"{v:.1%}" for v in vals]
    return out + "\n\n" + stale.to_markdown(index=False)


def _render(h1, h2, h3, h4, games, pregame, panel, split_date) -> list[str]:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    L = [f"# MLB hypothesis report ({now})", "",
         "Corrected historical research; the evaluated history has already been explored. "
         "No result here proves an executable or prospective trading edge. "
         "Generated by `python -m pmsports report`.", "",
         "## Data and timing", "",
         f"- {h1['n_games']:,} pregame price/outcome observations; {len(panel):,} game states and "
         f"{int(panel.checkpoint.sum()):,} half-inning checkpoints.",
         f"- Descriptive baseline: {h2['baseline_games']:,} games, seasons "
         f"{h2['baseline_seasons'][0]}–{h2['baseline_seasons'][-1]}.",
         "- State references use raw transaction timestamps strictly before the decision; "
         "H2/H3 require a reference no older than 120 seconds. Terminal and phantom duplicate states are removed.",
         "- H3 entries require the first later acquired-side print strictly after five seconds and before state expiry; "
         "printed size and a $100 fee-inclusive game cap limit allocation. Failed and partial entries remain counted. "
         "The five seconds start at a retrospective play clock: actual receipt latency and available book depth are unknown.", "",
         "## H1 — Pregame calibration", "",
         h1['execution_note'], "",
         f"Favorites won {h1['fav_win_rate']:.1%} at mean reference probability {h1['fav_avg_price']:.1%}. "
         f"Calibration slope {h1['calib_slope']:.2f} (standard error {h1['calib_slope_se']:.2f}), "
         f"intercept {h1['calib_intercept']:+.3f}; market Brier {h1['brier_market']:.4f}.", "",
         _md(h1['table'].assign(bucket=h1['table'].bucket.astype(str))), "",
         "![Pregame calibration](h1_calibration.png)", "", _md(h1['by_season']), "",
         "Synthetic equal-dollar reference-price benchmarks follow. Their `bets` column counts "
         "observations, not executable fills. Zero/5% fee-rate and 0/1c adjustments are explicit "
         "cost scenarios, not per-contract historical fees or evidence of available depth. "
         "See [FAVORITES.md](FAVORITES.md) for later-print, size-limited execution.", "",
         _md(h1['strategies']), "",
         "## H2 — Prices and outcomes by state", "",
         "The first table describes historical win frequencies, including the evaluated season; it is not "
         "a causal forecasting model. Trading rules use strictly prior seasons. Rows are the inning about "
         "to start; columns are absolute lead (six means six or more).", "",
         h2['baseline_win_pct'].round(3).reset_index().to_markdown(index=False), "",
         "The market table compares outcomes and prices on the same priced, non-stale observations. "
         "Residual confidence intervals resample whole games. These descriptive, overlapping cells do "
         "not provide independent confirmatory significance tests.", "",
         _md(h2['market_cells'][h2['market_cells'].n >= 40]), "",
         "## H3 — Model-versus-market settlement tests", ""]
    if 'error' in h3:
        L += [f"Unavailable: {h3['error']}", ""]
    else:
        L += [f"Training before {split_date}: {h3['train_games']:,} games/{h3['train_rows']:,} states. "
              f"Historical evaluation: {h3['test_games']:,} games/{h3['test_rows']:,} states. "
              "Each training state's baseline rate excludes that state's season; the evaluation baseline "
              "is frozen before the split year. Logistic inputs are baseline expectancy, pregame price and game progress.", "",
              _md(h3['scores'], '.4f'), "",
              f"Descriptive evaluation-set stacking coefficient on model-minus-market: "
              f"{h3['stack_coef_model_minus_market']:+.3f}, game-clustered standard error "
              f"{h3['stack_se_model_minus_market']:.3f}. This fitted diagnostic is not a deployed trading rule.", "",
              "First eligible signal per game at each declared threshold; later-print entry plus 1c sensitivity "
              "and actual contract fee; settlement exit. ROI divides total P&L by actual allocated capital.", "",
              _md(h3['backtest']), "",
              "The historical cell-average rule below uses training-period mean/std by inning, half, "
              "score difference and pregame price bucket. It holds to settlement. The requested literal "
              "exit at the mean is tested separately against received book depth in "
              "[LIVE_EXECUTION.md](research/LIVE_EXECUTION.md).", "", _md(h3['naive']), "",
              h3['execution_note'], ""]
    L += ["## H4 — Retrospective price response", ""]
    if 'summary' in h4:
        L += ["This descriptive event study selects scoring events with a subsequent price move. "
              "It compares retrospective Statcast contact times with transaction timestamps shifted "
              "2.5 seconds earlier using an estimated chain lag. That shift is not used for H3 execution. "
              "A stale historical print does not prove liquidity remained for a newly submitted order. "
              "Actual received-feed/book timing and hypothetical depth-crossing P&L are reported separately "
              "in [LIVE_EXECUTION.md](research/LIVE_EXECUTION.md).", "",
              _latency_table(h4['summary']), "", "![Retrospective response](h4_latency.png)", ""]
    else:
        L += ["Unavailable: no qualifying scoring events with transaction coverage.", ""]
    L += ["## Limits", "",
          "The corpus has legacy volume/window selection and incomplete market coverage. Reference transactions "
          "are not order-book asks/bids. Next-state expiry is analytical censoring, not verified cancellation "
          "during a sports order delay. Unknown fees fail closed in execution. H1 synthetic scenarios are "
          "separate from H3's recovered contract-specific historical fees. Nominal intervals do not correct "
          "for the many already inspected variants. No live orders were placed.", ""]
    return L
