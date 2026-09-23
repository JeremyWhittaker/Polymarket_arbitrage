from types import SimpleNamespace
import numpy as np
import pandas as pd
from pmsports.execution import TapeReplay
from pmsports.research.h_baseball_continuation import (
    normalize,yrfi_side,attach_states,pair_payoff,pair_implied,prior_games,
    prior_reference,replay_pairs,nrfi_orders,totals_orders,state_features,prior_facts,
    canonical_orientation,economic_exit)


def test_asset_orientation_and_sell_complement():
    r=SimpleNamespace(tok0='a',tok1='b',condition_id='m',fee_rate=.05)
    raw=pd.DataFrame({'asset':['a','b','wrong'],'side':['BUY','SELL','BUY'],
        'outcomeIndex':[1,0,0],'timestamp':[10,20,30],'price':[.4,.3,.1],'size':[5,7,99]})
    t=normalize(raw,r)
    assert t.s.tolist()==[0,0]
    assert np.allclose(t.q,[.4,.7])
    assert len(t)==2


def test_nrfi_both_phrasings_and_outcome_order():
    assert yrfi_side('NRFI: A vs B','','Yes','No')==1
    assert yrfi_side('Will there be a run scored in the first inning?','','Yes','No')==0
    assert yrfi_side('NRFI: A vs B','','No','Yes')==0
    assert yrfi_side('unknown','','Yes','No') is None


def test_state_alignment_and_terminal_expiry():
    states=pd.DataFrame({'state_ts':[10.,20.],'state_expiry_ts':[20.,30.],
        'inning':[2,2],'half':['top','bottom'],'outs':[2,0],'bases':[1,0],
        'diff':[0,1],'home_score':[0,1],'away_score':[0,0]})
    t=pd.DataFrame({'ts':[9.,10.,19.,20.,29.,30.]})
    z=attach_states(t,states)
    assert z.ts.tolist()==[10.,19.,20.,29.]
    assert z.outs.tolist()==[2,2,0,0]


def test_runline_pair_payoff_and_original_algebra_correction():
    assert pair_payoff([-3,-2,-1,0,1,2,3]).tolist()==[1,1,2,2,2,1,1]
    assert np.isclose(pair_implied(1.286),.286)
    assert not np.isclose(pair_implied(1.286),2-1.286)


def test_references_strict_prior_and_prior_season():
    t=pd.DataFrame({'s':[1,1,1],'ts':[10.,19.,20.],'q':[.5,.6,.9]})
    assert prior_reference(t,1,20).q==.6
    games=pd.DataFrame({'game_pk':[1,2,3], 'game_ts':[pd.Timestamp(f'{y}-06-01',tz='UTC').timestamp() for y in [2024,2025,2026]]})
    assert prior_games(games,2026).game_pk.tolist()==[1,2]


def test_partial_pair_keeps_failed_leg_and_unwinds_only_excess():
    tape=pd.DataFrame({'m':['a','b','a'],'s':[1,1,0],'ts':[104.,105.,204.],
        'q':[.5,.6,.3],'size':[10.,2.,3.],'fee_rate':[.05]*3,'print_id':['a1','b1','a2']})
    orders=pd.DataFrame({'m':['a','b'],'s':[1,1],'signal_ts':[100.,100.],
        'expiry_ts':[200.,200.],'game_pk':[1,1],'event':[1,1],'target_shares':[10.,10.],
        'budget_usd':[50.,50.],'y':[0.,1.],'fee_rate':[.05,.05]})
    b=replay_pairs(TapeReplay(tape),orders)
    assert b.shares.tolist()==[10.,2.]
    assert b.exit_shares.tolist()==[3.,0.]
    assert b.residual_shares.tolist()==[7.,2.]
    assert np.isclose(b.iloc[0].payout,2.1)
    assert b.pnl_usd.sum()<0
    assert b.status.tolist()==['filled','partial']
    assert (b.fill_ts>b.eligible_ts).all()


def test_no_hedge_future_size_does_not_resize_first_leg():
    t=pd.DataFrame({'m':['a'],'s':[1],'ts':[104.],'q':[.5],'size':[7.],'fee_rate':[0.]})
    orders=pd.DataFrame({'m':['a','b'],'s':[1,1],'signal_ts':[100.,100.],
        'expiry_ts':[200.,200.],'game_pk':[1,1],'event':[1,1],'target_shares':[10.,10.],
        'budget_usd':[50.,50.],'y':[0.,1.],'fee_rate':[0.,0.]})
    b=replay_pairs(TapeReplay(t),orders)
    assert b.shares.tolist()==[7.,0.]
    assert np.isclose(b.pnl_usd.sum(),-3.5)
    assert b.residual_shares.tolist()==[7.,0.]


