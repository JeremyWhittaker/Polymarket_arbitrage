"""Four recovered MLB hypotheses, repaired causal transaction proxies.

Run: OPENBLAS_NUM_THREADS=1 .venv/bin/python -m pmsports.research.h_baseball_continuation
No network; only finite derivative caches. Prior seasons train every model. Historical
July--September evaluation was already explored and is not a fresh holdout.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
from pathlib import Path
import numpy as np
import pandas as pd
import statsmodels.api as sm
from pmsports.execution import TapeReplay
from pmsports.research import common as C
from pmsports.research.h_mlb_derivs import _yes_means

SRC = C.DATA / 'research/h_mlb_derivs'
OUT = C.DATA / 'research/baseball_continuation'
REPORT = Path('reports/research/BASEBALL_CONTINUATION.md')
SLUGS = ['totals-pace-antiextrapolation', 'nrfi-demand-premium',
         'runline-1run-spike', 'runline-margin-transition']
HOLDOUT = pd.Timestamp('2026-07-01', tz='UTC').timestamp()
COLS = ['id','period','date','sport','league','event','market','side','entry_ts','entry_price',
        'stake_usd','fee_usd','exit_kind','exit_ts','exit_price','payout','pnl_usd','roi','note','signal_ts']


def normalize(raw, market):
    """Token identity, never API outcomeIndex; SELL acquires complement."""
    if raw.empty:
        return pd.DataFrame({c:pd.Series(dtype='object' if c in ('m','w','print_id') else 'float64')
                             for c in ['m','s','ts','q','size','w','fee_rate','print_id']})
    r = raw.drop_duplicates().copy()
    idx = np.where(r.asset.astype(str).eq(str(market.tok0)), 0,
                   np.where(r.asset.astype(str).eq(str(market.tok1)), 1, -1))
    ok = (idx >= 0) & r.side.isin(['BUY','SELL']).to_numpy()
    original = np.arange(len(r))[ok]; r = r[ok]; idx = idx[ok]
    buy = r.side.eq('BUY').to_numpy()
    t = pd.DataFrame({'m':market.condition_id, 's':np.where(buy,idx,1-idx),
        'ts':r.timestamp.to_numpy(float), 'q':np.where(buy,r.price,1-r.price),
        'size':r['size'].to_numpy(float), 'w':r.get('proxyWallet',pd.Series(None,index=r.index,dtype=object)),
        'fee_rate':market.fee_rate, 'print_id':[f'{market.condition_id}:{i}' for i in original]})
    return t[t.q.between(1e-6,1-1e-6)&t['size'].gt(0)].sort_values('ts',kind='stable').reset_index(drop=True)


def yrfi_side(question, description, o0, o1):
    """Canonical YRFI uses question sense and actual Yes/No outcome orientation."""
    sense = _yes_means(question, description)
    labels = [str(o0).lower(),str(o1).lower()]
    if sense == 'unknown' or sorted(labels) != ['no','yes']:
        return None
    return labels.index('yes' if sense == 'run' else 'no')


def attach_states(tape, states):
    """Only completed corrected states at public transaction timestamp, never ts-2.6."""
    if tape.empty or states.empty:
        return pd.DataFrame()
    keep=['state_ts','state_expiry_ts','inning','half','outs','bases','diff','home_score','away_score']
    s=states[keep].sort_values('state_ts').drop_duplicates('state_ts',keep='last')
    z=pd.merge_asof(tape.sort_values('ts'),s,left_on='ts',right_on='state_ts',direction='backward')
    # Final play is absent from corrected states; previous state expires at its actual end.
    return z[z.state_ts.notna() & z.ts.lt(z.state_expiry_ts)].copy()


def state_features(d):
    inn=d.inning.to_numpy(float)/9
    margin=d['diff'].to_numpy(float)/4
    bases=d.bases.to_numpy(int)
    return np.column_stack([np.ones(len(d)),inn,d.half.eq('bottom').to_numpy(float),
        d.outs.to_numpy(float)/3, bases&1,(bases>>1)&1,(bases>>2)&1,
        margin,margin*inn,margin*np.abs(margin)])


def prior_games(games, year):
    good=pd.to_datetime(games.game_ts,unit='s',utc=True).dt.year.lt(year)
    for c in ('r1','final_margin_home'):
        if c in games:
            good &= np.isfinite(pd.to_numeric(games[c],errors='coerce'))
    if 'r1' in games: good &= games.r1.ge(0)
    return games[good].drop_duplicates('game_pk')


def canonical_orientation(r, metadata):
    """Confirm the modeled Over/home-cover/away-cover claim is actual outcome 0."""
    if r.kind=='nrfi': return True  # separately resolved by question sense at decision
    if r.kind=='total': return str(r.o0).lower()=='over' and str(r.o1).lower()=='under'
    team=str(r.home_name if r.kind=='spread_home' else r.away_name).casefold()
    opposite=str(r.away_name if r.kind=='spread_home' else r.home_name).casefold()
    def match(label,name):
        label=str(label).casefold()
        return name==label or name.endswith(' '+label)
    if str(r.o0).casefold()!='yes': return match(r.o0,team) and match(r.o1,opposite)
    if str(r.o1).casefold()!='no' or metadata is None: return False
    named=re.fullmatch(r'Spread:\s*(.+?)\s*\(-1\.5\)\s*',str(metadata.question),re.I)
    desc=str(metadata.description).casefold().replace('“','"').replace('”','"')
    return bool(named and match(named.group(1),team) and 'resolve to "yes" if' in desc
                and 'win the game by 2 or more runs' in desc)


def prior_facts(baseline, schedules):
    """First-inning totals at corrected inning-2 checkpoint, not an incomplete inning-1 maximum."""
    b=baseline[baseline.inning.eq(2)&baseline.half.eq('top')&baseline.checkpoint].copy()
    if b.game_pk.duplicated().any():
        raise ValueError('multiple inning-2 checkpoints per baseline game')
    b['r1']=b.home_score+b.away_score
    g=schedules.drop_duplicates('game_pk').copy()
    g['final_margin_home']=g.home_score-g.away_score
    return b[['game_pk','r1']].merge(g[['game_pk','game_ts','final_margin_home']],on='game_pk',validate='one_to_one')


def pair_payoff(margin):
    """Both No runlines pay 1 normally, 2 at |margin|<=1 (including a tied final)."""
    m=np.asarray(margin)
    return (m<=1).astype(float)+(m>=-1).astype(float)


def pair_implied(cost):
    return np.asarray(cost)-1.0


def prior_reference(tape, side, decision, window=3600):
    t=tape[tape.s.eq(side)&tape.ts.lt(decision)&tape.ts.ge(decision-window)]
    return None if t.empty else t.iloc[-1]


def load_inputs():
    menu=pd.read_parquet(SRC/'menu.parquet')
    home=menu[menu.sampled&menu.kind.eq('spread_home')&menu.line.eq(1.5)]
    away=menu[menu.kind.eq('spread_away')&menu.line.eq(1.5)&menu.game_pk.isin(home.game_pk)].drop_duplicates('game_pk')
    wanted=pd.concat([menu[menu.sampled&menu.kind.isin(['nrfi','total','spread_home'])],away]).drop_duplicates('condition_id')
    games=pd.read_parquet(SRC/'games.parquet').drop_duplicates('event_slug')
    # Ambiguous contracts for a game cannot be picked using eventual outcomes.
    ambiguous=games.groupby('game_pk').event_slug.nunique(); bad=set(ambiguous[ambiguous>1].index)
    wanted=wanted[~wanted.game_pk.isin(bad)].copy()
    wanted=wanted.drop(columns=['game_start_ts']).merge(games[['event_slug','game_ts','r1','final_margin_home','home_name','away_name']],on='event_slug',validate='many_to_one')
    wanted['game_start_ts']=wanted.game_ts
    wanted['season']=pd.to_datetime(wanted.game_ts,unit='s',utc=True).dt.year
    meta=pd.read_parquet(SRC/'meta.parquet').drop_duplicates('condition_id').set_index('condition_id')
    orientation_ok=[canonical_orientation(r,meta.loc[r.condition_id] if r.condition_id in meta.index else None)
                    for r in wanted.itertuples()]
    orientation_unknown=int((~np.array(orientation_ok)).sum())
    wanted=wanted.loc[orientation_ok].copy()
    dirs=[OUT/'tapes',SRC/'trades',C.RESEARCH/'h_mlb_runline_home_trunc/tapes_v2']
    tapes={}; paths=[]; missing=[]; invalid_settlement=0
    for r in wanted.itertuples():
        path=next((d/f'{r.condition_id}.parquet' for d in dirs if (d/f'{r.condition_id}.parquet').exists()),None)
        if path is None: missing.append(r.condition_id); continue
        if r.y0 not in (0,.5,1) or r.y1 not in (0,.5,1) or abs(r.y0+r.y1-1)>1e-8:
            invalid_settlement+=1; continue
        tapes[r.condition_id]=normalize(pd.read_parquet(path),r); paths.append(path)
    panel=pd.read_parquet(C.DATA/'mlb/panel.parquet',columns=['game_pk','state_ts','state_expiry_ts','inning','half','outs','bases','diff','home_score','away_score'])
    states={int(pk):g for pk,g in panel.groupby('game_pk',sort=False)}
    pieces=[]
    for r in wanted.itertuples():
        t=tapes.get(r.condition_id)
        if t is None or r.kind not in ('total','spread_home'): continue
        z=attach_states(t,states.get(r.game_pk,pd.DataFrame()))
        if z.empty: continue
        for k in ('game_pk','event_slug','market_slug','game_start_ts','season','kind','line','y0','y1','closed_ts'):
            z[k]=getattr(r,k)
        z['pre_n']=(t.ts<r.game_start_ts).sum()
        z['p0']=np.where(z.s.eq(0),z.q,1-z.q)
        pieces.append(z)
    obs=pd.concat(pieces,ignore_index=True) if pieces else pd.DataFrame()
    baseline=pd.read_parquet(C.DATA/'mlb/baseline.parquet',columns=['game_pk','inning','half','checkpoint','home_score','away_score'])
    schedule_paths=sorted((C.DATA/'mlb/mlb_only').glob('schedule_*.parquet'))+[C.DATA/'mlb/mlb_games.parquet']
    schedules=pd.concat([pd.read_parquet(p,columns=['game_pk','game_ts','home_score','away_score']) for p in schedule_paths])
    facts=prior_facts(baseline,schedules)
    return wanted,tapes,meta,facts,obs,paths,{'ambiguous_games_excluded':len(bad),'missing_tapes':missing,'invalid_settlement':invalid_settlement,
        'unknown_canonical_orientation_excluded':orientation_unknown,
        'markets':len(wanted),'tapes':len(tapes),'state_joined_prints':len(obs),'corrected_panel_games':len(states)}


def order(r, side, signal, expiry, **extra):
    return {'m':r.condition_id if hasattr(r,'condition_id') else r.m,'s':int(side),'signal_ts':float(signal),
        'expiry_ts':float(expiry),'event':int(r.game_pk),'game_pk':int(r.game_pk),'event_slug':r.event_slug,
        'market_slug':r.market_slug,'game_start_ts':float(r.game_start_ts),'season':int(r.season),
        'fee_rate':r.fee_rate,'y':float(r.y0 if side==0 else r.y1),'budget_usd':100.,
        'closed_ts':float(getattr(r,'closed_ts',np.nan)),**extra}


def nrfi_orders(markets,tapes,meta,games):
    out=[]; bases={}; missing_prior=0; orientation={}; no_signal=0
    for r in markets[markets.kind.eq('nrfi')].itertuples():
        past=prior_games(games,r.season); base=float(past.r1.ge(1).mean()) if len(past)>=100 else np.nan
        bases[str(r.season)]={'games':len(past),'base':base}
        if not np.isfinite(base): missing_prior+=1; continue
        if r.condition_id not in meta.index: no_signal+=1; continue
        m=meta.loc[r.condition_id]; side=yrfi_side(m.question,m.description,r.o0,r.o1)
        orientation[m.yes_means]=orientation.get(m.yes_means,0)+1
        if side is None: no_signal+=1; continue
        t=tapes.get(r.condition_id,pd.DataFrame())
        if t.empty: no_signal+=1; continue
        cand=t[t.ts.ge(r.game_start_ts-1200)&t.ts.lt(r.game_start_ts)&t.s.eq(side)&t.q.le(base)]
        # At least five earlier prints are already observed at the trigger (ties excluded).
        cand=cand[cand.ts.map(lambda ts: np.searchsorted(t.ts.to_numpy(),ts,side='left')>=5)]
        if cand.empty: no_signal+=1; continue
        trigger=cand.iloc[0]
        out.append(order(r,side,trigger.ts,r.game_start_ts,limit_price=base,prior_base=base,reference=trigger.q))
    return out,{'prior_rates':bases,'orientation':orientation,'missing_prior_markets':missing_prior,'no_signal_markets':no_signal}


def totals_orders(obs):
    d=obs[obs.kind.eq('total')&obs.pre_n.ge(5)].copy()
    d['hic']=(d.inning-1)*2+d.half.eq('bottom').astype(int)
    d['pace']=d.home_score+d.away_score-d.line*d.hic/18
    d=d[d.hic.ge(4)]
    out=[]; models={}
    for year in sorted(d.season.unique()):
        train=d[d.season.lt(year)&d.s.eq(0)].copy()
        # Each prior game has equal total training weight, not one vote per active print.
        if train.game_pk.nunique()<20:
            models[str(year)]={'status':'unavailable','prior_games':train.game_pk.nunique()}; continue
        x=np.column_stack([np.ones(len(train)),train.pace]); y=train.y0-train.p0
        w=1/train.groupby('game_pk').game_pk.transform('size').to_numpy()
        coef=np.linalg.lstsq(x*np.sqrt(w[:,None]),y*np.sqrt(w),rcond=None)[0]
        models[str(year)]={'prior_games':train.game_pk.nunique(),'intercept':float(coef[0]),'slope':float(coef[1]),
                           'zero_crossing':float(-coef[0]/coef[1]) if abs(coef[1])>1e-12 else None}
        test=d[d.season.eq(year)].copy(); edge=coef[0]+coef[1]*test.pace
        side=np.where(test.pace.lt(0),0,1)
        qualifying=((test.pace.lt(0)&edge.ge(.06))|(test.pace.gt(0)&edge.le(-.06)))&test.s.eq(side)
        for r in test[qualifying].sort_values('ts').drop_duplicates('game_pk').itertuples():
            side=0 if r.pace<0 else 1
            out.append(order(r,side,r.ts,min(r.state_expiry_ts,r.ts+600),reference=r.q,pace=r.pace,
                             model_edge=float(coef[0]+coef[1]*r.pace)))
    return out,models


def transition_orders(obs):
    d=obs[obs.kind.eq('spread_home')&obs.line.eq(1.5)&obs.pre_n.ge(5)].copy()
    baseline=pd.read_parquet(C.DATA/'mlb/baseline.parquet',columns=['game_pk','season','inning','half','outs','bases','diff'])
    schedules=[pd.read_parquet(p,columns=['game_pk','home_score','away_score']) for p in sorted((C.DATA/'mlb/mlb_only').glob('schedule_*.parquet'))]
    schedules.append(pd.read_parquet(C.DATA/'mlb/mlb_games.parquet',columns=['game_pk','home_score','away_score']))
    finals=pd.concat(schedules).drop_duplicates('game_pk').set_index('game_pk')
    margin=finals.home_score-finals.away_score
    baseline['won']=baseline.game_pk.map(margin.gt(1.5).where(margin.notna()))
    baseline=baseline[baseline.won.notna()]
    out=[]; models={}
    for year in sorted(d.season.unique()):
        train=baseline[baseline.season.lt(year)]
        if train.game_pk.nunique()<100: models[str(year)]={'status':'unavailable'}; continue
        x=state_features(train); w=1/train.groupby('game_pk').game_pk.transform('size').to_numpy()
        model=sm.GLM(train.won.astype(float),x,family=sm.families.Binomial(),freq_weights=w).fit(maxiter=60)
        models[str(year)]={'prior_games':train.game_pk.nunique(),'prior_rows':len(train),'coefficients':model.params.tolist()}
        test=d[d.season.eq(year)].copy(); pred=model.predict(state_features(test))
        test['model']=pred; edge=pred-test.p0
        test['buy_side']=np.where(edge>0,0,1)
        q=test[edge.abs().ge(.08)&test.s.eq(test.buy_side)]
        for r in q.sort_values('ts').drop_duplicates('game_pk').itertuples():
            out.append(order(r,r.buy_side,r.ts,min(r.state_expiry_ts,r.ts+600),reference=r.q,model=r.model))
    return out,models


def pair_orders(markets,tapes,games):
    out=[]; diagnostic=[]; blocked=0
    h=markets[markets.kind.eq('spread_home')&markets.line.eq(1.5)]
    a=markets[markets.kind.eq('spread_away')&markets.line.eq(1.5)].set_index('game_pk')
    for r in h.itertuples():
        if r.game_pk not in a.index: blocked+=1; continue
        ar=a.loc[r.game_pk]; ar=next(ar.to_frame().T.itertuples(index=False))
        t0=tapes.get(r.condition_id,pd.DataFrame()); t1=tapes.get(ar.condition_id,pd.DataFrame())
        if t0.empty or t1.empty: blocked+=1; continue
        decision=r.game_start_ts-900
        ref=[prior_reference(t0,1,decision),prior_reference(t1,1,decision)]
        if any(v is None for v in ref): blocked+=1; continue
        past=prior_games(games,r.season); prior=float(past.final_margin_home.abs().eq(1).mean()) if len(past) else np.nan
        rate=np.array([r.fee_rate,ar.fee_rate]); px=np.array([v.q for v in ref])
        costs=px+rate*px*(1-px); cost=float(costs.sum())
        diagnostic.append({'game_pk':r.game_pk,'event_slug':r.event_slug,'season':r.season,'game_start_ts':r.game_start_ts,
            'pair_price':float(px.sum()),'pair_cost':cost,'implied_one_run':float(pair_implied(px.sum())),
            'prior_one_run':prior,'reference_age_home':decision-ref[0].ts,'reference_age_away':decision-ref[1].ts,
            'reference_skew':abs(ref[0].ts-ref[1].ts),'payout_sum':r.y1+ar.y1})
        if not np.isfinite(cost) or cost>=1.15: continue
        # Quantity/cash reservations are fixed from observed prior references, never future fill sizes.
        prior_qty=[t[t.s.eq(1)&t.ts.lt(decision)&t.ts.ge(decision-3600)]['size'].sum() for t in (t0,t1)]
        target=min(100/cost,*prior_qty)
        for i,m in enumerate((r,ar)):
            out.append(order(m,1,decision,decision+600,target_shares=target,
                             budget_usd=100*costs[i]/cost,pair_leg=i,reference=px[i],pair_cost=cost))
    return out,pd.DataFrame(diagnostic),{'missing_pair_or_prior_reference_games':blocked}


def replay_pairs(engine,orders,slip=0):
    """Retain independently partial legs; at deadline unwind unmatched inventory once.

    Sale proxy is 1-price of a later acquired complement print. This is not observed
    bid depth. Residual inventory after a partial/no unwind settles at actual payout.
    """
    b=engine.replay(orders,slip=slip)
    b['exit_shares']=0.; b['exit_price']=np.nan; b['exit_ts']=np.nan; b['exit_fee_usd']=0.
    exits=[]
    for _,g in b.groupby('game_pk'):
        matched=g.shares.min() if len(g)==2 else 0
        for i,r in g.iterrows():
            excess=r.shares-matched
            if excess>1e-9:
                exits.append({'m':r.m,'s':r.s,'signal_ts':r.expiry_ts,'expiry_ts':r.expiry_ts+600,
                    'target_shares':excess,'budget_usd':1e9,'event':r.game_pk,'source_index':i,'y':0,'fee_rate':r.fee_rate})
    if exits:
        # Entry is acquired No; liquidation consumes later acquired Yes. These print sets are disjoint.
        t=engine.tape.copy(); t['s']=1-t.s; t['q']=1-t.q
        x=TapeReplay(t).replay(pd.DataFrame(exits),event_cap_usd=np.inf,slip=0)
        for r in x.itertuples():
            i=r.source_index
            if not r.shares: continue
            px=max(0.,r.entry_price-slip); fee=r.shares*float(b.loc[i,'fee_rate'])*px*(1-px)
            b.loc[i,['exit_shares','exit_price','exit_ts','exit_fee_usd']]=[r.shares,px,r.fill_ts,fee]
            b.loc[i,'payout']=r.shares*px+(b.loc[i,'shares']-r.shares)*b.loc[i,'y']
            b.loc[i,'fee_usd']+=fee; b.loc[i,'cost_usd']+=fee
            b.loc[i,'pnl_usd']=b.loc[i,'payout']-b.loc[i,'cost_usd']
            b.loc[i,'roi']=b.loc[i,'pnl_usd']/b.loc[i,'cost_usd']
    b['residual_shares']=b.shares-b.exit_shares
    return b


def summarize(b):
    filled=b[b.cost_usd>0]
    if filled.empty: return {'signals':len(b),'filled_legs':0,'games':0,'cost':0.,'pnl':0.,'roi':None,'ci':[None,None],
                            'unfilled':len(b),'equal_game_roi':None,'equal_game_ci':[None,None]}
    roi,lo,hi=C.cluster_ci(filled.pnl_usd/filled.cost_usd,filled.game_pk,weights=filled.cost_usd)
    g=filled.groupby('game_pk').agg(pnl=('pnl_usd','sum'),cost=('cost_usd','sum')).sort_values('pnl',ascending=False)
    # Aggregate all legs first: one ROI and one bootstrap vote per funded game.
    # Unweighted legs would overweight games with more orders/partial fills.
    _,game_lo,game_hi=C.cluster_ci(g.pnl/g.cost,g.index)
    trimmed=g.iloc[3:]
    return {'signals':len(b),'filled_legs':len(filled),'games':len(g),'cost':float(filled.cost_usd.sum()),
        'pnl':float(filled.pnl_usd.sum()),'roi':roi,'ci':[lo,hi],'unfilled':int(b.shares.eq(0).sum()),
        'partial_legs':int(b.status.eq('partial').sum()),'median_fill_cost':float(filled.cost_usd.median()),
        'maximum_game_cost':float(g.cost.max()),'top3_pnl':float(g.head(3).pnl.sum()),
        'without_top3_roi':float(trimmed.pnl.sum()/trimmed.cost.sum()) if trimmed.cost.sum() else None,
        'equal_game_roi':float((g.pnl/g.cost).mean()),
        'equal_game_ci':[float(v) if np.isfinite(v) else None for v in (game_lo,game_hi)]}


def clean(obj):
    if isinstance(obj,dict): return {str(k):clean(v) for k,v in obj.items()}
    if isinstance(obj,(list,tuple)): return [clean(v) for v in obj]
    if isinstance(obj,(np.integer,)): return int(obj)
    if isinstance(obj,(float,np.floating)): return float(obj) if np.isfinite(obj) else None
    return obj


def economic_exit(r):
    if r.shares<=0: return 'unfilled',None,None
    sold=float(getattr(r,'exit_shares',0))
    residual=float(getattr(r,'residual_shares',r.shares))
    if sold>0 and residual<=1e-8:
        return 'exit_price',r.exit_ts,r.exit_price
    if sold>0:
        # Aggregate row: gross realized cash per original share combines sale and resolution.
        return 'mixed',max(r.closed_ts,r.exit_ts),r.payout/r.shares
    return 'resolution',r.closed_ts,r.y


def write_ledger(slug,b,summary):
    rows=[]
    for i,r in enumerate(b.sort_values('signal_ts').itertuples(),1):
        filled=r.shares>0; ts=r.fill_ts if filled else r.signal_ts
        note=f'{r.status}; signal={r.signal_ts}; eligible={r.eligible_ts}; expiry={r.expiry_ts}; shares={r.shares:.6f}; observed size={r.available_shares:.6f}; transaction proxy; fees inclusive ROI'
        if hasattr(r,'exit_shares'):
            note+=f'; unmatched unwind={r.exit_shares:.6f} at {r.exit_price} ts={r.exit_ts}; residual={r.residual_shares:.6f} resolves at {r.y}; resolution ts={r.closed_ts}'
        exit_kind,exit_ts,exit_price=economic_exit(r)
        rows.append([i,'holdout' if r.game_start_ts>=HOLDOUT else 'dev',pd.to_datetime(ts,unit='s',utc=True).strftime('%Y-%m-%d'),
            'baseball','mlb',r.event_slug,r.market_slug,f'Outcome {r.s} acquisition',r.fill_ts if filled else None,r.entry_price if filled else None,
            r.stake_usd,r.fee_usd,exit_kind,exit_ts,exit_price,
            r.payout,r.pnl_usd,r.roi,note,r.signal_ts])
    doc={'slug':slug,'title':slug.replace('-',' ').capitalize(),'group':'Recovered MLB hypotheses','sport':'baseball',
        'verdict':'INCONCLUSIVE','hypothesis':'Recovered original rule, repaired causal price and capacity accounting.',
        'mechanism':'See full report for the prior-season model and original hypothesis.',
        'entry_rule':'First rule signal/game; strictly later acquired-side transaction, 3s delay, observed size and $100/game cap.',
        'exit_rule':'Actual contract settlement; unmatched paired inventory gets one bounded unwind attempt then residual settlement.',
        'cost_model':'Historical contract fees; printed size proxy. Report also reruns +1c entry and adverse exit stress.',
        'periods':{'dev':'before 2026-07-01; historically explored','holdout':'from 2026-07-01; historically explored'},
        'headline':{p:{'bets':v['filled_legs'],'roi':v['roi'],'ci_lo':v['ci'][0],'ci_hi':v['ci'][1],'pnl_usd':v['pnl']} for p,v in summary.items() if p in ('dev','holdout')},
        'review':'No new holdout; transaction prices are not resting depth or proof of accepted orders.',
        'caveats':['Full signals include unfilled orders. ROI divides cash cost inclusive of all fees.',
            'Public receipt unknown; retrospective state clocks and next-state expiry are optimistic.'],
        'report_path':str(REPORT),'code_path':'pmsports/research/h_baseball_continuation.py','truncated':False,'n_total_trades':len(rows),'columns':COLS,'rows':rows}
    dst=C.RESEARCH/'ledgers'/f'{slug}.json'; dst.parent.mkdir(parents=True,exist_ok=True)
    tmp=dst.with_suffix(f'.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(clean(doc),allow_nan=False)); tmp.replace(dst)


def run():
    OUT.mkdir(parents=True,exist_ok=True)
    markets,tapes,meta,games,obs,paths,coverage=load_inputs()
    print('Loaded',coverage,flush=True)
    specs={}; details={}
    specs[SLUGS[0]],details[SLUGS[0]]=totals_orders(obs)
    specs[SLUGS[1]],details[SLUGS[1]]=nrfi_orders(markets,tapes,meta,games)
    specs[SLUGS[2]],diagnostic,details[SLUGS[2]]=pair_orders(markets,tapes,games)
    diagnostic.to_parquet(OUT/'pair_diagnostic.parquet',index=False)
    specs[SLUGS[3]],details[SLUGS[3]]=transition_orders(obs)
    engine=TapeReplay(pd.concat(list(tapes.values()),ignore_index=True))
    results={}
    for slug in SLUGS:
        orders=pd.DataFrame(specs[slug])
        if orders.empty: orders=pd.DataFrame(columns=['m','s','signal_ts','game_pk','game_start_ts','event_slug','market_slug'])
        outcomes=[]
        for slip in (0.,.01):
            b=replay_pairs(engine,orders,slip) if slug==SLUGS[2] else engine.replay(orders,slip=slip)
            b.to_parquet(OUT/f'{slug}-{"primary" if slip==0 else "stress"}.parquet',index=False)
            sums={p:summarize(b.loc[mask]) for p,mask in {'all':np.ones(len(b),bool),'dev':b.game_start_ts.lt(HOLDOUT),'holdout':b.game_start_ts.ge(HOLDOUT)}.items()}
            if slip==0: write_ledger(slug,b,sums)
            outcomes.append(sums)
        results[slug]={'primary':outcomes[0],'plus_1c':outcomes[1],'model':details[slug]}
        print(slug,clean(outcomes[0]['all']),flush=True)
    sources=paths+[C.DATA/'mlb/panel.parquet',C.DATA/'mlb/baseline.parquet',SRC/'games.parquet',SRC/'menu.parquet',SRC/'meta.parquet',
                   C.DATA/'mlb/mlb_games.parquet']+sorted((C.DATA/'mlb/mlb_only').glob('schedule_*.parquet'))
    identity={'code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'execution_sha256':hashlib.sha256(Path('pmsports/execution.py').read_bytes()).hexdigest(),
        'files':{str(p):{'size':p.stat().st_size,'mtime_ns':p.stat().st_mtime_ns} for p in sources}}
    artifact={'coverage':coverage,'results':results,'pair_diagnostic':{'n':len(diagnostic),
        'median_pair_cost':float(diagnostic.pair_cost.median()) if len(diagnostic) else None,
        'median_implied_one_run':float(diagnostic.implied_one_run.median()) if len(diagnostic) else None,
        'median_prior_one_run':float(diagnostic.prior_one_run.median()) if len(diagnostic) else None,
        'median_reference_skew_s':float(diagnostic.reference_skew.median()) if len(diagnostic) else None},'identity':identity}
    (OUT/'results.json').write_text(json.dumps(clean(artifact),indent=2,allow_nan=False))
    write_report(artifact)
    return artifact


def write_report(a):
    lines=['# Recovered MLB hypotheses: corrected historical tests','',
        'These four tests use actual later transactions with finite printed size, historical fees and $100/game targets. They do **not** establish executable depth, timely feed receipt or a fresh holdout. All historical periods had already been inspected.','',
        '| Rule | Period | Signals/legs | Filled games | Capital incl. fees | Net P&L | Cash-weighted ROI | Cash ROI game-cluster 95% CI | +1c cash ROI |',
        '|---|---|---:|---:|---:|---:|---:|---|---:|']
    def pct(x): return 'n/a' if x is None or not np.isfinite(x) else f'{100*x:.2f}%'
    for slug,r in a['results'].items():
        for period in ('all','dev','holdout'):
            v=r['primary'][period]; stress=r['plus_1c'][period]
            lines.append(f'| {slug} | {period} | {v["signals"]} | {v["games"]} | ${v["cost"]:,.2f} | ${v["pnl"]:,.2f} | {pct(v["roi"])} | {pct(v["ci"][0])} to {pct(v["ci"][1])} | {pct(stress["roi"])} |')
    lines+=['','No deployable edge is established by these tests. Read positive totals/NRFI cash point estimates with the interval, tiny first-print capacity, opposite-period results and concentration below. The pair diagnostic tests whether the pricing claim exists before any hypothetical atomic-fill profit.','',
        '## Original NRFI equal-game inference','',
        'The original NRFI requirement is equal weighting by game with `C.cluster_ci`. For each funded game, sum every leg’s net P&L and fees-inclusive capital, divide those sums, then average the resulting game ROIs. Each game gets one bootstrap vote, regardless of its capital or number of legs. This differs from the cash-weighted ROI above. No-fill games have no defined ROI and do not enter either ROI interval; their signals remain in the audit.','',
        '| Period | Replay | Funded games | Equal-game ROI | Equal-game 95% CI | Cash-weighted ROI |',
        '|---|---|---:|---:|---|---:|']
    nrfi=a['results'][SLUGS[1]]
    for period in ('all','dev','holdout'):
        for policy,label in (('primary','Primary'),('plus_1c','+1c')):
            v=nrfi[policy][period]
            lines.append(f'| {period} | {label} | {v["games"]} | {pct(v["equal_game_roi"])} | {pct(v["equal_game_ci"][0])} to {pct(v["equal_game_ci"][1])} | {pct(v["roi"])} |')
    v=nrfi['primary']['all']
    lines += ['',f'The original pooled NRFI equal-game estimate is **{pct(v["equal_game_roi"])}** (95% CI {pct(v["equal_game_ci"][0])} to {pct(v["equal_game_ci"][1])}); the cash-weighted estimate is {pct(v["roi"])}. The cash result cannot replace the original equal-game test. These already-explored periods do not establish a repeatable edge.','',
        '## Rules and repairs','',
        '- **Totals pace:** half innings completed = 2×(inning−1)+bottom; pace = runs−line×hic/18. A game-weighted linear regression of Over outcome minus its observed acquisition price on pace uses strictly prior seasons and hic≥4. First matching acquired-side signal/game requires ≥6c signed fitted residual; Over behind pace and Under ahead. At least five pregame prints; later entry before the next state. A season without 20 prior sampled games is unavailable, not a zero-return test. This is stricter than the recovered same-season DEV fit and prevents future-season training leakage.',
        '- **NRFI:** buy only canonical YRFI, using Gamma question/description plus actual Yes/No labels. Prior-season rate counts r1≥1, never raw run-count means. First-inning totals come from the corrected inning-2 top checkpoint, including runs on the final out; actual schedule dates restrict training to earlier seasons. At least 100 prior games are required. This avoids the old derivative-only history, which had just one pre-2025 game and could invent a 100% prior. Begin looking T−20min; five strictly earlier prints must already exist. Signal is first observed YRFI acquisition ≤ prior rate; entry is a different later acquisition still ≤ that limit, before scheduled start. No completed-pregame liquidity is used at an earlier decision.',
        '- **One-run pair:** at scheduled T−15min, last No acquisitions in each book within the preceding hour provide references. Trigger sum including fees <1.15. Equal requested quantities and per-leg cash reservations are fixed from these references and preceding-hour available printed quantity; future fill sizes never determine target quantity. Independent legs expire after 600s. Excess inventory receives one later size-limited unwind proxy, then residual settlement. The liquidation proxy is 1−the price of a later acquired complement, not a fabricated observed bid. Two later prints are not an atomic fill.',
        '- **Margin transition:** a fixed logistic model uses inning, half, outs, occupied bases, margin, margin×inning and signed squared margin. Fit on corrected baseline states from strictly earlier seasons, with each game equal total training weight. First observed cheap-side acquisition with |model−canonical home-cover price|≥8c signals; later entry before the next state. No moneyline haircut or outcome-dependent state reconstruction.','',
        '**Recovered algebra error:** the No/No pair pays 1+1{|margin|≤1}; for completed nontied games its fair value is 1+s₁. The price-implied one-run probability is therefore **pair price−1**, not `2−pair price` from the original prompt. A price below 1.15 is a directional one-run bet, not guaranteed profit. Settlement uses each actual contract payout, including voids.','',
        '## Capacity and concentration','',
        '| Rule | No fills | Partial legs | Median filled capital | Top-three-game P&L | Cash ROI excluding top three | Equal-game ROI | Equal-game 95% CI |',
        '|---|---:|---:|---:|---:|---:|---:|---|']
    for slug,r in a['results'].items():
        v=r['primary']['all']
        lines.append(f'| {slug} | {v["unfilled"]} | {v.get("partial_legs",0)} | ${v.get("median_fill_cost",0):.2f} | ${v.get("top3_pnl",0):.2f} | {pct(v.get("without_top3_roi"))} | {pct(v["equal_game_roi"])} | {pct(v["equal_game_ci"][0])} to {pct(v["equal_game_ci"][1])} |')
    lines+=['','## Coverage and inference','',
        f'Coverage: {a["coverage"]["markets"]} selected markets, {a["coverage"]["tapes"]} local tapes, {a["coverage"]["state_joined_prints"]:,} prints joined to corrected states. Ambiguous game aliases excluded: {a["coverage"]["ambiguous_games_excluded"]}. Missing tapes: {len(a["coverage"]["missing_tapes"])}. Newly fetched away-runline targets: 251; all requested creation-to-close, 25,076 returned prints, two empty tapes. Existing derivative/away caches retain their earlier sampling and bounded windows; the sample is not a complete unbiased MLB universe.',
        f'Canonical outcome 0 is verified from actual Over/Under labels or home/away team labels; legacy Yes/No runlines additionally require matching Gamma question and resolution text. Unknown orientation markets excluded: {a["coverage"]["unknown_canonical_orientation_excluded"]}. Missing first-inning totals or final margins are excluded from priors, never counted as false outcomes. Ledger no-fills retain signal time with null entry time; full and partial unwinds carry their actual economic exit clocks/prices.',
        'Pair diagnostic: '+json.dumps(clean(a['pair_diagnostic']))+'.',
        'Frozen NRFI rates: '+json.dumps(clean(a['results'][SLUGS[1]]['model']['prior_rates']))+'.',
        'Totals models: '+json.dumps(clean(a['results'][SLUGS[0]]['model']))+'. The fitted zero-crossing is reported rather than forced to zero; a nonzero crossing weakens the original mechanism.',
        'The corrected MLB panel replaces old phantom/duplicate states. Raw transaction timestamps are used without subtracting estimated chain lag. Historical state timestamps stand in for public knowledge; free MLB feed delay measured elsewhere is much longer than the 3s replay assumption. Next-state expiry is analytical censoring, not proof of canceling a pending sports order. Totals can close on crossing the line: results condition on observed state and surviving prints, and do not estimate unconditional late-game pricing error.',
        'Only the four recovered primary rules and the declared +1c stress were executed. No parameter scan or post-result winning variant is hidden. Models, source file identities, every signal/fill/no-fill, pair references and all four uncapped desk ledgers are saved. Both 95% bootstrap intervals use C.cluster_ci with 2,000 whole-game resamples and seed 0: cash intervals divide resampled total net P&L by resampled total fees-inclusive capital; equal-game intervals average the resampled per-game aggregate ROIs. Empty samples have null point estimates and bounds; fewer than five funded games have no interval. Neither interval corrects historical selection or multiple testing. A positive point estimate is exploratory until independently collected receipt/depth data validate entry, capacity and a frozen rule.','',
        'The +1c replay retains the original limit: for NRFI it can skip an originally affordable print and take another later print, so its filled sample can change and aggregate ROI need not decrease. This is an executable-limit sensitivity within the transaction proxy, not a same-filled-sample causal cost estimate. Primary and stress Parquets retain every such change.','',
        '## Reproduction','',
        '```bash','OPENBLAS_NUM_THREADS=1 .venv/bin/python -m pmsports.research.h_baseball_continuation',
        '.venv/bin/python -m pytest -q tests/test_baseball_continuation.py','```','',
        'Outputs: `data/research/baseball_continuation/results.json`, full primary/stress Parquets, `pair_diagnostic.parquet`, and `data/research/ledgers/<rule>.json`. No network calls occur in the study.']
    REPORT.write_text('\n'.join(lines)+'\n')


if __name__=='__main__': run()
