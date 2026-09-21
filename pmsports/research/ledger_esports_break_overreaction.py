"""Per-trade ledger export for hypothesis `esports_break_overreaction` (see pmsports/research/LEDGER_SPEC.md).

Reads the ACTUAL bets the backtest made -- `data/research/h_esports_break_overreaction/bets_{dev,holdout}.parquet`,
rule `primary_thr0.04`, the pre-registered primary rule -- joins the series metadata from `series.parquet`, and
writes `data/research/ledgers/esports_break_overreaction.json`.

Nothing is re-simulated: `q` (entry price), `rate` (fee rate), `won` (resolution payout) and `entT_ts` come straight
out of the saved bet rows that produced the report's headline. The only arithmetic here is the dollar framing.

Dollar framing. The study's metric is `C.taker_roi` = (won - c - f) / (c + f) per bet, equal-weighted, where c is
the fill price and f = rate * c * (1 - c) is the taker fee on one share. That is a flat $1 of CAPITAL DEPLOYED
(price + fee) per bet. So each ledger row buys shares = 1 / (c + f), giving

    stake_usd + fee_usd = $1.00 exactly, and
    pnl_usd = payout - stake_usd - fee_usd = C.taker_roi(c, won, rate)   (the identity the report's ROI averages)

Hence mean(pnl_usd) over a period IS the report's headline ROI, and sum(pnl_usd) is that ROI times the bet count.
The per-row `roi` column is pnl_usd / stake_usd as the spec defines it; because the spec's denominator excludes the
fee while the report's includes it, the stake-weighted roi runs ~2-3 points above the headline. Both numbers are
printed by --verify and the difference is disclosed in the ledger's `caveats`.

Run:
  PYTHONPATH=. systemd-run --user --scope -q -p MemoryMax=5G -p MemorySwapMax=0 .venv/bin/python \
      -m pmsports.research.ledger_esports_break_overreaction
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from pmsports.research import common as C

SLUG = "esports_break_overreaction"
SRC = C.RESEARCH / f"h_{SLUG}"
OUT = C.RESEARCH / "ledgers" / f"{SLUG}.json"
RULE = "primary_thr0.04"          # the pre-registered primary rule, executed entry convention
MAX_ROWS = 20_000

COLUMNS = ["id", "period", "date", "sport", "league", "event", "market", "side", "entry_ts", "entry_price",
           "stake_usd", "fee_usd", "exit_kind", "exit_ts", "exit_price", "payout", "pnl_usd", "roi", "note"]

LEAGUE = {"cs2": "cs2", "lol": "lol", "dota2": "dota2", "valorant": "valorant", "other": "other"}


def load() -> pd.DataFrame:
    ser = pd.read_parquet(SRC / "series.parquet").set_index("event_slug")
    out = []
    for per in ("dev", "holdout"):
        b = pd.read_parquet(SRC / f"bets_{per}.parquet")
        b = b[b.rule == RULE].copy()
        assert (b.period == per).all(), per
        assert b.event_slug.is_unique, f"{per}: one bet per series expected"
        b["market_slug"] = ser.market_slug.reindex(b.event_slug).to_numpy()
        b["closed_ts"] = ser.closed_ts.reindex(b.event_slug).to_numpy()
        o0 = ser.o0.reindex(b.event_slug).to_numpy()
        o1 = ser.o1.reindex(b.event_slug).to_numpy()
        L = b.L.to_numpy(int)
        b["team_T"] = np.where(L == 0, o1, o0)     # the trailer: the side the rule buys
        b["team_L"] = np.where(L == 0, o0, o1)     # the map-1 leader
        out.append(b)
    d = pd.concat(out, ignore_index=True)
    return d.sort_values("entT_ts", kind="stable").reset_index(drop=True)


def rows(d: pd.DataFrame) -> tuple[list[list], dict]:
    c = d.q.to_numpy(float)                                    # fill price actually used by the backtest
    rate = np.nan_to_num(d.rate.to_numpy(float))
    won = d.won.to_numpy(float)                                # series payout for the trailer: 1 / 0 (0.5 on void)
    f = np.asarray(C.taker_fee(1.0, c, rate))                  # taker fee on ONE share
    shares = 1.0 / (c + f)                                     # $1.00 of deployed capital per bet (price + fee)
    stake, fee = shares * c, shares * f
    payout = shares * won
    pnl = payout - stake - fee
    ref = np.asarray(C.taker_roi(d.q, d.won, d.rate))          # the report's own per-bet return
    assert np.abs(pnl - ref).max() < 1e-12, "pnl_usd must equal C.taker_roi per bet"

    gap = (d.P_break - d.P_cal).to_numpy(float)
    ts = d.entT_ts.to_numpy(float)
    out = []
    for i in range(len(d)):
        r = d.iloc[i]
        how = str(r.how)
        note = (f"gap +{gap[i]:.3f} (P_break {r.P_break:.3f} vs calibrated {r.P_cal:.3f} on {int(r.n_brk)} break "
                f"fills); map-1 leader {r.team_L} at pregame {float(r.P0):.2f}; entry {int(r.delay_s)}s after "
                f"map-1 end; ")
        note += ("first taker print acquiring the trailer inside [t_end+153, t_end+600]s, "
                 f"${float(r.first_usd):,.0f} at that print, ${float(r.cap):,.0f} of trailer prints at <= entry+1c "
                 "in the window"
                 if how == "window" else
                 "QUOTE-PROXY FILL: nobody printed on the trailer inside [t_end+153, t_end+600]s, so the bet is "
                 "filled at 1 - (last leader print in the break window) + 1c, dated t_end+153")
        note += f"; fee rate {rate[i]:.2f}"
        if won[i] == 0.5:
            note += "; series voided, paid 0.5"
        out.append([
            i + 1, str(r.period), pd.to_datetime(ts[i], unit="s", utc=True).strftime("%Y-%m-%d"),
            "esports", LEAGUE.get(str(r.title), str(r.title)), str(r.event_slug), str(r.market_slug),
            f"{r.team_T} (trailing team, taker buy)", int(ts[i]), round(float(c[i]), 6),
            round(float(stake[i]), 6), round(float(fee[i]), 6),
            "resolution", int(r.closed_ts), float(won[i]), round(float(payout[i]), 6),
            round(float(pnl[i]), 6), round(float(pnl[i] / stake[i]), 6), note,
        ])

    head = {}
    for per in ("dev", "holdout"):
        m = (d.period == per).to_numpy()
        roi, lo, hi = C.cluster_ci(ref[m], d.event_slug[m])
        head[per] = {"bets": int(m.sum()), "roi": round(roi, 4), "ci_lo": round(lo, 4), "ci_hi": round(hi, 4),
                     "pnl_usd": round(float(pnl[m].sum()), 4)}
    return out, head


def build() -> dict:
    d = load()
    rr, head = rows(d)
    assert len(rr) <= MAX_ROWS
    return {
        "slug": SLUG,
        "title": "Esports BO3 between-map break overreaction",
        "group": "Thorp hypotheses",
        "sport": "esports",
        "verdict": "PROMISING",
        "hypothesis": (
            "The break between map 1 and map 2 of a BO3 is a scheduled no-information window; retail piles onto the "
            "map-1 winner while the structural facts (next map pick, side/draft priority) are neutral or favour the "
            "loser, so the series price of the trailing team is pushed too low and buying it collects the pressure."),
        "mechanism": (
            "The map-1 result is public and dramatic, and the break is minutes of dead time with nothing new to "
            "price, so momentum-following takers move the series line further than a map-by-map model justifies. "
            "The counterparty is a retail taker extrapolating one map; we are paid for standing on the other side "
            "of that extrapolation. The effect only survived in Dota 2 (and partly Valorant), where the map-1 "
            "winner is ~4-5c too expensive at the break in both periods; in CS2 and LoL the sign is reversed, so "
            "the pre-registered mechanism is wrong outside Dota 2 and the rule reaches it via the title dummies."),
        "entry_rule": (
            "Universe: esports series moneylines with pregame volume >= $25k that are BO3 (the event lists -game1, "
            "-game2 and -total-games-2pt5, and none of -game4/-game5/-total-games-3pt5/-total-games-4pt5). "
            "Map-1 end t_end = first game-1 child fill whose implied price of either outcome is >= 0.99; that "
            "outcome is the leader L, the other is the trailer T. Drop the series if t_end < scheduled start + 10 "
            "min (round-2 hygiene fix). Model: P0 = L's pregame series mid oriented to L, clipped to [0.01,0.99]; "
            "solve P0 = p^2(3-2p); fair_iid = 1-(1-p)^2; P_cal = DEV-only frozen logistic regression of 1{L wins "
            "the series} on logit(fair_iid) plus title dummies (const +0.295, logit +0.861, LoL +0.055, Dota 2 "
            "-0.414, Valorant -0.225; CS2 base, n=1,313). Signal: P_break = median L-oriented series fill price over "
            "[t_end+30, t_end+150]s with >= 3 fills; if P_break - P_cal >= +0.04, BUY THE TRAILER T. Decision at "
            "t_end+150, +3s latency. Fill = the first series fill acquiring T in [t_end+153, t_end+600]s, at that "
            "fill's price, as a taker; if no such print exists the bet is filled at a quote proxy built only from "
            "pre-decision prints (1 - the last L-acquiring print in the break window, + 1c spread) and dated "
            "t_end+153. One bet per series, flat $1 of deployed capital (price + fee)."),
        "exit_rule": (
            "Held to resolution of the series moneyline. exit_price is the market payout for the trailer: 1 if the "
            "trailing team came back to win the series, 0 if the map-1 leader closed it out, 0.5 on a void. No "
            "in-play exit, no stop, no markout: every row is a real held position, not a maker fill or a markout "
            "measurement. exit_ts is the market's closed_ts."),
        "cost_model": (
            "Polymarket taker fee, charged at each market's own fee_rate on the fill: fee = shares * rate * p * "
            "(1-p). Rates in this sample are 0.00 (2025 markets), 0.03 (Mar-Jun 2026) and 0.05 (Jul 2026-). No "
            "maker rebate. Entry price is the actual printed price with NO slippage added, which is the report's "
            "headline convention; the report also shows a +1c slippage sensitivity (DEV +55.5%, HOLDOUT +22.8%) "
            "that is not in these rows. Each row deploys $1.00 of capital, so stake_usd + fee_usd = 1.00 and "
            "pnl_usd is exactly the report's per-bet return."),
        "periods": {"dev": "2025-01-01..2026-06-30", "holdout": "2026-07-01..2026-09-18"},
        "headline": {
            "dev": {"bets": head["dev"]["bets"], "roi": head["dev"]["roi"], "ci_lo": head["dev"]["ci_lo"],
                    "ci_hi": head["dev"]["ci_hi"], "pnl_usd": head["dev"]["pnl_usd"]},
            "holdout": {"bets": head["holdout"]["bets"], "roi": head["holdout"]["roi"],
                        "ci_lo": head["holdout"]["ci_lo"], "ci_hi": head["holdout"]["ci_hi"],
                        "pnl_usd": head["holdout"]["pnl_usd"]},
        },
        "review": (
            "Round 1 was reviewed as DEAD and the reviewer then found the reason was itself a bug: the entry rule "
            "dropped a signal when nobody else printed on our side within 600s of the decision, which is selection "
            "on post-decision activity, and the one dropped holdout signal was a winner. The reviewer also found "
            "that the pregame mid could overlap play when map 1 ended within 10 minutes of the scheduled start. "
            "Both were accepted and fixed (quote-proxy fallback, 10-minute hygiene filter, DEV-only refit of the "
            "calibration, all recorded in round2_precommit.txt before the round-2 holdout run), which moved the "
            "verdict to PROMISING. The reviewer cleared the DEV-only calibration, the leader detection (98%), the "
            "payout orientation, the >= 3 break-fill filter, the BO3/BO5 markers and the $50k volume gate as not "
            "material. The reviewers' standing objection is that the result is noise-dominated: the DEV CI is "
            "in-sample, cross-fitting it puts it across zero, and the holdout sign depends on which of two "
            "defensible calibrations is frozen (+29.3% vs +3.5%)."),
        "caveats": [
            "Noise-dominated, not an established edge. Cross-fitted DEV is +5.8% [-38.7, +58.0]; over 20 fold draws "
            "the CI lower bound clears zero in only 4 of 20.",
            "The holdout was looked at three times (round 1, the reviewer's recomputation, round 2). The round-2 "
            "choices were pre-committed in writing, but the holdout numbers should be read as descriptive.",
            "Calibration knife edge: the round-1 calibration with the same execution fix gives +3.5% [-44.6, +61.3] "
            "on 60 holdout bets instead of +29.3% on 53. The difference is 9 marginal Dota 2 bets that fell under "
            "the 0.04 threshold on the refit and all 9 lost (about a 4% event if prices were fair), plus 2 added "
            "Valorant bets.",
            "Three holdout series carry the result: dropping the best 3 gives -5.8%, the best 5 gives -24.8%. On "
            "DEV, dropping the best 3 gives +29.5%.",
            "These are ~20c longshots (holdout average price 0.203, win rate 0.28), so the P&L is dominated by a "
            "handful of 5-15c winners and the CI half-width is about 60 points on 53 bets.",
            "The pre-registered momentum mechanism is wrong in CS2 and LoL, where the map-1 winner is CHEAP at the "
            "break. The rule trades almost only Dota 2 and Valorant, through the calibration's title dummies. "
            "Holdout by title: Dota 2 34 bets +30.7%, Valorant 14 bets +40.2%, CS2 4 bets +11%, LoL 1 bet -100%.",
            "Only the pre-registered primary rule (gap >= +0.04, buy the trailer, executed entry) is exported. The "
            "variants are in the report and are NOT in these rows: threshold 0.03 (holdout 79 bets +8.0%) and 0.06 "
            "(14 bets +46.7%); the mirror, buy the leader when the gap is <= -0.04 (holdout 230 bets +6.4% [-3.8, "
            "+16.5]), which is the steadier lead; the placebo (holdout 246 bets -12.6%); the all-trailers and "
            "all-leaders baselines; the synthetic-decider variant (e); the next-print-no-cap and quote-proxy-for-"
            "every-bet entry conventions; and the cross-fitted calibration.",
            "One holdout row (dota2-re-lgd-2026-08-03) is a quote-proxy fill, not an observed trade: nobody printed "
            "on the trailer inside the entry window, so it is filled at the implied bid + 1c and dated t_end+153. "
            "Its note says so. Pricing every holdout bet at the quote proxy gives +34.0%, so the fill convention is "
            "not what makes the result positive.",
            "entry_price carries NO slippage: it is the actual print. The report's +1c sensitivity (DEV +55.5%, "
            "HOLDOUT +22.8%) is not reflected in these rows.",
            "stake_usd + fee_usd = $1.00 per row, because the report's ROI is the return on capital deployed "
            "(price + fee). The `roi` column is pnl_usd/stake_usd as the spec defines it, so it excludes the fee "
            "from the denominator and averages ~2-3 points above the headline ROI; mean(pnl_usd) is the headline.",
            "t_end is a proxy for the map end (first game-1 print at or above 0.99); about 2% of series get the "
            "wrong leader from glitch prints. Capacity is thin: 0.66 bets a day, a median of $7.9 at the first "
            "print and about $255 per series of trailer buying inside the entry window.",
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
    assert df.entry_ts.is_monotonic_increasing


def main() -> None:
    led = build()
    verify(led)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(led, separators=(",", ":")))
    print(f"wrote {OUT}  rows={led['n_total_trades']}  {OUT.stat().st_size / 1024:.0f} KiB")


if __name__ == "__main__":
    main()
