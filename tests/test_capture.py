"""Capture track (record.py, paper/capture_soccer.py, paper/rotate.py). No network."""
import asyncio
import gzip
import json
import logging
import os
import threading
import time
from collections import Counter
from datetime import date

import pytest

from pmsports import polymarket as pm
from pmsports import record as R
from pmsports.events import soccer as S
from pmsports.paper import capture_soccer as cs
from pmsports.paper import rotate as RT


@pytest.fixture(autouse=True)
def _no_discovery_gap(monkeypatch):
    monkeypatch.setattr(cs, "DISCOVER_ESPN_GAP_S", 0.0)


class ListSink:
    """Keeps what a real _Sink would have serialized at write time (no aliasing of later edits)."""

    def __init__(self):
        self.rows = []

    def write(self, obj, recv_ms=None):
        self.rows.append(json.loads(json.dumps({**obj, **({"recv_ms": recv_ms} if recv_ms is not None else {})})))


def router(*routes):
    """Fake get_json: first (predicate, response) whose predicate accepts (url, params)."""
    calls = []

    def get(url, params=None, **kw):
        calls.append((url, dict(params or {})))
        for pred, resp in routes:
            if pred(url, params or {}):
                return resp(url, params or {}) if callable(resp) else resp
        raise AssertionError(f"unexpected request {url} {params}")
    get.calls = calls
    return get


# ------------------------------------------------------------------------------------ sink / clob

def test_sink_monotonic_recv_ms_and_explicit_stamp(tmp_path):
    s = R._Sink("x", root=tmp_path)
    now = int(time.time() * 1000)
    s.write({"a": 1}, recv_ms=now + 5000)
    s.write({"a": 2})                         # wall clock is earlier: clamped, never decreases
    s.write({"a": 3}, recv_ms=now + 6000)
    rows = [json.loads(line) for f in tmp_path.glob("*/x.jsonl") for line in open(f)]
    assert [r["a"] for r in rows] == [1, 2, 3]
    assert [r["recv_ms"] for r in rows] == [now + 5000, now + 5000, now + 6000]
    assert list(rows[0]) == ["recv_ms", "a"]


