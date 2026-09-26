"""Real received order-book depth at MLB half-inning starts and soccer late key events.

Run: PYTHONPATH=. nice -n 10 .venv/bin/python -m pmsports.research.captured_depth [--until ISO]

This measures depth only. It never places, simulates or scores an order. It replays every
captured day under data/live/ (2026-09-18 and 2026-09-19 in the legacy format without
connection markers; 2026-09-25 onward with markers) through the paper engine. The engine
supplies the Replayer and State (ReceivedBook, last trades, freshness, market rules), the MLB
half-inning detection and mapping, the frozen prior-season fair-value snapshot, and the soccer
ESPN event handling. All of it is imported, not copied or edited. A subclass turns off order
entry and every output file and hooks `log_signal`, which the strategies call once per detected
state.

For each MLB half-inning start with a lead, and each soccer period-2 key event (minute >= 70)
with a lead before the regulation whistle, it reads the leader token's received ask ladder at the
signal receipt time and at +2 s and +5 s (soccer also at +3 s, its protocol entry delay). It
records:
  - dollars (price * size) and shares offered at <= reference + 0, 1, 2, 5, 10 cents, where the
    reference is the rule's last trade received before the signal and at most 120 s old;
  - dollars offered at <= leader fair - 10, 5, 3, 0 cents (MLB), i.e. depth that still carries that
    much model discount;
  - the ask VWAP to sweep $100 / $500 / $1,000 / $5,000 with no limit;
  - best ask, best bid, spread, and best ask minus the reference and minus the last trade at that
    moment;
  - the 10c-rule discount (fair - reference) at the signal, fair minus the last trade at the
    moment, and fair minus the best ask;
  - the top 30 ask levels and top 10 bid levels as received.

Sizes are the displayed quantities as received over the public websocket. Queue position, hidden
liquidity, other takers racing for the same levels, order acceptance and exchange latency are not
observed. This is a few days of real books, a different kind of evidence from the historical
backtest's trade-print proxy (a later same-side print within 5 s), and a small sample.
"""
from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import logging
import math
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from ..collect import DATA_DIR
from ..paper.engine import EPS, LIVE, REF_MS, Engine, Replayer, read_jsonl
from ..paper.mlb import _int, game_over
from ..paper.mlb_model import SNAP_DIR

log = logging.getLogger("pmsports")
ROOT = Path(__file__).resolve().parents[2]
OUT = DATA_DIR / "research" / "performance"
DELAYS_MS = {"mlb": (0, 2000, 5000), "soccer": (0, 2000, 3000, 5000)}
REF_OFFSETS_C = (0, 1, 2, 5, 10)      # cents above the reference (last trade)
FAIR_EDGES_C = (10, 5, 3, 0)          # cents below the leader's model fair value
BUDGETS = (100, 500, 1000, 5000)
ASK_LEVELS, BID_LEVELS = 30, 10
SOCCER_BAND = (0.60, 0.97)
DEPTH_THRESHOLDS = (100, 500, 1000)


def _utc(ms) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


def _f(x) -> float:
    return math.nan if x is None else float(x)


