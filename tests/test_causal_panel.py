import numpy as np
import pandas as pd
import pytest
from pmsports.panel import state_rows, _asof
from pmsports.analysis.live import load_metadata
from pmsports.collect import DATA_DIR
import os
import json
import tempfile
import pathlib
import shutil


def test_postponed_contract_alias_uses_cached_tokens(tmp_path):
    from pmsports.panel import _match_cached_contracts
    games = pd.DataFrame([
        dict(game_pk=7, slug="postponed", home_token="old-h", away_token="old-a"),
        dict(game_pk=7, slug="played", home_token="new-h", away_token="new-a"),
    ])
    pd.DataFrame({"asset": ["new-h", "new-a"]}).to_parquet(tmp_path / "7.parquet")
    matched = _match_cached_contracts(games, tmp_path)
    assert matched.slug.tolist() == ["played"]
    assert _match_cached_contracts(pd.concat([games, games]), tmp_path).slug.tolist() == ["played"]
    pd.DataFrame({"asset": ["unknown"]}).to_parquet(tmp_path / "7.parquet")
    with pytest.raises(ValueError, match="ambiguous cached contract"):
        _match_cached_contracts(games, tmp_path)


def test_fee_recovery_matches_only_known_contract_metadata():
    from pmsports.panel import _restore_known_fees
    games = pd.DataFrame(dict(condition_id=['known-free','known-paid','unknown'],fee_rate=[np.nan,.025,np.nan]))
    metadata = pd.DataFrame(dict(condition_id=['known-free','known-paid'],fee_rate=[0.,.05]))
    fixed = _restore_known_fees(games,metadata)
    assert fixed.fee_rate.iloc[0] == 0
    assert fixed.fee_rate.iloc[1] == .025
    assert pd.isna(fixed.fee_rate.iloc[2])

def test_metadata_rollover(tmp_path, monkeypatch):
    monkeypatch.setattr("pmsports.analysis.live.DATA_DIR", tmp_path)
    # Create yesterday's dir with games.jsonl
    d_yes = tmp_path / "live" / "2026-09-18"
    d_yes.mkdir(parents=True)
    with open(d_yes / "games.jsonl", "w") as f:
        f.write(json.dumps({"game": {"condition_id": "c1", "game_pk": 1}}) + "\n")

    # Create today's dir WITHOUT games.jsonl
    d_today = tmp_path / "live" / "2026-09-19"
    d_today.mkdir(parents=True)

    # load_metadata should find it from yesterday
    games = load_metadata("2026-09-19")
    assert len(games) == 1
    assert games.iloc[0].condition_id == "c1"


def test_phantom_state_and_postseason():
    plays = pd.DataFrame({
        "game_pk": [1, 1, 1, 2, 2, 2],
        "play_idx": [0, 1, 2, 0, 1, 2],
        "inning": [9, 9, 9, 9, 9, 9],
        "half": ["top", "top", "top", "top", "top", "top"],
        "outs": [1, 2, 3, 1, 2, 3],
        "home_score": [0, 0, 0, 0, 0, 0],
        "away_score": [0, 0, 0, 0, 0, 0],
        "start_ts": [0.0, 10.0, 20.0, 0.0, 10.0, 20.0],
        "end_ts": [10.0, 20.0, 30.0, 10.0, 20.0, 30.0],
        "on_1b": [False, False, False, False, False, False],
        "on_2b": [False, False, False, False, False, False],
        "on_3b": [False, False, False, False, False, False],
    })

    # We add play_idx=3 (the terminal play for the bottom of 9th) so that play_idx 2 (3rd out of top 9th) is NOT the terminal play!
    terminal_plays = pd.DataFrame({
        "game_pk": [1, 2],
        "play_idx": [3, 3],
        "inning": [9, 9],
        "half": ["bottom", "bottom"],
        "outs": [1, 1],
        "home_score": [1, 1],
        "away_score": [0, 0],
        "start_ts": [30.0, 30.0],
        "end_ts": [40.0, 40.0],
        "on_1b": [False, False],
        "on_2b": [False, False],
        "on_3b": [False, False],
    })
    plays = pd.concat([plays, terminal_plays], ignore_index=True)

    game_types = pd.Series({1: "R", 2: "P"})
    s = state_rows(plays, game_types)

    g1 = s[s.game_pk == 1]
    cp1 = g1[g1.checkpoint]
    assert len(cp1) == 1
    assert cp1.iloc[0].inning == 9
    assert cp1.iloc[0].half == "bottom"
    assert cp1.iloc[0].outs == 0
    assert not ((g1.play_idx == 2) & (g1.half == "top")).any()

    plays_10 = pd.DataFrame({
        "game_pk": [1, 2],
        "play_idx": [4, 4],
        "inning": [9, 9],
        "half": ["bottom", "bottom"],
        "outs": [3, 3],
        "home_score": [0, 0],
        "away_score": [0, 0],
        "start_ts": [40.0, 40.0],
        "end_ts": [50.0, 50.0],
        "on_1b": [False, False],
        "on_2b": [False, False],
        "on_3b": [False, False],
    })
    plays2 = pd.concat([plays, plays_10], ignore_index=True)
    term2 = pd.DataFrame({
        "game_pk": [1, 2],
        "play_idx": [5, 5],
        "inning": [10, 10],
        "half": ["top", "top"],
        "outs": [1, 1],
        "home_score": [0, 0],
        "away_score": [0, 0],
        "start_ts": [50.0, 50.0],
        "end_ts": [60.0, 60.0],
        "on_1b": [False, False],
        "on_2b": [False, False],
        "on_3b": [False, False],
    })
    plays2 = pd.concat([plays2, term2], ignore_index=True)
    s2 = state_rows(plays2, game_types)

    cp10 = s2[(s2.game_pk == 1) & (s2.inning == 10) & s2.checkpoint]
    assert cp10.iloc[0].bases == 2

    cp10_p = s2[(s2.game_pk == 2) & (s2.inning == 10) & s2.checkpoint]
    assert cp10_p.iloc[0].bases == 0