def test_top_of_book_skips_connection_markers(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "DATA_DIR", tmp_path)
    d = tmp_path / "live" / "2026-09-25"
    d.mkdir(parents=True)
    book = {"event_type": "book", "asset_id": "t1", "timestamp": "5",
            "bids": [{"price": "0.4", "size": "10"}], "asks": [{"price": "0.6", "size": "10"}]}
    lines = [{"recv_ms": 1, "conn": "open", "n_assets": 1}, {"recv_ms": 2, "msg": [book]},
             {"recv_ms": 3, "conn": "pong"}, {"recv_ms": 4, "conn": "closed"}]
    (d / "clob.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    df = R.top_of_book("2026-09-25")
    assert len(df) == 1 and df.best_bid[0] == 0.4 and df.best_ask[0] == 0.6


class FakeWS:
    """Scripted websocket: strings are received, callables run, exceptions raise, 'wait' idles 0.1 s."""

    def __init__(self, script):
        self.script, self.sent = list(script), []

    async def send(self, m):
        self.sent.append(m)

    async def recv(self):
        while self.script:
            x = self.script.pop(0)
            if callable(x):
                x()
                continue
            if isinstance(x, Exception):
                raise x
            if x == "wait":
                await asyncio.sleep(0.1)
                return "PONG"
            return x
        await asyncio.sleep(3600)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class PongWS(FakeWS):
    """Answers every PING with a PONG, like the CLOB server."""

    def __init__(self):
        super().__init__([])
        self.q = None

    async def send(self, m):
        self.sent.append(m)
        if m == "PING":
            self.q.put_nowait("PONG")

    async def recv(self):
        return await self.q.get()

    async def __aenter__(self):
        self.q = asyncio.Queue()
        return self


def _run_ws(fn, scripts, seconds, make=FakeWS):
    """Run a websocket loop `fn(sink)` against scripted connections for `seconds`."""
    sink, conns = ListSink(), []

    def connect(*a, **k):
        ws = make(scripts.pop(0) if scripts else []) if make is FakeWS else make()
        conns.append(ws)
        return ws

    async def go():
        try:
            await asyncio.wait_for(fn(sink), seconds)
        except asyncio.TimeoutError:
            pass
    orig = R.websockets.connect
    R.websockets.connect = connect
    try:
        asyncio.run(go())
    finally:
        R.websockets.connect = orig
    return sink.rows, conns


def _run_clob(ref, scripts, seconds=0.6, make=FakeWS):
    return _run_ws(lambda sink: R._clob(ref, sink, time.time() + 60), scripts, seconds, make)


def test_clob_connection_markers_pong_and_live_subscribe():
    ref = {"tokens": {"t1", "t2"}}
    book1 = json.dumps([{"event_type": "book", "asset_id": "t1", "bids": [], "asks": []}])
    book3 = json.dumps({"event_type": "book", "asset_id": "t3", "bids": [], "asks": []})

    def add():
        ref["tokens"] = {"t1", "t2", "t3"}
    rows, conns = _run_clob(ref, [[book1, "PONG", add, "PONG", book3, ConnectionError("gone")]])
    kinds = [r.get("conn") or "msg" for r in rows]
    assert kinds == ["open", "msg", "pong", "pong", "msg", "closed"]
    assert rows[0]["n_assets"] == 2 and "n_assets" not in rows[2]
    assert rows[1]["msg"] == json.loads(book1)          # payload unchanged: {"recv_ms", "msg"}
    assert len(conns) == 1                              # new token did not force a reconnect
    sent = conns[0].sent
    assert json.loads(sent[0]) == {"assets_ids": ["t1", "t2"], "type": "market", "custom_feature_enabled": True}
    assert "PING" in sent
    sub = [json.loads(m) for m in sent if m.startswith("{") and "operation" in m]
    assert sub == [{"assets_ids": ["t3"], "operation": "subscribe", "custom_feature_enabled": True}]


def test_clob_rebuilds_connection_when_live_subscribe_gets_no_snapshot(monkeypatch):
    monkeypatch.setattr(R, "SUB_CHECK_S", 0.05)
    monkeypatch.setattr(R, "RECONNECT_S", 0.01)
    ref = {"tokens": {"t1"}}

    def add():
        ref["tokens"] = {"t1", "t9"}
    rows, conns = _run_clob(ref, [[add, "PONG", "wait", "PONG"]])
    assert [r["conn"] for r in rows][:4] == ["open", "pong", "pong", "closed"]
    assert len(conns) == 2 and json.loads(conns[1].sent[0])["assets_ids"] == ["t1", "t9"]
    assert rows[4] == {**rows[4], "conn": "open", "n_assets": 2}


def test_clob_ping_every_two_seconds_default():
    assert R.PING_S == 2.0 and R.MLB_POLL_S == 2.0


def test_clob_pings_on_cadence_and_logs_every_pong(monkeypatch):
    monkeypatch.setattr(R, "PING_S", 0.05)
    rows, (ws,) = _run_clob({"tokens": {"t1"}}, [], seconds=0.5, make=PongWS)
    pings, pongs = ws.sent.count("PING"), [r for r in rows if r.get("conn") == "pong"]
    assert 6 <= pings <= 12                              # one PING per PING_S, not per message
    assert pings - 1 <= len(pongs) <= pings              # every PONG received is logged
    kinds = [r.get("conn") for r in rows]
    assert kinds[0] == "open" and set(kinds[1:-1]) == {"pong"} and kinds[-1] in ("pong", "closed")  # closed: cancelled


def test_clob_unsubscribe_writes_marker(monkeypatch):
    monkeypatch.setattr(R, "UNSUB_EVERY_S", 0.0)
    ref = {"tokens": {"t1", "t2"}}
    book = json.dumps([{"event_type": "book", "asset_id": "t1", "bids": [], "asks": []}])

    def drop():
        ref["tokens"] = {"t1"}
    rows, conns = _run_clob(ref, [[book, drop, "PONG", "wait", "PONG"]])
    assert [r for r in rows if r.get("conn") == "unsub"] == [{"conn": "unsub", "assets": ["t2"], "n_assets": 1}]
    sent = [json.loads(m) for m in conns[0].sent if "unsubscribe" in m]
    assert sent == [{"assets_ids": ["t2"], "operation": "unsubscribe"}] and len(conns) == 1


def test_clob_reconnects_when_nothing_arrives_for_dead_s(monkeypatch):
    monkeypatch.setattr(R, "DEAD_S", 0.15)
    monkeypatch.setattr(R, "PING_S", 0.05)
    monkeypatch.setattr(R, "RECONNECT_S", 0.01)
    rows, conns = _run_clob({"tokens": {"t1"}}, [[], []], seconds=0.45)   # sockets that never answer
    assert [r["conn"] for r in rows][:3] == ["open", "closed", "open"]
    assert len(conns) >= 2 and "PING" in conns[0].sent


def test_sports_answers_ping_filters_and_reconnects_on_silence(monkeypatch):
    monkeypatch.setattr(R, "SPORTS_DEAD_S", 0.1)
    monkeypatch.setattr(R, "RECONNECT_S", 0.01)
    mlb, atp = json.dumps({"leagueAbbreviation": "mlb", "gameId": 1}), json.dumps({"leagueAbbreviation": "atp"})
    rows, conns = _run_ws(lambda sink: R._sports(sink, time.time() + 60, {}), [["ping", mlb, atp], []], 0.4)
    assert [r["msg"]["gameId"] for r in rows] == [1] and conns[0].sent == ["pong"]
    assert len(conns) >= 2                                 # a silent (half-open) socket is rebuilt


def test_sports_filter_mlb_and_discovered_soccer():
    ref = {"soccer_leagues": {"epl"}, "soccer_game_ids": {777: 1e12}}
    assert R._keep_sports({"leagueAbbreviation": "MLB"}, ref)
    assert R._keep_sports({"leagueAbbreviation": "epl"}, ref)
    assert R._keep_sports({"leagueAbbreviation": "xyz", "gameId": 777}, ref)
    assert not R._keep_sports({"leagueAbbreviation": "atp", "gameId": 1}, ref)
    assert not R._keep_sports("ping", ref)


def test_retoken_expires_tokens():
    ref = {"tokens": set(), "mlb_tokens": {"a": 100.0, "b": 50.0}, "soccer_tokens": {"c": 200.0}}
    cs.retoken(ref, 60.0)
    assert ref["tokens"] == {"a", "c"} and "b" not in ref["mlb_tokens"]


# ------------------------------------------------------------------------------------ MLB polling

def _ls(inning=1, half="Top", outs=0, away=0, home=0):
    return {"currentInning": inning, "inningHalf": half, "outs": outs,
            "teams": {"away": {"runs": away}, "home": {"runs": home}}}


def _run_mlb(ref, get, seconds, **kw):
    sink = ListSink()

    async def go():
        try:
            await asyncio.wait_for(R._mlb(ref, sink, time.time() + 60, get=get, **kw), seconds)
        except asyncio.TimeoutError:
            pass
    asyncio.run(go())
    return sink.rows


def test_mlb_polls_every_live_game_each_cycle_concurrently():
    calls, lock = Counter(), threading.Lock()

    def get(url, params=None, **kw):
        assert kw == {"retries": 1, "timeout": R.MLB_TIMEOUT_S}     # no retry storm, short timeout
        pk = int(url.split("/game/")[1].split("/")[0])
        with lock:
            calls[pk] += 1
            n = calls[pk]
        time.sleep(1.0 if pk == 99 else 0.15)                        # game 99 hangs
        return _ls(outs=n % 3)
    rows = _run_mlb({"game_pks": set(range(1, 13)) | {99}}, get, 0.95, every_s=0.3, live_every_s=None)
    # polled one after another, 12 games x 0.15 s would take 1.8 s per cycle: at most one poll each
    assert all(calls[pk] >= 3 for pk in range(1, 13)), calls
    assert calls[99] == 1                                            # never re-polled while in flight
    by_pk = [r["recv_ms"] for r in rows if r["game_pk"] == 1]
    assert len(by_pk) >= 3 and by_pk == sorted(by_pk)                # stamped at receipt, change-only


def test_mlb_failure_marker_then_recovery_state(caplog):
    n = {"k": 0}

    def get(url, params=None, **kw):
        n["k"] += 1
        if n["k"] in (2, 3):
            raise RuntimeError("statsapi down")
        return _ls(inning=5, outs=1, home=2)
    with caplog.at_level(logging.WARNING, logger="pmsports"):
        rows = _run_mlb({"game_pks": {7}}, get, 0.4, every_s=0.05, live_every_s=None)
    state = {"game_pk": 7, "inning": 5, "half": "Top", "outs": 1, "away": 0, "home": 2, "offense": []}
    assert [{k: v for k, v in r.items() if k != "recv_ms"} for r in rows] == \
        [state, {"game_pk": 7, "error": "RuntimeError: statsapi down"}, state]   # recovery re-writes the state
    assert "linescore 7 failing" in caplog.text


def test_live_pks_include_delayed_start():
    now = pm.parse_ts("2026-09-25T23:30:00Z")

    def g(pk, st, iso):
        return {"gamePk": pk, "gameDate": iso, "status": {"abstractGameState": st}}
    sched = {"dates": [{"games": [g(1, "Live", "2026-09-25T23:05:00Z"), g(2, "Preview", "2026-09-25T23:10:00Z"),
                                  g(3, "Preview", "2026-09-26T00:05:00Z"), g(4, "Final", "2026-09-25T17:05:00Z")]}]}
    assert R.live_pks(sched, now) == {1, 2}          # 2: 'Delayed Start' stays Preview past its start time


def test_mlb_refreshes_polled_set_and_keeps_live_game_tokens():
    sched = {"dates": [{"games": [{"gamePk": 5, "gameDate": "2000-01-01T00:00:00Z",
                                   "status": {"abstractGameState": "Live"}}]}]}
    get = router((lambda u, p: u.endswith("/schedule"), sched), (lambda u, p: "/linescore" in u, _ls()))
    ref = {"game_pks": set(), "tokens": set(), "mlb_tokens": {"h": 1.0}, "mlb_pk_tokens": {5: (("h", "a"), 0.0)}}
    rows = _run_mlb(ref, get, 0.3, every_s=0.05)
    assert ref["game_pks"] == {5} and rows and rows[0]["game_pk"] == 5
    assert ref["tokens"] == {"h", "a"} and ref["mlb_tokens"]["h"] > time.time() + R.MLB_LIVE_GRACE_S - 5


# ------------------------------------------------------------------------------------ MLB mapping

NYY, BAL, BOS, CHC = ("New York Yankees", "Yankees"), ("Baltimore Orioles", "Orioles"), \
    ("Boston Red Sox", "Red Sox"), ("Chicago Cubs", "Cubs")


def _sg(pk, day, iso, home, away, dh="N", num=1):
    return {"gamePk": pk, "officialDate": day, "gameDate": iso, "gameType": "R", "doubleHeader": dh,
            "gameNumber": num, "status": {"abstractGameState": "Preview"}, "linescore": {"scheduledInnings": 9},
            "teams": {"home": {"team": {"name": home[0], "teamName": home[1]}},
                      "away": {"team": {"name": away[0], "teamName": away[1]}}}}


def _rows(*games):
    return R._sched_rows({"dates": [{"date": g["officialDate"], "games": [g]} for g in games]})


def _pm(slug, day, iso, home, away, cid="0xc"):
    return {"condition_id": cid, "slug": slug, "event_date": day, "home_team": home[0], "away_team": away[0],
            "home_token": "H", "away_token": "A", "start_ts": pm.parse_ts(iso), "fee_rate": 0.05, "fee_exponent": 1}


def test_mlb_map_exact_record_format_and_meta():
    rows = _rows(_sg(1, "2026-09-25", "2026-09-25T23:05:00Z", NYY, BAL))
    r = R.map_mlb_game(_pm("mlb-bal-nyy-2026-09-25", "2026-09-25", "2026-09-25T23:05:00Z", NYY, BAL), rows,
                       {"tick": 0.01, "min_size": 5, "seconds_delay": 0})
    assert set(r) == {"game_pk", "game_type", "scheduled_innings", "doubleheader", "game_number", "mlb_start_ts",
                      "home_name", "away_name", "pm", "match", "reason"}
    assert set(r["pm"]) == {"condition_id", "slug", "home_token", "away_token", "start_ts", "fee_rate",
                            "fee_exponent", "tick", "min_size", "seconds_delay"}
    assert (r["match"], r["game_pk"], r["scheduled_innings"], r["doubleheader"]) == ("exact", 1, 9, "N")
    assert (r["pm"]["home_token"], r["pm"]["tick"], r["pm"]["seconds_delay"]) == ("H", 0.01, 0)


def test_mlb_map_orientation_follows_mlb():
    rows = _rows(_sg(1, "2026-09-25", "2026-09-25T23:05:00Z", NYY, BAL))
    r = R.map_mlb_game(_pm("s", "2026-09-25", "2026-09-25T23:05:00Z", BAL, NYY), rows)   # Gamma lists BAL home
    assert r["match"] == "exact" and r["pm"]["home_token"] == "A" and r["pm"]["away_token"] == "H"
    assert r["home_name"] == "New York Yankees" and "swapped" in r["reason"]


def test_mlb_map_doubleheaders():
    split = _rows(_sg(1, "2026-09-25", "2026-09-25T17:05:00Z", BOS, CHC, "S", 1),
                  _sg(2, "2026-09-25", "2026-09-25T22:05:00Z", BOS, CHC, "S", 2))
    r = R.map_mlb_game(_pm("s", "2026-09-25", "2026-09-25T22:05:00Z", BOS, CHC), split)
    assert (r["match"], r["game_pk"], r["game_number"], r["doubleheader"]) == ("exact", 2, 2, "S")
    # both listed starts too close to the Polymarket start: ambiguity is explicit
    close = _rows(_sg(1, "2026-09-25", "2026-09-25T17:05:00Z", BOS, CHC, "S", 1),
                  _sg(2, "2026-09-25", "2026-09-25T17:50:00Z", BOS, CHC, "S", 2))
    r = R.map_mlb_game(_pm("s", "2026-09-25", "2026-09-25T17:30:00Z", BOS, CHC), close)
    assert r["match"] == "ambiguous" and r["game_pk"] is None and "doubleheader" in r["reason"]
    # traditional doubleheader: game 2's listed time is a placeholder right after game 1
    trad = _rows(_sg(1, "2026-09-25", "2026-09-25T20:05:00Z", NYY, BAL, "Y", 1),
                 _sg(2, "2026-09-25", "2026-09-25T20:10:00Z", NYY, BAL, "Y", 2))
    g2 = R.map_mlb_game(_pm("s", "2026-09-25", "2026-09-25T23:05:00Z", NYY, BAL), trad)
    g1 = R.map_mlb_game(_pm("s", "2026-09-25", "2026-09-25T20:05:00Z", NYY, BAL), trad)
    mid = R.map_mlb_game(_pm("s", "2026-09-25", "2026-09-25T21:30:00Z", NYY, BAL), trad)
    assert (g2["game_pk"], g1["game_pk"], mid["match"]) == (2, 1, "ambiguous")


def test_mlb_map_unmatched_and_moved_date():
    rows = _rows(_sg(1, "2026-09-25", "2026-09-25T20:05:00Z", NYY, BAL))
    r = R.map_mlb_game(_pm("s", "2026-09-25", "2026-09-25T20:05:00Z", BOS, CHC), rows)
    assert r["match"] == "unmatched" and r["game_pk"] is None
    moved = R.map_mlb_game(_pm("mlb-bal-nyy-2026-09-26", "2026-09-26", "2026-09-25T20:05:00Z", NYY, BAL), rows)
    assert moved["match"] == "exact" and moved["game_pk"] == 1 and "official date" in moved["reason"]
    far = R.map_mlb_game(_pm("s", "2026-09-26", "2026-09-26T20:05:00Z", NYY, BAL), rows)   # next series day
    assert far["match"] == "unmatched"


def test_map_slate_writes_on_change_rollover_and_one_to_one():
    sched = {"dates": [{"date": "2026-09-25", "games": [_sg(1, "2026-09-25", "2026-09-25T23:05:00Z", NYY, BAL)]}]}
    g = _pm("a", "2026-09-25", "2026-09-25T23:05:00Z", NYY, BAL, cid="0xa")
    meta = R.MarketMeta(ListSink(), get=router((lambda u, p: "/markets/" in u,
                                                {"tokens": [{"token_id": "H"}, {"token_id": "A"}],
                                                 "seconds_delay": 0, "minimum_tick_size": 0.01,
                                                 "minimum_order_size": 5, "accepting_orders": True,
                                                 "closed": False})))
    out, last = ListSink(), {}
    R._map_slate([g], sched, out, meta, False, last)
    R._map_slate([g], sched, out, meta, False, last)
    assert len(out.rows) == 1 and len(meta.sink.rows) == 1        # unchanged: nothing re-written
    R._map_slate([g], sched, out, meta, True, last)
    assert len(out.rows) == 2                                        # daily rollover re-writes
    assert meta.sink.rows[0]["tokens"] == ["H", "A"] and meta.sink.rows[0]["fee_rate"] == 0.05
    dup = _pm("b", "2026-09-25", "2026-09-25T23:00:00Z", NYY, BAL, cid="0xb")
    recs = R._map_slate([g, dup], sched, None, None, False, {})
    assert [r["match"] for r in recs] == ["ambiguous", "ambiguous"]


def test_mlb_map_frozen_at_first_pitch_and_late_markets_unmatched():
    s0 = pm.parse_ts("2026-09-25T23:05:00Z")
    one = {"dates": [{"date": "2026-09-25", "games": [_sg(1, "2026-09-25", "2026-09-25T23:05:00Z", NYY, BAL)]}]}
    two = {"dates": [{"date": "2026-09-25", "games": [_sg(1, "2026-09-25", "2026-09-25T23:05:00Z", NYY, BAL),
                                                      _sg(2, "2026-09-25", "2026-09-25T23:20:00Z", NYY, BAL)]}]}
    g = _pm("a", "2026-09-25", "2026-09-25T23:05:00Z", NYY, BAL, cid="0xa")
    out, state = ListSink(), {}
    R._map_slate([g], one, out, None, False, state, now=s0 - 600)
    assert (out.rows[-1]["match"], out.rows[-1]["game_pk"]) == ("exact", 1)
    assert R.map_mlb_game(g, R._sched_rows(two))["match"] == "ambiguous"     # what a re-map would now say
    R._map_slate([g], two, out, None, False, state, now=s0 + 600)
    assert len(out.rows) == 1                                                  # frozen: nothing re-written
    R._map_slate([g], two, out, None, True, state, now=s0 + 700)             # rollover re-writes it unchanged
    assert len(out.rows) == 2 and (out.rows[-1]["match"], out.rows[-1]["game_pk"]) == ("exact", 1)
    # a second market for the same game, first mapped after the start: unmatched; the first keeps game_pk 1
    late = _pm("b", "2026-09-25", "2026-09-25T23:05:00Z", NYY, BAL, cid="0xb")
    recs = R._map_slate([g, late], one, out, None, False, state, now=s0 + 800)
    assert [(r["match"], r["game_pk"]) for r in recs] == [("exact", 1), ("unmatched", None)]
    assert recs[1]["reason"] == R.FIRST_AFTER_START and recs[1]["pm"]["home_token"] == "H"


def test_mlb_map_freezes_on_earliest_start_or_live_and_keeps_venue_fields_current():
    one = {"dates": [{"date": "2026-09-25", "games": [_sg(1, "2026-09-25", "2026-09-25T23:05:00Z", NYY, BAL)]}]}
    # Polymarket lists 23:15 but MLB starts 23:05: first mapped at 23:10 = after the start
    g = _pm("a", "2026-09-25", "2026-09-25T23:15:00Z", NYY, BAL, cid="0xa")
    (r,) = R._map_slate([g], one, None, None, False, {}, now=pm.parse_ts("2026-09-25T23:10:00Z"))
    assert (r["match"], r["reason"]) == ("unmatched", R.FIRST_AFTER_START)
    # the MLB game turning Live freezes the mapping even before the listed start
    delay = {"s": 0}
    meta = R.MarketMeta(ListSink(), get=lambda u, p=None, **k: {"tokens": [], "seconds_delay": delay["s"],
                                                                 "minimum_tick_size": 0.01, "minimum_order_size": 5})
    out, state, t = ListSink(), {}, pm.parse_ts("2026-09-25T22:00:00Z")
    R._map_slate([g], one, out, meta, False, state, now=t)
    live = json.loads(json.dumps(one))
    live["dates"][0]["games"][0].update(status={"abstractGameState": "Live"}, gameDate="2026-09-25T23:50:00Z")
    delay["s"] = 3
    R._map_slate([g], live, out, meta, False, state, now=t + 60)
    assert [r["pm"]["seconds_delay"] for r in out.rows] == [0, 3]              # venue fields still flow
    assert out.rows[-1]["mlb_start_ts"] == pm.parse_ts("2026-09-25T23:05:00Z")  # not the post-start 23:50


def test_seed_mlb_map_restores_pre_start_mapping(tmp_path):
    now = time.time()
    s0 = now - 3600
    pmr = {"condition_id": "0xa", "slug": "a", "home_token": "H", "away_token": "A", "start_ts": s0}
    pre = {"recv_ms": int((s0 - 600) * 1000), "game_pk": 1, "match": "exact", "mlb_start_ts": s0, "pm": pmr}
    post = {**pre, "recv_ms": int((s0 + 60) * 1000), "game_pk": 2}            # a post-start re-map (old code)
    moved = {**post, "mlb_start_ts": s0 + 7200, "pm": {**pmr, "start_ts": s0 + 7200}}   # ... with a later start
    d = tmp_path / time.strftime("%Y-%m-%d", time.gmtime(now))
    d.mkdir()
    (d / "mlb_map.jsonl").write_text("".join(json.dumps(x) + "\n" for x in (pre, post, moved)))
    state = {}
    R.seed_mlb_map(state, tmp_path, now)
    assert state["pre"]["0xa"]["game_pk"] == 1 and "recv_ms" not in state["pre"]["0xa"]


def test_mlb_schedule_rows_record_games_without_a_market():
    s0 = pm.parse_ts("2026-09-25T23:05:00Z")
    sched = {"dates": [{"date": "2026-09-25", "games": [_sg(1, "2026-09-25", "2026-09-25T23:05:00Z", NYY, BAL),
                                                        _sg(3, "2026-09-25", "2026-09-25T23:10:00Z", BOS, CHC)]}]}
    g = _pm("a", "2026-09-25", "2026-09-25T23:05:00Z", NYY, BAL, cid="0xa")
    sch, state = ListSink(), {}
    R._map_slate([g], sched, None, None, False, state, now=s0 - 3600, sched_sink=sch)
    rows = {r["game_pk"]: r for r in sch.rows}
    assert rows[1]["condition_ids"] == ["0xa"] and rows[3]["condition_ids"] == []   # 3: missing market
    assert rows[3]["home_name"] == "Boston Red Sox" and rows[3]["state"] == "Preview"
    R._map_slate([g], sched, None, None, False, state, now=s0 - 3000, sched_sink=sch)
    R._map_slate([], sched, None, None, False, state, now=s0 + 60, sched_sink=sch)   # game 1 started: not re-written
    assert len(sch.rows) == 2


def test_market_meta_writes_only_on_change():
    state = {"acc": True}
    get = router((lambda u, p: True, lambda u, p: {"tokens": [{"token_id": "y"}], "seconds_delay": 1,
                                                   "minimum_tick_size": 0.01, "minimum_order_size": 5,
                                                   "accepting_orders": state["acc"], "closed": False}))
    m = R.MarketMeta(ListSink(), get=get)
    m.update("0x1", "soccer", 0.05, 1)
    m.update("0x1", "soccer", 0.05, 1)
    state["acc"] = False
    m.update("0x1", "soccer", 0.05, 1)
    rows = m.sink.rows
    assert [r["accepting_orders"] for r in rows] == [True, False]
    assert set(rows[0]) == {"condition_id", "tokens", "seconds_delay", "tick", "min_size", "fee_rate",
                            "fee_exponent", "accepting_orders", "closed", "sport"}


# ------------------------------------------------------------------------------ soccer discovery

START = "2026-09-26T14:00:00Z"
T0 = pm.parse_ts(START)
ARS = {"id": "359", "abbreviation": "ARS", "displayName": "Arsenal", "shortDisplayName": "Arsenal",
       "name": "Arsenal", "location": "Arsenal"}
CHE = {"id": "363", "abbreviation": "CHE", "displayName": "Chelsea", "shortDisplayName": "Chelsea",
       "name": "Chelsea", "location": "Chelsea"}
LEE = {"id": "357", "abbreviation": "LEE", "displayName": "Leeds United", "shortDisplayName": "Leeds",
       "name": "Leeds", "location": "Leeds"}


def gamma_event(slug="epl-ars-che-2026-09-26", series="premier-league-2025", start=START,
                teams=(("ars", "Arsenal FC", "home"), ("che", "Chelsea FC", "away")), gid=11, outcomes='["Yes", "No"]'):
    a, b = slug.split("-")[1:3]

    def mk(tok):
        return {"slug": f"{slug}-{tok}", "sportsMarketType": "moneyline", "conditionId": f"0x{tok}",
                "outcomes": outcomes, "clobTokenIds": json.dumps([f"{tok}-yes", f"{tok}-no"]),
                "feeSchedule": {"rate": 0.05, "exponent": 1}, "orderPriceMinTickSize": 0.01, "orderMinSize": 5,
                "secondsDelay": 1, "gameStartTime": start.replace("T", " ").replace("Z", "+00")}
    return {"id": gid, "slug": slug, "seriesSlug": series, "startTime": start, "gameId": gid,
            "teams": [{"name": n, "abbreviation": ab, "ordering": o} for ab, n, o in teams],
            "markets": [mk(a), mk("draw"), mk(b), {"slug": f"{slug}-total", "sportsMarketType": "totals"}]}


def espn_event(eid, home, away, iso=START, state="pre", period=0, clock=0.0, disp="0'", hs="0", as_="0",
               name="STATUS_SCHEDULED"):
    return {"id": eid, "date": iso, "competitions": [{
        "competitors": [{"homeAway": "home", "team": home, "score": hs},
                        {"homeAway": "away", "team": away, "score": as_}],
        "status": {"clock": clock, "displayClock": disp, "period": period, "type": {"state": state, "name": name}}}]}


def test_parse_event_legs_and_non_three_way():
    g = cs.parse_event(gamma_event())
    assert g["error"] is None and set(g["legs"]) == {"a", "b", "draw"}
    assert g["legs"]["a"]["yes_token"] == "ars-yes" and g["legs"]["b"]["no_token"] == "che-no"
    assert g["teams"]["a"] == {"abbr": "ars", "name": "Arsenal FC", "ordering": "home"}
    assert (g["series"], g["start_ts"], g["legs"]["draw"]["fee_rate"]) == ("premier-league-2025", T0, 0.05)
    two = gamma_event()
    two["markets"] = two["markets"][:2]
    assert cs.parse_event(two) is None                  # not a three-way moneyline event
    flipped = cs.parse_event(gamma_event(outcomes='["No", "Yes"]'))
    assert flipped["legs"]["a"]["yes_token"] == "ars-no"   # Yes token follows the outcome label
    bad = cs.parse_event(gamma_event(outcomes='["Arsenal", "Chelsea"]'))
    assert bad["error"] and "Yes/No" in bad["error"]


def _soccer_setup(sb_events, gamma_events, clob_delay=3):
    """clob_delay: CLOB seconds_delay, or a callable returning it."""
    get = router(
        (lambda u, p: u == cs.GAMMA_EVENTS and p.get("offset") == 0, gamma_events),
        (lambda u, p: u == cs.GAMMA_EVENTS, []),
        (lambda u, p: "/eng.1/scoreboard" in u, lambda u, p: {"events": sb_events()}),
        (lambda u, p: "/markets/" in u, lambda u, p: {"tokens": [{"token_id": u.rsplit("0x", 1)[1] + "-yes"}],
                                                        "seconds_delay": clob_delay() if callable(clob_delay)
                                                        else clob_delay, "minimum_tick_size": 0.001,
                                                        "minimum_order_size": 5, "accepting_orders": True,
                                                        "closed": False}))
    ref = {"tokens": set(), "game_pks": set()}
    meta = R.MarketMeta(ListSink(), get=get)
    sc = cs.SoccerCapture(ref, ListSink(), ListSink(), meta, get=get)
    return sc, ref, meta, get


def test_discover_orientation_from_team_matching_unmapped_and_tokens():
    # Gamma calls Arsenal home, but ESPN has Chelsea at home: legs follow ESPN via team matching.
    sb = [espn_event("700", CHE, ARS), espn_event("701", LEE, CHE, iso="2026-09-26T16:30:00Z")]
    events = [gamma_event(), gamma_event("xyz-aaa-bbb-2026-09-26", series="made-up-league", gid=12),
              gamma_event("epl-lee-bur-2026-09-26", gid=13,
                          teams=(("lee", "Leeds United FC", "home"), ("bur", "Burnley FC", "away")))]
    sc, ref, meta, get = _soccer_setup(lambda: sb, events)
    res = sc.discover(T0 - 3600)
    sc.apply(res, T0 - 3600)
    recs = {r["event_slug"]: r for r in sc.games_sink.rows}
    r = recs["epl-ars-che-2026-09-26"]
    assert r["match"] == "exact" and r["orientation"] == "espn"
    assert r["legs"]["home"]["condition_id"] == "0xche" and r["legs"]["away"]["condition_id"] == "0xars"
    assert r["legs"]["draw"]["condition_id"] == "0xdraw"
    assert r["espn"] == {"path": "eng.1", "id": "700", "home_id": "363", "away_id": "359", "home_name": "Chelsea",
                         "away_name": "Arsenal", "kickoff_ts": T0}
    assert r["legs"]["home"]["seconds_delay"] == 3 and r["legs"]["home"]["tick"] == 0.001   # CLOB values
    assert set(r["legs"]["home"]) == {"condition_id", "yes_token", "no_token", "fee_rate", "fee_exponent",
                                      "tick", "min_size", "seconds_delay"}
    u = recs["xyz-aaa-bbb-2026-09-26"]
    assert (u["match"], u["espn"]) == ("unmapped_league", None) and "made-up-league" in u["reason"]
    assert recs["epl-lee-bur-2026-09-26"]["match"] == "unmatched"       # no ESPN event within 30 min
    assert ref["tokens"] == {"ars-yes", "draw-yes", "che-yes"}
    assert {"epl", "xyz"} <= ref["soccer_leagues"] and 11 in ref["soccer_game_ids"]
    assert set(sc.active) == {"700"} and len(meta.sink.rows) == 3
    n = len(sc.games_sink.rows)
    sc.discover(T0 - 3000)                                             # nothing changed: nothing written
    assert len(sc.games_sink.rows) == n and len(meta.sink.rows) == 3


def test_discover_ambiguous_and_frozen_at_kickoff():
    sb = [espn_event("700", ARS, CHE), espn_event("702", ARS, CHE, iso="2026-09-26T14:15:00Z")]
    sc, ref, meta, get = _soccer_setup(lambda: sb, [gamma_event()])
    sc.discover(T0 - 3600)
    assert sc.games_sink.rows[-1]["match"] == "ambiguous" and "2 ESPN events agree" in sc.games_sink.rows[-1]["reason"]
    sb[:] = [espn_event("700", ARS, CHE)]
    sc.discover(T0 - 1800)
    assert sc.games_sink.rows[-1]["match"] == "exact" and len(sc.games_sink.rows) == 2
    sb[:] = []                                                          # after kickoff the mapping is frozen
    sc.apply(sc.discover(T0 + 600), T0 + 600)
    assert len(sc.games_sink.rows) == 2 and "700" in sc.active and "che-yes" in ref["tokens"]


def test_discover_espn_failure_keeps_previous_record():
    state = {"fail": False}

    def sb():
        if state["fail"]:
            raise RuntimeError("espn down")
        return [espn_event("700", ARS, CHE)]
    sc, ref, meta, get = _soccer_setup(sb, [gamma_event()])
    sc.discover(T0 - 7200)
    state["fail"] = True
    sc.sb_cache.clear()
    sc.discover(T0 - 3600)
    assert [r["match"] for r in sc.games_sink.rows] == ["exact"]


def test_discover_after_kickoff_is_unmatched_and_never_followed():
    sc, ref, meta, get = _soccer_setup(lambda: [espn_event("700", ARS, CHE)], [gamma_event()])
    sc.apply(sc.discover(T0 + 1200), T0 + 1200)
    (r,) = sc.games_sink.rows
    assert (r["match"], r["espn"], r["reason"]) == ("unmatched", None, cs.LATE)
    assert sc.active == {} and ref["tokens"] == set()
    assert not [u for u, _ in get.calls if "/scoreboard" in u]           # no ESPN lookup at all
    sc.discover(T0 + 1800)
    assert len(sc.games_sink.rows) == 1                                  # written once, then frozen


def test_discover_match_found_after_espn_kickoff_is_late():
    sb = [espn_event("700", ARS, CHE, iso="2026-09-26T13:40:00Z")]      # ESPN kicks off 20 min before Gamma's start
    sc, ref, meta, get = _soccer_setup(lambda: sb, [gamma_event()])
    sc.discover(T0 - 600)                                                # after 13:40, before 14:00
    assert [(r["match"], r["reason"]) for r in sc.games_sink.rows] == [("unmatched", cs.LATE)]


def test_discover_kickoff_passing_during_the_pass():
    mono = {"t": 0.0}

    def sb():
        mono["t"] += 120                                                 # this ESPN fetch takes 2 minutes
        return [espn_event("700", ARS, CHE)]
    sc, ref, meta, get = _soccer_setup(sb, [gamma_event()])
    sc.mono = lambda: mono["t"]
    sc.discover(T0 - 60)                                                 # the match is ready at T0 + 60
    assert [(r["match"], r["reason"]) for r in sc.games_sink.rows] == [("unmatched", cs.LATE)]
    # with a pre-kickoff record, a kickoff passing mid-pass keeps that record instead of a re-map
    sc2, _, _, _ = _soccer_setup(sb, [gamma_event()])
    sc2.discover(T0 - 3600)
    sc2.mono = lambda: mono["t"]
    sc2.sb_cache.clear()
    sc2.discover(T0 - 60)
    assert [r["match"] for r in sc2.games_sink.rows] == ["exact"]


def test_discover_frozen_at_recorded_kickoff_when_gamma_start_moves():
    events = [gamma_event()]
    sc, ref, meta, get = _soccer_setup(lambda: [espn_event("700", ARS, CHE)], events)
    sc.discover(T0 - 3600)
    events[:] = [gamma_event(start="2026-09-26T16:00:00Z")]             # Gamma moves the start after kickoff
    sc.apply(sc.discover(T0 + 600), T0 + 600)
    assert [r["match"] for r in sc.games_sink.rows] == ["exact"] and "700" in sc.active


def test_discover_remembers_a_failed_scoreboard_for_the_pass(monkeypatch):
    def sb():
        raise RuntimeError("espn down")
    events = [gamma_event(f"epl-a{i}-b{i}-2026-09-26", gid=100 + i) for i in range(10)]
    sc, ref, meta, get = _soccer_setup(sb, events)
    sc.discover(T0 - 3600)
    assert len([u for u, _ in get.calls if "/scoreboard" in u]) == 1       # not once per game
    assert {r["match"] for r in sc.games_sink.rows} == {"unmatched"} and "espn down" in sc.games_sink.rows[0]["reason"]
    sc.sb_cache.clear()
    sc.discover(T0 - 3000)                                                  # a new pass tries again, once
    assert len([u for u, _ in get.calls if "/scoreboard" in u]) == 2
    # once failures used the pass budget, other scoreboards are not tried either
    monkeypatch.setattr(cs, "DISCOVER_ESPN_BUDGET_S", 0.0)
    events[:] = events[:1] + [gamma_event("epl-cc-dd-2026-09-27", start="2026-09-27T14:00:00Z", gid=300)]
    sc.discover(T0 - 2400)
    assert len([u for u, _ in get.calls if "/scoreboard" in u]) == 3


def test_discover_writes_meta_change_during_espn_outage():
    state = {"fail": False, "delay": 3}

    def sb():
        if state["fail"]:
            raise RuntimeError("espn down")
        return [espn_event("700", ARS, CHE)]
    sc, ref, meta, get = _soccer_setup(sb, [gamma_event()], clob_delay=lambda: state["delay"])
    sc.discover(T0 - 7200)
    state.update(fail=True, delay=7)
    sc.sb_cache.clear()
    sc.discover(T0 - 3600)
    assert [r["legs"]["home"]["seconds_delay"] for r in sc.games_sink.rows] == [3, 7]
    assert sc.records["epl-ars-che-2026-09-26"]["legs"]["home"]["seconds_delay"] == 7


def test_team_score_uses_abbreviation_and_name():
    assert cs.team_score({"abbr": "ars", "name": None}, ARS) == 1.0
    assert cs.team_score({"abbr": "zzz", "name": "Arsenal FC"}, ARS) >= 0.9     # name carries it
    assert cs.team_score({"abbr": "zzz", "name": "Chelsea FC"}, ARS) < cs.TEAM_MIN


def test_espn_dates_eastern_bucket():
    assert cs.espn_dates(pm.parse_ts("2026-09-20T02:30:00Z")) == ["20260919", "20260920"]
    assert cs.espn_dates(T0) == ["20260926"]


# ------------------------------------------------------------------------------- league overrides

def test_paper_league_overrides_take_precedence_over_the_historical_map():
    assert cs.paths_for("soccer-unl") == ("uefa.nations",)
    assert cs.paths_for("concacaf-nations-league") == ("concacaf.nations.league",)
    # a series with no paper override falls back to events/soccer.py unchanged
    assert cs.paths_for("premier-league-2025") == S.paths_for("premier-league-2025") == ("eng.1",)
    # unknown / empty series: no path, no crash
    assert cs.paths_for("totally-unmapped-series") == () and cs.paths_for("") == ()


def test_paper_league_block_beats_the_historical_map():
    # events/soccer.py (frozen) maps chi2-games to Chile's 2nd division; live chi2-games events
    # are China League One, so the paper layer blocks it instead of trusting that mapping.
    assert S.LEAGUE_PATH.get("chi2-games") == "chi.2"      # historical map untouched
    assert "chi2-games" in cs.PAPER_LEAGUE_BLOCK
    assert cs.paths_for("chi2-games") == ()


def test_discover_uses_a_paper_override_path():
    ARM = {"id": "900", "abbreviation": "ARM", "displayName": "Armenia", "shortDisplayName": "Armenia",
           "name": "Armenia", "location": "Armenia"}
    LAT = {"id": "901", "abbreviation": "LAT", "displayName": "Latvia", "shortDisplayName": "Latvia",
           "name": "Latvia", "location": "Latvia"}
    start = "2026-09-26T14:00:00Z"
    ev = gamma_event(slug="unl-arm-lat-2026-09-26", series="soccer-unl", start=start,
                     teams=(("arm", "Armenia", "home"), ("lat", "Latvia", "away")), gid=50)
    get = router(
        (lambda u, p: u == cs.GAMMA_EVENTS and p.get("offset") == 0, [ev]),
        (lambda u, p: u == cs.GAMMA_EVENTS, []),
        (lambda u, p: "/uefa.nations/scoreboard" in u, lambda u, p: {"events": [espn_event("950", ARM, LAT, iso=start)]}),
        (lambda u, p: "/markets/" in u, lambda u, p: {"tokens": [{"token_id": u.rsplit("0x", 1)[1] + "-yes"}],
                                                        "seconds_delay": 0, "minimum_tick_size": 0.01,
                                                        "minimum_order_size": 5, "accepting_orders": True,
                                                        "closed": False}))
    ref = {"tokens": set(), "game_pks": set()}
    meta = R.MarketMeta(ListSink(), get=get)
    sc = cs.SoccerCapture(ref, ListSink(), ListSink(), meta, get=get)
    t0 = pm.parse_ts(start)
    sc.apply(sc.discover(t0 - 3600), t0 - 3600)
    (r,) = sc.games_sink.rows
    assert (r["match"], r["espn"]["path"], r["espn"]["id"]) == ("exact", "uefa.nations", "950")


def test_discover_blocks_chi2_games_before_any_espn_lookup():
    ev = gamma_event(slug="chi2-wux-gh-2026-09-26", series="chi2-games", start="2026-09-26T11:00:00Z",
                     teams=(("wux", "Wuxi Wugou", "home"), ("gh", "Guangxi Hengchen FC", "away")), gid=51)
    sc, ref, meta, get = _soccer_setup(lambda: [], [ev])       # scoreboard route would raise if ever called
    sc.discover(pm.parse_ts("2026-09-26T11:00:00Z") - 3600)
    (r,) = sc.games_sink.rows
    assert (r["match"], r["espn"]) == ("unmapped_league", None)
    assert "chi2-games" in r["reason"]
    assert not [u for u, _ in get.calls if "/scoreboard" in u]


# ------------------------------------------------------------------------------------ ESPN polling

def _ke(i, tp, period, value, disp, team="359", text="", scoring=False):
    return {"id": str(i), "type": {"id": "1", "text": tp.title(), "type": tp}, "period": {"number": period},
            "clock": {"value": value, "displayValue": disp}, "team": {"id": team, "displayName": "X"},
            "scoringPlay": scoring, "text": text, "wallclock": "2026-09-26T15:40:00Z"}


def _summary(events, state="in", period=2, clock=5400.0, disp="90'+1'", hs="1", as_="0", name="STATUS_SECOND_HALF"):
    return {"header": {"competitions": [{
        "status": {"clock": clock, "displayClock": disp, "period": period, "type": {"state": state, "name": name}},
        "competitors": [{"homeAway": "home", "score": hs, "team": {"id": "359"}},
                        {"homeAway": "away", "score": as_, "team": {"id": "363"}}]}]},
        "keyEvents": events}


def test_key_event_fields_and_fingerprint():
    e = _ke(9, "goal---header", 2, 5257.0, "88'", text="Goal! Arsenal 1, Chelsea 0.", scoring=True)
    (k,) = cs.key_events({"keyEvents": [e]})
    assert {x: k[x] for x in ("type", "period", "clock_value", "clock_display", "team_id", "scoring", "text")} == \
        {"type": "goal---header", "period": 2, "clock_value": 5257.0, "clock_display": "88'", "team_id": "359",
         "scoring": True, "text": "Goal! Arsenal 1, Chelsea 0."}
    same = dict(e, id="other", wallclock=None)
    assert cs.key_events({"keyEvents": [same]})[0]["fp"] == k["fp"]      # ids/wallclock are not identity
    edited = dict(e, text="Goal! Arsenal 1, Chelsea 0. Assisted by Y.")
    assert cs.key_events({"keyEvents": [edited]})[0]["fp"] != k["fp"]
    assert len(k["fp"]) == 16


def test_summary_state_changes_and_event_dedup():
    sc = cs.SoccerCapture({}, ListSink(), ListSink())
    e1 = _ke(1, "kickoff", 1, 0.0, "")
    e2 = _ke(2, "substitution", 2, 5257.0, "88'", text="Substitution, Arsenal.")
    e3 = _ke(3, "yellow-card", 2, 5460.0, "90'+1'", text="Card.")
    sc.on_summary("eng.1", "700", 1000, _summary([e1, e2]))
    sc.on_summary("eng.1", "700", 6000, _summary([e1, e2]))                 # identical poll: no record
    sc.on_summary("eng.1", "700", 11000, _summary([e1, e2, e3]))
    sc.on_summary("eng.1", "700", 16000, _summary([e1, e2, e3], disp="90'+2'", clock=5520.0))
    rows = sc.espn_sink.rows
    assert [len(r["new_events"]) for r in rows] == [2, 1, 0]
    assert rows[1]["new_events"][0]["text"] == "Card." and rows[1]["recv_ms"] == 11000
    assert {k: rows[0][k] for k in ("espn_id", "path", "state", "period", "clock_s", "display_clock", "home",
                                    "away", "src")} == {"espn_id": "700", "path": "eng.1", "state": "in",
                                                         "period": 2, "clock_s": 5400.0,
                                                         "display_clock": "90'+1'", "home": 1, "away": 0,
                                                         "src": "summary"}
    sc.on_summary("eng.1", "700", 20000, _summary([e1, e2, e3], state="post", name="STATUS_FULL_TIME"))
    assert sc.espn["700"]["final"] and rows[-1]["state"] == "post"


def test_scoreboard_then_summary_mode_and_polling_schedule():
    sc = cs.SoccerCapture({}, ListSink(), ListSink())
    sc.active = {"700": {"path": "eng.1", "espn_id": "700", "kickoff_ts": T0, "event_slug": "s",
                         "dates": ["20260926"]}}
    now = T0 + 60
    jobs = sc.due(now)
    assert [j[1] for j in jobs] == ["sb"]                                   # before minute 60: scoreboard only
    sc.on_scoreboard("eng.1", "20260926", 1, {"events": [espn_event("700", ARS, CHE, state="in", period=1,
                                                                    clock=600.0, disp="10'")]})
    sc.on_scoreboard("eng.1", "20260926", 2, {"events": [espn_event("700", ARS, CHE, state="in", period=1,
                                                                    clock=600.0, disp="10'")]})
    assert len(sc.espn_sink.rows) == 1 and sc.espn_sink.rows[0]["src"] == "scoreboard"
    sc.on_scoreboard("eng.1", "20260926", 3, {"events": [espn_event("700", ARS, CHE, state="in", period=2,
                                                                    clock=3610.0, disp="61'")]})
    assert sc.espn["700"]["summary"] and len(sc.espn_sink.rows) == 1       # summary mode: board is silent
    sc.last_sb[("eng.1", "20260926")] = now
    jobs = sc.due(now + 1)
    assert [(j[1], j[3]) for j in jobs] == [("sum", "700")]


def test_edited_key_event_is_a_revision_not_new(tmp_path):
    sink = R._Sink("espn", root=tmp_path)
    sc = cs.SoccerCapture({}, ListSink(), sink)
    t = int(time.time() * 1000)
    card = _ke(5, "yellow-card", 2, 4320.0, "72'", text="Card A.")
    sc.on_summary("eng.1", "700", t, _summary([card], clock=4320.0, disp="72'", hs="0"))
    edited = _ke(5, "yellow-card", 2, 4320.0, "72'", text="Card A (second yellow).")
    goal = _ke(6, "goal", 2, 4860.0, "81'", text="Goal!", scoring=True)
    sc.on_summary("eng.1", "700", t + 1, _summary([edited, goal], clock=4860.0, disp="81'", hs="1"))
    moved = _ke(6, "goal", 2, 4920.0, "82'", text="Goal!", scoring=True)   # ESPN corrects the clock
    sc.on_summary("eng.1", "700", t + 2, _summary([edited, moved], clock=4925.0, disp="82'", hs="1"))
    rows = [json.loads(x) for x in open(next(tmp_path.glob("*/espn.jsonl")))]
    # the 72' card was received at 0-0: its edit arriving with a lead must not look newly received
    assert [[e["id"] for e in r["new_events"]] for r in rows] == [["5"], ["6"], []]
    assert rows[1]["revised_events"][0]["prev_fp"] == rows[0]["new_events"][0]["fp"]
    assert rows[2]["revised_events"][0]["clock_value"] == 4920.0 and "revised_events" not in rows[0]
    again = cs.SoccerCapture({}, ListSink(), ListSink())                   # restart: ids come back
    again.seed(tmp_path)
    again.on_summary("eng.1", "700", t + 3, _summary([_ke(6, "goal", 2, 4980.0, "83'", text="Goal!", scoring=True)]))
    assert again.espn_sink.rows[-1]["new_events"] == [] and len(again.espn_sink.rows[-1]["revised_events"]) == 1


def test_summary_mode_when_board_clock_is_zero_or_not_advancing():
    sc = cs.SoccerCapture({}, ListSink(), ListSink())
    sc.active = {e: {"path": "eng.1", "espn_id": e, "kickoff_ts": T0, "event_slug": e, "dates": ["20260926"]}
                 for e in ("700", "701", "702")}
    t = T0 + 4600                                                        # past kickoff + 75 min

    def board(k, clock701):
        return {"events": [espn_event("700", ARS, CHE, state="in", period=2, clock=0.0, disp="61'"),
                           espn_event("701", LEE, CHE, state="in", period=2, clock=clock701, disp="50'"),
                           espn_event("702", ARS, LEE, state="in", period=2, clock=3000.0 + 30 * k, disp="50'")]}
    for k in range(6):                                                   # 701's board answers but never moves
        sc.on_scoreboard("eng.1", "20260926", int((t + 30 * k) * 1000), board(k, 3000.0))
    assert sc.espn["700"]["summary"]                                     # numeric clock 0: display minute counts
    assert not sc.espn["701"]["summary"] and not sc.espn["702"]["summary"]
    sc.last_sb[("eng.1", "20260926")] = t + 150
    jobs = sc.due(t + 151)
    assert sc.espn["701"]["summary"] and not sc.espn["702"]["summary"]   # stale content, not just silence
    assert sorted(j[3] for j in jobs if j[1] == "sum") == ["700", "701"]


def test_soccer_tokens_live_until_final_plus_grace():
    sc, ref, meta, get = _soccer_setup(lambda: [espn_event("700", ARS, CHE)], [gamma_event()])
    sc.apply(sc.discover(T0 - 3600), T0 - 3600)
    cap = T0 + cs.KEEP_S + cs.TOKEN_GRACE_S                              # outlives ESPN polling (kickoff + KEEP_S)
    assert ref["soccer_tokens"] == {t: cap for t in ("ars-yes", "draw-yes", "che-yes")}
    fin = T0 + 6900
    sc.on_summary("eng.1", "700", int(fin * 1000), _summary([], state="post", name="STATUS_FULL_TIME"))
    res = sc.discover(T0 + 7000)
    sc.apply(res, T0 + 7000)
    assert set(ref["soccer_tokens"].values()) == {fin + cs.TOKEN_GRACE_S} and len(ref["tokens"]) == 3
    sc.apply(res, fin + cs.TOKEN_GRACE_S + 1)
    assert ref["tokens"] == set()


def test_seed_keeps_pre_kickoff_record_and_resyncs(tmp_path):
    sc0, _, _, _ = _soccer_setup(lambda: [espn_event("700", ARS, CHE)], [gamma_event()])
    sc0.discover(T0 - 3600)
    (exact,) = sc0.games_sink.rows
    lee = gamma_event("epl-lee-che-2026-09-26", gid=13, teams=(("lee", "Leeds United FC", "home"),
                                                               ("che", "Chelsea FC", "away")))
    sc1, _, _, _ = _soccer_setup(lambda: [espn_event("701", LEE, CHE)], [lee])
    sc1.discover(T0 - 3600)
    (lee_exact,) = sc1.games_sink.rows
    later = {**exact, "match": "unmatched", "espn": None, "start_ts": T0 + 7200, "reason": "moved"}
    d = tmp_path / "2026-09-26"
    d.mkdir()
    lines = [{"recv_ms": int((T0 - 3600) * 1000), **exact}, {"recv_ms": int((T0 + 300) * 1000), **later},
             {"recv_ms": int((T0 + 300) * 1000), **lee_exact}]                # old code: matched after kickoff
    (d / "soccer_games.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    sc, ref, meta, get = _soccer_setup(lambda: [espn_event("700", ARS, CHE), espn_event("701", LEE, CHE)],
                                       [gamma_event(), lee])
    sc.seed(tmp_path, T0 + 600)
    assert sc.records == {"epl-ars-che-2026-09-26": exact} and set(sc.active) == {"700"}
    sc.discover(T0 + 600)
    rows = {r["event_slug"]: r for r in sc.games_sink.rows}
    assert rows["epl-ars-che-2026-09-26"] == exact                      # the pre-kickoff record is the latest again
    assert (rows["epl-lee-che-2026-09-26"]["match"], rows["epl-lee-che-2026-09-26"]["reason"]) == ("unmatched", cs.LATE)
    sc.discover(T0 + 900)
    assert len(sc.games_sink.rows) == 2


def test_summary_interval_stretches_within_budget():
    assert cs.intervals(4, 2) == (30, 5)
    sb, su = cs.intervals(40, 12)
    assert su > 5 and 12 / sb + 40 / su <= cs.ESPN_RPS + 1e-9
    sb, su = cs.intervals(200, 150)
    assert 150 / sb + 200 / su <= cs.ESPN_RPS + 1e-9


def test_seed_prevents_reemitting_events_after_restart(tmp_path):
    day = time.strftime("%Y-%m-%d", time.gmtime())
    e2 = _ke(2, "substitution", 2, 5257.0, "88'", text="Substitution, Arsenal.")
    first = cs.SoccerCapture({}, ListSink(), R._Sink("espn", root=tmp_path))
    first.on_summary("eng.1", "700", int(time.time() * 1000), _summary([e2]))
    rec = {"event_slug": "s", "start_ts": time.time() - 600, "match": "exact", "legs": {},
           "espn": {"path": "eng.1", "id": "700", "kickoff_ts": time.time() - 600}}
    (tmp_path / day / "soccer_games.jsonl").write_text(json.dumps({"recv_ms": 1, **rec}) + "\n")
    again = cs.SoccerCapture({}, ListSink(), ListSink())
    again.seed(tmp_path)
    assert again.records["s"] == rec and "700" in again.active and again.espn["700"]["summary"]
    again.on_summary("eng.1", "700", 5, _summary([e2]))
    assert again.espn_sink.rows == []


# ------------------------------------------------------------------------------------------ rotate

def _day(root, name, files, age_s=7200):
    d = root / name
    d.mkdir(parents=True)
    for f, n in files.items():
        p = d / f
        p.write_text("".join(json.dumps({"recv_ms": i, "k": f}) + "\n" for i in range(n)))
        os.utime(p, (time.time() - age_s, time.time() - age_s))
    return d


def test_rotate_only_old_days_and_verified(tmp_path):
    today = date(2026, 10, 1)
    for name in ("2026-10-01", "2026-09-30"):
        _day(tmp_path, name, {"clob.jsonl": 5})
    old = _day(tmp_path, "2026-09-29", {"clob.jsonl": 1000, "espn.jsonl": 3})
    older = _day(tmp_path, "2026-09-20", {"mlb.jsonl": 7})
    kept = _day(tmp_path, "2026-09-18", {"clob.jsonl": 2})             # frozen research reads it plain
    busy = _day(tmp_path, "2026-09-21", {"clob.jsonl": 2}, age_s=10)    # still being written?
    before = (old / "clob.jsonl").read_bytes()
    assert RT.rotate(tmp_path, today, dry_run=True) and (old / "clob.jsonl").exists()
    done = RT.rotate(tmp_path, today)
    assert sorted(p.name for p in done) == ["clob.jsonl", "espn.jsonl", "mlb.jsonl"]
    assert not (old / "clob.jsonl").exists() and gzip.open(old / "clob.jsonl.gz").read() == before
    assert (older / "mlb.jsonl.gz").exists() and not list(tmp_path.rglob("*.tmp"))
    for name in ("2026-10-01", "2026-09-30"):
        assert (tmp_path / name / "clob.jsonl").exists() and not (tmp_path / name / "clob.jsonl.gz").exists()
    assert (kept / "clob.jsonl").exists() and (busy / "clob.jsonl").exists()


def test_rotate_recovers_partial_runs(tmp_path):
    today = date(2026, 10, 1)
    d = _day(tmp_path, "2026-09-25", {"a.jsonl": 4, "b.jsonl": 4, "c.jsonl": 4})
    raw = (d / "a.jsonl").read_bytes()
    with gzip.open(d / "a.jsonl.gz", "wb") as z:                           # crash after rename, before delete
        z.write(raw)
    with gzip.open(d / "b.jsonl.gz", "wb") as z:                           # truncated gz from a bad run
        z.write((d / "b.jsonl").read_bytes()[:10])
    (d / "c.jsonl.gz.tmp").write_bytes(b"junk")
    (d / "gone.jsonl.gz.tmp").write_bytes(b"junk")                         # orphan tmp
    RT.rotate(tmp_path, today)
    assert sorted(p.name for p in d.iterdir()) == ["a.jsonl.gz", "b.jsonl.gz", "c.jsonl.gz"]
    for f in ("a", "b", "c"):
        assert sum(1 for _ in gzip.open(d / f"{f}.jsonl.gz")) == 4


def test_rotate_keeps_original_when_verification_fails(tmp_path, monkeypatch):
    d = _day(tmp_path, "2026-09-25", {"a.jsonl": 4})
    real = RT._count
    monkeypatch.setattr(RT, "_count", lambda p, gz=False: (0, 0) if gz else real(p))
    assert RT.rotate(tmp_path, date(2026, 10, 1)) == []
    assert sorted(p.name for p in d.iterdir()) == ["a.jsonl"]


def test_rotated_day_is_still_readable(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "DATA_DIR", tmp_path)
    d = tmp_path / "live" / "2026-09-25"
    d.mkdir(parents=True)
    book = {"event_type": "book", "asset_id": "t1", "timestamp": "5",
            "bids": [{"price": "0.4", "size": "10"}], "asks": [{"price": "0.6", "size": "10"}]}
    with gzip.open(d / "clob.jsonl.gz", "wt") as z:
        z.write(json.dumps({"recv_ms": 2, "msg": [book]}) + "\n")
    df = R.top_of_book("2026-09-25")
    assert len(df) == 1 and df.best_ask[0] == 0.6
    with RT.open_capture(d, "clob") as fh:
        assert json.loads(fh.readline())["recv_ms"] == 2


def test_rotate_main_cli(tmp_path):
    _day(tmp_path, "2000-01-01", {"a.jsonl": 2})
    RT.main(["--root", str(tmp_path)])
    assert (tmp_path / "2000-01-01" / "a.jsonl.gz").exists()
