"""reports/PAPER_TEST_US.md and desk ledgers for the US-venue paper test.

Same sections and statistics as the v1 report (reusing its functions), for the US policies, plus
US coverage (every international exact game with its US mapping status) and the preview audit.
Primary (post-activation, hash-matched) and shakedown decisions are summarized separately. Open
positions carry no P&L. Only the US market's published settlement (`resolved`) settles a position:
a position with only the provisional international-resolution fallback stays open in every
statistic, the endpoint and the gate, and its provisional cash is shown in a separate table. The
+1c rows reprice the recorded per-level fills one cent higher (fees recomputed at the US
coefficient). Results are never pooled with the international test.
"""
from __future__ import annotations

import json
import logging
import math
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .. import report as v1r
from ..engine import LIVE, ROOT, atomic_write, capture_rows, is_pregame, load_json, mlb_map_key, mlb_map_start, pregame_latest, read_jsonl
from ..spec import plain
from ..soccer import kickoff
from . import spec
from .engine import ACTIVATION_US, PAPER_US, code_hashes
from .settle import us_universe
from .venue import admit, before_play, in_play_marks, intl_keys, normalize_markets, sport_of, start_s

log = logging.getLogger("pmsports")
REPORT = ROOT / "reports" / "PAPER_TEST_US.md"
LEDGERS = v1r.LEDGERS
GROUP = "Forward paper tests (US venue)"
SPORT = v1r.SPORT
SETTLED = "resolved"                 # the US market's published settlement; nothing else settles a position
PROVISIONAL = "fallback_intl"         # international resolution while the US one is unpublished: shown apart


# ----------------------------------------------------------------------------- cash frame

def _cash(d: dict, settlements: dict, adj: float) -> dict:
    fee_c = float(d.get("fee_coefficient") if d.get("fee_coefficient") is not None else spec.FEE_COEFFICIENT)
    levels = [(min(p + adj, 0.999) - adj, q) for p, q in ((d.get("fill") or {}).get("levels") or [])]
    shares = float(sum(q for _, q in levels)) if levels else float(d.get("shares") or 0.)
    stake = sum(q * (p + adj) for p, q in levels)
    fee = sum(fee_c * q * (p + adj) * (1 - p - adj) for p, q in levels)
    s = settlements.get(str(d.get("market_slug"))) or {}

    def oriented(status):                      # the long-side price x as the bought side's payout
        x = s.get("settlement") if s.get("status") == status else None
        return None if x is None else (float(x) if d.get("us_side") == "long" else 1 - float(x))
    y, yp = oriented(SETTLED), oriented(PROVISIONAL)
    filled = shares > 1e-12
    settled = filled and y is not None
    provisional = filled and not settled and yp is not None
    payout = shares * y if settled else 0.
    cost = stake + fee if filled else 0.
    pnl = payout - cost if settled else (0. if not filled else math.nan)
    team = d.get("team") or d.get("side")
    return dict(policy=d["policy"], sport=d.get("sport"), game_id=str(d.get("game_id")), event=d.get("event") or str(d.get("game_id")),
                league=d.get("league") or ("mlb" if d.get("sport") == "mlb" else None), market=d.get("market_slug"),
                side=f"{team} ({d.get('us_side')})", shakedown=bool(d.get("shakedown", True)), status=d.get("status"),
                reason=d.get("reason") or "", filled=filled, settled=settled, open=filled and not settled,
                shares=shares if filled else 0., entry_price=stake / shares if filled else math.nan,
                stake_usd=stake if filled else 0., fee_usd=fee if filled else 0., cost_usd=cost,
                fee_usd_us=fee if filled else 0., cost_us=cost, payout=payout, y=y, pnl_usd=pnl, pnl_us=pnl,
                roi=pnl / cost if settled else math.nan, reference_price=d.get("reference_price"),
                signal_ts=(d.get("signal_recv_ms") or 0) / 1000, eligible_ts=(d.get("eligible_ms") or 0) / 1000 or None,
                exit_ts=s.get("checked_ts") if settled else None, limit=d.get("limit"), leader_prob=d.get("leader_prob"), book_age_ms=d.get("book_age_ms"),
                connection_ok=d.get("connection_ok"), reference_age_ms=d.get("reference_age_ms"),
                fee_rate=fee_c, decided_ms=d.get("decided_ms"), scheduled_start_ts=d.get("scheduled_start_ts"),
                us_side=d.get("us_side"), settle_source=s.get("source") if settled else None,
                provisional=provisional, y_provisional=yp if provisional else None,
                pnl_provisional=shares * yp - cost if provisional else math.nan,
                quantity=d.get("quantity"), key=d.get("key"))


