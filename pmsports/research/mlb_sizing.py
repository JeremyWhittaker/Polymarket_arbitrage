"""MLB inning-discount sizing replay: larger per-game budgets under three fee regimes.

HISTORICAL BACKTEST ONLY. Every number here comes from 2025-26 data that was already inspected,
and the 10c cutoff was picked from a threshold grid evaluated on that same data. These are
in-sample, selection-biased estimates. They are not forward evidence.

The module does four things:

1. Reproduction gate. It rebuilds the 10c first-signal, 3c first-signal and 10c every-signal sets
   with the ledger's own functions (``_mlb_checkpoints`` / ``_discount_signals``). At a $100 budget
   with as-recorded fees it must reproduce the canonical 10c ledger exactly: capital $40,559.49,
   P&L $2,969.81, 1,229 funded. If it does not, it raises.
2. Ledger execution at larger sizes. It runs the unchanged ``panel_entries``/``TapeReplay`` model:
   the first later same-side print after 5 s, +1c, and one IOC-like allocation capped by that print
   and by an inclusive-fee budget. Budgets are [100 .. 5000] USD per game, with the event cap set
   equal to the budget, under the as_recorded, intl_0.05 and us_0.0695 fee regimes. Fees are
   charged inside the share sizing (shares = budget / (price + fee per share)). Partial fills and
   no-fills are kept.
3. Capacity. For each signal it totals the same-side print notional strictly after the eligible time,
   at prices <= reference + 1c/2c/5c (and at any price), over four windows: 30/60/120 s, the
   ledger's own order deadline (next play) and the next half-inning. This is an UPPER bound on what
   we could have taken, because those prints are other traders' fills.
4. Sweep execution. This is an UPPER-bound variant that is kept out of the canonical outputs. The
   order takes every later same-side print whose price is <= reference + tol, each at print + 1c
   with the fee inside the budget, until the game budget or the window runs out. The model assumes
   that liquidity equal to each observed print was also available to us. A 1-print, any-price,
   next-play sweep must reproduce the ledger model; the module checks this.

Outputs are written to data/research/performance/ (git-ignored):
  sizing_trades.parquet, sizing_summary.csv                ledger execution (item 2)
  capacity_by_signal.parquet, capacity_summary.csv         capacity (item 3)
  sizing_sweep_trades.parquet, sizing_sweep_summary.csv    sweep upper bound (item 4)

Run with: PYTHONPATH=. nice .venv/bin/python -m pmsports.research.mlb_sizing
"""
from __future__ import annotations

import json
import logging
import time

import numpy as np
import pandas as pd

from ..collect import sport_dir
from ..execution import panel_entries
from ..polymarket import taker_fee
from .common import RESEARCH, cluster_ci
from .ledger_studies import (_discount_signals, _mlb_checkpoints, _mlb_games, _mlb_rows, _period,
                             validate_cash)

log = logging.getLogger("pmsports")
OUT = RESEARCH / "performance"
CANONICAL = RESEARCH / "ledgers" / "mlb_inning_discount_10c_first.json"
CANON_TOTALS = dict(capital_usd=40559.49, pnl_usd=2969.81, funded=1229)
SLIP = 0.01
DELAY_S = 5.0
BUDGETS = [100, 250, 500, 1000, 2000, 5000]
FEES = {"as_recorded": None, "intl_0.05": 0.05, "us_0.0695": 0.0695}
RULES = {"10c_first": (0.10, True), "03c_first": (0.03, True), "10c_every": (0.10, False)}
CAP_WINDOWS = {"30s": 30.0, "60s": 60.0, "120s": 120.0, "next_play": "state", "next_half": "half"}
CAP_TOLS = {"1c": 0.01, "2c": 0.02, "5c": 0.05, "any": np.inf}
SWEEPS = [("next_play", "2c"), ("next_play", "5c"), ("next_half", "2c"), ("next_half", "5c")]
EPS = 1e-9


# ----------------------------------------------------------------------------- signals

