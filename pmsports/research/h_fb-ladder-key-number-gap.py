"""Causal football 3/7 ladder-gap audit. Run `python .../h_fb-ladder-key-number-gap.py run`.

The original T-10min wording looked forward to kickoff. The corrected primary uses
[decision-10min, decision), not future prices, and retains every failed hedge.
"""
from __future__ import annotations
import importlib.util
import json
import sys
from pathlib import Path
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from pmsports.research import common as C
spec=importlib.util.spec_from_file_location('football_pairs',Path(__file__).with_name('h_fb-ladder-monotonicity-arb.py'))
H=importlib.util.module_from_spec(spec);spec.loader.exec_module(H)
SLUG='fb-ladder-key-number-gap'
OUT=C.RESEARCH/('h_'+SLUG)
KEYS=(3,7)
LAMBDA=.6
DECISION_S=600
MIN_PRINTS=3


def bucket(p):
    return int(np.searchsorted([.35,.55,.75],p,side='right'))


def prior_pmf(lg,season,key,p_side,training,scores):
    """Strictly prior seasons only; sparse price buckets use disclosed symmetric fallback."""
    prior=training[(training.season<season)&(training.lg==lg)]
    if np.isfinite(p_side):
        z=prior[prior.bucket==bucket(p_side)]
        if len(z)>=20:return float((z.margin_side==key).mean()),len(z),'prior-season directional bucket'
    raw=scores[(scores.season<season)&(scores.lg==lg)]
    if len(raw)<100:return np.nan,len(raw),'insufficient prior seasons'
    return .5*float((raw.margin==key).mean()),len(raw),'half prior absolute-margin mass; no reliable direction bucket'


def moneyline_references():
    games=H.moneylines()
    f=C.fills(markets=games.m,columns=['m','s','ts','q','size'])
    groups={m:g.sort_values('ts',kind='stable') for m,g in f.groupby('m')}
    original=pd.read_parquet(C.DATA/'events/nfl/games.parquet').drop_duplicates('condition_id').set_index('condition_id')
    rows=[]
    for r in games.itertuples():
        d=groups.get(r.m)
        if d is None:continue
        decision=r.espn_date-DECISION_S
        w=d[(d.ts>=decision-3600)&(d.ts<decision)]
        if len(w)<3:continue
        home=np.where(w.s==r.home_idx,w.q,1-w.q)
        ph=float(np.median(home));g=original.loc[r.condition_id]
        season=pd.to_datetime(r.espn_date,unit='s',utc=True).year-(pd.to_datetime(r.espn_date,unit='s',utc=True).month<7)
        for side in ('home','away'):
            p=ph if side=='home' else 1-ph
            margin=float(g.final_home-g.final_away)*(1 if side=='home' else -1)
            rows.append(dict(event_slug=r.event_slug,side=side,p_side=p,bucket=bucket(p),season=season,lg=str(r.league)[:3],margin_side=margin))
    return pd.DataFrame(rows)


