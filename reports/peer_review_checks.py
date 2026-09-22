"""Bounded, offline diagnostics for PEER_REVIEW.md; never edits research caches.

From the repository root:
  PYTHONDONTWRITEBYTECODE=1 .venv/bin/python reports/peer_review_checks.py > /tmp/review.json

These are exploratory sensitivity checks on already-seen historical data, not a
new out-of-sample backtest or a fill simulator. JSON contains only aggregates.
"""
from pathlib import Path
import json
import sys

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pmsports.research.common import cluster_ci, taker_roi
from pmsports.wallets.skill import positions
from pmsports.panel import state_rows

SPLIT = pd.Timestamp("2026-07-01", tz="UTC").timestamp()


def monthly_leak():
    t = pd.DataFrame({
        "proxyWallet": pd.Categorical(["example", "example"]),
        "condition_id": pd.Categorical(["market", "market"]),
        "event_slug": pd.Categorical(["game", "game"]),
        "family": pd.Categorical(["baseball", "baseball"]),
        "timestamp": [pd.Timestamp(x).timestamp() for x in
                      ["2026-01-31T12:00Z", "2026-02-02T12:00Z"]],
        "size": [1., 1000.], "q": [.5, .9], "y": [1., 1.],
        "fee_rate": [0., 0.], "side_idx": [0, 0], "in_play": [False, True],
    })
    boundary = pd.Timestamp("2026-02-01T00:00Z").timestamp()
    all_positions = positions(t)
    selected = all_positions[all_positions.ts < boundary]
    causal = positions(t[t.timestamp < boundary])
    cols = ["cost", "pnl", "shares", "n"]
    return {"selected_by_current_walk_forward": selected[cols].to_dict("records"),
            "only_pre_boundary_fills": causal[cols].to_dict("records"),
            "note": "Even causal fill slicing must additionally require outcomes known by selection time."}


def state_and_capture_checks():
    p = pd.DataFrame({"game_pk": [1, 1, 1], "play_idx": [0, 1, 2],
        "inning": [5, 5, 5], "half": ["top", "top", "bottom"], "outs": [2, 3, 1],
        "home_score": [0, 0, 0], "away_score": [1, 1, 1],
        "on_1b": [True, False, False], "on_2b": False, "on_3b": False,
        "start_ts": [1., 10., 100.], "end_ts": [9., 20., 110.]})
    states = state_rows(p)
    capture = ROOT / "data/live/2026-09-19"
    return {"after_third_out": states.loc[states.play_idx == 1,
                ["inning", "half", "outs", "bases", "checkpoint", "state_ts"]].to_dict("records"),
            "september_19_capture_files": sorted(x.name for x in capture.glob("*.jsonl")),
            "september_19_metadata_exists": (capture / "games.jsonl").exists()}


def summarize(g, label, **extra):
    result = []
    for period, keep in [("dev", g.ts < SPLIT), ("holdout", g.ts >= SPLIT)]:
        x = g[keep]
        if len(x) < 5:
            continue
        for slip in [0., .01]:
            avg, lo, hi = cluster_ci(taker_roi(x.q, x.y, x.fee_rate, slip), x.event_slug)
            result.append(dict(rule=label, period=period, bets=len(x),
                               events=int(x.event_slug.nunique()), slip=slip,
                               roi=avg, ci_lo=lo, ci_hi=hi, **extra))
    return result


def whale_checks():
    mk = pd.read_parquet(ROOT / "data/research/markets.parquet")
    mk = mk[(mk.pre_usd >= 25000) & mk.game_start_ts.notna()].set_index("m")
    dataset = ds.dataset(ROOT / "data/research/fills.parquet")
    cols = ["m", "w", "ts", "s", "q", "y", "size", "fee_rate", "in_play"]
    parts = []
    for batch in dataset.scanner(columns=cols, batch_size=500000).to_batches():
        f = batch.to_pandas()
        near = (abs(f.q - .74) <= .005) | (abs(f.q - .75) <= .005)
        parts.append(f[(f["size"] * f.q >= 10000) & near &
                       (f.y != .5) & f.m.isin(mk.index)])
    signals = pd.concat(parts).sort_values("ts", kind="stable").reset_index(drop=True)
    signals = signals.merge(mk[["event_slug", "pre_usd"]], left_on="m", right_index=True)
    returns = []
    for price in [74, 75, "combined"]:
        base = signals if price == "combined" else signals[abs(signals.q - price / 100) <= .005]
        for rule in ["all_signals", "first_per_event", "inplay_pre50k_first_per_event"]:
            x = base
            if rule.startswith("inplay"):
                x = x[x.in_play & (x.pre_usd >= 50000)]
            if rule != "all_signals":
                x = x.drop_duplicates("event_slug")
            returns.extend(summarize(x, rule, price=price))

    # Exclude the signal wallet's own follow-on fills, unlike whale_prices.py.
    tape = dataset.to_table(columns=cols, filter=ds.field("m").isin(signals.m.unique())).to_pandas()
    tape = tape[(tape.y != .5) & tape.q.between(.02, .995)]
    tape = tape.sort_values(["m", "s", "ts"], kind="stable").reset_index(drop=True)
    ts, wallet = tape.ts.to_numpy(), tape.w.to_numpy()
    groups = {k: g.index.to_numpy() for k, g in tape.groupby(["m", "s"], sort=False)}
    followers = []
    for rule in ["all_signals", "first_per_event", "inplay_pre50k_first_per_event"]:
        selected = signals
        if rule.startswith("inplay"):
            selected = selected[selected.in_play & (selected.pre_usd >= 50000)]
        if rule != "all_signals":
            selected = selected.drop_duplicates("event_slug")
        for delay in [3, 10, 30]:
            entries = []
            for s in selected.itertuples():
                ix = groups[(s.m, s.s)]
                j = np.searchsorted(ts[ix], s.ts + delay)
                while j < len(ix) and wallet[ix[j]] == s.w:
                    j += 1
                if j < len(ix) and ts[ix[j]] - s.ts <= 600:
                    entries.append((ix[j], s.event_slug, s.ts))
            for period in ["dev", "holdout"]:
                e = [x for x in entries if (x[2] < SPLIT) == (period == "dev")]
                if len(e) < 5:
                    continue
                x = tape.loc[[v[0] for v in e]]
                avg, lo, hi = cluster_ci(taker_roi(x.q, x.y, x.fee_rate), [v[1] for v in e])
                followers.append(dict(rule=rule, delay_s=delay, period=period, bets=len(e),
                    unique_fills=len({v[0] for v in e}), events=len({v[1] for v in e}),
                    roi=avg, ci_lo=lo, ci_hi=hi,
                    fraction_prints_below_100usd=float((x["size"] * x.q < 100).mean())))
    return {"signals": len(signals), "events": int(signals.event_slug.nunique()),
            "signal_price_sensitivity": returns, "delayed_other_wallet_prints": followers,
            "limitations": ["Already-seen holdout; exploratory and not multiple-test adjusted.",
                "Print prices are proxies, not available quotes or proof of order fills.",
                "First-per-event changes exposure; it does not refute every proportional sizing rule.",
                "Full pregame volume is future information for pregame signals.",
                "Sports order delay and signal receipt time are not reconstructed.",
                "Signal bands reproduce the original +/-0.5c tests and can overlap at their boundary."]}


if __name__ == "__main__":
    print(json.dumps({"reviewed_commit": "4fe2138", "monthly_selection": monthly_leak(),
                      "state_and_capture": state_and_capture_checks(),
                      "whales": whale_checks()}, indent=2, allow_nan=False))
