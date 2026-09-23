import math
import pandas as pd
import pytest
from pmsports.book_replay import ReceivedBook
from pmsports.research.h_live_execution import Experiment, prior_market_cells, terminal_payouts


def snapshot(bid=.4, ask=.5, size=10):
    return {'event_type':'book','asset_id':'t','bids':[{'price':str(bid),'size':str(size)}],
            'asks':[{'price':str(ask),'size':str(size)}]}


def change(size):
    return {'event_type':'price_change','price_changes':[{'asset_id':'t','side':'SELL','price':'.5','size':str(size)}]}


def test_depth_consumption_replenishment_and_independent_policy():
    book=ReceivedBook();book.apply(1000,snapshot())
    a=book.cross('t',1001,policy='a',limit=.5,shares=8,fee_rate=0)
    b=book.cross('t',1001,policy='a',limit=.5,shares=8,fee_rate=0)
    assert (a['shares'],b['shares'],b['status'])==(8,2,'partial')
    assert book.cross('t',1001,policy='b',limit=.5,shares=10)['shares']==10
    book.apply(1002,change(10))
    assert book.cross('t',1003,policy='a',limit=.5)['shares']==0
    book.apply(1004,change(13))
    assert book.cross('t',1005,policy='a',limit=.5)['shares']==3
    book.apply(1006,snapshot(size=13))
    assert book.cross('t',1007,policy='a',limit=.5)['shares']==0


def test_snapshot_required_staleness_crossed_and_minimum():
    book=ReceivedBook();book.apply(0,change(10))
    assert book.cross('t',1,limit=.5)['reason']=='no_initial_snapshot'
    book.apply(2,snapshot())
    assert book.cross('t',6000,limit=.5)['reason']=='stale_or_future_book'
    assert book.cross('t',1,limit=.5)['reason']=='stale_or_future_book'
    assert book.cross('t',3,limit=.5,shares=2,min_order_shares=5)['reason']=='below_minimum_order'
    book.apply(4,snapshot(bid=.6))
    assert book.cross('t',5,limit=.5)['reason']=='crossed_book'
    with pytest.raises(ValueError,match='out of order'):
        book.apply(3,snapshot())


def test_actual_multilevel_cash_and_exit_fee():
    book=ReceivedBook();m=snapshot(size=2)
    m['asks'].append({'price':'.6','size':'3'});book.apply(1,m)
    r=book.cross('t',2,limit=.6,shares=4,fee_rate=.05)
    assert r['shares']==4 and r['price']==pytest.approx(.55)
    assert r['cash_usd']==pytest.approx(2*.5+2*.6+2*.05*.5*.5+2*.05*.6*.4)
    sell=book.cross('t',3,buy=False,limit=.4,shares=4,fee_rate=.05)
    assert sell['shares']==2 and sell['status']=='partial'
    assert sell['cash_usd']==pytest.approx(.8-2*.05*.4*.6)


def test_malformed_update_cannot_freshen_and_sell_requires_position():
    book=ReceivedBook();book.apply(1,snapshot())
    book.apply(7000,change(float('nan')))
    assert book.cross('t',7001,limit=.5)['reason']=='stale_or_future_book'
    with pytest.raises(ValueError,match='nonfinite'):
        book.apply(float('nan'),snapshot())
    with pytest.raises(ValueError,match='sell quantity'):
        book.cross('t',7001,buy=False,limit=.4)
    with pytest.raises(ValueError,match='minimum'):
        book.cross('t',7001,limit=.4,min_order_shares=float('nan'))
    # A consumed top bid still invalidates a later raw crossed book.
    book=ReceivedBook();book.apply(1,snapshot(bid=.6,ask=.7,size=2))
    book.cross('t',2,buy=False,limit=.6,shares=2)
    book.apply(3,change(10))
    assert book.cross('t',4,limit=.5)['reason']=='crossed_book'


def fixture():
    meta={1:dict(slug='game',home_token='t',away_token='a',fee_rate=.05,
                 resolution_status='verified',resolution_ts=20.,y_home=1.,y_away=0.)}
    cells=pd.DataFrame([dict(inning=8,half='bottom',outs=1,bases=0,diff=0,mu=.7,sd=.1,games=30)])
    return Experiment(meta,cells)