def build_signals(rungs,tape,training,scores,lam=LAMBDA):
    lookup=H.paths(tape); refs=training.drop_duplicates(['event_slug','side']).set_index(['event_slug','side'])
    signals=[];diagnostics=[]
    for (event,side),g in rungs.groupby(['event_slug','side'],sort=False):
        for key in KEYS:
            lo=g[g.line==key-.5];hi=g[g.line==key+.5]
            if len(lo)!=1 or len(hi)!=1:continue
            a,b=next(lo.itertuples()),next(hi.itertuples())
            if str(a.espn_id)!=str(b.espn_id):continue
            decision=float(a.espn_date-DECISION_S)
            yes_a=lookup.get((a.condition_id,int(a.yes_idx)));yes_b=lookup.get((b.condition_id,int(b.yes_idx)))
            if yes_a is None or yes_b is None:continue
            wa=yes_a[(yes_a.ts>=decision-600)&(yes_a.ts<decision)]
            wb=yes_b[(yes_b.ts>=decision-600)&(yes_b.ts<decision)]
            if len(wa)<MIN_PRINTS or len(wb)<MIN_PRINTS:continue
            pa=float(wa.q.median());pb=float(wb.q.median())
            # A complementary historical reference is only used to prescribe quantity;
            # entry still requires an actual later print acquiring No on the upper rung.
            no_b=lookup.get((b.condition_id,1-int(b.yes_idx)))
            wn=no_b[(no_b.ts>=decision-600)&(no_b.ts<decision)] if no_b is not None else pd.DataFrame()
            no_ref=float(wn.q.median()) if len(wn) else 1-pb
            pside=float(refs.loc[(event,side),'p_side']) if (event,side) in refs.index else np.nan
            date=pd.to_datetime(a.espn_date,unit='s',utc=True);season=date.year-(date.month<7)
            ph,n,kind=prior_pmf(a.lg,season,key,pside,training,scores)
            implied=pa-pb
            diagnostics.append(dict(event=event,side=side,K=key,holdout=a.holdout,implied=implied,p_hat=ph,prior_n=n,model=kind))
            if not np.isfinite(ph) or implied>=lam*ph:continue
            signals.append(dict(event=event,event_slug=event,event_a=event,event_b=event,side=side,lg=a.lg,holdout=a.holdout,
                signal_ts=decision,expiry_ts=min(a.espn_date,a.closed_ts,b.closed_ts),
                cid_a=a.condition_id,cid_b=b.condition_id,s_a=int(a.yes_idx),s_b=1-int(b.yes_idx),
                ref_a=pa,ref_b=no_ref,rate_a=a.fee_rate,rate_b=b.fee_rate,y_a=a.pay_yes,y_b=b.pay_no,
                closed_a=a.closed_ts,closed_b=b.closed_ts,line_a=a.line,line_b=b.line,K=key,
                p_hat=ph,implied=implied,model=kind,prior_n=n,signal_gap=ph-implied,
                complementary_reference=not len(wn),market=a.market_slug+' | '+b.market_slug))
    d=pd.DataFrame(signals)
    if len(d):d=d.sort_values(['event','K','signal_gap'],ascending=[True,True,False]).drop_duplicates(['event','K'])
    return d,pd.DataFrame(diagnostics)


def run():
    rungs,coverage=H.discover();tape,available=H.raw_tape(rungs)
    coverage.update(cached_rungs=len(available),missing_rungs=int((~rungs.condition_id.isin(available)).sum()),
        primary_decision='T-10min, prior 10min references; earlier T-3h run retired, not silently reused')
    rungs=rungs[rungs.condition_id.isin(available)].copy()
    training=moneyline_references();scores=pd.read_parquet(OUT/'scores.parquet')
    signals,diagnostics=build_signals(rungs,tape,training,scores)
    tr=H.pair_replay(signals,tape,slip=.01)
    res={k:H.summary(tr[tr.holdout==(k=='holdout')]) for k in ('dev','holdout')}
    res['coverage']=coverage
    res['prescreen']={k:{'ladders':len(g),'median_implied':float(g.implied.median()),'median_prior_mass':float(g.p_hat.median())} for k,g in diagnostics.groupby('K')} if len(diagnostics) else {}
    zero=H.pair_replay(signals,tape,slip=0.)
    res['sensitivity_no_slippage']={k:H.summary(zero[zero.holdout==(k=='holdout')]) for k in ('dev','holdout')}
    res['lambda_variants']={}
    for lam in (.5,.7):
        sig,_=build_signals(rungs,tape,training,scores,lam=lam);other=H.pair_replay(sig,tape,slip=.01)
        res['lambda_variants'][str(lam)]={k:H.summary(other[other.holdout==(k=='holdout')]) for k in ('dev','holdout')}
    description='At T−10min, buy Yes on K−0.5 and No on K+0.5 for K=3/7 when the prior 10-minute Yes-price gap is below 0.6 times strictly prior-season directional margin mass. Require three signal prints per rung. One signal per event/key; $100 event capital cap, +1c per-leg stress. The pair is a $1 floor plus a $1 exact-margin bonus only when equally filled and consistently settled.'
    diagnostics.to_parquet(OUT/'causal_prescreen.parquet',index=False)
    H.save(SLUG,tr,res,description)
    print(json.dumps(H.clean(res),indent=2))


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1] not in ('run','analyze','all'):raise SystemExit('Use run; legacy future-dependent book/quotes caches are retired.')
    run()
