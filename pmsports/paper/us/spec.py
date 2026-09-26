"""Frozen US-venue rules `us-v1` (reports/PROSPECTIVE_US_VENUE_PROTOCOL.md). Changing any value is a new version.

Every US policy runs one frozen v1 trigger unchanged: `trigger` names the v1 policy whose signal,
threshold, soccer window and price band it reuses, and `trigger_rule_sha256` pins that v1 rule, so
a v1 rule change also changes this spec's hash. What differs is the venue:

- reference: the last Polymarket US trade on the leader's side, received strictly before the
  trigger and at most 120 s old (short side = 1 - traded long price);
- entry: receipt + min_delay_s + extra_delay_s (the US venue publishes no per-market matching
  delay): MLB 0 s, soccer 3 s, +2 s sensitivities, 10 s / 60 s total soccer delays;
- one IOC limit buy at reference + 0.01 rounded down to the market tick, quantity the largest
  multiple of the market increment (whole contracts unless us_map says otherwise) whose cost at the
  limit including the US taker fee is <= $100, against the received US book;
- fee 0.0695 x C x p x (1 - p) (from 2026-09-25) unless the market metadata carries another
  coefficient; settlement at the US market's published settlement price.
"""
from __future__ import annotations

import hashlib

from .. import spec as v1

VERSION = "us-v1"
VENUE = "polymarket_us"
FEE_COEFFICIENT = 0.0695
COMMON = dict(venue=VENUE, budget_usd=100.0, limit_offset=0.01, reference_max_age_s=120, freshness_ms=5000,
              reference_strictly_before_signal=True, first_signal_per_game=True, hold_to_resolution=True,
              fee_coefficient=FEE_COEFFICIENT, fee_effective_utc="2026-09-25", quantity_increment_default=1.0,
              order="limit, immediate-or-cancel, received-book crossing, partial fills kept",
              reference="last US trade on the leader's side strictly before the trigger; short side = 1 - long price",
              book="MARKET_DATA full long-side snapshots; BUY_LONG crosses offers, BUY_SHORT crosses long bids at 1 - bid",
              freshness="a book message for the market within 5 s of entry, no disconnect since",
              admission="latest us_map record received at or before the start, match exact",
              settlement="US /v1/markets/{slug}/settlement (long-side price)",
              preview_audit=dict(max_lag_s=15, max_per_s=2, affects_decisions=False),
              bootstrap=dict(resamples=2000, seed=0))

# US policy -> frozen v1 trigger policy
TRIGGERS = {
    "mlb_10c_us": "mlb_10c",
    "mlb_03c_us": "mlb_03c",
    "mlb_10c_us_delay2": "mlb_10c_delay2",
    "mlb_10c_us_plus1c": "mlb_10c_plus1c",
    "soccer_added_time_us": "soccer_added_time",
    "soccer_ctrl_75_85_us": "soccer_ctrl_75_85",
    "soccer_ctrl_70_80_us": "soccer_ctrl_70_80",
    "soccer_us_delay2": "soccer_delay2",
    "soccer_us_delay10": "soccer_delay10",
    "soccer_us_delay60": "soccer_delay60",
    "soccer_added_time_us_plus1c": "soccer_added_time_plus1c",
}
US_NAME = {v: k for k, v in TRIGGERS.items()}
_TRIGGER_KEYS = ("sport", "role", "signal", "mapping", "threshold", "window", "band", "min_delay_s", "extra_delay_s",
                 "price_adj", "report_only", "model", "model_min_n", "model_shrink", "model_fallback")


def _rule(policy: str, trigger: str) -> dict:
    r = v1.plain(v1.RULES[trigger])
    out = {k: r[k] for k in _TRIGGER_KEYS if k in r}
    if "base" in r:
        out["base"] = US_NAME[r["base"]]
    return {"version": VERSION, "policy": policy, **COMMON, **out, "trigger": trigger, "trigger_version": v1.VERSION,
            "trigger_rule_sha256": v1.rule_hash(trigger)}


RULES = v1._freeze({p: _rule(p, t) for p, t in TRIGGERS.items()})
POLICIES = tuple(RULES)
EXECUTED = tuple(p for p in POLICIES if not RULES[p].get("report_only"))
ENDPOINTS = v1._freeze({s: {**v1.plain(e), "primary": US_NAME[e["primary"]]} for s, e in v1.ENDPOINTS.items()})
SENSITIVITY_DELAY2 = {"mlb": "mlb_10c_us_delay2", "soccer": "soccer_us_delay2"}
CONTROLS = {"mlb": ("mlb_03c_us",), "soccer": ("soccer_ctrl_75_85_us", "soccer_ctrl_70_80_us")}
assert set(US_NAME) == set(v1.POLICIES), "every frozen v1 trigger needs exactly one US policy"


def delay_ms(policy: str) -> int:
    r = RULES[policy]
    return int(round((float(r["min_delay_s"]) + float(r["extra_delay_s"])) * 1000))


def rule_hash(policy: str) -> str:
    return hashlib.sha256(v1.canonical(RULES[policy])).hexdigest()


def spec_hash() -> str:
    return hashlib.sha256(v1.canonical({"version": VERSION, "rules": RULES, "endpoints": ENDPOINTS})).hexdigest()


def policies(sport: str, executed: bool = True) -> list[str]:
    return [p for p in POLICIES if RULES[p]["sport"] == sport and (not executed or not RULES[p].get("report_only"))]
