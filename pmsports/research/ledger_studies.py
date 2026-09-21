"""Per-trade ledgers for the studies run before the Thorp hypothesis hunt.

python -m pmsports.research.ledger_studies  ->  data/research/ledgers/<slug>.json
Format: pmsports/research/LEDGER_SPEC.md. Rows are the actual bets each study made.
"""
from __future__ import annotations

import json
import logging

import numpy as np
import pandas as pd

from ..analysis.favorites import PREGAME_MIN_USD, favorite_bets
from ..analysis.thresholds import _prior_season_fair
from ..collect import DATA_DIR, sport_dir
from ..polymarket import taker_fee
from .common import RESEARCH, cluster_ci

log = logging.getLogger("pmsports")
OUT = RESEARCH / "ledgers"
COLUMNS = ["id", "period", "date", "sport", "league", "event", "market", "side", "entry_ts", "entry_price",
           "stake_usd", "fee_usd", "exit_kind", "exit_ts", "exit_price", "payout", "pnl_usd", "roi", "note"]
STAKE = 100.0           # flat $100 per bet, so P&L columns read as dollars
SPLIT_TS = pd.Timestamp("2026-01-01", tz="UTC").timestamp()
MAX_ROWS = 20_000


def _rows(df: pd.DataFrame) -> list[list]:
    df = df.sort_values("entry_ts").reset_index(drop=True)
    df.insert(0, "id", np.arange(1, len(df) + 1))
    df["date"] = pd.to_datetime(df.entry_ts, unit="s", utc=True).dt.strftime("%Y-%m-%d")
    out = df[COLUMNS].copy()
    for c in ("entry_price", "stake_usd", "fee_usd", "exit_price", "payout", "pnl_usd", "roi"):
        out[c] = out[c].astype(float).round(6)
    return json.loads(out.to_json(orient="values"))


def _sample(df: pd.DataFrame, seed: int = 0) -> tuple[pd.DataFrame, bool]:
    if len(df) <= MAX_ROWS:
        return df, False
    hold = df[df.period == "holdout"]
    dev = df[df.period == "dev"]
    keep = max(MAX_ROWS - len(hold), 1000)
    return pd.concat([dev.sample(min(keep, len(dev)), random_state=seed), hold]), True


def _write(meta: dict, trades: pd.DataFrame) -> None:
    head = {}
    for period, g in trades.groupby("period"):      # headline on EVERY trade, before sampling
        roi, lo, hi = cluster_ci(g.roi, g.event, weights=g.stake_usd)
        head[period] = {"bets": int(len(g)), "roi": round(float(roi), 5), "ci_lo": round(float(lo), 5),
                        "ci_hi": round(float(hi), 5), "pnl_usd": round(float(g.pnl_usd.sum()), 2)}
    trades, truncated = _sample(trades)
    doc = {**meta, "headline": head, "truncated": truncated, "n_total_trades": int(meta.pop("_n_total")),
           "columns": COLUMNS, "rows": _rows(trades)}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{meta['slug']}.json").write_text(json.dumps(doc, separators=(",", ":")))
    log.info("%s: %d rows%s  %s", meta["slug"], len(trades), " (sampled)" if truncated else "",
             {k: v["roi"] for k, v in head.items()})


def _period(ts) -> np.ndarray:
    return np.where(np.asarray(ts) < SPLIT_TS, "dev", "holdout")


# ----------------------------------------------------------------------------- all-sports pregame

def _favorites_frame() -> pd.DataFrame:
    m = pd.read_parquet(DATA_DIR / "wallets" / "pregame_prices.parquet")
    b = favorite_bets(m)
    return b[b.pre_usd >= PREGAME_MIN_USD].copy()


