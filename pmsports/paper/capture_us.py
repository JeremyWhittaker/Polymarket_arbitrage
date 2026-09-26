"""Polymarket US capture for the US-venue paper test (reports/PROSPECTIVE_US_VENUE_PROTOCOL.md).

Runs inside `pmsports record` (record.py) beside the international capture and writes, to
data/live/<UTC day>/ (every line stamped with local receipt time `recv_ms`, never decreasing
per file; record._Sink):

  us_map.jsonl    one record per Polymarket US game in the discovery window, and one per
                  international game with no US market (coverage), written on change and on
                  each UTC-day rollover. Fields:
                    key            us_event_slug, or "intl:<sport>:<international id>" for a
                                   no_us_market record that no US event claims
                    sport          "mlb" | "soccer"
                    us_event_slug, league, us_game_id, us_start_ts   (US event; None when absent)
                    start_ts       admission instant: earliest of the US and international starts
                    intl_key       mlb: {game_pk, condition_id, event_slug}; soccer: {event_slug,
                                   espn_id, series}; None when no international game is linked
                    markets        mlb: {slug, long_team: "home"|"away"|None, tick, min_qty,
                                   qty_increment, fee_coefficient, long_abbr, long_name, short_abbr,
                                   short_name}; soccer: {"home"|"draw"|"away": {slug, tick, min_qty,
                                   qty_increment, fee_coefficient, abbr, name}} (keys "a"/"b"/"draw"
                                   in US slug order when the orientation is not established)
                    teams          {"home": {abbr, name}, "away": {abbr, name}} (US names) or None
                    method         how the international game was found: "slug" | "game_id" | "teams"
                    match          exact | ambiguous | unmatched | no_us_market, with `reason`
                  A game's mapping freezes at `start_ts` like mlb_map: afterwards only the venue
                  fields (tick, min_qty, qty_increment, fee_coefficient) follow the exchange, and a
                  US game first mapped at or after its start is `unmatched` ("first mapped after
                  start"). The admitting record is the latest one per `key` received before
                  `start_ts`. Coverage per international game is the latest record received before
                  its start whose `intl_key` names it (a US record that claims a game previously
                  written as no_us_market is re-written so it is the latest).
  us_book.jsonl   {"recv_ms", "msg"}: every markets-websocket message that is not a trade, raw
                  (MARKET_DATA full-depth snapshots of the LONG side, unsubscribe acks and errors),
                  plus connection markers {"recv_ms", "conn", "n_markets", ...}:
                    open       connection up and every wanted market requested
                    sub/unsub  one per subscription request sent / withdrawn: request_id, type, slugs
                               (the exchange sends no success ack: a subscribed market's full
                               snapshot follows at once, a refused request gets an error)
                    sub_error  the exchange refused that request (request_id, type, slugs, error):
                               none of its markets is streamed, and n_markets no longer counts them
                    heartbeat  every PONG to our text PING (sent every PING_S = 1 s), so a healthy
                               connection is confirmed at least every ~1 s
                    closed     the connection ended for any reason
  us_trade.jsonl  {"recv_ms", "msg"}: TRADE messages, raw.

Universe and matching (protocol "Universe" and its 2026-09-26 mapping addendum): MLB = league
`mlb`; soccer = every league of the US sport `soccer` (from /v2/sports; /v2/leagues filtered to the
soccer sport id, then a fixed core list, as fallbacks). An event is in the window when its US start
is within [now - 3 h, now + 6 h]; each gateway page is parsed as it arrives, so only the parsed
in-window events are held.
Each US event is linked to one international capture game (mlb_map.jsonl + games.jsonl, or
soccer_games.jsonl, records received at or before the decision time) by, in order: the identical
event slug; the identical Polymarket sports gameId (shared by both venues); else both teams by
name/abbreviation with the start within 45 min (MLB) or 30 min (soccer). A slug or gameId link
must still agree on teams and start, else the game is ambiguous. MLB orientation is the long
side's `team.ordering`, which must agree with team identity against the MLB home/away names.
Soccer `marketSides[].team.ordering` is a side position (always "home" on the long side), not the
team's home/away, so soccer orientation comes from team identity against the international
record's ESPN-oriented home/away teams (capture_soccer.team_score thresholds). A game is `exact`
only when the link, the orientation and the international mapping are all exact.

Websocket (one authenticated connection, headers re-signed by us_api.ws_headers() on every
connect). The exchange allows MAX_SUBS = 10 subscriptions per connection, MARKET_DATA and TRADE
combined, and refuses a slug already subscribed on the connection; either refusal rejects the whole
request, a refused request does not count toward the cap, and both limits are per connection
(verified live 2026-09-26). Each batch of newly wanted market slugs gets one MARKET_DATA
subscription (responsesDebounced = DEBOUNCED, i.e. every book change) and one TRADE subscription
per <= 100 slugs, sent on the live connection. When a batch would exceed the cap, every request is
unsubscribed and the merged wanted set re-subscribed in the fewest requests, back to back on the
same connection (accepted live; each market's full snapshot follows at once). Otherwise a
subscription is unsubscribed (checked every 10 minutes) only once none of its slugs is wanted. A
refused request is dropped (sub_error marker) and its slugs re-requested after RETRY_S = 60 s; a cap
or duplicate refusal means the exchange's subscriptions differ from ours, so the connection is
rebuilt. A market is wanted while its game's record is `exact`, until start + 8 h (MLB) / start +
5 h 10 min (soccer), or 10 minutes after discovery first sees the US event ended. A receive that
times out with nothing received for DEAD_S = 3 s (about three missed PONGs) ends the connection,
so a silent drop's `closed` marker lands inside the 5 s book-freshness window. Reconnects back off
exponentially (1 s .. 60 s).

Volume (measured 2026-09-26, live MLB): an undebounced full-depth MARKET_DATA stream is ~4.7
messages/s and ~39 KiB/s per live MLB moneyline (debounced: ~1 message/s, ~8 KiB/s); pregame
markets are far quieter. rotate.py gzips the day files after two days.

Nothing here trades: the only authenticated call is the read-only markets websocket handshake.
Credentials never leave us_api; nothing here logs them.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import math
import time
from collections import Counter, defaultdict
from pathlib import Path

import websockets

from .. import polymarket as pm
from ..collect import DATA_DIR, _team_eq
from ..events import soccer as S
from ..http import get_json
from . import capture_soccer as cs
from . import us_api as U

log = logging.getLogger("pmsports")

DISCOVER_S = 600                  # discovery cadence
BACK_S, AHEAD_S = 3 * 3600, 6 * 3600   # US start within [now - 3 h, now + 6 h]
MLB_TOL_S = 45 * 60               # MLB start agreement between venues
SOCCER_TOL_S = cs.KICKOFF_TOL_S   # soccer kickoff agreement (30 min, as for ESPN matching)
CORE_SOCCER = ("epl", "lal", "bun", "sea", "ucl", "uefa", "mls", "nb1", "flc")
SOCCER_SPORT, SOCCER_SPORT_ID = "soccer", 2
PAGE, MAX_PAGES = 100, 20
MLB_TTL_S = 8 * 3600              # subscription lifetime after start (record.MLB_TOKEN_TTL_S)
SOCCER_TTL_S = cs.KEEP_S + cs.TOKEN_GRACE_S
ENDED_GRACE_S = 600               # ... or 10 min after the US event is first seen ended
KEEP_RECORDS_S = 2 * 86400
MAX_SLUGS = 100                   # exchange limit per subscription
MAX_SUBS = 10                     # exchange limit per connection, MARKET_DATA and TRADE combined
RETRY_S = 60.0                    # a refused request's slugs are re-requested no sooner than this
CAP_ERR, DUP_ERR = "max subscriptions per connection reached", "slug already subscribed"
PING_S = 1.0                      # text PING cadence; every PONG is a heartbeat marker
DEAD_S = 3.0                      # nothing received (not even a PONG) for this long -> reconnect
UNSUB_EVERY_S = 600.0
CHECK_S = 0.5                     # how often the live connection compares wanted vs subscribed markets
RECONNECT_MIN_S, RECONNECT_MAX_S = 1.0, 60.0
STABLE_S = 60.0                   # a connection that lived this long resets the backoff
DEBOUNCED = False                 # full fidelity: every book change (see module docstring for volume)
MD, TR = "SUBSCRIPTION_TYPE_MARKET_DATA", "SUBSCRIPTION_TYPE_TRADE"
MONEYLINE, DRAWABLE = "SPORTS_MARKET_TYPE_MONEYLINE", "SPORTS_MARKET_TYPE_DRAWABLE_OUTCOME"
FIRST_AFTER_START = "first mapped after start"
VENUE_KEYS = ("tick", "min_qty", "qty_increment", "fee_coefficient")


def _f(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _ts(v) -> float | None:
    try:
        return pm.parse_ts(v) if v else None
    except (TypeError, ValueError, IndexError):
        return None


def _days(now: float) -> list[str]:
    return [time.strftime("%Y-%m-%d", time.gmtime(now - d * 86400)) for d in (1, 0)]


def _min(*xs) -> float | None:
    xs = [x for x in xs if x is not None]
    return min(xs) if xs else None


def _dt(s: float | None, starts) -> float | None:
    """Smallest |s - x| over the known international starts."""
    d = [abs(s - x) for x in starts if x is not None] if s is not None else []
    return min(d) if d else None


def _near(g: dict, ig: dict, tol: float) -> bool:
    dt = _dt(g["start_ts"], ig["starts"])
    return dt is not None and dt <= tol


# ------------------------------------------------------------------------------------ US events

def _venue(m: dict) -> dict:
    q = _f(m.get("minimumTradeQty"))
    # The US market schema has no separate quantity increment: minimumTradeQty ("0.01 = 1% of a
    # contract") is both the minimum and the step; book quantities are quoted to 0.01.
    return {"slug": m.get("slug"), "tick": _f(m.get("orderPriceMinTickSize")), "min_qty": q,
            "qty_increment": q, "fee_coefficient": _f(m.get("feeCoefficient"))}


def _side_team(side: dict) -> dict:
    t = side.get("team") or {}
    return {"abbr": str(t.get("abbreviation") or "").lower() or None, "name": t.get("name"),
            "ordering": t.get("ordering")}


def parse_event(e: dict, sport: str, league: str) -> dict:
    """A US event -> {us_event_slug, league, start_ts, game_id, ended, market (mlb) | legs
    (soccer), error}. `error` says why the event has no usable moneyline (no_us_market)."""
    slug = str(e.get("slug") or "")
    g = {"sport": sport, "us_event_slug": slug, "league": league,
         "start_ts": _ts(e.get("startDate")) or _ts(e.get("startTime")), "game_id": e.get("gameId"),
         "ended": bool(e.get("ended")) or bool(e.get("closed")), "market": None, "legs": None, "error": None}
    markets = [m for m in e.get("markets") or [] if isinstance(m, dict)]
    if sport == "mlb":
        ml = [m for m in markets if m.get("sportsMarketTypeV2") == MONEYLINE and m.get("slug") == f"aec-{slug}"]
        if not ml:
            ml = [m for m in markets if m.get("sportsMarketTypeV2") == MONEYLINE
                  and m.get("sportsMarketType") == "baseball_team_full_game_winner"]
        if len(ml) != 1:
            g["error"] = f"{len(ml)} full-game moneyline (aec-) markets"
            return g
        m = ml[0]
        sides = [s for s in m.get("marketSides") or [] if isinstance(s, dict)]
        longs = [s for s in sides if s.get("long") is True]
        shorts = [s for s in sides if s.get("long") is False]
        if len(sides) != 2 or len(longs) != 1 or len(shorts) != 1:
            g["error"] = f"moneyline {m.get('slug')!r} does not have one long and one short side"
            return g
        g["market"] = {**_venue(m), "long": _side_team(longs[0]), "short": _side_team(shorts[0])}
        return g
    pre, legs = f"atc-{slug}-", {}
    for m in markets:
        ms = str(m.get("slug") or "")
        tok = ms[len(pre):] if ms.startswith(pre) else ""
        if m.get("sportsMarketTypeV2") != DRAWABLE or not tok or "-" in tok:
            continue              # other drawable markets (halves etc.) carry extra slug parts
        if tok in legs:
            g["error"] = f"duplicate drawable-outcome leg {tok!r}"
            return g
        longs = [s for s in m.get("marketSides") or [] if isinstance(s, dict) and s.get("long") is True]
        team = _side_team(longs[0]) if len(longs) == 1 else {"abbr": None, "name": None, "ordering": None}
        if tok != "draw" and not team["abbr"]:
            team["abbr"] = tok
        legs[tok] = {**_venue(m), "team": team}
    teams = sorted(t for t in legs if t != "draw")
    if "draw" not in legs or len(teams) != 2:
        g["error"] = f"drawable-outcome (atc-) legs found: {sorted(legs)}"
        return g
    m = S.SLUG_RE.match(slug)
    if m and sorted((m["a"], m["b"])) == teams:
        teams = [m["a"], m["b"]]
    g["legs"] = {"a": legs[teams[0]], "b": legs[teams[1]], "draw": legs["draw"]}
    return g


def event_slugs(g: dict) -> list[str]:
    if g.get("market"):
        return [g["market"]["slug"]]
    return [leg["slug"] for leg in (g.get("legs") or {}).values() if leg and leg.get("slug")]


# ------------------------------------------------------------------------ international capture

def load_intl(root: Path, now: float) -> dict:
    """International capture games from today's and yesterday's files, using only records
    received at or before `now` (latest record per game)."""
    cut = int(now * 1000)
    mlb, gid, soc = {}, {}, {}
    for day in _days(now):
        d = Path(root) / day
        for r in cs._read_jsonl(d / "mlb_map.jsonl"):
            cid = (r.get("pm") or {}).get("condition_id")
            if cid and int(r.get("recv_ms") or 0) <= cut:
                mlb[cid] = r
        for r in cs._read_jsonl(d / "games.jsonl"):
            g = r.get("game") or {}
            if g.get("slug") and int(r.get("recv_ms") or 0) <= cut:
                gid[g["slug"]] = g.get("pm_game_id")
        for r in cs._read_jsonl(d / "soccer_games.jsonl"):
            if r.get("event_slug") and int(r.get("recv_ms") or 0) <= cut:
                soc[r["event_slug"]] = r
    out = {"mlb": [], "soccer": []}
    for cid, r in mlb.items():
        p = r.get("pm") or {}
        starts = [p.get("start_ts"), r.get("mlb_start_ts")]
        out["mlb"].append({"iid": f"mlb:{cid}", "condition_id": cid, "slug": p.get("slug"),
                           "game_pk": r.get("game_pk"), "match": r.get("match"), "reason": r.get("reason"),
                           "home_name": r.get("home_name"), "away_name": r.get("away_name"),
                           "starts": starts, "start": _min(*starts), "game_id": gid.get(p.get("slug"))})
    for slug, r in soc.items():
        e = r.get("espn") or {}
        starts = [r.get("start_ts"), e.get("kickoff_ts")]
        out["soccer"].append({"iid": f"soccer:{slug}", "slug": slug, "series": r.get("series"),
                              "match": r.get("match"), "reason": r.get("reason"), "teams": r.get("teams") or {},
                              "espn": e, "starts": starts, "start": _min(*starts), "game_id": r.get("game_id")})
    return out


def _intl_key(ig: dict, sport: str) -> dict:
    if sport == "mlb":
        return {"game_pk": ig["game_pk"], "condition_id": ig["condition_id"], "event_slug": ig["slug"]}
    return {"event_slug": ig["slug"], "espn_id": (ig.get("espn") or {}).get("id"), "series": ig.get("series")}


def iid_of(r: dict) -> str | None:
    """International game id ("<sport>:<condition id | event slug>") a us_map record names."""
    k = r.get("intl_key") or {}
    x = k.get("condition_id") if r.get("sport") == "mlb" else k.get("event_slug")
    return f"{r.get('sport')}:{x}" if x else None


def _candidates(g: dict, games: list[dict], near, agree) -> tuple[list[dict], str]:
    gid = g.get("game_id")
    for method, pred in (("slug", lambda ig: ig["slug"] == g["us_event_slug"]),
                         ("game_id", lambda ig: gid is not None and ig.get("game_id") is not None
                          and str(ig["game_id"]) == str(gid))):
        c = [ig for ig in games if pred(ig)]
        if c:
            return c, method
    return [ig for ig in games if near(ig) and agree(ig)], "teams"


def _base(g: dict) -> dict:
    return {"key": g["us_event_slug"], "sport": g["sport"], "us_event_slug": g["us_event_slug"],
            "league": g["league"], "us_game_id": g.get("game_id"), "us_start_ts": g["start_ts"],
            "start_ts": g["start_ts"], "intl_key": None, "markets": {}, "teams": None, "method": None,
            "match": "unmatched", "reason": ""}


def _link_problem(rec: dict, g: dict, ig: dict, method: str, tol: float) -> bool:
    """Record the link; True (rec made ambiguous) when the starts of the two venues disagree."""
    rec["method"] = method
    rec["start_ts"] = _min(g["start_ts"], ig["start"])
    if _near(g, ig, tol):
        return False
    dt = _dt(g["start_ts"], ig["starts"])
    rec.update(match="ambiguous", reason=f"{method} link but the start differs from international {ig['slug']} "
               f"by {'?' if dt is None else round(dt / 60)} min (limit {tol / 60:.0f})")
    return True


def _intl_verdict(rec: dict, ig: dict, why: str) -> dict:
    if ig.get("match") == "exact":
        rec.update(match="exact", reason=why)
    else:
        rec.update(match="ambiguous" if ig.get("match") == "ambiguous" else "unmatched",
                   reason=f"international mapping is {ig.get('match')}: {ig.get('reason')}")
    return rec


def _mlb_orient(mk: dict, ig: dict) -> bool | None:
    """True: the US long team is the international (MLB) home team; False: away; None: unclear."""
    h, a = ig.get("home_name") or "", ig.get("away_name") or ""
    ln, sn = mk["long"].get("name") or "", mk["short"].get("name") or ""

    def eq(x, y):
        return bool(x and y) and _team_eq(x, y)
    home, away = eq(h, ln) and eq(a, sn), eq(a, ln) and eq(h, sn)
    return True if home and not away else False if away and not home else None


def map_mlb(g: dict, games: list[dict]) -> dict:
    """us_map record for one parsed US MLB event against the international MLB games."""
    rec = _base(g)
    if g["error"]:
        rec.update(match="no_us_market", reason=g["error"])
        return rec
    mk = g["market"]
    rec["markets"] = {"slug": mk["slug"], "long_team": None, **{k: mk[k] for k in VENUE_KEYS},
                      "long_abbr": mk["long"]["abbr"], "long_name": mk["long"]["name"],
                      "short_abbr": mk["short"]["abbr"], "short_name": mk["short"]["name"]}
    cands, method = _candidates(g, games, lambda ig: _near(g, ig, MLB_TOL_S),
                                lambda ig: _mlb_orient(mk, ig) is not None)
    if not cands:
        rec["reason"] = "no international MLB game with this slug or game id, or with both teams within 45 min"
        return rec
    if len(cands) > 1:
        rec.update(match="ambiguous", reason=f"{len(cands)} international games by {method}: "
                   f"{sorted(str(c['slug']) for c in cands)}")
        return rec
    ig = cands[0]
    rec["intl_key"] = _intl_key(ig, "mlb")
    if _link_problem(rec, g, ig, method, MLB_TOL_S):
        return rec
    o = _mlb_orient(mk, ig)
    if o is None:
        rec.update(match="ambiguous", reason=f"US teams {mk['long']['name']!r}/{mk['short']['name']!r} do not "
                   f"match international home {ig.get('home_name')!r} / away {ig.get('away_name')!r}")
        return rec
    lo, so = mk["long"]["ordering"], mk["short"]["ordering"]
    if {lo, so} != {"home", "away"} or (lo == "home") != o:
        rec.update(match="ambiguous", reason=f"US ordering (long {lo}, short {so}) disagrees with team identity "
                   f"(long {mk['long']['name']!r} is the {'home' if o else 'away'} team)")
        return rec
    rec["markets"]["long_team"] = "home" if o else "away"
    t = {"home": mk["long"] if o else mk["short"], "away": mk["short"] if o else mk["long"]}
    rec["teams"] = {k: {"abbr": v["abbr"], "name": v["name"]} for k, v in t.items()}
    dt = _dt(g["start_ts"], ig["starts"])
    return _intl_verdict(rec, ig, f"{method}; start dt={dt / 60:.0f} min; long {mk['long']['name']} is "
                         f"{rec['markets']['long_team']} (US team ordering, confirmed by team names)")


def _soccer_score(team: dict, ig: dict, side: str) -> float:
    t = (ig.get("teams") or {}).get(side) or {}
    e = ig.get("espn") or {}
    target = {"abbreviation": t.get("abbr") or "", "name": t.get("name") or "",
              "displayName": e.get(f"{side}_name") or ""}
    return cs.team_score({"abbr": team.get("abbr") or "", "name": team.get("name") or ""}, target)


def _soccer_orient(legs: dict, ig: dict) -> tuple[bool, tuple, float]:
    """(US leg 'a' is the international home team, the chosen pair of scores, margin)."""
    a, b = legs["a"]["team"], legs["b"]["team"]
    ah = (_soccer_score(a, ig, "home"), _soccer_score(b, ig, "away"))
    aa = (_soccer_score(a, ig, "away"), _soccer_score(b, ig, "home"))
    a_home = sum(ah) >= sum(aa)
    return a_home, (ah if a_home else aa), abs(sum(ah) - sum(aa))


def _leg(leg: dict) -> dict:
    return {"slug": leg["slug"], **{k: leg[k] for k in VENUE_KEYS},
            "abbr": leg["team"]["abbr"], "name": leg["team"]["name"]}


def map_soccer(g: dict, games: list[dict]) -> dict:
    """us_map record for one parsed US soccer event against the international soccer games."""
    rec = _base(g)
    if g["error"]:
        rec.update(match="no_us_market", reason=g["error"])
        return rec
    legs = g["legs"]
    rec["markets"] = {k: _leg(v) for k, v in legs.items()}            # a / b / draw until oriented
    cands, method = _candidates(g, games, lambda ig: _near(g, ig, SOCCER_TOL_S),
                                lambda ig: min(_soccer_orient(legs, ig)[1]) >= cs.TEAM_MIN)
    if not cands:
        rec["reason"] = "no international soccer game with this slug or game id, or with both teams within 30 min"
        return rec
    if len(cands) > 1:
        rec.update(match="ambiguous", reason=f"{len(cands)} international games by {method}: "
                   f"{sorted(str(c['slug']) for c in cands)}")
        return rec
    ig = cands[0]
    rec["intl_key"] = _intl_key(ig, "soccer")
    if _link_problem(rec, g, ig, method, SOCCER_TOL_S):
        return rec
    a_home, pair, margin = _soccer_orient(legs, ig)
    if min(pair) < cs.TEAM_MIN:
        rec.update(match="ambiguous", reason=f"{method} link but teams do not agree with international "
                   f"{ig['slug']} (best {pair[0]:.2f}/{pair[1]:.2f})")
        return rec
    if margin < cs.ORIENT_MARGIN:
        rec.update(match="ambiguous", reason=f"home/away orientation unclear against {ig['slug']} "
                   f"(margin {margin:.2f})")
        return rec
    home, away = ("a", "b") if a_home else ("b", "a")
    rec["markets"] = {"home": _leg(legs[home]), "draw": _leg(legs["draw"]), "away": _leg(legs[away])}
    rec["teams"] = {k: {"abbr": legs[x]["team"]["abbr"], "name": legs[x]["team"]["name"]}
                    for k, x in (("home", home), ("away", away))}
    dt = _dt(g["start_ts"], ig["starts"])
    return _intl_verdict(rec, ig, f"{method}; kickoff dt={dt / 60:.0f} min; teams={pair[0]:.2f}/{pair[1]:.2f} "
                         f"orientation margin={margin:.2f} (team identity vs international home/away)")


def _no_us(ig: dict, sport: str) -> dict:
    return {"key": f"intl:{ig['iid']}", "sport": sport, "us_event_slug": None, "league": None,
            "us_game_id": None, "us_start_ts": None, "start_ts": ig["start"], "intl_key": _intl_key(ig, sport),
            "markets": {}, "teams": None, "method": None, "match": "no_us_market",
            "reason": "no US event maps to this international game"}


def record_slugs(r: dict) -> list[str]:
    """Market slugs of a us_map record."""
    m = r.get("markets") or {}
    if r.get("sport") == "mlb":
        return [m["slug"]] if m.get("slug") else []
    return [v["slug"] for v in m.values() if isinstance(v, dict) and v.get("slug")]


def _start(r: dict | None) -> float:
    s = (r or {}).get("start_ts")
    return float("inf") if s is None else s


def _frozen(pre: dict, fresh: dict) -> dict:
    """The pre-start record with the exchange's current venue fields."""
    rec = copy.deepcopy(pre)
    pm_, fm = rec.get("markets") or {}, fresh.get("markets") or {}
    pairs = [(pm_, fm)] if rec.get("sport") == "mlb" else \
        [(v, {x.get("slug"): x for x in fm.values() if isinstance(x, dict)}.get(v.get("slug")) or {})
         for v in pm_.values() if isinstance(v, dict)]
    for p, f in pairs:
        if p.get("slug") and p.get("slug") == f.get("slug"):
            for k in VENUE_KEYS:
                if f.get(k) is not None:
                    p[k] = f[k]
    return rec


