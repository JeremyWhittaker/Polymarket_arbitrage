"""Per-trade ledger export for hypothesis `inplay_certainty_premium` (see ../LEDGER_SPEC.md).

The rows are the ACTUAL bets the backtest made. This module re-runs the hypothesis script's own
primary path -- `H.universe()` -> `H.pass1()` (cached) -> `H.signals()` -> `H.windows()` ->
`H.maker_pnl(H.simulate(...))`, i.e. exactly the `run_set(sig, win, "PRIMARY phi=0.25 cap=200")`
call inside `h_inplay_certainty_premium.main()` -- and then asserts, column by column, that the
result is identical to the saved artifact `data/research/h_inplay_certainty_premium/
events_primary_full.parquet` that the reported numbers were computed from. Nothing is
re-simulated by hand.

UNIT OF A ROW: one row per FILLED SIGNAL (= per event), with that event's modelled maker fills
AGGREGATED. LEDGER_SPEC.md allows this and asks that it be said plainly, so: the hypothesis rests
one ask per event and caps it at 200 shares per event; inside the 5-minute window that ask is hit
by several taker orders (64,962 modelled fills over 13,357 filled events, median 3 per event,
max 130), all at the SAME price `a`, in the SAME market, on the SAME side, held to the SAME
resolution. Per-fill rows would be 64,962 rows (over the 20,000 cap) and would carry no extra
price information. The backtest itself scores at this per-event granularity: `stat_maker()` runs
on exactly these per-event rows, which is why the report's bet count is "events filled". `sh` on
a row is the total shares filled across that event's fills and `note` gives the fill count and the
time span. Signals that were never filled (`sh == 0`: 2,403 of 15,760) are NOT rows -- the report
counts them as unfilled signals, and they are the comeback games that carry the premium.

These are real HELD positions, not markouts: `exit_kind` is "resolution" on every row. They are
however MODELLED maker fills from a stylised queue model (phi = 0.25, 60-s level-exhaust rule),
not recorded maker fills -- the fills data set only has taker rows.

Accounting per row (consistent with `simulate()`/`maker_pnl()`, where `cap = 1 - a` is the
capital per share and `pnl = a - y_s + 0.15 * fee_rate * a * (1-a)` is the P&L per share):
we are the maker selling token s at `a`, which is the same position as buying the complementary
token (the underdog tail) at `1 - a`. So `entry_price = 1 - a`, `stake_usd = sh * (1 - a)`
(= the row's capital), `exit_price = 1 - y_s` (1 if the tail won, 0 if the favourite closed it
out, 0.5 void), `payout = sh * (1 - y_s)`, `fee_usd = -sh * rebate` (makers pay no fee; the
rebate is a credit, hence negative) and `pnl_usd = payout - stake_usd - fee_usd = sh * pnl`.

Run:
    PYTHONPATH=. systemd-run --user --scope -q -p MemoryMax=5G -p MemorySwapMax=0 \
        .venv/bin/python -m pmsports.research.ledger_inplay_certainty_premium
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from pmsports.research import common as C
from pmsports.research import h_inplay_certainty_premium as H

SLUG = "inplay_certainty_premium"
OUT = C.RESEARCH / "ledgers" / f"{SLUG}.json"
MAX_ROWS = 20_000
SEED = 0

COLUMNS = ["id", "period", "date", "sport", "league", "event", "market", "side", "entry_ts",
           "entry_price", "stake_usd", "fee_usd", "exit_kind", "exit_ts", "exit_price", "payout",
           "pnl_usd", "roi", "note"]

# Headline of the PRIMARY (pre-registered, phi = 0.25, cap 200) rule, from the summary table at the
# top of reports/research/inplay_certainty_premium.md ("DEV pooled" and "HOLDOUT" rows), with the
# full-precision values from data/research/h_inplay_certainty_premium/results_full.json ->
# "PRIMARY phi=0.25 cap=200" in `exact`.
REPORT_HEADLINE = {
    "dev": {"bets": 10048, "roi": -0.147, "ci_lo": -0.208, "ci_hi": -0.082, "pnl_usd": -20632.0,
            "shares": 1687033.0, "capital_usd": 140159.0, "c_per_share": -1.22,
            "exact": {"events": 10048, "shares": 1687033.1292339903, "capital": 140159.22680166317,
                      "pnl_usd": -20632.27987029677, "c_per_share": -1.222991979989451,
                      "roi": -0.14720600520644386, "roi_lo": -0.20841925960926003,
                      "roi_hi": -0.08162625088759527}},
    "holdout": {"bets": 3309, "roi": -0.202, "ci_lo": -0.305, "ci_hi": -0.099, "pnl_usd": -9493.0,
                "shares": 554094.0, "capital_usd": 46903.0, "c_per_share": -1.71,
                "exact": {"events": 3309, "shares": 554094.4355730625, "capital": 46902.6670205114,
                          "pnl_usd": -9492.70437404544, "c_per_share": -1.7131925109891735,
                          "roi": -0.2023915691168289, "roi_lo": -0.30501174200159803,
                          "roi_hi": -0.09883155518460773}},
}
# Per-period sub-splits of DEV, same source (used for the extra check printed by main()).
REPORT_DEV_SPLIT = {
    "dev25": {"events": 3719, "pnl_usd": -9244.916579929079, "roi": -0.19401575221761239},
    "dev26h1": {"events": 6329, "pnl_usd": -11387.363290367692, "roi": -0.12309480416040378},
}


# ----------------------------------------------------------------------------- build

def primary() -> pd.DataFrame:
    """The hypothesis script's own PRIMARY run, then checked against its saved artifact."""
    u = H.universe()
    cand, _er = H.pass1(u)                       # cached parquet; _er is variant (e) only
    del _er
    sig = H.signals(u, cand)                     # one signal per event (primary universe)
    win = H.windows(sig, "primary")              # cached parquet
    d = H.maker_pnl(H.simulate(sig, win))        # == run_set(sig, win, "PRIMARY phi=0.25 cap=200")

    saved = H.CACHE / "events_primary_full.parquet"
    if saved.exists():                           # the file the report's numbers came from
        sv = pd.read_parquet(saved)
        a = d.sort_values("ev").reset_index(drop=True)
        b = sv.sort_values("ev").reset_index(drop=True)
        assert len(a) == len(b), (len(a), len(b))
        for c in ("m", "s", "t0", "a", "y_s", "fee_rate", "ev", "sh", "sh_exh", "n_fills",
                  "a_used", "pnl", "cap"):
            assert np.allclose(a[c].astype(float), b[c].astype(float), atol=1e-9, equal_nan=True), c
        assert (a.event_slug.to_numpy() == b.event_slug.to_numpy()).all()
    return d, sig, win


