"""MLB Stats API (statsapi.mlb.com, public): schedule + timestamped play-by-play.

Each plate appearance carries Statcast-clock timestamps; the pitch event with
isInPlay marks the moment of contact, which is our "truth" time for latency
studies (how long until Polymarket reprices after a home run).
"""
from __future__ import annotations

from .http import get_json
from .polymarket import parse_ts

API = "https://statsapi.mlb.com/api/v1"

# R regular season, F wild card, D division series, L LCS, W World Series
GAME_TYPES = "R,F,D,L,W"


def schedule(start_date: str, end_date: str) -> list[dict]:
    j = get_json(f"{API}/schedule", {
        "sportId": 1, "startDate": start_date, "endDate": end_date,
        "gameType": GAME_TYPES, "hydrate": "team,linescore",
    })
    rows = []
    for d in j.get("dates", []):
        for g in d.get("games", []):
            a, h = g["teams"]["away"], g["teams"]["home"]
            rows.append({
                "game_pk": g["gamePk"],
                "official_date": g.get("officialDate") or d["date"],
                "game_ts": parse_ts(g.get("gameDate")),
                "game_type": g.get("gameType"),
                "status": g["status"].get("detailedState"),
                "abstract_state": g["status"].get("abstractGameState"),
                "double_header": g.get("doubleHeader"),
                "game_number": g.get("gameNumber"),
                "away_id": a["team"]["id"], "home_id": h["team"]["id"],
                "away_name": a["team"].get("name"), "home_name": h["team"].get("name"),
                "away_team_name": a["team"].get("teamName"), "home_team_name": h["team"].get("teamName"),
                "away_abbr": a["team"].get("abbreviation"), "home_abbr": h["team"].get("abbreviation"),
                "away_score": a.get("score"), "home_score": h.get("score"),
                "scheduled_innings": (g.get("linescore") or {}).get("scheduledInnings", 9),
                "final_inning": (g.get("linescore") or {}).get("currentInning"),
            })
    return rows


def _contact_time(play: dict) -> float | None:
    evs = play.get("playEvents") or []
    for ev in reversed(evs):
        if (ev.get("details") or {}).get("isInPlay"):
            return parse_ts(ev.get("startTime"))
    if evs:
        return parse_ts(evs[-1].get("startTime"))
    return parse_ts(play["about"].get("startTime"))


def plays(game_pk: int) -> list[dict]:
    """One row per completed plate appearance, with the post-play game state."""
    j = get_json(f"{API}/game/{game_pk}/playByPlay")
    rows = []
    for i, p in enumerate(j.get("allPlays", [])):
        about, res, cnt = p["about"], p["result"], p.get("count", {})
        if not about.get("isComplete", True):
            continue
        m = p.get("matchup") or {}
        rows.append({
            "game_pk": game_pk,
            "play_idx": i,
            "inning": about.get("inning"),
            "half": about.get("halfInning"),            # 'top' | 'bottom'
            "start_ts": parse_ts(about.get("startTime")),
            "end_ts": parse_ts(about.get("endTime")),
            "contact_ts": _contact_time(p),
            "event": res.get("event"),
            "event_type": res.get("eventType"),
            "is_scoring": bool(about.get("isScoringPlay")),
            "rbi": res.get("rbi"),
            "away_score": res.get("awayScore"),
            "home_score": res.get("homeScore"),
            "outs": cnt.get("outs"),
            "on_1b": "postOnFirst" in m,
            "on_2b": "postOnSecond" in m,
            "on_3b": "postOnThird" in m,
        })
    return rows