# ----------------------------------------------------------------------------------- capture

class UsCapture:
    """Discovery (us_map) + one markets websocket (us_book / us_trade). Sinks need
    `.write(obj, recv_ms=None)` (record._Sink). `root` is where this capture's own files live
    (for restart seeding); `intl_root` is the international capture read for matching."""

    def __init__(self, map_sink, book_sink, trade_sink, root: Path | None = None, intl_root: Path | None = None,
                 get=get_json, extra: tuple | list = ()):
        self.map_sink, self.book, self.trade, self.get = map_sink, book_sink, trade_sink, get
        self.root = Path(root) if root is not None else DATA_DIR / "live"
        self.intl_root = Path(intl_root) if intl_root is not None else DATA_DIR / "live"
        self.records: dict[str, dict] = {}     # key -> last written record (no recv_ms)
        self.last_key: dict[str, str] = {}     # key -> its serialization
        self.pre: dict[str, dict] = {}         # key -> latest record written before its start
        self.intl_written: dict[str, str] = {}  # international id -> "none" (no_us_market) | "claimed"
        self.ended_at: dict[str, float] = {}   # market slug -> first time its US event was seen ended
        self.want: dict[str, float] = {}       # market slug -> subscription expiry (event-loop thread)
        self.extra = tuple(extra)              # smoke-test only: always-wanted slugs
        self.last_day: str | None = None
        self.seq = 0                           # subscription request ids, unique per process
        self._warned: dict = {}
        self.mono = time.monotonic

    def _warn(self, key, msg: str, every_s: float = 300.0) -> None:
        now = time.time()
        if now - self._warned.get(key, 0.0) >= every_s:
            if len(self._warned) > 2000:
                self._warned.clear()
            self._warned[key] = now
            log.warning(msg)

    # -- restart ------------------------------------------------------------------------------
    def seed(self, now: float | None = None) -> None:
        """Restore today's and yesterday's us_map records: the last written record per key and,
        as the pre-start mapping, the latest one received before its own start and before the
        start of the one kept so far (as record.seed_mlb_map)."""
        now = time.time() if now is None else now
        latest, keep = {}, {}
        for day in _days(now):
            for r in cs._read_jsonl(self.root / day / "us_map.jsonl"):
                k = r.get("key")
                if not k:
                    continue
                rec = {x: v for x, v in r.items() if x != "recv_ms"}
                latest[k] = rec
                s = min(_start(rec), _start(keep.get(k)))
                if s == float("inf") or int(r.get("recv_ms") or 0) < s * 1000:
                    keep[k] = rec
        self.pre.update(keep)
        for k, rec in latest.items():
            self.records[k] = rec
            self.last_key[k] = json.dumps(rec, sort_keys=True)
            i = iid_of(rec)
            if i:
                self.intl_written[i] = "none" if rec.get("us_event_slug") is None else "claimed"
        self.want = self._want(now)

    # -- discovery ----------------------------------------------------------------------------
    def _pages(self, league: str):
        """One league's events, one gateway page (<= PAGE raw events) at a time."""
        for i in range(MAX_PAGES):
            j = self.get(f"{U.GATEWAY}/v2/leagues/{league}/events", {"limit": PAGE, "offset": i * PAGE},
                         retries=3, timeout=20)
            ev = (j or {}).get("events") or []
            yield [e for e in ev if isinstance(e, dict)]
            if len(ev) < PAGE:
                break

    def _league(self, sport: str, league: str, now: float) -> tuple[list[dict], list[str]]:
        """(parsed events of one league whose US start is in the window, market slugs of its
        ended events). Each page is parsed as it arrives and then dropped, so a pass holds about
        one page of raw events at a time, never every league's (the recorder runs under MemoryMax)."""
        keep, ended = [], []
        for page in self._pages(league):
            for e in page:
                try:
                    g = parse_event(e, sport, league)
                    if g["ended"]:
                        ended += event_slugs(g)
                    st = g["start_ts"]
                    if g["us_event_slug"] and st is not None and -BACK_S <= st - now <= AHEAD_S:
                        keep.append(g)
                except Exception as exc:
                    self._warn(("event", e.get("slug")), f"us discovery: event {e.get('slug')}: {exc}")
        return keep, ended

    def soccer_leagues(self) -> tuple[list[str], bool]:
        """(every US soccer league, complete): /v2/sports, else /v2/leagues by sport id, else the
        core list, which is incomplete (no no_us_market coverage records from such a pass)."""
        try:
            sp = self.get(f"{U.GATEWAY}/v2/sports", None, retries=3, timeout=20)
            ls = [str(x.get("slug")) for s in (sp or {}).get("sports") or [] if s.get("slug") == SOCCER_SPORT
                  for x in s.get("leagues") or [] if x.get("slug")]
            if ls:
                return sorted(set(ls)), True
        except Exception as exc:
            self._warn("sports", f"us discovery: /v2/sports: {exc}")
        try:
            lg = self.get(f"{U.GATEWAY}/v2/leagues", None, retries=3, timeout=20)
            ls = [str(x.get("slug")) for x in (lg or {}).get("leagues") or []
                  if x.get("sportId") == SOCCER_SPORT_ID and x.get("slug")]
            if ls:
                return sorted(set(ls) | set(CORE_SOCCER)), True
        except Exception as exc:
            self._warn("leagues", f"us discovery: /v2/leagues: {exc}")
        return list(CORE_SOCCER), False

    def fetch(self, now: float) -> dict:
        """{sport: {"events": [parsed event in the window], "ended": [market slug], "ok": every
        league fetched, "failed": [...]}}. A league whose fetch fails contributes nothing."""
        out = {}
        for sport in ("mlb", "soccer"):
            leagues, complete = (["mlb"], True) if sport == "mlb" else self.soccer_leagues()
            res = out[sport] = {"events": [], "ended": [], "ok": complete,
                                "failed": [] if complete else ["<league list>"]}
            for lg in leagues:
                try:
                    keep, ended = self._league(sport, lg, now)
                except Exception as exc:
                    res["ok"] = False
                    res["failed"].append(lg)
                    self._warn(("league", lg), f"us discovery: league {lg}: {exc}"[:300])
                    continue
                res["events"] += keep
                res["ended"] += ended
        return out

    def _decide(self, k: str, r: dict, t: float, late_ok: bool = True) -> tuple[dict, bool] | None:
        """(record to hold, frozen). Frozen from the start on; a first mapping at or after the
        start is unmatched (US events) or not written at all (no_us_market records)."""
        pre = self.pre.get(k)
        if pre is not None and (_start(pre) <= t or _start(r) <= t):
            return _frozen(pre, r), True
        if _start(r) <= t:
            if not late_ok:
                return None
            late = copy.deepcopy(r)
            late.update(match="unmatched", reason=FIRST_AFTER_START)
            return late, True
        return r, False

    def discover(self, now: float) -> dict:
        """One discovery pass (blocking; run in a thread). Writes us_map records; returns the
        wanted subscription set. Start checks use the time elapsed since `now`."""
        m0 = self.mono()

        def clock() -> float:
            return now + self.mono() - m0
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        rollover = day != self.last_day
        fetched = self.fetch(now)
        intl = load_intl(self.intl_root, clock())          # international records received so far
        fresh = {}
        for sport, res in fetched.items():
            for s in res["ended"]:
                self.ended_at.setdefault(s, now)
            for g in res["events"]:
                try:
                    r = map_mlb(g, intl["mlb"]) if sport == "mlb" else map_soccer(g, intl["soccer"])
                    fresh[r["key"]] = r
                except Exception as exc:
                    self._warn(("event", g["us_event_slug"]), f"us discovery: event {g['us_event_slug']}: {exc}")
        t = clock()
        out: dict[str, tuple[dict, bool]] = {}
        for k, r in fresh.items():
            out[k] = self._decide(k, r, t)
        claims = defaultdict(list)                          # one US event per international game
        for k, (r, _) in out.items():
            if r["match"] == "exact":
                claims[iid_of(r)].append(k)
        for i, ks in claims.items():
            if len(ks) > 1:
                for k in ks:
                    r, fz = out[k]
                    if not fz:
                        r.update(match="ambiguous", reason=f"international game {i} claimed by {len(ks)} US events: "
                                 f"{sorted(ks)}")
        claimed = {iid_of(r) for r, _ in out.values() if iid_of(r)}
        for sport in ("mlb", "soccer"):
            if not fetched[sport]["ok"]:                    # cannot tell "no US market" from a failed fetch
                continue
            for ig in intl[sport]:
                if ig["iid"] in claimed or ig["start"] is None or not -BACK_S <= ig["start"] - now <= AHEAD_S:
                    continue
                r = _no_us(ig, sport)
                d = self._decide(r["key"], r, t, late_ok=False)
                if d is not None:
                    out[r["key"]] = d
        for k, (r, fz) in out.items():
            if not fz and _start(r) <= clock():             # the start passed during this pass
                d = self._decide(k, r, clock(), late_ok=r["us_event_slug"] is not None)
                if d is None:
                    continue
                r, fz = d
            i = iid_of(r)
            force = rollover or (not fz and r["us_event_slug"] is not None and i is not None
                                 and self.intl_written.get(i) == "none")
            ser = json.dumps(r, sort_keys=True)
            if force or self.last_key.get(k) != ser:
                self.map_sink.write(r)
                self.last_key[k], self.records[k] = ser, copy.deepcopy(r)
                if i:
                    self.intl_written[i] = "none" if r["us_event_slug"] is None else "claimed"
            if not fz:
                self.pre[k] = copy.deepcopy(r)
        self.last_day = day
        self._prune(now)
        n = Counter((r["sport"], r["match"]) for r, _ in out.values())
        log.info("us discovery: %s; failed leagues %s", ", ".join(f"{s} {m} {c}" for (s, m), c in sorted(n.items())),
                 sum(len(x["failed"]) for x in fetched.values()))
        return {"want": self._want(now), "counts": dict(n), "failed": {s: x["failed"] for s, x in fetched.items()}}

    def _prune(self, now: float) -> None:
        cut = now - KEEP_RECORDS_S
        for k in [k for k, r in self.records.items() if _start(r) < cut]:
            self.records.pop(k, None)
            self.last_key.pop(k, None)
        for k in [k for k, r in self.pre.items() if _start(r) < cut]:
            del self.pre[k]
        for s in [s for s, t in self.ended_at.items() if t < cut]:
            del self.ended_at[s]
        if len(self.intl_written) > 5000:
            live = {iid_of(r) for r in self.records.values()}
            for i in [i for i in self.intl_written if i not in live]:
                del self.intl_written[i]

    def _want(self, now: float) -> dict:
        """Market slug -> expiry for every game whose current record is exact."""
        want: dict[str, float] = {}
        for r in list(self.records.values()):
            if r.get("match") != "exact" or r.get("us_event_slug") is None or r.get("start_ts") is None:
                continue
            ttl = MLB_TTL_S if r.get("sport") == "mlb" else SOCCER_TTL_S
            for s in record_slugs(r):
                exp = r["start_ts"] + ttl
                if s in self.ended_at:
                    exp = min(exp, self.ended_at[s] + ENDED_GRACE_S)
                if exp > now:
                    want[s] = max(want.get(s, 0.0), exp)
        for s in self.extra:
            want[s] = float("inf")
        return want

    def apply(self, res: dict, now: float) -> None:
        """Publish a discovery result to the websocket loop (event-loop thread)."""
        self.want = res["want"]
        log.info("us capture: %d markets wanted", len(self.live_slugs(now)))

    def live_slugs(self, now: float) -> set:
        return {s for s, exp in self.want.items() if exp > now}

    async def discover_loop(self, stop: float) -> None:
        while time.time() < stop:
            try:
                res = await asyncio.to_thread(self.discover, time.time())
                self.apply(res, time.time())
            except Exception as exc:
                self._warn("discover", f"us discovery: {exc}", 60)
            await asyncio.sleep(max(0.0, min(DISCOVER_S, stop - time.time())))

    # -- websocket ------------------------------------------------------------------------------
    async def _subscribe(self, ws, conn: dict, slugs, types=(MD, TR)) -> list[tuple[str, str, list]]:
        """`types` subscriptions (MARKET_DATA, then TRADE) for `slugs`, <= MAX_SLUGS per request."""
        out, slugs = [], sorted(slugs)
        for i in range(0, len(slugs), MAX_SLUGS):
            chunk = slugs[i:i + MAX_SLUGS]
            self.seq += 1
            for typ in types:
                rid = f"{'md' if typ == MD else 'tr'}-{self.seq}"
                sub = {"requestId": rid, "subscriptionType": typ, "marketSlugs": chunk}
                if typ == MD:
                    sub["responsesDebounced"] = DEBOUNCED
                await ws.send(json.dumps({"subscribe": sub}))
                conn["groups"][rid] = (typ, frozenset(chunk))
                out.append((rid, typ, chunk))
        return out

    @staticmethod
    def _n(conn: dict) -> int:
        return len({s for typ, ss in conn["groups"].values() if typ == MD for s in ss})

    @staticmethod
    def _have(conn: dict) -> dict:
        """Subscription type -> slugs requested on this connection and not refused."""
        have = {MD: set(), TR: set()}
        for typ, ss in conn["groups"].values():
            have[typ] |= ss
        return have

    def _marker(self, kind: str, conn: dict, rid: str, typ: str, slugs, **extra) -> None:
        self.book.write({"conn": kind, "request_id": rid, "type": typ.replace("SUBSCRIPTION_TYPE_", ""),
                         "slugs": sorted(slugs), **extra, "n_markets": self._n(conn)})

    def _fit(self, conn: dict, slugs: set, now: float) -> set:
        """The slugs one connection can hold (MAX_SUBS // 2 request pairs), earliest expiry first;
        the rest wait RETRY_S."""
        cap = MAX_SUBS // 2 * MAX_SLUGS
        if len(slugs) <= cap:
            return set(slugs)
        order = sorted(slugs, key=lambda s: (self.want.get(s, float("inf")), s))
        for s in order[cap:]:
            conn["retry"][(MD, s)] = conn["retry"][(TR, s)] = now + RETRY_S
        self._warn("cap", f"us ws: {len(slugs)} markets wanted but one connection holds {cap}; "
                   f"{len(slugs) - cap} not subscribed", 60)
        return set(order[:cap])

    async def _add(self, ws, conn: dict, want: set, new: dict, now: float) -> None:
        """Subscribe the missing `new` {type: slugs} on the live connection: as extra requests
        while they fit under MAX_SUBS, else every request is unsubscribed and the merged wanted set
        re-subscribed in the fewest requests, back to back (the exchange refuses a slug still
        subscribed, so unsubscribe first)."""
        need = sum(math.ceil(len(v) / MAX_SLUGS) for v in new.values())
        if len(conn["groups"]) + need <= MAX_SUBS:
            parts = [(new[MD], (MD, TR))] if new[MD] == new[TR] else [(new[MD], (MD,)), (new[TR], (TR,))]
            for slugs, types in parts:
                for rid, typ, chunk in await self._subscribe(ws, conn, slugs, types):
                    self._marker("sub", conn, rid, typ, chunk)
            log.info("us ws: subscribed %d more markets (%d total)", len(new[MD] | new[TR]), self._n(conn))
            return
        have = self._have(conn)
        target = self._fit(conn, (want & (have[MD] | have[TR])) | new[MD] | new[TR], now)
        if target <= have[MD] & have[TR]:                   # nothing would be gained: keep what streams
            return
        n_old = len(conn["groups"])
        for rid, (typ, ss) in list(conn["groups"].items()):
            await ws.send(json.dumps({"unsubscribe": {"requestId": rid}}))
            del conn["groups"][rid]
            self._marker("unsub", conn, rid, typ, ss)
        for rid, typ, chunk in await self._subscribe(ws, conn, target):
            self._marker("sub", conn, rid, typ, chunk)
        log.info("us ws: %d + %d requests exceed %d per connection; re-subscribed %d markets in %d requests",
                 n_old, need, MAX_SUBS, len(target), len(conn["groups"]))

    def _refused(self, conn: dict, data: dict, now: float) -> None:
        """An error reply. A refused request of ours is dropped (its markets are not streamed) and
        its slugs re-requested after RETRY_S; a cap or duplicate refusal means the exchange's
        subscriptions differ from ours, so the connection is rebuilt."""
        rid, err = data.get("requestId"), str(data.get("error"))
        self._warn(("err", str(rid)), f"us ws: {str(data)[:300]}", 60)
        g = conn["groups"].pop(rid, None) if isinstance(rid, str) else None
        if g is None:
            return
        typ, ss = g
        for s in ss:
            conn["retry"][(typ, s)] = now + RETRY_S
        self._marker("sub_error", conn, rid, typ, ss, error=err[:300])
        if CAP_ERR in err or DUP_ERR in err:
            raise ConnectionError(f"subscription {rid} refused ({err[:120]}); resubscribing on a new connection")

    async def _connection(self, stop: float) -> None:
        conn: dict = {"groups": {}, "retry": {}}   # request id -> (type, slugs); (type, slug) -> retry time
        opened = False
        try:
            headers = U.ws_headers()                        # signed afresh for every connection
            async with websockets.connect(U.WS_MARKETS, additional_headers=headers, max_size=2 ** 24,
                                          ping_interval=None) as ws:
                now = time.time()
                subs = await self._subscribe(ws, conn, self._fit(conn, self.live_slugs(now), now))
                self.book.write({"conn": "open", "n_markets": self._n(conn)})
                opened = True
                log.info("us ws: open, %d markets", self._n(conn))
                for rid, typ, chunk in subs:
                    self._marker("sub", conn, rid, typ, chunk)
                next_ping = last_rx = last_unsub = next_check = time.time()
                while time.time() < stop:
                    now = time.time()
                    if now >= next_check:                   # subscription bookkeeping, not per message
                        next_check = now + CHECK_S
                        want = self.live_slugs(now)
                        have = self._have(conn)
                        new = {typ: {s for s in want - have[typ] if conn["retry"].get((typ, s), 0.0) <= now}
                               for typ in (MD, TR)}
                        if new[MD] or new[TR]:
                            await self._add(ws, conn, want, new, now)
                        if now - last_unsub >= UNSUB_EVERY_S:
                            last_unsub = now
                            for rid, (typ, ss) in list(conn["groups"].items()):
                                if not ss & want:            # every market of this request expired
                                    await ws.send(json.dumps({"unsubscribe": {"requestId": rid}}))
                                    del conn["groups"][rid]
                                    self._marker("unsub", conn, rid, typ, ss)
                                    log.info("us ws: unsubscribed %s (%d markets)", rid, len(ss))
                            for s in [s for s, t in conn["retry"].items() if t <= now]:
                                del conn["retry"][s]
                        if not want and not conn["groups"]:
                            log.info("us ws: nothing wanted; closing")
                            return
                    if now >= next_ping:
                        await ws.send("PING")
                        next_ping = now + PING_S
                    try:
                        msg = await asyncio.wait_for(ws.recv(), max(0.05, next_ping - time.time()))
                    except asyncio.TimeoutError:
                        if time.time() - last_rx <= DEAD_S:
                            continue
                        try:                                # after an event-loop stall a message may be waiting
                            msg = await asyncio.wait_for(ws.recv(), 0.1)
                        except asyncio.TimeoutError:
                            raise ConnectionError(f"nothing received for {DEAD_S:.0f}s") from None
                    last_rx = time.time()
                    if msg == "PONG":
                        self.book.write({"conn": "heartbeat", "n_markets": self._n(conn)})
                        continue
                    try:
                        data = json.loads(msg)
                    except (TypeError, ValueError):
                        self._warn("nonjson", f"us ws: non-JSON message {str(msg)[:200]!r}")
                        continue
                    if isinstance(data, dict) and "trade" in data:
                        self.trade.write({"msg": data})
                    else:
                        self.book.write({"msg": data})
                        if isinstance(data, dict) and data.get("error"):
                            self._refused(conn, data, last_rx)
        finally:
            if opened:
                try:
                    self.book.write({"conn": "closed", "n_markets": self._n(conn)})
                except Exception as exc:
                    log.warning("us ws closed marker: %s", exc)

    async def ws_loop(self, stop: float) -> None:
        """One markets connection at a time; reconnects with exponential backoff."""
        backoff = RECONNECT_MIN_S
        while time.time() < stop:
            if not self.live_slugs(time.time()):
                await asyncio.sleep(max(0.0, min(5.0, stop - time.time())))
                continue
            t0 = time.time()
            try:
                await self._connection(stop)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._warn(("ws", type(exc).__name__), f"us ws: {type(exc).__name__}: {str(exc)[:200]}; "
                           f"reconnecting in {backoff:.0f}s", 30)
            if time.time() - t0 >= STABLE_S:
                backoff = RECONNECT_MIN_S
            if time.time() < stop:
                await asyncio.sleep(max(0.0, min(backoff, stop - time.time())))
                backoff = min(backoff * 2, RECONNECT_MAX_S)

    async def run(self, stop: float) -> None:
        """Discovery and websocket loops; never raises (record.py must not crash)."""
        async def guard(name, fn):
            while time.time() < stop:
                try:
                    await fn(stop)
                    return
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.warning("us capture %s loop failed: %s; restarting in 30 s", name, exc)
                    await asyncio.sleep(max(0.0, min(30.0, stop - time.time())))
        await asyncio.gather(guard("discovery", self.discover_loop), guard("ws", self.ws_loop))


# -------------------------------------------------------------------------------- smoke test

def main(argv=None) -> None:
    """Live smoke test into a scratch directory: one discovery pass + N seconds of websocket.

        python -m pmsports.paper.capture_us --root /tmp/us_smoke --seconds 60
    """
    import argparse

    from ..record import _Sink
    ap = argparse.ArgumentParser(description=main.__doc__)
    ap.add_argument("--root", required=True, help="output directory (never data/live)")
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--extra", default="", help="comma-separated market slugs to subscribe as well (volume)")
    a = ap.parse_args(argv)
    root = Path(a.root).resolve()
    if root == (DATA_DIR / "live").resolve() or (DATA_DIR / "live").resolve() in root.parents:
        raise SystemExit("refusing to write a smoke test into data/live")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cap = UsCapture(_Sink("us_map", root), _Sink("us_book", root), _Sink("us_trade", root), root=root,
                    extra=[s for s in a.extra.split(",") if s])
    cap.seed()
    t0 = time.time()
    cap.apply(cap.discover(t0), time.time())
    log.info("discovery took %.1fs", time.time() - t0)
    asyncio.run(cap.ws_loop(time.time() + a.seconds))


if __name__ == "__main__":
    main()
