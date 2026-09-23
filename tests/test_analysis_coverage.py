import numpy as np
import pandas as pd
import pytest
from pmsports.analysis import hypotheses as H, thresholds as T


def baseline():
    return pd.DataFrame(dict(season=[2024]*40+[2025]*40, inning=[5]*80,
        half=['top']*80, outs=[0]*80, bases=[0]*80, diff=[1]*80,
        home_won_final=[False]*40+[True]*40, game_pk=range(80), checkpoint=[True]*80))


def model_rows():
    return pd.DataFrame(dict(event_date=['2025-05-01','2026-05-01'],mkt_p=[.6,.6],pre_p=[.5,.5],
        home_won_final=[True,False],mkt_staleness=[1,1],inning=[5,5],half=['top','top'],
        outs=[0,0],bases=[0,0],diff=[1,1]))


def test_training_state_rate_cannot_encode_own_season_outcomes():
    b=baseline();p=model_rows();a=H.causal_model_rows(p,b,'2026-01-01')
    assert a.we.iloc[0]==pytest.approx(5/50)
    b.loc[b.season==2025,'home_won_final']=False
    changed=H.causal_model_rows(p,b,'2026-01-01')
    assert a.we.iloc[0]==changed.we.iloc[0]
    assert a.we.iloc[1]!=changed.we.iloc[1] # prior season is legitimately available in 2026
    later=baseline().assign(season=2026,home_won_final=True)
    pd.testing.assert_frame_equal(a,H.causal_model_rows(p,pd.concat([baseline(),later]),'2026-01-01'))


def test_model_requires_known_finite_pregame_and_fresh_reference():
    p=pd.concat([model_rows()]*3,ignore_index=True)
    p.loc[0,'pre_p']=np.nan;p.loc[1,'pre_p']=np.inf;p.loc[2,'mkt_staleness']=121
    assert H.causal_model_rows(p,baseline(),'2026-01-01').index.tolist()==[3,4,5]


def test_h2_uses_same_priced_observations_for_outcomes_and_prices():
    p=pd.concat([model_rows()]*2,ignore_index=True).assign(game_pk=range(4),checkpoint=True)
    p.loc[0,'mkt_p']=np.nan;p.loc[1,'mkt_staleness']=121
    p.loc[2,'home_won_final']=True;p.loc[3,'home_won_final']=False
    r=H.h2_state_tables(p,baseline())['market_cells'].iloc[0]
    assert r.n==2 and r.actual_win==.5 and r.mkt_mean==.6
    assert 'residual_ci_lo' in r.index and 't_stat' not in r.index


def test_adjusted_inning_model_excludes_missing_input_without_dropping_unadjusted_rows(monkeypatch):
    p=pd.concat([model_rows()]*120,ignore_index=True).assign(game_pk=range(240),checkpoint=True,state_ts=range(240))
    p.loc[0,'pre_p']=np.nan
    monkeypatch.setattr(T.pd,'read_parquet',lambda path:p.copy() if path.name=='panel.parquet' else baseline())
    monkeypatch.setattr(T,'_prior_season_fair',lambda rows,base:np.full(len(rows),.8))
    monkeypatch.setattr(T,'_execution_summary',lambda *args,**kw:dict(signals=len(args[0]),n=0))
    fitted=[]
    class FiniteFit:
        def __init__(self,y,x):
            assert np.isfinite(np.asarray(x)).all()
            fitted.append(len(y))
        def fit(self,**kw):return self
        def predict(self,x):
            assert np.isfinite(np.asarray(x)).all()
            return np.full(len(x),.7)
    monkeypatch.setattr(T.sm,'Logit',FiniteFit)
    r=T.inning_discounts()
    assert r['n_checkpoints']==240 and r['model_input_exclusions']==1
    assert fitted==[119] and len(r['adjusted'])==10