class DepthProbe(Engine):
    """The paper engine with order entry and every output file turned off. It measures the
    received book when a strategy logs a signal, and again at fixed delays afterwards."""

    def __init__(self, live_root: Path = LIVE):
        # out_dir is never created: every writer below is a no-op.
        super().__init__(out_dir=OUT / ".probe_unused", live_root=live_root, activation_path=None,
                         snapshot_log=None, model_dir=SNAP_DIR, allow_network=False)
        self.mlb, self.soccer = self.strategies
        self.timers: list = []
        self.tseq = 0
        self.rows: list[dict] = []
        self.skipped: Counter = Counter()
        self.maps: dict[int, dict] = {}           # game_pk -> latest exact mlb_map record (any time)
        self.mismatch = 0
        self.nobs = 0

    # --- measurement only: no orders, no decision/signal/coverage files
    def enter(self, rec, legacy_rules=False, game=None):
        return None

    def reject(self, rec, reason, decided_ms):
        return None

    def _write(self, rec, decided_ms):
        return None

    def cover(self, key, ms):
        return None

    def process(self, ms, stream, rec):
        if stream == "mlb_map" and rec.get("match") == "exact" and (pk := _int(rec.get("game_pk"))) is not None:
            self.maps[pk] = rec
        super().process(ms, stream, rec)

    def advance(self, watermark):
        # A timer at T runs before the first record with recv_ms > T, i.e. after every record <= T.
        while self.timers and self.timers[0][0] <= watermark:
            t, _, obs, delay = heapq.heappop(self.timers)
            self.measure(obs, delay, t)
        super().advance(watermark)

    def log_signal(self, rec):
        obs = self._mlb(rec) if rec.get("sport") == "mlb" else self._soccer(rec)
        if obs is None:
            return
        obs["obs_seq"] = self.nobs = self.nobs + 1   # log order: breaks ties among events of one response
        t0 = obs["signal_recv_ms"]
        for d in DELAYS_MS[obs["sport"]]:
            if d == 0:
                self.measure(obs, 0, t0)
            else:
                self.tseq += 1
                heapq.heappush(self.timers, (t0 + d, self.tseq, obs, d))

    # --- observations
    def _fallback_map(self, pk: int):
        """A game mapped exactly only after its start (depth is still real; it is flagged)."""
        r = self.maps.get(pk)
        pm = (r or {}).get("pm") or {}
        if not r or not pm.get("condition_id"):
            return None
        return dict(source="mlb_map_after_start", condition_id=str(pm["condition_id"]), slug=pm.get("slug"),
                    home_token=str(pm["home_token"]), away_token=str(pm["away_token"]),
                    game_type=r.get("game_type"), scheduled_innings=int(r.get("scheduled_innings") or 9),
                    start_ts=r.get("mlb_start_ts") or pm.get("start_ts"),
                    home_name=r.get("home_name"), away_name=r.get("away_name"))

    def _mlb(self, rec: dict):
        status = rec.get("status")
        if status in ("tied", "game_over"):
            self.skipped[f"mlb_{status}"] += 1
            return None
        pk, ms = int(rec["game_id"]), int(rec["recv_ms"])
        inning, half, home, away = int(rec["inning"]), rec["half"], int(rec["home"]), int(rec["away"])
        diff = home - away
        m, how = self.mlb.mapping(pk, ms)
        if m is None:
            m = self._fallback_map(pk)
            if m is None:
                self.skipped[f"mlb_{how}"] += 1
                return None
            if game_over((inning, half), diff, m["scheduled_innings"]):
                self.skipped["mlb_game_over"] += 1
                return None
        if diff == 0:
            self.skipped["mlb_tied"] += 1
            return None
        bases = 2 if inning > m["scheduled_innings"] and m["game_type"] == "R" else 0
        leader = "home" if diff > 0 else "away"
        token = m[f"{leader}_token"]
        start = m["start_ts"]
        season = datetime.fromtimestamp(start if start else ms / 1000, timezone.utc).year
        model = self.mlb.model(season - 1)
        fair = model.fair_home(inning, half, 0, bases, diff) if model else None
        lp = None if fair is None else (fair if diff > 0 else 1 - fair)
        ref = self.state.reference(token, ms)
        if status == "evaluated":   # the engine's own evaluation must agree with this one
            same = rec.get("token") == token and (rec.get("edge") is None) == (lp is None or ref is None)
            if same and rec.get("edge") is not None:
                same = abs(rec["edge"] - (lp - ref.price)) < 1e-12
            if not same:
                self.mismatch += 1
        rules, _, _, _ = self.state.market_rules(m["condition_id"], legacy=m["source"] == "legacy")
        evals = rec.get("evals") or {}
        return dict(sport="mlb", game_id=str(pk), state_id=f"{inning}{half}", signal_recv_ms=ms, signal_utc=_utc(ms),
                    day=_utc(ms)[:10], capture_format="markers" if self.state.markers else "legacy",
                    mapping=m["source"], condition_id=m["condition_id"], event=m.get("slug"), token=token,
                    side=leader, team=m.get(f"{leader}_name"), home_name=m.get("home_name"), away_name=m.get("away_name"),
                    inning=inning, half=half, minute=math.nan, diff=diff, home=home, away=away, bases=bases,
                    signal_source=rec.get("source"), fair_leader=_f(lp),
                    reference_price=ref.price if ref else math.nan,
                    reference_age_ms=ms - ref.ms if ref else math.nan,
                    discount_signal=(lp - ref.price) if (lp is not None and ref) else math.nan,
                    engine_eval_10c=evals.get("mlb_10c"), engine_eval_03c=evals.get("mlb_03c"),
                    fee_rate=_f(rules.get("fee_rate")), tick=_f(rules.get("tick")),
                    min_size=_f(rules.get("min_size")), seconds_delay=_f(rules.get("seconds_delay")))

    def _soccer(self, rec: dict):
        slug, ms = rec["game_id"], int(rec["recv_ms"])
        home, away = rec.get("home"), rec.get("away")
        if home is None or away is None or home == away:
            self.skipped["soccer_tied_or_no_score"] += 1
            return None
        gs = self.soccer.state.get(slug) or {}
        if gs.get("terminal_ms") is not None:
            self.skipped["soccer_after_terminal"] += 1
            return None
        g = self.soccer.games.get(slug) if rec.get("mapping") == "exact" else self.soccer.late.get(slug)
        if not g or g.get("match") != "exact":
            self.skipped[f"soccer_{rec.get('mapping') or 'unmapped'}"] += 1
            return None
        leader = "home" if home > away else "away"
        leg = (g.get("legs") or {}).get(leader) or {}
        token = str(leg["yes_token"]) if leg.get("yes_token") else None
        if not token:
            self.skipped["soccer_missing_leader_leg"] += 1
            return None
        ref = self.state.reference(token, ms)
        rules, _, _, _ = self.state.market_rules(leg.get("condition_id"))
        espn = g.get("espn") or {}
        evals = rec.get("evals") or {}
        return dict(sport="soccer", game_id=slug, state_id=str(rec.get("signal_id")), signal_recv_ms=ms, signal_utc=_utc(ms),
                    day=_utc(ms)[:10], capture_format="markers" if self.state.markers else "legacy",
                    mapping="soccer_games" if rec.get("mapping") == "exact" else "soccer_games_after_kickoff",
                    condition_id=leg.get("condition_id"), event=slug, token=token, side=leader,
                    team=espn.get(f"{leader}_name"), home_name=espn.get("home_name"), away_name=espn.get("away_name"),
                    inning=math.nan, half=None, minute=float(rec.get("minute")), diff=int(home) - int(away),
                    home=int(home), away=int(away), bases=math.nan, signal_source=rec.get("type"),
                    fair_leader=math.nan, reference_price=ref.price if ref else math.nan,
                    reference_age_ms=ms - ref.ms if ref else math.nan, discount_signal=math.nan,
                    engine_eval_primary=evals.get("soccer_added_time"),
                    fee_rate=_f(rules.get("fee_rate")), tick=_f(rules.get("tick")),
                    min_size=_f(rules.get("min_size")), seconds_delay=_f(rules.get("seconds_delay")))

    # --- the book at time t
    def measure(self, obs: dict, delay_ms: int, t: int) -> None:
        tok = obs["token"]
        book = self.state.book.books.get(tok) or {"BUY": {}, "SELL": {}}
        asks = sorted((p, lv.reported) for p, lv in book["SELL"].items() if lv.reported > 1e-10)
        bids = sorted(((p, lv.reported) for p, lv in book["BUY"].items() if lv.reported > 1e-10), reverse=True)
        f = self.state.freshness(tok, t)
        best_ask = asks[0][0] if asks else math.nan
        best_bid = bids[0][0] if bids else math.nan
        h = self.state.trades.get(tok) or [None]
        last = h[0]
        ref, fair = obs["reference_price"], obs["fair_leader"]
        last_px = last.price if last is not None else math.nan
        last_age = (t - last.ms) if last is not None else math.nan
        row = dict(obs, delay_s=delay_ms / 1000, measure_ms=t, measure_utc=_utc(t),
                   book_present=tok in self.state.book.initialized, fresh_ok=bool(f["ok"]), fresh_reason=f["reason"],
                   connection_ok=str(f["connection_ok"]), book_age_ms=_f(f["book_age_ms"]),
                   token_msg_age_ms=_f(t - f["token_msg_ms"]) if f["token_msg_ms"] is not None else math.nan,
                   best_ask=best_ask, best_bid=best_bid, spread=best_ask - best_bid,
                   crossed=bool(asks and bids and best_bid >= best_ask),
                   ask_levels=len(asks), ask_usd_total=sum(p * s for p, s in asks),
                   last_trade_T=last_px, last_trade_age_ms=last_age,
                   best_ask_minus_ref=best_ask - ref, best_ask_minus_last_T=best_ask - last_px,
                   discount_T=(fair - last_px) if (last is not None and last_age <= REF_MS) else math.nan,
                   discount_ask=fair - best_ask)
        for k in REF_OFFSETS_C:
            lim = round(ref + k / 100, 6) + EPS if math.isfinite(ref) else -1.
            row[f"depth_usd_ref_p{k}"] = sum(p * s for p, s in asks if p <= lim) if math.isfinite(ref) else math.nan
            row[f"depth_sh_ref_p{k}"] = sum(s for p, s in asks if p <= lim) if math.isfinite(ref) else math.nan
        for e in FAIR_EDGES_C:
            lim = round(fair - e / 100, 6) + EPS if math.isfinite(fair) else -1.
            row[f"depth_usd_fair_m{e}"] = sum(p * s for p, s in asks if p <= lim) if math.isfinite(fair) else math.nan
        for b in BUDGETS:
            spent = shares = 0.
            for p, s in asks:
                q = min(s, (b - spent) / p)
                spent += p * q
                shares += q
                if spent >= b - 1e-9:
                    break
            row[f"vwap_{b}"] = spent / shares if spent >= b - 1e-9 else math.nan
        row["ask_px"] = [p for p, _ in asks[:ASK_LEVELS]]
        row["ask_sz"] = [s for _, s in asks[:ASK_LEVELS]]
        row["bid_px"] = [p for p, _ in bids[:BID_LEVELS]]
        row["bid_sz"] = [s for _, s in bids[:BID_LEVELS]]
        self.rows.append(row)


