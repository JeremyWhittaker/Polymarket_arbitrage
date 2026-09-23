"""Full cash-allocation audits for pre-Thorp studies; no synthetic stake rescaling.

Run only after rebuilding the corrected inputs. A stale execution schema is an error,
not permission to manufacture entry prices. H1 itself is descriptive, so its ledger
contains reference observations with zero invested capital and an explicit status.
"""
from __future__ import annotations

import json
import logging
import numpy as np
import pandas as pd

from ..analysis.favorites import PREGAME_MIN_USD, favorite_bets, cached_pregame_prices
from ..analysis.thresholds import _prior_season_fair
from ..collect import DATA_DIR, sport_dir
from ..execution import panel_entries
from ..polymarket import taker_fee
from .common import RESEARCH, cluster_ci

log = logging.getLogger("pmsports")
OUT = RESEARCH / "ledgers"
SPLIT_TS = pd.Timestamp("2026-01-01", tz="UTC").timestamp()
COLUMNS = ["id", "period", "date", "sport", "league", "event", "market", "side", "entry_ts", "entry_price",
           "stake_usd", "fee_usd", "exit_kind", "exit_ts", "exit_price", "payout", "pnl_usd", "roi", "note",
           "signal_ts", "receipt_ts", "eligible_ts", "expiry_ts", "shares", "cost_usd", "print_id", "status",
           "reference_price"]
CASH = ["shares", "stake_usd", "fee_usd", "cost_usd", "payout", "pnl_usd", "roi"]


def validate_cash(df: pd.DataFrame) -> None:
    """Fail closed when rows predate allocations or their cash identities disagree."""
    missing = set(CASH + ["entry_price", "status"]) - set(df)
    if missing:
        raise ValueError(f"legacy execution schema; rebuild study (missing {sorted(missing)})")
    if df.empty:
        return
    if not np.isfinite(df[CASH[:-1]].to_numpy(float)).all():
        raise ValueError("nonfinite cash allocation")
    if (df[["shares", "stake_usd", "fee_usd", "cost_usd", "payout"]] < -1e-10).any().any():
        raise ValueError("negative shares, cash or payout")
    for actual, expected in ((df.cost_usd, df.stake_usd + df.fee_usd),
                             (df.pnl_usd, df.payout - df.cost_usd)):
        if not np.allclose(actual, expected, rtol=1e-8, atol=1e-8):
            raise ValueError("cash identity mismatch")
    filled = df.cost_usd > 0
    if not df.loc[filled, "entry_price"].between(0,1,inclusive="neither").all() or not df.loc[filled, "shares"].gt(0).all():
        raise ValueError("filled row has an invalid price or share count")
    if not np.isfinite(df.loc[filled, ["entry_price", "roi"]].to_numpy(float)).all():
        raise ValueError("filled row has no finite entry/ROI")
    if not np.allclose(df.loc[filled, "stake_usd"], df.loc[filled, "shares"] * df.loc[filled, "entry_price"], rtol=1e-8, atol=1e-8):
        raise ValueError("entry stake does not match consumed shares")
    if not np.allclose(df.loc[filled, "roi"], df.loc[filled, "pnl_usd"] / df.loc[filled, "cost_usd"], rtol=1e-8, atol=1e-8):
        raise ValueError("ROI must include entry fees in capital")
    if not np.allclose(df.loc[~filled, ["shares", "stake_usd", "fee_usd", "payout", "pnl_usd"]], 0):
        raise ValueError("unfilled row contains an invested position")
    if df.loc[~filled, "roi"].notna().any():
        raise ValueError("zero-capital ROI is undefined")
    if df.loc[~filled,"status"].isin(["filled","partial"]).any():
        raise ValueError("zero-capital row claims a fill")
    if not df.loc[filled, "status"].isin(["filled", "partial"]).all():
        raise ValueError("position status disagrees with allocated capital")


def _headline(trades):
    head = {}
    for period, signals in trades.groupby("period"):
        g = signals[signals.cost_usd > 0]
        roi, lo, hi = cluster_ci(g.roi, g.event, weights=g.cost_usd) if len(g) else (np.nan,) * 3
        head[period] = dict(signals=len(signals), bets=len(g), unfilled=int(signals.cost_usd.eq(0).sum()),
            descriptive=int(signals.status.eq("descriptive_only").sum()), partial=int(signals.status.eq("partial").sum()),
            roi=roi, ci_lo=lo, ci_hi=hi, pnl_usd=float(g.pnl_usd.sum()), capital_usd=float(g.cost_usd.sum()))
    return head


