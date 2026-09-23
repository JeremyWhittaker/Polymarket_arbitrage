"""Ledger API checks; browser gate is opt-in: PMSPORTS_BROWSER_QA=1 pytest ..."""
import asyncio
import json
import os
import socket
import threading
import time

import pytest
import pandas as pd
from fastapi.testclient import TestClient

from pmsports.webapp import server

client = TestClient(server.app)
COLUMNS = ["id", "period", "date", "sport", "league", "event", "market", "side", "entry_ts",
           "entry_price", "stake_usd", "fee_usd", "exit_kind", "exit_ts", "exit_price",
           "payout", "pnl_usd", "roi", "note"]


def ledger(slug="mixed"):
    rows = []
    for i, (sport, period, pnl, fee) in enumerate([
        ("mlb", "dev", 10, 1), ("baseball", "holdout", 5, 0),
        ("nfl", "dev", -10, 2), ("cricket", "dev", 20, 1),
        ("mma_boxing", "holdout", -20, 0),
    ], 1):
        rows.append([i, period, "2026-09-01", sport, sport, f"event-{i}", f"market-{i}", "YES",
                     100 + i, .5, 100, fee, "resolution", 200 + i, 1 if pnl > 0 else 0,
                     100 + fee + pnl, pnl, pnl / 100, f"signal-{i}"])
    return {"slug": slug, "title": "Mixed strategy", "n_total_trades": len(rows),
            "hypothesis": "Research example", "verdict": "INCONCLUSIVE",
            "columns": COLUMNS, "rows": rows}


@pytest.fixture(autouse=True)
def isolated_ledgers(monkeypatch, tmp_path):
    ledger_dir = tmp_path / "ledgers"
    ledger_dir.mkdir()
    monkeypatch.setattr(server, "LEDGERS", ledger_dir)
    monkeypatch.setattr(server, "INDEX", ledger_dir / "_index.json")
    monkeypatch.setattr(server, "MARKETS", tmp_path / "markets.parquet")
    with server._market_lock:
        server._market_cache.clear()
    with server._lock:
        server._cache.clear()
    yield ledger_dir
    with server._lock:
        server._cache.clear()


def put(directory, doc):
    target = directory / f"{doc['slug']}.json"
    target.write_text(json.dumps(doc))
    return target


def test_index_lifecycle_and_warm_cache(isolated_ledgers, monkeypatch):
    assert client.get("/api/index").json()["strategies"] == []
    first = put(isolated_ledgers, ledger())
    calls = []
    original = server._read_ledger

    def read(path):
        calls.append(path.name)
        return original(path)

    monkeypatch.setattr(server, "_read_ledger", read)
    assert client.get("/api/index").json()["strategies"][0]["slug"] == "mixed"
    assert calls == ["mixed.json"]
    calls.clear()
    client.get("/api/index")
    assert calls == []  # Warm request must not reopen any source ledger.
    (isolated_ledgers / "_manifest.json").write_text("{}")
    (isolated_ledgers / ".hidden.json").write_text("{}")
    assert client.get("/api/health").json()["ledgers"] == 1
    assert calls == []
    older = put(isolated_ledgers, ledger("older"))
    os.utime(older, ns=(1, 1))
    assert len(client.get("/api/index").json()["strategies"]) == 2
    doc = ledger()
    doc["title"] = "Revised title"
    put(isolated_ledgers, doc)
    assert next(d for d in client.get("/api/index").json()["strategies"] if d["slug"] == "mixed")["title"] == "Revised title"
    first.unlink()
    assert [d["slug"] for d in client.get("/api/index").json()["strategies"]] == ["older"]
    older.unlink()
    assert client.get("/api/index").json()["strategies"] == []


def test_cache_not_reused_when_size_changes_at_same_mtime(isolated_ledgers):
    path = put(isolated_ledgers, ledger())
    assert client.get("/api/strategy/mixed").json()["meta"]["title"] == "Mixed strategy"
    old = path.stat()
    doc = ledger()
    doc["title"] = "A longer replacement title"
    put(isolated_ledgers, doc)
    os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))
    assert client.get("/api/strategy/mixed").json()["meta"]["title"] == doc["title"]