# ----------------------------------------------------------------------------- post-processing

def _flags(df: pd.DataFrame) -> pd.DataFrame:
    """Rule flags per state (the same for every delay row of that state). "First" is the
    earliest qualifying state per game in the engine's log order, one per game."""
    df = df.sort_values(["obs_seq", "delay_s"]).reset_index(drop=True)
    base = df[df.delay_s == 0].copy()
    key = ["obs_seq"]
    m = base.sport.eq("mlb")
    s = base.sport.eq("soccer")
    for thr, name in ((0.10, "rule_first_10c"), (0.03, "rule_first_03c")):
        q = base[m & base.discount_signal.ge(thr - EPS)]
        first = q.groupby("game_id").obs_seq.transform("min").eq(q.obs_seq)
        base[name] = False
        base.loc[q.index[first.to_numpy()], name] = True
    lo, hi = SOCCER_BAND
    in_band = base.reference_price.between(lo - EPS, hi + EPS)
    base["soccer_in_band"] = s & in_band
    base["soccer_window"] = np.where(~s, None, np.where(base.minute >= 90 - EPS, "90+",
                                     np.where(base.minute.between(75 - EPS, 85 + EPS), "75-85", "other")))
    q = base[s & base.minute.ge(90 - EPS) & in_band]
    first = q.groupby("game_id").obs_seq.transform("min").eq(q.obs_seq)
    base["rule_first_90plus"] = False
    base.loc[q.index[first.to_numpy()], "rule_first_90plus"] = True
    cols = ["rule_first_10c", "rule_first_03c", "soccer_in_band", "soccer_window", "rule_first_90plus"]
    return df.merge(base[key + cols], on=key, how="left")


