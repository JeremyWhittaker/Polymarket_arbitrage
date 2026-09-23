"""Pregame favorite audit with a fixed decision 10 minutes before scheduled start.
Reference prices and liquidity use strictly earlier fills. Entry uses the first
same-side taker print more than five seconds later, capped by its observed size.
This is a partial-fill tape proxy, not a historical quote or guaranteed fill.
Legacy data remains a bounded, eventual-volume-selected subset until backfilled.
"""
from __future__ import annotations

import logging
import hashlib
import json
import os
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
DECISION_LEAD = 600
ENTRY_DELAY = 5
TARGET_USD = 100.0


def _pregame_p0(path: Path, start: float, tokens=None) -> tuple[float, float, float, int, float]:
    r = _pregame_quote(path, start, tokens)
    return tuple(r[k] for k in ("p0", "ask0", "ask1", "n_fills", "pre_usd"))


def _pregame_quote(path: Path, start: float, tokens=None) -> dict:
    tb = pq.read_table(path, columns=["timestamp", "side", "outcomeIndex", "price", "size", "asset"]).to_pandas()
    tb = tb.sort_values("timestamp", kind="stable")
    tb = tb[tb.price.between(0.001, 0.999) & tb["size"].gt(0) & tb.side.isin(["BUY", "SELL"])]
    dec_time = start - DECISION_LEAD
    usd = (tb.price * tb["size"]).to_numpy()
    pre_usd = float(usd[(tb.timestamp < dec_time).to_numpy()].sum())
    buy = (tb.side == "BUY").to_numpy()
    oi = tb.outcomeIndex.to_numpy()
    if tokens is not None:
        a = tb.asset.to_numpy()
        oi = np.where(a == tokens[0], 0, np.where(a == tokens[1], 1, -1))
    px = tb.price.to_numpy()
    p0 = np.where(oi == 0, px, 1 - px)
    acq = np.where(buy, oi, 1 - oi)
    paid = np.where(buy, px, 1 - px)

    valid = np.isin(oi, [0, 1])
    m_sig = ((tb.timestamp >= dec_time - 3600) & (tb.timestamp < dec_time)).to_numpy() & valid
    m_ent = ((tb.timestamp > dec_time + ENTRY_DELAY) & (tb.timestamp < start)).to_numpy() & valid
    signal_p0 = float(np.median(p0[m_sig])) if m_sig.sum() >= 3 else np.nan
    r = {"p0": signal_p0, "n_fills": int(m_sig.sum()), "pre_usd": pre_usd,
         "decision_ts": dec_time, "entry_delay_s": ENTRY_DELAY, "execution": "first_later_print_partial_proxy"}
    for s in (0, 1):
        ix = np.flatnonzero(m_ent & (acq == s))
        i = ix[0] if len(ix) else None
        r[f"ask{s}"] = float(paid[i]) if i is not None else np.nan
        r[f"entry_ts{s}"] = float(tb.timestamp.iloc[i]) if i is not None else np.nan
        r[f"entry_size{s}"] = float(tb["size"].iloc[i]) if i is not None else 0.0
    return r


def pregame_manifest():
    files = [OUT / "universe.parquet"] + sorted(TAPES.glob("*.parquet"))
    return {"version": 2, "decision_lead": DECISION_LEAD, "delay": ENTRY_DELAY,
            "code_hash": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "inputs": {str(p): [p.stat().st_size, p.stat().st_mtime_ns] for p in files if p.exists()}}


def cached_pregame_prices(workers=16):
    from ..collect import _write
    path, mp = OUT / "pregame_prices.parquet", OUT / "pregame_prices.manifest.json"
    wanted = pregame_manifest()
    try:
        old = json.loads(mp.read_text())
        st = path.stat()
        if old == {"request": wanted, "output": [st.st_size, st.st_mtime_ns]}:
            return pd.read_parquet(path)
    except (OSError, ValueError):
        pass
    result = pregame_prices(workers)
    if wanted != pregame_manifest():
        raise RuntimeError("pregame inputs changed during computation; retry after collection finishes")
    _write(result, path)
    st = path.stat()
    tmp = mp.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"request": wanted, "output": [st.st_size, st.st_mtime_ns]}, sort_keys=True))
    tmp.replace(mp)
    return result