def frame(decisions: list[dict], settlements: dict) -> pd.DataFrame:
    rows = []
    for d in v1r.unique(decisions):
        if d.get("policy") not in spec.RULES:
            continue
        rows.append(_cash(d, settlements, 0.0))
        for p, r in spec.RULES.items():
            if r.get("report_only") and r["base"] == d["policy"]:
                rows.append(_cash(d, settlements, r["price_adj"]) | dict(policy=p))
    df = pd.DataFrame(rows, columns=list(_cash({"policy": "mlb_10c_us"}, {}, 0.0)))
    for c in ("shakedown", "filled", "settled", "open", "provisional"):
        df[c] = df[c].astype(bool)
    for c in ("shares", "stake_usd", "fee_usd", "cost_usd", "fee_usd_us", "cost_us", "payout", "pnl_usd", "pnl_us", "roi",
              "entry_price", "signal_ts", "pnl_provisional"):
        df[c] = df[c].astype(float)
    return df


# ----------------------------------------------------------------------------- coverage and endpoints

def coverage(live: Path, activated_ms, signals: list[dict], settlements: dict, cover: set, decisions: list[dict],
             in_play: dict | None = None) -> dict:
    """US events by pregame match status, and every exact international game with its US status.
    `in_play` (venue.in_play_marks of the engine's coverage.jsonl) applies the engine's rule that a
    us_map record received after its international game was in play is not pregame."""
    in_play = in_play or {}
    sig_games = {(s.get("sport"), str(s.get("game_id"))) for s in signals}
    dec_games = {(d.get("sport"), str(d.get("game_id"))) for d in decisions
                 if d.get("policy") in (spec.ENDPOINTS["mlb"]["primary"], spec.ENDPOINTS["soccer"]["primary"])}
    pre = us_universe(live, in_play)
    late = {}
    for r in capture_rows(live, "us_map"):
        slug = r.get("us_event_slug") or r.get("event_slug")
        if slug and str(slug) not in pre:
            late[str(slug)] = r
    rows = []
    for r, disc in [*((r, True) for r in pre.values()), *((r, False) for r in late.values())]:
        legs = normalize_markets(r)
        slugs = sorted({leg.slug for leg in legs.values()})
        start = start_s(r)
        rows.append(dict(sport=sport_of(r), us_event=str(r.get("us_event_slug") or r.get("event_slug")),
                         match=r.get("match") if disc else "discovered_after_start", reason=r.get("reason"),
                         window=v1r._window(activated_ms, start),
                         resolved=bool(slugs) and all((settlements.get(s) or {}).get("status") == "resolved" for s in slugs),
                         books=bool(slugs) and all(f"usbook|{s}" in cover for s in slugs),
                         trades=bool(slugs) and any(f"ustrade|{s}" in cover for s in slugs)))
    us = pd.DataFrame(rows, columns=["sport", "us_event", "match", "reason", "window", "resolved", "books", "trades"])
    by_key = {}                  # intl key -> latest pregame record per us_map key (incl. no_us_market records)
    for r in _pregame_by_key(live, in_play).values():
        for k in intl_keys(r):
            by_key.setdefault(k, []).append(r)
    late_keys = {k for r in late.values() for k in intl_keys(r)}
    # the international universe (exact pregame games) and each game's US status
    rows = []
    mpre, _ = pregame_latest(capture_rows(live, "mlb_map"), mlb_map_key, mlb_map_start)
    for r in mpre.values():
        if r.get("match") != "exact":
            continue
        pm = r.get("pm") or {}
        pk = str(r.get("game_pk"))
        keys = [("mlb", pk), ("cid", str(pm.get("condition_id"))), ("slug", str(pm.get("slug")))]
        rows.append(_intl_row("mlb", pk, mlb_map_start(r), keys, by_key, late_keys, activated_ms, cover, sig_games, dec_games, f"mlb|{pk}"))
    spre, _ = pregame_latest(capture_rows(live, "soccer_games"), lambda r: r.get("event_slug"), kickoff)
    for r in spre.values():
        if r.get("match") != "exact":
            continue
        slug = str(r.get("event_slug"))
        rows.append(_intl_row("soccer", slug, r.get("start_ts"), [("slug", slug)], by_key, late_keys, activated_ms, cover, sig_games,
                              dec_games, f"espn|{(r.get('espn') or {}).get('id')}"))
    intl = pd.DataFrame(rows, columns=["sport", "game_id", "window", "us", "state_feed", "signal", "us_books",
                                       "primary_decision"])
    return dict(us=us, intl=intl, missing=_missing(intl), signals=pd.DataFrame(signals))