def _usable(df: pd.DataFrame) -> pd.Series:
    """A received snapshot, an uncrossed book, and a current connection. Days with markers use the
    engine's freshness check. Legacy days have no markers, so a quiet token counts only if its
    last message is at most 60 s old."""
    legacy = df.capture_format.eq("legacy")
    fresh = df.fresh_ok | (legacy & df.fresh_reason.eq("stale_token_legacy") & df.token_msg_age_ms.le(60_000))
    return df.book_present & ~df.crossed & fresh


SUBSETS = {
    "mlb": {
        "all_lead_states": lambda d: d.index == d.index,
        "discount_ge_0.03": lambda d: d.discount_signal.ge(0.03 - EPS),
        "discount_ge_0.10": lambda d: d.discount_signal.ge(0.10 - EPS),
        "rule_first_03c": lambda d: d.rule_first_03c.fillna(False).astype(bool),
        "rule_first_10c": lambda d: d.rule_first_10c.fillna(False).astype(bool),
    },
    "soccer": {
        "90plus_lead": lambda d: d.minute.ge(90 - EPS),
        "90plus_in_band": lambda d: d.minute.ge(90 - EPS) & d.soccer_in_band.fillna(False).astype(bool),
        "rule_first_90plus": lambda d: d.rule_first_90plus.fillna(False).astype(bool),
        "ctrl_75_85_lead": lambda d: d.minute.between(75 - EPS, 85 + EPS),
    },
}
METRICS = (["discount_signal", "discount_T", "discount_ask", "best_ask_minus_ref", "best_ask_minus_last_T", "spread"]
           + [f"depth_usd_ref_p{k}" for k in REF_OFFSETS_C] + [f"depth_usd_fair_m{e}" for e in FAIR_EDGES_C]
           + [f"vwap_minus_ref_{b}" for b in BUDGETS] + ["ask_usd_total", "book_age_ms", "reference_age_ms"])


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    for b in BUDGETS:
        d[f"vwap_minus_ref_{b}"] = d[f"vwap_{b}"] - d.reference_price
    d = d[d.usable]
    out = []
    for sport, subsets in SUBSETS.items():
        for fmt in ("all", "legacy", "markers"):
            ds = d[d.sport.eq(sport) & (d.capture_format.eq(fmt) if fmt != "all" else True)]
            if ds.empty:
                continue
            for name, fn in subsets.items():
                out += _describe(ds[fn(ds)], sport, fmt, name)
    return pd.DataFrame(out)


