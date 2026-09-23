"""Where is the breaking point? Price vs realized win rate, per sport, and whether it pays.

python -m pmsports calibration  ->  reports/CALIBRATION.md (+ CSVs, charts, desk ledgers)

Part A (descriptive): every taker fill is "someone paid q for this side, and the side paid y".
Bucket by q and ask what fraction actually won. Fine buckets at the top end (90c..99c), split
pregame vs in-play and by sport.

Part B: a threshold print is a signal; a later same-side print is a size-bounded
execution PROXY. Neither price nor depth is a historical order-book observation.
The existing corpus has incomplete lifetime coverage; prior observed volume >=$50k
is a sensitivity floor, not proof of an unbiased universe. July2026 was already explored.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from ..polymarket import taker_fee
from ..execution import TapeReplay, prior_notional
from ..research.common import cluster_ci, fills, markets

log = logging.getLogger("pmsports")
REPORTS = Path(__file__).resolve().parents[2] / "reports"
LEDGERS = Path(__file__).resolve().parents[2] / "data" / "research" / "ledgers"
PREGAME_MIN_USD = 50_000
SPLIT_TS = pd.Timestamp("2026-07-01", tz="UTC").timestamp()
STAKE = 100.0
THRESHOLDS = [0.60, 0.70, 0.80, 0.85, 0.90, 0.925, 0.95, 0.97, 0.98, 0.99]
EDGES = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.925, 0.95, 0.96, 0.97, 0.98, 0.99, 1.0]
SPORTS = ["baseball", "soccer", "american_football", "basketball", "tennis", "esports", "hockey"]


def _load() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Compact fill frame: ids stay integer codes (35M event-slug strings would cost GBs)."""
    mk = markets()
    mk = mk[mk.game_start_ts.notna()]
    f = fills(markets=mk.m.to_numpy(), columns=["m", "s", "w", "ts", "q", "y", "size", "fee_rate", "in_play"])
    f["prior_usd"] = prior_notional(f)
    # Keep the complete valid tape for later execution and low-price mirror signals.
    keep = f.q.between(0, 1, inclusive="neither")
    f = f.loc[keep]
    meta = mk.set_index("m")
    sport_codes, sport_names = pd.factorize(meta.family.to_numpy())
    event_codes, _ = pd.factorize(meta.event_slug.to_numpy())
    pos = pd.Series(np.arange(len(meta)), index=meta.index)      # market code -> row in meta
    row = pos.reindex(f.m).to_numpy()
    f["sport"] = pd.Categorical.from_codes(sport_codes[row].astype(np.int16), categories=pd.Index(sport_names))
    f["event"] = event_codes[row].astype(np.int32)               # cluster key, numeric
    f["closed_ts"] = meta.closed_ts.to_numpy()[row]
    log.info("%d fills in %d markets (prior-volume sensitivity floor $%s), %.1f GB",
             len(f), f.m.nunique(), f"{PREGAME_MIN_USD:,}", f.memory_usage(deep=True).sum() / 1e9)
    return f, mk


