import pytest
import numpy as np
import pandas as pd
import tempfile
from pathlib import Path
from pmsports.wallets import study, report

def _mock_data():
    t_rows = [
        {"timestamp": 100, "condition_id": "c1", "proxyWallet": "w1", "size": 10.0, "side_idx": 0, "q": 0.5, "y": 1.0, "fee_rate": 0.02, "in_play": False, "family": "soccer", "event_slug": "e1"},
        # This fill is before split (200), but closed_ts is 300 (after split). Should be excluded!
        {"timestamp": 150, "condition_id": "c2", "proxyWallet": "w1", "size": 10.0, "side_idx": 0, "q": 0.5, "y": 0.0, "fee_rate": 0.02, "in_play": False, "family": "soccer", "event_slug": "e2"},
        # This fill is after split (200), but closed_ts is 300. In evaluation!
        {"timestamp": 250, "condition_id": "c3", "proxyWallet": "w1", "size": 10.0, "side_idx": 0, "q": 0.5, "y": 1.0, "fee_rate": 0.02, "in_play": False, "family": "soccer", "event_slug": "e3"},
    ]
    t = pd.DataFrame(t_rows)
    for c in ["condition_id", "proxyWallet", "family", "event_slug"]:
        t[c] = t[c].astype("category")
        
    meta_rows = [
        {"condition_id": "c1", "closed_ts": 120.0},
        {"condition_id": "c2", "closed_ts": 300.0},
        {"condition_id": "c3", "closed_ts": 300.0},
    ]
    meta = pd.DataFrame(meta_rows).set_index("condition_id")
    return t, meta

def test_causality_boundary_study_run():
    t, meta = _mock_data()
    # Mock split to correspond to timestamp 200
    # pd.Timestamp(200, unit='s', tz="UTC")
    split_str = pd.Timestamp(200, unit='s', tz="UTC").strftime("%Y-%m-%d %H:%M:%S")
    
    # Run with meta
    res = study.run(t, split=split_str, label="all", meta=meta)
    
    # Should only include c1 in s1 (t1) since c2 closed after split
    s1 = res["s1"]
    # t1 should only have c1. So only 1 market.
    assert s1.loc["w1"].markets == 1
    assert s1.loc["w1"].staked == 5.0 # size 10 * q 0.5 = 5.0

def test_causality_boundary_walk_forward():
    t, meta = _mock_data()
    # Walk forward from 1970-01-01 to 1970-01-02
    # m0 = 100, m1 = whatever
    # To test walk forward we need month boundaries. Let's adjust timestamps to months.
    ts1 = pd.Timestamp("2025-01-15", tz="UTC").timestamp()
    ts2 = pd.Timestamp("2025-01-20", tz="UTC").timestamp()
    ts3 = pd.Timestamp("2025-02-15", tz="UTC").timestamp()
    
    t_rows = [
        {"timestamp": ts1, "condition_id": "c1", "proxyWallet": "w1", "size": 10.0, "side_idx": 0, "q": 0.5, "y": 1.0, "fee_rate": 0.0, "in_play": False, "family": "soccer", "event_slug": "e1"},
        {"timestamp": ts2, "condition_id": "c2", "proxyWallet": "w1", "size": 10.0, "side_idx": 0, "q": 0.5, "y": 0.0, "fee_rate": 0.0, "in_play": False, "family": "soccer", "event_slug": "e2"},
        {"timestamp": ts3, "condition_id": "c3", "proxyWallet": "w1", "size": 10.0, "side_idx": 0, "q": 0.5, "y": 1.0, "fee_rate": 0.0, "in_play": False, "family": "soccer", "event_slug": "e3"},
    ]
    t = pd.DataFrame(t_rows)
    for c in ["condition_id", "proxyWallet", "family", "event_slug"]:
        t[c] = t[c].astype("category")
        
    meta_rows = [
        {"condition_id": "c1", "closed_ts": ts1 + 10},
        {"condition_id": "c2", "closed_ts": pd.Timestamp("2025-02-05", tz="UTC").timestamp()},
        {"condition_id": "c3", "closed_ts": ts3 + 10},
    ]
    meta = pd.DataFrame(meta_rows).set_index("condition_id")
    
    # We set lookback to include Jan, evaluate on Feb. 
    # For evaluate on Feb, split is 2025-02-01.
    # Lookback window will evaluate fills < 2025-02-01 AND closed_ts < 2025-02-01.
    # c2 closed_ts is 2025-02-05, so it should be EXCLUDED from ranking!
    
    # study.walk_forward needs a bit more rows, but we can lower TOP_K and MIN_MKTS by monkeypatching
    study.MIN_MKTS = 1
    res = study.walk_forward(t, start="2025-01-01", end="2025-03-01", lookback_days=30, rules=("z",), delays=(0,), meta=meta)
    
    assert len(res) == 1
    # Only c1 was used for ranking, because c2 closed after Feb 1.
    
def test_cache_invalidation(monkeypatch):
    import shutil
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        monkeypatch.setattr(report, "OUT", tdp)
        
        # Write dummy files
        (tdp / "universe.parquet").write_text("u")
        (tdp / "leaderboard.parquet").write_text("l")
        
        cache = tdp / "report_cache"
        cache.mkdir(exist_ok=True)
        
        # Test 1: Generate manifest
        man1 = report.make_manifest("2026", "2025", "2027")
        hash1 = report.get_code_hash()
        
        # Update input
        (tdp / "universe.parquet").write_text("uu")
        man2 = report.make_manifest("2026", "2025", "2027")
        
        assert man1["inputs"]["universe.parquet"]["size"] != man2["inputs"]["universe.parquet"]["size"]
        assert man1 != man2

