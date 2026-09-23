"""The four hypothesis tests.

H1 calibration   Do pregame favorites win as often as their price implies? Does
                 "always bet the favorite" (or the dog) make money after fees?
H2 state tables  Given (inning, half, score diff), what does the market price and
                 how often does that team actually win? Mean/std of the price.
H3 fair value    Fit a model on older games (pregame price + game state), trade
                 newer games when the market deviates from it, hold to resolution.
H4 latency       After a scoring play (ball in play), how many seconds until the
                 price moves, and are stale fills still available N seconds later?
"""
from __future__ import annotations

from ..execution import panel_entries
from ..research.common import cluster_ci

import numpy as np
import pandas as pd
import statsmodels.api as sm

from ..panel import TRADE_TS_LAG_S  # on-chain settlement lag, measured vs CLOB websocket
from .stats import bet_pnl, bootstrap_mean, brier, log_loss, logit, wilson

CURRENT_FEE = 0.05   # sports taker fee rate since 2026-07


# ----------------------------------------------------------------------------- H1

def h1_calibration(pregame: pd.DataFrame, slip: float = 0.01) -> dict:
    df = pregame.dropna(subset=["pre_p", "home_won_final"]).copy()
    df = df[df.pre_p.between(0.02, 0.98)]
    df["won"] = df.home_won_final.astype(float)
    df["fav_p"] = np.maximum(df.pre_p, 1 - df.pre_p)
    df["fav_won"] = np.where(df.pre_p >= 0.5, df.won, 1 - df.won)
    df["fee_rate"] = df.fee_rate.fillna(0.0)

    bins = [0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 1.0]
    df["bucket"] = pd.cut(df.fav_p, bins, include_lowest=True)
    t = df.groupby("bucket", observed=True).agg(games=("won", "size"), implied=("fav_p", "mean"),
                                                 actual=("fav_won", "mean"))
    lo, hi = wilson(t.actual * t.games, t.games)
    t["ci95"] = [f"{a:.3f}-{b:.3f}" for a, b in zip(lo, hi)]
    t["actual_minus_implied"] = t.actual - t.implied

    # calibration slope: won ~ logit(p). slope 1 / intercept 0 == perfectly calibrated
    X = sm.add_constant(logit(df.pre_p))
    fit = sm.Logit(df.won.values, X).fit(disp=0)

    strategies = []
    for name, side_p, side_won in [
        ("bet favorite", df.fav_p, df.fav_won),
        ("bet underdog", 1 - df.fav_p, 1 - df.fav_won),
        ("bet home", df.pre_p, df.won),
        ("bet away", 1 - df.pre_p, 1 - df.won),
    ]:
        for fee_label, rate in [("no fee", 0.0), ("5% sports fee", CURRENT_FEE)]:
            for s in (0.0, slip):
                r = bet_pnl(side_p, side_won, rate, s)
                ci = bootstrap_mean(r.roi)
                strategies.append({"strategy": name, "fee": fee_label, "slippage": s,
                                   "bets": len(r), "win_rate": float(np.mean(side_won)),
                                   "avg_price": float(np.mean(side_p)),
                                   "roi_per_$": r.roi.mean(), "roi_ci95": f"{ci[0]:+.3f} to {ci[1]:+.3f}"})
    return {
        "n_games": len(df),
        "brier_market": brier(df.won, df.pre_p),
        "brier_coinflip": brier(df.won, np.full(len(df), 0.5)),
        "logloss_market": log_loss(df.won, df.pre_p),
        "calib_intercept": float(fit.params[0]), "calib_slope": float(fit.params[1]),
        "calib_slope_se": float(fit.bse[1]),
        "fav_win_rate": float(df.fav_won.mean()), "fav_avg_price": float(df.fav_p.mean()),
        "table": t.reset_index(),
        "strategies": pd.DataFrame(strategies),
        "execution_note": "Descriptive pregame-price calibration and synthetic cost sensitivities; no available quote/size is established.",
        "by_season": df.assign(season=df.event_date.str[:4]).groupby("season").agg(
            games=("won", "size"), fav_price=("fav_p", "mean"), fav_win=("fav_won", "mean")).reset_index(),
    }


# ----------------------------------------------------------------------------- H2

