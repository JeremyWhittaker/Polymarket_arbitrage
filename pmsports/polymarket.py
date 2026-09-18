"""Polymarket public data: game discovery (Gamma), price history (CLOB), trades (Data API).

Endpoints (all public, no auth), verified 2026-09:
  Gamma   GET https://gamma-api.polymarket.com/events/keyset?series_id=..&after_cursor=..
          (plain /events caps offset at ~2000; keyset is the supported deep paginator)
  CLOB    GET https://clob.polymarket.com/prices-history?market=<token>&startTs&endTs&fidelity=1
          (1-minute bars when startTs/endTs are given; interval=max degrades to 10-minute bars)
  Data    GET https://data-api.polymarket.com/trades?market=<conditionId>&start&end&limit=1000
          (taker fills, 1-second timestamps; offset pagination is capped, so we split
          the time window instead of paging)

The old Goldsky orderbook subgraph used by the 2024 code is deprecated since
Polymarket's V2 exchange migration; Goldsky's replacement (edge.goldsky.com) is paid.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Iterator

from .http import get_json

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DATA = "https://data-api.polymarket.com"

SERIES = {"mlb": 3, "nba": 10345, "nfl": 12185, "nhl": 10346}  # from GET /sports


def parse_ts(s: str | None) -> float | None:
    """Gamma mixes '2026-09-18T00:05:00Z' and '2026-09-18 00:05:00+00'."""
    if not s:
        return None
    s = s.strip().replace(" ", "T")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    elif s[-3] in "+-" and s[-3:].lstrip("+-").isdigit():
        s += ":00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def iter_events(series_id: int, closed: bool | None = True, page: int = 100) -> Iterator[dict]:
    params: dict = {"series_id": series_id, "limit": page}
    if closed is not None:
        params["closed"] = str(closed).lower()
    cursor = None
    while True:
        if cursor:
            params["after_cursor"] = cursor
        j = get_json(f"{GAMMA}/events/keyset", params)
        yield from j.get("events", [])
        cursor = j.get("next_cursor")
        if not cursor or not j.get("events"):
            return


def _loads(v):
    return json.loads(v) if isinstance(v, str) else (v or [])


def _norm(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


def _match_outcome(outcome: str, team: str) -> bool:
    o, t = _norm(outcome), _norm(team)
    return bool(o) and (o in t or t in o)


def parse_game_event(e: dict) -> dict | None:
    """Flatten a Gamma sports event to one row keyed on its moneyline market.

    Returns None for events without a two-outcome moneyline market (props-only
    events, futures, etc.).
    """
    markets = e.get("markets") or []
    ml = [m for m in markets if m.get("sportsMarketType") == "moneyline"]
    if not ml:
        ml = [m for m in markets if m.get("slug") == e.get("slug")]
    if not ml:
        return None
    m = ml[0]
    outcomes = _loads(m.get("outcomes"))
    tokens = _loads(m.get("clobTokenIds"))
    prices = [float(p) for p in _loads(m.get("outcomePrices"))]
    if len(outcomes) != 2 or len(tokens) != 2:
        return None

    teams = e.get("teams") or []
    away = next((t["name"] for t in teams if t.get("ordering") == "away"), None)
    home = next((t["name"] for t in teams if t.get("ordering") == "home"), None)
    if not (away and home):
        # Gamma sports ordering is "away" first: "Away vs. Home"
        parts = (e.get("title") or "").split(" vs. ")
        if len(parts) != 2:
            return None
        away, home = parts[0].strip(), parts[1].strip()

    if _match_outcome(outcomes[1], home) or _match_outcome(outcomes[0], away):
        home_idx = 1
    elif _match_outcome(outcomes[0], home) or _match_outcome(outcomes[1], away):
        home_idx = 0
    else:
        return None

    resolved = m.get("umaResolutionStatus") == "resolved" or (
        m.get("closed") and sorted(prices) == [0.0, 1.0]
    )
    home_won = None
    if resolved and sorted(prices) == [0.0, 1.0]:
        home_won = prices[home_idx] == 1.0

    fee = m.get("feeSchedule") or {}
    start = parse_ts(m.get("gameStartTime")) or parse_ts(e.get("startTime"))
    return {
        "event_id": str(e["id"]),
        "slug": e.get("slug"),
        "event_date": e.get("eventDate") or (e.get("slug") or "")[-10:],
        "title": e.get("title"),
        "away_team": away,
        "home_team": home,
        "start_ts": start,
        "finished_ts": parse_ts(e.get("finishedTimestamp")),
        "closed_ts": parse_ts(m.get("closedTime")) or parse_ts(e.get("closedTime")),
        "pm_score": e.get("score"),
        "pm_game_id": e.get("gameId"),
        "condition_id": m.get("conditionId"),
        "home_token": tokens[home_idx],
        "away_token": tokens[1 - home_idx],
        "home_outcome_idx": home_idx,
        "home_won": home_won,
        "volume": float(m.get("volumeNum") or m.get("volume") or 0),
        "fee_type": m.get("feeType"),
        "fee_rate": fee.get("rate") if fee else None,
        "fee_exponent": fee.get("exponent") if fee else None,
        "clear_book_on_start": m.get("clearBookOnStart"),
        "n_markets": len(markets),
    }


def price_history(token_id: str, start_ts: int, end_ts: int, fidelity: int = 1) -> list[dict]:
    """1-minute price samples [{t, p}] for a token. Chunked to 3-day windows."""
    out: list[dict] = []
    chunk = 3 * 86400
    s = int(start_ts)
    while s < end_ts:
        e = min(s + chunk, int(end_ts))
        j = get_json(f"{CLOB}/prices-history",
                     {"market": token_id, "startTs": s, "endTs": e, "fidelity": fidelity})
        out.extend(j.get("history", []))
        s = e
    seen, dedup = set(), []
    for x in sorted(out, key=lambda x: x["t"]):
        if x["t"] not in seen:
            seen.add(x["t"])
            dedup.append(x)
    return dedup


TRADE_FIELDS = ("timestamp", "side", "asset", "outcomeIndex", "outcome", "price", "size",
                "transactionHash", "proxyWallet")


def trades(condition_id: str, start_ts: int, end_ts: int, *, limit: int = 1000) -> list[dict]:
    """All taker fills for a market in [start_ts, end_ts], by recursive window splitting.

    The Data API returns the newest `limit` fills in a window; when a window is
    full we bisect it, so we never rely on (capped) offset paging.
    """
    out: list[dict] = []
    stack = [(int(start_ts), int(end_ts))]
    while stack:
        s, e = stack.pop()
        j = get_json(f"{DATA}/trades",
                     {"market": condition_id, "start": s, "end": e, "limit": limit})
        if len(j) >= limit and e - s > 1:
            mid = (s + e) // 2
            stack.append((s, mid))
            stack.append((mid + 1, e))
            continue
        out.extend({k: t.get(k) for k in TRADE_FIELDS} for t in j)
    # a fill can straddle nothing (windows are disjoint) but dedupe defensively
    seen, dedup = set(), []
    for t in sorted(out, key=lambda t: (t["timestamp"], t["transactionHash"] or "", t["asset"] or "")):
        key = (t["transactionHash"], t["asset"], t["side"], t["price"], t["size"], t["proxyWallet"])
        if key not in seen:
            seen.add(key)
            dedup.append(t)
    return dedup


def taker_fee(shares: float, price: float, rate: float = 0.05, exponent: float = 1.0) -> float:
    """USDC taker fee. Polymarket: fee = C * rate * (p*(1-p))^exponent, sports rate=0.05.

    Docs state exponent 1 as `C*rate*p*(1-p)`; sports feeSchedule reports exponent=1.
    Makers pay nothing (and receive a share of taker fees as rebates).
    """
    return shares * rate * (price * (1 - price)) ** exponent
