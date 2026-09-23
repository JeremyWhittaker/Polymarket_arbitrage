import json
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pandas as pd
import pytest
from pmsports.research import ledger_studies as led
from pmsports.research import ledger_esports_break_overreaction as esports
from pmsports.research import h_esports_break_overreaction as es_study
from pmsports.analysis.favorites import favorite_bets
from pmsports.wallets import report
from pmsports.execution import VERSION


def frame(doc):
    return pd.DataFrame(doc['rows'],columns=doc['columns'])


def nofills(n=2):
    return pd.DataFrame(dict(period='holdout',signal_ts=np.arange(n)+10.,entry_ts=np.nan,entry_price=np.nan,
        shares=0.,stake_usd=0.,fee_usd=0.,cost_usd=0.,payout=0.,pnl_usd=0.,roi=np.nan,
        event='g',status='unfilled'))


def test_full_ledger_over_twenty_thousand_preserves_every_no_fill(tmp_path,monkeypatch):
    monkeypatch.setattr(led,'OUT',tmp_path)
    t=nofills(21003)
    doc=led._write({'slug':'all','_n_total':1},t)
    assert doc['n_total_trades']==len(doc['rows'])==21003
    assert not doc['truncated']
    assert doc['headline']['holdout']['signals']==21003
    assert doc['headline']['holdout']['bets']==0
    assert doc['headline']['holdout']['roi'] is None
    d=frame(doc)
    assert d.entry_ts.isna().all() and d.date.notna().all()
    assert d.cost_usd.sum()==0
    assert '_n_total' not in doc


def pregame():
    return pd.DataFrame(dict(condition_id=['c1','c2'],event_slug=['g1','g2'],market_slug=['m1','m2'],
        family='baseball',league='mlb',game_start_ts=[700.,800.],closed_ts=[1000.,1100.],
        decision_ts=[100.,200.],entry_delay_s=3.,execution='first_later_print_partial_proxy',
        p0=[.7,.6],o0='A',o1='B',y0=[1.,.5],y1=[0.,.5],
        ask0=[.72,np.nan],ask1=[.28,np.nan],entry_size0=[2.,0.],entry_size1=[3.,0.],
        entry_ts0=[104.,np.nan],entry_ts1=[105.,np.nan],fee_rate=.05,pre_usd=60000.))


@pytest.mark.parametrize('side,shares,price',[('favorite',2,.72),('underdog',3,.28)])
def test_favorite_adapters_use_actual_partial_cash_and_keep_no_fill(tmp_path,monkeypatch,side,shares,price):
    monkeypatch.setattr(led,'OUT',tmp_path)
    monkeypatch.setattr(led,'cached_pregame_prices',pregame)
    doc=led.favorites_ledger(side)
    d=frame(doc)
    assert len(d)==2 and d.status.tolist()==['partial','unfilled']
    assert d.shares.iloc[0]==shares
    assert d.stake_usd.iloc[0]==pytest.approx(shares*price)
    assert d.entry_ts.iloc[0] in (104,105)
    assert pd.isna(d.entry_ts.iloc[1]) and d.cost_usd.iloc[1]==0
    assert d.roi.iloc[0]==pytest.approx(d.pnl_usd.iloc[0]/d.cost_usd.iloc[0])
    assert doc['headline']['dev']['roi']==pytest.approx(d.pnl_usd.sum()/d.cost_usd.sum())
    assert d.exit_ts.iloc[0]==1000 and pd.isna(d.exit_ts.iloc[1])
    assert pd.isna(d.exit_price.iloc[1]) and d.exit_kind.iloc[1]=='unfilled'