def _inn(x):
    return np.minimum(x, 10)   # 10 == extras


def h2_state_tables(panel: pd.DataFrame, baseline: pd.DataFrame) -> dict:
    """Leader-perspective tables at half-inning starts ("going into inning z")."""
    out = {}
    # Baseline: MLB-only seasons + market seasons, ground-truth win rates by lead & inning
    b = baseline[baseline.checkpoint & (baseline.half == "top") & (baseline["diff"] != 0)].copy()
    b["lead"] = np.minimum(b["diff"].abs(), 6)
    b["leader_is_home"] = b["diff"] > 0
    b["leader_won"] = np.where(b.leader_is_home, b.home_won_final, ~b.home_won_final.astype(bool)).astype(float)
    b["inning"] = _inn(b.inning)
    wt = b.pivot_table(index="inning", columns="lead", values="leader_won", aggfunc="mean")
    nt = b.pivot_table(index="inning", columns="lead", values="leader_won", aggfunc="size")
    out["baseline_win_pct"] = wt
    out["baseline_n"] = nt
    out["baseline_games"] = b.game_pk.nunique()
    out["baseline_seasons"] = sorted(b.season.unique().tolist())

    # Market: same cells, what did Polymarket price the leader at, and how did they do
    p = panel[panel.checkpoint & (panel.half == "top") & (panel["diff"] != 0) & panel.mkt_p.notna() & panel.mkt_staleness.le(120)].copy()
    p["lead"] = np.minimum(p["diff"].abs(), 6)
    p["leader_is_home"] = p["diff"] > 0
    p["leader_p"] = np.where(p.leader_is_home, p.mkt_p, 1 - p.mkt_p)
    p["leader_pre_p"] = np.where(p.leader_is_home, p.pre_p, 1 - p.pre_p)
    p["leader_won"] = np.where(p.leader_is_home, p.home_won_final, ~p.home_won_final.astype(bool)).astype(float)
    p["inning"] = _inn(p.inning)
    g = p.groupby(["inning", "lead"]).agg(n=("leader_won", "size"), mkt_mean=("leader_p", "mean"),
                                          mkt_std=("leader_p", "std"), mkt_p10=("leader_p", lambda x: x.quantile(.1)),
                                          mkt_p90=("leader_p", lambda x: x.quantile(.9)),
                                          actual_win=("leader_won", "mean"))
    g["edge_actual_minus_mkt"] = g.actual_win - g.mkt_mean
    # Multiple checkpoints within a game are not independent observations.
    uncertainty = []
    for (inning, lead), cell in p.groupby(["inning", "lead"]):
        _, lo, hi = cluster_ci(cell.leader_won-cell.leader_p, cell.game_pk.to_numpy())
        uncertainty.append(dict(inning=inning, lead=lead, residual_ci_lo=lo, residual_ci_hi=hi))
    if uncertainty:
        g = g.join(pd.DataFrame(uncertainty).set_index(["inning", "lead"]))
    base = b.groupby(["inning", "lead"]).leader_won.mean().rename("baseline_win")
    g = g.join(base)
    out["market_cells"] = g.reset_index()
    out["market_rows"] = p
    return out


# ----------------------------------------------------------------------------- H3

def _progress(df: pd.DataFrame) -> np.ndarray:
    half = (df.half == "bottom").astype(float)
    prog = ((df.inning - 1) * 2 + half + df.outs / 3.0) / 18.0
    return np.clip(prog, 0, 1).to_numpy()


def _baseline_we(baseline: pd.DataFrame, rows: pd.DataFrame, max_season: int) -> np.ndarray:
    """Empirical home win expectancy for each row's (inning, half, outs, bases, diff),
    estimated only from seasons <= max_season, with coarser fallbacks for sparse cells."""
    b = baseline[baseline.season <= max_season].copy()
    for df in (b, rows):
        df["inn_k"] = _inn(df.inning)
        df["diff_k"] = np.clip(df["diff"], -7, 7)
    b["y"] = b.home_won_final.astype(float)
    levels = [["inn_k", "half", "outs", "bases", "diff_k"], ["inn_k", "half", "diff_k"], ["diff_k"]]
    est = pd.Series(np.nan, index=rows.index)
    for keys in levels:
        t = b.groupby(keys).y.agg(["sum", "size"])
        t = t[t["size"] >= 30]
        # light shrinkage toward 0.5 for small cells
        rate = (t["sum"] + 5) / (t["size"] + 10)
        m = rows[keys].merge(rate.rename("we"), left_on=keys, right_index=True, how="left")["we"]
        est = est.fillna(pd.Series(m.values, index=rows.index))
    return est.fillna(0.5).to_numpy()