def calibration_table(f: pd.DataFrame, by_sport: bool = True, phase: str | None = None) -> pd.DataFrame:
    columns = ["q", "y", "size", "fee_rate", "event"] + (["sport"] if by_sport else [])
    mask = f.y.ne(.5)
    if phase is not None:
        mask &= f.in_play.eq(phase == "in_play")
    d = f.loc[mask, columns]
    d = d[d.y != .5]  # descriptive win/loss calibration only
    d = d.assign(bucket=pd.cut(d.q, EDGES, include_lowest=True, right=False))
    keys = (["sport"] if by_sport else []) + ["bucket"]
    rows = []
    for k, g in d.groupby(keys, observed=True):
        if len(g) < 200:
            continue
        usd = (g["size"] * g.q).to_numpy()
        fee = taker_fee(1.0, g.q.to_numpy(), g.fee_rate.to_numpy())
        roi = (g.y.to_numpy() - g.q.to_numpy() - fee) / (g.q.to_numpy() + fee)
        capital = g["size"].to_numpy() * (g.q.to_numpy() + fee)
        mean, lo, hi = cluster_ci(roi, g.event.to_numpy(), weights=capital)
        rec = dict(zip(keys, k if isinstance(k, tuple) else (k,)))
        rec.update({
            "fills": len(g), "usd": float(usd.sum()), "capital_usd": float(capital.sum()), "avg_price": float(np.average(g.q, weights=usd)),
            "won_pct": float(np.average(g.y, weights=usd)), "fills_won_pct": float(g.y.mean()),
            "edge_pts": float(np.average(g.y, weights=usd) - np.average(g.q, weights=usd)),
            "roi_after_fee": mean, "ci_lo": lo, "ci_hi": hi,
        })
        rows.append(rec)
    out = pd.DataFrame(rows)
    if len(out):
        out["bucket"] = out.bucket.astype(str)
    return out


def threshold_bets(f: pd.DataFrame, thr: float, mirror: bool = False,
                   replay: TapeReplay | None = None) -> pd.DataFrame:
    """One signal per market; retain every no-fill and partial in the returned audit."""
    prior = f.prior_usd if "prior_usd" in f else pd.Series(prior_notional(f), index=f.index)
    eligible = prior >= PREGAME_MIN_USD
    d = f[eligible & ((f.q <= 1 - thr) if mirror else (f.q >= thr))]
    first = d.sort_values("ts", kind="stable").drop_duplicates("m").copy()
    orders = pd.DataFrame({"m": first.m, "s": first.s, "signal_ts": first.ts,
                           "leader_w": first.get("w"), "event": first.event, "y": first.y,
                           "budget_usd": STAKE})
    orders["expiry_ts"] = np.minimum(first.get("closed_ts", first.ts + 603), first.ts + 603)
    fills_out = (replay or TapeReplay(f)).replay(orders, event_cap_usd=STAKE)
    first = first.reset_index(drop=True)
    for col in fills_out:
        if col not in ("m", "s", "event", "y"):
            first[col] = fills_out[col].to_numpy()
    first["entry_ts"] = first.fill_ts
    first["period"] = np.where(first.ts < SPLIT_TS, "dev", "holdout")
    first["threshold"] = thr
    first["phase"] = np.where(first.in_play, "in_play", "pregame")
    return first


def threshold_table(f: pd.DataFrame, by_sport: bool = True) -> pd.DataFrame:
    rows = []
    replay = TapeReplay(f)
    for thr in THRESHOLDS:
        for mirror in (False, True):
            b = threshold_bets(f, thr, mirror, replay)
            if len(b) == 0:
                continue
            groups = list(b.groupby("sport", observed=True)) if by_sport else [("ALL", b)]
            for sport, g in groups + ([("ALL", b)] if by_sport else []):
                for period in ("dev", "holdout", "all"):
                    gg = g if period == "all" else g[g.period == period]
                    signals = len(gg)
                    gg = gg[gg.cost_usd > 0]
                    if len(gg) < 25:
                        continue
                    mean, lo, hi = cluster_ci(gg.roi, gg.event.to_numpy(), weights=gg.cost_usd.to_numpy())
                    rows.append({
                        "side": "underdog <= " + f"{1 - thr:.0%}" if mirror else "favorite >= " + f"{thr:.0%}",
                        "threshold": thr, "sport": sport, "period": period, "signals": signals, "bets": len(gg),
                        "avg_price": float(gg.entry_price.mean()), "won_pct": float(gg.y.mean()),
                        "edge_pts": float(gg.y.mean() - gg.entry_price.mean()),
                        "roi": mean, "ci_lo": lo, "ci_hi": hi,
                        "pnl_usd": float(gg.pnl_usd.sum()),
                        "in_play_share": float((gg.phase == "in_play").mean()),
                    })
    return pd.DataFrame(rows)


