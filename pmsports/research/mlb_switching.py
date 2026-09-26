"""EXPLORATORY: MLB half-inning re-evaluation / switching backtest (the user's switching idea).

HISTORICAL BACKTEST ONLY — NOT FORWARD EVIDENCE. This is a new hypothesis evaluated on the same
already-inspected 2025-26 MLB panel and half-inning checkpoints that produced the 10c
hold-to-resolution rule, across many variants (thresholds x exit rules x side policies x entry
conventions x fee schedules). Selection bias and multiple comparisons apply to every number.

Idea under test. At every half-inning start (the exact checkpoint set of the 10c rule:
``ledger_studies._mlb_checkpoints``; tied states are excluded, as in that rule) compute the prior-
season state discount for each side, ``fair_side - price_side`` (identical arithmetic to
``_discount_signals``). Hold the side the historical table says is underpriced; when the situation
changes, sell and (possibly) switch; hold whatever remains to resolution.

Rules (per threshold ``thr`` in THRESHOLDS):
  hold_first        first qualifying checkpoint per game places one order, held to resolution.
                    With thr=0.10, leader sides, economic entry and as-recorded fees this is the
                    canonical 10c ledger, reproduced exactly (the run fails closed otherwise).
  hold_retry        when flat, buy at any qualifying checkpoint (re-evaluation without exits).
                    Never sells. This is the no-exit counterpart of the two rules below.
  exit_no_discount  (a) sell the held side whenever its discount < 0 (no longer underpriced);
                    after the sell, buy the other side if IT qualifies (>= thr); re-enter later.
  switch_only       (b) sell the held side only when the other side qualifies (>= thr), then buy it.
A side "qualifies" when its discount >= thr and the side policy allows it: ``leader`` (the 10c
rule's convention: only the currently leading team may be bought) or ``both`` (the literal
reading of the idea: whichever side is underpriced). No top-ups: an already-held side is not
added to; a partial fill stays partial.

Execution (reports/EXECUTION_PROTOCOL.md literal-action rules; shared ``TapeReplay`` engine):
* Sells (exits) fill only against later literal SELL prints of the held token (the soccer
  continuation unwind pattern: normalized opposite side, sale price = raw price - slip), first
  print strictly after decision+5 s and before the next-state expiry, capped by that print's size.
  Unsold shares stay in inventory, are re-offered at later checkpoints when the rule again says
  exit, and otherwise settle at resolution. Exit fees are charged.
* Buys fill against the first later same-side print, +1c, one IOC-like allocation capped by the
  print size and an inclusive-fee budget. ``literal``: literal BUY prints of the intended token
  only (BUY and SELL indexes partition prints, as in soccer). ``economic``: normalized
  acquisitions (BUY of the token or SELL of the complement), which is what the panel / 10c ledger
  used; a switch buy is always strictly after its own sell print, so no print is used twice.
* Sequencing is causal: a switch submits the sell first; the buy is submitted only after that sell
  fills (signal = sell fill time, no extra delay) and is sized from the exposure actually freed.
  A failed sell blocks the buy for that checkpoint.
* Sizing: $100 per order (inclusive of fees) and a $100 maximum open position per game, measured
  as the remaining fee-inclusive cost basis of held shares. Capital per game = peak cumulative
  net cash outlay (buys incl. fees minus net sale proceeds), which can exceed $100 when a
  position is sold at a loss and the freed exposure is redeployed.
* Fees: fee = rate * shares * p * (1 - p) on both entry and exit. Schedules: as_recorded (0 in
  2025, 0.03 / 0.05 in 2026 as stored per game), intl_0.05, us_0.0695.

Limits: the decision clock is the retrospective play clock (5 s is optimistic); historical prints
are capacity/price proxies, not book depth; pending-order cancellation is not modeled.

Outputs (data/research/performance/, git-ignored):
  switching_summary.{parquet,csv}   one row per variant
  switching_by_season.{parquet,csv} per variant x season
  switching_vs_10c.{parquet,csv}    paired game-level comparison with the 10c hold rule and with
                                    the same-config hold_first rule
  switching_games.parquet           per variant x game cash ledger
  switching_trades.parquet          every order (buy/sell), including no-fills
  switching_meta.json               labels, parameters, reconciliation with the canonical ledger

Run: PYTHONPATH=. nice .venv/bin/python -m pmsports.research.mlb_switching
"""
from __future__ import annotations

