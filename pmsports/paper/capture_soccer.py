"""Soccer capture for the paper test: Gamma three-way discovery, ESPN matching, ESPN state polling.

Runs inside `pmsports record` (record.py) and writes, per pmsports/paper/DESIGN.md:

  soccer_games.jsonl  every discovered three-way moneyline game, re-written only when its
                      mapping changes and frozen at kickoff (the earlier of the Gamma start and
                      the matched ESPN kickoff; the protocol matches before kickoff). A game
                      first seen, or first matched, only at or after kickoff is `unmatched`
                      ("first discovered after kickoff") and is never polled or subscribed.
  espn.jsonl          ESPN state changes of exactly matched games; key events carry a stable
                      fingerprint `fp` so each one is reported as new exactly once. An edit of an
                      event already received (same ESPN id: new text, corrected clock) is never
                      new again: it goes to `revised_events` with `prev_fp`.

Every ESPN record is stamped with the local time its response arrived. Nothing here trades.
Matching rule: same ESPN league path (this module's PAPER_LEAGUE_OVERRIDES/PAPER_LEAGUE_BLOCK,
else LEAGUE_PATH / EXTRA_PATHS of events/soccer.py -- see paths_for below), kickoff within 30
minutes, both teams agree (slug abbreviation and Gamma team name, scored with the events/soccer.py
heuristics), exactly one such ESPN event, and a clear home/away orientation.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .. import polymarket as pm
from ..collect import DATA_DIR
from ..events import soccer as S
from ..http import get_json

log = logging.getLogger("pmsports")

GAMMA_EVENTS = f"{pm.GAMMA}/events"
ESPN = S.BASE                     # https://site.api.espn.com/apis/site/v2/sports/soccer/{path}/{kind}
EASTERN = ZoneInfo("America/New_York")
DISCOVER_S = 600                  # discovery cadence
BACK_S, AHEAD_S = 3 * 3600, 6 * 3600   # started < 3 h ago or kicking off within 6 h
KICKOFF_TOL_S = 1800              # ESPN kickoff within 30 minutes
TEAM_MIN = 0.70                   # both teams must reach this agreement score (0..1)
NAME_MIN = 0.85                   # a Gamma team-name similarity counts only from here
ORIENT_MARGIN = 0.30              # home/away orientation must win by this (sum of two scores)
SB_EVERY_S = 30                   # scoreboard per active league path
SUMMARY_EVERY_S = 5               # summary per game from period 2 minute 60 until final
SUMMARY_FROM_S = 3600
ESPN_RPS = 4.0                    # total ESPN polling budget (host bucket allows 5/s)
MAX_INFLIGHT = 3
PRE_S = 600                       # start scoreboard polling 10 minutes before kickoff
KEEP_S = 5 * 3600                 # poll / remember a matched game until kickoff + 5 h
TOKEN_GRACE_S = 600               # Yes tokens stay subscribed until ESPN final + 10 min (cap: polling end + 10 min)
SB_CACHE_S = 60
DISCOVER_ESPN_GAP_S = 1.0         # discovery's own scoreboard fetches stay <= 1/s (polling 4 + 1 = host's 5)
DISCOVER_RETRIES, DISCOVER_TIMEOUT_S = 2, 8.0
DISCOVER_ESPN_BUDGET_S = 120.0    # failed ESPN fetches may cost one discovery pass at most this long
FALLBACK_FROM_S = 75 * 60         # from kickoff + 75 min, a scoreboard not advancing for
STALE_SB_S = 120                  # 2 minutes hands the game to summary polling
LATE = "first discovered after kickoff"

# ---------------------------------------------------------- paper-only league path overrides
# events/soccer.py (LEAGUE_PATH/EXTRA_PATHS) is the frozen historical map; live discovery
# consults these first so newly-observed Polymarket seriesSlugs (or slugs the historical map
# gets wrong) can be covered without touching that file. Each entry below was verified on
# 2026-09-25 by fetching the ESPN scoreboard for >= 1 upcoming Polymarket event of that series
# and confirming kickoff agreement (<= 30 min) and both teams agreeing (see match_game).
PAPER_LEAGUE_OVERRIDES: dict[str, tuple[str, ...]] = {
    # verified 2026-09-25: 6/6 UNL events matched (e.g. unl-arm-lat-2026-09-25 -> ESPN 401861050
    # Armenia vs Latvia, kickoff exact, team scores 1.00/0.95).
    "soccer-unl": ("uefa.nations",),
    # verified 2026-09-25: 6/6 matched (e.g. conl-ber-gdl-2026-09-25 -> ESPN 401900630
    # Bermuda vs Guadeloupe, kickoff exact, team scores 1.00/1.00).
    "concacaf-nations-league": ("concacaf.nations.league",),
    # verified 2026-09-25: 6/6 matched (e.g. el2-cra-bar-2026-09-26 -> ESPN 401881358
    # Crawley Town vs Barnet, kickoff exact, team scores 1.00/1.00). English League Two.
    "soccer-el2": ("eng.4",),
    # verified 2026-09-25: 6/6 matched (e.g. uslc-har-lc-2026-09-25 -> ESPN 401842249
    # Hartford Athletic vs Louisville City FC, kickoff exact, team scores 0.95/0.95). USL Championship.
    "uslc": ("usa.usl.1",),
    # verified 2026-09-25: 6/6 matched (e.g. nwsl-rac-wav-2026-09-25 -> ESPN 401854009
    # Racing Louisville FC vs San Diego Wave FC, kickoff exact, team scores 0.95/0.95).
    "soccer-nwsl": ("usa.nwsl",),
    # verified 2026-09-25: 6/6 matched (e.g. uru1-pro-rcm-2026-09-26 -> ESPN 401923664
    # Progreso vs Racing Club Montevideo, kickoff exact, team scores 0.95/0.89). Liga AUF Uruguaya.
    "uru1-games": ("uru.1",),
    # verified 2026-09-25: 6/6 matched (e.g. wsl-cha-mci-2026-09-26 -> ESPN 401902907
    # Charlton Athletic vs Manchester City, kickoff exact, team scores 1.00/0.95). English WSL.
    "wsl-games": ("eng.w.1",),
    # verified 2026-09-25: 5/5 matched (e.g. gtm-cdg-dsp-2026-09-26 -> ESPN 401879633
    # CD Guastatoya vs Deportivo San Pedro, kickoff exact, team scores 0.95/1.00). Guatemala Liga Nacional.
    "soccer-gtm": ("gua.1",),
}

# events/soccer.py maps "chi2-games" -> "chi.2" (Chile Segunda), but live chi2-games events are
# China League One (e.g. chi2-wux-gh-2026-09-25: Wuxi Wugou vs Guangxi Hengchen FC) -- verified
# 2026-09-25 by inspecting the Gamma team names, which are Chinese clubs, not Chilean ones. ESPN
# has no public scoreboard for China League One (soccer/chn.2 returns HTTP 400), so this series
# is blocked rather than remapped: it must not silently score against the wrong country's league.
PAPER_LEAGUE_BLOCK: set[str] = {"chi2-games"}


def paths_for(series: str) -> tuple[str, ...]:
    """ESPN league path(s) for a Polymarket seriesSlug: paper overrides, then the historical map."""
    if not series or series in PAPER_LEAGUE_BLOCK:
        return ()
    return PAPER_LEAGUE_OVERRIDES.get(series) or S.paths_for(series)


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _kickoff(*recs) -> float | None:
    """Earliest known kickoff of soccer_games records / parsed games: Gamma start or ESPN kickoff."""
    ts = [x for r in recs if r for x in (r.get("start_ts"), (r.get("espn") or {}).get("kickoff_ts")) if x]
    return min(ts) if ts else None


def retoken(games_ref: dict, now: float) -> None:
    """Shared CLOB token set = unexpired tokens of every source (`<src>_tokens`: token -> expiry ts)."""
    live: set[str] = set()
    for src in ("mlb_tokens", "soccer_tokens"):
        d = games_ref.get(src) or {}
        for t in [t for t, exp in d.items() if exp <= now]:
            del d[t]
        live |= d.keys()
    games_ref["tokens"] = live


# ------------------------------------------------------------------------------ Polymarket side

def fetch_events(now: float, get=get_json, page: int = 200, max_pages: int = 25) -> list[dict]:
    """Open Gamma soccer events whose end date (kickoff) is within [now - 3 h, now + 6 h]."""
    params = {"tag_slug": "soccer", "closed": "false", "end_date_min": _iso(now - BACK_S),
              "end_date_max": _iso(now + AHEAD_S), "order": "endDate", "ascending": "true", "limit": page}
    out, offset = [], 0
    for _ in range(max_pages):
        j = get(GAMMA_EVENTS, {**params, "offset": offset})
        if not j:
            break
        out.extend(j)
        offset += len(j)
    return out


def parse_event(e: dict) -> dict | None:
    """The three Yes/No moneyline legs of a Gamma soccer event, keyed 'a'/'b' (slug order) and 'draw'.

    None when the event does not have exactly three moneyline markets (props, halves, futures).
    Otherwise `error` says why the legs could not be assigned (the game stays in coverage).
    Leg assignment follows events/soccer.py our_games: market slug == event slug + '-' + token.
    """
    ml = [m for m in e.get("markets") or [] if m.get("sportsMarketType") == "moneyline"]
    if len(ml) != 3:
        return None
    slug = e.get("slug") or ""
    series = e.get("seriesSlug") or ((e.get("series") or [{}])[0] or {}).get("slug")
    start = pm.parse_ts(ml[0].get("gameStartTime")) or pm.parse_ts(e.get("startTime"))
    g = {"event_slug": slug, "series": series, "start_ts": start, "game_id": e.get("gameId"),
         "teams": {}, "legs": {}, "error": None}
    m = S.SLUG_RE.match(slug)
    if not m:
        g["error"] = "event slug does not end in two team tokens and a date"
        return g
    a, b = m["a"], m["b"]
    info = {str(t.get("abbreviation") or "").lower(): t for t in e.get("teams") or []}
    g["teams"] = {k: {"abbr": tok, "name": (info.get(tok) or {}).get("name"),
                      "ordering": (info.get(tok) or {}).get("ordering")} for k, tok in (("a", a), ("b", b))}
    for mk in ml:
        ms = mk.get("slug") or ""
        tok = ms[len(slug) + 1:] if ms.startswith(slug + "-") else ""
        leg = "draw" if tok == "draw" else "a" if tok == a else "b" if tok == b else None
        if leg is None or leg in g["legs"]:
            g["error"] = f"moneyline slug {ms!r} is not one of {a}/{b}/draw"
            continue
        outcomes = [str(o).lower() for o in pm._loads(mk.get("outcomes"))]
        tokens = [str(t) for t in pm._loads(mk.get("clobTokenIds"))]
        if sorted(outcomes) != ["no", "yes"] or len(tokens) != 2:
            g["error"] = f"moneyline {ms!r} outcomes are not Yes/No"
            continue
        y = outcomes.index("yes")
        fee = mk.get("feeSchedule") or {}
        g["legs"][leg] = {"condition_id": mk.get("conditionId"), "yes_token": tokens[y], "no_token": tokens[1 - y],
                          "fee_rate": fee.get("rate"), "fee_exponent": fee.get("exponent"),
                          "tick": mk.get("orderPriceMinTickSize"), "min_size": mk.get("orderMinSize"),
                          "seconds_delay": mk.get("secondsDelay")}
    if not g["error"] and len(g["legs"]) != 3:
        g["error"] = f"legs found: {sorted(g['legs'])}"
    return g


# ------------------------------------------------------------------------------------ ESPN side

def espn_dates(ts: float) -> list[str]:
    """ESPN buckets soccer scoreboards by US Eastern date; the UTC date is added as a guard."""
    d = (datetime.fromtimestamp(ts, EASTERN).strftime("%Y%m%d"),
         datetime.fromtimestamp(ts, timezone.utc).strftime("%Y%m%d"))
    return list(dict.fromkeys(d))


def _status(st: dict) -> dict:
    t = st.get("type") or {}
    return {"state": t.get("state"), "status": t.get("name"), "period": _int(st.get("period")),
            "clock_s": _float(st.get("clock")), "display_clock": st.get("displayClock")}


def espn_games(js: dict | None, path: str) -> list[dict]:
    """Flatten a scoreboard response: one dict per ESPN event with both competitors."""
    out = []
    for e in (js or {}).get("events") or []:
        comp = (e.get("competitions") or [{}])[0] or {}
        cs = {c.get("homeAway"): c for c in comp.get("competitors") or []}
        h, a = cs.get("home"), cs.get("away")
        if not h or not a:
            continue
        out.append({"path": path, "id": str(e.get("id")), "kickoff_ts": pm.parse_ts(e.get("date")),
                    "home": h.get("team") or {}, "away": a.get("team") or {},
                    "home_score": _int(h.get("score")), "away_score": _int(a.get("score")),
                    "status": _status(comp.get("status") or e.get("status") or {})})
    return out


def team_score(team: dict, espn_team: dict) -> float:
    """0..1 agreement of a Polymarket team (slug abbreviation + Gamma name) with an ESPN team."""
    s = S._abbr_score(team["abbr"], espn_team) if team.get("abbr") else 0.0
    name = S._norm(team.get("name"))
    if len(name) >= 4:
        for k in ("displayName", "shortDisplayName", "name", "location"):
            v = S._norm(espn_team.get(k))
            sim = S._sim(name, v) if len(v) >= 4 else 0.0
            if sim >= NAME_MIN:
                s = max(s, sim)
    return s


def match_game(g: dict, cands: list[dict]) -> tuple[str, dict | None, bool | None, str]:
    """(match, ESPN event, slug team 'a' is ESPN home, reason) for one parsed game."""
    st = g["start_ts"]
    near = [c for c in cands if c["kickoff_ts"] is not None and abs(c["kickoff_ts"] - st) <= KICKOFF_TOL_S]
    if not near:
        return "unmatched", None, None, "no ESPN event within 30 min of kickoff"
    ta, tb = g["teams"]["a"], g["teams"]["b"]
    scored = []
    for c in near:
        ha = (team_score(ta, c["home"]), team_score(tb, c["away"]))
        ah = (team_score(ta, c["away"]), team_score(tb, c["home"]))
        a_home = sum(ha) >= sum(ah)
        scored.append((c, a_home, ha if a_home else ah, abs(sum(ha) - sum(ah))))
    ok = [x for x in scored if min(x[2]) >= TEAM_MIN]
    if not ok:
        c, _, pick, _ = max(scored, key=lambda x: min(x[2]))
        return "unmatched", None, None, (f"no team agreement among {len(near)} ESPN events within 30 min "
                                         f"(best {c['id']}: {pick[0]:.2f}/{pick[1]:.2f})")
    if len(ok) > 1:
        return "ambiguous", None, None, f"{len(ok)} ESPN events agree: {sorted(x[0]['id'] for x in ok)}"
    c, a_home, pick, margin = ok[0]
    if margin < ORIENT_MARGIN:
        return "ambiguous", None, None, f"ESPN {c['id']}: home/away orientation unclear (margin {margin:.2f})"
    return "exact", c, a_home, (f"dt={c['kickoff_ts'] - st:+.0f}s teams={pick[0]:.2f}/{pick[1]:.2f} "
                                f"orientation margin={margin:.2f}")


def key_events(js: dict | None) -> list[dict]:
    """ESPN keyEvents with the fields events/soccer.py build_events reads, plus `fp`."""
    out = []
    for e in (js or {}).get("keyEvents") or []:
        clock = e.get("clock") or {}
        ev = {"type": (e.get("type") or {}).get("type") or "",
              "period": _int((e.get("period") or {}).get("number")) or 0,
              "clock_value": _float(clock.get("value")),
              "clock_display": clock.get("displayValue") or "",
              "team_id": str((e.get("team") or {}).get("id") or ""),
              "scoring": bool(e.get("scoringPlay")),
              "text": e.get("text") or "",
              "id": str(e.get("id") or ""), "wallclock": e.get("wallclock")}
        ev["fp"] = fingerprint(ev)
        out.append(ev)
    return out


def fingerprint(ev: dict) -> str:
    """Stable id of a key event: type, period, clock value, team and text."""
    k = [ev.get("type"), ev.get("period"), ev.get("clock_value"), ev.get("team_id"), ev.get("text")]
    return hashlib.sha1(json.dumps(k, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()[:16]


def summary_state(js: dict | None) -> tuple[dict, int | None, int | None]:
    """(status, home score, away score) from a summary header."""
    comp = (((js or {}).get("header") or {}).get("competitions") or [{}])[0] or {}
    cs = {c.get("homeAway"): c for c in comp.get("competitors") or []}
    return (_status(comp.get("status") or {}), _int((cs.get("home") or {}).get("score")),
            _int((cs.get("away") or {}).get("score")))


def intervals(n_summary: int, n_scoreboard: int, rps: float = ESPN_RPS) -> tuple[float, float]:
    """(scoreboard, summary) poll intervals keeping the total ESPN rate <= rps.

    Scoreboards take at most half the budget; summaries share the rest, so the 5-second
    summary interval stretches when many games are late in the second half at once."""
    sb = max(SB_EVERY_S, n_scoreboard / (rps / 2))
    budget = rps - (n_scoreboard / sb if n_scoreboard else 0.0)
    return sb, max(SUMMARY_EVERY_S, n_summary / budget)


def _minute_clock(s: dict) -> float:
    """Match clock in seconds: ESPN's numeric clock, or the display minute when that is missing or 0."""
    m = re.match(r"\s*(\d+)", str(s.get("display_clock") or ""))
    return max(s.get("clock_s") or 0.0, int(m[1]) * 60.0 if m else 0.0)


