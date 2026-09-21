"""Per-trade ledger export for hypothesis `late_draw_theta` (see pmsports/research/LEDGER_SPEC.md).

Reads the ACTUAL bets the backtest made -- `data/research/h_late_draw_theta/bets_dev.parquet` (the 415
development bets) and the `per == "hold"` rows of `bets_holdout.parquet` (the 158 holdout bets), which are the
pre-registered PRIMARY rule (taker, signal at KO+95 min, p in [0.40, 0.90], no slippage) -- joins the draw leg's
market slug and resolution time from `universe.parquet`, and writes `data/research/ledgers/late_draw_theta.json`.

Nothing is re-simulated. `px` (the entry price), `fee_rate`, `y_draw` (the Yes-token payout of the draw leg),
`t_entry`, `t0`, `p0`, `cap_usd` etc. come straight out of the saved bet rows that produced the report's headline;
the only arithmetic here is the dollar framing. `bets_holdout.parquet` also contains the 415 development rows and
they are asserted to be identical to `bets_dev.parquet`.

Dollar framing. The study's metric is `C.taker_roi` = (won - c - f) / (c + f) per bet, equal-weighted, where c is
the fill price and f = fee_rate * c * (1 - c) is the taker fee on one share. That is a flat $1 of CAPITAL DEPLOYED
(price + fee) per bet. So each ledger row buys shares = 1 / (c + f), giving

    stake_usd + fee_usd = $1.00 exactly, and
    pnl_usd = payout - stake_usd - fee_usd = C.taker_roi(c, won, fee_rate)   (the identity the report's ROI averages)

Hence mean(pnl_usd) over a period IS the report's headline ROI, and sum(pnl_usd) is that ROI times the bet count.
The per-row `roi` column is pnl_usd / stake_usd as the spec defines it; because the spec's denominator excludes the
fee while the report's includes it, the stake-weighted roi runs a touch below the headline (more negative, since
these bets lose). Both numbers are printed by the verify step and the difference is disclosed in `caveats`.

Run:
  PYTHONPATH=. systemd-run --user --scope -q -p MemoryMax=5G -p MemorySwapMax=0 .venv/bin/python \
      -m pmsports.research.ledger_late_draw_theta
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from pmsports.research import common as C

SLUG = "late_draw_theta"
SRC = C.RESEARCH / f"h_{SLUG}"
OUT = C.RESEARCH / "ledgers" / f"{SLUG}.json"
MAX_ROWS = 20_000

COLUMNS = ["id", "period", "date", "sport", "league", "event", "market", "side", "entry_ts", "entry_price",
           "stake_usd", "fee_usd", "exit_kind", "exit_ts", "exit_price", "payout", "pnl_usd", "roi", "note"]

GROUP_LABEL = {"top5_league": "top-5 league", "other_league": "other league", "uefa_club": "UEFA club",
               "mls/liga_mx": "MLS/Liga MX", "internationals": "internationals",
               "world_cup_group": "World Cup group stage", "world_cup_KO": "World Cup knockout",
               "domestic_cup/super_cup": "domestic cup / super cup"}


def load() -> pd.DataFrame:
    """The primary-rule bets, dev + holdout, with the draw leg's market slug and resolution time attached."""
    dev = pd.read_parquet(SRC / "bets_dev.parquet")
    hold_run = pd.read_parquet(SRC / "bets_holdout.parquet")
    hold = hold_run[hold_run.per == "hold"].copy()

    # the holdout run re-computes the development bets; they must be the same rows
    a = dev.sort_values("event_slug").reset_index(drop=True)
    b = hold_run[hold_run.per != "hold"].sort_values("event_slug").reset_index(drop=True)
    assert a.equals(b), "dev bets differ between the dev run and the holdout run"

    dev = dev.assign(period="dev")
    hold = hold.assign(period="holdout")
    d = pd.concat([dev, hold], ignore_index=True)
    assert d.event_slug.is_unique, "one bet per event expected"
    assert d.in_band.all()

    u = C.universe(columns=["event_slug", "market_slug", "outcome", "closed_ts", "payout"])
    u = u[u.event_slug.isin(set(d.event_slug)) & u.market_slug.str.endswith("-draw") & (u.outcome == "Yes")]
    u = u.drop_duplicates("event_slug").set_index("event_slug")
    d["market_slug"] = u.market_slug.reindex(d.event_slug).to_numpy()
    d["closed_ts"] = u.closed_ts.reindex(d.event_slug).to_numpy()
    assert not pd.isna(d.market_slug).any()
    # the payout in the bets file must be the draw leg's Yes payout in the universe
    assert np.allclose(u.payout.reindex(d.event_slug).to_numpy(), d.y_draw.to_numpy())
    assert (d.closed_ts > d.t_entry).all(), "resolution must follow entry"
    return d.sort_values("t_entry", kind="stable").reset_index(drop=True)