def fill_spans(sig: pd.DataFrame, win: pd.DataFrame) -> pd.DataFrame:
    """The taker orders our resting ask can be hit by, per event, in the order `simulate()` walks
    them: the SAME mask (our side, ts in [t0+3, t0+300], q >= a - TOL) and the same stable
    (ev, ts, q) sort. `simulate()` gives a positive fill to every one of these orders until the
    200-share cap runs out, so the k-th order is the k-th fill and the first `n_fills` are ours."""
    w = win.merge(sig[["ev", "s", "t0", "a"]].rename(columns={"s": "s_sig"}), on="ev")
    w = w[w.s == w.s_sig]
    w = w.sort_values(["ev", "ts", "q"], kind="stable").reset_index(drop=True)
    inwin = (w.ts >= w.t0 + H.DELAY) & (w.ts <= w.t0 + H.WIN_END) & (w.q >= w.a - H.TOL)
    q = w[inwin.to_numpy()][["ev", "ts", "q", "size"]].copy()
    q["k"] = q.groupby("ev").cumcount() + 1
    return q


def build() -> pd.DataFrame:
    d, sig, win = primary()
    f = d[d.sh > 0].copy()

    # timestamps of the first and last modelled fill in each event's window
    q = fill_spans(sig, win)
    first = q[q.k == 1].set_index("ev")[["ts", "q", "size"]].rename(
        columns={"ts": "ts_first", "q": "q_first", "size": "size_first"})
    last = q.merge(f[["ev", "n_fills"]], on="ev")
    last = last[last.k == last.n_fills].set_index("ev").ts.rename("ts_last")
    f = f.join(first, on="ev").join(last, on="ev")
    assert f.ts_first.notna().all() and f.ts_last.notna().all()
    assert (f.ts_first >= f.t0 + H.DELAY).all() and (f.ts_last <= f.t0 + H.WIN_END).all()

    mk = C.markets().set_index("m")[["market_slug", "o0", "o1", "closed_ts"]]
    f = f.join(mk, on="m")
    assert f.closed_ts.notna().all() and (f.closed_ts >= f.ts_last).all()

    # accounting (see the module docstring); `cap` and `pnl` come straight from maker_pnl()
    a = f.a_used.to_numpy(float)
    reb = C.MAKER_REBATE * f.fee_rate.to_numpy(float) * a * (1 - a)
    assert np.allclose(f.pnl.to_numpy(float), a - f.y_s.to_numpy(float) + reb, atol=1e-12)
    assert np.allclose(f.cap.to_numpy(float), 1 - a, atol=1e-12)
    f["period"] = np.where(f.per == "hold", "holdout", "dev")
    f["rebate_per_share"] = reb
    f["entry_price"] = 1.0 - a
    f["stake_usd"] = f.sh * f.cap
    f["fee_usd"] = -(f.sh * reb)
    f["exit_price"] = 1.0 - f.y_s.astype(float)
    f["payout"] = f.sh * f.exit_price
    f["pnl_usd"] = f.sh * f.pnl
    f["roi_row"] = f.pnl / f.cap
    assert np.allclose(f.payout - f.stake_usd - f.fee_usd, f.pnl_usd, atol=1e-9)

    f = f.sort_values(["ts_first", "ev"], kind="stable").reset_index(drop=True)
    return f


