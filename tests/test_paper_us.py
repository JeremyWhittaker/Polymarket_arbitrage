import calendar
import gzip
import json
import math
import time

import numpy as np
import pandas as pd
import pytest

from pmsports.paper import engine as E
from pmsports.paper import spec as v1spec
from pmsports.paper.us import engine as UE
from pmsports.paper.us import report as UR
from pmsports.paper.us import run as URUN
from pmsports.paper.us import settle as US
from pmsports.paper.us import spec
from pmsports.paper.us.preview import PreviewAuditor, status_of, summarize
from pmsports.paper.us.venue import Venue, in_play_marks, normalize_markets, order_quantity

T = calendar.timegm((2026, 9, 1, 18, 0, 0)) * 1000          # signal time (past: batch watermark)
START_S = T // 1000 - 3600
H, A, CID = "home_tok", "away_tok", "0xmlb"
ML = "aec-mlb-aw-ho-2026-09-01"
FEE = 0.0695


class Cap:
    """Synthetic capture written like the recorders: v1 streams plus us_map / us_book / us_trade."""

    def __init__(self, root):
        self.root, self.rows = root, {}

    def add(self, stream, ms, **rec):
        day = time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))
        self.rows.setdefault((day, stream), []).append({"recv_ms": int(ms), **rec})
        return self

    def write(self, gz=()):
        for (day, stream), rows in self.rows.items():
            d = self.root / day
            d.mkdir(parents=True, exist_ok=True)
            body = "".join(json.dumps(r) + "\n" for r in sorted(rows, key=lambda r: r["recv_ms"]))
            if (day, stream) in gz:
                with gzip.open(d / f"{stream}.jsonl.gz", "wt") as f:
                    f.write(body)
            else:
                (d / f"{stream}.jsonl").write_text(body)
        return self.root

    # --- v1 streams
    def mlb_map(self, ms, pk=1, home=H, away=A, cid=CID, start=START_S, match="exact"):
        pm = dict(condition_id=cid, slug=f"mlb-g{pk}", home_token=home, away_token=away, start_ts=start, fee_rate=.05,
                  fee_exponent=1, tick=.01, min_size=5, seconds_delay=0)
        return self.add("mlb_map", ms, game_pk=pk, game_type="R", scheduled_innings=9, doubleheader="N", game_number=1,
                        mlb_start_ts=start, home_name="Home", away_name="Away", pm=pm, match=match, reason="")

    def ls(self, ms, inning, half, outs, away, home, pk=1):
        return self.add("mlb", ms, game_pk=pk, inning=inning, half=half, outs=outs, away=away, home=home, offense=[])

    # --- US streams
    def us_map(self, ms, slug="mlb-aw-ho-2026-09-01", sport="mlb", intl=None, markets=None, start=START_S, match="exact"):
        """The capture's record (capture_us.map_mlb shape): one MLB instrument, away team long."""
        intl = intl if intl is not None else dict(game_pk=1, condition_id=CID, event_slug="mlb-g1")
        markets = markets if markets is not None else dict(
            slug=ML, long_team="away", tick=.005, min_qty=.01, qty_increment=None, fee_coefficient=FEE,
            long_abbr="aw", long_name="Away", short_abbr="ho", short_name="Home")
        return self.add("us_map", ms, key=slug, sport=sport, us_event_slug=slug, league=sport, us_start_ts=start,
                        intl_key=intl, start_ts=start, markets=markets, match=match, reason="")

    def conn(self, ms, kind):
        return self.add("us_book", ms, conn=kind)

    def book(self, ms, slug=ML, bids=((.25, 200),), offers=((.70, 50), (.72, 100)), state="MARKET_STATE_OPEN"):
        md = dict(marketSlug=slug, state=state,
                  bids=[dict(px=dict(value=f"{p:.4f}", currency="USD"), qty=f"{q:.4f}") for p, q in bids],
                  offers=[dict(px=dict(value=f"{p:.4f}", currency="USD"), qty=f"{q:.4f}") for p, q in offers])
        return self.add("us_book", ms, msg=dict(requestId="b", subscriptionType="SUBSCRIPTION_TYPE_MARKET_DATA", marketData=md))

    def trade(self, ms, price, slug=ML, qty=10):
        tr = dict(marketSlug=slug, price=dict(value=f"{price:.4f}", currency="USD"), quantity=dict(value=f"{qty:.4f}"),
                  tradeTime="2026-09-01T00:00:00Z", maker=dict(side="ORDER_SIDE_SELL"), taker=dict(side="ORDER_SIDE_BUY"),
                  state="TRADE_STATE_NEW")
        return self.add("us_trade", ms, msg=dict(requestId="t", subscriptionType="SUBSCRIPTION_TYPE_TRADE", trade=tr))

    def live_books(self, t0, t1, every=1000, **kw):
        """Connection open at t0, a snapshot (and heartbeat) every second until t1."""
        self.conn(t0, "open")
        for ms in range(t0 + 1, t1, every):
            self.book(ms, **kw)
            self.conn(ms + 1, "heartbeat")
        return self


def model_dir(tmp_path):
    d = tmp_path / "model"
    d.mkdir(exist_ok=True)
    cells = {"-3": .05, "-2": .10, "-1": .30, "1": .70, "2": .90, "3": .95}
    snap = dict(max_season=2025, fallback=.5, levels=[dict(keys=["inn_k", "half", "outs", "bases", "diff_k"], cells={}),
                                                      dict(keys=["inn_k", "half", "diff_k"], cells={}),
                                                      dict(keys=["diff_k"], cells=cells)])
    (d / "mlb_fair_2025.json").write_text(json.dumps(snap, sort_keys=True))
    return d


def run(tmp_path, live, out="out", **kw):
    kw = dict(activation=None, snapshot_log=None, model_dir=model_dir(tmp_path), allow_network=False) | kw
    URUN.run(live=live, out=tmp_path / out, **kw)
    return decisions(tmp_path / out)


def decisions(out):
    return {d["key"]: d for d in E.read_jsonl(out / "decisions.jsonl")}


def signals(out):
    return E.read_jsonl(out / "signals.jsonl")


