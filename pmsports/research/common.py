"""Shared toolkit for hypothesis backtests. Read RESEARCH_GUIDE.md first.

Fast data access (build once with `python -m pmsports.research.common build`):
  fills(markets=None, columns=None)  every wallet-attributed taker fill in 38k sports moneyline
                                     markets (>= $50k volume), 2025-01 .. 2026-09, normalized to
                                     "taker acquired side `s` at price `q`, side paid `y`".
  markets()                          one row per market (code `m`): condition_id, family, league,
                                     event_slug, game_start_ts, closed_ts, fee_rate, volume,
                                     outcomes o0/o1, payouts y0/y1, pregame mid/asks, pre_usd.
  wallet_ids()                       wallet code `w` -> address.
  universe()                         every resolved sports game market (1.9M; all market types).
Execution / statistics helpers:
  taker_fee, taker_roi, maker_roi, cluster_ci, split_oos.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
RESEARCH = DATA / "research"
FILLS = RESEARCH / "fills.parquet"
MARKETS = RESEARCH / "markets.parquet"
WALLETS = RESEARCH / "wallets.parquet"
SPLIT = "2026-01-01"                 # default out-of-sample boundary (train 2025, test 2026)
MAKER_REBATE = 0.15                   # sports: 15% of taker fees rebated to makers (approx per fill)


# ----------------------------------------------------------------------------- data access

def fills(markets=None, columns=None) -> pd.DataFrame:
    """Taker fills. Columns: m (market code), w (wallet code), ts (unix s, on-chain settlement
    time ~2.6 s after the match), size (shares), s (side acquired, 0/1 = outcome index), q (price
    paid for side s), y (payout of side s: 1/0, 0.5 void), fee_rate, in_play (ts >= scheduled start).
    `markets`: iterable of market codes to load (row groups are sorted by m -> fast)."""
    import pyarrow.dataset as ds
    d = ds.dataset(FILLS, format="parquet")
    filt = None if markets is None else ds.field("m").isin(np.asarray(list(markets), dtype=np.int32))
    return d.to_table(columns=columns, filter=filt).to_pandas()


def markets() -> pd.DataFrame:
    return pd.read_parquet(MARKETS)


def wallet_ids() -> pd.Series:
    return pd.read_parquet(WALLETS)["wallet"]


def universe(columns=None) -> pd.DataFrame:
    return pd.read_parquet(DATA / "wallets" / "universe.parquet", columns=columns)


# ----------------------------------------------------------------------------- execution

def taker_fee(shares, price, rate):
    """Polymarket taker fee in USDC: shares * rate * p * (1-p). Sports rate: 0 (2025), 0.03 (Mar-Jun
    2026), 0.05 (Jul 2026-); use each market's `fee_rate`. Makers pay nothing."""
    p = np.asarray(price, dtype=float)
    return np.asarray(shares, dtype=float) * np.nan_to_num(np.asarray(rate, dtype=float)) * p * (1 - p)


def taker_roi(price, won, fee_rate, slip=0.0):
    """Per-$1 return of BUYING a side at `price` (+slip) as a taker and holding to resolution."""
    c = np.clip(np.asarray(price, float) + slip, 0.001, 0.999)
    f = taker_fee(1.0, c, fee_rate)
    return (np.asarray(won, float) - c - f) / (c + f)


def maker_roi(price, won, fee_rate, rebate=MAKER_REBATE):
    """Per-$1 return of a resting order that BUYS a side at `price` and holds to resolution:
    no fee, plus a rebate share of the taker's fee on that fill (approximation)."""
    c = np.clip(np.asarray(price, float), 0.001, 0.999)
    reb = rebate * taker_fee(1.0, c, fee_rate)
    return (np.asarray(won, float) - c + reb) / c


# ----------------------------------------------------------------------------- statistics

