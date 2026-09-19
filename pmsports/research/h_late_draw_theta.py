"""Hypothesis `late_draw_theta`: late-match draw underpricing in tied soccer games.

Mechanism (behavioural + contract quirk): late in a tied match, holders of "Will X win? Yes" keep holding / hope
buyers keep bidding a late winner, while the draw's fair value rises every goalless minute; in knockouts "win" pays
on the 90-minute result, so fans who read "win" as "advance" bid team-Yes. All three leave draw-Yes cheap.

Primary rule (pre-registered; parameters frozen before looking at any data):
  UNIVERSE  soccer events (Yes/No legs) whose LARGER team-win leg has pre_usd >= $50k in C.markets() (pregame info
            only). The event's draw leg ('<event_slug>-draw', from C.universe) is always used: fills from the local
            tape when the draw leg is in C.markets(), otherwise fetched from the Data API, so inclusion never depends
            on draw volume. DEV = KO < 2026-07-01, HOLDOUT = KO >= 2026-07-01 (KO = scheduled game_start_ts).
  SIGNAL    the first draw-leg fill with ts >= KO+95 min (search window [KO+95, KO+180] min = the fetched window;
            nothing after KO+180 exists for fetched legs, so the same cut is applied to local legs).
            p = Yes-converted price of that fill (q if the taker acquired Yes, else 1-q). If p in [0.40, 0.90],
            t0 = its ts; otherwise the event has no bet.
  ENTRY     buy draw-Yes as a taker at the first fill acquiring Yes with ts in [t0+3 s, t0+300 s]; price = the
            HIGHEST Yes price printed in that first second (end of a sweep, conservative), plus the taker fee
            fee_rate*p*(1-p) (market's own fee_rate). One bet per event, held to resolution (payout of the draw
            leg's Yes token from C.universe; a 90-minute draw pays 1 in knockouts too).
  METRIC    C.taker_roi per $1 (each bet stakes $1), C.cluster_ci by event_slug, +1c slippage sensitivity.
Variants: (a) maker bid at p-0.01 from t0+3 s, filled when a taker SELLS draw-Yes (acquires No) at a Yes-equivalent
          price <= bid within 300 s (touch) / < bid (trade-through stress); C.maker_roi.
          (b) early window: first draw fill >= KO+60 min with p in [0.20, 0.45].
          (c) knockout vs league split. (d) +1c slippage. (e) team-leg pre_usd >= $100k.

Run:  PYTHONPATH=. systemd-run --user --scope -q -p MemoryMax=4G -p MemorySwapMax=0 .venv/bin/python \
        -m pmsports.research.h_late_draw_theta fetch          # Data API fetch of non-local draw legs (<= 2,000)
      ... -m pmsports.research.h_late_draw_theta              # development only (KO < 2026-07-01)
      ... -m pmsports.research.h_late_draw_theta --holdout    # + the one holdout evaluation
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

from pmsports.research import common as C

SLUG = "late_draw_theta"
CACHE = C.RESEARCH / f"h_{SLUG}"
TR = CACHE / "trades"
T_2026 = pd.Timestamp("2026-01-01", tz="UTC").timestamp()
T_HOLD = pd.Timestamp("2026-07-01", tz="UTC").timestamp()
T_WC_KO = pd.Timestamp("2026-06-28 12:00", tz="UTC").timestamp()   # World Cup round of 32 starts 2026-06-28
MIN = 60
PRE_MIN = 50_000
FETCH_REL = (50 * MIN, 180 * MIN)          # fills kept per draw leg, seconds from scheduled KO
DELAY, WINDOW = 3, 300
TOL = 1e-6                                 # float32 price tolerance
RAW_COLS = ["timestamp", "side", "asset", "outcomeIndex", "price", "size", "transactionHash", "proxyWallet"]

PRIMARY = dict(start=95 * MIN, lo=0.40, hi=0.90)
EARLY = dict(start=60 * MIN, lo=0.20, hi=0.45)

CUPS = {"efl-cup", "fa-cup", "copa-del-rey", "itc-2025", "coupe-de-france", "dfb-pokal", "soccer-brco", "soccer-nlc"}
SUPER_CUPS = {"spanish-super-cup", "uefa-super-cup", "frtc-games", "ecs-games", "gsc-games"}
TOP5 = {"premier-league-2025", "la-liga-2025", "serie-a-2025", "bundesliga-2025", "ligue-1-2025"}
UEFA_CLUB = {"ucl-2025", "uel-2025", "europa-conference-league"}


def period(ko):
    ko = np.asarray(ko, float)
    return np.where(ko < T_2026, "dev25", np.where(ko < T_HOLD, "dev26h1", "hold"))


def league_group(lg: str, ko: float) -> str:
    if lg == "soccer-fifwc":
        return "world_cup_KO" if ko >= T_WC_KO else "world_cup_group"
    if lg in CUPS or lg in SUPER_CUPS:
        return "domestic_cup/super_cup"
    if lg in TOP5:
        return "top5_league"
    if lg in UEFA_CLUB:
        return "uefa_club"
    if lg in {"uef-qualifiers", "fifa-friendly", "caf", "soccer-nlc"}:
        return "internationals"
    if lg.startswith("mls") or lg in {"soccer-lec", "mex-2025"}:
        return "mls/liga_mx"
    return "other_league"


# ----------------------------------------------------------------------------- universe

def events() -> pd.DataFrame:
    """One row per selected event with its draw leg (condition_id, Yes/No token ids, Yes payout, fee_rate)."""
    mk = C.markets()
    s = mk[(mk.family == "soccer") & (mk.o0 == "Yes") & (mk.o1 == "No")]
    team = s[~s.market_slug.str.endswith("-draw")]
    ev = team.groupby("event_slug").agg(pre_team=("pre_usd", "max"), ko=("game_start_ts", "first"),
                                        league=("league", "first"), n_team=("m", "size"))
    ev = ev[ev.pre_team >= PRE_MIN]
    u = C.universe(columns=["condition_id", "token_id", "outcome", "outcome_idx", "payout", "event_slug",
                            "market_slug", "fee_rate", "game_start_ts", "volume"])
    d = u[u.event_slug.isin(ev.index) & u.market_slug.str.endswith("-draw")]
    yes, no = d[d.outcome == "Yes"].set_index("event_slug"), d[d.outcome == "No"].set_index("event_slug")
    assert (yes.outcome_idx == 0).all() and (no.outcome_idx == 1).all()
    assert yes.index.is_unique and set(yes.index) == set(no.index)
    ev = ev.join(yes[["condition_id", "token_id", "payout", "fee_rate", "game_start_ts", "volume"]]
                 .rename(columns={"token_id": "tok_yes", "payout": "y_draw", "game_start_ts": "ko_draw",
                                  "volume": "draw_volume"}), how="inner")
    ev["tok_no"] = no.token_id.reindex(ev.index)
    ev["y_no"] = no.payout.reindex(ev.index)
    assert np.allclose(ev.ko, ev.ko_draw) and np.allclose(ev.y_draw + ev.y_no, 1)
    ev["local"] = ev.condition_id.isin(set(mk.condition_id))
    ev["per"] = period(ev.ko)
    ev["grp"] = [league_group(lg, k) for lg, k in zip(ev.league, ev.ko)]
    ev["knockout"] = ev.grp.isin(["world_cup_KO", "domestic_cup/super_cup"])
    return ev.reset_index()


# ----------------------------------------------------------------------------- fetch / load

def _fetch_one(cid: str, ko: float) -> int:
    from pmsports import polymarket as P
    path = TR / f"{cid}.parquet"
    if path.exists():
        return -1
    rows = P.trades(cid, int(ko) + FETCH_REL[0], int(ko) + FETCH_REL[1])
    df = pd.DataFrame(rows, columns=RAW_COLS) if rows else pd.DataFrame(columns=RAW_COLS)
    df = df[[c for c in RAW_COLS]]
    tmp = path.with_suffix(".tmp")
    df.to_parquet(tmp, index=False)
    tmp.rename(path)
    return len(df)


def fetch() -> None:
    import pmsports.http as H
    H.HOST_RPS["data-api.polymarket.com"] = 5
    TR.mkdir(parents=True, exist_ok=True)
    ev = events()
    todo = ev[~ev.local]
    assert len(todo) <= 2000, len(todo)
    todo = [(r.condition_id, r.ko) for r in todo.itertuples() if not (TR / f"{r.condition_id}.parquet").exists()]
    print(f"fetch: {int((~ev.local).sum())} non-local draw legs, {len(todo)} to fetch", flush=True)
    done = err = rows = 0
    with ThreadPoolExecutor(4) as ex:
        futs = {ex.submit(_fetch_one, c, k): c for c, k in todo}
        for f in as_completed(futs):
            try:
                rows += max(f.result(), 0)
                done += 1
            except Exception as e:  # noqa: BLE001
                err += 1
                print("ERR", futs[f], e, flush=True)
            if (done + err) % 50 == 0:
                print(f"  {done + err}/{len(todo)} (errors {err}, rows {rows:,})", flush=True)
    print(f"fetched {done}, errors {err}, rows {rows:,}")


def _normalize(d: pd.DataFrame, tok_yes: str, tok_no: str) -> pd.DataFrame:
    """Raw Data API rows -> taker acquired s (0 = draw-Yes, 1 = draw-No) at q. The token (`asset`) is
    authoritative; outcomeIndex is kept only for a mismatch count."""
    d = d[d["size"].astype(float) > 0]
    idx = d.asset.map({tok_yes: 0, tok_no: 1})
    no_tok = idx.isna().to_numpy()
    oi = d.outcomeIndex.astype(int).to_numpy()
    idx = np.where(no_tok, oi, idx.fillna(-1).to_numpy()).astype(int)
    buy = (d.side == "BUY").to_numpy()
    px = d.price.astype(float).to_numpy()
    return pd.DataFrame({"ts": d.timestamp.astype(np.int64).to_numpy(), "s": np.where(buy, idx, 1 - idx),
                         "q": np.where(buy, px, 1 - px), "size": d["size"].astype(float).to_numpy(),
                         "oi_mismatch": idx != oi, "no_token": no_tok})


def load_fills(ev: pd.DataFrame) -> pd.DataFrame:
    """Draw-leg fills in [KO+50, KO+180] min for every selected event (local tape or fetched), cached."""
    fn = CACHE / "draw_fills.parquet"
    if fn.exists():
        f = pd.read_parquet(fn)
    else:
        from pmsports.wallets.tapes import TAPES
        parts, missing = [], []
        for r in ev.itertuples():
            p = (TAPES if r.local else TR) / f"{r.condition_id}.parquet"
            if not p.exists():
                missing.append(r.event_slug)
                continue
            d = pd.read_parquet(p, columns=RAW_COLS)
            lo, hi = r.ko + FETCH_REL[0], r.ko + FETCH_REL[1]
            d = d[(d.timestamp >= lo) & (d.timestamp <= hi)]
            if len(d):
                parts.append(_normalize(d, r.tok_yes, r.tok_no).assign(event_slug=r.event_slug))
        if missing:
            raise SystemExit(f"{len(missing)} draw legs not fetched yet (run `fetch`): {missing[:5]}")
        f = pd.concat(parts, ignore_index=True)
        CACHE.mkdir(parents=True, exist_ok=True)
        f.to_parquet(fn, index=False)
    f = f.merge(ev[["event_slug", "ko"]], on="event_slug")
    f["rel"] = (f.ts - f.ko).astype(np.int64)
    f["p_yes"] = np.where(f.s == 0, f.q, 1 - f.q)
    return f.sort_values(["event_slug", "ts"], kind="stable").reset_index(drop=True)


# ----------------------------------------------------------------------------- rule

def signals(f: pd.DataFrame, start: int, lo: float, hi: float) -> pd.DataFrame:
    """First draw fill with rel >= start (within the loaded window). Several fills can share the first second;
    the Data API tape is sorted by (timestamp, transactionHash), so the tie is broken by tx hash (arbitrary, not
    chain order). All fills of that second are public at t0, so any tie-break is free of look-ahead."""
    g = f[f.rel >= start].groupby("event_slug", sort=False).head(1)
    g = g[["event_slug", "ts", "rel", "p_yes", "s", "q"]].rename(columns={"ts": "t0", "rel": "rel0", "p_yes": "p0",
                                                                          "s": "s0", "q": "q0"})
    g["in_band"] = (g.p0 >= lo - TOL) & (g.p0 <= hi + TOL)
    return g


def taker_entries(f: pd.DataFrame, sig: pd.DataFrame, delay=DELAY, window=WINDOW) -> pd.DataFrame:
    """First Yes-acquiring fill with ts in [t0+delay, t0+window]; price = max Yes price in that second.
    Capacity: shares/$ of Yes-acquiring taker fills in [t0+delay, t0+window] at <= entry price + 0.01."""
    x = f[f.s == 0].merge(sig[["event_slug", "t0"]], on="event_slug")
    x = x[(x.ts >= x.t0 + delay) & (x.ts <= x.t0 + window)]
    first = x.groupby("event_slug").ts.min().rename("t_entry")
    x = x.merge(first, on="event_slug")
    at = x[x.ts == x.t_entry].groupby("event_slug").agg(t_entry=("ts", "first"), px=("q", "max"),
                                                        px_min=("q", "min"))
    x = x.merge(at.px.rename("px_e"), on="event_slug")
    cap = x[x.q <= x.px_e + 0.01 + TOL].assign(usd=lambda d: d["size"] * d.q) \
        .groupby("event_slug").agg(cap_sh=("size", "sum"), cap_usd=("usd", "sum"))
    return at.join(cap).reset_index()


def maker_entries(f: pd.DataFrame, sig: pd.DataFrame, off=0.01, through=False, delay=DELAY,
                  window=WINDOW) -> pd.DataFrame:
    """Resting bid for draw-Yes at b = p0 - off from t0+delay; filled (at b) by the first taker who SELLS Yes
    (acquires No) at a Yes-equivalent price <= b (touch) or < b (through) within [t0+delay, t0+window].
    Fill size is capped at that taker's size (reported as capacity)."""
    x = f[f.s == 1].merge(sig[["event_slug", "t0", "p0"]], on="event_slug")
    x["bid"] = x.p0 - off
    x = x[(x.ts >= x.t0 + delay) & (x.ts <= x.t0 + window)]
    ok = (x.p_yes < x.bid - TOL) if through else (x.p_yes <= x.bid + TOL)
    x = x[ok]
    g = x.groupby("event_slug").agg(t_fill=("ts", "first"), bid=("bid", "first"), fill_sh=("size", "first"),
                                    fill_p=("p_yes", "first"))
    return g.reset_index()


