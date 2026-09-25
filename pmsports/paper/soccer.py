"""Soccer added-time leader policies (PROSPECTIVE_SOCCER_PROTOCOL.md).

Trigger: the first newly received ESPN key event (fingerprint not seen before for the game) in
period 2 whose true minute is in the policy window, when the score in that same response shows
a nonzero lead. The game is admitted by its latest soccer_games record received at or before
kickoff (the earlier of Gamma start and ESPN kickoff), which must be `exact`; its legs are in
ESPN home/away orientation. Games first recorded after kickoff are `discovered_after_start`.

Nothing at or after the regulation whistle triggers: once a response is `post`, shows period > 2
or carries an `end-regular-time` / `end-match` event, every event in it and in later responses is
`after_terminal` (the historical rule excluded terminal-whistle rows; these markets settle on
regulation plus stoppage time).

As in the historical rule, an event whose leader reference is missing or outside 0.60-0.97 is
recorded in signals.jsonl and does not use up the game's attempt; the first event that reaches
an order (or a recorded no-fill for missing leg metadata) is the game's only attempt per policy.
"""
from __future__ import annotations

import json
import math

from ..research.h_soccer_continuation import true_minute
from . import spec
from .engine import EPS, PRUNE_MS, _num, is_pregame

LOG_FROM_MINUTE = 70          # signals log: period-2 events from the earliest window onward
TERMINAL = ("end-regular-time", "end-match")


def kickoff(rec: dict):
    """Earliest known kickoff (epoch s) of a soccer_games record: Gamma start or ESPN kickoff."""
    ts = [x for x in (_num(rec.get("start_ts")), _num((rec.get("espn") or {}).get("kickoff_ts"))) if x is not None]
    return min(ts) if ts else None


def terminal(rec: dict, new: list) -> bool:
    """This response is at or after the end of regulation."""
    period, status = _num(rec.get("period")), str(rec.get("status") or "").upper()
    return (rec.get("state") == "post" or (period is not None and period > 2)
            or any(k in status for k in ("FULL_TIME", "FINAL", "END_OF_REG"))
            or any(str(e.get("type") or "") in TERMINAL for _, e in new))


def event_minute(e: dict) -> float:
    try:
        cv = e.get("clock_value")
        base = float(cv) / 60.0 if cv is not None else math.nan
        return true_minute(base, e.get("clock_display") or "")
    except (TypeError, ValueError):
        return math.nan


def in_window(minute: float, window) -> bool:
    lo, hi = window
    return math.isfinite(minute) and minute >= lo - EPS and (hi is None or minute <= hi + EPS)


