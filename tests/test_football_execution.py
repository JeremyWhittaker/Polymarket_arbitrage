import importlib.util
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

BASE=Path(__file__).resolve().parents[1]/'pmsports/research'
def module(slug):
    spec=importlib.util.spec_from_file_location(slug.replace('-','_'),BASE/('h_'+slug+'.py'))
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
H=module('fb-ladder-monotonicity-arb')
W=module('fb-key-number-wp-step')
G=module('fb-ladder-key-number-gap')


def signal(**kw):
    return dict(event='g',event_slug='g',event_a='g',event_b='g',holdout=True,signal_ts=0.,expiry_ts=10.,
        cid_a='a',cid_b='b',s_a=0,s_b=1,ref_a=.4,ref_b=.4,rate_a=0.,rate_b=0.,
        y_a=0.,y_b=1.,closed_a=100.,closed_b=100.,**kw)


def tape():
    return pd.DataFrame([
        dict(m='a',s=0,ts=3.,q=.99,size=1000.,print_id='equal-eligibility'),
        dict(m='a',s=0,ts=4.,q=.4,size=100.,print_id='a-entry'),
        dict(m='b',s=1,ts=5.,q=.4,size=2.,print_id='b-entry'),
        dict(m='a',s=1,ts=14.,q=.4,size=3.,print_id='a-exit'),
        dict(m='a',s=0,ts=20.,q=.1,size=10000.,print_id='later-better'),
    ])


def test_fixed_quantity_partial_hedge_partial_unwind_and_residual_payoff():
    d=H.pair_replay(pd.DataFrame([signal()]),tape()).iloc[0]
    assert d.target_shares==125 # fixed from prior refs, not min(future sizes)
    assert d.shares_a==100 and d.shares_b==2 and d.matched_shares==2
    assert d.fill_ts_a==4 and d.sold_a==3 and d.residual_a==95
    assert d.stake_usd==pytest.approx(40.8)
    assert d.payout==pytest.approx(3.8) and d.pnl_usd==pytest.approx(-37)
    assert d.matched_pnl==pytest.approx(.4) and d.unmatched_pnl==pytest.approx(-37.4)
    assert d.status=='unmatched'


def test_single_leg_not_discarded_and_exit_waits_for_hedge_timeout():
    t=tape();t=t[t.m!='b']
    # This opposite print happens before hedge failure is known and cannot unwind it.
    early=pd.DataFrame([dict(m='a',s=1,ts=8.,q=.1,size=1000.,print_id='early')])
    d=H.pair_replay(pd.DataFrame([signal()]),pd.concat([t,early])).iloc[0]
    assert d.shares_a==100 and d.shares_b==0 and d.sold_a==3 and d.residual_a==97
    assert d.pnl_usd==pytest.approx(-38.2)


def test_print_capacity_shared_for_duplicate_signals_and_api_overlap():
    t=pd.concat([tape(),tape()])
    sig=pd.DataFrame([signal(),signal()])
    d=H.pair_replay(sig,t,event_cap=10000)
    assert d.shares_a.sum()==100 and d.shares_b.sum()==2
    assert d.sold_a.sum()<=3
    assert len(d)==2 and d.status.iloc[1]=='unfilled'


def test_event_cap_fees_unknown_payout_and_mismatched_event():
    sig=signal();sig.update(rate_a=.05,rate_b=.05)
    d=H.pair_replay(pd.DataFrame([sig]),tape(),event_cap=1).iloc[0]
    assert d.stake_usd+d.entry_fee_a+d.entry_fee_b<=1+1e-9
    assert d.fee_usd>0
    for field,val in [('event_b','another'),('y_a',.25),('rate_a',np.nan),('ref_a',np.nan)]:
        bad={**signal(),field:val}
        z=H.pair_replay(pd.DataFrame([bad]),tape()).iloc[0]
        assert z.status=='ineligible' and z.cost_usd==0


def test_reversed_moneyline_and_spread_orientation_and_payoff_proof():
    assert H.orientation('Patriots','Seahawks','New England Patriots','Seattle Seahawks')==0
    assert H.orientation('Seahawks','Patriots','New England Patriots','Seattle Seahawks')==1
    assert H.orientation('Unknown','Patriots','New England Patriots','Seattle Seahawks') is None
    assert [H.secondary_payoff(m) for m in (-1,0,1,2)]==[1,.5,0,1]
    # Correct monotone pair is Yes(easier)+No(harder): minimum1 for nonvoid contracts.
    assert min(float(m>2.5)+float(m<=3.5) for m in range(-20,21))==1
    # Independently voided legs are not protected by that nonvoid proof.
    assert .5+0<1


def test_pair_future_prices_cannot_change_prescribed_quantity_or_signal():
    sig=pd.DataFrame([signal()]);future=tape().copy();future.loc[future.print_id=='later-better','size']=1e12
    a=H.pair_replay(sig,tape());b=H.pair_replay(sig,future)
    pd.testing.assert_frame_equal(a,b)
    assert H.pair_replay(sig,tape().iloc[:0]).status.iloc[0]=='unfilled'
    assert H.pair_replay(sig.iloc[:0],tape()).empty