def chart(cal: pd.DataFrame, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    INK, INK2, GRID, SURF = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
    COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#4a3aa7", "#e34948"]
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.4), facecolor=SURF)
    ax = axes[0]
    ax.plot([0.5, 1], [0.5, 1], color=INK2, lw=1, ls="--", label="perfectly fair")
    for i, sp in enumerate([s for s in SPORTS if s in set(cal.sport)]):
        g = cal[(cal.sport == sp) & (cal.avg_price >= 0.5)].sort_values("avg_price")
        if len(g) > 2:
            ax.plot(g.avg_price, g.won_pct, color=COLORS[i % len(COLORS)], lw=2, marker="o", ms=4, label=sp)
    ax.set_xlim(0.5, 1.0)
    ax.set_ylim(0.4, 1.0)
    ax.set_title("Price paid vs how often that side won", color=INK, fontsize=11, loc="left")
    ax.set_xlabel("price paid", color=INK2, fontsize=9)
    ax.set_ylabel("actually won", color=INK2, fontsize=9)
    ax2 = axes[1]
    ax2.axhline(0, color=INK2, lw=1)
    for i, sp in enumerate([s for s in SPORTS if s in set(cal.sport)]):
        g = cal[(cal.sport == sp) & (cal.avg_price >= 0.5)].sort_values("avg_price")
        if len(g) > 2:
            ax2.plot(g.avg_price, 100 * g.roi_after_fee, color=COLORS[i % len(COLORS)], lw=2, marker="o", ms=4, label=sp)
    ax2.set_xlim(0.5, 1.0)
    ax2.set_title("Return per $1 at that price, after fees", color=INK, fontsize=11, loc="left")
    ax2.set_xlabel("price paid", color=INK2, fontsize=9)
    ax2.set_ylabel("% per $1", color=INK2, fontsize=9)
    for a in axes:
        a.set_facecolor(SURF)
        a.grid(color=GRID, lw=.6)
        a.tick_params(colors=INK2, labelsize=8)
        for s in a.spines.values():
            s.set_visible(False)
    axes[0].legend(frameon=False, fontsize=8, labelcolor=INK2, loc="upper left")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def write_ledger(f: pd.DataFrame, thr: float, mirror: bool = False,
                 bets: pd.DataFrame | None = None, replay: TapeReplay | None = None) -> None:
    b = threshold_bets(f, thr, mirror, replay) if bets is None else bets.copy()
    point = (1 - thr) * 100 if mirror else thr * 100
    label = f"{point:.4f}".rstrip("0").rstrip(".").replace(".", "p")
    slug = ("underdog_at_" if mirror else "favorite_at_") + label
    b = b.sort_values("ts").reset_index(drop=True)
    audit = pd.DataFrame({"id": np.arange(1, len(b) + 1), "period": b.period,
        "date": pd.to_datetime(b.ts, unit="s", utc=True).dt.strftime("%Y-%m-%d"),
        "sport": b.sport, "event": b.event, "market": b.m, "side": b.s,
        "entry_ts": b.fill_ts, "entry_price": b.entry_price, "stake_usd": b.stake_usd,
        "fee_usd": b.fee_usd, "exit_kind": "resolution", "exit_ts": b.get("closed_ts", np.nan),
        "exit_price": b.y, "payout": b.payout, "pnl_usd": b.pnl_usd, "roi": b.roi,
        "signal_ts": b.signal_ts, "receipt_ts": b.receipt_ts, "eligible_ts": b.eligible_ts,
        "expiry_ts": b.expiry_ts, "shares": b.shares, "print_id": b.print_id,
        "status": b.status, "note": "Later transaction proxy; unknown public receipt and depth"})
    head = {}
    for period, g in b.groupby("period"):
        filled = g[g.cost_usd > 0]
        mean, lo, hi = cluster_ci(filled.roi, filled.event.to_numpy(), weights=filled.cost_usd.to_numpy()) if len(filled) else (np.nan,) * 3
        head[period] = dict(signals=len(g), bets=len(filled), roi=mean, ci_lo=lo, ci_hi=hi,
                            pnl_usd=float(filled.pnl_usd.sum()), capital_usd=float(filled.cost_usd.sum()))
    doc = dict(slug=slug, title=f"{'Underdog' if mirror else 'Favorite'} threshold {thr:.0%}",
        group="Price thresholds", sport="multi", verdict="UNVALIDATED",
        hypothesis="Threshold-conditioned settlement return after delayed size-bounded entry",
        mechanism="Price calibration", entry_rule="First threshold signal per market; first later same-side other-wallet print after3s; inclusive$100/event cap; first-print partial fills",
        exit_rule="Settlement; expiry idealizes order eligibility and does not model pending-order cancellation",
        cost_model="Historical fill fee; budget includes fees", periods={"dev":"before2026-07-01", "holdout":"historically explored2026-07 onward"},
        caveats=["Observed cumulative volume strictly before signal >=$50k; incomplete lifetime coverage", "Tape capacity is not resting order-book depth", "Historical receipt timestamp is unobserved", "No-fill signals included with zero capital and PnL"],
        report_path="reports/CALIBRATION_POINTS.md", code_path="pmsports/analysis/calibration.py",
        truncated=False, n_total_trades=len(audit), headline=head, columns=list(audit),
        rows=json.loads(audit.to_json(orient="values")))
    # pandas handles missing values uniformly, including empty strategies.
    doc["headline"] = json.loads(pd.Series(head).to_json())
    LEDGERS.mkdir(parents=True, exist_ok=True)
    tmp = LEDGERS / f"{slug}.json.tmp"
    tmp.write_text(json.dumps(doc, separators=(",", ":"), allow_nan=False))
    tmp.replace(LEDGERS / f"{slug}.json")