def favorites_ledger(side: str = "favorite") -> None:
    b = _favorites_frame()
    if side == "underdog":
        b = b[(b.kind == "two-way") & b.dog_ask.notna()].copy()
        price = np.maximum(b.dog_ask, 1 - b.fav_p)             # executable ask for the dog
        won = 1 - b.fav_won
        name = np.where(b.p0 >= 0.5, b.o1, b.o0)
        slug, title = "underdog_all_sports", "Bet every pregame underdog (all sports)"
    else:
        price = np.where(b.fav_ask.notna(), np.maximum(b.fav_ask, b.fav_p), b.fav_p + 0.01)
        won = b.fav_won
        name = np.where(b.p0 >= 0.5, b.o0, b.o1)
        slug, title = "favorites_all_sports", "Bet every pregame favorite (all sports)"
    fee = taker_fee(STAKE / price, price, b.fee_rate.fillna(0).to_numpy())
    shares = STAKE / price
    payout = shares * won.to_numpy(float)
    t = pd.DataFrame({
        "period": _period(b.game_start_ts), "sport": b.family, "league": b.league, "event": b.event_slug,
        "market": b.market_slug, "side": name, "entry_ts": b.game_start_ts.astype("int64"),
        "entry_price": price, "stake_usd": STAKE, "fee_usd": fee, "exit_kind": "resolution",
        "exit_ts": b.closed_ts.fillna(b.game_start_ts).astype("int64"), "exit_price": won.to_numpy(float),
        "payout": payout, "pnl_usd": payout - STAKE - fee,
        "note": ["fav " + f"{p:.0%}" + (" (3-way: draw loses)" if k != "two-way" else "")
                 for p, k in zip(b.fav_p, b.kind)]})
    t["roi"] = t.pnl_usd / t.stake_usd
    meta = {
        "slug": slug, "title": title, "group": "All-sports studies",
        "sport": "multi", "verdict": "DEAD",
        "hypothesis": ("Favorites win most games, so backing them (or, mirrored, backing underdogs) at the "
                       "pregame price and holding to the end should pay."),
        "mechanism": ("If the market misprices team strength systematically, one side wins more often than "
                      "its price implies. The price is the odds, so only that gap is edge."),
        "entry_rule": ("One bet per game at the scheduled start, on the side priced above (favorite) or below "
                       "(underdog) 50c. Entry price = the price takers actually paid for that side in the last "
                       "10 minutes before the start (executable ask), floored at the pregame mid. Flat $100."),
        "exit_rule": "Held to resolution: payout $1/share if that side won, $0 if it lost, $0.50 on a void.",
        "cost_model": "Polymarket taker fee at the market's own rate (0 in 2025, 3% Mar-Jun 2026, 5% since Jul 2026).",
        "periods": {"dev": "2025-01-01..2025-12-31", "holdout": "2026-01-01..2026-09-18"},
        "review": ("An earlier version selected games on TOTAL volume, which includes in-play trading; upsets "
                   "draw more in-play volume, so that made underdogs look +3% to +9% profitable. Selecting only "
                   "on pregame volume (>= $25k) removes the illusion."),
        "caveats": ["Selection uses pregame volume only, never total volume or outcome.",
                    "3-way soccer legs are Yes/No 'will X win' markets: a draw loses.",
                    "Thresholds (>=50c ... >=90c) are filters over this same ledger: filter on the note/fav price."],
        "report_path": "reports/FAVORITES.md", "code_path": "pmsports/analysis/favorites.py",
        "_n_total": len(t)}
    _write(meta, t)


# ----------------------------------------------------------------------------- MLB pregame favorites

def mlb_favorites_ledger() -> None:
    p = pd.read_parquet(sport_dir("mlb") / "pregame.parquet").dropna(subset=["pre_p", "home_won_final"])
    p = p[p.pre_p.between(0.02, 0.98)].copy()
    fav_home = p.pre_p >= 0.5
    price = np.maximum(p.pre_p, 1 - p.pre_p).to_numpy() + 0.01           # + 1c slippage
    won = np.where(fav_home, p.home_won_final.astype(float), 1 - p.home_won_final.astype(float))
    shares = STAKE / price
    fee = taker_fee(shares, price, p.fee_rate.fillna(0).to_numpy())
    payout = shares * won
    t = pd.DataFrame({
        "period": _period(p.first_pitch_ts), "sport": "baseball", "league": "mlb", "event": p.slug,
        "market": p.slug, "side": np.where(fav_home, p.home_team, p.away_team),
        "entry_ts": p.first_pitch_ts.astype("int64"), "entry_price": price, "stake_usd": STAKE,
        "fee_usd": fee, "exit_kind": "resolution", "exit_ts": p.first_pitch_ts.astype("int64") + 10800,
        "exit_price": won, "payout": payout, "pnl_usd": payout - STAKE - fee,
        "note": [f"fav {q:.0%} at first pitch" for q in np.maximum(p.pre_p, 1 - p.pre_p)]})
    t["roi"] = t.pnl_usd / t.stake_usd
    meta = {
        "slug": "mlb_pregame_favorites", "title": "MLB: bet every pregame favorite", "group": "MLB studies",
        "sport": "baseball", "verdict": "DEAD",
        "hypothesis": "Do MLB favorites win more often than their price implies? ('We only need 51/49.')",
        "mechanism": "Retail money leans to favorites, which would leave them overpriced.",
        "entry_rule": ("One bet per game on the side priced above 50c, at the median traded price in the 10 "
                       "minutes before the actual first pitch, plus 1c slippage. Flat $100."),
        "exit_rule": "Held to the final out.",
        "cost_model": "Taker fee at the market's own rate; 1c slippage included in entry_price.",
        "periods": {"dev": "2025 season", "holdout": "2026 season"},
        "review": ("Polymarket clears the book at first pitch and prints a glitch bar (0.50 or an empty-book "
                   "mid) at that minute; using it made favorites look 20 points overpriced. Prices here come "
                   "from actual fills."),
        "caveats": ["Favorites won 56.1% at an average price of 57.4%: the gap, not the win rate, is the edge."],
        "report_path": "reports/REPORT.md", "code_path": "pmsports/analysis/hypotheses.py",
        "_n_total": len(t)}
    _write(meta, t)


