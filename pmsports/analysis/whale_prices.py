"""Prespecified74/75c whale lead versus73/76c comparator; historical tape sensitivity.

These previously inspected windows cannot establish a fresh confirmatory edge.
Every selected signal is retained, including no-fill and event-cap exclusions.
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
from ..polymarket import taker_fee
from ..execution import TapeReplay
from ..research.common import cluster_ci
from .calibration import _load, SPLIT_TS, LEDGERS

REPORTS = Path(__file__).resolve().parents[2] / "reports"
STAKE = 100.0
WHALE_USD = 10_000
PRICES = [.73, .74, .75, .76]


def load() -> pd.DataFrame:
    f, _ = _load()
    return f.assign(ev=f.event, usd=f["size"] * f.q).reset_index(drop=True)


def _roi(q, y, rate):
    fee = taker_fee(1.0, q, rate)
    return (y - q - fee) / (q + fee)


def follower_entries(f: pd.DataFrame, signal: np.ndarray, delay: int = 3, horizon: int = 600,
                     proportional=False, replay=None, slip=0.0) -> pd.DataFrame:
    """Full signal-to-fill mapping; each invocation is one capacity/exposure replay."""
    sig = f.iloc[signal].copy()
    budget = np.minimum(STAKE, .001 * sig.usd) if proportional else np.full(len(sig), STAKE)
    orders = pd.DataFrame({"m": sig.m, "s": sig.s, "signal_ts": sig.ts,
        "leader_w": sig.w, "event": sig.ev, "y": sig.y, "budget_usd": budget,
        "expiry_ts": np.minimum(sig.closed_ts, sig.ts + delay + horizon)})
    out = (replay or TapeReplay(f)).replay(orders, delay_s=delay, horizon_s=horizon,
                                         event_cap_usd=STAKE, slip=slip)
    out["signal_row"] = signal
    out["signal_price"] = sig.q.to_numpy()
    out["leader_notional"] = sig.usd.to_numpy()
    out["sport"] = sig.sport.to_numpy()
    out["closed_ts"] = sig.closed_ts.to_numpy()
    out["period"] = np.where(sig.ts.to_numpy() < SPLIT_TS, "dev", "holdout")
    return out


def table(f: pd.DataFrame, tol: float = .005, return_audit=False):
    """No new price search: two prespecified bands, two causal exposure policies."""
    replay = TapeReplay(f)
    rows, audits = [], []
    for band, prices in (("primary74_75", (.74, .75)), ("comparator73_76", (.73, .76))):
        at = np.logical_or.reduce([np.abs(f.q.to_numpy() - p) <= tol for p in prices])
        idx = np.flatnonzero(at & (f.usd >= WHALE_USD) & (f.prior_usd >= 50_000))
        for policy in ("one_per_event", "capped_proportional"):
            use = idx
            if policy == "one_per_event":
                use = f.iloc[idx].assign(_row=idx).sort_values("ts", kind="stable").drop_duplicates("ev")._row.to_numpy()
            for slip in (0., .01):
                a = follower_entries(f, use, proportional=policy == "capped_proportional", replay=replay, slip=slip)
                a["band"], a["policy"], a["slip"] = band, policy, slip
                audits.append(a)
                for per in ("dev", "holdout"):
                    signals = a[a.period == per]
                    g = signals[signals.cost_usd > 0]
                    m, lo, hi = cluster_ci(g.roi, g.event.to_numpy(), weights=g.cost_usd.to_numpy()) if len(g) else (np.nan,) * 3
                    game = g.groupby("event").agg(pnl=("pnl_usd", "sum"), capital=("cost_usd", "sum"))
                    rows.append(dict(band=band, policy=policy, slip=slip, period=per, signals=len(signals),
                        bets=len(g), games=len(game), unfilled=int((signals.cost_usd == 0).sum()),
                        partial=int(signals.status.eq("partial").sum()), roi=m, ci_lo=lo, ci_hi=hi,
                        capital_usd=g.cost_usd.sum(), pnl_usd=g.pnl_usd.sum(), shares=g.shares.sum(),
                        max_game_capital_share=game.capital.max() / game.capital.sum() if len(game) else np.nan,
                        top5_game_pnl=game.pnl.nlargest(5).sum(), median_allocated_usd=g.cost_usd.median()))
    result = pd.DataFrame(rows)
    return (result, pd.concat(audits, ignore_index=True)) if return_audit else result


def run() -> None:
    f = load()
    t, audit = table(f, return_audit=True)
    REPORTS.mkdir(exist_ok=True)
    t.to_csv(REPORTS / "whale_prices.csv", index=False)
    audit.to_parquet(REPORTS / "whale_signal_audit.parquet", index=False)
    ev_table(f).to_csv(REPORTS / "expected_value_by_price.csv", index=False)
    for (band, policy, slip), g in audit.groupby(["band", "policy", "slip"]):
        g = g.copy().sort_values("signal_ts")
        g["id"] = np.arange(1, len(g) + 1)
        g["entry_ts"] = g.fill_ts
        filled = g.shares.gt(0)
        g["exit_kind"] = np.where(filled, "resolution", "unfilled")
        g["exit_ts"] = g.closed_ts.where(filled)
        g["exit_price"] = g.y.where(filled)
        g["note"] = "Historical later-print proxy; public receipt and depth unknown"
        slug = f"whale_{band}_{policy}_{int(slip * 100)}c"
        doc = dict(slug=slug, title=f"Whale {band} {policy} +{slip:.0%}", group="Whale signals",
            sport="multi", verdict="UNVALIDATED", entry_rule="3s delayed other-wallet acquired-side print; prior observed volume50k; first-print partial;100/event including fee",
            exit_rule="Settlement", cost_model="Actual historical fee plus specified price sensitivity",
            caveats=["Incomplete lifetime tapes", "July2026 was already explored", "No-fill signals are zero-capital audit rows", "Proportional policy:0.1% of observable leader notional capped100"],
            columns=list(g), rows=json.loads(g.to_json(orient="values")), truncated=False, n_total_trades=len(g),
            report_path="reports/WHALE_REPAIRED.md", code_path="pmsports/analysis/whale_prices.py")
        LEDGERS.mkdir(exist_ok=True, parents=True)
        tmp = LEDGERS / f"{slug}.json.tmp"
        tmp.write_text(json.dumps(doc, separators=(",", ":"), allow_nan=False))
        tmp.replace(LEDGERS / f"{slug}.json")
    (REPORTS / "WHALE_REPAIRED.md").write_text("# Whale signal repair\n\n" +
        "Prespecified74/75c versus73/76c; >=$10k leader ticket and >=$50k strictly prior observed volume. " +
        "Signal receipt is approximated by transaction timestamp; actual public delay and book depth are unknown. " +
        "Each policy has100 inclusive-dollar lifetime exposure per event. Proportional stakes are0.1% of observed leader notional capped100. " +
        "All signals, partials and no-fills are preserved in whale_signal_audit.parquet and desk ledgers. " +
        "July2026 is historically explored, so these results cannot confirm a new edge.\n\n" +
        t.to_markdown(index=False, floatfmt=".4f") + "\n")
    print(t.round(4).to_string(index=False))


def ev_table(f: pd.DataFrame) -> pd.DataFrame:
    """DESCRIPTIVE settlement calibration at the signal print, not a follower return."""
    q, y, fr, ev, m, ts = (f.q.to_numpy(), f.y.to_numpy(), f.fee_rate.to_numpy(),
                           f.ev.to_numpy(), f.m.to_numpy(), f.ts.to_numpy())
    edges = (np.arange(50, 101) / 100.).astype(q.dtype)
    pt = np.searchsorted(edges, q, side="right") + 49
    pt = np.where((q >= edges[0]) & (q < 1), np.minimum(pt, 99), -1)
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