def run() -> None:
    REPORTS.mkdir(exist_ok=True)
    f, mk = _load()
    cal_all = calibration_table(f)
    cal_pre = calibration_table(f, phase="pregame")
    cal_live = calibration_table(f, phase="in_play")
    overall = calibration_table(f, by_sport=False)
    thr = threshold_table(f)
    for name, df in [("calibration_by_sport", cal_all), ("calibration_pregame", cal_pre),
                     ("calibration_inplay", cal_live), ("calibration_overall", overall),
                     ("calibration_thresholds", thr)]:
        df.to_csv(REPORTS / f"{name}.csv", index=False)
    chart(cal_all, REPORTS / "calibration_curve.png")
    replay = TapeReplay(f)
    for t in THRESHOLDS:
        for mirror in (False, True):
            write_ledger(f, t, mirror=mirror, replay=replay)
    (REPORTS / "CALIBRATION.md").write_text(_render(f, overall, cal_all, cal_pre, cal_live, thr))
    log.info("wrote %s", REPORTS / "CALIBRATION.md")


def _md(df: pd.DataFrame, cols=None, fmt=".3f") -> str:
    d = df if cols is None else df[cols]
    return d.to_markdown(index=False, floatfmt=fmt) if len(d) else "_no data_"


def _render(f, overall, cal, pre, live, thr) -> str:
    CAL_COLS = ["bucket", "fills", "usd", "avg_price", "won_pct", "edge_pts", "roi_after_fee", "ci_lo", "ci_hi"]
    top = thr[(thr.period == "holdout") & (thr.sport == "ALL")]
    L = ["# The breaking point: price vs. reality, by sport", "",
         f"Every taker fill in {f.m.nunique():,} sports markets ({len(f):,} fills, 2025-01..2026-09), where the "
         "price paid and outcomes are known. Legacy collection is incomplete and selected by eventual volume. "
         f"Trading sensitivity uses >=${PREGAME_MIN_USD:,} observed strictly before each signal. Strategy voids settle at actual payout.",
         "",
         "`won_pct` is dollar-weighted: of every $1 spent at that price, how much came back as a winner. "
         "`edge_pts` = won_pct - avg_price (positive means the side won more often than it cost). "
         "`roi_after_fee` is the return per $1 including the market's actual taker fee, with a 95% CI "
         "clustered by game.", "",
         "## 1. All sports together", "", _md(overall, CAL_COLS), "",
         "![calibration](calibration_curve.png)", "",
         "## 2. By sport (all fills, pregame and in-play)", "", _md(cal, ["sport"] + CAL_COLS), "",
         "## 3. Pregame only", "", _md(pre, ["sport"] + CAL_COLS), "",
         "## 4. In-play only", "", _md(live, ["sport"] + CAL_COLS), "",
         "## 5. Delayed transaction-proxy threshold replay", "",
         "First crossing signals an order; a later same-side other-wallet print after3s caps shares. "
         "$100/event inclusive fees; partial and no-fill retained. July2026 is historically explored, not fresh confirmation.", "",
         _md(thr[thr.sport == "ALL"], ["side", "threshold", "period", "bets", "avg_price", "won_pct",
                                        "edge_pts", "roi", "ci_lo", "ci_hi", "in_play_share"]), "",
         "### Per sport (holdout)", "",
         _md(thr[(thr.period == "holdout") & (thr.sport != "ALL")],
             ["side", "threshold", "sport", "bets", "avg_price", "won_pct", "edge_pts", "roi", "ci_lo", "ci_hi"]), "",
         "## What to read here", "",
         "- A price level only pays if `won_pct` exceeds `avg_price` by more than the fee. The fee at 95c is "
         "0.24c per share (5% x p x (1-p)), so a 95c favourite must win **more than ~95.3%** of the time.",
         "- `edge_pts` near zero across a row means the market is right at that price and there is nothing to take.",
         "- In-play prices at the extremes are where 'the team never loses' intuitions live; the in-play table "
         "is the one to check for that.", ""]
    return "\n".join(L)


