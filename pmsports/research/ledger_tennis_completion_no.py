"""Per-trade ledger export for hypothesis `tennis_completion_no` (see ../LEDGER_SPEC.md).

The rows are the ACTUAL fills the backtest traded against: this module calls
`h_tennis_completion_no.sample()` and `h_tennis_completion_no.load_fills()` (the same cached Data API
tapes in `data/research/h_tennis_completion_no/trades/`) and then applies the frozen PRIMARY selection
and P&L formula, fill by fill. Nothing is re-simulated by hand: the per-market aggregation of these
rows is asserted equal, market by market, to `H.maker_markets(f, s)` -- the exact frame the hypothesis
script saved as `maker_markets_<split>.parquet` -- and the per-period totals are asserted equal to
`results_<split>.json["primary"]`.

A "trade" here is one TAKER fill that acquired Yes in [T-6h, T), which the study assumes we were the
maker on (up to 200 shares at whatever price the taker paid). We are therefore selling Yes at `q`,
which is the same position as buying No at `1 - q`, and we hold it to resolution. So per row:

    shares      = min(taker print size, 200)
    entry_price = 1 - q                       (the No price we effectively pay)
    stake_usd   = shares * (1 - q)            (= the study's `capital`, the No collateral)
    fee_usd     = -shares * REBATE * fee_rate * q * (1 - q)   (makers pay no fee; rebate is a credit)
    exit_kind   = "resolution"
    exit_price  = 1 - y_yes                   (the No payout: 1 if the match was NOT completed)
    payout      = shares * (1 - y_yes)
    pnl_usd     = payout - stake_usd - fee_usd = shares * (q - y_yes + REBATE*fee_rate*q*(1-q))

which is exactly `H.maker_markets`'s `pnl` per fill.

Run:
    PYTHONPATH=. systemd-run --user --scope -q -p MemoryMax=5G -p MemorySwapMax=0 \
        .venv/bin/python -m pmsports.research.ledger_tennis_completion_no
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from pmsports.research import common as C
from pmsports.research import h_tennis_completion_no as H

SLUG = "tennis_completion_no"
OUT = C.RESEARCH / "ledgers" / f"{SLUG}.json"
MAX_ROWS = 20_000
SEED = 0

COLUMNS = ["id", "period", "date", "sport", "league", "event", "market", "side", "entry_ts",
           "entry_price", "stake_usd", "fee_usd", "exit_kind", "exit_ts", "exit_price", "payout",
           "pnl_usd", "roi", "note"]

# Headline of the PRIMARY row of reports/research/tennis_completion_no.md (the table at the top),
# cross-checked against data/research/h_tennis_completion_no/results_<split>.json -> "primary".
# `bets` = maker fills; `roi`/`ci_*` = ROI on capital; `ew_c_share` = the verdict statistic
# (equal-weight c/share over markets, CI clustered by match date).
REPORT_HEADLINE = {
    "dev": {"bets": 202, "markets": 129, "dates": 37, "shares": 6608.493724,
            "capital_usd": 1352.785808375294, "pnl_usd": -75.68896524358888,
            "roi": -0.05595044298586482, "ci_lo": -0.5112953372000906, "ci_hi": 0.39917590687163035,
            "ew_c_share": -6.259167243590413, "ew_lo": -11.551689357176869, "ew_hi": -0.8244362330863347,
            "sw_c_share": -1.1453285484513678},
    "holdout": {"bets": 2006, "markets": 480, "dates": 66, "shares": 163233.15983199998,
                "capital_usd": 11494.268788582554, "pnl_usd": -5112.914887367706,
                "roi": -0.44482297929612064, "ci_lo": -0.7686122567417075, "ci_hi": 0.12226244088804043,
                "ew_c_share": -4.453651431609508, "ew_lo": -7.698297864811108, "ew_hi": -1.6972029758653286,
                "sw_c_share": -3.1322771014357187},
}


# ----------------------------------------------------------------------------- build

def primary_fills(split: str) -> pd.DataFrame:
    """Every taker fill that enters the frozen PRIMARY statistic for `split`, one row per fill.

    Selection and per-share P&L are `H.maker_markets(f, s)` with its default arguments
    (win=H.WIN=6h, shift=0, qmask=None), taken apart fill by fill instead of aggregated per market.
    """
    s_all = H.sample()
    s = s_all[(s_all.role == "sample") & (s_all.split == split)].reset_index(drop=True)
    f = H.load_fills(s)

    # ---- identical to H.maker_markets(f, s), before the groupby
    g = f[(f.s == 0) & (f.ts >= f["T"] - H.WIN) & (f.ts < f["T"])].copy()
    a = g.q                                                     # shift = 0 for the primary
    g["sh"] = np.minimum(g["size"], H.CAP)
    g["pnl_sh"] = a - g.y_yes + H.REBATE * g.fee_rate * a * (1 - a)
    g["pnl"] = g.pnl_sh * g.sh
    g["cap"] = (1 - a) * g.sh
    # ---- end of the copied block

    # cross-check: aggregating these fills reproduces the saved per-market frame exactly
    m_ref = H.maker_markets(f, s).set_index("condition_id").sort_index()
    m_own = g.groupby("condition_id").agg(n_fills=("sh", "size"), shares=("sh", "sum"),
                                          pnl=("pnl", "sum"), capital=("cap", "sum")).sort_index()
    assert list(m_own.index) == list(m_ref.index), "market set differs from H.maker_markets"
    for col in ("n_fills", "shares", "pnl", "capital"):
        assert np.allclose(m_own[col], m_ref[col], rtol=0, atol=1e-9), f"{col} differs from H.maker_markets"
    saved = pd.read_parquet(H.OUT / f"maker_markets_{split}.parquet").set_index("condition_id").sort_index()
    for col in ("n_fills", "shares", "pnl", "capital"):
        assert np.allclose(m_own[col], saved[col], rtol=0, atol=1e-9), f"{col} differs from the saved parquet"

    meta = s.set_index("condition_id")
    for col in ("league", "event_slug", "market_slug", "closed_ts", "game_start_ts"):
        g[col] = g.condition_id.map(meta[col])
    g["period"] = "holdout" if split == "holdout" else "dev"

    # ---- ledger accounting (maker sells Yes at q == buys No at 1-q, held to resolution)
    g["rebate_sh"] = H.REBATE * g.fee_rate * a * (1 - a)
    g["entry_price"] = 1.0 - a
    g["stake_usd"] = g.cap                                      # shares * (1 - q)
    g["fee_usd"] = -(g.sh * g.rebate_sh)                        # rebate credited -> negative
    g["exit_price"] = 1.0 - g.y_yes
    g["payout"] = g.sh * (1.0 - g.y_yes)
    g["pnl_usd"] = g.pnl
    g["roi_row"] = np.where(g.stake_usd > 0, g.pnl_usd / g.stake_usd.where(g.stake_usd > 0), np.nan)
    assert np.allclose(g.payout - g.stake_usd - g.fee_usd, g.pnl_usd, atol=1e-9)
    assert g.y_yes.isin([0.0, 1.0]).all(), "voids (0.5 payouts) present; the report says there are none"
    assert (g.ts < g["T"]).all() and (g.ts >= g["T"] - H.WIN).all()
    assert (g.sh <= H.CAP + 1e-9).all()
    return g


def totals(d: pd.DataFrame) -> dict:
    out = {}
    for p, gg in d.groupby("period"):
        pnl, cap, sh = gg.pnl_usd.sum(), gg.stake_usd.sum(), gg.sh.sum()
        out[p] = dict(bets=int(len(gg)), markets=int(gg.condition_id.nunique()),
                      dates=int(pd.Series(H.date_of(gg["T"])).nunique()),
                      shares=float(sh), stake_usd=float(cap), fee_usd=float(gg.fee_usd.sum()),
                      payout_usd=float(gg.payout.sum()), pnl_usd=float(pnl),
                      roi=float(pnl / cap), cents_per_share=float(100 * pnl / sh))
    return out


def rnd(x, nd: int = 6) -> float | None:
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return None
    v = round(float(x), nd)
    return 0.0 if v == 0 else v


def rows_of(d: pd.DataFrame) -> list[list]:
    ts = d.ts.to_numpy(np.int64)
    date = pd.to_datetime(ts, unit="s", utc=True).strftime("%Y-%m-%d")
    mdate = H.date_of(d["T"])
    out = []
    for i in range(len(d)):
        r = d.iloc[i]
        mins = (int(r["T"]) - int(r.ts)) / 60.0
        note = (f"taker bought {r['size']:.2f}sh Yes @ {r.q:.4f} at T-{mins:.0f}m "
                f"(match {mdate[i]} {pd.to_datetime(int(r['T']), unit='s', utc=True).strftime('%H:%M')}Z); "
                f"we are assumed the maker on min(size,200)={r.sh:.2f}sh; "
                f"{'NOT COMPLETED -> No pays 1' if r.y_yes < 1 else 'completed -> No pays 0'}; "
                f"rebate {100 * r.rebate_sh:.4f}c/sh @fee_rate {r.fee_rate:.3f}; "
                f"assumed maker fill, not an observed quote of ours")
        out.append([i + 1, r.period, date[i], "tennis", str(r.league), str(r.event_slug),
                    str(r.market_slug), f"No - maker fill (sold Yes @ {r.q:.4f})", int(ts[i]),
                    rnd(r.entry_price), rnd(r.stake_usd), rnd(r.fee_usd), "resolution",
                    int(r.closed_ts), rnd(r.exit_price), rnd(r.payout), rnd(r.pnl_usd),
                    rnd(r.roi_row), note])
    return out


def main() -> None:
    d = pd.concat([primary_fills("dev"), primary_fills("holdout")], ignore_index=True)
    d = d.sort_values(["ts", "condition_id", "tx"], kind="stable").reset_index(drop=True)
    full = totals(d)

    # ---- reproduction check against the report headline (all fills, before any sampling)
    print("\n=== reproduction check (all primary fills, before any sampling)")
    ok = True
    for p, h in REPORT_HEADLINE.items():
        t = full[p]
        d_bets, d_mk = t["bets"] - h["bets"], t["markets"] - h["markets"]
        d_pnl, d_roi = t["pnl_usd"] - h["pnl_usd"], t["roi"] - h["roi"]
        d_cap, d_sh = t["stake_usd"] - h["capital_usd"], t["shares"] - h["shares"]
        ok &= (d_bets == 0 and d_mk == 0 and abs(d_pnl) < 1e-6 and abs(d_roi) < 1e-9
               and abs(d_cap) < 1e-6 and abs(d_sh) < 1e-6)
        print(f"  {p:8s} fills {t['bets']:,} vs {h['bets']:,} (d={d_bets}); "
              f"markets {t['markets']} vs {h['markets']} (d={d_mk}); dates {t['dates']} vs {h['dates']}; "
              f"shares {t['shares']:,.3f} vs {h['shares']:,.3f} (d={d_sh:+.2e}); "
              f"capital ${t['stake_usd']:,.4f} vs ${h['capital_usd']:,.4f} (d=${d_cap:+.2e}); "
              f"P&L ${t['pnl_usd']:,.4f} vs ${h['pnl_usd']:,.4f} (d=${d_pnl:+.2e}); "
              f"ROI {100 * t['roi']:.4f}% vs {100 * h['roi']:.4f}% (d={100 * d_roi:+.2e}pp)")
    print(f"  reproduces headline: {ok}")
    assert ok, "ledger does not reproduce the report headline"

    n_total = len(d)
    truncated = n_total > MAX_ROWS
    if truncated:                                     # not reached: 2,208 fills in total
        hold = d.index[d.period == "holdout"].to_numpy()
        dev = d.index[d.period == "dev"].to_numpy()
        keep = np.random.default_rng(SEED).choice(dev, size=MAX_ROWS - len(hold), replace=False)
        rows_df = d.loc[np.sort(np.concatenate([hold, keep]))].reset_index(drop=True)
    else:
        rows_df = d
    shown = totals(rows_df)

    day = pd.to_datetime(d.ts, unit="s", utc=True).dt.strftime("%Y-%m-%d")
    span = {p: (g.min(), g.max()) for p, g in day.groupby(d.period)}

    doc = {
        "slug": SLUG,
        "title": "Tennis \"Completed Match?\": sell Yes to certainty buyers",
        "group": "Thorp hypotheses",
        "sport": "tennis",
        "verdict": "DEAD",
        "hypothesis": (
            "\"Yes, completed\" trades like a free 97-99c coupon, but 6.5% of ATP and 5.2% of WTA completion "
            "markets since 2026-05-09 resolved No (walkover, retirement, cancellation, or not finished in "
            "time all resolve No), so yield-seekers who buy that coupon should be overpaying and a maker who "
            "sells Yes to them - equivalently, bids No - should collect the certainty premium."),
        "mechanism": (
            "The market type is new (first markets 2026-05-09), thinly made, and has no arbitrage anchor: "
            "unlike a moneyline there is no correlated book to price it against, so nothing forces the quote "
            "to the true completion rate. The claim was that retail treats a 98c Yes as a savings account and "
            "bids it up past fair, leaving the seller a structural premium. It is not there: the takers who "
            "buy pregame Yes are the informed side, because they buy mostly below 97c where completion is "
            "UNDERpriced, and in the matches that end up not completed they stop buying once the price has "
            "already collapsed (mean Yes price paid 0.43 in DEV, 0.78 in the holdout)."),
        "entry_rule": (
            "Universe: every `tennis_completed_match` market in C.universe() with league in {atp, wta} and "
            "game_start_ts >= 2026-05-09 (7,401 markets: 2,700 DEV, 4,701 holdout); no volume filter on the "
            "analysed set. T = game_start_ts, the scheduled start. Fetch-budget shortcut: Data API tapes for "
            "[T-48h, T) were pulled for a seeded random sample of the volume > 0 markets (all 1,098 DEV, 882 "
            "of 1,683 holdout) plus 20 volume = 0 controls, all of which were empty; a zero-volume market has "
            "no fills and so cannot enter a statistic defined over markets with >= 1 fill. Fills are "
            "normalised to \"taker acquired side s at price q\" using the token id (authoritative; "
            "outcomeIndex disagreed on 1 row of 4,189), and rows of one taker order at one price level (same "
            "tx, wallet, side, price) are merged into one fill. PRIMARY (pre-registered, frozen before the "
            "holdout run): every fill acquiring Yes with ts in [T-6h, T) is a trade - we are assumed to be "
            "the maker on the other side, selling Yes at exactly the price q the taker paid, for "
            "min(print size, 200) shares. Selling Yes at q is the same position as buying No at 1 - q, which "
            "is this ledger's entry_price and the capital at risk. One row per fill; fills are NOT aggregated "
            "per market. No queue and no size/time priority are modelled, which flatters us (we fill 100% of "
            "the flow we want). No quote discipline and no price floor are modelled, which does the opposite: "
            "a real maker resting at 97-99c would never have sold Yes at 0.43, 0.05 or 0.01, and those "
            "unfloored fills are where nearly all of the loss is (see caveats). So the primary is not a "
            "quotable strategy in either direction - it is the P&L of \"the counterparty of every pregame Yes "
            "purchase\"."),
        "exit_rule": (
            "Held to resolution. The No side we hold pays 1 if the match was not played to completion "
            "through normal play (walkover, retirement, cancellation, tie, or not finished within 7 days - "
            "14 in later markets) and 0 if it completed; a void would pay 0.5 but the sample contains none. "
            "exit_ts is the market's close (closed_ts from the universe), exit_price is 1 - y_yes and payout "
            "is shares x (1 - y_yes). There is no intermediate exit, no stop and no hedge: the position is "
            "opened pregame and settled by the resolver, so exit_price is 1 or 0 on every row."),
        "cost_model": (
            "Makers pay no trading fee on Polymarket, so fee_usd is never positive. A maker rebate of 15% of "
            "the taker fee on our own fill is credited: rebate = 0.15 x fee_rate x q x (1 - q) per share, "
            "using each market's own fee_rate, which is why fee_usd is negative. At these prices it is worth "
            "less than 0.1c per share and changes nothing (the feeSchedule rebateRate was 0.25 for May-early "
            "July markets and 0.15 later; either way < 0.1c). No slippage is modelled on entry because we are "
            "the passive side and fill at the taker's own print price. The report's +1c sensitivity row "
            "prices every fill 1c worse for us (q - 0.01) and is NOT what these rows contain: it turns DEV "
            "-$76 into -$142 and the holdout -$5,113 into -$6,735."),
        "periods": {
            "dev": f"{span['dev'][0]}..{span['dev'][1]} (entry dates; scheduled match start in "
                   f"[2026-05-09, 2026-07-01), 37 match dates, 129 markets with a Yes fill)",
            "holdout": f"{span['holdout'][0]}..{span['holdout'][1]} (entry dates; scheduled match start in "
                       f"[2026-07-01, 2026-09-18], 66 match dates, 480 markets with a Yes fill)",
        },
        "headline": {
            "dev": {"bets": REPORT_HEADLINE["dev"]["bets"],
                    "roi": round(REPORT_HEADLINE["dev"]["roi"], 6),
                    "ci_lo": round(REPORT_HEADLINE["dev"]["ci_lo"], 6),
                    "ci_hi": round(REPORT_HEADLINE["dev"]["ci_hi"], 6),
                    "pnl_usd": round(REPORT_HEADLINE["dev"]["pnl_usd"], 2),
                    "markets": REPORT_HEADLINE["dev"]["markets"],
                    "capital_usd": round(REPORT_HEADLINE["dev"]["capital_usd"], 2),
                    "primary_stat_c_per_share_equal_weight": round(REPORT_HEADLINE["dev"]["ew_c_share"], 2),
                    "primary_stat_ci": [round(REPORT_HEADLINE["dev"]["ew_lo"], 2),
                                        round(REPORT_HEADLINE["dev"]["ew_hi"], 2)]},
            "holdout": {"bets": REPORT_HEADLINE["holdout"]["bets"],
                        "roi": round(REPORT_HEADLINE["holdout"]["roi"], 6),
                        "ci_lo": round(REPORT_HEADLINE["holdout"]["ci_lo"], 6),
                        "ci_hi": round(REPORT_HEADLINE["holdout"]["ci_hi"], 6),
                        "pnl_usd": round(REPORT_HEADLINE["holdout"]["pnl_usd"], 2),
                        "markets": REPORT_HEADLINE["holdout"]["markets"],
                        "capital_usd": round(REPORT_HEADLINE["holdout"]["capital_usd"], 2),
                        "primary_stat_c_per_share_equal_weight": round(REPORT_HEADLINE["holdout"]["ew_c_share"], 2),
                        "primary_stat_ci": [round(REPORT_HEADLINE["holdout"]["ew_lo"], 2),
                                            round(REPORT_HEADLINE["holdout"]["ew_hi"], 2)]},
        },
        "review": (
            "Three adversarial passes were run on the hypothesis caches (execution, statistics/robustness, "
            "look-ahead/selection) and none of them rescued it. Execution: nearly all of the loss is in "
            "Yes-acquiring fills below 90c that a disciplined maker resting at 97-99c would never have sold "
            "into, and one wallet (0xcf218ae4...) bought Yes at 0.98 in 19 holdout markets and dumped the "
            "same size minutes later at 0.03-0.06, which looks like wash trading or wallet transfers; a "
            "fixed-ask reconstruction at 0.95-0.99 was run instead and is at best break-even. Statistics: the "
            "negative sign survives trimming the best and worst 1% and 5% of markets, dropping the worst 5% "
            "of match dates, and every price floor, and the only positive slice (q >= 0.97, holdout +2.19c "
            "[-0.16, +5.49]) loses even its point significance when the single best market is dropped (+0.82c "
            "[-1.19, +3.67]); it is also one of ~12 slices, so it is what chance produces. Look-ahead: no "
            "fill in the window post-dates the market close, only 5 DEV and 4 holdout markets close before "
            "T+30 min (dropping them moves the primary to -7.19c and -4.75c), and the sample filter uses only "
            "pre-decision information. Conclusion: the certainty premium does not exist - the pregame Yes "
            "buyer is the informed side - and the verdict is DEAD in both periods."),
        "caveats": [
            "THE FILL MODEL IS DELIBERATELY GENEROUS AND STILL LOSES. Every row assumes we were the maker on "
            "a taker's Yes purchase, at whatever price that taker paid, with no queue and no minimum quote "
            "price. So the primary really measures \"the counterparty of all pregame Yes buying\", not a "
            "strategy anyone could quote. That is exactly the population the hypothesis claimed was "
            "mispricing, and it is the population that wins.",
            "exit_kind is \"resolution\" on every row and exit_price is 1 or 0, but the ENTRY is an assumed "
            "maker fill, not an observed quote of ours. No order was ever resting; the row exists because a "
            "taker bought Yes and the study took the other side.",
            "Losses are not spread evenly: in the holdout, fills below 0.97 lose -8.34c/share equal-weight "
            "while the 0.97-0.985 zone the hypothesis is actually about is +1.08c [-1.15, +4.37] and "
            "q >= 0.97 is +2.19c [-0.16, +5.49] - positive point estimates, CIs crossing zero, and only ~$36 "
            "of capital a day across every ATP and WTA match. Treat the aggregate ROI (-44.5% of capital in "
            "the holdout) as the primary's verdict, not as the economics of a 98c-only seller.",
            "ROI is on CAPITAL (the No collateral, 1 - q per share), which is tiny when q is near 1, so the "
            "percentage swings violently: -5.6% in DEV and -44.5% in the holdout come from -$76 and -$5,113 "
            "of P&L on $1,353 and $11,494 of collateral. The report's verdict statistic is the equal-weight "
            "c/share over markets (DEV -6.26 [-11.55, -0.82], holdout -4.45 [-7.70, -1.70]), which this file "
            "carries in \"headline\" as primary_stat_c_per_share_equal_weight but which cannot be recovered "
            "by summing rows - it is a mean over markets, clustered by match date.",
            "Each market is a Bernoulli ~5% No event, so a handful of walkouts moves everything: DEV has 8 "
            "not-completed markets of 129 and the holdout 22 of 480. The three worst holdout rows alone are "
            "12% of the -$5,113.",
            "THE HOLDOUT P&L IS DOMINATED BY A HANDFUL OF SUB-5c PRINTS. 29 of the 2,006 holdout rows (1.4%) "
            "are fills where a taker acquired Yes at 0.01-0.05 in markets whose other prints put Yes at "
            "0.94-0.99; the study makes us the maker on them, i.e. we sell Yes at 1-5c and post 95-99c of "
            "collateral, and all 29 matches completed. Those 29 rows are -$3,818, or 75% of the -$5,113; rows "
            "below 0.50 (50 rows, 2.5%) are -$4,401, or 86%. Two entry dates, 2026-09-04 and 2026-09-05, are "
            "76% of the holdout loss. Whether those prints are real quotes that someone actually posted or an "
            "artifact of the Data API tape, no maker would rest a Yes offer at 1c, so the headline "
            "-$5,113 / -44.5% should be read as the cost of the no-price-floor assumption, not as a strategy "
            "result. With the single filter q >= 0.05 and nothing else changed, the holdout becomes -$1,295 "
            "and -17.3% on $7,478 of capital, and q >= 0.97 becomes +$135 and +8.9% (still not significant). "
            "The verdict stays DEAD either way: the equal-weight statistic the report uses is not "
            "size-weighted and is negative in both periods without these rows.",
            "Sampling: the holdout rows come from 882 of the 1,683 volume > 0 holdout markets (DEV is "
            "complete, all 1,098). Totals in this file are the sample's, exactly as the report states them; "
            "scaled to the whole population the primary loses about $120 a day. Rows are NOT truncated - "
            "every one of the 2,208 primary fills is in this file.",
            "Variants in the report but NOT exported here: +1c sensitivity; (a) taker buys No near the start "
            "(25 DEV and 114 holdout signals but 0 and 1 fills - untestable); (b) the [T-24h, T) window (DEV "
            "-4.20c, holdout -4.43c); (c) splits by tour and by fill price; (d) calibration of the pregame "
            "Yes price; (e) ITF, not run; (x) the post-hoc reverse rule that buys Yes after a 0.90-0.97 print "
            "(holdout 134 bets, +0.53c/bet [-3.78, +4.27] - noise).",
            "Dump prints contaminate the tape below 0.5 (the 0xcf218ae4... wallet above). They do not enter "
            "these rows - the dumps acquire No, and only that wallet's 0.98 Yes purchases are trades here - "
            "but they do distort the report's calibration table.",
            "Market history is short: the type exists only since 2026-05-09, so DEV is 129 active markets "
            "over 37 match dates and is 10-30x thinner than the holdout.",
        ],
        "report_path": f"reports/research/{SLUG}.md",
        "code_path": f"pmsports/research/h_{SLUG}.py",
        "truncated": bool(truncated),
        "n_total_trades": int(n_total),
        "totals_full": full,
        "totals_rows": shown,
        "columns": COLUMNS,
        "rows": rows_of(rows_df),
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, separators=(",", ":")))
    print(f"\nwrote {OUT}  ({OUT.stat().st_size / 1e6:.2f} MB, {len(doc['rows']):,} rows of {n_total:,})")
    for p in ("dev", "holdout"):
        t = shown[p]
        print(f"  {p:8s} {t['bets']:,} fills, {t['markets']} markets, stake ${t['stake_usd']:,.2f}, "
              f"P&L ${t['pnl_usd']:,.2f}, ROI {100 * t['roi']:.2f}%, {t['cents_per_share']:+.2f}c/share")


if __name__ == "__main__":
    main()
