"""reports/PAPER_TEST.md and desk ledgers from decisions.jsonl + cached settlements.

Primary (post-activation, hash-matched) and shakedown decisions are always summarized
separately; shakedown rows are never evidence. Open positions (filled, unresolved) carry no
P&L and stay out of ROI and ledgers until their market resolves. +1c and the US-fee scenario
reprice the recorded per-level fills; they are not separate executions.
"""
from __future__ import annotations

import gzip
import json
import logging
import math
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from ..polymarket import parse_ts
from ..research.common import RESEARCH, cluster_ci
from . import spec
from .engine import (ACTIVATION, LIVE, PAPER, ROOT, _files, atomic_write, capture_rows, code_hashes, load_json,
                     mlb_map_key, mlb_map_start, pregame_latest, read_jsonl)
from .soccer import kickoff

log = logging.getLogger("pmsports")
REPORT = ROOT / "reports" / "PAPER_TEST.md"
LEDGERS = RESEARCH / "ledgers"
N_BOOT, SEED = spec.COMMON["bootstrap"]["resamples"], spec.COMMON["bootstrap"]["seed"]
US = spec.COMMON["us_fee_coefficient"]
EXTRA = ["policy", "game_id", "shakedown", "reason", "limit", "leader_prob", "book_age_ms", "fee_usd_us",
         "connection_ok", "reference_age_ms", "fee_rate", "decided_ms"]
SPORT = {"mlb": "baseball", "soccer": "soccer"}


def _ts(v):
    try:
        return parse_ts(v) if v else None
    except (ValueError, TypeError):
        return None


# ----------------------------------------------------------------------------- cash frame

def unique(decisions: list[dict]) -> list[dict]:
    """First record per decision key (a key is written once; guards against a second writer)."""
    seen, out = set(), []
    for d in decisions:
        k = d.get("key")
        if k is not None and k in seen:
            continue
        seen.add(k)
        out.append(d)
    return out


def frame(decisions: list[dict], settlements: dict) -> pd.DataFrame:
    """One row per executed-policy decision plus report-only +1c rows, with settled cash."""
    rows = []
    for d in unique(decisions):
        if d.get("policy") not in spec.RULES:
            continue
        rows.append(_cash(d, settlements, adj=0.0))
        for p, r in spec.RULES.items():
            if r.get("report_only") and r["base"] == d["policy"]:
                rows.append(_cash(d, settlements, adj=r["price_adj"]) | dict(policy=p))
    df = pd.DataFrame(rows, columns=list(_cash({"policy": "mlb_10c"}, {}, 0.0)))
    for c in ("shakedown", "filled", "settled", "open"):
        df[c] = df[c].astype(bool)
    for c in ("shares", "stake_usd", "fee_usd", "cost_usd", "fee_usd_us", "cost_us", "payout", "pnl_usd", "pnl_us", "roi",
              "entry_price", "signal_ts"):
        df[c] = df[c].astype(float)
    return df


