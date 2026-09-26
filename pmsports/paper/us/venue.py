"""Polymarket US venue state for the US paper engine, as received (recv_ms order).

- `us_map`: pregame admission. Per US event, only the latest record received at or before its own
  start counts, and it must be `exact`. A record received after the international game it names was
  seen in play is late too, whatever its listed start (an early start, a doubleheader game 2): for
  MLB that is v1's own mlb_map guard (a half-inning signal of the game was received, mlb.py), for
  soccer an ESPN response with state `in` or `post`. Later records never admit, re-orient or drop a
  game.
- `us_book`: MARKET_DATA messages are full snapshots of the LONG side (bids/offers). Each market
  keeps two crossing views in one ReceivedBook: `<slug>|long` (asks = offers) and `<slug>|short`
  (asks = 1 - long bids, bids = 1 - offers), so BUY_SHORT crosses long bids descending at 1 - bid.
  Snapshots are applied lazily (the latest one before an execution), which is identical for the
  crossing because every snapshot replaces the whole book; per-policy consumption (ReceivedBook's
  `used`) persists while a level stays in the applied snapshots. Connection markers
  (`open`/`heartbeat`/`closed`) come from the same stream.
- `us_trade`: last trade per market (the reference). A short-side reference is 1 - long price.
- `mlb_map` / `soccer_games`: the international token -> (game, side) index, from pregame records
  only (as the frozen strategies admit games), used to answer the strategies' reference lookups.
- `espn`: soccer in-play instants (above). MLB in-play instants come from the engine (`mark_in_play`
  after the frozen MLB strategy has labelled a half-inning of the game). Each game's first in-play
  instant is also written to coverage.jsonl as `inplay|<kind>|<id>` so the report applies the same
  admission.

Freshness at entry time t: a book message for the market received in [t - 5 s, t], after the last
`open` of its connection, with no `closed` marker at or after that message. Days without any
connection marker give connection_ok="unknown" (shakedown-only).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime

from ...book_replay import ReceivedBook
from ..engine import EPS, PRUNE_MS, REF_MS, STALE_OFF, Trade, _num, is_pregame, mlb_map_start
from ..soccer import kickoff
from . import spec

FRESH_MS = int(spec.COMMON["freshness_ms"])
IN_PLAY = "inplay"                                     # coverage key prefix of a game's first in-play instant
ESPN_IN_PLAY = ("in", "post")
SIDES = ("home", "away", "draw")
OPEN_STATES = ("MARKET_STATE_OPEN",)
_SLUG = ("slug", "market_slug", "marketSlug", "us_market_slug")
_TICK = ("tick", "orderPriceMinTickSize", "order_price_min_tick_size", "min_tick", "tick_size")
_MIN = ("min_qty", "minimumTradeQty", "minimum_trade_qty", "min_size", "min_quantity")
_INC = ("qty_increment", "quantity_increment", "qty_step", "increment", "lot_size")
_FEE = ("fee_coefficient", "feeCoefficient", "fee_rate")


def px(v) -> float | None:
    """A US price/quantity field: {"value": "0.85"}, "0.85" or 0.85."""
    if isinstance(v, dict):
        v = v.get("value")
    return _num(v)


def _first(d: dict, keys):
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            return d[k]
    return None


def _side(x) -> str | None:
    s = str(x or "").strip().lower()
    return "draw" if s in ("tie", "draw") else s if s in SIDES else None


def _long_flag(m: dict) -> bool | None:
    """Does this entry trade its outcome as the long side? (explicit flags only)"""
    for k in ("long", "is_long"):
        if isinstance(m.get(k), bool):
            return m[k]
    s = str(m.get("us_side") or m.get("position") or m.get("intent") or "").upper()
    if s.endswith("LONG"):
        return True
    if s.endswith("SHORT"):
        return False
    return None


def _long_side(m: dict) -> str | None:
    """Which intl side (home/away/draw) is this instrument's long side, when the entry says so."""
    for k in ("long_team", "long_side", "long_team_side", "long_ordering"):
        if _side(m.get(k)):
            return _side(m.get(k))
    lg = m.get("long")
    if isinstance(lg, dict):
        s = _side(lg.get("side") or lg.get("ordering") or (lg.get("team") or {}).get("ordering"))
        if s:
            return s
    for s in m.get("sides") or m.get("marketSides") or ():
        if isinstance(s, dict) and s.get("long") is True:
            side = _side(s.get("side") or s.get("ordering") or (s.get("team") or {}).get("ordering"))
            if side:
                return side
    return None


