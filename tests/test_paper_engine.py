import calendar
import functools
import gzip
import json
import time

import numpy as np
import pandas as pd
import pytest

from pmsports.paper import engine as E
from pmsports.paper import report as R
from pmsports.paper import run as RUN
from pmsports.paper import settle as S
from pmsports.paper import spec
from pmsports.paper.mlb import game_over, next_label

T = calendar.timegm((2026, 9, 1, 18, 0, 0)) * 1000         # 2026-09-01 18:00 UTC (past: batch watermark)
START_S = T // 1000 - 3600                                    # scheduled first pitch / kickoff
H, A, CID = "home_tok", "away_tok", "0xmlb"
DAY = "2026-09-01"


class Cap:
    """Synthetic capture: records per (day, stream), written as the recorder would."""

    def __init__(self, root):
        self.root, self.rows = root, {}

    def add(self, stream, ms, day=None, **rec):
        day = day or time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))    # the recorder's UTC receipt day
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

    # --- record helpers
    def clob(self, ms, *msgs, day=None):
        return self.add("clob", ms, day, msg=list(msgs) if len(msgs) > 1 else msgs[0])

    def conn(self, ms, kind, day=None):
        return self.add("clob", ms, day, conn=kind, n_assets=2)

    def mlb_map(self, ms, pk=1, match="exact", game_type="R", sched=9, delay=0, tick=.01, min_size=5, fee=.05,
                home=H, away=A, cid=CID, start=START_S):
        pm = dict(condition_id=cid, slug=f"mlb-g{pk}", home_token=home, away_token=away, start_ts=start, fee_rate=fee,
                  fee_exponent=1, tick=tick, min_size=min_size, seconds_delay=delay)
        return self.add("mlb_map", ms, game_pk=pk, game_type=game_type, scheduled_innings=sched, doubleheader="N",
                        game_number=1, mlb_start_ts=start, home_name="Home", away_name="Away", pm=pm, match=match, reason="")

    def ls(self, ms, inning, half, outs, away, home, pk=1):
        return self.add("mlb", ms, game_pk=pk, inning=inning, half=half, outs=outs, away=away, home=home, offense=[])


def book(tok, asks=((.70, 50),), bids=((.60, 50),), cid=CID):
    return dict(event_type="book", asset_id=tok, market=cid, bids=[dict(price=str(p), size=str(s)) for p, s in bids],
                asks=[dict(price=str(p), size=str(s)) for p, s in asks], timestamp="0")


def change(tok, price, size, side="SELL", cid=CID):
    return dict(event_type="price_change", market=cid, price_changes=[dict(asset_id=tok, price=str(price), size=str(size), side=side)])


def trade(tok, price, size=10, cid=CID):
    return dict(event_type="last_trade_price", asset_id=tok, market=cid, price=str(price), size=str(size), side="BUY", timestamp="0")


def model_dir(tmp_path):
    d = tmp_path / "model"
    d.mkdir(exist_ok=True)
    cells = {"-3": .05, "-2": .10, "-1": .30, "1": .70, "2": .90, "3": .95}
    snap = dict(max_season=2025, fallback=.5, levels=[dict(keys=["inn_k", "half", "outs", "bases", "diff_k"], cells={}),
                                                      dict(keys=["inn_k", "half", "diff_k"], cells={"10|bottom|-1": .20}),
                                                      dict(keys=["diff_k"], cells=cells)])
    (d / "mlb_fair_2025.json").write_text(json.dumps(snap, sort_keys=True))
    return d


def run(tmp_path, live, out="out", **kw):
    kw = dict(activation=None, snapshot_log=None, model_dir=model_dir(tmp_path), allow_network=False) | kw
    RUN.run(live=live, out=tmp_path / out, **kw)
    return decisions(tmp_path / out)


def decisions(out):
    return {d["key"]: d for d in E.read_jsonl(out / "decisions.jsonl")}


def signals(out):
    return E.read_jsonl(out / "signals.jsonl")