def _rows(df: pd.DataFrame) -> list[list]:
    df = df.copy().sort_values(["signal_ts", "entry_ts"], kind="stable").reset_index(drop=True)
    df["id"] = np.arange(1, len(df) + 1)
    df["date"] = pd.to_datetime(df.signal_ts.fillna(df.entry_ts), unit="s", utc=True).dt.strftime("%Y-%m-%d")
    for col in COLUMNS:
        if col not in df:
            df[col] = None
    return json.loads(df[COLUMNS].to_json(orient="values", double_precision=15))


def _sample(df: pd.DataFrame, seed: int = 0):
    """Compatibility name; server ledgers never sample away observations."""
    return df, False


def _document(meta, trades):
    validate_cash(trades)
    head = json.loads(pd.Series(_headline(trades)).to_json(double_precision=15))
    return {**{k:v for k,v in meta.items() if not k.startswith("_")}, "headline": head,
            "truncated": False, "n_total_trades": len(trades), "columns": COLUMNS, "rows": _rows(trades)}


def _write(meta: dict, trades: pd.DataFrame) -> dict:
    doc = _document(meta, trades)
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / f"{meta['slug']}.json"
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, separators=(",", ":"), allow_nan=False))
    tmp.replace(target)
    log.info("%s: %d signal rows, no truncation", meta["slug"], len(trades))
    return doc


def _period(ts):
    return np.where(np.asarray(ts) < SPLIT_TS, "dev", "holdout")


def _meta(slug, title, source, report, entry, sport="multi", extra=()):
    return dict(slug=slug, title=title, group="MLB studies" if sport == "baseball" else "All-sports studies",
        sport=sport, verdict="UNVALIDATED", hypothesis=title, mechanism="Historical signal evaluation",
        entry_rule=entry, exit_rule="Held to resolution when filled; no mean-reversion exit simulated",
        cost_model="Historical fee on actual allocated shares; ROI denominator includes fees",
        periods={"dev":"before2026-01-01", "holdout":"2026 onward, historically explored"},
        caveats=["Later transactions are price/capacity proxies, not resting book quotes",
                 "Receipt latency is unobserved; first-print sizes may not imply admissible venue orders",
                 "Every signal/no-fill retained; historical universe/tape coverage is incomplete",
                 "Previously explored evaluation periods are not fresh confirmation", *extra],
        report_path=report, code_path=source)


def _favorites_frame():
    m = cached_pregame_prices()
    required = {"decision_ts", "entry_delay_s", "entry_size0", "entry_size1", "entry_ts0", "entry_ts1", "execution"}
    if not required.issubset(m) or not m.execution.eq("first_later_print_partial_proxy").all():
        raise ValueError("pregame cache predates causal execution; regenerate favorites")
    b = favorite_bets(m)
    return b[b.pre_usd >= PREGAME_MIN_USD].copy()


