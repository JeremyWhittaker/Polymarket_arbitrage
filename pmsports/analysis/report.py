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
    ax.axvspan(10, 45, color=S2, alpha=0.08, lw=0)
    ax.text(11, 0.05, "typical TV/stream delay\n(~10-45s behind live)", fontsize=7, color=INK2)
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
    L = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    L += [f"# MLB x Polymarket: hypothesis report ({now})", "",
          "Generated by `python -m pmsports report`. All prices are Polymarket moneyline "
          "probabilities for the side named; P&L assumes you buy as a **taker** and hold to "
          "resolution. Fee = Polymarket sports taker fee `shares * 0.05 * p * (1-p)` "
          "(2.5% of notional at 50c); slippage = 1c unless stated.", ""]
    L += ["## Data", "",
          f"- Polymarket MLB game events: {len(games):,}; joined to MLB game_pk: {games.game_pk.notna().sum():,}",
          f"- Games with pregame price + outcome: {h1['n_games']:,}",
          f"- In-game state rows (plate appearances with a market price): {len(panel):,} "
          f"({int(panel.checkpoint.sum()):,} half-inning checkpoints)",
          f"- Baseline (MLB-only) games for win-expectancy tables: {h2['baseline_games']:,} "
          f"(seasons {h2['baseline_seasons'][0]}-{h2['baseline_seasons'][-1]})", ""]

    L += ["## H1 - Do pregame favorites win as often as the price says?", "",
          f"Favorites won **{h1['fav_win_rate']:.1%}** of games at an average price of "
          f"**{h1['fav_avg_price']:.1%}**. Calibration slope {h1['calib_slope']:.2f} "
          f"(se {h1['calib_slope_se']:.2f}; 1.00 = perfectly calibrated), intercept "
          f"{h1['calib_intercept']:+.3f}. Brier: market {h1['brier_market']:.4f} vs coin-flip "
          f"{h1['brier_coinflip']:.4f}.", "",
          "The question is not *does the favorite win more than 50%* (it does, by construction) "
          "but *does it win more often than its price*. Only the gap between the two columns "
          "below is edge.", "",
          _md(h1["table"].assign(bucket=h1["table"].bucket.astype(str))), "",
          "![calibration](h1_calibration.png)", "", "By season:", "", _md(h1["by_season"]), "",
          "Blind strategies (every game, ROI per $1 staked, bootstrap 95% CI):", "",
          _md(h1["strategies"]), ""]

    L += ["## H2 - Leading by X going into inning Z", "",
          "Ground truth from MLB play-by-play (includes seasons with no Polymarket data). "
          "Rows = inning about to start (10 = extras), columns = lead in runs (6 = 6+), "
          "value = how often the leading team went on to win.", "",
          h2["baseline_win_pct"].round(3).reset_index().to_markdown(index=False), "",
          "Same states on Polymarket games: what the market priced the leader at (mean, std, "
          "10th-90th pct) vs how often they actually won. `edge` = actual - mean price; |t| < 2 "
          "is noise.", "",
          _md(h2["market_cells"][h2["market_cells"].n >= 40]), ""]

    L += ["## H3 - Trade toward a fair value, out of sample", ""]
    if "error" in h3:
        L += [f"Skipped: {h3['error']}", ""]
    else:
        L += [f"Train = games before {split_date} ({h3['train_games']:,} games, {h3['train_rows']:,} states); "
              f"test = on/after ({h3['test_games']:,} games, {h3['test_rows']:,} states). "
              "Model: logistic regression of home win on baseline win expectancy for the state, "
              "pregame price, and pregame price x share of game left. Lower log-loss = better forecaster.", "",
              _md(h3["scores"], ".4f"), "",
              f"Stacking test (does the model add information the market lacks?): coefficient on "
              f"(model - market) = {h3['stack_coef_model_minus_market']:+.3f} "
              f"(game-clustered se {h3['stack_se_model_minus_market']:.3f}). ~0 means the market already contains the model. "
              "A positive value with a losing backtest means the model only helps inside the bid/ask + fee band.", "",
              "Backtest: first state per game where |model - market| > threshold; buy the side the model "
              "prefers at market + 1c, hold to resolution:", "", _md(h3["backtest"]), "",
              "Your literal idea - cell = (inning, half, score diff, pregame-favorite bucket), fair = the "
              "average market price for that cell in the training period, bet toward the average when "
              "the price is k std away:", "", _md(h3["naive"]), ""]

    L += ["## H4 - Can you beat the market after seeing a play?", ""]
    if "summary" not in h4:
        L += ["Skipped: no scoring plays with trades.", ""]
    else:
        s = h4["summary"]
        L += ["Clock: MLB Statcast timestamp of the in-play pitch vs Polymarket fill timestamps "
              "(1s resolution, on-chain settlement time, shifted 2.5s earlier to approximate match time - "
              "measured by matching tx hashes against the live CLOB websocket). `t50` = seconds until a fill prints at least "
              "halfway to the post-play price. `stale fill` = a taker bought the scoring side at a "
              "price < 25% of the way to the new level (someone's old resting order got picked off).", "",
              _latency_table(s), "",
              "![latency](h4_latency.png)", ""]
    L += ["## Caveats", "",
          "- 1-minute price bars are last-trade/mid samples, not executable quotes; H1-H3 add 1c slippage "
          "to approximate crossing the spread. Real books for live MLB are usually 1-2c wide but thin.",
          "- Polymarket clears the book at first pitch (`clearBookOnStart`); the 'pregame price' is the "
          "last price before that.",
          "- Early-2025 events sometimes had home/away reversed on Polymarket; the pipeline re-orients "
          "every game to MLB's home/away.",
          "- Fees were 0 in 2025, 3% Mar-Jun 2026, 5% since Jul 2026. Backtests show both 0 and 5% so "
          "you can see what the fee alone does.", ""]
    return L
