"""Five frozen soccer hypotheses, evaluated as causal historical transaction proxies.

Run: OPENBLAS_NUM_THREADS=1 .venv/bin/python -m pmsports.research.h_soccer_continuation --run
No captured soccer books or historical public receipt clocks are available. No result here
establishes atomic execution, available touch depth, or a fresh confirmation sample.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

from ..execution import TapeReplay, VERSION
from ..polymarket import taker_fee
from . import common as C
from .ledger_studies import _document

ROOT = C.ROOT
OUT = C.RESEARCH / "soccer_continuation"
REPORT = ROOT / "reports/research/SOCCER_CONTINUATION.md"
PANEL = C.DATA / "events/soccer/panel.parquet"
TAPES = C.DATA / "wallets/tapes"
EXTRAS = C.DATA / "events/soccer/extra_fills"
SPLIT = pd.Timestamp("2026-07-01", tz="UTC").timestamp()
LEGS = ("home", "draw", "away")
GOALS = {"goal", "penalty_goal", "own_goal"}
ACTION_POLICY = "soccer-direct-buy-sell-v2"
RAW_PROVENANCE = ("raw_action", "raw_token_side", "raw_asset", "raw_price")
SLUGS = {"card": "soccer-red-card-timescale", "dutch": "soccer-3way-goal-dutchbook",
         "sub": "soccer-injury-sub-shock", "anchor": "soccer-draw-anchor-basis",
         "leader": "soccer-added-time-leader"}
TITLES = {"card": "Red-card timescale", "dutch": "Post-goal three-book dutch book",
          "sub": "Early substitution shock", "anchor": "Draw-anchor team-pair basis",
          "leader": "Added-time leader"}
EMPTY_TAPE = dict(m=pd.Series(dtype=str), s=pd.Series(dtype=int), ts=pd.Series(dtype=float),
                  q=pd.Series(dtype=float), size=pd.Series(dtype=float), fee_rate=pd.Series(dtype=float),
                  print_id=pd.Series(dtype=str), raw_action=pd.Series(dtype=str),
                  raw_token_side=pd.Series(dtype=int), raw_asset=pd.Series(dtype=str), raw_price=pd.Series(dtype=float))


def clean(x):
    if isinstance(x, dict): return {str(k): clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)): return [clean(v) for v in x]
    if isinstance(x, np.generic): x = x.item()
    if isinstance(x, float) and not np.isfinite(x): return None
    return x


def true_minute(minute, display):
    """Prefer the display's explicit base+added clock; never add N twice."""
    m = re.search(r"(\d+)\s*['’]?\s*\+\s*(\d+)", str(display))
    return float(int(m[1]) + int(m[2])) if m else float(minute)


def normalize_raw(raw, cid, meta):
    """Preserve literal action/token alongside the economic acquisition normalization.

    Token identity, not the API's occasionally wrong outcomeIndex, identifies the
    native outcome. SELL complements are useful reference probabilities; they are
    not actual BUY observations or BUY entry capacity for these original protocols.
    """
    if raw.empty or not meta: return pd.DataFrame(EMPTY_TAPE)
    token = {str(meta["yes_token"]): 0, str(meta["no_token"]): 1}
    x = raw.copy()
    s = x.asset.astype(str).map(token)
    valid = s.notna() & x.side.isin(["BUY", "SELL"])
    x, s = x.loc[valid], s[valid].astype(int)
    buy = x.side.eq("BUY").to_numpy()
    # Exact repeated API rows may be duplicated; a transaction can contain distinct fills.
    identity = [c for c in ("transactionHash", "timestamp", "asset", "side", "price", "size", "proxyWallet") if c in x]
    keep = ~x.duplicated(identity)
    x, s, buy = x[keep], s[keep], buy[keep]
    return pd.DataFrame(dict(m=cid, s=np.where(buy, s, 1-s), ts=x.timestamp.to_numpy(float),
        q=np.where(buy, x.price, 1-x.price), size=x["size"].to_numpy(float),
        fee_rate=meta["fee_rate"], print_id=[f"{cid}:{i}" for i in x.index],
        raw_action=x.side.to_numpy(), raw_token_side=s.to_numpy(),
        raw_asset=x.asset.astype(str).to_numpy(), raw_price=x.price.to_numpy(float),
        w=x.proxyWallet.to_numpy() if "proxyWallet" in x else None))


def market_metadata(cids):
    columns = ["condition_id", "outcome", "token_id", "payout", "fee_rate", "closed_ts", "event_slug", "market_slug", "neg_risk"]
    u = ds.dataset(C.DATA / "wallets/universe.parquet", format="parquet").to_table(
        columns=columns, filter=ds.field("condition_id").isin(list(cids)),
        batch_size=16384, batch_readahead=0, fragment_readahead=1, use_threads=False).to_pandas()
    out = {}
    for cid, g in u.groupby("condition_id", sort=False):
        g = g.drop_duplicates(["outcome", "token_id", "payout"])
        yes, no = g[g.outcome.str.lower().eq("yes")], g[g.outcome.str.lower().eq("no")]
        if len(yes) != 1 or len(no) != 1: continue
        a, b = yes.iloc[0], no.iloc[0]
        # Terminal payout validation is metadata integrity, never a winner-only eligibility rule.
        if a.payout not in (0, .5, 1) or b.payout not in (0, .5, 1) or a.payout+b.payout != 1: continue
        if not np.isfinite(a.fee_rate) or a.fee_rate < 0: continue
        out[cid] = dict(yes_token=a.token_id, no_token=b.token_id, y=float(a.payout),
            fee_rate=float(a.fee_rate), closed_ts=float(a.closed_ts), event=a.event_slug,
            market=a.market_slug, neg_risk=bool(a.neg_risk))
    return out


