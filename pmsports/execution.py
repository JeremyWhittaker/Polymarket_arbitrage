"""Causal, size-bounded historical transaction replay. This is NOT a book simulator.

Normalized `s` is the acquired outcome, including the complement acquired by a SELL.
An observed transaction is a signal or a later price/capacity proxy, never a resting quote.
"""
from __future__ import annotations

import heapq
import numpy as np
import pandas as pd
from .polymarket import taker_fee

VERSION = "causal-tape-v1"


def prior_notional(tape: pd.DataFrame) -> np.ndarray:
    """Notional strictly before each timestamp in its market; ties never leak volume."""
    if tape.empty:
        return np.array([], dtype=float)
    x = tape[["m", "ts"]].copy()
    x["usd"] = tape["size"].to_numpy(float) * tape.q.to_numpy(float)
    totals = x.groupby(["m", "ts"], sort=True).usd.sum().rename("usd").reset_index()
    totals["prior"] = totals.groupby("m", sort=False).usd.cumsum() - totals.usd
    return x[["m", "ts"]].merge(totals[["m", "ts", "prior"]], how="left", on=["m", "ts"],
                                validate="many_to_one").prior.to_numpy()


class TapeReplay:
    """Reusable tape index; liquidity state is reset for each independent strategy replay.

    Orders accept m,s,signal_ts and optional receipt_ts (defaults to signal_ts),
    delay_s,expiry_ts,budget_usd (inclusive of fees; default100),leader_w,event,
    limit_price and y. First relevant print gets one IOC-like allocation; a partial
    order does not aggregate future prints. Multiple orders may share only the
    remaining shares of a print, in chronological fill/eligibility/order order.
    Unknown public receipt latency is an explicit assumption, not measured latency.
    """
    def __init__(self, tape: pd.DataFrame):
        need = {"m", "s", "ts", "q", "size"}
        if not need.issubset(tape):
            raise ValueError(f"missing tape columns: {sorted(need - set(tape))}")
        x = tape[[c for c in ("m", "s", "ts", "q", "size", "w", "fee_rate", "print_id") if c in tape]].copy(deep=False)
        x = x.assign(print_id=tape.get("print_id", pd.Series(np.arange(len(tape)), index=tape.index)))
        # Explicit IDs let callers deduplicate raw API overlaps. Implicit IDs are row identities.
        x = x.drop_duplicates("print_id").sort_values(["m", "s", "ts"], kind="stable")
        good = (np.isfinite(x.ts) & x.q.between(0.000001, .999999) & x["size"].gt(0) & np.isfinite(x["size"])
                & x.s.isin([0, 1]) & x.m.notna())
        self.tape = x[good].reset_index(drop=True)
        mm, ss = self.tape.m.to_numpy(), self.tape.s.to_numpy()
        starts = np.flatnonzero(np.r_[True, (mm[1:] != mm[:-1]) | (ss[1:] != ss[:-1])]) if len(mm) else np.array([], int)
        ends = np.r_[starts[1:], len(mm)] if len(mm) else np.array([], int)
        self.groups = {(mm[a], ss[a]): (a, b) for a, b in zip(starts, ends)}
        self.ts = self.tape.ts.to_numpy(float)
        self.q = self.tape.q.to_numpy(float)
        self.size = self.tape["size"].to_numpy(float)
        self.w = self.tape.get("w", pd.Series(np.full(len(self.tape), None, dtype=object))).to_numpy()
        self.rate = self.tape.get("fee_rate", pd.Series(np.full(len(self.tape), np.nan))).to_numpy(float)
        self.ids = self.tape.print_id.to_numpy()

    def replay(self, orders: pd.DataFrame, delay_s: float = 3, horizon_s: float = 600,
               event_cap_usd: float = 100, slip: float = 0) -> pd.DataFrame:
        if not np.isfinite([delay_s, horizon_s, slip]).all() or delay_s < 0 or horizon_s <= 0 or np.isnan(event_cap_usd) or event_cap_usd < 0 or slip < 0:
            raise ValueError("delay, horizon, cap and slippage must be nonnegative (horizon positive)")
        out = orders.copy().reset_index(drop=True)
        if "signal_ts" not in out:
            raise ValueError("orders require signal_ts")
        n = len(out)
        out["receipt_ts"] = out.get("receipt_ts", out.signal_ts)
        out["eligible_ts"] = out.receipt_ts + out.get("delay_s", delay_s)
        out["expiry_ts"] = out.get("expiry_ts", out.eligible_ts + horizon_s)
        out["budget_usd"] = out.get("budget_usd", 100.0)
        for col in ("fill_ts", "entry_price", "print_id", "roi"):
            out[col] = np.nan
        out["print_id"] = pd.Series([None] * n, dtype=object)
        for col in ("shares", "stake_usd", "fee_usd", "cost_usd", "payout", "pnl_usd", "available_shares"):
            out[col] = 0.0
        out["status"] = "unfilled"
        out["execution_model"] = VERSION
        out["receipt_assumption"] = "signal clock used as receipt" if "receipt_ts" not in orders else "caller supplied"
        # Python records are per order, never per transaction (the full corpus has54M prints).
        records = out.to_dict("records")
        values = {c: out[c].to_numpy(copy=True) for c in ("fill_ts", "entry_price", "print_id", "roi", "shares", "stake_usd", "fee_usd", "cost_usd", "payout", "pnl_usd", "available_shares", "status")}
        remaining = {}
        spent = {}
        heap = []

        def queue(i, offset=0):
            r = records[i]
            bounds = self.groups.get((r["m"], r["s"]))
            if bounds is None:
                return
            a, b = bounds
            if offset == 0:
                # Always strictly after signal/receipt, even when delay is zero.
                floor = max(r["signal_ts"], r["receipt_ts"])
                offset = max(np.searchsorted(self.ts[a:b], r["eligible_ts"], side="right"),
                             np.searchsorted(self.ts[a:b], floor, side="right"))
            while offset < b - a:
                j = a + offset
                if self.ts[j] >= r["expiry_ts"]:
                    return
                leader = r.get("leader_w")
                own = leader is not None and pd.notna(leader) and self.w[j] == leader
                if (not own and self.q[j] + slip <= r.get("limit_price", 1.0)
                        and remaining.get(j, self.size[j]) > 0):
                    heapq.heappush(heap, (self.ts[j], r["eligible_ts"], i, offset, j))
                    return
                offset += 1

        for i, r in enumerate(records):
            if (not np.isfinite(r["signal_ts"]) or not np.isfinite(r["receipt_ts"])
                    or r["receipt_ts"] < r["signal_ts"] or r["eligible_ts"] < r["receipt_ts"]
                    or not np.isfinite([r["eligible_ts"], r["expiry_ts"], r["budget_usd"]]).all()
                    or r["expiry_ts"] <= r["eligible_ts"] or r["budget_usd"] <= 0
                    or np.isnan(r.get("target_shares", r.get("requested_shares", np.inf)))
                    or r.get("target_shares", r.get("requested_shares", np.inf)) <= 0):
                values["status"][i] = "ineligible"
            else:
                queue(i)
        while heap:
            _, _, i, offset, j = heapq.heappop(heap)
            r = records[i]
            avail = remaining.get(j, self.size[j])
            if avail <= 0:
                queue(i, offset + 1)
                continue
            event = r.get("event", r["m"])
            if pd.isna(event):
                event = r["m"]
            budget = min(r["budget_usd"], max(0, event_cap_usd - spent.get(event, 0)))
            if budget <= 1e-10:
                values["status"][i] = "event_cap"
                continue
            price = self.q[j] + slip
            if not 0 < price < 1:
                continue
            rate = self.rate[j]
            if not np.isfinite(rate):
                rate = r.get("fee_rate", np.nan)
            if not np.isfinite(rate) or rate < 0:
                values["status"][i] = "unknown_fee"
                continue
            fee1 = float(taker_fee(1.0, price, rate))
            shares = min(avail, budget / (price + fee1), r.get("target_shares", r.get("requested_shares", np.inf)))
            remaining[j] = avail - shares
            fee, stake = shares * fee1, shares * price
            cost = stake + fee
            spent[event] = spent.get(event, 0) + cost
            payout = shares * r.get("y", np.nan)
            vals = {"fill_ts": self.ts[j], "entry_price": price, "print_id": self.ids[j],
                    "shares": shares, "available_shares": avail, "stake_usd": stake, "fee_usd": fee,
                    "cost_usd": cost, "payout": payout, "pnl_usd": payout - cost,
                    "roi": (payout - cost) / cost if cost > 0 else np.nan,
                    "status": "filled" if (cost >= r["budget_usd"] - 1e-8 or shares >= r.get("target_shares", r.get("requested_shares", np.inf)) - 1e-8) else "partial"}
            for key, value in vals.items():
                values[key][i] = value
        for key, value in values.items():
            out[key] = value
        return out