FEATURES = ["lg_we", "lg_pre", "lg_pre_x_left", "left"]


def _features(df: pd.DataFrame) -> pd.DataFrame:
    left = 1 - _progress(df)
    return pd.DataFrame({"lg_we": logit(df.we), "lg_pre": logit(df.pre_p),
                         "lg_pre_x_left": logit(df.pre_p) * left, "left": left}, index=df.index)


def causal_model_rows(panel: pd.DataFrame, baseline: pd.DataFrame, split_date: str) -> pd.DataFrame:
    """Keep known model inputs; training-state rates exclude their own season outcomes.

    The evaluation baseline is frozen at the split year minus one. The training
    rows use earlier seasons individually, so their labels cannot encode themselves.
    """
    df = panel.dropna(subset=["mkt_p", "pre_p", "home_won_final"]).copy()
    df = df[df.pre_p.between(.02, .98) & df.mkt_p.between(0, 1) & df.mkt_staleness.le(120)]
    df["we"] = np.nan
    seasons = df.event_date.str[:4].astype(int)
    for season in sorted(seasons.unique()):
        mask = seasons.eq(season)
        df.loc[mask, "we"] = _baseline_we(baseline, df.loc[mask].copy(),
                                             max_season=min(season-1, int(split_date[:4])-1))
    df["y"] = df.home_won_final.astype(float)
    return df


def h3_fair_value(panel: pd.DataFrame, baseline: pd.DataFrame, split_date: str,
                  slip: float = 0.01) -> dict:
    """Out-of-sample: fit P(home wins | pregame price, state) on games before split_date,
    then trade games on/after it whenever the model and market disagree by > threshold."""
    df = causal_model_rows(panel, baseline, split_date)
    train, test = df[df.event_date < split_date], df[df.event_date >= split_date]
    if len(train) < 1000 or len(test) < 1000:
        return {"error": f"not enough rows (train {len(train)}, test {len(test)})"}

    Xtr = _features(train)
    cols = [c for c in Xtr.columns if Xtr[c].std() > 1e-9]   # e.g. no prior seasons -> constant WE
    model = sm.Logit(train.y.values, sm.add_constant(Xtr[cols])).fit(disp=0)
    test = test.copy()
    test["model_p"] = model.predict(sm.add_constant(_features(test)[cols], has_constant="add"))

    scores = pd.DataFrame([
        {"predictor": "causal pre-decision transaction reference", "log_loss": log_loss(test.y, test.mkt_p), "brier": brier(test.y, test.mkt_p)},
        {"predictor": "model (pregame price + state)", "log_loss": log_loss(test.y, test.model_p), "brier": brier(test.y, test.model_p)},
        {"predictor": "baseline win expectancy only", "log_loss": log_loss(test.y, test.we), "brier": brier(test.y, test.we)},
        {"predictor": "pregame price only", "log_loss": log_loss(test.y, test.pre_p), "brier": brier(test.y, test.pre_p)},
    ])

    # does the model add information beyond the market? (logit stacking on the test set)
    X = sm.add_constant(np.column_stack([logit(test.mkt_p), logit(test.model_p) - logit(test.mkt_p)]))
    # cluster by game: ~90 plate appearances per game are not independent observations
    stack = sm.Logit(test.y.values, X).fit(disp=0, cov_type="cluster",
                                          cov_kwds={"groups": test.game_pk.to_numpy()})

    rows = []
    test["edge_home"] = test.model_p - test.mkt_p
    for thr in (0.02, 0.04, 0.06, 0.08, 0.10):
        sig = test[test.edge_home.abs() > thr].sort_values("state_ts")
        first = sig.groupby("game_pk").head(1)        # one bet per game: avoid counting one mispricing 20x
        buy_home = first.edge_home > 0
        r = panel_entries(first, buy_home, slip=slip)
        g = r[r.cost_usd > 0]
        mean, lo, hi = cluster_ci(g.roi, g.event.to_numpy(), weights=g.cost_usd.to_numpy()) if len(g) else (np.nan,) * 3
        rows.append({"threshold": thr, "fee": "historical actual", "signals": len(r), "bets": len(g),
                     "unfilled": int((r.cost_usd == 0).sum()), "partial": int(r.status.eq("partial").sum()),
                     "win_rate": g.y.mean(), "avg_price_paid": g.entry_price.mean(),
                     "capital_usd": g.cost_usd.sum(), "pnl_usd": g.pnl_usd.sum(),
                     "roi_per_$": mean, "roi_ci95": f"{lo:+.3f} to {hi:+.3f}"})

    # the user's literal idea: "trade to the average" market price for the state
    naive = _naive_state_average(train, test, slip)
    return {"train_rows": len(train), "test_rows": len(test), "train_games": train.game_pk.nunique(),
            "test_games": test.game_pk.nunique(), "scores": scores,
            "coef": model.params.to_frame("coef").join(model.bse.rename("se")),
            "stack_coef_market": float(stack.params[1]), "stack_coef_model_minus_market": float(stack.params[2]),
            "stack_se_model_minus_market": float(stack.bse[2]),
            "backtest": pd.DataFrame(rows), "naive": naive,
            "execution_note": "Optimistic5s from retrospective state clock; receipt latency and actual book unknown. Settlement exits only; no literal mean-reversion exit tested."}


