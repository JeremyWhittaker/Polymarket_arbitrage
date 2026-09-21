"""Per-trade ledger exporter for hypothesis `esports_identity_pickoff` (see LEDGER_SPEC.md).

Reads the ACTUAL trades the round-2 backtest made -- `data/research/h_esports_identity_pickoff/
trades_primary_all_r2.parquet`, written by `h_esports_identity_pickoff.analyze(holdout=True)` -- and
re-applies that module's own `pnl()` to get size / cost / payoff / P&L. Nothing is re-simulated here.

One ledger row = one *attempt* (the unit the report counts as a "bet"). An attempt is a pair trade
(X -1.5 maps + notX in game 2, both taker legs) or, when only one side printed inside the 57 s entry
window, a single naked leg held alone. Attempts that never filled on either side ("kind == none",
cost 0) are not bets and are excluded, exactly as `pnl()` excludes them; their count is in `caveats`.

For a completed pair the row's "share" is one synthetic $1 unit (1 handicap share + 1 game-2 share),
so `entry_price` = pa + pb and `exit_price` = ya + yb (1.0 normally, 0.5 / 0.0 on a void or a wrong T1).

Run:  PYTHONPATH=. systemd-run --user --scope -q -p MemoryMax=5G -p MemorySwapMax=0 .venv/bin/python \
        -m pmsports.research.ledger_esports_identity_pickoff
"""
from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd

from pmsports.research import common as C
from pmsports.research import h_esports_identity_pickoff as H

SLUG = "esports_identity_pickoff"
SRC = H.OUT / f"trades_primary_all{H.RUN_TAG}.parquet"
RESULTS = H.OUT / f"results_all{H.RUN_TAG}.json"
OUT_F = C.RESEARCH / "ledgers" / f"{SLUG}.json"
COLUMNS = ["id", "period", "date", "sport", "league", "event", "market", "side", "entry_ts", "entry_price",
           "stake_usd", "fee_usd", "exit_kind", "exit_ts", "exit_price", "payout", "pnl_usd", "roi", "note"]


def _r(x, n=6):
    """JSON-safe rounding (NaN -> None)."""
    if x is None:
        return None
    x = float(x)
    return None if math.isnan(x) else round(x, n)


