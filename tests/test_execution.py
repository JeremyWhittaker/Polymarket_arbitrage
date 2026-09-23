import numpy as np
import pandas as pd
import pytest
from pmsports.execution import TapeReplay, prior_notional, panel_entries
from pmsports.panel import attach_execution, state_rows
from pmsports.analysis import calibration
from pmsports.wallets import skill
from pmsports.research import h_esports_break_overreaction as esports


def tape():
    return pd.DataFrame(dict(m=[1]*6, s=[0,0,1,0,0,0], ts=[10.,13.,14.,15.,16.,20.],
        q=[.5]*6, size=[100.,100.,100.,100.,3.,100.], w=[1,2,2,1,2,2], fee_rate=[.05]*6))


def order(**kw):
    return dict(m=1,s=0,signal_ts=10.,leader_w=1,event='g',expiry_ts=20.,budget_usd=100.,y=1.,**kw)


def test_strict_delay_side_wallet_capacity_partial_and_no_fill():
    o = pd.DataFrame([order(), order(), {**order(), 'm':2}])
    r = TapeReplay(tape()).replay(o, event_cap_usd=1000)
    assert r.fill_ts.iloc[0] == 16
    assert r.shares.tolist() == [3,0,0]
    assert r.status.tolist() == ['partial','unfilled','unfilled']
    assert r.fee_usd.iloc[0] == pytest.approx(3*.05*.5*.5)
    assert r.pnl_usd.iloc[0] == pytest.approx(3-1.5-.0375)
    assert r.roi.iloc[0] == pytest.approx(r.pnl_usd.iloc[0]/r.cost_usd.iloc[0])


def test_zero_delay_not_signal_and_remaining_shares_and_fixed_quantity():
    t = tape()
    o = pd.DataFrame([{**order(), 'target_shares': 1}, {**order(), 'target_shares': 2}])
    r = TapeReplay(t).replay(o, delay_s=0, event_cap_usd=1000)
    assert r.fill_ts.tolist() == [13,13]
    assert r.shares.tolist() == [1,2]
    assert r.status.tolist() == ['filled','filled']
    assert r.print_id.nunique() == 1
    assert r.available_shares.tolist() == [100,99]


def test_chronological_fill_priority_not_input_order():
    t = pd.DataFrame(dict(m=[1,1],s=[0,0],ts=[20.,40.],q=[.5,.5],size=[2.,100.],w=[2,2],fee_rate=[0.,0.]))
    late = {**order(), 'signal_ts':30., 'expiry_ts':50., 'budget_usd':1.}
    early = {**order(), 'expiry_ts':50., 'budget_usd':1.}
    r = TapeReplay(t).replay(pd.DataFrame([late,early]),event_cap_usd=1)
    assert r.cost_usd.tolist() == [0,1]
    assert r.status.tolist() == ['event_cap','filled']


def test_future_data_invariance_expiry_and_unknown_fees():
    t = tape()
    r = TapeReplay(t).replay(pd.DataFrame([order()]))
    future = pd.concat([t,pd.DataFrame([{**t.iloc[-1].to_dict(),'ts':30.,'q':.99,'size':1e9}])])
    pd.testing.assert_frame_equal(r,TapeReplay(future).replay(pd.DataFrame([order()])))
    bad = t.copy(); bad['fee_rate'] = np.nan
    assert TapeReplay(bad).replay(pd.DataFrame([order()])).status.iloc[0] == 'unknown_fee'
    empty = TapeReplay(t.iloc[:0]).replay(pd.DataFrame([order()]))
    assert empty.status.iloc[0] == 'unfilled'
    assert TapeReplay(t).replay(pd.DataFrame(columns=['m','s','signal_ts'])).empty


@pytest.mark.parametrize('field,value',[('target_shares',-1),('target_shares',np.nan),('budget_usd',np.inf),('budget_usd',-1),('receipt_ts',9),('delay_s',-1),('expiry_ts',np.nan)])
def test_invalid_orders_cannot_create_negative_exposure(field,value):
    r = TapeReplay(tape()).replay(pd.DataFrame([{**order(),field:value}]))
    assert r.status.iloc[0] == 'ineligible'
    assert r.cost_usd.iloc[0] == r.shares.iloc[0] == 0


def test_volume_cutoff_excludes_same_timestamp_and_future():
    t = pd.DataFrame(dict(m=[1,1,1,2],ts=[10,10,20,10],size=[1,100,3,1000],q=[.5]*4))
    assert prior_notional(t).tolist() == [0,0,50.5,0]
    assert prior_notional(t.iloc[:2]).tolist() == [0,0]


def panel_rows():
    return pd.DataFrame(dict(game_pk=[1,1],decision_ts=[10.,30.],state_expiry_ts=[25.,40.],
                              home_won_final=[1.,1.],fee_rate=[.05,.05]))


def test_panel_side_normalization_unknown_asset_staleness_terminal_expiry():
    raw = (np.array([8.,9.,16.,17.,18.,40.,45.]),np.array([.5,.99,.6,.7,.8,.9,.99]),
           np.array([2.,99.,3.,4.,5.,6.,7.]), np.array(['BUY','BUY','SELL','SELL','BUY','BUY','BUY']),
           np.array(['h','unknown','a','h','h','h','a']))
    p = attach_execution(panel_rows(),raw,'h','a')
    assert p.mkt_p.iloc[0] == .5
    assert p.exec_home_p.iloc[0] == .6 # SELL away acquires home
    assert p.exec_away_p.iloc[0] == pytest.approx(.3) # SELL home acquires away
    assert p.exec_home_ts.iloc[0] == 16
    assert pd.isna(p.exec_home_ts.iloc[1]) # terminal at40 forbids equal/later print
    r = panel_entries(p.iloc[:1], [False])
    assert r.entry_price.iloc[0] == pytest.approx(.3)
    assert r.shares.iloc[0] == 4
    stale = panel_rows().iloc[:1].assign(decision_ts=1000.,state_expiry_ts=1100.)
    assert pd.isna(attach_execution(stale,raw,'h','a').mkt_p.iloc[0])