def signal_sets() -> dict[str, pd.DataFrame]:
    """Each rule's own signals, from the full checkpoint input (never filtered from another rule)."""
    p = _mlb_checkpoints()
    out = {}
    for rule, (discount, first) in RULES.items():
        s = _discount_signals(p, discount, first).reset_index(drop=True)
        s["rule"], s["discount_rule"], s["first_only"] = rule, discount, first
        s["signal_idx"] = np.arange(len(s))
        s["season"] = pd.to_datetime(s.decision_ts, unit="s", utc=True).dt.year.astype(int)
        s["reference_price"] = s.price_leader
        out[rule] = s
    return out


# ----------------------------------------------------------------------------- ledger execution

def _decorate(sig: pd.DataFrame, r: pd.DataFrame, games: pd.DataFrame, budget, regime, rate) -> pd.DataFrame:
    """Ledger rows in this module's schema (same exit clock and reference as ledger_studies._mlb_rows)."""
    sig = sig.reset_index(drop=True)
    r = r.reset_index(drop=True)
    buy_home = sig.leader_is_home.to_numpy(bool)
    slug = sig.game_pk.map(games.slug).fillna("mlb-game-" + sig.game_pk.astype(str))
    return pd.DataFrame(dict(
        rule=sig.rule, execution="ledger_first_print", budget_usd=float(budget), fee_regime=regime,
        fee_rate=r.fee_rate if rate is None else float(rate), signal_idx=sig.signal_idx,
        game_pk=sig.game_pk.astype(int), event=slug, side=np.where(buy_home, "home", "away"),
        season=sig.season, period=_period(sig.decision_ts), inning=sig.inning, half=sig.half,
        lead=sig.lead, fair_price=sig.fair_leader, reference_price=sig.reference_price,
        signal_ts=sig.decision_ts, eligible_ts=r.eligible_ts, expiry_ts=r.expiry_ts,
        entry_ts=r.fill_ts, exit_ts=sig.game_pk.map(games.closed_ts).astype(float),
        entry_price=r.entry_price, shares=r.shares.astype(float), available_shares=r.available_shares.astype(float),
        stake=r.stake_usd.astype(float), fee=r.fee_usd.astype(float), cost=r.cost_usd.astype(float),
        payout=r.payout.astype(float), pnl=r.pnl_usd.astype(float), roi=r.roi.astype(float),
        won=r.y.astype(float), status=r.status.astype(str), n_prints=(r.cost_usd > 0).astype(int),
        print_id=r.print_id.astype("string")))


def ledger_replay(sig: pd.DataFrame, budget: float, regime: str, games: pd.DataFrame) -> pd.DataFrame:
    """Unchanged panel_entries with a scaled budget/event cap and, optionally, a replacement fee rate.

    The fee rate is written into the replay input, so TapeReplay sizes shares as
    budget / (price + fee per share); the fee is not added afterwards.
    """
    rate = FEES[regime]
    s = sig.copy()
    if rate is not None:
        s["fee_rate"] = float(rate)
    r = panel_entries(s, s.leader_is_home, budget_usd=float(budget), slip=SLIP,
                      event_cap_usd=float(budget)).reset_index(drop=True)
    r["fee_rate"] = s.fee_rate.to_numpy(float)
    return _decorate(s, r, games, budget, regime, rate)


def _as_ledger(t: pd.DataFrame) -> pd.DataFrame:
    return t.rename(columns=dict(stake="stake_usd", fee="fee_usd", cost="cost_usd", pnl="pnl_usd"))