# ----------------------------------------------------------------------------- MLB in-game rules

def _mlb_slugs() -> pd.Series:
    g = pd.read_parquet(sport_dir("mlb") / "games.parquet", columns=["game_pk", "slug"]).dropna(subset=["game_pk"])
    g = g.drop_duplicates("game_pk")
    return pd.Series(g.slug.to_numpy(), index=g.game_pk.astype(int).to_numpy())


def _mlb_checkpoints() -> pd.DataFrame:
    d = sport_dir("mlb")
    panel = pd.read_parquet(d / "panel.parquet")
    base = pd.read_parquet(d / "baseline.parquet")
    p = panel[panel.checkpoint & panel.mkt_p.notna() & (panel["diff"] != 0)].copy()
    p = p.sort_values(["game_pk", "state_ts"]).reset_index(drop=True)
    p["fair_home"] = _prior_season_fair(p, base)
    hl = p["diff"] > 0
    won = p.home_won_final.astype(float)
    p["price_leader"] = np.where(hl, p.mkt_p, 1 - p.mkt_p)
    p["fair_leader"] = np.where(hl, p.fair_home, 1 - p.fair_home)
    p["leader_won"] = np.where(hl, won, 1 - won)
    p["leader_is_home"] = hl
    p["lead"] = p["diff"].abs()
    return p


def mlb_inning_discount_ledger(discount: float = 0.03) -> None:
    p = _mlb_checkpoints()
    sig = p[p.fair_leader - p.price_leader >= discount].groupby("game_pk").head(1).copy()
    slug = sig.game_pk.astype(int).map(_mlb_slugs()).fillna("mlb-game-" + sig.game_pk.astype(int).astype(str))
    price = sig.price_leader.to_numpy() + 0.01
    shares = STAKE / price
    fee = taker_fee(shares, price, sig.fee_rate.fillna(0).to_numpy())
    payout = shares * sig.leader_won.to_numpy()
    t = pd.DataFrame({
        "period": _period(sig.state_ts), "sport": "baseball", "league": "mlb", "event": slug,
        "market": slug, "side": np.where(sig.leader_is_home, "home (leading)", "away (leading)"),
        "entry_ts": sig.state_ts.astype("int64"), "entry_price": price, "stake_usd": STAKE, "fee_usd": fee,
        "exit_kind": "resolution", "exit_ts": sig.state_ts.astype("int64") + 5400,
        "exit_price": sig.leader_won.to_numpy(), "payout": payout, "pnl_usd": payout - STAKE - fee,
        "note": [f"top {int(i)}th, lead {int(l)}: history {f:.0%} vs price {q:.0%}"
                 for i, l, f, q in zip(sig.inning, sig.lead, sig.fair_leader, sig.price_leader)]})
    t["roi"] = t.pnl_usd / t.stake_usd
    meta = {
        "slug": "mlb_inning_discount", "title": "MLB: buy the leader when it trades below its historical win rate",
        "group": "MLB studies", "sport": "baseball", "verdict": "DEAD",
        "hypothesis": ("'Up 3 going into the 9th wins 98% of the time but trades at 93c, so buy it.' Buy the "
                       f"leading team whenever its price is at least {discount:.0%} below the historical win "
                       "rate for that inning/lead/home-away state."),
        "mechanism": ("If the market underweights how safe a lead is, leaders are systematically cheap. The "
                      "historical rate comes only from seasons BEFORE the game being bet."),
        "entry_rule": ("At each half-inning start, compare the leader's traded price (median fill 15-75s after "
                       "the previous half ended) with the historical rate for that state. First time in a game "
                       f"the gap is >= {discount:.0%}, buy the leader at that price + 1c. Flat $100, one bet per game."),
        "exit_rule": "Held to the end of the game.",
        "cost_model": "Taker fee at the market's rate plus 1c slippage.",
        "periods": {"dev": "2025 season", "holdout": "2026 season"},
        "review": ("Discounted leaders are nearly always the weaker team (78-98% were pregame underdogs) and "
                   "they win at about their price, not the historical average. Profitable in fee-free 2025 "
                   "(+6.0%, CI +1.1% to +10.5%), ~0 in 2026 after fees."),
        "caveats": ["The historical rate averages over all teams; the price knows which teams are playing.",
                    "Variants at 2/5/8/10 point discounts are in reports/THRESHOLDS.md."],
        "report_path": "reports/THRESHOLDS.md", "code_path": "pmsports/analysis/thresholds.py",
        "_n_total": len(t)}
    _write(meta, t)


