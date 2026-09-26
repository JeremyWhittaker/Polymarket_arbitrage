"""Polymarket US capture (paper/capture_us.py) and its record.py wiring. No network."""
import asyncio
import json
import time
import weakref
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pmsports import record as R
from pmsports.paper import capture_us as CU
from pmsports.paper.us import venue as V

T0 = 1790400000.0                     # 2026-09-26T05:20:00Z
DAY = time.strftime("%Y-%m-%d", time.gmtime(T0))
INF = float("inf")


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ListSink:
    """Keeps what a real _Sink would have serialized at write time."""

    def __init__(self):
        self.rows = []

    def write(self, obj, recv_ms=None):
        self.rows.append(json.loads(json.dumps({**obj, **({"recv_ms": recv_ms} if recv_ms is not None else {})})))


class DaySink:
    """Like record._Sink but stamped by a settable clock (seconds), so tests control recv_ms."""

    def __init__(self, root: Path, name: str):
        self.root, self.name, self.t = Path(root), name, T0
        self.rows = []

    def write(self, obj, recv_ms=None):
        ms = int(self.t * 1000) if recv_ms is None else recv_ms
        d = self.root / time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))
        d.mkdir(parents=True, exist_ok=True)
        rec = {"recv_ms": ms, **obj}
        with open(d / f"{self.name}.jsonl", "a") as fh:
            fh.write(json.dumps(rec) + "\n")
        self.rows.append(json.loads(json.dumps(rec)))


# ------------------------------------------------------------------------------ fixtures: US side

def mlb_side(name, abbr, ordering, long):
    return {"long": long, "description": name, "marketSideType": "MARKET_SIDE_TYPE_INSTRUMENT",
            "team": {"name": name, "abbreviation": abbr, "ordering": ordering}}


def us_mlb(slug, start, long=("Cleveland Guardians", "cle", "away"), short=("Kansas City Royals", "kc", "home"),
           game_id=None, tick=0.005, ended=False, moneyline=True):
    markets = [{"slug": f"asc-{slug}-pos-1pt5", "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_SPREAD"}]
    if moneyline:
        markets.append({"slug": f"aec-{slug}", "sportsMarketTypeV2": CU.MONEYLINE,
                        "sportsMarketType": "baseball_team_full_game_winner", "orderPriceMinTickSize": tick,
                        "minimumTradeQty": 0.01, "feeCoefficient": 0.0695,
                        "marketSides": [mlb_side(*long, True), mlb_side(*short, False)]})
    return {"slug": slug, "startDate": iso(start), "gameId": game_id, "ended": ended, "closed": False,
            "markets": markets}


def us_soccer(slug, start, a=("tij", "Club Tijuana"), b=("atl", "Atlas FC"), game_id=None, draw=True):
    def market(tok, name):
        team = {"name": name, "abbreviation": tok, "ordering": "home"} if name else None
        return {"slug": f"atc-{slug}-{tok}", "sportsMarketTypeV2": CU.DRAWABLE, "orderPriceMinTickSize": 0.01,
                "minimumTradeQty": 0.01, "feeCoefficient": 0.0695,
                "marketSides": [{"long": True, "description": "Yes", "team": team},
                                {"long": False, "description": "No",
                                 "team": {**team, "ordering": "away"} if team else None}]}
    ms = [market(*a), market(*b)] + ([market("draw", None)] if draw else [])
    ms.append({**market(*a), "slug": f"atc-{slug}-fh-{a[0]}"})           # first-half market: ignored
    ms.append({"slug": f"tsc-{slug}-2pt5", "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_TOTAL"})
    return {"slug": slug, "startDate": iso(start), "gameId": game_id, "ended": False, "markets": ms}


def gateway(events: dict, soccer_leagues=("mls", "lmx", "epl"), fail=()):
    """Fake get_json for the US gateway: /v2/sports and paged /v2/leagues/<slug>/events."""
    calls = []

    def get(url, params=None, **kw):
        calls.append((url, dict(params or {})))
        path = url.split("polymarket.us", 1)[1]
        if path == "/v2/sports":
            return {"sports": [{"slug": "baseball", "leagues": [{"slug": "mlb"}]},
                               {"slug": "soccer", "leagues": [{"slug": s} for s in soccer_leagues]}]}
        lg = path.split("/")[3]
        if lg in fail:
            raise RuntimeError(f"HTTP 500 for {lg}")
        ev = events.get(lg, [])
        off, lim = params["offset"], params["limit"]
        return {"events": ev[off:off + lim]}
    get.calls = calls
    return get


# ------------------------------------------------------------------------- fixtures: international

def intl_mlb(slug, cid, home, away, start, game_pk=1, match="exact", recv=T0 - 3600, pm_game_id=None):
    m = {"recv_ms": int(recv * 1000), "game_pk": game_pk if match == "exact" else None, "game_type": "R",
         "mlb_start_ts": start, "home_name": home, "away_name": away,
         "pm": {"condition_id": cid, "slug": slug, "start_ts": start, "home_token": "h", "away_token": "a"},
         "match": match, "reason": "intl reason"}
    g = {"recv_ms": int(recv * 1000), "game": {"slug": slug, "pm_game_id": pm_game_id}}
    return m, g


def intl_soccer(slug, start, home=("tij", "Club Tijuana"), away=("atl", "Atlas FC"), espn=("Club Tijuana", "Atlas"),
                game_id=None, match="exact", recv=T0 - 3600):
    return {"recv_ms": int(recv * 1000), "event_slug": slug, "series": "mex-2025", "start_ts": start,
            "legs": {}, "espn": {"path": "mex.1", "id": "401", "home_name": espn[0], "away_name": espn[1],
                                 "kickoff_ts": start} if match == "exact" else None,
            "match": match, "reason": "intl reason", "game_id": game_id,
            "teams": {"home": {"abbr": home[0], "name": home[1]}, "away": {"abbr": away[0], "name": away[1]}}}


def write_intl(root: Path, mlb=(), soccer=(), day=DAY):
    d = Path(root) / day
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "mlb_map.jsonl", "a") as a, open(d / "games.jsonl", "a") as b:
        for m, g in mlb:
            a.write(json.dumps(m) + "\n")
            b.write(json.dumps(g) + "\n")
    with open(d / "soccer_games.jsonl", "a") as fh:
        for r in soccer:
            fh.write(json.dumps(r) + "\n")


def games(root, now=T0):
    return CU.load_intl(root, now)


def cap(tmp_path, get, **kw):
    return CU.UsCapture(DaySink(tmp_path / "us", "us_map"), ListSink(), ListSink(), root=tmp_path / "us",
                        intl_root=tmp_path, get=get, **kw)


# ----------------------------------------------------------------------------------- parsing