import itertools
import json
import logging
import time

import numpy as np
import pandas as pd

from ..collect import sport_dir
from ..execution import VERSION as EXECUTION_VERSION
from ..execution import TapeReplay
from .common import RESEARCH, cluster_ci
from .ledger_studies import SPLIT_TS, _mlb_checkpoints, _mlb_games

log = logging.getLogger("pmsports")

OUT = RESEARCH / "performance"
CANONICAL = RESEARCH / "ledgers" / "mlb_inning_discount_10c_first.json"
THRESHOLDS = (0.03, 0.05, 0.08, 0.10)
RULES = ("hold_first", "hold_retry", "exit_no_discount", "switch_only")
SIDES = ("leader", "both")
ENTRY = ("literal", "economic")
FEES = {"as_recorded": None, "intl_0.05": 0.05, "us_0.0695": 0.0695}
SLIP = 0.01
DELAY_S = 5.0
BUDGET_USD = 100.0
POSITION_CAP_USD = 100.0
MIN_ORDER_USD = 1.0
EPS = 1e-9
N_BOOT = 2000
LABEL = ("EXPLORATORY historical backtest: new switching hypothesis on already-inspected 2025-26 MLB "
         "data; one of many variants; selection-biased; NOT forward evidence")
COMPARATOR = dict(thr=0.10, rule="hold_first", sides="leader", entry="economic")


# ----------------------------------------------------------------------------- inputs

def load_checkpoints(max_games=None) -> pd.DataFrame:
    """The 10c rule's own checkpoint table, plus the side discounts with its exact arithmetic."""
    p = _mlb_checkpoints()
    if max_games is not None:
        keep = np.sort(p.game_pk.unique())[:max_games]
        p = p[p.game_pk.isin(keep)]
    p = p.sort_values(["game_pk", "state_ts"], kind="stable").reset_index(drop=True)
    # ledger_studies: fair_leader - price_leader with fair/price = x (home) or 1 - x (away).
    p["disc_home"] = p.fair_home - p.mkt_p
    p["disc_away"] = (1 - p.fair_home) - (1 - p.mkt_p)
    p["leader_side"] = np.where(p["diff"] > 0, 0, 1)
    p["k"] = p.groupby("game_pk").cumcount()
    p["season"] = p.event_date.str[:4].astype(int)
    if (p.state_expiry_ts > p.groupby("game_pk").decision_ts.shift(-1)).any():
        raise ValueError("checkpoint windows overlap; per-checkpoint capacity would leak")
    return p


def load_tape(cp: pd.DataFrame) -> pd.DataFrame:
    """Normalized prints inside checkpoint windows with literal action/token provenance.

    Read and sort exactly as panel.build_panel.tr_of so the print order and raw ids equal the
    panel's (the 10c ledger print_id is ``game_pk:raw_id``).
    """
    games = _mlb_games()
    d = sport_dir("mlb") / "trades"
    parts = []
    for pk, c in cp.groupby("game_pk", sort=True):
        f = d / f"{pk}.parquet"
        if not f.exists() or pk not in games.index:
            continue
        t = pd.read_parquet(f, columns=["timestamp", "home_p", "size", "side", "asset", "price"]).sort_values("timestamp")
        ts, hp = t.timestamp.to_numpy(float), t.home_p.to_numpy(float)
        size, side, asset = t["size"].to_numpy(float), t.side.to_numpy(str), t.asset.to_numpy(str)
        home_tok, away_tok = str(games.at[pk, "home_token"]), str(games.at[pk, "away_token"])
        known = np.isin(asset, [home_tok, away_tok]) & np.isin(side, ["BUY", "SELL"])
        a = c.decision_ts.to_numpy(float)
        b = c.state_expiry_ts.to_numpy(float)
        w = np.searchsorted(a, ts, side="left") - 1  # last window whose decision is strictly earlier
        inwin = known & (w >= 0) & (ts < b[np.clip(w, 0, None)])
        keep = np.flatnonzero(inwin)
        if not len(keep):
            continue
        token = np.where(asset[keep] == home_tok, 0, 1)
        buy = side[keep] == "BUY"
        s = np.where(buy, token, 1 - token)
        parts.append(pd.DataFrame(dict(
            m=pk, s=s, ts=ts[keep], q=np.where(s == 0, hp[keep], 1 - hp[keep]), size=size[keep],
            print_id=[f"{pk}:{i}" for i in keep], raw_action=side[keep], raw_token_side=token,
            raw_price=t.price.to_numpy(float)[keep])))
    tape = pd.concat(parts, ignore_index=True)
    # Literal price identity: the literal token price equals the normalized price of that token.
    lit = np.where(tape.raw_token_side.eq(0), np.where(tape.s.eq(0), tape.q, 1 - tape.q),
                   np.where(tape.s.eq(1), tape.q, 1 - tape.q))
    bad = ~np.isclose(lit, tape.raw_price, atol=1e-6)
    if bad.mean() > 1e-3:
        raise ValueError(f"home_p/raw price mismatch on {bad.sum()} prints")
    return tape


