"""US settlement: the US market's published settlement price, cached per market slug.

`GET gateway.polymarket.us/v1/markets/<slug>/settlement` returns {"slug", "settlement": x} once the
market settles (404 "Settlement not found" before). x is the LONG side's settlement price
(verified 2026-09-26 against three resolved MLB moneylines and the international resolutions); a
short position receives 1 - x. Any value in [0, 1] is accepted (postponed games may settle at a
fair price); values other than 0, 0.5 and 1 are flagged `binary: false`.

Fallback (flagged, provisional): a filled position whose US market has no published settlement yet,
but whose international market is resolved in the v1 engine's settlements, gets the international
payout of the same team with `status: "fallback_intl"`. The US endpoint keeps being polled and
replaces a fallback as soon as it publishes. The report shows fallback positions as open, with
their provisional cash in a separate table; they never enter a statistic, the endpoint or the gate.

Polling: every unresolved slug keeps `first_polled_ts`, `last_polled_ts` and `n_polls` (status
`pending` while unpublished). A pass polls the due slugs least recently polled first, so an HTTP
429 that ends a pass early never starves the same slugs twice. A slug is due every pass for 12 h
after its first poll, hourly until 3 days, daily until 30 days; then it is flagged `unpublished`
and only a filled position's market is still polled, weekly. The cache is written at the end of
every pass, including one cut short by a 429.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import requests

from ..engine import LIVE, PAPER, atomic_write, capture_rows, is_pregame, load_json, read_jsonl
from .engine import PAPER_US
from .venue import before_play, in_play_marks, normalize_markets, sport_of, start_s

log = logging.getLogger("pmsports")
GATEWAY = "https://gateway.polymarket.us"
POST_START_S = 3 * 3600          # poll eligible MLB markets for the endpoint count after this
FILLED_AFTER_S = 2 * 3600        # a filled position's market cannot settle before its game ends
PAUSE_S = 0.5                    # the gateway's edge answers bursts with HTTP 429 well below 20 requests/s
BACKOFF = ((12 * 3600, 0), (3 * 86400, 3600), (30 * 86400, 86400))   # (age since first poll below, min interval)
GIVE_UP_S = BACKOFF[-1][0]       # then `unpublished`: universe-only slugs stop, filled ones go weekly
FILLED_EVERY_S = 7 * 86400
POLL_KEYS = ("first_polled_ts", "last_polled_ts", "n_polls")


def _num(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and abs(x) != float("inf") else None


def fetch_settlement(slug: str) -> dict | None:
    """The published settlement, or None while the market has none."""
    r = requests.get(f"{GATEWAY}/v1/markets/{slug}/settlement", timeout=20)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def us_universe(live: Path, in_play: dict | None = None) -> dict[str, dict]:
    """Latest pregame us_map record per US event (exact or not). With the engine's in-play marks
    (venue.in_play_marks), a record received after its international game was in play is not
    pregame, exactly as the engine admits."""
    pre = {}
    for r in capture_rows(live, "us_map"):
        slug = r.get("us_event_slug") or r.get("event_slug")
        ms = int(r.get("recv_ms") or 0)
        if slug and is_pregame(ms, start_s(r)) and (not in_play or before_play(r, ms, in_play)):
            pre[str(slug)] = r
    return pre


def _meta(e: dict | None) -> dict:
    return {k: e[k] for k in POLL_KEYS if k in (e or {})}


def _age(e: dict, now: float) -> float | None:
    first = _num(e.get("first_polled_ts"))
    return None if first is None else now - first


def due(e: dict | None, now: float, filled: bool) -> bool:
    """Is an unresolved slug due for a poll (backoff by time since its first poll)?"""
    e = e or {}
    last, age = _num(e.get("last_polled_ts")), _age(e, now)
    if last is None or age is None:
        return True
    for below, every in BACKOFF:
        if age < below:
            return now - last >= every
    return filled and now - last >= FILLED_EVERY_S


def _polled(e: dict | None, now: float) -> dict:
    m = _meta(e)
    return dict(first_polled_ts=int(m.get("first_polled_ts", now)), last_polled_ts=int(now), n_polls=int(m.get("n_polls", 0)) + 1)


def _unresolved(e: dict | None, now: float) -> str:
    """Status of a slug whose US settlement is still unpublished."""
    e = e or {}
    if e.get("status") in ("fallback_intl", "invalid_settlement"):
        return e["status"]
    age = _age(e, now)
    return "unpublished" if age is not None and age >= GIVE_UP_S else "pending"


def settle(out: Path = PAPER_US, live: Path = LIVE, fetch=fetch_settlement, intl: Path | None = PAPER / "settlements.json",
           universe: bool = True, now: float | None = None, pause: float = PAUSE_S) -> dict:
    out, now = Path(out), time.time() if now is None else now
    path = out / "settlements.json"
    cache = load_json(path, {}) or {}
    filled = [d for d in read_jsonl(out / "decisions.jsonl") if d.get("market_slug") and (d.get("shares") or 0) > 0]
    slugs = {str(d["market_slug"]) for d in filled
             if (_num(d.get("scheduled_start_ts")) or _num(d.get("us_start_ts")) or 0) < now - FILLED_AFTER_S}
    if universe:
        marks = in_play_marks(read_jsonl(out / "coverage.jsonl"))
        for r in us_universe(live, marks).values():
            if sport_of(r) == "mlb" and r.get("match") == "exact" and (start_s(r) or now) < now - POST_START_S:
                slugs |= {leg.slug for leg in normalize_markets(r).values()}
    held = {str(d["market_slug"]) for d in filled}
    open_ = [s for s in slugs if (cache.get(s) or {}).get("status") != "resolved"]
    for slug in open_:                       # flag markets past the polling horizon
        if slug in cache and cache[slug].get("status") in ("pending", "unpublished"):
            cache[slug]["status"] = _unresolved(cache[slug], now)
    # least recently polled first (never polled first of all), so a pass cut short by 429 moves on
    todo = sorted((s for s in open_ if due(cache.get(s), now, s in held)),
                  key=lambda s: (_num((cache.get(s) or {}).get("last_polled_ts")) or float("-inf"), s))
    n_new = n_polled = 0
    limited = False
    for slug in todo:
        e, err = cache.get(slug) or {}, None
        try:
            j = fetch(slug)
        except Exception as exc:             # transient; retried when next due
            log.warning("US settlement %s: %s", slug, exc)
            if getattr(getattr(exc, "response", None), "status_code", None) == 429:
                limited = True
                break                        # rate limited: stop this pass; the unpolled slugs lead the next one
            j, err = None, f"{type(exc).__name__}: {exc}"[:200]
        finally:
            if pause:
                time.sleep(pause)
        n_polled += 1
        meta = _polled(e, now)
        x = _num((j or {}).get("settlement")) if isinstance(j, dict) and str(j.get("slug") or slug) == slug else None
        if x is None:                        # unpublished (404) or a failed request
            rec = {k: v for k, v in e.items() if k != "error"} | meta
            cache[slug] = rec | dict(status=_unresolved(rec, now)) | (dict(error=err) if err else {})
        elif 0 <= x <= 1:
            cache[slug] = meta | dict(status="resolved", settlement=x, source="us_settlement", binary=x in (0.0, 0.5, 1.0),
                                      checked_ts=int(now))
            n_new += 1
        else:
            cache[slug] = meta | dict(status="invalid_settlement", raw=j, checked_ts=int(now))
    # flagged fallback from the international resolution of the same team
    intl_cache = (load_json(intl, {}) or {}) if intl else {}
    fb = set()
    for d in filled:
        slug = str(d["market_slug"])
        if (cache.get(slug) or {}).get("status") == "resolved":
            continue
        s = intl_cache.get(str(d.get("intl_condition_id"))) or {}
        y = (s.get("payouts") or {}).get(str(d.get("intl_token"))) if s.get("status") == "resolved" else None
        if y is None or d.get("us_side") not in ("long", "short"):
            continue
        cache[slug] = _meta(cache.get(slug)) | dict(
            status="fallback_intl", settlement=float(y) if d["us_side"] == "long" else 1 - float(y),
            source="intl_resolution", intl_condition_id=d.get("intl_condition_id"), checked_ts=int(now))
        fb.add(slug)
    out.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(cache, sort_keys=True, indent=1))
    summary = dict(due=len(todo), polled=n_polled, rate_limited=limited, newly_resolved=n_new, fallback=len(fb),
                   resolved=sum(v.get("status") == "resolved" for v in cache.values()),
                   unpublished=sum(v.get("status") == "unpublished" for v in cache.values()))
    log.info("US settle: %s", summary)
    return summary
