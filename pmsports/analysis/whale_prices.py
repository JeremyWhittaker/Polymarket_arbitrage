"""Does big money win at particular price levels, and could a follower ride it?

python -m pmsports.analysis.whale_prices  ->  reports/whale_prices.csv

Why this exists: the dollar-weighted calibration table shows +6.8% at 74-75c, but one bet per
market at those prices returns -0.4%. The gap is that the dollars belong to a few thousand large
tickets. This module asks the follow-up directly: at each price level, how do fills of >= $10k do,
does a follower entering seconds later capture it, and does an ordinary-sized fill at the same
price do the same (the placebo)?

Result as of 2026-09: positive only at exactly 74c and 75c (+10.6% / +7.1%, in both development
and holdout), flat or negative at 73c, 76c, 77c, 80c, 85c, 90c, 95c, with no structural difference
(negRisk share, in-play share, ticket size, leagues and months all look ordinary). An effect that
switches on at 74c and off at 73c is an anomaly to investigate, NOT an edge to trade.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from ..polymarket import taker_fee
from ..research.common import cluster_ci, fills, markets

log = logging.getLogger("pmsports")
REPORTS = Path(__file__).resolve().parents[2] / "reports"
SPLIT_TS = pd.Timestamp("2026-07-01", tz="UTC").timestamp()
STAKE = 100.0
WHALE_USD = 10_000
SMALL_USD = 1_000
PRICES = [0.60, 0.65, 0.70, 0.72, 0.73, 0.74, 0.75, 0.76, 0.77, 0.78, 0.80, 0.85, 0.90, 0.95]


def load() -> pd.DataFrame:
    mk = markets()
    mk = mk[(mk.pre_usd.fillna(0) >= 25_000) & mk.game_start_ts.notna()]
    f = fills(markets=mk.m.to_numpy(), columns=["m", "ts", "q", "y", "s", "size", "fee_rate", "in_play"])
    f = f[(f.y != 0.5) & f.q.between(0.02, 0.995)]
    meta = mk.set_index("m")
    pos = pd.Series(np.arange(len(meta)), index=meta.index)
    row = pos.reindex(f.m).to_numpy()
    f = f.assign(ev=pd.factorize(meta.event_slug.to_numpy())[0][row].astype(np.int32),
                 usd=(f["size"] * f.q).to_numpy())
    return f.sort_values(["m", "s", "ts"], kind="stable").reset_index(drop=True)


def _roi(q, y, rate):
    fee = taker_fee(1.0, q, rate)
    return (y - q - fee) / (q + fee)


def follower_entries(f: pd.DataFrame, signal: np.ndarray, delay: int = 3, horizon: int = 600) -> np.ndarray:
    """Index of the next fill on the same market+side at least `delay` seconds after each signal."""
    key = f.m.to_numpy().astype(np.int64) * 2 + f.s.to_numpy()
    ts = f.ts.to_numpy()
    start = np.searchsorted(key, key, side="left")
    end = np.searchsorted(key, key, side="right")
    out = []
    for i in signal:
        a, b = start[i], end[i]
        j = a + np.searchsorted(ts[a:b], ts[i] + delay, side="left")
        if j < b and ts[j] - ts[i] <= horizon:
            out.append(j)
    return np.array(out, dtype=int)


def table(f: pd.DataFrame, tol: float = 0.005) -> pd.DataFrame:
    q, y, fr, ts, ev, usd = (f.q.to_numpy(), f.y.to_numpy(), f.fee_rate.to_numpy(),
                             f.ts.to_numpy(), f.ev.to_numpy(), f.usd.to_numpy())
    rng = np.random.default_rng(0)
    rows = []
    for price in PRICES:
        at = np.abs(q - price) <= tol
        for label, sel in (("whale", at & (usd >= WHALE_USD)), ("placebo", at & (usd < SMALL_USD))):
            idx = np.flatnonzero(sel)
            if label == "placebo" and len(idx) > 6000:
                idx = np.sort(rng.choice(idx, 6000, replace=False))
            if len(idx) < 60:
                continue
            roi = _roi(q[idx], y[idx], fr[idx])
            m, lo, hi = cluster_ci(roi, ev[idx])
            rec = {"price": price, "who": label, "fills": len(idx), "games": int(pd.unique(ev[idx]).size),
                   "won_pct": float(y[idx].mean()), "roi": m, "ci_lo": lo, "ci_hi": hi}
            for per, mask in (("dev", ts[idx] < SPLIT_TS), ("holdout", ts[idx] >= SPLIT_TS)):
                if mask.sum() >= 40:
                    mm, _, _ = cluster_ci(roi[mask], ev[idx][mask])
                    rec[f"roi_{per}"] = mm
                    rec[f"bets_{per}"] = int(mask.sum())
            if label == "whale":                       # can a follower ride it, entering 3s later?
                e = follower_entries(f, idx)
                if len(e) >= 60:
                    fr_roi = _roi(q[e], y[e], fr[e])
                    fm, flo, fhi = cluster_ci(fr_roi, ev[e])
                    rec.update({"follower_bets": len(e), "follower_roi": fm,
                                "follower_ci_lo": flo, "follower_ci_hi": fhi})
            rows.append(rec)
    return pd.DataFrame(rows)


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    f = load()
    t = table(f)
    REPORTS.mkdir(exist_ok=True)
    t.to_csv(REPORTS / "whale_prices.csv", index=False)
    print(t.round(4).to_string(index=False))
    e = ev_table(f)
    e.to_csv(REPORTS / "expected_value_by_price.csv", index=False)
    print("\nExpected value per price (one bet per game, after fees):")
    print(e.round(3).to_string(index=False))
    pos = e[(e.ev_per_100usd > 0) & (e.ci_lo > 0)]
    print(f"prices with positive EV and a CI clear of zero: {pos.price_c.tolist() or 'none'}; "
          f"average EV per $100 = ${e.ev_per_100usd.mean():.2f}")




def ev_table(f: pd.DataFrame) -> pd.DataFrame:
    """Thorp-style expected value per price: EV = P(win)*$1 - (price + fee), one bet per game."""
    q, y, fr, ev, m, ts = (f.q.to_numpy(), f.y.to_numpy(), f.fee_rate.to_numpy(),
                           f.ev.to_numpy(), f.m.to_numpy(), f.ts.to_numpy())
    pt = np.floor(q * 100).astype(int)
    order = np.lexsort((ts, m))
    rows = []
    for c in range(50, 100):
        sel = order[pt[order] == c]
        if len(sel) < 200:
            continue
        _, firsts = np.unique(m[sel], return_index=True)     # earliest fill at that cent, per market
        i = sel[firsts]
        price = q[i]
        fee = taker_fee(1.0, price, fr[i])
        realized = y[i] - price - fee                        # EV per share, in dollars
        roi = realized / (price + fee)
        mm, lo, hi = cluster_ci(roi, ev[i])
        rows.append({"price_c": c, "bets": len(i), "p_win": float(y[i].mean()),
                     "cost_per_share": float((price + fee).mean()),
                     "ev_per_share_c": 100 * float(realized.mean()),
                     "ev_per_100usd": 100 * mm, "ci_lo": 100 * lo, "ci_hi": 100 * hi})
    return pd.DataFrame(rows)


if __name__ == "__main__":
    run()