def verify_reproduction(sets, games) -> dict:
    """Stop unless $100/as-recorded reproduces the canonical 10c ledger row by row and in total."""
    doc = json.loads(CANONICAL.read_text())
    canon = pd.DataFrame(doc["rows"], columns=doc["columns"])
    sig = sets["10c_first"]
    mine = ledger_replay(sig, 100.0, "as_recorded", games)
    # The canonical module's own row builder on the same signal set, as a second anchor.
    ref = _mlb_rows(sig, sig.leader_is_home, "reproduction")
    funded = mine[mine.cost > 0]
    totals = dict(capital_usd=round(float(funded.cost.sum()), 2), pnl_usd=round(float(funded.pnl.sum()), 2),
                  funded=int(len(funded)), signals=int(len(mine)))
    problems = []
    for k, v in CANON_TOTALS.items():
        if not np.isclose(totals[k], v, atol=0.005):
            problems.append(f"{k}: {totals[k]} != canonical {v}")
    if len(mine) != len(canon):
        problems.append(f"signal rows {len(mine)} != canonical {len(canon)}")
    else:
        a = mine.sort_values(["signal_ts", "entry_ts"], kind="stable").reset_index(drop=True)
        b = canon.sort_values("id").reset_index(drop=True)
        for x, y in (("cost", "cost_usd"), ("pnl", "pnl_usd"), ("shares", "shares"), ("fee", "fee_usd")):
            if not np.allclose(a[x].to_numpy(float), b[y].to_numpy(float), atol=1e-9, rtol=1e-9):
                problems.append(f"row-level {x} differs from canonical ledger")
        if not np.allclose(a.signal_ts.to_numpy(float), b.signal_ts.to_numpy(float), atol=1e-6):
            problems.append("row-level signal clock differs from canonical ledger")
        if not (a.status.to_numpy() == b.status.to_numpy()).all():
            problems.append("row-level status differs from canonical ledger")
    for x, y in (("cost", "cost_usd"), ("pnl", "pnl_usd")):
        if not np.allclose(mine[x].to_numpy(float), ref[y].to_numpy(float), atol=1e-12):
            problems.append(f"{x} differs from ledger_studies._mlb_rows")
    if problems:
        raise RuntimeError("canonical 10c ledger NOT reproduced; stopping: " + "; ".join(problems))
    log.info("reproduced canonical 10c ledger: %s", totals)
    return totals


# ----------------------------------------------------------------------------- tapes, clocks

def side_tapes(pks, games) -> dict:
    """Same-side taker prints per (game, side), classified exactly as panel.attach_execution does."""
    d = sport_dir("mlb") / "trades"
    tapes = {}
    for pk in sorted(set(int(x) for x in pks)):
        f = d / f"{pk}.parquet"
        if not f.exists():
            continue
        t = pd.read_parquet(f, columns=["timestamp", "home_p", "size", "side", "asset"])
        ht, at = str(games.at[pk, "home_token"]), str(games.at[pk, "away_token"])
        asset, raw = t.asset.astype(str).to_numpy(), t.side.astype(str).to_numpy()
        known = np.isin(asset, [ht, at]) & np.isin(raw, ["BUY", "SELL"])
        ts = t.timestamp.to_numpy(float)[known]
        hp = t.home_p.to_numpy(float)[known]
        size = t["size"].to_numpy(float)[known]
        asset, raw = asset[known], raw[known]
        home = ((asset == ht) & (raw == "BUY")) | ((asset == at) & (raw == "SELL"))
        for s, mask, price in ((0, home, hp), (1, ~home, 1 - hp)):
            q, z, tt = price[mask], size[mask], ts[mask]
            ok = np.isfinite(tt) & (q > 1e-6) & (q < 1 - 1e-6) & np.isfinite(z) & (z > 0)
            o = np.argsort(tt[ok], kind="stable")
            tapes[(pk, s)] = (tt[ok][o], q[ok][o], z[ok][o])
    return tapes