def _pregame_by_key(live: Path, in_play: dict | None = None) -> dict:
    out = {}
    for r in capture_rows(live, "us_map"):
        k = r.get("key") or r.get("us_event_slug") or r.get("event_slug")
        ms = int(r.get("recv_ms") or 0)
        if k and is_pregame(ms, start_s(r)) and before_play(r, ms, in_play or {}):
            out[str(k)] = r | {"_recv_ms": ms}
    return out


def _intl_row(sport, gid, start, keys, by_key, late_keys, activated_ms, cover, sig_games, dec_games, feed_key) -> dict:
    recs = list({id(r): r for k in keys for r in by_key.get(k, ())}.values())
    rec, status = admit(recs) if recs else \
        (None, "us_discovered_after_start" if any(k in late_keys for k in keys) else "us_unmapped")
    slugs = {leg.slug for leg in normalize_markets(rec).values()} if rec is not None else set()
    return dict(sport=sport, game_id=gid, window=v1r._window(activated_ms, start), us=status, state_feed=feed_key in cover,
                signal=(sport, gid) in sig_games, us_books=bool(slugs) and all(f"usbook|{s}" in cover for s in slugs),
                primary_decision=(sport, gid) in dec_games)


def _missing(intl: pd.DataFrame) -> pd.DataFrame:
    rows = []
    x = intl[intl.us == "exact"] if len(intl) else intl
    for (sport, w), g in (x.groupby(["sport", "window"]) if len(x) else ()):
        rows.append({"sport": sport, "window": w, "US-exact games": len(g), "no state feed": int((~g.state_feed).sum()),
                     "no signal logged": int((~g.signal).sum()), "no US book": int((~g.us_books).sum()),
                     "primary decision": int(g.primary_decision.sum())})
    return pd.DataFrame(rows)


def endpoint(sport: str, df: pd.DataFrame, cov: dict, activation: dict | None, now: float) -> dict:
    e = spec.ENDPOINTS[sport]
    act = (activation or {}).get("activated_ms")
    days = (now * 1000 - act) / 86400_000 if act else None
    prim = df[(df.policy == e["primary"]) & ~df.shakedown]
    funded = int(prim[prim.filled].game_id.nunique())
    if sport == "mlb":
        u = cov["us"]
        n = int(((u.sport == "mlb") & (u.match == "exact") & (u.window == "primary") & u.resolved).sum()) if len(u) else 0
    else:
        n = funded
    reached = act is not None and (n >= e["games"] or days >= e["days"])
    return dict(count=n, target=e["games"], days=days, day_limit=e["days"], funded=funded, reached=reached,
                inconclusive_below=e["min_funded"])


def gate(sport: str, stats: dict, comps: dict, ep: dict) -> list[tuple[str, bool | None]]:
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
              ("+2 s delay sensitivity net cash > 0", pos(net(spec.SENSITIVITY_DELAY2[sport]))),
              ("ROI > 0 after removing best 1 / 3 / 1% of games", robust),
              (f"funded primary games >= {e['min_funded']}", ep["funded"] >= e["min_funded"])]
    if sport == "soccer":
        checks += [(f"primary beats matched {c}", pos(comps.get(c, {}).get("diff"))) for c in spec.CONTROLS["soccer"]]
    return checks


# ----------------------------------------------------------------------------- preview audit

