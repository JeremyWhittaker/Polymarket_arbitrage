"""Preview audit (follow mode only): ask the exchange to *preview* each newly executed US entry.

The engine hands a copy of every newly written entry to `PreviewAuditor.submit`. An entry whose
decision time is within 15 s of the wall clock is previewed on a background thread (at most 2
requests per second) through `us_api.preview_buy`, which can only preview, never place, an order.
Anything older (a catch-up replay after downtime) is logged as `not_previewed`. Results go to
`previews.jsonl`, keyed by the decision key, so a restart never previews an entry again. Nothing
here feeds back into decisions, cash or the decision log.

US order prices are always long-side prices (docs.polymarket.us, Orders API overview): a
BUY_SHORT at short limit L is previewed at price 1 - L. `us_api.preview_buy` sends an integer
quantity; a fractional simulated quantity is previewed truncated and flagged.

Status: `accepted` (2xx, order not rejected); `rejected` only when the exchange refused the order
itself (an order state containing REJECT, or a validation-type 4xx with a JSON body); `error` for
everything that says nothing about the order: no response, 401/403/407/408/429 (signature, time
window, edge block, rate limit), 5xx, and non-JSON bodies. `http_status` is kept on every row.
"""
from __future__ import annotations

import logging
import math
import queue
import threading
import time
from pathlib import Path

from ..engine import JsonlLog
from . import spec

log = logging.getLogger("pmsports")
MAX_LAG_MS = int(spec.COMMON["preview_audit"]["max_lag_s"] * 1000)
MIN_INTERVAL_S = 1.0 / spec.COMMON["preview_audit"]["max_per_s"]


def _default_preview(slug, price, qty, intent):
    from .. import us_api
    return us_api.preview_buy(slug, price, qty, intent)


def _v(x):
    return x.get("value") if isinstance(x, dict) else x


def summarize(code: int | None, body) -> dict:
    """The fields of a preview response the audit keeps (the echoed market metadata is dropped)."""
    if not isinstance(body, dict):
        return dict(http_status=code, error=str(body)[:500] if body is not None else None)
    o = body.get("order") if isinstance(body.get("order"), dict) else {}
    out = dict(http_status=code, state=o.get("state"), side=o.get("side"), intent=o.get("intent"),
               price=_v(o.get("price")), quantity=o.get("quantity"), cum_quantity=o.get("cumQuantity"),
               leaves_quantity=o.get("leavesQuantity"), avg_px=_v(o.get("avgPx")),
               commission=_v(o.get("commissionNotionalTotalCollected")),
               commission_bps=o.get("commissionsBasisPoints"), outcome_side=o.get("outcomeSide"),
               reject_reason=o.get("rejectReason") or o.get("ordRejReason") or o.get("text"))
    if not o:
        out.update(error_code=body.get("code"), message=body.get("message"), details=body.get("details"))
    return {k: v for k, v in out.items() if v is not None}


TRANSPORT_4XX = (401, 403, 407, 408, 429)      # auth, edge, timeout, rate limit: not an order rejection


def status_of(code: int | None, summary: dict) -> str:
    """accepted | rejected (the exchange refused the order) | error (transport, auth or server)."""
    if code is None or "error" in summary:            # no response, or a body that is not a JSON object
        return "error"
    if "REJECT" in str(summary.get("state") or "").upper():
        return "rejected"
    if 200 <= code < 300:
        return "accepted"
    return "rejected" if 400 <= code < 500 and code not in TRANSPORT_4XX else "error"


class PreviewAuditor:
    def __init__(self, path: Path, preview_fn=None, clock=time.time, max_lag_ms: int = MAX_LAG_MS,
                 min_interval_s: float = MIN_INTERVAL_S, sleep=time.sleep):
        self.log = JsonlLog(Path(path))
        self.preview_fn, self.clock, self.sleep = preview_fn or _default_preview, clock, sleep
        self.max_lag_ms, self.min_interval_s = max_lag_ms, min_interval_s
        self.q: queue.Queue = queue.Queue()
        self.lock = threading.Lock()
        self.seen = set(self.log.keys)
        self.thread: threading.Thread | None = None
        self.last_call = -math.inf
        self.counts = dict(submitted=0, previewed=0, not_previewed=0, errors=0)

    def _write(self, rec: dict) -> None:
        with self.lock:
            self.log.write(rec)

    def submit(self, rec: dict) -> None:
        key = rec.get("key")
        if key is None or key in self.seen:
            return
        self.seen.add(key)
        self.counts["submitted"] += 1
        now = int(self.clock() * 1000)
        qty = float(rec.get("quantity") or 0)
        base = dict(key=key, policy=rec.get("policy"), game_id=rec.get("game_id"), market_slug=rec.get("market_slug"),
                    intent=rec.get("intent"), limit=rec.get("limit"), price_value=rec.get("order_price_value"),
                    quantity=qty, preview_quantity=int(qty), quantity_truncated=int(qty) != qty,
                    decided_ms=rec.get("decided_ms"), submitted_ms=now,
                    simulated=dict(status=rec.get("status"), reason=rec.get("reason"), shares=rec.get("shares"),
                                   price=rec.get("price"), fee_usd=rec.get("fee_usd"), cost_usd=rec.get("cost_usd")))
        lag = now - int(rec.get("decided_ms") or 0)
        why = ("lag" if lag > self.max_lag_ms else "no_market" if not rec.get("market_slug") else
               "no_price" if rec.get("order_price_value") is None else "no_whole_contract" if int(qty) < 1 else "")
        if why:
            self.counts["not_previewed"] += 1
            return self._write(base | dict(status="not_previewed", why=why, lag_ms=lag))
        if self.thread is None:
            self.thread = threading.Thread(target=self._work, name="us-preview", daemon=True)
            self.thread.start()
        self.q.put(base)

    def _work(self) -> None:
        while True:
            base = self.q.get()
            if base is None:
                return
            wait = self.last_call + self.min_interval_s - self.clock()
            if wait > 0:
                self.sleep(wait)
            t0 = self.clock()
            self.last_call = t0
            code, body = None, None
            try:
                code, body = self.preview_fn(base["market_slug"], float(base["price_value"]), base["preview_quantity"],
                                             base["intent"])
            except Exception as exc:               # network etc.: logged, never retried, never fatal
                body = repr(exc)
            t1 = self.clock()
            summary = summarize(code, body)
            status = status_of(code, summary)
            self.counts["previewed" if status != "error" else "errors"] += 1
            try:
                self._write(base | dict(status=status, requested_ms=int(t0 * 1000), lag_ms=int(t0 * 1000) - base["decided_ms"],
                                        response_ms=int((t1 - t0) * 1000), response=summary))
            except Exception as exc:
                log.warning("preview log write failed for %s: %r", base["key"], exc)

    def close(self, timeout: float = 30.0) -> None:
        if self.thread is not None:
            self.q.put(None)
            self.thread.join(timeout)
        with self.lock:
            self.log.close()