def test_wp_pick_does_not_use_future_entry_price_or_has_price():
    rows=pd.DataFrame(dict(am=[3,3],ref_age=[1.,1.],ref_lead=[.7,.7],fit_lead=[.7,.7],espn_id=['g','g'],play_idx=[1,2],
        lead_home=[True,True],home_idx=[0,0],home_abbr=['H','H'],away_abbr=['A','A'],ex_home_p=[np.nan,.7],has_price=[False,True]))
    a=W.pick(rows);rows['ex_home_p']=[.99,.01];rows['has_price']=[True,False];b=W.pick(rows)
    assert a.play_idx.tolist()==b.play_idx.tolist()==[1]
    assert a.s.iloc[0]==0 # reversed home index retained


def test_gap_prior_season_pmf_never_uses_current_season_outcome():
    training=pd.DataFrame(dict(season=[2024]*30+[2025]*30,lg=['nfl']*60,bucket=[2]*60,margin_side=[3]*3+[4]*27+[3]*30))
    scores=pd.DataFrame(dict(season=[2023]*100,lg=['nfl']*100,margin=[3]*10+[4]*90))
    a=G.prior_pmf('nfl',2025,3,.6,training,scores)
    training.loc[training.season==2025,'margin_side']=100
    assert a==G.prior_pmf('nfl',2025,3,.6,training,scores)
    assert a[0]==.1 and a[1]==30


def test_bootstrap_tail_mass_labeled_not_null_pvalue():
    d=pd.DataFrame(dict(event_slug=list('abcde'),cost_usd=[1]*5,pnl_usd=[1]*5))
    assert W.bootstrap_tail_mass(d)==0
    assert 'NOT a null-test p-value' in W.bootstrap_tail_mass.__doc__


def test_gap_signal_window_excludes_future_entry_prices():
    kickoff=pd.Timestamp('2025-09-01',tz='UTC').timestamp();decision=kickoff-600
    rungs=pd.DataFrame([
        dict(event_slug='g',side='home',line=2.5,condition_id='a',yes_idx=0,espn_date=kickoff,
             espn_id='game',lg='nfl',holdout=False,fee_rate=0.,pay_yes=1.,pay_no=0.,closed_ts=kickoff+1000,market_slug='a'),
        dict(event_slug='g',side='home',line=3.5,condition_id='b',yes_idx=0,espn_date=kickoff,
             espn_id='game',lg='nfl',holdout=False,fee_rate=0.,pay_yes=0.,pay_no=1.,closed_ts=kickoff+1000,market_slug='b')])
    rows=[dict(m=m,s=0,ts=decision-j,q=p,size=10.,print_id=m+str(j)) for m,p in [('a',.6),('b',.5)] for j in (1,2,3)]
    t=pd.DataFrame(rows)
    training=pd.DataFrame(dict(event_slug=['prior']*30+['g'],side=['home']*31,p_side=[.6]*31,
        season=[2024]*30+[2025],lg=['nfl']*31,bucket=[2]*31,margin_side=[3]*31))
    scores=pd.DataFrame(dict(season=[2023]*100,lg=['nfl']*100,margin=[3]*100))
    a,_=G.build_signals(rungs,t,training,scores)
    future=pd.DataFrame([dict(m='a',s=0,ts=decision+1,q=.99,size=1e9,print_id='future')])
    b,_=G.build_signals(rungs,pd.concat([t,future]),training,scores)
    pd.testing.assert_frame_equal(a,b)
    assert len(a)==1 and a.ref_a.iloc[0]==.6


def test_earlier_eligible_unwind_wins_same_print_over_later_entry():
    later={**signal(),'event':'g2','event_slug':'g2','event_a':'g2','event_b':'g2',
        'signal_ts':10.5,'expiry_ts':30.,'cid_a':'a','s_a':1,'cid_b':'c'}
    t=tape();t=t[t.m!='b']
    d=H.pair_replay(pd.DataFrame([signal(),later]),t)
    # First hedge expires10: sale eligible13. Second entry eligible13.5.
    # Both next see timestamp14, whose three shares belong to the earlier order.
    assert d.sold_a.iloc[0]==3
    assert d.shares_a.iloc[1]==0


def test_raw_prefix_states_ignore_final_game_quality_and_future_clocks():
    games=pd.DataFrame([dict(espn_id='g',m=1,condition_id='c',home_idx=0,lg='nfl',league='nfl',fee_rate=0.,
        espn_date=0.,closed_ts=1000.,home_abbr='H',away_abbr='A',game_ok=False,q_final_ok=False)])
    raw=pd.DataFrame([dict(espn_id='g',play_idx=i,wallclock=float(i+1),period=3,clock_s=900-i,
        home_score=3.,away_score=0.,type='Rush',end_home=1,reg_s=1000-i) for i in range(42)])
    a=W.causal_states(raw,games)
    games['game_ok']=True;games['q_final_ok']=True
    future=pd.DataFrame([{**raw.iloc[-1].to_dict(),'play_idx':42,'wallclock':-99999,'home_score':999}])
    b=W.causal_states(pd.concat([raw,future]),games)
    cols=['play_idx','margin','stable','prefix_ok','up_off_home']
    pd.testing.assert_frame_equal(a[cols],b[cols])
    assert a.prefix_ok.sum()==3 and a.margin.iloc[-1]==3


def test_fully_unwound_pair_exit_clock_is_actual_sale_not_settlement():
    t=tape();t=t[t.m!='b'].copy();t.loc[t.print_id=='a-exit','size']=100
    d=H.pair_replay(pd.DataFrame([signal()]),t).iloc[0]
    assert d.sold_a==100 and d.residual_a==0
    assert d.exit_ts==14 and d.exit_price==pytest.approx(.6)
