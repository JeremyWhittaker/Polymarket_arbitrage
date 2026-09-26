"""US-venue paper engine: the frozen v1 engine and strategies, composed with the US venue.

`USEngine` subclasses the v1 `Engine` without changing it. The v1 `MLBStrategy` and
`SoccerStrategy` run unchanged (half-inning detection, model snapshot, ESPN key-event trigger,
windows, band, first-signal-per-game) and see `engine.state.reference(token, t)` answered by the
US venue: the intl token names a game side, the pregame us_map names that side's US market and
orientation, and the reference is the last US trade on that side. Everything they emit is
translated to the US policy names (spec.TRIGGERS); entries, freshness, execution, labels and the
decision log are US-specific. A game with no exact pregame us_map is outside the US universe: its
signals are logged with the US mapping reason and use no attempt. A us_map record received after
the international game was seen in play is late whatever its listed start: for MLB that is the
frozen strategy's own mlb_map guard (a half-inning of the game was labelled), applied here by
`process`; for soccer the venue reads ESPN's in-play state.

Receipt order is the v1 Replayer's with US ranks for equal recv_ms (metadata, then US books and
trades, then game state). Decisions use only records with recv_ms <= the decision time; replaying
the same files gives the same decisions, and a restart replays 36 h and skips written keys.
Order previews are not part of this module: an optional `preview` hook receives a copy of each
newly written executed entry and can never change a decision.
"""
from __future__ import annotations

import hashlib
import heapq
import json
import logging
from pathlib import Path

from ...collect import DATA_DIR
from .. import engine as v1
from ..engine import (AUDIT_MS, LIVE, ROOT, SNAPSHOT_LOG, Engine, Pending, Replayer, State, floor_tick)
from ..mlb import MLBStrategy
from . import spec
from .venue import OPEN_STATES, Venue, order_quantity, start_s

log = logging.getLogger("pmsports")
PAPER_US = DATA_DIR / "paper_us"
ACTIVATION_US = ROOT / "reports" / "paper" / "ACTIVATION_US.json"
# Equal recv_ms: metadata first, then US books/trades, then game-state signals.
STREAMS = {"mlb_map": 0, "us_map": 1, "soccer_games": 2, "games": 3, "us_book": 4, "us_trade": 5, "mlb": 6, "espn": 7}
# Decision code (pinned by the US activation manifest). us_api.py is the only authenticated client.
US_DECISION_FILES = ("paper/us/__init__.py", "paper/us/spec.py", "paper/us/venue.py", "paper/us/engine.py",
                     "paper/us_api.py")
US_SUPPORT_FILES = ("paper/us/preview.py", "paper/us/settle.py", "paper/us/report.py", "paper/us/run.py",
                    "paper/capture_us.py", "record.py")


def code_files() -> list[Path]:
    pkg = ROOT / "pmsports"
    return [pkg / f for f in US_DECISION_FILES] + v1.code_files()


def code_hashes() -> dict:
    files = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in code_files()}
    fee = v1.code_hashes()["taker_fee_sha256"]
    combined = hashlib.sha256(json.dumps({"files": files, "taker_fee": fee}, sort_keys=True).encode()).hexdigest()
    return {"files": files, "taker_fee_sha256": fee, "code_sha256": combined}


def support_hashes() -> dict:
    pkg = ROOT / "pmsports"
    return {f"pmsports/{f}": hashlib.sha256((pkg / f).read_bytes()).hexdigest()
            for f in US_SUPPORT_FILES if (pkg / f).exists()}


class USReplayer(Replayer):
    """v1 Replayer over the streams the US engine reads, with US tie ranks."""

    def __init__(self, files, start_ms=None, end_ms=None, follow=False, streams=tuple(STREAMS), **kw):
        super().__init__(files, start_ms, end_ms, follow, streams=streams, **kw)

    def _discover(self) -> None:
        super()._discover()
        for s, c in self.chains.items():
            c.rank = STREAMS.get(s, 99)


class TriggerState(State):
    """`engine.state` as the frozen strategies see it: references come from the US venue."""

    def __init__(self, venue: Venue):
        super().__init__()
        self.venue = venue

    def reference(self, token: str, t: int, max_age_ms: int = v1.REF_MS):
        return self.venue.reference_for_token(token, t, max_age_ms)


