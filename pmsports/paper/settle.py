"""Poll Gamma for resolved paper markets; cache verified token payouts.

A payout is accepted only through h_live_execution.terminal_payouts (closed and UMA-resolved)
and the validity gate: every token pays 0, 0.5 or 1 and the market's payouts sum to 1. Anything
else stays unresolved (re-polled next time); positions stay open until then.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from ..http import get_json
from ..polymarket import GAMMA
from ..research.h_live_execution import terminal_payouts
from .engine import LIVE, PAPER, atomic_write, capture_rows, load_json, mlb_map_key, mlb_map_start, pregame_latest, read_jsonl

log = logging.getLogger("pmsports")
POST_START_S = 3 * 3600          # poll eligible MLB games for outcome follow-up after this


def valid_payouts(payouts: dict) -> bool:
    vals = list(payouts.values())
    return len(vals) >= 2 and all(v in (0.0, 0.5, 1.0) for v in vals) and abs(sum(vals) - 1) < 1e-9


def _gamma(cid: str) -> list[dict]:
    return get_json(f"{GAMMA}/markets", {"condition_ids": cid, "closed": "true"}) or []


def settle(out: Path = PAPER, live: Path = LIVE, fetch=_gamma, universe: bool = True, now: float | None = None) -> dict:
    out, now = Path(out), time.time() if now is None else now
    path = out / "settlements.json"
    cache = load_json(path, {}) or {}
    cids = {str(d["condition_id"]) for d in read_jsonl(out / "decisions.jsonl")
            if d.get("condition_id") and (d.get("shares") or 0) > 0}
    if universe:        # MLB endpoint counts eligible (pregame exact) games with complete outcome follow-up
        pre, _ = pregame_latest(capture_rows(live, "mlb_map"), mlb_map_key, mlb_map_start)
        for r in pre.values():
            pm, start = r.get("pm") or {}, mlb_map_start(r)
            if r.get("match") == "exact" and pm.get("condition_id") and start < now - POST_START_S:
                cids.add(str(pm["condition_id"]))
    todo = sorted(c for c in cids if (cache.get(c) or {}).get("status") != "resolved")
    n_new = 0
    for cid in todo:
        try:
            markets = fetch(cid)
        except Exception as exc:          # transient; retried on the next settle
            log.warning("gamma %s: %s", cid, exc)
            continue
        exact = [m for m in markets if m.get("conditionId") == cid and m.get("closed")]
        payouts = terminal_payouts(exact[0]) if len(exact) == 1 else {}
        if not payouts:
            continue
        ok = valid_payouts(payouts)
        cache[cid] = dict(status="resolved" if ok else "invalid_payout", payouts=payouts,
                          closed_time=exact[0].get("closedTime"), checked_ts=int(now))
        n_new += ok
    out.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(cache, sort_keys=True, indent=1))
    summary = dict(polled=len(todo), newly_resolved=n_new, resolved=sum(v.get("status") == "resolved" for v in cache.values()))
    log.info("settle: %s", summary)
    return summary