@dataclass(frozen=True)
class Leg:
    """How to buy one intl side on the US venue."""
    slug: str
    long: bool
    tick: float | None = None
    min_qty: float | None = None
    qty_increment: float | None = None
    fee_coefficient: float | None = None

    @property
    def us_side(self) -> str:
        return "long" if self.long else "short"

    @property
    def intent(self) -> str:
        return "ORDER_INTENT_BUY_LONG" if self.long else "ORDER_INTENT_BUY_SHORT"


def normalize_markets(rec: dict) -> dict[str, Leg]:
    """us_map `markets` -> {intl side: Leg}. Accepted shapes (see tests):

    - the capture's MLB shape, one instrument: {slug, long_team: "home"|"away", tick, min_qty,
      qty_increment, fee_coefficient, ...} -> the long team buys long, the other team buys short;
    - the capture's soccer shape: {"home"|"draw"|"away": {slug, tick, ...}}, each atc- market's
      long side being its own outcome (legs keyed "a"/"b" are not oriented and are dropped);
    - entries keyed by side with an explicit long flag / intent, or a list of entries naming
      their side or the instrument's long side.
    An entry whose orientation cannot be read is dropped (the side then has no US leg)."""
    out: dict[str, Leg] = {}
    for key, m, slug, rules in _entries(rec):
        side = _side(key) or _side(m.get("side") or m.get("outcome") or m.get("intl_side"))
        flag, ls = _long_flag(m), _long_side(m)
        if side is not None:
            if flag is None and ls is not None:
                flag = ls == side
            if flag is None and (str(slug).startswith("atc-") or sport_of(rec) == "soccer"):
                flag = True                       # each soccer atc- market's long side is its own outcome
            if flag is not None:
                out[side] = Leg(str(slug), bool(flag), **rules)
        elif ls in ("home", "away"):              # one MLB instrument: long team + the other team short
            out[ls] = Leg(str(slug), True, **rules)
            out["away" if ls == "home" else "home"] = Leg(str(slug), False, **rules)
    return out


def _entries(rec: dict):
    """(key, entry, market slug, venue rules) for every market entry of a us_map record."""
    markets = rec.get("markets")
    if isinstance(markets, dict) and _first(markets, _SLUG) is not None:
        markets = [markets]                       # a single instrument
    items = list(markets.items()) if isinstance(markets, dict) else \
        [(None, m) for m in markets] if isinstance(markets, list) else []
    for key, m in items:
        if isinstance(m, str):
            m = {"slug": m}
        if not isinstance(m, dict):
            continue
        slug = _first(m, _SLUG) or (key if isinstance(key, str) and key.startswith(("aec-", "atc-")) else None)
        if not slug:
            continue
        rules = {f: _num(_first(m, ks) if _first(m, ks) is not None else _first(rec, ks))
                 for f, ks in (("tick", _TICK), ("min_qty", _MIN), ("qty_increment", _INC), ("fee_coefficient", _FEE))}
        yield key, m, str(slug), rules


def market_rules(rec: dict) -> dict[str, dict]:
    """Market slug -> the venue fields a us_map record carries (oriented or not)."""
    return {slug: {k: v for k, v in rules.items() if v is not None} for _, _, slug, rules in _entries(rec)}


def start_s(rec: dict):
    """A us_map record's scheduled start in epoch seconds (numeric or ISO-8601 `start_ts`)."""
    for k in ("start_ts", "startDate", "start_time", "startTime"):
        v = rec.get(k)
        x = _num(v)
        if x is not None:
            return x / 1000 if x > 1e11 else x
        if isinstance(v, str) and v:
            try:
                return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
            except ValueError:
                continue
    return None


def sport_of(rec: dict) -> str | None:
    s = str(rec.get("sport") or "").lower()
    return "mlb" if s in ("mlb", "baseball") else "soccer" if s in ("soccer", "football") else s or None


