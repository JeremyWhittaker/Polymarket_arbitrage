"""Per-trade ledger export for hypothesis `halftime_intermission_mm` (see ../LEDGER_SPEC.md).

The rows are the ACTUAL maker fills produced by the hypothesis script: this module calls
`h_halftime_intermission_mm.simulate()` with the frozen PRIMARY configuration and default
(pre-registered, front-of-queue) fill model, i.e. exactly the `run_var("PRIMARY")` call inside
`h_halftime_intermission_mm.run()`. Nothing is re-simulated by hand.

A "trade" here is a single maker fill, and it is closed by a MARKOUT, not by a realised exit:
the P&L is (our fill price - the leg's two-sided print mid at KO+61 min) + the modelled maker
rebate. `exit_kind` is therefore "markout" on every row.

Accounting per row (consistent with `simulate()`'s `capital = 1 - a` and `mo = a - ref_s + reb`):
we are the maker selling token s at `a`, which is the same position as buying the complementary
token at `1 - a`. So `entry_price = 1 - a`, `stake_usd = shares * (1 - a)` (= the row's capital),
`exit_price = 1 - ref_s`, `payout = shares * (1 - ref_s)`, `fee_usd = -shares * rebate` (makers
pay no fee; the rebate is a credit, hence negative), and
`pnl_usd = payout - stake_usd - fee_usd = shares * mo`.

Run:
    PYTHONPATH=. systemd-run --user --scope -q -p MemoryMax=5G -p MemorySwapMax=0 \
        .venv/bin/python -m pmsports.research.ledger_halftime_intermission_mm
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from pmsports.research import common as C
from pmsports.research import h_halftime_intermission_mm as H

SLUG = "halftime_intermission_mm"
OUT = C.RESEARCH / "ledgers" / f"{SLUG}.json"
MAX_ROWS = 20_000
SEED = 0

COLUMNS = ["id", "period", "date", "sport", "league", "event", "market", "side", "entry_ts",
           "entry_price", "stake_usd", "fee_usd", "exit_kind", "exit_ts", "exit_price", "payout",
           "pnl_usd", "roi", "note"]

# Headline of the PRIMARY (pre-registered front-of-queue) row of reports/research/halftime_intermission_mm.md,
# "Main results" -> "Front of queue (pre-registered fill; float fix)", cross-checked against
# data/research/h_halftime_intermission_mm/results_full.json -> results.PRIMARY.table.
REPORT_HEADLINE = {
    "dev": {"bets": 54224, "roi": 0.0063547, "ci_lo": 0.0055013, "ci_hi": 0.0070766,
            "pnl_usd": 4168.30, "events": 1126, "mo_c_per_share": 0.3340},
    "holdout": {"bets": 10816, "roi": 0.0017347, "ci_lo": -0.0026492, "ci_hi": 0.0049083,
                "pnl_usd": 262.95, "events": 288, "mo_c_per_share": 0.0895},
}


# ----------------------------------------------------------------------------- build

def ref_timestamps(f: pd.DataFrame, ref: tuple[int, int]) -> pd.Series:
    """Per leg: the unix second at which the KO+61 markout reference is complete, i.e. the later of the
    two last prints (one per side) inside the ref window that `H.leg_ref()` uses to build `mid0`."""
    r = f[(f.rel >= ref[0] * H.MIN) & (f.rel <= ref[1] * H.MIN)]
    last = r.groupby(["m", "s"]).ts.max().unstack().reindex(columns=[0, 1])
    return last.max(axis=1).rename("ref_ts")


def leg_label(market_slug: str, event_slug: str) -> str:
    return market_slug[len(event_slug) + 1:] if market_slug.startswith(event_slug + "-") else market_slug


def build() -> tuple[pd.DataFrame, dict]:
    legs, f = H.cache_soccer(holdout=True)
    cfg = H.PRIMARY
    d, info = H.simulate(f, legs, cfg["win"], cfg["skip"], cfg["ref"])   # PRIMARY, exactly as run()

    # sanity: the same assertions run() makes on the primary fills
    assert (d.sh <= H.K_DEFAULT + 1e-9).all() and (d.groupby("g").sh.sum() <= H.CAP + 1e-6).all()
    assert (d.rel >= cfg["win"][0] * H.MIN).all() and (d.rel <= cfg["win"][1] * H.MIN).all()
    assert (d.q >= d.a - H.PX_TOL).all() and d.a.between(0, 1).all()

    meta = legs.set_index("m")[["market_slug", "game_start_ts"]]
    d = d.join(meta, on="m")
    d = d.join(ref_timestamps(f, cfg["ref"]), on="m")
    assert d.ref_ts.notna().all()

    d["period"] = np.where(d.per == "hold", "holdout", "dev")
    d["entry_price"] = 1.0 - d.ap
    d["stake_usd"] = d.sh * d.capital
    d["fee_usd"] = -(d.sh * d.reb)
    d["exit_price"] = 1.0 - d.ref_s
    d["payout"] = d.sh * (1.0 - d.ref_s)
    d["pnl_usd"] = d.sh * d.mo
    d["roi_row"] = d.mo / d.capital
    # identity check: payout - stake - fee == pnl, on every row
    assert np.allclose(d.payout - d.stake_usd - d.fee_usd, d.pnl_usd, atol=1e-9)

    d = d.sort_values(["ts", "m", "s"], kind="stable").reset_index(drop=True)
    return d, info


def totals(d: pd.DataFrame) -> dict:
    out = {}
    for p, g in d.groupby("period"):
        out[p] = dict(bets=int(len(g)), events=int(g.event_slug.nunique()), shares=float(g.sh.sum()),
                      stake_usd=float(g.stake_usd.sum()), pnl_usd=float(g.pnl_usd.sum()),
                      roi=float(g.pnl_usd.sum() / g.stake_usd.sum()),
                      cents_per_share=float(100 * g.pnl_usd.sum() / g.sh.sum()))
    return out


def rnd(x, nd: int = 6) -> float:
    v = round(float(x), nd)
    return 0.0 if v == 0 else v          # avoid -0.0 in the JSON


def rows_of(d: pd.DataFrame) -> list[list]:
    ev = d.event_slug.to_numpy()
    ms = d.market_slug.to_numpy()
    legn = [leg_label(a, b) for a, b in zip(ms, ev)]
    ts = d.ts.to_numpy(np.int64)
    date = pd.to_datetime(ts, unit="s", utc=True).strftime("%Y-%m-%d")
    out = []
    for i in range(len(d)):
        r = d.iloc[i]
        s = int(r.s)
        held, sold = ("No", "Yes") if s == 0 else ("Yes", "No")
        side = f"{held} on {legn[i]} - maker fill (sold {sold} @ {r.ap:.3f})"
        note = (f"print {r.q:.3f}x{r['size']:.1f}sh @KO+{r.rel / H.MIN:.1f}m "
                f"{'(through our quote)' if r.through else '(at our quote)'}; filled {r.sh:.1f}sh; "
                f"ref {r.ref_s:.4f}; rebate {100 * r.reb:.4f}c/sh @fee {r.fee_rate:.3f}; markout, not closed")
        out.append([i + 1, r.period, date[i], "soccer", str(r.league), str(ev[i]), str(ms[i]), side,
                    int(ts[i]), rnd(r.entry_price), rnd(r.stake_usd), rnd(r.fee_usd), "markout",
                    int(r.ref_ts), rnd(r.exit_price), rnd(r.payout), rnd(r.pnl_usd), rnd(r.roi_row), note])
    return out


def main() -> None:
    d, info = build()
    full = totals(d)
    day = pd.to_datetime(d.ts, unit="s", utc=True).dt.strftime("%Y-%m-%d")
    span = {p: (g.min(), g.max()) for p, g in day.groupby(d.period)}

    # ---- reproduction check against the report headline (full, unsampled fill set)
    print("\n=== reproduction check (all fills, before any sampling)")
    ok = True
    for p, h in REPORT_HEADLINE.items():
        t = full[p]
        d_bets = t["bets"] - h["bets"]
        d_pnl = t["pnl_usd"] - h["pnl_usd"]
        d_roi = t["roi"] - h["roi"]
        ok &= (d_bets == 0) and abs(d_pnl) < 0.01 and abs(d_roi) < 5e-7
        print(f"  {p:8s} bets {t['bets']:,} vs {h['bets']:,} (d={d_bets}); "
              f"events {t['events']} vs {h['events']}; "
              f"P&L ${t['pnl_usd']:,.2f} vs ${h['pnl_usd']:,.2f} (d=${d_pnl:+.4f}); "
              f"ROI {100 * t['roi']:.4f}% vs {100 * h['roi']:.4f}% (d={100 * d_roi:+.6f}pp); "
              f"c/sh {t['cents_per_share']:+.4f} vs {h['mo_c_per_share']:+.4f}")
    print(f"  reproduces headline: {ok}")

    # ---- row cap: keep every holdout fill, sample dev deterministically (LEDGER_SPEC.md)
    n_total = len(d)
    truncated = n_total > MAX_ROWS
    if truncated:
        hold = d.index[d.period == "holdout"].to_numpy()
        dev = d.index[d.period == "dev"].to_numpy()
        k = MAX_ROWS - len(hold)
        keep = np.random.default_rng(SEED).choice(dev, size=k, replace=False)
        sel = np.sort(np.concatenate([hold, keep]))
        rows_df = d.loc[sel].reset_index(drop=True)
    else:
        rows_df = d
    shown = totals(rows_df)

    doc = {
        "slug": SLUG,
        "title": "Soccer halftime market making",
        "group": "Thorp hypotheses",
        "sport": "soccer",
        "verdict": "DEAD",
        "hypothesis": (
            "Soccer halftime is a scheduled information blackout: for ~15 minutes no play can change the "
            "score, so quotes cannot be picked off by news, yet takers keep hitting the book to reposition. "
            "A maker quoting through halftime should therefore earn the spread with far less adverse "
            "selection than in live play, and the takers paying it are the ones losing money to us."),
        "mechanism": (
            "During live play a maker's resting quote is stale the instant something happens on the pitch, so "
            "the spread it earns is eaten by informed takers. At halftime nothing can happen, so flow is "
            "liquidity- and rebalancing-driven rather than informed, and the maker keeps the spread plus the "
            "15% maker rebate. The effect is real and measurable: halftime beats the matched in-play control "
            "windows (KO+[20,30] and KO+[70,80]) by 0.7-1.9 c/share under every fill model. The catch is that "
            "the same safety makes every other maker pile in, so the spread goes to whoever is already at the "
            "front of the queue."),
        "entry_rule": (
            "Universe: Polymarket soccer Yes/No legs with >= $25,000 volume at kickoff (4,617 legs in 3,088 "
            "events). KO = the scheduled game_start_ts. Skip the whole event if any of its universe legs shows "
            "a Yes-converted fill-price span > 0.03 in [KO+49, KO+52) min (366 of 3,088 events skipped). "
            "In the window [KO+52, KO+60] min we rest a maker quote on each token s at price a = the price of "
            "the last taker print acquiring s at ts <= t-3s (highest print if several share that second; no "
            "quote if that print is older than 120 s). PRE-REGISTERED (PRIMARY) FILL MODEL, exported here: a "
            "taker print acquiring s at q >= a - 1e-6 fills min(print size, 50) shares at a, capped "
            "cumulatively at 1,000 shares per token per leg. Selling token s at a is the same position as "
            "buying the complementary token at 1 - a, which is the entry_price and the capital at risk on "
            "each row. This front-of-queue fill is the pre-registered rule but is NOT realistic: it assumes "
            "queue priority at the level that just traded."),
        "exit_rule": (
            "There is no exit. Each row is one maker FILL closed by a MARKOUT, not a held or flattened "
            "position: P&L per share = (our fill price a) - (ref_s) + rebate, where ref_s is the leg's "
            "two-sided print mid at KO+61 min (the last print of each side in [KO+59, KO+61], converted to "
            "token s). exit_ts is the second at which that reference is complete and exit_price is 1 - ref_s, "
            "the mark of the side we hold. Legs with no two-sided print in the ref window are dropped "
            "(2,139 of 4,617; the as-of-ref variant that keeps them is a reported robustness row). Turning "
            "these markouts into cash would need holding to resolution (reported as variant (d): DEV +1.87%, "
            "holdout +2.77%, both CIs crossing 0) or flattening, which costs a 5% x p(1-p) taker fee "
            "(~1.2c at p=0.5) plus half the spread - far above the edge. One row per fill; fills are NOT "
            "aggregated per market."),
        "cost_model": (
            "Makers pay no trading fee on Polymarket, so fee_usd is never positive. A maker rebate of 15% of "
            "the taker fee on our own fill is credited: rebate = 0.15 x fee_rate x a x (1 - a) per share, "
            "using each print's own fee_rate (0.00/0.018/0.03 in DEV, 0.03/0.05 in the holdout), which is why "
            "fee_usd is negative. No slippage is modelled on entry because we are the passive side: the fill "
            "price is exactly our resting quote a. The rebate is an approximation of Polymarket's pool "
            "formula and it matters: at rebate 0 the same primary rule gives DEV +0.56% and holdout -0.04%."),
        "periods": {
            "dev": f"{span['dev'][0]}..{span['dev'][1]} (scheduled KO < 2026-07-01; the report splits it into "
                   f"DEV 2025 and DEV 2026H1)",
            "holdout": f"{span['holdout'][0]}..{span['holdout'][1]} (scheduled KO >= 2026-07-01)",
        },
        "headline": {
            "dev": {"bets": REPORT_HEADLINE["dev"]["bets"], "roi": round(REPORT_HEADLINE["dev"]["roi"], 6),
                    "ci_lo": round(REPORT_HEADLINE["dev"]["ci_lo"], 6),
                    "ci_hi": round(REPORT_HEADLINE["dev"]["ci_hi"], 6),
                    "pnl_usd": REPORT_HEADLINE["dev"]["pnl_usd"]},
            "holdout": {"bets": REPORT_HEADLINE["holdout"]["bets"],
                        "roi": round(REPORT_HEADLINE["holdout"]["roi"], 6),
                        "ci_lo": round(REPORT_HEADLINE["holdout"]["ci_lo"], 6),
                        "ci_hi": round(REPORT_HEADLINE["holdout"]["ci_hi"], 6),
                        "pnl_usd": REPORT_HEADLINE["holdout"]["pnl_usd"]},
        },
        "review": (
            "Two adversarial reviews (execution and stats) rejected the original PROMISING label and the "
            "verdict is now DEAD. Execution: 87-89% of filled shares are prints exactly at our quote, which "
            "needs queue priority a newcomer does not have, and fills where the print trades through our "
            "price lose 1.204 c/share in the holdout; real queue depth is far above the break-even. Stats: "
            "the holdout was not virgin (the feasibility check pooled all of 2026 with no cutoff), the "
            "positive sign depends on the assumed rebate (it flips below ~5%) and on the World Cup, the top "
            "3 events carry 41% of holdout P&L and removing 9 events flips the sign, and all the edge sits "
            "in the at-our-quote fills. Every point was accepted and re-run; the author also found a further "
            "bug (N1, a float32 price-equality tolerance) that had made the pre-registered back-of-queue gate "
            "far too lenient. With realistic execution the pre-registered gate loses -1.95% of capital in the "
            "holdout and the measured-depth queue model loses -0.23% (-0.41% with no rebate), so the strategy "
            "is DEAD: the blackout effect is real, but it is an incumbent's edge, not a newcomer's."),
        "caveats": [
            "THE EXPORTED ROWS ARE THE PRE-REGISTERED PRIMARY (FRONT-OF-QUEUE) FILL MODEL, which the reviews "
            "showed is not achievable. It is exported because LEDGER_SPEC.md asks for the pre-registered "
            "primary variant. Its headline is DEV +0.64% / holdout +0.17% [-0.26, +0.49]. The realistic "
            "variants, which decide the verdict, are: pre-registered back-of-queue gate DEV -1.01% / holdout "
            "-1.95% (1,945 holdout fills, -$643); queue model with Q drawn from measured halftime depth DEV "
            "+0.18% / holdout -0.23% (2,555 holdout fills, -$108); both DEAD, and both still DEAD at rebate 0 "
            "and under the as-of ref.",
            "exit_kind is \"markout\" on every row: these are maker fills marked to the KO+61 two-sided print "
            "mid, not closed positions. No row's payout was ever received in cash. Realising it needs holding "
            "to resolution (noisy: holdout +2.77% with a CI crossing 0) or flattening at a cost above the edge.",
            f"TRUNCATED: {n_total:,} fills in total, capped at {MAX_ROWS:,} rows. Every holdout fill "
            f"({full['holdout']['bets']:,}) is kept; the dev rows are a seeded (numpy default_rng({SEED})) "
            f"sample of {shown['dev']['bets']:,} of {full['dev']['bets']:,}, so the dev rows in this file sum "
            f"to about {100 * shown['dev']['bets'] / full['dev']['bets']:.0f}% of the dev headline. "
            "The unsampled per-period totals are in \"totals_full\" and they reproduce the report exactly; "
            "\"totals_rows\" are the totals of the rows actually in this file.",
            "The holdout was not virgin: the feasibility script behind the pre-registration pooled all of 2026 "
            "with no 2026-07-01 cutoff, so 34% of its window shares were holdout. That biases the holdout "
            "toward a positive result, and the rule failed anyway.",
            "The rebate is modelled as 15% of the taker fee on our own fill; the real rebate is a pool paid by "
            "Polymarket's formula. Every realistic variant is <= 0 without it.",
            "Scheduled kickoff: for US/Mexico competitions (MLS, Liga MX, Leagues Cup, CONCACAF) game_start_ts "
            "runs ~10 min before the real kickoff, so the fixed [52, 60] window trades live first-half play "
            "there. MLS is the worst league in both periods (-1.65c DEV, -2.82c holdout). Excluding these "
            "leagues after the fact is a post-hoc diagnostic only, not a verdict.",
            "Other variants not exported: (a) K=200, (c) 25% of each fill, (d/d2) hold to resolution, "
            "no-skip-rule diagnostic, quote-delay 5/10/20 s, tick-grid quotes, same-second aggregation, "
            "the Q grid {0, 100, 250, 500, 1k, 2k, 5k, 20k, inf}, 5 random seeds of the realistic queue, "
            "the in-play control windows, and the basketball/hockey intermission version - all in the report.",
        ],
        "report_path": f"reports/research/{SLUG}.md",
        "code_path": f"pmsports/research/h_{SLUG}.py",
        "truncated": bool(truncated),
        "n_total_trades": int(n_total),
        "totals_full": full,
        "totals_rows": shown,
        "simulate_info": {k: v for k, v in info.items() if not isinstance(v, dict)},
        "columns": COLUMNS,
        "rows": rows_of(rows_df),
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, separators=(",", ":")))
    print(f"\nwrote {OUT}  ({OUT.stat().st_size / 1e6:.1f} MB, {len(doc['rows']):,} rows of {n_total:,})")
    print(f"  rows in file: dev {shown['dev']['bets']:,} (P&L ${shown['dev']['pnl_usd']:,.2f}), "
          f"holdout {shown['holdout']['bets']:,} (P&L ${shown['holdout']['pnl_usd']:,.2f}, "
          f"ROI {100 * shown['holdout']['roi']:.4f}%)")


if __name__ == "__main__":
    main()