def mlb_fair_value_ledger(threshold: float = 0.02) -> None:
    """H3: model (pregame odds + game state) vs market; buy the side the model likes."""
    import statsmodels.api as sm
    from ..analysis.hypotheses import _baseline_we, _features
    d = sport_dir("mlb")
    panel = pd.read_parquet(d / "panel.parquet")
    base = pd.read_parquet(d / "baseline.parquet")
    df = panel.dropna(subset=["mkt_p", "pre_p", "home_won_final"]).copy()
    df = df[df.pre_p.between(0.02, 0.98)]
    df["we"] = _baseline_we(base, df, max_season=2025)
    df["y"] = df.home_won_final.astype(float)
    tr, te = df[df.event_date < "2026-01-01"], df[df.event_date >= "2026-01-01"].copy()
    X = _features(tr)
    cols = [c for c in X.columns if X[c].std() > 1e-9]
    fit = sm.Logit(tr.y.to_numpy(), sm.add_constant(X[cols])).fit(disp=0)
    te["model_p"] = fit.predict(sm.add_constant(_features(te)[cols], has_constant="add"))
    te["edge"] = te.model_p - te.mkt_p
    sig = te[te.edge.abs() > threshold].sort_values("state_ts").groupby("game_pk").head(1).copy()
    slug = sig.game_pk.astype(int).map(_mlb_slugs()).fillna("mlb-game-" + sig.game_pk.astype(int).astype(str))
    buy_home = sig.edge > 0
    price = np.where(buy_home, sig.mkt_p, 1 - sig.mkt_p) + 0.01
    won = np.where(buy_home, sig.y, 1 - sig.y)
    shares = STAKE / price
    fee = taker_fee(shares, price, sig.fee_rate.fillna(0).to_numpy())
    payout = shares * won
    t = pd.DataFrame({
        "period": "holdout", "sport": "baseball", "league": "mlb", "event": slug,
        "market": slug, "side": np.where(buy_home, "home", "away"),
        "entry_ts": sig.state_ts.astype("int64"), "entry_price": price, "stake_usd": STAKE, "fee_usd": fee,
        "exit_kind": "resolution", "exit_ts": sig.state_ts.astype("int64") + 5400, "exit_price": won,
        "payout": payout, "pnl_usd": payout - STAKE - fee,
        "note": [f"model {mp:.0%} vs market {q:.0%} (inning {int(i)})"
                 for mp, q, i in zip(sig.model_p, sig.mkt_p, sig.inning)]})
    t["roi"] = t.pnl_usd / t.stake_usd
    meta = {
        "slug": "mlb_fair_value", "title": "MLB: trade toward a fair value that knows team strength",
        "group": "MLB studies", "sport": "baseball", "verdict": "DEAD",
        "hypothesis": ("Fit a fair value from the pregame odds plus the game state, then buy whichever side "
                       "the market has cheaper than the model."),
        "mechanism": "If the in-game price drifts from what the state and team strength imply, it should revert.",
        "entry_rule": (f"Model fitted on 2025 only, frozen. In 2026, the first state per game where |model - "
                       f"market| > {threshold:.0%}, buy the side the model prefers at the traded price + 1c. Flat $100."),
        "exit_rule": "Held to the end of the game.",
        "cost_model": "Taker fee at the market's rate plus 1c slippage.",
        "periods": {"dev": "2025 (model fitting only, no bets)", "holdout": "2026 season"},
        "review": ("The market forecasts better than the model out of sample (log-loss 0.4947 vs 0.4930 for "
                   "the model on states, but the stacking coefficient is ~0), and the backtest loses after fees "
                   "at every threshold."),
        "caveats": ["Dev period is model fitting, so all rows here are holdout bets.",
                    "Thresholds 2%..10% all lose; see reports/REPORT.md."],
        "report_path": "reports/REPORT.md", "code_path": "pmsports/analysis/hypotheses.py",
        "_n_total": len(t)}
    _write(meta, t)