def build() -> tuple[pd.DataFrame, pd.DataFrame]:
    """-> (rows, priced attempts). `rows` is the ledger table; the second value is used for verification."""
    tr = pd.read_parquet(SRC)
    P = H.pnl(tr).reset_index(drop=True)          # the backtest's own sizing / fee / payoff code

    ser = pd.read_parquet(H.OUT / "series.parquet").set_index("event_slug")
    mts = pd.read_parquet(H.OUT / "markets.parquet")
    idt = pd.read_parquet(H.OUT / "identity.parquet").set_index("event_slug")
    by_cid = mts.set_index("condition_id")
    g2 = mts[mts.tl == "game2"].drop_duplicates("event_slug").set_index("event_slug")

    X = idt.X.reindex(P.event_slug).to_numpy()
    hc_cid = idt.hc_cid.reindex(P.event_slug).to_numpy()
    hc_slug = by_cid.market_slug.reindex(hc_cid).to_numpy()
    hc_closed = by_cid.closed_ts.reindex(hc_cid).to_numpy(float)
    g2_slug = g2.market_slug.reindex(P.event_slug).to_numpy()
    g2_closed = g2.closed_ts.reindex(P.event_slug).to_numpy(float)
    g2_o0 = g2.o0.reindex(P.event_slug).to_numpy()
    g2_o1 = g2.o1.reindex(P.event_slug).to_numpy()
    notX = np.where(g2_o0 == X, g2_o1, g2_o0)     # the game-2 side we buy: "X loses map 2"
    league = ser.league.reindex(P.event_slug).to_numpy()

    kind = P.kind.to_numpy()
    is_pair, is_a, is_b = kind == "pair", kind == "leg_a", kind == "leg_b"
    shares = np.where(is_pair, P.na, np.where(is_a, P.na, P.nb)).astype(float)
    stake = (P.cost - P.fees).to_numpy(float)     # shares x entry_price, fee excluded (spec)
    entry_price = stake / shares
    exit_price = (P.payoff.to_numpy(float)) / shares
    entry_ts = np.where(is_pair, np.fmin(P.ta.fillna(np.inf), P.tb.fillna(np.inf)),
                        np.where(is_a, P.ta, P.tb)).astype(float)
    exit_ts = np.where(is_pair, np.fmax(hc_closed, g2_closed), np.where(is_a, hc_closed, g2_closed))

    side = np.where(is_pair, [f"{x} -1.5 maps + {n} game 2 (taker pair)" for x, n in zip(X, notX)],
                    np.where(is_a, [f"{x} -1.5 maps (taker, unhedged leg)" for x in X],
                             [f"{n} game 2 moneyline (taker, unhedged leg)" for n in notX]))
    market = np.where(is_pair, [f"{a} + {b}" for a, b in zip(hc_slug, g2_slug)],
                      np.where(is_a, hc_slug, g2_slug))

    notes = []
    for r, k in zip(P.itertuples(), kind):
        base = (f"signal sum {r.sig:.4f} = ask_hc {r.ask_a:.3f} + ask_g2 {r.ask_b:.3f} at t={r.t} "
                f"({r.dt_T1}s after T1)")
        if k == "pair":
            base += (f"; both legs printed: hc {r.na:.1f}sh @ {r.pa:.4f} (t={int(r.ta)}), g2 {r.nb:.1f}sh @ "
                     f"{r.pb:.4f} (t={int(r.tb)}); executed sum {r.pa + r.pb:.4f}; "
                     f"payoff/share {r.payoff / r.na:.3f}")
        elif k == "leg_a":
            base += (f"; only the handicap leg printed in [t+3, t+60] ({r.na:.1f}sh @ {r.pa:.4f}, t={int(r.ta)}); "
                     f"naked X -1.5 held to resolution")
        else:
            base += (f"; the thin handicap leg never printed in [t+3, t+60]; naked game-2 {r.nb:.1f}sh @ "
                     f"{r.pb:.4f} (t={int(r.tb)}) held to resolution")
        base += f"; fee rate {r.ra if k != 'leg_b' else r.rb:.0%}"
        if r.after_T2:
            base += "; a leg filled at/after T2 (game 2 already decided)"
        notes.append(base)

    L = pd.DataFrame({
        "period": P.period, "date": pd.to_datetime(entry_ts, unit="s", utc=True).strftime("%Y-%m-%d"),
        "sport": P.sport, "league": league, "event": P.event_slug, "market": market, "side": side,
        "entry_ts": entry_ts.astype("int64"), "entry_price": entry_price, "stake_usd": stake,
        "fee_usd": P.fees, "exit_kind": "resolution", "exit_ts": exit_ts, "exit_price": exit_price,
        "payout": P.payoff, "pnl_usd": P.pnl, "roi": P.pnl / stake, "note": notes,
        # not a ledger column: the market fee_rate actually charged on this row, used to describe cost_model
        "_fee_rate": np.where(is_b, P.rb, P.ra),
    }).sort_values("entry_ts", kind="stable").reset_index(drop=True)
    L.insert(0, "id", np.arange(1, len(L) + 1))
    return L, P


