"""Per-market taker tape (every fill, wallet-attributed) for sports game markets.

Why per-market and not per-wallet: the Data API's /trades?user= silently returns nothing
for most of 2025 (a wallet with $37M of 2025 MLB fills shows 0 trades for June 2025), while
/trades?market= is complete. Every row is the *taker* of a fill - the wallet that chose to
cross the spread - which is precisely the copyable decision.

Output: data/wallets/tapes/<condition_id>.parquet
"""
from __future__ import annotations

import logging
import hashlib
import json
import os
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

from .. import polymarket as pm
from ..collect import _write
from .universe import OUT

log = logging.getLogger("pmsports")
TAPES = OUT / "tapes"


def select_markets(u: pd.DataFrame, market_types=("moneyline",), min_volume=0.0,
                   since="2025-01-01", families=None) -> pd.DataFrame:
    m = u.drop_duplicates("condition_id")
    m = m[m.market_type.isin(market_types) & m.game_start_ts.notna()]
    if min_volume > 0:
        log.warning("Retrospective eventual-volume filter requested: %.0f", min_volume)
        m = m[m.volume >= min_volume]
    m = m[m.game_start_ts >= pd.Timestamp(since, tz="UTC").timestamp()]
    if families:
        m = m[m.family.isin(families)]
    # Eventual volume may be requested for retrospective audits only.
    return m.sort_values(["game_start_ts", "condition_id"], kind="stable")


TAPE_VERSION = 2


def _window(r, since=None) -> tuple[int, int]:
    start = r.get("creation_ts") if hasattr(r, "get") else getattr(r, "creation_ts", None)
    if start is None or not pd.notna(start):
        start = pd.Timestamp(since or "2025-01-01", tz="UTC").timestamp()
    end = r.closed_ts if pd.notna(r.closed_ts) else r.game_start_ts + 7 * 86400
    if not np.isfinite(start) or not np.isfinite(end) or end < start:
        raise ValueError(f"invalid tape window for {r.condition_id}: {start}..{end}")
    return int(start), int(end) + 1800


def _request(r, since=None):
    return {"version": TAPE_VERSION, "requested_window": list(_window(r, since)),
            "condition_id": r.condition_id, "since": since,
            "code_hash": hashlib.sha256(Path(__file__).read_bytes() + Path(pm.__file__).read_bytes()).hexdigest()}


def _fresh(r, since=None):
    path = TAPES / f"{r.condition_id}.parquet"
    manifest = TAPES / f"{r.condition_id}.manifest.json"
    try:
        m = json.loads(manifest.read_text())
        st = path.stat()
        return (m["status"] == "ok" and m["request"] == _request(r, since)
                and m["file"] == {"size": st.st_size, "mtime_ns": st.st_mtime_ns})
    except (OSError, ValueError, KeyError):
        return False

def _fetch(r, since=None) -> int:
    request = _request(r, since)
    creation = getattr(r, "creation_ts", None)
    # Old universe snapshots lack creation times. Recover from the market itself;
    # event/start dates are not evidence of market listing time.
    if creation is None or not pd.notna(creation):
        from ..http import get_json
        try:
            found = get_json(f"{pm.GAMMA}/markets", {"condition_ids": r.condition_id, "closed": "true"})
            meta = next((x for x in found if x.get("conditionId") == r.condition_id), {})
            creation = pm.parse_ts(meta.get("createdAt"))
        except Exception as exc:
            log.warning("creation metadata unavailable %s: %s", r.condition_id, exc)
        if creation is not None and pd.notna(creation):
            from types import SimpleNamespace
            r = SimpleNamespace(**{**r._asdict(), "creation_ts": creation})
    s, e = _window(r, since)
    t = pd.DataFrame(pm.trades(r.condition_id, s, e), columns=list(pm.TRADE_FIELDS))
    t.insert(0, "condition_id", r.condition_id)
    manifest = {
        "version": TAPE_VERSION, "request": request,
        "requested_window": [s, e],
        "count": len(t),
        "actual_window": [int(t.timestamp.min()), int(t.timestamp.max())] if len(t) else None,
        "retrieval_params": {"since": since},
        "status": "ok",
        "coverage": "creation_to_close_requested" if pd.notna(creation) and pd.notna(r.closed_ts) else "bounded",
        "creation_ts": float(creation) if pd.notna(creation) else None,
        "completeness": "Provider-returned fills; not proof of historical API completeness or order-book depth."
    }
    tgt = TAPES / f"{r.condition_id}.parquet"
    mtgt = TAPES / f"{r.condition_id}.manifest.json"
    tgt.parent.mkdir(parents=True, exist_ok=True)
    tmp = tgt.with_suffix(f".{os.getpid()}.tmp")
    mtmp = mtgt.with_suffix(f".{os.getpid()}.tmp")
    t.to_parquet(tmp, index=False)
    st = tmp.stat()
    manifest["file"] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns}
    mtmp.write_text(json.dumps(manifest, sort_keys=True))
    tmp.replace(tgt)
    mtmp.replace(mtgt)  # Manifest last; interrupted replacement is never fresh.
    return len(t)


def fetch_tapes(markets: pd.DataFrame, workers: int = 6, max_markets: int = None, since: str = None) -> None:
    todo = []
    for r in markets.itertuples(index=False):
        if _fresh(r, since):
            continue
        todo.append(r)

    if max_markets is not None:
        if max_markets < 0:
            raise ValueError("max_markets must be nonnegative")
        todo = todo[:max_markets]

    log.info("tapes: %d markets to fetch (%d already done)", len(todo), len(markets) - len(todo))
    t0, done, failed, rows = time.time(), 0, 0, 0
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(_fetch, r, since): r for r in todo}
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
    return {"ok": done, "failed": failed, "rows": rows, "scheduled": len(todo)}