def rows(d: pd.DataFrame) -> tuple[list[list], dict]:
    c = d.px.to_numpy(float)                                   # fill price actually used by the backtest
    rate = np.nan_to_num(d.fee_rate.to_numpy(float))
    won = d.y_draw.to_numpy(float)                             # draw-Yes payout: 1 = 90-minute draw, else 0
    f = np.asarray(C.taker_fee(1.0, np.clip(c, 0.001, 0.999), rate))   # taker fee on ONE share
    shares = 1.0 / (np.clip(c, 0.001, 0.999) + f)              # $1.00 of deployed capital per bet (price + fee)
    stake, fee = shares * np.clip(c, 0.001, 0.999), shares * f
    payout = shares * won
    pnl = payout - stake - fee
    ref = np.asarray(C.taker_roi(d.px, d.y_draw, d.fee_rate))  # the report's own per-bet return
    assert np.abs(pnl - ref).max() < 1e-12, "pnl_usd must equal C.taker_roi per bet"

    ts = d.t_entry.to_numpy(np.int64)
    out = []
    for i in range(len(d)):
        r = d.iloc[i]
        sig_side = "draw-Yes" if int(r.s0) == 0 else "draw-No"
        note = (f"signal: first draw print at KO+{r.rel0 / 60:.1f} min (taker acquired {sig_side} at "
                f"{float(r.q0):.2f}), Yes-converted p {float(r.p0):.2f} inside [0.40, 0.90]; entry "
                f"{int(r.delay)} s later at the HIGHEST Yes print of that second ({float(r.px):.4f}"
                + (f"; lowest {float(r.px_min):.4f})" if abs(float(r.px_min) - float(r.px)) > 1e-9 else ")")
                + f"; ${float(r.cap_usd):,.0f} / {float(r.cap_sh):,.0f} sh of Yes-acquiring taker prints at <= "
                  f"entry+1c inside the 5-min window; draw tape {'local' if bool(r.local) else 'Data-API fetched'} "
                  f"(draw leg volume ${float(r.draw_volume) / 1000:,.1f}k, larger team leg pregame "
                  f"${float(r.pre_team) / 1000:,.1f}k); {GROUP_LABEL.get(str(r.grp), str(r.grp))}"
                  f"{' (knockout: draw-Yes still pays on the 90-minute result)' if bool(r.knockout) else ''}; "
                  f"fee rate {rate[i]:.4g}; "
                + ("resolved DRAW, paid 1" if won[i] == 1 else "resolved not a draw, paid 0"))
        out.append([
            i + 1, str(r.period), pd.to_datetime(ts[i], unit="s", utc=True).strftime("%Y-%m-%d"),
            "soccer", str(r.league), str(r.event_slug), str(r.market_slug),
            "Draw Yes (taker buy)", int(ts[i]), round(float(c[i]), 6),
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
        "title": "Late-match draw underpricing in tied soccer games",
        "group": "Thorp hypotheses",
        "sport": "soccer",
        "verdict": "DEAD",
        "hypothesis": (
            "Late in a tied soccer match the draw's fair value climbs every goalless minute, but holders of "
            "'Will X win? Yes' keep holding, hope buyers keep chasing a late winner, and in knockouts fans misread "
            "'win' as 'advance' when the contract actually settles on the 90-minute result -- so draw-Yes should be "
            "left too cheap around match minute 78, and simply buying it should collect the difference."),
        "mechanism": (
            "The counterparty is a retail holder or hope buyer of a team-win leg who is anchored to the result they "
            "want and is slow to re-price the rising probability of a stalemate; in cup ties the contract wording "
            "adds a second source of confusion, because a team that wins on penalties or in extra time still pays 0 "
            "on its 90-minute 'win' leg while the draw pays 1. Both would push draw-Yes below its true hit rate. "
            "The data says neither shows up: the draw came in 53.7% (dev) and 55.1% (holdout) of the time against "
            "average entry prices of 55.2c and 55.0c, so the 'theta' is already in the price and there is nothing "
            "to collect even before fees."),
        "entry_rule": (
            "Universe (pregame information only): soccer events with Yes/No legs whose LARGER team-win leg has "
            "pregame volume pre_usd >= $50k -- 2,053 events, 1,512 dev (kickoff < 2026-07-01) and 541 holdout. The "
            "event's draw leg is always taken from the universe, and its fills come from the local tape when that "
            "leg is in markets() (1,124 legs) and are otherwise fetched from the Data API for [KO+50, KO+180] min "
            "(929 legs, 115k fills, 0 errors), so inclusion never depends on draw volume. Fills are normalized by "
            "token id (taker acquired draw-Yes or draw-No at q), not by the Data API's outcomeIndex. SIGNAL: the "
            "first draw-leg fill with ts >= KO+95 min, searched in [KO+95, KO+180] min; p = its Yes-converted "
            "price; if 0.40 <= p <= 0.90 then t0 = that fill's ts (median t0 is KO+95.2 min, roughly match minute "
            "77-80). ENTRY: buy draw-Yes as a TAKER at the first Yes-acquiring fill with ts in [t0+3 s, t0+300 s]; "
            "the price is the HIGHEST Yes price printed in that second (the end of a sweep, the conservative "
            "choice; taking the lowest changes ROI by less than 0.1 pt). Median entry delay 14-15 s, p90 64-82 s. "
            "One bet per event, flat $1 of deployed capital (price + fee). Bets = 415 dev, 158 holdout; the funnel "
            "is 1,512 -> 1,497 with a fill after KO+95 -> 420 in band -> 415 entries found (dev) and 541 -> 530 -> "
            "160 -> 158 (holdout)."),
        "exit_rule": (
            "Held to resolution of the draw leg. Every row is a REAL HELD POSITION bought from a taker print -- not "
            "a maker fill, not a markout measurement -- so exit_kind is 'resolution' on all 573 rows. exit_price is "
            "the draw-Yes token payout: 1 if the match was level after 90 minutes, 0 otherwise. In knockouts this "
            "still pays 1 on a 90-minute draw even when a team then advances on extra time or penalties (checked "
            "against the team legs, e.g. Cultural Leonesa vs Athletic in the Copa del Rey, where the draw paid 1 "
            "and both 'win' legs paid 0). No in-play exit, no stop, no partial fills. exit_ts is the draw market's "
            "closed_ts."),
        "cost_model": (
            "Polymarket taker fee at each market's own fee_rate on the fill: fee = shares * fee_rate * p * (1-p). "
            "Rates present in these rows are 0.00 (2025 markets), 0.0175 and 0.03 (Mar-Jun 2026) and 0.05 (Jul "
            "2026-); the average fee is 0.26c/share in dev and 1.12c/share in the holdout. No maker rebate (this is "
            "a taker rule). entry_price is the actual printed price with NO slippage added, which is the report's "
            "headline convention; the report's +1c and +2c stresses (dev -5.37% / -7.04%, holdout -3.74% / -5.47%) "
            "are NOT in these rows. Each row deploys $1.00 of capital, so stake_usd + fee_usd = 1.00 and pnl_usd is "
            "exactly the report's per-bet return."),
        "periods": {"dev": "2025-09-09..2026-06-30", "holdout": "2026-07-02..2026-09-18"},
        "headline": {
            "dev": {"bets": head["dev"]["bets"], "roi": head["dev"]["roi"], "ci_lo": head["dev"]["ci_lo"],
                    "ci_hi": head["dev"]["ci_hi"], "pnl_usd": head["dev"]["pnl_usd"]},
            "holdout": {"bets": head["holdout"]["bets"], "roi": head["holdout"]["roi"],
                        "ci_lo": head["holdout"]["ci_lo"], "ci_hi": head["holdout"]["ci_hi"],
                        "pnl_usd": head["holdout"]["pnl_usd"]},
        },
        "review": (
            "None of the three adversarial reviewers (look-ahead/selection, execution/fees/capacity, statistics) "
            "refuted the DEAD verdict. The selection reviewer's finding was the one that mattered before review: an "
            "earlier +2.4c 'peek' came from a sample restricted to draw legs with >= $50k of their own volume, "
            "which is partly outcome-driven, so the rule was rebuilt to fetch every draw leg -- on the full "
            "unbiased sample the local-leg subset returns +0.35% in dev against -12.4% on the fetched legs, and the "
            "whole sample is negative. The statistics reviewer noted that with 415 and 158 near-coin-flip bets the "
            "test can only detect edges of roughly 13-21 points, so this should be read as 'no detectable edge' "
            "rather than proof of exactly zero; a +2c edge is unlikely but not ruled out."),
        "caveats": [
            "DEAD, and the point of the ledger is a negative result: the draw is priced at its hit rate. Dev 53.7% "
            "draws vs 55.2c average price; holdout 55.1% vs 55.0c. Before fees the edge is -1.50c (dev) and +0.09c "
            "(holdout), i.e. zero, and the actual taker fee then makes it negative.",
            "The CIs are wide -- the holdout half-width is about 14 points -- because every bet is a single "
            "near-50/50 outcome. The test's power is about 13-21 points, so these 573 rows cannot rule out a small "
            "positive edge; they do rule out an edge big enough to clear about 2c of fee plus spread.",
            "Only the pre-registered PRIMARY rule (taker, KO+95, p in [0.40,0.90], no slippage) is exported. The "
            "variants in the report are NOT in these rows: (a) the maker bid at p-1c filled on touch (dev 231 bets "
            "-6.78%, holdout 58 bets -11.06%) and its back-of-queue trade-through stress (164 bets -13.84%, 44 bets "
            "-19.02%); (b) the early window, first fill >= KO+60 with p in [0.20,0.45] (806 bets -0.89%, 269 bets "
            "+0.38%); (c) the knockout / league split; (d) +1c and +2c slippage; (e) team-leg pregame >= $100k (288 "
            "bets -4.15%, 88 bets -0.37%).",
            "The maker variant is the clearest evidence against the mechanism and is deliberately absent here: a "
            "resting draw bid fills on only 56% (dev) and 37% (holdout) of signals, and those filled bets draw just "
            "49.8% and 48.3% of the time against 53.7% and 55.1% for all signals -- the bid gets hit when a goal "
            "goes in. Any real maker implementation inherits that adverse selection.",
            "Kickoff is the SCHEDULED game_start_ts, so a delayed kickoff moves the KO+95 min signal earlier in the "
            "match. This is identical in both periods and is part of the pre-registered rule.",
            "The entry is the next Yes print within 300 s. If a goal falls between t0+3 s and that print, the "
            "simulated entry is at the post-goal price. Per $1 a loss is -100% at any price, so this is roughly "
            "neutral; the entries-within-30 s subset shows the same picture (dev -4.3%, holdout +1.3%).",
            "Subgroup numbers in the report are noise, not signal: no league group is positive in both periods with "
            "more than a handful of bets, World Cup knockouts went 1-in-4 in dev and 6-in-10 in the holdout, and "
            "domestic cups are -58% / -40% on 9 and 8 bets. Do not mine these rows by group.",
            "stake_usd + fee_usd = $1.00 per row, because the report's ROI is the return on capital deployed "
            "(price + fee). The `roi` column is pnl_usd/stake_usd as the spec defines it, so it excludes the fee "
            "from the denominator; mean(pnl_usd) is the headline ROI exactly, while sum(pnl)/sum(stake) is about "
            "0.01-0.04 pt more negative.",
            "Capacity, had the edge existed: about 43 bets a month in dev and 62 in the holdout, with a median of "
            "$168 (dev) and $104 (holdout) of other takers' draw-Yes flow at or below entry+1c in the 5-minute "
            "window -- so a few hundred dollars a game. The per-row note carries that dollar figure.",
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
    assert df.entry_ts.is_monotonic_increasing
    # the report's own descriptive stats, straight off the rows
    for per, draw, px in (("dev", 0.5373, 0.5523), ("holdout", 0.5506, 0.5497)):
        g = df[df.period == per]
        print(f"  {per}: draw rate {g.exit_price.mean():.4f} (report {draw}), "
              f"avg entry price {g.entry_price.mean():.4f} (report {px}), "
              f"avg fee/share {(g.fee_usd / (g.stake_usd / g.entry_price)).mean():.4f}")
        assert abs(g.exit_price.mean() - draw) < 5e-5 and abs(g.entry_price.mean() - px) < 5e-5


def main() -> None:
    led = build()
    verify(led)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(led, separators=(",", ":")))
    print(f"wrote {OUT}  rows={led['n_total_trades']}  {OUT.stat().st_size / 1024:.0f} KiB")


if __name__ == "__main__":
    main()