def build_bets(f, ev, rule=PRIMARY, maker=False, through=False, off=0.01) -> pd.DataFrame:
    sig = signals(f, rule["start"], rule["lo"], rule["hi"])
    sig = sig[sig.in_band]
    if maker:
        e = maker_entries(f, sig, off=off, through=through)
        b = sig.merge(e, on="event_slug").rename(columns={"bid": "px"})
    else:
        e = taker_entries(f, sig)
        b = sig.merge(e, on="event_slug")
    b = b.merge(ev[["event_slug", "ko", "league", "grp", "knockout", "per", "pre_team", "fee_rate", "y_draw",
                    "local", "draw_volume"]], on="event_slug")
    assert b.event_slug.is_unique
    return b


def roi(b: pd.DataFrame, maker=False, slip=0.0) -> np.ndarray:
    if maker:          # makers: no fee, 15% rebate; the 'worse fill' stress is the trade-through variant
        return C.maker_roi(b.px, b.y_draw, b.fee_rate)
    return C.taker_roi(b.px, b.y_draw, b.fee_rate, slip=slip)


def summ(b: pd.DataFrame, maker=False, slip=0.0) -> dict:
    if len(b) == 0:
        return dict(bets=0)
    r = roi(b, maker, slip)
    m, lo, hi = C.cluster_ci(r, b.event_slug)
    cost = np.clip(b.px + slip, 0.001, 0.999)
    fee = 0 if maker else C.taker_fee(1.0, cost, b.fee_rate)
    return dict(bets=int(len(b)), roi=round(m, 4), ci_lo=round(lo, 4), ci_hi=round(hi, 4),
                draw_rate=round(float(b.y_draw.mean()), 4), avg_px=round(float(b.px.mean()), 4),
                avg_fee=round(float(np.mean(fee)), 4), edge_c=round(100 * float((b.y_draw - cost - fee).mean()), 2),
                pnl=round(float(r.sum()), 2), dollars=float(len(b)))