def _describe(sub: pd.DataFrame, sport: str, fmt: str, name: str) -> list[dict]:
    out = []
    for delay, g in sub.groupby("delay_s"):
        for metric in METRICS:
            x = g[metric].astype(float)
            x = x[np.isfinite(x)]
            rec = dict(sport=sport, capture_format=fmt, subset=name, delay_s=delay, metric=metric,
                       n_states=len(g), n_games=g.game_id.nunique(), n_values=len(x))
            if len(x):
                q = np.percentile(x, [10, 25, 50, 75, 90])
                rec.update(mean=x.mean(), p10=q[0], p25=q[1], p50=q[2], p75=q[3], p90=q[4], min=x.min(), max=x.max())
                if metric.startswith("depth_usd") or metric == "ask_usd_total":
                    rec["frac_zero"] = float((x <= 1e-9).mean())
                    for t in DEPTH_THRESHOLDS:
                        rec[f"frac_ge_{t}"] = float((x >= t).mean())
            out.append(rec)
    return out


def _paper_check(df: pd.DataFrame) -> list[dict]:
    """Cross-check against the paper engine's own executed primary decisions (same book, same
    time): displayed depth at <= limit versus the fill it recorded (capped at a $100 budget)."""
    dec = [r for r in read_jsonl(DATA_DIR / "paper" / "decisions.jsonl")
           if r.get("policy") in ("mlb_10c", "soccer_added_time") and r.get("eligible_ms")]
    out = []
    for r in dec:
        delay = (r["eligible_ms"] - r["signal_recv_ms"]) / 1000
        sport = "mlb" if r["policy"].startswith("mlb") else "soccer"
        hit = df[df.sport.eq(sport) & df.game_id.eq(str(r["game_id"])) & df.signal_recv_ms.eq(r["signal_recv_ms"])
                 & df.delay_s.eq(delay)]
        mine = float(hit.depth_usd_ref_p1.iloc[0]) if len(hit) else math.nan
        out.append(dict(policy=r["policy"], game_id=r["game_id"], signal_utc=_utc(r["signal_recv_ms"]), delay_s=delay,
                        reference=r.get("reference_price"), limit=r.get("limit"), paper_status=r.get("status"),
                        paper_gross_usd=round(float(r.get("gross_usd") or 0), 2),
                        depth_usd_at_limit=round(mine, 2) if math.isfinite(mine) else None,
                        consistent=bool(len(hit)) and abs(min(mine, r.get("gross_usd") or 0) - float(r.get("gross_usd") or 0)) < 0.01
                        and (r.get("status") == "filled" or abs(mine - float(r.get("gross_usd") or 0)) < 0.01)))
    return out