class GameData:
    def __init__(self, events, meta, tape=None, references=None):
        self.events = events.sort_values(["wallclock", "idx"], kind="stable").reset_index(drop=True)
        self.event = str(self.events.event_slug.iloc[0])
        self.cids = {leg: self.events[f"cid_{leg}"].iloc[0] for leg in LEGS}
        self.meta = {leg: meta.get(cid) for leg, cid in self.cids.items()}
        parts, refs = [], {}
        self.coverage = {"native": 0, "extra_reference": 0}
        if tape is None:
            for leg, cid in self.cids.items():
                path, extra = TAPES / f"{cid}.parquet", EXTRAS / f"{cid}.parquet"
                info = self.meta[leg]
                if info and info["event"] != self.event:
                    raise ValueError(f"cross-event contract {cid}: {info['event']} != {self.event}")
                raw = normalize_raw(pd.read_parquet(path), cid, info) if path.exists() and info else pd.DataFrame(EMPTY_TAPE)
                if len(raw):
                    parts.append(raw)
                    self.coverage["native"] += 1
                    refs[leg] = pd.DataFrame(dict(ts=raw.ts, p=np.where(raw.s.eq(0), raw.q, 1-raw.q), source="native_reference"))
                elif extra.exists():
                    e = pd.read_parquet(extra)
                    refs[leg] = pd.DataFrame(dict(ts=e.ts, p=e.p_yes, source="extra_reference_no_side"))
                    self.coverage["extra_reference"] += 1
            tape = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(EMPTY_TAPE)
        # The two immutable indexes partition source prints by literal action.
        # They are built once per game, then reused by every independent case.
        # Missing/unknown provenance fails closed for entry and unwind capacity.
        action = tape.get("raw_action", pd.Series(None, index=tape.index, dtype=object))
        native = tape.get("raw_token_side", pd.Series(np.nan, index=tape.index))
        buy = action.eq("BUY") & native.eq(tape.s)
        sell = action.eq("SELL") & native.eq(1-tape.s)
        self.replay = TapeReplay(tape[buy])
        self.sell_replay = TapeReplay(tape[sell])
        self.provenance = tape.reindex(columns=["print_id", *RAW_PROVENANCE]).drop_duplicates("print_id").set_index("print_id")
        if references is not None: refs = references
        elif not refs and len(tape):
            for leg, cid in self.cids.items():
                t = tape[tape.m.eq(cid)]
                refs[leg] = pd.DataFrame(dict(ts=t.ts, p=np.where(t.s.eq(0), t.q, 1-t.q), source="fixture_reference"))
        self.refs = {leg: x[np.isfinite(x.ts) & x.p.between(0, 1, inclusive="neither")].sort_values("ts", kind="stable") for leg, x in refs.items()}
        end = self.events[self.events.type_raw.isin(["end-regular-time", "end-match"]) & self.events.period.le(2)].wallclock
        self.terminal = float(end.min()) if len(end) else np.inf
        closures = [x["closed_ts"] for x in self.meta.values() if x and np.isfinite(x["closed_ts"])]
        self.terminal = min([self.terminal, *closures])

    def prior(self, leg, ts, max_age=600, acquired=None):
        if acquired is None:
            x = self.refs.get(leg)
            if x is None or x.empty: return None
            i = np.searchsorted(x.ts.to_numpy(), ts, side="left")-1
            if i < 0 or ts-x.ts.iloc[i] > max_age: return None
            return dict(p=float(x.p.iloc[i]), ts=float(x.ts.iloc[i]), size=np.inf, source=x.source.iloc[i])
        bounds = self.replay.groups.get((self.cids[leg], acquired))
        if bounds is None: return None
        a, b = bounds; i = a+np.searchsorted(self.replay.ts[a:b], ts, side="right")-1
        if i < a or ts-self.replay.ts[i] > max_age: return None
        return dict(p=float(self.replay.q[i]), ts=float(self.replay.ts[i]), size=float(self.replay.size[i]), source="native_direct_buy")

    def observations(self, ts, side=0, horizon=60):
        found = {}
        for leg in LEGS:
            bounds = self.replay.groups.get((self.cids[leg], side))
            if bounds is None: return None
            a, b = bounds
            i = a+np.searchsorted(self.replay.ts[a:b], ts+3, side="right")
            if i >= b or self.replay.ts[i] >= min(ts+horizon, self.terminal): return None
            found[leg] = dict(p=float(self.replay.q[i]), ts=float(self.replay.ts[i]), size=float(self.replay.size[i]), source="native_direct_buy")
        return found


def configs():
    out = []
    def add(family, name="primary", **kw):
        out.append(dict(family=family, name=name, horizon=300, delay=3, window=5, floor=.995,
                        gap=.05, minute=65 if family == "card" else 25, **kw))
    # Only named, actually executed cases are reported; optional unimplemented variants remain explicit.
    for family in SLUGS: add(family)
    add("card", "late_secondary", late=True)
    for cut in (55, 75): add("card", f"early_{cut}", cut=cut)
    add("card", "no_state_filter", no_state=True)
    for h in (60, 600):
        add("card", f"horizon_{h}", decision_horizon=h)
        add("sub", f"horizon_{h}", decision_horizon=h)
    for cut in (20, 30): add("sub", f"minute_{cut}", cut=cut)
    add("sub", "explicit_injury", injury=True)
    add("sub", "exclude_1_5", exclude_early=True)
    add("sub", "no_sub_team", no_team=True)
    for window in (2, 10, 30): add("dutch", f"window_{window}", sync=window)
    for floor in (.99, .97): add("dutch", f"floor_{floor}", trigger_floor=floor)
    add("dutch", "no_mirror", no_side=True)
    for gap in (.03, .08): add("anchor", f"gap_{gap}", trigger_gap=gap)
    for window in (2, 30): add("anchor", f"window_{window}", sync=window)
    add("anchor", "goals_only", goals_only=True)
    add("anchor", "cheaper_team", cheap=True)
    add("leader", "control_75_85", control=(75, 85))
    add("leader", "control_70_80", control=(70, 80))
    for cut in (88, 92): add("leader", f"clock_{cut}", clock_cut=cut)
    for lo, hi in ((.70, .97), (.60, .99)): add("leader", f"price_{lo}_{hi}", band=(lo, hi))
    for delay in (10, 60): add("leader", f"delay_{delay}", entry_delay=delay)
    return out


