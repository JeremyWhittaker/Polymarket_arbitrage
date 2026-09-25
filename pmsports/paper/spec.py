"""Frozen v1 paper-test rules (reports/PROSPECTIVE_*PROTOCOL.md). Changing any value is a new version.

`report_only` policies are recomputed from another policy's fills at report time; the engine
never executes them. Soccer delays: eligible = receipt + max(min_delay_s, seconds_delay) +
extra_delay_s, so the 10/60-second sensitivities are total entry delays (as in the historical
configs) and the +2-second sensitivity is added to the primary's delay.
"""
from __future__ import annotations

import hashlib
import json
from types import MappingProxyType

VERSION = "v1"
COMMON = dict(budget_usd=100.0, limit_offset=0.01, reference_max_age_s=120, freshness_ms=5000,
              reference_strictly_before_signal=True, first_signal_per_game=True, hold_to_resolution=True,
              us_fee_coefficient=0.0695, bootstrap=dict(resamples=2000, seed=0))
_MLB = dict(sport="mlb", signal="half_inning_start", mapping="exact", model="_baseline_we prior seasons",
            model_min_n=30, model_shrink=[5, 10], model_fallback=0.5, min_delay_s=0)
_SOC = dict(sport="soccer", signal="first_new_key_event_period2_with_lead", mapping="exact",
            band=[0.60, 0.97], min_delay_s=3)
_RULES = {
    "mlb_10c": {**_MLB, "role": "primary", "threshold": 0.10, "extra_delay_s": 0},
    "mlb_03c": {**_MLB, "role": "control", "threshold": 0.03, "extra_delay_s": 0},
    "mlb_10c_delay2": {**_MLB, "role": "sensitivity", "threshold": 0.10, "extra_delay_s": 2, "base": "mlb_10c"},
    "mlb_10c_plus1c": {**_MLB, "role": "sensitivity", "base": "mlb_10c", "price_adj": 0.01, "report_only": True},
    "soccer_added_time": {**_SOC, "role": "primary", "window": [90, None], "extra_delay_s": 0},
    "soccer_ctrl_75_85": {**_SOC, "role": "control", "window": [75, 85], "extra_delay_s": 0},
    "soccer_ctrl_70_80": {**_SOC, "role": "control", "window": [70, 80], "extra_delay_s": 0},
    "soccer_delay2": {**_SOC, "role": "sensitivity", "window": [90, None], "extra_delay_s": 2, "base": "soccer_added_time"},
    "soccer_delay10": {**_SOC, "role": "sensitivity", "window": [90, None], "min_delay_s": 10, "extra_delay_s": 0,
                       "base": "soccer_added_time"},
    "soccer_delay60": {**_SOC, "role": "sensitivity", "window": [90, None], "min_delay_s": 60, "extra_delay_s": 0,
                       "base": "soccer_added_time"},
    "soccer_added_time_plus1c": {**_SOC, "role": "sensitivity", "base": "soccer_added_time", "price_adj": 0.01,
                                 "report_only": True},
}


def _freeze(x):
    if isinstance(x, dict):
        return MappingProxyType({k: _freeze(v) for k, v in x.items()})
    return tuple(_freeze(v) for v in x) if isinstance(x, list) else x


ENDPOINTS = _freeze({
    "mlb": dict(primary="mlb_10c", games=500, days=365, min_funded=100, count="eligible games with outcome"),
    "soccer": dict(primary="soccer_added_time", games=200, days=183, min_funded=100, count="funded primary games")})
RULES = _freeze({p: {"version": VERSION, "policy": p, **COMMON, **r} for p, r in _RULES.items()})
POLICIES = tuple(RULES)
EXECUTED = tuple(p for p in POLICIES if not RULES[p].get("report_only"))


def plain(x):
    """JSON-ready copy of a frozen rule."""
    if isinstance(x, (dict, MappingProxyType)):
        return {k: plain(v) for k, v in x.items()}
    return [plain(v) for v in x] if isinstance(x, tuple) else x


def canonical(x) -> bytes:
    return json.dumps(plain(x), sort_keys=True, separators=(",", ":")).encode()


def rule_hash(policy: str) -> str:
    return hashlib.sha256(canonical(RULES[policy])).hexdigest()


def spec_hash() -> str:
    return hashlib.sha256(canonical({"version": VERSION, "rules": RULES, "endpoints": ENDPOINTS})).hexdigest()


def policies(sport: str, executed: bool = True) -> list[str]:
    return [p for p in POLICIES if RULES[p]["sport"] == sport and (not executed or not RULES[p].get("report_only"))]