def favorites_ledger(side="favorite"):
    if side not in ("favorite", "underdog"):
        raise ValueError("side must be favorite or underdog")
    b = _favorites_frame()
    dog = side == "underdog"
    if dog:
        b = b[b.kind == "two-way"].copy()  # counterpart of a three-way Yes is not the other team
    prefix = "dog" if dog else "fav"
    price = b[f"{prefix}_ask"]
    size = b[f"{prefix}_entry_size"]
    fill_ts = b[f"{prefix}_entry_ts"]
    budget = b[f"{prefix}_stake"]
    valid = budget.gt(0) & price.notna()
    if not (fill_ts[valid] > (b.decision_ts + b.entry_delay_s)[valid]).all() or not (fill_ts[valid] < b.game_start_ts[valid]).all():
        raise ValueError("pregame entry is outside its causal execution window")
    if not np.isfinite(b.loc[valid, "fee_rate"]).all() or (b.loc[valid, "fee_rate"] < 0).any():
        raise ValueError("pregame execution fee unknown")
    fee1 = taker_fee(1., price, b.fee_rate)
    shares = np.where(valid, budget / (price + fee1), 0.)
    if (shares > size.to_numpy() + 1e-8).any():
        raise ValueError("pregame allocation exceeds observed first print")
    won = 1 - b.fav_won if dog else b.fav_won
    stake = pd.Series(shares, index=b.index).mul(price).fillna(0)
    fees = pd.Series(shares, index=b.index).mul(fee1).fillna(0)
    cost = stake + fees
    name = np.where(b.p0 >= .5, b.o1 if dog else b.o0, b.o0 if dog else b.o1)
    t = pd.DataFrame(dict(period=_period(b.decision_ts), sport=b.family, league=b.league, event=b.event_slug,
        market=b.market_slug, side=name, signal_ts=b.decision_ts, receipt_ts=b.decision_ts,
        eligible_ts=b.decision_ts + b.entry_delay_s, expiry_ts=b.game_start_ts,
        entry_ts=fill_ts.where(valid), entry_price=price.where(valid), shares=shares, stake_usd=stake,
        fee_usd=fees, cost_usd=cost, exit_kind="resolution", exit_ts=b.closed_ts, exit_price=won,
        payout=shares * won, pnl_usd=shares * won - cost,
        roi=np.where(cost > 0, (shares * won - cost) / cost, np.nan),
        status=np.where(cost <= 0,"unfilled",np.where(cost >= 100-1e-8,"filled","partial")),
        reference_price=1-b.fav_p if dog else b.fav_p, note="First later same-side print; no fallback quote; partial allocation"))
    slug = "underdog_all_sports" if dog else "favorites_all_sports"
    return _write(_meta(slug, f"Pregame {side}s across sports", "pmsports/analysis/favorites.py", "reports/FAVORITES.md",
        "Decision10min before scheduled start from prior-hour prices; first actual same-side print strictly after delay;100 inclusive-dollar target capped by print shares",
        extra=("Strictly prior observed pregame volume floor remains a legacy-coverage sensitivity",)), t)


def _mlb_games():
    from ..panel import _match_cached_contracts
    d = sport_dir("mlb")
    g = pd.read_parquet(d / "games.parquet")
    g = g[g.game_pk.notna() & ~g.resolution_mismatch].copy()
    g["game_pk"] = g.game_pk.astype(int)
    return _match_cached_contracts(g,d/"trades").set_index("game_pk")


def _mlb_slugs():
    return _mlb_games().slug


def mlb_favorites_ledger():
    """H1 is price calibration, not an executed strategy; retain its observations."""
    p = pd.read_parquet(sport_dir("mlb") / "pregame.parquet").dropna(subset=["pre_p","home_won_final"])
    p = p[p.pre_p.between(.02,.98)].copy()
    home = p.pre_p >= .5
    won = np.where(home,p.home_won_final.astype(float),1-p.home_won_final.astype(float))
    t = pd.DataFrame(dict(period=_period(p.first_pitch_ts),sport="baseball",league="mlb",event=p.slug,
        market=p.slug,side=np.where(home,p.home_team,p.away_team),signal_ts=p.first_pitch_ts,
        entry_ts=np.nan,entry_price=np.nan,shares=0.,stake_usd=0.,fee_usd=0.,cost_usd=0.,
        exit_kind="descriptive outcome",exit_ts=p.game_pk.map(_mlb_games().closed_ts),exit_price=won,
        payout=0.,pnl_usd=0.,roi=np.nan,status="descriptive_only",reference_price=np.maximum(p.pre_p,1-p.pre_p),
        note="H1 descriptive price calibration; no observed execution, invested capital or realized trading profit"))
    meta = _meta("mlb_pregame_favorites","MLB pregame favorite calibration (descriptive)",
        "pmsports/analysis/hypotheses.py","reports/REPORT.md","No order simulated; reference price is the H1 pregame descriptive median/bar",sport="baseball")
    meta["verdict"],meta["cost_model"],meta["exit_rule"] = "DESCRIPTIVE","No allocated capital; synthetic cost benchmarks are not trades","Outcome observation only"
    return _write(meta,t)


