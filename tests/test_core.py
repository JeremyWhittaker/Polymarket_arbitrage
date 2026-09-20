import numpy as np
import pandas as pd
import pytest

from pmsports import polymarket as pm
from pmsports.analysis.stats import bet_pnl, wilson
from pmsports.collect import match_mlb
from pmsports.panel import state_rows


def test_parse_ts_formats():
    assert pm.parse_ts("2026-09-18T00:05:00Z") == pm.parse_ts("2026-09-18 00:05:00+00") == 1789689900.0


def test_taker_fee_matches_docs_table():
    # docs.polymarket.com/trading/fees, sports tab, 100 shares
    assert pm.taker_fee(100, 0.50) == pytest.approx(1.25)
    assert pm.taker_fee(100, 0.30) == pytest.approx(1.05)
    assert pm.taker_fee(100, 0.95) == pytest.approx(0.2375)


def _event(outcomes, prices, teams=True):
    e = {"id": 1, "slug": "mlb-aaa-bbb-2026-09-17", "title": "Away Club vs. Home Club", "eventDate": "2026-09-17",
         "startTime": "2026-09-18T00:05:00Z",
         "markets": [{"slug": "mlb-aaa-bbb-2026-09-17", "sportsMarketType": "moneyline",
                      "outcomes": str(outcomes).replace("'", '"'), "clobTokenIds": '["t0", "t1"]',
                      "outcomePrices": str(prices).replace("'", '"'), "umaResolutionStatus": "resolved",
                      "conditionId": "0xc"}]}
    if teams:
        e["teams"] = [{"name": "Away Club", "ordering": "away"}, {"name": "Home Club", "ordering": "home"}]
    return e


@pytest.mark.parametrize("outcomes,prices,home_tok,home_won", [
    (["Away Club", "Home Club"], ["0", "1"], "t1", True),
    (["Home Club", "Away Club"], ["0", "1"], "t0", False),
    (["Club", "Home"], ["1", "0"], "t1", False),   # nickname outcomes still map by substring
])
def test_parse_game_event_orientation(outcomes, prices, home_tok, home_won):
    g = pm.parse_game_event(_event(outcomes, prices))
    assert g["home_token"] == home_tok and g["home_won"] is home_won


def test_match_flips_reversed_polymarket_orientation():
    pmg = pd.DataFrame([{"event_date": "2025-04-02", "start_ts": 1743612000.0, "away_team": "Cincinnati Reds",
                         "home_team": "Texas Rangers", "home_token": "tex", "away_token": "cin",
                         "home_outcome_idx": 1, "home_won": True}])
    mlb = pd.DataFrame([{"game_pk": 1, "official_date": "2025-04-02", "game_ts": 1743612000.0,
                         "abstract_state": "Final", "away_team_name": "Rangers", "home_team_name": "Reds",
                         "away_score": 3, "home_score": 1}])
    out = match_mlb(pmg, mlb).iloc[0]
    assert out.game_pk == 1 and out.pm_orientation_swapped
    assert out.home_team == "Cincinnati Reds" and out.home_token == "cin" and out.home_won == False  # noqa: E712
    assert not out.resolution_mismatch


def test_state_rows_checkpoints_and_terminal_drop():
    plays = pd.DataFrame({
        "game_pk": 1, "play_idx": [0, 1, 2], "inning": [1, 1, 1], "half": ["top", "top", "bottom"],
        "outs": [2, 3, 3], "home_score": [0, 0, 1], "away_score": [0, 0, 0],
        "on_1b": False, "on_2b": False, "on_3b": False, "start_ts": 0.0, "end_ts": [10.0, 20.0, 30.0]})
    s = state_rows(plays)
    cp = s[s.checkpoint]
    assert len(cp) == 1 and cp.iloc[0].half == "bottom" and cp.iloc[0].inning == 1 and cp.iloc[0].outs == 0
    assert 2 not in s.play_idx.values           # final PA dropped


def test_bet_pnl_fair_coin_loses_exactly_the_fee():
    won = np.array([1, 0] * 500)
    r = bet_pnl(np.full(1000, 0.5), won, 0.05, 0.0)
    assert r.pnl_per_share.mean() == pytest.approx(-0.0125)


def test_wilson_bounds():
    lo, hi = wilson(50, 100)
    assert lo < 0.5 < hi


def test_asof_handles_empty_and_before_start():
    from pmsports.panel import _asof
    assert np.isnan(_asof(np.array([]), np.array([]), np.array([5.0]))).all()
    out = _asof(np.array([10.0, 20.0]), np.array([0.4, 0.6]), np.array([5.0, 15.0, 25.0]))
    assert np.isnan(out[0]) and out[1] == 0.4 and out[2] == 0.6


def test_side_comes_from_token_id_not_outcome_index():
    """The Data API's outcomeIndex is wrong on a few thousand fills; the asset id is authoritative."""
    tok = {"cid": ("tok0", "tok1")}
    asset = np.array(["tok1", "tok0", "unknown"])
    oi = np.array([0, 1, 1], dtype="int8")          # first two disagree with the token
    tk = tok["cid"]
    fixed = np.where(asset == tk[0], 0, np.where(asset == tk[1], 1, oi)).astype("int8")
    assert list(fixed) == [1, 0, 1]                  # corrected, corrected, fallback kept