def _naive_state_average(train: pd.DataFrame, test: pd.DataFrame, slip: float) -> pd.DataFrame:
    """Cell = (inning, half, diff, pregame-favorite bucket). Fair = train-period average
    market price in the cell. Bet toward the average when price deviates by k std."""
    def key(df):
        return pd.DataFrame({"inn": _inn(df.inning), "half": df.half, "diff": np.clip(df["diff"], -5, 5),
                             "pre_b": (df.pre_p * 10).round().clip(2, 8)}, index=df.index)
    kt, ks = key(train), key(test)
    cols = list(kt.columns)
    stats = train.assign(**kt).groupby(cols).mkt_p.agg(["mean", "std", "size"])
    stats = stats[stats["size"] >= 30]
    t = test.assign(**ks).merge(stats, left_on=cols, right_index=True, how="inner")
    rows = []
    for k in (1.0, 1.5, 2.0):
        dev = (t.mkt_p - t["mean"]) / t["std"]
        sig = t[dev.abs() > k].sort_values("state_ts").groupby("game_pk").head(1)
        buy_home = sig.mkt_p < sig["mean"]    # price below the state's average -> buy home
        r = panel_entries(sig, buy_home, slip=slip)
        g = r[r.cost_usd > 0]
        mean, lo, hi = cluster_ci(g.roi, g.event.to_numpy(), weights=g.cost_usd.to_numpy()) if len(g) else (np.nan,) * 3
        rows.append({"k_std": k, "fee": "historical actual", "signals": len(r), "bets": len(g),
                     "unfilled": int((r.cost_usd == 0).sum()), "win_rate": g.y.mean(),
                     "avg_price_paid": g.entry_price.mean(), "capital_usd": g.cost_usd.sum(),
                     "pnl_usd": g.pnl_usd.sum(), "roi_per_$": mean,
                     "roi_ci95": f"{lo:+.3f} to {hi:+.3f}"})

    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- H4

LAT_BUCKETS = [(-30, 0), (0, 5), (5, 10), (10, 20), (20, 30), (30, 45), (45, 60)]


TRADE_COLS = ["timestamp", "side", "outcomeIndex", "price", "size", "home_p"]