def test_canonical_families_match_index_cards_table_and_equity(isolated_ledgers):
    put(isolated_ledgers, ledger())
    index = client.get("/api/index").json()
    strategy = index["strategies"][0]
    assert strategy["sport_counts"] == {"baseball": 2, "other": 2, "american_football": 1}
    assert sum(t["trades"] for t in index["tabs"][1:]) == index["tabs"][0]["trades"] == 5
    meta = client.get("/api/strategy/mixed").json()
    assert meta["sports"] == ["american_football", "baseball", "other"]
    for sport, expected_ids in [("baseball", [1, 2]), ("mlb", [1, 2]), ("other", [4, 5]),
                                 ("american_football", [3]), ("football", [3])]:
        trades = client.get(f"/api/strategy/mixed/trades?sport={sport}").json()
        equity = client.get(f"/api/strategy/mixed/equity?sport={sport}").json()
        assert trades["total"] == equity["n"] == len(expected_ids)
        assert [r[0] for r in trades["rows"]] == expected_ids
        assert equity["points"][-1][1] == trades["pnl"]
        assert trades["kpis"] == strategy["kpis"][server._fam(sport)]
    other = client.get("/api/strategy/mixed/trades?sport=other&period=holdout").json()
    assert other["kpis"] == {"holdout": {"roi": -.2, "bets": 1}}
    assert other["total"] == 1 and other["pnl"] == -20 and other["roi"] == -.2


def test_pagination_filters_and_empty_equity(isolated_ledgers):
    put(isolated_ledgers, ledger())
    pages = [client.get(f"/api/strategy/mixed/trades?offset={offset}&limit=2").json() for offset in (0, 2, 4)]
    assert [r[0] for page in pages for r in page["rows"]] == [1, 2, 3, 4, 5]
    assert all(p["total"] == 5 and p["pnl"] == 5 for p in pages)
    for params, ids in [("result=win", [1, 2, 4]), ("result=loss", [3, 5]), ("q=signal-4", [4]),
                        ("sport=other&period=holdout&result=loss", [5]), ("q=no-match", [])]:
        table = client.get("/api/strategy/mixed/trades?" + params).json()
        eq = client.get("/api/strategy/mixed/equity?" + params).json()
        assert [r[0] for r in table["rows"]] == ids
        assert eq["n"] == table["total"] == len(ids)
        assert (eq["points"][-1][1] if ids else 0) == table["pnl"]
    for suffix in ("trades?offset=-1", "trades?limit=0", "equity?points=0"):
        assert client.get("/api/strategy/mixed/" + suffix).status_code == 422


@pytest.mark.parametrize("bad", ["{", '{"slug":"broken","columns":[],"rows":[]}', '{"slug":"wrong","columns":[],"rows":[]}'])
def test_malformed_ledgers_visible_in_health_and_index(isolated_ledgers, bad):
    (isolated_ledgers / "broken.json").write_text(bad)
    index = client.get("/api/index").json()["strategies"]
    assert len(index) == 1 and index[0]["slug"] == "broken" and index[0]["error"]
    health = client.get("/api/health").json()
    assert health["ok"] is False and health["ledgers"] == 1 and health["errors"][0]["slug"] == "broken"
    assert client.get("/api/strategy/broken").status_code == 500


def test_security_traversal_and_noindex():
    assert client.get("/api/strategy/..%2f..%2fetc%2fpasswd").status_code == 404
    for slug in ("bad!slug", "_index"):
        assert client.get("/api/strategy/" + slug).status_code == 400
    assert client.get("/api/health").headers["X-Robots-Tag"] == "noindex"
    res = client.get("/robots.txt")
    assert res.status_code == 200 and "Disallow: /" in res.text and res.headers["X-Robots-Tag"] == "noindex"


