"""Per-trade ledger export for hypothesis `mlb_inning_break_mm` (see ../LEDGER_SPEC.md).

The rows are the ACTUAL maker fills the backtest produced. They are read straight from
`data/research/h_mlb_inning_break_mm/fills_emp_prop.parquet`, the file written by
`h_mlb_inning_break_mm.run()` for the VERDICT fill model (`go(fifo, "emp_prop", Wb, queue=Q_PROP,
seed=0, ...)`): the primary strategy variant (K = 50) simulated under the FIFO queue model with
(Q0, lambda) drawn from the recorded live books and proportional cancellation. Nothing is
re-simulated by hand; the only thing recomputed here is the break window table (b0/b1, inning,
half), which is joined back on `wid` and asserted to agree with the parquet on m/game_pk/ws.

Which model is exported, and why this one: the pre-registered primary FILL model was
front-of-queue (`fills_primary.parquet`), but the report keeps that only as the idealized case and
derives its DEAD verdict from this proportional-cancellation FIFO model, whose per-period bets /
ROI / P&L headline is the one stated for both periods in the report. The front-of-queue totals are
carried in "totals_front_of_queue_primary" and in the caveats.

A "trade" here is a single maker FILL closed by a MARKOUT, not a held or flattened position:
per-share P&L = (our ask a) - (two-sided print mid of that token at b0+105 s) + modelled rebate.
`exit_kind` is therefore "markout" on every row and no payout was ever received in cash.

Accounting per row (consistent with `run_windows()`'s `mo = a - ref + rebate` and `summarize()`'s
`capital = (1 - a) * n`): we are the maker selling token s at `a`, which is the same position as
buying the complementary token at `1 - a`. So `entry_price = 1 - a`,
`stake_usd = n * (1 - a)` (= the row's capital), `exit_price = 1 - ref`,
`payout = n * (1 - ref)`, `fee_usd = -n * rebate` (makers pay no fee; the rebate is a credit,
hence negative) and `pnl_usd = payout - stake_usd - fee_usd = n * mo`.

Run:
    PYTHONPATH=. systemd-run --user --scope -q -p MemoryMax=5G -p MemorySwapMax=0 \
        .venv/bin/python -m pmsports.research.ledger_mlb_inning_break_mm
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from pmsports.research import common as C
from pmsports.research import h_mlb_inning_break_mm as H

SLUG = "mlb_inning_break_mm"
SRC = H.OUT / "fills_emp_prop.parquet"          # the verdict model, seed 0, written by h_*.run()
RESULTS = H.OUT / "results.json"
OUT = C.RESEARCH / "ledgers" / f"{SLUG}.json"
MAX_ROWS = 20_000
SEED = 0

COLUMNS = ["id", "period", "date", "sport", "league", "event", "market", "side", "entry_ts",
           "entry_price", "stake_usd", "fee_usd", "exit_kind", "exit_ts", "exit_price", "payout",
           "pnl_usd", "roi", "note"]

# Headline of reports/research/mlb_inning_break_mm.md -> "Verdict model in detail"
# (FIFO, live queue, proportional cancel = the model the DEAD verdict is computed from).
# roi/ci are the report's "ROI on capital" row; c_per_share/c_ci are the "markout, pre-registered
# reference" row; pnl_usd is the markout half of the "P&L: markout / hold, $" row.
REPORT_HEADLINE = {
    "dev": {"bets": 24319, "games": 1728, "roi": -0.0017, "ci_lo": -0.0028, "ci_hi": -0.0007,
            "pnl_usd": -621.0, "c_per_share": -0.086, "c_ci_lo": -0.143, "c_ci_hi": -0.036},
    "holdout": {"bets": 9801, "games": 640, "roi": 0.0020, "ci_lo": 0.0011, "ci_hi": 0.0028,
                "pnl_usd": 300.0, "c_per_share": 0.099, "c_ci_lo": 0.057, "c_ci_hi": 0.137},
}
# Tolerances: the report prints P&L to the dollar, ROI to 2 decimals of a percent and the markout
# to 3 decimals of a cent, so compare at that resolution. results.json is checked exactly.
TOL = {"pnl_usd": 1.0, "roi": 5e-5, "c_per_share": 5e-4}


# ----------------------------------------------------------------------------- build

def windows() -> pd.DataFrame:
    """Rebuild the primary break-window table exactly as `h_mlb_inning_break_mm.main()` does, to get
    b0/b1/inning/half per `wid`. Deterministic: same universe, same cached plays, same T_stop."""
    res = json.loads(RESULTS.read_text())
    t_stop = float(res["t_stop"])
    assert t_stop == 100.0, t_stop
    u = H.universe()
    u_all = u[~u.dup].copy()
    u_pri = u_all[u_all.pre_usd >= H.PRE_USD_MIN].copy()
    p = H.plays(u_all.game_pk.unique())
    w = H.break_windows(u_pri, H.breaks(H.half_innings(p)), t_stop)
    w["leak"] = w.b1 + 2.6 < w.we
    return w[["wid", "m", "game_pk", "b0", "b1", "blen", "inning", "half", "ws", "we", "r", "leak"]]


def build() -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    res = json.loads(RESULTS.read_text())
    d = pd.read_parquet(SRC)

    # sanity: the same invariants simulate() enforces on these fills
    assert (d.n <= H.K_CLIP + 1e-9).all()
    assert (d.groupby(["wid", "s"]).n.sum() <= H.CAP_PER_SIDE + 1e-6).all()
    assert (d.q >= d.a - H.EPS).all() and d.a.between(0, 1).all()
    assert (d.ts >= d.ws - 1e-6).all() and (d.ts <= d.ws + 70 + 1e-6).all()

    w = windows()
    d = d.merge(w, on="wid", how="left", suffixes=("", "_w"), validate="many_to_one")
    assert (d.m == d.m_w).all() and (d.game_pk == d.game_pk_w).all()
    assert np.allclose(d.ws, d.ws_w) and np.allclose(d.r, d.ws + 75.0)

    mk = C.markets()[["m", "league", "event_slug", "market_slug", "o0", "o1"]]
    d = d.merge(mk, on="m", how="left", validate="many_to_one")
    assert d.market_slug.notna().all()

    # The pre-registered metric drops breaks with no two-sided print in [r-60, r]; those fills have
    # ref = NaN and are not in the headline. Keep them out of the ledger, count them in the caveats.
    n_all = len(d)
    dropped = d[d.ref.isna()]
    d = d[d.ref.notna()].copy()

    d["period"] = np.where(d.period == "HOLDOUT", "holdout", "dev")
    d["entry_price"] = 1.0 - d.a
    d["stake_usd"] = d.n * (1.0 - d.a)
    d["fee_usd"] = -(d.n * d.rebate)
    d["exit_price"] = 1.0 - d.ref
    d["payout"] = d.n * (1.0 - d.ref)
    d["pnl_usd"] = d.n * d.mo
    d["roi_row"] = d.mo / (1.0 - d.a)
    assert np.allclose(d.payout - d.stake_usd - d.fee_usd, d.pnl_usd, atol=1e-9)

    d = d.sort_values(["ts", "m", "s"], kind="stable").reset_index(drop=True)
    return d, dropped, res


def totals(d: pd.DataFrame, by: str = "period") -> dict:
    out = {}
    for p, g in d.groupby(by):
        out[str(p)] = dict(bets=int(len(g)), games=int(g.game_pk.nunique()),
                           windows=int(g.wid.nunique()), shares=float(g.n.sum()),
                           notional_at_ask=float((g.n * g.a).sum()),
                           stake_usd=float(g.stake_usd.sum()), pnl_usd=float(g.pnl_usd.sum()),
                           roi=float(g.pnl_usd.sum() / g.stake_usd.sum()),
                           cents_per_share=float(100 * g.pnl_usd.sum() / g.n.sum()),
                           rebate_usd=float(-g.fee_usd.sum()),
                           hold_to_resolution_pnl_usd=float((g.hold * g.n).sum()),
                           pickoff_share_of_shares=float(g.n[g.q > g.a + H.EPS].sum() / g.n.sum()))
    return out


def rnd(x, nd: int = 6) -> float:
    v = round(float(x), nd)
    return 0.0 if v == 0 else v          # avoid -0.0 in the JSON


def rows_of(d: pd.DataFrame) -> list[list]:
    ts = d.ts.to_numpy(np.int64)
    date = pd.to_datetime(ts, unit="s", utc=True).strftime("%Y-%m-%d")
    ev, ms = d.event_slug.to_numpy(), d.market_slug.to_numpy()
    o0, o1 = d.o0.to_numpy(), d.o1.to_numpy()
    out = []
    for i in range(len(d)):
        r = d.iloc[i]
        s = int(r.s)
        sold = str(o0[i] if s == 0 else o1[i])
        held = str(o1[i] if s == 0 else o0[i])
        side = f"{held} - maker fill (sold {sold} @ {r.a:.3f})"
        kind = ("picked off: taker printed through our ask (q > a), the level was gone"
                if r.q > r.a + H.EPS else "level held: taker printed at our ask (q == a)")
        note = (f"break after {r.half} {int(r.inning)} (b0+{r.ts - r.b0:.0f}s of a {r.blen:.0f}s break); "
                f"taker print {r.q:.3f} x {r['size']:.1f}sh vs our {r.a:.3f} ask - {kind}; "
                f"filled {r.n:.1f}sh; ref mid {r.ref:.4f} at b0+105s; "
                f"rebate {100 * r.rebate:.4f}c/sh @fee {r.fee_rate:.3f}; "
                f"hold-to-resolution would be {100 * r.hold:+.2f}c/sh; "
                + ("window leaked into live play; " if r.leak else "")
                + "MARKOUT, not a closed position")
        out.append([i + 1, r.period, date[i], "baseball", str(r.league), str(ev[i]), str(ms[i]),
                    side, int(ts[i]), rnd(r.entry_price), rnd(r.stake_usd), rnd(r.fee_usd),
                    "markout", int(r.r), rnd(r.exit_price), rnd(r.payout), rnd(r.pnl_usd),
                    rnd(r.roi_row), note])
    return out


# ----------------------------------------------------------------------------- main

def main() -> None:
    d, dropped, res = build()
    full = totals(d)
    by_sub = totals(d.assign(sub=np.where(d.period == "holdout", "holdout",
                                          np.where(d.ts >= H.Y2026_TS, "dev_2026H1", "dev_2025"))), "sub")
    day = pd.to_datetime(d.ts, unit="s", utc=True).dt.strftime("%Y-%m-%d")
    span = {p: (g.min(), g.max()) for p, g in day.groupby(d.period)}

    # ---- reproduction check 1: against results.json (the exact numbers the report was written from)
    print("\n=== reproduction check A: ledger vs results.json fifo.emp_prop (exact)")
    ok = True
    for p, key in (("dev", "DEV"), ("holdout", "HOLDOUT")):
        t, s = full[p], res["fifo"]["emp_prop"][key]
        e = (t["bets"] - s["fills"], t["pnl_usd"] - s["pnl_usd"], t["roi"] - s["roi_on_capital"],
             t["cents_per_share"] - s["c_per_share"])
        ok &= e[0] == 0 and abs(e[1]) < 1e-6 and abs(e[2]) < 1e-12 and abs(e[3]) < 1e-12
        print(f"  {p:8s} bets {t['bets']:,} vs {s['fills']:,} (d={e[0]}); games {t['games']} vs {s['games']}; "
              f"P&L ${t['pnl_usd']:,.4f} vs ${s['pnl_usd']:,.4f} (d={e[1]:+.2e}); "
              f"ROI {100 * t['roi']:.4f}% vs {100 * s['roi_on_capital']:.4f}% (d={e[2]:+.2e}); "
              f"c/sh {t['cents_per_share']:+.6f} vs {s['c_per_share']:+.6f} (d={e[3]:+.2e})")

    # ---- reproduction check 2: against the numbers printed in the report (rounded there)
    print("\n=== reproduction check B: ledger vs reports/research/mlb_inning_break_mm.md headline")
    for p, h in REPORT_HEADLINE.items():
        t = full[p]
        e = (t["bets"] - h["bets"], t["pnl_usd"] - h["pnl_usd"], t["roi"] - h["roi"],
             t["cents_per_share"] - h["c_per_share"])
        ok &= (e[0] == 0 and abs(e[1]) <= TOL["pnl_usd"] and abs(e[2]) <= TOL["roi"]
               and abs(e[3]) <= TOL["c_per_share"])
        print(f"  {p:8s} bets {t['bets']:,} vs {h['bets']:,} (d={e[0]}); games {t['games']} vs {h['games']}; "
              f"P&L ${t['pnl_usd']:,.2f} vs ${h['pnl_usd']:,.0f} (d=${e[1]:+.2f}, tol ${TOL['pnl_usd']}); "
              f"ROI {100 * t['roi']:.4f}% vs {100 * h['roi']:.2f}% (d={100 * e[2]:+.4f}pp); "
              f"markout {t['cents_per_share']:+.4f}c vs {h['c_per_share']:+.3f}c (d={e[3]:+.4f}c)")
    print(f"  reproduces headline: {ok}")
    assert ok

    # ---- row cap: keep every holdout fill, sample dev deterministically (LEDGER_SPEC.md)
    n_total = len(d)
    truncated = False
    rows_df = d
    shown = totals(rows_df)

    foq = res["foq"]["primary"]
    # Hold-to-resolution: the report's headline row is over ALL simulated fills; the rows in this
    # ledger are only the ones the markout metric scores, so quote both, from the saved extras.
    xh = res["extras"]["fifo_emp_prop"]["HOLDOUT"]
    h_rows, h_all = xh["hold_same_fills"], xh["hold_all"]
    h_rows_usd, h_all_usd = full["holdout"]["hold_to_resolution_pnl_usd"], xh["capacity"]["hold_usd"]
    ho = d[d.period == "holdout"]
    fee5 = float((ho.fee_rate >= 0.04).mean())     # holdout straddles the 3% -> 5% fee change
    doc = {
        "slug": SLUG,
        "title": "MLB half-inning break market making",
        "group": "Thorp hypotheses",
        "sport": "baseball",
        "verdict": "DEAD",
        "hypothesis": (
            "The MLB half-inning break is a scheduled information blackout: from the third out until the "
            "next half's first pitch (100+ s) nothing on the field can change, yet retail keeps crossing "
            "the spread. A maker quoting only inside that window should collect the half-spread plus the "
            "15% maker rebate with near-zero adverse selection, paid for by the takers who cross while no "
            "news can arrive."),
        "mechanism": (
            "In live play a maker without a fast feed is picked off: information arrives with every pitch "
            "and the book reprices in about 3.5 s. During the break there is no new information, so the "
            "flow that crosses is liquidity- or rebalancing-driven and the resting quote cannot be stale. "
            "The effect is real and large in the data (break windows mark at +0.4c/share at the front of "
            "the queue against -1.6c for the identical rule during live play), but because it is safe, "
            "everyone quotes it: the MLB touch is one tick wide 97.5% of the time with a median 20,726 "
            "shares resting, so the half-spread belongs to whoever already has time priority."),
        "entry_rule": (
            "Universe: Polymarket MLB moneylines joined to MLB game_pk (5 ambiguous game_pks / 10 markets "
            "dropped), filtered to pregame volume >= $25,000 - pregame-only information. Primary games: "
            "1,032 DEV 2025, 909 DEV 2026H1, 669 holdout. Break: b0 = the last plate-appearance end of a "
            "half-inning (the third out), b1 = the next half's first PA start. Quote window W = "
            "[b0+30 s, b0+100 s] in on-chain fill time; T_stop = 100 s is the 5th percentile of DEV break "
            "length (101.1 s over 61,119 breaks) rounded down, frozen before the holdout, and it never "
            "looks at b1, so 3.3-4.8% of windows leak into live play. Inside W we rest an ask on each "
            "token s at a_s(t) = the price of the last taker fill acquiring s with ts <= t - 3 s (no quote "
            "if that print is older than 120 s). FILL MODEL EXPORTED HERE (the verdict model): FIFO. "
            "Whenever our level a_s changes we rejoin the back of a queue of Q0 shares, with (Q0, lambda) "
            "drawn jointly (seeded per window, seed 0) from one of 484 token-windows recorded in live "
            "MLB order books on 2026-09-18/19; the queue ahead then decays as exp(-lambda*dt) from our "
            "join time (proportional cancellation). A taker order printing at q == a_s first depletes the "
            "queue ahead and we get min(what is left, 50) shares; a print at q > a_s means the level was "
            "swept, so the queue ahead is 0 and we get min(size, 50) - those are the pick-offs, and they "
            "are 45-84% of our shares. Cap 1,000 shares per token per break. Split taker rows sharing "
            "(market, wallet, ts, side, price) are merged into one taker order first. Selling token s at "
            "a is the same position as buying the complementary token at 1 - a, which is the entry_price "
            "and the capital at risk on each row."),
        "exit_rule": (
            "THERE IS NO EXIT. Each row is one maker FILL scored by a MARKOUT, not a held or flattened "
            "position: per-share P&L = (our fill price a) - ref_s + rebate, where ref_s is the two-sided "
            "print mid of token s at r = b0+105 s, built from the last taker print of each side in "
            "[r-60, r]. exit_ts is that fixed instant r and exit_price is 1 - ref_s, the mark of the side "
            "we are left holding; payout is a mark, never cash received. Breaks with no two-sided print "
            "in the reference window are dropped by the pre-registered metric (9,706 of 43,826 simulated "
            "fills, not in this ledger; the report's relaxed one-sided reference scores them and moves the "
            "holdout from +0.099c to +0.067c). One row per fill; fills are NOT aggregated per market. "
            "Turning these markouts into cash would need holding to resolution - on exactly the fills in "
            f"this ledger that is {h_rows[0]:+.3f} c/share [{h_rows[1]:+.3f}, {h_rows[2]:+.3f}] and "
            f"${h_rows_usd:+,.0f} in the holdout, which is \"hold_to_resolution_pnl_usd\" in totals_full "
            f"(the report's {h_all[0]:+.3f} c/share [{h_all[1]:+.3f}, {h_all[2]:+.3f}] / ${h_all_usd:+,.0f} "
            "is the same quantity over all 43,826 simulated fills, i.e. including the 9,706 the markout "
            "metric drops) - or flattening, which "
            "costs a 5% x p(1-p) taker fee plus half the spread, far above the edge. Only 17.5% of holdout "
            "shares even form matched pairs inside a break; the rest is directional inventory carried into "
            "the next half."),
        "cost_model": (
            "Makers pay no trading fee on Polymarket, so fee_usd is never positive. A maker rebate of 15% "
            "of the taker fee on our own fill is credited: rebate = 0.15 x fee_rate x a x (1 - a) per "
            "share, using each market's fee_rate - 0.000 c/share in 2025, 0.077 c/share in DEV 2026H1 and "
            "0.119 c/share in the holdout (the 5% fee regime) - which is why fee_usd is negative. No entry "
            "slippage is modelled because we are the passive side: the fill price is exactly our resting "
            "ask a. The rebate matters more than the edge: the holdout markout excluding it is -0.020 "
            "c/share (2025 -0.245c, 2026H1 -0.080c), i.e. the whole holdout gain is the rebate. "
            "Sensitivity: a fill 1c worse gives -0.901 c/share in the holdout (ROI -1.77%)."),
        "periods": {
            "dev": f"{span['dev'][0]}..{span['dev'][1]} (game_start_ts < 2026-07-01 UTC; the report splits "
                   f"it into DEV 2025 and DEV 2026H1)",
            "holdout": f"{span['holdout'][0]}..{span['holdout'][1]} (game_start_ts >= 2026-07-01 UTC; 77 days; "
                       f"the fee change lands inside it - {100 * fee5:.0f}% of holdout fills are in 5%-fee "
                       f"markets and the rest still at 3%, which the report calls the 5% fee regime)",
        },
        "headline": {
            "dev": {"bets": REPORT_HEADLINE["dev"]["bets"], "roi": rnd(full["dev"]["roi"]),
                    "ci_lo": res["fifo"]["emp_prop"]["DEV"]["roi_lo"],
                    "ci_hi": res["fifo"]["emp_prop"]["DEV"]["roi_hi"],
                    "pnl_usd": round(full["dev"]["pnl_usd"], 2),
                    "c_per_share": REPORT_HEADLINE["dev"]["c_per_share"],
                    "c_per_share_ci": [REPORT_HEADLINE["dev"]["c_ci_lo"], REPORT_HEADLINE["dev"]["c_ci_hi"]]},
            "holdout": {"bets": REPORT_HEADLINE["holdout"]["bets"], "roi": rnd(full["holdout"]["roi"]),
                        "ci_lo": res["fifo"]["emp_prop"]["HOLDOUT"]["roi_lo"],
                        "ci_hi": res["fifo"]["emp_prop"]["HOLDOUT"]["roi_hi"],
                        "pnl_usd": round(full["holdout"]["pnl_usd"], 2),
                        "c_per_share": REPORT_HEADLINE["holdout"]["c_per_share"],
                        "c_per_share_ci": [REPORT_HEADLINE["holdout"]["c_ci_lo"],
                                           REPORT_HEADLINE["holdout"]["c_ci_hi"]]},
        },
        "review": (
            "Two adversarial reviews (execution and statistics) killed the original PROMISING. Execution: "
            "the pre-registered primary gave us min(size, 50) of every touching taker order, i.e. front of "
            "the queue, but a newcomer joins behind a median 20,726 shares on a one-tick book; the "
            "break-even queue is only about 500-2,000 shares. Statistics: a markout is not realized P&L, "
            "and the martingale premise fails - the paired hold-minus-markout is -1.30c [-2.52, -0.05] in "
            "the holdout and only ~42% of shares form matched pairs; the markout horizon was short and "
            "overlapping; dropping windows without a two-sided print biases the result; and the verdict "
            "label did not follow the protocol. Both also flagged that the holdout was not blind (the "
            "feasibility scripts split years on fee_rate == 0, so their '2026' sample included Jul-Sep "
            "2026, and the [b0+30, b0+100] window was chosen with holdout data in view). Every point was "
            "accepted: the verdict now comes from a FIFO queue calibrated on recorded books with "
            "proportional cancellation, hold-to-resolution and fixed-horizon markouts are reported beside "
            "every markout, a relaxed one-sided reference is reported (the reviewer's own fallback had a "
            "sign error, which the author found and corrected), and the verdict is computed in code. The "
            "author's disclosure stands: the front-of-queue holdout is not a clean out-of-sample "
            "confirmation, and the execution reviewer had already run a FIFO grid on the holdout."),
        "caveats": [
            "THESE ROWS ARE THE VERDICT FILL MODEL (FIFO, live-calibrated queue, proportional cancellation, "
            "seed 0) OF THE PRE-REGISTERED PRIMARY VARIANT (K = 50), not the pre-registered front-of-queue "
            "fill model. The report keeps front-of-queue only as the idealized case - it assumes time "
            "priority a newcomer cannot have - and computes the DEAD verdict from these rows. Front of "
            f"queue on the same windows: {foq['DEV']['fills']:,} dev fills at "
            f"{foq['DEV']['c_per_share']:+.3f}c/share (ROI {100 * foq['DEV']['roi_on_capital']:+.2f}%, "
            f"${foq['DEV']['pnl_usd']:+,.0f}) and {foq['HOLDOUT']['fills']:,} holdout fills at "
            f"{foq['HOLDOUT']['c_per_share']:+.3f}c/share "
            f"(ROI {100 * foq['HOLDOUT']['roi_on_capital']:+.2f}%, ${foq['HOLDOUT']['pnl_usd']:+,.0f}); "
            "re-run this exporter against fills_primary.parquet to export those instead. Full totals are "
            "in \"totals_front_of_queue_primary\".",
            "exit_kind is \"markout\" on every row: these are maker fills marked to the b0+105 s two-sided "
            "print mid, not closed positions, and no payout was ever received in cash. Held to resolution, "
            f"the same fills lose money in the holdout: {h_rows[0]:+.3f} c/share "
            f"[{h_rows[1]:+.3f}, {h_rows[2]:+.3f}], ${h_rows_usd:+,.0f} (per-row hold-to-resolution P&L is "
            f"in each note). The report's headline hold row, {h_all[0]:+.3f} c/share "
            f"[{h_all[1]:+.3f}, {h_all[2]:+.3f}] / ${h_all_usd:+,.0f}, is over all 43,826 simulated fills, "
            "including the 9,706 that have no two-sided reference and are not in this ledger.",
            "The verdict depends on the cancellation assumption, which L2 data cannot settle. Pessimistic "
            "(no cancellation ahead of us): DEV -0.144c, holdout +0.045c, +$121. Proportional (exported "
            "here, the neutral standard): DEV -0.086c, holdout +0.099c, +$300. Optimistic (every "
            "cancellation at the level counted as ahead of us, applied at join): DEV +0.124c, holdout "
            "+0.257c, +$1,145 - that would be PROMISING, capped by gate (b), at about $15/day. Three "
            "seeds of the exported model give DEV -0.086/-0.099/-0.087 and holdout +0.099/+0.104/+0.112.",
            "The entire holdout edge is the modelled rebate (+0.119 c/share against a +0.099 c/share "
            "result); excluding it the spread captured is negative in every period (-0.245c, -0.080c, "
            "-0.020c). The rebate is a per-fill approximation of a pool Polymarket distributes by formula.",
            "The queue and cancellation calibration comes from one evening of recorded books (15 games, "
            "484 token-windows, 2026-09-18/19 - dates inside the holdout calendar, though no holdout "
            "fills or outcomes are used) and is applied to all periods. 2025 spreads were wider and books "
            "probably thinner, but DEV 2025 is negative even at Q0 = 500 (-0.119c).",
            "The holdout was not blind for the front-of-queue model: the feasibility scripts pooled all of "
            "2026 with no cutoff and the [b0+30, b0+100] window was chosen with holdout data visible. "
            "Recomputed on DEV only, the motivating number is still +0.373c [+0.317, +0.438] for 2026H1, "
            "so the hypothesis would have been proposed anyway. The FIFO models were fixed on DEV before "
            "the single holdout re-run, but the execution reviewer had already run a FIFO grid on it.",
            f"9,706 of the 43,826 simulated fills are not in this ledger: their break had no two-sided "
            f"print in [b0+45, b0+105], so the pre-registered reference is undefined and they are dropped "
            f"from the headline. Scored with the report's relaxed one-sided reference they mark at "
            f"-0.067 c/share in the holdout, moving it from +0.099c to +0.067c [+0.028, +0.104].",
            "Feed timing: the rule needs the third out known by b0+30 s in fill time. Public MLB feeds lag "
            "about 27 s, leaving roughly 0.4 s of margin, so a live implementation is tighter than this "
            "backtest assumes.",
            "Other variants and controls, not exported (all in the report, both fill models): (a) K = 200, "
            "(b) the pre-registered back-of-queue stress gate (fails: holdout -0.444c, -$435; -0.485c on "
            "top of FIFO), (c) 25% of what reaches us, (d) all games, (e) hold to resolution, (f) negative "
            "control in live play (-2.859c in the holdout under FIFO, which is the adverse selection the "
            "break avoids), (g) mid-inning pauses, the fixed queue grid Q0 in {0, 500, 2k, 5k, 20,726}, "
            "seeds 1 and 2, and the relaxed-reference and t+60/300/600 s markouts.",
        ],
        "report_path": f"reports/research/{SLUG}.md",
        "code_path": f"pmsports/research/h_{SLUG}.py",
        "source_artifact": str(SRC.relative_to(C.ROOT)) if hasattr(C, "ROOT") else str(SRC),
        "truncated": bool(truncated),
        "n_total_trades": int(n_total),
        "totals_full": full,
        "totals_full_by_subperiod": by_sub,
        "totals_rows": shown,
        "totals_front_of_queue_primary": {
            p: {k: foq[q][k] for k in ("fills", "games", "shares", "notional_a", "capital",
                                       "c_per_share", "ci_lo", "ci_hi", "roi_on_capital", "pnl_usd")}
            for p, q in (("dev", "DEV"), ("holdout", "HOLDOUT"))},
        "columns": COLUMNS,
        "rows": rows_of(rows_df),
    }
    if truncated:
        doc["caveats"].insert(1, (
            f"\"totals_full\" are the unsampled per-period totals and they "
            f"reproduce the report; \"totals_rows\" are the totals of the rows actually in this file."))

    # rounding disclosure: row values are stored to 6 dp, so on small rows the stored roi does not
    # divide out of the stored pnl_usd / stake_usd. Measure it on the rows actually written.
    ci_ = {c: i for i, c in enumerate(COLUMNS)}
    arr = np.array([[r[ci_["pnl_usd"]], r[ci_["stake_usd"]], r[ci_["roi"]]] for r in doc["rows"]], float)
    err = np.abs(np.divide(arr[:, 0], arr[:, 1], out=np.zeros(len(arr)), where=arr[:, 1] != 0) - arr[:, 2])
    doc["caveats"].append(
        f"Row values are rounded to 6 decimals in this file, so pnl_usd / stake_usd does not divide out "
        f"exactly to the stored roi on small rows: {int((err > 1e-6).sum()):,} of {len(arr):,} rows differ "
        f"by more than 1e-6, {int((err > 1e-4).sum())} by more than 1e-4, worst {err.max():.1e} (on a row "
        f"with a ${arr[int(err.argmax()), 1]:.4f} stake). Rounding only: \"totals_full\", "
        f"\"totals_full_by_subperiod\" and \"totals_rows\" are computed in full precision, and the "
        f"per-row identity payout - stake_usd - fee_usd == pnl_usd is asserted to 1e-9 before rounding.")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, separators=(",", ":")))
    print(f"\nwrote {OUT}  ({OUT.stat().st_size / 1e6:.1f} MB, {len(doc['rows']):,} rows of {n_total:,})")
    print(f"  rows in file: dev {shown['dev']['bets']:,} (P&L ${shown['dev']['pnl_usd']:,.2f}), "
          f"holdout {shown['holdout']['bets']:,} (P&L ${shown['holdout']['pnl_usd']:,.2f}, "
          f"ROI {100 * shown['holdout']['roi']:.4f}%)")


if __name__ == "__main__":
    main()