class Books:
    """Three immutable print indexes shared by every independent variant replay."""

    def __init__(self, tape: pd.DataFrame):
        literal_buy = tape.raw_action.eq("BUY") & tape.raw_token_side.eq(tape.s)
        literal_sell = tape.raw_action.eq("SELL") & tape.raw_token_side.eq(1 - tape.s)
        cols = ["m", "s", "ts", "q", "size", "print_id"]  # no fee_rate column: orders carry it
        self.buy = {"economic": TapeReplay(tape[cols]), "literal": TapeReplay(tape.loc[literal_buy, cols])}
        self.sell = TapeReplay(tape.loc[literal_sell, cols])
        self.provenance = tape.set_index("print_id")[["raw_action", "raw_token_side", "raw_price"]]


# ----------------------------------------------------------------------------- simulation

def simulate(cp: pd.DataFrame, books: Books, thr: float, rule: str, sides: str, entry: str,
             fee_name: str):
    """Sequential per-checkpoint replay, batched across games. Returns (orders, games)."""
    pks = cp.game_pk.unique()
    gi_of = pd.Series(np.arange(len(pks)), index=pks)
    g_all = gi_of.loc[cp.game_pk].to_numpy()
    fee_all = cp.fee_rate.to_numpy(float) if FEES[fee_name] is None else np.full(len(cp), FEES[fee_name])
    D = cp[["disc_home", "disc_away"]].to_numpy(float)
    leader = cp.leader_side.to_numpy()
    kk = cp.k.to_numpy()
    y_home = cp.home_won_final.astype(float).to_numpy()
    G = len(pks)
    sh = np.zeros((G, 2))
    basis = np.zeros((G, 2))
    cash = np.zeros(G)
    peak = np.zeros(G)
    entered = np.zeros(G, bool)
    acc = {k: np.zeros(G) for k in ("buy_cost", "buy_fee", "buy_stake", "sell_gross", "sell_fee",
                                    "buy_orders", "buy_fills", "sell_orders", "sell_fills",
                                    "sell_failed", "blocked_exit_failed", "blocked_cap", "switches")}
    records = []

    for k in range(int(kk.max()) + 1):
        rows = np.flatnonzero(kk == k)
        gi = g_all[rows]
        n = len(rows)
        ar = np.arange(n)
        d = D[rows]
        allowed = np.ones((n, 2), bool) if sides == "both" else np.column_stack([leader[rows] == 0, leader[rows] == 1])
        qual = allowed & (d >= thr)
        has_t = qual.any(1)
        t = np.where(qual[:, 0], 0, 1)
        held = sh[gi] > EPS
        if rule == "hold_first":
            sell = np.zeros((n, 2), bool)
            want = has_t & ~entered[gi]
            entered[gi[has_t]] = True
        elif rule == "hold_retry":
            sell = np.zeros((n, 2), bool)
            want = has_t & ~held.any(1)
        elif rule == "exit_no_discount":
            sell = held & (d < 0)
            want = has_t & ~held[ar, t]
        elif rule == "switch_only":
            sell = held & qual[:, ::-1]
            want = has_t & ~held[ar, t]
        else:
            raise ValueError(rule)
        if (sell.sum(1) > 1).any():
            raise AssertionError("both sides flagged for sale")

        # ---- exits: literal SELL of the held token
        sold = np.zeros(n)
        sell_fill_ts = np.full(n, np.nan)
        srows = np.flatnonzero(sell.any(1))
        if len(srows):
            h = np.where(sell[srows, 0], 0, 1)
            gs = gi[srows]
            orders = pd.DataFrame(dict(
                m=cp.game_pk.to_numpy()[rows[srows]], s=1 - h, signal_ts=cp.decision_ts.to_numpy()[rows[srows]],
                delay_s=DELAY_S, expiry_ts=cp.state_expiry_ts.to_numpy()[rows[srows]],
                target_shares=sh[gs, h], budget_usd=2 * sh[gs, h] + 1, fee_rate=fee_all[rows[srows]],
                event=[f"{x}:{k}:sell" for x in gs], y=0.0))
            orders["receipt_ts"] = orders.signal_ts
            r = books.sell.replay(orders, slip=SLIP, event_cap_usd=np.inf)
            q = r.shares.to_numpy(float)
            price = 1 - r.entry_price.to_numpy(float)  # actual sale price after slip
            gross = np.where(q > 0, q * price, 0.0)
            fee = r.fee_usd.to_numpy(float)
            frac = np.where(sh[gs, h] > 0, q / np.maximum(sh[gs, h], EPS), 0)
            before_basis = basis[gs, h].copy()
            basis[gs, h] = basis[gs, h] * (1 - frac)
            sh[gs, h] = np.maximum(sh[gs, h] - q, 0)
            basis[gs, h] = np.where(sh[gs, h] > EPS, basis[gs, h], 0.0)
            np.add.at(cash, gs, -(gross - fee))
            np.add.at(acc["sell_gross"], gs, gross)
            np.add.at(acc["sell_fee"], gs, fee)
            np.add.at(acc["sell_orders"], gs, 1)
            np.add.at(acc["sell_fills"], gs, (q > 0).astype(float))
            np.add.at(acc["sell_failed"], gs, (q <= 0).astype(float))
            sold[srows] = q
            sell_fill_ts[srows] = r.fill_ts.to_numpy(float)
            records.append(pd.DataFrame(dict(
                game_pk=orders.m, k=k, action="sell", side=np.where(h == 0, "home", "away"),
                signal_ts=orders.signal_ts, eligible_ts=r.eligible_ts, expiry_ts=orders.expiry_ts,
                fill_ts=r.fill_ts, price=np.where(q > 0, price, np.nan), shares=q, requested_shares=orders.target_shares,
                budget_usd=np.nan, stake_or_gross_usd=gross, fee_usd=fee, cash_usd=gross - fee,
                basis_released_usd=before_basis - basis[gs, h], fee_rate=orders.fee_rate,
                status=r.status, print_id=r.print_id, available_shares=r.available_shares,
                disc_side=d[srows, h], ref_price=np.where(h == 0, cp.mkt_p.to_numpy()[rows[srows]], 1 - cp.mkt_p.to_numpy()[rows[srows]]),
                blocked="")))

        # ---- entries: sell first, buy only after the sell fills (causal); size from freed exposure
        pre = sell.any(1)
        blocked_exit = want & pre & (sold <= 0)
        budget = np.minimum(BUDGET_USD, POSITION_CAP_USD - basis[gi].sum(1))
        blocked_cap = want & ~blocked_exit & (budget < MIN_ORDER_USD)
        np.add.at(acc["blocked_exit_failed"], gi[blocked_exit], 1)
        np.add.at(acc["blocked_cap"], gi[blocked_cap], 1)
        brows = np.flatnonzero(want & ~blocked_exit & ~blocked_cap)
        if len(brows):
            gb, tb = gi[brows], t[brows]
            after_sell = pre[brows]
            sig = np.where(after_sell, sell_fill_ts[brows], cp.decision_ts.to_numpy()[rows[brows]])
            yb = np.where(tb == 0, y_home[rows[brows]], 1 - y_home[rows[brows]])
            orders = pd.DataFrame(dict(
                m=cp.game_pk.to_numpy()[rows[brows]], s=tb, signal_ts=sig,
                delay_s=np.where(after_sell, 0.0, DELAY_S), expiry_ts=cp.state_expiry_ts.to_numpy()[rows[brows]],
                budget_usd=budget[brows], fee_rate=fee_all[rows[brows]], event=[f"{x}:{k}:buy" for x in gb], y=yb))
            orders["receipt_ts"] = orders.signal_ts
            r = books.buy[entry].replay(orders, slip=SLIP, event_cap_usd=np.inf)
            q = r.shares.to_numpy(float)
            cost = r.cost_usd.to_numpy(float)
            sh[gb, tb] += q
            basis[gb, tb] += cost
            np.add.at(cash, gb, cost)
            peak[gb] = np.maximum(peak[gb], cash[gb])
            np.add.at(acc["buy_cost"], gb, cost)
            np.add.at(acc["buy_fee"], gb, r.fee_usd.to_numpy(float))
            np.add.at(acc["buy_stake"], gb, r.stake_usd.to_numpy(float))
            np.add.at(acc["buy_orders"], gb, 1)
            np.add.at(acc["buy_fills"], gb, (q > 0).astype(float))
            np.add.at(acc["switches"], gb, ((q > 0) & after_sell).astype(float))
            records.append(pd.DataFrame(dict(
                game_pk=orders.m, k=k, action=np.where(after_sell, "buy_after_sell", "buy"),
                side=np.where(tb == 0, "home", "away"), signal_ts=orders.signal_ts, eligible_ts=r.eligible_ts,
                expiry_ts=orders.expiry_ts, fill_ts=r.fill_ts, price=r.entry_price, shares=q,
                requested_shares=np.nan, budget_usd=orders.budget_usd, stake_or_gross_usd=r.stake_usd.to_numpy(float),
                fee_usd=r.fee_usd.to_numpy(float), cash_usd=-cost, basis_released_usd=0.0, fee_rate=orders.fee_rate,
                status=r.status, print_id=r.print_id, available_shares=r.available_shares,
                disc_side=d[brows, tb], ref_price=np.where(tb == 0, cp.mkt_p.to_numpy()[rows[brows]], 1 - cp.mkt_p.to_numpy()[rows[brows]]),
                blocked="")))
        if blocked_exit.any() or blocked_cap.any():
            b = np.flatnonzero(blocked_exit | blocked_cap)
            records.append(pd.DataFrame(dict(
                game_pk=cp.game_pk.to_numpy()[rows[b]], k=k, action="buy_blocked",
                side=np.where(t[b] == 0, "home", "away"), signal_ts=cp.decision_ts.to_numpy()[rows[b]],
                shares=0.0, stake_or_gross_usd=0.0, fee_usd=0.0, cash_usd=0.0, basis_released_usd=0.0,
                budget_usd=budget[b], disc_side=d[b, t[b]], status="blocked",
                blocked=np.where(blocked_exit[b], "exit_failed", "position_cap"))))
        if (basis.sum(1) > POSITION_CAP_USD + 1e-6).any():
            raise AssertionError("position cap breached")

    first = cp.drop_duplicates("game_pk").set_index("game_pk")
    yg = first.home_won_final.astype(float).reindex(pks).to_numpy()
    payout = sh[:, 0] * yg + sh[:, 1] * (1 - yg)
    games = pd.DataFrame(dict(game_pk=pks, event_date=first.event_date.reindex(pks).to_numpy(),
                              season=first.season.reindex(pks).to_numpy(), **acc))
    games["end_home_shares"], games["end_away_shares"] = sh[:, 0], sh[:, 1]
    games["ended_both_sides"] = (sh > EPS).all(1)
    games["payout"] = payout
    games["sell_net"] = games.sell_gross - games.sell_fee
    games["fees_usd"] = games.buy_fee + games.sell_fee
    games["pnl_usd"] = games.payout + games.sell_net - games.buy_cost
    games["capital_usd"] = peak
    games["turnover_usd"] = games.buy_stake + games.sell_gross
    games["traded"] = games.buy_fills > 0
    if not np.allclose(cash, games.buy_cost - games.sell_net, atol=1e-6):
        raise AssertionError("cash identity")
    if (games.loc[games.traded, "capital_usd"] <= 0).any():
        raise AssertionError("traded game without capital")
    orders = pd.concat(records, ignore_index=True) if records else pd.DataFrame()
    return orders, games