def test_parse_mlb_moneyline_orientation_and_venue_fields():
    g = CU.parse_event(us_mlb("mlb-cle-kc-2026-09-26", T0 + 3600, game_id=77), "mlb", "mlb")
    assert g["error"] is None and g["game_id"] == 77 and g["start_ts"] == T0 + 3600
    m = g["market"]
    assert m["slug"] == "aec-mlb-cle-kc-2026-09-26" and m["tick"] == 0.005 and m["min_qty"] == 0.01
    assert m["qty_increment"] == 0.01 and m["fee_coefficient"] == 0.0695
    assert m["long"] == {"abbr": "cle", "name": "Cleveland Guardians", "ordering": "away"}
    assert m["short"]["ordering"] == "home"
    assert CU.event_slugs(g) == ["aec-mlb-cle-kc-2026-09-26"]


def test_parse_mlb_without_moneyline_is_no_market():
    g = CU.parse_event(us_mlb("mlb-cle-kc-2026-09-26", T0, moneyline=False), "mlb", "mlb")
    assert g["error"].startswith("0 full-game moneyline") and g["market"] is None


def test_parse_soccer_legs_follow_slug_tokens_and_ignore_other_drawable_markets():
    g = CU.parse_event(us_soccer("lmx-tij-atl-2026-09-26", T0), "soccer", "lmx")
    assert g["error"] is None
    assert [g["legs"][k]["slug"] for k in ("a", "b", "draw")] == [
        "atc-lmx-tij-atl-2026-09-26-tij", "atc-lmx-tij-atl-2026-09-26-atl", "atc-lmx-tij-atl-2026-09-26-draw"]
    assert g["legs"]["a"]["team"]["name"] == "Club Tijuana" and g["legs"]["draw"]["team"]["name"] is None
    bad = CU.parse_event(us_soccer("lmx-tij-atl-2026-09-26", T0, draw=False), "soccer", "lmx")
    assert bad["error"].startswith("drawable-outcome (atc-) legs found")


# ------------------------------------------------------------------------------------ mapping

def test_map_mlb_exact_by_slug_orientation_from_us_ordering(tmp_path):
    write_intl(tmp_path, mlb=[intl_mlb("mlb-cle-kc-2026-09-26", "0xc", "Kansas City Royals", "Cleveland Guardians",
                                        T0 + 3600, game_pk=11)])
    g = CU.parse_event(us_mlb("mlb-cle-kc-2026-09-26", T0 + 3600), "mlb", "mlb")
    r = CU.map_mlb(g, games(tmp_path)["mlb"])
    assert r["match"] == "exact" and r["method"] == "slug", r["reason"]
    assert r["intl_key"] == {"game_pk": 11, "condition_id": "0xc", "event_slug": "mlb-cle-kc-2026-09-26"}
    assert r["markets"]["long_team"] == "away" and r["markets"]["slug"] == "aec-mlb-cle-kc-2026-09-26"
    assert r["teams"]["home"]["name"] == "Kansas City Royals" and r["key"] == "mlb-cle-kc-2026-09-26"
    assert r["start_ts"] == T0 + 3600


def test_map_mlb_by_team_names_when_venue_slugs_differ(tmp_path):
    # international 'ari'/'oak' vs US 'az'/'ath' (verified live 2026-09-26)
    write_intl(tmp_path, mlb=[intl_mlb("mlb-ari-sd-2026-09-25", "0xa", "San Diego Padres", "Arizona Diamondbacks",
                                        T0 + 600)])
    us = us_mlb("mlb-az-sd-2026-09-25", T0 + 600 + 30 * 60, long=("Arizona Diamondbacks", "az", "away"),
                short=("San Diego Padres", "sd", "home"))
    r = CU.map_mlb(CU.parse_event(us, "mlb", "mlb"), games(tmp_path)["mlb"])
    assert r["match"] == "exact" and r["method"] == "teams" and r["start_ts"] == T0 + 600
    far = us_mlb("mlb-az-sd-2026-09-25", T0 + 600 + 50 * 60, long=("Arizona Diamondbacks", "az", "away"),
                 short=("San Diego Padres", "sd", "home"))
    r2 = CU.map_mlb(CU.parse_event(far, "mlb", "mlb"), games(tmp_path)["mlb"])
    assert r2["match"] == "unmatched" and r2["intl_key"] is None


def test_map_mlb_by_shared_game_id(tmp_path):
    write_intl(tmp_path, mlb=[intl_mlb("mlb-hou-oak-2026-09-25", "0xh", "Athletics", "Houston Astros", T0,
                                        pm_game_id=10079683)])
    us = us_mlb("mlb-hou-ath-2026-09-25", T0, long=("Houston Astros", "hou", "away"),
                short=("Athletics", "ath", "home"), game_id=10079683)
    r = CU.map_mlb(CU.parse_event(us, "mlb", "mlb"), games(tmp_path)["mlb"])
    assert r["match"] == "exact" and r["method"] == "game_id"


def test_map_mlb_orientation_conflict_and_slug_start_conflict_are_ambiguous(tmp_path):
    write_intl(tmp_path, mlb=[intl_mlb("mlb-cle-kc-2026-09-26", "0xc", "Kansas City Royals", "Cleveland Guardians",
                                        T0 + 3600)])
    wrong = us_mlb("mlb-cle-kc-2026-09-26", T0 + 3600, long=("Cleveland Guardians", "cle", "home"),
                   short=("Kansas City Royals", "kc", "away"))
    r = CU.map_mlb(CU.parse_event(wrong, "mlb", "mlb"), games(tmp_path)["mlb"])
    assert r["match"] == "ambiguous" and "disagrees with team identity" in r["reason"]
    assert r["markets"]["long_team"] is None
    late = us_mlb("mlb-cle-kc-2026-09-26", T0 + 3600 + 3 * 3600)
    r2 = CU.map_mlb(CU.parse_event(late, "mlb", "mlb"), games(tmp_path)["mlb"])
    assert r2["match"] == "ambiguous" and "start differs" in r2["reason"]
    other = us_mlb("mlb-cle-kc-2026-09-26", T0 + 3600, long=("Boston Red Sox", "bos", "away"))
    r3 = CU.map_mlb(CU.parse_event(other, "mlb", "mlb"), games(tmp_path)["mlb"])
    assert r3["match"] == "ambiguous" and "do not match" in r3["reason"]


def test_map_mlb_international_state_and_doubleheader(tmp_path):
    write_intl(tmp_path, mlb=[intl_mlb("mlb-cle-kc-2026-09-26", "0xc", "Kansas City Royals", "Cleveland Guardians",
                                        T0, match="ambiguous"),
                              intl_mlb("mlb-nym-atl-2026-09-26", "0x1", "Atlanta Braves", "New York Mets", T0),
                              intl_mlb("mlb-nym-atl-2026-09-26-g2", "0x2", "Atlanta Braves", "New York Mets",
                                       T0 + 1200)])
    r = CU.map_mlb(CU.parse_event(us_mlb("mlb-cle-kc-2026-09-26", T0), "mlb", "mlb"), games(tmp_path)["mlb"])
    assert r["match"] == "ambiguous" and r["reason"].startswith("international mapping is ambiguous")
    assert r["markets"]["long_team"] == "away"          # the US side itself is oriented
    dh = us_mlb("mlb-nym-atl-2026-09-26-x", T0 + 600, long=("New York Mets", "nym", "away"),
                short=("Atlanta Braves", "atl", "home"))
    r2 = CU.map_mlb(CU.parse_event(dh, "mlb", "mlb"), games(tmp_path)["mlb"])
    assert r2["match"] == "ambiguous" and "2 international games by teams" in r2["reason"]