def mlb_game(cap, t=T, lead="away", ref_long=.75, pk=1, us=True, us_at=None, books=True, **book_kw):
    """Exact on both venues; the leader after the top of the 5th (label bottom 5) leads 3-1.
    The US instrument's long side is the away team (as on the live venue). Games other than pk 1
    get their own intl tokens and no books of their own."""
    toks = dict(home=H, away=A, cid=CID) if pk == 1 else dict(home=f"h{pk}", away=f"a{pk}", cid=f"0x{pk}")
    books = books and pk == 1
    cap.mlb_map(t - 3_600_000, pk=pk, start=t // 1000 - 3600, **toks)
    if us:
        cap.us_map(t - 3_650_000 if us_at is None else us_at, start=t // 1000 - 3600)
    if books:
        cap.live_books(t - 600_000, t + 70_000, **book_kw)
    cap.trade(t - 30_000, ref_long)
    away, home = (3, 1) if lead == "away" else (1, 3)
    cap.ls(t - 60_000, 5, "Top", 2, away, home, pk=pk)
    cap.ls(t, 5, "Top", 3, away, home, pk=pk)
    return cap


# ----------------------------------------------------------------------------- spec

def test_spec_reuses_frozen_v1_triggers_and_pins_them():
    assert spec.VERSION == "us-v1" and set(spec.TRIGGERS.values()) == set(v1spec.POLICIES)
    assert set(spec.POLICIES) == {"mlb_10c_us", "mlb_03c_us", "mlb_10c_us_delay2", "mlb_10c_us_plus1c", "soccer_added_time_us",
                                  "soccer_ctrl_75_85_us", "soccer_ctrl_70_80_us", "soccer_us_delay2", "soccer_us_delay10",
                                  "soccer_us_delay60", "soccer_added_time_us_plus1c"}
    for p, t in spec.TRIGGERS.items():
        u, o = spec.RULES[p], v1spec.RULES[t]
        for k in ("sport", "role", "threshold", "window", "band", "min_delay_s", "extra_delay_s", "price_adj", "report_only"):
            assert u.get(k) == o.get(k), (p, k)
        assert u["trigger_rule_sha256"] == v1spec.rule_hash(t) and u["fee_coefficient"] == FEE and u["budget_usd"] == 100
    assert spec.RULES["soccer_us_delay2"]["base"] == "soccer_added_time_us"
    assert {p: spec.delay_ms(p) for p in spec.EXECUTED} == {
        "mlb_10c_us": 0, "mlb_03c_us": 0, "mlb_10c_us_delay2": 2000, "soccer_added_time_us": 3000, "soccer_ctrl_75_85_us": 3000,
        "soccer_ctrl_70_80_us": 3000, "soccer_us_delay2": 5000, "soccer_us_delay10": 10_000, "soccer_us_delay60": 60_000}
    assert spec.ENDPOINTS["mlb"]["primary"] == "mlb_10c_us" and spec.ENDPOINTS["soccer"]["games"] == 200
    assert spec.spec_hash() != v1spec.spec_hash() and len(spec.spec_hash()) == 64
    with pytest.raises(TypeError):
        spec.RULES["mlb_10c_us"]["threshold"] = 0.2


def test_us_map_market_shapes_and_orientation():
    side_keyed = normalize_markets(dict(markets={"home": dict(slug="aec-x", long=False, tick=.005, min_qty=.01),
                                                 "away": dict(slug="aec-x", long=True, tick=.005)}))
    assert side_keyed["home"].slug == "aec-x" and not side_keyed["home"].long and side_keyed["home"].intent == "ORDER_INTENT_BUY_SHORT"
    assert side_keyed["away"].long and side_keyed["home"].tick == .005 and side_keyed["home"].min_qty == .01
    single = normalize_markets(dict(markets={"moneyline": dict(slug="aec-y", orderPriceMinTickSize=.005, minimumTradeQty=.01,
                                                               marketSides=[dict(long=True, team=dict(ordering="home")),
                                                                            dict(long=False, team=dict(ordering="away"))])}))
    assert single["home"].long and not single["away"].long and single["away"].slug == "aec-y" and single["away"].tick == .005
    soccer = normalize_markets(dict(sport="soccer", tick=.01, markets={"home": "atc-a", "draw": "atc-draw", "away": "atc-b"}))
    assert {s: (leg.slug, leg.long, leg.tick) for s, leg in soccer.items()} == {
        "home": ("atc-a", True, .01), "draw": ("atc-draw", True, .01), "away": ("atc-b", True, .01)}
    listed = normalize_markets(dict(markets=[dict(slug="aec-z", long_side="away", intent=None)]))
    assert listed["away"].long and not listed["home"].long
    assert normalize_markets(dict(markets={"home": dict(slug="aec-q")})) == {}      # MLB side with no orientation


# ----------------------------------------------------------------------------- venue rules

def test_freshness_book_age_disconnect_reopen_and_legacy():
    (snap,), = Cap(None).book(0).rows.values()
    legacy = Venue()                                                  # a day without connection markers
    legacy.on_record("us_book", snap, 1000)
    f = legacy.freshness(ML, 6000)
    assert f["ok"] and f["connection_ok"] == "unknown" and f["book_age_ms"] == 5000
    assert legacy.freshness(ML, 6001)["reason"] == "stale_book" and legacy.freshness("other", 2000)["reason"] == "no_book"
    v = Venue()                                                       # every record in receipt order
    v.on_record("us_book", {"conn": "open"}, 0)
    v.on_record("us_book", snap, 100)
    assert v.freshness(ML, 150)["ok"] and v.freshness(ML, 150)["connection_ok"] is True
    v.on_record("us_book", {"conn": "closed"}, 200)
    f = v.freshness(ML, 250)
    assert f["reason"] == "connection_closed" and f["connection_ok"] is False and f["book_age_ms"] == 150
    v.on_record("us_book", {"conn": "open"}, 300)                     # reconnect: the old book predates it
    assert v.freshness(ML, 320)["reason"] == "no_snapshot_since_open"
    v.on_record("us_book", snap, 350)                                 # the new connection's first snapshot
    f = v.freshness(ML, 400)
    assert f["ok"] and f["connection_ok"] is True and (f["conn_open_ms"], f["conn_closed_ms"], f["book_age_ms"]) == (300, 200, 50)
    for k in range(1, 6):
        v.on_record("us_book", {"conn": "heartbeat"}, 350 + 1000 * k)
    assert v.freshness(ML, 5350)["ok"]
    assert v.freshness(ML, 5351)["reason"] == "stale_book"             # a heartbeat does not refresh a quiet book


def test_reference_strictly_before_at_most_120s_and_short_orientation():
    v = Venue()
    for ms, p in ((1000, .60), (2000, .62), (2000, .64)):
        v.on_record("us_trade", dict(msg=dict(trade=dict(marketSlug=ML, price=dict(value=str(p)), quantity=dict(value="3")))), ms)
    assert v.reference(ML, True, 2000).price == .60                         # same-ms prints are not "before"
    assert v.reference(ML, True, 2001).price == .64
    assert v.reference(ML, False, 2001).price == pytest.approx(.36) and v.reference(ML, False, 2001).ms == 2000
    assert v.reference(ML, True, 122_001) is None
    v.on_record("us_trade", dict(msg=dict(trade=dict(marketSlug=ML, price=dict(value="1.2"), quantity=dict(value="3")))), 3000)
    assert v.reference(ML, True, 3001).price == .64                         # invalid print ignored


def test_quantity_increments_and_budget():
    per = .76 + FEE * .76 * .24
    assert order_quantity(.76, 100, FEE, 1) == math.floor(100 / per) == 129
    assert order_quantity(.76, 100, FEE, .01) == pytest.approx(math.floor(100 / per * 100) / 100)
    assert order_quantity(.5, 100, FEE, 1) * (.5 + FEE * .25) <= 100 < (order_quantity(.5, 100, FEE, 1) + 1) * (.5 + FEE * .25)
    assert order_quantity(.99, 0.5, FEE, 1) == 0


# ----------------------------------------------------------------------------- MLB execution

def test_mlb_long_side_fill_fee_and_no_lookahead(tmp_path):
    cap = mlb_game(Cap(tmp_path / "live"))
    cap.trade(T, .50)                                            # same-ms print: not the reference
    cap.trade(T + 1, .95)                                        # after the decision
    cap.book(T + 1, offers=((.60, 500),))                        # a better book after the entry: invisible
    cap.book(T + 1500, offers=((.72, 30),))
    d = run(tmp_path, cap.write())
    ten, three, late = d["mlb_10c_us|1"], d["mlb_03c_us|1"], d["mlb_10c_us_delay2|1"]
    assert ten["reference_price"] == .75 and ten["reference_age_ms"] == 30_000 and ten["side"] == "away"
    assert ten["market_slug"] == ML and ten["us_side"] == "long" and ten["intent"] == "ORDER_INTENT_BUY_LONG"
    assert ten["leader_prob"] == pytest.approx(.90) and ten["limit"] == .76 and ten["order_price_value"] == .76
    assert ten["trigger_policy"] == "mlb_10c" and ten["intl_token"] == A and ten["intl_condition_id"] == CID
    assert ten["quantity"] == 129 and ten["eligible_ms"] == T and ten["status"] == "filled"
    lv = ten["fill"]["levels"]
    assert lv == [[.70, 50], [.72, 79]]
    fee = sum(FEE * q * p * (1 - p) for p, q in lv)
    assert ten["fee_usd"] == pytest.approx(fee) and ten["cost_usd"] == pytest.approx(.70 * 50 + .72 * 79 + fee)
    assert ten["cost_usd"] <= 100
    assert three["fill"]["levels"] == lv                           # independent consumption per policy
    assert late["eligible_ms"] == T + 2000 and late["fill"]["levels"] == [[.72, 30]] and late["status"] == "partial"
    assert ten["connection_ok"] is True and ten["book_age_ms"] == 999 and ten["shakedown_reasons"] == ["not_activated"]
    assert ten["spec_version"] == "us-v1" and ten["code_sha256"] == UE.code_hashes()["code_sha256"]


def test_mlb_short_side_crosses_long_bids_at_one_minus_bid(tmp_path):
    cap = mlb_game(Cap(tmp_path / "live"), lead="home", ref_long=.25, bids=((.26, 30), (.25, 200), (.20, 500)))
    d = run(tmp_path, cap.write())
    ten = d["mlb_10c_us|1"]
    assert ten["side"] == "home" and ten["us_side"] == "short" and ten["intent"] == "ORDER_INTENT_BUY_SHORT"
    assert ten["reference_price"] == pytest.approx(.75) and ten["leader_prob"] == pytest.approx(.90)
    assert ten["limit"] == .76 and ten["order_price_value"] == pytest.approx(.24)   # the API's long-side price
    assert ten["fill"]["levels"] == [[.74, 30], [.75, 99]] and ten["status"] == "filled"
    assert ten["fee_usd"] == pytest.approx(FEE * (30 * .74 * .26 + 99 * .75 * .25))
    sig = {s["signal_id"]: s for s in signals(tmp_path / "out")}
    assert sig["5bottom"]["evals"]["mlb_10c_us"] == "qualified" and sig["5bottom"]["us_side"] == "short"


def test_tick_rounding_increment_minimum_and_market_state(tmp_path):
    # reference .8525 -> limit .8625 floors to .860 on a half-cent tick (edge .0475: the 3c control only)
    cap = mlb_game(Cap(tmp_path / "live"), ref_long=.8525, offers=((.86, 500),))
    d = run(tmp_path, cap.write())
    x = d["mlb_03c_us|1"]
    assert "mlb_10c_us|1" not in d and x["limit"] == .86 and x["fill"]["levels"] == [[.86, x["quantity"]]]
    from pmsports.paper.engine import floor_tick
    assert floor_tick(.8525 + .01, .01) == .86 and floor_tick(.745 + .01, .005) == .755 and floor_tick(.8649, .005) == .86
    # increment from us_map, minimum above the budget, halted market
    cap = mlb_game(Cap(tmp_path / "live2"), us=False)
    cap.us_map(T - 3_650_000, markets={"away": dict(slug=ML, long=True, tick=.005, qty_increment=.01, min_qty=.01),
                                       "home": dict(slug=ML, long=False, tick=.005)})
    d = run(tmp_path, cap.write(), out="o2")
    per = .76 + FEE * .76 * .24
    assert d["mlb_10c_us|1"]["quantity"] == pytest.approx(math.floor(100 / per * 100) / 100)
    assert d["mlb_10c_us|1"]["shares"] == pytest.approx(d["mlb_10c_us|1"]["quantity"])
    cap = mlb_game(Cap(tmp_path / "live3"), us=False)
    cap.us_map(T - 3_650_000, markets={"away": dict(slug=ML, long=True, tick=.005, min_qty=500)})
    assert run(tmp_path, cap.write(), out="o3")["mlb_10c_us|1"]["reason"] == "below_minimum_order"
    cap = mlb_game(Cap(tmp_path / "live4"), state="MARKET_STATE_HALTED")
    assert run(tmp_path, cap.write(), out="o4")["mlb_10c_us|1"]["reason"] == "market_state:MARKET_STATE_HALTED"
    cap = mlb_game(Cap(tmp_path / "live5"), us=False)
    cap.us_map(T - 3_650_000, markets={"away": dict(slug=ML, long=True)})              # no tick: recorded no-fill
    assert run(tmp_path, cap.write(), out="o5")["mlb_10c_us|1"]["reason"] == "missing_market_rules:tick"


def test_stale_book_and_closed_connection_are_recorded_no_fills(tmp_path):
    cap = mlb_game(Cap(tmp_path / "live"), books=False)
    cap.conn(T - 600_000, "open")
    cap.book(T - 6000)
    for k in range(1, 400):
        cap.conn(T - 600_000 + 2000 * k, "heartbeat")               # live connection, quiet book
    d = run(tmp_path, cap.write())
    assert d["mlb_10c_us|1"]["reason"] == "stale_book" and d["mlb_10c_us|1"]["book_age_ms"] == 6000
    cap = mlb_game(Cap(tmp_path / "live2"), books=False)
    cap.conn(T - 600_000, "open").book(T - 1000).conn(T - 500, "closed")
    d = run(tmp_path, cap.write(), out="o2")
    assert d["mlb_10c_us|1"]["reason"] == "connection_closed" and d["mlb_10c_us|1"]["connection_ok"] is False
    cap = mlb_game(Cap(tmp_path / "live3"), books=False)
    cap.book(T - 1000)                                               # no connection markers at all: legacy
    d = run(tmp_path, cap.write(), out="o3")
    assert d["mlb_10c_us|1"]["status"] == "filled" and d["mlb_10c_us|1"]["connection_ok"] == "unknown"
    assert "connection_markers_missing" in d["mlb_10c_us|1"]["shakedown_reasons"]


def test_us_admission_is_pregame_exact_and_unmapped_games_use_no_attempt(tmp_path):
    cap = Cap(tmp_path / "live")
    mlb_game(cap)                                                    # pk 1: exact before the start
    cap.us_map(T - 60_000, match="ambiguous")                        # a later flip is ignored
    mlb_game(cap, pk=2, us=False)                                    # pk 2: no US event at all
    mlb_game(cap, pk=3, us=False)
    cap.us_map(T - 1_800_000, slug="late-event", intl=dict(game_pk=3))   # first mapped after the start
    cap.mlb_map(T - 3_600_000, pk=4, home="h4", away="a4", cid="0x4")
    cap.us_map(T - 3_650_000, slug="ev4a", intl=dict(game_pk=4))    # two US events claim game 4
    cap.us_map(T - 3_650_000, slug="ev4b", intl=dict(game_pk=4))
    cap.ls(T - 10, 5, "Top", 2, 3, 1, pk=4).ls(T, 5, "Top", 3, 3, 1, pk=4)
    d = run(tmp_path, cap.write())
    assert d["mlb_10c_us|1"]["status"] == "filled" and {k.split("|")[1] for k in d} == {"1"}
    ev = {s["game_id"]: s for s in signals(tmp_path / "out")}
    assert set(ev["2"]["evals"].values()) == {"us_unmapped"} and ev["2"]["us_mapping"] == "us_unmapped"
    assert set(ev["3"]["evals"].values()) == {"us_discovered_after_start"}
    assert set(ev["4"]["evals"].values()) == {"us_ambiguous"}
    cap = mlb_game(Cap(tmp_path / "live2"), us=False)
    cap.us_map(T - 3_650_000, match="unmatched")
    assert not run(tmp_path, cap.write(), out="o2")
    assert set(signals(tmp_path / "o2")[0]["evals"].values()) == {"us_unmatched"}


def _early_start_game(cap, us_at):
    """The probe's game: both venues list the start 1 h after the play below (an early start or a
    doubleheader game 2). The first linescore (T - 70 s) is not a signal; the first half-inning
    signal is at T - 60 s; the next one at T + 590 s has a fresh US reference."""
    late_start = T // 1000 + 3600
    cap.mlb_map(T - 3_600_000, start=late_start)
    cap.live_books(T - 600_000, T + 700_000)
    cap.trade(T - 30_000, .75)
    cap.ls(T - 70_000, 4, "Top", 2, 3, 1).ls(T - 60_000, 4, "Top", 3, 3, 1)
    cap.us_map(us_at, start=late_start)
    cap.trade(T + 500_000, .75)
    cap.ls(T + 590_000, 5, "Top", 2, 3, 1).ls(T + 600_000, 5, "Top", 3, 3, 1)
    return cap.write()


def test_us_mapping_received_after_the_game_is_in_play_never_admits_it(tmp_path):
    """Review finding: a us_map received after the game's first signal but before its listed start
    admitted the game mid-game. Now v1's own mlb_map guard (mlb.py) applies to us_map as well."""
    root = _early_start_game(Cap(tmp_path / "live"), us_at=T - 30_000)
    assert run(tmp_path, root) == {}
    sig = {s["signal_id"]: s for s in signals(tmp_path / "out")}
    assert set(sig["4bottom"]["evals"].values()) == {"us_unmapped"}
    assert set(sig["5top"]["evals"].values()) == {"us_discovered_after_start"} and sig["5top"]["us_mapping"] == \
        "us_discovered_after_start"
    rows = E.read_jsonl(tmp_path / "out" / "coverage.jsonl")
    marks = in_play_marks(rows)
    assert marks[("mlb", "1")] == marks[("cid", CID)] == marks[("slug", "mlb-g1")] == T - 60_000
    cov = UR.coverage(root, None, signals(tmp_path / "out"), {}, {r["key"] for r in rows}, [], marks)
    assert dict(zip(cov["intl"].game_id, cov["intl"].us)) == {"1": "us_discovered_after_start"}   # the report agrees
    assert dict(zip(cov["us"].us_event, cov["us"].match)) == {"mlb-aw-ho-2026-09-01": "discovered_after_start"}
    # received after the first linescore but before the first signal: admitted, exactly as v1 admits mlb_map
    root = _early_start_game(Cap(tmp_path / "live2"), us_at=T - 65_000)
    d = run(tmp_path, root, out="o2")
    assert d["mlb_10c_us|1"]["status"] == "filled" and d["mlb_10c_us|1"]["us_map_recv_ms"] == T - 65_000
    assert d["mlb_10c_us|1"]["signal_recv_ms"] == T + 590_000


def test_soccer_us_mapping_after_espn_in_play_is_late(tmp_path):
    """Soccer: ESPN's own state marks the kickoff; a `pre` response does not."""
    late = T // 1000 + 3600                                           # both venues list kickoff after the play
    for name, us_at, admitted in (("after_in", T - 5_000_000, False), ("after_pre", T - 6_500_000, True)):
        cap = soccer_game(Cap(tmp_path / name), start=late, us_at=us_at)
        espn(cap, T - 6_600_000, [], state="pre", period=0)
        espn(cap, T - 6_000_000, [], period=1)                        # kicked off
        espn(cap, T, [ev("e", 92, "90'+2'")])
        d = run(tmp_path, cap.write(), out=f"o_{name}")
        sig = signals(tmp_path / f"o_{name}")[0]
        if admitted:
            assert d[f"soccer_added_time_us|{SOC}"]["us_map_recv_ms"] == us_at and sig["us_mapping"] == "exact"
        else:
            assert not d and sig["us_mapping"] == "us_discovered_after_start"
            assert set(sig["evals"].values()) == {"us_discovered_after_start", "outside_window"}   # controls: minute 92
            assert in_play_marks(E.read_jsonl(tmp_path / f"o_{name}" / "coverage.jsonl")) == {("slug", SOC): T - 6_000_000}


def test_venue_indexes_are_bounded(tmp_path):
    """Review finding: emptied by_key sets were never removed (unbounded in a year-long follow)."""
    v = Venue()
    for i in range(200):
        v.on_record("us_map", dict(key=f"ev{i}", sport="mlb", us_event_slug=f"ev{i}", start_ts=START_S, match="exact",
                                   intl_key=dict(game_pk=i, condition_id=f"0x{i}", event_slug=f"g{i}"),
                                   markets=dict(slug=f"aec-{i}", long_team="away", tick=.005)), 1000 + i)
    assert len(v.by_key) == 600
    v.on_record("us_map", dict(key="ev0", sport="mlb", us_event_slug="ev0", start_ts=START_S, match="exact",
                               intl_key=dict(game_pk=999), markets=dict(slug="aec-0", long_team="away")), 2000)
    assert ("mlb", "0") not in v.by_key and ("cid", "0x0") not in v.by_key and v.by_key[("mlb", "999")] == {"ev0"}
    (soc,), = Cap(None).add("soccer_games", 0, event_slug=SOC, start_ts=START_S, legs={}, espn=dict(id="99")).rows.values()
    v.on_record("soccer_games", soc, 3000)
    v.on_record("espn", dict(espn_id="99", state="in"), 4000)
    v.mark_in_play("mlb", "7", 4000)
    assert v.in_play and v.espn_slug == {"99": SOC}
    v.prune(10 ** 13)
    assert v.pre == {} and v.by_key == {} and v.in_play == {} and v.espn_slug == {} and v.games == {} and v.rules == {}


# ----------------------------------------------------------------------------- soccer

SOC = "epl-a-b"
ATC = {"home": "atc-epl-a-b-a", "draw": "atc-epl-a-b-draw", "away": "atc-epl-a-b-b"}


def soccer_game(cap, ref=.80, offers=((.85, 200),), us_markets=None, start=START_S, us_at=T - 7_000_000):
    legs = {s: dict(condition_id=f"0x{s}", yes_token=f"{s}_yes", no_token=f"{s}_no", fee_rate=.05, fee_exponent=1,
                    tick=.01, min_size=5, seconds_delay=0) for s in ("home", "draw", "away")}
    cap.add("soccer_games", T - 7_200_000, event_slug=SOC, series="epl", start_ts=start, legs=legs,
            espn=dict(path="soccer/eng.1", id="99", home_id="1", away_id="2", home_name="A", away_name="B", kickoff_ts=start),
            match="exact", reason="")
    cap.us_map(us_at, slug="epl-a-b-us", sport="soccer", intl=dict(event_slug=SOC), start=start,
               markets=us_markets or {s: dict(slug=v, tick=.01, min_qty=.01) for s, v in ATC.items()})
    cap.live_books(T - 600_000, T + 70_000, slug=ATC["home"], bids=((.70, 50),), offers=offers)
    cap.trade(T - 20_000, ref, slug=ATC["home"])
    return cap


def espn(cap, ms, events, home=1, away=0, state="in", period=2):
    return cap.add("espn", ms, espn_id="99", path="soccer/eng.1", state=state, period=period, clock_s=0, display_clock="",
                   home=home, away=away, new_events=events)


def ev(fp, minute, display=None, period=2, kind="foul"):
    return dict(type=kind, period=period, clock_value=minute * 60, clock_display=display or f"{minute}'", team_id="1",
                scoring=False, text="", fp=fp)


def test_soccer_trigger_delays_band_on_us_reference(tmp_path):
    cap = soccer_game(Cap(tmp_path / "live"))
    espn(cap, T - 15_000, [ev("c", 80)])                         # both controls
    espn(cap, T, [ev("e", 92, "90'+2'")])                        # primary and delays
    d = run(tmp_path, cap.write())
    p, c = d[f"soccer_added_time_us|{SOC}"], d[f"soccer_ctrl_70_80_us|{SOC}"]
    assert p["market_slug"] == ATC["home"] and p["us_side"] == "long" and p["reference_price"] == .80 and p["limit"] == .81
    assert p["eligible_ms"] == T + 3000 and p["status"] == "unfilled" and p["reason"] == "no_depth_at_limit"
    assert c["signal"]["fp"] == "c" and c["eligible_ms"] == T - 12_000
    assert {k.split("|")[0]: v["eligible_ms"] - T for k, v in d.items() if v["signal_recv_ms"] == T} == \
        {"soccer_added_time_us": 3000, "soccer_us_delay2": 5000, "soccer_us_delay10": 10_000, "soccer_us_delay60": 60_000}
    cap = soccer_game(Cap(tmp_path / "live2"), ref=.98)          # US reference outside the band: no attempt yet
    espn(cap, T, [ev("x", 95, "90'+5'")])
    cap.trade(T + 5000, .84, slug=ATC["home"])
    espn(cap, T + 10_000, [ev("y", 96, "90'+6'")])
    d = run(tmp_path, cap.write(), out="o2")
    ev_by = {s["signal_id"]: s["evals"] for s in signals(tmp_path / "o2")}
    assert ev_by["x"]["soccer_added_time_us"] == "leader_price_outside_band" and ev_by["y"]["soccer_added_time_us"] == "qualified"
    p = d[f"soccer_added_time_us|{SOC}"]
    assert p["signal"]["fp"] == "y" and p["reference_price"] == .84 and p["status"] == "filled"
    assert p["fill"]["levels"] == [[.85, pytest.approx(p["quantity"])]] and p["quantity"] == order_quantity(.85, 100, FEE, 1)


# ----------------------------------------------------------------------------- preview audit

class FakePreview:
    def __init__(self):
        self.calls = []

    def __call__(self, slug, price, qty, intent):
        self.calls.append((slug, price, qty, intent))
        return 200, {"order": {"marketSlug": slug, "state": "ORDER_STATE_PENDING_NEW", "price": {"value": str(price)},
                               "quantity": qty, "cumQuantity": 0, "intent": intent,
                               "commissionNotionalTotalCollected": {"value": "0.0000"}, "marketMetadata": {"x": 1}}}


def test_preview_audit_is_isolated_rate_limited_and_restart_safe(tmp_path):
    root = mlb_game(Cap(tmp_path / "live")).write()
    plain = run(tmp_path, root, out="plain")
    fake, sleeps = FakePreview(), []
    clock = lambda: (T + 4000) / 1000                             # noqa: E731  entries are 2-4 s old
    aud = PreviewAuditor(tmp_path / "pv" / "previews.jsonl", fake, clock=clock, sleep=sleeps.append)
    with_pv = run(tmp_path, root, out="pv", preview=aud)
    aud.close()
    assert with_pv == plain
    assert (tmp_path / "pv" / "decisions.jsonl").read_text() == (tmp_path / "plain" / "decisions.jsonl").read_text()
    pv = {r["key"]: r for r in E.read_jsonl(tmp_path / "pv" / "previews.jsonl")}
    assert set(pv) == {"mlb_10c_us|1", "mlb_03c_us|1", "mlb_10c_us_delay2|1"}
    r = pv["mlb_10c_us|1"]
    assert r["status"] == "accepted" and r["lag_ms"] == 4000 and r["response"]["state"] == "ORDER_STATE_PENDING_NEW"
    assert "marketMetadata" not in json.dumps(r) and r["simulated"]["shares"] == plain["mlb_10c_us|1"]["shares"]
    assert sorted(fake.calls)[0] == (ML, .76, 129, "ORDER_INTENT_BUY_LONG") and len(fake.calls) == 3
    assert len(sleeps) == 2 and all(0 < s <= .5 for s in sleeps)       # at most 2 requests per second
    # restart: replayed entries are never previewed again
    aud2 = PreviewAuditor(tmp_path / "pv" / "previews.jsonl", fake, clock=clock)
    run(tmp_path, root, out="pv", preview=aud2)
    aud2.close()
    assert len(fake.calls) == 3 and len(E.read_jsonl(tmp_path / "pv" / "previews.jsonl")) == 3
    # a fresh output replayed long after the fact: every entry is "not previewed"
    aud3 = PreviewAuditor(tmp_path / "old" / "previews.jsonl", fake, clock=lambda: T / 1000 + 3600)
    assert run(tmp_path, root, out="old", preview=aud3) == plain
    aud3.close()
    old = E.read_jsonl(tmp_path / "old" / "previews.jsonl")
    assert {r["status"] for r in old} == {"not_previewed"} and {r["why"] for r in old} == {"lag"} and len(fake.calls) == 3


def test_preview_failures_never_reach_decisions(tmp_path):
    root = mlb_game(Cap(tmp_path / "live"), lead="home", ref_long=.25, bids=((.26, 30), (.25, 200))).write()
    plain = run(tmp_path, root, out="plain")

    def boom(*a):
        raise RuntimeError("network down")

    aud = PreviewAuditor(tmp_path / "pv" / "previews.jsonl", boom, clock=lambda: (T + 1000) / 1000, sleep=lambda s: None)
    assert run(tmp_path, root, out="pv", preview=aud) == plain
    aud.close()
    rows = E.read_jsonl(tmp_path / "pv" / "previews.jsonl")
    assert {r["status"] for r in rows} == {"error"} and rows[0]["price_value"] == pytest.approx(.24)
    assert status_of(400, summarize(400, {"code": 3, "message": "bad"})) == "rejected"
    assert summarize(400, {"code": 3, "message": "bad"})["message"] == "bad"


def test_preview_status_separates_order_rejections_from_transport_errors():
    """Review finding: every 4xx (401 signature, 403 edge, 429 rate limit) was logged as an order rejection."""
    assert status_of(200, summarize(200, {"order": {"state": "ORDER_STATE_PENDING_NEW"}})) == "accepted"
    assert status_of(200, summarize(200, {"order": {"state": "ORDER_STATE_REJECTED", "rejectReason": "x"}})) == "rejected"
    for code in (400, 404, 409, 422):
        assert status_of(code, summarize(code, {"code": 3, "message": "invalid price"})) == "rejected"
    for code in (401, 403, 407, 408, 429):
        assert status_of(code, summarize(code, {"code": 16, "message": "unauthenticated"})) == "error"
    for code in (500, 502, 503):
        assert status_of(code, summarize(code, {"message": "unavailable"})) == "error"
    assert status_of(400, summarize(400, "<html>Attention Required! | Cloudflare</html>")) == "error"
    assert status_of(200, summarize(200, "not json")) == "error" and status_of(None, summarize(None, "Timeout()")) == "error"
    frame = pd.DataFrame([dict(policy="mlb_10c_us", shakedown=False, status=s, lag_ms=100, http_status=c, reject=r,
                               cum_quantity=0., commission=0., sim_shares=1., sim_fee=.01, truncated=False)
                          for s, c, r in (("accepted", 200, None), ("rejected", 400, "invalid price"),
                                          ("error", 429, None), ("error", 401, None))])
    text = "\n".join(UR._preview_text(dict(frame=frame)))
    assert "the exchange answered 2 (acceptance rate 50.0% of answered previews) and 2 failed" in text
    assert "| error    |           429 |" in text and "invalid price" in text


# ----------------------------------------------------------------------------- determinism

def fixture_many(root):
    cap = Cap(root)
    cap.live_books(T - 600_000, T + 9_000_000, every=2000)
    for pk, t in ((1, T), (2, T + 3_600_000), (3, T + 7_200_000)):
        slug = f"aec-g{pk}"
        cap.mlb_map(t - 3_600_000, pk=pk, home=f"h{pk}", away=f"a{pk}", cid=f"0x{pk}", start=t // 1000 - 3600)
        cap.us_map(t - 3_650_000, slug=f"ev{pk}", intl=dict(game_pk=pk), start=t // 1000 - 3600,
                   markets={"away": dict(slug=slug, long=True, tick=.005), "home": dict(slug=slug, long=False, tick=.005)})
        for ms in range(t - 60_000, t + 10_000, 1000):
            cap.book(ms + 500, slug=slug)
        cap.trade(t - 30_000, .75, slug=slug)
        cap.ls(t - 60_000, 5, "Top", 2, 3, 1, pk=pk).ls(t, 5, "Top", 3, 3, 1, pk=pk)
    return cap


def test_restart_is_deterministic_without_duplicates(tmp_path):
    root = fixture_many(tmp_path / "live").write()
    full = run(tmp_path, root, out="a")
    lines = (tmp_path / "a" / "decisions.jsonl").read_text()
    assert len(full) == 9 and all(v["status"] in ("filled", "partial") for v in full.values())
    assert run(tmp_path, root, out="a") == full and (tmp_path / "a" / "decisions.jsonl").read_text() == lines
    cut = pd.Timestamp(T + 3_600_001, unit="ms", tz="UTC").isoformat()      # game 2's delay2 entry is pending
    part = run(tmp_path, root, out="b", until=cut)
    assert "mlb_10c_us|2" in part and "mlb_10c_us_delay2|2" not in part and "mlb_10c_us|3" not in part
    resumed = run(tmp_path, root, out="b")
    assert resumed == full and len(E.read_jsonl(tmp_path / "b" / "decisions.jsonl")) == 9
    root2 = fixture_many(tmp_path / "live_gz").write(gz={(k[0], "us_book") for k in fixture_many(tmp_path / "x").rows})
    assert {k: {f: v[f] for f in ("status", "shares", "fill")} for k, v in run(tmp_path, root2, out="c").items()} == \
        {k: {f: v[f] for f in ("status", "shares", "fill")} for k, v in full.items()}


# ----------------------------------------------------------------------------- settlement, report, activation

def test_settlement_orientation_fallback_and_report(tmp_path):
    cap = Cap(tmp_path / "live")
    mlb_game(cap)                                                          # game 1: away (long) leader
    t2 = T + 3_600_000
    cap.mlb_map(t2 - 3_600_000, pk=2, home="h2", away="a2", cid="0x2", start=t2 // 1000 - 3600)
    cap.us_map(t2 - 3_650_000, slug="ev2", intl=dict(game_pk=2), start=t2 // 1000 - 3600,
               markets={"away": dict(slug="aec-g2", long=True, tick=.005), "home": dict(slug="aec-g2", long=False, tick=.005)})
    cap.live_books(t2 - 60_000, t2 + 10_000, slug="aec-g2", bids=((.25, 500),))
    cap.trade(t2 - 30_000, .25, slug="aec-g2")
    cap.ls(t2 - 60_000, 5, "Top", 2, 1, 3, pk=2).ls(t2, 5, "Top", 3, 1, 3, pk=2)    # home (short) leader
    root = cap.write()
    md = model_dir(tmp_path)
    act = tmp_path / "ACTIVATION_US.json"
    sha = E.hashlib.sha256((md / "mlb_fair_2025.json").read_bytes()).hexdigest()
    act.write_text(json.dumps(dict(activated_ms=(START_S - 60) * 1000, activated_utc="x", code_sha256=UE.code_hashes()["code_sha256"],
                                   spec_sha256=spec.spec_hash(), model_snapshots={"2025": sha})))
    d = run(tmp_path, root, activation=act, model_dir=md)
    assert d["mlb_10c_us|1"]["shakedown"] is False and d["mlb_10c_us|2"]["us_side"] == "short"
    out = tmp_path / "out"
    intl = tmp_path / "intl_settlements.json"
    intl.write_text(json.dumps({"0x2": dict(status="resolved", payouts={"h2": 1.0, "a2": 0.0})}))
    fetched = []

    def fetch(slug):
        fetched.append(slug)
        return {ML: {"slug": ML, "settlement": 0}}.get(slug)               # game 1: away lost; game 2 unpublished

    r = US.settle(out, root, fetch=fetch, intl=intl, universe=False, pause=0)
    cache = json.loads((out / "settlements.json").read_text())
    assert r["newly_resolved"] == 1 and r["fallback"] == 1 and sorted(fetched) == sorted([ML, "aec-g2"])
    assert {k: cache[ML][k] for k in ("status", "settlement", "source", "binary", "n_polls")} == \
        dict(status="resolved", settlement=0.0, source="us_settlement", binary=True, n_polls=1)
    assert cache["aec-g2"]["status"] == "fallback_intl" and cache["aec-g2"]["settlement"] == 0.0   # long (away) lost
    assert cache["aec-g2"]["n_polls"] == 1                            # the fallback keeps the polling state
    res = UR.build(out, root, tmp_path / "PAPER_TEST_US.md", tmp_path / "ledgers", act)
    f = res["frame"]
    g1 = f[(f.policy == "mlb_10c_us") & (f.game_id == "1")].iloc[0]
    g2 = f[(f.policy == "mlb_10c_us") & (f.game_id == "2")].iloc[0]
    assert g1.y == 0.0 and g1.pnl_usd == pytest.approx(-g1.cost_usd) and g1.settle_source == "us_settlement"
    # review finding: the international fallback is provisional, never a settlement of the US test
    assert not g2.settled and g2.open and g2.provisional and math.isnan(g2.pnl_usd) and pd.isna(g2.settle_source)
    assert g2.y_provisional == 1.0 and g2.pnl_provisional == pytest.approx(g2.shares - g2.cost_usd)
    st = f[f.settled]
    assert np.allclose(st.cost_usd, st.stake_usd + st.fee_usd) and np.allclose(st.pnl_usd, st.payout - st.cost_usd)
    one = f[(f.policy == "mlb_10c_us_plus1c") & (f.game_id == "1")].iloc[0]
    lv = d["mlb_10c_us|1"]["fill"]["levels"]
    assert one.stake_usd == pytest.approx(sum(q * (p + .01) for p, q in lv))
    assert one.fee_usd == pytest.approx(sum(FEE * q * (p + .01) * (.99 - p) for p, q in lv))
    p = res["primary"]["mlb_10c_us"]
    assert p["settled"] == 1 and p["open"] == 1 and p["net_cash_usd"] == pytest.approx(g1.pnl_usd)
    ep = UR.endpoint("mlb", f, res["coverage"], json.loads(act.read_text()), time.time())
    assert dict(UR.gate("mlb", res["primary"], res["comparisons"], ep))["primary net cash > 0"] is False   # g1 only
    doc = json.loads((tmp_path / "ledgers" / "paper_us_mlb_10c_us.json").read_text())
    assert doc["group"] == "Forward paper tests (US venue)" and doc["slug"] == "paper_us_mlb_10c_us" and doc["n_total_trades"] == 1
    rows = pd.DataFrame(doc["rows"], columns=doc["columns"])
    assert set(rows.period) == {"prospective_paper"} and np.allclose(rows.pnl_usd, rows.payout - rows.stake_usd - rows.fee_usd)
    text = (tmp_path / "PAPER_TEST_US.md").read_text()
    for s in ("## Endpoints and gate", "## Coverage", "## Primary (prospective) results", "## Shakedown (not evidence)",
              "## Preview audit", "mlb_10c_us", "## Provisional settlements (not counted)", "provisional_net_cash"):
        assert s in text
    cov = res["coverage"]
    assert set(cov["intl"].us) == {"exact"} and len(cov["us"]) == 2
    assert res["previews"]["frame"].status.eq("not_previewed").all()
    # the US endpoint replaces a fallback once it publishes; only then does the position count
    US.settle(out, root, fetch=lambda s: {"slug": s, "settlement": 0}, intl=intl, universe=False, pause=0)
    assert json.loads((out / "settlements.json").read_text())["aec-g2"]["source"] == "us_settlement"
    res = UR.build(out, root, tmp_path / "PAPER_TEST_US.md", tmp_path / "ledgers", act)
    g2 = res["frame"][(res["frame"].policy == "mlb_10c_us") & (res["frame"].game_id == "2")].iloc[0]
    assert g2.settled and not g2.provisional and g2.pnl_usd == pytest.approx(g2.shares - g2.cost_usd)
    assert res["primary"]["mlb_10c_us"]["settled"] == 2 and "Open positions whose US settlement" not in \
        (tmp_path / "PAPER_TEST_US.md").read_text()


class _Http429(Exception):
    response = type("R", (), {"status_code": 429})()


def test_settlement_polling_rotates_after_429_and_backs_off(tmp_path):
    """Review finding: passes restarted from the alphabetically first slug, stopped at a 429 and kept
    re-polling unpublished markets every pass forever."""
    out = tmp_path / "out"
    now = 1_800_000_000
    log = E.JsonlLog(out / "decisions.jsonl")
    for slug in ("aec-a", "aec-b", "aec-c"):
        log.write(dict(key=f"mlb_10c_us|{slug}", market_slug=slug, shares=10, us_side="long", scheduled_start_ts=now - 36_000))
    log.close()
    calls = []

    def fetch(limit):
        def f(slug):
            calls.append(slug)
            if len(calls) > limit:
                raise _Http429()
            return None                                                # 404: unpublished
        return f
    r = US.settle(out, tmp_path, fetch=fetch(1), intl=None, universe=False, now=now, pause=0)
    cache = json.loads((out / "settlements.json").read_text())          # written although the pass was cut short
    assert calls == ["aec-a", "aec-b"] and r["rate_limited"] and r["polled"] == 1
    assert cache == {"aec-a": dict(status="pending", first_polled_ts=now, last_polled_ts=now, n_polls=1)}
    calls.clear()
    US.settle(out, tmp_path, fetch=fetch(3), intl=None, universe=False, now=now + 900, pause=0)
    assert calls == ["aec-b", "aec-c", "aec-a"]                          # least recently polled first
    cache = json.loads((out / "settlements.json").read_text())
    assert {s: (e["status"], e["n_polls"], e["first_polled_ts"]) for s, e in cache.items()} == {
        "aec-a": ("pending", 2, now), "aec-b": ("pending", 1, now + 900), "aec-c": ("pending", 1, now + 900)}
    # backoff by time since the first poll: every pass for 12 h, hourly to 3 days, daily to 30 days, then
    # `unpublished` (a filled position's market weekly, a universe-only market never)
    h, day = 3600, 86400
    assert US.due({}, now, False) and US.due(dict(first_polled_ts=0, last_polled_ts=11 * h), 11 * h + 1, False)
    assert not US.due(dict(first_polled_ts=0, last_polled_ts=13 * h - 60), 13 * h, False)
    assert US.due(dict(first_polled_ts=0, last_polled_ts=12 * h), 13 * h, False)
    assert not US.due(dict(first_polled_ts=0, last_polled_ts=4 * day - h), 4 * day, False)
    assert US.due(dict(first_polled_ts=0, last_polled_ts=3 * day), 4 * day, False)
    assert not US.due(dict(first_polled_ts=0, last_polled_ts=0), 60 * day, False)
    assert US.due(dict(first_polled_ts=0, last_polled_ts=52 * day), 60 * day, True)
    assert not US.due(dict(first_polled_ts=0, last_polled_ts=59 * day), 60 * day, True)
    calls.clear()
    US.settle(out, tmp_path, fetch=fetch(99), intl=None, universe=False, now=now + 13 * h, pause=0)
    assert calls == ["aec-a", "aec-b", "aec-c"]                          # hourly now: all due (same last poll)
    calls.clear()
    US.settle(out, tmp_path, fetch=fetch(99), intl=None, universe=False, now=now + 13 * h + 900, pause=0)
    assert calls == []                                                   # not due again within the hour
    US.settle(out, tmp_path, fetch=fetch(99), intl=None, universe=False, now=now + 31 * day, pause=0)
    cache = json.loads((out / "settlements.json").read_text())
    assert {e["status"] for e in cache.values()} == {"unpublished"} and calls == ["aec-a", "aec-b", "aec-c"]


def test_follow_mode_sigterm_checkpoint_periodic_thread_and_preview_drain(tmp_path, monkeypatch):
    """Review finding: the service paths had no test. `paper-us run --follow` through `main`: the
    periodic settle/report runs on its own thread, SIGTERM exits through the real handler, the
    checkpoint is written, and previews still queued at SIGTERM are drained before main returns."""
    import argparse
    import os
    import signal
    import threading
    from pmsports.paper.us import preview as UP
    root = mlb_game(Cap(tmp_path / "live")).write()
    batch = run(tmp_path, root, out="batch")
    out, previews = tmp_path / "out", tmp_path / "out" / "previews.jsonl"
    periodic_threads, periodic_ran, release, at_term = [], threading.Event(), threading.Event(), []

    def fake_settle(o, lv, **kw):
        periodic_threads.append(threading.current_thread())

    def fake_build(o, lv, report, *a, **kw):
        periodic_threads.append(threading.current_thread())
        periodic_ran.set()

    def blocked_preview(slug, price, qty, intent):                      # held until SIGTERM is on its way
        assert release.wait(10)
        time.sleep(.2)                                                   # still busy when the run ends
        return 200, {"order": {"state": "ORDER_STATE_PENDING_NEW", "cumQuantity": 0}}

    class Auditor(UP.PreviewAuditor):
        def __init__(self, path):
            super().__init__(path, blocked_preview, clock=lambda: (T + 4000) / 1000, sleep=lambda s: None)

    class FollowReplayer:
        """The capture as follow mode sees it: records, then idle heartbeats until SIGTERM."""

        def __init__(self, live, start, end, follow=False, **kw):
            assert follow
            self.inner, self.scan_ms = UE.USReplayer(live, start, end), None

        def __iter__(self):
            last = None
            for ms, stream, rec in self.inner:
                last = ms
                yield ms, stream, rec
            for _ in range(1000):
                yield last, None, None
                if periodic_ran.wait(0.01):
                    break
            at_term.append(len(E.read_jsonl(previews)))
            release.set()
            os.kill(os.getpid(), signal.SIGTERM)
            yield last, None, None
            raise AssertionError("SIGTERM did not stop the run")

        def stats(self):
            return {}

    orig_run = URUN.run
    monkeypatch.setattr(URUN, "run", lambda *a, **k: orig_run(*a, **k, activation=None, snapshot_log=None,
                                                              replayer=FollowReplayer))
    monkeypatch.setattr(URUN, "PERIODIC_S", 0)
    monkeypatch.setattr(URUN, "CHECKPOINT_S", 0)
    monkeypatch.setattr(US, "settle", fake_settle)
    monkeypatch.setattr(UR, "build", fake_build)
    monkeypatch.setattr(UP, "PreviewAuditor", Auditor)
    args = argparse.Namespace(action="run", follow=True, no_preview=False, since=None, until=None, out=str(out),
                              live=str(root), model_dir=str(model_dir(tmp_path)))
    old = signal.getsignal(signal.SIGTERM)

    def no_handler(*_):                                                 # main must replace this with its own
        raise RuntimeError("paper-us run --follow installed no SIGTERM handler")
    signal.signal(signal.SIGTERM, no_handler)
    try:
        with pytest.raises(SystemExit) as exc:
            URUN.main(args)
    finally:
        signal.signal(signal.SIGTERM, old)
    assert exc.value.code == 0
    assert periodic_threads and all(t is not threading.main_thread() and t.name == "us-periodic" for t in periodic_threads)
    last_ms = max(r["recv_ms"] for f in root.glob("*/*.jsonl") for r in E.read_jsonl(f))
    assert json.loads((out / "checkpoint.json").read_text())["last_recv_ms"] == last_ms
    pv = E.read_jsonl(previews)
    assert at_term == [0] and len(pv) == 3 and {r["status"] for r in pv} == {"accepted"}   # queued at SIGTERM, drained
    follow = decisions(out)
    assert {k: (v["status"], v["shares"], v["fill"]) for k, v in follow.items()} == \
        {k: (v["status"], v["shares"], v["fill"]) for k, v in batch.items()}


def test_activation_manifest_scope_and_refusals(tmp_path, monkeypatch):
    md = model_dir(tmp_path)
    git = {"status": "", "rev-parse": "abc123"}
    monkeypatch.setattr(URUN, "_git", lambda *a: git[a[0]])
    path = tmp_path / "paper" / "ACTIVATION_US.json"
    m = URUN.activate(path, md)
    h = UE.code_hashes()
    assert m["version"] == "us-v1" and m["code_sha256"] == h["code_sha256"] and m["spec_sha256"] == spec.spec_hash()
    for f in ("pmsports/paper/us/engine.py", "pmsports/paper/us/venue.py", "pmsports/paper/us/spec.py", "pmsports/paper/us_api.py",
              "pmsports/paper/engine.py", "pmsports/paper/mlb.py", "pmsports/paper/soccer.py", "pmsports/book_replay.py",
              "pmsports/research/h_soccer_continuation.py"):
        assert f in m["code_files"]
    assert "pmsports/paper/us/report.py" not in m["code_files"] and "pmsports/paper/us/report.py" in m["support_files"]
    assert "pmsports/paper/us/preview.py" in m["support_files"] and set(m["rules"]) == set(spec.POLICIES)
    assert m["model_snapshots"] == {"2025": E.hashlib.sha256((md / "mlb_fair_2025.json").read_bytes()).hexdigest()}
    assert "reports/PROSPECTIVE_US_VENUE_PROTOCOL.md" in m["protocols"]
    with pytest.raises(SystemExit):
        URUN.activate(path, md)
    git["status"] = " M pmsports/paper/us/engine.py"
    with pytest.raises(SystemExit):
        URUN.activate(tmp_path / "other.json", md)


def test_v1_engine_is_untouched_by_the_us_engine(tmp_path):
    """The US engine is composition only: v1 stream ranks and v1 rules are unchanged at runtime."""
    assert E.STREAMS == {"mlb_map": 0, "market_meta": 1, "soccer_games": 2, "games": 3, "clob": 4, "sports": 5, "mlb": 6, "espn": 7}
    root = mlb_game(Cap(tmp_path / "live")).write()
    run(tmp_path, root)
    assert v1spec.VERSION == "v1" and "mlb_10c_us" not in v1spec.RULES and E.STREAMS["mlb"] == 6
    rp = UE.USReplayer(root)
    list(rp)
    assert {s: c.rank for s, c in rp.chains.items()} == {s: UE.STREAMS[s] for s in rp.chains}


# ----------------------------------------------------------------------------- the capture's us_map contract

def _us_event_mlb(slug, start_iso, long_name, long_abbr, short_name, short_abbr, long_ordering="away"):
    return {"slug": slug, "startDate": start_iso, "gameId": 5, "markets": [{
        "slug": f"aec-{slug}", "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_MONEYLINE",
        "sportsMarketType": "baseball_team_full_game_winner", "orderPriceMinTickSize": 0.005, "minimumTradeQty": 0.01,
        "feeCoefficient": FEE, "marketSides": [
            {"long": True, "team": {"name": long_name, "abbreviation": long_abbr, "ordering": long_ordering}},
            {"long": False, "team": {"name": short_name, "abbreviation": short_abbr,
                                     "ordering": "home" if long_ordering == "away" else "away"}}]}]}


def test_capture_us_records_drive_the_engine(tmp_path):
    """Records built by the capture's own mapper (capture_us.map_mlb / map_soccer) are admitted,
    oriented and sized as the protocol says (qty increment = minimumTradeQty 0.01)."""
    C = pytest.importorskip("pmsports.paper.capture_us")
    iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(START_S))
    ig = {"iid": f"mlb:{CID}", "condition_id": CID, "slug": "mlb-g1", "game_pk": 1, "match": "exact", "reason": "",
          "home_name": "Los Angeles Angels", "away_name": "Seattle Mariners", "starts": [START_S, START_S],
          "start": START_S, "game_id": None}
    ev = _us_event_mlb("mlb-sea-laa-2026-09-01", iso, "Seattle Mariners", "sea", "Los Angeles Angels", "laa")
    rec = C.map_mlb(C.parse_event(ev, "mlb", "mlb"), [ig])
    assert rec["match"] == "exact" and rec["markets"]["long_team"] == "away"
    legs = normalize_markets(rec)
    assert legs["away"].long and not legs["home"].long and legs["home"].slug == "aec-mlb-sea-laa-2026-09-01"
    cap = Cap(tmp_path / "live")
    cap.mlb_map(T - 3_600_000)
    cap.add("us_map", T - 3_700_000, **rec)
    slug = rec["markets"]["slug"]
    cap.live_books(T - 600_000, T + 10_000, slug=slug, bids=((.25, 500),))
    cap.trade(T - 30_000, .25, slug=slug)
    cap.ls(T - 60_000, 5, "Top", 2, 1, 3).ls(T, 5, "Top", 3, 1, 3)        # home (the US short side) leads
    d = run(tmp_path, cap.write())
    x = d["mlb_10c_us|1"]
    per = .76 + FEE * .76 * .24
    assert x["us_side"] == "short" and x["qty_increment"] == .01 and x["min_qty"] == .01
    assert x["quantity"] == pytest.approx(math.floor(100 / per * 100 + 1e-9) / 100) and x["status"] == "filled"
    assert x["fill"]["levels"] == [[.75, pytest.approx(x["quantity"])]] and x["cost_usd"] <= 100
    # preview of a fractional quantity: whole contracts are sent (us_api) and the truncation is flagged
    fake = FakePreview()
    aud = PreviewAuditor(tmp_path / "pv" / "previews.jsonl", fake, clock=lambda: (T + 2000) / 1000, sleep=lambda s: None)
    run(tmp_path, cap.write(), out="pv", preview=aud)
    aud.close()
    pv = {r["key"]: r for r in E.read_jsonl(tmp_path / "pv" / "previews.jsonl")}["mlb_10c_us|1"]
    assert pv["quantity_truncated"] and pv["preview_quantity"] == int(x["quantity"])
    assert fake.calls[0][1:] == (pytest.approx(.24), int(x["quantity"]), "ORDER_INTENT_BUY_SHORT")
    # soccer: legs oriented by team identity against the international ESPN home/away
    def leg(tok, name):
        return {"slug": f"atc-epl-ars-che-2026-09-01-{tok}", "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_DRAWABLE_OUTCOME",
                "orderPriceMinTickSize": 0.01, "minimumTradeQty": 0.01, "feeCoefficient": FEE,
                "marketSides": [{"long": True, "team": {"name": name, "abbreviation": tok, "ordering": "home"}}]}
    sev = {"slug": "epl-ars-che-2026-09-01", "startDate": iso, "gameId": 7,
           "markets": [leg("ars", "Arsenal"), leg("che", "Chelsea"), leg("draw", "Draw")]}
    sig = {"iid": f"soccer:{SOC}", "slug": SOC, "series": "epl", "match": "exact", "reason": "",
           "teams": {"home": {"abbr": "che", "name": "Chelsea"}, "away": {"abbr": "ars", "name": "Arsenal"}},
           "espn": {"id": "99", "home_name": "Chelsea", "away_name": "Arsenal"}, "starts": [START_S, START_S],
           "start": START_S, "game_id": None}
    srec = C.map_soccer(C.parse_event(sev, "soccer", "epl"), [sig])
    assert srec["match"] == "exact"
    assert {s: (leg.slug.rsplit("-", 1)[1], leg.long) for s, leg in normalize_markets(srec).items()} == {
        "home": ("che", True), "draw": ("draw", True), "away": ("ars", True)}


def test_no_us_market_records_and_venue_rules_after_start(tmp_path):
    v = Venue()
    (row,), = Cap(None).mlb_map(0).rows.values()
    v.on_record("mlb_map", row, 0)
    base = dict(sport="mlb", intl_key=dict(game_pk=1, condition_id=CID, event_slug="mlb-g1"), start_ts=START_S)
    v.on_record("us_map", base | dict(key="intl:mlb:0xmlb", us_event_slug=None, markets={}, match="no_us_market"), 1000)
    assert v.admission("mlb", "1") == (None, "us_no_us_market")
    us = base | dict(key="ev", us_event_slug="ev", match="exact", markets=dict(slug=ML, long_team="away", tick=.01))
    v.on_record("us_map", us, 2000)                                   # a US event claims the game later
    assert v.admission("mlb", "1")[1] == "exact" and v.leg("mlb", "1", "home")[0].tick == .01
    v.on_record("us_map", us | dict(markets=dict(slug=ML, long_team="away", tick=.005)), START_S * 1000 + 5)
    leg, _, st = v.leg("mlb", "1", "home")                           # after the start: venue fields follow the exchange
    assert st == "exact" and leg.tick == .005 and not leg.long
    v.on_record("us_map", us | dict(markets=dict(slug=ML, long_team="home")), START_S * 1000 + 6)
    assert not v.leg("mlb", "1", "home")[0].long                      # ... orientation never changes after the start
    v.on_record("us_map", base | dict(key="intl:mlb:0xmlb", us_event_slug=None, markets={}, match="no_us_market"), 3000)
    assert v.admission("mlb", "1") == (None, "us_no_us_market")      # the latest pregame word is "no US market"


def test_primary_label_needs_both_venue_starts_after_activation(tmp_path):
    act = tmp_path / "A.json"
    base = dict(code_sha256=UE.code_hashes()["code_sha256"], spec_sha256=spec.spec_hash())
    act.write_text(json.dumps(base | dict(activated_ms=1000_000)))
    eng = UE.USEngine(tmp_path / "out", tmp_path / "live", act, None, model_dir(tmp_path))
    assert eng.shakedown_reasons(dict(sport="soccer", scheduled_start_ts=2000, us_start_ts=1500)) == []
    assert eng.shakedown_reasons(dict(sport="soccer", scheduled_start_ts=2000, us_start_ts=999)) == ["scheduled_before_activation"]
    assert eng.shakedown_reasons(dict(sport="soccer", connection_ok="unknown", scheduled_start_ts=2000)) == \
        ["connection_markers_missing"]
    act.write_text(json.dumps(base | dict(activated_ms=1000_000, code_sha256="x")))
    assert eng.shakedown_reasons(dict(sport="soccer", scheduled_start_ts=2000)) == ["code_hash_mismatch"]
    eng.close()


def test_report_coverage_statuses_match_admission(tmp_path):
    cap = Cap(tmp_path / "live")
    for pk in (1, 2, 3, 4):
        cap.mlb_map(T - 3_600_000, pk=pk, home=f"h{pk}", away=f"a{pk}", cid=f"0x{pk}")
    cap.us_map(T - 3_650_000, slug="ev1", intl=dict(game_pk=1))                                   # exact
    cap.add("us_map", T - 3_650_000, key="intl:mlb:0x2", sport="mlb", us_event_slug=None, intl_key=dict(game_pk=2),
            start_ts=START_S, markets={}, match="no_us_market", reason="")                         # no US market
    cap.us_map(T - 1_000_000, slug="ev3", intl=dict(game_pk=3))                                   # after the start
    root = cap.write()                                                                             # game 4: nothing
    cov = UR.coverage(root, None, [], {}, set(), [])
    assert dict(zip(cov["intl"].game_id, cov["intl"].us)) == {"1": "exact", "2": "us_no_us_market",
                                                              "3": "us_discovered_after_start", "4": "us_unmapped"}
    assert dict(zip(cov["us"].us_event, cov["us"].match)) == {"ev1": "exact", "ev3": "discovered_after_start"}