def load_trades(u: pd.DataFrame, cids=None, legacy_mcat: list[str] = None, legacy_wcat: list[str] = None) -> pd.DataFrame:
    """All tapes as copyable 'bought side X at q' rows joined to outcomes."""
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    files = sorted(TAPES.glob("*.parquet"))
    if cids is not None:
        cids = set(cids)
        files = [f for f in files if f.stem in cids]

    wid: dict[str, int] = {}
    if legacy_wcat:
        for w in legacy_wcat:
            wid[w] = len(wid)

    cols = {k: [] for k in ("m", "w", "ts", "size", "side_idx", "q")}
    mkts: list[str] = list(legacy_mcat or [])
    mid = {cid: i for i, cid in enumerate(mkts)}
    # token id -> outcome index per market: the Data API's `outcomeIndex` is wrong on a few
    # thousand fills (an API glitch, mostly tennis on 2026-05-13/14); the asset id never is.
    toks = u.pivot_table(index="condition_id", columns="outcome_idx", values="token_id", aggfunc="first")
    tok_of = {c: (a, b) for c, a, b in zip(toks.index, toks[0], toks[1])}
    for f in files:
        tb = pq.read_table(f, columns=["timestamp", "proxyWallet", "side", "outcomeIndex", "price", "size", "asset"])
        tk = tok_of.get(f.stem)
        if tk is None:
            continue  # Unknown contract/token mapping cannot establish acquired side.
        tb = tb.filter(pc.is_in(tb["asset"], value_set=__import__("pyarrow").array(list(tk))))
        if tb.num_rows == 0:
            continue
        de = pc.dictionary_encode(tb["proxyWallet"]).combine_chunks()
        gmap = np.fromiter((wid.setdefault(w, len(wid)) for w in de.dictionary.to_pylist()), dtype=np.int32)
        buy = pc.equal(tb["side"], "BUY").to_numpy(zero_copy_only=False)
        oi = tb["outcomeIndex"].to_numpy(zero_copy_only=False).astype(np.int8)
        if tk is not None:
            asset = tb["asset"].to_numpy(zero_copy_only=False)
            oi = np.where(asset == tk[0], 0, np.where(asset == tk[1], 1, oi)).astype(np.int8)
        px = tb["price"].to_numpy(zero_copy_only=False).astype(np.float32)
        if f.stem not in mid:
            mid[f.stem] = len(mkts)
            mkts.append(f.stem)
        cols["m"].append(np.full(tb.num_rows, mid[f.stem], dtype=np.int32))
        cols["w"].append(gmap[de.indices.to_numpy(zero_copy_only=False)])
        cols["ts"].append(tb["timestamp"].to_numpy(zero_copy_only=False).astype(np.int64))
        cols["size"].append(tb["size"].to_numpy(zero_copy_only=False).astype(np.float32))
        cols["side_idx"].append(np.where(buy, oi, 1 - oi).astype(np.int8))
        cols["q"].append(np.where(buy, px, 1 - px).astype(np.float32))
    arr = {k: np.concatenate(v) if v else np.array([], dtype=np.int64 if k in ("m", "w", "ts", "side_idx") else float) for k, v in cols.items()}
    wallets = np.empty(len(wid), dtype=object)
    for w, i in wid.items():
        wallets[i] = w
    t = pd.DataFrame({
        "condition_id": pd.Categorical.from_codes(arr["m"], categories=pd.Index(mkts)),
        "timestamp": arr["ts"],
        "proxyWallet": pd.Categorical.from_codes(arr["w"], categories=pd.Index(wallets)),
        "size": arr["size"], "side_idx": arr["side_idx"], "q": arr["q"]})
    del arr, cols
    t = t[t["size"] > 0]
    # outcome + market metadata via lookups on the market codes (no string merge)
    cats = t.condition_id.cat.categories
    codes = t.condition_id.cat.codes.to_numpy()
    pay = u[u.condition_id.isin(cats)].pivot_table(index="condition_id", columns="outcome_idx",
                                                   values="payout", aggfunc="first").reindex(cats)
    pay = pay.reindex(columns=[0, 1])
    valid_pay = pay.isin([0.0, .5, 1.0]).all(axis=1) & pay.sum(axis=1).eq(1)
    lut = pay.where(valid_pay, axis=0).to_numpy(dtype="float32")
    t["y"] = lut[codes, t.side_idx.to_numpy()]
    meta = u.drop_duplicates("condition_id").set_index("condition_id").reindex(cats)
    for c in ("family", "event_slug"):
        t[c] = pd.Categorical.from_codes(*_codes(meta[c].to_numpy(), codes))
    t["fee_rate"] = meta["fee_rate"].to_numpy(dtype="float32")[codes]
    t["in_play"] = t.timestamp.to_numpy() >= meta["game_start_ts"].to_numpy(dtype="float64")[codes]
    return t[~np.isnan(t.y.to_numpy())]


def _codes(per_market: np.ndarray, market_codes: np.ndarray):
    """Categorical (codes, categories) for a per-market attribute broadcast to rows."""
    vals, inv = np.unique(pd.Series(per_market).fillna("").astype(str).to_numpy(), return_inverse=True)
    return inv[market_codes].astype(np.int32), pd.Index(vals)