def test_map_soccer_by_game_id_orients_by_team_identity_not_slug_order(tmp_path):
    write_intl(tmp_path, soccer=[intl_soccer("mex-tij-atl-2026-09-25", T0, game_id=90112734)])
    # US lists the away team first; its team.ordering says "home" on every long side
    us = us_soccer("lmx-atl-tij-2026-09-25", T0, a=("atl", "Atlas FC"), b=("tij", "Club Tijuana"), game_id=90112734)
    r = CU.map_soccer(CU.parse_event(us, "soccer", "lmx"), games(tmp_path)["soccer"])
    assert r["match"] == "exact" and r["method"] == "game_id", r["reason"]
    assert r["markets"]["home"]["slug"] == "atc-lmx-atl-tij-2026-09-25-tij"
    assert r["markets"]["away"]["slug"] == "atc-lmx-atl-tij-2026-09-25-atl"
    assert r["markets"]["draw"]["slug"] == "atc-lmx-atl-tij-2026-09-25-draw"
    assert r["markets"]["home"]["tick"] == 0.01 and r["teams"]["home"]["name"] == "Club Tijuana"
    assert r["intl_key"] == {"event_slug": "mex-tij-atl-2026-09-25", "espn_id": "401", "series": "mex-2025"}


def test_map_soccer_by_teams_and_kickoff_without_shared_ids(tmp_path):
    write_intl(tmp_path, soccer=[intl_soccer("conl-slv-mart-2026-09-25", T0, home=("slv", "El Salvador"),
                                             away=("mart", "Martinique"), espn=("El Salvador", "Martinique"))])
    us = us_soccer("cnl-slv-mtq-2026-09-25", T0 + 600, a=("slv", "El Salvador"), b=("mtq", "Martinique"))
    r = CU.map_soccer(CU.parse_event(us, "soccer", "cnl"), games(tmp_path)["soccer"])
    assert r["match"] == "exact" and r["method"] == "teams"
    assert r["markets"]["away"]["slug"].endswith("-mtq")
    far = us_soccer("cnl-slv-mtq-2026-09-25", T0 + 3600, a=("slv", "El Salvador"), b=("mtq", "Martinique"))
    assert CU.map_soccer(CU.parse_event(far, "soccer", "cnl"), games(tmp_path)["soccer"])["match"] == "unmatched"


def test_map_soccer_international_unmapped_or_wrong_teams(tmp_path):
    write_intl(tmp_path, soccer=[intl_soccer("ja2-bla-alb-2026-09-26", T0, home=("bla", "Blaublitz Akita"),
                                             away=("alb", "Albirex Niigata"), match="unmapped_league"),
                                 intl_soccer("mls-clt-chi-2026-09-26", T0)])
    us = us_soccer("j2-bla-alb-2026-09-26", T0, a=("bla", "Blaublitz Akita"), b=("alb", "Albirex Niigata"))
    r = CU.map_soccer(CU.parse_event(us, "soccer", "j2"), games(tmp_path)["soccer"])
    assert r["match"] == "unmatched" and r["reason"].startswith("international mapping is unmapped_league")
    assert r["intl_key"]["event_slug"] == "ja2-bla-alb-2026-09-26" and "home" in r["markets"]
    # same slug, other teams: the slug claim is not trusted
    us2 = us_soccer("mls-clt-chi-2026-09-26", T0, a=("clt", "Charlotte FC"), b=("chi", "Chicago Fire FC"))
    r2 = CU.map_soccer(CU.parse_event(us2, "soccer", "mls"), games(tmp_path)["soccer"])
    assert r2["match"] == "ambiguous" and "teams do not agree" in r2["reason"]
    assert set(r2["markets"]) == {"a", "b", "draw"}     # never keyed home/away without an orientation


# ---------------------------------------------------------------------------------- discovery

def _world(tmp_path):
    write_intl(tmp_path,
               mlb=[intl_mlb("mlb-cle-kc-2026-09-26", "0xc", "Kansas City Royals", "Cleveland Guardians", T0 + 3600,
                             game_pk=11),
                    intl_mlb("mlb-bal-nyy-2026-09-26", "0xb", "New York Yankees", "Baltimore Orioles", T0 + 3600,
                             game_pk=12),
                    intl_mlb("mlb-tb-phi-2026-09-26", "0xt", "Philadelphia Phillies", "Tampa Bay Rays", T0 + 3600,
                             game_pk=13)],
               soccer=[intl_soccer("mex-tij-atl-2026-09-26", T0 + 7200, game_id=5)])
    return {"mlb": [us_mlb("mlb-cle-kc-2026-09-26", T0 + 3600),
                    us_mlb("mlb-tb-phi-2026-09-26", T0 + 3600, long=("Tampa Bay Rays", "tb", "away"),
                           short=("Philadelphia Phillies", "phi", "home"), moneyline=False),
                    us_mlb("mlb-sf-lad-2026-09-27", T0 + 20 * 3600)],             # outside the window
            "lmx": [us_soccer("lmx-tij-atl-2026-09-26", T0 + 7200, game_id=5)],
            "mls": [us_soccer("mls-sea-min-2026-09-26", T0 + 3000, a=("sea", "Seattle Sounders FC"),
                              b=("min", "Minnesota United FC"))]}


def test_discover_writes_mapping_coverage_and_wanted_markets(tmp_path):
    get = gateway(_world(tmp_path))
    c = cap(tmp_path, get)
    res = c.discover(T0)
    by = {r["key"]: r for r in c.map_sink.rows}
    assert by["mlb-cle-kc-2026-09-26"]["match"] == "exact"
    assert by["lmx-tij-atl-2026-09-26"]["match"] == "exact"
    assert by["mls-sea-min-2026-09-26"]["match"] == "unmatched"          # no international game
    assert by["mlb-tb-phi-2026-09-26"]["match"] == "no_us_market"        # US event without a moneyline
    assert by["intl:mlb:0xb"]["match"] == "no_us_market" and by["intl:mlb:0xb"]["us_event_slug"] is None
    assert by["intl:mlb:0xb"]["intl_key"]["game_pk"] == 12
    assert "intl:mlb:0xc" not in by and "mlb-sf-lad-2026-09-27" not in by
    assert "intl:mlb:0xt" in by                    # the US event without a market claims nothing
    assert all(r["recv_ms"] == int(T0 * 1000) for r in c.map_sink.rows)
    assert set(res["want"]) == {"aec-mlb-cle-kc-2026-09-26"} | {f"atc-lmx-tij-atl-2026-09-26-{x}"
                                                                for x in ("tij", "atl", "draw")}
    assert res["want"]["aec-mlb-cle-kc-2026-09-26"] == T0 + 3600 + CU.MLB_TTL_S
    leagues = sorted({u.split("/")[5] for u, _ in get.calls if "/events" in u})
    assert leagues == ["epl", "lmx", "mlb", "mls"]            # every soccer league of /v2/sports
    n = len(c.map_sink.rows)
    c.discover(T0 + 60)                                       # nothing changed: nothing written
    assert len(c.map_sink.rows) == n


