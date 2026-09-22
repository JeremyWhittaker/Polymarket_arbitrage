"""Validate and export corrected esports allocations without rescaling cash.

The primary study also emits this canonical full ledger. This compatibility command
refuses old quote/unit-stake caches and preserves every partial or no-fill signal.
"""
from __future__ import annotations

import json
import numpy as np
import pandas as pd
from pmsports.execution import VERSION
from pmsports.research import common as C
from pmsports.research import h_esports_break_overreaction as study
from pmsports.research.ledger_studies import COLUMNS, _rows, _headline, validate_cash

SLUG = "esports_break_overreaction"
SRC = C.RESEARCH / f"h_{SLUG}"
OUT = C.RESEARCH / "ledgers" / f"{SLUG}.json"
RULE = "primary_thr0.04"


def _validate_source(b):
    required = {"execution_version","rule","event_slug","period","q","rate","won","P_cal","fair_iid","title",
                "fill_ts","signal_ts","eligible_ts","available_shares","shares","stake_usd","fee_usd",
                "cost_usd","payout","pnl_usd","roi","status","t_end","L","how"}
    missing = required-set(b)
    if missing:
        raise ValueError(f"legacy esports cache; rerun corrected analyze --holdout --rebuild (missing {sorted(missing)})")
    if not b.execution_version.eq(VERSION).all() or b.how.eq("quote").any():
        raise ValueError("old or invented-quote execution cache is not exportable")
    validate_cash(b.rename(columns={"q":"entry_price"}))
    filled = b.cost_usd > 0
    if not b.loc[filled,"fill_ts"].gt(b.loc[filled,"eligible_ts"]).all() or not b.loc[filled,"fill_ts"].lt(b.loc[filled,"t_end"]+study.ENT_B).all():
        raise ValueError("esports fill outside causal execution window")
    if (b.shares > b.available_shares.fillna(0)+1e-8).any() or (b.cost_usd > 100+1e-8).any():
        raise ValueError("esports allocation exceeds first-print size or fixed budget")
    if not np.allclose(b.loc[filled,"fee_usd"],C.taker_fee(b.loc[filled,"shares"],b.loc[filled,"q"],b.loc[filled,"rate"]),atol=1e-8,rtol=1e-8):
        raise ValueError("esports fee disagrees with actual allocated shares")
    if not np.allclose(b.payout,b.shares*b.won,atol=1e-8,rtol=1e-8):
        raise ValueError("esports payout disagrees with actual shares")


def load():
    ser = pd.read_parquet(SRC/"series.parquet").set_index("event_slug")
    cal = json.loads((SRC/"calib_v1.json").read_text())
    out = []
    for per in ("dev","holdout"):
        b = pd.read_parquet(SRC/f"bets_{per}.parquet")
        if "rule" not in b:
            raise ValueError("legacy esports cache lacks rule provenance")
        b = b[b.rule == RULE].copy()
        _validate_source(b)
        if not b.period.eq(per).all() or not b.event_slug.is_unique:
            raise ValueError("esports primary period/event identity mismatch")
        if len(b) and not np.allclose(b.P_cal,study.predict(b,cal),atol=1e-10,rtol=1e-8):
            raise ValueError("esports primary cache does not use frozen round1 calibration")
        if not b.event_slug.isin(ser.index).all():
            raise ValueError("esports primary lacks market metadata")
        b["market_slug"] = b.event_slug.map(ser.market_slug)
        b["closed_ts"] = b.event_slug.map(ser.closed_ts)
        o0,o1 = b.event_slug.map(ser.o0),b.event_slug.map(ser.o1)
        b["team_T"] = np.where(b.L == 0,o1,o0)
        out.append(b)
    return pd.concat(out,ignore_index=True).sort_values("signal_ts",kind="stable").reset_index(drop=True)


def _frame(d):
    _validate_source(d)
    t = d.rename(columns={"event_slug":"event","title":"league","q":"entry_price"}).copy()
    t["sport"],t["market"],t["side"] = "esports",d.market_slug,d.team_T
    t["entry_ts"],t["receipt_ts"],t["expiry_ts"] = d.fill_ts,d.signal_ts,d.t_end+study.ENT_B
    t["exit_kind"],t["exit_ts"],t["exit_price"] = "resolution",d.closed_ts,d.won
    t["reference_price"] = d.get("P_break",np.nan)
    t["note"] = "Frozen round1; actual later-print capacity proxy; unknown public receipt/book; no quote fallback or stake rescaling"
    return t


def rows(d):
    t = _frame(d)
    return _rows(t),json.loads(pd.Series(_headline(t)).to_json(double_precision=15))


def build():
    d = load()
    rr,head = rows(d)
    return dict(slug=SLUG,title="Esports between-map overreaction: frozen round1",group="Sports studies",
        sport="esports",verdict="UNVALIDATED",hypothesis="Between-map pressure against a frozen map-level probability model",
        mechanism="Historical hypothesis; positive profitability is not presumed",
        entry_rule="Frozen round1 model;4point gap; actual same-side print strictly after3s;100 inclusive-dollar target capped by first-print shares",
        exit_rule="Settlement of actual allocated shares; all no-fill signals retain zero capital",
        cost_model="Saved actual historical fee/allocation; ROI=P&L/(price stake+fees)",
        periods={"dev":"before2026-07-01","holdout":"July2026 onward; historically explored"},
        caveats=["No quote fallback", "Partial and no-fill signals preserved", "Receipt clocks and resting depth unobserved",
                 "Original round1 calibration is primary; post-inspection refits are exploratory",
                 "Legacy discovery/tape coverage incomplete; tiny print allocations do not prove order admissibility"],
        execution_model=VERSION,headline=head,truncated=False,n_total_trades=len(rr),columns=COLUMNS,rows=rr,
        report_path=f"reports/research/{SLUG}.md",code_path=f"pmsports/research/h_{SLUG}.py")


def verify(led):
    df = pd.DataFrame(led["rows"],columns=led["columns"])
    validate_cash(df)
    if led["truncated"] or len(df) != led["n_total_trades"]:
        raise ValueError("esports ledger dropped source signal rows")
    actual = _headline(df)
    for per,rec in actual.items():
        expected = led["headline"][per]
        for key in ("signals","bets","unfilled","partial","pnl_usd","capital_usd","roi"):
            value = rec[key]
            target = expected[key]
            if pd.isna(value) and target is None:
                continue
            if not np.isclose(value,target,rtol=1e-8,atol=1e-8,equal_nan=True):
                raise ValueError(f"esports ledger headline mismatch: {per}/{key}")
    if not df.signal_ts.is_monotonic_increasing:
        raise ValueError("esports signals are not chronological")


def main():
    led = build()
    verify(led)
    OUT.parent.mkdir(parents=True,exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(led,separators=(",",":"),allow_nan=False))
    tmp.replace(OUT)
    print(f"wrote {OUT}: {led['n_total_trades']}full signal rows")


if __name__ == "__main__":
    main()