def fmt(name: str, d: dict) -> str:
    if d.get("bets", 0) == 0:
        return f"{name:<44} bets 0"
    return (f"{name:<44} bets {d['bets']:5d}  ROI {d['roi']:+.4f} [{d['ci_lo']:+.4f}, {d['ci_hi']:+.4f}]  "
            f"draw {d['draw_rate']:.3f} px {d['avg_px']:.3f} fee {d['avg_fee']:.4f} edge {d['edge_c']:+.2f}c")


# ----------------------------------------------------------------------------- run

def funnel(f, ev, rule) -> dict:
    sig = signals(f, rule["start"], rule["lo"], rule["hi"])
    return dict(events=int(len(ev)), with_fill_after_start=int(len(sig)), in_band=int(sig.in_band.sum()))


def run(holdout: bool) -> dict:
    ev = events()
    f = load_fills(ev)
    tok = f.groupby("event_slug").agg(mm=("oi_mismatch", "mean"), nt=("no_token", "mean"))
    print(f"events {len(ev)} (local draw legs {int(ev.local.sum())}, fetched {int((~ev.local).sum())}); "
          f"draw fills in window {len(f):,}; outcomeIndex != token on {f.oi_mismatch.mean():.2%} of fills "
          f"({int((tok.mm > 0).sum())} legs), unknown token {f.no_token.mean():.3%}")
    if not holdout:
        ev = ev[ev.per != "hold"].copy()
        f = f[f.event_slug.isin(ev.event_slug)].copy()
    out: dict = {"funnel": {}, "rows": {}}
    for name, rule in (("primary", PRIMARY), ("early", EARLY)):
        for per in ("dev", "hold"):
            e = ev[(ev.per != "hold") if per == "dev" else (ev.per == "hold")]
            if len(e):
                out["funnel"][f"{name}_{per}"] = funnel(f[f.event_slug.isin(e.event_slug)], e, rule)
    print("funnel:", json.dumps(out["funnel"]))

    B = build_bets(f, ev, PRIMARY)
    Bm = build_bets(f, ev, PRIMARY, maker=True)
    Bmt = build_bets(f, ev, PRIMARY, maker=True, through=True)
    Be = build_bets(f, ev, EARLY)
    rows = out["rows"]

    def add(name, b, maker=False, slip=0.0):
        for per, sel in (("dev", b.per != "hold"), ("dev25", b.per == "dev25"), ("dev26h1", b.per == "dev26h1"),
                         ("hold", b.per == "hold")):
            if per == "hold" and not holdout:
                continue
            d = summ(b[sel], maker, slip)
            rows[f"{name}|{per}"] = d
            print(fmt(f"{name} | {per}", d))

    print("\n== primary (taker, KO+95, p in [0.40, 0.90]) ==")
    add("primary", B)
    add("primary +1c", B, slip=0.01)
    add("primary +2c", B, slip=0.02)
    print("\n== variants ==")
    add("(a) maker bid p-1c, touch", Bm, maker=True)
    add("(a) maker bid p-1c, trade-through", Bmt, maker=True)
    add("(b) early KO+60, p in [0.20, 0.45]", Be)
    add("(b) early +1c", Be, slip=0.01)
    add("(c) knockout", B[B.knockout])
    add("(c) league (non-knockout)", B[~B.knockout])
    add("(c) knockout, maker touch", Bm[Bm.knockout], maker=True)
    add("(e) pre_team >= $100k", B[B.pre_team >= 100_000])
    add("(e) pre_team >= $100k +1c", B[B.pre_team >= 100_000], slip=0.01)
    print("\n== diagnostics (not variants) ==")
    add("diag: local draw legs only (vol >= $50k)", B[B.local])
    add("diag: fetched draw legs only", B[~B.local])
    add("diag: entry at MIN price of first second", B.assign(px=B.px_min))
    B = B.assign(delay=B.t_entry - B.t0)
    add("diag: entry within 30 s of t0", B[B.delay <= 30])
    add("diag: entry 31-300 s after t0", B[B.delay > 30])
    print("signal time rel0 (min) quantiles:", (B.rel0 / 60).quantile([.1, .5, .9]).round(1).tolist(),
          " entry delay (s) quantiles:", B.delay.quantile([.1, .5, .9]).tolist())
    for g in sorted(B.grp.unique()):
        add(f"diag: group {g}", B[B.grp == g])
    pb = pd.cut(B.px, [0, 0.5, 0.6, 0.7, 0.8, 1.0])
    for k, g in B.groupby(pb, observed=True):
        add(f"diag: entry px {k}", g)
    out["bets"] = B
    out["bets_maker"] = Bm
    out["bets_early"] = Be
    # capacity
    for per in ("dev", "hold"):
        b = B[(B.per != "hold") if per == "dev" else (B.per == "hold")]
        if len(b) == 0:
            continue
        months = max((b.ko.max() - b.ko.min()) / (30.4 * 86400), 1)
        out[f"capacity_{per}"] = dict(bets=int(len(b)), bets_per_month=round(len(b) / months, 1),
                                      med_cap_usd=round(float(b.cap_usd.median()), 0),
                                      mean_cap_usd=round(float(b.cap_usd.mean()), 0),
                                      p25_cap_usd=round(float(b.cap_usd.quantile(0.25)), 0),
                                      usd_per_month=round(float(b.cap_usd.sum() / months), 0))
        print(f"capacity {per}: {out[f'capacity_{per}']}")
        bm = Bm[(Bm.per != "hold") if per == "dev" else (Bm.per == "hold")]
        if len(bm):
            print(f"maker fill size {per}: median {bm.fill_sh.median():.0f} sh, mean {bm.fill_sh.mean():.0f} sh; "
                  f"fill rate {len(bm)}/{len(b) if len(b) else 0} signals")
    return out


def main(argv):
    if argv[:1] == ["fetch"]:
        fetch()
        return
    holdout = "--holdout" in argv
    out = run(holdout)
    CACHE.mkdir(parents=True, exist_ok=True)
    tag = "holdout" if holdout else "dev"
    out["bets"].to_parquet(CACHE / f"bets_{tag}.parquet", index=False)
    out["bets_maker"].to_parquet(CACHE / f"bets_maker_{tag}.parquet", index=False)
    out["bets_early"].to_parquet(CACHE / f"bets_early_{tag}.parquet", index=False)
    js = {k: v for k, v in out.items() if not k.startswith("bets")}
    (CACHE / f"results_{tag}.json").write_text(json.dumps(js, indent=1, default=str))


if __name__ == "__main__":
    main(sys.argv[1:])
