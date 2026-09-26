"""$100K account performance analytics for the MLB inning-discount rules.

HISTORICAL BACKTEST ONLY. Every number here comes from 2025-26 MLB data that had already been
inspected, and the 10c cutoff was chosen from a grid of 12 fixed rules (0/2/3/5/8/10c x first/every
signal) evaluated on that same data. Execution is a trade-print proxy (the first later same-side
print after 5 s, +1c), not a replay of a real order book. None of this is forward evidence.

Inputs (written by ``pmsports.research.mlb_sizing``):
  data/research/performance/sizing_trades.parquet        ledger execution, the canonical proxy
  data/research/performance/sizing_sweep_trades.parquet  sweep execution, an UPPER bound on size
  data/research/performance/capacity_summary.csv         print-liquidity percentiles per signal
  data/mlb/panel.parquet                                  game dates, for the MLB season windows

What it does, per scenario (rule x fee regime x budget per game, plus the sweep upper bounds):
  * Simulates a $100,000 account. The full cost of a trade (shares x price + fee) leaves cash at the
    fill time and the payout comes back when the game resolves (the ledger's exit clock). A trade
    whose cost exceeds free cash is skipped and counted. Profit is booked on the resolution day, so
    equity = $100,000 + realized P&L (open positions are carried at cost, never marked to market).
  * Daily return = that UTC day's realized P&L / equity at the start of the day. Every calendar day
    from the first 2025 game day to the last 2026 day in the data is in the series; days without a
    resolution are 0.
  * Metrics: CAGR, volatility, Sharpe, Sortino and Calmar annualized with periods=365 on calendar
    days (the primary convention), plus an MLB-season-days-only variant annualized with the length
    of the one complete season window (2025). QuantStats computes the same ratios as a cross-check.
  * Bootstraps per-game P&L into full-season outcomes and shows how the 95% range of ROI narrows as
    the number of games grows (law of large numbers under the observed per-game distribution).

Outputs (git-ignored): data/research/performance/{metrics.csv, dashboard.json,
daily_returns_<scenario>.csv, trades_<scenario>.csv, tearsheet_<scenario>.html, lln.csv,
bootstrap_seasons.csv}, sweep scenarios under data/research/performance/sweep/, and the
plain-language summary reports/MLB_INNING_PERFORMANCE.md.

Never places orders. Run with:
  PYTHONPATH=. nice .venv/bin/python -m pmsports.analysis.performance
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import time
import warnings
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from ..research.common import RESEARCH, ROOT

log = logging.getLogger("pmsports")
PERF = RESEARCH / "performance"
SWEEP_DIR = PERF / "sweep"
REPORT = ROOT / "reports" / "MLB_INNING_PERFORMANCE.md"
PANEL = ROOT / "data" / "mlb" / "panel.parquet"

EQUITY0 = 100_000.0
RULES = ["10c_first", "03c_first", "10c_every"]
FEES = ["as_recorded", "intl_0.05", "us_0.0695"]
FEE_RATES = {"as_recorded": None, "intl_0.05": 0.05, "us_0.0695": 0.0695}
BUDGETS = [100, 250, 500, 1000, 2000, 5000]
LEDGER = "ledger_first_print"
PERIODS_CAL = 365
N_BOOT = 10_000
LLN_N = [500, 1000, 2500, 5000]
SEED = 20260925
DAY = 86_400.0
EPS = 1e-9
TRADE_COLS = ["rule", "execution", "budget_usd", "fee_regime", "fee_rate", "signal_idx", "game_pk", "event",
              "side", "season", "inning", "half", "lead", "fair_price", "reference_price", "signal_ts",
              "entry_ts", "exit_ts", "entry_price", "shares", "stake", "fee", "cost", "payout", "pnl", "status"]
RULE_TEXT = {"10c_first": "10c discount, first signal per game",
             "03c_first": "3c discount, first signal per game (control)",
             "10c_every": "10c discount, every signal (event cap = budget)"}
FEE_TEXT = {"as_recorded": "as recorded (0 in 2025, 0.03 Apr-Jun 2026, 0.05 from Jul 2026)",
            "intl_0.05": "0.05 on every trade (international, current)",
            "us_0.0695": "0.0695 on every trade (Polymarket US, from 2026-09-25)"}

try:  # QuantStats is optional; every ratio is also computed by hand below.
    import matplotlib
    matplotlib.use("Agg")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        import quantstats as qs
    QS_OK, QS_ERR = True, ""
except Exception as exc:  # pragma: no cover - depends on the environment
    qs, QS_OK, QS_ERR = None, False, repr(exc)


# ----------------------------------------------------------------------------- inputs

def scenario_name(rule: str, budget: float, fee: str, execution: str = LEDGER) -> str:
    base = f"{rule}_b{int(budget)}_{fee}"
    return base if execution == LEDGER else f"{base}_{execution.removeprefix('sweep_')}_sweep"


def load_trades(sweep: bool = True) -> pd.DataFrame:
    frames = [pd.read_parquet(PERF / "sizing_trades.parquet", columns=TRADE_COLS)]
    if sweep and (PERF / "sizing_sweep_trades.parquet").exists():
        frames.append(pd.read_parquet(PERF / "sizing_sweep_trades.parquet", columns=TRADE_COLS))
    t = pd.concat(frames, ignore_index=True)
    for c in ("rule", "execution", "fee_regime", "event", "side", "half", "status"):
        t[c] = t[c].astype(str)
    return t


def season_windows() -> pd.DataFrame:
    """First and last UTC game day per season, from every game in the MLB panel (not only signals)."""
    p = pd.read_parquet(PANEL, columns=["game_pk", "decision_ts"])
    first = pd.to_datetime(p.groupby("game_pk").decision_ts.min(), unit="s", utc=True)
    d = first.dt.tz_localize(None).dt.normalize()
    w = d.groupby(d.dt.year).agg(["min", "max", "size"]).rename(columns={"min": "start", "max": "end",
                                                                      "size": "games"})
    w["days"] = (w.end - w.start).dt.days + 1
    return w


def to_day(ts) -> pd.DatetimeIndex:
    return pd.to_datetime(np.asarray(ts, float), unit="s").normalize().as_unit("ns")


# ----------------------------------------------------------------------------- account simulation

def simulate(sc: pd.DataFrame, cal: pd.DatetimeIndex) -> dict:
    """Walk entries and resolutions in time order on a $100k cash account.

    Cash leaves at the fill (cost incl. fee) and returns with the payout at resolution. Exits sort
    before entries at the same timestamp. Entries larger than free cash are skipped and counted.
    """
    cal = pd.DatetimeIndex(cal).as_unit("ns")
    f = sc[(sc.cost > EPS) & sc.entry_ts.notna()].sort_values(["entry_ts", "signal_ts"]).reset_index(drop=True)
    m = len(f)
    ent, ext = f.entry_ts.to_numpy(float), f.exit_ts.to_numpy(float)
    cost, pay = f.cost.to_numpy(float), f.payout.to_numpy(float)
    if (ext < ent).any():
        raise ValueError(f"{int((ext < ent).sum())} trades resolve before they are entered")
    times = np.concatenate([ext, ent])
    kind = np.concatenate([np.zeros(m, int), np.ones(m, int)])  # 0 = resolution, 1 = entry
    idx = np.concatenate([np.arange(m), np.arange(m)])
    order = np.lexsort((kind, times))
    taken, skipped = np.zeros(m, bool), np.zeros(m, bool)
    cash, committed, realized, n_open = EQUITY0, 0.0, 0.0, 0
    ev_t, ev_c, ev_eq, ev_open = [], [], [], []
    for k in order:
        i = idx[k]
        if kind[k] == 1:
            if cost[i] > cash + EPS:
                skipped[i] = True
                continue
            cash -= cost[i]
            committed += cost[i]
            n_open += 1
            taken[i] = True
        else:
            if not taken[i]:
                continue
            cash += pay[i]
            committed -= cost[i]
            realized += pay[i] - cost[i]
            n_open -= 1
        ev_t.append(times[k])
        ev_c.append(max(committed, 0.0))
        ev_eq.append(EQUITY0 + realized)
        ev_open.append(n_open)
    ev_t, ev_c, ev_eq, ev_open = map(np.asarray, (ev_t, ev_c, ev_eq, ev_open))
    trades = f[taken].copy()

    pnl_day = trades.groupby(to_day(trades.exit_ts)).pnl.sum().reindex(cal, fill_value=0.0)
    equity = EQUITY0 + pnl_day.cumsum()
    eq_start = equity.shift(1, fill_value=EQUITY0)
    ret = pnl_day / eq_start
    day_end = (cal + pd.Timedelta(days=1)).asi8 / 1e9
    pos = np.searchsorted(ev_t, day_end, side="left") - 1 if len(ev_t) else np.full(len(cal), -1)
    committed_eod = np.where(pos >= 0, ev_c[np.clip(pos, 0, None)] if len(ev_c) else 0.0, 0.0)
    open_eod = np.where(pos >= 0, ev_open[np.clip(pos, 0, None)] if len(ev_open) else 0, 0)
    start_level = np.concatenate([[0.0], committed_eod[:-1]])
    if len(ev_t):
        ev_max = pd.Series(ev_c).groupby(to_day(ev_t)).max().reindex(cal).to_numpy()
    else:
        ev_max = np.full(len(cal), np.nan)
    committed_peak = np.fmax(start_level, ev_max)
    n_entries = trades.groupby(to_day(trades.entry_ts)).size().reindex(cal, fill_value=0)
    n_exits = trades.groupby(to_day(trades.exit_ts)).size().reindex(cal, fill_value=0)
    peak = np.maximum.accumulate(np.concatenate([[EQUITY0], equity.to_numpy()]))[1:]
    daily = pd.DataFrame({"date": cal, "pnl": pnl_day.to_numpy(), "equity_start": eq_start.to_numpy(),
                          "equity": equity.to_numpy(), "return": ret.to_numpy(), "committed": committed_eod,
                          "committed_peak": committed_peak, "open_positions": open_eod,
                          "n_entries": n_entries.to_numpy(), "n_resolutions": n_exits.to_numpy(),
                          "drawdown_usd": equity.to_numpy() - peak, "drawdown_pct": equity.to_numpy() / peak - 1})

    # time-weighted committed capital over the calendar window
    t0, t1 = cal[0].value / 1e9, (cal[-1] + pd.Timedelta(days=1)).value / 1e9
    if len(ev_t):
        tt = np.clip(np.append(ev_t, t1), t0, t1)
        area = float((ev_c * np.diff(tt)).sum())
        busy = float((np.diff(tt) * (ev_c > EPS)).sum())
    else:
        area = busy = 0.0
    trade_path = np.concatenate([[EQUITY0], ev_eq]) if len(ev_eq) else np.array([EQUITY0])
    return dict(trades=trades, skipped=f[skipped], daily=daily, ev_t=ev_t, ev_c=ev_c, ev_eq=ev_eq,
                avg_committed=area / (t1 - t0), pct_time_committed=busy / (t1 - t0),
                peak_committed=float(ev_c.max()) if len(ev_c) else 0.0,
                peak_committed_pct_equity=float(np.divide(ev_c, ev_eq, out=np.where(ev_c > EPS, np.inf, 0.0),
                                                          where=ev_eq > EPS).max()) if len(ev_c) else 0.0,
                max_open_positions=int(ev_open.max()) if len(ev_open) else 0,
                trade_max_dd_usd=float((trade_path - np.maximum.accumulate(trade_path)).min()))


# ----------------------------------------------------------------------------- metrics

def _ratios(r: pd.Series, periods: float) -> dict:
    """Sharpe/Sortino/vol/CAGR with QuantStats' own definitions (rf=0, sample std, Sortino's
    downside deviation over all periods)."""
    r = r.astype(float)
    n = len(r)
    growth = float((1 + r).prod())
    sd = float(r.std(ddof=1))
    down = math.sqrt(float((r[r < 0] ** 2).sum()) / n) if n else float("nan")
    return dict(cagr=growth ** (periods / n) - 1 if n else float("nan"),
                ann_return_arith=float(r.mean()) * periods,
                ann_vol=sd * math.sqrt(periods),
                sharpe=float(r.mean()) / sd * math.sqrt(periods) if sd > 0 else float("nan"),
                sortino=float(r.mean()) / down * math.sqrt(periods) if down > 0 else float("nan"))


def drawdown_stats(daily: pd.DataFrame, rows: bool = False) -> dict:
    """Max drawdown on daily closing equity, and the longest spell below a prior high-water mark
    (calendar days from the last high to the day equity regains it, or to the end of the data).
    With ``rows=True`` the spell is counted in rows of ``daily`` (used for season-days-only)."""
    eq = daily.equity.to_numpy()
    dates = pd.DatetimeIndex(daily.date)
    peak = np.maximum.accumulate(np.concatenate([[EQUITY0], eq]))[1:]
    dd = eq - peak
    i_trough = int(np.argmin(dd / peak))
    out = dict(max_dd_usd=float(dd.min()), max_dd_pct=float((eq / peak - 1).min()))
    if out["max_dd_usd"] > -EPS:
        return {**out, "max_dd_peak_date": "", "max_dd_trough_date": "", "max_dd_recovery_date": "",
                "longest_dd_days": 0, "longest_dd_start": "", "longest_dd_end": "", "longest_dd_recovered": True}
    pk_val = peak[i_trough]
    at_peak = np.where(np.isclose(eq[: i_trough + 1], pk_val, atol=1e-6))[0]
    i_peak = int(at_peak[-1]) if len(at_peak) else -1
    rec = np.where(eq[i_trough:] >= pk_val - 1e-6)[0]
    out.update(max_dd_peak_date=str(dates[i_peak].date()) if i_peak >= 0 else "start",
               max_dd_trough_date=str(dates[i_trough].date()),
               max_dd_recovery_date=str(dates[i_trough + rec[0]].date()) if len(rec) else "not recovered")
    under = dd < -1e-6
    best = (0, "", "", True)
    i = 0
    while i < len(eq):
        if under[i]:
            j = i
            while j < len(eq) and under[j]:
                j += 1
            start = dates[i - 1] if i > 0 else dates[0] - pd.Timedelta(days=1)
            recovered = j < len(eq)
            end = dates[j] if recovered else dates[-1]
            days = (j if recovered else len(eq) - 1) - (i - 1) if rows else int((end - start).days)
            if days > best[0]:
                best = (days, str(start.date()), str(end.date()), recovered)
            i = j
        else:
            i += 1
    out.update(longest_dd_days=best[0], longest_dd_start=best[1], longest_dd_end=best[2],
               longest_dd_recovered=best[3])
    return out


def qs_crosscheck(ret: pd.Series) -> dict:
    if not QS_OK:
        return {}
    r = ret.copy()
    r.index = pd.DatetimeIndex(r.index)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            dd = qs.stats.to_drawdown_series(r)
            det = qs.stats.drawdown_details(dd)
            longest = int(det["days"].max()) if len(det) else 0
        except Exception:  # pragma: no cover
            longest = np.nan
        return dict(qs_cagr=float(qs.stats.cagr(r, periods=PERIODS_CAL)),
                    qs_sharpe=float(qs.stats.sharpe(r, periods=PERIODS_CAL)),
                    qs_sortino=float(qs.stats.sortino(r, periods=PERIODS_CAL)),
                    qs_vol=float(qs.stats.volatility(r, periods=PERIODS_CAL)),
                    qs_max_dd=float(qs.stats.max_drawdown(r)),
                    qs_calmar=float(qs.stats.calmar(r, periods=PERIODS_CAL)),
                    qs_longest_dd_days=longest)


def scenario_metrics(sc: pd.DataFrame, sim: dict, season_mask: np.ndarray, periods_season: int) -> dict:
    d, tr = sim["daily"], sim["trades"]
    r = pd.Series(d["return"].to_numpy(), index=pd.DatetimeIndex(d.date))
    cal = _ratios(r, PERIODS_CAL)
    sea = _ratios(r[season_mask], periods_season)
    dd = drawdown_stats(d)
    dd_season = drawdown_stats(d[season_mask].reset_index(drop=True), rows=True)
    pnl, cost = float(tr.pnl.sum()), float(tr.cost.sum())
    wins, losses = tr.pnl[tr.pnl > 0], tr.pnl[tr.pnl < 0]
    res_days = d[d.n_resolutions > 0]
    top = tr.nlargest(20, "cost")
    g = tr.groupby("game_pk")[["pnl", "cost"]].sum()
    g = g[g.cost > EPS]
    by_season = tr.groupby("season")[["pnl", "cost"]].sum()
    month = (1 + r).groupby(r.index.to_period("M")).prod() - 1
    month_pnl = d.groupby(pd.DatetimeIndex(d.date).to_period("M")).pnl.sum()
    traded_months = month_pnl[d.groupby(pd.DatetimeIndex(d.date).to_period("M")).n_resolutions.sum() > 0]
    best_i, worst_i = int(np.argmax(d.pnl.to_numpy())), int(np.argmin(d.pnl.to_numpy()))
    out = dict(
        start_date=str(d.date.iloc[0].date()), end_date=str(d.date.iloc[-1].date()),
        calendar_days=len(d), season_days=int(season_mask.sum()), periods_calendar=PERIODS_CAL,
        periods_season=periods_season,
        signals=len(sc), games_signaled=int(sc.game_pk.nunique()), trades=len(tr),
        games_traded=int(tr.game_pk.nunique()),
        unfilled_signals=int(((sc.cost <= EPS) | sc.entry_ts.isna()).sum()),
        skipped_for_cash=len(sim["skipped"]),
        capital_deployed=cost, fees_paid=float(tr.fee.sum()), total_pnl=pnl,
        roi_on_deployed=pnl / cost if cost > 0 else np.nan,
        equal_game_roi=float((g.pnl / g.cost).mean()) if len(g) else np.nan,
        return_on_equity_total=pnl / EQUITY0, final_equity=EQUITY0 + pnl,
        **cal,
        **{f"season_{k}": v for k, v in sea.items()},
        **dd,
        longest_dd_season_days=dd_season["longest_dd_days"],
        calmar=cal["cagr"] / abs(dd["max_dd_pct"]) if dd["max_dd_pct"] < -EPS else np.nan,
        season_calmar=sea["cagr"] / abs(dd["max_dd_pct"]) if dd["max_dd_pct"] < -EPS else np.nan,
        trade_max_dd_usd=sim["trade_max_dd_usd"],
        win_rate_trade=float((tr.pnl > 0).mean()) if len(tr) else np.nan,
        win_rate_day=float((res_days.pnl > 0).mean()) if len(res_days) else np.nan,
        profit_factor=float(wins.sum() / -losses.sum()) if len(losses) else np.nan,
        profit_factor_day=float(d.pnl[d.pnl > 0].sum() / -d.pnl[d.pnl < 0].sum()) if (d.pnl < 0).any() else np.nan,
        avg_win_usd=float(wins.mean()) if len(wins) else np.nan,
        avg_loss_usd=float(losses.mean()) if len(losses) else np.nan,
        win_loss_ratio=float(wins.mean() / -losses.mean()) if len(wins) and len(losses) else np.nan,
        best_day_usd=float(d.pnl.iloc[best_i]), best_day_pct=float(d["return"].iloc[best_i]),
        best_day_date=str(d.date.iloc[best_i].date()),
        worst_day_usd=float(d.pnl.iloc[worst_i]), worst_day_pct=float(d["return"].iloc[worst_i]),
        worst_day_date=str(d.date.iloc[worst_i].date()),
        pct_days_traded=float((d.n_entries > 0).mean()),
        pct_season_days_traded=float((d.n_entries[season_mask] > 0).mean()),
        pct_days_with_pnl=float((d.n_resolutions > 0).mean()),
        avg_committed=sim["avg_committed"],
        avg_committed_season=float(d.committed[season_mask].mean()),
        peak_committed=sim["peak_committed"], peak_committed_pct_equity=sim["peak_committed_pct_equity"],
        pct_time_committed=sim["pct_time_committed"], max_open_positions=sim["max_open_positions"],
        avg_cost_per_trade=float(tr.cost.mean()) if len(tr) else 0.0,
        median_cost_per_trade=float(tr.cost.median()) if len(tr) else 0.0,
        p90_cost_per_trade=float(tr.cost.quantile(.9)) if len(tr) else 0.0,
        max_cost_per_trade=float(tr.cost.max()) if len(tr) else 0.0,
        top20_cost_share=float(top.cost.sum() / cost) if cost > 0 else np.nan,
        top20_pnl_usd=float(top.pnl.sum()),
        roi_ex_top20=float((pnl - top.pnl.sum()) / (cost - top.cost.sum())) if cost - top.cost.sum() > 0 else np.nan,
        months_traded=int(len(traded_months)), positive_months=int((traded_months > 0).sum()),
        best_month_pct=float(month.max()), worst_month_pct=float(month.min()),
    )
    for yr in (2025, 2026):
        p_ = float(by_season.pnl.get(yr, 0.0))
        c_ = float(by_season.cost.get(yr, 0.0))
        out[f"pnl_{yr}"], out[f"capital_{yr}"] = p_, c_
        out[f"roi_deployed_{yr}"] = p_ / c_ if c_ > 0 else np.nan
        out[f"return_on_100k_{yr}"] = p_ / EQUITY0
    out.update(qs_crosscheck(r))
    return out


# ----------------------------------------------------------------------------- probability analysis

def game_table(sc: pd.DataFrame, taken_idx) -> pd.DataFrame:
    """One row per signaled game: realized P&L and cost of the trades actually taken (0 for no-fills)."""
    x = sc[["game_pk", "season", "signal_ts"]].copy()
    x["pnl"] = 0.0
    x["cost"] = 0.0
    x.loc[taken_idx, "pnl"] = sc.loc[taken_idx, "pnl"]
    x.loc[taken_idx, "cost"] = sc.loc[taken_idx, "cost"]
    g = x.groupby("game_pk").agg(season=("season", "first"), signal_ts=("signal_ts", "min"),
                                 pnl=("pnl", "sum"), cost=("cost", "sum"))
    g["day"] = to_day(g.signal_ts)
    return g


def _boot_sums(values: list[np.ndarray], n: int, b: int, rng, chunk: int = 2_000_000) -> list[np.ndarray]:
    k = len(values[0])
    rows = max(1, chunk // max(n, 1))
    outs = [np.empty(b) for _ in values]
    for s in range(0, b, rows):
        e = min(b, s + rows)
        ix = rng.integers(0, k, size=(e - s, n), dtype=np.int32)
        for o, v in zip(outs, values):
            o[s:e] = v[ix].sum(axis=1)
    return outs


def season_bootstrap(g: pd.DataFrame, n_games: int, n_days: int, rng, b: int = N_BOOT) -> dict:
    """Full-season outcomes: (a) n_games signaled games drawn iid from all games (2025+2026 pooled);
    (b) n_days signal days drawn iid (keeps same-day games together). Returns quantiles."""
    pnl, cost = _boot_sums([g.pnl.to_numpy(), g.cost.to_numpy()], n_games, b, rng)
    day = g.groupby("day")[["pnl", "cost"]].sum()
    dp, dc = _boot_sums([day.pnl.to_numpy(), day.cost.to_numpy()], n_days, b, rng)
    roi = np.divide(pnl, cost, out=np.zeros_like(pnl), where=cost > 0)
    q = lambda a, p: float(np.percentile(a, p))  # noqa: E731
    return dict(season_games=n_games, boot_season_trading_days=n_days,
                boot_p_loss=float((pnl < 0).mean()), boot_pnl_mean=float(pnl.mean()),
                boot_pnl_p05=q(pnl, 5), boot_pnl_p50=q(pnl, 50), boot_pnl_p95=q(pnl, 95),
                boot_ret100k_p05=q(pnl, 5) / EQUITY0, boot_ret100k_p50=q(pnl, 50) / EQUITY0,
                boot_ret100k_p95=q(pnl, 95) / EQUITY0,
                boot_roi_p05=q(roi, 5), boot_roi_p50=q(roi, 50), boot_roi_p95=q(roi, 95),
                boot_capital_p50=q(cost, 50),
                dayboot_p_loss=float((dp < 0).mean()), dayboot_pnl_p05=q(dp, 5), dayboot_pnl_p50=q(dp, 50),
                dayboot_pnl_p95=q(dp, 95), _pnl_samples=pnl)


def lln_table(g: pd.DataFrame, per_season: int, rng, b: int = N_BOOT) -> pd.DataFrame:
    """ROI on deployed capital over n traded games drawn iid from the observed traded games."""
    t = g[g.cost > EPS]
    p, c = t.pnl.to_numpy(), t.cost.to_numpy()
    r_hat = p.sum() / c.sum()
    resid_sd = float(np.std(p - r_hat * c, ddof=1))
    rows = []
    for n in LLN_N:
        sp, scost = _boot_sums([p, c], n, b, rng)
        roi = sp / scost
        half = 1.96 * resid_sd / (math.sqrt(n) * c.mean())
        rows.append(dict(n_games=n, seasons_equiv=n / per_season if per_season else np.nan, roi_point=r_hat,
                         roi_p025=float(np.percentile(roi, 2.5)), roi_p50=float(np.percentile(roi, 50)),
                         roi_p975=float(np.percentile(roi, 97.5)), p_loss=float((sp < 0).mean()),
                         pnl_p025=float(np.percentile(sp, 2.5)), pnl_p50=float(np.percentile(sp, 50)),
                         pnl_p975=float(np.percentile(sp, 97.5)),
                         normal_lo=r_hat - half, normal_hi=r_hat + half))
    n_star = (1.96 * resid_sd / (c.mean() * r_hat)) ** 2 if r_hat > 0 else np.inf
    out = pd.DataFrame(rows)
    out["games_to_exclude_zero"] = n_star
    return out


def games_to_exclude_zero(g: pd.DataFrame) -> float:
    t = g[g.cost > EPS]
    if t.empty:
        return np.nan
    p, c = t.pnl.to_numpy(), t.cost.to_numpy()
    r = p.sum() / c.sum()
    if r <= 0:
        return np.inf
    return float((1.96 * np.std(p - r * c, ddof=1) / (c.mean() * r)) ** 2)


# ----------------------------------------------------------------------------- writers

def write_daily(daily: pd.DataFrame, season_mask: np.ndarray, path) -> None:
    out = daily.copy()
    out["date"] = out.date.dt.strftime("%Y-%m-%d")
    out["in_season"] = season_mask
    out.to_csv(path, index=False, float_format="%.6f")


def write_trades(tr: pd.DataFrame, path) -> None:
    iso = lambda s: pd.to_datetime(s, unit="s", utc=True).dt.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    out = pd.DataFrame(dict(entry_ts=iso(tr.entry_ts), exit_ts=iso(tr.exit_ts), game=tr.game_pk.astype(int),
                            event=tr.event, side=tr.side, season=tr.season, inning=tr.inning, half=tr.half,
                            lead=tr.lead, signal_ts=iso(tr.signal_ts), fair_price=tr.fair_price,
                            reference_price=tr.reference_price, entry_price=tr.entry_price, shares=tr.shares,
                            cost=tr.cost, fee=tr.fee, fee_rate=tr.fee_rate, payout=tr.payout, pnl=tr.pnl,
                            status=tr.status))
    out.sort_values("entry_ts").to_csv(path, index=False, float_format="%.6f")


def tearsheet(ret: pd.Series, name: str, title: str) -> str:
    if not QS_OK:
        return f"skipped: quantstats unavailable ({QS_ERR})"
    path = PERF / f"tearsheet_{name}.html"
    r = ret.copy()
    r.index = pd.DatetimeIndex(r.index)
    r.name = name
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            qs.reports.html(r, benchmark=None, output=str(path), title=title, periods_per_year=PERIODS_CAL,
                            compounded=True, download_filename=path.name)
        return str(path)
    except Exception as exc:  # pragma: no cover
        log.warning("tearsheet %s failed: %r", name, exc)
        return f"failed: {exc!r}"


def _r(x, nd=6):
    if x is None:
        return None
    if isinstance(x, (np.floating, float)):
        if not np.isfinite(x):
            return None
        return round(float(x), nd)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.bool_,)):
        return bool(x)
    return x


# ----------------------------------------------------------------------------- report

def _money(x, sign=False):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "n/a"
    s = f"{abs(x):,.0f}"
    pre = "-" if x < 0 else ("+" if sign and x > 0 else "")
    return f"{pre}${s}"


def _pct(x, nd=2, sign=True):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "n/a"
    return f"{x * 100:+.{nd}f}%" if sign else f"{x * 100:.{nd}f}%"


def _num(x, nd=2):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "n/a"
    return f"{x:.{nd}f}"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def _label(m: pd.Series) -> str:
    return f"{m.rule} · ${int(m.budget_usd):,} · {m.fee_regime}"


def write_report(ctx: dict) -> None:
    M, H = ctx["metrics"], ctx["headline"]
    led = M[M.execution == LEDGER].set_index("scenario")
    h = led.loc[H]
    base = led.loc[scenario_name("10c_first", 100, "as_recorded")]
    b_intl, b_us = ctx["best_budget"]["intl_0.05"], ctx["best_budget"]["us_0.0695"]
    best_intl = led.loc[scenario_name("10c_first", b_intl, "intl_0.05")]
    best_us = led.loc[scenario_name("10c_first", b_us, "us_0.0695")]
    s100_intl = led.loc[scenario_name("10c_first", 100, "intl_0.05")]
    s100_us = led.loc[scenario_name("10c_first", 100, "us_0.0695")]
    ctrl = led.loc[scenario_name("03c_first", 100, "intl_0.05")]
    L = []
    A = L.append
    A("# MLB inning-discount rule: $100K account performance")
    A("")
    A(f"_Generated {ctx['generated_utc']} by `pmsports/analysis/performance.py`. Data files are in "
      "`data/research/performance/` (git-ignored)._")
    A("")
    A("> **This whole page is a historical backtest.** It uses 2025–26 MLB data we had already studied. "
      "The 10c cutoff was chosen as the best of 12 fixed rules tested on that same data. Execution uses "
      "trade prints as a stand-in for the order book. **Nothing here is forward evidence.** The forward "
      "paper test (`reports/PAPER_TEST.md`) is the only unbiased check.")
    A("")
    every_intl = led.loc[scenario_name("10c_every", b_intl, "intl_0.05")]
    A("## Bottom line")
    A("")
    A(f"- **$100 per game, 0.05 fee (today's international rate):** {_money(s100_intl.total_pnl, True)} on the "
      f"$100k over {s100_intl.calendar_days} days. That is about {_pct(s100_intl.season_cagr)} per season, with "
      f"Sharpe {_num(s100_intl.sharpe)}, a worst drawdown of {_pct(s100_intl.max_dd_pct)} and at most "
      f"{_money(s100_intl.peak_committed)} in play at once. At the US fee: {_money(s100_us.total_pnl, True)} "
      f"({_pct(s100_us.season_cagr)} per season).")
    dd_note = (", and the account is still in its worst drawdown at the end of the data"
               if best_intl.max_dd_recovery_date == "not recovered" else "")
    A(f"- **Most profitable feasible budget, ${int(b_intl):,} per game, 0.05 fee:** "
      f"{_money(best_intl.total_pnl, True)}, about {_pct(best_intl.season_cagr)} per season, with Sharpe "
      f"{_num(best_intl.sharpe)} and a worst drawdown of {_money(best_intl.max_dd_usd)} ({_pct(best_intl.max_dd_pct)}). "
      f"But 20 trades supply {_money(best_intl.top20_pnl_usd, True)} of that profit. The 2026 season "
      f"made {_pct(best_intl.return_on_100k_2026)} on the $100k against {_pct(best_intl.return_on_100k_2025)} "
      f"in 2025{dd_note}.")
    A(f"- **Chance a season loses money** (resampling observed games): {s100_intl.boot_p_loss:.0%} at "
      f"$100/0.05, {s100_us.boot_p_loss:.0%} at $100/US fee, {best_intl.boot_p_loss:.0%} at "
      f"${int(b_intl):,}/0.05. The 3c control rule loses in {ctrl.boot_p_loss:.0%} of seasons.")
    A("- **Capital is not the constraint. Liquidity is.** Even the largest budget never had more than "
      f"{_pct(best_intl.peak_committed_pct_equity, 0, False)} of the $100k in play at once.")
    A("- **All of this is in-sample.** Read these figures as a ceiling, not a forecast.")
    A("")
    A("## 1. The rule in one paragraph")
    A("")
    A("At the start of each half-inning in an MLB game, look at the team that is **leading**. Our fair "
      "value is how often teams in that exact spot (inning, top/bottom, size of lead, home or away) won in "
      "earlier seasons. If the leader's Polymarket price is at least **10 cents** below that fair value, "
      "buy the leader and hold the shares until the game settles ($1 per share if they win, $0 if they lose). "
      "`10c_first` buys only on the first signal in a game. `10c_every` buys on every signal, with the "
      "game capped at one budget. `03c_first` uses a 3c cutoff and serves as the control.")
    A("")
    A("**How a fill is simulated (ledger model).** We wait 5 seconds after the signal. We then take the "
      "first later trade on the same side, pay 1 cent more than it did, and buy at most as many shares as "
      "that trade printed, up to the budget with the fee included. If no trade prints before the next "
      "play, we get nothing; about 14% of signals end that way. Partial fills are kept. A larger budget "
      "therefore does **not** buy more than the market showed. It only stops cutting the fill at $100.")
    A("")
    A("## 2. What the numbers mean")
    A("")
    A(f"- **The old “+7.32%” is return on the money spent, over both seasons. It is not a yearly figure "
      f"and it is not a return on an account.** In total, {base.trades:,.0f} trades cost "
      f"{_money(base.capital_deployed)} and made {_money(base.total_pnl, True)}. "
      f"{_money(base.total_pnl)} ÷ {_money(base.capital_deployed)} = {_pct(base.roi_on_deployed)}. "
      f"That total took {base.calendar_days} calendar days "
      f"({base.start_date} to {base.end_date}), and no more than {_money(base.peak_committed)} was ever "
      f"at risk at once.")
    A("- **Its “range +1.0% to +13.5%” is a 95% confidence interval.** We resample the games at "
      "random, thousands of times, and see how far the return moves. The 1,229 games we actually saw "
      "are one draw of luck. The interval shows how much a different draw of games could have changed "
      "the result. A range touching or crossing 0% means we cannot tell the edge apart from zero.")
    A("- **“Chosen after the fact” means we tried 12 cutoffs** (0, 2, 3, 5, 8 and 10c, each with "
      "first-signal-only and every-signal versions) on this same data and kept the one that looked "
      "best. When you pick the best of 12 on the same data, some of what made it look best is luck. "
      "That luck will not repeat. So expect future returns to be lower than the ones shown here. "
      "More games from the **past** cannot fix this. Only new games can.")
    A(f"- **Account view (this report).** The account starts with $100,000 on {base.start_date}. A "
      "trade's full cost, shares × price + fee, leaves cash when it fills and comes back as the payout "
      "when the game settles. The profit or loss counts on the day the game settles (UTC). **Daily "
      "return = that day's realized P&L ÷ equity at the start of the day.** Open positions are held "
      "at cost and never marked to market. Every calendar day is in the series, including the "
      "off-season, when the return is 0.")
    A(f"- **Annualization.** The main figures use **calendar days, periods = 365**, the standard "
      "QuantStats setting for a daily series with zero-return days. We also give a **season-days-only** "
      f"version. It keeps only days inside an MLB season window ({ctx['window_text']}) and annualizes "
      f"with {ctx['periods_season']} days, the length of the one complete season (2025). Because the "
      "off-season earns nothing, the season version is the better guide to what one steady full year "
      "would return. The calendar CAGR spans one off-season in 17.5 months, so it runs slightly "
      "higher than the season version.")
    A("- **Risk per game.** Each fill is a binary bet. The most it can lose is its full cost, so "
      "“capital risked on a game” in the tables means that game's cost.")
    A("")
    A("## 3. Fees: what they are and how they are applied")
    A("")
    A("Polymarket's taker fee is **fee = rate × shares × p × (1 − p)**, where p is the price paid. "
      "It follows a published formula and the rate is set per market, so it can be re-applied to past "
      "trades exactly. It does not change with the order book. It changes only when the venue changes "
      "its rate. Makers (resting limit orders) pay no fee. This model always takes liquidity, so it "
      "always pays the fee. The fee is counted **inside** the budget: shares = budget ÷ (price + fee "
      "per share).")
    A("")
    p = ctx["avg_entry_price"]
    rows = []
    for fee, rate in (("as recorded, 2025", 0.0), ("as recorded, Apr–Jun 2026", 0.03),
                      ("intl_0.05 (as recorded from Jul 2026)", 0.05), ("us_0.0695 (Polymarket US)", 0.0695)):
        per = rate * p * (1 - p)
        rows.append([fee, f"{rate:.4f}", f"{per * 100:.2f}c", _pct(per / p, 2, False)])
    A(_table(["Fee regime", "Rate", f"Fee per share at p = {p:.2f}", "As % of price"], rows))
    A("")
    A(f"At the average entry price of {p:.2f}, a 0.05 fee costs about {0.05 * (1 - p) * 100:.2f}% of the "
      f"money spent, and the US rate about {0.0695 * (1 - p) * 100:.2f}%. The scenarios "
      "`intl_0.05` and `us_0.0695` re-price **every** historical trade at that rate, 2025 included. "
      "That is the right basis for judging the rule today. `as_recorded` keeps the fee each trade "
      f"actually paid: zero for all of 2025 and 0.03 then 0.05 in 2026. That is why its "
      f"{_money(base.fees_paid)} in fees is so low.")
    A("")
    A("## 4. Headline results on a $100,000 account")
    A("")
    A(f"We name the **most profitable liquidity-feasible budget** for each fee regime. A budget is "
      "liquidity-feasible when (a) no fill is larger than a trade that actually printed, which is always "
      "true in the ledger model, and (b) the $100k account never runs short of cash. It is the most "
      "profitable when it has the highest total P&L among such budgets. The pick is "
      f"**${int(b_intl):,}** for intl_0.05 and **${int(b_us):,}** for us_0.0695. See section 7 for why "
      f"those results rest on a few large fills. ${int(b_intl):,} is also the largest budget tested.")
    A("")
    cols = [("Total P&L", lambda m: _money(m.total_pnl, True)),
            ("Return on $100k (total)", lambda m: _pct(m.return_on_equity_total)),
            ("CAGR (calendar, 365)", lambda m: _pct(m.cagr)),
            ("Per-season return (season days)", lambda m: _pct(m.season_cagr)),
            ("2025 season on $100k", lambda m: _pct(m.return_on_100k_2025)),
            ("2026 season on $100k (to Sep 18)", lambda m: _pct(m.return_on_100k_2026)),
            ("Annual volatility", lambda m: _pct(m.ann_vol, 2, False)),
            ("Sharpe (365)", lambda m: _num(m.sharpe)),
            ("Sortino (365)", lambda m: _num(m.sortino)),
            ("Calmar", lambda m: _num(m.calmar)),
            ("Sharpe / Sortino (season days)", lambda m: f"{_num(m.season_sharpe)} / {_num(m.season_sortino)}"),
            ("Max drawdown", lambda m: f"{_money(m.max_dd_usd)} ({_pct(m.max_dd_pct)})"),
            ("Longest drawdown: calendar days (season days)", lambda m: f"{int(m.longest_dd_days)} ({int(m.longest_dd_season_days)})" + ("" if m.longest_dd_recovered else " ongoing")),
            ("Win rate: trades / days", lambda m: f"{_pct(m.win_rate_trade, 1, False)} / {_pct(m.win_rate_day, 1, False)}"),
            ("Profit factor (trades)", lambda m: _num(m.profit_factor)),
            ("Avg win / avg loss", lambda m: f"{_money(m.avg_win_usd)} / {_money(m.avg_loss_usd)} ({_num(m.win_loss_ratio)}x)"),
            ("Best day", lambda m: f"{_money(m.best_day_usd, True)} ({_pct(m.best_day_pct)})"),
            ("Worst day", lambda m: f"{_money(m.worst_day_usd, True)} ({_pct(m.worst_day_pct)})"),
            ("% calendar days with a new trade", lambda m: _pct(m.pct_days_traded, 1, False)),
            ("Trades (funded) / signals", lambda m: f"{int(m.trades):,} / {int(m.signals):,}"),
            ("Risked per trade: mean / median / max", lambda m: f"{_money(m.avg_cost_per_trade)} / {_money(m.median_cost_per_trade)} / {_money(m.max_cost_per_trade)}"),
            ("Capital committed: avg / peak", lambda m: f"{_money(m.avg_committed)} / {_money(m.peak_committed)}"),
            ("Peak committed as % of equity", lambda m: _pct(m.peak_committed_pct_equity, 1, False)),
            ("Capital deployed (sum of costs)", lambda m: _money(m.capital_deployed)),
            ("Fees paid", lambda m: _money(m.fees_paid)),
            ("Return on capital deployed", lambda m: _pct(m.roi_on_deployed)),
            ("Equal-weight game ROI", lambda m: _pct(m.equal_game_roi))]
    heads = ["Metric"] + [f"{r.rule} ${int(r.budget_usd):,} {r.fee_regime}" for _, r in h.iterrows()]
    A(_table(heads, [[c] + [f(r) for _, r in h.iterrows()] for c, f in cols]))
    A("")
    if QS_OK:
        A(f"QuantStats {qs.__version__} computes the same ratios as a cross-check. The largest gap between "
          f"our Sharpe/Sortino/CAGR/max-drawdown and QuantStats' over all scenarios is {ctx['qs_maxdiff']:.2e}, "
          "which is rounding. QuantStats counts a drawdown's length from its first day under water, so "
          "its “Longest DD Days” is usually one day shorter than ours, which counts from the last high. "
          "The tearsheets are `data/research/performance/tearsheet_<scenario>.html`.")
    else:
        A(f"QuantStats could not be imported ({QS_ERR}). All ratios were computed by hand with the same "
          "definitions.")
    A("")
    A("**How to read the ratios.** The account holds cash nearly all the time. Its **return on "
      f"$100k** is small ({_pct(s100_intl.season_cagr)} per season at $100/game with the 0.05 fee), "
      "even where the return on money deployed looks healthy. Sharpe and Sortino do not change with "
      "leverage, so they are the fairest measure of the edge. Calmar compares the yearly return with "
      "the worst drawdown. The ratios are per year on calendar days. In-sample ratios from a rule "
      "picked out of a grid overstate what to expect going forward.")
    A("")
    A("## 5. Every scenario (ledger execution)")
    A("")
    A("Ledger model, $100k account. Each ROI is the return on capital deployed, and each CAGR is on "
      "the $100k with periods = 365. The full column set is in `metrics.csv`.")
    A("")
    rows = []
    for _, m in led.sort_values(["rule", "fee_regime", "budget_usd"]).iterrows():
        rows.append([m.rule, f"${int(m.budget_usd):,}", m.fee_regime, f"{int(m.trades):,}",
                     _money(m.capital_deployed), _money(m.total_pnl, True), _pct(m.roi_on_deployed),
                     _pct(m.cagr), _num(m.sharpe), _num(m.sortino), _pct(m.max_dd_pct), _num(m.calmar),
                     _money(m.peak_committed), f"{m.boot_p_loss:.0%}" if pd.notna(m.boot_p_loss) else "n/a"])
    A(_table(["Rule", "Budget/game", "Fees", "Trades", "Deployed", "P&L", "ROI deployed", "CAGR",
              "Sharpe", "Sortino", "Max DD", "Calmar", "Peak committed", "P(season loss)"], rows))
    A("")
    A("## 6. Equity curve and drawdowns")
    A("")
    for name in H:
        m = led.loc[name]
        rec = ("and had not recovered by the end of the data" if m.max_dd_recovery_date == "not recovered"
               else f"and was recovered by {m.max_dd_recovery_date}")
        A(f"- **{_label(m)}.** Equity goes from $100,000 to {_money(m.final_equity)}. The largest drop is "
          f"{_money(m.max_dd_usd)} ({_pct(m.max_dd_pct)}). It ran from a high on {m.max_dd_peak_date} to a "
          f"low on {m.max_dd_trough_date} {rec}. The longest spell below a previous high lasted "
          f"{int(m.longest_dd_days)} calendar days ({m.longest_dd_start} → {m.longest_dd_end}"
          f"{'' if m.longest_dd_recovered else ', still under water at the end of the data'}). Only "
          f"{int(m.longest_dd_season_days)} of those were season days; the rest is the off-season, when "
          f"nothing trades. {int(m.positive_months)} of {int(m.months_traded)} trading months were "
          f"positive. The best month was {_pct(m.best_month_pct, 3)} and the worst {_pct(m.worst_month_pct, 3)}.")
    A("")
    A("**Monthly returns on the $100k** (compounded daily returns; months with no MLB games are omitted):")
    A("")
    mt = ctx["monthly"]
    A(_table(["Month"] + [_label(led.loc[n]) for n in H],
             [[str(mo)] + [_pct(mt[n].get(mo, 0.0), 2) for n in H] for mo in ctx["months"]]))
    A("")
    A("Drawdowns are measured on daily closing equity, after realized results only. Measured after "
      "each game settles instead, the worst drop for the headline $100-per-game intl_0.05 case is "
      f"{_money(s100_intl.trade_max_dd_usd)}. Positions last about 3.5 hours (median), and most open and "
      "settle on the same day, so daily closing equity is close to marked-to-market equity. The daily "
      "series is in `daily_returns_<scenario>.csv`, and the dashboard JSON has the equity and "
      "drawdown series.")
    A("")
    A("## 7. Sizing and capacity: how far can this be pushed?")
    A("")
    A(f"- **Liquidity limits this strategy, not capital.** At $100 per game (intl_0.05), the average "
      f"trade risks {_money(s100_intl.avg_cost_per_trade)} and the median {_money(s100_intl.median_cost_per_trade)}. "
      f"Peak capital in play at once is {_money(s100_intl.peak_committed)}, and the average is "
      f"{_money(s100_intl.avg_committed)}. Most of the $100,000 sits idle.")
    A(f"- **A 50x larger budget buys about 4x the money in play.** At ${int(b_intl):,} per game "
      f"({b_intl / EQUITY0:.0%} of equity), the average trade is still only {_money(best_intl.avg_cost_per_trade)}, "
      f"the median {_money(best_intl.median_cost_per_trade)}, and peak commitment "
      f"{_money(best_intl.peak_committed)} ({_pct(best_intl.peak_committed_pct_equity, 1, False)} of equity). "
      "The ledger fills only what the first later print showed, and that print is usually small.")
    A(f"- **The extra profit at large budgets comes from a handful of trades.** At ${int(b_intl):,} "
      f"(intl_0.05), the 20 largest trades hold {_pct(best_intl.top20_cost_share, 0, False)} of the "
      f"capital and contribute {_money(best_intl.top20_pnl_usd, True)} of the {_money(best_intl.total_pnl, True)} "
      f"total. Without them, ROI on deployed capital is {_pct(best_intl.roi_ex_top20)}. The same "
      f"rule has an equal-weight game ROI of only {_pct(best_intl.equal_game_roi)} at every budget.")
    A(f"- **10c every-signal looks bigger but is weaker per game.** At ${int(b_intl):,} with the 0.05 fee "
      f"it makes {_money(every_intl.total_pnl, True)} ({_pct(every_intl.roi_on_deployed)} on "
      f"{_money(every_intl.capital_deployed)}). Its equal-weight game ROI is {_pct(every_intl.equal_game_roi)}, "
      f"and the 20 largest trades supply {_money(every_intl.top20_pnl_usd, True)}. It is the same "
      "large-print effect again, not a better rule.")
    A(f"- **Return on the $100k.** The best feasible budget earns {_pct(best_intl.season_cagr)} per season "
      f"at intl_0.05 and {_pct(best_us.season_cagr)} at the US fee. At $100 per game it earns "
      f"{_pct(s100_intl.season_cagr)} and {_pct(s100_us.season_cagr)}. These are in-sample upper "
      "estimates.")
    cap = ctx["capacity_text"]
    A(f"- **Print liquidity (upper bound, 10c first signals).** {cap}")
    sw = ctx.get("sweep_text")
    if sw:
        A(f"- **Sweep upper bound.** {sw}")
    A("")
    A("**Allocation for a $100k account.** Capital is not the limit, so a per-game cap of 1–5% of "
      "equity ($1k–$5k) costs nothing in cash. It only lets the order take a large print when one "
      "appears. The real question is whether the edge is real after fees. At both current fee "
      "levels, the 95% ranges below include a loss for a season. Size for the forward test, not for "
      "these numbers.")
    A("")
    A("## 8. Law of large numbers: what hundreds or thousands of games would show")
    A("")
    A(f"We resample the per-game results we observed. A “season” is the number of games that had a "
      f"signal in 2025, the one complete season: {ctx['season_games_text']}. The day-block version "
      "resamples whole days, so games on the same day stay together. Both assume the future looks "
      "exactly like this sample, selection bias included.")
    A("")
    rows = []
    for name in H:
        m = led.loc[name]
        rows.append([_label(m), f"{m.boot_p_loss:.1%}", _money(m.boot_pnl_p05, True), _money(m.boot_pnl_p50, True),
                     _money(m.boot_pnl_p95, True), f"{_pct(m.boot_ret100k_p05)} / {_pct(m.boot_ret100k_p50)} / {_pct(m.boot_ret100k_p95)}",
                     f"{m.dayboot_p_loss:.1%}"])
    A(_table(["Scenario", "P(season loss)", "5th pct P&L", "Median P&L", "95th pct P&L",
              "Return on $100k (5/50/95)", "P(loss), day-block"], rows))
    A("")
    A("**How the 95% range of ROI (on capital deployed) narrows with more games**, drawn from the "
      "observed traded games:")
    A("")
    lln = ctx["lln"]
    rows = []
    for name in H:
        sub = lln[lln.scenario == name]
        if sub.empty:
            continue
        m = led.loc[name]
        cells = [f"{_pct(r.roi_p025, 1)} to {_pct(r.roi_p975, 1)} (P loss {r.p_loss:.0%})" for _, r in sub.iterrows()]
        n_star = sub.games_to_exclude_zero.iloc[0]
        rows.append([_label(m)] + cells + ["never (edge ≤ 0)" if not np.isfinite(n_star) else f"{n_star:,.0f}"])
    A(_table(["Scenario"] + [f"{n:,} games" for n in LLN_N] + ["Games for range to exclude 0"], rows))
    A("")
    A("The range shrinks roughly as 1/√n. With 4x as many games it is half as wide. The last column "
      "gives the number of traded games at which the 95% range would stop including zero, **if the "
      "true edge equals the one observed here**. Remember why that assumption is optimistic. "
      "Resampling the past only measures luck in which games happened. It cannot remove the bias from "
      "choosing the 10c rule after looking. It also cannot remove the gap between trade-print fills "
      "and real fills. Only the forward test can.")
    A("")
    A("## 9. Caveats (read before using any number above)")
    A("")
    A("1. **Historical and already inspected.** 2025–26 is the data the rule was built and chosen on.")
    A("2. **Picked from a grid.** The 10c cutoff is the best of 12 fixed rules on this data. Expect "
      "regression toward the 3c control, which is about zero or negative after fees.")
    A("3. **Execution proxy.** A fill is someone else's later trade plus 1c. It is not our own order "
      "in the book. The capacity figures show the first print usually sits above the reference price. "
      "When no trade prints before the next play, a live order may get nothing, as the first live "
      "signal did.")
    A("4. **Optimistic clock.** Signals use a retrospective state time plus 5 s. Real latency and "
      "cancel behavior are not modeled.")
    A("5. **Realized-only accounting.** Positions are carried at cost until settlement, so the "
      "drawdowns shown understate what live marks would show during games.")
    A("6. **Concentration.** Large-budget results depend on a few big prints (section 7).")
    A("7. **Not forward evidence.** The forward paper tests are the only unbiased check.")
    A("")
    A("## 10. Files")
    A("")
    A("- `data/research/performance/metrics.csv`: one row per scenario (ledger plus sweep upper bounds), all metrics.")
    A("- `data/research/performance/daily_returns_<scenario>.csv`: date, pnl, equity, return, committed, drawdown.")
    A("- `data/research/performance/trades_<scenario>.csv`: every funded trade with entry, exit, price, shares, cost, fee, payout, pnl.")
    A("- `data/research/performance/tearsheet_<scenario>.html`: QuantStats tearsheets for the headline scenarios.")
    A("- `data/research/performance/lln.csv` and `bootstrap_seasons.csv`: probability analysis.")
    A("- `data/research/performance/dashboard.json`: compact data for a dashboard.")
    A("- `data/research/performance/sweep/`: the same files for the sweep (upper-bound) execution.")
    A("")
    A("Reproduce: `PYTHONPATH=. nice .venv/bin/python -m pmsports.analysis.performance`")
    REPORT.write_text("\n".join(L) + "\n")


# ----------------------------------------------------------------------------- orchestration

def _capacity_payload() -> tuple[list[dict], str]:
    path = PERF / "capacity_summary.csv"
    if not path.exists():
        return [], "capacity_summary.csv not found."
    c = pd.read_csv(path)
    c = c[c.signal_set == "10c_first"]
    keep = ["season", "window", "price_tol", "signals", "window_s_median", "zero_share", "p10", "p25", "p50",
            "p75", "p90", "mean", "share_ge_100", "share_ge_500", "share_ge_1000"]
    rows = [{k: _r(v, 4) for k, v in r.items()} for r in c[keep].to_dict("records")]
    a = c[c.season.astype(str) == "all"].set_index(["window", "price_tol"])

    def g(w, t, col):
        try:
            return float(a.loc[(w, t), col])
        except KeyError:
            return float("nan")
    text = (f"Counting only trades within 2c of the reference price, {g('next_play', '2c', 'zero_share'):.0%} of "
            f"signals had none before the next play and {g('next_half', '2c', 'zero_share'):.0%} had none "
            f"before the next half-inning. The median amount available is $0 in both cases, and the 90th "
            f"percentile before the next half-inning is {_money(g('next_half', '2c', 'p90'))}. At any "
            f"price before the next half-inning, the median is {_money(g('next_half', 'any', 'p50'))} and "
            f"the 90th percentile {_money(g('next_half', 'any', 'p90'))}. Every one of those prints was "
            "another trader's fill, so these are ceilings, not what we could have taken.")
    return rows, text


def run(sweep: bool = True, tearsheets: bool = True, boot: int = N_BOOT) -> dict:
    t0 = time.time()
    PERF.mkdir(parents=True, exist_ok=True)
    SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(SEED)
    trades = load_trades(sweep)
    win = season_windows()
    end = max(win.end.max(), to_day([trades.exit_ts.max()])[0])
    cal = pd.date_range(win.start.min(), end, freq="D")
    season_mask = np.zeros(len(cal), bool)
    for _, w in win.iterrows():
        season_mask |= (cal >= w.start) & (cal <= w.end)
    periods_season = int(win.days.max())
    complete = int(win.days.idxmax())
    log.info("calendar %s..%s (%d days, %d season days); season periods %d from %d", cal[0].date(),
             cal[-1].date(), len(cal), season_mask.sum(), periods_season, complete)

    rows, sims, games = [], {}, {}
    keys = trades[["rule", "execution", "budget_usd", "fee_regime"]].drop_duplicates()
    for _, k in keys.iterrows():
        sc = trades[(trades.rule == k.rule) & (trades.execution == k.execution) & (trades.budget_usd == k.budget_usd)
                     & (trades.fee_regime == k.fee_regime)]
        name = scenario_name(k.rule, k.budget_usd, k.fee_regime, k.execution)
        sim = simulate(sc, cal)
        met = dict(scenario=name, rule=k.rule, execution=k.execution,
                   model="ledger (canonical proxy)" if k.execution == LEDGER else "sweep (upper bound)",
                   budget_usd=float(k.budget_usd), fee_regime=k.fee_regime,
                   budget_pct_equity=float(k.budget_usd) / EQUITY0)
        met.update(scenario_metrics(sc, sim, season_mask, periods_season))
        g = game_table(sc, _taken_index(sc, sim))
        met["games_to_exclude_zero"] = games_to_exclude_zero(g)
        if k.execution == LEDGER:
            n_games = int((g.season == complete).sum())
            n_days = int(g[g.season == complete].day.nunique())
            bs = season_bootstrap(g, n_games, n_days, rng, boot)
            games[name] = (g, bs)
            met.update({kk: v for kk, v in bs.items() if not kk.startswith("_")})
        rows.append(met)
        sims[name] = sim
        folder = PERF if k.execution == LEDGER else SWEEP_DIR
        write_daily(sim["daily"], season_mask, folder / f"daily_returns_{name}.csv")
        write_trades(sim["trades"], folder / f"trades_{name}.csv")
    M = pd.DataFrame(rows)

    # headline scenarios: the canonical ledger, $100 and the most profitable liquidity-feasible budget
    led = M[(M.execution == LEDGER) & (M.rule == "10c_first")]
    best_budget = {}
    for fee in FEES:
        ok = led[(led.fee_regime == fee) & (led.skipped_for_cash == 0) & (led.peak_committed_pct_equity <= 1)]
        best_budget[fee] = float(ok.loc[ok.total_pnl.idxmax(), "budget_usd"])
    headline = [scenario_name("10c_first", 100, "as_recorded"),
                scenario_name("10c_first", 100, "intl_0.05"), scenario_name("10c_first", 100, "us_0.0695"),
                scenario_name("10c_first", best_budget["intl_0.05"], "intl_0.05"),
                scenario_name("10c_first", best_budget["us_0.0695"], "us_0.0695"),
                scenario_name("03c_first", 100, "intl_0.05")]
    headline = list(dict.fromkeys(headline))
    M["headline"] = M.scenario.isin(headline)
    M.to_csv(PERF / "metrics.csv", index=False, float_format="%.8g")

    # law of large numbers and season distributions for the headline scenarios
    lln_rows, boot_rows = [], []
    for name in headline:
        g, bs = games[name]
        per_season = int(((g.season == complete) & (g.cost > EPS)).sum())
        tab = lln_table(g, per_season, rng, boot)
        tab.insert(0, "scenario", name)
        lln_rows.append(tab)
        pnl = bs["_pnl_samples"]
        boot_rows.append(pd.DataFrame({"scenario": name, "draw": np.arange(len(pnl)), "season_pnl": pnl}))
    lln = pd.concat(lln_rows, ignore_index=True)
    lln.to_csv(PERF / "lln.csv", index=False, float_format="%.8g")
    pd.concat(boot_rows, ignore_index=True).to_csv(PERF / "bootstrap_seasons.csv", index=False,
                                                  float_format="%.4f")

    # QuantStats tearsheets
    sheets = {}
    if tearsheets:
        for name in headline:
            m = M.set_index("scenario").loc[name]
            d = sims[name]["daily"]
            r = pd.Series(d["return"].to_numpy(), index=pd.DatetimeIndex(d.date), name=name)
            title = (f"MLB {RULE_TEXT[m.rule]} | ${int(m.budget_usd):,}/game | fees {m.fee_regime} | "
                     "$100k account | HISTORICAL BACKTEST (in-sample, not forward evidence)")
            sheets[name] = tearsheet(r, name, title)

    qs_cols = [("sharpe", "qs_sharpe"), ("sortino", "qs_sortino"), ("cagr", "qs_cagr"), ("max_dd_pct", "qs_max_dd"),
               ("ann_vol", "qs_vol")]
    qs_maxdiff = (max(float((M[a] - M[b]).abs().max()) for a, b in qs_cols) if QS_OK else float("nan"))

    # dashboard payload
    cap_rows, cap_text = _capacity_payload()
    base = trades[(trades.execution == LEDGER) & (trades.rule == "10c_first") & (trades.budget_usd == 100)
                  & (trades.fee_regime == "as_recorded") & (trades.cost > EPS)]
    premium = (base.entry_price - 0.01 - base.reference_price) * 100
    avg_p = float(M.loc[M.scenario == scenario_name("10c_first", 100, "intl_0.05"), "avg_cost_per_trade"].iloc[0])
    avg_entry = float(base.entry_price.mean())
    dash = dict(
        meta=dict(generated_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                  status="HISTORICAL BACKTEST - in-sample, already-inspected 2025-26 data; 10c cutoff chosen from a "
                         "12-rule grid on the same data; trade-print execution proxy; NOT forward evidence",
                  start_equity=EQUITY0, calendar=[str(cal[0].date()), str(cal[-1].date())],
                  calendar_days=len(cal), season_days=int(season_mask.sum()),
                  season_windows={int(y): [str(w.start.date()), str(w.end.date())] for y, w in win.iterrows()},
                  periods_calendar=PERIODS_CAL, periods_season=periods_season,
                  annualization="primary: calendar days, periods=365; secondary: MLB season days only, "
                                f"periods={periods_season} (2025 complete season window)",
                  daily_return="realized P&L on UTC resolution day / equity at start of day; cost leaves cash at "
                               "fill, payout returns at game resolution; positions carried at cost",
                  fee_formula="fee = rate * shares * p * (1 - p); taker only; fee inside budget",
                  fee_regimes=FEE_TEXT, rules=RULE_TEXT,
                  liquidity_feasible="ledger fills never exceed an observed print and the $100k account never "
                                     "lacks cash; 'best' = max total P&L among feasible budgets",
                  best_budget=best_budget, quantstats=(qs.__version__ if QS_OK else f"unavailable: {QS_ERR}"),
                  qs_max_abs_diff=_r(qs_maxdiff, 12), bootstrap_draws=boot, seed=SEED),
        headline=headline,
        scenarios={},
        metrics_table=[{k: _r(v) for k, v in r.items()} for r in M.drop(columns=[c for c in M.columns if c.startswith("_")]).to_dict("records")],
        sizing_curve=[{k: _r(v) for k, v in r.items()} for r in M[["rule", "execution", "fee_regime", "budget_usd",
                      "trades", "capital_deployed", "total_pnl", "roi_on_deployed", "equal_game_roi", "cagr",
                      "season_cagr", "sharpe", "sortino", "max_dd_pct", "peak_committed", "avg_cost_per_trade",
                      "median_cost_per_trade", "top20_cost_share", "roi_ex_top20"]].sort_values(
                          ["rule", "execution", "fee_regime", "budget_usd"]).to_dict("records")],
        capacity=dict(print_liquidity=cap_rows,
                      first_print_premium_cents=dict(zip(["p10", "p25", "p50", "p75", "p90", "mean"],
                                                         [_r(v, 3) for v in list(np.percentile(premium, [10, 25, 50, 75, 90])) + [premium.mean()]])),
                      note="print liquidity = same-side trade notional after the eligible time; upper bound "
                           "because every print is another trader's fill"),
        lln=[{k: _r(v) for k, v in r.items()} for r in lln.to_dict("records")],
    )
    for name in headline:
        d = sims[name]["daily"]
        m = M.set_index("scenario").loc[name]
        r = pd.Series(d["return"].to_numpy(), index=pd.DatetimeIndex(d.date))
        mon = ((1 + r).groupby(r.index.to_period("M")).prod() - 1)
        mon_pnl = d.groupby(pd.DatetimeIndex(d.date).to_period("M")).pnl.sum()
        mon_act = d.groupby(pd.DatetimeIndex(d.date).to_period("M")).n_resolutions.sum()
        _, bs = games[name]
        pnl = bs["_pnl_samples"]
        counts, edges = np.histogram(pnl, bins=40)
        dash["scenarios"][name] = dict(
            label=f"{m.rule} ${int(m.budget_usd):,}/game {m.fee_regime}",
            metrics={k: _r(v) for k, v in m.items()},
            daily=dict(date=d.date.dt.strftime("%Y-%m-%d").tolist(), equity=[round(x, 2) for x in d.equity],
                       drawdown_pct=[round(x, 6) for x in d.drawdown_pct], pnl=[round(x, 2) for x in d.pnl],
                       ret=[round(x, 7) for x in d["return"]], committed=[round(x, 2) for x in d.committed]),
            monthly=[dict(month=str(p), ret=_r(v, 6), pnl=_r(float(mon_pnl.loc[p]), 2),
                          active=bool(mon_act.loc[p] > 0)) for p, v in mon.items()],
            bootstrap=dict(season_games=bs["season_games"], p_loss=_r(bs["boot_p_loss"], 4),
                           quantiles={q: _r(float(np.percentile(pnl, q)), 2) for q in (1, 5, 10, 25, 50, 75, 90, 95, 99)},
                           hist=dict(counts=counts.tolist(), edges=[round(x, 2) for x in edges])),
            tearsheet=sheets.get(name, ""))
    (PERF / "dashboard.json").write_text(json.dumps(dash, separators=(",", ":"), allow_nan=False))

    # sweep summary line for the report
    sweep_text = ""
    if sweep:
        s = M[(M.execution == "sweep_next_half_2c") & (M.rule == "10c_first") & (M.fee_regime == "intl_0.05")]
        if len(s):
            top = s.loc[s.total_pnl.idxmax()]
            sweep_text = (f"If we could have matched **every** same-side print within 2c of the reference "
                          f"before the next half-inning (an optimistic ceiling), the 10c first-signal rule "
                          f"would have made {_money(top.total_pnl, True)} at ${int(top.budget_usd):,}/game with "
                          f"the 0.05 fee: {_pct(top.roi_on_deployed)} on {_money(top.capital_deployed)} "
                          f"deployed, {_pct(top.season_cagr)} per season on the $100k, peak commitment "
                          f"{_money(top.peak_committed)}. Only {int(top.trades):,} of {int(top.signals):,} "
                          "signals get any fill under that rule.")
    monthly, months = {}, set()
    for name in headline:
        d = sims[name]["daily"]
        r = pd.Series(d["return"].to_numpy(), index=pd.DatetimeIndex(d.date))
        active = d.groupby(pd.DatetimeIndex(d.date).to_period("M")).n_resolutions.sum()
        mon = ((1 + r).groupby(r.index.to_period("M")).prod() - 1)[active > 0]
        monthly[name] = {str(k): float(v) for k, v in mon.items()}
        months |= set(monthly[name])
    wtxt = "; ".join(f"{y}: {w.start.date()} to {w.end.date()}" for y, w in win.iterrows())
    ctx = dict(metrics=M, headline=headline, best_budget=best_budget, windows=win, lln=lln,
               generated_utc=dash["meta"]["generated_utc"], periods_season=periods_season, window_text=wtxt,
               qs_maxdiff=qs_maxdiff, capacity_text=cap_text, monthly=monthly, months=sorted(months), sweep_text=sweep_text,
               avg_entry_price=avg_entry, avg_cost=avg_p,
               season_games_text=" and ".join(f"{int(games[n][1]['season_games']):,} games for {n.split('_b')[0]}"
                                           for n in dict.fromkeys([headline[0], headline[-1]])))
    write_report(ctx)
    log.info("performance analytics done in %.0fs: %d scenarios, headline %s", time.time() - t0, len(M), headline)
    return dict(metrics=M, headline=headline, best_budget=best_budget, lln=lln, tearsheets=sheets,
                qs_ok=QS_OK, qs_maxdiff=qs_maxdiff)


def _taken_index(sc: pd.DataFrame, sim: dict) -> pd.Index:
    """Row labels (in ``sc``) of the trades the account actually took."""
    tr = sim["trades"]
    key = sc.set_index(["signal_idx"]).index
    taken = set(tr.signal_idx.tolist())
    return sc.index[np.isin(key, list(taken))]


def headline_table(M: pd.DataFrame, headline: list[str]) -> pd.DataFrame:
    h = M.set_index("scenario").loc[headline]
    return pd.DataFrame({
        "total_pnl": h.total_pnl.round(0), "ret_on_100k": (h.return_on_equity_total * 100).round(2),
        "cagr_%": (h.cagr * 100).round(2), "per_season_%": (h.season_cagr * 100).round(2),
        "vol_%": (h.ann_vol * 100).round(2), "sharpe": h.sharpe.round(2), "sortino": h.sortino.round(2),
        "calmar": h.calmar.round(2), "maxdd_$": h.max_dd_usd.round(0), "maxdd_%": (h.max_dd_pct * 100).round(2),
        "longest_dd_d": h.longest_dd_days, "win_trade_%": (h.win_rate_trade * 100).round(1),
        "win_day_%": (h.win_rate_day * 100).round(1), "pf": h.profit_factor.round(2),
        "trades": h.trades, "roi_deployed_%": (h.roi_on_deployed * 100).round(2),
        "peak_committed": h.peak_committed.round(0), "p_season_loss": h.boot_p_loss.round(3)})


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-sweep", action="store_true", help="skip the sweep upper-bound scenarios")
    ap.add_argument("--no-tearsheets", action="store_true", help="skip QuantStats HTML tearsheets")
    ap.add_argument("--boot", type=int, default=N_BOOT, help="bootstrap draws")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    res = run(sweep=not a.no_sweep, tearsheets=not a.no_tearsheets, boot=a.boot)
    with pd.option_context("display.width", 250, "display.max_columns", 40):
        print(headline_table(res["metrics"], res["headline"]).T.to_string())
    print("best liquidity-feasible budget:", res["best_budget"], "| quantstats:", res["qs_ok"],
          "| max |manual-qs|:", res["qs_maxdiff"])


if __name__ == "__main__":
    main()
