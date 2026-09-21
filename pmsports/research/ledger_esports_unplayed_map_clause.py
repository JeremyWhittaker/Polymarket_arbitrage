"""Per-trade ledger export for hypothesis `esports_unplayed_map_clause` (see pmsports/research/LEDGER_SPEC.md).

Reads the bets the backtest actually placed from
`data/research/h_esports_unplayed_map_clause/bets_primary_{dev,holdout}.parquet` (written by
`h_esports_unplayed_map_clause.run()`), joins market metadata from that study's `sample.parquet` and
resolution times from `C.universe()`, and writes `data/research/ledgers/esports_unplayed_map_clause.json`.

Nothing is re-simulated here: entry second (`te`), entry price (`price`), the market's own `fee_rate`,
the underdog payout (`y_und`) and the fillability field (`usd_at_entry`) all come straight from the
saved bets file. This module only turns those into dollars.

Dollar convention (it has to match the study's metric exactly):
  The study's headline is the CAPACITY-VALID PRIMARY: the same pre-registered rule, priced as a
  fill-or-kill $1 taker order, counted only when the entry second's underdog prints carried >= $1
  (minus USD_TOL) at or below the entry price. Its per-$1 ROI is C.taker_roi = (y - c - f)/(c + f),
  i.e. the $1 is the TOTAL outlay: shares = $1/(c+f), of which shares*c is the stake and shares*f the
  taker fee. So stake_usd + fee_usd = $1 per bet, and pnl_usd = 1 * taker_roi.
  Bets from the zero-volume fetch stratum carry the study's Horvitz-Thompson weight ht_w (1.50 in DEV);
  the study's ROI is the ht_w-weighted mean of per-bet ROI, so the ledger scales that bet's dollars by
  ht_w (a $1 ticket that stands for 1.5 population bets). Only 13 DEV rows have ht_w > 1; every HOLDOUT
  row and the other 96 DEV rows are literally $1.
  The 5 DEV + 2 HOLDOUT bets whose entry print carried less than $1 are kept as rows with
  exit_kind = "unfilled" (a $1 FOK order would not have filled): zero stake, zero fee, zero P&L.

Usage (under the project's memory cap):
  systemd-run --user --scope -q -p MemoryMax=5G -p MemorySwapMax=0 \
      .venv/bin/python -m pmsports.research.ledger_esports_unplayed_map_clause
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from pmsports.research import common as C

SLUG = "esports_unplayed_map_clause"
SRC = C.RESEARCH / f"h_{SLUG}"
OUT = C.RESEARCH / "ledgers" / f"{SLUG}.json"
STAKE = 1.0        # the capacity-valid primary's fill-or-kill order size (total outlay)
USD_TOL = 0.005    # same float tolerance the study uses: an exact $1 order prints as 0.999999

COLUMNS = ["id", "period", "date", "sport", "league", "event", "market", "side", "entry_ts", "entry_price",
           "stake_usd", "fee_usd", "exit_kind", "exit_ts", "exit_price", "payout", "pnl_usd", "roi", "note"]


def _r(x, n=6):
    if x is None:
        return None
    x = float(x)
    return None if not np.isfinite(x) else round(x, n)


def load_bets() -> pd.DataFrame:
    """The actual bets, exactly as the backtest saved them, plus market_slug / outcome names / close time."""
    s = pd.read_parquet(SRC / "sample.parquet").set_index("condition_id")
    frames = []
    for period in ("dev", "holdout"):
        b = pd.read_parquet(SRC / f"bets_primary_{period}.parquet")
        b["period"] = period
        frames.append(b)
    b = pd.concat(frames, ignore_index=True)
    assert b.entered.all(), "bets_primary_* should hold entered bets only"
    b["market_slug"] = b.condition_id.map(s.market_slug)
    b["o0"] = b.condition_id.map(s.o0)
    b["o1"] = b.condition_id.map(s.o1)
    u = C.universe(columns=["condition_id", "closed_ts"]).drop_duplicates("condition_id").set_index("condition_id")
    b["closed_ts"] = b.condition_id.map(u.closed_ts)
    del u
    return b.sort_values("te", kind="stable").reset_index(drop=True)


def build_rows(b: pd.DataFrame) -> tuple[list, dict]:
    """One row per bet the rule placed. Returns (rows, per-period totals)."""
    und = 1 - b.fav.to_numpy(int)
    side_name = np.where(und == 1, b.o1.to_numpy(object), b.o0.to_numpy(object))
    c = b.price.to_numpy(float)                                   # entry price, no slippage in the primary
    f = C.taker_fee(1.0, c, b.fee_rate.to_numpy(float))           # taker fee per share, market's own fee_rate
    y = b.y_und.to_numpy(float)
    w = b.ht_w.to_numpy(float)
    fillable = b.usd_at_entry.to_numpy(float) >= STAKE - USD_TOL

    notional = np.where(fillable, STAKE * w, 0.0)                 # total outlay = stake + fee
    shares = np.where(fillable, notional / (c + f), 0.0)
    stake = shares * c
    fee = shares * f
    payout = shares * y
    pnl = payout - stake - fee

    rel_min = (b.te.to_numpy(float) - b["T"].to_numpy(float)) / 60.0
    rows, tot = [], {}
    for i in range(len(b)):
        r = b.iloc[i]
        note = [
            f"signal: favourite printed {r.fav_p0:.3f} (>= 0.58) at t0, entry is the first underdog print >= 3 s later",
            f"entry {rel_min[i]:+.0f} min vs scheduled start" + (" (in-play)" if rel_min[i] >= 0 else " (pregame)"),
            f"entry second carried ${r.usd_at_entry:.2f} of underdog prints at <= {c[i]:.4f}",
            "map 4 never played -> 50-50 clause paid 0.5" if y[i] == 0.5 else
            ("underdog won map 4" if y[i] == 1 else "underdog lost map 4"),
        ]
        if r.series_maxg >= 5:
            note.append("BO7 series (game5/game6 listed): the clause is irrelevant here, kept as pre-registered")
        if c[i] >= 0.5:
            note.append("'underdog' entered above 0.50: absurd print in a new book, kept as pre-registered")
        if w[i] > 1:
            note.append(f"zero-volume fetch stratum: Horvitz-Thompson weight {w[i]:.2f}, dollars scaled by it")
        if not fillable[i]:
            note.append(f"NOT TAKEN in the capacity-valid primary: a $1 fill-or-kill order could not have filled "
                        f"(only ${r.usd_at_entry:.2f} printed at <= {c[i]:.4f}); it is one of the sub-$1 longshot "
                        f"fills that the literal pre-registered metric counted")
        exit_ts = None if not np.isfinite(r.closed_ts) else int(r.closed_ts)
        rows.append([
            0, r.period, pd.Timestamp(int(r.te), unit="s", tz="UTC").strftime("%Y-%m-%d"), "esports",
            str(r.league), str(r.event_slug), str(r.market_slug),
            f"{side_name[i]} (underdog, taker market buy, FOK $1)", int(r.te), _r(c[i]),
            _r(stake[i]), _r(fee[i]),
            "resolution" if fillable[i] else "unfilled",
            exit_ts if fillable[i] else None, _r(y[i]) if fillable[i] else None,
            _r(payout[i]), _r(pnl[i]), _r(pnl[i] / stake[i], 6) if fillable[i] else None,
            "; ".join(note),
        ])
    for pd_ in ("dev", "holdout"):
        m = (b.period.to_numpy() == pd_)
        mf = m & fillable
        tot[pd_] = dict(rows=int(m.sum()), filled=int(mf.sum()), unfilled=int((m & ~fillable).sum()),
                        stake=float(stake[m].sum()), fee=float(fee[m].sum()), pnl=float(pnl[m].sum()),
                        notional=float(notional[m].sum()),
                        roi_on_stake_plus_fee=float(pnl[m].sum() / (stake[m] + fee[m]).sum()),
                        roi_on_stake=float(pnl[m].sum() / stake[m].sum()))
    return rows, tot


def verify(b: pd.DataFrame, tot: dict) -> dict:
    """Re-derive the report's two headline numbers from the same rows, two independent ways."""
    verdict = json.loads((SRC / "results_verdict.json").read_text())
    out = {}
    for period in ("dev", "holdout"):
        g = b[b.period == period]
        fb = g[g.usd_at_entry >= STAKE - USD_TOL]
        # (1) straight from the study's own helper, on the fillable subset
        r = np.asarray(C.taker_roi(fb.price, fb.y_und, fb.fee_rate))
        w = fb.ht_w.to_numpy(float)
        cv_roi = float((r * w).sum() / w.sum())
        # (2) the literal pre-registered metric: every entered bet, $1 each
        rl = np.asarray(C.taker_roi(g.price, g.y_und, g.fee_rate))
        wl = g.ht_w.to_numpy(float)
        lit_roi = float((rl * wl).sum() / wl.sum())
        rep_cv = verdict["capacity_valid_primary"][period]
        rep_lit = verdict["literal_preregistered"][period]
        out[period] = dict(
            ledger_filled_rows=tot[period]["filled"], report_bets=rep_cv["bets"],
            ledger_roi_from_ledger_dollars=round(tot[period]["roi_on_stake_plus_fee"], 6),
            ledger_roi_from_taker_roi=round(cv_roi, 6), report_roi=rep_cv["roi"],
            abs_diff=round(abs(tot[period]["roi_on_stake_plus_fee"] - rep_cv["roi"]), 8),
            ledger_pnl_usd=round(tot[period]["pnl"], 4),
            ledger_stake_usd=round(tot[period]["stake"], 4), ledger_fee_usd=round(tot[period]["fee"], 4),
            roi_on_stake_only=round(tot[period]["roi_on_stake"], 6),
            literal_metric_all_rows=round(lit_roi, 6), literal_metric_report=rep_lit["roi"],
            literal_bets=int(len(g)), literal_report_bets=rep_lit["bets"],
        )
    return out