# ----------------------------------------------------------------------------- checks

def check_literal(orders: pd.DataFrame, books: Books, entry: str) -> None:
    """Every filled sell is a literal SELL of the held token; literal buys are literal BUYs."""
    f = orders[orders.shares.gt(0)]
    prov = books.provenance.reindex(f.print_id)
    token = np.where(f.side.eq("home"), 0, 1)
    sells = f.action.eq("sell").to_numpy()
    ok_sell = prov.raw_action.eq("SELL").to_numpy() & (prov.raw_token_side.to_numpy() == token)
    if not ok_sell[sells].all():
        raise AssertionError("exit filled on a non-literal SELL print")
    if entry == "literal":
        ok_buy = prov.raw_action.eq("BUY").to_numpy() & (prov.raw_token_side.to_numpy() == token)
        if not ok_buy[~sells].all():
            raise AssertionError("literal entry filled on a non-literal BUY print")
    if not (f.fill_ts > f.eligible_ts).all() or not (f.fill_ts < f.expiry_ts).all():
        raise AssertionError("fill outside its causal window")


def reconcile_canonical(orders: pd.DataFrame, games: pd.DataFrame) -> dict:
    """hold_first/0.10/leader/economic/as_recorded must equal the canonical 10c ledger."""
    doc = json.loads(CANONICAL.read_text())
    c = pd.DataFrame(doc["rows"], columns=doc["columns"])
    c["game_pk"] = c.print_id.str.split(":").str[0]
    mine = orders[orders.action.eq("buy")].copy()
    res = dict(canonical_signals=len(c), replicated_signals=len(mine),
               canonical_funded=int(c.cost_usd.gt(0).sum()), replicated_funded=int(mine.shares.gt(0).sum()),
               canonical_capital=float(c.cost_usd.sum()), replicated_capital=float(games.capital_usd.sum()),
               canonical_pnl=float(c.pnl_usd.sum()), replicated_pnl=float(games.pnl_usd.sum()))
    by_season = {}
    for per, g in c.groupby("period"):
        season = 2025 if per == "dev" else 2026
        gg = games[games.season.eq(season)]
        by_season[str(season)] = dict(canonical_capital=float(g.cost_usd.sum()), replicated_capital=float(gg.capital_usd.sum()),
                                      canonical_pnl=float(g.pnl_usd.sum()), replicated_pnl=float(gg.pnl_usd.sum()),
                                      canonical_funded=int(g.cost_usd.gt(0).sum()), replicated_funded=int(gg.traded.sum()))
    res["by_season"] = by_season
    # Row-level: same signal clock, same print, same shares.
    cf = c[c.cost_usd.gt(0)].copy()
    cf["pid"] = cf.print_id.map(lambda x: f"{x.split(':')[0]}:{int(float(x.split(':')[1]))}")
    mf = mine[mine.shares.gt(0)].set_index("print_id")
    j = cf.join(mf[["shares", "price", "signal_ts"]], on="pid", rsuffix="_mine")
    res["row_print_matches"] = int(j.shares_mine.notna().sum())
    res["row_share_matches"] = int(np.isclose(j.shares, j.shares_mine, rtol=1e-9, atol=1e-9).sum())
    res["exact"] = bool(res["canonical_signals"] == res["replicated_signals"]
                        and res["canonical_funded"] == res["replicated_funded"]
                        and np.isclose(res["canonical_capital"], res["replicated_capital"], atol=1e-6)
                        and np.isclose(res["canonical_pnl"], res["replicated_pnl"], atol=1e-6)
                        and res["row_share_matches"] == res["canonical_funded"])
    return res