def verify(P: pd.DataFrame, L: pd.DataFrame, res: dict) -> dict:
    """Ledger totals vs the report headline. The report's ROI is P&L / dollars DEPLOYED, and deployed
    dollars include the taker fee, so the ledger's denominator is stake_usd + fee_usd."""
    out = {}
    for p in ("dev", "holdout"):
        x, y, ref = P[P.period == p], L[L.period == p], res[f"primary_{p}"]
        dep = float((y.stake_usd + y.fee_usd).sum())
        out[p] = dict(
            ledger_bets=int(len(y)), report_bets=int(ref["bets"]),
            ledger_pnl=float(y.pnl_usd.sum()), report_pnl=float(ref["pnl"]),
            ledger_deployed=dep, report_deployed=float(ref["dollars"]),
            ledger_roi_on_deployed=float(y.pnl_usd.sum() / dep), report_roi=float(ref["roi"]),
            ledger_roi_on_stake_ex_fee=float(y.pnl_usd.sum() / y.stake_usd.sum()),
            ledger_series=int(y.event.nunique()), report_series=int(ref["series"]),
            ledger_pairs=int(y.side.str.contains("taker pair").sum()), report_pairs=int(ref["pairs"]),
            max_abs_pnl_diff_vs_backtest=float((y.pnl_usd.sum() - x.pnl.sum())),
        )
        out[p]["ok"] = bool(len(y) == ref["bets"]
                            and abs(out[p]["ledger_pnl"] - ref["pnl"]) < 1e-6
                            and abs(out[p]["ledger_roi_on_deployed"] - ref["roi"]) < 1e-9)
    return out