def intl_keys(rec: dict) -> list[tuple[str, str]]:
    """Identity keys of the international game a us_map record names (`intl_key`, or top-level fields)."""
    k = rec.get("intl_key")
    if isinstance(k, (int, float)) and not isinstance(k, bool):
        k = {"game_pk": k}
    elif isinstance(k, str):
        k = {"game_pk": k} if k.isdigit() else {"slug": k}
    elif not isinstance(k, dict):
        k = {}
    k = {**{f: rec[f] for f in ("game_pk", "intl_event_slug", "intl_condition_id") if rec.get(f) is not None}, **k}
    keys = []
    for f in ("game_pk", "mlb_game_pk", "gamePk"):
        if k.get(f) is not None:
            try:
                keys.append(("mlb", str(int(k[f]))))
            except (TypeError, ValueError):
                pass
    for f in ("condition_id", "conditionId", "intl_condition_id"):
        if k.get(f):
            keys.append(("cid", str(k[f])))
    for f in ("event_slug", "slug", "intl_event_slug", "intl_slug", "pm_slug"):
        if k.get(f):
            keys.append(("slug", str(k[f])))
    return list(dict.fromkeys(keys))


def in_play_marks(coverage_rows) -> dict[tuple[str, str], int]:
    """intl key -> first in-play recv_ms, from the engine's coverage.jsonl rows."""
    out: dict[tuple[str, str], int] = {}
    for r in coverage_rows:
        parts = str(r.get("key") or "").split("|", 2)
        if len(parts) == 3 and parts[0] == IN_PLAY:
            out.setdefault((parts[1], parts[2]), int(r.get("recv_ms") or 0))
    return out


def before_play(rec: dict, recv_ms: int, in_play: dict) -> bool:
    """No international game this us_map record names was in play before recv_ms (with equal
    recv_ms the metadata record is applied first, as the replay ranks streams)."""
    return not any(_first_ms(in_play.get(k)) < recv_ms for k in intl_keys(rec))


def _first_ms(v) -> float:
    return math.inf if v is None else v[0] if isinstance(v, list) else v