# ----------------------------------------------------------------------------- summaries

def _metrics(g: pd.DataFrame, seed=0) -> dict:
    tr = g[g.traded]
    cap = float(tr.capital_usd.sum())
    pnl = float(tr.pnl_usd.sum())
    if len(tr):
        roi, lo, hi = cluster_ci((tr.pnl_usd / tr.capital_usd).to_numpy(), tr.game_pk.to_numpy(),
                                 weights=tr.capital_usd.to_numpy(), n_boot=N_BOOT, seed=seed)
    else:
        roi = lo = hi = np.nan
    fills = tr.buy_fills.sum() + tr.sell_fills.sum()
    return dict(
        games_evaluated=len(g), signal_games=int((g.buy_orders + g.blocked_exit_failed + g.blocked_cap).gt(0).sum()),
        traded_games=len(tr), buy_orders=int(g.buy_orders.sum()), buy_fills=int(g.buy_fills.sum()),
        sell_orders=int(g.sell_orders.sum()), sell_fills=int(g.sell_fills.sum()), sell_failed=int(g.sell_failed.sum()),
        buys_blocked_exit_failed=int(g.blocked_exit_failed.sum()), buys_blocked_cap=int(g.blocked_cap.sum()),
        switches=int(g.switches.sum()), games_with_switch=int(g.switches.gt(0).sum()),
        games_ended_both_sides=int(g.ended_both_sides.sum()),
        trades_per_traded_game=float(fills / len(tr)) if len(tr) else np.nan,
        buy_notional_usd=float(tr.buy_stake.sum()), sell_notional_usd=float(tr.sell_gross.sum()),
        turnover_usd=float(tr.turnover_usd.sum()),
        turnover_per_capital=float(tr.turnover_usd.sum() / cap) if cap else np.nan,
        capital_usd=cap, capital_per_traded_game=cap / len(tr) if len(tr) else np.nan,
        max_game_capital_usd=float(tr.capital_usd.max()) if len(tr) else np.nan,
        fees_entry_usd=float(tr.buy_fee.sum()), fees_exit_usd=float(tr.sell_fee.sum()),
        fees_usd=float(tr.fees_usd.sum()), pnl_usd=pnl,
        roi_on_capital=roi, roi_ci_lo=lo, roi_ci_hi=hi,
        roi_on_buy_cost=pnl / float(tr.buy_cost.sum()) if len(tr) else np.nan,
        game_win_rate=float(tr.pnl_usd.gt(0).mean()) if len(tr) else np.nan)