def initial_candidates(game, cfg):
    x = game.events
    # No feed_consistent, reg_agrees, final-score or eventual-volume eligibility filters.
    x = x[x.period.isin([1, 2]) & ~x.shootout & x.wallclock.notna() & x.wallclock.lt(game.terminal)]
    family = cfg["family"]
    if family == "card":
        x = x[x.kind.eq("red_card") & x.team_side.isin(["home", "away"])]
        signed = np.where(x.team_side.eq("home"), x.lead, -x.lead)
        late = cfg.get("late", False)
        cut = cfg.get("cut", 70 if late else 65)
        x = x[(x.minute.ge(cut) if late else x.minute.le(cut)) & (True if cfg.get("no_state") else signed <= 0)]
    elif family == "sub":
        x = x[x.kind.eq("substitution") & x.period.eq(1) & x.minute.le(cfg.get("cut", 25)) & x.team_side.isin(["home", "away"])]
        if cfg.get("injury"): x = x[x.text.fillna("").str.contains("injur", case=False)]
        if cfg.get("exclude_early"): x = x[x.minute.gt(5)]
    elif family == "dutch" or cfg.get("goals_only"):
        x = x[x.kind.isin(GOALS)]
    elif family == "leader":
        x = x[x.period.eq(2) & x.lead.abs().ge(1)].copy()
        x["true_min"] = [true_minute(m, d) for m, d in zip(x.minute, x.clock_disp)]
        if "control" in cfg: x = x[x.true_min.between(*cfg["control"])]
        else: x = x[x.true_min.ge(cfg.get("clock_cut", 90))]
    return x


def make_signals(game, cfg):
    """Audit every initially eligible event; reject based only on information known at decision."""
    family, name = cfg["family"], cfg["name"]
    audits, orders = [], []
    selected = False
    for r in initial_candidates(game, cfg).to_dict("records"):
        t0 = float(r["wallclock"])
        decision = t0+cfg.get("decision_horizon", cfg["horizon"]) if family in ("card", "sub") else t0
        aid = f"{game.event}:{r['idx']}:{name}"
        a = dict(signal_id=aid, family=family, variant=name, event=game.event, league=r["league"],
            period="dev" if decision < SPLIT else "holdout", event_ts=t0, signal_ts=decision,
            eligible_ts=decision+cfg.get("entry_delay", cfg["delay"]), reason="eligible", selected=False,
            kind=r["kind"], team_side=r.get("team_side"), minute=r["minute"], lead=r["lead"],
            true_min=true_minute(r["minute"], r.get("clock_disp")), injury_text="injur" in str(r.get("text", "")).lower(),
            feed_consistent=r.get("feed_consistent"), reg_agrees=r.get("reg_agrees"))
        for leg in LEGS:
            for col in (f"age_{leg}_pre", f"n_{leg}_5m"):
                a[col] = r.get(col, np.nan)
        refs, targets, sides = {}, [], []
        paired = family in ("dutch", "anchor")
        if selected and family not in ("dutch", "anchor"): a["reason"] = "later_event_after_first_order"
        elif decision >= game.terminal: a["reason"] = "decision_after_terminal"
        elif not all(game.meta.values()): a["reason"] = "missing_valid_contract_metadata"
        else:
            if paired:
                obs_side = int(cfg.get("no_side", False)) if family == "dutch" else 0
                refs = game.observations(t0, obs_side)
                if refs is None: a["reason"] = "missing_three_acquired_side_observations"
                else:
                    spread = max(z["ts"] for z in refs.values())-min(z["ts"] for z in refs.values())
                    decision = max(z["ts"] for z in refs.values())
                    a.update(signal_ts=decision, eligible_ts=decision+3, observation_span_s=spread)
                    if spread > cfg.get("sync", cfg["window"]): a["reason"] = "observation_span_exceeded"
                    elif family == "dutch":
                        unit = sum(z["p"]+float(taker_fee(1., z["p"], game.meta[l]["fee_rate"])) for l,z in refs.items())
                        floor = cfg.get("trigger_floor", cfg["floor"])+(1 if obs_side else 0)
                        a.update(observed_unit_cost=unit, reference_price=sum(z["p"] for z in refs.values()), gap=unit-floor)
                        if unit >= floor: a["reason"] = "cost_threshold_not_met"
                        else: targets, sides = list(LEGS), [obs_side]*3
                    else:
                        gap = sum(z["p"] for z in refs.values())-1
                        a.update(gap=gap, reference_price=sum(z["p"] for z in refs.values()))
                        if abs(gap) < cfg.get("trigger_gap", cfg["gap"]): a["reason"] = "gap_threshold_not_met"
                        else:
                            targets, sides = ["home", "away"], [int(gap > 0)]*2
                            if gap > 0:
                                refs = {l: game.prior(l, decision, acquired=1, max_age=5) for l in targets}
                                if any(v is None for v in refs.values()): a["reason"] = "missing_actual_no_references"
                            if a["reason"] == "eligible" and cfg.get("cheap"):
                                target = min(targets, key=lambda l: refs[l]["p"])
                                targets, sides = [target], [sides[0]]
            else:
                refs = {l: game.prior(l, decision, max_age=120 if family == "leader" else 600) for l in LEGS}
                if any(v is None for v in refs.values()): a["reason"] = "missing_three_prior_references"
                else:
                    target = ("home" if r["lead"] > 0 else "away") if family == "leader" else (
                        r["team_side"] if cfg.get("late") else ("away" if r["team_side"] == "home" else "home"))
                    price = refs[target]["p"]
                    for leg in LEGS:
                        a[f"reference_{leg}"] = refs[leg]["p"]
                        a[f"y_{leg}"] = game.meta[leg]["y"]
                        a[f"reference_source_{leg}"] = refs[leg]["source"]
                    a["reference_price"] = price
                    if family == "sub" and not .15 <= price <= .85: a["reason"] = "opponent_price_outside_band"
                    elif family == "leader" and not cfg.get("band", (.60, .97))[0] <= price <= cfg.get("band", (.60, .97))[1]: a["reason"] = "leader_price_outside_band"
                    else:
                        targets, sides = [target], [0]
                        if cfg.get("no_team"):
                            target = r["team_side"]
                            ref = game.prior(target, decision, acquired=1)
                            if ref is None: a["reason"] = "missing_actual_no_reference"
                            else: targets, sides, refs = [target], [1], {target: ref}
        delay = cfg.get("entry_delay", 3)
        expiry = min(decision+delay+(60 if paired else 120 if family == "leader" else 600), game.terminal)
        a.update(expiry_ts=expiry, receipt_ts=decision,
                 period="dev" if decision < SPLIT else "holdout")
        # Continue measuring anchor gaps for its mandatory age/count control, while
        # keeping strategy selection fixed at its first qualifying order.
        if selected and family == "anchor" and a["reason"] == "eligible":
            a["reason"] = "later_event_after_first_order"
        if a["reason"] == "eligible" and expiry <= decision+delay: a["reason"] = "no_window_before_terminal"
        if a["reason"] == "eligible":
            a["selected"] = True; selected = True
            units = np.array([refs[l]["p"]+float(taker_fee(1., refs[l]["p"], game.meta[l]["fee_rate"])) for l in targets])
            if paired:
                budget = 100. if family == "dutch" else min(100., 1000*abs(a["gap"]), min(refs[l]["p"]*refs[l]["size"] for l in targets))
                weights = np.ones(len(targets)) if family == "dutch" else np.array([refs[l]["p"] for l in targets])
                qty = weights*budget/float(np.dot(weights, units))
                if family == "dutch": qty = np.minimum(qty, min(refs[l]["size"] for l in targets))
                allocations = qty*units
            else: qty, allocations = [np.inf], [100.]
            for leg, side, quantity, budget in zip(targets, sides, qty, allocations):
                m = game.meta[leg]
                orders.append(dict(signal_id=aid, family=family, variant=name, event=game.event, league=r["league"],
                    period="dev" if decision < SPLIT else "holdout", m=game.cids[leg], leg=leg, s=side,
                    signal_ts=decision, receipt_ts=decision, delay_s=delay, expiry_ts=expiry, budget_usd=float(budget),
                    target_shares=float(quantity), y=m["y"] if side == 0 else 1-m["y"], fee_rate=m["fee_rate"],
                    reference_price=refs[leg]["p"], market=m["market"], close_ts=m["closed_ts"], paired=paired))
        audits.append(a)
    return pd.DataFrame(audits), pd.DataFrame(orders)