class SoccerStrategy:
    streams = ("soccer_games", "espn")

    def __init__(self, engine):
        self.e = engine
        self.policies = spec.policies("soccer")
        self.games: dict[str, dict] = {}             # latest pregame soccer_games record per event
        self.late: dict[str, dict] = {}              # latest record received after kickoff (logging only)
        self.by_espn: dict[str, str] = {}
        self.state: dict[str, dict] = {}
        self.done: set[tuple[str, str]] = set()

    def prune(self, now_ms: int) -> None:
        """Drop games idle longer than PRUNE_MS (beyond any restart warmup)."""
        seen = {}
        for s, g in [*self.late.items(), *self.games.items()]:
            seen[s] = max(seen.get(s, 0), g["_recv_ms"], self.state.get(s, {}).get("ms", 0))
        for slug in [s for s, t in seen.items() if now_ms - t > PRUNE_MS]:
            for g in (self.games.pop(slug, None), self.late.pop(slug, None)):
                eid = str(((g or {}).get("espn") or {}).get("id"))
                if self.by_espn.get(eid) == slug:
                    del self.by_espn[eid]
            self.state.pop(slug, None)
            self.done -= {(p, slug) for p in self.policies}

    def on_record(self, stream: str, rec: dict, ms: int) -> None:
        if stream == "espn":
            return self.espn(rec, ms)
        slug = rec.get("event_slug")
        if not slug:
            return
        for leg in (rec.get("legs") or {}).values():
            if isinstance(leg, dict):                # venue rules: latest record
                self.e.state.update_meta(leg.get("condition_id"), leg, ms, "soccer_games")
        eid = (rec.get("espn") or {}).get("id")
        eid = None if eid is None else str(eid)
        if is_pregame(ms, kickoff(rec)):
            old = str(((self.games.get(slug) or {}).get("espn") or {}).get("id"))
            if self.by_espn.get(old) == slug:
                del self.by_espn[old]
            self.games[slug] = rec | {"_recv_ms": ms}
            if eid is not None:
                self.by_espn[eid] = slug
        else:
            self.late[slug] = rec | {"_recv_ms": ms}
            if eid is not None and eid not in self.by_espn and slug not in self.games:
                self.by_espn[eid] = slug             # its state is logged, never traded

    def espn(self, rec: dict, ms: int) -> None:
        slug = self.by_espn.get(str(rec.get("espn_id")))
        if slug is None:
            return
        self.e.cover(f"espn|{rec.get('espn_id')}", ms)
        g = self.games.get(slug)
        if g is not None and str((g.get("espn") or {}).get("id")) != str(rec.get("espn_id")):
            g = None                                 # this ESPN event is not the one mapped before kickoff
        gs = self.state.setdefault(slug, dict(fps=set(), n=0, ms=ms, terminal_ms=None))
        gs["n"] += 1
        gs["ms"] = ms
        new = []
        for e in rec.get("new_events") or ():
            fp = str(e.get("fp") or json.dumps(e, sort_keys=True))
            if fp not in gs["fps"]:
                gs["fps"].add(fp)
                new.append((fp, e))
        if gs["terminal_ms"] is None and terminal(rec, new):
            gs["terminal_ms"] = ms
        try:
            home, away = int(rec.get("home")), int(rec.get("away"))
        except (TypeError, ValueError):
            home = away = None
        lead = None if home is None else home - away
        exact = g is not None and g.get("match") == "exact"
        for fp, e in new:
            if str(e.get("period")) not in ("2", "2.0"):
                continue
            minute = event_minute(e)
            if not (math.isfinite(minute) and minute >= LOG_FROM_MINUTE):
                continue
            sig = dict(sport="soccer", game_id=slug, signal_id=fp, recv_ms=ms, minute=minute,
                       clock_display=e.get("clock_display"), type=e.get("type"), home=home, away=away,
                       espn_state=rec.get("state"), espn_period=rec.get("period"),
                       mapping=g.get("match") if g is not None else "discovered_after_start")
            evals = {}
            for policy in self.policies:
                r = spec.RULES[policy]
                evals[policy] = ("discovered_after_start" if g is None else "unmapped" if not exact
                                 else "after_first_signal" if (policy, slug) in self.done
                                 else "after_terminal" if gs["terminal_ms"] is not None
                                 else "outside_window" if not in_window(minute, r["window"])
                                 else "tied_or_no_score" if not lead else "trigger")
                if evals[policy] == "trigger":
                    evals[policy] = self.trigger(policy, slug, g, gs, e | {"fp": fp}, minute, home, away, rec, ms)
            self.e.log_signal(sig | dict(evals=evals))

    def trigger(self, policy, slug, g, gs, e, minute, home, away, rec, ms) -> str:
        """Reference and band checks; returns the signal's evaluation for this policy."""
        r = spec.RULES[policy]
        leader = "home" if home > away else "away"
        leg = (g.get("legs") or {}).get(leader) or {}
        espn = g.get("espn") or {}
        token = str(leg.get("yes_token")) if leg.get("yes_token") else None
        ref = self.e.state.reference(token, ms) if token else None
        d = dict(policy=policy, sport="soccer", game_id=slug, event=slug, league=espn.get("path") or g.get("series"),
                 condition_id=leg.get("condition_id"), token=token, side=leader, team=espn.get(f"{leader}_name"),
                 home_name=espn.get("home_name"), away_name=espn.get("away_name"), scheduled_start_ts=g.get("start_ts"),
                 kickoff_ts=espn.get("kickoff_ts"), mapping_source="soccer_games",
                 signal=dict(period=2, minute=minute, clock_display=e.get("clock_display"), clock_value=e.get("clock_value"),
                             type=e.get("type"), fp=e.get("fp"), team_id=e.get("team_id"), home=home, away=away,
                             espn_state=rec.get("state")),
                 signal_recv_ms=ms, window=list(r["window"]), band=list(r["band"]),
                 reference_price=ref.price if ref else None, reference_size=ref.size if ref else None,
                 reference_recv_ms=ref.ms if ref else None, reference_age_ms=ms - ref.ms if ref else None)
        lo, hi = r["band"]
        if not token or not leg.get("condition_id"):         # missing metadata: a recorded no-fill
            self.done.add((policy, slug))
            self.e.reject(d, "missing_leader_leg", ms)
            return "rejected"
        if ref is None:
            return "no_reference"
        if not lo - EPS <= ref.price <= hi + EPS:
            return "leader_price_outside_band"
        self.done.add((policy, slug))
        self.e.enter(d, game=gs)
        return "qualified"