def half_inning_end(sig: pd.DataFrame) -> np.ndarray:
    """Time the half-inning that starts at each checkpoint ends (3rd out), else the game's last play."""
    d = sport_dir("mlb") / "plays"
    pks = sorted(sig.game_pk.astype(int).unique())
    parts = []
    for pk in pks:
        f = d / f"{pk}.parquet"
        if f.exists():
            parts.append(pd.read_parquet(f, columns=["game_pk", "inning", "half", "outs", "end_ts"]))
    plays = pd.concat(parts, ignore_index=True)
    third = plays[plays.outs == 3].groupby(["game_pk", "inning", "half"]).end_ts.max()
    last = plays.groupby("game_pk").end_ts.max()
    key = pd.MultiIndex.from_arrays([sig.game_pk.astype(int), sig.inning.astype(int), sig.half.astype(str)])
    end = third.reindex(key).to_numpy(float)
    return np.where(np.isfinite(end), end, sig.game_pk.map(last).to_numpy(float))


# ----------------------------------------------------------------------------- capacity

def capacity(sig: pd.DataFrame, tapes: dict, half_end: np.ndarray) -> pd.DataFrame:
    """Same-side print notional available after the eligible time (upper bound on takeable size)."""
    rows = []
    for i, r in enumerate(sig.itertuples(index=False)):
        s = 0 if r.leader_is_home else 1
        elig = r.decision_ts + DELAY_S
        rec = dict(rule=r.rule, signal_idx=r.signal_idx, game_pk=int(r.game_pk), season=r.season,
                   side="home" if s == 0 else "away", signal_ts=r.decision_ts, eligible_ts=elig,
                   next_play_ts=r.state_expiry_ts, next_half_ts=half_end[i], reference_price=r.reference_price,
                   fair_price=r.fair_leader, inning=r.inning, half=r.half, lead=r.lead,
                   next_play_window_s=r.state_expiry_ts - elig, next_half_window_s=half_end[i] - elig,
                   exec_first_print_usd=(r.exec_home_p * r.exec_home_size if s == 0 else r.exec_away_p * r.exec_away_size))
        tape = tapes.get((int(r.game_pk), s))
        ts, q, z = tape if tape is not None else (np.array([]),) * 3
        a = np.searchsorted(ts, elig, side="right")  # strictly after the eligible time, as the ledger
        for wname, w in CAP_WINDOWS.items():
            end = elig + w if isinstance(w, float) else (r.state_expiry_ts if w == "state" else half_end[i])
            b = np.searchsorted(ts, end, side="left") if np.isfinite(end) else a  # prints strictly before end
            b = max(a, b)
            qq, zz = q[a:b], z[a:b]
            for tname, tol in CAP_TOLS.items():
                m = qq <= r.reference_price + tol + EPS
                rec[f"usd_{wname}_{tname}"] = float((qq[m] * zz[m]).sum())
                rec[f"shares_{wname}_{tname}"] = float(zz[m].sum())
                rec[f"prints_{wname}_{tname}"] = int(m.sum())
        rows.append(rec)
    return pd.DataFrame(rows)


def capacity_summary(cap: pd.DataFrame) -> pd.DataFrame:
    out = []
    groups = [("10c_first", cap[cap.first_in_game]), ("10c_every", cap[cap.rule == "10c_every"]),
              ("03c_first", cap[cap.rule == "03c_first"])]
    for label, g in groups:
        for season in ("all", 2025, 2026):
            h = g if season == "all" else g[g.season == season]
            if h.empty:
                continue
            for wname in CAP_WINDOWS:
                win = h[f"{wname}_window_s"] if wname in ("next_play", "next_half") else pd.Series(CAP_WINDOWS[wname], index=h.index)
                for tname in CAP_TOLS:
                    x = h[f"usd_{wname}_{tname}"]
                    pct = np.percentile(x, [10, 25, 50, 75, 90])
                    out.append(dict(signal_set=label, season=season, window=wname, price_tol=tname, signals=len(h),
                        window_s_median=float(np.nanmedian(win)), zero_share=float((x <= 0).mean()),
                        p10=pct[0], p25=pct[1], p50=pct[2], p75=pct[3], p90=pct[4], mean=float(x.mean()),
                        share_ge_100=float((x >= 100).mean()), share_ge_500=float((x >= 500).mean()),
                        share_ge_1000=float((x >= 1000).mean()), share_ge_5000=float((x >= 5000).mean()),
                        total_usd=float(x.sum())))
    return pd.DataFrame(out)