def cluster_ci(values, clusters, weights=None, n_boot=2000, seed=0) -> tuple[float, float, float]:
    """Weighted mean and 95% bootstrap CI resampling whole clusters (e.g. event_slug / game):
    bets in the same game are correlated; per-bet CIs are too narrow."""
    v = np.asarray(values, float)
    w = np.ones_like(v) if weights is None else np.asarray(weights, float)
    df = pd.DataFrame({"c": np.asarray(clusters), "vw": v * w, "w": w}).groupby("c")[["vw", "w"]].sum()
    if len(df) < 5:
        return float((v * w).sum() / w.sum()), np.nan, np.nan
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(df), (n_boot, len(df)))
    vw, ww = df.vw.to_numpy(), df.w.to_numpy()
    boots = vw[idx].sum(1) / ww[idx].sum(1)
    return float(vw.sum() / ww.sum()), float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))


def split_oos(df: pd.DataFrame, ts_col: str = "ts", split: str = SPLIT):
    t = pd.Timestamp(split, tz="UTC").timestamp()
    return df[df[ts_col] < t], df[df[ts_col] >= t]


# ----------------------------------------------------------------------------- build

def build() -> None:
    from ..wallets.tapes import load_trades
    RESEARCH.mkdir(parents=True, exist_ok=True)
    u = universe()
    t = load_trades(u)
    mcat, wcat = t.condition_id.cat.categories, t.proxyWallet.cat.categories
    f = pd.DataFrame({"m": t.condition_id.cat.codes.to_numpy().astype(np.int32),
                      "w": t.proxyWallet.cat.codes.to_numpy().astype(np.int32),
                      "ts": t.timestamp.to_numpy().astype(np.int64),
                      "size": t["size"].to_numpy(np.float32), "s": t.side_idx.to_numpy(np.int8),
                      "q": t.q.to_numpy(np.float32), "y": t.y.to_numpy(np.float32),
                      "fee_rate": t.fee_rate.to_numpy(np.float32), "in_play": t.in_play.to_numpy()})
    del t
    f = f.sort_values(["m", "ts"], kind="stable")
    f.to_parquet(FILLS, index=False, row_group_size=500_000)
    pd.DataFrame({"wallet": np.asarray(wcat, dtype=object)}).to_parquet(WALLETS)
    build_markets(u, list(mcat))


def _market_codes() -> list[str]:
    """Market code order used by fills.m (= load_trades: sorted non-empty tape files)."""
    import pyarrow.parquet as pq
    from ..wallets.tapes import TAPES
    return [f.stem for f in sorted(TAPES.glob("*.parquet")) if pq.ParquetFile(f).metadata.num_rows > 0]


def build_markets(u: pd.DataFrame | None = None, mcat: list[str] | None = None) -> None:
    u = universe() if u is None else u
    mcat = _market_codes() if mcat is None else mcat
    mcat = pd.Index(mcat, name="condition_id")
    meta = u.drop_duplicates("condition_id").set_index("condition_id").reindex(mcat)
    pay = u.pivot_table(index="condition_id", columns="outcome_idx", values="payout", aggfunc="first").reindex(mcat)
    outs = u.pivot_table(index="condition_id", columns="outcome_idx", values="outcome", aggfunc="first").reindex(mcat)
    mk = meta[["family", "league", "market_type", "event_slug", "market_slug", "game_start_ts", "closed_ts",
               "fee_rate", "volume", "neg_risk"]].reset_index()
    mk.insert(0, "m", np.arange(len(mk), dtype=np.int32))
    mk["o0"], mk["o1"] = outs[0].to_numpy(), outs[1].to_numpy()
    mk["y0"], mk["y1"] = pay[0].to_numpy(), pay[1].to_numpy()
    pg = pd.read_parquet(DATA / "wallets" / "pregame_prices.parquet",
                         columns=["condition_id", "p0", "ask0", "ask1", "n_fills", "pre_usd"])
    mk = mk.merge(pg.rename(columns={"p0": "pre_mid0", "ask0": "pre_ask0", "ask1": "pre_ask1",
                                     "n_fills": "pre_n_fills"}), on="condition_id", how="left")
    mk.to_parquet(MARKETS, index=False)
    print(f"markets {len(mk):,}")


if __name__ == "__main__":
    if sys.argv[1:] == ["build"]:
        build()
    elif sys.argv[1:] == ["build-markets"]:
        build_markets()