def test_discover_pages_through_league_events(tmp_path, monkeypatch):
    monkeypatch.setattr(CU, "PAGE", 2)
    evs = {"mlb": [us_mlb(f"mlb-cle-kc-2026-09-2{i}", T0 + 3600 + i) for i in range(5)]}
    get = gateway(evs, soccer_leagues=())
    c = cap(tmp_path, get)
    c.discover(T0)
    offsets = [p["offset"] for u, p in get.calls if u.endswith("/mlb/events")]
    assert offsets == [0, 2, 4] and len(c.map_sink.rows) == 5


class Raw(dict):
    """A raw gateway event that can be weakly referenced (to see what fetch() still holds)."""


def test_fetch_parses_each_page_as_it_arrives_and_keeps_only_parsed_window_events(tmp_path, monkeypatch):
    """Review finding: fetch() held every league's raw events until the whole pass ended."""
    monkeypatch.setattr(CU, "PAGE", 2)
    evs = {"mlb": [us_mlb(f"mlb-cle-kc-2026-09-2{i}", T0 + 3600 + i) for i in range(5)]
           + [us_mlb("mlb-sf-lad-2026-09-24", T0 - 86400, ended=True)],
           "mls": [us_soccer(f"mls-sea-min-2026-09-2{i}", T0 + 3000 + i, a=("sea", "Seattle Sounders FC"),
                             b=("min", "Minnesota United FC")) for i in range(3)]
           + [us_soccer("mls-clt-chi-2026-09-28", T0 + 30 * 3600)]}
    base = gateway(evs, soccer_leagues=("mls", "epl"))
    refs, held = [], []

    def alive():
        return sum(r() is not None for r in refs)

    def get(url, params=None, **kw):
        j = base(url, params, **kw)
        if "/events" in url:
            held.append(alive())               # raw events still referenced when the next page is fetched
            j = {"events": [Raw(e) for e in j["events"]]}
            refs.extend(weakref.ref(e) for e in j["events"])
        return j
    c = cap(tmp_path, get)
    out = c.fetch(T0)
    assert len(held) == 4 + 3 + 1 and max(held) <= CU.PAGE and alive() == 0     # mlb 4 pages, mls 3, epl 1
    assert sorted(g["us_event_slug"] for g in out["mlb"]["events"]) == [f"mlb-cle-kc-2026-09-2{i}" for i in range(5)]
    assert all(g["market"]["slug"].startswith("aec-") for g in out["mlb"]["events"])
    assert out["mlb"]["ended"] == ["aec-mlb-sf-lad-2026-09-24"]             # ended events outside the window too
    assert sorted(g["us_event_slug"] for g in out["soccer"]["events"]) == [f"mls-sea-min-2026-09-2{i}" for i in range(3)]
    assert out["mlb"]["ok"] and out["soccer"]["ok"]


def test_fetch_drops_a_league_that_fails_on_a_later_page(tmp_path, monkeypatch):
    monkeypatch.setattr(CU, "PAGE", 2)
    evs = {"mls": [us_soccer(f"mls-sea-min-2026-09-2{i}", T0 + 3000 + i, a=("sea", "Seattle Sounders FC"),
                             b=("min", "Minnesota United FC")) for i in range(3)]}
    for e in evs["mls"]:
        e["ended"] = True
    base = gateway(evs, soccer_leagues=("mls",))

    def get(url, params=None, **kw):
        if url.endswith("/mls/events") and params["offset"] == 2:
            raise RuntimeError("HTTP 500")
        return base(url, params, **kw)
    out = cap(tmp_path, get).fetch(T0)
    assert out["soccer"] == {"events": [], "ended": [], "ok": False, "failed": ["mls"]}


def test_discover_uses_only_international_records_received_by_decision_time(tmp_path):
    ev = _world(tmp_path)
    write_intl(tmp_path, soccer=[intl_soccer("mls-sea-min-2026-09-26", T0 + 3000, home=("sea", "Seattle Sounders FC"),
                                             away=("min", "Minnesota United FC"), recv=T0 + 5)])
    c = cap(tmp_path, gateway(ev))
    c.discover(T0)
    by = {r["key"]: r for r in c.map_sink.rows}
    assert by["mls-sea-min-2026-09-26"]["match"] == "unmatched"          # written 5 s after the decision
    c.map_sink.t = T0 + 60
    c.discover(T0 + 60)
    assert {r["key"]: r for r in c.map_sink.rows}["mls-sea-min-2026-09-26"]["match"] == "exact"


def test_mapping_freezes_at_start_and_only_venue_fields_follow(tmp_path):
    ev = _world(tmp_path)
    c = cap(tmp_path, gateway(ev))
    c.discover(T0)
    first = {r["key"]: r for r in c.map_sink.rows}["mlb-cle-kc-2026-09-26"]
    # after the start the international file names another game and the US tick changes
    write_intl(tmp_path, mlb=[intl_mlb("mlb-cle-kc-2026-09-26", "0xc", "Kansas City Royals", "Cleveland Guardians",
                                        T0 + 3600, game_pk=99, recv=T0 + 3500)])
    ev["mlb"][0] = us_mlb("mlb-cle-kc-2026-09-26", T0 + 3600, tick=0.01)
    n = len(c.map_sink.rows)
    c.map_sink.t = T0 + 4000
    c.discover(T0 + 4000)
    new = [r for r in c.map_sink.rows[n:] if r["key"] == "mlb-cle-kc-2026-09-26"]
    assert len(new) == 1 and new[0]["intl_key"]["game_pk"] == 11 and new[0]["markets"]["tick"] == 0.01
    assert {k: v for k, v in new[0].items() if k not in ("recv_ms", "markets")} == \
        {k: v for k, v in first.items() if k not in ("recv_ms", "markets")}
    n = len(c.map_sink.rows)
    c.discover(T0 + 4100)
    assert all(r["key"] != "mlb-cle-kc-2026-09-26" for r in c.map_sink.rows[n:])


def test_first_seen_after_start_is_unmatched_and_not_subscribed(tmp_path):
    ev = _world(tmp_path)
    c = cap(tmp_path, gateway(ev))
    c.map_sink.t = T0 + 3700
    res = c.discover(T0 + 3700)
    r = {x["key"]: x for x in c.map_sink.rows}["mlb-cle-kc-2026-09-26"]
    assert r["match"] == "unmatched" and r["reason"] == CU.FIRST_AFTER_START
    assert "aec-mlb-cle-kc-2026-09-26" not in res["want"]
    assert "intl:mlb:0xb" not in {x["key"] for x in c.map_sink.rows}  # coverage is never written late


def test_failed_fetch_never_claims_no_us_market(tmp_path):
    ev = _world(tmp_path)
    c = cap(tmp_path, gateway(ev, fail=("mlb",)))
    c.discover(T0)
    keys = {r["key"] for r in c.map_sink.rows}
    assert not any(k.startswith("intl:mlb:") or k.startswith("mlb-") for k in keys)
    assert "lmx-tij-atl-2026-09-26" in keys                   # the other sport still maps


