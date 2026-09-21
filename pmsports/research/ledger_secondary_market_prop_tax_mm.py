"""Per-trade ledger export for hypothesis `secondary_market_prop_tax_mm` (see ../LEDGER_SPEC.md).

The rows are the ACTUAL maker fills the backtest made. They are read straight out of the
hypothesis run's saved artifacts,
`data/research/h_secondary_market_prop_tax_mm/fills_{dev,holdout}.parquet`, which are written by
`h_secondary_market_prop_tax_mm.run(split)` from `simulate(ev, s, trig)` with the frozen PRIMARY
parameters (K=100, inv_cap=500, lag=3 s, stale=6 h, quote window [T-12h, T-15m), pull rule on).
Nothing here is re-simulated or hand-simulated; this module only re-shapes those fills into the
ledger columns and re-derives the same per-share accounting the hypothesis script uses.

Accounting per row. We are the MAKER, resting an ask on token `s` at price `a`; a taker lifts it,
so we SELL token s at `a`, which is exactly the same position as BUYING the complementary token at
`1 - a` and holding it to resolution. The hypothesis script scores each share as
`pnl = a - y_s + 0.15 * fee_rate * a * (1 - a)` with `notional = 1 - a`. In ledger columns:

    entry_price = 1 - a                      (our cost per share of the side we end up holding)
    stake_usd   = sh * (1 - a)               (= the script's notional, the capital at risk)
    fee_usd     = -sh * 0.15*fee_rate*a*(1-a)  (makers pay no fee; the rebate is a credit -> negative)
    exit_price  = 1 - y_s                    (payout of the side we hold: 1, 0 or 0.5)
    payout      = sh * (1 - y_s)
    pnl_usd     = payout - stake_usd - fee_usd = sh * pnl        (identity asserted below)
    roi         = pnl_usd / stake_usd

`exit_kind` is "resolution": unlike the markout studies, these positions really are held to the
market's resolution, so `exit_ts` is the side market's `closed_ts` from the universe.

Run:
    PYTHONPATH=. systemd-run --user --scope -q -p MemoryMax=5G -p MemorySwapMax=0 \
        .venv/bin/python -m pmsports.research.ledger_secondary_market_prop_tax_mm
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from pmsports.research import common as C
from pmsports.research import h_secondary_market_prop_tax_mm as H

SLUG = "secondary_market_prop_tax_mm"
OUT = C.RESEARCH / "ledgers" / f"{SLUG}.json"
MAX_ROWS = 20_000
SEED = 0

COLUMNS = ["id", "period", "date", "sport", "league", "event", "market", "side", "entry_ts",
           "entry_price", "stake_usd", "fee_usd", "exit_kind", "exit_ts", "exit_price", "payout",
           "pnl_usd", "roi", "note"]

# reports/research/secondary_market_prop_tax_mm.md -> "Main result", the PRIMARY rows.
# c/share and the CI are the report's headline statistic; roi/ci are per $ of notional.
# Cross-checked against data/research/h_secondary_market_prop_tax_mm/results_{split}.json["primary"].
REPORT_HEADLINE = {
    "dev": {"bets": 25252, "roi": 0.008360, "ci_lo": -0.012496, "ci_hi": 0.030997,
            "pnl_usd": 2934.69, "shares": 714549.0, "notional_usd": 351051.0,
            "markets": 814, "games": 673, "c_per_share": 0.4107,
            "c_ci_lo": -0.6153, "c_ci_hi": 1.5200},
    "holdout": {"bets": 19043, "roi": -0.021242, "ci_lo": -0.047243, "ci_hi": 0.003560,
                "pnl_usd": -7407.37, "shares": 709036.0, "notional_usd": 348713.0,
                "markets": 844, "games": 586, "c_per_share": -1.0447,
                "c_ci_lo": -2.3239, "c_ci_hi": 0.1748},
}


# ----------------------------------------------------------------------------- build

def build() -> tuple[pd.DataFrame, dict]:
    """The saved PRIMARY fills of both splits, joined to market metadata and scored."""
    parts, stats = [], {}
    for split in ("dev", "holdout"):
        d = pd.read_parquet(H.OUT / f"fills_{split}.parquet")
        res = json.loads((H.OUT / f"results_{split}.json").read_text())
        stats[split] = res["stats"]
        # the saved parquet must still reproduce results_<split>.json["primary"] exactly
        p = res["primary"]
        assert len(d) == p["fills"], (len(d), p["fills"])
        assert abs(float((d.sh * d.pnl).sum()) - p["pnl"]) < 1e-6
        assert abs(float((d.sh * d.notional).sum()) - p["notional"]) < 1e-6
        assert (d.split == split).all()
        parts.append(d)
    d = pd.concat(parts, ignore_index=True)

    # Was this fill cut short by the 500-share net inventory cap rather than by K or the taker's
    # size? simulate() appends rows in (market, ts) order and truncates the fill at the cap, so a
    # fill that lands exactly on +-inv_cap is the one that was truncated. Computed in the saved
    # append order, before the ledger re-sorts.
    step = np.where(d.s == 0, d.sh, -d.sh)
    net = pd.Series(step, index=d.index).groupby(d.condition_id).cumsum()
    d["at_cap"] = net.abs() > H.PARAMS["inv_cap"] - 1e-6

    # market metadata: slug + outcome names from the frozen sample, resolution time from the universe
    s = H.sample().drop_duplicates("condition_id").set_index("condition_id")
    u = C.universe(columns=["condition_id", "closed_ts"])
    u = u[u.condition_id.isin(set(s.index))].drop_duplicates("condition_id").set_index("condition_id")
    for col in ("market_slug", "game_start_ts", "o0", "o1", "y0", "y1"):
        d[col] = d.condition_id.map(s[col])
    d["closed_ts"] = d.condition_id.map(u.closed_ts)
    assert d[["market_slug", "o0", "o1", "game_start_ts", "closed_ts"]].notna().all().all()
    # the payout carried in the fills must agree with the sample's payout for the token we sold
    assert (d.y == np.where(d.s == 0, d.y0, d.y1)).all()

    # rebate per share, exactly the formula in H.simulate()
    d["reb"] = H.PARAMS["rebate"] * d.fee_rate.fillna(0) * d.a * (1 - d.a)
    assert np.allclose(d.pnl, d.a - d.y + d.reb, atol=1e-12)

    d["period"] = d.split
    d["entry_price"] = 1.0 - d.a
    d["stake_usd"] = d.sh * (1.0 - d.a)
    d["fee_usd"] = -(d.sh * d.reb)
    d["exit_price"] = 1.0 - d.y
    d["payout"] = d.sh * (1.0 - d.y)
    d["pnl_usd"] = d.sh * d.pnl
    d["roi_row"] = d.pnl / (1.0 - d.a)
    # spec identity, on every single row
    assert np.allclose(d.payout - d.stake_usd - d.fee_usd, d.pnl_usd, atol=1e-9)
    assert np.allclose(d.stake_usd, d.sh * d.notional, atol=1e-9)

    d["hrs"] = (d.game_start_ts - d.ts) / 3600.0
    d["through"] = d.q_taker > d.a + H.EPS
    d["spread_c"] = 100.0 * (d.a + d.a_oth - 1.0)

    d = d.sort_values(["ts", "condition_id", "s"], kind="stable").reset_index(drop=True)
    return d, stats


def totals(d: pd.DataFrame) -> dict:
    out = {}
    for p, g in d.groupby("period"):
        pnl, stake = float(g.pnl_usd.sum()), float(g.stake_usd.sum())
        out[p] = dict(bets=int(len(g)), markets=int(g.condition_id.nunique()),
                      games=int(g.event_slug.nunique()), shares=float(g.sh.sum()),
                      notional_usd=stake, pnl_usd=pnl, roi=pnl / stake,
                      c_per_share=100.0 * pnl / float(g.sh.sum()))
    return out


def rnd(x, nd: int = 6) -> float:
    v = round(float(x), nd)
    return 0.0 if v == 0 else v          # avoid -0.0 in the JSON


def rows_of(d: pd.DataFrame) -> list[list]:
    ts = d.ts.to_numpy(np.int64)
    date = pd.to_datetime(ts, unit="s", utc=True).strftime("%Y-%m-%d")
    ev = d.event_slug.to_numpy()
    ms = d.market_slug.to_numpy()
    out = []
    for i in range(len(d)):
        r = d.iloc[i]
        sold, held = (r.o0, r.o1) if int(r.s) == 0 else (r.o1, r.o0)
        kind = "NRFI" if r.mtype == "nrfi" else "total 8.5"
        side = f"{held} - maker fill (sold {sold} @ {r.a:.3f}, so long {held} @ {1 - r.a:.3f})"
        spread = "1-sided book" if not np.isfinite(r.spread_c) else f"spread {r.spread_c:+.1f}c"
        cut = " (inv cap)" if r.at_cap else (" (K cap)" if r.sh >= H.PARAMS["K"] - 1e-9 else "")
        # kept under ~160 chars: the explorer page truncates the note at 160 and the resolution
        # is the part a reader most needs to see.
        note = (f"{kind} T-{r.hrs:.2f}h; taker paid {r.q_taker:.3f} "
                f"{'THROUGH our stale quote' if r.through else 'at our quote'}; "
                f"filled {r.sh:.1f}sh{cut}; {spread}; "
                f"rebate {100 * r.reb:.3f}c/sh @fee {r.fee_rate:.0%}; {sold} paid {r.y:.0f}")
        out.append([i + 1, r.period, date[i], "baseball", "mlb", str(ev[i]), str(ms[i]), side,
                    int(ts[i]), rnd(r.entry_price), rnd(r.stake_usd), rnd(r.fee_usd), "resolution",
                    int(r.closed_ts), rnd(r.exit_price), rnd(r.payout), rnd(r.pnl_usd),
                    rnd(r.roi_row), note])
    return out


def main() -> None:
    d, stats = build()
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
        d_c = t["c_per_share"] - h["c_per_share"]
        ok &= (d_bets == 0) and abs(d_pnl) < 0.01 and abs(d_roi) < 5e-7 and abs(d_c) < 5e-5
        print(f"  {p:8s} bets {t['bets']:,} vs {h['bets']:,} (d={d_bets}); "
              f"markets {t['markets']} vs {h['markets']}; games {t['games']} vs {h['games']}; "
              f"shares {t['shares']:,.0f} vs {h['shares']:,.0f}; "
              f"notional ${t['notional_usd']:,.0f} vs ${h['notional_usd']:,.0f}; "
              f"P&L ${t['pnl_usd']:,.2f} vs ${h['pnl_usd']:,.2f} (d=${d_pnl:+.4f}); "
              f"ROI {100 * t['roi']:.4f}% vs {100 * h['roi']:.4f}% (d={100 * d_roi:+.6f}pp); "
              f"c/sh {t['c_per_share']:+.4f} vs {h['c_per_share']:+.4f} (d={d_c:+.6f})")
    print(f"  reproduces headline: {ok}")

    # ---- row cap: keep every holdout fill, sample dev deterministically (LEDGER_SPEC.md)
    n_total = len(d)
    truncated = n_total > MAX_ROWS
    if truncated:
        hold = d.index[d.period == "holdout"].to_numpy()
        dev = d.index[d.period == "dev"].to_numpy()
        keep = np.random.default_rng(SEED).choice(dev, size=MAX_ROWS - len(hold), replace=False)
        rows_df = d.loc[np.sort(np.concatenate([hold, keep]))].reset_index(drop=True)
    else:
        rows_df = d
    shown = totals(rows_df)

    doc = {
        "slug": SLUG,
        "title": "Prop-tax market making in MLB NRFI and total 8.5",
        "group": "Thorp hypotheses",
        "sport": "baseball",
        "verdict": "DEAD",
        "hypothesis": (
            "MLB side markets (NRFI, the fixed total-8.5 line) were supposed to be the thin, recreational "
            "corner of the book: few makers, wide spreads, and takers who are there for fun rather than for "
            "value, while the deep moneyline on the same game anchors fair value for free. A maker who quotes "
            "both tokens at the last print and pulls whenever the moneyline jumps should therefore collect a "
            "wide spread from a losing flow - a 'prop tax' on recreational bettors."),
        "mechanism": (
            "The claimed edge has two legs, and the data kills both. (1) Width: the quoted-spread proxy at our "
            "own fills is a median of 1.0c in total 8.5 and 1.0-2.0c in NRFI - the same as the moneyline - so "
            "joining the touch is worth at most 0.5c a share before adverse selection, and the markout to the "
            "pregame close says we actually keep only +0.15c. (2) A losing counterparty: total-8.5 takers made "
            "+6.5% (dev) and +9.1% (holdout) gross on a dollar-weighted basis, better than moneyline takers in "
            "the same games, so there is no tax to collect. What is left is a stale-quote inventory business: "
            "our ask is the last print, which in NRFI can be hours old, so when the book moves the next taker "
            "picks us off, and the flow is one-directional enough that 28-39% of filled markets hit the "
            "500-share inventory cap."),
        "entry_rule": (
            "Universe: MLB games whose moneyline has >= $25,000 of pre-start volume; for each such game take "
            "'<event>-nrfi' and '<event>-total-8pt5' from the universe when they exist (the line is fixed, "
            "never chosen by volume). 3,411 candidates; a seeded (20260919) random sample takes 1,000 dev "
            "markets (475 NRFI / 525 total) and 1,000 holdout markets (505 / 495). T = the scheduled "
            "game_start_ts; dev is T < 2026-07-01 UTC. QUOTE: from T-12h to T-15m we rest an ask on each token "
            "s at a = the price of the last Data-API taker fill acquiring s with ts <= t-3s, and no quote at "
            "all if that print is more than 6h old. Side, price and payout are oriented by token id, never by "
            "the Data API's outcomeIndex (which is wrong on ~1% of fills). FILL (each row here): a taker order "
            "acquiring s at q >= a - 1e-6 lifts us for min(order size, K=100) shares at a, truncated by a net "
            "inventory cap of 500 shares per market. Selling token s at a is the same position as buying the "
            "complementary token at 1 - a, which is this row's entry_price and capital at risk. PULL: we hold "
            "no quotes in (t_ml, t_ml + 60s] after any same-game moneyline fill whose outcome-0 price differs "
            "by >= 0.02 from the median of that market's fills in [t_ml - 300s, t_ml). The fill model assumes "
            "FRONT-OF-QUEUE priority at the price that just traded - that is the pre-registered rule, and the "
            "report's back-of-queue stress shows it is the optimistic end."),
        "exit_rule": (
            "Held to resolution - these are real positions, not markouts. Each row is one maker fill (never "
            "aggregated per market): we hold the complementary token from the fill until the market resolves, "
            "so exit_price is its payout (1 if the side we sold lost, 0 if it won; 0.5 would be a void, none "
            "occur here) and payout = shares x exit_price. exit_ts is the side market's `closed_ts` from the "
            "universe, i.e. when the market actually resolved (median 2.7h after the scheduled first pitch, "
            "99th pct 8.7h); it is metadata only and does not enter the P&L, and 13 of the shipped rows carry "
            "a closed_ts slightly earlier than the scheduled first pitch, which is an upstream timestamp "
            "quirk (every row's exit_ts is still after its entry_ts). No inter-fill netting or early "
            "flattening is modelled, and the "
            "two legs of a market are never merged, which is why the capital at risk (1 - a per share) is "
            "conservative. The report's variant (a) marks the same fills out to the last two-sided pregame "
            "print mid instead of holding, and that is what isolates the spread capture (+0.15c/share)."),
        "cost_model": (
            "Makers pay no Polymarket trading fee, so fee_usd is never positive. We are credited a maker "
            "rebate of 15% of the taker fee on our own fill: 0.15 x fee_rate x a x (1 - a) per share, with "
            "each market's own fee_rate (0 in 2025, 0.03 Mar-Jun 2026, 0.05 from Jul 2026), which is why "
            "fee_usd is negative. That is an approximation of Polymarket's rebate pool. No entry slippage is "
            "modelled because we are the passive side: the fill price is exactly our resting quote a. The "
            "report's stress row charges 1c a share worse on every fill (dev -$4,211 / -0.59c, holdout "
            "-$14,498 / -2.04c with a CI excluding zero); the back-of-queue stress, which is the real "
            "execution risk, is far worse still (-5.36c dev, -3.61c holdout)."),
        "periods": {
            "dev": f"{span['dev'][0]}..{span['dev'][1]} (entry timestamps; markets with scheduled first "
                   f"pitch < 2026-07-01 UTC, i.e. 2025-08..2026-06)",
            "holdout": f"{span['holdout'][0]}..{span['holdout'][1]} (entry timestamps; scheduled first pitch "
                       f">= 2026-07-01 UTC, through 2026-09-18)",
        },
        "headline": {
            p: {"bets": h["bets"], "roi": h["roi"], "ci_lo": h["ci_lo"], "ci_hi": h["ci_hi"],
                "pnl_usd": h["pnl_usd"], "c_per_share": h["c_per_share"],
                "c_ci_lo": h["c_ci_lo"], "c_ci_hi": h["c_ci_hi"]}
            for p, h in REPORT_HEADLINE.items()
        },
        "review": (
            "The adversarial reviewers did not have to refute anything - they agreed the premise itself is "
            "false. Totals books are not wide (a 1c median spread, the same as the moneyline), and totals "
            "takers were not the losing side: they made +6.5% (dev) to +9.1% (holdout) gross, better than "
            "moneyline takers in the same games, so there is no recreational tax to collect. The holdout loss "
            "is mostly outcome drift after the close rather than bad quoting; the genuine maker edge is the "
            "+0.15c/share markout to the pregame close, which is statistically positive in both periods but "
            "economically nil (0.3% of notional, ~$1.5-1.9 per market) and far below the cost of exiting. The "
            "spread that does exist belongs to whoever is already at the front of the queue: under the "
            "back-of-queue stress the rule loses 3.6-5.4c a share. Two bugs found and fixed before the holdout "
            "ran (Data API outcomeIndex disagreeing with the token id on ~1% of fills; float noise breaking a "
            "strict q >= a comparison), plus one market hand-traced fill by fill."),
        "caveats": [
            f"TRUNCATED: {n_total:,} maker fills in total, capped at {MAX_ROWS:,} rows by LEDGER_SPEC.md. "
            f"Every holdout fill ({full['holdout']['bets']:,}) is kept; the dev rows are a seeded "
            f"(numpy default_rng({SEED})) sample of {shown['dev']['bets']:,} of {full['dev']['bets']:,} "
            f"({100 * shown['dev']['bets'] / full['dev']['bets']:.1f}%). Per-fill P&L here is heavy-tailed "
            f"(each row resolves to 0 or 1), so that thin dev sample is NOT representative: the shipped dev "
            f"rows come to {100 * shown['dev']['roi']:+.2f}% ROI / ${shown['dev']['pnl_usd']:,.0f} against "
            f"the true dev headline of {100 * full['dev']['roi']:+.2f}% / ${full['dev']['pnl_usd']:,.0f}. "
            f"Read the dev number from \"totals_full\" (which reproduces the report exactly), not by summing "
            f"the rows; \"totals_rows\" holds the totals of the rows actually shipped here. Every holdout row "
            f"is present, so the holdout period does sum to its headline exactly.",
            "These rows are the pre-registered PRIMARY fill model, which assumes FRONT-OF-QUEUE priority at "
            "the price that just traded. That is the optimistic end: the back-of-queue stress (fill only if a "
            "later same-side taker trades through our price within 60s) gives -5.36c/share [-9.64, -1.29] in "
            "dev and -3.61c [-7.72, +0.65] in the holdout, and it is what makes the verdict DEAD rather than "
            "merely unprofitable.",
            "There are no historical order books. 'The touch' is the last taker print on each side, and the "
            "Data API reports a sweeping order at its VWAP, so a VWAP can stand in for a tick. When the two "
            "last prints cross, our two quotes are crossed with each other (348 dev events, 683 holdout); this "
            "is kept as specified and it hurts us.",
            "The rebate is modelled as 15% of the taker fee on our own fill; the real rebate is a pool paid by "
            "Polymarket's own formula. It is worth 0.03-0.06c a share here, an order of magnitude below the "
            "holdout loss, so it does not drive the verdict either way.",
            "Fee regimes differ between the periods: dev mixes fee-free 2025 (only 125 games, and the only "
            "strongly positive stretch at +5.32c/share [+0.78, +11.03]) with the 3% regime of 2026, while the "
            "holdout is almost entirely the 5% regime. By month the edge is +9.5c (2025-09), +3.8 (2026-03), "
            "-0.0, -1.2, +1.0, then -1.5, -0.6, -0.9 across the holdout.",
            "Sample selection: pre_usd measures moneyline volume up to T while we quote until T-15m, a mild "
            "use of post-decision information. It concerns liquidity rather than the outcome and applies "
            "equally to both splits. T is the scheduled first pitch, not the actual one.",
            "Only MLB was tested, by design; soccer over/under was excluded to avoid overlap with other "
            "hypotheses. The only per-sport split available is NRFI (dev -1.12c, holdout -1.24c) against "
            "total 8.5 (dev +0.67c, holdout -0.98c).",
            "Variants not exported (all in the report): (a) markout to the last two-sided pregame print mid "
            "(+0.16c dev / +0.15c holdout, the only variant positive in the holdout - it measures spread "
            "capture, not a tradeable P&L) and the same fills held to resolution; (b) no pull rule; (c) K=25 "
            "and K=500; (d) back-of-queue stress; (e) NRFI-only and total-8.5-only; plus diagnostic splits by "
            "fee regime, hours-to-start, and whether the taker joined our quote (+1.43c dev / -0.78c holdout) "
            "or traded through it (-4.17c / -2.30c).",
            "The pull rule is almost inert: it removed 261 of 36,167 dev events and 58 of 34,643 holdout "
            "events, worth 0.02-0.08c a share. Pregame moneylines rarely jump 2c against their 5-minute "
            "median.",
            "Capacity is tiny even if the fills were real: ~710 shares and ~$350 of notional per market, "
            "14.5-16.9 candidate markets a day, so ~$5-6k of notional a day in total - about +$43/day at the "
            "dev rate and -$125/day at the holdout rate.",
        ],
        "report_path": f"reports/research/{SLUG}.md",
        "code_path": f"pmsports/research/h_{SLUG}.py",
        "truncated": bool(truncated),
        "n_total_trades": int(n_total),
        "totals_full": full,
        "totals_rows": shown,
        "simulate_info": stats,
        "columns": COLUMNS,
        "rows": rows_of(rows_df),
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, separators=(",", ":")))
    print(f"\nwrote {OUT}  ({OUT.stat().st_size / 1e6:.1f} MB, {len(doc['rows']):,} rows of {n_total:,})")
    for p in ("dev", "holdout"):
        t = shown[p]
        print(f"  rows in file: {p:8s} {t['bets']:,} fills, stake ${t['notional_usd']:,.2f}, "
              f"P&L ${t['pnl_usd']:,.2f}, ROI {100 * t['roi']:+.4f}%, {t['c_per_share']:+.4f}c/sh")


if __name__ == "__main__":
    main()