class USEngine(Engine):
    def __init__(self, out_dir: Path = PAPER_US, live_root: Path = LIVE, activation_path: Path | None = ACTIVATION_US,
                 snapshot_log: Path | None = SNAPSHOT_LOG, model_dir: Path | None = None, allow_network: bool = False,
                 preview=None):
        super().__init__(out_dir, live_root, activation_path, snapshot_log, model_dir, allow_network=allow_network)
        self.venue = Venue(cover=self.cover)
        self.state = TriggerState(self.venue)
        self.code_sha, self.spec_sha = code_hashes()["code_sha256"], spec.spec_hash()
        self.preview = preview
        for name in self.venue.streams:
            self.routes.setdefault(name, []).append(self.venue)
        self.strategies.append(self.venue)              # pruned with the strategies
        self.mlb = next(s for s in self.strategies if isinstance(s, MLBStrategy))

    def process(self, ms: int, stream: str, rec: dict) -> None:
        super().process(ms, stream, rec)
        if stream == "mlb":
            # v1's mlb_map guard (mlb.py): once any half-inning of the game has been labelled, the
            # game is in play and a US mapping received from now on is late.
            try:
                pk = int(rec.get("game_pk"))
            except (TypeError, ValueError):
                return
            if (self.mlb.games.get(pk) or {}).get("labels"):
                self.venue.mark_in_play("mlb", str(pk), ms)

    # --- translation of the frozen strategies' output
    def _us_fields(self, rec: dict) -> tuple[dict | None, str]:
        """Attach the US event/market for the record's game (and side, when known)."""
        us, status = self.venue.admission(rec["sport"], str(rec["game_id"]))
        rec.update(venue=spec.VENUE, us_mapping=status, us_event_slug=(us or {}).get("us_event_slug"))
        side = rec.get("side") or rec.get("leader")
        if us is not None and side in us["_legs"]:
            leg = us["_legs"][side]
            rec.update(market_slug=leg.slug, us_side=leg.us_side)
        return us, status

    def log_signal(self, rec: dict) -> None:
        _, status = self._us_fields(rec)
        evals = rec.get("evals")
        if isinstance(evals, dict):
            rec["evals"] = {spec.US_NAME.get(p, p): (status if status != "exact" and v in ("no_reference", "rejected") else v)
                            for p, v in evals.items()}
        super().log_signal(rec)

    def _translate(self, rec: dict) -> str | None:
        """v1 policy -> US policy; the intl market/token are kept as intl_* audit fields."""
        v1p = rec["policy"]
        rec.update(policy=spec.US_NAME[v1p], trigger_policy=v1p, intl_condition_id=rec.pop("condition_id", None),
                   intl_token=rec.pop("token", None), intl_event=rec.get("event"))
        us, status = self._us_fields(rec)
        if us is not None:
            rec.update(event=us.get("us_event_slug"), us_start_ts=start_s(us), us_map_recv_ms=us["_recv_ms"])
        return status

    def reject(self, rec: dict, reason: str, decided_ms: int) -> None:
        if rec.get("policy") in spec.US_NAME:           # a frozen strategy's own no-fill (missing leader leg)
            if self._translate(rec) != "exact":
                return                                   # outside the US universe: signals.jsonl carries it
        super().reject(rec, reason, decided_ms)

    def enter(self, rec: dict, legacy_rules: bool = False, game: dict | None = None) -> None:
        """Signal-time US checks; schedule the IOC at receipt + the policy's delay."""
        t = rec["signal_recv_ms"]
        status = self._translate(rec)
        policy = rec["policy"]
        r = spec.RULES[policy]
        leg, _, why = self.venue.leg(rec["sport"], str(rec["game_id"]), rec["side"])
        if leg is None:
            return self.reject(rec, why if status == "exact" else status, t)
        fee = leg.fee_coefficient if leg.fee_coefficient is not None else r["fee_coefficient"]
        inc = leg.qty_increment if leg.qty_increment and leg.qty_increment > 0 else r["quantity_increment_default"]
        rec.update(intent=leg.intent, tick=leg.tick, min_qty=leg.min_qty, qty_increment=inc, fee_coefficient=fee,
                   fee_source="us_map" if leg.fee_coefficient is not None else "spec", fee_rate=fee)
        if leg.tick is None or not 0 < leg.tick < 1:
            return self.reject(rec, "missing_market_rules:tick", t)
        if not 0 <= fee < 1:
            return self.reject(rec, "invalid_fee_coefficient", t)
        rec["limit"] = limit = floor_tick(rec["reference_price"] + r["limit_offset"], leg.tick)
        if not 0 < limit < 1:
            return self.reject(rec, "invalid_limit", t)
        qty = order_quantity(limit, r["budget_usd"], fee, inc)
        rec.update(quantity=qty, order_price_value=limit if leg.long else round(1 - limit, 10))
        delay = spec.delay_ms(policy)
        rec.update(delay_ms=delay, eligible_ms=t + delay)
        self.seq += 1
        heapq.heappush(self.pending, Pending(t + delay, self.seq, rec, game, game["n"] if game else 0))

    def _execute(self, p: Pending) -> None:
        rec, t = p.rec, p.eligible_ms
        policy = rec["policy"]
        if p.game is not None:
            rec["state_changes_during_delay"] = p.game["n"] - p.n_at_signal
        leg, _, why = self.venue.leg(rec["sport"], str(rec["game_id"]), rec["side"])
        f = self.venue.freshness(rec["market_slug"], t)
        rec.update(connection_ok=f["connection_ok"], book_ms=f["book_ms"], book_age_ms=f["book_age_ms"],
                   market_state=f["market_state"],
                   conn={k: f[k] if f[k] is not None and t - f[k] <= AUDIT_MS else None
                         for k in ("conn_open_ms", "conn_activity_ms", "conn_closed_ms")})
        if leg is None or leg.slug != rec["market_slug"]:    # cannot happen: pregame admission is frozen
            return self.reject(rec, "us_mapping_changed", t)
        if not f["ok"]:
            return self._write_entry(self.reject_rec(rec, f["reason"]), t)
        state = f["market_state"]
        if state is not None and state not in OPEN_STATES:
            return self._write_entry(self.reject_rec(rec, f"market_state:{state}"), t)
        qty, min_qty = rec["quantity"], rec.get("min_qty")
        if qty <= 0 or (min_qty is not None and qty < min_qty - 1e-12):
            return self._write_entry(self.reject_rec(rec, "below_minimum_order"), t)
        fill = self.venue.cross(leg, t, policy, rec["limit"], qty, float(rec["fee_coefficient"]), min_qty,
                                spec.RULES[policy]["budget_usd"])
        rec.update(ask_at_exec=fill.pop("ask_at_exec"), bid_at_exec=fill.pop("bid_at_exec"), fill=fill,
                   status=fill["status"], reason=fill["reason"], shares=fill["shares"], price=fill["price"],
                   gross_usd=fill["gross_usd"], fee_usd=fill["fee_usd"], cost_usd=fill["cash_usd"],
                   fee_usd_us=fill["fee_usd"])
        self._write_entry(rec, t)

    @staticmethod
    def reject_rec(rec: dict, reason: str) -> dict:
        rec.update(status="rejected", reason=reason)
        return rec

    def _write_entry(self, rec: dict, t: int) -> None:
        """Write an executed entry; a newly written one goes to the preview hook (never back)."""
        if self._write(rec, t) and self.preview is not None:
            try:
                self.preview.submit(json.loads(json.dumps(v1._clean(rec))))
            except Exception as exc:                     # the audit can never stop or change the engine
                log.warning("preview hook failed for %s: %r", rec.get("key"), exc)

    # --- labels and output
    def shakedown_reasons(self, rec: dict) -> list[str]:
        a, why = self.activation, []
        if not a:
            why.append("not_activated")
        else:
            if a.get("code_sha256") != self.code_sha:
                why.append("code_hash_mismatch")
            if a.get("spec_sha256") != self.spec_sha:
                why.append("spec_hash_mismatch")
            starts = [x for x in (v1._num(rec.get("scheduled_start_ts")), v1._num(rec.get("us_start_ts"))) if x is not None]
            if not starts or min(starts) * 1000 < a.get("activated_ms", float("inf")):   # both venues' starts
                why.append("scheduled_before_activation")
        if rec.get("mapping_source") == "legacy":
            why.append("legacy_mapping")
        if rec.get("connection_ok") == "unknown":
            why.append("connection_markers_missing")
        if a and rec.get("sport") == "mlb" and rec.get("model_sha256") and (m := self._model_reason(rec)):
            why.append(m)
        return why

    def _write(self, rec: dict, decided_ms: int) -> bool:
        rec.setdefault("shares", 0.)
        rec.setdefault("cost_usd", 0.)
        why = self.shakedown_reasons(rec)
        rec.update(key=f"{rec['policy']}|{rec['game_id']}", decided_ms=decided_ms, spec_version=spec.VERSION,
                   code_sha256=self.code_sha, spec_sha256=self.spec_sha, shakedown=bool(why), shakedown_reasons=why)
        return self.decisions.write(rec)

