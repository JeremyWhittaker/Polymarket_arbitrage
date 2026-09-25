"""MLB leader-discount policies (mlb_10c, mlb_03c, mlb_10c_delay2) per the activation addendum.

Signal: the first received linescore with three outs, or a new inning/half when the three-out
state fell between polls. It is labelled as the next half-inning with 0 outs and empty bases
(runner on second in regular-season innings beyond the scheduled count), exactly like the
historical checkpoints; the lead comes from the runs in the same response.

Admission is by pregame metadata: per market, only the latest mlb_map record received at or
before its scheduled start (and before any signal of that game was received) counts, and it must be
`exact`. Later records (restarts, rollover rewrites, late resolution) never admit, re-orient or
drop a game; games first mapped after the start are logged as `discovered_after_start`.
Days without mlb_map.jsonl get a shakedown-only legacy mapping from games.jsonl + the MLB
schedule (collect.match_mlb). A signal seen while the season's model snapshot is missing is
logged but does not use up the game's first signal (the trigger cannot be evaluated). Build and
log each season's snapshot (`paper snapshot`) before that season's first mapped game, spring
training included; later-logged or unpinned snapshots label decisions shakedown.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ..collect import match_mlb
from . import spec
from .engine import EPS, PRUNE_MS, WARMUP_MS, is_pregame, mlb_map_key, mlb_map_start
from .mlb_model import Snapshot, snapshot_path

log = logging.getLogger("pmsports")


def _val(v):
    return None if v is None or (isinstance(v, float) and v != v) else v


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def label_half(half) -> str | None:
    """Linescore 'Top'/'Bottom' -> baseline 'top'/'bottom'."""
    h = str(half or "").strip().lower()
    return h if h in ("top", "bottom") else None


def next_label(inning: int, half: str) -> tuple[int, str]:
    return (inning, "bottom") if half == "top" else (inning + 1, "top")


def game_over(label: tuple[int, str], diff: int, scheduled: int) -> bool:
    """No such half is played: home already leads in the last scheduled bottom or later, or extras decided."""
    inning, half = label
    return (half == "bottom" and inning >= scheduled and diff > 0) or (half == "top" and inning > scheduled and diff != 0)


class MLBStrategy:
    streams = ("mlb_map", "games", "mlb")

    def __init__(self, engine, model_dir: Path, allow_network: bool = False):
        self.e, self.model_dir, self.allow_network = engine, Path(model_dir), allow_network
        self.policies = spec.policies("mlb")
        self.by_cid: dict[str, dict] = {}            # latest pregame mlb_map record per Polymarket market
        self.pk_cids: dict[int, set[str]] = {}       # game_pk -> markets whose pregame record names it
        self.late: dict[int, int] = {}               # game_pk -> recv_ms of an exact record after the start
        self.last_map_ms = None
        self.legacy: dict[str, dict] = {}
        self._legacy, self._legacy_ver = (-1, {}), 0
        self._schedules: pd.DataFrame | None = None
        self.games: dict[int, dict] = {}
        self.done: set[tuple[str, int]] = set()
        self.models: dict[int, tuple] = {}           # max_season -> (file signature, Snapshot)
        self.missing_models: set[int] = set()

    # --- inputs
    def on_record(self, stream: str, rec: dict, ms: int) -> None:
        if stream == "mlb":
            return self.linescore(rec, ms)
        if stream == "mlb_map":
            pm = rec.get("pm") or {}
            self.last_map_ms = ms
            self.e.state.update_meta(pm.get("condition_id"), pm, ms, "mlb_map")   # venue rules: latest
            cid, pk = mlb_map_key(rec), _int(rec.get("game_pk"))
            if not is_pregame(ms, mlb_map_start(rec)) or (pk is not None and (self.games.get(pk) or {}).get("labels")):
                if pk is not None and rec.get("match") == "exact":
                    self.late[pk] = ms
                return
            old = self.by_cid.get(cid)
            if old is not None and old["_pk"] is not None:
                self.pk_cids.get(old["_pk"], set()).discard(cid)
            self.by_cid[cid] = rec | {"_recv_ms": ms, "_pk": pk}
            if pk is not None:
                self.pk_cids.setdefault(pk, set()).add(cid)
        elif stream == "games" and isinstance(rec.get("game"), dict) and rec["game"].get("condition_id"):
            g = rec["game"]
            self.legacy[str(g["condition_id"])] = g | {"_recv_ms": ms}
            self._legacy_ver += 1
            self.e.state.update_meta(g["condition_id"], g, ms, "games")

    def prune(self, now_ms: int) -> None:
        """Drop games idle longer than PRUNE_MS (beyond any restart warmup)."""
        for pk in [pk for pk, g in self.games.items() if now_ms - g["ms"] > PRUNE_MS]:
            del self.games[pk]
            self.done -= {(p, pk) for p in self.policies}
        for cid in [c for c, g in self.legacy.items() if now_ms - g["_recv_ms"] > PRUNE_MS]:
            del self.legacy[cid]
            self._legacy_ver += 1
        for cid in [c for c, r in self.by_cid.items() if now_ms - r["_recv_ms"] > PRUNE_MS]:
            r = self.by_cid.pop(cid)
            if r["_pk"] is not None:
                self.pk_cids.get(r["_pk"], set()).discard(cid)
        for pk in [pk for pk, t in self.late.items() if now_ms - t > PRUNE_MS]:
            del self.late[pk]

    def model(self, max_season: int) -> Snapshot | None:
        """The snapshot file as it is now; a missing or unreadable file is retried on the next
        signal, and a replaced file is reloaded (labels then flag the unpinned hash)."""
        path = snapshot_path(max_season, self.model_dir)
        try:
            st = path.stat()
            sig = (st.st_mtime_ns, st.st_size)
            hit = self.models.get(max_season)
            if hit is None or hit[0] != sig:
                self.models[max_season] = hit = (sig, Snapshot.load(max_season, self.model_dir))
            self.missing_models.discard(max_season)
            return hit[1]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.models.pop(max_season, None)
            if max_season not in self.missing_models:
                self.missing_models.add(max_season)
                log.warning("no usable MLB model snapshot %s: %s", path, exc)
            return None

    def mapping(self, pk: int, ms: int) -> tuple[dict | None, str]:
        """Exact only: one market's latest mlb_map record names this game_pk with match 'exact'."""
        recs = [self.by_cid[c] for c in sorted(self.pk_cids.get(pk, ()))]
        if recs:
            if len(recs) > 1:
                return None, "ambiguous"
            rec = recs[0]
            pm = rec.get("pm") or {}
            if rec.get("match") != "exact" or not pm.get("condition_id"):
                return None, str(rec.get("match") or "unmatched")
            return dict(source="mlb_map", condition_id=str(pm["condition_id"]), slug=pm.get("slug"),
                        home_token=str(pm["home_token"]), away_token=str(pm["away_token"]),
                        game_type=rec.get("game_type"), scheduled_innings=int(rec.get("scheduled_innings") or 9),
                        start_ts=rec.get("mlb_start_ts") or pm.get("start_ts"),
                        home_name=rec.get("home_name"), away_name=rec.get("away_name")), "exact"
        if pk in self.late:
            return None, "discovered_after_start"
        if self.last_map_ms is not None and ms - self.last_map_ms <= WARMUP_MS:
            return None, "unmapped"          # mlb_map capture is live: no legacy fallback
        m = self._legacy_map().get(pk)
        return (m, "legacy") if m else (None, "unmapped")

    # --- legacy (shakedown-only) mapping
    def _schedule(self, dates: list[str]) -> pd.DataFrame | None:
        if self._schedules is None:
            frames = [pd.read_parquet(p) for p in sorted(self.e.live_root.glob("*/schedule.parquet"))]
            self._schedules = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        s = self._schedules
        have = set(pd.to_datetime(s.official_date).dt.strftime("%Y-%m-%d")) if len(s) else set()
        if not set(dates) <= have and self.allow_network:
            try:
                from ..mlb import schedule
                lo, hi = min(dates), max(dates)
                s = pd.concat([s, pd.DataFrame(schedule(lo, hi))], ignore_index=True)
                self._schedules = s
            except Exception as exc:          # shakedown-only path; the game stays unmapped
                log.warning("legacy MLB schedule %s: %s", dates, exc)
        return s.drop_duplicates("game_pk", keep="last") if len(s) else None

    def _legacy_map(self) -> dict[int, dict]:
        if self._legacy[0] == self._legacy_ver:
            return self._legacy[1]
        out = {}
        pmg = pd.DataFrame([{k: v for k, v in g.items() if k != "_recv_ms"} for g in self.legacy.values()])
        if len(pmg):
            days = sorted({str(d)[:10] for d in pmg.event_date.dropna()})
            dates = sorted({(pd.Timestamp(d) + pd.Timedelta(days=k)).strftime("%Y-%m-%d") for d in days for k in (-1, 0, 1)})
            sched = self._schedule(dates)
            if sched is not None:
                m = match_mlb(pmg, sched.assign(abstract_state="Final")).dropna(subset=["game_pk"])
                n = m.game_pk.value_counts()
                for r in m.to_dict("records"):
                    pk = int(r["game_pk"])
                    if n[pk] == 1:
                        r = {k: _val(v) for k, v in r.items()}
                        out[pk] = dict(source="legacy", condition_id=str(r["condition_id"]), slug=r.get("slug"),
                                       home_token=str(r["home_token"]), away_token=str(r["away_token"]),
                                       game_type=r.get("game_type"), scheduled_innings=int(r.get("scheduled_innings") or 9),
                                       start_ts=r.get("game_ts") or r.get("start_ts"),
                                       home_name=r.get("home_name"), away_name=r.get("away_name"))
        self._legacy = (self._legacy_ver, out)
        return out

    # --- signals
    def linescore(self, rec: dict, ms: int) -> None:
        try:
            pk, inning, outs = int(rec["game_pk"]), int(rec["inning"]), int(rec["outs"])
            away, home = int(rec["away"]), int(rec["home"])
        except (KeyError, TypeError, ValueError):
            return
        half = label_half(rec.get("half"))
        if half is None:
            return
        self.e.cover(f"mlb|{pk}", ms)
        g = self.games.setdefault(pk, dict(prev=None, labels=set(), n=0, ms=ms))
        g["n"] += 1
        g["ms"] = ms
        prev, g["prev"] = g["prev"], (inning, half, outs)
        if outs == 3:
            if prev == (inning, half, 3):
                return
            label, source = next_label(inning, half), "three_outs"
        elif prev is not None and prev[:2] != (inning, half):
            label, source = (inning, half), "half_change"
        else:
            return
        if label in g["labels"]:
            return
        g["labels"].add(label)
        self.signal(pk, label, source, home, away, ms, g)

    def signal(self, pk: int, label, source: str, home: int, away: int, ms: int, g: dict) -> None:
        inning, half = label
        diff = home - away
        state = dict(inning=inning, half=half, outs=0, bases=0, diff=diff, home=home, away=away, source=source)
        sig = dict(sport="mlb", game_id=str(pk), signal_id=f"{inning}{half}", recv_ms=ms, **state)
        mapping, how = self.mapping(pk, ms)
        sig["mapping"] = how
        if mapping is None:
            return self.e.log_signal(sig | dict(status="discovered_after_start" if how == "discovered_after_start" else "unmapped"))
        sched = mapping["scheduled_innings"]
        if game_over(label, diff, sched):
            return self.e.log_signal(sig | dict(status="game_over"))
        sig["bases"] = state["bases"] = bases = 2 if inning > sched and mapping["game_type"] == "R" else 0
        if diff == 0:
            return self.e.log_signal(sig | dict(status="tied"))
        leader = "home" if diff > 0 else "away"
        token = mapping[f"{leader}_token"]
        start = mapping["start_ts"]
        season = datetime.fromtimestamp(start if start else ms / 1000, timezone.utc).year
        model = self.model(season - 1)
        fair = model.fair_home(inning, half, 0, bases, diff) if model else None
        lp = None if fair is None else (fair if diff > 0 else 1 - fair)
        ref = self.e.state.reference(token, ms)
        edge = None if lp is None or ref is None else lp - ref.price
        sig.update(status="evaluated", leader=leader, token=token, fair_home=fair, leader_prob=lp,
                   reference_price=ref.price if ref else None, reference_age_ms=ms - ref.ms if ref else None, edge=edge)
        evals = {}
        for policy in self.policies:
            r = spec.RULES[policy]
            if (policy, pk) in self.done:
                evals[policy] = "after_first_signal"
                continue
            if model is None:                  # no fair value: the trigger cannot be evaluated
                evals[policy] = "missing_model_snapshot"
                continue
            if ref is None or edge < r["threshold"] - EPS:
                evals[policy] = "no_reference" if ref is None else "below_threshold"
                continue
            self.done.add((policy, pk))
            evals[policy] = "qualified"
            rec = dict(policy=policy, sport="mlb", game_id=str(pk), game_pk=pk, event=mapping["slug"],
                       condition_id=mapping["condition_id"], token=token, side=leader,
                       team=mapping.get(f"{leader}_name"), home_name=mapping.get("home_name"),
                       away_name=mapping.get("away_name"), scheduled_start_ts=start, game_type=mapping["game_type"],
                       mapping_source=mapping["source"], signal=dict(state), signal_recv_ms=ms,
                       model_max_season=season - 1, model_sha256=model.sha256,
                       fair_home=fair, leader_prob=lp, threshold=r["threshold"], edge=edge,
                       reference_price=ref.price if ref else None, reference_size=ref.size if ref else None,
                       reference_recv_ms=ref.ms if ref else None, reference_age_ms=ms - ref.ms if ref else None)
            self.e.enter(rec, legacy_rules=mapping["source"] == "legacy", game=g)
        self.e.log_signal(sig | dict(evals=evals))