def panel_entries(rows: pd.DataFrame, buy_home, budget_usd=100.0, slip=0.0,
                  event_cap_usd=100.0) -> pd.DataFrame:
    """Replay side-specific panel candidates; never substitute the reference price.

    The panel contains the first candidate of each side before the next state. The
    underlying prints are deduplicated before replay so repeated states cannot mint depth.
    """
    r = rows.copy().reset_index(drop=True)
    side = np.where(np.asarray(buy_home, bool), 0, 1)
    tape_parts = []
    for s, label in ((0, "home"), (1, "away")):
        prefix = f"exec_{label}_"
        required = [prefix + c for c in ("p", "ts", "size")]
        if not set(required).issubset(r):
            raise ValueError("panel predates causal side-specific execution; rebuild panel")
        t = pd.DataFrame({"m": r.game_pk, "s": s, "ts": r[prefix + "ts"],
                          "q": r[prefix + "p"], "size": r[prefix + "size"], "fee_rate": r.fee_rate})
        # Real raw-row identifier is included by the rebuilt panel.
        t["print_id"] = r.game_pk.astype(str) + ":" + r[prefix + "id"].astype(str)
        tape_parts.append(t)
    tape = pd.concat(tape_parts, ignore_index=True)
    orders = pd.DataFrame({"m": r.game_pk, "s": side, "signal_ts": r.decision_ts,
                           "expiry_ts": r.state_expiry_ts, "event": r.game_pk,
                           "y": np.where(side == 0, r.home_won_final, 1 - r.home_won_final),
                           "budget_usd": budget_usd})
    out = TapeReplay(tape).replay(orders, delay_s=5, slip=slip, event_cap_usd=event_cap_usd)
    out.index = rows.index
    return out