def main() -> None:
    b = load_bets()
    rows, tot = build_rows(b)
    for i, row in enumerate(rows, 1):
        row[0] = i
    checks = verify(b, tot)

    doc = {
        "slug": SLUG,
        "title": "Esports game-4 moneylines: the unplayed-map 50-50 clause",
        "group": "Thorp hypotheses",
        "sport": "esports",
        "verdict": "DEAD",
        "hypothesis": ("A '-game4' child moneyline of a best-of-5 resolves 50-50 when map 4 is never played, so it is "
                       "worth P_played*p + (1-P_played)*0.5, not p; if traders price it as plain 'who wins map 4' they "
                       "overpay for the favourite by 4-8c and we take the other side of that by buying the underdog."),
        "mechanism": ("The 50-50 clause truncates both tails: a 3-0 sweep voids the map and returns 0.5 to both sides, "
                      "which happens with probability 1-3p(1-p) under iid maps (about 32% at p=0.65). Anyone who reads "
                      "the market title rather than the rules text prices the favourite at p instead of the "
                      "clause-adjusted 0.59, and the underdog is the mirror-image cheap side. The counterparty is the "
                      "retail taker lifting the favourite in a thin, newly listed child book before anyone knows "
                      "whether map 4 will be needed."),
        "entry_rule": ("Sample: every esports child_moneyline in C.universe() whose slug ends in '-game4' with "
                       "game_start_ts >= 2026-01-01, seeded random order, no volume filter. Window: fills with ts in "
                       "[T-24h, T+60 min), T = the series' scheduled game_start_ts. Signal: the first fill in that "
                       "window whose favourite price max(q,1-q) >= 0.58; the side priced >= 0.5 on that fill is the "
                       "favourite and the underdog is fixed from then on. Entry: the first second in [t0+3 s, T+60 min) "
                       "with a fill that acquires the underdog, taken as a market buy at the HIGHEST underdog price "
                       "paid in that second (the conservative end of the sweep), plus the market's own taker fee. One "
                       "bet per market, one game4 market per series. Capacity-valid primary (the review fix that sets "
                       "the headline): the order is a fill-or-kill $1, taken only when the entry second's underdog "
                       "prints carried at least $1 (minus a $0.005 float tolerance) at or below the entry price - a "
                       "condition visible at entry. Parameters were frozen before the single holdout run."),
        "exit_rule": ("Held to resolution - these are real held positions, not markouts or maker fills. The underdog "
                      "pays 1 if it wins map 4, 0 if it loses, and 0.5 if map 4 is never played (the clause fires, "
                      "47% of DEV and 53% of HOLDOUT bets). exit_ts is the market's closed_ts from C.universe(). Rows "
                      "with exit_kind = 'unfilled' are bets the literal pre-registered rule counted but the "
                      "capacity-valid primary drops, because a $1 fill-or-kill order would not have filled at that "
                      "print (5 in DEV, 2 in HOLDOUT); they carry zero stake, fee and P&L."),
        "cost_model": ("Taker fee = shares * fee_rate * p * (1-p) with each market's own fee_rate from C.universe(), "
                       "never a schedule assumed here. Across these 205 bets that rate is 0 through March 2026 (31 "
                       "bets), 0.03 from April to June (79), mixed in July (11 at 0.03, 7 at 0.05) and 0.05 from "
                       "August (77) - about 1.2c per share on a 0.40 contract at the 5% rate. No maker rebate (this "
                       "is a taker rule). Slippage is zero in the headline: the "
                       "entry price is an actually printed trade price and the $1 must have been carried by that "
                       "print. The report also scores every number at +1c and +2c of slippage, which is what kills it: "
                       "at +1c the HOLDOUT drop-top-1% ROI is -0.6% and the equal-share ROI is -1.2%. stake_usd + "
                       "fee_usd = $1 per bet (x the zero-volume stratum's Horvitz-Thompson weight of 1.50 on 13 DEV "
                       "rows), because the study's per-$1 ROI treats the $1 as the total outlay."),
        "periods": {"dev": "2026-01-01..2026-06-30", "holdout": "2026-07-01..2026-09-18"},
        "headline": {
            "dev": {"bets": 104, "roi": 0.0496, "ci_lo": -0.1202, "ci_hi": 0.2369, "pnl_usd": round(tot["dev"]["pnl"], 2)},
            "holdout": {"bets": 94, "roi": 0.0398, "ci_lo": -0.1228, "ci_hi": 0.2053, "pnl_usd": round(tot["holdout"]["pnl"], 2)},
        },
        "review": ("The adversarial stats review raised 11 issues and 10 were accepted. The two that mattered: both "
                   "pre-review headlines (+43.5% DEV, +59.8% HOLDOUT per $1) rested on a single sub-$1 fill each - "
                   "`codmw-par-tex-2026-06-07` bought at 0.01 with $0.90 on the print supplied 95% of DEV's summed "
                   "ROI and `lol-gl-maz-2026-09-01` at 0.02 with $0.80 supplied 81% of HOLDOUT's - and a per-$1 ROI is "
                   "meaningless where $1 could not have been bought at the entry print. Re-scored as a fill-or-kill $1 "
                   "on the same frozen bets the same rule returns +5.0% (DEV) and +4.0% (HOLDOUT), and estimators that "
                   "one longshot cannot dominate put it at zero: equal-share ROI -3.3% / +1.1%, per-share t of -0.54 / "
                   "+0.13, and the sign flips negative at +1c once the top 1% is trimmed. Reviewers also showed the "
                   "result is unstable across months and titles, that most of the profit sits in in-play rather than "
                   "pregame entries, that the CIs on the literal metric are uninformative with a single 46x "
                   "observation, and that the honest label is DEAD rather than PROMISING. The pre-registered verdict "
                   "gate (every estimator > 0 after fees AND at +1c on the holdout capacity-valid primary) fails on "
                   "two of six checks, so the study self-scores DEAD. The one reviewer suggestion rejected was their "
                   "'decision-time proxy' entry at 1 - signal price + 1c, an artifact of absurd 0.90/0.94 prints."),
        "caveats": [
            "ROI denominator. The study's per-$1 ROI treats the $1 as the TOTAL outlay, so the period aggregate that "
            "reproduces the report exactly is sum(pnl_usd) / sum(stake_usd + fee_usd): +4.9646% (DEV) and +3.9781% "
            "(HOLDOUT) against the report's +5.0% / +4.0% (results_verdict.json: 0.0496 / 0.0398), i.e. exact to the "
            "4 decimals the study rounds to. The per-row `roi` column follows the spec (pnl_usd / stake_usd), and "
            "summing that way gives +5.0224% / +4.0869% - about 1% relative higher, purely because the fee sits in "
            "the study's denominator and not in the spec's. Both are in the `reconciliation` block.",
            "The headline is the capacity-valid primary (fill-or-kill $1), not the literal pre-registered per-$1 "
            "metric. The literal metric on the same rows is +43.5% (DEV, 109 bets) and +59.8% (HOLDOUT, 96), but "
            "almost all of that is two sub-$1 longshot tickets nobody could have bought $1 of; the 7 rows marked "
            "exit_kind = 'unfilled' are exactly the bets the two metrics disagree about.",
            "The mechanism is largely priced in already: pregame game4 favourites trade about 3c above the "
            "clause-adjusted fair value, not the 5-8c hypothesised, and a fully naive price would be 5.3-5.8c above. "
            "After a 5% fee (1.2c per share at p=0.4) and about 0.5-1c of crossing, roughly 1c per share is left - "
            "indistinguishable from zero with about 100 bets per period (per-share P&L SD about 0.37).",
            "Capacity is tiny. The median entry print was $7 (sum $7.9k over 6 DEV months, $2.4k over 2.6 HOLDOUT "
            "months, about $1k/month). At realistic partial fills of stake = min(entry-print $, cap) the report's "
            "dollar P&L is +$90 on $698 staked (DEV, $10 cap) and +$365 on $3,338 (DEV, $100 cap); HOLDOUT +$67 and "
            "+$234, and the $100-cap HOLDOUT number is +1.8% without its single best bet and -1.3% at +1c.",
            "Variants are reported in the study but NOT exported here - the exported rows are the pre-registered "
            "primary only. Variants tried: favourite threshold 0.55 and 0.65, pregame-only, pregame BO5-only, a maker "
            "bid at the last underdog print (30 of 66 candidate fills in DEV, 24 of 42 in HOLDOUT), and game-5 "
            "markets. Variant (a) at 0.55 narrowly passes the holdout gate, but its DEV equal-share ROI is -3.7% and "
            "picking it now would be selecting on the holdout.",
            "13 DEV rows carry a Horvitz-Thompson weight of 1.50 (the zero-volume fetch stratum: 530 of 784 "
            "zero-volume game4 markets were fetched, so those bets stand for the unfetched ones). Their stake, fee, "
            "payout and P&L are scaled by that weight so the ledger's totals reproduce the study's weighted ROI. No "
            "HOLDOUT row is weighted.",
            "13 DEV and 14 HOLDOUT rows entered above 0.50 - the 'underdog' was not actually the cheap side, because "
            "a newly listed book printed both sides at 0.90. They are kept as pre-registered and lost 49% (DEV) and "
            "27% (HOLDOUT). Excluding them is a post-hoc filter chosen after the holdout was seen.",
            "The premise that playability is unknown until T+60 min is false for fast titles: Rocket League, CoD, SC2 "
            "and some MLBB/HoK series finish three maps within the hour, so part of the result is an in-play strategy "
            "reacting to the sweep, not the clause. The rule stays causal (every entry is a later print), but in-play "
            "entries priced at 0.20 or above return -3.0% in HOLDOUT.",
            "About 10% of the sample is BO7 series (identified by their game5/game6 markets), where the clause is "
            "irrelevant. They are kept as pre-registered; BO5 alone gives DEV +9.8% (97 bets) and HOLDOUT +1.8% (91).",
            "DEV/HOLDOUT are split on the series' scheduled start (game_start_ts < / >= 2026-07-01), not on the entry "
            "date, and the rule window opens 24h before the start, so a handful of entry dates straddle the boundary "
            "(the earliest HOLDOUT entry is 2026-06-30, the latest DEV entry 2026-06-29). No bet uses information "
            "after its own entry second.",
            "The report quotes only an ROI for the capacity-valid primary, so headline.pnl_usd here is derived from "
            "these rows: $1 of total outlay per bet gives +$5.48 (DEV) and +$3.74 (HOLDOUT). That is the honest scale "
            "of the edge at the prices that actually printed.",
            "`ts` is on-chain time, about 2.6 s after the match, and T is the scheduled start, which esports routinely "
            "miss. Jan-Feb 2026 had a different listing regime (game4 markets created mid-series, mostly only when "
            "map 4 was going to be played); that affects 2 DEV bets.",
        ],
        "report_path": f"reports/research/{SLUG}.md",
        "code_path": f"pmsports/research/h_{SLUG}.py",
        "truncated": False,
        "n_total_trades": len(rows),
        "reconciliation": checks,
        "columns": COLUMNS,
        "rows": rows,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, separators=(",", ":")))
    print(json.dumps(checks, indent=1))
    print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KB, {len(rows)} rows)")


if __name__ == "__main__":
    main()