# ----------------------------------------------------------------------------- sweep (upper bound)

def sweep_replay(sig: pd.DataFrame, tapes: dict, half_end: np.ndarray, budget: float, regime: str,
                 window: str, tol: float, games: pd.DataFrame, max_prints: int | None = None) -> pd.DataFrame:
    """Aggregate later same-side prints (print price <= reference + tol) until budget/window is used up.

    UPPER BOUND: assumes liquidity equal to each observed print was also available to us. Orders
    in one game share the game budget and each print's shares, and they are processed in signal
    order (an earlier order sweeps its whole window first). The fee is inside the budget.
    """
    rate_fixed = FEES[regime]
    remaining = {}
    spent = {}
    n = len(sig)
    res = {k: np.zeros(n) for k in ("shares", "stake", "fee", "cost", "payout", "n_prints")}
    res.update({k: np.full(n, np.nan) for k in ("entry_ts", "last_fill_ts", "entry_price")})
    status = np.array(["unfilled"] * n, dtype=object)
    order = np.argsort(sig.decision_ts.to_numpy(float), kind="stable")
    cols = sig[["game_pk", "leader_is_home", "decision_ts", "state_expiry_ts", "reference_price", "fee_rate"]].to_numpy(object)
    won = sig.leader_won.to_numpy(float)
    for i in order:
        pk, lead_home, dts, sexp, ref, grate = cols[i]
        pk, s = int(pk), 0 if lead_home else 1
        tape = tapes.get((pk, s))
        if tape is None:
            continue
        ts, q, z = tape
        elig = float(dts) + DELAY_S
        end = float(sexp) if window == "next_play" else float(half_end[i])
        if not np.isfinite(end) or end <= elig:
            continue
        a, b = np.searchsorted(ts, elig, side="right"), np.searchsorted(ts, end, side="left")
        if b <= a:
            continue
        idx = np.arange(a, b)
        idx = idx[q[idx] <= float(ref) + tol + EPS]
        if max_prints is not None:
            idx = idx[:max_prints]
        rem = remaining.setdefault((pk, s), z.copy())
        idx = idx[rem[idx] > 0]
        left = budget - spent.get(pk, 0.0)
        if left <= 1e-10:
            status[i] = "event_cap"
            continue
        if not len(idx):
            continue
        price = q[idx] + SLIP
        ok = (price > 0) & (price < 1)
        idx, price = idx[ok], price[ok]
        if not len(idx):
            continue
        rate = float(grate) if rate_fixed is None else rate_fixed
        per_share = price + taker_fee(1.0, price, rate)
        avail = rem[idx]
        cum = np.cumsum(avail * per_share)
        k = int(np.searchsorted(cum, left - 1e-12, side="left"))  # first print that exhausts the budget
        take = avail.copy()
        if k < len(idx):
            prior = cum[k - 1] if k > 0 else 0.0
            take[k] = min(avail[k], (left - prior) / per_share[k])
            take[k + 1:] = 0.0
        used = take > 0
        rem[idx] = avail - take
        stake = float((take * price).sum())
        cost = float((take * per_share).sum())
        spent[pk] = spent.get(pk, 0.0) + cost
        sh = float(take.sum())
        res["shares"][i], res["stake"][i], res["fee"][i], res["cost"][i] = sh, stake, cost - stake, cost
        res["payout"][i], res["n_prints"][i] = sh * won[i], int(used.sum())
        if sh > 0:
            res["entry_ts"][i], res["last_fill_ts"][i] = ts[idx[used][0]], ts[idx[used][-1]]
            res["entry_price"][i] = stake / sh
            status[i] = "filled" if cost >= budget - 1e-8 else "partial"
    sig = sig.reset_index(drop=True)
    buy_home = sig.leader_is_home.to_numpy(bool)
    slug = sig.game_pk.map(games.slug).fillna("mlb-game-" + sig.game_pk.astype(str))
    pnl = res["payout"] - res["cost"]
    t = pd.DataFrame(dict(
        rule=sig.rule, execution=f"sweep_{window}_{'any' if not np.isfinite(tol) else f'{round(tol*100)}c'}",
        budget_usd=float(budget), fee_regime=regime,
        fee_rate=sig.fee_rate.to_numpy(float) if rate_fixed is None else rate_fixed, signal_idx=sig.signal_idx,
        game_pk=sig.game_pk.astype(int), event=slug, side=np.where(buy_home, "home", "away"), season=sig.season,
        period=_period(sig.decision_ts), inning=sig.inning, half=sig.half, lead=sig.lead,
        fair_price=sig.fair_leader, reference_price=sig.reference_price, signal_ts=sig.decision_ts,
        eligible_ts=sig.decision_ts + DELAY_S,
        expiry_ts=sig.state_expiry_ts.to_numpy(float) if window == "next_play" else half_end,
        entry_ts=res["entry_ts"], last_fill_ts=res["last_fill_ts"],
        exit_ts=sig.game_pk.map(games.closed_ts).astype(float), entry_price=res["entry_price"],
        shares=res["shares"], stake=res["stake"], fee=res["fee"], cost=res["cost"], payout=res["payout"],
        pnl=pnl, roi=np.where(res["cost"] > 0, pnl / np.where(res["cost"] > 0, res["cost"], 1), np.nan),
        won=won, status=status.astype(str), n_prints=res["n_prints"].astype(int)))
    return t