def preview_audit(decisions: list[dict], previews: list[dict]) -> dict:
    """Preview status per executed entry (an entry with no preview record is `not previewed`)."""
    execd = [d for d in v1r.unique(decisions) if d.get("eligible_ms") is not None and d.get("quantity") is not None]
    pv = {}
    for p in previews:
        pv.setdefault(p.get("key"), p)
    rows = []
    for d in execd:
        p = pv.get(d.get("key")) or {}
        r = p.get("response") or {}
        rows.append(dict(policy=d.get("policy"), key=d.get("key"), shakedown=bool(d.get("shakedown", True)),
                         status=p.get("status") or "not_previewed", why=p.get("why"), lag_ms=p.get("lag_ms"),
                         response_ms=p.get("response_ms"), http_status=r.get("http_status"), state=r.get("state"),
                         cum_quantity=_f(r.get("cum_quantity")), commission=_f(r.get("commission")),
                         reject=r.get("reject_reason") or r.get("message"), sim_status=d.get("status"),
                         sim_shares=float(d.get("shares") or 0.), sim_fee=float(d.get("fee_usd") or 0.),
                         quantity=d.get("quantity"), truncated=bool(p.get("quantity_truncated"))))
    return dict(frame=pd.DataFrame(rows, columns=["policy", "key", "shakedown", "status", "why", "lag_ms", "response_ms",
                                                  "http_status", "state", "cum_quantity", "commission", "reject",
                                                  "sim_status", "sim_shares", "sim_fee", "quantity", "truncated"]))


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _preview_text(pa: dict) -> list[str]:
    x = pa["frame"]
    if x.empty:
        return ["_no executed entries yet_", ""]
    t = x.assign(window=np.where(x.shakedown, "shakedown", "primary")).groupby(["window", "status"]).size()
    L = [t.rename("entries").reset_index().to_markdown(index=False), ""]
    req = x[x.status.isin(["accepted", "rejected", "error"])]
    if len(req):
        ans = req[req.status != "error"]
        acc = f"{(ans.status == 'accepted').mean():.1%}" if len(ans) else "n/a"
        lag = req.lag_ms.astype(float)
        L.append(f"Previewed {len(req)} of {len(x)} executed entries; the exchange answered {len(ans)} "
                 f"(acceptance rate {acc} of answered previews) and {len(req) - len(ans)} failed without an answer "
                 f"about the order (transport, auth, rate limit or server errors); request lag after the entry time "
                 f"p50 {lag.median():,.0f} ms, max {lag.max():,.0f} ms.")
        a = req[req.status == "accepted"]
        if len(a):
            cq = a.cum_quantity.fillna(0).sum()
            L.append(f"Accepted previews: exchange-reported cumulative quantity {cq:,.2f} and commission "
                     f"${a.commission.fillna(0).sum():,.2f}, against simulated fills of {a.sim_shares.sum():,.2f} contracts and "
                     f"simulated fees of ${a.sim_fee.sum():,.2f} for the same entries. A preview validates the order; "
                     "on 2026-09-26 it echoed the order as ORDER_STATE_PENDING_NEW with zero cumulative quantity and "
                     "commission, so it does not estimate fills.")
        rj = req[req.status != "accepted"]
        if len(rj):
            L += ["", rj.assign(reject=rj.reject.fillna("unspecified"), http_status=rj.http_status.fillna("none").astype(str))
                  .groupby(["status", "http_status", "reject"]).size().rename("entries").reset_index().to_markdown(index=False)]
        if x.truncated.any():
            L.append(f"{int(x.truncated.sum())} previews sent a whole-contract quantity below the simulated fractional quantity.")
    else:
        L.append("No entry has been previewed yet (previews are requested only in follow mode, within 15 s of the entry).")
    return L + [""]


# ----------------------------------------------------------------------------- tables, ledgers, markdown

def _stats_table(stats: dict) -> str:
    rows = []
    for p, s in stats.items():
        rows.append({"policy": p, "role": spec.RULES[p]["role"], "attempts": s["attempts"], "filled": s["filled"],
                     "partial": s["partial"], "unfilled": s["unfilled"], "rejected": s["rejected"], "open": s["open"],
                     "settled": s["settled"], "capital": v1r._fmt(s["capital_usd"], "usd"),
                     "net cash": v1r._fmt(s["net_cash_usd"], "usd"), "ROI": v1r._fmt(s["roi"], "pct"),
                     "95% CI": f"{v1r._fmt(s['ci_lo'], 'pct')} .. {v1r._fmt(s['ci_hi'], 'pct')}",
                     "equal-game ROI": v1r._fmt(s["equal_game_roi"], "pct"), "ex best 1": v1r._fmt(s["roi_ex_best1"], "pct"),
                     "ex best 3": v1r._fmt(s["roi_ex_best3"], "pct"), "ex best 1%": v1r._fmt(s["roi_ex_best1pct"], "pct")})
    return pd.DataFrame(rows).to_markdown(index=False) if rows else "_no decisions_"