# ----------------------------------------------------------------------------- copy the sharps

def copy_wallets_ledger(delay: int = 30) -> None:
    from ..wallets import skill
    from ..wallets.tapes import load_trades
    u = pd.read_parquet(DATA_DIR / "wallets" / "universe.parquet")
    t = load_trades(u)
    s1 = skill.wallet_stats(skill.positions(t[t.timestamp < SPLIT_TS]))
    fdr = skill.fdr_survivors(s1[s1.markets >= 30].z).tolist()
    t2 = t[t.timestamp >= SPLIT_TS]
    rows = t2[t2.proxyWallet.isin(fdr)]
    mk = t2[t2.condition_id.isin(rows.condition_id.unique())]
    rows = mk[mk.proxyWallet.isin(fdr)]
    cp = skill.copy_prices(mk, rows, delays=(delay,))
    r = skill.copy_returns(cp, delay, stake="proportional")
    del t, t2, mk, cp
    price = r[f"q_d{delay}"].to_numpy(float)
    shares = STAKE / price
    fee = taker_fee(shares, price, r.fee_rate.fillna(0).to_numpy())
    payout = shares * r.y.to_numpy(float)
    led = pd.DataFrame({
        "period": "holdout", "sport": r.family.astype(str), "league": "",
        "event": r.event_slug.astype(str), "market": r.condition_id.astype(str),
        "side": np.where(r.side_idx == 0, "outcome 0", "outcome 1"),
        "entry_ts": (r.timestamp + delay).astype("int64"), "entry_price": price, "stake_usd": STAKE,
        "fee_usd": fee, "exit_kind": "resolution", "exit_ts": (r.timestamp + 7200).astype("int64"),
        "exit_price": r.y.to_numpy(float), "payout": payout, "pnl_usd": payout - STAKE - fee,
        "note": [f"leader filled at {q:.3f}, copy at {c:.3f} after {delay}s"
                 for q, c in zip(r.q, price)]})
    led["roi"] = led.pnl_usd / led.stake_usd
    meta = {
        "slug": "copy_skilled_wallets", "title": "Copy the wallets with proven skill",
        "group": "All-sports studies", "sport": "multi", "verdict": "DEAD",
        "hypothesis": ("99 wallets had statistically real skill in 2025 (vs ~31 expected by luck). Copy their "
                       "2026 trades and share the edge."),
        "mechanism": "If their skill is prediction, a follower should capture most of it.",
        "entry_rule": (f"When a selected wallet's fill appears, buy the same side at the first OTHER taker "
                       f"print on that side at least {delay}s later (an executable price). Flat $100."),
        "exit_rule": "Held to resolution.",
        "cost_model": "Taker fee at the market's rate; the delayed print is the executable price (no extra slippage).",
        "periods": {"dev": "2025 (selection only, no bets)", "holdout": "2026"},
        "review": ("The skill is real but is in-play speed: copying at their own price returns +3.9% "
                   "(CI +2.1% to +5.9%), +1.1% one second later, +0.2% at 5s and -0.9% at 30s. A wallet is only "
                   "identifiable after on-chain settlement (~2.6s), so a follower is always late."),
        "caveats": ["94% of the copied trades are in-play; median leader fill is $4.",
                    f"This ledger is the {delay}s-delay version; 0/1/2/5s variants are in reports/WALLETS.md.",
                    "Stake is flattened to $100/trade here; the report also mirrors their sizes."],
        "report_path": "reports/WALLETS.md", "code_path": "pmsports/wallets/skill.py",
        "_n_total": len(led)}
    _write(meta, led)


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    favorites_ledger("favorite")
    favorites_ledger("underdog")
    mlb_favorites_ledger()
    mlb_inning_discount_ledger()
    mlb_fair_value_ledger()
    copy_wallets_ledger()


if __name__ == "__main__":
    run()
