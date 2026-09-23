import numpy as np
import pandas as pd
import pytest

from pmsports.research import h_soccer_continuation as S


def event(idx=1, wallclock=100., **kw):
    return dict(event_slug="g", idx=idx, wallclock=wallclock, period=1, shootout=False,
        type_raw="red-card", kind="red_card", team_side="home", minute=25., clock_disp="25'",
        lead=0, league="test", text="", cid_home="h", cid_draw="d", cid_away="a",
        feed_consistent=False, reg_agrees=False,
        **{f"age_{l}_pre":1 for l in S.LEGS}, **{f"n_{l}_5m":5 for l in S.LEGS}) | kw


def metadata():
    return {cid:dict(yes_token=cid+"y",no_token=cid+"n",y=y,fee_rate=.05,closed_ts=2000.,
                event="g",market=cid,neg_risk=True) for cid,y in (("h",0.),("d",0.),("a",1.))}


def tape(rows):
    return pd.DataFrame([dict(m=m,s=s,ts=ts,q=q,size=size,fee_rate=.05,print_id=str(i))
                         for i,(m,s,ts,q,size) in enumerate(rows)])


def game(events=None, rows=None, meta=None):
    if rows is None: rows=[(m,0,90.,p,1000.) for m,p in (("h",.3),("d",.25),("a",.45))]
    return S.GameData(pd.DataFrame(events or [event()]),meta or metadata(),tape(rows))


def config(family, name="primary"):
    return next(c for c in S.configs() if c["family"]==family and c["name"]==name)


def test_clock_explicit_base_avoids_double_counting():
    assert S.true_minute(90,"90'+5'")==95
    assert S.true_minute(95,"90'+5'")==95
    assert S.true_minute(81.5,"82'")==81.5


def test_token_mapping_and_sell_complement_unknown_side_rejected():
    raw=pd.DataFrame(dict(timestamp=[10,11,12,13],price=[.3,.6,.8,.9],size=[5]*4,
        asset=["hy","hy","hn","alien"],side=["BUY","SELL","BAD","BUY"],outcomeIndex=[1]*4,
        proxyWallet=["w"]*4,transactionHash=["x","y","z","zz"]))
    x=S.normalize_raw(raw,"h",metadata()["h"])
    assert x.s.tolist()==[0,1]
    assert x.q.tolist()==pytest.approx([.3,.4])
    assert len(x)==2


def test_red_card_opponent_and_300s_decision_no_final_quality_gate():
    g=game(rows=[("h",0,390,.3,100), ("d",0,390,.25,100), ("a",0,390,.45,100),
                 ("a",0,400,.4,100), ("a",0,403,.41,100), ("a",0,404,.42,100)])
    a,o=S.make_signals(g,config("card"))
    assert a.selected.tolist()==[True]  # feed_consistent/reg_agrees false are audit only
    assert o.leg.tolist()==["away"]
    assert o.signal_ts.tolist()==[400.]
    r=S.execute(g,o)
    assert r.fill_ts.tolist()==[404.]
    assert r.entry_price.tolist()==[.42]
    assert r.shares.iloc[0]==100


def test_missing_future_horizon_price_retained_no_signal():
    g=game(rows=[("h",0,390,.3,100),("a",0,390,.45,100),("d",0,401,.25,100)])
    a,o=S.make_signals(g,config("card"))
    assert a.reason.tolist()==["missing_three_prior_references"]
    assert o.empty


def test_first_order_not_first_successful_fill():
    g=game([event(),event(2,120)],rows=[(m,0,390,p,10) for m,p in (("h",.3),("d",.25),("a",.45))])
    a,o=S.make_signals(g,config("card"))
    assert a.reason.tolist()==["eligible","later_event_after_first_order"]
    assert len(o)==1
    r=S.execute(g,o)
    assert r.cost_usd.sum()==0 and r.status.tolist()==["unfilled"]


def test_terminal_whistle_and_et_excluded():
    g=game([event(1,100,period=2,kind="period",type_raw="end-regular-time",lead=1,minute=90,clock_disp="90'+5'"),
            event(2,101,period=3,lead=1,minute=91)])
    a,o=S.make_signals(g,config("leader"))
    assert a.empty and o.empty


def test_decision_after_whistle_audited():
    g=game([event(),event(2,200,kind="period",type_raw="end-regular-time",period=2)])
    a,o=S.make_signals(g,config("card"))
    assert a.reason.tolist()==["decision_after_terminal"]
    assert o.empty