def _panel_inputs():
    d = sport_dir("mlb")
    panel = pd.read_parquet(d / "panel.parquet")
    required = {"decision_ts","state_expiry_ts","mkt_staleness"} | {f"exec_{side}_{c}" for side in ("home","away") for c in ("p","ts","size","id")}
    if not required.issubset(panel):
        raise ValueError("MLB panel predates side-specific causal execution; rebuild panel")
    return panel,pd.read_parquet(d / "baseline.parquet")


def _mlb_checkpoints():
    panel,base = _panel_inputs()
    p = panel[panel.checkpoint & panel.mkt_p.notna() & panel.mkt_staleness.le(120) & panel["diff"].ne(0)].copy()
    p = p.sort_values(["game_pk","state_ts"]).reset_index(drop=True)
    p["fair_home"] = _prior_season_fair(p,base)
    hl = p["diff"] > 0
    p["price_leader"] = np.where(hl,p.mkt_p,1-p.mkt_p)
    p["fair_leader"] = np.where(hl,p.fair_home,1-p.fair_home)
    p["leader_won"] = np.where(hl,p.home_won_final.astype(float),1-p.home_won_final.astype(float))
    p["leader_is_home"],p["lead"] = hl,p["diff"].abs()
    return p


def _mlb_rows(sig,buy_home,note):
    r = panel_entries(sig,buy_home,slip=.01,event_cap_usd=100.).reset_index(drop=True)
    sig = sig.reset_index(drop=True)
    games = _mlb_games()
    slug = sig.game_pk.map(games.slug).fillna("mlb-game-"+sig.game_pk.astype(str))
    r["period"],r["sport"],r["league"] = _period(sig.decision_ts),"baseball","mlb"
    r["event"],r["market"],r["side"] = slug,slug,np.where(buy_home,"home","away")
    r["entry_ts"],r["exit_kind"],r["exit_ts"],r["exit_price"] = r.fill_ts,"resolution",sig.game_pk.map(games.closed_ts),r.y
    r["reference_price"] = np.where(buy_home,sig.mkt_p,1-sig.mkt_p)
    r["note"] = note
    return r


def mlb_inning_discount_ledger(discount=.03):
    p = _mlb_checkpoints()
    sig = p[p.fair_leader-p.price_leader >= discount].drop_duplicates("game_pk").copy()
    t = _mlb_rows(sig,sig.leader_is_home,"First half-inning signal; later same-side print plus1c sensitivity; optimistic retrospective state clock")
    return _write(_meta("mlb_inning_discount","MLB leader discount against prior-season state averages",
        "pmsports/analysis/thresholds.py","reports/THRESHOLDS.md",
        f"First half-inning signal per game with historical-minus-reference probability >={discount:.0%}; side-specific later print after5s plus1c;100 inclusive-dollar cap",
        sport="baseball",extra=("5s from retrospective state time is optimistic; pending-order cancellation is not established",)),t)


def mlb_fair_value_ledger(threshold=.02):
    """Match H3 fit/signal selection; use its shared execution adapter, never signal price."""
    import statsmodels.api as sm
    from ..analysis.hypotheses import causal_model_rows,_features
    panel,base = _panel_inputs()
    df = causal_model_rows(panel,base,"2026-01-01")
    tr,te = df[df.event_date < "2026-01-01"],df[df.event_date >= "2026-01-01"].copy()
    if len(tr) < 1000 or len(te) < 1000:
        raise ValueError(f"H3 cannot estimate ledger: insufficient rows ({len(tr)}train/{len(te)}test)")
    X = _features(tr)
    cols = [c for c in X if X[c].std() > 1e-9]
    fit = sm.Logit(tr.y.to_numpy(),sm.add_constant(X[cols])).fit(disp=0)
    te["model_p"] = fit.predict(sm.add_constant(_features(te)[cols],has_constant="add"))
    te["edge_home"] = te.model_p-te.mkt_p
    sig = te[te.edge_home.abs() > threshold].sort_values("state_ts",kind="stable").drop_duplicates("game_pk")
    t = _mlb_rows(sig,sig.edge_home > 0,"H3 frozen2025 fit; first2026 signal; later side-specific print plus1c; settlement exit only")
    t["period"] = "holdout"  # H3 development rows fit the model and never generate trades
    return _write(_meta("mlb_fair_value","MLB model-versus-market settlement rule",
        "pmsports/analysis/hypotheses.py","reports/REPORT.md",
        f"H3 pregame-plus-state logistic model fit before2026; first signal per game with absolute edge >{threshold:.0%}; later print after5s plus1c;100 inclusive-dollar cap",
        sport="baseball",extra=("All ledger orders are evaluation observations; development only fits the model",
            "No literal mean-reversion exit is simulated; state receipt/depth remain unobserved")),t)