@pytest.mark.parametrize('side,entry', [('favorite',104.),('underdog',105.)])
@pytest.mark.parametrize('closure_offset,filled', [(1.,True),(0.,False),(-1.,False),(np.nan,False)])
def test_pregame_closure_censors_capacity_without_dropping_void_signal(tmp_path,monkeypatch,side,entry,closure_offset,filled):
    source=pregame().iloc[:1].assign(closed_ts=entry+closure_offset,y0=.5,y1=.5)
    monkeypatch.setattr(led,'OUT',tmp_path)
    monkeypatch.setattr(led,'cached_pregame_prices',lambda:source)
    d=frame(led.favorites_ledger(side))
    assert len(d)==1 and d.signal_ts.iloc[0]==100.
    assert bool(d.cost_usd.iloc[0]>0)==filled
    if filled:
        assert d.entry_ts.iloc[0]<d.expiry_ts.iloc[0]==d.exit_ts.iloc[0]
        assert d.pnl_usd.iloc[0]==pytest.approx(d.payout.iloc[0]-d.cost_usd.iloc[0])
    else:
        assert d[['entry_ts','entry_price','exit_ts','exit_price','roi']].isna().all().all()
        assert d[['shares','stake_usd','fee_usd','cost_usd','payout','pnl_usd']].eq(0).all().all()
        assert d.status.iloc[0]==d.exit_kind.iloc[0]=='unfilled'


def test_favorite_adapter_rejects_legacy_cache_and_bad_clock(monkeypatch):
    monkeypatch.setattr(led,'cached_pregame_prices',lambda:pregame().drop(columns='execution'))
    with pytest.raises(ValueError,match='predates'):
        led._favorites_frame()
    monkeypatch.setattr(led,'cached_pregame_prices',lambda:pregame().assign(entry_ts0=[103,np.nan]))
    with pytest.raises(ValueError,match='causal'):
        led.favorites_ledger()


def test_mlb_h1_descriptive_observations_never_create_capital(monkeypatch,tmp_path):
    p=pd.DataFrame(dict(game_pk=[1],pre_p=[.6],home_won_final=[True],slug=['g1'],home_team=['A'],away_team=['B'],first_pitch_ts=[10.]))
    monkeypatch.setattr(pd,'read_parquet',lambda *a,**kw:p)
    monkeypatch.setattr(led,'_mlb_games',lambda:pd.DataFrame(dict(closed_ts=[900.]),index=[1]))
    monkeypatch.setattr(led,'OUT',tmp_path)
    doc=led.mlb_favorites_ledger()
    d=frame(doc)
    assert doc['verdict']=='DESCRIPTIVE'
    assert d.status.iloc[0]=='descriptive_only'
    assert d.cost_usd.sum()==d.pnl_usd.sum()==0
    assert d.entry_ts.isna().all() and d.entry_price.isna().all()
    assert d.reference_price.iloc[0]==.6


def panel_signals():
    return pd.DataFrame(dict(game_pk=[1,2],state_ts=[10.,30.],decision_ts=[10.,30.],state_expiry_ts=[25.,45.],
        mkt_p=[.6,.6],fair_leader=[.7,.7],price_leader=[.6,.6],leader_is_home=[True,False],
        home_won_final=[1.,0.],fee_rate=.05,
        exec_home_p=[.61,np.nan],exec_home_ts=[16.,np.nan],exec_home_size=[2.,np.nan],exec_home_id=[0.,np.nan],
        exec_away_p=[np.nan,np.nan],exec_away_ts=[np.nan,np.nan],exec_away_size=[np.nan,np.nan],exec_away_id=[np.nan,np.nan]))


def test_inning_adapter_calls_causal_side_entries_not_reference_price(monkeypatch,tmp_path):
    monkeypatch.setattr(led,'_mlb_checkpoints',panel_signals)
    monkeypatch.setattr(led,'_mlb_games',lambda:pd.DataFrame(dict(slug=['g1','g2'],closed_ts=[90.,100.]),index=[1,2]))
    monkeypatch.setattr(led,'OUT',tmp_path)
    d=frame(led.mlb_inning_discount_ledger())
    assert len(d)==2
    assert d.entry_price.iloc[0]==pytest.approx(.62)
    assert d.shares.iloc[0]==2 and d.cost_usd.iloc[0]<2
    assert d.entry_ts.iloc[0]==16
    assert d.status.iloc[1]=='unfilled' and pd.isna(d.entry_price.iloc[1])
    assert d.exit_ts.tolist()==[90,100]