def test_state_rows_retains_terminal_expiry():
    plays = pd.DataFrame(dict(game_pk=[1,1],play_idx=[1,2],inning=[9,9],half=['bottom']*2,
        outs=[1,2],home_score=[0,1],away_score=[0,0],start_ts=[1.,3.],end_ts=[2.,4.],
        on_1b=[False]*2,on_2b=[False]*2,on_3b=[False]*2))
    p = state_rows(plays)
    assert len(p) == 1 and p.state_expiry_ts.iloc[0] == 4


def threshold_tape():
    t = tape().assign(event=1,sport='baseball',in_play=True,y=.5,closed_ts=25.,prior_usd=60000.)
    t['q'] = .75
    return t


def test_threshold_sweep_uses_shared_execution_and_retains_voids(tmp_path,monkeypatch):
    t = threshold_tape()
    b = calibration.threshold_bets(t,.74)
    assert b.signal_ts.iloc[0] == 10
    assert b.fill_ts.iloc[0] == 16
    assert b.shares.iloc[0] == 3
    assert b.y.iloc[0] == .5
    monkeypatch.setattr(calibration,'LEDGERS',tmp_path)
    result = calibration.threshold_sweep(t, points=[74], export_ledgers=True)
    assert result.bets.sum() == 1
    import json
    doc = json.loads((tmp_path/'favorite_at_74.json').read_text())
    assert len(doc['rows']) == doc['n_total_trades'] == 1
    assert 'signal_ts' in doc['columns'] and not doc['truncated']
    empty = calibration.threshold_bets(t,.99)
    assert empty.empty
    calibration.write_ledger(t,.99,bets=empty)
    assert json.loads((tmp_path/'favorite_at_99.json').read_text())['n_total_trades'] == 0


def test_wallet_copy_zero_delay_other_wallet_and_cash_capacity():
    t = tape().rename(columns={'m':'condition_id','s':'side_idx','ts':'timestamp','w':'proxyWallet'}).assign(y=1.,event_slug='g')
    for col in ('condition_id','proxyWallet'):
        t[col] = t[col].astype('category')
    meta = pd.DataFrame({'closed_ts': 100.}, index=t.condition_id.cat.categories)
    r = skill.copy_prices(t,t.iloc[[0,0]],delays=[0,3],horizon=7,meta=meta)
    assert r.q_d0.notna().all()
    assert r.fill_ts_d0.gt(r.timestamp).all()
    assert r.shares_d3.sum() == 3
    ret = skill.copy_returns(r,3)
    assert ret.w.sum() == pytest.approx(3*.5125)
    assert ret.copy_shares.sum() == 3


def test_esports_no_quote_fallback_and_partial_signal_audit():
    ts=np.array([9.,13.,14.]); sides=np.array([0,0,1]); q=np.array([.5,.5,.5]); size=np.array([10.,10.,2.]); rates=np.array([.05]*3)
    assert esports.entry(ts,sides,q,size,rates,0,10,13,20,8,10) is None
    assert esports.quote_proxy(ts,sides,q,rates,0,8,10) is None
    e = pd.DataFrame(dict(event_slug=['a','b'],t_end=[0.,0.],entT_q=[.5,np.nan],entT_sh=[2.,np.nan],
        entT_ts=[154.,np.nan],entT_rate=[.05,np.nan],entT_how=['window','unfilled'],fee_mkt=[.05,.05],yT=[1.,1.]))
    b = esports.bets(e,'T',pd.Series([True,True]))
    assert b.shares.tolist() == [2,0]
    assert b.status.tolist() == ['partial','unfilled']
    s=esports.summ(b)
    assert s['signals']==2 and s['bets']==1 and s['unfilled']==1
    assert s['capital_usd']==pytest.approx(1.025)


def test_h1_remains_descriptive_without_execution_columns():
    from pmsports.analysis.hypotheses import h1_calibration
    rng=np.random.default_rng(912)
    p=rng.uniform(.2,.8,180)
    pre=pd.DataFrame(dict(pre_p=p,home_won_final=rng.random(len(p))<p,fee_rate=.05,event_date='2025-06-01'))
    r=h1_calibration(pre)
    assert r['n_games']==len(pre)
    assert 'Descriptive' in r['execution_note']


def test_all_fifty_threshold_audits_exist_without_truncation(tmp_path,monkeypatch):
    import json
    monkeypatch.setattr(calibration,'LEDGERS',tmp_path)
    t=threshold_tape()
    out=calibration.threshold_sweep(t,export_ledgers=True)
    assert len(out)==100
    assert len(list(tmp_path.glob('favorite_at_*.json')))==50
    for path in tmp_path.glob('*.json'):
        d=json.loads(path.read_text())
        assert d['n_total_trades']==len(d['rows'])
        assert d['truncated'] is False


def test_empty_wallet_positions_preserves_schema():
    t=pd.DataFrame({name:pd.Series(dtype='category') for name in ('proxyWallet','condition_id','family','event_slug')})
    out=skill.positions(t)
    assert out.empty
    assert {'cost','pnl_net','proxyWallet','condition_id','family','event'}.issubset(out)