def test_partial_soccer_league_list_never_claims_no_us_market(tmp_path):
    write_intl(tmp_path, soccer=[intl_soccer("mex-tij-atl-2026-09-26", T0 + 7200, game_id=5)])
    base = gateway({"lmx": [us_soccer("lmx-tij-atl-2026-09-26", T0 + 7200, game_id=5)]})

    def get(url, params=None, **kw):
        if url.endswith("/v2/sports") or url.endswith("/v2/leagues"):
            raise RuntimeError("HTTP 503")
        return base(url, params, **kw)
    c = cap(tmp_path, get)
    res = c.discover(T0)
    leagues = sorted({u.split("/")[5] for u, _ in base.calls})
    assert leagues == sorted(set(CU.CORE_SOCCER) | {"mlb"})    # core list only: lmx not queried
    assert not any(r["key"].startswith("intl:soccer:") for r in c.map_sink.rows)
    assert res["failed"]["soccer"] == ["<league list>"]


def test_one_international_game_claimed_twice_is_ambiguous(tmp_path):
    ev = _world(tmp_path)
    ev["mlb"].append(us_mlb("mlb-cle-kc-2026-09-26-x", T0 + 3600 + 60))      # same teams, same start
    c = cap(tmp_path, gateway(ev))
    res = c.discover(T0)
    by = {r["key"]: r for r in c.map_sink.rows}
    assert by["mlb-cle-kc-2026-09-26"]["match"] == "ambiguous" == by["mlb-cle-kc-2026-09-26-x"]["match"]
    assert "claimed by 2 US events" in by["mlb-cle-kc-2026-09-26"]["reason"]
    assert not any(s.startswith("aec-mlb") for s in res["want"])


def test_restart_seed_keeps_frozen_mapping_and_subscriptions(tmp_path):
    ev = _world(tmp_path)
    c = cap(tmp_path, gateway(ev))
    c.discover(T0)
    # restart after the start; the international capture has meanwhile re-mapped the game
    write_intl(tmp_path, mlb=[intl_mlb("mlb-cle-kc-2026-09-26", "0xc", "Kansas City Royals", "Cleveland Guardians",
                                        T0 + 3600, game_pk=None, match="ambiguous", recv=T0 + 3650)])
    c2 = cap(tmp_path, gateway(ev))
    c2.map_sink = c.map_sink
    c2.seed(now=T0 + 3700)
    assert "aec-mlb-cle-kc-2026-09-26" in c2.live_slugs(T0 + 3700)
    c2.last_day = DAY                                          # no rollover rewrite
    n = len(c.map_sink.rows)
    c.map_sink.t = T0 + 3700
    res = c2.discover(T0 + 3700)
    assert all(r["key"] != "mlb-cle-kc-2026-09-26" for r in c.map_sink.rows[n:])   # unchanged, frozen
    assert c2.records["mlb-cle-kc-2026-09-26"]["match"] == "exact"
    assert "aec-mlb-cle-kc-2026-09-26" in res["want"]


def test_claim_after_no_us_market_rewrites_the_us_record(tmp_path):
    ev = _world(tmp_path)
    c = cap(tmp_path, gateway(ev))
    c.discover(T0)
    kc = ev["mlb"].pop(0)
    c.discover(T0 + 60)                                       # US event gone: coverage says no US market
    assert c.map_sink.rows[-1]["key"] == "intl:mlb:0xc" and c.map_sink.rows[-1]["match"] == "no_us_market"
    ev["mlb"].insert(0, kc)
    n = len(c.map_sink.rows)
    c.discover(T0 + 120)                                      # identical to its earlier record, still re-written
    assert [r["key"] for r in c.map_sink.rows[n:]] == ["mlb-cle-kc-2026-09-26"]


def test_rollover_rewrites_current_records(tmp_path):
    ev = _world(tmp_path)
    c = cap(tmp_path, gateway(ev))
    c.discover(T0)
    n = len(c.map_sink.rows)
    c.last_day = "2026-09-25"
    c.discover(T0 + 60)
    assert len(c.map_sink.rows) - n == n


def test_wanted_markets_expire_by_ttl_or_after_the_event_ends(tmp_path):
    ev = _world(tmp_path)
    c = cap(tmp_path, gateway(ev))
    c.apply(c.discover(T0), T0)
    assert "aec-mlb-cle-kc-2026-09-26" in c.live_slugs(T0 + 3600 + CU.MLB_TTL_S - 1)
    assert "aec-mlb-cle-kc-2026-09-26" not in c.live_slugs(T0 + 3600 + CU.MLB_TTL_S)
    assert "aec-mlb-cle-kc-2026-09-26" not in c._want(T0 + 3600 + CU.MLB_TTL_S)
    tij = "atc-lmx-tij-atl-2026-09-26-tij"
    assert c._want(T0)[tij] == T0 + 7200 + CU.SOCCER_TTL_S
    ev["lmx"][0]["ended"] = True
    res = c.discover(T0 + 9000)
    assert res["want"][tij] == T0 + 9000 + CU.ENDED_GRACE_S


# ---------------------------------------------------------------------------------- websocket

class FakeWS:
    """Scripted websocket: strings are received, callables run, exceptions raise."""

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
                continue
            return x
        await asyncio.sleep(3600)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class PongWS(FakeWS):
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


def run_ws(monkeypatch, c, scripts, seconds, make=None, headers=None):
    conns, signed = [], []

    def ws_headers():
        signed.append(time.time())
        if headers is not None:
            return headers()
        return {"X-PM-Access-Key": "k", "X-PM-Timestamp": str(len(signed)), "X-PM-Signature": "s"}

    def connect(url, **kw):
        assert url == CU.U.WS_MARKETS and kw["additional_headers"]["X-PM-Timestamp"] == str(len(signed))
        ws = make() if make else FakeWS(scripts.pop(0) if scripts else [])
        conns.append(ws)
        return ws
    monkeypatch.setattr(CU.U, "ws_headers", ws_headers)
    monkeypatch.setattr(CU.websockets, "connect", connect)

    async def go():
        try:
            await asyncio.wait_for(c.ws_loop(time.time() + 60), seconds)
        except asyncio.TimeoutError:
            pass
    asyncio.run(go())
    return conns, signed


def ws_cap(slugs):
    c = CU.UsCapture(ListSink(), ListSink(), ListSink(), get=None)
    c.want = {s: INF for s in slugs}
    return c


