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
log = logging.getLogger("pmsports.webapp")

app = FastAPI(title="Polymarket research", docs_url=None, redoc_url=None)
_cache: dict[str, dict] = {}
_lock = threading.Lock()

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
    s = (s or "").strip().lower()
    return SPORT_ALIAS.get(s, s)


def _load(slug: str) -> dict:
    with _lock:
        if slug in _cache:
            return _cache[slug]
    f = LEDGERS / f"{slug}.json"
    if not f.exists():
        raise HTTPException(404, f"no ledger {slug}")
    doc = json.loads(f.read_text())
    df = pd.DataFrame(doc["rows"], columns=doc["columns"])
    for c in ("entry_price", "stake_usd", "fee_usd", "exit_price", "payout", "pnl_usd", "roi"):
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    deployed = df.get("stake_usd", pd.Series(0, index=df.index)).fillna(0) + df.get("fee_usd", pd.Series(0, index=df.index)).fillna(0)
    df["roi_deployed"] = np.where(deployed > 0, df.pnl_usd / deployed.replace(0, np.nan), df.get("roi"))
    doc = {k: v for k, v in doc.items() if k != "rows"}
    entry = {"meta": doc, "df": df}
    with _lock:
        _cache[slug] = entry
        if len(_cache) > 8:                       # keep memory flat: drop the oldest
            _cache.pop(next(iter(_cache)))
    return entry


def build_index(force: bool = False) -> list[dict]:
    if INDEX.exists() and not force:
        try:
            return json.loads(INDEX.read_text())
        except Exception:
            pass
    out = []
    for f in sorted(LEDGERS.glob("*.json")):
        if f.name.startswith("_"):
            continue
        doc = json.loads(f.read_text())
        df = pd.DataFrame(doc["rows"], columns=doc["columns"])
        counts = df.sport.map(_fam).value_counts().to_dict() if "sport" in df else {}
        out.append({
            "slug": doc["slug"], "title": doc.get("title", doc["slug"]), "group": doc.get("group", ""),
            "sport": doc.get("sport", ""), "verdict": doc.get("verdict", ""),
            "hypothesis": doc.get("hypothesis", ""), "headline": doc.get("headline", {}),
            "n_total_trades": doc.get("n_total_trades", len(df)), "rows_available": len(df),
            "sport_counts": {k: int(v) for k, v in counts.items()},
            "report_path": doc.get("report_path", ""), "code_path": doc.get("code_path", ""),
            "page_sampled": bool(doc.get("truncated")),
        })
    INDEX.write_text(json.dumps(out, separators=(",", ":")))
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
        for sp, g in df.groupby("sport"):
            dep = (g.stake_usd.fillna(0) + g.fee_usd.fillna(0)).sum()
            by_sport.append({"sport": sp, "trades": int(len(g)), "pnl": float(g.pnl_usd.sum()),
                             "roi": float(g.pnl_usd.sum() / dep) if dep else None})
    return {"meta": e["meta"], "by_sport": sorted(by_sport, key=lambda d: -d["trades"]),
            "sports": sorted(df.sport.dropna().unique().tolist()) if "sport" in df else [],
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
        df = df[df.exit_price == 0.5]
    if q:
        ql = q.lower()
        hay = df.get("event", "").astype(str) + " " + df.get("side", "").astype(str) + " " + df.get("note", "").astype(str)
        df = df[hay.str.lower().str.contains(ql, regex=False)]
    return df


@app.get("/api/strategy/{slug}/trades")
def api_trades(slug: str, offset: int = 0, limit: int = Query(100, le=1000), period: str = "all",
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
        "pnl": float(df.pnl_usd.sum()), "roi": float(df.pnl_usd.sum() / dep) if dep else None,
        "wins": int((df.pnl_usd > 0).sum()), "columns": list(page.columns),
        "rows": json.loads(page.to_json(orient="values")),
    })


@app.get("/api/strategy/{slug}/equity")
def api_equity(slug: str, period: str = "all", sport: str = "all", result: str = "all", q: str = "",
               points: int = Query(600, le=2000)):
    e = _load(slug)
    df = _filter(e["df"], period, sport, result, q).sort_values("entry_ts", kind="stable")
    if df.empty:
        return {"points": [], "holdout_at": None}
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
    return {"ok": True, "ledgers": len(list(LEDGERS.glob("*.json"))) - (1 if INDEX.exists() else 0),
            "cached": list(_cache)}


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