def test_price_point_projection_preserves_full_cash_statistics():
    from pmsports.analysis.calibration import per_point
    frame=pd.DataFrame(dict(q=[.741,.749,.751,.759],y=[1.,0.,1.,0.],size=[10.,20.,30.,40.],
        fee_rate=[0.,.05,0.,.03],event=['a','b','c','d'],sport=['baseball']*4,
        unrelated_payload=['large']*4))
    r=per_point(frame,by='sport',min_fills=1).set_index('pt')
    assert r.loc[74,'fills']==2 and r.loc[75,'fills']==2
    assert r.loc[74,'usd']==pytest.approx(10*.741+20*.749)
    capital=10*.741+20*(.749+.05*.749*(1-.749))
    expected=(10-capital)/capital
    assert r.loc[74,'roi']==pytest.approx(expected)
    from pmsports.analysis.calibration import calibration_table
    many=pd.concat([frame]*100,ignore_index=True).assign(in_play=True)
    table=calibration_table(many,by_sport=False)
    fees=many['size']*many.fee_rate*many.q*(1-many.q)
    cost=(many['size']*many.q+fees).sum()
    observed=(table.roi_after_fee*table.capital_usd).sum()/table.capital_usd.sum()
    assert observed==pytest.approx(((many['size']*many.y).sum()-cost)/cost)


def test_full_tape_retains_mirror_and_high_price_boundaries(monkeypatch):
    from pmsports.analysis import calibration as C
    prices=[-2.,.009,.01,.499,.50,.995,.999,1.]
    frame=pd.DataFrame(dict(m=[1]*8,s=[0]*8,w=list(range(8)),ts=list(range(8)),
        q=prices,y=[1.]*8,size=[1.]*8,fee_rate=[0.]*8,in_play=[False]*8))
    monkeypatch.setattr(C,'markets',lambda:pd.DataFrame(dict(m=[1],family=['baseball'],
        event_slug=['g'],game_start_ts=[10.],closed_ts=[20.])))
    monkeypatch.setattr(C,'fills',lambda **kw:frame.copy())
    loaded,_=C._load()
    assert loaded.q.tolist()==prices[1:-1]
    assert loaded.prior_usd.iloc[0]==0
    points=C.per_point(loaded,min_fills=1)
    assert points.pt.tolist()==[50,99]
    assert points.fills.tolist()==[1,2]


def test_float32_nominal_cent_boundaries_keep_their_bins():
    from pmsports.analysis.calibration import per_point
    prices=(np.arange(50,100)/100.).astype(np.float32)
    frame=pd.DataFrame(dict(q=prices,y=np.ones(50),size=np.ones(50),
        fee_rate=np.zeros(50),event=np.arange(50)))
    result=per_point(frame,min_fills=1)
    assert result.pt.tolist()==list(range(50,100))
    assert result.fills.eq(1).all()


def test_bounded_volume_matches_full_history_with_ties_and_unsorted_rows():
    from pmsports.analysis.calibration import bounded_prior_notional
    from pmsports.execution import prior_notional
    rng = np.random.default_rng(19)
    tape = pd.DataFrame({'m':np.repeat(np.arange(7),31), 'ts':rng.integers(0,9,217),
                         'q':rng.uniform(.01,.99,217), 'size':rng.uniform(1,100,217)})
    for frame in (tape, tape.sample(frac=1,random_state=5)):
        for limit in (1,31,50,1000):
            np.testing.assert_allclose(bounded_prior_notional(frame,limit),prior_notional(frame),rtol=1e-12,atol=1e-10)
    assert bounded_prior_notional(tape.iloc[:0]).size==0


def test_whale_descriptive_cent_bins_preserve_float32_boundaries():
    from pmsports.analysis.whale_prices import ev_table
    prices=np.arange(50,100,dtype=float)/100
    q=np.repeat(prices.astype(np.float32),200)
    f=pd.DataFrame({'q':q,'y':1.,'fee_rate':0.,'ev':np.arange(len(q)),
                    'm':np.arange(len(q)),'ts':np.arange(len(q))})
    result=ev_table(f)
    assert result.price_c.tolist()==list(range(50,100))
    assert result.bets.eq(200).all()
    np.testing.assert_allclose(result.cost_per_share,prices,rtol=1e-6)
