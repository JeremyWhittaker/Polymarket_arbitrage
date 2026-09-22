"""Ledger API checks; browser gate is opt-in: PMSPORTS_BROWSER_QA=1 pytest ..."""
import asyncio
import json
import os
import socket
import threading
import time

import pytest
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