def _cash(d: dict, settlements: dict, adj: float) -> dict:
    levels = [(min(p + adj, 0.999) - adj, q) for p, q in ((d.get("fill") or {}).get("levels") or [])]
    shares = float(sum(q for _, q in levels)) if levels else float(d.get("shares") or 0.)
    rate = float(d.get("fee_rate") or 0.)
    stake = sum(q * (p + adj) for p, q in levels)
    fee = sum(rate * q * (p + adj) * (1 - p - adj) for p, q in levels)
    fee_us = sum(US * q * (p + adj) * (1 - p - adj) for p, q in levels)
    s = settlements.get(str(d.get("condition_id"))) or {}
    y = (s.get("payouts") or {}).get(str(d.get("token"))) if s.get("status") == "resolved" else None
    filled = shares > 1e-12
    settled = filled and y is not None
    payout = shares * y if settled else 0.
    cost = stake + fee if filled else 0.
    pnl = payout - cost if settled else (0. if not filled else math.nan)
    return dict(policy=d["policy"], sport=d.get("sport"), game_id=str(d.get("game_id")), event=d.get("event") or str(d.get("game_id")),
                league=d.get("league") or ("mlb" if d.get("sport") == "mlb" else None), market=d.get("condition_id"),
                side=(f"{d.get('team')} Yes" if d.get("sport") == "soccer" else d.get("team")) or d.get("token"),
                shakedown=bool(d.get("shakedown", True)), status=d.get("status"), reason=d.get("reason") or "",
                filled=filled, settled=settled, open=filled and not settled, shares=shares if filled else 0.,
                entry_price=stake / shares if filled else math.nan, stake_usd=stake if filled else 0.,
                fee_usd=fee if filled else 0., cost_usd=cost, fee_usd_us=fee_us if filled else 0.,
                cost_us=stake + fee_us if filled else 0., payout=payout, y=y, pnl_usd=pnl,
                pnl_us=payout - (stake + fee_us) if settled else (0. if not filled else math.nan),
                roi=pnl / cost if settled else math.nan, reference_price=d.get("reference_price"),
                signal_ts=(d.get("signal_recv_ms") or 0) / 1000, eligible_ts=(d.get("eligible_ms") or 0) / 1000 or None,
                exit_ts=_ts(s.get("closed_time")) if settled else None, limit=d.get("limit"), leader_prob=d.get("leader_prob"),
                book_age_ms=d.get("book_age_ms"), connection_ok=d.get("connection_ok"),
                reference_age_ms=d.get("reference_age_ms"), fee_rate=d.get("fee_rate"), decided_ms=d.get("decided_ms"),
                scheduled_start_ts=d.get("scheduled_start_ts"))


# ----------------------------------------------------------------------------- statistics

def _roi(df: pd.DataFrame, pnl="pnl_usd", cost="cost_usd") -> float:
    c = df[cost].sum()
    return float(df[pnl].sum() / c) if c > 0 else math.nan


def removal(settled: pd.DataFrame, k: int) -> float:
    g = settled.groupby("game_id")[["pnl_usd", "cost_usd"]].sum().sort_values("pnl_usd", ascending=False)
    rest = g.iloc[k:]
    return float(rest.pnl_usd.sum() / rest.cost_usd.sum()) if len(rest) and rest.cost_usd.sum() > 0 else math.nan


def summarize(df: pd.DataFrame) -> dict:
    st = df[df.settled]
    games = st.groupby("game_id")[["pnl_usd", "cost_usd"]].sum()
    roi, lo, hi = cluster_ci(st.roi, st.game_id.to_numpy(), weights=st.cost_usd.to_numpy(), n_boot=N_BOOT, seed=SEED) \
        if len(st) else (math.nan,) * 3
    n = len(games)
    return dict(attempts=len(df), filled=int(df.filled.sum()), partial=int(df.status.eq("partial").sum()),
                unfilled=int(df.status.eq("unfilled").sum()), rejected=int(df.status.eq("rejected").sum()),
                open=int(df.open.sum()), settled=int(df.settled.sum()), funded_games=int(df[df.filled].game_id.nunique()),
                capital_usd=float(st.cost_usd.sum()), net_cash_usd=float(st.pnl_usd.sum()), roi=roi, ci_lo=lo, ci_hi=hi,
                equal_game_roi=float((games.pnl_usd / games.cost_usd).mean()) if n else math.nan,
                roi_ex_best1=removal(st, 1), roi_ex_best3=removal(st, 3),
                roi_ex_best1pct=removal(st, max(1, math.ceil(0.01 * n))) if n else math.nan,
                us_fee_net_usd=float(st.pnl_us.sum()), us_fee_roi=_roi(st, "pnl_us", "cost_us"))


def paired(a: pd.DataFrame, b: pd.DataFrame) -> dict:
    """Game-level cash difference a - b over games both policies settled (populations differ)."""
    ga = a[a.settled].groupby("game_id").pnl_usd.sum()
    gb = b[b.settled].groupby("game_id").pnl_usd.sum()
    common = ga.index.intersection(gb.index)
    d = (ga[common] - gb[common])
    mean, lo, hi = cluster_ci(d.to_numpy(), common.to_numpy(), n_boot=N_BOOT, seed=SEED) if len(d) else (math.nan,) * 3
    return dict(common_games=len(d), total_diff_usd=float(d.sum()), mean_diff_usd=mean, ci_lo=lo, ci_hi=hi)


