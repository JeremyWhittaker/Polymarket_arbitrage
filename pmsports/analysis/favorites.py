"""Bet the pregame favorite in every game and hold to the final whistle: what do you make?

python -m pmsports favorites  ->  reports/FAVORITES.md (+ CSV, chart)

Pregame price = median of actual fills in the 10 minutes before the scheduled start
(fallback: last 60 minutes), from the wallet tapes of every sports moneyline market with
>= $50k volume. Two-way markets (team A vs team B): the favorite is the side priced > 50c.
Soccer "Will X win?" Yes/No markets (3-way with a draw): the favorite is the team whose
"win" market is priced highest; the bet is Yes on it (a draw loses).

Return per $1 staked = (payout - price - fee) / (price + fee), fee = taker fee at the
market's own rate (0 in 2025, 3-5% in 2026) - or a fixed 5% "current" scenario.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from ..polymarket import taker_fee
from ..wallets.tapes import TAPES
from ..wallets.universe import OUT

log = logging.getLogger("pmsports")
REPORTS = Path(__file__).resolve().parents[2] / "reports"
PREGAME_MIN_USD = 25_000


def _pregame_p0(path: Path, start: float, tokens=None) -> tuple[float, float, float, int, float]:
    """Pregame mid of outcome 0, plus the executable asks for outcome 0 and outcome 1.

    Mid = median implied price over all fills. Ask for outcome k = median price paid by
    takers who *acquired* k (BUY k, or SELL the other side at p -> paid 1-p). Using the
    mid alone overstates the cheap side's value when takers mostly buy the favorite.
    """
    tb = pq.read_table(path, columns=["timestamp", "side", "outcomeIndex", "price", "size", "asset"]).to_pandas()
    if tb.empty:
        return np.nan, np.nan, np.nan, 0, 0.0
    usd = (tb.price * tb["size"]).to_numpy()
    pre_usd = float(usd[(tb.timestamp < start).to_numpy()].sum())
    buy = (tb.side == "BUY").to_numpy()
    oi = tb.outcomeIndex.to_numpy()
    if tokens is not None:                      # token id is authoritative; outcomeIndex glitches
        a = tb.asset.to_numpy()
        oi = np.where(a == tokens[0], 0, np.where(a == tokens[1], 1, oi))
    px = tb.price.to_numpy()
    p0 = np.where(oi == 0, px, 1 - px)
    acq = np.where(buy, oi, 1 - oi)          # which outcome the taker ended up long
    paid = np.where(buy, px, 1 - px)         # price paid for it
    for win in (600, 3600):
        m = ((tb.timestamp >= start - win) & (tb.timestamp < start)).to_numpy()
        if m.sum() >= 3:
            a0 = paid[m & (acq == 0)]
            a1 = paid[m & (acq == 1)]
            return (float(np.median(p0[m])), float(np.median(a0)) if len(a0) else np.nan,
                    float(np.median(a1)) if len(a1) else np.nan, int(m.sum()), pre_usd)
    return np.nan, np.nan, np.nan, 0, pre_usd


def pregame_prices(workers: int = 16) -> pd.DataFrame:
    u = pd.read_parquet(OUT / "universe.parquet")
    m = u[u.market_type == "moneyline"].drop_duplicates("condition_id")
    have = {p.stem for p in TAPES.glob("*.parquet")}
    m = m[m.condition_id.isin(have) & m.game_start_ts.notna()]
    log.info("pregame prices for %d markets", len(m))
    toks = u.pivot_table(index="condition_id", columns="outcome_idx", values="token_id", aggfunc="first")
    tok_of = {c: (a, b) for c, a, b in zip(toks.index, toks[0], toks[1])}
    with ThreadPoolExecutor(workers) as ex:
        res = list(ex.map(lambda r: _pregame_p0(TAPES / f"{r.condition_id}.parquet", r.game_start_ts,
                                                tok_of.get(r.condition_id)),
                          m.itertuples(index=False)))
    m = m.assign(p0=[r[0] for r in res], ask0=[r[1] for r in res], ask1=[r[2] for r in res],
                 n_fills=[r[3] for r in res], pre_usd=[r[4] for r in res])
    pay = u.pivot_table(index="condition_id", columns="outcome_idx", values="payout", aggfunc="first")
    outs = u.pivot_table(index="condition_id", columns="outcome_idx", values="outcome", aggfunc="first")
    m = m.join(pay.rename(columns={0: "y0", 1: "y1"})[["y0", "y1"]], on="condition_id")
    m = m.join(outs.rename(columns={0: "o0", 1: "o1"})[["o0", "o1"]], on="condition_id")
    return m.dropna(subset=["p0"])


def favorite_bets(m: pd.DataFrame) -> pd.DataFrame:
    """One bet per game on the pregame favorite."""
    yes_no = m.o0.str.lower().eq("yes") & m.o1.str.lower().eq("no")
    two = m[~yes_no].copy()
    two["fav_p"] = np.maximum(two.p0, 1 - two.p0)
    two["fav_won"] = np.where(two.p0 >= 0.5, two.y0, two.y1)
    two["fav_ask"] = np.where(two.p0 >= 0.5, two.ask0, two.ask1)
    two["dog_ask"] = np.where(two.p0 >= 0.5, two.ask1, two.ask0)
    two["kind"] = "two-way"
    # 3-way soccer: per event, the team-win market with the highest Yes price (skip draw markets)
    yn = m[yes_no & ~m.market_slug.str.contains("draw", case=False, na=False)].copy()
    yn = yn.sort_values("p0", ascending=False).drop_duplicates("event_slug")
    yn["fav_p"], yn["fav_won"], yn["kind"] = yn.p0, yn.y0, "3-way (draw loses)"
    yn["fav_ask"], yn["dog_ask"] = yn.ask0, np.nan
    bets = pd.concat([two, yn], ignore_index=True)
    bets = bets[bets.fav_p.between(0.02, 0.99) & bets.fav_won.notna()]
    bets["date"] = pd.to_datetime(bets.game_start_ts, unit="s", utc=True)
    bets["season"] = bets.date.dt.year
    return bets.sort_values("date")


def returns(b: pd.DataFrame, price_col: str, won_col: str, fee: str | float, slip: float) -> np.ndarray:
    c = np.clip(b[price_col].to_numpy() + slip, 0.001, 0.999)
    rate = b.fee_rate.fillna(0).to_numpy() if fee == "actual" else np.full(len(b), float(fee))
    f = taker_fee(1.0, c, rate)
    return (b[won_col].to_numpy() - c - f) / (c + f)


def _ci(x: np.ndarray, n_boot: int = 2000, seed: int = 1) -> str:
    rng = np.random.default_rng(seed)
    means = x[rng.integers(0, len(x), (n_boot, len(x)))].mean(1)
    return f"{np.percentile(means, 2.5):+.3f}..{np.percentile(means, 97.5):+.3f}"


def summarize(b: pd.DataFrame, by: str | None) -> pd.DataFrame:
    groups = [("ALL", b)] if by is None else list(b.groupby(by, observed=True))
    rows = []
    for key, g in groups:
        if len(g) < 30:
            continue
        r0 = returns(g, "fav_p", "fav_won", 0.0, 0.0)
        ra = returns(g, "fav_p", "fav_won", "actual", 0.01)
        r5 = returns(g, "fav_p", "fav_won", 0.05, 0.01)
        fa = g[g.fav_ask.notna()]
        rfa = returns(fa, "fav_ask", "fav_won", "actual", 0.0) if len(fa) else np.array([np.nan])
        dog = g.assign(dog_won=np.where(g.kind == "two-way", 1 - g.fav_won, np.nan))
        dog = dog[dog.dog_won.notna() & dog.dog_ask.notna()]
        rd = returns(dog, "dog_ask", "dog_won", "actual", 0.0) if len(dog) else np.array([np.nan])
        rows.append({by or "scope": key, "games": len(g), "avg_fav_price": g.fav_p.mean(),
                     "fav_win_rate": g.fav_won.mean(), "gap_win_minus_price": g.fav_won.mean() - g.fav_p.mean(),
                     "roi_no_fee_no_slip": r0.mean(), "roi_no_fee_ci": _ci(r0),
                     "roi_actual_fee_1c": ra.mean(), "roi_5pct_fee_1c": r5.mean(), "roi_5pct_ci": _ci(r5),
                     "pnl_$100_per_game_actual_fee": 100 * ra.sum(),
                     "fav_roi_at_ask_actual_fee": np.nanmean(rfa),
                     "fav_at_ask_ci": _ci(rfa) if len(fa) >= 30 else "",
                     "underdog_roi_at_ask_actual_fee": np.nanmean(rd),
                     "underdog_at_ask_ci": _ci(rd) if len(dog) >= 30 else ""})
    return pd.DataFrame(rows)


def chart(b: pd.DataFrame, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    INK, INK2, GRID, SURF = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
    fig, ax = plt.subplots(figsize=(7.2, 4.2), facecolor=SURF)
    for label, col, fee, slip in (("no fee, no slippage", "#2a78d6", 0.0, 0.0),
                                  ("actual fee + 1c slippage", "#eb6834", "actual", 0.01)):
        r = returns(b, "fav_p", "fav_won", fee, slip)
        ax.plot(b.date, np.cumsum(100 * r), color=col, lw=2, label=label)
    ax.axhline(0, color=INK2, lw=1)
    ax.set_facecolor(SURF)
    ax.set_title(f"\\$100 on every pregame favorite, all sports ({len(b):,} games)",
                 color=INK, fontsize=11, loc="left")
    ax.set_ylabel("Cumulative P&L (\\$)", color=INK2, fontsize=9)
    ax.tick_params(colors=INK2, labelsize=8)
    ax.grid(color=GRID, lw=0.6)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.legend(frameon=False, fontsize=8, labelcolor=INK2)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def run() -> None:
    REPORTS.mkdir(exist_ok=True)
    cache = OUT / "pregame_prices.parquet"
    m = pd.read_parquet(cache) if cache.exists() else pregame_prices()
    m.to_parquet(cache, index=False)
    if "pre_usd" not in m.columns:            # older cache
        m = pregame_prices()
        m.to_parquet(cache, index=False)
    full = favorite_bets(m)
    # Headline sample is selected on PREGAME volume only. The tapes cover markets with >= $50k
    # *total* volume, which includes in-play trading; upsets draw more in-play volume, so thin
    # markets clear the cut more often when the underdog wins (look-ahead selection). Games with
    # >= $25k traded before the start clear the cut regardless of outcome.
    b = full[full.pre_usd >= PREGAME_MIN_USD].copy()
    b["bucket"] = pd.cut(b.fav_p, [0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.9, 1.0], include_lowest=True).astype(str)
    b.loc[b.kind != "two-way", "bucket"] = "3-way: " + b.loc[b.kind != "two-way", "bucket"]
    tabs = {"overall": summarize(b, None), "biased_full_sample": summarize(full, None),
            "by_sport": summarize(b, "family"),
            "by_season": summarize(b, "season"), "by_price": summarize(b[b.kind == "two-way"], "bucket"),
            "by_kind": summarize(b, "kind")}
    for k, v in tabs.items():
        v.to_csv(REPORTS / f"favorites_{k}.csv", index=False)
    chart(b, REPORTS / "favorites_cumulative.png")
    (REPORTS / "FAVORITES.md").write_text(_render(b, tabs))
    log.info("wrote %s", REPORTS / "FAVORITES.md")


def _md(df):
    return df.to_markdown(index=False, floatfmt=".3f")


def _render(b, t) -> str:
    o = t["overall"].iloc[0]
    L = ["# Bet every pregame favorite and hold to the end (all sports)", "",
         f"{len(b):,} games, {b.date.min():%Y-%m-%d} to {b.date.max():%Y-%m-%d}: Polymarket sports moneylines "
         f"with >= ${PREGAME_MIN_USD:,} of taker volume *before* the start (selection uses no post-start "
         "information). Pregame price = median fill in the last 10 minutes before the start. "
         "ROI is per $1 staked; `actual fee` = the market's own taker fee (0 in 2025, 3-5% in 2026) plus 1c "
         "slippage; `5pct` = today's 5% sports fee on every game. `*_at_ask` columns price each bet at the "
         "median price takers actually *paid* for that side in the last 10 minutes (executable), with the "
         "actual fee and no extra slippage; the underdog columns are the mirror bet (two-way markets).", "",
         f"**Headline:** favorites won **{o.fav_win_rate:.1%}** of games at an average price of "
         f"**{o.avg_fav_price:.1%}**. Holding every favorite to the end returned **{o.roi_no_fee_no_slip:+.2%}** "
         f"per $1 before costs and **{o.roi_actual_fee_1c:+.2%}** after the fees actually charged plus 1c "
         f"(**{o.roi_5pct_fee_1c:+.2%}** at today's 5% fee). $100 on every game = "
         f"**${o['pnl_$100_per_game_actual_fee']:,.0f}** total.", "",
         "![cumulative](favorites_cumulative.png)", "", "## Overall", "", _md(t["overall"]), "",
         "### Why not all 32k games? A selection trap", "",
         "Selecting on *total* volume (>= $50k, which includes in-play trading) makes underdogs look "
         "profitable, because upsets draw more in-play volume and thin markets clear the cut more often "
         "when the underdog wins. The underdog edge on that sample grows with total volume (+2% at "
         "$100-250k up to +22% at $5M+) and vanishes (-2% to -3%) once games are selected on pregame "
         "volume only. Biased full sample, for reference:", "", _md(t["biased_full_sample"]), "",
         "## By sport", "", _md(t["by_sport"]), "", "## By season", "", _md(t["by_season"]), "",
         "## By how big a favorite (two-way markets)", "", _md(t["by_price"]), "",
         "## Two-way vs three-way (soccer, draw loses)", "", _md(t["by_kind"]), ""]
    return "\n".join(L)