def execute(game, orders, slip=0.):
    """Shared first-print entry allocation, fixed quantity; failed pairs unwind all acquired legs.

    Entry and observation prints must be literal BUYs of the intended native token.
    Unwinds must be literal SELLs of the held token. BUY and SELL indexes partition
    source prints; each pool consumes capacity once across that policy's positions.
    """
    if orders.empty: return pd.DataFrame()
    r = game.replay.replay(orders, event_cap_usd=100., slip=slip)
    r["action_policy"] = ACTION_POLICY
    for c in RAW_PROVENANCE:
        r["entry_"+c] = r.print_id.map(game.provenance[c])
        r["exit_"+c] = pd.Series(None, index=r.index, dtype=object)
    r["entry_ts"] = r.fill_ts
    r["entry_fee_usd"] = r.fee_usd
    r["entry_cost_usd"] = r.cost_usd
    r["exit_fee_usd"] = 0.; r["sold_shares"] = 0.; r["sale_proceeds"] = 0.
    r["residual_shares"] = r.shares
    r["exit_print_id"] = None; r["exit_fill_ts"] = np.nan
    r["hedge_status"] = "single"
    exits = []
    for _, g in r[r.paired].groupby("signal_id", sort=False):
        complete = bool((g.shares >= g.target_shares-1e-8).all())
        r.loc[g.index, "hedge_status"] = "complete" if complete else "failed_or_partial"
        if complete: continue
        for i, row in g[g.shares.gt(0)].iterrows():
            # A literal SELL of the held token normalizes to the opposite s at
            # q=1-raw_price. The SELL-only index excludes BUY-opposite substitutes.
            exits.append(dict(row_id=i, m=row.m, s=1-row.s, signal_ts=row.expiry_ts,
                receipt_ts=row.expiry_ts, expiry_ts=min(row.expiry_ts+3+120, game.terminal),
                target_shares=row.shares, budget_usd=row.shares*2, event=row.event,
                y=0., fee_rate=row.fee_rate))
    if exits:
        x = game.sell_replay.replay(pd.DataFrame(exits), event_cap_usd=np.inf, slip=slip)
        for row in x[x.shares.gt(0)].itertuples():
            i = row.row_id; sale_price = 1-row.entry_price
            r.loc[i, ["sold_shares", "sale_proceeds", "exit_fee_usd", "residual_shares"]] = [row.shares, row.shares*sale_price, row.fee_usd, r.loc[i, "shares"]-row.shares]
            r.loc[i, "exit_print_id"] = row.print_id; r.loc[i, "exit_fill_ts"] = row.fill_ts
            for c in RAW_PROVENANCE:
                r.loc[i, "exit_"+c] = game.provenance.loc[row.print_id, c]
    r["payout"] = r.sale_proceeds+r.residual_shares*r.y
    r["fee_usd"] = r.entry_fee_usd+r.exit_fee_usd
    r["cost_usd"] = r.stake_usd+r.fee_usd
    r["pnl_usd"] = r.payout-r.cost_usd
    r["roi"] = np.where(r.cost_usd.gt(0), r.pnl_usd/r.cost_usd, np.nan)
    r["exit_kind"] = np.where(r.sold_shares.gt(0), "partial_unwind_and_resolution", "resolution")
    r["exit_ts"] = r.close_ts
    r["exit_price"] = np.where(r.shares.gt(0), r.payout/r.shares, np.nan)
    fully_flat = r.shares.gt(0) & r.sold_shares.gt(0) & r.residual_shares.le(1e-10)
    r.loc[fully_flat, "exit_kind"] = "unwind"
    r.loc[fully_flat, "exit_ts"] = r.loc[fully_flat, "exit_fill_ts"]
    unfilled = r.shares.eq(0)
    r.loc[unfilled, "exit_kind"] = "unfilled"
    r.loc[unfilled, ["exit_ts", "exit_price"]] = np.nan
    r["side"] = r.leg+np.where(r.s.eq(0), " Yes", " No")
    r["sport"] = "soccer"; r["slip"] = slip
    r["note"] = "Literal native BUY entry / SELL unwind transaction proxy; unknown public receipt; "+r.hedge_status
    return r