def matched_control(p: pd.DataFrame, c: pd.DataFrame) -> dict:
    """Primary minus control ROI over common whole-cent reference bins, control reweighted to the
    primary's capital per bin; joint whole-game bootstrap (2,000, seed 0)."""
    p, c = p[p.settled].copy(), c[c.settled].copy()
    if not len(p) or not len(c):
        return dict(common_bins=0, diff=math.nan, ci_lo=math.nan, ci_hi=math.nan)
    x = pd.concat([p.assign(k=0), c.assign(k=1)], ignore_index=True)
    x["bin"] = np.floor(x.reference_price.astype(float) * 100 + 1e-9).astype(int)
    common = sorted(set(x[x.k == 0].bin) & set(x[x.k == 1].bin))
    x = x[x.bin.isin(common)]
    if not common:
        return dict(common_bins=0, diff=math.nan, ci_lo=math.nan, ci_hi=math.nan)
    games = pd.Index(sorted(x.game_id.unique()))
    gi, bi = games.get_indexer(x.game_id), np.searchsorted(common, x.bin)
    k, cost, pnl = x.k.to_numpy(), x.cost_usd.to_numpy(float), x.pnl_usd.to_numpy(float)
    nb = len(common)

    def diff(w):
        cs = np.bincount(k * nb + bi, cost * w, 2 * nb).reshape(2, nb)
        ps = np.bincount(k * nb + bi, pnl * w, 2 * nb).reshape(2, nb)
        ok = (cs > 0).all(0)
        if not ok.any():
            return math.nan
        wt = cs[0, ok] / cs[0, ok].sum()
        return float((wt * (ps[0, ok] / cs[0, ok] - ps[1, ok] / cs[1, ok])).sum())

    point = diff(np.ones(len(x)))
    rng = np.random.default_rng(SEED)
    boots = [diff(np.bincount(rng.integers(0, len(games), len(games)), minlength=len(games))[gi]) for _ in range(N_BOOT)]
    boots = np.array([b for b in boots if math.isfinite(b)])
    lo, hi = (np.percentile(boots, [2.5, 97.5]) if len(boots) and len(games) >= 5 else (math.nan, math.nan))
    return dict(common_bins=nb, games=len(games), diff=point, ci_lo=float(lo), ci_hi=float(hi))


def diagnostics(df: pd.DataFrame) -> str:
    """Endpoint diagnostics for one policy: calendar/league segments, fill sizes, exposure, drawdown."""
    f = df[df.filled]
    if f.empty:
        return "_no fills_"
    st = f[f.settled].copy()
    L = []
    if len(st):
        st["month"] = pd.to_datetime(st.signal_ts, unit="s", utc=True).dt.strftime("%Y-%m")
        for key in ("month", "league"):
            g = st.groupby(key).agg(games=("game_id", "nunique"), capital=("cost_usd", "sum"), net=("pnl_usd", "sum"))
            g["ROI"] = (g.net / g.capital).map(lambda v: _fmt(v, "pct"))
            g["capital"], g["net"] = g.capital.map(lambda v: _fmt(v, "usd")), g.net.map(lambda v: _fmt(v, "usd"))
            L += [g.reset_index().to_markdown(index=False), ""]
    q = f.cost_usd.quantile([.1, .5, .9])
    L.append(f"Fill cost p10/p50/p90 {_fmt(q[.1], 'usd')}/{_fmt(q[.5], 'usd')}/{_fmt(q[.9], 'usd')}; "
             f"partial fills {int(f.status.eq('partial').sum())}/{len(f)}; no-fill rate {1 - len(f) / len(df):.1%} of attempts.")
    ev = [(t, c) for t, c in zip(f.eligible_ts, f.cost_usd)] + [(t, -c) for t, c in zip(st.exit_ts, st.cost_usd) if t]
    ev.sort(key=lambda x: (x[0], x[1]))
    exposure = np.cumsum([c for _, c in ev]).max() if ev else 0.
    cum = st.dropna(subset=["exit_ts"]).sort_values("exit_ts").pnl_usd.cumsum()
    dd = float((cum.cummax().clip(lower=0) - cum).max()) if len(cum) else 0.
    L.append(f"Maximum concurrent exposure {_fmt(float(exposure), 'usd')} (entry to resolution); "
             f"maximum drawdown of cumulative net cash by resolution time {_fmt(dd, 'usd')}.")
    return "\n".join(L)