class ExchangeWS(PongWS):
    """The markets websocket's subscription rules as verified live 2026-09-26: at most 10
    subscriptions per connection, MARKET_DATA and TRADE combined; a slug already subscribed with
    the same type is refused; either refusal rejects the whole request, which then does not count;
    unsubscribe is acknowledged; a MARKET_DATA subscription sends each market's snapshot at once;
    every PING is answered with PONG."""
    LIMIT = 10

    def __init__(self, on_ping=None, ghost=(), fail=None):
        super().__init__()
        self.active, self.errors, self.peak = {}, [], 0
        self.on_ping, self.ghost, self.fail = on_ping, list(ghost), fail

    async def __aenter__(self):
        await super().__aenter__()
        for i, (typ, slug) in enumerate(self.ghost):     # subscribed without the capture's knowledge
            self.active[f"ghost-{i}"] = (typ, [slug])
        return self

    def streamed(self, typ):
        return {s for t, ss in self.active.values() if t == typ for s in ss}

    def reply(self, obj):
        self.q.put_nowait(json.dumps(obj))

    async def send(self, m):
        self.sent.append(m)
        if m == "PING":
            if self.on_ping:
                self.on_ping(self)
            self.q.put_nowait("PONG")
            return
        d = json.loads(m)
        if "unsubscribe" in d:
            rid = d["unsubscribe"]["requestId"]
            self.active.pop(rid, None)
            self.reply({"requestId": rid, "unsubscribed": True})
            return
        sub = d["subscribe"]
        rid, typ, slugs = sub["requestId"], sub["subscriptionType"], sub["marketSlugs"]
        dup = sorted(set(slugs) & self.streamed(typ))
        err = ((self.fail(sub) if self.fail else None)
               or ("max subscriptions per connection reached" if len(self.active) >= self.LIMIT else None)
               or (f"slug already subscribed: {dup[0]}" if dup else None))
        if err:
            self.errors.append((rid, err))
            self.reply({"requestId": rid, "error": err})
            return
        self.active[rid] = (typ, list(slugs))
        self.peak = max(self.peak, len(self.active))
        if typ == CU.MD:
            for x in slugs:
                self.reply({"requestId": rid, "subscriptionType": typ, "marketData": {
                    "marketSlug": x, "bids": [], "offers": [], "state": "MARKET_STATE_OPEN"}})


def believed(rows, typ):
    """Slugs the capture's own markers say are subscribed (sub minus unsub / sub_error)."""
    act = {}
    for r in rows:
        k = r.get("conn")
        if k == "sub":
            act[r["request_id"]] = (r["type"], r["slugs"])
        elif k in ("unsub", "sub_error"):
            act.pop(r["request_id"], None)
        elif k == "closed":
            act.clear()
    return {s for t, ss in act.values() if t == typ for s in ss}


class TimeSink(ListSink):
    def write(self, obj, recv_ms=None):
        super().write({**obj, "t": time.time()}, recv_ms)


MD_MSG = json.dumps({"requestId": "md-1", "subscriptionType": CU.MD, "marketData": {
    "marketSlug": "aec-a", "bids": [{"px": {"value": "0.40"}, "qty": "10"}], "offers": [],
    "state": "MARKET_STATE_OPEN"}})
TR_MSG = json.dumps({"requestId": "tr-1", "subscriptionType": CU.TR, "trade": {
    "marketSlug": "aec-a", "price": {"value": "0.41"}, "quantity": {"value": "3"}}})
ERR_MSG = json.dumps({"requestId": "zz-9", "error": "unknown request"})      # names no request of ours


def test_ws_subscribes_marks_connection_heartbeats_and_routes_messages(monkeypatch):
    c = ws_cap(["aec-a", "aec-b"])
    conns, signed = run_ws(monkeypatch, c, [["PONG", MD_MSG, TR_MSG, ERR_MSG, "PONG", ConnectionError("gone")]], 0.5)
    subs = [json.loads(m)["subscribe"] for m in conns[0].sent if m.startswith('{"subscribe"')]
    assert subs == [{"requestId": "md-1", "subscriptionType": CU.MD, "marketSlugs": ["aec-a", "aec-b"],
                     "responsesDebounced": False},
                    {"requestId": "tr-1", "subscriptionType": CU.TR, "marketSlugs": ["aec-a", "aec-b"]}]
    rows = c.book.rows
    kinds = [r.get("conn") or "msg" for r in rows]
    assert kinds[:8] == ["open", "sub", "sub", "heartbeat", "msg", "msg", "heartbeat", "closed"]
    assert rows[0]["n_markets"] == 2 and rows[1]["type"] == "MARKET_DATA" and rows[2]["type"] == "TRADE"
    assert rows[1]["slugs"] == ["aec-a", "aec-b"] and rows[3]["n_markets"] == 2
    assert rows[4]["msg"] == json.loads(MD_MSG) and rows[5]["msg"] == json.loads(ERR_MSG)   # raw
    assert [r["msg"] for r in c.trade.rows] == [json.loads(TR_MSG)]
    assert len(conns) >= 1 and "PING" in conns[0].sent


def test_ws_splits_subscriptions_at_100_markets(monkeypatch):
    c = ws_cap([f"m{i:03d}" for i in range(250)])
    conns, _ = run_ws(monkeypatch, c, [[]], 0.3)
    subs = [json.loads(m)["subscribe"] for m in conns[0].sent if m.startswith('{"subscribe"')]
    assert [len(s["marketSlugs"]) for s in subs] == [100, 100, 100, 100, 50, 50]
    assert len({s["requestId"] for s in subs}) == 6 and c.book.rows[0]["n_markets"] == 250


def test_ws_adds_markets_live_and_unsubscribes_expired_requests(monkeypatch):
    monkeypatch.setattr(CU, "CHECK_S", 0.0)
    monkeypatch.setattr(CU, "UNSUB_EVERY_S", 0.0)
    c = ws_cap(["aec-a"])

    def add():
        c.want = {"aec-a": INF, "aec-b": INF}

    def drop_a():
        c.want = {"aec-b": INF}
    conns, _ = run_ws(monkeypatch, c, [["PONG", add, "PONG", "PONG", drop_a, "PONG", "PONG"]], 0.5)
    assert len(conns) == 1                                    # never reconnected for a new market
    sent = [json.loads(m) for m in conns[0].sent if m.startswith("{")]
    assert [x["subscribe"]["marketSlugs"] for x in sent if "subscribe" in x] == [["aec-a"], ["aec-a"], ["aec-b"],
                                                                                   ["aec-b"]]
    assert [x["unsubscribe"]["requestId"] for x in sent if "unsubscribe" in x] == ["md-1", "tr-1"]
    marks = [(r["conn"], r.get("request_id"), r["n_markets"]) for r in c.book.rows if r.get("conn") in ("sub", "unsub")]
    assert marks == [("sub", "md-1", 1), ("sub", "tr-1", 1), ("sub", "md-2", 2), ("sub", "tr-2", 2),
                     ("unsub", "md-1", 1), ("unsub", "tr-1", 1)]


def test_ws_reconnects_with_fresh_signature_and_backoff(monkeypatch):
    monkeypatch.setattr(CU, "RECONNECT_MIN_S", 0.05)
    monkeypatch.setattr(CU, "DEAD_S", 0.1)
    monkeypatch.setattr(CU, "PING_S", 0.05)                  # the fake never answers PING
    c = ws_cap(["aec-a"])
    conns, signed = run_ws(monkeypatch, c, [[ConnectionError("x")], ["wait", "wait", "wait"], []], 1.2)
    assert len(conns) >= 3 and len(signed) == len(conns)      # every connection signed afresh
    gaps = [b - a for a, b in zip(signed, signed[1:])]
    assert gaps[1] > gaps[0] * 1.5                            # exponential backoff
    kinds = [r.get("conn") for r in c.book.rows]
    assert kinds.count("open") == kinds.count("closed") >= 2  # a silent (dead) connection is rebuilt