def _provisional_text(df: pd.DataFrame) -> str:
    """Filled positions still open because the US settlement is unpublished, valued provisionally
    at the international resolution (never part of any statistic, endpoint or gate)."""
    x = df[df.provisional]
    if x.empty:
        return "_none_"
    t = (x.assign(window=np.where(x.shakedown, "shakedown", "primary"))
         .groupby(["window", "policy"]).agg(positions=("key", "size"), capital=("cost_usd", "sum"),
                                            provisional_net_cash=("pnl_provisional", "sum")).reset_index())
    for c in ("capital", "provisional_net_cash"):
        t[c] = t[c].map(lambda v: v1r._fmt(v, "usd"))
    return ("Open positions whose US settlement is unpublished, valued at the international resolution of the same "
            "team (provisional; replaced by the US settlement when it publishes):\n\n" + t.to_markdown(index=False))


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
        stats.update({p: v1r.summarize(s[s.policy == p]) for p in spec.policies(sport, executed=False)})
    lines += ["### Policies", "", _stats_table(stats), "", "### No-fill and rejection reasons", "", _reasons(df), ""]
    return "\n".join(lines), stats


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


def write_ledger(policy: str, df: pd.DataFrame, ledgers: Path, n_open: int) -> dict:
    from ...research.ledger_studies import COLUMNS, _headline, validate_cash
    r = spec.RULES[policy]
    x = v1r.ledger_rows(df)
    validate_cash(x)
    x = x.sort_values(["signal_ts", "entry_ts"], kind="stable").reset_index(drop=True)
    x["id"] = np.arange(1, len(x) + 1)
    x["date"] = pd.to_datetime(x.signal_ts, unit="s", utc=True).dt.strftime("%Y-%m-%d")
    cols = COLUMNS + v1r.EXTRA + ["us_side", "settle_source"]
    for c in cols:
        if c not in x:
            x[c] = None
    head = json.loads(pd.Series(_headline(x) if len(x) else {}, dtype=object).to_json(double_precision=15))
    doc = dict(slug=f"paper_us_{policy}", title=f"Paper test (US venue): {policy}", group=GROUP, sport=SPORT[r["sport"]],
               verdict="PROSPECTIVE PAPER TEST ON POLYMARKET US (no orders)",
               hypothesis=f"{r['role']} rule {policy} ({spec.VERSION}; trigger {r['trigger']} {r['trigger_version']})",
               mechanism="Receipt-time replay of the captured Polymarket US book and trades on frozen v1 triggers",
               entry_rule=json.dumps(plain(r), sort_keys=True),
               exit_rule="Hold to the US market's published settlement; open positions are excluded until settled",
               cost_model="US taker fee 0.0695 x C x p x (1 - p) on received-book fills; ROI includes fees",
               periods={"prospective_paper": "games scheduled after US activation, hash-matched code",
                        "shakedown": "pre-activation or hash-mismatched decisions; not evidence"},
               headline=head, caveats=["Received-book crossing is hypothetical; the exchange preview validates orders only",
                                       f"{n_open} filled positions still open and excluded",
                                       "Never pooled with the international test"],
               report_path="reports/PAPER_TEST_US.md", code_path="pmsports/paper/us/report.py",
               truncated=False, n_total_trades=len(x), columns=cols,
               rows=json.loads(x[cols].to_json(orient="values", double_precision=15)))
    ledgers.mkdir(parents=True, exist_ok=True)
    atomic_write(ledgers / f"paper_us_{policy}.json", json.dumps(doc, separators=(",", ":"), allow_nan=False))
    return doc