def pregame_prices(workers: int = 16) -> pd.DataFrame:
    u = pd.read_parquet(OUT / "universe.parquet")
    m = u[u.market_type == "moneyline"].drop_duplicates("condition_id")
    have = {p.stem for p in TAPES.glob("*.parquet")}
    m = m[m.condition_id.isin(have) & m.game_start_ts.notna()]
    log.info("pregame prices for %d markets", len(m))
    toks = u.pivot_table(index="condition_id", columns="outcome_idx", values="token_id", aggfunc="first")
    tok_of = {c: (a, b) for c, a, b in zip(toks.index, toks[0], toks[1])}
    with ThreadPoolExecutor(workers) as ex:
        res = list(ex.map(lambda r: _pregame_quote(TAPES / f"{r.condition_id}.parquet", r.game_start_ts,
                                                tok_of.get(r.condition_id)),
                          m.itertuples(index=False)))
    m = pd.concat([m.reset_index(drop=True), pd.DataFrame(res)], axis=1)
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
    for col in ("entry_size", "entry_ts"):
        if f"{col}0" in two:
            two[f"fav_{col}"] = np.where(two.p0 >= .5, two[f"{col}0"], two[f"{col}1"])
            two[f"dog_{col}"] = np.where(two.p0 >= .5, two[f"{col}1"], two[f"{col}0"])
    two["kind"] = "two-way"
    # 3-way soccer: per event, the team-win market with the highest Yes price (skip draw markets)
    yn = m[yes_no & ~m.market_slug.str.contains("draw", case=False, na=False)].copy()
    yn = yn.sort_values("p0", ascending=False).drop_duplicates("event_slug")
    yn["fav_p"], yn["fav_won"], yn["kind"] = yn.p0, yn.y0, "3-way (draw loses)"
    yn["fav_ask"], yn["dog_ask"] = yn.ask0, np.nan
    for col in ("entry_size", "entry_ts"):
        if f"{col}0" in yn:
            yn[f"fav_{col}"], yn[f"dog_{col}"] = yn[f"{col}0"], 0.0
    bets = pd.concat([two, yn], ignore_index=True)
    bets = bets[bets.fav_p.between(0.02, 0.99) & bets.fav_won.notna()]
    bets["date"] = pd.to_datetime(bets.game_start_ts, unit="s", utc=True)
    bets["season"] = bets.date.dt.year
    # Gamma closure is an analytical cutoff, not a measured resolution receipt.
    # A canceled market can close before its advertised start. Keep its signal
    # but never allocate a later print or invent a post-entry settlement clock.
    bets["entry_expiry_ts"] = np.minimum(bets.game_start_ts, bets.closed_ts)
    for side in ("fav", "dog"):
        unavailable = (bets.entry_expiry_ts.isna()
                       | bets[f"{side}_entry_ts"].ge(bets.entry_expiry_ts))
        bets.loc[unavailable, [f"{side}_ask", f"{side}_entry_ts"]] = np.nan
        bets.loc[unavailable, f"{side}_entry_size"] = 0.
        price = bets[f"{side}_ask"]
        size = bets.get(f"{side}_entry_size", pd.Series(0.0, index=bets.index))
        unit_cost = price + taker_fee(1.0, price, bets.fee_rate.fillna(0))
        bets[f"{side}_stake"] = np.minimum(TARGET_USD, size * unit_cost).fillna(0)
    return bets.sort_values("date").drop_duplicates("event_slug")


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
        fa = g[g.fav_ask.notna() & g.fav_stake.gt(0)]
        rfa = returns(fa, "fav_ask", "fav_won", "actual", 0.0) if len(fa) else np.array([np.nan])
        dog_all = g.assign(dog_won=np.where(g.kind == "two-way", 1 - g.fav_won, np.nan))
        dog_all = dog_all[dog_all.dog_won.notna()]
        dog = dog_all[dog_all.dog_ask.notna() & dog_all.dog_stake.gt(0)]
        rd = returns(dog, "dog_ask", "dog_won", "actual", 0.0) if len(dog) else np.array([np.nan])
        rows.append({by or "scope": key, "games": len(g), "avg_fav_price": g.fav_p.mean(),
                     "fav_win_rate": g.fav_won.mean(), "gap_win_minus_price": g.fav_won.mean() - g.fav_p.mean(),
                     "roi_no_fee_no_slip": r0.mean(), "roi_no_fee_ci": _ci(r0),
                     "roi_actual_fee_1c": ra.mean(), "roi_5pct_fee_1c": r5.mean(), "roi_5pct_ci": _ci(r5),
                     "pnl_$100_per_game_actual_fee": 100 * ra.sum(),
                     "fav_roi_at_ask_actual_fee": np.nanmean(rfa),
                     "fav_unfilled": len(g) - len(fa),
                     "fav_proxy_stake_usd": fa.fav_stake.sum(),
                     "fav_full_100_fills": int(fa.fav_stake.ge(TARGET_USD - 1e-6).sum()),
                     "fav_partial_proxy_roi": float(np.average(rfa, weights=fa.fav_stake)) if len(fa) else np.nan,
                     "fav_at_ask_ci": _ci(rfa) if len(fa) >= 30 else "",
                     "underdog_roi_at_ask_actual_fee": np.nanmean(rd),
                     "dog_unfilled": len(dog_all) - len(dog),
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
    m = cached_pregame_prices()
    full = favorite_bets(m)
    # This applies causal liquidity within the collected subset; it does not repair
    # the old eventual-$50k collection floor or missing early tape history.
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
    lines = ["# Pregame favorite audit", "",
             f"{len(b):,} eligible games within the collected subset. Decision: {DECISION_LEAD}s before "
             f"scheduled start; liquidity >= ${PREGAME_MIN_USD:,} observed strictly before that decision. "
             "Signal is the preceding hour's median normalized price (at least three prints).", "",
             f"Entry: first same-side print strictly more than {ENTRY_DELAY}s after decision and before both start and recorded closure. "
             f"Available size caps each ${TARGET_USD:.0f} target, including fees. `fav_partial_proxy_roi` "
             "weights returns by those capped stakes; unfilled signals remain in the counts. Tape prints "
             "do not establish available order-book depth or a fill available to a follower. `ask` is a "
             "legacy column name for this later-print proxy, not a historical quote.", "",
             "Gamma closedTime is an analytical expiry/terminal-clock proxy, not measured public resolution receipt. "
             "Missing closure produces no fill. Late blockchain prints may settle earlier orders; they do not "
             "establish a new entry opportunity after the declared cutoff.", "",
             "The original corpus used an eventual $50k volume floor and bounded tape windows. A causal "
             "$25k decision-time filter does not restore missing markets or history. Results are exploratory; "
             "2026 has already been inspected. Signal-price and fixed-slippage columns are hypothetical "
             "comparisons, not executable returns. The 5% scenario is a sensitivity, not a fee guarantee.", ""]
    for name, table in t.items():
        lines += [f"## {name.replace('_', ' ')}", "", _md(table) if len(table) else "No eligible sample.", ""]
    return "\n".join(lines)