def test_nrfi_uses_run_boolean_and_known_earlier_liquidity():
    year=2026; start=pd.Timestamp('2026-06-01',tz='UTC').timestamp()
    markets=pd.DataFrame([dict(condition_id='n',kind='nrfi',season=year,o0='Yes',o1='No',
        game_start_ts=start,game_pk=9,event_slug='e',market_slug='nrfi',fee_rate=.05,y0=1.,y1=0.)])
    meta=pd.DataFrame([dict(condition_id='n',question='Will there be a run scored in the first inning?',description='',yes_means='run')]).set_index('condition_id')
    games=pd.DataFrame({'game_pk':list(range(101)),'game_ts':[pd.Timestamp('2025-06-01',tz='UTC').timestamp()]*100+[start], 'r1':[0,3]*50+[9]})
    tape=pd.DataFrame({'ts':[start-1200+i*10 for i in range(8)],'s':[0]*8,'q':[.4]*8})
    orders,diag=nrfi_orders(markets,{'n':tape},meta,games)
    assert diag['prior_rates']['2026']['base']==.5
    assert len(orders)==1
    assert orders[0]['signal_ts']==start-1150
    assert orders[0]['s']==0


def test_totals_fit_never_uses_traded_season_outcomes():
    rows=[]
    for year in [2025,2026]:
        for i in range(20):
            rows.append(dict(kind='total',pre_n=5,inning=3,half='top',home_score=0,away_score=0,
                line=9.,season=year,s=0,q=.2,p0=.2,y0=float(i%2),y1=float(1-i%2),
                game_pk=year*100+i,ts=1000.+i,state_expiry_ts=2000.,m=f'{year}-{i}',
                event_slug=f'e{year}-{i}',market_slug='total',game_start_ts=0.,fee_rate=.05))
    obs=pd.DataFrame(rows)
    orders,a=totals_orders(obs)
    obs.loc[obs.season.eq(2026),'y0']=1-obs.loc[obs.season.eq(2026),'y0']
    _,b=totals_orders(obs)
    assert a['2025']['status']=='unavailable'
    assert a['2026']==b['2026']
    assert all(r['season']==2026 and r['s']==0 for r in orders)
    assert len(orders)==20


def test_empty_raw_market_does_not_poison_replay_numeric_dtype():
    r=SimpleNamespace(tok0='a',tok1='b',condition_id='m',fee_rate=.05)
    empty=normalize(pd.DataFrame(),r)
    raw=pd.DataFrame({'asset':['a'],'side':['BUY'],'timestamp':[10.],'price':[.4],'size':[5.]})
    tape=pd.concat([empty,normalize(raw,r)],ignore_index=True)
    assert len(TapeReplay(tape).tape)==1


def test_first_inning_prior_includes_run_on_final_out_checkpoint():
    b=pd.DataFrame({'game_pk':[1,1,1],'inning':[1,1,2],'half':['top','bottom','top'],
        'checkpoint':[False,False,True],'home_score':[0,0,1],'away_score':[0,0,0]})
    schedules=pd.DataFrame({'game_pk':[1],'game_ts':[100.],'home_score':[3],'away_score':[2]})
    facts=prior_facts(b,schedules)
    assert facts.r1.tolist()==[1]
    assert facts.final_margin_home.tolist()==[1]


def test_missing_prior_outcomes_cannot_be_false_losses():
    games=pd.DataFrame({'game_pk':[1,2,3,4],'game_ts':[pd.Timestamp('2025-06-01',tz='UTC').timestamp()]*4,
        'r1':[1,np.nan,0,0],'final_margin_home':[2,2,np.nan,1]})
    assert prior_games(games,2026).game_pk.tolist()==[1,4]


def test_canonical_over_and_home_cover_orientation_from_metadata():
    total=SimpleNamespace(kind='total',o0='Over',o1='Under')
    assert canonical_orientation(total,None)
    assert not canonical_orientation(SimpleNamespace(kind='total',o0='Under',o1='Over'),None)
    market=SimpleNamespace(kind='spread_home',home_name='Los Angeles Dodgers',away_name='San Diego Padres',o0='Yes',o1='No')
    meta=SimpleNamespace(question='Spread: Dodgers (-1.5)',description='This market will resolve to “Yes” if the Dodgers win the game by 2 or more runs.')
    assert canonical_orientation(market,meta)
    meta.question='Spread: Padres (-1.5)'
    assert not canonical_orientation(market,meta)


def test_economic_exit_distinguishes_full_partial_unwind_and_no_entry():
    r=SimpleNamespace(shares=10.,exit_shares=10.,residual_shares=0.,exit_ts=120.,exit_price=.7,closed_ts=500.,y=0.,payout=7.)
    assert economic_exit(r)==('exit_price',120.,.7)
    r.exit_shares=3.;r.residual_shares=7.;r.payout=2.1
    kind,ts,px=economic_exit(r)
    assert kind=='mixed' and ts==500. and np.isclose(px,.21)
    r.shares=0.
    assert economic_exit(r)==('unfilled',None,None)