def main() -> None:
    res = json.loads(RESULTS.read_text())
    L, P = build()
    v = verify(P, L, res)
    hd = {p: dict(bets=int(res[f"primary_{p}"]["bets"]), roi=_r(res[f"primary_{p}"]["roi"]),
                  ci_lo=_r(res[f"primary_{p}"]["ci_lo"]), ci_hi=_r(res[f"primary_{p}"]["ci_hi"]),
                  pnl_usd=_r(res[f"primary_{p}"]["pnl"], 2), deployed_usd=_r(res[f"primary_{p}"]["dollars"], 2),
                  series=int(res[f"primary_{p}"]["series"]), pairs=int(res[f"primary_{p}"]["pairs"]),
                  legged=int(res[f"primary_{p}"]["legged"]), p_le0=_r(res[f"primary_{p}"].get("p_le0")),
                  roi_plus_1c=_r(res[f"primary_{p}_1c"]["roi"]))
          for p in ("dev", "holdout")}
    d_min = L.date[L.period == "dev"].min()
    h_max = L.date[L.period == "holdout"].max()
    fr = L["_fee_rate"].round(3)
    bands = "; ".join(f"{r:.0%} on {int((fr == r).sum())} rows ({L.date[fr == r].min()}..{L.date[fr == r].max()})"
                      for r in sorted(fr.unique()))
    n_below_100 = int((L.roi < -1).sum())

    rows = []
    for r in L.itertuples(index=False):
        rows.append([int(r.id), r.period, r.date, r.sport, r.league, r.event, r.market, r.side,
                     int(r.entry_ts), _r(r.entry_price), _r(r.stake_usd, 4), _r(r.fee_usd, 4), r.exit_kind,
                     None if pd.isna(r.exit_ts) else int(r.exit_ts), _r(r.exit_price, 4), _r(r.payout, 4),
                     _r(r.pnl_usd, 4), _r(r.roi), r.note])

    doc = {
        "slug": SLUG,
        "title": "Esports BO3 identity pick-off: stale 1.5-map handicap vs the liquid game-2 market",
        "group": "Thorp hypotheses",
        "sport": "esports",
        "verdict": "DEAD",
        "hypothesis": (
            "After team X wins map 1 of a best-of-3, 'X -1.5 maps' pays exactly when X wins map 2, which is the "
            "same event as X winning the game-2 child moneyline; when the thin handicap book is not re-marked "
            "while game 2 swings, buying X -1.5 plus notX in game 2 buys a guaranteed $1 for less than $1, and "
            "the counterparty losing money is whoever left the stale handicap quote up."),
        "mechanism": (
            "The 1.5-map handicap and the game-2 child moneyline are economically identical contracts that trade "
            "in two separate, unlinked order books. The handicap book is thin and slow, so when game 2 moves the "
            "handicap quote lags by seconds to minutes. The arbitrageur pays the two stale asks and collects the "
            "$1 the identity guarantees, so the loss is borne by the stale quoter rather than by anyone's view "
            "of who wins."),
        "entry_rule": (
            "Sample (pregame information only): esports series with '-game1' and '-game2' child moneylines, no "
            "'-game4' and no BO5 marker, a series moneyline with pre-game volume >= $25k, and a "
            "'-(map|game)-handicap-(home|away)-1pt5' market; seeded random sample of 350 series per period "
            "(2,041 eligible: 1,250 DEV / 791 HOLDOUT), with the 8 sampled holdout series the pre-registration "
            "feasibility probe had already seen excluded. T1 = the first game-1 fill whose implied price for "
            "either outcome is >= 0.99; that outcome is X. T2 = the first game-2 fill after T1 at >= 0.99. "
            "ask_hc(t) = price of the last fill buying 'X -1.5' in [t-20, t]; ask_g2(t) = same for game-2 fills "
            "buying notX (highest price if several share that second). SIGNAL: a fill time t in (T1+5s, T2) with "
            "ask_hc + ask_g2 <= 0.98, at most one attempt per 30 s per series. EXECUTION: both legs are bought "
            "as takers at the first second with prints on their side in [t+3, t+60], at that second's VWAP; "
            "pair size = min(the two prints, 100 shares); a second of prints consumed by one attempt cannot be "
            "reused. If only one leg prints, that leg is held naked."),
        "exit_rule": (
            "Every position is held to market resolution; there is no discretionary exit and no markout. A "
            "completed pair pays $1 per synthetic share (0.5 if a child market is voided, 0 if T1 named the "
            "wrong map-1 winner), a naked leg pays $1 or $0 on its own outcome, or $0.5 if that market itself "
            "was voided (4 game-2 legs in cs2-sparta-g2a-2026-07-28). exit_ts is the market's "
            "closed_ts (the later of the two markets for a pair). Each row is one attempt, not one fill: for a "
            "completed pair both legs are folded into a single row whose 'share' is one synthetic $1 unit "
            "(entry_price = handicap price + game-2 price, exit_price = the two payouts summed)."),
        "cost_model": (
            "Polymarket taker fee shares*rate*p*(1-p) charged on both legs at each market's own recorded "
            "fee_rate, never an assumed rate. In these rows that rate is " + bands + " (the date windows "
            "overlap because the rate is a property of the market, not of the calendar). That is why DEV pairs "
            "pay ~1.0c per share and holdout pairs ~1.9c. No maker rebates. "
            "Slippage beyond taking the next printed second at its VWAP "
            "is not charged in these rows; the report's +1c sensitivity (adding 1c to each leg) is the "
            "slippage stress test and it turns the holdout pairs negative."),
        "periods": {"dev": f"{d_min}..2026-06-30", "holdout": f"2026-07-01..{h_max}"},
        "headline": {p: {"bets": hd[p]["bets"], "roi": hd[p]["roi"], "ci_lo": hd[p]["ci_lo"],
                         "ci_hi": hd[p]["ci_hi"], "pnl_usd": hd[p]["pnl_usd"]} for p in ("dev", "holdout")},
        "headline_full": hd,
        "review": (
            "Adversarial review raised 8 issues and every one was upheld. The holdout's sign rests on 2 of 172 "
            "series (the top 2 hold 60% of P&L) and dies under a 1% trim; with one bet per game plus a 1% trim "
            "plus +1c it is -3.39%, which fails the robustness gate. 94% of the holdout P&L comes from naked "
            "game-2 underdog legs that lost 21% in DEV, and the holdout-minus-DEV difference of +34pp "
            "[-9, +75] is consistent with pure outcome variance. The riskless-looking components also fail: "
            "completed pairs -0.6% and the post-hoc sequential variant -0.7% in the holdout after the 1% trim "
            "(-2.6% / -2.7% at +1c). Reviewers also caught undisclosed pre-registration contamination (21 "
            "holdout series seen by the feasibility probe; the 8 that landed in the sample are now excluded, "
            "moving the headline from +8.90% to +8.38%) and multiple testing on variant (f), ~9 DEV "
            "configurations. Verdict was moved from the protocol's literal PROMISING to DEAD."),
        "caveats": [
            "Probe contamination (fixed): 21 holdout series were inspected with the frozen parameters before "
            "pre-registration; the 8 that fell into the seeded sample are excluded from every holdout number "
            "and from this ledger. Effect on the headline: +8.90% -> +8.38%.",
            "The positive primary holdout is not evidence for the mechanism: 94% of it ($803 of $858) is naked "
            "game-2 legs, it disappears after dropping 2-5 series, and it is negative with one bet per game "
            "after a 1% trim.",
            "Execution assumes we get the next print on our side at that second's VWAP. The signal is usually "
            "another taker lifting the stale handicap quote, so we buy the next level a median of 10-13 s "
            "later; book depth and queue position are unknown.",
            "Timestamps have 1-second on-chain resolution, so the within-second order of fills is ambiguous; it "
            "was handled conservatively (max price for the signal, VWAP for execution).",
            "Non-identity risks are in the P&L: voided child markets (cs2-sparta-g2a-2026-07-28), false T1 "
            "prints in 1.6% of series (val-mibrlo-agal-2026-07-07), and forfeits.",
            "Only 642 of the 2,041 eligible series were traded because of a 2,000-market fetch budget; the "
            "sample is random and seeded, and capacity is scaled by eligible/sampled.",
            "Fees differ by period: DEV markets charge 0% or 3%, holdout markets 5%. The completed-pair edge "
            "shrinking from +3.3% to +1.4% is consistent with the fee rise plus noise.",
            "28 signals (9 DEV, 19 HOLDOUT) never printed on either leg inside [t+3, t+60] and cost $0; they "
            "are not bets, are excluded by the backtest's own pnl() filter, and are therefore not rows here.",
            "This ledger is the pre-registered PRIMARY rule only. The report's variants are not exported: "
            "(a) thresholds 0.97/0.99, (b) latency +5s/+10s, (c) the reverse pair, (d) total-games-2.5 Under, "
            "(e) completed pairs only (conditions on the future - a diagnostic, not tradable), and (f) the "
            "post-hoc sequential 'handicap first, then hedge' variant selected from ~9 DEV configurations.",
            "Row-level convention: a completed pair is ONE row covering two taker fills, priced as one "
            "synthetic $1 unit. entry_price can therefore exceed 1.0 on a bad pair fill (the executed sum was "
            "below 1 in 85% of DEV pairs and 72% of holdout pairs).",
            "roi in each row is pnl_usd / stake_usd (fee excluded from the denominator, per the spec). The "
            "report's headline ROI is P&L per dollar DEPLOYED, whose denominator includes the fee, i.e. "
            "stake_usd + fee_usd. Stake-weighting the row roi column therefore gives -6.42% / +8.57% instead "
            f"of the report's -6.34% / +8.38%, and roi goes below -100% on {n_below_100} losing rows (the fee "
            "sits in the numerator but not the denominator), so a UI must not clamp it at -1.",
            "9 of the 902 attempts got a leg filled at or after T2, the first game-2 print implying >= 0.99, "
            "i.e. after game 2 was effectively decided; the rule allows it because the signal itself must fire "
            "before T2 and the fill window runs to t+60. Those rows say so in their note; they are 1.1% of DEV "
            "pairs and 1.3% of holdout pairs and are net negative here, so they do not carry the result.",
        ],
        "report_path": f"reports/research/{SLUG}.md",
        "code_path": f"pmsports/research/h_{SLUG}.py",
        "artifacts": [f"data/research/h_{SLUG}/trades_primary_all{H.RUN_TAG}.parquet",
                      f"data/research/h_{SLUG}/results_all{H.RUN_TAG}.json",
                      f"data/research/h_{SLUG}/analyze_holdout_r2.log"],
        "truncated": False,
        "n_total_trades": int(len(L)),
        "verification": v,
        "columns": COLUMNS,
        "rows": rows,
    }
    OUT_F.parent.mkdir(parents=True, exist_ok=True)
    OUT_F.write_text(json.dumps(doc, separators=(",", ":")))
    print(f"wrote {OUT_F}  rows {len(rows)}  bytes {OUT_F.stat().st_size:,}")
    print(json.dumps(v, indent=2))


if __name__ == "__main__":
    main()
