import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from pmsports.webapp.server import app, LEDGERS, INDEX, build_index, _cache, _lock

client = TestClient(app)

@pytest.fixture(autouse=True)
def isolated_ledgers(monkeypatch, tmp_path):
    ledger_dir = tmp_path / "ledgers"
    ledger_dir.mkdir()
    monkeypatch.setattr("pmsports.webapp.server.LEDGERS", ledger_dir)
    monkeypatch.setattr("pmsports.webapp.server.INDEX", ledger_dir / "_index.json")
    with _lock:
        _cache.clear()
    yield ledger_dir

def test_index_lifecycle_and_cache(isolated_ledgers):
    # Empty index
    res = client.get("/api/index")
    assert res.status_code == 200
    assert len(res.json()["strategies"]) == 0

    # Add ledger
    doc = {
        "slug": "test-strategy",
        "title": "Test Strategy",
        "n_total_trades": 2,
        "columns": ["id", "pnl_usd", "stake_usd", "fee_usd", "sport", "period"],
        "rows": [
            [1, 10, 100, 0, "nba", "dev"],
            [2, -5, 50, 0, "nfl", "holdout"]
        ]
    }
    f1 = isolated_ledgers / "test-strategy.json"
    f1.write_text(json.dumps(doc))

    # Re-fetch index - should see the new ledger automatically based on mtime
    res = client.get("/api/index")
    assert res.status_code == 200
    strategies = res.json()["strategies"]
    assert len(strategies) == 1
    assert strategies[0]["slug"] == "test-strategy"
    
    # Update ledger (change)
    doc["title"] = "Updated Strategy"
    f1.write_text(json.dumps(doc))
    import time
    time.sleep(0.01) # ensure mtime diff
    f1.touch()

    res = client.get("/api/index")
    strategies = res.json()["strategies"]
    assert strategies[0]["title"] == "Updated Strategy"

    time.sleep(0.01)
    # Delete ledger
    f1.unlink()
    res = client.get("/api/index")
    assert len(res.json()["strategies"]) == 0

def test_filtered_totals_and_aliases(isolated_ledgers):
    doc = {
        "slug": "alias-test",
        "title": "Alias Test",
        "columns": ["id", "pnl_usd", "stake_usd", "fee_usd", "sport", "period"],
        "rows": [
            [1, 10, 100, 1, "mlb", "dev"], # baseball alias
            [2, 5, 50, 0, "baseball", "holdout"],
            [3, -10, 100, 2, "nfl", "dev"], # american_football alias
        ]
    }
    (isolated_ledgers / "alias-test.json").write_text(json.dumps(doc))

    # Test api_trades filtered by sport 'baseball'
    res = client.get("/api/strategy/alias-test/trades?sport=baseball")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 2
    assert data["pnl"] == 15.0 # 10 + 5

    # Test api_trades filtered by period 'dev'
    res = client.get("/api/strategy/alias-test/trades?period=dev")
    data = res.json()
    assert data["total"] == 2
    assert data["pnl"] == 0.0 # 10 - 10

def test_security_traversal(isolated_ledgers):
    res = client.get("/api/strategy/..%2f..%2fetc%2fpasswd")
    assert res.status_code == 404

    res = client.get("/api/strategy/bad!slug")
    assert res.status_code == 400

    res = client.get("/api/strategy/hidden_metadata")
    assert res.status_code == 404

def test_robots_noindex():
    res = client.get("/api/health")
    assert res.headers.get("X-Robots-Tag") == "noindex"
    
    res = client.get("/robots.txt")
    assert res.status_code == 200
    assert "Disallow: /" in res.text
    assert res.headers.get("X-Robots-Tag") == "noindex"