# ----------------------------------------------------------------------------- coverage and endpoints

def _day_rows(live: Path, stream: str):
    for day, _, path in _files(Path(live), None, None, (stream,)):
        with (gzip.open(path, "rb") if path.suffix == ".gz" else open(path, "rb")) as f:
            for line in f:
                try:
                    yield day, json.loads(line)
                except ValueError:
                    pass


def _window(activated_ms, start) -> str:
    return "primary" if activated_ms and start and start * 1000 >= activated_ms else "shakedown"


def coverage(live: Path, activated_ms, signals: list[dict], settlements: dict, cover: set | None = None,
             decisions: list[dict] | None = None) -> dict:
    """Discovery coverage per Polymarket game market, admitted by its latest pregame record (a market
    first recorded after its start is `discovered_after_start`), plus missing-capture accounting for
    exact games: no state feed (linescore / ESPN), no signal logged, no book snapshot for every token."""
    out, cover = {}, cover or set()
    sig_games = {(s.get("sport"), str(s.get("game_id"))) for s in signals}
    dec_games = {(d.get("sport"), str(d.get("game_id"))) for d in decisions or ()
                 if d.get("policy") in (spec.ENDPOINTS["mlb"]["primary"], spec.ENDPOINTS["soccer"]["primary"])}
    pre, late = pregame_latest(capture_rows(live, "mlb_map"), mlb_map_key, mlb_map_start)
    rows = []
    for r, disc in [*((r, True) for r in pre.values()), *((r, False) for r in late.values())]:
        pm, start = r.get("pm") or {}, mlb_map_start(r)
        cid = str(pm.get("condition_id")) if pm.get("condition_id") else None
        pk = str(r.get("game_pk"))
        rows.append(dict(match=r.get("match") if disc else "discovered_after_start", window=_window(activated_ms, start),
                         resolved=bool(cid and (settlements.get(cid) or {}).get("status") == "resolved"),
                         game_id=pk, state_feed=f"mlb|{pk}" in cover, signal=("mlb", pk) in sig_games,
                         books=all(f"book|{t}" in cover for t in (pm.get("home_token"), pm.get("away_token"))),
                         primary_decision=("mlb", pk) in dec_games))
    out["mlb_map"] = pd.DataFrame(rows, columns=["match", "window", "resolved", "game_id", "state_feed", "signal", "books",
                                                 "primary_decision"])
    legacy = {day for day, _, _ in _files(Path(live), None, None, ("games",))} - \
        {day for day, _, _ in _files(Path(live), None, None, ("mlb_map",))}
    out["mlb_legacy_markets"] = len({(r.get("game") or {}).get("condition_id") for day, r in _day_rows(live, "games")
                                     if day in legacy} - {None})
    pre, late = pregame_latest(capture_rows(live, "soccer_games"), lambda r: r.get("event_slug"), kickoff)
    rows = []
    for r, disc in [*((r, True) for r in pre.values()), *((r, False) for r in late.values())]:
        slug, espn = str(r.get("event_slug")), r.get("espn") or {}
        yes = [(leg or {}).get("yes_token") for leg in (r.get("legs") or {}).values()]
        rows.append(dict(match=r.get("match") if disc else "discovered_after_start", reason=r.get("reason"),
                         window=_window(activated_ms, r.get("start_ts")), game_id=slug,
                         state_feed=f"espn|{espn.get('id')}" in cover, signal=("soccer", slug) in sig_games,
                         books=bool(yes) and all(f"book|{t}" in cover for t in yes),
                         primary_decision=("soccer", slug) in dec_games))
    out["soccer"] = pd.DataFrame(rows, columns=["match", "reason", "window", "game_id", "state_feed", "signal", "books",
                                                "primary_decision"])
    out["missing"] = missing_table(out)
    out["signals"] = pd.DataFrame(signals)
    return out