def _paired(a: pd.DataFrame, b: pd.DataFrame, seed=0) -> dict:
    """Game-paired P&L difference a - b over the union of games either strategy traded."""
    x = a.set_index("game_pk")
    y = b.set_index("game_pk")
    union = x.index[x.traded].union(y.index[y.traded])
    both = x.index[x.traded].intersection(y.index[y.traded])
    pa = x.pnl_usd.reindex(union).fillna(0).to_numpy()
    pb = y.pnl_usd.reindex(union).fillna(0).to_numpy()
    diff = pa - pb
    if len(diff) >= 5:
        rng = np.random.default_rng(seed)
        boots = diff[rng.integers(0, len(diff), (N_BOOT, len(diff)))].sum(1)
        lo, hi = np.percentile(boots, [2.5, 97.5])
    else:
        lo = hi = np.nan
    ci = lambda z, idx: (z.pnl_usd.reindex(idx).sum() / z.capital_usd.reindex(idx).sum()) if len(idx) else np.nan
    return dict(union_games=len(union), both_traded_games=len(both),
                only_this_games=int(len(x.index[x.traded].difference(y.index[y.traded]))),
                only_ref_games=int(len(y.index[y.traded].difference(x.index[x.traded]))),
                pnl_this_union=float(pa.sum()), pnl_ref_union=float(pb.sum()), pnl_diff=float(diff.sum()),
                pnl_diff_ci_lo=float(lo), pnl_diff_ci_hi=float(hi),
                roi_this_on_both=float(ci(x, both)), roi_ref_on_both=float(ci(y, both)),
                pnl_this_on_both=float(x.pnl_usd.reindex(both).sum()), pnl_ref_on_both=float(y.pnl_usd.reindex(both).sum()))


