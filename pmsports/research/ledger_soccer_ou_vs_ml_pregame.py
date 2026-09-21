"""Per-trade ledger export for hypothesis `soccer_ou_vs_ml_pregame` (see pmsports/research/LEDGER_SPEC.md).

Reads the ACTUAL bets the backtest made -- `data/research/h_soccer_ou_vs_ml_pregame/bets_primary_dev.parquet`
(228 development bets) and `bets_primary_holdout.parquet` (67 holdout bets), which are the pre-registered PRIMARY
rule (isotonic calibration frozen on DEV, |P_cal - P_ou| >= 0.04, taker entry at the first on-side O/U print after
D+3 s, held to resolution) -- joins the O/U 2.5 market's slug and resolution time from `universe.parquet`, and
writes `data/research/ledgers/soccer_ou_vs_ml_pregame.json`.

Nothing is re-simulated. `q_entry` (the printed fill price the backtest bought at), `fee_rate`, `y_over` (the
Over-token payout), `won`, `ts_entry`, `T`, `P_model`, `P_cal`, `P_ou`, `ou_close5`, `size_entry`, `cap_usd` and
`roi` all come straight out of the saved bet rows that produced the report's headline; the only arithmetic here is
the dollar framing, and the per-row P&L is asserted equal to the saved `roi` column.

Dollar framing. The study's metric is `C.taker_roi` = (won - c - f) / (c + f) per bet, equal-weighted, where c is
the fill price and f = fee_rate * c * (1 - c) is the taker fee on one share. That is a flat $1 of CAPITAL DEPLOYED
(price + fee) per bet. So each ledger row buys shares = 1 / (c + f), giving

    stake_usd + fee_usd = $1.00 exactly, and
    pnl_usd = payout - stake_usd - fee_usd = C.taker_roi(c, won, fee_rate)   (the identity the report's ROI averages)

Hence mean(pnl_usd) over a period IS the report's headline ROI, and sum(pnl_usd) is that ROI times the bet count.
The per-row `roi` column is pnl_usd / stake_usd as the spec defines it; because the spec's denominator excludes the
fee while the report's includes it, the stake-weighted roi differs slightly from the headline. Both numbers are
printed by the verify step and the difference is disclosed in `caveats`.

Run:
  PYTHONPATH=. systemd-run --user --scope -q -p MemoryMax=5G -p MemorySwapMax=0 .venv/bin/python \
      -m pmsports.research.ledger_soccer_ou_vs_ml_pregame
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from pmsports.research import common as C

SLUG = "soccer_ou_vs_ml_pregame"
SRC = C.RESEARCH / f"h_{SLUG}"
OUT = C.RESEARCH / "ledgers" / f"{SLUG}.json"
MAX_ROWS = 20_000
DEC = 600  # decision time D = T - 10 min

COLUMNS = ["id", "period", "date", "sport", "league", "event", "market", "side", "entry_ts", "entry_price",
           "stake_usd", "fee_usd", "exit_kind", "exit_ts", "exit_price", "payout", "pnl_usd", "roi", "note"]


def load() -> pd.DataFrame:
    """The primary-rule bets, dev + holdout, with the O/U 2.5 market slug and resolution time attached."""
    dev = pd.read_parquet(SRC / "bets_primary_dev.parquet").assign(period="dev")
    hold = pd.read_parquet(SRC / "bets_primary_holdout.parquet").assign(period="holdout")
    d = pd.concat([dev, hold])
    d.index.name = "event_slug"
    d = d.reset_index()
    assert d.event_slug.is_unique, "one bet per event expected"
    assert (d.period == d.split).all(), "split column must agree with the file the row came from"
    assert (d.ts_entry >= d["T"] - DEC + 3).all() and (d.ts_entry < d["T"]).all(), "entry in [D+3s, T)"
    assert (d.dev.abs() >= 0.04 - 1e-12).all() and (d.buy_over == (d.dev > 0)).all(), "signal/side mismatch"

    e = pd.read_parquet(SRC / "events.parquet").set_index("event_slug")
    cid = e.ou_cid.reindex(d.event_slug)
    u = C.universe(columns=["condition_id", "market_slug", "closed_ts", "league"])
    u = u[u.condition_id.isin(set(cid))].drop_duplicates("condition_id").set_index("condition_id")
    d["market_slug"] = u.market_slug.reindex(cid).to_numpy()
    d["closed_ts"] = u.closed_ts.reindex(cid).to_numpy()
    assert (d.market_slug == d.event_slug + "-total-2pt5").all(), "O/U leg must be <event>-total-2pt5"
    assert (u.league.reindex(cid).to_numpy() == d.league.to_numpy()).all()
    assert d.closed_ts.notna().all() and (d.closed_ts > d.ts_entry).all(), "resolution must follow entry"
    # the payout in the bets file must be the O/U leg's Over payout in the study universe
    assert np.array_equal(e.y_over.reindex(d.event_slug).to_numpy(), d.y_over.to_numpy())
    assert np.array_equal(np.where(d.buy_over, d.y_over, 1 - d.y_over), d.won.to_numpy())
    return d.sort_values("ts_entry", kind="stable").reset_index(drop=True)


def rows(d: pd.DataFrame) -> tuple[list[list], dict]:
    c = d.q_entry.to_numpy(float)                              # printed fill price the backtest bought at
    rate = np.nan_to_num(d.fee_rate.to_numpy(float))
    won = d.won.to_numpy(float)                                # 1 if our side's token paid 1, else 0
    cc = np.clip(c, 0.001, 0.999)                              # C.taker_roi's own clip
    f = np.asarray(C.taker_fee(1.0, cc, rate))                 # taker fee on ONE share
    shares = 1.0 / (cc + f)                                    # $1.00 of deployed capital per bet (price + fee)
    stake, fee = shares * cc, shares * f
    payout = shares * won
    pnl = payout - stake - fee
    ref = d.roi.to_numpy(float)                                # the backtest's own per-bet return, as saved
    assert np.abs(pnl - ref).max() < 1e-12, "pnl_usd must equal the saved roi column (C.taker_roi) per bet"

    ts = d.ts_entry.to_numpy(np.int64)
    ours_close = np.where(d.buy_over, d.ou_close5, 1 - d.ou_close5)   # our side's closing mark, [T-5m, T)
    ours_pou = np.where(d.buy_over, d.P_ou, 1 - d.P_ou)               # our side at the window median P_ou
    out = []
    for i in range(len(d)):
        r = d.iloc[i]
        side = "Over 2.5" if bool(r.buy_over) else "Under 2.5"
        clv = "" if not np.isfinite(ours_close[i]) else (
            f"; CLV vs the [T-5m,T) O/U median on our side {100 * (ours_close[i] - c[i]):+.2f}c")
        note = (
            f"signal dev = P_cal - P_ou = {float(r.P_cal):.4f} - {float(r.P_ou):.4f} = {float(r.dev):+.4f} "
            f"(|dev| >= 0.04 -> buy {side}); moneyline-implied P_model {float(r.P_model):.4f} at D = T-10m; "
            f"entry {int(ts[i] - (int(r['T']) - DEC))} s after D ({int(r['T']) - int(ts[i])} s before kickoff) at "
            f"the first on-side print, {float(r.q_entry):.4f} for our side, {100 * (c[i] - ours_pou[i]):+.2f}c vs "
            f"the [T-30m, D] O/U median on our side{clv}; that print was ${float(r.size_entry) * float(r.q_entry):,.2f} "
            f"and ${float(r.cap_usd):,.0f} of same-side flow printed within 1c of it before kickoff; fee rate "
            f"{rate[i]:.4g}; resolved "
            + (f"OVER 2.5 (Over token paid 1), so {side} " if float(r.y_over) == 1.0
               else f"UNDER 2.5 (Over token paid 0), so {side} ")
            + ("WON" if won[i] == 1 else "LOST"))
        out.append([
            i + 1, str(r.period), pd.to_datetime(ts[i], unit="s", utc=True).strftime("%Y-%m-%d"),
            "soccer", str(r.league), str(r.event_slug), str(r.market_slug),
            f"{side} (taker buy)", int(ts[i]), round(float(c[i]), 6),
            round(float(stake[i]), 6), round(float(fee[i]), 6),
            "resolution", int(r.closed_ts), float(won[i]), round(float(payout[i]), 6),
            round(float(pnl[i]), 6), round(float(pnl[i] / stake[i]), 6), note,
        ])

    head = {}
    for per in ("dev", "holdout"):
        m = (d.period == per).to_numpy()
        roi, lo, hi = C.cluster_ci(ref[m], d.event_slug[m], n_boot=4000)
        head[per] = {"bets": int(m.sum()), "roi": round(roi, 4), "ci_lo": round(lo, 4), "ci_hi": round(hi, 4),
                     "pnl_usd": round(float(pnl[m].sum()), 4)}
    return out, head


def build() -> dict:
    d = load()
    rr, head = rows(d)
    assert len(rr) <= MAX_ROWS
    # descriptive numbers quoted in the prose fields, computed from the rows so they cannot drift
    lag = (d.closed_ts.to_numpy(float) - d["T"].to_numpy(float)) / 3600.0
    hm = lambda h: f"{int(h)} h {round((h % 1) * 60):02d} min"  # noqa: E731
    hold_txt = (f"a median of {hm(np.median(lag))} after kickoff; the earliest is {hm(lag.min())} and the "
                f"25th percentile {hm(np.percentile(lag, 25))}")
    df_ = pd.DataFrame(rr, columns=COLUMNS)
    fee = {p: (g.fee_usd / g.stake_usd).mean() for p, g in df_.groupby("period")}     # fee as a share of stake
    cap = {p: g.fee_usd.mean() for p, g in df_.groupby("period")}                     # fee as a share of the $1
    fee_txt = (f"the mean fee is {100 * fee['dev']:.2f}% (dev) and {100 * fee['holdout']:.2f}% (holdout) of stake, "
               f"i.e. {100 * cap['dev']:.2f}% and {100 * cap['holdout']:.2f}% of the $1 deployed per bet")
    return {
        "slug": SLUG,
        "title": "Soccer O/U 2.5 lagging the 3-way moneyline pregame",
        "group": "Thorp hypotheses",
        "sport": "soccer",
        "verdict": "DEAD",
        "hypothesis": (
            "The deep 3-way soccer moneyline absorbs pregame news (lineups, injuries, weather) within minutes, "
            "while the Over/Under 2.5 market for the same game sits on a separate '-more-markets' page with no "
            "conversion link to it -- so when the moneyline-implied goal expectation moves, the O/U should still "
            "be quoting the old total, and buying the side the moneyline implies should collect the gap before "
            "kickoff."),
        "mechanism": (
            "The two team-win legs are neg-risk markets with $50k+ of pregame volume each, kept mutually "
            "consistent by arbitrage bots, so a news-driven repricing lands there first. The claimed counterparty "
            "is a maker still resting a stale O/U 2.5 quote that has not been dragged along, plus the takers "
            "hitting it. The data refutes the premise on its own terms: at the decision point the last O/U print "
            "is a median of 30 s (dev) / 39 s (holdout) old, the O/U moves with the moneyline inside the same "
            "window rather than after it, and after a gap opens the O/U does not drift toward the moneyline-implied "
            "value at all (-0.05c dev, -0.02c holdout, CIs ruling out more than about 0.2c)."),
        "entry_rule": (
            "Universe (pregame information only): soccer events in markets() with exactly two team-win legs "
            "('<event_slug>-<team>', not '-draw'), each with pre_usd >= $50k, plus a linked O/U 2.5 market "
            "'<event_slug>-total-2pt5' in universe() with valid 0/1 payouts -- 583 events, 448 dev (T < 2026-07-01) "
            "and 135 holdout. T is the legs' game_start_ts, identical for both legs and for the O/U market in all "
            "583. DECISION TIME D = T - 10 min. MODEL: pH, pA = unweighted medians of Yes-converted team-leg fill "
            "prices over [T-20m, D] (>= 1 fill per leg); pD = 1 - pH - pA clipped to [0.05, 0.5] with pH, pA "
            "rescaled proportionally when the clip binds; an independent Poisson (lam_h, lam_a) is solved so "
            "P(home) = pH and P(away) = pA, and P_model = P(total goals >= 3). Integrity filter (added after seeing "
            "dev, uses only pre-decision prices): skip the event if a team leg's median is <= 1c or >= 99c -- 8 "
            "events (7 dev, 1 holdout) whose scheduled start was later than the real kickoff. CALIBRATION: "
            "isotonic regression of the Over outcome on P_model, fitted on the 441 dev events and FROZEN "
            "(calibration_dev.json) -> P_cal. MARKET: P_ou = median Over-converted O/U fill price over [T-30m, D], "
            "skip if fewer than 2 fills; O/U prints come from Data API taker fills in [T-24h, T) with the side "
            "taken from the token id (a taker SELL of a token = acquiring the other token at 1 - price). SIGNAL: "
            "dev = P_cal - P_ou; buy Over if dev >= +0.04, buy Under if dev <= -0.04. ENTRY: taker, the FIRST O/U "
            "print acquiring our side with ts in [D+3s, T), at that print's own price -- 242 dev signals -> 228 "
            "entries and 79 holdout signals -> 67 entries (no on-side print before kickoff = no bet). One bet per "
            "event, flat $1 of deployed capital (price + fee). Median entry lag after D is 53 s (dev) / 64 s "
            "(holdout), and the entry costs +0.56c / +0.43c against the window median on our side."),
        "exit_rule": (
            "Held to resolution of the O/U 2.5 market. Every row is a REAL HELD POSITION bought from a taker print "
            "-- not a maker fill and not a markout measurement -- so exit_kind is 'resolution' on all 295 rows. "
            "exit_price is our side's token payout: 1 if the match finished with 3+ goals and we bought Over, or "
            "with 0-2 goals and we bought Under; 0 otherwise. There were no voids and no 0.5 payouts in these bets. "
            "No in-play exit, no stop, no partial fills, no position is closed before kickoff. exit_ts is the O/U "
            f"market's closed_ts ({hold_txt}), so the holding period runs from the entry print to resolution."),
        "cost_model": (
            "Polymarket taker fee at each market's own fee_rate on the fill: fee = shares * fee_rate * p * (1-p). "
            "Rates present in these rows are 0.00 (115 dev bets, Dec 2025 - mid-Apr 2026), 0.03 (113 dev + 18 "
            f"holdout) and 0.05 (49 holdout); {fee_txt}. No maker rebate (this is a taker rule). entry_price is "
            "the actual printed price with NO "
            "slippage added, which is the report's headline convention; the half-spread already paid relative to "
            "the window median (+0.56c dev, +0.43c holdout) is inside that printed price. The report's +1c "
            "stress (dev +8.34%, holdout -21.25%) is NOT in these rows. Each row deploys $1.00 of capital, so "
            "stake_usd + fee_usd = 1.00 and pnl_usd is exactly the report's per-bet return."),
        "periods": {"dev": "2025-12-06..2026-06-30", "holdout": "2026-07-01..2026-09-18"},
        "headline": {
            "dev": {"bets": head["dev"]["bets"], "roi": head["dev"]["roi"], "ci_lo": head["dev"]["ci_lo"],
                    "ci_hi": head["dev"]["ci_hi"], "pnl_usd": head["dev"]["pnl_usd"]},
            "holdout": {"bets": head["holdout"]["bets"], "roi": head["holdout"]["roi"],
                        "ci_lo": head["holdout"]["ci_lo"], "ci_hi": head["holdout"]["ci_hi"],
                        "pnl_usd": head["holdout"]["pnl_usd"]},
        },
        "review": (
            "All three adversarial reviewers (look-ahead/selection, execution/fees/capacity, statistics) upheld the "
            "DEAD verdict and none could rescue the strategy. The look-ahead reviewer reproduced both headline "
            "numbers from the frozen calibration, found no dev tape data after 2026-06-30 21:00 UTC, no duplicate "
            "or out-of-window prints in the 583 tapes, no payout incoherence against the 1.5/3.5/BTTS/draw legs, "
            "and confirmed that the three remaining in-play-looking events do not produce bets (excluding them "
            "changes nothing). The execution reviewer showed the result is not an execution artifact in either "
            "direction: entries sit a median of 0-1c from mid, and every friendlier fill -- the window median, the "
            "VWAP of our side, a fantasy fill at the closing median, even a maker fill at mid with a rebate and no "
            "fee -- still leaves the holdout at -16% to -19%, so the loss is the bet itself, not the cost of "
            "getting in. The statistics reviewer found the dev cross-fit is robustly negative across seeds (-8.9% "
            "+/- 3.9% at k=5, -10.6% leave-one-out), that the holdout's one-sided p is 0.93 for the primary rule, "
            "and that dropping the top 3 holdout winners takes it to -33.6%; the only positive holdout variant (b) "
            "has a Holm-adjusted p of 0.17 and is the mechanism reversed."),
        "caveats": [
            "DEAD, and this ledger is a negative result. The holdout's 67 bets lose 19.4% per $1 of deployed "
            "capital ([-44.7%, +7.0%], -21.3% with 1c of slippage), the pre-registered dev +10.8% is IN-SAMPLE "
            "(the isotonic curve was fitted on the same dev outcomes), and the honest dev numbers are -8.8% "
            "(5-fold cross-fit) and -3.5% (fit before March, test March-June). Do not read the dev rows as an edge.",
            "The mechanism is absent in both periods, which is what actually kills it: after a signal the O/U does "
            "not move toward the moneyline-implied value (-0.05c dev, -0.02c holdout, CIs of about +/-0.2c), the "
            "O/U is not stale at D (last print a median of 30-39 s old, 34 / 23 prints in the last half hour), and "
            "in the holdout the O/U price is the better forecaster (Brier .206 vs .226 for the calibrated model). "
            "Logit regressions give the moneyline-implied total a NEGATIVE coefficient given the O/U price "
            "(pooled -0.74, se 0.31).",
            "Only the pre-registered PRIMARY rule is exported (isotonic calibration, threshold 0.04). The report's "
            "variants are NOT in these rows: (a) thresholds 0.03 (78 holdout bets, -16.4%) and 0.06 (44, -22.2%); "
            "(b) the two-variable logistic, signal = fitted - P_ou (38 holdout bets, +21.7%); (diag) a smooth "
            "1-variable logistic calibration (63, -28.8%); (d) the dynamic T-45m rule (26, -16.1%); (e) the "
            "lead-lag regressions, which are measurements and not trades.",
            "Variant (b) is the one positive holdout number and it is deliberately excluded. Its dev fit gave the "
            "moneyline a negative coefficient, so 71-75% of its signals point AGAINST the raw moneyline-implied "
            "value -- it is 'fade the moneyline-implied total and stretch the O/U price', the opposite of this "
            "hypothesis. With 38 bets, a CI touching 0 and 7 variants tried, it is a lead for a separately "
            "pre-registered study on data after 2026-09-19, not a result.",
            "The holdout is small: 135 events clear the $50k-per-leg bar and only 67 trade, so the CI half-width is "
            "about 25 points and the -19.4% point estimate on its own proves nothing. The verdict rests on the "
            "in-sample-free dev estimate being <= 0, the absent convergence (a much lower-noise test), and the "
            "moneyline carrying no incremental information given the O/U price.",
            "The 1c/99c team-leg integrity filter was added after seeing dev. It uses only pre-decision prices and "
            "removes 8 events (7 dev, 1 holdout) whose scheduled start was later than the real kickoff, so they "
            "were in play or finished at D. More subtle start-time errors (a kickoff 5-10 min early) cannot be "
            "ruled out; they would shorten the window rather than bias toward profit.",
            "The holdout leaned 73% Under because the frozen isotonic curve maps the moneyline to a lower goal "
            "expectation than the O/U market (mean P_cal .544 vs P_ou .582), and the holdout happened to be "
            "high-scoring (Over hit 64%). Both sides lose in the holdout (Over 18 bets -21.3%, Under 49 bets "
            "-18.8%), so the direction is not the whole story.",
            "Per-league and per-month splits in these rows are noise. Every holdout league with 4+ bets is "
            "negative (World Cup -13% on 20, EPL -40% on 12, La Liga -13% on 8, UCL -38% on 5, Serie A -57% on 5, "
            "Liga MX -100% on 4) while dev had EPL and La Liga at +24% each and Bundesliga at -74%. The dev P&L is "
            "also top-heavy: the top 1% of bets is 43% of the dev total, and dropping the 3 best dev bets takes "
            "+10.8% to +6.3%. Do not mine these rows by subgroup.",
            "stake_usd + fee_usd = $1.00 per row, because the report's ROI is the return on capital deployed "
            "(price + fee). The `roi` column is pnl_usd/stake_usd as the spec defines it, so it excludes the fee "
            "from the denominator and runs slightly further from 0 than the headline.",
            "Capacity, had the edge existed: about 1.1 (dev) and 0.8 (holdout) bets a day. The print we copy is "
            "small (a median of $10-13), but same-side O/U flow within 1c of our entry between D+3s and kickoff is "
            "a median of $900 (dev) / $1,150 (holdout) per event -- heavy-tailed, with World Cup games reaching "
            "$0.9M and the 5 largest events holding about half of all capacity. Roughly $1k per event is "
            "deployable, about $30k a month, which at the holdout ROI loses money. The per-row note carries that "
            "dollar figure.",
        ],
        "report_path": f"reports/research/{SLUG}.md",
        "code_path": f"pmsports/research/h_{SLUG}.py",
        "truncated": False,
        "n_total_trades": len(rr),
        "columns": COLUMNS,
        "rows": rr,
    }


def verify(led: dict) -> None:
    """Re-derive the headline from the ledger rows alone, exactly as a reader of the JSON would."""
    df = pd.DataFrame(led["rows"], columns=led["columns"])
    print(f"{'period':8} {'rows':>5} {'sum pnl':>10} {'mean pnl':>9} {'= headline ROI':>15} {'sum pnl/sum stake':>18}")
    for per in ("dev", "holdout"):
        g = df[df.period == per]
        mean_pnl = g.pnl_usd.mean()
        sw = g.pnl_usd.sum() / g.stake_usd.sum()
        hl = led["headline"][per]
        print(f"{per:8} {len(g):5d} {g.pnl_usd.sum():10.4f} {mean_pnl:9.4f} {hl['roi']:15.4f} {sw:18.4f}")
        assert len(g) == hl["bets"]
        assert abs(mean_pnl - hl["roi"]) < 5e-5
        assert abs(g.pnl_usd.sum() - hl["pnl_usd"]) < 1e-3
        # every row is internally consistent
        assert np.abs(g.payout - g.stake_usd - g.fee_usd - g.pnl_usd).max() < 2e-6
        assert np.abs(g.stake_usd + g.fee_usd - 1.0).max() < 2e-6
        assert (g.exit_kind == "resolution").all()
        assert g.exit_price.isin([0.0, 1.0]).all()
        assert (g.exit_ts > g.entry_ts).all()
    assert df.entry_ts.is_monotonic_increasing
    assert list(df.id) == list(range(1, len(df) + 1)) and led["columns"] == COLUMNS and df.notna().all().all()
    assert (df.date == pd.to_datetime(df.entry_ts, unit="s", utc=True).dt.strftime("%Y-%m-%d")).all()
    # the fee on every row must be the market's own taker fee: shares * rate * p * (1-p), rate in {0, .03, .05}
    shares = df.stake_usd / df.entry_price
    implied = df.fee_usd / (shares * df.entry_price * (1 - df.entry_price))
    near = np.array([0.0, 0.03, 0.05])[np.abs(implied.to_numpy()[:, None] - [0.0, 0.03, 0.05]).argmin(1)]
    print(f"  implied fee rates: {dict(zip(*np.unique(near, return_counts=True)))} "
          f"(max deviation {np.abs(implied - near).max():.2e}, from rounding the columns to 6 dp)")
    assert np.abs(implied - near).max() < 1e-5
    # the report's own primary-table stats, straight off the rows
    for per, win, px, over in (("dev", 0.5395, 0.4878, 0.3947), ("holdout", 0.3881, 0.4695, 0.2687)):
        g = df[df.period == per]
        ov = g.side.str.startswith("Over").mean()
        print(f"  {per}: win rate {g.exit_price.mean():.4f} (report {win}), avg entry price "
              f"{g.entry_price.mean():.4f} (report {px}), Over share {ov:.4f} (report {over}), "
              f"avg fee/share {(g.fee_usd / (g.stake_usd / g.entry_price)).mean():.5f}")
        assert abs(g.exit_price.mean() - win) < 5e-5 and abs(g.entry_price.mean() - px) < 5e-5
        assert abs(ov - over) < 5e-5
    # the report's Over / Under splits
    for per, side, n, roi in (("dev", "Over", 90, 0.1357), ("dev", "Under", 138, 0.0891),
                              ("holdout", "Over", 18, -0.2125), ("holdout", "Under", 49, -0.1875)):
        g = df[(df.period == per) & df.side.str.startswith(side)]
        print(f"  {per} buy {side}: {len(g)} bets (report {n}), mean pnl {g.pnl_usd.mean():+.4f} (report {roi:+.4f})")
        assert len(g) == n and abs(g.pnl_usd.mean() - roi) < 5e-5


def main() -> None:
    led = build()
    verify(led)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(led, separators=(",", ":")))
    print(f"wrote {OUT}  rows={led['n_total_trades']}  {OUT.stat().st_size / 1024:.0f} KiB")


if __name__ == "__main__":
    main()