def missing_table(cov: dict) -> pd.DataFrame:
    """Exact (pregame) games per sport and window with each capture missing."""
    rows = []
    for sport, key in (("mlb", "mlb_map"), ("soccer", "soccer")):
        m = cov[key]
        m = m[m.match == "exact"] if len(m) else m
        for w, g in (m.groupby("window") if len(m) else ()):
            rows.append({"sport": sport, "window": w, "exact games": len(g), "no state feed": int((~g.state_feed).sum()),
                         "no signal logged": int((~g.signal).sum()), "no book snapshot": int((~g.books).sum()),
                         "primary decision": int(g.primary_decision.sum())})
    return pd.DataFrame(rows)


def _signal_table(sig: pd.DataFrame, sport: str) -> pd.DataFrame:
    if sig.empty or "sport" not in sig:
        return pd.DataFrame()
    s = sig[sig.sport == sport]
    rows = []
    for p in spec.policies(sport):
        ev = s.evals.map(lambda e: (e or {}).get(p) if isinstance(e, dict) else None) if "evals" in s else pd.Series(dtype=object)
        rows.append({"policy": p, "signals": int(len(s)), **ev.value_counts().to_dict()})
    t = pd.DataFrame(rows).fillna(0)
    if "status" in s:
        for k, v in s.status.value_counts().items():
            if k != "evaluated":
                t[f"state:{k}"] = int(v)
    return t


def endpoint(sport: str, df: pd.DataFrame, cov: dict, activation: dict | None, now: float) -> dict:
    e = spec.ENDPOINTS[sport]
    act = (activation or {}).get("activated_ms")
    days = (now * 1000 - act) / 86400_000 if act else None
    prim = df[(df.policy == e["primary"]) & ~df.shakedown]
    funded = int(prim[prim.filled].game_id.nunique())
    if sport == "mlb":
        m = cov["mlb_map"]
        n = int(((m.match == "exact") & (m.window == "primary") & m.resolved).sum()) if len(m) else 0
    else:
        n = funded
    reached = act is not None and (n >= e["games"] or days >= e["days"])
    return dict(count=n, target=e["games"], days=days, day_limit=e["days"], funded=funded, reached=reached,
                inconclusive_below=e["min_funded"])


def gate(sport: str, stats: dict, comps: dict, ep: dict) -> list[tuple[str, bool | None]]:
    """Protocol qualification checks on primary rows (None = no settled data yet)."""
    e = spec.ENDPOINTS[sport]
    prim = e["primary"]

    def net(p):
        s = stats.get(p) or {}
        return s.get("net_cash_usd") if s.get("settled") else None

    pos = lambda v: None if v is None or not math.isfinite(v) else v > 0  # noqa: E731
    p = stats.get(prim) or {}
    robust = None if not p.get("settled") else all(pos(p[k]) for k in ("roi_ex_best1", "roi_ex_best3", "roi_ex_best1pct"))
    checks = [("primary net cash > 0", pos(net(prim))),
              ("primary 95% interval lower bound > 0", pos(p.get("ci_lo"))),
              ("+1c sensitivity net cash > 0", pos(net(f"{prim}_plus1c"))),
              ("+2 s delay sensitivity net cash > 0", pos(net("mlb_10c_delay2" if sport == "mlb" else "soccer_delay2"))),
              ("ROI > 0 after removing best 1 / 3 / 1% of games", robust),
              (f"funded primary games >= {e['min_funded']}", ep["funded"] >= e["min_funded"])]
    if sport == "soccer":
        checks += [(f"primary beats matched {c}", pos(comps.get(c, {}).get("diff"))) for c in ("soccer_ctrl_75_85", "soccer_ctrl_70_80")]
    return checks


# ----------------------------------------------------------------------------- ledgers