def _write(df: pd.DataFrame, stem: str, csv=True) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = OUT / f"{stem}.parquet.tmp"
    df.to_parquet(tmp, index=False)
    tmp.replace(OUT / f"{stem}.parquet")
    if csv:
        df.to_csv(OUT / f"{stem}.csv", index=False, float_format="%.6g")


def run(max_games=None) -> dict:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    t0 = time.time()
    cp = load_checkpoints(max_games)
    tape = load_tape(cp)
    books = Books(tape)
    log.info("switching: %d checkpoints, %d games, %d window prints (%d literal SELL) in %.0fs",
             len(cp), cp.game_pk.nunique(), len(tape), len(books.sell.tape), time.time() - t0)
    del tape
    keys = ["thr", "rule", "sides", "entry", "fee_schedule"]
    all_orders, all_games, rows, seasons = [], [], [], []
    game_tables = {}
    for thr, rule, sides, entry, fee in itertools.product(THRESHOLDS, RULES, SIDES, ENTRY, FEES):
        orders, games = simulate(cp, books, thr, rule, sides, entry, fee)
        check_literal(orders, books, entry)
        v = dict(thr=thr, rule=rule, sides=sides, entry=entry, fee_schedule=fee)
        vid = f"{rule}|{thr:.2f}|{sides}|{entry}|{fee}"
        game_tables[(thr, rule, sides, entry, fee)] = games
        rows.append(dict(variant=vid, **v, **_metrics(games)))
        for season, g in games.groupby("season"):
            seasons.append(dict(variant=vid, **v, season=int(season), **_metrics(g)))
        all_orders.append(orders.assign(variant=vid, **v))
        all_games.append(games.assign(variant=vid, **v))
        log.info("%-48s traded=%4d pnl=%9.2f cap=%10.2f roi=%+.4f", vid, rows[-1]["traded_games"],
                 rows[-1]["pnl_usd"], rows[-1]["capital_usd"], rows[-1]["roi_on_capital"])

    recon = None
    if max_games is None:
        key = (COMPARATOR["thr"], COMPARATOR["rule"], COMPARATOR["sides"], COMPARATOR["entry"], "as_recorded")
        o = all_orders[list(game_tables).index(key)]
        recon = reconcile_canonical(o, game_tables[key])
        if not recon["exact"]:
            raise ValueError(f"canonical 10c ledger not reproduced: {recon}")

    comps = []
    for (thr, rule, sides, entry, fee), games in game_tables.items():
        vid = f"{rule}|{thr:.2f}|{sides}|{entry}|{fee}"
        ref10 = game_tables[(COMPARATOR["thr"], COMPARATOR["rule"], COMPARATOR["sides"], COMPARATOR["entry"], fee)]
        refhold = game_tables[(thr, "hold_first", sides, entry, fee)]
        comps.append(dict(variant=vid, thr=thr, rule=rule, sides=sides, entry=entry, fee_schedule=fee,
                          reference="10c_hold_first_leader_economic_same_fee (canonical 10c rule)",
                          **_paired(games, ref10)))
        comps.append(dict(variant=vid, thr=thr, rule=rule, sides=sides, entry=entry, fee_schedule=fee,
                          reference="hold_first_same_thr_sides_entry_fee", **_paired(games, refhold)))

    label = dict(evidence="historical_backtest", label=LABEL)
    summary = pd.DataFrame(rows).assign(**label)
    summary["canonical_replication"] = (summary.rule.eq("hold_first") & np.isclose(summary.thr, .10)
                                        & summary.sides.eq("leader") & summary.entry.eq("economic")
                                        & summary.fee_schedule.eq("as_recorded"))
    by_season = pd.DataFrame(seasons).assign(**label)
    vs = pd.DataFrame(comps).assign(**label)
    orders = pd.concat(all_orders, ignore_index=True).assign(**label)
    games = pd.concat(all_games, ignore_index=True).assign(**label)
    suffix = "" if max_games is None else "_smoke"
    _write(summary, f"switching_summary{suffix}")
    _write(by_season, f"switching_by_season{suffix}")
    _write(vs, f"switching_vs_10c{suffix}")
    _write(games, f"switching_games{suffix}", csv=False)
    orders["print_id"] = orders.print_id.astype("string")
    _write(orders, f"switching_trades{suffix}", csv=False)
    meta = dict(
        label=LABEL, evidence="historical_backtest (exploratory; not forward evidence)",
        variants=len(rows), thresholds=list(THRESHOLDS), rules=list(RULES), sides=list(SIDES), entry=list(ENTRY),
        fee_schedules={k: ("per-game stored rate: 0 in 2025, 0.03/0.05 in 2026" if v is None else v) for k, v in FEES.items()},
        fee_formula="rate * shares * p * (1 - p), charged on entry and exit",
        slip=SLIP, delay_s=DELAY_S, budget_usd=BUDGET_USD, position_cap_usd=POSITION_CAP_USD,
        min_order_usd=MIN_ORDER_USD, execution_engine=EXECUTION_VERSION,
        checkpoints=int(len(cp)), games=int(cp.game_pk.nunique()), split_ts=SPLIT_TS,
        capital_definition="peak cumulative net cash outlay per game (buys incl. fees minus net sale proceeds)",
        comparator=COMPARATOR, canonical_reconciliation=recon, runtime_s=round(time.time() - t0, 1),
        caveats=["Same already-inspected data and checkpoints that selected the 10c rule; many variants; multiple comparisons",
                 "5 s from the retrospective play clock is optimistic; real sports-feed receipt is later",
                 "Historical prints are price/capacity proxies, not book depth; one print per order",
                 "Sells need a later literal SELL of the held token in the same state window; failures are retained",
                 "Switch buys wait for their own sell fill (causal); a failed sell blocks the switch buy",
                 "No top-ups; partial positions stay partial; hold_first reproduces the canonical ledger"])
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"switching_meta{suffix}.json").write_text(json.dumps(meta, indent=1, default=float))
    log.info("switching done in %.0fs", time.time() - t0)
    return meta


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-games", type=int, default=None, help="smoke test on the first N games (writes *_smoke)")
    a = ap.parse_args()
    run(a.max_games)