def test_dutch_observation_prints_cannot_be_execution():
    rows=[(m,0,104+i,.2,100) for i,m in enumerate(("h","d","a"))]
    rows += [(m,0,110,.21,100) for m in ("h","d","a")]
    g=game([event(kind="goal",type_raw="goal")],rows)
    a,o=S.make_signals(g,config("dutch"))
    assert a.selected.tolist()==[True]
    assert o.signal_ts.tolist()==[106.]*3
    r=S.execute(g,o)
    assert r.fill_ts.tolist()==[110.]*3
    assert len(set(r.print_id))==3
    assert r.entry_cost_usd.sum()<=100+1e-9
    assert np.all(r.shares<=o.target_shares)


def test_dutch_no_mirror_requires_actual_no_prints():
    g=game([event(kind="goal")],[(m,0,104+i,.7,100) for i,m in enumerate(("h","d","a"))])
    a,o=S.make_signals(g,config("dutch","no_mirror"))
    assert a.reason.tolist()==["missing_three_acquired_side_observations"]
    assert o.empty


def pair_orders():
    rows=[]
    for m,l,y in (("h","home",0), ("a","away",1)):
        rows.append(dict(signal_id="pair",family="anchor",variant="primary",event="g",league="test",period="dev",
            m=m,leg=l,s=0,signal_ts=100.,receipt_ts=100.,delay_s=3,expiry_ts=120.,budget_usd=10.,
            target_shares=10.,y=y,fee_rate=.05,reference_price=.4,market=m,close_ts=2000.,paired=True))
    return pd.DataFrame(rows)


def test_failed_hedge_unwinds_actual_size_and_settles_residual_cash():
    g=game(rows=[("h",0,104,.4,10), ("h",1,124,.7,4)])
    r=S.execute(g,pair_orders())
    home=r[r.leg.eq("home")].iloc[0]
    assert home.shares==10 and home.sold_shares==4 and home.residual_shares==6
    assert home.hedge_status=="failed_or_partial"
    assert home.sale_proceeds==pytest.approx(1.2)
    assert home.exit_fee_usd==pytest.approx(4*.05*.7*.3)
    assert home.pnl_usd==pytest.approx(home.payout-home.stake_usd-home.fee_usd)
    assert r.loc[r.leg.eq("away"),"cost_usd"].iloc[0]==0
    assert home.exit_fill_ts==124


def test_pair_quantities_fixed_before_future_sizes_and_stress_on_sale():
    g=game(rows=[("h",0,104,.4,10),("a",0,104,.4,2),("h",1,124,.7,4),("a",1,124,.5,1)])
    r=S.execute(g,pair_orders(),.01)
    assert r.target_shares.tolist()==[10,10]
    assert r.shares.tolist()==[10,2]
    assert r.hedge_status.eq("failed_or_partial").all()
    assert r.sale_proceeds.sum()==pytest.approx(4*.29+1*.49)
    assert r.residual_shares.sum()==7


def test_complete_pair_held_no_unwind():
    g=game(rows=[("h",0,104,.4,10),("a",0,104,.4,10),("h",1,124,.7,4)])
    r=S.execute(g,pair_orders())
    assert r.hedge_status.eq("complete").all()
    assert r.sold_shares.sum()==0
    assert r.payout.sum()==10


def test_full_unwind_uses_actual_exit_clock_and_no_fill_has_no_exit():
    g=game(rows=[("h",0,104,.4,10),("h",1,124,.7,10)])
    r=S.execute(g,pair_orders())
    home=r[r.leg.eq("home")].iloc[0]
    away=r[r.leg.eq("away")].iloc[0]
    assert home.residual_shares==0 and home.sold_shares==10
    assert home.exit_kind=="unwind" and home.exit_ts==124
    assert home.exit_price==pytest.approx(.3)
    assert away.exit_kind=="unfilled"
    assert pd.isna(away.exit_ts) and pd.isna(away.exit_price)


def test_partial_unwind_retains_settlement_clock():
    g=game(rows=[("h",0,104,.4,10),("h",1,124,.7,4)])
    r=S.execute(g,pair_orders())
    home=r[r.leg.eq("home")].iloc[0]
    assert home.exit_fill_ts==124
    assert home.exit_kind=="partial_unwind_and_resolution"
    assert home.exit_ts==2000 and home.residual_shares==6


def test_multiple_pairs_share_exit_capacity():
    g=game(rows=[("h",0,104,.4,20),("h",1,124,.7,5)])
    o=pair_orders(); second=o.copy(); second.signal_id="pair2"
    r=S.execute(g,pd.concat([o,second],ignore_index=True))
    assert r.shares.sum()==20
    assert r.sold_shares.sum()==5
    assert r.residual_shares.sum()==15


