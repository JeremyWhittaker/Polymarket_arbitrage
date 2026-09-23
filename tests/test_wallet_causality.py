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

def test_causality_boundary_walk_forward(monkeypatch):
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
        {"timestamp": ts3 + 1, "condition_id": "c3", "proxyWallet": "w2", "size": 10.0, "side_idx": 0, "q": 0.55, "y": 1.0, "fee_rate": 0.0, "in_play": False, "family": "soccer", "event_slug": "e3"},
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
    monkeypatch.setattr(study, "MIN_MKTS", 1)
    ranked = []
    original = study.skill.positions
    def capture_ranked(frame):
        ranked.append(frame.copy())
        return original(frame)
    monkeypatch.setattr(study.skill, "positions", capture_ranked)
    res = study.walk_forward(t, start="2025-01-01", end="2025-03-01", lookback_days=30, rules=("z", "whales"), delays=(0,), meta=meta)

    assert len(res) == 2
    assert len(ranked) == 1 and ranked[0].condition_id.astype(str).tolist() == ["c1"]
    # Equal independent policies get equal capacity; they do not compete with one another.
    assert res.staked.tolist() == [5.5, 5.5]
    assert res.trades.tolist() == [1, 1]
    assert res["pnl_per_$1"].tolist() == [4.5, 4.5]

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

        u_key = str(tdp / "universe.parquet")
        assert man1["inputs"][u_key]["size"] != man2["inputs"][u_key]["size"]
        assert man1 != man2

def test_cache_hash_includes_shared_execution(monkeypatch, tmp_path):
    wallet_dir=tmp_path/'wallets'; wallet_dir.mkdir()
    monkeypatch.setattr(report,'__file__',str(wallet_dir/'report.py'))
    source=tmp_path/'execution.py'; source.write_text('version=1')
    first=report.get_code_hash()
    source.write_text('version=2')
    assert report.get_code_hash()!=first


def test_corrupt_quote_cannot_improve_ranking_or_trigger_copy():
    from pmsports.wallets import skill
    t,_=_mock_data()
    bad=t.iloc[[0]].copy();bad['q']=-10.;bad['size']=1e6;bad['timestamp']=90
    combined=pd.concat([t,bad],ignore_index=True)
    pd.testing.assert_frame_equal(skill.positions(combined),skill.positions(t))
    copied=skill.copy_prices(combined,bad,delays=(0,))
    assert len(copied)==1
    assert copied.cost_usd_d0.iloc[0]==0 and copied.prop_cost_usd_d0.iloc[0]==0
    assert pd.isna(copied.fill_ts_d0.iloc[0])


def test_wallet_comparison_does_not_sample_future_event_set(monkeypatch):
    t,_=_mock_data();rows=pd.concat([t.iloc[[2]]]*20,ignore_index=True)
    rows['event_slug']=pd.Categorical([f'e{i}' for i in range(20)])
    monkeypatch.setattr(study,'MAX_COPY_ROWS',1,raising=False)
    captured=[]
    monkeypatch.setattr(study.skill,'build_groups',lambda _: {'stub':True})
    def copy(t,selected,**kw):captured.append(len(selected));return selected
    monkeypatch.setattr(study.skill,'copy_prices',copy)
    monkeypatch.setattr(study.skill,'copy_returns',lambda rows,*args,**kw:rows)
    monkeypatch.setattr(study.skill,'summarize_copy',lambda rows:dict(roi=0.,ci_lo=0.,ci_hi=0.,trades=len(rows)))
    stats=pd.DataFrame(dict(markets=[20],staked=[100.],pnl=[1.],pnl_net=[.5]),index=['w1'])
    got=study.evaluate(rows,stats,['w1'],{})
    assert captured==[20]*len(study.DELAYS) and got['copy_d30_trades']==20


def test_monthly_default_keeps_more_than_old_40000_signal_limit(monkeypatch):
    t,_=_mock_data();history=t.iloc[[0]].copy();history['timestamp']=pd.Timestamp('2025-01-15',tz='UTC').timestamp()
    nxt=pd.concat([t.iloc[[2]]]*40001,ignore_index=True)
    nxt['timestamp']=pd.Timestamp('2025-02-15',tz='UTC').timestamp()+np.arange(len(nxt))
    both=pd.concat([history,nxt],ignore_index=True)
    meta=pd.DataFrame(dict(condition_id=['c1','c3'],closed_ts=[history.timestamp.iloc[0]+1,nxt.timestamp.max()+1])).set_index('condition_id')
    monkeypatch.setattr(study,'MIN_MKTS',1)
    monkeypatch.setattr(pd.DataFrame,'sample',lambda *a,**k: (_ for _ in ()).throw(AssertionError('future-event sampling')))
    monkeypatch.setattr(pd.Series,'sample',lambda *a,**k: (_ for _ in ()).throw(AssertionError('future-event sampling')))
    monkeypatch.setattr(study.skill,'build_groups',lambda _: {'stub':True})
    monkeypatch.setattr(study.skill,'copy_prices',lambda tape,selected,**kw:selected)
    monkeypatch.setattr(study.skill,'copy_returns',lambda rows,*a,**kw:rows.assign(copy_roi=.1,w=1.))
    got=study.walk_forward(both,'2025-02-01','2025-03-01',rules=('z',),delays=(0,),meta=meta)
    assert got.trades.tolist()==[40001] and got.staked.tolist()==[40001.]