# ----------------------------------------------------------------------------- summaries

def summarize(t: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    rows = []
    for key, g in t.groupby(keys, sort=False):
        f = g[g.cost > 0]
        roi, lo, hi = (cluster_ci(f.roi.to_numpy(), f.game_pk.to_numpy(), weights=f.cost.to_numpy(),
                                  n_boot=2000, seed=0) if len(f) else (np.nan,) * 3)
        gg = f.groupby("game_pk")[["pnl", "cost"]].sum()
        budget = float(g.budget_usd.iloc[0])
        rec = dict(zip(keys, key if isinstance(key, tuple) else (key,)))
        rec.update(signals=len(g), games_signaled=g.game_pk.nunique(), funded=len(f),
            games_funded=f.game_pk.nunique(), fill_rate=len(f) / len(g) if len(g) else np.nan,
            full_fills=int(g.status.eq("filled").sum()), partial=int(g.status.eq("partial").sum()),
            no_fill=int(g.cost.eq(0).sum()), event_cap=int(g.status.eq("event_cap").sum()),
            mean_fill_usd=float(f.cost.mean()) if len(f) else np.nan,
            median_fill_usd=float(f.cost.median()) if len(f) else np.nan,
            mean_game_usd=float(gg.cost.mean()) if len(gg) else np.nan,
            budget_use=float(gg.cost.mean() / budget) if len(gg) else np.nan,
            capital_usd=float(f.cost.sum()), fees_usd=float(f.fee.sum()), pnl_usd=float(f.pnl.sum()),
            roi=roi, ci_lo=lo, ci_hi=hi,
            equal_game_roi=float((gg.pnl / gg.cost).mean()) if len(gg) else np.nan,
            mean_entry_price=float(np.average(f.entry_price, weights=f.cost)) if len(f) else np.nan)
        for season in (2025, 2026):
            h = f[f.season == season]
            rec[f"games_{season}"] = int(h.game_pk.nunique())
            rec[f"capital_{season}"] = float(h.cost.sum())
            rec[f"pnl_{season}"] = float(h.pnl.sum())
            rec[f"roi_{season}"] = float(h.pnl.sum() / h.cost.sum()) if h.cost.sum() > 0 else np.nan
        rows.append(rec)
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- driver

def run(sweep: bool = True) -> dict:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    games = _mlb_games()
    sets = signal_sets()
    totals = verify_reproduction(sets, games)

    # 2. ledger execution at larger budgets and fee regimes
    parts = []
    for rule, sig in sets.items():
        for budget in BUDGETS:
            for regime in FEES:
                t = ledger_replay(sig, budget, regime, games)
                validate_cash(_as_ledger(t))
                parts.append(t)
    trades = pd.concat(parts, ignore_index=True)
    trades.to_parquet(OUT / "sizing_trades.parquet", index=False)
    keys = ["rule", "budget_usd", "fee_regime"]
    summary = summarize(trades, keys)
    summary.to_csv(OUT / "sizing_summary.csv", index=False)
    log.info("ledger replay: %d rows (%.0fs)", len(trades), time.time() - t0)

    # 3. capacity (10c signals; the 3c first-signal set is added for comparison)
    every = sets["10c_every"]
    pks = pd.concat([every.game_pk, sets["03c_first"].game_pk]).unique()
    tapes = side_tapes(pks, games)
    half_every = half_inning_end(every)
    cap = capacity(every, tapes, half_every)
    cap["first_in_game"] = ~every.game_pk.duplicated().to_numpy()
    first_keys = set(zip(sets["10c_first"].game_pk, sets["10c_first"].decision_ts))
    if set(zip(cap.game_pk[cap.first_in_game], cap.signal_ts[cap.first_in_game])) != first_keys:
        raise RuntimeError("10c first-signal set is not the first row of each game in the every-signal set")
    s3 = sets["03c_first"]
    half3 = half_inning_end(s3)
    cap3 = capacity(s3, tapes, half3)
    cap3["first_in_game"] = False  # flag reserved for the 10c first-signal subset
    capall = pd.concat([cap, cap3], ignore_index=True)
    capall.to_parquet(OUT / "capacity_by_signal.parquet", index=False)
    capsum = capacity_summary(capall)
    capsum.to_csv(OUT / "capacity_summary.csv", index=False)
    log.info("capacity: %d signals (%.0fs)", len(capall), time.time() - t0)

    result = dict(reproduction=totals, trades=len(trades))
    if sweep:
        halves = {"10c_first": half_inning_end(sets["10c_first"]), "10c_every": half_every, "03c_first": half3}
        # Gate: a 1-print, any-price, next-play sweep must equal the ledger model at $100.
        s10 = sets["10c_first"]
        check = sweep_replay(s10, tapes, halves["10c_first"], 100.0, "as_recorded", "next_play", np.inf,
                             games, max_prints=1)
        base = trades[(trades.rule == "10c_first") & (trades.budget_usd == 100) & (trades.fee_regime == "as_recorded")]
        if not (np.allclose(check.cost.to_numpy(), base.cost.to_numpy(), atol=1e-9)
                and np.allclose(check.pnl.to_numpy(), base.pnl.to_numpy(), atol=1e-9)):
            raise RuntimeError("tape reconstruction does not reproduce the ledger's first-print execution")
        sw = []
        for rule, sig in sets.items():
            for window, tname in SWEEPS:
                for budget in BUDGETS:
                    for regime in FEES:
                        t = sweep_replay(sig, tapes, halves[rule], float(budget), regime, window,
                                         CAP_TOLS[tname], games)
                        validate_cash(_as_ledger(t))
                        sw.append(t)
        sw = pd.concat(sw, ignore_index=True)
        sw.to_parquet(OUT / "sizing_sweep_trades.parquet", index=False)
        swsum = summarize(sw, ["rule", "execution", "budget_usd", "fee_regime"])
        swsum.to_csv(OUT / "sizing_sweep_summary.csv", index=False)
        result["sweep_rows"] = len(sw)
        log.info("sweep: %d rows (%.0fs)", len(sw), time.time() - t0)
    return result


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-sweep", action="store_true", help="skip the upper-bound multi-print sweep")
    print(run(sweep=not ap.parse_args().no_sweep))