def test_void_remains_trade_with_half_payout():
    m=metadata();m["a"]["y"]=.5
    g=game(rows=[("h",0,390,.3,100), ("d",0,390,.25,100), ("a",0,390,.45,100), ("a",0,404,.42,10)],meta=m)
    a,o=S.make_signals(g,config("card"));r=S.execute(g,o)
    assert a.selected.all() and r.payout.sum()==5


def test_added_time_ties_excluded_and_reference_price_gate():
    events=[event(1,100,period=2,lead=0,minute=90,clock_disp="90'+1'"),
            event(2,110,period=2,lead=1,minute=90,clock_disp="90'+2'")]
    g=game(events,[("h",0,90,.8,100),("d",0,90,.1,100),("a",0,90,.1,100),("h",0,114,.82,5)])
    a,o=S.make_signals(g,config("leader"))
    assert len(a)==1 and a.true_min.tolist()==[92]
    assert o.leg.tolist()==["home"]
    assert S.execute(g,o,.01).entry_price.iloc[0]==pytest.approx(.83)


def test_ledger_unfilled_date_cash_and_whole_candidate_audit():
    g=game();a,o=S.make_signals(g,config("card"))
    # ensure a submitted zero-fill leg, plus an explicit rejected candidate
    r=S.execute(g,o)
    a2=a.copy();a2.signal_id="reject";a2.selected=False;a2.reason="missing_three_prior_references"
    all_a=pd.concat([a,a2],ignore_index=True)
    t=S.ledger_frame(all_a,r,"card")
    from pmsports.research.ledger_studies import validate_cash,_rows,COLUMNS
    validate_cash(t)
    assert len(t)==2 and t.cost_usd.sum()==0
    rows=_rows(t)
    assert all(x[COLUMNS.index("entry_ts")] is None for x in rows)
    assert all(x[COLUMNS.index("date")] is not None for x in rows)


def test_coherence_uses_identical_selected_rows_and_void_actual_sum():
    rows=[]
    for i in range(6):
        rows.append(dict(family="sub",variant="primary",selected=True,period="dev",event=str(i),
            reference_home=.3,reference_draw=.3,reference_away=.4,y_home=0,y_draw=0,y_away=1))
    a=pd.DataFrame(rows)
    assert S.coherence(a)["dev"]["sum_leg_errors"]==pytest.approx(0)
    a["reference_home"]+=.05
    assert S.coherence(a)["dev"]["gate"]=="fails_1c_tolerance"
    assert S.coherence(a)["dev"]["sum_leg_errors"]==pytest.approx(-.05)


def test_price_matching_discards_nonoverlap_and_preserves_game_covariance():
    a=[];t=[]
    for i in range(6):
        for variant,pnl in (("primary",10.),("control_75_85",0.)):
            sid=f"{i}-{variant}"
            a.append(dict(family="leader",variant=variant,selected=True,period="holdout",
                          signal_id=sid,event=str(i),reference_price=.8))
            t.append(dict(family="leader",variant=variant,slip=.01,period="holdout",
                          signal_id=sid,event=str(i),cost_usd=100.,pnl_usd=pnl))
    result=S.matched_control(pd.DataFrame(a),pd.DataFrame(t))["holdout"]
    assert result["difference"]==pytest.approx(.1)
    assert result["difference_ci_lo"]==pytest.approx(.1)
    assert result["difference_ci_hi"]==pytest.approx(.1)
    assert result["overlap_bins"]==[80]


def test_staleness_control_fit_uses_dev_only():
    rows=[]
    for i in range(30):
        a=dict(family="anchor",variant="primary",period="dev" if i<20 else "holdout",gap=.01*np.log1p(i))
        for l in S.LEGS: a[f"age_{l}_pre"]=i;a[f"n_{l}_5m"]=0
        rows.append(a)
    a=pd.DataFrame(rows);base=S.staleness_control(a)
    a.loc[a.period.eq("holdout"),"gap"]=.5
    changed=S.staleness_control(a)
    assert changed["coefficients"]==pytest.approx(base["coefficients"])
    assert base["holdout"]["r2"]==pytest.approx(1.)


def test_empty_ledger_has_complete_cash_schema():
    audit=pd.DataFrame(columns=["family","variant","signal_id"])
    trades=pd.DataFrame(columns=["family","variant","slip","signal_id","period"])
    t=S.ledger_frame(audit,trades,"card")
    from pmsports.research.ledger_studies import _document
    doc=_document({"slug":"empty"},t)
    assert doc["rows"]==[] and doc["headline"]=={} and doc["n_total_trades"]==0