def run(until: str | None = None, live: Path = LIVE, out: Path = OUT) -> dict:
    end_ms = int(pd.Timestamp(until, tz="UTC").value // 1_000_000) if until else int(time.time() * 1000) - 120_000
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    probe = DepthProbe(live)
    rp = Replayer(live, None, end_ms, follow=False)
    n, last, t0 = 0, None, time.time()
    for ms, stream, rec in rp:
        probe.process(ms, stream, rec)
        last = ms
        n += 1
        if n % 2_000_000 == 0:
            log.info("captured_depth: %d records, at %s, %d rows", n, _utc(ms), len(probe.rows))
    if last is not None:
        probe.advance(last)
    censored = len(probe.timers)
    df = pd.DataFrame(probe.rows)
    if df.empty:
        raise SystemExit("no measurable signals in the capture")
    df = _flags(df)
    df["usable"] = _usable(df)
    df.to_parquet(out / "captured_depth.parquet", index=False)
    summary = summarize(df)
    summary.to_csv(out / "captured_depth_summary.csv", index=False)
    check = _paper_check(df)
    d0 = df[df.delay_s.eq(0)]
    counts = {}
    for sport, g in d0.groupby("sport"):
        counts[sport] = dict(
            games=int(g.game_id.nunique()), states=len(g), usable_states=int(g.usable.sum()),
            by_day={k: dict(games=int(v.game_id.nunique()), states=len(v)) for k, v in g.groupby("day")},
            by_format={k: dict(games=int(v.game_id.nunique()), states=len(v)) for k, v in g.groupby("capture_format")},
            by_mapping={k: int(v) for k, v in g.mapping.value_counts().items()},
            unusable_reasons={f"{a}|{b}": int(v) for (a, b), v in
                              g[~g.usable].groupby(["book_present", "fresh_reason"]).size().items()})
        flag, ev = (("rule_first_10c", "engine_eval_10c") if sport == "mlb" else ("rule_first_90plus", "engine_eval_primary"))
        engine_q = g[ev].eq("qualified")
        counts[sport]["first_flag_vs_engine_qualified"] = dict(
            both=int((g[flag] & engine_q).sum()), flag_only=int((g[flag] & ~engine_q).sum()),
            engine_only=int((~g[flag] & engine_q).sum()))
        if sport == "mlb":
            counts[sport].update(discount_ge_010=int(g.discount_signal.ge(0.10 - EPS).sum()),
                                 discount_ge_003=int(g.discount_signal.ge(0.03 - EPS).sum()),
                                 rule_first_10c_games=int(g.rule_first_10c.sum()),
                                 rule_first_03c_games=int(g.rule_first_03c.sum()),
                                 no_reference=int(g.reference_price.isna().sum()))
        else:
            counts[sport].update(events_90plus=int(g.minute.ge(90 - EPS).sum()),
                                 games_90plus=int(g[g.minute.ge(90 - EPS)].game_id.nunique()),
                                 rule_first_90plus_games=int(g.rule_first_90plus.sum()))
    manifest = dict(
        created_utc=datetime.now(timezone.utc).isoformat(), end_ms=end_ms, end_utc=_utc(end_ms),
        last_record_utc=_utc(last) if last else None, records=n, runtime_s=round(time.time() - t0, 1),
        replay=rp.stats(), book_errors=probe.state.errors, record_errors=probe.errors,
        engine_disagreements=probe.mismatch, timers_after_capture_end=censored, skipped=dict(probe.skipped),
        counts=counts, paper_cross_check=check,
        live_files={str(p.relative_to(ROOT)): p.stat().st_size for p in sorted(Path(live).glob("*/*.jsonl*"))},
        code={str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in [Path(__file__), ROOT / "pmsports/paper/engine.py", ROOT / "pmsports/paper/mlb.py",
                        ROOT / "pmsports/paper/soccer.py", ROOT / "pmsports/book_replay.py"]},
        model_snapshot=str(SNAP_DIR / "mlb_fair_2025.json"),
        caveat=("Real received books over a few captured days (2026-09-18/19 legacy capture, 2026-09-25 onward with "
                "connection markers). Displayed size only: queue position, hidden size, competing takers, "
                "acceptance and latency are not observed. The historical backtest instead used a trade-print proxy "
                "(a later same-side print within 5 s, +1c slippage, $100 cap)."))
    (out / "captured_depth_manifest.json").write_text(json.dumps(manifest, indent=1, default=str))
    return manifest


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--until", help="UTC end of the replay (default: now - 2 min; capture keeps growing)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    m = run(a.until)
    print(json.dumps({k: m[k] for k in ("records", "runtime_s", "last_record_utc", "engine_disagreements",
                                        "timers_after_capture_end", "skipped", "counts", "paper_cross_check")},
                     indent=1, default=str))