def summary(audit, trades):
    filled = trades[trades.cost_usd.gt(0)] if len(trades) else trades
    result = dict(candidates=len(audit), signals=int(audit.selected.sum()) if len(audit) else 0,
        filled_signals=int(filled.signal_id.nunique()) if len(filled) else 0, filled_legs=len(filled),
        games=int(filled.event.nunique()) if len(filled) else 0, capital_usd=0., pnl_usd=0., roi=None,
        ci_lo=None, ci_hi=None, reasons=audit.reason.value_counts().to_dict() if len(audit) else {})
    result["unfilled_signals"] = result["signals"]-result["filled_signals"]
    if not len(filled): return result
    mean, lo, hi = C.cluster_ci(filled.roi, filled.event, weights=filled.cost_usd, n_boot=1000, seed=20260922)
    games = filled.groupby("event").agg(pnl=("pnl_usd", "sum"), capital=("cost_usd", "sum"))
    result.update(capital_usd=float(filled.cost_usd.sum()), pnl_usd=float(filled.pnl_usd.sum()),
        roi=mean, ci_lo=lo, ci_hi=hi, capacity_usd_per_funded_game=float(games.capital.mean()),
        capacity_usd_per_candidate_game=float(filled.cost_usd.sum()/max(1, audit.event.nunique())),
        top3_game_pnl_usd=float(games.pnl.nlargest(3).sum()),
        pnl_without_top3_games=float(games.pnl.sum()-games.pnl.nlargest(3).sum()),
        partial_legs=int(filled.status.eq("partial").sum()),
        failed_hedge_signals=int(filled.loc[filled.hedge_status.eq("failed_or_partial"), "signal_id"].nunique()),
        residual_shares=float(filled.residual_shares.sum()), sold_shares=float(filled.sold_shares.sum()),
        settlement_void_legs=int(filled.y.eq(.5).sum()), entry_capital_usd=float(filled.entry_cost_usd.sum()))
    return clean(result)


def coherence(audit):
    a = audit[(audit.family == "sub") & (audit.variant == "primary") & audit.selected].copy()
    out = {}
    for period, g in a.groupby("period"):
        e = sum(g[f"y_{l}"]-g[f"reference_{l}"] for l in LEGS)
        mean, lo, hi = C.cluster_ci(e, g.event)
        out[period] = dict(n=len(g), sum_leg_errors=mean, ci_lo=lo, ci_hi=hi,
            leg_errors={l:float((g[f"y_{l}"]-g[f"reference_{l}"]).mean()) for l in LEGS},
            mean_price_sum=float(sum(g[f"reference_{l}"] for l in LEGS).mean()),
            mean_payout_sum=float(sum(g[f"y_{l}"] for l in LEGS).mean()),
            gate="passes_1c_tolerance" if abs(mean) <= .01+1e-8 else "fails_1c_tolerance")
    return clean(out)


def staleness_control(audit):
    if "gap" not in audit:
        return {"status":"no_three_acquired_side_observations"}
    a = audit[(audit.family == "anchor") & (audit.variant == "primary") & audit.gap.notna()].copy()
    cols = [f"age_{l}_pre" for l in LEGS]+[f"n_{l}_5m" for l in LEGS]
    a = a.dropna(subset=cols+["gap"])
    if not len(a): return {"status":"no_complete_features"}
    x = np.log1p(a[cols].clip(lower=0).to_numpy(float)); x = np.c_[np.ones(len(x)), x]
    dev = a.period.eq("dev").to_numpy()
    if dev.sum() < 20: return {"status":"insufficient_dev", "dev_rows":int(dev.sum())}
    beta = np.linalg.lstsq(x[dev], a.gap.to_numpy(float)[dev], rcond=None)[0]
    pred = x@beta; resid = a.gap.to_numpy()-pred
    out = dict(status="descriptive_dev_fitted_control", features=["intercept"]+cols, coefficients=beta.tolist())
    for period, mask in (("dev",dev),("holdout",~dev)):
        y = a.gap.to_numpy()[mask]
        if not len(y): continue
        denominator = np.sum((y-np.mean(y))**2)
        out[period] = dict(n=len(y), r2=1-float(np.sum(resid[mask]**2)/denominator) if denominator else None,
            mean_abs_gap=float(np.mean(abs(y))), mean_abs_residual=float(np.mean(abs(resid[mask]))),
            original_5c_exceedance=float(np.mean(abs(y)>=.05)), residual_5c_exceedance=float(np.mean(abs(resid[mask])>=.05)))
    out["interpretation"] = "Prediction of gap from age/count alone; failure to predict does not prove a correct draw anchor. No mechanism pass is inferred from an insignificant coefficient."
    return clean(out)