def ledger_rows(df: pd.DataFrame) -> pd.DataFrame:
    from ..research.ledger_studies import COLUMNS
    x = df[~df.open].copy()
    x["period"] = np.where(x.shakedown, "shakedown", "prospective_paper")
    x["sport"] = x.sport.map(SPORT).fillna(x.sport)
    x["entry_ts"] = np.where(x.filled, x.eligible_ts.astype(float), np.nan)
    x["entry_price"] = np.where(x.filled, x.entry_price, np.nan)
    x["exit_kind"] = np.where(x.settled, "resolution", "unfilled")
    x["exit_price"] = np.where(x.settled, x.y.astype(float), np.nan)
    x["exit_ts"] = np.where(x.settled, x.exit_ts.astype(float), np.nan)
    x["receipt_ts"] = x.signal_ts
    x["expiry_ts"] = None
    x["print_id"] = None
    x["roi"] = np.where(x.settled, x.roi, np.nan)
    x["note"] = [f"{s}{': ' + r if r else ''}; limit {lim}; ref age {a} ms; book age {b} ms; connection {c}"
                 for s, r, lim, a, b, c in zip(x.status, x.reason, x.limit, x.reference_age_ms, x.book_age_ms, x.connection_ok)]
    for col in COLUMNS + EXTRA:
        if col not in x:
            x[col] = None
    return x


def write_ledger(policy: str, df: pd.DataFrame, ledgers: Path, n_open: int) -> dict:
    from ..research.ledger_studies import COLUMNS, _headline, validate_cash
    r = spec.RULES[policy]
    x = ledger_rows(df)
    validate_cash(x)
    x = x.sort_values(["signal_ts", "entry_ts"], kind="stable").reset_index(drop=True)
    x["id"] = np.arange(1, len(x) + 1)
    x["date"] = pd.to_datetime(x.signal_ts, unit="s", utc=True).dt.strftime("%Y-%m-%d")
    cols = COLUMNS + EXTRA
    head = json.loads(pd.Series(_headline(x) if len(x) else {}, dtype=object).to_json(double_precision=15))
    doc = dict(slug=f"paper_{policy}", title=f"Paper test: {policy}", group="Forward paper tests",
               sport=SPORT[r["sport"]], verdict="PROSPECTIVE PAPER TEST (no orders)",
               hypothesis=f"{r['role']} rule {policy} ({spec.VERSION})", mechanism="Receipt-time replay of captured books and feeds",
               entry_rule=json.dumps(spec.plain(r), sort_keys=True),
               exit_rule="Hold to actual resolution; open positions are excluded until resolved",
               cost_model="Market fee schedule at signal time on received-book fills; ROI includes fees",
               periods={"prospective_paper": "games scheduled after activation, hash-matched code",
                        "shakedown": "pre-activation, legacy or hash-mismatched decisions; not evidence"},
               headline=head, caveats=["Received-book crossing is hypothetical; no order admission or queue observed",
                                       f"{n_open} filled positions still open and excluded"],
               report_path="reports/PAPER_TEST.md", code_path="pmsports/paper/report.py",
               truncated=False, n_total_trades=len(x), columns=cols,
               rows=json.loads(x[cols].to_json(orient="values", double_precision=15)))
    ledgers.mkdir(parents=True, exist_ok=True)
    target = ledgers / f"paper_{policy}.json"
    atomic_write(target, json.dumps(doc, separators=(",", ":"), allow_nan=False))
    return doc


# ----------------------------------------------------------------------------- markdown

def _fmt(v, kind=""):
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "n/a"
    if kind == "pct":
        return f"{v:+.2%}"
    if kind == "usd":
        return f"${v:,.2f}"
    return f"{v:,.0f}" if isinstance(v, (int, np.integer)) or float(v).is_integer() else f"{v:,.3f}"