@pytest.mark.skipif(os.environ.get("PMSPORTS_BROWSER_QA") != "1", reason="opt-in browser gate; install Playwright and a Chromium browser")
def test_ui_delayed_filters_do_not_overwrite_table_or_chart(isolated_ledgers):
    """Hold real old responses until the new filter renders, then release both."""
    playwright = pytest.importorskip("playwright.async_api")
    import uvicorn

    put(isolated_ledgers, ledger())
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    instance = uvicorn.Server(uvicorn.Config(server.app, log_level="critical"))
    thread = threading.Thread(target=instance.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()

    async def check():
        async with playwright.async_playwright() as pw:
            kwargs = {"executable_path": os.environ["PMSPORTS_CHROMIUM"]} if os.environ.get("PMSPORTS_CHROMIUM") else {}
            browser = await pw.chromium.launch(**kwargs)
            page = await browser.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            held = set()
            released = set()
            release = asyncio.Event()

            async def delayed(route):
                response = await route.fetch()
                if "sport=american_football" in route.request.url:
                    path = route.request.url.split("?")[0]
                    held.add(path)
                    await release.wait()
                    await route.fulfill(response=response)
                    released.add(path)
                else:
                    await route.fulfill(response=response)

            try:
                await page.route("**/api/strategy/mixed/*", delayed)
                await page.goto(f"http://127.0.0.1:{port}/")
                await page.wait_for_selector("#tb tr[data-id='1']")
                assert "0/1" in await page.locator("#facts").inner_text()
                await page.select_option("#fs", "american_football")
                assert await page.locator("#fs option:checked").text_content() == "Football"
                for _ in range(200):
                    if len(held) == 2:
                        break
                    await asyncio.sleep(.01)
                assert len(held) == 2
                await page.select_option("#fs", "baseball")
                await page.wait_for_function("document.querySelector('#kTrades').textContent === '2' && document.querySelector('#chartNote').textContent.includes('2 rows')")
                await page.evaluate("window.dispatchEvent(new Event('resize'))")
                release.set()
                for _ in range(200):
                    if len(released) == 2:
                        break
                    await asyncio.sleep(.01)
                assert len(released) == 2
                await page.wait_for_timeout(100)
                assert await page.locator("#tb tr.t").count() == 2
                assert await page.locator("#tb tr[data-id='3']").count() == 0
                assert "2 rows" in await page.locator("#chartNote").text_content()
                await page.click(".tab[data-k='other']")
                await page.wait_for_selector("#tb tr[data-id='4']")
                assert await page.locator("#fs").input_value() == "other"
                assert await page.locator("#kTrades").inner_text() == "2"
                assert "−$20" not in await page.locator("#kPnl").inner_text()
                await page.select_option("#fp", "holdout")
                await page.wait_for_function("document.querySelector('#kTrades').textContent === '1'")
                assert await page.locator("#tb tr[data-id='5']").count() == 1
                assert "−$20" == await page.locator("#kPnl").inner_text()
                assert "no bets" in await page.locator("#kpisBox .kpi").nth(0).inner_text()
                assert not errors
            finally:
                release.set()
                await browser.close()

    try:
        deadline = time.monotonic() + 5
        while not instance.started and time.monotonic() < deadline:
            time.sleep(.01)
        assert instance.started
        asyncio.run(check())
    finally:
        instance.should_exit = True
        thread.join(timeout=5)
        sock.close()
        assert not thread.is_alive()


def test_no_fill_audits_do_not_count_as_bets_and_arbitrary_periods_work():
    import pandas as pd
    frame = pd.DataFrame(dict(period=['historical_capture']*3, stake_usd=[10.,0.,0.],
        fee_usd=[.25,0.,0.], pnl_usd=[-1.,0.,0.]))
    result = server._calc_kpis(frame)
    assert result['historical_capture']['bets'] == 1
    assert result['historical_capture']['roi'] == -1/10.25


def test_empty_declared_sport_has_zero_membership_without_invented_metrics(isolated_ledgers):
    for slug, sport in [("empty-football", "nfl"), ("empty-other", "cricket"),
                        ("empty-multi", "multi"), ("empty-unknown", "unknown")]:
        doc = ledger(slug)
        doc.update(rows=[], n_total_trades=0, sport=sport)
        put(isolated_ledgers, doc)
    index = client.get("/api/index").json()
    strategies = {d["slug"]: d for d in index["strategies"]}
    assert strategies["empty-football"]["sport_counts"] == {"american_football": 0}
    assert strategies["empty-other"]["sport_counts"] == {"other": 0}
    assert strategies["empty-multi"]["sport_counts"] == strategies["empty-unknown"]["sport_counts"] == {}
    assert {t["key"]: t["trades"] for t in index["tabs"]} == {"all": 0, "american_football": 0, "other": 0}
    meta = client.get("/api/strategy/empty-football").json()
    assert meta["sports"] == ["american_football"]
    assert meta["by_sport"] == [{"sport": "american_football", "trades": 0, "pnl": 0., "roi": None}]
    rows = client.get("/api/strategy/empty-football/trades?sport=american_football").json()
    assert rows["rows"] == [] and rows["total"] == rows["fills"] == 0
    assert rows["roi"] is None and rows["kpis"] == {}
    assert client.get("/api/strategy/empty-football/equity?sport=american_football").json()["points"] == []


def compact_doc(slug="compact", whale=False):
    doc = ledger(slug)
    row = dict(zip(doc["columns"], doc["rows"][0]))
    row.update(event=999, market=42, side=1)
    if whale:
        row.update(m=row.pop("market"), s=row.pop("side"), y=1., signal_ts=99.)
        for field in ("exit_ts", "exit_price", "date"):
            row.pop(field)
    doc.update(columns=list(row), rows=[list(row.values())], n_total_trades=1,
               code_path="pmsports/analysis/" + ("whale_prices.py" if whale else "calibration.py"))
    return doc


def market_mapping(**kwargs):
    row = dict(m=42, event_slug="away-home-2026-09-01", market_slug="away-home-moneyline",
               o0="Home Team", o1="Away Team", closed_ts=500.)
    row.update(kwargs)
    pd.DataFrame([row]).to_parquet(server.MARKETS, index=False)


def api_rows(slug="compact", params=""):
    response = client.get(f"/api/strategy/{slug}/trades{params}")
    assert response.status_code == 200, response.text
    page = response.json()
    return [dict(zip(page["columns"], r)) for r in page["rows"]]


def test_compact_labels_use_exact_outcome_and_keep_raw_codes(isolated_ledgers):
    market_mapping()
    put(isolated_ledgers, compact_doc())
    row = api_rows()[0]
    assert (row["event"], row["market"], row["side"]) == ("away-home-2026-09-01", "away-home-moneyline", "Away Team")
    assert (row["market_code"], row["event_code"], row["side_code"]) == (42, 999, 1)
    assert len(api_rows(params="?q=moneyline")) == 1
    assert len(api_rows(params="?q=Away%20Team")) == 1
    assert row["stake_usd"] == 100 and row["fee_usd"] == 1 and row["pnl_usd"] == 10
    doc = compact_doc("ordinary")
    doc["code_path"] = "pmsports/research/other.py"
    put(isolated_ledgers, doc)
    original = api_rows("ordinary")[0]
    assert (original["event"], original["market"], original["side"]) == (999, 42, 1)
    assert "market_code" not in original


def test_whale_resolution_and_no_fill_clock_are_not_fabricated(isolated_ledgers):
    market_mapping()
    doc = compact_doc(whale=True)
    row = dict(zip(doc["columns"], doc["rows"][0]))
    row.update(entry_ts=None, entry_price=None, stake_usd=0., fee_usd=0., payout=0., pnl_usd=0., status="unfilled")
    doc.update(columns=list(row), rows=[list(row.values())])
    put(isolated_ledgers, doc)
    shown = api_rows()[0]
    assert shown["m"] == shown["market_code"] == 42 and shown["s"] == shown["side_code"] == 1
    assert shown["side"] == "Away Team" and shown["exit_ts"] == 500 and shown["exit_price"] == 1
    assert shown["entry_ts"] is None and shown["entry_price"] is None and shown["date"] == "1970-01-01"
    assert all(shown[c] == 0 for c in ("stake_usd", "fee_usd", "payout", "pnl_usd"))


def test_mapping_lifecycle_invalidates_display_and_index_without_ledger_edits(isolated_ledgers, monkeypatch):
    path = put(isolated_ledgers, compact_doc())
    ledger_identity = (path.stat().st_mtime_ns, path.stat().st_size)
    assert api_rows()[0]["event"] == "Event unavailable"
    market_mapping()
    assert api_rows()[0]["side"] == "Away Team"
    client.get("/api/index")
    reads = []
    original = server._read_ledger
    monkeypatch.setattr(server, "_read_ledger", lambda p: (reads.append(p.name), original(p))[1])
    client.get("/api/index")
    assert reads == []
    market_mapping(o1="Renamed Outcome")
    assert api_rows()[0]["side"] == "Renamed Outcome"
    reads.clear()
    client.get("/api/index")
    assert reads == ["compact.json"]
    server.MARKETS.unlink()
    assert api_rows()[0]["side"] == "Outcome unavailable"
    server.MARKETS.write_text("malformed parquet")
    assert api_rows()[0]["market"] == "Market unavailable"
    assert (path.stat().st_mtime_ns, path.stat().st_size) == ledger_identity


def test_unknown_and_ambiguous_mapping_never_guesses_side(isolated_ledgers):
    market_mapping()
    doc = compact_doc()
    side_idx, market_idx = doc["columns"].index("side"), doc["columns"].index("market")
    doc["rows"][0][side_idx] = 2
    put(isolated_ledgers, doc)
    assert api_rows()[0]["side"] == "Outcome unavailable"
    doc["rows"][0][side_idx] = 0
    doc["rows"][0][market_idx] = 123456
    put(isolated_ledgers, doc)
    assert api_rows()[0]["event"] == "Event unavailable"
    doc["rows"][0][market_idx] = 42
    put(isolated_ledgers, doc)
    assert api_rows()[0]["side"] == "Home Team"
    mapping = pd.read_parquet(server.MARKETS)
    pd.concat([mapping, mapping.assign(o0="Conflicting Team")]).to_parquet(server.MARKETS, index=False)
    assert api_rows()[0]["side"] == "Outcome unavailable"