def build(out: Path = PAPER_US, live: Path = LIVE, report: Path = REPORT, ledgers: Path | None = LEDGERS,
          activation_path: Path = ACTIVATION_US, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    out = Path(out)
    decisions = v1r.unique(read_jsonl(out / "decisions.jsonl"))
    settlements = load_json(out / "settlements.json", {}) or {}
    signals = read_jsonl(out / "signals.jsonl")
    cover_rows = read_jsonl(out / "coverage.jsonl")
    cover = {r.get("key") for r in cover_rows}
    previews = read_jsonl(out / "previews.jsonl")
    activation = load_json(activation_path) if activation_path else None
    act_ms = (activation or {}).get("activated_ms")
    df = frame(decisions, settlements)
    cov = coverage(live, act_ms, signals, settlements, cover, decisions, in_play_marks(cover_rows))
    prim, shake = df[~df.shakedown], df[df.shakedown]
    ptxt, pstats = _section("Primary (prospective) results", prim)
    stxt, sstats = _section("Shakedown (not evidence)", shake)
    comps = {c: v1r.matched_control(prim[prim.policy == "soccer_added_time_us"], prim[prim.policy == c])
             for c in spec.CONTROLS["soccer"]}
    pair = v1r.paired(prim[prim.policy == "mlb_10c_us"], prim[prim.policy == "mlb_03c_us"])
    pa = preview_audit(decisions, previews)
    stamp = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    L = ["# Forward paper test: Polymarket US venue", "",
         f"Generated {stamp}. **Paper only: no orders are sent; the exchange is asked only for order previews.** "
         f"Rules: `{spec.VERSION}`, spec hash `{spec.spec_hash()[:12]}`. Protocol: "
         "[US venue](PROSPECTIVE_US_VENUE_PROTOCOL.md), triggers from [MLB](PROSPECTIVE_PROTOCOL.md) and "
         "[soccer](PROSPECTIVE_SOCCER_PROTOCOL.md). Separate test; never pooled with the international results.", ""]
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
    u, i = cov["us"], cov["intl"]
    L += [u.groupby(["sport", "window", "match"]).size().rename("US events").reset_index().to_markdown(index=False)
          if len(u) else "No `us_map.jsonl` discovery records yet.", ""]
    if len(i):
        L += ["International exact (pregame) games by US mapping status; games without an exact US event are outside "
              "this test's universe:", "",
              i.groupby(["sport", "window", "us"]).size().rename("games").reset_index().to_markdown(index=False), ""]
    if len(cov["missing"]):
        L += ["Missing captures among games mapped exactly on both venues; these games stay in the counts above:", "",
              cov["missing"].to_markdown(index=False), ""]
    for sport in ("mlb", "soccer"):
        t = _signal_table(cov["signals"], sport)
        if len(t):
            L += [f"Signals evaluated ({sport}, all windows):", "", t.to_markdown(index=False), ""]
    L += [ptxt, "### Paired and matched comparisons", "",
          f"MLB `mlb_10c_us` minus `mlb_03c_us` over {pair['common_games']} common settled games: total "
          f"{v1r._fmt(pair['total_diff_usd'], 'usd')}, mean {v1r._fmt(pair['mean_diff_usd'], 'usd')} "
          f"(95% CI {v1r._fmt(pair['ci_lo'], 'usd')} .. {v1r._fmt(pair['ci_hi'], 'usd')}).", ""]
    for c, r in comps.items():
        L.append(f"Soccer primary minus `{c}` (common cent bins {r['common_bins']}, control reweighted to primary): "
                 f"{v1r._fmt(r['diff'], 'pct')} (95% CI {v1r._fmt(r['ci_lo'], 'pct')} .. {v1r._fmt(r['ci_hi'], 'pct')}).")
    L += ["", "### Primary diagnostics", ""]
    for sport in ("mlb", "soccer"):
        p = spec.ENDPOINTS[sport]["primary"]
        L += [f"`{p}`:", "", v1r.diagnostics(prim[prim.policy == p]), ""]
    L += ["", stxt, "## Provisional settlements (not counted)", "", _provisional_text(df), "",
          "## Preview audit", ""] + _preview_text(pa)
    L += ["## Notes", "",
          "- ROI = net cash / fee-inclusive capital over settled positions; open positions are listed but excluded.",
          "- Fees are the US taker fee (0.0695 x C x p x (1 - p) from 2026-09-25) on every filled level; "
          "`+1c` rows reprice every filled share one cent higher with fees recomputed.",
          "- A short-side (BUY_SHORT) entry is priced in the bought side's terms: reference 1 - long trade, fills at 1 - long bid.",
          "- Intervals resample whole games (2,000 resamples, seed 0). Overlapping policies are never summed.",
          "- Only the US market's published settlement settles a position. The provisional table above uses the "
          "international resolution of the same team and is never part of a statistic, an endpoint or the gate.",
          "- Shakedown = scheduled before US activation, code/spec hash mismatch, missing connection markers, or a model "
          "snapshot that is not the pinned one.", ""]
    Path(report).parent.mkdir(parents=True, exist_ok=True)
    atomic_write(Path(report), "\n".join(L))
    docs = {}
    if ledgers is not None:
        for p in spec.POLICIES:
            x = df[df.policy == p]
            docs[p] = write_ledger(p, x, Path(ledgers), int(x.open.sum()) if len(x) else 0)
    log.info("US paper report: %d decisions -> %s", len(decisions), report)
    return dict(frame=df, primary=pstats, shakedown=sstats, coverage=cov, ledgers=docs, comparisons=comps, paired=pair,
                previews=pa)