def _copy_fixture():
    rows=[]
    for event, start, won in [('a',100.,1.),('b',1000.,0.)]:
        for delay, wallet, price in [(0.,'leader',.5),(6.,'other',.55),(31.,'other',.6),(61.,'other',.65)]:
            rows.append(dict(timestamp=start+delay,condition_id=event,proxyWallet=wallet,
                size=100.,side_idx=0,q=price,y=won,fee_rate=.05,in_play=False,
                family='soccer',event_slug=event))
    t=pd.DataFrame(rows)
    for col in ('condition_id','proxyWallet','family','event_slug'):
        t[col]=t[col].astype('category')
    return t


def test_evaluation_caches_small_summaries_and_matches_full_audit(monkeypatch):
    from pmsports.wallets import skill
    t=_copy_fixture();signals=t[t.proxyWallet=='leader']
    stats=skill.wallet_stats(skill.positions(t))
    full=skill.copy_prices(t,signals,delays=study.DELAYS)
    expected={}
    for delay in study.DELAYS:
        summary=skill.summarize_copy(skill.copy_returns(full,delay))
        expected.update({f'copy_d{delay}_roi':summary['roi'],
            f'copy_d{delay}_ci':f"{summary['ci_lo']:+.3f}..{summary['ci_hi']:+.3f}",
            f'copy_d{delay}_trades':summary['trades']})
    summary=skill.summarize_copy(skill.copy_returns(full,30,stake='proportional'))
    expected.update(copy_d30_prop_roi=summary['roi'],
        copy_d30_prop_ci=f"{summary['ci_lo']:+.3f}..{summary['ci_hi']:+.3f}")
    calls=[];original=skill.copy_prices
    def capture(*args,**kwargs):
        calls.append(kwargs['delays'])
        return original(*args,**kwargs)
    monkeypatch.setattr(skill,'copy_prices',capture)
    cache={}
    result=study.evaluate(t,stats,['leader'],cache)
    assert calls==[(d,) for d in study.DELAYS]
    assert {key:result[key] for key in expected}==expected
    assert cache[('leader',)]==expected
    assert all(np.isscalar(value) for value in cache[('leader',)].values())
    def forbidden(*args,**kwargs):raise AssertionError('same selected wallets should reuse summary')
    monkeypatch.setattr(skill,'copy_prices',forbidden)
    monkeypatch.setattr(skill,'build_groups',forbidden)
    again=study.evaluate(t,stats,['leader','inactive'],cache)
    assert again['selected']==2 and again['active_p2']==1
    assert {key:again[key] for key in expected}==expected


def test_funded_move_is_strictly_between_five_and_ten_minutes(monkeypatch,tmp_path):
    from pmsports.wallets import skill
    t=_copy_fixture()
    # Signal A has a print at exactly five minutes, then at five minutes + one second.
    # Signal B only has prints on the excluded endpoints.
    t.loc[t.condition_id=='a','timestamp']=[100.,400.,401.,700.]
    t.loc[t.condition_id=='b','timestamp']=[1000.,1300.,1600.,1700.]
    t.loc[t.proxyWallet=='leader','size']=2000.
    signals=t[t.proxyWallet=='leader']
    audit=skill.copy_prices(t,signals,delays=(300,))
    assert audit.eligible_ts_d300.tolist()==[400.,1300.]
    assert audit.expiry_ts_d300.tolist()==[700.,1600.]
    assert audit.fill_ts_d300.iloc[0]==401. and pd.isna(audit.fill_ts_d300.iloc[1])
    result=study.big_trade_signal(t,thresholds=(1000,),delays=())
    all_rows=result[result.phase=='all'].iloc[0]
    assert all_rows['funded_move_5_10min']==pytest.approx(.1)
    assert all_rows['move_funded_signals']==1 and all_rows['trades']==2
    assert 'price_move_5min' not in result
    monkeypatch.setattr(report,'OUT',tmp_path)
    rules=pd.DataFrame(columns=['family','rule','selected','active_p2','p1_roi','p1_median_z',
        'p2_roi','p2_roi_net_fee','copy_d0_roi','copy_d5_roi','copy_d30_roi','copy_d30_ci','copy_d60_roi','copy_d30_prop_roi'])
    text=report._render(t,pd.DataFrame(),rules,pd.DataFrame(),result,pd.DataFrame(),pd.DataFrame(),'2026-01-01')
    assert 'strictly after signal+300 seconds and before signal+600 seconds' in text
    assert 'only for actually funded equal-target orders' in text
    assert 'move_funded_signals' in text and '~0 means no information' not in text
