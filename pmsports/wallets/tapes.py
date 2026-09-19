"""Per-market taker tape (every fill, wallet-attributed) for sports game markets.

Why per-market and not per-wallet: the Data API's /trades?user= silently returns nothing
for most of 2025 (a wallet with $37M of 2025 MLB fills shows 0 trades for June 2025), while
/trades?market= is complete. Every row is the *taker* of a fill - the wallet that chose to
cross the spread - which is precisely the copyable decision.

Output: data/wallets/tapes/<condition_id>.parquet
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

from .. import polymarket as pm
from ..collect import _write
from .universe import OUT

log = logging.getLogger("pmsports")
TAPES = OUT / "tapes"


def select_markets(u: pd.DataFrame, market_types=("moneyline",), min_volume=5000.0,
                   since="2025-01-01", families=None) -> pd.DataFrame:
    m = u.drop_duplicates("condition_id")
    m = m[m.market_type.isin(market_types) & (m.volume >= min_volume) & m.game_start_ts.notna()]
    m = m[m.game_start_ts >= pd.Timestamp(since, tz="UTC").timestamp()]
    if families:
        m = m[m.family.isin(families)]
    return m.sort_values("volume", ascending=False)


def _window(r) -> tuple[int, int]:
    start = r.game_start_ts
    end = r.closed_ts if pd.notna(r.closed_ts) else start + 8 * 3600
    end = min(max(end, start + 3 * 3600), start + 14 * 3600)   # guard bogus closed times
    return int(start - 24 * 3600), int(end + 1800)


def _fetch(r) -> int:
    s, e = _window(r)
    t = pd.DataFrame(pm.trades(r.condition_id, s, e), columns=list(pm.TRADE_FIELDS))
    t.insert(0, "condition_id", r.condition_id)
    _write(t, TAPES / f"{r.condition_id}.parquet")
    return len(t)


def fetch_tapes(markets: pd.DataFrame, workers: int = 6) -> None:
    todo = [r for r in markets.itertuples(index=False) if not (TAPES / f"{r.condition_id}.parquet").exists()]
    log.info("tapes: %d markets to fetch (%d already done)", len(todo), len(markets) - len(todo))
    t0, done, failed, rows = time.time(), 0, 0, 0
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(_fetch, r): r for r in todo}
        for f in as_completed(futs):
            try:
                rows += f.result()
                done += 1
            except Exception as exc:
                failed += 1
                log.warning("tape %s failed: %s", futs[f].market_slug, exc)
            if (done + failed) % 200 == 0:
                rate = (done + failed) / (time.time() - t0)
                log.info("tapes %d/%d (%d failed, %d rows) %.1f mkts/s eta %.0f min", done + failed, len(todo),
                         failed, rows, rate, (len(todo) - done - failed) / max(rate, 1e-9) / 60)
    log.info("tapes done: %d ok, %d failed, %d rows", done, failed, rows)


def load_trades(u: pd.DataFrame, cids=None, cols=("condition_id", "timestamp", "proxyWallet", "side",
                                                  "outcomeIndex", "price", "size")) -> pd.DataFrame:
    """All tapes as copyable 'bought side X at q' rows joined to outcomes.

    A taker SELL of outcome k at p is economically a purchase of the other outcome at 1-p
    (binary markets), so every fill becomes: wallet bought `side_idx` at `q`, won `y`.
    """
    import pyarrow.dataset as ds
    files = sorted(str(f) for f in TAPES.glob("*.parquet"))
    if cids is not None:
        cids = set(cids)
        files = [f for f in files if f.rsplit("/", 1)[-1][:-8] in cids]
    # dictionary-encode wallet/market ids: tens of millions of fills stay a few hundred MB
    tbl = ds.dataset(files, format="parquet").to_table(columns=list(cols))
    t = tbl.to_pandas(strings_to_categorical=True)
    t = t[t["size"] > 0]
    t["side_idx"] = np.where(t.side == "BUY", t.outcomeIndex, 1 - t.outcomeIndex).astype("int8")
    t["q"] = np.where(t.side == "BUY", t.price, 1 - t.price).astype("float32")
    t = t.drop(columns=["side", "outcomeIndex", "price"])
    # outcome + market metadata via lookups on the categorical codes (no string merge)
    cats = t.condition_id.cat.categories
    codes = t.condition_id.cat.codes.to_numpy()
    pay = u[u.condition_id.isin(cats)].pivot_table(index="condition_id", columns="outcome_idx",
                                                   values="payout", aggfunc="first").reindex(cats)
    lut = pay.reindex(columns=[0, 1]).to_numpy(dtype="float32")
    t["payout"] = lut[codes, t.side_idx.to_numpy()]
    meta = u.drop_duplicates("condition_id").set_index("condition_id").reindex(cats)
    for c in ("family", "league", "market_type", "event_slug"):
        t[c] = pd.Series(meta[c].to_numpy()[codes], index=t.index).astype("category")
    for c in ("game_start_ts", "fee_rate"):
        t[c] = meta[c].to_numpy(dtype="float64")[codes]
    t = t[~np.isnan(t.payout.to_numpy())]
    t["y"] = t.payout.astype("float32")
    t["size"] = t["size"].astype("float32")
    t["in_play"] = t.timestamp >= t.game_start_ts
    return t.drop(columns=["payout"])
