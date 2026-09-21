"""Per-trade ledger export for hypothesis `soccer_under_btts_no` (see ../LEDGER_SPEC.md).

The rows are the ACTUAL maker fills the backtest made: they are read straight out of
`data/research/h_soccer_under_btts_no/fills_{dev,holdout}.parquet`, which `h_soccer_under_btts_no.run()`
writes from `maker_side(t, over=True)` -- the frozen PRIMARY rule. Nothing is re-simulated here; the only
things this module adds are the event/market slugs and the resolution timestamp, joined from
`sample.parquet`, and the LEDGER_SPEC column algebra.

A "trade" is one modelled maker fill against one pregame retail taker ticket that bought Over/Yes.
Selling Over/Yes at q is the same position as buying Under/No at 1 - q, and that position IS held to
resolution, so `exit_kind` is "resolution" on every row (`exit_ts` = the market's `closed_ts`,
`exit_price` = 1 - y_over, i.e. 1 if Under/No won). What is modelled, not observed, is the FILL: the
pre-registered rule assumes we are at the front of the queue on every Over/Yes print and get
min(size, 100) shares at the taker's own price. The `note` on every row says so.

Accounting per row (matches `maker_side()`'s `pnl = q - y + REBATE*fee_rate*q*(1-q)` and `notional = 1-q`):
    entry_price = 1 - q                       (the Under/No share we end up holding)
    stake_usd   = sh * (1 - q)                = sh * notional
    fee_usd     = -sh * 0.15 * fee_rate * q * (1-q)   (makers pay no fee; the rebate is a credit -> negative)
    exit_price  = 1 - y_over ,  payout = sh * (1 - y_over)
    pnl_usd     = payout - stake_usd - fee_usd = sh * pnl
    roi         = pnl_usd / stake_usd = pnl / notional

Row cap: 190,954 fills is far above the 20,000-row cap and the HOLDOUT alone is 92,834, so LEDGER_SPEC's
"keep every holdout trade" is impossible. Instead each period gets half the cap and is sampled WITHIN EVERY
MARKET (seeded, proportional, at least one fill per market), so all 1,940 markets of the study stay visible
in the explorer and the rows of any market are an unbiased sample of that market's fills.
`totals_full` holds the unsampled per-period totals that reproduce the report.

Run:
    PYTHONPATH=. systemd-run --user --scope -q -p MemoryMax=5G -p MemorySwapMax=0 \
        .venv/bin/python -m pmsports.research.ledger_soccer_under_btts_no
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from pmsports.research import common as C

SLUG = "soccer_under_btts_no"
SRC = C.RESEARCH / f"h_{SLUG}"
OUT = C.RESEARCH / "ledgers" / f"{SLUG}.json"
MAX_ROWS = 20_000
SEED = 0

COLUMNS = ["id", "period", "date", "sport", "league", "event", "market", "side", "entry_ts",
           "entry_price", "stake_usd", "fee_usd", "exit_kind", "exit_ts", "exit_price", "payout",
           "pnl_usd", "roi", "note"]

# Headline of the PRIMARY rule, reports/research/soccer_under_btts_no.md -> "Primary results",
# rows "DEV pooled" and "HOLDOUT pooled"; cross-checked against
# data/research/h_soccer_under_btts_no/results_{dev,holdout}.json -> "primary".
# NOTE: the report's headline ROI is EQUAL-WEIGHT BY MARKET (sum_m pnl_m / sum_m notional_m over the
# per-market share-weighted means), not dollar-weighted. The dollar-weighted number is the report's
# variant (e) and it has the OPPOSITE SIGN in both periods, because a handful of huge World Cup markets
# carry 44% (DEV) / 62% (HOLDOUT) of all captured shares. Both are recorded below.
REPORT_HEADLINE = {
    "dev": {"bets": 98120, "markets": 949, "matches": 890, "roi": 0.0374057, "ci_lo": -0.0368602,
            "ci_hi": 0.1100587, "c_per_share": 1.7147, "captured_usd": 1644082.40, "pnl_usd": -7645.08,
            "roi_plus1c": 0.0152587, "roi_share_weighted": -0.0046501},
    "holdout": {"bets": 92834, "markets": 991, "matches": 787, "roi": -0.0232369, "ci_lo": -0.0965423,
                "ci_hi": 0.0499262, "c_per_share": -1.0363, "captured_usd": 1831235.20, "pnl_usd": 53227.46,
                "roi_plus1c": -0.0446583, "roi_share_weighted": 0.0290664},
}
REBATE = 0.15


# ----------------------------------------------------------------------------- build

def build() -> pd.DataFrame:
    """The actual PRIMARY maker fills the backtest wrote, plus the LEDGER_SPEC columns."""
    s = pd.read_parquet(SRC / "sample.parquet").set_index("condition_id")
    parts = []
    for period in ("dev", "holdout"):
        d = pd.read_parquet(SRC / f"fills_{period}.parquet")
        assert (d.split == period).all() and d.is_over.all()          # primary = taker bought Over/Yes
        d["period"] = period
        parts.append(d)
    d = pd.concat(parts, ignore_index=True)
    d = d.join(s[["event_slug", "market_slug", "closed_ts", "o0", "o1", "game_start_ts"]], on="condition_id")
    assert d.closed_ts.notna().all() and (d.closed_ts >= d.ts).all()

    # sanity: the frozen rule, re-asserted on the cached fills
    assert np.allclose(d.sh, np.minimum(d["size"], 100.0))
    assert np.allclose(d.notional, 1.0 - d.q)
    assert (d.ts >= d["T"] - 86400).all() and (d.ts < d["T"]).all()   # strictly pregame, 24 h window
    assert d.y.isin([0.0, 1.0]).all()                                 # no voids / 0.5 payouts in this sample

    d["over_name"] = np.where(d.ov == 0, d.o0, d.o1)                  # "Over" or "Yes", read from the market
    d["held_name"] = np.where(d.ov == 0, d.o1, d.o0)                  # "Under" or "No"
    d["reb_per_share"] = REBATE * d.fee_rate * d.q * (1.0 - d.q)
    d["entry_price"] = 1.0 - d.q
    d["stake_usd"] = d.sh * d.notional
    d["fee_usd"] = -(d.sh * d.reb_per_share)
    d["exit_price"] = 1.0 - d.y
    d["payout"] = d.sh * (1.0 - d.y)
    d["pnl_usd"] = d.sh * d.pnl
    d["roi_row"] = d.pnl / d.notional
    # identity check on every row: payout - stake - fee == pnl, and it equals the study's sh * pnl
    assert np.allclose(d.payout - d.stake_usd - d.fee_usd, d.pnl_usd, atol=1e-9)

    return d.sort_values(["ts", "condition_id", "tx"], kind="stable").reset_index(drop=True)


# ----------------------------------------------------------------------------- statistics

def totals(d: pd.DataFrame) -> dict:
    """Per period: dollar totals from the ledger rows, and the report's equal-weight-by-market statistic
    recomputed from those same rows (per market: share-weighted mean P&L and notional per share)."""
    out = {}
    for p, g in d.groupby("period"):
        m = g.groupby("condition_id").apply(
            lambda x: pd.Series({"pnl": (x.pnl_usd.sum() / x.sh.sum()),
                                 "notl": (x.stake_usd.sum() / x.sh.sum())}), include_groups=False)
        out[p] = dict(
            bets=int(len(g)), markets=int(g.condition_id.nunique()), matches=int(g.match.nunique()),
            shares=float(g.sh.sum()), stake_usd=float(g.stake_usd.sum()), fee_usd=float(g.fee_usd.sum()),
            payout_usd=float(g.payout.sum()), pnl_usd=float(g.pnl_usd.sum()),
            roi_share_weighted=float(g.pnl_usd.sum() / g.stake_usd.sum()),
            roi_equal_weight_by_market=float(m.pnl.sum() / m.notl.sum()),
            c_per_share_equal_weight=float(100 * m.pnl.mean()),
            c_per_share_share_weighted=float(100 * g.pnl_usd.sum() / g.sh.sum()))
    return out


def rnd(x, nd: int = 6) -> float:
    v = round(float(x), nd)
    return 0.0 if v == 0 else v          # avoid -0.0 in the JSON


def rows_of(d: pd.DataFrame) -> list[list]:
    ts = d.ts.to_numpy(np.int64)
    date = pd.to_datetime(ts, unit="s", utc=True).strftime("%Y-%m-%d")
    out = []
    for i in range(len(d)):
        r = d.iloc[i]
        kind = "Under 2.5" if r.mtype == "ou25" else "BTTS No"
        side = f"{r.held_name} ({kind}) @ {r.entry_price:.3f} - maker fill, sold {r.over_name} @ {r.q:.3f}"
        note = (f"retail taker bought {r.over_name} {r['size']:.1f}sh @{r.q:.3f}, {r.hrs:.1f}h pre-KO; "
                f"we fill {r.sh:.1f}sh{' (capped at 100)' if r['size'] > 100 else ''} at the taker's price, "
                f"FRONT-OF-QUEUE ASSUMPTION; rebate {100 * r.reb_per_share:+.4f}c/sh @fee_rate {r.fee_rate:.3f}; "
                f"{r.over_name} resolved {r.y:.0f}")
        out.append([i + 1, r.period, date[i], "soccer", str(r.league), str(r.event_slug), str(r.market_slug),
                    side, int(ts[i]), rnd(r.entry_price), rnd(r.stake_usd), rnd(r.fee_usd), "resolution",
                    int(r.closed_ts), rnd(r.exit_price), rnd(r.payout), rnd(r.pnl_usd), rnd(r.roi_row), note])
    return out


def sample_within_markets(d: pd.DataFrame, budget: int, seed: int) -> np.ndarray:
    """Seeded proportional sample inside every market (>= 1 fill per market), total <= budget.
    Keeping every market means the explorer can show any of the study's markets; the rows of a market are
    an unbiased sample of its fills."""
    n = d.groupby("condition_id", sort=True).size()
    lo, hi = 0.0, 1.0                                     # largest rate whose row count fits the budget
    for _ in range(60):
        mid = (lo + hi) / 2
        if int(np.maximum(1, np.floor(n.to_numpy() * mid)).sum()) <= budget:
            lo = mid
        else:
            hi = mid
    k = pd.Series(np.maximum(1, np.floor(n.to_numpy() * lo)).astype(int), index=n.index)
    rng = np.random.default_rng(seed)
    keep = []
    for cid, idx in d.groupby("condition_id", sort=True).groups.items():
        idx = np.asarray(idx)
        keep.append(idx if k[cid] >= len(idx) else rng.choice(idx, size=int(k[cid]), replace=False))
    return np.sort(np.concatenate(keep))


# ----------------------------------------------------------------------------- main

def main() -> None:
    d = build()
    full = totals(d)
    day = pd.to_datetime(d.ts, unit="s", utc=True).dt.strftime("%Y-%m-%d")
    span = {p: (g.min(), g.max()) for p, g in day.groupby(d.period)}

    # ---- reproduction check against the report headline (all fills, before any sampling)
    print("\n=== reproduction check (all fills, before any sampling)")
    ok = True
    for p, h in REPORT_HEADLINE.items():
        t = full[p]
        d_bets, d_mkt, d_match = t["bets"] - h["bets"], t["markets"] - h["markets"], t["matches"] - h["matches"]
        d_pnl = t["pnl_usd"] - h["pnl_usd"]
        d_usd = t["stake_usd"] - h["captured_usd"]
        d_roi = t["roi_equal_weight_by_market"] - h["roi"]
        d_cps = t["c_per_share_equal_weight"] - h["c_per_share"]
        d_shw = t["roi_share_weighted"] - h["roi_share_weighted"]
        ok &= (d_bets == 0 and d_mkt == 0 and d_match == 0 and abs(d_pnl) < 0.01 and abs(d_usd) < 0.01
               and abs(d_roi) < 5e-7 and abs(d_cps) < 5e-4 and abs(d_shw) < 5e-7)
        print(f"  {p:8s} fills {t['bets']:,} vs {h['bets']:,} (d={d_bets}); markets {t['markets']} vs "
              f"{h['markets']} (d={d_mkt}); matches {t['matches']} vs {h['matches']} (d={d_match})")
        print(f"           captured ${t['stake_usd']:,.2f} vs ${h['captured_usd']:,.2f} (d=${d_usd:+.4f}); "
              f"P&L ${t['pnl_usd']:,.2f} vs ${h['pnl_usd']:,.2f} (d=${d_pnl:+.4f})")
        print(f"           ROI equal-weight {100 * t['roi_equal_weight_by_market']:+.4f}% vs {100 * h['roi']:+.4f}% "
              f"(d={100 * d_roi:+.6f}pp); c/sh {t['c_per_share_equal_weight']:+.4f} vs {h['c_per_share']:+.4f} "
              f"(d={d_cps:+.5f})")
        print(f"           ROI share-weighted {100 * t['roi_share_weighted']:+.4f}% vs report variant (e) "
              f"{100 * h['roi_share_weighted']:+.4f}% (d={100 * d_shw:+.6f}pp)")
    print(f"  reproduces headline: {ok}")

    # ---- row cap (see the module docstring): half the budget per period, whole markets only
    n_total = len(d)
    truncated = n_total > MAX_ROWS
    if truncated:
        sel = [sample_within_markets(g, MAX_ROWS // 2, SEED) for _, g in d.groupby("period")]
        rows_df = d.loc[np.sort(np.concatenate(sel))].reset_index(drop=True)
    else:
        rows_df = d
    shown = totals(rows_df)

    doc = {
        "slug": SLUG,
        "title": "Selling Over 2.5 and BTTS-Yes to retail pregame",
        "group": "Thorp hypotheses",
        "sport": "soccer",
        "verdict": "DEAD",
        "hypothesis": (
            "Retail soccer bettors root for goals, so pregame taker flow in Over/Under 2.5 and "
            "both-teams-to-score piles onto Over and Yes. If that one-sided flow pushes the Over/Yes price "
            "above the true probability, a maker who sits on the other side - selling Over/Yes, i.e. holding "
            "Under/No - collects the premium from the retail taker clicking Over/Yes."),
        "mechanism": (
            "In soccer moneylines the neg-risk conversion link lets arbitrageurs mint and sell the whole "
            "outcome set, so a Yes premium there is arbitraged away. O/U 2.5 and BTTS are plain binary "
            "markets with no such link and thinner market making, so a structural Over/Yes premium could in "
            "principle survive there. The flow bias is real and large (61% of pregame tickets in DEV, 73% in "
            "the holdout buy Over/Yes, and it grew under the 5% fee), but the makers absorbing it simply "
            "lean their quotes: the implied Over/Yes price tracks the realised hit rate to within ~1c, so "
            "there is no premium left to collect."),
        "entry_rule": (
            "Universe: Polymarket soccer markets whose slug is exactly '<match>-total-2pt5' (market_type "
            "totals) or '<match>-btts' (both_teams_to_score), with <match> = '<league>-<a>-<b>-<yyyy-mm-dd>' "
            "in 16 top leagues (second divisions excluded), kickoff from 2025-10-01 on, no volume or outcome "
            "filter. Seeded random sample of 1,000 markets per period. T = min(the market's game_start_ts, "
            "the same match's moneyline game_start_ts), which defuses the September-2025 markets that carry "
            "a start 4 h after the real kickoff. Tape = Data API taker fills in [T-24h, T), one ticket per "
            "(tx, side acquired), side read from the token id, a taker SELL counted as acquiring the other "
            "token at 1 - price. ENTRY: for every pregame taker ticket that acquires Over (O/U) or Yes "
            "(BTTS) at price q, we are the resting maker on the other side and are filled min(size, 100) "
            "shares at q. That is a passive sale of Over/Yes at q = a purchase of Under/No at 1 - q, which "
            "is the entry_price and the capital at risk on the row. No slippage is added on entry because we "
            "are the passive side - but the fill itself assumes FRONT-OF-QUEUE priority on every Over/Yes "
            "print, which is optimistic (see caveats)."),
        "exit_rule": (
            "Held to resolution. Every row is a real position carried to settlement: exit_ts is the market's "
            "closed_ts, exit_price is 1 - y_over (1 if Under/No won, 0 if Over/Yes won) and payout = shares x "
            "exit_price. No intra-window exit, no markout, no flattening. One row per taker ticket filled "
            "(fills are NOT aggregated per market); a market typically contributes 50-200 rows, and the "
            "report's headline is the equal-weight mean across markets of each market's share-weighted P&L "
            "per share, so many small rows in one market count as much as one large row in another."),
        "cost_model": (
            "Makers pay no trading fee on Polymarket, so fee_usd is never positive. A maker rebate of 15% of "
            "the taker fee on our own fill is credited: 0.15 x fee_rate x q x (1-q) per share at the "
            "market's own fee_rate (DEV: 0 on 707 markets, 0.03 on 291, 0.0175 on 2; HOLDOUT: 0.05 on 880, "
            "0.03 on 120), which is why fee_usd is negative or zero. The rebate is an approximation: without "
            "it the holdout headline is -1.21c / -2.71% instead of -1.04c / -2.32%. The report's main "
            "sensitivity makes every fill 1c worse (P&L -0.01, notional +0.01), giving DEV +1.53% and "
            "HOLDOUT -4.47%; those shifted numbers are NOT applied to these rows."),
        "periods": {
            "dev": f"{span['dev'][0]}..{span['dev'][1]} (entries; kickoff < 2026-07-01, universe from 2025-10-01)",
            "holdout": f"{span['holdout'][0]}..{span['holdout'][1]} (entries; kickoff >= 2026-07-01)",
        },
        "headline": {
            "dev": {k: REPORT_HEADLINE["dev"][k] for k in
                    ("bets", "roi", "ci_lo", "ci_hi", "pnl_usd", "markets", "matches", "captured_usd",
                     "c_per_share", "roi_plus1c", "roi_share_weighted")},
            "holdout": {k: REPORT_HEADLINE["holdout"][k] for k in
                        ("bets", "roi", "ci_lo", "ci_hi", "pnl_usd", "markets", "matches", "captured_usd",
                         "c_per_share", "roi_plus1c", "roi_share_weighted")},
            "note": (
                "'bets' is the number of maker fills; 'markets'/'matches' are the clustering units. 'roi' and "
                "'c_per_share' are the report's headline statistic, which is EQUAL-WEIGHT BY MARKET (every "
                "market counts once, CI clustered by match), so it does NOT equal pnl_usd / captured_usd. "
                "'pnl_usd' and 'roi_share_weighted' are the dollar totals of exactly these ledger rows "
                "(= the report's variant (e), share-weighted). The two weightings have opposite signs in both "
                "periods because ~24 (DEV) and ~14 (HOLDOUT) World Cup markets hold 44% / 62% of all captured "
                "shares; the verdict rests on the equal-weight number, whose CI crosses 0 in DEV and is "
                "negative in the holdout."),
        },
        "review": (
            "Three adversarial passes (execution/fees/capacity, look-ahead/selection, stats/robustness) all "
            "supported the DEAD verdict. Execution: an independent re-implementation reproduced the primary "
            "exactly (949 / 991 markets, +3.74% / -2.32%, 0 voids); the raw tape is clean (0 multi-row "
            "transactions, 0 duplicate prints, 0 prints at or after T, 96% of prices on the cent grid); the "
            "result barely moves with the rebate (0% to 100% of the taker fee: holdout -2.71% to -0.15%) or "
            "the 100-share cap (K=20/500: -2.36% / -2.25%), but if Data API prices were fee-inclusive the "
            "holdout would be -4.88%, and 20-22% of tickets are larger than the cap, so at a 250 or 1,000 "
            "share cap the dollar-weighted holdout is -5.7% / -5.3%. Look-ahead: the median drift between "
            "the [T-60m, T-20m] price and the last pregame prints is 1c, with 1 market over 8c in DEV and 0 "
            "in the holdout, so no in-play information leaks in; dropping the last 10 / 30 / 60 minutes "
            "leaves DEV +3.8 / +3.6 / +4.4% and the holdout -2.7 / -3.3 / -3.6%, i.e. the holdout gets "
            "worse. Stats: the sign depends entirely on the weighting (equal-weight +3.74% DEV vs "
            "share-weighted -0.47%; holdout -2.32% vs +2.91%), the dollar P&L is a handful of World Cup "
            "matches (top DEV match +$128k, worst -$71k; top holdout match +$269k, worst -$158k), dropping "
            "the top 1% of matches takes DEV to +1.3% and the holdout to -4.7%, leave-one-league-out spans "
            "+2.0 to +5.8 in DEV and -4.7 to -0.8 in the holdout, and re-clustering by date or by "
            "league-week changes nothing. No reviewer found an error in the implementation; they found that "
            "the effect is not there."),
        "caveats": [
            "The FILL MODEL IS OPTIMISTIC, not the exit: we assume front-of-queue priority on every Over/Yes "
            "print and take min(size, 100) shares at the taker's price. The back-of-queue stress test (keep "
            "a fill only if a later same-side ticket trades through our price within 60 s) keeps only 4-8% "
            "of the shares and is negative in both periods: DEV -1.46c / -3.16%, HOLDOUT -1.94c / -4.33%. "
            "Every row's exit, by contrast, is a genuine resolution payout.",
            "HEADLINE WEIGHTING: the report's headline ROI is equal-weight by market, so summing the dollar "
            "columns of this ledger gives a DIFFERENT NUMBER AND SIGN (DEV -$7,645 / -0.47%, HOLDOUT "
            "+$53,227 / +2.91%). Both are in the report - the dollar version is variant (e) - and both have "
            "CIs that swamp the point estimate ([-25.1, +23.4] and [-39.5, +43.1] share-weighted). The "
            "dollar totals are dominated by World Cup markets: 44% (DEV) and 62% (HOLDOUT) of all captured "
            "shares come from 24 and 14 markets.",
            f"TRUNCATED: {n_total:,} fills in total (DEV {full['dev']['bets']:,}, HOLDOUT "
            f"{full['holdout']['bets']:,}), capped at {MAX_ROWS:,} rows. LEDGER_SPEC's 'keep every holdout "
            f"trade' is impossible here, so each period gets half the cap and is sampled WITHIN EVERY MARKET "
            f"(seeded numpy default_rng({SEED}), proportional, at least one fill per market): "
            f"{shown['dev']['bets']:,} of {full['dev']['bets']:,} DEV fills across all "
            f"{shown['dev']['markets']} DEV markets, and {shown['holdout']['bets']:,} of "
            f"{full['holdout']['bets']:,} HOLDOUT fills across all {shown['holdout']['markets']} HOLDOUT "
            "markets. Every market of the study is therefore still visible, but the dollar columns of the "
            "rows in this file sum to roughly a tenth of the period totals and a single market's rows are a "
            "sample of its fills, not all of them. 'totals_full' are the unsampled totals that reproduce the "
            "report; 'totals_rows' are the totals of the rows actually in this file.",
            "POWER: each market settles 0/1, so the 95% CI half-width is about +/-3.3c per share with "
            "800-900 matches per period. The data rules out an Over/Yes premium of 3c or more; it cannot "
            "rule out one below about 2c.",
            "The pre-registered symmetric control (maker sells Under/No instead) is the key comparison and "
            "it reversed out of sample: DEV +0.06c / +0.11% for the control against +1.71c / +3.74% for the "
            "primary, but HOLDOUT +2.38c / +4.35% for the control against -1.04c / -2.32%. That is what "
            "noise around a fair price looks like, not an Over/Yes premium.",
            "Variants not exported (all in the report, none drives the verdict): (a) taker buys Under/No at "
            "the first print after T-10min with the taker fee, DEV +4.82% / HOLDOUT -4.92% over 558 / 585 "
            "bets (-6.93% in the holdout with 1c slippage); (b) the maker-sells-Under/No control; (d) "
            "tickets under / over $100 notional; (e) share-weighted; plus the +1c sensitivity and the "
            "back-of-queue stress. The calibration variant (c) is the cleanest statement of the result: "
            "median implied Over/Yes price vs hit rate is -1.24c [-4.41, +1.79] in DEV and +1.15c [-2.04, "
            "+4.44] in the holdout, and the BTTS-Yes overpricing seen in DEV (-3.68c) became +0.80c out of "
            "sample.",
            "CAPACITY (for context, since the edge is absent): the optimistic fill model captures a mean of "
            "$1,644 (DEV) and $1,831 (HOLDOUT) of notional per market over 24 h, with about 26-31 such "
            "markets a day, i.e. roughly $43-57k of notional a day. At the holdout ROI that capacity loses "
            "money.",
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
    print(f"\nwrote {OUT}  ({OUT.stat().st_size / 1e6:.1f} MB, {len(doc['rows']):,} rows of {n_total:,})")
    for p in ("dev", "holdout"):
        t, f = shown[p], full[p]
        print(f"  rows in file {p:8s}: {t['bets']:,} fills in {t['markets']} markets "
              f"(of {f['bets']:,} in {f['markets']}), stake ${t['stake_usd']:,.0f}, "
              f"P&L ${t['pnl_usd']:,.2f}, equal-weight ROI {100 * t['roi_equal_weight_by_market']:+.2f}%")


if __name__ == "__main__":
    main()
