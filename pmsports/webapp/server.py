"""Local research server: every tested strategy, browsable by sport, trade by trade.

  .venv/bin/python -m pmsports.webapp            # http://0.0.0.0:8808  (Tailscale-reachable)

Reads data/research/ledgers/*.json (the per-trade ledgers). Ledgers are loaded lazily and
cached, and trades are paged over HTTP, so nothing large is ever shipped at once.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse

ROOT = Path(__file__).resolve().parents[2]
LEDGERS = ROOT / "data" / "research" / "ledgers"
STATIC = Path(__file__).resolve().parent / "static"
INDEX = LEDGERS / "_index.json"
MARKETS = ROOT / "data" / "research" / "markets.parquet"
log = logging.getLogger("pmsports.webapp")

app = FastAPI(title="Polymarket research", docs_url=None, redoc_url=None)

@app.middleware("http")
async def add_noindex(request, call_next):
    response = await call_next(request)
    response.headers["X-Robots-Tag"] = "noindex"
    return response

@app.get("/robots.txt")
def robots():
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse("User-agent: *\nDisallow: /", headers={"X-Robots-Tag": "noindex"})

_cache: dict[str, dict] = {}
_lock = threading.Lock()
_index_lock = threading.Lock()
_market_lock = threading.Lock()
_market_cache: dict = {}
INDEX_VERSION = 4
COMPACT_SOURCES = {"pmsports/analysis/calibration.py", "pmsports/analysis/whale_prices.py"}

SPORT_LABEL = {"baseball": "Baseball", "soccer": "Soccer", "american_football": "Football",
               "basketball": "Basketball", "tennis": "Tennis", "esports": "Esports",
               "hockey": "Hockey", "cricket": "Cricket", "mma_boxing": "MMA / Boxing", "multi": "Multi-sport"}
TAB_ORDER = ["baseball", "soccer", "american_football", "basketball", "tennis", "esports", "hockey", "other"]
# some ledgers label the sport at league level; fold those into the tab families
SPORT_ALIAS = {"cs2": "esports", "dota2": "esports", "lol": "esports", "val": "esports", "valorant": "esports",
               "csgo": "esports", "rl": "esports", "mlb": "baseball", "nfl": "american_football",
               "cfb": "american_football", "ncaaf": "american_football", "nba": "basketball",
               "wnba": "basketball", "ncaab": "basketball", "nhl": "hockey", "atp": "tennis", "wta": "tennis",
               "epl": "soccer", "ucl": "soccer", "uel": "soccer", "football": "american_football"}


def _fam(s: str) -> str:
    s = str(s).strip().lower() if pd.notna(s) else ""
    sp = SPORT_ALIAS.get(s, s)
    return sp if sp in TAB_ORDER else "other"


def _ledger_files() -> list[Path]:
    return sorted(f for f in LEDGERS.glob("*.json") if not f.name.startswith(("_", ".")))


def _market_identity() -> tuple:
    try:
        st = MARKETS.stat()
        return (str(MARKETS), st.st_mtime_ns, st.st_size, st.st_ino, st.st_ctime_ns)
    except FileNotFoundError:
        return (str(MARKETS), None)


def _market_labels() -> pd.DataFrame:
    """Read only the immutable compact-ID dictionary, never the transaction tape."""
    identity = _market_identity()
    with _market_lock:
        if _market_cache.get("identity") == identity:
            return _market_cache["labels"]
        columns = ["m", "event_slug", "market_slug", "o0", "o1", "closed_ts"]
        try:
            labels = pd.read_parquet(MARKETS, columns=columns)
            codes = pd.to_numeric(labels.m, errors="coerce")
            valid = codes.notna() & np.isfinite(codes) & codes.ge(0) & codes.mod(1).eq(0)
            labels = labels.loc[valid].assign(m=codes.loc[valid])
            # An ambiguous mapping must never pick a convenient first row.
            labels = labels.loc[~labels.m.duplicated(keep=False)].set_index("m")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.warning("Compact market labels unavailable: %s", exc)
            labels = pd.DataFrame(columns=columns).set_index("m")
        _market_cache.update(identity=identity, labels=labels)
        return labels


def _compact_labels(doc: dict, df: pd.DataFrame) -> pd.DataFrame:
    if doc.get("code_path") not in COMPACT_SOURCES:
        return df
    df = df.copy()
    # Only these two producers are known to encode physical outcome indices.
    market = df.get("m", df.get("market", pd.Series(index=df.index, dtype=float)))
    side = df.get("s", df.get("side", pd.Series(index=df.index, dtype=float)))
    df["market_code"], df["side_code"] = market, side
    df["event_code"] = df.get("event", pd.Series(index=df.index, dtype=float))
    labels = _market_labels()
    codes = pd.to_numeric(market, errors="coerce")
    sides = pd.to_numeric(side, errors="coerce")

    def label(column, unavailable):
        values = codes.map(labels[column]).astype("string")
        return values.where(values.notna() & values.str.strip().ne(""), unavailable)

    df["event"] = label("event_slug", "Event unavailable")
    df["market"] = label("market_slug", "Market unavailable")
    df["side"] = "Outcome unavailable"
    for idx in (0, 1):
        mask = sides.eq(idx)
        df.loc[mask, "side"] = label(f"o{idx}", "Outcome unavailable").loc[mask]
    # Whale audits omit settlement display fields. Resolve them without touching cash.
    if doc.get("code_path") == "pmsports/analysis/whale_prices.py":
        if "exit_ts" not in df:
            df["exit_ts"] = pd.to_numeric(codes.map(labels.closed_ts), errors="coerce")
        if "exit_price" not in df:
            payout = pd.to_numeric(df.get("y", pd.Series(index=df.index, dtype=float)), errors="coerce")
            df["exit_price"] = payout.where(payout.isin([0., .5, 1.]))
        if "date" not in df:
            clock = df.entry_ts.fillna(df.get("signal_ts", pd.Series(index=df.index, dtype=float)))
            df["date"] = pd.to_datetime(clock, unit="s", utc=True, errors="coerce").dt.strftime("%Y-%m-%d")
    return df


def _sport_counts(doc: dict, df: pd.DataFrame) -> dict:
    counts = df.sport.map(_fam).value_counts().to_dict()
    declared = str(doc.get("sport") or "").strip().lower()
    if df.empty and declared != "multi" and declared in (set(SPORT_LABEL) | set(SPORT_ALIAS) | {"other"}):
        counts[_fam(declared)] = 0
    return {k: int(v) for k, v in counts.items()}


def _read_ledger(f: Path) -> tuple[dict, pd.DataFrame]:
    doc = json.loads(f.read_text())
    if doc.get("slug") != f.stem:
        raise ValueError("ledger slug must match its filename")
    df = pd.DataFrame(doc["rows"], columns=doc["columns"])
    required = {"id", "entry_ts", "stake_usd", "fee_usd", "pnl_usd", "sport", "period"}
    missing = required - set(df.columns)
    if missing or not df.columns.is_unique:
        raise ValueError(f"invalid ledger columns; missing={sorted(missing)}")
    for c in ("entry_ts", "entry_price", "stake_usd", "fee_usd", "exit_price", "payout", "pnl_usd", "roi"):
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = _compact_labels(doc, df)
    return doc, df


def _load(slug: str) -> dict:
    if not slug or slug.startswith("_") or not slug.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(400, "invalid slug")
    f = LEDGERS / f"{slug}.json"
    if not f.exists():
        raise HTTPException(404, f"no ledger {slug}")
    st = f.stat()
    mkey = (st.st_mtime_ns, st.st_size, _market_identity())
    with _lock:
        if slug in _cache and _cache[slug].get("mkey") == mkey:
            return _cache[slug]
    try:
        doc, df = _read_ledger(f)
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(500, f"malformed ledger {slug}: {e}") from e
    deployed = df.stake_usd.fillna(0) + df.fee_usd.fillna(0)
    df["roi_deployed"] = df.pnl_usd / deployed.where(deployed > 0)
    meta = {k: v for k, v in doc.items() if k != "rows"}
    meta["columns"] = list(df.columns)
    entry = {"meta": meta, "df": df, "mkey": mkey}
    with _lock:
        _cache[slug] = entry
        if len(_cache) > 8:
            _cache.pop(next(iter(_cache)))
    return entry


def _calc_kpis(df: pd.DataFrame) -> dict:
    res = {}
    for p in sorted(df.period.dropna().unique()) if "period" in df else ["all"]:
        pdf = df[df.period == p] if "period" in df else df
        if pdf.empty: continue
        dep = (pdf.get("stake_usd", pd.Series(0, index=pdf.index)).fillna(0) + pdf.get("fee_usd", pd.Series(0, index=pdf.index)).fillna(0)).sum()
        pnl = pdf.get("pnl_usd", pd.Series(0, index=pdf.index)).sum()
        filled = (pdf.get("stake_usd", pd.Series(0, index=pdf.index)).fillna(0) + pdf.get("fee_usd", pd.Series(0, index=pdf.index)).fillna(0)) > 0
        res[p] = {"roi": float(pnl / dep) if dep else None, "bets": int(filled.sum())}
    return res

def build_index(force: bool = False) -> list[dict]:
    # Exclude our own sidecars: writing the index must not invalidate itself.
    # Serialize rebuilds so concurrent clients do not each parse the full corpus.
    with _index_lock:
        ledgers = _ledger_files()
        market_identity = list(_market_identity())
        fingerprint = []
        for f in ledgers:
            st = f.stat()
            fingerprint.append([f.name, st.st_mtime_ns, st.st_size])
        if INDEX.exists() and not force:
            try:
                cached = json.loads(INDEX.read_text())
                if (cached.get("version") == INDEX_VERSION and cached.get("fingerprint") == fingerprint
                        and cached.get("market_identity") == market_identity):
                    return cached["strategies"]
            except (ValueError, KeyError, AttributeError):
                pass
        out = []
        for f in ledgers:
            try:
                doc, df = _read_ledger(f)
                counts = _sport_counts(doc, df)
                kpis = {"all": _calc_kpis(df)}
                for sp, sdf in df.groupby(df.sport.map(_fam)):
                    kpis[sp] = _calc_kpis(sdf)
                out.append({
                    "slug": doc["slug"], "title": doc.get("title", doc["slug"]), "group": doc.get("group", ""),
                    "sport": doc.get("sport", ""), "verdict": doc.get("verdict", ""),
                    "hypothesis": doc.get("hypothesis", ""), "headline": doc.get("headline", {}),
                    "kpis": kpis,
                    "n_total_trades": doc.get("n_total_trades", len(df)), "rows_available": len(df),
                    "sport_counts": counts,
                    "report_path": doc.get("report_path", ""), "code_path": doc.get("code_path", ""),
                    "page_sampled": bool(doc.get("truncated")),
                })
            except (ValueError, KeyError, TypeError) as e:
                out.append({
                    "slug": f.stem, "title": f"Cannot read: {f.name}", "verdict": "ERROR", "error": str(e),
                    "n_total_trades": 0, "rows_available": 0, "kpis": {"all": {}}, "sport_counts": {},
                })
        INDEX.parent.mkdir(parents=True, exist_ok=True)
        tmp = INDEX.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": INDEX_VERSION, "fingerprint": fingerprint,
                                  "market_identity": market_identity, "strategies": out}, separators=(",", ":")))
        tmp.replace(INDEX)
        return out


@app.get("/api/index")
def api_index():
    idx = build_index()
    tabs = {}
    for d in idx:
        for sp, n in (d.get("sport_counts") or {}).items():
            key = sp if sp in TAB_ORDER else "other"
            tabs[key] = tabs.get(key, 0) + n
    order = [t for t in TAB_ORDER if t in tabs]
    return {"strategies": idx,
            "tabs": [{"key": "all", "label": "All sports", "trades": sum(tabs.values())}] +
                    [{"key": t, "label": SPORT_LABEL.get(t, t.title()), "trades": tabs[t]} for t in order]}


@app.get("/api/strategy/{slug}")
def api_strategy(slug: str):
    e = _load(slug)
    df = e["df"]
    by_sport = []
    if "sport" in df:
        canon_sport = df.sport.map(_fam)
        for sp, g in df.groupby(canon_sport):
            dep = (g.stake_usd.fillna(0) + g.fee_usd.fillna(0)).sum()
            by_sport.append({"sport": sp, "trades": int(len(g)), "pnl": float(g.pnl_usd.sum()),
                             "roi": float(g.pnl_usd.sum() / dep) if dep else None})
        sports = sorted(canon_sport.dropna().unique().tolist())
    else:
        sports = []
    if df.empty:
        sports = list(_sport_counts(e["meta"], df))
        by_sport = [{"sport": sp, "trades": 0, "pnl": 0., "roi": None} for sp in sports]
    return {"meta": e["meta"], "by_sport": sorted(by_sport, key=lambda d: -d["trades"]),
            "sports": sports,
            "periods": sorted(df.period.dropna().unique().tolist()) if "period" in df else []}


def _filter(df: pd.DataFrame, period: str, sport: str, result: str, q: str) -> pd.DataFrame:
    if period != "all" and "period" in df:
        df = df[df.period == period]
    if sport != "all" and "sport" in df:
        df = df[df.sport.map(_fam) == _fam(sport)]
    if result == "win":
        df = df[df.pnl_usd > 0]
    elif result == "loss":
        df = df[df.pnl_usd < 0]
    elif result == "void":
        df = df[(df.get("exit_price", pd.Series(index=df.index, dtype=float)) == 0.5) &
                (df.get("exit_kind", pd.Series("", index=df.index)) == "resolution")]
    if q:
        ql = q.lower()
        hay = pd.Series("", index=df.index)
        for col in ("event", "market", "side", "note"):
            hay = hay + " " + df.get(col, pd.Series("", index=df.index)).fillna("").astype(str)
        df = df[hay.str.lower().str.contains(ql, regex=False)]
    return df


@app.get("/api/strategy/{slug}/trades")
def api_trades(slug: str, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=1000), period: str = "all",
               sport: str = "all", result: str = "all", q: str = "", sort: str = "entry_ts",
               desc: bool = False):
    e = _load(slug)
    df = _filter(e["df"], period, sport, result, q)
    if sort in df.columns:
        df = df.sort_values(sort, ascending=not desc, kind="stable")
    dep = (df.stake_usd.fillna(0) + df.fee_usd.fillna(0)).sum()
    page = df.iloc[offset: offset + limit]
    return JSONResponse({
        "total": int(len(df)), "offset": offset, "limit": limit,
        "fills": int((df.stake_usd.fillna(0) + df.fee_usd.fillna(0)).gt(0).sum()),
        "pnl": float(df.pnl_usd.sum()), "roi": float(df.pnl_usd.sum() / dep) if dep else None,
        "wins": int((df.pnl_usd > 0).sum()), "kpis": _calc_kpis(df), "columns": list(page.columns),
        "rows": json.loads(page.to_json(orient="values")),
    })


@app.get("/api/strategy/{slug}/equity")
def api_equity(slug: str, period: str = "all", sport: str = "all", result: str = "all", q: str = "",
               points: int = Query(600, ge=2, le=2000)):
    e = _load(slug)
    df = _filter(e["df"], period, sport, result, q)
    clock = df.entry_ts.fillna(df.get("signal_ts", pd.Series(index=df.index, dtype=float)))
    df = df.assign(_clock=clock).sort_values("_clock", kind="stable")
    if df.empty:
        return {"n": 0, "points": [], "holdout_at": None, "dates": []}
    cum = df.pnl_usd.fillna(0).cumsum().to_numpy()
    n = len(cum)
    idx = np.unique(np.linspace(0, n - 1, min(points, n)).astype(int))
    hold = None
    if "period" in df:
        h = np.flatnonzero((df.period == "holdout").to_numpy())
        hold = int(h[0]) if len(h) else None
    return {"n": n, "holdout_at": hold,
            "points": [[int(i), round(float(cum[i]), 2)] for i in idx],
            "dates": [str(df.date.iloc[i]) for i in idx] if "date" in df else []}


@app.get("/api/health")
def health():
    index = build_index()
    errors = [{"slug": d["slug"], "error": d["error"]} for d in index if d.get("error")]
    return {"ok": not errors, "ledgers": len(index), "errors": errors, "cached": list(_cache)}


@app.post("/api/reindex")
def reindex():
    with _lock:
        _cache.clear()
    return {"strategies": len(build_index(force=True))}


@app.get("/")
def home():
    return FileResponse(STATIC / "index.html")


@app.get("/app.js")
def appjs():
    return FileResponse(STATIC / "app.js", media_type="application/javascript")