def test_fair_value_adapter_reuses_h3_features_and_actual_entries(monkeypatch,tmp_path):
    from pmsports.analysis import hypotheses
    import statsmodels.api as sm
    p=panel_signals().iloc[[0]].copy()
    p=pd.concat([p]*2000,ignore_index=True)
    p['event_date']=['2025-06-01']*1000+['2026-06-01']*1000
    p['mkt_staleness']=1.
    p['pre_p']=np.linspace(.4,.6,len(p))
    monkeypatch.setattr(led,'_panel_inputs',lambda:(p,pd.DataFrame()))
    monkeypatch.setattr(hypotheses,'_baseline_we',lambda base,rows,max_season:np.full(len(rows),.5))
    monkeypatch.setattr(hypotheses,'_features',lambda rows:pd.DataFrame({'x':rows.pre_p},index=rows.index))
    fit=SimpleNamespace(predict=lambda x:np.full(len(x),.7))
    monkeypatch.setattr(sm,'Logit',lambda *a,**kw:SimpleNamespace(fit=lambda **kw:fit))
    monkeypatch.setattr(led,'_mlb_games',lambda:pd.DataFrame(dict(slug=['g1'],closed_ts=[100.]),index=[1]))
    monkeypatch.setattr(led,'OUT',tmp_path)
    d=frame(led.mlb_fair_value_ledger())
    assert len(d)==1 and d.entry_price.iloc[0]==pytest.approx(.62)
    assert d.shares.iloc[0]==2 and d.cost_usd.iloc[0]<2


def wallet_tape():
    cutoff=led.SPLIT_TS
    t=pd.DataFrame(dict(timestamp=[cutoff-10,cutoff-5,cutoff+10,cutoff+14,cutoff+20],
        condition_id=['old','unsettled','new','new','missing'],proxyWallet=['leader','leader','leader','other','leader'],
        side_idx=0,q=.5,size=[100.,10000.,100.,2.,100.],y=1.,fee_rate=.05,in_play=True,family='baseball',event_slug=['a','b','c','c','d']))
    for c in ['condition_id','proxyWallet','family','event_slug']:
        t[c]=t[c].astype('category')
    return t


def test_wallet_adapter_uses_settled_ranking_and_proportional_allocations(monkeypatch,tmp_path):
    from pmsports.wallets import skill,tapes,study
    u=pd.DataFrame(dict(condition_id=['old','unsettled','new','missing'],closed_ts=[led.SPLIT_TS-1,led.SPLIT_TS+1,led.SPLIT_TS+100,led.SPLIT_TS+100]))
    u=pd.concat([u.assign(outcome_idx=0,outcome='Away'),u.assign(outcome_idx=1,outcome='Home')],ignore_index=True)
    u['market_slug']=u.condition_id+'-market'
    monkeypatch.setattr(pd,'read_parquet',lambda *a,**kw:u)
    monkeypatch.setattr(tapes,'load_trades',lambda universe:wallet_tape())
    actual=skill.positions
    seen=[]
    def capture(t):
        seen.append(t.condition_id.astype(str).tolist())
        return actual(t)
    monkeypatch.setattr(skill,'positions',capture)
    monkeypatch.setattr(study,'MIN_MKTS',1)
    monkeypatch.setattr(skill,'fdr_survivors',lambda z:pd.Series(['leader']))
    monkeypatch.setattr(led,'OUT',tmp_path)
    d=frame(led.copy_wallets_ledger(delay=3))
    assert seen==[['old']]
    assert len(d)==2 and d.status.tolist()==['filled','unfilled']
    assert d.cost_usd.iloc[0]==pytest.approx(.5) #1% of leader's$50 ticket
    assert d.shares.iloc[0]<2 and d.stake_usd.iloc[0]<.5
    assert d.entry_ts.iloc[0]==led.SPLIT_TS+14
    assert d.cost_usd.iloc[1]==0
    assert d.exit_ts.iloc[0]==led.SPLIT_TS+100 and pd.isna(d.exit_ts.iloc[1])
    assert d.exit_kind.tolist()==['resolution','unfilled']
    assert pd.isna(d.exit_price.iloc[1])
    assert d.side.tolist()==['Away','Away']
    assert d.market.tolist()==['new-market','missing-market']
    assert 'condition=new' in d.note.iloc[0]