def test_ws_heartbeat_every_pong(monkeypatch):
    monkeypatch.setattr(CU, "PING_S", 0.05)
    c = ws_cap(["aec-a"])
    conns, _ = run_ws(monkeypatch, c, [], 0.5, make=PongWS)
    pings = conns[0].sent.count("PING")
    beats = [r for r in c.book.rows if r.get("conn") == "heartbeat"]
    assert 6 <= pings <= 12 and pings - 1 <= len(beats) <= pings


def test_ws_default_heartbeat_well_inside_two_seconds():
    assert CU.PING_S <= 1.0 and CU.DEBOUNCED is False and CU.MAX_SLUGS == 100


def test_ws_missing_credentials_never_raises(monkeypatch):
    monkeypatch.setattr(CU, "RECONNECT_MIN_S", 0.01)
    c = ws_cap(["aec-a"])

    def no_creds():
        raise RuntimeError("Polymarket US credentials missing from .env")

    async def go():
        await c.ws_loop(time.time() + 0.3)                    # returns normally at stop
    monkeypatch.setattr(CU.U, "ws_headers", no_creds)
    asyncio.run(go())
    assert c.book.rows == []


def test_ws_closes_when_nothing_is_wanted(monkeypatch):
    monkeypatch.setattr(CU, "CHECK_S", 0.0)
    monkeypatch.setattr(CU, "UNSUB_EVERY_S", 0.0)
    c = ws_cap(["aec-a"])

    def drop():
        c.want = {}
    conns, _ = run_ws(monkeypatch, c, [["PONG", drop, "PONG", "PONG"]], 0.4)
    assert [r.get("conn") for r in c.book.rows if r.get("conn") in ("open", "closed", "unsub")] == \
        ["open", "unsub", "unsub", "closed"] and len(conns) == 1


def test_ws_stays_under_the_per_connection_cap_and_streams_every_wanted_market(monkeypatch):
    """Review finding (critical): the exchange allows 10 subscriptions per connection; the 5th
    live batch of newly wanted markets was refused while the capture believed it subscribed."""
    monkeypatch.setattr(CU, "CHECK_S", 0.0)
    monkeypatch.setattr(CU, "PING_S", 0.02)
    c = ws_cap(["aec-g00"])
    n = [0]

    def add(ws):                           # one discovery pass per PING, each adding one newly exact game
        if n[0] < 12:
            n[0] += 1
            c.want = {**c.want, f"aec-g{n[0]:02d}": INF}
    conns, _ = run_ws(monkeypatch, c, [], 0.8, make=lambda: ExchangeWS(on_ping=add))
    ws, wanted = conns[0], {f"aec-g{i:02d}" for i in range(13)}
    assert len(conns) == 1 and n[0] == 12 and ws.errors == [] and ws.peak <= 10
    assert ws.streamed(CU.MD) == ws.streamed(CU.TR) == wanted                       # what the exchange streams
    rows = c.book.rows[:[r.get("conn") for r in c.book.rows].index("closed")]
    assert believed(rows, "MARKET_DATA") == believed(rows, "TRADE") == wanted       # what the capture records
    assert {r["msg"]["marketData"]["marketSlug"] for r in rows if "marketData" in (r.get("msg") or {})} == wanted
    unsubs = [r for r in rows if r.get("conn") == "unsub"]
    assert len(unsubs) == 20 and {s for r in unsubs for s in r["slugs"]} <= wanted     # two re-subscriptions
    assert [r["n_markets"] for r in rows if r.get("conn") == "sub"][-1] == 13


def test_ws_refused_request_is_dropped_marked_and_retried(monkeypatch):
    """Review finding: a refused request stayed recorded as subscribed and was never re-requested."""
    monkeypatch.setattr(CU, "CHECK_S", 0.0)
    monkeypatch.setattr(CU, "PING_S", 0.02)
    monkeypatch.setattr(CU, "RETRY_S", 0.2)
    c = ws_cap(["aec-a", "aec-b"])
    c.book = TimeSink()
    conns, _ = run_ws(monkeypatch, c, [], 0.6, make=lambda: ExchangeWS(
        fail=lambda sub: "internal error" if sub["requestId"] == "md-1" else None))
    ws = conns[0]
    subs = [json.loads(m)["subscribe"] for m in ws.sent if m.startswith('{"subscribe"')]
    assert [(x["requestId"], x["marketSlugs"]) for x in subs] == [
        ("md-1", ["aec-a", "aec-b"]), ("tr-1", ["aec-a", "aec-b"]), ("md-2", ["aec-a", "aec-b"])]   # TRADE kept
    assert len(conns) == 1 and ws.streamed(CU.MD) == ws.streamed(CU.TR) == {"aec-a", "aec-b"}
    marks = [(r["conn"], r["request_id"], r["n_markets"]) for r in c.book.rows
             if r.get("conn") in ("sub", "unsub", "sub_error")]
    assert marks == [("sub", "md-1", 2), ("sub", "tr-1", 2), ("sub_error", "md-1", 0), ("sub", "md-2", 2)]
    err = next(r for r in c.book.rows if r.get("conn") == "sub_error")
    assert err["type"] == "MARKET_DATA" and err["slugs"] == ["aec-a", "aec-b"] and err["error"] == "internal error"
    resub = next(r for r in c.book.rows if r.get("request_id") == "md-2")
    assert resub["t"] - err["t"] >= CU.RETRY_S                                  # backoff, no fast loop


@pytest.mark.parametrize("ghost, err", [
    ([(CU.MD, "aec-a")], "slug already subscribed: aec-a"),
    ([(CU.TR, f"x{i}") for i in range(9)], "max subscriptions per connection reached")])
def test_ws_cap_or_duplicate_refusal_resubscribes_on_a_new_connection(monkeypatch, ghost, err):
    """The exchange's subscriptions differ from the capture's: rebuild the connection."""
    monkeypatch.setattr(CU, "RECONNECT_MIN_S", 0.05)
    c = ws_cap(["aec-a", "aec-b"])
    fakes = [ExchangeWS(ghost=ghost)]
    conns, signed = run_ws(monkeypatch, c, [], 0.6, make=lambda: fakes.pop(0) if fakes else ExchangeWS())
    assert [e for _, e in conns[0].errors] == [err] and len(conns) == 2 == len(signed)
    assert conns[1].errors == [] and conns[1].streamed(CU.MD) == conns[1].streamed(CU.TR) == {"aec-a", "aec-b"}
    kinds = [r["conn"] for r in c.book.rows if r.get("conn") not in (None, "heartbeat")]
    assert kinds[:6] == ["open", "sub", "sub", "sub_error", "closed", "open"]
    e = next(r for r in c.book.rows if r.get("conn") == "sub_error")
    assert e["slugs"] == ["aec-a", "aec-b"] and e["error"] == err
    assert e["n_markets"] == (0 if e["type"] == "MARKET_DATA" else 2)


