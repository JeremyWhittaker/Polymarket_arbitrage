"""Per-trade ledger export for hypothesis `mlb_runline_home_trunc` (see pmsports/research/LEDGER_SPEC.md).

Reads the ACTUAL bets the backtest made -- `data/research/h_mlb_runline_home_trunc/bets_primary_DEV.parquet`
and `bets_primary_HOLDOUT.parquet`, which are the pre-registered primary rule (threshold 0.02 + fee) and the
rows behind the report's headline -- and writes `data/research/ledgers/mlb_runline_home_trunc.json`.

Nothing is re-simulated. `entry_q` (the fill price), `fee_rate`, `won` (the market payout of the side bought),
`entry_ts{0,1}` and `closed_ts` come straight out of the saved bet rows that produced the report's numbers.
The only arithmetic here is the dollar framing.

Dollar framing. The study's metric is `C.taker_roi` = (won - c - f) / (c + f) per bet, equal-weighted and
clustered by game_pk, where c is the fill price and f = fee_rate * c * (1 - c) is the taker fee on one share.
That is a flat $1 of CAPITAL DEPLOYED (price + fee) per bet. So each ledger row buys shares = 1 / (c + f):

    stake_usd + fee_usd = $1.00 exactly, and
    pnl_usd = payout - stake_usd - fee_usd = C.taker_roi(c, won, fee_rate)   (the per-bet number the report averages)

Hence mean(pnl_usd) over a period IS the report's headline ROI, and sum(pnl_usd) is that ROI times the bet count.
The per-row `roi` column is pnl_usd / stake_usd as the spec defines it; because the spec's denominator excludes
the fee while the report's includes it, the stake-weighted roi runs a little below (for losers: above) the
headline. --verify prints both and the difference is disclosed in the ledger's `caveats`.

Run:
  PYTHONPATH=. systemd-run --user --scope -q -p MemoryMax=5G -p MemorySwapMax=0 .venv/bin/python \
      -m pmsports.research.ledger_mlb_runline_home_trunc
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from pmsports.research import common as C

SLUG = "mlb_runline_home_trunc"
SRC = C.RESEARCH / f"h_{SLUG}"
OUT = C.RESEARCH / "ledgers" / f"{SLUG}.json"
DEC_OFFSET = 1800.0               # D = actual first pitch - 30 min (same constant as the hypothesis script)
MAX_ROWS = 20_000

COLUMNS = ["id", "period", "date", "sport", "league", "event", "market", "side", "entry_ts", "entry_price",
           "stake_usd", "fee_usd", "exit_kind", "exit_ts", "exit_price", "payout", "pnl_usd", "roi", "note"]


def load() -> pd.DataFrame:
    """The saved primary bets of both periods, one row per bet (one bet per game), sorted by entry time."""
    out = []
    for per in ("DEV", "HOLDOUT"):
        b = pd.read_parquet(SRC / f"bets_primary_{per}.parquet")
        assert (b.period == per).all(), per
        assert b.game_pk.is_unique, f"{per}: the primary rule takes one bet per game"
        assert b.entry_q.notna().all() and b.won.isin([0.0, 1.0]).all()
        b = b.copy()
        b["period"] = per.lower()
        side = b.bet_side.to_numpy(int)                       # 1 = buy NO (the +1.5 side), 0 = buy YES (-1.5)
        b["entry_ts_used"] = np.where(side == 0, b.entry_ts0, b.entry_ts1)
        b["entry_sz_used"] = np.where(side == 0, b.entry_sz0, b.entry_sz1)
        assert np.isfinite(b.entry_ts_used).all()
        out.append(b)
    d = pd.concat(out, ignore_index=True)
    return d.sort_values("entry_ts_used", kind="stable").reset_index(drop=True)


def rows(d: pd.DataFrame) -> tuple[list[list], dict]:
    c = d.entry_q.to_numpy(float)                             # the printed fill price the backtest entered at
    rate = np.nan_to_num(d.fee_rate.to_numpy(float))
    won = d.won.to_numpy(float)                               # market payout of the side bought: 1 / 0
    f = np.asarray(C.taker_fee(1.0, c, rate))                 # taker fee on ONE share
    shares = 1.0 / (c + f)                                    # $1.00 of deployed capital per bet (price + fee)
    stake, fee = shares * c, shares * f
    payout = shares * won
    pnl = payout - stake - fee
    ref = np.asarray(C.taker_roi(d.entry_q, d.won, d.fee_rate))          # the report's own per-bet return
    assert np.abs(pnl - ref).max() < 1e-12, "pnl_usd must equal C.taker_roi per bet"
    assert np.abs(ref - d.roi.to_numpy(float)).max() < 1e-12, "and must equal the saved roi column"

    ts = d.entry_ts_used.to_numpy(float)
    lag_min = (ts - (d.first_pitch_ts.to_numpy(float) - DEC_OFFSET)) / 60.0
    out = []
    for i in range(len(d)):
        r = d.iloc[i]
        lay, plus = str(r.o0), str(r.o1)                      # o0 lays 1.5 (verified in the report), o1 gets +1.5
        buy_no = int(r.bet_side) == 1
        side_txt = (f"No on {lay} -1.5 (= {plus} +1.5), taker buy" if buy_no
                    else f"Yes on {lay} -1.5, taker buy")
        where = "home" if int(r.home) == 1 else "away"
        note = (
            f"{lay} ({where}) lays -1.5; moneyline p_team {float(r.p_team):.2f} on {int(r.n_ml)} fills in "
            f"[D-10m, D]; P_yes {float(r.P_yes):.3f} vs FV {float(r.FV):.3f} (symmetric model {float(r.FV_sym):.3f}) "
            f"on {int(r.n_rl)} run-line fills in [D-120m, D]; edge {float(r.edge):+.3f} vs need "
            f"{float(r.need):.3f} -> buy {'NO' if buy_no else 'YES'}; entry {lag_min[i]:.1f} min after D "
            f"(first print on our side, {float(r.entry_sz_used):,.0f} shares; ${float(r.cap_usd):,.0f} printed on "
            f"our side within +1c before first pitch); fee rate {rate[i]:.2f}; final "
            f"{r.home_name} {int(r.home_score)} - {int(r.away_score)} {r.away_name}, {lay} by "
            f"{int(r.margin_team):+d} -> {'covered' if float(r.cover_mlb) == 1.0 else 'did not cover'}, bet "
            f"{'WON' if won[i] == 1.0 else 'LOST'}")
        out.append([
            i + 1, str(r.period), pd.to_datetime(ts[i], unit="s", utc=True).strftime("%Y-%m-%d"),
            "baseball", "mlb", str(r.event_slug), str(r.market_slug), side_txt,
            int(ts[i]), round(float(c[i]), 6), round(float(stake[i]), 6), round(float(fee[i]), 6),
            "resolution", int(r.closed_ts), float(won[i]), round(float(payout[i]), 6),
            round(float(pnl[i]), 6), round(float(pnl[i] / stake[i]), 6), note,
        ])

    head = {}
    for per in ("dev", "holdout"):
        m = (d.period == per).to_numpy()
        roi, lo, hi = C.cluster_ci(ref[m], d.game_pk[m])      # exactly how the report computes the headline
        head[per] = {"bets": int(m.sum()), "roi": round(roi, 4), "ci_lo": round(lo, 4), "ci_hi": round(hi, 4),
                     "pnl_usd": round(float(pnl[m].sum()), 4)}
    return out, head


def build() -> dict:
    d = load()
    rr, head = rows(d)
    assert len(rr) <= MAX_ROWS
    return {
        "slug": SLUG,
        "title": "MLB run line: is home -1.5 structurally rich? (walk-off and no-bottom-9th truncation)",
        "group": "Thorp hypotheses",
        "sport": "baseball",           # sport family, as `C.universe().family` and the other MLB ledgers use it
        "verdict": "DEAD",
        "hypothesis": (
            "Two baseball rules cap home margins -- a leading home team never bats in the bottom of the 9th, and a "
            "walk-off ends the game the moment the winning run scores -- so at the same moneyline price a home "
            "favourite covers -1.5 far less often than an away favourite. If Polymarket's run-line makers convert "
            "the moneyline to a run line with a symmetric margin model, home -1.5 Yes is structurally overpriced "
            "and away -1.5 Yes is cheap, and a frozen asymmetric model can take the other side."),
        "mechanism": (
            "The counterparty would be a maker (or a taker copying one) pricing the run line off the moneyline with "
            "a margin distribution that ignores which dugout is home, which is a rules fact, not an opinion about "
            "the teams. The effect itself is confirmed on 3,627 training games: at moneyline 0.60-0.65 the team "
            "laying 1.5 covers 40.8% at home against 53.4% away, and the frozen model's home term is -0.207 logit. "
            "The hypothesis fails at the second step: the market's own home coefficient is -0.220 (SE 0.005) across "
            "1,281 markets against a true -0.203, so Polymarket already prices the asymmetry in full, if anything "
            "slightly over. There is nothing structural left to harvest."),
        "entry_rule": (
            "Universe: 2026 MLB `...-spread-home-1pt5` / `...-spread-away-1pt5` markets joined to their moneyline "
            "(pregame pre_usd >= $25k), one event per game_pk, first pitch within 1 h of the scheduled start; "
            "outcome 0 is the team laying 1.5 (verified against MLB finals on all 4,344 2026 markets, 0 "
            "mismatches). Within the Data API budget a seeded random sample of games (seed 20260701) of <= 1,000 "
            "markets per period is taped: 529 DEV games / 510 HOLDOUT games. FV model: logistic regression of "
            "1{the team laying 1.5 wins by >= 2} on logit(p_team), home and logit(p_team) x home, fit on every game "
            "with first pitch + 5 h before 2026-07-01 (3,627 games, 7,260 rows) and frozen; coefficients "
            "[-0.503, 0.789, -0.207, 0.049]. Decision at D = actual first pitch - 30 min: p_team = median oriented "
            "moneyline fill price in [D-10 min, D] from local fills; FV_yes = model(p_team, home); P_yes = median "
            "Yes-converted run-line fill price in [D-120 min, D], needing >= 2 fills. Signal: need = 0.02 + "
            "fee_rate * P_yes * (1-P_yes); if P_yes - FV_yes >= need BUY NO (the +1.5 side), if <= -need BUY YES "
            "(the -1.5 side). One bet per game -- if both of a game's markets signal, only the larger |edge| is "
            "taken. Entry = the first run-line fill acquiring our side with ts in [D+3 s, first pitch), at that "
            "print's price as a taker (median 7.3 min after D in DEV, 9.8 min in the holdout); no print, no bet. "
            "Flat $1 of deployed capital (price + fee) per bet."),
        "exit_rule": (
            "Held to resolution of the run-line market: no in-play exit, no stop, no markout, and these are real "
            "held positions rather than maker fills or markout measurements. exit_price is the market payout of "
            "the side bought (1 if it won, 0 if it lost; there were no voids in this sample), and it was checked "
            "against the cover recomputed from MLB finals for every bet. exit_ts is the market's closed_ts."),
        "cost_model": (
            "Polymarket taker fee at each market's own fee_rate, charged on the fill: fee = shares * fee_rate * p * "
            "(1-p). Rates in these rows: DEV 75 bets at 0.03 and 1 at 0.00 (a market listed before the fee "
            "switch); HOLDOUT 29 at 0.05 and 23 at 0.03 (early-July markets created before the switch). No maker "
            "rebate -- every row is a taker fill. entry_price is the actual printed price with NO slippage added, "
            "which is the report's headline convention; the report's +1c sensitivity (DEV -3.5%, HOLDOUT -10.6%) "
            "is not in these rows. Each row deploys $1.00 of capital, so stake_usd + fee_usd = 1.00 and pnl_usd is "
            "exactly the report's per-bet return."),
        # the split is on the SCHEDULED first pitch; entry_ts (= scheduled start - 30 min, UTC) can fall on the
        # previous UTC day, so the first/last figures below are the entry dates the rows themselves carry
        "periods": {"dev": "2026-01-01..2026-06-30 scheduled (2026 MLB games before the boundary; entries "
                           "2026-03-28..2026-06-30 UTC)",
                    "holdout": "2026-07-01..2026-09-18 scheduled (entries 2026-06-30..2026-09-17 UTC)"},
        "headline": {
            "dev": {"bets": head["dev"]["bets"], "roi": head["dev"]["roi"], "ci_lo": head["dev"]["ci_lo"],
                    "ci_hi": head["dev"]["ci_hi"], "pnl_usd": head["dev"]["pnl_usd"]},
            "holdout": {"bets": head["holdout"]["bets"], "roi": head["holdout"]["roi"],
                        "ci_lo": head["holdout"]["ci_lo"], "ci_hi": head["holdout"]["ci_hi"],
                        "pnl_usd": head["holdout"]["pnl_usd"]},
        },
        "review": (
            "No reviewer refuted the rule; the one bug that bit was found before the holdout ran. The first build "
            "oriented run-line fills by the Data API `outcomeIndex`, which is wrong on 2026-05-13/14 (57-96% of "
            "fills flipped in 13 markets, ~20% in 4 more), inventing fake 40c edges; re-fetching all 1,999 tapes "
            "with the token id moved the DEV primary from +3.1% (80 bets) to -1.7% (76 bets). The execution "
            "reviewer re-priced every entry at the worst print in the same second, at the same-second VWAP, at a "
            "flat 5% fee and at +2c, and the look-ahead reviewer re-anchored the decision on the scheduled start "
            "(-3.6% DEV, -9.0% HOLDOUT) and confirmed the training set ends before the holdout; the stats reviewer "
            "re-clustered by date, series, team and t-CI and ran leave-one-month-out and drop-top-5. Nothing turned "
            "the primary positive in both periods. Their standing objection is power, not bias: 76 and 52 bets can "
            "only detect about 32 and 39 points of ROI, so the DEAD verdict rests on the 1,281-market pricing "
            "diagnostic (home coefficient -0.22 market vs -0.20 true, SE 0.005), and a residual mispricing of "
            "about 3c either way cannot be excluded."),
        "caveats": [
            "Small primary sample: 76 DEV and 52 HOLDOUT bets, CIs +-20-27 points, MDE about 32 (DEV) and 39 "
            "(HOLDOUT) points of ROI. Run-line books are thin pregame -- the median market has 5 fills (DEV) and 2 "
            "(HOLDOUT) in the 2-hour window, and 28%/42% have fewer than 2 and are skipped.",
            "The verdict does not rest on these rows. It rests on the pricing diagnostic over 1,281 markets: the "
            "market's home coefficient is -0.220 (SE 0.005) against a true -0.203, i.e. the asymmetry is already "
            "priced. The residual is a uniform ~0.6-0.9c premium on the -1.5 side of both home and away markets, "
            "smaller than half-spread plus fee.",
            "FV is in-sample for DEV: the frozen model includes the DEV games, as the pre-registered rule "
            "specifies. The walk-forward version (fit on 2025 only) gives DEV +8.3% [-10.5, +27.4] on 96 bets, "
            "which is not significant; its home term is nearly identical (-0.200 vs -0.207).",
            "Direction is one-sided: 73 of 76 DEV bets and 49 of 52 HOLDOUT bets are NO (buying the +1.5 side), "
            "because the -1.5 side trades ~0.7c over the model, so the +2c gate fires almost only there. The "
            "per-side signs flip between periods (NO on home -1.5: DEV -11.7% on 30 bets, HOLDOUT +34.8% on 22; "
            "NO on away -1.5: DEV +2.3% on 43, HOLDOUT -34.2% on 27), which is what noise looks like.",
            "P_yes is a median of fills over 2 hours, so it mixes bid-side and ask-side prints and can be stale "
            "against the moneyline at D. That adds noise signals, but it cannot hide a 4-5c structural mispricing.",
            "The games are a seeded random sample (59% of eligible DEV games, 78% of HOLDOUT), drawn on "
            "pre-decision information only (moneyline pregame volume, the listing, the schedule, and whether a "
            "rain delay was already visible at D).",
            "D uses the ACTUAL first pitch, so rain delays are handled by excluding games whose first pitch was "
            "more than 1 h off schedule; anchoring on the scheduled start instead gives -3.6% (DEV).",
            "Only the pre-registered primary rule (threshold 0.02 + fee) is exported. NOT in these rows: threshold "
            "0.01 (DEV 169 bets +3.4%, HOLDOUT 98 bets -20.1% [-39.1, -1.5]), threshold 0.04 (9 and 9 bets, +49.5% "
            "and -30.1%), the naive structural bet (NO home -1.5 / YES away -1.5: DEV 606 bets -2.1%, HOLDOUT 525 "
            "bets -2.5%), the walk-forward FV, the scheduled-start anchor, and variant (d), the in-play walk-off "
            "trade, which fired only 4 and 2 times and is INCONCLUSIVE on sample size.",
            "Multiple testing: 5 pregame rule variants per period were tried. The only CI excluding 0 is threshold "
            "0.01 in the holdout, and it is negative.",
            "Capacity is small even before the edge question: about 0.9-1.4 bets per day league-wide, with a "
            "median of $199 (DEV) and $313 (HOLDOUT) printed on our side within 1c of the entry print inside the "
            "30-minute window (the per-row note carries each bet's own figure).",
            "Period is assigned by the scheduled start (>= 2026-07-01 UTC is holdout), so one holdout row "
            "(mlb-sd-chc-2026-06-30) carries an entry timestamp late on June 30 UTC: it is the half hour before a "
            "first pitch that falls on July 1 UTC.",
            "stake_usd + fee_usd = $1.00 per row, because the report's ROI is the return on capital deployed "
            "(price + fee). The `roi` column is pnl_usd/stake_usd as the spec defines it, so it excludes the fee "
            "from the denominator and differs from the headline by roughly the fee share; mean(pnl_usd) per period "
            "IS the headline ROI.",
            "entry_price carries NO slippage: it is the actual next print on our side. The report's +1c "
            "sensitivity (DEV -3.5%, HOLDOUT -10.6%) is not reflected in these rows, and run-line books often run "
            "2-6c wide.",
            "Data caveat inherited by the rest of the repo: the Data API `outcomeIndex` is unreliable on "
            "2026-05-13/14. These tapes are oriented by token id (`asset`); `C.fills` and `data/mlb/trades` are "
            "not.",
        ],
        "report_path": f"reports/research/{SLUG}.md",
        "code_path": f"pmsports/research/h_{SLUG}.py",
        "truncated": False,
        "n_total_trades": len(rr),
        "columns": COLUMNS,
        "rows": rr,
    }


def verify(led: dict) -> None:
    """Re-derive the headline from the ledger rows alone, exactly as a reader of the JSON would, and compare it
    with the numbers the hypothesis script itself wrote to results.json (which are the report's table)."""
    res = json.loads((SRC / "results.json").read_text())["results"]
    df = pd.DataFrame(led["rows"], columns=led["columns"])
    print(f"{'period':8} {'rows':>5} {'sum pnl':>10} {'mean pnl':>10} {'ledger ROI':>11} {'report ROI':>11} "
          f"{'report pnl':>11} {'stake-wtd roi':>14}")
    for per in ("dev", "holdout"):
        g = df[df.period == per]
        rep = res[per.upper()]["primary"]
        hl = led["headline"][per]
        mean_pnl, sw = g.pnl_usd.mean(), g.pnl_usd.sum() / g.stake_usd.sum()
        print(f"{per:8} {len(g):5d} {g.pnl_usd.sum():10.4f} {mean_pnl:10.4f} {hl['roi']:11.4f} "
              f"{rep['roi']:11.4f} {rep['pnl']:11.4f} {sw:14.4f}")
        assert len(g) == hl["bets"] == rep["bets"], per
        assert abs(mean_pnl - rep["roi"]) < 5e-5, per                      # ledger P&L -> the report's ROI
        assert abs(hl["roi"] - rep["roi"]) < 5e-5, per                     # and the same clustered mean
        assert abs(g.pnl_usd.sum() - rep["pnl"]) < 1e-3, per               # report "pnl" = sum of per-bet returns
        assert abs(hl["ci_lo"] - rep["ci_lo"]) < 5e-4 and abs(hl["ci_hi"] - rep["ci_hi"]) < 5e-4, per
        assert abs(g.entry_price.mean() - rep["avg_price"]) < 5e-5, per
        assert abs((g.exit_price == 1.0).mean() - rep["win_rate"]) < 5e-5, per
        assert np.abs(g.payout - g.stake_usd - g.fee_usd - g.pnl_usd).max() < 2e-6, per
        assert np.abs(g.stake_usd + g.fee_usd - 1.0).max() < 2e-6, per
        assert np.abs(g.pnl_usd / g.stake_usd - g.roi).max() < 2e-6, per
    assert df.entry_ts.is_monotonic_increasing
    assert df.exit_kind.eq("resolution").all()


def main() -> None:
    led = build()
    verify(led)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(led, separators=(",", ":")))
    print(f"wrote {OUT}  rows={led['n_total_trades']}  {OUT.stat().st_size / 1024:.0f} KiB")


if __name__ == "__main__":
    main()