def late_second_half(s: dict) -> bool:
    """Summary polling starts at period 2 minute 60 (and continues into extra time)."""
    p = s.get("period") or 0
    return s.get("state") == "in" and (p >= 3 or (p == 2 and _minute_clock(s) >= SUMMARY_FROM_S))


def _read_jsonl(path: Path):
    try:
        with open(path) as fh:
            for line in fh:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except OSError:
        return


# -------------------------------------------------------------------------------------- capture

class SoccerCapture:
    """Discovery + ESPN polling. `games_sink`/`espn_sink` need `.write(obj, recv_ms=None)`;
    `meta` (record.MarketMeta) fetches CLOB market metadata and writes market_meta.jsonl."""

    def __init__(self, games_ref: dict, games_sink, espn_sink, meta=None, get=get_json):
        self.ref, self.games_sink, self.espn_sink, self.meta, self.get = games_ref, games_sink, espn_sink, meta, get
        self.records: dict[str, dict] = {}     # event_slug -> last written soccer_games record (no recv_ms)
        self.resync: set[str] = set()          # seeded slugs whose file-latest record is not the one kept
        self.active: dict[str, dict] = {}      # espn_id -> exactly matched game being followed
        self.espn: dict[str, dict] = {}        # espn_id -> polling state (seen fps, ids, last key, mode)
        self.sb_cache: dict[tuple, tuple] = {}  # (path, date) -> (recv ts, espn_games)
        self.last_sb: dict[tuple, float] = {}
        self.inflight: set = set()
        self._tasks: set = set()
        self._warned: dict = {}
        self._failed: dict[tuple, str] = {}    # this discovery pass: (path, date) -> error
        self._espn_spent = 0.0
        self.mono = time.monotonic             # elapsed time within a discovery pass

    # -- state ----------------------------------------------------------------------------------
    def _st(self, eid: str) -> dict:
        return self.espn.setdefault(eid, {"seen": set(), "ids": {}, "key": None, "summary": False, "final": False,
                                          "final_at": None, "sb_key": None, "t_prog": 0.0, "t_sum": 0.0,
                                          "post_since": None, "t": time.time()})

    @staticmethod
    def _final(st: dict, t: float) -> None:
        if not st["final"]:
            st["final"], st["final_at"] = True, t

    def _warn(self, key, msg, every_s: float = 60.0) -> None:
        now = time.time()
        if now - self._warned.get(key, 0.0) >= every_s:
            self._warned[key] = now
            log.warning(msg)

    def seed(self, root: Path | None = None, now: float | None = None) -> None:
        """Reload today's and yesterday's records so a restart neither re-reports key events
        nor re-matches games that already kicked off. Per game the record kept is the one live
        discovery would hold: the latest received before the kickoff of the record kept so far
        and of itself (else the latest record if it is not exact). A game whose only records
        are exact matches received after kickoff is forgotten, so discovery marks it LATE."""
        root = root or DATA_DIR / "live"
        now = time.time() if now is None else now
        days = [time.strftime("%Y-%m-%d", time.gmtime(now - d * 86400)) for d in (1, 0)]
        latest, keep = {}, {}
        for day in days:
            for r in _read_jsonl(root / day / "soccer_games.jsonl"):
                if not r.get("event_slug"):
                    continue
                rec = {k: v for k, v in r.items() if k != "recv_ms"}
                latest[r["event_slug"]] = rec
                k = _kickoff(keep.get(r["event_slug"]), rec)
                if k is None or int(r.get("recv_ms") or 0) < k * 1000:
                    keep[r["event_slug"]] = rec
        for slug, rec in latest.items():
            if slug not in keep and rec.get("match") != "exact":
                keep[slug] = rec
            if keep.get(slug) != rec:
                self.resync.add(slug)
        self.records.update(keep)
        for day in days:
            for r in _read_jsonl(root / day / "espn.jsonl"):
                if not r.get("espn_id"):
                    continue
                st = self._st(str(r["espn_id"]))
                for e in (r.get("new_events") or []) + (r.get("revised_events") or []):
                    st["seen"].add(e.get("fp"))
                    if e.get("id"):
                        st["ids"][str(e["id"])] = e.get("fp")
                st["key"] = self._key(r)
                st["summary"] |= r.get("src") == "summary" or late_second_half(r)
                if r.get("state") == "post":
                    self._final(st, int(r.get("recv_ms") or 0) / 1000)
        self.active = self._active(now)
        self.ref.setdefault("soccer_tokens", {}).update(self._tokens())
        retoken(self.ref, now)

    # -- discovery ------------------------------------------------------------------------------
    def scoreboard(self, path: str, date: str, now: float) -> list[dict]:
        """Discovery's scoreboard fetch: cached; a failure is remembered for the rest of the pass,
        and failures stop being retried once they cost DISCOVER_ESPN_BUDGET_S in one pass."""
        hit = self.sb_cache.get((path, date))
        if hit and now - hit[0] <= SB_CACHE_S:
            return hit[1]
        if (path, date) in self._failed:
            raise RuntimeError(self._failed[(path, date)])
        if self._failed and self._espn_spent >= DISCOVER_ESPN_BUDGET_S:
            raise RuntimeError("ESPN failures used this pass's budget")
        t = time.monotonic()
        try:
            js = self.get(ESPN.format(path=path, kind="scoreboard"), {"dates": date, "limit": 400},
                          retries=DISCOVER_RETRIES, timeout=DISCOVER_TIMEOUT_S)
        except Exception as exc:
            self._failed[(path, date)] = f"{type(exc).__name__}: {exc}"[:160]
            self._espn_spent += time.monotonic() - t
            raise
        finally:
            time.sleep(DISCOVER_ESPN_GAP_S)
        games = espn_games(js, path)
        self.sb_cache[(path, date)] = (time.time(), games)
        return games

    def _record(self, g: dict, match: str, c: dict | None, a_home: bool | None, reason: str) -> dict:
        if a_home is not None:
            orient, home = "espn", "a" if a_home else "b"
        elif (g["teams"].get("b") or {}).get("ordering") == "home":
            orient, home = "gamma", "b"
        elif (g["teams"].get("a") or {}).get("ordering") == "home":
            orient, home = "gamma", "a"
        else:
            orient, home = "slug", "a"
        away = "b" if home == "a" else "a"
        espn = None
        if match == "exact":
            espn = {"path": c["path"], "id": c["id"], "home_id": str(c["home"].get("id")),
                    "away_id": str(c["away"].get("id")), "home_name": c["home"].get("displayName"),
                    "away_name": c["away"].get("displayName"), "kickoff_ts": c["kickoff_ts"]}
        t = g.get("teams") or {}
        legs = copy.deepcopy(g["legs"])        # records never share dicts with each other
        return {"event_slug": g["event_slug"], "series": g["series"], "start_ts": g["start_ts"],
                "legs": {"home": legs.get(home), "draw": legs.get("draw"), "away": legs.get(away)},
                "espn": espn, "match": match, "reason": reason, "orientation": orient, "game_id": g["game_id"],
                "teams": {k: {x: (t.get(leg) or {}).get(x) for x in ("abbr", "name")}
                          for k, leg in (("home", home), ("away", away))}}

    def _classify(self, g: dict) -> dict | None:
        """Record for a game that needs no ESPN lookup (bad legs, unmapped league, no kickoff)."""
        if g["error"]:
            return self._record(g, "unmatched", None, None, g["error"])
        if not paths_for(g["series"] or ""):
            return self._record(g, "unmapped_league", None, None, f"series {g['series']!r} has no ESPN league path")
        if not g["start_ts"]:
            return self._record(g, "unmatched", None, None, "no kickoff time")
        return None

    def _match(self, g: dict, now: float) -> dict | None:
        """soccer_games record for one parsed game; None keeps the previous record (ESPN down)."""
        r = self._classify(g)
        if r is not None:
            return r
        cands, failed = [], []
        for p in paths_for(g["series"] or ""):
            for d in espn_dates(g["start_ts"]):
                try:
                    cands += self.scoreboard(p, d, now)
                except Exception as exc:
                    failed.append(f"{p}/{d}: {exc}"[:160])
        if failed:
            if g["event_slug"] in self.records:
                return None
            return self._record(g, "unmatched", None, None, "ESPN scoreboard fetch failed: " + "; ".join(failed))
        return self._record(g, *match_game(g, cands))

    def discover(self, now: float) -> dict:
        """One discovery pass (blocking; run in a thread). Writes soccer_games / market_meta.

        `now` is the pass start; kickoff checks use the time elapsed since then, and are made
        again just before writing, so a mapping is never written once its kickoff has passed."""
        m0 = self.mono()

        def clock() -> float:
            return now + self.mono() - m0

        def after(*recs) -> bool:
            k = _kickoff(*recs)
            return k is not None and clock() >= k
        self._failed, self._espn_spent = {}, 0.0
        events = fetch_events(now, self.get)
        leagues, gids, parsed = set(), set(), []
        for e in events:
            leagues.add((e.get("slug") or "").split("-")[0])
            if e.get("gameId"):
                gids.add(e["gameId"])
            g = parse_event(e)
            if g and (g["start_ts"] is None or -BACK_S <= g["start_ts"] - now <= AHEAD_S):
                parsed.append(g)
        recs, frozen = {}, set()
        for g in parsed:
            slug, prev = g["event_slug"], self.records.get(g["event_slug"])
            if prev is not None and after(prev, g):
                recs[slug] = copy.deepcopy(prev)       # decided before kickoff: frozen from here on
                frozen.add(slug)
                continue
            r = self._classify(g)
            if r is None and prev is None and after(g):
                r = self._record(g, "unmatched", None, None, LATE)
            elif r is None:
                try:
                    r = self._match(g, now)
                except Exception as exc:
                    log.warning("soccer match %s: %s", slug, exc)
                    r = None
            if r is not None or prev is not None:
                recs[slug] = r if r is not None else copy.deepcopy(prev)
        # one ESPN event, one Polymarket game
        claims = defaultdict(list)
        for slug, r in recs.items():
            if r["match"] == "exact":
                claims[(r["espn"]["path"], r["espn"]["id"])].append(slug)
        for (_, eid), slugs in claims.items():
            if len(slugs) > 1:
                for s in slugs:
                    if s not in frozen:
                        recs[s] = {**recs[s], "match": "ambiguous", "espn": None,
                                   "reason": f"ESPN {eid} claimed by {len(slugs)} events: {sorted(slugs)}"}
        fresh = {g["event_slug"]: g for g in parsed}

        def settle(slug: str, r: dict) -> dict:
            """What may still be written: never a new mapping once kickoff has passed (it may pass mid-pass)."""
            prev = self.records.get(slug)
            if slug in frozen or not after(prev, r, fresh[slug]):
                return r
            if prev is not None:
                frozen.add(slug)
                return copy.deepcopy(prev)
            return self._record(fresh[slug], "unmatched", None, None, LATE) if r["match"] == "exact" else r
        for slug in list(recs):
            r = recs[slug] = settle(slug, recs[slug])
            try:
                if r["match"] == "exact" and self.meta is not None:
                    self._refresh_meta(r, fresh.get(slug), slug in frozen)
            except Exception as exc:
                log.warning("soccer market meta %s: %s", slug, exc)
            r = recs[slug] = settle(slug, r)          # the CLOB metadata calls take time too
            if slug in self.resync or (slug not in frozen and self.records.get(slug) != r):
                self.games_sink.write(r)
                self.records[slug] = r
                self.resync.discard(slug)
        for slug in [s for s, r in self.records.items() if (r.get("start_ts") or now) < now - KEEP_S]:
            del self.records[slug]
        n = Counter(r["match"] for r in recs.values())
        log.info("soccer: %d events, %d three-way (%s)", len(events), len(recs),
                 ", ".join(f"{k} {v}" for k, v in sorted(n.items())))
        return {"leagues": leagues, "game_ids": {x: now + 12 * 3600 for x in gids},
                "counts": dict(n), "active": self._active(now)}

    def _refresh_meta(self, r: dict, g: dict | None, frozen: bool) -> None:
        """market_meta for each leg (fee from the current Gamma event); fills leg tick/size/delay."""
        for name, leg in r["legs"].items():
            fee = ((g or {}).get("legs") or {}).get(_leg_key(r, name, g)) or leg
            m = self.meta.update(leg["condition_id"], "soccer", fee.get("fee_rate"), fee.get("fee_exponent"))
            if m and not frozen:
                for k in ("tick", "min_size", "seconds_delay"):
                    if m.get(k) is not None:
                        leg[k] = m[k]

    def _active(self, now: float) -> dict:
        out = {}
        for r in self.records.values():
            e = r.get("espn")
            if r.get("match") == "exact" and e and e.get("kickoff_ts") and now <= e["kickoff_ts"] + KEEP_S:
                out[str(e["id"])] = {"path": e["path"], "espn_id": str(e["id"]), "kickoff_ts": e["kickoff_ts"],
                                     "event_slug": r["event_slug"], "dates": espn_dates(e["kickoff_ts"])}
        return out

    def _tokens(self) -> dict:
        """Yes token -> expiry for every exactly matched game: ESPN final + TOKEN_GRACE_S, capped at
        the end of its ESPN polling + TOKEN_GRACE_S, so no trigger can outlive its books."""
        out = {}
        for r in self.records.values():
            e = r.get("espn") or {}
            k = [x for x in (r.get("start_ts"), e.get("kickoff_ts")) if x]
            if r.get("match") != "exact" or not e.get("id") or not k:
                continue
            exp = max(k) + KEEP_S + TOKEN_GRACE_S
            fin = (self.espn.get(str(e["id"])) or {}).get("final_at")
            if fin is not None:
                exp = min(exp, fin + TOKEN_GRACE_S)
            for leg in (r.get("legs") or {}).values():
                if leg and leg.get("yes_token"):
                    out[leg["yes_token"]] = exp
        return out

    def apply(self, res: dict, now: float) -> None:
        """Publish a discovery result to the shared state (event-loop thread)."""
        self.ref.setdefault("soccer_tokens", {}).update(self._tokens())
        self.ref["soccer_leagues"] = set(self.ref.get("soccer_leagues") or ()) | res["leagues"]
        ids = {k: v for k, v in (self.ref.get("soccer_game_ids") or {}).items() if v > now}
        ids.update(res["game_ids"])
        self.ref["soccer_game_ids"] = ids
        retoken(self.ref, now)
        self.active = res["active"]
        for eid in [e for e, st in self.espn.items() if e not in self.active and now - st["t"] > 12 * 3600]:
            del self.espn[eid]
        for k in [k for k, t in self._warned.items() if now - t > 86400]:
            del self._warned[k]

    async def discover_loop(self, stop: float) -> None:
        while time.time() < stop:
            try:
                res = await asyncio.to_thread(self.discover, time.time())
                self.apply(res, time.time())
            except Exception as exc:
                log.warning("soccer discovery: %s", exc)
            await asyncio.sleep(max(0.0, min(DISCOVER_S, stop - time.time())))

    # -- ESPN polling ---------------------------------------------------------------------------
    @staticmethod
    def _key(r: dict) -> tuple:
        return (r.get("state"), r.get("period"), r.get("display_clock"), r.get("home"), r.get("away"))

    def _emit(self, eid: str, path: str, recv_ms: int, s: dict, home, away, new: list, src: str,
              revised: list | None = None) -> None:
        st = self._st(eid)
        rec = {"espn_id": eid, "path": path, "state": s["state"], "period": s["period"], "clock_s": s["clock_s"],
               "display_clock": s["display_clock"], "home": home, "away": away, "new_events": new,
               "src": src, "status": s["status"]}
        if revised:
            rec["revised_events"] = revised
        key = self._key(rec)
        st["t"] = time.time()
        if new or revised or key != st["key"]:
            self.espn_sink.write(rec, recv_ms=recv_ms)
            st["key"] = key

    def on_scoreboard(self, path: str, date: str, recv_ms: int, js: dict | None) -> None:
        games = espn_games(js, path)
        self.sb_cache[(path, date)] = (recv_ms / 1000, games)
        for c in games:
            if c["id"] not in self.active:
                continue
            st, s = self._st(c["id"]), c["status"]
            k = (s["state"], s["period"], s["clock_s"], s["display_clock"])
            if k != st["sb_key"]:                      # the board is advancing
                st["sb_key"], st["t_prog"] = k, recv_ms / 1000
            if s["state"] == "post" and st["post_since"] is None:
                st["post_since"] = recv_ms / 1000
            if not st["summary"] and late_second_half(s):
                st["summary"] = True
            if not st["summary"]:
                self._emit(c["id"], path, recv_ms, s, c["home_score"], c["away_score"], [], "scoreboard")
                if s["state"] == "post":
                    self._final(st, recv_ms / 1000)

    def on_summary(self, path: str, eid: str, recv_ms: int, js: dict | None) -> None:
        s, home, away = summary_state(js)
        st, new, revised = self._st(eid), [], []
        for ev in key_events(js):
            if ev["fp"] in st["seen"]:
                continue
            st["seen"].add(ev["fp"])
            prev = st["ids"].get(ev["id"]) if ev["id"] else None
            if ev["id"]:
                st["ids"][ev["id"]] = ev["fp"]
            if prev is not None:                       # same ESPN event, edited: received before
                revised.append({**ev, "prev_fp": prev})
            else:
                new.append(ev)
        self._emit(eid, path, recv_ms, s, home, away, new, "summary", revised)
        if s["state"] == "post":
            self._final(st, recv_ms / 1000)

    def due(self, now: float) -> list[tuple]:
        """Jobs to launch now: ('sb', path, date) and ('sum', path, espn_id)."""
        games = [g for g in self.active.values()
                 if g["kickoff_ts"] - PRE_S <= now <= g["kickoff_ts"] + KEEP_S and not self._st(g["espn_id"])["final"]]
        summ = []
        for g in games:
            st = self._st(g["espn_id"])
            if not st["summary"] and now >= g["kickoff_ts"] + FALLBACK_FROM_S and now - st["t_prog"] > STALE_SB_S:
                st["summary"] = True                   # scoreboard silent or not advancing: use the summary
            if st["summary"] and st["post_since"] is not None and now - st["post_since"] > 300:
                self._final(st, now)                   # scoreboard says final for 5 min
            elif st["summary"]:
                summ.append(g)
        sbs = sorted({(g["path"], d) for g in games for d in g["dates"]})
        sb_every, sum_every = intervals(len(summ), len(sbs))
        jobs = []
        for p, d in sbs:
            if now - self.last_sb.get((p, d), 0.0) >= sb_every:
                jobs.append((("sb", p, d), "sb", p, d))
        for g in summ:
            if now - self._st(g["espn_id"])["t_sum"] >= sum_every:
                jobs.append((("sum", g["espn_id"]), "sum", g["path"], g["espn_id"]))
        jobs = [j for j in jobs if j[0] not in self.inflight]
        jobs.sort(key=lambda j: j[1] != "sum")        # summaries first: they carry the triggers
        return jobs[:max(0, MAX_INFLIGHT - len(self.inflight))]

    def _fetch(self, url: str, params: dict) -> tuple[int, dict]:
        js = self.get(url, params, retries=2, timeout=8)
        return int(time.time() * 1000), js

    async def _job(self, job: tuple) -> None:
        key, kind, path, arg = job
        try:
            if kind == "sb":
                recv_ms, js = await asyncio.to_thread(
                    self._fetch, ESPN.format(path=path, kind="scoreboard"), {"dates": arg, "limit": 400})
                self.on_scoreboard(path, arg, recv_ms, js)
            else:
                recv_ms, js = await asyncio.to_thread(self._fetch, ESPN.format(path=path, kind="summary"),
                                                      {"event": arg})
                self.on_summary(path, arg, recv_ms, js)
        except Exception as exc:
            self._warn(key, f"espn {kind} {path} {arg}: {exc}"[:300])
        finally:
            self.inflight.discard(key)

    async def espn_loop(self, stop: float) -> None:
        while time.time() < stop:
            try:
                now = time.time()
                for job in self.due(now):
                    key = job[0]
                    if job[1] == "sb":
                        self.last_sb[(job[2], job[3])] = now
                    else:
                        self._st(job[3])["t_sum"] = now
                    self.inflight.add(key)
                    t = asyncio.create_task(self._job(job))
                    self._tasks.add(t)
                    t.add_done_callback(self._tasks.discard)
                for k in [k for k, t in self.last_sb.items() if now - t > 6 * 3600]:
                    del self.last_sb[k]
                for k, (t, _) in list(self.sb_cache.items()):     # discovery thread also writes it
                    if now - t > 3600:
                        self.sb_cache.pop(k, None)
            except Exception as exc:
                self._warn("loop", f"espn loop: {exc}")
            await asyncio.sleep(0.5)


def _leg_key(r: dict, name: str, g: dict | None) -> str:
    """Which slug leg ('a'/'b'/'draw') of the fresh Gamma parse is record leg `name`."""
    if name == "draw" or not g:
        return name
    cid = r["legs"][name]["condition_id"]
    return next((k for k, v in g["legs"].items() if v.get("condition_id") == cid), name)