def mlb_game(cap, t=T, asks=((.70, 50), (.72, 100)), ref=.75, pk=1, map_at=None, **map_kw):
    """Exact-mapped game (first pitch t - 1 h, mapped then); away leads 3-1 when the top of the
    5th ends at t (label bottom 5)."""
    map_kw.setdefault("start", -(-t // 1000) - 3600)
    cap.mlb_map(t - 3_600_000 if map_at is None else map_at, pk=pk, **map_kw)
    a, h, cid = map_kw.get("away", A), map_kw.get("home", H), map_kw.get("cid", CID)
    cap.conn(t - 600_000, "open")
    cap.clob(t - 590_000, book(a, asks, cid=cid), book(h, ((.31, 50),), cid=cid))
    cap.clob(t - 30_000, trade(a, ref, cid=cid))
    for k in range(1, 600):                          # heartbeat replies every 2 s, well past t
        cap.conn(t - 600_000 + 2000 * k, "pong")
    cap.ls(t - 60_000, 5, "Top", 2, 3, 1, pk=pk)
    cap.ls(t, 5, "Top", 3, 3, 1, pk=pk)
    return cap


# ----------------------------------------------------------------------------- replay

def test_merge_order_across_streams_days_gz_and_bounds(tmp_path):
    cap = Cap(tmp_path / "live")
    D2 = "2026-09-02"
    cap.add("clob", T + 1, msg={}).add("clob", T + 5, msg={}).add("clob", T + 9, msg={})
    cap.add("mlb", T + 2).add("mlb", T + 5).add("market_meta", T + 5)
    cap.add("clob", T + 10, day=D2, msg={}).add("clob", T + 12, day=D2, msg={}).add("mlb", T + 11, day=D2)
    root = cap.write(gz={(D2, "clob")})
    got = [(ms - T, s) for ms, s, _ in E.Replayer(root)]
    assert got == [(1, "clob"), (2, "mlb"), (5, "market_meta"), (5, "clob"), (5, "mlb"), (9, "clob"),
                   (10, "clob"), (11, "mlb"), (12, "clob")]
    assert [(ms - T, s) for ms, s, _ in E.Replayer(root, start_ms=T + 5, end_ms=T + 11)] == \
        [(5, "market_meta"), (5, "clob"), (5, "mlb"), (9, "clob"), (10, "clob")]
    assert [ms - T for ms, _, _ in E.Replayer([root / D2 / "clob.jsonl.gz", root / DAY / "clob.jsonl"])] == [1, 5, 9, 10, 12]
    # a rotated copy never duplicates the plain file; an out-of-order line is clamped, not reordered
    d = root / DAY
    with gzip.open(d / "mlb.jsonl.gz", "wt") as f:
        f.write((d / "mlb.jsonl").read_text())
    with open(d / "mlb.jsonl", "a") as f:
        f.write(json.dumps({"recv_ms": T + 3}) + "\n")
    rp = E.Replayer(root)
    assert [ms - T for ms, s, _ in rp if s == "mlb"] == [2, 5, 5, 11] and rp.stats()["mlb"]["clamped"] == 1


def test_seek_skips_to_start_in_large_plain_file(tmp_path):
    cap = Cap(tmp_path / "live")
    for i in range(20000):
        cap.add("clob", T + i, msg={"pad": "x" * 20})
    root = cap.write()
    got = [ms - T for ms, _, _ in E.Replayer(root, start_ms=T + 18_500)]
    assert got[0] == 18_500 and got[-1] == 19_999 and len(got) == 1500


def test_follow_mode_lag_partial_lines_and_day_rollover(tmp_path):
    root = tmp_path / "live"
    (root / DAY).mkdir(parents=True)
    f = root / DAY / "mlb.jsonl"
    f.write_text(json.dumps({"recv_ms": T + 1000}) + "\n" + json.dumps({"recv_ms": T + 5000}) + "\n" + '{"recv_ms": %d' % (T // 10000))
    now = [T / 1000 + 4]
    R = E.Replayer(root, follow=True, poll_s=0, clock=lambda: now[0])     # production rescan interval
    assert R.rescan_s == 5
    rp = iter(R)

    def released():
        out = []
        while True:
            ms, s, _ = next(rp)
            if s is None:
                return out, ms - T
            out.append(ms - T)

    assert released() == ([1000], 2000)                        # 5000 is younger than the 2 s lag
    now[0] += 4
    assert released() == ([], 2000)                            # no scan since t+4 s: the watermark holds
    now[0] += 1
    assert released() == ([5000], 7000)                        # scanned at t+9 s; the partial line waits
    with open(f, "a") as fh:
        fh.write("9000}\n")
    assert released() == ([], 7000)
    D2 = root / "2026-09-02"                                   # new stream/day files appear between scans
    D2.mkdir()
    (D2 / "mlb.jsonl").write_text(json.dumps({"recv_ms": T + 9800}) + "\n")
    (D2 / "clob.jsonl").write_text(json.dumps({"recv_ms": T + 9500, "msg": {}}) + "\n")
    now[0] += 4.5                                              # wall t+13.5 s, next scan at t+14 s
    assert released() == ([], 7000)
    now[0] += .5
    assert released() == ([9000, 9500, 9800], 12000) and R.late == 0
    with open(D2 / "mlb.jsonl", "a") as fh:                    # written behind the watermark: counted late
        fh.write(json.dumps({"recv_ms": T + 11000}) + "\n")
    assert released() == ([12000], 12000) and R.late == 1


def test_follow_mode_matches_batch_across_midnight(tmp_path):
    """The recorder rotates to a new UTC day between two scans. The delay2 entry (eligible
    00:00:00.5) must see the asks pulled at 00:00:00.2 in the new day's file, as a batch replay does."""
    M = calendar.timegm((2026, 9, 2, 0, 0, 0)) * 1000
    cap = mlb_game(Cap(tmp_path / "unused"), t=M - 1500)       # signal 23:59:58.5, pongs every 2 s
    cap.clob(M + 200, change(A, .70, 0), change(A, .72, 0))
    rows = sorted(((r["recv_ms"], day, stream, r) for (day, stream), rs in cap.rows.items() for r in rs), key=lambda x: x[0])
    live, i = tmp_path / "live", [0]

    def emit(upto):                                            # the recorder: append by UTC receipt day
        while i[0] < len(rows) and rows[i[0]][0] <= upto:
            _, day, stream, r = rows[i[0]]
            (live / day).mkdir(parents=True, exist_ok=True)
            with open(live / day / f"{stream}.jsonl", "a") as fh:
                fh.write(json.dumps(r) + "\n")
            i[0] += 1

    now = [(M - 61_000) / 1000]                                # scans at -61 s, ..., -1 s, +4 s
    emit(M - 61_000)
    eng = E.Engine(tmp_path / "follow", live, None, None, model_dir(tmp_path))
    R = E.Replayer(live, follow=True, poll_s=0, clock=lambda: now[0])
    rp = iter(R)
    while now[0] * 1000 < M + 30_000:
        emit(int(now[0] * 1000))
        for ms, s, rec in rp:
            if s is None:
                eng.advance(ms)
                break
            eng.process(ms, s, rec)
        now[0] += .25
    eng.close()
    follow = decisions(tmp_path / "follow")
    batch = run(tmp_path, live, out="batch")
    assert follow["mlb_10c_delay2|1"]["status"] == "unfilled" and follow["mlb_10c_delay2|1"]["reason"] == "no_depth_at_limit"
    assert follow["mlb_10c|1"]["status"] == "filled" and follow == batch and R.late == 0


# ----------------------------------------------------------------------------- state rules

def test_freshness_quiet_live_closed_missing_snapshot_and_legacy():
    st = E.State()
    st.apply_clob(0, {"msg": book("t")})
    assert st.freshness("t", 3000)["connection_ok"] == "unknown" and st.freshness("t", 3000)["ok"]
    assert st.freshness("t", 6000)["reason"] == "stale_token_legacy"
    st.apply_clob(10_000, {"conn": "open"})
    assert st.freshness("t", 10_001)["reason"] == "no_snapshot_since_open"   # pre-open snapshot does not count
    st.apply_clob(10_500, {"msg": change("t", .7, 5)})
    assert st.freshness("t", 10_600)["reason"] == "no_snapshot_since_open"
    st.apply_clob(11_000, {"msg": book("t")})
    for k in range(1, 30):                                     # quiet book, live connection
        st.apply_clob(11_000 + 2000 * k, {"conn": "pong"})
    f = st.freshness("t", 70_000)
    assert f["ok"] and f["connection_ok"] is True and f["book_age_ms"] == 59_000
    assert st.freshness("t", 75_001)["reason"] == "connection_not_current"
    st.apply_clob(70_500, {"conn": "closed"})
    assert st.freshness("t", 71_000)["reason"] == "connection_closed"


def test_reference_is_strictly_before_and_at_most_120s():
    st = E.State()
    st.apply_clob(1000, {"msg": trade("t", .5)})
    st.apply_clob(2000, {"msg": trade("t", .6)})
    st.apply_clob(2000, {"msg": trade("t", .65)})
    assert st.reference("t", 2000).price == .5                 # same-ms prints are not "before"
    assert st.reference("t", 2001).price == .65
    assert st.reference("t", 122_001) is None
    st.apply_clob(3000, {"msg": trade("t", 1.2)})              # invalid print ignored
    assert st.reference("t", 3001).price == .65


def test_mlb_labels_ghost_runner_and_game_over():
    assert next_label(5, "top") == (5, "bottom") and next_label(5, "bottom") == (6, "top")
    assert game_over((9, "bottom"), 2, 9) and not game_over((9, "bottom"), -1, 9)
    assert game_over((10, "top"), 1, 9) and not game_over((10, "top"), 0, 9) and not game_over((9, "top"), 3, 9)


# ----------------------------------------------------------------------------- MLB policies

def test_mlb_fill_fees_no_lookahead_and_delay2(tmp_path):
    cap = mlb_game(Cap(tmp_path / "live"))
    cap.clob(T, trade(A, .50))                                 # same-ms print: not the reference
    cap.clob(T + 1, trade(A, .95), change(A, .70, 0))          # after the decision: invisible to t=T entries
    cap.clob(T + 1500, change(A, .72, 30))
    d = run(tmp_path, cap.write())
    ten, three, late = d["mlb_10c|1"], d["mlb_03c|1"], d["mlb_10c_delay2|1"]
    assert ten["reference_price"] == .75 and ten["reference_age_ms"] == 30_000
    assert ten["leader_prob"] == pytest.approx(.90) and ten["side"] == "away" and ten["limit"] == .76
    assert ten["signal"]["half"] == "bottom" and ten["signal"]["inning"] == 5 and ten["eligible_ms"] == T
    lv = ten["fill"]["levels"]
    assert lv[0] == [.70, 50] and lv[1][0] == .72 and ten["status"] == "filled"
    fee = sum(.05 * q * p * (1 - p) for p, q in lv)
    assert ten["fee_usd"] == pytest.approx(fee) and ten["cost_usd"] == pytest.approx(sum(p * q for p, q in lv) + fee)
    assert ten["cost_usd"] < 100 and ten["shares"] == pytest.approx(100 / (.76 + .05 * .76 * .24)) and ten["fee_usd_us"] == pytest.approx(sum(.0695 * q * p * (1 - p) for p, q in lv))
    assert three["fill"]["levels"] == lv                       # independent depth consumption per policy
    assert late["eligible_ms"] == T + 2000 and late["fill"]["levels"] == [[.72, 30]] and late["status"] == "partial"
    assert ten["connection_ok"] is True and ten["book_age_ms"] == 590_000 and ten["shakedown"]
    assert ten["shakedown_reasons"] == ["not_activated"]


def test_pending_entry_executes_before_later_records(tmp_path):
    cap = mlb_game(Cap(tmp_path / "live"), delay=1)
    cap.clob(T + 1000, change(A, .70, 5))                      # recv == eligible: applied first
    cap.clob(T + 1001, change(A, .70, 50), change(A, .72, 0))  # later: must not be seen
    d = run(tmp_path, cap.write())
    assert d["mlb_10c|1"]["eligible_ms"] == T + 1000 and d["mlb_10c|1"]["fill"]["levels"] == [[.70, 5], [.72, 100]]
    assert d["mlb_10c_delay2|1"]["eligible_ms"] == T + 3000 and d["mlb_10c_delay2|1"]["fill"]["levels"] == [[.70, 50]]


def test_first_signal_per_game_and_threshold(tmp_path):
    cap = Cap(tmp_path / "live")
    mlb_game(cap, ref=.85)                                     # edge .05: only the 3c control qualifies
    cap.clob(T + 60_000, trade(A, .70))
    cap.ls(T + 120_000, 5, "Bottom", 3, 3, 1)                  # top 6 label, edge .20: 10c qualifies now
    cap.clob(T + 200_000, trade(A, .70))
    cap.ls(T + 300_000, 6, "Top", 3, 3, 1)                     # a later qualifying signal is ignored
    out = tmp_path / "out"
    d = run(tmp_path, cap.write())
    assert d["mlb_03c|1"]["signal"]["inning"] == 5 and d["mlb_10c|1"]["signal"]["inning"] == 6
    assert len(d) == 3
    ev = {s["signal_id"]: s["evals"] for s in signals(out)}
    assert ev["5bottom"]["mlb_10c"] == "below_threshold" and ev["6top"]["mlb_03c"] == "after_first_signal"
    assert ev["6bottom"]["mlb_10c"] == "after_first_signal"


def test_half_inning_detection_missed_outs_ghost_runner_and_rejections(tmp_path):
    cap = Cap(tmp_path / "live")
    cap.mlb_map(T - 3_600_000, pk=2, home="h2", away="a2", cid="0x2")
    cap.mlb_map(T - 3_600_000, pk=3, home="h3", away="a3", cid="0x3", game_type="F")
    cap.mlb_map(T - 3_600_000, pk=4, match="ambiguous", home="h4", away="a4", cid="0x4")
    cap.ls(T, 3, "Top", 2, 0, 0, pk=2)
    cap.ls(T + 10, 3, "Bottom", 1, 0, 1, pk=2)                 # three-out state fell between polls
    cap.ls(T + 20, 3, "Bottom", 3, 0, 1, pk=2)
    cap.ls(T + 30, 3, "Bottom", 3, 0, 2, pk=2)                 # duplicate three-out state
    cap.ls(T + 40, 4, "Top", 0, 0, 2, pk=2)                    # already signalled
    cap.ls(T + 50, 9, "Top", 3, 1, 2, pk=2)                    # home leads after top 9: game over
    cap.ls(T, 9, "Bottom", 3, 1, 1, pk=3)                      # postseason extras: no ghost runner
    cap.ls(T + 10, 10, "Top", 3, 1, 1, pk=3)
    cap.ls(T, 5, "Top", 3, 1, 2, pk=4)
    out = tmp_path / "out"
    run(tmp_path, cap.write())
    sig = {(s["game_id"], s["signal_id"]): s for s in signals(out)}
    assert sig[("2", "3bottom")]["source"] == "half_change" and sig[("2", "4top")]["source"] == "three_outs"
    assert sig[("2", "4top")]["diff"] == 1 and sig[("2", "3bottom")]["diff"] == 1
    assert sig[("2", "9bottom")]["status"] == "game_over" and len([k for k in sig if k[0] == "2"]) == 3
    assert sig[("3", "10top")]["bases"] == 0 and sig[("3", "10top")]["status"] == "tied"
    assert sig[("4", "5bottom")]["status"] == "unmapped" and sig[("4", "5bottom")]["mapping"] == "ambiguous"
    cap2 = Cap(tmp_path / "live2")
    cap2.mlb_map(T - 3_600_000, pk=5, home="h5", away="a5", cid="0x5")
    cap2.clob(T - 1000, trade("a5", .5, cid="0x5"))
    cap2.ls(T, 9, "Bottom", 3, 2, 2, pk=5)
    cap2.ls(T + 10, 10, "Top", 3, 3, 2, pk=5)                  # regular season 10th: runner on second
    run(tmp_path, cap2.write(), out="out2")
    s = {x["signal_id"]: x for x in signals(tmp_path / "out2")}
    assert s["10top"]["bases"] == 2 and s["10top"]["status"] == "tied"
    assert s["10bottom"]["bases"] == 2 and s["10bottom"]["fair_home"] == .20 and s["10bottom"]["evals"]["mlb_10c"] == "qualified"


def test_missing_rules_model_and_stale_book_are_recorded(tmp_path):
    cap = mlb_game(Cap(tmp_path / "live"), min_size=None)
    d = run(tmp_path, cap.write())
    assert d["mlb_10c|1"]["status"] == "rejected" and d["mlb_10c|1"]["reason"] == "missing_market_rules:min_size"
    # a missing season snapshot neither rejects the game nor is cached: the file is retried
    md = model_dir(tmp_path)
    cap = Cap(tmp_path / "live2")
    cap.mlb_map(T - 3_600_000, start=calendar.timegm((2027, 4, 1, 0, 0, 0)))
    cap.clob(T - 1000, trade(A, .5))
    cap.ls(T, 5, "Top", 3, 3, 1)
    cap.clob(T + 100_000, trade(A, .5))
    cap.ls(T + 120_000, 5, "Bottom", 3, 3, 1)
    root = cap.write()
    eng = E.Engine(tmp_path / "out2", root, None, None, md)
    new = md / "mlb_fair_2026.json"
    for ms, s, rec in E.Replayer(root):
        if ms > T + 60_000 and not new.exists():               # built after the first signal
            new.write_text((md / "mlb_fair_2025.json").read_text().replace('"max_season": 2025', '"max_season": 2026'))
        eng.process(ms, s, rec)
    eng.finish()
    eng.close()
    d, sig = decisions(tmp_path / "out2"), {x["signal_id"]: x for x in signals(tmp_path / "out2")}
    assert set(sig["5bottom"]["evals"].values()) == {"missing_model_snapshot"}
    assert d["mlb_10c|1"]["signal"]["inning"] == 6 and d["mlb_10c|1"]["model_max_season"] == 2026
    cap = Cap(tmp_path / "live3")
    cap.mlb_map(T - 3_600_000)
    cap.conn(T - 600_000, "open").clob(T - 590_000, book(A)).clob(T - 100_000, trade(A, .5))
    cap.ls(T, 5, "Top", 3, 3, 1)
    d = run(tmp_path, cap.write(), out="out3")
    assert d["mlb_10c|1"]["reason"] == "connection_not_current" and d["mlb_10c|1"]["connection_ok"] is False


# ----------------------------------------------------------------------------- soccer

def soccer_game(cap, slug="epl-a-b", delay=0, espn_id="99", ref=.80, games_at=T - 7_200_000, match="exact"):
    legs = {s: dict(condition_id=f"0x{s}", yes_token=f"{s}_yes", no_token=f"{s}_no", fee_rate=.05, fee_exponent=1,
                    tick=.01, min_size=5, seconds_delay=delay) for s in ("home", "draw", "away")}
    cap.add("soccer_games", games_at, event_slug=slug, series="epl", start_ts=START_S, legs=legs,
            espn=dict(path="soccer/eng.1", id=espn_id, home_id="1", away_id="2", home_name="A", away_name="B",
                      kickoff_ts=START_S), match=match, reason="")
    cap.conn(T - 600_000, "open")
    cap.clob(T - 590_000, book("home_yes", ((.85, 200),), cid="0xhome"))
    for k in range(1, 400):
        cap.conn(T - 600_000 + 2000 * k, "pong")
    cap.clob(T - 20_000, trade("home_yes", ref, cid="0xhome"))
    return cap


def espn(cap, ms, events, home=1, away=0, espn_id="99", state="in", period=2):
    return cap.add("espn", ms, espn_id=espn_id, path="soccer/eng.1", state=state, period=period, clock_s=0,
                   display_clock="", home=home, away=away, new_events=events)


def ev(fp, minute, display=None, period=2, kind="foul"):
    return dict(type=kind, period=period, clock_value=minute * 60, clock_display=display or f"{minute}'",
                team_id="1", scoring=False, text="", fp=fp)


def test_soccer_windows_delays_band_and_new_event_rule(tmp_path):
    cap = soccer_game(Cap(tmp_path / "live"))
    espn(cap, T - 900_000, [ev("p1", 91, "45'+2'", period=1)])   # first-half added time never counts
    espn(cap, T - 600_000, [ev("a", 72)], home=0)              # tied: no trigger
    espn(cap, T - 500_000, [ev("a", 72), ev("b", 86)])         # 'a' is not new; 86 is outside every window
    espn(cap, T - 400_000, [ev("c", 78)])                      # both controls, no reference yet: not an attempt
    espn(cap, T - 15_000, [ev("c2", 80)])                      # both controls again, now with a reference
    espn(cap, T - 1000, [ev("d", 90, "90'+1'")], home=1, away=1)
    espn(cap, T, [ev("d", 90, "90'+1'"), ev("e", 92, "90'+2'")])
    cap.conn(T + 70_000, "pong")
    out = tmp_path / "out"
    d = run(tmp_path, cap.write())
    c1, c2, p = d["soccer_ctrl_75_85|epl-a-b"], d["soccer_ctrl_70_80|epl-a-b"], d["soccer_added_time|epl-a-b"]
    assert c1["signal"]["fp"] == c2["signal"]["fp"] == "c2" and c1["signal_recv_ms"] == T - 15_000
    assert c1["reference_price"] == .80 and c1["status"] == "unfilled"
    assert p["signal"]["fp"] == "e" and p["signal"]["minute"] == 92 and p["side"] == "home" and p["token"] == "home_yes"
    assert p["reference_price"] == .80 and p["limit"] == .81 and p["eligible_ms"] == T + 3000
    assert p["status"] == "unfilled" and p["reason"] == "no_depth_at_limit"
    assert {k.split("|")[0]: v["eligible_ms"] - T for k, v in d.items() if v["signal_recv_ms"] == T} == \
        {"soccer_added_time": 3000, "soccer_delay2": 5000, "soccer_delay10": 10_000, "soccer_delay60": 60_000}
    ev_by = {s["signal_id"]: s["evals"] for s in signals(out)}
    assert ev_by["d"]["soccer_added_time"] == "tied_or_no_score" and "p1" not in ev_by
    assert ev_by["c"]["soccer_ctrl_75_85"] == ev_by["c"]["soccer_ctrl_70_80"] == "no_reference"
    assert ev_by["c2"]["soccer_ctrl_70_80"] == "qualified" and ev_by["e"]["soccer_ctrl_70_80"] == "after_first_signal"
    cap = soccer_game(Cap(tmp_path / "live2"), ref=.98, delay=5)
    espn(cap, T, [ev("x", 95, "90'+5'")])
    cap.clob(T - 10_000, trade("home_yes", .84, cid="0xhome"))
    cap.conn(T + 70_000, "pong")
    d = run(tmp_path, cap.write(), out="out2")
    p = d["soccer_added_time|epl-a-b"]
    assert p["eligible_ms"] == T + 5000 and p["status"] == "filled" and p["fill"]["levels"][0][0] == .85
    # outside the band: recorded as the event's evaluation; a later in-band event is the attempt
    cap = soccer_game(Cap(tmp_path / "live3"), ref=.98)
    espn(cap, T, [ev("x", 95, "90'+5'")])
    cap.clob(T + 5000, trade("home_yes", .84, cid="0xhome"))
    espn(cap, T + 10_000, [ev("y", 96, "90'+6'")])
    cap.conn(T + 70_000, "pong")
    d = run(tmp_path, cap.write(), out="out3")
    ev_by = {s["signal_id"]: s["evals"] for s in signals(tmp_path / "out3")}
    assert ev_by["x"]["soccer_added_time"] == "leader_price_outside_band" and ev_by["y"]["soccer_added_time"] == "qualified"
    p = d["soccer_added_time|epl-a-b"]
    assert p["signal"]["fp"] == "y" and p["reference_price"] == .84 and p["status"] == "filled"


def test_soccer_whistle_post_state_and_extra_time_never_trigger(tmp_path):
    cap = soccer_game(Cap(tmp_path / "live"))
    espn(cap, T, [ev("w", 95, "90'+5'", kind="end-regular-time")])         # the whistle itself (final score)
    espn(cap, T + 5000, [ev("x", 96, "90'+6'")])                            # anything after it
    cap.conn(T + 70_000, "pong")
    d = run(tmp_path, cap.write())
    ev_by = {s["signal_id"]: s["evals"] for s in signals(tmp_path / "out")}
    assert not d and set(ev_by["w"].values()) == set(ev_by["x"].values()) == {"after_terminal"}
    cap = soccer_game(Cap(tmp_path / "live2"))
    espn(cap, T, [ev("x", 95, "90'+5'")], state="post")                    # a final response
    d = run(tmp_path, cap.write(), out="out2")
    assert not d and signals(tmp_path / "out2")[0]["evals"]["soccer_added_time"] == "after_terminal"
    cap = soccer_game(Cap(tmp_path / "live3"))
    espn(cap, T, [ev("x", 95, "90'+5'")], period=3)                        # extra time under way
    espn(cap, T + 1000, [ev("y", 94, "90'+4'")])
    d = run(tmp_path, cap.write(), out="out3")
    assert not d and {s["evals"]["soccer_added_time"] for s in signals(tmp_path / "out3")} == {"after_terminal"}
    cap = soccer_game(Cap(tmp_path / "live4"))                              # control: before the whistle
    espn(cap, T, [ev("x", 95, "90'+5'")])
    espn(cap, T + 1000, [ev("w", 96, "90'+6'", kind="end-regular-time")], state="post")
    cap.conn(T + 70_000, "pong")
    d = run(tmp_path, cap.write(), out="out4")
    assert d["soccer_added_time|epl-a-b"]["signal"]["fp"] == "x"


# ----------------------------------------------------------------------------- determinism, activation

def fixture_many(root):
    cap = Cap(root)
    for pk, t in ((1, T), (2, T + 3_600_000), (3, T + 7_200_000)):
        mlb_game(cap, t=t, pk=pk, home=f"h{pk}", away=f"a{pk}", cid=f"0x{pk}", delay=0)
    soccer_game(cap)
    cap.clob(T + 2_990_000, trade("home_yes", .80, cid="0xhome"))
    espn(cap, T + 3_000_000, [ev("e", 92, "90'+2'")])
    cap.conn(T + 9_000_000, "pong")
    return cap


def test_restart_is_deterministic_without_duplicates(tmp_path):
    root = fixture_many(tmp_path / "live").write()
    full = run(tmp_path, root, out="a")
    lines = (tmp_path / "a" / "decisions.jsonl").read_text()
    assert run(tmp_path, root, out="a") == full and (tmp_path / "a" / "decisions.jsonl").read_text() == lines
    cut = pd.Timestamp(T + 3_600_001, unit="ms", tz="UTC").isoformat()   # game 2's delay2 entry is pending
    part = run(tmp_path, root, out="b", until=cut)
    assert "mlb_10c|2" in part and "mlb_10c_delay2|2" not in part and "mlb_10c|3" not in part
    resumed = run(tmp_path, root, out="b")
    assert resumed == full and len(E.read_jsonl(tmp_path / "b" / "decisions.jsonl")) == len(full) == 3 * 3 + 4
    ck = json.loads((tmp_path / "b" / "checkpoint.json").read_text())
    assert ck["last_recv_ms"] == T + 9_000_000


def test_restart_rebuilds_state_from_a_partial_warmup_window(tmp_path):
    """Game 2 is 40 h after the connection opened: the resumed replay starts mid-capture, after
    that `open` and after all of game 1, and must still reproduce the uninterrupted decisions."""
    T2 = T + 40 * 3_600_000
    cap = mlb_game(Cap(tmp_path / "live"))                     # connection opens at T - 10 min
    cap.mlb_map(T2 - 3_600_000, pk=2, home="h2", away="a2", cid="0x2", start=T2 // 1000 - 3600, delay=1)
    cap.clob(T2 - 590_000, book("a2", ((.70, 50), (.72, 100)), cid="0x2"), book("h2", ((.31, 50),), cid="0x2"))
    for k in range(1, 600):                                    # same connection: pongs only, no new open
        cap.conn(T2 - 600_000 + 2000 * k, "pong")
    cap.clob(T2 - 30_000, trade("a2", .75, cid="0x2"))
    cap.clob(T2 + 1500, change("a2", .70, 10))
    cap.ls(T2 - 60_000, 5, "Top", 2, 3, 1, pk=2)
    cap.ls(T2, 5, "Top", 3, 3, 1, pk=2)
    root = cap.write()
    full = run(tmp_path, root, out="a")
    part = run(tmp_path, root, out="b", until=pd.Timestamp(T2 + 1, unit="ms", tz="UTC").isoformat())
    assert "mlb_10c|1" in part and not [k for k in part if k.endswith("|2")]   # game 2's entries were pending
    start = json.loads((tmp_path / "b" / "checkpoint.json").read_text())["last_recv_ms"] - E.WARMUP_MS
    assert start > T + 600_000                                 # after the open and every game 1 record
    resumed = run(tmp_path, root, out="b")
    assert resumed == full and len(E.read_jsonl(tmp_path / "b" / "decisions.jsonl")) == len(full) == 6
    x = full["mlb_10c|2"]
    assert x["status"] == "filled" and x["fill"]["levels"] == [[.70, 50], [.72, pytest.approx(x["shares"] - 50)]]
    assert x["conn"]["conn_open_ms"] is None and full["mlb_10c|1"]["conn"]["conn_open_ms"] == T - 600_000


def test_batch_end_watermark_is_the_directory_scan(tmp_path, monkeypatch):
    """A batch run over a growing capture executes only entries eligible before its scan - lag."""
    cap = mlb_game(Cap(tmp_path / "live"), delay=3)             # entries eligible at T + 3 s and T + 5 s
    cap.rows[(DAY, "clob")] = [r for r in cap.rows[(DAY, "clob")] if r["recv_ms"] <= T]   # capture ends at T
    root = cap.write()
    monkeypatch.setattr(RUN, "Replayer", functools.partial(E.Replayer, clock=lambda: T / 1000 + 4))
    r = RUN.run(live=root, out=tmp_path / "out", activation=None, snapshot_log=None, model_dir=model_dir(tmp_path),
                allow_network=False)
    assert r["pending"] == 3 and not decisions(tmp_path / "out")
    r = RUN.run(live=root, out=tmp_path / "out2", activation=None, snapshot_log=None, model_dir=model_dir(tmp_path),
                allow_network=False, until=pd.Timestamp(T + 6000, unit="ms", tz="UTC").isoformat())
    assert r["pending"] == 3                                   # the scan bounds the end watermark too


def test_admission_uses_pregame_metadata_only(tmp_path):
    cap = Cap(tmp_path / "live")
    mlb_game(cap)                                              # pk 1: exact before first pitch
    cap.mlb_map(T - 60_000, pk=1, match="ambiguous")           # later flips are ignored
    cap.mlb_map(T - 50_000, pk=1, home=A, away=H)
    mlb_game(cap, pk=2, home="h2", away="a2", cid="0x2", map_at=T - 1_800_000)   # first mapped 30 min after first pitch
    root = cap.write()
    d = run(tmp_path, root)
    assert d["mlb_10c|1"]["status"] == "filled" and d["mlb_10c|1"]["token"] == A and d["mlb_10c|1"]["side"] == "away"
    assert not [k for k in d if k.endswith("|2")]
    sig = {s["game_id"]: s for s in signals(tmp_path / "out")}
    assert sig["2"]["status"] == "discovered_after_start" and sig["2"]["mapping"] == "discovered_after_start"
    m = R.coverage(root, None, [], {})["mlb_map"].set_index("game_id")
    assert m.loc["1", "match"] == "exact" and m.loc["2", "match"] == "discovered_after_start"
    cap = soccer_game(Cap(tmp_path / "live2"), games_at=T - 2_700_000)            # recorded 45 min after kickoff
    espn(cap, T, [ev("x", 95, "90'+5'")])
    cap.conn(T + 70_000, "pong")
    root = cap.write()
    assert not run(tmp_path, root, out="out2")
    assert set(signals(tmp_path / "out2")[0]["evals"].values()) == {"discovered_after_start"}
    assert R.coverage(root, None, [], {})["soccer"].match.tolist() == ["discovered_after_start"]
    cap = soccer_game(Cap(tmp_path / "live3"))
    soccer_game(cap, games_at=T - 600_000, match="ambiguous")   # re-matched after kickoff: ignored
    espn(cap, T, [ev("x", 95, "90'+5'")])
    cap.conn(T + 70_000, "pong")
    assert run(tmp_path, cap.write(), out="out3")["soccer_added_time|epl-a-b"]["status"] == "unfilled"


def test_model_label_requires_the_pinned_snapshot_and_follows_manifest_changes(tmp_path):
    md = model_dir(tmp_path)
    sha = E.hashlib.sha256((md / "mlb_fair_2025.json").read_bytes()).hexdigest()
    base = dict(activated_ms=(START_S - 60) * 1000, code_sha256=E.code_hashes()["code_sha256"], spec_sha256=spec.spec_hash())
    act, log = tmp_path / "ACTIVATION.json", tmp_path / "snapshots.jsonl"
    root = mlb_game(Cap(tmp_path / "live")).write()
    act.write_text(json.dumps(base | dict(model_snapshots={"2025": "0" * 64})))       # a replacement was logged later
    log.write_text(json.dumps(dict(max_season=2025, sha256=sha, logged_ms=1)) + "\n")
    d = run(tmp_path, root, activation=act, snapshot_log=log, model_dir=md)
    assert d["mlb_10c|1"]["shakedown_reasons"] == ["model_snapshot_mismatch"]
    act.write_text(json.dumps(base | dict(model_snapshots={})))                       # unpinned: logged for 2025?
    log.write_text(json.dumps(dict(max_season=2024, sha256=sha, logged_ms=1)) + "\n")
    d = run(tmp_path, root, out="o2", activation=act, snapshot_log=log, model_dir=md)
    assert d["mlb_10c|1"]["shakedown_reasons"] == ["model_snapshot_unlogged"]
    # manifest and snapshot log written while the engine runs: labels match a fresh replay
    act2, log2 = tmp_path / "A2.json", tmp_path / "L2.jsonl"
    eng = E.Engine(tmp_path / "o3", root, act2, log2, md)
    for ms, s, rec in E.Replayer(root):
        if ms >= T - 100_000 and not act2.exists():
            act2.write_text(json.dumps(base | dict(model_snapshots={})))
            log2.write_text(json.dumps(dict(max_season=2025, sha256=sha, logged_ms=(START_S - 10) * 1000)) + "\n")
        eng.process(ms, s, rec)
    eng.finish()
    eng.close()
    live = decisions(tmp_path / "o3")
    assert live["mlb_10c|1"]["shakedown"] is False and live == run(tmp_path, root, out="o4", activation=act2,
                                                                     snapshot_log=log2, model_dir=md)


def test_malformed_records_are_skipped_not_fatal(tmp_path):
    st = E.State()
    st.apply_clob(1, {"msg": {"event_type": "tick_size_change", "asset_id": "t", "market": "0x1", "new_tick_size": ""}})
    assert st.errors == 1 and "0x1" not in st.meta
    cap = soccer_game(Cap(tmp_path / "live"))
    cap.clob(T - 5000, {"event_type": "tick_size_change", "asset_id": "home_yes", "market": "0xhome", "new_tick_size": "bad"})
    cap.add("mlb_map", T - 4000, game_pk="x", match="exact", pm={"condition_id": "0xq"}, mlb_start_ts="soon")
    espn(cap, T - 3000, [7])                                   # raises inside the strategy
    espn(cap, T, [ev("x", 95, "90'+5'")])
    cap.conn(T + 70_000, "pong")
    r = RUN.run(live=cap.write(), out=tmp_path / "out", activation=None, snapshot_log=None, model_dir=model_dir(tmp_path),
                allow_network=False)
    assert r["record_errors"] == {"espn": 1} and r["book_errors"] == 1
    assert decisions(tmp_path / "out")["soccer_added_time|epl-a-b"]["status"] == "unfilled"


def test_one_writer_per_output_and_duplicate_keys_count_once(tmp_path):
    out, kw = tmp_path / "out", dict(activation=None, snapshot_log=None, model_dir=model_dir(tmp_path), allow_network=False)
    with E.out_lock(out):
        with pytest.raises(SystemExit, match="in use"):
            RUN.run(live=tmp_path / "live", out=out, **kw)
    assert RUN.run(live=tmp_path / "live", out=out, **kw)["decisions"] == 0      # released
    d = dict(key="mlb_10c|1", policy="mlb_10c", sport="mlb", game_id="1", condition_id="0x1", token="t", fee_rate=0.,
             fill={"levels": [[.5, 10.]]}, shares=10., status="filled", shakedown=False)
    f = R.frame([d, d | {"fill": {"levels": [[.6, 10.]]}}], {"0x1": dict(status="resolved", payouts={"t": 1.0})})
    x = f[f.policy == "mlb_10c"]
    assert len(x) == 1 and x.stake_usd.iloc[0] == pytest.approx(5.0)


def test_legacy_map_cache_follows_changes_not_counts(tmp_path, monkeypatch):
    from pmsports.paper import mlb as M
    eng = E.Engine(tmp_path / "out", tmp_path / "live", None, None, model_dir(tmp_path))
    s = eng.strategies[0]
    monkeypatch.setattr(s, "_schedule", lambda dates: pd.DataFrame({"game_pk": [0]}))
    monkeypatch.setattr(M, "match_mlb", lambda pmg, sched: pmg.assign(game_pk=pmg.pk))

    def g(pk):
        return {"game": dict(pk=pk, condition_id=f"0x{pk}", home_token=f"h{pk}", away_token=f"a{pk}", event_date=DAY)}

    s.on_record("games", g(1), 0)
    s.on_record("games", g(2), E.PRUNE_MS)
    assert sorted(s._legacy_map()) == [1, 2]
    s.prune(E.PRUNE_MS + 1)                                    # game 1 dropped, game 3 added: same count
    s.on_record("games", g(3), E.PRUNE_MS + 2)
    assert sorted(s._legacy_map()) == [2, 3]
    eng.close()


def test_activation_labels_primary_only_with_matching_hashes(tmp_path):
    md = model_dir(tmp_path)
    sha = E.hashlib.sha256((md / "mlb_fair_2025.json").read_bytes()).hexdigest()
    act = tmp_path / "ACTIVATION.json"
    act.write_text(json.dumps(dict(activated_ms=(START_S - 60) * 1000, code_sha256=E.code_hashes()["code_sha256"],
                                   spec_sha256=spec.spec_hash(), model_snapshots={"2025": sha})))
    root = mlb_game(Cap(tmp_path / "live")).write()
    d = run(tmp_path, root, activation=act, model_dir=md)
    assert d["mlb_10c|1"]["shakedown"] is False and d["mlb_10c|1"]["shakedown_reasons"] == []
    act.write_text(json.dumps(dict(activated_ms=(START_S + 60) * 1000, code_sha256="x", spec_sha256=spec.spec_hash(),
                                   model_snapshots={})))
    d = run(tmp_path, root, out="o2", activation=act, model_dir=md)
    assert d["mlb_10c|1"]["shakedown_reasons"] == ["code_hash_mismatch", "scheduled_before_activation", "model_snapshot_unlogged"]


def test_legacy_day_mapping_is_shakedown_only(tmp_path):
    cap = Cap(tmp_path / "live")
    g = dict(event_id="1", slug="mlb-away-home-2026-10-01", event_date=DAY, title="Away Team vs. Home Team",
             away_team="Away Team", home_team="Home Team", start_ts=START_S, condition_id=CID, home_token=H, away_token=A,
             home_outcome_idx=1, home_won=None, fee_rate=.05, fee_exponent=1)
    cap.add("games", T - 7_200_000, game=g)
    cap.clob(T - 590_000, book(A, ((.70, 50), (.72, 100))))
    cap.clob(T - 30_000, trade(A, .75))
    cap.clob(T - 2000, change(A, .60, 0, side="BUY"))           # token message 2 s old at the entry
    cap.ls(T - 60_000, 5, "Top", 2, 3, 1, pk=77)
    cap.ls(T, 5, "Top", 3, 3, 1, pk=77)
    root = cap.write()
    pd.DataFrame([dict(game_pk=77, official_date=DAY, game_ts=float(START_S), game_type="R", status="Final",
                       abstract_state="Final", double_header="N", game_number=1, away_id=1, home_id=2,
                       away_name="Away Team", home_name="Home Team", away_team_name="Away Team",
                       home_team_name="Home Team", away_abbr="AW", home_abbr="HO", away_score=3, home_score=1,
                       scheduled_innings=9, final_inning=9)]).to_parquet(root / DAY / "schedule.parquet")
    d = run(tmp_path, root)
    x = d["mlb_10c|77"]
    assert x["mapping_source"] == "legacy" and x["connection_ok"] == "unknown" and x["status"] == "filled"
    assert set(x["shakedown_reasons"]) == {"not_activated", "legacy_mapping", "connection_markers_missing", "legacy_market_rules"}


# ----------------------------------------------------------------------------- settlement and report

def test_settlement_validity_gate_and_cache(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "decisions.jsonl").write_text("".join(json.dumps(dict(key=k, condition_id=c, shares=s)) + "\n" for k, c, s in
                                                 (("a", "0xok", 5), ("b", "0xbad", 5), ("c", "0xopen", 5), ("d", "0xnofill", 0))))
    calls = []

    def fetch(cid):
        calls.append(cid)
        m = dict(conditionId=cid, closed=True, umaResolutionStatus="resolved", clobTokenIds=json.dumps(["y", "n"]),
                 closedTime="2026-10-02 01:00:00+00")
        return {"0xok": [m | dict(outcomePrices=json.dumps(["1", "0"]))], "0xbad": [m | dict(outcomePrices=json.dumps(["0.7", "0.3"]))],
                "0xopen": [m | dict(closed=False, outcomePrices=json.dumps(["0.9", "0.1"]))]}[cid]

    r = S.settle(out, tmp_path / "live", fetch=fetch, universe=False)
    cache = json.loads((out / "settlements.json").read_text())
    assert r["newly_resolved"] == 1 and cache["0xok"]["payouts"] == {"y": 1.0, "n": 0.0}
    assert cache["0xbad"]["status"] == "invalid_payout" and "0xopen" not in cache and "0xnofill" not in calls
    calls.clear()
    S.settle(out, tmp_path / "live", fetch=fetch, universe=False)
    assert sorted(calls) == ["0xbad", "0xopen"]
    assert S.valid_payouts({"a": .5, "b": .5}) and not S.valid_payouts({"a": 1.})


def test_report_cash_identities_and_separation(tmp_path):
    cap = fixture_many(tmp_path / "live")
    cap.mlb_map(T - 3_600_000, pk=9, home="h9", away="a9", cid="0x9")    # exact, but nothing captured
    root = cap.write()
    md = model_dir(tmp_path)
    act = tmp_path / "ACTIVATION.json"
    sha = E.hashlib.sha256((md / "mlb_fair_2025.json").read_bytes()).hexdigest()
    act.write_text(json.dumps(dict(activated_ms=(START_S - 60) * 1000, activated_utc="x", code_sha256=E.code_hashes()["code_sha256"],
                                   spec_sha256=spec.spec_hash(), model_snapshots={"2025": sha})))
    d = run(tmp_path, root, activation=act, model_dir=md)
    out = tmp_path / "out"
    # game 1 resolves for the away leader, game 2 against it, game 3 stays open; one shakedown row
    (out / "settlements.json").write_text(json.dumps({
        "0x1": dict(status="resolved", payouts={"a1": 1.0, "h1": 0.0}, closed_time="2026-09-02T03:00:00Z"),
        "0x2": dict(status="resolved", payouts={"a2": 0.0, "h2": 1.0}, closed_time="2026-09-02T04:00:00Z")}))
    lines = E.read_jsonl(out / "decisions.jsonl")
    for x in lines:
        if x["key"] == "mlb_03c|2":
            x["shakedown"], x["shakedown_reasons"] = True, ["test"]
    (out / "decisions.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    res = R.build(out, root, tmp_path / "PAPER_TEST.md", tmp_path / "ledgers", act)
    f = res["frame"]
    st = f[f.settled]
    assert np.allclose(st.cost_usd, st.stake_usd + st.fee_usd) and np.allclose(st.pnl_usd, st.payout - st.cost_usd)
    p = res["primary"]
    ten = f[(f.policy == "mlb_10c") & f.settled]
    assert p["mlb_10c"]["settled"] == 2 and p["mlb_10c"]["open"] == 1
    assert p["mlb_10c"]["net_cash_usd"] == pytest.approx(ten.pnl_usd.sum())
    assert p["mlb_10c"]["roi"] == pytest.approx(ten.pnl_usd.sum() / ten.cost_usd.sum())
    x1 = d["mlb_10c|1"]
    lv = x1["fill"]["levels"]
    one = f[(f.policy == "mlb_10c_plus1c") & (f.game_id == "1")].iloc[0]
    assert one.stake_usd == pytest.approx(sum(q * (px + .01) for px, q in lv))
    assert one.fee_usd == pytest.approx(sum(.05 * q * (px + .01) * (.99 - px) for px, q in lv))
    assert one.pnl_us == pytest.approx(sum(q for _, q in lv) - sum(q * (px + .01) for px, q in lv)
                                       - sum(.0695 * q * (px + .01) * (.99 - px) for px, q in lv))
    assert p["mlb_03c"]["settled"] == 1 and res["shakedown"]["mlb_03c"]["settled"] == 1
    assert p["mlb_10c"]["roi_ex_best1"] == pytest.approx(-1.0)
    doc = json.loads((tmp_path / "ledgers" / "paper_mlb_10c.json").read_text())
    assert doc["group"] == "Forward paper tests" and doc["n_total_trades"] == 2 and doc["truncated"] is False
    rows = pd.DataFrame(doc["rows"], columns=doc["columns"])
    assert set(rows.period) == {"prospective_paper"} and {"book_age_ms", "fee_usd_us", "connection_ok", "reference_age_ms"} <= set(rows)
    assert np.allclose(rows.pnl_usd, rows.payout - rows.stake_usd - rows.fee_usd)
    sh = pd.DataFrame(json.loads((tmp_path / "ledgers" / "paper_mlb_03c.json").read_text())["rows"],
                      columns=doc["columns"])
    assert sorted(sh.period) == ["prospective_paper", "shakedown"]
    md_text = (tmp_path / "PAPER_TEST.md").read_text()
    assert "## Primary (prospective) results" in md_text and "## Shakedown (not evidence)" in md_text
    assert "Endpoints and gate" in md_text and "soccer_added_time" in md_text and "Missing captures" in md_text
    miss = res["coverage"]["missing"].set_index("sport")
    assert miss.loc["mlb"].to_dict() == {"window": "primary", "exact games": 4, "no state feed": 1, "no signal logged": 1,
                                         "no book snapshot": 1, "primary decision": 3}
    # the soccer fixture only captured the home Yes book
    assert miss.loc["soccer"].to_dict() == {"window": "primary", "exact games": 1, "no state feed": 0, "no signal logged": 0,
                                            "no book snapshot": 1, "primary decision": 1}
    assert R.endpoint("mlb", f, res["coverage"], json.loads(act.read_text()), T / 1000)["count"] == 2


# ----------------------------------------------------------------------------- model snapshot

def test_model_snapshot_reproduces_baseline_we_exactly(tmp_path):
    from pmsports.analysis.hypotheses import _baseline_we
    from pmsports.paper.mlb_model import BASELINE, COLS, Snapshot, build_snapshot
    if not BASELINE.exists():
        pytest.skip("baseline parquet not built")
    b = pd.read_parquet(BASELINE, columns=COLS)
    snap = build_snapshot(2025, BASELINE, snap_dir=tmp_path)
    assert snap["sha256"] == E.hashlib.sha256((tmp_path / "mlb_fair_2025.json").read_bytes()).hexdigest()
    s = Snapshot.load(2025, tmp_path)
    rows = b.sample(2500, random_state=7)[["inning", "half", "outs", "bases", "diff"]].reset_index(drop=True)
    extra = pd.DataFrame(dict(inning=[15, 1, 12, 9, 11], half=["bottom", "top", "top", "bottom", "top"], outs=[2, 0, 1, 0, 2],
                              bases=[7, 7, 2, 6, 5], diff=[12, -9, 3, 0, -8]))
    rows = pd.concat([rows, extra], ignore_index=True)
    want = _baseline_we(b, rows.copy(), 2025)
    got = np.array([s.fair_home(r.inning, r.half, r.outs, r.bases, r["diff"]) for _, r in rows.iterrows()])
    assert len(rows) >= 2000 and (got == want).all()
    tiny = pd.DataFrame(dict(season=[2020] * 40, inning=[1] * 40, half=["top"] * 40, outs=[0] * 40, bases=[0] * 40,
                             diff=[0] * 40, home_won_final=[True] * 30 + [False] * 10))
    t = Snapshot(build_snapshot(2025, tiny, snap_dir=None))
    assert t.fair_home(1, "top", 0, 0, 0) == (30 + 5) / (40 + 10) and t.fair_home(1, "top", 0, 0, 3) == .5


def test_idle_state_is_pruned_after_48h(tmp_path):
    st = E.State()
    st.apply_clob(0, {"msg": book("old")})
    st.apply_clob(0, {"msg": trade("old", .5)})
    st.update_meta("0xold", {"tick": .01}, 0, "t")
    st.apply_clob(E.PRUNE_MS, {"msg": book("new")})
    assert st.prune(E.PRUNE_MS + 1) == 1
    assert "old" not in st.book.books and "old" not in st.trades and "old" not in st.book.initialized
    assert "new" in st.book.initialized and "0xold" not in st.meta
    cap = mlb_game(Cap(tmp_path / "live"))
    cap.conn(T + 3 * 86400_000, "pong")                        # three days later: game state dropped
    root = cap.write()
    eng = E.Engine(tmp_path / "out", root, None, None, model_dir(tmp_path))
    for ms, s, rec in E.Replayer(root):
        eng.process(ms, s, rec)
    eng.close()
    mlb = eng.strategies[0]
    assert len(decisions(tmp_path / "out")) == 3 and not mlb.games and not mlb.by_cid and not mlb.done
    assert A not in eng.state.book.books


def test_snapshot_command_logs_once_and_refuses_silent_replacement(tmp_path, monkeypatch):
    from pmsports.paper import mlb_model as M
    base = pd.DataFrame(dict(season=[2024] * 40, inning=[1] * 40, half=["top"] * 40, outs=[0] * 40, bases=[0] * 40,
                             diff=[0] * 40, home_won_final=[True] * 25 + [False] * 15))
    monkeypatch.setattr(RUN, "build_snapshot", lambda ms, snap_dir=None: M.build_snapshot(ms, base, snap_dir))
    log = tmp_path / "log.jsonl"
    a = RUN.snapshot(2025, tmp_path, log)
    assert RUN.snapshot(2025, tmp_path, log)["sha256"] == a["sha256"] and len(E.read_jsonl(log)) == 1
    assert M.Snapshot.load(2025, tmp_path).sha256 == a["sha256"] == E.read_jsonl(log)[0]["sha256"]
    base.loc[0, "home_won_final"] = False
    with pytest.raises(SystemExit):
        RUN.snapshot(2025, tmp_path, log)


def test_activation_manifest_contents_and_refusals(tmp_path, monkeypatch):
    md = model_dir(tmp_path)
    git = {"status": "", "rev-parse": "abc123"}
    monkeypatch.setattr(RUN, "_git", lambda *a: git[a[0]])
    path = tmp_path / "paper" / "ACTIVATION.json"
    m = RUN.activate(path, md)
    h = E.code_hashes()
    assert m["code_sha256"] == h["code_sha256"] and m["taker_fee_sha256"] == h["taker_fee_sha256"]
    assert "pmsports/paper/engine.py" in m["code_files"] and "pmsports/book_replay.py" in m["code_files"]
    assert "pmsports/paper/capture_soccer.py" not in m["code_files"] and "pmsports/paper/report.py" in m["support_files"]
    assert m["spec_sha256"] == spec.spec_hash() and set(m["rules"]) == set(spec.POLICIES) and m["git_commit"] == "abc123"
    assert m["model_snapshots"] == {"2025": E.hashlib.sha256((md / "mlb_fair_2025.json").read_bytes()).hexdigest()}
    assert abs(m["activated_ms"] - time.time() * 1000) < 60_000 and json.loads(path.read_text()) == m
    with pytest.raises(SystemExit):
        RUN.activate(path, md)                                 # one-time
    git["status"] = " M pmsports/paper/engine.py"
    with pytest.raises(SystemExit):
        RUN.activate(tmp_path / "other.json", md)              # uncommitted engine code
