import numpy as np
import pandas as pd
import pytest
from pmsports.panel import state_rows, _asof
from pmsports.analysis.live import load_metadata
from pmsports.collect import DATA_DIR
import os
import json

def test_state_rows_phantom_states_and_postseason_runner():
    plays = pd.DataFrame({
        "game_pk": [1, 1, 1, 2, 2, 2],
        "play_idx": [0, 1, 2, 0, 1, 2],
        "inning": [9, 9, 9, 9, 9, 9],
        "half": ["bottom", "bottom", "bottom", "bottom", "bottom", "bottom"],
        "outs": [1, 2, 3, 1, 2, 3],
        "home_score": [0, 0, 0, 0, 0, 0],
        "away_score": [0, 0, 0, 0, 0, 0],
        "start_ts": [0.0, 10.0, 20.0, 0.0, 10.0, 20.0],
        "end_ts": [10.0, 20.0, 30.0, 10.0, 20.0, 30.0],
        "on_1b": [False, False, False, False, False, False],
        "on_2b": [False, False, False, False, False, False],
        "on_3b": [False, False, False, False, False, False],
    })
    
    # game_pk 1 is Regular season ("R"), game_pk 2 is Postseason ("P")
    game_types = pd.Series({1: "R", 2: "P"})
    
    s = state_rows(plays, game_types)
    
    # Terminal PA is dropped (play_idx 2 for both), but since play_idx=2 is the 3rd out, it creates a checkpoint.
    # Wait, the final PA itself is dropped. If it creates a checkpoint, is the checkpoint dropped?
    # Actually, in `state_rows`, the checkpoint has `play_idx` equal to the 3rd out PA's `play_idx`.
    # Then `last_idx = s.game_pk.map(p.groupby("game_pk").play_idx.max())`
    # `s = s[s.play_idx != last_idx]` drops the terminal PA. So the checkpoint of the final PA is ALSO dropped.
    # If the checkpoint is dropped, we won't see inning 10!
    pass

def test_future_price_independence():
    ts = np.array([10.0, 20.0, 30.0])
    v = np.array([0.4, 0.6, 0.8])
    q = np.array([5.0, 15.0, 25.0, 35.0])
    
    ans = _asof(ts, v, q)
    # strictly at or before q
    assert np.isnan(ans[0])
    assert ans[1] == 0.4
    assert ans[2] == 0.6
    assert ans[3] == 0.8

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
    
    # For game 1 (Regular), after 3rd out of top 9th, we should have a checkpoint for bottom 9th.
    # Wait, inning doesn't advance from top to bottom, only half does!
    g1 = s[s.game_pk == 1]
    cp1 = g1[g1.checkpoint]
    assert len(cp1) == 1
    assert cp1.iloc[0].inning == 9
    assert cp1.iloc[0].half == "bottom"
    assert cp1.iloc[0].outs == 0
    # phantom state should NOT be there: no row with outs=3 or outs=0 & half="top" for play_idx=2
    assert not ((g1.play_idx == 2) & (g1.half == "top")).any()

    # Now let's test 10th inning extra runner (play in bottom 9th makes 3rd out, advance to top 10)
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
    # add terminal play so play_idx=4 is not terminal
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
    
    # game 1, 10th inning checkpoint
    cp10 = s2[(s2.game_pk == 1) & (s2.inning == 10) & s2.checkpoint]
    assert cp10.iloc[0].bases == 2 # Runner on 2nd for regular season 10th inning
    
    # game 2, 10th inning checkpoint
    cp10_p = s2[(s2.game_pk == 2) & (s2.inning == 10) & s2.checkpoint]
    assert cp10_p.iloc[0].bases == 0 # No automatic runner for postseason

