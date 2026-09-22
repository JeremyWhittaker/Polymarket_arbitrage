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
import hashlib
import json
import os
import tempfile
import time
import warnings
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
_checked = None


def source_identity():
    from ..wallets.tapes import TAPES
    files = [DATA / "wallets" / "universe.parquet", DATA / "wallets" / "pregame_prices.parquet"]
    files += sorted(TAPES.glob("*.parquet"))
    deps = [Path(__file__), ROOT / "pmsports/wallets/tapes.py", ROOT / "pmsports/wallets/universe.py",
            ROOT / "pmsports/polymarket.py", ROOT / "pmsports/analysis/favorites.py"]
    return {"version": 2, "code": hashlib.sha256(b"".join(p.read_bytes() for p in deps)).hexdigest(),
            "inputs": {str(p): [p.stat().st_size, p.stat().st_mtime_ns] for p in files if p.exists()}}


def validate_cache(force=False):
    """Legacy data is audit-only; versioned mixed/stale generations fail closed.

    Writers and research readers must run sequentially. Input validation is reused
    for 30 seconds within a process to avoid rescanning 38k tapes for each query.
    """
    global _checked
    if (RESEARCH / "BUILDING").exists():
        raise RuntimeError("research cache build incomplete/in progress; do not read mixed generations")
    mp = RESEARCH / "manifest.json"
    stamp = mp.stat().st_mtime_ns if mp.exists() else None
    key = (str(RESEARCH), stamp)
    if not force and _checked and _checked[0] == key and time.monotonic() - _checked[1] < 30:
        return _checked[2]
    if not mp.exists():
        info = {"status": "legacy_unversioned", "coverage": "bounded eventual-volume-selected subset"}
        warnings.warn("Reading legacy research cache for audit: freshness and full coverage unproved", RuntimeWarning)
    else:
        info = json.loads(mp.read_text())
        if info.get("source") != source_identity():
            raise RuntimeError("stale research cache: run python -m pmsports.research.common build")
        for p in (FILLS, MARKETS, WALLETS):
            st = p.stat()
            if info.get("outputs", {}).get(p.name) != [st.st_size, st.st_mtime_ns]:
                raise RuntimeError(f"mixed/modified research generation: {p.name}")
    _checked = (key, time.monotonic(), info)
    return info


# ----------------------------------------------------------------------------- data access

def fills(markets=None, columns=None) -> pd.DataFrame:
    """Taker fills. Columns: m (market code), w (wallet code), ts (unix s, on-chain settlement
    time ~2.6 s after the match), size (shares), s (side acquired, 0/1 = outcome index), q (price
    paid for side s), y (payout of side s: 1/0, 0.5 void), fee_rate, in_play (ts >= scheduled start).
    `markets`: iterable of market codes to load (row groups are sorted by m -> fast)."""
    import pyarrow.dataset as ds
    validate_cache()
    d = ds.dataset(FILLS, format="parquet")
    filt = None if markets is None else ds.field("m").isin(np.asarray(list(markets), dtype=np.int32))
    return d.to_table(columns=columns, filter=filt).to_pandas()


def markets() -> pd.DataFrame:
    validate_cache()
    return pd.read_parquet(MARKETS)


def wallet_ids() -> pd.Series:
    validate_cache()
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

    manifest_path = RESEARCH / "manifest.json"
    # Migrate IDs from the actual legacy tables, never recompute from sorted files.
    legacy_mcat = _market_codes() if MARKETS.exists() else []
    legacy_wcat = pd.read_parquet(WALLETS).wallet.tolist() if WALLETS.exists() else []
    from ..analysis.favorites import cached_pregame_prices
    cached_pregame_prices()
    source = source_identity()
    t = load_trades(u, legacy_mcat=legacy_mcat, legacy_wcat=legacy_wcat)
    mcat, wcat = t.condition_id.cat.categories, t.proxyWallet.cat.categories
    f = pd.DataFrame({"m": t.condition_id.cat.codes.to_numpy().astype(np.int32),
                      "w": t.proxyWallet.cat.codes.to_numpy().astype(np.int32),
                      "ts": t.timestamp.to_numpy().astype(np.int64),
                      "size": t["size"].to_numpy(np.float32), "s": t.side_idx.to_numpy(np.int8),
                      "q": t.q.to_numpy(np.float32), "y": t.y.to_numpy(np.float32),
                      "fee_rate": t.fee_rate.to_numpy(np.float32), "in_play": t.in_play.to_numpy()})
    del t
    f = f.sort_values(["m", "ts"], kind="stable")
    # Construct all outputs before touching a published generation. The marker
    # makes an interrupted multi-file promotion fail closed for every accessor.
    with tempfile.TemporaryDirectory(prefix="build-", dir=RESEARCH) as staging:
        staging = Path(staging)
        f.to_parquet(staging / FILLS.name, index=False, row_group_size=500_000)
        pd.DataFrame({"wallet": np.asarray(wcat, dtype=object)}).to_parquet(staging / WALLETS.name, index=False)
        build_markets(u, list(mcat), output=staging / MARKETS.name)
        if source != source_identity():
            raise RuntimeError("source data changed during build; no new generation published")
        outputs = {p.name: [p.stat().st_size, p.stat().st_mtime_ns] for p in staging.iterdir()}
        manifest = {"source": source, "outputs": outputs,
                    "coverage": "Existing bounded selected tapes plus explicit upgrades; not full universe",
                    "id_policy": "Append-only market and wallet IDs; absent old IDs retained"}
        (staging / "manifest.json").write_text(json.dumps(manifest, sort_keys=True))
        marker = RESEARCH / "BUILDING"
        marker.write_text(f"pid={os.getpid()} promotion of validated generation\n")
        for target in (FILLS, WALLETS, MARKETS, manifest_path):
            (staging / target.name).replace(target)
        marker.unlink()


def _market_codes() -> list[str]:
    """Market code order used by fills.m (= load_trades: sorted non-empty tape files)."""
    import pyarrow.parquet as pq
    from ..wallets.tapes import TAPES
    if MARKETS.exists():
        old = pd.read_parquet(MARKETS, columns=["m", "condition_id"]).sort_values("m")
        if old.m.tolist() != list(range(len(old))) or old.condition_id.duplicated().any():
            raise ValueError("invalid existing market ID mapping; refusing to remap it")
        return old.condition_id.tolist()
    return [f.stem for f in sorted(TAPES.glob("*.parquet")) if pq.ParquetFile(f).metadata.num_rows > 0]


def build_markets(u: pd.DataFrame | None = None, mcat: list[str] | None = None, output=None) -> None:
    u = universe() if u is None else u
    mcat = _market_codes() if mcat is None else mcat
    mcat = pd.Index(mcat, name="condition_id")
    meta = u.drop_duplicates("condition_id").set_index("condition_id").reindex(mcat)
    pay = u.pivot_table(index="condition_id", columns="outcome_idx", values="payout", aggfunc="first").reindex(mcat)
    pay = pay.reindex(columns=[0, 1])
    pay = pay.where(pay.isin([0.0, .5, 1.0]).all(axis=1) & pay.sum(axis=1).eq(1), axis=0)
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
    mk.to_parquet(output or MARKETS, index=False)
    print(f"markets {len(mk):,}")


if __name__ == "__main__":
    if sys.argv[1:] == ["build"]:
        build()
    elif sys.argv[1:] == ["build-markets"]:
        build()  # A metadata-only replacement would invalidate the shared generation.