class Venue:
    """Received US-venue state. Methods are called with non-decreasing recv_ms."""

    streams = ("us_map", "us_book", "us_trade", "mlb_map", "soccer_games", "espn")

    def __init__(self, cover=None):
        self.cover = cover or (lambda key, ms: None)
        # admission
        self.pre: dict[str, dict] = {}                  # record key -> latest pregame us_map record
        self.by_key: dict[tuple, set] = {}              # intl key -> record keys (pregame records)
        self.rules: dict[str, dict] = {}                # market slug -> venue fields of the latest us_map record
        self.late_keys: dict[tuple, int] = {}           # intl key -> recv_ms of a record received after the start
        self.late: dict[str, int] = {}
        # intl side index (pregame mapping records, as the frozen strategies admit games)
        self.tokens: dict[str, tuple[str, str, str]] = {}   # intl token -> (sport, game_id, side)
        self.games: dict[tuple, dict] = {}              # (sport, game_id) -> {keys, ms[, espn_id]}
        self.espn_slug: dict[str, str] = {}             # ESPN id -> soccer event slug (pregame records)
        self.in_play: dict[tuple, list] = {}            # intl key -> [first, last] recv_ms seen in play
        # books, trades, connection
        self.latest: dict[str, tuple] = {}              # slug -> (recv_ms, seq, marketData)
        self.applied: dict[str, int] = {}               # slug -> seq applied to its ReceivedBook
        self.books: dict[str, ReceivedBook] = {}
        self.book_ms: dict[str, int] = {}
        self.book_conn: dict[str, str] = {}
        self.market_state: dict[str, str | None] = {}
        self.trades: dict[str, list] = {}               # slug -> [latest, latest with a smaller recv_ms]
        self.markers = False
        self.open_ms: dict[str, int] = {}
        self.closed_ms: dict[str, int] = {}
        self.activity_ms: dict[str, int] = {}
        self.seq = 0

    # ------------------------------------------------------------------ records
    def on_record(self, stream: str, rec: dict, ms: int) -> None:
        if stream == "us_book":
            return self.book_record(rec, ms)
        if stream == "us_trade":
            return self.trade_record(rec, ms)
        if stream == "us_map":
            return self.map_record(rec, ms)
        if stream == "mlb_map":
            return self.mlb_map_record(rec, ms)
        if stream == "soccer_games":
            return self.soccer_record(rec, ms)
        if stream == "espn":
            return self.espn_record(rec, ms)

    def map_record(self, rec: dict, ms: int) -> None:
        """Admission and orientation from the latest pregame record per `key`; venue fields
        (tick, minimum, increment, fee) follow the latest record of any time, as for v1 rules."""
        for slug, vals in market_rules(rec).items():
            if vals:
                self.rules.setdefault(slug, {}).update(vals, recv_ms=ms)
        key = rec.get("key") or rec.get("us_event_slug") or rec.get("event_slug")
        if not key:
            return
        key, keys = str(key), intl_keys(rec)
        if not is_pregame(ms, start_s(rec)) or not before_play(rec, ms, self.in_play):
            self.late[key] = ms
            for k in keys:
                self.late_keys[k] = ms
            return
        old = self.pre.get(key)
        self._unindex(key, (old or {}).get("_keys", ()))
        self.pre[key] = rec | {"_recv_ms": ms, "_keys": keys, "_legs": normalize_markets(rec)}
        for k in keys:
            self.by_key.setdefault(k, set()).add(key)

    def _unindex(self, key: str, keys) -> None:
        for k in keys:
            s = self.by_key.get(k)
            if s is not None:
                s.discard(key)
                if not s:
                    del self.by_key[k]

    def mark_in_play(self, sport: str, game_id: str, ms: int) -> None:
        """The international game was seen in play at ms: its US mapping is frozen from here."""
        g = self.games.get((sport, str(game_id)))
        keys = g["keys"] if g else [("mlb", str(game_id))] if sport == "mlb" else [("slug", str(game_id))]
        for k in keys:
            seen = self.in_play.get(k)
            if seen is None:
                self.in_play[k] = [ms, ms]
                self.cover(f"{IN_PLAY}|{k[0]}|{k[1]}", ms)
            else:
                seen[1] = ms

    def espn_record(self, rec: dict, ms: int) -> None:
        if str(rec.get("state") or "").lower() in ESPN_IN_PLAY:
            slug = self.espn_slug.get(str(rec.get("espn_id")))
            if slug is not None:
                self.mark_in_play("soccer", slug, ms)

    def mlb_map_record(self, rec: dict, ms: int) -> None:
        if not is_pregame(ms, mlb_map_start(rec)):
            return
        pm = rec.get("pm") or {}
        try:
            pk = str(int(rec.get("game_pk")))
        except (TypeError, ValueError):
            return
        keys = [("mlb", pk)] + [(n, str(pm[f])) for n, f in (("cid", "condition_id"), ("slug", "slug")) if pm.get(f)]
        self.games[("mlb", pk)] = dict(keys=keys, ms=ms)
        for side in ("home", "away"):
            if pm.get(f"{side}_token"):
                self.tokens[str(pm[f"{side}_token"])] = ("mlb", pk, side)

    def soccer_record(self, rec: dict, ms: int) -> None:
        slug = rec.get("event_slug")
        if not slug or not is_pregame(ms, kickoff(rec)):
            return
        eid = (rec.get("espn") or {}).get("id")
        eid = None if eid is None else str(eid)
        old = (self.games.get(("soccer", str(slug))) or {}).get("espn_id")
        if old is not None and self.espn_slug.get(old) == str(slug):
            del self.espn_slug[old]
        self.games[("soccer", str(slug))] = dict(keys=[("slug", str(slug))], ms=ms, espn_id=eid)
        if eid is not None:
            self.espn_slug[eid] = str(slug)
        for side, leg in (rec.get("legs") or {}).items():
            if isinstance(leg, dict) and leg.get("yes_token") and _side(side):
                self.tokens[str(leg["yes_token"])] = ("soccer", str(slug), _side(side))

    def book_record(self, rec: dict, ms: int) -> None:
        conn = rec.get("conn")
        cid = str(rec.get("conn_id") or rec.get("shard") or "0")
        if conn is not None:
            self.markers = True
            if conn == "open":
                self.open_ms[cid] = self.activity_ms[cid] = ms
            elif conn == "closed":
                self.closed_ms[cid] = ms
            else:                                         # heartbeat / pong / subscribed
                self.activity_ms[cid] = ms
            return
        msg = rec.get("msg")
        for m in msg if isinstance(msg, list) else (msg,):
            if not isinstance(m, dict):
                continue
            md = m.get("marketData") if isinstance(m.get("marketData"), dict) else m if "offers" in m or "bids" in m else None
            if md is None or not md.get("marketSlug"):
                continue
            slug = str(md["marketSlug"])
            self.seq += 1
            self.latest[slug] = (ms, self.seq, md)
            self.book_ms[slug] = self.activity_ms[cid] = ms
            self.book_conn[slug] = cid
            self.market_state[slug] = md.get("state")
            self.cover(f"usbook|{slug}", ms)

    def trade_record(self, rec: dict, ms: int) -> None:
        msg = rec.get("msg")
        for m in msg if isinstance(msg, list) else (msg,):
            if not isinstance(m, dict):
                continue
            t = m.get("trade") if isinstance(m.get("trade"), dict) else m if "marketSlug" in m and "price" in m else None
            if t is None or not t.get("marketSlug"):
                continue
            state = str(t.get("state") or "").upper()
            if "BUST" in state or "CANCEL" in state:
                continue
            p, q = px(t.get("price")), px(t.get("quantity"))
            if p is None or q is None or not (0 < p < 1) or q <= 0:
                continue
            slug = str(t["marketSlug"])
            h = self.trades.setdefault(slug, [None, None])
            if h[0] is not None and ms > h[0].ms:
                h[1] = h[0]
            h[0] = Trade(ms, p, q)
            self.cover(f"ustrade|{slug}", ms)

    def prune(self, now_ms: int, age_ms: int = PRUNE_MS) -> None:
        """Forget markets and mappings idle for age_ms (bounded memory in a year-long follow)."""
        for slug in [s for s, t in self.book_ms.items() if now_ms - t > age_ms]:
            for d in (self.latest, self.applied, self.books, self.book_ms, self.book_conn, self.market_state):
                d.pop(slug, None)
        for slug in [s for s, h in self.trades.items() if h[0] is not None and now_ms - h[0].ms > age_ms]:
            del self.trades[slug]
        for slug in [s for s, r in self.pre.items() if now_ms - r["_recv_ms"] > age_ms]:
            self._unindex(slug, self.pre.pop(slug)["_keys"])
        for k in [k for k, (_, last) in self.in_play.items() if now_ms - last > age_ms]:
            del self.in_play[k]
        for d in (self.late, self.late_keys):
            for k in [k for k, t in d.items() if now_ms - t > age_ms]:
                del d[k]
        for slug in [s for s, r in self.rules.items() if now_ms - r["recv_ms"] > age_ms]:
            del self.rules[slug]
        for g in [g for g, v in self.games.items() if now_ms - v["ms"] > age_ms]:
            del self.games[g]
        live = set(self.games)
        for tok in [t for t, v in self.tokens.items() if v[:2] not in live]:
            del self.tokens[tok]
        for eid in [e for e, slug in self.espn_slug.items() if ("soccer", slug) not in live]:
            del self.espn_slug[eid]

    # ------------------------------------------------------------------ admission and legs
    def admission(self, sport: str, game_id: str) -> tuple[dict | None, str]:
        """(latest pregame us_map record of the game's US event, "exact") or (None, reason).

        Among the latest pregame records (per key) naming the international game: none -> the
        game is `us_unmapped` (or `us_discovered_after_start` if a record came only after the
        start); only records without a US event, or one received after the US event's record ->
        `us_no_us_market`; more than one US event -> `us_ambiguous`; else the US record's match."""
        g = self.games.get((sport, str(game_id)))
        keys = g["keys"] if g else [("mlb", str(game_id))] if sport == "mlb" else [("slug", str(game_id))]
        recs = [self.pre[k] for k in sorted(set().union(*(self.by_key.get(k, set()) for k in keys)))]
        if not recs:
            return None, "us_discovered_after_start" if any(k in self.late_keys for k in keys) else "us_unmapped"
        return admit(recs)

    def leg(self, sport: str, game_id: str, side: str) -> tuple[Leg | None, dict | None, str]:
        rec, status = self.admission(sport, game_id)
        if rec is None:
            return None, None, status
        leg = rec["_legs"].get(side)
        if leg is None:
            return None, rec, "us_leg_missing"
        latest = {k: v for k, v in (self.rules.get(leg.slug) or {}).items() if k != "recv_ms"}
        return (replace(leg, **latest) if latest else leg), rec, "exact"

    def reference_for_token(self, token, t: int, max_age_ms: int = REF_MS):
        """The frozen strategies' reference lookup: intl token -> its side on the US venue."""
        hit = self.tokens.get(str(token))
        if hit is None:
            return None
        leg, _, _ = self.leg(*hit)
        return None if leg is None else self.reference(leg.slug, leg.long, t, max_age_ms)

    def reference(self, slug: str, long: bool, t: int, max_age_ms: int = REF_MS):
        """Last valid US trade received strictly before t and at most max_age_ms old, in the
        bought side's price (short = 1 - long)."""
        for tr in self.trades.get(slug) or ():
            if tr is not None and tr.ms < t:
                if t - tr.ms > max_age_ms:
                    return None
                return tr if long else Trade(tr.ms, round(1 - tr.price, 10), tr.size)
        return None

    # ------------------------------------------------------------------ execution
    def freshness(self, slug: str, t: int) -> dict:
        b = self.book_ms.get(slug)
        cid = self.book_conn.get(slug, "0")
        o, c, a = self.open_ms.get(cid), self.closed_ms.get(cid), self.activity_ms.get(cid)
        out = dict(book_ms=b, book_age_ms=None if b is None else t - b, market_state=self.market_state.get(slug),
                   conn_open_ms=o, conn_closed_ms=c, conn_activity_ms=a)
        reason = ("no_book" if b is None else
                  "stale_book" if not 0 <= t - b <= FRESH_MS else "")
        if not self.markers:
            return out | dict(ok=not reason, reason=reason, connection_ok="unknown")
        if not reason:
            reason = ("no_snapshot_since_open" if o is not None and b < o else
                      "connection_closed" if c is not None and c >= b else "")
        return out | dict(ok=not reason, reason=reason, connection_ok=not reason)

    def _sync(self, slug: str) -> ReceivedBook:
        """Apply the latest received snapshot of a market to its crossing views."""
        ms, seq, md = self.latest[slug]
        rb = self.books.setdefault(slug, ReceivedBook())
        if self.applied.get(slug) != seq:
            bids = [(px(x.get("px")), px(x.get("qty"))) for x in md.get("bids") or () if isinstance(x, dict)]
            offers = [(px(x.get("px")), px(x.get("qty"))) for x in md.get("offers") or () if isinstance(x, dict)]
            if any(p is None or q is None or q < 0 for p, q in bids + offers):
                raise ValueError("invalid US book level")

            def lv(levels, flip=False):
                return [dict(price=round(1 - p, 10) if flip else p, size=q) for p, q in levels if 0 < p < 1]

            rb.apply(ms, [dict(event_type="book", asset_id=f"{slug}|long", bids=lv(bids), asks=lv(offers)),
                          dict(event_type="book", asset_id=f"{slug}|short", bids=lv(offers, True), asks=lv(bids, True))])
            self.applied[slug] = seq
        return rb

    def cross(self, leg: Leg, t: int, policy: str, limit: float, qty: float, fee: float, min_qty: float | None,
              budget: float) -> dict:
        """One IOC buy of `qty` at `limit` (bought-side price) against the received book as of t."""
        view = f"{leg.slug}|{leg.us_side}"
        rb = self._sync(leg.slug)
        asks = rb.books.get(view, {}).get("SELL", {})
        bids = rb.books.get(view, {}).get("BUY", {})
        top = dict(ask_at_exec=min(asks, default=None), bid_at_exec=max(bids, default=None))
        before = {p: lv.used.get(policy, 0.) for p, lv in asks.items()}
        # the budget cap never binds: qty was sized at the limit, and p + fee(p) rises with p
        fill = rb.cross(view, t, policy=policy, buy=True, budget=budget * (1 + 1e-9), shares=qty, limit=limit,
                        fee_rate=fee, min_order_shares=min_qty, stale_ms=STALE_OFF)
        levels = sorted((p, lv.used.get(policy, 0.) - before.get(p, 0.)) for p, lv in asks.items())
        fill["levels"] = [[p, q] for p, q in levels if q > 1e-12]
        return fill | top


def admit(recs: list[dict]) -> tuple[dict | None, str]:
    """Admission among the latest pregame us_map records (each with `_recv_ms`) naming one
    international game: more than one US event -> `us_ambiguous`; no US event, or a `no_us_market`
    record received after the US event's -> `us_no_us_market`; else the US record's own match."""
    us = [r for r in recs if r.get("us_event_slug")]
    if len(us) > 1:
        return None, "us_ambiguous"
    none = [r["_recv_ms"] for r in recs if not r.get("us_event_slug")]
    if not us or (none and max(none) > us[0]["_recv_ms"]):
        return None, "us_no_us_market"
    if us[0].get("match") != "exact":
        return None, f"us_{us[0].get('match') or 'unmatched'}"
    return us[0], "exact"


def order_quantity(limit: float, budget: float, fee: float, increment: float) -> float:
    """Largest multiple of `increment` whose cost at `limit` including the taker fee is <= budget."""
    per = limit + fee * limit * (1 - limit)
    n = math.floor(budget / per / increment + EPS)
    q = round(n * increment, 10)
    while q > 0 and q * per > budget + 1e-9:          # guard against floating-point overshoot
        n -= 1
        q = round(n * increment, 10)
    return max(q, 0.)