def totals(d: pd.DataFrame, by="period") -> dict:
    out = {}
    for p, g in d.groupby(by):
        out[str(p)] = dict(bets=int(len(g)), events=int(g.event_slug.nunique()),
                           shares=float(g.sh.sum()), stake_usd=float(g.stake_usd.sum()),
                           fee_usd=float(g.fee_usd.sum()), pnl_usd=float(g.pnl_usd.sum()),
                           roi=float(g.pnl_usd.sum() / g.stake_usd.sum()),
                           cents_per_share=float(100 * g.pnl_usd.sum() / g.sh.sum()))
    return out


def rnd(x, nd: int = 6) -> float:
    v = round(float(x), nd)
    return 0.0 if v == 0 else v                  # avoid -0.0 in the JSON


def rows_of(d: pd.DataFrame) -> list[list]:
    ev = d.event_slug.to_numpy()
    ms = d.market_slug.to_numpy()
    lg = d.league.fillna("").to_numpy()
    fam = d.family.fillna("").to_numpy()
    ts = d.ts_first.to_numpy(np.int64)
    date = pd.to_datetime(ts, unit="s", utc=True).strftime("%Y-%m-%d")
    out = []
    for i in range(len(d)):
        r = d.iloc[i]
        s = int(r.s)
        sold, held = (str(r.o0), str(r.o1)) if s == 0 else (str(r.o1), str(r.o0))
        side = f"{held} - maker fill (sold {sold} @ {r.a_used:.3f})"
        span = int(r.ts_last - r.t0)
        note = (f"lock: {sold} first traded {r.a_used:.3f} in-play {(r.t0 - r.game_start_ts) / 60:.0f}m "
                f"after start (pregame {r.pre_s:.2f}); {int(r.n_fills)} "
                f"{'fill' if r.n_fills == 1 else 'fills'} in t0+[3,{span}]s, "
                f"{r.sh:.0f}sh{' (cap)' if r.sh >= H.CAP - 1e-6 else ''}, "
                f"{100 * r.sh_exh / r.sh:.0f}% through an exhausted level; "
                f"rebate {100 * r.rebate_per_share:.4f}c/sh @fee {r.fee_rate:.3f}; "
                f"{'TAIL WON' if r.y_s < 0.5 else ('void' if r.y_s == 0.5 else 'favourite held')}")
        out.append([i + 1, r.period, date[i], str(fam[i]), str(lg[i]), str(ev[i]), str(ms[i]), side,
                    int(ts[i]), rnd(r.entry_price), rnd(r.stake_usd), rnd(r.fee_usd), "resolution",
                    int(r.closed_ts), rnd(r.exit_price), rnd(r.payout), rnd(r.pnl_usd),
                    rnd(r.roi_row), note])
    return out


# ----------------------------------------------------------------------------- main