def matched_control(audit, trades, name="control_75_85"):
    """Prespecified 1c decision-price bins; equal target bin weights and game-cluster CI.

    Matching is a descriptive comparison of independent strategies, not new fills.
    Bootstrap resamples games jointly, preserving covariance when a game is in both arms.
    """
    out = {}
    for period in ("dev", "holdout"):
        arms = []
        for name_i in ("primary", name):
            a = audit[(audit.family == "leader") & (audit.variant == name_i) & audit.selected & audit.period.eq(period)]
            t = trades[(trades.family == "leader") & (trades.variant == name_i) & trades.slip.eq(.01) & trades.period.eq(period)]
            if not len(a) or not len(t): arms.append(pd.DataFrame()); continue
            t = t.groupby(["signal_id", "event"], as_index=False).agg(cost=("cost_usd", "sum"), pnl=("pnl_usd", "sum"))
            a = a[["signal_id", "reference_price"]].merge(t, on="signal_id", how="left").fillna({"cost":0,"pnl":0})
            a["bin"] = np.floor(a.reference_price*100+1e-8).astype(int)
            arms.append(a)
        if any(a.empty for a in arms): out[period] = dict(status="insufficient_overlap"); continue
        bins = set(arms[0].bin)&set(arms[1].bin)
        a, b = [x[x.bin.isin(bins)].copy() for x in arms]
        if a.empty or b.empty: out[period] = dict(status="no_common_bins"); continue
        target = a.bin.value_counts(normalize=True)
        for x in (a,b):
            x["weight"] = x.bin.map(target)/x.bin.map(x.bin.value_counts())
            x["wpnl"] = x.pnl*x.weight; x["wcost"] = x.cost*x.weight
        av = float(a.wpnl.sum()/a.wcost.sum()) if a.wcost.sum() else np.nan
        bv = float(b.wpnl.sum()/b.wcost.sum()) if b.wcost.sum() else np.nan
        ga = a.groupby("event")[["wpnl","wcost"]].sum().rename(columns={"wpnl":"ap","wcost":"ac"})
        gb = b.groupby("event")[["wpnl","wcost"]].sum().rename(columns={"wpnl":"bp","wcost":"bc"})
        g = ga.join(gb, how="outer").fillna(0)
        lo = hi = np.nan
        if len(g)>=5:
            z=g.to_numpy(); idx=np.random.default_rng(20260922).integers(0,len(g),(1000,len(g)))
            sums=z[idx].sum(axis=1)
            valid=(sums[:,1]>0)&(sums[:,3]>0)
            diff=sums[valid,0]/sums[valid,1]-sums[valid,2]/sums[valid,3]
            if len(diff): lo,hi=np.quantile(diff,[.025,.975])
        out[period]=dict(status="matched_exploratory", overlap_bins=sorted(int(i) for i in bins),
            leader_signals=len(a), control_signals=len(b), leader_overlap_fraction=len(a)/len(arms[0]),
            control_overlap_fraction=len(b)/len(arms[1]), leader_roi=av,control_roi=bv,difference=av-bv,
            difference_ci_lo=lo,difference_ci_hi=hi,slip=.01)
    return clean(out)


def ledger_frame(audit, trades, family):
    a = audit[(audit.family == family) & audit.variant.eq("primary")]
    # The added-time primary ledger uses the prespecified headline +1c stress.
    slip = .01 if family == "leader" else 0.
    t = trades[(trades.family == family) & trades.variant.eq("primary") & trades.slip.eq(slip)].copy()
    represented = set(t.signal_id) if len(t) else set()
    absent = a[~a.signal_id.isin(represented)].copy()
    if len(absent):
        absent["status"] = "ineligible"; absent["sport"] = "soccer"
        absent["market"] = None; absent["side"] = None; absent["entry_price"] = np.nan; absent["entry_ts"] = np.nan
        for col in ("shares", "stake_usd", "fee_usd", "cost_usd", "payout", "pnl_usd"): absent[col] = 0.
        absent["roi"] = np.nan; absent["exit_kind"] = "unfilled"; absent["note"] = absent.reason
        t = pd.concat([t, absent], ignore_index=True)
    for c in ("shares", "stake_usd", "fee_usd", "cost_usd", "payout", "pnl_usd", "roi",
              "entry_price", "entry_ts", "status", "signal_ts", "event"):
        if c not in t:
            t[c] = pd.Series(dtype=object if c in ("status", "event") else float)
    return t