def esports_bets():
    e=pd.DataFrame(dict(event_slug=['a','b'],t_end=[0.,0.],entT_q=[.5,np.nan],entT_sh=[2.,np.nan],
        entT_ts=[154.,np.nan],entT_rate=[.05,np.nan],entT_how=['window','unfilled'],fee_mkt=[.05,.05],yT=[1.,1.]))
    b=es_study.bets(e,'T',pd.Series([True,True]))
    b=b.assign(execution_version=VERSION,rule=esports.RULE,period='dev',P_cal=.5,fair_iid=.5,title='cs2',L=0,
        market_slug=['m1','m2'],closed_ts=1000.,team_T='B')
    return b


def test_esports_adapter_preserves_cash_no_fill_and_reconciles_headline(monkeypatch):
    b=esports_bets()
    monkeypatch.setattr(esports,'load',lambda:b)
    doc=esports.build()
    esports.verify(doc)
    d=frame(doc)
    assert len(d)==2 and d.shares.tolist()==[2,0]
    assert d.cost_usd.iloc[0]==pytest.approx(1.025)
    assert d.cost_usd.iloc[0]!=1
    assert pd.isna(d.entry_ts.iloc[1])
    assert doc['headline']['dev']['signals']==2 and doc['headline']['dev']['bets']==1
    assert doc['headline']['dev']['roi']==pytest.approx(d.pnl_usd.sum()/d.cost_usd.sum())


@pytest.mark.parametrize('mutation',[lambda b:b.drop(columns='shares'),lambda b:b.assign(how='quote'),lambda b:b.assign(pnl_usd=100.),lambda b:b.assign(fill_ts=153.)])
def test_esports_adapter_refuses_old_or_invented_execution(mutation):
    with pytest.raises(ValueError):
        esports.rows(mutation(esports_bets()))


def test_wallet_report_exports_actual_usd_and_explains_reset(monkeypatch,tmp_path):
    wf=pd.DataFrame(dict(scope='all',rule='z',delay=3,trades=[1,2],staked=[10.,30.],**{'pnl_per_$1':[2.,-1.]}))
    summary=report._aggregate_walk(wf)
    assert summary.pnl_usd.iloc[0]==1 and summary.capital_usd.iloc[0]==40
    assert summary.copy_roi.iloc[0]==pytest.approx(.025)
    assert summary.filled_months_positive.iloc[0]==.5
    assert report._aggregate_walk(pd.DataFrame()).empty
    monkeypatch.setattr(report,'OUT',tmp_path)
    rules=pd.DataFrame(columns=['family'])
    text=report._render(wallet_tape(),pd.DataFrame(),rules,pd.DataFrame(),pd.DataFrame(),summary,pd.DataFrame(),'2026-01-01')
    assert '$100 per event per month' in text
    assert 'delay0 still requires a later' in text
    assert 'pnl_usd' in text and 'capital_usd' in text
    assert 'equal $ per trade' not in text


def test_wallet_adapter_empty_selection_keeps_valid_empty_ledger(monkeypatch,tmp_path):
    from pmsports.wallets import skill,tapes
    u=pd.DataFrame(dict(condition_id=['old','unsettled','new','missing'],closed_ts=[led.SPLIT_TS-1,led.SPLIT_TS+1,led.SPLIT_TS+100,led.SPLIT_TS+100]))
    monkeypatch.setattr(pd,'read_parquet',lambda *a,**kw:u)
    monkeypatch.setattr(tapes,'load_trades',lambda universe:wallet_tape())
    monkeypatch.setattr(skill,'fdr_survivors',lambda z:pd.Series([],dtype=object))
    monkeypatch.setattr(led,'OUT',tmp_path)
    doc=led.copy_wallets_ledger()
    assert doc['n_total_trades']==0 and doc['rows']==[] and doc['headline']=={}


def test_panel_adapter_fails_closed_on_legacy_schema(monkeypatch):
    monkeypatch.setattr(pd,'read_parquet',lambda *a,**kw:pd.DataFrame({'mkt_p':[.5]}))
    with pytest.raises(ValueError,match='predates'):
        led._panel_inputs()