# ----------------------------------------------------------------------------- point-by-point

def per_point(f: pd.DataFrame, by: str | None = None, min_fills: int = 400) -> pd.DataFrame:
    """Calibration at 1-percentage-point resolution from 50c up."""
    columns = ["q", "y", "fee_rate", "size", "event"] + ([by] if by else [])
    d = f.loc[f.q >= 0.50, columns]
    # Compare to edges in the stored price precision: float32(0.53)*100 can
    # round below 53, even though it represents the nominal 53-cent boundary.
    edges = (np.arange(50, 101) / 100.).astype(d.q.dtype)
    d["pt"] = np.clip(np.searchsorted(edges, d.q.to_numpy(), side="right") + 49, 50, 99)
    keys = ([by] if by else []) + ["pt"]
    rows = []
    for k, g in d.groupby(keys, observed=True):
        if len(g) < min_fills:
            continue
        usd = (g["size"] * g.q).to_numpy()
        fee = taker_fee(1.0, g.q.to_numpy(), g.fee_rate.to_numpy())
        roi = (g.y.to_numpy() - g.q.to_numpy() - fee) / (g.q.to_numpy() + fee)
        capital = g["size"].to_numpy() * (g.q.to_numpy() + fee)
        mean, lo, hi = cluster_ci(roi, g.event.to_numpy(), weights=capital)
        rec = dict(zip(keys, k if isinstance(k, tuple) else (k,)))
        rec.update({"fills": len(g), "games": int(g.event.nunique()), "usd": float(usd.sum()), "capital_usd": float(capital.sum()),
                    "avg_price": float(np.average(g.q, weights=usd)),
                    "won_pct": float(np.average(g.y, weights=usd)),
                    "edge_pts": float(np.average(g.y, weights=usd) - np.average(g.q, weights=usd)),
                    "roi": mean, "ci_lo": lo, "ci_hi": hi})
        rows.append(rec)
    return pd.DataFrame(rows)