def report_and_export(audit, trades, coverage, run_info):
    results = {}
    for cfg in configs():
        family, name = cfg["family"], cfg["name"]
        a = audit[(audit.family == family) & audit.variant.eq(name)]
        t = trades[(trades.family == family) & trades.variant.eq(name)]
        results[f"{family}/{name}"] = {period: {str(slip): summary(a[a.period.eq(period)], t[t.period.eq(period) & t.slip.eq(slip)]) for slip in (0.,.01)} for period in ("dev","holdout")}
    controls = dict(sub_coherence=coherence(audit), anchor_staleness=staleness_control(audit),
                    added_time_vs_control=matched_control(audit,trades),
                    added_time_vs_70_80=matched_control(audit,trades,"control_70_80"))
    payload = clean(dict(run=run_info,coverage=coverage,results=results,controls=controls))
    OUT.mkdir(parents=True, exist_ok=True)
    audit.to_parquet(OUT/"signals.parquet", index=False)
    trades.to_parquet(OUT/"trades.parquet", index=False)
    (OUT/"results.json").write_text(json.dumps(payload,indent=2,allow_nan=False))
    paragraphs = ["# Five soccer hypotheses: causal continuation", "", "**No executable edge is established.** These are historically explored transaction proxies. The atomic three-book/depth claim is blocked by absent soccer order books; no historical public-feed receipt clocks were captured.", "",
        f"Run: {run_info['games']} games, {run_info['events']} events; {run_info['scope']}; action policy {ACTION_POLICY}. Development precedes July 1, 2026; the later period was already inspected and is exploratory, not fresh confirmation.", "",
        "| Hypothesis | Period | Selected | Funded signals | Capital | Net P&L | ROI | 95% game CI | +1c ROI |",
        "|---|---|---:|---:|---:|---:|---:|---|---:|"]
    pct=lambda x: "n/a" if x is None else f"{100*x:+.2f}%"
    for family in SLUGS:
        for period in ("dev","holdout"):
            s=results[f"{family}/primary"][period]["0.0"]; stress=results[f"{family}/primary"][period]["0.01"]
            paragraphs.append(f"| {TITLES[family]} | {period} | {s['signals']} | {s['filled_signals']} | ${s['capital_usd']:.2f} | ${s['pnl_usd']:.2f} | {pct(s['roi'])} | {pct(s['ci_lo'])} to {pct(s['ci_hi'])} | {pct(stress['roi'])} |")
    paragraphs += ["", "## Frozen rules and causal corrections", "",
        "Red-card primary: first qualifying card at minute ≤65 when the carded team is not leading; buy opponent Yes. Late ≥70 is separate. Substitution primary: first qualifying first-half substitution ≤25, opponent reference 15–85c; this is an early-substitution proxy, with explicit injury text separately labeled. Both decide 300s after the event, require three previously observed reference probabilities, then enter strictly after another 3s within 600s. These delay corrections replace future h300 selection. An opponent may itself have an earlier red; the primary follows the original team-side rule and does not establish the 11-versus-10 mechanism.", "",
        "Dutch-book: first literal BUY-Yes observations strictly after event+3 through event+60, ≤5s span; decide on the latest print. Trigger all-in observed unit cost <0.995. Submit NEW later orders after 3s; the observation prints are never execution. The No mirror requires literal BUY-No observations and <1.995, not complemented Yes asks. Equal quantities are fixed from these known observations, capped by their known sizes and $100/event. A void may pay 1.5 across three Yes or No legs; the advertised $1/$2 locks require compatible nonvoid settlement rules. No atomic or depth-based implementation is claimed.", "",
        "Draw anchor: analogous three literal BUY-Yes observations, 5s span, |sum−1|≥5c. Underround buys both teams Yes; overround buys both No only when literal BUY-No references are available within 5s. Fixed share counts are proportional to the required-side prices. The original unspecified proportional sizing coefficient is frozen at min($100,1000×|gap|,smaller known leg notional). Entries occur after a new 3s delay. One first qualifying order per game. The 60s observation lookout is a declared causal bound.", "",
        "Pairs receive a 60s entry window. Any leg failing its prescribed quantity triggers an attempted unwind of every acquired leg, using the first later literal SELL of the held native token strictly after deadline+3 within 120s and before the regulation whistle. The SELL normalizes to opposite-side q, so 1−q recovers its actual sale price before slippage; it is still a historical transaction proxy, shares are capped, exit fees are charged, and all unsold residuals settle. Complete pairs settle. Each policy uses a shared print-capacity pool; independent policies reset it. Capital is $100/event including entry fees and is never recycled. Exit fees are additionally reported in total cost and ROI. No fill is required for a signal to remain in the audit.", "",
        "Added time: parse explicit base+N clock, period 2 only, no ties or terminal-whistle rows, leader reference 60–97c, first qualifying event. Enter after 3s within 120s; +1c is the headline ledger sensitivity. Price-matched 75–85 and 70–80 controls use the same rule and prespecified 1c decision-price bins. Both arms retain no-fills and compare only their common price support. Joint game bootstrap preserves same-game covariance. Control ROI is a reweighted comparison, not additional simulated capacity.", "",
        "## Mandatory checks and interpretation", "",
        "Substitution coherence uses the same selected rows for all three leg errors, with a declared 1c mean-sum tolerance. The sum equals payout sum minus reference-price sum; failure is a coherence-gate failure, not proof of a particular causal selection mechanism. Anchor staleness regresses gap on log(1+age/count) using DEV only, reports historical out-of-period fit and residual violation rates. Failure of that simple model to explain a gap does not establish a valid draw anchor. Positive added-time ROI alone is insufficient: it must outperform the price-matched control, with uncertainty and overlap reported.", "", "```json", json.dumps(controls,indent=2,allow_nan=False), "```", "",
        "## Coverage and limitations", "", "```json", json.dumps(clean(coverage),indent=2), "```", "",
        "Native asset IDs identify the traded token. Literal BUY of the intended token is mandatory for detection/entry, and literal SELL of the held token is mandatory for unwind; a complementary trade cannot substitute for either action. The saved trade audit preserves raw action, native token, raw price and print identity. Historical single-leg reference probabilities may still use normalized SELL complements, explicitly separated from execution. Extra tapes contain only ts/p_yes/size: they can provide past reference probabilities but never acquired-side detection, entry or exit capacity. References must be strictly earlier than decision (max age 600s, 120s for added time); no interpolation or invented asks. The all-three-price gate remains an explicit rejection reason. Market terminal payouts include valid 0.5 voids; no winner-only selection or future final-score/feed-consistency eligibility gate is used. Final quality flags are audit fields.", "",
        "ESPN wallclocks are retrospectively stored event timestamps, not measured arrival at a free feed. Even the delayed entries may be optimistic. Regulation whistle/closure expiries analytically censor eligibility; they do not promise exchange-valid short GTD orders or guaranteed cancellation. Printed sizes bound hypothetical capacity, not available order-book depth, minimum order admissibility, or fills obtainable by us. API coverage remains incomplete and the collected universe carries legacy selection effects. ROI includes entry and exit fees; bootstrap intervals are nominal game-cluster intervals, not multiple-testing-adjusted evidence or p-values.", "",
        "## Completed scope and remaining variants", "",
        "Implemented: all five primary transaction protocols, red-card late branch, mandatory substitution coherence, anchor staleness, and added-time matched controls. Atomic three-book execution and soccer live depth: blocked by no captured soccer books. Detailed completed variant names and both slippage scenarios appear in results.json. Unrun optional variants: red-card matched non-card control, added draw-No leg and alternate late cuts; substitution <2c movement gate; Dutch pregame control; anchor per-leg draw-fair sizing. League/goal-type/lead/home-away splits remain available in full audits but are not claimed run. No variant is selected as a winner.", "",
        "Artifacts: data/research/soccer_continuation/{signals.parquet,trades.parquet,results.json,manifest.json}; five complete canonical ledgers in data/research/ledgers. Every initial candidate is audited; trade rows include every submitted leg including no fills. Ledgers also include rejected candidates with zero capital, so candidate/leg totals differ from funded signal counts.", "",
        "Rerun: `OPENBLAS_NUM_THREADS=1 .venv/bin/python -m pmsports.research.h_soccer_continuation --run`. Bounded smoke: append `--max-games 25`; smoke output is explicitly labeled and cannot be presented as full-corpus evidence.", ""]
    REPORT.write_text("\n".join(paragraphs))
    for family, slug in SLUGS.items():
        t=ledger_frame(audit,trades,family)
        verdict="INCONCLUSIVE"
        if family=="sub" and any(x.get("gate")=="fails_1c_tolerance" for x in controls["sub_coherence"].values()): verdict="DEAD"
        if family=="leader":
            x=controls["added_time_vs_control"].get("holdout",{})
            if x.get("difference") is not None and x["difference"]<=0: verdict="DEAD"
        meta=dict(slug=slug,title=TITLES[family],group="Thorp hypotheses",sport="soccer",verdict=verdict,
            action_policy=ACTION_POLICY,
            hypothesis=TITLES[family],mechanism="Original frozen soccer hypothesis; causal transaction-proxy test only",
            entry_rule="Literal BUY of the intended native token strictly after decision plus delay; references are not execution",
            exit_rule="Settlement; failed pairs attempt bounded literal SELL of held tokens and retain unsold residual exposure",
            cost_model="Actual allocated shares; historical market fees; ROI divides net P&L by entry principal plus all charged fees",
            periods={"dev":"before 2026-07-01","holdout":"2026-07-01 onward, already explored"},
            caveats=["No measured historical receipt latency or executable soccer depth", "Literal BUY entry and literal SELL unwind; economic complements are reference-only", "No atomic execution proof", "Full candidate and leg audit; no-fills are zero capital", f"{run_info['scope']}; slippage {'.01' if family=='leader' else '0'}"],
            report_path="reports/research/SOCCER_CONTINUATION.md",code_path="pmsports/research/h_soccer_continuation.py")
        doc=_document(meta,t)
        target=C.RESEARCH/"ledgers"/f"{slug}.json";target.parent.mkdir(parents=True,exist_ok=True)
        temporary=target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(doc,separators=(",",":"),allow_nan=False))
        temporary.replace(target)
    return payload