def test_esports_loader_requires_original_round1_predictions(tmp_path,monkeypatch):
    b=esports_bets()
    cal={'titles':[],'coef':[0.,1.]}
    (tmp_path/'calib_v1.json').write_text(json.dumps(cal))
    monkeypatch.setattr(esports,'SRC',tmp_path)
    ser=pd.DataFrame(dict(event_slug=['a','b'],market_slug=['m1','m2'],closed_ts=[1000.,1000.],o0='A',o1='B'))
    def read(path,*args,**kwargs):
        if Path(path).name=='series.parquet':
            return ser
        if 'holdout' in Path(path).name:
            return b.iloc[:0].assign(period='holdout')
        return b
    monkeypatch.setattr(pd,'read_parquet',read)
    assert len(esports.load())==2
    b['P_cal']=.9
    with pytest.raises(ValueError,match='round1'):
        esports.load()


def test_inning_ten_cent_first_signal_is_not_filtered_three_cent_trade(monkeypatch):
    p=panel_signals().copy()
    p['game_pk']=[1,1]
    p['fair_leader']=[.65,.75]
    p['leader_is_home']=True
    p['exec_home_p']=[.61,.71]
    p['exec_home_ts']=[16.,36.]
    p['exec_home_size']=[2.,3.]
    p['exec_home_id']=[0.,1.]
    monkeypatch.setattr(led,'_mlb_games',lambda:pd.DataFrame(dict(slug=['g1'],closed_ts=[90.]),index=[1]))
    three=led._discount_signals(p,.03,first=True)
    ten=led._discount_signals(p,.10,first=True)
    assert three.decision_ts.tolist()==[10.]
    assert ten.decision_ts.tolist()==[30.]
    assert led._discount_signals(three,.10,first=True).empty
    traded=led._mlb_rows(ten,ten.leader_is_home,'ten-cent rule')
    assert traded.entry_ts.tolist()==[36.]
    assert traded.entry_price.iloc[0]==pytest.approx(.72)
    assert len(led._discount_signals(p,.03,first=False))==2


def test_inning_grid_exports_all_fixed_rules_with_unique_names_and_no_fills(monkeypatch,tmp_path):
    p=panel_signals().assign(diff=[1,-1],edge_home=[.12,-.12])
    monkeypatch.setattr(led,'_mlb_checkpoints',lambda:p)
    monkeypatch.setattr(led,'_adjusted_checkpoint_rows',lambda rows:rows)
    monkeypatch.setattr(led,'_mlb_games',lambda:pd.DataFrame(dict(slug=['g1','g2'],closed_ts=[90.,100.]),index=[1,2]))
    monkeypatch.setattr(led,'OUT',tmp_path)
    alias=tmp_path/'mlb_inning_discount.json';alias.write_text('existing alias')
    docs=led.mlb_inning_grid_ledgers()
    assert len(docs)==22 and len({d['slug'] for d in docs})==22
    assert alias.read_text()=='existing alias'
    primary=next(d for d in docs if d['slug']=='mlb_inning_adjusted_08c_leader')
    rows=frame(primary)
    assert len(rows)==2 and rows.status.tolist()==['partial','unfilled']
    assert rows.entry_ts.iloc[0]==16 and pd.isna(rows.entry_ts.iloc[1])
    assert set(rows.period)=={'holdout'}
    assert next(d for d in docs if d['slug']=='mlb_inning_adjusted_08c_trailer')['rows']==[]


def test_inning_grid_reconciliation_rejects_wrong_cash_or_first_signal_counts():
    rows=nofills(2)
    good=dict(signals=2,n=0,unfilled=2,partial=0,capital_usd=0.,pnl_usd=0.)
    assert led._reconcile_inning_rule(rows,good,'test')['signals']==2
    with pytest.raises(ValueError,match='signals'):
        led._reconcile_inning_rule(rows,{**good,'signals':1},'test')
    with pytest.raises(ValueError,match='capital_usd'):
        led._reconcile_inning_rule(rows,{**good,'capital_usd':100},'test')