def main() -> None:
    d = build()
    full = totals(d)
    split = totals(d, by="per")
    day = pd.to_datetime(d.game_start_ts, unit="s", utc=True).dt.strftime("%Y-%m-%d")
    span = {p: (g.min(), g.max()) for p, g in day.groupby(d.period)}

    # ---- reproduction check against the report headline
    print("\n=== reproduction check: ledger sums vs reports/research/%s.md" % SLUG)
    ok = True
    for p, h in REPORT_HEADLINE.items():
        t, x = full[p], h["exact"]
        ok &= (t["bets"] == h["bets"]) and abs(t["pnl_usd"] - x["pnl_usd"]) < 1e-6 \
            and abs(t["roi"] - x["roi"]) < 1e-9 and abs(t["stake_usd"] - x["capital"]) < 1e-6 \
            and abs(t["shares"] - x["shares"]) < 1e-6 \
            and abs(t["cents_per_share"] - x["c_per_share"]) < 1e-9
        print(f"  {p:8s} rows {t['bets']:,} vs report 'events filled' {h['bets']:,}; "
              f"shares {t['shares']:,.1f} vs {h['shares']:,.0f}; "
              f"capital(=sum stake) ${t['stake_usd']:,.2f} vs ${h['capital_usd']:,.0f}; "
              f"P&L ${t['pnl_usd']:,.2f} vs ${h['pnl_usd']:,.0f} "
              f"(exact {x['pnl_usd']:.6f}, d={t['pnl_usd'] - x['pnl_usd']:+.2e}); "
              f"ROI {100 * t['roi']:.4f}% vs {100 * h['roi']:.1f}% "
              f"(exact {100 * x['roi']:.6f}%, d={100 * (t['roi'] - x['roi']):+.2e}pp); "
              f"c/share {t['cents_per_share']:+.4f} vs {h['c_per_share']:+.2f}")
    for p, h in REPORT_DEV_SPLIT.items():
        t = split[p]
        ok &= (t["bets"] == h["events"]) and abs(t["pnl_usd"] - h["pnl_usd"]) < 1e-6 \
            and abs(t["roi"] - h["roi"]) < 1e-9
        print(f"  {p:8s} rows {t['bets']:,} vs {h['events']:,}; P&L ${t['pnl_usd']:,.2f} vs "
              f"${h['pnl_usd']:,.2f}; ROI {100 * t['roi']:.4f}% vs {100 * h['roi']:.4f}%")
    print(f"  reproduces headline: {ok}")

    n_total = len(d)
    truncated = n_total > MAX_ROWS
    assert not truncated, "row cap logic not needed; add sampling if this ever fires"

    doc = {
        "slug": SLUG,
        "title": "In-play certainty premium (sell the new 90-97c lock)",
        "group": "Thorp hypotheses",
        "sport": "multi",
        "verdict": "DEAD",
        "hypothesis": (
            "When a side first becomes a 90%+ favourite during play, two groups cross the spread to buy it "
            "-- 'sure-thing' yield buyers hunting a few cents of certain return, and bettors piling onto the "
            "side that is winning -- while variance-limited makers refuse to hold the 3-10c comeback tail "
            "near the end of a game. If that is true the tail is systematically underpriced, and whoever "
            "sells certainty (i.e. holds the underdog) collects a premium. The proposer's prior was "
            "+2.2c/share in 2025 and +0.8c in 2026H1."),
        "mechanism": (
            "The counterparty is a taker who buys a 92c lock for the yield or for the thrill of backing the "
            "winner, and pays the ask to get it; the natural seller is a maker who does not want a position "
            "that loses 92c whenever a comeback happens. That imbalance should leave the favourite a little "
            "too dear. The premium is real AT THE PRINT -- selling one share at exactly the price the "
            "lock-buyer just paid earned +2.15c in 2025 -- but it is not tradable, and it has decayed to "
            "+0.31c [-0.59, +1.21] in the holdout. A maker who posts at that same price 3 s later is "
            "adversely selected: about 95% of the shares arrive through a level that was traded through, "
            "i.e. the favourite kept rising, and the comeback games that carry the whole premium are exactly "
            "the ones where no taker ever comes back to buy at that price."),
        "entry_rule": (
            "Universe: C.markets() with pregame taker volume pre_usd >= $20,000, all sports (20,001 markets "
            "in 18,157 events). Fills sharing (market, wallet, ts, side, price) are one taker order split "
            "across makers and are merged first. SIGNAL, one per event_slug: the first in-play taker order "
            "(ts >= scheduled game_start_ts) in any universe market of the event in which the taker acquired "
            "side s at q in (0.90, 0.97] while s's pregame price (pre_mid0 for s=0, 1-pre_mid0 for s=1) was "
            "< 0.80; ties inside the first second go to the lowest q, then the lowest market code. That "
            "print sets t0 = its ts and a = its price. ENTRY: from t0+3 s to t0+300 s we rest an ask on s at "
            "a -- the same position as a maker bid on the complementary token (the underdog tail) at 1-a, "
            "which is this ledger's entry_price -- capped at 200 shares per event. PRE-REGISTERED QUEUE "
            "MODEL (phi = 0.25): each later taker order acquiring s at q >= a inside the window fills us "
            "min(order size, remaining cap) shares if the level was exhausted (some fill acquires s at "
            "q > a with ts within 60 s, which is itself proof that a is gone), otherwise phi x that. Signals "
            "fire on 15,760 events (DEV 11,965, holdout 3,795); 13,357 of them get at least one fill and are "
            "the rows here, 2,403 are never filled and are not rows."),
        "exit_rule": (
            "Held to resolution of the same market -- there is no exit trade, no stop and no markout. "
            "exit_price is the payout of the side we hold (the tail): 1 if the comeback happened, 0 if the "
            "favourite closed it out, 0.5 on a void (6 rows); exit_ts is the market's closed_ts. ONE ROW PER "
            "FILLED EVENT, WITH THAT EVENT'S FILLS AGGREGATED: the rule rests a single ask per event at a "
            "single price a and caps it at 200 shares, so an event's fills are all the same price, market, "
            "side and resolution (64,962 modelled fills over 13,357 events; median 3, max 130 per event). "
            "stake_usd/pnl_usd on a row are that event's totals, and note gives the fill count and the "
            "window span. The fills are MODELLED maker fills from a stylised queue model, not recorded maker "
            "fills: the data set contains taker rows only, so phi = 0.25 and the 60-s exhaust rule are "
            "assumptions (phi = 0 and phi = 1 are both significantly negative too)."),
        "cost_model": (
            "Makers pay no Polymarket trading fee, so fee_usd is never positive. A maker rebate of 15% of "
            "the taker fee on our own fill is credited: 0.15 x fee_rate x a x (1-a) per share at each "
            "market's own fee_rate -- on these rows that is 0.00 on all 3,719 DEV-2025 rows and on 2,628 "
            "DEV-2026H1 rows, 0.0175 on 36 and 0.03 on 3,665 DEV-2026H1 rows, and in the holdout 0.05 on "
            "2,903 rows with 406 older markets (games up to 2026-08-29) still on the 0.03 rate -- "
            "which is why fee_usd is negative. The rebate is tiny here -- +0.02c/share in DEV 2026H1 and "
            "+0.05c in the holdout -- and removing it changes the headline only from -14.7% to -14.9% (DEV) "
            "and -20.2% to -20.9% (holdout). No slippage is added on entry because we are the passive side: "
            "the fill price is exactly our resting quote a. The report's sensitivity run prices every fill "
            "1c worse (a - 0.01) and gives DEV -23.9% and holdout -28.6% [-37.8, -19.3]."),
        "periods": {
            "dev": f"{span['dev'][0]}..{span['dev'][1]} (scheduled game_start_ts < 2026-07-01; the report "
                   f"splits it into DEV 2025 and DEV 2026H1)",
            "holdout": f"{span['holdout'][0]}..{span['holdout'][1]} (scheduled game_start_ts >= 2026-07-01, "
                       f"mostly the 5% taker-fee regime: 2,903 of the 3,309 rows are at fee_rate 0.05 and "
                       f"406 older markets are still at 0.03)",
        },
        "headline": {
            p: {"bets": h["bets"], "roi": round(h["exact"]["roi"], 6),
                "ci_lo": round(h["exact"]["roi_lo"], 6), "ci_hi": round(h["exact"]["roi_hi"], 6),
                "pnl_usd": round(h["exact"]["pnl_usd"], 2),
                "shares": round(h["exact"]["shares"], 1),
                "capital_usd": round(h["exact"]["capital"], 2),
                "cents_per_share": round(h["exact"]["c_per_share"], 4)}
            for p, h in REPORT_HEADLINE.items()
        },
        "review": (
            "None of the adversarial reviewers refuted the DEAD verdict -- reports/THORP.md calls this "
            "\"the most conclusive result of the 12\". Their conclusion was that the loss is adverse "
            "selection, not a bug: the maker is filled precisely when the favourite keeps rising, and in the "
            "comeback games that carry the premium nobody ever trades back to the maker's price. The "
            "author's own bug checks all came back clean (payout orientation matches `markets` for all "
            "11,965 DEV signals with 0 mismatches; a hand-coded queue loop reproduced the vectorised share "
            "counts; the look-ahead in the exhaust flag works against us by granting fuller fills when the "
            "favourite rises), and every generous re-cut stays negative: front-of-queue phi = 1 is -18.0% in "
            "the holdout, the taker version -10.5%, joining at t0+6 s -1.80c, rounding a up to the tick grid "
            "-1.67c, and even the most generous upper bound -- filled at the price each taker actually paid "
            "-- is -2.2%."),
        "caveats": [
            "Rows are per FILLED EVENT with that event's modelled fills aggregated, not per fill: 13,357 "
            "rows cover 64,962 modelled fills. All of an event's fills share one price, market, side and "
            "resolution, so nothing is lost but the individual print times (note carries the count and the "
            "window span).",
            "The fills are modelled, not observed. The data set has taker rows only, so whether our resting "
            "ask would have been hit is an assumption: phi = 0.25 of each same-level order, full size when "
            "the level is traded through within 60 s. The sign does not depend on it -- phi = 0 gives "
            "-15.5% (DEV) / -22.0% (holdout) and phi = 1 gives -12.7% / -18.0%, both still entirely "
            "negative -- but the exact P&L on any single row does.",
            "Unfilled signals are not in this file and that is where the money was: 2,403 of 15,760 signals "
            "(13-23% per period) never traded back to a, they are the comebacks, and selling them at a "
            "would have paid +10.9c, +8.8c and +9.7c per share. A maker cannot fill there, which is the "
            "whole result.",
            "The holdout is not fully clean: the proposer had already looked at 2026H2 with an "
            "unconstrained fill model (sell at the signal print) before pre-registering this rule. The "
            "primary, the variants and the verdict rule (including \"DEV CI entirely below 0 means DEAD\") "
            "were fixed on DEV-only runs and the full run was then made once.",
            "We may cross at the join: if at t0+3 s the bid on s is already at or above a, a real order at a "
            "would cross and fill immediately as a taker (better price, but paying the fee). The model "
            "instead waits for a later print. Few events, and it would not change the sign.",
            "In-play is defined by the scheduled start, and tennis and esports often start late; 66 DEV "
            "signals come within 5 min of the scheduled start.",
            "Variants not exported (all pre-listed, all in the report): (a) phi = 0 and phi = 1; (b) the "
            "taker version that buys the tail at the next print (-11.2% DEV / -10.5% holdout, -20.7% / "
            "-21.5% with 1c slippage); (c) buckets of a; (d) pre_usd >= $100k; (e) the merged "
            "`inplay_longshot_maker` version (all in-play fills at [0.90, 0.98), no cap, share-weighted), "
            "whose positive-looking holdout +11.4% [-25.9, +52.7] is dominated by a few huge games -- equal "
            "weight per event it is -2.10c [-2.58, -1.62] and capped at 200 shares -0.26c [-1.07, +0.55]; "
            "(f) the control of sides already >= 0.85 pregame.",
            "Voids pay 0.5 to both sides. 7 signals resolve void (6 DEV -- the count the report gives -- "
            "and 1 holdout); 6 of them were filled and so appear here with exit_price 0.5: 5 dev rows "
            "and 1 holdout row.",
        ],
        "report_path": f"reports/research/{SLUG}.md",
        "code_path": f"pmsports/research/h_{SLUG}.py",
        "truncated": bool(truncated),
        "n_total_trades": int(n_total),
        "n_modelled_fills": int(d.n_fills.sum()),
        "totals_full": full,
        "totals_dev_split": split,
        "columns": COLUMNS,
        "rows": rows_of(d),
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, separators=(",", ":")))
    print(f"\nwrote {OUT}  ({OUT.stat().st_size / 1e6:.1f} MB, {len(doc['rows']):,} rows)")
    print(f"  dev {full['dev']['bets']:,} rows, P&L ${full['dev']['pnl_usd']:,.2f}, "
          f"ROI {100 * full['dev']['roi']:.4f}%; "
          f"holdout {full['holdout']['bets']:,} rows, P&L ${full['holdout']['pnl_usd']:,.2f}, "
          f"ROI {100 * full['holdout']['roi']:.4f}%")


if __name__ == "__main__":
    main()