def per_point_split(f: pd.DataFrame) -> pd.DataFrame:
    """Same, but dev vs holdout side by side: a real sweet spot must survive out of sample."""
    columns = ["q", "y", "fee_rate", "size", "event"]
    dev = per_point(f.loc[f.ts < SPLIT_TS, columns], min_fills=200).set_index("pt")
    hold = per_point(f.loc[f.ts >= SPLIT_TS, columns], min_fills=200).set_index("pt")
    out = dev.join(hold, lsuffix="_dev", rsuffix="_hold", how="inner").reset_index()
    out["both_positive"] = (out.roi_dev > 0) & (out.roi_hold > 0)
    return out


def _records(f: pd.DataFrame) -> pd.DataFrame:
    """Eligible record-high SIGNALS only; execution must still use the complete tape."""
    prior = f.prior_usd if "prior_usd" in f else pd.Series(prior_notional(f), index=f.index)
    d = f[prior >= PREGAME_MIN_USD].sort_values(["m", "ts"], kind="stable")
    prev = d.groupby("m", sort=False).q.cummax().groupby(d.m).shift()
    return d[prev.isna() | d.q.gt(prev)].copy()


def threshold_sweep(f: pd.DataFrame, points=range(50, 100), export_ledgers=False) -> pd.DataFrame:
    """Every threshold uses the identical chronological execution path, full audits optional."""
    replay = TapeReplay(f)
    rows = []
    for pt in points:
        first = threshold_bets(f, pt / 100., replay=replay)
        if export_ledgers:
            write_ledger(f, pt / 100., bets=first)
        for period in ("dev", "holdout"):
            signals = first[first.period == period]
            g = signals[signals.cost_usd > 0]
            mean, lo, hi = cluster_ci(g.roi, g.event.to_numpy(), weights=g.cost_usd.to_numpy()) if len(g) else (np.nan,) * 3
            rows.append(dict(threshold_pct=pt, period=period, signals=len(signals), bets=len(g),
                unfilled=int((signals.cost_usd == 0).sum()), partial=int((signals.status == "partial").sum()),
                avg_price=g.entry_price.mean(), won_pct=g.y.mean(), edge_pts=(g.y - g.entry_price).mean(),
                roi=mean, ci_lo=lo, ci_hi=hi, pnl_usd=g.pnl_usd.sum(), capital_usd=g.cost_usd.sum()))
    return pd.DataFrame(rows)