def test_entry_uses_later_depth_and_residual_settles_after_partial_exit():
    sim=fixture();sim.book.apply(1000,snapshot(ask=.5,size=10))
    event=dict(kind='state',game_pk=1,recv_ms=1001,state=dict(inning=8,half='bottom',outs=1,bases=0,diff=0))
    sim.receive(event);r=sim.audit[-1]
    assert r['eligible_ts']==pytest.approx(4.001)
    # Recorded ask rises beyond the fixed limit while the order is pending.
    sim.book.apply(2000,snapshot(ask=.6,size=10))
    sim.execute(4001,'entry',r)
    assert r['shares']==0 and r['reason']=='no_depth_at_limit'
    # Independent fixture enters10shares, only2shares can exit at the mean.
    sim=fixture();sim.book.apply(1000,snapshot(ask=.5,size=10));sim.receive(event);r=sim.audit[-1]
    sim.execute(4001,'entry',r)
    sim.book.apply(5000,snapshot(bid=.7,ask=.8,size=2));sim.on_book(5000,{'t'})
    sim.execute(8000,'target_exit',r)
    assert r['exit_shares']==2 and r['residual_shares']==8
    sim.pending=[];audit=sim.finish().iloc[0]
    assert audit.pnl_usd==pytest.approx(audit.exit_proceeds+8-audit.cost_usd)
    assert audit.exit_price==pytest.approx(.94)
    assert audit.book_exit_ts==8 and audit.exit_ts==20
    assert audit.exit_kind=='book exits + residual resolution'


def test_full_book_exit_and_no_fill_have_correct_exit_price_and_clock():
    sim=fixture();sim.book.apply(1000,snapshot(ask=.5,size=10))
    e=dict(kind='score',game_pk=1,recv_ms=1001,feed='mlb',side='home')
    r=sim.row(e,'mean_reversion_3s','home');sim.enter(r,1001,3)
    sim.execute(4001,'entry',r)
    sim.book.apply(5000,snapshot(bid=.7,ask=.8,size=20))
    sim.execute(8000,'timeout_exit',r)
    nofill=sim.row(e,'mlb_score_3s','home')
    audit=sim.finish()
    assert audit.iloc[0].exit_ts==8 and audit.iloc[0].exit_price==pytest.approx(.7)
    assert audit.iloc[0].exit_kind=='timeout exit' and audit.iloc[0].residual_shares==0
    assert math.isnan(nofill['exit_price']) and math.isnan(nofill['exit_ts'])
    assert nofill['exit_kind'] is None


def test_mean_cells_ignore_future_and_weight_games_equally():
    base=dict(inning=8,half='bottom',outs=1,bases=0,diff=0,mkt_staleness=1,exec_home_size=1)
    p=pd.DataFrame([{**base,'game_pk':1,'event_date':'2025-05-01','mkt_p':.2}]*10+
                   [{**base,'game_pk':2,'event_date':'2025-05-02','mkt_p':.8},
                    {**base,'game_pk':3,'event_date':'2026-05-02','mkt_p':.99}])
    cells=prior_market_cells(p,2026)
    assert cells.iloc[0].mu==pytest.approx(.5) and cells.iloc[0].games==2


def test_resolution_requires_final_authority_and_capture_censor_is_explicit():
    g=dict(closed=True,clobTokenIds=['t','a'],outcomePrices=['1','0'])
    assert terminal_payouts(g)=={}
    assert terminal_payouts({**g,'umaResolutionStatus':'resolved'})=={'t':1.,'a':0.}
    sim=fixture()
    assert 'policy' in sim.finish() and sim.finish().empty
    sim.book.apply(1000,snapshot());e=dict(kind='score',game_pk=1,recv_ms=1001,feed='mlb',side='home')
    r=sim.row(e,'mean_reversion_3s','home');sim.enter(r,1001,3)
    sim.pending=[];sim.execute(4001,'entry',r)
    d=sim.finish()
    assert d.iloc[0].exit_censor_reason=='capture_ended_before_timeout_signal'
    assert d.iloc[0].residual_shares==10