def run(max_games=None):
    started=time.monotonic(); panel=pd.read_parquet(PANEL)
    groups=panel.groupby("event_slug",sort=True)
    keys=list(groups.groups)
    if max_games is not None: keys=keys[:max_games]
    panel=panel[panel.event_slug.isin(keys)].copy()
    cids=set(pd.concat([panel[f"cid_{l}"] for l in LEGS]).dropna())
    meta=market_metadata(cids)
    all_a,all_t=[],[]
    coverage=dict(native_leg_games=0,extra_reference_leg_games=0,all_three_native_games=0,missing_metadata_legs=0,
        soccer_captured_book_games=0,book_validation_status="blocked: local capture is MLB-only; no soccer CID overlap",
        extra_tape_schema="ts,p_yes,size only; acquired direction and wallet identity unavailable")
    conf=configs()
    for n,(event,p) in enumerate(panel.groupby("event_slug",sort=True),1):
        game=GameData(p,meta)
        game_a,game_t=[],[]
        coverage["native_leg_games"]+=game.coverage["native"]
        coverage["extra_reference_leg_games"]+=game.coverage["extra_reference"]
        coverage["all_three_native_games"]+=int(game.coverage["native"]==3)
        coverage["missing_metadata_legs"]+=sum(x is None for x in game.meta.values())
        for cfg in conf:
            a,o=make_signals(game,cfg)
            if len(a): game_a.append(a)
            if len(o):
                for slip in (0.,.01): game_t.append(execute(game,o,slip))
        # Retain at most one audit/trade frame per game instead of thousands of
        # tiny per-configuration frames. Concatenation preserves case/row order.
        if game_a: all_a.append(pd.concat(game_a,ignore_index=True))
        if game_t: all_t.append(pd.concat(game_t,ignore_index=True))
        if n%100==0: print(f"soccer {n}/{len(keys)} games; {time.monotonic()-started:.1f}s",flush=True)
    audit=pd.concat(all_a,ignore_index=True) if all_a else pd.DataFrame(columns=["family","variant","period","selected","reason","event","gap"])
    trades=pd.concat(all_t,ignore_index=True) if all_t else pd.DataFrame(columns=["family","variant","period","slip","signal_id","event","cost_usd"])
    run_info=dict(games=len(keys),events=len(panel),elapsed_s=time.monotonic()-started,scope="full local panel" if max_games is None else f"bounded smoke max_games={max_games}",execution=VERSION,action_policy=ACTION_POLICY)
    result=report_and_export(audit,trades,coverage,run_info)
    inputs=[PANEL,C.DATA/"wallets/universe.parquet"]+[p for cid in cids for p in (TAPES/f"{cid}.parquet",EXTRAS/f"{cid}.parquet") if p.exists()]
    manifest=dict(run=run_info,source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        execution_sha256=hashlib.sha256((ROOT/"pmsports/execution.py").read_bytes()).hexdigest(),
        inputs={str(p.relative_to(ROOT)):[p.stat().st_size,p.stat().st_mtime_ns] for p in inputs})
    (OUT/"manifest.json").write_text(json.dumps(manifest,indent=2))
    print(json.dumps(clean(dict(run=run_info,coverage=coverage)),indent=2),flush=True)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run",action="store_true")
    parser.add_argument("--max-games",type=int)
    args=parser.parse_args()
    if args.max_games is not None and args.max_games<=0: parser.error("--max-games must be positive")
    if args.run: run(args.max_games)
    else: parser.print_help()


if __name__=="__main__": main()