def _stats_table(stats: dict) -> str:
    rows = []
    for p, s in stats.items():
        rows.append({"policy": p, "role": spec.RULES[p]["role"], "attempts": s["attempts"], "filled": s["filled"],
                     "partial": s["partial"], "unfilled": s["unfilled"], "rejected": s["rejected"], "open": s["open"],
                     "settled": s["settled"], "capital": _fmt(s["capital_usd"], "usd"), "net cash": _fmt(s["net_cash_usd"], "usd"),
                     "ROI": _fmt(s["roi"], "pct"), "95% CI": f"{_fmt(s['ci_lo'], 'pct')} .. {_fmt(s['ci_hi'], 'pct')}",
                     "equal-game ROI": _fmt(s["equal_game_roi"], "pct"), "ex best 1": _fmt(s["roi_ex_best1"], "pct"),
                     "ex best 3": _fmt(s["roi_ex_best3"], "pct"), "ex best 1%": _fmt(s["roi_ex_best1pct"], "pct"),
                     "US-fee ROI": _fmt(s["us_fee_roi"], "pct")})
    return pd.DataFrame(rows).to_markdown(index=False) if rows else "_no decisions_"


def _reasons(df: pd.DataFrame) -> str:
    x = df[~df.filled & ~df.policy.map(lambda p: bool(spec.RULES[p].get("report_only"))).astype(bool)]
    if x.empty:
        return "_none_"
    t = x.assign(reason=x.reason.replace("", "unspecified")).groupby(["policy", "reason"]).size().rename("decisions").reset_index()
    return t.to_markdown(index=False)


def _section(title: str, df: pd.DataFrame) -> tuple[str, dict]:
    lines, stats = [f"## {title}", ""], {}
    for sport in ("mlb", "soccer"):
        s = df[df.sport == sport]
        stats.update({p: summarize(s[s.policy == p]) for p in spec.policies(sport, executed=False)})
    lines += ["### Policies", "", _stats_table(stats), "", "### No-fill and rejection reasons", "", _reasons(df), ""]
    return "\n".join(lines), stats


