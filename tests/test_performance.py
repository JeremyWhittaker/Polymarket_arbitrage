"""Unit checks for the $100k account simulator in pmsports.analysis.performance (synthetic data only)."""
import numpy as np
import pandas as pd

from pmsports.analysis import performance as P


def _ts(s: str) -> float:
    return pd.Timestamp(s, tz="UTC").timestamp()


def _trades(rows):
    base = dict(rule="10c_first", execution=P.LEDGER, budget_usd=100.0, fee_regime="intl_0.05", fee_rate=0.05,
                event="e", side="home", season=2025, inning=5, half="top", lead=1, fair_price=0.8,
                reference_price=0.65, entry_price=0.66, shares=10.0, stake=6.6, fee=0.0, status="filled")
    out = []
    for i, r in enumerate(rows):
        x = dict(base, signal_idx=i, game_pk=1000 + i, signal_ts=r["entry_ts"] - 10 if r.get("entry_ts") else _ts("2025-04-01"))
        x.update(r)
        out.append(x)
    return pd.DataFrame(out)


def test_cash_accounting_and_daily_returns():
    cal = pd.date_range("2025-04-01", "2025-04-04", freq="D")
    tr = _trades([
        dict(entry_ts=_ts("2025-04-01 18:00"), exit_ts=_ts("2025-04-01 21:00"), cost=60.0, payout=100.0, pnl=40.0),
        dict(entry_ts=_ts("2025-04-02 23:00"), exit_ts=_ts("2025-04-03 02:00"), cost=50.0, payout=0.0, pnl=-50.0),
        dict(entry_ts=np.nan, exit_ts=_ts("2025-04-03 02:00"), cost=0.0, payout=0.0, pnl=0.0, status="unfilled"),
    ])
    sim = P.simulate(tr, cal)
    d = sim["daily"].set_index("date")
    assert len(sim["trades"]) == 2 and len(sim["skipped"]) == 0
    assert d.loc["2025-04-01", "pnl"] == 40.0
    assert d.loc["2025-04-03", "pnl"] == -50.0
    assert np.isclose(d.loc["2025-04-03", "return"], -50.0 / 100_040.0)
    assert d.loc["2025-04-02", "committed"] == 50.0          # overnight position carried at cost
    assert np.isclose(sim["peak_committed"], 60.0)
    dd = P.drawdown_stats(sim["daily"])
    assert np.isclose(dd["max_dd_usd"], -50.0)
    assert dd["longest_dd_recovered"] is False


def test_entries_beyond_free_cash_are_skipped():
    cal = pd.date_range("2025-04-01", "2025-04-02", freq="D")
    tr = _trades([
        dict(entry_ts=_ts("2025-04-01 18:00"), exit_ts=_ts("2025-04-01 22:00"), cost=80_000.0, payout=0.0, pnl=-80_000.0),
        dict(entry_ts=_ts("2025-04-01 19:00"), exit_ts=_ts("2025-04-01 22:00"), cost=30_000.0, payout=40_000.0, pnl=10_000.0),
        dict(entry_ts=_ts("2025-04-01 22:00"), exit_ts=_ts("2025-04-02 01:00"), cost=20_000.0, payout=0.0, pnl=-20_000.0),
    ])
    sim = P.simulate(tr, cal)
    assert len(sim["skipped"]) == 1                            # 30k does not fit next to 80k
    assert len(sim["trades"]) == 2                             # the 22:00 entry fits after the 22:00 resolution
    assert sim["peak_committed_pct_equity"] <= 1.0