def test_ws_keeps_a_request_until_every_one_of_its_markets_expired(monkeypatch):
    """Review finding: with one slug per request, 'all expired' and 'any expired' looked alike."""
    monkeypatch.setattr(CU, "CHECK_S", 0.0)
    monkeypatch.setattr(CU, "UNSUB_EVERY_S", 0.0)
    monkeypatch.setattr(CU, "PING_S", 0.02)
    c = ws_cap(["aec-a", "aec-b"])
    step, mid = [0], {}

    def tick(ws):
        step[0] += 1
        if step[0] == 3:
            c.want = {"aec-b": INF}                               # aec-a expired, aec-b still live
        if step[0] == 8:
            mid.update(kinds=[r.get("conn") for r in c.book.rows], active=dict(ws.active))
            c.want = {}                                            # aec-b expired too
    conns, _ = run_ws(monkeypatch, c, [], 0.5, make=lambda: ExchangeWS(on_ping=tick))
    assert "unsub" not in mid["kinds"]
    assert mid["active"] == {"md-1": (CU.MD, ["aec-a", "aec-b"]), "tr-1": (CU.TR, ["aec-a", "aec-b"])}
    sent = [json.loads(m) for m in conns[0].sent if m.startswith("{")]
    assert [x["unsubscribe"]["requestId"] for x in sent if "unsubscribe" in x] == ["md-1", "tr-1"]
    assert conns[0].active == {}


def test_ws_silent_drop_is_closed_inside_the_freshness_window(monkeypatch):
    """Review finding: DEAD_S = 15 s let a silently dead connection look open for 15 s."""
    monkeypatch.setattr(CU, "PING_S", 0.05)
    monkeypatch.setattr(CU, "DEAD_S", 0.3)
    monkeypatch.setattr(CU, "RECONNECT_MIN_S", 5.0)
    c = ws_cap(["aec-a"])
    c.book = TimeSink()

    class HalfOpen(ExchangeWS):                                    # answers 4 PINGs, then silence
        async def send(self, m):
            if m == "PING" and self.sent.count("PING") >= 4:
                self.sent.append(m)
                return
            await super().send(m)
    run_ws(monkeypatch, c, [], 0.9, make=HalfOpen)
    rows = c.book.rows
    last = max(r["t"] for r in rows if r.get("conn") in ("heartbeat", None))
    closed = next(r["t"] for r in rows if r.get("conn") == "closed")
    assert CU.DEAD_S < closed - last <= CU.DEAD_S + CU.PING_S + 0.1 + 0.1


def test_ws_default_dead_connection_detected_inside_book_freshness():
    # the closed marker lands at most DEAD_S + PING_S + 0.1 s (re-read) after the last message
    assert CU.DEAD_S + CU.PING_S + 0.1 < V.FRESH_MS / 1000 and CU.DEAD_S >= 2 * CU.PING_S
    assert CU.MAX_SUBS == 10 and CU.RETRY_S >= 30


def test_ws_event_loop_stall_is_not_mistaken_for_a_dead_connection(monkeypatch):
    monkeypatch.setattr(CU, "PING_S", 0.05)
    monkeypatch.setattr(CU, "DEAD_S", 0.2)
    c = ws_cap(["aec-a"])

    class Stalled(PongWS):                         # never answers PING; a message arrives during a stall
        async def send(self, m):
            self.sent.append(m)

        async def __aenter__(self):
            await super().__aenter__()
            loop = asyncio.get_running_loop()

            def stall():
                loop.call_soon(self.q.put_nowait, "PONG")
                time.sleep(0.4)                    # the event loop is blocked past DEAD_S
            loop.call_later(0.1, stall)
            return self
    run_ws(monkeypatch, c, [], 0.9, make=Stalled)
    kinds = [r.get("conn") for r in c.book.rows]
    assert "heartbeat" in kinds and kinds.index("heartbeat") < kinds.index("closed")


def test_ws_files_have_monotonic_recv_ms_with_record_sink(tmp_path, monkeypatch):
    c = CU.UsCapture(R._Sink("us_map", tmp_path), R._Sink("us_book", tmp_path), R._Sink("us_trade", tmp_path))
    c.want = {"aec-a": INF}
    run_ws(monkeypatch, c, [["PONG", MD_MSG, TR_MSG, "PONG", ConnectionError("x")]], 0.3)
    for name in ("us_book", "us_trade"):
        rows = [json.loads(x) for f in tmp_path.glob(f"*/{name}.jsonl") for x in open(f)]
        assert rows and [r["recv_ms"] for r in rows] == sorted(r["recv_ms"] for r in rows)
        assert all(list(r)[0] == "recv_ms" for r in rows)


def test_run_survives_a_failing_loop(monkeypatch):
    c = ws_cap([])
    calls = []

    async def bad(stop):
        calls.append(1)
        raise ValueError("boom")

    async def fine(stop):
        await asyncio.sleep(0)
    monkeypatch.setattr(c, "discover_loop", bad)
    monkeypatch.setattr(c, "ws_loop", fine)
    orig = asyncio.sleep

    async def fast(s):
        await orig(0)
    monkeypatch.setattr(CU.asyncio, "sleep", fast)
    asyncio.run(asyncio.wait_for(c.run(time.time() + 0.2), 2))
    assert len(calls) >= 2                                    # restarted, never raised


def test_discover_loop_survives_discovery_errors(monkeypatch):
    c = ws_cap([])
    n = []

    def boom(now):
        n.append(now)
        raise RuntimeError("gateway down")
    monkeypatch.setattr(c, "discover", boom)
    monkeypatch.setattr(CU, "DISCOVER_S", 0.02)
    asyncio.run(c.discover_loop(time.time() + 0.2))
    assert len(n) >= 3


# ---------------------------------------------------------------------------------- record.py

def test_record_run_starts_us_capture_with_its_own_sinks(monkeypatch):
    seen = {}

    async def idle(*a, **k):
        await asyncio.sleep(0)

    class FakeUS:
        def __init__(self, m, b, t, *a, **k):
            seen["sinks"] = (m.name, b.name, t.name)

        def seed(self):
            seen["seed"] = True

        async def run(self, stop):
            seen["run"] = stop

    for name in ("_refresh", "_clob", "_sports", "_mlb"):
        monkeypatch.setattr(R, name, idle)
    monkeypatch.setattr(R.capture_soccer.SoccerCapture, "seed", lambda self: None)
    monkeypatch.setattr(R.capture_soccer.SoccerCapture, "discover_loop", lambda self, stop: idle())
    monkeypatch.setattr(R.capture_soccer.SoccerCapture, "espn_loop", lambda self, stop: idle())
    monkeypatch.setattr(R, "seed_mlb_map", lambda *a, **k: None)
    monkeypatch.setattr(R.capture_us, "UsCapture", FakeUS)
    asyncio.run(R._run(0.0001, 4.0))
    assert seen["sinks"] == ("us_map", "us_book", "us_trade") and seen["seed"] and "run" in seen


def test_us_api_is_the_only_signer_and_capture_never_posts():
    src = Path(CU.__file__).read_text()
    assert "requests.post" not in src and "preview" not in src.lower().replace("preview-only", "")
    assert "signed_headers" not in src and "ws_headers()" in src