def h4_latency(plays: pd.DataFrame, trades_dir, games: pd.DataFrame,
               min_move: float = 0.03, ts_lag: float = TRADE_TS_LAG_S) -> dict:
    """Reaction of trade prices to scoring plays, measured from the moment of contact.

    Trades are read one game at a time (only the columns needed) so memory stays
    flat regardless of how many games have been collected.
    """
    home_idx = games.set_index("game_pk").home_outcome_idx.to_dict()
    have = {int(f.stem) for f in trades_dir.glob("*.parquet")} & set(home_idx)

    def load(pk: int) -> pd.DataFrame:
        g = pd.read_parquet(trades_dir / f"{pk}.parquet", columns=TRADE_COLS).sort_values("timestamp")
        g["timestamp"] = g.timestamp - ts_lag      # approximate match time
        # did the taker buy the home side? (BUY home token, or SELL away token)
        g["taker_buys_home"] = (g.outcomeIndex == home_idx[pk]) == (g.side == "BUY")
        g["usd"] = g.price * g["size"]
        return g

    p = plays.sort_values(["game_pk", "play_idx"]).copy()
    p["next_contact"] = p.groupby("game_pk").contact_ts.shift(-1)
    p["prev_end"] = p.groupby("game_pk").end_ts.shift(1)
    ev = p[p.is_scoring & p.game_pk.isin(have)].copy()
    ev = ev[(ev.next_contact - ev.contact_ts > 90) & (ev.contact_ts - ev.prev_end > 20)]

    recs, curve = [], []
    grid = np.arange(-30, 91, 1)
    cur_pk, g = None, None
    for e in ev.sort_values("game_pk").itertuples(index=False):
        if e.game_pk != cur_pk:
            cur_pk, g = e.game_pk, load(e.game_pk)
        ts, hp = g.timestamp.to_numpy(float), g.home_p.to_numpy(float)
        t0 = e.contact_ts
        sign = 1.0 if e.half == "bottom" else -1.0   # bottom half = home batting
        pre_m = (ts >= t0 - 90) & (ts < t0 - 2)
        post_m = (ts >= t0 + 60) & (ts < t0 + 90)
        if pre_m.sum() < 2 or post_m.sum() < 2:
            continue
        pre, post = np.median(hp[pre_m]), np.median(hp[post_m])
        move = sign * (post - pre)
        if move < min_move:
            continue
        frac = sign * (hp - pre) / move            # 0 = old price, 1 = new price
        after = ts >= t0 - 2
        def first_cross(level):
            idx = np.where(after & (frac >= level))[0]
            return ts[idx[0]] - t0 if len(idx) else np.nan
        # stale fills: taker bought the scoring side at (near) the pre-play price
        scoring_buy = g.taker_buys_home.to_numpy() == (sign > 0)
        stale = scoring_buy & (frac < 0.25)
        usd = g.usd.to_numpy(float)
        rec = {"game_pk": e.game_pk, "event": e.event_type, "inning": e.inning, "rbi": e.rbi,
               "pre": pre, "post": post, "move": move, "t50": first_cross(0.5), "t90": first_cross(0.9)}
        for a, b in LAT_BUCKETS:
            m = (ts >= t0 + a) & (ts < t0 + b)
            rec[f"stale_fills_{a}_{b}"] = int((m & stale).sum())
            rec[f"stale_usd_{a}_{b}"] = float(usd[m & stale].sum())
            rec[f"fills_{a}_{b}"] = int(m.sum())
        recs.append(rec)
        # step-interpolated normalized path on a 1s grid
        idx = np.searchsorted(ts, t0 + grid, side="right") - 1
        path = np.where(idx >= 0, frac[np.clip(idx, 0, None)], np.nan)
        curve.append(path)
    r = pd.DataFrame(recs)
    if r.empty:
        return {"events": r}
    cv = np.vstack(curve)
    curve_df = pd.DataFrame({"sec": grid, "median_frac": np.nanmedian(cv, 0),
                             "p25": np.nanpercentile(cv, 25, 0), "p75": np.nanpercentile(cv, 75, 0)})
    summary = []
    for name, sub in [("all scoring plays", r), ("home runs", r[r.event == "home_run"])]:
        row = {"events": name, "n": len(sub), "median_move": sub.move.median(),
               "t50_median_s": sub.t50.median(), "t50_p90_s": sub.t50.quantile(.9),
               "t90_median_s": sub.t90.median()}
        for a, b in LAT_BUCKETS[1:]:
            row[f"events_w_stale_fill_{a}-{b}s"] = float((sub[f"stale_fills_{a}_{b}"] > 0).mean())
            row[f"median_stale_usd_{a}-{b}s"] = float(sub[f"stale_usd_{a}_{b}"].median())
        summary.append(row)
    return {"events": r, "curve": curve_df, "summary": pd.DataFrame(summary)}