def point_chart(pts: pd.DataFrame, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    INK, INK2, GRID, SURF = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
    BLUE, ORANGE = "#2a78d6", "#eb6834"
    fig, ax = plt.subplots(figsize=(10.5, 4.6), facecolor=SURF)
    ax.axhline(0, color=INK2, lw=1)
    ax.fill_between(pts.pt, 100 * pts.ci_lo, 100 * pts.ci_hi, color=BLUE, alpha=.15, lw=0,
                    label="95% CI (clustered by game)")
    ax.plot(pts.pt, 100 * pts.roi, color=BLUE, lw=2, marker="o", ms=4, label="return per $1 after fees")
    best = pts.loc[pts.roi.idxmax()]
    ax.annotate(f"best point: {int(best.pt)}c  {100 * best.roi:+.1f}%",
                (best.pt, 100 * best.roi), textcoords="offset points", xytext=(6, 10),
                fontsize=9, color=ORANGE)
    ax.set_facecolor(SURF)
    ax.grid(color=GRID, lw=.6)
    ax.tick_params(colors=INK2, labelsize=8)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_title("Every price point from 50c: what $1 returns after fees", color=INK, fontsize=11, loc="left")
    ax.set_xlabel("price paid (cents)", color=INK2, fontsize=9)
    ax.set_ylabel("% per $1", color=INK2, fontsize=9)
    ax.legend(frameon=False, fontsize=8, labelcolor=INK2)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def run_points() -> None:
    from scipy.stats import norm
    REPORTS.mkdir(exist_ok=True)
    f, _ = _load()
    pts = per_point(f)
    split = per_point_split(f)
    sweep = threshold_sweep(f, export_ledgers=True)
    by_sport = per_point(f, by="sport", min_fills=300)
    for name, df in [("points_all", pts), ("points_dev_holdout", split),
                     ("points_threshold_sweep", sweep), ("points_by_sport", by_sport)]:
        df.to_csv(REPORTS / f"calibration_{name}.csv", index=False)
    point_chart(pts, REPORTS / "calibration_points.png")

    # how many points look good by luck? one-sided z on each point, Benjamini-Hochberg at 5%
    z = (pts.roi / ((pts.ci_hi - pts.ci_lo) / (2 * 1.96))).replace([np.inf, -np.inf], np.nan).dropna()
    p = pd.Series(norm.sf(z), index=z.index).sort_values()
    bh = p <= 0.05 * np.arange(1, len(p) + 1) / len(p)
    survivors = pts.loc[p.index[:int(np.max(np.where(bh)[0]) + 1)]] if bh.any() else pts.iloc[:0]
    (REPORTS / "CALIBRATION_POINTS.md").write_text(_render_points(f, pts, split, sweep, by_sport, survivors))
    log.info("wrote %s", REPORTS / "CALIBRATION_POINTS.md")


def _render_points(f, pts, split, sweep, by_sport, survivors) -> str:
    cols = ["pt", "fills", "games", "avg_price", "won_pct", "edge_pts", "roi", "ci_lo", "ci_hi"]
    sp_cols = ["pt", "roi_dev", "ci_lo_dev", "ci_hi_dev", "roi_hold", "ci_lo_hold", "ci_hi_hold", "both_positive"]
    pos = split[split.both_positive]
    L = ["# Every price point from 50c: is there a sweet spot?", "",
         f"{len(f):,} fills, {f.m.nunique():,} markets (legacy corpus with incomplete lifetime coverage). Each row is one "
         "cent of price. `roi` is total net cash divided by capital including fees, with a 95% CI "
         "clustered by game. Price/payout averages use observed notional weights; void payout is 0.5. "
         "These descriptive historical tickets are separate from the delayed strategy replay.", "",
         "![points](calibration_points.png)", "",
         "## Every point, all sports", "", _md(pts, cols, ".4f"), "",
         "## The historically explored split", "",
         "Development is before2026-07-01; the later window has already been inspected and is not fresh confirmation.", "",
         _md(split, sp_cols, ".4f"), "",
         f"Points positive in BOTH windows: **{len(pos)} of {len(split)}** "
         f"({', '.join(str(int(p)) + 'c' for p in pos.pt) if len(pos) else 'none'}).", "",
         f"Exploratory approximate normal/BH screen across {len(pts)} points flags "
         f"**{len(survivors)}**. It estimates standard errors from bootstrap interval widths; "
         "the displayed intervals remain unadjusted. This is not a calibrated confirmatory test, "
         "especially with overlapping price bins, inspected history and asymmetric returns.", "",
         "### What different weights mean", "",
         "Dollar weighting is descriptive of historical tickets. Equal-dollar, proportional, and one-per-event "
         "policies are each legitimate if weights are known at signal time and exposure and capacity are enforced. "
         "The whale replay separately evaluates prespecified74/75c and73/76c comparator bands. "
         "Positive historical estimates remain unexplained and unvalidated pending new observations.", "",
         "## Causal delayed size-bounded threshold replay", "", _md(sweep, None, ".4f"), "",
         "## By sport", "", _md(by_sport, ["sport"] + cols, ".4f"), ""]
    return "\n".join(L)