def test_one_game_smoke(monkeypatch):
    from pmsports.panel import build_panel

    orig_d = DATA_DIR / "mlb"
    if not (orig_d / "games.parquet").exists():
        pytest.skip("Optional integration smoke requires locally collected MLB data")
    games = pd.read_parquet(orig_d / "games.parquet")
    games = games[games.game_pk.notna() & ~games.resolution_mismatch]
    games["game_pk"] = games.game_pk.astype(int)

    target_pk = None
    for pk in games.game_pk:
        if (orig_d / "plays" / f"{pk}.parquet").exists() and \
           (orig_d / "prices" / f"{pk}.parquet").exists() and \
           (orig_d / "trades" / f"{pk}.parquet").exists():
           target_pk = pk
           break
    if target_pk is None:
        pytest.skip("No game with all files found")

    with tempfile.TemporaryDirectory() as td:
        tdp = pathlib.Path(td)
        g_df = games[games.game_pk == target_pk].copy()
        g_df.to_parquet(tdp / "games.parquet")

        for subdir in ["plays", "prices", "trades"]:
            (tdp / subdir).mkdir()
            shutil.copy2(orig_d / subdir / f"{target_pk}.parquet", tdp / subdir / f"{target_pk}.parquet")

        (tdp / "mlb_only" / "plays").mkdir(parents=True)
        for sched_f in (orig_d / "mlb_only").glob("schedule_*.parquet"):
            shutil.copy2(sched_f, tdp / "mlb_only" / sched_f.name)

        monkeypatch.setattr("pmsports.panel.sport_dir", lambda sport: tdp)

        build_panel("mlb")

        panel = pd.read_parquet(tdp / "panel.parquet")

        EXEC_DELAY_S = 5

        mkt_valid = panel[panel.mkt_ts.notna()]
        if len(mkt_valid):
            assert (mkt_valid.mkt_ts < mkt_valid.decision_ts).all(), "mkt_ts must be strictly before decision_ts"

        exec_valid = panel[panel.exec_ts.notna()]
        if len(exec_valid):
            assert (exec_valid.exec_ts >= exec_valid.decision_ts + EXEC_DELAY_S).all(), "exec_ts must be strictly after delay"

        assert panel.state_ts.is_monotonic_increasing, "states must be sorted by time"

        assert not panel.duplicated(["game_pk", "play_idx", "checkpoint"]).any(), "unique game/play state"

import pytest
import asyncio
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
from pmsports.record import _refresh, _Sink

def test_refresh_day_rollover():
    games_ref = {"tokens": set(), "game_pks": set()}
    meta_sink = MagicMock(spec=_Sink)

    mock_games = [
        {"gamePk": 1, "home_token": "h1", "away_token": "a1"},
    ]
    mock_sched = {"dates": [{"games": [{"gamePk": 1, "status": {"abstractGameState": "Live"}}]}]}

    stop_time = 105.0
    time_returns = [100.0, 101.0, 102.0, 106.0, 106.0]
    def mock_time():
        return time_returns.pop(0)

    class MockDatetime:
        @classmethod
        def now(cls, tz=None):
            if len(time_returns) >= 3:
                return datetime(2026, 9, 22, tzinfo=timezone.utc)
            return datetime(2026, 9, 23, tzinfo=timezone.utc)

    async def mock_sleep(s):
        pass

    def mock_slate(window_h):
        return mock_games

    def mock_get_json(url, params=None):
        return mock_sched

    def mock_log_warning(msg, exc):
        print(f"Warning: {msg}, {exc}")

    async def run_test():
        with patch("pmsports.record.time.time", side_effect=mock_time), \
             patch("pmsports.record.datetime", MockDatetime), \
             patch("pmsports.record.asyncio.sleep", mock_sleep), \
             patch("pmsports.record._slate", side_effect=mock_slate), \
             patch("pmsports.record.get_json", side_effect=mock_get_json), \
             patch("pmsports.record.log.warning", side_effect=mock_log_warning):

             await _refresh(games_ref, 4.0, stop_time, meta_sink)

    asyncio.run(run_test())

    assert meta_sink.write.call_count == 2
    args = meta_sink.write.call_args_list
    assert args[0][0][0] == {"game": mock_games[0]}
    assert args[1][0][0] == {"game": mock_games[0]}