def build(out: Path = PAPER, live: Path = LIVE, report: Path = REPORT, ledgers: Path | None = LEDGERS,
          activation_path: Path = ACTIVATION, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    out = Path(out)
    decisions = unique(read_jsonl(out / "decisions.jsonl"))
    settlements = load_json(out / "settlements.json", {}) or {}
    signals = read_jsonl(out / "signals.jsonl")
    cover = {r.get("key") for r in read_jsonl(out / "coverage.jsonl")}
    activation = load_json(activation_path) if activation_path else None
    act_ms = (activation or {}).get("activated_ms")
    df = frame(decisions, settlements)
    cov = coverage(live, act_ms, signals, settlements, cover, decisions)
    prim, shake = df[~df.shakedown], df[df.shakedown]
    ptxt, pstats = _section("Primary (prospective) results", prim)
    stxt, sstats = _section("Shakedown (not evidence)", shake)
    comps = {c: matched_control(prim[prim.policy == "soccer_added_time"], prim[prim.policy == c])
             for c in ("soccer_ctrl_75_85", "soccer_ctrl_70_80")}
    pair = paired(prim[prim.policy == "mlb_10c"], prim[prim.policy == "mlb_03c"])
    stamp = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    L = ["# Forward paper test", "",
         f"Generated {stamp}. **Paper only: no orders are sent.** Rules: `{spec.VERSION}`, spec hash `{spec.spec_hash()[:12]}`. "
         "Protocols: [MLB](PROSPECTIVE_PROTOCOL.md), [soccer](PROSPECTIVE_SOCCER_PROTOCOL.md).", ""]
    if activation:
        same = code_hashes()["code_sha256"] == activation.get("code_sha256")
        L.append(f"Activated {activation.get('activated_utc')} at commit `{str(activation.get('git_commit'))[:10]}`; "
                 f"code hash `{str(activation.get('code_sha256'))[:12]}` "
                 f"({'matches the current code' if same else '**differs from the current code: new decisions are shakedown**'}).")
    else:
        L.append("**Not activated.** Every decision so far is shakedown; nothing below counts toward an endpoint.")
    L += ["", "## Endpoints and gate", ""]
    for sport in ("mlb", "soccer"):
        ep = endpoint(sport, df, cov, activation, now)
        e = spec.ENDPOINTS[sport]
        days = "n/a" if ep["days"] is None else f"{ep['days']:.1f}"
        L.append(f"**{sport.upper()}** (`{e['primary']}`): {ep['count']}/{ep['target']} {e['count']}, day {days}/{ep['day_limit']}, "
                 f"{ep['funded']} funded primary games. Endpoint {'reached' if ep['reached'] else 'not reached (provisional)'}; "
                 f"fewer than {ep['inconclusive_below']} funded games is inconclusive.")
        L.append("")
        L.append(pd.DataFrame([{"check": k, "status": "n/a" if v is None else "pass" if v else "fail"}
                               for k, v in gate(sport, pstats, comps, ep)]).to_markdown(index=False))
        L.append("")
    L += ["## Coverage", ""]
    m = cov["mlb_map"]
    if len(m):
        L += [m.groupby(["window", "match"]).size().rename("MLB games").reset_index().to_markdown(index=False), ""]
    else:
        L += ["No `mlb_map.jsonl` discovery records yet.", ""]
    if cov["mlb_legacy_markets"]:
        L += [f"Legacy `games.jsonl` MLB markets captured (shakedown-only mapping): {cov['mlb_legacy_markets']}.", ""]
    s = cov["soccer"]
    L += [s.groupby(["window", "match"]).size().rename("soccer games").reset_index().to_markdown(index=False)
          if len(s) else "No `soccer_games.jsonl` discovery records yet.", ""]
    if len(cov["missing"]):
        L += ["Missing captures among exact (pregame-admitted) games; these games stay in the counts above:", "",
              cov["missing"].to_markdown(index=False), ""]
    for sport in ("mlb", "soccer"):
        t = _signal_table(cov["signals"], sport)
        if len(t):
            L += [f"Signals evaluated ({sport}, all windows):", "", t.to_markdown(index=False), ""]
    L += [ptxt, "### Paired and matched comparisons", "",
          f"MLB `mlb_10c` minus `mlb_03c` over {pair['common_games']} common settled games: total {_fmt(pair['total_diff_usd'], 'usd')}, "
          f"mean {_fmt(pair['mean_diff_usd'], 'usd')} (95% CI {_fmt(pair['ci_lo'], 'usd')} .. {_fmt(pair['ci_hi'], 'usd')}).", ""]
    for c, r in comps.items():
        L.append(f"Soccer primary minus `{c}` (common cent bins {r['common_bins']}, control reweighted to primary): "
                 f"{_fmt(r['diff'], 'pct')} (95% CI {_fmt(r['ci_lo'], 'pct')} .. {_fmt(r['ci_hi'], 'pct')}).")
    L += ["", "### Primary diagnostics", ""]
    for sport in ("mlb", "soccer"):
        p = spec.ENDPOINTS[sport]["primary"]
        L += [f"`{p}`:", "", diagnostics(prim[prim.policy == p]), ""]
    L += ["", stxt,
          "## Notes", "",
          "- ROI = net cash / fee-inclusive capital over settled positions; open positions are listed but excluded.",
          "- `+1c` rows reprice every filled share one cent higher (fees recomputed); `US-fee` reprices fees at 0.0695.",
          "- Intervals resample whole games (2,000 resamples, seed 0). Overlapping policies are never summed.",
          "- Shakedown = scheduled before activation, code/spec hash mismatch, legacy capture (no connection markers or mlb_map), "
          "or a model snapshot that is not the pinned one / not logged before the game.", ""]
    Path(report).parent.mkdir(parents=True, exist_ok=True)
    atomic_write(Path(report), "\n".join(L))
    docs = {}
    if ledgers is not None:
        for p in spec.POLICIES:
            x = df[df.policy == p]
            docs[p] = write_ledger(p, x, Path(ledgers), int(x.open.sum()) if len(x) else 0)
    log.info("paper report: %d decisions -> %s", len(decisions), report)
    return dict(frame=df, primary=pstats, shakedown=sstats, coverage=cov, ledgers=docs, comparisons=comps, paired=pair)