def copy_wallets_ledger(delay=30):
    from ..wallets import skill,study
    from ..wallets.tapes import load_trades
    u = pd.read_parquet(DATA_DIR / "wallets" / "universe.parquet")
    meta = u.drop_duplicates("condition_id").set_index("condition_id")
    t = skill.valid_trades(load_trades(u))
    s1 = skill.wallet_stats(skill.positions(t[study._causal_cutoff(t,SPLIT_TS,meta)]))
    fdr = skill.fdr_survivors(s1[s1.markets >= study.MIN_MKTS].z).tolist()
    t2 = t[t.timestamp >= SPLIT_TS]
    selected = t2[t2.proxyWallet.isin(fdr)]
    mk = t2[t2.condition_id.isin(selected.condition_id.unique())]
    cp = skill.copy_prices(mk,selected,delays=(delay,))  # full selected signals, no sample/head cap
    pref = "prop_"
    def col(name):
        return cp[f"{pref}{name}_d{delay}"]
    price = cp[f"{pref}q_d{delay}"]
    labels = u.reindex(columns=["condition_id", "outcome_idx", "outcome"]).dropna(subset=["condition_id", "outcome_idx"]).drop_duplicates()
    labels = labels.loc[~labels.duplicated(["condition_id", "outcome_idx"], keep=False)]
    labels = labels.pivot(index="condition_id", columns="outcome_idx", values="outcome").reindex(columns=[0,1])
    sides = [cp.condition_id.map(labels[s]).astype("string").fillna("Outcome unavailable") for s in (0,1)]
    filled = col("shares").gt(0)
    led = pd.DataFrame(dict(period="holdout",sport=cp.family.astype(str),league="",event=cp.event_slug.astype(str),
        market=cp.condition_id.map(meta.get("market_slug", pd.Series(index=meta.index, dtype=str))).astype("string").fillna("Market unavailable"),
        side=np.where(cp.side_idx == 0,sides[0],sides[1]),
        signal_ts=col("signal_ts"),receipt_ts=col("receipt_ts"),eligible_ts=col("eligible_ts"),expiry_ts=col("expiry_ts"),
        entry_ts=col("fill_ts"),entry_price=price,shares=col("shares"),stake_usd=col("stake_usd"),
        fee_usd=col("fee_usd"),cost_usd=col("cost_usd"),payout=col("payout"),pnl_usd=col("pnl_usd"),roi=col("roi"),
        print_id=col("print_id"),status=col("status"),exit_kind=np.where(filled,"resolution","unfilled"),
        exit_ts=cp.condition_id.map(meta.closed_ts).astype(float).where(filled),
        exit_price=cp.y.where(filled),reference_price=cp.q,
        note="Causal FDR selection; proportional1% leader-notional target capped100/event; all no-fills retained; condition="+cp.condition_id.astype(str)))
    return _write(_meta("copy_skilled_wallets","Copy historically selected wallets: capped proportional policy",
        "pmsports/wallets/skill.py","reports/WALLETS.md",
        f"Rank only trades and settled outcomes known before2026; FDR survivors with >={study.MIN_MKTS}markets; first later other-wallet same-side print strictly after{delay}s;1% leader notional capped100/order/event",
        extra=("Whole2026 evaluation is one event-cap replay; monthly walk-forward has a different monthly reset scope",
               "All selected valid signals are retained; no comparison or ledger samples future events or rescales execution rows")),led)


def run():
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(message)s")
    favorites_ledger("favorite")
    favorites_ledger("underdog")
    mlb_favorites_ledger()
    mlb_inning_discount_ledger()
    mlb_fair_value_ledger()
    copy_wallets_ledger()


if __name__ == "__main__":
    run()
