"""Turn the trade ledgers into the data files for the trade-explorer page.

python -m pmsports.research.build_explorer
  data/research/ledgers/*.json  ->  artifact/index.js + artifact/ledgers/<slug>.js

The page loads same-origin JS (no fetch), one file per strategy, on demand.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEDGERS = ROOT / "data" / "research" / "ledgers"
OUT = ROOT / "artifact"
MAX_ROWS = 2500          # per strategy, in the page (full ledgers stay in data/research/ledgers)
NOTE_MAX = 160           # characters of per-trade note kept in the page
GROUP_ORDER = ["MLB studies", "All-sports studies", "Thorp hypotheses"]
log = logging.getLogger("pmsports")

# column index -> rounding for the page
ROUND = {"entry_price": 4, "stake_usd": 2, "fee_usd": 4, "exit_price": 4, "payout": 2, "pnl_usd": 2, "roi": 4}


def _trim(doc: dict) -> tuple[list, bool]:
    cols = doc["columns"]
    rows = doc["rows"]
    i_period = cols.index("period")
    trimmed = False
    if len(rows) > MAX_ROWS:
        trimmed = True
        hold = [r for r in rows if r[i_period] == "holdout"]
        dev = [r for r in rows if r[i_period] != "holdout"]
        # thin whichever period is oversized; a strategy with only one period still gets capped
        share = MAX_ROWS // 2 if (hold and dev) else MAX_ROWS
        if len(hold) > share:
            hold = hold[:: max(1, len(hold) // share)][:share]
        keep = max(MAX_ROWS - len(hold), 400)
        if len(dev) > keep:
            dev = dev[:: max(1, len(dev) // keep)][:keep]
        rows = sorted(dev + hold, key=lambda r: r[cols.index("entry_ts")])
    idx = {c: cols.index(c) for c in ROUND if c in cols}
    i_note = cols.index("note") if "note" in cols else None
    i_event = cols.index("event") if "event" in cols else None
    i_market = cols.index("market") if "market" in cols else None
    out = []
    for r in rows:
        r = list(r)
        if i_note is not None and isinstance(r[i_note], str) and len(r[i_note]) > NOTE_MAX:
            r[i_note] = r[i_note][:NOTE_MAX - 1] + "\u2026"
        if i_market is not None and i_event is not None and r[i_market] == r[i_event]:
            r[i_market] = ""                                # same as event: don't ship it twice
        for c, nd in ROUND.items():
            j = idx.get(c)
            if j is not None and isinstance(r[j], (int, float)):
                r[j] = round(float(r[j]), nd)
        out.append(r)
    return out, trimmed


def build() -> None:
    (OUT / "ledgers").mkdir(parents=True, exist_ok=True)
    index = []
    for f in sorted(LEDGERS.glob("*.json")):
        doc = json.loads(f.read_text())
        rows, trimmed_here = _trim(doc)
        slug = doc["slug"]
        page_doc = {k: v for k, v in doc.items() if k != "rows"}
        page_doc["rows"] = rows
        page_doc["page_sampled"] = bool(trimmed_here or doc.get("truncated"))
        (OUT / "ledgers" / f"{slug}.js").write_text(
            "PM.receive(" + json.dumps(page_doc, separators=(",", ":")) + ");")
        index.append({
            "slug": slug, "title": doc.get("title", slug), "group": doc.get("group", "Thorp hypotheses"),
            "sport": doc.get("sport", ""), "verdict": doc.get("verdict", ""),
            "hypothesis": doc.get("hypothesis", ""), "headline": doc.get("headline", {}),
            "n_total_trades": doc.get("n_total_trades", len(doc["rows"])), "rows_in_page": len(rows),
            "report_path": doc.get("report_path", ""), "code_path": doc.get("code_path", ""),
        })
    index.sort(key=lambda d: (GROUP_ORDER.index(d["group"]) if d["group"] in GROUP_ORDER else 9, d["title"]))
    (OUT / "index.js").write_text("PM.index = " + json.dumps(index, separators=(",", ":")) + ";")
    total = sum((OUT / "ledgers" / f"{d['slug']}.js").stat().st_size for d in index)
    log.info("%d strategies, %.1f MB of data", len(index), total / 1e6)
    print(f"{len(index)} strategies, {total/1e6:.1f} MB")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    build()
